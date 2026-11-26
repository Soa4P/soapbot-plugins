#!/usr/bin/env python
"""Release one package as a patch: bump manifest version, zip, create the
GitHub release with the zip asset, and update the three index files.

Patch-bump only (0.4.0 -> 0.4.1): this helper exists for manifest/content
repairs of already-published plugins — feature releases stay a human-run flow.
The digest recorded in the index is the SHA-256 of the zip asset itself, so a
user-side install verifies against exactly what this script uploaded.

Usage: python scripts/release_patch.py <package-name> [--notes "…"]
Requires: gh authenticated; run from the repository root.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _run(args: list[str], *, check: bool = True) -> str:
    result = subprocess.run(
        args, cwd=ROOT, capture_output=True, text=True,
        encoding="utf-8", errors="replace", shell=False, check=False,
    )
    if check and result.returncode != 0:
        raise RuntimeError(f"{' '.join(args[:3])}… failed: {result.stderr.strip()[:400]}")
    return result.stdout


def _bump(version: str) -> str:
    major, minor, patch = version.split(".")
    return f"{major}.{minor}.{int(patch) + 1}"


def _load(path: str) -> dict:
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def _write(path: str, data: dict) -> None:
    (ROOT / path).write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    argv = sys.argv[1:]
    name = argv[0].strip() if argv else ""
    notes_arg = argv[argv.index("--notes") + 1] if "--notes" in argv else ""
    summary_arg = argv[argv.index("--summary") + 1] if "--summary" in argv else ""
    if not name:
        print("用法：release_patch.py <package-name> [--notes \"…\"] [--summary \"…\"]")
        return 2

    manifest_path = ROOT / "packages" / name / "plugin.yaml"
    import yaml

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    declared = str(manifest.get("version") or "")
    index = _load("plugins.json")
    latest = ""
    for entry in index.get("plugins", []):
        if entry.get("name") == name:
            latest = str(entry.get("latest") or "")
    is_new = latest == "" and not any(
        e.get("name") == name for e in index.get("plugins", [])
    )
    # 版本语义：新包按声明发布；manifest 已声明一个未发布的新版本 → 按声明发布
    # （如 1.0.0 MAJOR）；manifest 版本 == 索引 latest（已发布过）→ patch bump。
    # 两者之外拒绝（防手滑）。
    if declared == latest and not is_new:
        new_version = _bump(declared)
        manifest["version"] = new_version
        manifest_path.write_text(
            yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        print(f"{name}: bump {latest} -> {new_version}")
    elif declared and (declared != latest or is_new):
        new_version = declared
        if is_new:
            print(f"{name}: 新包按声明版本 {declared} 首发")
        else:
            print(f"{name}: 按声明版本 {declared} 发布（索引 latest={latest}）")
    else:
        print(f"{name}: 版本缺失")
        return 2
    print(f"release version: {new_version}")

    dist = ROOT / ".release"
    dist.mkdir(exist_ok=True)
    zip_path = dist / f"{name}-{new_version}.zip"
    # Written through ``writestr`` with normalised line endings rather than ``write``:
    # a zip built on a Windows checkout used to carry CRLF while the repository stores LF,
    # so "is the release the code in git" was unanswerable across platforms
    # (scripts/verify_release_assets.py hit exactly that). Archives from now on are
    # LF-only and byte-stable regardless of where they were built.
    _TEXT_SUFFIXES = {".py", ".yaml", ".yml", ".json", ".md", ".txt", ".toml", ".cfg", ".ini", ".sh"}
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((ROOT / "packages" / name).rglob("*")):
            if not path.is_file() or "__pycache__" in path.parts:
                continue
            body = path.read_bytes()
            if path.suffix.lower() in _TEXT_SUFFIXES:
                body = body.replace(b"\r\n", b"\n")
            zf.writestr(path.relative_to(ROOT / "packages" / name).as_posix(), body)
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"zip: {zip_path.name} sha256={digest}")

    tag = f"{name}-{new_version}"
    _run([
        "gh", "release", "create", tag,
        "--title", f"{name} {new_version}",
        "--notes", notes_arg or f"{name} {new_version}：manifest 修复重发（描述禁字与能力声明对齐宿主校验）。",
        "--latest=false",
    ], check=True)
    _run(["gh", "release", "upload", tag, str(zip_path)], check=True)
    download = f"https://github.com/Soa4P/soapbot-plugins/releases/download/{tag}/{name}-{new_version}.zip"

    released_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plugins = _load("plugins.json")
    if is_new:
        if not summary_arg:
            print("新包首发需要 --summary（索引 plugins.json 的短摘要）。")
            return 2
        plugins.setdefault("plugins", []).append({
            "name": name,
            "latest": new_version,
            "versions": [new_version],
            "summary": summary_arg,
            "author": str(manifest.get("author") or "soapbot-plugins"),
            "homepage": f"https://github.com/Soa4P/soapbot-plugins/tree/main/packages/{name}",
            "repository": "https://github.com/Soa4P/soapbot-plugins",
        })
    for entry in plugins.get("plugins", []):
        if entry.get("name") == name:
            entry["latest"] = new_version
            if new_version not in (entry.get("versions") or []):
                entry.setdefault("versions", []).append(new_version)
    _write("plugins.json", plugins)

    versions = _load("plugin_versions.json")
    versions.setdefault("versions", {}).setdefault(name, []).append(
        {
            "version": new_version,
            "released_at": released_at,
            "digest": f"sha256:{digest}",
            "min_host_version": str(manifest.get("min_host_version") or "0.1.0"),
            "download": download,
        }
    )
    _write("plugin_versions.json", versions)

    details = _load("plugin_details.json")
    details.setdefault("details", {})
    det = details["details"].get(name)
    if isinstance(det, dict):
        if det.get("version"):
            det["version"] = new_version
    else:
        # 新包首发：从 manifest 建 details 条目（与既有包的形状一致）。
        details["details"][name] = {
            "summary": summary_arg or str(manifest.get("description") or "")[:60],
            "description": str(manifest.get("description") or ""),
            "capabilities": list(manifest.get("capabilities") or []),
            "requires": [],
            "readme": f"packages/{name}/README.md",
        }
    _write("plugin_details.json", details)

    print(f"release {tag} 完成；索引三件套已更新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
