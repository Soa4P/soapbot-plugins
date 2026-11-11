# weather-alert（soapbot 插件）

改编自 [not-zai/weather-watch](https://github.com/not-zai/weather-watch)（MIT）。
原版分工保留：插件只负责**存**与**按队列播报**，说不说、怎么说由 agent/操作者决定。
预警原文来自中央气象台公开接口，当前天气来自 open-meteo——两个端点都无凭据、只读。

## 命令

| 命令 | 作用 |
|---|---|
| `/weather-set <省份> <纬度> <经度>` | 一次性配置；目标会话自动取当前会话 |
| `/weather` | 当前天气 + 新预警（看完即标记已说） |
| `/weather-pending` | 看未播报的预警 |
| `/weather-ack` | 把当前预警标记为已说 |
| `/weather-clear` | 清空去重记录 |

## 能力与边界

| 能力 | 用途 | 收窄 |
|---|---|---|
| `http` | 只 GET 两个公开端点（nmc.cn / open-meteo） | 无凭据；宿主沙箱另有目的地白名单与禁重定向 |
| `cron` | 每小时抓一次 | 无配置时跳过，不猜默认地点 |
| `storage` | 配置 + 已播报标题（上限 200） | 加密 storage，不出插件命名空间 |
| `send` | 新预警播到配置会话 | 只发往 `/weather-set` 时的那个会话 |
| `command` | 上表四个 | — |

与原版的差异：原版写本地 JSON 文件由外部 daemon 读；soapbot 版没有 daemon——cron
就是 tick，`send` 就是投递，队列就是 storage。三档分区（区/市/周边）在 findAlarm
的返回里没有稳定字段，v1 只按省抓取，分档留给后续版本。
