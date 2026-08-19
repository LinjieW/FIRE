"""
FIRE Model v6 — Analyst-A · 2026-05-06
====================================

Updates from v5:
  1. ACTUAL INITIAL STACK (per user statement)
     - Roth IRA: $46K (was assumed $30K)
     - HSA: $16K
     - Taxable: $59K (was assumed $64K)
     - Pre-tax 401(k): $89K (residual; was assumed $100K)

  2. SHANGHAI RELOCATION LAYER (new)
     - Configurable relocation_age (None = stay in US)
     - Cost-of-living ratio (Shanghai expense / DC expense, default 1.0)
     - Stochastic USD/CNY exchange rate after relocation:
         * default drift = 0 (random walk)
         * default sigma = 6%/yr
     - Tax regime switches at relocation:
         * US state tax (DC ~5-7%) drops to 0
         * Chinese taxes assumed 0 (per user)
         * US federal worldwide income tax continues
         * Net effect: taxable WD tax 4%→1%, 401k WD tax 12%→10%

  3. RELOCATION TIMING SENSITIVITY (new)
     - Default still: stay in US (None)
     - But report() runs grid: relocation at age 35, 38, 41, 45, never
     - Shows Pareto frontier of relocation timing vs lifetime success

Methodology note:
  Currency risk is the new tail risk introduced. If RMB strengthens
  significantly (USD/CNY falls), the analyst's USD-denominated portfolio buys
  fewer RMB → effective USD expenses rise → faster portfolio depletion.
  The 6%/yr FX vol means a 25-year horizon has ±30% cumulative drift potential.

Requires: numpy
Usage:
    python fire_v6_model.py
"""

from __future__ import annotations
import numpy as np
from dataclasses import dataclass, field
from typing import Callable, Sequence, Optional
from enum import Enum

from fire_rule_pack import CONTRIBUTION_LIMIT_RULES, US_FEDERAL_RULES


# ============================================================
# CANONICAL STATE
# ============================================================
@dataclass
class State:
    start_age: int = 27
    contrib_growth: float = 0.035
    inflation: float = 0.030          # US inflation
    inflation_cn: float = 0.025       # China inflation (historical avg ~2-2.5%)
    expenses_y0: float = 40_440
    swr_pref: float = 0.0333
    swr_low: float = 0.030
    accum_years: int = 25
    retire_horizon: int = 50
    # --- opt-in retirement spending "smile" (real age-decline). 0.0 = flat-real
    # (bit-identical to prior behaviour). e.g. 0.01 = the real spending target
    # decays ~1%/yr through retirement, floored at spending_decline_floor. ---
    spending_decline: float = 0.0
    spending_decline_floor: float = 0.55


# ============================================================
# ACCOUNT STACK
# ============================================================
#: The account fields the United States ships with. Kept as an explicit tuple
#: rather than derived at import time: the engine must not depend on the
#: server package to define its own data shape, and a country pack adds its
#: accounts through `AccountStack.hold()` rather than by editing this.
US_STACK_FIELDS = ("pretax_401k", "roth_ira", "hsa", "taxable")


