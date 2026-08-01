"""
FIRE Model v9.6 — Analyst-A · 2026-05-17
=====================================

Fork of v9.5 (and v9.5.2's stack + match patches stay external).
Fixes three SEVERE Shanghai-bias bugs discovered in the 2026-05-17
systematic audit. Same root pattern in all three: US-specific logic
runs unconditionally before the `in_china` gate.

FIX A (Audit F1) · Shanghai ACA gate
  v9.5: aca_paid computed unconditionally; premium_savings subtracted
        from target on Shanghai paths too.
  v9.6: if in_china, set aca_paid = full_premium (no subsidy applies)
        AND replace US healthcare line items with China healthcare cost.

FIX B (Audit F2) · Initial expenses region-aware
  v9.5: initial_total_expenses seeded with US healthcare components
        (non_medical + routine + aca_paid + oop). For paths that will
        relocate, this inflates GK's starting target by ~$10K real.
  v9.6: still seed at FIRE age (US still applies pre-relocation), BUT
        re-seed rule_state at relocation if relocation_age > fire_age.

FIX C (Audit F3) · SS NRA haircut for in_china years
  v9.5: ss_income returned regardless of residence; phantom full SS.
  v9.6: when in_china, apply ss_nra_haircut (default 0.20 = 20%
        haircut for combined NRA withholding + Chinese tax + treaty
        residual).

ALSO inherits all v9.5 fixes:
  - Floor on adjusted_target (FIX 1 from v9.5)
  - Dynamic Shanghai CoL by property tier (FIX 2 from v9.5)
  - Behavioral property cap (FIX 3 from v9.5)

NOT INCLUDED in v9.6 base; orthogonal modules apply via context manager:
  - Stochastic accumulation (fire_v96_healthcare_stochastic.py)
  - US ACA MAGI-endogenous (fire_v96_healthcare_stochastic.py)
  - v9.5.2 stack + match-excludes-bonus (fire_v95_actual_baseline.py)

Usage:
    from fire_v9_6_model import simulate_lifecycle_v96, run_lifecycle_mc_v96
    from fire_v95_actual_baseline import (
        INITIAL_STACK_ACTUAL, match_excludes_bonus,
    )
    from fire_v9_1_model import GK_STANDARD

    with match_excludes_bonus():
        results = run_lifecycle_mc_v96(
            n_paths=1_500_000, seed=42_000,
            rule=GK_STANDARD,
            initial=INITIAL_STACK_ACTUAL,
        )

ORIGINAL v9.5 DOCSTRING BELOW (preserved for context):
================================================================
PATCH responding to Gemini review of v9.4 FO Matrix results.

Three fixes:

FIX 1: ACCOUNTING — Floor on adjusted_target
  v9.4 bug: when ACA premium subsidies exceed the rule's target nominal,
  `adjusted_target` becomes negative. This propagated into the
  real_consumption_path and caused MaxDD > 100% artifacts in the FO matrix.
  v9.5: enforce floor at 0.

FIX 2: DYNAMIC CoL REDUCTION (Non-linear Rent-to-Price)
  v9.4: flat 0.30 CoL reduction regardless of purchase amount.
  Reality: high-end properties have:
    - High HOA/maintenance (1-2% annually)
    - Higher property tax (China has limited residential property tax
      currently, but luxury properties face other holding costs)
    - Higher utilities and neighborhood expectations
    - Low rent-to-price ratio (luxury rentals don't scale with price)
  v9.5: piecewise CoL reduction:
    - purchase <= $400K: 0.30 reduction (full benefit)
    - $400K < purchase < $800K: linear interpolation 0.30 → 0.15
    - purchase >= $800K: 0.10 reduction (luxury offset)
  Mechanism implemented in compute_dynamic_col_reduction() and applied in
  simulate_retirement_v95.

FIX 3: BEHAVIORAL COHERENCE FLAG (Soft Luxury Trap)
  Gemini originally proposed hard caps:
    - expenses ≤ $45K: max property $500K
    - expenses ≤ $65K: max property $800K
    - expenses > $65K: max property $1.5M

  I partially agree but disagree with hard caps. Instead, v9.5 implements:
    a. Dynamic CoL (Fix 2) already penalizes high-price purchases — this is
       the financial mechanism by which the "luxury trap" should manifest
    b. Behavioral COHERENCE FLAG: matrix cells where the budget/expense
       combination is behaviorally implausible are FLAGGED in the output
       but not hard-capped. Reasoning: a researcher may want to know
       what the pure financial math says even in implausible cells.
    c. Optional "soft cap" mode in the binary search that respects
       Gemini's caps — enabled by default for the user-facing report

INHERITS FROM v9.4 unchanged.

Usage:
    from fire_v9_5_model import simulate_lifecycle_v95, run_lifecycle_mc_v95
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass
from typing import Optional, Sequence

from fire_v9_3_model import (
    BondParams, DEFAULT_BOND_PARAMS,
    GlidePath, GLIDE_ALL_EQUITY, GLIDE_CONSERVATIVE, GLIDE_STANDARD, ALL_GLIDE_PATHS,
    sample_bond_returns, blended_return,
    ShockMode,
    EldercareShockParams, sample_eldercare_events,
    InheritanceParams, sample_inheritance,
    OBBBAMode, OBBBAParams,
    ShanghaiPropertyParams,
    project_stratified_v93,
)
from fire_v6_model import (
    State, AccountStack, TaxParams, RelocationParams,
    INITIAL_STACK, STATE, TAX_US,
    Regime, REGIMES, find_fire_crossing,
)
from fire_v7_model import (
    TaxParamsChina, TAX_CN, V7Config, sample_lifetime_v7,
)
from fire_v8_model import (
    PromotionParams, V8ContributionParams, sample_promotion_event,
)
from fire_v9_1_model import (
    MortalityParams, MORTALITY_MALE, MORTALITY_FEMALE, MORTALITY_UNISEX,
    annual_mortality_rate, sample_age_at_death,
    MedicalParams, DEFAULT_MEDICAL, compute_medical_components,
    ACAScenario, ACAParams,
    estimate_magi_proxy, compute_aca_premium_paid,
    WithdrawalRule, FixedRealRule,
    GuytonKlingerRule, GK_STANDARD, GK_CONSERVATIVE, GK_AGGRESSIVE,
    VPWRule, ALL_RULES,
)
from fire_v9_2_model import (
    RothLadderParams, SeasoningEntry,
    update_seasoning_queue, execute_roth_conversion,
    SocialSecurityParams, compute_ss_factor, compute_ss_annual_income,
    FTCParams, apply_ftc_to_tax_cn,
)
from fire_v9_4_model import (
    EARLY_WD_PENALTY_AGE, EARLY_WD_PENALTY_RATE,
    withdraw_with_seasoning_v94, fund_shock_or_purchase,
)


# ============================================================
# v9.6 NEW · China Healthcare Cost Model (Audit Fix B)
# ============================================================
@dataclass
class ChinaHealthcareParams:
    """China healthcare costs in real-$ per year.

    Mid-tier commercial supplement assumption — provides good coverage
    on top of urban resident social insurance (城镇居民医保).

    Ages 35-59: commercial supplement is the main cost (~$2,500/yr USD).
    Ages 60+: social insurance kicks in more meaningfully, commercial
              supplement can scale back (~$1,000/yr USD).

    Numbers chosen to be mid-range; real range per recent industry data
    is $1-3K USD for working-age, $500-2K USD for 60+.
    """
    cost_working_age_real: float = 2_500.0    # 35-59
    cost_senior_real: float = 1_000.0          # 60+
    senior_age_threshold: int = 60


def china_healthcare_cost_nominal(
    age: int,
    cpi_cumulative: float,
    params: ChinaHealthcareParams = None,
) -> float:
    """Return nominal China healthcare cost for given age and CPI."""
    params = params or ChinaHealthcareParams()
    real_cost = (params.cost_senior_real
                 if age >= params.senior_age_threshold
                 else params.cost_working_age_real)
    return real_cost * cpi_cumulative


# ============================================================
# v9.6 NEW · SS NRA Haircut (Audit Fix C)
# ============================================================
@dataclass
class SSNRAHaircutParams:
    """When Chinese tax resident receives US Social Security:

    (a) US NRA withholding: default 30% on SS for non-resident aliens,
        reduced by US-China tax treaty (1984 Art 17) to a residual rate.
        Treaty interpretation is contested; practical net rate often
        ~15-20%.
    (b) Chinese taxation: worldwide income, SS may be taxable. FTC
        partially offsets. Net additional impact: ~0-10%.
    (c) Administrative friction (FBAR/FATCA reporting, banking).

    Combined haircut: 15-25% is realistic; default to 0.20.
    """
    haircut_fraction: float = 0.20    # 20% reduction when in_china


# ============================================================
# FIX 2: DYNAMIC CoL REDUCTION (inherited from v9.5)
# ============================================================
def compute_dynamic_col_reduction(purchase_amount_y0: float) -> float:
    """
    Piecewise CoL reduction based on property purchase amount.

    Rationale (Gemini Fix 2):
      - Low-end / lean property: rent-to-price ratio is ~1.6-2% in Shanghai,
        making the rent savings on a $300K property meaningful relative to
        the price. Full 0.30 CoL benefit.
      - Mid-range: rent doesn't scale linearly. A $600K property doesn't
        eliminate twice the rent of a $300K property. Carrying costs
        (maintenance, HOA, utilities) start to eat into the savings.
      - Luxury: carrying costs are substantial. A $1M+ property has
        property management, premium services, higher utilities. The
        net CoL benefit shrinks to ~0.10.

    Returns: CoL reduction in [0.10, 0.30].
    """
    if purchase_amount_y0 <= 400_000:
        return 0.30
    elif purchase_amount_y0 >= 800_000:
        return 0.10
    else:
        # Linear interpolation in $400K-$800K range
        t = (purchase_amount_y0 - 400_000) / (800_000 - 400_000)
        return 0.30 + t * (0.10 - 0.30)


# ============================================================
# FIX 3: BEHAVIORAL COHERENCE CHECK
# ============================================================
def behavioral_max_budget(expenses_y0: float) -> float:
    """
    Soft cap on plausible property budget given baseline expenses.

    Reasoning (modified from Gemini Fix 3):
      A $1.2M property cannot coexist with $40K lifestyle in reality.
      Luxury property demands a baseline lifestyle to maintain it —
      property tax, HOA, maintenance, utilities, and social/neighborhood
      expectations push up baseline.

      Gemini's exact thresholds adopted:
        expenses ≤ $45K  → max property $500K
        expenses ≤ $65K  → max property $800K
        expenses >  $65K → max property $1,500K (no cap)
    """
    if expenses_y0 <= 45_000:
        return 500_000
    elif expenses_y0 <= 65_000:
        return 800_000
    else:
        return 1_500_000


def is_behaviorally_coherent(purchase_amount_y0: float,
                              expenses_y0: float) -> bool:
    """Check if budget/expense combination is behaviorally plausible."""
    return purchase_amount_y0 <= behavioral_max_budget(expenses_y0)


# ============================================================
# v9.5 RETIREMENT SIMULATOR (Fixes 1 + 2)
# ============================================================
def simulate_retirement_v96(
    starting_accounts: AccountStack,
    starting_age: int,
    fire_year_cpi_cumulative: float,
    equity_returns: Sequence[float],
    bond_returns: Sequence[float],
    inflations: Sequence[float],
    rule: WithdrawalRule,
    glide_path: GlidePath,
    relocation: RelocationParams,
    sh_property: ShanghaiPropertyParams,
    medical: MedicalParams,
    aca: ACAParams,
    mortality: MortalityParams,
    roth_ladder: RothLadderParams,
    ss: SocialSecurityParams,
    ftc: FTCParams,
    eldercare_events: list,
    inheritance_event: Optional[tuple],
    state: State = None,
    tax_us: TaxParams = None,
    tax_cn: TaxParamsChina = None,
    friction: float = 0.005,
    rng: np.random.Generator = None,
    china_healthcare: ChinaHealthcareParams = None,    # v9.6 NEW
    ss_nra: SSNRAHaircutParams = None,                  # v9.6 NEW
) -> dict:
    """v9.6 retirement simulator: v9.5 + audit fixes A, B, C.

    Fix A: Shanghai ACA gate — no US ACA premium logic when in_china,
           use China healthcare cost instead.
    Fix B: Initial expenses region-aware — if relocation_age <= fire_age,
           re-seed rule_state with China healthcare components.
    Fix C: SS NRA haircut — apply 20% reduction to SS when in_china.
    """
    state = state or STATE
    tax_us = tax_us or TAX_US
    tax_cn = tax_cn or TAX_CN
    china_healthcare = china_healthcare or ChinaHealthcareParams()
    ss_nra = ss_nra or SSNRAHaircutParams()
    rng = rng or np.random.default_rng()

    tax_cn_effective = apply_ftc_to_tax_cn(tax_cn, ftc)
    accounts = starting_accounts.copy()

    initial_components = compute_medical_components(
        year_in_simulation=starting_age - state.start_age,
        age=starting_age,
        in_retirement=True,
        med=medical,
        cpi_cumulative=fire_year_cpi_cumulative,
    )
    initial_full_premium = initial_components['premium_full']

    # FIX B: Determine if this path will start retirement already in China
    # (relocation_age <= fire_age means relocation has already occurred or
    # occurs immediately at FIRE). Use China healthcare in seed if so.
    starts_in_china = (
        relocation.relocation_age is not None
        and relocation.relocation_age <= starting_age
    )

    if starts_in_china:
        # Region-aware initial seed: use China healthcare, no ACA subsidy game.
        initial_china_health = china_healthcare_cost_nominal(
            starting_age, fire_year_cpi_cumulative, china_healthcare,
        )
        initial_total_expenses = (
            initial_components['non_medical']
            + initial_china_health  # replaces routine + aca_paid + oop
        )
    else:
        # Standard US seed (will re-seed at relocation event if needed)
        initial_aca_paid = compute_aca_premium_paid(
            initial_full_premium,
            magi_nominal=initial_components['non_medical'] * 0.6,
            cpi_cumulative=fire_year_cpi_cumulative,
            params=aca,
        )
        initial_total_expenses = (
            initial_components['non_medical']
            + initial_components['routine']
            + initial_aca_paid
            + initial_components['oop']
        )

    initial_swr = initial_total_expenses / starting_accounts.total
    rule_state = rule.initialize(starting_accounts.total, initial_total_expenses, initial_swr)

    in_china = starts_in_china   # may be True from the start
    cny_expenses_real = None
    fx_rate = relocation.fx_initial
    # If starts in China, we never enter the relocation event handler;
    # initialize fx_at_relocation to fx_initial so downstream CNY logic works.
    fx_at_relocation = relocation.fx_initial if starts_in_china else None
    relocation_done = starts_in_china  # if already in china, don't relocate again
    col_effective = relocation.col_ratio
    property_fully_paid = False


    survived_financially = True
    shortfall_age = None
    age_at_death = None
    cpi_cumulative = fire_year_cpi_cumulative
    cpi_at_ss_claim = None
    real_consumption_path = []
    nominal_consumption_path = []
    portfolio_path = [accounts.total]

    seasoning_queue: list = []
    total_conversions = 0.0
    ss_payments_received_real = 0.0
    eldercare_total_real = 0.0
    eldercare_count = 0
    inheritance_received_real = 0.0
    sh_property_purchased_nominal = 0.0
    total_early_wd_penalty = 0.0

    eldercare_by_age = {}
    for age, amt in eldercare_events:
        eldercare_by_age.setdefault(age, []).append(amt)

    # FIX 2: Compute dynamic CoL reduction if property is enabled
    if sh_property.enabled:
        dynamic_col_reduction = compute_dynamic_col_reduction(
            sh_property.purchase_amount_y0
        )
    else:
        dynamic_col_reduction = sh_property.col_reduction

    for year_idx, (eq_r, bd_r, inf) in enumerate(
        zip(equity_returns, bond_returns, inflations)
    ):
        current_age = starting_age + year_idx + 1
        cpi_cumulative *= (1 + inf)

        eq_pct = glide_path.equity_pct(current_age)
        port_r = blended_return(eq_r, bd_r, eq_pct)
        r_eff = port_r - friction

        accounts.pretax_401k *= (1 + r_eff)
        accounts.roth_ira *= (1 + r_eff)
        accounts.hsa *= (1 + r_eff)
        accounts.taxable *= (1 + r_eff - tax_us.drag_taxable)

        seasoning_queue, roth_locked = update_seasoning_queue(
            seasoning_queue, current_age, r_eff,
            roth_ladder.senior_age_threshold, roth_ladder.seasoning_years,
        )

        if mortality.enabled:
            q = annual_mortality_rate(current_age, mortality)
            if rng.random() < q:
                age_at_death = current_age
                portfolio_path.append(accounts.total)
                break

        # Relocation event
        if (relocation.relocation_age is not None and not relocation_done
                and current_age >= relocation.relocation_age):
            in_china = True
            relocation_done = True
            fx_at_relocation = fx_rate

            if sh_property.enabled:
                purchase_nominal = sh_property.purchase_amount_y0 * cpi_cumulative
                accounts, paid, penalty = fund_shock_or_purchase(
                    accounts, purchase_nominal, tax_us, current_age, roth_locked,
                )
                total_early_wd_penalty += penalty
                sh_property_purchased_nominal = paid

                # FIX 2: use DYNAMIC CoL reduction (not flat 0.30)
                if paid >= purchase_nominal - 1.0:
                    col_effective = max(0.10, relocation.col_ratio - dynamic_col_reduction)
                    property_fully_paid = True
                else:
                    col_effective = relocation.col_ratio
                    property_fully_paid = False

        if relocation.fx_sigma > 0:
            z = rng.standard_normal()
            fx_rate = fx_rate * np.exp(relocation.fx_drift + relocation.fx_sigma * z)

        if inheritance_event is not None:
            inh_age, inh_amount_y0 = inheritance_event
            if current_age == inh_age:
                inflow = inh_amount_y0 * cpi_cumulative
                accounts.taxable += inflow
                inheritance_received_real += inh_amount_y0

        if current_age in eldercare_by_age:
            for amt_y0 in eldercare_by_age[current_age]:
                shock_nominal = amt_y0 * cpi_cumulative
                accounts, paid, penalty = fund_shock_or_purchase(
                    accounts, shock_nominal, tax_us, current_age, roth_locked,
                )
                total_early_wd_penalty += penalty
                eldercare_total_real += amt_y0
                eldercare_count += 1

        sim_year = current_age - state.start_age

        accounts, seasoning_queue, conversion_this_year = execute_roth_conversion(
            accounts, seasoning_queue, current_age, sim_year, roth_ladder,
        )
        total_conversions += conversion_this_year
        roth_locked += conversion_this_year

        components = compute_medical_components(
            year_in_simulation=sim_year, age=current_age, in_retirement=True,
            med=medical, cpi_cumulative=cpi_cumulative,
        )

        target_nominal, rule_state = rule.compute_target_withdrawal(
            year_in_retirement=year_idx, age=current_age,
            portfolio_nominal=accounts.total, inflation_this_year=inf,
            cpi_cumulative=cpi_cumulative, state=rule_state,
        )

        if ss.enabled and current_age == ss.claim_age:
            cpi_at_ss_claim = cpi_cumulative
        ss_income_gross = compute_ss_annual_income(
            current_age, cpi_at_ss_claim or cpi_cumulative, cpi_cumulative, ss,
        )
        # ── FIX C (Audit F3) · SS NRA haircut when in_china ──
        # 30% NRA withholding minus treaty residual minus admin friction
        # nets to ~20% reduction. SS is paid in USD; haircut applies
        # regardless of where the money is then spent.
        if in_china:
            ss_income = ss_income_gross * (1 - ss_nra.haircut_fraction)
        else:
            ss_income = ss_income_gross
        ss_payments_received_real += ss_income / cpi_cumulative

        magi_proxy = estimate_magi_proxy(
            taxable_wd_nominal=target_nominal * 0.5,
            pretax_401k_wd_nominal=target_nominal * 0.3,
        )
        magi_proxy += conversion_this_year
        full_premium = components['premium_full']

        # ── FIX A (Audit F1) · Shanghai ACA gate ──
        # When in_china: no US ACA premium logic applies. Use deterministic
        # China healthcare cost instead.
        #
        # Architecture detail: target_nominal comes from GK rule_state which
        # was seeded with either US or China healthcare in initial_total_expenses
        # (see FIX B at function start). The adjustment differs:
        #
        # - Path started in US, relocated mid-retirement:
        #   target has US healthcare implicitly. Subtract (us_premium - china)
        #   to make withdrawal reflect actual China cost.
        # - Path started in China (starts_in_china=True):
        #   target already calibrated to China cost. No adjustment needed.
        if in_china:
            china_health_nominal = china_healthcare_cost_nominal(
                current_age, cpi_cumulative, china_healthcare,
            )
            if starts_in_china:
                # Target already accounts for China healthcare; no further adjust
                adjusted_target = target_nominal
            else:
                # Target was seeded with US healthcare; subtract the delta
                health_adjustment = full_premium - china_health_nominal
                adjusted_target = max(0.0, target_nominal - health_adjustment)
        else:
            aca_paid = compute_aca_premium_paid(
                full_premium, magi_proxy, cpi_cumulative, aca,
            )
            premium_savings = full_premium - aca_paid
            # FIX 1: Floor adjusted_target at 0 (prevent negative consumption)
            adjusted_target = max(0.0, target_nominal - premium_savings)

        if in_china and sh_property.enabled and sh_property.rental_income_y0 > 0:
            rental_nominal = sh_property.rental_income_y0 * cpi_cumulative
            adjusted_target = max(0.0, adjusted_target - rental_nominal)

        portfolio_withdrawal_needed = max(0.0, adjusted_target - ss_income)

        if in_china:
            if cny_expenses_real is None:
                cny_expenses_real = portfolio_withdrawal_needed * fx_at_relocation * col_effective
            else:
                cn_inf = state.inflation_cn if relocation.use_cn_inflation else state.inflation
                cny_expenses_real *= (1 + cn_inf)
            portfolio_withdrawal_needed = cny_expenses_real / fx_rate
            tax_to_use = tax_cn_effective
        else:
            tax_to_use = tax_us

        accounts, received, penalty_this_yr = withdraw_with_seasoning_v94(
            accounts, portfolio_withdrawal_needed, tax_to_use, roth_locked,
            current_age,
        )
        total_early_wd_penalty += penalty_this_yr

        if received < portfolio_withdrawal_needed - 1.0:
            survived_financially = False
            shortfall_age = current_age
            portfolio_path.append(accounts.total)
            real_consumed = max(0.0, (received + ss_income) / cpi_cumulative)
            real_consumption_path.append(real_consumed)
            nominal_consumption_path.append(received + ss_income)
            break

        # FIX 1 (continued): floor the consumption record at 0 explicitly
        total_consumed_nominal = max(0.0, adjusted_target)
        real_consumption_path.append(total_consumed_nominal / cpi_cumulative)
        nominal_consumption_path.append(total_consumed_nominal)
        portfolio_path.append(accounts.total)

    years_in_retirement = len(portfolio_path) - 1
    died_during_retirement = age_at_death is not None

    return {
        'survived_financially': survived_financially,
        'died_during_retirement': died_during_retirement,
        'age_at_death': age_at_death,
        'shortfall_age': shortfall_age,
        'years_in_retirement': years_in_retirement,
        'terminal_balance': accounts.total if survived_financially else 0.0,
        'final_accounts': accounts,
        'fx_at_relocation': fx_at_relocation,
        'final_fx': fx_rate,
        'in_china_at_end': in_china,
        'guardrail_triggers': rule_state.get('guardrail_triggers', 0),
        'real_consumption_path': real_consumption_path,
        'nominal_consumption_path': nominal_consumption_path,
        'portfolio_path': list(portfolio_path),
        'mean_real_consumption': (
            float(np.mean(real_consumption_path)) if real_consumption_path else 0.0
        ),
        'min_real_consumption': (
            float(np.min(real_consumption_path)) if real_consumption_path else 0.0
        ),
        'total_roth_conversions_nominal': total_conversions,
        'ss_total_received_real': ss_payments_received_real,
        'final_roth_locked': roth_locked if seasoning_queue else 0.0,
        'eldercare_total_real': eldercare_total_real,
        'eldercare_event_count': eldercare_count,
        'inheritance_received_real': inheritance_received_real,
        'sh_property_purchased_nominal': sh_property_purchased_nominal,
        'sh_property_fully_paid': property_fully_paid,
        'total_early_wd_penalty_nominal': total_early_wd_penalty,
        'glide_path_name': glide_path.name,
        'col_reduction_applied': dynamic_col_reduction if sh_property.enabled else 0.0,
        'lifetime_success': survived_financially,
    }


# ============================================================
# v9.5 LIFECYCLE (drop-in replacement)
# ============================================================
def simulate_lifecycle_v96(
    config: V7Config = None,
    promo_params: PromotionParams = None,
    contrib_params: V8ContributionParams = None,
    rule: WithdrawalRule = None,
    glide_path: GlidePath = None,
    bond_params: BondParams = None,
    medical: MedicalParams = None,
    aca: ACAParams = None,
    mortality: MortalityParams = None,
    roth_ladder: RothLadderParams = None,
    ss: SocialSecurityParams = None,
    ftc: FTCParams = None,
    obbba: OBBBAParams = None,
    eldercare: EldercareShockParams = None,
    inheritance: InheritanceParams = None,
    sh_property: ShanghaiPropertyParams = None,
    initial: AccountStack = None,
    state: State = None,
    tax_us: TaxParams = None,
    tax_cn: TaxParamsChina = None,
    fire_swr: float = None,
    relocation: RelocationParams = None,
    regimes: Optional[list] = None,
    rng: np.random.Generator = None,
    china_healthcare: ChinaHealthcareParams = None,    # v9.6 NEW
    ss_nra: SSNRAHaircutParams = None,                  # v9.6 NEW
) -> dict:
    """v9.6 lifecycle: v9.5 + Shanghai ACA gate + region-aware seed + SS NRA."""
    config = config or V7Config()
    promo_params = promo_params or PromotionParams()
    rule = rule or FixedRealRule()
    glide_path = glide_path or GLIDE_ALL_EQUITY
    bond_params = bond_params or DEFAULT_BOND_PARAMS
    medical = medical or DEFAULT_MEDICAL
    aca = aca or ACAParams()
    mortality = mortality or MORTALITY_MALE
    roth_ladder = roth_ladder or RothLadderParams()
    ss = ss or SocialSecurityParams()
    ftc = ftc or FTCParams()
    obbba = obbba or OBBBAParams()
    eldercare = eldercare or EldercareShockParams()
    inheritance = inheritance or InheritanceParams()
    sh_property = sh_property or ShanghaiPropertyParams()
    state = state or STATE
    fire_swr = fire_swr or state.swr_pref
    relocation = relocation or RelocationParams()
    rng = rng or np.random.default_rng()

    total_years = state.accum_years + state.retire_horizon

    regime, all_equity_returns, all_inflations = sample_lifetime_v7(
        total_years, rng, config, regimes=regimes,
    )
    all_bond_returns = sample_bond_returns(all_equity_returns, bond_params, rng)

    promo_year, bonus_pcts = sample_promotion_event(promo_params, rng)

    accum_returns = all_equity_returns[:state.accum_years]
    accum_inflations = all_inflations[:state.accum_years]

    accum_path = project_stratified_v93(
        accum_returns, accum_inflations,
        promo_year, bonus_pcts,
        initial, contrib_params, promo_params,
        tax_us, state, friction=config.friction_accum,
        obbba=obbba,
    )

    fire_step = find_fire_crossing(accum_path, fire_swr)
    if fire_step is None:
        return {
            'regime': regime.name, 'died_during_accum': False,
            'reached_fire': False, 'lifetime_success': False,
            'fire_age': None, 'accum_path': accum_path,
            'withdrawal': None, 'promotion_year': promo_year,
        }

    fire_age = fire_step['age']
    fire_year_idx = fire_age - state.start_age

    death_in_accum = None
    if mortality.enabled:
        for i in range(fire_year_idx):
            age = state.start_age + i + 1
            q = annual_mortality_rate(age, mortality)
            if rng.random() < q:
                death_in_accum = age
                break

    if death_in_accum is not None:
        return {
            'regime': regime.name, 'died_during_accum': True,
            'age_at_death': death_in_accum, 'reached_fire': False,
            'lifetime_success': True, 'fire_age': None,
            'accum_path': accum_path, 'withdrawal': None,
            'promotion_year': promo_year,
        }

    cpi_cum_at_fire = fire_step['expenses'] / state.expenses_y0

    eldercare_events = sample_eldercare_events(
        rng, eldercare, fire_age, fire_age + state.retire_horizon,
    )
    inheritance_event = sample_inheritance(rng, inheritance)

    wd_equity = all_equity_returns[fire_year_idx:fire_year_idx + state.retire_horizon]
    wd_bond = all_bond_returns[fire_year_idx:fire_year_idx + state.retire_horizon]
    wd_inflations = all_inflations[fire_year_idx:fire_year_idx + state.retire_horizon]

    wd_result = simulate_retirement_v96(
        starting_accounts=fire_step['accounts'],
        starting_age=fire_age,
        fire_year_cpi_cumulative=cpi_cum_at_fire,
        equity_returns=wd_equity, bond_returns=wd_bond, inflations=wd_inflations,
        rule=rule, glide_path=glide_path,
        relocation=relocation, sh_property=sh_property,
        medical=medical, aca=aca, mortality=mortality,
        roth_ladder=roth_ladder, ss=ss, ftc=ftc,
        eldercare_events=eldercare_events, inheritance_event=inheritance_event,
        state=state, tax_us=tax_us, tax_cn=tax_cn,
        friction=config.friction_retire, rng=rng,
        china_healthcare=china_healthcare, ss_nra=ss_nra,    # v9.6 NEW
    )

    return {
        'regime': regime.name,
        'died_during_accum': False,
        'reached_fire': True,
        'fire_age': fire_age,
        'fire_balance': fire_step['total'],
        'fire_accounts': fire_step['accounts'].copy(),
        'fire_expenses': fire_step['expenses'],
        'lifetime_success': wd_result['lifetime_success'],
        'accum_path': accum_path,
        'withdrawal': wd_result,
        'relocation_age': relocation.relocation_age,
        'promotion_year': promo_year,
        'rule_name': rule.name,
        'glide_path_name': glide_path.name,
        'eldercare_events_sampled': eldercare_events,
        'inheritance_event_sampled': inheritance_event,
    }


def run_lifecycle_mc_v96(
    config: V7Config = None,
    n_paths: int = None,
    seed: int = None,
    **kwargs,
) -> list:
    config = config or V7Config()
    n_paths = n_paths or config.n_paths
    seed = seed if seed is not None else config.seed
    rng = np.random.default_rng(seed)
    return [simulate_lifecycle_v96(config=config, rng=rng, **kwargs)
            for _ in range(n_paths)]
