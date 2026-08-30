"""
FIRE Model v9.4 — Analyst-A · 2026-05-10
======================================

BUGFIX PATCH responding to Gemini Pro review of v9.3.

FIX 1: Conditional CoL Reduction in Shanghai Property Scenario
  v9.3 bug: col_effective was reduced unconditionally even when accounts
  couldn't fully fund the property purchase.
  v9.4 fix: only apply CoL reduction if purchase_remaining == 0 after
  taxable + 401k drain. Partial purchases either fail entirely (logical
  default) or proportionally adjust CoL.

FIX 2: 10% Early Withdrawal Penalty for Pre-59.5 Pretax_401k Hits
  v9.3 bug: the 12% withdrawal_tax_traditional did not include the IRS
  10% early withdrawal penalty when current_age < 59.5.
  v9.4 fix: apply +10% penalty rate addition to traditional WD tax
  whenever pretax_401k is touched before age 59.5. This applies
  consistently across:
    - Normal retirement withdrawals (withdraw_with_seasoning_v94)
    - Eldercare shock funding
    - Shanghai property purchase
    - Any other forced 401k touch

  Note on exemptions NOT modeled (would soften the penalty in reality):
    - 72(t) SEPP elections (would require ongoing fixed schedule)
    - Medical expense exception (only for OWN medical >7.5% AGI;
      parental eldercare does NOT qualify)
    - Permanent disability, first-time homebuyer ($10K Roth only),
      higher education

  In reality, with Roth ladder operating, Analyst-A almost never hits 401k
  pre-59.5 in the normal flow. Shock events are the main case where
  the penalty matters.

ALL OTHER FUNCTIONALITY UNCHANGED FROM v9.3.

Usage:
    from fire_v9_4_model import simulate_lifecycle_v94, run_lifecycle_mc_v94
    # (same interface as v9.3)
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
from typing import Callable, Sequence, Optional
from enum import Enum

# Re-export everything from v9.3
from fire_v9_3_model import (
    # All v9.3 exports
    BondParams, DEFAULT_BOND_PARAMS,
    GlidePath, GLIDE_ALL_EQUITY, GLIDE_CONSERVATIVE, GLIDE_STANDARD, ALL_GLIDE_PATHS,
    sample_bond_returns, blended_return,
    ShockMode,
    EldercareShockParams, sample_eldercare_events,
    InheritanceParams, sample_inheritance,
    OBBBAMode, OBBBAParams, compute_obbba_boost_path,
    ShanghaiPropertyParams,
    project_stratified_v93,
)

# Imports from earlier versions
from fire_v6_model import (
    State, AccountStack, TaxParams, RelocationParams,
    INITIAL_STACK, STATE, TAX_US,
    Regime, REGIMES, find_fire_crossing,
)
from fire_rule_pack import US_FEDERAL_RULES
from fire_v7_model import (
    TaxParamsChina, TAX_CN, V7Config, sample_lifetime_v7,
)
from fire_v8_model import (
    PromotionParams, V8ContributionParams, sample_promotion_event,
)
from fire_v9_1_model import (
    MortalityParams, MORTALITY_MALE, MORTALITY_FEMALE, MORTALITY_UNISEX,
    annual_mortality_rate, sample_age_at_death, median_life_expectancy,
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


# ============================================================
# v9.4 FIX 2: PENALTY-AWARE WITHDRAWAL
# ============================================================
EARLY_WD_PENALTY_AGE = US_FEDERAL_RULES["early_withdrawal_age"]
EARLY_WD_PENALTY_RATE = US_FEDERAL_RULES["early_withdrawal_rate"]


def _schema_order(withdrawal_order=None):
    """The declared account types in draw order, plus the schema module.

    Imported lazily so the engine keeps no import-time dependency on the
    server package -- the engine is the part with no third-party surface but
    numpy, and Phase 2 does not spend that.

    The module comes back alongside the order because the withdrawal below
    needs one of its constants as well, and reaching for it a second time
    would mean two copies of this shim in one file.
    """
    import os
    import sys
    server = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "server")
    if server not in sys.path:
        sys.path.insert(0, server)
    import account_schema as SCHEMA
    return SCHEMA.ordered_types(withdrawal_order), SCHEMA


def withdraw_with_seasoning_v94(
    accounts: AccountStack,
    needed_after_tax: float,
    tax,
    roth_locked_amount: float,
    current_age: float,
    withdrawal_order: "Optional[list]" = None,
    gain_fraction: "Optional[float]" = None,
    meta_out: "Optional[dict]" = None,
) -> tuple[AccountStack, float, float]:
    """
    v9.4 patched withdrawal. Adds 10% IRS early withdrawal penalty when
    pretax_401k is touched before age 59.5.

    Returns (new_accounts, actual_after_tax_received, penalty_paid_real_dollars).

    `gain_fraction` is `(value - basis) / value` for the CAPITAL-GAIN accounts
    this year (OPEN_ITEMS E32). Without it this function charged the taxable
    rate on the whole withdrawal, principal included -- and principal in a
    taxable account is money that was already taxed on the way in. That the
    rate belongs on the gain alone is not a new reading: the 2026-08-14
    step-up ruling settled it and `engine_adapter._terminal_values` has
    shipped it since, computing `taxable - gain * tx`.

    `None` means 1.0 -- everything is gain -- which is exactly what this
    function did before the parameter existed. That default is deliberate:
    three earlier engine generations still call this, and their numbers are
    not in this slice's scope.

    `meta_out` receives `capital_gain_withdrawal`, so a caller tracking basis
    can retire it in proportion without re-deriving the draw from balance
    differences -- which would be a second implementation of the ordering rule
    right here.
    """
    accounts = accounts.copy()
    remaining = needed_after_tax
    total_penalty = 0.0
    gain_frac = (1.0 if gain_fraction is None
                 else max(0.0, min(1.0, float(gain_fraction))))
    capital_gain_taken = 0.0

    # Roadmap 7.0 Phase 2: the order and the per-account rules come from the
    # declaration in `server/account_schema.py` rather than from four blocks
    # written out by hand. The six dimensions all live here, which is why this
    # function was chosen as the first thing to read the schema: if a
    # declaration can drive this, it can drive the engine.
    #
    # `withdrawal_order` absent means the declared default, which is the order
    # this function has used across four engine generations. That is what
    # keeps this change bit-identical for every existing plan.
    order, SCHEMA = _schema_order(withdrawal_order)
    for account in order:
        if remaining <= 0:
            break
        balance = getattr(accounts, account.field, 0.0)
        if balance <= 0:
            continue

        # Seasoning: money that exists but cannot be touched yet. Subtracted
        # from what is reachable rather than from the balance, because the
        # locked part is still the owner's -- it is access that is limited.
        if account.seasoned:
            balance = max(0.0, balance - roth_locked_amount)
            if balance <= 0:
                continue

        rate = 0.0
        if account.withdrawal_rate is not None:
            rate = getattr(tax, account.withdrawal_rate, 0.0)
        # The rate applies to the GAIN, not to the whole sale. For every other
        # account type `gain_frac` is not consulted at all, because "how much
        # of this is profit" is only a question a capital-gain account can be
        # asked -- a pre-tax dollar is taxed in full whatever its history.
        if account.tax_character == SCHEMA.CHARACTER_CAPITAL_GAIN:
            rate = rate * gain_frac
        penalised = (account.early_penalty_age is not None
                     and current_age < account.early_penalty_age)
        if penalised:
            rate += account.early_penalty_rate

        if rate:
            gross_take = min(remaining / max(1 - rate, 0.001), balance)
        else:
            # No rate at all: the gross amount IS the net one. Kept as a
            # separate branch rather than folded into the arithmetic above,
            # because `1 - 0.0` is a multiplication the engine never performed
            # on this path and identity here is the whole gate.
            gross_take = min(remaining, balance)
        setattr(accounts, account.field,
                getattr(accounts, account.field) - gross_take)
        remaining -= gross_take * (1 - rate)
        if account.tax_character == SCHEMA.CHARACTER_CAPITAL_GAIN:
            capital_gain_taken += gross_take
        if penalised:
            total_penalty += gross_take * account.early_penalty_rate

    actual = needed_after_tax - max(remaining, 0.0)
    if meta_out is not None:
        meta_out["capital_gain_withdrawal"] = capital_gain_taken
    return accounts, actual, total_penalty


def fund_shock_or_purchase(
    accounts: AccountStack,
    cost_nominal: float,
    tax_us: TaxParams,
    current_age: float,
    roth_locked: float,
) -> tuple[AccountStack, float, float]:
    """
    Fund a one-time outflow (eldercare shock, property, etc.) with proper
    early withdrawal penalty applied to pre-59.5 pretax_401k touches.

    Returns (new_accounts, paid_amount, penalty_paid).
    """
    accounts = accounts.copy()
    remaining = cost_nominal
    penalty = 0.0

    # Taxable first
    if accounts.taxable >= remaining:
        accounts.taxable -= remaining
        remaining = 0
    else:
        remaining -= accounts.taxable
        accounts.taxable = 0

    # 401k with potential penalty
    if remaining > 0 and accounts.pretax_401k > 0:
        base_rate = tax_us.withdrawal_tax_traditional
        effective_rate = (base_rate + EARLY_WD_PENALTY_RATE
                           if current_age < EARLY_WD_PENALTY_AGE else base_rate)
        gross_needed = remaining / max(1 - effective_rate, 0.001)
        gross_take = min(gross_needed, accounts.pretax_401k)
        accounts.pretax_401k -= gross_take
        net = gross_take * (1 - effective_rate)
        remaining -= net
        if current_age < EARLY_WD_PENALTY_AGE:
            penalty += gross_take * EARLY_WD_PENALTY_RATE

    # Roth (last resort)
    if remaining > 0:
        accessible_roth = max(0.0, accounts.roth_ira - roth_locked)
        if accessible_roth > 0:
            take = min(remaining, accessible_roth)
            accounts.roth_ira -= take
            remaining -= take

    paid = cost_nominal - max(remaining, 0)
    return accounts, paid, penalty


# ============================================================
# v9.4 RETIREMENT SIMULATOR (patched)
# ============================================================
def simulate_retirement_v94(
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
    eldercare_events: list[tuple[int, float]],
    inheritance_event: Optional[tuple[int, float]],
    state: State = None,
    tax_us: TaxParams = None,
    tax_cn: TaxParamsChina = None,
    friction: float = 0.005,
    rng: np.random.Generator = None,
) -> dict:
    """v9.4 patched retirement simulator."""
    state = state or STATE
    tax_us = tax_us or TAX_US
    tax_cn = tax_cn or TAX_CN
    rng = rng or np.random.default_rng()

    tax_cn_effective = apply_ftc_to_tax_cn(tax_cn, ftc)
    accounts = starting_accounts.copy()

    # Initialize withdrawal rule
    initial_components = compute_medical_components(
        year_in_simulation=starting_age - state.start_age,
        age=starting_age,
        in_retirement=True,
        med=medical,
        cpi_cumulative=fire_year_cpi_cumulative,
    )
    initial_full_premium = initial_components['premium_full']
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

    in_china = False
    cny_expenses_real = None
    fx_rate = relocation.fx_initial
    fx_at_relocation = None
    relocation_done = False
    col_effective = relocation.col_ratio
    property_fully_paid = False    # NEW v9.4: track if property purchase was complete

    survived_financially = True
    shortfall_age = None
    age_at_death = None
    cpi_cumulative = fire_year_cpi_cumulative
    cpi_at_ss_claim = None
    real_consumption_path = []
    nominal_consumption_path = []
    portfolio_path = [accounts.total]

    seasoning_queue: list[SeasoningEntry] = []
    total_conversions = 0.0
    ss_payments_received_real = 0.0

    eldercare_total_real = 0.0
    eldercare_count = 0
    inheritance_received_real = 0.0
    sh_property_purchased_nominal = 0.0
    total_early_wd_penalty = 0.0   # NEW v9.4 tracking

    eldercare_by_age = {}
    for age, amt in eldercare_events:
        eldercare_by_age.setdefault(age, []).append(amt)

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

        # ----- Relocation event (with v9.4-FIX-1: conditional CoL) -----
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

                # v9.4 FIX 1: only apply CoL reduction if property fully paid
                if paid >= purchase_nominal - 1.0:
                    col_effective = max(0.10, relocation.col_ratio - sh_property.col_reduction)
                    property_fully_paid = True
                else:
                    # Couldn't fully fund — keep original CoL (renting)
                    # User effectively spent the money but didn't get the housing benefit
                    # (worst-case from a planning perspective)
                    col_effective = relocation.col_ratio
                    property_fully_paid = False

        if relocation.fx_sigma > 0:
            z = rng.standard_normal()
            fx_rate = fx_rate * np.exp(relocation.fx_drift + relocation.fx_sigma * z)

        # Inheritance
        if inheritance_event is not None:
            inh_age, inh_amount_y0 = inheritance_event
            if current_age == inh_age:
                inflow = inh_amount_y0 * cpi_cumulative
                accounts.taxable += inflow
                inheritance_received_real += inh_amount_y0

        # ----- Eldercare events (v9.4-FIX-2: penalty-aware) -----
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
        ss_income = compute_ss_annual_income(
            current_age, cpi_at_ss_claim or cpi_cumulative, cpi_cumulative, ss,
        )
        ss_payments_received_real += ss_income / cpi_cumulative

        magi_proxy = estimate_magi_proxy(
            taxable_wd_nominal=target_nominal * 0.5,
            pretax_401k_wd_nominal=target_nominal * 0.3,
        )
        magi_proxy += conversion_this_year
        full_premium = components['premium_full']
        aca_paid = compute_aca_premium_paid(
            full_premium, magi_proxy, cpi_cumulative, aca,
        )
        premium_savings = full_premium - aca_paid
        adjusted_target = target_nominal - premium_savings

        if in_china and sh_property.enabled and sh_property.rental_income_y0 > 0:
            rental_nominal = sh_property.rental_income_y0 * cpi_cumulative
            adjusted_target = max(0, adjusted_target - rental_nominal)

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

        # v9.4 FIX 2: penalty-aware normal withdrawal
        accounts, received, penalty_this_yr = withdraw_with_seasoning_v94(
            accounts, portfolio_withdrawal_needed, tax_to_use, roth_locked,
            current_age,
        )
        total_early_wd_penalty += penalty_this_yr

        if received < portfolio_withdrawal_needed - 1.0:
            survived_financially = False
            shortfall_age = current_age
            portfolio_path.append(accounts.total)
            real_consumed = (received + ss_income) / cpi_cumulative
            real_consumption_path.append(real_consumed)
            nominal_consumption_path.append(received + ss_income)
            break

        total_consumed_nominal = adjusted_target
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
        'sh_property_fully_paid': property_fully_paid,        # NEW v9.4
        'total_early_wd_penalty_nominal': total_early_wd_penalty,  # NEW v9.4
        'glide_path_name': glide_path.name,
        'lifetime_success': survived_financially,
    }


# ============================================================
# v9.4 LIFECYCLE (drop-in replacement using v94 retirement)
# ============================================================
def simulate_lifecycle_v94(
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
    regimes: Optional[list[Regime]] = None,
    rng: np.random.Generator = None,
) -> dict:
    """v9.4 full lifecycle (uses patched simulate_retirement_v94)."""
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

    wd_result = simulate_retirement_v94(
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


def run_lifecycle_mc_v94(
    config: V7Config = None,
    n_paths: int = None,
    seed: int = None,
    **kwargs,
) -> list[dict]:
    config = config or V7Config()
    n_paths = n_paths or config.n_paths
    seed = seed if seed is not None else config.seed
    rng = np.random.default_rng(seed)
    return [simulate_lifecycle_v94(config=config, rng=rng, **kwargs)
            for _ in range(n_paths)]


def aggregate_v94(results: list[dict]) -> dict:
    """v9.4 aggregation. Same as v9.3 plus penalty tracking."""
    n = len(results)
    reached = [r for r in results if r['reached_fire']]
    died_in_accum = [r for r in results if r.get('died_during_accum')]
    succeeded = [r for r in results if r['lifetime_success']]
    failed = [r for r in reached if not r['lifetime_success']]
    succeeded_in_retirement = [r for r in reached if r['lifetime_success']]

    fire_ages = [r['fire_age'] for r in reached]
    terminal_balances = [r['withdrawal']['terminal_balance']
                          for r in succeeded_in_retirement]

    mean_consumptions = [r['withdrawal']['mean_real_consumption']
                          for r in reached if r['withdrawal'] is not None]
    min_consumptions = [r['withdrawal']['min_real_consumption']
                         for r in reached if r['withdrawal'] is not None]
    guardrail_triggers = [r['withdrawal']['guardrail_triggers']
                           for r in reached if r['withdrawal'] is not None]
    years_lived = [r['withdrawal']['years_in_retirement']
                    for r in reached if r['withdrawal'] is not None]
    roth_conversions = [r['withdrawal']['total_roth_conversions_nominal']
                         for r in reached if r['withdrawal'] is not None]
    ss_received_real = [r['withdrawal']['ss_total_received_real']
                         for r in reached if r['withdrawal'] is not None]

    eldercare_totals = [r['withdrawal']['eldercare_total_real']
                         for r in reached if r['withdrawal'] is not None]
    eldercare_counts = [r['withdrawal']['eldercare_event_count']
                         for r in reached if r['withdrawal'] is not None]
    inheritance_totals = [r['withdrawal']['inheritance_received_real']
                           for r in reached if r['withdrawal'] is not None]
    sh_property_totals = [r['withdrawal']['sh_property_purchased_nominal']
                           for r in reached if r['withdrawal'] is not None]

    # NEW v9.4
    early_wd_penalties = [r['withdrawal']['total_early_wd_penalty_nominal']
                           for r in reached if r['withdrawal'] is not None]
    sh_property_fully_paid = [r['withdrawal']['sh_property_fully_paid']
                                for r in reached if r['withdrawal'] is not None
                                and 'sh_property_fully_paid' in r['withdrawal']]

    paths_with_eldercare = sum(1 for x in eldercare_counts if x > 0)
    paths_with_inheritance = sum(1 for x in inheritance_totals if x > 0)

    return {
        'n_paths': n,
        'reached_fire_rate': len(reached) / n,
        'died_in_accum_count': len(died_in_accum),
        'lifetime_success_rate': len(succeeded) / n,
        'conditional_success_rate': (
            sum(1 for r in reached if r['lifetime_success']) / len(reached)
            if reached else 0.0
        ),
        'failure_count': len(failed),
        'fire_age_p25': int(np.percentile(fire_ages, 25)) if fire_ages else None,
        'fire_age_p50': int(np.percentile(fire_ages, 50)) if fire_ages else None,
        'fire_age_p75': int(np.percentile(fire_ages, 75)) if fire_ages else None,
        'terminal_p10': float(np.percentile(terminal_balances, 10)) if terminal_balances else 0.0,
        'terminal_p50': float(np.percentile(terminal_balances, 50)) if terminal_balances else 0.0,
        'terminal_p90': float(np.percentile(terminal_balances, 90)) if terminal_balances else 0.0,
        'mean_real_consumption_p10': float(np.percentile(mean_consumptions, 10)) if mean_consumptions else 0.0,
        'mean_real_consumption_p50': float(np.percentile(mean_consumptions, 50)) if mean_consumptions else 0.0,
        'mean_real_consumption_p90': float(np.percentile(mean_consumptions, 90)) if mean_consumptions else 0.0,
        'min_real_consumption_p10': float(np.percentile(min_consumptions, 10)) if min_consumptions else 0.0,
        'min_real_consumption_p50': float(np.percentile(min_consumptions, 50)) if min_consumptions else 0.0,
        'guardrail_trigger_p50': int(np.percentile(guardrail_triggers, 50)) if guardrail_triggers else 0,
        'years_lived_p50': int(np.percentile(years_lived, 50)) if years_lived else 0,
        'roth_conversions_p50_nominal': float(np.percentile(roth_conversions, 50)) if roth_conversions else 0.0,
        'ss_received_p50_real': float(np.percentile(ss_received_real, 50)) if ss_received_real else 0.0,
        'eldercare_paths_with_event_pct': paths_with_eldercare / max(len(reached), 1),
        'eldercare_total_p50_real': float(np.percentile(eldercare_totals, 50)) if eldercare_totals else 0.0,
        'eldercare_total_p90_real': float(np.percentile(eldercare_totals, 90)) if eldercare_totals else 0.0,
        'inheritance_paths_with_event_pct': paths_with_inheritance / max(len(reached), 1),
        'sh_property_p50_nominal': float(np.percentile(sh_property_totals, 50)) if sh_property_totals else 0.0,
        # NEW v9.4
        'early_wd_penalty_p50_nominal': float(np.percentile(early_wd_penalties, 50)) if early_wd_penalties else 0.0,
        'early_wd_penalty_p90_nominal': float(np.percentile(early_wd_penalties, 90)) if early_wd_penalties else 0.0,
        'sh_property_full_paid_pct': (
            sum(1 for x in sh_property_fully_paid if x) / len(sh_property_fully_paid)
            if sh_property_fully_paid else None
        ),
    }
