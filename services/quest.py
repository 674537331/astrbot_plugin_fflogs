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
from .storage import JsonStore, get_plugin_data_dir
from .xivapi import SEARCH_FIELDS, XIVAPIService

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

# XIVAPI exposes the expansion/quest chapter, but not a stable patch-number
# field.  These are reviewed main-scenario endpoints from the CN data and are
# deliberately kept as an explicit manifest.  A missing endpoint is skipped;
# the service never infers a patch from row IDs or level values.
MAIN_SCENARIO_PATCH_TERMINALS = {
    "3": (
        ("5.0", "69190", "暗影之逆焰"),
        ("5.1", "69218", "纯白誓约、漆黑密约"),
        ("5.2", "69306", "追忆的凶星"),
        ("5.3", "69318", "水晶的残光"),
        ("5.4", "69552", "另一个未来"),
        ("5.5", "69602", "死斗至黎明"),
    ),
}

MAIN_SCENARIO_API_QUERY = 'JournalGenre.JournalCategory.Name~"主线任务"'

EXPANSION_NAMES = {
    "0": "2.0 重生之境",
    "1": "3.0 苍穹之禁城",
    "2": "4.0 红莲之狂潮",
    "3": "5.0 暗影之逆焰",
    "4": "6.0 晓月之终途",
    "5": "7.0 金曦之遗辉",
}


