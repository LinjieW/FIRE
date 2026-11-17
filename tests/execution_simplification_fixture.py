"""The one plan both execution-simplification checks drive.

`tests/test_execution_simplification.py` runs it through the source engine;
`tests/frozen_ui_smoke.py` runs it through a BUILT bundle at promotion time.
Until 2026-11-16 each carried its own copy of the numbers. MODEL_AUDIT A1
moved the plan, the source test was re-tuned the same day, and the frozen
copy -- which no gate runs -- kept the old figures and refused the
`v14.0-installed-47` promotion (41/42). Lesson 23b again: a fact stored
twice, one copy outside the gate. Stored once now.

Why these numbers: $25,000 at 4% stopped splitting any path once the typed
spending became the whole budget (A1). Spending and SWR rise together, so
the FI target stays $625,000 and the same paths reach FIRE at 76; path 0
splits from $34,000 / 5.44% up, and 36,000 / 5.76% sits inside that band.
"""
from __future__ import annotations

import copy

PATHS = 8
SEED = 15

#: What `execution_simplification_stress(stress_config(...), PATHS, SEED)`
#: returns on this plan, measured 2026-11-16 on the source engine.
EXPOSED_PATHS = 3
BASELINE_SUCCESS = 1.0
SIMPLIFIED_SUCCESS = 2 / 3


def stress_config(base: dict) -> dict:
    """The fixture, laid over `base` (the shipped default or its served
    copy). Returns a new dict; `base` is not touched."""
    cfg = copy.deepcopy(base)
    cfg["state"].update({"start_age": 75, "accum_years": 1,
                         "retire_horizon": 30, "expenses_y0": 36_000,
                         "swr_pref": 0.0576})
    cfg["initial"] = {"pretax_401k": 500_000, "roth_ira": 0,
                      "hsa": 0, "taxable": 0}
    cfg["social_security"]["enabled"] = False
    cfg["roth_ladder"]["enabled"] = False
    return cfg
