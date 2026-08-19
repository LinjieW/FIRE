"""Phase 2 · the annuitization decision, and the two things it must not do.

ROADMAP asks for a three-way comparison — buy now, defer to 70, don't buy —
shown with a `license-to-spend` reading beside the tail failure rate. Both
halves have an obvious implementation that is wrong, and this module exists to
not do them.

**It never manufactures the quote it is missing.** "Defer to 70" needs a quote
for income starting at 70. That is a different number, and deriving it from a
mortality table plus a discount rate produces something that looks like a
quote, is not one, and *decides the comparison* — the deferred arm wins or
loses on exactly that figure. The user ruled on 2026-08-12 that only the arms
the user actually holds quotes for are compared, and the rest are named as not
compared, with the reason. A missing arm is reported, not filled in.

**A spending ceiling that could not be measured is `None`.** license-to-spend
is "at the same success constraint, how much more can you spend", which means
searching for the highest spending the plan still clears. The search assumes
success falls as spending rises. Under Guyton-Klinger that assumption can be
false: the guardrails absorb the increase by cutting spending later, so
`lifetime_success` sits nearly still while the plan quietly becomes a
different plan. A bisection run over a flat function returns whatever its
midpoint happened to be, and reports it with the same confidence as a real
answer. So the search checks its own premise first and returns `None` with a
reason when the premise fails. This project's name for the alternative is a
false zero.

Nothing here runs the engine. `spending_ceiling` takes an `evaluate` callable,
so the judging is testable without a Monte Carlo run behind every assertion
and the caller supplies the real thing.
"""
from __future__ import annotations

from typing import Callable, Optional

import decision_packet as DP

#: The lever license-to-spend moves. One path, because "how much can you
#: spend" is a question about one number in this engine, and a search that
#: moved several would be reporting a bundle rather than a ceiling.
SPENDING_PATH = "state.expenses_y0"

#: The arm that needs no quote. Not buying is always available, and it is the
#: baseline every other arm is read against.
DO_NOTHING = "don't buy"

#: The deferred arm ROADMAP names. Kept as a constant rather than spelled into
#: prose so a test can hold the packet to naming it even when it cannot be
#: built.
DEFER_AGE = 70


class QuoteMissing(str):
    """A reason, in the user's terms, why an arm is absent."""


def _annuity_changes(quote: dict) -> dict:
    """The config delta that turns the baseline into "you bought this one"."""
    return {
        "guaranteed_income.mode": "on",
        "guaranteed_income.annuities": [dict(quote)],
        "guaranteed_income.ladders": [],
    }


def build_alternatives(quotes, *, current_age: int,
                       defer_age: int = DEFER_AGE) -> dict:
    """The arms that can be compared, and the named arms that cannot.

    `quotes` are entries in the shape `guaranteed_income.annuities` takes —
    the user's own quotes, one dict each. An arm exists when a quote for it
    exists. Nothing is interpolated between them.
    """
    quotes = list(quotes or [])
    alternatives, not_compared = [], []
    seen_start_ages = set()

    for index, quote in enumerate(quotes):
        entry = dict(quote or {})
        start = entry.get("start_age")
        if not isinstance(start, (int, float)):
            not_compared.append({
                "name": "quote %d" % (index + 1),
                "reason": "this quote does not say what age income starts, "
                          "and an annuity's start age is most of its price",
            })
            continue
        start = int(start)
        if start in seen_start_ages:
            not_compared.append({
                "name": "income from %d" % start,
                "reason": "two quotes start income at %d; the packet compares "
                          "one arm per start age rather than picking between "
                          "quotes on the user's behalf" % start,
            })
            continue
        seen_start_ages.add(start)
        name = ("buy now (income from %d)" % start if start <= current_age
                else "defer to %d" % start)
        alternatives.append(DP.Alternative(
            name, _annuity_changes(entry),
            rationale="the user's own quote: %s premium, %s a year%s"
                      % (entry.get("premium"), entry.get("annual_payout_real"),
                         "" if entry.get("cola") else ", no COLA")))

    if defer_age not in seen_start_ages:
        not_compared.append({
            "name": "defer to %d" % defer_age,
            "reason": "no quote for income starting at %d was supplied. A "
                      "deferred payout derived from a mortality table and a "
                      "discount rate is not a quote — it is our number, and "
                      "it would decide this comparison" % defer_age,
        })
    if not alternatives:
        not_compared.append({
            "name": DO_NOTHING,
            "reason": "nothing to compare it against: no usable quote was "
                      "supplied, so this is not a decision yet",
        })
    return {
        "alternatives": alternatives,
        "not_compared": not_compared,
        # The arm that needs no quote is always present when there is anything
        # to read it against, and it is the baseline rather than an arm of its
        # own: the baseline config already is "don't buy".
        "baseline_is": DO_NOTHING if alternatives else None,
    }


