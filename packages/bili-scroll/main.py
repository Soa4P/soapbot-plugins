"""bili-scroll: one round of Bilibili browsing per day, reported back.

Adapted from https://github.com/c1overzz/dsh-bili-scroll — the idea (search, read,
judge, report, @me) survives; the machinery does not. The original runs Whisper
locally and writes comments; soapbot's version is *read-only* by design:

* v1 pulls the public popular feed (no login state needed) and optional keyword
  filtering; deep "read the transcript" judging (the original's core) needs the
  WBI-signed player endpoint and is listed in ADAPTATION.md as future work rather
  than half-shipped here.
* It never writes to Bilibili: no likes, no comments, no follows. The original's
  "leave a comment @me" step is a *send to the operator's session* instead.

Judging is a deterministic scoring rule (title/desc keyword hits minus clickbait
markers), not a model call — a plugin command can run the model only through the
pipeline, which this capability set deliberately does not include. Operators who
want model judging can pipe the report into their own flow.
"""

from __future__ import annotations

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

SEEN_KEY = "seen_bvids"
CFG_KEY = "config"
MAX_SEEN = 500

POPULAR_PATH = "/x/web-interface/popular"
VIEW_PATH = "/x/web-interface/view"

DEFAULT_KEYWORDS = ["教程", "科普", "开源", "记录", "实测"]
CLICKBAIT_MARKERS = ["震惊", "竟然", "真相", "必看", "秒杀"]


def _score(item: dict, keywords: list[str]) -> int:
    title = str(item.get("title") or "")
    desc = str(item.get("desc") or "")
    text = title + desc
    hits = sum(1 for k in keywords if k in text)
    bait = sum(1 for m in CLICKBAIT_MARKERS if m in title)
    return hits * 2 - bait


class BiliScrollPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.command("bili-scroll-set", description="配置每日刷 B 站的关键词与投递会话",
                     usage="/bili-scroll-set <逗号分隔关键词>（留空用默认）")
        async def scroll_set(caller: CallerContext, args: list[str]) -> str:
            keywords = [k.strip() for k in (args[0] if args else "").split(",") if k.strip()]
            ctx.storage.set(CFG_KEY, {
                "keywords": keywords or DEFAULT_KEYWORDS,
                "session_key": caller.session_key,
            })
            names = "、".join((keywords or DEFAULT_KEYWORDS)[:10])
            return f"已配置。关注词：{names}。每天 09:00 刷一轮，报告发到这个会话。"

        @ctx.command("bili-scroll-now", description="现在刷一轮", usage="/bili-scroll-now")
        async def scroll_now(caller: CallerContext, args: list[str]) -> str:
            return await _run_round(ctx, deliver_to=str(caller.session_key))

        @ctx.command("bili-scroll-forget", description="清空已推荐去重记录", usage="/bili-scroll-forget")
        async def scroll_forget(caller: CallerContext, args: list[str]) -> str:
            ctx.storage.set(SEEN_KEY, [])
            return "已清空去重记录。"

        @ctx.cron("0 9 * * *", name="bili-scroll-daily")
        async def daily() -> None:
            cfg = ctx.storage.get(CFG_KEY) or {}
            if not cfg.get("session_key"):
                return
            report = await _run_round(ctx, deliver_to=None)
            if report:
                try:
                    await ctx.send(report, session_key=str(cfg["session_key"]))
                except (PermissionError, RuntimeError, ValueError):
                    return  # 发送失败：不标已读，明天同一批视频还会被看到

        async def _run_round(ctx: PluginContext, *, deliver_to: str | None) -> str:
            cfg = ctx.storage.get(CFG_KEY) or {}
            keywords = [str(k) for k in (cfg.get("keywords") or DEFAULT_KEYWORDS)]
            seen = {str(b) for b in (ctx.storage.get(SEEN_KEY) or []) if isinstance(b, str)}

            payload = await ctx.bilibili.get_json(POPULAR_PATH, params={"ps": "40"})
            if payload.get("code") != 0:
                return f"B 站热门接口返回错误码 {payload.get('code')}"
            items = (payload.get("data") or {}).get("list") or []
            scored = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                bvid = str(item.get("bvid") or "")
                if not bvid or bvid in seen:
                    continue
                score = _score(item, keywords)
                if score > 0:
                    scored.append((score, item))
            scored.sort(key=lambda pair: -pair[0])
            picks = scored[:5]
            if not picks:
                if deliver_to is not None:
                    return "这一轮没有值得看的（按当前关注词）。"
                return ""
            lines = []
            new_bvids: list[str] = []
            for score, item in picks:
                bvid = str(item.get("bvid"))
                title = str(item.get("title") or "")
                owner = str((item.get("owner") or {}).get("name") or "")
                stat = item.get("stat") or {}
                lines.append(
                    f"- [{bvid}] {title}（{owner} · 播放 {stat.get('view', 0)}）"
                )
                new_bvids.append(bvid)
            seen_list = [b for b in (ctx.storage.get(SEEN_KEY) or []) if isinstance(b, str)]
            ctx.storage.set(SEEN_KEY, (seen_list + new_bvids)[-MAX_SEEN:])
            # 只返回报告；投递由调用方决定（命令路径=返回值即展示，cron 路径=ctx.send）。
            return "今天的 B 站摘要：\n" + "\n".join(lines)

        # 把内部函数挂到实例上，供 daily 闭包复用（cron 注册要求顶层 async 函数）
        self._run_round = _run_round  # type: ignore[attr-defined]
