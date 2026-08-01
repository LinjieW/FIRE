"""Phase 0 package capture and archive recovery primitives.

This module owns the control plane that must survive replacement of the
business archive.  It is intentionally independent from the normal
``PersistenceStore`` write path: backup/restore is opt-in, is serialized by a
single manager lock, and never reads WebKit private storage.

The formal localStorage import/cutover workflow is implemented separately in
``formal_migration``.  This module owns the external generation-owner seam used
by the later formal finalize: it can prepare, apply, reconcile, and roll back a
deterministic ``archive_write`` child, but it does not own browser fences or
ordinary Plan/Run writer integration.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import shutil
import sqlite3
import stat
import struct
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

import migration_bridge as MIGRATION
import persistence as PERSISTENCE


PACKAGE_FORMAT = "fire-modeling-backup-v1"
PROJECTION_VERSION = "migration-projection-v1"
TARGET_SCHEMA_VERSION = 7
ARCHIVE_SCHEMA_VERSION = 6
# A package may be captured from any archive schema the app has ever written.
# v8 joined this set when the formal cutover started producing v8 archives: the
# live archive is v8 the moment a cutover completes, so a backup taken after one
# is a v8 backup, and refusing it left a cut-over install with no way to take a
# package at all.
SUPPORTED_SOURCE_SCHEMA_VERSIONS = ("6", "7", "8")
ABSENT_LOGICAL_SHA256 = (
    "98d9795836406c36f08d4ebe3ef610815eb8786d86cd500b6249d9f3c8b91a42")
EMPTY_TARGET_HASH = (
    "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945")
PACKAGE_MEMBERS = (
    "manifest.json", "archive.sqlite3", "localstorage-envelope.json",
    "projection.json", "ready")
DATA_MEMBERS = PACKAGE_MEMBERS[1:]
MAX_MANIFEST_BYTES = 131_072
MAX_ENVELOPE_BYTES = 6_291_456
MAX_PROJECTION_BYTES = 2_097_152
MAX_ARCHIVE_BYTES = 2_147_483_648
HEX64 = frozenset("0123456789abcdef")
MIGRATION_FENCE_TTL_MS = 5 * 60 * 1000
AUTHORITY_INTENT_FORMAT = "fire-authority-intent-v1"


class RecoveryError(RuntimeError):
    """A package or recovery operation failed closed."""


class RecoveryConflict(RecoveryError):
    """The caller's generation or fresh envelope no longer matches."""


class ManualRecoveryRequired(RecoveryError):
    """The journal cannot safely choose a filesystem outcome."""


def _canonical(value: Any) -> bytes:
    try:
        return PERSISTENCE.canonical_json_bytes(value)
    except PERSISTENCE.PersistenceError as exc:
        raise RecoveryError("value is not canonical JSON") from exc


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_json(value: Any) -> str:
    return _sha256(_canonical(value))


def _lp(data: bytes) -> bytes:
    return struct.pack(">Q", len(data)) + data


def _millis_now() -> str:
    now = datetime.now(timezone.utc)
    return now.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _epoch_millis() -> int:
    return int(time.time() * 1000)