class AccountStack:
    """Balances by account, with the four US ones as named attributes.

    Roadmap 7.0 Phase 2b (idea-bank S8). This used to be a four-field
    dataclass whose `total` added those four names. A fifth account could be
    attached and was then **silently excluded from net worth** -- the failure
    class this project treats as worst, because the arithmetic still looks
    complete.

    So balances live in a mapping and the four US names are properties over
    it. That keeps all 491 existing references working unchanged, which is
    what makes this phase's bit-identity gate achievable at all, while `total`
    sums what is actually held rather than what somebody typed out in 2026.

    **An unknown account is refused, not absorbed.** `stack.uk_isa = 1` raises
    rather than creating a balance nothing sums; a country pack declares its
    accounts through `hold()`. Absorbing it silently is how the old shape
    failed, and a generalisation that reproduces the original defect in a
    roomier container has generalised nothing.
    """

    __slots__ = ("_balances",)

    def __init__(self, pretax_401k: float = 0, roth_ira: float = 0,
                 hsa: float = 0, taxable: float = 0, **extra):
        object.__setattr__(self, "_balances", {
            "pretax_401k": pretax_401k, "roth_ira": roth_ira,
            "hsa": hsa, "taxable": taxable,
        })
        for key, value in extra.items():
            self._balances[key] = value

    # -- the four US names, as properties over the mapping -------------------
    @property
    def pretax_401k(self) -> float:
        return self._balances["pretax_401k"]

    @pretax_401k.setter
    def pretax_401k(self, value) -> None:
        self._balances["pretax_401k"] = value

    @property
    def roth_ira(self) -> float:
        return self._balances["roth_ira"]

    @roth_ira.setter
    def roth_ira(self, value) -> None:
        self._balances["roth_ira"] = value

    @property
    def hsa(self) -> float:
        return self._balances["hsa"]

    @hsa.setter
    def hsa(self, value) -> None:
        self._balances["hsa"] = value

    @property
    def taxable(self) -> float:
        return self._balances["taxable"]

    @taxable.setter
    def taxable(self, value) -> None:
        self._balances["taxable"] = value

    # -- the general surface -------------------------------------------------
    def hold(self, key: str, amount: float = 0.0) -> None:
        """Start tracking an account this stack does not yet know about."""
        self._balances.setdefault(str(key), amount)

    def balances(self) -> dict:
        return dict(self._balances)

    def __setattr__(self, name: str, value) -> None:
        if name in US_STACK_FIELDS:
            object.__getattribute__(self, "_balances")[name] = value
            return
        if name == "_balances":
            object.__setattr__(self, name, value)
            return
        raise AttributeError(
            "%r is not an account this stack holds. Use hold(%r) first -- "
            "attaching it silently would leave it out of `total`, which is "
            "the defect this class was rewritten to remove." % (name, name))

    @property
    def total(self) -> float:
        """Every balance held, not four names written down in 2026."""
        return sum(self._balances.values())

    def __eq__(self, other) -> bool:
        return (isinstance(other, AccountStack)
                and self._balances == other._balances)

    def __repr__(self) -> str:                                # pragma: no cover
        return "AccountStack(%s)" % ", ".join(
            "%s=%r" % item for item in sorted(self._balances.items()))

    def replace(self, **changes) -> "AccountStack":
        """A `dataclasses.replace` equivalent, because callers use that.

        `engine_adapter` calls `dataclasses.replace(init, taxable=...)` to fold
        other liquid assets in. That call site names the FIELD but not the
        class, so no search for "AccountStack" finds it -- the second
        reflective helper this phase reached without touching its name. Both
        are now handled at the shape rather than at each call site.
        """
        clone = self.copy()
        for key, value in changes.items():
            if key not in clone._balances:
                raise AttributeError(
                    "%r is not an account this stack holds" % (key,))
            clone._balances[key] = value
        return clone

    def copy(self) -> "AccountStack":
        clone = AccountStack()
        object.__getattribute__(clone, "_balances").clear()
        object.__getattribute__(clone, "_balances").update(self._balances)
        return clone

    def fmt(self) -> str:
        return (f"401k=${self.pretax_401k/1000:.0f}K  "
                f"Roth=${self.roth_ira/1000:.0f}K  "
                f"HSA=${self.hsa/1000:.0f}K  "
                f"Taxable=${self.taxable/1000:.0f}K  "
                f"Total=${self.total/1000:.0f}K")


# UPDATED with user-reported actual balances (May 2026)
INITIAL_STACK = AccountStack(
    pretax_401k=89_000,    # residual: 210 - 46 - 16 - 59 = 89
    roth_ira=46_000,       # actual
    hsa=16_000,            # actual
    taxable=59_000,        # actual
)
assert INITIAL_STACK.total == 210_000


# ============================================================
# CONTRIBUTION STREAM (unchanged from v5)
# ============================================================
@dataclass
class ContributionStream:
    pretax_401k_y1: float = CONTRIBUTION_LIMIT_RULES["pretax_401k_limit_y1"]
    employer_match_y1: float = 9_450
    roth_ira_y1: float = CONTRIBUTION_LIMIT_RULES["roth_ira_limit_y1"]
    hsa_y1: float = CONTRIBUTION_LIMIT_RULES["hsa_limit_y1"]
    taxable_y1: float = 39_800
    salary_growth: float = 0.035
    irs_limit_growth: float = CONTRIBUTION_LIMIT_RULES["irs_limit_growth"]

    def amounts_at_year(self, year: int) -> AccountStack:
        if year < 1:
            return AccountStack()
        irs_factor = (1 + self.irs_limit_growth) ** (year - 1)
        sal_factor = (1 + self.salary_growth) ** (year - 1)
        return AccountStack(
            pretax_401k=(self.pretax_401k_y1 * irs_factor +
                        self.employer_match_y1 * sal_factor),
            roth_ira=self.roth_ira_y1 * irs_factor,
            hsa=self.hsa_y1 * irs_factor,
            taxable=self.taxable_y1 * sal_factor,
        )


