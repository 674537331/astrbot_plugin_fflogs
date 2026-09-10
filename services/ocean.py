"""Ocean Fishing departure calculations and lightweight route matching."""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from .schedule import CHINA_TZ, format_countdown, normalize_now

OCEAN_REFERENCE_DATE = datetime(2026, 1, 1, tzinfo=CHINA_TZ)
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

    @staticmethod
    def _next_departure(now: datetime) -> datetime:
        elapsed = (now - OCEAN_REFERENCE_DATE).total_seconds()
        intervals = math.floor(elapsed / OCEAN_INTERVAL.total_seconds()) + 1
        return OCEAN_REFERENCE_DATE + intervals * OCEAN_INTERVAL

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
                (departure - OCEAN_REFERENCE_DATE) / OCEAN_INTERVAL,
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
        lines.append("时间按北京时间计算；路线/成就鱼数据可由配置覆盖。")
        return "\n".join(lines)
