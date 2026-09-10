"""FF14资料查询兼容层。

命令名和 ``WikiService`` 类名保留是为了兼容插件内部旧调用；运行时
不再访问旧百科接口，而是通过 FFCafe 维护的 XIVAPI v2 读取短字段。
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schedule import CHINA_TZ, future_et_windows
from .storage import JsonStore, get_plugin_data_dir
from .xivapi import XIVAPI_DOC_URL, XIVAPIRow, XIVAPIService

logger = logging.getLogger(__name__)

WIKI_CACHE_VERSION = 2
WIKI_MAX_EXTRACT_LENGTH = 420

DETAIL_FIELDS_BY_SHEET = {
    "Quest": (
        "Name,Description,Expansion.Name,JournalGenre.Name,JournalGenre.JournalCategory.Name,"
        "ClassJobLevel,PreviousQuest@as(raw),IssuerStart.Name,IssuerStart.TerritoryType.Name"
    ),
    "Item": (
        "Name,Description,ItemUICategory.Name,LevelItem,LevelEquip,"
        "ClassJobCategory.Name,EquipSlotCategory.Name,ItemAction.Name"
    ),
    "Achievement": "Name,Description,Points,Title.Name,Reward.Name",
    "Mount": "Name,Description,Item.Name",
    "Companion": "Name,Description,Item.Name",
    "Emote": "Name,Description,UnlockLink",
    "Orchestrion": "Name,Description,Item.Name",
    "TripleTriadCard": "Name,Description,Stars,Type.Name",
    "GatheringItem": "Name,Description,Level,Item.Name,GatheringItemLevel",
    "GatheringPoint": "Name,Description,Level,TerritoryType.Name,PlaceName.Name",
    "FishParameter": (
        "Name,Description,FishingSpot.Name,FishingSpot.TerritoryType.Name,"
        "TimeOfDay,Weather,Predator,BigFishSize"
    ),
    "FishingSpot": "Name,Description,TerritoryType.Name,PlaceName.Name",
    "Recipe": "Name,Description,ItemResult.Name,ItemResultQuantity,RecipeLevelTable.Level",
    "GilShop": "Name,Description,Item.Name,Price",
    "SpecialShop": "Name,Description,Item.Name,Cost.Name,CostQuantity",
}


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
    sheet: str = ""
    row_id: int | None = None
    api_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["time_windows"] = list(self.time_windows)
        result["url"] = result.pop("source_url")
        result["type"] = result.pop("page_type")
        result["details"] = dict(result["details"])
        return result

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WikiResult:
        windows = raw.get("time_windows", [])
        details = raw.get("details", {})
        if not isinstance(windows, (list, tuple)):
            windows = []
        if not isinstance(details, dict):
            details = {}
        row_id = raw.get("row_id")
        try:
            row_id = int(row_id) if row_id not in {None, ""} else None
        except (TypeError, ValueError):
            row_id = None
        return cls(
            title=str(raw.get("title", "")),
            page_type=str(raw.get("page_type", raw.get("type", "普通"))),
            summary=str(raw.get("summary", "")),
            acquisition=str(raw.get("acquisition", "")),
            unlock_info=str(raw.get("unlock_info", "")),
            quest_progress=str(raw.get("quest_progress", "")),
            time_windows=tuple(str(value) for value in windows),
            details={str(key): str(value) for key, value in details.items()},
            source_url=str(raw.get("source_url", raw.get("url", ""))),
            cached_at=str(raw["cached_at"]) if raw.get("cached_at") else None,
            sheet=str(raw.get("sheet", "")),
            row_id=row_id,
            api_version=str(raw.get("api_version", "")),
        )


def _value(fields: dict[str, Any], key: str, default: Any = "") -> Any:
    value = fields.get(key, default)
    if isinstance(value, dict) and isinstance(value.get("fields"), dict):
        return value["fields"].get("Name", value.get("value", default))
    return value


def _relation_name(fields: dict[str, Any], key: str) -> str:
    value = fields.get(key)
    if not isinstance(value, dict):
        return ""
    nested = value.get("fields")
    if not isinstance(nested, dict):
        return ""
    return str(nested.get("Name", "")).strip()


class WikiService:
    """Search and format short XIVAPI v2 results.

    The class name is intentionally retained so existing plugin installations
    and LLM-tool registrations do not need a breaking migration.
    """

    def __init__(
        self,
        config: dict[str, Any],
        data_dir: str | Path | None = None,
        xivapi: XIVAPIService | None = None,
    ):
        self.config = config
        self.xivapi = xivapi or XIVAPIService(config, data_dir)
        self.cache = JsonStore(get_plugin_data_dir(data_dir) / "xivapi" / "results.json")
        self.last_error: str | None = None

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
        if not isinstance(item, dict) or not isinstance(item.get("results"), list):
            return [], None
        cached_at = str(item.get("cached_at", "")) or None
        results = []
        for raw in item["results"]:
            if isinstance(raw, dict):
                try:
                    results.append(WikiResult.from_dict({**raw, "cached_at": cached_at}))
                except (TypeError, ValueError):
                    continue
        return results, cached_at

    def _write_cache(self, query: str, results: list[WikiResult]) -> None:
        payload = self.cache.read({})
        if not isinstance(payload, dict):
            payload = {}
        cached_at = self._now_text()
        payload[self._cache_key(query)] = {
            "version": WIKI_CACHE_VERSION,
            "cached_at": cached_at,
            "results": [
                {**result.to_dict(), "cached_at": cached_at}
                for result in results[:5]
            ],
        }
        self.cache.write(payload)

    @staticmethod
    def _trim_summary(value: str) -> str:
        value = html.unescape(re.sub(r"\s+", " ", value)).strip()
        if len(value) <= WIKI_MAX_EXTRACT_LENGTH:
            return value
        return value[: WIKI_MAX_EXTRACT_LENGTH - 1].rstrip() + "…"

    @staticmethod
    def classify(title: str, text: str, categories: list[str]) -> str:
        """Compatibility classifier for old callers passing free text."""

        haystack = f"{title} {' '.join(categories)} {text}".casefold()
        if "成就" in haystack or "achievement" in haystack:
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

    @classmethod
    def _extract_section(cls, text: str, labels: tuple[str, ...]) -> str:
        for label in labels:
            match = re.search(
                rf"{re.escape(label)}\s*[:：]?\s*([^。\n]+)",
                text,
                flags=re.IGNORECASE,
            )
            if match:
                return cls._trim_summary(match.group(1))
        return ""

    @staticmethod
    def _sheet_type(row: XIVAPIRow) -> str:
        sheet = row.sheet
        fields = row.fields
        if sheet == "Quest":
            return "任务"
        if sheet == "Achievement":
            return "成就"
        if sheet in {"Mount", "Companion", "Emote", "Orchestrion", "TripleTriadCard"}:
            return "收藏"
        if sheet in {"FishParameter", "FishingSpot"}:
            return "天气鱼"
        if sheet in {"GatheringItem", "GatheringPoint"}:
            return "限时采集"
        if sheet == "Recipe":
            return "制作"
        if sheet in {"GilShop", "SpecialShop"}:
            return "商店"
        category = _relation_name(fields, "ItemUICategory")
        if any(word in category for word in ("武器", "防具", "盾")) or _value(fields, "LevelEquip"):
            return "装备"
        if any(word in category for word in ("素材", "材料")):
            return "材料"
        return "普通"

    @classmethod
    def _make_result(cls, page: dict[str, Any] | XIVAPIRow) -> WikiResult:
        """Build a short result from an XIVAPI row or legacy test payload."""

        if isinstance(page, XIVAPIRow):
            fields = page.fields
            title = str(_value(fields, "Name", "未命名词条"))
            page_type = cls._sheet_type(page)
            description = str(
                _value(fields, "Description")
                or _value(fields, "Text")
                or _value(fields, "Tooltip")
                or "",
            )
            expansion = _relation_name(fields, "Expansion")
            journal = _relation_name(fields, "JournalGenre")
            category = _relation_name(fields, "ItemUICategory")
            details: dict[str, str] = {"数据表": page.sheet}
            if expansion:
                details["所属版本"] = expansion
            if journal:
                details["任务分类"] = journal
            if category:
                details["物品分类"] = category
            for label, key in (
                ("获取方式", "Acquisition"),
                ("解锁条件", "Unlock"),
                ("奖励", "Reward"),
                ("奖励物品", "Item"),
                ("兑换物品", "ItemResult"),
                ("兑换成本", "Cost"),
                ("称号", "Title"),
                ("钓场", "FishingSpot"),
                ("NPC", "IssuerStart"),
                ("地图", "TerritoryType"),
                ("职业", "ClassJobCategory"),
            ):
                value = _relation_name(fields, key) or str(fields.get(key, "")).strip()
                if value and value not in {"0", "[]"}:
                    details[label] = value
            for label, key in (
                ("装等", "LevelItem"),
                ("装备等级", "LevelEquip"),
                ("等级", "ClassJobLevel"),
                ("采集等级", "Level"),
                ("采集手册等级", "GatheringItemLevel"),
                ("出现时间", "TimeOfDay"),
                ("天气", "Weather"),
                ("前置鱼", "Predator"),
                ("大鱼尺寸", "BigFishSize"),
            ):
                value = _value(fields, key)
                if value not in ("", None, [], [0, 0]):
                    details[label] = str(value)
            if page_type in {"收藏", "成就", "任务", "天气鱼", "限时采集"} and not description:
                details.setdefault("资料状态", "XIVAPI资料不完整：当前字段未提供短摘要，未进行猜测。")
            if page_type in {"天气鱼", "限时采集"}:
                details.setdefault("资料状态", "XIVAPI资料不完整：时间、地点或前置条件需更多数据字段。")
            summary = cls._trim_summary(description)
            if not summary:
                summary = f"XIVAPI v2 已找到{page_type}“{title}”的数据行。"
            return WikiResult(
                title=title,
                page_type=page_type,
                summary=summary,
                details=details,
                source_url=XIVAPIService.row_url(page),
                sheet=page.sheet,
                row_id=page.row_id,
                api_version=page.api_version,
            )

        title = str(page.get("title", "未命名词条"))
        extract = str(page.get("extract", page.get("summary", "")))
        categories = [
            str(category.get("title", ""))
            for category in page.get("categories", [])
            if isinstance(category, dict)
        ]
        page_type = cls.classify(title, extract, categories)
        acquisition = cls._extract_section(
            extract,
            ("获取方式", "获取", "来源", "兑换", "掉落", "采集", "制作"),
        )
        unlock_info = cls._extract_section(
            extract,
            ("解锁条件", "前置任务", "任务要求", "需要", "参与条件"),
        )
        quest_progress = cls._extract_section(extract, ("版本", "章节", "主线进度", "任务量"))
        time_text = cls._extract_section(extract, ("出现时间", "时间", "窗口", "ET"))
        details: dict[str, str] = {}
        for key, labels in {
            "钓场": ("钓场", "地图", "区域"),
            "鱼饵链": ("鱼饵链", "鱼饵", "钓饵"),
            "前置天气": ("前置天气", "天气条件", "天气"),
            "直感条件": ("直感条件", "直感"),
            "采集职业": ("职业", "采集职业"),
            "等级": ("等级", "采集等级"),
            "坐标": ("坐标", "位置"),
            "传承录": ("传承录", "采集手册"),
        }.items():
            value = cls._extract_section(extract, labels)
            if value:
                details[key] = value
        time_windows: list[str] = []
        if time_text:
            time_windows.append(time_text)
            time_windows.extend(
                f"北京时间 {start_at:%m-%d %H:%M}-{end_at:%H:%M}"
                for start_at, end_at, _ in future_et_windows(time_text, count=3)
            )
        if page_type in {"限时采集", "天气鱼"} and not time_windows:
            details["资料状态"] = "XIVAPI资料不完整：未找到可计算的ET窗口。"
        if page_type == "限时采集":
            missing = [key for key in ("采集职业", "等级", "坐标", "传承录") if key not in details]
            if missing:
                details.setdefault(
                    "资料状态",
                    f"XIVAPI资料不完整：缺少{'、'.join(missing)}，未进行猜测。",
                )
        if page_type == "天气鱼":
            missing = [key for key in ("钓场", "鱼饵链", "前置天气") if key not in details]
            if missing:
                details.setdefault(
                    "资料状态",
                    f"XIVAPI资料不完整：缺少{'、'.join(missing)}，未进行猜测。",
                )
        return WikiResult(
            title=title,
            page_type=page_type,
            summary=cls._trim_summary(extract) or "资料未提供摘要。",
            acquisition=acquisition,
            unlock_info=unlock_info,
            quest_progress=quest_progress,
            time_windows=tuple(time_windows),
            details=details,
            source_url=str(page.get("fullurl", page.get("url", ""))),
        )

    async def _fetch_remote(self, query: str, limit: int) -> list[WikiResult]:
        rows = await self.xivapi.search_rows(query, limit)
        search_cached_at = (
            getattr(self.xivapi, "last_cached_at", None)
            if getattr(self.xivapi, "last_result_from_cache", False)
            else None
        )
        exact_rows = [
            row
            for row in rows
            if str(_value(row.fields, "Name", "")).casefold() == query.casefold()
        ]
        if len(exact_rows) == 1:
            row = exact_rows[0]
            get_row = getattr(self.xivapi, "get_row", None)
            fields = DETAIL_FIELDS_BY_SHEET.get(row.sheet)
            if callable(get_row) and fields:
                detailed = await get_row(row.sheet, row.row_id, fields)
                if detailed:
                    rows = [detailed if candidate.row_id == row.row_id else candidate for candidate in rows]
        results = [self._make_result(row) for row in rows[:limit]]
        cached_at = search_cached_at
        if not cached_at and getattr(self.xivapi, "last_result_from_cache", False):
            cached_at = getattr(self.xivapi, "last_cached_at", None)
        if cached_at:
            cached_text = datetime.fromtimestamp(cached_at, timezone.utc).astimezone(
                CHINA_TZ,
            ).isoformat(timespec="seconds")
            results = [
                WikiResult(**{**result.__dict__, "cached_at": cached_text})
                for result in results
            ]
        return results

    async def search_ff14_wiki(self, query: str, limit: int = 5) -> list[WikiResult]:
        """Search XIVAPI v2 and return at most five structured short results."""

        query = re.sub(r"\s+", " ", query.strip())
        try:
            limit = min(max(int(limit), 1), 5)
        except (TypeError, ValueError):
            limit = 5
        if not query:
            self.last_error = None
            return []
        cached, cached_at = self._read_cache(query)
        self.last_error = None
        try:
            results = await self._fetch_remote(query, limit)
        except Exception as exc:
            logger.warning("XIVAPI资料查询失败：%s", type(exc).__name__)
            results = []
            self.last_error = f"XIVAPI v2 暂时不可用：{type(exc).__name__}"
        if results:
            if not any(result.cached_at for result in results):
                self._write_cache(query, results)
            return results[:limit]
        api_error = getattr(self.xivapi, "last_error", None)
        if api_error:
            self.last_error = api_error
        if cached:
            self.last_error = self.last_error or "XIVAPI v2 暂时不可用，已返回缓存。"
            return [
                WikiResult(**{**result.__dict__, "cached_at": cached_at})
                for result in cached[:limit]
            ]
        return []

    @staticmethod
    def _format_single(result: WikiResult) -> str:
        lines = [f"📚 【{result.title}】（{result.page_type}）", result.summary]
        if result.acquisition:
            lines.append(f"获取/来源：{result.acquisition}")
        if result.unlock_info:
            lines.append(f"解锁/前置：{result.unlock_info}")
        if result.quest_progress:
            lines.append(f"任务信息：{result.quest_progress}")
        if result.time_windows:
            lines.append(f"时间窗口：{'；'.join(result.time_windows)}")
        for key, value in list(result.details.items())[:8]:
            lines.append(f"{key}：{value}")
        if result.cached_at:
            lines.append(f"（缓存于 {result.cached_at}）")
        if result.source_url:
            lines.append(result.source_url)
        return "\n".join(lines)

    @staticmethod
    def format_unavailable(query: str, error: str | None = None) -> str:
        reason = error or "主接口未返回数据"
        return (
            f"⚠️ FF14资料接口暂时不可用，无法确认“{query}”。\n"
            f"原因：{reason}\n"
            f"请稍后重试；接口文档：{XIVAPI_DOC_URL}"
        )

    @staticmethod
    def format_results(query: str, results: list[WikiResult]) -> str:
        if not results:
            return f"❌ FF14资料中没有找到“{query}”的候选词条。"
        if len(results) == 1:
            return WikiService._format_single(results[0])
        exact = [
            result
            for result in results
            if result.title.casefold() == query.casefold()
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
