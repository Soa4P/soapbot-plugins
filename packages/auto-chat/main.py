"""auto-chat: occasionally speak first, in sessions the operator opted in.

被动应答是管线的事；这个插件只做一件事——在**被显式开启**的会话里，隔一阵子用
最近的话题自己开一次口。五道自我克制，每一道都对着一个具体的滥用：

1. **会话白名单**：宿主 ledger 本来只放行观察过的会话；本插件再收窄为
   「/autochat-set 显式开启的会话」。没被开启的会话，温度再高也不说话。
2. **频率上限**：同一会话两条主动发言 ≥4 小时、每会话每天 ≤2 条、每轮 cron 全局
   只挑一个会话。硬编码，不可配置——配置化等于让「打扰」变成可调参数。
3. **温度阈值**：warmth 由消息钩子计数近似（近 7 天交互的对数归一 × 最近交互的
   时间衰减），低于阈值不开口。阈值可调（/autochat-set），公式不可调。
4. **掷骰判定**（0.4.0，dsh-speak-up 的本地掷骰思想）：过了温度与冷却的会话还要
   过一次本地掷骰——峰时（工作日 9-12/14-18）命中线 60%，其余 20%；连败补偿
   每次 +10、上限 +40，命中即清零。掷骰是 Math.random 级别的本地判定，零 token：
   它解决的是「温度达标就必开口」的机械感，而不是要不要说话。
5. **话题问过即退**（0.4.0，private_companion「未完话头 48h 语义复核不机械追问」
   的确定性版）：开口用过的话题立即从片段池退役——同一句话头不会被追问第二次。
   没有模型复核（本插件刻意不含模型能力），用「问过即退 + 片段池自然换血」实现
   同一件事：宁可沉默，不做复读机。

开口内容来自消息钩子攒下的**话题片段**（每会话最近 3 条、各 ≤120 字符），套进
有限的模板。没有模型调用——本插件能力集刻意不含跑模型；更自然的开口走人格卡的
autonomy 演进（宿主侧，见 main 仓库 docs/qq-autonomy-design.md）。
"""

from __future__ import annotations

import datetime as dt
import math
import random
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

MORNING_OPENERS = [
    "早。昨晚想到「{topic}」，总觉得该问问。",
    "刚起来就想起来「{topic}」这事，有后续吗？",
]

#: 深夜不打扰（23:00-07:30）。private_companion 的「生活作息」思想、longing 的
#: 「会累」思想，soapbot 式的落法：一条确定性时间规则，温度极高也只放行轻声模板，
#: 而不是把夜话写成另一种人格。判断标准硬编码。
QUIET_START_HOUR = 23
QUIET_END_HOUR = 7
DEEP_NIGHT_WARMTH = 0.9

#: 掷骰判定（dsh-speak-up 的公式形状，数字按本插件的重写）：峰时命中线高、
#: 谷时低——「该说话的时段更容易开口」；连败补偿防止「一直不开口」的死角。
PEAK_HIT_LINE = 60
VALLEY_HIT_LINE = 20
STREAK_BOOST = 10
STREAK_CAP = 40
#: 刚聊过不久（<2 小时）命中线打折：刚说完话又主动插一句，像监视。
FRESH_FACTOR = 0.15
#: 太久没聊（>72 小时）命中线上浮：再不开口，这个会话就真的凉了。
COLD_FACTOR = 1.5
FRESH_SECONDS = 2 * 3600
COLD_SECONDS = 72 * 3600

PEAK_START_HOURS = (9, 14)
PEAK_END_HOURS = (12, 18)


def _warmth(count: int, last_seen: float) -> float:
    """近 7 天交互的对数归一 × 最近交互衰减（7 天半衰）。"""
    if count <= 0 or last_seen <= 0:
        return 0.0
    base = math.log1p(min(count, 200)) / math.log1p(200)
    age_days = max(0.0, (time.time() - last_seen) / 86400)
    decay = 0.5 ** (age_days / 7)
    return round(min(1.0, base * decay), 3)


def _is_peak(now_local: dt.datetime) -> bool:
    """工作日的两个峰时段（speak-up 的峰谷表，写死不配置）。"""
    if now_local.weekday() >= 5:  # 周末没有「工作时间」
        return False
    return any(start <= now_local.hour < end for start, end in zip(PEAK_START_HOURS, PEAK_END_HOURS))


