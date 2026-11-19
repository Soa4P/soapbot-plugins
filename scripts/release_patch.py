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
    name = sys.argv[1].strip() if len(sys.argv) > 1 else ""
    notes_arg = sys.argv[sys.argv.index("--notes") + 1] if "--notes" in sys.argv else ""
    if not name:
        print("用法：release_patch.py <package-name> [--notes \"…\"]")
        return 2

    manifest_path = ROOT / "packages" / name / "plugin.yaml"
    import yaml

    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    old_version = str(manifest.get("version") or "")
    new_version = _bump(old_version)
    manifest["version"] = new_version
    manifest_path.write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"{name}: {old_version} -> {new_version}")

    dist = ROOT / ".release"
    dist.mkdir(exist_ok=True)
    zip_path = dist / f"{name}-{new_version}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted((ROOT / "packages" / name).rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, path.relative_to(ROOT / "packages" / name).as_posix())
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    print(f"zip: {zip_path.name} sha256={digest}")

    tag = f"{name}-{new_version}"
    _run([
        "gh", "release", "create", tag,
        "--title", f"{name} {new_version}",
        "--notes", notes_arg or f"{name} {new_version}：manifest 修复重发（描述禁字与能力声明对齐宿主校验）。",
        "--latest" if "--latest" in sys.argv else "--latest=false",
    ], check=True)
    _run(["gh", "release", "upload", tag, str(zip_path)], check=True)
    download = f"https://github.com/Soa4P/soapbot-plugins/releases/download/{tag}/{name}-{new_version}.zip"

    released_at = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    plugins = _load("plugins.json")
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
    det = (details.get("details") or {}).get(name)
    if isinstance(det, dict) and det.get("version"):
        det["version"] = new_version
        _write("plugin_details.json", details)

    print(f"release {tag} 完成；索引三件套已更新。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
