"""Shadow-only bridge from the 2.0 WebView localStorage contract.

This module deliberately stops before the 3.0 persistence cutover.  The
browser supplies the raw values for a small, explicit allowlist; this module
validates the evidence, projects it into deterministic shadow candidates, and
optionally stages an immutable raw backup.  It never reads WebKit storage and
never writes the formal SQLite store.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import secrets
import stat
from pathlib import Path
from typing import Any, Callable, Optional


ENVELOPE_VERSION = 1
PROJECTION_VERSION = "shadow-projection-v1"
NORMALIZER_VERSION = "persistence.normalize_config-v1"
BACKUP_FORMAT_VERSION = "raw-localstorage-backup-v1"
MAX_ENVELOPE_BYTES = 6_000_000
MAX_JSON_DEPTH = 128
ALLOWED_KEYS = ("fire_draft", "fire_plans_v1")
SUPPORTED_DRAFT_VERSIONS = frozenset((1, 2))
_HEX64 = set("0123456789abcdef")


class MigrationEnvelopeError(ValueError):
    """A malformed envelope that must not be staged or projected."""

    def __init__(self, reason_code: str, pointer: str = ""):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.pointer = pointer


class ShadowBackupError(RuntimeError):
    """A raw backup could not be published without replacing evidence."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


class RawJSONError(ValueError):
    """A source localStorage value that cannot be safely projected."""

    def __init__(self, reason_code: str):
        super().__init__(reason_code)
        self.reason_code = reason_code


def _canonical_bytes(value: Any) -> bytes:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeEncodeError, RecursionError) as exc:
        raise MigrationEnvelopeError("non_canonical_json") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256_bytes(_canonical_bytes(value))


def _parse_constant(value: str):
    raise ValueError(value)


def _reject_duplicate_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate object key")
        result[key] = value
    return result


def _json_depth_exceeded(value: Any) -> bool:
    stack = [(value, 1)]
    while stack:
        current, depth = stack.pop()
        if depth > MAX_JSON_DEPTH:
            return True
        if isinstance(current, dict):
            stack.extend((child, depth + 1) for child in current.values())
        elif isinstance(current, list):
            stack.extend((child, depth + 1) for child in current)
    return False


def _parse_raw_json(raw: str):
    try:
        value = json.loads(raw, parse_constant=_parse_constant,
                           object_pairs_hook=_reject_duplicate_pairs)
    except RecursionError:
        raise RawJSONError("json_depth_exceeded") from None
    except (TypeError, ValueError, json.JSONDecodeError):
        raise RawJSONError("invalid_json") from None
    if _json_depth_exceeded(value):
        raise RawJSONError("json_depth_exceeded")
    return value


def _is_sha256(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and set(value) <= _HEX64)


def _validate_entry(entry: Any, index: int) -> dict:
    pointer = f"/entries/{index}"
    if not isinstance(entry, dict):
        raise MigrationEnvelopeError("entry_not_object", pointer)
    if set(entry) != {"key", "present", "raw", "raw_sha256"}:
        raise MigrationEnvelopeError("entry_fields_invalid", pointer)
    key = entry.get("key")
    if not isinstance(key, str):
        raise MigrationEnvelopeError("entry_key_invalid", pointer + "/key")
    if key not in ALLOWED_KEYS:
        raise MigrationEnvelopeError("unknown_entry_key", pointer + "/key")
    present = entry.get("present")
    if not isinstance(present, bool):
        raise MigrationEnvelopeError("entry_presence_invalid", pointer + "/present")
    raw = entry.get("raw")
    raw_sha256 = entry.get("raw_sha256")
    if not present:
        if raw is not None or raw_sha256 is not None:
            raise MigrationEnvelopeError("absent_entry_payload", pointer)
        return {"key": key, "present": False, "raw": None,
                "raw_sha256": None}
    if not isinstance(raw, str):
        raise MigrationEnvelopeError("present_raw_invalid", pointer + "/raw")
    try:
        raw_bytes = raw.encode("utf-8")
    except UnicodeEncodeError:
        raise MigrationEnvelopeError("raw_not_utf8", pointer + "/raw") from None
    if not _is_sha256(raw_sha256):
        raise MigrationEnvelopeError("raw_hash_invalid", pointer + "/raw_sha256")
    if _sha256_bytes(raw_bytes) != raw_sha256:
        raise MigrationEnvelopeError("raw_hash_mismatch", pointer + "/raw_sha256")
    return {"key": key, "present": True, "raw": raw,
            "raw_sha256": raw_sha256}


