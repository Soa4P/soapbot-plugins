"""auto-chat: occasionally speak first, in sessions the operator opted in.

被动应答是管线的事；这个插件只做一件事——在**被显式开启**的会话里，隔一阵子用
最近的话题自己开一次口。三道自我克制，每一道都对着一个具体的滥用：

1. **会话白名单**：宿主 ledger 本来只放行观察过的会话；本插件再收窄为
   「/autochat-set 显式开启的会话」。没被开启的会话，温度再高也不说话。
2. **频率上限**：同一会话两条主动发言 ≥4 小时、每会话每天 ≤2 条、每轮 cron 全局
   只挑一个会话。硬编码，不可配置——配置化等于让「打扰」变成可调参数。
3. **温度阈值**：warmth 由消息钩子计数近似（近 7 天交互的对数归一 × 最近交互的
   时间衰减），低于阈值不开口。阈值可调（/autochat-set），公式不可调。

开口内容来自消息钩子攒下的**话题片段**（每会话最近 3 条、各 ≤120 字符），套进
有限的模板。没有模型调用——本插件能力集刻意不含跑模型；更自然的开口走人格卡的
autonomy 演进（宿主侧，见 main 仓库 docs/qq-autonomy-design.md）。
"""

from __future__ import annotations

import time

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

CFG_KEY = "sessions"
SNIPPETS_KEY = "topics"
WARMTH_KEY = "warmth"

MIN_GAP_SECONDS = 4 * 3600
DAILY_CAP = 2
WARMTH_FLOOR = 0.6
SNIPPET_CHARS = 120

OPENERS = [
    "之前聊到「{topic}」，后来怎么样了？",
    "忽然想起「{topic}」这事，有下文吗？",
    "{topic}——想到这个，顺便问一句。",
]


def _warmth(count: int, last_seen: float) -> float:
    """近 7 天交互的对数归一 × 最近交互衰减（7 天半衰）。"""
    if count <= 0 or last_seen <= 0:
        return 0.0
    import math

    base = math.log1p(min(count, 200)) / math.log1p(200)
    age_days = max(0.0, (time.time() - last_seen) / 86400)
    decay = 0.5 ** (age_days / 7)
    return round(min(1.0, base * decay), 3)


class AutoChatPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.on_message
        async def observe(caller: CallerContext, text: str) -> None:
            # 计数与话题片段都在这里攒——钩子有 caller（可信的会话身份），
            # cron 里没有。原文不落盘：只留前 120 字符的片段。
            session = caller.session_key
            warmth = ctx.storage.get(WARMTH_KEY) or {}
            entry = warmth.get(session) or {"count": 0, "last": 0.0}
            if time.time() - entry.get("day_start", 0) > 86400:
                entry["day_count"] = 0
                entry["day_start"] = time.time()
            entry["count"] = int(entry.get("count", 0)) + 1
            entry["last"] = time.time()
            entry["day_count"] = int(entry.get("day_count", 0)) + 1
            warmth[session] = entry
            ctx.storage.set(WARMTH_KEY, warmth)

            snippets = ctx.storage.get(SNIPPETS_KEY) or {}
            pieces = [p for p in (snippets.get(session) or []) if isinstance(p, str)]
            snippet = text.strip()[:SNIPPET_CHARS]
            if snippet and snippet not in pieces:
                pieces = (pieces + [snippet])[-3:]
                snippets[session] = pieces
                ctx.storage.set(SNIPPETS_KEY, snippets)

        @ctx.command("autochat-set", description="在当前会话开启主动开口，并设温度阈值",
                     usage="/autochat-set [阈值0-1，默认0.6]")
        async def autochat_set(caller: CallerContext, args: list[str]) -> str:
            try:
                threshold = max(0.0, min(1.0, float(args[0]))) if args else WARMTH_FLOOR
            except ValueError:
                return "阈值要是 0 到 1 之间的小数，例如 /autochat-set 0.6"
            cfg = ctx.storage.get(CFG_KEY) or {}
            cfg[caller.session_key] = {"threshold": threshold, "set_at": time.time()}
            ctx.storage.set(CFG_KEY, cfg)
            return (f"已开启（阈值 {threshold}）。我会按温度偶尔自己开口，"
                    "两次之间至少隔 4 小时；/autochat-off 关掉。")

        @ctx.command("autochat-off", description="在当前会话关闭主动开口", usage="/autochat-off")
        async def autochat_off(caller: CallerContext, args: list[str]) -> str:
            cfg = ctx.storage.get(CFG_KEY) or {}
            cfg.pop(caller.session_key, None)
            ctx.storage.set(CFG_KEY, cfg)
            return "已关闭。"

        @ctx.command("autochat-clear", description="清空话题片段与计数", usage="/autochat-clear")
        async def autochat_clear(caller: CallerContext, args: list[str]) -> str:
            ctx.storage.set(SNIPPETS_KEY, {})
            ctx.storage.set(WARMTH_KEY, {})
            return "已清空。"

        @ctx.cron("*/30 * * * *", name="auto-chat-tick")
        async def tick() -> None:
            cfg = ctx.storage.get(CFG_KEY) or {}
            warmth = ctx.storage.get(WARMTH_KEY) or {}
            snippets = ctx.storage.get(SNIPPETS_KEY) or {}
            now = time.time()
            for session, conf in cfg.items():
                if not isinstance(conf, dict):
                    continue
                entry = warmth.get(session) or {}
                w = _warmth(int(entry.get("count", 0)), float(entry.get("last", 0)))
                if w < float(conf.get("threshold", WARMTH_FLOOR)):
                    continue
                last_spoke = float(entry.get("last_spoke", 0))
                if now - last_spoke < MIN_GAP_SECONDS:
                    continue
                if int(entry.get("day_count_spoke", 0)) >= DAILY_CAP:
                    continue
                pieces = [p for p in (snippets.get(session) or []) if isinstance(p, str)]
                if not pieces:
                    continue
                topic = pieces[-1]
                template = OPENERS[int(now) % len(OPENERS)]
                text = template.format(topic=topic)
                try:
                    await ctx.send(text, session_key=session)
                except (PermissionError, RuntimeError, ValueError):
                    continue  # 会话失效/未授权：跳过，不改计数
                entry["last_spoke"] = now
                entry["day_count_spoke"] = int(entry.get("day_count_spoke", 0)) + 1
                warmth[session] = entry
                ctx.storage.set(WARMTH_KEY, warmth)
                return  # 每轮只挑一个会话
