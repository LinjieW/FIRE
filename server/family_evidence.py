"""Build a selected, self-describing family evidence brief.

The caller supplies only receipts the user explicitly selected from the live
parent-identity read model.  This module validates and narrows those receipts;
it never reads or writes a plan and never re-runs a detector.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


FORMAT = "fire-family-evidence-brief-v1"
FRESHNESS = {"current", "stale_version", "stale_lifecycle", "inactive", "ended"}
FINDINGS = {"match", "contradiction"}


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise TypeError("%s must be an object" % label)
    return value


def _text(row: dict, key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s.%s is required" % (label, key))
    return value


def _optional_text(row: dict, key: str, label: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s.%s must be text or null" % (label, key))
    return value


def _boolean(row: dict, key: str, label: str) -> bool:
    value = row.get(key)
    if type(value) is not bool:  # bool is deliberate; 0 is not an attestation.
        raise ValueError("%s.%s must be boolean" % (label, key))
    return value


def _number_or_none(row: dict, key: str, label: str) -> int | float | None:
    value = row.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("%s.%s must be numeric or null" % (label, key))
    return value


def _finding(row: dict, label: str) -> tuple[bool, str | None]:
    applicable = _boolean(row, "applicable", label)
    finding = row.get("finding")
    if applicable:
        if finding not in FINDINGS:
            raise ValueError("%s.finding must be measured" % label)
    elif finding is not None:
        raise ValueError("%s.finding must be null when not applicable" % label)
    return applicable, finding


def _freshness(row: dict, label: str) -> tuple[str, str]:
    freshness = _text(row, "freshness", label)
    if freshness not in FRESHNESS:
        raise ValueError("%s.freshness is unknown" % label)
    return freshness, _text(row, "freshness_reason", label)


def _relationship(raw: Any) -> dict:
    row = _object(raw, "relationship")
    relation = _text(row, "relation", "relationship")
    if relation != "parent_identity":
        raise ValueError("relationship.relation must be parent_identity")
    return {
        "relation": relation,
        "link_id": _text(row, "link_id", "relationship"),
        "household_plan_id": _text(row, "household_plan_id", "relationship"),
        "household_display_name": _text(
            row, "household_display_name", "relationship"),
        "household_status": _text(row, "household_status", "relationship"),
        "parent_plan_id": _text(row, "parent_plan_id", "relationship"),
        "parent_display_name": _text(row, "parent_display_name", "relationship"),
        "parent_status": _text(row, "parent_status", "relationship"),
        "link_created_at": _text(row, "created_at", "relationship"),
        "link_ended_at": _optional_text(row, "ended_at", "relationship"),
        "link_ended_reason": _optional_text(row, "ended_reason", "relationship"),
    }


def _age(raw: Any, relationship: dict) -> dict:
    row = _object(raw, "evaluation")
    label = "evaluation"
    evaluation_id = _text(row, "evaluation_id", label)
    if _text(row, "link_id", label) != relationship["link_id"]:
        raise ValueError("evaluation belongs to a different relationship")
    applicable, finding = _finding(row, label)
    delta = _number_or_none(row, "delta_years", label)
    if not applicable and delta is not None:
        raise ValueError("evaluation.delta_years must be null when not applicable")
    freshness, freshness_reason = _freshness(row, label)
    slot = row.get("parent_slot_index")
    if isinstance(slot, bool) or not isinstance(slot, int) or slot < 0:
        raise ValueError("evaluation.parent_slot_index must be a non-negative integer")
    return {
        "evaluation_id": evaluation_id,
        "household_plan_version_id": _text(
            row, "household_plan_version_id", label),
        "parent_plan_version_id": _text(row, "parent_plan_version_id", label),
        "household_status_revision": row.get("household_status_revision"),
        "parent_status_revision": row.get("parent_status_revision"),
        "parent_slot_index": slot,
        "parent_slot_label": _text(row, "parent_slot_label", label),
        "as_of_date": _optional_text(row, "as_of_date", label),
        "as_of_basis": _optional_text(row, "as_of_basis", label),
        "detector_version": _text(row, "detector_version", label),
        "household_age": _number_or_none(row, "household_age", label),
        "parent_age": _number_or_none(row, "parent_age", label),
        "applicable": applicable,
        "finding": finding,
        "delta_years": delta,
        "reason_code": _text(row, "reason_code", label),
        "reason": _text(row, "reason", label),
        "created_at": _text(row, "created_at", label),
        "freshness": freshness,
        "freshness_reason": freshness_reason,
    }


def _sex(raw: Any, relationship: dict, age: dict) -> dict:
    if raw is None:
        return {
            "status": "not_recorded",
            "reason_code": "sex_not_recorded",
            "reason": "this older age evaluation has no recorded sex evidence",
        }
    row = _object(raw, "sex_evaluation")
    label = "sex_evaluation"
    if _text(row, "age_evaluation_id", label) != age["evaluation_id"]:
        raise ValueError("sex evidence belongs to a different age evaluation")
    if _text(row, "link_id", label) != relationship["link_id"]:
        raise ValueError("sex evidence belongs to a different relationship")
    for key in ("household_plan_version_id", "parent_plan_version_id",
                "parent_slot_index", "parent_slot_label"):
        if row.get(key) != age[key]:
            raise ValueError("sex evidence %s does not match its age root" % key)
    applicable, finding = _finding(row, label)
    freshness, freshness_reason = _freshness(row, label)
    if freshness != age["freshness"]:
        raise ValueError("sex evidence freshness does not match its age root")
    return {
        "status": "recorded",
        "sex_evaluation_id": _text(row, "sex_evaluation_id", label),
        "age_evaluation_id": age["evaluation_id"],
        "detector_version": _text(row, "detector_version", label),
        "household_sex": _optional_text(row, "household_sex", label),
        "parent_sex": _optional_text(row, "parent_sex", label),
        "applicable": applicable,
        "finding": finding,
        "reason_code": _text(row, "reason_code", label),
        "reason": _text(row, "reason", label),
        "created_at": _text(row, "created_at", label),
        "freshness": freshness,
        "freshness_reason": freshness_reason,
    }


def _escape(value: Any) -> str:
    if value is None:
        return "N/A"
    text = str(value)
    for char in ("\\", "`", "*", "_", "[", "]", "|"):
        text = text.replace(char, "\\" + char)
    return text.replace("\n", " ").replace("\r", " ")


def _markdown(document: dict, language: str) -> str:
    zh = language == "zh"
    title = "家庭计划证据摘要" if zh else "Family plan evidence brief"
    warning = (
        "**隐私提醒：这份导出未经脱敏，包含真实计划名称、年龄与性别输入。离开本 App 后，不再受本 App 的隐私属性保护。**"
        if zh else
        "**Privacy warning: this export is NOT de-identified. It contains real plan names, ages, and sex inputs. Once it leaves this app, it is not covered by this app's privacy properties.**")
    boundary = (
        "本摘要只重放用户选择的既有证据。它不证明身份、同意、法律授权、赡养能力、整体计划一致性或联合模拟结果。"
        if zh else
        "This brief only replays existing evidence selected by the user. It does not prove identity, consent, legal authority, support capacity, whole-plan consistency, or a joint simulation result.")
    lines = ["# " + title, "", warning, "", boundary, "",
             ("格式" if zh else "Format") + ": `" + FORMAT + "`  ",
             ("生成时间" if zh else "Generated at") + ": `" + document["generated_at"] + "`  ",
             ("用户选择的核对记录" if zh else "Receipts selected by the user")
             + ": **%d**" % document["selection_count"]]
    for index, receipt in enumerate(document["receipts"], 1):
        rel, age, sex = receipt["relationship"], receipt["age_evidence"], receipt["sex_evidence"]
        lines += ["", "## %d. %s → %s" % (
            index, _escape(rel["household_display_name"]),
            _escape(rel["parent_display_name"])), "",
            "- %s: `%s`" % (("关系" if zh else "Relationship"), rel["relation"]),
            "- `link_id`: `%s`" % rel["link_id"],
            "- %s: `%s` → `%s`" % (("稳定计划 ID" if zh else "Stable plan IDs"),
                                      rel["household_plan_id"], rel["parent_plan_id"]),
            "- %s: `%s` → `%s`" % (("精确版本" if zh else "Exact versions"),
                                      age["household_plan_version_id"],
                                      age["parent_plan_version_id"]),
            "- `evaluation_id`: `%s`" % age["evaluation_id"],
            "- %s: `%s` — %s" % (("导出时新鲜度" if zh else "Freshness at export"),
                                     age["freshness"], _escape(age["freshness_reason"])),
            "", "### " + ("年龄证据" if zh else "Age evidence"), "",
            "- %s: `%s`" % (("检测器" if zh else "Detector"), age["detector_version"]),
            "- %s: `%s` · %s" % (("父母槽位" if zh else "Parent slot"),
                                    age["parent_slot_index"], _escape(age["parent_slot_label"])),
            "- %s: `%s` (`%s`)" % (("共同日期" if zh else "Common date"),
                                      _escape(age["as_of_date"]), _escape(age["as_of_basis"])),
            "- %s: `%s` / `%s`" % (("家庭计划值 / 父母计划值" if zh else
                                      "Household value / parent-plan value"),
                                      _escape(age["household_age"]), _escape(age["parent_age"])),
            "- %s: `%s` · `%s` · %s" % (("结论" if zh else "Finding"),
                                           _escape(age["finding"]), age["reason_code"],
                                           _escape(age["reason"])),
            "", "### " + ("性别证据" if zh else "Sex evidence"), ""]
        if sex["status"] == "not_recorded":
            lines.append("- `%s` · %s" % (sex["reason_code"], _escape(sex["reason"])))
        else:
            lines += [
                "- %s: `%s`" % (("检测器" if zh else "Detector"), sex["detector_version"]),
                "- %s: `%s` / `%s`" % (("家庭计划值 / 父母计划值" if zh else
                                          "Household value / parent-plan value"),
                                          _escape(sex["household_sex"]), _escape(sex["parent_sex"])),
                "- %s: `%s` · `%s` · %s" % (("结论" if zh else "Finding"),
                                               _escape(sex["finding"]), sex["reason_code"],
                                               _escape(sex["reason"])),
            ]
    return "\n".join(lines) + "\n"


def build(entries: Any, language: str = "zh",
          generated_at: str | None = None) -> dict:
    if language not in ("zh", "en"):
        raise ValueError("language must be zh or en")
    if not isinstance(entries, list) or not entries:
        raise ValueError("at least one explicitly selected evaluation is required")
    receipts = []
    seen = set()
    for index, raw in enumerate(entries):
        entry = _object(raw, "evidence[%d]" % index)
        relationship = _relationship(entry.get("relationship"))
        age = _age(entry.get("evaluation"), relationship)
        key = (relationship["link_id"], age["evaluation_id"])
        if key in seen:
            raise ValueError("the same evaluation was selected twice")
        seen.add(key)
        receipts.append({
            "relationship": relationship,
            "age_evidence": age,
            "sex_evidence": _sex(
                entry["evaluation"].get("sex_evaluation"), relationship, age),
        })
    document = {
        "format": FORMAT,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "language": language,
        "de_identified": False,
        "contains_real_personal_data": True,
        "selected_by_user": True,
        "selection_count": len(receipts),
        "boundaries": [
            "does_not_prove_identity", "does_not_prove_consent",
            "is_not_legal_authority", "does_not_measure_support_capacity",
            "does_not_prove_whole_plan_consistency", "is_not_a_joint_simulation",
        ],
        "receipts": receipts,
    }
    return {"markdown": _markdown(document, language), "json": document}
