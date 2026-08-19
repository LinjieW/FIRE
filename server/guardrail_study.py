"""Phase 4 · what a policy would have done, measured on the plan's own paths.

ROADMAP's first acceptance item for this phase is that a conditional policy and
the no-policy baseline are compared **on the same batch of paths**. Two facts
make that exact here rather than approximate, and they are worth stating
because neither is obvious.

**The engine cannot give two configs the same paths.** Its inheritance and
eldercare samplers are conditional -- they draw only inside a branch -- so two
configs at one seed desynchronise after the first divergent draw. Phase 2 pinned
this in `tests/test_attribution_crn.py`, and Phase 3 declined to claim common
random numbers because of it.

**A guardrail is not a config.** ROADMAP also requires that it "只提示复核，不
自动修改计划", and that constraint is what rescues the first one: because a
policy never changes the plan, there is no second trajectory to generate. The
policy arm and the baseline arm ARE the same path -- one is the path, the other
is the path with an observer walking alongside it. So the comparison is an
identity, not a matched sample, and this module runs the engine exactly once.

What that buys is the number this phase exists for: **how often would this
guardrail have told you to act, in paths that turned out fine?** A policy with a
70% false-alarm rate is not a safety net, it is a source of exactly the anxious
decisions it was meant to prevent -- and no amount of reasoning about thresholds
reveals that. Running it does.

The observation series per path is deliberately annual and deliberately thin:
portfolio, spending and income are what a person can actually observe about
themselves each year. `lifetime_success` and `fire_age` are NOT in it -- a
person does not observe their own success rate mid-life, they would have to
re-run the model, and pretending otherwise would let a policy trigger on
something its owner could never have seen at the time.
"""
from __future__ import annotations

from typing import Callable, Optional

import guardrails as G

#: Triggers a simulated path can honestly exercise.
#:
#: Only one survives, and the reason is a units problem the first version of
#: this module got wrong in the most flattering direction.
#:
#: The engine's per-path output is NOMINAL: `accum_path[].total` runs 205k ->
#: 11.6M and `accum_path[].expenses` runs 42k -> 90k over one accumulation,
#: which is inflation, not overspending. Comparing those against a threshold
#: anchored on today's money made `overspend` report a 47.7% false-alarm rate
#: that was almost entirely the price level crossing a fixed line.
#:
#: In RETIREMENT the deflator is recoverable, because the engine emits real and
#: nominal consumption side by side: `cpi[i] = nominal[i] / real[i]`. Checked
#: against the engine's own arithmetic -- `portfolio_path[-1] / cpi[-1]` equals
#: `terminal_after_tax_real` exactly.
#:
#: In ACCUMULATION there is no such pair and no CPI series, so real values
#: cannot be recovered at all. Those years are emitted as unmeasured rather
#: than as nominal figures wearing a `_real` name, and `guardrails.evaluate`
#: HOLDS a streak across an unmeasured period instead of clearing it.
#:
#: `income_real` used to be unavailable in both phases, so INCOME_INTERRUPTION
#: was unobservable and had previously reported `fire_rate 0.0%,
#: false_alarm 0.0%` -- the numbers of a flawless guardrail, produced by not
#: measuring. That is closed now, and it took the two engine changes ROADMAP
#: Phase 4 named as a condition rather than the one it first assumed:
#:
#:   * the retirement loop emits `income_received_path_nominal`;
#:   * the accumulation projector emits `cpi` and `contributions_nominal`.
#:
#: The second was not optional. The ONLY income interruption this engine
#: models is the layoff, and the layoff is an accumulation mechanic -- without
#: a CPI for those years every accumulation observation stayed unmeasured and
#: the trigger had nothing to fire on.
#:
#: The trigger is exercised on RETIREMENT income only, and that boundary is
#: the honest one. Accumulation now emits contributions -- a layoff is visible
#: there as a drop -- but contributions are not income, and feeding them to a
#: policy whose baseline is retirement income produced a false-alarm rate of
#: 0.79 that measured a unit mismatch. They are carried as
#: `contributions_real` under their own name instead, so nothing is lost and
#: nothing is compared to the wrong thing.
#:
#: What the accumulation CPI DID close is separate and real: those years'
#: portfolio and spending are now measurable at all, where before every one of
#: them was `None`.
OBSERVABLE_TRIGGERS = (G.PORTFOLIO_BELOW_BAND, G.PERMANENT_OVERSPEND,
                       G.INCOME_INTERRUPTION)

