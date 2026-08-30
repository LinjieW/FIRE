"""One narrow cross-plan detector for the B15 first slice.

The detector compares two explicit age inputs from two immutable plan versions.
It does not infer identity, support, consent, or any model outcome.  Callers must
load both the source and normalized configs from the exact pinned versions:
source config proves that the user supplied a field, while normalized config is
the value the plan actually runs with.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional


DETECTOR_VERSION = "parent-age-v1"
SEX_DETECTOR_VERSION = "parent-sex-v1"
RELATION = "parent_identity"
AS_OF_BASIS = "user_confirmed_same_date"


def _record(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(config: dict) -> list:
    rows = _record(config.get("parents")).get("parents")
    return rows if isinstance(rows, list) else []


def _age(value: Any) -> Optional[int]:
    """An exact whole-year age, never a bool and never rounded."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        out = value
    elif isinstance(value, float) and value.is_integer():
        out = int(value)
    else:
        return None
    return out if 0 <= out <= 130 else None


def _valid_date(value: Any) -> Optional[str]:
    if not isinstance(value, str) or len(value) != 10:
        return None
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        return None
    return value if parsed.isoformat() == value else None


def _result(*, label: str, household_age: Optional[int],
            parent_age: Optional[int], applicable: bool,
            finding: Optional[str], delta_years: Optional[int],
            reason_code: str, reason: str, as_of_date: Optional[str],
            as_of_basis: Optional[str]) -> dict:
    return {
        "detector_version": DETECTOR_VERSION,
        "parent_slot_label": label,
        "household_age": household_age,
        "parent_age": parent_age,
        "applicable": bool(applicable),
        "finding": finding,
        "delta_years": delta_years,
        "reason_code": reason_code,
        "reason": reason,
        "as_of_date": as_of_date,
        "as_of_basis": as_of_basis,
    }


def evaluate(*, household_source: dict, household_normalized: dict,
             parent_source: dict, parent_normalized: dict,
             parent_slot_index: int, same_date_confirmed: bool,
             as_of_date: Optional[str]) -> dict:
    """Compare one explicit parent row with one explicit parent-plan age.

    Structural applicability is checked before the date attestation.  A user is
    not asked to attest a common date for fields that are not present in the
    pinned evidence.  Request-shape validation remains the storage seam's job;
    this function reports modelled-but-unavailable evidence as N/A.
    """
    if (isinstance(parent_slot_index, bool)
            or not isinstance(parent_slot_index, int)
            or parent_slot_index < 0):
        raise ValueError("parent_slot_index must be a non-negative integer")
    if not isinstance(same_date_confirmed, bool):
        raise ValueError("same_date_confirmed must be a boolean")

    source_rows = _rows(household_source)
    normalized_rows = _rows(household_normalized)
    source_row = (source_rows[parent_slot_index]
                  if parent_slot_index < len(source_rows)
                  and isinstance(source_rows[parent_slot_index], dict) else {})
    normalized_row = (normalized_rows[parent_slot_index]
                      if parent_slot_index < len(normalized_rows)
                      and isinstance(normalized_rows[parent_slot_index], dict) else {})
    label_value = normalized_row.get("label", source_row.get("label"))
    label = (str(label_value).strip() if label_value not in (None, "")
             else "parent[%d]" % parent_slot_index)

    mode = _record(household_normalized.get("parents")).get("mode")
    if mode in (None, "off"):
        return _result(
            label=label, household_age=None, parent_age=None,
            applicable=False, finding=None, delta_years=None,
            reason_code="parents_module_off",
            reason="the household plan does not model parents",
            as_of_date=None, as_of_basis=None)
    if parent_slot_index >= len(source_rows) or not source_row:
        return _result(
            label=label, household_age=None, parent_age=None,
            applicable=False, finding=None, delta_years=None,
            reason_code="parent_slot_missing",
            reason="the selected parent slot is not present in the pinned household version",
            as_of_date=None, as_of_basis=None)
    if "current_age" not in source_row:
        return _result(
            label=label, household_age=None, parent_age=None,
            applicable=False, finding=None, delta_years=None,
            reason_code="household_age_not_explicit",
            reason="the selected parent age was not explicitly supplied",
            as_of_date=None, as_of_basis=None)
    household_age = _age(normalized_row.get("current_age"))
    if household_age is None:
        return _result(
            label=label, household_age=None, parent_age=None,
            applicable=False, finding=None, delta_years=None,
            reason_code="household_age_unavailable",
            reason="the selected parent age is not a usable whole-year age",
            as_of_date=None, as_of_basis=None)

    parent_source_state = _record(parent_source.get("state"))
    if "start_age" not in parent_source_state:
        return _result(
            label=label, household_age=household_age, parent_age=None,
            applicable=False, finding=None, delta_years=None,
            reason_code="parent_age_not_explicit",
            reason="the parent plan start age was not explicitly supplied",
            as_of_date=None, as_of_basis=None)
    parent_age = _age(_record(parent_normalized.get("state")).get("start_age"))
    if parent_age is None:
        return _result(
            label=label, household_age=household_age, parent_age=None,
            applicable=False, finding=None, delta_years=None,
            reason_code="parent_age_unavailable",
            reason="the parent plan start age is not a usable whole-year age",
            as_of_date=None, as_of_basis=None)

    if not same_date_confirmed:
        return _result(
            label=label, household_age=household_age, parent_age=parent_age,
            applicable=False, finding=None, delta_years=None,
            reason_code="same_date_not_confirmed",
            reason="the user did not confirm that both ages describe the same date",
            as_of_date=None, as_of_basis=None)
    checked_date = _valid_date(as_of_date)
    if checked_date is None:
        raise ValueError("as_of_date must be a valid ISO YYYY-MM-DD date")

    delta = parent_age - household_age
    finding = "match" if delta == 0 else "contradiction"
    return _result(
        label=label, household_age=household_age, parent_age=parent_age,
        applicable=True, finding=finding, delta_years=delta,
        reason_code="measured_" + finding,
        reason=("the two explicit ages match on the user-confirmed date"
                if delta == 0 else
                "the two explicit ages differ on the user-confirmed date"),
        as_of_date=checked_date, as_of_basis=AS_OF_BASIS)


