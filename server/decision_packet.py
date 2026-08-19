"""Phase 3 · the decision packet, and what `Robust` is allowed to mean.

ROADMAP calls Phase 3 the killer feature and the word carrying it is `Robust`:
"只有跨 seeds、return models 和不利 assumption packs 方向稳定，才能标记为
`Robust`". Everything here exists to make that label expensive.

Three rules, each because the obvious version is worse.

**The constraints stay apart.** ROADMAP forbids "用单一 utility score 隐藏偏好"
and names the four that must remain separate: minimum consumption, FIRE
deadline, success threshold, legacy. So a `Goal` holds them as named
constraints with their own thresholds and their own pass/fail, and nothing here
combines them into a number. A single score is not a summary of a trade-off; it
is a set of weights the user never chose, wearing the costume of a measurement.

**An axis the perturbation cannot reach is not evidence.** The engine's own
comment says the mu-shift "flows into markov but cannot apply to blocks
(historical table)". Sweeping a return-posture pack across all three models and
finding the direction unchanged under `blocks` would look like stability and
would mean the model never saw the change. Every axis point that produced no
movement at all is reported as `unreached`, and `unreached` never counts toward
`Robust`.

**A reversal names itself.** ROADMAP: "不稳定结论必须显示其反转来源". A packet
that is not robust carries the specific seed, model or pack under which the
direction flipped, because "not robust" without that is a mood rather than a
finding.

Nothing here runs the engine. It takes evaluations and judges them, so the
judging can be tested without a Monte Carlo run behind every assertion.
"""
from __future__ import annotations

import copy
from typing import Optional

#: The four constraints ROADMAP requires be kept separate. Each is a direction
#: and a threshold; none is weighted against another.
CONSTRAINT_KINDS = {
    "min_consumption": "at_least",
    "fire_deadline": "at_most",
    "success_threshold": "at_least",
    "legacy": "at_least",
}

#: The three return models the engine accepts. `engine_adapter` raises on anything
#: else, so this list is the engine's, not a guess.
RETURN_MODELS = ("iid", "markov", "blocks")

ROBUST = "robust"
DIRECTIONAL = "directional"
UNSTABLE = "unstable"
INCONCLUSIVE = "inconclusive"


class PacketError(RuntimeError):
    """A packet this module will not assemble rather than assemble wrongly."""


# ---------------------------------------------------------------------------
# What is being decided.
# ---------------------------------------------------------------------------

class Constraint:
    """One named requirement, with its own threshold and its own verdict."""

    __slots__ = ("name", "metric", "kind", "threshold", "note")

    def __init__(self, name: str, metric: str, threshold: float,
                 note: str = ""):
        if name not in CONSTRAINT_KINDS:
            raise PacketError(
                "unknown constraint %r; the four ROADMAP names are %s"
                % (name, ", ".join(sorted(CONSTRAINT_KINDS))))
        self.name = name
        self.metric = metric
        self.kind = CONSTRAINT_KINDS[name]
        self.threshold = float(threshold)
        self.note = note

    def holds(self, metrics: dict):
        """`True`, `False`, or `None` when the metric is missing.

        `None` rather than `False`: "we could not measure it" and "it failed"
        are different answers, and a packet that conflates them reports a
        constraint breach that may not exist.
        """
        value = metrics.get(self.metric)
        if not isinstance(value, (int, float)):
            return None
        return (value >= self.threshold if self.kind == "at_least"
                else value <= self.threshold)

    def describe(self) -> dict:
        return {"name": self.name, "metric": self.metric, "kind": self.kind,
                "threshold": self.threshold, "note": self.note}