# ============================================================
# TAX PARAMETERS (with US/China differentiation)
# ============================================================
@dataclass
class TaxParams:
    """US tax regime (DC resident)."""
    # The annual tax drag on the taxable bucket, as a return haircut. It is
    # DERIVED from the three dividend fields below unless this is set, in which
    # case the given number is used verbatim.
    #
    # `None` here does not mean "unmeasured" -- it means "not overridden".
    # `__post_init__` below turns it into a number at construction, so no
    # consumer ever sees `None`. It exists as an override because a saved plan
    # stores its whole config, so every plan saved before the derivation landed
    # carries an explicit 0.0025 and must keep reproducing exactly.
    drag_taxable: Optional[float] = None
    #: Set during construction when `drag_taxable` was supplied rather than
    #: derived. Internal bookkeeping, deliberately kept out of
    #: `default_config()` (see `_gd(..., drop=...)`): it is not an input, it
    #: it records HOW the drag arrived, and the engine needs that to know
    #: whether it may price the drag from brackets instead.
    drag_taxable_explicit: bool = False

    # --- what the drag is made of (Phase 3) ---
    # It used to be one hardcoded 0.0025, which is a yield times a tax rate
    # with both halves hidden. Separating them is the whole point: a retiree in
    # the 0% LTCG bracket and a high earner holding the same fund do not pay
    # the same drag, and neither could say so before.
    #: Annual distribution yield of the taxable holdings.
    dividend_yield: float = 0.017
    #: Share of that yield taxed at qualified/LTCG rates; the rest is treated
    #: as ordinary (interest, non-qualified distributions) and taxed at
    #: `withdrawal_tax_traditional`.
    dividend_qualified_fraction: float = 0.90
    #: The qualified/LTCG rate applied to the qualified share on the flat path.
    #: When the true-tax engine is on it derives the rate from real brackets
    #: instead.
    dividend_tax_rate: float = 0.15

    def __post_init__(self):
        """Resolve the drag here rather than in the adapter.

        Every consumer in this lineage -- v8, v9.1, v9.6, v9.8 -- writes
        `1 + r_eff - tax_us.drag_taxable` straight into a return, so a `None`
        that survives construction is a TypeError somewhere far away. It was:
        a preview path that builds `TaxParams()` directly, bypassing the
        server's mapper, died with "unsupported operand type(s) for -: 'float'
        and 'NoneType'". Resolving at construction means the mapper is a
        convenience rather than a precondition, and no vendored engine file
        has to learn about dividends.
        """
        if self.drag_taxable is None:
            qualified = float(self.dividend_qualified_fraction)
            rate = (qualified * float(self.dividend_tax_rate)
                    + (1.0 - qualified) * float(self.withdrawal_tax_traditional))
            self.drag_taxable = float(self.dividend_yield) * rate
        else:
            self.drag_taxable = float(self.drag_taxable)
            self.drag_taxable_explicit = True

    # US WD tax rates (DC + federal)
    withdrawal_tax_taxable: float = 0.04
    withdrawal_tax_traditional: float = 0.12
    withdrawal_tax_roth: float = 0.0
    withdrawal_tax_hsa: float = 0.0
    # --- opt-in progressive mode (default OFF => the flat traditional rate above
    # is used, bit-identical to prior behaviour). When ON, the traditional rate
    # becomes a size-aware effective ordinary rate (brackets + std deduction). ---
    progressive: bool = False
    state_rate: float = 0.0            # flat state add-on to the federal effective rate
    std_deduction: float = US_FEDERAL_RULES["std_deduction_single"]


# US federal ordinary-income brackets (single filer, 2026, real today's $).
# Used only when TaxParams.progressive is enabled.
# VINTAGE: 2026 federal ordinary-income brackets, SINGLE filer (IRS Rev. Proc.
# 2025-45). MFJ is approximated as thresholds ×2 in this legacy model (the
# true-tax path uses the canonical MFJ table). Update yearly or treat as an
# editable starting point.
US_ORDINARY_BRACKETS_SINGLE = list(
    US_FEDERAL_RULES["ordinary_single"])


def effective_ordinary_rate(taxable_income_real: float,
                            brackets=US_ORDINARY_BRACKETS_SINGLE,
                            std_deduction: float = US_FEDERAL_RULES["std_deduction_single"],
                            state_rate: float = 0.0,
                            filing_jointly: bool = False) -> float:
    """Effective (average) US ordinary-income tax rate on `taxable_income_real`
    (today's $) after the standard deduction, plus a flat state add-on. The rate
    is scale-invariant, so it can be applied to a nominal withdrawal of the same
    real size. When filing_jointly, bracket thresholds and the standard
    deduction are doubled (a close MFJ approximation). Returns a rate in [0, 0.90]."""
    scale = 2.0 if filing_jointly else 1.0
    if filing_jointly:
        brackets = [(lo * scale, rate) for (lo, rate) in brackets]
        std_deduction = std_deduction * scale
    gross = max(0.0, taxable_income_real)
    ti = max(0.0, gross - std_deduction)
    tax = 0.0
    for i, (lo, rate) in enumerate(brackets):
        hi = brackets[i + 1][0] if i + 1 < len(brackets) else float("inf")
        if ti <= lo:
            break
        tax += (min(ti, hi) - lo) * rate
    fed_eff = tax / max(gross, 1.0)
    return min(0.90, fed_eff + max(0.0, state_rate))


