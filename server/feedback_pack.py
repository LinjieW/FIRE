"""Build the user-carried, explicitly reviewed A34 feedback pack.

The browser sends only sections the user selected.  This module resolves the
named fields from the live config, removes direct identifiers, and renders one
document into both Markdown and JSON.  It does not send, schedule, persist, or
count anything.

Removing a plan name is not anonymisation.  Ages, locations, dates and an
optionally selected amount can still identify a person in combination, so both
formats say that the pack is NOT de-identified before showing its contents.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import json
import re
from typing import Any


FORMAT = "fire-user-feedback-pack-v1"
_PATH = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\.(?:[A-Za-z_][A-Za-z0-9_]*|[0-9]+))*$")
_DIRECT_IDENTIFIERS = {
    "name": "plan_name_removed",
    "succession.accounts": "free_text_account_map_removed",
}
_KNOWN_ABSENT_PATHS = {
    # UI-only choices. Their absence is meaningful and must travel as
    # not-recorded/null rather than as the numeric zero their controls may
    # display as a default posture.
    "returns.equity_mu_shift",
    "relocation.destination",
    # Older saved plans predate the explicit NRA confirmation leaf.
    "ss_nra.residency_status",
}


def _object(value: Any, label: str) -> dict:
    if not isinstance(value, dict):
        raise TypeError("%s must be an object" % label)
    return value


def _text(row: dict, key: str, label: str) -> str:
    value = row.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError("%s.%s is required" % (label, key))
    return value.strip()


def _value(config: dict, path: str) -> tuple[str, Any]:
    if not _PATH.fullmatch(path):
        raise ValueError("invalid config path %s" % path)
    value: Any = config
    for token in path.split("."):
        if isinstance(value, dict) and token in value:
            value = value[token]
        elif isinstance(value, list) and token.isdigit() and int(token) < len(value):
            value = value[int(token)]
        else:
            if path in _KNOWN_ABSENT_PATHS:
                return "not_recorded", None
            raise ValueError("config path not found: %s" % path)
    return "recorded", copy.deepcopy(value)


def _selected_config(config: dict, selection: Any) -> tuple[list[dict], list[dict]]:
    if not isinstance(selection, list) or not selection:
        raise ValueError("at least one explicitly selected section is required")
    sections, removed, seen_sections, seen_paths = [], [], set(), set()
    for section_index, raw_section in enumerate(selection):
        label = "selection[%d]" % section_index
        section = _object(raw_section, label)
        section_id = _text(section, "id", label)
        title = _text(section, "title", label)
        if section_id in seen_sections:
            raise ValueError("section selected twice: %s" % section_id)
        seen_sections.add(section_id)
        raw_fields = section.get("fields")
        if not isinstance(raw_fields, list):
            raise TypeError("%s.fields must be a list" % label)
        fields = []
        for field_index, raw_field in enumerate(raw_fields):
            field_label = "%s.fields[%d]" % (label, field_index)
            field = _object(raw_field, field_label)
            path = _text(field, "path", field_label)
            display = _text(field, "label", field_label)
            money = field.get("money")
            if type(money) is not bool:
                raise ValueError("%s.money must be boolean" % field_label)
            if path in seen_paths:
                raise ValueError("config path selected twice: %s" % path)
            seen_paths.add(path)
            if path in _DIRECT_IDENTIFIERS:
                removed.append({
                    "path": path,
                    "reason_code": _DIRECT_IDENTIFIERS[path],
                })
                continue
            value_status, value = _value(config, path)
            fields.append({
                "path": path,
                "label": display,
                "money": money,
                "value_status": value_status,
                "value": value,
            })
        sections.append({"id": section_id, "title": title, "fields": fields})
    return sections, removed


def _markdown(document: dict, language: str) -> str:
    zh = language == "zh"
    warning = "**%s**" % document["privacy_warning"]
    lines = [
        "# " + ("FIRE 用户反馈包" if zh else "FIRE user feedback pack"),
        "", warning, "",
        ("格式" if zh else "Format") + ": `%s`  " % FORMAT,
        ("生成时间" if zh else "Generated at") + ": `%s`  " % document["generated_at"],
        ("用户逐段审阅" if zh else "Sections explicitly reviewed")
        + ": **%d**" % document["selection_count"],
        "",
        "## " + ("App 版本" if zh else "App version"), "",
        "```json", json.dumps(document["app_version"], ensure_ascii=False,
                                indent=2, sort_keys=True), "```", "",
        "## " + ("这份配置触发的局限" if zh else
                   "Limitations triggered by this configuration"), "",
        "```json", json.dumps(document["triggered_limitations"], ensure_ascii=False,
                                indent=2, sort_keys=True), "```", "",
        "## " + ("使用计数" if zh else "Usage counts"), "",
        ("_本第一刀不收集使用计数；这里是未测量（`null`），不是测得的零。_"
         if zh else
         "_This first slice does not collect usage counts. This is not measured (`null`), not a measured zero._"),
    ]
    for index, section in enumerate(document["selected_sections"], 1):
        lines += ["", "## %d. %s" % (index, section["title"]), ""]
        if not section["fields"]:
            lines.append("_%s_" % ("本段没有可导出的已选字段。" if zh else
                                     "This section has no exportable selected fields."))
            continue
        for field in section["fields"]:
            lines += ["- **%s** (`%s`)%s" % (
                field["label"], field["path"],
                " · " + ("金额" if zh else "amount") if field["money"] else ""),
                "  `value_status`: `%s`" % field["value_status"],
                "  ```json",
                json.dumps(field["value"], ensure_ascii=False, sort_keys=True),
                "  ```"]
    if document["direct_identifiers_removed"]:
        lines += ["", "## " + ("已移除的直接标识符" if zh else
                                 "Direct identifiers removed"), "",
                  "```json",
                  json.dumps(document["direct_identifiers_removed"],
                             ensure_ascii=False, indent=2, sort_keys=True),
                  "```"]
    return "\n".join(lines) + "\n"


def build(*, config: Any, selection: Any, app_version: Any,
          limitations: Any, language: str = "zh",
          generated_at: str | None = None) -> dict:
    """Return Markdown and JSON rendered from one validated document."""
    config = _object(config, "config")
    app_version = _object(app_version, "app_version")
    if language not in ("zh", "en"):
        raise ValueError("language must be zh or en")
    sections, removed = _selected_config(config, selection)
    privacy_warning = (
        "隐私提醒：这份导出未经脱敏。它移除了计划名和自由文本账户地图，但年龄、地点、日期与您主动勾选的金额仍可能共同识别个人。离开本 App 后，不再受本 App 的隐私属性保护。"
        if language == "zh" else
        "Privacy warning: this export is NOT de-identified. It removes the plan name and free-text account map, but ages, locations, dates, and amounts you explicitly selected may still identify a person in combination. Once it leaves this app, it is not covered by this app's privacy properties.")
    document = {
        "format": FORMAT,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "language": language,
        "de_identified": False,
        "contains_real_personal_data": True,
        "privacy_warning": privacy_warning,
        "selected_by_user": True,
        "selection_count": len(sections),
        "selected_field_count": sum(len(s["fields"]) for s in sections),
        "app_version": copy.deepcopy(app_version),
        "triggered_limitations": copy.deepcopy(limitations),
        "usage_counts": None,
        "usage_counts_reason": "not_collected_in_a34_first_slice",
        "direct_identifiers_removed": removed,
        "selected_sections": sections,
    }
    return {
        "markdown": _markdown(document, language),
        "json": document,
        "de_identified": False,
    }