class Goal:
    """The question, its hard constraints, and the levers allowed to move.

    ROADMAP acceptance: "每个建议绑定明确目标、硬约束和允许改变的 lever". The
    levers are declared up front so a recommendation cannot quietly propose
    moving something the user ruled out.
    """

    __slots__ = ("question", "objective_metric", "direction", "constraints",
                 "levers")

    def __init__(self, question: str, objective_metric: str, direction: str,
                 constraints: list, levers: list):
        if direction not in ("higher_is_better", "lower_is_better"):
            raise PacketError("direction must say which way is better")
        if not question:
            raise PacketError("a packet needs the question it answers")
        if not levers:
            raise PacketError(
                "a packet needs the levers it is allowed to move; without them "
                "a recommendation cannot be checked against what the user "
                "ruled out")
        for constraint in constraints:
            if not isinstance(constraint, Constraint):
                raise PacketError("constraints must be Constraint instances")
        self.question = question
        self.objective_metric = objective_metric
        self.direction = direction
        self.constraints = list(constraints)
        self.levers = list(levers)

    def better(self, value, baseline) -> Optional[bool]:
        if not isinstance(value, (int, float)) \
                or not isinstance(baseline, (int, float)):
            return None
        if value == baseline:
            return None
        return (value > baseline if self.direction == "higher_is_better"
                else value < baseline)

    def describe(self) -> dict:
        return {"question": self.question,
                "objective_metric": self.objective_metric,
                "direction": self.direction,
                "levers": list(self.levers),
                "constraints": [c.describe() for c in self.constraints]}


class Alternative:
    """A named choice, as a declarative config delta.

    Declarative so that ROADMAP's "每个可行解可恢复成对应 config" is a property
    rather than a promise: `apply` reconstructs the exact config from the
    baseline plus the delta, and the delta is what gets archived.
    """

    __slots__ = ("name", "changes", "rationale")

    def __init__(self, name: str, changes: dict, rationale: str = ""):
        if not name:
            raise PacketError("an alternative needs a name")
        if not isinstance(changes, dict) or not changes:
            raise PacketError("alternative %r changes nothing" % name)
        self.name = name
        self.changes = dict(changes)
        self.rationale = rationale

    def uses_only(self, levers: list) -> list:
        """Changed paths that no declared lever covers."""
        allowed = tuple(levers)
        return sorted(path for path in self.changes
                      if not any(path == lever or path.startswith(lever + ".")
                                 for lever in allowed))

    def apply(self, base_config: dict) -> dict:
        out = copy.deepcopy(base_config)
        for path, value in sorted(self.changes.items()):
            parts = path.split(".")
            node = out
            for part in parts[:-1]:
                if not isinstance(node, dict) or part not in node:
                    raise PacketError(
                        "alternative %r: %s is not in the config"
                        % (self.name, path))
                node = node[part]
            if not isinstance(node, dict) or parts[-1] not in node:
                raise PacketError(
                    "alternative %r: %s is not in the config"
                    % (self.name, path))
            node[parts[-1]] = value
        return out

    def describe(self) -> dict:
        return {"name": self.name, "rationale": self.rationale,
                "changes": dict(sorted(self.changes.items()))}


# ---------------------------------------------------------------------------
# Judging.
# ---------------------------------------------------------------------------

def _axis_verdict(points: list, goal: Goal, baseline_by_key: dict,
                  anchor_better):
    """One axis: does the direction hold at every point that saw the change?

    "Agrees" means the point points the SAME WAY as the anchor, not that the
    alternative won there. Running this for real is what showed the difference:
    a fixture whose baseline success was already 1.0 made every point worse, so
    every point counted as a disagreement and the verdict came back `unstable`
    -- when "worse everywhere" is one of the most stable findings there is, and
    calling it unstable hides a clear answer behind a word that means "we
    cannot tell".

    A point where the alternative and the baseline produced identical metrics
    is `unreached` — the perturbation did not move that configuration at all,
    which is a fact about reach, not about stability. The engine's own comment
    that the mu-shift "cannot apply to blocks" is exactly this case, and
    counting it as agreement would manufacture robustness.
    """
    agree, disagree, unreached, measured = [], [], [], []
    for point in points:
        key = point["key"]
        baseline = baseline_by_key.get(key)
        if baseline is None:
            continue
        value = point["metrics"].get(goal.objective_metric)
        base_value = baseline.get(goal.objective_metric)
        # Recorded for every point, reached or not. `effect` is the
        # alternative minus the baseline AT THIS POINT, never against the
        # anchor: an adverse pack read in the relocation scenario would
        # otherwise have the scenario gap folded into its effect.
        measured.append({
            "key": key, "value": value, "baseline": base_value,
            "effect": (None if value is None or base_value is None
                       else value - base_value),
            "reached": value != base_value,
        })
        if value == base_value:
            unreached.append(key)
            continue
        better = goal.better(value, base_value)
        if better is None or anchor_better is None:
            unreached.append(key)
        elif better == anchor_better:
            agree.append(key)
        else:
            disagree.append(key)
    tested = len(agree) + len(disagree)
    return {"agree": agree, "disagree": disagree, "unreached": unreached,
            "points": measured,
            # Carried with the verdict rather than buried beside it. "Stable
            # across return models" when only one of three saw the change is
            # true and misleading in the same breath; the counts are what stop
            # it being read as all three.
            "coverage": {"tested": tested, "unreached": len(unreached),
                         "total": tested + len(unreached)}}