class QuestGraphService:
    def __init__(
        self,
        config: dict[str, Any],
        data_dir: str | Path | None = None,
        xivapi: XIVAPIService | None = None,
    ):
        self.config = config
        plugin_data_dir = get_plugin_data_dir(data_dir)
        self.path = plugin_data_dir / "Quest.csv"
        self.api_cache = JsonStore(plugin_data_dir / "mainline_graph.json")
        self.xivapi = xivapi or XIVAPIService(config, data_dir)
        self._records: list[dict[str, str]] | None = None
        self._source = ""

    @staticmethod
    def _api_records(rows: list[Any]) -> list[dict[str, str]]:
        records = []
        for row in rows:
            fields = getattr(row, "fields", {})
            if not isinstance(fields, dict):
                continue
            name = fields.get("Name")
            if not name:
                continue
            expansion = fields.get("Expansion", {})
            expansion_id = ""
            if isinstance(expansion, dict):
                expansion_id = str(expansion.get("row_id", ""))
            journal = fields.get("JournalGenre", {})
            journal_id = ""
            if isinstance(journal, dict):
                journal_id = str(journal.get("row_id", ""))
            previous = fields.get("PreviousQuest@as(raw)", [])
            if not isinstance(previous, (list, tuple)):
                previous = [previous]
            record = {
                "#": str(getattr(row, "row_id", "")),
                "Name": str(name),
                "Expansion": expansion_id,
                "JournalGenre": journal_id,
            }
            for index, value in enumerate(previous[:4]):
                record[f"PreviousQuest[{index}]"] = str(value or "0")
            records.append(record)
        return records

    async def _load_from_xivapi(self) -> list[dict[str, str]]:
        cached = self.api_cache.read({})
        if isinstance(cached, dict) and isinstance(cached.get("records"), list):
            try:
                age = datetime.now().timestamp() - float(cached.get("cached_at", 0))
            except (TypeError, ValueError):
                age = QUEST_CACHE_MAX_AGE.total_seconds() + 1
            if 0 <= age < QUEST_CACHE_MAX_AGE.total_seconds():
                records = [
                    {str(key): str(value) for key, value in record.items()}
                    for record in cached["records"]
                    if isinstance(record, dict)
                ]
                by_id = {record.get("#", ""): record for record in records}
                if any(
                    terminal_id in by_id
                    for _, terminal_id, _ in MAIN_SCENARIO_TERMINALS
                ):
                    return records
        rows = await self.xivapi.search_all_rows(
            MAIN_SCENARIO_API_QUERY,
            sheets=("Quest",),
            fields=SEARCH_FIELDS,
        )
        records = self._api_records(rows)
        by_id = {record.get("#", ""): record for record in records}
        if not any(terminal_id in by_id for _, terminal_id, _ in MAIN_SCENARIO_TERMINALS):
            return []
        self.api_cache.write(
            {
                "cached_at": datetime.now().timestamp(),
                "api_version": self.xivapi.last_api_version,
                "records": records,
            },
        )
        return records

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
        reader = csv.reader(io.StringIO(content))
        header_index = None
        headers: list[str] = []
        for index, row in enumerate(reader):
            if "Name" in row and "PreviousQuest[0]" in row:
                header_index = index
                headers = row
                break
            if index >= 4:
                break
        if header_index is None:
            return []
        indices: dict[str, int] = {}
        for index, header in enumerate(headers):
            if header and header not in indices:
                indices[header] = index
        records = []
        rows_after_header = reader
        for row in rows_after_header:
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
            records = await self._load_from_xivapi()
            if records:
                self._records = records
                self._source = "XIVAPI v2"
                return records
        except Exception as exc:
            logger.warning("加载 XIVAPI v2 主线任务图失败: %s", type(exc).__name__)
        try:
            content = await self._download_if_needed()
            self._records = self._parse_csv(content)
            self._source = "Quest.csv"
        except (OSError, httpx.HTTPError, UnicodeError, csv.Error) as exc:
            logger.warning("加载国服 Quest.csv 失败: %s", type(exc).__name__)
            if self.path.exists():
                self._records = self._parse_csv(
                    self.path.read_text(encoding="utf-8-sig"),
                )
                self._source = "Quest.csv缓存"
            else:
                self._records = []
                self._source = ""
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

    def _patch_terminals(self, expansion: str) -> tuple[tuple[str, str, str], ...]:
        configured = self.config.get("mainline_patch_terminals")
        if isinstance(configured, dict):
            configured_items = configured.get(expansion)
            if isinstance(configured_items, list):
                output = []
                for item in configured_items:
                    if isinstance(item, dict) and item.get("version") and item.get("row_id"):
                        output.append(
                            (str(item["version"]), str(item["row_id"]), str(item.get("title", ""))),
                        )
                if output:
                    return tuple(output)
        return MAIN_SCENARIO_PATCH_TERMINALS.get(expansion, ())

    def _patch_progress(
        self,
        candidate: dict[str, str],
        expansion_chain: list[dict[str, str]],
    ) -> tuple[str, str]:
        expansion = candidate.get("Expansion", "")
        position_by_id = {
            record.get("#", ""): index
            for index, record in enumerate(expansion_chain, start=1)
        }
        candidate_position = position_by_id.get(candidate.get("#", ""))
        if not candidate_position:
            return "", "主线补丁分界资料不完整，未猜测小版本。"
        terminals = []
        for version, row_id, title in self._patch_terminals(expansion):
            terminal_position = position_by_id.get(row_id)
            if terminal_position:
                terminals.append((version, terminal_position, title))
        terminals.sort(key=lambda item: item[1])
        start = 1
        for version, end, _title in terminals:
            if candidate_position <= end:
                patch_position = candidate_position - start + 1
                patch_total = end - start + 1
                percent = patch_position / patch_total * 100
                return (
                    f"小版本进度：{version} 内约第 {patch_position}/{patch_total} 条 "
                    f"（{percent:.1f}%）；约剩 {patch_total - patch_position} 条。",
                    "",
                )
            start = end + 1
        if terminals:
            last_version = terminals[-1][0]
            return (
                f"小版本进度：当前已超过已验证的 {last_version} 终点；"
                "后续补丁分界资料不完整，未猜测百分比。",
                "",
            )
        return "", "小版本进度：当前没有已验证的补丁分界资料，未猜测百分比。"

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
        major_label = ""
        if expansion_name:
            lines.append(f"所属版本：{expansion_name}")
            major_label = f"{expansion_name.split(' ', 1)[0].split('.', 1)[0]}.x"
        if anchored:
            if expansion_name:
                lines.append(
                    f"大版本进度：{major_label} 约第 "
                    f"{expansion_position}/{len(expansion_chain)} 条 "
                    f"（{expansion_position / len(expansion_chain) * 100:.1f}%）；"
                    f"当前版本主线约剩 {len(expansion_chain) - expansion_position} 条。"
                )
                patch_line, patch_note = self._patch_progress(candidate, expansion_chain)
                if patch_line:
                    lines.append(patch_line)
                elif patch_note:
                    lines.append(patch_note)
            lines.append(
                f"主线约第 {position}/{total} 条（{percent:.1f}%）；"
                f"按已知主线终点估算总序约剩 {remaining} 条。"
            )
        else:
            lines.append(
                f"主线约第 {position}/{total} 条（{percent:.1f}%）；"
                "当前数据未找到已知主线终点，仅按前置链估算，未将支线总数计入分母。"
            )
        if expansion_position and expansion_name and not anchored:
            lines.append(
                f"大版本进度：{expansion_name.split(' ', 1)[0]} 内约第 "
                f"{expansion_position}/{len(expansion_chain)} 条 "
                f"（{expansion_position / len(expansion_chain) * 100:.1f}%）。"
            )
        source_note = self._source or "运行时任务图"
        lines.append(f"数据来源：{source_note}；百分比表示任务顺序位置，不表示角色实际完成度。")
        return "\n".join(lines)