def _sex_result(*, label: str, household_sex: Optional[str],
                parent_sex: Optional[str], applicable: bool,
                finding: Optional[str], reason_code: str,
                reason: str) -> dict:
    return {
        "detector_version": SEX_DETECTOR_VERSION,
        "parent_slot_label": label,
        "household_sex": household_sex,
        "parent_sex": parent_sex,
        "applicable": bool(applicable),
        "finding": finding,
        "reason_code": reason_code,
        "reason": reason,
    }


def evaluate_sex(*, household_source: dict, household_normalized: dict,
                 parent_source: dict, parent_normalized: dict,
                 parent_slot_index: int) -> dict:
    """Compare two explicit, person-specific mortality-sex declarations.

    Normalized defaults never establish that the user declared a value.  The
    parent-plan value ``unisex`` is a valid modelling choice but is not a
    person-specific declaration, so it produces N/A rather than a match or a
    contradiction.
    """
    if (isinstance(parent_slot_index, bool)
            or not isinstance(parent_slot_index, int)
            or parent_slot_index < 0):
        raise ValueError("parent_slot_index must be a non-negative integer")

    source_rows = _rows(household_source)
    normalized_rows = _rows(household_normalized)
    source_row = (source_rows[parent_slot_index]
                  if parent_slot_index < len(source_rows)
                  and isinstance(source_rows[parent_slot_index], dict) else {})
    normalized_row = (normalized_rows[parent_slot_index]
                      if parent_slot_index < len(normalized_rows)
                      and isinstance(normalized_rows[parent_slot_index], dict) else {})
    label_value = normalized_row.get("label", source_row.get("label"))
    label = (str(label_value).strip() if label_value not in (None, "")
             else "parent[%d]" % parent_slot_index)

    mode = _record(household_normalized.get("parents")).get("mode")
    if mode in (None, "off"):
        return _sex_result(
            label=label, household_sex=None, parent_sex=None,
            applicable=False, finding=None, reason_code="parents_module_off",
            reason="the household plan does not model parents")
    if parent_slot_index >= len(source_rows) or not source_row:
        return _sex_result(
            label=label, household_sex=None, parent_sex=None,
            applicable=False, finding=None, reason_code="parent_slot_missing",
            reason="the selected parent slot is not present in the pinned household version")
    household_declared_sex = source_row.get("sex")
    if "sex" not in source_row or household_declared_sex in (None, ""):
        return _sex_result(
            label=label, household_sex=None, parent_sex=None,
            applicable=False, finding=None,
            reason_code="household_sex_not_explicit",
            reason="the selected parent's sex was not explicitly supplied")
    if household_declared_sex not in ("male", "female"):
        return _sex_result(
            label=label, household_sex=None, parent_sex=None,
            applicable=False, finding=None,
            reason_code="household_sex_not_person_specific",
            reason="the household value is not a person-specific male or female declaration")
    household_sex = household_declared_sex

    parent_source_mortality = _record(parent_source.get("mortality"))
    parent_declared_sex = parent_source_mortality.get("sex")
    if ("sex" not in parent_source_mortality
            or parent_declared_sex in (None, "")):
        return _sex_result(
            label=label, household_sex=household_sex, parent_sex=None,
            applicable=False, finding=None,
            reason_code="parent_sex_not_explicit",
            reason="the parent plan mortality sex was not explicitly supplied")
    if parent_declared_sex not in ("male", "female"):
        return _sex_result(
            label=label, household_sex=household_sex, parent_sex=None,
            applicable=False, finding=None,
            reason_code="parent_sex_not_person_specific",
            reason="the parent plan uses a non-person-specific mortality table")
    parent_sex = parent_declared_sex

    finding = "match" if household_sex == parent_sex else "contradiction"
    return _sex_result(
        label=label, household_sex=household_sex, parent_sex=parent_sex,
        applicable=True, finding=finding,
        reason_code="measured_" + finding,
        reason=("the two explicit sex declarations match"
                if finding == "match" else
                "the two explicit sex declarations differ"))
