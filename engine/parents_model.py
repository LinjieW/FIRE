"""4.0 Phase 2 · the parent lifecycle, as one object instead of two strangers.

The engine already models both halves of supporting a parent, and models them
as if the parent in each were a different person:

  * `EldercareShockParams` fires an independent Bernoulli **every year** in a
    31-year window, each hit drawing its own lognormal severity. On one path it
    can fire at 41, 52, 58 and 66 — four emergencies from a parent who never
    dies, because nothing in that sampler knows a parent can die.
  * `InheritanceParams` draws one lifetime Bernoulli, then an age **uniform**
    over its own window, then an amount. The age is uniform because nothing
    tells it when the parent died.

So the engine happily produces a path where the bequest lands at 57 and the
care payments continue to 68 — money inherited from someone still being cared
for. Each module is individually defensible and the pair is not, which is this
project's second recurring failure: parts that pass their own tests and an
assembly nobody checked.

What this module changes, and what it deliberately does not
-----------------------------------------------------------
One parent is one object with one death. Everything else derives from it:

  * the parent's death age is drawn ONCE, from the same Gompertz tables the
    plan already uses for the user — no new mortality data enters the repo;
  * care can only happen while the parent is alive, and a care episode is
    TRUNCATED at death rather than allowed to outlive them;
  * the bequest arrives AT the death, not at a uniform draw over a window;
  * care is paid for out of the estate first, by a declared share, so a long
    expensive decline leaves less to inherit. That is the "negative correlation
    dial" — and it is a mechanism rather than a correlation coefficient, so a
    user can say whether they believe it.

The care model is not re-derived here. `ltc_model` already carries an
age-based entry hazard, a duration distribution with the tail that matters, and
a calibration against a mortality table; a parent needing care is that same
process applied to a different person. Writing a second one would mean two
sets of numbers to keep honest, and the second set would drift.

**No new empirical constant is introduced by this file.** Every figure it uses
is either the plan's own (mortality) or `ltc_model`'s already-disclosed own
(entry, duration, level mix, cost). What is new is structure. The one genuinely
new parameter — how much of the care bill the estate absorbs — has no empirical
answer at all, so it is a dial with a stated default and no pretence.

The false zero, in this module's own terms
------------------------------------------
`bequest = 0` is the same character whether the parent left nothing, the parent
is still alive at the end of the simulation, the estate was consumed by care,
or the module never ran. Every return here carries a `reason`, and the caller
is expected to show it rather than print the zero.
"""
from __future__ import annotations


from dataclasses import dataclass, field

import ltc_model as LTC

#: Mode names, deliberately the same three words `ltc_model` uses. A user who
#: has met one opt-in module should not have to learn a second vocabulary.
OFF = "off"
STOCHASTIC = "stochastic"
SCENARIO = "scenario"
MODES = (OFF, STOCHASTIC, SCENARIO)

#: How much of the parent's care bill their own estate pays before the plan
#: does. 1.0 = their savings are spent first and the plan covers the remainder;
#: 0.0 = the plan pays every dollar and the estate is inherited untouched.
#:
#: **This dial does not change how much the plan ends up with, and saying
#: otherwise was wrong.** The first version of this comment called 1.0 "the
#: conservative end", which does not survive the arithmetic: whatever the
#: share, the plan's net position is `estate - care`. At 1.0 it pays nothing
#: and inherits what is left; at 0.0 it pays everything and inherits the lot.
#: Measured through the engine, moving the dial from 1.0 to 0.25 changed median
#: real consumption by +21 on 72,324 — 0.03%, and in the *favourable*
#: direction, which is the tell that it is not a risk lever at all.
#:
#: What it does change is real but narrow, and worth having:
#:   * **timing** — care leaves the accounts year by year while a bequest
#:     arrives in one lump at the death, so the same net figure is not the same
#:     sequence of cash;
#:   * **the floor** — care beyond what the estate can cover falls to the plan
#:     regardless, which is where a family's actual arrangement stops being a
#:     bookkeeping choice.
#:
#: So it stays a parameter, and stays at 1.0 by user ruling (2026-08-11), but
#: it is a statement about WHEN money moves rather than about whether the plan
#: is safe. `test_the_dial_moves_money_between_ledgers_without_changing_the_net`
#: holds the identity, because an assertion that the two ends merely "differ"
#: was measuring a rounding artefact and reading as though the dial mattered.
DEFAULT_ESTATE_SHARE_OF_CARE = 1.0


