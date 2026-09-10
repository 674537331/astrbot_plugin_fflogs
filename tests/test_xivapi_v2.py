import asyncio
from datetime import datetime, timedelta

from services.quest import QuestGraphService
from services.reminders import build_reminder_events, due_events
from services.schedule import CHINA_TZ, weather_at_et_window
from services.wiki import WikiService
from services.xivapi import XIVAPIRow, XIVAPIService, XIVAPIUnavailable


class FakeXIVAPI:
    last_error = None
    last_api_version = "test-version"

    def __init__(self, rows):
        self.rows = rows

    async def search_rows(self, query, limit=5):
        return self.rows[:limit]

    async def search_all_rows(self, query, sheets=("Quest",), fields="", max_pages=12):
        return self.rows


def test_xivapi_row_is_rendered_directly_without_a_second_candidate_step(tmp_path):
    row = XIVAPIRow(
        sheet="Item",
        row_id=123,
        score=1.0,
        fields={"Name": "水晶塔", "ItemUICategory": {"fields": {"Name": "素材"}}},
        api_version="test-version",
    )

    async def scenario():
        service = WikiService({}, tmp_path, FakeXIVAPI([row]))
        results = await service.search_ff14_wiki("水晶塔")
        assert len(results) == 1
        assert results[0].title == "水晶塔"
        assert results[0].page_type == "材料"
        assert "XIVAPI v2" in service.format_results("水晶塔", results)

    asyncio.run(scenario())


def test_xivapi_cursor_search_is_bounded_and_uses_stale_cache(tmp_path):
    async def scenario():
        service = XIVAPIService({}, tmp_path)
        calls = []

        async def fake_page(query, sheets, fields, limit, cursor=None):
            calls.append((query, sheets, fields, limit, cursor))
            if cursor is None:
                return {
                    "version": "v1",
                    "next": "cursor-1",
                    "results": [
                        {
                            "sheet": "Item",
                            "row_id": 1,
                            "score": 1,
                            "fields": {"Name": "第一项"},
                        },
                    ],
                }
            return {
                "version": "v1",
                "results": [
                    {
                        "sheet": "Item",
                        "row_id": 2,
                        "score": 1,
                        "fields": {"Name": "第二项"},
                    },
                ],
            }

        service._search_page = fake_page
        rows = await service.search_all_rows('Name~"项"', max_pages=2)
        assert [row.row_id for row in rows] == [1, 2]
        assert calls[0][0] == 'Name~"项"'
        assert calls[1][0] is None
        assert calls[1][-1] == "cursor-1"

        async def initial_page(query, sheets, fields, limit, cursor=None):
            return {
                "version": "v2",
                "results": [rows[0].to_dict()],
            }

        service._search_page = initial_page
        initial_rows = await service.search_rows("缓存测试")
        assert [row.row_id for row in initial_rows] == [1]

        async def unavailable_page(query, sheets, fields, limit, cursor=None):
            raise XIVAPIUnavailable("offline")

        service._search_page = unavailable_page
        cached_rows = await service.search_rows("缓存测试")
        assert [row.row_id for row in cached_rows] == [1]
        assert service.last_result_from_cache is True
        assert service.last_cached_at is not None

    asyncio.run(scenario())


def test_mainline_progress_has_expansion_and_patch_percentages(tmp_path):
    def make_row(row_id, name, previous, expansion="3"):
        return XIVAPIRow(
            sheet="Quest",
            row_id=row_id,
            score=1.0,
            fields={
                "Name": name,
                "Expansion": {"row_id": int(expansion)},
                "PreviousQuest@as(raw)": [previous, 0, 0],
            },
        )

    rows = [
        make_row(69179, "前一任务", 0),
        make_row(69180, "舞台上最悲惨的演员", 69179),
        make_row(69190, "暗影之逆焰", 69180),
        make_row(69218, "纯白誓约、漆黑密约", 69190),
        make_row(69602, "死斗至黎明", 69218),
        make_row(70970, "雾中奇境", 69602, "5"),
    ]

    async def scenario():
        service = QuestGraphService({}, tmp_path, FakeXIVAPI(rows))
        progress = await service.progress_for("舞台上最悲惨的演员")
        assert "大版本进度：5.x 约第 2/5 条" in progress
        assert "小版本进度：5.0 内约第 2/3 条" in progress
        assert "14.2%" not in progress
        assert "不表示角色实际完成度" in progress

    asyncio.run(scenario())


def test_weather_without_a_reviewed_rate_table_is_unknown_and_not_fabricated():
    assert weather_at_et_window("拉诺西亚", 12345) is None


def test_reminder_due_window_uses_persistent_last_check():
    from services.reminders import ReminderEvent

    trigger = datetime(2026, 9, 7, 9, 0, tzinfo=CHINA_TZ)
    event = ReminderEvent("pvp:1", "pvp_weekly", "PvP", trigger, trigger, "")
    assert due_events([event], trigger) == [event]
    assert due_events([event], trigger + timedelta(minutes=2), trigger) == []


def test_fish_reminder_is_not_created_without_weather_condition():
    from services.ocean import OceanService
    from services.pvp import PvpService

    now = datetime(2026, 9, 9, 22, 30, tzinfo=CHINA_TZ)
    events = build_reminder_events(
        {"reminder_types": ["fish"], "reminder_targets": [{"type": "fish", "name": "鱼", "et_window": "02:00-04:00"}]},
        PvpService({}),
        OceanService({}),
        now=now,
    )
    assert events == []
