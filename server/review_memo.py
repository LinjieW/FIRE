"""Phase 2 · the annual review memo, and its three-way call.

ROADMAP asks the memo to answer four questions — what happened, is it
material, does it need action, when to look again — and to be "the minimal
forerunner of 3.1's full guardrail state machine": `no action / keep watching /
adjust one lever`.

Three rules shape everything below, and each exists because the obvious
version would be worse.

**Materiality is measured against the plan, not against a dollar threshold.**
A $30,000 miss is nothing to a $3M portfolio and most of a year's progress to
someone with $150k. So the tests are ratios of the opening value, and the memo
says which ratio it used.

**An incomplete attribution cannot produce a confident call.** When the
waterfall could not compute a term — a moved engine build, an unclassifiable
ledger — the verdict is `keep watching` with the gap named, never `no action`.
"We could not measure it" and "we measured it and it is fine" are the two
statements this memo most needs to keep apart.

**It recommends at most one lever.** That is the ROADMAP's wording and it is
also the honest limit of what a single period's variance supports: one year of
data can say "your spending ran ahead of plan", it cannot rank a spending cut
against a later retirement date. Ranking is Phase 3's job, with the ensemble
behind it.

Nothing here decides anything for the user. It is a reading of one check-in,
and it says what it is reading and what it could not.
"""
from __future__ import annotations

from typing import Optional

#: The three-way call. Ordered worst to best so `max` picks the strongest.
NO_ACTION = "no_action"
KEEP_WATCHING = "keep_watching"
ADJUST_ONE_LEVER = "adjust_one_lever"
_RANK = {NO_ACTION: 0, KEEP_WATCHING: 1, ADJUST_ONE_LEVER: 2}

#: Materiality thresholds, as a fraction of the period's opening value.
#: A deviation under `NOISE` is not worth a sentence; over `MATERIAL` it is
#: worth changing something. Between them the honest answer is to watch.
NOISE = 0.01
MATERIAL = 0.05

#: Which component each lever answers to. `market` has no lever on purpose:
#: the one thing a year of variance cannot tell you is that the market was
#: wrong, and "adjust your allocation because last year was bad" is the
#: single most expensive thing this memo could say.
_LEVERS = {
    "spending": "spending",
    "net_contribution": "savings_rate",
    "income": "income",
    "tax": "tax_placement",
    "fee": "costs",
    "life_event": "one_off_events",
}


def _fraction(value: Optional[float], opening: float) -> Optional[float]:
    if value is None or not opening:
        return None
    return value / abs(opening)


