import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from .http import config_text, create_http_client

FFLOGS_TOKEN_URL = "https://cn.fflogs.com/oauth/token"
FFLOGS_API_URL = "https://cn.fflogs.com/api/v2/client"
SAVAGE_DIFFICULTY_ID = 101

JOB_MAP = {
    "Paladin": "骑士",
    "Warrior": "战士",
    "DarkKnight": "暗骑",
    "Gunbreaker": "绝枪",
    "WhiteMage": "白魔",
    "Scholar": "学者",
    "Astrologian": "占星",
    "Sage": "贤者",
    "Monk": "武僧",
    "Dragoon": "龙骑",
    "Ninja": "忍者",
    "Samurai": "武士",
    "Reaper": "钐镰",
    "Viper": "蛇镰",
    "Bard": "诗人",
    "Machinist": "机工",
    "Dancer": "舞者",
    "BlackMage": "黑魔",
    "Summoner": "召唤",
    "RedMage": "赤魔",
    "Pictomancer": "画家",
}

SAVAGE_BOSS_MAP = {
    105: "M12S",
    104: "M12S-门",
    103: "M11S",
    102: "M10S",
    101: "M9S",
    100: "M8S",
    99: "M7S",
    98: "M6S",
    97: "M5S",
    96: "M4S",
    95: "M3S",
    94: "M2S",
    93: "M1S",
    92: "P12S",
    91: "P11S",
    90: "P10S",
    89: "P9S",
    87: "P8S",
    86: "P7S",
    85: "P6S",
    84: "P5S",
    82: "P4S",
    81: "P3S",
    80: "P2S",
    79: "P1S",
}

ULTIMATE_BOSS_MAP = {
    1060: "绝巴哈",
    1061: "绝神兵",
    1062: "绝亚",
    1065: "绝龙诗",
    1068: "绝欧",
    1073: "绝巴哈",
    1074: "绝神兵",
    1075: "绝亚",
    1076: "绝龙诗",
    1077: "绝欧",
    1079: "绝伊甸",
    1085: "绝妖星乱舞",
}

SAVAGE_ZONE_RANKINGS = (
    ("s73", 73, SAVAGE_DIFFICULTY_ID),
    ("s68", 68, SAVAGE_DIFFICULTY_ID),
    ("s63", 63, SAVAGE_DIFFICULTY_ID),
    ("s54", 54, SAVAGE_DIFFICULTY_ID),
    ("s49", 49, SAVAGE_DIFFICULTY_ID),
    ("s44", 44, SAVAGE_DIFFICULTY_ID),
)
ULTIMATE_ZONE_RANKINGS = (
    ("u_dmu", 76, None),
    ("u_fru", 65, None),
    ("u_7x_legacy", 59, None),
    ("u_5x", 53, None),
    ("u_4x", 45, None),
    ("u_3x", 43, None),
)
FFLOGS_ZONE_RANKINGS = SAVAGE_ZONE_RANKINGS + ULTIMATE_ZONE_RANKINGS
SAVAGE_ZONE_ALIASES = {alias for alias, _, _ in SAVAGE_ZONE_RANKINGS}

ULTIMATE_DISPLAY_ORDER = [
    "绝妖星乱舞",
    "绝伊甸",
    "绝欧",
    "绝龙诗",
    "绝亚",
    "绝神兵",
    "绝巴哈",
]
SAVAGE_70_DISPLAY_ORDER = [
    "M12S",
    "M12S-门",
    "M11S",
    "M10S",
    "M9S",
    "M8S",
    "M7S",
    "M6S",
    "M5S",
    "M4S",
    "M3S",
    "M2S",
    "M1S",
]
SAVAGE_60_DISPLAY_ORDER = [
    "P12S",
    "P11S",
    "P10S",
    "P9S",
    "P8S",
    "P7S",
    "P6S",
    "P5S",
    "P4S",
    "P3S",
    "P2S",
    "P1S",
]


class FFLogsConfigurationError(ValueError):
    """FFLogs credentials are missing from plugin configuration."""


class FFLogsAPIError(RuntimeError):
    """FFLogs returned an invalid or unsuccessful response."""


@dataclass(frozen=True)
class RankingResult:
    percent: float
    job: str


