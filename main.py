import httpx
import logging
from astrbot.api.star import Context, Star, register
from astrbot.api.event import filter

logger = logging.getLogger("astrbot")

@register("fflogs_query", "YourName", "FF14 Logs 全版本查询", "1.2.0")
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

            # 扩展查询范围：包含 7.0 (63), 6.4 (54), 6.2 (49), 6.0 (44) 以及绝本
            query = """
            query ($name: String, $server: String, $region: String) {
              characterData {
                character(name: $name, serverSlug: $server, serverRegion: $region) {
                  s70: zoneRankings(zoneID: 63, difficulty: 101)
                  s64: zoneRankings(zoneID: 54, difficulty: 101)
                  s62: zoneRankings(zoneID: 49, difficulty: 101)
                  s60: zoneRankings(zoneID: 44, difficulty: 101)
                  u_new: zoneRankings(zoneID: 62)
                  u_old: zoneRankings(zoneID: 53)
                  u_dsr: zoneRankings(zoneID: 45)
                  u_leg: zoneRankings(zoneID: 43)
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

            # 职业映射
            JOB_MAP = {
                "Paladin": "骑士", "Warrior": "战士", "DarkKnight": "暗骑", "Gunbreaker": "绝枪",
                "WhiteMage": "白魔", "Scholar": "学者", "Astrologian": "占星", "Sage": "贤者",
                "Monk": "武僧", "Dragoon": "龙骑", "Ninja": "忍者", "Samurai": "武士", "Reaper": "钐镰", "Viper": "蛇镰",
                "Bard": "诗人", "Machinist": "机工", "Dancer": "舞者",
                "BlackMage": "黑魔", "Summoner": "召唤", "RedMage": "赤魔", "Pictomancer": "画家"
            }

            # 副本映射
            BOSS_MAP = {
                # 7.0 零式
                93: "M1S", 94: "M2S", 95: "M3S", 96: "M4S",
                # 6.4 零式
                89: "P9S", 90: "P10S", 91: "P11S", 92: "P12S",
                # 6.2 零式
                84: "P5S", 85: "P6S", 86: "P7S", 87: "P8S",
                # 6.0 零式
                79: "P1S", 80: "P2S", 81: "P3S", 82: "P4S",
                # 绝本
                1077: "绝伊甸", 1068: "绝欧", 1065: "绝龙诗", 1062: "绝亚", 1061: "绝神兵", 1060: "绝巴哈"
            }

            results = {}
            for zone in char.values():
                if not zone or "rankings" not in zone: continue
                for r in zone["rankings"]:
                    bid = r.get("encounter", {}).get("id")
                    if bid in BOSS_MAP:
                        name = BOSS_MAP[bid]
                        percent = r.get("rankPercent", 0)
                        job = JOB_MAP.get(r.get("spec"), r.get("spec"))
                        # 取最高纪录
                        if name not in results or percent > results[name]['p']:
                            results[name] = {"p": percent, "j": job}

            if not results:
                yield event.plain_result(f"📊 {r_name}@{s_name}\n无公开解析记录。")
                return

            # 格式化输出
            def get_line(name):
                if name in results:
                    res = results[name]
                    return f"{name.ljust(5)}: {res['p']:>4.1f} ({res['j']})"
                return None

            msg = f"📊 FFLogs 战绩: {r_name} @ {s_name}\n"
            
            # 绝本部分
            msg += "\n【绝境战】\n"
            ults = ["绝伊甸", "绝欧", "绝龙诗", "绝亚", "绝神兵", "绝巴哈"]
            u_lines = [get_line(u) for u in ults if get_line(u)]
            msg += "\n".join(u_lines) if u_lines else "  暂无记录"

            # 7.0 零式
            msg += "\n\n【7.0 阿卡狄亚】\n"
            s70 = [get_line(b) for b in ["M4S", "M3S", "M2S", "M1S"] if get_line(b)]
            msg += "\n".join(s70) if s70 else "  暂无记录"

            # 6.0 零式
            msg += "\n\n【6.0 万魔殿】\n"
            s60 = [get_line(b) for b in ["P12S", "P11S", "P10S", "P9S", "P8S", "P7S", "P6S", "P5S", "P4S", "P3S", "P2S", "P1S"] if get_line(b)]
            if s60:
                # 如果记录太多，只显示最近的 8 个
                msg += "\n".join(s60) 
                if len(s60) > 8: msg += f"\n  ...(余下 {len(s60)-8} 个副本已省略)"
            else:
                msg += "  暂无记录"

            yield event.plain_result(msg.strip())

        except Exception as e:
            logger.error(f"FFLogs 出错: {e}")
            yield event.plain_result(f"❌ 查询失败: {str(e)}")


