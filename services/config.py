"""Configuration accessors with v1 compatibility."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

FEATURE_NAMES = (
    "logs",
    "price",
    "status",
    "news",
    "maint",
    "wiki",
    "patch",
    "ocean",
    "fashion",
    "pvp",
    "events",
    "calendar",
    "wiki_llm_tool",
)


def as_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on", "是", "开启"}:
            return True
        if normalized in {"0", "false", "no", "off", "否", "关闭"}:
            return False
    if value is None:
        return default
    return bool(value)


def nested(config: Mapping[str, Any], key: str, default: Any = None) -> Any:
    value = config.get(key, default)
    return value


def feature_enabled(config: Mapping[str, Any], feature: str) -> bool:
    """Read a feature switch, defaulting to enabled for query features.

    Older configurations have no ``feature_switches`` object, so all existing
    query commands remain enabled after upgrading.
    """

    switches = config.get("feature_switches")
    if isinstance(switches, Mapping) and feature in switches:
        return as_bool(switches[feature], True)
    return True


def config_list(config: Mapping[str, Any], key: str) -> list[Any]:
    value = config.get(key, [])
    return list(value) if isinstance(value, list) else []


def selected_values(config: Mapping[str, Any], key: str, defaults: tuple[str, ...]) -> list[str]:
    values = config_list(config, key)
    if not values:
        return list(defaults)
    return [str(value) for value in values]


def reminder_types(config: Mapping[str, Any]) -> set[str]:
    values = config_list(config, "reminder_types")
    return {str(value) for value in values}


def reminder_targets(config: Mapping[str, Any]) -> list[dict[str, Any]]:
    targets = config_list(config, "reminder_targets")
    return [target for target in targets if isinstance(target, dict)]


def feature_switches() -> dict[str, bool]:
    return dict.fromkeys(FEATURE_NAMES, True)

