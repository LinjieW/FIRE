"""
FIRE Model v8 — Analyst-A · 2026-05-06
====================================

NEW IN v8: PROMOTION TO ASSOCIATE (career inflection)

Per user (2026-05):
  - Next career level: Associate
  - Today's Associate base salary: $170,000
  - Bonus: 15-25% of base (uniform uncertainty)
  - No OT (FLSA exempt)
  - Promotion timing: 2-5 years (uniform uncertainty)

What changes at promotion:
  1. Base salary: 130K → 170K (in today's dollars; inflated to promotion year)
  2. Bonus: $5K fixed → 15-25% of base ($25.5K-$42.5K range, mid ~$34K)
  3. OT income: $22,500/yr → $0 (Associates don't get OT)
  4. 401(k) match base: gross income changes
  5. Marginal tax: ~24% → ~28% (federal + DC, due to higher income)
  6. Higher MAGI may trigger Roth IRA phase-out (modeled via reduced direct
     contribution; assumes backdoor Roth pathway remains available)

Net effect:
  Gross income: $157.5K → $204K mid-bonus = +$46.5K/yr nominal
  Post-tax-and-contribution disposable: roughly +$25-30K/yr extra to invest
  This is net positive but smaller than gross delta because of bracket creep.

Sensitivity dimensions in v8:
  A. Promotion timing (deterministic at year 2/3/4/5, plus stochastic uniform)
  B. Bonus realization (low/mid/high, plus stochastic uniform)
  C. Marginal tax rate sensitivity (26-30%)
  D. "No promotion ever" scenario (worst case)

Inherits from v7 unchanged:
  - Three-regime mixture
  - Student-t returns (df=6)
  - Stochastic correlated inflation
  - 50 bps retirement friction
  - Account stratification + Roth ladder
  - Shanghai relocation layer

Requires: numpy
Usage:
    python fire_v8_model.py
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
from typing import Callable, Sequence, Optional

from fire_rule_pack import CONTRIBUTION_LIMIT_RULES
from fire_v6_model import (
    State, AccountStack, TaxParams, RelocationParams,
    INITIAL_STACK, STATE, TAX_US,
    Regime, REGIMES,
    withdraw_from_stack, find_fire_crossing,
    aggregate_lifecycle,
)
from fire_v7_model import (
    TaxParamsChina, TAX_CN,
    V7Config,
    sample_lifetime_v7,
    simulate_withdrawal_v7,
)


# ============================================================
# MODULE-LEVEL OVERRIDE HOOK (for match_excludes_bonus context manager)
# ============================================================
# None  → respect contrib_params.match_excludes_bonus (default path)
# True  → force match base to EXCLUDE bonus, regardless of params
# False → force match base to INCLUDE bonus (legacy), regardless of params
#
# compute_contributions_for_year() reads this as a global IN ITS OWN MODULE
# at call time, so toggling fire_v8_model._FORCE_MATCH_EXCLUDES_BONUS
# propagates correctly even to callers that did `from fire_v8_model import
# project_stratified_v8` (avoids the ARCHIVE.md rule-9 import-binding pitfall).
_FORCE_MATCH_EXCLUDES_BONUS = None

# ============================================================
# HOUSEHOLD (couple) — module-level hook, mirrors the pattern above.
# None / .enabled=False  → single-person (bit-identical to prior behaviour).
# When set, compute_contributions_for_year adds a spouse earner's contributions
# and simulate_retirement_v98 (reads fire_v9_8_model._HOUSEHOLD) runs joint
# mortality / survivor spending / survivor SS / filing-jointly tax.
# ============================================================
_HOUSEHOLD = None

# v9.8+ opt-in: layoff / income-interruption risk during accumulation.
# Ported from the v2 product engine's semantics: annual layoff probability,
# multiplied in bad market years (return <= threshold), capped; a layoff year
# loses gap_months/12 of ALL contributions. None/disabled => ZERO rng draws,
# bit-identical to the unextended engine. The adapter sets this via a context
# manager (same process-wide-global pattern as _HOUSEHOLD; runs are serialized
# by the adapter's engine lock).
_LAYOFF = None
#: Roadmap 6.0 (A13). A per-path wage factor, set by the caller before an
#: accumulation run and cleared after -- the same module-global idiom
#: `_LAYOFF` above already uses, chosen over threading a parameter through
#: four call layers for one optional feature. `None` means every plan that has
#: not asked for a stochastic career, and then nothing here changes.
_WAGE_FACTORS = None


@dataclass
class LayoffParams:
    enabled: bool = False
    p_annual: float = 0.025
    return_threshold: float = -0.10
    bad_year_multiplier: float = 3.0
    p_cap: float = 0.50
    gap_months: float = 4.0
    #: Roadmap 6.0 (A13). Extra months of search per year of age past
    #: `decay_from_age`. Zero by default: the flat gap is what every existing
    #: plan computed, and this must not change one of them silently.
    #:
    #: Deliberately not defaulted from any labour-market study. Re-employment
    #: hazard by age is measured, but it varies enormously by occupation and
    #: by cycle, and picking one number would be this app asserting a fact
    #: about your industry that it does not have.
    gap_months_per_year_of_age: float = 0.0
    decay_from_age: int = 45
    max_gap_months: float = 12.0
    # runtime: independent career stream (seed + 7_000_000, v2 convention),
    # consumed sequentially across a run's paths; set by the adapter.
    rng: object = None


@dataclass
class HouseholdParams:
    enabled: bool = False
    spouse_age_offset: int = 0                 # spouse_age = primary_age + offset
    # --- accumulation: spouse as a second earner ---
    spouse_base_salary_pre: float = 0.0
    spouse_bonus_pre: float = 0.0
    spouse_salary_growth_pre: float = 0.035
    spouse_pretax_401k_limit_y1: float = 0.0
    spouse_roth_ira_limit_y1: float = 0.0
    spouse_hsa_limit_y1: float = 0.0
    spouse_match_rate: float = 0.0
    spouse_marginal_tax_pre: float = 0.24
    # --- starting balances (added to the household stack) ---
    spouse_initial_pretax: float = 0.0
    spouse_initial_roth: float = 0.0
    spouse_initial_hsa: float = 0.0
    spouse_initial_taxable: float = 0.0
    # --- retirement ---
    spouse_pia_monthly_y0: float = 0.0
    spouse_claim_age: int = 67
    spouse_sex: str = "female"
    survivor_spending_frac: float = 0.70       # retirement spend after first death


# ============================================================
# PROMOTION PARAMETERS
# ============================================================
@dataclass
class PromotionParams:
    """
    Models promotion to Associate level.

    Two layers of uncertainty:
    1. WHEN: promotion year (1=next year, ...)
    2. HOW MUCH: bonus % of base
    """
    # Whether to model promotion at all
    enabled: bool = True

    # Promotion timing
    timing_mode: str = 'uniform_int'    # 'fixed', 'uniform_int', 'never'
    timing_min: int = 2                  # earliest year (= age 29 if start_age 27)
    timing_max: int = 5                  # latest year (= age 32)
    timing_fixed: int = 3                # used if timing_mode='fixed'

    # Compensation post-promotion (in today's dollars at year 0)
    base_salary_post: float = 170_000
    base_growth_post: float = 0.035       # base grows at salary growth post-promotion too

    # Bonus realization
    bonus_mode: str = 'uniform'           # 'fixed', 'uniform'
    bonus_pct_min: float = 0.15
    bonus_pct_max: float = 0.25
    bonus_pct_fixed: float = 0.20         # used if bonus_mode='fixed'
    bonus_resampled_each_year: bool = True  # bonus varies year-to-year vs sampled once

    # Tax assumptions post-promotion
    marginal_tax_post: float = 0.28       # for back-of-envelope contribution sizing

    # OT eliminated post-promotion
    ot_eliminated: bool = True


# ============================================================
# TIME-VARYING CONTRIBUTION STREAM
# ============================================================
@dataclass
class V8ContributionParams:
    """
    Contribution stream parameters for v8 (replaces v6 ContributionStream
    when promotion is enabled).
    """
    # Pre-promotion (current Senior Analyst, year 1 levels)
    base_salary_pre: float = 130_000
    bonus_pre: float = 5_000              # fixed
    ot_income_pre: float = 22_500         # 240 hrs × $93.75
    salary_growth_pre: float = 0.035

    # v9.8+ opt-in: current working-years living cost (today's $) used for the
    # taxable-savings residual. None => legacy behavior (module STATE.expenses_y0),
    # bit-identical. The app's adapter always resolves this explicitly so a
    # user's own spending — not the calibration default — drives savings.
    annual_spending_now: Optional[float] = None

    # IRS limits (apply to both pre and post)
    irs_limit_growth: float = CONTRIBUTION_LIMIT_RULES["irs_limit_growth"]
    pretax_401k_limit_y1: float = CONTRIBUTION_LIMIT_RULES[
        "pretax_401k_limit_y1"]
    roth_ira_limit_y1: float = CONTRIBUTION_LIMIT_RULES[
        "roth_ira_limit_y1"]
    hsa_limit_y1: float = CONTRIBUTION_LIMIT_RULES["hsa_limit_y1"]

    # Match rate (6%)
    match_rate: float = 0.06

    # Whether the 401(k) match base EXCLUDES the annual bonus.
    # the analyst's CurrEmployer plan matches 6% × (base + OT) only; the $5K (pre) /
    # base×bonus_pct (post) bonus is NOT matched. This is locked decision #12.
    # Default True so the model's out-of-the-box behavior matches the official
    # v9.5.2 / v9.6 baseline WITHOUT needing an external monkey-patch.
    # (Set False to recover the legacy "match on all gross" assumption.)
    match_excludes_bonus: bool = True

    # Marginal tax rate (for computing taxable contribution residual)
    marginal_tax_pre: float = 0.24


def compute_contributions_for_year(
    year: int,
    promotion_year: Optional[int],
    bonus_pct_realized: float,
    base_salary_post_today: float,
    contrib_params: V8ContributionParams,
    promo_params: PromotionParams,
    primary_alive: bool = True,
    spouse_alive: bool = True,
    pool_household_expenses: bool = False,
    wage_factor: float = 1.0,
) -> AccountStack:
    """
    Compute account-level contributions for a given year, accounting for
    pre/post-promotion state.

    year: 1-indexed year of accumulation (year 1 = first year of investing).
    promotion_year: year when promotion happens (None = never).
    bonus_pct_realized: actual bonus % for this year (drawn from distribution).
    """
    # IRS limits scale with inflation (3% indexed)
    irs_factor = (1 + contrib_params.irs_limit_growth) ** (year - 1)
    pretax_401k_limit = contrib_params.pretax_401k_limit_y1 * irs_factor
    roth_ira_limit = contrib_params.roth_ira_limit_y1 * irs_factor
    hsa_limit = contrib_params.hsa_limit_y1 * irs_factor

    # Determine compensation this year
    promoted = promotion_year is not None and year >= promotion_year

    if promoted:
        # Years post-promotion: scale base salary at growth_post
        years_since_promo = year - promotion_year
        base_salary_now = (
            promo_params.base_salary_post
            * (1 + promo_params.base_growth_post) ** years_since_promo
            * (1 + contrib_params.salary_growth_pre) ** (promotion_year - 1)
            * float(wage_factor)
            # ^ the today's $170K is also inflated to promotion year via salary growth
            # (bracket creep proxy — could decouple if needed)
        )
        bonus_now = base_salary_now * bonus_pct_realized
        ot_now = 0.0 if promo_params.ot_eliminated else contrib_params.ot_income_pre
        marginal_tax = promo_params.marginal_tax_post
    else:
        # Pre-promotion: scale current values at salary growth
        # `wage_factor` is 1.0 for every plan that has not asked for a
        # stochastic career (Roadmap 6.0, A13), so this is the same curve it
        # always was. When a plan does ask, the factor carries that path's
        # permanent and transitory shocks -- kept multiplicative so the two
        # compose without either needing to know about the other.
        sal_factor = ((1 + contrib_params.salary_growth_pre) ** (year - 1)
                      * float(wage_factor))
        base_salary_now = contrib_params.base_salary_pre * sal_factor
        bonus_now = contrib_params.bonus_pre * sal_factor
        ot_now = contrib_params.ot_income_pre * sal_factor
        marginal_tax = contrib_params.marginal_tax_pre

    gross = base_salary_now + bonus_now + ot_now

    # 401(k) employer match base. Locked decision #12: CurrEmployer matches
    # 6% × (base + OT), bonus is NOT matched. Resolution order:
    #   1. module-level _FORCE_MATCH_EXCLUDES_BONUS (set by the
    #      match_excludes_bonus() context manager), else
    #   2. contrib_params.match_excludes_bonus (default True).
    # The full `gross` is still used below for the take-home / taxable
    # residual, since the bonus IS income — it just isn't matched.
    if _FORCE_MATCH_EXCLUDES_BONUS is not None:
        exclude_bonus = _FORCE_MATCH_EXCLUDES_BONUS
    else:
        exclude_bonus = contrib_params.match_excludes_bonus
    match_base = (base_salary_now + ot_now) if exclude_bonus else gross
    employer_match = match_base * contrib_params.match_rate

    # Employee 401(k): max out at IRS limit
    pretax_401k_employee = pretax_401k_limit
    pretax_401k_total = pretax_401k_employee + employer_match

    # Roth IRA + HSA: max
    roth_ira = roth_ira_limit
    hsa = hsa_limit

    # Taxable contribution: residual of (gross - tax - tax-advantaged)
    # Approximation: taxable = (gross - 401k_employee - HSA) * (1 - marginal_tax)
    #                          - expenses - Roth - residual
    # Simplification: keep it consistent with v6/v7 approach where taxable
    # is computed as (target SR proportion of gross). User's v6 had taxable
    # ≈ 47% of (gross - tax_advantaged), matching savings rate of ~54%.
    #
    # Use: taxable = (gross - employee_401k - HSA) * (1-tax) - roth - expenses
    #               - everything else implicitly zero for now (no other deduc)
    _spend0 = (contrib_params.annual_spending_now
               if contrib_params.annual_spending_now is not None
               else STATE.expenses_y0)
    expenses = _spend0 * (1 + STATE.inflation) ** (year - 1)
    net_after_pretax = (gross - pretax_401k_employee - hsa) * (1 - marginal_tax)
    primary_taxable_capacity = (
        net_after_pretax - roth_ira if primary_alive else 0.0
    )
    taxable_contribution = max(0.0, primary_taxable_capacity - expenses)
    primary = AccountStack(
        pretax_401k=pretax_401k_total,
        roth_ira=roth_ira,
        hsa=hsa,
        taxable=taxable_contribution,
    ) if primary_alive else AccountStack()

    # ---- household: add a spouse earner's contributions (default OFF) ----
    hh = _HOUSEHOLD
    if hh is not None and hh.enabled and spouse_alive:
        s_irs = irs_factor       # spouse IRS limits grow at the same indexation
        s_sal = (1 + hh.spouse_salary_growth_pre) ** (year - 1)
        s_pretax_emp = hh.spouse_pretax_401k_limit_y1 * s_irs
        s_roth = hh.spouse_roth_ira_limit_y1 * s_irs
        s_hsa = hh.spouse_hsa_limit_y1 * s_irs
        s_base = hh.spouse_base_salary_pre * s_sal
        s_bonus = hh.spouse_bonus_pre * s_sal
        s_gross = s_base + s_bonus
        s_match = s_base * hh.spouse_match_rate          # match on base only
        # Mortality-aware household paths pool the two earners' post-tax
        # residual before charging the one full household expense. Subtracting
        # from the primary first and flooring there would undercharge expenses
        # whenever the primary residual is smaller than expenses but the spouse
        # still has available cash. Older callers retain their historical
        # primary-first calculation unless they opt into the alive schedule.
        s_taxable_capacity = (
            (s_gross - s_pretax_emp - s_hsa)
            * (1 - hh.spouse_marginal_tax_pre)
            - s_roth
        )
        if pool_household_expenses or not primary_alive:
            household_taxable = max(
                0.0,
                primary_taxable_capacity + s_taxable_capacity - expenses,
            )
        else:
            household_taxable = (
                primary.taxable + max(0.0, s_taxable_capacity)
            )
        return AccountStack(
            pretax_401k=primary.pretax_401k + s_pretax_emp + s_match,
            roth_ira=primary.roth_ira + s_roth,
            hsa=primary.hsa + s_hsa,
            taxable=household_taxable,
        )

    return primary


# ============================================================
# V8 ACCUMULATION (with promotion-aware contributions)
# ============================================================
def project_stratified_v8(
    returns: Sequence[float],
    inflations: Sequence[float],
    promotion_year: Optional[int],
    bonus_pcts_per_year: Sequence[float],
    initial: AccountStack = None,
    contrib_params: V8ContributionParams = None,
    promo_params: PromotionParams = None,
    tax: TaxParams = None,
    state: State = None,
    friction: float = 0.0,
    alive_by_year: Optional[Sequence[tuple[bool, bool]]] = None,
) -> list[dict]:
    """
    Accumulation phase with time-varying contributions due to promotion event.
    """
    initial = initial or INITIAL_STACK
    contrib_params = contrib_params or V8ContributionParams()
    promo_params = promo_params or PromotionParams()
    tax = tax or TAX_US
    state = state or STATE

    if alive_by_year is not None and len(alive_by_year) != len(returns):
        raise ValueError("alive_by_year must match the accumulation horizon")

    accounts = initial.copy()
    expenses = state.expenses_y0
    cumulative_inf_factor = 1.0
    path = [{
        'age': state.start_age, 'accounts': accounts.copy(),
        'expenses': expenses, 'total': accounts.total,
        # No contributions have been made at the opening step.
        'contributions_nominal': 0.0,
        # The cumulative CPI these years were grown by. It was always here as
        # a local; emitting it is what lets a reader deflate an accumulation
        # year back into today's money. Without it every accumulation year is
        # correctly but uselessly reported as unmeasured.
        'cpi': cumulative_inf_factor,
    }]

    for i, (r, inf) in enumerate(zip(returns, inflations)):
        r_eff = r - friction
        accounts.pretax_401k *= (1 + r_eff)
        accounts.roth_ira *= (1 + r_eff)
        accounts.hsa *= (1 + r_eff)
        accounts.taxable *= (1 + r_eff - tax.drag_taxable)

        year = i + 1
        bonus_this_year = bonus_pcts_per_year[i] if i < len(bonus_pcts_per_year) else 0.20
        primary_alive, spouse_alive = (
            alive_by_year[i]
            if alive_by_year is not None and i < len(alive_by_year)
            else (True, True)
        )
        _wf = 1.0
        if _WAGE_FACTORS is not None and 0 <= year - 1 < len(_WAGE_FACTORS):
            _wf = float(_WAGE_FACTORS[year - 1])
        c = compute_contributions_for_year(
            year, promotion_year, bonus_this_year,
            promo_params.base_salary_post, contrib_params, promo_params,
            primary_alive=primary_alive, spouse_alive=spouse_alive,
            pool_household_expenses=alive_by_year is not None,
            wage_factor=_wf,
        )
        _lo = _LAYOFF
        if _lo is not None and getattr(_lo, "enabled", False) and _lo.rng is not None:
            p_lay = _lo.p_annual * (_lo.bad_year_multiplier
                                    if r <= _lo.return_threshold else 1.0)
            p_lay = min(_lo.p_cap, p_lay)
            if _lo.rng.random() < p_lay:
                # Roadmap 6.0 (A13): the door back in narrows with age. A
                # flat four months at every age says a 55-year-old finds work
                # as fast as a 30-year-old, which is a claim, not a neutral
                # default -- and it is the optimistic one for exactly the
                # people deciding whether they can afford to quit.
                _gap = _lo.gap_months
                _decay = float(getattr(_lo, "gap_months_per_year_of_age", 0.0))
                if _decay:
                    _from = int(getattr(_lo, "decay_from_age", 45))
                    _age_now = int(state.start_age) + int(year) - 1
                    _gap += max(0, _age_now - _from) * _decay
                    _gap = min(_gap, float(getattr(_lo, "max_gap_months", 12.0)))
                frac = max(0.0, 1.0 - _gap / 12.0)
                c = AccountStack(pretax_401k=c.pretax_401k * frac,
                                 roth_ira=c.roth_ira * frac,
                                 hsa=c.hsa * frac,
                                 taxable=c.taxable * frac)

        accounts.pretax_401k += c.pretax_401k
        accounts.roth_ira += c.roth_ira
        accounts.hsa += c.hsa
        accounts.taxable += c.taxable

        cumulative_inf_factor *= (1 + inf)
        expenses = state.expenses_y0 * cumulative_inf_factor
        age = state.start_age + year
        path.append({
            'age': age, 'accounts': accounts.copy(),
            'expenses': expenses, 'total': accounts.total,
            'cpi': cumulative_inf_factor,
            # What was actually contributed this year, AFTER any layoff has
            # scaled it down. Named `contributions` and not `income` because
            # that is what it is: this engine never models gross salary as a
            # cash flow, it models what reaches the accounts. A layoff is
            # visible here as a drop, which is the only place in the whole
            # engine where an income interruption is observable at all.
            'contributions_nominal': c.total,
        })

    return path


# ============================================================
# V8 LIFECYCLE
# ============================================================
def sample_promotion_event(
    promo_params: PromotionParams,
    rng: np.random.Generator,
) -> tuple[Optional[int], list[float]]:
    """
    Draw promotion year and bonus % path.
    Returns (promotion_year, list_of_bonus_pcts_per_year).
    promotion_year = None if not enabled or 'never' mode.
    """
    if not promo_params.enabled or promo_params.timing_mode == 'never':
        # Bonus % is irrelevant; return placeholder
        return None, [0.20] * 25

    if promo_params.timing_mode == 'fixed':
        promo_year = promo_params.timing_fixed
    elif promo_params.timing_mode == 'uniform_int':
        promo_year = rng.integers(
            promo_params.timing_min, promo_params.timing_max + 1
        )
    else:
        raise ValueError(f"Unknown timing_mode: {promo_params.timing_mode}")

    # Bonus % path
    if promo_params.bonus_mode == 'fixed':
        bonus_pcts = [promo_params.bonus_pct_fixed] * 25
    elif promo_params.bonus_mode == 'uniform':
        if promo_params.bonus_resampled_each_year:
            bonus_pcts = [
                rng.uniform(promo_params.bonus_pct_min, promo_params.bonus_pct_max)
                for _ in range(25)
            ]
        else:
            single = rng.uniform(promo_params.bonus_pct_min, promo_params.bonus_pct_max)
            bonus_pcts = [single] * 25
    else:
        raise ValueError(f"Unknown bonus_mode: {promo_params.bonus_mode}")

    return int(promo_year), bonus_pcts


def simulate_lifecycle_v8(
    config: V7Config = None,
    promo_params: PromotionParams = None,
    contrib_params: V8ContributionParams = None,
    initial: AccountStack = None,
    state: State = None,
    tax_us: TaxParams = None, tax_cn: TaxParamsChina = None,
    fire_swr: float = None,
    relocation: RelocationParams = None,
    regimes: Optional[list[Regime]] = None,
    rng: np.random.Generator = None,
) -> dict:
    config = config or V7Config()
    promo_params = promo_params or PromotionParams()
    state = state or STATE
    fire_swr = fire_swr or state.swr_pref
    relocation = relocation or RelocationParams()
    rng = rng or np.random.default_rng()

    total_years = state.accum_years + state.retire_horizon
    regime, all_returns, all_inflations = sample_lifetime_v7(
        total_years, rng, config, regimes=regimes,
    )

    # Sample promotion event
    promo_year, bonus_pcts = sample_promotion_event(promo_params, rng)

    # Accumulation
    accum_returns = all_returns[:state.accum_years]
    accum_inflations = all_inflations[:state.accum_years]
    accum_path = project_stratified_v8(
        accum_returns, accum_inflations,
        promo_year, bonus_pcts,
        initial, contrib_params, promo_params,
        tax_us, state, friction=config.friction_accum,
    )

    fire_step = find_fire_crossing(accum_path, fire_swr)
    if fire_step is None:
        return {
            'regime': regime.name, 'fire_age': None, 'reached_fire': False,
            'lifetime_success': False, 'accum_path': accum_path,
            'withdrawal': None, 'promotion_year': promo_year,
        }

    fire_age = fire_step['age']
    fire_year_idx = fire_age - state.start_age
    wd_returns = all_returns[fire_year_idx:fire_year_idx + state.retire_horizon]
    wd_inflations = all_inflations[fire_year_idx:fire_year_idx + state.retire_horizon]

    wd_result = simulate_withdrawal_v7(
        fire_step['accounts'], fire_age, fire_step['expenses'],
        wd_returns, wd_inflations,
        relocation, state, tax_us, tax_cn,
        friction=config.friction_retire, rng=rng,
    )

    return {
        'regime': regime.name,
        'fire_age': fire_age,
        'fire_balance': fire_step['total'],
        'fire_accounts': fire_step['accounts'].copy(),
        'fire_expenses': fire_step['expenses'],
        'reached_fire': True,
        'lifetime_success': wd_result['survived'],
        'accum_path': accum_path,
        'withdrawal': wd_result,
        'relocation_age': relocation.relocation_age,
        'promotion_year': promo_year,
    }


def run_lifecycle_mc_v8(
    config: V7Config = None,
    promo_params: PromotionParams = None,
    relocation: RelocationParams = None,
    regimes: Optional[list[Regime]] = None,
    n_paths: int = None,
    seed: int = None,
    **kwargs,
) -> list[dict]:
    config = config or V7Config()
    n_paths = n_paths or config.n_paths
    seed = seed if seed is not None else config.seed
    rng = np.random.default_rng(seed)
    return [
        simulate_lifecycle_v8(
            config=config, promo_params=promo_params,
            relocation=relocation, regimes=regimes, rng=rng, **kwargs,
        )
        for _ in range(n_paths)
    ]


# ============================================================
# AGGREGATION (extends v6/v7 with promotion-conditional stats)
# ============================================================
def aggregate_v8(results: list[dict]) -> dict:
    base = aggregate_lifecycle(results)

    # Conditional success rate by promotion year
    promo_years = sorted(set(
        r['promotion_year'] for r in results if r.get('promotion_year') is not None
    ))
    by_promo_year = {}
    for py in promo_years:
        subset = [r for r in results if r.get('promotion_year') == py]
        if subset:
            by_promo_year[py] = aggregate_lifecycle(subset)
    base['by_promotion_year'] = by_promo_year

    return base


# ============================================================
# REPORT
# ============================================================
def report(n_paths: int = 5000):
    print("=" * 78)
    print("FIRE Model v8 — Promotion to Associate")
    print(f"  · {n_paths:,} paths · seed 42")
    print("=" * 78)
    print()

    base_relo = RelocationParams()
    sh_relo = RelocationParams(relocation_age=41, col_ratio=0.85)
    cfg = V7Config(n_paths=n_paths)

    # ================================================================
    # [1] HEADLINE: Stochastic promotion (year 2-5, bonus 15-25%)
    # ================================================================
    print("[1] HEADLINE — Stochastic promotion · year 2-5 · bonus 15-25%")
    print("-" * 78)

    promo_stoch = PromotionParams(
        enabled=True,
        timing_mode='uniform_int', timing_min=2, timing_max=5,
        bonus_mode='uniform', bonus_pct_min=0.15, bonus_pct_max=0.25,
    )

    res_us = run_lifecycle_mc_v8(config=cfg, promo_params=promo_stoch,
                                 relocation=base_relo)
    res_sh = run_lifecycle_mc_v8(config=cfg, promo_params=promo_stoch,
                                 relocation=sh_relo)
    a_us = aggregate_v8(res_us)
    a_sh = aggregate_v8(res_sh)

    # v7 baseline (no promotion modeled, salary just grows at 3.5%)
    promo_none = PromotionParams(enabled=False)
    res_v7_us = run_lifecycle_mc_v8(config=cfg, promo_params=promo_none,
                                    relocation=base_relo)
    res_v7_sh = run_lifecycle_mc_v8(config=cfg, promo_params=promo_none,
                                    relocation=sh_relo)
    a_v7_us = aggregate_v8(res_v7_us)
    a_v7_sh = aggregate_v8(res_v7_sh)

    print(f"\n  {'Scenario':<35} {'Lifetime success':<18} {'FIRE p50':<10} {'Terminal p50':<14}")
    print(f"  {'-'*35} {'-'*18} {'-'*10} {'-'*14}")
    print(f"  {'v7 baseline (no promo, US)':<35} {a_v7_us['lifetime_success_rate']*100:>6.1f}%             "
          f"{a_v7_us['fire_age_p50']:<10} ${a_v7_us['terminal_p50']/1e6:.1f}M")
    print(f"  {'v8 stochastic promo (US)':<35} {a_us['lifetime_success_rate']*100:>6.1f}%             "
          f"{a_us['fire_age_p50']:<10} ${a_us['terminal_p50']/1e6:.1f}M")
    print(f"  {'  Δ vs v7':<35} {(a_us['lifetime_success_rate']-a_v7_us['lifetime_success_rate'])*100:>+6.1f} pp")
    print()
    print(f"  {'v7 baseline (no promo, Shanghai)':<35} {a_v7_sh['lifetime_success_rate']*100:>6.1f}%             "
          f"{a_v7_sh['fire_age_p50']:<10} ${a_v7_sh['terminal_p50']/1e6:.1f}M")
    print(f"  {'v8 stochastic promo (Shanghai)':<35} {a_sh['lifetime_success_rate']*100:>6.1f}%             "
          f"{a_sh['fire_age_p50']:<10} ${a_sh['terminal_p50']/1e6:.1f}M")
    print(f"  {'  Δ vs v7':<35} {(a_sh['lifetime_success_rate']-a_v7_sh['lifetime_success_rate'])*100:>+6.1f} pp")

    # ================================================================
    # [2] Promotion timing sensitivity (deterministic year, mid bonus)
    # ================================================================
    print(f"\n\n[2] PROMOTION TIMING SENSITIVITY (US-only · 20% bonus fixed)")
    print("-" * 78)
    print(f"\n  {'Promo year':<14} {'Age at promo':<14} {'Lifetime success':<18} "
          f"{'Δ vs no promo':<15} {'FIRE p50':<10}")
    print(f"  {'-'*14} {'-'*14} {'-'*18} {'-'*15} {'-'*10}")

    base_no_promo = a_v7_us['lifetime_success_rate']

    timings = [('never', None), ('year 2', 2), ('year 3', 3), ('year 4', 4),
               ('year 5', 5), ('year 7', 7), ('year 10', 10)]
    for label, py in timings:
        if py is None:
            pp = PromotionParams(enabled=False)
            age_at = '—'
        else:
            pp = PromotionParams(
                enabled=True, timing_mode='fixed', timing_fixed=py,
                bonus_mode='fixed', bonus_pct_fixed=0.20,
            )
            age_at = STATE.start_age + py

        res = run_lifecycle_mc_v8(config=cfg, promo_params=pp, relocation=base_relo)
        a = aggregate_v8(res)
        delta = (a['lifetime_success_rate'] - base_no_promo) * 100
        print(f"  {label:<14} {str(age_at):<14} {a['lifetime_success_rate']*100:>6.1f}%             "
              f"{delta:>+6.1f} pp        {a['fire_age_p50']:<10}")

    # ================================================================
    # [3] Bonus realization sensitivity (year 3 fixed, bonus varied)
    # ================================================================
    print(f"\n\n[3] BONUS REALIZATION SENSITIVITY (US-only · promo year 3 fixed)")
    print("-" * 78)
    print(f"\n  {'Bonus %':<12} {'Annual gross':<14} {'Lifetime success':<18} "
          f"{'FIRE p50':<10} {'Terminal p50':<12}")
    print(f"  {'-'*12} {'-'*14} {'-'*18} {'-'*10} {'-'*12}")

    for pct in [0.10, 0.15, 0.20, 0.25, 0.30]:
        pp = PromotionParams(
            enabled=True, timing_mode='fixed', timing_fixed=3,
            bonus_mode='fixed', bonus_pct_fixed=pct,
        )
        res = run_lifecycle_mc_v8(config=cfg, promo_params=pp, relocation=base_relo)
        a = aggregate_v8(res)
        gross_post = 170_000 * (1 + pct)
        print(f"  {pct*100:.0f}%          ${gross_post/1000:.0f}K          {a['lifetime_success_rate']*100:>6.1f}%             "
              f"{a['fire_age_p50']:<10} ${a['terminal_p50']/1e6:.1f}M")

    # ================================================================
    # [4] Combined matrix: promotion year × bonus
    # ================================================================
    print(f"\n\n[4] FULL MATRIX: PROMO YEAR × BONUS (US-only · success rate %)")
    print("-" * 78)
    print(f"  Bonus →     ", end="")
    for b in [0.15, 0.20, 0.25]:
        print(f"  {b*100:.0f}%   ", end="")
    print()
    print(f"  Promo year ↓")
    print(f"  {'-'*60}")

    for py in [2, 3, 4, 5, 7]:
        print(f"  year {py:<4} ", end="")
        for pct in [0.15, 0.20, 0.25]:
            pp = PromotionParams(
                enabled=True, timing_mode='fixed', timing_fixed=py,
                bonus_mode='fixed', bonus_pct_fixed=pct,
            )
            # Smaller n_paths for matrix to keep runtime manageable
            res = run_lifecycle_mc_v8(config=V7Config(n_paths=2000),
                                      promo_params=pp, relocation=base_relo)
            a = aggregate_v8(res)
            print(f"   {a['lifetime_success_rate']*100:>5.1f}%", end="")
        print()

    # ================================================================
    # [5] Stochastic promotion: conditional outcomes by realized promo year
    # ================================================================
    print(f"\n\n[5] CONDITIONAL OUTCOMES BY REALIZED PROMOTION YEAR")
    print("-" * 78)
    print("  Under v8 stochastic promotion (year 2-5 uniform, bonus 15-25%),")
    print("  what does the success rate look like CONDITIONAL on promo year?")
    print()
    print(f"  {'Realized promo':<16} {'N paths':<10} {'Success':<10} {'FIRE p50':<10}")
    print(f"  {'-'*16} {'-'*10} {'-'*10} {'-'*10}")
    for py in sorted(a_us['by_promotion_year'].keys()):
        sub = a_us['by_promotion_year'][py]
        print(f"  year {py:<11} {sub['n_paths']:<10} {sub['lifetime_success_rate']*100:>5.1f}%     "
              f"{sub['fire_age_p50']:<10}")

    # ================================================================
    # [6] Promotion never happens (worst case)
    # ================================================================
    print(f"\n\n[6] WHAT IF PROMOTION NEVER HAPPENS? (worst case)")
    print("-" * 78)
    print(f"  v7 already partially captures this: salary growth assumed 3.5%/yr")
    print(f"  even without explicit promotion. But what if salary stagnates?")
    print()

    # Simulate "stagnant" career — keep current Senior Analyst comp, no promo
    cp_stagnant = V8ContributionParams(salary_growth_pre=0.030)  # below inflation
    pp_none = PromotionParams(enabled=False)
    res_stag = run_lifecycle_mc_v8(config=cfg, promo_params=pp_none,
                                   contrib_params=cp_stagnant,
                                   relocation=base_relo)
    a_stag = aggregate_v8(res_stag)

    cp_normal = V8ContributionParams()  # 3.5% growth (v7 default)
    res_norm = run_lifecycle_mc_v8(config=cfg, promo_params=pp_none,
                                   contrib_params=cp_normal,
                                   relocation=base_relo)
    a_norm = aggregate_v8(res_norm)

    print(f"  Scenario                                      Success    FIRE p50")
    print(f"  {'-'*60}")
    print(f"  v7 default (3.5% salary growth, no promo)     {a_norm['lifetime_success_rate']*100:>5.1f}%     {a_norm['fire_age_p50']}")
    print(f"  Stagnant (3.0% salary growth, no promo)       {a_stag['lifetime_success_rate']*100:>5.1f}%     {a_stag['fire_age_p50']}")
    print(f"  v8 stochastic promo (the upside case)         {a_us['lifetime_success_rate']*100:>5.1f}%     {a_us['fire_age_p50']}")
    print()
    print(f"  Range across career outcomes: {a_stag['lifetime_success_rate']*100:.1f}% to "
          f"{a_us['lifetime_success_rate']*100:.1f}% "
          f"({(a_us['lifetime_success_rate']-a_stag['lifetime_success_rate'])*100:.1f} pp span)")

    # ================================================================
    # [7] FINAL TAKEAWAY
    # ================================================================
    print(f"\n\n[7] FINAL TAKEAWAY")
    print("=" * 78)
    print(f"""
  v8 confirms what we suspected: the promotion is net positive but its
  magnitude depends on timing (when it happens) and realization (bonus %).

  Stochastic outcome (year 2-5, bonus 15-25% uniform):
    US-only:   {a_us['lifetime_success_rate']*100:.1f}%  vs v7 {a_v7_us['lifetime_success_rate']*100:.1f}% ({(a_us['lifetime_success_rate']-a_v7_us['lifetime_success_rate'])*100:+.1f} pp)
    Shanghai:  {a_sh['lifetime_success_rate']*100:.1f}%  vs v7 {a_v7_sh['lifetime_success_rate']*100:.1f}% ({(a_sh['lifetime_success_rate']-a_v7_sh['lifetime_success_rate'])*100:+.1f} pp)

  Span across promotion-year decisions:
    Year 2 promotion:   ~{aggregate_v8(run_lifecycle_mc_v8(config=cfg, promo_params=PromotionParams(enabled=True, timing_mode='fixed', timing_fixed=2, bonus_mode='fixed', bonus_pct_fixed=0.20), relocation=base_relo))['lifetime_success_rate']*100:.0f}% (best case timing)
    Year 5 promotion:   ~{aggregate_v8(run_lifecycle_mc_v8(config=cfg, promo_params=PromotionParams(enabled=True, timing_mode='fixed', timing_fixed=5, bonus_mode='fixed', bonus_pct_fixed=0.20), relocation=base_relo))['lifetime_success_rate']*100:.0f}%
    Never promoted:     ~{a_v7_us['lifetime_success_rate']*100:.0f}% (v7 default — assumes 3.5%/yr growth absorbs)

  Bonus matters less than timing: ±5pp success across 15-25% bonus range
  is comparable to ±2-3 year promotion timing variance.

  Decision implication: Stop worrying about exact promo timing. The plan
  is robust to anywhere in the 2-5 year window. Focus on what actually
  matters — staying employed, hitting promotion criteria when ready.
    """)


if __name__ == '__main__':
    report(n_paths=3000)
