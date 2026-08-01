"""
fire_engine.py — Generalized, config-driven FIRE Monte Carlo engine.

This is a clean-room, faithfully-ported CORE of an institutional-grade FIRE
simulation. Everything is driven by a plain dict (loaded from YAML); nothing is
hardcoded to any individual. See references/methodology.md for what is and isn't
in scope.

Core mechanics (all ported from a proven, audited engine):
  - Regime-mixture return generation (lognormal draws)
  - Stratified accumulation with per-bucket contributions + milestone tracking
  - FIRE-crossing detection at a configurable safe-withdrawal rate (SWR)
  - Guyton-Klinger guardrail withdrawals in retirement, with the freeze rule
    anchored to CPI-at-retirement (a correctness fix — see methodology.md)
  - Withdrawal from the account stack in tax-efficient order, taxed per bucket
  - Mortality during retirement
  - Social Security offsetting withdrawals, with cash conservation enforced
  - Optional relocation to a lower/higher cost-of-living country, with FX,
    foreign inflation, foreign tax, and a GK re-seed on the destination basis
  - Three-branch lifecycle success semantics (see note below)

A built-in cash-conservation invariant (Sigma consumption == Sigma withdrawals +
Sigma SS, per path) is checked on a sample of paths and will raise if violated.

DISCIPLINE NOTE — three-branch success semantics:
  lifetime_success is NOT the same as "reached FI". A path can (1) never reach
  FI within the working window = true accumulation failure; (2) reach FI but die
  before retiring = counts as success (didn't go broke, just died first);
  (3) reach FI and stay solvent through retirement. Reporting "% who reached FI"
  as a success rate conflates (1) and (2) and understates true robustness. The
  aggregator below keeps these separate.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Callable, Optional, Sequence
import numpy as np


# ============================================================
# REGIMES — return-generating scenarios
# ============================================================
@dataclass
class Regime:
    name: str
    prob: float
    # params(year) -> (arithmetic_mean_return, sigma) for that year of the horizon
    params: Callable[[int], tuple[float, float]]


def default_regimes() -> list[Regime]:
    """The out-of-the-box regime mixture (a blend of valuation-, AI-, and
    history-anchored equity scenarios). Blended geometric mean ~9%/yr nominal,
    sigma ~17%. Deliberately conservative vs. a naive historical fit."""
    return [
        Regime("highCAPE", 0.40,
               lambda y: (0.05, 0.17) if y <= 10 else (0.095, 0.17)),
        Regime("aiPersists", 0.20, lambda y: (0.11, 0.18)),
        Regime("historical", 0.40, lambda y: (0.093, 0.17)),
    ]


def regimes_from_config(cfg: dict) -> list[Regime]:
    ra = cfg.get("return_assumptions", {}) or {}
    mode = ra.get("mode", "regime_mixture")
    if mode == "simple":
        mu = float(ra["mean_return"])
        sigma = float(ra["volatility"])
        return [Regime("simple", 1.0, lambda y, mu=mu, sigma=sigma: (mu, sigma))]
    if mode == "custom" and ra.get("regimes"):
        regs = []
        for r in ra["regimes"]:
            mu = float(r["mean_return"]); sg = float(r["volatility"])
            regs.append(Regime(r.get("name", "custom"), float(r["prob"]),
                               lambda y, mu=mu, sg=sg: (mu, sg)))
        tot = sum(r.prob for r in regs)
        if abs(tot - 1.0) > 1e-6:
            raise ValueError(f"custom regime probs sum to {tot}, must be 1.0")
        return regs
    return default_regimes()


def draw_return(mu_arith: float, sigma: float, rng: np.random.Generator) -> float:
    """Lognormal draw calibrated to an arithmetic mean and sigma."""
    sigma_log = np.sqrt(np.log(1 + sigma ** 2 / (1 + mu_arith) ** 2))
    mu_log = np.log(1 + mu_arith) - 0.5 * sigma_log ** 2
    return float(np.exp(mu_log + sigma_log * rng.standard_normal()) - 1)


def pick_regime(regimes: list[Regime], rng: np.random.Generator) -> Regime:
    r = rng.random(); cum = 0.0
    for reg in regimes:
        cum += reg.prob
        if r < cum:
            return reg
    return regimes[-1]


def sample_lifetime_returns(total_years: int, regimes: list[Regime],
                            rng: np.random.Generator) -> tuple[str, list[float]]:
    reg = pick_regime(regimes, rng)
    return reg.name, [draw_return(*reg.params(y), rng)
                      for y in range(1, total_years + 1)]


# ============================================================
# ACCOUNT STACK
# ============================================================
@dataclass
class AccountStack:
    pretax: float = 0.0     # 401k / traditional IRA (pretax)
    roth: float = 0.0       # Roth IRA
    hsa: float = 0.0        # HSA
    taxable: float = 0.0    # brokerage

    @property
    def total(self) -> float:
        return self.pretax + self.roth + self.hsa + self.taxable

    def copy(self) -> "AccountStack":
        return AccountStack(self.pretax, self.roth, self.hsa, self.taxable)


# ============================================================
# TAX PARAMETERS (effective withdrawal tax rates by bucket)
# ============================================================
@dataclass
class TaxParams:
    drag_taxable: float = 0.0025   # annual tax drag on taxable holdings
    wd_taxable: float = 0.04       # cap-gains rate on taxable withdrawals
    wd_pretax: float = 0.12        # ordinary rate on pretax withdrawals
    wd_roth: float = 0.0
    wd_hsa: float = 0.0


ENGINE_VERSION = "2.3-rc"   # product engine: fork of skill fire_engine + v2 modules (all default OFF)

EARLY_WD_PENALTY_AGE = 59.5
EARLY_WD_PENALTY_RATE = 0.10


# ============================================================
# ALL-IN CONFIG (built from the YAML dict)
# ============================================================
@dataclass
class Plan:
    # timing
    start_age: int
    max_work_years: int
    retire_horizon: int
    # portfolio
    initial: AccountStack
    # income / savings (year-1 contributions; grown over time)
    c_pretax_y1: float
    c_match_y1: float
    c_roth_y1: float
    c_hsa_y1: float
    c_taxable_y1: float
    salary_growth: float
    irs_limit_growth: float
    # spending / SWR
    expenses_y0: float
    swr: float
    inflation: float
    milestones: list[float]
    # returns
    regimes: list[Regime]
    bond_alloc: float           # constant bond fraction (0 = 100% equity)
    bond_mean: float
    bond_sigma: float
    friction: float
    # taxes
    tax_us: TaxParams
    # retirement extras
    healthcare_annual_real: float   # pre-Medicare add-on (real $/yr), 0 to skip
    medicare_age: int
    # mortality
    mortality_enabled: bool
    # social security
    ss_enabled: bool
    ss_claim_age: int
    ss_annual_real: float       # annual benefit in today's dollars
    ss_nra_haircut: float       # fraction lost if abroad (0 if n/a)
    # relocation (None disables)
    reloc_age: Optional[int]
    reloc_col_ratio: float
    reloc_inflation: float
    fx_initial: float
    fx_drift: float
    fx_sigma: float
    tax_foreign: TaxParams
    healthcare_annual_real_foreign: float
    # ---- v2 modules (all default OFF => bitwise-identical to the base engine) ----
    hc_schedule: Optional[list] = None       # home-side piecewise: [{from_age,to_age,annual_real}]
    events: Optional[list] = None            # [{age, amount_real, label}]; +=outflow, -=inflow(credited to taxable)
    roth_ladder_enabled: bool = False
    roth_conv_annual_real: float = 0.0       # today's $ per year, CPI-indexed
    roth_conv_tax_rate: float = 0.12         # flat effective rate on conversions (progressive = Tier C tool)
    roth_conv_start_age: Optional[int] = None  # default: retirement start age
    layoff_enabled: bool = False
    layoff_p_annual: float = 0.025
    layoff_ret_threshold: float = -0.10
    layoff_ret_mult: float = 3.0
    layoff_p_cap: float = 0.50
    layoff_gap_months: float = 4.0
    creep_mode: str = "off"                  # off | fixed | clipnorm
    creep_magnitude: float = 0.15
    creep_sd: float = 0.05
    creep_cap: float = 0.25
    creep_year_lo: int = 2
    creep_year_hi: int = 5
    reloc_hc_lifetime: bool = False   # v2.1: 海外医疗无 Medicare cliff（65 后继续；默认 False=旧行为）
    reloc_hc_schedule: Optional[list] = None  # v2.2: 海外医疗年龄加载分段（私保 60+ 陡升）；优先级最高

    @staticmethod
    def from_config(cfg: dict) -> "Plan":
        p = cfg.get("portfolio", {})
        inc = cfg.get("income_and_savings", {})
        sp = cfg.get("spending", {})
        ra = cfg.get("return_assumptions", {})
        tx = cfg.get("taxes", {})
        hc = cfg.get("healthcare", {})
        mo = cfg.get("mortality", {})
        ss = cfg.get("social_security", {})
        rl = cfg.get("relocation", {})

        def tp(d, defaults):
            return TaxParams(
                drag_taxable=float(d.get("drag_taxable", defaults.drag_taxable)),
                wd_taxable=float(d.get("wd_taxable", defaults.wd_taxable)),
                wd_pretax=float(d.get("wd_pretax", defaults.wd_pretax)),
                wd_roth=float(d.get("wd_roth", defaults.wd_roth)),
                wd_hsa=float(d.get("wd_hsa", defaults.wd_hsa)),
            )

        us_tax = tp(tx.get("home", {}), TaxParams())
        foreign_defaults = TaxParams(wd_taxable=0.01, wd_pretax=0.10)
        foreign_tax = tp((rl.get("taxes", {}) if rl else {}), foreign_defaults)

        reloc_enabled = bool(rl.get("enabled", False)) if rl else False

        ev = cfg.get("events", []) or []
        rothl = cfg.get("roth_ladder", {}) or {}
        lo_ = cfg.get("layoff", {}) or {}
        cr = cfg.get("lifestyle_creep", {}) or {}

        return Plan(
            start_age=int(cfg["current_age"]),
            max_work_years=int(sp.get("max_work_years", 25)),
            retire_horizon=int(sp.get("retirement_horizon_years", 50)),
            initial=AccountStack(
                pretax=float(p.get("pretax_401k", 0)),
                roth=float(p.get("roth_ira", 0)),
                hsa=float(p.get("hsa", 0)),
                taxable=float(p.get("taxable", 0)),
            ),
            c_pretax_y1=float(inc.get("annual_pretax_401k", 0)),
            c_match_y1=float(inc.get("annual_employer_match", 0)),
            c_roth_y1=float(inc.get("annual_roth_ira", 0)),
            c_hsa_y1=float(inc.get("annual_hsa", 0)),
            c_taxable_y1=float(inc.get("annual_taxable", 0)),
            salary_growth=float(inc.get("salary_growth", 0.035)),
            irs_limit_growth=float(inc.get("contribution_limit_growth", 0.030)),
            expenses_y0=float(sp["annual_retirement_spending"]),
            swr=float(sp.get("safe_withdrawal_rate", 0.0333)),
            inflation=float(sp.get("inflation", 0.030)),
            milestones=[float(m) for m in sp.get("milestones", [1_000_000, 3_000_000])],
            regimes=regimes_from_config(cfg),
            bond_alloc=float(ra.get("bond_allocation", 0.0)),
            bond_mean=float(ra.get("bond_mean_return", 0.03)),
            bond_sigma=float(ra.get("bond_volatility", 0.05)),
            friction=float(ra.get("annual_friction", 0.005)),
            tax_us=us_tax,
            healthcare_annual_real=float(hc.get("pre_medicare_annual", 0.0)),
            medicare_age=int(hc.get("medicare_age", 65)),
            mortality_enabled=bool(mo.get("enabled", True)),
            ss_enabled=bool(ss.get("enabled", False)),
            ss_claim_age=int(ss.get("claim_age", 67)),
            ss_annual_real=float(ss.get("annual_benefit", 0.0)),
            ss_nra_haircut=float(ss.get("abroad_haircut", 0.0)),
            reloc_age=(int(rl["relocation_age"]) if reloc_enabled else None),
            reloc_col_ratio=float(rl.get("cost_of_living_ratio", 1.0)),
            reloc_inflation=float(rl.get("foreign_inflation", 0.025)),
            fx_initial=float(rl.get("fx_initial", 1.0)),
            fx_drift=float(rl.get("fx_drift", 0.0)),
            fx_sigma=float(rl.get("fx_volatility", 0.0)),
            tax_foreign=foreign_tax,
            healthcare_annual_real_foreign=float(
                rl.get("foreign_pre_medicare_annual",
                       hc.get("pre_medicare_annual", 0.0))),
            hc_schedule=(hc.get("schedule") or None),
            events=([{"age": int(e["age"]), "amount_real": float(e["amount_real"]),
                      "label": str(e.get("label", "event"))} for e in ev] or None),
            roth_ladder_enabled=bool(rothl.get("enabled", False)),
            roth_conv_annual_real=float(rothl.get("annual_conversion_real", 0.0)),
            roth_conv_tax_rate=float(rothl.get("conversion_tax_rate",
                                     tx.get("home", {}).get("wd_pretax", 0.12))),
            roth_conv_start_age=(int(rothl["start_age"])
                                 if rothl.get("start_age") is not None else None),
            layoff_enabled=bool(lo_.get("enabled", False)),
            layoff_p_annual=float(lo_.get("p_annual", 0.025)),
            layoff_ret_threshold=float(lo_.get("return_threshold", -0.10)),
            layoff_ret_mult=float(lo_.get("bad_year_multiplier", 3.0)),
            layoff_p_cap=float(lo_.get("p_cap", 0.50)),
            layoff_gap_months=float(lo_.get("gap_months", 4.0)),
            creep_mode=str(cr.get("mode", "off")),
            creep_magnitude=float(cr.get("magnitude", 0.15)),
            creep_sd=float(cr.get("sd", 0.05)),
            creep_cap=float(cr.get("cap", 0.25)),
            creep_year_lo=int(cr.get("year_lo", 2)),
            creep_year_hi=int(cr.get("year_hi", 5)),
            reloc_hc_lifetime=bool(rl.get("hc_lifetime", False)),
            reloc_hc_schedule=(rl.get("hc_schedule") or None),
        )


# ============================================================
# V2 HELPERS (hand-testable units)
# ============================================================
def _sched_val(sched, age: float) -> float:
    """Piecewise [from_age, to_age) lookup; outside all segments -> 0."""
    for seg in (sched or []):
        if float(seg["from_age"]) <= age < float(seg["to_age"]):
            return float(seg["annual_real"])
    return 0.0


def _hc_sched_real(plan: "Plan", age: float) -> float:
    """Home-side healthcare real $ at age from the piecewise schedule.
    Segments are [from_age, to_age); outside all segments -> 0 (the schedule
    itself is the gate; medicare_age is ignored when a schedule is provided)."""
    return _sched_val(plan.hc_schedule, age)


def apply_roth_conversion(acc: AccountStack, conv_nominal: float,
                          tax_rate: float, wd_taxable_rate: float
                          ) -> tuple[AccountStack, float, float]:
    """Convert up to conv_nominal pretax->roth. The flat conversion tax is paid
    FROM TAXABLE with gross-up (selling `gross` delivers exactly the tax bill
    after cap-gains). Stop-when-unaffordable rule: if taxable can't cover the
    grossed bill, skip the year entirely (v1; partial conversions = P2).
    Returns (accounts, converted_nominal, tax_bill)."""
    acc = acc.copy()
    conv = min(max(conv_nominal, 0.0), acc.pretax)
    if conv <= 0.0:
        return acc, 0.0, 0.0
    tax_bill = conv * tax_rate
    gross = tax_bill / max(1.0 - wd_taxable_rate, 1e-3)
    if acc.taxable < gross:
        return acc, 0.0, 0.0
    acc.taxable -= gross
    acc.pretax -= conv
    acc.roth += conv
    return acc, conv, tax_bill


# ============================================================
# ACCUMULATION
# ============================================================
def contributions_at_year(plan: Plan, year: int) -> AccountStack:
    if year < 1:
        return AccountStack()
    irs = (1 + plan.irs_limit_growth) ** (year - 1)
    sal = (1 + plan.salary_growth) ** (year - 1)
    return AccountStack(
        pretax=plan.c_pretax_y1 * irs + plan.c_match_y1 * sal,
        roth=plan.c_roth_y1 * irs,
        hsa=plan.c_hsa_y1 * irs,
        taxable=plan.c_taxable_y1 * sal,
    )


def project_accumulation(plan: Plan, returns: Sequence[float]) -> list[dict]:
    acc = plan.initial.copy()
    path = [{"age": plan.start_age, "total": acc.total,
             "expenses": plan.expenses_y0, "accounts": acc.copy()}]
    for i, r in enumerate(returns):
        acc.pretax *= (1 + r)
        acc.roth *= (1 + r)
        acc.hsa *= (1 + r)
        acc.taxable *= (1 + r - plan.tax_us.drag_taxable)
        year = i + 1
        c = contributions_at_year(plan, year)
        acc.pretax += c.pretax; acc.roth += c.roth
        acc.hsa += c.hsa; acc.taxable += c.taxable
        exp = plan.expenses_y0 * (1 + plan.inflation) ** year
        path.append({"age": plan.start_age + year, "total": acc.total,
                     "expenses": exp, "accounts": acc.copy()})
    return path


def project_accumulation_v2(plan: Plan, returns: Sequence[float],
                            career_rng: np.random.Generator) -> tuple[list[dict], dict]:
    """Accumulation with v2 features: layoff (contribution gap), lifestyle creep
    (permanent expense step), and one-off events. Stochastic draws come ONLY from
    career_rng (never the main stream). Draw order is fixed and documented:
    creep(year, magnitude) first, then per-year layoff uniforms."""
    meta = {"creep_applied_year": None, "creep_factor": 0.0,
            "layoff_years": [], "events_underfunded": 0}
    creep_year, creep_f = None, 0.0
    if plan.creep_mode != "off":
        creep_year = int(career_rng.integers(plan.creep_year_lo, plan.creep_year_hi + 1))
        if plan.creep_mode == "fixed":
            creep_f = plan.creep_magnitude
        else:  # clipnorm: normal clipped to [0, cap] (approx of truncnorm; disclosed)
            creep_f = float(np.clip(career_rng.normal(plan.creep_magnitude, plan.creep_sd),
                                    0.0, plan.creep_cap))
    events_by_age = {}
    for e in (plan.events or []):
        events_by_age[int(e["age"])] = events_by_age.get(int(e["age"]), 0.0) + float(e["amount_real"])

    acc = plan.initial.copy()
    creep_mult = 1.0
    path = [{"age": plan.start_age, "total": acc.total,
             "expenses": plan.expenses_y0, "accounts": acc.copy()}]
    for i, r in enumerate(returns):
        acc.pretax *= (1 + r)
        acc.roth *= (1 + r)
        acc.hsa *= (1 + r)
        acc.taxable *= (1 + r - plan.tax_us.drag_taxable)
        year = i + 1
        age = plan.start_age + year
        c = contributions_at_year(plan, year)
        frac = 1.0
        if plan.layoff_enabled:
            p = plan.layoff_p_annual * (plan.layoff_ret_mult
                                        if r <= plan.layoff_ret_threshold else 1.0)
            p = min(plan.layoff_p_cap, p)
            if career_rng.random() < p:
                frac = max(0.0, 1.0 - plan.layoff_gap_months / 12.0)
                meta["layoff_years"].append(age)
        acc.pretax += c.pretax * frac; acc.roth += c.roth * frac
        acc.hsa += c.hsa * frac; acc.taxable += c.taxable * frac
        if creep_year is not None and year == creep_year:
            creep_mult = 1.0 + creep_f
            meta["creep_applied_year"] = age; meta["creep_factor"] = creep_f
        ev = events_by_age.get(age, 0.0)
        if ev:
            nominal_ev = ev * (1 + plan.inflation) ** year
            if nominal_ev > 0:
                acc, delivered, _pen = withdraw_from_stack(acc, nominal_ev, plan.tax_us, age)
                if delivered < nominal_ev - 1.0:
                    meta["events_underfunded"] += 1
            else:
                acc.taxable += -nominal_ev
        exp = plan.expenses_y0 * creep_mult * (1 + plan.inflation) ** year
        path.append({"age": age, "total": acc.total,
                     "expenses": exp, "accounts": acc.copy()})
    return path, meta


def find_fire_crossing(path: list[dict], swr: float) -> Optional[dict]:
    for step in path:
        if step["total"] >= step["expenses"] / swr:
            return step
    return None


def find_milestone_age(path: list[dict], target: float) -> Optional[int]:
    for step in path:
        if step["total"] >= target:
            return step["age"]
    return None


# ============================================================
# GUYTON-KLINGER GUARDRAIL RULE (freeze anchored to cpi_at_init)
# ============================================================
@dataclass
class GKRule:
    upper_guardrail: float = 0.20
    lower_guardrail: float = 0.20
    adjustment_pct: float = 0.10
    freeze_enabled: bool = True

    def initialize(self, portfolio_nominal: float, w_nominal: float,
                   swr: float, cpi_at_init: float) -> dict:
        return {
            "initial_w_nominal": w_nominal,
            "initial_swr": swr,
            "prev_w_nominal": w_nominal,
            "prev_portfolio_nominal": portfolio_nominal,
            "cpi_at_init": max(float(cpi_at_init), 1e-12),
            "guardrail_triggers": 0,
        }

    def target(self, year_in_retirement: int, portfolio_nominal: float,
               inflation_this_year: float, cpi_cumulative: float,
               state: dict) -> tuple[float, dict]:
        if year_in_retirement == 0:
            t = state["initial_w_nominal"]
            s = dict(state); s["prev_w_nominal"] = t
            s["prev_portfolio_nominal"] = portfolio_nominal
            return t, s

        prev_w = state["prev_w_nominal"]
        prev_port = state["prev_portfolio_nominal"]
        init_w = state["initial_w_nominal"]
        init_swr = state["initial_swr"]
        triggers = state.get("guardrail_triggers", 0)

        tentative = prev_w * (1 + inflation_this_year)

        # Inflation-freeze rule, compared on a same-real basis (the fix):
        if self.freeze_enabled:
            cpi_at_init = max(state.get("cpi_at_init", 1.0), 1e-12)
            if (portfolio_nominal < prev_port
                    and tentative / cpi_cumulative > init_w / cpi_at_init):
                tentative = prev_w

        implied = tentative / max(portfolio_nominal, 1.0)
        if implied > init_swr * (1 + self.upper_guardrail):
            tentative *= (1 - self.adjustment_pct); triggers += 1
        elif implied < init_swr * (1 - self.lower_guardrail):
            tentative *= (1 + self.adjustment_pct); triggers += 1

        s = dict(state)
        s["prev_w_nominal"] = tentative
        s["prev_portfolio_nominal"] = portfolio_nominal
        s["guardrail_triggers"] = triggers
        return tentative, s


def withdraw_from_stack(acc: AccountStack, needed_after_tax: float,
                        tax: TaxParams, age: float) -> tuple[AccountStack, float, float]:
    """Withdraw in order taxable -> pretax -> hsa -> roth. Returns (accounts,
    after-tax dollars actually delivered, early-withdrawal penalty)."""
    acc = acc.copy(); remaining = needed_after_tax; penalty = 0.0

    if remaining > 0 and acc.taxable > 0:
        rate = tax.wd_taxable
        take = min(remaining / max(1 - rate, 1e-3), acc.taxable)
        acc.taxable -= take; remaining -= take * (1 - rate)

    if remaining > 0 and acc.pretax > 0:
        rate = tax.wd_pretax + (EARLY_WD_PENALTY_RATE
                                if age < EARLY_WD_PENALTY_AGE else 0.0)
        take = min(remaining / max(1 - rate, 1e-3), acc.pretax)
        acc.pretax -= take; remaining -= take * (1 - rate)
        if age < EARLY_WD_PENALTY_AGE:
            penalty += take * EARLY_WD_PENALTY_RATE

    if remaining > 0 and acc.hsa > 0:
        rate = tax.wd_hsa
        take = min(remaining / max(1 - rate, 1e-3), acc.hsa)
        acc.hsa -= take; remaining -= take * (1 - rate)

    if remaining > 0 and acc.roth > 0:
        take = min(remaining, acc.roth)
        acc.roth -= take; remaining -= take

    delivered = needed_after_tax - max(remaining, 0.0)
    return acc, delivered, penalty


# ============================================================
# MORTALITY (Gompertz-style, simple)
# ============================================================
def annual_mortality_rate(age: int) -> float:
    # Rough US-unisex hazard; monotone increasing. Only used to end paths.
    if age < 60:
        return 0.004
    return min(0.5, 0.004 * (1.09 ** (age - 60)))


# ============================================================
# RETIREMENT SIMULATION
# ============================================================
def blended_return(eq_r: float, bd_r: float, bond_alloc: float) -> float:
    return (1 - bond_alloc) * eq_r + bond_alloc * bd_r


def simulate_retirement(plan: Plan, start_age: int, fire_cpi: float,
                        eq_returns: Sequence[float], bd_returns: Sequence[float],
                        inflations: Sequence[float], gk: GKRule,
                        start_accounts: AccountStack,
                        rng: np.random.Generator,
                        expenses_at_fire: Optional[float] = None) -> dict:
    acc = start_accounts.copy()
    starts_abroad = plan.reloc_age is not None and plan.reloc_age <= start_age

    # --- initial retirement-expense seed (GROSS basis: include healthcare) ---
    hc0 = 0.0
    if start_age < plan.medicare_age:
        hc0 = (plan.healthcare_annual_real_foreign if starts_abroad
               else plan.healthcare_annual_real) * fire_cpi
    if plan.hc_schedule and not starts_abroad:   # v2: home schedule supersedes flat+gate
        hc0 = _hc_sched_real(plan, start_age) * fire_cpi
    if starts_abroad and plan.reloc_hc_lifetime and start_age >= plan.medicare_age:
        hc0 = plan.healthcare_annual_real_foreign * fire_cpi   # v2.1: no Medicare cliff abroad
    if starts_abroad and plan.reloc_hc_schedule:               # v2.2: 年龄加载分段（最高优先）
        hc0 = _sched_val(plan.reloc_hc_schedule, start_age) * fire_cpi
    base_exp = (expenses_at_fire if expenses_at_fire is not None
                else plan.expenses_y0 * fire_cpi)
    if starts_abroad:
        initial_expenses = base_exp * plan.reloc_col_ratio + hc0
    else:
        initial_expenses = base_exp + hc0

    init_swr = initial_expenses / max(acc.total, 1.0)
    state = gk.initialize(acc.total, initial_expenses, init_swr, fire_cpi)

    in_abroad = starts_abroad
    reloc_done = starts_abroad
    gk_reseeded = starts_abroad
    reseed_idx = 0
    cpi_track = fire_cpi
    fx_rate = plan.fx_initial
    fx_at_reloc = plan.fx_initial if starts_abroad else None

    cpi_cum = fire_cpi
    cpi_at_ss = None
    survived = True
    shortfall_age = None
    age_at_death = None

    real_cons, nom_cons, lifestyle_real = [], [], []
    port_path = [acc.total]
    total_wd = 0.0; total_ss_applied = 0.0; ss_real = 0.0
    total_event_out = 0.0; total_conv_tax = 0.0
    events_by_age = {}
    if plan.events:
        for _e in plan.events:
            if int(_e["age"]) > start_age:
                events_by_age[int(_e["age"])] = (events_by_age.get(int(_e["age"]), 0.0)
                                                 + float(_e["amount_real"]))

    for yi, (eq_r, bd_r, inf) in enumerate(zip(eq_returns, bd_returns, inflations)):
        age = start_age + yi + 1
        cpi_cum *= (1 + inf)

        r_eff = blended_return(eq_r, bd_r, plan.bond_alloc) - plan.friction
        acc.pretax *= (1 + r_eff); acc.roth *= (1 + r_eff); acc.hsa *= (1 + r_eff)
        acc.taxable *= (1 + r_eff - plan.tax_us.drag_taxable)

        if plan.mortality_enabled and rng.random() < annual_mortality_rate(age):
            age_at_death = age; port_path.append(acc.total); break

        # relocation event
        if (plan.reloc_age is not None and not reloc_done and age >= plan.reloc_age):
            in_abroad = True; reloc_done = True; fx_at_reloc = fx_rate

        if plan.fx_sigma > 0:
            fx_rate *= np.exp(plan.fx_drift + plan.fx_sigma * rng.standard_normal())

        col_eff = plan.reloc_col_ratio if in_abroad else 1.0
        hc_real = (plan.healthcare_annual_real_foreign if in_abroad
                   else plan.healthcare_annual_real)
        hc_nominal = hc_real * cpi_cum if age < plan.medicare_age else 0.0
        if plan.hc_schedule and not in_abroad:   # v2: home schedule supersedes flat+gate
            hc_nominal = _hc_sched_real(plan, age) * cpi_cum
        if in_abroad and plan.reloc_hc_lifetime and age >= plan.medicare_age:
            hc_nominal = plan.healthcare_annual_real_foreign * cpi_cum   # v2.1
        if in_abroad and plan.reloc_hc_schedule:               # v2.2: 年龄加载分段（最高优先）
            hc_nominal = _sched_val(plan.reloc_hc_schedule, age) * cpi_cum

        # ---- GK target (with re-seed at relocation) ----
        if in_abroad and not gk_reseeded:
            target_home, state = gk.target(yi, acc.total, inf, cpi_cum, state)
            # strip home healthcare from the target, apply CoL to the rest,
            # add destination healthcare -> destination living standard L
            home_hc = (plan.healthcare_annual_real * cpi_cum
                       if age < plan.medicare_age else 0.0)
            if plan.hc_schedule:                 # v2: strip the schedule value instead
                home_hc = _hc_sched_real(plan, age) * cpi_cum
            L = max(0.0, target_home - home_hc) * col_eff + hc_nominal
            fx_ratio = fx_rate / max(fx_at_reloc, 1e-9)
            prev_trig = state.get("guardrail_triggers", 0)
            state = gk.initialize(acc.total * fx_ratio, L,
                                  L / max(acc.total * fx_ratio, 1.0), cpi_cum)
            state["guardrail_triggers"] = prev_trig
            cpi_track = cpi_cum; gk_reseeded = True; reseed_idx = yi
            target_nominal = L * (fx_at_reloc / fx_rate)
        elif in_abroad and gk_reseeded:
            cpi_track *= (1 + plan.reloc_inflation)
            fx_ratio = fx_rate / max(fx_at_reloc, 1e-9)
            target_L, state = gk.target(yi - reseed_idx, acc.total * fx_ratio,
                                        plan.reloc_inflation, cpi_track, state)
            target_nominal = target_L * (fx_at_reloc / fx_rate)
        else:
            target_nominal, state = gk.target(yi, acc.total, inf, cpi_cum, state)
            cpi_track = cpi_cum
            # add pre-Medicare healthcare on top of the home target
            target_nominal += hc_nominal

        # ---- Social Security (offsets withdrawals; cash conservation) ----
        ss_income = 0.0
        if plan.ss_enabled:
            if age == plan.ss_claim_age:
                cpi_at_ss = cpi_cum
            if cpi_at_ss is not None and age >= plan.ss_claim_age:
                ss_income = plan.ss_annual_real * cpi_cum
                if in_abroad:
                    ss_income *= (1 - plan.ss_nra_haircut)
        ss_real += ss_income / cpi_cum

        ss_applied = min(ss_income, target_nominal)
        surplus = ss_income - ss_applied
        if surplus > 0:
            acc.taxable += surplus
        needed = max(0.0, target_nominal - ss_applied)
        tax = plan.tax_foreign if in_abroad else plan.tax_us

        acc, delivered, _ = withdraw_from_stack(acc, needed, tax, age)
        total_wd += delivered; total_ss_applied += ss_applied

        if delivered < needed - 1.0:
            survived = False; shortfall_age = age
            consumed = delivered + ss_applied
            real_cons.append(max(0.0, consumed) / cpi_cum)
            nom_cons.append(max(0.0, consumed))
            lifestyle_real.append(max(0.0, consumed) / col_eff / cpi_cum)
            port_path.append(acc.total); break

        # ---- v2: Roth conversion ladder + one-off events (guarded; OFF = zero ops) ----
        if plan.roth_ladder_enabled or events_by_age:
            if plan.roth_ladder_enabled and acc.pretax > 0:
                _cstart = (plan.roth_conv_start_age
                           if plan.roth_conv_start_age is not None else start_age)
                if age >= _cstart:
                    acc, _conv, _ctax = apply_roth_conversion(
                        acc, plan.roth_conv_annual_real * cpi_cum,
                        plan.roth_conv_tax_rate, tax.wd_taxable)
                    total_conv_tax += _ctax; total_wd += _ctax
            _ev = events_by_age.get(age, 0.0)
            if _ev:
                _nom_ev = _ev * cpi_cum
                if _nom_ev > 0:
                    acc, _ev_del, _ = withdraw_from_stack(acc, _nom_ev, tax, age)
                    total_event_out += _ev_del; total_wd += _ev_del
                    if _ev_del < _nom_ev - 1.0:   # committed expense unmet = failure (disclosed)
                        survived = False; shortfall_age = age
                        consumed = max(0.0, target_nominal)
                        real_cons.append(consumed / cpi_cum)
                        nom_cons.append(consumed)
                        lifestyle_real.append(consumed / col_eff / cpi_cum)
                        port_path.append(acc.total); break
                else:
                    acc.taxable += -_nom_ev      # inflow lands (never a counter)

        consumed = max(0.0, target_nominal)
        real_cons.append(consumed / cpi_cum)
        nom_cons.append(consumed)
        lifestyle_real.append(consumed / col_eff / cpi_cum)
        port_path.append(acc.total)

    return {
        "survived": survived,
        # port_path[i] is the nominal portfolio total at (retire_start_age + i);
        # index 0 is the value at retirement start. Exposed READ-ONLY for the
        # app's fan chart — this is the ONLY delta from the pipeline engine and
        # it changes no math, no summary stat, and no invariant.
        "port_path": list(port_path),
        "real_cons_path": list(real_cons),   # real (today's $) consumption, per retirement year (READ-ONLY, illustrative)
        "retire_start_age": start_age,
        "died_in_retirement": age_at_death is not None,
        "age_at_death": age_at_death,
        "shortfall_age": shortfall_age,
        "terminal_nominal": acc.total if survived else 0.0,
        "terminal_real": (acc.total / cpi_cum) if survived else 0.0,
        "in_abroad_at_end": in_abroad,
        "guardrail_triggers": state.get("guardrail_triggers", 0),
        "mean_real_consumption": float(np.mean(real_cons)) if real_cons else 0.0,
        "min_real_consumption": float(np.min(real_cons)) if real_cons else 0.0,
        "mean_lifestyle_real": float(np.mean(lifestyle_real)) if lifestyle_real else 0.0,
        "ss_total_real": ss_real,
        # cash-conservation ledger (per path):
        "_sum_nom_consumption": float(np.sum(nom_cons)),
        "_sum_wd": total_wd,
        "_sum_ss_applied": total_ss_applied,
        "_sum_event_out": total_event_out,
        "_sum_conv_tax": total_conv_tax,
        "cpi_end": cpi_cum,
        "terminal_pretax": acc.pretax if survived else 0.0,
        "terminal_roth": acc.roth if survived else 0.0,
        "terminal_hsa": acc.hsa if survived else 0.0,
        "terminal_taxable": acc.taxable if survived else 0.0,
        "lifetime_success": survived,
    }


# ============================================================
# FULL LIFECYCLE (one path) — three-branch semantics
# ============================================================
def simulate_lifecycle(plan: Plan, rng: np.random.Generator,
                       career_rng: Optional[np.random.Generator] = None) -> dict:
    total_years = plan.max_work_years + plan.retire_horizon
    _, eq_all = sample_lifetime_returns(total_years, plan.regimes, rng)
    # bonds (only used if bond_alloc > 0)
    bd_all = [draw_return(plan.bond_mean, plan.bond_sigma, rng)
              for _ in range(total_years)]
    # accumulation uses equity path (contributions land in the blended stack;
    # for simplicity accumulation is modeled at equity returns, matching the
    # 100%-equity accumulation assumption; bond_alloc affects retirement only)
    accum_returns = eq_all[:plan.max_work_years]
    _use_v2 = (plan.layoff_enabled or plan.creep_mode != "off" or bool(plan.events))
    if _use_v2:
        if career_rng is None:
            career_rng = np.random.default_rng(7_000_000)
        path, _accum_meta = project_accumulation_v2(plan, accum_returns, career_rng)
    else:
        path = project_accumulation(plan, accum_returns)

    fire_step = find_fire_crossing(path, plan.swr)
    milestone_ages = {m: find_milestone_age(path, m) for m in plan.milestones}

    if fire_step is None:
        return {"reached_fire": False, "lifetime_success": False,
                "died_during_accum": False, "fire_age": None,
                "milestone_ages": milestone_ages, "retirement": None}

    fire_age = fire_step["age"]
    fire_year_idx = fire_age - plan.start_age
    # CPI at FIRE (nominal accumulation): expenses ratio
    fire_cpi = (1 + plan.inflation) ** fire_year_idx

    # mortality before FIRE? (only checked on paths that WOULD reach FI)
    if plan.mortality_enabled:
        for a in range(plan.start_age + 1, fire_age + 1):
            if rng.random() < annual_mortality_rate(a):
                return {"reached_fire": False, "lifetime_success": True,
                        "died_during_accum": True, "fire_age": None,
                        "milestone_ages": milestone_ages, "retirement": None}

    eq_ret = eq_all[fire_year_idx:fire_year_idx + plan.retire_horizon]
    bd_ret = bd_all[fire_year_idx:fire_year_idx + plan.retire_horizon]
    inflations = [plan.inflation] * len(eq_ret)
    gk = GKRule()
    ret = simulate_retirement(plan, fire_age, fire_cpi, eq_ret, bd_ret,
                              inflations, gk, fire_step["accounts"].copy(), rng,
                              expenses_at_fire=fire_step["expenses"])

    return {"reached_fire": True, "lifetime_success": ret["lifetime_success"],
            "died_during_accum": False, "fire_age": fire_age,
            "milestone_ages": milestone_ages, "retirement": ret}


# ============================================================
# MONTE CARLO DRIVER + AGGREGATION
# ============================================================
def _pctiles(xs: list[float]) -> dict:
    if not xs:
        return {"p10": 0.0, "p50": 0.0, "p90": 0.0}
    a = np.array(xs, dtype=float)
    return {"p10": float(np.percentile(a, 10)),
            "p50": float(np.percentile(a, 50)),
            "p90": float(np.percentile(a, 90))}


def run_monte_carlo(plan: Plan, n_paths: int, seed: int,
                    check_invariant: bool = True) -> dict:
    rng = np.random.default_rng(seed)
    career_rng = np.random.default_rng(seed + 7_000_000)  # consumed only when v2 accum features are on
    reached = died_accum = solvent = 0
    fire_ages, term_nom, term_real = [], [], []
    mean_cons, min_cons, ss_totals, lifestyle = [], [], [], []
    at_real = {12: [], 18: [], 24: []}; pretax_share = []; at_dest = []
    ev_out_all, conv_tax_all = [], []
    milestone_hits = {m: [] for m in plan.milestones}   # ages when reached
    milestone_reach_count = {m: 0 for m in plan.milestones}

    inv_checked = 0; inv_max_err = 0.0

    for i in range(n_paths):
        res = simulate_lifecycle(plan, rng, career_rng)
        for m in plan.milestones:
            a = res["milestone_ages"].get(m)
            if a is not None:
                milestone_hits[m].append(a); milestone_reach_count[m] += 1

        if res["died_during_accum"]:
            died_accum += 1
            continue
        if not res["reached_fire"]:
            continue

        reached += 1
        fire_ages.append(res["fire_age"])
        ret = res["retirement"]
        if ret["survived"]:
            solvent += 1
            term_nom.append(ret["terminal_nominal"])
            term_real.append(ret["terminal_real"])
            _ce = max(ret["cpi_end"], 1e-9)
            _liq = ret["terminal_taxable"] + ret["terminal_roth"] + ret["terminal_hsa"]
            for _h in (12, 18, 24):
                at_real[_h].append((_liq + ret["terminal_pretax"] * (1 - _h / 100.0)) / _ce)
            pretax_share.append(ret["terminal_pretax"] / max(ret["terminal_nominal"], 1.0))
            if plan.reloc_age is not None:  # v2.3: 目的地一致的税后终值（per-bucket 目的地税率）
                at_dest.append((ret["terminal_taxable"] + ret["terminal_hsa"]
                                + ret["terminal_roth"] * (1 - plan.tax_foreign.wd_roth)
                                + ret["terminal_pretax"] * (1 - plan.tax_foreign.wd_pretax)) / _ce)
        ev_out_all.append(ret["_sum_event_out"]); conv_tax_all.append(ret["_sum_conv_tax"])
        mean_cons.append(ret["mean_real_consumption"])
        min_cons.append(ret["min_real_consumption"])
        lifestyle.append(ret["mean_lifestyle_real"])
        ss_totals.append(ret["ss_total_real"])

        # cash-conservation invariant on a sample of solvent paths
        if check_invariant and ret["survived"] and inv_checked < 200:
            lhs = (ret["_sum_nom_consumption"] + ret["_sum_event_out"]
                   + ret["_sum_conv_tax"])
            rhs = ret["_sum_wd"] + ret["_sum_ss_applied"]
            denom = max(abs(lhs), 1.0)
            err = abs(lhs - rhs) / denom
            inv_max_err = max(inv_max_err, err); inv_checked += 1

    if check_invariant and inv_checked > 0 and inv_max_err > 0.02:
        raise AssertionError(
            f"Cash-conservation invariant violated: max relative error "
            f"{inv_max_err:.4f} over {inv_checked} paths (should be ~0). "
            f"This means reported consumption != withdrawals + SS. Stop and "
            f"debug before trusting any output.")

    # lifetime success = (solvent among reached) + (died during accumulation)
    # over ALL paths — the three-branch definition.
    lifetime_success = (solvent + died_accum) / n_paths
    reached_rate = reached / n_paths
    true_accum_fail = (n_paths - reached - died_accum) / n_paths
    post_fire_solvency = (solvent / reached) if reached else 0.0

    out = {
        "n_paths": n_paths,
        "seed": seed,
        "lifetime_success": lifetime_success,
        "reached_fi_rate": reached_rate,
        "died_during_accum_rate": died_accum / n_paths,
        "true_accumulation_failure_rate": true_accum_fail,
        "post_fire_solvency": post_fire_solvency,
        "fire_age": {
            "p10": float(np.percentile(fire_ages, 10)) if fire_ages else None,
            "p50": float(np.percentile(fire_ages, 50)) if fire_ages else None,
            "p90": float(np.percentile(fire_ages, 90)) if fire_ages else None,
            "min": int(np.min(fire_ages)) if fire_ages else None,
        },
        "milestones": {},
        "terminal_nominal": _pctiles(term_nom),
        "terminal_real": _pctiles(term_real),
        "mean_real_consumption": _pctiles(mean_cons),
        "min_real_consumption": _pctiles(min_cons),
        "mean_lifestyle_real": _pctiles(lifestyle),
        "ss_total_real": _pctiles(ss_totals),
        "invariant_max_rel_error": inv_max_err,
        "invariant_paths_checked": inv_checked,
        "engine_version": ENGINE_VERSION,
        "after_tax_terminal_real": {f"h{_h}": _pctiles(at_real[_h]) for _h in (12, 18, 24)},
        "terminal_pretax_share_p50": (float(np.percentile(pretax_share, 50))
                                       if pretax_share else 0.0),
        "after_tax_terminal_dest_real": (_pctiles(at_dest) if at_dest else None),
        "mean_event_outflow_nominal": float(np.mean(ev_out_all)) if ev_out_all else 0.0,
        "mean_conversion_tax_nominal": float(np.mean(conv_tax_all)) if conv_tax_all else 0.0,
    }
    for m in plan.milestones:
        ages = milestone_hits[m]
        out["milestones"][str(int(m))] = {
            "reach_probability": milestone_reach_count[m] / n_paths,
            "median_age": float(np.percentile(ages, 50)) if ages else None,
            "p10_age": float(np.percentile(ages, 10)) if ages else None,
            "p90_age": float(np.percentile(ages, 90)) if ages else None,
        }
    return out


def run_scenarios(cfg: dict, n_paths: int, seed: int) -> dict:
    """Run the base (home-only) scenario and, if relocation is enabled, a second
    scenario with relocation active. Returns both under a common structure."""
    base_cfg = dict(cfg)
    # scenario 1: home-only (force relocation off)
    home_cfg = _deep_merge(base_cfg, {"relocation": {"enabled": False}})
    plan_home = Plan.from_config(home_cfg)
    result = {"meta": _meta(cfg), "home": run_monte_carlo(plan_home, n_paths, seed)}

    rl = cfg.get("relocation", {}) or {}
    if rl.get("enabled", False):
        plan_reloc = Plan.from_config(cfg)
        result["relocation"] = run_monte_carlo(plan_reloc, n_paths, seed)
    return result


def _meta(cfg: dict) -> dict:
    sp = cfg.get("spending", {})
    return {
        "name": cfg.get("name", "FIRE plan"),
        "current_age": cfg.get("current_age"),
        "annual_retirement_spending": sp.get("annual_retirement_spending"),
        "safe_withdrawal_rate": sp.get("safe_withdrawal_rate", 0.0333),
        "relocation_enabled": bool((cfg.get("relocation") or {}).get("enabled", False)),
    }


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
