# 插件改编清单：dsh 生态 → soapbot 能力面

本文回答：用户圈定的参考仓库，各自改编成什么、改编时砍了什么、为什么。改编的
总原则只有一条——**运行时不照搬，功能对照改编**：dsh 插件跑在宿主进程内（JS
bundle），soapbot 插件跑在 `python -I` 子进程里、每个能力都要过宿主的 capability
白名单。凡是 soapbot 能力面给不了的东西（本地 Whisper、屏幕截取、回复管线拦截），
宁可列为「待宿主能力」，也不在插件里绕边界。

> 状态标记：✅ 已落 `packages/`（digest 占位，发版后回填）；🚧 待宿主能力，先有设计；
> ⛔ 评估后不改编，理由写明。

| 参考仓库 | 一句话 | 状态 | 改编落点 |
|---|---|---|---|
| [not-zai/self-nudge](https://github.com/not-zai/self-nudge) | 给未来的自己留言，到点开口 | ✅ `self-nudge` | cron(分钟 tick) + storage(队列) + send(只投发起会话)；措辞原则（像想起了，不是闹钟）保留 |
| [not-zai/weather-watch](https://github.com/not-zai/weather-watch) | 气象预警 → 待说队列 | ✅ `weather-alert` | http(两个公开端点) + cron(小时) + storage(去重) + send；原版分三档（区/市/周边），v1 只按省——findAlarm 返回无稳定分区字段，不硬编 |
| [c1overzz/dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll) | 每天替你刷 B 站、写报告、@你 | ✅ `bili-scroll` | bilibili(热门列表) + cron(每日 09:00) + storage(去重) + send；**只读**：不点赞不评论——原版的「留评论 @你」改成 send 给操作者会话；字幕深读待 WBI 能力（见下） |
| [Nozzi2455/fishtalk](https://github.com/Nozzi2455/fishtalk) | 学真人说话、拦八股 | ✅ `fishtalk-lite` | bilibili(元数据语料) + storage(样本≤300)；**check 做成命令不是闸门**——插件的 message hook 只读不拦，接回复管线是宿主侧工作；词表硬编码（判断标准悄悄可改=结论悄悄可改） |
| [InariNGT-Ofiicial/dsh-bili-humanism](https://github.com/InariNGT-Ofiicial/dsh-bili-humanism) | B 站字幕学习（WBI 签名、ASR、抽帧） | 🚧 | 字幕轨需要 WBI 签名的 `/x/player/wbi/v2`——签名钥匙属于凭据，应与 SESSDATA 一样宿主持有；正确形态是给 `bilibili` 能力加「取字幕」专用方法（签名在宿主内完成），而不是插件自己实现签名。列为下一个宿主能力项 |
| [not-zai/senses-bridge](https://github.com/not-zai/senses-bridge) | 文件夹当队列的进程间总线 | ⛔ | soapbot 已有三个更受控的跨进程通道：面板 API（鉴权+CSRF）、插件 IPC（capability 白名单）、`send` 面（observed-session 白名单）。文件总线绕过全部三道审计，「不占端口不要鉴权」在 soapbot 的威胁模型里是负分 |
| [SAOS-3624/dsh-see-now](https://github.com/SAOS-3624/dsh-see-now) | 实时看屏幕（变化帧筛选→拼图） | 🚧 | 这是「自助玩游戏」的前置能力：需要宿主新能力面 `screen`（截屏由宿主执行、帧数/频率配额、画面只进模型上下文不落盘），插件只声明它。设计要点见 `docs/` 中的 autonomous-surfaces 设计（下一批提交） |
| [Mieksido/surfcore](https://github.com/Mieksido/surfcore) | DSH 的完整身份/记忆/作息框架 | ⛔ | 本体级框架，且其治理机制已按 `docs/reference-dsh-ecosystem.md` 的评估吸收进宿主（rid 关联、帧预算、fail-closed 工具面、发布门禁）。桌宠/Windows ACL 形态不适配 |
| [mashanglinyu/luanyujia-workbench](https://github.com/mashanglinyu/luanyujia-workbench) | 多场景工作台：宪法/场景/记忆模板 | 🚧 | 产物是 Markdown 模板与组织方法，不是代码——改编形态是「kb_read + 命令」的场景模板插件，模板正文放进 packages/ 的纯文本。用户注明「源码照搬全量更新！」：**照搬的是模板与结构，不照搬任何运行时**（其运行时是 dsh 配置脚本，直接搬会把宿主语义带进来）。待下一批 |

## 逐项边界核对（已落地的四个）

每个包的 README 都写了「读了什么/发到哪里/留不留副本」；这里是边界层面的硬核对：

1. **出站目的地**：`weather-alert` 只碰 nmc.cn 与 open-meteo；`bili-scroll` 与
   `fishtalk-lite` 只碰 api.bilibili.com（经 `bilibili` 能力，宿主钉死）。没有任何
   插件请求「任意 URL」的自由——这是 `http` 能力与 `bilibili` 能力的差别。
2. **凭据**：`bilibili` 能力的 Cookie 由宿主在请求时注入，四个包的**可执行源码**里
   `Cookie`/`SESSDATA` 零命中（唯一的字样是两个 manifest 说明里告诉操作者去配
   `SOAPBOT_BILI_COOKIE` 这个名字——配置项名称不是凭据本身）。
3. **send 的收窄**：三个用 send 的包都把目标收窄到「发起命令的那个会话」，而不是
   宿主允许的 observed-session 全集。
4. **无模型调用**：四个包都不含「跑一次模型」的路径——判断全部是确定性规则。
   要模型判断，走 persona/管线，不走插件后门。
5. **写入为零**：没有任何包向 B 站或气象台写数据；插件对外的全部输出是命令返回
   与（配置后）send。

## 与宿主的协议

- 版本 0.1.0 的四个包都声明 `min_host_version: 0.1.0`；`bili-*` 两个包需要宿主带
  `bilibili` 能力（main 仓库 #41 之后）。
- 发版流程见 VERSIONING.md：打 zip → GitHub Release（`<name>-<version>`）→ 把真实
  SHA-256 回填 `plugin_versions.json`。在回填之前，这些索引条目是**草稿**（占位
  digest），安装器会拒绝占位 digest 的版本。
