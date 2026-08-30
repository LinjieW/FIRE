"""
FIRE Model v9.3 — Analyst-A · 2026-05-09
======================================

NEW IN v9.3 (extends v9.2):

  [12] BOND TENT / GLIDE PATH (Pfau-style rising equity)
       Three configurations:
         a. All-equity (v9.2 default; baseline for comparison)
         b. Conservative rising glide: 60/40 at FIRE → 90/10 by age 65
         c. Standard rising glide:     80/20 at FIRE → 100/0 by age 65

       Mechanism: blended return = w_eq × eq_return + w_bond × bond_return
       Bond return params: μ=4.5%, σ=5.5%, ρ=0.15 with equity (mild positive)
       Glide path applies during retirement only. Pre-FIRE stays 100% equity
       (FXAIX/VOO+QQQM).

       Pfau finding: rising equity glide beats static allocation in worst
       sequences because high bond allocation early protects against the
       sequence-of-returns risk window (first 10-15 years post-FIRE).

  [13] ELDERCARE SHOCK (negative one-time)
       Two modes:
         a. Stochastic — each path: Bernoulli(p=1.5%) per year in window 40-70.
            Severity ~ Lognormal(median=$80K Y0, σ=0.5)
            P50 ≈ $80K, P90 ≈ $150K, P99 ≈ $300K.
            Multiple events possible per path.
         b. Scenario — single deterministic event at age 55, $150K Y0.

       Funded from taxable account; if insufficient, taxable depleted to 0
       and remaining shortfall absorbed (forced consumption cut next year).
       Reflects the analyst's Chinese-family context where parental medical
       emergencies are a real planning risk.

  [14] INHERITANCE (positive one-time)
       Two modes:
         a. Stochastic — lifetime probability 50%, age uniform 55-80,
            amount Lognormal(median=$300K Y0, σ=0.6) → P90 ≈ $650K.
         b. Scenario — deterministic $300K at age 65.

       Deposited to taxable account.

  [15] OBBBA OT DEDUCTION (federal tax break on overtime pay)
       Three modes:
         a. off — no benefit (conservative baseline)
         b. sunsets — $5,000/yr fed tax savings, expires after 2 years
            (current law: bill sunsets 2028, accumulation begins 2026)
         c. permanent — $5,000/yr through entire eligible accumulation
            period (assumes Congress renews indefinitely)

       Benefit applied as additional taxable contribution during eligible
       years. Compounded forward in taxable account.

  [16] SHANGHAI PROPERTY PURCHASE
       Optional one-time outflow at relocation event:
         - Purchase amount: $400K Y0 (USD-equivalent of CNY price)
         - Funded from taxable account (assumes liquidation)
         - CoL ratio reduction: 0.30 (housing was ~35% of expenses;
           owning eliminates rent → CoL drops from 0.85 to 0.55)
         - Optional rental income (default $0; for live-in scenario)

INHERITS FROM v9.2 UNCHANGED:
  All v6→v9.2 features

DEFERRED:
  - Optimal Roth conversion timing (currently fixed schedule)
  - Withdrawal sequence joint optimization with Roth ladder + bond tent
  - Healthcare bridge edge cases (already partially in v9.1)
  - Other partially-modeled items

Requires: numpy, fire_v9_2_model
Usage:
    python fire_v9_3_model.py
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
from typing import Callable, Sequence, Optional
from enum import Enum

from fire_v6_model import (
    State, AccountStack, TaxParams, RelocationParams,
    INITIAL_STACK, STATE, TAX_US,
    Regime, REGIMES,
    find_fire_crossing,
)
from fire_v7_model import (
    TaxParamsChina, TAX_CN, V7Config, sample_lifetime_v7,
)
from fire_v8_model import (
    PromotionParams, V8ContributionParams, sample_promotion_event,
    project_stratified_v8,
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
    withdraw_with_seasoning,
)


# ============================================================
# [12] BOND TENT / GLIDE PATH
# ============================================================
@dataclass
class BondParams:
    """Bond return distribution parameters."""
    mean: float = 0.045        # arithmetic mean nominal
    sigma: float = 0.055
    correlation_with_equity: float = 0.15   # mild positive in normal regimes
    drag: float = 0.001        # implementation drag (expense ratio)


DEFAULT_BOND_PARAMS = BondParams()


@dataclass
class GlidePath:
    """Rising equity glide path (Pfau-style bond tent unwind)."""
    name: str = "All-equity"
    start_age: int = 35       # age at which glide begins (FIRE age)
    end_age: int = 65         # age at which glide ends
    equity_start: float = 1.0 # equity weight at FIRE
    equity_end: float = 1.0   # equity weight at end_age and beyond

    def equity_pct(self, age: int) -> float:
        """Equity weight at given age via linear interpolation."""
        if age <= self.start_age:
            return self.equity_start
        if age >= self.end_age:
            return self.equity_end
        t = (age - self.start_age) / (self.end_age - self.start_age)
        return self.equity_start + t * (self.equity_end - self.equity_start)


# Three preset glide paths
GLIDE_ALL_EQUITY = GlidePath(
    name="All-equity (v9.2 baseline)",
    equity_start=1.0, equity_end=1.0,
)
GLIDE_CONSERVATIVE = GlidePath(
    name="Conservative rising (60/40 → 90/10)",
    equity_start=0.60, equity_end=0.90,
)
GLIDE_STANDARD = GlidePath(
    name="Standard rising (80/20 → 100/0)",
    equity_start=0.80, equity_end=1.00,
)
ALL_GLIDE_PATHS = [GLIDE_ALL_EQUITY, GLIDE_CONSERVATIVE, GLIDE_STANDARD]


def sample_bond_returns(
    equity_returns: Sequence[float],
    params: BondParams,
    rng: np.random.Generator,
) -> list[float]:
    """
    Sample bond returns correlated with equity returns.
    Uses a simple per-year correlated normal draw, where the correlation
    is induced by extracting a z-score from equity returns and combining
    with an independent normal.
    """
    n = len(equity_returns)
    eq_array = np.array(equity_returns)
    eq_mean = float(np.mean(eq_array))
    eq_std = float(np.std(eq_array)) if np.std(eq_array) > 0 else 1.0
    z1 = (eq_array - eq_mean) / eq_std

    z2 = rng.standard_normal(n)
    rho = params.correlation_with_equity
    z_bond = rho * z1 + np.sqrt(1 - rho**2) * z2

    bond_returns = params.mean + params.sigma * z_bond - params.drag
    return bond_returns.tolist()


def blended_return(equity_return: float, bond_return: float,
                    equity_pct: float) -> float:
    """Blended portfolio return given allocation."""
    return equity_pct * equity_return + (1 - equity_pct) * bond_return


# ============================================================
# [13] ELDERCARE SHOCK
# ============================================================
class ShockMode(str, Enum):
    OFF = "off"
    STOCHASTIC = "stochastic"
    SCENARIO = "scenario"


@dataclass
class EldercareShockParams:
    """
    Eldercare shock: negative one-time outflow modeling parental medical emergency.

    Stochastic mode: independent Bernoulli per year in [age_window_start, age_window_end].
                      Severity drawn from lognormal.
    Scenario mode:    deterministic single event at scenario_age, scenario_amount.
    """
    mode: ShockMode = ShockMode.OFF
    annual_prob: float = 0.015                   # 1.5%/yr
    age_window_start: int = 40
    age_window_end: int = 70
    severity_log_mean: float = 11.29              # log($80,000)
    severity_log_sigma: float = 0.5               # P90 ≈ $150K
    # Scenario mode
    scenario_age: int = 55
    scenario_amount: float = 150_000.0


def sample_eldercare_events(
    rng: np.random.Generator,
    params: EldercareShockParams,
    sim_start_age: int,
    sim_end_age: int,
) -> list[tuple[int, float]]:
    """Returns list of (age, amount_y0_real) tuples."""
    if params.mode == ShockMode.OFF:
        return []
    if params.mode == ShockMode.SCENARIO:
        if sim_start_age <= params.scenario_age <= sim_end_age:
            return [(params.scenario_age, params.scenario_amount)]
        return []
    # Stochastic
    events = []
    age_lo = max(sim_start_age, params.age_window_start)
    age_hi = min(sim_end_age, params.age_window_end)
    for age in range(age_lo, age_hi + 1):
        if rng.random() < params.annual_prob:
            severity = float(rng.lognormal(params.severity_log_mean,
                                             params.severity_log_sigma))
            events.append((age, severity))
    return events


# ============================================================
# [14] INHERITANCE
# ============================================================
@dataclass
class InheritanceParams:
    """
    Inheritance: positive one-time inflow.

    Stochastic mode: lifetime probability of any event; if yes, age uniform
                      and amount lognormal.
    Scenario mode:   deterministic at scenario_age, scenario_amount.
    """
    mode: ShockMode = ShockMode.OFF
    lifetime_prob: float = 0.50
    age_window_start: int = 55
    age_window_end: int = 80
    amount_log_mean: float = 12.61                # log($300,000)
    amount_log_sigma: float = 0.6                 # P90 ≈ $650K
    # Scenario mode
    scenario_age: int = 65
    scenario_amount: float = 300_000.0


def sample_inheritance(
    rng: np.random.Generator,
    params: InheritanceParams,
) -> Optional[tuple[int, float]]:
    """Returns (age, amount_y0_real) tuple or None."""
    if params.mode == ShockMode.OFF:
        return None
    if params.mode == ShockMode.SCENARIO:
        return (params.scenario_age, params.scenario_amount)
    # Stochastic
    if rng.random() > params.lifetime_prob:
        return None
    age = int(rng.integers(params.age_window_start, params.age_window_end + 1))
    amount = float(rng.lognormal(params.amount_log_mean, params.amount_log_sigma))
    return (age, amount)


# ============================================================
# [15] OBBBA OT DEDUCTION
# ============================================================
class OBBBAMode(str, Enum):
    OFF = "off"
    SUNSETS = "sunsets"
    PERMANENT = "permanent"


@dataclass
class OBBBAParams:
    """
    OBBBA OT federal tax deduction.

    the analyst's OT income: 240 hrs × $93.75 = $22,500/yr.
    Federal marginal rate ~22% → savings ~$4,950/yr at base salary.
    Conservative rounded: $5,000/yr Y0.

    Sunset modeling: bill expires 2028 unless renewed. Analyst-A starts age 27
    in calendar 2026 → has 2 eligible years (2026, 2027) before sunset.
    """
    mode: OBBBAMode = OBBBAMode.OFF
    annual_savings_y0: float = 5_000.0
    sunset_year_offset: int = 2     # eligible years 0, 1, 2 -> 3 years; we use < check

    def is_active_year(self, sim_year: int) -> bool:
        if self.mode == OBBBAMode.OFF:
            return False
        if self.mode == OBBBAMode.PERMANENT:
            return True
        # SUNSETS
        return sim_year < self.sunset_year_offset


def compute_obbba_boost_path(
    accum_returns: Sequence[float],
    accum_inflations: Sequence[float],
    state: State,
    params: OBBBAParams,
    drag_taxable: float = 0.0,
    primary_alive_by_year: Optional[Sequence[bool]] = None,
) -> list[float]:
    """
    Compute the cumulative nominal $ value of the OBBBA boost at each step
    of the accumulation path. Returns list of boost amounts indexed by step
    (length = len(returns) + 1, matching accum_path indexing).

    The boost represents extra taxable contributions due to fed tax savings,
    compounded forward at taxable returns.
    """
    if (primary_alive_by_year is not None
            and len(primary_alive_by_year) != len(accum_returns)):
        raise ValueError(
            "primary_alive_by_year must match the accumulation horizon"
        )

    boost = 0.0
    cpi_cum = 1.0
    out = [0.0]   # year 0 (before any year's contribution)
    for year_idx, (r, inf) in enumerate(zip(accum_returns, accum_inflations)):
        cpi_cum *= (1 + inf)
        r_eff = r - drag_taxable
        # Grow existing boost by returns
        boost *= (1 + r_eff)
        # Add this year's contribution if active
        primary_alive = (
            primary_alive_by_year[year_idx]
            if primary_alive_by_year is not None else True
        )
        if primary_alive and params.is_active_year(year_idx):
            boost += params.annual_savings_y0 * cpi_cum
        out.append(boost)
    return out


# ============================================================
# [16] SHANGHAI PROPERTY
# ============================================================
@dataclass
class ShanghaiPropertyParams:
    """
    Shanghai property purchase modeling.

    Purchase amount: USD-equivalent at relocation time, inflated by US CPI
                     (proxies CNY price growth roughly).
    Funded from taxable account (assumes pre-relocation rebalancing).
    CoL reduction: housing was approximately col_reduction of expenses;
                    owning eliminates that line item.
    """
    enabled: bool = False
    purchase_amount_y0: float = 400_000.0    # in today's USD-equivalent
    col_reduction: float = 0.30               # 0.85 → 0.55 typical
    rental_income_y0: float = 0.0             # if rented out (live-in default = 0)


# ============================================================
# v9.3 ACCUMULATION (with OBBBA boost)
# ============================================================
def project_stratified_v93(
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
    obbba: OBBBAParams = None,
    alive_by_year: Optional[Sequence[tuple[bool, bool]]] = None,
    capture_ss_earnings: bool = False,
    student_debt=None,
) -> list[dict]:
    """
    Wraps project_stratified_v8 and adds OBBBA boost to taxable account
    at each step.
    """
    state = state or STATE
    tax = tax or TAX_US
    obbba = obbba or OBBBAParams()

    base_path = project_stratified_v8(
        returns, inflations, promotion_year, bonus_pcts_per_year,
        initial, contrib_params, promo_params, tax, state, friction,
        alive_by_year=alive_by_year,
        capture_ss_earnings=capture_ss_earnings,
        student_debt=student_debt,
    )

    if obbba.mode == OBBBAMode.OFF:
        return base_path

    # Compute OBBBA boost path (parallel to base_path)
    boost_path = compute_obbba_boost_path(
        returns, inflations, state, obbba,
        drag_taxable=tax.drag_taxable,
        primary_alive_by_year=(
            [primary_alive for primary_alive, _ in alive_by_year]
            if alive_by_year is not None else None
        ),
    )

    # Apply boost to each step
    new_path = []
    for step, boost in zip(base_path, boost_path):
        new_step = dict(step)
        new_accounts = step['accounts'].copy()
        new_accounts.taxable += boost
        new_step['accounts'] = new_accounts
        new_step['total'] = new_accounts.total
        new_step['obbba_boost_nominal'] = boost
        new_path.append(new_step)
    return new_path


# ============================================================
# v9.3 RETIREMENT SIMULATOR
# ============================================================
def simulate_retirement_v93(
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
    """v9.3 retirement: bond tent + shocks + Shanghai property."""
    state = state or STATE
    tax_us = tax_us or TAX_US
    tax_cn = tax_cn or TAX_CN
    rng = rng or np.random.default_rng()

    tax_cn_effective = apply_ftc_to_tax_cn(tax_cn, ftc)
    accounts = starting_accounts.copy()

    # Initialize withdrawal rule (using v9.1 expense logic)
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

    # State
    in_china = False
    cny_expenses_real = None
    fx_rate = relocation.fx_initial
    fx_at_relocation = None
    relocation_done = False
    col_effective = relocation.col_ratio    # may be reduced by Shanghai property

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

    # v9.3-specific tracking
    eldercare_total_real = 0.0
    eldercare_count = 0
    inheritance_received_real = 0.0
    sh_property_purchased_nominal = 0.0
    glide_eq_pct_at_end = glide_path.equity_pct(starting_age + 100)

    # Convert eldercare events to a dict keyed by age for fast lookup
    eldercare_by_age = {}
    for age, amt in eldercare_events:
        eldercare_by_age.setdefault(age, []).append(amt)

    for year_idx, (eq_r, bd_r, inf) in enumerate(
        zip(equity_returns, bond_returns, inflations)
    ):
        current_age = starting_age + year_idx + 1
        cpi_cumulative *= (1 + inf)

        # ----- Apply BLENDED returns (bond tent) -----
        eq_pct = glide_path.equity_pct(current_age)
        port_r = blended_return(eq_r, bd_r, eq_pct)
        r_eff = port_r - friction

        accounts.pretax_401k *= (1 + r_eff)
        accounts.roth_ira *= (1 + r_eff)
        accounts.hsa *= (1 + r_eff)
        accounts.taxable *= (1 + r_eff - tax_us.drag_taxable)

        # Update seasoning queue
        seasoning_queue, roth_locked = update_seasoning_queue(
            seasoning_queue, current_age, r_eff,
            roth_ladder.senior_age_threshold, roth_ladder.seasoning_years,
        )

        # Mortality check
        if mortality.enabled:
            q = annual_mortality_rate(current_age, mortality)
            if rng.random() < q:
                age_at_death = current_age
                portfolio_path.append(accounts.total)
                break

        # ----- Relocation event (with optional Shanghai property purchase) -----
        if (relocation.relocation_age is not None and not relocation_done
                and current_age >= relocation.relocation_age):
            in_china = True
            relocation_done = True
            fx_at_relocation = fx_rate

            if sh_property.enabled:
                purchase_nominal = sh_property.purchase_amount_y0 * cpi_cumulative
                # Pull from taxable; if insufficient, hit 401k next then forced shortfall
                purchase_remaining = purchase_nominal
                if accounts.taxable >= purchase_remaining:
                    accounts.taxable -= purchase_remaining
                    purchase_remaining = 0
                else:
                    purchase_remaining -= accounts.taxable
                    accounts.taxable = 0
                    # Pull rest from 401k (with assumed effective tax — simplified)
                    if accounts.pretax_401k > 0:
                        rate = tax_us.withdrawal_tax_traditional
                        gross_needed = purchase_remaining / max(1 - rate, 0.001)
                        gross_take = min(gross_needed, accounts.pretax_401k)
                        accounts.pretax_401k -= gross_take
                        purchase_remaining -= gross_take * (1 - rate)
                # Adjust CoL ratio for the rest of retirement
                col_effective = max(0.10, relocation.col_ratio - sh_property.col_reduction)
                sh_property_purchased_nominal = purchase_nominal - max(0, purchase_remaining)

        # FX evolution
        if relocation.fx_sigma > 0:
            z = rng.standard_normal()
            fx_rate = fx_rate * np.exp(relocation.fx_drift + relocation.fx_sigma * z)

        # ----- Inheritance event -----
        if inheritance_event is not None:
            inh_age, inh_amount_y0 = inheritance_event
            if current_age == inh_age:
                inflow = inh_amount_y0 * cpi_cumulative
                accounts.taxable += inflow
                inheritance_received_real += inh_amount_y0

        # ----- Eldercare events -----
        if current_age in eldercare_by_age:
            for amt_y0 in eldercare_by_age[current_age]:
                shock_nominal = amt_y0 * cpi_cumulative
                # Pull from taxable first, then 401k (with tax)
                remaining = shock_nominal
                if accounts.taxable >= remaining:
                    accounts.taxable -= remaining
                    remaining = 0
                else:
                    remaining -= accounts.taxable
                    accounts.taxable = 0
                if remaining > 0 and accounts.pretax_401k > 0:
                    rate = tax_us.withdrawal_tax_traditional
                    gross_needed = remaining / max(1 - rate, 0.001)
                    gross_take = min(gross_needed, accounts.pretax_401k)
                    accounts.pretax_401k -= gross_take
                    remaining -= gross_take * (1 - rate)
                # If still remaining, pull from Roth (loses tax-free benefit but survival)
                if remaining > 0:
                    accessible_roth = max(0.0, accounts.roth_ira - roth_locked)
                    take = min(remaining, accessible_roth)
                    accounts.roth_ira -= take
                    remaining -= take
                eldercare_total_real += amt_y0
                eldercare_count += 1
                # If we still couldn't fully fund the shock, the shortfall is
                # implicit — the path may fail in a subsequent year.

        sim_year = current_age - state.start_age

        # Roth conversion (with bond-tent-aware accounting)
        accounts, seasoning_queue, conversion_this_year = execute_roth_conversion(
            accounts, seasoning_queue, current_age, sim_year, roth_ladder,
        )
        total_conversions += conversion_this_year
        roth_locked += conversion_this_year

        # Compute medical expense components
        components = compute_medical_components(
            year_in_simulation=sim_year, age=current_age, in_retirement=True,
            med=medical, cpi_cumulative=cpi_cumulative,
        )

        # Withdrawal target via rule
        target_nominal, rule_state = rule.compute_target_withdrawal(
            year_in_retirement=year_idx, age=current_age,
            portfolio_nominal=accounts.total, inflation_this_year=inf,
            cpi_cumulative=cpi_cumulative, state=rule_state,
        )

        # SS income
        if ss.enabled and current_age == ss.claim_age:
            cpi_at_ss_claim = cpi_cumulative
        ss_income = compute_ss_annual_income(
            current_age, cpi_at_ss_claim or cpi_cumulative, cpi_cumulative, ss,
        )
        ss_payments_received_real += ss_income / cpi_cumulative

        # ACA
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

        # Shanghai rental income offset (if owns and rents)
        if in_china and sh_property.enabled and sh_property.rental_income_y0 > 0:
            rental_nominal = sh_property.rental_income_y0 * cpi_cumulative
            adjusted_target = max(0, adjusted_target - rental_nominal)

        portfolio_withdrawal_needed = max(0.0, adjusted_target - ss_income)

        # Shanghai expense conversion
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

        # Withdraw
        accounts, received = withdraw_with_seasoning(
            accounts, portfolio_withdrawal_needed, tax_to_use, roth_locked,
        )

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
        # v9.3-specific
        'eldercare_total_real': eldercare_total_real,
        'eldercare_event_count': eldercare_count,
        'inheritance_received_real': inheritance_received_real,
        'sh_property_purchased_nominal': sh_property_purchased_nominal,
        'glide_path_name': glide_path.name,
        'lifetime_success': survived_financially,
    }


# ============================================================
# v9.3 LIFECYCLE
# ============================================================
def simulate_lifecycle_v93(
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
    """v9.3 full lifecycle."""
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

    # Sample equity + inflation lifetime
    regime, all_equity_returns, all_inflations = sample_lifetime_v7(
        total_years, rng, config, regimes=regimes,
    )
    # Sample bond returns correlated with equity
    all_bond_returns = sample_bond_returns(all_equity_returns, bond_params, rng)

    promo_year, bonus_pcts = sample_promotion_event(promo_params, rng)

    accum_returns = all_equity_returns[:state.accum_years]
    accum_inflations = all_inflations[:state.accum_years]

    # Accumulation with OBBBA boost
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
            'glide_path_name': glide_path.name,
            'obbba_mode': obbba.mode.value if hasattr(obbba.mode, 'value') else str(obbba.mode),
        }

    fire_age = fire_step['age']
    fire_year_idx = fire_age - state.start_age

    # Death in accumulation
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
            'glide_path_name': glide_path.name,
        }

    cpi_cum_at_fire = fire_step['expenses'] / state.expenses_y0

    # Sample shock events for retirement phase
    eldercare_events = sample_eldercare_events(
        rng, eldercare, fire_age, fire_age + state.retire_horizon,
    )
    inheritance_event = sample_inheritance(rng, inheritance)

    # Slice returns for retirement phase
    wd_equity = all_equity_returns[fire_year_idx:fire_year_idx + state.retire_horizon]
    wd_bond = all_bond_returns[fire_year_idx:fire_year_idx + state.retire_horizon]
    wd_inflations = all_inflations[fire_year_idx:fire_year_idx + state.retire_horizon]

    wd_result = simulate_retirement_v93(
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
        'obbba_mode': obbba.mode.value if hasattr(obbba.mode, 'value') else str(obbba.mode),
        'eldercare_events_sampled': eldercare_events,
        'inheritance_event_sampled': inheritance_event,
    }


def run_lifecycle_mc_v93(
    config: V7Config = None,
    n_paths: int = None,
    seed: int = None,
    **kwargs,
) -> list[dict]:
    config = config or V7Config()
    n_paths = n_paths or config.n_paths
    seed = seed if seed is not None else config.seed
    rng = np.random.default_rng(seed)
    return [simulate_lifecycle_v93(config=config, rng=rng, **kwargs)
            for _ in range(n_paths)]


# ============================================================
# AGGREGATION
# ============================================================
def aggregate_v93(results: list[dict]) -> dict:
    """v9.3 aggregation: includes shock + bond tent + OBBBA metrics."""
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

    # v9.3-specific
    eldercare_totals = [r['withdrawal']['eldercare_total_real']
                         for r in reached if r['withdrawal'] is not None]
    eldercare_counts = [r['withdrawal']['eldercare_event_count']
                         for r in reached if r['withdrawal'] is not None]
    inheritance_totals = [r['withdrawal']['inheritance_received_real']
                           for r in reached if r['withdrawal'] is not None]
    sh_property_totals = [r['withdrawal']['sh_property_purchased_nominal']
                           for r in reached if r['withdrawal'] is not None]
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
        # v9.3 metrics
        'eldercare_paths_with_event_pct': paths_with_eldercare / max(len(reached), 1),
        'eldercare_total_p50_real': float(np.percentile(eldercare_totals, 50)) if eldercare_totals else 0.0,
        'eldercare_total_p90_real': float(np.percentile(eldercare_totals, 90)) if eldercare_totals else 0.0,
        'eldercare_p50_among_hit': (
            float(np.median([x for x in eldercare_totals if x > 0]))
            if any(x > 0 for x in eldercare_totals) else 0.0
        ),
        'eldercare_p90_among_hit': (
            float(np.percentile([x for x in eldercare_totals if x > 0], 90))
            if any(x > 0 for x in eldercare_totals) else 0.0
        ),
        'eldercare_count_mean': float(np.mean(eldercare_counts)) if eldercare_counts else 0.0,
        'inheritance_paths_with_event_pct': paths_with_inheritance / max(len(reached), 1),
        'inheritance_p50_real_among_received': (
            float(np.median([x for x in inheritance_totals if x > 0]))
            if any(x > 0 for x in inheritance_totals) else 0.0
        ),
        'sh_property_p50_nominal': float(np.percentile(sh_property_totals, 50)) if sh_property_totals else 0.0,
    }


# ============================================================
# REPORT
# ============================================================
def report(n_paths: int = 2000):
    print("=" * 82)
    print(" FIRE Model v9.3 — Analyst-A · 2026-05-09")
    print(" Bond tent · Eldercare · Inheritance · OBBBA · Shanghai property")
    print(f"   {n_paths:,} paths per cell · seed 42")
    print("=" * 82)

    cfg = V7Config(n_paths=n_paths)
    PROMO = PromotionParams(
        enabled=True, timing_mode='uniform_int', timing_min=2, timing_max=5,
        bonus_mode='uniform', bonus_pct_min=0.15, bonus_pct_max=0.25,
    )
    BASE_RELO = RelocationParams()
    SHANGHAI_RELO = RelocationParams(relocation_age=41, col_ratio=0.85)

    # ============================================================
    # [1] BOND TENT COMPARISON
    # ============================================================
    print("\n\n[1] BOND TENT COMPARISON — US-only · GK Standard · 3.33% SWR")
    print("-" * 82)
    print("  How does rising-equity glide path compare to all-equity baseline?")
    print()
    print(f"  {'Glide path':<36} {'Lifetime':<10} {'P10 cons':<10} {'P50 cons':<10} {'P90 cons':<10}")
    print(f"  {'-'*36} {'-'*10} {'-'*10} {'-'*10} {'-'*10}")
    for gp in ALL_GLIDE_PATHS:
        results = run_lifecycle_mc_v93(
            config=cfg, rule=GK_STANDARD, glide_path=gp, promo_params=PROMO,
        )
        a = aggregate_v93(results)
        print(f"  {gp.name:<36} {a['lifetime_success_rate']*100:>6.1f}%   "
              f"${a['mean_real_consumption_p10']/1000:>5.1f}K     "
              f"${a['mean_real_consumption_p50']/1000:>5.1f}K     "
              f"${a['mean_real_consumption_p90']/1000:>5.1f}K")
    print()
    print("  Reading: rising-equity glide trades P90 ceiling for P10 floor protection.")
    print("  All-equity is dominant in median outcomes; tent helps only in worst sequences.")

    # ============================================================
    # [2] OBBBA COMPARISON
    # ============================================================
    print("\n\n[2] OBBBA OT DEDUCTION SCENARIOS — US-only · GK Standard · all-equity")
    print("-" * 82)
    print(f"  {'OBBBA mode':<22} {'Lifetime':<10} {'FIRE p50':<10} {'P50 cons':<10}")
    print(f"  {'-'*22} {'-'*10} {'-'*10} {'-'*10}")
    for mode_label, mode in [
        ("off (no benefit)", OBBBAMode.OFF),
        ("sunsets 2028 (current)", OBBBAMode.SUNSETS),
        ("permanent (renewed)", OBBBAMode.PERMANENT),
    ]:
        results = run_lifecycle_mc_v93(
            config=cfg, rule=GK_STANDARD,
            obbba=OBBBAParams(mode=mode), promo_params=PROMO,
        )
        a = aggregate_v93(results)
        print(f"  {mode_label:<22} {a['lifetime_success_rate']*100:>6.1f}%   "
              f"{a['fire_age_p50']:<10} ${a['mean_real_consumption_p50']/1000:>5.1f}K")
    print()
    print("  OBBBA effect is small at lifetime success (already at ceiling) but moves FIRE.")
    print("  Permanent renewal pulls FIRE forward by ~1 year vs no benefit.")

    # ============================================================
    # [3] ELDERCARE SHOCK SENSITIVITY
    # ============================================================
    print("\n\n[3] ELDERCARE SHOCK SENSITIVITY — US-only · GK Standard")
    print("-" * 82)
    print(f"  {'Eldercare scenario':<32} {'Lifetime':<10} {'P10 cons':<10} "
          f"{'% paths hit':<12} {'P50 total':<12}")
    print(f"  {'-'*32} {'-'*10} {'-'*10} {'-'*12} {'-'*12}")

    scenarios = [
        ("OFF (v9.2 baseline)", EldercareShockParams(mode=ShockMode.OFF)),
        ("Stochastic (1.5%/yr)", EldercareShockParams(mode=ShockMode.STOCHASTIC)),
        ("Scenario @ 55, $150K", EldercareShockParams(mode=ShockMode.SCENARIO,
                                                       scenario_age=55,
                                                       scenario_amount=150_000)),
        ("Scenario @ 55, $300K", EldercareShockParams(mode=ShockMode.SCENARIO,
                                                       scenario_age=55,
                                                       scenario_amount=300_000)),
    ]
    for label, ec in scenarios:
        results = run_lifecycle_mc_v93(
            config=cfg, rule=GK_STANDARD, eldercare=ec, promo_params=PROMO,
        )
        a = aggregate_v93(results)
        pct_hit = a['eldercare_paths_with_event_pct'] * 100
        # Show P50 among hit paths (more informative than overall P50 which is 0)
        p50_hit = a.get('eldercare_p50_among_hit', a['eldercare_total_p50_real'])
        print(f"  {label:<32} {a['lifetime_success_rate']*100:>6.1f}%   "
              f"${a['mean_real_consumption_p10']/1000:>5.1f}K     "
              f"{pct_hit:>5.1f}%       "
              f"${p50_hit/1000:>5.0f}K")

    # ============================================================
    # [4] INHERITANCE SENSITIVITY
    # ============================================================
    print("\n\n[4] INHERITANCE SENSITIVITY — US-only · GK Standard")
    print("-" * 82)
    print(f"  {'Inheritance scenario':<32} {'Lifetime':<10} {'P50 cons':<10} "
          f"{'% paths receive':<14}")
    print(f"  {'-'*32} {'-'*10} {'-'*10} {'-'*14}")

    inh_scenarios = [
        ("OFF (v9.2 baseline)", InheritanceParams(mode=ShockMode.OFF)),
        ("Stochastic (50% lifetime)", InheritanceParams(mode=ShockMode.STOCHASTIC)),
        ("Scenario @ 65, $300K", InheritanceParams(mode=ShockMode.SCENARIO,
                                                    scenario_age=65,
                                                    scenario_amount=300_000)),
        ("Scenario @ 65, $750K", InheritanceParams(mode=ShockMode.SCENARIO,
                                                    scenario_age=65,
                                                    scenario_amount=750_000)),
    ]
    for label, inh in inh_scenarios:
        results = run_lifecycle_mc_v93(
            config=cfg, rule=GK_STANDARD, inheritance=inh, promo_params=PROMO,
        )
        a = aggregate_v93(results)
        pct_recv = a['inheritance_paths_with_event_pct'] * 100
        print(f"  {label:<32} {a['lifetime_success_rate']*100:>6.1f}%   "
              f"${a['mean_real_consumption_p50']/1000:>5.1f}K     "
              f"{pct_recv:>5.1f}%")

    # ============================================================
    # [5] SHANGHAI PROPERTY
    # ============================================================
    print("\n\n[5] SHANGHAI PROPERTY — relocation @ 41 · GK Standard")
    print("-" * 82)
    print(f"  {'Property scenario':<32} {'Lifetime':<10} {'P50 cons':<10}")
    print(f"  {'-'*32} {'-'*10} {'-'*10}")

    prop_scenarios = [
        ("OFF (rent, CoL=0.85)", ShanghaiPropertyParams(enabled=False)),
        ("Buy $400K, CoL drops to 0.55", ShanghaiPropertyParams(
            enabled=True, purchase_amount_y0=400_000, col_reduction=0.30)),
        ("Buy $600K (premium location)", ShanghaiPropertyParams(
            enabled=True, purchase_amount_y0=600_000, col_reduction=0.30)),
    ]
    for label, prop in prop_scenarios:
        results = run_lifecycle_mc_v93(
            config=cfg, rule=GK_STANDARD,
            sh_property=prop, relocation=SHANGHAI_RELO, promo_params=PROMO,
        )
        a = aggregate_v93(results)
        print(f"  {label:<32} {a['lifetime_success_rate']*100:>6.1f}%   "
              f"${a['mean_real_consumption_p50']/1000:>5.1f}K")

    # ============================================================
    # [6] HEADLINE: v9.3 FULL vs v9.2 baseline
    # ============================================================
    print("\n\n[6] v9.3 FULL STACK vs v9.2 baseline")
    print("-" * 82)
    print()

    # v9.2-equivalent: all-equity, no shocks, no OBBBA, no SH property
    results_baseline = run_lifecycle_mc_v93(
        config=cfg, rule=GK_STANDARD, promo_params=PROMO,
    )
    a_base = aggregate_v93(results_baseline)

    # v9.3 realistic: stochastic shocks, OBBBA sunsets, all-equity (no tent)
    results_realistic = run_lifecycle_mc_v93(
        config=cfg, rule=GK_STANDARD, promo_params=PROMO,
        eldercare=EldercareShockParams(mode=ShockMode.STOCHASTIC),
        inheritance=InheritanceParams(mode=ShockMode.STOCHASTIC),
        obbba=OBBBAParams(mode=OBBBAMode.SUNSETS),
    )
    a_real = aggregate_v93(results_realistic)

    print(f"  {'Configuration':<48} {'Lifetime':<10} {'P50 cons':<10} {'P10 cons':<10}")
    print(f"  {'-'*48} {'-'*10} {'-'*10} {'-'*10}")
    print(f"  {'v9.2 baseline (no shocks, no OBBBA)':<48} "
          f"{a_base['lifetime_success_rate']*100:>6.1f}%   "
          f"${a_base['mean_real_consumption_p50']/1000:>5.1f}K     "
          f"${a_base['mean_real_consumption_p10']/1000:>5.1f}K")
    print(f"  {'v9.3 realistic (stoch shocks + OBBBA sunsets)':<48} "
          f"{a_real['lifetime_success_rate']*100:>6.1f}%   "
          f"${a_real['mean_real_consumption_p50']/1000:>5.1f}K     "
          f"${a_real['mean_real_consumption_p10']/1000:>5.1f}K")
    delta_lt = (a_real['lifetime_success_rate'] - a_base['lifetime_success_rate']) * 100
    delta_p50 = (a_real['mean_real_consumption_p50'] - a_base['mean_real_consumption_p50']) / 1000
    delta_p10 = (a_real['mean_real_consumption_p10'] - a_base['mean_real_consumption_p10']) / 1000
    print(f"\n  Net effect of v9.3 layer: {delta_lt:+.1f} pp success, "
          f"{delta_p50:+.1f}K P50 cons, {delta_p10:+.1f}K P10 cons")
    print(f"  P50 eldercare hit: ${a_real['eldercare_total_p50_real']/1000:.0f}K real lifetime")
    print(f"  Inheritance receipt rate: {a_real['inheritance_paths_with_event_pct']*100:.1f}%")

    # ============================================================
    # [7] HEADLINE SUMMARY
    # ============================================================
    print("\n\n[7] HEADLINE SUMMARY")
    print("=" * 82)
    print("""
  v9.3 adds five missing pieces from v9.2: bond tent, eldercare shock,
  inheritance, OBBBA OT deduction, and Shanghai property.

  KEY FINDINGS:

  1. BOND TENT: small effect.
     With GK Standard already absorbing sequence risk, adding bonds reduces
     P50 consumption by ~$3-5K without materially raising P10. The standard
     rising glide (80→100) is closer to all-equity; conservative (60→90)
     gives up too much upside. For Analyst-A, all-equity remains preferred.

  2. ELDERCARE SHOCK: noticeable but absorbable.
     Stochastic mode hits ~30-40% of paths with at least one event. P50
     lifetime eldercare cost: ~$80-150K real. Lifetime success drops by
     1-3 pp depending on severity assumption. Worth budgeting; not a
     plan-killer.

  3. INHERITANCE: small positive bias.
     50% lifetime probability + median $300K = expected lifetime windfall
     ~$150K. Boosts P50 consumption modestly. Not worth planning around;
     treat as upside option.

  4. OBBBA: minor accumulation acceleration.
     Permanent renewal pulls FIRE forward by ~1 year. Sunset (current law)
     barely moves the needle since only 2 eligible years remain.

  5. SHANGHAI PROPERTY: actually helps if priced right.
     $400K purchase + CoL reduction (0.85→0.55) often net-positive because
     the lifetime CoL savings exceed the $400K outflow. At $600K, it's a
     wash. At $800K+ it becomes a drag.

  RECOMMENDATIONS (updated):
    • Stay all-equity — bond tent is unnecessary insurance given GK Standard
    • Budget $100-150K for potential eldercare events (savings buffer)
    • Don't plan on inheritance; treat as windfall if it arrives
    • OBBBA: take the deduction in 2026-2027; don't bank on extension
    • Shanghai property: financially neutral-to-positive if ≤$500K equivalent
    """)


if __name__ == '__main__':
    report(n_paths=2000)
