"""Local FFXIV clock and refresh/window calculations."""

from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import as_bool

CHINA_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
ET_SECONDS_PER_DAY = 24 * 60 * 60
ET_SPEED = 144 / 7  # one Eorzean day is 70 real minutes
ET_WINDOW_HOURS = 8


def normalize_now(now: datetime | None = None) -> datetime:
    if now is None:
        return datetime.now(CHINA_TZ)
    if now.tzinfo is None:
        return now.replace(tzinfo=CHINA_TZ)
    return now.astimezone(CHINA_TZ)


def format_countdown(delta: timedelta) -> str:
    seconds = max(int(delta.total_seconds()), 0)
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days}天")
    if hours or days:
        parts.append(f"{hours}小时")
    if minutes or hours or days:
        parts.append(f"{minutes}分")
    parts.append(f"{seconds}秒")
    return "".join(parts)


def next_daily_reset(now: datetime | None = None, hour: int = 23) -> datetime:
    current = normalize_now(now)
    target = current.replace(hour=hour, minute=0, second=0, microsecond=0)
    if target <= current:
        target += timedelta(days=1)
    return target


def next_weekly_reset(now: datetime | None = None) -> datetime:
    """Return the next Tuesday 16:00 China-time weekly reset."""

    current = normalize_now(now)
    target = current.replace(hour=16, minute=0, second=0, microsecond=0)
    days_until_tuesday = (1 - current.weekday()) % 7
    target += timedelta(days=days_until_tuesday)
    if target <= current:
        target += timedelta(days=7)
    return target


def next_weekday_at(
    now: datetime | None,
    weekday: int,
    hour: int,
    minute: int = 0,
) -> datetime:
    current = normalize_now(now)
    target = current.replace(hour=hour, minute=minute, second=0, microsecond=0)
    target += timedelta(days=(weekday - current.weekday()) % 7)
    if target <= current:
        target += timedelta(days=7)
    return target


def eorzea_datetime(now: datetime | None = None) -> datetime:
    """Return the accelerated Eorzea timestamp for a real-world instant."""

    current = normalize_now(now)
    return datetime.fromtimestamp(current.timestamp() * ET_SPEED, tz=CHINA_TZ)


def eorzea_clock(now: datetime | None = None) -> str:
    return eorzea_datetime(now).strftime("%H:%M:%S")


def _et_absolute_seconds(now: datetime) -> float:
    return now.timestamp() * ET_SPEED


def real_time_for_et_clock(
    et_hour: int,
    et_minute: int = 0,
    now: datetime | None = None,
) -> datetime:
    current = normalize_now(now)
    current_et = _et_absolute_seconds(current)
    current_day = math.floor(current_et / ET_SECONDS_PER_DAY)
    target = current_day * ET_SECONDS_PER_DAY + et_hour * 3600 + et_minute * 60
    if target <= current_et:
        target += ET_SECONDS_PER_DAY
    return datetime.fromtimestamp(target / ET_SPEED, tz=CHINA_TZ)


def parse_et_window(value: str) -> tuple[int, int] | None:
    match = re.search(
        r"(\d{1,2})(?::(\d{2}))?\s*(?:-|~|—|至|到)\s*"
        r"(\d{1,2})(?::(\d{2}))?",
        value,
    )
    if not match:
        return None
    start_hour, start_minute, end_hour, end_minute = match.groups()
    start = int(start_hour) * 60 + int(start_minute or 0)
    end = int(end_hour) * 60 + int(end_minute or 0)
    if start >= 1440 or end >= 1440:
        return None
    return start, end


