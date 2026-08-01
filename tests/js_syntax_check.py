"""Mandatory release gate: parse every shipped JavaScript file with Node."""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys


def main() -> int:
    node = shutil.which("node")
    if not node:
        print("JS SYNTAX: FAIL (Node.js is required for release builds)", file=sys.stderr)
        return 2

    root = pathlib.Path(sys.argv[1] if len(sys.argv) > 1 else pathlib.Path(__file__).parents[1] / "web")
    files = sorted(root.rglob("*.js"))
    if not files:
        print(f"JS SYNTAX: FAIL (no JavaScript files under {root})", file=sys.stderr)
        return 2

    failed = []
    for path in files:
        result = subprocess.run(
            [node, "--check", str(path)], text=True, capture_output=True, check=False
        )
        if result.returncode:
            failed.append(path)
            sys.stderr.write(result.stderr or result.stdout)

    if failed:
        print(f"JS SYNTAX: FAIL ({len(failed)}/{len(files)} invalid)", file=sys.stderr)
        return 1
    print(f"JS SYNTAX: PASS ({len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
