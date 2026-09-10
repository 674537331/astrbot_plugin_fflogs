"""HuijiWiki search service.

Only short extracts and structured facts are retained.  The plugin never
mirrors or bundles wiki pages, which keeps the cache useful while respecting
the source's CC BY-NC-SA requirements.
"""

from __future__ import annotations

import asyncio
import html
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .http import create_http_client
from .schedule import CHINA_TZ, future_et_windows
from .storage import JsonStore, get_plugin_data_dir

logger = logging.getLogger(__name__)

WIKI_API_URL = "https://ff14.huijiwiki.com/w/api.php"
WIKI_FALLBACK_API_URL = "https://cdn.huijiwiki.com/ff14/api.php"
WIKI_PAGE_URL = "https://ff14.huijiwiki.com/wiki/"
WIKI_CACHE_VERSION = 1
WIKI_CACHE_TTL_SECONDS = 60 * 60 * 6
WIKI_MAX_EXTRACT_LENGTH = 420


@dataclass(frozen=True)
class WikiResult:
    title: str
    page_type: str
    summary: str
    acquisition: str = ""
    unlock_info: str = ""
    quest_progress: str = ""
    time_windows: tuple[str, ...] = ()
    details: dict[str, str] = field(default_factory=dict)
    source_url: str = ""
    cached_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["time_windows"] = list(self.time_windows)
        result["url"] = result.pop("source_url")
        result["type"] = result.pop("page_type")
        result["details"] = dict(result["details"])
        return result


