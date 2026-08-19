"""Phase 3 · running a decision across the three axes `Robust` is defined over.

`ensemble.py` measures what one assumption does. `decision_packet.py` judges
what a verdict is allowed to claim. This is the part between them: it takes a
question, a baseline and some alternatives, works out every run needed to test
the direction across seeds, return models and adverse assumption packs, runs
them, and hands the measurements to the judge.

Two things it refuses to do quietly.

**It will not start a study without saying what it costs.** The run count is
the product of alternatives and axis points, and at Standard precision each one
is a full Monte Carlo. `plan_study` returns that count without running
anything, so a caller can put a number in front of the user before a five-minute
job starts rather than after.

**It will not let an axis quietly shrink.** `Robust` is defined over three
axes, and a study that ran only two of them would produce a verdict the word
does not cover. Missing an axis is an error here, not a smaller claim -- the
smaller claim would still print as `robust`.

The four generic questions and the backend-only annuitization question are
ROADMAP's, and their levers are real config paths checked against
`default_config()`. An earlier draft of a sibling module invented
`state.retire_age`, which does not exist; the leaf that moves a retirement
date is `state.accum_years`.
"""
from __future__ import annotations

import copy
from typing import Callable, Optional

import decision_packet as DP
import ensemble as ENS
import packet_evidence as PE

#: The axes `Robust` is defined over. Naming them here rather than accepting
#: whatever a caller passes is what stops a two-axis study printing as robust.
REQUIRED_AXES = ("seeds", "return_models", "adverse_packs")

#: ROADMAP's four high-frequency decisions plus the annuitization workflow,
#: with the levers each is allowed to move. The fifth question is deliberately
#: backend-only: its alternatives come from the user's quote sheet through
#: `guaranteed_income_packet`, not from the generic browser lever picker.
#: Every path below exists in `default_config()`.
QUESTIONS = {
    "transition": {
        "question": "What does this life transition change?",
        "objective": "lifetime_success",
        "direction": "higher_is_better",
        # The union of what the five transitions can actually edit. NOT a
        # hand-copied list: `test_life_transitions` derives the same union
        # from the transition builders and fails if they diverge.
        #
        # A transition could not reuse an existing question's levers. Mapping
        # it to `large_life_choice` would have let the study run twenty-odd
        # engines and then report "this alternative moved a lever the goal
        # does not list" -- which is E20-1, fixed earlier the same day.
        "levers": ["household.enabled", "household.spouse_base_salary_pre",
                   "household.spouse_bonus_pre",
                   "contributions.base_salary_pre", "inheritance.mode"],
        "note": "a transition is not a choice being weighed but a change that "
                "has happened; the packet measures what it did, and the "
                "direction is kept as higher-is-better so the wording of a "
                "verdict stays the same as everywhere else",
    },
    "earlier_fire": {
        "question": "Can I reach financial independence earlier?",
        "objective": "lifetime_success",
        "direction": "higher_is_better",
        "levers": ["state.accum_years", "contributions"],
        "note": "accum_years is the accumulation length; there is no "
                "retire_age leaf",
    },
    "higher_spending": {
        "question": "Can I permanently raise what I spend?",
        "objective": "lifetime_success",
        "direction": "higher_is_better",
        "levers": ["state.expenses_y0", "state.spending_decline"],
        "note": "the objective stays success, not consumption: raising "
                "spending always raises consumption, so scoring on "
                "consumption would make every alternative look good",
    },
    "coast_or_barista": {
        "question": "Can I stop saving, or move to part-time work?",
        "objective": "lifetime_success",
        "direction": "higher_is_better",
        "levers": ["contributions", "income_streams"],
        "note": "coasting cuts contributions; barista adds part-time income",
    },
    "large_life_choice": {
        "question": "Can I buy, relocate, or take a sabbatical?",
        "objective": "lifetime_success",
        "direction": "higher_is_better",
        "levers": ["housing", "relocation", "income_streams", "life_events",
                   # The ONGOING cost of the choice, which is what the panel
                   # actually offers: "raise annual spending permanently by
                   # 15% (the ongoing cost of a house, a move, or a
                   # sabbatical)". Without it the one alternative this
                   # question can produce moved a path the goal did not list,
                   # so the packet spent twenty-odd engine runs and then said
                   # the alternative does not answer the question. That is
                   # still not a savings-rate tweak -- the note below draws
                   # the line at tweaking the RATE, and this is the standing
                   # cost of a structural decision.
                   "state.expenses_y0"],
        "note": "one-off and structural changes, and their ongoing cost; "
                "not a savings-rate tweak",
    },
    "annuitization": {
        "question": "Does one of these quoted annuity choices improve "
                    "lifetime success versus not buying?",
        "objective": "lifetime_success",
        "direction": "higher_is_better",
        "levers": ["guaranteed_income"],
        "note": "backend-only; the arms are the user's own quotes and the "
                "baseline is guaranteed income forced off by "
                "_annuity_context",
    },
}


