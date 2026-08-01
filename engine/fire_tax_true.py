"""
fire_tax_true.py — opt-in TRUE year-by-year US tax engine (2.0 pillar E1).

Replaces the flat effective-rate approximation with real mechanics:
  * ordinary brackets + standard deduction (2026 tables, IRS-indexed => REAL $)
  * LTCG stacked ON TOP of ordinary income (0/15/20 thresholds)
  * Social Security taxation via the two-tier provisional-income rule —
    thresholds are FIXED IN NOMINAL LAW (unindexed), so inflation drags more
    SS into taxation over time (the "tax torpedo"), modeled faithfully
  * RMDs from the IRS Uniform Lifetime Table (SECURE 2.0: default age 75)
  * IRMAA Part B+D surcharges by MAGI tier (2026 tiers, treated as indexed)
  * early-withdrawal penalty (10% pre-59.5) unchanged from v9.4 semantics

Design contract (same discipline as every other extension):
  enabled=False  =>  the caller never touches this module: bit-identical runs.

VINTAGE: all tables 2026 (single & true MFJ — not the ×2 approximation).
Treat as editable starting points; tests/test_regression.py alarms on staleness.
"""
from __future__ import annotations

from dataclasses import dataclass

from fire_rule_pack import IRMAA_RULES, US_FEDERAL_RULES


# ---------------------------------------------------------------- parameters
@dataclass
class TrueTaxParams:
    enabled: bool = False
    filing_jointly: bool = False          # adapter sets from household.enabled
    rmd_enabled: bool = True
    rmd_age: int = 75                     # SECURE 2.0: 75 for those born ≥1960
    irmaa_enabled: bool = True
    # LTCG share of every taxable-account withdrawal (cost-basis proxy; the
    # engine does not track lots — disclosed approximation).
    taxable_gain_fraction: float = 0.5
    state_rate: float = 0.0               # flat state tax on ordinary + LTCG


# ------------------------------------------------------- 2026 tables (real $)
# Ordinary brackets: (lower_bound, rate). IRS-indexed => stated in real terms.
ORD_SINGLE = list(US_FEDERAL_RULES["ordinary_single"])
ORD_MFJ = list(US_FEDERAL_RULES["ordinary_mfj"])
STD_DED_SINGLE = US_FEDERAL_RULES["std_deduction_single"]
STD_DED_MFJ = US_FEDERAL_RULES["std_deduction_mfj"]
# LTCG rate thresholds on (ordinary_taxable + ltcg) stacking basis.
LTCG_SINGLE = list(US_FEDERAL_RULES["ltcg_single"])
LTCG_MFJ = list(US_FEDERAL_RULES["ltcg_mfj"])
# SS provisional-income thresholds — NOMINAL by statute (never indexed).
SS_T1_SINGLE, SS_T2_SINGLE = US_FEDERAL_RULES["ss_provisional_single"]
SS_T1_MFJ, SS_T2_MFJ = US_FEDERAL_RULES["ss_provisional_mfj"]
# IRS Uniform Lifetime Table (divisors), ages 72..120+.
RMD_TABLE = dict(US_FEDERAL_RULES["rmd_divisors"])
# IRMAA 2026: per-person ANNUAL Part B+D surcharge (above standard premium).
# Published intermediate MAGI thresholds are strict lower bounds for the next
# tier: an exact threshold remains in the preceding tier. The final
# $500,000/$750,000 threshold is inclusive for the top tier. (Rounded; tiers
# indexed annually => treated as real $.)
IRMAA_SINGLE = list(IRMAA_RULES["single"])
IRMAA_MFJ = list(IRMAA_RULES["mfj"])

EARLY_WD_AGE = US_FEDERAL_RULES["early_withdrawal_age"]
EARLY_WD_RATE = US_FEDERAL_RULES["early_withdrawal_rate"]


# ------------------------------------------------------------- pure functions
def ordinary_tax_real(taxable_real: float, mfj: bool) -> float:
    """Tax on ordinary TAXABLE income (already net of deduction), real $."""
    if taxable_real <= 0:
        return 0.0
    br = ORD_MFJ if mfj else ORD_SINGLE
    tax = 0.0
    for i, (lo, rate) in enumerate(br):
        hi = br[i + 1][0] if i + 1 < len(br) else float("inf")
        if taxable_real <= lo:
            break
        tax += (min(taxable_real, hi) - lo) * rate
    return tax


def ltcg_tax_real(ltcg_real: float, ordinary_taxable_real: float, mfj: bool) -> float:
    """LTCG stacked on top of ordinary taxable income (0/15/20)."""
    if ltcg_real <= 0:
        return 0.0
    br = LTCG_MFJ if mfj else LTCG_SINGLE
    base = max(0.0, ordinary_taxable_real)
    top = base + ltcg_real
    tax = 0.0
    for i, (lo, rate) in enumerate(br):
        hi = br[i + 1][0] if i + 1 < len(br) else float("inf")
        seg = max(0.0, min(top, hi) - max(base, lo))
        tax += seg * rate
    return tax


def ss_taxable_amount(ss_nominal: float, other_income_nominal: float,
                      mfj: bool) -> float:
    """Two-tier provisional-income rule. NOMINAL thresholds by statute."""
    if ss_nominal <= 0:
        return 0.0
    t1 = SS_T1_MFJ if mfj else SS_T1_SINGLE
    t2 = SS_T2_MFJ if mfj else SS_T2_SINGLE
    prov = other_income_nominal + 0.5 * ss_nominal
    if prov <= t1:
        return 0.0
    tier1 = min(0.5 * ss_nominal, 0.5 * (prov - t1), 0.5 * (t2 - t1))
    if prov <= t2:
        return min(tier1, 0.85 * ss_nominal)
    tier2 = 0.85 * (prov - t2)
    return min(tier1 + tier2, 0.85 * ss_nominal)