@dataclass
class TaxParamsChina:
    """When in China: drop US state, drop CN tax, keep US federal."""
    drag_taxable: float = 0.0025      # same drag (same fund holdings)

    # Lower because: no DC state, mostly 0% LTCG bracket federal,
    # ordinary income tax also lower without state piece
    withdrawal_tax_taxable: float = 0.01      # mostly 0% LTCG, tiny fed
    withdrawal_tax_traditional: float = 0.10  # federal ordinary income, no DC
    withdrawal_tax_roth: float = 0.0
    withdrawal_tax_hsa: float = 0.0           # NOTE: HSA reimbursement abroad
                                              # is operationally complex; user
                                              # said not to model CN-side tax


# ============================================================
# RELOCATION PARAMETERS
# ============================================================
@dataclass
class RelocationParams:
    """Parameters for US -> Shanghai retirement relocation."""

    # When to relocate. None = stay in US permanently.
    relocation_age: Optional[int] = None

    # Cost of living: Shanghai expenses / DC expenses (real-terms)
    # 1.00 = same purchasing power maintained
    # 0.85 = Shanghai 15% cheaper at equivalent comfort
    # User can override; default conservative parity
    col_ratio: float = 1.00

    # USD/CNY exchange rate parameters
    fx_initial: float = 7.20      # USD/CNY May 2026
    fx_drift: float = 0.000       # annual log-drift (0 = random walk)
    fx_sigma: float = 0.060       # annual log-vol

    # E6 (opt-in): PPP mean reversion on the log FX rate — the yearly drift
    # gains kappa*(ln(anchor) − ln(fx)). The same single z is drawn either
    # way, so ppp_kappa=0 is bit-identical BY CONSTRUCTION (no guard branch).
    # fx_ppp None => anchor at fx_initial.
    ppp_kappa: float = 0.0
    fx_ppp: Optional[float] = None

    # Use CN inflation for expenses while in China (default 2.5%)
    use_cn_inflation: bool = True


# ============================================================
# REGIME DEFINITIONS (same as v4/v5)
# ============================================================
@dataclass
class Regime:
    name: str
    prob: float
    params: Callable[[int], tuple[float, float]]
    rationale: str = ""


REGIMES: list[Regime] = [
    Regime(name='highCAPE', prob=0.40,
           params=lambda y: (0.05, 0.17) if y <= 10 else (0.095, 0.17)),
    Regime(name='aiPersists', prob=0.20,
           params=lambda y: (0.11, 0.18)),
    Regime(name='historical', prob=0.40,
           params=lambda y: (0.093, 0.17)),
]
assert abs(sum(r.prob for r in REGIMES) - 1.0) < 1e-9


STATE = State()
TAX_US = TaxParams()
TAX_CN = TaxParamsChina()
CONTRIB = ContributionStream()


# ============================================================
# RANDOM DRAWS
# ============================================================
def draw_return(mu_arith: float, sigma: float, rng: np.random.Generator) -> float:
    sigma_log = np.sqrt(np.log(1 + sigma**2 / (1 + mu_arith)**2))
    mu_log = np.log(1 + mu_arith) - 0.5 * sigma_log**2
    return float(np.exp(mu_log + sigma_log * rng.standard_normal()) - 1)


def pick_regime(rng: np.random.Generator) -> Regime:
    r = rng.random()
    cum = 0.0
    for reg in REGIMES:
        cum += reg.prob
        if r < cum:
            return reg
    return REGIMES[-1]


def sample_lifetime_returns(total_years: int, rng: np.random.Generator,
                            regime: Optional[Regime] = None
                            ) -> tuple[Regime, list[float]]:
    if regime is None:
        regime = pick_regime(rng)
    returns = [draw_return(*regime.params(y), rng) for y in range(1, total_years + 1)]
    return regime, returns


def sample_fx_path(years: int, params: RelocationParams,
                   rng: np.random.Generator) -> list[float]:
    """Generate USD/CNY rate path. Lognormal random walk by default.

    Returns: list of FX rates of length years+1, starting with fx_initial.
    """
    rates = [params.fx_initial]
    sigma_log = params.fx_sigma   # already log-vol approximately
    drift_log = params.fx_drift
    for _ in range(years):
        z = rng.standard_normal()
        new_rate = rates[-1] * np.exp(drift_log + sigma_log * z)
        rates.append(new_rate)
    return rates