#: How many points each axis needs before a verdict of a given strength is
#: allowed, per precision tier.
#:
#: This closes the attribution protocol's O5, which was referred here. O5's
#: finding: on the condition-5 fixture the conditional gate passed comfortably
#: (false-pass 0.028, power 1.000) and the packet was STILL reported
#: `underpowered`, because with S=5 clusters the outer bootstrap upper bound on
#: false-pass was 0.235. `df=4` is small and the outer resample is very coarse
#: there -- the packet was not short of evidence, it was short of a usable
#: bound on its own error.
#:
#: The same arithmetic governs a decision study, and it was governing it
#: silently. `Robust` was reachable at three seeds and three return models.
#: Three points is `df=2`: the spread across them is a two-degree-of-freedom
#: estimate of this engine's own randomness, which is enough to notice a
#: reversal and nowhere near enough to bound how often a non-reversal is luck.
#: So the tiers below say what each one may claim, and the packet carries the
#: bound rather than implying there is none.
#:
#: Only the two tiers a packet can actually BE. `build_packet` refuses anything
#: below Standard (decision_packet.py:592), so a `quick` row here would
#: describe a claim no packet can ever carry -- documentation of a capability
#: that does not exist, which is the failure this phase keeps meeting.
TIERS = {
    "standard": {
        "seeds": 3, "return_models": 3, "adverse_packs": 3,
        "max_verdict": DP.ROBUST,
        "why": "three seeds is df=2 -- enough to catch a reversal, not enough "
               "to bound how often a non-reversal is luck. `Robust` here means "
               "the direction held everywhere tested, with the number of "
               "points that tested it stated alongside it",
    },
    "official": {
        "seeds": 5, "return_models": 3, "adverse_packs": 5,
        "max_verdict": DP.ROBUST,
        "why": "five seeds is df=4, which is where the attribution protocol "
               "measured the outer bootstrap bound still degrading (O5). This "
               "is the strongest tier the current design offers and it is a "
               "floor, not a guarantee -- the bound is coarse here too, and "
               "the packet says so rather than dropping the caveat",
    },
}

#: Names the fact O5 established, carried into every packet so no reader has to
#: infer it from the point counts.
TIER_DISCLOSURE = (
    "Axis sizes bound what `Robust` can mean. The spread across seeds is an "
    "estimate of this engine's own randomness with as many degrees of freedom "
    "as there are seeds, minus one; at three that is two, which catches a "
    "reversal and cannot bound how often a non-reversal is chance. No tier "
    "here removes that limit -- the attribution protocol measured the same "
    "bound still degrading at five clusters (O5) -- so the count of points "
    "that actually saw the change travels with the verdict."
)


def tier_of(precision: str) -> dict:
    if precision not in TIERS:
        raise StudyError("unknown precision tier %r; this study defines %s"
                         % (precision, ", ".join(sorted(TIERS))))
    return TIERS[precision]


def tier_shortfall(precision: str, *, seeds: int, return_models,
                   adverse_packs) -> list:
    """Which axes are below the tier's floor, and by how much.

    Returned rather than raised: a plan with three applicable packs cannot
    reach the official tier no matter what the user does, and refusing the
    study would leave them with nothing. The study runs, and the verdict is
    capped instead.
    """
    tier = tier_of(precision)
    have = {"seeds": int(seeds), "return_models": len(return_models),
            "adverse_packs": len(adverse_packs)}
    return [{"axis": axis, "have": have[axis], "needs": tier[axis]}
            for axis in REQUIRED_AXES if have[axis] < tier[axis]]


#: A verdict may never be stronger than its tier allows.
_STRENGTH = (DP.INCONCLUSIVE, DP.UNSTABLE, DP.DIRECTIONAL, DP.ROBUST)


def cap_verdict(verdict: str, ceiling: str) -> str:
    if verdict not in _STRENGTH or ceiling not in _STRENGTH:
        return verdict
    return verdict if _STRENGTH.index(verdict) <= _STRENGTH.index(ceiling) \
        else ceiling


