from __future__ import annotations

import asyncio
import contextlib
import json
import re
import shutil
import time
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, MessageChain, filter
from astrbot.api.star import Context, Star

from .services import FFLogsConfigurationError, FFLogsService, FFXIVService
from .services.calendar import (
    CALENDAR_TEMPLATE,
    build_calendar_data,
    format_calendar_text,
)
from .services.config import feature_enabled, reminder_types
from .services.fashion import FashionService
from .services.ocean import OceanService
from .services.pvp import PvpService
from .services.quest import QuestGraphService
from .services.reminders import build_reminder_events, due_events
from .services.schedule import format_countdown, normalize_now
from .services.storage import get_plugin_data_dir
from .services.wiki import WikiResult, WikiService

PVP_TEMPLATE = """
<style>
body{margin:0;background:#111827;color:#eef3fb;font-family:Arial,"Noto Sans SC",sans-serif}
.card{width:980px;padding:28px;background:#17233a;border-radius:20px}
h1{margin:0 0 8px;color:#ffd479}.sub{color:#aebbd0;margin-bottom:18px}
.maps{display:grid;grid-template-columns:repeat(2,1fr);gap:12px}
.map{border:1px solid #3b5275;border-radius:13px;overflow:hidden;background:#20324f}
.map img,.placeholder{display:block;width:100%;height:120px;object-fit:cover}
.placeholder{display:flex;align-items:center;justify-content:center;color:#aebbd0;background:#263957}
.map div{padding:10px;font-size:17px}
</style>
<div class="card">
  <h1>⚔️ FF14 PvP 周历</h1>
  <div class="sub">当前纷争前线：{{ current_frontline }} · 23:00换图倒计时：{{ frontline_countdown }} · 当前水晶冲突：{{ current_cc }}</div>
  <div class="maps">
  {% for entry in maps %}<div class="map">{% if entry.image_data %}<img src="{{ entry.image_data }}">{% else %}<div class="placeholder">地图图片暂不可用</div>{% endif %}<div>{{ entry.date }}　{{ entry.name }}</div></div>{% endfor %}
  </div>
  <div class="sub" style="margin-top:18px">后续水晶冲突：{% for entry in cc_upcoming %}{{ entry.date }} {{ entry.name }}{% if not loop.last %} · {% endif %}{% endfor %}</div>
</div>
"""


