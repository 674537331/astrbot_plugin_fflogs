import httpx
import time
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger

# --- 1. 常量定义 ---
JOB_MAP = {
    "Paladin": "骑士", "Warrior": "战士", "DarkKnight": "暗骑", "Gunbreaker": "绝枪",
    "WhiteMage": "白魔", "Scholar": "学者", "Astrologian": "占星", "Sage": "贤者",
    "Monk": "武僧", "Dragoon": "龙骑", "Ninja": "忍者", "Samurai": "武士", "Reaper": "钐镰", "Viper": "蛇镰",
    "Bard": "诗人", "Machinist": "机工", "Dancer": "舞者",
    "BlackMage": "黑魔", "Summoner": "召唤", "RedMage": "赤魔", "Pictomancer": "画家"
}

BOSS_MAP = {
    # 7.x 阿卡狄亚 第三阶段 (依据用户请求映射 101-105)
    105: "M12S本", 104: "M12S门", 103: "M11S", 102: "M10S", 101: "M9S",
    # 7.x 阿卡狄亚 第二阶段 (依据用户请求映射 97-100)
    100: "M8S", 99: "M7S", 98: "M6S", 97: "M5S",
    # 7.x 阿卡狄亚 第一阶段 (标准 ID 93-96)
    96: "M4S", 95: "M3S", 94: "M2S", 93: "M1S",
    
    # 6.x 万魔殿
    92: "P12S本", 91: "P12S门", 90: "P11S", 89: "P10S",
    88: "P8S本", 87: "P8S门", 86: "P7S", 85: "P6S", 84: "P5S",
    83: "P4S本", 82: "P4S门", 81: "P3S", 80: "P2S", 79: "P1S",
    
    # 绝境战
    1077: "绝伊甸", 1068: "绝欧", 1065: "绝龙诗", 1062: "绝亚", 1061: "绝神兵", 1060: "绝巴哈"
}

@register("fflogs_query", "YourName", "FF14 Logs 全版本查询", "1.5.0")
class FF14LogsPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config if config else {}
        self.token = None
        self.token_expiry = 0

    async def _get_token(self):
        cid = self.config.get("client_id", "").strip()
        secret = self.config.get("client_secret", "").strip()
        if not cid or not secret: raise ValueError("请填写 Client ID/Secret")
        
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post("https://cn.fflogs.com/oauth/token", 
                                    data={"grant_type": "client_credentials"}, 
                                    auth=(cid, secret))
            data = res.json()
            self.token = data.get("access_token")
            self.token_expiry = time.time() + data.get("expires_in", 86400) - 60

    @filter.command("fflogs")
    async def fflogs(self, event: AstrMessageEvent, r_name: str, s_name: str):
        yield event.plain_result(f"🔍 正在检索 {r_name}@{s_name} 的全版本档案...")
        
        try:
            if not self.token or time.time() > self.token_expiry:
                await self._get_token()

            query = """
            query ($name: String, $server: String, $region: String) {
              characterData {
                character(name: $name, serverSlug: $server, serverRegion: $region) {
                  s7x_3: zoneRankings(zoneID: 71, difficulty: 101)
                  s7x_2: zoneRankings(zoneID: 68, difficulty: 101)
                  s7x_1: zoneRankings(zoneID: 63, difficulty: 101)
                  s6x_3: zoneRankings(zoneID: 54, difficulty: 101)
                  s6x_2: zoneRankings(zoneID: 49, difficulty: 101)
                  s6x_1: zoneRankings(zoneID: 44, difficulty: 101)
                  u: zoneRankings(zoneID: 62)
                }
              }
            }
            """
            
            headers = {"Authorization": f"Bearer {self.token}"}
            async with httpx.AsyncClient(timeout=25.0) as client:
                res = await client.post("https://cn.fflogs.com/api/v2/client", 
                                        json={"query": query, "variables": {"name": r_name, "server": s_name, "region": "CN"}}, 
                                        headers=headers)
                data = res.json()

            char = data.get("data", {}).get("characterData", {}).get("character")
            if not char:
                yield event.plain_result(f"❌ 未找到角色: {r_name} @ {s_name}")
                return

            results = {}
            for zone in char.values():
                if not zone or "rankings" not in zone: continue
                for r in zone["rankings"]:
                    bid = r.get("encounter", {}).get("id")
                    if bid in BOSS_MAP:
                        name = BOSS_MAP[bid]
                        percent = float(r.get("rankPercent", 0) or 0)
                        job = JOB_MAP.get(r.get("spec", ""), r.get("spec", ""))
                        if name not in results or percent > results[name]['p']:
                            results[name] = {"p": percent, "j": job}

            msg = [f"📊 FFLogs 战绩: {r_name} @ {s_name}"]
            
            def get_line(name):
                if name in results:
                    res = results[name]
                    return f"  {name.ljust(7)}: {res['p']:>4.1f} ({res['j']})"
                return None

            # 1. 绝境战
            msg.append("\n【绝境战】")
            for u in ["绝伊甸", "绝欧", "绝龙诗", "绝亚", "绝神兵", "绝巴哈"]:
                line = get_line(u)
                if line: msg.append(line)

            # 2. 7.x 阿卡狄亚 (分段显示)
            msg.append("\n【7.x 阿卡狄亚】")
            s7x_order = ["M12S本", "M12S门", "M11S", "M10S", "M9S", "M8S", "M7S", "M6S", "M5S", "M4S", "M3S", "M2S", "M1S"]
            for s in s7x_order:
                line = get_line(s)
                if line: msg.append(line)
            if not any(results.get(s) for s in s7x_order): msg.append("  暂无记录")

            # 3. 6.x 万魔殿
            msg.append("\n【6.x 万魔殿】")
            s6x_order = ["P12S本", "P12S门", "P11S", "P10S", "P9S", "P8S本", "P8S门", "P7S", "P6S", "P5S", "P4S本", "P4S门", "P3S", "P2S", "P1S"]
            for s in s6x_order:
                line = get_line(s)
                if line: msg.append(line)

            yield event.plain_result("\n".join(msg))

        except Exception as e:
            logger.error(f"FFLogs 错误: {e}", exc_info=True)
            yield event.plain_result(f"❌ 查询出错")
