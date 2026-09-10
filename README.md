# FF14 助手

面向 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 FF14 国服综合助手。本项目在原 `astrbot_plugin_fflogs` 上升级，保留仓库名、插件 ID、作者字段、FFLogs 配置和旧指令兼容。

> 当前版本为 `2.1.0`。2.1.0 保留 `/ff14 wiki` 和 `search_ff14_wiki` 的公开名称，但底层资料源已迁移为 FFCafe XIVAPI v2，不再访问灰机 Wiki。

## 功能

- FFLogs：查询 6.x、7.x 零式和 3.x 至 7.x 绝境战记录，并保留 `search_fflogs` LLM Tool。
- 国服信息：Universalis 国服四大区物价、服务器状态、官网新闻和维护公告。
- 统一资料查询：通过 `/ff14 wiki` 查询物品、装备、收藏、成就、主线任务、限时采集物和天气鱼；底层使用 FFCafe XIVAPI v2，唯一结果直接回复，不确定时返回最多 5 个类型候选，并保留 `search_ff14_wiki` LLM Tool。
- 版本与活动：查询国服版本公告、当前及未来 30 天限时活动。
- 本地时间功能：ET 窗口、海钓航班、PvP 轮换和综合日历。
- 时尚评鉴：获取本周主题、已确认的 80 分方案和参考图；未确认的数据会明确标注。
- 主动提醒：可按后台选择订阅日常、周常、海钓、采集、天气鱼、PvP 和活动提醒。

## 指令

```text
/ff14 help
/ff14 logs <角色> <服务器>
/ff14 price <物品>
/ff14 status
/ff14 news
/ff14 maint
/ff14 wiki <关键词>
/ff14 wiki #<编号>
/ff14 patch [版本]
/ff14 ocean [路线/成就鱼]
/ff14 fashion
/ff14 pvp
/ff14 events
/ff14 calendar
/ff14 subscribe
/ff14 unsubscribe
```

兼容指令：`/fflogs`、`/ff14 <物品>`、`/ff14status`、`/ff14news`、`/ff14maint`、`/ff14helps`。

已删除的独立 `collect`、`achievement`、`msq`、`gather`、`fish` 不会再注册；它们统一从 `/ff14 wiki` 入口识别。没有注册 `/ff14 todo`。

## 配置

插件设置使用 `_conf_schema.json`，主要分为：

- `feature_switches`：单独开关 logs、price、status、news、maint、wiki、patch、ocean、fashion、pvp、events、calendar 和资料查询 LLM Tool。为兼容旧配置，资料查询仍使用 `wiki` 与 `wiki_llm_tool` 键；查询类功能默认开启。
- `calendar_sections`：日常、周常、PvP、活动四个日历栏目多选。
- `daily_items` / `weekly_items`：日常与周常清单多选，仅影响日历内容，不实现勾选或自定义任务。
- `event_categories`：季节、联动、奖励型运营活动多选。
- `reminder_types`：提醒类型多选，默认空列表，升级后不会自动开始推送。
- `reminder_targets`：使用 `template_list` 添加海钓路线/成就鱼、限时采集物和天气鱼目标，填写名称、ET 窗口、区域、提前分钟数和启用状态。

群聊执行 `subscribe` / `unsubscribe` 需要群管理员权限；私聊用户可以管理自己的会话。订阅只保存当前 `unified_msg_origin`，内容取决于后台提醒设置。事件 ID、订阅和资料候选使用 AstrBot KV 持久化；候选按平台、群/私聊和发送者隔离保存 5 分钟，插件进程内还有短暂的内存兜底。调度器每分钟检查一次，重载或卸载时会取消任务。