class FFLogsService:
    def __init__(self, config: Mapping[str, Any]):
        self.config = config
        self.token: str | None = None
        self.token_expiry = 0.0

    @staticmethod
    def build_zone_rankings_query() -> str:
        lines = []
        for alias, zone_id, difficulty_id in FFLOGS_ZONE_RANKINGS:
            args = f"zoneID: {zone_id}"
            if difficulty_id is not None:
                args += f", difficulty: {difficulty_id}"
            lines.append(f"                  {alias}: zoneRankings({args})")
        return "\n".join(lines)

    @staticmethod
    def get_difficulty_id(*payloads: object) -> int | None:
        for payload in payloads:
            if not isinstance(payload, dict):
                continue
            difficulty = payload.get("difficulty")
            if isinstance(difficulty, dict):
                difficulty = difficulty.get("id") or difficulty.get("value")
            for value in (
                difficulty,
                payload.get("difficultyID"),
                payload.get("difficultyId"),
            ):
                if value is None:
                    continue
                try:
                    return int(value)
                except (TypeError, ValueError):
                    continue
        return None

    @classmethod
    def get_ranking_display_name(
        cls,
        alias: str,
        zone: dict[str, Any],
        ranking: dict[str, Any],
    ) -> str | None:
        encounter = ranking.get("encounter")
        if not isinstance(encounter, dict):
            return None
        encounter_id = encounter.get("id")

        if encounter_id in SAVAGE_BOSS_MAP:
            difficulty_id = cls.get_difficulty_id(ranking, zone)
            if alias not in SAVAGE_ZONE_ALIASES or difficulty_id != SAVAGE_DIFFICULTY_ID:
                return None
            return SAVAGE_BOSS_MAP[encounter_id]

        return ULTIMATE_BOSS_MAP.get(encounter_id)

    async def _get_token(self) -> None:
        client_id = config_text(self.config, "client_id")
        client_secret = config_text(self.config, "client_secret")
        if not client_id or not client_secret:
            raise FFLogsConfigurationError(
                "请先在插件设置中填写 FFLogs Client ID 和 Client Secret。",
            )

        async with create_http_client(self.config, timeout=10.0) as client:
            response = await client.post(
                FFLOGS_TOKEN_URL,
                data={"grant_type": "client_credentials"},
                auth=(client_id, client_secret),
            )
            response.raise_for_status()
            data = response.json()

        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise FFLogsAPIError("FFLogs token response did not contain a token")

        try:
            expires_in = float(data.get("expires_in", 86400))
        except (TypeError, ValueError):
            expires_in = 86400.0
        self.token = token
        self.token_expiry = time.time() + max(expires_in - 60, 0)

    def _build_query(self) -> str:
        rankings = self.build_zone_rankings_query()
        return f"""
        query ($name: String, $server: String, $region: String) {{
          characterData {{
            character(name: $name, serverSlug: $server, serverRegion: $region) {{
{rankings}
            }}
          }}
        }}
        """

    async def _fetch_character(self, character_name: str, server_name: str):
        variables = {
            "name": character_name,
            "server": server_name,
            "region": "CN",
        }
        for attempt in range(2):
            if not self.token or time.time() >= self.token_expiry:
                await self._get_token()

            async with create_http_client(self.config, timeout=25.0) as client:
                response = await client.post(
                    FFLOGS_API_URL,
                    headers={"Authorization": f"Bearer {self.token}"},
                    json={"query": self._build_query(), "variables": variables},
                )

            if response.status_code == 401 and attempt == 0:
                self.token = None
                continue
            response.raise_for_status()
            data = response.json()
            errors = data.get("errors")
            if errors:
                raise FFLogsAPIError(f"FFLogs GraphQL errors: {errors!r}")
            return data.get("data", {}).get("characterData", {}).get("character")

        raise FFLogsAPIError("FFLogs authentication failed after token refresh")

    @classmethod
    def extract_results(
        cls,
        character_data: Mapping[str, Any],
    ) -> dict[str, RankingResult]:
        results: dict[str, RankingResult] = {}
        for alias, zone in character_data.items():
            if not isinstance(zone, dict):
                continue
            rankings = zone.get("rankings")
            if not isinstance(rankings, list):
                continue

            for ranking in rankings:
                if not isinstance(ranking, dict):
                    continue
                name = cls.get_ranking_display_name(alias, zone, ranking)
                if not name:
                    continue
                try:
                    percent = float(ranking.get("rankPercent") or 0)
                except (TypeError, ValueError):
                    percent = 0.0
                spec_name = str(ranking.get("spec") or "")
                result = RankingResult(percent, JOB_MAP.get(spec_name, spec_name))
                current = results.get(name)
                if current is None or result.percent > current.percent:
                    results[name] = result
        return results

    @staticmethod
    def _ranking_lines(
        results: Mapping[str, RankingResult],
        display_order: list[str],
    ) -> list[str]:
        lines = []
        for name in display_order:
            result = results.get(name)
            if result is not None:
                lines.append(
                    f"  {name.ljust(8)}: {result.percent:>4.1f} ({result.job})",
                )
        return lines or ["  暂无记录"]

    @classmethod
    def format_results(
        cls,
        character_name: str,
        server_name: str,
        results: Mapping[str, RankingResult],
    ) -> str:
        message = [f"📊 FFLogs 战绩: {character_name} @ {server_name}"]
        sections = (
            ("绝境战", ULTIMATE_DISPLAY_ORDER),
            ("7.x 阿卡狄亚", SAVAGE_70_DISPLAY_ORDER),
            ("6.x 万魔殿", SAVAGE_60_DISPLAY_ORDER),
        )
        for title, order in sections:
            message.append(f"\n【{title}】")
            message.extend(cls._ranking_lines(results, order))
        return "\n".join(message)

    async def query(self, character_name: str, server_name: str) -> str:
        character_name = character_name.strip()
        server_name = server_name.strip()
        if not character_name or not server_name:
            return "❌ 角色名和服务器名不能为空。"

        character_data = await self._fetch_character(character_name, server_name)
        if not isinstance(character_data, dict):
            return f"❌ 未找到角色: {character_name} @ {server_name}"
        results = self.extract_results(character_data)
        return self.format_results(
            character_name,
            server_name,
            results,
        )
