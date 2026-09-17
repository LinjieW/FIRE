"""Strict offline loader for declarative non-US account-rule packs.

Roadmap 11 Phase 7 deliberately puts country facts in JSON rather than in
engine conditionals.  The loader is jurisdiction-agnostic: a two-letter code
selects ``rule_pack_<code>_accounts.json`` and the same validation is applied
to every pack.  Nothing here chooses a legal option for the user.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import sys
from functools import lru_cache
from typing import Any, Mapping


SCHEMA_VERSION = 1
GOVERNANCE_SCHEMA_VERSION = 1


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")


def _filename(jurisdiction: str) -> str:
    code = str(jurisdiction).upper()
    if not re.fullmatch(r"[A-Z]{2}", code):
        raise ValueError("jurisdiction must be a two-letter code")
    return "rule_pack_%s_accounts.json" % code.lower()


def _payload_path(jurisdiction: str) -> str:
    filename = _filename(jurisdiction)
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        candidate = os.path.join(frozen, filename)
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)


def _registry_path() -> str:
    frozen = getattr(sys, "_MEIPASS", None)
    if frozen:
        candidate = os.path.join(frozen, "country_pack_registry.json")
        if os.path.isfile(candidate):
            return candidate
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "country_pack_registry.json")


def _require_keys(row: Mapping[str, Any], keys: set[str], where: str) -> None:
    missing = sorted(keys - set(row))
    if missing:
        raise ValueError("%s missing required fields: %s" % (where, missing))


def _validate(payload: Mapping[str, Any], jurisdiction: str) -> None:
    _require_keys(payload, {
        "schema_version", "jurisdiction", "coverage_year",
        "maintenance_due_on", "account_types", "contribution_rules",
        "disposition_rules", "distribution_rules", "sources", "scope",
    }, "country pack")
    # A32's third deliverable: the per-jurisdiction disclaimer belongs in the
    # schema, not in a document beside it. The Canada pack has carried an
    # accurate `scope` sentence since it was written -- naming provincial tax,
    # CPP/OAS, locked-in plans and annuity pricing as outside it -- but nothing
    # required it and, measured on 2026-09-10, no code anywhere read it. An
    # unrequired, unread disclaimer is one the next pack can simply omit.
    if not str(payload["scope"]).strip():
        raise ValueError("country pack must state what is outside it")
    if payload["schema_version"] != SCHEMA_VERSION:
        raise ValueError("unsupported country-pack schema version")
    if not re.fullmatch(r"[A-Z]{2}", jurisdiction):
        raise ValueError("country-pack jurisdiction must be a two-letter code")
    if payload["jurisdiction"] != jurisdiction.upper():
        raise ValueError("country-pack jurisdiction does not match request")

    sources = payload["sources"]
    if not isinstance(sources, list) or not sources:
        raise ValueError("country pack must carry official sources")
    source_ids = set()
    for row in sources:
        _require_keys(row, {"id", "url", "vintage", "accessed_on"}, "source")
        if not str(row["url"]).startswith("https://"):
            raise ValueError("country-pack source must use https")
        if not row["vintage"] or not row["accessed_on"]:
            raise ValueError("country-pack source vintage is required")
        source_ids.add(row["id"])

    accounts = payload["account_types"]
    if not isinstance(accounts, list) or not accounts:
        raise ValueError("country pack must declare account types")
    account_keys = set()
    account_fields = set()
    for row in accounts:
        _require_keys(row, {
            "key", "field", "default_order", "withdrawal_rate",
            "tax_character", "early_penalty_rate", "early_penalty_age",
            "seasoned", "contribution_limited", "forced_distribution",
            "source_ids",
        }, "account type")
        if row["key"] in account_keys or row["field"] in account_fields:
            raise ValueError("country-pack account keys and fields must be unique")
        account_keys.add(row["key"])
        account_fields.add(row["field"])
        if not row["source_ids"] or not set(row["source_ids"]).issubset(source_ids):
            raise ValueError("account type names missing or unknown sources")

    dispositions = payload["disposition_rules"]
    for rule_id, rule in dispositions.items():
        _require_keys(rule, {"trigger_age", "options", "source_ids"},
                      "disposition rule %s" % rule_id)
        if "default_option" in rule:
            raise ValueError("a legal disposition choice may not acquire a default")
        if not rule["options"]:
            raise ValueError("disposition rule must declare its options")
        if not set(rule["source_ids"]).issubset(source_ids):
            raise ValueError("disposition rule names unknown sources")

    distributions = payload["distribution_rules"]
    for rule_id, rule in distributions.items():
        _require_keys(rule, {
            "balance_basis", "establishment_year_factor", "age_basis_options",
            "under_age", "under_age_formula", "factors", "terminal_age",
            "terminal_factor", "source_ids",
        }, "distribution rule %s" % rule_id)
        if not rule["age_basis_options"]:
            raise ValueError("distribution rule must expose its age-basis choice")
        if not set(rule["source_ids"]).issubset(source_ids):
            raise ValueError("distribution rule names unknown sources")

    for rule_id, rule in payload["contribution_rules"].items():
        _require_keys(rule, {"source_ids"},
                      "contribution rule %s" % rule_id)
        if not rule["source_ids"] or not set(rule["source_ids"]).issubset(source_ids):
            raise ValueError("contribution rule names missing or unknown sources")

    for row in accounts:
        disposition = row.get("disposition_rule")
        distribution = row.get("forced_distribution_rule")
        contribution = row.get("contribution_rule")
        if disposition is not None and disposition not in dispositions:
            raise ValueError("account type names unknown disposition rule")
        if distribution is not None and distribution not in distributions:
            raise ValueError("account type names unknown distribution rule")
        if contribution is not None and contribution not in payload["contribution_rules"]:
            raise ValueError("account type names unknown contribution rule")
        if bool(distribution) != bool(row["forced_distribution"]):
            raise ValueError("forced-distribution flag and rule must agree")


def qa_report(payload: Mapping[str, Any],
              previous: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Public candidate QA. It never approves or writes a pack."""
    problems = []
    jurisdiction = (str(payload.get("jurisdiction", "")).upper()
                    if isinstance(payload, Mapping) else "")
    try:
        _validate(payload, jurisdiction)
    except (TypeError, ValueError, KeyError) as exc:
        problems.append(str(exc))
    if previous is not None and not problems:
        changed = _canonical_bytes(payload) != _canonical_bytes(previous)
        if changed:
            current_sources = sorted(
                (row.get("id"), row.get("vintage"), row.get("accessed_on"))
                for row in payload.get("sources", []))
            previous_sources = sorted(
                (row.get("id"), row.get("vintage"), row.get("accessed_on"))
                for row in previous.get("sources", []))
            if current_sources == previous_sources:
                problems.append(
                    "country-pack content changed while every source vintage "
                    "and access date stayed unchanged")
    digest = (hashlib.sha256(_canonical_bytes(payload)).hexdigest()
              if not problems else None)
    return {"ok": not problems, "problems": problems,
            "jurisdiction": jurisdiction or None,
            "content_sha256": digest}


