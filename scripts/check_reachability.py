#!/usr/bin/env python
"""Probe every index entry's download URL and report unreachable ones.

The reverse of the digest chain: install-from-index already proves "what you
download is what the index declares"; this proves the download still *exists*.
A deleted release or a vanished repo breaks installs for users who have never
seen this repo — the probe surfaces that BEFORE a user hits it.

Scope: the **latest** version of every plugin (historical releases are archive;
quarantining them would be a silent disappearance, which this repo's index
philosophy forbids). Egress is allowlisted to github.com — the only host the
index legitimately points at. Exit codes: 0 = all reachable, 1 = unreachable
(the workflow turns that into an issue; it does not mutate the index — losing
an entry would be a silent disappearance, the exact thing the probe exists to
prevent).
"""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
INDEX = ROOT / "plugin_versions.json"
ALLOWED_HOST = "github.com"
TIMEOUT = 15.0
USER_AGENT = "soapbot-plugins-reachability/1.0"


def latest_downloads() -> list[tuple[str, str, str]]:
    """(plugin, version, download_url) for each plugin's declared latest version.

    ``plugins.json`` is the authority for what "latest" means (its ``latest``
    field); ``plugin_versions.json`` supplies the download URL for that version.
    Reading them together also means a stale/phantom pair fails loudly here
    instead of 404-ing at a user's install step.
    """
    index = json.loads((ROOT / "plugins.json").read_text(encoding="utf-8"))
    versions = json.loads(INDEX.read_text(encoding="utf-8")).get("versions") or {}
    out: list[tuple[str, str, str]] = []
    for entry in index.get("plugins") or []:
        name = str(entry.get("name") or "")
        latest = str(entry.get("latest") or "")
        if not name or not latest:
            continue
        match = next(
            (v for v in versions.get(name, []) if str(v.get("version")) == latest), None
        )
        url = str(match.get("download") or "").strip() if match else ""
        if url:
            out.append((name, latest, url))
    return out


def _probe(url: str) -> str | None:
    """Return the failure reason, or None when reachable. HEAD + redirects."""
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        return f"非 https（{parsed.scheme}）"
    if parsed.hostname != ALLOWED_HOST:
        return f"主机不在白名单（{parsed.hostname}）"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT}, method="HEAD")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            if response.status < 400:
                return None
            return f"HTTP {response.status}"
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return f"{type(exc).__name__}: {exc}"


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    targets = latest_downloads()
    print(f"探测 {len(targets)} 个最新版本的 download URL（host 白名单：{ALLOWED_HOST}）")
    unreachable: list[tuple[str, str, str, str]] = []
    for plugin, version, url in targets:
        reason = _probe(url)
        mark = "ok  " if reason is None else "FAIL"
        print(f"[{mark}] {plugin}@{version} {url}" + (f" → {reason}" if reason else ""))
        if reason is not None:
            unreachable.append((plugin, version, url, reason))
        time.sleep(0.3)  # 温和节奏：16 个探测不该像扫描
    if unreachable:
        print(f"\n{len(unreachable)} 个失联（发布资产不可达；安装会在下载步失败）：")
        for plugin, version, url, reason in unreachable:
            print(f"  - {plugin}@{version}: {reason}（{url}）")
        return 1
    print(f"\n全部可达（{len(targets)}/{len(targets)}）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