def validate_envelope(payload: Any) -> dict:
    """Validate and canonicalize one raw localStorage envelope.

    The returned entries are always in ALLOWED_KEYS order.  Consequently the
    envelope digest is independent of the order in which the browser sent
    the two entries, while each raw string and its byte hash remain exact.
    """
    if not isinstance(payload, dict):
        raise MigrationEnvelopeError("envelope_not_object")
    if set(payload) != {"envelope_version", "entries"}:
        raise MigrationEnvelopeError("envelope_fields_invalid")
    if payload.get("envelope_version") != ENVELOPE_VERSION:
        raise MigrationEnvelopeError("envelope_version_unsupported",
                                     "/envelope_version")
    entries = payload.get("entries")
    if not isinstance(entries, list) or len(entries) != len(ALLOWED_KEYS):
        raise MigrationEnvelopeError("entry_set_incomplete", "/entries")
    checked = [_validate_entry(entry, index)
               for index, entry in enumerate(entries)]
    seen = set()
    for index, entry in enumerate(checked):
        if entry["key"] in seen:
            raise MigrationEnvelopeError("duplicate_entry_key",
                                         f"/entries/{index}/key")
        seen.add(entry["key"])
    if seen != set(ALLOWED_KEYS):
        raise MigrationEnvelopeError("entry_set_incomplete", "/entries")
    by_key = {entry["key"]: entry for entry in checked}
    canonical = {"envelope_version": ENVELOPE_VERSION,
                 "entries": [copy.deepcopy(by_key[key]) for key in ALLOWED_KEYS]}
    if len(_canonical_bytes(canonical)) > MAX_ENVELOPE_BYTES:
        raise MigrationEnvelopeError("envelope_too_large")
    return canonical


def envelope_sha256(envelope: dict) -> str:
    return _sha256_bytes(_canonical_bytes(validate_envelope(envelope)))


def _candidate_id(kind: str, envelope_id: str, source_pointer: str,
                  normalizer_version: str, default_hash: Optional[str]) -> str:
    material = {
        "projection_version": PROJECTION_VERSION,
        "kind": kind,
        "envelope_sha256": envelope_id,
        "source_pointer": source_pointer,
        "normalizer_version": normalizer_version,
        "default_config_sha256": default_hash,
    }
    return f"{PROJECTION_VERSION}:{kind}:{_sha256_json(material)}"


def _quarantine(key: str, pointer: str, reason_code: str,
                raw_sha256: Optional[str], record_sha256: Optional[str] = None) -> dict:
    item = {"source_key": key, "source_pointer": pointer,
            "reason_code": reason_code, "raw_sha256": raw_sha256}
    if record_sha256 is not None:
        item["record_sha256"] = record_sha256
    return item


def _warning(code: str, pointer: str) -> dict:
    return {"reason_code": code, "source_pointer": pointer}


def _is_number(value: Any) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(float(value)))


def _normalize_without_checkins(config: dict, normalizer: Callable[[dict], dict]) -> dict:
    source = copy.deepcopy(config)
    source.pop("checkins", None)
    normalized = normalizer(source)
    if not isinstance(normalized, dict):
        raise ValueError("normalizer must return an object")
    normalized = copy.deepcopy(normalized)
    normalized.pop("checkins", None)
    return normalized


