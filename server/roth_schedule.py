"""Multi-year Roth conversion schedules, shown as a frontier rather than a pick.

Three rulings shape this file, all made 2026-08-14.

**Search a SCHEDULE, not one number.** The existing `/api/roth_opt` sweeps a
single annual amount. Real ladders are not flat: when you convert matters as
much as how much, and the engine agrees -- converting $40k from 45 to 55 and
the same $40k from 55 to 65 differ by 15 points of survival on the default
plan. A candidate here is a WINDOW (start age, end age, annual amount), which
is a per-year schedule: the amount in those years, zero outside them.

Not a free per-year vector, and that is a deliberate limit rather than a
shortcut. The roadmap's acceptance criterion for this work is "more
explainable than MaxiFi", whose single-utility-function opacity is the named
cautionary tale. A free vector over thirty years is both combinatorially
enormous and impossible to explain in a sentence; "convert $40k a year from 55
to 63" is a plan somebody can act on and argue with.

**Expose the trade-off; do not resolve it.** No objective function, no "best".
Every candidate reports survival AND after-tax terminal wealth, and the
non-dominated set is marked. Choosing between more spending-security and more
terminal wealth is the user's call, and encoding a preference nobody ruled on
is exactly what the optimizer audit refused to do implicitly.

**Every year visible.** Each candidate carries the conversions the engine
ACTUALLY executed year by year, from the engine's own record. The request and
the execution differ whenever the caps bite -- the pretax balance emptying, or
the 4x taxable buffer -- and a table showing the request would be showing a
plan that was never run.
"""
from __future__ import annotations

import copy
from typing import Optional

import numpy as np

import engine_adapter as ENG
import fire_v9_8_model as V98

#: Annual conversion amounts, as FRACTIONS of the pretax balance the plan
#: actually reaches at retirement -- not fixed dollars.
#:
#: Fixed dollars was the first version and it was wrong in a way worth
#: recording: $75k a year is absurd on a $200k plan and trivial on a $5M one,
#: so on the default plan every conversion arm was dominated and the frontier
#: collapsed to "do nothing". That is a true statement about $75k, and a
#: useless one about conversions.
AMOUNT_FRACTIONS = (0.03, 0.06, 0.12)

#: Window shapes, as offsets from the plan's retirement age. `None` for the end
#: means "run to the RMD age", which is the deadline the whole manoeuvre exists
#: to beat: once RMDs start, the pretax balance is being drawn down for you.
WINDOWS = (
    ("early", 0, 8),
    ("middle", 5, 15),
    ("to_rmd", 0, None),
)


def _retire_age(cfg: dict) -> int:
    state = ENG.build_kwargs(cfg, False)["state"]
    return int(state.start_age) + int(state.accum_years)


def pretax_at_retirement(cfg: dict, seed: int) -> Optional[float]:
    """The pretax balance this plan actually reaches, on one path.

    `None` if the plan never reaches retirement on that path -- and the caller
    must then not invent a scale, because a conversion ladder for a plan that
    never retires is not a smaller ladder, it is a different question.
    """
    plan = copy.deepcopy(cfg)
    plan.setdefault("roth_ladder", {})["enabled"] = False
    kwargs = ENG.build_kwargs(plan, False)
    inner = kwargs.pop("config")
    run = V98.simulate_lifecycle_v98(
        config=inner, rng=np.random.default_rng(int(seed)), **kwargs)
    path = run.get("accum_path") or []
    if not run.get("reached_fire") or not path:
        return None
    accounts = path[-1].get("accounts")
    return float(accounts.pretax_401k) if accounts is not None else None


def candidates(cfg: dict, scale: Optional[float]) -> list:
    """Every (label, start, end, amount) this search will price.

    The zero-conversion arm is always present and always first: without a
    do-nothing arm a frontier can only say which conversion is least bad, and
    on the default plan doing nothing genuinely wins.
    """
    retire = _retire_age(cfg)
    rmd_age = int((cfg.get("tax_true") or {}).get("rmd_age", 75))
    out = [{"label": "no_conversion", "start_age": retire, "end_age": retire,
            "amount": 0.0}]
    if scale is None or scale <= 0:
        return out
    for name, lo, hi in WINDOWS:
        start = retire + lo
        end = (rmd_age - 1) if hi is None else (retire + hi)
        if end <= start:
            continue
        for fraction in AMOUNT_FRACTIONS:
            amount = round(float(scale) * fraction, -3)
            if amount <= 0:
                continue
            out.append({"label": "%s_%dpct" % (name, int(fraction * 100)),
                        "start_age": start, "end_age": end,
                        "amount": amount, "fraction_of_pretax": fraction})
    return out


