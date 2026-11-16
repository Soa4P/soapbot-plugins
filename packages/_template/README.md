# my-plugin（脚手架，不是可发布插件）

这个目录是**从零写一个 soapbot 插件的起点**：复制整个 `_template/` 为你的插件名、
改 `plugin.yaml` 与 `main.py`、按 README 模板填边界说明。它不进插件索引
（`plugins.json` 没有它），索引校验脚本也只认索引里声明过的名字。

## 起步清单（对着 CONTRIBUTING.md 走）

1. `plugin.yaml`：改 `name` / `version` / `entry` / `description`，`capabilities`
   只留真正用到的（可用能力与边界见主仓库 `docs/capabilities.md`）。
2. `main.py`：`entry` 指向的类继承 `Plugin`；命令用 `@ctx.command` 注册，钩子用
   `@ctx.on_message`，定时用 `@ctx.cron`。docstring 回答「读了什么 / 发到哪里 /
   留不留副本」。
3. 本地装一遍：`soapbot plugins install ./my-plugin-0.1.0.zip`（zip 根目录只有
   `plugin.yaml` + `main.py` + README）。
4. 上架：往 `plugins.json` + `plugin_versions.json` + `plugin_details.json` 提
   PR（digest 是发布 zip 的真实 SHA-256；发布流程见 VERSIONING.md）。

## 判断标准

- 能确定性就确定性：无模型调用是本仓库插件的主流形态；要模型判断，登记为宿主
  设计输入（定时语义任务/工具面），不走插件后门。
- 主动发言的克制：send 只发显式开启的会话，频率上限写死不配置化。
- 「提升体验」不是边界说明：读了什么、发到哪里、留不留副本，逐条写清。
