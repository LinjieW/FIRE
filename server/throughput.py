"""How fast THIS machine runs paths, measured rather than assumed.

Roadmap 5.0 Phase 1. The decision study's cost panel says "20 engine runs" and
nothing about time. Measured on 2026-08-16, the same study took **42 seconds**
on an idle machine and **5,314 seconds** on one also running the full gate --
a factor of 126. A user presses the button and sees a progress bar with no
estimate at all; the wait might be a minute or might be an hour.

**Everything here is calibrated on the user's own machine.** There is no
built-in seconds-per-path constant, because such a constant would be measured
on mine and shown to them as if it were theirs. With no samples yet, the
estimate is `None` with a reason -- the same discipline as the medical
premium, the annuity quote, and the funded ratio's discount rate.

**Deliberately outside the archive.** `elapsed_s` is wall-clock, and
`persistence` strips it before archiving precisely so a snapshot stays
deterministic. Throughput samples are local observations about a machine, not
part of any run's record, so they live in a side file beside the archive --
the same placement `working_draft` uses and for a related reason: this must
keep working when the control journal is latched.

**What it will not do.** It will not promise a number it cannot stand behind.
The estimate carries the assumption it was computed under (an unloaded
machine), and the spread that made this phase necessary is the reason that
sentence travels with the figure rather than sitting in a footnote.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

FILENAME = "throughput-samples.json"

#: Keep the recent past only. A machine's speed changes -- a laptop on battery,
#: a different macOS release, an app left open -- and an average over a year of
#: samples describes a machine nobody is using now.
MAX_SAMPLES = 20

#: Below this, a sample says more about process startup than about throughput.
MIN_PATHS = 500

#: Samples are kept PER KIND of work, and an estimate only ever uses samples
#: of its own kind.
#:
#: The first version recorded one pooled rate and divided by the worker count
#: for parallel work. That was modelling, and it was wrong by 3x: a single
#: run's 5.6s already contains its own chunk parallelism, so dividing again
#: double-counted it and estimated 13s for a study measured at 42s. A
#: confidently wrong number is the failure this phase exists to remove, not
#: one to introduce.
#:
#: Measuring each shape separately needs no model of how parallelism composes.
#: The cost is that a kind with no history says so instead of borrowing
#: another kind's rate -- which is the right refusal, since twenty runs cost
#: 7.5x one run here, neither 20x nor 2x.
RUN = "run"
STUDY = "study"
KINDS = (RUN, STUDY)


def samples_path(archive_path: str) -> Path:
    """`support_root/throughput-samples.json`, derived without opening anything.

    Same construction as `working_draft.draft_path`, and for the same reason:
    building a `BackupRestoreManager` opens the control journal, and this has
    to work while that journal is latched.
    """
    return Path(os.path.abspath(os.path.expanduser(archive_path))).parent / FILENAME


def read(archive_path: str, kind: str = RUN) -> list:
    """Stored samples, or `[]` for every damaged-file case.

    A corrupt telemetry file must never take the app down: this is an
    estimate, and the honest degradation is "no estimate yet".
    """
    path = samples_path(archive_path)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    rows = []
    for row in data:
        if not isinstance(row, dict):
            continue
        if str(row.get("kind") or RUN) != kind:
            continue
        try:
            units = int(row["units"])
            elapsed = float(row["elapsed_s"])
        except (KeyError, TypeError, ValueError):
            continue
        if units >= MIN_PATHS and elapsed > 0:
            rows.append({"units": units, "elapsed_s": elapsed,
                         "kind": kind,
                         "mode": str(row.get("mode") or "sequential")})
    return rows[-MAX_SAMPLES:]


def _read_all(archive_path: str) -> list:
    path = samples_path(archive_path)
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return []
    return data if isinstance(data, list) else []


def record(archive_path: str, *, units: int, elapsed_s: float,
           kind: str = RUN, mode: str = "sequential") -> None:
    """Add one observation. Never raises -- a failure here must not fail a run.

    Small runs are dropped rather than stored: at a few hundred paths the
    number measures interpreter and process startup, and mixing that into a
    throughput estimate makes long runs look slower than they are.
    """
    if kind not in KINDS:
        return
    try:
        units = int(units)
        elapsed_s = float(elapsed_s)
    except (TypeError, ValueError):
        return
    if units < MIN_PATHS or elapsed_s <= 0:
        return
    try:
        rows = _read_all(archive_path)
        rows.append({"kind": kind, "units": units, "elapsed_s": elapsed_s,
                     "mode": str(mode or "sequential")})
        path = samples_path(archive_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(rows[-(MAX_SAMPLES * len(KINDS)):], handle)
        os.replace(tmp, path)
    except OSError:
        return


def estimate(archive_path: str, *, units: int, kind: str = RUN) -> dict:
    """Seconds this machine will probably need for `units` of work of `kind`.

    `units` is paths for a run and total simulated paths for a study, so a
    rate learned from one study transfers to a study of a different size.

    NO model of parallel speedup. The rate is whatever this machine actually
    achieved on work of this shape, parallelism included, so nothing has to be
    divided by a worker count -- the mistake the first version made, which
    estimated 13s for a study measured at 42s.

    `applicable: False` when this kind has not been timed here yet. Borrowing
    the other kind's rate would be a confident wrong number, and twenty runs
    cost 7.5x one run on this machine -- neither 20x nor 2x.
    """
    if kind not in KINDS:
        return _unknown("unknown work kind %r" % (kind,))
    try:
        units = int(units)
    except (TypeError, ValueError):
        return _unknown("units must be a number")
    if units <= 0:
        return _unknown("an estimate needs a positive amount of work")

    rows = read(archive_path, kind)
    if not rows:
        return _unknown(
            "nothing of this kind has been timed on this machine yet, so "
            "there is no estimate to give. Run one and this fills in.")

    # Median rather than mean: one sample taken while something else was
    # compiling should not move every later estimate.
    rates = sorted(row["units"] / row["elapsed_s"] for row in rows)
    middle = len(rates) // 2
    rate = (rates[middle] if len(rates) % 2
            else (rates[middle - 1] + rates[middle]) / 2.0)
    if rate <= 0:
        return _unknown("the recorded samples give no usable rate")

    return {
        "applicable": True,
        "seconds": units / rate,
        "kind": kind,
        "samples": len(rows),
        "units_per_second": rate,
        "basis": ("Measured on THIS machine from %d recent %s(s) of the same "
                  "shape. It assumes the machine is no busier than it was "
                  "then -- the same study measured 42s idle and 5,314s while "
                  "a full test suite ran, so treat this as a floor rather "
                  "than a promise." % (len(rows), kind)),
    }


def _unknown(reason: str) -> dict:
    return {"applicable": False, "seconds": None, "samples": 0,
            "reason": reason}
