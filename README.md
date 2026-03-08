# AstrBot FFLogs 查询插件
一个为 AstrBot [<sup>1</sup>](https://github.com/Soulter/AstrBot) 设计的 Final Fantasy XIV logs和universalis物价查询功能查询插件。支持通过 FFLogs API 获取 5.0 - 7.0 版本的绝本与零式副本数据。
![效果图](https://raw.githubusercontent.com/674537331/astrbot_plugin_fflogs/master/docs/1.jpg)

<div align="center">

[![Moe Counter](https://count.getloli.com/get/@astrbot_plugin_fflogs?theme=moebooru)](https://github.com/674537331/astrabot_plugin_discogs)

</div>

 ## 🚀 v1.4.0 更新内容

本次更新带来了重磅的 **物价查询功能** 以及更智能的 **自然语言交互** 支持！

### ✨ 新特性 (New Features)

* **新增 Universalis 市场物价查询**
  * 新增指令：`/ff14 [物品名]` (例如：`/ff14 小牛皮骑手手套`)。
  * **跨大区比价**：自动将物品名称转化为 ID，并**并发请求**国服四大区（陆行鸟、莫古力、猫小胖、豆豆柴）的市场数据。
  * **精准定位**：直观展示四大区的最低售价，并精确标注该最低价所在的**小服务器**（如：`900000 金币 @ 伊修加德`）及是否为 HQ 品质，助你光速跨服扫货。
  * *注：该功能基于公用的 Universalis API 与国服 XIVAPI 构建，开箱即用，无需额外配置 Token！*
 ![效果图](https://raw.githubusercontent.com/674537331/astrbot_plugin_fflogs/master/docs/2.png)

* **支持 LLM 自然语言调用 FFLogs (Agent Tool)**
  * 插件现已将 FFLogs 查询能力注册为 AstrBot 的大模型工具（Tool）。
  * 告别繁琐的指令格式！你现在可以直接对机器人说：**“帮我查一下白银乡扁扁走开的logs”**，大模型会自动提取区服与角色名并调用查询，体验更加丝滑拟人。

### 🛠️ 优化与重构 (Improvements)
* 重构了 FFLogs 的底层请求逻辑，将核心逻辑与指令触发解耦，大幅提升了代码的复用性与稳定性。
* 优化了异步并发策略，在物价查询时同时向四个大区发起请求，极大缩短了用户的等待时间。

* 
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

**在聊天框输入：**
- `/fflogs [角色名] [服务器名]`
- `/ff14 [物品名]`
- 直接说：“帮我查一下白银乡某人的logs”

**示例：**
- `/fflogs 艾默里克 摩杜纳`
- `/ff14 小牛皮骑手手套`
---
## 🤝 致谢

* Gemini3.1 Pro完成代码构建


## 📄 许可证

本项目采用 **AGPL-3.0 License** 开源协议 - 详见 [LICENSE](LICENSE) 文件。

---

如果您发现这个插件对您有所帮助，请给一个 ⭐ **Star** 以示支持！