#: Which way each metric has to move to be an improvement. Needed because
#: gain-and-cost reports every metric, not just the objective, and `fire_age`
#: is the one that runs the other way -- retiring earlier is a lower number,
#: and scoring it like the rest would print every successful plan's headline
#: benefit as a cost.
METRIC_DIRECTION = {
    "lifetime_success": "higher_is_better",
    "terminal_real_p50": "higher_is_better",
    "fire_age_p50": "lower_is_better",
    "mean_real_consumption": "higher_is_better",
}


def gain_and_cost(anchor_metrics: dict, anchor_baseline: dict) -> dict:
    """What the change buys and what it costs, metric by metric.

    Deliberately not netted into a number. ROADMAP forbids a utility score,
    and a net figure is one wearing a different name: it would need weights
    between "retire a year earlier" and "leave 200k less", and that trade is
    the user's to make, not the packet's to assume. So both lists are returned
    and neither is ranked against the other.
    """
    gains, costs, unchanged, unknown = [], [], [], []
    for metric, direction in sorted(METRIC_DIRECTION.items()):
        value, base = anchor_metrics.get(metric), anchor_baseline.get(metric)
        if value is None or base is None:
            unknown.append({"metric": metric,
                            "why": "not reported by this run"})
            continue
        delta = value - base
        entry = {"metric": metric, "baseline": base, "alternative": value,
                 "delta": delta}
        if delta == 0:
            unchanged.append(entry)
        elif (delta > 0) == (direction == "higher_is_better"):
            gains.append(entry)
        else:
            costs.append(entry)
    return {"gains": gains, "costs": costs, "unchanged": unchanged,
            "unknown": unknown,
            "note": "Not combined into a score: weighting these against each "
                    "other is a preference, not a measurement."}


def _downside(per_axis: dict, goal: Goal) -> Optional[dict]:
    """The worst place the alternative actually landed, and where.

    The level, not the effect. "If this adverse assumption is the true one,
    here is where you end up" is the question a downside answers, and an
    improvement of +3 points is no comfort when the level it improves to still
    fails.
    """
    worst = None
    for axis, verdict in sorted(per_axis.items()):
        for point in verdict.get("points") or []:
            if point["value"] is None:
                continue
            if worst is None or goal.better(worst["value"], point["value"]):
                worst = {"axis": axis, "at": point["key"],
                         "value": point["value"],
                         "baseline_here": point["baseline"]}
    return worst


#: The seeds axis is excluded from sensitivity on purpose. Its spread is this
#: engine's own randomness -- `ensemble.py` documents why common random numbers
#: are impossible here -- and listing it beside the return models and the
#: adverse packs would present noise as an assumption the user could hold an
#: opinion about.
_NOT_AN_ASSUMPTION = ("seeds",)


def _sensitive_assumptions(per_axis: dict, anchor_effect) -> list:
    """Which assumptions move the alternative's benefit, largest first.

    Measured as the change in EFFECT, not in level: `effect_here` minus
    `effect_at_anchor`, both of which are alternative-minus-baseline at the
    same point. Levels would be dominated by whatever else that point changed
    -- an adverse pack read in the relocation scenario would rank first every
    time on the scenario gap alone, having told us nothing about the
    assumption.
    """
    if anchor_effect is None:
        return []
    ranked = []
    for axis, verdict in per_axis.items():
        if axis in _NOT_AN_ASSUMPTION:
            continue
        for point in verdict.get("points") or []:
            if point["effect"] is None or not point["reached"]:
                continue
            ranked.append({"axis": axis, "at": point["key"],
                           "effect_here": point["effect"],
                           "effect_at_anchor": anchor_effect,
                           "shift": point["effect"] - anchor_effect})
    ranked.sort(key=lambda entry: (-abs(entry["shift"]), entry["axis"],
                                   entry["at"]))
    return ranked