def future_et_windows(
    et_range: str,
    now: datetime | None = None,
    count: int = 3,
) -> list[tuple[datetime, datetime, str]]:
    parsed = parse_et_window(et_range)
    if not parsed:
        return []
    start_minutes, end_minutes = parsed
    current = normalize_now(now)
    current_et = _et_absolute_seconds(current)
    current_day = math.floor(current_et / ET_SECONDS_PER_DAY)
    output: list[tuple[datetime, datetime, str]] = []
    for offset in range(0, 4):
        start_absolute = (
            (current_day + offset) * ET_SECONDS_PER_DAY + start_minutes * 60
        )
        if start_absolute <= current_et:
            continue
        end_absolute = (current_day + offset) * ET_SECONDS_PER_DAY + end_minutes * 60
        if end_minutes <= start_minutes:
            end_absolute += ET_SECONDS_PER_DAY
        start_real = datetime.fromtimestamp(start_absolute / ET_SPEED, tz=CHINA_TZ)
        end_real = datetime.fromtimestamp(end_absolute / ET_SPEED, tz=CHINA_TZ)
        output.append((start_real, end_real, f"ET {start_minutes // 60:02d}:{start_minutes % 60:02d}-{end_minutes // 60:02d}:{end_minutes % 60:02d}"))
        if len(output) >= count:
            break
    return output


DEFAULT_WEATHER_PROFILES: dict[str, tuple[str, ...]] = {
    "拉诺西亚": ("碧空", "阴云", "小雨", "薄雾"),
    "黑衣森林": ("碧空", "阴云", "薄雾", "暴雨"),
    "萨纳兰": ("碧空", "晴朗", "热浪", "沙尘暴"),
    "库尔札斯": ("碧空", "阴云", "暴雪", "薄雾"),
    "龙堡": ("碧空", "阴云", "暴雨", "雷雨"),
    "基拉巴尼亚": ("碧空", "阴云", "雷雨", "扬沙"),
    "诺弗兰特": ("碧空", "阴云", "暴雨", "雷雨"),
    "萨雷安": ("碧空", "阴云", "小雨", "薄雾"),
    "塔拉": ("碧空", "阴云", "雷雨", "小雨"),
}


def weather_at_et_window(
    zone: str,
    et_absolute_seconds: float,
    weather_profiles: dict[str, Any] | None = None,
) -> str:
    profiles = weather_profiles or DEFAULT_WEATHER_PROFILES
    profile = None
    for name, values in profiles.items():
        if str(name) in zone:
            profile = values
            break
    if not isinstance(profile, (list, tuple)) or not profile:
        profile = DEFAULT_WEATHER_PROFILES["拉诺西亚"]
    # This is a deterministic local fallback.  The production data set can
    # override each zone's weather profile through configuration; no network
    # request is needed for the window calculation itself.
    weather_slot = int(et_absolute_seconds // (ET_WINDOW_HOURS * 3600))
    return str(profile[weather_slot % len(profile)])


def future_weather_windows(
    zone: str,
    now: datetime | None = None,
    weather_profiles: dict[str, Any] | None = None,
    count: int = 3,
) -> list[dict[str, Any]]:
    current = normalize_now(now)
    current_et = _et_absolute_seconds(current)
    slot_seconds = ET_WINDOW_HOURS * 3600
    current_slot = math.floor(current_et / slot_seconds)
    output = []
    for offset in range(0, 8):
        slot = current_slot + offset
        start_et = slot * slot_seconds
        if start_et <= current_et:
            continue
        end_et = start_et + slot_seconds
        start_real = datetime.fromtimestamp(start_et / ET_SPEED, tz=CHINA_TZ)
        end_real = datetime.fromtimestamp(end_et / ET_SPEED, tz=CHINA_TZ)
        output.append(
            {
                "weather": weather_at_et_window(zone, start_et, weather_profiles),
                "et": f"ET {int(start_et // 3600) % 24:02d}:00-{int(end_et // 3600) % 24:02d}:00",
                "start_at": start_real,
                "end_at": end_real,
            },
        )
        if len(output) >= count:
            break
    return output


def parse_enabled(value: Any) -> bool:
    return as_bool(value, False)

