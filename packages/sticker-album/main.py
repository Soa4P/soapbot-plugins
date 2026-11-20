"""sticker-album: the bot's own sticker packs, stored, searched and picked by AI.

存储走插件加密 storage（base64 + 标签 + 包名，按包分键 ``album.<包>``），发送走
宿主的 ``send_media`` 能力面（ledger + 独立审批 + 4 MiB 双边界上限）。1.0.0 吸收
AstrBot 市场表情包管理器（207k 下载）的机制，按本仓边界收窄：

* **多包管理**：每张图带包名，按包过滤/发送/统计（0.2.0 单相册自动迁移进
  default 包）；
* **AI 选图（tool 面）**：``meme-pick`` 注册为模型工具——模型根据对话语境从图库
  选表情并直接发送到当前会话。选图打分是确定性的（标签与语境词重叠 + 包内轮换），
  模型不接触图片本体（base64 不回传模型），发送走 send_media 的 ledger 与审批链；
* **SHA-256 去重**：同一张图重复入库会被拒绝（内容哈希标记）；
* 两条入库路不变：URL 抓取（/meme-save）与聊天里顺手收藏（/meme-keep）；
* 上限 100 张/包；标签必须显式。

**双模式存储注意**：进程内 storage 是同步 API，隔离 worker 的 _RemoteStorage 是
async（且无 keys()——包枚举走 ``packs`` 注册表键），并有一个硬约束：**setup()
阶段没有活跃请求，storage 回调无人应答**——所有 storage 访问必须发生在命令/
工具调用期（invoke 管道内），迁移因此是惰性的（首次访问 default 包时执行）。
本插件所有 storage 调用经 ``sget``/``sset``/``sdel`` 助手（isawaitable 则
await），进程内与隔离两种模式同一份代码。

## 命令

| 命令 | 作用 |
|---|---|
| `/meme-save <URL> <标签...>` | 抓图入默认包 |
| `/meme-save-in <包> <URL> <标签...>` | 抓图入指定包 |
| `/meme-keep <标签...>` | 收刚收到的消息里的图 |
| `/meme-list [包] [标签]` | 列出（可按包与标签过滤） |
| `/meme-search <词>` | 按关键词模糊搜标签与包名 |
| `/meme-send [包/]标签 [说明]` | 发一张（可指定包） |
| `/meme-packs` | 列出所有包与张数 |
| `/meme-stats` | 相册统计（包与标签分布） |
| `/meme-rm <编号>` | 删一张 |
| `/meme-forget [包] 确认` | 清空（可只清一个包） |

## 模型工具

| 工具 | 作用 |
|---|---|
| `meme-pick` | 模型按对话语境选一张表情并发送到当前会话（确定性打分，不回传图片本体） |

## 能力与边界

| 能力 | 用途 | 收窄 |
|---|---|---|
| `media_observe` | 取当前消息的图片引用 | 宿主只给 http(s) 引用，会话由宿主绑定 |
| `http` | 只 GET /meme-save 传入的图 URL | 宿主白名单 + 禁重定向 + 2MiB 响应上限 |
| `storage` | base64 图 + 标签 + 包名（加密） | 不出插件命名空间，100 张/包 |
| `send_media` | 发图 | 宿主 ledger + 独立审批 + 4MiB 上限 |
| `tool` | 模型选图（meme-pick） | 模型不接触图片本体；发送走 send_media 全链 |
"""

from __future__ import annotations

import base64
import hashlib
import re
import time
import uuid
from collections import Counter

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

MAX_ITEMS_PER_PACK = 100
DEFAULT_PACK = "default"
PACK_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
UA = "soapbot-sticker-album/1.0 (+https://github.com/Soa4P/soapbot-plugins)"
ALBUM_KEY = "album"      # 0.2.0 旧键；惰性迁移到 album.default
PACKS_KEY = "packs"      # 包名注册表（隔离 worker 无 storage.keys()，枚举靠它）


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


async def sdel(ctx: PluginContext, key: str) -> None:
    result = ctx.storage.delete(key)
    if inspect.isawaitable(result):
        await result


class StickerAlbumPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)
        # 注意：setup 阶段不做任何 storage 访问——隔离模式的 storage 回调只在
        # invoke 期可用（boot 期没有活跃请求），迁移与枚举全部惰性化到命令期。

        @ctx.command("meme-save", description="抓一张图进默认包并打标签",
                     usage="/meme-save <图片URL> <标签...>")
        async def meme_save(caller: CallerContext, args: list[str]) -> str:
            if len(args) < 2:
                return "用法：/meme-save <图片URL> <标签...>"
            url = args[0]
            if not url.lower().startswith(("http://", "https://")):
                return "URL 要以 http(s):// 开头。"
            tags = [t.strip() for t in args[1:] if t.strip()]
            if not tags:
                return "至少要有一个标签——没标签的图检索不到。"
            return await self._fetch_and_store(ctx, url, tags, DEFAULT_PACK)

        @ctx.command("meme-save-in", description="抓一张图进指定包",
                     usage="/meme-save-in <包> <图片URL> <标签...>")
        async def meme_save_in(caller: CallerContext, args: list[str]) -> str:
            if len(args) < 3:
                return "用法：/meme-save-in <包> <图片URL> <标签...>"
            pack, url = args[0], args[1]
            if not PACK_RE.match(pack):
                return "包名只能用字母数字与 - _（≤32 字符）。"
            tags = [t.strip() for t in args[2:] if t.strip()]
            if not tags:
                return "至少要有一个标签。"
            return await self._fetch_and_store(ctx, url, tags, pack)

        @ctx.command("meme-keep", description="把刚收到的消息里的图片顺手收进默认包",
                     usage="/meme-keep <标签...>")
        async def meme_keep(caller: CallerContext, args: list[str]) -> str:
            tags = [t.strip() for t in args if t.strip()]
            if not tags:
                return "用法：/meme-keep <标签...>"
            refs = await ctx.observe_images()
            if not refs:
                return "刚才那条消息里没有图片。"
            album = await self._album(ctx, DEFAULT_PACK)
            kept, skipped = 0, 0
            for ref in refs:
                url = str(ref.get("url") or "")
                if not url:
                    continue
                try:
                    status, body = await ctx.http.fetch(url, headers={"User-Agent": UA})
                except (PermissionError, RuntimeError, ValueError):
                    continue  # 单张失败不放弃其余的
                if status != 200 or len(body) > 3 * 1024 * 1024:
                    continue
                if self._dup_of(album, body):
                    skipped += 1
                    continue
                if len(album) >= MAX_ITEMS_PER_PACK:
                    return f"默认包已满（{MAX_ITEMS_PER_PACK} 张）。已收 {kept} 张，{skipped} 张重复跳过。"
                self._put(album, body, tags, DEFAULT_PACK)
                kept += 1
            await sset(ctx, self._key(DEFAULT_PACK), album)
            return f"已收 {kept} 张（默认包共 {len(album)} 张）。"

        @ctx.command("meme-list", description="列出相册（可按包与标签过滤）",
                     usage="/meme-list [包] [标签]")
        async def meme_list(caller: CallerContext, args: list[str]) -> str:
            pack, tag = (args[0], args[1]) if len(args) >= 2 else (args[0] if args else "", "")
            album = await self._album(ctx, pack or DEFAULT_PACK)
            if not album:
                which = f"包「{pack}」" if pack else "相册"
                return f"{which}是空的。"
            lines = []
            for item_id, item in album.items():
                tags = item.get("tags") or []
                if tag and tag not in tags:
                    continue
                lines.append(f"#{item_id} [{item.get('pack', DEFAULT_PACK)}] [{'、'.join(tags)}]")
            return "\n".join(lines[:40]) or "没有匹配的图。"

        @ctx.command("meme-search", description="按关键词模糊搜标签与包名",
                     usage="/meme-search <词>")
        async def meme_search(caller: CallerContext, args: list[str]) -> str:
            word = args[0].strip() if args else ""
            if not word:
                return "用法：/meme-search <词>"
            hits = await self._search(ctx, word)
            if not hits:
                return f"没有带「{word}」的图。"
            return "\n".join(
                f"#{iid} [{it.get('pack', DEFAULT_PACK)}] [{'、'.join(it.get('tags') or [])}]"
                for iid, it in hits[:40]
            )

        @ctx.command("meme-send", description="按 [包/]标签 发一张到当前会话",
                     usage="/meme-send [包/]标签 [说明]")
        async def meme_send(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/meme-send [包/]标签 [说明]"
            target = args[0]
            caption = " ".join(args[1:])
            pack, _, tag = target.partition("/")
            album = await self._album(ctx, pack or DEFAULT_PACK)
            candidates = [(iid, it) for iid, it in album.items() if tag in (it.get("tags") or [])]
            if not candidates:
                return f"没有带「{tag}」标签的图。"
            _item_id, item = self._rotate(candidates)
            await ctx.send_media(session_key=caller.session_key, image_b64=item["b64"], caption=caption)
            return ""

        @ctx.command("meme-packs", description="列出所有包与张数", usage="/meme-packs")
        async def meme_packs(caller: CallerContext, args: list[str]) -> str:
            packs = await self._packs(ctx)
            if not packs:
                return "相册是空的。"
            return "\n".join(f"{name}: {count} 张" for name, count in packs)

        @ctx.command("meme-stats", description="相册统计（包与标签分布）", usage="/meme-stats")
        async def meme_stats(caller: CallerContext, args: list[str]) -> str:
            total, packs, tags = 0, [], Counter()
            for pack, count in await self._packs(ctx):
                total += count
                packs.append((pack, count))
                album = await self._album(ctx, pack)
                for item in album.values():
                    for tag in item.get("tags") or []:
                        tags[tag] += 1
            if not total:
                return "相册是空的。"
            pack_lines = "\n".join(f"  {name}: {count} 张" for name, count in packs)
            top_tags = "、".join(f"{t}×{n}" for t, n in tags.most_common(8))
            return f"共 {total} 张。\n按包：\n{pack_lines}\n高频标签：{top_tags}"

        @ctx.command("meme-rm", description="按编号删一张", usage="/meme-rm <编号>")
        async def meme_rm(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/meme-rm <编号>（/meme-list 里方括号前的那个）"
            for pack in await self._pack_names(ctx):
                album = await self._album(ctx, pack)
                if args[0] in album:
                    del album[args[0]]
                    await sset(ctx, self._key(pack), album)
                    return f"已删 #{args[0]}（包：{pack}）。"
            return f"没有 #{args[0]} 这张。"

        @ctx.command("meme-forget", description="清空相册（可只清一个包，必须确认）",
                     usage="/meme-forget [包] 确认")
        async def meme_forget(caller: CallerContext, args: list[str]) -> str:
            pack = DEFAULT_PACK
            if args and args[0] != "确认":
                pack = args[0]
                args = args[1:]
            if args[:1] != ["确认"]:
                return (f"这会清空{('包「' + pack + '」') if pack != DEFAULT_PACK else '全部相册'}"
                        f"的图且找不回来。确定的话：/meme-forget {pack if pack != DEFAULT_PACK else ''} 确认")
            await sset(ctx, self._key(pack), {})
            which = f"包「{pack}」" if pack != DEFAULT_PACK else "全部相册"
            return f"{which}已清空。"

        @ctx.tool(
            "meme-pick",
            description=("从表情包图库按对话语境选一张表情并发送到当前会话。"
                         "语境描述用中文短语，如「对方说了个冷笑话」「用户分享了开心的事」。"),
            parameters={
                "type": "object",
                "properties": {
                    "context": {"type": "string", "description": "当前对话语境的中文短语"},
                    "pack": {"type": "string", "description": "可选：只从这个包里选"},
                },
            },
        )
        async def meme_pick(caller: CallerContext, args: dict) -> str:
            context_words = str(args.get("context") or "").strip()
            if not context_words:
                return "需要语境描述才能选图。"
            pack = str(args.get("pack") or "").strip()
            packs = ([pack] if PACK_RE.match(pack)
                     else [p for p, _ in await self._pack_names(ctx)])
            if not packs:
                return "图库是空的。"
            best: tuple[dict, int] | None = None
            for pack_name in packs:
                for item in (await self._album(ctx, pack_name)).values():
                    score = self._score(item, context_words)
                    if score > 0 and (best is None or score > best[1]):
                        best = (item, score)
            if best is not None:
                item = best[0]
                await ctx.send_media(session_key=caller.session_key, image_b64=item["b64"], caption="")
                return "已按语境发送一张匹配的表情。"
            # 无标签命中时按包内轮换兜底（确定性，不随机）。
            for pack_name in packs:
                candidates = list((await self._album(ctx, pack_name)).values())
                if candidates:
                    item = self._rotate(candidates)
                    await ctx.send_media(session_key=caller.session_key, image_b64=item["b64"], caption="")
                    return f"已按包「{pack_name}」轮换发送一张。"
            return "图库没有可选的表情。"

    # ------------------------------------------------------------- helpers
    @staticmethod
    def _key(pack: str) -> str:
        return f"album.{pack}"

    async def _pack_names(self, ctx: PluginContext) -> list[str]:
        """包枚举走注册表键（隔离 worker 的 storage 没有 keys()）。default 恒在。"""
        registry = await sget(ctx, PACKS_KEY) or [DEFAULT_PACK]
        return sorted(set(registry) | {DEFAULT_PACK})

    async def _register_pack(self, ctx: PluginContext, pack: str) -> None:
        if not PACK_RE.match(pack):
            return
        registry = await sget(ctx, PACKS_KEY) or [DEFAULT_PACK]
        if pack not in registry:
            registry.append(pack)
            await sset(ctx, PACKS_KEY, registry)

    async def _album(self, ctx: PluginContext, pack: str) -> dict:
        """读一个包；default 包惰性迁移 0.2.0 旧单相册（迁移在命令期执行）。"""
        album = await sget(ctx, self._key(pack)) or {}
        if pack == DEFAULT_PACK:
            legacy = await sget(ctx, ALBUM_KEY)
            if legacy:
                for item_id, item in legacy.items():
                    item.setdefault("pack", DEFAULT_PACK)
                    album.setdefault(item_id, item)
                await sset(ctx, self._key(pack), album)
                await sdel(ctx, ALBUM_KEY)
        return album

    async def _register_pack(self, ctx: PluginContext, pack: str) -> None:
        if PACK_RE.match(pack):
            registry = await sget(ctx, PACKS_KEY) or [DEFAULT_PACK]
            if pack not in registry:
                registry.append(pack)
                await sset(ctx, PACKS_KEY, registry)

    async def _search(self, ctx: PluginContext, word: str) -> list[tuple[str, dict]]:
        hits = []
        for pack in await self._pack_names(ctx):
            album = await self._album(ctx, pack)
            for item_id, item in album.items():
                hay = " ".join([pack, *(item.get("tags") or [])]).lower()
                if word.lower() in hay:
                    hits.append((item_id, item))
        return hits

    @staticmethod
    def _dup_of(album: dict, body: bytes) -> str | None:
        h = hashlib.sha256(body).hexdigest()
        for item_id, item in album.items():
            if item.get("sha256") == h:
                return item_id
        return None

    @staticmethod
    def _put(album: dict, body: bytes, tags: list[str], pack: str) -> str:
        item_id = uuid.uuid4().hex[:10]
        album[item_id] = {
            "b64": base64.b64encode(body).decode("ascii"),
            "tags": tags,
            "pack": pack,
            "sha256": hashlib.sha256(body).hexdigest(),
            "saved_at": time.time(),
        }
        return item_id

    @staticmethod
    def _score(item: dict, context_words: str) -> int:
        """确定性打分：标签与语境词的字面重叠数。零模型调用。"""
        words = [w for w in re.split(r"[\s，。！？,.!?]+", context_words) if w]
        hay = " ".join(item.get("tags") or []).lower()
        return sum(1 for w in words if w.lower() in hay)

    @staticmethod
    def _rotate(candidates: list[tuple[str, dict]]) -> tuple[str, dict]:
        candidates.sort(key=lambda pair: pair[1].get("saved_at", 0))
        return candidates[int(time.time() // 60) % len(candidates)]

    async def _fetch_and_store(
        self, ctx: PluginContext, url: str, tags: list[str], pack: str
    ) -> str:
        try:
            status, body = await ctx.http.fetch(url, headers={"User-Agent": UA})
        except (PermissionError, RuntimeError, ValueError) as exc:
            return f"抓取失败：{exc}（图床域名可能不在 http 允许列表里）"
        if status != 200:
            return f"图床返回 HTTP {status}。"
        album = await self._album(ctx, pack)
        if len(album) >= MAX_ITEMS_PER_PACK:
            return f"包「{pack}」已满（{MAX_ITEMS_PER_PACK} 张），先 /meme-rm 腾位置。"
        dup = self._dup_of(album, body)
        if dup:
            return f"这张已经在包「{pack}」里（#{dup}），不重复入库。"
        item_id = self._put(album, body, tags, pack)
        await sset(ctx, self._key(pack), album)
        await self._register_pack(ctx, pack)
        return f"已入包「{pack}」（#{item_id}，标签：{'、'.join(tags)}）。共 {len(album)} 张。"
