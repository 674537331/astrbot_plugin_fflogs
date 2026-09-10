"""Ocean Fishing departure calculations and lightweight route matching."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .schedule import CHINA_TZ, format_countdown, normalize_now

# Versioned epoch for the schedule introduced with Ocean Fishing.  The exact
# route order can be overridden from the backend config when the CN service
# changes; it is no longer tied to an arbitrary future date.
OCEAN_ROTATION_VERSION = "5.2-cn"
OCEAN_REFERENCE_DATE = datetime(2020, 2, 18, tzinfo=CHINA_TZ)
OCEAN_INTERVAL = timedelta(hours=2)

DEFAULT_ROUTES = (
    ("梅尔托尔海峡：西侧", ("梅尔托尔", "西侧", "海钓")),
    ("梅尔托尔海峡：东侧", ("梅尔托尔", "东侧", "海钓")),
    ("红玉海", ("红玉海", "赤海", "海钓")),
    ("太阳神草原外海", ("太阳神", "萨维奈", "海钓")),
    ("北洋", ("北洋", "冰海", "海钓")),
)

ACHIEVEMENT_FISH_ROUTES = {
    "海之都": ("梅尔托尔", "梅尔托尔海峡"),
    "红玉海的传说": ("红玉海", "赤海"),
    "萨维奈的传说": ("萨维奈", "太阳神草原"),
    "冰海的传说": ("冰海", "北洋"),
}


@dataclass(frozen=True)
class OceanVoyage:
    depart_at: datetime
    route: str
    matched_target: str = ""


class OceanService:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    def routes(self) -> tuple[tuple[str, tuple[str, ...]], ...]:
        configured = self.config.get("ocean_routes")
        if not isinstance(configured, list):
            return DEFAULT_ROUTES
        result = []
        for item in configured:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            keywords = item.get("keywords", [])
            if not isinstance(keywords, list):
                keywords = []
            result.append((str(item["name"]), tuple(str(value) for value in keywords)))
        return tuple(result) or DEFAULT_ROUTES

    def _rotation_config(self) -> dict[str, Any]:
        value = self.config.get("ocean_rotation")
        return value if isinstance(value, dict) else {}

    def rotation_version(self) -> str:
        value = self._rotation_config().get("version")
        return str(value).strip() if value else OCEAN_ROTATION_VERSION

    def reference_date(self) -> datetime:
        value = self._rotation_config().get("reference_date")
        if not isinstance(value, str) or not value.strip():
            return OCEAN_REFERENCE_DATE
        try:
            return normalize_now(datetime.fromisoformat(value.replace("Z", "+00:00")))
        except ValueError:
            return OCEAN_REFERENCE_DATE

    def _next_departure(self, now: datetime) -> datetime:
        elapsed = (now - self.reference_date()).total_seconds()
        intervals = math.floor(elapsed / OCEAN_INTERVAL.total_seconds()) + 1
        return self.reference_date() + intervals * OCEAN_INTERVAL

    def _matching_route(self, query: str) -> tuple[str, str] | None:
        normalized = query.strip().casefold()
        if not normalized:
            return None
        for achievement, keywords in ACHIEVEMENT_FISH_ROUTES.items():
            if normalized in achievement.casefold() or achievement.casefold() in normalized:
                return achievement, " ".join(keywords)
        for route, keywords in self.routes():
            if any(
                normalized in keyword.casefold() or keyword.casefold() in normalized
                for keyword in keywords
            ):
                return query.strip(), " ".join(keywords)
            if normalized in route.casefold() or route.casefold() in normalized:
                return query.strip(), route
        return None

    def future_voyages(
        self,
        query: str = "",
        now: datetime | None = None,
        count: int = 3,
    ) -> list[OceanVoyage]:
        current = normalize_now(now)
        target = self._matching_route(query)
        departures: list[OceanVoyage] = []
        departure = self._next_departure(current)
        sequence = self.routes()
        for _ in range(0, 60):
            route_index = math.floor(
                (departure - self.reference_date()) / OCEAN_INTERVAL,
            ) % len(sequence)
            route, keywords = sequence[route_index]
            if target is None:
                matches = True
            else:
                target_tokens = tuple(token for token in target[1].split() if token)
                matches = any(
                    token.casefold() in f"{route} {' '.join(keywords)}".casefold()
                    for token in target_tokens
                )
            if matches:
                departures.append(
                    OceanVoyage(
                        depart_at=departure,
                        route=route,
                        matched_target=target[0] if target else "",
                    ),
                )
                if len(departures) >= max(count, 1):
                    break
            departure += OCEAN_INTERVAL
        return departures

    def format_voyages(
        self,
        query: str = "",
        now: datetime | None = None,
    ) -> str:
        current = normalize_now(now)
        target = self._matching_route(query) if query.strip() else None
        voyages = self.future_voyages(query, current, 3)
        if query.strip() and target is None:
            return f"❌ 未识别海钓路线或成就鱼“{query.strip()}”，请改用路线名或成就鱼名。"
        if not voyages:
            return "🌊 未来没有匹配的海钓航班。"
        heading = "🌊 未来3班海钓航班"
        if target:
            heading += f"（匹配：{target[0]}）"
        lines = [heading]
        for voyage in voyages:
            lines.append(
                f"{voyage.depart_at:%m-%d %H:%M} {voyage.route}"
                f"（倒计时 {format_countdown(voyage.depart_at - current)}）",
            )
        lines.append(
            f"时间按北京时间计算；轮换数据版本 {self.rotation_version()}，"
            "路线/成就鱼数据可由后台配置覆盖。"
        )
        return "\n".join(lines)
