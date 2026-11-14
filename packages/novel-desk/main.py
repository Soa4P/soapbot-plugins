"""novel-desk: a private novel-writing desk — character cards, world rules, drafts.

改编自 mistnest/dsh-cuigengji-plugin（小说工作台：设定按需检索、章节草稿、字数
统计、恢复旧稿）。原仓库的核心机制「AI 通过工具调用按需读设定」依赖宿主的模型
工具面——soapbot 插件没有这个面，所以本包走确定性版：**作者自己用命令管设定与
草稿**，「模型按需读设定」登记为宿主 tools/MCP 设计输入（ADAPTATION.md 第七批）。

形态选择：

* **storage-only**：设定卡与章节草稿都是私密的，存插件加密 storage，不进宿主
  知识库（kb 是共享检索面，小说设定通常不想被别的会话召回）；
* **检索是子串匹配**：`/nd-search` 在所有设定卡里找关键词，命中返回卡名与行——
  原仓库的向量/语义检索需要模型或嵌入面，这里如实降级为精确子串；
* **草稿带历史**：每次保存把上一版压进历史（保最近 3 版），`/nd-ch-undo` 恢复；
  字数统计是 `len`，不是 token——如实标注。

冰山比喻保留：正文只是表面，水面下的设定供检索；但水面下的人是作者，不是模型。
"""

from __future__ import annotations

import time

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

CARDS_KEY = "cards"        # name -> {"text", "updated_at"}
CH_KEY = "chapters"        # title -> {"text", "words", "updated_at", "history": [...]}
HISTORY_KEEP = 3
CHUNK_CHARS = 1500
MAX_CARDS = 200
MAX_CHAPTERS = 100
TEXT_MAX_CHARS = 20000


class NovelDeskPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.command("nd-card", description="新建/更新一张设定卡（人物/世界观/规则）",
                     usage="/nd-card <名字> <内容...>")
        async def nd_card(caller: CallerContext, args: list[str]) -> str:
            if len(args) < 2:
                return "用法：/nd-card <名字> <内容...>"
            name = args[0].strip()
            text = " ".join(args[1:]).strip()[:TEXT_MAX_CHARS]
            cards = ctx.storage.get(CARDS_KEY) or {}
            if name not in cards and len(cards) >= MAX_CARDS:
                return f"设定卡已满（{MAX_CARDS} 张），先 /nd-card-del 腾位置。"
            cards[name] = {"text": text, "updated_at": time.time()}
            ctx.storage.set(CARDS_KEY, cards)
            return f"设定卡「{name}」已保存（{len(text)} 字）。/nd-search 可按关键词检索。"

        @ctx.command("nd-card-list", description="列出全部设定卡名", usage="/nd-card-list")
        async def nd_card_list(caller: CallerContext, args: list[str]) -> str:
            cards = ctx.storage.get(CARDS_KEY) or {}
            if not cards:
                return "还没有设定卡。/nd-card <名字> <内容> 建第一张。"
            return "设定卡：" + "、".join(sorted(cards.keys()))

        @ctx.command("nd-card-show", description="查看一张设定卡", usage="/nd-card-show <名字>")
        async def nd_card_show(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/nd-card-show <名字>"
            cards = ctx.storage.get(CARDS_KEY) or {}
            entry = cards.get(args[0].strip())
            if not entry:
                return f"没有「{args[0].strip()}」这张卡。/nd-card-list 列出全部。"
            return f"【{args[0].strip()}】\n{entry['text']}"

        @ctx.command("nd-card-del", description="删除一张设定卡", usage="/nd-card-del <名字>")
        async def nd_card_del(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/nd-card-del <名字>"
            cards = ctx.storage.get(CARDS_KEY) or {}
            name = args[0].strip()
            if name not in cards:
                return f"没有「{name}」这张卡。"
            del cards[name]
            ctx.storage.set(CARDS_KEY, cards)
            return f"已删除「{name}」。"

        @ctx.command("nd-search", description="在全部设定卡里按关键词检索", usage="/nd-search <关键词>")
        async def nd_search(caller: CallerContext, args: list[str]) -> str:
            """「资料按需读取」的确定性版：子串匹配，如实降级（无语义检索）。"""
            if not args:
                return "用法：/nd-search <关键词>"
            needle = args[0].strip()
            cards = ctx.storage.get(CARDS_KEY) or {}
            lines: list[str] = []
            for name, entry in sorted(cards.items()):
                text = str(entry.get("text", ""))
                for line in text.splitlines() + ([text] if "\n" not in text else []):
                    if needle in line:
                        lines.append(f"【{name}】{line.strip()[:120]}")
                        break
            if not lines:
                return f"设定卡里没有提到「{needle}」。"
            return "\n".join(lines[:10])

        @ctx.command("nd-ch", description="新建/追加章节草稿（自动保存上一版）",
                     usage="/nd-ch <章节名> <正文...>")
        async def nd_ch(caller: CallerContext, args: list[str]) -> str:
            if len(args) < 2:
                return "用法：/nd-ch <章节名> <正文...>"
            title = args[0].strip()
            chunk = " ".join(args[1:]).strip()[:CHUNK_CHARS]
            chapters = ctx.storage.get(CH_KEY) or {}
            if title not in chapters and len(chapters) >= MAX_CHAPTERS:
                return f"章节已满（{MAX_CHAPTERS} 章），先 /nd-ch-del 腾位置。"
            entry = chapters.get(title) or {"text": "", "history": []}
            history = list(entry.get("history") or [])
            if entry.get("text"):
                history = (history + [entry["text"]])[-HISTORY_KEEP:]
            text = (str(entry.get("text", "")) + ("\n" if entry.get("text") else "") + chunk)
            if len(text) > TEXT_MAX_CHARS:
                return f"这一章已经太长（{len(text)} 字，上限 {TEXT_MAX_CHARS}）。开新章节吧。"
            chapters[title] = {
                "text": text,
                "words": len(text),
                "updated_at": time.time(),
                "history": history,
            }
            ctx.storage.set(CH_KEY, chapters)
            return f"「{title}」已保存：{len(text)} 字（本次 +{len(chunk)}，历史 {len(history)} 版）。"

        @ctx.command("nd-ch-list", description="列出章节与字数", usage="/nd-ch-list")
        async def nd_ch_list(caller: CallerContext, args: list[str]) -> str:
            chapters = ctx.storage.get(CH_KEY) or {}
            if not chapters:
                return "还没有章节。/nd-ch <章节名> <正文> 开第一章。"
            total = sum(int(c.get("words", 0)) for c in chapters.values())
            lines = [f"· {t}（{c.get('words', 0)} 字）" for t, c in sorted(chapters.items())]
            lines.append(f"合计 {total} 字，{len(chapters)} 章。")
            return "\n".join(lines)

        @ctx.command("nd-ch-show", description="查看章节（默认开头 600 字）",
                     usage="/nd-ch-show <章节名> [字数]")
        async def nd_ch_show(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/nd-ch-show <章节名> [字数]"
            chapters = ctx.storage.get(CH_KEY) or {}
            entry = chapters.get(args[0].strip())
            if not entry:
                return f"没有「{args[0].strip()}」这一章。"
            limit = 600
            if len(args) > 1 and args[1].isdigit():
                limit = max(50, min(int(args[1]), 4000))
            text = str(entry.get("text", ""))
            head = text[:limit]
            more = f"\n…（共 {len(text)} 字）" if len(text) > limit else ""
            return f"【{args[0].strip()}】\n{head}{more}"

        @ctx.command("nd-ch-undo", description="恢复章节的上一版", usage="/nd-ch-undo <章节名>")
        async def nd_ch_undo(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/nd-ch-undo <章节名>"
            chapters = ctx.storage.get(CH_KEY) or {}
            entry = chapters.get(args[0].strip())
            if not entry:
                return f"没有「{args[0].strip()}」这一章。"
            history = list(entry.get("history") or [])
            if not history:
                return "这一章没有历史版本。"
            current = str(entry.get("text", ""))
            restored = history.pop()
            entry["text"] = restored
            entry["words"] = len(restored)
            entry["history"] = history + [current]
            chapters[args[0].strip()] = entry
            ctx.storage.set(CH_KEY, chapters)
            return f"已恢复上一版（{len(restored)} 字）；当前版进了历史。"

        @ctx.command("nd-ch-del", description="删除一整个章节（含历史）",
                     usage="/nd-ch-del <章节名>")
        async def nd_ch_del(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/nd-ch-del <章节名>"
            chapters = ctx.storage.get(CH_KEY) or {}
            title = args[0].strip()
            if title not in chapters:
                return f"没有「{title}」这一章。"
            del chapters[title]
            ctx.storage.set(CH_KEY, chapters)
            return f"已删除「{title}」（含全部历史）。"

        @ctx.command("nd-stats", description="全库字数统计", usage="/nd-stats")
        async def nd_stats(caller: CallerContext, args: list[str]) -> str:
            cards = ctx.storage.get(CARDS_KEY) or {}
            chapters = ctx.storage.get(CH_KEY) or {}
            card_words = sum(len(str(c.get("text", ""))) for c in cards.values())
            ch_words = sum(int(c.get("words", 0)) for c in chapters.values())
            return (
                f"设定卡 {len(cards)} 张（{card_words} 字），章节 {len(chapters)} 章"
                f"（{ch_words} 字），合计 {card_words + ch_words} 字。"
                "字数是字符数不是 token，如实标注。"
            )
