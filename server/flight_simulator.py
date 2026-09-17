"""Roadmap 11 Phase 4: deterministic retirement-behaviour rehearsal.

The exercise is deliberately not a forecast.  It replays stylised market
paths through the shipped retirement engine, pausing once per year so the
caller can either follow the configured withdrawal/allocation rules, keep the
real spending base despite a rule cut, or sell equities for the following
year.  No choice changes the saved plan.
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Sequence

import numpy as np

import engine_adapter as ENG


CHOICES = ("follow_rule", "hold_spending", "panic_sell")
HORIZON = 10
DISCLOSURE = "This is one sampled path for rehearsal, not a prediction."


@dataclass(frozen=True)
class Scenario:
    key: str
    equity: tuple[float, ...]
    bonds: tuple[float, ...]
    inflation: tuple[float, ...]


SCENARIOS = {
    "bear_start": Scenario(
        "bear_start",
        (-0.30, -0.13, 0.07, 0.18, 0.12, 0.08, 0.06, 0.07, 0.05, 0.06),
        (0.04, 0.03, 0.04, 0.02, 0.03, 0.03, 0.03, 0.03, 0.03, 0.03),
        (0.035, 0.040, 0.035, 0.030, 0.028, 0.027, 0.026, 0.026, 0.025, 0.025),
    ),
    "inflation_decade": Scenario(
        "inflation_decade",
        (0.02, -0.04, 0.08, 0.05, 0.04, 0.07, 0.03, 0.06, 0.05, 0.06),
        (-0.05, -0.03, 0.01, 0.02, 0.02, 0.03, 0.02, 0.03, 0.03, 0.03),
        (0.080, 0.075, 0.070, 0.065, 0.060, 0.055, 0.050, 0.045, 0.040, 0.035),
    ),
    "long_bull": Scenario(
        "long_bull",
        (0.17, 0.14, 0.12, 0.16, 0.11, 0.13, 0.10, 0.12, 0.09, 0.10),
        (0.03, 0.03, 0.035, 0.03, 0.035, 0.03, 0.035, 0.03, 0.035, 0.03),
        (0.025, 0.025, 0.027, 0.026, 0.025, 0.024, 0.025, 0.024, 0.025, 0.024),
    ),
}


class _Controller:
    def __init__(self, choices: Sequence[str]):
        self.choices = tuple(choices)
        self.records: list[dict] = []
        self.allocations: list[dict] = []
        self.panic_for_age: int | None = None


class _ChoiceRule:
    """Wrap the configured strategy; only the user's annual action is new."""

    def __init__(self, native, controller: _Controller):
        self.native = native
        self.controller = controller
        self.name = getattr(native, "name", native.__class__.__name__)
        self._hold_target = None

    def initialize(self, *args, **kwargs):
        state = self.native.initialize(*args, **kwargs)
        self._hold_target = float(state["initial_w_nominal"])
        return state

    def compute_target_withdrawal(self, year_in_retirement, age,
                                  portfolio_nominal, inflation_this_year,
                                  cpi_cumulative, state):
        native_target, next_state = self.native.compute_target_withdrawal(
            year_in_retirement, age, portfolio_nominal, inflation_this_year,
            cpi_cumulative, state)
        if year_in_retirement > 0:
            self._hold_target *= 1.0 + float(inflation_this_year)
        choice = (self.controller.choices[year_in_retirement]
                  if year_in_retirement < len(self.controller.choices)
                  else "follow_rule")
        applied = float(native_target)
        if choice == "hold_spending":
            applied = max(applied, float(self._hold_target))
        elif choice == "panic_sell":
            # The choice is made after this year's return is known.  Selling
            # therefore changes next year's allocation, not the year already
            # experienced.  A later follow_rule choice restores the glide.
            self.controller.panic_for_age = int(age) + 1
        self.controller.records.append({
            "age": int(age),
            "choice": choice,
            "native_target_nominal": float(native_target),
            "applied_target_nominal": applied,
            "rule_cut_overridden": applied > float(native_target) + 0.005,
        })
        return applied, next_state


