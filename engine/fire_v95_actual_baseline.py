"""
FIRE Model v9.5.2 — Actual Portfolio + Match-Excludes-Bonus · 2026-05-16
=========================================================================

Patch to v9.5 baseline reflecting actual the brokerage portfolio positions
from Portfolio_Positions_May-16-2026.csv, PLUS the locked-decision-#12
correction that the 401(k) match excludes the annual bonus.

This file is the helper layer for BOTH the v9.5.2 and v9.6 official
baselines. It exports:
  - INITIAL_STACK_ACTUAL : the May-16-2026 account stack
  - match_excludes_bonus() : context manager forcing match = 6%×(base+OT)

REPRODUCIBILITY FIX (2026-05-29)
------------------------------------------------------------
Earlier, match_excludes_bonus() lived only as a transient monkey-patch
that was never committed to the project; the on-disk copy of this file
was the pre-correction v9.5.1 version, so the v9.6 runners raised
ImportError and the official baseline could not be reproduced from the
project files. Fix: the match-excludes-bonus logic is now BAKED IN as
`V8ContributionParams.match_excludes_bonus` (default True, = decision #12),
and match_excludes_bonus() below is a thin, robust context manager over the
module-level hook `fire_v8_model._FORCE_MATCH_EXCLUDES_BONUS`. No fragile
import-binding monkey-patch remains. Default behavior (no context manager)
already reproduces the official baseline.

CHANGES vs v9.5:
------------------------------------------------------------
1. INITIAL_STACK (account balances)
   - pretax_401k: $89K → $100,128
     ├ PrevEmployer 401(k):    $79,394 (FXAIX)
     └ CurrEmployer 401k:$20,734 (FXAIX) — also current pretax 401(k)
   - roth_ira:    $46K → $46,160
   - hsa:         $16K → $16,110
   - taxable:     $59K → $52,209  ← after $10K emergency reserve carve-out
   Total FIRE portfolio: $210K → $214,608  (+$4.6K vs v9.5)
   Total net worth:       $224,622 = $214,608 (FIRE) + $10K (emergency, separate)

2. MATCH EXCLUDES BONUS (locked decision #12)
   - CurrEmployer 401(k) match = 6% × (base + OT) only; bonus NOT matched.
   - v8 default historically matched on all gross; this is now corrected
     via V8ContributionParams.match_excludes_bonus (default True).

3. CASH HANDLING
   - Observed $17.2K in MM funds (SPAXX/FDRXX) per CSV
   - User intent: DCA part into 75 VOO / 25 QQQM, hold part as emergency reserve
   - Default assumption: $10K emergency (≈3 months expenses) carved out of
     FIRE portfolio. Remaining $7.2K assumed deployed within months at
     75/25 mix → for the 50-yr horizon, treated as already-deployed equity.
   - Emergency reserve NOT modeled as compounding inside FIRE simulation,
     so it acts as a conservative tilt (real $10K cushion exists outside).

4. ASSET ALLOCATION DRIFT (informational; regime params NOT changed)
   - v9.5 effective mix (memory): S&P 85.6% / QQQM 14.4% of equity
   - Actual observed mix:         S&P 91.1% / QQQM  8.9%
   - Post-deployment mix:         S&P 90.5% / QQQM  9.5%
   - Blended μ (arith):  10.14% → 10.10% (−0.04pp, immaterial)
   - Regime tuple (mu, sigma) left untouched. Re-tune if QQQM weight
     drifts outside [5%, 15%].

NOT CHANGED (everything else inherited from v9.5):
- Contribution stream, salary growth, expenses ($40,440), inflation
- Withdrawal rule (GK standard, SWR 3.33%)
- Roth ladder $48K/yr, SS@67, OBBBA take-and-ignore, eldercare buffer
- Shanghai property logic, mortality, FX dynamics, regime mixture
- Promotion model, tax parameters

Usage:
    from fire_v95_actual_baseline import INITIAL_STACK_ACTUAL, match_excludes_bonus
    # Default behavior already excludes bonus from match:
    res = run_lifecycle_mc_v96(initial=INITIAL_STACK_ACTUAL, ...)
    # The context manager is optional belt-and-suspenders / explicit override:
    with match_excludes_bonus():
        res = run_lifecycle_mc_v96(initial=INITIAL_STACK_ACTUAL, ...)
"""
from __future__ import annotations
import numpy as np
import time
from contextlib import contextmanager

import fire_v8_model
from fire_v6_model import AccountStack
from fire_v9_5_model import run_lifecycle_mc_v95