FFLogs 查询仍需在 [FFLogs API Clients](https://www.fflogs.com/api/clients/) 创建 V2 客户端并填写 `client_id` 与 `client_secret`。`client_secret` 在配置界面中以敏感字段显示。`proxy_url` 仍适用于所有外部 HTTP 请求。

## 数据来源与缓存

- `/ff14 wiki` 的结构化游戏资料来自 [FFCafe XIVAPI v2](https://xivapi-v2.xivcdn.com/zh-cn/docs/welcome/)，默认使用简体中文 `chs`。公开指令和 LLM Tool 名称保持不变，但运行时不再请求灰机 Wiki。
- XIVAPI 请求只读取必要字段，关系字段使用原始 ID，避免递归展开产生超大响应。搜索和详情按响应中的数据版本缓存；网络失败时优先返回标注缓存时间的旧结果，无缓存时明确提示“FF14 资料服务暂不可用”，不会把用户输入伪装成候选。
- 官方新闻、版本和活动使用国服官网新闻接口；时间未确认、包含 `??` 或“待定”的活动不会进入提醒队列。
- 主线任务图由 XIVAPI `Quest` 数据在运行时构建并缓存于 `data/plugin_data/astrbot_plugin_fflogs/`，不会提交大型数据文件。只纳入经过验证的国服主线链，不把支线任务混入分母。
- PvP 轮换使用版本化参考时间、间隔和地图顺序，管理员可以在 `pvp_rotation` 中覆盖；算法设计参考 [ffxiv-wakeng/pvp-calendar](https://github.com/ffxiv-wakeng/pvp-calendar)，没有复制其代码或资源。
- 时尚评鉴使用公开的社区确认页面。数据源没有确认主题或 80 分方案时，插件不会猜测，并且不会据此创建提醒。
- 生成的日历/PvP 图片、XIVAPI 缓存、资料索引和 PvP 地图缩略图都位于 AstrBot 插件数据目录，不写入插件源码目录；PvP 周历把缓存图片嵌入最终 PNG，渲染器无法访问外部图片时也不会出现破图标。

XIVAPI 提供的是游戏客户端静态数据，不等同于社区百科。无法从结构化数据确认获取攻略、鱼饵链、直感或限时条件时，插件会写明“游戏数据不完整”，不会自行推测；未加载经过确认的 WeatherRate 数据时不会创建天气鱼提醒。FFCafe 版暂不提供资源文件接口，因此资料查询不依赖其图片资源。

### 主线进度口径

主线任务结果同时显示两个层级的进度，二者都基于任务在主线图中的顺序，不表示玩家角色实际完成度：

- **大版本进度**：当前任务在所属资料片完整主线中的位置，例如整个 `5.x`。显示当前序号、该资料片主线总数、百分比和大版本剩余任务量。
- **小版本进度**：当前任务在具体补丁章节中的位置，例如 `5.0`、`5.1`。显示当前序号、该章节主线总数、百分比和小版本剩余任务量。

输出格式示例（序号由运行时任务图计算）：

```text
所属版本：5.0 暗影之逆焰
大版本进度：5.x 约第 n/N 条（P%）；当前版本主线约剩 N-n 条。
小版本进度：5.0 内约第 m/M 条（Q%）；约剩 M-m 条。
主线约第 k/K 条（R%）；按已知主线终点估算总序约剩 K-k 条。
数据来源：XIVAPI v2；百分比表示任务顺序位置，不表示角色实际完成度。
```

如果无法确认资料片边界、补丁章节或任务链完整性，只显示已确认的版本与任务信息，并明确写明“主线进度数据不完整”，不计算百分比。

## 兼容与升级

版本 2.1.0 继续要求 Python 3.10+、AstrBot `>=4.17,<5`。从 2.0.0 升级时会保留已有 `client_id`、`client_secret`、`proxy_url`、`news_count`、`show_low_impact_maintenance`、功能开关和订阅设置。`wiki` 与 `wiki_llm_tool` 配置键继续有效，只更换底层数据源。提醒类型仍默认空列表；若希望接收主动消息，需要在后台配置提醒类型、目标并执行 `/ff14 subscribe`。

插件不导入或依赖 `astrbot_plugin_help`；所有公开 handler 都有命令 docstring，AstrBot 自带帮助系统可以自动收录。

## 开发检查

```bash
python -m compileall -q .
ruff check .
pytest -q
```

## 许可证

本项目采用 [GPL-3.0](LICENSE) 许可证。资料查询通过公开 HTTP API 使用 FFCafe XIVAPI v2，不复制其 AGPL 服务端代码；PvP 轮换参考项目的 MIT 许可仅作为设计参考。