def spending_ceiling(evaluate: Callable[[float], Optional[float]], *,
                     low: float, high: float, threshold: float,
                     tolerance: float, max_evaluations: int = 12) -> dict:
    """The highest spending that still clears `threshold`, or `None` and why.

    `evaluate(spending) -> success` is the caller's; this runs no engine.

    The premise is checked before the search rather than assumed by it. If
    the low end already fails, there is no ceiling to find inside the bracket.
    If the high end still passes, the ceiling is above the bracket and
    returning `high` would report the edge of the search as a property of the
    plan. And if the two ends differ by less than `tolerance`, the metric did
    not respond to a change spanning the whole bracket — the guardrail case —
    and any midpoint the bisection lands on is an artefact of where it
    happened to stop.
    """
    if not high > low:
        raise ValueError("the bracket is empty: low=%r high=%r" % (low, high))
    calls = [0]

    def measure(x):
        calls[0] += 1
        value = evaluate(x)
        return None if not isinstance(value, (int, float)) else float(value)

    at_low, at_high = measure(low), measure(high)
    if at_low is None or at_high is None:
        return _no_ceiling("the success metric was not measurable at the ends "
                           "of the search bracket", calls[0])
    # Flatness is checked BEFORE the two bracket-position checks, because a
    # metric that did not respond makes both of their diagnoses wrong: with a
    # flat 0.94 against a 0.90 threshold the position check says "the ceiling
    # is above the bracket, widen it", and widening a bracket the metric
    # ignores finds nothing. Which check fires first IS the diagnosis the user
    # acts on.
    if abs(at_low - at_high) < tolerance:
        return _no_ceiling(
            "the metric moved %s across the whole bracket, less than the %s "
            "tolerance. Guardrails absorb a spending increase by cutting "
            "spending later, so success can sit still while the plan becomes "
            "a different plan; a bisection over a flat function returns its "
            "own midpoint" % (_num(abs(at_low - at_high)), _num(tolerance)),
            calls[0])
    if at_low < threshold:
        return _no_ceiling(
            "spending %s already misses the %s constraint, so there is no "
            "increase to license — the question below this bracket is a "
            "different one" % (_num(low), _num(threshold)), calls[0])
    if at_high >= threshold:
        return _no_ceiling(
            "spending %s still clears the constraint, so the ceiling is above "
            "the bracket. Reporting %s would report where the search stopped, "
            "not what the plan can carry" % (_num(high), _num(high)),
            calls[0])
    lo, hi = low, high
    while calls[0] < max_evaluations and (hi - lo) > max(tolerance * lo, 1.0):
        mid = (lo + hi) / 2.0
        value = measure(mid)
        if value is None:
            return _no_ceiling("the success metric stopped being measurable "
                               "at spending %s" % _num(mid), calls[0])
        if value >= threshold:
            lo = mid
        else:
            hi = mid
    return {
        "ceiling": lo,
        "reason": None,
        "evaluations": calls[0],
        # The bracket that remains, so a caller can see how tightly this was
        # pinned rather than reading `ceiling` as exact.
        "bracket": [lo, hi],
    }


def _no_ceiling(reason: str, evaluations: int) -> dict:
    return {"ceiling": None, "reason": reason, "evaluations": evaluations,
            "bracket": None}