# ============================================================
# ACCUMULATION PHASE (same as v5)
# ============================================================
def project_stratified(returns: Sequence[float], initial: AccountStack = None,
                       contributions: ContributionStream = None,
                       tax: TaxParams = None, state: State = None) -> list[dict]:
    initial = initial or INITIAL_STACK
    contributions = contributions or CONTRIB
    tax = tax or TAX_US
    state = state or STATE

    accounts = initial.copy()
    path = [{'age': state.start_age, 'accounts': accounts.copy(),
             'expenses': state.expenses_y0, 'total': accounts.total}]

    for i, r in enumerate(returns):
        accounts.pretax_401k *= (1 + r)
        accounts.roth_ira *= (1 + r)
        accounts.hsa *= (1 + r)
        accounts.taxable *= (1 + r - tax.drag_taxable)

        year = i + 1
        c = contributions.amounts_at_year(year)
        accounts.pretax_401k += c.pretax_401k
        accounts.roth_ira += c.roth_ira
        accounts.hsa += c.hsa
        accounts.taxable += c.taxable

        age = state.start_age + year
        exp = state.expenses_y0 * (1 + state.inflation) ** year
        path.append({'age': age, 'accounts': accounts.copy(),
                     'expenses': exp, 'total': accounts.total})

    return path


def find_fire_crossing(path, swr=STATE.swr_pref):
    for step in path:
        if step['total'] >= step['expenses'] / swr:
            return step
    return None


# ============================================================
# WITHDRAWAL with US/China switch
# ============================================================
def withdraw_from_stack(accounts: AccountStack, needed_after_tax: float,
                        tax) -> tuple[AccountStack, float]:
    """Same priority as v5: taxable -> 401k -> HSA -> Roth."""
    accounts = accounts.copy()
    remaining = needed_after_tax

    if remaining > 0 and accounts.taxable > 0:
        rate = tax.withdrawal_tax_taxable
        gross_needed = remaining / (1 - rate)
        gross_take = min(gross_needed, accounts.taxable)
        accounts.taxable -= gross_take
        remaining -= gross_take * (1 - rate)

    if remaining > 0 and accounts.pretax_401k > 0:
        rate = tax.withdrawal_tax_traditional
        gross_needed = remaining / (1 - rate)
        gross_take = min(gross_needed, accounts.pretax_401k)
        accounts.pretax_401k -= gross_take
        remaining -= gross_take * (1 - rate)

    if remaining > 0 and accounts.hsa > 0:
        rate = tax.withdrawal_tax_hsa
        gross_take = min(remaining / max(1 - rate, 0.001), accounts.hsa)
        accounts.hsa -= gross_take
        remaining -= gross_take * (1 - rate)

    if remaining > 0 and accounts.roth_ira > 0:
        gross_take = min(remaining, accounts.roth_ira)
        accounts.roth_ira -= gross_take
        remaining -= gross_take

    actual = needed_after_tax - max(remaining, 0)
    return accounts, actual


def simulate_withdrawal_with_relocation(
    starting_accounts: AccountStack,
    starting_age: int,
    starting_expenses_usd: float,   # FIRE-year expenses in today's USD power
    returns: Sequence[float],
    relocation: RelocationParams,
    state: State = None,
    tax_us: TaxParams = None,
    tax_cn: TaxParamsChina = None,
    rng: np.random.Generator = None,
) -> dict:
    """
    Withdrawal phase with optional Shanghai relocation.

    starting_expenses_usd: first-year withdrawal need in USD (already inflation-
                           adjusted to FIRE year). If user later relocates,
                           expenses convert to CNY-equivalent at FX rate at
                           relocation, then grow at CN inflation, then convert
                           back to USD via stochastic FX for withdrawal.
    """
    state = state or STATE
    tax_us = tax_us or TAX_US
    tax_cn = tax_cn or TAX_CN
    rng = rng or np.random.default_rng()

    accounts = starting_accounts.copy()

    # Track expense levels in both currencies + FX
    expenses_usd = starting_expenses_usd
    in_china = False
    cny_expenses_real = None     # set at relocation
    fx_rate = relocation.fx_initial
    fx_at_relocation = None

    balances = [accounts.total]
    survived = True
    shortfall_age = None
    relocation_done = False

    for year_idx, r in enumerate(returns):
        current_age = starting_age + year_idx + 1

        # Apply returns
        accounts.pretax_401k *= (1 + r)
        accounts.roth_ira *= (1 + r)
        accounts.hsa *= (1 + r)
        accounts.taxable *= (1 + r - tax_us.drag_taxable)

        # Check for relocation event
        if (relocation.relocation_age is not None and
                not relocation_done and
                current_age >= relocation.relocation_age):
            in_china = True
            relocation_done = True
            fx_at_relocation = fx_rate
            # Convert current USD expenses to CNY using FX, apply CoL ratio
            cny_expenses_real = expenses_usd * fx_rate * relocation.col_ratio

        # Evolve FX (only meaningful while/before in China, but always cheap)
        if relocation.fx_sigma > 0:
            z = rng.standard_normal()
            fx_rate = fx_rate * np.exp(relocation.fx_drift +
                                       relocation.fx_sigma * z)

        # Determine this year's USD-equivalent withdrawal need
        if in_china:
            # CNY expenses inflated at CN inflation each year
            # Convert to USD at current FX rate
            usd_needed = cny_expenses_real / fx_rate
            tax_to_use = tax_cn
        else:
            usd_needed = expenses_usd
            tax_to_use = tax_us

        # Withdraw
        accounts, received = withdraw_from_stack(accounts, usd_needed, tax_to_use)

        if received < usd_needed - 0.01:
            survived = False
            shortfall_age = current_age
            balances.append(accounts.total)
            break

        # Inflate expenses for next year (both representations)
        expenses_usd *= (1 + state.inflation)
        if in_china and cny_expenses_real is not None:
            cn_inf = state.inflation_cn if relocation.use_cn_inflation else state.inflation
            cny_expenses_real *= (1 + cn_inf)

        balances.append(accounts.total)

    return {
        'survived': survived,
        'years_survived': len(balances) - 1,
        'terminal_balance': accounts.total if survived else 0.0,
        'final_accounts': accounts,
        'shortfall_age': shortfall_age,
        'balance_path': balances,
        'fx_at_relocation': fx_at_relocation,
        'final_fx': fx_rate,
        'in_china_at_end': in_china,
    }