class _ChoiceGlide:
    def __init__(self, native, controller: _Controller):
        self.native = native
        self.controller = controller
        self.name = getattr(native, "name", native.__class__.__name__)

    def equity_pct(self, age):
        native = float(self.native.equity_pct(age))
        applied = 0.0 if self.controller.panic_for_age == int(age) else native
        self.controller.allocations.append({
            "age": int(age), "native_equity": native,
            "applied_equity": applied,
        })
        return applied


def _paths(scenario: Scenario, seed: int, sample_index: int = 0):
    """Return one stable stylised path; later samples form the end comparison."""
    if sample_index == 0:
        return scenario.equity, scenario.bonds, scenario.inflation
    scenario_id = list(SCENARIOS).index(scenario.key) + 1
    rng = np.random.default_rng([int(seed), scenario_id, int(sample_index)])
    eq = np.clip(np.asarray(scenario.equity) + rng.normal(0, 0.045, HORIZON),
                 -0.55, 0.45)
    bd = np.clip(np.asarray(scenario.bonds) + rng.normal(0, 0.012, HORIZON),
                 -0.20, 0.20)
    inf = np.clip(np.asarray(scenario.inflation) + rng.normal(0, 0.006, HORIZON),
                  -0.01, 0.15)
    return tuple(eq), tuple(bd), tuple(inf)


def _starting_stack(cfg: dict, kw: dict):
    af = cfg.get("already_fired") or {}
    if af.get("enabled"):
        return kw["initial"].copy(), int(kw["state"].start_age)
    expenses = float(kw["state"].expenses_y0)
    swr = max(float(kw.get("fire_swr") or kw["state"].swr_pref), 0.005)
    total = expenses / swr
    source = kw["initial"]
    balances = source.balances()
    source_total = max(float(source.total), 0.0)
    if source_total <= 0:
        return ENG.AccountStack(taxable=total), int(
            kw["state"].start_age + kw["state"].accum_years)
    scaled = {name: total * float(value) / source_total
              for name, value in balances.items()}
    return ENG.AccountStack(**scaled), int(
        kw["state"].start_age + kw["state"].accum_years)


def _run_path(cfg: dict, scenario: Scenario, choices: Sequence[str],
              seed: int, sample_index: int = 0) -> dict:
    kw = ENG.build_kwargs(cfg, False)
    starting, starting_age = _starting_stack(cfg, kw)
    controller = _Controller(choices)
    rule = _ChoiceRule(kw["rule"], controller)
    glide = _ChoiceGlide(kw["glide_path"], controller)
    eq, bd, inf = _paths(scenario, seed, sample_index)
    mortality = dataclasses.replace(kw["mortality"], enabled=False)
    with (ENG._ENGINE_LOCK, ENG.match_excludes_bonus(),
          ENG._household_ctx(cfg), ENG._tax_posture_ctx(cfg)):
        result = ENG.simulate_retirement_v98(
            starting_accounts=starting, starting_age=starting_age,
            fire_year_cpi_cumulative=1.0,
            equity_returns=eq, bond_returns=bd, inflations=inf,
            rule=rule, glide_path=glide,
            relocation=kw["relocation"], sh_property=kw["sh_property"],
            medical=kw["medical"], aca=kw["aca"], mortality=mortality,
            roth_ladder=kw["roth_ladder"], ss=kw["ss"], ftc=kw["ftc"],
            eldercare_events=[], inheritance_event=None,
            state=kw["state"], tax_us=kw["tax_us"], tax_cn=kw["tax_cn"],
            rng=np.random.default_rng([int(seed), int(sample_index), 41]),
            china_healthcare=kw["china_healthcare"], ss_nra=kw["ss_nra"],
            income_streams=kw["income_streams"], tax_true=kw["tax_true"],
            student_debt=kw["student_debt"],
        )
    cpi = float(np.prod(1.0 + np.asarray(inf[:result["years_in_retirement"]])))
    return {
        "result": result, "records": controller.records,
        "allocations": controller.allocations,
        "equity_returns": list(map(float, eq)),
        "bond_returns": list(map(float, bd)),
        "inflation": list(map(float, inf)),
        "terminal_real": float(result["terminal_balance"]) / max(cpi, 1e-9),
        "starting_real": float(starting.total),
        "starting_age": starting_age,
    }