class StudyError(RuntimeError):
    """A study this module will not run rather than run partially."""


def _with_return_model(config: dict, model: str) -> dict:
    if model not in DP.RETURN_MODELS:
        raise StudyError("unknown return model %r; the engine accepts %s"
                         % (model, ", ".join(DP.RETURN_MODELS)))
    out = copy.deepcopy(config)
    out.setdefault("returns", {})["model"] = model
    return out


def plan_study(question_key: str, alternatives: list, *, seeds: int,
               return_models: tuple, adverse_packs: list) -> dict:
    """Every run the study needs, counted, without running any of them.

    The count is what a caller shows the user before starting. At Standard
    precision each run is a full Monte Carlo, so a modest-looking study is
    minutes of machine time and saying so first is the difference between a
    considered decision and a surprise.
    """
    if question_key not in QUESTIONS:
        raise StudyError("unknown question %r; ROADMAP's four are %s"
                         % (question_key, ", ".join(sorted(QUESTIONS))))
    if seeds < 2:
        raise StudyError(
            "the seeds axis needs at least two seeds; with one there is no "
            "spread and 'stable across seeds' is a claim about one draw")
    missing = [m for m in return_models if m not in DP.RETURN_MODELS]
    if missing:
        raise StudyError("unknown return model(s): %s" % ", ".join(missing))
    if not return_models:
        raise StudyError("the return_models axis cannot be empty")
    if not adverse_packs:
        raise StudyError(
            "the adverse_packs axis cannot be empty; `Robust` is defined over "
            "adverse assumptions and a study without any has not tested them")
    for pack in adverse_packs:
        if not isinstance(pack, ENS.AssumptionPack):
            raise StudyError("adverse packs must be AssumptionPack instances")

    per_arm = seeds + len(return_models) + len(adverse_packs)
    arms = 1 + len(alternatives)
    return {
        "question": question_key,
        "arms": arms,
        "points_per_arm": per_arm,
        "axes": {"seeds": seeds, "return_models": len(return_models),
                 "adverse_packs": len(adverse_packs)},
        "engine_runs": arms * per_arm,
    }


def _tasks_for_arm(label: str, config: dict, *, paths: int, seed: int,
                   dist_paths: int, root: str, seeds: int,
                   return_models: tuple, adverse_packs: list) -> list:
    tasks = []
    for i in range(seeds):
        tasks.append({"label": label, "axis": "seeds", "key": "seed=%d" % (seed + i),
                      "config": config, "paths": paths, "seed": seed + i,
                      "dist_paths": dist_paths, "root": root})
    for model in return_models:
        tasks.append({"label": label, "axis": "return_models", "key": model,
                      "config": _with_return_model(config, model),
                      "paths": paths, "seed": seed, "dist_paths": dist_paths,
                      "root": root})
    for pack in adverse_packs:
        # Read in the pack's own scenario. Both arms use the same one for the
        # same key, so the comparison at that point is like-for-like; what it
        # is NOT like-for-like with is the home-scenario anchor, which is why
        # the packet discloses which points were read where.
        tasks.append({"label": label, "axis": "adverse_packs",
                      "key": pack.name, "config": pack.apply(config),
                      "paths": paths, "seed": seed, "dist_paths": dist_paths,
                      "root": root, "scenario": pack.scenario})
    return tasks


