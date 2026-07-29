import httpx
import time
import asyncio
import urllib.parse
import re
import html
from datetime import datetime, timedelta
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

SAVAGE_BOSS_MAP = {
    105: "M12S", 104: "M12S-门", 103: "M11S", 102: "M10S", 101: "M9S",
    100: "M8S", 99: "M7S", 98: "M6S", 97: "M5S",
    96: "M4S", 95: "M3S", 94: "M2S", 93: "M1S",
    92: "P12S", 91: "P11S", 90: "P10S", 89: "P9S", 
    87: "P8S", 86: "P7S", 85: "P6S", 84: "P5S",
    82: "P4S", 81: "P3S", 80: "P2S", 79: "P1S",
}

ULTIMATE_BOSS_MAP = {
    # 旧 zone 中的绝本 encounter id
    1060: "绝巴哈",
    1061: "绝神兵",
    1062: "绝亚",
    1065: "绝龙诗",
    1068: "绝欧",
    # 7.x Ultimates (Legacy) zone 中的旧绝本 encounter id
    1073: "绝巴哈",
    1074: "绝神兵",
    1075: "绝亚",
    1076: "绝龙诗",
    1077: "绝欧",
    # 7.x Futures Rewritten
    1079: "绝伊甸",
    # 7.x Dancing Mad
    1085: "绝妖星乱舞",
}

SAVAGE_DIFFICULTY_ID = 101
SAVAGE_ZONE_RANKINGS = (
    ("s73", 73, "difficulty: 101"),
    ("s68", 68, "difficulty: 101"),
    ("s63", 63, "difficulty: 101"),
    ("s54", 54, "difficulty: 101"),
    ("s49", 49, "difficulty: 101"),
    ("s44", 44, "difficulty: 101"),
)
ULTIMATE_ZONE_RANKINGS = (
    ("u_dmu", 76, ""),
    ("u_fru", 65, ""),
    ("u_7x_legacy", 59, ""),
    ("u_5x", 53, ""),
    ("u_4x", 45, ""),
    ("u_3x", 43, ""),
)
FFLOGS_ZONE_RANKINGS = SAVAGE_ZONE_RANKINGS + ULTIMATE_ZONE_RANKINGS
SAVAGE_ZONE_ALIASES = {alias for alias, _, _ in SAVAGE_ZONE_RANKINGS}
ULTIMATE_DISPLAY_ORDER = ["绝妖星乱舞", "绝伊甸", "绝欧", "绝龙诗", "绝亚", "绝神兵", "绝巴哈"]
SAVAGE_70_DISPLAY_ORDER = ["M12S", "M12S-门", "M11S", "M10S", "M9S", "M8S", "M7S", "M6S", "M5S", "M4S", "M3S", "M2S", "M1S"]
SAVAGE_60_DISPLAY_ORDER = ["P12S", "P11S", "P10S", "P9S", "P8S", "P7S", "P6S", "P5S", "P4S", "P3S", "P2S", "P1S"]

# 国服四大区名称列表
CN_DCS = ["陆行鸟", "莫古力", "猫小胖", "豆豆柴"]
XIVAPI_V2_BASE_URL = "https://xivapi-v2.xivcdn.com"
SERVER_STATUS_URL = "https://ff14act.web.sdo.com/api/serverStatus/getServerStatus"
NEWS_LIST_URL = "https://cqnews.web.sdo.com/api/news/newsList"
NEWS_DETAIL_URL = "https://cqnews.web.sdo.com/api/news/newsDetail"
NEWS_DETAIL_BASE_URL = "https://ff.web.sdo.com/web8/index.html#/newstab/newscont"
NEWS_CATEGORY_CODES = "8324,8325,8326,8327,5309,5310,5311,5312,5313"
MAINTENANCE_CATEGORY_CODE = "8324"
MAINTENANCE_DONE_KEYWORDS = ("完成公告", "维护完成", "现已完成", "维护现已完成")
IMPORTANT_MAINTENANCE_KEYWORDS = (
    "全区全服更新维护公告",
    "临时维护",
    "停机维护",
    "无法登录游戏",
    "服务器临时维护",
)
LOW_IMPACT_MAINTENANCE_KEYWORDS = (
    "调整维护",
    "调整优化",
    "网络线路调整",
    "线路优化",
)

