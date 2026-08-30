"""
engine_adapter.py — adapter exposing the app's result contract on the authoritative
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
import copy
import dataclasses
import datetime as _dt
import math
import multiprocessing
import numbers
import os
import threading
from enum import Enum
from typing import Optional

import numpy as np

import fire_v8_model
import fire_v9_8_model as V98
from fire_v9_8_model import (
    HousePriceProcess,
    RetainedStockProcess,
    HumanCapitalParams,
    BlockySpendingParams,
    SSTrustFundParams,
    simulate_lifecycle_v98, run_lifecycle_mc_v98,
    simulate_retirement_v98, GuytonKlingerRuleV98,
    IncomeStreamSpec, HousingMortgageSpec, INCOME_STREAM_OWNERS,
)
from fire_v6_model import AccountStack, State, TaxParams, RelocationParams
from fire_v7_model import TaxParamsChina, V7Config, Regime, REGIMES
from fire_v8_model import (PromotionParams, V8ContributionParams, HouseholdParams,
                           LayoffParams, CareerBreakParams, compile_career_break,
                           LifestyleCreepParams, sample_lifestyle_creep,
                           DisabilityParams, sample_ssdi_entitlement)
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
from ltc_model import LtcParams, DEFAULT_LIFETIME_RISK as LTC_LIFETIME_RISK
import ltc_model as LTC
from parents_model import Parent, ParentsParams
import parents_model as PARENTS
from guaranteed_income import (
    Annuity, TipsLadder, GuaranteedIncomeParams,
    compile_all as _compile_guaranteed,
)
import guaranteed_income as GI
import ssa_import as SSA_IMPORT
import student_debt as STUDENT_DEBT
from fire_tax_true import TrueTaxParams
from fire_tax_true import ORD_SINGLE as TRUE_ORD_SINGLE
from funded_ratio import FundedRatioParams
from fire_rule_pack import US_STATE_ARCHETYPES
from fire_rule_pack import (
    rule_pack_for_run as _rule_pack_for_run,
    rule_pack_reference_defaults as _rule_pack_reference_defaults,
    PLAN_SHAPE_RULES, ESPP_RULES,
)
from fire_v95_actual_baseline import INITIAL_STACK_ACTUAL, match_excludes_bonus

ENGINE_VERSION = "v9.8-rc"
LIFESTYLE_CREEP_SEED_OFFSET = 17_000_000
DISABILITY_SEED_OFFSET = 19_000_000


class ConfigIncomplete(ValueError):
    """A plan this adapter will not map, raised while mapping it.

    The distinction that matters is WHEN, not whether. Refusing to invent a
    parameter is right — see the LTC lifetime-risk message, which says why
    guessing one would move every number in the run. Refusing partway through
    the engine is not: the user is shown a run that failed, when the true
    report is that their plan is missing a setting. So the refusal lives here,
    in the mapping, where nothing has been drawn yet, and carries the field to
    fix so the seam can name it.

    `field` is a config path (`mortality.sex`), not prose — the seam puts it in
    the response so a client can point at the control instead of parsing English.
    """

    #: `config_incomplete` = a setting the plan never states. `config_invalid`
    #: = one it states in a shape this adapter cannot read. Different repairs,
    #: so different codes; a client that only knows one of them still gets the
    #: message and the field.
    code = "config_incomplete"

    def __init__(self, message: str, *, field: str, code: str = None):
        super().__init__(message)
        self.field = field
        if code is not None:
            self.code = code


def check_config(cfg: dict) -> None:
    """Raise what a run of `cfg` would raise, before the run exists.

    Deliberately the real `build_kwargs` and not a second checker that answers
    the same question: a parallel validator agrees with the adapter only until
    one of them changes, and this repo has already paid once for a binding that
    described an engine surface that had moved (the attribution inventory).
    Mapping is pure and cheap — dataclass construction, no draws — so running it
    twice costs a run nothing and buys the refusal a place to happen where the
    caller can still be told what to fix.

    Both postures are mapped when relocation is on, because a plan can be
    complete for the home leg and incomplete for the other one.
    """
    _validate_medical_premium_anchor(cfg)
    _validate_annual_medical_trajectory(cfg)
    _validate_eol_peak(cfg)
    _validate_ss_trust_fund(cfg)
    _validate_dividend_drag(cfg)
    _validate_state_archetype(cfg)
    _validate_funded_ratio(cfg)
    _validate_career_break(cfg)
    _validate_layoff(cfg)
    _validate_savings_mode(cfg)
    _validate_tax_model(cfg)
    _validate_accumulation_state_rate(cfg)
    _validate_employment_type(cfg)
    _validate_workplace_plan(cfg)
    _validate_hsa_eligibility(cfg)
    _validate_temporary_expenses(cfg)
    _validate_variable_income(cfg)
    _validate_spouse_variable_income(cfg)
    _validate_spouse_promotion(cfg)
    _validate_second_promotions(cfg)
    _validate_rsu_vest(cfg)
    _validate_rsu_retained(cfg)
    _validate_espp(cfg)
    _validate_gov_457b(cfg)
    _validate_ssa_basis(cfg)
    _validate_student_debt(cfg)
    _validate_lifestyle_creep(cfg)
    _validate_disability(cfg)
    build_kwargs(cfg, False)
    if bool((cfg.get("relocation") or {}).get("enabled", False)):
        build_kwargs(cfg, True)


def _validate_ssa_basis(cfg: dict) -> None:
    """Refuse a malformed or stale exact-replay basis before any draws.

    The stored numbers were indexed with one AWI table vintage.  Replaying
    them under a different vintage would still produce a plausible number,
    but no longer the exact top-35 calculation the UI promises.  The repair
    is to re-import the local statement, not to guess how to migrate a
    sufficient statistic that deliberately carries no calendar years.
    """
    basis = (cfg.get("social_security") or {}).get("ssa_basis_v1")
    if basis is None:
        return
    try:
        SSA_IMPORT.estimate_pia_from_basis(basis, [])
    except (TypeError, ValueError) as exc:
        raise ConfigIncomplete(
            "social_security.ssa_basis_v1 is invalid; re-import the SSA "
            "statement on this device",
            field="social_security.ssa_basis_v1",
            code="config_invalid") from exc
    if basis.get("awi_vintage") != SSA_IMPORT.AWI_MAX_YEAR:
        raise ConfigIncomplete(
            "social_security.ssa_basis_v1 uses an older AWI table; re-import "
            "the SSA statement on this device",
            field="social_security.ssa_basis_v1",
            code="config_invalid")
    first_simulated_year = int(basis["birth_year"]) + int(
        (cfg.get("state") or {}).get("start_age", State().start_age))
    if first_simulated_year < int(basis["projection_start_year"]):
        raise ConfigIncomplete(
            "state.start_age begins before the imported SSA record ends; "
            "re-import the statement for this plan age",
            field="state.start_age", code="config_invalid")


def _ssa_pia_resolver(ss_block: dict):
    """Compile a stored SSA basis into the lifecycle engine's local seam."""
    basis = (ss_block or {}).get("ssa_basis_v1")
    if basis is None:
        return None
    birth_year = int(basis["birth_year"])

    def resolve(age_amounts):
        rows = [
            {
                "age": int(age),
                "year": birth_year + int(age),
                "covered_earnings_nominal": float(amount),
            }
            for age, amount in age_amounts
        ]
        calc = SSA_IMPORT.estimate_pia_from_basis(
            basis,
            [(row["year"], row["covered_earnings_nominal"])
             for row in rows],
        )
        return {
            "basis_schema": 1,
            "aime_monthly": calc["aime_monthly"],
            "pia_monthly_y0": calc["pia_monthly"],
            "future_years_used": calc["future_years_used"],
            "future_earnings": rows,
        }

    return resolve


def _validate_gov_457b(cfg: dict) -> None:
    """Refuse a 457(b) contribution the law does not allow.

    OPEN_ITEMS E37. Unlike its neighbours this leaf IS held to statute, and
    the reason is that the statute is the only thing making it a separate
    account: a governmental 457(b) has its own section 457(e)(15) limit, not
    a share of the 401(k)'s, and a stated figure above it describes a year
    that cannot happen. Refusing beats capping -- a silently capped number is
    a control the adapter dropped, which from outside looks exactly like one
    it honoured.

    The catch-up is NOT added to the ceiling, matching the engine: a
    governmental 457(b) may allow the age-50 catch-up, but whether a person in
    both plans gets one in EACH is not stated on any first-party IRS page this
    was checked against, so the model under-credits rather than invents room.
    `limitations` says so, and this refusal names the same figure the
    disclosure does.
    """
    block = cfg.get("contributions") or {}
    stated = block.get("gov_457b_y1", 0.0)
    if stated is None:
        return
    if not isinstance(stated, numbers.Real) or isinstance(stated, bool) \
            or not math.isfinite(float(stated)):
        raise ConfigIncomplete(
            "contributions.gov_457b_y1 must be a number",
            field="contributions.gov_457b_y1", code="config_invalid")
    stated = float(stated)
    ceiling = float(PLAN_SHAPE_RULES["deferral_limit_457b_governmental"])
    if stated < 0.0 or stated > ceiling:
        raise ConfigIncomplete(
            "contributions.gov_457b_y1 is what you actually defer into a "
            "governmental 457(b) in year one, and section 457(e)(15) caps it "
            "at $%s for %d. It is a SEPARATE limit from your 401(k)/403(b) "
            "one -- you may fill both -- but neither may be exceeded."
            % (format(int(ceiling), ","), 2026),
            field="contributions.gov_457b_y1", code="config_invalid")


def _validate_employment_type(cfg: dict) -> None:
    """Refuse an employment type the engine does not have, and refuse the one
    combination it cannot honour.

    A self-employed person with an employer match rate is not a typo -- it is
    a Solo 401(k) or SEP owner, and they are RIGHT that employer money should
    reach them. This engine cannot give it to them yet: the employer term is
    `min(employee_deferral, ceiling)`, so employer money can never exceed the
    deferral, and a SEP is employer-only. Modelling that as a match would put
    the contribution in the wrong place AND miss the 415(c) cap that employer
    contributions are what makes bind.

    So the refusal says which plan shapes are missing rather than pretending
    the input is malformed. Silently zeroing the rate would be worse: a
    control the adapter drops looks identical, from outside, to one it honours.
    """
    block = cfg.get("contributions") or {}
    kind = block.get("employment_type", "w2")
    if kind not in ("w2", "self_employed"):
        raise ConfigIncomplete(
            "contributions.employment_type must be 'w2' (an employer withholds "
            "your payroll tax) or 'self_employed' (you pay both halves)",
            field="contributions.employment_type", code="config_invalid")
    if kind != "self_employed":
        return
    rate = block.get("match_rate", 0.0)
    if isinstance(rate, numbers.Real) and not isinstance(rate, bool) \
            and math.isfinite(float(rate)) and float(rate) > 0.0:
        raise ConfigIncomplete(
            "contributions.match_rate must be 0 when employment_type is "
            "'self_employed'. Employer contributions for the self-employed -- "
            "a Solo 401(k) employer piece, or a SEP -- are not modelled yet, "
            "and they are not a match: they do not depend on what you defer. "
            "Modelling them as a match would put the money in the wrong place "
            "and skip the annual additions cap",
            field="contributions.match_rate", code="config_invalid")


def _validate_workplace_plan(cfg: dict) -> None:
    """Bind SIMPLE's user-stated contribution to its own statutory ceiling.

    The contribution fields mean what the person plans to put in, while the
    dated pack supplies the legal room. Refusing an impossible plan is better
    than silently clipping the user's stated amount and reporting a different
    plan. The engine still caps as a defence for direct callers.
    """
    rows = [
        ("contributions", cfg.get("contributions") or {},
         "workplace_plan_type", "simple_higher_limit",
         "pretax_401k_limit_y1"),
    ]
    household = cfg.get("household") or {}
    if household.get("enabled"):
        rows.append((
            "household", household, "spouse_workplace_plan_type",
            "spouse_simple_higher_limit", "spouse_pretax_401k_limit_y1"))
    for prefix, block, kind_name, higher_name, amount_name in rows:
        kind = block.get(kind_name, "standard")
        field = "%s.%s" % (prefix, kind_name)
        if kind not in ("standard", "403b", "simple"):
            raise ConfigIncomplete(
                "%s must be 'standard', '403b', or 'simple'" % field,
                field=field, code="config_invalid")
        if kind != "simple":
            continue
        amount = block.get(amount_name, 0.0)
        amount_field = "%s.%s" % (prefix, amount_name)
        if (not isinstance(amount, numbers.Real) or isinstance(amount, bool)
                or not math.isfinite(float(amount)) or float(amount) < 0.0):
            raise ConfigIncomplete(
                "%s must be a finite non-negative number" % amount_field,
                field=amount_field, code="config_invalid")
        ceiling = float(PLAN_SHAPE_RULES[
            "simple_deferral_limit_small_employer"
            if block.get(higher_name, False) else "simple_deferral_limit"])
        if float(amount) > ceiling:
            raise ConfigIncomplete(
                "%s is the planned year-one SIMPLE deferral and cannot exceed "
                "the 2026 SIMPLE base limit of $%s. Age-based SIMPLE catch-up "
                "room is added separately by the engine."
                % (amount_field, format(int(ceiling), ",")),
                field=amount_field, code="config_invalid")

    catchup_rows = [
        ("contributions", cfg.get("contributions") or {},
         "workplace_plan_type", "catchup_403b_15yr_enabled",
         "catchup_403b_15yr_schedule_nominal",
         "catchup_403b_15yr_prior_used_nominal"),
    ]
    if household.get("enabled"):
        catchup_rows.append((
            "household", household, "spouse_workplace_plan_type",
            "spouse_catchup_403b_15yr_enabled",
            "spouse_catchup_403b_15yr_schedule_nominal",
            "spouse_catchup_403b_15yr_prior_used_nominal"))
    _annual = float(PLAN_SHAPE_RULES["catchup_403b_15yr_annual"])
    _lifetime = float(PLAN_SHAPE_RULES["catchup_403b_15yr_lifetime"])
    for (prefix, block, kind_name, enabled_name, schedule_name,
         prior_used_name) in catchup_rows:
        if not block.get(enabled_name, False):
            continue
        enabled_field = "%s.%s" % (prefix, enabled_name)
        if block.get(kind_name, "standard") != "403b":
            raise ConfigIncomplete(
                "%s requires %s.%s = '403b'" % (
                    enabled_field, prefix, kind_name),
                field=enabled_field, code="config_invalid")
        schedule = block.get(schedule_name)
        schedule_field = "%s.%s" % (prefix, schedule_name)
        if not isinstance(schedule, (list, tuple)) or not schedule:
            raise ConfigIncomplete(
                "%s must be a non-empty list of NOMINAL annual special "
                "catch-up room from your plan administrator or IRS worksheet"
                % schedule_field,
                field=schedule_field, code="config_invalid")
        total = 0.0
        for index, raw in enumerate(schedule):
            if (not isinstance(raw, numbers.Real) or isinstance(raw, bool)
                    or not math.isfinite(float(raw)) or float(raw) < 0.0
                    or float(raw) > _annual):
                raise ConfigIncomplete(
                    "%s[%d] must be between $0 and the statutory annual "
                    "special catch-up ceiling of $%s" % (
                        schedule_field, index, format(int(_annual), ",")),
                    field=schedule_field, code="config_invalid")
            total += float(raw)
        prior_used = block.get(prior_used_name)
        prior_field = "%s.%s" % (prefix, prior_used_name)
        if (not isinstance(prior_used, numbers.Real)
                or isinstance(prior_used, bool)
                or not math.isfinite(float(prior_used))
                or not 0.0 <= float(prior_used) <= _lifetime):
            raise ConfigIncomplete(
                "%s must state the finite amount of the $%s lifetime "
                "special catch-up already used before this plan starts; "
                "blank is unmeasured, not zero" % (
                    prior_field, format(int(_lifetime), ",")),
                field=prior_field, code="config_incomplete")
        if total + float(prior_used) > _lifetime:
            raise ConfigIncomplete(
                "%s plus %s totals more than the statutory $%s lifetime "
                "special catch-up ceiling" % (
                    schedule_field, prior_field,
                    format(int(_lifetime), ",")),
                field=schedule_field, code="config_invalid")


def _validate_hsa_eligibility(cfg: dict) -> None:
    """Require enough user facts to support every non-zero HSA contribution."""
    household = cfg.get("household") or {}
    state = cfg.get("state") or {}
    start_age = int(state.get("start_age", State().start_age))
    rows = [
        ("contributions", cfg.get("contributions") or {}, "hsa_limit_y1",
         "hsa_coverage_tier", "hsa_deductible_y1",
         "hsa_out_of_pocket_max_y1", "hsa_disqualifying_other_coverage",
         "hsa_medicare_enrolled", "hsa_claimed_as_dependent",
         "hsa_eligible_through_age", start_age),
    ]
    if household.get("enabled"):
        rows.append((
            "household", household, "spouse_hsa_limit_y1",
            "spouse_hsa_coverage_tier", "spouse_hsa_deductible_y1",
            "spouse_hsa_out_of_pocket_max_y1",
            "spouse_hsa_disqualifying_other_coverage",
            "spouse_hsa_medicare_enrolled",
            "spouse_hsa_claimed_as_dependent",
            "spouse_hsa_eligible_through_age",
            start_age + int(household.get("spouse_age_offset", 0) or 0)))
    active = []
    for (prefix, block, amount_name, tier_name, deductible_name, oop_name,
         other_name, medicare_name, dependent_name, through_name, age) in rows:
        amount = block.get(amount_name, 0.0)
        amount_field = "%s.%s" % (prefix, amount_name)
        if (not isinstance(amount, numbers.Real) or isinstance(amount, bool)
                or not math.isfinite(float(amount)) or float(amount) < 0.0):
            raise ConfigIncomplete(
                "%s must be a finite non-negative number" % amount_field,
                field=amount_field, code="config_invalid")
        if float(amount) == 0.0:
            continue
        tier = block.get(tier_name, "none")
        tier_field = "%s.%s" % (prefix, tier_name)
        if tier not in ("self_only", "family"):
            raise ConfigIncomplete(
                "%s must be 'self_only' or 'family' for a non-zero HSA "
                "contribution" % tier_field,
                field=tier_field, code="config_incomplete")
        suffix = "family" if tier == "family" else "self_only"
        deductible = block.get(deductible_name)
        deductible_field = "%s.%s" % (prefix, deductible_name)
        minimum = float(PLAN_SHAPE_RULES[
            "hdhp_min_deductible_" + suffix])
        if (not isinstance(deductible, numbers.Real)
                or isinstance(deductible, bool)
                or not math.isfinite(float(deductible))
                or float(deductible) < minimum):
            raise ConfigIncomplete(
                "%s must be at least the 2026 HDHP minimum deductible of "
                "$%s for %s coverage" % (
                    deductible_field, format(int(minimum), ","), tier),
                field=deductible_field, code="config_incomplete")
        oop = block.get(oop_name)
        oop_field = "%s.%s" % (prefix, oop_name)
        maximum = float(PLAN_SHAPE_RULES[
            "hdhp_max_out_of_pocket_" + suffix])
        if (not isinstance(oop, numbers.Real) or isinstance(oop, bool)
                or not math.isfinite(float(oop))
                or not float(deductible) <= float(oop) <= maximum):
            raise ConfigIncomplete(
                "%s must be between the deductible and the 2026 HDHP "
                "out-of-pocket ceiling of $%s" % (
                    oop_field, format(int(maximum), ",")),
                field=oop_field, code="config_incomplete")
        for name, label in ((other_name, "disqualifying other coverage"),
                            (medicare_name, "Medicare enrollment"),
                            (dependent_name, "dependent status")):
            field = "%s.%s" % (prefix, name)
            value = block.get(name)
            if value is not False:
                raise ConfigIncomplete(
                    "%s must be explicitly false: %s prevents HSA "
                    "eligibility, and blank is unmeasured" % (field, label),
                    field=field, code="config_incomplete")
        through = block.get(through_name)
        through_field = "%s.%s" % (prefix, through_name)
        if (isinstance(through, bool) or not isinstance(through, numbers.Real)
                or not math.isfinite(float(through))
                or int(through) != float(through) or int(through) < age):
            raise ConfigIncomplete(
                "%s must be a whole age at or after the plan's starting age; "
                "contributions stop after that age rather than assuming "
                "HDHP eligibility forever" % through_field,
                field=through_field, code="config_incomplete")
        base = float(PLAN_SHAPE_RULES["hsa_limit_" + suffix])
        catchup = (float(PLAN_SHAPE_RULES["hsa_catch_up_amount"])
                   if age >= PLAN_SHAPE_RULES["hsa_catch_up_age"] else 0.0)
        if float(amount) > base + catchup:
            raise ConfigIncomplete(
                "%s exceeds the 2026 %s HSA limit plus any age-55 catch-up"
                % (amount_field, tier),
                field=amount_field, code="config_invalid")
        active.append((tier, float(amount), age))
    if len(active) == 2 and any(row[0] == "family" for row in active):
        family_cap = float(PLAN_SHAPE_RULES["hsa_limit_family"])
        family_cap += sum(float(PLAN_SHAPE_RULES["hsa_catch_up_amount"])
                          for _, _, age in active
                          if age >= PLAN_SHAPE_RULES["hsa_catch_up_age"])
        if sum(row[1] for row in active) > family_cap:
            raise ConfigIncomplete(
                "the spouses' combined year-one HSA contributions exceed "
                "the shared family limit plus their individual age-55 "
                "catch-ups", field="household.spouse_hsa_limit_y1",
                code="config_invalid")


def _validate_temporary_expenses(cfg: dict) -> None:
    block = cfg.get("contributions") or {}
    horizon = int((cfg.get("state") or {}).get(
        "accum_years", State().accum_years))
    for name in ("childcare_schedule_real", "commuting_schedule_real"):
        schedule = block.get(name, ())
        field = "contributions.%s" % name
        if not isinstance(schedule, (list, tuple)):
            raise ConfigIncomplete(
                "%s must be a list of annual real-dollar costs" % field,
                field=field, code="config_invalid")
        if len(schedule) > horizon:
            raise ConfigIncomplete(
                "%s has more rows than the accumulation horizon" % field,
                field=field, code="config_invalid")
        for index, value in enumerate(schedule):
            if not isinstance(value, numbers.Real) or isinstance(value, bool):
                raise ConfigIncomplete(
                    "%s[%d] must be a finite non-negative real-dollar cost"
                    % (field, index), field=field, code="config_invalid")
            numeric = float(value)
            if not math.isfinite(numeric) or numeric < 0.0:
                raise ConfigIncomplete(
                    "%s[%d] must be a finite non-negative real-dollar cost"
                    % (field, index), field=field, code="config_invalid")


