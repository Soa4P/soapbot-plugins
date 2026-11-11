# self-nudge（soapbot 插件）

改编自 [not-zai/self-nudge](https://github.com/not-zai/self-nudge)（MIT）。
原版是 Node daemon 读 JSON 文件；soapbot 版把钟交给宿主的 cron、状态交给插件加密
storage、投递交给 `send` 能力面——三样都是宿主受审的边界，插件只决定「约什么、几点、说什么」。

## 命令

| 命令 | 作用 |
|---|---|
| `/nudge <30m\|2h\|HH:MM> <话>` | 约一件事，到点在这个会话说出来 |
| `/nudge-list` | 看还挂着的约 |
| `/nudge-clear` | 全清（删了找不回来） |

## 能力与边界

| 能力 | 用途 | 收窄 |
|---|---|---|
| `command` | 三个命令 | — |
| `storage` | nudge 列表（加密） | 只存时间/内容/目标会话 |
| `cron` | 每分钟 tick | — |
| `send` | 到点投递 | 只发往**发起命令的那个会话**（比宿主的 observed-session 白名单更窄） |

投递措辞默认「我想起一件事：……」——照原版的立场：读起来是自己想起来了，不是闹钟响了。
投递失败不删条目，下个 tick 重试；发不出（会话失效）的条目会一直挂着，`/nudge-clear` 可清。

## 安装

zip 根目录一个 `plugin.yaml`：`soapbot plugins install ./self-nudge-0.1.0.zip`，
逐项勾选上述四个能力后生效。