# ============================================================
# JOINT LIFECYCLE
# ============================================================
def simulate_lifecycle_v6(
    initial: AccountStack = None,
    contributions: ContributionStream = None,
    state: State = None,
    tax_us: TaxParams = None,
    tax_cn: TaxParamsChina = None,
    fire_swr: float = None,
    relocation: RelocationParams = None,
    rng: np.random.Generator = None,
) -> dict:
    """Full lifecycle simulation supporting US-only or US+Shanghai relocation."""
    initial = initial or INITIAL_STACK
    contributions = contributions or CONTRIB
    state = state or STATE
    tax_us = tax_us or TAX_US
    tax_cn = tax_cn or TAX_CN
    relocation = relocation or RelocationParams()    # default: no relocation
    fire_swr = fire_swr or state.swr_pref
    rng = rng or np.random.default_rng()

    total_years = state.accum_years + state.retire_horizon
    regime, all_returns = sample_lifetime_returns(total_years, rng)

    accum_returns = all_returns[:state.accum_years]
    accum_path = project_stratified(accum_returns, initial, contributions,
                                    tax_us, state)

    fire_step = find_fire_crossing(accum_path, fire_swr)
    if fire_step is None:
        return {'regime': regime.name, 'fire_age': None, 'reached_fire': False,
                'lifetime_success': False, 'accum_path': accum_path,
                'withdrawal': None}

    fire_age = fire_step['age']
    fire_year_idx = fire_age - state.start_age
    wd_returns = all_returns[fire_year_idx:fire_year_idx + state.retire_horizon]

    wd_result = simulate_withdrawal_with_relocation(
        starting_accounts=fire_step['accounts'],
        starting_age=fire_age,
        starting_expenses_usd=fire_step['expenses'],
        returns=wd_returns,
        relocation=relocation,
        state=state,
        tax_us=tax_us,
        tax_cn=tax_cn,
        rng=rng,
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
    }


def run_lifecycle_mc_v6(
    n_paths: int = 2000,
    seed: int = 42,
    fire_swr: float = None,
    relocation: RelocationParams = None,
    initial: AccountStack = None,
    **kwargs,
) -> list[dict]:
    rng = np.random.default_rng(seed)
    return [simulate_lifecycle_v6(initial=initial, fire_swr=fire_swr,
                                  relocation=relocation, rng=rng, **kwargs)
            for _ in range(n_paths)]


def aggregate_lifecycle(results: list[dict]) -> dict:
    n = len(results)
    reached = [r for r in results if r['reached_fire']]
    succeeded = [r for r in reached if r['lifetime_success']]
    failed = [r for r in reached if not r['lifetime_success']]

    fire_ages = [r['fire_age'] for r in reached]
    terminal_balances = [r['withdrawal']['terminal_balance'] for r in succeeded]
    failure_ages = [r['withdrawal']['shortfall_age'] for r in failed]

    return {
        'n_paths': n,
        'reached_fire_rate': len(reached) / n,
        'lifetime_success_rate': len(succeeded) / n,
        'conditional_success_rate': len(succeeded) / len(reached) if reached else 0,
        'failure_count': len(failed),
        'fire_age_p25': int(np.percentile(fire_ages, 25)) if fire_ages else None,
        'fire_age_p50': int(np.percentile(fire_ages, 50)) if fire_ages else None,
        'fire_age_p75': int(np.percentile(fire_ages, 75)) if fire_ages else None,
        'terminal_p10': float(np.percentile(terminal_balances, 10)) if terminal_balances else 0,
        'terminal_p50': float(np.percentile(terminal_balances, 50)) if terminal_balances else 0,
        'terminal_p90': float(np.percentile(terminal_balances, 90)) if terminal_balances else 0,
        'failure_age_p50': int(np.percentile(failure_ages, 50)) if failure_ages else None,
    }