def _reversal_conditions(per_axis: dict, reversals: list,
                         anchor_effect) -> dict:
    """What would have to be true for the conclusion to flip.

    When nothing reverses, the nearest miss is reported rather than silence.
    "No reversals" and "no reversals, and the closest point was a hair away"
    are different situations, and only the second one tells the user the
    finding is fragile.
    """
    if reversals:
        conditions = []
        for reversal in reversals:
            axis, at = reversal["axis"], reversal["at"]
            if axis in _NOT_AN_ASSUMPTION:
                # A seed is not a state of the world anyone can hold an
                # opinion about. Phrasing it as one ("if seeds turns out to be
                # seed=4243") invites the user to discount it as an unlikely
                # scenario, when it is the opposite: a direction that flips
                # between seeds is inside this engine's own sampling noise,
                # which is the strongest reason of all not to act on it.
                conditions.append({
                    "axis": axis, "at": at,
                    "condition": "the direction already flips at %s, so the "
                                 "finding sits inside this engine's own "
                                 "sampling noise -- there is no assumption to "
                                 "disbelieve here, the measurement is simply "
                                 "not sharp enough to call" % at})
            else:
                conditions.append({
                    "axis": axis, "at": at,
                    "condition": "if %s turns out to be %s, the direction "
                                 "flips" % (axis, at)})
        return {"reverses": True, "conditions": conditions,
                "noise_reversal": any(r["axis"] in _NOT_AN_ASSUMPTION
                                      for r in reversals),
                "nearest_margin": None}
    ranked = _sensitive_assumptions(per_axis, anchor_effect)
    if not ranked or anchor_effect in (None, 0):
        return {"reverses": False, "conditions": [], "noise_reversal": False,
                "nearest_margin": None}
    # Closest to zero effect among the points that saw the change: the one that
    # came nearest to wiping the benefit out.
    nearest = min((e for e in ranked), key=lambda e: abs(e["effect_here"]))
    return {
        "reverses": False,
        "conditions": [],
        "noise_reversal": False,
        "nearest_margin": {
            "axis": nearest["axis"], "at": nearest["at"],
            "effect_here": nearest["effect_here"],
            "fraction_of_anchor_effect": (nearest["effect_here"] / anchor_effect
                                          if anchor_effect else None),
            "note": "closest any tested assumption came to erasing the "
                    "benefit; it did not reverse",
        },
    }


