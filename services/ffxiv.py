import asyncio
import html
import logging
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

import httpx

from .http import create_http_client

logger = logging.getLogger(__name__)

CN_DCS = ("陆行鸟", "莫古力", "猫小胖", "豆豆柴")
CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
XIVAPI_SEARCH_URL = "https://xivapi-v2.xivcdn.com/api/search"
UNIVERSALIS_API_URL = "https://universalis.app/api/v2"
SERVER_STATUS_URL = "https://ff14act.web.sdo.com/api/serverStatus/getServerStatus"
NEWS_LIST_URL = "https://cqnews.web.sdo.com/api/news/newsList"
NEWS_DETAIL_URL = "https://cqnews.web.sdo.com/api/news/newsDetail"
NEWS_DETAIL_BASE_URL = "https://ff.web.sdo.com/web8/index.html#/newstab/newscont"
NEWS_CATEGORY_CODES = "8324,8325,8326,8327,5309,5310,5311,5312,5313"
MAINTENANCE_CATEGORY_CODE = "8324"
MAINTENANCE_DONE_KEYWORDS = (
    "完成公告",
    "维护完成",
    "现已完成",
    "维护现已完成",
)
IMPORTANT_MAINTENANCE_KEYWORDS = (
    "全区全服更新维护公告",
    "临时维护",
    "停机维护",
    "无法登录游戏",
    "服务器临时维护",
)
LOW_IMPACT_MAINTENANCE_KEYWORDS = (
    "调整维护",
    "调整优化",
    "网络线路调整",
    "线路优化",
)


class FFXIVAPIError(RuntimeError):
    """An upstream FF14 service returned an invalid response."""


@dataclass(frozen=True)
class MaintenanceItem:
    title: str
    url: str
    status: str
    impact_level: str
    impact_text: str
    start_at: datetime
    end_at: datetime


@dataclass(frozen=True)
class EventItem:
    title: str
    url: str
    category: str
    date_text: str
    start_at: datetime | None
    end_at: datetime | None
    date_confirmed: bool


