"""FIRE v9.8+ · E4 returns model 2.0 — opt-in alternative return generators.

Two models, both replacing the v7 "one regime per lifetime, iid annual draws"
sampler ONLY when explicitly enabled (default off = bit-identical, zero RNG
consumed by this module):

  * markov — each year retains the current regime with probability p; otherwise
    it redraws from the FULL configured mixture (including the current regime).
    The kernel P = p I + (1-p) 1 w^T preserves the configured weights w exactly
    as its stationary distribution. Regime year-dependence (e.g. highCAPE's low
    first decade) restarts on every redraw, even if it selects the same regime.
    Annual (equity, inflation)
    draws reuse the exact v7 joint sampler on the current regime's (mu,
    sigma). Optional AR(1) inflation: phi > 0 makes inflation persistent
    (inf_t = mu + phi·(inf_{t-1} − mu) + sigma·sqrt(1−phi²)·z_t) while
    keeping the marginal variance; the contemporaneous equity-inflation
    correlation is scaled by sqrt(1−phi²) as a side effect (disclosed).
    Bonds keep the v9.3 term-premium sampler.

  * blocks — historical circular block bootstrap: consecutive blocks of
    (S&P 500 total return, 10y Treasury total return, CPI inflation) drawn
    from the embedded 1928–2024 annual table, preserving within-block
    sequence structure and cross-asset correlation ("your plan meets the
    real 1929/1973/2000/2008"). block_years sets the block length; the
    stochastic_inflation and inflation_ar1 knobs are IGNORED here —
    inflation IS the historical CPI series.

DATA PROVENANCE (blocks table): S&P 500 (incl. dividends) and 10-year
Treasury total returns from the Damodaran/NYU-Stern annual series; CPI
annual-average inflation from BLS CPI-U (both fetched 2026-07, values
rounded to 1bp). Treat as the usual editable, illustrative empirical
default — pinned by tests, verify vintage before real decisions.

ENGINE CONTRACT: sample_lifetime_x mirrors sample_lifetime_v7's output shape
(+ bond returns) so fire_v9_8_model can swap samplers behind one guard. It
must be called INSTEAD of (never alongside) the v7 sampler — RNG stream
discipline is the whole default-off contract.
"""
from dataclasses import dataclass
from typing import Optional

import numpy as np

from fire_v6_model import Regime, REGIMES
from fire_v7_model import V7Config, sample_joint_return_inflation
from fire_v9_3_model import BondParams, sample_bond_returns


@dataclass
class ReturnsXParams:
    """Opt-in returns 2.0. enabled=False => the module does not exist."""
    enabled: bool = False
    model: str = "markov"          # "markov" | "blocks"
    persistence: float = 0.85      # markov: P(retain); otherwise redraw from w
    block_years: int = 5           # blocks: circular block length (1..97)
    inflation_ar1: float = 0.0     # markov: AR(1) phi on inflation [0..0.95]