class WikiService:
    def __init__(
        self,
        config: dict[str, Any],
        data_dir: str | Path | None = None,
    ):
        self.config = config
        cache_dir = get_plugin_data_dir(data_dir) / "wiki"
        self.cache = JsonStore(cache_dir / "search.json")
        self._request_semaphore = asyncio.Semaphore(4)

    @staticmethod
    def _now_text() -> str:
        return datetime.now(timezone.utc).astimezone(CHINA_TZ).isoformat(timespec="seconds")

    @staticmethod
    def _cache_key(query: str) -> str:
        return re.sub(r"\s+", " ", query.strip()).casefold()

    def _read_cache(self, query: str) -> tuple[list[WikiResult], str | None]:
        payload = self.cache.read({})
        if not isinstance(payload, dict):
            return [], None
        item = payload.get(self._cache_key(query))
        if not isinstance(item, dict):
            return [], None
        cached_at = item.get("cached_at")
        raw_results = item.get("results")
        if not isinstance(raw_results, list):
            return [], cached_at if isinstance(cached_at, str) else None
        results = []
        for raw in raw_results:
            if not isinstance(raw, dict):
                continue
            try:
                raw_details = raw.get("details", {})
                results.append(
                    WikiResult(
                        title=str(raw.get("title", "")),
                        page_type=str(raw.get("page_type", raw.get("type", "普通"))),
                        summary=str(raw.get("summary", "")),
                        acquisition=str(raw.get("acquisition", "")),
                        unlock_info=str(raw.get("unlock_info", "")),
                        quest_progress=str(raw.get("quest_progress", "")),
                        time_windows=tuple(
                            str(value) for value in raw.get("time_windows", [])
                        ),
                        details={
                            str(key): str(value)
                            for key, value in raw_details.items()
                        }
                        if isinstance(raw_details, dict)
                        else {},
                        source_url=str(raw.get("source_url", raw.get("url", ""))),
                        cached_at=str(cached_at) if cached_at else None,
                    ),
                )
            except (TypeError, ValueError):
                continue
        return results, cached_at if isinstance(cached_at, str) else None

    def _write_cache(self, query: str, results: list[WikiResult]) -> None:
        payload = self.cache.read({})
        if not isinstance(payload, dict):
            payload = {}
        cached_at = self._now_text()
        payload[self._cache_key(query)] = {
            "version": WIKI_CACHE_VERSION,
            "cached_at": cached_at,
            "results": [
                {
                    **asdict(result),
                    "time_windows": list(result.time_windows),
                    "cached_at": cached_at,
                }
                for result in results
            ],
        }
        self.cache.write(payload)

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        endpoint: str,
        params: dict[str, Any],
    ) -> dict[str, Any]:
        async with self._request_semaphore:
            for attempt in range(3):
                response = await client.get(endpoint, params=params)
                if response.status_code in {403, 429}:
                    if attempt == 2:
                        response.raise_for_status()
                    retry_after = response.headers.get("Retry-After", "")
                    try:
                        delay = min(float(retry_after), 3.0)
                    except (TypeError, ValueError):
                        delay = 0.25 * (attempt + 1)
                    await asyncio.sleep(max(delay, 0.05))
                    continue
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    raise ValueError("Wiki API response is not an object")
                return payload
        raise RuntimeError("Wiki API request exhausted")

    @staticmethod
    def _trim_summary(value: str) -> str:
        value = html.unescape(re.sub(r"\s+", " ", value)).strip()
        if len(value) <= WIKI_MAX_EXTRACT_LENGTH:
            return value
        return value[: WIKI_MAX_EXTRACT_LENGTH - 1].rstrip() + "…"

    @staticmethod
    def classify(title: str, text: str, categories: list[str]) -> str:
        haystack = f"{title} {' '.join(categories)} {text}".casefold()
        if any(word in haystack for word in ("成就", "achievement")):
            return "成就"
        if any(word in haystack for word in ("主线", "任务", "quest", "scenario")):
            return "任务"
        if any(word in haystack for word in ("天气鱼", "钓鱼", "捕鱼", "fish")):
            return "天气鱼"
        if any(word in haystack for word in ("限时采集", "传承录", "采集点", "gather")):
            return "限时采集"
        if any(
            word in haystack
            for word in ("坐骑", "宠物", "乐谱", "幻卡", "发型", "情感动作", "配饰")
        ):
            return "收藏"
        if any(word in haystack for word in ("装备", "武器", "防具", "gear", "weapon")):
            return "装备"
        if any(word in haystack for word in ("材料", "素材", "矿石", "木材", "material")):
            return "材料"
        return "普通"

    @staticmethod
    def _extract_section(text: str, labels: tuple[str, ...]) -> str:
        for label in labels:
            match = re.search(
                rf"{re.escape(label)}\s*[:：]?\s*([^。\n]+)",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                return WikiService._trim_summary(match.group(1))
        return ""

    @classmethod
    def _make_result(cls, page: dict[str, Any]) -> WikiResult:
        title = str(page.get("title", "未命名词条"))
        extract = str(page.get("extract", ""))
        categories = [
            str(category.get("title", ""))
            for category in page.get("categories", [])
            if isinstance(category, dict)
        ]
        page_type = cls.classify(title, extract, categories)
        summary = cls._trim_summary(extract) or "Wiki未提供摘要。"
        acquisition = cls._extract_section(
            extract,
            ("获取方式", "获取", "来源", "兑换", "掉落", "采集", "制作"),
        )
        unlock_info = cls._extract_section(
            extract,
            ("解锁条件", "前置任务", "任务要求", "需要", "参与条件"),
        )
        quest_progress = cls._extract_section(
            extract,
            ("版本", "章节", "主线进度", "任务量"),
        )
        time_text = cls._extract_section(
            extract,
            ("出现时间", "时间", "窗口", "ET"),
        )
        details: dict[str, str] = {}
        detail_labels = {
            "钓场": ("钓场", "地图", "区域"),
            "鱼饵链": ("鱼饵链", "鱼饵", "钓饵"),
            "前置天气": ("前置天气", "天气条件", "天气"),
            "直感条件": ("直感条件", "直感"),
            "采集职业": ("职业", "采集职业"),
            "等级": ("等级", "采集等级"),
            "坐标": ("坐标", "位置"),
            "传承录": ("传承录", "采集手册"),
            "达成条件": ("达成条件", "完成条件"),
            "成就点数": ("成就点数", "成就点"),
            "称号": ("称号",),
            "物品奖励": ("物品奖励", "奖励物品"),
            "兑换数量": ("兑换数量", "兑换所需"),
            "NPC": ("NPC", "兑换NPC", "商人"),
            "副本": ("副本", "掉落副本"),
            "限时状态": ("限时状态", "限时"),
            "装等": ("装等", "Item Level", "物品等级"),
            "装备等级": ("装备等级",),
            "职业": ("适用职业", "职业要求"),
        }
        for key, labels in detail_labels.items():
            value = cls._extract_section(extract, labels)
            if value:
                details[key] = value
        time_windows = []
        if time_text:
            time_windows.append(time_text)
            et_windows = future_et_windows(time_text, count=3)
            time_windows.extend(
                f"北京时间 {start_at:%m-%d %H:%M}-{end_at:%H:%M}"
                for start_at, end_at, _ in et_windows
            )
        if page_type in {"限时采集", "天气鱼"} and not time_windows:
            details.setdefault("资料状态", "Wiki资料不完整：未找到可计算的ET窗口。")
        if page_type == "限时采集":
            missing = [
                key
                for key in ("采集职业", "等级", "坐标", "传承录")
                if not details.get(key)
            ]
            if missing:
                details.setdefault(
                    "资料状态",
                    f"Wiki资料不完整：缺少{'、'.join(missing)}，未进行猜测。",
                )
        if page_type == "天气鱼":
            missing = [
                key
                for key in ("钓场", "鱼饵链", "前置天气")
                if not details.get(key)
            ]
            if missing:
                details.setdefault(
                    "资料状态",
                    f"Wiki资料不完整：缺少{'、'.join(missing)}，未进行猜测。",
                )
        url = str(page.get("fullurl") or f"{WIKI_PAGE_URL}{quote(title)}")
        return WikiResult(
            title=title,
            page_type=page_type,
            summary=summary,
            acquisition=acquisition,
            unlock_info=unlock_info,
            quest_progress=quest_progress,
            time_windows=tuple(time_windows),
            details=details,
            source_url=url,
        )

    async def _fetch_remote(self, query: str, limit: int) -> list[WikiResult]:
        params = {
            "action": "query",
            "format": "json",
            "formatversion": 2,
            "utf8": 1,
            "list": "search",
            "srsearch": query,
            "srlimit": min(max(limit, 1), 5),
            "srprop": "snippet|titlesnippet",
        }
        async with create_http_client(self.config, timeout=15.0) as client:
            last_error: Exception | None = None
            search_payload: dict[str, Any] | None = None
            for endpoint in (WIKI_API_URL, WIKI_FALLBACK_API_URL):
                try:
                    search_payload = await self._request_json(client, endpoint, params)
                    break
                except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
                    last_error = exc
                    logger.warning("Wiki endpoint failed (%s): %s", endpoint, type(exc).__name__)
            if search_payload is None:
                raise last_error or RuntimeError("Wiki search failed")

            search_data = search_payload.get("query", {})
            search_items = search_data.get("search", []) if isinstance(search_data, dict) else []
            titles = [
                str(item.get("title"))
                for item in search_items
                if isinstance(item, dict) and item.get("title")
            ][:limit]
            if not titles:
                return []

            page_params = {
                "action": "query",
                "format": "json",
                "formatversion": 2,
                "utf8": 1,
                "titles": "|".join(titles),
                "redirects": 1,
                "prop": "extracts|info|categories",
                "exintro": 1,
                "explaintext": 1,
                "exchars": WIKI_MAX_EXTRACT_LENGTH,
                "inprop": "url",
                "cllimit": "max",
            }
            page_payload = None
            for endpoint in (WIKI_API_URL, WIKI_FALLBACK_API_URL):
                try:
                    page_payload = await self._request_json(client, endpoint, page_params)
                    break
                except (httpx.HTTPError, OSError, ValueError, RuntimeError) as exc:
                    last_error = exc
                    logger.warning("Wiki page endpoint failed (%s): %s", endpoint, type(exc).__name__)
            if page_payload is None:
                # Search results are still useful when the page-detail call is
                # rate-limited or temporarily unavailable.  Keep these as
                # explicitly labelled candidates rather than inventing facts.
                return [
                    WikiResult(
                        title=title,
                        page_type="候选",
                        summary="Wiki详情接口暂时不可用，已保留搜索标题；请打开原文链接查看。",
                        source_url=f"{WIKI_PAGE_URL}{quote(title)}",
                    )
                    for title in titles
                ]

        page_query = page_payload.get("query", {})
        pages = page_query.get("pages", []) if isinstance(page_query, dict) else []
        if not isinstance(pages, list):
            return []
        page_by_title = {
            str(page.get("title")): page
            for page in pages
            if isinstance(page, dict) and page.get("title")
        }
        redirect_map = {}
        if isinstance(page_query, dict) and isinstance(page_query.get("redirects"), list):
            redirect_map = {
                str(item.get("from")): str(item.get("to"))
                for item in page_query["redirects"]
                if isinstance(item, dict) and item.get("from") and item.get("to")
            }
        results = []
        for title in titles:
            resolved_title = redirect_map.get(title, title)
            page = page_by_title.get(resolved_title) or page_by_title.get(title)
            if page:
                results.append(self._make_result(page))
        return results

    async def search_ff14_wiki(self, query: str, limit: int = 5) -> list[WikiResult]:
        """Search FF14 Wiki and return at most five structured short results."""

        query = re.sub(r"\s+", " ", query.strip())
        try:
            limit = int(limit)
        except (TypeError, ValueError):
            limit = 5
        limit = min(max(limit, 1), 5)
        if not query:
            return []
        cached, cached_at = self._read_cache(query)
        try:
            results = await self._fetch_remote(query, limit)
            if results:
                if any(result.page_type != "候选" for result in results):
                    self._write_cache(query, results)
                return results[:limit]
            return cached[:limit]
        except Exception as exc:
            logger.warning("Wiki search failed for %r: %s", query, type(exc).__name__)
            if cached:
                return cached[:limit]
            # The caller can still offer a useful direct link when both API
            # endpoints fail, without pretending that data was retrieved.
            return [
                WikiResult(
                    title=query,
                    page_type="候选",
                    summary=(
                        "Wiki搜索接口暂时不可用（主端点和备用端点均失败，"
                        "可能是 Cloudflare 验证或网络/代理问题）；当前没有可用缓存。"
                    ),
                    source_url=f"https://ff14.huijiwiki.com/index.php?search={quote(query)}",
                    cached_at=cached_at,
                ),
            ]

    @staticmethod
    def _format_single(result: WikiResult) -> str:
        lines = [f"📚 【{result.title}】（{result.page_type}）", result.summary]
        if result.page_type == "普通":
            points = []
            for label, value in (
                ("获取/来源", result.acquisition),
                ("解锁/前置", result.unlock_info),
                ("任务信息", result.quest_progress),
            ):
                if value:
                    points.append(f"{label}：{value}")
            points.extend(f"{key}：{value}" for key, value in result.details.items())
            lines.extend(f"重点：{point}" for point in points[:3])
        else:
            if result.acquisition:
                lines.append(f"获取/来源：{result.acquisition}")
            if result.unlock_info:
                lines.append(f"解锁/前置：{result.unlock_info}")
            if result.quest_progress:
                lines.append(f"任务信息：{result.quest_progress}")
            if result.time_windows:
                lines.append(f"时间窗口：{'；'.join(result.time_windows)}")
            for key, value in result.details.items():
                lines.append(f"{key}：{value}")
        if result.cached_at:
            lines.append(f"（缓存于 {result.cached_at}）")
        if result.page_type == "候选":
            lines.append("⚠️ 这是搜索候选标题，详细资料暂未返回；请稍后重试。")
        lines.append(result.source_url)
        return "\n".join(lines)

    @staticmethod
    def format_results(query: str, results: list[WikiResult]) -> str:
        if not results:
            return f"❌ Wiki没有找到“{query}”的候选词条。"
        if len(results) == 1:
            return WikiService._format_single(results[0])
        exact = [
            result
            for result in results
            if result.title.casefold() == query.casefold()
            and result.page_type != "候选"
        ]
        if len(exact) == 1:
            return WikiService._format_single(exact[0])

        lines = [f"🔎 “{query}”有多个候选词条，请在5分钟内回复 /ff14 wiki #编号："]
        for index, result in enumerate(results[:5], start=1):
            summary = WikiService._trim_summary(result.summary)
            cached_mark = f"（缓存于 {result.cached_at}）" if result.cached_at else ""
            lines.append(
                f"#{index} [{result.page_type}] {result.title}{cached_mark}：{summary}\n"
                f"{result.source_url}",
            )
        return "\n".join(lines)
