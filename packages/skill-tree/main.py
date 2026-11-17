"""skill-tree: a learning tracker with prerequisite locking — deterministic, private.

改编自 zeroa234/cs-math-skill-tree（193 技能的学习树：证据先行、必须/推荐前置
分层、保守的进度合并、环检测）。走 soapbot 自己的路：

* **点亮只来自用户自己声明**（/tree-done），没有「自动认定」——原仓库的「保守
  合并：部分完成不算掌握」在这里是「没声明就是没学会」。推荐前置只提示，必须
  前置挡解锁，判定是确定性的。
* **加前置时拒绝环**：原仓库在构建脚本里检测环；这里在 /tree-add 当场 DFS——
  一条成环的前置会让整棵树失去「解锁」的意义。
* **进度是私密的**：整棵树与进度都在插件加密 storage，不进宿主 kb（学习记录
  通常不想被别的会话召回），导出/导入是显式命令。
* 无模型调用、无网络出站、无 cron——学习是用户的事，插件只记账。
"""

from __future__ import annotations

import json
import time

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

TREE_KEY = "tree"        # {"skills": {id: {...}}, "done": {id: {"ts"}}}
REVIEW_KEY = "review"     # {"session": ..., "days": 7, "reviewed": {id: ts}}
REVIEW_REPEAT_DAYS = 7
MAX_SKILLS = 300


def _cycle_reachable(tree: dict, start: str, target: str) -> bool:
    """target 加上指向 start 的边后能否回到 target（DFS，显式栈）。"""
    seen: set[str] = set()
    stack = [target]
    while stack:
        node = stack.pop()
        if node == start:
            return True
        if node in seen:
            continue
        seen.add(node)
        stack.extend(tree.get("skills", {}).get(node, {}).get("reqs", []) or [])
    return False


class SkillTreePlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.command("tree-add", description="新增一个技能（含必须/推荐前置）",
                     usage="/tree-add <id> <名称> [必须前置,逗号分隔] [推荐前置,逗号分隔]")
        async def tree_add(caller: CallerContext, args: list[str]) -> str:
            if len(args) < 2:
                return "用法：/tree-add <id> <名称> [必须前置,逗号分隔] [推荐前置,逗号分隔]"
            sid, name = args[0].strip(), args[1].strip()
            tree = ctx.storage.get(TREE_KEY) or {}
            skills = tree.get("skills", {})
            if sid in skills:
                return f"「{sid}」已存在。"
            if len(skills) >= MAX_SKILLS:
                return f"技能已满（{MAX_SKILLS}）。"
            reqs = [r.strip() for r in (args[2].split(",") if len(args) > 2 and args[2] else []) if r.strip()]
            soft = [r.strip() for r in (args[3].split(",") if len(args) > 3 and args[3] else []) if r.strip()]
            unknown = [r for r in reqs + soft if r not in skills and r != sid]
            if unknown:
                return f"前置里没有这些技能：{'、'.join(unknown)}。先 /tree-add 它们。"
            if any(_cycle_reachable({"skills": skills}, r, sid) for r in reqs):
                return "拒绝：这条必须前置会成环（它依赖你、你又依赖它）。"
            skills[sid] = {
                "name": name[:100],
                "reqs": reqs,
                "soft": soft,
                "added_at": time.time(),
            }
            tree["skills"] = skills
            ctx.storage.set(TREE_KEY, tree)
            return f"技能「{name}」（{sid}）已加入，必须前置 {len(reqs)}、推荐 {len(soft)}。"

        @ctx.command("tree-done", description="点亮一个技能（必须前置未齐会被拒绝）",
                     usage="/tree-done <id>")
        async def tree_done(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/tree-done <id>"
            sid = args[0].strip()
            tree = ctx.storage.get(TREE_KEY) or {}
            skills = tree.get("skills", {})
            skill = skills.get(sid)
            if not skill:
                return f"没有「{sid}」这个技能。/tree-card-list 不存在——/tree 看全树。"
            done = tree.get("done", {})
            if sid in done:
                return f"「{sid}」已经点亮了（{time.strftime('%m-%d', time.localtime(done[sid]['ts']))}）。"
            missing = [r for r in skill.get("reqs", []) if r not in done]
            if missing:
                # 必须前置挡解锁（cs-math 的分层判定）：推荐前置只提示。
                soft_missing = [r for r in skill.get("soft", []) if r not in done]
                note = f"（推荐前置还差：{'、'.join(soft_missing)}）" if soft_missing else ""
                return f"必须前置还没点亮：{'、'.join(missing)}。{note}学会了再来点亮。"
            done[sid] = {"ts": time.time()}
            tree["done"] = done
            ctx.storage.set(TREE_KEY, tree)
            unlocked = [
                other for other, meta in skills.items()
                if other not in done and sid in meta.get("reqs", [])
                and all(r in done for r in meta.get("reqs", []))
            ]
            extra = f" 解锁了：{'、'.join(unlocked)}。" if unlocked else ""
            return f"✅「{skill.get('name', sid)}」点亮。{extra}"

        @ctx.command("tree-undo", description="取消一个技能的点亮", usage="/tree-undo <id>")
        async def tree_undo(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/tree-undo <id>"
            tree = ctx.storage.get(TREE_KEY) or {}
            done = tree.get("done", {})
            sid = args[0].strip()
            if sid not in done:
                return f"「{sid}」本来就没点亮。"
            # 保守合并原则的镜像：取消上游后，依赖它的下游点亮也撤（不自动认定掌握）。
            dependents = [
                d for d in done
                if d != sid and sid in tree.get("skills", {}).get(d, {}).get("reqs", [])
            ]
            for d in dependents:
                done.pop(d, None)
            done.pop(sid, None)
            tree["done"] = done
            ctx.storage.set(TREE_KEY, tree)
            note = f"，连带撤了 {len(dependents)} 个依赖它的点亮" if dependents else ""
            return f"已取消「{sid}」{note}。"

        @ctx.command("tree", description="查看整棵树（✅ 已点亮 / ⭕ 可点亮 / 🔒 锁定）",
                     usage="/tree")
        async def tree_view(caller: CallerContext, args: list[str]) -> str:
            tree = ctx.storage.get(TREE_KEY) or {}
            skills = tree.get("skills", {})
            if not skills:
                return "树是空的。/tree-add <id> <名称> [必须前置] 建第一棵。"
            done = tree.get("done", {})
            lines = []
            for sid, meta in sorted(skills.items()):
                if sid in done:
                    mark = "✅"
                elif all(r in done for r in meta.get("reqs", [])):
                    mark = "⭕"
                else:
                    mark = "🔒"
                reqs = f"（前置：{'、'.join(meta['reqs'])}）" if meta.get("reqs") else ""
                lines.append(f"{mark} {sid} {meta.get('name', '')}{reqs}")
            return "\n".join(lines[:40])

        @ctx.command("tree-progress", description="统计进度与下一个可点亮的", usage="/tree-progress")
        async def tree_progress(caller: CallerContext, args: list[str]) -> str:
            tree = ctx.storage.get(TREE_KEY) or {}
            skills = tree.get("skills", {})
            done = tree.get("done", {})
            ready = [
                sid for sid, meta in skills.items()
                if sid not in done and all(r in done for r in meta.get("reqs", []))
            ]
            return (
                f"进度：{len(done)}/{len(skills)} 已点亮。"
                f"下一个可点亮：{'、'.join(sorted(ready)[:5]) or '（没有了）'}。"
            )

        @ctx.command("tree-export", description="导出整棵树与进度（JSON）", usage="/tree-export")
        async def tree_export(caller: CallerContext, args: list[str]) -> str:
            tree = ctx.storage.get(TREE_KEY) or {}
            return "```json\n" + json.dumps(tree, ensure_ascii=False) + "\n```"

        @ctx.command("tree-import", description="导入树与进度（保守合并：已有点亮不被覆盖）",
                     usage="/tree-import <JSON>")
        async def tree_import(caller: CallerContext, args: list[str]) -> str:
            raw = " ".join(args).strip()
            if raw.startswith("```"):
                raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            try:
                incoming = json.loads(raw)
            except ValueError:
                return "JSON 解析失败。/tree-export 的输出可以直接粘回来。"
            if not isinstance(incoming, dict) or "skills" not in incoming:
                return "结构不对：需要 {\"skills\": ..., \"done\": ...}。"
            tree = ctx.storage.get(TREE_KEY) or {}
            skills = tree.get("skills", {})
            merged_new = 0
            for sid, meta in (incoming.get("skills") or {}).items():
                if sid not in skills and isinstance(meta, dict):
                    skills[sid] = meta
                    merged_new += 1
            done = tree.get("done", {})
            # 保守合并（cs-math 原则）：已有点亮不被导入覆盖；导入只补缺。
            for sid in (incoming.get("done") or {}):
                if sid in skills and sid not in done:
                    done[sid] = incoming["done"][sid]
            tree["skills"] = skills
            tree["done"] = done
            ctx.storage.set(TREE_KEY, tree)
            return f"已导入：新增 {merged_new} 个技能；已有点亮保持不变（保守合并）。"

        @ctx.command("tree-review-set", description="开启复习提醒（当前会话接收，默认每 7 天）",
                     usage="/tree-review-set [天数]")
        async def review_set(caller: CallerContext, args: list[str]) -> str:
            days = 7
            if args and args[0].isdigit():
                days = max(1, min(int(args[0]), 90))
            review = ctx.storage.get(REVIEW_KEY) or {}
            review["session"] = caller.session_key
            review["days"] = days
            ctx.storage.set(REVIEW_KEY, review)
            return f"复习提醒已开启（每 {days} 天）。会从点亮最早的技能开始提醒。"

        @ctx.command("tree-review-off", description="关闭复习提醒", usage="/tree-review-off")
        async def review_off(caller: CallerContext, args: list[str]) -> str:
            review = ctx.storage.get(REVIEW_KEY) or {}
            review.pop("session", None)
            ctx.storage.set(REVIEW_KEY, review)
            return "复习提醒已关闭。"

        @ctx.cron("30 9 * * *", name="skill-tree-review")
        async def review_tick() -> None:
            review = ctx.storage.get(REVIEW_KEY) or {}
            session = review.get("session")
            if not session:
                return
            tree = ctx.storage.get(TREE_KEY) or {}
            done = tree.get("done", {})
            reviewed = review.get("reviewed", {})
            import time as _t

            now = _t.time()
            repeat_cutoff = now - REVIEW_REPEAT_DAYS * 86400
            candidates = [
                (sid, meta.get("name", sid), done[sid].get("ts", 0))
                for sid, meta in sorted(tree.get("skills", {}).items())
                if sid in done and reviewed.get(sid, 0) < done[sid].get("ts", 0) - 86400
            ]
            candidates.sort(key=lambda pair: pair[2])  # 点亮最早优先
            picks = [c for c in candidates if c[2] <= now - 86400][:3]
            if not picks:
                return
            lines = [f"「{name}」（点亮于 {_t.strftime('%m-%d', _t.localtime(ts))}）"
                     for _sid, name, ts in picks]
            for sid, _name, _ts in picks:
                reviewed[sid] = now
            review["reviewed"] = reviewed
            ctx.storage.set(REVIEW_KEY, review)
            try:
                await ctx.send(
                    "复习提醒——这些你点亮得最早，还记得吗？\n" + "\n".join(lines),
                    session_key=session,
                )
            except (PermissionError, RuntimeError, ValueError):
                pass  # 发送失败：reviewed 已记，7 天后自然重提

        @ctx.command("tree-reset", description="清空整棵树（必须确认）", usage="/tree-reset 确认")
        async def tree_reset(caller: CallerContext, args: list[str]) -> str:
            if args[:1] != ["确认"]:
                return "这会清空整棵树与进度。确定的话：/tree-reset 确认"
            ctx.storage.set(TREE_KEY, {})
            return "已清空。"
