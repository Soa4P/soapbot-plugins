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

## 第二批改编（2026-11-12，六个新参考仓库）

| 参考仓库 | 一句话 | 状态 | 改编落点 |
|---|---|---|---|
| [thawline/dsh-memewiki](https://github.com/thawline/dsh-memewiki) | 梗百科：预查注入 + 6792 条离线词库 | ✅ `meme-wiki` 0.1.0 | 命令式查询 + 双向匹配 + 同步（cnmeme.wiki 公开 API）。**预查注入列待宿主能力**——message 钩子只读，回复管线注入点插件够不到；反馈上传不做（涉及第三方账号凭证） |
| [menglimi/astrbot_plugin_private_companion](https://github.com/menglimi/astrbot_plugin_private_companion) | 一站式拟人化陪伴套件（作息/记忆/主动联系/七能力） | ⛔ 整体 / ✅ 思想并入 | 本体级巨型套件，soapbot 已有对应积木：温度模型（宿主）、auto-chat（主动开口）、审批总线。**作息感知思想并入 auto-chat 0.2.0**（深夜不打扰 + 晨间语气），拆解路线见下 |
| [mearihaka/dsh-longing](https://github.com/mearihaka/dsh-longing) | 暗涌：七维数值状态机渲染内心状态 | ⛔ 状态机 / 🚧 设计输入 | #53 已定立场：soapbot 温度由宿主管线确定性计算、不由对话推动任意值。但它的**事件→维度映射表 + 每维独立半衰与日常值回归**是温度模型 v2 的直接设计输入（warmth 三参数可扩展负向事件与日常回归），记录进 docs/qq-autonomy-design.md |
| [Yuuyuko-uu/dsh-xingxi](https://github.com/Yuuyuko-uu/dsh-xingxi) | 记忆星图可视化 + 档案检索 | 🚧 | 检索已有（/recall、timeline）；星图可视化是面板前端工作（本体），等前端排期；档案整理（拾星）可改编为 kb/memory 写入插件——但写入能力未开放，先记 |
| [kukemc/bili-cli](https://github.com/kukemc/bili-cli) | UP 主运营全套（发动态/评论/私信/数据） | 🚧 需写面 | soapbot 的 bilibili 能力目前只读（GET）。写动作（发动态/评论）按 qzone 模式：动作白名单 + SESSDATA 宿主注入 + 独立审批 + 显式确认 + 风险自负。等操作者确认接受写风险后落地 `bili-poster` |
| [Mieksido/surfcore](https://github.com/Mieksido/surfcore) | DSH 完整身份框架 | ⛔ | 维持 #1 批评估：治理机制已吸收进宿主 |

**private_companion 拆解路线**（不整体改编）：连续会话与记忆=宿主已有；主动联系=auto-chat；表情包=sticker-album；生活作息=auto-chat 0.2.0；创作/生图/识屏/游戏=对应宿主能力面（screen 见 autonomous-capabilities-design.md），逐个成熟逐个拆。

## 第三批改编（2026-11-13，六个参考仓库）

| 参考仓库 | 一句话 | 状态 | 改编落点 |
|---|---|---|---|
| [InariNGT-Ofiicial/dsh-bili-humanism](https://github.com/InariNGT-Ofiicial/dsh-bili-humanism) | B 站字幕学习（WBI 签名/ASR/抽帧） | ✅ 字幕语料 | 第一批的阻塞已解除：宿主 `bilibili.subtitle_text`（wbi 签名 + hdslb 抓取，#52 面）落地，`fishtalk-lite` 0.2.0 的 collect 升级为字幕正文语料（无字幕退回简介）。ASR 与抽帧仍不改编——本地 Whisper 与画面抓取超出能力面 |
| [Nozzi2455/fishtalk](https://github.com/Nozzi2455/fishtalk) | 学真人说话、拦八股 | ✅ 升级 0.2.0 | collect 从标题简介升级为字幕语料；check/forget 不变 |
| [Alterssss/dsh-bili-whale](https://github.com/Alterssss/dsh-bili-whale) | 有日子的鱼：生物钟+偏好+克制 | ✅ 并入 `bili-scroll` 0.2.0 | 作息窗口（21 点后抓取）、撞风控半小时退避、手动刷轮不受限。whale 的写动作（评论/私信）不改编——零写入立场 |
| [CatisAkaLi/dsh-diary-relation-check](https://github.com/CatisAkaLi/dsh-diary-relation-check) | 记忆更新后触发关系复盘（分层抽读年1/月3/周4/日7） | 🚧 | 「模型判断」需要宿主定时语义任务能力（插件无模型面）；分层抽读是好的预算模板，登记为温度 v2/定时语义任务设计输入 |
| [wonderandyou/dsh-grow](https://github.com/wonderandyou/dsh-grow) | 亲近/信任双维仪表盘 + 自动情绪记忆 | 🚧 | 多维温度（亲近与信任分离）是温度 v2 的第二个设计输入；「自动记忆只管情绪、技术细节归工程记忆」的分工立场与本仓库 memory 分层一致，无需改编 |
| [Yuuyuko-uu/dsh-fish-buddy](https://github.com/Yuuyuko-uu/dsh-fish-buddy) | 桌面挂件 + 本机开口转发会话 | ⛔ | 已有等价积木：桌面壳（Tauri）、多端对话（web/mobile）、send_media（图片进会话）；本机开口需鉴权（文件总线式无鉴权通道此前已 ⛔） |

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
