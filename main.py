from collections.abc import Awaitable, Callable

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star

from .services import FFLogsConfigurationError, FFLogsService, FFXIVService


class FF14LogsPlugin(Star):
    """查询 FFLogs、国服物价、服务器状态和官网公告。"""

    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.fflogs = FFLogsService(config)
        self.ffxiv = FFXIVService(config)

    async def _run_query(
        self,
        operation: str,
        query: Callable[[], Awaitable[str]],
        failure_message: str,
    ) -> str:
        try:
            return await query()
        except FFLogsConfigurationError as exc:
            return f"❌ {exc}"
        except Exception:
            logger.error("%s失败", operation, exc_info=True)
            return failure_message

    async def _query_fflogs(self, character_name: str, server_name: str) -> str:
        return await self._run_query(
            "查询 FFLogs",
            lambda: self.fflogs.query(character_name, server_name),
            "❌ FFLogs 查询失败，请稍后重试或检查插件日志。",
        )

    @filter.command("fflogs")
    async def cmd_fflogs(
        self,
        event: AstrMessageEvent,
        character_name: str,
        server_name: str,
    ):
        """查询 FF14 战绩。用法：/fflogs 角色名 服务器名"""
        yield event.plain_result(
            f"🔍 正在检索 {character_name}@{server_name} 的全版本档案...",
        )
        yield event.plain_result(
            await self._query_fflogs(character_name, server_name),
        )

    @filter.llm_tool(name="search_fflogs")
    async def tool_fflogs(
        self,
        event: AstrMessageEvent,
        character_name: str,
        server_name: str,
    ):
        """查询 FF14 玩家的 FFLogs 战绩。

        Args:
            character_name(string): 玩家角色名，例如“冰冷”
            server_name(string): 玩家所在服务器名，例如“白银乡”
        """
        await event.send(
            event.plain_result(
                f"🔍 正在检索 {character_name}@{server_name} 的战绩...",
            ),
        )
        result = await self._query_fflogs(character_name, server_name)
        await event.send(event.plain_result(result))
        return "查询已经完成，结果已直接发送给用户。"

    @filter.command("ff14")
    async def cmd_ff14_price(self, event: AstrMessageEvent, item_name: str):
        """查询 FF14 国服各大区最低物价。用法：/ff14 物品名"""
        yield event.plain_result(f"🔍 正在查询物品 [{item_name}]...")
        result = await self._run_query(
            "查询物价",
            lambda: self.ffxiv.query_prices(item_name),
            "❌ 物价查询失败，请稍后重试或检查插件日志。",
        )
        yield event.plain_result(result)

    @filter.command("ff14status")
    async def cmd_ff14_status(self, event: AstrMessageEvent):
        """查询 FF14 国服服务器状态。用法：/ff14status"""
        yield event.plain_result("🔍 正在获取国服服务器状态...")
        result = await self._run_query(
            "查询服务器状态",
            self.ffxiv.query_server_status,
            "❌ 获取服务器状态失败，请稍后重试。",
        )
        yield event.plain_result(result)

    @filter.command("ff14news")
    async def cmd_ff14_news(self, event: AstrMessageEvent):
        """查询 FF14 国服官网最新新闻。用法：/ff14news"""
        yield event.plain_result("🔍 正在获取官方最新情报...")
        result = await self._run_query(
            "查询官方新闻",
            self.ffxiv.query_news,
            "❌ 获取官方新闻失败，请稍后重试。",
        )
        yield event.plain_result(result)

    @filter.command("ff14maint")
    async def cmd_ff14_maintenance(self, event: AstrMessageEvent):
        """查询 FF14 国服维护公告。用法：/ff14maint"""
        yield event.plain_result("🔍 正在获取官方维护公告...")
        result = await self._run_query(
            "查询维护公告",
            self.ffxiv.query_maintenance,
            "❌ 获取维护公告失败，请稍后重试。",
        )
        yield event.plain_result(result)
