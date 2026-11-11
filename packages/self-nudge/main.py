"""self-nudge: the agent leaves a note for its future self, and speaks on time.

Adapted from https://github.com/not-zai/self-nudge (MIT) into a soapbot plugin.
The original keeps its clock outside the agent (a daemon reads a JSON file); here
the clock is the host's cron system and the state is the plugin's own encrypted
storage. The original's core principle survives the port intact:

* 只有「约不约、约多久、到点说什么」是插件自己的事；
* 投递措辞读起来要像「自己想起来了」，不是「闹钟响了」——所以默认前缀刻意写成
  「我想起一件事」而不是「提醒任务触发」。

Capability mapping (why each one is here, and nothing more):

* ``command``  — /nudge 系列；
* ``storage``  — nudge 列表（时间 + 内容 + 目标会话）；
* ``cron``     — 每分钟的扫描 tick（original 的 daemon tick）；
* ``send``     — 到点投递。宿主本来就只允许发到本插件观察过的会话，这里进一步
  收窄为「发起 /nudge 的那个会话」——提醒谁设的，就回到谁的会话里，不广播。
"""

from __future__ import annotations

import time
import uuid

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

STATE_KEY = "nudges"
MAX_ITEMS = 100
DELIVERY_PREFIX = "我想起一件事："


def _now() -> float:
    return time.time()


def _parse_when(arg: str) -> float | None:
    """``30m`` / ``2h`` / ``HH:MM``（今天或明天）→ 绝对时间戳；不合法返回 None。"""
    arg = arg.strip().lower()
    if arg.endswith("m") and arg[:-1].isdigit():
        return _now() + int(arg[:-1]) * 60
    if arg.endswith("h") and arg[:-1].isdigit():
        return _now() + int(arg[:-1]) * 3600
    if ":" in arg:
        try:
            hour, minute = (int(x) for x in arg.split(":", 1))
        except ValueError:
            return None
        if not (0 <= hour < 24 and 0 <= minute < 60):
            return None
        import datetime as dt

        today = dt.datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
        if today.timestamp() <= _now():
            today += dt.timedelta(days=1)
        return today.timestamp()
    return None


class SelfNudgePlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.command("nudge", description="给未来的自己留一句话，到点在这个会话说出来",
                     usage="/nudge <30m|2h|HH:MM> <想说的话>")
        async def nudge(caller: CallerContext, args: list[str]) -> str:
            if len(args) < 2:
                return "用法：/nudge <30m|2h|HH:MM> <想说的话>"
            when = _parse_when(args[0])
            if when is None:
                return f"看不懂时间 {args[0]!r}；支持 30m / 2h / HH:MM"
            note = " ".join(args[1:]).strip()
            items = ctx.storage.get(STATE_KEY) or []
            if not isinstance(items, list):
                items = []
            if len(items) >= MAX_ITEMS:
                return f"已挂着 {MAX_ITEMS} 条，先清掉一些（/nudge-clear）再约。"
            items.append({
                "id": uuid.uuid4().hex[:12],
                "at": when,
                "note": note,
                "session_key": caller.session_key,
                "created_at": _now(),
            })
            ctx.storage.set(STATE_KEY, items)
            import datetime as dt

            local = dt.datetime.fromtimestamp(when).strftime("%m-%d %H:%M")
            return f"好，{local} 我会想起这件事。"

        @ctx.command("nudge-list", description="看现在还挂着几个约", usage="/nudge-list")
        async def nudge_list(caller: CallerContext, args: list[str]) -> str:
            items = [i for i in (ctx.storage.get(STATE_KEY) or []) if isinstance(i, dict)]
            if not items:
                return "现在没有挂着任何约。"
            import datetime as dt

            lines = []
            for item in sorted(items, key=lambda x: x.get("at", 0)):
                local = dt.datetime.fromtimestamp(item.get("at", 0)).strftime("%m-%d %H:%M")
                lines.append(f"- {local}　{item.get('note', '')}")
            return "还挂着的约：\n" + "\n".join(lines)

        @ctx.command("nudge-clear", description="清空全部约（删了找不回来）", usage="/nudge-clear")
        async def nudge_clear(caller: CallerContext, args: list[str]) -> str:
            ctx.storage.set(STATE_KEY, [])
            return "已清空。"

        @ctx.cron("* * * * *", name="self-nudge-tick")
        async def tick() -> None:
            """Every minute: deliver due nudges, delete on success.

            发送失败不删条目——下一次 tick 会再试。与 original 的 --take 语义一致：
            只有确实取走（投递成功）才从队列移除。
            """
            items = ctx.storage.get(STATE_KEY) or []
            if not isinstance(items, list):
                return
            now = _now()
            due = [i for i in items if isinstance(i, dict) and i.get("at", 0) <= now]
            if not due:
                return
            remaining = [i for i in items if i not in due]
            for item in due:
                note = str(item.get("note", "")).strip()
                session = str(item.get("session_key", ""))
                if not note or not session:
                    continue  # 缺目标或缺内容的条目丢弃：它没有可以诚实验证的来源
                try:
                    await ctx.send(f"{DELIVERY_PREFIX}{note}", session_key=session)
                except (PermissionError, RuntimeError, ValueError):
                    remaining.append(item)  # 下一个 tick 重试
                    continue
            ctx.storage.set(STATE_KEY, remaining)
