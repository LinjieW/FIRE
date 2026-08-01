"""
engine_v98.py — adapter exposing the app's result contract on the authoritative
v9.8 engine chain (fire_v9_8_model + its v6→v9.6 dependencies).

WHY THIS EXISTS
  The interactive app was first wired to a simplified single-file core engine
  (fire_engine_v2, "2.3-rc"). This module replaces that with the REAL v9.8
  lifecycle engine that produced the official 1.5M-path baseline — the same
  mechanics behind the master report: ACA/MAGI, eldercare, inheritance, OBBBA,
  Shanghai property, promotion timing, China healthcare schedule, SS NRA
  haircut, Roth seasoning ladder, FTC, glide paths, stochastic inflation.

DESIGN
  * The full v9.8 parameter surface is exposed as a plain (JSON) config dict,
    grouped 1:1 with the engine's dataclasses. `default_config()` is built from
    the dataclass defaults, so it always equals the official baseline
    (class defaults + INITIAL_STACK_ACTUAL + match_excludes_bonus).
  * `build_kwargs()` maps that config back into the dataclass params.
  * `run_scenarios()`, `lifecycle_sample()`, `summary()`, `backtest()` emit
    exactly the JSON shapes the existing frontend (web/app.js, web/charts.js)
    already renders — so the whole UI is preserved.

Nothing here re-implements engine math; it only constructs params, runs the
engine, and reduces per-path results to the shapes the dashboard draws.
"""
from __future__ import annotations

import contextlib
import dataclasses
import datetime as _dt
import multiprocessing
import os
import threading
from enum import Enum
from typing import Optional

import numpy as np

import fire_v8_model
import fire_v9_8_model as V98
from fire_v9_8_model import (
    simulate_lifecycle_v98, run_lifecycle_mc_v98,
    simulate_retirement_v98, GuytonKlingerRuleV98,
    IncomeStreamSpec, HousingMortgageSpec, INCOME_STREAM_OWNERS,
)
from fire_v6_model import AccountStack, State, TaxParams, RelocationParams
from fire_v7_model import TaxParamsChina, V7Config, Regime, REGIMES
from fire_v8_model import PromotionParams, V8ContributionParams, HouseholdParams, LayoffParams
from fire_v9_1_model import (
    MedicalParams, ACAParams, ACAScenario,
    MortalityParams, MORTALITY_MALE, MORTALITY_FEMALE,
    FixedRealRule,
)
from fire_rules_x import STRATEGY_LIBRARY
from fire_returns_x import ReturnsXParams
import housing as HOUSING
from fire_v9_2_model import RothLadderParams, SocialSecurityParams, FTCParams
from fire_v9_3_model import (
    BondParams, GlidePath, OBBBAParams, OBBBAMode,
    EldercareShockParams, InheritanceParams, ShanghaiPropertyParams, ShockMode,
)
from fire_v9_6_model import ChinaHealthcareParams, SSNRAHaircutParams
from fire_tax_true import TrueTaxParams
from fire_rule_pack import (
    rule_pack_for_run as _rule_pack_for_run,
    rule_pack_reference_defaults as _rule_pack_reference_defaults,
)
from fire_v95_actual_baseline import INITIAL_STACK_ACTUAL, match_excludes_bonus

ENGINE_VERSION = "v9.8-rc"


def current_rule_pack(
        config: dict,
        *,
        evaluated_on: _dt.date | str = None,
) -> dict:
    """Capture one runtime-owned pack evaluation outside the user's config."""
    when = evaluated_on if evaluated_on is not None else _dt.date.today()
    return _rule_pack_for_run(config, as_of=when)


def rule_pack_reference_defaults() -> dict:
    return _rule_pack_reference_defaults()

# The account stack served as the editable default. Deliberately FUZZED —
# representative round numbers, NOT the real baseline the engine chain was
# calibrated on (the app is shared with other people; audit P0-1).
_BASELINE_STACK = dict(
    pretax_401k=95_000,
    roth_ira=45_000,
    hsa=15_000,
    taxable=50_000,
)


# ============================================================
# CONFIG SCHEMA (JSON) <-> dataclass params
# ============================================================
def _gd(cls, drop=()) -> dict:
    """Group defaults: {field: json_default} for a dataclass, enums -> .value."""
    out = {}
    for f in dataclasses.fields(cls):
        if f.name in drop:
            continue
        d = f.default
        if d is dataclasses.MISSING:
            d = (f.default_factory() if f.default_factory is not dataclasses.MISSING
                 else None)
        if isinstance(d, Enum):
            d = d.value
        out[f.name] = d
    return out


def default_config() -> dict:
    """The full editable config, defaulted to the official de-identified baseline.
    Every group mirrors a v9.8 dataclass; every field is user-editable."""
    return {
        # Schema version for saved plans / exports / drafts. Loaders deep-merge
        # any older config onto the current defaults (additive migration); bump
        # this only on a BREAKING semantic change and add a real migrator.
        "config_version": 2,
        "name": "Baseline · de-identified analyst",
        # state/contributions: dataclass defaults carry the real calibration
        # baseline, so the identifying scalars are overridden here (audit P0-1).
        "state": {**_gd(State), "start_age": 30, "expenses_y0": 42_000},
        "initial": dict(_BASELINE_STACK),
        "contributions": {**_gd(V8ContributionParams),
                          "base_salary_pre": 125_000, "ot_income_pre": 20_000},
        "promotion": _gd(PromotionParams),
        # returns 2.0 (E4): model "iid" (default, the v7 sampler untouched) |
        # "markov" (annual regime transitions) | "blocks" (1928-2024
        # historical block bootstrap). Extra knobs are no-ops while "iid".
        "returns": {**_gd(V7Config, drop=("n_paths", "seed")),
                    "expense_ratio": 0.0010, "rebalance_cost": 0.0,
                    "model": "iid", "persistence": 0.85, "block_years": 5,
                    "inflation_ar1": 0.0},
        "bonds": _gd(BondParams),
        "glide": _gd(GlidePath),
        "medical": _gd(MedicalParams),
        "aca": _gd(ACAParams),
        "mortality": {**_gd(MortalityParams), "sex": "male"},
        "household": _gd(HouseholdParams),
        "roth_ladder": _gd(RothLadderParams),
        "social_security": _gd(SocialSecurityParams),
        "ftc": _gd(FTCParams),
        "obbba": _gd(OBBBAParams),
        "eldercare": _gd(EldercareShockParams),
        "inheritance": _gd(InheritanceParams),
        "sh_property": _gd(ShanghaiPropertyParams),
        "tax_us": _gd(TaxParams),
        "tax_cn": _gd(TaxParamsChina),
        "relocation": {"enabled": False, **_gd(RelocationParams)},
        "china_healthcare": _gd(ChinaHealthcareParams),
        "ss_nra": _gd(SSNRAHaircutParams),
        "rule": {"upper_guardrail": 0.20, "lower_guardrail": 0.20,
                 "adjustment_pct": 0.10, "inflation_freeze_enabled": True},
        # Assets beyond the four engine buckets: cash/other_liquid fold into
        # taxable at run time; home equity is EXCLUDED from the simulation
        # (illiquid) unless a planned sale turns it into an inflow event.
        "other_assets": {"cash": 0, "other_liquid": 0, "home_equity": 0,
                         "sell_home_enabled": False, "sell_home_age": 65,
                         "sell_home_net_real": 0},
        # children: [{parent_age_at_birth, annual_cost_real, support_years,
        #             college_total_real}] — compiled into life events.
        "children": [],
        # Income streams beyond salary. Owners are additive schema leaves:
        # old plans normalize to "unspecified", preserving last-survivor numeric
        # behavior without inventing confirmed joint ownership.
        "income_streams": {
            "pension_enabled": False, "pension_annual_real": 0,
            "pension_start_age": 65, "pension_cola": True,
            "pension_owner": "unspecified",
            "rental_enabled": False, "rental_annual_net_real": 0,
            "rental_start_age": 30, "rental_end_age": 75,
            "rental_owner": "unspecified",
            "parttime_enabled": False, "parttime_annual_real": 0,
            "parttime_start_age": 40, "parttime_years": 10,
            "parttime_owner": "unspecified",
            "equity_enabled": False, "equity_annual_real": 0,
            "equity_years": 4, "equity_owner": "unspecified",
        },
        # life_events: [{age, amount_real, label}] — + = outflow, − = inflow.
        "life_events": [],
        # E5 housing (opt-in): housing cash flows compiled into life events
        # (rent path or down payment + mortgage/tax/maintenance), with
        # replace_annual refunding the housing budget already in expenses_y0.
        # Default OFF => zero events => bit-identical.
        "housing": dict(HOUSING.HOUSING_DEFAULTS),
        # TRUE year-by-year US tax engine (E1): brackets+LTCG stacking, SS
        # provisional-income taxation, RMD, IRMAA, true-MAGI ACA. Default OFF
        # => the flat/progressive approximations below stay in effect.
        "tax_true": _gd(TrueTaxParams, drop=("filing_jointly",)),
        # Layoff / income-interruption risk (accumulation): opt-in, default OFF.
        "layoff": {"enabled": False, "p_annual": 0.025, "return_threshold": -0.10,
                   "bad_year_multiplier": 3.0, "p_cap": 0.50, "gap_months": 4.0},
        "milestones": [1_000_000, 3_000_000],
        # I4 forecast-vs-actual check-ins: [{date, age, actual_total_nominal}].
        # Pure plan-file data — the engine NEVER reads this (guarded by a
        # bit-identical test); the trajectory page overlays it on the fan.
        "checkins": [],
    }


