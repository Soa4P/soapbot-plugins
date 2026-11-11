#!/usr/bin/env python3
"""Ask a model to read a pull request to *this index* and say whether it is safe.

Sibling of `Soa4P/soapbot`'s `scripts/ai_review.py`; keep the two in step when the prompt
or the degradation rules change. This copy adds one thing: `decide_merge`, which is the
only place in either repository that may authorise an automatic merge.

Why it is so narrow. This repository holds *only the index* — plugin code lives in
release zips. So a PR here cannot "add a plugin's code"; it changes where users are told
to download bytes from and what they are told those bytes do. That makes three fields
load-bearing and everything else cosmetic:

* `digest` — the host compares it after download; rewriting it silently redefines what
  every existing install trusts;
* `download` — the only record of where the bytes come from;
* `versions` / `latest` — what users are offered.

`decide_merge` therefore allows auto-merge only for a PR that touches nothing but
`plugin_details.json` and does not touch any of those keys. Everything else stays with a
human, which is also what CONTRIBUTING already asks for ("one PR does one thing").

Usage (from a workflow; all input arrives through the environment):

    python scripts/ai_review.py
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"
MAX_DIFF_BYTES = 200_000
MAX_DIFF_LINES = 4_000
REQUEST_TIMEOUT = 60.0

VERDICTS = ("safe", "needs_review", "malicious")
RISKS = ("low", "medium", "high")

# Files whose only purpose is human-facing text. Auto-merge is limited to these.
SAFE_MERGE_FILES = {"plugin_details.json"}

# A changed JSON key that decides trust rather than describes it. Matched only at the
# start of an added/removed line so that a description *mentioning* "version" does not
# trip it.
TRUST_KEY_LINE = re.compile(
    r'^[+-]\s*"(digest|download|versions|latest|version|blocked_versions|trusted_developers)"\s*:'
)

SYSTEM_PROMPT = """你是一名审查「插件索引」变更的代码审查员。这个仓库只放索引，不放插件代码的运行时。

索引里的三个字段是信任链的一部分，任何改动都要当成高风险：
- `digest`：宿主下载插件后用它比对；改掉它等于改变所有已安装用户的信任对象。
- `download`：唯一记录「字节从哪里来」的地方；指向其他仓库或域名即视为可疑。
- `versions` / `latest`：用户会被提供哪个版本。

重点检查：
1. 把 download 指向外部地址，或指向与本仓库无关的 release。
2. 修改已发布版本（已存在 name+version）的 digest/download/released_at。
3. 删除或削弱 blocked_versions（封禁是单向的）、archived_plugins。
4. 在新增插件的同时修改 trusted_developers（信任列表需要单独审查）。
5. 描述与 capabilities 不符：请求 send/memory_read/kb_read 却没说明发到哪里、读什么。
6. 一次 PR 夹带无关变更（CONTRIBUTING 要求一次只做一件事）。
7. 版本号回退，或复用已发布过的版本号。

纯描述订正（只改 description、summary、homepage、author 展示名）不属于风险。

只输出一个 JSON 对象，不要输出任何其他文字：

{"verdict": "safe|needs_review|malicious",
 "risk": "low|medium|high",
 "summary": "一到两句中文结论",
 "concerns": [{"file": "路径", "why": "具体原因"}]}

