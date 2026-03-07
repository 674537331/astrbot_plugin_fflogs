import httpx
import time
import asyncio
import urllib.parse
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
    105: "M12S", 104: "M12S-门", 103: "M11S", 102: "M10S", 101: "M9S",
    100: "M8S", 99: "M7S", 98: "M6S", 97: "M5S",
    96: "M4S", 95: "M3S", 94: "M2S", 93: "M1S",
    92: "P12S", 91: "P11S", 90: "P10S", 89: "P9S", 
    87: "P8S", 86: "P7S", 85: "P6S", 84: "P5S",
    82: "P4S", 81: "P3S", 80: "P2S", 79: "P1S",
    1077: "绝伊甸", 1068: "绝欧", 1065: "绝龙诗", 1062: "绝亚", 1061: "绝神兵", 1060: "绝巴哈"
}

# 国服四大区名称列表
CN_DCS = ["陆行鸟", "莫古力", "猫小胖", "豆豆柴"]

@register("fflogs_query", "YourName", "FF14 Logs与物价查询", "1.4.0")
class FF14LogsPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config if config else {}
        self.token = None
        self.token_expiry = 0

    # ========================== FFLogs 战绩部分 ==========================
    async def _get_token(self):
        cid = self.config.get("client_id", "").strip()
        secret = self.config.get("client_secret", "").strip()
        if not cid or not secret or "获取" in cid:
            raise ValueError("请在插件设置中填写正确的 Client ID 和 Secret。")
        
        url = "https://cn.fflogs.com/oauth/token"
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.post(url, data={"grant_type": "client_credentials"}, auth=(cid, secret))
            res.raise_for_status()
            data = res.json()
            self.token = data.get("access_token")
            self.token_expiry = time.time() + data.get("expires_in", 86400) - 60
            logger.info("FFLogs Token 已更新")

    async def _do_fflogs_query(self, r_name: str, s_name: str) -> str:
        """核心 Logs 查询与排版逻辑提取"""
        try:
            if not self.token or time.time() > self.token_expiry:
                await self._get_token()

            query = """
            query ($name: String, $server: String, $region: String) {
              characterData {
                character(name: $name, serverSlug: $server, serverRegion: $region) {
                  s73: zoneRankings(zoneID: 73, difficulty: 101)
                  s68: zoneRankings(zoneID: 68, difficulty: 101)
                  s63: zoneRankings(zoneID: 63, difficulty: 101)
                  s54: zoneRankings(zoneID: 54, difficulty: 101)
                  s49: zoneRankings(zoneID: 49, difficulty: 101)
                  s44: zoneRankings(zoneID: 44, difficulty: 101)
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
                if res.status_code == 401:
                    self.token = None
                    return "❌ 认证失效，请重新尝试。"
                res.raise_for_status()
                data = res.json()

            char = data.get("data", {}).get("characterData", {}).get("character")
            if not char:
                return f"❌ 未找到角色: {r_name} @ {s_name}"

            results = {}
            for zone in char.values():
                if not zone or "rankings" not in zone: continue
                for r in zone["rankings"]:
                    bid = r.get("encounter", {}).get("id")
                    if bid in BOSS_MAP:
                        name = BOSS_MAP[bid]
                        raw_p = r.get("rankPercent")
                        percent = float(raw_p) if raw_p is not None else 0.0
                        spec_name = r.get("spec", "")
                        job = JOB_MAP.get(spec_name, spec_name)
                        if name not in results or percent > results[name]['p']:
                            results[name] = {"p": percent, "j": job}

            msg = [f"📊 FFLogs 战绩: {r_name} @ {s_name}"]
            def get_line(name):
                if name in results:
                    res = results[name]
                    return f"  {name.ljust(8)}: {res['p']:>4.1f} ({res['j']})"
                return None

            msg.append("\n【绝境战】")
            u_list = ["绝伊甸", "绝欧", "绝龙诗", "绝亚", "绝神兵", "绝巴哈"]
            u_lines = [get_line(u) for u in u_list if get_line(u)]
            msg.extend(u_lines if u_lines else ["  暂无记录"])

            msg.append("\n【7.0 阿卡狄亚】")
            s70_list = ["M12S", "M12S-门", "M11S", "M10S", "M9S", "M8S", "M7S", "M6S", "M5S", "M4S", "M3S", "M2S", "M1S"]
            s70_lines = [get_line(b) for b in s70_list if get_line(b)]
            msg.extend(s70_lines if s70_lines else ["  暂无记录"])

            msg.append("\n【6.0 万魔殿】")
            s60_all = ["P12S", "P11S", "P10S", "P9S", "P8S", "P7S", "P6S", "P5S", "P4S", "P3S", "P2S", "P1S"]
            s60_lines = [get_line(b) for b in s60_all if get_line(b)]
            msg.extend(s60_lines if s60_lines else ["  暂无记录"])

            return "\n".join(msg)
        except Exception as e:
            logger.error(f"FFLogs出错: {e}", exc_info=True)
            return f"❌ 查询出错: {str(e)}"

    @filter.command("fflogs")
    async def cmd_fflogs(self, event: AstrMessageEvent, r_name: str, s_name: str):
        '''查询 FF14 战绩。用法: /fflogs 角色名 服务器名'''
        yield event.plain_result(f"🔍 正在检索 {r_name}@{s_name} 的全版本档案...")
        result_msg = await self._do_fflogs_query(r_name, s_name)
        yield event.plain_result(result_msg)

    # 通过这个装饰器，向大模型暴露工具
    @filter.llm_tool(name="search_fflogs")
    async def tool_fflogs(self, event: AstrMessageEvent, character_name: str, server_name: str):
        '''用于查询FF14玩家的Logs战绩。
        Args:
            character_name(string): 玩家的角色名，如"冰冷"
            server_name(string): 玩家所在的服务器名，如"白银乡"
        '''
        # 提前向用户发送等待提示（使用直接 send）
        await event.send(event.plain_result(f"🔍 收到自然语言请求，正在检索 {character_name}@{server_name} 的战绩..."))
        
        # 执行查询
        result_msg = await self._do_fflogs_query(character_name, server_name)
        
        # 将完整结果发给用户
        await event.send(event.plain_result(result_msg))
        
        # 告诉大模型结果已经发送了，避免它长篇大论复读格式
        return "查询结果已经直接发送给用户了。请你简单回复一句话告知用户查询完毕即可，不要重复输出查询战绩。"

    # ========================== Universalis 查价部分 ==========================
    async def _search_item_id(self, item_name: str):
        """利用国服版 XIVAPI (cafemaker) 模糊检索物品 ID"""
        url = f"https://cafemaker.wakingsands.com/search?indexes=Item&string={urllib.parse.quote(item_name)}"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    results = data.get("Results", [])
                    if results:
                        # 尝试精确匹配名字
                        for item in results:
                            if item.get("Name", "").lower() == item_name.lower():
                                return item.get("ID"), item.get("Name")
                        # 没有精确匹配就返回第一个搜索结果
                        return results[0].get("ID"), results[0].get("Name")
            except Exception as e:
                logger.error(f"请求物品ID失败: {e}")
        return None, None

    async def _get_dc_lowest_price(self, item_id: int, dc: str):
        """请求单个大区的最低价 (listings=1 确保服务器只返回最便宜的那条数据)"""
        url = f"https://universalis.app/api/v2/{urllib.parse.quote(dc)}/{item_id}?listings=1"
        async with httpx.AsyncClient(timeout=10.0) as client:
            try:
                res = await client.get(url)
                if res.status_code == 200:
                    data = res.json()
                    listings = data.get("listings", [])
                    if listings:
                        listing = listings[0]
                        return {
                            "price": listing.get("pricePerUnit"),
                            "world": listing.get("worldName", "未知"),
                            "quantity": listing.get("quantity", 0),
                            "hq": listing.get("hq", False)
                        }
            except Exception as e:
                logger.error(f"获取 {dc} 物价失败: {e}")
        return None

    @filter.command("ff14")
    async def cmd_ff14_price(self, event: AstrMessageEvent, item_name: str):
        '''查询 FF14 物品大区最低价。用法: /ff14 物品名'''
        yield event.plain_result(f"🔍 正在寻找物品 [{item_name}]...")
        
        item_id, real_name = await self._search_item_id(item_name)
        if not item_id:
            yield event.plain_result(f"❌ 未找到物品: {item_name}，请检查错别字。")
            return

        yield event.plain_result(f"📦 确认物品: {real_name} (ID: {item_id})\n正在并发查询各大区物价...")

        # 利用协程并发，一次性向 Universalis 索要四大区数据，大幅提高速度
        tasks = [self._get_dc_lowest_price(item_id, dc) for dc in CN_DCS]
        results = await asyncio.gather(*tasks)

        msg = [f"💰 【{real_name}】 全大区最低价一览:"]
        for dc, res in zip(CN_DCS, results):
            if res:
                hq_mark = " (HQ)" if res["hq"] else ""
                msg.append(f"[{dc}] {res['price']} 金币 @ {res['world']} x{res['quantity']}{hq_mark}")
            else:
                msg.append(f"[{dc}] 暂无在售")

        yield event.plain_result("\n".join(msg))
