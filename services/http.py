from collections.abc import Mapping
from typing import Any

import httpx

USER_AGENT = (
    "astrbot_plugin_fflogs/2.1.0 "
    "(FF14 Assistant; +https://github.com/674537331/astrbot_plugin_fflogs)"
)


def config_text(config: Mapping[str, Any], key: str) -> str:
    value = config.get(key, "")
    return value.strip() if isinstance(value, str) else ""


def create_http_client(
    config: Mapping[str, Any],
    timeout: float,
) -> httpx.AsyncClient:
    proxy_url = config_text(config, "proxy_url")
    kwargs: dict[str, Any] = {
        "follow_redirects": True,
        "headers": {"User-Agent": USER_AGENT},
        "timeout": httpx.Timeout(timeout),
        # Use the plugin's explicit proxy_url setting instead of inheriting a
        # process-wide ALL_PROXY value (which may require httpx[socks]).
        "trust_env": False,
    }
    if proxy_url:
        kwargs["proxy"] = proxy_url
    return httpx.AsyncClient(**kwargs)