def _append_checkins(config: dict, *, source_key: str, config_pointer: str,
                     raw_sha256: str, plan_candidate: dict,
                     checkins: list, quarantine: list, counts: dict):
    if "checkins" not in config:
        return
    values = config.get("checkins")
    if not isinstance(values, list):
        quarantine.append(_quarantine(
            source_key, config_pointer + "/checkins", "checkins_not_array",
            raw_sha256, _sha256_json(values)))
        counts["checkin_records_quarantined"] += 1
        return
    counts["checkin_records_seen"] += len(values)
    for index, value in enumerate(values):
        pointer = f"{config_pointer}/checkins/{index}"
        record_hash = None
        try:
            record_hash = _sha256_json(value)
        except MigrationEnvelopeError:
            pass
        valid = isinstance(value, dict)
        if valid:
            age = value.get("age")
            actual = value.get("actual_total_nominal")
            valid = (_is_number(age) and _is_number(actual)
                     and float(age) >= 0 and float(actual) >= 0)
            if "date" in value and not isinstance(value["date"], str):
                valid = False
        if not valid:
            quarantine.append(_quarantine(
                source_key, pointer, "checkin_record_invalid", raw_sha256,
                record_hash))
            counts["checkin_records_quarantined"] += 1
            continue
        candidate_id = _candidate_id(
            "checkin", plan_candidate["candidate_id"], pointer,
            plan_candidate["normalizer_version"],
            plan_candidate["default_config_sha256"])
        checkins.append({
            "candidate_id": candidate_id,
            "plan_candidate_id": plan_candidate["candidate_id"],
            "plan_version_candidate_id": plan_candidate["version_candidate_id"],
            "source_key": source_key,
            "source_pointer": pointer,
            "source_sha256": record_hash,
            "raw": copy.deepcopy(value),
            "plan_version": plan_candidate["normalized_config"],
        })
        counts["checkin_candidates"] += 1


def _project_config(config: dict, *, source_key: str, source_pointer: str,
                    source_sha256: str, kind: str, envelope_id: str,
                    normalizer: Callable[[dict], dict],
                    normalizer_version: str, default_hash: Optional[str],
                    plans: list, drafts: list, checkins: list,
                    quarantine: list, counts: dict):
    try:
        normalized = _normalize_without_checkins(config, normalizer)
    except Exception:
        quarantine.append(_quarantine(
            source_key, source_pointer, "config_normalization_failed",
            source_sha256, _sha256_json(config)))
        if kind == "plan":
            counts["plan_records_quarantined"] += 1
        else:
            counts["draft_records_quarantined"] += 1
        return
    candidate_kind = "plan" if kind == "plan" else "draft"
    candidate_id = _candidate_id(candidate_kind, envelope_id, source_pointer,
                                 normalizer_version, default_hash)
    version_id = _candidate_id("plan-version", envelope_id, source_pointer,
                               normalizer_version, default_hash)
    candidate = {
        "candidate_id": candidate_id,
        "version_candidate_id": version_id,
        "source_kind": "legacy_plan" if kind == "plan"
                       else "legacy_draft_recovered",
        "source_key": source_key,
        "source_pointer": source_pointer,
        "source_config_sha256": source_sha256,
        "normalized_config_sha256": _sha256_json(normalized),
        "checkin_count": 0,
        "normalizer_version": normalizer_version,
        "default_config_sha256": default_hash,
        "source_config": copy.deepcopy(config),
        "normalized_config": normalized,
    }
    if kind == "plan":
        plans.append(candidate)
        counts["plan_candidates"] += 1
    else:
        drafts.append(candidate)
        counts["draft_candidates"] += 1
    before = len(checkins)
    _append_checkins(config, source_key=source_key,
                     config_pointer=source_pointer, raw_sha256=source_sha256,
                     plan_candidate=candidate, checkins=checkins,
                     quarantine=quarantine, counts=counts)
    candidate["checkin_count"] = len(checkins) - before


