# 版本与发布规则

## 版本号

语义化版本，`MAJOR.MINOR.PATCH`，与 `plugin.yaml` 里的 `version` 完全一致。索引里的版本号是**声明**，安装器读的是包里的清单；两者不一致时以包为准，并且这次提交会被要求修正。

- `MAJOR`：`plugin.yaml` 的 `capabilities` 增加，或命令名/参数不兼容变更。
- `MINOR`：新增命令、新增可选配置。
- `PATCH`：修复，不改变对外行为。

**发布过的版本号不可复用。** 改了内容就发新版本。索引里同一版本的 `digest` 变化会被校验脚本拒绝，因为那意味着「同一个版本号对应两份不同的代码」，而 soapbot 的 digest 比对正是靠这个前提成立的。

## digest

`digest` 是发布 zip 的 SHA-256，格式 `sha256:<64 位小写十六进制>`。

```bash
sha256sum weather-1.2.0.zip
```

发布流程是：先打包、再算 digest、再写进 `plugin_versions.json`、最后创建 release。顺序反了就会出现「索引里的 digest 指向一个还没上传的文件」，而 soapbot 在下载时会拿到 404 或一个不匹配的包。

## 发布流程

1. 在插件自己的目录里改代码，跑通 `soapbot plugins install`。
2. 打包：`zip -r weather-1.2.0.zip plugin.yaml main.py`（根目录只有一个 `plugin.yaml`）。
3. 算 digest，写进 `plugin_versions.json`，同时更新 `plugins.json` 的 `latest` 与 `versions`。
4. 提 PR，等校验脚本与人工审核。
5. 合并后创建 release，上传 zip。

第 3 步和第 5 步的顺序在 PR 里是反的（先写索引，合并后才发 release），这是有意的：索引指向一个尚不存在的 release，最坏情况是有人提前点下载拿到 404；反过来先发 release 再写索引，则会出现「包已经能下载，但没人知道它是什么」。

## 撤回一个版本

不要删条目。删掉之后，已经装了该版本的实例就查不到自己装的是什么了。正确做法是往 `blocked_versions.json` 加一条，写清原因，然后发一个修复版本。

## 归档

插件停止维护时，从 `plugins.json` 的 `plugins` 里移除，往 `archived_plugins.json` 加一条，写 `last_version` 与原因。已安装的实例仍能运行，插件页会显示「已归档」。
