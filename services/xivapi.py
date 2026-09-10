"""Small, bounded client for FFCafe's XIVAPI v2 service.

The plugin only keeps the short fields needed to answer a query.  It does not
mirror the game data set: search responses and individual rows are cached in
AstrBot's plugin-data directory with a short TTL, and stale entries are used
only when the public endpoint is temporarily unavailable.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from .http import create_http_client
from .storage import JsonStore, get_plugin_data_dir

logger = logging.getLogger(__name__)

XIVAPI_BASE_URL = "https://xivapi-v2.xivcdn.com/api"
XIVAPI_DOC_URL = "https://xivapi-v2.xivcdn.com/zh-cn/docs/welcome/"
XIVAPI_LANGUAGE = "chs"
XIVAPI_CACHE_TTL_SECONDS = 6 * 60 * 60
XIVAPI_CACHE_VERSION = 1
XIVAPI_MAX_LIMIT = 5
XIVAPI_MAX_PAGES = 12

# These sheets have a stable Name field and cover the public /ff14 wiki
# command without requesting the whole data set.  Quest is kept first because
# it is the most common exact match and also supplies the main-scenario graph.
SEARCH_SHEETS = (
    "Quest",
    "Item",
    "Achievement",
    "Mount",
    "Companion",
    "Emote",
    "Orchestrion",
    "TripleTriadCard",
    "FishParameter",
    "FishingSpot",
    "GatheringItem",
    "GatheringPoint",
    "Recipe",
    "GilShop",
    "SpecialShop",
)

SEARCH_FIELDS = (
    "Name,Description,Text,Expansion.Name,JournalGenre.Name,"
    "JournalGenre.JournalCategory.Name,ItemUICategory.Name,LevelItem,LevelEquip,"
    "ClassJobCategory.Name,ClassJobLevel,PreviousQuest@as(raw)"
)


class XIVAPIError(RuntimeError):
    """Base error raised for an invalid or unavailable XIVAPI response."""


class XIVAPIUnavailable(XIVAPIError):
    """The endpoint could not be reached after bounded retries."""


@dataclass(frozen=True)
class XIVAPIRow:
    sheet: str
    row_id: int
    score: float
    fields: dict[str, Any]
    api_version: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> XIVAPIRow | None:
        try:
            row_id = int(value.get("row_id"))
        except (TypeError, ValueError):
            return None
        fields = value.get("fields", {})
        if not isinstance(fields, dict):
            fields = {}
        try:
            score = float(value.get("score", 0))
        except (TypeError, ValueError):
            score = 0.0
        return cls(
            sheet=str(value.get("sheet", "")),
            row_id=row_id,
            score=score,
            fields=fields,
            api_version=str(value.get("api_version", "")),
        )


def _normalise_query(query: str) -> str:
    return re.sub(r"\s+", " ", query.strip()).casefold()


class XIVAPIService:
    """Async XIVAPI v2 client with fresh/stale cache fallback."""

    def __init__(
        self,
        config: dict[str, Any],
        data_dir: str | Path | None = None,
    ):
        self.config = config
        self.cache = JsonStore(get_plugin_data_dir(data_dir) / "xivapi" / "cache.json")
        self._request_semaphore = asyncio.Semaphore(4)
        self.last_error: str | None = None
        self.last_api_version = ""
        self.last_result_from_cache = False
        self.last_cached_at: float | None = None

    @staticmethod
    def _now() -> float:
        return datetime.now(timezone.utc).timestamp()

    @staticmethod
    def _cache_key(prefix: str, value: str) -> str:
        return f"{prefix}:{_normalise_query(value)}"

    def _read_cache(self) -> dict[str, Any]:
        payload = self.cache.read({})
        return payload if isinstance(payload, dict) else {}

    def _read_cached_rows(
        self,
        key: str,
        limit: int,
    ) -> tuple[list[XIVAPIRow], float | None, bool]:
        item = self._read_cache().get(key)
        if not isinstance(item, dict):
            return [], None, False
        cached_at = item.get("cached_at")
        try:
            cached_at_number = float(cached_at)
        except (TypeError, ValueError):
            cached_at_number = None
        rows = []
        for raw in item.get("rows", []):
            if isinstance(raw, dict):
                row = XIVAPIRow.from_dict(raw)
                if row:
                    rows.append(row)
        fresh = (
            cached_at_number is not None
            and self._now() - cached_at_number <= XIVAPI_CACHE_TTL_SECONDS
        )
        return rows[:limit], cached_at_number, fresh

    def _write_cached_rows(
        self,
        key: str,
        rows: list[XIVAPIRow],
        api_version: str,
    ) -> None:
        payload = self._read_cache()
        payload[key] = {
            "version": XIVAPI_CACHE_VERSION,
            "cached_at": self._now(),
            "api_version": api_version,
            "rows": [row.to_dict() for row in rows[:XIVAPI_MAX_LIMIT]],
        }
        self.cache.write(payload)

    async def _request_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        endpoint = f"{XIVAPI_BASE_URL}/{path.lstrip('/')}"
        last_error: Exception | None = None
        async with self._request_semaphore:
            for attempt in range(3):
                try:
                    response = await client.get(endpoint, params=params)
                    if response.status_code in {403, 408, 425, 429, 500, 502, 503, 504}:
                        if attempt >= 2:
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
                        raise XIVAPIError("XIVAPI 返回格式不是对象")
                    return payload
                except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPError, OSError) as exc:
                    last_error = exc
                    if attempt >= 2:
                        break
                    await asyncio.sleep(0.15 * (attempt + 1))
                except (ValueError, XIVAPIError) as exc:
                    last_error = exc
                    break
        raise XIVAPIUnavailable(str(last_error or "XIVAPI 请求失败")) from last_error

    async def _search_page(
        self,
        query: str | None,
        sheets: str,
        fields: str,
        limit: int,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "sheets": sheets,
            "fields": fields,
            "language": XIVAPI_LANGUAGE,
            "limit": min(max(limit, 1), 500),
        }
        if cursor:
            params = {
                "cursor": cursor,
                "language": XIVAPI_LANGUAGE,
                "limit": params["limit"],
                "fields": fields,
            }
        elif query:
            params["query"] = query
        async with create_http_client(self.config, timeout=20.0) as client:
            return await self._request_json(client, "search", params)

    @staticmethod
    def _rows_from_payload(payload: dict[str, Any]) -> list[XIVAPIRow]:
        result = []
        api_version = str(payload.get("version", ""))
        for raw in payload.get("results", []):
            if not isinstance(raw, dict):
                continue
            row = XIVAPIRow.from_dict({**raw, "api_version": api_version})
            if row and row.sheet:
                result.append(row)
        return result

    async def search_rows(
        self,
        query: str,
        limit: int = 5,
        sheets: tuple[str, ...] = SEARCH_SHEETS,
        fields: str = SEARCH_FIELDS,
    ) -> list[XIVAPIRow]:
        """Search exact names first, then a bounded fuzzy name search."""

        query = re.sub(r"\s+", " ", query.strip())
        limit = min(max(int(limit), 1), XIVAPI_MAX_LIMIT)
        if not query:
            self.last_error = None
            return []
        cache_key = self._cache_key(
            "search",
            f"{','.join(sheets)}|{fields}|{query}",
        )
        self.last_error = None
        self.last_result_from_cache = False
        self.last_cached_at = None
        try:
            exact_payload = await self._search_page(
                f'Name="{query.replace(chr(34), chr(92) + chr(34))}"',
                ",".join(sheets),
                fields,
                limit,
            )
            rows = self._rows_from_payload(exact_payload)
            if not rows:
                fuzzy_payload = await self._search_page(
                    f'Name~"{query.replace(chr(34), chr(92) + chr(34))}"',
                    ",".join(sheets),
                    fields,
                    limit,
                )
                rows = self._rows_from_payload(fuzzy_payload)
                api_version = str(fuzzy_payload.get("version", ""))
            else:
                api_version = str(exact_payload.get("version", ""))
            self.last_api_version = api_version
            self._write_cached_rows(cache_key, rows, api_version)
            return rows[:limit]
        except XIVAPIError as exc:
            stale, cached_at, _fresh = self._read_cached_rows(cache_key, limit)
            self.last_error = self._error_text(exc, cached_at)
            if stale:
                self.last_result_from_cache = True
                self.last_cached_at = cached_at
                return stale
            return []

    async def search_all_rows(
        self,
        query: str,
        sheets: tuple[str, ...] = ("Quest",),
        fields: str = SEARCH_FIELDS,
        max_pages: int = XIVAPI_MAX_PAGES,
    ) -> list[XIVAPIRow]:
        """Read a bounded cursor-paginated search, used for the quest graph."""

        rows: list[XIVAPIRow] = []
        cursor: str | None = None
        self.last_error = None
        self.last_result_from_cache = False
        self.last_cached_at = None
        try:
            page_limit = min(max(int(max_pages), 1), XIVAPI_MAX_PAGES)
            for _ in range(page_limit):
                payload = await self._search_page(
                    query if cursor is None else None,
                    ",".join(sheets),
                    fields,
                    500,
                    cursor,
                )
                rows.extend(self._rows_from_payload(payload))
                self.last_api_version = str(payload.get("version", ""))
                cursor = str(payload.get("next", "")) or None
                if not cursor:
                    break
            return rows
        except XIVAPIError as exc:
            self.last_error = self._error_text(exc, None)
            return []

    async def get_row(
        self,
        sheet: str,
        row_id: int,
        fields: str = SEARCH_FIELDS,
    ) -> XIVAPIRow | None:
        """Fetch one detailed row; return no fabricated data on failure."""

        key = self._cache_key("row", f"{sheet}:{row_id}:{fields}")
        self.last_error = None
        self.last_result_from_cache = False
        self.last_cached_at = None
        try:
            async with create_http_client(self.config, timeout=20.0) as client:
                payload = await self._request_json(
                    client,
                    f"sheet/{quote(sheet, safe='')}/{int(row_id)}",
                    {"language": XIVAPI_LANGUAGE, "fields": fields},
                )
            api_version = str(payload.get("version", ""))
            row = XIVAPIRow.from_dict(
                {
                    "sheet": sheet,
                    "row_id": payload.get("row_id", row_id),
                    "score": 1.0,
                    "fields": payload.get("fields", {}),
                    "api_version": api_version,
                },
            )
            if row:
                self._write_cached_rows(key, [row], api_version)
            return row
        except XIVAPIError as exc:
            cached, cached_at, _fresh = self._read_cached_rows(key, 1)
            self.last_error = self._error_text(exc, cached_at)
            if cached:
                self.last_result_from_cache = True
                self.last_cached_at = cached_at
            return cached[0] if cached else None

    @staticmethod
    def row_url(row: XIVAPIRow) -> str:
        return (
            f"{XIVAPI_BASE_URL}/sheet/{quote(row.sheet, safe='')}/{row.row_id}"
            f"?language={XIVAPI_LANGUAGE}"
        )

    @staticmethod
    def _error_text(error: Exception, cached_at: float | None) -> str:
        if cached_at:
            cached = datetime.fromtimestamp(cached_at, timezone.utc).astimezone().isoformat(
                timespec="seconds",
            )
            return f"XIVAPI v2 暂时不可用，已返回缓存（缓存于 {cached}）：{type(error).__name__}"
        return f"XIVAPI v2 暂时不可用：{type(error).__name__}"