def build_memo(attribution: dict, *, next_review_months: int = 12) -> dict:
    """One check-in's reading. Pure: no I/O, no clock, no engine.

    `attribution` is a `/api/checkin/attribute` response. The memo never
    recomputes any of its numbers -- it reads them, ranks them, and says what
    it could not read.
    """
    opening = float(attribution.get("opening") or 0.0)
    components = {c["key"]: c for c in attribution.get("components") or []}
    unknown = sorted(k for k, c in components.items() if c.get("value") is None)
    incomplete = (attribution.get("state") != "complete") or bool(unknown)

    behaviour = []
    for key, lever in _LEVERS.items():
        value = (components.get(key) or {}).get("value")
        share = _fraction(value, opening)
        if share is None or abs(share) < NOISE:
            continue
        behaviour.append({"key": key, "lever": lever, "value": value,
                          "share_of_opening": share})
    behaviour.sort(key=lambda item: abs(item["share_of_opening"]), reverse=True)

    market = (components.get("market") or {}).get("value")
    market_share = _fraction(market, opening)

    # --- what happened -----------------------------------------------------
    # `effect` describes what the line did to the PORTFOLIO, not whether the
    # category was "above" or "below" plan. For spending those two readings are
    # opposites -- a more negative spending line means more money left, which
    # is overspending, and "spending below plan" reads as the reverse. Every
    # line's value is already signed as its effect on the portfolio, so saying
    # so directly removes the ambiguity instead of documenting it.
    happened = []
    if market_share is not None and abs(market_share) >= NOISE:
        happened.append({
            "kind": "market", "value": market,
            "share_of_opening": market_share,
            "effect": "left_you_lower" if market < 0 else "left_you_higher"})
    for item in behaviour:
        happened.append({
            "kind": item["key"], "value": item["value"],
            "share_of_opening": item["share_of_opening"],
            "effect": ("left_you_lower" if item["value"] < 0
                       else "left_you_higher")})

    # --- is it material ----------------------------------------------------
    worst_behaviour = behaviour[0] if behaviour else None
    material = bool(worst_behaviour
                    and abs(worst_behaviour["share_of_opening"]) >= MATERIAL)

    # --- does it need action ----------------------------------------------
    # Gaps are codes, not sentences. The memo is rendered in two languages and
    # the first version returned English prose, which then appeared verbatim
    # inside the Chinese UI -- a leak the CJK lint cannot catch, because it
    # looks for hardcoded Chinese rather than for English arriving from the
    # server. `detail` keeps the protocol's own wording for the log and for any
    # code the UI does not know.
    gaps = []
    if incomplete:
        for reason in attribution.get("reasons") or []:
            gaps.append({"code": "waterfall_incomplete", "detail": reason})
        for key in unknown:
            gaps.append({"code": "component_unknown", "component": key,
                         "detail": "the %s line could not be computed" % key})
    if attribution.get("within_tolerance") is False:
        gaps.append({"code": "residual_outside_tolerance",
                     "detail": "the residual is outside the reconciliation "
                               "tolerance, so part of the change is not "
                               "explained by the seven lines"})

    # A large market deviation is not a reason to act, and it IS a reason to
    # look again sooner: the plan's premise has moved even though nothing the
    # user did caused it. Reporting `no action` after a twenty-percent
    # drawdown would be technically defensible and useless — and the opposite
    # error, recommending an allocation change, is the most expensive sentence
    # this memo could produce, which is why `market` still has no lever.
    market_material = (market_share is not None
                       and abs(market_share) >= MATERIAL)

    # Any gap caps the verdict, and the cap is the point. Recommending "cut
    # your spending" while a comparable slice of the movement is unexplained is
    # exactly the overconfidence this memo exists to avoid -- the spending line
    # is not wrong, but acting on it before the unexplained part is resolved
    # means acting on a partial picture. Driving the real UI is what surfaced
    # this: the panel showed `adjust one lever` above a sentence saying the
    # reading could be no stronger than `keep watching`, because the verdict
    # ignored `gaps` while the disclosure did not.
    confident = not incomplete and not gaps

    if material and confident:
        verdict = ADJUST_ONE_LEVER
    elif gaps or worst_behaviour is not None or market_material:
        verdict = KEEP_WATCHING
    else:
        verdict = NO_ACTION

    # "We could not measure it" and "we measured it and it is fine" must not
    # collapse into one verdict.
    if not confident and verdict == NO_ACTION:
        verdict = KEEP_WATCHING

    lever = None
    if verdict == ADJUST_ONE_LEVER and worst_behaviour:
        lever = {"lever": worst_behaviour["lever"],
                 "because": worst_behaviour["key"],
                 "value": worst_behaviour["value"],
                 "share_of_opening": worst_behaviour["share_of_opening"]}

    # --- when to look again ------------------------------------------------
    # Sooner when something is unresolved, because the reason to come back is
    # the open question rather than the calendar.
    months = 3 if verdict == ADJUST_ONE_LEVER else (
        6 if verdict == KEEP_WATCHING else next_review_months)

    return {
        "verdict": verdict,
        "material": material,
        "happened": happened,
        "lever": lever,
        "gaps": gaps,
        "complete": not incomplete,
        #: True only when nothing is unmeasured AND the residual reconciles.
        #: `lever` is never populated without it -- see the invariant test.
        "confident": confident,
        # Stated rather than left to be inferred from `lever: null`: the memo
        # deliberately never recommends an allocation change off one period.
        "market_moved_but_has_no_lever": market_material,
        "next_review_months": months,
        "basis": {
            "opening": opening,
            "noise_threshold": NOISE,
            "material_threshold": MATERIAL,
            "measured_against": "share of the portfolio value at the start of "
                                "the period",
            "period": attribution.get("period"),
            "model_update_basis": (attribution.get("forecast") or {}).get(
                "model_update_basis"),
        },
    }


def strongest(*verdicts: str) -> str:
    """The most cautious of several verdicts, for a multi-period summary."""
    known = [v for v in verdicts if v in _RANK]
    if not known:
        return KEEP_WATCHING
    return max(known, key=lambda v: _RANK[v])
