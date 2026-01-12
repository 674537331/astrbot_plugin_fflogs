# AstrBot FFLogs 查询插件
一个为 AstrBot [<sup>1</sup>](https://github.com/Soulter/AstrBot) 设计的 Final Fantasy XIV 战绩查询插件。支持通过 FFLogs API 获取 5.0 - 7.0 版本的绝本与零式副本数据。
 ![<sup>1</sup>](https://github.com/674537331/astrbot_plugin_fflogs/blob/master/docs/1.jpg)
## ✨ 功能特性
- **多版本支持**：涵盖 5.0 漆黑的反叛者、6.0 晓月之终途及 7.0 最新版本。
- **全副本覆盖**：支持绝境战、万魔殿、阿卡狄亚等零式副本。
- **配置简单**：直接在 AstrBot 管理面板填写 FFLogs API Key，无需修改代码。
- **异步请求**：基于 `httpx`，查询流畅不阻塞。
## 🚀 安装方法
1. 在 AstrBot 的插件管理面板中，点击“安装远程插件”。
2. 输入本仓库地址：`https://github.com/674537331/astrbot_plugin_fflogs`
3. 等待安装完成后重启 AstrBot。
## ⚙️ 配置说明
在使用前，请先前往 FFLogs V2 客户端页面 [<sup>2</sup>](https://www.fflogs.com/api/clients/) 获取你的 `Client ID` 和 `Client Secret`。
1. 进入 AstrBot 管理面板 -> 插件设置。
2. 找到 **FFLogs 查询插件**。
3. 填入你的 `Client ID` 和 `Client Secret`。
4. 保存配置。
## 📖 使用方法
在聊天框输入：
`/fflogs [角色名] [服务器名]`
**示例：**
`/fflogs 艾默里克 摩杜纳`
---
本插件完全基于Gemini-3，仅在Astrbot v4.11.1下进行过测试，作者本人仅会HELLOWORLE, 对于适配兼容性等问题无能为力.
感谢使用！如有 Bug 或者功能性建议欢迎联系联系作者QQ674537331。
