# fishtalk-lite（soapbot 插件）

改编自 [Nozzi2455/fishtalk](https://github.com/Nozzi2455/fishtalk)。三个动作保留：
collect（收语料）、check（查八股）、forget（全忘）；「自动拦截回复」没有搬——soapbot
插件的 message hook 只读不拦，check 先做成命令，接进回复管线是宿主侧后续工作。

## 命令

| 命令 | 作用 |
|---|---|
| `/fish-collect <BV号>` | 收一个视频的标题/简介作风格样本 |
| `/fish-check <草稿>` | 查一段话的「鱼味」（八股词 + 模板形状打分） |
| `/fish-status` | 看语料量 |
| `/fish-forget 确认` | 全部忘掉（需要确认词） |

## 能力与边界

| 能力 | 用途 | 收窄 |
|---|---|---|
| `bilibili` | 只 GET 视频公开元数据 | 宿主钉死 api.bilibili.com；无写入 |
| `storage` | 语料样本（≤200 字/条，300 条上限，加密） | 不存评论、不存用户隐私 |

检查是确定性规则（词表 + 形状正则），没有模型调用——本插件的能力集刻意不包含跑模型。
词表硬编码而非配置化：判断标准悄悄可改，等于结论悄悄可改。
