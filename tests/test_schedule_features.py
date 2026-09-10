from datetime import datetime, timedelta

from services.calendar import build_calendar_data, format_calendar_text
from services.ffxiv import EventItem
from services.ocean import OceanService
from services.pvp import PvpService
from services.reminders import build_reminder_events, due_events
from services.schedule import (
    CHINA_TZ,
    future_et_windows,
    next_daily_reset,
    next_weekly_reset,
)


def test_refresh_boundaries_are_china_time():
    now = datetime(2026, 9, 9, 22, 30, tzinfo=CHINA_TZ)

    assert next_daily_reset(now) == datetime(2026, 9, 9, 23, 0, tzinfo=CHINA_TZ)
    assert next_weekly_reset(now) == datetime(2026, 9, 15, 16, 0, tzinfo=CHINA_TZ)


def test_et_window_returns_three_future_windows():
    now = datetime(2026, 9, 9, 22, 30, tzinfo=CHINA_TZ)
    windows = future_et_windows("02:00-04:00", now, 3)

    assert len(windows) == 3
    assert all(start > now for start, _, _ in windows)
    assert all(end > start for start, end, _ in windows)
    assert all(label == "ET 02:00-04:00" for _, _, label in windows)


def test_pvp_rotation_has_current_map_and_weekly_calendar():
    service = PvpService({})
    now = datetime(2026, 9, 9, 22, 30, tzinfo=CHINA_TZ)

    current = service.current_frontline(now)
    cc = service.current_cc(now)
    weekly = service.weekly_frontline(now)

    assert current.end_at - current.start_at == timedelta(days=1)
    assert cc.end_at - cc.start_at == timedelta(minutes=90)
    assert len(weekly) == 7
    assert service.upcoming_cc(now, 4)[0].start_at <= now


def test_pvp_rotation_reference_and_intervals_are_configurable():
    service = PvpService(
        {
            "pvp_rotation": {
                "version": "test-rotation",
                "frontline_reference": "2026-01-01T23:00:00+08:00",
                "frontline_interval_minutes": 720,
                "cc_interval_minutes": 60,
            },
        },
    )
    now = datetime(2026, 1, 2, 12, 0, tzinfo=CHINA_TZ)

    assert service.rotation_version() == "test-rotation"
    assert service.current_frontline(now).end_at - service.current_frontline(now).start_at == timedelta(hours=12)
    assert service.current_cc(now).end_at - service.current_cc(now).start_at == timedelta(hours=1)


def test_ocean_route_and_achievement_fish_both_return_three_voyages():
    service = OceanService({})
    now = datetime(2026, 9, 9, 22, 30, tzinfo=CHINA_TZ)

    route_voyages = service.future_voyages("红玉海", now)
    fish_voyages = service.future_voyages("红玉海的传说", now)

    assert len(route_voyages) == 3
    assert len(fish_voyages) == 3
    assert all(voyage.route == "红玉海" for voyage in fish_voyages)


def test_calendar_strictly_uses_selected_sections_and_items():
    data = build_calendar_data(
        {
            "calendar_sections": ["daily"],
            "daily_items": ["随机任务"],
        },
        PvpService({}),
        now=datetime(2026, 9, 9, 22, 30, tzinfo=CHINA_TZ),
    )
    text = format_calendar_text(data)

    assert [section["key"] for section in data["sections"]] == ["daily"]
    assert "随机任务" in text
    assert "周常清单" not in text
    assert "大国防联军筹备" not in text


def test_reminder_types_default_to_no_events_and_due_event_is_deterministic():
    now = datetime(2026, 9, 9, 22, 30, tzinfo=CHINA_TZ)
    pvp = PvpService({})
    ocean = OceanService({})

    assert build_reminder_events({}, pvp, ocean, now=now) == []
    events = build_reminder_events(
        {"reminder_types": ["daily_refresh"]},
        pvp,
        ocean,
        now=now,
    )
    assert len(due_events(events, now)) == 1
    assert due_events(events, now)[0].event_id == "daily-refresh:2026-09-09"


def test_unconfirmed_event_never_enters_event_reminder_queue():
    now = datetime(2026, 9, 9, 22, 30, tzinfo=CHINA_TZ)
    event = EventItem(
        title="时间待定活动",
        url="https://example.com/event",
        category="季节活动",
        date_text="时间待确认",
        start_at=None,
        end_at=None,
        date_confirmed=False,
    )

    reminders = build_reminder_events(
        {"reminder_types": ["event_start", "event_end"]},
        PvpService({}),
        OceanService({}),
        events=[event],
        now=now,
    )

    assert reminders == []