# --- de-identification guard (audit P0-1): the served defaults must never
# equal the real calibration baseline. Compares against the dataclass defaults
# dynamically so no real value is embedded here. Fails loudly at import.
_dc = default_config()
assert all(_dc["initial"][k] != getattr(INITIAL_STACK_ACTUAL, k)
           for k in _dc["initial"]), \
    "de-identification regressed: served portfolio equals the real baseline"
assert _dc["state"]["expenses_y0"] != _gd(State)["expenses_y0"], \
    "de-identification regressed: real expenses_y0 in served defaults"
assert _dc["contributions"]["base_salary_pre"] != _gd(V8ContributionParams)["base_salary_pre"], \
    "de-identification regressed: real base salary in served defaults"
del _dc


def _mk(cls, d: dict, enums: Optional[dict] = None):
    """Construct `cls` from dict `d`, coercing enum-valued fields and ignoring
    any keys the dataclass does not declare (e.g. relocation.enabled)."""
    d = dict(d or {})
    for k, EC in (enums or {}).items():
        if k in d and d[k] is not None and not isinstance(d[k], EC):
            d[k] = EC(d[k])
    valid = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in d.items() if k in valid})


def build_kwargs(cfg: dict, relocation_on: bool) -> dict:
    """Map the JSON config into the v9.8 param objects. Returns a kwargs dict for
    run_lifecycle_mc_v98 (the V7Config lands under 'config')."""
    g = lambda k: cfg.get(k, {}) or {}

    rl = dict(g("relocation"))
    if not relocation_on or not rl.get("enabled"):
        rl["relocation_age"] = None

    # E3: rule.type selects the withdrawal strategy. Absent or "gk" =>
    # exactly the v9.8 GK construction (bit-identical contract). _mk drops
    # params the chosen dataclass doesn't declare, so GK's guardrail fields
    # and e.g. VPW's depletion_age can coexist in the same rule dict.
    rd = dict(g("rule"))
    rule_type = str(rd.pop("type", "gk") or "gk")
    if rule_type == "gk":
        rule = _mk(GuytonKlingerRuleV98,
                   {"name": "GK Standard (interactive)", **rd})
    elif rule_type == "fixed_real":
        rule = _mk(FixedRealRule, rd)
    elif rule_type in STRATEGY_LIBRARY:
        rule = _mk(STRATEGY_LIBRARY[rule_type][0], rd)
    else:
        raise ValueError(f"unknown rule.type: {rule_type!r}")

    # Fees: fold the all-in expense ratio + rebalancing/turnover cost into the
    # engine's friction. Applies to BOTH accumulation and retirement — note the
    # engine's default accumulation friction is 0, so fees during the ~25 saving
    # years were previously unmodelled.
    ret = g("returns")
    cfg_obj = _mk(V7Config, ret)

    # E4: returns.model != "iid" activates the 2.0 sampler. NOTE the
    # μ-sensitivity regime shift flows into markov but cannot apply to
    # blocks (historical table) — disclosed in the app's limitations.
    _rmodel = str(ret.get("model", "iid") or "iid")
    returns_x = None
    if _rmodel != "iid":
        if _rmodel not in ("markov", "blocks"):
            raise ValueError(f"unknown returns.model: {_rmodel!r}")
        returns_x = ReturnsXParams(
            enabled=True, model=_rmodel,
            persistence=float(ret.get("persistence", 0.85) or 0.0),
            block_years=int(ret.get("block_years", 5) or 5),
            inflation_ar1=float(ret.get("inflation_ar1", 0.0) or 0.0))
    fee = float(ret.get("expense_ratio", 0) or 0) + float(ret.get("rebalance_cost", 0) or 0)
    if fee:
        cfg_obj = dataclasses.replace(
            cfg_obj, friction_accum=cfg_obj.friction_accum + fee,
            friction_retire=cfg_obj.friction_retire + fee)

    # Mortality sex -> Gompertz table (male default; female lives longer).
    mort_d = dict(g("mortality"))
    _sex = mort_d.pop("sex", None)
    if _sex == "female":
        mort_d["alpha"], mort_d["beta"] = MORTALITY_FEMALE.alpha, MORTALITY_FEMALE.beta
    elif _sex == "male":
        mort_d["alpha"], mort_d["beta"] = MORTALITY_MALE.alpha, MORTALITY_MALE.beta

    # Household: combine spouse starting balances into the household stack.
    init = _mk(AccountStack, g("initial"))
    _hh = g("household")
    if _hh.get("enabled"):
        init = AccountStack(
            pretax_401k=init.pretax_401k + float(_hh.get("spouse_initial_pretax", 0) or 0),
            roth_ira=init.roth_ira + float(_hh.get("spouse_initial_roth", 0) or 0),
            hsa=init.hsa + float(_hh.get("spouse_initial_hsa", 0) or 0),
            taxable=init.taxable + float(_hh.get("spouse_initial_taxable", 0) or 0),
        )

    # Other assets: cash & other liquid instruments fold into taxable (same
    # tax treatment on withdrawal); home equity stays OUT of the simulated
    # stack (illiquid) unless a planned sale (inflow event below).
    oa = g("other_assets")
    _liquid = float(oa.get("cash") or 0) + float(oa.get("other_liquid") or 0)
    if _liquid:
        init = dataclasses.replace(init, taxable=init.taxable + _liquid)

    # Life events: explicit list + children compiler + planned home sale.
    # + = outflow (funded from the stack), − = inflow (credited to taxable).
    events = []
    for e in (cfg.get("life_events") or []):
        try:
            events.append((int(e["age"]), float(e["amount_real"])))
        except (KeyError, TypeError, ValueError):
            continue
    for ch in (cfg.get("children") or []):
        try:
            b = int(ch.get("parent_age_at_birth", 32))
            annual = float(ch.get("annual_cost_real", 15000))
            yrs = int(ch.get("support_years", 22))
            college = float(ch.get("college_total_real", 0))
        except (TypeError, ValueError):
            continue
        for k in range(max(0, yrs)):
            events.append((b + k, annual))
        if college > 0:
            for k in range(4):
                events.append((b + 18 + k, college / 4.0))
    if oa.get("sell_home_enabled") and float(oa.get("sell_home_net_real") or 0) > 0:
        events.append((int(oa.get("sell_home_age", 65)),
                       -float(oa["sell_home_net_real"])))

    # Structured annual income: today's-dollar, after-tax spendable cash.
    # Ages stay on the primary user's timeline. Disabled streams do not validate
    # dormant owner data and compile to no runtime object (OFF-path identity).
    ist = g("income_streams")
    st_d = g("state")
    _sa = int(st_d.get("start_age", 30) or 30)
    structured_income = []

    def _owner(kind: str, enabled: bool) -> str:
        field = f"{kind}_owner"
        raw = ist.get(field, "unspecified")
        owner = "unspecified" if raw is None or raw == "" else str(raw)
        if enabled and owner not in INCOME_STREAM_OWNERS:
            raise ValueError(
                f"income_streams.{field} must be one of "
                + ", ".join(INCOME_STREAM_OWNERS))
        return owner

    def _enabled_amount(field: str) -> float:
        amount = float(ist.get(field) or 0)
        if not np.isfinite(amount):
            raise ValueError(
                f"income_streams.{field} must be finite")
        return amount

    pension_enabled = bool(ist.get("pension_enabled"))
    if pension_enabled:
        pension_owner = _owner("pension", True)
        pension_amount = _enabled_amount("pension_annual_real")
        if pension_amount > 0:
            structured_income.append(IncomeStreamSpec(
                kind="pension",
                owner=pension_owner,
                annual_real=pension_amount,
                start_age=int(ist.get("pension_start_age", 65)),
                cola=bool(ist.get("pension_cola", True)),
            ))

    rental_enabled = bool(ist.get("rental_enabled"))
    if rental_enabled:
        rental_owner = _owner("rental", True)
        rental_amount = _enabled_amount("rental_annual_net_real")
        if rental_amount > 0:
            structured_income.append(IncomeStreamSpec(
                kind="rental",
                owner=rental_owner,
                annual_real=rental_amount,
                start_age=int(ist.get("rental_start_age", 30)),
                end_age=int(ist.get("rental_end_age", 75)),
            ))

    parttime_enabled = bool(ist.get("parttime_enabled"))
    if parttime_enabled:
        parttime_owner = _owner("parttime", True)
        parttime_amount = _enabled_amount("parttime_annual_real")
        if parttime_amount > 0:
            structured_income.append(IncomeStreamSpec(
                kind="parttime",
                owner=parttime_owner,
                annual_real=parttime_amount,
                start_age=int(ist.get("parttime_start_age", 40)),
                duration_years=int(ist.get("parttime_years", 10) or 0),
                after_fire_only=True,
            ))

    equity_enabled = bool(ist.get("equity_enabled"))
    if equity_enabled:
        equity_owner = _owner("equity", True)
        equity_amount = _enabled_amount("equity_annual_real")
        if equity_amount > 0:
            structured_income.append(IncomeStreamSpec(
                kind="equity",
                owner=equity_owner,
                annual_real=equity_amount,
                start_age=_sa + 1,
                duration_years=int(ist.get("equity_years", 4) or 0),
            ))

    # E5 housing -> static yearly events (rent / down payment / refunds). When
    # a real mortgage exists, the positive post-purchase carrying rows travel
    # with its frozen internal spec so lifecycle/backtest can merge carrying +
    # realized-CPI mortgage before generic funding/shortfall handling. Rent and
    # 100%-down buy mode have no mortgage spec and retain static carrying rows.
    mortgage_payload = HOUSING.compile_housing_mortgage(cfg)
    events.extend(HOUSING.compile_housing_events(
        cfg, include_mortgage=False, include_carry=mortgage_payload is None))
    housing_mortgage = (
        HousingMortgageSpec(
            purchase_age=mortgage_payload["purchase_age"],
            payments=mortgage_payload["payments"],
            carrying_by_age=mortgage_payload["carrying_by_age"],
        ) if mortgage_payload is not None else None
    )

    # Resolve the working-years living cost driving the taxable-savings
    # residual: explicit value, else the user's retirement spending — never
    # the engine's calibration default (adapter correctness fix, 2026-07-10).
    contrib_d = dict(g("contributions"))
    if not contrib_d.get("annual_spending_now"):
        contrib_d["annual_spending_now"] = float(st_d.get("expenses_y0", 40_440) or 40_440)

    return {
        "config": cfg_obj,
        "state": _mk(State, g("state")),
        "initial": init,
        "contrib_params": _mk(V8ContributionParams, contrib_d),
        "promo_params": _mk(PromotionParams, g("promotion")),
        "bond_params": _mk(BondParams, g("bonds")),
        "glide_path": _mk(GlidePath, g("glide")),
        "medical": _mk(MedicalParams, g("medical")),
        "aca": _mk(ACAParams, {
            **g("aca"),
            **({"household_size": 2 if _hh.get("enabled") else 1}
               if any(f.name == "household_size" for f in dataclasses.fields(ACAParams))
               and "household_size" not in g("aca") else {}),
        }, {"scenario": ACAScenario}),
        "mortality": _mk(MortalityParams, mort_d),
        "roth_ladder": _mk(RothLadderParams, g("roth_ladder")),
        "ss": _mk(SocialSecurityParams, g("social_security")),
        "ftc": _mk(FTCParams, g("ftc")),
        "obbba": _mk(OBBBAParams, g("obbba"), {"mode": OBBBAMode}),
        "eldercare": _mk(EldercareShockParams, g("eldercare"), {"mode": ShockMode}),
        "inheritance": _mk(InheritanceParams, g("inheritance"), {"mode": ShockMode}),
        "sh_property": _mk(ShanghaiPropertyParams, g("sh_property")),
        "tax_us": _mk(TaxParams, g("tax_us")),
        "tax_cn": _mk(TaxParamsChina, g("tax_cn")),
        "relocation": _mk(RelocationParams, rl),
        "china_healthcare": _mk(ChinaHealthcareParams, g("china_healthcare")),
        "ss_nra": _mk(SSNRAHaircutParams, g("ss_nra")),
        "rule": rule,
        "fire_swr": float(g("state").get("swr_pref", 0.0333)),
        "life_events": (sorted(events) or None),
        "housing_mortgage": housing_mortgage,
        "income_streams": (tuple(structured_income) or None),
        "tax_true": _mk(TrueTaxParams, {**g("tax_true"),
                                        "filing_jointly": bool(_hh.get("enabled"))}),
        "returns_x": returns_x,
    }


def _milestones(cfg: dict) -> list:
    ms = cfg.get("milestones") or [1_000_000, 3_000_000]
    return [float(m) for m in ms]


def _expenses_y0(cfg: dict) -> float:
    return float((cfg.get("state") or {}).get("expenses_y0", 40_440) or 1.0)


# ============================================================
# RUN + PER-PATH STAT EXTRACTION
# ============================================================
#: blended long-run arithmetic equity mean of the default regime mixture,
#: used as the x-axis center for the μ-uncertainty band.
BASE_MU = sum(r.prob * r.params(15)[0] for r in REGIMES)


def _shifted_regimes(shift: float) -> list:
    """The default regime mixture with every regime's mean return moved by
    `shift` (sigma unchanged). Used for the μ-sensitivity section."""
    out = []
    for r in REGIMES:
        base = r.params
        out.append(Regime(r.name, r.prob,
                          (lambda y, b=base, s=shift: (b(y)[0] + s, b(y)[1])),
                          getattr(r, "rationale", "")))
    return out


# Serializes all engine runs: the engine chain communicates opt-in settings
# through module-level globals (fire_v8_model._HOUSEHOLD, match_excludes_bonus),
# which are process-wide. See audit P1-1.
_ENGINE_LOCK = threading.Lock()


class _AccumPathWithEventMeta(list):
    pass


@contextlib.contextmanager
def _event_meta_ctx():
    """Retain the upstream accumulation helper's otherwise-discarded metadata."""
    original = V98._apply_life_events_accum_v98

    def wrapped(*args, **kwargs):
        path, meta = original(*args, **kwargs)
        captured = _AccumPathWithEventMeta(path)
        captured.event_meta = dict(meta or {})
        return captured, meta

    V98._apply_life_events_accum_v98 = wrapped
    try:
        yield
    finally:
        V98._apply_life_events_accum_v98 = original


@contextlib.contextmanager
def _layoff_ctx(cfg: dict, seed: int):
    """Set fire_v8_model._LAYOFF for the run (career stream seed+7_000_000,
    the v2 engine's convention). Off => None => zero draws, bit-identical."""
    lo = (cfg.get("layoff") or {})
    if lo.get("enabled"):
        params = _mk(LayoffParams, {k: v for k, v in lo.items() if k != "rng"})
        params.rng = np.random.default_rng(int(seed) + 7_000_000)
        prev = fire_v8_model._LAYOFF
        fire_v8_model._LAYOFF = params
        try:
            yield
        finally:
            fire_v8_model._LAYOFF = prev
    else:
        yield