class ParentsError(ValueError):
    """A parent configuration this module refuses rather than models badly."""


@dataclass
class Parent:
    """One parent, as the plan describes them today.

    `estate_y0` is what the user expects to inherit from this parent in
    today's money BEFORE any care costs — not a net figure. The netting is
    what this module does; asking for a net figure would ask the user to do
    the calculation themselves and then hide it.
    """

    label: str = "parent"
    current_age: int = 70
    sex: str = "female"
    estate_y0: float = 0.0
    #: None => this parent is modelled as never needing paid care. Distinct
    #: from 0.0, which would be a measured zero risk.
    care_lifetime_risk: float | None = None


@dataclass
class ParentsParams:
    """The plan's parent settings.

    `rng` is runtime, not configuration: an independent stream set by the
    adapter, on the `LtcParams.rng` precedent, so `mode = off` is bit-identical
    by construction — off draws from nothing at all.
    """

    mode: str = OFF
    parents: list = field(default_factory=list)
    estate_share_of_care: float = DEFAULT_ESTATE_SHARE_OF_CARE
    #: Care cost settings are the LTC module's, reused rather than re-derived.
    care_level_mix: dict | None = None
    care_annual_cost: dict | None = None
    care_duration_buckets: tuple | None = None
    cost_excess_inflation: float = LTC.DEFAULT_COST_EXCESS_INFLATION
    #: Scenario mode: the user states the story instead of drawing it.
    scenario_death_age: int = 88
    scenario_care_years: float = 3.0
    scenario_care_level: str = LTC.ASSISTED_LIVING
    #: The counterfactual behind the zero-bequest honesty flag. It suppresses
    #: the INFLOW while leaving the care outflow exactly as it was — the estate
    #: still pays its share, the plan still pays the rest. Zeroing `estate_y0`
    #: instead would answer a different and less useful question, because it
    #: would also stop the parent funding their own care and so change the cost
    #: side at the same time. What a user wants to know is narrower: does this
    #: plan still work if the money never actually reaches me — spent late,
    #: left to someone else, or gone to care in a country this model does not
    #: cover.
    assume_zero_bequest: bool = False
    rng: object = None


def death_age_distribution(annual_death_rate, *, current_age: int,
                           cap_age: int = 110) -> list:
    """The whole distribution of remaining years, never a life expectancy.

    `annual_death_rate(age)` is the plan's OWN hazard, passed in rather than
    re-derived here — the same shape `ltc_model.survival_rates_for` takes, and
    for the same reason. The first draft of this function integrated the
    Gompertz hazard in closed form instead, which is exact for the continuous
    model and **wrong for this engine**: `annual_mortality_rate` holds the
    hazard constant within each year at its value at the year's start, so a
    closed-form integral of a rising hazard reports more deaths. Measured, the
    two disagreed by 4.95% on survival from 70 to 90. That would have put the
    parent and the user on two different mortality models while appearing to
    use one table — precisely the kind of quiet divergence this module exists
    to remove.

    A single expected death age would be the same mistake a median care
    duration would be: the decisions this module informs — how long support
    might run, when a bequest might land — are decided by the spread, and an
    expectation throws the spread away.

    Returns `[{"age", "prob"}]` over `current_age+1 .. cap_age`, summing to 1
    (the cap absorbs the remainder, as the engine's own `cap_age` does).
    """
    if current_age >= cap_age:
        return [{"age": int(cap_age), "prob": 1.0}]
    out, alive = [], 1.0
    for age in range(int(current_age) + 1, int(cap_age) + 1):
        q = float(annual_death_rate(age))
        if not 0.0 <= q <= 1.0:
            raise ParentsError("annual death rate at age %d is %r, which is "
                               "not a probability" % (age, q))
        out.append({"age": age, "prob": alive * q})
        alive -= alive * q
    if out:
        out[-1]["prob"] += alive          # the cap is a forced death
    return out