def evaluate_alternative(alternative: Alternative, goal: Goal, *,
                         anchor_metrics: dict, anchor_baseline: dict,
                         axes: dict, baselines: dict) -> dict:
    """Judge one alternative. Pure: takes measurements, returns a verdict.

    `axes` maps an axis name (`seeds`, `return_models`, `adverse_packs`) to a
    list of `{key, metrics}`; `baselines` maps the same keys to the baseline's
    metrics at that point.
    """
    illegal = alternative.uses_only(goal.levers)
    constraints = []
    for constraint in goal.constraints:
        constraints.append({
            **constraint.describe(),
            "holds": constraint.holds(anchor_metrics),
            "value": anchor_metrics.get(constraint.metric),
            "baseline_holds": constraint.holds(anchor_baseline),
        })

    anchor_value = anchor_metrics.get(goal.objective_metric)
    anchor_base = anchor_baseline.get(goal.objective_metric)
    anchor_better = goal.better(anchor_value, anchor_base)
    anchor_effect = (None if anchor_value is None or anchor_base is None
                     else anchor_value - anchor_base)
    per_axis = {name: _axis_verdict(points, goal, baselines.get(name) or {},
                                    anchor_better)
                for name, points in axes.items()}
    reversals = []
    for name, verdict in per_axis.items():
        for key in verdict["disagree"]:
            reversals.append({"axis": name, "at": key,
                              "why": "the direction flips here relative to "
                                     "the anchor"})

    covered = {name: bool(v["agree"]) for name, v in per_axis.items()}
    unreached_axes = [name for name, v in per_axis.items()
                      if not v["agree"] and not v["disagree"]]

    if illegal:
        verdict = INCONCLUSIVE
    elif anchor_better is None:
        verdict = INCONCLUSIVE
    elif reversals:
        verdict = UNSTABLE
    elif not all(covered.values()):
        # Every required axis must have at least one point that actually saw
        # the change. An axis of only-unreached points is not agreement.
        verdict = DIRECTIONAL
    else:
        verdict = ROBUST if anchor_better else DIRECTIONAL

    return {
        "alternative": alternative.describe(),
        "verdict": verdict,
        "better_than_baseline_at_anchor": anchor_better,
        "constraints": constraints,
        "constraints_all_hold": (None if any(c["holds"] is None
                                             for c in constraints)
                                 else all(c["holds"] for c in constraints)),
        "axes": per_axis,
        "reversals": reversals,
        "unreached_axes": unreached_axes,
        "levers_violated": illegal,
        "why": _why(verdict, reversals, unreached_axes, illegal),
        "qualification": (_robust_qualification(per_axis)
                          if verdict == ROBUST else ""),
        # ROADMAP's packet contents. Each is reported separately and none is
        # combined with another.
        "gain_and_cost": gain_and_cost(anchor_metrics, anchor_baseline),
        "downside": _downside(per_axis, goal),
        "sensitive_assumptions": _sensitive_assumptions(per_axis,
                                                        anchor_effect),
        "reversal_conditions": _reversal_conditions(per_axis, reversals,
                                                    anchor_effect),
    }


def _why(verdict, reversals, unreached_axes, illegal) -> str:
    if illegal:
        return ("this alternative moves %s, which the goal did not list as a "
                "lever; a recommendation that changes something the user ruled "
                "out is not an answer to their question"
                % ", ".join(illegal))
    if verdict == UNSTABLE:
        return ("the direction reverses at %s, so the conclusion depends on "
                "which of those you believe"
                % "; ".join("%s=%s" % (r["axis"], r["at"]) for r in reversals))
    if verdict == DIRECTIONAL and unreached_axes:
        return ("no point on %s moved at all, so that axis is untested rather "
                "than stable -- the perturbation may not reach that "
                "configuration" % ", ".join(sorted(unreached_axes)))
    if verdict == DIRECTIONAL:
        return ("the direction holds everywhere tested but points away from "
                "the goal -- a stable answer, and a negative one")
    if verdict == ROBUST:
        return ("the direction holds at every seed, return model and adverse "
                "pack that saw the change")
    return "the objective could not be compared"


def _robust_qualification(per_axis) -> str:
    """What a robust verdict must say about what it did not test.

    A claim of stability across an axis where most points never saw the change
    is true about the points it tested and false about the axis. The sentence
    below is what keeps those apart.
    """
    partial = []
    for name, verdict in sorted(per_axis.items()):
        coverage = verdict.get("coverage") or {}
        if coverage.get("unreached"):
            partial.append("%s (%d of %d points saw the change; %s did not)"
                           % (name, coverage["tested"], coverage["total"],
                              ", ".join(verdict["unreached"])))
    if not partial:
        return ""
    return ("This holds where the change had any effect. Not every point was "
            "reached: " + "; ".join(partial) + ". An unreached point is "
            "untested, not agreement.")
    return "the objective could not be compared"


#: What a packet's decision can be. `deferred` is deliberately distinct from
#: `open`: a question nobody has looked at and a question looked at and put
#: down are different states, and only the second one has a reason attached.
OPEN, CHOSEN, DECLINED, DEFERRED, SUPERSEDED = (
    "open", "chosen", "declined", "deferred", "superseded")