def score(choices: Sequence[str], played: dict, committed: dict) -> dict:
    """Score behaviour only; terminal wealth never enters this function."""
    n = max(1, len(choices))
    adherence = sum(c == "follow_rule" for c in choices) / n
    result = played["result"]
    committed_min = max(float(committed["result"]["min_real_consumption"]), 1.0)
    floor_ratio = min(1.0, float(result["min_real_consumption"]) / committed_min)
    solvent = result.get("shortfall_age") is None
    robustness = (0.5 + 0.5 * floor_ratio) if solvent else 0.0
    # Eighty adherence points make the invariant structural: even an
    # outcome-perfect all-timing sequence is capped at 20, while the committed
    # sequence is 100.  Wealth, returns and hindsight never affect the score.
    total = round(100 * (0.8 * adherence + 0.2 * robustness))
    return {
        "adherence": round(100 * adherence),
        "robustness": round(100 * robustness),
        "total": total,
        "formula": "80% rule adherence + 20% spending-floor solvency; no wealth or timing input",
    }


def _distribution(cfg, scenario, choices, seed):
    values, committed = [], []
    for i in range(1, 22):
        values.append(_run_path(cfg, scenario, choices, seed, i)["terminal_real"])
        committed.append(_run_path(
            cfg, scenario, ("follow_rule",) * HORIZON, seed, i)["terminal_real"])
    def qs(xs):
        return {"p10": float(np.percentile(xs, 10)),
                "p50": float(np.percentile(xs, 50)),
                "p90": float(np.percentile(xs, 90))}
    return {"played_terminal_real": qs(values),
            "committed_terminal_real": qs(committed), "paths": len(values)}


def rehearse(cfg: dict, scenario_key: str, choices: Sequence[str], seed: int):
    if scenario_key not in SCENARIOS:
        raise ValueError(f"unknown flight scenario: {scenario_key}")
    if len(choices) > HORIZON or any(c not in CHOICES for c in choices):
        raise ValueError("flight choices must be follow_rule, hold_spending, or panic_sell")
    scenario = SCENARIOS[scenario_key]
    played = _run_path(cfg, scenario, choices, seed)
    committed_choices = ("follow_rule",) * HORIZON
    committed = _run_path(cfg, scenario, committed_choices, seed)
    visible_n = min(len(choices), len(played["records"]))
    years = []
    for i in range(visible_n):
        rec = dict(played["records"][i])
        rec.update({
            "equity_return": played["equity_returns"][i],
            "bond_return": played["bond_returns"][i],
            "inflation": played["inflation"][i],
            "equity_allocation": played["allocations"][i]["applied_equity"],
            "portfolio_end_nominal": float(played["result"]["portfolio_path"][i + 1]),
            "consumption_real": (float(played["result"]["real_consumption_path"][i])
                                 if i < len(played["result"]["real_consumption_path"])
                                 else 0.0),
        })
        years.append(rec)
    complete = len(choices) == HORIZON
    response = {
        "scenario": scenario_key, "seed": int(seed), "horizon": HORIZON,
        "choices": list(choices), "years": years, "complete": complete,
        "next_year": None if complete else {
            "number": len(choices) + 1,
            "age": played["starting_age"] + len(choices) + 1,
            "equity_return": played["equity_returns"][len(choices)],
            "bond_return": played["bond_returns"][len(choices)],
            "inflation": played["inflation"][len(choices)],
            "choices": list(CHOICES),
        },
        "disclosure": DISCLOSURE,
        "scoring_invariant": "Following the committed rule scores 100; timing cannot score higher.",
    }
    if complete:
        response["score"] = score(choices, played, committed)
        response["ending"] = {
            "played_terminal_real": played["terminal_real"],
            "committed_terminal_real": committed["terminal_real"],
            "played_shortfall_age": played["result"].get("shortfall_age"),
            "committed_shortfall_age": committed["result"].get("shortfall_age"),
        }
        response["ending_distribution"] = _distribution(
            cfg, scenario, choices, seed)
    return response