#: And which phases they can be exercised in. Both, now that accumulation
#: years can be deflated -- it was `"retirement"` alone while they could not.
OBSERVABLE_PHASE = ("accumulation", "retirement")


#: Why each unobservable trigger is unobservable, so a zero is never bare.
_WHY_NOT = {
    G.SUCCESS_RATE_DECLINING:
        "a person does not observe their own success rate mid-life; it needs a "
        "model re-run, which is a check-in event, not something a simulated "
        "path supplies",
    G.FIRE_AGE_RECEDING:
        "same as the success rate -- a projected FIRE age is model output, not "
        "something observable in a given year",
}


class StudyError(RuntimeError):
    """A study this module will not run rather than run misleadingly."""


def observations_from_path(result: dict) -> list:
    """One annual observation per year of a single simulated path.

    Everything is in today's money or is `None`. Nothing is emitted under a
    `_real` name that is not real -- see `OBSERVABLE_TRIGGERS` for why that
    distinction produced a fabricated false-alarm rate.
    """
    out = []
    for row in (result.get("accum_path") or []):
        if not isinstance(row, dict):
            continue
        # Deflated by the projector's own cumulative CPI. Before that series
        # existed every one of these years was correctly reported as
        # unmeasured, which is honest and is also why the only income
        # interruption this engine models could never be observed.
        cpi = row.get("cpi")
        contributions = row.get("contributions_nominal")
        measurable = bool(cpi)
        out.append({"age": row.get("age"),
                    "portfolio_real": (row.get("total") / cpi) if measurable
                                      else None,
                    "spending_real": (row.get("expenses") / cpi) if measurable
                                     else None,
                    # Deliberately NOT `income_real`. Contributions are real
                    # and measured, but they are not income, and an income
                    # policy whose baseline is retirement income would compare
                    # 150k against 87k of contributions and fire almost every
                    # year -- a false alarm rate of 0.79 that describes a unit
                    # mismatch rather than a guardrail. Measured here first,
                    # and it was exactly the fabrication the old comment
                    # warned about, arriving by a different door.
                    "income_real": None,
                    "contributions_real": ((contributions / cpi)
                                           if (measurable and contributions is not None)
                                           else None),
                    "income_basis": "contributions_not_income",
                    "portfolio_nominal": row.get("total"),
                    "spending_nominal": row.get("expenses"),
                    "cpi": cpi,
                    "phase": "accumulation",
                    "unmeasured_reason": ("" if measurable else
                                          "this path carries no CPI for the "
                                          "accumulation years")})
    # `withdrawal` is None for a path that never reached FIRE. Those paths have
    # no measurable years at all, which is why `study_policies` counts them
    # separately instead of letting them dilute a rate.
    withdrawal = result.get("withdrawal") or {}
    balances = withdrawal.get("portfolio_path") or []
    income_nominal = withdrawal.get("income_received_path_nominal") or []
    real_spend = withdrawal.get("real_consumption_path") or []
    nominal_spend = withdrawal.get("nominal_consumption_path") or []
    start_age = result.get("fire_age")
    # `portfolio_path` carries ONE MORE entry than the consumption series: its
    # first element is the opening balance at FIRE (it equals `fire_balance`),
    # and element i+1 is the balance after year i's spending. Pairing them by
    # a shared index therefore labels each year with the balance it started
    # with, and drops the final year entirely. The engine's own arithmetic
    # settles the correct pairing: `portfolio_path[-1] / cpi[-1]` equals
    # `terminal_after_tax_real`, i.e. the LAST balance against the LAST
    # consumption year.
    for index, real in enumerate(real_spend):
        nominal = nominal_spend[index] if index < len(nominal_spend) else None
        cpi = (nominal / real) if (real and nominal) else None
        closing = balances[index + 1] if index + 1 < len(balances) else None
        out.append({
            "age": (start_age + index) if start_age is not None else None,
            "portfolio_real": (closing / cpi) if (cpi and closing is not None)
                              else None,
            "spending_real": real,
            # The engine's own per-year record, deflated by the same CPI the
            # spending pair implies. `None` only when that year has no CPI to
            # divide by -- never imputed.
            "income_real": ((income_nominal[index] / cpi)
                            if (cpi and index < len(income_nominal)) else None),
            "income_basis": "income_received",
            "portfolio_nominal": closing,
            "spending_nominal": nominal,
            "cpi": cpi,
            "phase": "retirement"})
    return out


def _path_ended_well(result: dict) -> bool:
    """Did this path turn out fine? The engine's own verdict, not a proxy."""
    success = result.get("lifetime_success")
    if success is None:
        return bool(result.get("reached_fire"))
    return bool(success)