class FF14LogsPlugin(Star):
    """FF14 助手：FFLogs、国服资料、轮换、活动和订阅提醒。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = get_plugin_data_dir()
        self.fflogs = FFLogsService(config)
        self.ffxiv = FFXIVService(config)
        self.wiki = WikiService(config, self.data_dir)
        self.quest_graph = QuestGraphService(config, self.data_dir)
        self.pvp = PvpService(config)
        self.ocean = OceanService(config)
        self.fashion = FashionService(config, self.data_dir)
        self._scheduler_task: asyncio.Task[None] | None = None
        self._event_cache: list[Any] = []
        self._event_cache_at: datetime | None = None
        self._fashion_cache: Any = None
        self._fashion_cache_at: datetime | None = None
        self._wiki_candidates_memory: dict[str, dict[str, Any]] = {}

    async def initialize(self) -> None:
        """Start the one-minute reminder loop after the plugin is activated."""

        if self._scheduler_task is None or self._scheduler_task.done():
            self._scheduler_task = asyncio.create_task(self._scheduler_loop())

    async def terminate(self) -> None:
        """Cancel the scheduler when AstrBot reloads or unloads the plugin."""

        task = self._scheduler_task
        self._scheduler_task = None
        if task and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run_query(
        self,
        operation: str,
        query: Callable[[], Awaitable[str]],
        failure_message: str,
    ) -> str:
        try:
            return await query()
        except FFLogsConfigurationError as exc:
            return f"❌ {exc}"
        except Exception:
            logger.error("%s失败", operation, exc_info=True)
            return failure_message

    @staticmethod
    def _command_rest(event: AstrMessageEvent, command: str) -> str:
        message = getattr(event, "get_message_str", lambda: "")()
        if not isinstance(message, str):
            message = str(getattr(event, "message_str", ""))
        match = re.match(rf"^\s*/?{re.escape(command)}(?:\s+(.*))?\s*$", message, re.I)
        return match.group(1).strip() if match and match.group(1) else ""

    def _feature_error(self, feature: str, label: str) -> str | None:
        if feature_enabled(self.config, feature):
            return None
        return f"⏸️ {label}功能已在插件后台关闭。"

    @staticmethod
    def _event_identifier(event: AstrMessageEvent, name: str) -> str:
        value = getattr(event, name, "")
        if callable(value):
            try:
                value = value()
            except Exception:
                value = ""
        return str(value or "").strip()

    def _wiki_candidate_keys(self, event: AstrMessageEvent) -> list[str]:
        """Build a stable session key and retain the old key for upgrades."""

        origin = self._event_identifier(event, "unified_msg_origin")
        platform = (
            self._event_identifier(event, "get_platform_id")
            or self._event_identifier(event, "get_platform_name")
            or "unknown"
        )
        group_id = self._event_identifier(event, "get_group_id")
        if group_id:
            stable_key = f"wiki_candidates:v2:{platform}:group:{group_id}"
        else:
            sender_id = (
                self._event_identifier(event, "get_sender_id")
                or self._event_identifier(event, "get_session_id")
                or origin
            )
            stable_key = f"wiki_candidates:v2:{platform}:user:{sender_id}"
        keys = [stable_key]
        if origin:
            keys.append(f"wiki_candidates:{origin}")
        return list(dict.fromkeys(keys))

    async def _load_wiki_candidates(
        self,
        event: AstrMessageEvent,
    ) -> tuple[dict[str, Any] | None, bool]:
        now = time.time()
        expired_seen = False
        for key in self._wiki_candidate_keys(event):
            candidates = self._wiki_candidates_memory.get(key)
            if candidates is None:
                try:
                    stored = await self.get_kv_data(key, {})
                except Exception:
                    logger.warning("读取 Wiki 候选失败：%s", key, exc_info=True)
                    stored = {}
                if isinstance(stored, dict):
                    candidates = stored
                    self._wiki_candidates_memory[key] = stored
            if not isinstance(candidates, dict):
                continue
            try:
                expires_at = float(candidates.get("expires_at", 0))
            except (TypeError, ValueError):
                expires_at = 0
            raw_results = candidates.get("results")
            if expires_at >= now and isinstance(raw_results, list) and raw_results:
                return candidates, False
            if expires_at < now:
                expired_seen = True
        return None, expired_seen

    async def _save_wiki_candidates(
        self,
        event: AstrMessageEvent,
        results: list[WikiResult],
    ) -> None:
        payload = {
            "created_at": time.time(),
            "expires_at": time.time() + 5 * 60,
            "results": [result.to_dict() for result in results[:5]],
        }
        for key in self._wiki_candidate_keys(event):
            self._wiki_candidates_memory[key] = payload
            try:
                await self.put_kv_data(key, payload)
            except Exception:
                # The memory copy still makes the same-process interaction
                # work when the KV backend is temporarily unavailable.
                logger.warning("保存 Wiki 候选失败：%s", key, exc_info=True)

    async def _invalidate_wiki_candidates(self, event: AstrMessageEvent) -> None:
        payload = {
            "created_at": time.time(),
            "expires_at": time.time(),
            "results": [],
        }
        for key in self._wiki_candidate_keys(event):
            self._wiki_candidates_memory[key] = payload
            try:
                await self.put_kv_data(key, payload)
            except Exception:
                logger.warning("清理 Wiki 候选失败：%s", key, exc_info=True)

    async def _wiki_quest_fallback(
        self,
        result: WikiResult,
        query: str,
    ) -> WikiResult:
        """Fill in main-quest progress when HuijiWiki is temporarily blocked."""

        if result.page_type != "候选":
            return result
        try:
            progress = await self.quest_graph.progress_for(query)
        except Exception:
            logger.warning("Wiki 主线任务降级查询失败", exc_info=True)
            return result
        if not progress:
            return result
        return WikiResult(
            title=result.title,
            page_type="任务",
            summary="Wiki接口暂时不可用；以下主线进度来自运行时 Quest.csv 任务图。",
            quest_progress=progress,
            source_url=result.source_url,
            cached_at=result.cached_at,
        )

    async def _query_fflogs(self, character_name: str, server_name: str) -> str:
        error = self._feature_error("logs", "FFLogs")
        if error:
            return error
        return await self._run_query(
            "查询 FFLogs",
            lambda: self.fflogs.query(character_name, server_name),
            "❌ FFLogs 查询失败，请稍后重试或检查插件日志。",
        )

    @filter.command("fflogs")
    async def cmd_fflogs(
        self,
        event: AstrMessageEvent,
        character_name: str,
        server_name: str,
    ):
        """查询 FF14 战绩。用法：/fflogs 角色名 服务器名"""

        yield event.plain_result(f"🔍 正在检索 {character_name}@{server_name} 的全版本档案...")
        yield event.plain_result(await self._query_fflogs(character_name, server_name))

    @filter.llm_tool(name="search_fflogs")
    async def tool_fflogs(
        self,
        event: AstrMessageEvent,
        character_name: str,
        server_name: str,
    ):
        """查询 FF14 玩家的 FFLogs 战绩。

        Args:
            character_name(string): 玩家角色名，例如“冰冷”
            server_name(string): 玩家所在服务器名，例如“白银乡”
        """

        await event.send(event.plain_result(f"🔍 正在检索 {character_name}@{server_name} 的战绩..."))
        result = await self._query_fflogs(character_name, server_name)
        await event.send(event.plain_result(result))
        return "查询已经完成，结果已直接发送给用户。"

    @filter.llm_tool(name="search_ff14_wiki")
    async def tool_ff14_wiki(
        self,
        event: AstrMessageEvent,
        query: str,
        limit: int = 5,
    ) -> str:
        """搜索 FF14 Wiki 并返回结构化短结果，不主动发送消息。

        Args:
            query(string): 要查询的物品、装备、收藏、成就、任务、采集物或鱼名。
            limit(int): 最多返回的候选数量，范围为 1 到 5。
        """

        del event
        error = self._feature_error("wiki_llm_tool", "Wiki LLM Tool")
        if error:
            return error
        results = await self.wiki.search_ff14_wiki(query, limit)
        return json.dumps([result.to_dict() for result in results], ensure_ascii=False)

    @filter.command("ff14helps")
    async def cmd_ff14_help(self, event: AstrMessageEvent):
        """显示 FF14 助手帮助；也可使用 /ff14 help。"""

        yield event.plain_result(self.help_text())

    @staticmethod
    def help_text() -> str:
        return """🛡️ FF14 助手

