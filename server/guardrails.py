"""Phase 4 · conditional policies, decided before the market moves.

The point is not to detect trouble -- a falling number detects itself. It is to
have decided, in advance and calmly, what would count as trouble and what you
would do about it, so the decision is not made in the week it feels worst.

Four things this module refuses to do, each because ROADMAP says so and each
because the obvious implementation gets it wrong.

**It never modifies a plan.** ROADMAP: "Guardrail 只提示复核，不自动修改计划."
Nothing here returns a config, and `test_guardrails.py` asserts that over the
module's whole public surface. A guardrail that quietly re-planned would be
making exactly the reactive decision it exists to prevent, only faster and
without telling anyone.

**One bad observation cannot reach `Action`.** ROADMAP: "单次波动或单一 seed
不得直接触发 Action." A single check-in below a band is a fluctuation; a
threshold crossed once is not evidence. `Action` requires a breach that is both
material and *persistent* -- `consecutive` observations of it. A single breach
can reach `Watch`, which is what `Watch` is for.

**Recovery is not the trigger read backwards.** A policy that fires at -10% and
clears at -10% flaps: the same portfolio wobbling around the line produces
alternating alerts, and a status light that changes every check-in trains its
reader to ignore it. Each policy carries a separate, weaker `recovery`
threshold, so it must actually come back before it goes quiet -- hysteresis, not
symmetry.

**A snoozed policy is still evaluated.** Snooze suppresses the alert, never the
measurement. `would_have_fired` stays true in the record, because the honest
thing to show a user in three months is "this was firing the whole time and you
had asked not to hear it", not silence that reads as calm.

The trigger conditions and actions are ROADMAP's list, and the `since` fields
below are what makes the phase useful rather than merely alarming: the same
evaluation run over simulated paths (see `guardrail_study.py`) answers how often
this policy would have told you to act in paths that ended fine.
"""
from __future__ import annotations

import copy
from typing import Optional

# --- the three states ROADMAP names -----------------------------------------
ON_TRACK = "on_track"
WATCH = "watch"
ACTION = "action"

#: Ordered, so a plan's status is the worst of its policies' statuses.
_RANK = {ON_TRACK: 0, WATCH: 1, ACTION: 2}

# --- trigger conditions, ROADMAP's list -------------------------------------
PORTFOLIO_BELOW_BAND = "portfolio_below_band"
SUCCESS_RATE_DECLINING = "success_rate_declining"
FIRE_AGE_RECEDING = "fire_age_receding"
PERMANENT_OVERSPEND = "permanent_overspend"
INCOME_INTERRUPTION = "income_interruption"

TRIGGERS = (PORTFOLIO_BELOW_BAND, SUCCESS_RATE_DECLINING, FIRE_AGE_RECEDING,
            PERMANENT_OVERSPEND, INCOME_INTERRUPTION)

#: Which observed field each trigger reads, and which direction is bad. Kept as
#: data rather than branches so a trigger cannot be added without saying both.
_READS = {
    PORTFOLIO_BELOW_BAND:   ("portfolio_real", "below"),
    SUCCESS_RATE_DECLINING: ("lifetime_success", "below"),
    FIRE_AGE_RECEDING:      ("fire_age", "above"),
    PERMANENT_OVERSPEND:    ("spending_real", "above"),
    INCOME_INTERRUPTION:    ("income_real", "below"),
}

# --- actions, ROADMAP's list ------------------------------------------------
DEFER_FIRE = "defer_fire"
CUT_PERMANENT_SPENDING = "cut_permanent_spending"
EXTEND_PART_TIME = "extend_part_time"
PAUSE_LARGE_EVENTS = "pause_large_events"
REASSESS_CONVERSION_OR_RELOCATION = "reassess_conversion_or_relocation"

ACTIONS = (DEFER_FIRE, CUT_PERMANENT_SPENDING, EXTEND_PART_TIME,
           PAUSE_LARGE_EVENTS, REASSESS_CONVERSION_OR_RELOCATION)

#: A breach smaller than this share of the reference is not worth a status
#: change. Mirrors `review_memo.NOISE` deliberately: the two must not disagree
#: about what counts as nothing, or the home page and the review page will.
DEFAULT_MATERIALITY = 0.01

#: How many consecutive breaching observations `Action` needs. Two is the
#: smallest number that is not "once".
DEFAULT_CONSECUTIVE = 2

DEFAULT_REVIEW_MONTHS = 12


class GuardrailError(ValueError):
    """A policy this module will not evaluate rather than evaluate wrongly."""


