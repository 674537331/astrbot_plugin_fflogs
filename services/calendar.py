"""Calendar data assembly and the compact HTML template used by AstrBot."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .pvp import PvpService
from .schedule import (
    format_countdown,
    next_daily_reset,
    next_weekly_reset,
    normalize_now,
)

DEFAULT_CALENDAR_SECTIONS = ("daily", "weekly", "pvp", "events")
DEFAULT_DAILY_ITEMS = (
    "随机任务",
    "友好部族",
    "每日通缉",
    "纷争前线日随",
    "大国防联军筹备",
)
DEFAULT_WEEKLY_ITEMS = (
    "神典石",
    "天书",
    "周限制副本/零式",
    "幻巧",
    "老主顾",
    "B怪周常",
)

CALENDAR_TEMPLATE = """
<style>
body{margin:0;background:#101827;color:#e7edf7;font-family:Arial,"Noto Sans SC",sans-serif}
.card{width:920px;padding:28px 32px;box-sizing:border-box;background:linear-gradient(135deg,#17243a,#0f1725);border-radius:22px}
h1{margin:0 0 6px;font-size:30px}.meta{color:#9eb0ca;font-size:14px;margin-bottom:18px}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:14px}.section{padding:16px;background:#1c2b43;border:1px solid #314766;border-radius:15px}
h2{font-size:19px;margin:0 0 8px;color:#ffd479}.section p{margin:5px 0;font-size:15px;line-height:1.45}.muted{color:#aab8ca}
</style>
<div class="card">
  <h1>FF14 综合日历</h1>
  <div class="meta">北京时间 {{ generated_at }} · 本地计算刷新与轮换时间</div>
  <div class="grid">
  {% for section in sections %}
    <div class="section">
      <h2>{{ section.title }}</h2>
      {% for line in section.lines %}<p>{{ line }}</p>{% endfor %}
    </div>
  {% endfor %}
  </div>
</div>
"""


def _configured_or_default(config: dict[str, Any], key: str, defaults: tuple[str, ...]) -> list[str]:
    if key not in config:
        return list(defaults)
    value = config.get(key)
    return [str(item) for item in value] if isinstance(value, list) else []


def build_calendar_data(
    config: dict[str, Any],
    pvp: PvpService,
    events: list[dict[str, Any]] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    current = normalize_now(now)
    chosen = _configured_or_default(config, "calendar_sections", DEFAULT_CALENDAR_SECTIONS)
    sections = []
    if "daily" in chosen:
        daily_items = _configured_or_default(config, "daily_items", DEFAULT_DAILY_ITEMS)
        reset = next_daily_reset(current)
        sections.append(
            {
                "key": "daily",
                "title": "日常清单",
                "lines": [
                    *[f"□ {item}" for item in daily_items],
                    f"23:00刷新倒计时：{format_countdown(reset - current)}",
                ],
            },
        )
    if "weekly" in chosen:
        weekly_items = _configured_or_default(config, "weekly_items", DEFAULT_WEEKLY_ITEMS)
        reset = next_weekly_reset(current)
        weekly_lines = [
            *[f"□ {item}" for item in weekly_items],
            f"周二16:00刷新倒计时：{format_countdown(reset - current)}",
        ]
        if "大国防联军筹备" in _configured_or_default(
            config,
            "daily_items",
            DEFAULT_DAILY_ITEMS,
        ):
            weekly_lines.append("大国防联军筹备：04:00刷新（每日）")
        sections.append(
            {
                "key": "weekly",
                "title": "周常清单",
                "lines": weekly_lines,
            },
        )
    if "pvp" in chosen:
        current_frontline = pvp.current_frontline(current)
        lines = [f"当前纷争前线：{current_frontline.name}"]
        lines.extend(
            f"{entry.start_at:%m-%d} {entry.name}" for entry in pvp.weekly_frontline(current)
        )
        sections.append({"key": "pvp", "title": "PvP轮换", "lines": lines})
    if "events" in chosen:
        event_lines = []
        for event in (events or [])[:6]:
            name = str(event.get("title", "未命名活动"))
            date_text = str(event.get("date_text", "时间待确认"))
            event_lines.append(f"{name}：{date_text}")
        sections.append(
            {
                "key": "events",
                "title": "活动",
                "lines": event_lines or ["当前没有已确认日期的活动。"],
            },
        )
    return {
        "generated_at": current.strftime("%Y-%m-%d %H:%M"),
        "sections": sections,
    }


def format_calendar_text(data: dict[str, Any]) -> str:
    lines = ["🗓️ FF14 综合日历", f"北京时间 {data.get('generated_at', '')}"]
    for section in data.get("sections", []):
        lines.append(f"\n【{section.get('title', '')}】")
        lines.extend(str(line) for line in section.get("lines", []))
    return "\n".join(lines)