def _project_plans(raw: str, entry: dict, *, envelope_id: str,
                   normalizer: Callable[[dict], dict],
                   normalizer_version: str, default_hash: Optional[str],
                   plans: list, drafts: list, checkins: list,
                   quarantine: list, warnings: list, counts: dict):
    key = entry["key"]
    try:
        root = _parse_raw_json(raw)
    except RawJSONError as exc:
        quarantine.append(_quarantine(key, "/fire_plans_v1", exc.reason_code,
                                      entry["raw_sha256"]))
        counts["plan_root_quarantined"] += 1
        return
    if not isinstance(root, list):
        quarantine.append(_quarantine(
            key, "/fire_plans_v1", "plans_root_not_array", entry["raw_sha256"],
            _sha256_json(root)))
        counts["plan_root_quarantined"] += 1
        return
    counts["plan_records_seen"] = len(root)
    legacy_ids = {}
    content_hashes = {}
    names = {}
    for index, record in enumerate(root):
        pointer = f"/fire_plans_v1/{index}"
        record_hash = None
        try:
            record_hash = _sha256_json(record)
        except MigrationEnvelopeError:
            pass
        if not isinstance(record, dict):
            quarantine.append(_quarantine(
                key, pointer, "plan_record_not_object", entry["raw_sha256"],
                record_hash))
            counts["plan_records_quarantined"] += 1
            continue
        config = record.get("config")
        if not isinstance(config, dict):
            quarantine.append(_quarantine(
                key, pointer + "/config", "plan_config_not_object",
                entry["raw_sha256"], record_hash))
            counts["plan_records_quarantined"] += 1
            continue
        config_hash = _sha256_json(config)
        if config_hash in content_hashes:
            warnings.append(_warning("duplicate_plan_content", pointer + "/config"))
        content_hashes[config_hash] = pointer
        if "id" in record:
            legacy_id_hash = _sha256_json(record["id"])
            if legacy_id_hash in legacy_ids:
                warnings.append(_warning("duplicate_legacy_id", pointer + "/id"))
            legacy_ids[legacy_id_hash] = pointer
        if isinstance(record.get("name"), str):
            name = record["name"]
            if name in names:
                warnings.append(_warning("same_display_name_not_merged",
                                         pointer + "/name"))
            names[name] = pointer
        _project_config(
            config, source_key=key, source_pointer=pointer + "/config",
            source_sha256=config_hash, kind="plan", envelope_id=envelope_id,
            normalizer=normalizer, normalizer_version=normalizer_version,
            default_hash=default_hash, plans=plans, drafts=drafts,
            checkins=checkins, quarantine=quarantine, counts=counts)


def _project_draft(raw: str, entry: dict, *, envelope_id: str,
                   normalizer: Callable[[dict], dict],
                   normalizer_version: str, default_hash: Optional[str],
                   plans: list, drafts: list, checkins: list,
                   quarantine: list, counts: dict):
    key = entry["key"]
    try:
        root = _parse_raw_json(raw)
    except RawJSONError as exc:
        quarantine.append(_quarantine(key, "/fire_draft", exc.reason_code,
                                      entry["raw_sha256"]))
        counts["draft_records_quarantined"] += 1
        return
    config_pointer = "/fire_draft"
    config = root
    if isinstance(root, dict) and "v" in root:
        version = root.get("v")
        if (not isinstance(version, int) or isinstance(version, bool)
                or version not in SUPPORTED_DRAFT_VERSIONS):
            quarantine.append(_quarantine(
                key, "/fire_draft/v", "draft_version_unsupported",
                entry["raw_sha256"], _sha256_json(root)))
            counts["draft_records_quarantined"] += 1
            return
        config = root.get("config")
        config_pointer = "/fire_draft/config"
    if not isinstance(config, dict):
        quarantine.append(_quarantine(
            key, config_pointer, "draft_config_not_object", entry["raw_sha256"],
            _sha256_json(root)))
        counts["draft_records_quarantined"] += 1
        return
    config_hash = _sha256_json(config)
    _project_config(
        config, source_key=key, source_pointer=config_pointer,
        source_sha256=config_hash, kind="draft", envelope_id=envelope_id,
        normalizer=normalizer, normalizer_version=normalizer_version,
        default_hash=default_hash, plans=plans, drafts=drafts,
        checkins=checkins, quarantine=quarantine, counts=counts)


def _public_candidate(candidate: dict) -> dict:
    return {key: candidate[key] for key in (
        "candidate_id", "version_candidate_id", "source_kind", "source_key",
        "source_pointer", "source_config_sha256", "normalized_config_sha256",
        "checkin_count")}


def _public_checkin(candidate: dict) -> dict:
    return {key: candidate[key] for key in (
        "candidate_id", "plan_candidate_id", "plan_version_candidate_id",
        "source_key", "source_pointer", "source_sha256")}