def sample_death_age(rng, annual_death_rate, *, current_age: int,
                     cap_age: int = 110) -> int:
    """One draw from `death_age_distribution`.

    Exactly one uniform is consumed whatever the outcome, so a parent who dies
    early and one who reaches the cap cost the same number of draws. That is
    deliberate: condition 4 of the attribution protocol showed that a sampler
    which draws a different NUMBER of times depending on its own outcome
    desynchronises every later draw, and both modules this one replaces have
    exactly that shape.
    """
    dist = death_age_distribution(annual_death_rate, current_age=current_age,
                                  cap_age=cap_age)
    u = float(rng.random())
    cumulative = 0.0
    for row in dist:
        cumulative += row["prob"]
        if u < cumulative:
            return int(row["age"])
    return int(dist[-1]["age"])


def care_window(death_age: int, onset_age: float, years: float) -> dict:
    """A care episode clipped to the parent's own life.

    The whole point of the shared object. An episode that would run past death
    is truncated, and the truncation is REPORTED rather than silently applied:
    a plan whose support cost was cut short by a death should be able to say so,
    because the alternative reading — "care was cheap" — is the wrong lesson.
    """
    if years < 0:
        raise ParentsError("a care episode cannot last negative years")
    onset = float(onset_age)
    if onset >= death_age:
        return {"onset_age": None, "years": 0.0, "truncated_by_death": False,
                "reason": "care would have begun at or after this parent's "
                          "death, so none was charged"}
    available = float(death_age) - onset
    if years > available:
        return {"onset_age": onset, "years": available,
                "truncated_by_death": True,
                "reason": "the episode was cut short by this parent's death: "
                          "%.1f years drawn, %.1f chargeable"
                          % (years, available)}
    return {"onset_age": onset, "years": float(years),
            "truncated_by_death": False,
            "reason": "the full episode fell within this parent's life"}


def split_care_cost(total_cost: float, estate_y0: float, *,
                    estate_share: float) -> dict:
    """Who pays, and what is left to inherit.

    The mechanism behind the negative-correlation dial. Deliberately arithmetic
    rather than a correlation parameter: a user can check "the estate pays
    first, and what it cannot cover falls to me" against their own situation,
    and cannot check whether rho should be -0.4.
    """
    if not 0.0 <= estate_share <= 1.0:
        raise ParentsError("estate_share_of_care must be between 0 and 1, "
                           "got %r" % (estate_share,))
    total = max(0.0, float(total_cost))
    estate = max(0.0, float(estate_y0))
    offered = total * float(estate_share)
    from_estate = min(estate, offered)
    return {"paid_by_estate": from_estate,
            "paid_by_user": total - from_estate,
            "bequest": estate - from_estate}


