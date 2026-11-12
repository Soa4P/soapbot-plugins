"""sticker-album: the bot's own sticker and photo album, stored for later use.

存储走插件加密 storage（base64 + 标签），发送走宿主的 ``send_media`` 能力面
（与 send 同一套 observed-session ledger + 独立审批 + 4 MiB 双边界上限）。
三个刻意的限制：

* 来源只有 URL——「从聊天里顺手收藏」需要消息钩子暴露图片段，宿主还没有这个面
  （见 main 仓库 docs/qq-autonomy-design.md §5），先不做。
* 上限 100 张：相册是「顺手能用」，不是图床；总量封顶防止 storage 无界膨胀。
* 标签必须显式：没有标签的图检索不到，存了等于没存。

## 命令

| 命令 | 作用 |
|---|---|
| `/meme-save <URL> <标签...>` | 抓图入册（图床域名需在 http 允许列表） |
| `/meme-list [标签]` | 列出（可按标签过滤） |
| `/meme-send <标签> [说明]` | 发一张到当前会话 |
| `/meme-forget 确认` | 全清 |

## 能力与边界

| 能力 | 用途 | 收窄 |
|---|---|---|
| `http` | 只 GET /meme-save 传入的图 URL | 宿主白名单 + 禁重定向 + 2MiB 响应上限 |
| `storage` | base64 图 + 标签（加密，≤100 张） | 不出插件命名空间 |
| `send_media` | 发图 | 宿主 ledger + 独立审批 + 4MiB 双边界上限 |
"""

from __future__ import annotations

import base64
import time
import uuid

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

ALBUM_KEY = "album"
MAX_ITEMS = 100
UA = "soapbot-sticker-album/0.1 (+https://github.com/Soa4P/soapbot-plugins)"


class StickerAlbumPlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.command("meme-save", description="抓一张图进相册并打标签", usage="/meme-save <图片URL> <标签...>")
        async def meme_save(caller: CallerContext, args: list[str]) -> str:
            if len(args) < 2:
                return "用法：/meme-save <图片URL> <标签...>（至少一个标签）"
            url = args[0]
            if not url.lower().startswith(("http://", "https://")):
                return "URL 要以 http(s):// 开头。"
            tags = [t.strip() for t in args[1:] if t.strip()]
            try:
                status, body = await ctx.http.fetch(url, headers={"User-Agent": UA})
            except (PermissionError, RuntimeError, ValueError) as exc:
                return f"抓取失败：{exc}（图床域名可能不在 http 允许列表里）"
            if status != 200:
                return f"图床返回 HTTP {status}。"
            if len(body) > 3 * 1024 * 1024:
                return f"图片 {len(body) // 1024} KiB，超过 3 MiB 的入库上限。"
            album = ctx.storage.get(ALBUM_KEY) or {}
            if len(album) >= MAX_ITEMS:
                return f"相册已满（{MAX_ITEMS} 张），先 /meme-rm 腾位置。"
            item_id = uuid.uuid4().hex[:10]
            album[item_id] = {
                "b64": base64.b64encode(body).decode("ascii"),
                "tags": tags,
                "saved_at": time.time(),
            }
            ctx.storage.set(ALBUM_KEY, album)
            return f"已入册（#{item_id}，标签：{'、'.join(tags)}）。相册 {len(album)} 张。"

        @ctx.command("meme-list", description="列出相册（可按标签过滤）", usage="/meme-list [标签]")
        async def meme_list(caller: CallerContext, args: list[str]) -> str:
            album = ctx.storage.get(ALBUM_KEY) or {}
            if not album:
                return "相册是空的。/meme-save <URL> <标签> 收第一张。"
            tag = args[0].strip() if args else ""
            lines = []
            for item_id, item in album.items():
                tags = item.get("tags") or []
                if tag and tag not in tags:
                    continue
                lines.append(f"#{item_id} [{'、'.join(tags)}]")
            if not lines:
                return f"没有带「{tag}」标签的图。"
            return "\n".join(lines[:40])

        @ctx.command("meme-send", description="按标签发一张到当前会话", usage="/meme-send <标签> [说明]")
        async def meme_send(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/meme-send <标签> [说明]"
            tag = args[0]
            caption = " ".join(args[1:])
            album = ctx.storage.get(ALBUM_KEY) or {}
            candidates = [(iid, it) for iid, it in album.items() if tag in (it.get("tags") or [])]
            if not candidates:
                return f"没有带「{tag}」标签的图。"
            # 命中多张时按时间轮流（确定性：存入顺序 + 当前分钟取模），避免永远发同一张。
            candidates.sort(key=lambda pair: pair[1].get("saved_at", 0))
            _item_id, item = candidates[int(time.time() // 60) % len(candidates)]
            await ctx.send_media(
                session_key=caller.session_key,
                image_b64=item["b64"],
                caption=caption or f"[{tag}]",
            )
            return ""

        @ctx.command("meme-rm", description="按编号删一张", usage="/meme-rm <编号>")
        async def meme_rm(caller: CallerContext, args: list[str]) -> str:
            if not args:
                return "用法：/meme-rm <编号>（/meme-list 里方括号前的那个）"
            album = ctx.storage.get(ALBUM_KEY) or {}
            if args[0] not in album:
                return f"没有 #{args[0]} 这张。"
            del album[args[0]]
            ctx.storage.set(ALBUM_KEY, album)
            return f"已删 #{args[0]}。"

        @ctx.command("meme-forget", description="清空整个相册（必须确认）", usage="/meme-forget 确认")
        async def meme_forget(caller: CallerContext, args: list[str]) -> str:
            if args[:1] != ["确认"]:
                return "这会清空全部图片且找不回来。确定的话：/meme-forget 确认"
            ctx.storage.set(ALBUM_KEY, {})
            return "相册已清空。"

