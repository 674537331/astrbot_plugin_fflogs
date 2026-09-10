"""Reminder event generation and duplicate-safe scheduling primitives."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .config import reminder_targets, reminder_types
from .fashion import FashionReport
from .ffxiv import EventItem
from .ocean import OceanService
from .pvp import PvpService
from .schedule import (
    future_et_windows,
    next_daily_reset,
    next_weekday_at,
    next_weekly_reset,
    normalize_now,
)

DEFAULT_LEADS = {
    "daily_refresh": 30,
    "weekly_refresh": 30,
    "ocean": 15,
    "gather": 10,
    "fish": 10,
    "event_start": 24 * 60,
    "event_end": 24 * 60,
    "pvp_weekly": 0,
    "fashion": 0,
}


@dataclass(frozen=True)
class ReminderEvent:
    event_id: str
    reminder_type: str
    title: str
    trigger_at: datetime
    target_at: datetime
    message: str
    image_url: str = ""


def _lead(target: dict[str, Any], reminder_type: str) -> int:
    value = target.get("lead_minutes", DEFAULT_LEADS.get(reminder_type, 10))
    try:
        return min(max(int(value), 0), 7 * 24 * 60)
    except (TypeError, ValueError):
        return DEFAULT_LEADS.get(reminder_type, 10)


def build_reminder_events(
    config: dict[str, Any],
    pvp: PvpService,
    ocean: OceanService,
    events: list[EventItem] | None = None,
    fashion: FashionReport | None = None,
    now: datetime | None = None,
) -> list[ReminderEvent]:
    current = normalize_now(now)
    enabled = reminder_types(config)
    output: list[ReminderEvent] = []

    if "daily_refresh" in enabled:
        target = next_daily_reset(current)
        lead = DEFAULT_LEADS["daily_refresh"]
        output.append(
            ReminderEvent(
                event_id=f"daily-refresh:{target.date().isoformat()}",
                reminder_type="daily_refresh",
                title="日常刷新提醒",
                trigger_at=target - timedelta(minutes=lead),
                target_at=target,
                message=f"日常内容将在 {target:%m-%d %H:%M} 刷新。",
            ),
        )

    if "weekly_refresh" in enabled:
        target = next_weekly_reset(current)
        lead = DEFAULT_LEADS["weekly_refresh"]
        output.append(
            ReminderEvent(
                event_id=f"weekly-refresh:{target.date().isoformat()}",
                reminder_type="weekly_refresh",
                title="周常刷新提醒",
                trigger_at=target - timedelta(minutes=lead),
                target_at=target,
                message=f"周常内容将在 {target:%m-%d %H:%M} 刷新。",
            ),
        )

    if "pvp_weekly" in enabled:
        target = next_weekday_at(current, 0, 9)
        current_frontline = pvp.current_frontline(target)
        output.append(
            ReminderEvent(
                event_id=f"pvp-weekly:{target.date().isoformat()}",
                reminder_type="pvp_weekly",
                title="PvP周历提醒",
                trigger_at=target,
                target_at=target,
                message=f"本周PvP轮换已更新，当前纷争前线为：{current_frontline.name}。",
                image_url=current_frontline.image_url,
            ),
        )

    if "fashion" in enabled and fashion and fashion.confirmed:
        output.append(
            ReminderEvent(
                event_id=f"fashion:{fashion.week}",
                reminder_type="fashion",
                title="时尚评鉴已确认",
                trigger_at=current,
                target_at=current,
                message=f"本周主题：{fashion.theme}。已确认80分方案，请查看 /ff14 fashion。",
                image_url=fashion.image_url,
            ),
        )

    for target in reminder_targets(config):
        target_type = str(
            target.get("type") or target.get("__template_key") or "",
        ).strip()
        if target_type not in enabled:
            continue
        name = str(target.get("name", "未命名目标"))
        if not name.strip():
            continue
        if not target.get("enabled", True):
            continue
        if target_type == "ocean":
            for voyage in ocean.future_voyages(name, current, 3):
                lead = _lead(target, target_type)
                output.append(
                    ReminderEvent(
                        event_id=f"ocean:{name}:{voyage.depart_at.isoformat()}",
                        reminder_type=target_type,
                        title=f"海钓：{name}",
                        trigger_at=voyage.depart_at - timedelta(minutes=lead),
                        target_at=voyage.depart_at,
                        message=f"海钓航班将在 {voyage.depart_at:%m-%d %H:%M} 出发：{voyage.route}。",
                    ),
                )
                break
        elif target_type in {"gather", "fish"}:
            et_range = str(target.get("et_window") or target.get("window") or "")
            zone = str(target.get("zone", "未知区域"))
            for start_at, end_at, et_text in future_et_windows(et_range, current, 3):
                lead = _lead(target, target_type)
                suffix = "采集" if target_type == "gather" else "天气鱼"
                output.append(
                    ReminderEvent(
                        event_id=f"{target_type}:{name}:{start_at.isoformat()}",
                        reminder_type=target_type,
                        title=f"{suffix}：{name}",
                        trigger_at=start_at - timedelta(minutes=lead),
                        target_at=start_at,
                        message=f"{zone}的{name}将在 {et_text}（北京时间 {start_at:%m-%d %H:%M}）进入窗口。",
                    ),
                )
                break

    for event in events or []:
        if not event.date_confirmed or event.start_at is None or event.end_at is None:
            continue
        for reminder_type, target_at in (
            ("event_start", event.start_at),
            ("event_end", event.end_at),
        ):
            if reminder_type not in enabled:
                continue
            lead = _lead({}, reminder_type)
            output.append(
                ReminderEvent(
                    event_id=f"{reminder_type}:{event.url}:{target_at.isoformat()}",
                    reminder_type=reminder_type,
                    title=event.title,
                    trigger_at=target_at - timedelta(minutes=lead),
                    target_at=target_at,
                    message=f"活动“{event.title}”将在 {target_at:%m-%d %H:%M}"
                    f"{'开始' if reminder_type == 'event_start' else '结束'}。",
                ),
            )
    return output


def due_events(
    events: list[ReminderEvent],
    now: datetime | None = None,
    tolerance: timedelta = timedelta(minutes=1),
) -> list[ReminderEvent]:
    current = normalize_now(now)
    return [
        event
        for event in events
        if event.trigger_at <= current < event.trigger_at + tolerance
    ]