def _hit_line(now_local: dt.datetime, last_interaction: float, streak: int) -> int:
    """命中线（0-100）。掷出 random() * 100 低于它就开口。"""
    line = PEAK_HIT_LINE if _is_peak(now_local) else VALLEY_HIT_LINE
    since = time.time() - last_interaction if last_interaction > 0 else None
    if since is not None and since < FRESH_SECONDS:
        line = int(line * FRESH_FACTOR)
    elif since is None or since > COLD_SECONDS:
        line = int(line * COLD_FACTOR)
    line += min(STREAK_CAP, max(0, streak) * STREAK_BOOST)
    return max(0, min(100, line))


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
            return (f"已开启（阈值 {threshold}）。我会按温度和掷骰偶尔自己开口，"
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

        @ctx.command("autochat-dry", description="演练一次掷骰判定（绝不真发、不改计数）",
                     usage="/autochat-dry")
        async def autochat_dry(caller: CallerContext, args: list[str]) -> str:
            """dsh-speak-up 的「演练一次（绝不真发）」：把判定摊开给人看。

            读同样的存储、算同样的公式、掷同样的骰子——只是不发送、不推进任何
            计数。演练结果与真发完全同源，才配叫「演练」。
            """
            session = caller.session_key
            warmth = ctx.storage.get(WARMTH_KEY) or {}
            entry = warmth.get(session) or {}
            w = _warmth(int(entry.get("count", 0)), float(entry.get("last", 0)))
            conf = (ctx.storage.get(CFG_KEY) or {}).get(session)
            enabled = conf is not None
            now_local = dt.datetime.now()
            line = _hit_line(now_local, float(entry.get("last", 0)), int(entry.get("streak", 0)))
            roll = random.randint(0, 99)
            pieces = [p for p in ((ctx.storage.get(SNIPPETS_KEY) or {}).get(session) or [])
                      if isinstance(p, str)]
            verdict = "会开口" if (enabled and w >= float((conf or {}).get("threshold", WARMTH_FLOOR))
                                   and roll < line and pieces) else "不会开口"
            peak = "峰时" if _is_peak(now_local) else "谷时"
            return (
                f"演练（不发送、不改计数）：{peak} 命中线 {line}%，掷出 {roll}，"
                f"温度 {w:.2f}，话题片段 {len(pieces)} 条，开关{'已开' if enabled else '未开'}"
                f" → {verdict}。"
            )

        @ctx.cron("*/30 * * * *", name="auto-chat-tick")
        async def tick() -> None:
            cfg = ctx.storage.get(CFG_KEY) or {}
            warmth = ctx.storage.get(WARMTH_KEY) or {}
            snippets = ctx.storage.get(SNIPPETS_KEY) or {}
            now = time.time()
            now_local = dt.datetime.now()
            quiet = now_local.hour >= QUIET_START_HOUR or now_local.hour < QUIET_END_HOUR
            morning = QUIET_END_HOUR <= now_local.hour < 9
            for session, conf in cfg.items():
                if not isinstance(conf, dict):
                    continue
                entry = warmth.get(session) or {}
                w = _warmth(int(entry.get("count", 0)), float(entry.get("last", 0)))
                if w < float(conf.get("threshold", WARMTH_FLOOR)):
                    continue
                if quiet and w < DEEP_NIGHT_WARMTH:
                    continue  # 深夜不打扰；温度极高也只允许轻声模板
                last_spoke = float(entry.get("last_spoke", 0))
                if now - last_spoke < MIN_GAP_SECONDS:
                    continue
                if int(entry.get("day_count_spoke", 0)) >= DAILY_CAP:
                    continue
                pieces = [p for p in (snippets.get(session) or []) if isinstance(p, str)]
                if not pieces:
                    continue
                # 掷骰判定：本地随机，零 token。连败补偿在命中线里。
                line = _hit_line(now_local, float(entry.get("last", 0)), int(entry.get("streak", 0)))
                roll = random.randint(0, 99)
                if roll >= line:
                    entry["streak"] = int(entry.get("streak", 0)) + 1
                    warmth[session] = entry
                    ctx.storage.set(WARMTH_KEY, warmth)
                    continue
                entry["streak"] = 0
                topic = pieces[-1]
                pool = MORNING_OPENERS if morning else OPENERS
                template = pool[int(now) % len(pool)]
                text = template.format(topic=topic)
                try:
                    await ctx.send(text, session_key=session)
                except (PermissionError, RuntimeError, ValueError):
                    continue  # 会话失效/未授权：跳过，不改计数
                # 话题问过即退：同一句话头不被追问第二次（见模块 docstring 第 5 条）。
                snippets[session] = [p for p in pieces if p != topic]
                ctx.storage.set(SNIPPETS_KEY, snippets)
                entry["last_spoke"] = now
                entry["day_count_spoke"] = int(entry.get("day_count_spoke", 0)) + 1
                warmth[session] = entry
                ctx.storage.set(WARMTH_KEY, warmth)
                return  # 每轮只挑一个会话