class Policy:
    """One conditional decision, made in advance.

    `threshold` is where it starts mattering; `recovery` is where it stops.
    They are deliberately different numbers -- see the module docstring on
    flapping.
    """

    __slots__ = ("policy_id", "trigger", "threshold", "recovery", "action",
                 "materiality", "consecutive", "review_months", "note")

    def __init__(self, policy_id: str, trigger: str, threshold: float,
                 action: str, *, recovery: Optional[float] = None,
                 materiality: float = DEFAULT_MATERIALITY,
                 consecutive: int = DEFAULT_CONSECUTIVE,
                 review_months: int = DEFAULT_REVIEW_MONTHS, note: str = ""):
        if trigger not in TRIGGERS:
            raise GuardrailError(
                "unknown trigger %r; ROADMAP's five are %s"
                % (trigger, ", ".join(TRIGGERS)))
        if action not in ACTIONS:
            raise GuardrailError(
                "unknown action %r; ROADMAP's five are %s"
                % (action, ", ".join(ACTIONS)))
        if consecutive < 2:
            raise GuardrailError(
                "consecutive must be at least 2: with 1, a single fluctuation "
                "reaches Action, which ROADMAP forbids outright")
        if materiality < 0:
            raise GuardrailError("materiality cannot be negative")
        self.policy_id = policy_id
        self.trigger = trigger
        self.threshold = float(threshold)
        self.action = action
        self.materiality = float(materiality)
        self.consecutive = int(consecutive)
        self.review_months = int(review_months)
        self.note = note
        self.recovery = (float(recovery) if recovery is not None
                         else self._default_recovery())
        self._check_hysteresis()

    def _default_recovery(self) -> float:
        """Halfway back toward safety, when the caller does not say.

        A default equal to the threshold would be the flapping case, so there
        is no "unset" that produces it.
        """
        span = abs(self.threshold) * 0.5 or 0.5
        return (self.threshold + span if self.direction == "below"
                else self.threshold - span)

    def _check_hysteresis(self) -> None:
        if self.direction == "below" and self.recovery <= self.threshold:
            raise GuardrailError(
                "policy %r: recovery (%.4g) must be ABOVE threshold (%.4g) for "
                "a below-trigger, or the same value both fires and clears it "
                "and the status flaps on every observation"
                % (self.policy_id, self.recovery, self.threshold))
        if self.direction == "above" and self.recovery >= self.threshold:
            raise GuardrailError(
                "policy %r: recovery (%.4g) must be BELOW threshold (%.4g) for "
                "an above-trigger, or the same value both fires and clears it "
                "and the status flaps on every observation"
                % (self.policy_id, self.recovery, self.threshold))

    @property
    def field(self) -> str:
        return _READS[self.trigger][0]

    @property
    def direction(self) -> str:
        return _READS[self.trigger][1]

    def breaches(self, value) -> bool:
        if value is None:
            return False
        return (value < self.threshold if self.direction == "below"
                else value > self.threshold)

    def recovered(self, value) -> bool:
        if value is None:
            return False
        return (value >= self.recovery if self.direction == "below"
                else value <= self.recovery)

    def shortfall(self, value) -> Optional[float]:
        """How far past the line, as a share of the line. `None` when unknown.

        A share rather than an amount, for `review_memo`'s reason: the same
        dollar gap is not the same fact at a different scale.
        """
        if value is None:
            return None
        base = abs(self.threshold) or 1.0
        return (self.threshold - value) / base if self.direction == "below" \
            else (value - self.threshold) / base

    def describe(self) -> dict:
        return {"policy_id": self.policy_id, "trigger": self.trigger,
                "threshold": self.threshold, "recovery": self.recovery,
                "action": self.action, "materiality": self.materiality,
                "consecutive": self.consecutive,
                "review_months": self.review_months, "note": self.note}


class Snooze:
    """A user's request not to be told, with an end date.

    Holds `until_index` rather than a date because the observation sequence is
    what this module can actually see; the seam converts.
    """

    __slots__ = ("policy_id", "until_index", "reason")

    def __init__(self, policy_id: str, until_index: int, reason: str = ""):
        self.policy_id = policy_id
        self.until_index = int(until_index)
        self.reason = reason

    def covers(self, index: int) -> bool:
        return index < self.until_index


