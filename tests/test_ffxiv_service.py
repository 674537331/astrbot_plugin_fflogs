from datetime import datetime

from services.ffxiv import CHINA_TZ, FFXIVService, MaintenanceItem


def test_news_count_is_clamped():
    assert FFXIVService({"news_count": 0}).get_news_count() == 1
    assert FFXIVService({"news_count": 99}).get_news_count() == 20
    assert FFXIVService({"news_count": "bad"}).get_news_count() == 5


def test_parse_maintenance_time_uses_china_timezone():
    now = datetime(2026, 7, 30, 9, 0, tzinfo=CHINA_TZ)

    start_at, end_at = FFXIVService.parse_maintenance_time(
        "维护时间：2026年7月30日 10:00-12:30",
        now,
    )

    assert start_at == datetime(2026, 7, 30, 10, 0, tzinfo=CHINA_TZ)
    assert end_at == datetime(2026, 7, 30, 12, 30, tzinfo=CHINA_TZ)


def test_parse_maintenance_time_handles_overnight_range():
    now = datetime(2026, 7, 30, 9, 0, tzinfo=CHINA_TZ)

    start_at, end_at = FFXIVService.parse_maintenance_time(
        "7月30日 23:00-02:00",
        now,
    )

    assert start_at == datetime(2026, 7, 30, 23, 0, tzinfo=CHINA_TZ)
    assert end_at == datetime(2026, 7, 31, 2, 0, tzinfo=CHINA_TZ)


def test_build_maintenance_item_filters_completed_announcements():
    now = datetime(2026, 7, 30, 9, 0, tzinfo=CHINA_TZ)
    item = {"Id": 1, "Title": "全区全服更新维护完成公告"}

    assert FFXIVService.build_maintenance_item(item, {}, now) is None


def test_build_maintenance_item_classifies_important_maintenance():
    now = datetime(2026, 7, 30, 10, 30, tzinfo=CHINA_TZ)
    item = {"Id": 1, "Title": "全区全服更新维护公告"}
    detail = {
        "Id": 1,
        "Title": "全区全服更新维护公告",
        "Content": "<p>2026年7月30日 10:00-12:00</p>",
    }

    result = FFXIVService.build_maintenance_item(item, detail, now)

    assert result is not None
    assert result.status == "进行中"
    assert result.impact_level == "important"
    assert result.url.endswith("/1")


def test_format_maintenance_list_hides_general_items_by_default():
    service = FFXIVService({"show_low_impact_maintenance": False})
    item = MaintenanceItem(
        title="线路调整维护",
        url="https://example.com",
        status="预定",
        impact_level="low",
        impact_text="低影响",
        start_at=datetime(2026, 8, 1, 1, 0, tzinfo=CHINA_TZ),
        end_at=datetime(2026, 8, 1, 2, 0, tzinfo=CHINA_TZ),
    )

    result = service.format_maintenance_list([item])

    assert "已隐藏 1 条" in result


def test_format_server_status():
    result = FFXIVService.format_server_status(
        [
            {
                "AreaName": "陆行鸟",
                "Group": [
                    {
                        "name": "拉诺西亚",
                        "runing": True,
                        "isint": False,
                        "isout": True,
                        "iscreate": False,
                        "isupgrade": True,
                    },
                ],
            },
        ],
    )

    assert "拉诺西亚: 运行中 / 不可转入 / 可转出 / 不可创建新角色 / 优待状态: 优待" in result
