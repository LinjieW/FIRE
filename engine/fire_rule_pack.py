"""Canonical offline rule pack for calendar-varying US model inputs.

This module is a dependency leaf: it imports no FIRE engine modules and never
uses the network.  Runtime modules import their existing numeric defaults from
here, while ``rule_pack_for_run`` produces result-bound vintage/status metadata.

Important vocabulary:

* ``maintenance_due_on`` is FIRE Modeling's review deadline, not a claim that a
  law is legally valid through that date.
* ``current`` means inside that declared maintenance window, not independently
  verified tax advice.
* pack metadata belongs to the runtime/result, never to a user's plan config.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
import os
import sys
from typing import Any, Mapping


RULE_PACK_SCHEMA_VERSION = 1


# JSON-safe source of truth.  Infinity is represented as ``None`` in the
# canonical payload and converted only in the tax module's runtime convenience
# table.  That keeps the content hash strict (allow_nan=False).
#: The pack is DATA, loaded from a declarative file, not a Python literal.
#:
#: 4.0's hard rule is that a rule pack carries declarative data and never code.
#: It shipped as a 400-line dict inside this module, which is code by any
#: reading -- so the payload moved to `rule_pack_us_offline.json` and the logic
#: that interprets it stayed here.
#:
#: Behaviour-neutral by construction, not by hope: `RULE_PACK_SHA256` is
#: computed from `_canonical_bytes(payload)`, which is already
#: `json.dumps(sort_keys=True, separators=(",", ":"))`. The file's own
#: indentation therefore cannot move the hash -- only the parsed structure can,
#: and the payload was verified to be pure-JSON expressible (no tuples, no
#: non-string keys) before it was extracted. `tests/test_rule_pack_payload.py`
#: pins the shipped hash so a future edit to the data cannot change the pack's
#: identity silently.
_PAYLOAD_FILENAME = "rule_pack_us_offline.json"


def _payload_path() -> str:
    """Where the data file is, from source AND from inside the frozen app.

    PyInstaller unpacks bundled data under `sys._MEIPASS`; from a source
    checkout it sits beside this module. Checked in that order because the
    frozen case is the one a source-only lookup would fail in -- silently, at
    the user's first run.
    """
    base = getattr(sys, "_MEIPASS", None)
    if base:
        candidate = os.path.join(base, _PAYLOAD_FILENAME)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        _PAYLOAD_FILENAME)


def _load_payload() -> dict[str, Any]:
    path = _payload_path()
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except OSError as exc:
        # Loud. A missing pack means every tax figure in the app is missing;
        # falling back to anything would be inventing tax law.
        raise RuntimeError(
            "rule pack payload not found at %s -- the app cannot run without "
            "it, and there is no default to fall back on" % path) from exc


_PACK_PAYLOAD: dict[str, Any] = _load_payload()


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")


RULE_PACK_SHA256 = hashlib.sha256(_canonical_bytes(_PACK_PAYLOAD)).hexdigest()
RULE_PACK_ID = f"us-offline-{RULE_PACK_SHA256[:16]}"


def _component(component_id: str) -> dict[str, Any]:
    for row in _PACK_PAYLOAD["components"]:
        if row["id"] == component_id:
            return row
    raise KeyError(component_id)


def canonical_pack_payload() -> dict[str, Any]:
    """Return an isolated copy of the exact content-addressed payload."""
    return copy.deepcopy(_PACK_PAYLOAD)


# Runtime conveniences.  These are derived once from the canonical payload;
# the tests bind each consumer back to them so no module can quietly drift.
_FEDERAL_VALUES = _component("us_federal_tax")["values"]
US_FEDERAL_RULES = {
    "ordinary_single": tuple(tuple(row) for row in _FEDERAL_VALUES["ordinary_single"]),
    "ordinary_mfj": tuple(tuple(row) for row in _FEDERAL_VALUES["ordinary_mfj"]),
    "std_deduction_single": _FEDERAL_VALUES["std_deduction_single"],
    "std_deduction_mfj": _FEDERAL_VALUES["std_deduction_mfj"],
    "ltcg_single": tuple(tuple(row) for row in _FEDERAL_VALUES["ltcg_single"]),
    "ltcg_mfj": tuple(tuple(row) for row in _FEDERAL_VALUES["ltcg_mfj"]),
    "ss_provisional_single": tuple(_FEDERAL_VALUES["ss_provisional_single"]),
    "ss_provisional_mfj": tuple(_FEDERAL_VALUES["ss_provisional_mfj"]),
    "rmd_divisors": {
        int(age): divisor
        for age, divisor in _FEDERAL_VALUES["rmd_divisors"].items()
    },
    "early_withdrawal_age": _FEDERAL_VALUES["early_withdrawal_age"],
    "early_withdrawal_rate": _FEDERAL_VALUES["early_withdrawal_rate"],
}

#: State income-tax archetypes, keyed by id. TYPES, never states: no row
#: reproduces a particular state's schedule, and nothing may label one with a
#: state name.
US_STATE_ARCHETYPES = {
    row["id"]: dict(row)
    for row in _component("us_state_archetypes")["values"]["archetypes"]
}

_IRMAA_VALUES = _component("medicare_irmaa")["values"]


def _runtime_irmaa(rows: list[list[Any]]) -> tuple[tuple[float, float], ...]:
    """Convert canonical strict-lower-bound tiers to runtime tuples."""
    return tuple((float(low), surcharge) for low, surcharge in rows)


IRMAA_RULES = {
    "single": _runtime_irmaa(_IRMAA_VALUES["single"]),
    "mfj": _runtime_irmaa(_IRMAA_VALUES["mfj"]),
}
CONTRIBUTION_LIMIT_RULES = dict(
    _component("contribution_limits")["values"])
#: Roadmap 10.0: statutory amounts the accumulation side had been doing
#: without. Separate components rather than fields on `contribution_limits`,
#: because that component's peel test exists to catch an existing row edited
#: under cover of an addition.
RETIREMENT_CATCH_UP_RULES = dict(
    _component("retirement_catch_up_limits")["values"])
IRA_PHASE_OUT_RULES = dict(_component("ira_income_phase_outs")["values"])
FICA_RULES = dict(_component("fica_payroll_tax")["values"])
SECA_RULES = dict(_component("self_employment_tax")["values"])
PLAN_SHAPE_RULES = dict(_component("plan_shape_limits")["values"])
ESPP_RULES = dict(_component("espp_section_423")["values"])
ACA_MARKETPLACE_RULES = dict(_component("aca_marketplace")["values"])
SSA_RULES = {
    **_component("ssa_benefit_rules")["values"],
    **_component("ssa_statement_import")["values"],
}

#: The 2026 Trustees Report's OASI reserve-depletion projection.
#:
#: Kept SEPARATE from `SSA_RULES` on purpose. Those are statutory rules, which
#: change when Congress acts. These are actuarial PROJECTIONS, which change
#: every June whether or not anything happened -- a different kind of fact with
#: a different expiry, and merging them would let a stale projection ride along
#: under a rule component's freshness.
SSA_TRUST_FUND = dict(_component("ssa_trust_fund")["values"])


def _as_date(value: str | _dt.date) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("as_of must be an explicit ISO date or date object")


def _group(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = config.get(name) if isinstance(config, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def _editable_value_state(
        configured: Mapping[str, Any],
        reference: Mapping[str, Any],
) -> tuple[str, list[str], dict[str, Any]]:
    """Compare values without pretending to know who authored a mismatch."""
    actual = {field: configured.get(field) for field in reference}
    mismatches = [
        field for field, expected in reference.items()
        if actual.get(field) != expected
    ]
    status = ("matches_pack_value" if not mismatches
              else "user_or_legacy_override")
    return status, mismatches, actual


def _component_applicability(config: Mapping[str, Any]) -> dict[str, bool]:
    state = _group(config, "state")
    true_tax = _group(config, "tax_true")
    tax_us = _group(config, "tax_us")
    ss = _group(config, "social_security")
    contributions = _group(config, "contributions")
    household = _group(config, "household")
    start_age = float(state.get("start_age", 0) or 0)
    accum_years = float(state.get("accum_years", 0) or 0)
    retire_horizon = float(state.get("retire_horizon", 0) or 0)
    primary_earnings = sum(
        float(contributions.get(field, 0) or 0)
        for field in ("base_salary_pre", "bonus_pre", "ot_income_pre")
    )
    spouse_earnings = (
        sum(float(household.get(field, 0) or 0)
            for field in ("spouse_base_salary_pre", "spouse_bonus_pre"))
        if household.get("enabled") else 0.0
    )
    primary_caps = any(
        float(contributions.get(field, 0) or 0) > 0
        for field in (
            "pretax_401k_limit_y1", "roth_ira_limit_y1", "hsa_limit_y1")
    )
    spouse_caps = any(
        float(household.get("spouse_" + field, 0) or 0) > 0
        for field in (
            "pretax_401k_limit_y1", "roth_ira_limit_y1", "hsa_limit_y1")
    )
    primary_active = primary_earnings > 0 and primary_caps
    spouse_active = bool(household.get("enabled")) and (
        spouse_earnings > 0 and spouse_caps)
    early_penalty_possible = bool(
        retire_horizon > 0
        and start_age + 1 < US_FEDERAL_RULES["early_withdrawal_age"])
    federal = bool(
        true_tax.get("enabled") or tax_us.get("progressive")
        or early_penalty_possible)
    return {
        "us_federal_tax": federal,
        "medicare_irmaa": bool(
            true_tax.get("enabled") and true_tax.get("irmaa_enabled", True)),
        "contribution_limits": bool(
            accum_years > 0 and (primary_active or spouse_active)),
        # Config-based conservative applicability.  It deliberately does not
        # claim to instrument which stochastic path first retired before 65.
        "aca_marketplace": bool(
            start_age + 1 < 65 and retire_horizon > 0),
        "ssa_benefit_rules": bool(ss.get("enabled", True)),
        "ssa_statement_import": False,
        "espp_section_423": bool(contributions.get("espp_enabled")),
    }


def _value_evidence(
        component_id: str,
    config: Mapping[str, Any],
) -> tuple[str, list[str], dict[str, Any] | None]:
    if component_id == "contribution_limits":
        contributions = _group(config, "contributions")
        household = _group(config, "household")
        primary_earnings = sum(
            float(contributions.get(field, 0) or 0)
            for field in ("base_salary_pre", "bonus_pre", "ot_income_pre")
        )
        primary_caps = any(
            float(contributions.get(field, 0) or 0) > 0
            for field in (
                "pretax_401k_limit_y1", "roth_ira_limit_y1", "hsa_limit_y1")
        )
        spouse_earnings = sum(
            float(household.get(field, 0) or 0)
            for field in ("spouse_base_salary_pre", "spouse_bonus_pre")
        ) if household.get("enabled") else 0.0
        spouse_caps = any(
            float(household.get("spouse_" + field, 0) or 0) > 0
            for field in (
                "pretax_401k_limit_y1", "roth_ira_limit_y1", "hsa_limit_y1")
        )
        primary_active = primary_earnings > 0 and primary_caps
        spouse_active = bool(household.get("enabled")) and (
            spouse_earnings > 0 and spouse_caps)
        if not (primary_active or spouse_active):
            # Dormant fields are not evidence of an override.  Keep the
            # component's source vocabulary valid while making its non-use
            # explicit to the result-bound descriptor.
            return "matches_pack_value", [], {}

        configured: dict[str, Any] = {}
        reference: dict[str, Any] = {}
        growth_key = "contributions.irs_limit_growth"
        configured[growth_key] = contributions.get("irs_limit_growth")
        reference[growth_key] = CONTRIBUTION_LIMIT_RULES["irs_limit_growth"]
        if primary_active:
            for field in (
                    "pretax_401k_limit_y1", "roth_ira_limit_y1",
                    "hsa_limit_y1"):
                if (field == "hsa_limit_y1"
                        and contributions.get("hsa_coverage_tier", "none")
                        == "none"):
                    continue
                key = "contributions." + field
                configured[key] = contributions.get(field)
                reference[key] = CONTRIBUTION_LIMIT_RULES[field]
        if spouse_active:
            for field in (
                    "pretax_401k_limit_y1", "roth_ira_limit_y1",
                    "hsa_limit_y1"):
                if (field == "hsa_limit_y1"
                        and household.get(
                            "spouse_hsa_coverage_tier", "none") == "none"):
                    continue
                key = "household.spouse_" + field
                configured[key] = household.get("spouse_" + field)
                reference[key] = CONTRIBUTION_LIMIT_RULES[field]
        return _editable_value_state(configured, reference)
    if component_id == "aca_marketplace":
        aca_reference = {
            key: ACA_MARKETPLACE_RULES[key]
            for key in (
                "fpl_single_y0", "fpl_additional_person_y0",
                "fpl_threshold", "cap_pct_ira", "cap_pct_pre_ira",
            )
        }
        configured = dict(_group(config, "aca"))
        configured["default_scenario"] = configured.pop(
            "scenario", ACA_MARKETPLACE_RULES["default_scenario"])
        return _editable_value_state(configured, {
            "default_scenario": ACA_MARKETPLACE_RULES["default_scenario"],
            **aca_reference,
        })
    if component_id == "us_federal_tax" and _group(
            config, "tax_us").get("progressive"):
        return _editable_value_state(
            _group(config, "tax_us"),
            {"std_deduction": US_FEDERAL_RULES["std_deduction_single"]})
    if component_id == "ssa_benefit_rules":
        return _editable_value_state(
            _group(config, "social_security"),
            {"fra_age": SSA_RULES["fra_age"]})
    return "pack", [], None


def rule_pack_for_run(
        config: Mapping[str, Any],
        *,
        as_of: str | _dt.date,
) -> dict[str, Any]:
    """Build immutable display/audit metadata for one headline run.

    The returned value is JSON-safe.  It is meant to be frozen into
    ``result.meta.rule_pack`` and must not be inserted into the plan config.
    """
    evaluated = _as_date(as_of)
    applicable = _component_applicability(config)
    rows = []
    stale_ids: list[str] = []
    review_ids: list[str] = []
    applicable_ids: list[str] = []
    for source in _PACK_PAYLOAD["components"]:
        component_id = source["id"]
        is_applicable = bool(applicable.get(component_id))
        effective_source, mismatches, configured = _value_evidence(
            component_id, config)
        maintenance_due = _dt.date.fromisoformat(
            source["maintenance_due_on"])
        if not is_applicable:
            review_status = (
                "stale" if evaluated > maintenance_due
                else ("review_required"
                      if effective_source == "user_or_legacy_override"
                      else "within_recorded_window")
            )
            status = "not_used_at_run"
        elif evaluated > maintenance_due:
            review_status = "stale"
            status = "stale"
            stale_ids.append(component_id)
        elif effective_source == "user_or_legacy_override":
            review_status = "review_required"
            status = "review_required"
            review_ids.append(component_id)
        else:
            review_status = "within_recorded_window"
            status = "current"
        if is_applicable:
            applicable_ids.append(component_id)
        row = {
            "id": component_id,
            "label": source["label"],
            "source_vintage": source["source_vintage"],
            "maintenance_due_on": source["maintenance_due_on"],
            "applicability": (
                "applicable" if is_applicable else "not_used_at_run"),
            "review_status": review_status,
            "status": status,
            "effective_source": effective_source,
            "mismatched_fields": mismatches,
        }
        if configured is not None:
            row["configured_values"] = configured
        rows.append(row)

    # A stale maintenance claim is more urgent than an input mismatch.  Keep
    # both id lists so the UI/report can still disclose both facts.
    if stale_ids:
        overall = "stale"
    elif review_ids:
        overall = "review_required"
    else:
        overall = "current"
    return {
        "schema_version": RULE_PACK_SCHEMA_VERSION,
        "pack_id": RULE_PACK_ID,
        "content_sha256": RULE_PACK_SHA256,
        "delivery": "offline_embedded",
        "runtime_network_refresh": False,
        "evaluated_on": evaluated.isoformat(),
        "evaluation_basis": "config_applicability_not_path_instrumentation",
        "status": overall,
        "conclusion_status": overall,
        "applicable_component_ids": applicable_ids,
        "stale_component_ids": stale_ids,
        "review_required_component_ids": review_ids,
        "components": rows,
    }


def rule_pack_reference_defaults() -> dict[str, Any]:
    """Small server-owned descriptor used by browser-only input helpers."""
    return {
        "pack_id": RULE_PACK_ID,
        "content_sha256": RULE_PACK_SHA256,
        "contribution_limits": copy.deepcopy(CONTRIBUTION_LIMIT_RULES),
        # The quick estimate asks for HDHP coverage tier, so it needs that
        # tier's ceiling. `contribution_limits.hsa_limit_y1` is the SELF-ONLY
        # figure; capping a family plan at it would under-credit a user who
        # had just said they have family coverage. Base amounts only -- the
        # age-55 catch-up is deliberately not here, because the quick screen
        # does not ask the question the catch-up turns on.
        "hsa_tier_limits": {
            "self_only": float(PLAN_SHAPE_RULES["hsa_limit_self_only"]),
            "family": float(PLAN_SHAPE_RULES["hsa_limit_family"]),
        },
    }


def rule_pack_for_ssa_import(
        *, as_of: str | _dt.date,
) -> dict[str, Any]:
    """Descriptor for the separate statement-import seam."""
    evaluated = _as_date(as_of)
    source = _component("ssa_statement_import")
    status = ("stale" if evaluated > _dt.date.fromisoformat(
        source["maintenance_due_on"]) else "current")
    return {
        "pack_id": RULE_PACK_ID,
        "content_sha256": RULE_PACK_SHA256,
        "evaluated_on": evaluated.isoformat(),
        "component": {
            "id": source["id"],
            "label": source["label"],
            "source_vintage": source["source_vintage"],
            "maintenance_due_on": source["maintenance_due_on"],
            "status": status,
        },
    }