@contextlib.contextmanager
def _household_ctx(cfg: dict):
    """Set the module-level household hook (read by compute_contributions_for_year
    and simulate_retirement_v98) for the duration of a run, then restore it.
    Mirrors the match_excludes_bonus() pattern. Off => single-person, unchanged."""
    hh = _mk(HouseholdParams, (cfg.get("household") or {}))
    prev = fire_v8_model._HOUSEHOLD
    fire_v8_model._HOUSEHOLD = hh if hh.enabled else None
    try:
        yield
    finally:
        fire_v8_model._HOUSEHOLD = prev


def _run(cfg: dict, n: int, seed: int, relocation_on: bool,
         mu_shift: Optional[float] = None, cb=None) -> list:
    """Sequential shared-stream Monte Carlo — byte-identical to
    run_lifecycle_mc_v98(per_path_substreams=False), but with an optional
    progress callback cb(frac in [0,1)) invoked periodically for the progress
    bar. Same rng draw order => same results with or without cb."""
    kw = build_kwargs(cfg, relocation_on)
    config = kw.pop("config")
    # Return posture: the user's `returns.equity_mu_shift` moves the whole regime
    # mixture's mean; the sensitivity μ-band's own shift composes on top of it, so
    # the band explores μ *around the user's chosen central assumption*.
    base_shift = float((cfg.get("returns") or {}).get("equity_mu_shift", 0.0) or 0.0)
    total_shift = base_shift + (mu_shift or 0.0)
    if abs(total_shift) > 1e-12:
        kw["regimes"] = _shifted_regimes(total_shift)
    n = int(n)
    rng = np.random.default_rng(int(seed))
    out = []
    step = max(1, n // 40)
    # _ENGINE_LOCK: match_excludes_bonus/_household_ctx set process-wide globals
    # in the engine chain; the HTTP server is threaded (background job + on-demand
    # sweep/sensitivity/backtest requests), so engine runs must be serialized or
    # concurrent runs with different settings silently corrupt each other (audit P1-1).
    with (_ENGINE_LOCK, match_excludes_bonus(), _household_ctx(cfg),
          _layoff_ctx(cfg, seed), _event_meta_ctx()):
        for i in range(n):
            result = simulate_lifecycle_v98(config=config, rng=rng, **kw)
            out.append(_annotate_result(result, cfg, kw.get("life_events")))
            if cb is not None and i % step == 0:
                cb(i / n)
    return out


def _cpi_end(wd: dict):
    rc = wd.get("real_consumption_path") or []
    nc = wd.get("nominal_consumption_path") or []
    if rc and nc and rc[-1]:
        return nc[-1] / rc[-1]
    return None


def _real_lifetime_total(nominal: float, wd: dict) -> float:
    """Deflate a multi-year nominal total using its observed annual CPI span."""
    rc = wd.get("real_consumption_path") or []
    nc = wd.get("nominal_consumption_path") or []
    cpis = [n / r for n, r in zip(nc, rc) if r and np.isfinite(n / r)]
    tax_path = wd.get("true_tax_path_nominal") or []
    if tax_path and len(tax_path) == len(cpis):
        return float(sum(t / cpi for t, cpi in zip(tax_path, cpis)))
    return float(nominal or 0.0) / (float(np.mean(cpis)) if cpis else 1.0)


def _after_tax_value(accounts, tax_us) -> float:
    if accounts is None:
        return 0.0
    tr = min(max(float(getattr(tax_us, "withdrawal_tax_traditional", 0.0)), 0.0), 1.0)
    tx = min(max(float(getattr(tax_us, "withdrawal_tax_taxable", 0.0)), 0.0), 1.0)
    return max(0.0, (float(accounts.pretax_401k) * (1.0 - tr)
                     + float(accounts.taxable) * (1.0 - tx)
                     + float(accounts.roth_ira) + float(accounts.hsa)))


def _accum_event_failure_age(r: dict):
    ages = [int(s["age"]) for s in (r.get("event_shortfalls") or [])
            if s.get("phase") == "accumulation"]
    return min(ages) if ages else None


def _annotate_result(r: dict, cfg: dict, life_events) -> dict:
    """Expose mandatory-event floors and make them financial failures."""
    positive = {}
    for age, amount in (life_events or []):
        if float(amount) > 0:
            positive[int(age)] = positive.get(int(age), 0.0) + float(amount)

    shortfalls = []
    accum_meta = r.get("accum_life_event_meta") or {}
    for age, amount in (accum_meta.get("shortfall_real_by_age") or {}).items():
        age = int(age)
        # The realized-CPI housing seam is resolved inside v9.8 after path
        # sampling, so its merged carrying+mortgage row is not present in the
        # adapter's static life_events list. The engine metadata is authoritative
        # for the housing aggregate at a shortfall age.
        mandatory = (accum_meta.get("out_real_by_age") or {}).get(age)
        if mandatory is None:
            mandatory = positive.get(age, float(amount))
        shortfalls.append({
            "age": age,
            "phase": "accumulation",
            "mandatory_outflow_real": float(mandatory),
            "shortfall_real": float(amount),
        })

    wd = r.get("withdrawal") or {}
    shortfalls.extend(dict(s) for s in (wd.get("life_event_shortfalls") or []))

    shortfalls = sorted({(s["age"], s["phase"]): s for s in shortfalls}.values(),
                        key=lambda s: (s["age"], s["phase"]))
    r["event_shortfalls"] = shortfalls
    r["event_shortfall_count"] = len(shortfalls)
    if wd:
        wd["event_shortfalls"] = shortfalls
    if shortfalls:
        r["lifetime_success"] = False
        if wd and any(s.get("phase") == "retirement" for s in shortfalls):
            wd["survived_financially"] = False

    successful = bool(r.get("lifetime_success"))
    accounts, cpi = None, 1.0
    if r.get("reached_fire") and wd:
        accounts, cpi = wd.get("final_accounts"), (_cpi_end(wd) or 1.0)
    elif successful and (r.get("accum_path") or []):
        path = r["accum_path"]
        if r.get("died_during_accum") and r.get("age_at_death") is not None:
            visible = [step for step in path
                       if int(step.get("age", -1)) <= int(r["age_at_death"])]
            last = visible[-1] if visible else path[0]
        else:
            last = path[-1]
        accounts = last.get("accounts")
        exp0 = _expenses_y0(cfg)
        cpi = float(last.get("expenses") or exp0) / exp0
    r["terminal_after_tax_real"] = (
        _after_tax_value(accounts, _mk(TaxParams, cfg.get("tax_us") or {}))
        / max(cpi, 1e-9) if successful else 0.0)
    return r


def _path_stats(results: list, milestones: list) -> dict:
    """Reduce raw per-path dicts to scalar columns (official chunked_runner
    semantics) plus milestone-crossing ages and the cash-conservation residual."""
    cols = {k: [] for k in (
        "fire_age", "reached", "died_accum", "success", "post_fire_success",
        "cons", "term_nom", "term_real", "ss", "min_cons", "lifestyle",
        "true_tax", "true_tax_nominal", "terminal_after_tax_real",
        "event_shortfall")}
    ms_ages = {m: [] for m in milestones}
    inv_max = 0.0
    inv_checked = 0

    for r in results:
        accum_failure_age = _accum_event_failure_age(r)
        reached = bool(r.get("reached_fire")) and accum_failure_age is None
        died = bool(r.get("died_during_accum")) and accum_failure_age is None
        cols["reached"].append(1.0 if reached else 0.0)
        cols["died_accum"].append(1.0 if died else 0.0)
        cols["success"].append(1.0 if r.get("lifetime_success") else 0.0)
        cols["event_shortfall"].append(1.0 if r.get("event_shortfalls") else 0.0)
        cols["terminal_after_tax_real"].append(
            float(r.get("terminal_after_tax_real") or 0.0))

        ap = r.get("accum_path") or []
        censor_age = (int(r["age_at_death"])
                      if died and r.get("age_at_death") is not None
                      else accum_failure_age)
        first = {m: None for m in milestones}
        for step in ap:
            if censor_age is not None and int(step["age"]) > censor_age:
                break
            t = step["total"]
            for m in milestones:
                if first[m] is None and t >= m:
                    first[m] = step["age"]
        for m in milestones:
            ms_ages[m].append(first[m])

        if reached:
            cols["fire_age"].append(r.get("fire_age"))
            wd = r.get("withdrawal") or {}
            cols["post_fire_success"].append(
                1.0 if wd.get("lifetime_success") else 0.0)
            tb = wd.get("terminal_balance")
            ce = _cpi_end(wd)
            cols["cons"].append(wd.get("mean_real_consumption"))
            cols["term_nom"].append(tb)
            cols["term_real"].append((tb / ce) if (tb and ce) else None)
            cols["ss"].append(wd.get("ss_total_received_real") or 0.0)
            cols["min_cons"].append(wd.get("min_real_consumption"))
            cols["lifestyle"].append(wd.get("mean_lifestyle_real"))
            _tt_nom = float(wd.get("true_tax_total_nominal", 0.0) or 0.0)
            cols["true_tax_nominal"].append(_tt_nom)
            cols["true_tax"].append(float(
                wd.get("true_tax_total_real", _real_lifetime_total(_tt_nom, wd))
                or 0.0))

            if inv_checked < 400:
                lhs = float(np.sum(wd.get("nominal_consumption_path") or [0.0]))
                rhs = (float(wd.get("total_wd_received_nominal") or 0.0)
                       + float(wd.get("total_ss_applied_nominal") or 0.0)
                       + float(wd.get("total_income_applied_nominal") or 0.0))
                inv_max = max(inv_max, abs(lhs - rhs) / max(abs(lhs), 1.0))
                inv_checked += 1

    return {"cols": cols, "ms_ages": ms_ages,
            "inv_max": inv_max, "inv_checked": inv_checked, "n": len(results)}


def _pc(xs, ps=(10, 25, 50, 75, 90), truthy=False):
    a = np.array([x for x in xs if x is not None and (not truthy or x != 0)],
                 dtype=float)
    if a.size == 0:
        return {p: None for p in ps}
    return {p: float(np.percentile(a, p)) for p in ps}


def _p3(xs, truthy=True):
    d = _pc(xs, (10, 50, 90), truthy=truthy)
    return {"p10": d[10], "p50": d[50], "p90": d[90]}


def _summarize(st: dict, milestones: list) -> dict:
    n = max(1, st["n"])
    c = st["cols"]
    reached = sum(c["reached"])
    died = sum(c["died_accum"])
    fire = [x for x in c["fire_age"] if x is not None]
    fa = _pc(fire, (10, 50, 90)) if fire else {10: None, 50: None, 90: None}

    ms_out = {}
    for m in milestones:
        ages = [a for a in st["ms_ages"][m] if a is not None]
        p = _pc(ages, (10, 50, 90)) if ages else {10: None, 50: None, 90: None}
        ms_out[str(int(m))] = {
            "reach_probability": len(ages) / n,
            "median_age": p[50], "p10_age": p[10], "p90_age": p[90],
        }

    return {
        "n_paths": st["n"], "engine_version": ENGINE_VERSION,
        "lifetime_success": sum(c["success"]) / n,
        "reached_fi_rate": reached / n,
        "died_during_accum_rate": died / n,
        "true_accumulation_failure_rate": max(0.0, (n - reached - died) / n),
        "post_fire_solvency": (sum(c["post_fire_success"]) / reached) if reached else 0.0,
        "fire_age": {"p10": fa[10], "p50": fa[50], "p90": fa[90],
                     "min": int(min(fire)) if fire else None},
        "milestones": ms_out,
        "terminal_nominal": _p3(c["term_nom"]),
        "terminal_real": _p3(c["term_real"]),
        "terminal_after_tax_real": _p3(c["terminal_after_tax_real"], truthy=False),
        "mean_real_consumption": _p3(c["cons"]),
        "min_real_consumption": _p3(c["min_cons"], truthy=False),
        "mean_lifestyle_real": _p3(c["lifestyle"]),
        "ss_total_real": _p3(c["ss"], truthy=False),
        "event_shortfall_rate": sum(c["event_shortfall"]) / n,
        "invariant_max_rel_error": st["inv_max"],
        "invariant_paths_checked": st["inv_checked"],
        "true_tax_real": _pc(st["cols"]["true_tax"]),
        "true_tax_nominal": _pc(st["cols"]["true_tax_nominal"]),
    }


# ============================================================
# PUBLIC: headline scenarios
# ============================================================
def run_scenarios(cfg: dict, n_paths: int, seed: int) -> dict:
    """Home (relocation forced off) + optional relocation, each summarized in the
    exact shape the dashboard reads."""
    ms = _milestones(cfg)
    reloc_on = bool((cfg.get("relocation") or {}).get("enabled", False))

    home = _summarize(_path_stats(_run(cfg, n_paths, seed, False), ms), ms)
    out = {"meta": {"name": cfg.get("name", "FIRE plan")}, "home": home}
    if reloc_on:
        out["relocation"] = _summarize(
            _path_stats(_run(cfg, n_paths, seed, True), ms), ms)
    return out


# ============================================================
# CHUNKED PARALLEL RUNNER (official 1.5M protocol: seeds seed+idx)
# ============================================================
# Fixed chunk size => results depend only on (paths, seed), never on how many
# cores the machine has. Workers reduce to _path_stats before returning, so
# IPC carries compact columns, not raw paths. Used only for large summary runs.
MP_CHUNK = 5_000
MP_THRESHOLD = 20_000

_MP_Q = None


def _mp_init(q):
    global _MP_Q
    _MP_Q = q


def _mp_chunk(args):
    """Worker: run one chunk sequentially and reduce it. Runs in a spawned
    process — module import re-runs the engine chain there."""
    cfg, n, seed, reloc, idx = args
    def cb(frac):
        try:
            _MP_Q.put((idx, frac), block=False)
        except Exception:
            pass
    res = _run(cfg, n, seed, reloc, cb=cb)
    st = _path_stats(res, _milestones(cfg))
    try:
        _MP_Q.put((idx, 1.0), block=False)
    except Exception:
        pass
    return st


def _merge_stats(parts: list) -> dict:
    out = {"cols": {}, "ms_ages": {}, "inv_max": 0.0, "inv_checked": 0, "n": 0}
    for st in parts:
        for k, v in st["cols"].items():
            out["cols"].setdefault(k, []).extend(v)
        for m, v in st["ms_ages"].items():
            out["ms_ages"].setdefault(m, []).extend(v)
        out["inv_max"] = max(out["inv_max"], st["inv_max"])
        out["inv_checked"] += st["inv_checked"]
        out["n"] += st["n"]
    return out


def _run_chunked_stats(cfg: dict, n: int, seed: int, reloc: bool, cb=None) -> dict:
    """Parallel chunked run returning MERGED _path_stats. Deterministic for a
    given (n, seed): chunk layout is fixed (MP_CHUNK), chunk i uses seed+i —
    the same protocol family as the official 1.5M baseline (seeds 96000+idx).
    Worker count only affects wall-clock time."""
    sizes = []
    left = int(n)
    while left > 0:
        take = min(MP_CHUNK, left)
        sizes.append(take)
        left -= take
    args = [(cfg, sizes[i], int(seed) + i, reloc, i) for i in range(len(sizes))]
    workers = int(os.environ.get("FIRE_MP_WORKERS") or 0) \
        or max(1, min(len(sizes), (os.cpu_count() or 4) - 1))
    total = float(sum(sizes))
    ctx = multiprocessing.get_context("spawn")   # threads live in this server
    q = ctx.Queue()
    fracs = {}
    pool = ctx.Pool(workers, initializer=_mp_init, initargs=(q,))
    try:
        async_res = pool.map_async(_mp_chunk, args)
        # Watchdog: if workers die at bootstrap the pool respawns them forever
        # and map_async never completes — bail out after 120s without progress
        # instead of hanging the job thread.
        import time as _time
        last_progress = _time.monotonic()
        last_sum = 0.0
        while True:
            got = False
            try:
                idx, frac = q.get(timeout=0.4)
                got = True
                fracs[idx] = max(fracs.get(idx, 0.0), float(frac))
                cur = sum(fracs.get(i, 0.0) * sizes[i] for i in range(len(sizes)))
                if cur > last_sum:
                    last_sum = cur
                    last_progress = _time.monotonic()
                if cb:
                    cb(min(0.999, cur / total))
            except Exception:
                pass   # queue timeout — fall through to readiness check
            if async_res.ready():
                break
            if not got and _time.monotonic() - last_progress > 120:
                raise RuntimeError(
                    "parallel chunk workers made no progress for 120s "
                    "(worker bootstrap failure?) — aborting chunked run")
        parts = async_res.get()
        if cb:
            cb(1.0)
        return _merge_stats(parts)
    except BaseException:
        pool.terminate()
        raise
    finally:
        pool.close()
        pool.join()


def run_full(cfg: dict, n_paths: int, seed: int, dist_paths: int, cb=None) -> dict:
    """The full headline payload (summaries + illustrative distributions) with a
    single combined progress callback cb(pct in [0,1], stage_key). Runs home,
    then relocation (if enabled), then their distribution samples — reporting a
    smooth global percentage across all phases (for the progress bar)."""
    evaluated_on = _dt.date.today()
    ms = _milestones(cfg)
    reloc_on = bool((cfg.get("relocation") or {}).get("enabled", False))
    n, dist_paths = int(n_paths), int(dist_paths)

    phases = [("run_home", n, False, "summary")]
    if reloc_on:
        phases.append(("run_reloc", n, True, "summary"))
    phases.append(("dist_home", dist_paths, False, "dist"))
    if reloc_on:
        phases.append(("dist_reloc", dist_paths, True, "dist"))
    total = sum(p[1] for p in phases) or 1
    acc = [0]

    def phase_cb(count, stage):
        base = acc[0]
        def _cb(frac):
            if cb:
                cb(min(1.0, (base + frac * count) / total), stage)
        return _cb

    out = {"meta": {
        "name": cfg.get("name", "FIRE plan"),
        "rule_pack": current_rule_pack(cfg, evaluated_on=evaluated_on),
    }}
    dist = {}
    for key, count, reloc, stage in phases:
        pc = phase_cb(count, key)
        if stage == "summary":
            if count >= MP_THRESHOLD:
                st = _run_chunked_stats(cfg, count, seed, reloc, cb=pc)
                out["mode"] = f"chunked-{-(-count // MP_CHUNK)}x{MP_CHUNK}"
            else:
                st = _path_stats(_run(cfg, count, seed, reloc, cb=pc), ms)
            out["relocation" if reloc else "home"] = _summarize(st, ms)
        else:
            dist["relocation" if reloc else "home"] = lifecycle_sample(
                cfg, count, seed, reloc, cb=pc)
        acc[0] += count
    out["dist"] = dist
    if cb:
        cb(1.0, "done")
    return out


def summary(cfg: dict, n: int, seed: int, relocation_on: bool = False,
            mu_shift: Optional[float] = None) -> dict:
    """Compact metrics for sweeps / sensitivity. `mu_shift` moves the whole
    regime mixture's mean return (for the μ-uncertainty section)."""
    ms = _milestones(cfg)
    s = _summarize(_path_stats(_run(cfg, n, seed, relocation_on, mu_shift), ms), ms)
    return {
        "lifetime_success": s["lifetime_success"],
        "reached_fi_rate": s["reached_fi_rate"],
        "post_fire_solvency": s["post_fire_solvency"],
        "fire_age_p50": s["fire_age"]["p50"],
        "cons_p50": s["mean_real_consumption"]["p50"],
        "cons_p10": s["mean_real_consumption"]["p10"],
        "min_cons_p50": s["min_real_consumption"]["p50"],
        "terminal_real_p50": s["terminal_real"]["p50"],
        "terminal_after_tax_real_p50": s["terminal_after_tax_real"]["p50"],
        "ss_p50": s["ss_total_real"]["p50"],
        "true_tax_p50": (s.get("true_tax_real") or {}).get(50),
        "event_shortfall_rate": s.get("event_shortfall_rate", 0.0),
    }


# ============================================================
# PUBLIC: I2 story mode — single-path chronicles
# ============================================================
def _story_chronicle(r: dict, cfg: dict) -> dict:
    """Extract one path's year-by-year chronicle: real-wealth curve + notable
    events (crash years, milestones, promotion, FIRE, SS claim, ending)."""
    st = cfg.get("state") or {}
    exp0 = _expenses_y0(cfg)
    start_age = int(st.get("start_age", 30))
    milestones = _milestones(cfg)
    ap = r.get("accum_path") or []
    wd = r.get("withdrawal") or {}
    fire_age = r.get("fire_age")
    accum_failure_age = _accum_event_failure_age(r)

    curve, events = [], []
    hit = set()
    prev_real = None

    def _mark_crash(age, chg):
        events.append({"age": int(age), "kind": "crash", "v": round(chg, 4)})

    fire_idx = None
    for i, s in enumerate(ap):
        if (r.get("died_during_accum") and r.get("age_at_death") is not None
                and int(s["age"]) > int(r["age_at_death"])):
            break
        if accum_failure_age is not None and int(s["age"]) > accum_failure_age:
            break
        cpi = (s.get("expenses") or exp0) / exp0
        tr = s["total"] / max(cpi, 1e-9)
        if accum_failure_age is None and fire_age is not None and s["age"] > fire_age:
            break                      # retirement covered by portfolio_path
        curve.append([int(s["age"]), round(tr, 2)])
        if prev_real is not None and prev_real > 1000 and tr / prev_real - 1 < -0.15:
            _mark_crash(s["age"], tr / prev_real - 1)
        for m in milestones:
            if m not in hit and s["total"] >= m:
                hit.add(m)
                events.append({"age": int(s["age"]), "kind": "milestone", "v": m})
        prev_real = tr
        if accum_failure_age is None and fire_age is not None and s["age"] == fire_age:
            fire_idx = i
    if r.get("promotion_year"):
        events.append({"age": start_age + int(r["promotion_year"]), "kind": "promotion"})
    if fire_age is not None and accum_failure_age is None:
        fire_real = curve[-1][1] if curve else None
        events.append({"age": int(fire_age), "kind": "fire", "v": fire_real})

    # retirement leg: deflate the nominal portfolio path with the consumption CPI
    pp = wd.get("portfolio_path") or []
    rc = wd.get("real_consumption_path") or []
    nc = wd.get("nominal_consumption_path") or []
    cpi_fire = (ap[fire_idx].get("expenses") / exp0) if (fire_idx is not None) else 1.0
    prev_real = curve[-1][1] if curve else None
    for i in range(1, len(pp) if accum_failure_age is None else 1):
        if i - 1 < len(rc) and rc[i - 1]:
            cpi_i = nc[i - 1] / rc[i - 1]
        elif rc and rc[-1]:
            cpi_i = nc[-1] / rc[-1]     # death-year tail point: the loop broke
            #                             before consuming, so reuse the last
            #                             known CPI instead of the FIRE-year one
        else:
            cpi_i = cpi_fire
        tr = pp[i] / max(cpi_i, 1e-9)
        age = int(fire_age) + i
        curve.append([age, round(tr, 2)])
        if prev_real and prev_real > 1000 and tr / prev_real - 1 < -0.15:
            _mark_crash(age, tr / prev_real - 1)
        prev_real = tr

    ss = cfg.get("social_security") or {}
    if ss.get("enabled") and fire_age is not None:
        ca = int(ss.get("claim_age", 67))
        if curve and ca <= curve[-1][0]:
            events.append({"age": ca, "kind": "ss_claim"})

    if r.get("died_during_accum"):
        ending = {"kind": "died", "age": int(r["age_at_death"]),
                  "legacy_real": curve[-1][1] if curve else None}
    elif not r.get("reached_fire"):
        ending = {"kind": "never_fired"}
    elif wd.get("shortfall_age") or r.get("event_shortfalls"):
        _ages = [s["age"] for s in r.get("event_shortfalls") or []]
        ending = {"kind": "ruin",
                  "age": (int(wd["shortfall_age"]) if wd.get("shortfall_age")
                          else (min(_ages) if _ages else None))}
    elif wd.get("age_at_death"):
        ending = {"kind": "died", "age": int(wd["age_at_death"]),
                  "legacy_real": curve[-1][1] if curve else None}
    else:
        ending = {"kind": "horizon", "age": curve[-1][0] if curve else None,
                  "legacy_real": curve[-1][1] if curve else None}

    events.sort(key=lambda e: e["age"])
    return {
        "regime": r.get("regime"), "fire_age": fire_age,
        "curve": curve, "events": events, "ending": ending,
        "guardrail_triggers": wd.get("guardrail_triggers"),
        "mean_real_consumption": wd.get("mean_real_consumption"),
    }


def story(cfg: dict, n: int, seed: int) -> dict:
    """Run one small batch and pick three lives from the SAME distribution:
    typical (P50 by real terminal wealth), lucky (P90), unlucky (a ruined
    path if any exist, else P10). Within one batch the ordering contract
    lucky >= typical >= unlucky holds exactly."""
    results = _run(cfg, n, seed, False)
    fired = [r for r in results
             if r.get("reached_fire") and r.get("withdrawal")
             and _accum_event_failure_age(r) is None]
    if not fired:
        return {"n_paths": n, "seed": seed, "stories": None}

    def term_real(r):
        wd = r["withdrawal"]
        rc, nc = wd.get("real_consumption_path"), wd.get("nominal_consumption_path")
        cpi_end = (nc[-1] / rc[-1]) if (rc and rc[-1]) else 1.0
        return wd.get("terminal_balance", 0.0) / max(cpi_end, 1e-9)

    ranked = sorted(fired, key=term_real)
    qtile = lambda q: ranked[min(int(q * (len(ranked) - 1)), len(ranked) - 1)]
    ruined = [r for r in ranked if not r["withdrawal"].get("survived_financially")]
    picks = {
        "typical": qtile(0.50),
        "lucky": qtile(0.90),
        "unlucky": (ruined[len(ruined) // 2] if ruined else qtile(0.10)),
    }
    return {"n_paths": n, "seed": seed,
            "stories": {k: _story_chronicle(r, cfg) for k, r in picks.items()}}


# ============================================================
# PUBLIC: I3 drill-down — small-batch slices behind chart clicks
# ============================================================
def _term_real(r: dict) -> float:
    wd = r["withdrawal"]
    rc, nc = wd.get("real_consumption_path"), wd.get("nominal_consumption_path")
    cpi_end = (nc[-1] / rc[-1]) if (rc and rc[-1]) else 1.0
    return wd.get("terminal_balance", 0.0) / max(cpi_end, 1e-9)


def drill(cfg: dict, kind: str, seed: int, n: int = 200, **kw) -> dict:
    """One small batch, two drill kinds:
      * age_slice: the full real-wealth histogram + regime mix at one age
        (what the fan chart's percentile bands hide);
      * term_bucket: what the paths inside one terminal-value bucket have
        in common (regime mix, FIRE age, first-5-retirement-years growth,
        ruin rate) vs the whole batch.
    Reuses the story chronicles for per-path real-wealth curves — same
    deflation rules, already under test."""
    n = max(80, min(int(n), 400))
    results = _run(cfg, n, seed + 777_000, False)
    curves = [( r, dict(_story_chronicle(r, cfg)["curve"]) ) for r in results]

    if kind == "age_slice":
        age = int(kw["age"])
        vals, regimes = [], {}
        for r, cv in curves:
            v = cv.get(age)
            if v is not None and v > 0:
                vals.append(v)
                regimes[r.get("regime")] = regimes.get(r.get("regime"), 0) + 1
        return {"kind": kind, "age": age, "n": n, "alive": len(vals),
                "hist": _hist(vals), "regimes": regimes}

    if kind == "term_bucket":
        lo, hi = float(kw["lo"]), float(kw["hi"])
        fired = [(r, cv) for r, cv in curves
                 if r.get("reached_fire") and r.get("withdrawal")
                 and _accum_event_failure_age(r) is None]

        def stats(rows):
            if not rows:
                return None
            regimes, fages, g5, ruined = {}, [], [], 0
            for r, cv in rows:
                regimes[r.get("regime")] = regimes.get(r.get("regime"), 0) + 1
                fa = r.get("fire_age")
                fages.append(fa)
                v0, v1 = cv.get(fa), cv.get(fa + 5)
                if v0 and v1 and v0 > 0:
                    g5.append((v1 / v0) ** 0.2 - 1.0)
                if not r["withdrawal"].get("survived_financially", True):
                    ruined += 1
            return {"regimes": regimes,
                    "fire_age_p50": float(np.median(fages)),
                    "first5_annual_p50": (float(np.median(g5)) if g5 else None),
                    "ruin_rate": ruined / len(rows), "count": len(rows)}

        sub = [(r, cv) for r, cv in fired if lo <= _term_real(r) < hi]
        return {"kind": kind, "lo": lo, "hi": hi,
                "n_fired": len(fired),
                "share": (len(sub) / len(fired)) if fired else 0.0,
                "bucket": stats(sub), "all": stats(fired)}

    raise ValueError(f"unknown drill kind: {kind!r}")


# ============================================================
# PUBLIC: illustrative distributions (fan / consumption / terminal / milestones)
# ============================================================
def _bands_by_age(by_age: dict, n: int, min_frac=0.05) -> list:
    thr = max(20, int(min_frac * n))
    rows = []
    for a in sorted(by_age):
        vals = by_age[a]
        if len(vals) < thr:
            continue
        arr = np.array(vals, dtype=float)
        rows.append({
            "age": a,
            "p10": float(np.percentile(arr, 10)),
            "p25": float(np.percentile(arr, 25)),
            "p50": float(np.percentile(arr, 50)),
            "p75": float(np.percentile(arr, 75)),
            "p90": float(np.percentile(arr, 90)),
        })
    return rows


def _hist(vals, log=True, nbins=34) -> Optional[dict]:
    a = np.array([v for v in vals if v is not None and v > 0], dtype=float)
    if a.size < 5:
        return None
    lo, hi = float(a.min()), float(a.max())
    if log and lo > 0:
        edges = np.logspace(np.log10(lo), np.log10(hi), nbins + 1)
    else:
        edges = np.linspace(lo, hi, nbins + 1)
    counts, _ = np.histogram(a, bins=edges)
    return {"edges": [float(e) for e in edges], "counts": [int(c) for c in counts],
            "p10": float(np.percentile(a, 10)), "p50": float(np.percentile(a, 50)),
            "p90": float(np.percentile(a, 90)), "mean": float(a.mean())}


def lifecycle_sample(cfg: dict, n: int, seed: int, relocation_on: bool,
                     cb=None) -> dict:
    """Full-lifecycle percentile bands (nominal + real), retirement consumption
    fan, terminal histograms, and milestone-age distributions — an independent
    illustrative sample (NOT the headline run)."""
    ms = _milestones(cfg)
    exp0 = _expenses_y0(cfg)
    results = _run(cfg, n, seed + 4_242_000, relocation_on, cb=cb)

    by_age_real: dict = {}
    by_age_nom: dict = {}
    cons_by_age: dict = {}
    ms_ages = {m: [] for m in ms}
    term_real, term_nom, fire_ages = [], [], []

    for r in results:
        ap_all = r.get("accum_path") or []
        accum_failure_age = _accum_event_failure_age(r)
        valid_reached = (bool(r.get("reached_fire"))
                         and accum_failure_age is None)
        if accum_failure_age is not None:
            accum_end = accum_failure_age
        elif valid_reached and r.get("fire_age") is not None:
            accum_end = int(r["fire_age"])
        elif r.get("died_during_accum") and r.get("age_at_death") is not None:
            accum_end = int(r["age_at_death"])
        else:
            accum_end = None
        ap = [step for step in ap_all
              if accum_end is None or int(step["age"]) <= accum_end]
        for step in ap:
            age = step["age"]
            cpi = (step.get("expenses") or exp0) / exp0
            by_age_nom.setdefault(age, []).append(step["total"])
            by_age_real.setdefault(age, []).append(step["total"] / max(cpi, 1e-9))
        # milestone ages
        first = {m: None for m in ms}
        for step in ap:
            for m in ms:
                if first[m] is None and step["total"] >= m:
                    first[m] = step["age"]
        for m in ms:
            ms_ages[m].append(first[m])

        if not valid_reached:
            continue
        fa = r.get("fire_age")
        fire_ages.append(fa)
        wd = r.get("withdrawal") or {}
        pp = wd.get("portfolio_path") or []
        rc = wd.get("real_consumption_path") or []
        nc = wd.get("nominal_consumption_path") or []
        # cpi at FIRE from accum path
        cpi_fire = 1.0
        for step in ap:
            if step["age"] == fa:
                cpi_fire = (step.get("expenses") or exp0) / exp0
                break
        if pp:
            by_age_nom.setdefault(fa, []).append(pp[0])
            by_age_real.setdefault(fa, []).append(pp[0] / max(cpi_fire, 1e-9))
        for i in range(len(rc)):
            age = fa + i + 1
            cpi_i = (nc[i] / rc[i]) if (i < len(nc) and rc[i]) else cpi_fire
            if i + 1 < len(pp):
                by_age_nom.setdefault(age, []).append(pp[i + 1])
                by_age_real.setdefault(age, []).append(pp[i + 1] / max(cpi_i, 1e-9))
            cons_by_age.setdefault(age, []).append(rc[i])

        tb = wd.get("terminal_balance")
        ce = _cpi_end(wd)
        if tb:
            term_nom.append(tb)
            if ce:
                term_real.append(tb / ce)

    # milestone-age bar distributions
    ms_dist = {}
    for m in ms:
        ages = [a for a in ms_ages[m] if a is not None]
        if len(ages) < 5:
            ms_dist[str(int(m))] = None
            continue
        amin, amax = int(min(ages)), int(max(ages))
        span = list(range(amin, amax + 1))
        counts = [ages.count(a) for a in span]
        ms_dist[str(int(m))] = {
            "ages": span, "counts": counts,
            "p10": int(np.percentile(ages, 10)),
            "p50": int(np.percentile(ages, 50)),
            "p90": int(np.percentile(ages, 90)),
            "reach_frac": len(ages) / max(1, len(results)),
        }

    return {
        "n_paths": n,
        "start_age": int((cfg.get("state") or {}).get("start_age", 27)),
        "fire_age_p50": float(np.percentile(fire_ages, 50)) if fire_ages else None,
        "fan_real": _bands_by_age(by_age_real, n),
        "fan_nom": _bands_by_age(by_age_nom, n),
        "consumption": _bands_by_age(cons_by_age, n, min_frac=0.02),
        "terminal_real_hist": _hist(term_real, log=True),
        "terminal_nom_hist": _hist(term_nom, log=True),
        "milestones": ms_dist,
    }


# ============================================================
# PUBLIC: sequence-of-returns backtest (deterministic stress openings)
# ============================================================
STRESS = {
    "crash": {"label": "深度崩盘开局（大萧条式）",
              "eq": [-0.25, -0.18, -0.12, 0.20, 0.15, 0.12, 0.10],
              "infl": 0.02, "tail": 0.09},
    "lost_decade": {"label": "失去的十年（2000 式）",
                    "eq": [-0.09, -0.12, -0.22, 0.28, 0.11, 0.05, -0.37,
                           0.26, 0.15, 0.02],
                    "infl": 0.025, "tail": 0.09},
    "stagflation": {"label": "滞胀开局（1970 式）",
                    "eq": [0.02, -0.15, -0.26, 0.37, 0.24, -0.07, 0.06],
                    "infl": 0.065, "tail": 0.085},
}


def _pad(seq, n, tail):
    s = list(seq)[:n]
    while len(s) < n:
        s.append(tail)
    return s


def backtest(cfg: dict, retire_age, seed: int) -> dict:
    """Retire exactly at the FI number (spending / SWR) with the baseline's tax
    mix and account proportions, then apply each stylized stress sequence
    deterministically (mortality off) via the v9.8 retirement engine."""
    kw = build_kwargs(cfg, relocation_on=False)
    state = kw["state"]
    exp0 = _expenses_y0(cfg)
    swr = float((cfg.get("state") or {}).get("swr_pref", 0.0333)) or 0.0333
    target = exp0 / swr
    horizon = int(state.retire_horizon)
    ra = int(retire_age) if retire_age else int(state.start_age + 15)

    stack = kw["initial"]
    tot = stack.total or 1.0
    start = AccountStack(
        pretax_401k=stack.pretax_401k / tot * target,
        roth_ira=stack.roth_ira / tot * target,
        hsa=stack.hsa / tot * target,
        taxable=stack.taxable / tot * target,
    )
    mort = _mk(MortalityParams, {**(cfg.get("mortality") or {}), "enabled": False})

    scen = {}
    for key, sc in STRESS.items():
        eq = _pad(sc["eq"], horizon, sc["tail"])
        bd = [0.03] * len(eq)
        infl = [sc["infl"]] * len(eq)
        # Backtest starts at a synthetic FIRE age rather than the config's
        # model start. Build the complete CPI history first: pre-FIRE years use
        # the configured mean, while the stress opening uses its scenario CPI.
        # This supplies the same purchase anchor that the lifecycle resolver
        # uses without pretending the prehistory was a sampled market path.
        prehistory_years = max(0, int(ra) - int(state.start_age))
        raw_pi = (cfg.get("returns") or {}).get("inflation_mu", 0.03)
        mean_pi = 0.03 if raw_pi is None else float(raw_pi)
        synthetic_inflations = (
            [mean_pi] * prehistory_years + list(infl)
        )
        mortgage_events = V98.resolve_housing_mortgage_events(
            kw.get("housing_mortgage"), state.start_age,
            synthetic_inflations)
        stress_life_events = list(kw["life_events"] or ())
        stress_life_events.extend(
            (age, amount) for age, amount in mortgage_events if int(age) > ra
        )
        stress_life_events.sort(key=lambda item: (int(item[0]), float(item[1])))
        # Lock + household ctx: simulate_retirement_v98 reads the process-wide
        # _HOUSEHOLD global, so the backtest must both honor this request's
        # household setting and never interleave with another engine run.
        with _ENGINE_LOCK, _household_ctx(cfg):
            wd = simulate_retirement_v98(
                starting_accounts=start.copy(), starting_age=ra,
                fire_year_cpi_cumulative=1.0,
                equity_returns=eq, bond_returns=bd, inflations=infl,
                rule=kw["rule"], glide_path=kw["glide_path"],
                relocation=RelocationParams(relocation_age=None),
                sh_property=kw["sh_property"], medical=kw["medical"], aca=kw["aca"],
                mortality=mort, roth_ladder=kw["roth_ladder"], ss=kw["ss"],
                ftc=kw["ftc"], eldercare_events=[], inheritance_event=None,
                state=state, tax_us=kw["tax_us"], tax_cn=kw["tax_cn"],
                friction=((cfg.get("returns") or {}).get("friction_retire", 0.005)
                          + float((cfg.get("returns") or {}).get("expense_ratio", 0) or 0)
                          + float((cfg.get("returns") or {}).get("rebalance_cost", 0) or 0)),
                rng=np.random.default_rng(seed),
                china_healthcare=kw["china_healthcare"], ss_nra=kw["ss_nra"],
                life_events=([(a, amt) for a, amt in stress_life_events
                              if a > ra] or None),
                income_streams=kw["income_streams"],
                tax_true=kw["tax_true"],
            )
        synthetic = {"reached_fire": True, "fire_age": ra,
                     "lifetime_success": bool(wd.get("lifetime_success")),
                     "accum_path": [], "withdrawal": wd}
        _annotate_result(synthetic, cfg, stress_life_events)
        ce = _cpi_end(wd)
        tb = wd.get("terminal_balance")
        scen[key] = {
            "label": sc["label"], "start_age": ra,
            "real_cons": wd.get("real_consumption_path") or [],
            "survived": bool(synthetic.get("lifetime_success")),
            "shortfall_age": wd.get("shortfall_age"),
            "event_shortfalls": synthetic.get("event_shortfalls") or [],
            "terminal_real": (tb / ce) if (tb and ce and synthetic.get("lifetime_success")) else 0.0,
            "terminal_after_tax_real": synthetic.get("terminal_after_tax_real", 0.0),
            "true_tax_real": float(wd.get(
                "true_tax_total_real",
                _real_lifetime_total(wd.get("true_tax_total_nominal", 0.0), wd))
                or 0.0),
        }
    return {"retire_age": ra, "target": target, "swr": swr,
            "expenses": exp0, "scenarios": scen}