# ─────────────────────────────────────────────────────────────
# CONTEXT MANAGER · force match = 6% × (base + OT), bonus excluded
# ─────────────────────────────────────────────────────────────
@contextmanager
def match_excludes_bonus():
    """Force the 401(k) match base to EXCLUDE bonus within the block.

    Backward-compatible with the v9.5.2 / v9.6 runners. Implemented via the
    module-level hook fire_v8_model._FORCE_MATCH_EXCLUDES_BONUS, which
    compute_contributions_for_year() reads as a global in its own module at
    call time — so the override propagates correctly to all callers and
    avoids the `from X import Y` binding pitfall (ARCHIVE.md rule 9).

    NOTE: As of the 2026-05-29 fix, V8ContributionParams.match_excludes_bonus
    already defaults to True, so this context manager is redundant for the
    default baseline — but it remains a valid explicit override (e.g. to force
    exclusion even if a caller passed a params object with the flag set False).
    """
    prev = fire_v8_model._FORCE_MATCH_EXCLUDES_BONUS
    fire_v8_model._FORCE_MATCH_EXCLUDES_BONUS = True
    try:
        yield
    finally:
        fire_v8_model._FORCE_MATCH_EXCLUDES_BONUS = prev


# ─────────────────────────────────────────────────────────────
# UPDATED INITIAL STACK (May 16 2026 actuals, $10K emergency carved out)
# ─────────────────────────────────────────────────────────────
INITIAL_STACK_ACTUAL = AccountStack(
    pretax_401k = 100_128,   # PrevEmployer $79,394 + CurrEmployer $20,734
    roth_ira    =  46_160,   # actual
    hsa         =  16_110,   # actual
    taxable     =  52_209,   # actual $62,209 − $10,000 emergency reserve
)
assert abs(INITIAL_STACK_ACTUAL.total - 214_607) < 5

EMERGENCY_RESERVE_USD = 10_000  # held outside FIRE portfolio

# Equity allocation reference (post-deployment) — informational
EQUITY_MIX_ACTUAL = {'sp500_pct': 0.905, 'qqqm_pct': 0.095}


# ─────────────────────────────────────────────────────────────
# SANITY CHECK — compare v9.5 baseline ($210K stack) vs ACTUAL ($214.6K)
# ─────────────────────────────────────────────────────────────
def _milestones_from_path(accum_path, targets=(1_000_000, 3_000_000)):
    """Return ages at which `total` first crosses each target."""
    out = {t: None for t in targets}
    for step in accum_path:
        for t in targets:
            if out[t] is None and step['total'] >= t:
                out[t] = step['age']
    return out


def _pctiles(xs, ps=(10, 25, 50, 75, 90)):
    xs = [x for x in xs if x is not None]
    if not xs:
        return None
    return {p: float(np.percentile(xs, p)) for p in ps}


def _summarize(results, label):
    ages_1m, ages_3m, ages_fire = [], [], []
    successes = 0
    n_reached_fire = 0
    for r in results:
        if r.get('accum_path') is None:
            continue
        ms = _milestones_from_path(r['accum_path'])
        ages_1m.append(ms[1_000_000])
        ages_3m.append(ms[3_000_000])
        if r.get('fire_age') is not None:
            ages_fire.append(r['fire_age'])
            n_reached_fire += 1
        if r.get('lifetime_success'):
            successes += 1

    n = len(results)
    n_1m = sum(1 for a in ages_1m if a is not None)
    n_3m = sum(1 for a in ages_3m if a is not None)

    p_1m_by_33 = sum(1 for a in ages_1m if a is not None and a <= 33) / n
    p_1m_by_35 = sum(1 for a in ages_1m if a is not None and a <= 35) / n
    p_3m_by_40 = sum(1 for a in ages_3m if a is not None and a <= 40) / n
    p_3m_by_42 = sum(1 for a in ages_3m if a is not None and a <= 42) / n
    p_fire_by_40 = sum(1 for a in ages_fire if a <= 40) / n
    p_fire_by_42 = sum(1 for a in ages_fire if a <= 42) / n

    pc_1m = _pctiles(ages_1m)
    pc_3m = _pctiles(ages_3m)
    pc_fire = _pctiles(ages_fire)
    life_success = successes / n

    return {
        'label': label, 'n': n, 'n_1m': n_1m, 'n_3m': n_3m,
        'n_fire': n_reached_fire,
        'pc_1m': pc_1m, 'pc_3m': pc_3m, 'pc_fire': pc_fire,
        'p_1m_by_33': p_1m_by_33, 'p_1m_by_35': p_1m_by_35,
        'p_3m_by_40': p_3m_by_40, 'p_3m_by_42': p_3m_by_42,
        'p_fire_by_40': p_fire_by_40, 'p_fire_by_42': p_fire_by_42,
        'lifetime_success': life_success,
    }


