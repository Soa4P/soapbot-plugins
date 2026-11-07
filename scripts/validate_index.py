#!/usr/bin/env python3
"""Cross-file consistency check for this index.

Each file is valid JSON on its own long before the *set* of them makes sense: a version
listed in `plugins.json` with no entry in `plugin_versions.json` parses fine and then
renders as a plugin with no download link. That is the class of mistake this catches.

Rules, all of which have a corresponding failure in a real PR:

1. every file parses;
2. every name in `plugins.json` has details and at least one version;
3. `latest` is the highest version listed, and is present in `versions`;
4. every version in `plugins.json` has metadata in `plugin_versions.json`;
5. no version metadata exists for a version `plugins.json` does not list;
6. every blocked entry names a version that actually exists;
7. no duplicate names;
8. every digest is either a real `sha256:<64 hex>` or the all-zero placeholder — and a
   placeholder is a warning, because it means the PR is not mergeable yet.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
PLACEHOLDER = "sha256:" + "0" * 64
VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def load(name: str) -> dict:
    path = ROOT / name
    if not path.is_file():
        raise SystemExit(f"missing {name}")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"{name} is not valid JSON: {exc}") from exc


def version_key(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    plugins = load("plugins.json")
    details = load("plugin_details.json").get("details", {})
    versions = load("plugin_versions.json").get("versions", {})
    blocked = load("blocked_versions.json").get("blocked_versions", [])
    archived = load("archived_plugins.json").get("archived", [])

    problems: list[str] = []
    warnings: list[str] = []

    listed = plugins.get("plugins", [])
    names = [item.get("name", "") for item in listed]

    duplicates = {name for name in names if names.count(name) > 1}
    for name in sorted(duplicates):
        problems.append(f"plugins.json: duplicate name {name!r}")

    archived_names = {item.get("name") for item in archived}
    for item in listed:
        name = item.get("name", "")
        if not name:
            problems.append("plugins.json: entry without a name")
            continue
        if name in archived_names:
            problems.append(f"plugins.json: {name} is archived but still listed for install")
        if name not in details:
            problems.append(f"plugin_details.json: no entry for {name}")
        if name not in versions:
            problems.append(f"plugin_versions.json: no versions for {name}")
            continue

        declared = item.get("versions", [])
        if not declared:
            problems.append(f"plugins.json: {name} lists no versions")
        for version in declared:
            if not VERSION.match(version):
                problems.append(f"plugins.json: {name} has malformed version {version!r}")

        known = [entry.get("version") for entry in versions[name]]
        for version in declared:
            if version not in known:
                problems.append(
                    f"plugin_versions.json: {name} {version} is listed but has no metadata"
                )
        for version in known:
            if version not in declared:
                problems.append(
                    f"plugins.json: {name} {version} has metadata but is not listed"
                )

        if declared:
            highest = max(declared, key=version_key)
            if item.get("latest") != highest:
                problems.append(
                    f"plugins.json: {name} latest is {item.get('latest')!r}, highest is {highest!r}"
                )

        for entry in versions[name]:
            digest = entry.get("digest", "")
            if digest == PLACEHOLDER:
                warnings.append(
                    f"plugin_versions.json: {name} {entry.get('version')} still has a placeholder digest"
                )
            elif not DIGEST.match(digest):
                problems.append(
                    f"plugin_versions.json: {name} {entry.get('version')} digest is malformed"
                )

    for entry in blocked:
        name = entry.get("name", "")
        version = entry.get("version", "")
        if not entry.get("reason"):
            problems.append(f"blocked_versions.json: {name} {version} has no reason")
        if name not in versions:
            problems.append(f"blocked_versions.json: {name} is not a known plugin")
        elif version not in [item.get("version") for item in versions[name]]:
            problems.append(f"blocked_versions.json: {name} {version} is not a known version")

    for entry in archived:
        if not entry.get("reason"):
            problems.append(f"archived_plugins.json: {entry.get('name')} has no reason")

    for warning in warnings:
        print(f"[warn] {warning}")
    for problem in problems:
        print(f"[FAIL] {problem}")

    if problems:
        print(f"\n{len(problems)} 项不合格")
        return 1
    print(f"\n索引一致（{len(listed)} 个插件，{len(warnings)} 项提醒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