_TRANSITIONS = {
    OPEN: (CHOSEN, DECLINED, DEFERRED),
    DEFERRED: (CHOSEN, DECLINED, OPEN, SUPERSEDED),
    CHOSEN: (SUPERSEDED,),
    DECLINED: (SUPERSEDED, OPEN),
    SUPERSEDED: (),
}


def set_choice_state(packet: dict, state: str, *, reason: str,
                     at: str) -> dict:
    """Move a packet's decision, keeping what it was before.

    Transitions are checked rather than assigned. A packet that could go
    straight from `chosen` back to `open` would lose the fact that it was ever
    acted on, and a decision record that can forget a decision is not one.
    `at` is passed in rather than read from the clock so the caller owns the
    timestamp and the packet stays reproducible.
    """
    current = (packet.get("choice_state") or {}).get("state", OPEN)
    allowed = _TRANSITIONS.get(current)
    if allowed is None:
        raise PacketError("packet is in unknown state %r" % current)
    if state not in allowed:
        raise PacketError(
            "a %s packet cannot become %s; from here it can only become %s"
            % (current, state, ", ".join(allowed) or "nothing -- it is final"))
    if not reason:
        raise PacketError(
            "moving a packet to %s needs a reason; the state without the "
            "reason is a fact nobody can act on later" % state)
    history = list((packet.get("choice_state") or {}).get("history") or [])
    history.append({"from": current, "to": state, "reason": reason, "at": at})
    packet["choice_state"] = {"state": state, "reason": reason,
                              "history": history}
    return packet


def build_packet(question_id: str, goal: Goal, *, base_config: dict,
                 baseline_metrics: dict, alternatives: list,
                 protocol: dict, review_months: int = 12) -> dict:
    """Assemble the packet. Refuses rather than emitting a weak one.

    ROADMAP requires a formal packet run to use true tax, unified income
    semantics, and Standard-or-better precision, with the config and precision
    in the metadata. Those are checked here because a packet that does not say
    what it was computed under cannot be re-checked offline, which is one of
    the acceptance criteria.
    """
    precision = str(protocol.get("precision") or "")
    if precision not in ("standard", "official"):
        raise PacketError(
            "a formal packet needs Standard or Official precision; %r cannot "
            "carry a Robust claim" % (precision or "unset"))
    if not protocol.get("true_tax"):
        raise PacketError(
            "a formal packet needs true tax enabled; the progressive-tax "
            "approximation moves exactly the numbers a decision turns on")
    for key in ("paths", "seed", "engine_version"):
        if key not in protocol:
            raise PacketError(
                "packet protocol is missing %s, so its numbers cannot be "
                "reproduced" % key)

    return {
        "format": "fire-decision-packet-v1",
        "question_id": question_id,
        "goal": goal.describe(),
        "protocol": dict(protocol),
        "baseline": {"metrics": dict(baseline_metrics),
                     "config": copy.deepcopy(base_config)},
        "alternatives": list(alternatives),
        # ROADMAP's "one line: if you choose wrong, how bad is the worst
        # regret". Computed from what the alternatives already report, so it
        # measures the packet rather than re-deriving anything, and reported
        # per metric because a combined figure would be the utility score this
        # packet refuses everywhere else.
        "regret": regret_summary(list(alternatives)),
        "choice_state": {"state": OPEN, "reason": "", "history": []},
        "review_months": review_months,
        "disclosure": (
            "No single score combines these constraints. Each is reported "
            "separately because weighting them is a preference the packet "
            "cannot read off the numbers. `Robust` means the direction held at "
            "every seed, return model and adverse assumption pack that "
            "actually saw the change; an axis nothing moved is reported as "
            "unreached rather than counted as agreement."),
    }