def _print_summary(s):
    print(f"\n─── {s['label']} (N={s['n']:,}) ───")
    if s['pc_1m']:
        print(f"$1M crossing age (n={s['n_1m']:,}):  "
              f"P10={s['pc_1m'][10]:.0f}  P25={s['pc_1m'][25]:.0f}  "
              f"P50={s['pc_1m'][50]:.0f}  P75={s['pc_1m'][75]:.0f}  "
              f"P90={s['pc_1m'][90]:.0f}")
    if s['pc_3m']:
        print(f"$3M crossing age (n={s['n_3m']:,}):  "
              f"P10={s['pc_3m'][10]:.0f}  P25={s['pc_3m'][25]:.0f}  "
              f"P50={s['pc_3m'][50]:.0f}  P75={s['pc_3m'][75]:.0f}  "
              f"P90={s['pc_3m'][90]:.0f}")
    if s['pc_fire']:
        print(f"FIRE age @ SWR 3.33% (n={s['n_fire']:,}): "
              f"P10={s['pc_fire'][10]:.0f}  P25={s['pc_fire'][25]:.0f}  "
              f"P50={s['pc_fire'][50]:.0f}  P75={s['pc_fire'][75]:.0f}  "
              f"P90={s['pc_fire'][90]:.0f}")
    print(f"P($1M by 33): {s['p_1m_by_33']*100:5.1f}%   "
          f"P($1M by 35): {s['p_1m_by_35']*100:5.1f}%")
    print(f"P($3M by 40): {s['p_3m_by_40']*100:5.1f}%   "
          f"P($3M by 42): {s['p_3m_by_42']*100:5.1f}%")
    print(f"P(FIRE by 40): {s['p_fire_by_40']*100:5.1f}%   "
          f"P(FIRE by 42): {s['p_fire_by_42']*100:5.1f}%")
    print(f"Lifetime success: {s['lifetime_success']*100:.1f}%")


if __name__ == "__main__":
    N_PATHS = 60_000
    SEED = 42

    # v9.5 BASELINE ($210K stack, INITIAL_STACK default)
    print(f"\n[1/2] v9.5 baseline ($210K) — running {N_PATHS:,} paths...")
    t0 = time.time()
    res_base = run_lifecycle_mc_v95(n_paths=N_PATHS, seed=SEED)
    print(f"      elapsed: {time.time()-t0:.1f}s")
    summ_base = _summarize(res_base, "v9.5 baseline ($210K)")

    # ACTUAL ($214.6K stack)
    print(f"\n[2/2] v9.5.1 ACTUAL ($214.6K) — running {N_PATHS:,} paths...")
    t0 = time.time()
    res_act = run_lifecycle_mc_v95(
        n_paths=N_PATHS, seed=SEED, initial=INITIAL_STACK_ACTUAL,
    )
    print(f"      elapsed: {time.time()-t0:.1f}s")
    summ_act = _summarize(res_act, "v9.5.1 ACTUAL ($214.6K)")

    print("\n" + "="*72)
    print("MILESTONE COMPARISON: v9.5 baseline vs v9.5.1 ACTUAL")
    print("="*72)
    _print_summary(summ_base)
    _print_summary(summ_act)

    print("\n" + "="*72)
    print("MARGINAL IMPACT OF +$4.6K HEAD START")
    print("="*72)
    if summ_base['pc_1m'] and summ_act['pc_1m']:
        d50_1m = summ_act['pc_1m'][50] - summ_base['pc_1m'][50]
        d50_3m = summ_act['pc_3m'][50] - summ_base['pc_3m'][50]
        d50_fire = summ_act['pc_fire'][50] - summ_base['pc_fire'][50]
        print(f"Δ P50 age to $1M:  {d50_1m:+.2f} yrs")
        print(f"Δ P50 age to $3M:  {d50_3m:+.2f} yrs")
        print(f"Δ P50 FIRE age:    {d50_fire:+.2f} yrs")
    print(f"Δ P($1M by 33):    {(summ_act['p_1m_by_33']-summ_base['p_1m_by_33'])*100:+.1f}pp")
    print(f"Δ P($1M by 35):    {(summ_act['p_1m_by_35']-summ_base['p_1m_by_35'])*100:+.1f}pp")
    print(f"Δ P($3M by 40):    {(summ_act['p_3m_by_40']-summ_base['p_3m_by_40'])*100:+.1f}pp")
    print(f"Δ P(FIRE by 40):   {(summ_act['p_fire_by_40']-summ_base['p_fire_by_40'])*100:+.1f}pp")
    print(f"Δ lifetime success: {(summ_act['lifetime_success']-summ_base['lifetime_success'])*100:+.2f}pp")
