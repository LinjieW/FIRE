"""presets.py — starting configs served to the UI, all in the v9.8 grouped
schema (see engine_adapter.default_config). Every value is de-identified and fully
editable in the form; these are only starting points.
"""
import copy

import engine_adapter as ENG


def _baseline() -> dict:
    return copy.deepcopy(ENG.default_config())


def _with_relocation() -> dict:
    """The default view: home vs relocation side-by-side. Numbers reproduce the
    official de-identified baseline; the destination is a generic lower-cost
    overseas region (relocation @41, cost-of-living 0.85)."""
    c = _baseline()
    c["name"] = "Baseline · home vs relocation"
    c["relocation"].update({"enabled": True, "relocation_age": 41, "col_ratio": 0.85})
    return c


def _home_only() -> dict:
    c = _baseline()
    c["name"] = "Baseline · home only"
    c["relocation"]["enabled"] = False
    return c


def _lean() -> dict:
    """A lighter, lower-income early-retirement example."""
    c = _home_only()
    c["name"] = "Lean · low-spend early retirement"
    c["state"].update({"expenses_y0": 32000, "swr_pref": 0.035})
    c["initial"].update({"pretax_401k": 40000, "roth_ira": 20000, "hsa": 5000, "taxable": 25000})
    c["contributions"].update({"base_salary_pre": 95000, "bonus_pre": 0, "ot_income_pre": 0})
    c["roth_ladder"]["annual_conversion_y0"] = 30000
    c["milestones"] = [500000, 1000000]
    return c


PRESETS = {
    "baseline_reloc": {"label": "Baseline · home vs relocation 基线·本土vs搬迁", "config": _with_relocation()},
    "baseline": {"label": "Baseline · home only 基线·仅本土", "config": _home_only()},
    "lean": {"label": "Lean · low-spend early FIRE 低支出早退休", "config": _lean()},
}
