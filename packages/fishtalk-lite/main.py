"""fishtalk-lite: teach the bot how real people talk, check before it speaks.

Adapted from https://github.com/Nozzi2455/fishtalk (check its LICENSE) into a
soapbot plugin. The original has three jobs — collect (learn from videos and
chats), check (score a draft for "fish-flavoured" boilerplate), forget. The port
keeps the three jobs and drops the parts soapbot's capability set cannot host yet:

* 「发前自动拦截」需要回复管线的 hook，插件的 message hook 是 observe-only——所以
  check 在这里是**命令**（/fish-check <草稿>），不是自动闸门；把它接进回复路径是
  宿主侧的后续工作（见 ADAPTATION.md）。
* 语料是标题/简介/UP 名的**风格样本**（每条 ≤200 字符，上限 300 条），不是评论或
  用户内容：collect 只经公开元数据，不抓评论区。

The check itself is deterministic: boilerplate markers + template-shape regexes.
No model call — the capability set here does not include running the model.
"""

from __future__ import annotations

import re

from soapbot.plugins.base import Plugin
from soapbot.plugins.context import CallerContext, PluginContext

CORPUS_KEY = "corpus"
MAX_CORPUS = 300
MAX_SAMPLE_CHARS = 200

VIEW_PATH = "/x/web-interface/view"

#: 「鱼八股」标记词。对照 fishtalk 的拦截思路自写的起点词表，可在 storage 外改：
#: 这里刻意硬编码而不是配置化——词表是本插件的判断标准，悄悄换词表等于悄悄换结论。
BOILERPLATE = [
    "这条我认",
    "我记下了",
    "已记录",
    "收到，",
    "好的，我会",
    "希望这对你有帮助",
    "作为一个",
    "总的来说，",
    "值得注意的是",
    "综上所述",
]

#: 模板形状：破折号小标题、三段式总结开头。
TEMPLATE_SHAPES = [
    re.compile(r"——[^—\n]{2,12}："),
    re.compile(r"^首先[，,]", re.MULTILINE),
    re.compile(r"^总之[，,]", re.MULTILINE),
]


def _fish_score(text: str) -> tuple[int, list[str]]:
    """返回 (扣分, 命中原因)。扣分 ≥ 2 视为八股。"""
    reasons: list[str] = []
    score = 0
    for marker in BOILERPLATE:
        if marker in text:
            score += 2
            reasons.append(f"八股词「{marker}」")
    for pattern in TEMPLATE_SHAPES:
        if pattern.search(text):
            score += 1
            reasons.append("模板形状（破折号小标题/三段式开头）")
    if len(text) > 200 and text.count("。") <= 1:
        score += 1
        reasons.append("长句不断句")
    return score, reasons


class FishTalkLitePlugin(Plugin):
    async def setup(self, ctx: PluginContext) -> None:
        await super().setup(ctx)

        @ctx.command("fish-collect", description="收一个视频的字幕正文作语料样本（无字幕退回简介）",
                     usage="/fish-collect <BV号>")
        async def collect(caller: CallerContext, args: list[str]) -> str:
            if not args or not args[0].startswith("BV"):
                return "用法：/fish-collect <BV号>"
            bvid = args[0]
            # 字幕正文由宿主 L2 client 代签代抓（wbi + hdslb），插件只发 bvid。
            # 这是 dsh-bili-humanism 的核心思想在 soapbot 边界内的落法：
            # 「真看懂视频」的第一步是拿到原文，签名与抓取属于宿主能力面。
            subtitle_text = ""
            try:
                sub = await ctx.bilibili.subtitle(bvid)
                subtitle_text = str(sub.get("text") or "")
            except (ValueError, RuntimeError):
                # 无字幕、未配置、宿主未接——都退回标题+简介。能力拒绝
                # （PermissionError）不在这里吞：没批准就该看得见拒绝。
                subtitle_text = ""
            payload = await ctx.bilibili.get_json(VIEW_PATH, params={"bvid": bvid})
            if payload.get("code") != 0:
                return f"没有找到 {bvid}（B 站错误码 {payload.get('code')}）"
            data = payload.get("data") or {}
            title = str(data.get("title") or "")
            desc = str(data.get("desc") or "")
            owner = str((data.get("owner") or {}).get("name") or "")
            if subtitle_text:
                sample = f"{title}（UP：{owner}）字幕｜{subtitle_text}"[:MAX_SAMPLE_CHARS]
            else:
                sample = f"{title}｜{owner}｜{desc}"[:MAX_SAMPLE_CHARS]
            if not sample.strip("｜"):
                return "这个视频没有可采样的文本。"
            corpus = ctx.storage.get(CORPUS_KEY) or []
            if not isinstance(corpus, list):
                corpus = []
            corpus.append(sample)
            corpus = corpus[-MAX_CORPUS:]
            ctx.storage.set(CORPUS_KEY, corpus)
            kind = "字幕" if subtitle_text else "简介"
            return f"已收{kind}语料（{len(corpus)} 条）。多收几个你常看的 UP，检查才有基准。"

        @ctx.command("fish-check", description="检查一段话有没有鱼味", usage="/fish-check <草稿>")
        async def check(caller: CallerContext, args: list[str]) -> str:
            text = " ".join(args).strip()
            if not text:
                return "用法：/fish-check <要发的话>"
            score, reasons = _fish_score(text)
            if score < 2:
                return f"没有明显八股（扣分 {score}）。可以发。"
            detail = "；".join(reasons[:5])
            return f"有鱼味（扣分 {score}）：{detail}。建议改写成你自己的口气再发。"

        @ctx.command("fish-status", description="看语料有多少", usage="/fish-status")
        async def status(caller: CallerContext, args: list[str]) -> str:
            corpus = ctx.storage.get(CORPUS_KEY) or []
            return f"语料 {len(corpus)}/{MAX_CORPUS} 条。"

        @ctx.command("fish-forget", description="全部忘掉（必须确认，删了找不回来）",
                     usage="/fish-forget 确认")
        async def forget(caller: CallerContext, args: list[str]) -> str:
            if args[:1] != ["确认"]:
                return "这会清空全部语料且找不回来。确定的话：/fish-forget 确认"
            ctx.storage.set(CORPUS_KEY, [])
            return "已全部忘掉。"
