# soapbot-plugins

soapbot 的社区插件索引。

这个仓库**不托管插件代码**，只放索引文件。soapbot 拉取它是为了回答一个问题：某个已装插件有没有更新版本。它不下载、不安装、不批准任何东西。

## 为什么索引不是信任源

一个索引就是别人服务器上的一个 JSON 文件。如果「从索引一键安装」成立，那么篡改这个文件就等于在所有部署上执行任意代码，而安装时那张让人确认能力的批准页会退化成装饰。

所以 soapbot 的分工是：

- **索引负责发现**：有哪些插件、有哪些版本、作者声称它做什么。
- **本地负责信任**：下载、上传、逐项勾选能力、记录 digest。少一步都装不上。

`blocked_versions.json` 是唯一的例外方向——索引可以**加**封禁，不能**减**。soapbot 自己也带一份封禁表随版本发布，两份合并使用。

## 文件

| 文件 | 内容 |
| --- | --- |
| `plugins.json` | 插件列表与版本号，soapbot 每次打开插件页会拉这个 |
| `plugin_details.json` | 长描述、能力声明、README 路径 |
| `plugin_versions.json` | 每个版本的发布日期、digest、最低宿主版本、下载地址 |
| `blocked_versions.json` | 禁止安装的版本与原因 |
| `archived_plugins.json` | 停止维护的插件 |
| `trusted_developers.json` | 作者显示提示，不改变任何安装门槛 |

## 在 soapbot 里启用

```bash
SOAPBOT_PLUGIN_INDEX_URL=https://raw.githubusercontent.com/Soa4P/soapbot-plugins/main/plugins.json
```

不设这个变量时，插件页只显示已安装的插件——这是默认状态，不是故障。

拉取有 5 秒超时、15 分钟缓存。索引不可达时插件页显示「无法发现」，已装插件照常工作。

## 提交插件

见 [CONTRIBUTING.md](CONTRIBUTING.md)，版本与发布规则见 [VERSIONING.md](VERSIONING.md)。

本地校验：

```bash
python scripts/validate_index.py
```

## 审核边界

合并一个 PR 意味着格式正确、描述与声明一致、没有明显恶意。它不意味着代码经过安全审计，也不意味着作者可信。装不装、给哪些能力，始终由部署 bot 的人决定。