def run_study(base_config: dict, question_key: str, alternatives: list, *,
              paths: int, seed: int, root: str, adverse_packs: list,
              constraints: Optional[list] = None, dist_paths: int = 200,
              seeds: int = 3, return_models: tuple = DP.RETURN_MODELS,
              protocol: Optional[dict] = None,
              analyses: Optional[list] = None,
              on_progress: Optional[Callable] = None,
              runner: Optional[Callable] = None) -> dict:
    """Run one decision across all three axes and assemble its packet."""
    plan = plan_study(question_key, alternatives, seeds=seeds,
                      return_models=return_models, adverse_packs=adverse_packs)
    template = QUESTIONS[question_key]
    goal = DP.Goal(template["question"], template["objective"],
                   template["direction"], list(constraints or []),
                   list(template["levers"]))
    for alternative in alternatives:
        if not isinstance(alternative, DP.Alternative):
            raise StudyError("alternatives must be Alternative instances")

    tasks = _tasks_for_arm("baseline", base_config, paths=paths, seed=seed,
                           dist_paths=dist_paths, root=root, seeds=seeds,
                           return_models=return_models,
                           adverse_packs=adverse_packs)
    for alternative in alternatives:
        tasks.extend(_tasks_for_arm(
            alternative.name, alternative.apply(base_config), paths=paths,
            seed=seed, dist_paths=dist_paths, root=root, seeds=seeds,
            return_models=return_models, adverse_packs=adverse_packs))

    results = ENS._run_tasks(tasks, runner=runner,
                             on_progress=on_progress)
    measured = {}
    for task, result in zip(tasks, results):
        measured.setdefault(task["label"], {}).setdefault(task["axis"], []) \
            .append({"key": task["key"], "metrics": result["metrics"]})

    baseline_axes = measured.get("baseline") or {}
    missing = [axis for axis in REQUIRED_AXES if axis not in baseline_axes]
    if missing:
        raise StudyError(
            "the study is missing the %s axis; `Robust` is defined over all "
            "three, and a verdict from fewer would still print as robust"
            % ", ".join(missing))
    baselines = {axis: {p["key"]: p["metrics"] for p in points}
                 for axis, points in baseline_axes.items()}
    anchor_key = "seed=%d" % seed
    anchor_baseline = baselines["seeds"].get(anchor_key)
    if anchor_baseline is None:
        raise StudyError("the baseline was not run at the anchor seed")

    evaluated = []
    for alternative in alternatives:
        axes = measured.get(alternative.name) or {}
        anchor = next((p["metrics"] for p in axes.get("seeds", [])
                       if p["key"] == anchor_key), None)
        if anchor is None:
            raise StudyError("alternative %r was not run at the anchor seed"
                             % alternative.name)
        evaluated.append(DP.evaluate_alternative(
            alternative, goal, anchor_metrics=anchor,
            anchor_baseline=anchor_baseline, axes=axes, baselines=baselines))

    packet_protocol = dict(protocol or {})
    packet_protocol.setdefault("paths", paths)
    packet_protocol.setdefault("seed", seed)
    packet = DP.build_packet(question_key, goal, base_config=base_config,
                             baseline_metrics=anchor_baseline,
                             alternatives=evaluated,
                             protocol=packet_protocol)
    packet["plan"] = plan
    packet["adverse_packs"] = [p.describe() for p in adverse_packs]
    packet["return_models"] = list(return_models)
    packet["minimum_viable_change"] = DP.minimum_viable_change(evaluated, goal)
    packet["head_to_head"] = PE.head_to_head(evaluated, goal)

    # The tier gate. Applied after the verdicts are computed and before they
    # are published, because a verdict is a claim about evidence and the tier
    # is what says how much evidence there was. Capping is deliberate rather
    # than refusing: a plan with three applicable packs cannot reach the
    # official tier however it is run, and refusing would leave the user with
    # nothing instead of a smaller, honest answer.
    precision = str(packet_protocol.get("precision") or "")
    tier = tier_of(precision) if precision in TIERS else None
    if tier is not None:
        shortfall = tier_shortfall(precision, seeds=seeds,
                                   return_models=return_models,
                                   adverse_packs=adverse_packs)
        ceiling = DP.DIRECTIONAL if shortfall else tier["max_verdict"]
        for alternative in packet["alternatives"]:
            capped = cap_verdict(alternative["verdict"], ceiling)
            if capped != alternative["verdict"]:
                alternative["verdict_before_tier_cap"] = alternative["verdict"]
                alternative["verdict"] = capped
                alternative["why"] = (
                    "%s -- and the axes were below the %s tier's floor (%s), "
                    "so the claim is capped at %s regardless"
                    % (alternative["why"], precision,
                       "; ".join("%s had %d, needs %d"
                                 % (s["axis"], s["have"], s["needs"])
                                 for s in shortfall), capped))
        packet["tier"] = {"precision": precision, "floor": {
            axis: tier[axis] for axis in REQUIRED_AXES},
            "shortfall": shortfall, "max_verdict": ceiling,
            "why": tier["why"], "disclosure": TIER_DISCLOSURE}
    if analyses:
        # Attached as context and checked afterwards. Every lab analysis is
        # capped below this packet's precision, so none of them may move a
        # verdict; the check is what makes that a rule rather than a hope.
        import copy as _copy
        before = _copy.deepcopy(packet)
        PE.attach_analyses(packet, analyses)
        PE.assert_verdicts_unchanged(before, packet)
    packet["sampling_disclosure"] = ENS.run_ensemble.__doc__ and (
        "Effects across seeds are this engine's own sampling spread; its "
        "inheritance and eldercare samplers are conditional, so two configs do "
        "not share a draw sequence and no common-random-number claim is made.")
    return packet
