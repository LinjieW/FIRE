"""Funded ratio: a second ruler, orthogonal to the success rate.

ROADMAP 4.0 Phase 4. A success rate answers "how often did this plan survive
the sampler". A funded ratio answers a different question with no sampling in
it at all: discount what you owe, discount what you have, divide. Two plans
with the same success rate can have very different funded ratios, and the gap
is usually where the sequence risk lives.

Three things about the framing, all of which change the number.

**Everything is real, discounted at a real rate.** Today's dollars on both
sides. Mixing a nominal discount rate with real cash flows is the classic way
to get a funded ratio that is wrong by the whole inflation assumption, and it
looks fine.

**The discount rate is the user's, and it is visible.** Ruled 2026-08-14, on
the same principle as the medical premium, the annuity quote and the LTC
premium: the user can read today's real TIPS yield off TreasuryDirect, and
this app can only offer a round number. Nothing is bundled and nothing is
fetched. Unset means unset -- the ratio is `None` with a reason, never a
default rate quietly applied.

**Future contributions are NOT an asset here, on purpose.** The funded ratio
asks "if you stopped adding today, is what you have enough for what you owe".
Counting money you have not saved yet would answer "will you be funded", which
is what the Monte Carlo already answers. Stated in the payload so nobody reads
the number as the other question.

The floor is the user's too. Only they know which of their spending is
non-negotiable, and a split this app invented would put a number in front of
them that looks measured and is not.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import engine_adapter as ENG
from fire_v9_2_model import compute_ss_factor


@dataclass
class FundedRatioParams:
    """The two numbers this analysis will not guess.

    Both default to `None` and both are refused when absent. A default
    discount rate would silently decide the answer -- the ratio moves with it
    more than with anything else -- and a default floor would put this app's
    opinion of "essential spending" in front of somebody as if it were theirs.
    """
    #: Real (inflation-adjusted) discount rate, e.g. 0.02. The user reads
    #: today's TIPS yield; nothing is bundled and nothing is fetched.
    discount_rate_real: Optional[float] = None
    #: Annual non-negotiable spending in today's dollars. Whatever is left of
    #: `state.expenses_y0` above this is treated as discretionary.
    floor_annual_real: Optional[float] = None


def _pv_real(annual_real: float, first_age: int, last_age: int,
             today_age: int, rate: float) -> float:
    """PV of a level real cash flow, discounted from today's age.

    Discounted at the START of each year of receipt, so a payment beginning
    this year is not discounted at all. Years already past are skipped rather
    than counted at face value.
    """
    if annual_real <= 0 or last_age < first_age:
        return 0.0
    total = 0.0
    for age in range(int(first_age), int(last_age) + 1):
        offset = age - int(today_age)
        if offset < 0:
            continue
        total += float(annual_real) / ((1.0 + rate) ** offset)
    return total


def _income_stream_pv(streams, today_age: int, horizon_end: int,
                      rate: float) -> list:
    """PV of each modelled income stream, using the ADAPTER's compiled objects.

    Not re-derived from the raw config: the adapter already resolves start
    ages, durations and end ages, and a second implementation here would be a
    second answer to the same question.
    """
    rows = []
    for stream in (streams or ()):
        start = int(getattr(stream, "start_age", 0) or 0)
        end = getattr(stream, "end_age", None)
        duration = getattr(stream, "duration_years", None)
        if end is None and duration:
            end = start + int(duration) - 1
        if end is None:
            end = horizon_end
        rows.append({
            "kind": getattr(stream, "kind", "income"),
            "annual_real": float(getattr(stream, "annual_real", 0.0) or 0.0),
            "start_age": start,
            "end_age": int(end),
            "present_value": _pv_real(
                float(getattr(stream, "annual_real", 0.0) or 0.0),
                start, int(end), today_age, rate),
        })
    return rows


def _social_security_pv(ss, today_age: int, horizon_end: int,
                        rate: float) -> Optional[dict]:
    """PV of the modelled Social Security benefit.

    The claim-age factor comes from the engine's own `compute_ss_factor`, so
    this cannot drift from what the simulation pays. `None` when the module is
    off -- not a zero, because "no benefit modelled" and "a benefit worth
    nothing" are different statements.
    """
    if ss is None or not getattr(ss, "enabled", False):
        return None
    factor = compute_ss_factor(ss.claim_age, ss.fra_age)
    annual_real = float(ss.pia_monthly_y0) * 12.0 * float(factor)
    return {
        "annual_real": annual_real,
        "claim_age": int(ss.claim_age),
        "claim_factor": float(factor),
        "present_value": _pv_real(annual_real, int(ss.claim_age),
                                  horizon_end, today_age, rate),
    }


def compute(cfg: dict) -> dict:
    """Floor and total funded ratios, or a stated refusal."""
    group = (cfg.get("funded_ratio") or {})
    rate = group.get("discount_rate_real", None)
    floor = group.get("floor_annual_real", None)
    if rate is None or floor is None:
        missing = [name for name, value in
                   (("discount_rate_real", rate), ("floor_annual_real", floor))
                   if value is None]
        return {
            "applicable": False,
            "reason": ("needs both a real discount rate and a floor spending "
                       "level; neither is guessed"),
            "missing": missing,
            "floor_funded_ratio": None,
            "total_funded_ratio": None,
            "counts_future_contributions": False,
        }

    kwargs = ENG.build_kwargs(cfg, False)
    state = kwargs["state"]
    today_age = int(state.start_age)
    retire_age = today_age + int(state.accum_years)
    horizon_end = retire_age + int(state.retire_horizon) - 1
    rate = float(rate)
    floor = float(floor)

    spend = float(state.expenses_y0)
    discretionary = max(0.0, spend - floor)

    floor_pv = _pv_real(floor, retire_age, horizon_end, today_age, rate)
    discretionary_pv = _pv_real(discretionary, retire_age, horizon_end,
                                today_age, rate)

    portfolio = float(kwargs["initial"].total)
    streams = _income_stream_pv(kwargs.get("income_streams"), today_age,
                                horizon_end, rate)
    social_security = _social_security_pv(kwargs.get("ss"), today_age,
                                          horizon_end, rate)
    assets = (portfolio + sum(row["present_value"] for row in streams)
              + (social_security["present_value"] if social_security else 0.0))

    return {
        "applicable": True,
        "reason": None,
        "discount_rate_real": rate,
        "floor_annual_real": floor,
        "discretionary_annual_real": discretionary,
        "today_age": today_age,
        "retire_age": retire_age,
        "horizon_end_age": horizon_end,
        "assets": {
            "portfolio_today": portfolio,
            "income_streams": streams,
            "social_security": social_security,
            "total": assets,
        },
        "liabilities": {
            "floor_present_value": floor_pv,
            "discretionary_present_value": discretionary_pv,
            "total_present_value": floor_pv + discretionary_pv,
        },
        "floor_funded_ratio": (assets / floor_pv) if floor_pv > 0 else None,
        "total_funded_ratio": ((assets / (floor_pv + discretionary_pv))
                               if (floor_pv + discretionary_pv) > 0 else None),
        #: Said in the payload, not only in this module's docstring: the ratio
        #: asks "if you stopped saving today", so money not yet saved is not on
        #: the asset side. Reading it as "will I be funded" overstates it.
        "counts_future_contributions": False,
        "basis": ("Real dollars on both sides, discounted at the real rate you "
                  "supplied. Future contributions are excluded on purpose: "
                  "this asks whether what you already have covers what you "
                  "owe, which is a different question from the Monte Carlo's."),
    }
