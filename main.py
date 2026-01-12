import httpx
import logging
from astrbot.api.all import *
logger = logging.getLogger("astrbot")
@register("ff14_logs_query", "YourName", "FF14 Logs 全版本查询", "1.0.0")
class FF14LogsPlugin(Star):
    def __init__(self, context: Context, config: dict) -> None:
        super().__init__(context)
        self.config = config
        self.token = None
    async def _get_token(self):
        """获取 FFLogs OAuth2 Token"""
        cid = self.config.get("client_id")
        secret = self.config.get("client_secret")
        
        if not cid or not secret:
            raise Exception("请先在插件配置中填写 FFLogs Client ID 和 Secret")
        url = "https://www.fflogs.com/oauth/token"
        async with httpx.AsyncClient() as client:
            res = await client.post(url, data={"grant_type": "client_credentials"}, auth=(cid, secret))
            res.raise_for_status()
            self.token = res.json().get("access_token")
    @command("fflogs")
    async def fflogs(self, event: AstrMessageEvent, 角色名: str, 服务器: str):
        '''查询 FF14 角色全版本战绩。用法: /fflogs 角色名 服务器名'''
        yield event.plain_result(f"🔍 正在检索 FFLogs 历史档案: {角色名} @ {服务器}...")
        
        try:
            if not self.token:
                await self._get_token()
            # 核心查询：涵盖 5.0 - 7.0 所有相关 Zone
            query = """
            query ($name: String, $server: String, $region: String) {
              characterData {
                character(name: $name, serverSlug: $server, serverRegion: $region) {
                  z63: zoneRankings(zoneID: 63, difficulty: 100)
                  z62: zoneRankings(zoneID: 62, difficulty: 101)
                  z59: zoneRankings(zoneID: 59, difficulty: 100)
                  z53: zoneRankings(zoneID: 53, difficulty: 100)
                  z45: zoneRankings(zoneID: 45, difficulty: 100)
                  z43: zoneRankings(zoneID: 43, difficulty: 100)
                  z38: zoneRankings(zoneID: 38)
                  z32: zoneRankings(zoneID: 32)
                  z30: zoneRankings(zoneID: 30)
                }
              }
            }
            """
            
            headers = {"Authorization": f"Bearer {self.token}"}
            async with httpx.AsyncClient() as client:
                payload = {"query": query, "variables": {"name": 角色名, "server": 服务器, "region": "CN"}}
                res = await client.post("https://cn.fflogs.com/api/v2/client", json=payload, headers=headers)
                res.raise_for_status()
                data = res.json()
            char = data.get("data", {}).get("characterData", {}).get("character")
            if not char:
                yield event.plain_result(f"❌ 未找到角色: {角色名} @ {服务器}，请检查名称是否正确或战绩是否公开。")
                return
            # 数据处理逻辑
            JOB_MAP = {
                "Paladin": "骑士", "Warrior": "战士", "DarkKnight": "暗骑", "Gunbreaker": "绝枪",
                "WhiteMage": "白魔", "Scholar": "学者", "Astrologian": "占星", "Sage": "贤者",
                "Monk": "武僧", "Dragoon": "龙骑", "Ninja": "忍者", "Samurai": "武士", "Reaper": "钐镰", "Viper": "蛇镰",
                "Bard": "诗人", "Machinist": "机工", "Dancer": "舞者",
                "BlackMage": "黑魔", "Summoner": "召唤", "RedMage": "赤魔", "Pictomancer": "画家"
            }
            # 精准适配 7.0 归档区 ID (1073-1075)
            BOSS_MAP = {
                1075: "绝亚", 1074: "绝神兵", 1073: "绝巴哈", 1076: "绝龙诗", 1077: "绝欧",
                1062: "绝亚", 1061: "绝神兵", 1060: "绝巴哈", 2060: "绝伊甸", 1068: "绝欧", 1065: "绝龙诗",
                1050: "绝亚", 1048: "绝神兵", 1047: "绝巴哈",
                93: "M1S", 94: "M2S", 95: "M3S", 96: "M4S"
            }
            ULTIMATE_LIST = ["绝伊甸", "绝欧", "绝龙诗", "绝亚", "绝神兵", "绝巴哈"]
            final_results = {} 
            for zone_key in char:
                zone_data = char[zone_key]
                if not zone_data or "rankings" not in zone_data: continue
                for r in zone_data["rankings"]:
                    eid = r.get("encounter", {}).get("id")
                    percent = r.get("rankPercent")
                    if eid in BOSS_MAP and percent is not None:
                        boss_name = BOSS_MAP[eid]
                        if boss_name not in final_results or percent > final_results[boss_name]['percent']:
                            final_results[boss_name] = {
                                "percent": percent, 
                                "job": JOB_MAP.get(r.get("spec"), r.get("spec"))
                            }
            if not final_results:
                yield event.plain_result(f"📊 {角色名} @ {服务器}\n⚠️ 未发现公开战绩记录。")
                return
            # 组装输出
            msg = f"📊 FFLogs 全版本战绩: {角色名} @ {服务器}\n\n【绝境战 Ultimate】\n"
            has_ult = False
            for name in ULTIMATE_LIST:
                if name in final_results:
                    res = final_results[name]
                    msg += f"  {name.ljust(6)}: {res['percent']:.1f} ({res['job']})\n"
                    has_ult = True
            if not has_ult: msg += "  暂无记录\n"
            msg += "\n【零式 Savage (近期)】\n"
            savage_items = sorted([(n, final_results[n]) for n in final_results if n not in ULTIMATE_LIST], key=lambda x: x[0], reverse=True)
            for name, res in savage_items[:8]:
                msg += f"  {name.ljust(7)}: {res['percent']:.1f} ({res['job']})\n"
            msg += "\n━━━━━━━━━━━━━━\n数据已穿透 5.0-7.0 归档区"
            yield event.plain_result(msg.strip())
        except Exception as e:
            logger.error(f"FFLogs 插件出错: {e}")
            yield event.plain_result(f"❌ 查询失败: {str(e)}")