def _num(value) -> str:
    return format(value, ",.0f") if abs(value) >= 100 else "%.4g" % value


def license_to_spend(baseline: dict, alternative: dict, *,
                     name: str = "") -> dict:
    """What the arm buys, expressed as spending rather than as a probability.

    Returns `None` for the reading — with the reason that produced it — when
    either ceiling is missing. A packet that printed `0` here would be saying
    the annuity buys nothing, which is a finding; "we could not measure it" is
    not.
    """
    for label, side in (("baseline", baseline), ("this arm", alternative)):
        if side.get("ceiling") is None:
            return {
                "name": name, "delta": None, "pct": None, "resolution": None,
                "reason": "no reading: the %s spending ceiling was not "
                          "measured (%s)" % (label, side.get("reason") or
                                             "no reason recorded"),
            }
    base = float(baseline["ceiling"])
    arm = float(alternative["ceiling"])
    delta = arm - base
    # A difference finer than the search grid is not a difference. Both
    # searches stop on a bracket, and two ceilings inside one grid step land on
    # the same number — which arrives here as a delta of exactly 0.0 and reads
    # as "the annuity buys you nothing". Driving the real page produced that:
    # 5 evaluations over a 58,800 bracket resolve to about 7,350, both arms
    # reported 72,500, and the reading said 0 with no reason attached. That is
    # the false zero this module exists to refuse, arriving through the one
    # function that was not checking for it.
    resolution = max(_width(baseline.get("bracket")),
                     _width(alternative.get("bracket")))
    if abs(delta) < resolution:
        return {
            "name": name, "delta": None, "pct": None,
            "resolution": resolution,
            "reason": "no reading: the two spending ceilings differ by less "
                      "than the %s the search resolved, so any figure here "
                      "would be the grid rather than the annuity. Raise the "
                      "evaluation budget to tell them apart"
                      % _num(resolution),
        }
    return {
        "name": name,
        "delta": delta,
        "pct": None if base == 0 else delta / base,
        "resolution": resolution,
        "reason": None,
    }


def _width(bracket) -> float:
    """How finely a search pinned its answer. `0.0` when it did not say."""
    if not bracket or len(bracket) != 2:
        return 0.0
    return abs(float(bracket[1]) - float(bracket[0]))


def consumption_reading(baseline_p50, arm_p50, *, name: str = "") -> dict:
    """What the arm did to median consumption, at unchanged spending.

    Beside the licence reading, not instead of it, and it exists because of a
    measured property of this engine: Guyton-Klinger absorbs a shock by cutting
    spending later, so `lifetime_success` can sit still while the plan becomes
    a different plan. On the default plan a 200,000 premium for 13,000 a year
    moved success by 0.0000 at two separate spending levels and moved median
    consumption by roughly 1,000 a year. A packet reporting only the metric the
    guardrails flatten would report nothing, forever, and the nothing would
    read as "the annuity makes no difference".

    The project has now paid for this twice: the zero-bequest flag was written
    against `lifetime_success` first and could never fire.
    """
    for label, value in (("baseline", baseline_p50), ("this arm", arm_p50)):
        if not isinstance(value, (int, float)):
            return {"name": name, "delta": None, "reason":
                    "no reading: %s median consumption was not measured"
                    % label}
    return {"name": name, "delta": float(arm_p50) - float(baseline_p50),
            "baseline": float(baseline_p50), "arm": float(arm_p50),
            "reason": None}


def read_packet(packet: dict, readings: list, not_compared: list,
                consumption: list = None) -> dict:
    """The tail verdict and the spending reading, side by side, plus absences.

    ROADMAP asks for license-to-spend "beside" the failure rate rather than
    instead of it, because they answer different questions and an annuity that
    improves one can worsen the other. Nothing here reduces the pair to a
    score.
    """
    return {
        **packet,
        "license_to_spend": list(readings),
        "consumption": list(consumption or []),
        "arms_not_compared": list(not_compared),
        # Stated rather than left for the reader to notice, because an absent
        # arm looks exactly like an arm that lost.
        "comparison_is_partial": bool(not_compared),
    }
