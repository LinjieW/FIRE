"""Run a built app's private smoke mode through every runnable CPU slice.

Two modes, and the difference is narrow on purpose.

The default is what a *candidate* and a *newly installed* app must pass, and it
is unchanged in effect: the marker has to report `ok`, the exact slice, and both
`persistence` and `build_identity`.

`--allow-legacy-marker` exists for one caller — `gate_previous` in
`tools/promote.py`, the gate a retained previous install is judged by before it
is moved aside. Bundles built before 2026-07-17 emit
`{"engine", "machine", "numpy", "ok"}` and nothing else, because `persistence`
and `build_identity` arrived with 429d560. The strict mode therefore refuses
every such bundle, and promotion refused to upgrade over the app users actually
have — safely, before moving anything, but permanently.

What the legacy mode proves and what it does not: it proves the old app starts
on that slice and completes the engine smoke *of its era*. It does not prove
persistence or build identity, and it must never be read as proving them —
those are capabilities that bundle never had. That is why absence is the only
thing it forgives. If either key appears, both must be strictly `True`: a bundle
new enough to report one of them is new enough to report both, and a partial,
null, false or merely truthy value is a bundle saying something is wrong.
"""

from __future__ import annotations

import json
import pathlib
import platform
import subprocess
import sys


MARKER = "FIRE_FROZEN_SMOKE="
LEGACY_FLAG = "--allow-legacy-marker"

# The two fields a pre-2026-07-17 bundle cannot emit.
_MODERN_KEYS = ("persistence", "build_identity")


def _nonempty_str(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def validate_marker(payload, arch: str, *, allow_legacy: bool = False):
    """Return None when the marker is acceptable, else a reason string.

    Split out of `main()` so the accept/reject matrix can be tested without
    building an app for each case — the cases that matter most are markers no
    bundle in this repository produces any more.
    """
    if not isinstance(payload, dict):
        return f"marker is not an object: {payload!r}"
    # `is True`, not truthiness. A marker reporting `ok: 1` is a marker whose
    # author meant something other than "the smoke passed".
    if payload.get("ok") is not True:
        return f"ok is not True: {payload.get('ok')!r}"
    if payload.get("machine") != arch:
        return f"machine {payload.get('machine')!r} != {arch!r}"
    for field in ("engine", "numpy"):
        if not _nonempty_str(payload.get(field)):
            return f"{field} is not a non-empty string: {payload.get(field)!r}"

    present = [key for key in _MODERN_KEYS if key in payload]
    if not present and allow_legacy:
        # The one thing legacy mode forgives: both absent. See the module
        # docstring for what this does and does not establish.
        return None
    if len(present) != len(_MODERN_KEYS):
        missing = [key for key in _MODERN_KEYS if key not in payload]
        return (f"missing {', '.join(missing)}"
                + ("" if allow_legacy else
                   f" (rerun with {LEGACY_FLAG} only for a pre-2026-07-17 "
                   f"previous install)"))
    for key in _MODERN_KEYS:
        if payload.get(key) is not True:
            return f"{key} is not True: {payload.get(key)!r}"
    return None


def architecture_available(arch: str) -> bool:
    return subprocess.run(
        ["/usr/bin/arch", f"-{arch}", "/usr/bin/true"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def main() -> int:
    argv = list(sys.argv[1:])
    allow_legacy = LEGACY_FLAG in argv
    argv = [arg for arg in argv if arg != LEGACY_FLAG]
    if len(argv) != 1:
        print(f"usage: frozen_smoke.py [{LEGACY_FLAG}] /path/to/FIRE Modeling.app",
              file=sys.stderr)
        return 2

    app = pathlib.Path(argv[0]).resolve()
    executable = app / "Contents" / "MacOS" / "FIRE Modeling"
    if not executable.is_file():
        print(f"FROZEN SMOKE: missing executable: {executable}", file=sys.stderr)
        return 2

    runnable = [arch for arch in ("arm64", "x86_64") if architecture_available(arch)]
    if not runnable:
        runnable = [platform.machine()]

    for arch in runnable:
        result = subprocess.run(
            ["/usr/bin/arch", f"-{arch}", str(executable), "--frozen-smoke"],
            text=True,
            capture_output=True,
            timeout=90,
            check=False,
        )
        marker_line = next(
            (line for line in result.stdout.splitlines() if line.startswith(MARKER)), None
        )
        if result.returncode or marker_line is None:
            sys.stderr.write(result.stdout)
            sys.stderr.write(result.stderr)
            print(f"FROZEN SMOKE: FAIL ({arch}, exit {result.returncode})", file=sys.stderr)
            return 1
        try:
            payload = json.loads(marker_line[len(MARKER):])
        except json.JSONDecodeError as exc:
            print(f"FROZEN SMOKE: FAIL ({arch}, bad marker: {exc})", file=sys.stderr)
            return 1
        reason = validate_marker(payload, arch, allow_legacy=allow_legacy)
        if reason is not None:
            print(f"FROZEN SMOKE: FAIL ({arch}, {reason}; {payload!r})",
                  file=sys.stderr)
            return 1
        legacy = any(key not in payload for key in _MODERN_KEYS)
        print(
            f"FROZEN SMOKE: PASS ({arch}, engine {payload['engine']}, "
            f"numpy {payload['numpy']}"
            # Said out loud on every legacy pass. A run that forgave two absent
            # fields must not be quoted later as though it had checked them.
            + (", legacy marker: engine smoke only, persistence and build "
               "identity NOT established" if legacy else "")
            + ")"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