def project_envelope(payload: Any, *, normalizer: Optional[Callable[[dict], dict]] = None,
                     default_factory: Optional[Callable[[], dict]] = None) -> dict:
    """Return an in-memory shadow projection with no filesystem side effect."""
    envelope = validate_envelope(payload)
    envelope_id = _sha256_bytes(_canonical_bytes(envelope))
    if normalizer is None:
        normalizer = lambda value: copy.deepcopy(value)
        normalizer_version = "identity-normalizer-v1"
        default_hash = None
    else:
        normalizer_version = NORMALIZER_VERSION
        default_hash = (_sha256_json(default_factory())
                        if default_factory is not None else None)

    plans = []
    drafts = []
    checkins = []
    quarantine = []
    warnings = []
    counts = {
        "source_entries_present": 0,
        "source_entries_absent": 0,
        "plan_records_seen": 0,
        "plan_records_quarantined": 0,
        "plan_root_quarantined": 0,
        "plan_candidates": 0,
        "draft_records_seen": 0,
        "draft_records_quarantined": 0,
        "draft_candidates": 0,
        "checkin_records_seen": 0,
        "checkin_records_quarantined": 0,
        "checkin_candidates": 0,
    }
    for entry in envelope["entries"]:
        if not entry["present"]:
            counts["source_entries_absent"] += 1
            continue
        counts["source_entries_present"] += 1
        if entry["key"] == "fire_plans_v1":
            _project_plans(
                entry["raw"], entry, envelope_id=envelope_id,
                normalizer=normalizer, normalizer_version=normalizer_version,
                default_hash=default_hash, plans=plans, drafts=drafts,
                checkins=checkins, quarantine=quarantine, warnings=warnings,
                counts=counts)
        elif entry["key"] == "fire_draft":
            counts["draft_records_seen"] = 1
            _project_draft(
                entry["raw"], entry, envelope_id=envelope_id,
                normalizer=normalizer, normalizer_version=normalizer_version,
                default_hash=default_hash, plans=plans, drafts=drafts,
                checkins=checkins, quarantine=quarantine, counts=counts)

    warnings.sort(key=lambda item: (item["source_pointer"], item["reason_code"]))
    quarantine.sort(key=lambda item: (item["source_pointer"], item["reason_code"]))
    candidate_count = len(plans) + len(drafts)
    if counts["source_entries_present"] == 0:
        outcome = "empty"
    elif not quarantine:
        outcome = "clean"
    elif candidate_count:
        outcome = "partial"
    else:
        outcome = "blocked"
    counts["quarantine_count"] = len(quarantine)
    counts["warning_count"] = len(warnings)
    counts["balanced"] = (
        counts["checkin_records_seen"] == counts["checkin_candidates"]
        + counts["checkin_records_quarantined"])
    reconciliation = {
        "outcome": outcome,
        "counts": counts,
        "quarantine_count": len(quarantine),
        "warning_count": len(warnings),
    }
    return {
        "mode": "shadow",
        "envelope_version": ENVELOPE_VERSION,
        "projection_version": PROJECTION_VERSION,
        "normalizer_version": normalizer_version,
        "default_config_sha256": default_hash,
        "envelope_sha256": envelope_id,
        "idempotency_key": f"{PROJECTION_VERSION}:envelope:{envelope_id}",
        "reconciliation": reconciliation,
        "plans": [_public_candidate(candidate) for candidate in plans],
        "drafts": [_public_candidate(candidate) for candidate in drafts],
        "checkins": [_public_checkin(candidate) for candidate in checkins],
        "quarantine": quarantine,
        "warnings": warnings,
        "_canonical_envelope": envelope,
        "_plan_candidates": plans,
        "_draft_candidates": drafts,
        "_checkin_candidates": checkins,
    }


def public_projection(projection: dict) -> dict:
    """Strip in-process source/config values before an HTTP response."""
    return {key: copy.deepcopy(projection[key]) for key in (
        "mode", "envelope_version", "projection_version", "normalizer_version",
        "default_config_sha256", "envelope_sha256", "idempotency_key",
        "reconciliation", "plans", "drafts", "checkins", "quarantine",
        "warnings")}


def default_shadow_backup_dir() -> str:
    return os.path.expanduser(
        "~/Library/Application Support/com.local.fire-modeling/migration-shadow")


