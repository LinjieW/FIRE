"""Get your plans out of the archive without this app, or any part of it.

S10 ③ (idea bank), the data-longevity escape hatch. The sharpest fair
criticism of a local-first app maintained by one person is not "what if it has
a bug" -- it is "what if it stops existing". A forty-year financial history is
not something to hand to software on the promise that someone keeps shipping.

**This script imports nothing but the Python standard library.** No numpy, no
server package, no engine. It reads the SQLite file directly. If everything
else in this repository disappeared, this one file plus a stock Python would
still return your plans as JSON -- and if THIS file disappeared too, the
recipe it implements is short enough to retype from the comments below.

**The archive is plain SQLite on purpose, and that was verified rather than
assumed**: seven tables, and the config lives in
`plan_versions.normalized_config_json` as ordinary JSON. Nothing is encrypted,
nothing is a custom binary format, nothing needs a library that might not
build in ten years.

**CI runs this against a real archive on every gate.** A recovery recipe that
nobody executes is a promise, and this project has already paid for a promise
nothing enforced -- a docstring saying pools were not nested, which stayed
true-sounding while becoming false and killed decision studies for two
releases. `tests/test_recover_without_app.py` is what stops that here.

The recipe, in case you are retyping it:

    import sqlite3, json
    con = sqlite3.connect("your-archive.sqlite3")
    for plan_id, name in con.execute("select id, display_name from plans"):
        row = con.execute(
            "select normalized_config_json from plan_versions "
            "where plan_id = ? order by created_at desc limit 1",
            (plan_id,)).fetchone()
        print(name, json.loads(row[0]) if row else None)

Usage:
    python3 tools/recover_without_app.py ~/path/to/archive.sqlite3 [outdir]
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys

#: Every table the recipe reads. Named so a failure says which table is
#: missing rather than "no such column", which is what a person staring at an
#: unfamiliar file at a bad moment actually needs.
REQUIRED_TABLES = ("plans", "plan_versions")


def recover(archive_path: str) -> list:
    """Every plan's latest configuration, as plain dictionaries."""
    if not os.path.exists(archive_path):
        raise SystemExit("No such archive: %s" % archive_path)
    con = sqlite3.connect("file:%s?mode=ro" % archive_path, uri=True)
    present = {row[0] for row in con.execute(
        "select name from sqlite_master where type='table'")}
    missing = [t for t in REQUIRED_TABLES if t not in present]
    if missing:
        raise SystemExit(
            "This file is missing %s, so it is not a FIRE archive (tables "
            "found: %s)" % (", ".join(missing), ", ".join(sorted(present))))

    out = []
    for plan_id, name, created in con.execute(
            "select id, display_name, created_at from plans order by created_at"):
        row = con.execute(
            "select normalized_config_json, source_config_json, created_at "
            "from plan_versions where plan_id = ? "
            "order by created_at desc limit 1", (plan_id,)).fetchone()
        config = None
        if row is not None:
            # `normalized` is what the engine ran; `source` is what was typed.
            # Preferring normalized means the recovered plan reproduces the
            # numbers that were on screen, which is the point of recovering it.
            raw = row[0] or row[1]
            if raw:
                config = json.loads(raw)
        out.append({"plan_id": plan_id, "name": name, "created_at": created,
                    "version_created_at": row[2] if row else None,
                    "config": config,
                    # Said out loud rather than left as an empty dict: a plan
                    # row with no version is a real state (created, never
                    # saved), and reporting it as {} would read as an empty
                    # plan rather than as an absent one.
                    "note": None if config else
                            "this plan has no saved version in the archive"})
    return out


def main() -> int:
    if not 2 <= len(sys.argv) <= 3:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    plans = recover(sys.argv[1])
    if len(sys.argv) == 3:
        os.makedirs(sys.argv[2], exist_ok=True)
        for plan in plans:
            safe = "".join(c if c.isalnum() or c in "-_ " else "_"
                           for c in (plan["name"] or plan["plan_id"]))
            path = os.path.join(sys.argv[2], "%s.json" % safe.strip() or "plan")
            with open(path, "w", encoding="utf-8") as handle:
                json.dump(plan, handle, ensure_ascii=False, indent=1)
            print("wrote %s" % path)
    else:
        json.dump(plans, sys.stdout, ensure_ascii=False, indent=1)
        print()
    recovered = sum(1 for p in plans if p["config"])
    print("\n%d plan(s) in the archive, %d with a saved configuration."
          % (len(plans), recovered), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
