"""Weekly Fashion Report reader.

The report is community-confirmed rather than exposed by the official game
API.  The parser therefore returns an explicit "待确认" state whenever the
source does not contain a theme or an 80-point recommendation.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from .http import create_http_client
from .schedule import CHINA_TZ
from .storage import JsonStore, get_plugin_data_dir

logger = logging.getLogger(__name__)

FASHION_REPORT_URL = "https://fashionreportxiv.com/"


@dataclass(frozen=True)
class FashionReport:
    week: str
    theme: str
    easy_80: str
    source_url: str
    image_url: str = ""
    confirmed: bool = False
    cached_at: str | None = None


class FashionService:
    def __init__(self, config: dict[str, Any], data_dir: str | Path | None = None):
        self.config = config
        self.cache = JsonStore(get_plugin_data_dir(data_dir) / "fashion.json")

    @staticmethod
    def parse_html(content: str, source_url: str = FASHION_REPORT_URL) -> FashionReport:
        soup = BeautifulSoup(content, "html.parser")
        text = re.sub(r"\s+", " ", soup.get_text(" ", strip=True))
        week_match = re.search(r"\bWeek\s+(\d+)\b", text, flags=re.IGNORECASE)
        week = week_match.group(1) if week_match else "未知"

        theme = ""
        for heading in soup.find_all(["h1", "h2", "h3"]):
            heading_text = re.sub(r"\s+", " ", heading.get_text(" ", strip=True))
            heading_match = re.search(r"(.+?)\s*\|\s*Week\s+\d+", heading_text, re.I)
            if heading_match:
                theme = heading_match.group(1).strip()
                break
        if not theme:
            theme_match = re.search(
                r"(?:Current Fashion Report Theme|本周主题|主题)\s*[:：]?\s*([^|]{2,80})",
                text,
                flags=re.IGNORECASE,
            )
            if theme_match:
                theme = theme_match.group(1).strip()

        easy_80 = ""
        marker = re.search(r"Easy\s+80|80\s*点|80\s*分", text, flags=re.IGNORECASE)
        if marker:
            easy_80 = text[marker.start() : marker.start() + 420].strip()
        image_url = ""
        og_image = soup.find("meta", attrs={"property": "og:image"})
        if og_image and og_image.get("content"):
            image_url = urljoin(source_url, str(og_image["content"]))
        if not image_url:
            image = soup.find("img")
            if image and image.get("src"):
                image_url = urljoin(source_url, str(image["src"]))
        confirmed = bool(theme and easy_80)
        return FashionReport(
            week=week,
            theme=theme or "主题待确认",
            easy_80=easy_80 or "80分方案待确认",
            source_url=source_url,
            image_url=image_url,
            confirmed=confirmed,
        )

    def _read_cache(self) -> FashionReport | None:
        payload = self.cache.read({})
        if not isinstance(payload, dict) or not payload.get("theme"):
            return None
        try:
            return FashionReport(**payload)
        except TypeError:
            return None

    def _write_cache(self, report: FashionReport) -> None:
        payload = asdict(report)
        payload["cached_at"] = datetime.now(CHINA_TZ).isoformat(timespec="seconds")
        self.cache.write(payload)

    async def query(self) -> FashionReport:
        try:
            async with create_http_client(self.config, timeout=15.0) as client:
                response = await client.get(FASHION_REPORT_URL)
                response.raise_for_status()
                report = self.parse_html(response.text)
            if report.confirmed:
                self._write_cache(report)
                return report
            cached = self._read_cache()
            return cached or report
        except (httpx.HTTPError, OSError, ValueError) as exc:
            logger.warning("时尚评鉴数据获取失败: %s", type(exc).__name__)
            cached = self._read_cache()
            if cached:
                return FashionReport(**{**asdict(cached), "cached_at": "本地缓存"})
            return FashionReport(
                week="未知",
                theme="主题待确认",
                easy_80="80分方案待确认；数据源暂时不可用。",
                source_url=FASHION_REPORT_URL,
            )

    @staticmethod
    def format_report(report: FashionReport) -> str:
        lines = [
            f"👗 本周时尚评鉴（第 {report.week} 周）",
            f"主题：{report.theme}",
            report.easy_80,
        ]
        if report.confirmed and report.image_url:
            lines.append(f"参考图：{report.image_url}")
        if report.cached_at:
            lines.append(f"（{report.cached_at}）")
        lines.append(f"原文：{report.source_url}")
        if not report.confirmed:
            lines.append("当前资料未确认，未据此创建提醒。")
        return "\n".join(lines)
