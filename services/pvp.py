"""Versioned PvP rotation calculations.

The reference points and map order are kept as constants so a future in-game
rotation change can be reviewed and updated in one place. Administrators can
override the lists, intervals and reference points through plugin configuration
without changing code.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .schedule import CHINA_TZ, format_countdown, normalize_now

PVP_ROTATION_VERSION = "2026.01"
FRONTLINE_REFERENCE_DATE = datetime(2025, 7, 16, 23, 0, tzinfo=CHINA_TZ)
CC_REFERENCE_DATE = datetime(2026, 1, 9, 23, 0, tzinfo=CHINA_TZ)
FRONTLINE_INTERVAL = timedelta(days=1)
CC_INTERVAL = timedelta(minutes=90)

FRONTLINE_MAPS = (
    ("secure", "周边遗迹群（制压战）"),
    ("seize", "尘封秘岩（争夺战）"),
    ("shatter", "荣誉野（碎冰战）"),
    ("naadam", "昂萨尔·哈卡尔（阵地战）"),
)
CC_MAPS = (
    ("palaistra", "狼狱停船场"),
    ("volcanic", "火山高原"),
    ("castletown", "城塞遗迹群"),
    ("bayside", "东岸"),
    ("cloudnine", "云海"),
    ("redsands", "红沙小径"),
)
MAP_IMAGE_BASE_URL = "https://raw.githubusercontent.com/ffxiv-wakeng/pvp-calendar/master/public/maps"


@dataclass(frozen=True)
class RotationEntry:
    map_id: str
    name: str
    start_at: datetime
    end_at: datetime
    image_url: str


class PvpService:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def _rotation_overrides(self) -> dict[str, Any]:
        value = self.config.get("pvp_rotation")
        return value if isinstance(value, dict) else {}

    def rotation_version(self) -> str:
        value = self._rotation_overrides().get("version")
        return str(value).strip() if value else PVP_ROTATION_VERSION

    def _configured_datetime(self, key: str, default: datetime) -> datetime:
        value = self._rotation_overrides().get(key)
        if not isinstance(value, str) or not value.strip():
            return default
        try:
            parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return default
        return normalize_now(parsed)

    def _configured_interval(self, key: str, default: timedelta) -> timedelta:
        value = self._rotation_overrides().get(key)
        try:
            number = float(value)
        except (TypeError, ValueError):
            return default
        if number <= 0:
            return default
        return timedelta(minutes=number)

    @staticmethod
    def _configured_maps(config: dict[str, Any], key: str, default: tuple[tuple[str, str], ...]):
        overrides = config.get("pvp_rotation")
        if not isinstance(overrides, dict):
            return default
        values = overrides.get(key)
        if not isinstance(values, list):
            return default
        result = []
        for value in values:
            if isinstance(value, dict) and value.get("id") and value.get("name"):
                result.append((str(value["id"]), str(value["name"])))
        return tuple(result) or default

    def frontline_maps(self) -> tuple[tuple[str, str], ...]:
        return self._configured_maps(self.config, "frontline_maps", FRONTLINE_MAPS)

    def cc_maps(self) -> tuple[tuple[str, str], ...]:
        return self._configured_maps(self.config, "cc_maps", CC_MAPS)

    def frontline_reference(self) -> datetime:
        return self._configured_datetime("frontline_reference", FRONTLINE_REFERENCE_DATE)

    def cc_reference(self) -> datetime:
        return self._configured_datetime("cc_reference", CC_REFERENCE_DATE)

    def frontline_interval(self) -> timedelta:
        return self._configured_interval(
            "frontline_interval_minutes",
            FRONTLINE_INTERVAL,
        )

    def cc_interval(self) -> timedelta:
        return self._configured_interval(
            "cc_interval_minutes",
            CC_INTERVAL,
        )

    @staticmethod
    def image_url(map_id: str) -> str:
        return f"{MAP_IMAGE_BASE_URL}/{map_id}.webp"

    @classmethod
    def _rotation_index(
        cls,
        now: datetime,
        reference: datetime,
        interval: timedelta,
        length: int,
    ) -> tuple[int, datetime, datetime]:
        elapsed = (now - reference).total_seconds()
        interval_seconds = interval.total_seconds()
        index = math.floor(elapsed / interval_seconds) % length
        start = reference + timedelta(seconds=math.floor(elapsed / interval_seconds) * interval_seconds)
        return index, start, start + interval

    def current_frontline(self, now: datetime | None = None) -> RotationEntry:
        current = normalize_now(now)
        maps = self.frontline_maps()
        index, start, end = self._rotation_index(
            current,
            self.frontline_reference(),
            self.frontline_interval(),
            len(maps),
        )
        map_id, name = maps[index]
        return RotationEntry(map_id, name, start, end, self.image_url(map_id))

    def current_cc(self, now: datetime | None = None) -> RotationEntry:
        current = normalize_now(now)
        maps = self.cc_maps()
        index, start, end = self._rotation_index(
            current,
            self.cc_reference(),
            self.cc_interval(),
            len(maps),
        )
        map_id, name = maps[index]
        return RotationEntry(map_id, name, start, end, self.image_url(map_id))

    def weekly_frontline(self, now: datetime | None = None) -> list[RotationEntry]:
        current = normalize_now(now)
        monday = current.date() - timedelta(days=current.weekday())
        week_start = datetime.combine(monday, datetime.min.time(), tzinfo=CHINA_TZ).replace(
            hour=23,
        )
        entries = []
        maps = self.frontline_maps()
        reference = self.frontline_reference()
        interval = self.frontline_interval()
        for index in range(7):
            start = week_start + timedelta(days=index)
            map_id, name = maps[
                self._rotation_index(start + timedelta(seconds=1), reference, interval, len(maps))[0]
            ]
            entries.append(
                RotationEntry(
                    map_id,
                    name,
                    start,
                    start + timedelta(days=1),
                    self.image_url(map_id),
                ),
            )
        return entries

    def upcoming_cc(self, now: datetime | None = None, count: int = 4) -> list[RotationEntry]:
        current = normalize_now(now)
        maps = self.cc_maps()
        interval = self.cc_interval()
        index, start, _ = self._rotation_index(
            current,
            self.cc_reference(),
            interval,
            len(maps),
        )
        return [
            RotationEntry(
                *maps[(index + offset) % len(maps)],
                start + offset * interval,
                start + (offset + 1) * interval,
                self.image_url(maps[(index + offset) % len(maps)][0]),
            )
            for offset in range(max(count, 1))
        ]

    def format_status(self, now: datetime | None = None) -> str:
        current = normalize_now(now)
        frontline = self.current_frontline(current)
        cc = self.current_cc(current)
        lines = [
            f"⚔️ PvP 轮换（算法 {self.rotation_version()}）",
            f"纷争前线：{frontline.name}",
            f"距离 23:00 换图：{format_countdown(frontline.end_at - current)}",
            "本周每日纷争前线：",
        ]
        for entry in self.weekly_frontline(current):
            lines.append(f"  {entry.start_at:%m-%d} {entry.name}")
        lines.append(f"当前水晶冲突：{cc.name}（至 {cc.end_at:%H:%M}）")
        lines.append("后续水晶冲突：")
        for entry in self.upcoming_cc(current, 4)[1:]:
            lines.append(f"  {entry.start_at:%m-%d %H:%M} {entry.name}")
        return "\n".join(lines)