判断标准：
- safe：改动与其描述一致，且没有触及 digest/download/versions。
- needs_review：需要人确认，包括信息不足或被截断。
- malicious：明确发现后门、外传、提权或掩盖行为。
concerns 可以为空数组。"""


class ReviewError(RuntimeError):
    """Raised when the model could not be consulted; callers degrade to needs_review."""


def truncate(text: str) -> tuple[str, bool, str]:
    """Bound the input by lines and bytes.

    Returns `(text, was_truncated, what_was_dropped)`. Saying only "truncated" leaves the
    reader unable to judge how much was missed, and "not reviewed" is not the same as
    "reviewed and fine".
    """
    lines = text.splitlines()
    total_lines = len(lines)
    kept = lines[:MAX_DIFF_LINES]
    joined = "\n".join(kept)
    dropped_lines = total_lines - len(kept)

    dropped_bytes = 0
    encoded = joined.encode("utf-8", "replace")
    if len(encoded) > MAX_DIFF_BYTES:
        dropped_bytes = len(encoded) - MAX_DIFF_BYTES
        joined = encoded[:MAX_DIFF_BYTES].decode("utf-8", "ignore")

    parts = []
    if dropped_lines:
        parts.append(f"{dropped_lines} 行")
    if dropped_bytes:
        parts.append(f"{dropped_bytes} 字节" if dropped_bytes < 1024 else f"{dropped_bytes // 1024} KB")
    return joined, bool(parts), " + ".join(parts)


def build_prompt(diff: str, meta: dict, truncated: bool, dropped: str = "") -> str:
    head = [
        f"标题：{meta.get('title', '(空)')}",
        f"作者：{meta.get('author', '(未知)')}",
        f"变更文件：{meta.get('files', '(未知)')}",
    ]
    if meta.get("body"):
        head.append(f"描述：\n{str(meta['body'])[:2000]}")
    if truncated:
        head.append(
            f"注意：diff 已被截断（未提供 {dropped or '部分内容'}），你看到的不是全部改动。"
            "这种情况下不要给出 safe。"
        )
    head.append("--- diff 开始 ---")
    head.append(diff or "(空 diff)")
    head.append("--- diff 结束 ---")
    return "\n".join(head)


def _extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        pass
    else:
        return _as_object(payload, text)
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            payload = json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            pass
        else:
            return _as_object(payload, text)
    raise ReviewError(f"模型没有返回可解析的 JSON（开头：{text[:80]!r}）")


def _as_object(payload: object, text: str) -> dict:
    """Only a JSON object is usable.

    ``json.loads("[]")``、``"null"``、``"42"`` 都能解析成功，返回的却不是对象。旧版本把
    它们直接交给 ``normalise``，于是 ``payload.get`` 抛 AttributeError，审核步骤整个异常
    退出，PR 上只剩一个没有解释的红叉（而 ``review`` 是必需检查）。在这里变成明确的
    ``ReviewError``，由上层降级成 ``needs_review`` 并照常发评论。
    """
    if not isinstance(payload, dict):
        raise ReviewError(
            f"模型返回的 JSON 不是对象（{type(payload).__name__}；开头：{text[:80]!r}）"
        )
    return payload


def normalise(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ReviewError(f"normalise 收到非对象载荷：{type(payload).__name__}")
    verdict = str(payload.get("verdict", "")).strip().lower()
    risk = str(payload.get("risk", "")).strip().lower()
    if verdict not in VERDICTS:
        raise ReviewError(f"模型返回了未知 verdict: {verdict!r}")
    if risk not in RISKS:
        risk = "medium"

    concerns: list[dict] = []
    for item in payload.get("concerns") or []:
        if isinstance(item, dict):
            file = str(item.get("file", ""))[:200]
            why = str(item.get("why", ""))[:500]
        else:
            file, why = "", str(item)[:500]
        if why:
            concerns.append({"file": file, "why": why})

    return {
        "verdict": verdict,
        "risk": risk,
        "summary": str(payload.get("summary", ""))[:1000],
        "concerns": concerns[:20],
    }


def call_model(prompt: str, *, api_key: str, model: str, base_url: str) -> str:
    # A BOM in the secret breaks urllib's latin-1 header encoding with an error that
    # looks like a network failure; report it as the configuration problem it is.
    try:
        api_key.encode("ascii")
    except UnicodeEncodeError as exc:
        raise ReviewError(
            f"DEEPSEEK_API_KEY 含非 ASCII 字符（位置 {exc.start}）；"
            "通常是复制或管道传输时带入了 BOM/全角字符"
        ) from exc

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
        "temperature": 0,
        # Reasoning model: `completion_tokens` includes the hidden reasoning, so a small
        # budget can be consumed before any `content` is produced.
        "max_tokens": 3000,
    }
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            status = getattr(response, "status", 0)
            raw = response.read()
    except urllib.error.HTTPError as exc:
        snippet = exc.read()[:300].decode("utf-8", "replace")
        raise ReviewError(f"模型返回 HTTP {exc.code}（响应开头：{snippet!r}）") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ReviewError(f"无法连接模型：{type(exc).__name__}: {exc}") from exc

    try:
        body = json.loads(raw.decode("utf-8", "replace"))
    except (ValueError, TypeError) as exc:
        # The transport answered with a non-JSON body (an HTML block page, say) — that is
        # not the same failure as a model that replied with prose.
        raise ReviewError(f"响应体不是 JSON（HTTP {status}，开头：{raw[:300]!r}）") from exc

    choice = (body.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    content = message.get("content")
    finish = choice.get("finish_reason", "?")
    if not content or not str(content).strip():
        usage = body.get("usage") or {}
        reasoning = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        raise ReviewError(
            f"模型返回空内容（finish_reason={finish}, reasoning_tokens={reasoning}, "
            f"completion_tokens={usage.get('completion_tokens')}）"
        )
    return str(content)


def review(diff: str, meta: dict, *, api_key: str, model: str, base_url: str) -> dict:
    clipped, truncated, dropped = truncate(diff)
    prompt = build_prompt(clipped, meta, truncated, dropped)
    try:
        raw = call_model(prompt, api_key=api_key, model=model, base_url=base_url)
        result = normalise(_extract_json(raw))
    except ReviewError as exc:
        return {
            "verdict": "needs_review",
            "risk": "medium",
            "summary": f"自动审查未能完成：{exc}",
            "concerns": [],
            "truncated": truncated,
            "dropped": dropped,
            "degraded": True,
        }

    if truncated and result["verdict"] == "safe":
        result["verdict"] = "needs_review"
        result["risk"] = "medium"
        result["summary"] = (result["summary"] + "（diff 已截断，无法确认全部改动）").strip()
    result["truncated"] = truncated
    result["dropped"] = dropped
    result["degraded"] = False
    return result


def decide_merge(result: dict, files: list[str], diff: str) -> tuple[bool, str]:
    """The only function in either repository that may authorise an automatic merge.

    Every branch returns a reason, which is posted in the comment: a silent "blocked"
    teaches nobody, and a silent "allowed" is worse.
    """
    if result["verdict"] != "safe":
        return False, f"AI 结论为 {result['verdict']}，不是 safe"
    if result["risk"] != "low":
        return False, f"风险等级为 {result['risk']}，不是 low"
    if result.get("truncated"):
        return False, "diff 被截断，未看完全部改动"
    if result.get("degraded"):
        return False, "审查未完成，不能据此合并"
    if not files:
        return False, "未拿到变更文件列表"

    outside = sorted(set(files) - SAFE_MERGE_FILES)
    if outside:
        return False, "变更涉及索引或其他文件：" + ", ".join(outside[:5])

    for line in diff.splitlines():
        if TRUST_KEY_LINE.match(line):
            return False, f"变更触及信任字段：{line.strip()[:60]}"

    return True, "仅修改描述类字段，未触及 digest/download/versions"


def render_comment(result: dict, meta: dict, merge: tuple[bool, str] | None = None) -> str:
    badge = {"safe": "✅ 未发现问题", "needs_review": "⚠️ 需要人工确认", "malicious": "🚫 疑似恶意"}
    lines = [
        f"## {badge.get(result['verdict'], result['verdict'])}",
        "",
        f"**风险等级**：{result['risk']}　**模型**：`{meta.get('model', '')}`",
        "",
        result["summary"] or "(无结论)",
    ]
    if result.get("truncated"):
        dropped = result.get("dropped") or "未知数量"
        lines += [
            "",
            f"> diff 超过审查上限，只看了前面的部分：**{dropped} 未审查**。结论已自动降级为 needs_review。",
        ]
    if result.get("degraded"):
        lines += ["", "> 模型调用失败，本条不是对代码的判断。请人工审查。"]
    if result["concerns"]:
        lines += ["", "### 具体关注点", ""]
        for item in result["concerns"]:
            where = f"`{item['file']}`" if item["file"] else "（未指明文件）"
            lines.append(f"- {where}：{item['why']}")
    if merge is not None:
        allowed, reason = merge
        lines += [
            "",
            "### 自动合并",
            "",
            ("✅ 允许（仅当 `AI_REVIEW_MODE=enforce` 时生效）" if allowed else "❌ 不允许")
            + f"：{reason}",
        ]
    lines += [
        "",
        "---",
        "",
        "本评论由 AI 生成，只作参考；索引类变更始终需要人工确认。",
    ]
    return "\n".join(lines)


def _write_output(name: str, value: str) -> None:
    target = os.environ.get("GITHUB_OUTPUT")
    if target:
        with open(target, "a", encoding="utf-8") as handle:
            handle.write(f"{name}={value}\n")


def _read_files() -> list[str]:
    path = os.environ.get("PR_FILES_PATH", "")
    if not path:
        return []
    try:
        return [line.strip() for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def self_test() -> int:
    """`decide_merge` is the highest-consequence branch here; test it directly."""

    def verdict(**overrides) -> dict:
        base = {
            "verdict": "safe",
            "risk": "low",
            "summary": "ok",
            "concerns": [],
            "truncated": False,
            "degraded": False,
        }
        base.update(overrides)
        return base

    description_only = (
        '-      "description": "查询城市天气"\n+      "description": "查询城市天气与空气质量"'
    )
    cases = [
        ("描述订正允许自动合并", verdict(), ["plugin_details.json"], description_only, True),
        ("改动 plugins.json 阻止", verdict(), ["plugins.json"], description_only, False),
        ("改动 digest 阻止", verdict(), ["plugin_details.json"], '-      "digest": "sha256:abc"', False),
        (
            "改动 download 阻止",
            verdict(),
            ["plugin_details.json"],
            '+      "download": "https://evil.example/x.zip"',
            False,
        ),
        ("非 safe 阻止", verdict(verdict="needs_review"), ["plugin_details.json"], description_only, False),
        ("high risk 阻止", verdict(risk="high"), ["plugin_details.json"], description_only, False),
        ("截断阻止", verdict(truncated=True), ["plugin_details.json"], description_only, False),
        ("降级阻止", verdict(degraded=True), ["plugin_details.json"], description_only, False),
        ("空文件列表阻止", verdict(), [], description_only, False),
        (
            "描述文字里提到 version 不误判",
            verdict(),
            ["plugin_details.json"],
            '-      "description": "修复 version 显示"',
            True,
        ),
    ]

    failures = 0
    for label, result, files, diff, want in cases:
        allowed, reason = decide_merge(result, files, diff)
        ok = allowed is want
        detail = "" if ok else f"（期望 {want}，实得 {allowed}：{reason}）"
        print(f"[{'ok' if ok else 'FAIL'}] {label}{detail}")
        if not ok:
            failures += 1

    if failures:
        print(f"\nself-test: {failures} 项失败")
        return 1
    print("\nself-test: 全部通过")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    if "--self-test" in sys.argv:
        return self_test()

    files = _read_files()
    diff = ""
    diff_path = Path(os.environ.get("PR_DIFF_PATH", "pr.diff"))
    try:
        diff = diff_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        diff = ""

    api_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        result = {
            "verdict": "needs_review",
            "risk": "medium",
            "summary": "未配置 DEEPSEEK_API_KEY，本次没有进行自动审查。",
            "concerns": [],
            "truncated": False,
            "degraded": True,
        }
    elif not diff:
        result = {
            "verdict": "needs_review",
            "risk": "medium",
            "summary": "读取 diff 失败或 diff 为空，未进行审查。",
            "concerns": [],
            "truncated": False,
            "degraded": True,
        }
    else:
        model = os.environ.get("AI_REVIEW_MODEL", DEFAULT_MODEL)
        base_url = os.environ.get("AI_REVIEW_BASE_URL", DEFAULT_BASE_URL)
        meta = {
            "title": os.environ.get("PR_TITLE", ""),
            "author": os.environ.get("PR_AUTHOR", ""),
            "body": os.environ.get("PR_BODY", ""),
            "files": ", ".join(files)[:500],
            "model": model,
        }
        result = review(diff, meta, api_key=api_key, model=model, base_url=base_url)
        result["model"] = model

    merge = decide_merge(result, files, diff)
    meta = {
        "title": os.environ.get("PR_TITLE", ""),
        "author": os.environ.get("PR_AUTHOR", ""),
        "model": result.get("model", os.environ.get("AI_REVIEW_MODEL", DEFAULT_MODEL)),
    }
    comment = render_comment(result, meta, merge)

    out_path = os.environ.get("AI_REVIEW_COMMENT_PATH")
    if out_path:
        Path(out_path).write_text(comment, encoding="utf-8")

    _write_output("verdict", result["verdict"])
    _write_output("risk", result["risk"])
    _write_output("degraded", "true" if result.get("degraded") else "false")
    _write_output("merge_allowed", "true" if merge[0] else "false")
    _write_output("merge_reason", merge[1])
    print(comment)
    return 0


if __name__ == "__main__":
    sys.exit(main())
