"""my-plugin: 一句话定位。

写插件前先回答三个问题（答案要写进本 docstring 和 README）：

1. 读了什么？——消息钩子拿到的文本只留片段还是原文？
2. 发到哪里？——send 只发显式开启的会话；send_media 走宿主 ledger。
3. 留不留副本？——storage 是加密的、按插件隔离的，但「存了什么」必须写明。

能力集最小化：`capabilities` 只写真正用到的。多写一项，安装时就要多勾一个框，
而勾框的人会开始不看内容——这是最容易被滥用的地方。判断标准能确定性就确定性：
无模型调用是本仓库插件的主流形态，要模型判断走宿主的管线/人格，不走插件后门。
"""

from __future__ import annotations

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

KEY = "state"


class MyPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.command("my-plugin-hello", description="示例命令：返回一句问候",
                     usage="/my-plugin-hello")
        async def hello(caller: CallerContext, args: list[str]) -> str:
            state = ctx.storage.get(KEY) or {"count": 0}
            state["count"] = int(state.get("count", 0)) + 1
            ctx.storage.set(KEY, state)
            return f"你好（第 {state['count']} 次）。storage 是加密的、按会话可用的。"
