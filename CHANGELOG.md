# 更新日志

本文件记录项目的重要变更，版本号与 `metadata.yaml` 保持一致。

## 2.0.0 - 2026-09-09

### Added

- 新增 `/ff14` 统一路由、`/ff14 help`、`/ff14 wiki`、`/ff14 patch`、`/ff14 ocean`、`/ff14 fashion`、`/ff14 pvp`、`/ff14 events`、`/ff14 calendar`、`/ff14 subscribe` 和 `/ff14 unsubscribe`。
- 新增 `search_ff14_wiki` LLM Tool；返回结构化短结果和原文链接，不主动发送群消息。
- 新增 HuijiWiki 主/备用端点、重定向解析、短摘要缓存、403/429 退避和缓存降级。
- 新增国服 Quest.csv 运行时缓存、ET 时间窗口、海钓航班、版本化 PvP 轮换、时尚评鉴、活动筛选和 HTML 日历/PvP 图片。
- 新增基于 AstrBot KV 的候选词条、订阅和提醒去重记录，以及每分钟检查的统一调度器。

### Changed

- 展示名改为“FF14 助手”，版本为 `2.0.0`，AstrBot 要求调整为 `>=4.17,<5`。
- `_conf_schema.json` 改为嵌套配置和多选项；新增功能、日历、活动、提醒和提醒目标配置，FFLogs Client Secret 标记为敏感字段。
- Wiki、Quest.csv、缓存和生成图片全部使用 AstrBot 插件数据目录，不写入插件源码目录。
- 更新 README、内置帮助和数据来源说明；许可证说明统一为 GPL-3.0。

### Compatibility

- 保留 `/fflogs`、`/ff14 <物品>`、`/ff14status`、`/ff14news`、`/ff14maint` 和 `/ff14helps`。
- 保留原有 FFLogs 查询方式、作者字段、仓库名和插件 ID `astrbot_plugin_fflogs`。
- 独立 `collect`、`achievement`、`msq`、`gather`、`fish` 入口合并到 `/ff14 wiki`，不再注册；不新增 `/ff14 todo`。
- 2.0.0 的提醒类型默认空列表，升级不会意外推送；订阅目标仍需通过 `/ff14 subscribe` 绑定。

## v1.6.0 - 2026-07-30

### 新增

- 为 FFLogs 排名解析、维护时间解析、配置边界和服务器状态格式化添加测试。
- 添加 GitHub Actions，自动执行编译、Ruff 和 Pytest 检查。
- 在插件元数据中声明展示名称、简短描述和 AstrBot 版本范围。

### 变更

- 按 AstrBot 4.26.8 开发规范移除已废弃的 `@register` 装饰器，并使用 `AstrBotConfig`。
- 将 AstrBot handler、FFLogs 服务和 FF14 国服服务拆分，降低入口文件复杂度。
- 将维护公告详情改为限制并发查询，并统一使用中国标准时间解析公告。
- 更新 README，将版本记录迁移到本文件。

### 修复

- 修复 Ruff 报告的单行复合语句问题及其余格式问题。
- 检查 FFLogs GraphQL 错误并在令牌失效后自动刷新重试。
- 避免把上游异常或代理信息直接返回给聊天用户。
- 修正插件描述中的“物价查板”错字及过时的功能范围说明。

## v1.5.0 - 2026-06-04

### 新增

- 新增 `/ff14status` 国服服务器状态查询。
- 新增 `/ff14news` 国服官网新闻查询。
- 新增 `/ff14maint` 国服维护公告查询。
- 新增代理、新闻条数和维护公告显示范围配置。

### 变更

- 将停止维护的 Cafemaker 接口替换为 XIVAPI v2。

### 修复

- 更新 FFLogs 7.x zone 和 encounter ID，恢复旧绝本、绝伊甸及后续绝境战查询。
- 区分普通难度与零式难度，避免把普通难度记录误认为零式记录。

## v1.4.0

### 新增

- 新增 Universalis 国服四大区物价查询。
- 新增 `search_fflogs` LLM Tool，支持自然语言触发 FFLogs 查询。

### 变更

- 将 FFLogs 查询逻辑与命令 handler 解耦。
- 并发请求四个国服大区的物价数据。
