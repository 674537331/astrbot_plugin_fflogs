"""Lazy main-scenario quest graph loader.

The Chinese client data is downloaded at runtime and cached below AstrBot's
plugin data directory.  The repository therefore stays small and the plugin
does not redistribute the large CSV.
"""

from __future__ import annotations

import csv
import io
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from .http import create_http_client
from .storage import get_plugin_data_dir

logger = logging.getLogger(__name__)

QUEST_CSV_URL = "https://raw.githubusercontent.com/thewakingsands/ffxiv-datamining-cn/master/Quest.csv"
QUEST_CACHE_MAX_AGE = timedelta(days=7)


class QuestGraphService:
    def __init__(self, config: dict[str, Any], data_dir: str | Path | None = None):
        self.config = config
        self.path = get_plugin_data_dir(data_dir) / "Quest.csv"
        self._records: list[dict[str, str]] | None = None

    async def _download_if_needed(self) -> str:
        if self.path.exists():
            modified = datetime.fromtimestamp(self.path.stat().st_mtime)
            if datetime.now() - modified < QUEST_CACHE_MAX_AGE:
                return self.path.read_text(encoding="utf-8-sig")
        async with create_http_client(self.config, timeout=30.0) as client:
            response = await client.get(QUEST_CSV_URL)
            response.raise_for_status()
            content = response.content
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(content)
        return content.decode("utf-8-sig")

    @staticmethod
    def _parse_csv(content: str) -> list[dict[str, str]]:
        rows = list(csv.reader(io.StringIO(content)))
        if not rows:
            return []
        header_index = next(
            (
                index
                for index, row in enumerate(rows[:5])
                if "Name" in row and "PreviousQuest[0]" in row
            ),
            None,
        )
        if header_index is None:
            return []
        headers = rows[header_index]
        indices: dict[str, int] = {}
        for index, header in enumerate(headers):
            if header and header not in indices:
                indices[header] = index
        records = []
        for row in rows[header_index + 1 :]:
            # The datamining CSV has a type row immediately after its header.
            if row and row[0] in {"#", "int32", "str"}:
                continue
            if len(row) <= indices.get("Name", 1):
                continue
            record = {
                name: row[index].strip() if index < len(row) else ""
                for name, index in indices.items()
            }
            if record.get("Name"):
                records.append(record)
        return records

    async def _load(self) -> list[dict[str, str]]:
        if self._records is not None:
            return self._records
        try:
            content = await self._download_if_needed()
            self._records = self._parse_csv(content)
        except (OSError, httpx.HTTPError, UnicodeError, csv.Error) as exc:
            logger.warning("加载国服 Quest.csv 失败: %s", type(exc).__name__)
            if self.path.exists():
                self._records = self._parse_csv(
                    self.path.read_text(encoding="utf-8-sig"),
                )
            else:
                self._records = []
        return self._records

    @staticmethod
    def _is_main_scenario(record: dict[str, str]) -> bool:
        # The CN datamining table uses Type=0 for the main quest journal
        # entries.  If a test fixture omits Type, keep non-empty rows usable.
        quest_type = record.get("Type")
        return quest_type in {None, "", "0"}

    async def progress_for(self, query: str) -> str:
        records = await self._load()
        main_records = [record for record in records if self._is_main_scenario(record)]
        query = query.strip().casefold()
        candidates = [
            record
            for record in main_records
            if record.get("Name", "").casefold() == query
        ]
        if not candidates:
            candidates = [
                record
                for record in main_records
                if query and query in record.get("Name", "").casefold()
            ]
        if not candidates:
            return ""

        by_id = {
            record.get("#", ""): record
            for record in main_records
            if record.get("#")
        }
        current = candidates[0]
        chain: set[str] = set()
        position = 0
        while current and current.get("#") not in chain:
            current_id = current.get("#", "")
            chain.add(current_id)
            position += 1
            previous = next(
                (
                    current.get(f"PreviousQuest[{index}]")
                    for index in range(4)
                    if current.get(f"PreviousQuest[{index}]") not in {None, "", "0"}
                ),
                "",
            )
            current = by_id.get(previous)
        total = max(len(main_records), position)
        percent = position / total * 100 if total else 0
        return f"主线约第 {position}/{total} 条（{percent:.1f}%）；此百分比不表示角色实际完成度。"

