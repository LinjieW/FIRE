"""Server-only formal localStorage migration M1/M2.

M1 stops at the disposable preview/stage boundary.  M2 parses the distinct
formal envelope and imports only into a disposable v8 archive; it does not
change the live archive, alter authority, advance a generation, or read/write
WebKit localStorage.  The existing ``migration_bridge`` shadow format is
intentionally not imported here so that a shadow hash cannot be relabeled as
formal migration evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import engine_adapter as ENGINE
import persistence as PERSISTENCE
import recovery as RECOVERY


FORMAL_ENVELOPE_FORMAT = "fire-localstorage-envelope-v1"
FORMAL_PROJECTION_FORMAT = "fire-migration-projection-v1"
PROJECTION_VERSION = "migration-projection-v1"
NORMALIZER_VERSION = "migration-normalizer-v1"
CONFIG_SCHEMA_VERSION = 2
FORMAL_KEYS = ("fire_draft", "fire_plans_v1")
MAX_FORMAL_ENVELOPE_BYTES = RECOVERY.MAX_ENVELOPE_BYTES
# The HTTP request contains one formal envelope plus a small, fixed request
# wrapper (and an optional retry nonce).  Keep this budget next to the formal
# contract so the parser and the transport gate cannot drift apart.
FORMAL_HTTP_WRAPPER_BUDGET_BYTES = 4096
OPERATION_ID_RE = re.compile(r"^mig_[0-9a-f]{32}$")
RETRY_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{8,80}$")
HEX64 = frozenset("0123456789abcdef")
FORMAL_JSON_MAX_DEPTH = 128
SUPPORTED_DRAFT_VERSIONS = frozenset((1, 2))
M2_SOURCE_KINDS = frozenset(("plan", "draft", "legacy_checkin", "unknown"))
M2_RESOLUTIONS = frozenset((
    "created", "reused_validated", "evidence_only", "quarantined",
    "structural_missing"))
M2_TARGET_KINDS = frozenset((
    "plan", "plan_version", "recovered_draft", "legacy_checkin_evidence"))


class FormalMigrationError(RECOVERY.RecoveryError):
    """A formal M1 request failed closed."""

    def __init__(self, message: str, *, reason_code: str = "migration_failed",
                 pointer: Optional[str] = None):
        super().__init__(message)
        self.reason_code = reason_code
        self.pointer = pointer


class FormalMigrationConflict(RECOVERY.RecoveryConflict, FormalMigrationError):
    """The formal operation identity or current source state conflicts."""

    def __init__(self, message: str, *, reason_code: str = "migration_conflict",
                 pointer: Optional[str] = None):
        RECOVERY.RecoveryConflict.__init__(self, message)
        self.reason_code = reason_code
        self.pointer = pointer


class FormalEnvelopeError(FormalMigrationError):
    """The exact formal envelope contract was not satisfied."""


def _canonical(value: Any) -> bytes:
    try:
        return PERSISTENCE.canonical_json_bytes(value)
    except PERSISTENCE.PersistenceError as exc:
        raise FormalEnvelopeError(
            "formal envelope is not canonical JSON",
            reason_code="envelope_not_canonical") from exc


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256(_canonical(value))


def _is_hex64(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and not (set(value) - HEX64))


def _envelope_error(message: str, reason_code: str, pointer: str) -> None:
    raise FormalEnvelopeError(message, reason_code=reason_code, pointer=pointer)


def validate_envelope(envelope: Any) -> dict[str, Any]:
    """Validate and identify the exact formal two-key envelope.

    The returned ``canonical_bytes`` are the only bytes persisted by M1.  The
    function accepts no shadow ``envelope_version``/``entries`` shape and does
    not run the business-config normalizer.
    """
    if not isinstance(envelope, dict):
        _envelope_error("formal envelope must be an object",
                        "envelope_type_invalid", "/")
    if set(envelope) != {"format", "keys", "key_sha256"}:
        _envelope_error("formal envelope fields are invalid",
                        "envelope_fields_invalid", "/")
    if envelope["format"] != FORMAL_ENVELOPE_FORMAT:
        _envelope_error("formal envelope format is unsupported",
                        "envelope_format_invalid", "/format")

    keys = envelope["keys"]
    if not isinstance(keys, list) or len(keys) != len(FORMAL_KEYS):
        _envelope_error("formal envelope key list is invalid",
                        "key_list_invalid", "/keys")
    key_hashes = envelope["key_sha256"]
    if not isinstance(key_hashes, dict) or set(key_hashes) != set(FORMAL_KEYS):
        _envelope_error("formal envelope key hash map is invalid",
                        "key_hash_map_invalid", "/key_sha256")

    raw_hashes: dict[str, Optional[str]] = {}
    for index, expected_name in enumerate(FORMAL_KEYS):
        pointer = f"/keys/{index}"
        entry = keys[index]
        if (not isinstance(entry, dict)
                or set(entry) != {"name", "present", "value"}):
            _envelope_error("formal envelope key entry is invalid",
                            "key_entry_invalid", pointer)
        if entry["name"] != expected_name:
            _envelope_error("formal envelope key order is invalid",
                            "key_order_invalid", pointer + "/name")
        if type(entry["present"]) is not bool:
            _envelope_error("formal envelope presence flag is invalid",
                            "presence_invalid", pointer + "/present")
        present = entry["present"]
        value = entry["value"]
        if present and not isinstance(value, str):
            _envelope_error("present localStorage value must be a string",
                            "value_type_invalid", pointer + "/value")
        if not present and value is not None:
            _envelope_error("absent localStorage value must be null",
                            "absent_value_invalid", pointer + "/value")
        supplied_hash = key_hashes[expected_name]
        if present:
            try:
                raw = value.encode("utf-8")
            except UnicodeEncodeError as exc:
                _envelope_error("localStorage value is not valid UTF-8",
                                "value_utf8_invalid", pointer + "/value")
            expected_hash = _sha256(raw)
            if supplied_hash != expected_hash:
                _envelope_error("localStorage raw hash does not match value",
                                "raw_hash_mismatch",
                                f"/key_sha256/{expected_name}")
            if not _is_hex64(supplied_hash):
                _envelope_error("localStorage raw hash is invalid",
                                "raw_hash_invalid",
                                f"/key_sha256/{expected_name}")
            raw_hashes[expected_name] = supplied_hash
        else:
            if supplied_hash is not None:
                _envelope_error("absent localStorage key must have null hash",
                                "absent_hash_invalid",
                                f"/key_sha256/{expected_name}")
            raw_hashes[expected_name] = None

    canonical_bytes = _canonical(envelope)
    if len(canonical_bytes) > MAX_FORMAL_ENVELOPE_BYTES:
        _envelope_error("formal envelope is too large", "envelope_too_large", "/")
    envelope_sha256 = _sha256(canonical_bytes)
    source_identity = {
        "envelope_sha256": envelope_sha256,
        "projection_version": PROJECTION_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "config_schema_version": CONFIG_SCHEMA_VERSION,
    }
    return {
        "envelope": envelope,
        "canonical_bytes": canonical_bytes,
        "envelope_sha256": envelope_sha256,
        "raw_hashes": raw_hashes,
        "source_identity": source_identity,
        "source_identity_sha256": _sha256_json(source_identity),
    }


def _idempotency_key(source_identity: dict, expected_generation: str,
                     attempt_id: str) -> str:
    return _sha256_json({
        "kind": "migration",
        "source_identity": source_identity,
        "expected_generation": expected_generation,
        "attempt_id": attempt_id,
    })


def _request_fingerprint(details: dict, expected_generation: str,
                         attempt_id: str) -> str:
    return _sha256_json({
        "kind": "migration_preview",
        "envelope": details["envelope"],
        "source_identity": details["source_identity"],
        "expected_generation": expected_generation,
        "attempt_id": attempt_id,
    })


def _operation_id_valid(value: Any) -> bool:
    return isinstance(value, str) and OPERATION_ID_RE.fullmatch(value) is not None


def _parse_formal_json(raw: str) -> Any:
    """Parse one formal localStorage value without importing shadow semantics."""
    def reject_constant(value: str):
        raise ValueError(value)

    def reject_duplicate_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate object key")
            result[key] = value
        return result

    try:
        value = json.loads(raw, parse_constant=reject_constant,
                           object_pairs_hook=reject_duplicate_pairs)
    except (TypeError, ValueError, json.JSONDecodeError, RecursionError) as exc:
        raise FormalMigrationError(
            "formal localStorage value is not valid JSON",
            reason_code="source_json_invalid") from exc
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > FORMAL_JSON_MAX_DEPTH:
            raise FormalMigrationError(
                "formal localStorage JSON is too deep",
                reason_code="source_json_depth_exceeded")
        if isinstance(current, dict):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
    return value


def _source_record_id(source_key: str, json_pointer: str,
                      raw_record_sha256: str) -> str:
    return _sha256_json({
        "source_key": source_key,
        "json_pointer": json_pointer,
        "raw_record_sha256": raw_record_sha256,
    })


def _target_hash(targets: list[dict]) -> str:
    ordered = sorted(targets, key=lambda item: (
        int(item["ordinal"]), item["kind"], item["id"], item["hash"]))
    return _sha256_json(ordered)


def _operation_target_hash(records: list[dict]) -> str:
    aggregate = [{
        "source_record_id": record["source_record_id"],
        "target_count": len(record["targets"]),
        "target_hash": _target_hash(record["targets"]),
        "archive_resolution": record["archive_resolution"],
    } for record in sorted(records, key=lambda item: item["source_record_id"])]
    return _sha256_json(aggregate)


def _m2_target_id(operation_id: str, kind: str, source_record_id: str) -> str:
    material = _sha256_json({
        "operation_id": operation_id,
        "kind": kind,
        "source_record_id": source_record_id,
    })
    prefix = {
        "plan": "plan_mig_",
        "plan_version": "ver_mig_",
        "recovered_draft": "draft_mig_",
        "legacy_checkin_evidence": "evidence_mig_",
    }[kind]
    return prefix + material


def _m2_error(source_pointer: str, error_code: str,
              raw_record_sha256: Optional[str] = None) -> dict:
    result = {"source_pointer": source_pointer, "error_code": error_code}
    if raw_record_sha256 is not None:
        result["raw_record_sha256"] = raw_record_sha256
    return result


def _m2_record(*, source_key: str, json_pointer: str,
               raw_record_sha256: str, source_kind: str,
               archive_resolution: str, data: Optional[dict] = None) -> dict:
    if source_kind not in M2_SOURCE_KINDS:
        raise FormalMigrationError("formal M2 source kind is invalid",
                                   reason_code="projection_source_kind_invalid")
    if archive_resolution not in M2_RESOLUTIONS:
        raise FormalMigrationError("formal M2 archive resolution is invalid",
                                   reason_code="projection_resolution_invalid")
    return {
        "source_record_id": _source_record_id(
            source_key, json_pointer, raw_record_sha256),
        "source_key": source_key,
        "json_pointer": json_pointer,
        "raw_record_sha256": raw_record_sha256,
        "source_kind": source_kind,
        "archive_resolution": archive_resolution,
        "data": data,
        "targets": [],
    }


def _formal_config(config: dict) -> tuple[dict, dict, list]:
    """Return full source config plus normalized config without CheckIns."""
    if not isinstance(config, dict):
        raise FormalMigrationError("formal config is not an object",
                                   reason_code="config_not_object")
    source = json.loads(json.dumps(config, ensure_ascii=False,
                                   allow_nan=False))
    checkins = source.get("checkins", [])
    if not isinstance(checkins, list):
        raise FormalMigrationError("formal checkins are not an array",
                                   reason_code="checkins_not_array")
    normalized_source = dict(source)
    normalized_source.pop("checkins", None)
    try:
        normalized = PERSISTENCE.normalize_config(
            normalized_source, ENGINE.default_config)
    except PERSISTENCE.PersistenceError as exc:
        raise FormalMigrationError(
            "formal config normalization failed",
            reason_code="config_normalization_failed") from exc
    normalized = json.loads(json.dumps(normalized, ensure_ascii=False,
                                       allow_nan=False))
    normalized.pop("checkins", None)
    return source, normalized, checkins


def _valid_legacy_checkin(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    age = value.get("age")
    actual = value.get("actual_total_nominal")
    if (isinstance(age, bool) or not isinstance(age, (int, float))
            or not math.isfinite(float(age))
            or float(age) < 0):
        return False
    if (isinstance(actual, bool) or not isinstance(actual, (int, float))
            or not math.isfinite(float(actual))
            or float(actual) < 0):
        return False
    return "date" not in value or isinstance(value["date"], str)


def _formal_projection(envelope_details: dict, operation_id: str) -> dict:
    """Build formal M2 candidates; no database or filesystem side effects."""
    envelope = envelope_details["envelope"]
    records: list[dict] = []
    errors: list[dict] = []
    draft_candidates: list[dict] = []
    evidence_candidates: list[dict] = []
    for entry in envelope["keys"]:
        if not entry["present"]:
            continue
        raw = entry["value"]
        key = entry["name"]
        raw_root_hash = _sha256(raw.encode("utf-8"))
        try:
            root = _parse_formal_json(raw)
        except FormalMigrationError as exc:
            records.append(_m2_record(
                source_key=key, json_pointer="/" + key,
                raw_record_sha256=raw_root_hash, source_kind="unknown",
                archive_resolution="quarantined"))
            errors.append(_m2_error("/" + key, exc.reason_code,
                                    raw_root_hash))
            continue
        if key == "fire_plans_v1":
            if not isinstance(root, list):
                records.append(_m2_record(
                    source_key=key, json_pointer="/fire_plans_v1",
                    raw_record_sha256=raw_root_hash, source_kind="unknown",
                    archive_resolution="quarantined"))
                errors.append(_m2_error("/fire_plans_v1",
                                        "plans_root_not_array", raw_root_hash))
                continue
            for index, item in enumerate(root):
                pointer = f"/fire_plans_v1/{index}"
                try:
                    item_hash = _sha256_json(item)
                except PERSISTENCE.PersistenceError:
                    item_hash = raw_root_hash
                if not isinstance(item, dict):
                    records.append(_m2_record(
                        source_key=key, json_pointer=pointer,
                        raw_record_sha256=item_hash, source_kind="unknown",
                        archive_resolution="quarantined"))
                    errors.append(_m2_error(pointer, "plan_record_not_object",
                                            item_hash))
                    continue
                config = item.get("config")
                if not isinstance(config, dict):
                    records.append(_m2_record(
                        source_key=key, json_pointer=pointer,
                        raw_record_sha256=item_hash, source_kind="unknown",
                        archive_resolution="quarantined"))
                    errors.append(_m2_error(pointer + "/config",
                                            "plan_config_not_object", item_hash))
                    continue
                try:
                    source_config, normalized_config, checkins = _formal_config(config)
                except FormalMigrationError as exc:
                    records.append(_m2_record(
                        source_key=key, json_pointer=pointer,
                        raw_record_sha256=item_hash, source_kind="unknown",
                        archive_resolution="quarantined"))
                    errors.append(_m2_error(pointer + "/config", exc.reason_code,
                                            item_hash))
                    continue
                plan_record = _m2_record(
                    source_key=key, json_pointer=pointer,
                    raw_record_sha256=item_hash, source_kind="plan",
                    archive_resolution="created",
                    data={"source_config": source_config,
                          "normalized_config": normalized_config,
                          "display_name": (item.get("name")
                                            if isinstance(item.get("name"), str)
                                            and item.get("name").strip()
                                            else f"Imported plan {index + 1}")})
                plan_record["targets"] = [
                    {"ordinal": 0, "kind": "plan",
                     "id": _m2_target_id(operation_id, "plan",
                                          plan_record["source_record_id"]),
                     "hash": None},
                    {"ordinal": 1, "kind": "plan_version",
                     "id": _m2_target_id(operation_id, "plan_version",
                                          plan_record["source_record_id"]),
                     "hash": None},
                ]
                records.append(plan_record)
                for checkin_index, checkin in enumerate(checkins):
                    checkin_pointer = f"{pointer}/config/checkins/{checkin_index}"
                    try:
                        checkin_hash = _sha256_json(checkin)
                    except PERSISTENCE.PersistenceError:
                        checkin_hash = item_hash
                    valid_checkin = _valid_legacy_checkin(checkin)
                    checkin_record = _m2_record(
                        source_key=key, json_pointer=checkin_pointer,
                        raw_record_sha256=checkin_hash,
                        source_kind="legacy_checkin",
                        archive_resolution=("evidence_only"
                                             if valid_checkin else "quarantined"),
                        data={"value": checkin,
                              "plan_source_record_id": plan_record["source_record_id"]}
                        if valid_checkin else None)
                    if checkin_record["data"] is None:
                        errors.append(_m2_error(
                            checkin_pointer, "checkin_record_invalid", checkin_hash))
                    else:
                        checkin_record["targets"] = [{
                            "ordinal": 0, "kind": "legacy_checkin_evidence",
                            "id": _m2_target_id(
                                operation_id, "legacy_checkin_evidence",
                                checkin_record["source_record_id"]),
                            "hash": None,
                        }]
                        evidence_candidates.append(checkin_record)
                    records.append(checkin_record)
        else:  # fire_draft
            config = root
            pointer = "/fire_draft"
            if isinstance(root, dict) and "v" in root:
                version = root.get("v")
                if (isinstance(version, bool) or not isinstance(version, int)
                        or version not in SUPPORTED_DRAFT_VERSIONS):
                    errors.append(_m2_error(
                        "/fire_draft/v", "draft_version_unsupported", raw_root_hash))
                    records.append(_m2_record(
                        source_key=key, json_pointer=pointer,
                        raw_record_sha256=raw_root_hash, source_kind="unknown",
                        archive_resolution="quarantined"))
                    continue
                config = root.get("config")
                pointer = "/fire_draft/config"
            try:
                source_config, normalized_config, checkins = _formal_config(config)
            except FormalMigrationError as exc:
                errors.append(_m2_error(pointer, exc.reason_code, raw_root_hash))
                records.append(_m2_record(
                    source_key=key, json_pointer=pointer,
                    raw_record_sha256=raw_root_hash, source_kind="unknown",
                    archive_resolution="quarantined"))
                continue
            draft_record_hash = _sha256_json(config)
            draft_record = _m2_record(
                source_key=key, json_pointer=pointer,
                raw_record_sha256=draft_record_hash, source_kind="draft",
                archive_resolution="evidence_only",
                data={"source_config": source_config,
                      "normalized_config": normalized_config,
                      "raw_json": _canonical(config).decode("utf-8")})
            draft_record["targets"] = [{
                "ordinal": 0, "kind": "recovered_draft",
                "id": _m2_target_id(operation_id, "recovered_draft",
                                     draft_record["source_record_id"]),
                "hash": None,
            }]
            records.append(draft_record)
            draft_candidates.append(draft_record)
            if checkins:
                errors.append(_m2_error(
                    pointer + "/checkins", "draft_checkins_need_plan_lineage",
                    raw_root_hash))
                for checkin_index, checkin in enumerate(checkins):
                    try:
                        checkin_hash = _sha256_json(checkin)
                    except PERSISTENCE.PersistenceError:
                        checkin_hash = raw_root_hash
                    checkin_record = _m2_record(
                        source_key=key,
                        json_pointer=f"{pointer}/checkins/{checkin_index}",
                        raw_record_sha256=checkin_hash,
                        source_kind="legacy_checkin",
                        archive_resolution="quarantined")
                    records.append(checkin_record)

    records.sort(key=lambda item: (
        item["source_key"], item["json_pointer"], item["raw_record_sha256"]))
    errors.sort(key=lambda item: (item["source_pointer"], item["error_code"]))
    # M2 is deliberately all-or-nothing at the business-row boundary.  Keep
    # every source pointer as quarantine evidence, but do not let a valid
    # sibling become a Plan, PlanVersion, evidence row, or recovered draft
    # when any source record is partial/blocked.
    if errors or any(record["archive_resolution"] == "quarantined"
                     for record in records):
        for record in records:
            record["archive_resolution"] = "quarantined"
            record["targets"] = []
        draft_candidates = []
        evidence_candidates = []
    return {
        "records": records,
        "draft_candidates": draft_candidates,
        "evidence_candidates": evidence_candidates,
        "errors": errors,
    }


def _public_formal_projection(details: dict, candidates: dict,
                              target_hashes: dict[str, str]) -> dict:
    records = []
    evidence = []
    drafts = []
    targets_by_source = {}
    for source in candidates["records"]:
        targets = []
        for target in source["targets"]:
            target = dict(target)
            target["hash"] = target_hashes.get(target["id"], target["hash"])
            if not _is_hex64(target["hash"]):
                raise FormalMigrationError(
                    "formal M2 target hash is missing",
                    reason_code="projection_target_hash_missing")
            targets.append(target)
        records.append({
            "source_record_id": source["source_record_id"],
            "source_key": source["source_key"],
            "json_pointer": source["json_pointer"],
            "raw_record_sha256": source["raw_record_sha256"],
            "source_kind": source["source_kind"],
            "archive_resolution": source["archive_resolution"],
            "targets": sorted(targets, key=lambda item: (
                item["ordinal"], item["kind"], item["id"], item["hash"])),
        })
        targets_by_source[source["source_record_id"]] = {
            target["kind"]: target["id"] for target in targets}
        if source["source_kind"] == "legacy_checkin" and targets:
            plan_targets = targets_by_source.get(
                source["data"]["plan_source_record_id"], {})
            plan_id = plan_targets.get("plan")
            version_id = plan_targets.get("plan_version")
            if plan_id is None or version_id is None:
                raise FormalMigrationError(
                    "formal checkin lineage target is missing",
                    reason_code="checkin_lineage_target_missing")
            lineage_target_hash = _sha256_json({
                "plan_row_hash": target_hashes[plan_id],
                "plan_version_row_hash": target_hashes[version_id],
            })
            evidence.append({
                "source_record_id": source["source_record_id"],
                "evidence_id": targets[0]["id"],
                "target_hash": lineage_target_hash,
                "target_plan_id": plan_id,
                "target_plan_version_id": version_id,
            })
        if source["source_kind"] == "draft" and targets:
            drafts.append({
                "source_record_id": source["source_record_id"],
                "draft_id": targets[0]["id"],
            })
    projection = {
        "format": FORMAL_PROJECTION_FORMAT,
        "projection_version": PROJECTION_VERSION,
        "normalizer_version": NORMALIZER_VERSION,
        "config_schema_version": CONFIG_SCHEMA_VERSION,
        "source_envelope_sha256": details["envelope_sha256"],
        "records": records,
        "legacy_checkin_evidence": sorted(
            evidence, key=lambda item: item["evidence_id"]),
        "recovered_drafts": sorted(
            drafts, key=lambda item: item["draft_id"]),
        "errors": candidates["errors"],
    }
    return projection


def _safe_regular_file(path: Path, *, limit: int) -> bool:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    return (stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid()
            and info.st_nlink == 1 and stat.S_IMODE(info.st_mode) == 0o600
            and info.st_size <= limit)


class FormalMigrationManager:
    """Own the disposable M1 intent and M2 import/verify workflow."""

    def __init__(self, recovery_manager: RECOVERY.BackupRestoreManager):
        self.recovery = recovery_manager
        self.journal = recovery_manager.journal
        # Reuse the recovery manager's process lock so a formal staging call
        # cannot race a recovery/restore call in the same server process.
        self._lock = recovery_manager._lock

    def _staging_paths(self, operation_id: str) -> tuple[Path, Path, Path]:
        if not _operation_id_valid(operation_id):
            raise FormalMigrationError("migration operation id is invalid",
                                       reason_code="operation_id_invalid")
        root = self.recovery.support_root / ".migration-staging"
        RECOVERY._secure_dir(root)
        operation_root = root / operation_id
        RECOVERY._secure_dir(operation_root)
        return (operation_root, operation_root / "staging.sqlite3",
                operation_root / "localstorage-envelope.json")

    def _projection_path(self, operation_id: str) -> Path:
        return self._staging_paths(operation_id)[0] / "projection.json"

    def _ensure_staging_archive(self, stage_path: Path) -> None:
        if not stage_path.exists():
            store = PERSISTENCE.PersistenceStore(
                str(stage_path), app_release_id=self.recovery.app_release_id)
            store.close()
        # Re-run the established descriptor/path guard before every write-side
        # staging migration, including after a restart.
        PERSISTENCE._preflight_sqlite_paths(
            str(stage_path), writable=True, create_database=False)
        conn = sqlite3.connect(str(stage_path))
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()
        if version in (6, 7):
            RECOVERY._migrate_stage_to_v7(stage_path)
        elif version != 8:
            raise FormalMigrationError(
                "staging archive schema is unsupported",
                reason_code="staging_schema_unsupported")

    def _ensure_staging_archive_v8(self, stage_path: Path) -> None:
        self._ensure_staging_archive(stage_path)
        conn = sqlite3.connect(str(stage_path))
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        finally:
            conn.close()
        if version == 7:
            RECOVERY._migrate_stage_to_v8(stage_path)
        elif version != 8:
            raise FormalMigrationError(
                "staging archive schema is unsupported for M2",
                reason_code="staging_schema_unsupported")

    @staticmethod
    def _metadata(op: dict) -> dict:
        try:
            metadata = json.loads(op["receipt_json"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FormalMigrationError(
                "migration operation metadata is invalid",
                reason_code="operation_metadata_invalid") from exc
        if (not isinstance(metadata, dict)
                or metadata.get("format") != "fire-migration-intent-v1"):
            raise FormalMigrationError(
                "migration operation metadata format is invalid",
                reason_code="operation_metadata_invalid")
        return metadata

    def _upsert_staging_root(self, *, stage_path: Path, operation: dict,
                             status: str) -> None:
        if status not in {"previewed", "raw_backed_up"}:
            raise FormalMigrationError("invalid M1 staging status",
                                       reason_code="staging_status_invalid")
        store = PERSISTENCE.PersistenceStore(
            str(stage_path), app_release_id=self.recovery.app_release_id)
        try:
            with store._transaction() as conn:
                row = conn.execute(
                    "SELECT * FROM migration_operations WHERE operation_id=?",
                    (operation["operation_id"],)).fetchone()
                values = {
                    "operation_id": operation["operation_id"],
                    "envelope_sha256": operation["envelope_sha256"],
                    "control_operation_id": operation["operation_id"],
                    "attempt_id": self._metadata(operation)["attempt_id"],
                    # Deliberately NULL here, and not a lost link.  This column
                    # carries a self-referential FK into `migration_operations`
                    # in *this* staging file, and a retry gets a fresh staging
                    # file whose only row is its own — so any non-NULL value is
                    # unsatisfiable by construction and the INSERT aborts.  That
                    # is the second half of why `retry_nonce` never worked.  The
                    # durable retry provenance is the control journal's, where
                    # it is immutable and trigger-checked: the operation's
                    # `parent_operation_id` names the attempt being retried, and
                    # its `receipt_json` metadata records the same id alongside
                    # the `attempt_id`.  Nothing is only knowable from here.
                    "retry_of_operation_id": None,
                    "projection_version": PROJECTION_VERSION,
                    "normalizer_version": NORMALIZER_VERSION,
                    "config_schema_version": str(CONFIG_SCHEMA_VERSION),
                    "target_count": 0,
                    "target_hash": RECOVERY.EMPTY_TARGET_HASH,
                }
                if row is None:
                    conn.execute(
                        "INSERT INTO migration_operations "
                        "(operation_id,envelope_sha256,control_operation_id,"
                        "attempt_id,retry_of_operation_id,projection_version,"
                        "normalizer_version,config_schema_version,status,target_count,"
                        "target_hash,error_code,created_at,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (values["operation_id"], values["envelope_sha256"],
                         values["control_operation_id"], values["attempt_id"],
                         values["retry_of_operation_id"],
                         values["projection_version"], values["normalizer_version"],
                         values["config_schema_version"], status,
                         values["target_count"], values["target_hash"], None,
                         RECOVERY._millis_now(), RECOVERY._millis_now()))
                else:
                    for field, expected in values.items():
                        if row[field] != expected:
                            raise FormalMigrationConflict(
                                "disposable migration root identity conflicts",
                                reason_code="staging_identity_conflict")
                    current = row["status"]
                    if current not in {"previewed", "raw_backed_up"}:
                        raise FormalMigrationConflict(
                            "migration root is not in the M1 state graph",
                            reason_code="staging_state_conflict")
                    if status == "raw_backed_up" and current == "previewed":
                        conn.execute(
                            "UPDATE migration_operations SET status=?,updated_at=? "
                            "WHERE operation_id=?",
                            (status, RECOVERY._millis_now(), operation["operation_id"]))
        finally:
            store.close()
        conn = PERSISTENCE._readonly_connect(str(stage_path))
        try:
            RECOVERY.validate_archive_connection(conn)
        finally:
            conn.close()

    @staticmethod
    def _persist_envelope(path: Path, canonical_bytes: bytes) -> str:
        if _safe_regular_file(path, limit=MAX_FORMAL_ENVELOPE_BYTES):
            existing = RECOVERY._read_regular(path, MAX_FORMAL_ENVELOPE_BYTES)
            if existing != canonical_bytes:
                raise FormalMigrationConflict(
                    "staged formal envelope bytes conflict",
                    reason_code="staged_envelope_conflict")
            return "already_present"
        if path.exists() or path.is_symlink():
            raise FormalMigrationError("staged envelope artifact is unsafe",
                                       reason_code="staged_artifact_unsafe")
        RECOVERY._write_new(path, canonical_bytes)
        RECOVERY._fsync_dir(path.parent)
        return "written"

    @staticmethod
    def _persist_projection(path: Path, projection: dict) -> str:
        canonical_bytes = _canonical(projection)
        limit = 2 * 1024 * 1024
        if len(canonical_bytes) > limit:
            raise FormalMigrationError(
                "formal M2 projection is too large",
                reason_code="projection_too_large")
        if _safe_regular_file(path, limit=limit):
            existing = RECOVERY._read_regular(path, limit)
            if existing != canonical_bytes:
                raise FormalMigrationConflict(
                    "staged formal projection bytes conflict",
                    reason_code="staged_projection_conflict")
            return "already_present"
        if path.exists() or path.is_symlink():
            raise FormalMigrationError("staged projection artifact is unsafe",
                                       reason_code="staged_artifact_unsafe")
        RECOVERY._write_new(path, canonical_bytes)
        RECOVERY._fsync_dir(path.parent)
        return "written"

    @staticmethod
    def _stage_operation(stage_path: Path, operation_id: str) -> tuple[int, dict]:
        conn = PERSISTENCE._readonly_connect(str(stage_path))
        try:
            version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            row = conn.execute(
                "SELECT * FROM migration_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            if row is None:
                raise FormalMigrationError(
                    "staged migration operation is missing",
                    reason_code="staging_operation_missing")
            return version, dict(row)
        finally:
            conn.close()

    @staticmethod
    def _update_stage_status(stage_path: Path, operation_id: str,
                             status: str) -> dict:
        conn = sqlite3.connect(str(stage_path), isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                "UPDATE migration_operations SET status=?,updated_at=? "
                "WHERE operation_id=?", (status, RECOVERY._millis_now(),
                                           operation_id))
            row = conn.execute(
                "SELECT * FROM migration_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            if row is None:
                raise FormalMigrationError(
                    "staged migration operation is missing",
                    reason_code="staging_operation_missing")
            conn.commit()
            return dict(row)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _stage_presence(stage_path: Path) -> str:
        """`absent` or `present` — and never a guess.

        `lexists()` would fold "the file is not there" together with "I am not
        allowed to look", and those two have opposite correct answers.
        """
        try:
            stage_path.lstat()
        except FileNotFoundError:
            return "absent"
        except OSError as exc:
            raise FormalMigrationError(
                "migration staging root is unreadable",
                reason_code="staging_unreadable") from exc
        return "present"

    def _unreadable_stage_metadata(self, operation: dict, stage_path: Path,
                                   cause: Exception) -> tuple:
        """Classify a stage we could not read, instead of zeroing it out.

        The previous version caught every `OSError` here and let the caller
        report `target_count=0`, an empty `target_hash`, and
        `schema_version=None`.  Only one of the things that lands in this handler
        is benign, and the degraded values were wrong for all three of the
        others:

        - A `previewed` operation has no disposable root yet.  Zeros are the
          honest answer, and hiding the external journal row instead would be
          worse.  This is the case the handler was written for.
        - A `cutover_marked` operation's stage is legitimately gone: finalize
          moved that exact file onto the live archive.  But its metadata is not
          gone with it — the durable authority intent committed to the count and
          hash before the move, and the live archive is that image, so its
          schema is readable directly.  Reporting zeros here told the seam the
          UI polls at startup that a completed migration had migrated nothing.
        - A stage that is *present* but unreadable is a `PermissionError`, a
          corrupt file, or a malformed root.  Degrading that to zeros reports a
          healthy-looking empty migration for a broken one, and it is
          indistinguishable from the case above.  It fails closed.
        - A stage absent in any other state is a missing disposable root the
          contract says should exist.  It fails closed too.
        """
        if self._stage_presence(stage_path) == "present":
            raise FormalMigrationError(
                "migration staging root is unreadable",
                reason_code="staging_unreadable") from cause

        state = operation["state"]
        if state == "cutover_marked":
            intent = self.journal.get_authority_intent_for_operation(
                operation["operation_id"])
            if intent is None:
                raise RECOVERY.ManualRecoveryRequired(
                    "migration cutover receipt is missing its authority intent")
            body = intent["body"]
            # The archive is the staged image, moved.  Read its schema from the
            # file that now holds those bytes rather than reporting None.
            schema_version = int(RECOVERY._archive_schema_version(
                self.recovery.archive_path))
            return (state, int(body["target_count"]), body["target_hash"],
                    schema_version)

        if state == "previewed":
            return (state, 0, RECOVERY.EMPTY_TARGET_HASH, None)

        raise FormalMigrationError(
            "migration staging root is missing",
            reason_code="staging_root_missing") from cause

    def _public_operation(self, operation: dict) -> dict:
        metadata = self._metadata(operation)
        operation_root, stage_path, envelope_path = self._staging_paths(
            operation["operation_id"])
        if (operation.get("staging_path") != str(stage_path)
                or operation.get("preimage_path") is not None
                or operation.get("package_id") is not None):
            raise FormalMigrationConflict(
                "migration operation staging binding is invalid",
                reason_code="operation_binding_conflict")
        stage_state = operation["state"]
        target_count = 0
        target_hash = RECOVERY.EMPTY_TARGET_HASH
        schema_version = None
        try:
            schema_version, staged = self._stage_operation(
                stage_path, operation["operation_id"])
            stage_state = staged["status"]
            target_count = int(staged["target_count"])
            target_hash = staged["target_hash"]
        except (FormalMigrationError, PERSISTENCE.PersistenceError,
                RECOVERY.RecoveryError, sqlite3.DatabaseError, OSError) as exc:
            # sqlite3.DatabaseError is here because a stage that is present but
            # is not a database at all used to escape this handler entirely and
            # surface raw to the caller.  It belongs in the same classification
            # as every other way of failing to read a stage.
            (stage_state, target_count, target_hash,
             schema_version) = self._unreadable_stage_metadata(
                 operation, stage_path, exc)
        projection_path = self._projection_path(operation["operation_id"])
        projection_staged = _safe_regular_file(
            projection_path, limit=2 * 1024 * 1024)
        projection_sha256 = None
        if projection_staged:
            projection_sha256 = _sha256(
                RECOVERY._read_regular(projection_path, 2 * 1024 * 1024))
        fence_state = "not_issued"
        fence_expires_at_ms = None
        fence_page_instance_id = None
        if operation.get("legacy_fence_id") is not None:
            try:
                fence_expires_at_ms = RECOVERY._fence_expiry_ms(
                    operation["legacy_fence_id"])
                fence_page_instance_id = RECOVERY._fence_page_instance_id(
                    operation["legacy_fence_id"])
                if operation["state"] == "cutover_marked":
                    fence_state = "consumed"
                elif RECOVERY._epoch_millis() > fence_expires_at_ms:
                    fence_state = "expired"
                else:
                    fence_state = "held"
            except RECOVERY.RecoveryError:
                fence_state = "invalid"
        return {
            "operation_id": operation["operation_id"],
            "kind": operation["kind"],
            "state": stage_state,
            "attempt_id": metadata["attempt_id"],
            "envelope_sha256": operation["envelope_sha256"],
            "source_identity": metadata["source_identity"],
            "expected_generation": operation["expected_generation"],
            "old_logical_sha256": operation["old_logical_sha256"],
            "target_count": target_count,
            "target_hash": target_hash,
            "schema_version": schema_version,
            "staging_ready": _safe_regular_file(
                stage_path, limit=RECOVERY.MAX_ARCHIVE_BYTES),
            "raw_envelope_staged": _safe_regular_file(
                envelope_path, limit=MAX_FORMAL_ENVELOPE_BYTES),
            "projection_staged": projection_staged,
            "projection_sha256": projection_sha256,
            "staging_root": str(operation_root.name),
            "legacy_fence_id": operation.get("legacy_fence_id"),
            "legacy_fence_digest": operation.get("legacy_fence_digest"),
            "fence_expires_at_ms": fence_expires_at_ms,
            "fence_state": fence_state,
            "page_instance_id": fence_page_instance_id,
        }

    def _require_legacy_authority(self, snapshot: dict) -> None:
        authority = snapshot.get("authority") or {}
        status = authority.get("status")
        if status == "manual_recovery_required":
            raise RECOVERY.ManualRecoveryRequired(
                "migration authority is latched for manual recovery")
        if status != "legacy_authoritative":
            raise FormalMigrationConflict(
                "formal M1 requires legacy authority",
                reason_code="authority_not_legacy")

    @staticmethod
    def _insert_plan_version(conn: sqlite3.Connection, *, plan_id: str,
                             version_id: str, source_config: dict,
                             normalized_config: dict, created_at: str) -> None:
        source_json = PERSISTENCE.canonical_json_text(source_config)
        normalized_json = PERSISTENCE.canonical_json_text(normalized_config)
        conn.execute(
            "INSERT INTO plan_versions "
            "(id,plan_id,parent_version_id,source_kind,source_config_json,"
            "source_config_sha256,normalized_config_json,normalized_config_sha256,"
            "config_schema_version,canonicalizer_version,created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (version_id, plan_id, None, "import", source_json,
             PERSISTENCE.sha256_bytes(source_json.encode("utf-8")),
             normalized_json,
             PERSISTENCE.sha256_bytes(normalized_json.encode("utf-8")),
             int(normalized_config.get("config_version",
                                      CONFIG_SCHEMA_VERSION)),
             PERSISTENCE.CANONICALIZER_VERSION, created_at))

    def import_operation(self, operation_id: Any, envelope: Any) -> dict:
        """Import formal candidates into a disposable v8 archive only."""
        if not _operation_id_valid(operation_id):
            raise FormalMigrationError("migration operation id is invalid",
                                       reason_code="operation_id_invalid")
        details = validate_envelope(envelope)
        with self._lock:
            operation = self.journal.get_operation(operation_id)
            if operation is None or operation["kind"] != "migration":
                raise FormalMigrationError("migration operation is unknown",
                                           reason_code="operation_missing")
            metadata = self._metadata(operation)
            if (operation["envelope_sha256"] != details["envelope_sha256"]
                    or metadata.get("source_identity") != details["source_identity"]):
                raise FormalMigrationConflict(
                    "import envelope does not match preview identity",
                    reason_code="import_envelope_conflict")
            snapshot = self.recovery._bootstrap()
            self._require_legacy_authority(snapshot)
            generation = snapshot["generation"]
            if (generation["generation_id"] != operation["expected_generation"]
                    or generation["logical_sha256"] != operation["old_logical_sha256"]):
                raise FormalMigrationConflict(
                    "live archive generation changed before M2 import",
                    reason_code="source_changed")
            _root, stage_path, envelope_path = self._staging_paths(operation_id)
            self._ensure_staging_archive_v8(stage_path)
            version, staged = self._stage_operation(stage_path, operation_id)
            if version != 8:
                raise FormalMigrationError(
                    "M2 staging archive is not v8",
                    reason_code="staging_schema_unsupported")
            if staged["status"] in {"imported", "verified"}:
                if not _safe_regular_file(
                        self._projection_path(operation_id),
                        limit=2 * 1024 * 1024):
                    raise FormalMigrationError(
                        "imported M2 staging projection is missing",
                        reason_code="projection_missing")
                if operation["state"] == "raw_backed_up":
                    operation = self.journal.update_operation(
                        operation_id, state="imported")
                return self._public_operation(operation)
            if staged["status"] != "raw_backed_up":
                raise FormalMigrationConflict(
                    "migration operation is not importable",
                    reason_code="operation_state_conflict")
            if RECOVERY._read_regular(envelope_path,
                                      MAX_FORMAL_ENVELOPE_BYTES) \
                    != details["canonical_bytes"]:
                raise FormalMigrationConflict(
                    "staged formal envelope bytes conflict",
                    reason_code="staged_envelope_conflict")

            candidates = _formal_projection(details, operation_id)
            conn = sqlite3.connect(str(stage_path), isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("PRAGMA foreign_keys=ON")
                conn.execute("BEGIN IMMEDIATE")
                root = conn.execute(
                    "SELECT * FROM migration_operations WHERE operation_id=?",
                    (operation_id,)).fetchone()
                if root is None or root["status"] != "raw_backed_up":
                    raise FormalMigrationConflict(
                        "migration operation changed during M2 import",
                        reason_code="operation_state_conflict")
                if conn.execute(
                        "SELECT 1 FROM migration_source_records "
                        "WHERE operation_id=? LIMIT 1", (operation_id,)
                ).fetchone() is not None:
                    raise FormalMigrationConflict(
                        "M2 staging source map is unexpectedly non-empty",
                        reason_code="staging_identity_conflict")
                created_at = PERSISTENCE.utc_now()
                target_hashes: dict[str, str] = {}
                plan_targets: dict[str, tuple[str, str]] = {}

                # Plans and PlanVersions are created first so CheckIn evidence
                # can carry a real, row-rooted lineage edge.
                blocked = bool(candidates["errors"]) or any(
                    source["archive_resolution"] == "quarantined"
                    for source in candidates["records"])
                for source in candidates["records"]:
                    if blocked or source["source_kind"] != "plan":
                        continue
                    data = source["data"]
                    plan_id = next(target["id"] for target in source["targets"]
                                   if target["kind"] == "plan")
                    version_id = next(
                        target["id"] for target in source["targets"]
                        if target["kind"] == "plan_version")
                    conn.execute(
                        "INSERT INTO plans(id,display_name,source_key,created_at) "
                        "VALUES (?,?,?,?)",
                        (plan_id, data["display_name"], source["source_key"],
                         created_at))
                    self._insert_plan_version(
                        conn, plan_id=plan_id, version_id=version_id,
                        source_config=data["source_config"],
                        normalized_config=data["normalized_config"],
                        created_at=created_at)
                    target_hashes[plan_id] = RECOVERY._logical_row_hash(
                        conn, "plans", plan_id)
                    target_hashes[version_id] = RECOVERY._logical_row_hash(
                        conn, "plan_versions", version_id)
                    plan_targets[source["source_record_id"]] = (
                        plan_id, version_id)

                for source in candidates["records"]:
                    if blocked or source["source_kind"] != "draft":
                        continue
                    data = source["data"]
                    draft_id = next(target["id"] for target in source["targets"]
                                    if target["kind"] == "recovered_draft")
                    conn.execute(
                        "INSERT INTO recovered_drafts "
                        "(draft_id,operation_id,source_key,json_pointer,"
                        "raw_record_sha256,raw_json,normalized_json,status,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?)",
                        (draft_id, operation_id, source["source_key"],
                         source["json_pointer"], source["raw_record_sha256"],
                         data["raw_json"],
                         PERSISTENCE.canonical_json_text(data["normalized_config"]),
                         "recovered", created_at))
                    target_hashes[draft_id] = RECOVERY._logical_row_hash(
                        conn, "recovered_drafts", draft_id)

                for source in candidates["evidence_candidates"]:
                    plan_id, version_id = plan_targets.get(
                        source["data"]["plan_source_record_id"], (None, None))
                    if plan_id is None or version_id is None:
                        raise FormalMigrationError(
                            "legacy CheckIn has no PlanVersion lineage",
                            reason_code="checkin_lineage_target_missing")
                    evidence_id = next(
                        target["id"] for target in source["targets"]
                        if target["kind"] == "legacy_checkin_evidence")
                    value = source["data"]["value"]
                    raw_json = _canonical(value).decode("utf-8")
                    conn.execute(
                        "INSERT INTO legacy_checkin_evidence "
                        "(evidence_id,operation_id,source_key,json_pointer,"
                        "raw_record_sha256,observed_date,observed_age,"
                        "actual_total_nominal,status,raw_json) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (evidence_id, operation_id, source["source_key"],
                         source["json_pointer"], source["raw_record_sha256"],
                         value.get("date"), value.get("age"),
                         value.get("actual_total_nominal"),
                         "incomplete_inputs", raw_json))
                    target_hashes[evidence_id] = RECOVERY._logical_row_hash(
                        conn, "legacy_checkin_evidence", evidence_id)

                # Install immutable source roots and their target maps only
                # after every target row has a stable logical hash.
                for source in candidates["records"]:
                    targets = []
                    for target in source["targets"]:
                        target = dict(target)
                        target["hash"] = target_hashes[target["id"]]
                        targets.append(target)
                    source_hash = _target_hash(targets)
                    conn.execute(
                        "INSERT INTO migration_source_records "
                        "(operation_id,source_record_id,source_key,json_pointer,"
                        "raw_record_sha256,source_kind,archive_resolution,"
                        "target_count,target_hash) VALUES (?,?,?,?,?,?,?,?,?)",
                        (operation_id, source["source_record_id"],
                         source["source_key"], source["json_pointer"],
                         source["raw_record_sha256"], source["source_kind"],
                         source["archive_resolution"], len(targets), source_hash))
                    for target in sorted(targets, key=lambda item: item["ordinal"]):
                        conn.execute(
                            "INSERT INTO migration_source_targets "
                            "(operation_id,source_record_id,target_ordinal,"
                            "target_kind,target_id,target_hash) VALUES (?,?,?,?,?,?)",
                            (operation_id, source["source_record_id"],
                             target["ordinal"], target["kind"], target["id"],
                             target["hash"]))

                for source in candidates["evidence_candidates"]:
                    plan_id, version_id = plan_targets[
                        source["data"]["plan_source_record_id"]]
                    lineage_hash = _sha256_json({
                        "plan_row_hash": target_hashes[plan_id],
                        "plan_version_row_hash": target_hashes[version_id],
                    })
                    evidence_id = next(
                        target["id"] for target in source["targets"]
                        if target["kind"] == "legacy_checkin_evidence")
                    conn.execute(
                        "INSERT INTO legacy_checkin_lineage "
                        "(evidence_id,operation_id,target_plan_id,"
                        "target_plan_version_id,target_hash,created_at) "
                        "VALUES (?,?,?,?,?,?)",
                        (evidence_id, operation_id, plan_id, version_id,
                         lineage_hash, created_at))

                projection = _public_formal_projection(
                    details, candidates, target_hashes)
                target_count = sum(len(record["targets"])
                                   for record in projection["records"])
                operation_target_hash = _operation_target_hash(
                    projection["records"])
                projection_path = self._projection_path(operation_id)
                self._persist_projection(projection_path, projection)
                conn.execute(
                    "UPDATE migration_operations SET status='imported',"
                    "target_count=?,target_hash=?,updated_at=? "
                    "WHERE operation_id=?",
                    (target_count, operation_target_hash,
                     RECOVERY._millis_now(), operation_id))
                RECOVERY.validate_archive_connection(conn)
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
            RECOVERY._checkpoint_delete_journal(stage_path)
            conn = PERSISTENCE._readonly_connect(str(stage_path))
            try:
                RECOVERY.validate_archive_connection(conn)
            finally:
                conn.close()
            if operation["state"] == "raw_backed_up":
                operation = self.journal.update_operation(
                    operation_id, state="imported")
            return self._public_operation(operation)

    def verify_operation(self, operation_id: Any, envelope: Any, *,
                         page_instance_id: str) -> dict:
        """Verify M2 and bind one short-lived browser-fence identity."""
        if not _operation_id_valid(operation_id):
            raise FormalMigrationError("migration operation id is invalid",
                                       reason_code="operation_id_invalid")
        try:
            RECOVERY._validate_page_instance_id(page_instance_id)
        except RECOVERY.RecoveryError as exc:
            raise FormalMigrationError(
                "browser page identity is required for formal verify",
                reason_code=("page_instance_required" if page_instance_id is None
                             else "page_instance_invalid")) from exc
        details = validate_envelope(envelope)
        with self._lock:
            operation = self.journal.get_operation(operation_id)
            if operation is None or operation["kind"] != "migration":
                raise FormalMigrationError("migration operation is unknown",
                                           reason_code="operation_missing")
            snapshot = self.recovery._bootstrap()
            self._require_legacy_authority(snapshot)
            generation = snapshot["generation"]
            if (generation["generation_id"] != operation["expected_generation"]
                    or generation["logical_sha256"] != operation["old_logical_sha256"]):
                raise FormalMigrationConflict(
                    "live archive generation changed before M2 verify",
                    reason_code="source_changed")
            _root, stage_path, envelope_path = self._staging_paths(operation_id)
            version, staged = self._stage_operation(stage_path, operation_id)
            if version != 8 or staged["status"] not in {"imported", "verified"}:
                raise FormalMigrationConflict(
                    "migration operation is not imported",
                    reason_code="operation_state_conflict")
            if (operation["state"] in {"verified", "cutover_marked"}
                    and (operation["legacy_fence_id"] is None
                         or operation["legacy_fence_digest"] is None)):
                raise FormalMigrationConflict(
                    "verified migration has no bound browser fence",
                    reason_code="verified_fence_missing")
            envelope_bytes = RECOVERY._read_regular(
                envelope_path, MAX_FORMAL_ENVELOPE_BYTES)
            if envelope_bytes != details["canonical_bytes"]:
                raise FormalMigrationConflict(
                    "fresh formal envelope does not match staged bytes",
                    reason_code="source_changed")
            if details["envelope_sha256"] != operation["envelope_sha256"]:
                raise FormalMigrationConflict(
                    "staged formal envelope identity changed",
                    reason_code="staged_envelope_conflict")
            projection_path = self._projection_path(operation_id)
            projection_bytes = RECOVERY._read_regular(
                projection_path, 2 * 1024 * 1024)
            try:
                projection = json.loads(
                    projection_bytes.decode("utf-8"),
                    object_pairs_hook=lambda pairs: dict(pairs))
            except (UnicodeDecodeError, ValueError) as exc:
                raise FormalMigrationError(
                    "staged formal projection is unreadable",
                    reason_code="projection_invalid") from exc
            if _canonical(projection) != projection_bytes:
                raise FormalMigrationError(
                    "staged formal projection is not canonical",
                    reason_code="projection_not_canonical")
            conn = PERSISTENCE._readonly_connect(str(stage_path))
            try:
                RECOVERY.validate_archive_connection(conn)
                target_hashes = {
                    row["target_id"]: row["target_hash"]
                    for row in conn.execute(
                        "SELECT target_id,target_hash FROM migration_source_targets "
                        "WHERE operation_id=?", (operation_id,))
                }
            finally:
                conn.close()
            candidates = _formal_projection(details, operation_id)
            expected = _public_formal_projection(
                details, candidates, target_hashes)
            if projection != expected:
                raise FormalMigrationConflict(
                    "staged formal projection does not match readback",
                    reason_code="projection_readback_mismatch")
            if staged["target_hash"] != _operation_target_hash(
                    projection["records"]):
                raise FormalMigrationError(
                    "staged migration operation target hash is invalid",
                    reason_code="operation_target_hash_invalid")
            projection_sha256 = _sha256(projection_bytes)
            cutover_eligible = not projection["errors"] and not any(
                record["archive_resolution"] == "quarantined"
                for record in projection["records"])
            if not cutover_eligible:
                if operation["state"] != "imported":
                    raise FormalMigrationConflict(
                        "verified migration projection is no longer eligible",
                        reason_code="cutover_projection_blocked")
                # Close the external intent first.  If the disposable stage
                # status write is interrupted, a retry can still finish the
                # terminal stage transition instead of leaving imported +
                # failed split-brain state.
                operation = self.journal.update_operation(
                    operation_id, state="failed")
                if staged["status"] != "failed":
                    self._update_stage_status(stage_path, operation_id, "failed")
                RECOVERY._checkpoint_delete_journal(stage_path)
                result = self._public_operation(operation)
                result.update({
                    "projection_sha256": projection_sha256,
                    "cutover_eligible": False,
                    "zero_business_rows": not any(
                        record["targets"] for record in projection["records"]),
                })
                return result
            fence_context = {
                "target_count": int(staged["target_count"]),
                "target_hash": staged["target_hash"],
                "projection_sha256": projection_sha256,
                "raw_key_sha256": details["raw_hashes"],
                "old_logical_sha256": operation["old_logical_sha256"],
            }
            if staged["status"] == "imported":
                self._update_stage_status(stage_path, operation_id, "verified")
            RECOVERY._checkpoint_delete_journal(stage_path)
            if operation["state"] == "imported":
                operation = self.journal.bind_migration_fence(
                    operation_id, page_instance_id=page_instance_id,
                    fence_context=fence_context)
            else:
                try:
                    self.journal.validate_migration_fence(
                        operation_id,
                        fence_id=operation["legacy_fence_id"],
                        fence_digest=operation["legacy_fence_digest"],
                        page_instance_id=page_instance_id,
                        fence_context=fence_context)
                except RECOVERY.RecoveryConflict as exc:
                    message = str(exc)
                    if "expired" in message:
                        reason_code = "fence_expired"
                    elif "page" in message:
                        reason_code = "fence_page_conflict"
                    else:
                        reason_code = "fence_invalid"
                    raise FormalMigrationConflict(
                        "formal migration fence verification failed",
                        reason_code=reason_code) from exc
                except RECOVERY.RecoveryError as exc:
                    raise FormalMigrationConflict(
                        "formal migration fence verification failed",
                        reason_code="fence_invalid") from exc
            result = self._public_operation(operation)
            result.update({
                "projection_sha256": projection_sha256,
                "cutover_eligible": cutover_eligible,
                "zero_business_rows": not any(
                    record["targets"] for record in projection["records"]),
            })
            return result

    def preflight_finalize(self, operation_id: Any, envelope: Any, *,
                           legacy_fence_id: Any,
                           legacy_fence_digest: Any,
                           page_instance_id: str) -> dict:
        """Return a non-executable M4 preflight evidence object.

        M3 owns the archive-write machinery, but M4 still owns the browser
        writer fence and two-key readback adapter.  This method deliberately
        stops before `prepare_archive_write`: a passed server preflight is not
        a cutover capability and cannot support a zero-loss claim.
        """
        if not _operation_id_valid(operation_id):
            raise FormalMigrationError("migration operation id is invalid",
                                       reason_code="operation_id_invalid")
        details = validate_envelope(envelope)
        with self._lock:
            operation = self.journal.get_operation(operation_id)
            if operation is None or operation["kind"] != "migration":
                raise FormalMigrationError("migration operation is unknown",
                                           reason_code="operation_missing")
            if operation["state"] != "verified":
                raise FormalMigrationConflict(
                    "migration operation is not verified",
                    reason_code="operation_state_conflict")

            # Re-run the complete M2 verification against the second exact
            # envelope before looking at the fence.  This keeps the final
            # request from becoming a digest-only replay of /verify.
            verified = self.verify_operation(
                operation_id, envelope, page_instance_id=page_instance_id)
            if not verified["cutover_eligible"]:
                raise FormalMigrationConflict(
                    "migration projection is not eligible for cutover",
                    reason_code="cutover_projection_blocked")
            fence_context = {
                "target_count": int(verified["target_count"]),
                "target_hash": verified["target_hash"],
                "projection_sha256": verified["projection_sha256"],
                "raw_key_sha256": details["raw_hashes"],
                "old_logical_sha256": operation["old_logical_sha256"],
            }
            try:
                self.journal.validate_migration_fence(
                    operation_id,
                    fence_id=legacy_fence_id,
                    fence_digest=legacy_fence_digest,
                    page_instance_id=page_instance_id,
                    fence_context=fence_context)
            except RECOVERY.RecoveryConflict as exc:
                message = str(exc)
                if "expired" in message:
                    reason_code = "fence_expired"
                elif "page" in message:
                    reason_code = "fence_page_conflict"
                else:
                    reason_code = "fence_invalid"
                raise FormalMigrationConflict(
                    "formal migration fence precondition failed",
                    reason_code=reason_code) from exc
            except RECOVERY.RecoveryError as exc:
                raise FormalMigrationConflict(
                    "formal migration fence precondition failed",
                    reason_code="fence_invalid") from exc

            snapshot = self.recovery._bootstrap()
            self._require_legacy_authority(snapshot)
            generation = snapshot["generation"]
            if (generation["generation_id"] != operation["expected_generation"]
                    or generation["logical_sha256"] != operation["old_logical_sha256"]):
                raise FormalMigrationConflict(
                    "live archive generation changed before formal preflight",
                    reason_code="source_changed")
            _root, stage_path, _envelope_path = self._staging_paths(operation_id)
            staged_db_bytes = RECOVERY._read_regular(
                stage_path, RECOVERY.MAX_ARCHIVE_BYTES)
            staged_logical = RECOVERY.logical_identity(str(stage_path))
            operation = self.journal.get_operation(operation_id)
            return {
                "format": "fire-migration-finalize-preflight-v1",
                "operation_id": operation_id,
                "server_preflight": "passed",
                "live_apply_allowed": False,
                "blocked_on": "m4_browser_fence_readback",
                "authority_status": snapshot["authority"]["status"],
                "expected_generation": operation["expected_generation"],
                "current_generation": generation["generation_id"],
                "old_logical_sha256": operation["old_logical_sha256"],
                "envelope_sha256": details["envelope_sha256"],
                "raw_key_sha256": details["raw_hashes"],
                "target_count": verified["target_count"],
                "target_hash": verified["target_hash"],
                "projection_sha256": verified["projection_sha256"],
                "staged_db_sha256": _sha256(staged_db_bytes),
                "staged_logical_sha256": staged_logical,
                "legacy_fence_id": operation["legacy_fence_id"],
                "legacy_fence_digest": operation["legacy_fence_digest"],
                "fence_expires_at_ms": verified["fence_expires_at_ms"],
            }

    def finalize(self, operation_id: Any, envelope: Any, *,
                 legacy_fence_id: Any, legacy_fence_digest: Any,
                 page_instance_id: str,
                 close_store: Optional[Callable[[], None]] = None,
                 reopen_store: Optional[Callable[[], Any]] = None) -> dict:
        """Perform the one live cutover: the staged image becomes the archive.

        This is the only call in the formal migration surface that touches the
        live archive; everything before it is disposable.  The linearization
        point is the external authority CAS inside
        `complete_archive_write_operation`, not the byte swap.  A crash before
        it leaves localStorage authoritative and the staged image untouched; a
        crash after the bytes land but before the receipts is completed by
        startup reconciliation.  Both directions are covered by the cutover
        crash matrix in tests/test_authority_intent_binding.py.

        The whole sequence runs under the recovery manager's reentrant process
        lock, so a concurrent restore or archive write cannot interleave with
        the preflight it was validated against.
        """
        if not _operation_id_valid(operation_id):
            raise FormalMigrationError("migration operation id is invalid",
                                       reason_code="operation_id_invalid")
        with self._lock:
            operation = self.journal.get_operation(operation_id)
            if operation is None or operation["kind"] != "migration":
                raise FormalMigrationError("migration operation is unknown",
                                           reason_code="operation_missing")
            # A lost response replays to the original cutover rather than
            # attempting a second one; the fence is already spent by then, so
            # re-running the preflight would fail for the wrong reason.  But
            # "do not re-run the preflight" is not "do not authenticate the
            # inputs": the arguments still have to be the ones this cutover was
            # actually performed under.
            if operation["state"] == "cutover_marked":
                self._assert_cutover_replay_inputs(
                    operation, envelope,
                    legacy_fence_id=legacy_fence_id,
                    legacy_fence_digest=legacy_fence_digest,
                    page_instance_id=page_instance_id)
                return self._cutover_receipt(operation_id)

            _root, stage_path, _envelope_path = self._staging_paths(operation_id)
            # Fold any staging WAL back into the file before it is measured and
            # moved: the swap relocates one file, so a sidecar left behind
            # would silently drop the tail of the import.
            RECOVERY._checkpoint_delete_journal(stage_path)

            preflight = self.preflight_finalize(
                operation_id, envelope,
                legacy_fence_id=legacy_fence_id,
                legacy_fence_digest=legacy_fence_digest,
                page_instance_id=page_instance_id)

            new_generation_id = "gen-migration-" + operation_id
            intent = self.journal.create_authority_intent(
                operation_id,
                fresh_envelope_sha256=preflight["envelope_sha256"],
                legacy_fence_id=preflight["legacy_fence_id"],
                legacy_fence_digest=preflight["legacy_fence_digest"],
                page_instance_id=page_instance_id,
                fence_context={
                    "target_count": int(preflight["target_count"]),
                    "target_hash": preflight["target_hash"],
                    "projection_sha256": preflight["projection_sha256"],
                    "raw_key_sha256": preflight["raw_key_sha256"],
                    "old_logical_sha256": preflight["old_logical_sha256"],
                },
                expected_authority_status=preflight["authority_status"],
                new_generation_id=new_generation_id)

            prepared = self.recovery.prepare_archive_write(
                idempotency_key="migration-cutover:" + operation_id,
                request_fingerprint=intent["intent_receipt_sha256"],
                new_logical_sha256=preflight["staged_logical_sha256"],
                new_generation_id=new_generation_id,
                parent_operation_id=operation_id,
                envelope_sha256=preflight["envelope_sha256"],
                staged_db_sha256=preflight["staged_db_sha256"],
                staging_path=str(stage_path),
                authority_status="sqlite_preferred",
                authority_snapshot={
                    "envelope_sha256": preflight["envelope_sha256"],
                    "target_count": int(preflight["target_count"]),
                    "target_hash": preflight["target_hash"],
                    "legacy_digest_last_seen": preflight["envelope_sha256"],
                },
                authority_intent=intent)

            self.recovery.apply_archive_write(
                prepared["operation_id"],
                lambda path: os.replace(str(stage_path), str(path)),
                close_store=close_store, reopen_store=reopen_store)
            return self._cutover_receipt(operation_id)

    def _assert_cutover_replay_inputs(
            self, operation: dict, envelope: Any, *, legacy_fence_id: Any,
            legacy_fence_digest: Any, page_instance_id: Any) -> None:
        """Authenticate a replay against what the first cutover committed to.

        The `cutover_marked` branch returned the stored receipt before looking at
        anything the caller passed, so `finalize(op, None, legacy_fence_id=None,
        legacy_fence_digest=None, page_instance_id=None)` was answered with a
        signed cutover receipt — as was a call carrying a different envelope, a
        different fence, or a different page.  That turns the replay path into a
        way to obtain an authenticated statement about a migration you cannot
        describe, and to be told that an envelope you never staged is now live.

        A replay legitimately must *not* re-require an unexpired fence: the
        fence is spent by the time the first call returns, so demanding a live
        one would refuse the honest retry.  But every value the caller supplies
        is one the durable record already fixed, so each is compared item by
        item:

        - the canonical envelope hash, against the operation's immutable
          `envelope_sha256` (and the intent's `fresh_envelope_sha256`);
        - `legacy_fence_id` and the fence digest, against the operation's
          immutable fence columns;
        - `page_instance_id`, against the intent's committed page and against
          the page encoded in the fence id itself.

        A mismatched replay is refused with the same reason code the first call
        would have used; an identical replay returns the original receipt.
        """
        operation_id = operation["operation_id"]
        intent = self.journal.get_authority_intent_for_operation(operation_id)
        if intent is None:
            # A cutover_marked operation without its intent is exactly what the
            # completeness sweep exists to catch; refuse rather than describe it.
            raise RECOVERY.ManualRecoveryRequired(
                "migration cutover receipt is missing its authority intent")
        body = intent["body"]

        try:
            details = validate_envelope(envelope)
        except FormalMigrationError as exc:
            raise FormalMigrationConflict(
                "cutover replay envelope does not match the completed cutover",
                reason_code="replay_input_mismatch") from exc
        if (details["envelope_sha256"] != operation["envelope_sha256"]
                or details["envelope_sha256"] != body["fresh_envelope_sha256"]):
            raise FormalMigrationConflict(
                "cutover replay envelope does not match the completed cutover",
                reason_code="replay_input_mismatch")

        if (not isinstance(legacy_fence_id, str)
                or legacy_fence_id != operation["legacy_fence_id"]
                or legacy_fence_id != body["legacy_fence_id"]
                or not isinstance(legacy_fence_digest, str)
                or legacy_fence_digest != operation["legacy_fence_digest"]
                or legacy_fence_digest != body["legacy_fence_digest"]):
            raise FormalMigrationConflict(
                "cutover replay fence does not match the completed cutover",
                reason_code="replay_input_mismatch")

        if (not isinstance(page_instance_id, str)
                or page_instance_id != body["page_instance_id"]
                or page_instance_id
                != RECOVERY._fence_page_instance_id(legacy_fence_id)):
            raise FormalMigrationConflict(
                "cutover replay page does not match the completed cutover",
                reason_code="replay_input_mismatch")

    def _cutover_receipt(self, operation_id: str) -> dict:
        """Describe a completed cutover from durable journal state only."""
        child_id = RECOVERY.derive_archive_child_id(operation_id)
        operation = self.journal.get_operation(operation_id)
        child = self.journal.get_operation(child_id)
        if operation is None or child is None:
            raise RECOVERY.ManualRecoveryRequired(
                "migration cutover receipt is missing its child")
        snapshot = self.journal.snapshot()
        events = self.journal.authority_events_for_operation(child_id)
        return {
            "format": "fire-migration-cutover-v1",
            "operation_id": operation_id,
            "state": operation["state"],
            "archive_child_id": child_id,
            "live_apply_allowed": True,
            "authority_status": snapshot["authority"]["status"],
            "generation_id": snapshot["generation"]["generation_id"],
            "old_logical_sha256": child["old_logical_sha256"],
            "new_logical_sha256": child["new_logical_sha256"],
            "envelope_sha256": child["envelope_sha256"],
            "archive_commit_receipt": child["archive_commit_receipt"],
            "control_ack_receipt": child["control_ack_receipt"],
            "authority_event_id": events[0]["event_id"] if events else None,
        }

    def preview(self, envelope: Any, *, retry_nonce: Optional[str] = None) -> dict:
        details = validate_envelope(envelope)
        if retry_nonce is not None:
            if (not isinstance(retry_nonce, str)
                    or RETRY_NONCE_RE.fullmatch(retry_nonce) is None):
                raise FormalMigrationError("retry nonce is invalid",
                                           reason_code="retry_nonce_invalid")
        with self._lock:
            snapshot = self.recovery._bootstrap()
            self._require_legacy_authority(snapshot)
            generation = snapshot["generation"]
            expected_generation = generation["generation_id"]
            attempt_id = "attempt-0"
            parent = None
            base_key = _idempotency_key(details["source_identity"],
                                       expected_generation, attempt_id)
            if retry_nonce is not None:
                parent = self.journal.find_operation(base_key)
                if parent is None or parent["state"] not in {
                        "failed", "source_changed"}:
                    raise FormalMigrationConflict(
                        "retry nonce requires a terminal migration attempt",
                        reason_code="retry_parent_invalid")
                attempt_id = retry_nonce
            idempotency_key = _idempotency_key(
                details["source_identity"], expected_generation, attempt_id)
            fingerprint = _request_fingerprint(
                details, expected_generation, attempt_id)
            existing = self.journal.find_operation(idempotency_key)
            if existing is not None:
                if (existing["kind"] != "migration"
                        or existing["request_fingerprint"] != fingerprint
                        or existing["envelope_sha256"] != details["envelope_sha256"]):
                    raise FormalMigrationConflict(
                        "migration idempotency key conflicts with a different request",
                        reason_code="idempotency_conflict")
                if existing["state"] not in {"previewed", "raw_backed_up"}:
                    raise FormalMigrationConflict(
                        "migration operation is not replayable in M1",
                        reason_code="operation_not_replayable")
                operation = existing
            else:
                operation_id = "mig_" + uuid.uuid4().hex
                _operation_root, stage_path, _envelope_path = self._staging_paths(
                    operation_id)
                metadata = {
                    "format": "fire-migration-intent-v1",
                    "operation_id": operation_id,
                    "attempt_id": attempt_id,
                    "retry_of_operation_id": (parent["operation_id"]
                                               if parent is not None else None),
                    "source_identity": details["source_identity"],
                    "expected_generation": expected_generation,
                    "old_logical_sha256": generation["logical_sha256"],
                }
                operation = self.journal.create_operation(
                    operation_id=operation_id, kind="migration", state="previewed",
                    idempotency_key=idempotency_key,
                    request_fingerprint=fingerprint,
                    expected_generation=expected_generation,
                    old_logical_sha256=generation["logical_sha256"],
                    envelope_sha256=details["envelope_sha256"],
                    staging_path=str(stage_path),
                    parent_operation_id=(parent["operation_id"]
                                         if parent is not None else None),
                    receipt_json=_canonical(metadata).decode("utf-8"))
            _operation_root, stage_path, _envelope_path = self._staging_paths(
                operation["operation_id"])
            self._ensure_staging_archive(stage_path)
            self._upsert_staging_root(
                stage_path=stage_path, operation=operation, status="previewed")
            operation = self.journal.get_operation(operation["operation_id"])
            if operation is None:
                raise FormalMigrationError("migration operation disappeared",
                                           reason_code="operation_missing")
            return self._public_operation(operation)

    def stage(self, operation_id: Any, envelope: Any) -> dict:
        if not _operation_id_valid(operation_id):
            raise FormalMigrationError("migration operation id is invalid",
                                       reason_code="operation_id_invalid")
        details = validate_envelope(envelope)
        with self._lock:
            snapshot = self.recovery._bootstrap()
            self._require_legacy_authority(snapshot)
            operation = self.journal.get_operation(operation_id)
            if operation is None or operation["kind"] != "migration":
                raise FormalMigrationError("migration operation is unknown",
                                           reason_code="operation_missing")
            metadata = self._metadata(operation)
            if (operation["envelope_sha256"] != details["envelope_sha256"]
                    or metadata.get("source_identity") != details["source_identity"]):
                raise FormalMigrationConflict(
                    "stage envelope does not match preview identity",
                    reason_code="staged_envelope_conflict")
            generation = snapshot["generation"]
            if (generation["generation_id"] != operation["expected_generation"]
                    or generation["logical_sha256"] != operation["old_logical_sha256"]):
                raise FormalMigrationConflict(
                    "live archive generation changed since preview",
                    reason_code="source_changed")
            _operation_root, stage_path, envelope_path = self._staging_paths(
                operation_id)
            self._ensure_staging_archive(stage_path)
            current = operation["state"]
            if current not in {"previewed", "raw_backed_up"}:
                raise FormalMigrationConflict(
                    "migration operation is not stageable in M1",
                    reason_code="operation_state_conflict")
            self._persist_envelope(envelope_path, details["canonical_bytes"])
            self._upsert_staging_root(
                stage_path=stage_path, operation=operation,
                status="raw_backed_up")
            if current == "previewed":
                operation = self.journal.update_operation(
                    operation_id, state="raw_backed_up")
            else:
                operation = self.journal.get_operation(operation_id)
            if operation is None:
                raise FormalMigrationError("migration operation disappeared",
                                           reason_code="operation_missing")
            return self._public_operation(operation)

    def authority(self) -> dict:
        with self._lock:
            snapshot = self.recovery._bootstrap()
            operations = [self._public_operation(operation)
                          for operation in self.journal.list_operations(
                              kind="migration")]
            return {
                "format": "fire-migration-authority-v1",
                "authority": snapshot["authority"],
                "generation": snapshot["generation"],
                "operations": operations,
                "localstorage_authority": "legacy",
            }