# ============================================================
# Embedded historical annual table (see DATA PROVENANCE above)
# ============================================================
HIST_START_YEAR = 1928
# 97 years, 1928-2024
HIST_EQUITY = (
    +0.4381, -0.0830, -0.2512, -0.4384, -0.0864, +0.4998,
    -0.0119, +0.4674, +0.3194, -0.3534, +0.2928, -0.0110,
    -0.1067, -0.1277, +0.1917, +0.2506, +0.1903, +0.3582,
    -0.0843, +0.0520, +0.0570, +0.1830, +0.3081, +0.2368,
    +0.1815, -0.0121, +0.5256, +0.3260, +0.0744, -0.1046,
    +0.4372, +0.1206, +0.0034, +0.2664, -0.0881, +0.2261,
    +0.1642, +0.1240, -0.0997, +0.2380, +0.1081, -0.0824,
    +0.0356, +0.1422, +0.1876, -0.1431, -0.2590, +0.3700,
    +0.2383, -0.0698, +0.0651, +0.1852, +0.3174, -0.0470,
    +0.2042, +0.2234, +0.0615, +0.3124, +0.1849, +0.0581,
    +0.1654, +0.3148, -0.0306, +0.3023, +0.0749, +0.0997,
    +0.0133, +0.3720, +0.2268, +0.3310, +0.2834, +0.2089,
    -0.0903, -0.1185, -0.2197, +0.2836, +0.1074, +0.0483,
    +0.1561, +0.0548, -0.3655, +0.2594, +0.1482, +0.0210,
    +0.1589, +0.3215, +0.1352, +0.0138, +0.1177, +0.2161,
    -0.0423, +0.3121, +0.1802, +0.2847, -0.1804, +0.2606,
    +0.2488,
)
HIST_BOND = (
    +0.0084, +0.0420, +0.0454, -0.0256, +0.0879, +0.0186,
    +0.0796, +0.0447, +0.0502, +0.0138, +0.0421, +0.0441,
    +0.0540, -0.0202, +0.0229, +0.0249, +0.0258, +0.0380,
    +0.0313, +0.0092, +0.0195, +0.0466, +0.0043, -0.0030,
    +0.0227, +0.0414, +0.0329, -0.0134, -0.0226, +0.0680,
    -0.0210, -0.0265, +0.1164, +0.0206, +0.0569, +0.0168,
    +0.0373, +0.0072, +0.0291, -0.0158, +0.0327, -0.0501,
    +0.1675, +0.0979, +0.0282, +0.0366, +0.0199, +0.0361,
    +0.1598, +0.0129, -0.0078, +0.0067, -0.0299, +0.0820,
    +0.3281, +0.0320, +0.1373, +0.2571, +0.2428, -0.0496,
    +0.0822, +0.1769, +0.0624, +0.1500, +0.0936, +0.1421,
    -0.0804, +0.2348, +0.0143, +0.0994, +0.1492, -0.0825,
    +0.1666, +0.0557, +0.1512, +0.0038, +0.0449, +0.0287,
    +0.0196, +0.1021, +0.2010, -0.1112, +0.0846, +0.1604,
    +0.0297, -0.0910, +0.1075, +0.0128, +0.0069, +0.0280,
    -0.0002, +0.0964, +0.1133, -0.0442, -0.1783, +0.0388,
    -0.0164,
)
HIST_CPI = (
    -0.0170, +0.0000, -0.0230, -0.0900, -0.0990, -0.0510,
    +0.0310, +0.0220, +0.0150, +0.0360, -0.0210, -0.0140,
    +0.0070, +0.0500, +0.1090, +0.0610, +0.0170, +0.0230,
    +0.0830, +0.1440, +0.0810, -0.0120, +0.0130, +0.0790,
    +0.0190, +0.0080, +0.0070, -0.0040, +0.0150, +0.0330,
    +0.0280, +0.0070, +0.0170, +0.0100, +0.0100, +0.0130,
    +0.0130, +0.0160, +0.0290, +0.0310, +0.0420, +0.0550,
    +0.0570, +0.0440, +0.0320, +0.0620, +0.1100, +0.0910,
    +0.0580, +0.0650, +0.0760, +0.1130, +0.1350, +0.1030,
    +0.0620, +0.0320, +0.0430, +0.0360, +0.0190, +0.0360,
    +0.0410, +0.0480, +0.0540, +0.0420, +0.0300, +0.0300,
    +0.0260, +0.0280, +0.0300, +0.0230, +0.0160, +0.0220,
    +0.0340, +0.0280, +0.0160, +0.0230, +0.0270, +0.0340,
    +0.0320, +0.0280, +0.0380, -0.0040, +0.0160, +0.0320,
    +0.0210, +0.0150, +0.0160, +0.0010, +0.0130, +0.0210,
    +0.0240, +0.0180, +0.0120, +0.0470, +0.0800, +0.0410,
    +0.0290,
)

assert len(HIST_EQUITY) == len(HIST_BOND) == len(HIST_CPI) == 97, \
    "historical table must cover 1928-2024 inclusive"


