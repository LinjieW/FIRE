"""Phase 4 · turning what actually happened into what a policy can read.

The check-in ledger records money as it was at the time: `closing_value_minor`
for a period is nominal, in that period's currency, and this app has no
inflation feed to deflate it -- it makes no network requests at all, which is a
privacy claim it keeps literally.

So an absolute threshold would drift. "Below $800,000" means one thing in 2026
and another in 2041, and a guardrail whose meaning quietly changes is worse than
none.

What removes the problem is measuring against the plan's own forecast **for the
same period**. Both sides are then in the same period's money, the ratio is
unit-free, and "20% below what the plan projected for this year" means exactly
the same thing in every year. That is also how a person actually thinks about
it: not "am I below a number" but "am I behind where I should be".

So the observation this seam produces is a RATIO, and the policies it is read
with carry ratio thresholds. A period with no archived forecast to compare
against is unmeasured -- `guardrails.evaluate` holds a streak across it rather
than clearing it, because "we have nothing to compare to" is not "you are
fine".
"""
from __future__ import annotations

from typing import Optional

import guardrails as G

#: Ratio-shaped defaults, anchored on the plan rather than on an amount. The
#: numbers mirror `guardrails.default_policies` -- 20% behind fires, 10% behind
#: clears -- but as fractions of plan rather than of today's portfolio.
RATIO_BASELINE = {"portfolio_real": 1.0, "spending_real": 1.0,
                  "income_real": 1.0}


def _minor_to_major(value, exponent) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value) / (10 ** int(exponent or 0))
    except (TypeError, ValueError):
        return None


def observations_from_checkins(checkins: list,
                               expected: Optional[list] = None) -> list:
    """Oldest-first observations, each a ratio of actual to plan.

    `expected` is what the plan projected for each period, aligned by ordinal
    with the non-superseded check-ins -- see `expected_from_forecasts` for why
    the join is an ordinal and not a key. A period with no projection yields an
    unmeasured observation rather than a ratio against a guess.
    """
    plan = list(expected or [])
    out = []
    ordinal = -1
    for row in sorted(checkins or [],
                      key=lambda r: (r.get("forecast_period_start") or "",
                                     r.get("created_at") or "")):
        # A superseded check-in is a correction that was replaced; reading both
        # would count one period twice and could manufacture a streak.
        if row.get("supersedes_checkin_id"):
            continue
        ordinal += 1
        period_end = row.get("forecast_period_end")
        exponent = row.get("portfolio_currency_exponent")
        actual = _minor_to_major(row.get("closing_value_minor"), exponent)
        projected = plan[ordinal] if ordinal < len(plan) else None
        ratio = None
        if actual is not None and projected:
            ratio = actual / float(projected)
        out.append({
            "period_end": period_end,
            "checkin_id": row.get("checkin_id"),
            "portfolio_real": ratio,
            # Not derivable from a check-in: the ledger records portfolio
            # values, not a spending or income series. Left unmeasured rather
            # than imputed -- see guardrail_study for what a fabricated zero
            # looks like when it reaches a rate.
            "spending_real": None,
            "income_real": None,
            "actual": actual,
            "expected": projected,
            "unmeasured_reason": ("" if ratio is not None else
                                  "no archived forecast for this period, so "
                                  "there is nothing to compare against"),
        })
    return out


def ratio_policies(baseline: Optional[dict] = None) -> list:
    """The default set, expressed against plan rather than against an amount."""
    return [
        G.Policy("behind_plan", G.PORTFOLIO_BELOW_BAND, 0.80,
                 G.CUT_PERMANENT_SPENDING, recovery=0.90,
                 note="20% behind what the plan projected for the period, "
                      "clearing at 10% behind"),
    ]


def status_from_history(checkins: list, expected: Optional[list] = None,
                        *, policies: Optional[list] = None,
                        snoozes: Optional[list] = None) -> dict:
    """The home page's one word, from the plan's real history."""
    observations = observations_from_checkins(checkins, expected)
    chosen = policies if policies is not None else ratio_policies()
    status = G.plan_status(chosen, observations, snoozes=snoozes)
    measured = [o for o in observations if o["portfolio_real"] is not None]
    status["observations"] = len(observations)
    status["measured_observations"] = len(measured)
    # A status computed from too few comparisons is not On Track, it is
    # unknown. Saying On Track here would be the reassuring-zero mistake in
    # its most consequential form: the home page's headline.
    status["enough_history"] = len(measured) >= 2
    if not status["enough_history"]:
        status["state_reason"] = (
            "fewer than two periods have both a check-in and an archived "
            "forecast to compare against, so there is not yet enough history "
            "for a status -- Action needs two consecutive breaches by design")
    status["basis"] = ("each period's recorded closing value against what the "
                       "plan projected for that same period, so the comparison "
                       "is unit-free and does not drift with inflation")
    return status

def expected_from_forecasts(forecasts: list, periods: int) -> list:
    """Projected portfolio value for each check-in period, by ordinal.

    There is no key that joins these two directly, and assuming one is how the
    first version of this seam shipped: the route read `entry["period_end"]`
    and `entry["projected_closing"]`, neither of which exists. `by_period` was
    therefore always empty, every observation was unmeasured, and the light
    would have said "not enough history" forever -- a feature that silently
    never works, which is the same shape as the `/api/decide/start` defect
    found in an installed build earlier.

    What actually exists: a check-in stores its period as DATES, a forecast
    stores its curve by AGE (`[{"age", "value"}]`) plus the run's `start_age`.
    The join is therefore the ordinal -- reviews are annual, so period N ends
    at `start_age + N + 1`. Returns `None` for any period the curve does not
    reach, which reads as unmeasured rather than as a comparison against a
    guess.
    """
    curve, start_age = None, None
    for entry in (forecasts or []):
        series = entry.get("series")
        if entry.get("series_available") and isinstance(series, list) and series:
            curve, start_age = series, entry.get("start_age")
            break                       # newest first, as `forecasts` returns
    if not curve or start_age is None:
        return [None] * periods
    by_age = {float(row["age"]): float(row["value"])
              for row in curve if isinstance(row, dict)
              and "age" in row and "value" in row}
    return [by_age.get(float(start_age) + index + 1) for index in range(periods)]