def sample_parents(params: ParentsParams, death_rate_for, *,
                   first_age: int, last_age: int, anchor_age: int = None,
                   cap_age: int = 110) -> tuple:
    """One draw per parent, into the two channels the engine already has.

    `death_rate_for(sex)` returns that parent's annual hazard function. A
    resolver rather than one rate function, because each parent has their own
    table: the first version of this took a single `annual_death_rate` and so
    ran a mother and a father on whichever table the PLAN HOLDER uses, leaving
    `Parent.sex` read by nothing at all. The panel offered the control, the
    user chose, and no number moved — the exact defect the panel contract test
    exists to catch, found by a mutation surviving it. The gap between the
    tables is not small: about 20% of hazard across 65-100.

    Returns `(care_events, bequests, meta)`, both lists of `(plan_age, amount)`
    in today's money:

      * `care_events` are OUTFLOWS — the share of the parent's care the plan
        pays — and go through the eldercare channel, which is the same funding
        machinery because it models the same thing;
      * `bequests` are INFLOWS at the age the parent dies, which is the whole
        point: the existing module drew that age uniformly over a window with
        nothing connecting it to the parent at all.

    `first_age`/`last_age` bound the modelled window in the PLAN HOLDER's ages.
    A parent's death or care outside it is reported in `meta` and charged
    nothing, rather than clamped to the edge so the numbers look complete.

    OFF draws nothing — not a discarded draw, not a draw on another stream —
    so a run with the module off is bit-identical by construction.
    """
    meta = {"mode": OFF, "parents": [],
            "reason": "the parent lifecycle module is off"}
    if params is None or params.mode == OFF:
        return [], [], meta
    if params.mode not in MODES:
        raise ParentsError("unknown parents mode %r; this module knows %s"
                           % (params.mode, ", ".join(MODES)))
    meta["mode"] = params.mode
    meta["reason"] = "one death per parent, with care and bequest derived from it"
    if params.mode == STOCHASTIC and params.rng is None:
        raise ParentsError(
            "stochastic parent lifecycles need their own rng; the adapter sets "
            "an independent stream, and leaving it unset would silently model "
            "no parents at all")

    anchor = int(anchor_age if anchor_age is not None else first_age)
    costs = {LTC.HOME_CARE: (params.care_annual_cost or {}).get(
                 LTC.HOME_CARE, LTC.DEFAULT_ANNUAL_COST[LTC.HOME_CARE]),
             LTC.ASSISTED_LIVING: (params.care_annual_cost or {}).get(
                 LTC.ASSISTED_LIVING, LTC.DEFAULT_ANNUAL_COST[LTC.ASSISTED_LIVING]),
             LTC.NURSING_HOME: (params.care_annual_cost or {}).get(
                 LTC.NURSING_HOME, LTC.DEFAULT_ANNUAL_COST[LTC.NURSING_HOME])}
    buckets = params.care_duration_buckets or LTC.DEFAULT_DURATION_BUCKETS
    mix = params.care_level_mix or LTC.DEFAULT_LEVEL_MIX

    care_events, bequests = [], []
    for index, parent in enumerate(params.parents):
        offset = int(first_age) - int(parent.current_age)   # plan age - parent age
        row = {"label": parent.label or ("parent %d" % (index + 1)),
               "index": index, "death_age": None, "death_plan_age": None,
               "care_onset_age": None, "care_years": 0.0,
               "care_total": 0.0, "paid_by_estate": 0.0, "paid_by_plan": 0.0,
               "bequest": None, "truncated_by_death": False,
               "died_within_horizon": False}

        if params.mode == SCENARIO:
            death_age = int(params.scenario_death_age)
            care_years = float(params.scenario_care_years)
            onset = death_age - care_years
            level = params.scenario_care_level
        else:
            death_age = sample_death_age(params.rng,
                                         death_rate_for(parent.sex),
                                         current_age=int(parent.current_age),
                                         cap_age=cap_age)
            # One draw decides whether this parent ever needs paid care, and a
            # second and third place and size it. Always three draws, whatever
            # the outcome, for the reason `sample_death_age` states.
            u_enter = float(params.rng.random())
            u_when = float(params.rng.random())
            u_level = float(params.rng.random())
            risk = parent.care_lifetime_risk
            if risk is None:
                row["care_reason"] = ("this parent is modelled as never needing "
                                      "paid care, because the plan states no "
                                      "lifetime risk for them")
                care_years, onset, level = 0.0, None, None
            elif u_enter >= float(risk):
                row["care_reason"] = ("no care episode was drawn for this "
                                      "parent on this path")
                care_years, onset, level = 0.0, None, None
            else:
                care_years = _pick_duration(u_when, buckets)
                level = _pick_level(u_level, mix)
                onset = death_age - care_years

        row["death_age"] = int(death_age)
        row["death_plan_age"] = int(death_age) + offset

        if onset is not None and care_years > 0:
            window = care_window(int(death_age), onset, care_years)
            row["truncated_by_death"] = window["truncated_by_death"]
            row["care_reason"] = window["reason"]
            if window["onset_age"] is not None and window["years"] > 0:
                raw = LTC.expand_episode(
                    int(round(window["onset_age"] + offset)), window["years"],
                    float(costs[level]), last_age=int(last_age),
                    excess_inflation=params.cost_excess_inflation,
                    anchor_age=anchor)
                inside = [(age, amt) for age, amt in raw
                          if int(first_age) <= age <= int(last_age)]
                total = sum(amt for _, amt in inside)
                split = split_care_cost(total, parent.estate_y0,
                                        estate_share=params.estate_share_of_care)
                row.update({"care_onset_age": window["onset_age"],
                            "care_years": window["years"],
                            "care_level": level, "care_total": total,
                            "paid_by_estate": split["paid_by_estate"],
                            "paid_by_plan": split["paid_by_user"]})
                if len(inside) < len(raw):
                    row["care_outside_window"] = len(raw) - len(inside)
                # The plan pays its share pro rata across the care years rather
                # than as one lump, because that is when the money leaves.
                if total > 0 and split["paid_by_user"] > 0:
                    scale = split["paid_by_user"] / total
                    care_events.extend((age, amt * scale) for age, amt in inside)

        estate_left = max(0.0, float(parent.estate_y0) - row["paid_by_estate"])
        death_plan_age = row["death_plan_age"]
        within = int(first_age) <= death_plan_age <= int(last_age)
        row["died_within_horizon"] = within
        if within:
            row["bequest"] = estate_left
            if estate_left > 0 and not params.assume_zero_bequest:
                bequests.append((death_plan_age, estate_left))
            if params.assume_zero_bequest:
                # Recorded, not silently dropped: the row still says what the
                # bequest would have been, so a reader can see the size of what
                # the counterfactual removed.
                row["bequest_suppressed"] = estate_left
        else:
            row["bequest"] = None
        row["bequest_reason"] = bequest_reason(
            estate_left if within else 0.0, estate_y0=parent.estate_y0,
            from_estate=row["paid_by_estate"], died_within_horizon=within)
        meta["parents"].append(row)

    return care_events, bequests, meta