/ff14 help　显示帮助
/ff14 logs <角色> <服务器>　查询 FFLogs（兼容 /fflogs）
/ff14 price <物品>　查询国服物价（兼容 /ff14 <物品>）
/ff14 status　国服服务器状态（兼容 /ff14status）
/ff14 news　最新国服公告（兼容 /ff14news）
/ff14 maint　维护公告（兼容 /ff14maint）
/ff14 wiki <关键词>　统一查询 Wiki；回复 /ff14 wiki #编号 选择候选
/ff14 patch [版本]　查询国服版本更新
/ff14 ocean [路线/成就鱼]　查询未来3班海钓航班
/ff14 fashion　查询本周时尚评鉴和80分方案
/ff14 pvp　查询PvP轮换和本周地图图
/ff14 events　查询当前及未来30天限时活动
/ff14 calendar　生成综合日历图
/ff14 subscribe　订阅后台提醒（群聊仅管理员）
/ff14 unsubscribe　解除当前会话订阅"""

    @filter.command("ff14")
    async def cmd_ff14(self, event: AstrMessageEvent):
        """FF14 助手统一入口。用法：/ff14 help|logs|price|status|news|maint|wiki|patch|ocean|fashion|pvp|events|calendar|subscribe|unsubscribe"""

        rest = self._command_rest(event, "ff14")
        if not rest:
            yield event.plain_result(self.help_text())
            return
        command, _, argument = rest.partition(" ")
        command = command.casefold()
        argument = argument.strip()

        if command in {"help", "helps"}:
            yield event.plain_result(self.help_text())
        elif command == "logs":
            pieces = argument.split()
            if len(pieces) < 2:
                yield event.plain_result("用法：/ff14 logs <角色名> <服务器名>")
                return
            yield event.plain_result(f"🔍 正在检索 {pieces[0]}@{pieces[1]} 的全版本档案...")
            yield event.plain_result(await self._query_fflogs(pieces[0], pieces[1]))
        elif command == "price":
            yield event.plain_result(await self._price_result(argument))
        elif command in {"status", "server"}:
            yield event.plain_result(await self._status_result())
        elif command == "news":
            yield event.plain_result(await self._news_result())
        elif command in {"maint", "maintenance"}:
            yield event.plain_result(await self._maintenance_result())
        elif command == "wiki":
            yield event.plain_result(await self._wiki_result(event, argument))
        elif command == "patch":
            yield event.plain_result(await self._patch_result(argument))
        elif command == "ocean":
            yield event.plain_result(await self._ocean_result(argument))
        elif command == "fashion":
            result, image = await self._fashion_result()
            yield event.plain_result(result)
            if image:
                yield event.image_result(image)
        elif command == "pvp":
            result, image = await self._pvp_result()
            yield event.plain_result(result)
            if image:
                yield event.image_result(image)
        elif command == "events":
            yield event.plain_result(await self._events_result())
        elif command == "calendar":
            result, image = await self._calendar_result()
            if image:
                yield event.image_result(image)
            else:
                yield event.plain_result(result)
        elif command == "subscribe":
            yield event.plain_result(await self._subscribe(event))
        elif command == "unsubscribe":
            yield event.plain_result(await self._unsubscribe(event))
        else:
            # v1 compatibility: /ff14 <物品名> means price lookup.
            yield event.plain_result(await self._price_result(rest))

    @filter.command("ff14status")
    async def cmd_ff14_status(self, event: AstrMessageEvent):
        """查询 FF14 国服服务器状态。用法：/ff14status"""

        yield event.plain_result("🔍 正在获取国服服务器状态...")
        yield event.plain_result(await self._status_result())

    @filter.command("ff14news")
    async def cmd_ff14_news(self, event: AstrMessageEvent):
        """查询 FF14 国服官网最新新闻。用法：/ff14news"""

        yield event.plain_result("🔍 正在获取官方最新情报...")
        yield event.plain_result(await self._news_result())

    @filter.command("ff14maint")
    async def cmd_ff14_maintenance(self, event: AstrMessageEvent):
        """查询 FF14 国服维护公告。用法：/ff14maint"""

        yield event.plain_result("🔍 正在获取维护公告...")
        yield event.plain_result(await self._maintenance_result())

    async def _price_result(self, item_name: str) -> str:
        error = self._feature_error("price", "物价")
        if error:
            return error
        item_name = item_name.strip()
        if not item_name:
            return "用法：/ff14 price <物品名>"
        return await self._run_query(
            "查询物价",
            lambda: self.ffxiv.query_prices(item_name),
            "❌ 物价查询失败，请稍后重试或检查插件日志。",
        )

    async def _status_result(self) -> str:
        error = self._feature_error("status", "服务器状态")
        if error:
            return error
        return await self._run_query(
            "查询服务器状态",
            self.ffxiv.query_server_status,
            "❌ 获取服务器状态失败，请稍后重试。",
        )

    async def _news_result(self) -> str:
        error = self._feature_error("news", "官方新闻")
        if error:
            return error
        return await self._run_query(
            "查询官方新闻",
            self.ffxiv.query_news,
            "❌ 获取官方新闻失败，请稍后重试。",
        )

    async def _maintenance_result(self) -> str:
        error = self._feature_error("maint", "维护公告")
        if error:
            return error
        return await self._run_query(
            "查询维护公告",
            self.ffxiv.query_maintenance,
            "❌ 获取维护公告失败，请稍后重试。",
        )

    async def _wiki_result(self, event: AstrMessageEvent, query: str) -> str:
        error = self._feature_error("wiki", "Wiki")
        if error:
            return error
        query = query.strip()
        if not query:
            return "用法：/ff14 wiki <关键词>"
        if query.startswith("#"):
            try:
                index = int(query[1:])
            except ValueError:
                return "❌ 候选编号必须是数字，例如 /ff14 wiki #1。"
            candidates, expired = await self._load_wiki_candidates(event)
            if candidates is None and expired:
                return "❌ 上一次 Wiki 候选已过期，请重新搜索。"
            if candidates is None:
                return "❌ 没有找到5分钟内的候选词条，请重新搜索。"
            raw_results = candidates.get("results", [])
            if not isinstance(raw_results, list) or not 1 <= index <= len(raw_results):
                return "❌ 候选编号不存在，请使用列表中的编号。"
            raw = raw_results[index - 1]
            if not isinstance(raw, dict):
                return "❌ 候选词条数据无效，请重新搜索。"
            raw_time_windows = raw.get("time_windows", [])
            if not isinstance(raw_time_windows, (list, tuple)):
                raw_time_windows = []
            raw_details = raw.get("details", {})
            if not isinstance(raw_details, dict):
                raw_details = {}
            selected = WikiResult(
                title=str(raw.get("title", "")),
                page_type=str(raw.get("type", raw.get("page_type", "普通"))),
                summary=str(raw.get("summary", "")),
                acquisition=str(raw.get("acquisition", "")),
                unlock_info=str(raw.get("unlock_info", "")),
                quest_progress=str(raw.get("quest_progress", "")),
                time_windows=tuple(str(value) for value in raw_time_windows),
                details={
                    str(key): str(value)
                    for key, value in raw_details.items()
                },
                source_url=str(raw.get("url", raw.get("source_url", ""))),
            )
            selected = await self._wiki_quest_fallback(selected, selected.title)
            return self.wiki.format_results(selected.title, [selected])

        results = await self.wiki.search_ff14_wiki(query, 5)
        exact = next(
            (result for result in results if result.title.casefold() == query.casefold()),
            None,
        )
        if len(results) == 1 or exact:
            selected = exact or results[0]
            selected = await self._wiki_quest_fallback(selected, query)
            if selected.page_type == "任务" and not selected.quest_progress:
                progress = await self.quest_graph.progress_for(selected.title)
                if progress:
                    selected = WikiResult(**{**selected.__dict__, "quest_progress": progress})
            await self._invalidate_wiki_candidates(event)
            return self.wiki.format_results(query, [selected])
        await self._save_wiki_candidates(event, results)
        return self.wiki.format_results(query, results[:5])

    async def _patch_result(self, version: str) -> str:
        error = self._feature_error("patch", "版本更新")
        if error:
            return error
        return await self._run_query(
            "查询版本更新",
            lambda: self.ffxiv.query_patch(version),
            "❌ 获取版本更新失败，请稍后重试。",
        )

    async def _ocean_result(self, query: str) -> str:
        error = self._feature_error("ocean", "海钓")
        if error:
            return error
        return self.ocean.format_voyages(query)

    async def _fashion_result(self) -> tuple[str, str]:
        error = self._feature_error("fashion", "时尚评鉴")
        if error:
            return error, ""
        report = await self.fashion.query()
        image = report.image_url if report.confirmed and report.image_url.startswith("http") else ""
        return self.fashion.format_report(report), image

    async def _pvp_result(self) -> tuple[str, str]:
        error = self._feature_error("pvp", "PvP")
        if error:
            return error, ""
        status = self.pvp.format_status()
        now = normalize_now()
        current = self.pvp.current_frontline()
        cc = self.pvp.current_cc()
        weekly = self.pvp.weekly_frontline()
        map_ids = list(dict.fromkeys(entry.map_id for entry in weekly))
        image_data = await asyncio.gather(
            *(self.pvp.local_image_data(map_id, self.data_dir) for map_id in map_ids),
        )
        image_by_map = dict(zip(map_ids, image_data, strict=True))
        maps = [
            {
                "date": entry.start_at.strftime("%m-%d"),
                "name": entry.name,
                "image_data": image_by_map.get(entry.map_id, ""),
            }
            for entry in weekly
        ]
        cc_upcoming = [
            {
                "date": entry.start_at.strftime("%m-%d %H:%M"),
                "name": entry.name,
            }
            for entry in self.pvp.upcoming_cc(now, 4)[1:]
        ]
        image = await self._render_image(
            PVP_TEMPLATE,
            {
                "current_frontline": current.name,
                "frontline_countdown": format_countdown(current.end_at - now),
                "current_cc": cc.name,
                "maps": maps,
                "cc_upcoming": cc_upcoming,
            },
            "pvp-weekly",
        )
        return status, image

    async def _events_result(self) -> str:
        error = self._feature_error("events", "活动")
        if error:
            return error
        return await self._run_query(
            "查询限时活动",
            self.ffxiv.query_events,
            "❌ 获取限时活动失败，请稍后重试。",
        )

    async def _calendar_result(self) -> tuple[str, str]:
        error = self._feature_error("calendar", "综合日历")
        if error:
            return error, ""
        events = []
        if feature_enabled(self.config, "events"):
            try:
                events = [
                    {
                        "title": event.title,
                        "date_text": event.date_text,
                        "url": event.url,
                    }
                    for event in await self.ffxiv.get_event_items()
                ]
            except Exception:
                logger.warning("日历读取活动失败", exc_info=True)
        data = build_calendar_data(self.config, self.pvp, events)
        image = await self._render_image(CALENDAR_TEMPLATE, data, "calendar")
        return format_calendar_text(data), image

    async def _render_image(self, template: str, data: dict[str, Any], name: str) -> str:
        try:
            rendered = await self.html_render(
                template,
                data,
                return_url=False,
                options={"type": "png", "full_page": True},
            )
        except Exception:
            logger.warning("渲染 %s 图片失败", name, exc_info=True)
            return ""
        if not rendered:
            return ""
        rendered_text = str(rendered)
        if rendered_text.startswith(("http://", "https://")):
            return rendered_text
        source = Path(rendered_text)
        if not await asyncio.to_thread(source.exists):
            return rendered_text
        target_dir = self.data_dir / "generated"
        await asyncio.to_thread(target_dir.mkdir, parents=True, exist_ok=True)
        target = target_dir / f"{name}.png"
        try:
            source_resolved, target_resolved = await asyncio.gather(
                asyncio.to_thread(source.resolve),
                asyncio.to_thread(target.resolve),
            )
            if source_resolved != target_resolved:
                await asyncio.to_thread(shutil.copy2, source, target)
            return str(target)
        except OSError:
            return rendered_text

    async def _subscriptions(self) -> list[dict[str, Any]]:
        value = await self.get_kv_data("subscriptions", [])
        return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []

    async def _subscribe(self, event: AstrMessageEvent) -> str:
        if not reminder_types(self.config):
            return "⚙️ 请先在插件后台选择至少一种 reminder_types，再执行订阅。"
        group_id = event.get_group_id()
        if group_id and not event.is_admin():
            return "❌ 群聊订阅和退订仅允许群管理员操作。"
        origin = event.unified_msg_origin
        subscriptions = await self._subscriptions()
        if any(item.get("origin") == origin for item in subscriptions):
            return "✅ 当前会话已经订阅，无需重复操作。"
        subscriptions.append(
            {
                "origin": origin,
                "group_id": group_id,
                "subscribed_at": datetime.now().isoformat(timespec="seconds"),
            },
        )
        await self.put_kv_data("subscriptions", subscriptions)
        return "✅ 已订阅 FF14 助手提醒；提醒内容由插件后台配置决定。"

    async def _unsubscribe(self, event: AstrMessageEvent) -> str:
        group_id = event.get_group_id()
        if group_id and not event.is_admin():
            return "❌ 群聊订阅和退订仅允许群管理员操作。"
        origin = event.unified_msg_origin
        subscriptions = await self._subscriptions()
        remaining = [item for item in subscriptions if item.get("origin") != origin]
        if len(remaining) == len(subscriptions):
            return "ℹ️ 当前会话没有订阅。"
        await self.put_kv_data("subscriptions", remaining)
        return "✅ 已解除当前会话的 FF14 助手提醒。"

    async def _cached_events(self) -> list[Any]:
        now = datetime.now()
        if self._event_cache_at and now - self._event_cache_at < timedelta(minutes=10):
            return self._event_cache
        try:
            self._event_cache = await self.ffxiv.get_event_items()
            self._event_cache_at = now
        except Exception:
            logger.warning("调度器读取活动失败", exc_info=True)
        return self._event_cache

    async def _cached_fashion(self) -> Any:
        now = datetime.now()
        if self._fashion_cache_at and now - self._fashion_cache_at < timedelta(minutes=10):
            return self._fashion_cache
        try:
            self._fashion_cache = await self.fashion.query()
            self._fashion_cache_at = now
        except Exception:
            logger.warning("调度器读取时尚评鉴失败", exc_info=True)
        return self._fashion_cache

    async def _dispatch_reminders(self) -> None:
        enabled = reminder_types(self.config)
        if not enabled:
            return
        events = await self._cached_events() if {"event_start", "event_end"} & enabled else []
        fashion = await self._cached_fashion() if "fashion" in enabled else None
        reminder_events = build_reminder_events(
            self.config,
            self.pvp,
            self.ocean,
            events,
            fashion,
        )
        due = due_events(reminder_events)
        if not due:
            return
        subscriptions = await self._subscriptions()
        sent = await self.get_kv_data("sent_reminders", {})
        if not isinstance(sent, dict):
            sent = {}
        changed = False
        for subscription in subscriptions:
            origin = subscription.get("origin")
            if not isinstance(origin, str) or not origin:
                continue
            for reminder in due:
                sent_key = f"{origin}|{reminder.event_id}"
                if sent_key in sent:
                    continue
                chain = MessageChain().message(f"🔔 {reminder.title}\n{reminder.message}")
                if reminder.image_url:
                    try:
                        from astrbot.api import message_components as Comp

                        chain.chain.append(Comp.Image.fromURL(reminder.image_url))
                    except Exception:
                        logger.debug("提醒图片组件创建失败", exc_info=True)
                try:
                    result = await self.context.send_message(origin, chain)
                    if result is not False:
                        sent[sent_key] = datetime.now().isoformat(timespec="seconds")
                        changed = True
                except Exception:
                    logger.warning("向 %s 推送 FF14 提醒失败", origin, exc_info=True)
        if changed:
            sent = dict(list(sent.items())[-2000:])
            await self.put_kv_data("sent_reminders", sent)

    async def _scheduler_loop(self) -> None:
        while True:
            try:
                await self._dispatch_reminders()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.error("FF14 助手调度器异常", exc_info=True)
            await asyncio.sleep(60)