def _hex64(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 \
        and not (set(value) - HEX64)


def _validate_page_instance_id(value: Any) -> str:
    if (not isinstance(value, str) or not 1 <= len(value) <= 128
            or not value.isprintable() or any(char.isspace() for char in value)):
        raise RecoveryError("migration browser page identity is invalid")
    return value


def _fence_expiry_ms(fence_id: Any) -> int:
    if not isinstance(fence_id, str):
        raise RecoveryError("migration fence identity is invalid")
    parts = fence_id.split("_")
    if (len(parts) != 4 or parts[0] != "fence"
            or len(parts[1]) != 13 or not parts[1].isdigit()
            or len(parts[2]) != 32 or set(parts[2]) - HEX64
            or not parts[3] or len(parts[3]) % 2
            or set(parts[3]) - HEX64):
        raise RecoveryError("migration fence identity is invalid")
    expires_at_ms = int(parts[1])
    if expires_at_ms <= 0:
        raise RecoveryError("migration fence expiry is invalid")
    return expires_at_ms


def _fence_page_instance_id(fence_id: Any) -> str:
    _fence_expiry_ms(fence_id)
    try:
        page_instance_id = bytes.fromhex(fence_id.split("_")[3]).decode("utf-8")
    except (UnicodeDecodeError, ValueError) as exc:
        raise RecoveryError("migration fence page binding is invalid") from exc
    return _validate_page_instance_id(page_instance_id)


def _normalize_fence_context(value: Any) -> dict:
    required = {"target_count", "target_hash", "projection_sha256",
                "raw_key_sha256", "old_logical_sha256"}
    if not isinstance(value, dict) or set(value) != required:
        raise RecoveryError("migration fence context is incomplete")
    if (type(value["target_count"]) is not int
            or value["target_count"] < 0
            or not _hex64(value["target_hash"])
            or not _hex64(value["projection_sha256"])
            or not _hex64(value["old_logical_sha256"])):
        raise RecoveryError("migration fence context identity is invalid")
    raw_hashes = value["raw_key_sha256"]
    if not isinstance(raw_hashes, dict) or not raw_hashes:
        raise RecoveryError("migration fence raw hashes are invalid")
    for key, digest in raw_hashes.items():
        if (not isinstance(key, str)
                or (digest is not None and not _hex64(digest))):
            raise RecoveryError("migration fence raw hashes are invalid")
    return {
        "target_count": value["target_count"],
        "target_hash": value["target_hash"],
        "projection_sha256": value["projection_sha256"],
        "raw_key_sha256": dict(sorted(raw_hashes.items())),
        "old_logical_sha256": value["old_logical_sha256"],
    }


# The marker a cutover child carries in its receipt payload.  It is what tells
# an archive-write replay whether the child it resolved to is a cutover's or an
# ordinary write's; the two must never be served to each other's caller.
_INTENT_PAYLOAD_KEYS = ("authority_intent_id", "authority_intent_receipt_sha256",
                        "authority_intent_receipt_mac")

# The external request identity an archive-write child carries in its receipt
# payload, written at prepare time and therefore before the bytes land.
#
# It has to be recorded before the commit and *spent by* the commit.  Spending it
# after — the previous shape — left a window in which the archive bytes were
# durable and the record of who asked for them was not: a caller whose ledger
# write failed got an error, resynchronised, retried under the same
# `Idempotency-Key`, and got a second object.  Reserving it before instead would
# make an honest pre-commit failure permanently unretryable under the id the
# caller was told to use.  So it is *carried* pre-commit and *committed* by the
# same transaction that flips the operation to `succeeded`.
_EXTERNAL_REQUEST_PAYLOAD_KEYS = (
    "external_request_kind", "external_request_id",
    "external_body_fingerprint", "external_object_id")


def derive_archive_child_id(operation_id: str) -> str:
    """Derive the one logical archive child reserved for a migration intent."""
    if not isinstance(operation_id, str) or not operation_id:
        raise RecoveryError("authority intent operation id is invalid")
    return "op_" + _sha256_json({
        "format": AUTHORITY_INTENT_FORMAT,
        "operation_id": operation_id,
        "operation_kind": "archive_write",
    })[:32]


def _normalize_authority_intent_body(value: Any) -> dict:
    required = {
        "format", "operation_id", "operation_kind",
        "expected_authority_status", "target_authority_status",
        "expected_generation", "new_generation_id", "old_logical_sha256",
        "fresh_envelope_sha256", "raw_key_sha256", "projection_sha256",
        "target_count", "target_hash", "legacy_fence_id",
        "legacy_fence_digest", "page_instance_id", "archive_child_id",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise RecoveryError("authority intent fields are invalid")
    if value["format"] != AUTHORITY_INTENT_FORMAT \
            or value["operation_kind"] != "migration":
        raise RecoveryError("authority intent format is invalid")
    if (not isinstance(value["operation_id"], str)
            or not value["operation_id"]
            or not isinstance(value["expected_generation"], str)
            or not value["expected_generation"]
            or not isinstance(value["new_generation_id"], str)
            or not value["new_generation_id"]
            or value["expected_generation"] == value["new_generation_id"]):
        raise RecoveryError("authority intent generation binding is invalid")
    if value["expected_authority_status"] not in {
            "legacy_authoritative", "source_changed"} \
            or value["target_authority_status"] != "sqlite_preferred":
        raise RecoveryError("authority intent authority transition is invalid")
    if (not _hex64(value["old_logical_sha256"])
            or not _hex64(value["fresh_envelope_sha256"])
            or not _hex64(value["projection_sha256"])
            or type(value["target_count"]) is not int
            or value["target_count"] < 0
            or not _hex64(value["target_hash"])
            or not _hex64(value["legacy_fence_digest"])
            or not isinstance(value["legacy_fence_id"], str)
            or not isinstance(value["page_instance_id"], str)
            or not isinstance(value["archive_child_id"], str)):
        raise RecoveryError("authority intent identity is invalid")
    _validate_page_instance_id(value["page_instance_id"])
    _fence_expiry_ms(value["legacy_fence_id"])
    raw_hashes = value["raw_key_sha256"]
    if not isinstance(raw_hashes, dict) or not raw_hashes:
        raise RecoveryError("authority intent raw hashes are invalid")
    normalized_raw = {}
    for key, digest in raw_hashes.items():
        if (not isinstance(key, str)
                or (digest is not None and not _hex64(digest))):
            raise RecoveryError("authority intent raw hashes are invalid")
        normalized_raw[key] = digest
    normalized = dict(value)
    normalized["raw_key_sha256"] = dict(sorted(normalized_raw.items()))
    return normalized


def _authority_intent_receipt(body: dict, secret: bytes) -> dict:
    normalized = _normalize_authority_intent_body(body)
    body_json = _canonical(normalized)
    receipt_sha256 = _sha256(body_json)
    receipt_mac = hmac.new(secret, body_json, hashlib.sha256).hexdigest()
    return {
        "body": normalized,
        "body_json": body_json.decode("utf-8"),
        "intent_receipt_sha256": receipt_sha256,
        "intent_receipt_mac": receipt_mac,
        "intent_id": "aint_" + receipt_sha256[:32],
    }


def _secure_dir(path: Path) -> None:
    """Create/validate a private directory without following path symlinks."""
    try:
        fd = MIGRATION._open_backup_dir(str(path))
    except Exception as exc:  # noqa: BLE001
        raise RecoveryError("managed directory is not safe") from exc
    try:
        os.fchmod(fd, 0o700)
    finally:
        os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_new(path: Path, data: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        fd = os.open(str(path), flags, 0o600)
        os.fchmod(fd, 0o600)
        offset = 0
        while offset < len(data):
            count = os.write(fd, data[offset:])
            if count <= 0:
                raise OSError("short write")
            offset += count
        os.fsync(fd)
    except FileExistsError as exc:
        raise RecoveryError("managed artifact already exists") from exc
    except OSError as exc:
        raise RecoveryError("managed artifact write failed") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _read_regular(path: Path, limit: int) -> bytes:
    fd = None
    try:
        fd = os.open(str(path), os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_uid != os.getuid()
                or stat.S_IMODE(before.st_mode) != 0o600
                or before.st_size > limit):
            raise RecoveryError("package member is not a private regular file")
        chunks = []
        total = 0
        while True:
            chunk = os.read(fd, min(1024 * 1024, limit + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise RecoveryError("package member is too large")
        after = os.fstat(fd)
        if (before.st_dev, before.st_ino, before.st_size) != (
                after.st_dev, after.st_ino, after.st_size):
            raise RecoveryError("package member changed while reading")
        return b"".join(chunks)
    except FileNotFoundError as exc:
        raise RecoveryError("package member is missing") from exc
    except OSError as exc:
        raise RecoveryError("package member cannot be read") from exc
    finally:
        if fd is not None:
            os.close(fd)


def _validate_package_directory(path: Path) -> dict[str, bytes]:
    try:
        info = path.lstat()
    except FileNotFoundError as exc:
        raise RecoveryError("package directory is missing") from exc
    if (not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) != 0o700):
        raise RecoveryError("package directory is not private")
    try:
        names = {entry.name for entry in os.scandir(path)}
    except OSError as exc:
        raise RecoveryError("package directory cannot be listed") from exc
    if names != set(PACKAGE_MEMBERS):
        raise RecoveryError("package member allowlist mismatch")
    return {
        "manifest.json": _read_regular(path / "manifest.json", MAX_MANIFEST_BYTES),
        "archive.sqlite3": _read_regular(path / "archive.sqlite3", MAX_ARCHIVE_BYTES),
        "localstorage-envelope.json": _read_regular(
            path / "localstorage-envelope.json", MAX_ENVELOPE_BYTES),
        "projection.json": _read_regular(path / "projection.json", MAX_PROJECTION_BYTES),
        "ready": _read_regular(path / "ready", 0),
    }


def _value_encoding(value: Any) -> bytes:
    if value is None:
        return b"N" + _lp(b"")
    if isinstance(value, int) and not isinstance(value, bool):
        return b"I" + _lp(str(value).encode("utf-8"))
    if isinstance(value, float):
        return b"R" + _lp(struct.pack(">d", value))
    if isinstance(value, bytes):
        return b"B" + _lp(value)
    return b"T" + _lp(str(value).encode("utf-8"))


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def _json_safe_value(value: Any) -> Any:
    """Convert SQLite values to the canonical JSON domain used by evidence."""
    if isinstance(value, bytes):
        return {"__bytes_hex__": value.hex()}
    return value


def _logical_row_hash(conn: sqlite3.Connection, table: str, row_id: str) -> str:
    """Hash one v7 evidence target without depending on SQLite page layout."""
    allowed = {
        "plans": "id", "plan_versions": "id", "recovered_drafts": "draft_id",
        "legacy_checkin_evidence": "evidence_id",
    }
    key = allowed.get(table)
    if key is None:
        raise RecoveryError("unsupported evidence target table")
    row = conn.execute(
        "SELECT * FROM " + _quote_identifier(table) + " WHERE "
        + _quote_identifier(key) + " = ?", (row_id,)).fetchone()
    if row is None:
        raise RecoveryError("evidence target row is missing")
    values = {name: _json_safe_value(row[name]) for name in row.keys()}
    return _sha256_json({"table": table, "row": values})


def _validate_run_status_cardinality(conn: sqlite3.Connection) -> None:
    """Validate status/snapshot cardinality from each owning row."""
    bad_attempt = conn.execute(
        """
        SELECT a.id, a.status, a.snapshot_id, COUNT(s.id) AS snapshot_count
          FROM run_attempts a
          LEFT JOIN run_snapshots s ON s.attempt_id = a.id
         GROUP BY a.id, a.status, a.snapshot_id
        HAVING (a.status = 'completed' AND
                (COUNT(s.id) != 1 OR a.snapshot_id IS NULL OR
                 MAX(CASE WHEN s.id = a.snapshot_id THEN 1 ELSE 0 END) != 1))
            OR (a.status IN ('running','failed','cancelled') AND
                (COUNT(s.id) != 0 OR a.snapshot_id IS NOT NULL))
         LIMIT 1
        """).fetchone()
    if bad_attempt is not None:
        raise RecoveryError("archive attempt/snapshot cardinality is invalid")

    bad_request = conn.execute(
        """
        SELECT r.request_id, r.status, r.snapshot_id, COUNT(s.id) AS snapshot_count
          FROM run_requests r
          LEFT JOIN run_snapshots s
            ON s.id = r.snapshot_id
           AND s.attempt_id = r.attempt_id
           AND s.plan_id = r.plan_id
           AND s.plan_version_id = r.plan_version_id
           AND s.engine_build_id = r.engine_build_id
         GROUP BY r.request_id, r.status, r.snapshot_id
        HAVING (r.status = 'completed' AND
                (COUNT(s.id) != 1 OR r.snapshot_id IS NULL))
            OR (r.status IN ('running','failed','cancelled') AND
                (COUNT(s.id) != 0 OR r.snapshot_id IS NOT NULL))
         LIMIT 1
        """).fetchone()
    if bad_request is not None:
        raise RecoveryError("archive request/snapshot cardinality is invalid")


def _validate_v7_evidence(conn: sqlite3.Connection, *,
                          include_archive_resolution: bool = False) -> None:
    """Validate every non-empty migration evidence surface from its row root.

    v8 extends the operation aggregate with each source record's archive
    resolution.  Keep the v7 aggregate byte-compatible while making the
    additive M2 archive validate the stronger contract.
    """
    authority_rows = conn.execute(
        "SELECT * FROM migration_authority WHERE singleton_id=1").fetchall()
    total_authority = int(conn.execute(
        "SELECT COUNT(*) FROM migration_authority").fetchone()[0])
    if total_authority != 1 or len(authority_rows) != 1:
        raise RecoveryError("v7 authority singleton is missing or duplicated")
    authority = authority_rows[0]
    if (authority["status"] not in {
            "legacy_authoritative", "sqlite_preferred", "source_changed",
            "manual_recovery_required"}
            or int(authority["target_count"]) < 0
            or not _hex64(authority["target_hash"])):
        raise RecoveryError("v7 authority row is invalid")

    operations = {
        row["operation_id"]: row for row in conn.execute(
            "SELECT * FROM migration_operations")
    }
    target_tables = {
        "plan": ("plans", "id"),
        "plan_version": ("plan_versions", "id"),
        "recovered_draft": ("recovered_drafts", "draft_id"),
        "legacy_checkin_evidence": ("legacy_checkin_evidence", "evidence_id"),
    }
    source_rows = conn.execute(
        "SELECT * FROM migration_source_records "
        "ORDER BY operation_id, source_record_id").fetchall()
    source_by_operation: dict[str, list[sqlite3.Row]] = {}
    for source in source_rows:
        operation = operations.get(source["operation_id"])
        if operation is None or operation["status"] not in {
                "raw_backed_up", "imported", "verified", "cutover_marked",
                "source_changed", "failed", "manual_recovery_required"}:
            raise RecoveryError("v7 source record has no valid operation root")
        if (int(source["target_count"]) < 0
                or not _hex64(source["raw_record_sha256"])
                or not _hex64(source["target_hash"])):
            raise RecoveryError("v7 source record identity is invalid")
        source_by_operation.setdefault(source["operation_id"], []).append(source)

        targets = conn.execute(
            "SELECT target_ordinal,target_kind,target_id,target_hash "
            "FROM migration_source_targets WHERE operation_id=? AND "
            "source_record_id=? ORDER BY target_ordinal",
            (source["operation_id"], source["source_record_id"])).fetchall()
        expected_ordinals = list(range(int(source["target_count"])))
        if [int(row["target_ordinal"]) for row in targets] != expected_ordinals:
            raise RecoveryError("v7 source target ordinals are not contiguous")
        target_payload = []
        for target in targets:
            target_kind = target["target_kind"]
            target_table = target_tables.get(target_kind)
            if target_table is None or not _hex64(target["target_hash"]):
                raise RecoveryError("v7 source target identity is invalid")
            table, key = target_table
            exists = conn.execute(
                "SELECT 1 FROM " + _quote_identifier(table) + " WHERE "
                + _quote_identifier(key) + "=?", (target["target_id"],)
            ).fetchone()
            if exists is None:
                raise RecoveryError("v7 source target has no row root")
            if _logical_row_hash(conn, table, target["target_id"]) != target["target_hash"]:
                raise RecoveryError("v7 source target row hash mismatch")
            target_payload.append({
                "ordinal": int(target["target_ordinal"]),
                "kind": target_kind, "id": target["target_id"],
                "hash": target["target_hash"],
            })
        if _sha256_json(target_payload) != source["target_hash"]:
            raise RecoveryError("v7 source target aggregate hash mismatch")

    all_targets = conn.execute(
        "SELECT operation_id,source_record_id,target_ordinal,target_kind,target_id,"
        "target_hash FROM migration_source_targets").fetchall()
    for target in all_targets:
        source = conn.execute(
            "SELECT 1 FROM migration_source_records WHERE operation_id=? "
            "AND source_record_id=?", (target["operation_id"],
                                         target["source_record_id"])).fetchone()
        if source is None:
            raise RecoveryError("v7 target row has no source root")
    reused = conn.execute(
        "SELECT target_kind,target_id,COUNT(DISTINCT target_hash) AS hashes "
        "FROM migration_source_targets GROUP BY target_kind,target_id "
        "HAVING hashes > 1 LIMIT 1").fetchone()
    if reused is not None:
        raise RecoveryError("v7 target row is reused with incompatible hashes")

    for operation_id, operation in operations.items():
        if (int(operation["target_count"]) < 0
                or not _hex64(operation["target_hash"])):
            raise RecoveryError("v7 migration operation target identity is invalid")
        sources = source_by_operation.get(operation_id, [])
        aggregate = []
        for row in sources:
            item = {
                "source_record_id": row["source_record_id"],
                "target_count": int(row["target_count"]),
                "target_hash": row["target_hash"],
            }
            if include_archive_resolution:
                item["archive_resolution"] = row["archive_resolution"]
            aggregate.append(item)
        if int(operation["target_count"]) != sum(
                int(row["target_count"]) for row in sources):
            raise RecoveryError("v7 migration operation target count mismatch")
        if _sha256_json(aggregate) != operation["target_hash"]:
            raise RecoveryError("v7 migration operation target hash mismatch")

    for draft in conn.execute("SELECT * FROM recovered_drafts"):
        operation = operations.get(draft["operation_id"])
        source = conn.execute(
            "SELECT 1 FROM migration_source_records WHERE operation_id=? "
            "AND source_key=? AND json_pointer=?",
            (draft["operation_id"], draft["source_key"], draft["json_pointer"])
        ).fetchone()
        if operation is None or source is None or not _hex64(draft["raw_record_sha256"]):
            raise RecoveryError("v7 recovered draft root is invalid")
        if _sha256(draft["raw_json"].encode("utf-8")) != draft["raw_record_sha256"]:
            raise RecoveryError("v7 recovered draft raw hash mismatch")

    for evidence in conn.execute("SELECT * FROM legacy_checkin_evidence"):
        operation = operations.get(evidence["operation_id"])
        source = conn.execute(
            "SELECT 1 FROM migration_source_records WHERE operation_id=? "
            "AND source_key=? AND json_pointer=?",
            (evidence["operation_id"], evidence["source_key"],
             evidence["json_pointer"])).fetchone()
        if (operation is None or source is None
                or evidence["status"] != "incomplete_inputs"
                or not _hex64(evidence["raw_record_sha256"])
                or _sha256(evidence["raw_json"].encode("utf-8"))
                   != evidence["raw_record_sha256"]):
            raise RecoveryError("v7 legacy evidence root is invalid")

    for event in conn.execute("SELECT * FROM recovered_draft_events"):
        draft = conn.execute(
            "SELECT 1 FROM recovered_drafts WHERE draft_id=? AND operation_id=?",
            (event["draft_id"], event["migration_operation_id"])).fetchone()
        version = conn.execute(
            "SELECT v.*, p.id AS owner_plan_id FROM plan_versions v "
            "JOIN plans p ON p.id=v.plan_id WHERE v.id=?",
            (event["target_plan_version_id"],)).fetchone()
        if (draft is None or event["migration_operation_id"] not in operations
                or version is None or version["owner_plan_id"] != event["target_plan_id"]
                or not _hex64(event["target_hash"])):
            raise RecoveryError("v7 recovered draft event root is invalid")
        plan_hash = _logical_row_hash(conn, "plans", event["target_plan_id"])
        version_hash = _logical_row_hash(conn, "plan_versions",
                                          event["target_plan_version_id"])
        if _sha256_json({"plan_row_hash": plan_hash,
                         "plan_version_row_hash": version_hash}) != event["target_hash"]:
            raise RecoveryError("v7 recovered draft event target hash mismatch")

    for event in conn.execute("SELECT * FROM migration_authority_events"):
        if event["operation_kind"] == "migration" \
                and event["operation_id"] not in operations:
            raise RecoveryError("v7 authority event has no migration root")
        if (not _hex64(event["external_receipt_sha256"])
                or not _hex64(event["external_receipt_mac"])
                or not _hex64(event["target_hash"])
                or int(event["target_count"]) < 0
                or not event["expected_generation"]
                or not event["new_generation"]):
            raise RecoveryError("v7 authority event identity is invalid")

    if (authority["operation_kind"] == "migration"
            and authority["operation_id"] not in operations):
        raise RecoveryError("v7 authority row has no migration root")
    if authority["status"] == "legacy_authoritative" and int(authority["target_count"]) == 0 \
            and authority["target_hash"] != EMPTY_TARGET_HASH:
        raise RecoveryError("v7 empty authority target hash is invalid")


def _validate_v8_lineage(conn: sqlite3.Connection) -> None:
    """Validate the additive M2 CheckIn-to-PlanVersion lineage table."""
    evidence = {
        row["evidence_id"]: row
        for row in conn.execute("SELECT * FROM legacy_checkin_evidence")
    }
    lineage = {
        row["evidence_id"]: row
        for row in conn.execute("SELECT * FROM legacy_checkin_lineage")
    }
    if len(lineage) != conn.execute(
            "SELECT COUNT(*) FROM legacy_checkin_lineage").fetchone()[0]:
        raise RecoveryError("v8 legacy checkin lineage identity is duplicated")
    for evidence_id, row in evidence.items():
        edge = lineage.get(evidence_id)
        if edge is None:
            raise RecoveryError("v8 legacy checkin evidence has no lineage")
        if edge["operation_id"] != row["operation_id"] \
                or not _hex64(edge["target_hash"]):
            raise RecoveryError("v8 legacy checkin lineage root is invalid")
        version = conn.execute(
            "SELECT v.id, v.plan_id FROM plan_versions v "
            "JOIN plans p ON p.id=v.plan_id "
            "WHERE v.id=? AND v.plan_id=?",
            (edge["target_plan_version_id"], edge["target_plan_id"])).fetchone()
        if version is None:
            raise RecoveryError("v8 legacy checkin lineage target is invalid")
        plan_hash = _logical_row_hash(conn, "plans", edge["target_plan_id"])
        version_hash = _logical_row_hash(
            conn, "plan_versions", edge["target_plan_version_id"])
        expected = _sha256_json({
            "plan_row_hash": plan_hash,
            "plan_version_row_hash": version_hash,
        })
        if edge["target_hash"] != expected:
            raise RecoveryError("v8 legacy checkin lineage target hash mismatch")
    for evidence_id in lineage:
        if evidence_id not in evidence:
            raise RecoveryError("v8 lineage points to missing evidence")


def validate_archive_connection(conn: sqlite3.Connection) -> dict:
    """Run the single archive validator used before every logical identity."""
    try:
        PERSISTENCE._readonly_schema_preflight(conn)
        PERSISTENCE.PersistenceStore._validate_state_rows(conn)
        _validate_run_status_cardinality(conn)
        version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if version in (7, 8):
            _validate_v7_evidence(
                conn, include_archive_resolution=(version == 8))
        if version == 8:
            _validate_v8_lineage(conn)
        return {"schema_version": version, "database_state": "present"}
    except (PERSISTENCE.PersistenceError, sqlite3.Error, RecoveryError) as exc:
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError("archive state validation failed") from exc


def logical_identity(path: str) -> str:
    """Return a page-layout-independent identity for a validated archive."""
    conn = PERSISTENCE._readonly_connect(path)
    try:
        validate_archive_connection(conn)
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        migrations = [dict(row) for row in conn.execute(
            "SELECT version, applied_at, app_release_id "
            "FROM schema_migrations ORDER BY version").fetchall()]
        tables = []
        names = [row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        for table in names:
            table_sql = _quote_identifier(table)
            columns = [dict(row) for row in conn.execute(
                "PRAGMA table_info(" + table_sql + ")").fetchall()]
            pk_columns = [row["name"] for row in sorted(
                columns, key=lambda row: int(row["pk"])) if row["pk"]]
            order = ", ".join(_quote_identifier(name) for name in pk_columns)
            if not order:
                order = "rowid"
            rows = []
            for row in conn.execute("SELECT * FROM " + table_sql
                                    + " ORDER BY " + order):
                values = tuple(row)
                preimage = _lp(table.encode("utf-8"))
                preimage += _lp(_canonical(columns))
                preimage += _lp(_canonical(
                    [values[index] for index, column in enumerate(columns)
                     if column["pk"]]))
                preimage += b"".join(_value_encoding(value) for value in values)
                rows.append(_sha256(preimage))
            descriptor = {
                "name": table,
                "columns": columns,
                "row_count": len(rows),
                "row_hashes": rows,
            }
            tables.append(descriptor)
        return _sha256_json({
            "format": "fire-archive-logical-identity-v1",
            "user_version": user_version,
            "schema_migrations": migrations,
            "tables": tables,
        })
    finally:
        conn.close()


def _remove_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if not sidecar.exists():
            continue
        info = sidecar.lstat()
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1):
            raise RecoveryError("SQLite staging sidecar is unsafe")
        sidecar.unlink()


def _checkpoint_delete_journal(path: Path) -> None:
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.execute("PRAGMA journal_mode=DELETE")
    finally:
        conn.close()
    _remove_sidecars(path)


def _empty_archive(path: Path) -> None:
    store = PERSISTENCE.PersistenceStore(str(path), app_release_id="fire-modeling-3.0")
    store.close()
    _checkpoint_delete_journal(path)
    lock_path = Path(str(path) + ".lock")
    if lock_path.exists():
        info = lock_path.lstat()
        if stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
            raise RecoveryError("empty archive lock artifact is unsafe")
        lock_path.unlink()


def _sqlite_backup(source: Path, destination: Path) -> None:
    source_conn = PERSISTENCE._readonly_connect(str(source))
    dest_conn = None
    try:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL \
            | getattr(os, "O_NOFOLLOW", 0)
        fd = os.open(str(destination), flags, 0o600)
        os.close(fd)
        dest_conn = sqlite3.connect(str(destination), isolation_level=None)
        dest_conn.execute("PRAGMA journal_mode=DELETE")
        source_conn.backup(dest_conn, pages=128)
        dest_conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        dest_conn.execute("PRAGMA journal_mode=DELETE")
    except sqlite3.Error as exc:
        raise RecoveryError("SQLite backup API failed") from exc
    except OSError as exc:
        raise RecoveryError("SQLite backup destination failed") from exc
    finally:
        if dest_conn is not None:
            dest_conn.close()
        source_conn.close()
    _remove_sidecars(destination)


CONTROL_SCHEMA = """
CREATE TABLE control_meta (
  singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
  schema_version INTEGER NOT NULL CHECK (schema_version=1),
  created_at TEXT NOT NULL
);
CREATE TABLE control_generation (
  singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
  generation_id TEXT NOT NULL, logical_sha256 TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('present','absent','manual_recovery_required')),
  updated_at TEXT NOT NULL
);
CREATE TABLE control_authority (
  singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
  status TEXT NOT NULL CHECK (status IN
    ('legacy_authoritative','sqlite_preferred','source_changed','manual_recovery_required')),
  operation_id TEXT, operation_kind TEXT CHECK (operation_kind IS NULL OR operation_kind IN
    ('migration','restore','raw_restore','recovery','archive_write','observation')),
  envelope_sha256 TEXT, target_count INTEGER NOT NULL, target_hash TEXT NOT NULL,
  legacy_digest_last_seen TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE control_operations (
  operation_id TEXT PRIMARY KEY,
  kind TEXT NOT NULL CHECK (kind IN
    ('backup','restore','migration','raw_restore','recovery','archive_write','observation')),
  state TEXT NOT NULL CHECK (state IN
    ('previewed','raw_backed_up','imported','verified','cutover_marked','prepared',
     'applying','swapping','succeeded','resolved','rolled_back','conflict',
     'source_changed','failed','resolving','manual_recovery_required')),
  idempotency_key TEXT NOT NULL UNIQUE, request_fingerprint TEXT NOT NULL,
  parent_operation_id TEXT REFERENCES control_operations(operation_id),
  expected_generation TEXT, new_generation_id TEXT, old_logical_sha256 TEXT,
  new_logical_sha256 TEXT, staged_db_sha256 TEXT,
  archive_commit_receipt TEXT, control_ack_receipt TEXT,
  package_id TEXT, envelope_sha256 TEXT, legacy_fence_id TEXT,
  legacy_fence_digest TEXT, staging_path TEXT, preimage_path TEXT,
  receipt_json TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE control_operation_receipts (
  receipt_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL REFERENCES control_operations(operation_id),
  receipt_kind TEXT NOT NULL CHECK (receipt_kind IN
    ('archive_commit','control_commit','control_ack')),
  expected_generation TEXT NOT NULL, new_generation_id TEXT NOT NULL,
  old_logical_sha256 TEXT NOT NULL, expected_new_logical_sha256 TEXT NOT NULL,
  observed_new_logical_sha256 TEXT NOT NULL,
  receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256)=64),
  receipt_mac TEXT NOT NULL CHECK (length(receipt_mac)=64),
  body_json TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (operation_id, receipt_kind),
  CHECK (new_generation_id != expected_generation),
  CHECK (observed_new_logical_sha256 = expected_new_logical_sha256)
);
CREATE TABLE control_failure_receipts (
  receipt_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL REFERENCES control_operations(operation_id),
  failure_kind TEXT NOT NULL CHECK (failure_kind='manual_latch'),
  failure_code TEXT NOT NULL CHECK (failure_code IN
    ('rollback_failed','receipt_mac_invalid','key_unavailable','authority_mismatch',
     'artifact_mismatch','startup_reconciliation_ambiguous')),
  expected_generation TEXT NOT NULL, observed_generation TEXT NOT NULL,
  old_logical_sha256 TEXT NOT NULL, staged_db_sha256 TEXT,
  preimage_sha256 TEXT, package_id TEXT, preserved_artifact_json TEXT NOT NULL,
  receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256)=64),
  receipt_mac TEXT NOT NULL CHECK (length(receipt_mac)=64),
  body_json TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE (operation_id, failure_kind), CHECK (observed_generation=expected_generation)
);
CREATE TABLE control_packages (
  backup_id TEXT PRIMARY KEY, package_dir TEXT NOT NULL UNIQUE,
  manifest_core_sha256 TEXT NOT NULL, manifest_final_sha256 TEXT NOT NULL,
  package_sha256 TEXT NOT NULL, source_schema_version TEXT NOT NULL,
  captured_generation TEXT NOT NULL, archive_sha256 TEXT NOT NULL,
  envelope_sha256 TEXT NOT NULL, projection_sha256 TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('staged','ready','invalid','manual_recovery_required')),
  created_at TEXT NOT NULL
);
CREATE TABLE control_authority_events (
  event_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL REFERENCES control_operations(operation_id),
  operation_kind TEXT NOT NULL CHECK (operation_kind IN
    ('migration','restore','raw_restore','recovery','archive_write','observation')),
  from_status TEXT NOT NULL, to_status TEXT NOT NULL,
  expected_generation TEXT NOT NULL, new_generation_id TEXT NOT NULL,
  envelope_sha256 TEXT, target_count INTEGER NOT NULL CHECK (target_count >= 0),
  target_hash TEXT NOT NULL, legacy_digest_last_seen TEXT,
  receipt_sha256 TEXT NOT NULL, receipt_mac TEXT NOT NULL, created_at TEXT NOT NULL,
  CHECK ((from_status='legacy_authoritative' AND to_status='sqlite_preferred') OR
         (from_status='sqlite_preferred' AND to_status='source_changed') OR
         (from_status='source_changed' AND to_status='sqlite_preferred') OR
         (from_status='manual_recovery_required' AND to_status IN
            ('legacy_authoritative','sqlite_preferred','source_changed')) OR
         (to_status='manual_recovery_required'))
);
CREATE TABLE IF NOT EXISTS control_authority_intents (
  intent_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL REFERENCES control_operations(operation_id),
  operation_kind TEXT NOT NULL CHECK (operation_kind='migration'),
  expected_authority_status TEXT NOT NULL CHECK
    (expected_authority_status IN ('legacy_authoritative','source_changed')),
  target_authority_status TEXT NOT NULL CHECK
    (target_authority_status='sqlite_preferred'),
  expected_generation TEXT NOT NULL,
  new_generation_id TEXT NOT NULL,
  old_logical_sha256 TEXT NOT NULL CHECK (length(old_logical_sha256)=64),
  fresh_envelope_sha256 TEXT NOT NULL CHECK (length(fresh_envelope_sha256)=64),
  raw_key_sha256_json TEXT NOT NULL,
  projection_sha256 TEXT NOT NULL CHECK (length(projection_sha256)=64),
  target_count INTEGER NOT NULL CHECK (target_count >= 0),
  target_hash TEXT NOT NULL CHECK (length(target_hash)=64),
  legacy_fence_id TEXT NOT NULL,
  legacy_fence_digest TEXT NOT NULL CHECK (length(legacy_fence_digest)=64),
  page_instance_id TEXT NOT NULL,
  archive_child_id TEXT NOT NULL,
  intent_receipt_sha256 TEXT NOT NULL CHECK (length(intent_receipt_sha256)=64),
  intent_receipt_mac TEXT NOT NULL CHECK (length(intent_receipt_mac)=64),
  body_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (operation_id), UNIQUE (intent_receipt_sha256),
  UNIQUE (archive_child_id), CHECK (new_generation_id != expected_generation)
);

CREATE TRIGGER control_operation_initial_state
BEFORE INSERT ON control_operations
WHEN NOT ((NEW.kind='backup' AND NEW.state='prepared') OR
          (NEW.kind='restore' AND NEW.state='prepared') OR
          (NEW.kind='recovery' AND NEW.state='resolving') OR
          (NEW.kind='archive_write' AND NEW.state='prepared') OR
          (NEW.kind='observation' AND NEW.state='prepared') OR
          (NEW.kind='migration' AND NEW.state='previewed') OR
          (NEW.kind='raw_restore' AND NEW.state='prepared'))
BEGIN SELECT RAISE(ABORT,'invalid initial control operation state'); END;
CREATE TRIGGER control_operation_initial_bindings
BEFORE INSERT ON control_operations
WHEN ((NEW.kind IN ('backup','raw_restore','migration') AND
       (NEW.expected_generation IS NULL OR NEW.old_logical_sha256 IS NULL OR
        NEW.new_generation_id IS NOT NULL)) OR
      (NEW.kind IN ('restore','recovery','archive_write','observation') AND
       (NEW.expected_generation IS NULL OR NEW.new_generation_id IS NULL OR
        NEW.old_logical_sha256 IS NULL OR NEW.new_generation_id=NEW.expected_generation)))
BEGIN SELECT RAISE(ABORT,'control operation generation binding is incomplete'); END;
CREATE TRIGGER control_operation_transition
BEFORE UPDATE OF state ON control_operations
WHEN NOT (
  (OLD.kind='backup' AND OLD.state='prepared' AND NEW.state IN
    ('succeeded','conflict','manual_recovery_required')) OR
  (OLD.kind='restore' AND OLD.state='prepared' AND NEW.state IN
    ('failed','swapping','conflict','manual_recovery_required')) OR
  (OLD.kind='restore' AND OLD.state='swapping' AND NEW.state IN
    ('succeeded','rolled_back','conflict','manual_recovery_required')) OR
  (OLD.kind='recovery' AND OLD.state='resolving' AND NEW.state IN
    ('applying','manual_recovery_required')) OR
  (OLD.kind='recovery' AND OLD.state='applying' AND NEW.state IN
    ('resolved','manual_recovery_required')) OR
  (OLD.kind='archive_write' AND OLD.state='prepared' AND NEW.state IN
    ('applying','conflict','failed','manual_recovery_required')) OR
  (OLD.kind='archive_write' AND OLD.state='applying' AND NEW.state IN
    ('succeeded','conflict','failed','manual_recovery_required')) OR
  (OLD.kind='observation' AND OLD.state='prepared' AND NEW.state IN
    ('succeeded','failed','manual_recovery_required')) OR
  (OLD.kind='raw_restore' AND OLD.state='prepared' AND NEW.state IN
    ('applying','manual_recovery_required')) OR
  (OLD.kind='raw_restore' AND OLD.state='applying' AND NEW.state IN
    ('succeeded','rolled_back','source_changed','manual_recovery_required')) OR
  (OLD.kind='migration' AND OLD.state='previewed' AND NEW.state IN
    ('raw_backed_up','failed','manual_recovery_required')) OR
  (OLD.kind='migration' AND OLD.state='raw_backed_up' AND NEW.state IN
    ('imported','failed','manual_recovery_required')) OR
  (OLD.kind='migration' AND OLD.state='imported' AND NEW.state IN
    ('verified','source_changed','failed','manual_recovery_required')) OR
  (OLD.kind='migration' AND OLD.state='verified' AND NEW.state IN
    ('cutover_marked','source_changed','failed','manual_recovery_required')))
BEGIN SELECT RAISE(ABORT,'invalid control operation transition'); END;
CREATE TRIGGER control_operation_terminal_receipts
BEFORE UPDATE OF state ON control_operations
WHEN NEW.kind IN ('restore','recovery','archive_write','observation')
 AND NEW.state IN ('succeeded','resolved','cutover_marked')
 AND (NEW.archive_commit_receipt IS NULL OR NEW.control_ack_receipt IS NULL)
BEGIN SELECT RAISE(ABORT,'successful generation operation requires commit and ack receipts'); END;
CREATE TRIGGER control_operation_identity
BEFORE UPDATE ON control_operations
WHEN NEW.operation_id IS NOT OLD.operation_id OR NEW.kind IS NOT OLD.kind OR
     NEW.idempotency_key IS NOT OLD.idempotency_key OR
     NEW.request_fingerprint IS NOT OLD.request_fingerprint OR
     NEW.parent_operation_id IS NOT OLD.parent_operation_id OR
     NEW.expected_generation IS NOT OLD.expected_generation OR
     NEW.new_generation_id IS NOT OLD.new_generation_id OR
     NEW.old_logical_sha256 IS NOT OLD.old_logical_sha256 OR
     (NEW.staged_db_sha256 IS NOT OLD.staged_db_sha256 AND NOT
       (OLD.kind='restore' AND OLD.state='prepared' AND
        OLD.staged_db_sha256 IS NULL AND NEW.staged_db_sha256 IS NOT NULL)) OR
     (NEW.new_logical_sha256 IS NOT OLD.new_logical_sha256 AND NOT
       (OLD.kind='restore' AND OLD.state='prepared' AND
        OLD.new_logical_sha256 IS NULL AND NEW.new_logical_sha256 IS NOT NULL))
BEGIN SELECT RAISE(ABORT,'control operation identity is immutable'); END;
CREATE TRIGGER control_operation_receipt_write_once
BEFORE UPDATE ON control_operations
WHEN NEW.receipt_json IS NOT OLD.receipt_json OR
     (OLD.archive_commit_receipt IS NOT NULL AND
      NEW.archive_commit_receipt IS NOT OLD.archive_commit_receipt) OR
     (OLD.control_ack_receipt IS NOT NULL AND
      NEW.control_ack_receipt IS NOT OLD.control_ack_receipt)
BEGIN SELECT RAISE(ABORT,'control operation receipts are write-once'); END;
CREATE TRIGGER control_operation_update_scope
BEFORE UPDATE ON control_operations
WHEN NEW.updated_at IS NOT OLD.updated_at AND NEW.state IS OLD.state AND
     NEW.new_logical_sha256 IS OLD.new_logical_sha256 AND
     NEW.staged_db_sha256 IS OLD.staged_db_sha256 AND
     NEW.archive_commit_receipt IS OLD.archive_commit_receipt AND
     NEW.control_ack_receipt IS OLD.control_ack_receipt
BEGIN SELECT RAISE(ABORT,'control operation timestamp requires a journal edge'); END;
CREATE TRIGGER control_operation_duplicate
BEFORE INSERT ON control_operations
WHEN EXISTS (SELECT 1 FROM control_operations WHERE operation_id=NEW.operation_id)
   OR EXISTS (SELECT 1 FROM control_operations WHERE idempotency_key=NEW.idempotency_key)
   OR EXISTS (SELECT 1 FROM control_operations
              WHERE request_fingerprint=NEW.request_fingerprint)
BEGIN SELECT RAISE(ABORT,'control operation identity already exists'); END;
CREATE TRIGGER control_operation_no_delete
BEFORE DELETE ON control_operations
BEGIN SELECT RAISE(ABORT,'control operations are append-only'); END;

CREATE TRIGGER control_receipt_duplicate
BEFORE INSERT ON control_operation_receipts
WHEN EXISTS (SELECT 1 FROM control_operation_receipts WHERE receipt_id=NEW.receipt_id)
  OR EXISTS (SELECT 1 FROM control_operation_receipts
             WHERE operation_id=NEW.operation_id AND receipt_kind=NEW.receipt_kind)
  OR NOT EXISTS (SELECT 1 FROM control_operations o
                 WHERE o.operation_id=NEW.operation_id
                   AND o.expected_generation=NEW.expected_generation
                   AND o.new_generation_id=NEW.new_generation_id
                   AND o.old_logical_sha256=NEW.old_logical_sha256
                   AND o.new_logical_sha256=NEW.expected_new_logical_sha256)
BEGIN SELECT RAISE(ABORT,'control receipt root mismatch'); END;
CREATE TRIGGER control_receipt_order
BEFORE INSERT ON control_operation_receipts
WHEN (NEW.receipt_kind='archive_commit' AND NOT EXISTS
        (SELECT 1 FROM control_operations o WHERE o.operation_id=NEW.operation_id
         AND o.kind IN ('restore','recovery','archive_write')
         AND o.state IN ('swapping','applying','succeeded','resolved'))) OR
     (NEW.receipt_kind='control_ack' AND NOT EXISTS
        (SELECT 1 FROM control_operation_receipts r
         WHERE r.operation_id=NEW.operation_id
           AND r.receipt_kind IN ('archive_commit','control_commit')
           AND r.expected_generation=NEW.expected_generation
           AND r.new_generation_id=NEW.new_generation_id
           AND r.old_logical_sha256=NEW.old_logical_sha256
           AND r.expected_new_logical_sha256=NEW.expected_new_logical_sha256
           AND r.observed_new_logical_sha256=NEW.observed_new_logical_sha256))
BEGIN SELECT RAISE(ABORT,'control receipt order is invalid'); END;
CREATE TRIGGER control_receipt_no_update
BEFORE UPDATE ON control_operation_receipts
BEGIN SELECT RAISE(ABORT,'control receipts are immutable'); END;
CREATE TRIGGER control_receipt_no_delete
BEFORE DELETE ON control_operation_receipts
BEGIN SELECT RAISE(ABORT,'control receipts are append-only'); END;
CREATE TRIGGER control_failure_receipt_duplicate
BEFORE INSERT ON control_failure_receipts
WHEN EXISTS (SELECT 1 FROM control_failure_receipts WHERE receipt_id=NEW.receipt_id)
  OR EXISTS (SELECT 1 FROM control_failure_receipts
             WHERE operation_id=NEW.operation_id AND failure_kind=NEW.failure_kind)
  OR NOT EXISTS (SELECT 1 FROM control_operations o
                 WHERE o.operation_id=NEW.operation_id
                   AND o.state='manual_recovery_required'
                   AND o.expected_generation=NEW.expected_generation
                   AND o.expected_generation=NEW.observed_generation
                   AND o.old_logical_sha256=NEW.old_logical_sha256
                   AND o.staged_db_sha256 IS NEW.staged_db_sha256
                   AND o.package_id IS NEW.package_id)
BEGIN SELECT RAISE(ABORT,'control failure receipt root mismatch'); END;
CREATE TRIGGER control_failure_receipt_no_update
BEFORE UPDATE ON control_failure_receipts
BEGIN SELECT RAISE(ABORT,'control failure receipts are immutable'); END;
CREATE TRIGGER control_failure_receipt_no_delete
BEFORE DELETE ON control_failure_receipts
BEGIN SELECT RAISE(ABORT,'control failure receipts are append-only'); END;
CREATE TRIGGER control_operation_receipt_binding
BEFORE UPDATE ON control_operations
WHEN (NEW.archive_commit_receipt IS NOT NULL AND NOT EXISTS
        (SELECT 1 FROM control_operation_receipts r WHERE r.receipt_id=NEW.archive_commit_receipt
         AND r.operation_id=NEW.operation_id AND r.receipt_kind IN ('archive_commit','control_commit'))) OR
     (NEW.control_ack_receipt IS NOT NULL AND NOT EXISTS
        (SELECT 1 FROM control_operation_receipts r WHERE r.receipt_id=NEW.control_ack_receipt
         AND r.operation_id=NEW.operation_id AND r.receipt_kind='control_ack'))
BEGIN SELECT RAISE(ABORT,'control operation receipt reference is invalid'); END;

CREATE TRIGGER control_generation_duplicate
BEFORE INSERT ON control_generation
WHEN EXISTS (SELECT 1 FROM control_generation WHERE singleton_id=NEW.singleton_id)
BEGIN SELECT RAISE(ABORT,'control generation singleton already exists'); END;
CREATE TRIGGER control_generation_no_same_value
BEFORE UPDATE ON control_generation
WHEN NEW.generation_id IS OLD.generation_id OR NEW.generation_id IS NULL
  OR NEW.logical_sha256 IS NULL
BEGIN SELECT RAISE(ABORT,'control generation must advance to a new identity'); END;
CREATE TRIGGER control_generation_no_delete
BEFORE DELETE ON control_generation
BEGIN SELECT RAISE(ABORT,'control generation is append-only'); END;
CREATE TRIGGER control_generation_receipt_guard
BEFORE UPDATE ON control_generation
WHEN NOT EXISTS (SELECT 1 FROM control_operations o
                 WHERE o.new_generation_id=NEW.generation_id
                   AND o.expected_generation=OLD.generation_id
                   AND o.new_logical_sha256=NEW.logical_sha256
                   AND o.archive_commit_receipt IS NOT NULL
                   AND o.control_ack_receipt IS NOT NULL
                   AND EXISTS (SELECT 1 FROM control_operation_receipts r
                              WHERE r.receipt_id=o.archive_commit_receipt
                                AND r.observed_new_logical_sha256=NEW.logical_sha256)
                   AND EXISTS (SELECT 1 FROM control_operation_receipts r
                              WHERE r.receipt_id=o.control_ack_receipt
                                AND r.observed_new_logical_sha256=NEW.logical_sha256)
                   AND o.state IN ('succeeded','resolved','cutover_marked'))
BEGIN SELECT RAISE(ABORT,'unreceipted control generation update'); END;

CREATE TRIGGER control_package_duplicate
BEFORE INSERT ON control_packages
WHEN EXISTS (SELECT 1 FROM control_packages WHERE backup_id=NEW.backup_id)
  OR EXISTS (SELECT 1 FROM control_packages WHERE package_dir=NEW.package_dir)
BEGIN SELECT RAISE(ABORT,'control package identity already exists'); END;
CREATE TRIGGER control_package_initial_state
BEFORE INSERT ON control_packages
WHEN NEW.state != 'staged'
BEGIN SELECT RAISE(ABORT,'control package must start staged'); END;
CREATE TRIGGER control_package_transition
BEFORE UPDATE OF state ON control_packages
WHEN NOT ((OLD.state='staged' AND NEW.state IN ('ready','invalid','manual_recovery_required')) OR
          (OLD.state IN ('ready','invalid') AND NEW.state='manual_recovery_required'))
BEGIN SELECT RAISE(ABORT,'invalid control package transition'); END;
CREATE TRIGGER control_package_no_delete
BEFORE DELETE ON control_packages
BEGIN SELECT RAISE(ABORT,'control packages are append-only'); END;

CREATE TRIGGER control_authority_event_duplicate
BEFORE INSERT ON control_authority_events
WHEN EXISTS (SELECT 1 FROM control_authority_events WHERE event_id=NEW.event_id)
BEGIN SELECT RAISE(ABORT,'authority event identity already exists'); END;
CREATE TRIGGER control_authority_event_root
BEFORE INSERT ON control_authority_events
WHEN NOT (
  (EXISTS (SELECT 1 FROM control_operations o WHERE o.operation_id=NEW.operation_id
           AND o.kind=NEW.operation_kind
           AND o.expected_generation=NEW.expected_generation
           AND o.new_generation_id=NEW.new_generation_id
           AND o.state IN ('succeeded','resolved','cutover_marked'))
   AND EXISTS (SELECT 1 FROM control_operation_receipts r
               WHERE r.operation_id=NEW.operation_id AND r.receipt_kind='control_ack'
                 AND r.receipt_sha256=NEW.receipt_sha256 AND r.receipt_mac=NEW.receipt_mac)) OR
  (NEW.to_status='manual_recovery_required' AND NEW.new_generation_id=NEW.expected_generation
   AND EXISTS (SELECT 1 FROM control_operations o WHERE o.operation_id=NEW.operation_id
               AND o.kind=NEW.operation_kind AND o.state='manual_recovery_required'
               AND o.expected_generation=NEW.expected_generation)
   AND EXISTS (SELECT 1 FROM control_failure_receipts f WHERE f.operation_id=NEW.operation_id
               AND f.failure_kind='manual_latch' AND f.receipt_sha256=NEW.receipt_sha256
               AND f.receipt_mac=NEW.receipt_mac
               AND f.expected_generation=NEW.expected_generation
               AND f.observed_generation=NEW.new_generation_id)))
BEGIN SELECT RAISE(ABORT,'authority event has no matching control intent'); END;
CREATE TRIGGER control_authority_event_no_update
BEFORE UPDATE ON control_authority_events
BEGIN SELECT RAISE(ABORT,'control authority events are immutable'); END;
CREATE TRIGGER control_authority_event_no_delete
BEFORE DELETE ON control_authority_events
BEGIN SELECT RAISE(ABORT,'control authority events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS control_authority_intent_root
BEFORE INSERT ON control_authority_intents
WHEN NOT EXISTS (
  SELECT 1 FROM control_operations o
   WHERE o.operation_id=NEW.operation_id
     AND o.kind='migration'
     AND o.state='verified'
     AND o.expected_generation=NEW.expected_generation
     AND o.old_logical_sha256=NEW.old_logical_sha256
     AND o.envelope_sha256=NEW.fresh_envelope_sha256
     AND o.legacy_fence_id=NEW.legacy_fence_id
     AND o.legacy_fence_digest=NEW.legacy_fence_digest
)
BEGIN SELECT RAISE(ABORT,'authority intent has no verified migration root'); END;
CREATE TRIGGER IF NOT EXISTS control_authority_intent_no_update
BEFORE UPDATE ON control_authority_intents
BEGIN SELECT RAISE(ABORT,'authority intents are immutable'); END;
CREATE TRIGGER IF NOT EXISTS control_authority_intent_no_delete
BEFORE DELETE ON control_authority_intents
BEGIN SELECT RAISE(ABORT,'authority intents are append-only'); END;
CREATE TRIGGER control_authority_transition
BEFORE UPDATE ON control_authority
WHEN NOT EXISTS (SELECT 1 FROM control_authority_events e
                 WHERE e.from_status=OLD.status AND e.to_status=NEW.status
                   AND e.operation_id=NEW.operation_id
                   AND e.operation_kind IS NEW.operation_kind
                   AND e.envelope_sha256 IS NEW.envelope_sha256
                   AND e.target_count=NEW.target_count
                   AND e.target_hash=NEW.target_hash
                   AND e.legacy_digest_last_seen IS NEW.legacy_digest_last_seen
                   AND (EXISTS (SELECT 1 FROM control_operation_receipts r
                                WHERE r.operation_id=e.operation_id
                                  AND r.receipt_kind='control_ack'
                                  AND r.receipt_sha256=e.receipt_sha256)
                        OR (e.to_status='manual_recovery_required' AND EXISTS
                            (SELECT 1 FROM control_failure_receipts f
                             WHERE f.operation_id=e.operation_id
                               AND f.failure_kind='manual_latch'
                               AND f.receipt_sha256=e.receipt_sha256
                               AND f.receipt_mac=e.receipt_mac))))
BEGIN SELECT RAISE(ABORT,'invalid or unreceipted control authority transition'); END;
"""


# The external client request identity, kept separately from the internal
# CAS fingerprint on purpose.
#
# The internal key hashes the generation, the authority receipt, and the whole
# body, which is right for a compare-and-swap: it is what makes a write refuse
# to land on an epoch it was not computed against.  It is exactly wrong as a
# record of "has this caller already asked for this".  A successful write
# advances the epoch, so after the caller resynchronises, the same external
# `request_id` hashes to a different internal key — and the object id derived
# from it named a different object.  The result was a twin: the caller believed
# it was retrying one action and got two.
#
# So the stable half lives here, keyed only by the endpoint and the caller's own
# request id.  Nothing about the epoch enters the key, which is what makes it
# unable to forget.  Append-only by trigger, because a record that can be
# deleted is a record that can be made to forget on demand.
EXTERNAL_REQUEST_CONTROL_EXTENSION = """
CREATE TABLE IF NOT EXISTS control_external_requests (
  request_kind TEXT NOT NULL,
  external_request_id TEXT NOT NULL,
  body_fingerprint TEXT NOT NULL CHECK (length(body_fingerprint)=64),
  object_key TEXT NOT NULL CHECK (length(object_key)=64),
  object_id TEXT,
  observed_generation TEXT NOT NULL,
  observed_authority_receipt TEXT,
  created_at TEXT NOT NULL,
  PRIMARY KEY (request_kind, external_request_id)
);
CREATE TRIGGER IF NOT EXISTS control_external_request_no_update
BEFORE UPDATE ON control_external_requests
BEGIN SELECT RAISE(ABORT,'external request records are immutable'); END;
CREATE TRIGGER IF NOT EXISTS control_external_request_no_delete
BEFORE DELETE ON control_external_requests
BEGIN SELECT RAISE(ABORT,'external request records are append-only'); END;
"""


RAW_CONTROL_EXTENSION = """
CREATE TABLE IF NOT EXISTS control_raw_restore (
  operation_id TEXT PRIMARY KEY REFERENCES control_operations(operation_id),
  target_envelope_json TEXT NOT NULL, target_sha256 TEXT NOT NULL
    CHECK (length(target_sha256)=64),
  preimage_envelope_json TEXT NOT NULL, preimage_sha256 TEXT NOT NULL
    CHECK (length(preimage_sha256)=64),
  key_order_json TEXT NOT NULL,
  expected_generation TEXT NOT NULL,
  phase TEXT NOT NULL CHECK (phase IN
    ('prepared','applying','succeeded','rolled_back','manual_recovery_required')),
  readback_envelope_json TEXT, readback_sha256 TEXT,
  outcome_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS control_raw_restore_outcomes (
  outcome_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL UNIQUE REFERENCES control_operations(operation_id),
  outcome_kind TEXT NOT NULL CHECK (outcome_kind IN
    ('succeeded','rolled_back','manual_recovery_required')),
  expected_generation TEXT NOT NULL, target_sha256 TEXT NOT NULL,
  preimage_sha256 TEXT NOT NULL, readback_sha256 TEXT NOT NULL,
  receipt_sha256 TEXT NOT NULL CHECK (length(receipt_sha256)=64),
  receipt_mac TEXT NOT NULL CHECK (length(receipt_mac)=64),
  body_json TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TRIGGER IF NOT EXISTS raw_restore_root_insert
BEFORE INSERT ON control_raw_restore
WHEN NOT EXISTS (SELECT 1 FROM control_operations o
                 WHERE o.operation_id=NEW.operation_id
                   AND o.kind='raw_restore'
                   AND o.expected_generation=NEW.expected_generation
                   AND o.state='prepared')
BEGIN SELECT RAISE(ABORT,'raw restore root mismatch'); END;
CREATE TRIGGER IF NOT EXISTS raw_restore_identity_guard
BEFORE UPDATE ON control_raw_restore
WHEN NEW.operation_id IS NOT OLD.operation_id
  OR NEW.target_envelope_json IS NOT OLD.target_envelope_json
  OR NEW.target_sha256 IS NOT OLD.target_sha256
  OR NEW.preimage_envelope_json IS NOT OLD.preimage_envelope_json
  OR NEW.preimage_sha256 IS NOT OLD.preimage_sha256
  OR NEW.key_order_json IS NOT OLD.key_order_json
  OR NEW.expected_generation IS NOT OLD.expected_generation
  OR (NEW.phase IS OLD.phase AND NEW.updated_at IS NOT OLD.updated_at)
BEGIN SELECT RAISE(ABORT,'raw restore identity is immutable'); END;
CREATE TRIGGER IF NOT EXISTS raw_restore_no_delete
BEFORE DELETE ON control_raw_restore
BEGIN SELECT RAISE(ABORT,'raw restore records are append-only'); END;
CREATE TRIGGER IF NOT EXISTS raw_restore_outcome_root
BEFORE INSERT ON control_raw_restore_outcomes
WHEN EXISTS (SELECT 1 FROM control_raw_restore_outcomes
             WHERE operation_id=NEW.operation_id)
  OR NOT EXISTS (SELECT 1 FROM control_raw_restore r
                 WHERE r.operation_id=NEW.operation_id
                   AND r.expected_generation=NEW.expected_generation
                   AND r.target_sha256=NEW.target_sha256
                   AND r.preimage_sha256=NEW.preimage_sha256)
BEGIN SELECT RAISE(ABORT,'raw restore outcome root mismatch'); END;
CREATE TRIGGER IF NOT EXISTS raw_restore_outcome_no_update
BEFORE UPDATE ON control_raw_restore_outcomes
BEGIN SELECT RAISE(ABORT,'raw restore outcomes are immutable'); END;
CREATE TRIGGER IF NOT EXISTS raw_restore_outcome_no_delete
BEFORE DELETE ON control_raw_restore_outcomes
BEGIN SELECT RAISE(ABORT,'raw restore outcomes are append-only'); END;
"""


# M1 uses the existing journal table but needs its immutable migration-input
# bindings to be enforced for both fresh and pre-existing control databases.
# This additive trigger is safe to install on restart; it does not alter the
# journal schema or any already-recorded recovery operation.
MIGRATION_CONTROL_EXTENSION = """
CREATE TRIGGER IF NOT EXISTS control_operation_migration_identity
BEFORE UPDATE ON control_operations
WHEN OLD.kind='migration' AND (
     NEW.envelope_sha256 IS NOT OLD.envelope_sha256
  OR NEW.package_id IS NOT OLD.package_id
  OR NEW.staging_path IS NOT OLD.staging_path
  OR NEW.preimage_path IS NOT OLD.preimage_path
  OR NEW.receipt_json IS NOT OLD.receipt_json)
BEGIN SELECT RAISE(ABORT,'migration operation input binding is immutable'); END;
"""


# M3 extends the already-seeded control journal with the generation-owner
# invariants that cannot be expressed by the original recovery slice alone.
# These are additive, restart-safe guards: they make archive-write ownership
# explicit without changing any existing operation rows or reopening the
# frozen filesystem/SQL hardening family.
ARCHIVE_WRITE_CONTROL_EXTENSION = """
CREATE TABLE IF NOT EXISTS control_authority_intents (
  intent_id TEXT PRIMARY KEY,
  operation_id TEXT NOT NULL REFERENCES control_operations(operation_id),
  operation_kind TEXT NOT NULL CHECK (operation_kind='migration'),
  expected_authority_status TEXT NOT NULL CHECK
    (expected_authority_status IN ('legacy_authoritative','source_changed')),
  target_authority_status TEXT NOT NULL CHECK
    (target_authority_status='sqlite_preferred'),
  expected_generation TEXT NOT NULL,
  new_generation_id TEXT NOT NULL,
  old_logical_sha256 TEXT NOT NULL CHECK (length(old_logical_sha256)=64),
  fresh_envelope_sha256 TEXT NOT NULL CHECK (length(fresh_envelope_sha256)=64),
  raw_key_sha256_json TEXT NOT NULL,
  projection_sha256 TEXT NOT NULL CHECK (length(projection_sha256)=64),
  target_count INTEGER NOT NULL CHECK (target_count >= 0),
  target_hash TEXT NOT NULL CHECK (length(target_hash)=64),
  legacy_fence_id TEXT NOT NULL,
  legacy_fence_digest TEXT NOT NULL CHECK (length(legacy_fence_digest)=64),
  page_instance_id TEXT NOT NULL,
  archive_child_id TEXT NOT NULL,
  intent_receipt_sha256 TEXT NOT NULL CHECK (length(intent_receipt_sha256)=64),
  intent_receipt_mac TEXT NOT NULL CHECK (length(intent_receipt_mac)=64),
  body_json TEXT NOT NULL,
  created_at TEXT NOT NULL,
  UNIQUE (operation_id), UNIQUE (intent_receipt_sha256),
  UNIQUE (archive_child_id), CHECK (new_generation_id != expected_generation)
);
CREATE TRIGGER IF NOT EXISTS control_authority_intent_root
BEFORE INSERT ON control_authority_intents
WHEN NOT EXISTS (
  SELECT 1 FROM control_operations o
   WHERE o.operation_id=NEW.operation_id
     AND o.kind='migration'
     AND o.state='verified'
     AND o.expected_generation=NEW.expected_generation
     AND o.old_logical_sha256=NEW.old_logical_sha256
     AND o.envelope_sha256=NEW.fresh_envelope_sha256
     AND o.legacy_fence_id=NEW.legacy_fence_id
     AND o.legacy_fence_digest=NEW.legacy_fence_digest
)
BEGIN SELECT RAISE(ABORT,'authority intent has no verified migration root'); END;
CREATE TRIGGER IF NOT EXISTS control_authority_intent_no_update
BEFORE UPDATE ON control_authority_intents
BEGIN SELECT RAISE(ABORT,'authority intents are immutable'); END;
CREATE TRIGGER IF NOT EXISTS control_authority_intent_no_delete
BEFORE DELETE ON control_authority_intents
BEGIN SELECT RAISE(ABORT,'authority intents are append-only'); END;
CREATE TABLE IF NOT EXISTS control_authority_intent_events (
  event_id TEXT PRIMARY KEY
    REFERENCES control_authority_events(event_id),
  intent_id TEXT NOT NULL
    REFERENCES control_authority_intents(intent_id),
  operation_id TEXT NOT NULL
    REFERENCES control_operations(operation_id),
  intent_receipt_sha256 TEXT NOT NULL CHECK (length(intent_receipt_sha256)=64),
  intent_receipt_mac TEXT NOT NULL CHECK (length(intent_receipt_mac)=64),
  created_at TEXT NOT NULL,
  UNIQUE (intent_id),
  UNIQUE (operation_id)
);
CREATE TRIGGER IF NOT EXISTS control_authority_intent_event_root
BEFORE INSERT ON control_authority_intent_events
WHEN NOT EXISTS (
  SELECT 1
    FROM control_authority_events e
    JOIN control_authority_intents i ON i.intent_id = NEW.intent_id
    JOIN control_operations c ON c.operation_id = NEW.operation_id
   WHERE e.event_id            = NEW.event_id
     AND e.operation_id        = NEW.operation_id
     AND e.operation_kind      = 'archive_write'
     AND e.from_status         = i.expected_authority_status
     AND e.to_status           = i.target_authority_status
     AND e.expected_generation = i.expected_generation
     AND e.new_generation_id   = i.new_generation_id
     AND e.envelope_sha256     = i.fresh_envelope_sha256
     AND e.target_count        = i.target_count
     AND e.target_hash         = i.target_hash
     AND c.kind                = 'archive_write'
     AND c.state               = 'succeeded'
     AND c.operation_id        = i.archive_child_id
     AND c.parent_operation_id = i.operation_id
     AND c.expected_generation = i.expected_generation
     AND c.new_generation_id   = i.new_generation_id
     AND c.old_logical_sha256  = i.old_logical_sha256
     AND c.envelope_sha256     = i.fresh_envelope_sha256
     AND NEW.intent_receipt_sha256 = i.intent_receipt_sha256
     AND NEW.intent_receipt_mac    = i.intent_receipt_mac
)
BEGIN SELECT RAISE(ABORT,'authority intent event has no matching intent and archive child'); END;
CREATE TRIGGER IF NOT EXISTS control_authority_intent_event_no_update
BEFORE UPDATE ON control_authority_intent_events
BEGIN SELECT RAISE(ABORT,'authority intent events are immutable'); END;
CREATE TRIGGER IF NOT EXISTS control_authority_intent_event_no_delete
BEFORE DELETE ON control_authority_intent_events
BEGIN SELECT RAISE(ABORT,'authority intent events are append-only'); END;
-- control_receipt_order never learned about `observation`, while its two
-- sibling guards already assume it is a receipted generation operation:
-- control_operation_transition allows observation prepared -> succeeded, and
-- control_operation_terminal_receipts requires both receipts before it may.
-- The three could not all hold, so a succeeding observation was unreachable.
--
-- The arm that closes the gap has to be a `control_commit` one.  An
-- observation commits no archive bytes, so letting it into the archive_commit
-- branch would have it sign a mutation that never happened, and no reader
-- downstream — authority event, generation guard, or audit — could separate
-- that claim from a real archive write.  So the two cases are constrained
-- separately: `archive_commit` stays exactly as it was, restricted to the
-- three archive owners, and `observation` gets its own `control_commit` arm.
-- The `control_ack` branch is reproduced verbatim; it already accepted either
-- commit kind as its predecessor, which is what makes
-- `control_commit -> control_ack` orderable without touching it.
--
-- An observation is `prepared` when its commit receipt is written because that
-- is the only pre-terminal state its transition guard permits.
DROP TRIGGER IF EXISTS control_receipt_order;
CREATE TRIGGER control_receipt_order
BEFORE INSERT ON control_operation_receipts
WHEN (NEW.receipt_kind='archive_commit' AND NOT EXISTS
        (SELECT 1 FROM control_operations o WHERE o.operation_id=NEW.operation_id
         AND o.kind IN ('restore','recovery','archive_write')
         AND o.state IN ('swapping','applying','succeeded','resolved'))) OR
     (NEW.receipt_kind='control_commit' AND NOT EXISTS
        (SELECT 1 FROM control_operations o WHERE o.operation_id=NEW.operation_id
         AND o.kind='observation'
         AND o.state IN ('prepared','succeeded'))) OR
     (NEW.receipt_kind='control_ack' AND NOT EXISTS
        (SELECT 1 FROM control_operation_receipts r
         WHERE r.operation_id=NEW.operation_id
           AND r.receipt_kind IN ('archive_commit','control_commit')
           AND r.expected_generation=NEW.expected_generation
           AND r.new_generation_id=NEW.new_generation_id
           AND r.old_logical_sha256=NEW.old_logical_sha256
           AND r.expected_new_logical_sha256=NEW.expected_new_logical_sha256
           AND r.observed_new_logical_sha256=NEW.observed_new_logical_sha256))
BEGIN SELECT RAISE(ABORT,'control receipt order is invalid'); END;
-- The commit-receipt column is named `archive_commit_receipt`, and its
-- original binding guard accepted either commit kind for any operation kind.
-- That is the loophole the archive_commit arm above no longer reaches through
-- the receipts table: bind the column to the kind its owner is allowed to
-- produce, so an observation's commit slot can hold only a `control_commit`
-- and an archive owner's can hold only an `archive_commit`.
DROP TRIGGER IF EXISTS control_operation_receipt_binding;
CREATE TRIGGER control_operation_receipt_binding
BEFORE UPDATE ON control_operations
WHEN (NEW.archive_commit_receipt IS NOT NULL AND NOT EXISTS
        (SELECT 1 FROM control_operation_receipts r WHERE r.receipt_id=NEW.archive_commit_receipt
         AND r.operation_id=NEW.operation_id
         AND r.receipt_kind = CASE WHEN NEW.kind='observation'
                                   THEN 'control_commit' ELSE 'archive_commit' END)) OR
     (NEW.control_ack_receipt IS NOT NULL AND NOT EXISTS
        (SELECT 1 FROM control_operation_receipts r WHERE r.receipt_id=NEW.control_ack_receipt
         AND r.operation_id=NEW.operation_id AND r.receipt_kind='control_ack'))
BEGIN SELECT RAISE(ABORT,'control operation receipt reference is invalid'); END;
-- Terminal-state guard, restated in terms of the exact receipt kinds and their
-- order rather than "both columns are non-NULL".  The binding trigger above
-- already pins each column to the kind its owner may produce; this one adds
-- the ordering, so no operation can reach a receipted terminal state with its
-- ack recorded before the commit it is supposed to acknowledge.
DROP TRIGGER IF EXISTS control_operation_terminal_receipts;
CREATE TRIGGER control_operation_terminal_receipts
BEFORE UPDATE OF state ON control_operations
WHEN NEW.kind IN ('restore','recovery','archive_write','observation')
 AND NEW.state IN ('succeeded','resolved','cutover_marked')
 AND NOT EXISTS (
   SELECT 1 FROM control_operation_receipts c, control_operation_receipts a
    WHERE c.receipt_id = NEW.archive_commit_receipt
      AND a.receipt_id = NEW.control_ack_receipt
      AND c.operation_id = NEW.operation_id
      AND a.operation_id = NEW.operation_id
      AND c.receipt_kind = CASE WHEN NEW.kind='observation'
                                THEN 'control_commit' ELSE 'archive_commit' END
      AND a.receipt_kind = 'control_ack'
      AND c.rowid < a.rowid)
BEGIN SELECT RAISE(ABORT,'successful generation operation requires commit and ack receipts'); END;
DROP TRIGGER IF EXISTS control_operation_transition;
CREATE TRIGGER control_operation_transition
BEFORE UPDATE OF state ON control_operations
WHEN NOT (
  (OLD.kind='backup' AND OLD.state='prepared' AND NEW.state IN
    ('succeeded','conflict','manual_recovery_required')) OR
  (OLD.kind='restore' AND OLD.state='prepared' AND NEW.state IN
    ('failed','swapping','conflict','manual_recovery_required')) OR
  (OLD.kind='restore' AND OLD.state='swapping' AND NEW.state IN
    ('succeeded','rolled_back','conflict','manual_recovery_required')) OR
  (OLD.kind='recovery' AND OLD.state='resolving' AND NEW.state IN
    ('applying','manual_recovery_required')) OR
  (OLD.kind='recovery' AND OLD.state='applying' AND NEW.state IN
    ('resolved','manual_recovery_required')) OR
  (OLD.kind='archive_write' AND OLD.state='prepared' AND NEW.state IN
    ('applying','conflict','failed','manual_recovery_required')) OR
  (OLD.kind='archive_write' AND OLD.state='applying' AND NEW.state IN
    ('succeeded','conflict','failed','manual_recovery_required')) OR
  (OLD.kind='observation' AND OLD.state='prepared' AND NEW.state IN
    ('succeeded','failed','manual_recovery_required')) OR
  (OLD.kind='raw_restore' AND OLD.state='prepared' AND NEW.state IN
    ('applying','manual_recovery_required')) OR
  (OLD.kind='raw_restore' AND OLD.state='applying' AND NEW.state IN
    ('succeeded','rolled_back','source_changed','manual_recovery_required')) OR
  (OLD.kind='migration' AND OLD.state='previewed' AND NEW.state IN
    ('raw_backed_up','failed','manual_recovery_required')) OR
  (OLD.kind='migration' AND OLD.state='raw_backed_up' AND NEW.state IN
    ('imported','failed','manual_recovery_required')) OR
  (OLD.kind='migration' AND OLD.state='imported' AND NEW.state IN
    ('verified','source_changed','failed','manual_recovery_required')) OR
  (OLD.kind='migration' AND OLD.state='verified' AND NEW.state IN
    ('cutover_marked','source_changed','failed','manual_recovery_required')))
BEGIN SELECT RAISE(ABORT,'invalid control operation transition'); END;

-- M3 wrote this as "only an archive_write may have a parent", which silently
-- disabled M1's `retry_nonce`: a retry preview records the attempt it is
-- retrying as its parent, and that INSERT has been aborting ever since.  A
-- migration therefore had no way to be retried at all, which is half of why an
-- abandoned cutover was unrecoverable.  The two arms are complements, not
-- duplicates: an archive_write's parent must still be live, while a retried
-- migration's parent must be exactly the terminal attempt `retry_nonce` already
-- requires it to be.
DROP TRIGGER IF EXISTS control_operation_parent_binding_m3;
CREATE TRIGGER control_operation_parent_binding_m3
BEFORE INSERT ON control_operations
WHEN (NEW.kind='archive_write' AND NEW.parent_operation_id IS NOT NULL AND
      NOT EXISTS (SELECT 1 FROM control_operations p
                  WHERE p.operation_id=NEW.parent_operation_id
                    AND p.kind IN ('migration','restore','recovery')
                    AND p.state NOT IN ('failed','conflict','source_changed'))) OR
     (NEW.kind='migration' AND NEW.parent_operation_id IS NOT NULL AND
      NOT EXISTS (SELECT 1 FROM control_operations p
                  WHERE p.operation_id=NEW.parent_operation_id
                    AND p.kind='migration'
                    AND p.state IN ('failed','source_changed'))) OR
     (NEW.kind NOT IN ('archive_write','migration')
      AND NEW.parent_operation_id IS NOT NULL)
BEGIN SELECT RAISE(ABORT,'invalid archive-write parent'); END;

CREATE TRIGGER IF NOT EXISTS control_operation_initial_hash_m3
BEFORE INSERT ON control_operations
WHEN NEW.kind IN ('recovery','archive_write','observation')
 AND NEW.new_logical_sha256 IS NULL
BEGIN SELECT RAISE(ABORT,'generation owner must prebind new logical hash'); END;

CREATE TRIGGER IF NOT EXISTS control_operation_single_cutover_child_m3
BEFORE INSERT ON control_operations
WHEN NEW.kind='archive_write' AND NEW.parent_operation_id IS NOT NULL
 AND EXISTS (SELECT 1 FROM control_operations p
             WHERE p.operation_id=NEW.parent_operation_id
               AND p.kind='migration')
 AND EXISTS (SELECT 1 FROM control_operations c
             WHERE c.parent_operation_id=NEW.parent_operation_id
               AND c.kind='archive_write'
               AND c.state IN ('prepared','applying','succeeded','resolved','cutover_marked'))
BEGIN SELECT RAISE(ABORT,'migration already has an archive commit child'); END;

CREATE TRIGGER IF NOT EXISTS control_operation_single_generation_owner_m3
BEFORE INSERT ON control_operations
WHEN NEW.kind IN ('restore','archive_write')
 AND EXISTS (SELECT 1 FROM control_operations c
             WHERE c.kind IN ('restore','archive_write')
               AND c.state IN ('prepared','resolving','applying','swapping'))
BEGIN SELECT RAISE(ABORT,'another archive generation owner is active'); END;

CREATE TRIGGER IF NOT EXISTS control_operation_owned_fields_m3
BEFORE UPDATE ON control_operations
WHEN NEW.created_at IS NOT OLD.created_at OR
     (OLD.package_id IS NOT NULL AND NEW.package_id IS NOT OLD.package_id) OR
     (OLD.envelope_sha256 IS NOT NULL AND NEW.envelope_sha256 IS NOT OLD.envelope_sha256) OR
     (OLD.legacy_fence_id IS NOT NULL AND NEW.legacy_fence_id IS NOT OLD.legacy_fence_id) OR
     (OLD.legacy_fence_digest IS NOT NULL AND NEW.legacy_fence_digest IS NOT OLD.legacy_fence_digest) OR
     (OLD.staging_path IS NOT NULL AND NEW.staging_path IS NOT OLD.staging_path) OR
     (OLD.preimage_path IS NOT NULL AND NEW.preimage_path IS NOT OLD.preimage_path)
BEGIN SELECT RAISE(ABORT,'control operation owned field is immutable'); END;

CREATE TRIGGER IF NOT EXISTS control_operation_fence_pair_m3
BEFORE INSERT ON control_operations
WHEN (NEW.legacy_fence_id IS NULL) != (NEW.legacy_fence_digest IS NULL)
BEGIN SELECT RAISE(ABORT,'legacy fence identity and digest must be paired'); END;

CREATE TRIGGER IF NOT EXISTS control_operation_fence_pair_update_m3
BEFORE UPDATE ON control_operations
WHEN (NEW.legacy_fence_id IS NULL) != (NEW.legacy_fence_digest IS NULL)
BEGIN SELECT RAISE(ABORT,'legacy fence identity and digest must be paired'); END;

CREATE TRIGGER IF NOT EXISTS control_operation_fence_initial_m4
BEFORE INSERT ON control_operations
WHEN NEW.legacy_fence_id IS NOT NULL OR NEW.legacy_fence_digest IS NOT NULL
BEGIN SELECT RAISE(ABORT,'migration fence cannot be present at intent creation'); END;

CREATE TRIGGER IF NOT EXISTS control_operation_verified_fence_m4
BEFORE UPDATE OF state ON control_operations
WHEN OLD.kind='migration' AND NEW.state IN ('verified','cutover_marked')
 AND (NEW.legacy_fence_id IS NULL OR NEW.legacy_fence_digest IS NULL)
BEGIN SELECT RAISE(ABORT,'verified migration requires a browser fence'); END;

CREATE TRIGGER IF NOT EXISTS control_operation_owned_field_fill_m4
BEFORE UPDATE ON control_operations
WHEN ((OLD.package_id IS NULL AND NEW.package_id IS NOT NULL) OR
      (OLD.envelope_sha256 IS NULL AND NEW.envelope_sha256 IS NOT NULL) OR
      (OLD.staging_path IS NULL AND NEW.staging_path IS NOT NULL) OR
      (OLD.preimage_path IS NULL AND NEW.preimage_path IS NOT NULL) OR
      (OLD.legacy_fence_id IS NULL AND NEW.legacy_fence_id IS NOT NULL) OR
      (OLD.legacy_fence_digest IS NULL AND NEW.legacy_fence_digest IS NOT NULL))
 AND NOT (OLD.kind='migration' AND OLD.state='imported' AND NEW.state='verified'
          AND OLD.legacy_fence_id IS NULL AND NEW.legacy_fence_id IS NOT NULL
          AND OLD.legacy_fence_digest IS NULL AND NEW.legacy_fence_digest IS NOT NULL
          AND OLD.package_id IS NEW.package_id
          AND OLD.envelope_sha256 IS NEW.envelope_sha256
          AND OLD.staging_path IS NEW.staging_path
          AND OLD.preimage_path IS NEW.preimage_path)
BEGIN SELECT RAISE(ABORT,'control operation owned field fill is outside its edge'); END;

CREATE TRIGGER IF NOT EXISTS control_migration_cutover_child_m3
BEFORE UPDATE OF state ON control_operations
WHEN OLD.kind='migration' AND NEW.state='cutover_marked'
 AND NOT EXISTS (SELECT 1 FROM control_operations c
                 WHERE c.parent_operation_id=OLD.operation_id
                   AND c.kind='archive_write'
                   AND c.state IN ('succeeded','resolved','cutover_marked'))
BEGIN SELECT RAISE(ABORT,'migration cutover has no successful archive child'); END;
"""


class RecoveryJournal:
    """Small external journal that is never swapped with the archive DB."""

    def __init__(self, path: str):
        self.path = os.path.abspath(os.path.expanduser(path))
        self._mutex = threading.RLock()
        self._writer_lock = None
        self._control_file_preexisted = Path(self.path).exists()
        parent_fd, _ = PERSISTENCE._open_secure_parent(self.path, create=True)
        os.close(parent_fd)
        try:
            self._writer_lock = PERSISTENCE._acquire_writer_lock(self.path)
            self._secret = self._load_secret()
            self._initialize_schema()
        except Exception:
            if self._writer_lock is not None:
                self._writer_lock.release()
                self._writer_lock = None
            raise

    def close(self) -> None:
        if self._writer_lock is not None:
            self._writer_lock.release()
            self._writer_lock = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _load_secret(self) -> bytes:
        path = Path(self.path + ".key")
        try:
            value = _read_regular(path, 64)
            if len(value) != 32:
                raise RecoveryError("recovery journal key is invalid")
            return value
        except RecoveryError as exc:
            if path.exists():
                raise exc
            if self._control_file_preexisted:
                # A journal with a missing key is not a fresh journal.  Never
                # mint a replacement key that would make old receipts appear
                # unverifiable but usable after a restart.
                raise RecoveryError("recovery journal key is unavailable") from exc
        value = secrets.token_bytes(32)
        _write_new(path, value)
        return value

    def _connect(self) -> sqlite3.Connection:
        lease = PERSISTENCE._acquire_sqlite_path_lease(self.path)
        conn = None
        try:
            expected = PERSISTENCE._preflight_sqlite_paths(
                self.path, writable=True, create_database=True)
            conn = sqlite3.connect(self.path, timeout=10.0,
                                   isolation_level=None,
                                   factory=PERSISTENCE._PathLockedConnection)
            conn._bind_path_lease(lease)
            conn.row_factory = sqlite3.Row
            PERSISTENCE._postflight_sqlite_paths(
                self.path, expected, writable=True, repair_new_sidecars=False)
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA recursive_triggers=ON")
            conn.execute("PRAGMA synchronous=FULL")
            conn.execute("PRAGMA journal_mode=DELETE")
            return conn
        except Exception:
            if conn is not None:
                conn.close()
            else:
                lease.release()
            raise

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize_schema(self) -> None:
        with self._mutex:
            conn = self._connect()
            try:
                exists = conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table' AND name='control_meta'").fetchone()
                if exists is None:
                    conn.executescript(CONTROL_SCHEMA)
                    conn.executescript(RAW_CONTROL_EXTENSION)
                    conn.executescript(MIGRATION_CONTROL_EXTENSION)
                    conn.executescript(ARCHIVE_WRITE_CONTROL_EXTENSION)
                    conn.executescript(EXTERNAL_REQUEST_CONTROL_EXTENSION)
                    conn.execute("INSERT INTO control_meta VALUES (1,1,?)",
                                 (_millis_now(),))
                    conn.commit()
                else:
                    row = conn.execute(
                        "SELECT schema_version FROM control_meta WHERE singleton_id=1").fetchone()
                    if row is None or int(row[0]) != 1:
                        raise RecoveryError("recovery journal schema is invalid")
                    conn.executescript(RAW_CONTROL_EXTENSION)
                    conn.executescript(MIGRATION_CONTROL_EXTENSION)
                    conn.executescript(ARCHIVE_WRITE_CONTROL_EXTENSION)
                    conn.executescript(EXTERNAL_REQUEST_CONTROL_EXTENSION)
                self._validate_migration_fence_rows(conn)
                self._validate_operation_receipt_kinds(conn)
                self._validate_authority_intent_rows(conn)
                self._validate_authority_intent_event_rows(conn)
            finally:
                conn.close()

    @staticmethod
    def _validate_operation_receipt_kinds(conn: sqlite3.Connection) -> None:
        """Fail closed when a receipted operation's commit kind is wrong.

        The triggers pin this going forward, but a journal written before them
        — or one edited underneath us — can still hold an `observation` whose
        commit slot is an `archive_commit`.  That row is a signed claim that
        archive bytes were mutated during an operation the contract defines as
        having no archive byte commit, so it has to stop startup rather than be
        served.  Checked here for every kind, not just observation: an archive
        owner holding a `control_commit` is the same lie inverted.
        """
        rows = conn.execute(
            "SELECT operation_id,kind,state,archive_commit_receipt,control_ack_receipt "
            "FROM control_operations "
            "WHERE archive_commit_receipt IS NOT NULL "
            "   OR control_ack_receipt IS NOT NULL "
            "ORDER BY operation_id").fetchall()
        for row in rows:
            expected_commit = ("control_commit" if row["kind"] == "observation"
                               else "archive_commit")
            commit = None
            if row["archive_commit_receipt"] is not None:
                commit = conn.execute(
                    "SELECT receipt_kind,rowid FROM control_operation_receipts "
                    "WHERE receipt_id=? AND operation_id=?",
                    (row["archive_commit_receipt"], row["operation_id"])).fetchone()
                if commit is None or commit["receipt_kind"] != expected_commit:
                    raise RecoveryError("control operation commit receipt kind is invalid")
            ack = None
            if row["control_ack_receipt"] is not None:
                ack = conn.execute(
                    "SELECT receipt_kind,rowid FROM control_operation_receipts "
                    "WHERE receipt_id=? AND operation_id=?",
                    (row["control_ack_receipt"], row["operation_id"])).fetchone()
                if ack is None or ack["receipt_kind"] != "control_ack":
                    raise RecoveryError("control operation ack receipt kind is invalid")
            if commit is not None and ack is not None and commit["rowid"] >= ack["rowid"]:
                raise RecoveryError("control operation receipt order is invalid")
            # The kind that must not appear at all is as load-bearing as the one
            # that must: an observation carrying a stray `archive_commit` row
            # still asserts a mutation even when its commit slot points
            # elsewhere.
            stray = conn.execute(
                "SELECT 1 FROM control_operation_receipts "
                "WHERE operation_id=? AND receipt_kind=? LIMIT 1",
                (row["operation_id"],
                 "archive_commit" if row["kind"] == "observation"
                 else "control_commit")).fetchone()
            if stray is not None:
                raise RecoveryError("control operation commit receipt kind is invalid")

    @staticmethod
    def _validate_migration_fence_rows(conn: sqlite3.Connection) -> None:
        """Fail closed on historical fence/state rows before serving requests."""
        # A fence is bound at `verified` and is immutable afterwards, so the
        # states that may carry one are the ones reachable from `verified`:
        # `cutover_marked` when the cutover completed, and `failed` when it was
        # abandoned and the parent was released for retry.  Dropping the fence
        # on release is not an option — the columns are immutable by trigger,
        # and the spent fence is the evidence that this attempt is over.
        fenced_states = "('verified','cutover_marked','failed')"
        rows = conn.execute(
            "SELECT * FROM control_operations WHERE kind='migration' "
            "AND state IN ('verified','cutover_marked')").fetchall()
        for row in rows:
            if row["legacy_fence_id"] is None or row["legacy_fence_digest"] is None:
                raise RecoveryError("verified migration fence is missing")
            try:
                _fence_page_instance_id(row["legacy_fence_id"])
            except RecoveryError as exc:
                raise RecoveryError("migration fence history is invalid") from exc
            if not _hex64(row["legacy_fence_digest"]):
                raise RecoveryError("migration fence history is invalid")
        invalid = conn.execute(
            "SELECT 1 FROM control_operations "
            "WHERE (legacy_fence_id IS NULL) != (legacy_fence_digest IS NULL) "
            "OR (legacy_fence_id IS NOT NULL AND kind!='migration') "
            "OR (legacy_fence_id IS NOT NULL AND state NOT IN "
            + fenced_states + ") LIMIT 1").fetchone()
        if invalid is not None:
            raise RecoveryError("migration fence history is invalid")

    def _validate_authority_intent_rows(self, conn: sqlite3.Connection) -> None:
        """Fail closed on durable pre-cutover authority commitments."""
        rows = conn.execute(
            "SELECT * FROM control_authority_intents ORDER BY intent_id"
        ).fetchall()
        for row in rows:
            try:
                body = json.loads(row["body_json"])
                receipt = _authority_intent_receipt(body, self._secret)
            except (TypeError, ValueError, RecoveryError) as exc:
                raise RecoveryError("authority intent history is invalid") from exc
            if (row["intent_id"] != receipt["intent_id"]
                    or row["intent_receipt_sha256"]
                    != receipt["intent_receipt_sha256"]
                    or row["intent_receipt_mac"] != receipt["intent_receipt_mac"]
                    or row["body_json"] != receipt["body_json"]):
                raise RecoveryError("authority intent history is invalid")
            body = receipt["body"]
            if (row["operation_id"] != body["operation_id"]
                    or row["operation_kind"] != body["operation_kind"]
                    or row["expected_authority_status"]
                    != body["expected_authority_status"]
                    or row["target_authority_status"]
                    != body["target_authority_status"]
                    or row["expected_generation"] != body["expected_generation"]
                    or row["new_generation_id"] != body["new_generation_id"]
                    or row["old_logical_sha256"] != body["old_logical_sha256"]
                    or row["fresh_envelope_sha256"]
                    != body["fresh_envelope_sha256"]
                    or row["raw_key_sha256_json"]
                    != _canonical(body["raw_key_sha256"]).decode("utf-8")
                    or row["projection_sha256"] != body["projection_sha256"]
                    or int(row["target_count"]) != body["target_count"]
                    or row["target_hash"] != body["target_hash"]
                    or row["legacy_fence_id"] != body["legacy_fence_id"]
                    or row["legacy_fence_digest"]
                    != body["legacy_fence_digest"]
                    or row["page_instance_id"] != body["page_instance_id"]
                    or row["archive_child_id"] != body["archive_child_id"]
                    or derive_archive_child_id(row["operation_id"])
                    != row["archive_child_id"]):
                raise RecoveryError("authority intent history is invalid")
            operation = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (row["operation_id"],)).fetchone()
            # `failed` joins the set because a cutover whose bytes never landed
            # releases its parent so the migration can be retried (see
            # _release_abandoned_cutover_parents).  The intent stays durable and
            # still has to be well-formed; what changes is that it is now the
            # record of an abandoned attempt.  It cannot be a way to escape the
            # binding obligation: an intent whose event exists is checked by the
            # event-rooted sweep below, and a parent whose cutover succeeded is
            # `cutover_marked`, which has no transition out.
            if (operation is None or operation["kind"] != "migration"
                    or operation["state"] not in {"verified", "cutover_marked",
                                                  "failed"}
                    or operation["expected_generation"]
                    != row["expected_generation"]
                    or operation["old_logical_sha256"]
                    != row["old_logical_sha256"]
                    or operation["envelope_sha256"]
                    != row["fresh_envelope_sha256"]
                    or operation["legacy_fence_id"] != row["legacy_fence_id"]
                    or operation["legacy_fence_digest"]
                    != row["legacy_fence_digest"]):
                raise RecoveryError("authority intent history is invalid")

    def _validate_authority_intent_event_rows(self, conn: sqlite3.Connection) -> None:
        """Fail closed on intent-to-child authority-event bindings."""
        rows = conn.execute(
            "SELECT * FROM control_authority_intent_events ORDER BY event_id"
        ).fetchall()
        for row in rows:
            event = conn.execute(
                "SELECT * FROM control_authority_events WHERE event_id=?",
                (row["event_id"],)).fetchone()
            intent = conn.execute(
                "SELECT * FROM control_authority_intents WHERE intent_id=?",
                (row["intent_id"],)).fetchone()
            child = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (row["operation_id"],)).fetchone()
            if event is None or intent is None or child is None:
                raise RecoveryError("authority intent event history is invalid")
            try:
                intent_body = json.loads(intent["body_json"])
                payload = json.loads(child["receipt_json"] or "{}")
            except (TypeError, ValueError) as exc:
                raise RecoveryError(
                    "authority intent event history is invalid") from exc
            ack = conn.execute(
                "SELECT receipt_sha256,receipt_mac FROM control_operation_receipts "
                "WHERE receipt_id=? AND operation_id=? AND receipt_kind='control_ack'",
                (child["control_ack_receipt"], child["operation_id"])).fetchone()
            if (event["operation_id"] != child["operation_id"]
                    or event["operation_kind"] != "archive_write"
                    or event["from_status"] != intent_body["expected_authority_status"]
                    or event["to_status"] != intent_body["target_authority_status"]
                    or event["expected_generation"] != intent_body["expected_generation"]
                    or event["new_generation_id"] != intent_body["new_generation_id"]
                    or event["envelope_sha256"] != intent_body["fresh_envelope_sha256"]
                    or int(event["target_count"]) != int(intent_body["target_count"])
                    or event["target_hash"] != intent_body["target_hash"]
                    or event["receipt_sha256"] != (None if ack is None else ack["receipt_sha256"])
                    or event["receipt_mac"] != (None if ack is None else ack["receipt_mac"])
                    or child["kind"] != "archive_write"
                    or child["state"] != "succeeded"
                    or child["parent_operation_id"] != intent_body["operation_id"]
                    or child["operation_id"] != intent_body["archive_child_id"]
                    or child["expected_generation"] != intent_body["expected_generation"]
                    or child["new_generation_id"] != intent_body["new_generation_id"]
                    or child["old_logical_sha256"] != intent_body["old_logical_sha256"]
                    or child["envelope_sha256"] != intent_body["fresh_envelope_sha256"]
                    or row["intent_id"] != intent["intent_id"]
                    or row["operation_id"] != child["operation_id"]
                    or row["intent_receipt_sha256"]
                    != intent["intent_receipt_sha256"]
                    or row["intent_receipt_mac"] != intent["intent_receipt_mac"]
                    or payload.get("authority_intent_id") != intent["intent_id"]
                    or payload.get("authority_intent_receipt_sha256")
                    != intent["intent_receipt_sha256"]
                    or payload.get("authority_intent_receipt_mac")
                    != intent["intent_receipt_mac"]):
                raise RecoveryError("authority intent event history is invalid")

        # Rooted at the authority EVENT, not at a marker inside the child.
        # The previous version started from receipt_json.authority_intent_id,
        # which meant deleting that marker also deleted the obligation: an
        # event could then survive a restart with zero intent and zero binding.
        # A cutover event is identified by what it structurally is — an
        # archive_write event reaching sqlite_preferred whose child hangs off a
        # migration parent — and that cannot be edited away without breaking
        # the parent link the rest of the journal already validates.
        cutover_events = conn.execute(
            "SELECT e.event_id, e.operation_id FROM control_authority_events e "
            "JOIN control_operations c ON c.operation_id = e.operation_id "
            "JOIN control_operations p ON p.operation_id = c.parent_operation_id "
            "WHERE e.operation_kind='archive_write' "
            "  AND e.to_status='sqlite_preferred' "
            "  AND c.kind='archive_write' AND p.kind='migration'").fetchall()
        for event in cutover_events:
            bindings = conn.execute(
                "SELECT intent_id FROM control_authority_intent_events "
                "WHERE event_id=?", (event["event_id"],)).fetchall()
            if len(bindings) != 1:
                raise RecoveryError(
                    "authority intent event history is incomplete")
            intent = conn.execute(
                "SELECT intent_id FROM control_authority_intents WHERE intent_id=?",
                (bindings[0]["intent_id"],)).fetchone()
            if intent is None:
                raise RecoveryError(
                    "authority intent event history is incomplete")

    def ensure_bootstrap(self, *, logical_sha256: str, state: str) -> dict:
        if state not in ("present", "absent") or not _hex64(logical_sha256):
            raise RecoveryError("invalid recovery bootstrap identity")
        generation_id = ("gen-absent-0" if state == "absent"
                         else "gen-bootstrap-" + logical_sha256)
        with self._mutex, self._transaction() as conn:
            row = conn.execute("SELECT * FROM control_generation WHERE singleton_id=1").fetchone()
            if row is None:
                now = _millis_now()
                conn.execute("INSERT INTO control_generation VALUES (1,?,?,?,?)",
                             (generation_id, logical_sha256, state, now,))
                conn.execute(
                    "INSERT INTO control_authority VALUES "
                    "(1,'legacy_authoritative',NULL,NULL,NULL,0,?,NULL,?)",
                    (EMPTY_TARGET_HASH, now))
            else:
                if (row["logical_sha256"] != logical_sha256
                        or row["state"] != state):
                    raise ManualRecoveryRequired("control/archive generation mismatch")
            return self._snapshot_from_connection(conn)

    @staticmethod
    def _snapshot_from_connection(conn: sqlite3.Connection) -> dict:
        generation_row = conn.execute(
            "SELECT * FROM control_generation WHERE singleton_id=1").fetchone()
        authority_row = conn.execute(
            "SELECT * FROM control_authority WHERE singleton_id=1").fetchone()
        generation = dict(generation_row) if generation_row is not None else None
        authority = dict(authority_row) if authority_row is not None else None
        return {"generation": generation, "authority": authority}

    def snapshot(self) -> dict:
        with self._mutex:
            conn = self._connect()
            try:
                return self._snapshot_from_connection(conn)
            finally:
                conn.close()

    def get_operation(self, operation_id: str) -> Optional[dict]:
        with self._mutex:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM control_operations WHERE operation_id=?",
                    (operation_id,)).fetchone()
                return dict(row) if row is not None else None
            finally:
                conn.close()

    def find_operation(self, idempotency_key: str) -> Optional[dict]:
        with self._mutex:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM control_operations WHERE idempotency_key=?",
                    (idempotency_key,)).fetchone()
                return dict(row) if row is not None else None
            finally:
                conn.close()

    def active_operations(self) -> list[dict]:
        """Return non-terminal operation intents in deterministic order."""
        with self._mutex:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM control_operations WHERE state IN "
                    "('prepared','resolving','applying','swapping') "
                    "ORDER BY created_at, operation_id").fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def list_operations(self, *, kind: Optional[str] = None) -> list[dict]:
        """Return journal operations for a read-only status seam."""
        with self._mutex:
            conn = self._connect()
            try:
                if kind is None:
                    rows = conn.execute(
                        "SELECT * FROM control_operations "
                        "ORDER BY created_at, operation_id").fetchall()
                else:
                    rows = conn.execute(
                        "SELECT * FROM control_operations WHERE kind=? "
                        "ORDER BY created_at, operation_id", (kind,)).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def failure_receipt(self, operation_id: str) -> Optional[dict]:
        with self._mutex:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM control_failure_receipts "
                    "WHERE operation_id=? ORDER BY created_at DESC LIMIT 1",
                    (operation_id,)).fetchone()
                return dict(row) if row is not None else None
            finally:
                conn.close()

    def authority_events_for_operation(self, operation_id: str) -> list[dict]:
        with self._mutex:
            conn = self._connect()
            try:
                rows = conn.execute(
                    "SELECT * FROM control_authority_events WHERE operation_id=? "
                    "ORDER BY created_at, event_id", (operation_id,)).fetchall()
                return [dict(row) for row in rows]
            finally:
                conn.close()

    def get_package(self, backup_id: str) -> Optional[dict]:
        with self._mutex:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM control_packages WHERE backup_id=?",
                    (backup_id,)).fetchone()
                return dict(row) if row is not None else None
            finally:
                conn.close()

    def get_raw_restore(self, operation_id: str) -> Optional[dict]:
        with self._mutex:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM control_raw_restore WHERE operation_id=?",
                    (operation_id,)).fetchone()
                return dict(row) if row is not None else None
            finally:
                conn.close()

    def get_raw_restore_outcome(self, operation_id: str) -> Optional[dict]:
        with self._mutex:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM control_raw_restore_outcomes "
                    "WHERE operation_id=?", (operation_id,)).fetchone()
                return dict(row) if row is not None else None
            finally:
                conn.close()

    def create_raw_restore(self, *, operation_id: str,
                           target_envelope: dict, preimage_envelope: dict,
                           expected_generation: str) -> dict:
        target = MIGRATION.validate_envelope(target_envelope)
        preimage = MIGRATION.validate_envelope(preimage_envelope)
        target_json = _canonical(target).decode("utf-8")
        preimage_json = _canonical(preimage).decode("utf-8")
        target_sha = MIGRATION.envelope_sha256(target)
        preimage_sha = MIGRATION.envelope_sha256(preimage)
        key_order_json = _canonical(list(MIGRATION.ALLOWED_KEYS)).decode("utf-8")
        now = _millis_now()
        with self._mutex, self._transaction() as conn:
            try:
                conn.execute(
                    "INSERT INTO control_raw_restore "
                    "(operation_id,target_envelope_json,target_sha256,"
                    "preimage_envelope_json,preimage_sha256,key_order_json,"
                    "expected_generation,phase,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (operation_id, target_json, target_sha, preimage_json,
                     preimage_sha, key_order_json, expected_generation,
                     "prepared", now, now))
            except sqlite3.IntegrityError as exc:
                raise RecoveryError("raw restore identity conflict") from exc
            return dict(conn.execute(
                "SELECT * FROM control_raw_restore WHERE operation_id=?",
                (operation_id,)).fetchone())

    @staticmethod
    def _raw_receipt(*, operation_id: str, outcome_kind: str,
                     expected_generation: str, target_sha256: str,
                     preimage_sha256: str, readback_sha256: str,
                     key_order: list[str], secret: bytes) -> dict:
        body = {
            "format": "fire-raw-restore-outcome-v1",
            "operation_id": operation_id, "outcome_kind": outcome_kind,
            "expected_generation": expected_generation,
            "target_sha256": target_sha256, "preimage_sha256": preimage_sha256,
            "readback_sha256": readback_sha256, "key_order": key_order,
        }
        body_json = _canonical(body)
        return {"body": body, "body_json": body_json.decode("utf-8"),
                "receipt_sha256": _sha256(body_json),
                "receipt_mac": hmac.new(secret, body_json, hashlib.sha256).hexdigest()}

    def complete_raw_restore(self, operation_id: str, *, outcome_kind: str,
                             readback_envelope: dict) -> dict:
        if outcome_kind not in {"succeeded", "rolled_back"}:
            raise RecoveryError("invalid raw restore terminal outcome")
        readback = MIGRATION.validate_envelope(readback_envelope)
        readback_json = _canonical(readback).decode("utf-8")
        readback_sha = MIGRATION.envelope_sha256(readback)
        with self._mutex, self._transaction() as conn:
            op = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            raw = conn.execute(
                "SELECT * FROM control_raw_restore WHERE operation_id=?",
                (operation_id,)).fetchone()
            if op is None or raw is None or op["kind"] != "raw_restore":
                raise RecoveryError("raw restore operation is unknown")
            existing = conn.execute(
                "SELECT * FROM control_raw_restore_outcomes WHERE operation_id=?",
                (operation_id,)).fetchone()
            if existing is not None:
                if existing["readback_sha256"] != readback_sha:
                    raise RecoveryConflict("raw restore finalize readback changed")
                return dict(existing)
            if op["state"] == "prepared":
                conn.execute(
                    "UPDATE control_operations SET state='applying',updated_at=? "
                    "WHERE operation_id=?", (_millis_now(), operation_id))
                conn.execute(
                    "UPDATE control_raw_restore SET phase='applying',updated_at=? "
                    "WHERE operation_id=?", (_millis_now(), operation_id))
            elif op["state"] != "applying":
                raise RecoveryConflict("raw restore operation is not finalizable")
            key_order = json.loads(raw["key_order_json"])
            receipt = self._raw_receipt(
                operation_id=operation_id, outcome_kind=outcome_kind,
                expected_generation=raw["expected_generation"],
                target_sha256=raw["target_sha256"],
                preimage_sha256=raw["preimage_sha256"],
                readback_sha256=readback_sha, key_order=key_order,
                secret=self._secret)
            outcome_id = "rawrcpt_" + uuid.uuid4().hex
            conn.execute(
                "INSERT INTO control_raw_restore_outcomes "
                "(outcome_id,operation_id,outcome_kind,expected_generation,"
                "target_sha256,preimage_sha256,readback_sha256,receipt_sha256,"
                "receipt_mac,body_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (outcome_id, operation_id, outcome_kind,
                 raw["expected_generation"], raw["target_sha256"],
                 raw["preimage_sha256"], readback_sha, receipt["receipt_sha256"],
                 receipt["receipt_mac"], receipt["body_json"], _millis_now()))
            terminal = "succeeded" if outcome_kind == "succeeded" else "rolled_back"
            conn.execute(
                "UPDATE control_raw_restore SET phase=?,readback_envelope_json=?,"
                "readback_sha256=?,outcome_id=?,updated_at=? WHERE operation_id=?",
                (terminal, readback_json, readback_sha, outcome_id,
                 _millis_now(), operation_id))
            conn.execute(
                "UPDATE control_operations SET state=?,updated_at=? WHERE operation_id=?",
                (terminal, _millis_now(), operation_id))
            return {"outcome_id": outcome_id, "operation_id": operation_id,
                    "outcome_kind": outcome_kind,
                    "readback_sha256": readback_sha,
                    "receipt_sha256": receipt["receipt_sha256"],
                    "receipt_mac": receipt["receipt_mac"]}

    def raw_manual_latch(self, operation_id: str, *,
                         readback_envelope: Optional[dict],
                         reason: str) -> dict:
        readback = None
        readback_json = None
        readback_sha = None
        if readback_envelope is not None:
            readback = MIGRATION.validate_envelope(readback_envelope)
            readback_json = _canonical(readback).decode("utf-8")
            readback_sha = MIGRATION.envelope_sha256(readback)
        with self._mutex, self._transaction() as conn:
            op = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            raw = conn.execute(
                "SELECT * FROM control_raw_restore WHERE operation_id=?",
                (operation_id,)).fetchone()
            if op is None or raw is None or op["kind"] != "raw_restore":
                raise RecoveryError("raw restore operation is unknown")
            if op["state"] == "manual_recovery_required":
                return {"operation_id": operation_id,
                        "state": "manual_recovery_required"}
            if op["state"] not in {"prepared", "applying"}:
                raise RecoveryConflict("raw restore operation is already terminal")
            if op["state"] == "prepared":
                conn.execute(
                    "UPDATE control_operations SET state='applying',updated_at=? "
                    "WHERE operation_id=?", (_millis_now(), operation_id))
            body = {
                "operation_id": operation_id, "failure_kind": "manual_latch",
                "failure_code": "artifact_mismatch", "reason": str(reason),
                "expected_generation": op["expected_generation"],
                "observed_generation": op["expected_generation"],
                "old_logical_sha256": op["old_logical_sha256"],
                "staged_db_sha256": None, "preimage_sha256": None,
                "package_id": op["package_id"],
                "preserved_artifacts": {"target_sha256": raw["target_sha256"],
                                         "preimage_sha256": raw["preimage_sha256"],
                                         "key_order_json": raw["key_order_json"],
                                         "readback_sha256": readback_sha},
            }
            body_json = _canonical(body)
            receipt_sha = _sha256(body_json)
            receipt_mac = hmac.new(self._secret, body_json, hashlib.sha256).hexdigest()
            conn.execute(
                "UPDATE control_operations SET state='manual_recovery_required',"
                "updated_at=? WHERE operation_id=?", (_millis_now(), operation_id))
            conn.execute(
                "INSERT INTO control_failure_receipts "
                "(receipt_id,operation_id,failure_kind,failure_code,expected_generation,"
                "observed_generation,old_logical_sha256,staged_db_sha256,preimage_sha256,"
                "package_id,preserved_artifact_json,receipt_sha256,receipt_mac,body_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("fail_" + uuid.uuid4().hex, operation_id, "manual_latch",
                 "artifact_mismatch", op["expected_generation"],
                 op["expected_generation"], op["old_logical_sha256"], None, None,
                 op["package_id"], _canonical(body["preserved_artifacts"]).decode("utf-8"),
                 receipt_sha, receipt_mac, body_json.decode("utf-8"), _millis_now()))
            outcome = self._raw_receipt(
                operation_id=operation_id,
                outcome_kind="manual_recovery_required",
                expected_generation=raw["expected_generation"],
                target_sha256=raw["target_sha256"],
                preimage_sha256=raw["preimage_sha256"],
                readback_sha256=readback_sha or "0" * 64,
                key_order=json.loads(raw["key_order_json"]), secret=self._secret)
            outcome_id = "rawrcpt_" + uuid.uuid4().hex
            conn.execute(
                "INSERT INTO control_raw_restore_outcomes "
                "(outcome_id,operation_id,outcome_kind,expected_generation,"
                "target_sha256,preimage_sha256,readback_sha256,receipt_sha256,"
                "receipt_mac,body_json,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (outcome_id, operation_id, "manual_recovery_required",
                 raw["expected_generation"], raw["target_sha256"],
                 raw["preimage_sha256"], readback_sha or "0" * 64,
                 outcome["receipt_sha256"], outcome["receipt_mac"],
                 outcome["body_json"], _millis_now()))
            conn.execute(
                "UPDATE control_raw_restore SET phase='manual_recovery_required',"
                "readback_envelope_json=?,readback_sha256=?,outcome_id=?,updated_at=? "
                "WHERE operation_id=?",
                (readback_json, readback_sha, outcome_id, _millis_now(), operation_id))
            return {"operation_id": operation_id,
                    "state": "manual_recovery_required",
                    "receipt_sha256": receipt_sha, "receipt_mac": receipt_mac}

    def raw_manual_active(self) -> bool:
        with self._mutex:
            conn = self._connect()
            try:
                return conn.execute(
                    "SELECT 1 FROM control_operations WHERE kind='raw_restore' "
                    "AND state='manual_recovery_required' LIMIT 1").fetchone() is not None
            finally:
                conn.close()

    def create_operation(self, *, operation_id: str, kind: str, state: str,
                         idempotency_key: str, request_fingerprint: str,
                         expected_generation: str, old_logical_sha256: str,
                         new_generation_id: Optional[str] = None,
                         new_logical_sha256: Optional[str] = None,
                         staged_db_sha256: Optional[str] = None,
                         package_id: Optional[str] = None,
                         envelope_sha256: Optional[str] = None,
                         staging_path: Optional[str] = None,
                         preimage_path: Optional[str] = None,
                         parent_operation_id: Optional[str] = None,
                         receipt_json: Optional[str] = None) -> dict:
        if receipt_json is None:
            receipt_json = "{}"
        if not isinstance(receipt_json, str):
            raise RecoveryError("control operation receipt must be text")
        now = _millis_now()
        values = (operation_id, kind, state, idempotency_key,
                  request_fingerprint, parent_operation_id, expected_generation,
                  new_generation_id, old_logical_sha256, new_logical_sha256,
                  staged_db_sha256,
                  None, None, package_id, envelope_sha256, None, None, staging_path,
                  preimage_path, receipt_json, now, now)
        with self._mutex, self._transaction() as conn:
            try:
                conn.execute(
                    "INSERT INTO control_operations "
                    "(operation_id,kind,state,idempotency_key,request_fingerprint,"
                    "parent_operation_id,expected_generation,new_generation_id,"
                    "old_logical_sha256,new_logical_sha256,staged_db_sha256,"
                    "archive_commit_receipt,control_ack_receipt,package_id,"
                    "envelope_sha256,legacy_fence_id,legacy_fence_digest,staging_path,"
                    "preimage_path,receipt_json,created_at,updated_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", values)
            except sqlite3.IntegrityError as exc:
                raise RecoveryError("control operation identity conflict") from exc
            return dict(conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone())

    def update_operation(self, operation_id: str, **fields: Any) -> dict:
        allowed = {"state", "staged_db_sha256", "new_logical_sha256",
                   "archive_commit_receipt", "control_ack_receipt"}
        if not fields or not set(fields) <= allowed:
            raise RecoveryError("invalid control operation update")
        fields = dict(fields)
        fields["updated_at"] = _millis_now()
        assignments = ", ".join(key + "=?" for key in fields)
        with self._mutex, self._transaction() as conn:
            try:
                conn.execute("UPDATE control_operations SET " + assignments
                             + " WHERE operation_id=?",
                             tuple(fields.values()) + (operation_id,))
            except sqlite3.IntegrityError as exc:
                raise RecoveryError("control operation update rejected") from exc
            row = conn.execute("SELECT * FROM control_operations WHERE operation_id=?",
                               (operation_id,)).fetchone()
            if row is None:
                raise RecoveryError("unknown control operation")
        return dict(row)

    def _migration_fence_body(self, operation: sqlite3.Row | dict, *,
                               fence_id: str, page_instance_id: str,
                               expires_at_ms: int,
                               fence_context: dict) -> bytes:
        context = _normalize_fence_context(fence_context)
        body = {
            "format": "fire-migration-fence-v1",
            "operation_id": operation["operation_id"],
            "expected_generation": operation["expected_generation"],
            "envelope_sha256": operation["envelope_sha256"],
            "fence_id": fence_id,
            "page_instance_id": page_instance_id,
            "expires_at_ms": expires_at_ms,
            "context": context,
        }
        return _canonical(body)

    def bind_migration_fence(self, operation_id: str, *,
                             page_instance_id: str,
                             fence_context: dict,
                             ttl_ms: int = MIGRATION_FENCE_TTL_MS) -> dict:
        """Fill the one server-owned migration fence on imported->verified.

        The page identity and expiry are authenticated by the journal key but
        are not stored as mutable operation columns.  The expiry is carried in
        the opaque fence id so a restarted process can enforce it without a
        second mutable fence table.  A successful verify advances the external
        operation and fills both fence columns in one journal transaction.
        """
        _validate_page_instance_id(page_instance_id)
        fence_context = _normalize_fence_context(fence_context)
        if (type(ttl_ms) is not int or ttl_ms <= 0
                or ttl_ms > MIGRATION_FENCE_TTL_MS):
            raise RecoveryError("migration fence lifetime is invalid")
        now_ms = _epoch_millis()
        expires_at_ms = now_ms + ttl_ms
        page_hex = page_instance_id.encode("utf-8").hex()
        fence_id = f"fence_{expires_at_ms}_{uuid.uuid4().hex}_{page_hex}"
        with self._mutex, self._transaction() as conn:
            operation = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            if operation is None or operation["kind"] != "migration":
                raise RecoveryError("migration operation is unknown")
            if operation["state"] == "verified":
                if (operation["legacy_fence_id"] is None
                        or operation["legacy_fence_digest"] is None):
                    raise RecoveryError("verified migration fence is missing")
                return dict(operation)
            if operation["state"] != "imported":
                raise RecoveryConflict(
                    "migration operation is not ready for fence binding")
            if (operation["legacy_fence_id"] is not None
                    or operation["legacy_fence_digest"] is not None):
                raise RecoveryConflict("migration fence is already bound")
            body_json = self._migration_fence_body(
                operation, fence_id=fence_id,
                page_instance_id=page_instance_id,
                expires_at_ms=expires_at_ms,
                fence_context=fence_context)
            digest = hmac.new(
                self._secret, body_json, hashlib.sha256).hexdigest()
            try:
                conn.execute(
                    "UPDATE control_operations SET state='verified',"
                    "legacy_fence_id=?,legacy_fence_digest=?,updated_at=? "
                    "WHERE operation_id=?",
                    (fence_id, digest, _millis_now(), operation_id))
            except sqlite3.IntegrityError as exc:
                raise RecoveryError("migration fence binding rejected") from exc
            return dict(conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone())

    def validate_migration_fence(self, operation_id: str, *,
                                 fence_id: str, fence_digest: str,
                                 page_instance_id: str,
                                 fence_context: dict) -> dict:
        """Validate a still-live, page-bound migration fence."""
        _validate_page_instance_id(page_instance_id)
        fence_context = _normalize_fence_context(fence_context)
        expires_at_ms = _fence_expiry_ms(fence_id)
        if _epoch_millis() > expires_at_ms:
            raise RecoveryConflict("migration fence has expired")
        if _fence_page_instance_id(fence_id) != page_instance_id:
            raise RecoveryConflict("migration fence is not bound to page")
        if not _hex64(fence_digest):
            raise RecoveryConflict("migration fence digest is invalid")
        with self._mutex:
            operation = self.get_operation(operation_id)
            if operation is None or operation["kind"] != "migration":
                raise RecoveryError("migration operation is unknown")
            if operation["state"] != "verified":
                raise RecoveryConflict("migration fence is not single-use eligible")
            if (operation["legacy_fence_id"] != fence_id
                    or operation["legacy_fence_digest"] != fence_digest):
                raise RecoveryConflict("migration fence does not match operation")
            body_json = self._migration_fence_body(
                operation, fence_id=fence_id,
                page_instance_id=page_instance_id,
                expires_at_ms=expires_at_ms,
                fence_context=fence_context)
            expected = hmac.new(
                self._secret, body_json, hashlib.sha256).hexdigest()
            if not hmac.compare_digest(expected, fence_digest):
                raise RecoveryConflict("migration fence is not bound to page")
            return {**operation, "fence_expires_at_ms": expires_at_ms}

    @staticmethod
    def _public_authority_intent(row: sqlite3.Row | dict) -> dict:
        try:
            body = json.loads(row["body_json"])
        except (TypeError, ValueError) as exc:
            raise RecoveryError("authority intent body is invalid") from exc
        return {
            "format": AUTHORITY_INTENT_FORMAT,
            "intent_id": row["intent_id"],
            "operation_id": row["operation_id"],
            "archive_child_id": row["archive_child_id"],
            "intent_receipt_sha256": row["intent_receipt_sha256"],
            "intent_receipt_mac": row["intent_receipt_mac"],
            "body": body,
            "server_preflight": "passed",
            "live_apply_allowed": False,
        }

    def get_authority_intent(self, intent_id: str) -> Optional[dict]:
        if not isinstance(intent_id, str) or not intent_id.startswith("aint_"):
            raise RecoveryError("authority intent id is invalid")
        with self._mutex:
            conn = self._connect()
            try:
                self._validate_authority_intent_rows(conn)
                row = conn.execute(
                    "SELECT * FROM control_authority_intents WHERE intent_id=?",
                    (intent_id,)).fetchone()
                return (None if row is None
                        else self._public_authority_intent(row))
            finally:
                conn.close()

    @staticmethod
    def external_object_key(request_kind: str, external_request_id: str) -> str:
        """The stable identity of one external request, epoch-free by design.

        Object ids are derived from this rather than from the internal CAS key,
        so a duplicate external request resolves to the object it already
        created instead of naming a new one.  That makes "no twin" structural:
        even if the ledger check were somehow skipped, the second create would
        collide with the first object rather than sit beside it.
        """
        if (not isinstance(request_kind, str) or not request_kind
                or not isinstance(external_request_id, str)
                or not external_request_id):
            raise RecoveryError("external request identity is invalid")
        return _sha256_json({"format": "fire-external-request-v1",
                             "request_kind": request_kind,
                             "external_request_id": external_request_id})

    def find_external_request(self, request_kind: str,
                              external_request_id: str) -> Optional[dict]:
        """Whether this caller has already spent this request id here."""
        self.external_object_key(request_kind, external_request_id)
        with self._mutex:
            conn = self._connect()
            try:
                row = conn.execute(
                    "SELECT * FROM control_external_requests "
                    "WHERE request_kind=? AND external_request_id=?",
                    (request_kind, external_request_id)).fetchone()
                return None if row is None else dict(row)
            finally:
                conn.close()

    def _insert_external_request(self, conn: sqlite3.Connection, *,
                                 request_kind: str, external_request_id: str,
                                 body_fingerprint: str,
                                 observed_generation: str,
                                 observed_authority_receipt: Optional[str],
                                 object_id: Optional[str]) -> dict:
        """Spend an external request id inside the caller's transaction.

        Taking the connection rather than opening one is the whole point: the
        only safe moment to record that a request has been spent is the same
        atomic step that makes its effect durable.  See
        `complete_archive_write_operation`.
        """
        object_key = self.external_object_key(request_kind, external_request_id)
        if not _hex64(body_fingerprint):
            raise RecoveryError("external request fingerprint is invalid")
        existing = conn.execute(
            "SELECT * FROM control_external_requests "
            "WHERE request_kind=? AND external_request_id=?",
            (request_kind, external_request_id)).fetchone()
        if existing is not None:
            # Reached only by a replay of the completion itself — a second
            # `complete_archive_write_operation` for the same child, or startup
            # reconciliation finishing what the online path already finished.
            # The same row for the same request is the expected outcome; a
            # *different* one would mean two actions share an identity.
            if (existing["body_fingerprint"] != body_fingerprint
                    or existing["object_key"] != object_key
                    or existing["object_id"] != object_id):
                raise ManualRecoveryRequired(
                    "external request record disagrees with the operation that "
                    "claims it")
            return dict(existing)
        try:
            conn.execute(
                "INSERT INTO control_external_requests "
                "(request_kind,external_request_id,body_fingerprint,"
                "object_key,object_id,observed_generation,"
                "observed_authority_receipt,created_at) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (request_kind, external_request_id, body_fingerprint,
                 object_key, object_id, observed_generation,
                 observed_authority_receipt, _millis_now()))
        except sqlite3.IntegrityError as exc:
            raise RecoveryConflict(
                "external request id has already been used") from exc
        return dict(conn.execute(
            "SELECT * FROM control_external_requests "
            "WHERE request_kind=? AND external_request_id=?",
            (request_kind, external_request_id)).fetchone())

    def _spend_operation_external_request(self, conn: sqlite3.Connection,
                                          operation: sqlite3.Row | dict) -> None:
        """Record the external request this operation was prepared for, if any."""
        try:
            payload = json.loads(operation["receipt_json"] or "{}")
        except (TypeError, ValueError) as exc:
            raise RecoveryError(
                "archive-write receipt payload is unreadable") from exc
        if not isinstance(payload, dict):
            raise RecoveryError("archive-write receipt payload is unreadable")
        present = [key for key in _EXTERNAL_REQUEST_PAYLOAD_KEYS
                   if payload.get(key) is not None]
        if not present:
            return
        # `external_object_id` is legitimately absent for a kind that creates no
        # object, so completeness is judged on the three that identify the
        # request itself. A partial marker is a corrupt one.
        identifying = ("external_request_kind", "external_request_id",
                       "external_body_fingerprint")
        if any(payload.get(key) is None for key in identifying):
            raise RecoveryError(
                "archive-write external request marker is incomplete")
        self._insert_external_request(
            conn,
            request_kind=payload["external_request_kind"],
            external_request_id=payload["external_request_id"],
            body_fingerprint=payload["external_body_fingerprint"],
            observed_generation=operation["new_generation_id"],
            observed_authority_receipt=None,
            object_id=payload.get("external_object_id"))

    def record_external_request(self, *, request_kind: str,
                                external_request_id: str,
                                body_fingerprint: str,
                                observed_generation: str,
                                observed_authority_receipt: Optional[str],
                                object_id: Optional[str]) -> dict:
        """Spend an external request id in a transaction of its own.

        Only for operations whose whole effect *is* the control journal — the
        observation seam.  Anything that commits archive bytes must not use this:
        it would reopen the window where the bytes are durable and the record of
        who asked for them is not.
        """
        with self._mutex, self._transaction() as conn:
            return self._insert_external_request(
                conn, request_kind=request_kind,
                external_request_id=external_request_id,
                body_fingerprint=body_fingerprint,
                observed_generation=observed_generation,
                observed_authority_receipt=observed_authority_receipt,
                object_id=object_id)

    def get_authority_intent_for_operation(
            self, operation_id: str) -> Optional[dict]:
        """The one durable intent a migration committed to, if it has one.

        `control_authority_intents` is UNIQUE on `operation_id`, so this is a
        lookup rather than a search.  It exists so that a replay can be checked
        against what the first call already promised without having to be told
        the intent id it is supposed to match.
        """
        if not isinstance(operation_id, str) or not operation_id:
            raise RecoveryError("authority intent operation id is invalid")
        with self._mutex:
            conn = self._connect()
            try:
                self._validate_authority_intent_rows(conn)
                row = conn.execute(
                    "SELECT * FROM control_authority_intents WHERE operation_id=?",
                    (operation_id,)).fetchone()
                return (None if row is None
                        else self._public_authority_intent(row))
            finally:
                conn.close()

    def create_authority_intent(
            self, operation_id: str, *, fresh_envelope_sha256: str,
            legacy_fence_id: str, legacy_fence_digest: str,
            page_instance_id: str, fence_context: dict,
            expected_authority_status: str,
            target_authority_status: str = "sqlite_preferred",
            new_generation_id: str) -> dict:
        """Persist a non-executable authority commitment for future M4 CAS.

        The body intentionally contains no final logical identity, archive
        commit hash, control-ack hash, timestamp, or random nonce.  It is the
        stable preimage that a future cutover may write into the archive's
        authority event before computing the final archive logical identity.
        This method never creates a child, changes authority, or advances a
        generation.
        """
        if not _hex64(fresh_envelope_sha256):
            raise RecoveryError("authority intent envelope hash is invalid")
        _validate_page_instance_id(page_instance_id)
        fence_context = _normalize_fence_context(fence_context)
        if (not isinstance(new_generation_id, str) or not new_generation_id
                or expected_authority_status not in {
                    "legacy_authoritative", "source_changed"}
                or target_authority_status != "sqlite_preferred"):
            raise RecoveryError("authority intent precondition is invalid")
        with self._mutex:
            operation = self.get_operation(operation_id)
            if (operation is None or operation["kind"] != "migration"
                    or operation["state"] not in {"verified", "cutover_marked"}):
                raise RecoveryConflict(
                    "authority intent requires a verified migration")
            if operation["envelope_sha256"] != fresh_envelope_sha256:
                raise RecoveryConflict(
                    "authority intent envelope does not match migration")
            archive_child_id = derive_archive_child_id(operation_id)
            body = _normalize_authority_intent_body({
                "format": AUTHORITY_INTENT_FORMAT,
                "operation_id": operation_id,
                "operation_kind": "migration",
                "expected_authority_status": expected_authority_status,
                "target_authority_status": target_authority_status,
                "expected_generation": operation["expected_generation"],
                "new_generation_id": new_generation_id,
                "old_logical_sha256": operation["old_logical_sha256"],
                "fresh_envelope_sha256": fresh_envelope_sha256,
                "raw_key_sha256": fence_context["raw_key_sha256"],
                "projection_sha256": fence_context["projection_sha256"],
                "target_count": fence_context["target_count"],
                "target_hash": fence_context["target_hash"],
                "legacy_fence_id": legacy_fence_id,
                "legacy_fence_digest": legacy_fence_digest,
                "page_instance_id": page_instance_id,
                "archive_child_id": archive_child_id,
            })
            receipt = _authority_intent_receipt(body, self._secret)
            # A response lost after a later cutover must replay the durable
            # intent without trying to revalidate the now-consumed fence.
            existing_conn = self._connect()
            try:
                existing = existing_conn.execute(
                    "SELECT * FROM control_authority_intents "
                    "WHERE operation_id=?", (operation_id,)).fetchone()
                if existing is not None:
                    self._validate_authority_intent_rows(existing_conn)
                    if (existing["intent_id"] != receipt["intent_id"]
                            or existing["intent_receipt_sha256"]
                            != receipt["intent_receipt_sha256"]):
                        raise RecoveryConflict(
                            "authority intent conflicts with existing operation")
                    return self._public_authority_intent(existing)
            finally:
                existing_conn.close()
            self.validate_migration_fence(
                operation_id, fence_id=legacy_fence_id,
                fence_digest=legacy_fence_digest,
                page_instance_id=page_instance_id,
                fence_context=fence_context)
            with self._transaction() as conn:
                existing = conn.execute(
                    "SELECT * FROM control_authority_intents "
                    "WHERE operation_id=?", (operation_id,)).fetchone()
                if existing is not None:
                    self._validate_authority_intent_rows(conn)
                    if (existing["intent_id"] != receipt["intent_id"]
                            or existing["intent_receipt_sha256"]
                            != receipt["intent_receipt_sha256"]):
                        raise RecoveryConflict(
                            "authority intent conflicts with existing operation")
                    return self._public_authority_intent(existing)
                generation = conn.execute(
                    "SELECT * FROM control_generation WHERE singleton_id=1"
                ).fetchone()
                authority = conn.execute(
                    "SELECT * FROM control_authority WHERE singleton_id=1"
                ).fetchone()
                if (generation is None or authority is None
                        or generation["generation_id"]
                        != operation["expected_generation"]
                        or generation["logical_sha256"]
                        != operation["old_logical_sha256"]
                        or authority["status"] != expected_authority_status):
                    raise RecoveryConflict(
                        "authority intent generation or authority is stale")
                try:
                    conn.execute(
                        "INSERT INTO control_authority_intents "
                        "(intent_id,operation_id,operation_kind,"
                        "expected_authority_status,target_authority_status,"
                        "expected_generation,new_generation_id,old_logical_sha256,"
                        "fresh_envelope_sha256,raw_key_sha256_json,projection_sha256,"
                        "target_count,target_hash,legacy_fence_id,legacy_fence_digest,"
                        "page_instance_id,archive_child_id,intent_receipt_sha256,"
                        "intent_receipt_mac,body_json,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (receipt["intent_id"], operation_id, "migration",
                         expected_authority_status, target_authority_status,
                         operation["expected_generation"], new_generation_id,
                         operation["old_logical_sha256"], fresh_envelope_sha256,
                         _canonical(body["raw_key_sha256"]).decode("utf-8"),
                         body["projection_sha256"], body["target_count"],
                         body["target_hash"], legacy_fence_id,
                         legacy_fence_digest, page_instance_id, archive_child_id,
                         receipt["intent_receipt_sha256"],
                         receipt["intent_receipt_mac"], receipt["body_json"],
                         _millis_now()))
                except sqlite3.IntegrityError as exc:
                    raise RecoveryConflict(
                        "authority intent identity is already bound") from exc
                row = conn.execute(
                    "SELECT * FROM control_authority_intents WHERE intent_id=?",
                    (receipt["intent_id"],)).fetchone()
                return self._public_authority_intent(row)

    def validate_authority_intent(
            self, intent_id: str, *, fresh_envelope_sha256: str,
            page_instance_id: str, fence_context: dict) -> dict:
        """Recheck a durable intent before a future live authority CAS."""
        if not _hex64(fresh_envelope_sha256):
            raise RecoveryError("authority intent envelope hash is invalid")
        with self._mutex:
            conn = self._connect()
            try:
                self._validate_authority_intent_rows(conn)
                row = conn.execute(
                    "SELECT * FROM control_authority_intents WHERE intent_id=?",
                    (intent_id,)).fetchone()
            finally:
                conn.close()
            if row is None:
                raise RecoveryError("authority intent is unknown")
            public = self._public_authority_intent(row)
            body = public["body"]
            if body["fresh_envelope_sha256"] != fresh_envelope_sha256:
                raise RecoveryConflict("authority intent source has changed")
            context = _normalize_fence_context(fence_context)
            expected_context = {
                "target_count": body["target_count"],
                "target_hash": body["target_hash"],
                "projection_sha256": body["projection_sha256"],
                "raw_key_sha256": body["raw_key_sha256"],
                "old_logical_sha256": body["old_logical_sha256"],
            }
            if context != expected_context:
                raise RecoveryConflict("authority intent context has changed")
            self.validate_migration_fence(
                body["operation_id"], fence_id=body["legacy_fence_id"],
                fence_digest=body["legacy_fence_digest"],
                page_instance_id=page_instance_id, fence_context=context)
            snapshot = self.snapshot()
            if (snapshot["generation"]["generation_id"]
                    != body["expected_generation"]
                    or snapshot["generation"]["logical_sha256"]
                    != body["old_logical_sha256"]
                    or snapshot["authority"]["status"]
                    != body["expected_authority_status"]):
                raise RecoveryConflict(
                    "authority intent generation or authority is stale")
            return public

    def add_package(self, *, backup_id: str, package_dir: str,
                    manifest_core_sha256: str, manifest_final_sha256: str,
                    package_sha256: str, source_schema_version: str,
                    captured_generation: str, archive_sha256: str,
                    envelope_sha256: str, projection_sha256: str) -> None:
        with self._mutex, self._transaction() as conn:
            try:
                conn.execute(
                    "INSERT INTO control_packages "
                    "(backup_id,package_dir,manifest_core_sha256,manifest_final_sha256,"
                    "package_sha256,source_schema_version,captured_generation,archive_sha256,"
                    "envelope_sha256,projection_sha256,state,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (backup_id, package_dir, manifest_core_sha256, manifest_final_sha256,
                     package_sha256, source_schema_version, captured_generation,
                     archive_sha256, envelope_sha256, projection_sha256, "staged",
                     _millis_now()))
            except sqlite3.IntegrityError as exc:
                raise RecoveryError("control package identity conflict") from exc

    def set_package_state(self, backup_id: str, state: str) -> None:
        with self._mutex, self._transaction() as conn:
            try:
                conn.execute("UPDATE control_packages SET state=? WHERE backup_id=?",
                             (state, backup_id))
            except sqlite3.IntegrityError as exc:
                raise RecoveryError("control package state rejected") from exc

    def _receipt(self, *, operation_id: str, kind: str, expected: str,
                 new_generation: str, old_logical: str, logical: str,
                 receipt_kind: str) -> dict:
        body = {
            "operation_id": operation_id, "receipt_kind": receipt_kind,
            "expected_generation": expected, "new_generation_id": new_generation,
            "old_logical_sha256": old_logical,
            "expected_new_logical_sha256": logical,
            "observed_new_logical_sha256": logical,
        }
        body_json = _canonical(body)
        receipt_sha256 = _sha256(body_json)
        receipt_mac = hmac.new(self._secret, body_json, hashlib.sha256).hexdigest()
        return {"body": body, "body_json": body_json.decode("utf-8"),
                "receipt_sha256": receipt_sha256, "receipt_mac": receipt_mac}

    def add_receipt(self, *, operation_id: str, receipt_kind: str) -> dict:
        with self._mutex, self._transaction() as conn:
            row = conn.execute("SELECT * FROM control_operations WHERE operation_id=?",
                               (operation_id,)).fetchone()
            if row is None:
                raise RecoveryError("unknown control operation")
            return self._insert_receipt(conn, row, receipt_kind)

    def _insert_receipt(self, conn: sqlite3.Connection, row: sqlite3.Row,
                        receipt_kind: str) -> dict:
        receipt = self._receipt(
            operation_id=row["operation_id"], kind=row["kind"],
            expected=row["expected_generation"],
            new_generation=row["new_generation_id"],
            old_logical=row["old_logical_sha256"],
            logical=row["new_logical_sha256"], receipt_kind=receipt_kind)
        rid = "rcpt_" + uuid.uuid4().hex
        try:
            conn.execute(
                "INSERT INTO control_operation_receipts "
                "(receipt_id,operation_id,receipt_kind,expected_generation,"
                "new_generation_id,old_logical_sha256,expected_new_logical_sha256,"
                "observed_new_logical_sha256,receipt_sha256,receipt_mac,body_json,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (rid, row["operation_id"], receipt_kind,
                 row["expected_generation"], row["new_generation_id"],
                 row["old_logical_sha256"], row["new_logical_sha256"],
                 row["new_logical_sha256"], receipt["receipt_sha256"],
                 receipt["receipt_mac"], receipt["body_json"], _millis_now()))
        except sqlite3.IntegrityError as exc:
            raise RecoveryError("control receipt rejected") from exc
        return {"receipt_id": rid, **receipt}

    def complete_generation_operation(self, operation_id: str, *, state: str) -> dict:
        """Atomically receipt, terminal state, and generation advancement."""
        if state not in ("succeeded", "resolved", "cutover_marked"):
            raise RecoveryError("invalid generation terminal state")
        with self._mutex, self._transaction() as conn:
            op = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            old = conn.execute(
                "SELECT * FROM control_generation WHERE singleton_id=1").fetchone()
            if op is None or old is None:
                raise RecoveryError("generation root is missing")
            if op["state"] != "swapping":
                raise RecoveryError("generation operation is not swapping")
            archive_receipt = self._insert_receipt(conn, op, "archive_commit")
            now = _millis_now()
            conn.execute(
                "UPDATE control_operations SET archive_commit_receipt=?,updated_at=? "
                "WHERE operation_id=?",
                (archive_receipt["receipt_id"], now, operation_id))
            op = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            ack = self._insert_receipt(conn, op, "control_ack")
            conn.execute(
                "UPDATE control_operations SET control_ack_receipt=?,state=?,updated_at=? "
                "WHERE operation_id=?",
                (ack["receipt_id"], state, _millis_now(), operation_id))
            conn.execute(
                "UPDATE control_generation SET generation_id=?,logical_sha256=?,"
                "state=?,updated_at=? WHERE singleton_id=1",
                (op["new_generation_id"], op["new_logical_sha256"],
                 "absent" if op["new_logical_sha256"] == ABSENT_LOGICAL_SHA256
                 else "present", _millis_now()))
            return dict(conn.execute(
                "SELECT * FROM control_generation WHERE singleton_id=1").fetchone())

    def record_observation(self, *, idempotency_key: str,
                           request_fingerprint: str,
                           legacy_digest: str) -> dict:
        """Record a post-cutover legacy-digest observation.

        Resolves the ambiguity written up in PHASE_0_EXIT_CONTRACT.md §6 by
        splitting the two cases the contract's "may append" already implies:

        A matching digest is a pure confirmation.  It creates no operation, no
        event, and no generation — the UI polls this seam, and an observation
        that moved the epoch every time it agreed would churn the journal for
        saying nothing happened.

        A drifted digest is a control-plane transition, so it materialises one
        `observation` operation, appends the append-only
        `sqlite_preferred -> source_changed` event, writes both receipts, and
        advances the generation.  That reading takes `control_generation` to
        name a control-journal epoch rather than an archive-bytes epoch, which
        is why `new_logical_sha256` equals the old one here: the archive is
        untouched and only the epoch moves.  It is also what the schema
        already requires — a succeeded observation needs both receipts, and a
        receipt may not reuse its expected generation.

        Because the archive bytes are untouched, the commit receipt is a
        `control_commit`, never an `archive_commit`: the pair is
        `control_commit -> control_ack`.  An `archive_commit` here would be a
        signed claim that archive bytes were mutated, and nothing downstream
        could tell that claim apart from a real one.  `control_receipt_order`
        constrains the two cases separately, and
        `_validate_operation_receipt_kinds()` re-checks the exact kinds and
        their order on every startup.

        Never creates or touches a business object either way.
        """
        if not _hex64(legacy_digest):
            raise RecoveryError("observation digest is invalid")
        with self._mutex, self._transaction() as conn:
            authority = conn.execute(
                "SELECT * FROM control_authority WHERE singleton_id=1").fetchone()
            generation = conn.execute(
                "SELECT * FROM control_generation WHERE singleton_id=1").fetchone()
            if authority is None or generation is None:
                raise RecoveryError("observation root is missing")
            if authority["status"] == "manual_recovery_required":
                raise ManualRecoveryRequired(
                    "archive is latched for manual recovery")
            if authority["status"] != "sqlite_preferred":
                raise RecoveryConflict(
                    "observation requires sqlite authority")

            if authority["legacy_digest_last_seen"] == legacy_digest:
                return {"drift": False, "operation_id": None,
                        "authority": dict(authority),
                        "generation": dict(generation)}

            existing = conn.execute(
                "SELECT * FROM control_operations WHERE idempotency_key=?",
                (idempotency_key,)).fetchone()
            if existing is not None:
                if (existing["kind"] != "observation"
                        or existing["request_fingerprint"] != request_fingerprint):
                    raise RecoveryConflict("observation idempotency conflicts")
                return {"drift": True, "operation_id": existing["operation_id"],
                        "authority": dict(authority),
                        "generation": dict(generation)}

            operation_id = "obs_" + uuid.uuid4().hex
            new_generation_id = "gen-observation-" + uuid.uuid4().hex
            now = _millis_now()
            conn.execute(
                "INSERT INTO control_operations "
                "(operation_id,kind,state,idempotency_key,request_fingerprint,"
                "expected_generation,new_generation_id,old_logical_sha256,"
                "new_logical_sha256,receipt_json,created_at,updated_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (operation_id, "observation", "prepared", idempotency_key,
                 request_fingerprint, generation["generation_id"],
                 new_generation_id, generation["logical_sha256"],
                 generation["logical_sha256"],
                 _canonical({"legacy_digest": legacy_digest}).decode("utf-8"),
                 now, now))
            operation = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            commit_receipt = self._insert_receipt(conn, operation, "control_commit")
            conn.execute(
                "UPDATE control_operations SET archive_commit_receipt=?,updated_at=? "
                "WHERE operation_id=?",
                (commit_receipt["receipt_id"], _millis_now(), operation_id))
            operation = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            ack = self._insert_receipt(conn, operation, "control_ack")
            conn.execute(
                "UPDATE control_operations SET control_ack_receipt=?,"
                "state='succeeded',updated_at=? WHERE operation_id=?",
                (ack["receipt_id"], _millis_now(), operation_id))
            conn.execute(
                "UPDATE control_generation SET generation_id=?,updated_at=? "
                "WHERE singleton_id=1", (new_generation_id, _millis_now()))
            conn.execute(
                "INSERT INTO control_authority_events "
                "(event_id,operation_id,operation_kind,from_status,to_status,"
                "expected_generation,new_generation_id,envelope_sha256,target_count,"
                "target_hash,legacy_digest_last_seen,receipt_sha256,receipt_mac,"
                "created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("event_" + uuid.uuid4().hex, operation_id, "observation",
                 "sqlite_preferred", "source_changed",
                 generation["generation_id"], new_generation_id,
                 authority["envelope_sha256"], authority["target_count"],
                 authority["target_hash"], legacy_digest,
                 ack["receipt_sha256"], ack["receipt_mac"], _millis_now()))
            conn.execute(
                "UPDATE control_authority SET status='source_changed',"
                "operation_id=?,operation_kind='observation',"
                "legacy_digest_last_seen=?,updated_at=? WHERE singleton_id=1",
                (operation_id, legacy_digest, _millis_now()))
            return {"drift": True, "operation_id": operation_id,
                    "authority": dict(conn.execute(
                        "SELECT * FROM control_authority WHERE singleton_id=1"
                    ).fetchone()),
                    "generation": dict(conn.execute(
                        "SELECT * FROM control_generation WHERE singleton_id=1"
                    ).fetchone())}

    @staticmethod
    def _validate_authority_transition(status: str,
                                       snapshot: Optional[dict]) -> dict:
        if status not in {"legacy_authoritative", "sqlite_preferred",
                          "source_changed"}:
            raise RecoveryError("invalid archive-write authority status")
        required = {"envelope_sha256", "target_count", "target_hash",
                    "legacy_digest_last_seen"}
        if snapshot is None or set(snapshot) != required:
            raise RecoveryError("archive-write authority snapshot is incomplete")
        if snapshot["envelope_sha256"] is not None \
                and not _hex64(snapshot["envelope_sha256"]):
            raise RecoveryError("archive-write authority envelope hash is invalid")
        try:
            target_count = int(snapshot["target_count"])
        except (TypeError, ValueError) as exc:
            raise RecoveryError("archive-write authority target count is invalid") from exc
        if target_count < 0 or not _hex64(snapshot["target_hash"]):
            raise RecoveryError("archive-write authority target identity is invalid")
        return {"envelope_sha256": snapshot["envelope_sha256"],
                "target_count": target_count,
                "target_hash": snapshot["target_hash"],
                "legacy_digest_last_seen": snapshot["legacy_digest_last_seen"]}

    @staticmethod
    def _generation_result(conn: sqlite3.Connection, operation_id: str) -> dict:
        operation = conn.execute(
            "SELECT * FROM control_operations WHERE operation_id=?",
            (operation_id,)).fetchone()
        generation = conn.execute(
            "SELECT * FROM control_generation WHERE singleton_id=1").fetchone()
        authority = conn.execute(
            "SELECT * FROM control_authority WHERE singleton_id=1").fetchone()
        if operation is None or generation is None or authority is None:
            raise RecoveryError("archive-write completion root is missing")
        return {"operation_id": operation_id, "state": operation["state"],
                "operation": dict(operation), "generation": dict(generation),
                "authority": dict(authority)}

    def begin_archive_write(self, operation_id: str) -> dict:
        """Bind a prepared archive-write intent to the current generation."""
        with self._mutex, self._transaction() as conn:
            operation = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            generation = conn.execute(
                "SELECT * FROM control_generation WHERE singleton_id=1").fetchone()
            if operation is None or generation is None \
                    or operation["kind"] != "archive_write":
                raise RecoveryError("archive-write operation is unknown")
            if operation["state"] == "succeeded":
                return self._generation_result(conn, operation_id)
            if operation["state"] == "prepared":
                if (operation["expected_generation"] != generation["generation_id"]
                        or operation["old_logical_sha256"] != generation["logical_sha256"]):
                    conn.execute(
                        "UPDATE control_operations SET state='conflict',updated_at=? "
                        "WHERE operation_id=?", (_millis_now(), operation_id))
                    raise RecoveryConflict(
                        "archive-write generation compare-and-swap failed")
                conn.execute(
                    "UPDATE control_operations SET state='applying',updated_at=? "
                    "WHERE operation_id=?", (_millis_now(), operation_id))
            elif operation["state"] != "applying":
                raise RecoveryConflict("archive-write operation is not applicable")
            return self._generation_result(conn, operation_id)

    def abort_archive_write(self, operation_id: str, *, state: str) -> dict:
        """Close an unacknowledged archive intent without advancing generation."""
        if state not in {"conflict", "failed"}:
            raise RecoveryError("invalid archive-write abort state")
        with self._mutex, self._transaction() as conn:
            operation = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            if operation is None or operation["kind"] != "archive_write":
                raise RecoveryError("archive-write operation is unknown")
            if operation["state"] == state:
                return self._generation_result(conn, operation_id)
            if operation["state"] not in {"prepared", "applying"}:
                raise RecoveryConflict("archive-write operation is already terminal")
            conn.execute(
                "UPDATE control_operations SET state=?,updated_at=? "
                "WHERE operation_id=?", (state, _millis_now(), operation_id))
            return self._generation_result(conn, operation_id)

    def complete_archive_write_operation(
            self, operation_id: str, *, observed_logical_sha256: str,
            authority_status: Optional[str] = None,
            authority_snapshot: Optional[dict] = None) -> dict:
        """Acknowledge one archive-write child and advance its generation.

        The archive transaction is deliberately outside this method.  The
        caller must first commit the archive bytes, recompute the logical
        identity, and pass that readback here.  This external transaction then
        writes the signed archive-commit receipt, the control acknowledgement,
        the generation CAS, and (when requested) the authority event.
        """
        if not _hex64(observed_logical_sha256):
            raise RecoveryError("archive-write observed identity is invalid")
        if authority_status is None and authority_snapshot is not None:
            raise RecoveryError("archive-write authority status is missing")
        normalized_authority = None
        if authority_status is not None:
            normalized_authority = self._validate_authority_transition(
                authority_status, authority_snapshot)
        with self._mutex, self._transaction() as conn:
            operation = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            generation = conn.execute(
                "SELECT * FROM control_generation WHERE singleton_id=1").fetchone()
            authority = conn.execute(
                "SELECT * FROM control_authority WHERE singleton_id=1").fetchone()
            if operation is None or generation is None or authority is None \
                    or operation["kind"] != "archive_write":
                raise RecoveryError("archive-write completion root is missing")
            if operation["new_logical_sha256"] != observed_logical_sha256:
                raise RecoveryConflict(
                    "archive-write post-commit identity does not match intent")
            if operation["state"] == "succeeded":
                return self._generation_result(conn, operation_id)
            if operation["state"] == "prepared":
                if (operation["expected_generation"] != generation["generation_id"]
                        or operation["old_logical_sha256"] != generation["logical_sha256"]):
                    conn.execute(
                        "UPDATE control_operations SET state='conflict',updated_at=? "
                        "WHERE operation_id=?", (_millis_now(), operation_id))
                    raise RecoveryConflict(
                        "archive-write generation compare-and-swap failed")
                conn.execute(
                    "UPDATE control_operations SET state='applying',updated_at=? "
                    "WHERE operation_id=?", (_millis_now(), operation_id))
                operation = conn.execute(
                    "SELECT * FROM control_operations WHERE operation_id=?",
                    (operation_id,)).fetchone()
            if operation["state"] != "applying":
                raise RecoveryConflict("archive-write operation is not finalizable")
            if (operation["expected_generation"] != generation["generation_id"]
                    or operation["old_logical_sha256"] != generation["logical_sha256"]):
                raise RecoveryConflict(
                    "archive-write generation compare-and-swap failed")

            if operation["archive_commit_receipt"] is None:
                archive_receipt = self._insert_receipt(
                    conn, operation, "archive_commit")
                conn.execute(
                    "UPDATE control_operations SET archive_commit_receipt=?,"
                    "updated_at=? WHERE operation_id=?",
                    (archive_receipt["receipt_id"], _millis_now(), operation_id))
            else:
                archive_receipt = conn.execute(
                    "SELECT receipt_id,receipt_sha256,receipt_mac FROM "
                    "control_operation_receipts WHERE receipt_id=?",
                    (operation["archive_commit_receipt"],)).fetchone()
                if archive_receipt is None:
                    raise ManualRecoveryRequired(
                        "archive-write commit receipt reference is missing")
            operation = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            if operation["control_ack_receipt"] is None:
                ack = self._insert_receipt(conn, operation, "control_ack")
                conn.execute(
                    "UPDATE control_operations SET control_ack_receipt=?,state='succeeded',"
                    "updated_at=? WHERE operation_id=?",
                    (ack["receipt_id"], _millis_now(), operation_id))
            else:
                ack = conn.execute(
                    "SELECT receipt_id,receipt_sha256,receipt_mac FROM "
                    "control_operation_receipts WHERE receipt_id=?",
                    (operation["control_ack_receipt"],)).fetchone()
                if ack is None:
                    raise ManualRecoveryRequired(
                        "archive-write acknowledgement receipt reference is missing")
                conn.execute(
                    "UPDATE control_operations SET state='succeeded',updated_at=? "
                    "WHERE operation_id=?", (_millis_now(), operation_id))
            operation = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            # Spend the external request id here, in the same transaction that
            # just made this operation `succeeded` and is about to advance the
            # generation. This is the step that closes the twin window: there is
            # no longer any moment at which the archive bytes are durable and the
            # record of which external request produced them is not.
            #
            # Startup reconciliation calls this same method, so the crash path
            # between the byte swap and the receipts is covered by construction
            # rather than by a second mechanism.
            self._spend_operation_external_request(conn, operation)
            conn.execute(
                "UPDATE control_generation SET generation_id=?,logical_sha256=?,"
                "state=?,updated_at=? WHERE singleton_id=1",
                (operation["new_generation_id"], operation["new_logical_sha256"],
                 "absent" if operation["new_logical_sha256"] == ABSENT_LOGICAL_SHA256
                 else "present", _millis_now()))

            if normalized_authority is not None:
                current_status = authority["status"]
                if current_status == authority_status:
                    if any(authority[key] != normalized_authority[key]
                           for key in normalized_authority):
                        raise RecoveryConflict(
                            "archive-write authority snapshot changed")
                else:
                    event_id = "event_" + uuid.uuid4().hex
                    ack_row = conn.execute(
                        "SELECT receipt_sha256,receipt_mac FROM "
                        "control_operation_receipts WHERE receipt_id=?",
                        (operation["control_ack_receipt"],)).fetchone()
                    conn.execute(
                        "INSERT INTO control_authority_events "
                        "(event_id,operation_id,operation_kind,from_status,to_status,"
                        "expected_generation,new_generation_id,envelope_sha256,target_count,"
                        "target_hash,legacy_digest_last_seen,receipt_sha256,receipt_mac,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (event_id, operation_id, "archive_write", current_status,
                         authority_status, operation["expected_generation"],
                         operation["new_generation_id"],
                         normalized_authority["envelope_sha256"],
                         normalized_authority["target_count"],
                         normalized_authority["target_hash"],
                         normalized_authority["legacy_digest_last_seen"],
                         ack_row["receipt_sha256"], ack_row["receipt_mac"],
                         _millis_now()))
                    self._bind_authority_intent_event(
                        conn, operation, event_id=event_id)
                    conn.execute(
                        "UPDATE control_authority SET status=?,operation_id=?,"
                        "operation_kind='archive_write',envelope_sha256=?,target_count=?,"
                        "target_hash=?,legacy_digest_last_seen=?,updated_at=? "
                        "WHERE singleton_id=1",
                        (authority_status, operation_id,
                         normalized_authority["envelope_sha256"],
                         normalized_authority["target_count"],
                         normalized_authority["target_hash"],
                         normalized_authority["legacy_digest_last_seen"],
                         _millis_now()))
            operation = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            if operation["parent_operation_id"] is not None:
                parent = conn.execute(
                    "SELECT * FROM control_operations WHERE operation_id=?",
                    (operation["parent_operation_id"],)).fetchone()
                current_generation = conn.execute(
                    "SELECT * FROM control_generation WHERE singleton_id=1").fetchone()
                current_authority = conn.execute(
                    "SELECT * FROM control_authority WHERE singleton_id=1").fetchone()
                if parent is None or parent["kind"] != "migration":
                    raise RecoveryError("archive-write parent migration is missing")
                if parent["state"] == "verified":
                    if (current_generation["generation_id"]
                            != operation["new_generation_id"]
                            or current_authority["status"] != "sqlite_preferred"
                            or current_authority["operation_id"] != operation_id):
                        raise RecoveryConflict(
                            "migration cutover authority is not acknowledged")
                    conn.execute(
                        "UPDATE control_operations SET state='cutover_marked',updated_at=? "
                        "WHERE operation_id=?", (_millis_now(), parent["operation_id"]))
                elif parent["state"] != "cutover_marked":
                    raise RecoveryConflict("migration parent is not cutover-ready")
            return self._generation_result(conn, operation_id)

    def _bind_authority_intent_event(self, conn: sqlite3.Connection,
                                     operation: sqlite3.Row, *,
                                     event_id: str) -> None:
        """Bind one authority event to the intent that committed to it.

        Only archive-write children prepared from a durable authority intent
        carry the three `authority_intent_*` fields in their receipt payload;
        an ordinary archive write has none and is left alone.  For the ones
        that do, the binding row is written inside the same transaction as the
        authority event and the CAS, so no reader can ever observe a cutover
        event whose intent is unaccounted for — the startup validator's
        completeness sweep depends on exactly that pairing existing.

        The intent receipt is re-derived from the journal rather than trusted
        from the payload: the payload was written at prepare time, before the
        archive transaction, and this is the last point at which the two can
        still be shown to agree.
        """
        try:
            payload = json.loads(operation["receipt_json"] or "{}")
        except (TypeError, ValueError) as exc:
            raise RecoveryError(
                "archive-write receipt payload is invalid") from exc
        intent_id = payload.get("authority_intent_id")
        if intent_id is None:
            return
        intent = conn.execute(
            "SELECT * FROM control_authority_intents WHERE intent_id=?",
            (intent_id,)).fetchone()
        if intent is None:
            raise RecoveryError("archive-write authority intent is missing")
        if (payload.get("authority_intent_receipt_sha256")
                != intent["intent_receipt_sha256"]
                or payload.get("authority_intent_receipt_mac")
                != intent["intent_receipt_mac"]):
            raise RecoveryError(
                "archive-write authority intent receipt does not match")
        try:
            conn.execute(
                "INSERT INTO control_authority_intent_events "
                "(event_id,intent_id,operation_id,intent_receipt_sha256,"
                "intent_receipt_mac,created_at) VALUES (?,?,?,?,?,?)",
                (event_id, intent_id, operation["operation_id"],
                 intent["intent_receipt_sha256"], intent["intent_receipt_mac"],
                 _millis_now()))
        except sqlite3.IntegrityError as exc:
            # UNIQUE(intent_id) is the contract's one-cutover-child-per-migration
            # rule; the root trigger is its structural half.  Either firing here
            # means a second event tried to claim an intent already spent.
            raise RecoveryConflict(
                "authority intent is already bound to an archive event") from exc

    def mark_migration_cutover(self, migration_operation_id: str,
                               child_operation_id: str) -> dict:
        """Close a verified migration only after its archive child succeeds."""
        with self._mutex, self._transaction() as conn:
            migration = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (migration_operation_id,)).fetchone()
            child = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (child_operation_id,)).fetchone()
            generation = conn.execute(
                "SELECT * FROM control_generation WHERE singleton_id=1").fetchone()
            authority = conn.execute(
                "SELECT * FROM control_authority WHERE singleton_id=1").fetchone()
            if migration is None or child is None or generation is None \
                    or authority is None or migration["kind"] != "migration" \
                    or child["kind"] != "archive_write" \
                    or child["parent_operation_id"] != migration_operation_id:
                raise RecoveryError("migration cutover child is unknown")
            if migration["state"] == "cutover_marked":
                if child["state"] != "succeeded":
                    raise RecoveryConflict("migration cutover child is not successful")
                return dict(migration)
            if migration["state"] != "verified" or child["state"] != "succeeded":
                raise RecoveryConflict("migration is not ready for cutover")
            if generation["generation_id"] != child["new_generation_id"]:
                raise RecoveryConflict("migration cutover generation is stale")
            if authority["status"] != "sqlite_preferred" \
                    or authority["operation_id"] != child_operation_id:
                raise RecoveryConflict("migration cutover authority is not acknowledged")
            conn.execute(
                "UPDATE control_operations SET state='cutover_marked',updated_at=? "
                "WHERE operation_id=?", (_millis_now(), migration_operation_id))
            return dict(conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (migration_operation_id,)).fetchone())

    def complete_recovery_operation(self, operation_id: str, *,
                                    authority_status: str,
                                    authority_snapshot: dict) -> dict:
        """Resolve a manual latch through a new, signed recovery operation."""
        if authority_status not in {
                "legacy_authoritative", "sqlite_preferred", "source_changed"}:
            raise RecoveryError("invalid recovery authority status")
        required = {"envelope_sha256", "target_count", "target_hash",
                    "legacy_digest_last_seen"}
        if set(authority_snapshot) != required:
            raise RecoveryError("recovery authority snapshot is incomplete")
        with self._mutex, self._transaction() as conn:
            op = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            old = conn.execute(
                "SELECT * FROM control_generation WHERE singleton_id=1").fetchone()
            authority = conn.execute(
                "SELECT * FROM control_authority WHERE singleton_id=1").fetchone()
            if op is None or old is None or authority is None:
                raise RecoveryError("recovery completion root is missing")
            if op["kind"] != "recovery" or op["state"] != "applying":
                raise RecoveryError("recovery operation is not applying")
            if authority["status"] != "manual_recovery_required":
                raise RecoveryError("manual recovery latch is not active")
            if op["expected_generation"] != old["generation_id"]:
                raise RecoveryConflict("recovery generation compare-and-swap failed")
            archive_receipt = self._insert_receipt(conn, op, "archive_commit")
            conn.execute(
                "UPDATE control_operations SET archive_commit_receipt=?,updated_at=? "
                "WHERE operation_id=?",
                (archive_receipt["receipt_id"], _millis_now(), operation_id))
            op = conn.execute(
                "SELECT * FROM control_operations WHERE operation_id=?",
                (operation_id,)).fetchone()
            ack = self._insert_receipt(conn, op, "control_ack")
            conn.execute(
                "UPDATE control_operations SET control_ack_receipt=?,state='resolved',"
                "updated_at=? WHERE operation_id=?",
                (ack["receipt_id"], _millis_now(), operation_id))
            conn.execute(
                "INSERT INTO control_authority_events "
                "(event_id,operation_id,operation_kind,from_status,to_status,"
                "expected_generation,new_generation_id,envelope_sha256,target_count,"
                "target_hash,legacy_digest_last_seen,receipt_sha256,receipt_mac,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("event_" + uuid.uuid4().hex, operation_id, "recovery",
                 "manual_recovery_required", authority_status,
                 old["generation_id"], op["new_generation_id"],
                 authority_snapshot["envelope_sha256"],
                 int(authority_snapshot["target_count"]),
                 authority_snapshot["target_hash"],
                 authority_snapshot["legacy_digest_last_seen"],
                 ack["receipt_sha256"], ack["receipt_mac"], _millis_now()))
            conn.execute(
                "UPDATE control_authority SET status=?,operation_id=?,"
                "operation_kind='recovery',envelope_sha256=?,target_count=?,"
                "target_hash=?,legacy_digest_last_seen=?,updated_at=? "
                "WHERE singleton_id=1",
                (authority_status, operation_id,
                 authority_snapshot["envelope_sha256"],
                 int(authority_snapshot["target_count"]),
                 authority_snapshot["target_hash"],
                 authority_snapshot["legacy_digest_last_seen"], _millis_now()))
            conn.execute(
                "UPDATE control_generation SET generation_id=?,logical_sha256=?,"
                "state=?,updated_at=? WHERE singleton_id=1",
                (op["new_generation_id"], op["new_logical_sha256"],
                 "absent" if op["new_logical_sha256"] == ABSENT_LOGICAL_SHA256
                 else "present", _millis_now()))
            return self._snapshot_from_connection(conn)

    def advance_generation(self, operation_id: str, *, state: str) -> dict:
        with self._mutex, self._transaction() as conn:
            op = conn.execute("SELECT * FROM control_operations WHERE operation_id=?",
                              (operation_id,)).fetchone()
            old = conn.execute("SELECT * FROM control_generation WHERE singleton_id=1").fetchone()
            if op is None or old is None:
                raise RecoveryError("generation root is missing")
            try:
                conn.execute(
                    "UPDATE control_generation SET generation_id=?,logical_sha256=?,"
                    "state=?,updated_at=? WHERE singleton_id=1",
                    (op["new_generation_id"], op["new_logical_sha256"], state,
                     _millis_now()))
            except sqlite3.IntegrityError as exc:
                raise RecoveryError("generation acknowledgement rejected") from exc
            return dict(conn.execute(
                "SELECT * FROM control_generation WHERE singleton_id=1").fetchone())

    def manual_latch(self, operation_id: str, *, failure_code: str,
                     staged_db_sha256: Optional[str], preimage_sha256: Optional[str],
                     preserved_artifacts: dict) -> dict:
        with self._mutex, self._transaction() as conn:
            op = conn.execute("SELECT * FROM control_operations WHERE operation_id=?",
                              (operation_id,)).fetchone()
            authority = conn.execute("SELECT * FROM control_authority WHERE singleton_id=1").fetchone()
            if op is None or authority is None:
                raise RecoveryError("manual latch root is missing")
            conn.execute("UPDATE control_operations SET state=?,updated_at=? WHERE operation_id=?",
                         ("manual_recovery_required", _millis_now(), operation_id))
            body = {
                "operation_id": operation_id, "failure_kind": "manual_latch",
                "failure_code": failure_code,
                "expected_generation": op["expected_generation"],
                "observed_generation": op["expected_generation"],
                "old_logical_sha256": op["old_logical_sha256"],
                "staged_db_sha256": staged_db_sha256,
                "preimage_sha256": preimage_sha256,
                "package_id": op["package_id"],
                "preserved_artifacts": preserved_artifacts,
            }
            body_json = _canonical(body)
            receipt_sha = _sha256(body_json)
            receipt_mac = hmac.new(self._secret, body_json, hashlib.sha256).hexdigest()
            try:
                conn.execute(
                    "INSERT INTO control_failure_receipts "
                    "(receipt_id,operation_id,failure_kind,failure_code,expected_generation,"
                    "observed_generation,old_logical_sha256,staged_db_sha256,preimage_sha256,"
                    "package_id,preserved_artifact_json,receipt_sha256,receipt_mac,body_json,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("fail_" + uuid.uuid4().hex, operation_id, "manual_latch", failure_code,
                     op["expected_generation"], op["expected_generation"],
                     op["old_logical_sha256"], staged_db_sha256, preimage_sha256,
                     op["package_id"], _canonical(preserved_artifacts).decode("utf-8"),
                     receipt_sha, receipt_mac, body_json.decode("utf-8"), _millis_now()))
                conn.execute(
                    "INSERT INTO control_authority_events "
                    "(event_id,operation_id,operation_kind,from_status,to_status,"
                    "expected_generation,new_generation_id,envelope_sha256,target_count,"
                    "target_hash,legacy_digest_last_seen,receipt_sha256,receipt_mac,created_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    ("event_" + uuid.uuid4().hex, operation_id, op["kind"],
                     authority["status"], "manual_recovery_required",
                     op["expected_generation"], op["expected_generation"],
                     authority["envelope_sha256"], authority["target_count"],
                     authority["target_hash"], authority["legacy_digest_last_seen"],
                     receipt_sha, receipt_mac, _millis_now()))
                conn.execute(
                    "UPDATE control_authority SET status=?,operation_id=?,operation_kind=?,"
                    "updated_at=? WHERE singleton_id=1",
                    ("manual_recovery_required", operation_id, op["kind"], _millis_now()))
            except sqlite3.IntegrityError as exc:
                raise RecoveryError("manual recovery latch rejected") from exc
            return {"status": "manual_recovery_required", "receipt_sha256": receipt_sha,
                    "receipt_mac": receipt_mac}