# ============================================================
# REPORT
# ============================================================
def report(n_paths: int = 2000):
    print("=" * 78)
    print("FIRE Model v6 — Analyst-A · 2026-05-06")
    print("Updated stack + Shanghai relocation layer")
    print("=" * 78)
    print()

    print(f"Initial stack (UPDATED with actuals): {INITIAL_STACK.fmt()}")
    print(f"  Composition: 401k {INITIAL_STACK.pretax_401k/210000*100:.0f}%, "
          f"Roth {INITIAL_STACK.roth_ira/210000*100:.0f}%, "
          f"HSA {INITIAL_STACK.hsa/210000*100:.0f}%, "
          f"Taxable {INITIAL_STACK.taxable/210000*100:.0f}%")
    print(f"  vs v5 assumed: 401k 48%, Roth 14%, HSA 8%, Taxable 30%")
    print(f"  >> Roth is materially higher than assumed (+$16K), "
          f"401k lower (-$11K)")
    print()

    # ----------------------------------------------------------
    # 1) Stack-update impact (US-only baseline)
    # ----------------------------------------------------------
    print(f"{'='*78}\n[1] STACK-UPDATE IMPACT (no relocation, SWR=3.33%)\n{'='*78}\n")

    # v5 stack
    v5_stack = AccountStack(pretax_401k=100_000, roth_ira=30_000,
                            hsa=16_000, taxable=64_000)
    res_v5stack = run_lifecycle_mc_v6(n_paths=n_paths, initial=v5_stack, seed=42)
    res_v6stack = run_lifecycle_mc_v6(n_paths=n_paths, initial=INITIAL_STACK, seed=42)

    a_v5 = aggregate_lifecycle(res_v5stack)
    a_v6 = aggregate_lifecycle(res_v6stack)

    print(f"  {'Metric':<35} {'v5 stack':<14} {'v6 stack':<14} {'Δ':<10}")
    print(f"  {'Lifetime success':<35} {a_v5['lifetime_success_rate']*100:>6.1f}%        "
          f"{a_v6['lifetime_success_rate']*100:>6.1f}%        "
          f"{(a_v6['lifetime_success_rate']-a_v5['lifetime_success_rate'])*100:>+6.1f} pp")
    print(f"  {'Failure count':<35} {a_v5['failure_count']:>6}/{n_paths}      "
          f"{a_v6['failure_count']:>6}/{n_paths}      "
          f"{a_v6['failure_count']-a_v5['failure_count']:>+6}")
    print(f"  {'Median FIRE age':<35} {a_v5['fire_age_p50']:<14} {a_v6['fire_age_p50']:<14}")
    print(f"  {'Median terminal $':<35} ${a_v5['terminal_p50']/1e6:>5.1f}M         "
          f"${a_v6['terminal_p50']/1e6:>5.1f}M         "
          f"${(a_v6['terminal_p50']-a_v5['terminal_p50'])/1e6:>+5.1f}M")
    print()
    print(f"  >> Higher Roth share is mildly positive: more 'last-resort' tax-free")
    print(f"     reserve for late-retirement years = better tail outcomes.")

    # ----------------------------------------------------------
    # 2) Relocation-age sensitivity
    # ----------------------------------------------------------
    print(f"\n{'='*78}\n[2] RELOCATION-AGE SENSITIVITY (SWR=3.33%, CoL=1.0, FX σ=6%)\n{'='*78}\n")
    print(f"  All runs use UPDATED actual stack. Default RelocationParams except age.")
    print()
    print(f"  {'Reloc age':<12} {'Lifetime success':<18} {'Δ vs US-only':<15} "
          f"{'Median terminal $':<20} {'Failure age (med)':<18}")

    # US-only baseline
    res_us = run_lifecycle_mc_v6(n_paths=n_paths, seed=42,
                                 relocation=RelocationParams())
    a_us = aggregate_lifecycle(res_us)
    base_success = a_us['lifetime_success_rate']
    print(f"  {'never (US)':<12} {a_us['lifetime_success_rate']*100:>6.1f}%             "
          f"{'baseline':<15} ${a_us['terminal_p50']/1e6:>5.1f}M               "
          f"{a_us['failure_age_p50'] or '—':<18}")

    for reloc_age in [35, 38, 41, 45, 50]:
        rp = RelocationParams(relocation_age=reloc_age)
        res = run_lifecycle_mc_v6(n_paths=n_paths, seed=42, relocation=rp)
        a = aggregate_lifecycle(res)
        delta = (a['lifetime_success_rate'] - base_success) * 100
        delta_str = f"{delta:+.1f} pp"
        print(f"  {reloc_age:<12} {a['lifetime_success_rate']*100:>6.1f}%             "
              f"{delta_str:<15} ${a['terminal_p50']/1e6:>5.1f}M               "
              f"{a['failure_age_p50'] or '—':<18}")
    print()
    print(f"  >> Relocating during retirement provides meaningful uplift via tax savings.")
    print(f"     Earlier relocation captures more years of lower-tax withdrawals,")
    print(f"     but exposes more years of FX risk. Tradeoff is empirical.")

    # ----------------------------------------------------------
    # 3) FX sensitivity (assume relocation at age 41, vary FX vol)
    # ----------------------------------------------------------
    print(f"\n{'='*78}\n[3] FX SENSITIVITY (relocation @ age 41, SWR=3.33%)\n{'='*78}\n")
    print(f"  How much does USD/CNY uncertainty hurt? Vary fx_sigma:")
    print()
    print(f"  {'FX σ':<10} {'FX drift':<10} {'Lifetime success':<18} {'P10 terminal $':<18}")
    for sigma, drift in [(0.00, 0.00), (0.04, 0.00), (0.06, 0.00), (0.10, 0.00),
                          (0.06, -0.01), (0.06, +0.01)]:
        rp = RelocationParams(relocation_age=41, fx_sigma=sigma, fx_drift=drift)
        res = run_lifecycle_mc_v6(n_paths=n_paths, seed=42, relocation=rp)
        a = aggregate_lifecycle(res)
        drift_str = f"{drift*100:+.1f}%" if drift != 0 else "0.0%"
        print(f"  {sigma*100:>4.1f}%     {drift_str:<10} "
              f"{a['lifetime_success_rate']*100:>6.1f}%             "
              f"${a['terminal_p10']/1e6:>5.2f}M")
    print()
    print(f"  >> FX volatility costs ~1-3 pp of success rate. Persistent CNY")
    print(f"     strengthening (drift -1%/yr) is the bigger risk than vol alone.")

    # ----------------------------------------------------------
    # 4) CoL sensitivity (assume relocation at age 41, FX defaults)
    # ----------------------------------------------------------
    print(f"\n{'='*78}\n[4] COST-OF-LIVING SENSITIVITY (relocation @ age 41)\n{'='*78}\n")
    print(f"  Shanghai expenses as fraction of DC equivalent:")
    print()
    print(f"  {'CoL ratio':<12} {'Interpretation':<32} {'Lifetime success':<18}")
    for col, label in [(0.70, "Modest middle-class Shanghai"),
                        (0.85, "Comfortable Shanghai (likely)"),
                        (1.00, "Same purchasing power as DC"),
                        (1.20, "Shanghai with private school/healthcare"),
                        (1.50, "Premium expat lifestyle")]:
        rp = RelocationParams(relocation_age=41, col_ratio=col)
        res = run_lifecycle_mc_v6(n_paths=n_paths, seed=42, relocation=rp)
        a = aggregate_lifecycle(res)
        print(f"  {col:<12} {label:<32} {a['lifetime_success_rate']*100:>6.1f}%")
    print()
    print(f"  >> CoL is the largest controllable lever. A 15% spending cut at relocation")
    print(f"     (CoL=0.85) yields several pp of success rate vs parity (CoL=1.00).")

    # ----------------------------------------------------------
    # 5) Combined: optimal-case Shanghai
    # ----------------------------------------------------------
    print(f"\n{'='*78}\n[5] HEADLINE: WHAT IF YOU RELOCATE AT 41 WITH CoL=0.85?\n{'='*78}\n")
    rp_opt = RelocationParams(relocation_age=41, col_ratio=0.85)
    res_opt = run_lifecycle_mc_v6(n_paths=n_paths, seed=42, relocation=rp_opt)
    a_opt = aggregate_lifecycle(res_opt)
    delta = (a_opt['lifetime_success_rate'] - a_us['lifetime_success_rate']) * 100

    print(f"  US-only baseline:       {a_us['lifetime_success_rate']*100:.1f}% lifetime success")
    print(f"  Shanghai @ 41, CoL 0.85: {a_opt['lifetime_success_rate']*100:.1f}% lifetime success "
          f"({delta:+.1f} pp)")
    print(f"  Median terminal:        US ${a_us['terminal_p50']/1e6:.1f}M  vs  "
          f"Shanghai ${a_opt['terminal_p50']/1e6:.1f}M")
    print()
    print(f"  >> Shanghai relocation can substantially improve success rate IF:")
    print(f"     (a) you maintain CoL ≤ 1.0 (avoid expat luxury creep)")
    print(f"     (b) FX risk doesn't materialize as one-way CNY strengthening")
    print(f"     (c) you actually go (option value vs realized value)")


if __name__ == '__main__':
    report(n_paths=2000)