def _pick_duration(u: float, buckets) -> float:
    """Inverse-CDF over `ltc_model`'s duration buckets. One uniform, always."""
    total = sum(share for _, share in buckets) or 1.0
    cumulative = 0.0
    for years, share in buckets:
        cumulative += share / total
        if u < cumulative:
            return float(years)
    return float(buckets[-1][0])


def _pick_level(u: float, mix: dict) -> str:
    """Inverse-CDF over the care-level mix, in a fixed order so the same
    uniform always means the same level."""
    order = [LTC.HOME_CARE, LTC.ASSISTED_LIVING, LTC.NURSING_HOME]
    total = sum(float(mix.get(k, 0.0)) for k in order) or 1.0
    cumulative = 0.0
    for level in order:
        cumulative += float(mix.get(level, 0.0)) / total
        if u < cumulative:
            return level
    return order[-1]


def bequest_reason(bequest: float, *, estate_y0: float, from_estate: float,
                   died_within_horizon: bool) -> str:
    """Why the number is what it is — required because 0.0 has four meanings.

    Reported for every outcome, not only the zero, so a caller cannot start
    treating the presence of a reason as a warning sign.
    """
    if not died_within_horizon:
        return ("this parent was still alive at the end of the simulation, so "
                "nothing was inherited within the modelled window — this is "
                "NOT an estimate that they leave nothing")
    if estate_y0 <= 0:
        return "the plan states no estate for this parent"
    if bequest <= 0 and from_estate > 0:
        return ("the estate was fully consumed by this parent's care; the "
                "remainder of that bill fell to the plan")
    if from_estate > 0:
        return ("the estate paid %.0f of this parent's care and the rest was "
                "inherited" % from_estate)
    return "the estate was inherited in full; no care cost was charged to it"