def _validate_variable_income(cfg: dict) -> None:
    """Validate only the active employment contract before any RNG draw."""
    block = cfg.get("contributions") or {}
    kind = block.get("employment_type", "w2")
    bonus_mode = block.get("bonus_mode_pre", "fixed_amount")
    profit_mode = block.get("self_employed_profit_mode", "fixed")
    if bonus_mode not in ("fixed_amount", "uniform_pct"):
        raise ConfigIncomplete(
            "contributions.bonus_mode_pre must be 'fixed_amount' or "
            "'uniform_pct'",
            field="contributions.bonus_mode_pre", code="config_invalid")
    if profit_mode not in ("fixed", "uniform"):
        raise ConfigIncomplete(
            "contributions.self_employed_profit_mode must be 'fixed' or "
            "'uniform'",
            field="contributions.self_employed_profit_mode",
            code="config_invalid")
    if kind == "w2" and profit_mode != "fixed":
        raise ConfigIncomplete(
            "self-employed profit variability cannot be enabled for a W-2 "
            "plan; use bonus_mode_pre for W-2 variable compensation",
            field="contributions.self_employed_profit_mode",
            code="config_invalid")
    if kind == "self_employed" and bonus_mode != "fixed_amount":
        raise ConfigIncomplete(
            "W-2 bonus variability cannot be enabled for a self-employed "
            "plan; use self_employed_profit_mode for the whole net profit",
            field="contributions.bonus_mode_pre", code="config_invalid")

    def _bounds(lo_name: str, hi_name: str) -> None:
        raw_lo = block.get(lo_name)
        raw_hi = block.get(hi_name)
        for name, raw in ((lo_name, raw_lo), (hi_name, raw_hi)):
            if (not isinstance(raw, numbers.Real) or isinstance(raw, bool)
                    or not math.isfinite(float(raw)) or float(raw) < 0.0):
                raise ConfigIncomplete(
                    "contributions.%s must be a finite non-negative number; "
                    "negative business profit/loss is not modelled in this "
                    "slice" % name,
                    field="contributions.%s" % name, code="config_invalid")
        if float(raw_lo) > float(raw_hi):
            raise ConfigIncomplete(
                "contributions.%s must be at or below contributions.%s"
                % (lo_name, hi_name),
                field="contributions.%s" % hi_name, code="config_invalid")

    if kind == "w2" and bonus_mode == "uniform_pct":
        _bounds("bonus_pct_min_pre", "bonus_pct_max_pre")
    if kind == "self_employed" and profit_mode == "uniform":
        _bounds("self_employed_profit_factor_min",
                "self_employed_profit_factor_max")


def _validate_rsu_vest(cfg: dict) -> None:
    """Validate the vest schedule before it can reach one earned-income year.

    Four refusals rather than silent coercion (lesson 3). The fourth exists
    because this repository has already paid for the other shape once
    (lesson 23b): `income_streams.equity_*` books equity compensation as
    AFTER-TAX cash, this books it as PRE-TAX wages, and a plan with both on
    holds two contradictory statements about the same grants.
    """
    block = cfg.get("contributions") or {}
    if not block.get("rsu_vest_enabled"):
        return
    if block.get("employment_type", "w2") != "w2":
        raise ConfigIncomplete(
            "contributions.rsu_vest_enabled requires employment_type 'w2'. "
            "RSU vesting is ordinary W-2 wages; applying it to a "
            "self-employed plan would run it through SECA, which is the "
            "wrong tax base rather than an approximation",
            field="contributions.rsu_vest_enabled", code="config_invalid")
    if (cfg.get("income_streams") or {}).get("equity_enabled"):
        raise ConfigIncomplete(
            "contributions.rsu_vest_enabled and income_streams.equity_enabled "
            "cannot both be on: they are two different statements about the "
            "same equity compensation -- one after-tax cash credited to "
            "taxable, one pre-tax wages that pay income tax, FICA and build "
            "Social Security covered earnings. Keep the one you mean",
            field="contributions.rsu_vest_enabled", code="config_invalid")
    schedule = block.get("rsu_vest_schedule_real")
    if not isinstance(schedule, (list, tuple)) or not schedule:
        raise ConfigIncomplete(
            "contributions.rsu_vest_schedule_real must be a non-empty list of "
            "annual vest values in today's dollars, index 0 = the first "
            "accumulation year. An enabled schedule with nothing in it is not "
            "a zero vest, it is an unanswered question",
            field="contributions.rsu_vest_schedule_real",
            code="config_invalid")
    for index, raw in enumerate(schedule):
        if (not isinstance(raw, numbers.Real) or isinstance(raw, bool)
                or not math.isfinite(float(raw)) or float(raw) < 0.0):
            raise ConfigIncomplete(
                "contributions.rsu_vest_schedule_real[%d] must be a finite "
                "non-negative number; a negative vest is not modelled "
                "(forfeiture and clawback are out of scope for this slice)"
                % index,
                field="contributions.rsu_vest_schedule_real",
                code="config_invalid")


def _validate_rsu_retained(cfg: dict) -> None:
    """Refuse a concentration the vest schedule cannot support."""
    block = cfg.get("contributions") or {}
    if not block.get("rsu_retained_enabled"):
        return
    if not block.get("rsu_vest_enabled"):
        raise ConfigIncomplete(
            "contributions.rsu_retained_enabled requires "
            "contributions.rsu_vest_enabled: you can only keep shares that "
            "vested, and without the vest schedule there is nothing to "
            "measure the retained amount against",
            field="contributions.rsu_retained_enabled", code="config_invalid")
    kept = block.get("rsu_retained_schedule_real")
    if not isinstance(kept, (list, tuple)) or not kept:
        raise ConfigIncomplete(
            "contributions.rsu_retained_schedule_real must be a non-empty "
            "list, in today's dollars, of how much of each year's vest you "
            "kept in shares instead of selling",
            field="contributions.rsu_retained_schedule_real",
            code="config_invalid")
    vest = list(block.get("rsu_vest_schedule_real") or ())
    for index, raw in enumerate(kept):
        if (not isinstance(raw, numbers.Real) or isinstance(raw, bool)
                or not math.isfinite(float(raw)) or float(raw) < 0.0):
            raise ConfigIncomplete(
                "contributions.rsu_retained_schedule_real[%d] must be a "
                "finite non-negative number" % index,
                field="contributions.rsu_retained_schedule_real",
                code="config_invalid")
        vested = float(vest[index]) if index < len(vest) else 0.0
        if float(raw) > vested:
            raise ConfigIncomplete(
                "contributions.rsu_retained_schedule_real[%d] keeps %.2f but "
                "only %.2f vested that year. Keeping more than vested is not "
                "a concentration, it is a different grant -- add it to the "
                "vest schedule if it is real" % (index, float(raw), vested),
                field="contributions.rsu_retained_schedule_real",
                code="config_invalid")
    if block.get("rsu_retained_sigma_enabled"):
        sigma = block.get("rsu_retained_sigma_real")
        drift = block.get("rsu_retained_drift_real")
        if (not isinstance(sigma, numbers.Real) or isinstance(sigma, bool)
                or not math.isfinite(float(sigma)) or float(sigma) <= 0.0):
            raise ConfigIncomplete(
                "contributions.rsu_retained_sigma_real must be a finite "
                "positive annual real volatility, in your own figures. There "
                "is no default: no index ships a single stock's vol. A zero "
                "would not be a low-risk position, it would be the flat case "
                "with extra machinery",
                field="contributions.rsu_retained_sigma_real",
                code="config_invalid")
        if (not isinstance(drift, numbers.Real) or isinstance(drift, bool)
                or not math.isfinite(float(drift))):
            raise ConfigIncomplete(
                "contributions.rsu_retained_drift_real must be a finite "
                "annual real drift; 0 leaves the median where the flat case "
                "put it and only adds spread",
                field="contributions.rsu_retained_drift_real",
                code="config_invalid")
    multiple = block.get("rsu_retained_value_multiple")
    if (not isinstance(multiple, numbers.Real) or isinstance(multiple, bool)
            or not math.isfinite(float(multiple)) or float(multiple) < 0.0):
        raise ConfigIncomplete(
            "contributions.rsu_retained_value_multiple must be a finite "
            "non-negative number: what the kept shares are worth when you "
            "sell, as a multiple of what you put in, in today's dollars. "
            "1.0 means flat; 0 means the position went to nothing",
            field="contributions.rsu_retained_value_multiple",
            code="config_invalid")
    start_age = int((cfg.get("state") or {}).get("start_age", 30) or 30)
    last_kept_age = start_age + len(kept) - 1
    try:
        sale_age = int(block.get("rsu_retained_sale_age"))
    except (TypeError, ValueError):
        sale_age = None
    if sale_age is None or sale_age <= last_kept_age:
        raise ConfigIncomplete(
            "contributions.rsu_retained_sale_age must be an age after the "
            "last year you kept shares (%d); selling at or before it would "
            "book the sale before the position exists" % last_kept_age,
            field="contributions.rsu_retained_sale_age",
            code="config_invalid")


def _validate_espp(cfg: dict) -> None:
    """Validate the annual §423 offering before any payroll year is built."""
    block = cfg.get("contributions") or {}
    if not block.get("espp_enabled"):
        return
    if block.get("employment_type", "w2") != "w2":
        raise ConfigIncomplete(
            "contributions.espp_enabled requires employment_type 'w2'; a "
            "section 423 employee plan is not self-employment profit",
            field="contributions.espp_enabled", code="config_invalid")
    mode = block.get("espp_disposition_mode", "immediate")
    if mode not in ("immediate", "qualifying_hold"):
        raise ConfigIncomplete(
            "contributions.espp_disposition_mode must be 'immediate' or "
            "'qualifying_hold'",
            field="contributions.espp_disposition_mode",
            code="config_invalid")
    discount = block.get("espp_discount_rate")
    if (not isinstance(discount, numbers.Real) or isinstance(discount, bool)
            or not math.isfinite(float(discount))
            or not 0.0 <= float(discount) <=
            float(ESPP_RULES["max_discount_rate"])):
        raise ConfigIncomplete(
            "contributions.espp_discount_rate must be a finite share from 0 "
            "through the section 423 maximum %.2f"
            % float(ESPP_RULES["max_discount_rate"]),
            field="contributions.espp_discount_rate", code="config_invalid")
    if not isinstance(block.get("espp_lookback_enabled"), bool):
        raise ConfigIncomplete(
            "contributions.espp_lookback_enabled must be true or false",
            field="contributions.espp_lookback_enabled",
            code="config_invalid")

    grants = block.get("espp_grant_fmv_schedule_nominal")
    exercises = block.get("espp_exercise_fmv_schedule_nominal")
    for field, schedule in (
            ("espp_grant_fmv_schedule_nominal", grants),
            ("espp_exercise_fmv_schedule_nominal", exercises)):
        if not isinstance(schedule, (list, tuple)) or not schedule:
            raise ConfigIncomplete(
                "contributions.%s must be a non-empty annual list of "
                "nominal-dollar Form 3922 / offering facts" % field,
                field="contributions." + field, code="config_invalid")
    if len(grants) != len(exercises):
        raise ConfigIncomplete(
            "contributions.espp_grant_fmv_schedule_nominal and "
            "contributions.espp_exercise_fmv_schedule_nominal must have the "
            "same number of annual lots",
            field="contributions.espp_exercise_fmv_schedule_nominal",
            code="config_invalid")
    accum_years = int((cfg.get("state") or {}).get("accum_years", 0) or 0)
    if len(grants) > accum_years:
        raise ConfigIncomplete(
            "contributions.espp_grant_fmv_schedule_nominal has %d rows but "
            "the plan has only %d accumulation years"
            % (len(grants), accum_years),
            field="contributions.espp_grant_fmv_schedule_nominal",
            code="config_invalid")
    any_lot = False
    for index, (raw_grant, raw_exercise) in enumerate(zip(grants, exercises)):
        for field, raw in (("espp_grant_fmv_schedule_nominal", raw_grant),
                           ("espp_exercise_fmv_schedule_nominal", raw_exercise)):
            if (not isinstance(raw, numbers.Real) or isinstance(raw, bool)
                    or not math.isfinite(float(raw)) or float(raw) < 0.0):
                raise ConfigIncomplete(
                    "contributions.%s[%d] must be a finite non-negative "
                    "nominal-dollar amount" % (field, index),
                    field="contributions." + field, code="config_invalid")
        grant = float(raw_grant)
        exercise = float(raw_exercise)
        if grant > float(ESPP_RULES["annual_grant_fmv_limit"]):
            raise ConfigIncomplete(
                "contributions.espp_grant_fmv_schedule_nominal[%d] is %.2f, "
                "above the section 423 annual grant-date FMV limit %.2f"
                % (index, grant,
                   float(ESPP_RULES["annual_grant_fmv_limit"])),
                field="contributions.espp_grant_fmv_schedule_nominal",
                code="config_invalid")
        if (grant == 0.0) != (exercise == 0.0):
            raise ConfigIncomplete(
                "ESPP lot %d must state both grant-date and exercise-date FMV "
                "or zero for both" % index,
                field="contributions.espp_exercise_fmv_schedule_nominal",
                code="config_invalid")
        any_lot = any_lot or grant > 0.0
    if not any_lot:
        raise ConfigIncomplete(
            "contributions.espp_grant_fmv_schedule_nominal has no purchase; "
            "turn ESPP off instead of enabling an all-zero unanswered plan",
            field="contributions.espp_grant_fmv_schedule_nominal",
            code="config_invalid")

    if mode != "qualifying_hold":
        return
    sales = block.get("espp_qualifying_sale_value_schedule_nominal")
    if not isinstance(sales, (list, tuple)) or len(sales) != len(grants):
        raise ConfigIncomplete(
            "contributions.espp_qualifying_sale_value_schedule_nominal must "
            "give one nominal-dollar sale value for every annual lot",
            field="contributions.espp_qualifying_sale_value_schedule_nominal",
            code="config_invalid")
    for index, raw in enumerate(sales):
        if (not isinstance(raw, numbers.Real) or isinstance(raw, bool)
                or not math.isfinite(float(raw)) or float(raw) < 0.0):
            raise ConfigIncomplete(
                "contributions.espp_qualifying_sale_value_schedule_nominal"
                "[%d] must be a finite non-negative nominal-dollar amount"
                % index,
                field="contributions.espp_qualifying_sale_value_schedule_nominal",
                code="config_invalid")
    start_age = int((cfg.get("state") or {}).get("start_age", 30) or 30)
    last_lot_index = max(index for index, raw in enumerate(grants)
                         if float(raw) > 0.0)
    minimum_age = start_age + last_lot_index + 2
    end_age = start_age + accum_years - 1
    raw_sale_age = block.get("espp_qualifying_sale_age")
    if (not isinstance(raw_sale_age, numbers.Integral)
            or isinstance(raw_sale_age, bool)
            or not minimum_age <= int(raw_sale_age) <= end_age):
        raise ConfigIncomplete(
            "contributions.espp_qualifying_sale_age must be from %d through "
            "%d: two annual model steps after the last grant/purchase and "
            "still inside accumulation" % (minimum_age, end_age),
            field="contributions.espp_qualifying_sale_age",
            code="config_invalid")


def _validate_spouse_variable_income(cfg: dict) -> None:
    """Validate the spouse's bonus contract before any draw is taken."""
    hh = cfg.get("household") or {}
    mode = hh.get("spouse_bonus_mode_pre", "fixed_amount")
    if mode not in ("fixed_amount", "uniform_pct"):
        raise ConfigIncomplete(
            "household.spouse_bonus_mode_pre must be 'fixed_amount' or "
            "'uniform_pct'. The spouse is modelled as a W-2 earner, so there "
            "is no self-employed profit factor on their side",
            field="household.spouse_bonus_mode_pre", code="config_invalid")
    if mode != "uniform_pct" or not hh.get("enabled"):
        return
    lo = hh.get("spouse_bonus_pct_min_pre")
    hi = hh.get("spouse_bonus_pct_max_pre")
    for name, raw in (("spouse_bonus_pct_min_pre", lo),
                      ("spouse_bonus_pct_max_pre", hi)):
        if (not isinstance(raw, numbers.Real) or isinstance(raw, bool)
                or not math.isfinite(float(raw)) or float(raw) < 0.0):
            raise ConfigIncomplete(
                "household.%s must be a finite non-negative share of the "
                "spouse's base pay; it comes from their compensation plan, "
                "not from an industry band this model does not ship" % name,
                field="household.%s" % name, code="config_invalid")
    if float(lo) > float(hi):
        raise ConfigIncomplete(
            "household.spouse_bonus_pct_min_pre must be at or below "
            "household.spouse_bonus_pct_max_pre",
            field="household.spouse_bonus_pct_max_pre", code="config_invalid")


def _validate_spouse_promotion(cfg: dict) -> None:
    """Refuse an enabled spouse promotion before it can take a draw."""
    block = cfg.get("spouse_promotion") or {}
    if not block.get("enabled") or not (cfg.get("household") or {}).get("enabled"):
        return

    def _finite(name: str, *, positive: bool = False,
                minimum: float | None = None, maximum: float | None = None):
        raw = block.get(name)
        ok = (isinstance(raw, numbers.Real) and not isinstance(raw, bool)
              and math.isfinite(float(raw)))
        if ok and positive:
            ok = float(raw) > 0.0
        if ok and minimum is not None:
            ok = float(raw) >= minimum
        if ok and maximum is not None:
            ok = float(raw) <= maximum
        if not ok:
            raise ConfigIncomplete(
                "spouse_promotion.%s must be a finite %snumber supplied from "
                "the spouse's own compensation plan; this model ships no "
                "industry promotion defaults" %
                (name, "positive " if positive else ""),
                field="spouse_promotion.%s" % name, code="config_invalid")
        return float(raw)

    timing_mode = block.get("timing_mode")
    if timing_mode not in ("fixed", "uniform_int", "never"):
        raise ConfigIncomplete(
            "spouse_promotion.timing_mode must be 'fixed', 'uniform_int' or "
            "'never'", field="spouse_promotion.timing_mode",
            code="config_invalid")
    if timing_mode == "never":
        return
    if timing_mode == "fixed":
        _finite("timing_fixed", minimum=1.0)
    else:
        lo = _finite("timing_min", minimum=1.0)
        hi = _finite("timing_max", minimum=1.0)
        if int(lo) != lo or int(hi) != hi:
            raise ConfigIncomplete(
                "spouse_promotion timing bounds must be whole accumulation "
                "years", field="spouse_promotion.timing_min",
                code="config_invalid")
        if lo > hi:
            raise ConfigIncomplete(
                "spouse_promotion.timing_min must be at or below "
                "spouse_promotion.timing_max",
                field="spouse_promotion.timing_max", code="config_invalid")
    _finite("base_salary_post", positive=True)
    _finite("base_growth_post", minimum=-0.99)
    bonus_mode = block.get("bonus_mode")
    if bonus_mode not in ("fixed", "uniform"):
        raise ConfigIncomplete(
            "spouse_promotion.bonus_mode must be 'fixed' or 'uniform'",
            field="spouse_promotion.bonus_mode", code="config_invalid")
    if bonus_mode == "fixed":
        _finite("bonus_pct_fixed", minimum=0.0)
    else:
        lo = _finite("bonus_pct_min", minimum=0.0)
        hi = _finite("bonus_pct_max", minimum=0.0)
        if lo > hi:
            raise ConfigIncomplete(
                "spouse_promotion.bonus_pct_min must be at or below "
                "spouse_promotion.bonus_pct_max",
                field="spouse_promotion.bonus_pct_max", code="config_invalid")
    if (cfg.get("contributions") or {}).get("tax_model") == "flat":
        _finite("marginal_tax_post", minimum=0.0, maximum=0.99)


def _validate_second_promotions(cfg: dict) -> None:
    """Validate U36's two absolute-year second-promotion contracts."""
    pairs = (
        ("second_promotion", "promotion", False),
        ("spouse_second_promotion", "spouse_promotion", True),
    )
    for block_name, first_name, spouse_side in pairs:
        block = cfg.get(block_name) or {}
        if (not block.get("enabled") or
                (spouse_side and not (cfg.get("household") or {}).get("enabled"))):
            continue

        first = cfg.get(first_name) or (
            {} if spouse_side else dataclasses.asdict(PromotionParams()))
        if not first.get("enabled") or first.get("timing_mode") == "never":
            raise ConfigIncomplete(
                "%s requires an enabled first promotion whose timing is not "
                "'never'" % block_name,
                field="%s.enabled" % block_name, code="config_invalid")

        def _finite(name: str, *, positive: bool = False,
                    minimum: float | None = None,
                    maximum: float | None = None):
            raw = block.get(name)
            ok = (isinstance(raw, numbers.Real) and not isinstance(raw, bool)
                  and math.isfinite(float(raw)))
            if ok and positive:
                ok = float(raw) > 0.0
            if ok and minimum is not None:
                ok = float(raw) >= minimum
            if ok and maximum is not None:
                ok = float(raw) <= maximum
            if not ok:
                raise ConfigIncomplete(
                    "%s.%s must be a finite %snumber supplied from the "
                    "earner's own compensation plan; this model ships no "
                    "industry promotion defaults" %
                    (block_name, name, "positive " if positive else ""),
                    field="%s.%s" % (block_name, name),
                    code="config_invalid")
            return float(raw)

        mode = block.get("timing_mode")
        if mode not in ("fixed", "uniform_int", "never"):
            raise ConfigIncomplete(
                "%s.timing_mode must be 'fixed', 'uniform_int' or 'never'" %
                block_name, field="%s.timing_mode" % block_name,
                code="config_invalid")
        if mode == "never":
            continue
        if mode == "fixed":
            second_earliest = _finite("timing_fixed", minimum=1.0)
            if int(second_earliest) != second_earliest:
                raise ConfigIncomplete(
                    "%s.timing_fixed must be a whole accumulation year" %
                    block_name, field="%s.timing_fixed" % block_name,
                    code="config_invalid")
        else:
            second_earliest = _finite("timing_min", minimum=1.0)
            second_latest = _finite("timing_max", minimum=1.0)
            if (int(second_earliest) != second_earliest or
                    int(second_latest) != second_latest):
                raise ConfigIncomplete(
                    "%s timing bounds must be whole accumulation years" %
                    block_name, field="%s.timing_min" % block_name,
                    code="config_invalid")
            if second_earliest > second_latest:
                raise ConfigIncomplete(
                    "%s.timing_min must be at or below %s.timing_max" %
                    (block_name, block_name),
                    field="%s.timing_max" % block_name,
                    code="config_invalid")

        first_mode = first.get("timing_mode")
        if first_mode == "fixed":
            first_latest = first.get("timing_fixed")
        elif first_mode == "uniform_int":
            first_latest = first.get("timing_max")
        else:
            raise ConfigIncomplete(
                "%s requires the first promotion timing to be fixed or an "
                "integer-year window" % block_name,
                field="%s.enabled" % block_name, code="config_invalid")
        if (not isinstance(first_latest, numbers.Real)
                or isinstance(first_latest, bool)
                or not math.isfinite(float(first_latest))
                or second_earliest <= float(first_latest)):
            raise ConfigIncomplete(
                "%s earliest possible year must be strictly later than the "
                "first promotion's latest possible year" % block_name,
                field=("%s.timing_fixed" % block_name if mode == "fixed"
                       else "%s.timing_min" % block_name),
                code="config_invalid")

        _finite("base_salary_post", positive=True)
        _finite("base_growth_post", minimum=-0.99)
        bonus_mode = block.get("bonus_mode")
        if bonus_mode not in ("fixed", "uniform"):
            raise ConfigIncomplete(
                "%s.bonus_mode must be 'fixed' or 'uniform'" % block_name,
                field="%s.bonus_mode" % block_name, code="config_invalid")
        if bonus_mode == "fixed":
            _finite("bonus_pct_fixed", minimum=0.0)
        else:
            lo = _finite("bonus_pct_min", minimum=0.0)
            hi = _finite("bonus_pct_max", minimum=0.0)
            if lo > hi:
                raise ConfigIncomplete(
                    "%s.bonus_pct_min must be at or below %s.bonus_pct_max" %
                    (block_name, block_name),
                    field="%s.bonus_pct_max" % block_name,
                    code="config_invalid")
        if (cfg.get("contributions") or {}).get("tax_model") == "flat":
            _finite("marginal_tax_post", minimum=0.0, maximum=0.99)