def _staged_member_bytes(path: Path) -> dict[str, bytes]:
    """Read a package staging directory before its final ready marker exists."""
    try:
        names = {entry.name for entry in os.scandir(path)}
    except OSError as exc:
        raise RecoveryError("package staging directory cannot be listed") from exc
    expected = set(PACKAGE_MEMBERS) - {"ready"}
    if names != expected:
        raise RecoveryError("package staging member allowlist mismatch")
    return {
        "manifest.json": _read_regular(path / "manifest.json", MAX_MANIFEST_BYTES),
        "archive.sqlite3": _read_regular(path / "archive.sqlite3", MAX_ARCHIVE_BYTES),
        "localstorage-envelope.json": _read_regular(
            path / "localstorage-envelope.json", MAX_ENVELOPE_BYTES),
        "projection.json": _read_regular(path / "projection.json", MAX_PROJECTION_BYTES),
    }


def _parse_manifest(data: bytes) -> dict:
    if len(data) > MAX_MANIFEST_BYTES:
        raise RecoveryError("manifest is too large")
    try:
        manifest = json.loads(data.decode("utf-8"),
                              parse_constant=lambda value: (_ for _ in ()).throw(
                                  ValueError(value)))
    except (UnicodeDecodeError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise RecoveryError("manifest JSON is invalid") from exc
    if not isinstance(manifest, dict) or _canonical(manifest) != data:
        raise RecoveryError("manifest is not canonical")
    required = {
        "format", "backup_id", "manifest_core_sha256", "source_schema_version",
        "target_schema_version", "database_state", "members", "envelope_sha256",
        "projection_version", "normalizer_version", "config_schema_version",
        "captured_generation", "authority_snapshot", "source_identity",
        "created_at", "exclusions",
    }
    if set(manifest) != required or manifest["format"] != PACKAGE_FORMAT:
        raise RecoveryError("manifest field set is invalid")
    if (not isinstance(manifest["backup_id"], str)
            or not manifest["backup_id"].startswith("bkp_")
            or not _hex64(manifest["backup_id"][4:])):
        raise RecoveryError("manifest backup_id is invalid")
    if not _hex64(manifest["manifest_core_sha256"]):
        raise RecoveryError("manifest core hash is invalid")
    if manifest["source_schema_version"] not in SUPPORTED_SOURCE_SCHEMA_VERSIONS:
        raise RecoveryError("manifest source schema is unsupported")
    # The target is derived from the source rather than fixed, so it cannot
    # under-state where a restore of this package will land.  A v6/v7 package
    # still says 7 — the existing vectors are byte-identical — while a v8
    # package says 8, which is what stops its restore from being staged at v7
    # and silently dropping the v8 read-model and lineage it was captured with.
    if (manifest["target_schema_version"]
            != _package_target_schema_version(manifest["source_schema_version"])):
        raise RecoveryError("manifest target schema is unsupported")
    if manifest["database_state"] not in ("present", "absent_at_capture"):
        raise RecoveryError("manifest database state is invalid")
    if set(manifest["members"]) != set(DATA_MEMBERS):
        raise RecoveryError("manifest member map is invalid")
    for name in DATA_MEMBERS:
        member = manifest["members"][name]
        if (not isinstance(member, dict) or set(member) != {"bytes", "sha256"}
                or not isinstance(member["bytes"], int) or member["bytes"] < 0
                or not _hex64(member["sha256"])):
            raise RecoveryError("manifest member identity is invalid")
    authority = manifest["authority_snapshot"]
    if (not isinstance(authority, dict)
            or set(authority) != {"status", "operation_id", "operation_kind",
                                  "envelope_sha256", "target_count", "target_hash",
                                  "legacy_digest_last_seen", "generation_id",
                                  "generation_state", "receipt_sha256"}):
        raise RecoveryError("manifest authority snapshot is invalid")
    core = dict(manifest)
    core["manifest_core_sha256"] = None
    core["backup_id"] = None
    if _sha256_json(core) != manifest["manifest_core_sha256"]:
        raise RecoveryError("manifest core hash mismatch")
    preimage = _lp(manifest["format"].encode("utf-8"))
    preimage += _lp(manifest["manifest_core_sha256"].encode("ascii"))
    for name in PACKAGE_MEMBERS:
        if name == "manifest.json":
            # The manifest hash would be circular.  The package identity uses
            # the already-derived core hash for this fixed tuple position.
            member_hash = manifest["manifest_core_sha256"]
        elif name == "ready":
            member_hash = _sha256(b"")
        else:
            member_hash = manifest["members"][name]["sha256"]
        preimage += _lp(name.encode("utf-8")) + _lp(member_hash.encode("ascii"))
    if manifest["backup_id"] != "bkp_" + _sha256(preimage):
        raise RecoveryError("manifest backup_id mismatch")
    return manifest


def _validate_complete_package(path: Path) -> tuple[dict, dict[str, bytes]]:
    members = _validate_package_directory(path)
    manifest = _parse_manifest(members["manifest.json"])
    for name in DATA_MEMBERS:
        data = members[name]
        record = manifest["members"][name]
        if len(data) != record["bytes"] or _sha256(data) != record["sha256"]:
            raise RecoveryError("package member hash mismatch")
    envelope = json.loads(members["localstorage-envelope.json"].decode("utf-8"))
    try:
        validated = MIGRATION.validate_envelope(envelope)
    except MIGRATION.MigrationEnvelopeError as exc:
        raise RecoveryError("package envelope is invalid") from exc
    if MIGRATION.envelope_sha256(validated) != manifest["envelope_sha256"]:
        raise RecoveryError("package envelope hash mismatch")
    if _sha256(members["ready"]) != _sha256(b""):
        raise RecoveryError("package ready marker is not empty")
    return manifest, members


def _package_target_schema_version(source_schema_version: str) -> int:
    """The archive schema a restore of this package must land on.

    A package restores forward, never backward.  For the two legacy sources the
    target is v7, which is what every existing v6/v7 package vector records.  A
    v8 source targets v8: its archive already carries the M2 lineage tables and
    the post-cutover read model, and staging it at v7 would drop both while
    still reporting a successful restore.
    """
    if source_schema_version not in SUPPORTED_SOURCE_SCHEMA_VERSIONS:
        raise RecoveryError("manifest source schema is unsupported")
    return max(int(source_schema_version), TARGET_SCHEMA_VERSION)


def _migrate_stage_to_target(path: Path, target_schema_version: int) -> None:
    """Bring a restore stage up to the schema its package committed to.

    Both steps are the same additive installers the formal migration uses, so a
    restored archive is schema-identical to one the app produced itself.  The
    only new rule is the refusal: a stage already newer than its declared target
    is never opened, because the only way to make it match would be to discard
    the surface the newer schema added.
    """
    if target_schema_version not in (7, 8):
        raise RecoveryError("restore target schema is unsupported")
    version = int(_archive_schema_version(path))
    if version > target_schema_version:
        raise RecoveryError("restore package would down-migrate the archive")
    if target_schema_version == 7:
        _migrate_stage_to_v7(path)
        return
    if version < 7:
        _migrate_stage_to_v7(path)
    _migrate_stage_to_v8(path)


def _migrate_stage_to_v7(path: Path) -> None:
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        # Validate the source v6 state before any additive migration work.
        # This is the same validator used by logical_identity/startup.
        validate_archive_connection(conn)
        if user_version == 6:
            PERSISTENCE.PersistenceStore.install_v7_schema(
                conn, app_release_id="fire-modeling-3.0")
        elif user_version != 7:
            raise RecoveryError("staged archive schema is unsupported")
        conn.commit()
        check = conn.execute("PRAGMA quick_check").fetchall()
        if len(check) != 1 or str(check[0][0]).lower() != "ok":
            raise RecoveryError("staged archive quick_check failed")
        # Validate the committed staging image before its physical hash is
        # recorded or it becomes eligible for swap.
        validate_archive_connection(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _checkpoint_delete_journal(path)


def _migrate_stage_to_v8(path: Path) -> None:
    """Install the additive M2 lineage schema in a disposable stage."""
    conn = sqlite3.connect(str(path), isolation_level=None)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("BEGIN IMMEDIATE")
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if user_version == 7:
            validate_archive_connection(conn)
            PERSISTENCE.PersistenceStore.install_v8_schema(
                conn, app_release_id="fire-modeling-3.0")
        elif user_version != 8:
            raise RecoveryError("staged archive schema is unsupported for M2")
        conn.commit()
        check = conn.execute("PRAGMA quick_check").fetchall()
        if len(check) != 1 or str(check[0][0]).lower() != "ok":
            raise RecoveryError("staged archive quick_check failed after v8")
        validate_archive_connection(conn)
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    _checkpoint_delete_journal(path)


def _archive_schema_version(path: Path) -> str:
    conn = PERSISTENCE._readonly_connect(str(path))
    try:
        PERSISTENCE._readonly_schema_preflight(conn)
        return str(int(conn.execute("PRAGMA user_version").fetchone()[0]))
    finally:
        conn.close()


def _artifact_hashes(paths: list[Path]) -> dict[str, str]:
    result = {}
    for path in paths:
        if path.exists():
            result[str(path)] = _sha256(_read_regular(path, MAX_ARCHIVE_BYTES))
    return result


class BackupRestoreManager:
    """Coordinate package capture and explicit restore swap/rollback."""

    def __init__(self, archive_path: str, *, app_release_id: str = "fire-modeling-3.0"):
        self.archive_path = Path(os.path.abspath(os.path.expanduser(archive_path)))
        self.app_release_id = app_release_id
        self.support_root = self.archive_path.parent
        self.backup_root = self.support_root / "backups"
        self.control_path = self.support_root / "recovery-control.sqlite3"
        self.restore_root = self.backup_root / ".restore-staging"
        self.archive_write_root = self.support_root / ".archive-write-staging"
        _secure_dir(self.support_root)
        _secure_dir(self.backup_root)
        self.journal = RecoveryJournal(str(self.control_path))
        self._lock = threading.RLock()
        self._local_archive_write_intents: set[str] = set()

    def close(self) -> None:
        self.journal.close()

    def _current_identity(self) -> tuple[str, str]:
        if not self.archive_path.exists():
            return ABSENT_LOGICAL_SHA256, "absent"
        try:
            logical = logical_identity(str(self.archive_path))
        except Exception as exc:  # noqa: BLE001
            raise RecoveryError("current archive identity is unavailable") from exc
        return logical, "present"

    def _create_reconciliation_latch(self, snapshot: dict, *,
                                     observed_logical: Optional[str],
                                     reason: str,
                                     preserved_artifacts: dict) -> None:
        generation = snapshot["generation"]
        operation_id = "op_" + uuid.uuid4().hex
        self.journal.create_operation(
            operation_id=operation_id, kind="recovery", state="resolving",
            idempotency_key="reconcile:" + generation["generation_id"] + ":"
                            + uuid.uuid4().hex,
            request_fingerprint=_sha256_json({
                "kind": "startup_reconciliation",
                "generation": generation["generation_id"],
                "observed_logical": observed_logical,
            }),
            expected_generation=generation["generation_id"],
            new_generation_id="gen-recovery-" + uuid.uuid4().hex,
            old_logical_sha256=generation["logical_sha256"],
            new_logical_sha256=generation["logical_sha256"])
        self.journal.manual_latch(
            operation_id, failure_code="startup_reconciliation_ambiguous",
            staged_db_sha256=None, preimage_sha256=None,
            preserved_artifacts={"reason": reason,
                                 "observed_logical": observed_logical,
                                 **preserved_artifacts})

    @staticmethod
    def _archive_write_authority(operation: dict) -> Optional[tuple[str, dict]]:
        """Decode the immutable authority transition carried by an intent."""
        try:
            payload = json.loads(operation.get("receipt_json") or "{}")
        except (TypeError, ValueError) as exc:
            raise ManualRecoveryRequired(
                "archive-write authority intent is not readable") from exc
        status = payload.get("authority_status")
        snapshot = payload.get("authority_snapshot")
        if status is None and snapshot is None:
            return None
        if not isinstance(status, str) or not isinstance(snapshot, dict):
            raise ManualRecoveryRequired(
                "archive-write authority intent is incomplete")
        return status, snapshot

    def _reconcile_startup(self) -> None:
        """Reconcile a surviving archive before any new archive operation."""
        snapshot = self.journal.snapshot()
        if snapshot["generation"] is None or snapshot["authority"] is None:
            return
        if self.journal.raw_manual_active():
            raise ManualRecoveryRequired("raw restore manual latch is active")
        if snapshot["authority"]["status"] == "manual_recovery_required":
            raise ManualRecoveryRequired("manual recovery latch is active")

        # Run before the branches below, so a crash *between* aborting a child
        # and releasing its parent still converges on the next startup rather
        # than on no startup at all.
        self._release_abandoned_cutover_parents()

        current_logical, _current_state = self._current_identity()
        generation = snapshot["generation"]
        active = self.journal.active_operations()
        restore_ops = [op for op in active if op["kind"] == "restore"]
        archive_write_ops = [op for op in active if op["kind"] == "archive_write"]
        if len(archive_write_ops) > 1 or (archive_write_ops and restore_ops):
            self._create_reconciliation_latch(
                snapshot, observed_logical=current_logical,
                reason="multiple_archive_generation_intents",
                preserved_artifacts={"operation_ids": [
                    op["operation_id"] for op in restore_ops + archive_write_ops]})
            raise ManualRecoveryRequired(
                "multiple archive generation intents require resolution")

        if archive_write_ops:
            op = archive_write_ops[0]
            old_logical = op["old_logical_sha256"]
            new_logical = op["new_logical_sha256"]
            if current_logical == old_logical:
                state = "conflict" if op["state"] == "prepared" else "failed"
                self.journal.abort_archive_write(op["operation_id"], state=state)
                # Aborting the child is only half of it.  A cutover child's id
                # is derived from its parent, so closing it spends the one child
                # that migration will ever be able to open.  Release the parent
                # in the same startup, or the pair is stuck.
                self._release_abandoned_cutover_parents()
                return
            if new_logical and current_logical == new_logical:
                if op["state"] == "prepared":
                    self.journal.begin_archive_write(op["operation_id"])
                authority = self._archive_write_authority(op)
                kwargs = {}
                if authority is not None:
                    kwargs = {"authority_status": authority[0],
                              "authority_snapshot": authority[1]}
                self.journal.complete_archive_write_operation(
                    op["operation_id"], observed_logical_sha256=current_logical,
                    **kwargs)
                if op["parent_operation_id"] is not None:
                    parent = self.journal.get_operation(op["parent_operation_id"])
                    if parent is not None and parent["kind"] == "migration":
                        self.journal.mark_migration_cutover(
                            parent["operation_id"], op["operation_id"])
                return
            self.journal.manual_latch(
                op["operation_id"], failure_code="startup_reconciliation_ambiguous",
                staged_db_sha256=op["staged_db_sha256"], preimage_sha256=None,
                preserved_artifacts={"staging_path": op["staging_path"],
                                     "preimage_path": op["preimage_path"],
                                     "observed_logical": current_logical})
            raise ManualRecoveryRequired(
                "archive-write intent has an ambiguous archive identity")

        if len(restore_ops) > 1:
            self._create_reconciliation_latch(
                snapshot, observed_logical=current_logical,
                reason="multiple_restore_intents",
                preserved_artifacts={"operation_ids": [
                    op["operation_id"] for op in restore_ops]})
            raise ManualRecoveryRequired("multiple restore intents require resolution")

        if restore_ops:
            op = restore_ops[0]
            old_logical = op["old_logical_sha256"]
            new_logical = op["new_logical_sha256"]
            if current_logical == old_logical:
                if op["state"] == "swapping":
                    self.journal.update_operation(
                        op["operation_id"], state="rolled_back")
                return
            if new_logical and current_logical == new_logical:
                if op["state"] == "prepared":
                    self.journal.update_operation(
                        op["operation_id"], state="swapping")
                self.journal.complete_generation_operation(
                    op["operation_id"], state="succeeded")
                return
            self.journal.manual_latch(
                op["operation_id"], failure_code="startup_reconciliation_ambiguous",
                staged_db_sha256=op["staged_db_sha256"], preimage_sha256=None,
                preserved_artifacts={"staging_path": op["staging_path"],
                                     "preimage_path": op["preimage_path"],
                                     "observed_logical": current_logical})
            raise ManualRecoveryRequired("restore intent has an ambiguous archive identity")

        # A raw/localStorage intent must not silently authorize an unrelated
        # archive mutation.  Its expected archive identity remains the only
        # permitted live identity until the raw operation is finalized.
        for op in active:
            if op["kind"] == "raw_restore":
                if current_logical != generation["logical_sha256"]:
                    self.journal.manual_latch(
                        op["operation_id"], failure_code="startup_reconciliation_ambiguous",
                        staged_db_sha256=None, preimage_sha256=None,
                        preserved_artifacts={"observed_logical": current_logical})
                    raise ManualRecoveryRequired(
                        "raw restore intent found an unrelated archive identity")

        if current_logical != generation["logical_sha256"]:
            self._create_reconciliation_latch(
                snapshot, observed_logical=current_logical,
                reason="untracked_archive_identity",
                preserved_artifacts={"expected_logical": generation["logical_sha256"]})
            raise ManualRecoveryRequired("archive identity changed without a journal intent")

    def _release_abandoned_cutover_parents(self) -> None:
        """Move a migration off `verified` once its one child is spent.

        A cutover child's operation id is `derive_archive_child_id(parent)`, so
        it is not merely *a* child — it is the only one that parent can ever
        open.  When startup reconciliation finds the live archive still on the
        old logical identity it closes that child as `conflict` (from
        `prepared`) or `failed` (from `applying`), which is right: those bytes
        never landed and must not be acknowledged.

        But the parent was left on `verified`.  That combination has no way
        forward.  The same preview replays into the same deterministic child id,
        which is now terminal; the same finalize re-derives the same child;
        and `retry_nonce` refuses, because it requires the attempt it is
        retrying to be `failed` or `source_changed` and `verified` is neither.
        So the migration could neither complete nor be abandoned — the user's
        data stayed in localStorage with no route out.

        Releasing the parent to `failed` is the option the state machine already
        allows (`verified -> failed` is an existing legal transition) and it is
        the one that restores liveness without reopening a spent child: a fresh
        `preview(retry_nonce=...)` then opens a new attempt, with a new
        operation id, a new deterministic child, and a new authority intent.

        Idempotent by construction, and safe against the crash in the middle of
        this pair: `cutover_marked` is terminal and has no transition out, so a
        parent whose cutover really succeeded can never be swept here.
        """
        for parent in self.journal.list_operations(kind="migration"):
            if parent["state"] != "verified":
                continue
            child = self.journal.get_operation(
                derive_archive_child_id(parent["operation_id"]))
            if child is None or child["state"] not in {"conflict", "failed"}:
                continue
            self.journal.update_operation(parent["operation_id"], state="failed")

    def _bootstrap(self) -> dict:
        self._reconcile_startup()
        logical, state = self._current_identity()
        return self.journal.ensure_bootstrap(logical_sha256=logical, state=state)

    @staticmethod
    def _archive_write_result(result: dict) -> dict:
        operation = result["operation"]
        generation = result["generation"]
        return {"operation_id": operation["operation_id"],
                "state": operation["state"],
                "generation_id": generation["generation_id"],
                "logical_sha256": generation["logical_sha256"],
                "authority_status": result["authority"]["status"]}

    def prepare_archive_write(
            self, *, idempotency_key: str, request_fingerprint: str,
            new_logical_sha256: str, new_generation_id: Optional[str] = None,
            parent_operation_id: Optional[str] = None,
            envelope_sha256: Optional[str] = None,
            staged_db_sha256: Optional[str] = None,
            staging_path: Optional[str] = None,
            authority_status: Optional[str] = None,
            authority_snapshot: Optional[dict] = None,
            authority_intent: Optional[dict] = None,
            external_request: Optional[dict] = None) -> dict:
        """Create a prebound archive-write intent for a deterministic image.

        The caller supplies the complete post-transaction logical identity.
        This method never guesses a hash after the archive write and never
        creates a live archive as a side effect of preparing the intent.

        When `authority_intent` is supplied this child is the cutover child of
        a migration.  Its operation id is then not random: it is the
        deterministic `archive_child_id` the intent already committed to, so a
        replay after a lost response resolves to the same child instead of
        opening a second one.  The intent's identity is recorded in the receipt
        payload here, before the archive transaction, which is what lets the
        acknowledgement hash stay out of the preimage entirely.
        """
        if (not isinstance(idempotency_key, str) or not idempotency_key
                or not isinstance(request_fingerprint, str)
                or not _hex64(request_fingerprint)
                or not _hex64(new_logical_sha256)):
            raise RecoveryError("archive-write identity is invalid")
        if envelope_sha256 is not None and not _hex64(envelope_sha256):
            raise RecoveryError("archive-write envelope hash is invalid")
        if staged_db_sha256 is not None and not _hex64(staged_db_sha256):
            raise RecoveryError("archive-write staged hash is invalid")
        normalized_authority = None
        if authority_status is not None:
            normalized_authority = RecoveryJournal._validate_authority_transition(
                authority_status, authority_snapshot)
        elif authority_snapshot is not None:
            raise RecoveryError("archive-write authority status is missing")
        intent_fields: dict[str, str] = {}
        if authority_intent is not None:
            if normalized_authority is None:
                raise RecoveryError(
                    "authority intent requires an authority transition")
            required = {"intent_id", "intent_receipt_sha256",
                        "intent_receipt_mac", "archive_child_id"}
            if not isinstance(authority_intent, dict) \
                    or not required.issubset(authority_intent):
                raise RecoveryError("authority intent binding is invalid")
            if (not _hex64(authority_intent["intent_receipt_sha256"])
                    or not _hex64(authority_intent["intent_receipt_mac"])
                    or not isinstance(authority_intent["intent_id"], str)
                    or not authority_intent["intent_id"]
                    or not isinstance(authority_intent["archive_child_id"], str)
                    or not authority_intent["archive_child_id"]):
                raise RecoveryError("authority intent binding is invalid")
            if parent_operation_id is None:
                raise RecoveryError(
                    "authority intent requires the migration parent")
            intent_fields = {
                "authority_intent_id": authority_intent["intent_id"],
                "authority_intent_receipt_sha256":
                    authority_intent["intent_receipt_sha256"],
                "authority_intent_receipt_mac":
                    authority_intent["intent_receipt_mac"],
            }
        external_fields: dict[str, Any] = {}
        if external_request is not None:
            required_external = {"request_kind", "request_id",
                                 "body_fingerprint", "object_id"}
            if not isinstance(external_request, dict) \
                    or set(external_request) != required_external:
                raise RecoveryError("archive-write external request is invalid")
            if (not isinstance(external_request["request_kind"], str)
                    or not external_request["request_kind"]
                    or not isinstance(external_request["request_id"], str)
                    or not external_request["request_id"]
                    or not _hex64(external_request["body_fingerprint"])):
                raise RecoveryError("archive-write external request is invalid")
            external_fields = {
                "external_request_kind": external_request["request_kind"],
                "external_request_id": external_request["request_id"],
                "external_body_fingerprint": external_request["body_fingerprint"],
                "external_object_id": external_request["object_id"],
            }
        replay_inputs = {
            "intent_fields": intent_fields,
            "authority_intent": authority_intent,
            "request_fingerprint": request_fingerprint,
            "new_logical_sha256": new_logical_sha256,
            "new_generation_id": new_generation_id,
            "parent_operation_id": parent_operation_id,
            "envelope_sha256": envelope_sha256,
            "staged_db_sha256": staged_db_sha256,
        }
        with self._lock:
            existing = self.journal.find_operation(idempotency_key)
            if (existing is not None
                    and existing["operation_id"] in self._local_archive_write_intents):
                # This fast path exists for a lost response inside one manager
                # and deliberately returns the original operation.  It used to
                # return it after checking only the three intent receipt fields,
                # so everything else about the request — the child id it names,
                # the parent, the generation, the logical identity, the envelope,
                # the staged bytes — could differ and still inherit this child.
                # It now runs the same verification as the slow path below.
                snapshot = self.journal.snapshot()
                self._assert_archive_write_replay(existing, snapshot, **replay_inputs)
                return self._archive_write_result({
                    "operation": existing,
                    "generation": snapshot["generation"],
                    "authority": snapshot["authority"],
                })
            active = [op for op in self.journal.active_operations()
                      if op["kind"] == "archive_write"]
            if active and not all(op["operation_id"]
                                  in self._local_archive_write_intents
                                  for op in active):
                # A fresh manager treats an orphaned intent as a startup
                # reconciliation case.  The manager that created it instead
                # reports the duplicate without aborting its in-flight work.
                self._bootstrap()
                active = [op for op in self.journal.active_operations()
                          if op["kind"] == "archive_write"]
            if active:
                if existing is None or existing["state"] not in {
                        "succeeded", "failed", "conflict"}:
                    raise RecoveryConflict("another archive-write intent is active")
            snapshot = self._bootstrap()
            existing = self.journal.find_operation(idempotency_key)
            if existing is not None:
                self._assert_archive_write_replay(existing, snapshot, **replay_inputs)
                return self._archive_write_result({
                    "operation": existing,
                    "generation": snapshot["generation"],
                    "authority": snapshot["authority"],
                })
            expected_generation = snapshot["generation"]["generation_id"]
            old_logical = snapshot["generation"]["logical_sha256"]
            if new_generation_id is None:
                new_generation_id = "gen-archive-write-" + uuid.uuid4().hex
            if new_generation_id == expected_generation:
                raise RecoveryError("archive-write generation must advance")
            # A cutover child inherits the id its intent already committed to;
            # only ordinary archive writes get a fresh one.
            operation_id = (authority_intent["archive_child_id"]
                            if authority_intent is not None
                            else "op_" + uuid.uuid4().hex)
            _secure_dir(self.archive_write_root)
            operation_root = self.archive_write_root / operation_id
            preimage = operation_root / "preimage"
            os.mkdir(operation_root, 0o700)
            os.mkdir(preimage, 0o700)
            payload = {}
            if normalized_authority is not None:
                payload = {"authority_status": authority_status,
                           "authority_snapshot": normalized_authority}
            payload.update(intent_fields)
            # Carried here, before the bytes land, and spent by the transaction
            # that makes them durable. `receipt_json` is write-once by trigger,
            # so what is recorded now is what the completion will read.
            payload.update(external_fields)
            receipt_json = _canonical(payload).decode("utf-8")
            try:
                operation = self.journal.create_operation(
                    operation_id=operation_id, kind="archive_write", state="prepared",
                    idempotency_key=idempotency_key,
                    request_fingerprint=request_fingerprint,
                    expected_generation=expected_generation,
                    new_generation_id=new_generation_id,
                    old_logical_sha256=old_logical,
                    new_logical_sha256=new_logical_sha256,
                    envelope_sha256=envelope_sha256,
                    staged_db_sha256=staged_db_sha256,
                    staging_path=staging_path,
                    preimage_path=str(preimage),
                    parent_operation_id=parent_operation_id,
                    receipt_json=receipt_json)
            except Exception:
                shutil.rmtree(operation_root, ignore_errors=True)
                raise
            self._local_archive_write_intents.add(operation_id)
            return {"operation_id": operation_id, "state": operation["state"],
                    "expected_generation": expected_generation,
                    "old_logical_sha256": old_logical,
                    "new_generation_id": new_generation_id,
                    "new_logical_sha256": new_logical_sha256,
                    "preimage_path": str(preimage)}

    @staticmethod
    def _recorded_intent_fields(existing: dict) -> dict:
        """The authority-intent marker an existing child's payload carries."""
        try:
            payload = json.loads(existing.get("receipt_json") or "{}")
        except (TypeError, ValueError) as exc:
            raise RecoveryConflict(
                "archive-write idempotency payload is unreadable") from exc
        if not isinstance(payload, dict):
            raise RecoveryConflict("archive-write idempotency payload is unreadable")
        return {key: payload[key] for key in _INTENT_PAYLOAD_KEYS
                if payload.get(key) is not None}

    def _assert_archive_write_replay(
            self, existing: dict, snapshot: dict, *, intent_fields: dict,
            authority_intent: Optional[dict], request_fingerprint: str,
            new_logical_sha256: str, new_generation_id: Optional[str],
            parent_operation_id: Optional[str],
            envelope_sha256: Optional[str],
            staged_db_sha256: Optional[str]) -> None:
        """Refuse a replay that would inherit a child it does not describe.

        Two callers reach the same durable child: an ordinary archive write
        retrying after a lost response, and a migration cutover doing the same.
        They cannot be checked the same way.

        An ordinary write is prepared from the caller's body alone, so several
        of these fields are legitimately absent and the pre-existing lenient
        contract — compare what was supplied, ignore what was not — is kept
        verbatim for it.

        A cutover is different in kind.  Its child id is not random: it is the
        deterministic `archive_child_id` its intent already committed to, and
        the intent fixes the parent, the generation pair, the logical identity,
        the envelope, and the staged bytes before the child exists.  Every one
        of those is therefore knowable at replay time, so every one is compared,
        and a difference in any of them is a different operation wearing a
        reused key rather than a retry of this one.

        The marked/unmarked distinction is checked first and is deliberately
        asymmetric in both directions.  A cutover child replayed *without* its
        intent used to fall through the old guard's `if not intent_fields:
        return` and be handed back as though it were an ordinary archive write —
        which would let a caller spend a cutover's already-bound child on
        unrelated bytes.  The inverse, an ordinary child replayed *with* an
        intent, would adopt a child that no intent ever committed to.  Neither
        is a retry, so neither is served.
        """
        recorded = self._recorded_intent_fields(existing)
        if bool(recorded) != bool(intent_fields):
            raise RecoveryConflict(
                "archive-write idempotency fingerprint conflicts")

        generation = snapshot["generation"]
        if not intent_fields:
            lenient = {
                "kind": "archive_write",
                "request_fingerprint": request_fingerprint,
                "expected_generation": generation["generation_id"],
                "old_logical_sha256": generation["logical_sha256"],
                "new_logical_sha256": new_logical_sha256,
                "new_generation_id": new_generation_id,
                "parent_operation_id": parent_operation_id,
            }
            for key, expected in lenient.items():
                if expected is not None and existing.get(key) != expected:
                    raise RecoveryConflict(
                        "archive-write idempotency fingerprint conflicts")
            return

        if recorded != intent_fields:
            raise RecoveryConflict(
                "archive-write idempotency fingerprint conflicts")
        # The child id is part of the intent's own commitment, so a replay that
        # names a different one is not describing this child at all.
        if (not isinstance(authority_intent, dict)
                or existing.get("operation_id")
                != authority_intent.get("archive_child_id")
                or existing.get("operation_id")
                != derive_archive_child_id(str(parent_operation_id))):
            raise RecoveryConflict(
                "archive-write idempotency fingerprint conflicts")
        strict = {
            "kind": "archive_write",
            "request_fingerprint": request_fingerprint,
            "parent_operation_id": parent_operation_id,
            "new_generation_id": new_generation_id,
            "new_logical_sha256": new_logical_sha256,
            "envelope_sha256": envelope_sha256,
            "staged_db_sha256": staged_db_sha256,
        }
        for key, expected in strict.items():
            if existing.get(key) != expected:
                raise RecoveryConflict(
                    "archive-write idempotency fingerprint conflicts")
        # The row's own generation binding must still describe the live epoch.
        # Which end of the binding to compare depends on whether this child has
        # already spent it: after a successful cutover the epoch it created *is*
        # the current one, and a lost-response replay must not read that as
        # drift.
        if existing.get("new_generation_id") == generation["generation_id"]:
            bound = (existing.get("new_generation_id"),
                     existing.get("new_logical_sha256"))
        else:
            bound = (existing.get("expected_generation"),
                     existing.get("old_logical_sha256"))
        if bound != (generation["generation_id"], generation["logical_sha256"]):
            raise RecoveryConflict(
                "archive-write idempotency fingerprint conflicts")

    def apply_archive_write(
            self, operation_id: str, apply: Callable[[Path], None], *,
            close_store: Optional[Callable[[], None]] = None,
            reopen_store: Optional[Callable[[], Any]] = None) -> dict:
        """Apply and acknowledge one archive-write intent with rollback.

        ``apply`` is the archive transaction seam.  It must commit the exact
        image whose logical identity was prebound by ``prepare_archive_write``.
        The method owns the writer lease, captures a journal-bound preimage,
        performs post-commit readback, and only then advances the external
        generation.  A failed compensation becomes the existing sticky
        manual-recovery latch.
        """
        if not callable(apply):
            raise RecoveryError("archive-write callback is not callable")
        with self._lock:
            operation = self.journal.get_operation(operation_id)
            if operation is None or operation["kind"] != "archive_write":
                raise RecoveryError("archive-write operation is unknown")
            if operation["state"] == "succeeded":
                return {"operation_id": operation_id, "state": "succeeded",
                        "generation_id": operation["new_generation_id"],
                        "logical_sha256": operation["new_logical_sha256"]}
            if operation["state"] == "applying":
                # A restart or a previous lost response may already have
                # committed the archive.  Reconciliation is authoritative.
                self._bootstrap()
                operation = self.journal.get_operation(operation_id)
                if operation["state"] == "succeeded":
                    return {"operation_id": operation_id, "state": "succeeded",
                            "generation_id": operation["new_generation_id"],
                            "logical_sha256": operation["new_logical_sha256"]}
            if operation["state"] != "prepared":
                raise RecoveryConflict("archive-write operation is not ready")

            # A freshly prepared operation is intentionally not passed through
            # startup reconciliation: its old identity is expected until this
            # call begins the archive transaction.  We still perform the same
            # generation and authority checks before changing any bytes.
            current_logical, _current_state = self._current_identity()
            snapshot = self.journal.snapshot()
            if snapshot["authority"]["status"] == "manual_recovery_required":
                raise ManualRecoveryRequired("manual recovery latch is active")
            if (snapshot["generation"]["generation_id"]
                    != operation["expected_generation"]
                    or snapshot["generation"]["logical_sha256"]
                    != operation["old_logical_sha256"]
                    or current_logical != operation["old_logical_sha256"]):
                self.journal.abort_archive_write(operation_id, state="conflict")
                raise RecoveryConflict("archive-write generation changed before apply")
            self.journal.begin_archive_write(operation_id)
            operation = self.journal.get_operation(operation_id)
            preimage = Path(operation["preimage_path"])
            _secure_dir(preimage)
            had_archive = self.archive_path.exists()
            preimage_ready = not had_archive
            writer_lock = None
            mutation_started = False
            try:
                if close_store is not None:
                    close_store()
                writer_lock = PERSISTENCE._acquire_writer_lock(str(self.archive_path))
                if had_archive:
                    preimage_file = preimage / self.archive_path.name
                    if preimage_file.exists():
                        if logical_identity(str(preimage_file)) != operation["old_logical_sha256"]:
                            raise ManualRecoveryRequired(
                                "archive-write preimage identity is not bound")
                    else:
                        _sqlite_backup(self.archive_path, preimage_file)
                    preimage_ready = True
                mutation_started = True
                apply(self.archive_path)
                if self.archive_path.exists():
                    _checkpoint_delete_journal(self.archive_path)
                    observed = logical_identity(str(self.archive_path))
                else:
                    observed = ABSENT_LOGICAL_SHA256
                if observed != operation["new_logical_sha256"]:
                    raise RecoveryConflict(
                        "archive-write post-commit identity does not match intent")
                if reopen_store is not None:
                    reopen_store()
                authority = self._archive_write_authority(operation)
                kwargs = {}
                if authority is not None:
                    kwargs = {"authority_status": authority[0],
                              "authority_snapshot": authority[1]}
                result = self.journal.complete_archive_write_operation(
                    operation_id, observed_logical_sha256=observed, **kwargs)
                if operation["parent_operation_id"] is not None:
                    parent = self.journal.get_operation(operation["parent_operation_id"])
                    if parent is not None and parent["kind"] == "migration":
                        self.journal.mark_migration_cutover(
                            parent["operation_id"], operation_id)
                return self._archive_write_result(result)
            except Exception as exc:  # noqa: BLE001
                rollback_needed = mutation_started and (preimage_ready or not had_archive)
                try:
                    if rollback_needed:
                        self._rollback(preimage=preimage, had_archive=had_archive,
                                       close_store=close_store,
                                       reopen_store=reopen_store)
                except Exception as rollback_exc:  # noqa: BLE001
                    failure = self.journal.manual_latch(
                        operation_id, failure_code="rollback_failed",
                        staged_db_sha256=operation["staged_db_sha256"],
                        preimage_sha256=_sha256_json(
                            _artifact_hashes([preimage / self.archive_path.name])),
                        preserved_artifacts={"preimage_path": str(preimage),
                                             "staging_path": operation["staging_path"],
                                             "preimage": str(preimage),
                                             "staging": operation["staging_path"]})
                    raise ManualRecoveryRequired(
                        "archive-write rollback failed; manual recovery is required") \
                        from rollback_exc
                if self.journal.get_operation(operation_id)["state"] == "applying":
                    self.journal.abort_archive_write(operation_id, state="failed")
                if isinstance(exc, (RecoveryError, ManualRecoveryRequired)):
                    raise
                raise RecoveryError("archive-write failed and was rolled back") from exc
            finally:
                if writer_lock is not None:
                    writer_lock.release()

    @staticmethod
    def _verify_failure_receipt(receipt: dict, secret: bytes) -> dict:
        try:
            body = json.loads(receipt["body_json"])
            body_json = _canonical(body)
        except (KeyError, TypeError, ValueError, RecoveryError) as exc:
            raise ManualRecoveryRequired("manual failure receipt is malformed") from exc
        if (_sha256(body_json) != receipt["receipt_sha256"]
                or not hmac.compare_digest(
                    hmac.new(secret, body_json, hashlib.sha256).hexdigest(),
                    receipt["receipt_mac"])):
            raise ManualRecoveryRequired("manual failure receipt MAC is invalid")
        return body

    def resolve_manual(self, operation_id: str, *, artifact: str,
                       expected_generation: str,
                       close_store: Optional[Callable[[], None]] = None,
                       reopen_store: Optional[Callable[[], Any]] = None) -> dict:
        """Resolve only a journal-preserved restore artifact through a CAS."""
        if artifact not in {"preimage", "staging"}:
            raise ManualRecoveryRequired("recovery artifact is not journal-preserved")
        with self._lock:
            op = self.journal.get_operation(operation_id)
            if op is None or op["state"] != "manual_recovery_required":
                raise RecoveryError("manual recovery operation is unknown or terminal")
            failure = self.journal.failure_receipt(operation_id)
            if failure is None:
                raise ManualRecoveryRequired("manual failure receipt is missing")
            body = self._verify_failure_receipt(failure, self.journal._secret)
            try:
                preserved = json.loads(failure["preserved_artifact_json"])
            except (TypeError, ValueError) as exc:
                raise ManualRecoveryRequired("preserved artifact record is invalid") from exc
            expected_path = (op["preimage_path"] if artifact == "preimage"
                             else op["staging_path"])
            recorded_path = preserved.get("preimage_path" if artifact == "preimage"
                                          else "staging_path")
            if recorded_path is None:
                recorded_path = preserved.get("preimage" if artifact == "preimage"
                                              else "staging")
            if recorded_path is not None and recorded_path != expected_path:
                raise ManualRecoveryRequired("preserved artifact is not bound to the journal")
            if expected_generation != op["expected_generation"]:
                raise RecoveryConflict("manual recovery generation does not match")
            snapshot = self.journal.snapshot()
            if (snapshot["generation"] is None
                    or snapshot["authority"] is None
                    or snapshot["authority"]["status"] != "manual_recovery_required"
                    or snapshot["generation"]["generation_id"] != expected_generation):
                raise ManualRecoveryRequired("manual recovery CAS root is not active")
            events = [event for event in self.journal.authority_events_for_operation(
                operation_id) if event["to_status"] == "manual_recovery_required"]
            if len(events) != 1 or events[0]["receipt_sha256"] != failure["receipt_sha256"] \
                    or events[0]["receipt_mac"] != failure["receipt_mac"]:
                raise ManualRecoveryRequired("manual authority event is not bound")
            authority_event = events[0]
            target_authority_status = authority_event["from_status"]
            if target_authority_status not in {
                    "legacy_authoritative", "sqlite_preferred", "source_changed"}:
                raise ManualRecoveryRequired("manual authority exit is not derivable")
            authority_snapshot = {
                "envelope_sha256": authority_event["envelope_sha256"],
                "target_count": int(authority_event["target_count"]),
                "target_hash": authority_event["target_hash"],
                "legacy_digest_last_seen": authority_event["legacy_digest_last_seen"],
            }
            current_logical, _state = self._current_identity()
            allowed_current = {op["old_logical_sha256"]}
            if op["new_logical_sha256"]:
                allowed_current.add(op["new_logical_sha256"])
            if current_logical not in allowed_current:
                raise ManualRecoveryRequired(
                    "current archive identity is not a journal-bound recovery branch")

            source_dir = Path(expected_path or "")
            source_archive = (source_dir / self.archive_path.name
                              if artifact == "preimage"
                              else source_dir)
            if artifact == "staging":
                source_archive = Path(expected_path or "")
            source_present = source_archive.exists()
            if source_present:
                source_logical = logical_identity(str(source_archive))
                target_logical = source_logical
                expected_target = op["old_logical_sha256"] if artifact == "preimage" \
                    else op["new_logical_sha256"]
                if not expected_target or target_logical != expected_target:
                    raise ManualRecoveryRequired("preserved archive hash is not journal-bound")
            else:
                if artifact == "staging" or any(
                        (source_dir / (self.archive_path.name + suffix)).exists()
                        for suffix in ("-wal", "-shm")):
                    raise ManualRecoveryRequired("preserved archive artifact is incomplete")
                target_logical = ABSENT_LOGICAL_SHA256
                expected_target = op["old_logical_sha256"] if artifact == "preimage" \
                    else op["new_logical_sha256"]
                if expected_target != ABSENT_LOGICAL_SHA256:
                    raise ManualRecoveryRequired("absent preserved artifact is not journal-bound")

            recovery_id = "op_" + uuid.uuid4().hex
            recovery_op = self.journal.create_operation(
                operation_id=recovery_id, kind="recovery", state="resolving",
                idempotency_key="resolve:" + operation_id + ":" + artifact,
                request_fingerprint=_sha256_json({
                    "kind": "manual_resolution", "operation_id": operation_id,
                    "artifact": artifact, "expected_generation": expected_generation,
                }),
                expected_generation=expected_generation,
                new_generation_id="gen-recovery-" + uuid.uuid4().hex,
                old_logical_sha256=current_logical,
                new_logical_sha256=target_logical,
                package_id=op["package_id"], staging_path=expected_path)
            self.journal.update_operation(recovery_id, state="applying")
            changed = current_logical != target_logical
            try:
                if changed:
                    if close_store is not None:
                        close_store()
                    for suffix in ("", "-wal", "-shm"):
                        installed = Path(str(self.archive_path) + suffix)
                        if installed.exists():
                            info = installed.lstat()
                            if stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
                                raise RecoveryError("installed recovery artifact is unsafe")
                            installed.unlink()
                    if source_present:
                        _write_new(self.archive_path,
                                   _read_regular(source_archive, MAX_ARCHIVE_BYTES))
                    _fsync_dir(self.archive_path.parent)
                    if reopen_store is not None and source_present:
                        reopen_store()
                    if source_present and logical_identity(str(self.archive_path)) != target_logical:
                        raise RecoveryError("resolved archive failed post-open validation")
                    if not source_present and self.archive_path.exists():
                        raise RecoveryError("resolved absent artifact left an archive")
                self.journal.complete_recovery_operation(
                    recovery_id, authority_status=target_authority_status,
                    authority_snapshot=authority_snapshot)
                return {"operation_id": operation_id, "resolution_operation_id": recovery_id,
                        "state": "resolved", "artifact": artifact,
                        "generation_id": self.journal.snapshot()["generation"]["generation_id"],
                        "logical_sha256": target_logical}
            except Exception as exc:  # noqa: BLE001
                try:
                    if self.journal.get_operation(recovery_id)["state"] == "applying":
                        self.journal.manual_latch(
                            recovery_id, failure_code="artifact_mismatch",
                            staged_db_sha256=None, preimage_sha256=None,
                            preserved_artifacts={"source": str(source_archive),
                                                 "artifact": artifact,
                                                 "parent_operation_id": operation_id})
                except Exception:
                    pass
                if isinstance(exc, ManualRecoveryRequired):
                    raise
                raise ManualRecoveryRequired(
                    "manual recovery resolution did not complete") from exc

    @staticmethod
    def _projection_member(projection: Optional[dict]) -> dict:
        if projection is None:
            return {
                "format": "fire-migration-projection-v1",
                "projection_version": PROJECTION_VERSION,
                "normalizer_version": MIGRATION.NORMALIZER_VERSION,
                "config_schema_version": 2,
                "records": [], "legacy_checkin_evidence": [],
                "recovered_drafts": [], "errors": [],
            }
        if "_canonical_envelope" in projection:
            projection = MIGRATION.public_projection(projection)
        return projection

    def _build_manifest(self, *, archive_bytes: bytes, envelope_bytes: bytes,
                        projection_bytes: bytes, source_schema: str,
                        database_state: str, generation: dict,
                        authority: dict) -> tuple[dict, bytes, str, str]:
        authority_snapshot = {
            "status": authority["status"],
            "operation_id": authority["operation_id"],
            "operation_kind": authority["operation_kind"],
            "envelope_sha256": authority["envelope_sha256"],
            "target_count": authority["target_count"],
            "target_hash": authority["target_hash"],
            "legacy_digest_last_seen": authority["legacy_digest_last_seen"],
            "generation_id": generation["generation_id"],
            "generation_state": generation["state"],
            "receipt_sha256": None,
        }
        members = {
            "archive.sqlite3": {"bytes": len(archive_bytes),
                                "sha256": _sha256(archive_bytes)},
            "localstorage-envelope.json": {"bytes": len(envelope_bytes),
                                             "sha256": _sha256(envelope_bytes)},
            "projection.json": {"bytes": len(projection_bytes),
                                 "sha256": _sha256(projection_bytes)},
            "ready": {"bytes": 0, "sha256": _sha256(b"")},
        }
        core = {
            "format": PACKAGE_FORMAT, "backup_id": None,
            "manifest_core_sha256": None, "source_schema_version": source_schema,
            "target_schema_version": _package_target_schema_version(source_schema),
            "database_state": database_state, "members": members,
            "envelope_sha256": _sha256(envelope_bytes),
            "projection_version": PROJECTION_VERSION,
            "normalizer_version": MIGRATION.NORMALIZER_VERSION,
            "config_schema_version": "2",
            "captured_generation": generation["generation_id"],
            "authority_snapshot": authority_snapshot,
            "source_identity": {"app_build": self.app_release_id,
                                 "engine": "fire-engine-v9.8"},
            "created_at": _millis_now(),
            "exclusions": ["webkit-private-storage", "browser-cache", "cloud-state"],
        }
        core_hash = _sha256_json(core)
        preimage = _lp(PACKAGE_FORMAT.encode("utf-8")) + _lp(core_hash.encode("ascii"))
        member_hashes = {"manifest.json": None,
                         "archive.sqlite3": members["archive.sqlite3"]["sha256"],
                         "localstorage-envelope.json": members["localstorage-envelope.json"]["sha256"],
                         "projection.json": members["projection.json"]["sha256"],
                         "ready": members["ready"]["sha256"]}
        # The manifest member hash is not circular: it is omitted from the
        # stored member map and uses the core hash as its preimage value.
        for name in PACKAGE_MEMBERS:
            value = core_hash if name == "manifest.json" else member_hashes[name]
            preimage += _lp(name.encode("utf-8")) + _lp(value.encode("ascii"))
        backup_id = "bkp_" + _sha256(preimage)
        manifest = dict(core)
        manifest["backup_id"] = backup_id
        manifest["manifest_core_sha256"] = core_hash
        manifest_bytes = _canonical(manifest)
        package_preimage = _lp(manifest_bytes)
        package_preimage += b"".join(_lp(value) for value in (
            archive_bytes, envelope_bytes, projection_bytes, b""))
        return manifest, manifest_bytes, backup_id, _sha256(package_preimage)

    def _capture_staging(self, *, envelope: dict, projection: Optional[dict],
                         snapshot: dict) -> dict:
        _secure_dir(self.backup_root)
        stage = self.backup_root / (".staging-" + uuid.uuid4().hex)
        os.mkdir(stage, 0o700)
        try:
            archive_stage = stage / "archive.sqlite3"
            if self.archive_path.exists():
                _sqlite_backup(self.archive_path, archive_stage)
                database_state = "present"
            else:
                _empty_archive(archive_stage)
                database_state = "absent_at_capture"
            archive_bytes = _read_regular(archive_stage, MAX_ARCHIVE_BYTES)
            source_schema = _archive_schema_version(archive_stage)
            envelope_bytes = _canonical(MIGRATION.validate_envelope(envelope))
            projection_bytes = _canonical(self._projection_member(projection))
            if len(envelope_bytes) > MAX_ENVELOPE_BYTES:
                raise RecoveryError("envelope is too large")
            if len(projection_bytes) > MAX_PROJECTION_BYTES:
                raise RecoveryError("projection is too large")
            manifest, manifest_bytes, backup_id, package_sha = self._build_manifest(
                archive_bytes=archive_bytes, envelope_bytes=envelope_bytes,
                projection_bytes=projection_bytes, source_schema=source_schema,
                database_state=database_state, generation=snapshot["generation"],
                authority=snapshot["authority"])
            _write_new(stage / "localstorage-envelope.json", envelope_bytes)
            _write_new(stage / "projection.json", projection_bytes)
            _write_new(stage / "manifest.json", manifest_bytes)
            _fsync_dir(stage)
            return {"stage": stage, "backup_id": backup_id,
                    "manifest": manifest, "manifest_bytes": manifest_bytes,
                    "package_sha256": package_sha,
                    "archive_sha256": manifest["members"]["archive.sqlite3"]["sha256"],
                    "envelope_sha256": manifest["envelope_sha256"],
                    "projection_sha256": manifest["members"]["projection.json"]["sha256"],
                    "source_schema_version": source_schema,
                    "package_dir": self.backup_root / backup_id}
        except Exception:
            shutil.rmtree(stage, ignore_errors=True)
            raise

    def prepare_raw_restore(self, backup_id: str,
                            current_envelope: dict) -> dict:
        """Persist the exact browser preimage before any UI key mutation."""
        with self._lock:
            package = self.journal.get_package(backup_id)
            if package is None or package["state"] != "ready":
                raise RecoveryError("backup package is not ready")
            manifest, members = _validate_complete_package(
                Path(package["package_dir"]))
            target = MIGRATION.validate_envelope(
                json.loads(members["localstorage-envelope.json"].decode("utf-8")))
            preimage = MIGRATION.validate_envelope(current_envelope)
            target_sha = MIGRATION.envelope_sha256(target)
            preimage_sha = MIGRATION.envelope_sha256(preimage)
            snapshot = self._bootstrap()
            expected_generation = snapshot["generation"]["generation_id"]
            idempotency = "raw_restore:" + backup_id + ":" + expected_generation \
                + ":" + preimage_sha
            existing = self.journal.find_operation(idempotency)
            if existing is not None:
                raw = self.journal.get_raw_restore(existing["operation_id"])
                if (raw is None or raw["target_sha256"] != target_sha
                        or raw["preimage_sha256"] != preimage_sha):
                    raise RecoveryConflict("raw restore retry identity changed")
                return {
                    "operation_id": existing["operation_id"],
                    "state": existing["state"], "backup_id": backup_id,
                    "expected_generation": expected_generation,
                    "target_envelope": target,
                    "target_sha256": target_sha,
                    "preimage_sha256": preimage_sha,
                    "key_order": list(MIGRATION.ALLOWED_KEYS),
                }
            operation_id = "op_" + uuid.uuid4().hex
            self.journal.create_operation(
                operation_id=operation_id, kind="raw_restore", state="prepared",
                idempotency_key=idempotency,
                request_fingerprint=_sha256_json({
                    "kind": "raw_restore", "backup_id": backup_id,
                    "expected_generation": expected_generation,
                    "target_sha256": target_sha, "preimage_sha256": preimage_sha,
                }),
                expected_generation=expected_generation,
                old_logical_sha256=snapshot["generation"]["logical_sha256"],
                package_id=backup_id, envelope_sha256=target_sha)
            self.journal.create_raw_restore(
                operation_id=operation_id, target_envelope=target,
                preimage_envelope=preimage,
                expected_generation=expected_generation)
            return {
                "operation_id": operation_id, "state": "prepared",
                "backup_id": backup_id, "expected_generation": expected_generation,
                "target_envelope": target, "target_sha256": target_sha,
                "preimage_sha256": preimage_sha,
                "key_order": list(MIGRATION.ALLOWED_KEYS),
            }

    def finalize_raw_restore(self, operation_id: str,
                             readback_envelope: dict) -> dict:
        """Classify a complete browser readback as target, preimage, or third."""
        with self._lock:
            op = self.journal.get_operation(operation_id)
            raw = self.journal.get_raw_restore(operation_id)
            if op is None or raw is None or op["kind"] != "raw_restore":
                raise RecoveryError("raw restore operation is unknown")
            readback = MIGRATION.validate_envelope(readback_envelope)
            readback_sha = MIGRATION.envelope_sha256(readback)
            if op["state"] in {"succeeded", "rolled_back"}:
                outcome = self.journal.get_raw_restore_outcome(operation_id)
                if outcome is None or outcome["readback_sha256"] != readback_sha:
                    raise RecoveryConflict("raw restore finalize is not idempotent")
                return outcome
            if op["state"] == "manual_recovery_required":
                raise ManualRecoveryRequired("raw restore manual latch is active")
            if readback_sha == raw["target_sha256"]:
                return self.journal.complete_raw_restore(
                    operation_id, outcome_kind="succeeded",
                    readback_envelope=readback)
            if readback_sha == raw["preimage_sha256"]:
                return self.journal.complete_raw_restore(
                    operation_id, outcome_kind="rolled_back",
                    readback_envelope=readback)
            self.journal.raw_manual_latch(
                operation_id, readback_envelope=readback,
                reason="readback_matches_neither_target_nor_preimage")
            raise ManualRecoveryRequired(
                "raw restore readback is neither the target nor the exact preimage")

    def prepare_backup(self, envelope: dict, *, projection: Optional[dict] = None) -> dict:
        with self._lock:
            snapshot = self._bootstrap()
            envelope = MIGRATION.validate_envelope(envelope)
            envelope_hash = MIGRATION.envelope_sha256(envelope)
            generation_id = snapshot["generation"]["generation_id"]
            idempotency = "backup:" + envelope_hash + ":" + generation_id
            fingerprint = _sha256_json({"kind": "backup", "envelope_sha256": envelope_hash,
                                       "expected_generation": generation_id})
            existing = self.journal.find_operation(idempotency)
            if existing is not None:
                return {"operation_id": existing["operation_id"],
                        "state": existing["state"], "package_id": existing["package_id"],
                        "generation_id": existing["expected_generation"]}
            artifact = self._capture_staging(
                envelope=envelope, projection=projection, snapshot=snapshot)
            self.journal.add_package(
                backup_id=artifact["backup_id"], package_dir=str(artifact["package_dir"]),
                manifest_core_sha256=artifact["manifest"]["manifest_core_sha256"],
                manifest_final_sha256=_sha256(artifact["manifest_bytes"]),
                package_sha256=artifact["package_sha256"],
                source_schema_version=artifact["source_schema_version"],
                captured_generation=generation_id,
                archive_sha256=artifact["archive_sha256"],
                envelope_sha256=artifact["envelope_sha256"],
                projection_sha256=artifact["projection_sha256"])
            operation_id = "op_" + uuid.uuid4().hex
            self.journal.create_operation(
                operation_id=operation_id, kind="backup", state="prepared",
                idempotency_key=idempotency, request_fingerprint=fingerprint,
                expected_generation=generation_id,
                old_logical_sha256=snapshot["generation"]["logical_sha256"],
                package_id=artifact["backup_id"], envelope_sha256=envelope_hash,
                staging_path=str(artifact["stage"]))
            return {"operation_id": operation_id, "state": "prepared",
                    "package_id": artifact["backup_id"],
                    "generation_id": generation_id, "envelope_sha256": envelope_hash}

    def finalize_backup(self, operation_id: str, envelope: dict) -> dict:
        with self._lock:
            op = self.journal.get_operation(operation_id)
            if op is None or op["kind"] != "backup":
                raise RecoveryError("unknown backup operation")
            if op["state"] == "succeeded":
                return {"operation_id": operation_id, "state": op["state"],
                        "package_id": op["package_id"]}
            if op["state"] != "prepared":
                raise RecoveryConflict("backup operation is already terminal")
            envelope = MIGRATION.validate_envelope(envelope)
            if MIGRATION.envelope_sha256(envelope) != op["envelope_sha256"]:
                self.journal.update_operation(operation_id, state="conflict")
                self.journal.set_package_state(op["package_id"], "invalid")
                raise RecoveryConflict("backup envelope changed before finalize")
            snapshot = self._bootstrap()
            if snapshot["generation"]["generation_id"] != op["expected_generation"]:
                self.journal.update_operation(operation_id, state="conflict")
                self.journal.set_package_state(op["package_id"], "invalid")
                raise RecoveryConflict("archive generation changed before finalize")
            stage = Path(op["staging_path"])
            staged = _staged_member_bytes(stage)
            _parse_manifest(staged["manifest.json"])
            ready = stage / "ready"
            _write_new(ready, b"")
            _fsync_dir(stage)
            final_dir = self.backup_root / op["package_id"]
            if final_dir.exists():
                existing_manifest, existing = _validate_complete_package(final_dir)
                if existing["manifest.json"] != staged["manifest.json"]:
                    raise RecoveryError("backup ID collision")
                shutil.rmtree(stage, ignore_errors=True)
            else:
                os.rename(stage, final_dir)
                _fsync_dir(self.backup_root)
            self.journal.set_package_state(op["package_id"], "ready")
            self.journal.update_operation(operation_id, state="succeeded")
            package = self.journal.get_package(op["package_id"])
            return {"operation_id": operation_id, "state": "succeeded",
                    "package_id": op["package_id"], "package": package}

    @staticmethod
    def _authority_matches(manifest: dict, snapshot: dict) -> bool:
        packaged = manifest["authority_snapshot"]
        current = snapshot["authority"]
        if (packaged["generation_id"] != snapshot["generation"]["generation_id"]
                or packaged["generation_state"] != snapshot["generation"]["state"]
                or manifest["captured_generation"] != snapshot["generation"]["generation_id"]):
            return False
        for key in ("status", "operation_id", "operation_kind", "envelope_sha256",
                    "target_count", "target_hash", "legacy_digest_last_seen"):
            if packaged[key] != current[key]:
                return False
        return True

    def prepare_restore(self, backup_id: str) -> dict:
        with self._lock:
            package = self.journal.get_package(backup_id)
            if package is None or package["state"] != "ready":
                raise RecoveryError("backup package is not ready")
            package_dir = Path(package["package_dir"])
            manifest, members = _validate_complete_package(package_dir)
            snapshot = self._bootstrap()
            expected_generation = snapshot["generation"]["generation_id"]
            old_logical = snapshot["generation"]["logical_sha256"]
            idempotency = "restore:" + backup_id + ":" + expected_generation
            existing = self.journal.find_operation(idempotency)
            if existing is not None:
                return {"operation_id": existing["operation_id"],
                        "state": existing["state"], "backup_id": backup_id,
                        "expected_generation": existing["expected_generation"],
                        "new_generation_id": existing["new_generation_id"],
                        "new_logical_sha256": existing["new_logical_sha256"]}
            fingerprint = _sha256_json({"kind": "restore", "backup_id": backup_id,
                                       "expected_generation": expected_generation})
            operation_id = "op_" + uuid.uuid4().hex
            _secure_dir(self.restore_root)
            stage = self.restore_root / operation_id
            preimage = self.restore_root / (operation_id + ".preimage")
            os.mkdir(stage, 0o700)
            os.mkdir(preimage, 0o700)
            self.journal.create_operation(
                operation_id=operation_id, kind="restore", state="prepared",
                idempotency_key=idempotency, request_fingerprint=fingerprint,
                expected_generation=expected_generation,
                new_generation_id="gen-restore-" + uuid.uuid4().hex,
                old_logical_sha256=old_logical, package_id=backup_id,
                envelope_sha256=manifest["envelope_sha256"],
                staging_path=str(stage / "archive.sqlite3"),
                preimage_path=str(preimage))
            try:
                _write_new(stage / "archive.sqlite3", members["archive.sqlite3"])
                _migrate_stage_to_target(stage / "archive.sqlite3",
                                         manifest["target_schema_version"])
                staged_hash = _sha256(_read_regular(
                    stage / "archive.sqlite3", MAX_ARCHIVE_BYTES))
                new_logical = (ABSENT_LOGICAL_SHA256
                               if manifest["database_state"] == "absent_at_capture"
                               else logical_identity(str(stage / "archive.sqlite3")))
                self.journal.update_operation(
                    operation_id, staged_db_sha256=staged_hash,
                    new_logical_sha256=new_logical)
                if not self._authority_matches(manifest, snapshot):
                    self.journal.manual_latch(
                        operation_id, failure_code="authority_mismatch",
                        staged_db_sha256=staged_hash, preimage_sha256=None,
                        preserved_artifacts={"package_dir": str(package_dir),
                                             "staging_path": str(stage)})
                    raise ManualRecoveryRequired("package authority snapshot differs")
                return {"operation_id": operation_id, "state": "prepared",
                        "backup_id": backup_id,
                        "expected_generation": expected_generation,
                        "new_generation_id": self.journal.get_operation(
                            operation_id)["new_generation_id"],
                        "staged_db_sha256": staged_hash,
                        "new_logical_sha256": new_logical,
                        "database_state": manifest["database_state"]}
            except ManualRecoveryRequired:
                raise
            except Exception as exc:  # noqa: BLE001
                try:
                    self.journal.update_operation(operation_id, state="failed")
                except Exception:
                    pass
                shutil.rmtree(stage, ignore_errors=True)
                shutil.rmtree(preimage, ignore_errors=True)
                if isinstance(exc, RecoveryError):
                    raise
                raise RecoveryError("restore staging failed") from exc

    @staticmethod
    def _safe_move(source: Path, destination: Path) -> bool:
        if not source.exists():
            return False
        info = source.lstat()
        if (stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1 or info.st_uid != os.getuid()):
            raise RecoveryError("archive artifact is not movable")
        os.replace(str(source), str(destination))
        return True

    def _rollback(self, *, preimage: Path, had_archive: bool,
                  close_store: Optional[Callable[[], None]],
                  reopen_store: Optional[Callable[[], Any]]) -> None:
        if close_store is not None:
            close_store()
        for suffix in ("", "-wal", "-shm"):
            installed = Path(str(self.archive_path) + suffix)
            if installed.exists():
                info = installed.lstat()
                if stat.S_ISLNK(info.st_mode) or info.st_nlink != 1:
                    raise RecoveryError("rollback found unsafe installed artifact")
                installed.unlink()
        if had_archive:
            for suffix in ("", "-wal", "-shm"):
                source = preimage / (self.archive_path.name + suffix)
                if source.exists():
                    # Copy the preimage back so the signed manual-recovery
                    # record still points at an intact rollback artifact.
                    _write_new(
                        Path(str(self.archive_path) + suffix),
                        _read_regular(source, MAX_ARCHIVE_BYTES))
        if reopen_store is not None and had_archive:
            reopen_store()

    def commit_restore(self, operation_id: str, *,
                       close_store: Optional[Callable[[], None]] = None,
                       reopen_store: Optional[Callable[[], Any]] = None) -> dict:
        with self._lock:
            op = self.journal.get_operation(operation_id)
            if op is None or op["kind"] != "restore":
                raise RecoveryError("unknown restore operation")
            if op["state"] == "succeeded":
                return {"operation_id": operation_id, "state": op["state"],
                        "generation_id": op["new_generation_id"]}
            if op["state"] != "prepared":
                raise RecoveryConflict("restore operation is not ready to swap")
            snapshot = self._bootstrap()
            if snapshot["generation"]["generation_id"] != op["expected_generation"]:
                self.journal.update_operation(operation_id, state="conflict")
                raise RecoveryConflict("archive generation changed before restore")
            package = self.journal.get_package(op["package_id"])
            if package is None:
                raise RecoveryError("restore package catalog row is missing")
            manifest, _members = _validate_complete_package(Path(package["package_dir"]))
            stage_archive = Path(op["staging_path"])
            preimage = Path(op["preimage_path"])
            had_archive = self.archive_path.exists()
            moved: list[Path] = []
            self.journal.update_operation(operation_id, state="swapping")
            try:
                if close_store is not None:
                    close_store()
                _secure_dir(preimage)
                for suffix in ("", "-wal", "-shm"):
                    source = Path(str(self.archive_path) + suffix)
                    destination = preimage / (self.archive_path.name + suffix)
                    if self._safe_move(source, destination):
                        moved.append(destination)
                if manifest["database_state"] == "present":
                    os.replace(str(stage_archive), str(self.archive_path))
                elif self.archive_path.exists():
                    raise RecoveryError("absent restore left an archive behind")
                if reopen_store is not None and manifest["database_state"] == "present":
                    reopen_store()
                if manifest["database_state"] == "present":
                    if logical_identity(str(self.archive_path)) != op["new_logical_sha256"]:
                        raise RecoveryError("post-open logical identity mismatch")
                self.journal.complete_generation_operation(
                    operation_id, state="succeeded")
                return {"operation_id": operation_id, "state": "succeeded",
                        "generation_id": op["new_generation_id"],
                        "logical_sha256": op["new_logical_sha256"],
                        "preimage_path": str(preimage)}
            except Exception as exc:  # noqa: BLE001
                try:
                    self._rollback(preimage=preimage, had_archive=had_archive,
                                   close_store=close_store, reopen_store=reopen_store)
                except Exception as rollback_exc:  # noqa: BLE001
                    failure = self.journal.manual_latch(
                        operation_id, failure_code="rollback_failed",
                        staged_db_sha256=op["staged_db_sha256"],
                        preimage_sha256=_sha256_json(_artifact_hashes(moved)),
                        preserved_artifacts={"preimage_path": str(preimage),
                                             "staging_path": str(stage_archive),
                                             "preimage": str(preimage),
                                             "staging": str(stage_archive),
                                             "moved": [str(item) for item in moved]})
                    raise ManualRecoveryRequired(
                        "restore rollback failed; manual recovery is required") from rollback_exc
                if self.journal.get_operation(operation_id)["state"] == "swapping":
                    self.journal.update_operation(operation_id, state="rolled_back")
                if isinstance(exc, RecoveryError):
                    raise exc
                raise RecoveryError("restore swap failed and was rolled back") from exc


def assert_archive_write_allowed(archive_path: str) -> None:
    """Gate the normal archive seam when an external recovery journal exists.

    The journal remains opt-in: no control database means no recovery manager
    is created and the existing archive path is untouched.  Once a journal is
    present, startup reconciliation and the generation identity check must
    pass before the normal writable store is opened.  This is the narrow
    generation fence; formal archive-write ownership is part of cutover.
    """
    archive = Path(os.path.abspath(os.path.expanduser(archive_path)))
    control = archive.parent / "recovery-control.sqlite3"
    if not control.exists():
        return
    manager = BackupRestoreManager(str(archive))
    try:
        manager._bootstrap()
    finally:
        manager.close()
