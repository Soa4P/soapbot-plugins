#!/usr/bin/env python3
"""Verify the detached signature over this repository's published index.

Why a copy of the host's logic
------------------------------
The host verifies with ``soapbot.plugins.index_signature``. This file re-implements
the few lines needed (canonical JSON + Ed25519 verify) so CI needs no access to the
host repository — and so a red badge here means "the index and its signature disagree",
which is exactly the condition that must block a merge.

The duplication is deliberate and safe in the fail-closed direction: if the two
canonicalisations ever drift, *every* verification fails loudly rather than passing
something it should have rejected. Keep `canonical_payload` byte-identical to
``src/soapbot/plugins/index_signature.py``.

Runs on any change to the published JSON: an index edit without re-signing fails here,
which is the point — the signature is only meaningful if it is re-made whenever the
bytes change.
"""

from __future__ import annotations

import base64
import hashlib
import json
import sys
from pathlib import Path

#: Must match ``MARKET_PUBLIC_KEY_B64`` / ``MARKET_KEY_ID`` in the host. Public data.
PUBLIC_KEY_B64 = "N558xgdHlVRPqUY9Z9A9WtVUbGPAJBhqr1Nkqamuy1E="
KEY_ID = "c74ba4090e4ea9ca"
ALGORITHM = "ed25519"
SIGNATURE_FILENAME = "index.sig.json"
COVERED = ("plugins.json", "plugin_versions.json", "archived_plugins.json")


def canonical_payload(covers: dict[str, str]) -> bytes:
    document = {
        "algorithm": ALGORITHM,
        "covers": {name: covers[name] for name in sorted(covers)},
        "version": 1,
    }
    return json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def main(index_dir: Path) -> int:
    signature_path = index_dir / SIGNATURE_FILENAME
    if not signature_path.is_file():
        print(f"{SIGNATURE_FILENAME} is missing", file=sys.stderr)
        return 1
    try:
        document = json.loads(signature_path.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        print(f"{SIGNATURE_FILENAME} is not valid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(document, dict):
        print(f"{SIGNATURE_FILENAME} is not an object", file=sys.stderr)
        return 1
    if document.get("version") != 1 or document.get("algorithm") != ALGORITHM:
        print("unsupported signature version/algorithm", file=sys.stderr)
        return 1
    if document.get("key_id") != KEY_ID:
        print(f"unexpected key_id {document.get('key_id')!r}", file=sys.stderr)
        return 1

    covers = document.get("covers")
    if not isinstance(covers, dict) or "plugins.json" not in covers:
        print("signature does not cover plugins.json", file=sys.stderr)
        return 1
    for name, expected in covers.items():
        if name not in COVERED:
            print(f"signature covers an unknown file: {name}", file=sys.stderr)
            return 1
        path = index_dir / name
        if not path.is_file():
            print(f"signed file is missing: {name}", file=sys.stderr)
            return 1
        actual = "sha256:" + hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
        if actual != expected:
            print(f"{name} was modified after signing (re-sign the index)", file=sys.stderr)
            return 1

    try:
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError:
        print("cryptography is required to verify the signature", file=sys.stderr)
        return 2

    try:
        key = Ed25519PublicKey.from_public_bytes(base64.b64decode(PUBLIC_KEY_B64, validate=True))
        key.verify(
            base64.b64decode(str(document.get("signature") or ""), validate=True),
            canonical_payload({name: str(value) for name, value in covers.items()}),
        )
    except (InvalidSignature, ValueError):
        print("signature does not verify", file=sys.stderr)
        return 1

    print(f"signature OK ({KEY_ID}) covering: {', '.join(sorted(covers))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(Path(sys.argv[1] if len(sys.argv) > 1 else ".")))
