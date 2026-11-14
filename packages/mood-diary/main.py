"""mood-diary: a diary-style mood timeline, deterministic, no model calls.

改编自 dsh-feiyu-mood（模型每轮判心情 + 注入 prompt）与 private_companion
（每日反思、纪念日），走 soapbot 自己的路：

* **心情是日记，不是注入**。feiyu-mood 把模型判出的心情写进 systemPrompt.context
  让模型「带着情绪」回答——soapbot 的边界是插件状态不进 prompt 权威（温度由宿主
  确定性计算、写入权只在管线）。所以这里的心情是**事后日记**：每天 21:50 由交互
  统计推出当日基调（热闹/平静/想念……），写进插件自己的加密 storage 时间线；
  用户 `/mood` 查看最近一周。它描述的是「这一天聊得怎么样」，不试图改变模型。
* **确定性基调，不掷心情骰子**。基调由当日交互数硬编码分档：≥15 开心、6-14 热闹、
  1-5 平静、0 想念。与温度互补：温度是宿主的「关系亲疏」，这里是「当日热闹程度」。
* **纪念日**（private_companion 的「每日反思提取互动纪念日」的确定性版）：记录
  每个开启会话的第一次对话日期；周年当天主动说一句（每年最多一次，标记防重）。

主动发言走 send、只发显式开启的会话，每日主动 ≤1 条——与 auto-chat 同一套克制。
"""

from __future__ import annotations

import datetime as dt
import time

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

CFG_KEY = "sessions"          # session -> {"set_at": ...}
STATS_KEY = "stats"           # session -> {"day": "YYYY-MM-DD", "count": n, "snippets": []}
TIMELINE_KEY = "timeline"     # session -> [ {"date", "tone", "count", "note"} ] (≤30)
FIRST_KEY = "first_talk"      # session -> {"date": "YYYY-MM-DD", "anniversaries": [YYYY]}
SNIPPET_CHARS = 60
SNIPPETS_PER_DAY = 5
TIMELINE_DAYS = 30

TONES = (
    (15, "开心"),
    (6, "热闹"),
    (1, "平静"),
    (0, "想念"),
)

ANNIVERSARY_MIN_DAYS = 30

MOOD_NOTES = {
    "开心": "今天聊得特别热，记一笔：{note}",
    "热闹": "今天很热闹，{note}",
    "平静": "平平静静的一天，{note}",
    "想念": "今天一次也没聊，有点想你。",
}
ANNIVERSARY_LINE = "今天是我们认识满 {days} 天的日子。谢谢你还在这里。"


def _today() -> str:
    return dt.date.today().isoformat()


def _tone(count: int) -> str:
    for floor, name in TONES:
        if count >= floor:
            return name
    return "想念"


class MoodDiaryPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.on_message
        async def observe(caller: CallerContext, text: str) -> None:
            session = caller.session_key
            stats = ctx.storage.get(STATS_KEY) or {}
            entry = stats.get(session) or {}
            if entry.get("day") != _today():
                entry = {"day": _today(), "count": 0, "snippets": []}
            entry["count"] = int(entry.get("count", 0)) + 1
            snippet = text.strip()[:SNIPPET_CHARS]
            if snippet and len(entry["snippets"]) < SNIPPETS_PER_DAY:
                entry["snippets"] = entry.get("snippets", []) + [snippet]
            stats[session] = entry
            ctx.storage.set(STATS_KEY, stats)

            first = ctx.storage.get(FIRST_KEY) or {}
            if session not in first:
                first[session] = {"date": _today(), "anniversaries": []}
                ctx.storage.set(FIRST_KEY, first)

        @ctx.command("mood-on", description="开启每日心情日记与主动一句", usage="/mood-on")
        async def mood_on(caller: CallerContext, args: list[str]) -> str:
            cfg = ctx.storage.get(CFG_KEY) or {}
            cfg[caller.session_key] = {"set_at": time.time()}
            ctx.storage.set(CFG_KEY, cfg)
            first = ctx.storage.get(FIRST_KEY) or {}
            since = (first.get(caller.session_key) or {}).get("date", "今天")
            return f"已开启心情日记（从 {since} 记起）。每晚 21:50 记一笔；/mood 查看。"

        @ctx.command("mood-off", description="关闭心情日记", usage="/mood-off")
        async def mood_off(caller: CallerContext, args: list[str]) -> str:
            cfg = ctx.storage.get(CFG_KEY) or {}
            cfg.pop(caller.session_key, None)
            ctx.storage.set(CFG_KEY, cfg)
            return "已关闭。时间线保留，重新开启后接着记。"

        @ctx.command("mood", description="查看最近七天的心情日记", usage="/mood")
        async def mood_view(caller: CallerContext, args: list[str]) -> str:
            timeline = (ctx.storage.get(TIMELINE_KEY) or {}).get(caller.session_key) or []
            if not timeline:
                return "还没有日记。开启（/mood-on）后每晚 21:50 记一笔。"
            lines = [f"{e['date']} {e['tone']}（{e['count']} 条）" for e in timeline[-7:]]
            stats = (ctx.storage.get(STATS_KEY) or {}).get(caller.session_key) or {}
            if stats.get("day") == _today():
                lines.append(f"今天：{_tone(int(stats.get('count', 0)))}（{stats.get('count', 0)} 条，尚未入夜记）")
            return "最近的心情：\n" + "\n".join(lines)

        @ctx.command("mood-forget", description="清空心情日记与统计", usage="/mood-forget")
        async def mood_forget(caller: CallerContext, args: list[str]) -> str:
            for key in (STATS_KEY, TIMELINE_KEY, FIRST_KEY):
                data = ctx.storage.get(key) or {}
                data.pop(caller.session_key, None)
                ctx.storage.set(key, data)
            return "心情日记已清空。"

        @ctx.cron("50 21 * * *", name="mood-diary-tick")
        async def tick() -> None:
            cfg = ctx.storage.get(CFG_KEY) or {}
            stats = ctx.storage.get(STATS_KEY) or {}
            timeline = ctx.storage.get(TIMELINE_KEY) or {}
            first = ctx.storage.get(FIRST_KEY) or {}
            today = _today()
            now_local = dt.datetime.now()
            for session in list(cfg.keys()):
                entry = stats.get(session) or {"day": today, "count": 0}
                if entry.get("day") != today:  # 钩子当天没来过：今天想念
                    entry = {"day": today, "count": 0, "snippets": []}
                count = int(entry.get("count", 0))
                tone = _tone(count)
                snippets = [s for s in entry.get("snippets", []) if isinstance(s, str)]
                note = snippets[-1] if snippets else ""
                days = timeline.get(session) or []
                days = (days + [{"date": today, "tone": tone, "count": count, "note": note}])[-TIMELINE_DAYS:]
                timeline[session] = days
                ctx.storage.set(TIMELINE_KEY, timeline)

                # 纪念日：第一次对话的周年（每年一次，标记防重）。
                first_entry = first.get(session) or {}
                first_date = str(first_entry.get("date") or "")
                sent_line = ""
                if first_date and first_date != today:
                    try:
                        origin = dt.date.fromisoformat(first_date)
                        days_together = (dt.date.today() - origin).days
                        if (
                            days_together >= ANNIVERSARY_MIN_DAYS
                            and origin.month == dt.date.today().month
                            and origin.day == dt.date.today().day
                            and dt.date.today().year not in (first_entry.get("anniversaries") or [])
                        ):
                            first_entry["anniversaries"] = list(first_entry.get("anniversaries") or []) + [
                                dt.date.today().year
                            ]
                            first[session] = first_entry
                            ctx.storage.set(FIRST_KEY, first)
                            sent_line = ANNIVERSARY_LINE.format(days=days_together)
                    except ValueError:
                        pass

                # 主动一句：仅当日基调极端（开心/想念）且开启了日记的会话，每天 ≤1 条。
                if tone in ("开心", "想念"):
                    line = sent_line or MOOD_NOTES.get(tone, "").format(note=note) if note or sent_line else MOOD_NOTES.get(tone, "")
                    if tone == "想念" and not sent_line:
                        line = MOOD_NOTES["想念"]
                    if line:
                        try:
                            await ctx.send(line, session_key=session)
                        except (PermissionError, RuntimeError, ValueError):
                            pass
                # 当日计数清零由下一次钩子按日期翻页处理，这里不动 stats。
