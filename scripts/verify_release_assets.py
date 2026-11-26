#!/usr/bin/env python3
"""Independently verify published release assets (audit S-SUP-09).

The index records a ``digest`` per version. That digest is computed on the publisher's
machine, by the same script that uploads the zip — so it proves the upload was
self-consistent and nothing more. Two things it cannot tell you, and this script can:

1. **The asset is the declared digest.** Download the release asset and hash it. This is
   the only check that catches "the file on the release page is not what the index says",
   whether that is drift, a re-upload, or a swapped asset.
2. **The asset is the code in this repository.** A zip's own sha256 depends on the
   compiler's file timestamps, so it is not reproducible across machines; a *content*
   digest is. It hashes the sorted (relative path, file sha256) pairs — the same shape
   the host's plugin integrity check uses — so a release whose bytes no longer correspond
   to `packages/<name>/` fails even though its zip digest is internally consistent.

Usage::

    python scripts/verify_release_assets.py              # every plugin's latest version
    python scripts/verify_release_assets.py --name skill-tree
    python scripts/verify_release_assets.py --all-versions

Exit codes: 0 = all verified, 1 = a mismatch, 2 = could not check (network/index error).

Network use is deliberate and bounded: GitHub release assets only, https, no redirects
followed beyond the release host, and a hard size cap.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parent.parent
DIGEST = "sha256:"
MAX_ASSET_BYTES = 32 * 1024 * 1024
ALLOWED_ASSET_HOSTS = {
    "github.com",
    "objects.githubusercontent.com",
    "release-assets.githubusercontent.com",
}


class VerifyError(RuntimeError):
    """A mismatch or an unavailable asset; the message is the report line."""


def _load(name: str) -> dict:
    return json.loads((ROOT / name).read_text(encoding="utf-8"))


#: Suffixes whose content is compared with line endings normalised. The published zips
#: were built from a Windows working tree, so they carry CRLF; a Linux CI checkout of the
#: same commit has LF. Comparing raw bytes therefore answers "was this built on the same
#: OS", not "is this the same code" — and the first version of this check failed every
#: plugin on Linux for exactly that reason. Binary suffixes are excluded: normalising a
#: PNG that happens to contain 0x0D 0x0A would corrupt the comparison.
_TEXT_SUFFIXES = frozenset(
    {".py", ".yaml", ".yml", ".json", ".md", ".txt", ".toml", ".cfg", ".ini", ".sh"}
)


def _normalise_line_endings(name: str, body: bytes) -> bytes:
    if PurePosixPath(name).suffix.lower() in _TEXT_SUFFIXES:
        return body.replace(b"\r\n", b"\n")
    return body


def content_digest_from_directory(directory: Path) -> str:
    """Reproducible digest of a plugin tree: sorted ``path + sha256(file)`` pairs.

    ``__pycache__`` is skipped for the same reason the host skips it — the interpreter
    rewrites it, and its contents say nothing about the reviewed source. Line endings are
    normalised for text files so the digest describes the code, not the machine that
    checked it out.
    """
    pairs: list[tuple[str, str]] = []
    for path in directory.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(directory)
        if "__pycache__" in relative.parts:
            continue
        name = relative.as_posix()
        body = _normalise_line_endings(name, path.read_bytes())
        pairs.append((name, hashlib.sha256(body).hexdigest()))
    # Keyed by the *path string*, not by ``Path`` ordering: ``Path`` comparisons are
    # case-insensitive on Windows and compare components, while the archive side sorts
    # strings. Two orderings of the same files give two digests, and the check would then
    # fail for a reason that has nothing to do with the release.
    lines = [f"{name}\0{digest}" for name, digest in sorted(pairs)]
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def content_digest_from_zip(payload: bytes) -> str:
    """Same digest, computed from the published archive."""
    pairs: list[tuple[str, str]] = []
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        for info in archive.infolist():
            if info.is_dir():
                continue
            name = info.filename.replace("\\", "/")
            if "__pycache__" in name.split("/"):
                continue
            body = _normalise_line_endings(name, archive.read(info))
            pairs.append((name, hashlib.sha256(body).hexdigest()))
    lines = [f"{name}\0{digest}" for name, digest in sorted(pairs)]
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


class _AllowedRedirect(urllib.request.HTTPRedirectHandler):
    """Follow one hop, and only to a host that serves release assets.

    GitHub answers a release download with a 302 to ``release-assets.githubusercontent.com``
    (signed, short-lived URL). Refusing every redirect would make the check unrunnable;
    following any redirect would let a compromised index point the check at an arbitrary
    host. So: one hop, allow-listed destination, https only.
    """

    hops = 0
    MAX_HOPS = 1

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or (parsed.hostname or "") not in ALLOWED_ASSET_HOSTS:
            raise VerifyError(f"refusing to follow a redirect to {newurl!r}")
        self.hops += 1
        if self.hops > self.MAX_HOPS:
            raise VerifyError("refusing to follow more than one redirect")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_once(url: str) -> bytes:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or (parsed.hostname or "") not in ALLOWED_ASSET_HOSTS:
        raise VerifyError(f"refusing to download from {url!r}")
    request = urllib.request.Request(url, headers={"User-Agent": "updatepilot-index-check"})
    opener = urllib.request.build_opener(_AllowedRedirect())
    try:
        with opener.open(request, timeout=60) as response:
            payload = response.read(MAX_ASSET_BYTES + 1)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise VerifyError(f"download failed: {exc}") from exc
    if len(payload) > MAX_ASSET_BYTES:
        raise VerifyError(f"asset is larger than {MAX_ASSET_BYTES} bytes")
    return payload


def download(url: str, *, attempts: int = 3) -> bytes:
    """Download with a bounded retry.

    A weekly verification job that fails on one slow handshake is a job people learn to
    ignore. Three attempts with a short pause covers the noise; a real mismatch is not
    retried away, because it is not a transport error.
    """
    import time

    last: VerifyError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return _download_once(url)
        except VerifyError as exc:
            last = exc
            if attempt < attempts:
                time.sleep(2 * attempt)
    raise last if last is not None else VerifyError("download failed")


def verify_one(name: str, entry: dict) -> list[str]:
    """Check one version entry; return problem lines (empty means fine)."""
    problems: list[str] = []
    version = str(entry.get("version", ""))
    declared = str(entry.get("digest", ""))
    url = str(entry.get("download", ""))
    if not url:
        return [f"{name} {version}: metadata has no download url"]
    if not declared.startswith(DIGEST) or len(declared) != len(DIGEST) + 64:
        return [f"{name} {version}: digest is missing or malformed"]

    payload = download(url)
    actual = DIGEST + hashlib.sha256(payload).hexdigest()
    if actual != declared:
        problems.append(
            f"{name} {version}: release asset is {actual[:19]}… but the index says "
            f"{declared[:19]}…"
        )

    source = ROOT / "packages" / name
    if source.is_dir():
        published = content_digest_from_zip(payload)
        expected = content_digest_from_directory(source)
        if published != expected:
            problems.append(
                f"{name} {version}: archive content is {published[:19]}… but packages/{name} "
                f"is {expected[:19]}… (the release does not match this repository)"
            )
    else:
        problems.append(f"{name} {version}: packages/{name} does not exist in this repository")
    return problems


def self_test() -> int:
    """Offline checks of the parts that can be wrong without anyone noticing.

    No network: the point is to prove the digest is *reproducible* from a directory and a
    zip of the same files, and that the ways it should differ do produce a difference. A
    digest that quietly ignores a file is worse than no check at all.
    """
    import tempfile

    failures: list[str] = []
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp) / "demo"
        (root / "sub").mkdir(parents=True)
        (root / "main.py").write_text("x = 1\n", encoding="utf-8")
        (root / "sub" / "data.json").write_text("{}", encoding="utf-8")

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.write(root / "main.py", "main.py")
            archive.write(root / "sub" / "data.json", "sub/data.json")

        from_dir = content_digest_from_directory(root)
        from_zip = content_digest_from_zip(buffer.getvalue())
        if from_dir != from_zip:
            failures.append(f"目录与归档的摘要不一致：{from_dir} != {from_zip}")

        # A rebuild with different timestamps must still match: zip bytes are not
        # reproducible, content is.
        buffer2 = io.BytesIO()
        with zipfile.ZipFile(buffer2, "w", zipfile.ZIP_STORED) as archive:
            # Bytes from the files themselves: writing the literal "x = 1\n" would be
            # b"x = 1\n" while Windows wrote b"x = 1\r\n" to disk, and the test would
            # fail for a line-ending reason rather than a digest one.
            archive.writestr("sub/data.json", (root / "sub" / "data.json").read_bytes())
            archive.writestr("main.py", (root / "main.py").read_bytes())
        if content_digest_from_zip(buffer2.getvalue()) != from_dir:
            failures.append("同样内容、不同打包方式，摘要变了")

        # Bytecode is skipped on both sides.
        (root / "__pycache__").mkdir()
        (root / "__pycache__" / "main.pyc").write_bytes(b"\0\0")
        if content_digest_from_directory(root) != from_dir:
            failures.append("__pycache__ 影响了摘要")

        # Any content change must change the digest.
        (root / "main.py").write_text("x = 2\n", encoding="utf-8")
        if content_digest_from_directory(root) == from_dir:
            failures.append("内容改了，摘要没变")

    # Entry validation that never needs the network.
    missing = verify_one("demo", {"version": "1.0.0", "digest": "sha256:" + "a" * 64})
    if not missing or "download" not in missing[0]:
        failures.append("缺 download 的条目没有被判不合格")
    malformed = verify_one("demo", {"version": "1.0.0", "digest": "sha256:xyz", "download": "https://x"})
    if not malformed or "malformed" not in malformed[0]:
        failures.append("畸形 digest 没有被判不合格")

    for failure in failures:
        print(f"[FAIL] {failure}")
    if failures:
        return 1
    print("self-test 通过")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--name", action="append", default=[], help="only this plugin")
    parser.add_argument(
        "--all-versions",
        action="store_true",
        help="check every published version, not just each plugin's latest",
    )
    parser.add_argument(
        "--self-test", action="store_true", help="offline checks of the digest and rules"
    )
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()

    versions = _load("plugin_versions.json").get("versions", {})
    plugins = {item.get("name"): item for item in _load("plugins.json").get("plugins", [])}
    wanted = args.name or sorted(versions)

    problems: list[str] = []
    unreachable: list[str] = []
    checked = 0
    skipped = 0
    for name in wanted:
        entries = versions.get(name)
        if not entries:
            problems.append(f"{name}: no versions in the index")
            continue
        if args.all_versions:
            selected = entries
        else:
            listed = plugins.get(name)
            if listed is None:
                # Present in plugin_versions.json but not offered for install (archived or
                # withdrawn). Nothing to verify against — say so instead of calling it a
                # failure: the installer cannot reach it either.
                print(f"[skip] {name}: not listed for install (archived or withdrawn)")
                skipped += 1
                continue
            latest = listed.get("latest", "")
            selected = [entry for entry in entries if entry.get("version") == latest]
            if not selected:
                problems.append(f"{name}: plugins.json latest={latest!r} has no metadata")
                continue
        for entry in selected:
            try:
                problems.extend(verify_one(name, entry))
            except VerifyError as exc:
                # Transport problems are reported apart from mismatches: one means "come
                # back later", the other means "this release is wrong".
                unreachable.append(f"{name} {entry.get('version')}: {exc}")
            checked += 1

    for problem in problems:
        print(f"[FAIL] {problem}")
    for note in unreachable:
        print(f"[unreachable] {note}")
    if problems:
        print(f"\n{len(problems)} 项不合格（检查了 {checked} 个版本，{skipped} 个跳过）")
        return 1
    if unreachable:
        print(f"\n{len(unreachable)} 项无法核对（网络或发布资产不可达）")
        return 2
    print(f"\n发布资产核对通过（{checked} 个版本，{skipped} 个跳过）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
