"""Functional smoke: drive storage-touching commands through real isolated workers.

装载冒烟（smoke_validate）只证明「能加载」；本脚本证明「装上了真能跑」——每包
驱动 2-3 个存储触碰型命令真跑（隔离 worker + 加密存储后端）。裸同步 storage 调用
在隔离模式拿 coroutine 首用即坏（#18 之前的存量问题），这类缺陷装载冒烟永远看不
见，只有功能冒烟抓得住。

用法：
    python scripts/func_smoke.py                    # 全部有 PLAN 条目的包
    python scripts/func_smoke.py --plugins a,b      # 只跑列出的包
无 PLAN 条目的包记 skip，不算失败。新增插件时在 PLAN 登记一行。
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import shutil
import sys
import tempfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

PACKAGES = Path(__file__).resolve().parent.parent / "packages"

# package -> list[(command, args)]：每包挑 2-3 个**存储触碰型**命令（真读写
# storage，而不是空参返回 usage 的）。新增插件时在这里登记一行。
PLAN: dict[str, list[tuple[str, list[str]]]] = {
    "fishtalk-lite": [("fish-status", [])],
    "bili-scroll": [("bili-scroll-set", ["测试关键词"]), ("bili-scroll-forget", [])],
    "meme-wiki": [("meme-add", ["测试梗", "这是一条测试释义"]), ("meme-count", [])],
    "auto-chat": [("autochat-set", ["0.0"]), ("autochat-off", [])],
    "novel-desk": [("nd-card", ["人物", "主角", "一个测试用的人物卡"]), ("nd-card-list", [])],
    "qzone-composer": [("qzc-set", []), ("qzc-off", [])],
    "self-nudge": [("nudge", ["2h", "这是测试留言"]), ("nudge-list", []), ("nudge-clear", [])],
    "weather-alert": [("weather-set", ["广东", "23.1", "113.2"]), ("weather-clear", [])],
    "skill-tree": [("tree-add", ["python", "Python 建树测试"]), ("tree-done", ["python"]), ("tree", [])],
    "group-digest": [("digest-now", []), ("digest-reset", [])],
    "sticker-album": [("meme-packs", []), ("meme-stats", [])],
    "memory-weekly": [("memweek", [])],
    "mood-diary": [("mood-on", []), ("mood", []), ("mood-forget", [])],
}

# 有消息钩子的包：功能冒烟顺带真发一条，钩子里的 storage 写也要活着。
HOOKS_TO_FIRE = {"auto-chat"}

# 必须声明 min_role=ADMIN 的命令（S-SUP-06）：改全局配置、开关群级行为、写外部
# 账号、清空跨会话共享数据。插件命令默认 MEMBER——不显式收紧就是任何群成员可调。
ADMIN_ONLY: dict[str, set[str]] = {
    "qzone-composer": {"qzc-set", "qzc-off", "qzc-confirm", "qzc-drop"},
    "weather-alert": {"weather-set", "weather-ack", "weather-clear"},
    "auto-chat": {"autochat-set", "autochat-off", "autochat-clear"},
    "fishtalk-lite": {"fish-forget"},
    "self-nudge": {"nudge-clear"},
    "sticker-album": {"meme-forget"},
}


def _caller():
    from soapbot.plugins.context import CallerContext

    return CallerContext(
        user_id="funcsmoke", display_name="功能冒烟", role="member",
        session_key="qq:group:funcsmoke", scene="group", group_id="funcsmoke",
    )


async def run_package(name: str, temp: str, fails: list[str]) -> None:
    plugins_dir = Path(temp) / "plugins"
    plugins_dir.mkdir(exist_ok=True)
    shutil.copytree(PACKAGES / name, plugins_dir / name)
    from soapbot.plugins.dispatch import PluginManager
    from soapbot.plugins.loader import PluginLoader
    from soapbot.plugins.supervisor import PluginSupervisor
    from soapbot.security.audit import AuditLog
    from soapbot.security.crypto import SecretBox

    loader = PluginLoader(
        plugins_dir=plugins_dir, audit=AuditLog(), supervisor=PluginSupervisor(),
        crypto=SecretBox(key=base64.urlsafe_b64encode(b"0" * 32)),
        storage_backend={},
    )
    PluginManager(loader).install_isolation(supervisor=loader.supervisor)
    manifest = next(m for m in loader.discover().found if m.name == name)
    loader.approve_from_manifest(manifest)
    loaded = await loader.load(name, force=True)
    try:
        # 宿主侧读命令表（dispatch 用的就是它），确认敏感命令真的声明了 ADMIN——
        # 注释里写 "仅 bot 管理员" 不算数。
        for cmd in sorted(ADMIN_ONLY.get(name, ())):
            meta = loaded.context.commands.get(cmd) or {}
            declared = str(meta.get("min_role") or "").lower()
            ok = declared == "admin"
            print(f"[{'ok  ' if ok else 'FAIL'}] {name} /{cmd} min_role={declared or '(member)'}")
            if not ok:
                fails.append(f"{name}/{cmd}/min_role")
        for cmd, args in PLAN[name]:
            try:
                out = await loaded.worker.invoke(cmd, caller=_caller(), args=args)
                text = str(out)
                bad = any(k in text for k in ("AttributeError", "TypeError", "RuntimeError"))
                print(f"[{'ok  ' if not bad else 'FAIL'}] {name} /{cmd} -> {text[:70]!r}")
                if bad:
                    fails.append(f"{name}/{cmd}")
            except Exception as exc:
                print(f"[FAIL] {name} /{cmd} -> {type(exc).__name__}: {exc}")
                fails.append(f"{name}/{cmd}")
        if name in HOOKS_TO_FIRE:
            for hook in getattr(loaded.context, "_message_hooks", []):
                try:
                    await hook(_caller(), "这是一条测试消息")
                    print(f"[ok  ] {name} hook {hook.__name__} fired")
                except Exception as exc:
                    print(f"[FAIL] {name} hook -> {type(exc).__name__}: {exc}")
                    fails.append(f"{name}/hook")
    finally:
        await loader.supervisor.stop_all()


async def main() -> int:
    parser = argparse.ArgumentParser(description="功能级隔离冒烟：真 worker 驱动存储触碰型命令")
    parser.add_argument("--plugins", default="", help="逗号分隔；空 = 全部有 PLAN 条目的包")
    args = parser.parse_args()

    wanted = [s.strip() for s in args.plugins.split(",") if s.strip()] or sorted(PLAN)
    fails: list[str] = []
    for name in wanted:
        if name not in PLAN:
            print(f"[skip] {name}: 无 PLAN 条目（请在 scripts/func_smoke.py 登记）")
            continue
        if not (PACKAGES / name).is_dir():
            print(f"[FAIL] {name}: packages/ 下不存在")
            fails.append(f"{name}/missing")
            continue
        with tempfile.TemporaryDirectory() as temp:
            await run_package(name, temp, fails)
    print()
    print("全部通过" if not fails else f"{len(fails)} 项失败: {fails}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
