# 向社区索引提交插件

这个仓库只放**索引**，不放插件代码的运行时。soapbot 拉取 `plugins.json` 是为了回答一个问题：某个已装插件有没有更新。它不下载、不安装、不批准任何东西。

理解这一点，后面的规则才好理解：**这里的一切都是声明，不是授权。**

## 提交什么

一次 PR 只做一件事，通常是下面之一：

| 目的 | 改哪个文件 |
| --- | --- |
| 新插件或新版本 | `plugins.json` + `plugin_versions.json` + `plugin_details.json` |
| 修正描述、作者、主页 | `plugin_details.json` |
| 标记某个版本不安全 | `blocked_versions.json` |
| 标记插件停止维护 | `archived_plugins.json` |

## 新插件的最低要求

1. **能装**。包必须是单个 zip，根目录只有一个 `plugin.yaml`，用 `soapbot` 的安装器能装上。提交前自己跑一遍：

   ```bash
   soapbot plugins install ./your-plugin.zip
   ```

2. **声明最小能力集**。`plugin.yaml` 里 `capabilities` 只写真正用到的。多写一项，安装时就要多勾一个框，而勾框的人会开始不看内容——这是最容易被滥用的地方。

3. **有 digest**。`plugin_versions.json` 里的 `digest` 是发布 zip 的 SHA-256，soapbot 下载后比对，不一致直接拒绝。占位值（全 0）只能出现在草稿 PR 里，合并前必须换成真实值。

4. **说明它拿你的数据做什么**。`plugin_details.json` 的 `description` 要写清楚：读了什么、发到哪里、留不留副本。写「提升体验」不算说明。

## 会被拒绝的提交

- 请求 `send` 能力但没有说明往哪里发、发什么。
- 请求 `memory_read` 或 `kb_read` 但没有说明读取范围与保留策略。
- 打包了二进制、`.so`/`.pyd`/`.dll`，或安装时联网下载额外代码。
- `digest` 与实际发布包不一致。
- 版本号回退，或同一版本号改了内容（发布过的版本号不可复用，改内容就发新版本）。

## 封禁是怎么发生的

维护者可以往 `blocked_versions.json` 加一条，写明原因。soapbot 在安装前查这张表，命中就拒绝；已经装上的实例会在插件页看到告警，但**不会被自动卸载**——因为「列表变了就删掉正在跑的代码」本身是一次故障。

封禁是单向的：这个仓库可以加，不能减。soapbot 自己也带一份封禁表，随版本发布，两份合并使用。所以清空这个文件不会解除任何封禁。

## 审核不承诺什么

合并一个 PR 意味着「格式正确、描述与声明一致、没有明显恶意」。它**不**意味着代码经过安全审计，也不意味着作者可信。`trusted_developers.json` 只是显示用的提示，不改变任何安装门槛。

装不装、给哪些能力，始终由部署这个 bot 的人决定。

## 本地校验

```bash
python scripts/validate_index.py
```

它会检查：JSON 可解析、`plugins.json` 里的每个名字在 `plugin_details.json` 与 `plugin_versions.json` 里都有对应条目、版本列表与版本元数据一致、封禁条目指向真实存在的版本、没有重复名字。

CI 还有第二个 job（`code`），它检查的不是「现在自洽吗」而是「这次改动安全吗」：`download` 必须指向本仓库对应 `name-version` 的 release 路径、已发布版本的 `digest`/`download`/`released_at` 不可修改（要改就发新版本）、封禁与归档只增不减、`trusted_developers.json` 变更必须单独开 PR。

## AI 审查与自动合并

PR 会多一条 AI 评论，判 `safe` / `needs_review` / `malicious` 并说明理由。它按这个仓库的风险来判：不是「插件代码有没有后门」（代码不在这里），而是「这次改动有没有动 `digest` / `download` / `versions`，有没有让用户下载到别处」。

**自动合并默认关闭。** 只有下面全部满足时才会开启：

- AI 判 `safe` 且风险 `low`，diff 没被截断、没有降级；
- 变更文件**只有** `plugin_details.json`；
- 增删行里没有出现 `digest` / `download` / `versions` / `latest` 这些字段。

其余一律留给人工，包括所有 `plugins.json`、`plugin_versions.json`、`blocked_versions.json`、`trusted_developers.json` 的改动。这不是保守设置的问题：`digest` 是宿主下载后比对的哈希，改它等于改掉所有已安装用户的信任对象。

开关是仓库 variable `AI_REVIEW_MODE`：`observe`（默认，只评论）或 `enforce`（满足条件时 `gh pr merge --auto`）。即使 `enforce`，auto-merge 仍要等 `index` 与 `code` 两个检查通过 —— 确定性门禁始终是最后一道。

配置：仓库 secret `DEEPSEEK_API_KEY`（必需）；variable `AI_REVIEW_MODEL`（可选，默认 `deepseek-chat`）。