def _validate_accumulation_state_rate(cfg: dict) -> None:
    """Refuse a state rate that would make deferring a dollar PROFITABLE.

    Now that the working years pay state tax, the affordability solve prices a
    deferred dollar at `1 - federal_rate - state_rate`. Above
    `1 - top_federal_rate` that term reaches zero and then goes negative, and
    the solve stops meaning anything -- a plan would be told it can shelter
    unbounded amounts. The bound is derived from the shipped bracket table
    rather than picked, and it can genuinely fire: the control is a percent
    field with no maximum, so a user can type 95.
    """
    block = cfg.get("tax_true") or {}
    if not block.get("enabled"):
        return
    rate = block.get("state_rate", 0.0)
    if isinstance(rate, bool) or not isinstance(rate, numbers.Real) \
            or not math.isfinite(float(rate)):
        raise ConfigIncomplete("tax_true.state_rate must be a number",
                               field="tax_true.state_rate",
                               code="config_invalid")
    ceiling = 1.0 - max(r for _, r in TRUE_ORD_SINGLE)
    if not (0.0 <= float(rate) < ceiling):
        raise ConfigIncomplete(
            "tax_true.state_rate must be at least 0 and below %.0f%% -- above "
            "that, a dollar put into a 401(k) would come back as more cash "
            "than it cost, which no tax system does"
            % (ceiling * 100.0),
            field="tax_true.state_rate", code="config_invalid")


def _validate_tax_model(cfg: dict) -> None:
    """Refuse a tax posture the engine does not have.

    Named refusal for the same reason as the savings mode: a typo silently
    falling back to the schedule would look identical, from outside, to a
    plan that asked for the schedule.
    """
    model = (cfg.get("contributions") or {}).get("tax_model", "schedule")
    if model not in ("schedule", "flat"):
        raise ConfigIncomplete(
            "contributions.tax_model must be 'schedule' (2026 federal "
            "brackets plus FICA, rate follows income) or 'flat' (one rate "
            "you state yourself)",
            field="contributions.tax_model", code="config_invalid")


def _validate_savings_mode(cfg: dict) -> None:
    """Refuse a savings description the waterfall cannot honour.

    Both refusals name the field, because a plan that quietly ignored a
    savings rate the user typed would be indistinguishable from one that
    honoured it -- the shape this repo files under "a control the adapter
    drops looks identical from outside".
    """
    block = cfg.get("contributions") or {}
    mode = block.get("savings_mode", "residual")
    if mode not in ("residual", "savings_rate"):
        raise ConfigIncomplete(
            "contributions.savings_mode must be 'residual' (you state your "
            "spending) or 'savings_rate' (you state what you save)",
            field="contributions.savings_mode", code="config_invalid")
    if mode != "savings_rate":
        return
    rate = block.get("savings_rate")
    if isinstance(rate, bool) or not isinstance(rate, numbers.Real) \
            or not math.isfinite(float(rate)):
        raise ConfigIncomplete(
            "contributions.savings_rate must be a number",
            field="contributions.savings_rate", code="config_invalid")
    if not 0.0 <= float(rate) <= 1.0:
        raise ConfigIncomplete(
            "contributions.savings_rate is the share of gross pay you save, "
            "so it must be between 0 and 1",
            field="contributions.savings_rate", code="config_invalid")


def _student_debt_schedule(cfg: dict):
    raw = cfg.get("student_debt") or {}
    if not isinstance(raw, dict):
        raise ConfigIncomplete(
            "student_debt must be a settings object",
            field="student_debt", code="config_invalid")
    state = cfg.get("state") or {}
    try:
        horizon = int(state.get("accum_years", State().accum_years)) + int(
            state.get("retire_horizon", State().retire_horizon))
        schedule = STUDENT_DEBT.compile_schedule(raw, horizon)
    except STUDENT_DEBT.StudentDebtError as exc:
        raise ConfigIncomplete(
            str(exc), field=exc.field, code="config_invalid") from exc
    except (TypeError, ValueError, OverflowError) as exc:
        raise ConfigIncomplete(
            "student debt needs a valid whole-year model horizon",
            field="state", code="config_invalid") from exc
    if schedule is None:
        return None
    contrib = cfg.get("contributions") or {}
    if str(contrib.get("savings_mode", "residual")) == "savings_rate":
        return schedule
    spending = contrib.get("annual_spending_now")
    if spending is None:
        spending = state.get("expenses_y0", State().expenses_y0)
    if (isinstance(spending, bool)
            or not isinstance(spending, numbers.Real)
            or not math.isfinite(float(spending))):
        # The savings-mode validator owns the general field shape.  Naming it
        # here still matters for direct callers that reach this check first.
        raise ConfigIncomplete(
            "contributions.annual_spending_now must be a finite number",
            field="contributions.annual_spending_now", code="config_invalid")
    embedded = schedule.monthly_payment_nominal * 12.0
    if float(spending) < embedded:
        raise ConfigIncomplete(
            "current annual spending must include at least twelve student "
            "loan payments, because this plan says that payment is already "
            "inside the spending figure",
            field="contributions.annual_spending_now", code="config_invalid")
    return schedule


def _validate_student_debt(cfg: dict) -> None:
    """Refuse a loan that would turn the confirmed expense contract negative."""
    _student_debt_schedule(cfg)


def _validate_lifestyle_creep(cfg: dict) -> None:
    """Refuse ambiguous event windows and invalid distributions pre-draw."""
    raw = cfg.get("lifestyle_creep") or {}
    if not isinstance(raw, dict):
        raise ConfigIncomplete(
            "lifestyle_creep must be a settings object",
            field="lifestyle_creep", code="config_invalid")
    mode = raw.get("mode", "off")
    if mode not in ("off", "fixed", "clipnorm"):
        raise ConfigIncomplete(
            "lifestyle_creep.mode must be 'off', 'fixed', or 'clipnorm'",
            field="lifestyle_creep.mode", code="config_invalid")
    if mode == "off":
        return

    def number(name: str) -> float:
        value = raw.get(name, getattr(LifestyleCreepParams(), name))
        if (isinstance(value, bool) or not isinstance(value, numbers.Real)
                or not math.isfinite(float(value))):
            raise ConfigIncomplete(
                "lifestyle_creep.%s must be a finite number" % name,
                field="lifestyle_creep.%s" % name, code="config_invalid")
        return float(value)

    magnitude, sd, cap = (number("magnitude"), number("sd"), number("cap"))
    if magnitude < 0.0 or sd < 0.0 or cap < 0.0:
        raise ConfigIncomplete(
            "lifestyle creep magnitude, sd, and cap cannot be negative",
            field="lifestyle_creep", code="config_invalid")
    if mode == "fixed" and magnitude > cap:
        raise ConfigIncomplete(
            "fixed lifestyle creep magnitude cannot exceed its cap",
            field="lifestyle_creep.magnitude", code="config_invalid")
    lo, hi = raw.get("year_lo", 2), raw.get("year_hi", 5)
    if (isinstance(lo, bool) or not isinstance(lo, int)
            or isinstance(hi, bool) or not isinstance(hi, int)):
        raise ConfigIncomplete(
            "lifestyle creep event years must be whole years",
            field="lifestyle_creep.year_lo", code="config_invalid")
    horizon = int((cfg.get("state") or {}).get(
        "accum_years", State().accum_years))
    if lo < 2 or hi < lo or hi > horizon:
        raise ConfigIncomplete(
            "lifestyle creep event window must run from year 2 through the "
            "end of accumulation",
            field="lifestyle_creep.year_lo", code="config_invalid")


def _validate_disability(cfg: dict) -> None:
    """Refuse an ambiguous SSDI stress before its first incidence draw."""
    raw = cfg.get("disability") or {}
    if not isinstance(raw, dict):
        raise ConfigIncomplete(
            "disability must be a settings object",
            field="disability", code="config_invalid")
    if not raw.get("enabled"):
        return
    sex = (cfg.get("mortality") or {}).get("sex")
    if sex not in ("male", "female"):
        raise ConfigIncomplete(
            "disability incidence needs mortality.sex to be 'male' or "
            "'female'; SSA publishes separate award rates for those rows",
            field="mortality.sex", code="config_invalid")
    for name in ("ssdi_monthly_real", "ltd_monthly_real",
                 "medical_premium_annual_real"):
        value = raw.get(name, getattr(DisabilityParams(), name))
        if (isinstance(value, bool) or not isinstance(value, numbers.Real)
                or not math.isfinite(float(value)) or float(value) < 0.0):
            raise ConfigIncomplete(
                "disability.%s must be a finite non-negative number" % name,
                field="disability.%s" % name, code="config_invalid")


def _validate_career_break(cfg: dict) -> None:
    """Refuse a career break the accumulation loop cannot honour.

    Every one of these is refused rather than clamped, because each of them
    is a plan the user could actually mean and the engine would answer a
    DIFFERENT question than the one asked. A break that runs past the last
    working year is the sharp case: the loop would simply stop, the tail of
    the break would evaporate, and the run would succeed while modelling a
    shorter break than the one that was typed -- the silent-discard shape this
    repo has already paid for with `life_events`.

    Off => nothing here fires, so an untouched plan cannot be refused by a
    module it never asked for.
    """
    brk = cfg.get("career_break") or {}
    if not isinstance(brk, dict) or not brk.get("enabled"):
        return

    def _num(key):
        v = brk.get(key)
        if isinstance(v, bool) or not isinstance(v, numbers.Real):
            raise ConfigIncomplete(
                f"career_break.{key} must be a number",
                field=f"career_break.{key}", code="config_invalid")
        v = float(v)
        if not math.isfinite(v):
            raise ConfigIncomplete(
                f"career_break.{key} must be finite",
                field=f"career_break.{key}", code="config_invalid")
        return v

    years = _num("years")
    if years != int(years) or int(years) < 1:
        raise ConfigIncomplete(
            "career_break.years must be a whole number of years, at least 1",
            field="career_break.years", code="config_invalid")
    years = int(years)

    fraction = _num("income_fraction")
    if not 0.0 <= fraction <= 1.0:
        raise ConfigIncomplete(
            "career_break.income_fraction is the share of your usual pay you "
            "still earn while away, so it must be between 0 and 1",
            field="career_break.income_fraction", code="config_invalid")

    factor = _num("return_wage_factor")
    if not 0.0 < factor <= 1.0:
        # Above 1.0 is refused because the control is a DISCOUNT. Accepting a
        # raise here would quietly turn a field the page labels "return-to-work
        # pay cut" into a general wage multiplier, and the two need different
        # words next to them before either is offered.
        raise ConfigIncomplete(
            "career_break.return_wage_factor is the share of your would-be pay "
            "you return on, so it must be above 0 and at most 1",
            field="career_break.return_wage_factor", code="config_invalid")

    premium_raw = brk.get(
        "medical_premium_annual_real",
        CareerBreakParams().medical_premium_annual_real)
    if (isinstance(premium_raw, bool)
            or not isinstance(premium_raw, numbers.Real)
            or not math.isfinite(float(premium_raw))):
        raise ConfigIncomplete(
            "career_break.medical_premium_annual_real must be a finite number",
            field="career_break.medical_premium_annual_real",
            code="config_invalid")
    premium = float(premium_raw)
    if premium < 0.0:
        raise ConfigIncomplete(
            "career_break.medical_premium_annual_real must be a finite "
            "non-negative annual household amount",
            field="career_break.medical_premium_annual_real",
            code="config_invalid")

    st = cfg.get("state") or {}
    start_age = _num("start_age")
    if start_age != int(start_age):
        raise ConfigIncomplete(
            "career_break.start_age must be a whole age",
            field="career_break.start_age", code="config_invalid")
    start_age = int(start_age)
    plan_start = int(st.get("start_age", 27) or 27)
    accum_years = int(st.get("accum_years", 25) or 25)

    first_year = start_age - plan_start + 1
    if first_year < 1:
        raise ConfigIncomplete(
            f"career_break.start_age {start_age} is before this plan begins "
            f"at age {plan_start}",
            field="career_break.start_age", code="config_invalid")
    last_year = first_year + years - 1
    if last_year > accum_years:
        raise ConfigIncomplete(
            f"a {years}-year break from age {start_age} runs to age "
            f"{start_age + years - 1}, past the last working year "
            f"(age {plan_start + accum_years - 1}); shorten it or start it "
            f"earlier",
            field="career_break.years", code="config_invalid")


def _validate_layoff(cfg: dict) -> None:
    """Refuse a gap or health-cost input the annual model would truncate."""
    block = cfg.get("layoff") or {}
    if not isinstance(block, dict) or not block.get("enabled"):
        return

    def number(name: str) -> float:
        value = block.get(name, getattr(LayoffParams(), name))
        if (isinstance(value, bool) or not isinstance(value, numbers.Real)
                or not math.isfinite(float(value))):
            raise ConfigIncomplete(
                "layoff.%s must be a finite number" % name,
                field="layoff.%s" % name, code="config_invalid")
        return float(value)

    premium = number("medical_premium_monthly_real")
    gap = number("gap_months")
    decay = number("gap_months_per_year_of_age")
    cap = number("max_gap_months")
    if premium < 0.0:
        raise ConfigIncomplete(
            "layoff.medical_premium_monthly_real must be a finite "
            "non-negative monthly household amount",
            field="layoff.medical_premium_monthly_real",
            code="config_invalid")
    if not 0.0 <= gap <= 12.0:
        raise ConfigIncomplete(
            "layoff.gap_months must be between 0 and 12",
            field="layoff.gap_months", code="config_invalid")
    if decay < 0.0:
        raise ConfigIncomplete(
            "layoff.gap_months_per_year_of_age cannot be negative",
            field="layoff.gap_months_per_year_of_age", code="config_invalid")
    if not 0.0 <= cap <= 12.0:
        raise ConfigIncomplete(
            "layoff.max_gap_months must be between 0 and 12",
            field="layoff.max_gap_months", code="config_invalid")
    if gap > cap:
        raise ConfigIncomplete(
            "layoff.gap_months cannot exceed layoff.max_gap_months",
            field="layoff.gap_months", code="config_invalid")


def _validate_medical_premium_anchor(cfg: dict) -> None:
    """Validate the user-facing ACA anchor at the pre-job boundary.

    Internal engine fixtures intentionally use a zero premium to isolate other
    mechanics, so this belongs in ``check_config`` -- which every HTTP job calls
    before minting an id -- rather than in the lower-level mapper those fixtures
    use directly.
    """
    medical_d = cfg.get("medical", {}) or {}
    if not isinstance(medical_d, dict):
        raise ConfigIncomplete(
            "medical.premium_aca must be supplied inside a medical object",
            field="medical.premium_aca",
            code="config_invalid",
        )
    if "premium_aca" not in medical_d:
        return
    premium_aca = medical_d["premium_aca"]
    if (isinstance(premium_aca, bool)
            or not isinstance(premium_aca, numbers.Real)
            or not math.isfinite(float(premium_aca))
            or float(premium_aca) <= 0):
        raise ConfigIncomplete(
            "medical.premium_aca must be a positive finite annual "
            "household-total premium",
            field="medical.premium_aca",
            code="config_invalid",
        )


def _validate_ss_trust_fund(cfg: dict) -> None:
    """The calendar anchor the trust fund module cannot run without.

    The engine already refuses -- it raises rather than guessing a year -- but
    it raises DURING the run, which for a background job means the user waits
    for a computation that was never going to finish and is then told it died.
    That is the same report this repository has already shipped once (the
    decision panel, refusing after the job id). So the refusal moves here,
    where the caller can still be told which box to fill.

    Only checked when the module is on. A blank year with the module off is
    not an incomplete plan; it is a plan that does not use this.
    """
    block = cfg.get("ss_trust_fund") or {}
    if not isinstance(block, dict) or not block.get("enabled"):
        return
    year = block.get("plan_start_year")
    if (year is None or isinstance(year, bool)
            or not isinstance(year, numbers.Real)
            or not math.isfinite(float(year))
            or not (1900 <= int(year) <= 2200)):
        raise ConfigIncomplete(
            "ss_trust_fund.plan_start_year must be the calendar year this "
            "plan's year zero represents (usually the current year). Reserve "
            "depletion is a calendar event and this engine works in ages, so "
            "the year is not guessed: a fixed default would mis-time a "
            "federal event by however far your plan is offset, and reading "
            "today's date would make the same plan answer differently in "
            "different years",
            field="ss_trust_fund.plan_start_year",
            code="config_incomplete",
        )
    scenario = block.get("scenario")
    if scenario not in (None, "intermediate", "range"):
        raise ConfigIncomplete(
            "ss_trust_fund.scenario must be 'intermediate' or 'range' -- "
            "these are the Trustees Report's own alternatives, and this app "
            "does not invent a third",
            field="ss_trust_fund.scenario",
            code="config_invalid",
        )


def _validate_eol_peak(cfg: dict) -> None:
    """The optional end-of-life spending peak.

    Independent of the annual trajectory on purpose: a peak is a discrete
    charge in a death year, not a rebuilt yearly basket. Its real precondition
    is mortality sampling, and that is the refusal that matters here -- with
    `mortality.enabled` false nothing in the run can die, so the control could
    be filled in, saved, and run forever without changing a number. A control
    the engine cannot reach looks exactly like one that works.
    """
    medical_d = cfg.get("medical", {}) or {}
    if not isinstance(medical_d, dict):
        return  # The premium validator owns the named refusal for this shape.
    peak = medical_d.get("eol_peak_real")
    if peak is None:
        return
    if (isinstance(peak, bool) or not isinstance(peak, numbers.Real)
            or not math.isfinite(float(peak)) or float(peak) <= 0):
        raise ConfigIncomplete(
            "medical.eol_peak_real must be a positive finite amount in "
            "today's dollars -- one person's end-of-life medical spending, "
            "charged once per death",
            field="medical.eol_peak_real",
            code="config_invalid",
        )
    mortality_d = cfg.get("mortality", {}) or {}
    # The fallback is `MortalityParams`' own default, not `False`, because a
    # request may omit the block entirely and the engine then builds the
    # dataclass -- which has mortality ON. Assuming False here refused a peak
    # on a config whose run would in fact have sampled deaths: a control the
    # user filled in correctly, rejected by name, on the partial-config path
    # that is exactly where this project's installed-app failures have lived.
    enabled = (bool(mortality_d.get("enabled", MortalityParams().enabled))
               if isinstance(mortality_d, dict) else MortalityParams().enabled)
    if not enabled:
        raise ConfigIncomplete(
            "medical.eol_peak_real needs mortality.enabled to be true; with "
            "no death sampling nobody ever dies and the peak could never be "
            "charged",
            field="medical.eol_peak_real",
            code="config_invalid",
        )


def _validate_annual_medical_trajectory(cfg: dict) -> None:
    """Reject malformed opt-in trajectory inputs at the pre-job boundary."""
    medical_d = cfg.get("medical", {}) or {}
    if not isinstance(medical_d, dict):
        return  # The premium validator above owns this named refusal.
    if "annual_trajectory_enabled" in medical_d:
        enabled = medical_d["annual_trajectory_enabled"]
        if not isinstance(enabled, bool):
            raise ConfigIncomplete(
                "medical.annual_trajectory_enabled must be a boolean",
                field="medical.annual_trajectory_enabled",
                code="config_invalid",
            )
    else:
        enabled = False
    if not enabled:
        # The age-rating anchor rides on the trajectory. Silently ignoring a
        # quote the user typed is the failure this repo has already paid for
        # (a control the adapter drops is indistinguishable from a working
        # one from outside), so refuse by name instead.
        if medical_d.get("premium_aca_age_end") is not None:
            raise ConfigIncomplete(
                "medical.premium_aca_age_end only applies when "
                "medical.annual_trajectory_enabled is true; clear one or set "
                "the other rather than leaving a quote that does nothing",
                field="medical.premium_aca_age_end",
                code="config_invalid",
            )
        if medical_d.get("household_share_primary") is not None:
            raise ConfigIncomplete(
                "medical.household_share_primary only applies when "
                "medical.annual_trajectory_enabled is true; clear one or set "
                "the other rather than leaving a split that does nothing",
                field="medical.household_share_primary",
                code="config_invalid",
            )
        return

    def finite_number(name, *, positive=False):
        value = medical_d.get(name)
        valid = (not isinstance(value, bool)
                 and isinstance(value, numbers.Real)
                 and math.isfinite(float(value)))
        valid = valid and (float(value) > 0 if positive else float(value) >= 0)
        if not valid:
            qualifier = "positive finite" if positive else "finite and non-negative"
            raise ConfigIncomplete(
                "medical.%s must be %s when the annual trajectory is enabled"
                % (name, qualifier),
                field="medical.%s" % name,
                code="config_invalid",
            )

    for name in ("non_medical_y0", "routine_y0", "oop_y0"):
        finite_number(name)
    finite_number("premium_medicare", positive=True)
    for name in ("cpi_delta_routine", "cpi_delta_premium", "cpi_delta_oop"):
        value = medical_d.get(name)
        if (isinstance(value, bool)
                or not isinstance(value, numbers.Real)
                or not math.isfinite(float(value))
                or not (-1 < float(value) <= 1)):
            raise ConfigIncomplete(
                "medical.%s must be finite and greater than -1 through 1 "
                "when the annual trajectory is enabled" % name,
                field="medical.%s" % name,
                code="config_invalid",
            )
    ages = {}
    for name in ("aca_start_age", "medicare_age"):
        value = medical_d.get(name)
        if isinstance(value, bool) or not isinstance(value, int) or not (18 <= value <= 120):
            raise ConfigIncomplete(
                "medical.%s must be an integer age from 18 through 120 "
                "when the annual trajectory is enabled" % name,
                field="medical.%s" % name,
                code="config_invalid",
            )
        ages[name] = value
    if ages["aca_start_age"] >= ages["medicare_age"]:
        raise ConfigIncomplete(
            "medical.aca_start_age must be earlier than medical.medicare_age",
            field="medical.aca_start_age",
            code="config_invalid",
        )
    _validate_aca_age_rating_anchor(cfg, medical_d, ages["medicare_age"])
    _validate_medical_household_split(cfg, medical_d)


def _validate_medical_household_split(cfg: dict, medical_d: dict) -> None:
    """The optional per-person split of the household medical basket.

    Absent (``None``) is the supported default and means the household was
    never split -- not that the two halves were measured and found equal.
    Present means it has to describe a household that exists and a share that
    is a share, and both refusals name the field so the message points at
    something the user can go and change:

      * a share outside 0..1 or not a finite number -- a "share" of 1.4 or of
        text is not a reading of anything;
      * `household.enabled` false -- there is no second person to give the
        remainder to, so the split would silently do nothing, which is the
        shape this project has been bitten by before.
    """
    share = medical_d.get("household_share_primary")
    if share is None:
        return
    if (isinstance(share, bool) or not isinstance(share, numbers.Real)
            or not math.isfinite(float(share))
            or not (0.0 <= float(share) <= 1.0)):
        raise ConfigIncomplete(
            "medical.household_share_primary must be a finite share from 0 "
            "through 1 -- the plan holder's fraction of the annual household "
            "medical totals, with the remainder covering the spouse",
            field="medical.household_share_primary",
            code="config_invalid",
        )
    household_d = cfg.get("household", {}) or {}
    enabled = (isinstance(household_d, dict)
               and bool(household_d.get("enabled", False)))
    if not enabled:
        raise ConfigIncomplete(
            "medical.household_share_primary needs household.enabled to be "
            "true; with nobody to give the remaining share to, splitting the "
            "medical basket would change nothing",
            field="medical.household_share_primary",
            code="config_invalid",
        )


def _validate_aca_age_rating_anchor(cfg: dict, medical_d: dict,
                                    medicare_age: int) -> None:
    """The optional second ACA quote that carries the age effect.

    Absent (``None``) is the supported default and means the age effect was
    never measured. Present means it has to be usable, and the two ways it can
    fail to be usable are refused separately so the message names the field the
    user has to go fix:

      * a non-positive or non-finite quote -- a $0 "quote" would read as free
        coverage for the whole bridge, which is the fake zero this project
        keeps paying for;
      * a degenerate span -- if the plan already starts at or past the last
        pre-Medicare age there is no interval between the two quotes, and any
        factor computed over it would be an invention.
    """
    end = medical_d.get("premium_aca_age_end")
    if end is None:
        return
    if (isinstance(end, bool) or not isinstance(end, numbers.Real)
            or not math.isfinite(float(end)) or float(end) <= 0):
        raise ConfigIncomplete(
            "medical.premium_aca_age_end must be a positive finite annual "
            "household-total premium, quoted for the same county, plan and "
            "covered household as medical.premium_aca but at age %d"
            % (medicare_age - 1),
            field="medical.premium_aca_age_end",
            code="config_invalid",
        )
    state_d = cfg.get("state", {}) or {}
    # Same reason as the mortality fallback above: a literal here mirrors a
    # dataclass default and drifts from it silently. `State().start_age` is 27,
    # not the 30 an earlier draft assumed, so a request omitting `state` was
    # having its span checked against an age the run would never use.
    _default_start = State().start_age
    start_age = (state_d.get("start_age", _default_start)
                 if isinstance(state_d, dict) else _default_start)
    try:
        start_age = int(start_age if start_age is not None else _default_start)
    except (TypeError, ValueError):
        raise ConfigIncomplete(
            "state.start_age must be an age before medical.medicare_age to "
            "place the ACA age-rating anchors",
            field="state.start_age",
            code="config_invalid",
        )
    if medicare_age - 1 <= start_age:
        raise ConfigIncomplete(
            "medical.premium_aca_age_end needs at least one year between "
            "state.start_age (%d) and the last pre-Medicare age (%d); with no "
            "span between the two quotes there is no age curve to draw"
            % (start_age, medicare_age - 1),
            field="medical.premium_aca_age_end",
            code="config_invalid",
        )


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
    # OPEN_ITEMS E37. Zero, and PRESENT: an absent key and a stated zero are
    # different things, and this is the leaf that makes a governmental 457(b)
    # something a user can type. Measured: adding it at zero moves no shipped
    # digest, because a zero balance adds nothing to `total` and the
    # withdrawal skips an empty account.
    gov_457b=0,
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
                          "base_salary_pre": 125_000, "ot_income_pre": 20_000,
                          # U35 / A1. The engine's one fact is the schedule
                          # itself (an empty one means no vest), so this switch
                          # lives only here: it is what the page toggles and
                          # what validation keys on, and it is deliberately not
                          # a second copy of "is there a vest" inside the
                          # engine. JSON carries a list, not the tuple default.
                          "rsu_vest_enabled": False,
                          "rsu_vest_schedule_real": [],
                          # U38=C. Annual same-year §423 offering; all dollar
                          # facts are nominal because the statutory $25,000
                          # ceiling and tax basis are nominal too.
                          "espp_enabled": False,
                          "espp_grant_fmv_schedule_nominal": [],
                          "espp_exercise_fmv_schedule_nominal": [],
                          "espp_qualifying_sale_age": None,
                          "espp_qualifying_sale_value_schedule_nominal": [],
                          "catchup_403b_15yr_schedule_nominal": [],
                          "childcare_schedule_real": [],
                          "commuting_schedule_real": [],
                          # U35 / A2a. Concentration with no shipped parameter
                          # of any kind: the user says what they kept, when
                          # they sold, and what it was worth. No share price
                          # process lives here -- that is A2b, and it is the
                          # only place a volatility is ever asked for.
                          "rsu_retained_enabled": False,
                          "rsu_retained_schedule_real": [],
                          "rsu_retained_sale_age": 60,
                          "rsu_retained_value_multiple": 1.0,
                          # U35 / A2b: the optional half. Undefaulted on
                          # purpose -- no index ships a single stock's vol.
                          "rsu_retained_sigma_enabled": False,
                          "rsu_retained_sigma_real": 0.0,
                          "rsu_retained_drift_real": 0.0},
        "promotion": _gd(PromotionParams),
        # U35 / B5. Dormant and intentionally blank: every active number is a
        # fact from the spouse's own compensation plan. Copying the primary's
        # one-person $170k/2-5 year defaults would invent a second career.
        "spouse_promotion": {
            "enabled": False,
            "timing_mode": "fixed",
            "timing_min": None,
            "timing_max": None,
            "timing_fixed": None,
            "base_salary_post": None,
            "base_growth_post": None,
            "bonus_mode": "fixed",
            "bonus_pct_min": None,
            "bonus_pct_max": None,
            "bonus_pct_fixed": None,
            "bonus_resampled_each_year": True,
            "marginal_tax_post": None,
        },
        # U36=C+A. A second promotion is always opt-in and blank. The primary
        # intentionally does NOT inherit the first promotion's shipped career
        # assumptions, and the spouse does not inherit anybody else's facts.
        "second_promotion": {
            "enabled": False,
            "timing_mode": "fixed",
            "timing_min": None,
            "timing_max": None,
            "timing_fixed": None,
            "base_salary_post": None,
            "base_growth_post": None,
            "bonus_mode": "fixed",
            "bonus_pct_min": None,
            "bonus_pct_max": None,
            "bonus_pct_fixed": None,
            "bonus_resampled_each_year": True,
            "marginal_tax_post": None,
        },
        "spouse_second_promotion": {
            "enabled": False,
            "timing_mode": "fixed",
            "timing_min": None,
            "timing_max": None,
            "timing_fixed": None,
            "base_salary_post": None,
            "base_growth_post": None,
            "bonus_mode": "fixed",
            "bonus_pct_min": None,
            "bonus_pct_max": None,
            "bonus_pct_fixed": None,
            "bonus_resampled_each_year": True,
            "marginal_tax_post": None,
        },
        # U35 / B. The spouse's own planned break. Same five wage fields as
        # `career_break` and deliberately NOT its medical premium: the
        # accumulation-phase health cost is already a household figure the
        # user prices once (Phase 6, U33), and a second spouse-side premium
        # would be two controls for one household cost.
        "spouse_career_break": {"enabled": False, "start_age": 35, "years": 1,
                                "income_fraction": 0.0,
                                "return_wage_factor": 1.0},
        # U35 / B. The spouse's own human capital: same three fields as the
        # primary's, drawn on a stream of its own.
        "spouse_human_capital": _gd(HumanCapitalParams,
                                    drop=("seed_offset",)),
        # U35 / B. The spouse's own layoff risk: the primary's nine wage
        # fields, and deliberately NOT its medical premium -- accumulation
        # health is one household figure, priced once (Phase 6 / U33).
        "spouse_layoff": {k: v for k, v in _gd(
            LayoffParams, drop=("rng", "seed_offset")).items()
            if k != "medical_premium_monthly_real"},
        # Roadmap 9.0 (B10). A PLANNED career break -- the user's own decision
        # to step back for a fixed stretch -- as opposed to `layoff`, which is
        # a random event drawn inside a path. The two are deliberately not one
        # block: "what if I take two years off" and "what if I am let go" are
        # different questions and one control could not answer either.
        # Off by default => not one line of the accumulation loop changes.
        "career_break": _gd(CareerBreakParams),
        # Roadmap 10.0 Phase 4.  All three numbers are user facts and the
        # neutral zeros do nothing while disabled.  No market average, legal
        # forgiveness rule, or "typical" payment ships in this block.
        "student_debt": _gd(STUDENT_DEBT.StudentDebtConfig),
        # Roadmap 10.0 Phase 5. Defaults preserve the superseded v2 contract,
        # but the module remains off until the user chooses a mode.
        "lifestyle_creep": _gd(LifestyleCreepParams, drop=("rng",)),
        # Roadmap 10.0 Phase 6, user choice A. SSA award incidence with all
        # cash amounts supplied by the user; disabled means no draw and no
        # change to saved plans or shipped defaults.
        "disability": _gd(DisabilityParams, drop=("rng",)),
        # returns 2.0 (E4): model "iid" (default, the v7 sampler untouched) |
        # "markov" (annual regime transitions) | "blocks" (1928-2024
        # historical block bootstrap). Extra knobs are no-ops while "iid".
        "returns": {**_gd(V7Config, drop=("n_paths", "seed")),
                    "expense_ratio": 0.0010, "rebalance_cost": 0.0,
                    "model": "iid", "persistence": 0.85, "block_years": 5,
                    "inflation_ar1": 0.0},
        "bonds": _gd(BondParams),
        # Server-side analysis, not an engine dataclass: the funded ratio
        # never reaches the simulator. It lives here so the page can offer
        # controls for it and the attribution inventory can see it.
        "funded_ratio": _gd(FundedRatioParams),
        # Estate exposure. The exemption is USER-ENTERED and unset by
        # default, on the same principle as the medical premium, the
        # annuity quote, the LTC premium and the funded-ratio discount
        # rate: it is a legislated figure that moves, this app makes no
        # network requests, and a bundled number would age silently into
        # a wrong one. Unset means the line is not shown at all rather
        # than shown against a guess.
        "estate": {"exemption_real": None},
        "glide": _gd(GlidePath),
        "medical": _gd(MedicalParams),
        "aca": _gd(ACAParams),
        "mortality": {**_gd(MortalityParams), "sex": "male"},
        "household": {**_gd(HouseholdParams),
                      "spouse_catchup_403b_15yr_schedule_nominal": []},
        "roth_ladder": _gd(RothLadderParams),
        "social_security": _gd(SocialSecurityParams),
        "ftc": _gd(FTCParams),
        "obbba": _gd(OBBBAParams),
        "eldercare": _gd(EldercareShockParams),
        # 4.0 Phase 2 · the user's OWN long-term care (eldercare above is paying
        # for a parent). Opt-in, default "off" => not one draw, bit-identical.
        # `rng` is dropped: it is the independent stream _run attaches, runtime
        # rather than configuration, on the layoff precedent.
        "ltc": _gd(LtcParams, drop=("rng",)),
        "guaranteed_income": {"mode": "off", "annuities": [], "ladders": []},
        "parents": {**_gd(ParentsParams, drop=("rng", "parents",
                                               "care_level_mix",
                                               "care_annual_cost",
                                               "care_duration_buckets")),
                    "parents": []},
        "inheritance": _gd(InheritanceParams),
        "sh_property": _gd(ShanghaiPropertyParams),
        "blocky_spending": _gd(BlockySpendingParams,
                               drop=("seed_offset",)),
        "human_capital": _gd(HumanCapitalParams, drop=("seed_offset",)),
        "house_price": _gd(HousePriceProcess,
                           # `liquidity_discount` is dropped for the same
                           # reason as the sale figures: it is read from
                           # `other_assets.sale_liquidity_discount`, where the
                           # user already set it. Leaving it here made a
                           # SECOND box for one number -- the exact defect the
                           # comment two lines up warns about, committed while
                           # writing that comment, and caught by the
                           # attribution pin within the hour.
                           drop=("seed_offset", "sale_age", "sale_base_real",
                                 "equity_base_real", "liquidity_discount")),
        "ss_trust_fund": _gd(SSTrustFundParams, drop=("seed_offset",)),
        "tax_us": _gd(TaxParams, drop=("drag_taxable_explicit",)),
        "tax_cn": _gd(TaxParamsChina),
        "relocation": {"enabled": False, **_gd(RelocationParams)},
        "china_healthcare": _gd(ChinaHealthcareParams),
        "ss_nra": _gd(SSNRAHaircutParams),
        "rule": {"upper_guardrail": 0.20, "lower_guardrail": 0.20,
                 "adjustment_pct": 0.10, "inflation_freeze_enabled": True,
                 # 1.0 = the historical behaviour: a triggered cut happens in
                 # full. Roadmap 5.0 Phase 4 makes that assumption visible and
                 # adjustable rather than removing it -- the default is
                 # bit-identical, and a test proves it.
                 "cut_realisation": 1.0},
        # Assets beyond the four engine buckets: cash/other_liquid fold into
        # taxable at run time; home equity is EXCLUDED from the simulation
        # (illiquid) unless a planned sale turns it into an inflow event.
        "other_assets": {"cash": 0, "other_liquid": 0, "home_equity": 0,
                         "sell_home_enabled": False, "sell_home_age": 65,
                         "sell_home_net_real": 0,
                         # Roadmap 6.0 Phase 4. Both default to "not asked
                         # for": a discount that defaults above zero would
                         # make every plan quietly poorer, and a downsize
                         # nobody planned would change what they pay to live.
                         "sale_liquidity_discount": 0.0,
                         "downsize_enabled": False,
                         "downsize_new_price_real": 0},
        # children: [{parent_age_at_birth, annual_cost_real, support_years,
        #             college_total_real}] — compiled into life events.
        "children": [],
        # Income streams beyond salary. Owners are additive schema leaves:
        # old plans normalize to "unspecified", preserving last-survivor numeric
        # behavior without inventing confirmed joint ownership.
        "income_streams": {
            "pension_enabled": False, "pension_annual_real": 0,
            "pension_amount_mode": "manual",
            "pension_db_service_years": 0,
            "pension_db_accrual_rate": 0,
            "pension_db_final_average_salary_real": 0,
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
                   "bad_year_multiplier": 3.0, "p_cap": 0.50, "gap_months": 4.0,
                   # Roadmap 6.0 (A13): zero keeps the flat gap every
                   # existing plan computed.
                   "gap_months_per_year_of_age": 0.0,
                   "decay_from_age": 45, "max_gap_months": 12.0,
                   "medical_premium_monthly_real": 0.0},
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
# Only the buckets the real baseline actually HOLDS. Since Roadmap 7.0 an
# `AccountStack` refuses an account it was never given, so `getattr` here
# would raise on the 457(b) leaf rather than compare -- and comparing it
# would be meaningless anyway: an account the real baseline never had cannot
# be leaked by a default that matches it at zero.
_actual_stack = INITIAL_STACK_ACTUAL.balances()
assert all(_dc["initial"][k] != _actual_stack[k]
           for k in _dc["initial"] if k in _actual_stack), \
    "de-identification regressed: served portfolio equals the real baseline"
assert _dc["state"]["expenses_y0"] != _gd(State)["expenses_y0"], \
    "de-identification regressed: real expenses_y0 in served defaults"
assert _dc["contributions"]["base_salary_pre"] != _gd(V8ContributionParams)["base_salary_pre"], \
    "de-identification regressed: real base salary in served defaults"
del _dc, _actual_stack


def _mk(cls, d: dict, enums: Optional[dict] = None):
    """Construct `cls` from dict `d`, coercing enum-valued fields and ignoring
    any keys the dataclass does not declare (e.g. relocation.enabled)."""
    d = dict(d or {})
    for k, EC in (enums or {}).items():
        if k in d and d[k] is not None and not isinstance(d[k], EC):
            d[k] = EC(d[k])
    # `AccountStack` stopped being a dataclass in Roadmap 7.0 Phase 2b: it now
    # holds balances in a mapping so a fifth account is counted rather than
    # silently dropped from `total`. It still takes the four US names as
    # keyword arguments, so the only thing that had to change here is HOW the
    # accepted names are discovered -- a reflective helper reaching for
    # `dataclasses.fields` is exactly the kind of call site a data-shape
    # change reaches without touching its name.
    if dataclasses.is_dataclass(cls):
        valid = {f.name for f in dataclasses.fields(cls)}
    else:
        import inspect
        signature = inspect.signature(cls)
        valid = {name for name, param in signature.parameters.items()
                 if param.kind in (param.POSITIONAL_OR_KEYWORD,
                                   param.KEYWORD_ONLY)}
        # OPEN_ITEMS E37. `AccountStack` takes its extra accounts through
        # `**extra`, which is VAR_KEYWORD -- the kinds above exclude it, so
        # `initial.gov_457b = 100000` was dropped WITHOUT A WORD. Measured
        # before the fix: `total` came back 10 on a stack that had been handed
        # 100,010. That is the E34 failure a third time, at the door rather
        # than inside the engine.
        #
        # Only fields the SCHEMA declares are let through. An undeclared key
        # is still dropped, because this helper's job is to ignore config a
        # class does not want (`relocation.enabled` and friends) -- what it
        # must not do is ignore an account somebody actually has.
        if cls is AccountStack:
            import account_schema as _schema
            valid |= {account.field for account in _schema.US_ACCOUNT_TYPES
                      if account.field}
    return cls(**{k: v for k, v in d.items() if k in valid})


def _validate_funded_ratio(cfg: dict) -> None:
    """The two funded-ratio inputs, checked before a job exists.

    Both are optional -- absent means the analysis reports itself as not
    applicable rather than running on a guess. What is refused is a value that
    is present and nonsense, because the ratio moves with the discount rate
    more than with anything else and a mistyped one produces a confident wrong
    answer rather than an error.
    """
    estate = cfg.get("estate", None)
    if estate is not None:
        if not isinstance(estate, dict):
            raise ConfigIncomplete("estate must be an object",
                                   field="estate", code="config_invalid")
        exemption = estate.get("exemption_real", None)
        if exemption is not None:
            if isinstance(exemption, bool) or not isinstance(
                    exemption, (int, float)):
                raise ConfigIncomplete(
                    "estate.exemption_real must be a number",
                    field="estate.exemption_real", code="config_invalid")
            if float(exemption) < 0:
                raise ConfigIncomplete(
                    "estate.exemption_real cannot be negative",
                    field="estate.exemption_real", code="config_invalid")
    group = cfg.get("funded_ratio", None)
    if group is None:
        return
    if not isinstance(group, dict):
        raise ConfigIncomplete("funded_ratio must be an object",
                               field="funded_ratio", code="config_invalid")
    rate = group.get("discount_rate_real", None)
    if rate is not None:
        if isinstance(rate, bool) or not isinstance(rate, (int, float)):
            raise ConfigIncomplete(
                "funded_ratio.discount_rate_real must be a number",
                field="funded_ratio.discount_rate_real", code="config_invalid")
        rate = float(rate)
        if rate != rate or not (-0.05 <= rate <= 0.20):
            raise ConfigIncomplete(
                "funded_ratio.discount_rate_real must be between -5% and 20% "
                "(it is a REAL rate, so today's is a low single digit)",
                field="funded_ratio.discount_rate_real", code="config_invalid")
    floor = group.get("floor_annual_real", None)
    if floor is not None:
        if isinstance(floor, bool) or not isinstance(floor, (int, float)):
            raise ConfigIncomplete(
                "funded_ratio.floor_annual_real must be a number",
                field="funded_ratio.floor_annual_real", code="config_invalid")
        floor = float(floor)
        if floor != floor or floor < 0 or floor in (float("inf"),):
            raise ConfigIncomplete(
                "funded_ratio.floor_annual_real must be a finite amount of at "
                "least zero",
                field="funded_ratio.floor_annual_real", code="config_invalid")


def _validate_state_archetype(cfg: dict) -> None:
    """An unknown archetype id must be refused, not silently ignored.

    The engine indexes `US_STATE_ARCHETYPES[id]` directly, so a typo would be a
    KeyError inside a background job -- a run that dies rather than a request
    that is answered. And silently falling back to the flat rate would be
    worse: the user would be shown a plan for a state posture they did not
    choose, with no indication anything was wrong.
    """
    tt = cfg.get("tax_true", {}) or {}
    if not isinstance(tt, dict):
        raise ConfigIncomplete("tax_true must be an object",
                               field="tax_true", code="config_invalid")
    archetype = tt.get("state_archetype", None)
    if archetype is None:
        return
    if not isinstance(archetype, str) or archetype not in US_STATE_ARCHETYPES:
        raise ConfigIncomplete(
            "tax_true.state_archetype must be one of: %s"
            % ", ".join(sorted(US_STATE_ARCHETYPES)),
            field="tax_true.state_archetype", code="config_invalid")


def _validate_dividend_drag(cfg: dict) -> None:
    """The three inputs the taxable drag is now derived from.

    Checked here, before a job id exists, for the reason lesson 1 records: a
    background job turns every refusal into a failed run the user has to go
    read. And checked at all because these three multiply straight into a
    return -- a stray string or a yield of 40% does not raise anywhere
    downstream, it silently produces a plan.

    `drag_taxable` is validated as an override rather than as a required
    input: absent means "derive it", which is the normal case, while a present
    value must still be a sane fraction.
    """
    tax_d = cfg.get("tax_us", {}) or {}
    if not isinstance(tax_d, dict):
        raise ConfigIncomplete(
            "tax_us must be an object",
            field="tax_us", code="config_invalid")

    def check(name, value, low, high):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ConfigIncomplete(
                "tax_us.%s must be a number between %s and %s" % (name, low, high),
                field="tax_us." + name, code="config_invalid")
        number = float(value)
        if number != number or number in (float("inf"), float("-inf")):
            raise ConfigIncomplete(
                "tax_us.%s must be finite" % name,
                field="tax_us." + name, code="config_invalid")
        if not (low <= number <= high):
            raise ConfigIncomplete(
                "tax_us.%s must be between %s and %s" % (name, low, high),
                field="tax_us." + name, code="config_invalid")

    for name, high in (("dividend_yield", 0.25),
                       ("dividend_qualified_fraction", 1.0),
                       ("dividend_tax_rate", 1.0)):
        if name in tax_d:
            check(name, tax_d[name], 0.0, high)
    override = tax_d.get("drag_taxable", None)
    if override is not None:
        check("drag_taxable", override, 0.0, 1.0)


def _mk_tax_us(d: dict) -> TaxParams:
    """`TaxParams` with the taxable drag resolved to a number.

    The engine multiplies this into a return every year, so it must never see
    `None`. Resolution happens here rather than in the dataclass because it is
    a mapping decision: an explicit `drag_taxable` in the config wins verbatim,
    and only its absence means "derive it from the yield and the rates".

    That ordering is what lets a plan saved before Phase 3 keep reproducing --
    a stored config carries its own `drag_taxable`, so it keeps the assumption
    it was saved with instead of silently adopting a new one.
    """
    # `TaxParams.__post_init__` does the resolving, so every construction path
    # gets a float whether or not it came through here. This wrapper stays for
    # the name at the call site and as the place the docstring lives.
    return _mk(TaxParams, d)


def _after_liquidity_discount(amount: float, other_assets: dict) -> float:
    """What a sale actually nets, which is not what the house is worth.

    Roadmap 6.0 Phase 4. The price process says what the place is worth; a
    seller gets that minus commission, minus repairs the buyer demanded, minus
    whatever a timed sale costs. Zero by default: a discount that arrived
    switched on would make every existing plan quietly poorer, and nobody
    would know which number moved.
    """
    discount = float((other_assets or {}).get("sale_liquidity_discount") or 0.0)
    if not (0.0 <= discount < 1.0):
        raise ConfigIncomplete(
            "other_assets.sale_liquidity_discount must be a fraction from 0 "
            "up to (not including) 1 -- the share of the sale price lost to "
            "commission, repairs and timing",
            field="other_assets.sale_liquidity_discount",
            code="config_invalid")
    return amount * (1.0 - discount)


def _mk_retained_stock(block, state_block):
    """The optional drawn path for kept shares; dormant unless asked for."""
    if not (block.get("rsu_retained_enabled")
            and block.get("rsu_retained_sigma_enabled")):
        return RetainedStockProcess()
    kept = [float(v) for v in (block.get("rsu_retained_schedule_real") or ())]
    return RetainedStockProcess(
        enabled=True,
        sigma_real=float(block.get("rsu_retained_sigma_real", 0.0) or 0.0),
        drift_real=float(block.get("rsu_retained_drift_real", 0.0) or 0.0),
        sale_age=int(block["rsu_retained_sale_age"]),
        kept_total_real=sum(kept))


def _mk_house_price(block, cfg: dict):
    """Build the price process from its own block plus the figures the user
    already gave `other_assets`.

    Deliberately not a second place to type a home value. This repository has
    paid twice this week for one fact living in two lists; a house worth
    $600,000 in one box and $700,000 in another would be the same defect with
    money on it.
    """
    block = dict(block or {})
    other = cfg.get("other_assets") or {}
    if not isinstance(other, dict):
        other = {}
    sale_on = bool(other.get("sell_home_enabled"))
    return HousePriceProcess(
        enabled=bool(block.get("enabled", False)),
        sigma_real=float(block.get("sigma_real", 0.10) or 0.0),
        drift_real=float(block.get("drift_real", 0.01) or 0.0),
        include_in_net_worth=bool(block.get("include_in_net_worth", False)),
        sale_age=int(other.get("sell_home_age", 65)) if sale_on else None,
        sale_base_real=float(other.get("sell_home_net_real") or 0.0)
        if sale_on else 0.0,
        liquidity_discount=float(other.get("sale_liquidity_discount") or 0.0),
        equity_base_real=float(other.get("home_equity") or 0.0),
    )


def build_kwargs(cfg: dict, relocation_on: bool) -> dict:
    """Map the JSON config into the v9.8 param objects. Returns a kwargs dict for
    run_lifecycle_mc_v98 (the V7Config lands under 'config')."""
    g = lambda k: cfg.get(k, {}) or {}

    medical_d = g("medical")

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

    # Mortality sex -> Gompertz table. NOT "male by default": `default_config()`
    # states male, but a config that omits `mortality.sex` keeps
    # `MortalityParams`' own defaults, which are the UNISEX table (alpha 8e-05
    # vs male's 1.04e-04 — about 20% less hazard across 65-100). Supplying a sex
    # here would therefore MOVE the numbers of every partial-config run that
    # works today, which is why the refusal below names the field instead.
    mort_d = dict(g("mortality"))
    _sex = mort_d.pop("sex", None)
    if _sex == "female":
        mort_d["alpha"], mort_d["beta"] = MORTALITY_FEMALE.alpha, MORTALITY_FEMALE.beta
        mort_d["sex_label"] = MORTALITY_FEMALE.sex_label
    elif _sex == "male":
        mort_d["alpha"], mort_d["beta"] = MORTALITY_MALE.alpha, MORTALITY_MALE.beta
        mort_d["sex_label"] = MORTALITY_MALE.sex_label
    # The label travels with the table it names. Before this, `default_config()`
    # stated `sex: "male"` and `sex_label: "unisex"` separately, so the object
    # carried male hazards under a label saying otherwise. Nothing in shipping
    # code reads `sex_label` today, which is the only reason this was harmless
    # rather than a wrong number on a page — and is exactly why it should be
    # fixed before something starts reading it.

    # Long-term care. The plan already states a sex for mortality, so the LTC
    # lifetime risk is resolved from it rather than asked for twice — two
    # answers to one question is a contradiction waiting to be shipped. An
    # explicit non-zero `ltc.lifetime_risk` still wins.
    ltc_params = _mk(LtcParams, g("ltc"))
    if not ltc_params.lifetime_risk and _sex in LTC_LIFETIME_RISK:
        ltc_params = dataclasses.replace(
            ltc_params, lifetime_risk=LTC_LIFETIME_RISK[_sex])
    if ltc_params.mode == LTC.STOCHASTIC and not ltc_params.lifetime_risk:
        # The engine refuses this too, and for the same reason — but it refuses
        # from inside the extraction loop, which reaches the user as a run that
        # died rather than as a plan that is missing a setting. Refuse here,
        # before a single draw, and say which field. Three ways out, all the
        # user's: state a sex, state a risk, or use `scenario` mode, which needs
        # neither. Silently running with care switched off is NOT among them:
        # a module the user turned on must not report a zero it never modelled.
        _known = ", ".join(sorted(LTC_LIFETIME_RISK))
        if not _sex:                      # absent, or present and left blank
            raise ConfigIncomplete(
                "stochastic long-term care needs a lifetime risk, and this plan "
                "does not say which mortality table it uses. Set mortality.sex "
                "(%s), or set ltc.lifetime_risk directly, or use ltc.mode "
                "'scenario', which asks for a duration instead. Defaulting the "
                "sex here would move every number in the run, including the "
                "mortality table this plan plainly did not ask for."
                % _known, field="mortality.sex")
        raise ConfigIncomplete(
            "stochastic long-term care carries no lifetime risk for sex %r — "
            "the care module has figures for %s only. Set ltc.lifetime_risk "
            "directly, or use ltc.mode 'scenario', which asks for a duration "
            "instead." % (_sex, _known), field="ltc.lifetime_risk")

    # The parent lifecycle. Opt-in, and when it is on it REPLACES the eldercare
    # shock and the inheritance draw rather than joining them: those two model
    # the same parent as two unrelated people, and running all three would bill
    # the plan twice for one decline and credit it twice for one death. That is
    # refused here rather than netted out, because there is no honest netting —
    # the modules disagree about how many parents there are.
    _pd = dict(g("parents"))
    _raw_parents = _pd.pop("parents", None) or []
    parents_params = _mk(ParentsParams, _pd)
    if parents_params.mode != PARENTS.OFF:
        for _other, _label in (("eldercare", "eldercare shock"),
                               ("inheritance", "inheritance")):
            if str((g(_other) or {}).get("mode", "off") or "off") != "off":
                raise ConfigIncomplete(
                    "the parent lifecycle module and the %s cannot both be on: "
                    "they model the same parent as two different people, so "
                    "the plan would pay for one decline twice and inherit from "
                    "one death twice. Set %s.mode to 'off' to use the parent "
                    "module, or set parents.mode to 'off' to keep the older "
                    "one." % (_label, _other),
                    field="%s.mode" % _other, code="config_invalid")
        people = []
        for _i, _p in enumerate(_raw_parents):
            try:
                people.append(_mk(Parent, dict(_p)))
            except (TypeError, ValueError, AttributeError) as exc:
                raise ConfigIncomplete(
                    "parents.parents[%d] is not a usable parent (%s). Each one "
                    "is an object with `current_age`, `sex`, `estate_y0` and an "
                    "optional `care_lifetime_risk`." % (_i, exc),
                    field="parents.parents[%d]" % _i,
                    code="config_invalid") from exc
        for _i, _p in enumerate(people):
            if str(_p.sex) not in LTC_LIFETIME_RISK:
                raise ConfigIncomplete(
                    "parents.parents[%d] states sex %r, and this engine carries "
                    "a mortality table for %s only. Falling back to the plan "
                    "holder's own table would run that parent on a lifespan "
                    "nobody chose, and report it as though it had been asked "
                    "for." % (_i, _p.sex, ", ".join(sorted(LTC_LIFETIME_RISK))),
                    field="parents.parents[%d].sex" % _i, code="config_invalid")
        if not people:
            raise ConfigIncomplete(
                "the parent lifecycle module is on but the plan lists no "
                "parents, so it would model nothing and report zero support "
                "cost and zero inheritance — both of which would read as "
                "measurements. Add at least one parent, or set parents.mode "
                "to 'off'.", field="parents.parents")
        parents_params = dataclasses.replace(parents_params, parents=people)

    # Household: combine spouse starting balances into the household stack.
    init = _mk(AccountStack, g("initial"))
    _hh = g("household")
    if _hh.get("enabled"):
        # `replace` rather than a fresh construction: a spouse merge that
        # names four fields drops any other account the stack holds, and the
        # household path is exactly where a couple's balances get bigger, not
        # smaller. The four spouse inputs stay four because that is what the
        # config offers; the difference is that everything ELSE survives.
        init = init.replace(
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
        # `AccountStack` is no longer a dataclass (7.0 Phase 2b); it carries
        # an equivalent `replace` so this reads the same.
        init = init.replace(taxable=init.taxable + _liquid)

    # Life events: explicit list + children compiler + planned home sale.
    # + = outflow (funded from the stack), − = inflow (credited to taxable).
    # A malformed entry is REFUSED, not skipped. Skipping is what this did
    # before, and it is the false zero in its purest form: a config whose
    # amount key was spelled `amount` instead of `amount_real` lost a $250,000
    # event in silence and reported a HIGHER success rate for it (0.9967 ->
    # 0.9983 measured), with nothing anywhere saying an event had been dropped.
    # The UI cannot reach this — its editor writes `+i.value || 0`, so both keys
    # always exist and are always numbers — so the population this refuses is
    # exactly the one that hand-writes configs, which is who the partial-config
    # LTC defect was found by.
    events = []
    for index, e in enumerate(cfg.get("life_events") or []):
        try:
            events.append((int(e["age"]), float(e["amount_real"])))
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise ConfigIncomplete(
                "life_events[%d] is not a usable event (%s). Each one needs an "
                "integer `age` and a numeric `amount_real` — positive for an "
                "outflow, negative for an inflow. Dropping it instead would "
                "remove money from the plan and report the result as if you "
                "had never asked for it." % (index, exc),
                field="life_events[%d]" % index, code="config_invalid") from exc
    for index, ch in enumerate(cfg.get("children") or []):
        try:
            b = int(ch.get("parent_age_at_birth", 32))
            annual = float(ch.get("annual_cost_real", 15000))
            yrs = int(ch.get("support_years", 22))
            college = float(ch.get("college_total_real", 0))
        except (TypeError, ValueError, AttributeError) as exc:
            raise ConfigIncomplete(
                "children[%d] is not a usable child (%s). Each one is an object "
                "with numeric `parent_age_at_birth`, `annual_cost_real`, "
                "`support_years` and `college_total_real`; every field has a "
                "default, so the usual cause is the list holding something "
                "other than objects." % (index, exc),
                field="children[%d]" % index, code="config_invalid") from exc
        for k in range(max(0, yrs)):
            events.append((b + k, annual))
        if college > 0:
            for k in range(4):
                events.append((b + 18 + k, college / 4.0))
    # The sale is compiled here as a fixed amount UNLESS the price process is
    # on, in which case the engine emits it per path from the drawn path and
    # this one would be a second sale of the same house. The first version
    # emitted both and moved median terminal wealth 24% -- the same "one fact
    # in two places" shape this repository has now paid for three times, with
    # money on it.
    _price_process_on = bool((cfg.get("house_price") or {}).get("enabled"))
    if (oa.get("sell_home_enabled")
            and float(oa.get("sell_home_net_real") or 0) > 0
            and not _price_process_on):
        events.append((int(oa.get("sell_home_age", 65)),
                       -_after_liquidity_discount(
                           float(oa["sell_home_net_real"]), oa)))
    # A downsize is a sale AND a purchase. Without the second half a plan
    # would bank the proceeds and keep living somewhere for free, which is the
    # most flattering possible arithmetic.
    _down = float(oa.get("downsize_new_price_real") or 0.0)
    if oa.get("downsize_enabled") and _down > 0:
        events.append((int(oa.get("sell_home_age", 65)), _down))

    # U35 / A2a. Keeping vested shares instead of selling them is compiled
    # here rather than modelled as a fifth account, because the cost of the
    # decision is already expressible in this channel: the money LEAVES the
    # diversified stack in the year it is kept (so it stops compounding at the
    # portfolio's return, and stops being available), and comes back at the
    # sale age worth whatever the user says it was worth. That difference IS
    # the concentration. No share price process and no volatility is involved
    # -- this half deliberately asks for no parameter nobody can source.
    _contrib_block = g("contributions")
    if _contrib_block.get("rsu_retained_enabled"):
        _kept = [float(v) for v in
                 (_contrib_block.get("rsu_retained_schedule_real") or ())]
        _start_age = int((g("state")).get("start_age", 30) or 30)
        for _index, _amount in enumerate(_kept):
            if _amount:
                events.append((_start_age + _index, _amount))
        # The sale is compiled here as a fixed multiple UNLESS the drawn path
        # is on, in which case the engine emits it per path and this one would
        # be a second sale of the same shares. The house paid 24% of median
        # terminal wealth to learn this; it is not re-learned here.
        _pot = sum(_kept) * float(
            _contrib_block.get("rsu_retained_value_multiple", 1.0))
        if _pot and not _contrib_block.get("rsu_retained_sigma_enabled"):
            events.append(
                (int(_contrib_block["rsu_retained_sale_age"]), -_pot))

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
        pension_mode = str(ist.get("pension_amount_mode", "manual") or "manual")
        if pension_mode not in ("manual", "traditional_db"):
            raise ValueError(
                "income_streams.pension_amount_mode must be manual or traditional_db")
        if pension_mode == "traditional_db":
            _db_values = {}
            for _field in ("pension_db_service_years",
                           "pension_db_accrual_rate",
                           "pension_db_final_average_salary_real"):
                _value = _enabled_amount(_field)
                if _value < 0:
                    raise ValueError(f"income_streams.{_field} must be non-negative")
                _db_values[_field] = _value
            if _db_values["pension_db_accrual_rate"] > 1.0:
                raise ValueError(
                    "income_streams.pension_db_accrual_rate must be at most 100%")
            pension_amount = (
                _db_values["pension_db_service_years"]
                * _db_values["pension_db_accrual_rate"]
                * _db_values["pension_db_final_average_salary_real"])
        else:
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

    # Guaranteed income. Compiles the user's own quotes into the cash channels
    # the engine already has: the premium down the life-event channel (positive
    # = outflow) and the payments as income streams. Nothing here derives a
    # payout — the numbers are the ones a company actually offered this user.
    _gi = dict(g("guaranteed_income"))
    _gi_mode = str(_gi.get("mode", GI.OFF) or GI.OFF)
    guaranteed = GuaranteedIncomeParams(mode=_gi_mode)
    guaranteed_meta = None
    if _gi_mode != GI.OFF:
        def _instrument(cls, raw, index, field):
            try:
                return _mk(cls, dict(raw))
            except (TypeError, ValueError, AttributeError) as exc:
                raise ConfigIncomplete(
                    "guaranteed_income.%s[%d] is not usable (%s). Each entry "
                    "carries the figures from your own quote."
                    % (field, index, exc),
                    field="guaranteed_income.%s[%d]" % (field, index),
                    code="config_invalid") from exc

        guaranteed = GuaranteedIncomeParams(
            mode=_gi_mode,
            annuities=[_instrument(Annuity, raw, i, "annuities")
                       for i, raw in enumerate(_gi.get("annuities") or [])],
            ladders=[_instrument(TipsLadder, raw, i, "ladders")
                     for i, raw in enumerate(_gi.get("ladders") or [])])
        _st = _mk(State, g("state"))
        _horizon_end = (int(_st.start_age) + int(_st.accum_years)
                        + int(_st.retire_horizon))
        try:
            compiled = _compile_guaranteed(guaranteed,
                                           horizon_end_age=_horizon_end)
        except GI.GuaranteedIncomeError as exc:
            raise ConfigIncomplete(str(exc), field="guaranteed_income",
                                   code="config_invalid") from exc
        guaranteed_meta = compiled["meta"]
        # Premiums ride the life-event channel, which is where a mandatory
        # one-off outflow already belongs; the engine funds it from the stack
        # exactly as it funds a home purchase.
        events.extend(compiled["premium_events"])
        for spec in compiled["streams"]:
            kwargs = dict(spec)
            if not kwargs.get("cola"):
                # The non-COLA path needs the anchor the engine measures CPI
                # against; without it the engine refuses rather than guessing.
                kwargs["nominal_anchor_cpi"] = None
            structured_income.append(_mk(IncomeStreamSpec, kwargs))

    # E5 housing -> static yearly events (rent / down payment / refunds). When
    # a real mortgage exists, the positive post-purchase carrying rows travel
    # with its frozen internal spec so lifecycle/backtest can merge carrying +
    # realized-CPI mortgage before generic funding/shortfall handling. Rent and
    # 100%-down buy mode have no mortgage spec and retain static carrying rows.
    mortgage_payload = HOUSING.compile_housing_mortgage(cfg)
    _state_for_housing = g("state")
    _accum_end_age = (int(_state_for_housing.get(
        "start_age", State().start_age)) + int(_state_for_housing.get(
            "accum_years", State().accum_years)))
    _static_housing_events = HOUSING.compile_housing_events(
        cfg, include_mortgage=False, include_carry=mortgage_payload is None)
    events.extend((age, amount) for age, amount in _static_housing_events
                  if int(age) > _accum_end_age)
    if mortgage_payload is not None:
        # The engine resolves mortgage/carry against each realized CPI path.
        # Cancel the deterministic amount already reserved in the working
        # waterfall, so the event channel carries actual minus reserved only.
        _full = {}
        _base = {}
        for age, amount in HOUSING.compile_housing_events(
                cfg, include_mortgage=True, include_carry=True):
            _full[int(age)] = _full.get(int(age), 0.0) + float(amount)
        for age, amount in _static_housing_events:
            _base[int(age)] = _base.get(int(age), 0.0) + float(amount)
        events.extend(
            (age, -(_full.get(age, 0.0) - _base.get(age, 0.0)))
            for age in sorted(_full)
            if age <= _accum_end_age
            and abs(_full.get(age, 0.0) - _base.get(age, 0.0)) > 1e-12)
    housing_mortgage = (
        HousingMortgageSpec(
            purchase_age=mortgage_payload["purchase_age"],
            payments=mortgage_payload["payments"],
            carrying_by_age=mortgage_payload["carrying_by_age"],
        ) if mortgage_payload is not None else None
    )
    student_debt = _student_debt_schedule(cfg)

    # Resolve the working-years living cost driving the taxable-savings
    # residual: explicit value, else the user's retirement spending — never
    # the engine's calibration default (adapter correctness fix, 2026-07-10).
    contrib_d = dict(g("contributions"))
    if not contrib_d.get("annual_spending_now"):
        contrib_d["annual_spending_now"] = float(st_d.get("expenses_y0", 40_440) or 40_440)
    # U35 / A1. The switch is resolved HERE, so the engine receives exactly one
    # fact: a schedule, or nothing. A disabled plan hands over an empty tuple
    # even when the user's numbers are still stored, which is what keeps a
    # saved-but-off schedule bit-identical to a plan that never had one.
    contrib_d["rsu_vest_schedule_real"] = (
        tuple(float(v) for v in (contrib_d.get("rsu_vest_schedule_real") or ()))
        if contrib_d.get("rsu_vest_enabled") else ())
    _espp_on = bool(contrib_d.get("espp_enabled"))
    for _field in ("espp_grant_fmv_schedule_nominal",
                   "espp_exercise_fmv_schedule_nominal",
                   "espp_qualifying_sale_value_schedule_nominal"):
        contrib_d[_field] = (
            tuple(float(v) for v in (contrib_d.get(_field) or ()))
            if _espp_on else ())
    contrib_d["catchup_403b_15yr_schedule_nominal"] = tuple(
        float(v) for v in (
            contrib_d.get("catchup_403b_15yr_schedule_nominal") or ()))
    contrib_d.pop("rsu_vest_enabled", None)
    contrib_d.pop("espp_enabled", None)

    out = {
        "config": cfg_obj,
        "state": _mk(State, g("state")),
        "initial": init,
        "contrib_params": _mk(V8ContributionParams, contrib_d),
        "promo_params": _mk(PromotionParams, g("promotion")),
        "spouse_promotion": _mk(PromotionParams, {
            "enabled": False, **g("spouse_promotion"),
            # There is no spouse overtime input, so this engine-only field is
            # fixed and never exposed as a control that cannot do anything.
            "ot_eliminated": True,
        }),
        "second_promotion": _mk(PromotionParams, {
            "enabled": False, **g("second_promotion"),
            # OT already transitioned (or did not) at the first promotion.
            "ot_eliminated": True,
        }),
        "spouse_second_promotion": _mk(PromotionParams, {
            "enabled": False, **g("spouse_second_promotion"),
            "ot_eliminated": True,
        }),
        "bond_params": _mk(BondParams, g("bonds")),
        "glide_path": _mk(GlidePath, g("glide")),
        "medical": _mk(MedicalParams, medical_d),
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
        "ltc": ltc_params,
        "parents": parents_params,
        "inheritance": _mk(InheritanceParams, g("inheritance"), {"mode": ShockMode}),
        "sh_property": _mk(ShanghaiPropertyParams, g("sh_property")),
        "blocky_spending": _mk(BlockySpendingParams, g("blocky_spending")),
        # The sale and equity figures are NOT config of their own: they are
        # read from where the user already entered them (`other_assets`), so
        # this module cannot disagree with the numbers on screen.
        "house_price": _mk_house_price(g("house_price"), cfg),
        # U35 / A2b. Built from the SAME kept schedule the fixed-multiple
        # events came from, so the two halves cannot disagree about how much
        # was kept -- only about how it is valued.
        "retained_stock": _mk_retained_stock(g("contributions"), g("state")),
        # Seed offset 90_020: the primary's human capital owns 90_002, and two
        # earners sharing one offset would draw one career twice.
        "spouse_human_capital": dataclasses.replace(
            _mk(HumanCapitalParams, g("spouse_human_capital")),
            seed_offset=90_020),
        "human_capital": _mk(HumanCapitalParams, g("human_capital")),
        "ss_trust_fund": _mk(SSTrustFundParams, g("ss_trust_fund")),
        "tax_us": _mk_tax_us(g("tax_us")),
        "tax_cn": _mk(TaxParamsChina, g("tax_cn")),
        "relocation": _mk(RelocationParams, rl),
        "china_healthcare": _mk(ChinaHealthcareParams, g("china_healthcare")),
        "ss_nra": _mk(SSNRAHaircutParams, g("ss_nra")),
        "rule": rule,
        "fire_swr": float(g("state").get("swr_pref", 0.0333)),
        "life_events": (sorted(events) or None),
        "housing_mortgage": housing_mortgage,
        "student_debt": student_debt,
        "income_streams": (tuple(structured_income) or None),
        "tax_true": _mk(TrueTaxParams, {**g("tax_true"),
                                        "filing_jointly": bool(_hh.get("enabled"))}),
        "returns_x": returns_x,
    }
    ssa_pia_resolver = _ssa_pia_resolver(g("social_security"))
    if ssa_pia_resolver is not None:
        out["ssa_pia_resolver"] = ssa_pia_resolver
    return out


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
def _layoff_ctx(cfg: dict, seed: int, path_index: Optional[int] = None):
    """Set fire_v8_model._LAYOFF for the run (career stream seed+7_000_000,
    the v2 engine's convention).  B4's opt-in path isolation instead uses
    ``[seed, path_index, 7_000_000]``.  Off => None => zero draws,
    bit-identical."""
    lo = (cfg.get("layoff") or {})
    if lo.get("enabled"):
        params = _mk(LayoffParams, {k: v for k, v in lo.items() if k != "rng"})
        params.rng = np.random.default_rng(
            int(seed) + 7_000_000 if path_index is None
            else [int(seed), int(path_index), 7_000_000])
        prev = fire_v8_model._LAYOFF
        fire_v8_model._LAYOFF = params
        try:
            yield
        finally:
            fire_v8_model._LAYOFF = prev
    else:
        yield


@contextlib.contextmanager
def _spouse_layoff_ctx(cfg: dict, seed: int, path_index: Optional[int] = None):
    """Set `fire_v8_model._SPOUSE_LAYOFF` for the run, then restore it.

    Seeded `seed + 23_000_000`: the existing career-stream convention is a
    prime times a million (1, 3, 7, 11, 13, 17, 19 are taken), and two earners
    sharing 7_000_000 would be one job lost twice. Only compiled when the
    household is on -- a spouse layoff with no spouse is not an event.
    """
    lo = (cfg.get("spouse_layoff") or {})
    if lo.get("enabled") and (cfg.get("household") or {}).get("enabled"):
        params = _mk(LayoffParams, {k: v for k, v in lo.items() if k != "rng"})
        params.rng = np.random.default_rng(
            int(seed) + 23_000_000 if path_index is None
            else [int(seed), int(path_index), 23_000_000])
        prev = fire_v8_model._SPOUSE_LAYOFF
        fire_v8_model._SPOUSE_LAYOFF = params
        try:
            yield
        finally:
            fire_v8_model._SPOUSE_LAYOFF = prev
    else:
        yield


@contextlib.contextmanager
def _lifestyle_creep_ctx(
    cfg: dict, seed: int, path_index: Optional[int] = None,
):
    """Install one sampled event on an independent stable RNG substream.

    The old v2 engine shared this draw with layoffs. The live engine does not:
    enabling an unrelated spending module must not move a same-seed layoff.
    Off creates neither a generator nor a draw.
    """
    raw = cfg.get("lifestyle_creep") or {}
    if raw.get("mode", "off") == "off":
        yield
        return
    params = _mk(LifestyleCreepParams, {
        k: v for k, v in raw.items() if k != "rng"})
    params.rng = np.random.default_rng(
        int(seed) + LIFESTYLE_CREEP_SEED_OFFSET if path_index is None
        else [int(seed), int(path_index), LIFESTYLE_CREEP_SEED_OFFSET])
    event = sample_lifestyle_creep(params)
    prev = fire_v8_model._LIFESTYLE_CREEP
    fire_v8_model._LIFESTYLE_CREEP = event
    try:
        yield
    finally:
        fire_v8_model._LIFESTYLE_CREEP = prev


@contextlib.contextmanager
def _disability_ctx(
    cfg: dict, seed: int, path_index: Optional[int] = None,
):
    """Install one path's SSA-award stress on an independent stable stream."""
    raw = cfg.get("disability") or {}
    if not raw.get("enabled"):
        yield
        return
    _validate_disability(cfg)
    params = _mk(DisabilityParams, {
        k: v for k, v in raw.items() if k != "rng"})
    params.rng = np.random.default_rng(
        int(seed) + DISABILITY_SEED_OFFSET if path_index is None
        else [int(seed), int(path_index), DISABILITY_SEED_OFFSET])
    state = cfg.get("state") or {}
    event = sample_ssdi_entitlement(
        params,
        int(state.get("start_age", State().start_age)),
        int(state.get("accum_years", State().accum_years)),
        str((cfg.get("mortality") or {}).get("sex")),
    )
    prev = fire_v8_model._DISABILITY
    fire_v8_model._DISABILITY = event
    try:
        yield
    finally:
        fire_v8_model._DISABILITY = prev


@contextlib.contextmanager
def _career_break_ctx(cfg: dict):
    """Set `fire_v8_model._CAREER_BREAK` for the run, then restore it.

    Deterministic, so unlike `_layoff_ctx` there is no generator to attach and
    no per-path variant: the same compiled break applies to every path, which
    is exactly what makes it a plan rather than a risk.

    Compiled once here against the plan's own `state.start_age`, so the
    age->year arithmetic happens at the boundary instead of inside the year
    loop. Off => None => the engine runs the code it ran before B10 existed.
    """
    params = _mk(CareerBreakParams, (cfg.get("career_break") or {}))
    start_age = int((cfg.get("state") or {}).get("start_age", 27) or 27)
    prev = fire_v8_model._CAREER_BREAK
    fire_v8_model._CAREER_BREAK = compile_career_break(params, start_age)
    try:
        yield
    finally:
        fire_v8_model._CAREER_BREAK = prev


@contextlib.contextmanager
def _spouse_career_break_ctx(cfg: dict):
    """Set `fire_v8_model._SPOUSE_CAREER_BREAK`, then restore it.

    Compiled against the SPOUSE's own start age -- the primary's start age
    plus `household.spouse_age_offset` -- because the control asks when the
    spouse steps back, in the spouse's years. Compiling it against the
    primary's clock would silently move the break by the age gap.
    """
    block = cfg.get("spouse_career_break") or {}
    prev = fire_v8_model._SPOUSE_CAREER_BREAK
    compiled = None
    if block.get("enabled") and (cfg.get("household") or {}).get("enabled"):
        params = _mk(CareerBreakParams, block)
        start_age = (int((cfg.get("state") or {}).get("start_age", 27) or 27)
                     + int((cfg.get("household") or {}).get(
                         "spouse_age_offset", 0) or 0))
        compiled = compile_career_break(params, start_age)
    fire_v8_model._SPOUSE_CAREER_BREAK = compiled
    try:
        yield
    finally:
        fire_v8_model._SPOUSE_CAREER_BREAK = prev


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


def _housing_accumulation_adjustments(cfg: dict) -> tuple:
    """Net deterministic housing cost for each working-year waterfall."""
    state = cfg.get("state") or {}
    start_age = int(state.get("start_age", State().start_age))
    years = int(state.get("accum_years", State().accum_years))
    by_age = {}
    for age, amount in HOUSING.compile_housing_events(
            cfg, include_mortgage=True, include_carry=True):
        if start_age < int(age) <= start_age + years:
            by_age[int(age)] = by_age.get(int(age), 0.0) + float(amount)
    return tuple(by_age.get(start_age + year, 0.0)
                 for year in range(1, years + 1))


@contextlib.contextmanager
def _housing_accumulation_ctx(cfg: dict):
    prev = fire_v8_model._ACCUMULATION_HOUSING_ADJUSTMENTS_REAL
    compiled = _housing_accumulation_adjustments(cfg)
    fire_v8_model._ACCUMULATION_HOUSING_ADJUSTMENTS_REAL = (
        compiled if any(abs(value) > 1e-12 for value in compiled) else ())
    try:
        yield
    finally:
        fire_v8_model._ACCUMULATION_HOUSING_ADJUSTMENTS_REAL = prev


@contextlib.contextmanager
def _tax_posture_ctx(cfg: dict):
    """Let the WORKING years see the state tax the retirement years already see.

    Roadmap 10.0, OPEN_ITEMS E30. `TrueTaxParams` is built exactly once and
    handed only to the retirement leg, so a plan with a state archetype paid
    that state for half a life and nothing for the other half. This carries
    the SAME two existing leaves -- no new configuration, no new attribution
    leaf, no registry entry.

    Gated on `tax_true.enabled`, because that is the only gate the retirement
    state tax has. Ungated, a plan with the true-tax engine off but a state
    rate set would pay state tax while working and none while retired, which
    is the same asymmetry pointing the other way.
    """
    block = cfg.get("tax_true") or {}
    prev = fire_v8_model._TAX_POSTURE
    fire_v8_model._TAX_POSTURE = (
        _mk(TrueTaxParams, block) if block.get("enabled") else None)
    try:
        yield
    finally:
        fire_v8_model._TAX_POSTURE = prev


def estimate_first_year_savings(cfg: dict) -> dict:
    """What this plan actually saves in its first working year.

    OPEN_ITEMS E33. The wizard sidebar used to answer this in the page, with
    its own arithmetic that filled three accounts to their limits and applied
    one flat rate. Measured before this existed: at $45,000 the page said the
    household saved 87% of gross -- and added a note congratulating them for
    beating almost everybody -- while the engine had them $3,662 A YEAR SHORT
    of covering their spending. Worse, in `savings_mode="savings_rate"` the
    page returned the same number whether the user typed 10%, 30% or 50%,
    because that function never read `savings_mode` at all.

    So this does not compute a savings figure. It asks the ENGINE for the one
    it is already going to use, down the same path a real run takes: refusal
    at the mapping stage, `build_kwargs`, and the same process-wide hooks
    under the same lock. Skipping the lock would be worse than the bug being
    fixed -- `compute_contributions_for_year` reads module globals, so an
    estimate racing a running simulation would read that run's household.

    **The assumptions are deterministic and travel with the answer**, because
    a single number with hidden draws behind it is how the page got into this
    in the first place:

      * year 1, and no promotion in it -- promotion timing is sampled, and
        its earliest configurable year is checked rather than assumed;
      * the bonus at the EXPECTED value of its own distribution: the fixed
        percentage in `fixed` mode, the midpoint in `uniform` mode, which is
        exactly the mean of the draw the engine makes;
      * both people alive, no career break, no layoff.

    `savings` can be NEGATIVE, and that is the finding rather than a bug: it
    means the plan does not cover its own spending. It is never rounded up to
    zero, and the caller must not either.
    """
    check_config(cfg)                 # refusal at the mapping stage, lesson 1
    kw = build_kwargs(cfg, False)
    promo = kw["promo_params"]
    state_params = kw["state"]
    # The bonus the engine would draw, at its own expected value.
    if getattr(promo, "bonus_mode", "uniform") == "fixed":
        bonus_pct = float(promo.bonus_pct_fixed)
    else:
        bonus_pct = (float(promo.bonus_pct_min)
                     + float(promo.bonus_pct_max)) / 2.0
    # A promotion in year 1 is possible only if the plan is configured for
    # one. Checked rather than assumed away: `timing_min` defaults to 2, but
    # a plan may set `timing_mode="fixed", timing_fixed=1`.
    promotion_year = None
    if getattr(promo, "enabled", False):
        if getattr(promo, "timing_mode", "") == "fixed":
            promotion_year = int(promo.timing_fixed)
        elif int(getattr(promo, "timing_min", 2)) <= 1:
            promotion_year = 1
    household = cfg.get("household") or {}
    # `pool_household_expenses=False`, and that is measured rather than
    # careless. The real loop passes `alive_by_year is not None`, which for a
    # both-alive year selects a branch that computes the SAME
    # `household_taxable` -- 175 household grids over income, spouse income
    # and spending, zero differences in the figure this returns. The branches
    # differ only in the meta they write, which this no longer reads. The
    # assertion that keeps that true is in
    # `tests/test_savings_estimate.py`, so the day the branches diverge this
    # stops being safe out loud rather than silently.
    with (_ENGINE_LOCK, match_excludes_bonus(), _household_ctx(cfg),
          _housing_accumulation_ctx(cfg), _career_break_ctx(cfg),
          _spouse_career_break_ctx(cfg), _tax_posture_ctx(cfg)):
        stack = fire_v8_model.compute_contributions_for_year(
            1, promotion_year, bonus_pct, promo.base_salary_post,
            kw["contrib_params"], promo,
            pool_household_expenses=False,
            break_multiplier=1.0, meta_out={},
            age=int(state_params.start_age),
            student_debt_payment_nominal=(
                kw["student_debt"].payment_for_year(0)
                if kw.get("student_debt") is not None else None),
            student_debt_embedded_payment_nominal=(
                kw["student_debt"].monthly_payment_nominal * 12.0
                if kw.get("student_debt") is not None else 0.0))

    contributions = kw["contrib_params"]
    gross = (float(contributions.base_salary_pre)
             + float(contributions.ot_income_pre)
             + float(contributions.bonus_pre))
    if household.get("enabled"):
        gross += (float(household.get("spouse_base_salary_pre", 0) or 0)
                  + float(household.get("spouse_bonus_pre", 0) or 0))
    # DERIVED from the answer, not read from the engine's meta. Measured: on
    # 57 of 560 grids the meta `expense_shortfall` is the PRIMARY earner's
    # gap reported as if it were the household's -- one case has the meta
    # saying $20,000 short while the household is saving $14,320. That number
    # next to a savings figure would be a new false alarm, in a slice whose
    # whole point is removing one. See OPEN_ITEMS E38 for the meta itself,
    # which the career-break comparison also reads.
    shortfall = max(0.0, -float(stack.total))
    return {
        "savings": float(stack.total),
        "gross": gross,
        # `None`, not zero, when there is no income to divide by: a rate of
        # 0% would read as "saves nothing" rather than "no answer".
        "savings_rate": (float(stack.total) / gross) if gross > 0 else None,
        "expense_shortfall": shortfall,
        "by_account": stack.balances(),
        "assumptions": {
            "year": 1,
            "bonus_pct": bonus_pct,
            "promotion_in_year_one": promotion_year == 1,
        },
    }


def _run(cfg: dict, n: int, seed: int, relocation_on: bool,
         mu_shift: Optional[float] = None, cb=None,
         execution_policy=None, per_path_substreams: bool = False) -> list:
    """Sequential shared-stream Monte Carlo — byte-identical to
    run_lifecycle_mc_v98(per_path_substreams=False), but with an optional
    progress callback cb(frac in [0,1)) invoked periodically for the progress
    bar. Same rng draw order => same results with or without cb.

    ``per_path_substreams`` is an opt-in B4 comparison protocol.  Each path
    then owns main, LTC, parent and layoff streams derived from its index, so a
    shorter retirement in path i cannot change the sampled life at i+1.  The
    official/default runner stays on its historical shared stream.
    """
    kw = build_kwargs(cfg, relocation_on)
    config = kw.pop("config")
    if execution_policy is not None:
        kw["execution_policy"] = execution_policy
    # Return posture: the user's `returns.equity_mu_shift` moves the whole regime
    # mixture's mean; the sensitivity μ-band's own shift composes on top of it, so
    # the band explores μ *around the user's chosen central assumption*.
    base_shift = float((cfg.get("returns") or {}).get("equity_mu_shift", 0.0) or 0.0)
    total_shift = base_shift + (mu_shift or 0.0)
    if abs(total_shift) > 1e-12:
        kw["regimes"] = _shifted_regimes(total_shift)
    # Long-term care runs on its own generator (seed + 11_000_000), consumed
    # sequentially across this run's paths — the layoff career stream's
    # convention, and the reason turning LTC on cannot shift a single draw of
    # the shared stream. Chunked runs give chunk i seed+i, so each chunk gets a
    # distinct care stream for the same reason its market stream is distinct.
    # Off attaches nothing at all: there is no generator to advance.
    _ltc = kw.get("ltc")
    if (not per_path_substreams
            and _ltc is not None and _ltc.mode != LTC.OFF):
        _ltc.rng = np.random.default_rng(int(seed) + 11_000_000)
    # Parents get their own stream too, and a different offset, so turning the
    # parent module on cannot shift a single draw of the care module's stream
    # any more than it can shift the shared one. Chunked runs give chunk i
    # seed+i, so each chunk's parent stream is distinct for the same reason its
    # market stream is. Off attaches nothing at all.
    _parents = kw.get("parents")
    if (not per_path_substreams
            and _parents is not None and _parents.mode != PARENTS.OFF):
        _parents.rng = np.random.default_rng(int(seed) + 13_000_000)
    n = int(n)
    out = []
    step = max(1, n // 40)
    # _ENGINE_LOCK: match_excludes_bonus/_household_ctx set process-wide globals
    # in the engine chain; the HTTP server is threaded (background job + on-demand
    # sweep/sensitivity/backtest requests), so engine runs must be serialized or
    # concurrent runs with different settings silently corrupt each other (audit P1-1).
    if not per_path_substreams:
        # Keep this branch structurally identical to the official runner.  B4
        # opting in must not silently migrate headline/replay RNG semantics.
        rng = np.random.default_rng(int(seed))
        with (_ENGINE_LOCK, match_excludes_bonus(), _household_ctx(cfg),
              _housing_accumulation_ctx(cfg), _career_break_ctx(cfg),
              _spouse_career_break_ctx(cfg), _tax_posture_ctx(cfg),
              _layoff_ctx(cfg, seed), _spouse_layoff_ctx(cfg, seed),
              _lifestyle_creep_ctx(cfg, seed),
              _event_meta_ctx()):
            for i in range(n):
                # Disability incidence is path-specific even in the legacy
                # shared-market runner. Its own indexed child stream keeps it
                # independent without consuming or shifting market/layoff.
                with _disability_ctx(cfg, seed, path_index=i):
                    result = simulate_lifecycle_v98(
                        config=config, rng=rng, **kw)
                out.append(_annotate_result(
                    result, cfg, kw.get("life_events")))
                if cb is not None and i % step == 0:
                    cb(i / n)
        return out

    with (_ENGINE_LOCK, match_excludes_bonus(), _household_ctx(cfg),
          _housing_accumulation_ctx(cfg), _career_break_ctx(cfg),
          _spouse_career_break_ctx(cfg), _tax_posture_ctx(cfg),
          _event_meta_ctx()):
        for i in range(n):
            # Auxiliary stochastic modules carry their generator on the
            # parameter object, so resetting only the main rng is not path
            # isolation.  All four domains include the same path index and a
            # stable domain tag; both B4 arms reconstruct these exact streams.
            if _ltc is not None and _ltc.mode != LTC.OFF:
                _ltc.rng = np.random.default_rng(
                    [int(seed), int(i), 11_000_000])
            if _parents is not None and _parents.mode != PARENTS.OFF:
                _parents.rng = np.random.default_rng(
                    [int(seed), int(i), 13_000_000])
            rng = np.random.default_rng([int(seed), int(i)])
            with (_layoff_ctx(cfg, seed, path_index=i),
                  _spouse_layoff_ctx(cfg, seed, path_index=i),
                  _lifestyle_creep_ctx(cfg, seed, path_index=i),
                  _disability_ctx(cfg, seed, path_index=i)):
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
    """The flat liquidation proxy, frozen.

    Production no longer uses this -- `_terminal_values` below replaced it on
    2026-08-14 -- but `ROTH_OPTIMIZER_CONTRACT_AUDIT_2026-08-01.json` measured
    THIS function, and its numbers are evidence. Redefining what the audit
    measured would silently change what that evidence refers to, so it stays
    exactly as it was: the whole taxable bucket taxed at the flat rate, which
    is the same answer `_terminal_values` gives for a liquidation with 100%
    gain and no basis.
    """
    if accounts is None:
        return 0.0
    tr = min(max(float(getattr(tax_us, "withdrawal_tax_traditional", 0.0)), 0.0), 1.0)
    tx = min(max(float(getattr(tax_us, "withdrawal_tax_taxable", 0.0)), 0.0), 1.0)
    return max(0.0, (float(accounts.pretax_401k) * (1.0 - tr)
                     + float(accounts.taxable) * (1.0 - tx)
                     + float(accounts.roth_ira) + float(accounts.hsa)))


def _terminal_values(accounts, tax_us, basis_end, gain_fraction_fallback):
    """What is left at the end, priced two ways. Returns (bequest, liquidated).

    The old single figure taxed the WHOLE taxable bucket at the flat taxable
    rate, which is wrong twice over: it taxes principal that was already taxed,
    and it assumes a final-year sale of money that is by definition a bequest.
    The user's 2026-08-14 ruling is two numbers with the bequest one as the
    headline.

    **Bequest (step-up).** US law steps the basis of taxable assets up at
    death, so the unrealised gain is never taxed. A pretax 401(k) gets NO
    step-up -- heirs pay ordinary income tax on distributions -- so it keeps
    its haircut. Roth and HSA are unchanged from the old proxy.

    **Liquidated.** Everything sold in the final year: only the GAIN is taxed,
    not the principal, which is the part cost-basis tracking made knowable.

    `basis_end` is `None` when the true-tax engine was off and no basis was
    tracked. Rather than invent one, the configured `taxable_gain_fraction`
    proxy is used and the caller is told the number is a proxy -- the same
    fallback the solver already uses for callers that do not track basis.
    """
    if accounts is None:
        return 0.0, 0.0
    tr = min(max(float(getattr(tax_us, "withdrawal_tax_traditional", 0.0)), 0.0), 1.0)
    tx = min(max(float(getattr(tax_us, "withdrawal_tax_taxable", 0.0)), 0.0), 1.0)
    taxable = max(0.0, float(accounts.taxable))
    untaxed = float(accounts.roth_ira) + float(accounts.hsa)
    pretax_after = float(accounts.pretax_401k) * (1.0 - tr)
    if basis_end is None:
        gain = taxable * min(max(float(gain_fraction_fallback), 0.0), 1.0)
    else:
        gain = max(0.0, taxable - max(0.0, float(basis_end)))
    bequest = pretax_after + taxable + untaxed
    liquidated = pretax_after + (taxable - gain * tx) + untaxed
    return max(0.0, bequest), max(0.0, liquidated)


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
    _wd = r.get("withdrawal") or {}
    _bequest, _liquidated = _terminal_values(
        accounts, _mk_tax_us(cfg.get("tax_us") or {}),
        _wd.get("taxable_basis_end"),
        (cfg.get("tax_true") or {}).get("taxable_gain_fraction",
                                        TrueTaxParams().taxable_gain_fraction))
    _deflate = max(cpi, 1e-9)
    # The headline is the bequest figure, per the ruling: money left at the end
    # of a plan is an inheritance, not a final-year sale.
    r["terminal_after_tax_real"] = _bequest / _deflate if successful else 0.0
    r["terminal_liquidated_real"] = _liquidated / _deflate if successful else 0.0
    # Whether the gain above was measured or taken from the configured proxy.
    r["terminal_basis_measured"] = _wd.get("taxable_basis_end") is not None
    return r


def _path_stats(results: list, milestones: list) -> dict:
    """Reduce raw per-path dicts to scalar columns (official chunked_runner
    semantics) plus milestone-crossing ages and the cash-conservation residual."""
    cols = {k: [] for k in (
        "fire_age", "reached", "died_accum", "success", "post_fire_success",
        "cons", "term_nom", "term_real", "ss", "min_cons", "lifestyle",
        "true_tax", "true_tax_nominal", "terminal_after_tax_real",
        "terminal_liquidated_real",
        # Rich / Broke / Dead. The three-branch success definition is the
        # thing users misread most -- "dying before the money runs out counts
        # as success" is true and sounds like a trick until you can see the
        # three groups laid out by age. Every value here is already computed
        # per path by the retirement simulator; nothing new is being modelled
        # and no RNG draw moves. Collecting them is all that was missing.
        "outcome", "outcome_age",
        # The lifetime regime this path drew. ROADMAP asks for failure rates
        # stratified by starting valuation; this engine has NO valuation
        # input -- every path starts from the same portfolio under the same
        # assumptions, and what differs is the sampled world. One of the three
        # sampled worlds is literally `highCAPE`, so conditioning on the
        # regime is the honest neighbour of what was asked for, and the panel
        # says which one it is rather than letting "CAPE" imply an input.
        "regime",
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
        # Rich / Broke / Dead, for EVERY path.
        #
        # The first version of this sat inside the reached-FI branch, so the
        # column was shorter than the others and any share computed against
        # `n` would have been wrong -- silently, and in the direction that
        # flatters. Four outcomes rather than three, because a path that never
        # reached FI is none of the retirement three and calling it `alive`
        # would count a plan that never started as one that survived.
        #
        # Order matters where a path both went broke and later died: it is
        # BROKE. The shortfall is the thing the plan got wrong, and filing it
        # under `dead` hides a failure behind an outcome nobody controls.
        cols["regime"].append(r.get("regime"))
        _wdr = r.get("withdrawal") or {}
        _short = _wdr.get("shortfall_age")
        _death_r = _wdr.get("age_at_death")
        if not reached:
            cols["outcome"].append("dead_in_accumulation" if died
                                   else "never_reached_fi")
            cols["outcome_age"].append(
                int(r["age_at_death"]) if died and r.get("age_at_death")
                is not None else None)
        elif _short is not None:
            cols["outcome"].append("broke")
            cols["outcome_age"].append(int(_short))
        elif _wdr.get("died_during_retirement") and _death_r is not None:
            cols["outcome"].append("dead")
            cols["outcome_age"].append(int(_death_r))
        else:
            cols["outcome"].append("alive")
            cols["outcome_age"].append(None)
        cols["success"].append(1.0 if r.get("lifetime_success") else 0.0)
        cols["event_shortfall"].append(1.0 if r.get("event_shortfalls") else 0.0)
        cols["terminal_after_tax_real"].append(
            float(r.get("terminal_after_tax_real") or 0.0))
        cols["terminal_liquidated_real"].append(
            float(r.get("terminal_liquidated_real") or 0.0))

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


def _estate_exemption(cfg: dict):
    """The user's exemption, or None. One reader, so the four call sites
    cannot disagree about where it lives."""
    return (cfg.get("estate") or {}).get("exemption_real", None)


def _estate_exposure(term_real: list, exemption) -> dict:
    """The fraction of paths ending above a USER-SUPPLIED exemption.

    ROADMAP 4.0 Phase 4: "terminal distribution notes the fraction of paths
    above that year's exemption + advises a professional; MODELLING IT IS
    PERMANENTLY OUT OF SCOPE". So this counts and says so; nothing anywhere
    computes an estate tax.

    Counted EXACTLY over the full headline path array, not read off the
    terminal histogram. That histogram is built from the illustrative
    distribution -- 1,500 paths against the headline's 100,000 -- and it is
    binned, so a fraction taken from it would be a smaller sample reported to
    a precision the bins cannot carry. Both wrongnesses would be invisible.

    Unset exemption gives `applicable: False` with a reason. A default would
    put this app's guess at a legislated number in front of someone as though
    it were measured, and the figure moves with law while the app makes no
    network requests.
    """
    if exemption is None:
        return {"applicable": False,
                "reason": "no exemption entered; this app does not carry tax "
                          "tables and will not guess a legislated figure"}
    values = [v for v in (term_real or []) if v is not None]
    if not values:
        return {"applicable": False,
                "reason": "no terminal values to count"}
    threshold = float(exemption)
    above = sum(1 for v in values if float(v) > threshold)
    return {
        "applicable": True,
        "exemption_real": threshold,
        "paths_above": above,
        "paths_counted": len(values),
        "fraction_above": above / len(values),
        "modelled": False,
        "note": ("Estate tax is NOT modelled anywhere in this app and never "
                 "will be: no exemption indexing, no portability, no state "
                 "estate or inheritance tax, no trust or gifting structure. "
                 "This is a count of paths, not a tax calculation. If this "
                 "fraction is not small, that is a question for a professional."),
    }


def _regime_conditional(cols: dict, n: int) -> dict:
    """How the plan did inside each sampled world, rather than on average.

    ROADMAP asks for "conditional failure rates stratified by starting
    valuation (high/mid/low CAPE), presented rather than applied as a rule,
    with no default". Two of those three requirements are straightforward.
    The third needs saying out loud: **this engine has no valuation input.**
    Every path starts from the same portfolio under the same assumptions, so
    there is no starting CAPE to stratify by.

    What it does have is a lifetime regime drawn per path, and one of the
    three is `highCAPE`. Conditioning on that answers the question the
    stratification was for -- "is my result carried by having drawn a
    favourable world?" -- and it is a different question from the one the
    roadmap's words describe. The payload says which, because a reader who
    thinks they are seeing "your plan at today's valuation" is reading
    something this app cannot compute.

    Presented, never applied: nothing here changes a rule or a default.
    """
    regimes = cols.get("regime") or []
    success = cols.get("success") or []
    if len(regimes) != n or len(success) != n:
        return {"applicable": False,
                "reason": ("the regime column covered %d of %d paths, so a "
                           "conditional rate would be wrong"
                           % (len(regimes), n))}
    buckets = {}
    for name, ok in zip(regimes, success):
        row = buckets.setdefault(name or "unlabelled",
                                 {"paths": 0, "successes": 0})
        row["paths"] += 1
        row["successes"] += 1 if ok else 0
    out = {}
    for name, row in buckets.items():
        out[name] = {
            "paths": row["paths"],
            "share_of_paths": row["paths"] / n if n else None,
            #: `None` rather than 1.0 for an empty bucket: a rate over zero
            #: paths is not a perfect rate, it is no rate.
            "success_rate": (row["successes"] / row["paths"]
                             if row["paths"] else None),
        }
    return {
        "applicable": True,
        "n_paths": n,
        "regimes": out,
        "is_starting_valuation": False,
        "basis": ("These are the sampled market worlds, not a valuation you "
                  "entered -- this model has no CAPE input, and every path "
                  "starts from the same portfolio. `highCAPE` is one of the "
                  "worlds the sampler can draw, so this answers 'how did the "
                  "plan do when the world was unfavourable' rather than 'how "
                  "does it do at today's valuation', which this app cannot "
                  "compute. Shown, never applied as a rule."),
    }


def _outcome_layers(cols: dict, n: int) -> dict:
    """Rich / Broke / Dead, by age, so the three-branch rule is visible.

    ROADMAP's presentation item. "Dying before the money runs out counts as
    success" is true, is the standard definition, and reads as a trick until
    somebody can see the groups laid out -- at which point it is obviously
    right, because the alternative counts every death as a failure.

    Shares are over EVERY path, and the count is checked rather than assumed:
    a column shorter than `n` divides by the wrong denominator, and it does it
    in the flattering direction. The first version of the collection sat
    inside the reached-FI branch and would have done exactly that.
    """
    outcomes = cols.get("outcome") or []
    ages = cols.get("outcome_age") or []
    if len(outcomes) != n or len(ages) != n:
        return {"applicable": False,
                "reason": ("the outcome column covered %d of %d paths, so a "
                           "share of it would be wrong" % (len(outcomes), n))}
    layers = {}
    for name, age in zip(outcomes, ages):
        row = layers.setdefault(name, {"paths": 0, "ages": []})
        row["paths"] += 1
        if age is not None:
            row["ages"].append(int(age))
    out = {}
    for name, row in layers.items():
        ages_sorted = sorted(row["ages"])
        out[name] = {
            "paths": row["paths"],
            "share": row["paths"] / n if n else None,
            #: `None`, not 0, when a layer has no ages: an `alive` path has no
            #: outcome age because nothing happened to it, which is a
            #: different statement from "it happened at age 0".
            "median_age": (ages_sorted[len(ages_sorted) // 2]
                           if ages_sorted else None),
            "earliest_age": ages_sorted[0] if ages_sorted else None,
        }
    return {
        "applicable": True,
        "n_paths": n,
        "layers": out,
        "basis": ("Every path lands in exactly one layer. `alive` reached "
                  "retirement and stayed solvent; `broke` ran out at the age "
                  "shown; `dead` ended before the money did, which this model "
                  "counts as success; `never_reached_fi` never retired at "
                  "all, which is none of the three."),
    }


def _summarize(st: dict, milestones: list, exemption=None) -> dict:
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

    out = {
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
        "terminal_liquidated_real": _p3(c["terminal_liquidated_real"], truthy=False),
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
    # ONLY when the user asked for it. Emitting it unconditionally put a new
    # key in `home` for every plan, which broke four recorded contracts that
    # pin `home` as bit-identical while a module is off -- this project's
    # oldest standing rule, and mine to follow like everyone else's. An
    # analysis nobody requested should leave no trace in the payload, and its
    # absence is unambiguous in a way `applicable: False` is not: the key is
    # simply not there.
    if exemption is not None:
        out["estate_exposure"] = _estate_exposure(c.get("term_real"), exemption)
    return out


# ============================================================
# PUBLIC: headline scenarios
# ============================================================
def run_scenarios(cfg: dict, n_paths: int, seed: int) -> dict:
    """Home (relocation forced off) + optional relocation, each summarized in the
    exact shape the dashboard reads."""
    ms = _milestones(cfg)
    reloc_on = bool((cfg.get("relocation") or {}).get("enabled", False))

    exemption = _estate_exemption(cfg)
    home = _summarize(_path_stats(_run(cfg, n_paths, seed, False), ms), ms,
                      exemption)
    out = {"meta": {"name": cfg.get("name", "FIRE plan")}, "home": home}
    if reloc_on:
        out["relocation"] = _summarize(
            _path_stats(_run(cfg, n_paths, seed, True), ms), ms, exemption)
    return out


# ============================================================
# CHUNKED PARALLEL RUNNER (official 1.5M protocol: seeds seed+idx)
# ============================================================
# Fixed chunk size => results depend only on (paths, seed), never on how many
# cores the machine has. Workers reduce to _path_stats before returning, so
# IPC carries compact columns, not raw paths. Used only for large summary runs.
MP_CHUNK = 5_000
#: Above this, a run is chunked across processes. Lowered from 20,000 to
#: 5,000 on 2026-08-16 (Roadmap 5.0 Phase 0) because Standard -- 10,000 paths,
#: the tier archived runs and decision packets require -- sat just under it
#: and ran single-core: 10.0-10.7s against 5.9-6.2s for TWICE the work at
#: 20,000, measured four times on an idle machine.
#:
#: Moving it changes results for a given seed, because chunk i uses seed + i.
#: That is safe only because `run_full` now takes the layout as an input and
#: `replay_snapshot` passes the one each snapshot recorded --
#: `test_execution_mode_replay` is the gate on that, and this constant must
#: not move again without it passing.
MP_THRESHOLD = 5_000

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


def _run_chunked_stats(cfg: dict, n: int, seed: int, reloc: bool, cb=None,
                       *, chunk_size: Optional[int] = None) -> dict:
    """Parallel chunked run returning MERGED _path_stats. Deterministic for a
    given (n, seed): chunk layout is fixed (MP_CHUNK), chunk i uses seed+i —
    the same protocol family as the official 1.5M baseline (seeds 96000+idx).
    Worker count only affects wall-clock time."""
    sizes = []
    left = int(n)
    while left > 0:
        take = min(int(chunk_size or MP_CHUNK), left)
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
        started = _time.monotonic()
        last_progress = started
        last_sum = 0.0
        # Two windows, not one. Before any worker has reported, the pool is
        # still booting: a fresh interpreter each, importing numpy and this
        # chain. After a worker has reported, silence means something stopped.
        # Measured on a 10-core Mac at load average 16.6, that bootstrap takes
        # about one second -- so the old flat 120s was 120x the real cost and
        # still fired, which means the abort it produced was never actually
        # about slowness. Splitting the windows does not explain that; it makes
        # the next occurrence say which half it happened in, and gives a busy
        # machine room in the half where waiting is legitimate.
        boot_grace = float(os.environ.get("FIRE_MP_BOOTSTRAP_GRACE") or 600)
        stall_limit = float(os.environ.get("FIRE_MP_STALL_TIMEOUT") or 120)
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
            if not got:
                idle = _time.monotonic() - last_progress
                booting = last_sum <= 0.0
                if idle > (boot_grace if booting else stall_limit):
                    phase = ("no worker reported in %.0fs; the pool never "
                             "started producing" % idle) if booting else (
                             "workers stopped after %.0f%% in %.0fs"
                             % (100.0 * last_sum / total, idle))
                    raise RuntimeError(
                        "parallel chunked run aborted — %s "
                        "(workers=%d chunks=%d elapsed=%.0fs). Raise "
                        "FIRE_MP_BOOTSTRAP_GRACE or FIRE_MP_STALL_TIMEOUT if "
                        "this machine is simply slow; a bootstrap that never "
                        "reports is usually a worker dying at import."
                        % (phase, workers, len(sizes),
                           _time.monotonic() - started))
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


def parse_execution_mode(mode: Optional[str]) -> Optional[tuple]:
    """`"chunked-4x5000"` -> `(4, 5000)`; `"sequential"` -> `None`.

    A recorded mode is a LAYOUT, not a preference. `_run_chunked_stats` gives
    chunk i the seed `seed + i`, so the number and size of chunks decide the
    numbers -- two runs of the same (n, seed) under different layouts are
    different results, correctly. Replaying a snapshot therefore has to
    reproduce the layout it recorded, not today's.
    """
    if not mode or mode == "sequential":
        return None
    if not isinstance(mode, str) or not mode.startswith("chunked-"):
        raise ValueError("unknown execution mode %r" % (mode,))
    try:
        count, size = mode[len("chunked-"):].split("x", 1)
        return int(count), int(size)
    except (ValueError, TypeError):
        raise ValueError("malformed execution mode %r" % (mode,)) from None


def run_full(cfg: dict, n_paths: int, seed: int, dist_paths: int, cb=None,
             *, execution_mode: Optional[str] = None) -> dict:
    """The full headline payload (summaries + illustrative distributions) with a
    single combined progress callback cb(pct in [0,1], stage_key). Runs home,
    then relocation (if enabled), then their distribution samples — reporting a
    smooth global percentage across all phases (for the progress bar).

    `execution_mode` forces the LAYOUT rather than letting `MP_THRESHOLD`
    decide it. `None` keeps today's behaviour; a recorded mode string replays
    the layout a snapshot was computed under.

    This exists because the threshold cannot otherwise be changed. Chunk i
    uses `seed + i`, so moving the threshold changes the numbers for a given
    seed, and `replay_snapshot` refuses on an execution-mode mismatch -- every
    archived snapshot would stop replaying. With the layout passed in, old
    snapshots replay as recorded and new runs use whatever threshold is
    current."""
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
            forced = parse_execution_mode(execution_mode)
            if execution_mode == "sequential":
                # Forced sequential: a snapshot recorded before the threshold
                # moved. Without this branch it would replay chunked and the
                # numbers would differ, which `replay_snapshot` reports as a
                # mode mismatch rather than as a wrong result.
                st = _path_stats(_run(cfg, count, seed, reloc, cb=pc), ms)
            elif forced is not None:
                chunks, size = forced
                st = _run_chunked_stats(cfg, count, seed, reloc, cb=pc,
                                        chunk_size=size)
                out["mode"] = f"chunked-{chunks}x{size}"
            elif count >= MP_THRESHOLD:
                st = _run_chunked_stats(cfg, count, seed, reloc, cb=pc)
                out["mode"] = f"chunked-{-(-count // MP_CHUNK)}x{MP_CHUNK}"
            else:
                st = _path_stats(_run(cfg, count, seed, reloc, cb=pc), ms)
            out["relocation" if reloc else "home"] = _summarize(
                st, ms, _estate_exemption(cfg))
            # Rich / Broke / Dead goes under `meta`, NOT into `home`.
            #
            # `home`'s key set is pinned by `test_forecast_period_statistics`
            # with an exact `assertEqual`, and four more suites hold a
            # recorded `home` bit-identical while a module is off. A new key
            # there means re-recording five pieces of evidence for a
            # presentation feature, which is far too much pinned proof to
            # disturb for a derived view.
            #
            # `meta` is where derived presentation already lives -- this is
            # the same call `sampling_error` needed earlier today, for the
            # same reason: it is not part of the engine's numeric contract.
            out["meta"].setdefault("outcome_layers", {})[
                "relocation" if reloc else "home"] = _outcome_layers(
                    st["cols"], st["n"])
            out["meta"].setdefault("regime_conditional", {})[
                "relocation" if reloc else "home"] = _regime_conditional(
                    st["cols"], st["n"])
        else:
            dist["relocation" if reloc else "home"] = lifecycle_sample(
                cfg, count, seed, reloc, cb=pc)
        acc[0] += count
    out["dist"] = dist
    if cb:
        cb(1.0, "done")
    return out


def bequest_dependency(cfg: dict, n: int, seed: int, *,
                       material_drop: float = 0.10,
                       success_threshold: float = 0.90) -> dict:
    """Does this plan only work because somebody dies?

    ROADMAP asks for this by name, and the parent module is why it is needed: a
    bequest is an inflow, so switching that module ON usually makes a plan look
    BETTER, and a number that improved because of an inheritance reads exactly
    like a number that improved because the plan is sound.

    Two runs at the SAME seed, differing in one thing: whether the bequest is
    credited. Care cost is untouched in both, because the question is "what if
    the money never reaches me" — zeroing the estate instead would also stop
    the parent funding their own care and answer something nobody asked.

    **Keyed on consumption, not on a success rate, and that was a measurement
    rather than a preference.** The first version of this compared
    `lifetime_success` against a threshold. On this engine that flag could
    never fire: the withdrawal rule cuts spending in bad years, so post-FIRE
    solvency sits at 1.000 and a lost inheritance does not produce failures —
    measured, dropping a $3M bequest moved `lifetime_success` by exactly 0.0000
    while median real consumption fell from $86,276 to $66,029, a 23% cut. A
    check that can only ever answer "not dependent" is worse than no check,
    because it reports safety it cannot see. So the flag reports what actually
    moves, and says out loud that survival is unchanged **because the plan
    absorbs the loss by spending less** — otherwise an unchanged success rate
    reads as "the inheritance does not matter", which is backwards.

    Every field is `None` when it was not measured. `depends_on_bequest` is
    never `False` by default: with the module off there is no bequest to depend
    on, and "not applicable" is a different fact from "checked, and no".
    """
    unmeasured = {"applicable": False, "depends_on_bequest": None,
                  "with_bequest": None, "without_bequest": None,
                  "consumption_drop": None, "consumption_drop_pct": None,
                  "success_unchanged": None,
                  "material_drop": float(material_drop),
                  "reason": "no parent lifecycle module is on, so this plan "
                            "has no modelled bequest to depend on"}
    parents_block = (cfg.get("parents") or {})
    if str(parents_block.get("mode", PARENTS.OFF) or PARENTS.OFF) == PARENTS.OFF:
        return unmeasured

    counterfactual = copy.deepcopy(cfg)
    counterfactual["parents"] = {**dict(parents_block),
                                 "assume_zero_bequest": True}
    with_b = summary(cfg, n, seed)
    without_b = summary(counterfactual, n, seed)

    cons_with = float(with_b["cons_p50"])
    cons_without = float(without_b["cons_p50"])
    drop = cons_with - cons_without
    drop_pct = (drop / cons_with) if cons_with else 0.0
    success_fell = (with_b["lifetime_success"] >= success_threshold
                    and without_b["lifetime_success"] < success_threshold)
    depends = bool(drop_pct >= material_drop or success_fell)

    if success_fell:
        reason = ("this plan clears %.0f%% only because it inherits: %.1f%% "
                  "with the bequest and %.1f%% without. Plan on the second "
                  "number — an inheritance is not yours to schedule."
                  % (success_threshold * 100,
                     with_b["lifetime_success"] * 100,
                     without_b["lifetime_success"] * 100))
    elif depends:
        reason = ("without the inheritance this plan does not fail — it "
                  "shrinks. Median real spending falls from %s to %s a year, "
                  "%.0f%% less, while the success rate is unchanged at %.1f%% "
                  "because the withdrawal rule absorbs the loss by cutting "
                  "spending rather than by running out. An unchanged success "
                  "rate here does NOT mean the inheritance is unimportant."
                  % (_money(cons_with), _money(cons_without), drop_pct * 100,
                     with_b["lifetime_success"] * 100))
    else:
        reason = ("this plan does not lean on the inheritance: median real "
                  "spending changes from %s to %s a year without it, %.0f%%, "
                  "below the %.0f%% that would count as material"
                  % (_money(cons_with), _money(cons_without), drop_pct * 100,
                     material_drop * 100))
    return {"applicable": True, "depends_on_bequest": depends,
            "with_bequest": with_b["lifetime_success"],
            "without_bequest": without_b["lifetime_success"],
            "consumption_with": cons_with, "consumption_without": cons_without,
            "consumption_drop": drop, "consumption_drop_pct": drop_pct,
            "success_unchanged": with_b["lifetime_success"] == without_b["lifetime_success"],
            "material_drop": float(material_drop), "reason": reason}


def _execution_plan_is_already_simple(cfg: dict) -> bool:
    """Whether B4's three simplifications are already this plan's posture."""
    rule = cfg.get("rule") or {}
    roth = cfg.get("roth_ladder") or {}
    glide = cfg.get("glide") or {}
    return bool(
        str(rule.get("type", "gk") or "gk") == "fixed_real"
        and (not bool(roth.get("enabled", False))
             or float(roth.get("annual_conversion_y0", 0.0) or 0.0) == 0.0)
        and float(glide.get("equity_start", 1.0))
        == float(glide.get("equity_end", 1.0))
    )


def _execution_metrics(rows: list) -> dict:
    """Unconditional paired-path metrics; failed terminal wealth stays zero."""
    success = []
    consumption = []
    minimum = []
    terminal = []
    for row in rows:
        wd = row.get("withdrawal") or {}
        success.append(1.0 if row.get("lifetime_success") else 0.0)
        consumption.append(float(wd.get("mean_real_consumption") or 0.0))
        minimum.append(float(wd.get("min_real_consumption") or 0.0))
        cpi_end = _cpi_end(wd)
        terminal.append(
            float(wd.get("terminal_balance") or 0.0) / cpi_end
            if cpi_end else 0.0)
    return {
        "lifetime_success": float(np.mean(success)),
        "mean_real_consumption_p50": float(np.median(consumption)),
        "min_real_consumption_p50": float(np.median(minimum)),
        "terminal_real_p50": float(np.median(terminal)),
    }


def execution_simplification_stress(cfg: dict, n: int, seed: int, *,
                                    transition_age: int = 80) -> dict:
    """Price a simpler age-80 execution posture on paired sampled lives.

    This is an execution-complexity stress, not a cognitive prediction.  Both
    arms start from the same seed and path order; the policy consumes no random
    draws.  Only paths that reach a retirement payment at/after the transition
    enter the comparison.  If none do, every delta is ``None`` with a reason --
    "not observed" must never be printed as a measured zero.

    The allocation change is intentionally named a TARGET freeze.  This engine
    applies one blended return to every account, which is equivalent to
    implicit annual rebalancing; it cannot represent literal no-rebalancing.
    """
    transition_age = int(transition_age)
    if transition_age != 80:
        raise ValueError("this study's reviewed transition age is fixed at 80")
    n = int(n)
    if n <= 0:
        raise ValueError("paths must be positive")

    baseline_rows = _run(
        cfg, n, seed, False, per_path_substreams=True)
    simplified_rows = _run(
        cfg, n, seed, False,
        execution_policy=V98.ExecutionSimplificationPolicy(transition_age),
        per_path_substreams=True,
    )
    exposed_indexes = []
    for index, row in enumerate(simplified_rows):
        meta = ((row.get("withdrawal") or {})
                .get("execution_simplification") or {})
        if meta.get("exposed"):
            exposed_indexes.append(index)

    shared = {
        "transition_age": transition_age,
        "paths": n,
        "seed": int(seed),
        "exposed_paths": len(exposed_indexes),
        "paired_per_path": True,
        "pairing_protocol": "path_indexed_substreams",
        "policy": {
            "withdrawal": "fixed_real_base",
            "new_roth_conversions": False,
            "existing_roth_seasoning_continues": True,
            "allocation": "freeze_target_at_transition",
            "implicit_rebalancing_remains": True,
            "relocation": "base_location_only",
        },
        "categorical_verdict": None,
    }
    if not exposed_indexes:
        return {
            **shared,
            "applicable": False,
            "already_simple": None,
            "baseline": None,
            "simplified": None,
            "deltas": {
                "lifetime_success": None,
                "mean_real_consumption_p50": None,
                "min_real_consumption_p50": None,
                "terminal_real_p50": None,
            },
            "reason": "no sampled path reached a retirement payment at or "
                      "after age 80; the simplification was not exposed",
        }

    base = _execution_metrics([baseline_rows[i] for i in exposed_indexes])
    simple = _execution_metrics([simplified_rows[i] for i in exposed_indexes])
    deltas = {key: simple[key] - base[key] for key in base}
    already_simple = _execution_plan_is_already_simple(cfg)
    return {
        **shared,
        "applicable": True,
        "already_simple": already_simple,
        "baseline": base,
        "simplified": simple,
        "deltas": deltas,
        "reason": (
            "already_simple" if already_simple
            else "measured on paths exposed to the age-80 execution policy"
        ),
    }


def _money(value: float) -> str:
    return "$%s" % format(int(round(value)), ",d")


def career_break_comparison(cfg: dict, n: int, seed: int) -> dict:
    """Keep working, or take the break: the same plan run both ways.

    Roadmap 9.0 Phase 3. Built on the shape `bequest_dependency` established --
    this module composes BOTH configs from the one the user is looking at, so
    a caller cannot hand it two plans that differ in more than the one thing.

    Two Monte Carlo arms at one seed give the headline metrics. The wage-side
    figures do NOT come from differencing those arms: each break run already
    carries, for every year, what that SAME path would have earned and
    contributed with no break (identical promotion draw, identical bonus,
    identical wage factor). Two arms at one seed are not the same path, and
    attributing a difference between them to the break alone would be the
    common-random-numbers mistake this repo has already recorded.

    Every field is `None` when it was not measured. Nothing here reports a
    zero it did not compute.
    """
    brk = (cfg.get("career_break") or {})
    if not brk.get("enabled"):
        return {"applicable": False,
                "reason": "no career break is planned, so there is nothing to "
                          "compare this plan against",
                "keep_working": None, "with_break": None,
                "forgone_employee_contributions": None,
                "forgone_employer_match": None,
                "break_expenses_drawn_from_portfolio": None,
                "unfunded_break_expenses": None,
                "return_wage_gap_final_year": None,
                "fire_age_delay": None, "not_modelled": None}

    baseline_cfg = copy.deepcopy(cfg)
    baseline_cfg["career_break"] = {**dict(brk), "enabled": False}

    keep = summary(baseline_cfg, n, seed)
    break_paths = _run(cfg, n, seed, False, None)
    took = _summary_of(break_paths, cfg, _milestones(cfg))

    # The paired per-year figures, read off the break arm's own accumulation
    # paths. Median across paths rather than mean: promotion timing is drawn,
    # so the distribution of "what the break cost" is skewed by which paths
    # were promoted before it started, and a mean would report a career
    # nobody has.
    forgone_employee, forgone_match, unfunded, wage_gap = [], [], [], []
    drawn = []
    for result in break_paths:
        rows = [step.get("career_break") for step in result.get("accum_path", [])
                if step.get("career_break")]
        if not rows:
            continue
        forgone_employee.append(sum(
            row["employee_deferrals_without_break"] - row["employee_deferrals"]
            for row in rows))
        forgone_match.append(sum(
            row["employer_match_without_break"] - row["employer_match"]
            for row in rows))
        # Since U27 the shortfall is SPLIT. Reporting only one half would be a
        # different lie in each direction: the drawn part alone hides what the
        # plan could not cover, and the unfunded part alone hides that the
        # portfolio was sold down to get there.
        drawn.append(sum(row["drawn_from_taxable"] for row in rows))
        unfunded.append(sum(row["unfunded_expenses"] for row in rows))
        last = rows[-1]
        wage_gap.append(last["earned_gross_without_break"] - last["earned_gross"])

    def _median(values):
        # `None`, not 0.0, when there is nothing to take a median of: an
        # unmeasured figure and a measured zero must not print the same.
        if not values:
            return None
        ordered = sorted(values)
        mid = len(ordered) // 2
        return (ordered[mid] if len(ordered) % 2
                else (ordered[mid - 1] + ordered[mid]) / 2.0)

    keep_fire, took_fire = keep["fire_age_p50"], took["fire_age_p50"]
    delay = (None if keep_fire is None or took_fire is None
             else took_fire - keep_fire)

    return {
        "applicable": True,
        "paths": int(n), "seed": int(seed),
        "keep_working": keep,
        "with_break": took,
        "fire_age_delay": delay,
        "forgone_employee_contributions": _median(forgone_employee),
        "forgone_employer_match": _median(forgone_match),
        "break_expenses_drawn_from_portfolio": _median(drawn),
        "unfunded_break_expenses": _median(unfunded),
        "return_wage_gap_final_year": _median(wage_gap),
        # Carried WITH the numbers rather than left to a panel elsewhere: the
        # three omissions all point the same way, and a comparison that shows
        # only the measured part overstates how affordable the break is.
        "not_modelled": [
            "the effect of a break on WHEN you are promoted (the promotion "
            "clock keeps running; the return-to-work pay discount is the only "
            "place wage scarring is modelled)",
            "break-year living costs ARE now drawn from the taxable account "
            "(U27), but only from there: a 401(k)/IRA/HSA withdrawal before "
            "59.5 carries penalties and tax this app does not compute, so "
            "those accounts are never raided and anything the taxable account "
            "could not fund is reported as still unfunded",
            "health insurance during the break: the user supplies the net-new "
            "annual household premium. Residual mode pays it before saving; "
            "savings-rate mode keeps the stated rate authoritative and absorbs "
            "the premium inside residual spending. The app does not choose a "
            "spouse plan, COBRA or Marketplace policy or compute an "
            "accumulation-phase ACA subsidy",
        ],
    }


def summary(cfg: dict, n: int, seed: int, relocation_on: bool = False,
            mu_shift: Optional[float] = None) -> dict:
    """Compact metrics for sweeps / sensitivity. `mu_shift` moves the whole
    regime mixture's mean return (for the μ-uncertainty section)."""
    ms = _milestones(cfg)
    return _summary_of(_run(cfg, n, seed, relocation_on, mu_shift), cfg, ms)


def _summary_of(results: list, cfg: dict, ms: list) -> dict:
    """`summary`'s payload, from paths already run.

    Split out so `career_break_comparison` can read the break arm's headline
    metrics and its per-year accumulation detail from ONE set of paths. It
    previously ran that arm twice -- once through `summary` and once for the
    detail -- which is not just slow: two runs of the same seed are the same
    paths only for as long as nothing in between touches a generator, and
    relying on that is the assumption this repo files under "same seed is not
    the same path".
    """
    s = _summarize(_path_stats(results, ms), ms, _estate_exemption(cfg))
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
            # The arithmetic mean over the same population the bands describe.
            # Added for the Phase 2 realized bridge, which needs
            # `forecast_statistic=mean` and is explicitly not allowed to reuse a
            # p50 (ATTRIBUTION_ROBUSTNESS_PROTOCOL.md §1). Additive: no existing
            # value is recomputed, and `terminal_nom_hist` already reports a
            # mean the same way.
            "mean": float(arr.mean()),
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
        # The per-period forecast statistic the Phase 2 realized bridge reads.
        # Same population and same age set as `fan_nom`; the protocol's
        # canonical metric is the nominal closing portfolio value with
        # `forecast_statistic=mean`, and a check-in for a period reads the row
        # whose age is that period's close.
        "forecast_period_statistics": {
            "closing_portfolio_nominal": [
                {"age": row["age"], "mean": row["mean"]}
                for row in _bands_by_age(by_age_nom, n)
            ],
        },
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
    debt = kw.get("student_debt")
    horizon = int(state.retire_horizon)
    ra = int(retire_age) if retire_age else int(state.start_age + 15)
    # The stress runner starts at a synthetic FIRE boundary and therefore has
    # no accumulation path from which to read the permanent lifestyle step.
    # Resolve the same independent substream directly and apply it when its
    # event year precedes this chosen retirement age.
    _creep_raw = cfg.get("lifestyle_creep") or {}
    if _creep_raw.get("mode", "off") != "off":
        _validate_lifestyle_creep(cfg)
        _creep_params = _mk(LifestyleCreepParams, {
            k: v for k, v in _creep_raw.items() if k != "rng"})
        _creep_params.rng = np.random.default_rng(
            int(seed) + LIFESTYLE_CREEP_SEED_OFFSET)
        _creep_event = sample_lifestyle_creep(_creep_params)
        if (_creep_event is not None
                and _creep_event.event_year <= max(0, ra - int(state.start_age))):
            exp0 *= 1.0 + float(_creep_event.factor)
    debt_at_retirement = (
        float(debt.balance_after_years(
            max(0, ra - int(state.start_age)))) if debt is not None else 0.0)
    # User choice A: the synthetic backtest starts at the same readiness line
    # as the forward simulator -- SWR assets plus the balance still reserved.
    # It does not deduct/pay that balance; the schedule below keeps paying it.
    target = exp0 / swr + debt_at_retirement

    stack = kw["initial"]
    tot = stack.total or 1.0
    # The expression moves across verbatim -- `v / tot * target`, not
    # `v * (target / tot)`, which is a different float and would move every
    # stress-scenario number in the last digits.
    start = stack.map_balances(lambda v: v / tot * target)
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
        # E1. LTC and the parent lifecycle ARE retirement-phase modules, so
        # they belong here; only the reason they were absent was structural.
        # Resolved per scenario on a generator derived from the backtest seed,
        # so each stress column is reproducible and columns do not share draws.
        #
        # Parents run on REAL mortality tables even though `mort.enabled` is
        # False. The engine states the principle itself: a plan that switched
        # mortality off is not a plan where other people stop dying. Here it is
        # off by CONSTRUCTION -- the stress sequences are deterministic for the
        # plan holder -- and feeding parents a zero hazard would manufacture
        # "we modelled your parents and they cost nothing", which is the exact
        # fake zero this codebase keeps paying for.
        # Each module gets its OWN stream, on the adapter's offsets plus a
        # per-column shift, so one stress column cannot consume another's
        # draws and neither can collide with a forward run at the same seed.
        # `sample_ltc_events` refuses outright if the stream is missing rather
        # than modelling no care -- attaching them is not optional.
        _column = abs(hash(key)) % 997
        _bt_ltc = kw.get("ltc")
        if _bt_ltc is not None and _bt_ltc.mode != LTC.OFF:
            _bt_ltc.rng = np.random.default_rng(
                int(seed) + 11_000_000 + 909_000 + _column)
        _bt_parents = kw.get("parents")
        if _bt_parents is not None and _bt_parents.mode != PARENTS.OFF:
            _bt_parents.rng = np.random.default_rng(
                int(seed) + 13_000_000 + 909_000 + _column)
        _ltc_events, _ltc_meta = LTC.sample_ltc_events(
            kw.get("ltc"), ra + 1, ra + horizon, anchor_age=int(state.start_age),
            calibration=(
                LTC.calibration_for(
                    (lambda age: V98.annual_mortality_rate(age, kw["mortality"])),
                    risk=float(kw["ltc"].lifetime_risk),
                    cap_age=int(kw["mortality"].cap_age),
                    onset_age=kw["ltc"].onset_age,
                    onset_spread=kw["ltc"].onset_spread)
                if (kw.get("ltc") is not None and kw["ltc"].mode == LTC.STOCHASTIC
                    and float(kw["ltc"].lifetime_risk) > 0.0) else None),
        )

        def _bt_parent_rate(sex):
            table = {"male": V98.MORTALITY_MALE,
                     "female": V98.MORTALITY_FEMALE}[str(sex)]
            return lambda age: V98.annual_mortality_rate(age, table)

        _care_events, _bequests, _parents_meta = PARENTS.sample_parents(
            kw.get("parents"), _bt_parent_rate,
            first_age=ra + 1, last_age=ra + horizon,
            anchor_age=int(state.start_age),
            cap_age=int(kw["mortality"].cap_age))

        with _ENGINE_LOCK, _household_ctx(cfg):
            wd = simulate_retirement_v98(
                starting_accounts=start.copy(), starting_age=ra,
                fire_year_cpi_cumulative=1.0,
                equity_returns=eq, bond_returns=bd, inflations=infl,
                rule=kw["rule"], glide_path=kw["glide_path"],
                relocation=RelocationParams(relocation_age=None),
                sh_property=kw["sh_property"], medical=kw["medical"], aca=kw["aca"],
                mortality=mort, roth_ladder=kw["roth_ladder"], ss=kw["ss"],
                ftc=kw["ftc"], eldercare_events=None, inheritance_event=None,
                state=state, tax_us=kw["tax_us"], tax_cn=kw["tax_cn"],
                friction=((cfg.get("returns") or {}).get("friction_retire", 0.005)
                          + float((cfg.get("returns") or {}).get("expense_ratio", 0) or 0)
                          + float((cfg.get("returns") or {}).get("rebalance_cost", 0) or 0)),
                rng=np.random.default_rng(seed),
                # Blocky spending gets its own generator here too. Excluding
                # it from the backtest would mean a plan that models lumps
                # silently stops modelling them the moment it is backtested
                # -- and the backtest already carries the other stochastic
                # modules (LTC, mortality), so leaving out just this one
                # would be arbitrary.
                ss_trust_fund=kw.get("ss_trust_fund"),
                ss_trust_fund_depletion_year=(
                    int(V98.SSA_TRUST_FUND["oasi_depletion_year_intermediate"])
                    if (kw.get("ss_trust_fund") is not None
                        and kw["ss_trust_fund"].enabled) else None),
                blocky_spending=kw.get("blocky_spending"),
                blocky_rng=(np.random.default_rng(
                    int(seed) + int(kw["blocky_spending"].seed_offset))
                    if (kw.get("blocky_spending") is not None
                        and kw["blocky_spending"].enabled) else None),
                china_healthcare=kw["china_healthcare"], ss_nra=kw["ss_nra"],
                life_events=([(a, amt) for a, amt in stress_life_events
                              if a > ra] or None),
                income_streams=kw["income_streams"],
                tax_true=kw["tax_true"],
                ltc_events=(_ltc_events or None),
                parent_care_events=(_care_events if _care_events is not None else None),
                parent_bequests=(_bequests if _bequests is not None else None),
                student_debt=debt,
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
            # Which mode each newly-modelled module ran in, and -- when it
            # produced nothing -- why. Both resolvers return this precisely so
            # an empty list cannot be read as a measured zero.
            "ltc_meta": _ltc_meta,
            "parents_meta": _parents_meta,
        }
    # What this backtest did NOT model, said out loud rather than left to the
    # reader — and derived from the plan rather than hand-listed. The first
    # version named long-term care only, which made a partial list look
    # complete: `backtest` builds 31 adapter params and consumes 19, and among
    # the twelve it drops are the eldercare shock, the inheritance draw, the
    # parent lifecycle and the OBBBA tax scenario, every one of them something
    # the user switched on. These are fixed stress sequences with mortality
    # off, so none of those subsystems can run here; that is a reason to say so,
    # not a reason to be quiet.
    #
    # `None` when nothing is omitted, so a caller cannot read an empty list as
    # "checked and found nothing".
    _ltc = kw.get("ltc")
    _parents = kw.get("parents")
    _why = ("this backtest applies fixed stress sequences with mortality off, "
            "so %s is not charged in any scenario below — these survival "
            "results are for a plan WITHOUT it, and the headline Monte Carlo "
            "run is where it appears")
    excluded = []
    # `ltc` and `parents` used to be listed here. They are MODELLED now (E1,
    # ruled 2026-08-14): both are retirement-phase modules, so the only thing
    # that had kept them out was that nobody wired them in. Leaving them on
    # this list would now be the opposite error -- disclosing an absence that
    # is not there.
    _elder = kw.get("eldercare")
    if _elder is not None and getattr(_elder.mode, "value", _elder.mode) != "off":
        excluded.append({"module": "eldercare",
                         "mode": getattr(_elder.mode, "value", _elder.mode),
                         "why": _why % "the eldercare shock"})
    _inh = kw.get("inheritance")
    if _inh is not None and getattr(_inh.mode, "value", _inh.mode) != "off":
        excluded.append({"module": "inheritance",
                         "mode": getattr(_inh.mode, "value", _inh.mode),
                         "why": _why % "the inheritance you expect"})
    _obbba = kw.get("obbba")
    if _obbba is not None and getattr(_obbba.mode, "value", _obbba.mode) != "off":
        # Not an omission and not fixable by wiring: OBBBA is an ACCUMULATION
        # mechanic. `compute_obbba_boost_path` turns federal tax savings into
        # extra taxable contributions compounded forward, and this backtest has
        # no accumulation phase at all -- it synthesises a portfolio at the FI
        # number and starts on the day of retirement. Reporting it next to a
        # module that simply was not wired in would say "we could have modelled
        # this", which is false.
        excluded.append({"module": "obbba",
                         "mode": getattr(_obbba.mode, "value", _obbba.mode),
                         "inapplicable": True,
                         "why": "OBBBA acts during accumulation, by boosting "
                                "contributions out of tax savings. This "
                                "backtest starts on the day you retire, so "
                                "there is no accumulation for it to act on -- "
                                "it is not applicable here rather than left "
                                "out"})
    excluded = excluded or None
    return {"retire_age": ra, "target": target, "swr": swr,
            "expenses": exp0, "scenarios": scen, "not_modelled": excluded}