def _sample_markov(total_years: int, rng: np.random.Generator,
                   config: V7Config, xp: ReturnsXParams,
                   regimes: Optional[list] = None):
    regimes = list(regimes or REGIMES)
    p_retain = min(max(float(xp.persistence), 0.0), 1.0)
    phi = min(max(float(xp.inflation_ar1), 0.0), 0.95)
    mu_inf, sig_inf = config.inflation_mu, config.inflation_sigma
    ar_damp = float(np.sqrt(max(0.0, 1.0 - phi * phi)))

    # Normalize once for both the stationary initial draw and every full-mixture
    # redraw. For the default regimes this is exactly 40% / 20% / 40%.
    weights = np.asarray([max(0.0, float(reg.prob)) for reg in regimes])
    if weights.sum() <= 0.0:
        weights = np.ones(len(regimes), dtype=float)
    weights /= weights.sum()

    r0 = rng.random()
    cum, cur = 0.0, len(regimes) - 1
    for i, weight in enumerate(weights):
        cum += weight
        if r0 < cum:
            cur = i
            break

    returns, inflations, names = [], [], []
    spell_year = 1
    prev_inf = mu_inf                      # AR(1) starts at the stationary mean
    for _ in range(total_years):
        names.append(regimes[cur].name)
        mu, sigma = regimes[cur].params(spell_year)
        r, inf_raw = sample_joint_return_inflation(
            mu, sigma, mu_inf, sig_inf, config.inflation_equity_corr,
            config.return_df, config.return_distribution, rng,
        )
        if not config.stochastic_inflation:
            inf = mu_inf
        elif phi > 0.0:
            z2 = (inf_raw - mu_inf) / max(sig_inf, 1e-12)
            inf = mu_inf + phi * (prev_inf - mu_inf) + sig_inf * ar_damp * z2
        else:
            inf = inf_raw
        inf = max(config.inflation_floor, min(config.inflation_ceiling, inf))
        prev_inf = inf
        returns.append(r)
        inflations.append(inf)

        # End-of-year kernel: retain with p, otherwise redraw from the full w.
        # The same uniform selects both the branch and the conditional redraw,
        # preserving one transition draw per year across persistence values.
        u = rng.random()
        if u >= p_retain:
            pick = (u - p_retain) / max(1.0 - p_retain, 1e-12)
            acc = 0.0
            nxt = len(regimes) - 1
            for i, weight in enumerate(weights):
                acc += weight
                if pick < acc:
                    nxt = i
                    break
            cur, spell_year = nxt, 1
        else:
            spell_year += 1

    return returns, inflations, names


def _sample_blocks(total_years: int, rng: np.random.Generator,
                   xp: ReturnsXParams):
    n = len(HIST_EQUITY)
    L = min(max(int(xp.block_years), 1), n)
    eq, bd, inf = [], [], []
    while len(eq) < total_years:
        start = int(rng.integers(0, n))
        for k in range(L):
            i = (start + k) % n            # circular: no end-of-sample bias
            eq.append(HIST_EQUITY[i])
            bd.append(HIST_BOND[i])
            inf.append(HIST_CPI[i])
    return eq[:total_years], bd[:total_years], inf[:total_years]


def sample_lifetime_x(total_years: int, rng: np.random.Generator,
                      config: V7Config, bond_params: BondParams,
                      xp: ReturnsXParams, regimes: Optional[list] = None):
    """Drop-in replacement for (sample_lifetime_v7 + sample_bond_returns).

    Returns (regime, equity_returns, bond_returns, inflations); regime is a
    label-only Regime whose .name records the generator ("markov"/"blocks").
    """
    if xp.model == "blocks":
        eq, bd, inf = _sample_blocks(total_years, rng, xp)
        name = "blocks"
    elif xp.model == "markov":
        eq, inf, _names = _sample_markov(total_years, rng, config, xp, regimes)
        bd = sample_bond_returns(eq, bond_params, rng)
        name = "markov"
    else:
        raise ValueError(f"unknown returns model: {xp.model!r}")
    return Regime(name=name, prob=1.0, params=lambda y: (0.0, 0.0),
                  rationale="returns 2.0 generator label"), eq, bd, inf