@register("fflogs_query", "YourName", "FF14 Logs与物价查询", "1.4.0")
class FF14LogsPlugin(Star):
    def __init__(self, context: Context, config: dict = None):
        super().__init__(context)
        self.config = config if config else {}
        self.token = None
        self.token_expiry = 0

    def _get_proxy_url(self) -> str:
        return self.config.get("proxy_url", "").strip()

    def _create_http_client(self, timeout: float) -> httpx.AsyncClient:
        proxy_url = self._get_proxy_url()
        if not proxy_url:
            return httpx.AsyncClient(timeout=timeout)
        try:
            return httpx.AsyncClient(timeout=timeout, proxy=proxy_url)
        except TypeError:
            return httpx.AsyncClient(timeout=timeout, proxies=proxy_url)

    @staticmethod
    def _xivapi_query_value(value: str) -> str:
        return value.replace("\\", "\\\\").replace('"', '\\"')

    def _get_news_count(self) -> int:
        try:
            count = int(self.config.get("news_count", 5))
        except (TypeError, ValueError):
            count = 5
        return min(max(count, 1), 20)

    def _show_low_impact_maintenance(self) -> bool:
        value = self.config.get("show_low_impact_maintenance", False)
        if isinstance(value, str):
            return value.strip().lower() in ("1", "true", "yes", "on", "是", "开启")
        return bool(value)

    @staticmethod
    def _official_news_url(item: dict) -> str:
        out_link = item.get("OutLink", "").strip()
        if out_link:
            return out_link
        return f"{NEWS_DETAIL_BASE_URL}/{item.get('Id')}"

    @staticmethod
    def _plain_text_from_html(content: str) -> str:
        text = re.sub(r"<br\s*/?>", "\n", content or "", flags=re.IGNORECASE)
        text = re.sub(r"</p\s*>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text).replace("\xa0", " ")
        return re.sub(r"[ \t]+", " ", text)

    @staticmethod
    def _parse_maintenance_time(text: str):
        now = datetime.now()
        time_range = re.search(
            r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*"
            r"(\d{1,2}):(\d{2})\s*(?:-|~|—|至|到)\s*"
            r"(?:(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*)?"
            r"(\d{1,2}):(\d{2})",
            text,
        )
        if time_range:
            (
                start_year,
                start_month,
                start_day,
                start_hour,
                start_minute,
                end_year,
                end_month,
                end_day,
                end_hour,
                end_minute,
            ) = time_range.groups()
            start_year = int(start_year or now.year)
            start_month = int(start_month)
            start_day = int(start_day)
            end_year = int(end_year or start_year)
            end_month = int(end_month or start_month)
            end_day = int(end_day or start_day)
            start_at = datetime(start_year, start_month, start_day, int(start_hour), int(start_minute))
            end_at = datetime(end_year, end_month, end_day, int(end_hour), int(end_minute))
            if end_at < start_at:
                end_at += timedelta(days=1)
            return start_at, end_at

        date_range = re.search(
            r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日\s*(?:至|到|-|~|—)\s*"
            r"(?:(?:(\d{4})年)?(\d{1,2})月)?(\d{1,2})日",
            text,
        )
        if date_range:
            start_year, start_month, start_day, end_year, end_month, end_day = date_range.groups()
            start_year = int(start_year or now.year)
            start_month = int(start_month)
            start_day = int(start_day)
            end_year = int(end_year or start_year)
            end_month = int(end_month or start_month)
            start_at = datetime(start_year, start_month, start_day, 0, 0)
            end_at = datetime(end_year, end_month, int(end_day), 23, 59, 59)
            return start_at, end_at

        return None, None

    @staticmethod
    def _format_dt(dt: datetime) -> str:
        return dt.strftime("%Y-%m-%d %H:%M")

    # ========================== FFLogs 战绩部分 ==========================
    @staticmethod
    def _build_zone_rankings_query() -> str:
        lines = []
        for alias, zone_id, extra_args in FFLOGS_ZONE_RANKINGS:
            args = f"zoneID: {zone_id}"
            if extra_args:
                args = f"{args}, {extra_args}"
            lines.append(f"                  {alias}: zoneRankings({args})")
        return "\n".join(lines)

    @staticmethod
    def _get_difficulty_id(*payloads):
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

    def _get_ranking_display_name(self, alias: str, zone: dict, ranking: dict):
        encounter_id = ranking.get("encounter", {}).get("id")

        if encounter_id in SAVAGE_BOSS_MAP:
            difficulty_id = self._get_difficulty_id(ranking, zone)
            if alias not in SAVAGE_ZONE_ALIASES or difficulty_id != SAVAGE_DIFFICULTY_ID:
                logger.debug(
                    f"忽略非零式记录: alias={alias}, encounter={encounter_id}, difficulty={difficulty_id}"
                )
                return None
            return SAVAGE_BOSS_MAP[encounter_id]

        if encounter_id in ULTIMATE_BOSS_MAP:
            return ULTIMATE_BOSS_MAP[encounter_id]

        return None

    async def _get_token(self):
        cid = self.config.get("client_id", "").strip()
        secret = self.config.get("client_secret", "").strip()
        if not cid or not secret or "获取" in cid:
            raise ValueError("请在插件设置中填写正确的 Client ID 和 Secret。")
        
        url = "https://cn.fflogs.com/oauth/token"
        async with self._create_http_client(timeout=10.0) as client:
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

            zone_rankings_query = self._build_zone_rankings_query()
            query = f"""
            query ($name: String, $server: String, $region: String) {{
              characterData {{
                character(name: $name, serverSlug: $server, serverRegion: $region) {{
{zone_rankings_query}
                }}
              }}
            }}
            """
            headers = {"Authorization": f"Bearer {self.token}"}
            async with self._create_http_client(timeout=25.0) as client:
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
            for alias, zone in char.items():
                if not zone or "rankings" not in zone: continue
                for r in zone["rankings"]:
                    name = self._get_ranking_display_name(alias, zone, r)
                    if not name:
                        continue

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
            u_lines = [get_line(u) for u in ULTIMATE_DISPLAY_ORDER if get_line(u)]
            msg.extend(u_lines if u_lines else ["  暂无记录"])

            msg.append("\n【7.0 阿卡狄亚】")
            s70_lines = [get_line(b) for b in SAVAGE_70_DISPLAY_ORDER if get_line(b)]
            msg.extend(s70_lines if s70_lines else ["  暂无记录"])

            msg.append("\n【6.0 万魔殿】")
            s60_lines = [get_line(b) for b in SAVAGE_60_DISPLAY_ORDER if get_line(b)]
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
        """利用国服版 XIVAPI v2 模糊检索物品 ID"""
        url = f"{XIVAPI_V2_BASE_URL}/api/search"
        params = {
            "sheets": "Item",
            "fields": "Name",
            "query": f'Name~"{self._xivapi_query_value(item_name)}"',
            "limit": 10,
            "language": "chs",
        }
        async with self._create_http_client(timeout=10.0) as client:
            try:
                res = await client.get(url, params=params)
                if res.status_code == 200:
                    data = res.json()
                    results = data.get("results", [])
                    if results:
                        # 尝试精确匹配名字
                        for item in results:
                            name = item.get("fields", {}).get("Name", "")
                            if name.lower() == item_name.lower():
                                return item.get("row_id"), name
                        # 没有精确匹配就返回第一个搜索结果
                        first = results[0]
                        return first.get("row_id"), first.get("fields", {}).get("Name")
            except Exception as e:
                logger.error(f"请求物品ID失败: {e}")
        return None, None

    async def _get_dc_lowest_price(self, item_id: int, dc: str):
        """请求单个大区的最低价 (listings=1 确保服务器只返回最便宜的那条数据)"""
        url = f"https://universalis.app/api/v2/{urllib.parse.quote(dc)}/{item_id}?listings=1"
        async with self._create_http_client(timeout=10.0) as client:
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

    # ========================== 国服服务器状态部分 ==========================
    @staticmethod
    def _format_bool_status(enabled: bool, true_text: str, false_text: str) -> str:
        return true_text if enabled else false_text

    @staticmethod
    def _format_preferred_status(server: dict) -> str:
        if server.get("isnew", False):
            return "优待状态: 特别优待"
        if server.get("isupgrade", False):
            return "优待状态: 优待"
        return "优待状态: 普通"

    def _format_server_status(self, data: list) -> str:
        msg = ["🌐 国服服务器状态一览"]
        for area in data:
            area_name = area.get("AreaName", "未知大区")
            servers = area.get("Group", [])
            msg.append(f"\n【{area_name}】")
            if not servers:
                msg.append("  暂无服务器状态")
                continue

            for server in servers:
                if server.get("iskong"):
                    continue

                name = server.get("name", "未知服务器")
                statuses = [
                    self._format_bool_status(server.get("runing", False), "运行中", "维护中"),
                    self._format_bool_status(server.get("isint", False), "可转入", "不可转入"),
                    self._format_bool_status(server.get("isout", False), "可转出", "不可转出"),
                    self._format_bool_status(server.get("iscreate", False), "可创建新角色", "不可创建新角色"),
                    self._format_preferred_status(server),
                ]
                msg.append(f"  {name}: {' / '.join(statuses)}")

        return "\n".join(msg)

    async def _do_server_status_query(self) -> str:
        try:
            async with self._create_http_client(timeout=10.0) as client:
                res = await client.get(SERVER_STATUS_URL)
                res.raise_for_status()
                data = res.json()

            if not data.get("IsSuccess"):
                return f"❌ 获取服务器状态失败: {data.get('Errormsg') or '官网接口返回失败'}"

            return self._format_server_status(data.get("Data", []))
        except Exception as e:
            logger.error(f"获取服务器状态失败: {e}", exc_info=True)
            return f"❌ 获取服务器状态失败: {str(e)}"

    @filter.command("ff14status")
    async def cmd_ff14_status(self, event: AstrMessageEvent):
        '''查询 FF14 国服服务器状态。用法: /ff14status'''
        yield event.plain_result("🔍 正在获取国服服务器状态...")
        result_msg = await self._do_server_status_query()
        yield event.plain_result(result_msg)

    # ========================== 国服官网新闻与维护公告部分 ==========================
    async def _fetch_news_list(self, category_codes: str, page_size: int, page_index: int = 0) -> list:
        params = {
            "gameCode": "ff",
            "CategoryCode": category_codes,
            "pageIndex": page_index,
            "pageSize": page_size,
        }
        async with self._create_http_client(timeout=10.0) as client:
            res = await client.get(NEWS_LIST_URL, params=params)
            res.raise_for_status()
            data = res.json()
        if str(data.get("Code")) != "0":
            raise ValueError(data.get("Message") or "官网新闻列表接口返回失败")
        return data.get("Data", [])

    async def _fetch_news_detail(self, news_id: int) -> dict:
        params = {"gameCode": "ff", "id": news_id}
        async with self._create_http_client(timeout=10.0) as client:
            res = await client.get(NEWS_DETAIL_URL, params=params)
            res.raise_for_status()
            data = res.json()
        if str(data.get("Code")) != "0":
            raise ValueError(data.get("Message") or "官网新闻详情接口返回失败")
        return data.get("Data", {})

    def _format_news_list(self, items: list) -> str:
        if not items:
            return "📰 暂无官方新闻。"

        msg = ["📰 最新官方情报"]
        for index, item in enumerate(items, start=1):
            publish_date = item.get("PublishDate", "").split(" ")[0].replace("/", "-")
            title = item.get("Title", "未命名公告")
            url = self._official_news_url(item)
            msg.append(f"{index}. [{publish_date}] {title}\n{url}")
        return "\n".join(msg)

    async def _do_news_query(self) -> str:
        try:
            count = self._get_news_count()
            items = await self._fetch_news_list(NEWS_CATEGORY_CODES, count)
            return self._format_news_list(items[:count])
        except Exception as e:
            logger.error(f"获取官方新闻失败: {e}", exc_info=True)
            return f"❌ 获取官方新闻失败: {str(e)}"

    @staticmethod
    def _is_maintenance_candidate(item: dict) -> bool:
        title = item.get("Title", "")
        summary = item.get("Summary", "")
        text = f"{title} {summary}"
        return "维护" in text and not any(keyword in text for keyword in MAINTENANCE_DONE_KEYWORDS)

    @staticmethod
    def _classify_maintenance_impact(text: str):
        if any(keyword in text for keyword in IMPORTANT_MAINTENANCE_KEYWORDS):
            return "important", "重点维护，可能影响登录"
        if any(keyword in text for keyword in LOW_IMPACT_MAINTENANCE_KEYWORDS):
            return "low", "网络/线路调整，通常只影响少部分用户"
        return "general", "一般维护，可能影响部分功能"

    def _build_maintenance_item(self, item: dict, detail: dict):
        title = detail.get("Title") or item.get("Title", "未命名维护公告")
        summary = detail.get("Summary") or item.get("Summary", "")
        content_text = self._plain_text_from_html(detail.get("Content", ""))
        all_text = f"{title}\n{summary}\n{content_text}"

        if any(keyword in all_text for keyword in MAINTENANCE_DONE_KEYWORDS):
            return None

        start_at, end_at = self._parse_maintenance_time(all_text)
        if not start_at or not end_at:
            return None
        if end_at and end_at < datetime.now():
            return None

        now = datetime.now()
        status = "进行中" if start_at <= now <= end_at else "预定"
        impact_level, impact_text = self._classify_maintenance_impact(all_text)

        return {
            "title": title,
            "url": self._official_news_url(detail or item),
            "status": status,
            "impact_level": impact_level,
            "impact_text": impact_text,
            "start_at": start_at,
            "end_at": end_at,
            "publish_date": (detail.get("PublishDate") or item.get("PublishDate", "")).split(" ")[0].replace("/", "-"),
        }

    def _format_maintenance_list(self, items: list) -> str:
        important_items = [item for item in items if item["impact_level"] == "important"]
        general_items = [item for item in items if item["impact_level"] == "general"]
        low_items = [item for item in items if item["impact_level"] == "low"]
        show_low_impact = self._show_low_impact_maintenance()

        if not important_items and not show_low_impact:
            if general_items or low_items:
                hidden_count = len(general_items) + len(low_items)
                return f"🛠️ 当前没有影响登录的重点维护公告。\n已隐藏 {hidden_count} 条一般/低影响维护公告，可在插件配置中开启显示。"
            return "🛠️ 当前没有正在进行中或已预定的重点维护公告。"

        msg = ["🛠️ 当前重点维护公告"]
        display_items = important_items
        if show_low_impact:
            display_items = important_items + general_items + low_items
            msg = ["🛠️ 当前维护公告"]

        if not display_items:
            return "🛠️ 当前没有正在进行中或已预定的维护公告。"

        for index, item in enumerate(display_items, start=1):
            if item["start_at"] and item["end_at"]:
                time_text = f"{self._format_dt(item['start_at'])} 至 {self._format_dt(item['end_at'])}"
            else:
                time_text = "详见公告"
            msg.append(
                f"{index}. [{item['status']}] {item['title']}\n"
                f"影响: {item['impact_text']}\n"
                f"时间: {time_text}\n"
                f"{item['url']}"
            )
        return "\n".join(msg)

    async def _do_maintenance_query(self) -> str:
        try:
            news_items = await self._fetch_news_list(MAINTENANCE_CATEGORY_CODE, 30)
            candidates = [item for item in news_items if self._is_maintenance_candidate(item)]
            results = []
            for item in candidates:
                detail = await self._fetch_news_detail(item.get("Id"))
                maintenance = self._build_maintenance_item(item, detail)
                if maintenance:
                    results.append(maintenance)
            return self._format_maintenance_list(results)
        except Exception as e:
            logger.error(f"获取维护公告失败: {e}", exc_info=True)
            return f"❌ 获取维护公告失败: {str(e)}"

    @filter.command("ff14news")
    async def cmd_ff14_news(self, event: AstrMessageEvent):
        '''查询 FF14 国服官网最新新闻。用法: /ff14news'''
        yield event.plain_result("🔍 正在获取官方最新情报...")
        result_msg = await self._do_news_query()
        yield event.plain_result(result_msg)

    @filter.command("ff14maint")
    async def cmd_ff14_maintenance(self, event: AstrMessageEvent):
        '''查询 FF14 国服维护公告。用法: /ff14maint'''
        yield event.plain_result("🔍 正在获取官方维护公告...")
        result_msg = await self._do_maintenance_query()
        yield event.plain_result(result_msg)
