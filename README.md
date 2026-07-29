# AstrBot FFLogs 查询插件

面向 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 的 FF14 国服信息插件，支持查询 FFLogs 战绩、Universalis 物价、服务器状态、官方新闻和维护公告。

![FFLogs 查询效果](https://raw.githubusercontent.com/674537331/astrbot_plugin_fflogs/master/docs/1.jpg)

## 功能

- 查询 6.x、7.x 零式战绩。
- 查询 3.x 至 7.x 绝境战战绩，包括绝伊甸和绝妖星乱舞。
- 通过 AstrBot LLM Tool 以自然语言查询 FFLogs。
- 查询国服四大区的最低物价。
- 查询国服服务器运行、转入、转出、新角色创建和优待状态。
- 查询国服官网最新新闻及正在进行或已预定的维护公告。
- 可为所有外部请求配置 HTTP/HTTPS 代理。

![物价查询效果](https://raw.githubusercontent.com/674537331/astrbot_plugin_fflogs/master/docs/2.png)

## 安装

在 AstrBot 插件管理页面选择“安装远程插件”，输入：

```text
https://github.com/674537331/astrbot_plugin_fflogs
```

本版本要求 AstrBot `>=4.16,<5`。

## 配置

如需使用 FFLogs 查询，请先前往 [FFLogs API Clients](https://www.fflogs.com/api/clients/) 创建 V2 客户端，然后在插件设置中填写：

- `client_id`：FFLogs Client ID。
- `client_secret`：FFLogs Client Secret。
- `proxy_url`：可选代理，例如 `http://127.0.0.1:7890`。
- `news_count`：`/ff14news` 返回条数，范围 1–20。
- `show_low_impact_maintenance`：是否显示一般或低影响维护公告。

物价、服务器状态和官网公告查询不需要 FFLogs 凭据。

## 命令

```text
/fflogs 角色名 服务器名
/ff14 物品名
/ff14status
/ff14news
/ff14maint
```

也可以让大模型调用 `search_fflogs` 工具，例如：“帮我查询白银乡某人的 FFLogs”。

## 开发与检查

```bash
python -m compileall -q .
ruff check .
pytest -q
```

项目更新记录见 [CHANGELOG.md](CHANGELOG.md)。

## 许可证

本项目采用 [AGPL-3.0](LICENSE) 许可证。
