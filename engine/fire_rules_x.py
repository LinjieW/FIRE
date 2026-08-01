"""FIRE v9.8+ · E3 strategy library — additional WithdrawalRule subclasses.

Adds three withdrawal strategies alongside the v9.8 GK standard:
  * VPWRule        — Variable Percentage Withdrawal (age-based % of portfolio)
  * FloorUpsideRule — CPI-indexed real floor + upside participation
  * ABWRule        — Amortization-Based Withdrawal with a bequest knob

ENGINE COMPATIBILITY CONTRACT (do not break):
  * Rules are initialized ONLY through fire_v9_8_model._init_rule(rule,
    portfolio, w, swr, cpi_at_init). The relocation branch RE-SEEDS the rule
    mid-flight (re-initialize on the China-basis budget, then restores the
    guardrail_triggers count). Therefore state must stay a plain dict laid
    out like WithdrawalRule.initialize()'s, must tolerate
    state.get('guardrail_triggers', 0), and must NOT assume a call with
    year_in_retirement == 0 will ever arrive (after a re-seed the first
    compute call comes in at year >= 1).
  * compute_target_withdrawal is pure: returns (target_nominal, new_state)
    and never mutates the incoming state.
  * Rules never consume RNG — determinism of the shared stream is part of
    the default-off bit-identical contract.

Percentage-of-portfolio targets are capped at rate_cap so the engine's tax
gross-up (net need / (1 - withdrawal tax rate)) always remains fundable from
the portfolio — this is what makes "cannot deplete by construction" hold
inside the simulator, not just in the idealized math.
"""
from dataclasses import dataclass
from typing import Optional

from fire_v9_1_model import WithdrawalRule


def _annuity_rate(r: float, n: int) -> float:
    """Payment rate amortizing 1 unit over n years at return r."""
    n = max(int(n), 1)
    if r <= 1e-9:
        return 1.0 / n
    return r / (1.0 - (1.0 + r) ** (-n))


@dataclass
class VPWRule(WithdrawalRule):
    """Variable Percentage Withdrawal.

    Each year withdraw an age-based percentage of the CURRENT portfolio:
    the annuity payout factor on an assumed real return, amortized to
    depletion_age (Bogleheads-style schedule, computed not tabulated).
    Consumption floats with the market — deep cuts in crashes, raises in
    booms — but the portfolio cannot be depleted by withdrawals.
    """
    name: str = "VPW"
    expected_real_return: float = 0.034   # ~50/50 portfolio long-run real
    depletion_age: int = 100
    rate_cap: float = 0.20

    def compute_target_withdrawal(self, year_in_retirement, age,
                                  portfolio_nominal, inflation_this_year,
                                  cpi_cumulative, state):
        rate = min(_annuity_rate(self.expected_real_return,
                                 self.depletion_age - int(age)),
                   self.rate_cap)
        target = rate * max(portfolio_nominal, 0.0)
        new_state = dict(state)
        new_state['prev_w_nominal'] = target
        new_state['prev_portfolio_nominal'] = portfolio_nominal
        return target, new_state


@dataclass
class FloorUpsideRule(WithdrawalRule):
    """Floor-and-upside: guaranteed real floor + market participation.

    target = max(floor, upside_pct × current portfolio), where the floor is
    floor_ratio × the initial retirement budget, CPI-indexed on the same
    real basis as GK's F-4 freeze (nominal_at_init / cpi_at_init). The floor
    models a TIPS-ladder / annuity spending guarantee; the upside leg spends
    a fixed share of the current portfolio when markets cooperate.

    upside_pct=None (default) uses the initial SWR from state, so the year-0
    target equals the initial budget exactly and the floor only binds after
    drawdowns. After a relocation re-seed the floor re-anchors to the
    China-basis budget (consistent with how GK re-anchors).
    """
    name: str = "Floor + Upside"
    floor_ratio: float = 0.85
    upside_pct: Optional[float] = None

    def compute_target_withdrawal(self, year_in_retirement, age,
                                  portfolio_nominal, inflation_this_year,
                                  cpi_cumulative, state):
        cpi_at_init = max(state.get('cpi_at_init', 1.0), 1e-12)
        floor_nominal = (self.floor_ratio * state['initial_w_nominal']
                         * (cpi_cumulative / cpi_at_init))
        up_pct = (self.upside_pct if self.upside_pct is not None
                  else state['initial_swr'])
        target = max(floor_nominal, up_pct * max(portfolio_nominal, 0.0))
        new_state = dict(state)
        new_state['prev_w_nominal'] = target
        new_state['prev_portfolio_nominal'] = portfolio_nominal
        return target, new_state


@dataclass
class ABWRule(WithdrawalRule):
    """Amortization-Based Withdrawal.

    Every year re-amortize the CURRENT portfolio (in real terms) over the
    remaining horizon to horizon_age at an assumed real return, preserving
    bequest_frac × the initial portfolio (real) as a terminal target.
    bequest_frac=0 spends down to a near-zero estate: higher lifetime
    consumption, lower terminal wealth. Equivalent to VPW when
    bequest_frac=0 and the parameters match — the bequest knob and the
    (deliberately higher) assumed return are what distinguish it here.
    """
    name: str = "ABW"
    expected_real_return: float = 0.04
    horizon_age: int = 100
    bequest_frac: float = 0.0
    rate_cap: float = 0.20

    def compute_target_withdrawal(self, year_in_retirement, age,
                                  portfolio_nominal, inflation_this_year,
                                  cpi_cumulative, state):
        n = max(int(self.horizon_age) - int(age), 1)
        r = self.expected_real_return
        cpi = max(cpi_cumulative, 1e-12)
        cpi_at_init = max(state.get('cpi_at_init', 1.0), 1e-12)
        p_real = max(portfolio_nominal, 0.0) / cpi
        bequest_real = (self.bequest_frac
                        * state['initial_portfolio_nominal'] / cpi_at_init)
        amortizable = max(p_real - bequest_real / (1.0 + r) ** n, 0.0)
        pmt_real = min(amortizable * _annuity_rate(r, n),
                       self.rate_cap * p_real)
        target = pmt_real * cpi
        new_state = dict(state)
        new_state['prev_w_nominal'] = target
        new_state['prev_portfolio_nominal'] = portfolio_nominal
        return target, new_state


# type key -> (class, zh label, en label); used by the adapter's rule.type
# mapping and the /api/strategies compare endpoint. "gk" is handled by the
# adapter itself (GuytonKlingerRuleV98 — the default, absent => bit-identical).
STRATEGY_LIBRARY = {
    "vpw": (VPWRule, "VPW 变比例", "VPW"),
    "floor_upside": (FloorUpsideRule, "地板+上行", "Floor + Upside"),
    "abw": (ABWRule, "ABW 摊销", "ABW amortization"),
}