def regret_summary(alternatives: list) -> dict:
    """"If I pick the wrong one, how much do I lose?" -- metric by metric.

    ROADMAP 4.0 Phase 4: one line in the packet, "reported per goal, NOT
    combined into a scalar, with the disclosure that it is relative to the
    option set listed here". All three of those are load-bearing.

    **Never summed.** A single regret number needs weights between "retire a
    year later" and "leave $200k less", and that trade belongs to the person
    deciding. The packet already refuses a utility score for the same reason;
    a regret scalar would be one wearing a different name.

    **Relative to THIS option set, and it says so.** Regret is measured
    against the best option on the table. An option nobody put on the table
    cannot appear, so a small regret means "these options differ little", not
    "there is little to gain here". Those read identically if the sentence is
    missing, which is why it is in the payload rather than only the docstring.

    **A metric missing for any option is reported as unknown.** Dropping it
    would quietly narrow the comparison; filling it in would invent one. This
    is the same false-zero the whole packet is built to avoid -- and the
    reason ROADMAP held this item back until packets persisted, because a
    regret line delivered too early "would be permanently empty, and empty
    looks exactly like no-regret".

    Takes the ALREADY EVALUATED alternatives, so it measures what the packet
    reports rather than re-deriving anything.
    """
    options = {}
    for entry in alternatives:
        name = (entry.get("alternative") or {}).get("name")
        gc = entry.get("gain_and_cost") or {}
        values, baseline = {}, {}
        for bucket in ("gains", "costs", "unchanged"):
            for row in gc.get(bucket) or ():
                metric = row.get("metric")
                if metric is None:
                    continue
                # `.get`, not `[]`. A row carrying only a delta is a shape
                # `build_packet` accepts -- several suites construct exactly
                # that -- and crashing on it would make this summary decide
                # which packets are allowed to exist. A missing level means
                # the metric is not comparable for this option, which the
                # unknown branch below already states rather than guesses.
                values[metric] = row.get("alternative")
                baseline[metric] = row.get("baseline")
        if name:
            options[name] = values
        if (any(v is not None for v in baseline.values())
                and "baseline" not in options):
            # The do-nothing option is always on the table, and leaving it out
            # would let every alternative look regret-free next to the others.
            options["baseline"] = baseline

    per_metric, unknown = [], []
    for metric, direction in sorted(METRIC_DIRECTION.items()):
        missing = [name for name, values in options.items()
                   if values.get(metric) is None]
        if missing or not options:
            unknown.append({
                "metric": metric,
                "why": ("not reported for %s, so a comparison across the "
                        "options would be missing one of them"
                        % ", ".join(sorted(missing)) if missing
                        else "no options to compare"),
            })
            continue
        scores = {name: float(values[metric]) for name, values in options.items()}
        best = (max(scores.values()) if direction == "higher_is_better"
                else min(scores.values()))
        regrets = {name: abs(best - value) for name, value in scores.items()}
        worst_name = max(regrets, key=lambda k: regrets[k])
        per_metric.append({
            "metric": metric,
            "direction": direction,
            "best_option": min(name for name, value in scores.items()
                               if value == best),
            "worst_case_regret": regrets[worst_name],
            "worst_case_option": worst_name,
            "by_option": [{"option": name, "value": scores[name],
                           "regret": regrets[name]}
                          for name in sorted(scores)],
        })

    return {
        "per_metric": per_metric,
        "unknown": unknown,
        "combined": None,
        "scope_note": (
            "Regret is measured against the best option ON THIS LIST. An "
            "option you did not put on the table cannot appear here, so a "
            "small regret means these options differ little -- not that there "
            "is little to gain from the decision."),
        "no_scalar_note": (
            "Reported per metric and never summed. Adding them would need "
            "weights between, say, a year of retirement and $200k of "
            "terminal wealth, and that trade is yours rather than this "
            "packet's to assume."),
    }


def minimum_viable_change(alternatives: list, goal: Goal) -> Optional[dict]:
    """The smallest alternative that both holds every constraint and helps.

    "Smallest" is the number of config leaves it moves, not a score -- a
    ranking by magnitude would be the utility score ROADMAP forbids wearing a
    different name. Ties are broken by the order the alternatives were given,
    so the caller's own ordering decides rather than this function.
    """
    viable = [a for a in alternatives
              if a.get("verdict") in (ROBUST, DIRECTIONAL)
              and a.get("constraints_all_hold") is True
              and a.get("better_than_baseline_at_anchor") is True
              and not a.get("levers_violated")]
    if not viable:
        return None
    fewest = min(len(a["alternative"]["changes"]) for a in viable)
    for candidate in viable:
        if len(candidate["alternative"]["changes"]) == fewest:
            return candidate
    return None