def study_policies(config: dict, policies: list, *, paths: int, seed: int,
                   runner: Optional[Callable] = None) -> dict:
    """Walk every policy along every simulated path, once.

    Returns per-policy rates, of which the one that matters is
    `false_alarm_rate`: the share of paths that ended well in which this policy
    would nonetheless have said Act.
    """
    if not policies:
        raise StudyError("no policies to study")
    for policy in policies:
        if not isinstance(policy, G.Policy):
            raise StudyError("policies must be Policy instances")

    if runner is not None:
        results = runner(config, paths, seed)
    else:
        import engine_adapter as ENG
        results = ENG._run(config, int(paths), int(seed), False)
    if not results:
        raise StudyError("the engine returned no paths")

    series = [observations_from_path(r) for r in results]
    outcomes = [_path_ended_well(r) for r in results]
    # A path is only evidence about a policy if the policy could see anything
    # in it. A path that never reached FIRE has no retirement years, and
    # accumulation is unmeasured, so it contributes no observation -- counting
    # it in the denominator would report "this policy caught 0% of failures"
    # when the truth is "this policy was never given a chance to look".
    measurable = [any(o.get("portfolio_real") is not None
                      or o.get("spending_real") is not None for o in obs)
                  for obs in series]
    total = len(results)
    good = sum(1 for ok, m in zip(outcomes, measurable) if ok and m)
    bad = sum(1 for ok, m in zip(outcomes, measurable) if not ok and m)
    unmeasurable_bad = sum(1 for ok, m in zip(outcomes, measurable)
                           if not ok and not m)

    per_policy = []
    for policy in policies:
        observable = policy.trigger in OBSERVABLE_TRIGGERS
        fired, fired_good, fired_bad, lead_times = 0, 0, 0, []
        for observations, ok, seen in zip(series, outcomes, measurable):
            if not seen:
                continue
            verdict = G.evaluate(policy, observations)
            if not verdict["would_have_fired"]:
                continue
            fired += 1
            if ok:
                fired_good += 1
            else:
                fired_bad += 1
                index = verdict["first_action_index"]
                if index is not None:
                    lead_times.append(len(observations) - index)
        per_policy.append({
            "policy_id": policy.policy_id,
            "trigger": policy.trigger,
            "action": policy.action,
            # Whether a simulated path can honestly exercise this trigger at
            # all. An unobservable one reports zeros, and says why, rather than
            # reporting a reassuring zero that means "never tested".
            "observable_in_simulation": observable,
            "not_measured_reason": ("" if observable else _WHY_NOT.get(
                policy.trigger, "not observable in a simulated path")),
            "fired_paths": fired,
            "measurable_paths": good + bad,
            "fire_rate": (fired / (good + bad)) if (good + bad) else None,
            # THE number: it said Act, and the path was fine anyway.
            "false_alarm_rate": (fired_good / good) if good else None,
            # And the other side of it: it said Act in a path that did fail.
            "caught_rate": (fired_bad / bad) if bad else None,
            "median_years_of_warning": (
                sorted(lead_times)[len(lead_times) // 2] if lead_times else None),
        })

    return {
        "paths": total,
        "seed": int(seed),
        "measurable_paths": good + bad,
        "ended_well": good,
        "ended_badly": bad,
        # Failures a guardrail could not have seen coming here, because they
        # happen before the only phase this study can measure. Reported rather
        # than folded into `ended_badly`: a catch rate computed over paths with
        # no observations is a zero that means "never tested", and a zero that
        # means "never tested" reads exactly like a zero that means "never
        # missed".
        "ended_badly_before_any_measurable_year": unmeasurable_bad,
        "coverage_warning": (
            "" if not unmeasurable_bad else
            "%d of %d failing paths failed during accumulation, which this "
            "study cannot measure -- no CPI series exists for those years. The "
            "catch rates below describe retirement-phase failures only."
            % (unmeasurable_bad, unmeasurable_bad + bad)),
        "policies": per_policy,
        # Stated in the payload because it is the claim a reader would
        # otherwise have to take on trust, and it is the phase's central one.
        "exercised_phase": OBSERVABLE_PHASE,
        "accumulation_years_unmeasured": True,
        "same_paths": True,
        "same_paths_basis": (
            "The engine ran once. A guardrail never modifies the plan, so the "
            "policy arm and the no-policy baseline are the same trajectory -- "
            "the comparison is an identity rather than a matched sample. This "
            "matters because this engine's conditional samplers cannot give "
            "two different configs the same paths at all."),
        "modifies_plan": False,
    }
