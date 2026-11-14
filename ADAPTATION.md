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

## 第五批改编（2026-11-15，六个参考仓库）

| 参考仓库 | 一句话 | 状态 | 改编落点 |
|---|---|---|---|
| [menglimi/astrbot_plugin_private_companion](https://github.com/menglimi/astrbot_plugin_private_companion) | 一站式拟人陪伴（生活状态/日程/主动联系/关系网/七能力） | ⛔ 整体 / ✅ 思想三处并入 | 本体级套件的积木 soapbot 全都有宿主对应物（温度、多端对话、审批、screen 面）。本轮吸收三件：①「话题问过即退」并入 auto-chat 0.4.0（未完话头 48h 语义复核的确定性版：插件无模型面，用问过即退 + 片段池换血实现「不机械追问」）；②「每日反思提取互动纪念日」并入新包 mood-diary 0.1.0（周年主动一句，年份标记防重）；③「固定拟人策略合并单条 system 保 Prompt Cache」与主仓库 #70 增量折叠同源，登记为宿主 prompt-cache 设计输入 |
| [CixiSVesper/dsh-soundtry](https://github.com/CixiSVesper/dsh-soundtry) | 本地音频理解（转写/BPM/和弦，faster-whisper + onnx，零 torch） | ⛔ | 宿主已有 transcribe 槽（语音消息走 Provider）；插件自带 2.4GB 依赖栈（隔离 venv + 模型下载）违背安装器轻量哲学与「六类攻击防护」的审查面。吸收两条原则进改编纪律：①诚实性输出（BPM ambiguous/level/conflict 三态、置信度自报——本批 memory-weekly「召回无时间戳就不编造时间感」即此原则的落法）；②依赖 doctor 自检模式（若将来有重型包，先出 doctor 再出功能） |
| [Yuuyuko-uu/dsh-qq-link](https://github.com/Yuuyuko-uu/dsh-qq-link) | DSH ↔ QQ 双向桥（OneBot HTTP 轮询/富消息转写/戳一戳） | ⛔ | soapbot 本体就是 QQ 适配器，桥是整个产品不是插件。三条宿主设计输入登记：①戳一戳（notice 事件）进管线供插件钩子；②B 站分享卡片提取 BV 喂 bilibili 面；③「点名后接着听 N 条」的注意力窗口。富消息转写（合并转发展开/表情占位映射）属消息内容规范化，宿主适配器职责 |
| [Yuuyuko-uu/dsh-feiyu-mood](https://github.com/Yuuyuko-uu/dsh-feiyu-mood) | 模型每轮判心情（七根情绪 + 注入 systemPrompt.context） | ⛔ 注入 / ✅ 日记式改编 | 两条立场冲突：①模型写入的持久状态是 #53 已拒绝的形状（写入权只在宿主管线）；②每轮 prompt 注入破坏「插件状态不进 prompt 权威」边界。改编落点是新包 **mood-diary 0.1.0**：心情是**事后日记**（每晚由当日交互统计确定性分档），存插件 storage、/mood 查看、不注入。与宿主温度互补：温度管关系亲疏，日记管当日热闹程度。feiyu 的「分口径判断（user/speak-up/cron）/ 可信度门槛 / 半衰回归」是好设计，若宿主将来开放定时语义任务能力可升级为模型判日记 |
| [Yuuyuko-uu/dsh-speak-up](https://github.com/Yuuyuko-uu/dsh-speak-up) | 本地掷骰主动搭话（峰谷命中线 + 连败补偿 + 演练） | ✅ 并入 `auto-chat` 0.4.0 | 掷骰零 token 解决「温度达标就必开口」的机械感；峰谷表（工作日 9-12/14-18 峰时 60%、其余 20%）、连败补偿 +10 上限 +40、命中清零，全部写死。dsh 用 agent.followup，本仓库没有「代理一轮」的插件面——开口仍是模板 + 话题片段。**演练命令 /autochat-dry**（与真发同源、不发送不推进计数）照搬其「演练一次绝不真发」 |
| [Yuuyuko-uu/dsh-fish-memory](https://github.com/Yuuyuko-uu/dsh-fish-memory) | 会淡忘的长期记忆（半衰期/证据召回/事实键/每日配额） | ⛔ 插件版 / 🚧 宿主设计输入 / ✅ 回顾改编 | 召回权在宿主：插件只有 memory.recall（无全量列举/按时间过滤），淡忘半衰期与事实键 latest-wins 做不了——登记为宿主记忆层设计输入（L2 淡忘半衰期按重要度分档、事实键「对象.方面」latest-wins、每日提炼配额）。插件侧改编为新包 **memory-weekly 0.1.0**：/memweek 用本周话题逐个召回、按记忆 id 去重产出「这周聊到的、还记得的事」；召回无时间戳就不编造时间感（soundtry 诚实性原则），时间感只来自钩子攒话题的时间戳 |

## 第五批边界核对

1. **出站目的地**：本批三个包零新增出站——auto-chat/mood-diary 只经 `send` 投回
   显式开启的会话，memory-weekly 只走宿主 `memory.recall`（无网络）。soundtry 的
   模型下载 URL 不改编即不存在。
2. **随机数**：auto-chat 0.4.0 的掷骰用 `random.randint`——刻意非密码学随机：
   它是「避免机械感」的心情骰子，不是密钥或防重凭据；峰谷表与连败参数写死。
3. **无模型调用**：三个包都不含模型路径（mood-diary 的心情是确定性分档，feiyu 的
   「模型判心情」需宿主定时语义任务能力，登记升级路径不偷跑）。
4. **无写入**：零写入立场不变（B 站、QQ 空间、第三方全部只读或不触）。
5. **cron 主动发言的收窄**：auto-chat 与 mood-diary 的 send 目标都是显式开启的
   会话白名单（/autochat-set、/mood-on），每日 ≤2/≤1 条，全局每轮各挑一个会话。

## 第六批改编（2026-11-15，六个参考仓库）

| 参考仓库 | 一句话 | 状态 | 改编落点 |
|---|---|---|---|
| [Yuuyuko-uu/dsh-tts-bridge](https://github.com/Yuuyuko-uu/dsh-tts-bridge) | 浏览器扩展替 DeepSeek 网页点「朗读」按钮 | ⛔ | soapbot 有真 TTS（edge-tts + voice_self_select 挑音色管线），不需要借网页端朗读；浏览器扩展模拟点击的形态与宿主能力面无关。设计输入登记：**每条回复的操作键（▶ 朗读 / ⏸ 停止）**可进多端对话 UI（网页/App 的消息气泡加「朗读」按钮，调宿主 TTS）——面板前端工作，待排期 |
| [c1overzz/dsh-bili-scroll](https://github.com/c1overzz/dsh-bili-scroll) | 每天 B 站值班：字幕原文判断 + 日报 + 可选自动评论 | ✅ 升级 `bili-scroll` 0.2.0 | 第一批登记的「字幕原文判断待字幕面」已解除（#52）。**判断基于字幕原文而非摘要**：标题初筛前 6 条取字幕原文加权（关注词命中 +2），无字幕/失败降级标题分（原仓库降级链字幕→抽帧→跳过，能力面没有画面抓取，降级终点如实为标题分）；日报改「段首固定 ### BV号 + 为什么值得」。**自动评论不改编**（零写入立场，第三批对 whale 已定）；章程 sha256 记账与本仓库无模型提示词无关；深度沙箱/最小工具集宿主已有（进程隔离 + 敏感能力审批） |
| [wonderandyou/dsh-grow](https://github.com/wonderandyou/dsh-grow) | 心智层：六参数人格漂移 + 关系四阶段 + 日记 + 隐私闸门（0.3.2） | ⛔ 插件版 / ✅ 设计输入扩充 | 第三批已登记亲近/信任分离；0.3.x 新增的四条进宿主设计：①**漂移算法四件套**（批量信号归一化而非按轮稀释、0.05 门限、0.06 步长、8 轮间隔——0.2.0 修复「乘法稀释 396 轮只动 0.0136」的教训直接适用于温度 v2 的参数演化）；②**阶段天花板落到数值层**（关系四阶段 cap 参数，渐进增量防撞 1.00——温度 v2 的 baseline/上限设计）；③**注入「这一轮该做到的行为」而非标签**（prompt 设计输入）；④**隐私闸门 19 条 PII 规则**（vet_memory 的扩充方向）。「面板只读（数字能改就失去意义）」与 session.view 立场一致 |
| [NoMindSama/qq-dsh-bridge](https://github.com/NoMindSama/qq-dsh-bridge) | DSH 全功能接入 QQ（五层链路/远程控制/四层记忆） | ⛔ 桥即本体 / ✅ 强设计输入 | 本体即适配器（多端对话 #67 已落地统一会话）。**最重要的一条：闲置边界压缩**——对话中阈值 0.95 永不压缩保 KV cache 命中、闲置 30 分钟才压缩（实测单步 45~55s → 3~4s）。#70 的增量折叠解决了「重复付费」，这条解决「压缩时机」：活跃会话不打断前缀缓存，折叠挪到闲置边界——登记为压缩管线的下一步（宿主）。次条：**L4 selfNote 仅新会话首次读**（「每轮注入自我描述等于洗脑而非成长」——本仓库 L4 self-notes 语义一致）；2 秒防抖合批（companion 防抖同源，第二处独立证据） |
| [kakamiku/dsh-qzone](https://github.com/kakamiku/dsh-qzone) | QQ 空间机器人：自动发说说/点赞/回评（大肥鱼人设） | ⛔ 自动发帖 / ✅ 新包 `qzone-composer` 0.1.0 | cron 自动发帖与 A12 三重确认（启用 + 审批 + 逐条 confirm）直接冲突，不改编。素材链路（素材 → 脱敏 → 写一句话 → 发布）改编为 **qzone-composer**：机器起草、人确认发布——cron 只产出草稿，/qzc-confirm 是三重确认的最后一道。脱敏 scrubber 采纳子集（QQ号/手机号/身份证/长数字串/路径/凭据形态，命中即弃）；**写失败不自动重试**与其风控实测（票据会掉、retcode=10027、只发经典说说）直接采纳为行为。chat 分桶素材不改编（把会话内容搬进公开空间，风险自担立场不变） |
| [icyaaaww/dsh-soul](https://github.com/icyaaaww/dsh-soul) | 结构化记忆地图（约束/决策/产物/进度节点 + 图记忆 + 受控演化影子试用） | ⛔ 插件版 / ✅ 设计输入 | 全部机制需要宿主记忆写入与模型路由（插件只有只读 recall）。三条进宿主记忆层设计：①**结构化节点抽取**（约束/决策/产物/进度四类，规则抽取零模型起步）与边类型（supersedes/contradicts——与 handoff 的「取代」语义同源）；②**runtime-context 快照不重新入图**（防自我污染——与 #70 增量折叠的「摘要不二次入图」同一条纪律）；③**受控演化的影子试用形态**（候选编辑 → 成对回放验证 → 门控 → 影子试用 → 接纳或回滚，全程审计）——若将来做人格自动演化，这是安全形态的参考，默认关闭立场不变 |

## 第六批边界核对

1. **出站目的地**：bili-scroll 只碰宿主钉死的 bilibili 域（既有）；qzone-composer
   只抓 http 允许列表里的 RSS（域名由操作者决定，插件列的兜底源只是「值得试」
   顺序，不是授权）——新增出站为零。
2. **XML 加固**：qzone-composer 的 RSS 是外部 XML，解析前拒 `<!DOCTYPE`/`<!ENTITY`
   并限 512KiB（标准库解析器不做实体扩展防护，安全扫描拦下后补的）。
3. **写操作的克制**：qzone-composer 是本批唯一带写能力的包——每日起草 ≤1、发布
   ≤1、写失败不自动重试、发布必须人工 confirm；「自动发帖」明确不改编。
4. **无模型调用**：三个包都不含模型路径（c1overzz 的模型判价值→字幕关键词计数；
   dsh-qzone 的按人设写文案→固定句式模板）。
5. **脱敏即弃**：qzone-composer 素材命中脱敏正则直接放弃（不是替换后照发）——
   宁缺毋滥，发布出去的话是拿操作者账号说的。
