#!/usr/bin/env python3
"""PR-level integrity checks that a single-snapshot validator cannot express.

`validate_index.py` answers "is this index self-consistent right now". That is a
different question from "is this *change* safe", and the dangerous changes all pass the
snapshot check:

* rewriting the digest of an already-published version (the host compares digests after
  download, so this silently redefines what every existing install trusts);
* pointing `download` at another repository (the index is the only place that says where
  bytes come from);
* deleting an entry from `blocked_versions.json` (CONTRIBUTING: blocking is one-way);
* adding yourself to `trusted_developers.json` in the same PR that adds a plugin.

Each of those leaves a perfectly consistent index. They are only visible by comparing
against the base commit.

Usage:
    python scripts/check_index_integrity.py                  # snapshot rules only
    python scripts/check_index_integrity.py --base <sha>     # plus change rules
    python scripts/check_index_integrity.py --self-test      # rules tested on fixtures

Exit codes: 0 = pass, 1 = a problem was found, 2 = the check could not run.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

REPO = "Soa4P/soapbot-plugins"
RELEASE_BASE = f"https://github.com/{REPO}/releases/download"
PLACEHOLDER_DIGEST = "sha256:" + "0" * 64
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")

# Files whose every change needs a human, no matter how consistent the result looks.
MANUAL_ONLY = ("trusted_developers.json",)

FILES = (
    "plugins.json",
    "plugin_details.json",
    "plugin_versions.json",
    "blocked_versions.json",
    "archived_plugins.json",
    "trusted_developers.json",
)


def load_dir(base: Path) -> dict[str, dict]:
    loaded: dict[str, dict] = {}
    for name in FILES:
        path = base / name
        if not path.is_file():
            raise SystemExit(f"missing {name}")
        try:
            loaded[name] = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{name} is not valid JSON: {exc}") from exc
    return loaded


def load_git(base: str) -> dict[str, dict] | None:
    """Read the same files from a commit. `None` when the commit is not available.

    A missing base is not an error: the first push to a branch has no `before`, and the
    workflow passes whatever it has. Returning `None` lets the caller skip the change
    rules with a warning instead of reporting every version as "newly added".
    """
    probe = subprocess.run(
        ["git", "cat-file", "-e", f"{base}^{{commit}}"],
        cwd=ROOT,
        capture_output=True,
        check=False,
    )
    if probe.returncode != 0:
        return None

    loaded: dict[str, dict] = {}
    for name in FILES:
        proc = subprocess.run(
            ["git", "show", f"{base}:{name}"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        if proc.returncode != 0:
            loaded[name] = {}
            continue
        try:
            loaded[name] = json.loads(proc.stdout or "{}")
        except json.JSONDecodeError as exc:
            raise SystemExit(f"{name} at {base} is not valid JSON: {exc}") from exc
    return loaded


def _versions(data: dict) -> dict[str, list[dict]]:
    return data.get("plugin_versions.json", {}).get("versions", {}) or {}


def _blocked(data: dict) -> set[tuple[str, str]]:
    entries = data.get("blocked_versions.json", {}).get("blocked_versions", []) or []
    return {(str(e.get("name", "")), str(e.get("version", ""))) for e in entries}


def _archived(data: dict) -> set[str]:
    entries = data.get("archived_plugins.json", {}).get("archived", []) or []
    return {str(e.get("name", "")) for e in entries}


def expected_download(name: str, version: str) -> str:
    return f"{RELEASE_BASE}/{name}-{version}/{name}-{version}.zip"


def check_snapshot(data: dict, *, today: dt.date) -> tuple[list[str], list[str]]:
    """Rules answerable from the working tree alone. Returns (problems, warnings)."""
    problems: list[str] = []
    warnings: list[str] = []

    for name, entries in _versions(data).items():
        for entry in entries:
            version = str(entry.get("version", ""))
            label = f"{name} {version}"
            if not VERSION.match(version):
                problems.append(f"plugin_versions.json: {label} version is malformed")
            if not VERSION.match(str(entry.get("min_host_version", ""))):
                problems.append(f"plugin_versions.json: {label} min_host_version is malformed")

            download = str(entry.get("download", ""))
            if download != expected_download(name, version):
                problems.append(
                    f"plugin_versions.json: {label} download must be "
                    f"{expected_download(name, version)!r}, got {download!r}"
                )

            released = str(entry.get("released_at", ""))
            try:
                when = dt.date.fromisoformat(released)
            except ValueError:
                problems.append(f"plugin_versions.json: {label} released_at {released!r} is not a date")
            else:
                if when > today:
                    problems.append(f"plugin_versions.json: {label} released_at is in the future")

            digest = str(entry.get("digest", ""))
            if digest == PLACEHOLDER_DIGEST:
                warnings.append(
                    f"plugin_versions.json: {label} still has a placeholder digest"
                )
            elif not DIGEST.match(digest):
                problems.append(f"plugin_versions.json: {label} digest is malformed")

    return problems, warnings


def check_change(current: dict, base: dict, *, today: dt.date) -> list[str]:
    """Rules that need the base commit: what changed, not just what is."""
    problems: list[str] = []

    base_versions = _versions(base)
    for name, entries in _versions(current).items():
        published = {str(e.get("version")): e for e in base_versions.get(name, [])}
        for entry in entries:
            version = str(entry.get("version", ""))
            label = f"{name} {version}"
            before = published.get(version)
            if before is None:
                # A new version may not ship a placeholder digest: the placeholder is
                # explicitly a draft-PR marker (CONTRIBUTING), and moving it into a
                # merged index would publish a download nobody can verify.
                if str(entry.get("digest", "")) == PLACEHOLDER_DIGEST:
                    problems.append(
                        f"plugin_versions.json: {label} is new but its digest is still the placeholder"
                    )
                continue
            for field in ("digest", "download", "released_at", "min_host_version"):
                if str(before.get(field, "")) != str(entry.get(field, "")):
                    problems.append(
                        f"plugin_versions.json: {label} changes published field {field!r}; "
                        "published metadata is immutable — release a new version instead"
                    )

    for name, version in sorted(_blocked(base) - _blocked(current)):
        problems.append(
            f"blocked_versions.json: {name} {version} was removed; blocking is one-way"
        )

    for name in sorted(_archived(base) - _archived(current)):
        problems.append(f"archived_plugins.json: {name} was removed; archiving is one-way")

    for name in MANUAL_ONLY:
        if base.get(name) is not None and current.get(name) != base.get(name):
            problems.append(
                f"{name}: changed in this PR. It needs its own review — one PR does one thing."
            )

    del today
    return problems


def self_test() -> int:
    """Rules tested on fixtures, so a rule that silently stops matching fails CI."""
    today = dt.date(2026, 11, 8)

    def snapshot(entry: dict) -> dict:
        return {"plugin_versions.json": {"versions": {"demo": [entry]}}}

    good = {
        "version": "1.0.0",
        "released_at": "2026-10-01",
        "digest": "sha256:" + "a" * 64,
        "min_host_version": "0.1.0",
        "download": expected_download("demo", "1.0.0"),
    }

    cases = [
        ("a valid entry produces nothing", good, 0, 0),
        (
            "a foreign download URL is rejected",
            {**good, "download": "https://example.com/x.zip"},
            1,
            0,
        ),
        (
            "a placeholder digest is only a warning on a snapshot",
            {**good, "digest": PLACEHOLDER_DIGEST},
            0,
            1,
        ),
        ("a future date is rejected", {**good, "released_at": "2027-01-01"}, 1, 0),
        ("a malformed digest is rejected", {**good, "digest": "sha256:xyz"}, 1, 0),
    ]

    failures = 0
    for label, entry, want_problems, want_warnings in cases:
        problems, warnings = check_snapshot(snapshot(entry), today=today)
        ok = len(problems) == want_problems and len(warnings) == want_warnings
        print(f"[{'ok' if ok else 'FAIL'}] {label}")
        if not ok:
            failures += 1
            print(f"       problems={problems} warnings={warnings}")

    base = {"plugin_versions.json": {"versions": {"demo": [good]}}}
    current = {
        "plugin_versions.json": {
            "versions": {"demo": [{**good, "digest": "sha256:" + "b" * 64}]}
        }
    }
    rewritten = check_change(current, base, today=today)
    ok = any("published field 'digest'" in item for item in rewritten)
    print(f"[{'ok' if ok else 'FAIL'}] rewriting a published digest is rejected")
    failures += 0 if ok else 1

    added = {
        "plugin_versions.json": {
            "versions": {"demo": [good, {**good, "version": "1.1.0", "digest": PLACEHOLDER_DIGEST}]}
        }
    }
    new_placeholder = check_change(added, base, today=today)
    ok = any("is new but its digest is still the placeholder" in item for item in new_placeholder)
    print(f"[{'ok' if ok else 'FAIL'}] a new version may not ship a placeholder digest")
    failures += 0 if ok else 1

    removed_block = check_change(
        {
            "blocked_versions.json": {"blocked_versions": []},
            "plugin_versions.json": {"versions": {}},
        },
        {
            "blocked_versions.json": {
                "blocked_versions": [{"name": "demo", "version": "1.0.0", "reason": "r"}]
            },
            "plugin_versions.json": {"versions": {}},
        },
        today=today,
    )
    ok = any("blocking is one-way" in item for item in removed_block)
    print(f"[{'ok' if ok else 'FAIL'}] removing a block entry is rejected")
    failures += 0 if ok else 1

    if failures:
        print(f"\nself-test: {failures} 项失败")
        return 1
    print("\nself-test: 全部通过")
    return 0


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", default="", help="base commit SHA for change rules")
    parser.add_argument("--self-test", action="store_true", help="test the rules on fixtures")
    args = parser.parse_args()

    if args.self_test:
        return self_test()

    try:
        current = load_dir(ROOT)
        base = load_git(args.base) if args.base else None
    except SystemExit:
        raise
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"check failed to run: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    if args.base and base is None:
        print(f"[warn] base {args.base[:8]} 不可用（首次推送？），跳过变更规则")

    problems, warnings = check_snapshot(current, today=dt.date.today())
    if base is not None:
        problems.extend(check_change(current, base, today=dt.date.today()))

    for warning in warnings:
        print(f"[warn] {warning}")
    for problem in problems:
        print(f"[FAIL] {problem}")

    if problems:
        print(f"\n{len(problems)} 项不合格")
        return 1
    scope = f"对比 {args.base[:8]}" if base is not None else "仅快照"
    print(f"\n索引变更安全（{scope}，{len(warnings)} 项提醒）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