def evaluate(policy: Policy, observations: list, *,
             snooze: Optional[Snooze] = None) -> dict:
    """Walk one policy along a sequence of observations.

    `observations` are oldest-first dicts holding whichever of
    `portfolio_real`, `lifetime_success`, `fire_age`, `spending_real`,
    `income_real` are known; a missing field is not a breach, because "we did
    not measure it" and "it was fine" are different and only one of them is
    reassuring.

    Returns the state at the END of the sequence plus the whole trail, so a
    caller can show why rather than only what.
    """
    if not isinstance(observations, list):
        raise GuardrailError("observations must be a list, oldest first")
    streak, state, trail = 0, ON_TRACK, []
    fired_at = None
    for index, observation in enumerate(observations):
        value = (observation or {}).get(policy.field)
        breaching = policy.breaches(value)
        share = policy.shortfall(value)
        material = breaching and share is not None \
            and abs(share) >= policy.materiality
        if value is None:
            # An unmeasured period HOLDS the streak: it neither advances nor
            # clears it. Clearing would treat "we did not look" as "it was
            # fine", which is the one reading the rest of this module refuses.
            pass
        elif material:
            streak += 1
        elif state != ON_TRACK and policy.recovered(value):
            # Hysteresis: an observation that merely stopped breaching is not
            # a recovery. It has to come back past the recovery line.
            streak = 0
        elif not breaching:
            streak = 0
        # A single material breach is Watch; Action needs persistence.
        if streak >= policy.consecutive:
            state = ACTION
            fired_at = index if fired_at is None else fired_at
        elif streak > 0:
            state = WATCH
        elif state != ON_TRACK and policy.recovered(value):
            state = ON_TRACK
        trail.append({"index": index, "value": value, "breaching": breaching,
                      "material": material, "streak": streak, "state": state,
                      "shortfall": share})
    suppressed = bool(snooze and snooze.policy_id == policy.policy_id
                      and observations and snooze.covers(len(observations) - 1))
    return {
        "policy_id": policy.policy_id,
        # What the measurement says, regardless of what the user asked to hear.
        "would_have_fired": state == ACTION,
        # What the user is shown. A snoozed Action is held at Watch rather than
        # hidden: silence would read as calm, and it is not calm.
        "state": WATCH if (suppressed and state == ACTION) else state,
        "snoozed": suppressed,
        "streak": streak,
        "first_action_index": fired_at,
        "action": policy.action if state == ACTION else None,
        "review_months": policy.review_months,
        "trail": trail,
    }


def plan_status(policies: list, observations: list, *,
                snoozes: Optional[list] = None) -> dict:
    """The home page's one word, and the reason for it.

    The plan's status is the WORST of its policies', because a light that
    averaged them would go green while something was red.
    """
    by_id = {s.policy_id: s for s in (snoozes or [])}
    results = [evaluate(p, observations, snooze=by_id.get(p.policy_id))
               for p in policies]
    state = ON_TRACK
    for result in results:
        if _RANK[result["state"]] > _RANK[state]:
            state = result["state"]
    firing = [r for r in results if r["state"] == ACTION]
    watching = [r for r in results if r["state"] == WATCH]
    return {
        "state": state,
        "policies": results,
        "acting": [r["policy_id"] for r in firing],
        "watching": [r["policy_id"] for r in watching],
        # Suppressed alerts are surfaced separately rather than folded in: the
        # user asked not to be alerted, not to be misinformed.
        "snoozed_but_firing": [r["policy_id"] for r in results
                               if r["snoozed"] and r["would_have_fired"]],
        "next_review_months": min([r["review_months"] for r in results],
                                  default=DEFAULT_REVIEW_MONTHS),
        "modifies_plan": False,
    }


def default_policies(baseline: dict) -> list:
    """A starting set, anchored to this plan's own baseline.

    Thresholds are relative to what the plan currently projects, not absolute
    numbers that would mean different things to different people. Returned for
    the user to edit -- they are a starting point, not advice.
    """
    out = []
    portfolio = baseline.get("portfolio_real")
    if portfolio:
        out.append(Policy(
            "portfolio_band", PORTFOLIO_BELOW_BAND, portfolio * 0.80,
            CUT_PERMANENT_SPENDING, recovery=portfolio * 0.90,
            note="20% below today's real portfolio, clearing at 10% below"))
    success = baseline.get("lifetime_success")
    # Below this there is no room to fall ten points, and clamping both lines
    # at zero would collapse the hysteresis into the flapping case. A plan that
    # is already failing does not need a guardrail to notice.
    if success is not None and success > 0.10:
        out.append(Policy(
            "success_floor", SUCCESS_RATE_DECLINING, success - 0.10,
            DEFER_FIRE, recovery=success - 0.05,
            note="10 points below today's success rate, clearing at 5"))
    fire_age = baseline.get("fire_age")
    if fire_age is not None:
        out.append(Policy(
            "fire_age_drift", FIRE_AGE_RECEDING, fire_age + 3.0,
            EXTEND_PART_TIME, recovery=fire_age + 1.0,
            note="three years later than planned, clearing at one"))
    spending = baseline.get("spending_real")
    if spending:
        out.append(Policy(
            "overspend", PERMANENT_OVERSPEND, spending * 1.10,
            PAUSE_LARGE_EVENTS, recovery=spending * 1.03,
            note="10% above planned real spending, clearing at 3%"))
    income = baseline.get("income_real")
    if income:
        out.append(Policy(
            "income_gap", INCOME_INTERRUPTION, income * 0.70,
            REASSESS_CONVERSION_OR_RELOCATION, recovery=income * 0.90,
            note="a 30% income drop, clearing at 10%"))
    return out


def describe_policies(policies: list) -> list:
    return [p.describe() for p in policies]


def freeze(observations: list) -> list:
    """A defensive copy. Nothing in this module may edit its caller's data --
    the same rule as not modifying the plan, one level down."""
    return copy.deepcopy(list(observations))