def rmd_required(pretax_balance: float, age: int, p: TrueTaxParams) -> float:
    if not p.rmd_enabled or age < p.rmd_age or pretax_balance <= 0:
        return 0.0
    div = RMD_TABLE.get(min(int(age), 120), 2.0 if age > 120 else None)
    return pretax_balance / div if div else 0.0


def irmaa_annual_surcharge_real(magi_real: float, mfj: bool, persons: int) -> float:
    tiers = IRMAA_MFJ if mfj else IRMAA_SINGLE
    top_low, top_sur = tiers[-1]
    if magi_real >= top_low:
        return top_sur * persons
    for low, sur in reversed(tiers[1:-1]):
        if magi_real > low:
            return sur * persons
    return tiers[0][1] * persons


# --------------------------------------------------------------- year solver
def solve_retirement_year(accounts, need_after_tax_nominal: float,
                          ss_gross_nominal: float, conversions_nominal: float,
                          roth_locked: float, age: float, cpi: float,
                          p: TrueTaxParams,
                          rmd_balance_prior_year_end: float = None) -> dict:
    """Withdraw (taxable → pretax → HSA → unlocked Roth) so that after REAL
    taxes the year's need is met. Fixed-point on the tax bill (≤8 iters).

    Cash-flow identity enforced (returned as flow_err, should be ≈0):
        Σgross_wd + ss_gross == need_met + tax_total + deposit_back
    RMD excess (forced draw beyond spending+tax) is deposited back to taxable.
    Returns dict(accounts, tax_total, penalty, ss_taxable, magi_agi_nominal,
                 magi_aca_nominal, delivered, deposit_back, gross_wd, flow_err,
                 shortfall).
    """
    mfj = p.filing_jointly
    std_ded_nom = (STD_DED_MFJ if mfj else STD_DED_SINGLE) * cpi
    bal_tax, bal_pre = accounts.taxable, accounts.pretax_401k
    bal_hsa, bal_roth = accounts.hsa, max(0.0, accounts.roth_ira - roth_locked)
    # RMD law uses the account value at the close of the prior December 31,
    # not the post-return balance available when this year's withdrawal runs.
    rmd_base = (bal_pre if rmd_balance_prior_year_end is None
                else max(0.0, rmd_balance_prior_year_end))
    forced_rmd = min(rmd_required(rmd_base, int(age), p), bal_pre)

    tax_guess = 0.0
    w_tax = w_pre = w_hsa = w_roth = 0.0
    ss_taxable = ordinary_nom = ltcg_nom = penalty = 0.0
    for _ in range(8):
        cash_target = max(0.0, need_after_tax_nominal + tax_guess - ss_gross_nominal)
        w_tax = min(cash_target, bal_tax)
        rem = cash_target - w_tax
        w_pre = min(rem, bal_pre)
        w_pre = max(w_pre, forced_rmd)               # RMD floor
        rem = max(0.0, rem - w_pre)
        w_hsa = min(rem, bal_hsa)
        rem -= w_hsa
        w_roth = min(rem, bal_roth)
        rem -= w_roth

        ordinary_nom = w_pre + conversions_nominal
        ltcg_nom = w_tax * p.taxable_gain_fraction
        ss_taxable = ss_taxable_amount(ss_gross_nominal,
                                       ordinary_nom + ltcg_nom, mfj)
        ordinary_before_ded_nom = ordinary_nom + ss_taxable
        taxable_ord_nom = max(0.0, ordinary_before_ded_nom - std_ded_nom)
        # The standard deduction applies against taxable income generally:
        # any amount unused by ordinary income also shelters LTCG.
        unused_std_ded_nom = max(0.0, std_ded_nom - ordinary_before_ded_nom)
        taxable_ltcg_nom = max(0.0, ltcg_nom - unused_std_ded_nom)
        fed = (ordinary_tax_real(taxable_ord_nom / cpi, mfj)
               + ltcg_tax_real(taxable_ltcg_nom / cpi,
                               taxable_ord_nom / cpi, mfj)) * cpi
        penalty = (EARLY_WD_RATE * w_pre) if age < EARLY_WD_AGE else 0.0
        state = p.state_rate * (ordinary_nom + ltcg_nom)
        tax_total = fed + state + penalty
        if abs(tax_total - tax_guess) < 1.0:
            tax_guess = tax_total
            break
        tax_guess = tax_total

    gross = w_tax + w_pre + w_hsa + w_roth
    available = gross + ss_gross_nominal - tax_guess
    delivered = min(need_after_tax_nominal, max(0.0, available))
    deposit_back = max(0.0, available - need_after_tax_nominal)
    shortfall = max(0.0, need_after_tax_nominal - available)

    out = accounts.copy()
    out.taxable = bal_tax - w_tax + deposit_back     # RMD/SS excess reinvested
    out.pretax_401k = bal_pre - w_pre
    out.hsa = bal_hsa - w_hsa
    out.roth_ira = accounts.roth_ira - w_roth

    magi_agi = ordinary_nom + ltcg_nom + ss_taxable
    flow_err = abs((gross + ss_gross_nominal)
                   - (delivered + tax_guess + deposit_back)) if shortfall <= 1.0 else 0.0
    return dict(accounts=out, tax_total=tax_guess, penalty=penalty,
                ss_taxable=ss_taxable, magi_agi_nominal=magi_agi,
                magi_aca_nominal=ordinary_nom + ltcg_nom + ss_gross_nominal,
                delivered=delivered, deposit_back=deposit_back,
                gross_wd=gross, flow_err=flow_err, shortfall=shortfall)
