#!/usr/bin/env python
"""Rewrite a plugin manifest's ``description`` to a single, host-legal line.

The host's manifest validation forbids ASCII ``/`` and newlines in free-text
fields (log-injection and path-injection resistance). This helper rewrites the
description in place: ASCII ``/`` becomes the full-width ``／`` (a Chinese
typography convention the host accepts), folded newlines become spaces, and the
value is re-emitted as a JSON double-quoted scalar (valid YAML, immune to
``#``-comment and special-character parsing in plain style).

Usage: python scripts/fix_description.py <package-name> [--dry-run]
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "packages"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) < 2:
        print("用法：fix_description.py <package-name> [--dry-run]")
        return 2
    name = sys.argv[1].strip()
    dry = "--dry-run" in sys.argv

    manifest = PACKAGES / name / "plugin.yaml"
    if not manifest.is_file():
        print(f"找不到 {manifest}")
        return 2

    raw = manifest.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict):
        print(f"{manifest} 不是映射")
        return 2
    old = str(data.get("description") or "")
    new = old.replace("/", "／").replace("\n", " ").strip()

    lines = raw.split("\n")
    out: list[str] = []
    in_desc = False
    done = False
    for line in lines:
        stripped = line.strip()
        if not done and in_desc and (stripped == "" or (line[:1] not in (" ", "\t"))):
            in_desc = False
        if not done and line.lstrip().startswith("description:"):
            indent = line[: len(line) - len(line.lstrip())]
            out.append(indent + "description: " + json.dumps(new, ensure_ascii=False))
            done = True
            in_desc = True
            continue
        if in_desc:
            continue  # 丢弃旧描述的续行
        out.append(line)

    new_raw = "\n".join(out)
    if dry:
        print(new_raw)
        return 0
    manifest.write_text(new_raw, encoding="utf-8")

    # Verify through the host's own validator: the rewrite is only done when the
    # host would accept it.
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / ".." / "soapbot" / "src"))
    from soapbot.plugins.manifest import ManifestError, parse_manifest

    try:
        data2 = yaml.safe_load(manifest.read_text(encoding="utf-8"))
        parse_manifest(data2)
    except Exception as exc:
        print(f"重写后宿主校验仍拒绝：{exc}")
        return 1
    print(f"{name}: description 已重写并通过宿主校验（{len(old)} → {len(new)} 字符）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