def _open_backup_dir(directory: str) -> int:
    try:
        path = Path(directory)
    except TypeError as exc:
        raise ShadowBackupError("backup_directory_not_safe") from exc
    if not path.is_absolute() or any(part in ("", ".", "..")
                                    for part in path.parts[1:]):
        raise ShadowBackupError("backup_directory_not_safe")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) \
        | getattr(os, "O_NOFOLLOW", 0)
    try:
        current_fd = os.open(os.sep, flags)
    except OSError as exc:
        raise ShadowBackupError("backup_directory_not_safe") from exc
    try:
        for part in path.parts:
            if part == os.sep:
                continue
            try:
                child_fd = os.open(part, flags, dir_fd=current_fd)
            except FileNotFoundError:
                try:
                    os.mkdir(part, 0o700, dir_fd=current_fd)
                except FileExistsError:
                    pass
                except OSError as exc:
                    raise ShadowBackupError("backup_directory_unavailable") from exc
                try:
                    child_fd = os.open(part, flags, dir_fd=current_fd)
                except OSError as exc:
                    raise ShadowBackupError("backup_directory_not_safe") from exc
            except OSError as exc:
                raise ShadowBackupError("backup_directory_not_safe") from exc
            os.close(current_fd)
            current_fd = child_fd
        st = os.fstat(current_fd)
        if not stat.S_ISDIR(st.st_mode) or stat.S_IMODE(st.st_mode) & 0o077:
            raise ShadowBackupError("backup_directory_permissions")
        result_fd = current_fd
        current_fd = None
        return result_fd
    finally:
        if current_fd is not None:
            os.close(current_fd)


def _read_existing_backup(dir_fd: int, filename: str) -> Optional[bytes]:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(filename, flags, dir_fd=dir_fd)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise ShadowBackupError("backup_existing_not_regular") from exc
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or stat.S_IMODE(st.st_mode) & 0o077:
            raise ShadowBackupError("backup_existing_permissions")
        chunks = []
        while True:
            chunk = os.read(fd, 1024 * 1024)
            if not chunk:
                break
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        os.close(fd)


def _write_all(fd: int, content: bytes):
    offset = 0
    while offset < len(content):
        written = os.write(fd, content[offset:])
        if written <= 0:
            raise OSError("short write")
        offset += written


def persist_raw_backup(envelope: dict, directory: Optional[str] = None) -> dict:
    """Atomically publish one raw envelope without replacing an existing one."""
    canonical = validate_envelope(envelope)
    content = _canonical_bytes(canonical)
    digest = _sha256_bytes(content)
    filename = digest + ".json"
    target_relative = "migration-shadow/" + filename
    directory = directory or default_shadow_backup_dir()
    dir_fd = _open_backup_dir(directory)
    temp_name = f".{filename}.{secrets.token_hex(8)}.tmp"
    temp_fd = None
    try:
        existing = _read_existing_backup(dir_fd, filename)
        if existing is not None:
            if existing != content:
                raise ShadowBackupError("backup_digest_collision")
            return {"status": "already_present", "envelope_sha256": digest,
                    "artifact": target_relative,
                    "format_version": BACKUP_FORMAT_VERSION}
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL \
            | getattr(os, "O_NOFOLLOW", 0)
        try:
            temp_fd = os.open(temp_name, flags, 0o600, dir_fd=dir_fd)
            _write_all(temp_fd, content)
            os.fsync(temp_fd)
        except OSError as exc:
            raise ShadowBackupError("backup_write_failed") from exc
        finally:
            if temp_fd is not None:
                os.close(temp_fd)
                temp_fd = None
        try:
            os.link(temp_name, filename, src_dir_fd=dir_fd,
                    dst_dir_fd=dir_fd, follow_symlinks=False)
        except FileExistsError:
            existing = _read_existing_backup(dir_fd, filename)
            if existing != content:
                raise ShadowBackupError("backup_digest_collision")
            status = "already_present"
        except OSError as exc:
            raise ShadowBackupError("backup_publish_failed") from exc
        else:
            status = "written"
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
            os.fsync(dir_fd)
        except OSError as exc:
            raise ShadowBackupError("backup_finalize_failed") from exc
        return {"status": status, "envelope_sha256": digest,
                "artifact": target_relative,
                "format_version": BACKUP_FORMAT_VERSION}
    finally:
        if temp_fd is not None:
            os.close(temp_fd)
        try:
            os.unlink(temp_name, dir_fd=dir_fd)
        except (FileNotFoundError, OSError):
            pass
        os.close(dir_fd)
