"""Versioned PvP rotation calculations.

The reference points and map order are kept as constants so a future in-game
rotation change can be reviewed and updated in one place. Administrators can
override the lists, intervals and reference points through plugin configuration
without changing code.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import math
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .http import create_http_client
from .schedule import CHINA_TZ, format_countdown, normalize_now

logger = logging.getLogger(__name__)

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
        self._image_semaphore = asyncio.Semaphore(4)

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

    @staticmethod
    def _image_cache_path(data_dir: str | Path, map_id: str) -> Path:
        safe_map_id = re.sub(r"[^A-Za-z0-9_.-]", "_", map_id).strip(".") or "map"
        return Path(data_dir) / "pvp_maps" / f"{safe_map_id}.webp"

    @staticmethod
    def _is_image(content: bytes, content_type: str) -> bool:
        if content_type.startswith("image/"):
            return True
        return content.startswith((b"RIFF", b"\x89PNG", b"\xff\xd8"))

    @staticmethod
    def _as_data_url(content: bytes, content_type: str) -> str:
        if content.startswith(b"\x89PNG"):
            mime_type = "image/png"
        elif content.startswith(b"\xff\xd8"):
            mime_type = "image/jpeg"
        elif content.startswith(b"RIFF"):
            mime_type = "image/webp"
        else:
            mime_type = content_type if content_type.startswith("image/") else "image/webp"
        encoded = base64.b64encode(content).decode("ascii")
        return f"data:{mime_type};base64,{encoded}"

    @staticmethod
    def _write_image(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_bytes(content)
        temporary.replace(path)

    async def local_image_data(self, map_id: str, data_dir: str | Path) -> str:
        """Return a cached map image as a data URL for HTML rendering.

        QQ clients and AstrBot's HTML renderer may not be able to fetch the
        upstream GitHub image URL.  The image is therefore downloaded once to
        the plugin data directory and embedded into the generated PNG.  A
        failed download is deliberately represented by an empty string so the
        template can show a text placeholder instead of a broken image icon.
        """

        cache_path = await self.local_image_path(map_id, data_dir)
        if not cache_path:
            return ""
        try:
            content = await asyncio.to_thread(Path(cache_path).read_bytes)
        except OSError:
            return ""
        return self._as_data_url(content, "image/webp") if content else ""

    async def local_image_path(self, map_id: str, data_dir: str | Path) -> str:
        """Download once and return an absolute local path for AstrBot.

        ``Image.fromFileSystem`` is more reliable for QQ/OneBot than a remote
        URL.  The response is size-limited and validated before it is cached.
        """

        cache_path = self._image_cache_path(data_dir, map_id)
        try:
            content = await asyncio.to_thread(cache_path.read_bytes)
        except OSError:
            content = b""
        if content and self._is_image(content, ""):
            return str(cache_path.resolve())

        async with self._image_semaphore:
            try:
                content = await asyncio.to_thread(cache_path.read_bytes)
            except OSError:
                content = b""
            if content and self._is_image(content, ""):
                return str(cache_path.resolve())
            try:
                async with create_http_client(self.config, timeout=10.0) as client:
                    response = await client.get(self.image_url(map_id))
                    response.raise_for_status()
                    content = response.content
                    if len(content) > 8 * 1024 * 1024:
                        raise ValueError("image response is too large")
                    content_type = response.headers.get("content-type", "")
                    content_type = content_type.split(";", 1)[0].strip().lower()
                    if not content or not self._is_image(content, content_type):
                        raise ValueError("upstream response is not an image")
                await asyncio.to_thread(self._write_image, cache_path, content)
                return str(cache_path.resolve())
            except Exception:
                logger.warning("PvP 地图图片缓存失败：%s", map_id, exc_info=True)
                return ""

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
