import httpx
import logging
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter

logger = logging.getLogger("astrbot")

@register("fflogs_query", "YourName", "FF14 Logs 全版本查询", "1.2.1")
class FF14LogsPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config if config else {}
        self.token = None

    async def _get_token(self):
        cid = self.config.get("client_id")
        secret = self.config.get("client_secret")
        if not cid or not secret or "获取" in cid:
            raise Exception("请在插件设置中填写正确的 Client ID 和 Secret。")
        
        url = "https://cn.fflogs.com/oauth/token"
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, data={"grant_type": "client_credentials"}, auth=(cid.strip(), secret.strip()))
            res.raise_for_status()
            self.token = res.json().get("access_token")

    @filter.command("fflogs")
    async def fflogs(self, event, r_name: str, s_name: str):
        '''查询 FF14 6.0/7.0 全战绩。用法: /fflogs 角色名 服务器名'''
        yield event.plain_result(f"🔍 正在检索 {r_name}@{s_name} 的全版本档案...")
        
        try:
            if not self.token: await self._get_token()

            # 查询 7.0(63), 6.4(54), 6.2(49), 6.0(44) 以及绝本
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
                payload = {"query": query, "variables": {"name": r_name, "server": s_name, "region": "CN"}}
                res = await client.post("https://cn.fflogs.com/api/v2/client", json=payload, headers=headers)
                data = res.json()

            char = data.get("data", {}).get("characterData", {}).get("character")
            if not char:
                yield event.plain_result(f"❌ 未找到角色: {r_name} @ {s_name}")
                return

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

            results = {}
            for zone in char.values():
                if not zone or "rankings" not in zone: continue
                for r in zone["rankings"]:
                    bid = r.get("encounter", {}).get("id")
                    if bid in BOSS_MAP:
                        name = BOSS_MAP[bid]
                        # 核心修复：处理 None 值，确保百分比是数字
                        raw_p = r.get("rankPercent")
                        percent = float(raw_p) if raw_p is not None else 0.0
                        job = JOB_MAP.get(r.get("spec"), r.get("spec"))
                        if name not in results or percent > results[name]['p']:
                            results[name] = {"p": percent, "j": job}

            msg = f"📊 FFLogs 战绩: {r_name} @ {s_name}\n"
            
            def get_line(name):
                if name in results:
                    res = results[name]
                    return f"  {name.ljust(6)}: {res['p']:>4.1f} ({res['j']})"
                return None

            # 1. 绝境战
            msg += "\n【绝境战】\n"
            u_list = ["绝伊甸", "绝欧", "绝龙诗", "绝亚", "绝神兵", "绝巴哈"]
            u_lines = [get_line(u) for u in u_list if get_line(u)]
            msg += "\n".join(u_lines) if u_lines else "  暂无记录"

            # 2. 7.0 零式
            msg += "\n\n【7.0 阿卡狄亚】\n"
            s70_list = ["M4S", "M3S", "M2S", "M1S"]
            s70_lines = [get_line(b) for b in s70_list if get_line(b)]
            msg += "\n".join(s70_lines) if s70_lines else "  暂无记录"

            # 3. 6.0 零式 (万魔殿)
            msg += "\n\n【6.0 万魔殿】\n"
            # 分三段显示：天狱、炼狱、边境
            s60_all = ["P12S", "P11S", "P10S", "P9S", "P8S", "P7S", "P6S", "P5S", "P4S", "P3S", "P2S", "P1S"]
            s60_lines = [get_line(b) for b in s60_all if get_line(b)]
            
            if s60_lines:
                msg += "\n".join(s60_lines)
            else:
                msg += "  暂无记录"

            yield event.plain_result(msg.strip())

        except Exception as e:
            logger.error(f"FFLogs 出错: {e}")
            yield event.plain_result(f"❌ 查询失败: {str(e)}")
