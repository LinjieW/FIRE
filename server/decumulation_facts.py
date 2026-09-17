"""Exact annual facts archived for the Roadmap 11 execution Cockpit.

This module deliberately does not derive calendar year from a period or an
RMD basis from today's balance.  A caller either supplies the historical fact
at the annual check-in boundary or the Cockpit reports that section as
unmeasured.
"""
from __future__ import annotations

import json
import math
import sqlite3
from typing import Any, Optional

import account_schema as ACCOUNT_SCHEMA
import persistence as PERSISTENCE


FORCED_BALANCE_FIELDS = tuple(
    account.field for account in ACCOUNT_SCHEMA.ordered_types()
    if account.forced_distribution
)


class DecumulationFactError(ValueError):
    """A named annual-fact validation refusal."""


def validate_request(value: Any) -> Optional[dict]:
    """Validate an optional exact-facts object without filling any gaps."""
    if value is None:
        return None
    if not isinstance(value, dict):
        raise DecumulationFactError("decumulation_facts must be an object")
    allowed = {"calendar_year", "rmd_prior_year_end_balances"}
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise DecumulationFactError(
            "decumulation_facts has unknown field(s): %s" % ", ".join(unknown))
    year = value.get("calendar_year")
    if isinstance(year, bool) or not isinstance(year, int) or year <= 0:
        raise DecumulationFactError(
            "decumulation_facts.calendar_year must be a positive integer")
    balances = value.get("rmd_prior_year_end_balances")
    if not isinstance(balances, dict):
        raise DecumulationFactError(
            "decumulation_facts.rmd_prior_year_end_balances must be an object")
    missing = [field for field in FORCED_BALANCE_FIELDS
               if field not in balances]
    unknown_balances = sorted(set(balances) - set(FORCED_BALANCE_FIELDS))
    if missing:
        raise DecumulationFactError(
            "decumulation_facts.rmd_prior_year_end_balances missing required "
            "field(s): %s" % ", ".join(missing))
    if unknown_balances:
        raise DecumulationFactError(
            "decumulation_facts.rmd_prior_year_end_balances has unknown "
            "field(s): %s" % ", ".join(unknown_balances))
    normalized = {}
    for field in FORCED_BALANCE_FIELDS:
        amount = balances[field]
        if (isinstance(amount, bool) or not isinstance(amount, (int, float))
                or not math.isfinite(float(amount)) or amount < 0):
            raise DecumulationFactError(
                "decumulation_facts.rmd_prior_year_end_balances.%s must be "
                "finite and non-negative" % field)
        normalized[field] = float(amount)
    return {
        "calendar_year": year,
        "rmd_prior_year_end_balances": normalized,
    }


def insert(conn: sqlite3.Connection, checkin_id: str, facts: dict,
           *, created_at: str) -> None:
    """Append one immutable fact row rooted in an existing CheckIn."""
    conn.execute(
        "INSERT INTO decumulation_checkin_facts ("
        "checkin_id,calendar_year,rmd_prior_year_end_balances_json,created_at) "
        "VALUES (?,?,?,?)",
        (checkin_id, facts["calendar_year"], PERSISTENCE.canonical_json_text(
            facts["rmd_prior_year_end_balances"]), created_at))


def load(conn: sqlite3.Connection, checkin_id: str) -> Optional[dict]:
    """Read one fact row; absence remains absence, never a derived value."""
    row = conn.execute(
        "SELECT calendar_year,rmd_prior_year_end_balances_json "
        "FROM decumulation_checkin_facts WHERE checkin_id=?", (checkin_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "calendar_year": int(row[0]),
        "rmd_prior_year_end_balances": json.loads(row[1]),
    }
