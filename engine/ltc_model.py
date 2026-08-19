"""4.0 Phase 2 · long-term care: the tail lean FIRE hides.

A plan that reports 90% success without modelling long-term care is not
reporting 90%. It is reporting the success of a different plan — one in which
the most expensive years of a long life do not happen. ROADMAP puts it as
"不建模等于把最重要的尾巴藏起来".

Distinct from `EldercareShock`, which models paying for a *parent*: a one-off
outflow in a 40–70 window. This is the user's own care — late, multi-year, and
fat-tailed. Phase 2 keeps both, and its parent-lifecycle module later refactors
the eldercare/inheritance pair; nothing here touches that.

**The tail is the product.** Roughly a quarter of people who enter care stay
more than two years, and that quarter is where the money is. Any summary that
reports a median duration has thrown away the reason to model this at all, so
`duration_distribution` returns the whole distribution and
`tail_share(years)` exists to be asserted on.

**Two modes, and the scenario one is not a lesser option.** A user who says
"assume five years" gets exactly five years. Most people cannot calibrate a
hazard curve but can answer "what if it were five years", and an answer to the
question they can actually pose beats a better-specified answer to one they
cannot.

**Numbers here are structure, not authority.** The hazard and duration figures
below are stated as the module's own defaults with their basis named, and they
belong in `assumption ensemble` as a dimension rather than as truth. Where a
real figure is unavailable the parameter is exposed rather than invented, on
the same principle that keeps annuity quotes and insurance premiums
user-supplied throughout this roadmap.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

OFF = "off"
STOCHASTIC = "stochastic"
SCENARIO = "scenario"
MODES = (OFF, STOCHASTIC, SCENARIO)

#: Care levels, cheapest first. Costs are annual, in today's money, and are
#: the module's defaults rather than a quote — a user in a high-cost metro can
#: pay double, and the parameter is exposed for exactly that reason.
HOME_CARE = "home_care"
ASSISTED_LIVING = "assisted_living"
NURSING_HOME = "nursing_home"

DEFAULT_ANNUAL_COST = {
    HOME_CARE: 62_000.0,
    ASSISTED_LIVING: 64_000.0,
    NURSING_HOME: 116_000.0,
}

#: Share of entries that begin at each level. A plan that assumed everyone goes
#: straight to a nursing home would overstate cost; one that assumed home care
#: for all would understate the tail, which is the expensive half.
DEFAULT_LEVEL_MIX = {HOME_CARE: 0.55, ASSISTED_LIVING: 0.25,
                     NURSING_HOME: 0.20}

#: Lifetime probability of ever needing care, by sex. Women's higher figure is
#: mostly longevity: they live long enough to need it more often, and are more
#: often the survivor with nobody at home to provide it unpaid.
DEFAULT_LIFETIME_RISK = {"male": 0.47, "female": 0.58}

#: Age at which care typically begins, and the spread around it.
DEFAULT_ONSET_AGE = 83.0
DEFAULT_ONSET_SPREAD = 7.0

#: The duration distribution, as explicit buckets rather than a fitted curve.
#: Buckets because the shape that matters is not smooth: a large share of
#: entries are short, and a long tail carries most of the cost. A lognormal fit
#: would smooth over exactly the discontinuity worth showing.
#: Calibrated so that `tail_share(2.0)` is about 0.24, which is the figure
#: ROADMAP names and the one this module exists for. The first draft of these
#: buckets put 44% past two years — nearly double — which would have made every
#: LTC-enabled plan look worse than the evidence supports. Pinned by a test,
#: because a number that carries the whole module should not be adjustable by
#: accident.
DEFAULT_DURATION_BUCKETS = (
    (0.5, 0.36),      # under a year: recovery, or death soon after entry
    (1.5, 0.40),
    (3.0, 0.14),
    (5.0, 0.07),
    (8.0, 0.03),      # the tail that decides whether a plan survives
)

#: Medical costs outrun general inflation; care costs outrun medical. Applied
#: on top of the plan's own inflation, so this is the EXCESS.
DEFAULT_COST_EXCESS_INFLATION = 0.01


class LtcError(ValueError):
    """A configuration this module refuses rather than models badly."""


def duration_distribution(buckets=None) -> list:
    """The whole distribution, never a summary.

    Returns `[{"years", "share"}]`. There is deliberately no
    `median_duration()` in this module: a median throws away the tail, and the
    tail is the entire reason to model long-term care.
    """
    rows = list(buckets or DEFAULT_DURATION_BUCKETS)
    total = sum(share for _, share in rows)
    if abs(total - 1.0) > 1e-9:
        raise LtcError("duration buckets must sum to 1.0, got %.6g" % total)
    return [{"years": years, "share": share} for years, share in rows]


def tail_share(years: float, buckets=None) -> float:
    """Share of care episodes lasting LONGER than `years`.

    Exists to be asserted on. ROADMAP's acceptance for this module is about
    whether the long tail stays visible, and a number nobody can check is not
    a disclosure.
    """
    return sum(row["share"] for row in duration_distribution(buckets)
               if row["years"] > years)


def expected_cost(*, level_mix=None, annual_cost=None, buckets=None) -> float:
    """Expected total cost of one care episode, today's money, undiscounted.

    Undiscounted on purpose: discounting here would bury the tail's weight
    inside a rate assumption the reader cannot see. The engine applies its own
    inflation and returns when this is wired in.
    """
    mix = dict(level_mix or DEFAULT_LEVEL_MIX)
    costs = dict(annual_cost or DEFAULT_ANNUAL_COST)
    missing = sorted(set(mix) - set(costs))
    if missing:
        raise LtcError("no annual cost for care level(s): %s"
                       % ", ".join(missing))
    if abs(sum(mix.values()) - 1.0) > 1e-9:
        raise LtcError("level mix must sum to 1.0, got %.6g" % sum(mix.values()))
    per_year = sum(share * costs[level] for level, share in mix.items())
    years = sum(row["years"] * row["share"]
                for row in duration_distribution(buckets))
    return per_year * years


def scenario_episode(years: float, *, level=NURSING_HOME,
                     annual_cost=None) -> dict:
    """Exactly what the user asked for, with nothing added.

    A user who says "assume five years" gets five years — not five years
    adjusted by a hazard, not a distribution centred on five. The whole value
    of the scenario mode is that its answer is inspectable.
    """
    if years < 0:
        raise LtcError("a care episode cannot last negative years")
    costs = dict(annual_cost or DEFAULT_ANNUAL_COST)
    if level not in costs:
        raise LtcError("unknown care level %r; this module knows %s"
                       % (level, ", ".join(sorted(costs))))
    return {"mode": SCENARIO, "years": float(years), "level": level,
            "annual_cost": costs[level],
            "total_cost": float(years) * costs[level],
            "basis": "exactly the duration the user specified; no hazard, no "
                     "distribution, no adjustment"}


def entry_hazard(age: float, *, sex: str = "female",
                 lifetime_risk=None, onset_age: float = DEFAULT_ONSET_AGE,
                 onset_spread: float = DEFAULT_ONSET_SPREAD) -> float:
    """Annual probability of entering care at `age`, given not yet in care.

    A logistic curve around `onset_age`, scaled so the lifetime integral
    approaches the declared lifetime risk. Deliberately simple and deliberately
    exposed: the shape is a modelling choice, and it belongs in the assumption
    ensemble rather than being defended as fact.

    CAUTION, added when this was wired into the engine: "given not yet in care"
    is not "given still alive". The scaling above is unconditional on survival,
    so anything that also models death must go through `calibrate_entry_scale`
    rather than use this rate directly — see `unconditional_entry_rate`, which
    is the same function under the name that says so.
    """
    risks = dict(lifetime_risk or DEFAULT_LIFETIME_RISK)
    if sex not in risks:
        raise LtcError("unknown sex %r; this module carries %s"
                       % (sex, ", ".join(sorted(risks))))
    return unconditional_entry_rate(age, risk=risks[sex], onset_age=onset_age,
                                    onset_spread=onset_spread)


def unconditional_entry_rate(age: float, *, risk: float,
                             onset_age: float = DEFAULT_ONSET_AGE,
                             onset_spread: float = DEFAULT_ONSET_SPREAD
                             ) -> float:
    """`entry_hazard` with the lifetime risk supplied directly rather than by sex.

    Named UNCONDITIONAL deliberately. It is scaled so that summing it over a
    late-life age range approaches the declared lifetime risk **for someone who
    is alive at every one of those ages** — which nobody is. Feeding it to a
    simulation that also kills people counts death twice, and the result is not
    a small bias: see `calibrate_entry_scale`.
    """
    if onset_spread <= 0:
        raise LtcError("onset_spread must be positive")
    import math
    # Logistic in age, scaled by lifetime risk. The 0.06 factor sets the peak
    # annual rate; it is a shape parameter, not a measured rate.
    shape = 1.0 / (1.0 + math.exp(-(age - onset_age) / onset_spread))
    return max(0.0, min(1.0, risk * shape * 0.06))


def couples_sequential(first: dict, second: dict) -> dict:
    """Two people's care, where the second person's begins after the first's.

    ROADMAP: "第二人护理时第一人成本已终止、房产可释放". The costs do not
    overlap, which a naive sum would double-count, and that matters because a
    couple's worst case is not both at once — it is one after the other, with
    the survivor paying alone and nobody left to provide care unpaid.
    """
    for episode in (first, second):
        if not isinstance(episode, dict) or "total_cost" not in episode:
            raise LtcError("each episode must carry a total_cost")
    return {
        "total_cost": first["total_cost"] + second["total_cost"],
        "overlapping": False,
        "basis": "sequential: the first episode has ended before the second "
                 "begins, so the costs are added rather than run concurrently. "
                 "A couple's worst case is one after the other, not both at "
                 "once — the survivor pays alone, with nobody left to provide "
                 "care unpaid.",
    }


# ---------------------------------------------------------------------------
# The engine seam. Everything above is the model; everything below is how a
# simulated life draws from it. Kept in this file so that LTC knowledge has one
# home and the engine only calls in.
# ---------------------------------------------------------------------------


@dataclass
class LtcParams:
    """One plan's long-term-care settings.

    `rng` is runtime, not configuration: an INDEPENDENT stream, set by the
    adapter, on the `LayoffParams.rng` precedent. It is the reason `mode = off`
    is bit-identical by construction rather than by care — off draws from
    nothing at all, so the shared stream cannot notice this module exists.
    """

    mode: str = OFF
    #: 0 => resolve from the plan's mortality sex. Left at 0 with `stochastic`
    #: selected, `sample_ltc_events` raises instead of picking a sex for you:
    #: silently defaulting to one would move every number in the run.
    lifetime_risk: float = 0.0
    onset_age: float = DEFAULT_ONSET_AGE
    onset_spread: float = DEFAULT_ONSET_SPREAD
    cost_home_care: float = DEFAULT_ANNUAL_COST[HOME_CARE]
    cost_assisted_living: float = DEFAULT_ANNUAL_COST[ASSISTED_LIVING]
    cost_nursing_home: float = DEFAULT_ANNUAL_COST[NURSING_HOME]
    mix_home_care: float = DEFAULT_LEVEL_MIX[HOME_CARE]
    mix_assisted_living: float = DEFAULT_LEVEL_MIX[ASSISTED_LIVING]
    mix_nursing_home: float = DEFAULT_LEVEL_MIX[NURSING_HOME]
    cost_excess_inflation: float = DEFAULT_COST_EXCESS_INFLATION
    scenario_years: float = 5.0
    scenario_onset_age: int = 83
    scenario_level: str = NURSING_HOME
    rng: object = None


def params_annual_cost(params: LtcParams) -> dict:
    return {HOME_CARE: float(params.cost_home_care),
            ASSISTED_LIVING: float(params.cost_assisted_living),
            NURSING_HOME: float(params.cost_nursing_home)}


def params_level_mix(params: LtcParams) -> dict:
    return {HOME_CARE: float(params.mix_home_care),
            ASSISTED_LIVING: float(params.mix_assisted_living),
            NURSING_HOME: float(params.mix_nursing_home)}


#: Bounded so a pathological config cannot grow it without limit. Keyed by the
#: whole calibration input, so it is a cache of a pure function and nothing
#: else — no run can observe another run's entry through it.
_SCALE_CACHE: dict = {}
_SCALE_CACHE_LIMIT = 64

#: The largest multiple of the unconditional rate the calibration will apply.
#: Reached only when the survival curve is so harsh that the declared lifetime
#: risk is unreachable, which `calibrate_entry_scale` reports rather than hides.
MAX_ENTRY_SCALE = 400.0

#: The age the declared lifetime risk is a risk *from*. The published figures
#: this module's defaults come from are stated as "of people turning 65", and
#: calibrating against that window rather than against the plan's own start age
#: keeps the care hazard a property of the mortality assumption alone — two
#: plans differing only in FIRE age get the same hazard, which they should.
CALIBRATION_REFERENCE_AGE = 65


def survival_rates_for(annual_death_rate, first_age: int, cap_age: int) -> list:
    """`[P(alive at first_age + i | alive at first_age + i - 1)]`.

    Index 0 is 1.0 on purpose: being alive at the reference age is the
    condition, not an event to be survived. Getting this off by one silently
    shifts the whole calibration by one year of mortality, which is why the
    engine and the tests both build the list here rather than each in their own
    loop.
    """
    return [1.0] + [1.0 - float(annual_death_rate(age))
                    for age in range(int(first_age) + 1, int(cap_age) + 1)]


def modelled_incidence(scale: float, survival_rates, *, risk: float,
                       first_age: int = CALIBRATION_REFERENCE_AGE,
                       onset_age: float = DEFAULT_ONSET_AGE,
                       onset_spread: float = DEFAULT_ONSET_SPREAD) -> float:
    """P(entering care at some point | alive at `first_age`), under a mortality.

    `survival_rates` is what `survival_rates_for` builds. The state machine is
    the honest one: each year you are alive and not yet in care, you may die or
    you may enter care.
    """
    alive_and_free = 1.0
    entered = 0.0
    for index, survives in enumerate(survival_rates):
        alive_and_free *= float(survives)
        rate = min(1.0, scale * unconditional_entry_rate(
            first_age + index, risk=risk, onset_age=onset_age,
            onset_spread=onset_spread))
        entered += alive_and_free * rate
        alive_and_free *= (1.0 - rate)
    return entered


def calibrate_entry_scale(survival_rates, *, risk: float,
                          first_age: int = CALIBRATION_REFERENCE_AGE,
                          onset_age: float = DEFAULT_ONSET_AGE,
                          onset_spread: float = DEFAULT_ONSET_SPREAD) -> dict:
    """Scale the entry rate so the SIMULATED lifetime incidence equals `risk`.

    Without this the module understates its own headline by a factor of four or
    five, and it does so in the direction that makes plans look safe. Measured
    on this engine's default male table, applying the unconditional rate only
    while alive produces a lifetime incidence of about 0.10 against a declared
    0.47 — because roughly seven in ten simulated men are dead before the
    logistic peaks at 83, and the declared figure already counted those deaths.
    A module built to stop a plan hiding its most expensive tail must not
    itself report that tail at a fifth of its size.

    `first_age` is the age the target is a risk FROM — `CALIBRATION_REFERENCE_AGE`,
    because that is how the published figures are stated. The scale it returns is
    then applied at every age, so a plan can still draw an entry before 65; what
    is pinned is "of those alive at 65, `risk` of them enter care eventually".

    Returns the scale together with what it achieved, because a survival curve
    can be harsh enough that no scale reaches the target; in that case the
    caller gets a number it can disclose rather than a silent shortfall.
    """
    if risk <= 0.0:
        raise LtcError("lifetime risk must be positive to calibrate against")
    key = (tuple(round(float(s), 12) for s in survival_rates), round(risk, 12),
           int(first_age), round(float(onset_age), 12),
           round(float(onset_spread), 12))
    hit = _SCALE_CACHE.get(key)
    if hit is not None:
        return dict(hit)

    def incidence(scale):
        return modelled_incidence(scale, survival_rates, risk=risk,
                                  first_age=first_age, onset_age=onset_age,
                                  onset_spread=onset_spread)

    lo, hi = 0.0, 1.0
    while hi < MAX_ENTRY_SCALE and incidence(hi) < risk:
        hi *= 2.0
    hi = min(hi, MAX_ENTRY_SCALE)
    reachable = incidence(hi) >= risk
    if reachable:
        for _ in range(80):
            mid = (lo + hi) / 2.0
            if incidence(mid) < risk:
                lo = mid
            else:
                hi = mid
        scale = (lo + hi) / 2.0
    else:
        scale = MAX_ENTRY_SCALE
    out = {"scale": scale, "achieved_incidence": incidence(scale),
           "target_incidence": float(risk), "saturated": not reachable}
    if len(_SCALE_CACHE) >= _SCALE_CACHE_LIMIT:
        _SCALE_CACHE.clear()
    _SCALE_CACHE[key] = dict(out)
    return out


def calibration_for(annual_death_rate, *, risk: float, cap_age: int = 110,
                    first_age: int = CALIBRATION_REFERENCE_AGE,
                    onset_age: float = DEFAULT_ONSET_AGE,
                    onset_spread: float = DEFAULT_ONSET_SPREAD) -> dict:
    """`calibrate_entry_scale` against a mortality table given as a callable.

    The engine has `annual_mortality_rate(age, params)` and this module has no
    business importing it; passing the rate in keeps the dependency pointing one
    way. A plan with mortality switched off passes a callable returning 0, and
    the calibration then targets the same incidence among people who never die,
    which is the self-consistent answer for that plan rather than a special case.
    """
    return calibrate_entry_scale(
        survival_rates_for(annual_death_rate, first_age, cap_age),
        risk=risk, first_age=int(first_age), onset_age=onset_age,
        onset_spread=onset_spread)


def expand_episode(onset_age: int, years: float, annual_cost: float, *,
                   last_age: int, excess_inflation: float,
                   anchor_age: int) -> list:
    """One episode as `[(age, amount_in_today's_money)]`, one row per care year.

    Public because `parents_model` needs exactly this: a parent's care and the
    plan-holder's own care are the same episode arithmetic applied to different
    people, and a second copy of it would be a second thing to keep true.

    Costs are emitted in today's money because that is what the engine's event
    channel consumes; the engine multiplies by each path's own realized CPI.
    `excess_inflation` compounds from `anchor_age` on top of that, which is what
    "care costs outrun medical inflation, which outruns general" means when the
    general part is already handled elsewhere.
    """
    events = []
    remaining = float(years)
    age = int(onset_age)
    while remaining > 1e-9 and age <= last_age:
        share = min(1.0, remaining)
        events.append((age, annual_cost * share
                       * (1.0 + excess_inflation) ** (age - anchor_age)))
        remaining -= share
        age += 1
    return events


def _note_truncation(meta: dict, events: list, years: float) -> None:
    """Say when the modelled horizon cut an episode short.

    Both modes, not just the stochastic one. A five-year episode starting at 86
    against a horizon ending at 89 costs three years, and the difference between
    "care was cheap" and "we stopped counting" is exactly the kind of thing that
    has to be on the record rather than inferable.
    """
    charged = sum(1 for _ in events)
    if charged and charged < years:
        meta["truncated_at_horizon"] = True
        meta["years_charged"] = charged


def _pick(rng, weights: dict):
    """Draw one key, in sorted order so the draw does not depend on dict order."""
    draw = float(rng.random())
    total = sum(weights.values())
    if total <= 0:
        raise LtcError("weights must sum to something positive")
    running = 0.0
    keys = sorted(weights)
    for key in keys:
        running += weights[key] / total
        if draw < running:
            return key
    return keys[-1]


def sample_ltc_events(params: Optional[LtcParams], first_age: int,
                      last_age: int, *, calibration=None,
                      anchor_age: Optional[int] = None):
    """Returns `(events, meta)` — `events` shaped exactly like eldercare's.

    Two returns rather than one because of the failure this project keeps
    paying for: a bare `[]` is the same object whether nobody needed care,
    nobody could have (the module is off), or the user's scenario age fell
    outside the modelled window. `meta` says which, so a zero can never be read
    as a measurement.

    OFF draws nothing — not a discarded draw, not a draw on another stream.
    That is what makes the default run bit-identical to one from before this
    module existed, by construction rather than by care.

    `calibration` is the dict from `calibrate_entry_scale`, computed once per
    run from the plan's mortality table. It is required rather than defaulted,
    for the reason the error message gives.
    """
    meta = {"mode": OFF, "entered": False, "onset_age": None, "years": None,
            "level": None, "entry_scale": None, "modelled_incidence": None,
            "reason": "the long-term-care module is off"}
    if params is None or params.mode == OFF:
        return [], meta

    if params.mode not in MODES:
        raise LtcError("unknown ltc mode %r; this module knows %s"
                       % (params.mode, ", ".join(MODES)))
    meta["mode"] = params.mode
    anchor = int(anchor_age if anchor_age is not None else first_age)
    costs = params_annual_cost(params)

    if params.mode == SCENARIO:
        onset = int(params.scenario_onset_age)
        episode = scenario_episode(float(params.scenario_years),
                                   level=params.scenario_level,
                                   annual_cost=costs)
        if not (first_age <= onset <= last_age):
            meta["reason"] = (
                "the scenario's onset age %d is outside the modelled window "
                "%d-%d, so no care was charged — the age was NOT moved to fit"
                % (onset, first_age, last_age))
            return [], meta
        events = expand_episode(
            onset, episode["years"], episode["annual_cost"],
            last_age=last_age, excess_inflation=params.cost_excess_inflation,
            anchor_age=anchor)
        meta.update({"entered": True, "onset_age": onset,
                     "years": episode["years"], "level": episode["level"],
                     "reason": "exactly the duration the user specified"})
        _note_truncation(meta, events, episode["years"])
        return events, meta

    # Stochastic.
    if params.rng is None:
        raise LtcError("stochastic long-term care needs its own rng; the "
                       "adapter sets an independent stream and leaving it "
                       "unset would silently model no care at all")
    if params.lifetime_risk <= 0.0:
        raise LtcError("stochastic long-term care needs a positive "
                       "lifetime_risk; the adapter resolves it from the plan's "
                       "mortality sex, and guessing one here would move every "
                       "number in the run")
    if calibration is None:
        raise LtcError("stochastic long-term care needs a calibration from "
                       "`calibrate_entry_scale`; without one the entry rate is "
                       "the unconditional one, which under this engine's own "
                       "mortality understates lifetime incidence about "
                       "fivefold — in the direction that makes plans look safe")
    ages = list(range(int(first_age), int(last_age) + 1))
    meta["entry_scale"] = calibration["scale"]
    meta["modelled_incidence"] = calibration["achieved_incidence"]
    if calibration["saturated"]:
        meta["saturated"] = True

    onset = None
    for age in ages:
        rate = min(1.0, calibration["scale"] * unconditional_entry_rate(
            age, risk=float(params.lifetime_risk), onset_age=params.onset_age,
            onset_spread=params.onset_spread))
        if float(params.rng.random()) < rate:
            onset = age
            break
    if onset is None:
        meta["reason"] = ("no care episode was drawn on this path (modelled "
                          "lifetime incidence %.3f)"
                          % calibration["achieved_incidence"])
        return [], meta

    buckets = {row["years"]: row["share"] for row in duration_distribution()}
    years = _pick(params.rng, buckets)
    level = _pick(params.rng, params_level_mix(params))
    events = expand_episode(
        onset, years, costs[level], last_age=last_age,
        excess_inflation=params.cost_excess_inflation, anchor_age=anchor)
    meta.update({"entered": True, "onset_age": onset, "years": years,
                 "level": level,
                 "reason": "drawn from the module's own hazard and duration "
                           "distribution"})
    _note_truncation(meta, events, years)
    return events, meta


#: What this module does NOT model, for the limitations list. Named here so the
#: disclosure and the code cannot drift apart.
NOT_MODELLED = (
    "Medicaid spend-down（资产耗尽后转入 Medicaid 的资格、look-back 期与州际差异）",
    "长期护理保险的保单条款（等待期、日限额、通胀附加）—— 保费与条款一律用户自填",
    "非正式照护（配偶或子女无偿提供）对成本的替代",
    "护理级别在一次照护期内的升级路径（本模块按进入时的级别定价整段）",
    "护理成本是在原有生活开销之上**叠加**的，不下调既有开销 —— 机构费用通常已含食宿，"
    "因此两者部分重叠，本模块偏保守；要反映这一点请直接调低年成本参数",
    "退休前进入护理（本模块只在退休段建模，与 eldercare 冲击同一条通道）",
    "夫妻序贯护理尚未接入引擎 —— `couples_sequential` 仍只是模型，"
    "有一条测试断言它没被接进去",
)
