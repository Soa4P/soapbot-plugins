"""memory-weekly: a weekly "what do we still remember" digest, command-driven.

改编自 dsh-fish-memory（淡忘半衰期 + 证据式召回 + 事实键）与 private_companion
（记忆反思）。走 soapbot 自己的路，边界决定形态：

* **召回权在宿主**。插件只能经 ``ctx.memory.recall``（memory_read、每次调用重查
  能力、会话键由宿主绑定的 caller 决定）读记忆，没有「全量列出」「按时间过滤」
  的能力——fish-memory 的淡忘半衰期与事实键 latest-wins 是宿主记忆层的设计输入
  （记进 ADAPTATION.md 第五批），不是插件能自己实现的。
* **于是「回顾」的锚点是本周聊过的话题**：消息钩子攒本周话题片段（前 100 字符、
  保留 14 天），``/memweek`` 用它们逐个召回，按记忆 id 去重，产出「这周聊到的、
  还被想起的事」。不编造时间感——召回结果没有时间戳，就不假装有（dsh-soundtry
  的诚实性原则：不确定的东西明说，不输出似是而非的「X 天前」）。
* **没有 cron 主动推送**：跨会话召回在 cron 里做不到（无 caller 即无会话范围，
  空范围会被宿主拒绝），而只发一句话不内容的通知意义不大。回顾是用户主动要的。
"""

from __future__ import annotations

import datetime as dt
import time

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

TOPICS_KEY = "topics"          # session -> [ {"text", "ts"} ] (≤20，14 天过期)
TOPIC_CHARS = 100
TOPICS_MAX = 20
TOPIC_TTL_DAYS = 14
RECALL_PER_TOPIC = 3
DIGEST_MAX = 12


def _now() -> float:
    return time.time()


def _fresh(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cutoff = _now() - TOPIC_TTL_DAYS * 86400
    return [e for e in entries if isinstance(e, dict) and float(e.get("ts", 0)) >= cutoff]


class MemoryWeeklyPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.on_message
        async def observe(caller: CallerContext, text: str) -> None:
            session = caller.session_key
            topics = ctx.storage.get(TOPICS_KEY) or {}
            entries = _fresh(topics.get(session) or [])
            snippet = text.strip()[:TOPIC_CHARS]
            if snippet and all(snippet != e.get("text") for e in entries):
                entries = (entries + [{"text": snippet, "ts": _now()}])[-TOPICS_MAX:]
                topics[session] = entries
                ctx.storage.set(TOPICS_KEY, topics)

        @ctx.command(
            "memweek",
            description="把本周聊过的话题逐个召回，整理成「这周还记得的事」",
            usage="/memweek",
        )
        async def memweek(caller: CallerContext, args: list[str]) -> str:
            if not ctx.memory.available:
                return "宿主没有配置记忆层，回顾无从谈起。"
            session = caller.session_key
            topics = _fresh((ctx.storage.get(TOPICS_KEY) or {}).get(session) or [])
            if not topics:
                return "最近两周没有攒下话题。聊点什么，再来 /memweek。"

            seen: set[int] = set()
            lines: list[str] = []
            for entry in topics[:TOPICS_MAX]:
                query = str(entry.get("text", ""))[:60]
                if not query:
                    continue
                try:
                    hits = await ctx.memory.recall(query, limit=RECALL_PER_TOPIC)
                except (PermissionError, RuntimeError, ValueError):
                    continue  # 能力被撤回/无 store：静默跳过该话题
                for hit in hits:
                    if hit.id in seen:
                        continue
                    seen.add(hit.id)
                    days = max(0, int((_now() - float(entry["ts"])) / 86400))
                    when = "今天" if days == 0 else f"{days} 天前"
                    lines.append(f"· {hit.content}（因{when}聊到「{query[:30]}」想起）")
                    if len(lines) >= DIGEST_MAX:
                        break
                if len(lines) >= DIGEST_MAX:
                    break
            if not lines:
                return "本周的话题在记忆里没有勾起任何东西——也可能还没攒下值得记的。"
            return "这周聊到的、还记得的事：\n" + "\n".join(lines[:DIGEST_MAX])
