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

# Quest.csv's Type=0 is not a main-scenario flag.  It also contains side
# quests, role quests and other normal quests.  The main-scenario graph is
# therefore anchored at known official MSQ endpoints instead of treating the
# whole Type=0 table as one sequence.  Keep this list newest-first and add a
# new endpoint when the CN data source publishes a new main-scenario patch;
# guessing from the largest quest ID would re-introduce side quests.
MAIN_SCENARIO_TERMINALS = (
    ("7.4", "70970", "雾中奇境"),
    ("7.3", "70909", "明日的路标"),
)

EXPANSION_NAMES = {
    "0": "2.0 重生之境",
    "1": "3.0 苍穹之禁城",
    "2": "4.0 红莲之狂潮",
    "3": "5.0 暗影之逆焰",
    "4": "6.0 晓月之终途",
    "5": "7.0 金曦之遗辉",
}


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
    def _is_quest_record(record: dict[str, str]) -> bool:
        # Type=0 means a normal quest record, not necessarily a main-scenario
        # quest.  If a fixture omits Type, keep non-empty rows usable.
        quest_type = record.get("Type")
        return quest_type in {None, "", "0"}

    @staticmethod
    def _previous_quest_id(record: dict[str, str]) -> str:
        return next(
            (
                record.get(f"PreviousQuest[{index}]")
                for index in range(4)
                if record.get(f"PreviousQuest[{index}]") not in {None, "", "0"}
            ),
            "",
        )

    @classmethod
    def _walk_back(
        cls,
        start: dict[str, str],
        by_id: dict[str, dict[str, str]],
    ) -> list[dict[str, str]]:
        """Return one quest's dependency chain in root-to-leaf order."""

        reverse_chain: list[dict[str, str]] = []
        seen: set[str] = set()
        current = start
        while current and current.get("#") not in seen:
            current_id = current.get("#", "")
            seen.add(current_id)
            reverse_chain.append(current)
            current = by_id.get(cls._previous_quest_id(current))
        reverse_chain.reverse()
        return reverse_chain

    @classmethod
    def _known_mainline_chain(
        cls,
        by_id: dict[str, dict[str, str]],
    ) -> list[dict[str, str]]:
        for _version, terminal_id, _title in MAIN_SCENARIO_TERMINALS:
            terminal = by_id.get(terminal_id)
            if terminal:
                return cls._walk_back(terminal, by_id)
        return []

    @staticmethod
    def _find_quest(
        query: str,
        records: list[dict[str, str]],
    ) -> dict[str, str] | None:
        exact = [
            record
            for record in records
            if record.get("Name", "").casefold() == query
        ]
        if exact:
            return exact[0]
        return next(
            (
                record
                for record in records
                if query and query in record.get("Name", "").casefold()
            ),
            None,
        )

    async def progress_for(self, query: str) -> str:
        records = await self._load()
        quest_records = [record for record in records if self._is_quest_record(record)]
        by_id = {
            record.get("#", ""): record
            for record in quest_records
            if record.get("#")
        }
        query = query.strip().casefold()
        chain = self._known_mainline_chain(by_id)
        anchored = bool(chain)
        if not chain:
            # Older cached snapshots and small unit fixtures may not contain
            # a known endpoint.  Keep the useful predecessor-chain fallback,
            # but never use the size of the Type=0 table as a denominator.
            candidate = self._find_quest(query, quest_records)
            if not candidate:
                return ""
            chain = self._walk_back(candidate, by_id)
        candidate = self._find_quest(query, chain)
        if not candidate:
            # A normal side quest can be present in Quest.csv but absent from
            # the anchored MSQ chain.  It must not be labelled as main story.
            return ""

        position = next(
            index for index, record in enumerate(chain, start=1)
            if record.get("#") == candidate.get("#")
        )
        total = len(chain)
        percent = position / total * 100 if total else 0
        expansion = candidate.get("Expansion", "")
        expansion_name = EXPANSION_NAMES.get(expansion)
        expansion_chain = [
            record for record in chain if record.get("Expansion", "") == expansion
        ]
        expansion_position = next(
            (
                index
                for index, record in enumerate(expansion_chain, start=1)
                if record.get("#") == candidate.get("#")
            ),
        )
        remaining = max(total - position, 0)
        lines = []
        if expansion_name:
            lines.append(f"所属版本：{expansion_name}")
        if anchored:
            lines.append(
                f"主线约第 {position}/{total} 条（{percent:.1f}%）；"
                f"按当前 Quest.csv 已知主线终点估算还剩约 {remaining} 条。"
            )
        else:
            lines.append(
                f"主线约第 {position}/{total} 条（{percent:.1f}%）；"
                "当前数据未找到已知主线终点，仅按前置链估算，未将支线总数计入分母。"
            )
        if expansion_position and expansion_name:
            expansion_total = len(expansion_chain)
            expansion_percent = expansion_position / expansion_total * 100
            lines.append(
                f"{expansion_name.split(' ', 1)[0]} 内约第 "
                f"{expansion_position}/{expansion_total} 条（{expansion_percent:.1f}%）。"
            )
        lines.append("以上百分比表示任务顺序位置，不表示角色实际完成度。")
        return "\n".join(lines)
