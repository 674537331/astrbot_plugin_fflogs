# FF14 助手

面向 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 FF14 国服综合助手。本项目在原 `astrbot_plugin_fflogs` 上升级，保留仓库名、插件 ID、作者字段、FFLogs 配置和旧指令兼容。

## 功能

- FFLogs：查询 6.x、7.x 零式和 3.x 至 7.x 绝境战记录，并保留 `search_fflogs` LLM Tool。
- 国服信息：Universalis 国服四大区物价、服务器状态、官网新闻和维护公告。
- 统一 Wiki：通过 `/ff14 wiki` 查询物品、装备、收藏、成就、主线任务、限时采集物和天气鱼；不确定时返回最多 5 个类型候选。
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

- `feature_switches`：单独开关 logs、price、status、news、maint、wiki、patch、ocean、fashion、pvp、events、calendar 和 Wiki LLM Tool。查询类功能默认开启。
- `calendar_sections`：日常、周常、PvP、活动四个日历栏目多选。
- `daily_items` / `weekly_items`：日常与周常清单多选，仅影响日历内容，不实现勾选或自定义任务。
- `event_categories`：季节、联动、奖励型运营活动多选。
- `reminder_types`：提醒类型多选。2.0.0 默认空列表，升级后不会自动开始推送。
- `reminder_targets`：使用 `template_list` 添加海钓路线/成就鱼、限时采集物和天气鱼目标，填写名称、ET 窗口、区域、提前分钟数和启用状态。

群聊执行 `subscribe` / `unsubscribe` 需要群管理员权限；私聊用户可以管理自己的会话。订阅只保存当前 `unified_msg_origin`，内容取决于后台提醒设置。事件 ID、订阅和 Wiki 候选使用 AstrBot KV 持久化；Wiki 候选按平台和群/用户会话保存 5 分钟，插件进程内还有短暂的内存兜底。调度器每分钟检查一次，重载或卸载时会取消任务。

FFLogs 查询仍需在 [FFLogs API Clients](https://www.fflogs.com/api/clients/) 创建 V2 客户端并填写 `client_id` 与 `client_secret`。`client_secret` 在配置界面中以敏感字段显示。`proxy_url` 仍适用于所有外部 HTTP 请求。

## 数据来源与缓存

- Wiki 主端点为 `ff14.huijiwiki.com/w/api.php`，备用端点为 `cdn.huijiwiki.com/ff14/api.php`。请求带描述性 User-Agent、超时、并发限制和 403/429 退避；失败时优先返回带缓存时间的旧结果。若 Wiki 被 Cloudflare 或网络代理拦截，运行时 Quest.csv（可自动下载并缓存）仍可补充主线任务进度，其他词条只返回候选标题和原文链接。
- 官方新闻、版本和活动使用国服官网新闻接口；时间未确认、包含 `??` 或“待定”的活动不会进入提醒队列。
- 主线任务图从国服数据仓库运行时下载 `Quest.csv`，缓存于 `data/plugin_data/astrbot_plugin_fflogs/`，不会提交大型数据文件。任务百分比表示数据图中的顺序位置，不表示账号实际完成度。
- PvP 轮换使用版本化参考时间、间隔和地图顺序，管理员可以在 `pvp_rotation` 中覆盖；算法设计参考 [ffxiv-wakeng/pvp-calendar](https://github.com/ffxiv-wakeng/pvp-calendar)，没有复制其代码或资源。
- 时尚评鉴使用公开的社区确认页面。数据源没有确认主题或 80 分方案时，插件不会猜测，并且不会据此创建提醒。
- 生成的日历/PvP 图片、Wiki缓存、PvP 地图缩略图和 Quest.csv 都位于 AstrBot 插件数据目录，不写入插件源码目录；PvP 周历把缓存图片嵌入最终 PNG，渲染器无法访问外部图片时也不会出现破图标。

Wiki 结果只保留短摘要、结构化字段和原文链接；Wiki 内容版权及署名遵守 CC BY-NC-SA 3.0，不批量复制或打包页面内容。

## 兼容与升级

版本 2.0.0 要求 Python 3.10+、AstrBot `>=4.17,<5`。升级时会保留已有 `client_id`、`client_secret`、`proxy_url`、`news_count` 和 `show_low_impact_maintenance`。新增提醒类型默认空列表；若希望接收主动消息，需要在后台配置提醒类型、目标并执行 `/ff14 subscribe`。

插件不导入或依赖 `astrbot_plugin_help`；所有公开 handler 都有命令 docstring，AstrBot 自带帮助系统可以自动收录。

## 开发检查

```bash
python -m compileall -q .
ruff check .
pytest -q
```

## 许可证

本项目采用 [GPL-3.0](LICENSE) 许可证。项目中未复制 AGPL 代码；PvP 轮换参考项目的 MIT 许可仅作为设计参考。