class FFXIVService:
    def __init__(self, config: Mapping[str, Any]):
        self.config = config

    def get_news_count(self) -> int:
        try:
            count = int(self.config.get("news_count", 5))
        except (TypeError, ValueError):
            count = 5
        return min(max(count, 1), 20)

    def show_low_impact_maintenance(self) -> bool:
        value = self.config.get("show_low_impact_maintenance", False)
        if isinstance(value, str):
            return value.strip().lower() in {
                "1",
                "true",
                "yes",
                "on",
                "是",
                "开启",
            }
        return bool(value)

    @staticmethod
    def _xivapi_query_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    async def _search_item_id(
        self,
        client: httpx.AsyncClient,
        item_name: str,
    ) -> tuple[int | None, str | None]:
        params = {
            "sheets": "Item",
            "fields": "Name",
            "query": f'Name~"{self._xivapi_query_value(item_name)}"',
            "limit": 10,
            "language": "chs",
        }
        response = await client.get(XIVAPI_SEARCH_URL, params=params)
        response.raise_for_status()
        results = response.json().get("results", [])
        if not isinstance(results, list) or not results:
            return None, None

        for item in results:
            if not isinstance(item, dict):
                continue
            name = item.get("fields", {}).get("Name", "")
            if isinstance(name, str) and name.casefold() == item_name.casefold():
                return item.get("row_id"), name

        first = results[0]
        if not isinstance(first, dict):
            return None, None
        name = first.get("fields", {}).get("Name")
        return first.get("row_id"), name if isinstance(name, str) else None

    async def _get_dc_lowest_price(
        self,
        client: httpx.AsyncClient,
        item_id: int,
        dc: str,
    ) -> dict[str, Any] | None:
        url = f"{UNIVERSALIS_API_URL}/{quote(dc)}/{item_id}"
        response = await client.get(url, params={"listings": 1})
        response.raise_for_status()
        listings = response.json().get("listings", [])
        if not isinstance(listings, list) or not listings:
            return None
        listing = listings[0]
        return listing if isinstance(listing, dict) else None

    async def query_prices(self, item_name: str) -> str:
        item_name = item_name.strip()
        if not item_name:
            return "❌ 物品名不能为空。"

        async with create_http_client(self.config, timeout=15.0) as client:
            item_id, real_name = await self._search_item_id(client, item_name)
            if item_id is None or not real_name:
                return f"❌ 未找到物品: {item_name}，请检查名称。"

            price_results = await asyncio.gather(
                *(self._get_dc_lowest_price(client, item_id, dc) for dc in CN_DCS),
                return_exceptions=True,
            )

        message = [f"💵 【{real_name}】全大区最低价一览"]
        for dc, result in zip(CN_DCS, price_results, strict=True):
            if isinstance(result, Exception):
                logger.warning(
                    "获取 %s 物价失败: %s",
                    dc,
                    type(result).__name__,
                )
                result = None
            if result:
                hq_mark = " (HQ)" if result.get("hq", False) else ""
                message.append(
                    f"[{dc}] {result.get('pricePerUnit', '未知')} 金币"
                    f" @ {result.get('worldName', '未知')}"
                    f" x{result.get('quantity', 0)}{hq_mark}",
                )
            else:
                message.append(f"[{dc}] 暂无在售或查询失败")
        return "\n".join(message)

    @staticmethod
    def _format_preferred_status(server: Mapping[str, Any]) -> str:
        if server.get("isnew", False):
            return "优待状态: 特别优待"
        if server.get("isupgrade", False):
            return "优待状态: 优待"
        return "优待状态: 普通"

    @classmethod
    def format_server_status(cls, data: list[dict[str, Any]]) -> str:
        message = ["🌐 国服服务器状态一览"]
        for area in data:
            area_name = area.get("AreaName", "未知大区")
            servers = area.get("Group", [])
            message.append(f"\n【{area_name}】")
            if not isinstance(servers, list) or not servers:
                message.append("  暂无服务器状态")
                continue
            for server in servers:
                if not isinstance(server, dict) or server.get("iskong"):
                    continue
                statuses = [
                    "运行中" if server.get("runing", False) else "维护中",
                    "可转入" if server.get("isint", False) else "不可转入",
                    "可转出" if server.get("isout", False) else "不可转出",
                    ("可创建新角色" if server.get("iscreate", False) else "不可创建新角色"),
                    cls._format_preferred_status(server),
                ]
                message.append(
                    f"  {server.get('name', '未知服务器')}: {' / '.join(statuses)}",
                )
        return "\n".join(message)

    async def query_server_status(self) -> str:
        async with create_http_client(self.config, timeout=10.0) as client:
            response = await client.get(SERVER_STATUS_URL)
            response.raise_for_status()
            data = response.json()

        if not data.get("IsSuccess"):
            raise FFXIVAPIError("server status API returned failure")
        areas = data.get("Data", [])
        if not isinstance(areas, list):
            raise FFXIVAPIError("server status API returned invalid data")
        return self.format_server_status(areas)

    @staticmethod
    def official_news_url(item: Mapping[str, Any]) -> str:
        out_link = item.get("OutLink")
        if isinstance(out_link, str) and out_link.strip():
            return out_link.strip()
        news_id = item.get("Id")
        return f"{NEWS_DETAIL_BASE_URL}/{news_id}" if news_id is not None else NEWS_DETAIL_BASE_URL

    @staticmethod
    def _plain_text_from_html(content: str) -> str:
        text = re.sub(r"<br\s*/?>", "\n", content or "", flags=re.IGNORECASE)
        text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text).replace("\xa0", " ")
        text = re.sub(r"[ \t]+", " ", text)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    async def _fetch_news_list(
        self,
        client: httpx.AsyncClient,
        category_codes: str,
        page_size: int,
        page_index: int = 0,
    ) -> list[dict[str, Any]]:
        response = await client.get(
            NEWS_LIST_URL,
            params={
                "gameCode": "ff",
                "CategoryCode": category_codes,
                "pageIndex": page_index,
                "pageSize": page_size,
            },
        )
        response.raise_for_status()
        data = response.json()
        if str(data.get("Code")) != "0":
            raise FFXIVAPIError("news list API returned failure")
        items = data.get("Data", [])
        return [item for item in items if isinstance(item, dict)]

    async def _fetch_news_detail(
        self,
        client: httpx.AsyncClient,
        news_id: int,
    ) -> dict[str, Any]:
        response = await client.get(
            NEWS_DETAIL_URL,
            params={"gameCode": "ff", "id": news_id},
        )
        response.raise_for_status()
        data = response.json()
        if str(data.get("Code")) != "0":
            raise FFXIVAPIError("news detail API returned failure")
        detail = data.get("Data", {})
        return detail if isinstance(detail, dict) else {}

    @classmethod
    def format_news_list(cls, items: list[dict[str, Any]]) -> str:
        if not items:
            return "📰 暂无官方新闻。"
        message = ["📰 最新官方情报"]
        for index, item in enumerate(items, start=1):
            publish_date = str(item.get("PublishDate", "")).split(" ")[0].replace("/", "-")
            title = item.get("Title", "未命名公告")
            message.append(
                f"{index}. [{publish_date}] {title}\n{cls.official_news_url(item)}",
            )
        return "\n".join(message)

    async def query_news(self) -> str:
        count = self.get_news_count()
        async with create_http_client(self.config, timeout=10.0) as client:
            items = await self._fetch_news_list(
                client,
                NEWS_CATEGORY_CODES,
                count,
            )
        return self.format_news_list(items[:count])

    @staticmethod
    def _is_maintenance_candidate(item: Mapping[str, Any]) -> bool:
        text = f"{item.get('Title', '')} {item.get('Summary', '')}"
        return "维护" in text and not any(keyword in text for keyword in MAINTENANCE_DONE_KEYWORDS)

    @staticmethod
    def _classify_maintenance_impact(text: str) -> tuple[str, str]:
        if any(keyword in text for keyword in IMPORTANT_MAINTENANCE_KEYWORDS):
            return "important", "重点维护，可能影响登录"
        if any(keyword in text for keyword in LOW_IMPACT_MAINTENANCE_KEYWORDS):
            return "low", "网络/线路调整，通常只影响少部分用户"
        return "general", "一般维护，可能影响部分功能"

    @staticmethod
    def _normalize_now(now: datetime | None) -> datetime:
        if now is None:
            return datetime.now(CHINA_TZ)
        if now.tzinfo is None:
            return now.replace(tzinfo=CHINA_TZ)
        return now.astimezone(CHINA_TZ)

    @classmethod
    def _infer_year(
        cls,
        month: int,
        day: int,
        now: datetime,
    ) -> int:
        candidate = datetime(now.year, month, day, tzinfo=CHINA_TZ)
        if candidate < now - timedelta(days=180):
            return now.year + 1
        if candidate > now + timedelta(days=180):
            return now.year - 1
        return now.year

    @classmethod
    def parse_maintenance_time(
        cls,
        text: str,
        now: datetime | None = None,
    ) -> tuple[datetime | None, datetime | None]:
        now = cls._normalize_now(now)
        time_range = re.search(
            r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*"
            r"(\d{1,2}):(\d{2})\s*(?:-|~|—|至|到)\s*"
            r"(?:(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*)?"
            r"(\d{1,2}):(\d{2})",
            text,
        )
        try:
            if time_range:
                (
                    start_year,
                    start_month,
                    start_day,
                    start_hour,
                    start_minute,
                    end_year,
                    end_month,
                    end_day,
                    end_hour,
                    end_minute,
                ) = time_range.groups()
                start_month_int = int(start_month)
                start_day_int = int(start_day)
                start_year_int = int(start_year or 0) or cls._infer_year(
                    start_month_int,
                    start_day_int,
                    now,
                )
                end_year_int = int(end_year or start_year_int)
                end_month_int = int(end_month or start_month_int)
                end_day_int = int(end_day or start_day_int)
                start_at = datetime(
                    start_year_int,
                    start_month_int,
                    start_day_int,
                    int(start_hour),
                    int(start_minute),
                    tzinfo=CHINA_TZ,
                )
                end_at = datetime(
                    end_year_int,
                    end_month_int,
                    end_day_int,
                    int(end_hour),
                    int(end_minute),
                    tzinfo=CHINA_TZ,
                )
                if end_at < start_at:
                    end_at += timedelta(days=1)
                return start_at, end_at

            date_range = re.search(
                r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*(?:至|到|-|~|—)\s*"
                r"(?:(?:(\d{4})年)?(\d{1,2})月)?(\d{1,2})日",
                text,
            )
            if date_range:
                (
                    start_year,
                    start_month,
                    start_day,
                    end_year,
                    end_month,
                    end_day,
                ) = date_range.groups()
                start_month_int = int(start_month)
                start_day_int = int(start_day)
                start_year_int = int(start_year or 0) or cls._infer_year(
                    start_month_int,
                    start_day_int,
                    now,
                )
                end_at = datetime(
                    int(end_year or start_year_int),
                    int(end_month or start_month_int),
                    int(end_day),
                    23,
                    59,
                    59,
                    tzinfo=CHINA_TZ,
                )
                start_at = datetime(
                    start_year_int,
                    start_month_int,
                    start_day_int,
                    tzinfo=CHINA_TZ,
                )
                return start_at, end_at
        except ValueError:
            return None, None
        return None, None

    @classmethod
    def build_maintenance_item(
        cls,
        item: Mapping[str, Any],
        detail: Mapping[str, Any],
        now: datetime | None = None,
    ) -> MaintenanceItem | None:
        title = str(
            detail.get("Title") or item.get("Title") or "未命名维护公告",
        )
        summary = str(detail.get("Summary") or item.get("Summary") or "")
        content_text = cls._plain_text_from_html(
            str(detail.get("Content") or ""),
        )
        all_text = f"{title}\n{summary}\n{content_text}"
        if any(keyword in all_text for keyword in MAINTENANCE_DONE_KEYWORDS):
            return None

        start_at, end_at = cls.parse_maintenance_time(all_text, now)
        current = cls._normalize_now(now)
        if start_at is None or end_at is None or end_at < current:
            return None

        impact_level, impact_text = cls._classify_maintenance_impact(all_text)
        status = "进行中" if start_at <= current <= end_at else "预定"
        url_source = detail if detail else item
        return MaintenanceItem(
            title=title,
            url=cls.official_news_url(url_source),
            status=status,
            impact_level=impact_level,
            impact_text=impact_text,
            start_at=start_at,
            end_at=end_at,
        )

    def format_maintenance_list(self, items: list[MaintenanceItem]) -> str:
        important = [item for item in items if item.impact_level == "important"]
        general = [item for item in items if item.impact_level == "general"]
        low = [item for item in items if item.impact_level == "low"]
        show_all = self.show_low_impact_maintenance()

        if not important and not show_all:
            hidden_count = len(general) + len(low)
            if hidden_count:
                return (
                    "🛠️ 当前没有影响登录的重点维护公告。\n"
                    f"已隐藏 {hidden_count} 条一般/低影响维护公告，"
                    "可在插件配置中开启显示。"
                )
            return "🛠️ 当前没有正在进行中或已预定的重点维护公告。"

        display_items = important + general + low if show_all else important
        if not display_items:
            return "🛠️ 当前没有正在进行中或已预定的维护公告。"

        heading = "🛠️ 当前维护公告" if show_all else "🛠️ 当前重点维护公告"
        message = [heading]
        for index, item in enumerate(display_items, start=1):
            time_text = f"{item.start_at:%Y-%m-%d %H:%M} 至 {item.end_at:%Y-%m-%d %H:%M}"
            message.append(
                f"{index}. [{item.status}] {item.title}\n"
                f"影响: {item.impact_text}\n"
                f"时间: {time_text}\n"
                f"{item.url}",
            )
        return "\n".join(message)

    async def query_maintenance(self) -> str:
        async with create_http_client(self.config, timeout=15.0) as client:
            news_items = await self._fetch_news_list(
                client,
                MAINTENANCE_CATEGORY_CODE,
                30,
            )
            candidates = [
                item
                for item in news_items
                if self._is_maintenance_candidate(item) and isinstance(item.get("Id"), int)
            ]
            semaphore = asyncio.Semaphore(5)

            async def fetch_detail(item: dict[str, Any]):
                async with semaphore:
                    return await self._fetch_news_detail(client, item["Id"])

            details = await asyncio.gather(
                *(fetch_detail(item) for item in candidates),
                return_exceptions=True,
            )

        maintenance_items = []
        for item, detail in zip(candidates, details, strict=True):
            if isinstance(detail, Exception):
                logger.warning(
                    "获取维护公告详情失败: %s (%s)",
                    item.get("Id"),
                    type(detail).__name__,
                )
                continue
            maintenance = self.build_maintenance_item(item, detail)
            if maintenance is not None:
                maintenance_items.append(maintenance)
        maintenance_items.sort(key=lambda entry: entry.start_at)
        return self.format_maintenance_list(maintenance_items)

    @staticmethod
    def _event_category(text: str) -> str | None:
        if any(keyword in text for keyword in ("周边", "商品", "手办", "销售", "商城")):
            return None
        if any(keyword in text for keyword in ("直播", "节目", "直播间")):
            return None
        if any(keyword in text for keyword in ("联动", "合作")):
            return "联动活动"
        if any(keyword in text for keyword in ("庆典", "季节", "守护神", "新年", "圣诞", "节日")):
            return "季节活动"
        if any(keyword in text for keyword in ("活动", "奖励", "登录奖", "兑换")):
            return "奖励型运营活动"
        return None

    @classmethod
    def build_event_item(
        cls,
        item: Mapping[str, Any],
        detail: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> EventItem | None:
        detail = detail or {}
        title = str(detail.get("Title") or item.get("Title") or "未命名活动")
        summary = str(detail.get("Summary") or item.get("Summary") or "")
        content = cls._plain_text_from_html(str(detail.get("Content") or ""))
        all_text = f"{title}\n{summary}\n{content}"
        category = cls._event_category(all_text)
        if category is None:
            return None
        start_at, end_at = cls.parse_maintenance_time(all_text, now)
        uncertain = bool(re.search(r"\?{2,}|待定|未定|另行通知|时间待确认", all_text))
        confirmed = bool(start_at and end_at and not uncertain)
        if confirmed:
            current = cls._normalize_now(now)
            if end_at < current or start_at > current + timedelta(days=30):
                return None
            date_text = f"{start_at:%Y-%m-%d %H:%M} 至 {end_at:%Y-%m-%d %H:%M}"
        else:
            date_text = "时间待确认"
        source = detail if detail else item
        return EventItem(
            title=title,
            url=cls.official_news_url(source),
            category=category,
            date_text=date_text,
            start_at=start_at if confirmed else None,
            end_at=end_at if confirmed else None,
            date_confirmed=confirmed,
        )

    @staticmethod
    def _event_enabled(config: Mapping[str, Any], category: str) -> bool:
        categories = config.get("event_categories")
        if not isinstance(categories, list):
            return True
        return category in {str(value) for value in categories}

    async def _fetch_event_details(
        self,
        client: httpx.AsyncClient,
        items: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        semaphore = asyncio.Semaphore(5)

        async def fetch(item: dict[str, Any]) -> dict[str, Any]:
            news_id = item.get("Id")
            if not isinstance(news_id, int):
                return {}
            async with semaphore:
                try:
                    return await self._fetch_news_detail(client, news_id)
                except Exception as exc:
                    logger.warning("获取活动公告详情失败 %s: %s", news_id, type(exc).__name__)
                    return {}

        return await asyncio.gather(*(fetch(item) for item in items))

    async def get_event_items(self, now: datetime | None = None) -> list[EventItem]:
        async with create_http_client(self.config, timeout=15.0) as client:
            news_items = await self._fetch_news_list(client, NEWS_CATEGORY_CODES, 60)
            candidates = []
            for item in news_items:
                title_text = f"{item.get('Title', '')} {item.get('Summary', '')}"
                if self._event_category(title_text) is not None:
                    candidates.append(item)
            details = await self._fetch_event_details(client, candidates[:20])
        events = []
        for item, detail in zip(candidates[:20], details, strict=True):
            event = self.build_event_item(item, detail, now)
            if event and self._event_enabled(self.config, event.category):
                events.append(event)
        events.sort(key=lambda event: event.start_at or datetime.max.replace(tzinfo=CHINA_TZ))
        return events[:6]

    @staticmethod
    def format_events(events: list[EventItem]) -> str:
        if not events:
            return "🎉 当前及未来30天没有已识别的限时活动。"
        lines = ["🎉 当前及未来30天限时活动"]
        for index, event in enumerate(events[:6], start=1):
            lines.append(
                f"{index}. [{event.category}] {event.title}\n"
                f"时间：{event.date_text}\n{event.url}",
            )
        return "\n".join(lines)

    async def query_events(self, now: datetime | None = None) -> str:
        return self.format_events(await self.get_event_items(now))

    @staticmethod
    def _patch_title(title: str) -> bool:
        return any(keyword in title for keyword in ("版本", "补丁", "更新", "HotFix", "热修复"))

    async def query_patch(self, version: str = "") -> str:
        requested = version.strip()
        async with create_http_client(self.config, timeout=15.0) as client:
            items = await self._fetch_news_list(client, NEWS_CATEGORY_CODES, 40)
        patch_items = [
            item
            for item in items
            if self._patch_title(str(item.get("Title", "")))
            and (not requested or requested.casefold() in str(item.get("Title", "")).casefold())
        ][:8]
        if not patch_items:
            if requested:
                return f"❌ 没有找到国服版本“{requested}”的更新公告。"
            return "📌 暂未获取到国服版本更新公告。"
        lines = [f"📌 国服版本更新（{requested or '最新'}）"]
        for index, item in enumerate(patch_items, start=1):
            title = str(item.get("Title", "未命名版本公告"))
            publish = str(item.get("PublishDate", "")).split(" ")[0].replace("/", "-")
            lines.append(f"{index}. [{publish}] {title}\n{self.official_news_url(item)}")
        if requested:
            lines.append(
                "资料数据源：FFCafe XIVAPI v2；版本详情以以上国服官方公告为准。"
            )
        return "\n".join(lines)
