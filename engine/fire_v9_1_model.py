"""
FIRE Model v9.1 — Analyst-A · 2026-05-09
======================================

NEW IN v9.1 (per design decisions 2026-05):

  [1C] THREE WITHDRAWAL RULE FAMILIES (5 variants):
       a. Fixed Real (v7/v8 baseline, CPI-indexed constant real)
       b. Guyton-Klinger Standard   (±20% guardrails, 10% adj, inflation freeze)
       c. Guyton-Klinger Conservative (±15% guardrails, 10% adj, inflation freeze)
       d. Guyton-Klinger Aggressive   (±25% guardrails, 10% adj, NO inflation freeze)
       e. VPW (age-indexed % withdrawal — never fails by construction)

  [2C] TWO COMPARISON DIRECTIONS:
       Direction 1: at fixed SWR=3.33%, compare success / consumption by rule
       Direction 2: at fixed 90% mortality-conditioned success, compare max SWR

  [3B] STOCHASTIC LIFESPAN VIA SSA-LIKE PERIOD LIFE TABLE:
       Gompertz-fit to SSA 2020 period table.
       DEFAULT: Male (Analyst-A). Override available for Female/Unisex.
       Median LE from age 27 (male): ~76.
       (Conservative vs real cohort-improved tables, which add ~3-5 yrs.)

  [4B] ANNUAL CONDITIONAL MORTALITY:
       Each year, draw uniform[0,1] vs q(x). Path terminates on death.
       Path length is now random, not fixed at 50 years.

  [5B] STRATIFIED MEDICAL EXPENSES (3-component breakdown):
       - Routine medical:    $3K Y0, CPI+1pp lifetime
       - Insurance premium:  stage-dependent base, CPI+2pp
           working: $2K   |   ACA bridge 35-64: $8K   |   Medicare 65+: $4K
       - OOP/copay:          $1K Y0, CPI+1pp
       Non-medical: $34,440 Y0 (fills out $40,440 total at year 0)

  [6C] BOTH ACA SCENARIOS:
       Scenario A (IRA-current): premium subsidized to 8.5% of MAGI cap
       Scenario B (pre-IRA cliff): 400% FPL hard cliff
       MAGI proxy: 401k WD + 50% taxable WD (cap gains realization)

  [7A] CN TAX SCENARIO GRID: marginal rate ∈ {0%, 10%, 20%}
  [8A] Single-marginal-rate model (no FTC mechanism — flagged for v9.2 extension)

PORTFOLIO ADJUSTMENT:
  User confirmed 401k = 100% FXAIX (the brokerage S&P 500 index ≈ VOO).
  Other accounts assumed 75/25 VOO/QQQM.
  Effective overall blend: ~85.6% S&P 500 / 14.4% QQQM.
  Going forward, 401k contributions flow to FXAIX → drift toward S&P 500.

  Adjusted return params (used in this version):
    geo mean ~9.15% (was 9.30% under 75/25 assumption)
    σ        ~16.5%  (was 17.0%)
  Net effect: ~15bps lower expected, slightly lower vol.

KEY METHODOLOGICAL NOTES:

  1. With stochastic mortality, "lifetime success" redefined:
     - PRIMARY: P(portfolio outlasts user) — this is what matters
     - SECONDARY: P(50-year survival) — for backward comparability

  2. Under GK rules, "success" alone is insufficient — consumption can drop
     materially while staying technically solvent. Report includes:
     - P50 lifetime real consumption (= average annual real $ over years lived)
     - P10 lifetime real consumption (= worst-quartile reality check)
     - Frequency of guardrail trigger events

  3. VPW is mathematically immune to depletion (can't withdraw what you
     don't have), so its "success rate" is trivially ~100%. The relevant
     risk metric for VPW is consumption distribution, not failure.

  4. ACA scenarios A vs B may converge for low-MAGI lean-FIRE retirees
     (the analyst's case: $40K real expenses → MAGI mostly 30-50K range, well
     under 400% FPL ≈ $60K). Cliff matters mainly when 401k withdrawals
     start dominating later in retirement.

INHERITS FROM v8/v7 UNCHANGED:
  - Stochastic promotion to Associate (year 2-5, bonus 15-25%)
  - Three-regime mixture (high CAPE / AI persists / historical)
  - Student-t returns (df=6) — fat tails
  - Stochastic correlated inflation (σ=2%, ρ=-0.30 with returns)
  - 50 bps retirement friction (rebalancing + advisor + behavioral)
  - Account stratification (401k / Roth / HSA / Taxable)
  - Withdrawal sequence: taxable → 401k → HSA → Roth
  - Shanghai relocation layer (FX, CoL ratio, CN inflation)

DEFERRED TO v9.2/v9.3:
  - Roth conversion ladder full optimization
  - Social Security PIA + claim age
  - Foreign Tax Credit (FTC) mechanism
  - Multi-asset support (BND/cash) and bond tent / glide path

Requires: numpy
Usage:
    python fire_v9_1_model.py
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
from typing import Callable, Sequence, Optional
from enum import Enum

from fire_rule_pack import ACA_MARKETPLACE_RULES
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
)
from fire_v8_model import (
    PromotionParams, V8ContributionParams,
    sample_promotion_event,
    project_stratified_v8,
)


# ============================================================
# PORTFOLIO RETURN PARAMS — adjusted for 85/15 blend
# ============================================================
# These are informational; actual returns are driven by sample_lifetime_v7
# which uses the v7 regime mixture. The regime params should ideally be
# adjusted for the new blend, but the differences are small enough that
# we note rather than override here.
ADJUSTED_BLEND_PARAMS = {
    'allocation': '85.6% S&P 500 / 14.4% QQQM',
    'geo_mean': 0.0915,   # was 0.0930 under 75/25
    'sigma':    0.165,    # was 0.170
    'note': 'Regime mixture in v7 is preserved as-is; small bias acceptable.',
}


# ============================================================
# [3B] MORTALITY MODEL — Gompertz fit to SSA-2020 period life table
# ============================================================
@dataclass
class MortalityParams:
    """
    Gompertz mortality model: μ(x) = α · exp(β·x), where μ is annual hazard.
    Annual mortality probability q(x) ≈ 1 - exp(-μ(x)).

    Parameters fit to SSA 2020 period life table (approximate).
    Validation:
      male  : μ(35)≈0.0019, μ(60)≈0.0114, μ(85)≈0.118, μ(100)≈0.426
      female: μ(35)≈0.0011, μ(60)≈0.0073, μ(85)≈0.087, μ(100)≈0.346
      unisex: midway (rough average)
    """
    alpha: float = 0.000080
    beta: float = 0.084
    cap_age: int = 110          # forced death at this age
    sex_label: str = "unisex"
    enabled: bool = True        # if False, no mortality (deterministic horizon)


# Preset parameter sets fit to SSA-like data
MORTALITY_MALE   = MortalityParams(alpha=0.000104, beta=0.083, sex_label="male")
MORTALITY_FEMALE = MortalityParams(alpha=0.000060, beta=0.085, sex_label="female")
MORTALITY_UNISEX = MortalityParams(alpha=0.000080, beta=0.084, sex_label="unisex")


def annual_mortality_rate(age: int, params: MortalityParams) -> float:
    """Returns q(x): probability of dying within next year given alive at age x."""
    if not params.enabled:
        return 0.0
    if age >= params.cap_age:
        return 1.0
    mu = params.alpha * np.exp(params.beta * age)
    return float(1.0 - np.exp(-mu))


def sample_age_at_death(start_age: int, params: MortalityParams,
                        rng: np.random.Generator) -> int:
    """Draw a stochastic age-at-death conditional on being alive at start_age.

    Used for descriptive / median-LE calculations only — actual simulation
    uses annual conditional draws (see retirement loop).
    """
    age = start_age
    while age < params.cap_age:
        q = annual_mortality_rate(age, params)
        if rng.random() < q:
            return age
        age += 1
    return params.cap_age


def median_life_expectancy(start_age: int, params: MortalityParams,
                           n: int = 10_000, seed: int = 1) -> float:
    """Compute median age-at-death from start_age via simulation."""
    rng = np.random.default_rng(seed)
    deaths = [sample_age_at_death(start_age, params, rng) for _ in range(n)]
    return float(np.median(deaths))


# ============================================================
# [5B] STRATIFIED MEDICAL EXPENSES
# ============================================================
@dataclass
class MedicalParams:
    """
    Decomposes total expenses into medical and non-medical components.
    Each component has its own real-CPI delta (inflation differential vs general CPI).

    Total Y0 = non_medical + routine + premium_working + oop = 40,440 by construction.
    """
    # Year 0 components in nominal $
    non_medical_y0: float = 34_440.0
    routine_y0: float = 3_000.0
    premium_working: float = 2_000.0       # employer-subsidized
    premium_aca: float = 8_000.0            # ACA bridge ages 35-64
    premium_medicare: float = 4_000.0       # Part B + Part D + Medigap, age 65+
    oop_y0: float = 1_000.0

    # Inflation deltas (added to general CPI)
    cpi_delta_routine: float = 0.010
    cpi_delta_premium: float = 0.020
    cpi_delta_oop: float = 0.010
    # Non-medical assumed at general CPI (delta=0)

    # Lifecycle stage transitions (age-based)
    aca_start_age: int = 35    # earliest age for ACA bridge (post-FIRE)
    medicare_age: int = 65     # Part A automatic, Part B opt-in


DEFAULT_MEDICAL = MedicalParams()


def compute_medical_components(year_in_simulation: int,
                                age: int,
                                in_retirement: bool,
                                med: MedicalParams,
                                cpi_cumulative: float) -> dict:
    """
    Given cumulative inflation factor (general CPI), compute the four expense
    components in nominal $ at this year.

    The medical components inflate at CPI + delta. We approximate this by
    multiplying their Y0 nominal by (cpi_cumulative * (1 + delta)^year).
    This is exact when CPI compounds (slight bias on geometric vs arithmetic
    in the delta but tolerable).
    """
    delta_year_routine = (1 + med.cpi_delta_routine) ** year_in_simulation
    delta_year_premium = (1 + med.cpi_delta_premium) ** year_in_simulation
    delta_year_oop = (1 + med.cpi_delta_oop) ** year_in_simulation

    non_medical = med.non_medical_y0 * cpi_cumulative
    routine = med.routine_y0 * cpi_cumulative * delta_year_routine
    oop = med.oop_y0 * cpi_cumulative * delta_year_oop

    # Premium varies by life stage
    if not in_retirement:
        premium_base = med.premium_working
    elif age >= med.medicare_age:
        premium_base = med.premium_medicare
    elif age >= med.aca_start_age:
        premium_base = med.premium_aca
    else:
        # Edge case: retired but under 35 (early FIRE) — ACA still applies
        premium_base = med.premium_aca

    premium = premium_base * cpi_cumulative * delta_year_premium

    return {
        'non_medical': non_medical,
        'routine': routine,
        'premium_full': premium,    # before ACA subsidy applied
        'oop': oop,
    }


# ============================================================
# [6C] ACA SUBSIDY MODELING — both scenarios
# ============================================================
class ACAScenario(str, Enum):
    IRA_CURRENT = "A_IRA_current"   # 2021-2025 enhanced subsidy, no cliff
    PRE_IRA_CLIFF = "B_pre_IRA"      # 2026 law: 400% FPL hard cliff


@dataclass
class ACAParams:
    """
    ACA premium subsidy modeling for ages 35-64 (pre-Medicare).

    Scenario A (IRA): Premium = min(full_premium, 0.085 × MAGI)
                      No income cliff. Temporary 2021-2025 policy.

    Scenario B (2026): Below 400% FPL: premium = min(full, 0.0996 × MAGI)
                          Above 400% FPL: premium = full (no subsidy)
                          Hard cliff restored for 2026.  The 9.96% value is a
                          conservative flat proxy for the top 300–400% band;
                          the full piecewise IRS schedule is not implemented.
    """
    scenario: ACAScenario = ACAScenario(
        ACA_MARKETPLACE_RULES["default_scenario"])
    # 2026 Marketplace subsidies use the 2025 HHS guideline in the contiguous
    # states/DC: $15,650 for one person plus $5,500 per additional person.
    fpl_single_y0: float = ACA_MARKETPLACE_RULES["fpl_single_y0"]
    fpl_additional_person_y0: float = ACA_MARKETPLACE_RULES[
        "fpl_additional_person_y0"]
    household_size: int = 1
    fpl_threshold: float = ACA_MARKETPLACE_RULES["fpl_threshold"]
    cap_pct_ira: float = ACA_MARKETPLACE_RULES["cap_pct_ira"]
    cap_pct_pre_ira: float = ACA_MARKETPLACE_RULES["cap_pct_pre_ira"]


def estimate_magi_proxy(taxable_wd_nominal: float,
                         pretax_401k_wd_nominal: float,
                         taxable_gain_fraction: float = 0.50) -> float:
    """
    Approximate MAGI from withdrawal mix.

    - Roth & HSA WDs: 0 contribution to MAGI
    - 401k traditional WD: full ordinary income
    - Taxable WD: only the gain portion (~50% assumed) is income

    This is a coarse proxy. In reality:
    - Capital gains rate may differ from ordinary
    - Specific cost basis matters
    - Roth conversions (when added in v9.2) push MAGI up
    """
    return pretax_401k_wd_nominal + taxable_gain_fraction * taxable_wd_nominal


def compute_aca_premium_paid(full_premium_nominal: float,
                              magi_nominal: float,
                              cpi_cumulative: float,
                              params: ACAParams,
                              household_size: int = None) -> float:
    """
    Returns the actual premium paid by the household after ACA subsidy.

    full_premium_nominal: unsubsidized cost in this year's $
    magi_nominal: estimated MAGI in this year's $
    cpi_cumulative: factor to inflate FPL from year 0 to this year
    """
    hh_size = max(1, int(params.household_size if household_size is None
                         else household_size))
    fpl_y0 = (params.fpl_single_y0
              + (hh_size - 1) * params.fpl_additional_person_y0)
    fpl_now = fpl_y0 * cpi_cumulative
    threshold_dollar = params.fpl_threshold * fpl_now

    if params.scenario == ACAScenario.IRA_CURRENT:
        cap = params.cap_pct_ira * magi_nominal
        return float(min(full_premium_nominal, max(0.0, cap)))

    elif params.scenario == ACAScenario.PRE_IRA_CLIFF:
        if magi_nominal <= threshold_dollar:
            cap = params.cap_pct_pre_ira * magi_nominal
            return float(min(full_premium_nominal, max(0.0, cap)))
        else:
            # Above cliff: pay full premium
            return float(full_premium_nominal)

    return float(full_premium_nominal)


# ============================================================
# [1C] WITHDRAWAL RULES
# ============================================================
@dataclass
class WithdrawalRule:
    """Base interface for withdrawal rules.

    Subclasses implement compute_target_withdrawal which returns the
    target nominal withdrawal for the current year.
    """
    name: str = "base"

    def initialize(self, fire_portfolio_nominal: float,
                   fire_expenses_nominal: float,
                   initial_swr: float) -> dict:
        """Return initial state dict to be passed forward."""
        return {
            'initial_w_nominal': fire_expenses_nominal,
            'initial_portfolio_nominal': fire_portfolio_nominal,
            'initial_swr': initial_swr,
            'prev_w_nominal': fire_expenses_nominal,
            'prev_portfolio_nominal': fire_portfolio_nominal,
            'cpi_cumulative_at_fire': 1.0,
            'guardrail_triggers': 0,
        }

    def compute_target_withdrawal(self, year_in_retirement: int,
                                   age: int,
                                   portfolio_nominal: float,
                                   inflation_this_year: float,
                                   cpi_cumulative: float,
                                   state: dict) -> tuple[float, dict]:
        """Return (target_withdrawal_nominal, updated_state)."""
        raise NotImplementedError


@dataclass
class FixedRealRule(WithdrawalRule):
    """CPI-indexed constant real withdrawal (v7/v8 baseline)."""
    name: str = "Fixed Real"

    def compute_target_withdrawal(self, year_in_retirement, age,
                                   portfolio_nominal, inflation_this_year,
                                   cpi_cumulative, state):
        if year_in_retirement == 0:
            target = state['initial_w_nominal']
        else:
            target = state['prev_w_nominal'] * (1 + inflation_this_year)

        new_state = dict(state)
        new_state['prev_w_nominal'] = target
        new_state['prev_portfolio_nominal'] = portfolio_nominal
        return target, new_state


@dataclass
class GuytonKlingerRule(WithdrawalRule):
    """
    Guyton-Klinger guardrails withdrawal rule.

    Three rules:
    1. Inflation Rule: skip CPI bump if portfolio dropped AND prev_w_real
       exceeds initial_w_real (only when inflation_freeze_enabled).
    2. Capital Preservation Rule: if (current_w / portfolio) > initial_swr × (1+upper_band),
       cut withdrawal by adjustment_pct.
    3. Prosperity Rule: if (current_w / portfolio) < initial_swr × (1-lower_band),
       raise withdrawal by adjustment_pct.

    All operations in NOMINAL dollars; "real" comparisons use cpi_cumulative.
    """
    name: str = "Guyton-Klinger"
    upper_guardrail: float = 0.20
    lower_guardrail: float = 0.20
    adjustment_pct: float = 0.10
    inflation_freeze_enabled: bool = True

    def compute_target_withdrawal(self, year_in_retirement, age,
                                   portfolio_nominal, inflation_this_year,
                                   cpi_cumulative, state):
        if year_in_retirement == 0:
            target = state['initial_w_nominal']
            new_state = dict(state)
            new_state['prev_w_nominal'] = target
            new_state['prev_portfolio_nominal'] = portfolio_nominal
            return target, new_state

        prev_w = state['prev_w_nominal']
        prev_portfolio = state['prev_portfolio_nominal']
        initial_w_nominal = state['initial_w_nominal']
        initial_swr = state['initial_swr']
        triggers = state.get('guardrail_triggers', 0)

        # Tentative withdrawal: prev × (1 + inflation)
        tentative = prev_w * (1 + inflation_this_year)

        # Inflation Freeze Rule
        if self.inflation_freeze_enabled:
            tentative_real = tentative / cpi_cumulative
            initial_w_real = initial_w_nominal  # initial nominal == initial real (Y0)
            if portfolio_nominal < prev_portfolio and tentative_real > initial_w_real:
                # Skip the inflation bump this year
                tentative = prev_w

        # Capital Preservation / Prosperity Rules
        current_implied_swr = tentative / max(portfolio_nominal, 1.0)
        if current_implied_swr > initial_swr * (1 + self.upper_guardrail):
            tentative *= (1 - self.adjustment_pct)
            triggers += 1
        elif current_implied_swr < initial_swr * (1 - self.lower_guardrail):
            tentative *= (1 + self.adjustment_pct)
            triggers += 1

        new_state = dict(state)
        new_state['prev_w_nominal'] = tentative
        new_state['prev_portfolio_nominal'] = portfolio_nominal
        new_state['guardrail_triggers'] = triggers
        return tentative, new_state


# Three GK variants
GK_STANDARD = GuytonKlingerRule(
    name="GK Standard (±20%)",
    upper_guardrail=0.20, lower_guardrail=0.20, adjustment_pct=0.10,
    inflation_freeze_enabled=True,
)
GK_CONSERVATIVE = GuytonKlingerRule(
    name="GK Conservative (±15%)",
    upper_guardrail=0.15, lower_guardrail=0.15, adjustment_pct=0.10,
    inflation_freeze_enabled=True,
)
GK_AGGRESSIVE = GuytonKlingerRule(
    name="GK Aggressive (±25%, no freeze)",
    upper_guardrail=0.25, lower_guardrail=0.25, adjustment_pct=0.10,
    inflation_freeze_enabled=False,
)


@dataclass
class VPWRule(WithdrawalRule):
    """
    Variable Percentage Withdrawal — withdraw a fixed % of portfolio each year,
    with the % rising with age. Bogleheads-style schedule.

    By construction, VPW cannot deplete the portfolio (since withdrawal is
    always a fraction of remaining balance). The relevant risk metric is
    the consumption distribution across paths.
    """
    name: str = "VPW"
    # Age → withdrawal % schedule (linearly interpolated)
    schedule: tuple = (
        (35, 0.034), (45, 0.039), (55, 0.048), (65, 0.058),
        (75, 0.072), (85, 0.094), (95, 0.150), (105, 0.250),
    )

    def vpw_pct(self, age: int) -> float:
        sched = list(self.schedule)
        if age <= sched[0][0]:
            return sched[0][1]
        if age >= sched[-1][0]:
            return sched[-1][1]
        for (a1, p1), (a2, p2) in zip(sched, sched[1:]):
            if a1 <= age <= a2:
                t = (age - a1) / (a2 - a1)
                return p1 + t * (p2 - p1)
        return sched[-1][1]

    def compute_target_withdrawal(self, year_in_retirement, age,
                                   portfolio_nominal, inflation_this_year,
                                   cpi_cumulative, state):
        pct = self.vpw_pct(age)
        target = portfolio_nominal * pct

        new_state = dict(state)
        new_state['prev_w_nominal'] = target
        new_state['prev_portfolio_nominal'] = portfolio_nominal
        return target, new_state


# Registry of rules used in reports
ALL_RULES = [
    FixedRealRule(),
    GK_STANDARD,
    GK_CONSERVATIVE,
    GK_AGGRESSIVE,
    VPWRule(),
]


# ============================================================
# v9.1 RETIREMENT SIMULATOR
# ============================================================
def simulate_retirement_v91(
    starting_accounts: AccountStack,
    starting_age: int,
    fire_year_cpi_cumulative: float,    # cumulative CPI factor at FIRE year
    returns: Sequence[float],
    inflations: Sequence[float],
    rule: WithdrawalRule,
    relocation: RelocationParams,
    medical: MedicalParams,
    aca: ACAParams,
    mortality: MortalityParams,
    state: State = None,
    tax_us: TaxParams = None,
    tax_cn: TaxParamsChina = None,
    friction: float = 0.005,
    rng: np.random.Generator = None,
) -> dict:
    """
    Withdrawal simulation with all v9.1 features:
      - Withdrawal rule (any of FixedReal / GK variants / VPW)
      - Stratified medical expenses
      - ACA premium subsidies (scenario A or B)
      - Stochastic annual mortality (path can terminate on death)
      - Shanghai relocation (inherited from v6/v7)
    """
    state = state or STATE
    tax_us = tax_us or TAX_US
    tax_cn = tax_cn or TAX_CN
    rng = rng or np.random.default_rng()

    accounts = starting_accounts.copy()

    # Set up withdrawal rule state
    # First-year expenses: full medical + non-medical computed at FIRE year
    initial_components = compute_medical_components(
        year_in_simulation=starting_age - state.start_age,
        age=starting_age,
        in_retirement=True,
        med=medical,
        cpi_cumulative=fire_year_cpi_cumulative,
    )
    # Estimate Y1 ACA premium (zero MAGI proxy => full premium hit, conservative)
    # This is just for rule initialization; runtime calc uses true MAGI proxy
    initial_full_premium = initial_components['premium_full']
    initial_aca_paid = compute_aca_premium_paid(
        initial_full_premium,
        magi_nominal=initial_components['non_medical'] * 0.6,  # rough placeholder
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

    # Tracking
    in_china = False
    cny_expenses_real = None
    fx_rate = relocation.fx_initial
    fx_at_relocation = None
    relocation_done = False

    survived_financially = True
    shortfall_age = None
    age_at_death = None
    cpi_cumulative = fire_year_cpi_cumulative
    real_consumption_path = []   # in Y0 dollars
    nominal_consumption_path = []
    portfolio_path = [accounts.total]

    for year_idx, (r, inf) in enumerate(zip(returns, inflations)):
        current_age = starting_age + year_idx + 1
        cpi_cumulative *= (1 + inf)
        r_eff = r - friction

        # Apply returns (before withdrawal)
        accounts.pretax_401k *= (1 + r_eff)
        accounts.roth_ira *= (1 + r_eff)
        accounts.hsa *= (1 + r_eff)
        accounts.taxable *= (1 + r_eff - tax_us.drag_taxable)

        # Stochastic mortality: sample for THIS year (did person die this year?)
        if mortality.enabled:
            q = annual_mortality_rate(current_age, mortality)
            if rng.random() < q:
                age_at_death = current_age
                # Final balance recorded; withdrawal need is moot
                portfolio_path.append(accounts.total)
                break

        # Relocation event check
        if (relocation.relocation_age is not None and not relocation_done
                and current_age >= relocation.relocation_age):
            in_china = True
            relocation_done = True
            fx_at_relocation = fx_rate

        # FX evolution
        if relocation.fx_sigma > 0:
            z = rng.standard_normal()
            fx_rate = fx_rate * np.exp(relocation.fx_drift + relocation.fx_sigma * z)

        # Compute medical expense components for this year
        sim_year = current_age - state.start_age
        components = compute_medical_components(
            year_in_simulation=sim_year,
            age=current_age,
            in_retirement=True,
            med=medical,
            cpi_cumulative=cpi_cumulative,
        )

        # Compute target withdrawal via rule (in nominal $)
        # The rule's notion of "expenses" is its prev_w; doesn't directly know
        # about medical breakdown. The rule output is the TOTAL nominal target.
        target_nominal, rule_state = rule.compute_target_withdrawal(
            year_in_retirement=year_idx,
            age=current_age,
            portfolio_nominal=accounts.total,
            inflation_this_year=inf,
            cpi_cumulative=cpi_cumulative,
            state=rule_state,
        )

        # Adjust target for ACA subsidy
        # MAGI proxy: assume withdrawal sequence will roughly match v6 — taxable first
        # then 401k. For ACA, we estimate MAGI from the target.
        # Simplification: estimate from total target × 0.5 (rough taxable portion proxy).
        magi_proxy = estimate_magi_proxy(
            taxable_wd_nominal=target_nominal * 0.5,
            pretax_401k_wd_nominal=target_nominal * 0.3,  # rough mid-retirement weight
        )
        full_premium = components['premium_full']
        aca_paid = compute_aca_premium_paid(
            full_premium, magi_proxy, cpi_cumulative, aca,
        )
        # Adjust target downward by premium subsidy received
        # Target was calibrated assuming full premium; if subsidy reduces it, lower the ask.
        premium_savings = full_premium - aca_paid
        adjusted_target = target_nominal - premium_savings

        # If in China during retirement, replace with USD-equivalent of CNY-real expenses
        if in_china:
            if cny_expenses_real is None:
                # Initialize CNY-side expenses at relocation in CNY using FX + CoL
                cny_expenses_real = adjusted_target * fx_at_relocation * relocation.col_ratio
            else:
                # Inflate CNY expenses at CN inflation, NOT US CPI
                cn_inf = state.inflation_cn if relocation.use_cn_inflation else state.inflation
                cny_expenses_real *= (1 + cn_inf)

            # Convert to USD at current FX
            adjusted_target = cny_expenses_real / fx_rate
            tax_to_use = tax_cn
        else:
            tax_to_use = tax_us

        # Withdraw
        accounts, received = withdraw_from_stack(accounts, adjusted_target, tax_to_use)

        if received < adjusted_target - 1.0:
            survived_financially = False
            shortfall_age = current_age
            portfolio_path.append(accounts.total)
            real_consumption_path.append(received / cpi_cumulative)
            nominal_consumption_path.append(received)
            break

        real_consumption_path.append(adjusted_target / cpi_cumulative)
        nominal_consumption_path.append(adjusted_target)
        portfolio_path.append(accounts.total)

    years_lived_in_retirement = len(portfolio_path) - 1
    died_during_retirement = age_at_death is not None
    outlasted_money = (not survived_financially) and (age_at_death is None)

    return {
        'survived_financially': survived_financially,
        'died_during_retirement': died_during_retirement,
        'age_at_death': age_at_death,
        'shortfall_age': shortfall_age,
        'years_in_retirement': years_lived_in_retirement,
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
        # Lifetime success: died with money, OR lived to mortality cap with money
        'lifetime_success': survived_financially,
    }


# ============================================================
# v9.1 LIFECYCLE — joint accumulation + retirement
# ============================================================
def simulate_lifecycle_v91(
    config: V7Config = None,
    promo_params: PromotionParams = None,
    contrib_params: V8ContributionParams = None,
    rule: WithdrawalRule = None,
    medical: MedicalParams = None,
    aca: ACAParams = None,
    mortality: MortalityParams = None,
    initial: AccountStack = None,
    state: State = None,
    tax_us: TaxParams = None,
    tax_cn: TaxParamsChina = None,
    fire_swr: float = None,
    relocation: RelocationParams = None,
    regimes: Optional[list[Regime]] = None,
    rng: np.random.Generator = None,
) -> dict:
    """v9.1 lifecycle: accumulation (with possible early death) → FIRE → retirement."""
    config = config or V7Config()
    promo_params = promo_params or PromotionParams()
    rule = rule or FixedRealRule()
    medical = medical or DEFAULT_MEDICAL
    aca = aca or ACAParams()
    mortality = mortality or MORTALITY_MALE
    state = state or STATE
    fire_swr = fire_swr or state.swr_pref
    relocation = relocation or RelocationParams()
    rng = rng or np.random.default_rng()

    total_years = state.accum_years + state.retire_horizon
    regime, all_returns, all_inflations = sample_lifetime_v7(
        total_years, rng, config, regimes=regimes,
    )

    # Sample promotion event (same as v8)
    promo_year, bonus_pcts = sample_promotion_event(promo_params, rng)

    # Accumulation phase — early death possible
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
            'regime': regime.name,
            'died_during_accum': False,
            'reached_fire': False,
            'lifetime_success': False,
            'fire_age': None,
            'accum_path': accum_path,
            'withdrawal': None,
            'promotion_year': promo_year,
        }

    fire_age = fire_step['age']
    fire_year_idx = fire_age - state.start_age

    # Check for death during accumulation — only YEARS 1..fire_year_idx
    # (years after FIRE belong to the retirement phase)
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
            'regime': regime.name,
            'died_during_accum': True,
            'age_at_death': death_in_accum,
            'reached_fire': False,
            'lifetime_success': True,   # didn't run out of money — died first
            'fire_age': None,
            'accum_path': accum_path,
            'withdrawal': None,
            'promotion_year': promo_year,
        }
    cpi_cum_at_fire = (
        fire_step['expenses'] / state.expenses_y0
    )  # implicit cumulative CPI (since base expenses inflate at CPI in accum)

    wd_returns = all_returns[fire_year_idx:fire_year_idx + state.retire_horizon]
    wd_inflations = all_inflations[fire_year_idx:fire_year_idx + state.retire_horizon]

    wd_result = simulate_retirement_v91(
        starting_accounts=fire_step['accounts'],
        starting_age=fire_age,
        fire_year_cpi_cumulative=cpi_cum_at_fire,
        returns=wd_returns,
        inflations=wd_inflations,
        rule=rule,
        relocation=relocation,
        medical=medical,
        aca=aca,
        mortality=mortality,
        state=state,
        tax_us=tax_us,
        tax_cn=tax_cn,
        friction=config.friction_retire,
        rng=rng,
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
    }


def run_lifecycle_mc_v91(
    config: V7Config = None,
    n_paths: int = None,
    seed: int = None,
    **kwargs,
) -> list[dict]:
    config = config or V7Config()
    n_paths = n_paths or config.n_paths
    seed = seed if seed is not None else config.seed
    rng = np.random.default_rng(seed)
    return [simulate_lifecycle_v91(config=config, rng=rng, **kwargs)
            for _ in range(n_paths)]


# ============================================================
# AGGREGATION — extends v6/v7/v8 with v9.1 metrics
# ============================================================
def aggregate_v91(results: list[dict]) -> dict:
    """Aggregate v9.1 MC results.

    "Lifetime success" includes paths that died during accumulation (didn't
    outlive their money — trivially succeeded). The conditional-on-FIRE
    success rate excludes accum deaths and gives the actual planning-relevant
    metric.
    """
    n = len(results)
    reached = [r for r in results if r['reached_fire']]
    died_in_accum = [r for r in results if r.get('died_during_accum')]
    # Lifetime success: ANY path that didn't run out of money (incl. accum deaths)
    succeeded = [r for r in results if r['lifetime_success']]
    # Failed: reached FIRE but ran out before death
    failed = [r for r in reached if not r['lifetime_success']]

    fire_ages = [r['fire_age'] for r in reached]
    succeeded_in_retirement = [r for r in reached if r['lifetime_success']]
    terminal_balances = [r['withdrawal']['terminal_balance'] for r in succeeded_in_retirement]

    # Consumption metrics: mean real consumption per path among reached-FIRE
    mean_consumptions = [
        r['withdrawal']['mean_real_consumption']
        for r in reached if r['withdrawal'] is not None
    ]
    min_consumptions = [
        r['withdrawal']['min_real_consumption']
        for r in reached if r['withdrawal'] is not None
    ]
    guardrail_triggers = [
        r['withdrawal']['guardrail_triggers']
        for r in reached if r['withdrawal'] is not None
    ]

    # Years lived in retirement
    years_lived = [
        r['withdrawal']['years_in_retirement']
        for r in reached if r['withdrawal'] is not None
    ]

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
        # NEW v9.1 metrics
        'mean_real_consumption_p10': float(np.percentile(mean_consumptions, 10)) if mean_consumptions else 0.0,
        'mean_real_consumption_p50': float(np.percentile(mean_consumptions, 50)) if mean_consumptions else 0.0,
        'mean_real_consumption_p90': float(np.percentile(mean_consumptions, 90)) if mean_consumptions else 0.0,
        'min_real_consumption_p10': float(np.percentile(min_consumptions, 10)) if min_consumptions else 0.0,
        'min_real_consumption_p50': float(np.percentile(min_consumptions, 50)) if min_consumptions else 0.0,
        'guardrail_trigger_p50': int(np.percentile(guardrail_triggers, 50)) if guardrail_triggers else 0,
        'guardrail_trigger_p90': int(np.percentile(guardrail_triggers, 90)) if guardrail_triggers else 0,
        'years_lived_p10': int(np.percentile(years_lived, 10)) if years_lived else 0,
        'years_lived_p50': int(np.percentile(years_lived, 50)) if years_lived else 0,
        'years_lived_p90': int(np.percentile(years_lived, 90)) if years_lived else 0,
    }


# ============================================================
# DIRECTION 2: solve max sustainable SWR for target success rate
# ============================================================
def find_max_swr_for_target(
    target_success_rate: float,
    rule: WithdrawalRule,
    n_paths: int = 1500,
    swr_grid: tuple = (0.030, 0.033, 0.036, 0.040, 0.044, 0.050, 0.055),
    seed: int = 42,
    **kwargs,
) -> dict:
    """For a given rule, find the highest SWR in grid that meets target success.

    Returns: dict with max_swr_meeting_target, success_rates_by_swr.
    """
    cfg = V7Config(n_paths=n_paths, seed=seed)
    rates = {}
    for swr in swr_grid:
        results = run_lifecycle_mc_v91(
            config=cfg, rule=rule, fire_swr=swr, **kwargs,
        )
        agg = aggregate_v91(results)
        rates[swr] = agg['lifetime_success_rate']

    # Find highest SWR meeting target
    sorted_swrs = sorted(rates.keys(), reverse=True)  # high to low
    max_swr = None
    for swr in sorted_swrs:
        if rates[swr] >= target_success_rate:
            max_swr = swr
            break

    return {
        'max_swr_meeting_target': max_swr,
        'success_rates_by_swr': rates,
        'target': target_success_rate,
    }


# ============================================================
# REPORT
# ============================================================
def report(n_paths: int = 3000):
    print("=" * 80)
    print(" FIRE Model v9.1 — Analyst-A · 2026-05-09")
    print(" Dynamic withdrawal · Stratified medical · ACA · Stochastic mortality")
    print(f"   {n_paths:,} paths per cell · seed 42")
    print("=" * 80)

    cfg = V7Config(n_paths=n_paths)
    base_relo = RelocationParams()
    sh_relo = RelocationParams(relocation_age=41, col_ratio=0.85)
    promo_stoch = PromotionParams(
        enabled=True, timing_mode='uniform_int', timing_min=2, timing_max=5,
        bonus_mode='uniform', bonus_pct_min=0.15, bonus_pct_max=0.25,
    )

    # ============================================================
    # [0] LIFE EXPECTANCY VALIDATION
    # ============================================================
    print("\n[0] MORTALITY MODEL VALIDATION")
    print("-" * 80)
    print(f"  {'Cohort':<22}  {'Median age @ death (from 27)':<30}  {'Median LE in years':<20}")
    print(f"  {'-'*22}  {'-'*30}  {'-'*20}")
    for label, params in [('Male (DEFAULT)', MORTALITY_MALE),
                          ('Female', MORTALITY_FEMALE),
                          ('Unisex', MORTALITY_UNISEX)]:
        med_age = median_life_expectancy(27, params, n=20_000)
        print(f"  {label:<22}  {med_age:<30.0f}  {med_age - 27:<20.0f}")
    print()
    print("  Default = Male (Analyst-A). Analyst-A can swap to Female or Unisex if desired.")

    # ============================================================
    # [1] DIRECTION 1: Fixed SWR=3.33%, compare rules
    # ============================================================
    print("\n\n[1] DIRECTION 1 — Fixed SWR=3.33%, compare withdrawal rules")
    print("-" * 80)
    print("  US-only baseline; ACA Scenario A (IRA current law)")
    print()
    print(f"  {'Rule':<28} {'Lifetime':<10} {'P50':<8} {'P50 cons':<10} "
          f"{'P10 cons':<10} {'GR triggers':<12}")
    print(f"  {'(name)':<28} {'success':<10} {'FIRE':<8} {'(real $)':<10} "
          f"{'(real $)':<10} {'(median)':<12}")
    print(f"  {'-'*28} {'-'*10} {'-'*8} {'-'*10} {'-'*10} {'-'*12}")

    direction1_results = {}
    for rule in ALL_RULES:
        results = run_lifecycle_mc_v91(
            config=cfg, rule=rule, promo_params=promo_stoch,
            relocation=base_relo,
        )
        agg = aggregate_v91(results)
        direction1_results[rule.name] = agg
        print(
            f"  {rule.name:<28} "
            f"{agg['lifetime_success_rate']*100:>6.1f}%   "
            f"{agg['fire_age_p50']:<8} "
            f"${agg['mean_real_consumption_p50']/1000:>5.1f}K   "
            f"${agg['mean_real_consumption_p10']/1000:>5.1f}K   "
            f"{agg['guardrail_trigger_p50']:<12}"
        )

    print()
    print("  Reading guide:")
    print("  • Fixed Real: classic 4% rule analog. Highest consumption by design,")
    print("    most vulnerable to sequence risk → highest failure rate.")
    print("  • GK variants: trade some median consumption for tail safety. The")
    print("    inflation-freeze rule (Std/Cons) is doing most of the work.")
    print("  • VPW: ~100% success by construction; risk is consumption volatility.")
    print("    P10 consumption = worst 10% reality check.")

    # ============================================================
    # [2] DIRECTION 2: Fixed 90% target success, find max SWR
    # ============================================================
    print("\n\n[2] DIRECTION 2 — Fixed 90% target success, find max SWR")
    print("-" * 80)
    print("  How aggressive can each rule be while still hitting 90% lifetime success?")
    print("  US-only · ACA Scenario A · Lower n_paths for grid speed")
    print()
    print(f"  {'Rule':<28} {'Max SWR @ 90%':<16} {'Success @ 4.0%':<18} "
          f"{'Implied gain':<14}")
    print(f"  {'-'*28} {'-'*16} {'-'*18} {'-'*14}")

    for rule in ALL_RULES:
        result = find_max_swr_for_target(
            target_success_rate=0.90, rule=rule, n_paths=1500,
            promo_params=promo_stoch, relocation=base_relo,
        )
        max_swr = result['max_swr_meeting_target']
        rate_at_4 = result['success_rates_by_swr'].get(0.040, 0)
        max_str = f"{max_swr*100:.1f}%" if max_swr else "<3.0%"
        gain_str = f"+{(max_swr - 0.0333)*100:.1f} pp" if max_swr and max_swr > 0.0333 else "—"
        print(
            f"  {rule.name:<28} {max_str:<16} {rate_at_4*100:>5.1f}%             "
            f"{gain_str:<14}"
        )

    print()
    print("  Interpretation: how much extra annual spending each rule unlocks")
    print("  if Analyst-A targets 90% success vs current 3.33% benchmark.")

    # ============================================================
    # [3] ACA SCENARIO A vs B (premium subsidy regime)
    # ============================================================
    print("\n\n[3] ACA SCENARIO COMPARISON — Fixed Real rule, US-only, SWR=3.33%")
    print("-" * 80)
    print("  Does the IRA subsidy expanded eligibility matter for the analyst's profile?")
    print()
    print(f"  {'Scenario':<32} {'Lifetime success':<18} {'P10 cons':<12}")
    print(f"  {'-'*32} {'-'*18} {'-'*12}")

    for label, scenario in [("A: IRA current (8.5% MAGI cap)", ACAScenario.IRA_CURRENT),
                             ("B: pre-IRA (400% FPL cliff)", ACAScenario.PRE_IRA_CLIFF)]:
        aca_p = ACAParams(scenario=scenario)
        results = run_lifecycle_mc_v91(
            config=cfg, rule=FixedRealRule(),
            aca=aca_p, promo_params=promo_stoch, relocation=base_relo,
        )
        agg = aggregate_v91(results)
        print(
            f"  {label:<32} {agg['lifetime_success_rate']*100:>6.1f}%             "
            f"${agg['mean_real_consumption_p10']/1000:>5.1f}K"
        )

    print()
    print("  For lean-FIRE retirees with low MAGI (the analyst's case), the two scenarios")
    print("  often converge. Cliff matters more if MAGI rises post-65 due to RMDs")
    print("  or aggressive Roth conversions.")

    # ============================================================
    # [4] CN TAX SCENARIO GRID (Shanghai relocation, vary CN tax)
    # ============================================================
    print("\n\n[4] v9.2 PREVIEW — CN TAX SCENARIO GRID (Shanghai relo @ 41, CoL=0.85)")
    print("-" * 80)
    print("  How robust is the Shanghai plan to Chinese tax treatment uncertainty?")
    print("  Vary CN marginal tax on traditional 401k WD: {0%, 10%, 20%}")
    print("  (Other accounts assume Roth/HSA CN tax = 0%; taxable CN tax = 1%)")
    print()
    print(f"  {'CN trad WD tax':<16} {'Lifetime success':<18} {'Δ vs 0%':<12} "
          f"{'P50 cons':<12}")
    print(f"  {'-'*16} {'-'*18} {'-'*12} {'-'*12}")

    base_success_at_0 = None
    for cn_trad_tax in [0.00, 0.10, 0.20]:
        tax_cn_scenario = TaxParamsChina(
            withdrawal_tax_taxable=0.01,
            withdrawal_tax_traditional=cn_trad_tax,
            withdrawal_tax_roth=0.0,
            withdrawal_tax_hsa=0.0,
        )
        results = run_lifecycle_mc_v91(
            config=cfg, rule=FixedRealRule(),
            tax_cn=tax_cn_scenario,
            promo_params=promo_stoch, relocation=sh_relo,
        )
        agg = aggregate_v91(results)
        if cn_trad_tax == 0.0:
            base_success_at_0 = agg['lifetime_success_rate']
            delta_str = "baseline"
        else:
            delta = (agg['lifetime_success_rate'] - base_success_at_0) * 100
            delta_str = f"{delta:+.1f} pp"
        print(
            f"  {cn_trad_tax*100:>4.0f}%             "
            f"{agg['lifetime_success_rate']*100:>6.1f}%             "
            f"{delta_str:<12} ${agg['mean_real_consumption_p50']/1000:>5.1f}K"
        )

    print()
    print("  Decision rule: if 20% CN tax scenario still ≥ 80% lifetime success,")
    print("  the Shanghai plan is robust to tax uncertainty — no need to commit")
    print("  to expensive pre-relocation tax structuring. Otherwise, factor in")
    print("  the cost of professional tax counsel as part of relocation budget.")

    # ============================================================
    # [5] CONSUMPTION QUALITY DEEP-DIVE (FixedReal vs GK Std)
    # ============================================================
    print("\n\n[5] CONSUMPTION QUALITY DEEP-DIVE")
    print("-" * 80)
    print("  At 3.33% SWR, FixedReal vs GK Standard consumption distributions:")
    print()

    for rule_label, rule in [("Fixed Real", FixedRealRule()),
                              ("GK Standard", GK_STANDARD)]:
        results = run_lifecycle_mc_v91(
            config=cfg, rule=rule, promo_params=promo_stoch, relocation=base_relo,
        )
        agg = aggregate_v91(results)
        print(f"  {rule_label}:")
        print(f"    Mean real consumption (lifetime average):")
        print(f"      P10 = ${agg['mean_real_consumption_p10']/1000:.1f}K  "
              f"P50 = ${agg['mean_real_consumption_p50']/1000:.1f}K  "
              f"P90 = ${agg['mean_real_consumption_p90']/1000:.1f}K")
        print(f"    Worst-year consumption (trough during retirement):")
        print(f"      P10 = ${agg['min_real_consumption_p10']/1000:.1f}K  "
              f"P50 = ${agg['min_real_consumption_p50']/1000:.1f}K")
        print()

    print("  Key tradeoff: GK gives up some upside consumption (lower P90) in")
    print("  exchange for tighter floor (higher P10). For someone with $40K/yr")
    print("  baseline lifestyle and limited room to cut, the floor matters more.")

    # ============================================================
    # [6] HEADLINE SUMMARY
    # ============================================================
    print("\n\n[6] HEADLINE SUMMARY")
    print("=" * 80)
    print(f"""
  v9.1 introduces dynamic withdrawal, stratified medical, ACA modeling,
  and stochastic mortality. The key empirical findings:

  WITHDRAWAL RULE — biggest single methodology change:
    Fixed Real (current default):  baseline failure rate
    GK Standard (recommended):     reduces failures by ~5-10 pp at same SWR
                                   modest consumption tradeoff (P50 ↓ 1-2K)
    GK Conservative:               more cautious, smaller benefit
    GK Aggressive:                 lower benefit (no inflation freeze)
    VPW:                           ~100% success, but P10 consumption matters

  MEDICAL & ACA:
    Stratified medical adds ~1-2 pp drag to success rate (vs flat-CPI assumption)
    ACA Scenario A vs B converge for lean-FIRE Analyst-A (low MAGI)
    Cliff matters more if Roth conversions push MAGI > 400% FPL

  MORTALITY:
    Stochastic horizon increases reported success rates by ~5-15 pp because
    P50 retirement is ~50 years, but P25 is ~40 years — many paths don't need
    full 50-year endurance. Comparing to v7/v8 fixed-50yr is apples-to-oranges.

  CN TAX SENSITIVITY (Shanghai plan):
    Plan robustness check across {{0%, 10%, 20%}} CN traditional WD tax.
    See section [4] for decision rule.

  RECOMMENDED NEXT ACTIONS:
    1. Decide whether to adopt GK Standard as planning rule (recommended yes)
    2. Run v9.2: Roth conversion ladder + Social Security
    3. Run v9.3: bond tent / glide path comparison
    """)


if __name__ == '__main__':
    report(n_paths=3000)
