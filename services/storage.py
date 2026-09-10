"""Persistent filesystem helpers used by the plugin.

AstrBot plugins must not put mutable data next to their source files.  This
module keeps that rule in one place and remains importable in unit tests where
AstrBot itself is not installed.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

PLUGIN_NAME = "astrbot_plugin_fflogs"


def get_plugin_data_dir(data_dir: str | Path | None = None) -> Path:
    """Return and create the plugin data directory.

    The optional argument is intentionally public so tests can use a temporary
    directory without monkeypatching AstrBot's global path.
    """

    if data_dir is not None:
        path = Path(data_dir)
    else:
        try:
            from astrbot.core.utils.astrbot_path import get_astrbot_data_path

            path = Path(get_astrbot_data_path()) / "plugin_data" / PLUGIN_NAME
        except (ImportError, AttributeError):
            # This fallback is only for standalone tests/development.  At
            # runtime AstrBot's data path is always preferred.
            path = Path("data") / "plugin_data" / PLUGIN_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


class JsonStore:
    """Small atomic JSON store for cache files and generated metadata."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read(self, default: Any) -> Any:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return default

    def write(self, value: Any) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary_name = tempfile.mkstemp(
            prefix=f".{self.path.name}.",
            suffix=".tmp",
            dir=self.path.parent,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_name, self.path)
        finally:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass

