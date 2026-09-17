"""Pure Roadmap 11 annual execution compiler.

The compiler owns no withdrawal, guardrail, RMD, bracket, or IRMAA math.  It
maps a current already-FIRE plan and explicit annual facts into calls to the
same engine objects used by a lifecycle run, then labels the evidence basis of
each section.  A missing historical fact disables only the section that needs
it; it is never replaced with today's balance or an age-derived guess.
"""
from __future__ import annotations

import dataclasses
import datetime as dt
import math
from typing import Any, Optional

import decumulation_facts as FACTS
import engine_adapter as ENG
import account_schema as ACCOUNT_SCHEMA
import fire_tax_true as TRUE_TAX
import fire_v9_2_model as V92
import fire_v9_4_model as V94
import fire_v9_8_model as V98
from fire_rules_x import ABWRule, FloorUpsideRule


SCHEMA_VERSION = 1


class CockpitError(ValueError):
    """A named refusal at the current-year compiler boundary."""


def _positive_year(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CockpitError("calendar_year must be a positive integer")
    return value


def _exact_balances(calendar_year: int, balances: Any) -> Optional[dict]:
    if balances is None:
        return None
    try:
        return FACTS.validate_request({
            "calendar_year": calendar_year,
            "rmd_prior_year_end_balances": balances,
        })["rmd_prior_year_end_balances"]
    except FACTS.DecumulationFactError as exc:
        raise CockpitError(str(exc)) from None


def _unmeasured(reason: str, **details) -> dict:
    return {"measurement_state": "unmeasured", "reason": reason, **details}


def _not_applicable(reason: str, **details) -> dict:
    return {"measurement_state": "not_applicable", "reason": reason,
            **details}


def _measured(**details) -> dict:
    return {"measurement_state": "measured", **details}


def _current_income(streams, age: int, retirement_start_age: int) -> dict:
    by_kind = {}
    for stream in streams or ():
        if not V98._income_schedule_active(
                stream, age, retirement_start_age=retirement_start_age):
            continue
        amount = V98._income_nominal(
            stream, 1.0,
            nominal_anchor_cpi=(stream.nominal_anchor_cpi
                                if stream.nominal_anchor_cpi is not None
                                else 1.0))
        by_kind[stream.kind] = by_kind.get(stream.kind, 0.0) + float(amount)
    return {"by_kind": by_kind, "total_nominal": float(sum(by_kind.values()))}


def _current_ss(cfg: dict, kw: dict, age: int) -> dict:
    if kw.get("ssa_pia_resolver") is not None:
        raise CockpitError(
            "social_security.pia_mode needs a resolved current PIA before "
            "the annual Cockpit can compile")
    ss = kw["ss"]
    household = cfg.get("household") or {}
    primary = V92.compute_ss_annual_income(age, 1.0, 1.0, ss)
    if not household.get("enabled"):
        return {"primary_nominal": float(primary), "spouse_nominal": 0.0,
                "total_nominal": float(primary),
                "household_state": "single"}
    spouse_age = age + int(household.get("spouse_age_offset", 0) or 0)
    spouse = dataclasses.replace(
        ss,
        pia_monthly_y0=float(household.get("spouse_pia_monthly_y0", 0) or 0),
        claim_age=int(household.get("spouse_claim_age", ss.claim_age)))
    spouse_amount = V92.compute_ss_annual_income(
        spouse_age, 1.0, 1.0, spouse)
    return {"primary_nominal": float(primary),
            "spouse_nominal": float(spouse_amount),
            "total_nominal": float(primary + spouse_amount),
            "household_state": "both_plan_members_alive"}


def _target_receipt(cfg: dict, kw: dict, current_spending: float,
                    age: int) -> tuple[Optional[float], dict]:
    rule = kw["rule"]
    portfolio = float(kw["initial"].total)
    rule_type = str((cfg.get("rule") or {}).get("type", "gk") or "gk")
    if isinstance(rule, (FloorUpsideRule, ABWRule)):
        return None, _unmeasured(
            "historical_strategy_anchor_missing",
            rule_type=rule_type,
            detail=("this strategy needs the portfolio/budget at its original "
                    "start; today's balances are not substituted"))
    if rule_type == "gk":
        initial_swr = kw["already_fired"].guardrail_initial_swr
        if initial_swr is None:
            return current_spending, _unmeasured(
                "guardrail_initial_swr_missing", rule_type="gk")
        band = rule.guardrail_band_receipt(
            current_spending, portfolio, float(initial_swr))
        state = V98._init_rule(
            rule, portfolio, current_spending, float(initial_swr), 1.0)
        target, _state = rule.compute_target_withdrawal(
            1, age, portfolio, 0.0, 1.0, state)
        return float(target), _measured(
            rule_type="gk", target_before_rule_nominal=current_spending,
            target_after_rule_nominal=float(target), **band)
    initial_swr = float(kw["fire_swr"])
    state = V98._init_rule(
        rule, portfolio, current_spending, initial_swr, 1.0)
    target, _state = rule.compute_target_withdrawal(
        1, age, portfolio, 0.0, 1.0, state)
    return float(target), _not_applicable(
        "strategy_has_no_guardrail_band", rule_type=rule_type,
        target_after_rule_nominal=float(target))


def _rmd_section(kw: dict, calendar_year: int,
                 balances: Optional[dict]) -> tuple[dict, Optional[int], bool]:
    tax = kw["tax_true"]
    if not tax.enabled:
        return _not_applicable("true_tax_disabled"), None, False
    birth_year = kw["already_fired"].birth_year
    if birth_year is None:
        return _unmeasured("birth_year_missing"), None, True
    age = calendar_year - int(birth_year)
    if age < 0:
        raise CockpitError("birth_year must not be after calendar_year")
    due = bool(tax.rmd_enabled and age >= tax.rmd_age)
    if due and balances is None:
        return _unmeasured(
            "rmd_prior_year_end_balances_missing",
            age_this_year=age, rmd_start_age=int(tax.rmd_age)), age, True
    try:
        receipt = TRUE_TAX.rmd_execution_receipt(
            balances, int(birth_year), calendar_year, tax)
    except ValueError as exc:
        raise CockpitError(str(exc)) from None
    return _measured(**receipt), age, False


def _unsupported_current_plan(cfg: dict, kw: dict, age: int) -> Optional[str]:
    relocation = kw["relocation"]
    if (relocation.relocation_age is not None
            and age >= int(relocation.relocation_age)):
        return "current_non_us_execution_is_not_compiled"
    if kw["ss_trust_fund"].enabled:
        return "stochastic_social_security_trust_fund_path_is_unresolved"
    if (cfg.get("blocky_spending") or {}).get("enabled"):
        return "current_blocky_spending_event_is_unresolved"
    if any(int(event_age) == age for event_age, _amount in kw["life_events"] or ()):
        return "current_age_mandatory_event_requires_an_execution_record"
    return None


def _country_account_worksheet(cfg: dict, kw: dict, calendar_year: int,
                               target: float, age: int) -> dict:
    """Compile the bounded non-US account beta from pack data and plan facts."""
    block = cfg["country_accounts"]
    jurisdiction = str(block["jurisdiction"]).upper()
    types = ACCOUNT_SCHEMA.account_types(jurisdiction)
    pack_rows = {row["key"]: row for row in
                 ACCOUNT_SCHEMA.country_pack(jurisdiction)["account_types"]}
    accounts = V98.AccountStack(**{
        account.field: float(block["balances"][account.field])
        for account in types
    })
    holder_birth_year = kw["already_fired"].birth_year
    if holder_birth_year is None:
        raise CockpitError("already_fired.birth_year is required for country accounts")
    holder_age = calendar_year - int(holder_birth_year)
    choices = block.get("disposition_choices") or {}
    dispositions = []
    for account in types:
        rule = ACCOUNT_SCHEMA.disposition_rule(account.key, jurisdiction)
        if rule is None or accounts.balance(account.field) <= 0.0:
            continue
        choice = choices.get(account.key)
        if choice is None:
            raise CockpitError(
                "country_accounts.disposition_choices.%s is required" % account.key)
        option = next((row for row in rule["options"]
                       if row["key"] == choice), None)
        if option is None:
            raise CockpitError("unsupported disposition choice for %s" % account.key)
        if "target_account_type" not in option:
            raise CockpitError(
                "the selected disposition is legal but not modeled by this RRIF-only beta")
        trigger = int(rule["trigger_age"])
        if holder_age > trigger:
            raise CockpitError(
                "%s still has a balance after its age-%d disposition year" %
                (account.key, trigger))
        if holder_age == trigger:
            target_type = next(a for a in types
                               if a.key == option["target_account_type"])
            amount = accounts.balance(account.field)
            setattr(accounts, account.field, 0.0)
            setattr(accounts, target_type.field,
                    accounts.balance(target_type.field) + amount)
            dispositions.append({"from": account.key, "to": target_type.key,
                                 "gross_nominal": float(amount)})

    forced_gross = forced_tax = 0.0
    forced_rows = []
    establishment = block.get("distribution_establishment_years") or {}
    bases = block.get("distribution_age_basis") or {}
    for account in types:
        if not account.forced_distribution:
            continue
        if account.key not in establishment:
            raise CockpitError(
                "country_accounts.distribution_establishment_years.%s is required" %
                account.key)
        established = establishment[account.key]
        if (isinstance(established, bool) or not isinstance(established, int)
                or established > calendar_year):
            raise CockpitError("RRIF establishment year must be an integer no later than the worksheet year")
        basis = bases.get(account.key)
        rule = ACCOUNT_SCHEMA.distribution_rule(account.key, jurisdiction)
        if basis not in rule["age_basis_options"]:
            raise CockpitError(
                "country_accounts.distribution_age_basis.%s must be an allowed pack choice" %
                account.key)
        if basis == "annuitant":
            basis_age = holder_age
        else:
            spouse_birth = block.get("elected_spouse_birth_year")
            if (isinstance(spouse_birth, bool) or not isinstance(spouse_birth, int)
                    or spouse_birth > calendar_year):
                raise CockpitError("country_accounts.elected_spouse_birth_year is required")
            basis_age = calendar_year - spouse_birth
        gross = ACCOUNT_SCHEMA.minimum_distribution(
            account.key, accounts.balance(account.field), basis_age,
            jurisdiction, establishment_year=(established == calendar_year))
        setattr(accounts, account.field, accounts.balance(account.field) - gross)
        tax = gross * float(kw["tax_us"].withdrawal_tax_traditional)
        forced_gross += gross
        forced_tax += tax
        forced_rows.append({"account_type": account.key,
                            "account_key": account.key,
                            "gross_nominal": float(gross),
                            "prior_year_end_balance_nominal": float(
                                accounts.balance(account.field) + gross),
                            "required_nominal": float(gross),
                            "age_basis": basis, "age_used": basis_age})

    income = _current_income(kw["income_streams"], age, age)
    need = max(0.0, target - income["total_nominal"])
    forced_net = max(0.0, forced_gross - forced_tax)
    forced_applied = min(need, forced_net)
    remaining = max(0.0, need - forced_applied)
    meta = {}
    inclusion = next((float(pack_rows[account.key].get(
        "capital_gain_inclusion_fraction", 1.0)) for account in types
        if account.tax_character == ACCOUNT_SCHEMA.CHARACTER_CAPITAL_GAIN), 1.0)
    accounts, delivered, penalty = V94.withdraw_with_seasoning_v94(
        accounts, remaining, kw["tax_us"], 0.0, age,
        gain_fraction=kw["tax_true"].taxable_gain_fraction * inclusion,
        meta_out=meta, jurisdiction=jurisdiction)
    excess = forced_net - forced_applied
    if excess > 0.0:
        reinvest = ACCOUNT_SCHEMA.reinvestment_type(jurisdiction=jurisdiction)
        setattr(accounts, reinvest.field, accounts.balance(reinvest.field) + excess)
    return {
        "schema_version": SCHEMA_VERSION, "calendar_year": calendar_year,
        "current_age": age, "portfolio_nominal": float(sum(block["balances"].values())),
        "guardrail": _not_applicable("country_account_beta_uses_current_spending"),
        "rmd": _measured(rule_kind="pack_forced_distribution",
                         status=("due" if forced_gross > 0.0 else "not_due"),
                         age_this_year=holder_age,
                         total_required_nominal=float(forced_gross),
                         accounts=forced_rows, dispositions=dispositions),
        "worksheet": _measured(
            tax_model="flat_effective_rates", target_nominal=target,
            structured_income=income,
            social_security={"measurement_state": "unmeasured",
                             "reason": "cpp_oas_not_modeled"},
            roth_conversion_nominal=0.0,
            portfolio_after_tax_needed_nominal=need,
            portfolio_after_tax_delivered_nominal=float(forced_applied + delivered),
            shortfall_nominal=max(0.0, need - forced_applied - delivered),
            withdrawal_order=meta["withdrawal_order"],
            withdrawals_by_account=(forced_rows + meta["withdrawals_by_account"]),
            forced_distribution_excess_reinvested_nominal=float(excess)),
        "tax": _measured(
            tax_model="flat_effective_rates",
            tax_and_penalty_nominal=float(forced_tax + meta["tax_and_penalty_nominal"]),
            penalty_nominal=float(penalty),
            limitation="not_a_canadian_federal_or_provincial_bracket_model"),
        "irmaa": _not_applicable("us_only"),
    }


def compile_cockpit(cfg: dict, *, calendar_year: int,
                    rmd_prior_year_end_balances: Any = None) -> dict:
    """Compile one current-year worksheet without running a stochastic path."""
    if not isinstance(cfg, dict):
        raise CockpitError("config must be an object")
    calendar_year = _positive_year(calendar_year)
    balances = _exact_balances(calendar_year, rmd_prior_year_end_balances)
    try:
        ENG.check_config(cfg)
        kw = ENG.build_kwargs(cfg, False)
    except (ValueError, TypeError, ENG.ConfigIncomplete) as exc:
        raise CockpitError(str(exc)) from None
    already = kw["already_fired"]
    if not already.enabled:
        raise CockpitError("already_fired.enabled must be true")
    try:
        fire_date = dt.date.fromisoformat(str(already.actual_fire_date))
    except (TypeError, ValueError):
        raise CockpitError("already_fired.actual_fire_date must be ISO YYYY-MM-DD") from None
    if calendar_year < fire_date.year:
        raise CockpitError("calendar_year must not precede actual_fire_date")
    age = int(kw["state"].start_age)
    spending = float(already.annual_spending_real)
    if not math.isfinite(spending) or spending <= 0.0:
        raise CockpitError("already_fired.annual_spending_real must be positive")

    target, guardrail = _target_receipt(cfg, kw, spending, age)
    if target is not None and bool((cfg.get("country_accounts") or {}).get("enabled")):
        return _country_account_worksheet(cfg, kw, calendar_year, target, age)
    rmd, rmd_age, exclude_rmd = _rmd_section(
        kw, calendar_year, balances)
    base = {
        "schema_version": SCHEMA_VERSION,
        "calendar_year": calendar_year,
        "current_age": age,
        "portfolio_nominal": float(kw["initial"].total),
        "guardrail": guardrail,
        "rmd": rmd,
    }
    if target is None:
        return {**base, "worksheet": _unmeasured(
            "withdrawal_target_unmeasured"),
            "tax": _unmeasured("withdrawal_target_unmeasured"),
            "irmaa": _unmeasured("withdrawal_target_unmeasured")}
    unsupported = _unsupported_current_plan(cfg, kw, age)
    if unsupported:
        return {**base, "worksheet": _unmeasured(unsupported),
                "tax": _unmeasured(unsupported),
                "irmaa": _unmeasured(unsupported)}

    retirement_start_age = age - max(0, calendar_year - fire_date.year)
    income = _current_income(kw["income_streams"], age, retirement_start_age)
    try:
        ss = _current_ss(cfg, kw, age)
    except CockpitError as exc:
        return {**base, "worksheet": _unmeasured(str(exc)),
                "tax": _unmeasured(str(exc)),
                "irmaa": _unmeasured(str(exc))}

    accounts = kw["initial"].copy()
    accounts, _queue, conversion = V92.execute_roth_conversion(
        accounts, [], age, 0, kw["roth_ladder"])
    roth_locked = float(conversion)
    true_tax_on = bool(kw["tax_true"].enabled)
    if true_tax_on and conversion > 0.0:
        accounts.taxable += conversion * kw["roth_ladder"].federal_tax_rate
    need_before_ss = max(0.0, target - income["total_nominal"])

    if not true_tax_on:
        need_from_portfolio = max(0.0, need_before_ss - ss["total_nominal"])
        meta = {}
        _out, delivered, penalty = V94.withdraw_with_seasoning_v94(
            accounts, need_from_portfolio, kw["tax_us"], roth_locked, age,
            gain_fraction=kw["tax_true"].taxable_gain_fraction,
            meta_out=meta)
        worksheet = _measured(
            tax_model="flat", target_nominal=target,
            structured_income=income, social_security=ss,
            roth_conversion_nominal=float(conversion),
            portfolio_after_tax_needed_nominal=need_from_portfolio,
            portfolio_after_tax_delivered_nominal=float(delivered),
            shortfall_nominal=max(0.0, need_from_portfolio - delivered),
            withdrawal_order=meta["withdrawal_order"],
            withdrawals_by_account=meta["withdrawals_by_account"])
        return {**base, "worksheet": worksheet,
                "tax": _measured(
                    tax_model="flat",
                    tax_and_penalty_nominal=meta["tax_and_penalty_nominal"],
                    penalty_nominal=float(penalty)),
                "irmaa": _not_applicable("true_tax_disabled")}

    solve_tax = kw["tax_true"]
    rmd_basis = balances
    if exclude_rmd:
        solve_tax = dataclasses.replace(solve_tax, rmd_enabled=False)
        rmd_basis = None
    meta = {}
    result = TRUE_TAX.solve_retirement_year(
        accounts, need_before_ss, ss["total_nominal"], float(conversion),
        roth_locked, age, 1.0, solve_tax,
        rmd_bases_prior_year_end=rmd_basis,
        gain_fraction=None, meta_out=meta, rmd_age=rmd_age)
    ordinary = TRUE_TAX.ordinary_tax_cliff_receipt(
        result["ordinary_taxable_real"], solve_tax.filing_jointly)
    tax = _measured(
        tax_model="true_tax", tax_total_nominal=float(result["tax_total"]),
        penalty_nominal=float(result["penalty"]),
        ordinary_cliff=ordinary,
        rmd_treatment=("excluded_unmeasured" if exclude_rmd else "included"))
    if age < kw["medical"].medicare_age:
        irmaa = _not_applicable("below_medicare_age")
    elif not solve_tax.irmaa_enabled:
        irmaa = _not_applicable("irmaa_disabled")
    else:
        persons = 2 if solve_tax.filing_jointly else 1
        irmaa = _measured(
            evidence_basis="current_year_magi_fallback_not_t_minus_2",
            **TRUE_TAX.irmaa_cliff_receipt(
                result["magi_agi_nominal"], solve_tax.filing_jointly, persons))
    worksheet = _measured(
        tax_model="true_tax", target_nominal=target,
        structured_income=income, social_security=ss,
        roth_conversion_nominal=float(conversion),
        portfolio_after_tax_needed_nominal=need_before_ss,
        portfolio_after_tax_delivered_nominal=float(result["delivered"]),
        shortfall_nominal=float(result["shortfall"]),
        withdrawal_order=meta["withdrawal_order"],
        withdrawals_by_account=meta["withdrawals_by_account"],
        rmd_treatment=("excluded_unmeasured" if exclude_rmd else "included"))
    return {**base, "worksheet": worksheet, "tax": tax, "irmaa": irmaa}
