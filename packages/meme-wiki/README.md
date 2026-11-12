# meme-wiki（soapbot 插件）

改编自 [thawline/dsh-memewiki](https://github.com/thawline/dsh-memewiki)（梗百科 /
黑话词典，词库源 [cnmeme.wiki](https://cnmeme.wiki)）。原版的灵魂是「预查注入」——
在模型看到消息之前把词条塞进上下文；soapbot 的插件面还没有这个注入点（message 钩子
只读），所以本版做**命令式查询**，词库机制按原版语义重排。

## 命令

| 命令 | 作用 |
| --- | --- |
| `/meme <词>` | 查梗（双向匹配：标题、别名、包含关系） |
| `/meme-add <词> <解释>` | 手动补一条（同步不覆盖） |
| `/meme-rm <词>` | 删一条 |
| `/meme-sync [页数]` | 从 cnmeme.wiki 同步（只补新增） |
| `/meme-count` | 词库规模 |

## 能力与边界

| 能力 | 用途 | 收窄 |
| --- | --- | --- |
| `http` | 只 GET cnmeme.wiki 公开 API | 无凭据；宿主白名单 + 禁重定向 |
| `storage` | 词条（≤2000 条，加密） | 手动词条同步不覆盖 |

## 与原版的差异

- **反馈上传通道不做**：原版替用户领凭证、上传词条到站内审核——涉及账号凭据的
  第三方交互，soapbot 的扩展边界是「凭据类一律宿主持有」，而宿主没有这个面。
- **预查注入列待宿主能力**：见 main 仓库 docs/qq-autonomy-design.md §5。
- 词库来源致谢 cnmeme.wiki 与原作者（灵感来自 AstrBot 插件 astrbot_plugin_memewiki）。
