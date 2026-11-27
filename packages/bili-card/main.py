"""bili-card: 群里有人贴 B 站链接时，回一张确定性的信息卡。

改编自 AstrBot 市场 ``analysis_bilibili``（机制参照，不是移植）：上游在插件侧抓
view API、带封面图、无去重。本仓形态的差异在三条边界上：

* **解析在宿主**：插件只发一个目标字符串（BV 号、av 号或链接）给宿主新增的
  ``bilibili.view`` surface——b23.tv 短链只读 Location 头、限流、字段白名单都在
  宿主侧完成。插件拿到的没有封面 URL（hdslb 热链，给出去就是引诱插件自己抓图）、
  没有播放地址、没有 UP 主 mid。
* **四层限流**：会话每分钟上限（本插件）→ 会话 TTL 去重（本插件，30 分钟内同一
  链接只卡一次）→ surface 的能力门与审计（宿主）→ L2 client 的最小间隔与每分钟
  预算（宿主）。一层是礼仪，三层是边界。
* **失败不是事件**：钩子里任何一步失败都静默跳过——不 reply、不抛、不刷日志。
  与直播播报同一哲学：没人问它要错误报告，下一张卡自然会出现。

这是 bili 只读面的「被动」一半：／bili 命令管「有人问」，本插件管「有人贴」；
直播播报管「状态变化」。三者共用同一个宿主 L2 client，不新增出站路径。

**双模式存储**：进程内 sync、隔离 worker async，所有 storage 调用经 ``sget``/``sset``
助手；setup() 阶段不做任何 storage 访问（隔离模式 boot 期没有活跃请求）。
"""

from __future__ import annotations

import inspect
import re
import time

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

#: 目标提取——只认文本里的四种形态。小程序卡片的 JSON 不进正则面（宿主都不解析）。
BV_RE = re.compile(r"BV[0-9A-Za-z]{10}")
AV_RE = re.compile(r"\bav(\d{1,15})\b")
BILI_URL_RE = re.compile(r"(?:www\.)?bilibili\.com/video/(BV[0-9A-Za-z]{10}|av\d+)")
SHORT_RE = re.compile(r"https?://b23\.tv/[0-9A-Za-z]+")

#: 每会话每分钟最多几张卡。防刷屏是礼仪；真正的边界在宿主两层限流上。
PER_MINUTE = 2
#: 同一链接的静默窗（秒）。不同链接指向同一视频会各卡一次——TTL 与每分钟上限
#: 一起兜住总量，dedup 键用「提取到的目标」是因为解析前的我们只知道这个。
DEDUP_TTL = 30 * 60

STORE_KEY = "cards"

#: 每次钩子最多提取的目标数。一条消息塞十个链接只卡第一个——洪流的形状就是列表。
MAX_PER_MESSAGE = 1


async def sget(ctx: PluginContext, key: str, default=None):
    value = ctx.storage.get(key, default)
    if inspect.isawaitable(value):
        value = await value
    return value


async def sset(ctx: PluginContext, key: str, value) -> None:
    result = ctx.storage.set(key, value)
    if inspect.isawaitable(result):
        await result


def extract_targets(text: str) -> list[str]:
    """从消息文本提取 B 站目标，按出现顺序去重。链接优先于裸 id。"""
    out: list[str] = []
    for pattern in (BILI_URL_RE, SHORT_RE, BV_RE, AV_RE):
        for match in pattern.finditer(text or ""):
            target = match.group(1) if pattern is AV_RE else match.group(0)
            if target not in out:
                out.append(target)
    return out


def _human(value: int) -> str:
    if value >= 100_000_000:
        return f"{value / 100_000_000:.1f}亿"
    if value >= 10_000:
        return f"{value / 10_000:.1f}万"
    return str(value)


def render_card(info: dict) -> str:
    """facts only——标题、UP 主、时长、公开数据、原链。与直播播报同一克制。"""
    stat = info.get("stat") or {}
    lines = [f"《{info.get('title') or info.get('bvid', '')}》"]
    facts = " · ".join(
        part
        for part in (
            f"UP 主：{info.get('author', '')}" if info.get("author") else "",
            info.get("category", ""),
            info.get("duration_text", ""),
        )
        if part
    )
    if facts:
        lines.append(facts)
    numbers = " · ".join(
        part
        for part in (
            f"播放 {_human(int(stat.get('view', 0) or 0))}",
            f"弹幕 {_human(int(stat.get('danmaku', 0) or 0))}",
            f"点赞 {_human(int(stat.get('like', 0) or 0))}",
        )
        if stat
    )
    if numbers:
        lines.append(numbers)
    if info.get("url"):
        lines.append(str(info["url"]))
    return "\n".join(lines)


def _seen_target(seen: dict, session: str, target: str, now: float) -> bool:
    """True = 刚看到过（应静默）。读时顺手清理过期项。"""
    own = seen.get(session)
    if not isinstance(own, dict):
        return False
    expired = [k for k, ts in own.items() if not isinstance(ts, (int, float)) or now - ts > DEDUP_TTL]
    for key in expired:
        own.pop(key, None)
    return target in own


def _remember_target(seen: dict, session: str, target: str, now: float) -> dict:
    own = seen.setdefault(session, {})
    own[target] = now
    # 全局兜底：会话键再多也不超过 100 个，防无界增长。
    if len(seen) > 100:
        for key in sorted(seen)[: len(seen) - 100]:
            seen.pop(key, None)
    return seen


class BiliCardPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)
        # setup 阶段不做 storage 访问（隔离模式 boot 期没有活跃请求）。
        self._minute_stamps: dict[str, list[float]] = {}
        self._minute_window = 60.0

        def _over_session_budget(session: str) -> bool:
            now = time.monotonic()
            stamps = [ts for ts in self._minute_stamps.get(session, []) if now - ts < self._minute_window]
            self._minute_stamps[session] = stamps
            return len(stamps) >= PER_MINUTE

        def _stamp_session(session: str) -> None:
            self._minute_stamps.setdefault(session, []).append(time.monotonic())

        @ctx.on_message
        async def card(caller: CallerContext, text: str) -> None:
            session = caller.session_key
            if not session:
                return
            targets = extract_targets(text)[:MAX_PER_MESSAGE]
            if not targets:
                return
            if _over_session_budget(session):
                return
            now = time.time()
            seen = await sget(ctx, STORE_KEY) or {}
            target = targets[0]
            if _seen_target(seen if isinstance(seen, dict) else {}, session, target, now):
                return
            try:
                info = await ctx.bili_view(target)
            except Exception:
                # 限流、解析失败、网络抖动——失败不是事件，下一张卡自然会出现。
                return
            if not isinstance(info, dict) or not info.get("bvid"):
                return
            await ctx.send(render_card(info), session_key=session)
            _stamp_session(session)
            await sset(ctx, STORE_KEY, _remember_target(seen if isinstance(seen, dict) else {}, session, target, now))
