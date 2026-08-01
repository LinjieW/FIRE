#!/usr/bin/env python3
"""Create the content-bound identity embedded in a frozen candidate."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.release_identity import (  # noqa: E402
    MANIFEST_VERSION,
    build_data_manifest,
    build_runtime_manifest,
    canonical_json,
    sha256_json,
)


IDENTITY_CANONICALIZER = "release-json-c14n-v1"


def build_identity(root: str | Path) -> dict:
    runtime = build_runtime_manifest(root)
    data = build_data_manifest(root)
    payload = {
        "manifest_version": MANIFEST_VERSION,
        "manifest_type": "frozen_runtime_identity",
        "identity_canonicalizer": IDENTITY_CANONICALIZER,
        "runtime_manifest": runtime,
        "data_manifest": data,
        "runtime_manifest_sha256": runtime["component_sha256"],
        "code_manifest_sha256": runtime["component_sha256"],
        "data_manifest_sha256": data["component_sha256"],
    }
    payload["identity_sha256"] = sha256_json(payload)
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".")
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(canonical_json(build_identity(args.root)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