@lru_cache(maxsize=1)
def governance_registry() -> dict[str, Any]:
    try:
        with open(_registry_path(), encoding="utf-8") as handle:
            registry = json.load(handle)
    except OSError as exc:
        raise RuntimeError("country-pack governance registry is missing") from exc
    if registry.get("schema_version") != GOVERNANCE_SCHEMA_VERSION:
        raise RuntimeError("unsupported country-pack governance schema version")
    entries = registry.get("entries")
    if not isinstance(entries, list):
        raise RuntimeError("country-pack governance entries must be a list")
    seen = set()
    for entry in entries:
        _require_keys(entry, {
            "jurisdiction", "content_sha256", "status", "origin",
            "contributor_credit", "accepted_by", "accepted_on",
            "signature_state",
        }, "country-pack governance entry")
        key = (entry["jurisdiction"], entry["content_sha256"])
        if key in seen:
            raise RuntimeError("country-pack governance entry is duplicated")
        seen.add(key)
        if entry["status"] not in ("active", "revoked"):
            raise RuntimeError("country-pack governance status is invalid")
        if entry["status"] == "revoked" and not (
                entry.get("revoked_on") and entry.get("revocation_reason")):
            raise RuntimeError("revoked country pack must name date and reason")
    return registry


def governance_status(jurisdiction: str, content_sha256: str) -> dict[str, Any]:
    code = str(jurisdiction).upper()
    matches = [entry for entry in governance_registry()["entries"]
               if entry["jurisdiction"] == code
               and entry["content_sha256"] == content_sha256]
    if not matches:
        raise ValueError("country pack is not approved by the governance registry")
    return copy.deepcopy(matches[0])


@lru_cache(maxsize=None)
def _load(jurisdiction: str) -> tuple[dict[str, Any], str]:
    code = str(jurisdiction).upper()
    path = _payload_path(code)
    try:
        with open(path, encoding="utf-8") as handle:
            payload = json.load(handle)
    except OSError as exc:
        raise RuntimeError(
            "offline account-rule pack not found for %s at %s" % (code, path)
        ) from exc
    _validate(payload, code)
    digest = hashlib.sha256(_canonical_bytes(payload)).hexdigest()
    governance = governance_status(code, digest)
    if governance["status"] != "active":
        raise RuntimeError(
            "country pack %s is revoked: %s" %
            (code, governance.get("revocation_reason", "reason not recorded")))
    return payload, digest


def pack_for(jurisdiction: str) -> dict[str, Any]:
    payload, digest = _load(str(jurisdiction).upper())
    result = copy.deepcopy(payload)
    result["content_sha256"] = digest
    result["pack_id"] = "%s-accounts-%s" % (
        result["jurisdiction"].lower(), digest[:16])
    result["delivery"] = "offline_embedded"
    result["runtime_network_refresh"] = False
    result["governance"] = governance_status(
        result["jurisdiction"], digest)
    return result
