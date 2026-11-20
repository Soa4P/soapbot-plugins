"""group-digest: 群活跃度统计——「这个群最近聊得怎么样」的确定性回答。

改编自 AstrBot 市场群分析总结（432★，多平台）：上游机制是拉取群历史消息 → LLM 按
时间线摘要 → 定时主动推送日报/周报。按本仓边界收窄成它的**确定性残量**：

* **不读消息文本**：消息钩子只累计计数——按会话记「每天总量、当天分时段分布、
  发言成员与次数」（显示名 + 计数，滚动 14 天窗口）。上游的 LLM 时间线摘要需要
  模型面，插件没有；消息内容的回顾与摘要是记忆类插件（memory-weekly ／memweek）
  的职责面，本插件不重复。
* **不主动推送**：上游的定时日报/周报在 cron 里做不到——cron 调用者没有会话
  范围（宿主把 scope 钉死在 caller 上，空范围会被拒绝），而且「定时向群里推
  内容」的推送审批语义本来就是宿主侧待拍板的写面边界。所以概览是用户主动
  要的：／digest-now 随要随算。
* **统计即算**：不沉淀任何「摘要产物」——概览每次从计数现算，不写进记忆，
  不产生第二事实源。

**双模式存储**：进程内 storage 是同步 API，隔离 worker 的 _RemoteStorage 是
async，所有 storage 调用经 ``sget``/``sset`` 助手（isawaitable 则 await），两种
模式同一份代码；setup() 阶段不做任何 storage 访问（隔离模式 boot 期没有活跃
请求，回调无人应答）。

## 命令

| 命令 | 作用 |
|---|---|
| ／digest-now [天数] | 输出活跃概览（默认近 7 天，1..14） |
| ／digest-reset | 清空本会话的统计 |

## 能力与边界

| 能力 | 用途 | 收窄 |
|---|---|---|
| `message` | 钩子累计计数 | 只存计数、时段与显示名，不存任何消息文本 |
| `storage` | 每会话一份计数（加密） | 14 天滚动窗口，成员数上限 200 |
| `command` | ／digest-now、／digest-reset | 只读本会话（caller 的 session_key） |
"""

from __future__ import annotations

import datetime as dt
import inspect

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

STATS_KEY = "stats"          # session -> {"days": {date: {"total", "hours": {h: n}}}, "members": {uid: {...}}}
WINDOW_DAYS = 14             # 滚动窗口
MEMBER_CAP = 200             # 成员计数上限（超出按次数淘汰最低者）
DEFAULT_RANGE = 7            # ／digest-now 默认报告范围
TOP_HOURS = 3
TOP_MEMBERS = 5


async def sget(ctx: PluginContext, key: str, default=None):
    """双模式存储读：进程内 sync / 隔离 worker async，同一份插件代码。"""
    value = ctx.storage.get(key, default)
    if inspect.isawaitable(value):
        value = await value
    return value


async def sset(ctx: PluginContext, key: str, value) -> None:
    result = ctx.storage.set(key, value)
    if inspect.isawaitable(result):
        await result


def _prune(stats: dict) -> dict:
    """滚动窗口：删掉 14 天前的日计数与久未发言的成员。"""
    today = dt.date.today()
    cutoff = (today - dt.timedelta(days=WINDOW_DAYS - 1)).isoformat()
    days = stats.get("days")
    if isinstance(days, dict):
        stats["days"] = {d: v for d, v in days.items()
                         if isinstance(d, str) and d >= cutoff and isinstance(v, dict)}
    members = stats.get("members")
    if isinstance(members, dict):
        stats["members"] = {u: m for u, m in members.items()
                            if isinstance(m, dict) and str(m.get("last", "")) >= cutoff}
    return stats


def _bump_member(members: dict, uid: str, name: str) -> None:
    entry = members.get(uid)
    if isinstance(entry, dict):
        entry["count"] = int(entry.get("count", 0)) + 1
        entry["name"] = name or str(entry.get("name", ""))
        entry["last"] = dt.date.today().isoformat()
    else:
        members[uid] = {"name": name or uid, "count": 1, "last": dt.date.today().isoformat()}
    if len(members) > MEMBER_CAP:
        # 超上限淘汰次数最低者——统计面不是名册面，冷成员先走。
        victim = min(members, key=lambda u: int(members[u].get("count", 0)))
        del members[victim]


class GroupDigestPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)
        # setup 阶段不做 storage 访问（隔离模式 boot 期没有活跃请求）。

        @ctx.on_message
        async def observe(caller: CallerContext, text: str) -> None:
            session = caller.session_key
            if not session:
                return
            stats = await sget(ctx, STATS_KEY) or {}
            own = _prune(stats.get(session) if isinstance(stats.get(session), dict) else {})
            day = dt.date.today().isoformat()
            days = own.setdefault("days", {})
            entry = days.setdefault(day, {"total": 0, "hours": {}})
            entry["total"] = int(entry.get("total", 0)) + 1
            hours = entry.setdefault("hours", {})
            hour = str(dt.datetime.now().hour)
            hours[hour] = int(hours.get(hour, 0)) + 1
            members = own.setdefault("members", {})
            _bump_member(members, caller.user_id, caller.display_name)
            stats[session] = own
            await sset(ctx, STATS_KEY, stats)

        @ctx.command(
            "digest-now",
            description="输出本群活跃概览（日均、最活跃时段、活跃成员）",
            usage="/digest-now [天数]",
        )
        async def digest_now(caller: CallerContext, args: list[str]) -> str:
            session = caller.session_key
            rng = DEFAULT_RANGE
            if args:
                try:
                    rng = max(1, min(WINDOW_DAYS, int(args[0])))
                except ValueError:
                    return "天数要是一个数字（1..14），例如 ／digest-now 7。"
            stats = await sget(ctx, STATS_KEY) or {}
            own = stats.get(session) if isinstance(stats.get(session), dict) else {}
            days = own.get("days") if isinstance(own, dict) else None
            if not isinstance(days, dict) or not days:
                return "还没有攒下统计——说几句话再来。"
            today = dt.date.today()
            start = (today - dt.timedelta(days=rng - 1)).isoformat()
            picked = {d: v for d, v in sorted(days.items())
                      if d >= start and isinstance(v, dict)}
            total = sum(int(v.get("total", 0)) for v in picked.values())
            if total == 0:
                return f"近 {rng} 天没有记录到消息量。"
            span = max(len(picked), 1)
            # 时段聚合：窗口内各小时总量，取 top。
            hour_totals: dict[int, int] = {}
            for v in picked.values():
                for h, n in (v.get("hours") or {}).items():
                    try:
                        hour_totals[int(h)] = hour_totals.get(int(h), 0) + int(n)
                    except (TypeError, ValueError):
                        continue
            top_hours = sorted(hour_totals.items(), key=lambda kv: (-kv[1], kv[0]))[:TOP_HOURS]
            hour_part = "、".join(f"{h} 点（{n} 条）" for h, n in top_hours) or "暂无分布"
            members = own.get("members") if isinstance(own.get("members"), dict) else {}
            ranked = sorted(
                ((str(m.get("name") or u), int(m.get("count", 0)))
                 for u, m in members.items() if isinstance(m, dict)),
                key=lambda kv: (-kv[1], kv[0]),
            )
            active = sum(1 for _, c in ranked if c > 0)
            member_part = "、".join(f"{n}（{c}）" for n, c in ranked[:TOP_MEMBERS]) or "暂无成员计数"
            lines = [
                f"群活跃概览（近 {rng} 天，记录到 {len(picked)} 天、共 {total} 条消息）：",
                f"· 日均 {total // span} 条",
                f"· 最活跃时段：{hour_part}",
                f"· 发言成员 {active} 人；最活跃：{member_part}",
            ]
            today_n = int(picked.get(today.isoformat(), {}).get("total", 0))
            if today.isoformat() in picked:
                lines.append(f"· 今天已有 {today_n} 条")
            lines.append("说明：本插件只统计计数，不读消息内容；消息内容的回顾是记忆类插件的面。")
            return "\n".join(lines)

        @ctx.command(
            "digest-reset",
            description="清空本会话的活跃统计",
            usage="/digest-reset",
        )
        async def digest_reset(caller: CallerContext, args: list[str]) -> str:
            session = caller.session_key
            stats = await sget(ctx, STATS_KEY) or {}
            if session in stats:
                del stats[session]
                await sset(ctx, STATS_KEY, stats)
                return "本会话的活跃统计已清空，从零开始攒。"
            return "本会话本来就没有统计。"
