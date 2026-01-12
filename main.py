import httpx
import time
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter, AstrMessageEvent
from astrbot.api import logger
# --- 1. 常量定义移至模块级别，避免函数调用时重复创建 ---
JOB_MAP = {
    "Paladin": "骑士", "Warrior": "战士", "DarkKnight": "暗骑", "Gunbreaker": "绝枪",
    "WhiteMage": "白魔", "Scholar": "学者", "Astrologian": "占星", "Sage": "贤者",
    "Monk": "武僧", "Dragoon": "龙骑", "Ninja": "忍者", "Samurai": "武士", "Reaper": "钐镰", "Viper": "蛇镰",
    "Bard": "诗人", "Machinist": "机工", "Dancer": "舞者",
    "BlackMage": "黑魔", "Summoner": "召唤", "RedMage": "赤魔", "Pictomancer": "画家"
}
BOSS_MAP = {
    93: "M1S", 94: "M2S", 95: "M3S", 96: "M4S",
    89: "P9S", 90: "P10S", 91: "P11S", 92: "P12S",
    84: "P5S", 85: "P6S", 86: "P7S", 87: "P8S",
    79: "P1S", 80: "P2S", 81: "P3S", 82: "P4S",
    1077: "绝伊甸", 1068: "绝欧", 1065: "绝龙诗", 1062: "绝亚", 1061: "绝神兵", 1060: "绝巴哈"
}
@register("fflogs_query", "YourName", "FF14 Logs 全版本查询", "1.2.2")
class FF14LogsPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config if config else {}
        self.token = None
        self.token_expiry = 0  # Token 过期时间戳
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
            # 记录过期时间，提前 60 秒以防边界情况
            self.token_expiry = time.time() + data.get("expires_in", 86400) - 60
            logger.info("FFLogs Token 已更新")
    @filter.command("fflogs")
    async def fflogs(self, event: AstrMessageEvent, r_name: str, s_name: str):
        '''查询 FF14 战绩。用法: /fflogs 角色名 服务器名'''
        yield event.plain_result(f"🔍 正在检索 {r_name}@{s_name} 的全版本档案...")
        
        try:
            # --- Token 有效性检查 ---
            if not self.token or time.time() > self.token_expiry:
                await self._get_token()
            # GraphQL 查询语句
            query = """
            query ($name: String, $server: String, $region: String) {
              characterData {
                character(name: $name, serverSlug: $server, serverRegion: $region) {
                  s70: zoneRankings(zoneID: 63, difficulty: 101)
                  s64: zoneRankings(zoneID: 54, difficulty: 101)
                  s62: zoneRankings(zoneID: 49, difficulty: 101)
                  s60: zoneRankings(zoneID: 44, difficulty: 101)
                  u_6x: zoneRankings(zoneID: 62)
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
                
                # 处理 Token 意外失效 (401)
                if res.status_code == 401:
                    self.token = None
                    yield event.plain_result("❌ 认证失效，请重新尝试查询。")
                    return
                    
                res.raise_for_status()
                data = res.json()
            char = data.get("data", {}).get("characterData", {}).get("character")
            if not char:
                yield event.plain_result(f"❌ 未找到角色: {r_name} @ {s_name}\n请检查角色名和服务器是否正确，且数据已上传 FFLogs。")
                return
            # 数据处理逻辑
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
                        
                        # 同一副本取最高百分比
                        if name not in results or percent > results[name]['p']:
                            results[name] = {"p": percent, "j": job}
            # 消息构建
            msg = [f"📊 FFLogs 战绩: {r_name} @ {s_name}"]
            
            def get_line(name):
                if name in results:
                    res = results[name]
                    # 使用格式化对齐，让输出更美观
                    return f"  {name.ljust(6)}: {res['p']:>4.1f} ({res['j']})"
                return None
            # 1. 绝境战
            msg.append("\n【绝境战】")
            u_list = ["绝伊甸", "绝欧", "绝龙诗", "绝亚", "绝神兵", "绝巴哈"]
            u_lines = [get_line(u) for u in u_list if get_line(u)]
            msg.extend(u_lines if u_lines else ["  暂无记录"])
            # 2. 7.0 零式
            msg.append("\n【7.0 阿卡狄亚】")
            s70_list = ["M4S", "M3S", "M2S", "M1S"]
            s70_lines = [get_line(b) for b in s70_list if get_line(b)]
            msg.extend(s70_lines if s70_lines else ["  暂无记录"])
            # 3. 6.0 零式
            msg.append("\n【6.0 万魔殿】")
            s60_all = ["P12S", "P11S", "P10S", "P9S", "P8S", "P7S", "P6S", "P5S", "P4S", "P3S", "P2S", "P1S"]
            s60_lines = [get_line(b) for b in s60_all if get_line(b)]
            msg.extend(s60_lines if s60_lines else ["  暂无记录"])
            yield event.plain_result("\n".join(msg))
        except httpx.HTTPError as e:
            logger.error(f"FFLogs 网络请求失败: {e}")
            yield event.plain_result(f"❌ 网络连接失败，请稍后重试。")
        except Exception as e:
            logger.error(f"FFLogs 插件逻辑出错: {e}", exc_info=True)
            yield event.plain_result(f"❌ 查询出错: {str(e)}")
