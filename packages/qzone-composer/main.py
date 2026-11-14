"""qzone-composer: draft one Qzone post per day from RSS material — human confirms.

改编自 kakamiku/dsh-qzone（素材 → 脱敏 → 按人设写一句话 → 发布），把「自动发帖」
改成 soapbot 边界内的形态：**机器起草，人确认发布**。

与 dsh-qzone 的三个决定性差别（都来自本仓库的既有边界，不是新发明）：

1. **不自动发帖**。宿主 qzone 网关（A12）的写路径是三重确认：操作者启用 + 插件
   能力审批 + 每条 ``confirm=true`` 与插件二次命令确认。dsh-qzone 的 cron 直接
   发布在本仓库走不通，所以 cron 只产出**草稿**，发布必须由人敲 /qzc-confirm。
2. **素材只有 RSS 一种**（chat 分桶需要把会话内容搬进空间——公开性风险自担的
   立场不变；bili 看过表需要读别的插件的 storage，做不到）。RSS 域名必须在
   操作者的 http 允许列表里——插件不能自己加源，这是 http 能力的既有边界。
3. **写失败不重试**。dsh-qzone 的实测（票据会掉、retcode=10027 等 60 秒、只发
   经典说说）直接采纳为行为：网关报错就如实返回，重试是人的决定。

脱敏是确定性正则（QQ 号 / 群号 / 手机号 / 长数字串 / 路径 / key=value），过滤后
不合格就放弃这条素材——宁缺毋滥，发出去的话是拿操作者账号说的。
"""

from __future__ import annotations

import datetime as dt
import re
import time
import xml.etree.ElementTree as ET

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

CFG_KEY = "sessions"        # session -> {"set_at"}
DRAFT_KEY = "draft"         # session -> {"text", "source_title", "ts", "day"}
DAY_KEY = "day_count"       # session -> {"day", "drafted", "posted"}

DAILY_DRAFT_LIMIT = 1
DAILY_POST_LIMIT = 1
MAX_ITEMS_FROM_FEED = 5
TITLE_MAX_CHARS = 60
TEXT_MAX_CHARS = 200
FEED_BODY_MAX = 512 * 1024

LINE_TEMPLATE = "看到一条：{title}（{source}），有点意思。"

#: 脱敏（dsh-qzone 的 scrubber 思想，规则子集）：命中即放弃该素材。
_SENSITIVE = re.compile(
    r"1[3-9]\d{9}"                       # 手机号
    r"|[1-9]\d{5}(?:19|20)?\d{2}(?:0[1-9]|1[0-2])(?:[0-2]\d|3[01])\d{3}[\dXx]"  # 身份证
    r"|\b\d{5,12}\b"                     # QQ 号/群号形态的长数字串
    r"|[A-Za-z]:\\\\[^\\s\"]+"           # Windows 路径
    r"|\b(?:key|token|secret|password)\s*[=:]"  # 凭据形态
)

#: RSS 是外部 XML：先拒 DOCTYPE/ENTITY（实体扩展炸弹），再限输入大小。
_FEED_FORBIDDEN = re.compile(r"<!DOCTYPE|<!ENTITY", re.IGNORECASE)


def _scrub_ok(text: str) -> bool:
    return _SENSITIVE.search(text) is None


def _clean_title(raw: str) -> str:
    title = re.sub(r"<[^>]+>", "", str(raw or "")).strip()
    return title[:TITLE_MAX_CHARS]


class QzoneComposerPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.command("qzc-set", description="开启每日素材草稿（当前会话接收提醒并确认发布）",
                     usage="/qzc-set")
        async def qzc_set(caller: CallerContext, args: list[str]) -> str:
            cfg = ctx.storage.get(CFG_KEY) or {}
            cfg[caller.session_key] = {"set_at": time.time()}
            ctx.storage.set(CFG_KEY, cfg)
            return (
                "已开启。每天 10:00 从 http 允许列表里的 RSS 源抓素材、脱敏后起草一条，"
                "提醒发到这里；发布要你敲 /qzc-confirm（三重确认的最后一道）。"
                "素材源域名请先加进 http 允许列表。"
            )

        @ctx.command("qzc-off", description="关闭每日草稿", usage="/qzc-off")
        async def qzc_off(caller: CallerContext, args: list[str]) -> str:
            cfg = ctx.storage.get(CFG_KEY) or {}
            cfg.pop(caller.session_key, None)
            ctx.storage.set(CFG_KEY, cfg)
            return "已关闭。未确认的草稿保留。"

        @ctx.command("qzc-draft-now", description="现在起草一轮（不发布）", usage="/qzc-draft-now")
        async def draft_now(caller: CallerContext, args: list[str]) -> str:
            return await self._draft(ctx, str(caller.session_key), manual=True)

        @ctx.command("qzc-confirm", description="确认发布当前草稿（三重确认的最后一道）",
                     usage="/qzc-confirm")
        async def qzc_confirm(caller: CallerContext, args: list[str]) -> str:
            draft = ctx.storage.get(DRAFT_KEY) or {}
            entry = draft.get(caller.session_key)
            if not isinstance(entry, dict) or not entry.get("text"):
                return "没有待确认的草稿。"
            counts = ctx.storage.get(DAY_KEY) or {}
            bucket = counts.get(caller.session_key) or {}
            if bucket.get("day") != dt.date.today().isoformat():
                bucket = {"day": dt.date.today().isoformat(), "posted": 0}
            if int(bucket.get("posted", 0)) >= DAILY_POST_LIMIT:
                return "今天已经发过一条了（每日 ≤1，写操作不自动重试）。"
            try:
                await ctx.qzone_write(str(entry["text"]), confirm=True)
            except (PermissionError, RuntimeError, ValueError) as exc:
                # 写失败不自动重试（dsh-qzone 实测：票据会掉，重试是人的决定）。
                counts[caller.session_key] = bucket
                ctx.storage.set(DAY_KEY, counts)
                return f"发布失败：{exc}。要不要再试，由你决定。"
            bucket["posted"] = int(bucket.get("posted", 0)) + 1
            counts[caller.session_key] = bucket
            ctx.storage.set(DAY_KEY, counts)
            draft.pop(caller.session_key, None)
            ctx.storage.set(DRAFT_KEY, draft)
            return f"已发布：{str(entry['text'])[:50]}"

        @ctx.command("qzc-drop", description="放弃当前草稿", usage="/qzc-drop")
        async def qzc_drop(caller: CallerContext, args: list[str]) -> str:
            draft = ctx.storage.get(DRAFT_KEY) or {}
            draft.pop(caller.session_key, None)
            ctx.storage.set(DRAFT_KEY, draft)
            return "草稿已放弃。"

        @ctx.cron("0 10 * * *", name="qzone-composer-daily")
        async def daily() -> None:
            cfg = ctx.storage.get(CFG_KEY) or {}
            for session in list(cfg.keys()):
                result = await self._draft(ctx, session, manual=False)
                if result.startswith("草稿"):
                    try:
                        await ctx.send(result, session_key=session)
                    except (PermissionError, RuntimeError, ValueError):
                        continue  # 发送失败：草稿留在 storage，/qzc-confirm 仍可用

        async def _draft(self, ctx: PluginContext, session: str, *, manual: bool) -> str:
            counts = ctx.storage.get(DAY_KEY) or {}
            bucket = counts.get(session) or {}
            if bucket.get("day") != dt.date.today().isoformat():
                bucket = {"day": dt.date.today().isoformat(), "drafted": 0, "posted": 0}
            if not manual and int(bucket.get("drafted", 0)) >= DAILY_DRAFT_LIMIT:
                return ""
            existing = ctx.storage.get(DRAFT_KEY) or {}
            if isinstance(existing.get(session), dict) and existing[session].get("text"):
                return f"草稿还在，先 /qzc-confirm 或 /qzc-drop：{str(existing[session]['text'])[:50]}"

            # 素材：允许列表里的 RSS（第一源可用即止）。域名由操作者的 http 允许列表
            # 决定——插件列在这里只是「值得试」的顺序，不是授权。
            picked: tuple[str, str] | None = None
            for feed in RSS_FALLBACK_FEEDS:
                try:
                    status, body = await ctx.http.fetch(
                        feed, headers={"User-Agent": "soapbot-qzone-composer/0.1"}
                    )
                except (PermissionError, RuntimeError, ValueError):
                    continue  # 域名不在允许列表/网络失败：换下一源
                if status != 200:
                    continue
                picked = _pick_from_rss(body, source=feed)
                if picked:
                    break
            if not picked:
                return "这一轮没有可用的素材（RSS 都不可用或全被脱敏拦下）。宁缺毋滥。"

            title, source = picked
            text = LINE_TEMPLATE.format(title=title, source=source)[:TEXT_MAX_CHARS]
            bucket["drafted"] = int(bucket.get("drafted", 0)) + 1
            counts[session] = bucket
            ctx.storage.set(DAY_KEY, counts)
            draft = ctx.storage.get(DRAFT_KEY) or {}
            draft[session] = {"text": text, "source_title": title, "ts": time.time(),
                              "day": dt.date.today().isoformat()}
            ctx.storage.set(DRAFT_KEY, draft)
            return f"草稿（待确认，/qzc-confirm 发布 / /qzc-drop 放弃）：{text}"

        self._draft = _draft  # type: ignore[attr-defined]


#: 兜底 RSS 源（公开、无凭据）。能否真的抓到取决于操作者的 http 允许列表——
#: 插件列在这里只是「值得试」的顺序，不是授权。
RSS_FALLBACK_FEEDS = [
    "https://www.ithome.com/rss/",
    "https://sspai.com/feed",
]


def _pick_from_rss(body: bytes, *, source: str) -> tuple[str, str] | None:
    """从 RSS/Atom 里挑第一条标题。脱敏不过就跳过，全被拦返回 None（宁缺毋滥）。"""
    if len(body) > FEED_BODY_MAX:
        return None
    head = body[:4096].decode("utf-8", errors="replace")
    if _FEED_FORBIDDEN.search(head):
        # 外部 XML 带 DOCTYPE/ENTITY 一律拒绝：标准库解析器不做实体扩展防护。
        return None
    try:
        root = ET.fromstring(body.decode("utf-8", errors="replace"))
    except ET.ParseError:
        return None
    titles: list[str] = []
    for item in root.iter():
        tag = item.tag.rsplit("}", 1)[-1]
        if tag in ("item", "entry"):
            for child in item:
                if child.tag.rsplit("}", 1)[-1] == "title":
                    titles.append(child.text or "")
    for raw in titles[:MAX_ITEMS_FROM_FEED]:
        title = _clean_title(raw)
        if len(title) >= 8 and _scrub_ok(title):
            host = re.sub(r"^https?://", "", source).split("/")[0]
            return title, host
    return None