def _configured(cfg: dict, candidate: dict) -> dict:
    plan = copy.deepcopy(cfg)
    plan.setdefault("tax_true", {})["enabled"] = True
    ladder = plan.setdefault("roth_ladder", {})
    ladder["enabled"] = candidate["amount"] > 0
    ladder["start_age"] = int(candidate["start_age"])
    ladder["end_age"] = int(candidate["end_age"])
    ladder["annual_conversion_y0"] = float(candidate["amount"])
    return plan


def executed_schedule(cfg: dict, candidate: dict, seed: int) -> list:
    """What the engine actually converted, year by year, on one path.

    ONE representative path, said plainly here and in the payload: the caps
    depend on balances, so the executed schedule genuinely differs between
    paths. Averaging them would invent a year-by-year plan no path ever ran.
    """
    if candidate["amount"] <= 0:
        return []
    kwargs = ENG.build_kwargs(_configured(cfg, candidate), False)
    inner = kwargs.pop("config")
    run = V98.simulate_lifecycle_v98(
        config=inner, rng=np.random.default_rng(int(seed)), **kwargs)
    rows = (run.get("withdrawal") or {}).get("roth_conversion_by_age") or []
    return [{"age": int(age), "converted_nominal": float(amount)}
            for age, amount in rows]


def pareto_front(points: list) -> list:
    """Labels of the non-dominated candidates.

    Dominated means another candidate is at least as good on BOTH axes and
    strictly better on one. Ties are kept rather than broken: two plans that
    genuinely match on both numbers are two answers, not one winner and one
    loser, and picking between them would be the preference this refuses to
    encode.
    """
    front = []
    for point in points:
        survival = point["lifetime_success"]
        wealth = point["terminal_after_tax_real_p50"]
        if survival is None or wealth is None:
            continue
        dominated = any(
            other is not point
            and (other["lifetime_success"] or 0.0) >= survival
            and (other["terminal_after_tax_real_p50"] or 0.0) >= wealth
            and ((other["lifetime_success"] or 0.0) > survival
                 or (other["terminal_after_tax_real_p50"] or 0.0) > wealth)
            for other in points)
        if not dominated:
            front.append(point["label"])
    return front


def search(cfg: dict, paths: int = 1_200, seed: int = 4242) -> dict:
    """Price every candidate schedule and mark the frontier."""
    n = max(300, min(int(paths), 3_000))
    scale = pretax_at_retirement(cfg, int(seed))
    points = []
    for candidate in candidates(cfg, scale):
        summary = ENG.summary(_configured(cfg, candidate), n, int(seed),
                              relocation_on=False)
        points.append({
            "label": candidate["label"],
            "start_age": candidate["start_age"],
            "end_age": candidate["end_age"],
            "amount": candidate["amount"],
            "lifetime_success": summary.get("lifetime_success"),
            "terminal_after_tax_real_p50":
                summary.get("terminal_after_tax_real_p50"),
            "true_tax_p50": summary.get("true_tax_p50"),
            "schedule": executed_schedule(cfg, candidate, int(seed)),
        })
    front = pareto_front(points)
    for point in points:
        point["on_frontier"] = point["label"] in front
    return {
        "n_paths": n,
        "seed": int(seed),
        # Named rather than implied: this endpoint has NO objective, and a
        # caller looking for `best` should find its absence explained.
        "objective": None,
        "objective_note": (
            "No objective function. Survival and after-tax terminal wealth are "
            "reported for every candidate and the non-dominated set is marked; "
            "choosing between them is a preference this tool does not hold."),
        "schedule_basis": (
            "Each schedule is what the engine actually converted on ONE "
            "representative path at this seed. Caps depend on balances, so "
            "other paths convert different amounts."),
        "pretax_at_retirement": scale,
        "scale_note": (
            "Conversion amounts are fractions of the pretax balance this plan "
            "reaches at retirement on one representative path, not fixed "
            "dollars: a fixed amount is absurd on a small plan and trivial on "
            "a large one."
            if scale else
            "This plan does not reach retirement on the representative path, "
            "so no conversion scale could be derived and only the "
            "do-nothing arm is priced. That is not a zero -- it is a "
            "different question."),
        "points": points,
        "frontier": front,
    }
