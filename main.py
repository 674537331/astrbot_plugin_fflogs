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
    # 7.x 零式 (阿卡狄亚) - Zone 71, 68, 63
    105: "M12S本", 104: "M12S门", 103: "M11S", 102: "M10S", 101: "M9S",
    100: "M8S", 99: "M7S", 98: "M6S", 97: "M5S",
    96: "M4S", 95: "M3S", 94: "M2S", 93: "M1S",
    
    # 6.x 零式 (万魔殿) - Zone 54, 49, 44
    92: "P12S本", 91: "P12S门", 90: "P11S", 89: "P10S",
    88: "P8S本", 87: "P8S门", 86: "P7S", 85: "P6S", 84: "P5S",
    83: "P4S本", 82: "P4S门", 81: "P3S", 80: "P2S", 79: "P1S",
    
    # 绝境战
    1077: "绝伊甸", 1068: "绝欧", 1065: "绝龙诗", 1062: "绝亚", 1061: "绝神兵", 1060: "绝巴哈"
}

@register("fflogs_query", "YourName", "FF14 Logs 全版本查询", "1.4.0")
class FF14LogsPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config if config else {}
        self.token = None
        self.token_expiry = 0

    async def _get_token(self):
        """获取并更新 FFLogs OAuth2 Token"""
        cid = self.config.get("client_id", "").strip()
        secret = self.config.get("client_secret", "").strip()
        
        if not cid or not secret or "获取" in cid:
            raise ValueError("请在插件设置中填写正确的 Client ID 和 Secret。")
        
        url = "https://cn.fflogs.com/oauth/token"
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(
                url, 
                data={"grant_type": "client_credentials"}, 
                auth=(cid, secret)
            )
            res.raise_for_status()
            data = res.json()
            self.token = data.get("access_token")
            self.token_expiry = time.time() + data.get("expires_in", 86400) - 60
            logger.info("FFLogs Token 已更新")

    @filter.command("fflogs")
    async def fflogs(self, event: AstrMessageEvent, r_name: str, s_name: str):
        '''查询 FF14 战绩。用法: /fflogs 角色名 服务器名'''
        yield event.plain_result(f"🔍 正在检索 {r_name}@{s_name} 的全版本档案...")
        
        try:
            if not self.token or time.time() > self.token_expiry:
                await self._get_token()

            query = """
            query ($name: String, $server: String, $region: String) {
              characterData {
                character(name: $name, serverSlug: $server, serverRegion: $region) {
                  s71: zoneRankings(zoneID: 71, difficulty: 101)
                  s68: zoneRankings(zoneID: 68, difficulty: 101)
                  s63: zoneRankings(zoneID: 63, difficulty: 101)
                  s54: zoneRankings(zoneID: 54, difficulty: 101)
                  s49: zoneRankings(zoneID: 49, difficulty: 101)
                  s44: zoneRankings(zoneID: 44, difficulty: 101)
                  u_all: zoneRankings(zoneID: 62)
                  u_5x: zoneRankings(zoneID: 53)
                  u_4x: zoneRankings(zoneID: 45)
                  u_3x: zoneRankings(zoneID: 43)
                }
              }
            }
            """
            
            headers = {"Authorization": f"Bearer {self.token}"}
            async with httpx.AsyncClient(timeout=25.0) as client:
                payload = {
                    "query": query, 
                    "variables": {"name": r_name, "server": s_name, "region": "CN"}
                }
                res = await client.post("https://cn.fflogs.com/api/v2/client", json=payload, headers=headers)
                
                if res.status_code == 401:
                    self.token = None
                    yield event.plain_result("❌ 认证失效，请重新尝试查询。")
                    return
                    
                res.raise_for_status()
                data = res.json()

            char = data.get("data", {}).get("characterData", {}).get("character")
            if not char:
                yield event.plain_result(f"❌ 未找到角色: {r_name} @ {s_name}")
                return

            results = {}
            for zone in char.values():
                if not zone or "rankings" not in zone:
                    continue
                for r in zone["rankings"]:
                    bid = r.get("encounter", {}).get("id")
                    if bid in BOSS_MAP:
                        name = BOSS_MAP[bid]
                        raw_p = r.get("rankPercent")
                        percent = float(raw_p) if raw_p is not None else 0.0
                        spec_name = r.get("spec", "")
                        job = JOB_MAP.get(spec_name, spec_name)
                        
                        # 核心逻辑改动：每个副本名（含门/本）独立记录
                        if name not in results or percent > results[name]['p']:
                            results[name] = {"p": percent, "j": job}

            msg = [f"📊 FFLogs 战绩: {r_name} @ {s_name}"]
            
            def get_line(name):
                if name in results:
                    res = results[name]
                    # 由于含有中文字符，此处使用 ljust 可能会有微小偏移，但在大部分客户端表现尚可
                    return f"  {name.ljust(6)}: {res['p']:>4.1f} ({res['j']})"
                return None

            # 1. 绝境战
            msg.append("\n【绝境战】")
            u_list = ["绝伊甸", "绝欧", "绝龙诗", "绝亚", "绝神兵", "绝巴哈"]
            u_lines = [get_line(u) for u in u_list if get_line(u)]
            msg.extend(u_lines if u_lines else ["  暂无记录"])

            # 2. 7.x 零式 (阿卡狄亚)
            msg.append("\n【7.x 阿卡狄亚】")
            s7x_list = ["M12S本", "M12S门", "M11S", "M10S", "M9S", "M8S", "M7S", "M6S", "M5S", "M4S", "M3S", "M2S", "M1S"]
            s7x_lines = [get_line(b) for b in s7x_list if get_line(b)]
            msg.extend(s7x_lines if s7x_lines else ["  暂无记录"])

            # 3. 6.x 零式 (万魔殿)
            msg.append("\n【6.x 万魔殿】")
            s60_all = ["P12S本", "P12S门", "P11S", "P10S", "P9S", "P8S本", "P8S门", "P7S", "P6S", "P5S", "P4S本", "P4S门", "P3S", "P2S", "P1S"]
            s60_lines = [get_line(b) for b in s60_all if get_line(b)]
            msg.extend(s60_lines if s60_lines else ["  暂无记录"])

            yield event.plain_result("\n".join(msg))

        except httpx.HTTPError as e:
            logger.error(f"FFLogs 网络请求失败: {e}")
            yield event.plain_result(f"❌ 网络连接失败。")
        except Exception as e:
            logger.error(f"FFLogs 错误: {e}", exc_info=True)
            yield event.plain_result(f"❌ 查询出错: {str(e)}")
