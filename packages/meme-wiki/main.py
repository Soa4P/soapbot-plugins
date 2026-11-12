"""meme-wiki: a slang/meme dictionary, adapted from thawline/dsh-memewiki.

原版的灵魂是「预查注入」——在模型看到消息之前把词条塞进上下文。soapbot 的插件
面还没有这个注入点（message 钩子只读，reply_ready 只能改写回复文本），所以本版
做的是同一份词库的**命令式查询**：问梗的人自己敲 /meme <词>，查询结果以纯文本
返回。原版的其余机制按 soapbot 边界重排：

* 词库同步走 cnmeme.wiki 公开 API（无凭据 GET），只取 summary/aliases/tags 白名单
  字段，上限 2000 条——原版的「只补新增、不覆盖手改」语义保留：本地手改过的标题
  同步时跳过。
* 词条存插件加密 storage。原版用 node:sqlite；soapbot 的 storage 是加密键值，
  键即标题。
* 反馈上传通道**不做**：原版要领 90 天凭证、替用户提交到站内审核——涉及账号
  凭据的第三方交互，soapbot 的扩展边界是「凭据类一律宿主持有」，而宿主没有
  这个面。列 ADAPTATION.md 待能力项。

查询匹配照原版的双向思路：标题精确命中优先；否则标题含查询词或查询词含标题
（查询 ≥2 字），别名同样参与。命中多条时列出候选。
"""

from __future__ import annotations

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

WIKI_KEY = "wiki_entries"
LOCAL_KEY = "local_only"
MAX_ENTRIES = 2000
SYNC_URL = "https://cnmeme.wiki/api/v1/public/memes?limit=100&offset="
UA = "soapbot-meme-wiki/0.1 (+https://github.com/Soa4P/soapbot-plugins)"

# storage 键：wiki_entries = 同步来的词条；local_only = 手动添加、同步不覆盖。


class MemeWikiPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.command("meme", description="查一个网络梗", usage="/meme <词> [别名也算]")
        async def meme(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/meme <词>"
            query = " ".join(args).strip()
            wiki = ctx.storage.get(WIKI_KEY) or {}
            local = ctx.storage.get(LOCAL_KEY) or {}
            hits = _match(query, {**wiki, **local})
            if not hits:
                return f"词库里没有「{query}」。/meme-add <词> <解释> 自己补一条。"
            if len(hits) == 1:
                entry = hits[0][1]
                alias = "、".join(entry.get("aliases") or []) or "—"
                return f"「{hits[0][0]}」：{entry.get('summary', '')}\n（别名：{alias}）"
            names = "、".join(title for title, _ in hits[:8])
            return f"「{query}」命中 {len(hits)} 条：{names}。用更精确的词再查。"

        @ctx.command("meme-add", description="手动补一条梗（同步时不被覆盖）",
                     usage="/meme-add <词> <解释>")
        async def meme_add(caller: CallerContext, args: list[str]) -> str:
            if len(args) < 2:
                return "用法：/meme-add <词> <解释>"
            title = args[0].strip()
            summary = " ".join(args[1:]).strip()
            if not title or len(summary) < 2:
                return "词条名和解释都要有。"
            local = ctx.storage.get(LOCAL_KEY) or {}
            if len(local) + len(ctx.storage.get(WIKI_KEY) or {}) >= MAX_ENTRIES:
                return f"词库已到 {MAX_ENTRIES} 条上限。"
            local[title] = {"summary": summary[:800], "aliases": [], "manual": True}
            ctx.storage.set(LOCAL_KEY, local)
            return f"已收录「{title}」。同步词库不会覆盖手动词条。"

        @ctx.command("meme-rm", description="删一条词条", usage="/meme-rm <词>")
        async def meme_rm(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/meme-rm <词>"
            title = args[0].strip()
            local = ctx.storage.get(LOCAL_KEY) or {}
            wiki = ctx.storage.get(WIKI_KEY) or {}
            if title in local:
                del local[title]
                ctx.storage.set(LOCAL_KEY, local)
                return f"已删手动词条「{title}」。"
            if title in wiki:
                del wiki[title]
                ctx.storage.set(WIKI_KEY, wiki)
                return f"已删同步词条「{title}」（下次同步会带回来）。"
            return f"词库里没有「{title}」。"

        @ctx.command("meme-sync", description="从梗维基同步词条（只补新增，不覆盖手改）",
                     usage="/meme-sync [页数，默认 5]")
        async def meme_sync(caller: CallerContext, args: list[str]) -> str:
            try:
                pages = max(1, min(int(args[0]) if args else 5, 20))
            except ValueError:
                return "用法：/meme-sync [页数]"
            wiki = ctx.storage.get(WIKI_KEY) or {}
            local = ctx.storage.get(LOCAL_KEY) or {}
            added = 0
            for page in range(pages):
                offset = page * 100
                try:
                    status, body = await ctx.http.fetch(
                        f"{SYNC_URL}{offset}", headers={"User-Agent": UA_MEME}
                    )
                except (PermissionError, RuntimeError, ValueError):
                    return f"第 {page + 1} 页抓取失败（域名可能不在 http 允许列表）。已入库 {added} 条。"
                if status != 200:
                    return f"cnmeme.wiki 返回 HTTP {status}。已入库 {added} 条。"
                import json as _json

                try:
                    payload = _json.loads(body.decode("utf-8", errors="replace"))
                except ValueError:
                    return "cnmeme.wiki 返回了无法解析的内容。"
                items = payload.get("items") or []
                if not isinstance(items, list) or not items:
                    break
                for row in items:
                    if not isinstance(row, dict):
                        continue
                    title = str(row.get("title") or "").strip()
                    if not title or title in local or title in wiki:
                        continue  # 只补新增；手改的不覆盖（照原版语义）
                    if len(wiki) + len(local) >= MAX_ENTRIES:
                        break
                    wiki[title] = {
                        "summary": str(row.get("summary") or "")[:800],
                        "aliases": [str(a) for a in (row.get("aliases") or [])][:10],
                        "tags": [str(t) for t in (row.get("tags") or [])][:10],
                    }
                    added += 1
                ctx.storage.set(WIKI_KEY, wiki)
                if len(wiki) + len(local) >= MAX_ENTRIES:
                    break
            return f"同步完成：新入库 {added} 条，词库共 {len(wiki) + len(local)} 条。"

        @ctx.command("meme-count", description="看词库规模", usage="/meme-count")
        async def meme_count(caller: CallerContext, args: list[str]) -> str:
            wiki = ctx.storage.get(WIKI_KEY) or {}
            local = ctx.storage.get(LOCAL_KEY) or {}
            return f"同步词条 {len(wiki)} 条，手动词条 {len(local)} 条。"


UA_MEME = "soapbot-meme-wiki/0.1 (+https://github.com/Soa4P/soapbot-plugins)"


def _match(query: str, pool: dict) -> list[tuple[str, dict]]:
    """双向匹配：标题精确 > 标题含查询 > 查询含标题/别名（查询 ≥2 字）。"""
    exact = []
    contains = []
    contained = []
    q = query.strip()
    for title, entry in pool.items():
        aliases = [str(a) for a in (entry.get("aliases") or [])]
        if title == q or q in aliases:
            exact.append((title, entry))
            continue
        if q in title:
            contains.append((title, entry))
        elif len(q) >= 2 and (q in " ".join(aliases) or title in q):
            contained.append((title, entry))
    return exact + contains + contained
