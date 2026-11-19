#!/usr/bin/env python
"""Smoke-validate listed plugins by loading them into a real soapbot host.

AstrBot_Plugins_Collection 的 validate-plugin-smoke 移植：PR 只测变更插件，
每晚全量扫一遍——装进**真宿主**（soapbot.loader）确认可加载。索引一致性门禁
（validate_index）管的是文件对得上；本脚本管的是「对得上的东西真的能加载」，
包括 manifest 声明的能力在加载时是否自洽。

失败即 exit 1 并逐条列原因；成功也是逐条列证据（插件名 + 模式 + 加载耗时）。
宿主来源：``--host-import``（默认 soapbot，CI 用 pip install 从钉住的 ref 装）。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import tempfile
import time
from pathlib import Path

PACKAGES_DIR_NAME = "packages"


async def smoke_one(
    package_dir: Path,
    *,
    host_import: str,
) -> tuple[bool, str]:
    """Copy one package into a fresh plugins dir, load it through the real
    host loader, report (ok, detail). A fresh dir per plugin keeps a broken
    package from poisoning its neighbours."""
    host = __import__(host_import)
    from soapbot.plugins.dispatch import PluginManager
    from soapbot.plugins.loader import PluginLoader
    from soapbot.plugins.supervisor import PluginSupervisor
    from soapbot.security.audit import AuditLog

    with tempfile.TemporaryDirectory() as temp:
        plugins_dir = Path(temp) / "plugins"
        plugins_dir.mkdir()
        shutil.copytree(package_dir, plugins_dir / package_dir.name)

        loader = PluginLoader(
            plugins_dir=plugins_dir,
            audit=AuditLog(),
            supervisor=PluginSupervisor(),
        )
        manager = PluginManager(loader)
        manager.install_isolation(supervisor=loader.supervisor)
        discovered = loader.discover().found
        if not discovered:
            return False, f"{package_dir.name}: 宿主 discover 没找到任何 manifest"
        started = time.monotonic()
        try:
            for manifest in discovered:
                loader.approve_from_manifest(manifest)
                loaded = await loader.load(manifest.name, force=True)
                # 隔离模式：instance 为 None，worker 才是运行句柄——两者其一非空即装载成功
                running = (
                    getattr(loaded, "worker", None) is not None
                    or getattr(loaded, "instance", None) is not None
                )
                if loaded is None or not running:
                    return False, (
                        f"{package_dir.name}/{manifest.name}: load 返回空"
                        "（隔离与进程内都未构造实例）"
                    )
        finally:
            supervisor = getattr(loader, "supervisor", None)
            if supervisor is not None:
                await supervisor.stop_all()
    elapsed = time.monotonic() - started
    names = ", ".join(m.name for m in discovered)
    return True, f"{package_dir.name} → 加载 {names}（{elapsed:.2f}s，隔离 worker）"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="把列出的插件装进真宿主做装载冒烟")
    parser.add_argument("--host-import", default="soapbot", help="宿主包的 import 名")
    parser.add_argument(
        "--packages-dir", default=PACKAGES_DIR_NAME, help="插件包根目录（默认 packages/）"
    )
    parser.add_argument(
        "--plugins", default="", help="逗号分隔的包目录名；空 = 全部（每晚全量）"
    )
    args = parser.parse_args()

    packages = Path(args.packages_dir)
    if not packages.is_dir():
        print(f"插件包根目录不存在：{packages.resolve()}")
        return 2
    if args.plugins:
        wanted = [n.strip() for n in args.plugins.split(",") if n.strip()]
    else:
        wanted = sorted(d.name for d in packages.iterdir() if d.is_dir() and not d.name.startswith("_"))
    if not wanted:
        print("没有要冒烟的插件。")
        return 0

    failures: list[str] = []
    for name in wanted:
        package_dir = packages / name
        if not package_dir.is_dir():
            failures.append(f"{name}: packages/ 下不存在该目录")
            print(f"[FAIL] {name}: 目录缺失")
            continue
        try:
            ok, detail = asyncio.run(smoke_one(package_dir, host_import=args.host_import))
        except Exception as exc:  # 一个插件的装载异常不能中断整轮巡检
            print(f"[FAIL] {name}: {type(exc).__name__}: {exc}")
            failures.append(f"{name}: {type(exc).__name__}: {exc}")
            continue
        print(f"[{'ok  ' if ok else 'FAIL'}] {detail}")
        if not ok:
            failures.append(detail)

    if failures:
        print(f"\n{len(failures)}/{len(wanted)} 个插件装载冒烟失败（上面逐条有原因）。")
        return 1
    print(f"\n全部装载成功（{len(wanted)}/{len(wanted)}）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
