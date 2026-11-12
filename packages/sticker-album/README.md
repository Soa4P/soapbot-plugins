# sticker-album（soapbot 插件）

机器人自己的表情包/照片相册：存、按标签找、发出去。发送依赖宿主的 `send_media`
能力（main 仓库 `bilibili`/#45 之后），与 send 同一套会话白名单、独立审批。

## 命令

| 命令 | 作用 |
|---|---|
| `/meme-save <URL> <标签...>` | 抓图入册（图床域名需在 http 允许列表） |
| `/meme-list [标签]` | 列出 |
| `/meme-send <标签> [说明]` | 发一张到当前会话 |
| `/meme-rm <编号>` | 删一张 |
| `/meme-forget 确认` | 全清 |

## 能力与边界

| 能力 | 用途 | 收窄 |
|---|---|---|
| `http` | 只 GET 图片 URL | 宿主白名单 + 禁重定向 + 2MiB 响应上限 |
| `storage` | base64 图 + 标签（≤100 张，加密） | 不出插件命名空间 |
| `send_media` | 发图 | ledger + 独立审批 + 4MiB 双边界上限 |

来源只有 URL：从聊天里顺手收藏需要消息钩子暴露图片段（宿主还没有这个面，
见 main 仓库 docs/qq-autonomy-design.md §5）。
