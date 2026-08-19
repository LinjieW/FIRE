"""Phase 0 local persistence primitives.

This module is deliberately a small, server-owned seam rather than a full
3.0 storage migration.  It stores a plan version and run snapshot in an
explicit SQLite database supplied by the caller; the opt-in Standard/Official
HTTP path supplies the app-support database lazily.  Nothing in the normal
2.0 request path opens this database.

The important boundary is that a snapshot is created by the same process that
ran the engine.  There is no public "upload result" operation here: callers
must prepare a run, execute the resolved config, and commit the result through
``save_run_snapshot`` before the run can be marked complete.
"""
from __future__ import annotations

import copy
import errno
import fcntl
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import sqlite3
import stat
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator, Optional
from urllib.parse import quote


DB_SCHEMA_VERSION = 6
CONFIG_SCHEMA_VERSION = 2
# These are result/runtime envelopes, not plan inputs.  They are stripped only
# at the explicit persistence projection boundaries below; unrelated unknown
# config fields remain compatible and are preserved byte-for-byte.
RUNTIME_ONLY_CONFIG_KEYS = frozenset({"rule_pack", "rule_pack_defaults"})
RESULT_SCHEMA_VERSION = 1
MIGRATION_EMPTY_TARGET_HASH = "4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945"
PROTOCOL_VERSION = "run-protocol-v1"
CANONICALIZER_VERSION = "json-c14n-v1"
STREAM_MAP_VERSION = "v98-seeded-chunks-v1"
TIMELINE_PROTOCOL_VERSION = "plan-timeline-v1"
FROZEN_IDENTITY_CANONICALIZER = "release-json-c14n-v1"
IDEMPOTENCY_FINGERPRINT_VERSION = "archive-request-fingerprint-v1"
REQUEST_ID_RE = re.compile(r"^req_[A-Za-z0-9]{16,80}$")
CHUNK_SIZE = 5_000
PRECISION_PATHS = {"quick": 2_000, "standard": 10_000,
                   "deep": 30_000, "official": 100_000}
SUPPORTED_PRECISIONS = frozenset((*PRECISION_PATHS, "test"))
DETERMINISTIC_RESULT_KEYS = ("home", "relocation", "dist")
RESULT_REQUIRED_KEYS = ("meta", "home", "dist")

#: The states an archived decision may be in, for the v10 CHECK constraints.
#:
#: Spelled here rather than imported from `decision_packet` on purpose: this
#: module is the storage floor and nothing product-shaped should be underneath
#: it. The cost of that choice is that the two lists could drift, so
#: `tests.test_decision_archive` asserts they are the same set -- which is a
#: test that goes red on the drift, not a comment hoping nobody causes it.
#: WHICH transitions are legal stays in `decision_packet._TRANSITIONS` and is
#: not repeated here; the schema enforces only that the history is a chain and
#: that `superseded` is final.
DECISION_STATES = ("open", "chosen", "declined", "deferred", "superseded")


class PersistenceError(RuntimeError):
    """Raised when a persistence contract cannot be satisfied."""


class IdempotencyConflictError(PersistenceError):
    """Raised when a retry key is reused for different formal-run inputs."""


class UnsupportedSchemaError(PersistenceError):
    """Raised when a database is newer than this application understands."""


class PlanNotFoundError(PersistenceError):
    """Raised when a timeline is requested for a plan not in the store."""


_WRITER_LOCKS_GUARD = threading.Lock()
_WRITER_LOCKS: dict[str, dict] = {}
_SQLITE_PATH_LOCKS_GUARD = threading.Lock()
_SQLITE_PATH_LOCKS: dict[str, dict] = {}
_SQLITE_PATH_LOCK_STATE = threading.local()


def _absolute_path(path: str) -> str:
    """Normalize spelling without resolving any filesystem symlink."""
    return os.path.abspath(os.path.expanduser(path))


class _SQLitePathLease:
    """One full-lifecycle lease for a canonical-spelling SQLite path."""

    def __init__(self, key: str, entry: dict):
        self.key = key
        self.entry = entry
        self.released = False

    def release(self) -> None:
        if self.released:
            return
        if getattr(_SQLITE_PATH_LOCK_STATE, "key", None) != self.key:
            raise PersistenceError(
                "SQLite connection must close on its opening thread")
        self.entry["lock"].release()
        _SQLITE_PATH_LOCK_STATE.key = None
        with _SQLITE_PATH_LOCKS_GUARD:
            current = _SQLITE_PATH_LOCKS.get(self.key)
            if current is not self.entry or current["refs"] <= 0:
                raise PersistenceError("SQLite path lock registry is inconsistent")
            current["refs"] -= 1
            if current["refs"] == 0:
                _SQLITE_PATH_LOCKS.pop(self.key, None)
        self.released = True


def _drop_sqlite_path_waiter(key: str, entry: dict) -> None:
    """Undo a registry reference when RLock acquisition is interrupted."""
    with _SQLITE_PATH_LOCKS_GUARD:
        current = _SQLITE_PATH_LOCKS.get(key)
        if current is entry:
            current["refs"] -= 1
            if current["refs"] == 0:
                _SQLITE_PATH_LOCKS.pop(key, None)


def _acquire_sqlite_path_lease(path: str) -> _SQLitePathLease:
    """Serialize one complete SQLite connection lifecycle per absolute path.

    Registry references include waiters, and the registry guard is never held
    while waiting on a path lock.  A thread may own only one live SQLite
    connection, preventing hidden lock-order cycles across paths.
    """
    key = _absolute_path(path)
    if getattr(_SQLITE_PATH_LOCK_STATE, "key", None) is not None:
        raise PersistenceError("nested SQLite connections are not supported")
    with _SQLITE_PATH_LOCKS_GUARD:
        entry = _SQLITE_PATH_LOCKS.get(key)
        if entry is None:
            entry = {"lock": threading.RLock(), "refs": 0}
            _SQLITE_PATH_LOCKS[key] = entry
        entry["refs"] += 1
    try:
        entry["lock"].acquire()
    except BaseException:
        _drop_sqlite_path_waiter(key, entry)
        raise
    _SQLITE_PATH_LOCK_STATE.key = key
    return _SQLitePathLease(key, entry)


class _PathLockedConnection(sqlite3.Connection):
    """sqlite3 connection that releases its path lease only after close."""

    def _bind_path_lease(self, lease: _SQLitePathLease) -> None:
        self._path_lease = lease

    def close(self) -> None:
        lease = getattr(self, "_path_lease", None)
        super().close()
        if lease is not None:
            self._path_lease = None
            lease.release()

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass


def _secure_open_flags(*, directory: bool, writable: bool = False,
                       create: bool = False) -> int:
    """Return the macOS flags required for a no-symlink SQLite path open."""
    nofollow_any = getattr(os, "O_NOFOLLOW_ANY", None)
    if nofollow_any is None:
        raise PersistenceError("secure SQLite path guard is unavailable")
    directory_flag = getattr(os, "O_DIRECTORY", None)
    if directory and directory_flag is None:
        raise PersistenceError("secure SQLite directory guard is unavailable")
    flags = nofollow_any | getattr(os, "O_CLOEXEC", 0)
    if directory:
        flags |= directory_flag | os.O_RDONLY
    else:
        flags |= os.O_RDWR if writable else os.O_RDONLY
        if create:
            flags |= os.O_CREAT | os.O_EXCL
    return flags


def _fd_identity(fd: int) -> tuple[int, int]:
    info = os.fstat(fd)
    return int(info.st_dev), int(info.st_ino)


def _validate_directory_fd(fd: int, label: str, *, private: bool) -> tuple[int, int]:
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        raise PersistenceError(f"SQLite path component is not a directory: {label}")
    if private and (info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077):
        raise PersistenceError(f"SQLite parent directory is not private: {label}")
    return int(info.st_dev), int(info.st_ino)


def _open_secure_parent(path: str, *, create: bool) -> tuple[int, tuple[int, int]]:
    """Walk to a DB parent using descriptor-relative no-follow opens.

    Ancestors only need to be directories without symlinks.  The final parent
    is the app-owned security boundary and must be owned by this user and
    mode 0700.  This deliberately avoids realpath(), which would silently
    accept a user-supplied symlink path.
    """
    absolute = _absolute_path(path)
    parent = os.path.dirname(absolute) or os.sep
    parts = Path(parent).parts
    if not parts or parts[0] != os.sep:
        raise PersistenceError("SQLite path must be absolute")
    flags = _secure_open_flags(directory=True)
    fd = os.open(os.sep, flags)
    try:
        for component in parts[1:]:
            created = False
            try:
                child = os.open(component, flags, dir_fd=fd)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(component, 0o700, dir_fd=fd)
                    created = True
                except FileExistsError:
                    pass
                try:
                    child = os.open(component, flags, dir_fd=fd)
                except FileNotFoundError:
                    raise
                except OSError as exc:
                    raise PersistenceError(
                        f"unsafe SQLite parent path: {parent}") from exc
            except OSError as exc:
                if exc.errno == errno.ENOENT and not create:
                    raise
                raise PersistenceError(
                    f"unsafe SQLite parent path: {parent}") from exc
            if created:
                # os.mkdir honours umask; repair only through this validated
                # descriptor, never through a path-based chmod.
                os.fchmod(child, 0o700)
            os.close(fd)
            fd = child
        identity = _validate_directory_fd(fd, parent, private=True)
        return fd, identity
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


def _validate_regular_fd(fd: int, label: str, *, allow_repair: bool = False) -> tuple[int, int]:
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode):
        raise PersistenceError(f"SQLite object is not a regular file: {label}")
    if info.st_uid != os.getuid():
        raise PersistenceError(f"SQLite object is not owned by the current user: {label}")
    if info.st_nlink == 0:
        raise PersistenceError(f"SQLite object was unlinked during validation: {label}")
    if info.st_nlink > 1:
        raise PersistenceError(f"SQLite object has multiple hard links: {label}")
    if stat.S_IMODE(info.st_mode) & 0o077:
        if not allow_repair:
            raise PersistenceError(f"SQLite object is not private: {label}")
        os.fchmod(fd, 0o600)
        info = os.fstat(fd)
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise PersistenceError(f"could not secure SQLite object: {label}")
    return int(info.st_dev), int(info.st_ino)


def _open_sqlite_file_at(parent_fd: int, name: str, *, writable: bool,
                         create: bool, label: str,
                         allow_repair: bool = False) -> tuple[tuple[int, int], bool]:
    """Open/validate one DB object relative to an already-safe parent fd."""
    try:
        before = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        before = None
    if before is not None:
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise PersistenceError(f"SQLite object is not a regular file: {label}")
        if before.st_uid != os.getuid():
            raise PersistenceError(f"SQLite object identity is unsafe: {label}")
        if before.st_nlink == 0:
            raise PersistenceError(f"SQLite object was unlinked during validation: {label}")
        if before.st_nlink > 1:
            raise PersistenceError(f"SQLite object has multiple hard links: {label}")

    flags = _secure_open_flags(directory=False, writable=writable,
                               create=(create and before is None))
    try:
        fd = os.open(name, flags, 0o600, dir_fd=parent_fd)
    except FileNotFoundError:
        if before is None and not create:
            raise
        raise
    except OSError as exc:
        raise PersistenceError(f"unsafe SQLite object path: {label}") from exc
    try:
        after = _validate_regular_fd(fd, label, allow_repair=allow_repair)
        if before is not None and after != (int(before.st_dev), int(before.st_ino)):
            raise PersistenceError(f"SQLite object changed during validation: {label}")
        return after, before is None
    finally:
        os.close(fd)


def _preflight_sqlite_paths(path: str, *, writable: bool,
                            create_database: bool) -> dict[str, Any]:
    """Validate DB and existing sidecars before SQLite opens the pathname."""
    parent_fd, parent_identity = _open_secure_parent(
        path, create=create_database)
    try:
        absolute = _absolute_path(path)
        database_name = os.path.basename(absolute)
        database_identity, database_created = _open_sqlite_file_at(
            parent_fd, database_name, writable=writable,
            create=create_database, label=absolute)
        sidecars: dict[str, tuple[int, int]] = {}
        for suffix in ("-wal", "-shm"):
            try:
                identity, _created = _open_sqlite_file_at(
                    parent_fd, database_name + suffix, writable=writable,
                    create=False, label=absolute + suffix)
            except FileNotFoundError:
                continue
            sidecars[suffix] = identity
        return {"parent": parent_identity, "database": database_identity,
                "database_created": database_created, "sidecars": sidecars}
    finally:
        os.close(parent_fd)


def _postflight_sqlite_paths(path: str, expected: dict[str, Any], *,
                             writable: bool, repair_new_sidecars: bool) -> dict[str, Any]:
    """Revalidate objects after SQLite opens/changes journal mode."""
    parent_fd, parent_identity = _open_secure_parent(path, create=False)
    try:
        absolute = _absolute_path(path)
        database_name = os.path.basename(absolute)
        database_identity, _created = _open_sqlite_file_at(
            parent_fd, database_name, writable=writable, create=False,
            label=absolute)
        if parent_identity != expected["parent"] or database_identity != expected["database"]:
            raise PersistenceError("SQLite path identity changed during open")
        sidecars: dict[str, tuple[int, int]] = {}
        for suffix in ("-wal", "-shm"):
            try:
                identity, _created = _open_sqlite_file_at(
                    parent_fd, database_name + suffix, writable=writable,
                    create=False, label=absolute + suffix,
                    allow_repair=(repair_new_sidecars and suffix not in expected["sidecars"]))
            except FileNotFoundError:
                if suffix in expected["sidecars"]:
                    raise PersistenceError("SQLite sidecar disappeared during open")
                continue
            if suffix in expected["sidecars"] and identity != expected["sidecars"][suffix]:
                raise PersistenceError("SQLite sidecar identity changed during open")
            sidecars[suffix] = identity
        return {"parent": parent_identity, "database": database_identity,
                "database_created": False, "sidecars": sidecars}
    finally:
        os.close(parent_fd)


class _WriterLock:
    """Re-entrant within one process, exclusive across processes."""

    def __init__(self, db_path: str, entry: dict):
        self.db_path = db_path
        self.entry = entry
        self.released = False

    def release(self) -> None:
        if self.released:
            return
        with _WRITER_LOCKS_GUARD:
            entry = _WRITER_LOCKS.get(self.db_path)
            if entry is None:
                self.released = True
                return
            entry["refs"] -= 1
            if entry["refs"] == 0:
                try:
                    fcntl.flock(entry["fd"], fcntl.LOCK_UN)
                finally:
                    os.close(entry["fd"])
                    _WRITER_LOCKS.pop(self.db_path, None)
            self.released = True


def _acquire_writer_lock(db_path: str) -> _WriterLock:
    """Acquire a private app-support lock; fail closed if another process owns it."""
    absolute = _absolute_path(db_path)
    parent_fd, _parent_identity = _open_secure_parent(absolute, create=False)
    os.close(parent_fd)
    with _WRITER_LOCKS_GUARD:
        existing = _WRITER_LOCKS.get(absolute)
        if existing is not None:
            existing["refs"] += 1
            return _WriterLock(absolute, existing)
        lock_path = absolute + ".lock"
        lock_parent_fd, _lock_parent_identity = _open_secure_parent(
            lock_path, create=False)
        fd = None
        try:
            lock_name = os.path.basename(lock_path)
            try:
                before = os.stat(lock_name, dir_fd=lock_parent_fd,
                                 follow_symlinks=False)
            except FileNotFoundError:
                before = None
            if before is not None and (stat.S_ISLNK(before.st_mode)
                                       or not stat.S_ISREG(before.st_mode)):
                raise PersistenceError("SQLite writer lock is not a regular file")
            flags = _secure_open_flags(directory=False, writable=True,
                                       create=before is None)
            fd = os.open(lock_name, flags, 0o600, dir_fd=lock_parent_fd)
            lock_identity = _validate_regular_fd(fd, lock_path)
            if before is not None and lock_identity != (
                    int(before.st_dev), int(before.st_ino)):
                raise PersistenceError("SQLite writer lock identity changed")
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except PersistenceError:
            try:
                if fd is not None:
                    os.close(fd)
            except Exception:
                pass
            raise
        except OSError as exc:
            try:
                if fd is not None:
                    os.close(fd)
            except Exception:
                pass
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                raise PersistenceError("SQLite writer lock unavailable") from exc
            raise PersistenceError("could not acquire SQLite writer lock") from exc
        finally:
            os.close(lock_parent_fd)
        entry = {"fd": fd, "refs": 1, "path": lock_path}
        _WRITER_LOCKS[absolute] = entry
        return _WriterLock(absolute, entry)


_RECEIPT_SENTINEL = object()


class EngineResultReceipt:
    """Opaque in-process handoff from the server engine runner to the store."""

    __slots__ = ("attempt_id", "execution_mode", "result", "_token")

    def __init__(self, attempt_id: str, execution_mode: str, result: dict,
                 token: object, sentinel: object):
        if sentinel is not _RECEIPT_SENTINEL:
            raise TypeError("EngineResultReceipt is server-internal")
        self.attempt_id = attempt_id
        self.execution_mode = execution_mode
        self.result = copy.deepcopy(result)
        self._token = token


def utc_now() -> str:
    return (datetime.now(timezone.utc).isoformat(timespec="microseconds")
            .replace("+00:00", "Z"))


def canonical_json_bytes(value: Any) -> bytes:
    """Return the v1 canonical JSON representation used for hashes.

    We intentionally do not round floats.  Rounding would make a hash look
    stable while silently changing the stored inputs/results.  Non-finite
    values are rejected rather than serialized as non-standard JSON.
    """
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise PersistenceError(f"value is not canonical JSON: {exc}") from exc
    return text.encode("utf-8")


def canonical_json_text(value: Any) -> str:
    return canonical_json_bytes(value).decode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _frozen_identity_sha256(value: Any) -> str:
    """Hash the release-tool JSON form used for the embedded identity.

    This is intentionally separate from the runtime result/config
    canonicalizer above.  The release identity is generated before freezing,
    so its algorithm is kept explicit and stable at this build/runtime seam.
    """
    encoded = (json.dumps(value, ensure_ascii=True, sort_keys=True,
                          separators=(",", ":")) + "\n").encode("utf-8")
    return sha256_bytes(encoded)


def validate_precision(precision: str, effective_paths: Optional[int] = None) -> str:
    value = str(precision or "").strip().lower()
    if value not in SUPPORTED_PRECISIONS:
        raise PersistenceError(f"unknown precision tier: {precision!r}")
    if value != "test" and effective_paths is not None \
            and int(effective_paths) != PRECISION_PATHS[value]:
        raise PersistenceError(
            f"precision {value!r} requires {PRECISION_PATHS[value]} effective paths")
    return value


def validate_request_id(value: Optional[str]) -> Optional[str]:
    """Validate the explicit archive retry key without accepting free text."""
    if value is None:
        return None
    if not isinstance(value, str) or not REQUEST_ID_RE.fullmatch(value):
        raise PersistenceError("request_id must match req_<16-80 alphanumeric chars>")
    return value


def _archive_request_fingerprint(*, source_config: dict,
                                 normalized_config_sha256: str,
                                 provided_plan_id: Optional[str],
                                 provided_plan_version_id: Optional[str],
                                 precision: str, requested_paths: int,
                                 effective_paths: int, requested_dist_paths: int,
                                 effective_dist_paths: int, seed: int,
                                 build: dict) -> str:
    """Bind every caller-visible formal-run input to one retry key."""
    environment = json.loads(build["environment_json"])
    payload = {
        "fingerprint_version": IDEMPOTENCY_FINGERPRINT_VERSION,
        "source_config_sha256": sha256_bytes(
            canonical_json_text(source_config or {}).encode("utf-8")),
        "normalized_config_sha256": normalized_config_sha256,
        "provided_plan_id": provided_plan_id,
        "provided_plan_version_id": provided_plan_version_id,
        "precision": precision,
        "requested_paths": int(requested_paths),
        "effective_paths": int(effective_paths),
        "requested_dist_paths": int(requested_dist_paths),
        "effective_dist_paths": int(effective_dist_paths),
        "seed": int(seed),
        "engine_build_id": build["id"],
        "engine_version": build["engine_version"],
        "protocol_version": build["protocol_version"],
        "code_manifest_sha256": build.get("code_manifest_sha256"),
        "data_manifest_sha256": build.get("data_manifest_sha256"),
        "build_identity_sha256": environment.get("build_identity_sha256"),
    }
    return sha256_json(payload)


def _deep_merge(base: Any, override: Any) -> Any:
    if override is None or not isinstance(override, dict):
        return copy.deepcopy(override)
    out = copy.deepcopy(base) if isinstance(base, dict) else {}
    for key, value in override.items():
        if key in {"__proto__", "constructor", "prototype"}:
            continue
        out[key] = _deep_merge(out.get(key), value)
    return out


def _sanitize_runtime_metadata(config: Optional[dict]) -> dict:
    """Project result/runtime envelopes out of a plan-input dictionary.

    This is deliberately narrow: only the two reserved top-level leaves and
    their counterparts under ``meta`` are removed.  Unknown, unrelated
    fields remain part of the caller's compatibility surface.  The input is
    never mutated, and this helper does not touch result dictionaries.
    """
    if not isinstance(config, dict):
        return copy.deepcopy(config)
    clean = copy.deepcopy(config)
    for key in RUNTIME_ONLY_CONFIG_KEYS:
        clean.pop(key, None)
    meta = clean.get("meta")
    if isinstance(meta, dict):
        had_reserved_meta = any(key in meta for key in RUNTIME_ONLY_CONFIG_KEYS)
        for key in RUNTIME_ONLY_CONFIG_KEYS:
            meta.pop(key, None)
        if had_reserved_meta and not meta:
            clean.pop("meta", None)
    return clean


def normalize_config(config: Optional[dict], default_factory: Callable[[], dict]) -> dict:
    """Resolve a possibly old/partial config against server-side defaults.

    The browser still owns the 2.0 draft experience.  This function is used
    only by the explicit persistence seam so a saved PlanVersion records the
    exact resolved input that the server will run.
    """
    if config is None:
        config = {}
    if not isinstance(config, dict):
        raise PersistenceError("config must be an object")
    config = _sanitize_runtime_metadata(config)
    raw_version = config.get("config_version", CONFIG_SCHEMA_VERSION)
    try:
        raw_version = int(raw_version)
    except (TypeError, ValueError) as exc:
        raise PersistenceError("config_version must be an integer") from exc
    if raw_version > CONFIG_SCHEMA_VERSION:
        raise PersistenceError(
            f"config schema {raw_version} is newer than supported {CONFIG_SCHEMA_VERSION}")
    out = _deep_merge(default_factory(), config)
    # Phase 1 additive ownership leaves: explicit JSON null wins a generic
    # deep-merge, but it must mean the same honest legacy sentinel as a missing
    # owner. Preserve unknown non-null strings so enabled streams still fail
    # closed in the engine adapter instead of being silently reclassified.
    income_streams = out.get("income_streams")
    if isinstance(income_streams, dict):
        for kind in ("pension", "rental", "parttime", "equity"):
            field = f"{kind}_owner"
            if income_streams.get(field) is None or income_streams.get(field) == "":
                income_streams[field] = "unspecified"
    # Current migrations are additive.  Older configs are stamped with the
    # current schema after the merge; future/breaking versions fail closed.
    out["config_version"] = CONFIG_SCHEMA_VERSION
    return out


def _validate_result_schema(result: dict) -> dict:
    if not isinstance(result, dict):
        raise PersistenceError("result must be an object")
    missing = [key for key in RESULT_REQUIRED_KEYS if key not in result]
    if missing:
        raise PersistenceError(
            "result is missing required fields: " + ", ".join(missing))
    if not isinstance(result["meta"], dict):
        raise PersistenceError("result meta must be an object")
    if "name" not in result["meta"]:
        raise PersistenceError("result meta.name is required")
    for key in ("home", "dist"):
        if not isinstance(result[key], dict):
            raise PersistenceError(f"result {key} must be an object")
    if "relocation" in result and not isinstance(result["relocation"], dict):
        raise PersistenceError("result relocation must be an object")
    return result


def _deterministic_result_view(result: dict) -> dict:
    """Return the explicit engine payload used by the replay hash.

    The allowlist is intentional.  Response-envelope metadata (timing,
    protocol, snapshot ids, and display-only fields) is archived and checked
    separately, but cannot silently become part of the numeric replay contract.
    If the engine adds a new payload field, the result schema must be reviewed
    and this allowlist updated.
    """
    _validate_result_schema(result)
    clean = {"engine_payload": {}}
    for key in DETERMINISTIC_RESULT_KEYS:
        if key in result:
            clean["engine_payload"][key] = copy.deepcopy(result[key])
    for key in ("home", "relocation"):
        if isinstance(clean["engine_payload"].get(key), dict):
            # The adapter convenience seed is not returned by run_full itself;
            # the actual seed is part of the separately hashed protocol.
            clean["engine_payload"][key].pop("seed", None)
    meta = result.get("meta")
    if isinstance(meta, dict) and "name" in meta:
        clean["meta"] = {"name": copy.deepcopy(meta["name"])}
    return clean


def deterministic_result_sha256(result: dict) -> str:
    return sha256_json(_deterministic_result_view(result))


def _uuid(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _readonly_connect(path: str) -> sqlite3.Connection:
    """Open an existing SQLite file without creating or migrating anything."""
    absolute = _absolute_path(path)
    lease = _acquire_sqlite_path_lease(absolute)
    conn = None
    try:
        expected = _preflight_sqlite_paths(
            absolute, writable=False, create_database=False)
        uri = "file:" + quote(absolute, safe="/") + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0,
                               isolation_level=None,
                               factory=_PathLockedConnection)
        conn._bind_path_lease(lease)
        conn.row_factory = sqlite3.Row
        _postflight_sqlite_paths(absolute, expected, writable=False,
                                 repair_new_sidecars=False)
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout = 5000")
        _postflight_sqlite_paths(absolute, expected, writable=False,
                                 repair_new_sidecars=False)
        return conn
    except sqlite3.Error as exc:
        if conn is not None:
            conn.close()
        else:
            lease.release()
        raise PersistenceError("timeline database is not readable") from exc
    except Exception:
        if conn is not None:
            conn.close()
        else:
            lease.release()
        raise


def _readonly_schema_preflight(conn: sqlite3.Connection) -> None:
    """Reject future, partial, or unversioned databases without writing."""
    try:
        rows = conn.execute(
            "SELECT version FROM schema_migrations ORDER BY version").fetchall()
        versions = [int(row[0]) for row in rows]
        user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error as exc:
        raise PersistenceError("timeline database schema is unreadable") from exc
    current = versions[-1] if versions else 0
    if current in (7, 8, 9, 10):
        expected_versions = list(range(1, current + 1))
        complete = {
            7: PersistenceStore._schema_v7_complete,
            8: PersistenceStore._schema_v8_complete,
            9: PersistenceStore._schema_v9_complete,
            10: PersistenceStore._schema_v10_complete,
        }[current](conn)
        if (versions != expected_versions or user_version != current
                or not complete):
            raise PersistenceError(
                f"timeline database schema v{current} is incomplete")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise PersistenceError("timeline database contains foreign-key violations")
        return
    if current > DB_SCHEMA_VERSION or user_version > DB_SCHEMA_VERSION:
        raise UnsupportedSchemaError("timeline database schema is newer than supported")
    if versions != list(range(1, current + 1)) or current != DB_SCHEMA_VERSION:
        raise PersistenceError("timeline database schema migration lineage is incomplete")
    if user_version != current:
        raise PersistenceError("timeline database schema/user_version mismatch")
    if not PersistenceStore._schema_complete(
            conn, include_v2=True, include_v3=True, include_v4=True,
            include_v5=True, include_v6=True):
        raise PersistenceError("timeline database schema is incomplete")
    if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise PersistenceError("timeline database contains foreign-key violations")


def _validate_readonly_sidecars(path: str) -> None:
    """Allow only private regular WAL sidecars created by SQLite's read path."""
    _preflight_sqlite_paths(_absolute_path(path), writable=False,
                            create_database=False)


def _file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def source_manifest(source_root: Optional[str]) -> dict:
    """Build a relative, deterministic manifest for the engine source seam."""
    if not source_root:
        return {"status": "not-provided", "files": []}
    root = Path(source_root).resolve()
    files = []
    for folder in (root / "engine", root / "server"):
        if not folder.is_dir():
            continue
        for path in sorted(folder.glob("*.py")):
            files.append({"path": str(path.relative_to(root)),
                          "sha256": _file_sha256(path)})
    payload = {
        "status": "computed",
        "algorithm": "sha256(canonical-json-v1(payload-without-sha256))",
        "scope": ["engine/*.py", "server/*.py"],
        "files": files,
    }
    payload["sha256"] = sha256_json(payload)
    return payload


def _load_bundled_identity() -> Optional[dict]:
    path = os.environ.get("FIRE_BUNDLED_IDENTITY")
    if not path:
        return None
    try:
        with open(path, encoding="utf-8") as handle:
            identity = json.load(handle)
        if not isinstance(identity, dict):
            raise ValueError("identity is not an object")
        identity_sha256 = identity.get("identity_sha256")
        payload = dict(identity)
        payload.pop("identity_sha256", None)
        if (identity.get("identity_canonicalizer")
                != FROZEN_IDENTITY_CANONICALIZER
                or identity_sha256 != _frozen_identity_sha256(payload)):
            raise ValueError("identity digest mismatch")
        source = identity.get("runtime_manifest") or identity.get("source_manifest")
        data = identity.get("data_manifest")
        code_hash = (identity.get("runtime_manifest_sha256")
                     or identity.get("code_manifest_sha256"))
        data_hash = identity.get("data_manifest_sha256")
        if (not isinstance(source, dict) or not isinstance(data, dict)
                or source.get("component_sha256") != code_hash
                or data.get("component_sha256") != data_hash
                or not isinstance(code_hash, str) or len(code_hash) != 64
                or not isinstance(data_hash, str) or len(data_hash) != 64):
            raise ValueError("identity manifest fields are invalid")
        return identity
    except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
        raise PersistenceError("bundled build identity is invalid") from exc


def _package_version(name: str) -> Optional[str]:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def build_environment(source_root: Optional[str], metadata: Optional[dict] = None) -> tuple[dict, dict]:
    """Return non-secret environment metadata and its source manifest."""
    metadata = dict(metadata or {})
    bundled_identity = _load_bundled_identity()
    manifest = source_manifest(source_root)
    if bundled_identity is not None:
        manifest = copy.deepcopy(
            bundled_identity.get("runtime_manifest")
            or bundled_identity["source_manifest"])
        manifest["status"] = "bundled"
        manifest["sha256"] = bundled_identity["code_manifest_sha256"]
        manifest["bundled_identity_sha256"] = bundled_identity["identity_sha256"]
        expected_code = metadata.get("code_manifest_sha256")
        if expected_code and expected_code != manifest["sha256"]:
            raise PersistenceError("bundled code manifest does not match metadata")
        expected_data = metadata.get("data_manifest_sha256")
        if expected_data and expected_data != bundled_identity["data_manifest_sha256"]:
            raise PersistenceError("bundled data manifest does not match metadata")
    environment = {
        "app_release_id": metadata.get("app_release_id"),
        "bundle_version": metadata.get("bundle_version"),
        "git_tag": metadata.get("git_tag"),
        "python_version": platform.python_version(),
        "numpy_version": metadata.get("numpy_version") or _package_version("numpy"),
        "architecture": platform.machine(),
        "platform": sys.platform,
        "data_manifest_id": metadata.get("data_manifest_id"),
        "data_manifest_sha256": metadata.get("data_manifest_sha256"),
    }
    if bundled_identity is not None:
        environment["build_identity_sha256"] = bundled_identity["identity_sha256"]
        environment["data_manifest_id"] = (
            environment["data_manifest_id"]
            or bundled_identity["data_manifest_sha256"])
        environment["data_manifest_sha256"] = (
            environment["data_manifest_sha256"]
            or bundled_identity["data_manifest_sha256"])
    return environment, manifest


def make_engine_build_id(engine_version: str, protocol_version: str,
                         environment: dict, manifest: dict) -> str:
    code_hash = manifest.get("sha256")
    payload = {
        "engine_version": engine_version,
        "protocol_version": protocol_version,
        "environment": environment,
        "source_manifest_sha256": code_hash,
    }
    return "build_" + sha256_json(payload)[:32]


class PersistenceStore:
    """SQLite store with explicit initialization and append-only snapshots."""

    def __init__(self, path: str, *, app_release_id: str = "phase0-prototype"):
        if not path or path == ":memory:":
            raise ValueError("Phase 0 requires an explicit file-backed SQLite path")
        self.path = os.path.abspath(os.path.expanduser(path))
        self.app_release_id = app_release_id
        self._writer_lock: Optional[_WriterLock] = None
        self._process_lock = None
        self._receipt_tokens: dict[str, object] = {}
        self._ensure_parent()
        try:
            self._writer_lock = _acquire_writer_lock(self.path)
            self.initialize()
        except Exception:
            if self._writer_lock is not None:
                self._writer_lock.release()
                self._writer_lock = None
            raise

    def close(self) -> None:
        """Release the process-lifetime writer lock."""
        if self._writer_lock is not None:
            self._writer_lock.release()
            self._writer_lock = None

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def _ensure_parent(self) -> None:
        parent_fd, _identity = _open_secure_parent(self.path, create=True)
        os.close(parent_fd)

    def _connect(self) -> sqlite3.Connection:
        lease = _acquire_sqlite_path_lease(self.path)
        conn = None
        try:
            expected = _preflight_sqlite_paths(
                self.path, writable=True, create_database=True)
            # sqlite3.connect() is pathname based.  The preflight and the
            # immediate post-open identity check are the strongest safe
            # contract available through Python's sqlite3 module; no PRAGMA
            # is executed until the post-open check passes.
            conn = sqlite3.connect(self.path, timeout=10.0,
                                   isolation_level=None,
                                   factory=_PathLockedConnection)
            conn._bind_path_lease(lease)
            conn.row_factory = sqlite3.Row
            _postflight_sqlite_paths(self.path, expected, writable=True,
                                     repair_new_sidecars=False)
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 10000")
            conn.execute("PRAGMA synchronous = FULL")
            conn.execute("PRAGMA journal_mode = WAL")
            _postflight_sqlite_paths(self.path, expected, writable=True,
                                     repair_new_sidecars=True)
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

    @staticmethod
    def _trigger_statements() -> list[str]:
        return [
            """
            CREATE TRIGGER IF NOT EXISTS plan_versions_immutable_update
            BEFORE UPDATE ON plan_versions
            BEGIN SELECT RAISE(ABORT, 'plan_versions are immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS plan_versions_immutable_delete
            BEFORE DELETE ON plan_versions
            BEGIN SELECT RAISE(ABORT, 'plan_versions are immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS plan_versions_parent_plan_guard
            BEFORE INSERT ON plan_versions
            WHEN NEW.parent_version_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM plan_versions
                WHERE id = NEW.parent_version_id AND plan_id = NEW.plan_id
            )
            BEGIN SELECT RAISE(ABORT, 'parent plan version belongs to another plan'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_snapshots_immutable_update
            BEFORE UPDATE ON run_snapshots
            BEGIN SELECT RAISE(ABORT, 'run_snapshots are immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_snapshots_immutable_delete
            BEFORE DELETE ON run_snapshots
            BEGIN SELECT RAISE(ABORT, 'run_snapshots are immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS engine_builds_immutable_update
            BEFORE UPDATE ON engine_builds
            BEGIN SELECT RAISE(ABORT, 'engine_builds are immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS engine_builds_immutable_delete
            BEFORE DELETE ON engine_builds
            BEGIN SELECT RAISE(ABORT, 'engine_builds are immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_running_bindings_immutable
            BEFORE UPDATE ON run_attempts
            WHEN OLD.status = 'running' AND (
                NEW.job_id IS NOT OLD.job_id OR
                NEW.plan_id IS NOT OLD.plan_id OR
                NEW.plan_version_id IS NOT OLD.plan_version_id OR
                NEW.engine_build_id IS NOT OLD.engine_build_id OR
                NEW.precision IS NOT OLD.precision OR
                NEW.requested_paths IS NOT OLD.requested_paths OR
                NEW.effective_paths IS NOT OLD.effective_paths OR
                NEW.dist_paths IS NOT OLD.dist_paths OR
                NEW.requested_dist_paths IS NOT OLD.requested_dist_paths OR
                NEW.effective_dist_paths IS NOT OLD.effective_dist_paths OR
                NEW.seed IS NOT OLD.seed OR
                NEW.started_at IS NOT OLD.started_at
            )
            BEGIN SELECT RAISE(ABORT, 'running attempt bindings are immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_plan_lineage_insert_guard
            BEFORE INSERT ON run_attempts
            WHEN NOT EXISTS (
                SELECT 1 FROM plan_versions
                WHERE id = NEW.plan_version_id AND plan_id = NEW.plan_id
            )
            BEGIN SELECT RAISE(ABORT, 'attempt plan lineage mismatch'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_plan_lineage_update_guard
            BEFORE UPDATE ON run_attempts
            WHEN NOT EXISTS (
                SELECT 1 FROM plan_versions
                WHERE id = NEW.plan_version_id AND plan_id = NEW.plan_id
            )
            BEGIN SELECT RAISE(ABORT, 'attempt plan lineage mismatch'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_terminal_immutable
            BEFORE UPDATE ON run_attempts
            WHEN OLD.status IN ('completed', 'failed', 'cancelled') AND (
                NEW.job_id IS NOT OLD.job_id OR
                NEW.plan_id IS NOT OLD.plan_id OR
                NEW.plan_version_id IS NOT OLD.plan_version_id OR
                NEW.engine_build_id IS NOT OLD.engine_build_id OR
                NEW.status IS NOT OLD.status OR
                NEW.precision IS NOT OLD.precision OR
                NEW.requested_paths IS NOT OLD.requested_paths OR
                NEW.effective_paths IS NOT OLD.effective_paths OR
                NEW.dist_paths IS NOT OLD.dist_paths OR
                NEW.requested_dist_paths IS NOT OLD.requested_dist_paths OR
                NEW.effective_dist_paths IS NOT OLD.effective_dist_paths OR
                NEW.seed IS NOT OLD.seed OR
                NEW.started_at IS NOT OLD.started_at OR
                NEW.finished_at IS NOT OLD.finished_at OR
                NEW.error IS NOT OLD.error OR
                NEW.snapshot_id IS NOT OLD.snapshot_id OR
                NEW.execution_mode IS NOT OLD.execution_mode
            )
            BEGIN SELECT RAISE(ABORT, 'terminal attempts are immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_completed_guard
            BEFORE UPDATE ON run_attempts
            WHEN NEW.status = 'completed' AND NOT EXISTS (
                SELECT 1 FROM run_snapshots
                WHERE id = NEW.snapshot_id AND attempt_id = NEW.id
            )
            BEGIN SELECT RAISE(ABORT, 'completed attempt requires its snapshot'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_completed_insert_guard
            BEFORE INSERT ON run_attempts
            WHEN NEW.status = 'completed' AND NOT EXISTS (
                SELECT 1 FROM run_snapshots
                WHERE id = NEW.snapshot_id AND attempt_id = NEW.id
            )
            BEGIN SELECT RAISE(ABORT, 'completed attempt requires its snapshot'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_snapshot_ref_insert_guard
            BEFORE INSERT ON run_attempts
            WHEN NEW.snapshot_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM run_snapshots
                WHERE id = NEW.snapshot_id AND attempt_id = NEW.id
            )
            BEGIN SELECT RAISE(ABORT, 'attempt snapshot reference is missing'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_snapshot_ref_update_guard
            BEFORE UPDATE ON run_attempts
            WHEN NEW.snapshot_id IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM run_snapshots
                WHERE id = NEW.snapshot_id AND attempt_id = NEW.id
            )
            BEGIN SELECT RAISE(ABORT, 'attempt snapshot reference is missing'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_immutable_delete
            BEFORE DELETE ON run_attempts
            BEGIN SELECT RAISE(ABORT, 'run attempts are append-only'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_snapshots_binding_guard
            BEFORE INSERT ON run_snapshots
            WHEN NOT EXISTS (
                SELECT 1
                FROM run_attempts a
                JOIN plan_versions p ON p.id = NEW.plan_version_id
                WHERE a.id = NEW.attempt_id
                  AND a.status = 'running'
                  AND a.plan_id = NEW.plan_id
                  AND a.plan_version_id = NEW.plan_version_id
                  AND a.engine_build_id = NEW.engine_build_id
                  AND p.plan_id = NEW.plan_id
                  AND NEW.resolved_input_sha256 = p.normalized_config_sha256
            )
            BEGIN SELECT RAISE(ABORT, 'snapshot binding mismatch'); END
            """,
        ]

    @staticmethod
    def _request_trigger_statements() -> list[str]:
        return [
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_request_insert_contract
            BEFORE INSERT ON run_attempts
            WHEN NEW.status != 'running'
              OR NEW.finished_at IS NOT NULL
              OR NEW.error IS NOT NULL
              OR NEW.snapshot_id IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'new attempts must be running and empty'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_request_update_contract
            BEFORE UPDATE ON run_attempts
            WHEN (NEW.status = 'running' AND (
                      NEW.finished_at IS NOT NULL OR NEW.error IS NOT NULL OR
                      NEW.snapshot_id IS NOT NULL))
              OR (NEW.status IN ('failed', 'cancelled') AND (
                      NEW.finished_at IS NULL OR NEW.snapshot_id IS NOT NULL))
              OR (NEW.status = 'completed' AND (
                      NEW.finished_at IS NULL OR NEW.snapshot_id IS NULL OR
                      NEW.error IS NOT NULL))
            BEGIN SELECT RAISE(ABORT, 'attempt terminal fields are inconsistent'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_request_mirror
            AFTER UPDATE OF status, snapshot_id, finished_at, error ON run_attempts
            WHEN EXISTS (SELECT 1 FROM run_requests WHERE attempt_id = NEW.id)
            BEGIN
                UPDATE run_requests
                   SET status = NEW.status,
                       snapshot_id = NEW.snapshot_id,
                       finished_at = NEW.finished_at,
                       error = NEW.error
                 WHERE attempt_id = NEW.id;
            END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_requests_insert_guard
            BEFORE INSERT ON run_requests
            WHEN EXISTS (SELECT 1 FROM run_requests WHERE request_id = NEW.request_id)
              OR EXISTS (SELECT 1 FROM run_requests WHERE attempt_id = NEW.attempt_id)
              OR NEW.request_id IS NULL
              OR length(NEW.request_id) < 20
              OR length(NEW.request_id) > 84
              OR substr(NEW.request_id, 1, 4) != 'req_'
              OR substr(NEW.request_id, 5) GLOB '*[^A-Za-z0-9]*'
              OR NEW.fingerprint_sha256 IS NULL
              OR length(NEW.fingerprint_sha256) != 64
              OR NEW.fingerprint_sha256 GLOB '*[^0-9a-f]*'
              OR NOT EXISTS (
                   SELECT 1
                     FROM run_attempts a
                     JOIN plan_versions p
                       ON p.id = a.plan_version_id AND p.plan_id = a.plan_id
                     JOIN engine_builds b ON b.id = a.engine_build_id
                    WHERE a.id = NEW.attempt_id
                      AND a.status = 'running'
                      AND a.job_id IS NEW.job_id
                      AND a.plan_id = NEW.plan_id
                      AND a.plan_version_id = NEW.plan_version_id
                      AND a.engine_build_id = NEW.engine_build_id
                      AND NEW.status = 'running'
                      AND NEW.snapshot_id IS NULL
                      AND NEW.finished_at IS NULL
                      AND NEW.error IS NULL
                      AND p.plan_id = NEW.plan_id
                      AND b.id = NEW.engine_build_id)
            BEGIN SELECT RAISE(ABORT, 'run request insert binding mismatch'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_requests_delete_guard
            BEFORE DELETE ON run_requests
            BEGIN SELECT RAISE(ABORT, 'run requests are append-only'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_requests_binding_immutable
            BEFORE UPDATE ON run_requests
            WHEN NEW.request_id IS NOT OLD.request_id OR
                 NEW.fingerprint_sha256 IS NOT OLD.fingerprint_sha256 OR
                 NEW.job_id IS NOT OLD.job_id OR
                 NEW.attempt_id IS NOT OLD.attempt_id OR
                 NEW.plan_id IS NOT OLD.plan_id OR
                 NEW.plan_version_id IS NOT OLD.plan_version_id OR
                 NEW.engine_build_id IS NOT OLD.engine_build_id OR
                 NEW.created_at IS NOT OLD.created_at
            BEGIN SELECT RAISE(ABORT, 'run request bindings are immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_requests_state_lineage_guard
            BEFORE UPDATE ON run_requests
            WHEN NOT EXISTS (
                SELECT 1
                  FROM run_attempts a
                  JOIN plan_versions p
                    ON p.id = a.plan_version_id AND p.plan_id = a.plan_id
                  JOIN engine_builds b ON b.id = a.engine_build_id
                 WHERE a.id = NEW.attempt_id
                   AND a.job_id IS NEW.job_id
                   AND a.plan_id = NEW.plan_id
                   AND a.plan_version_id = NEW.plan_version_id
                   AND a.engine_build_id = NEW.engine_build_id
                   AND NEW.status IS a.status
                   AND NEW.snapshot_id IS a.snapshot_id
                   AND NEW.finished_at IS a.finished_at
                   AND NEW.error IS a.error
                   AND p.plan_id = NEW.plan_id
                   AND b.id = NEW.engine_build_id)
            BEGIN SELECT RAISE(ABORT, 'run request state does not mirror attempt'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_requests_terminal_immutable
            BEFORE UPDATE ON run_requests
            WHEN OLD.status IN ('completed', 'failed', 'cancelled') AND (
                NEW.status IS NOT OLD.status OR
                NEW.snapshot_id IS NOT OLD.snapshot_id OR
                NEW.finished_at IS NOT OLD.finished_at OR
                NEW.error IS NOT OLD.error)
            BEGIN SELECT RAISE(ABORT, 'terminal run requests are immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_requests_completed_guard
            BEFORE UPDATE ON run_requests
            WHEN NEW.status = 'completed' AND NOT EXISTS (
                SELECT 1 FROM run_snapshots
                WHERE id = NEW.snapshot_id AND attempt_id = NEW.attempt_id)
            BEGIN SELECT RAISE(ABORT, 'completed request requires its snapshot'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_requests_attempt_status_guard
            BEFORE UPDATE ON run_requests
            WHEN NEW.status != (
                SELECT status FROM run_attempts WHERE id = NEW.attempt_id)
            BEGIN SELECT RAISE(ABORT, 'request/attempt status mismatch'); END
            """,
        ]

    @staticmethod
    def _v5_trigger_statements() -> list[str]:
        """Block REPLACE from bypassing append-only DELETE triggers.

        SQLite only fires DELETE triggers for INSERT OR REPLACE when
        recursive_triggers is enabled.  Every immutable identity therefore
        needs a BEFORE INSERT duplicate guard for all of its unique keys.
        """
        return [
            """
            CREATE TRIGGER IF NOT EXISTS schema_migrations_duplicate_insert_guard
            BEFORE INSERT ON schema_migrations
            WHEN EXISTS (
                SELECT 1 FROM schema_migrations WHERE version = NEW.version)
            BEGIN SELECT RAISE(ABORT, 'schema migration version already exists'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS schema_migrations_immutable_update
            BEFORE UPDATE ON schema_migrations
            BEGIN SELECT RAISE(ABORT, 'schema migrations are immutable'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS schema_migrations_immutable_delete
            BEFORE DELETE ON schema_migrations
            BEGIN SELECT RAISE(ABORT, 'schema migrations are append-only'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS engine_builds_duplicate_insert_guard
            BEFORE INSERT ON engine_builds
            WHEN EXISTS (SELECT 1 FROM engine_builds WHERE id = NEW.id)
            BEGIN SELECT RAISE(ABORT, 'engine build identity already exists'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS plan_versions_duplicate_insert_guard
            BEFORE INSERT ON plan_versions
            WHEN EXISTS (SELECT 1 FROM plan_versions WHERE id = NEW.id)
            BEGIN SELECT RAISE(ABORT, 'plan version identity already exists'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_attempts_duplicate_insert_guard
            BEFORE INSERT ON run_attempts
            WHEN EXISTS (SELECT 1 FROM run_attempts WHERE id = NEW.id)
            BEGIN SELECT RAISE(ABORT, 'run attempt identity already exists'); END
            """,
            """
            CREATE TRIGGER IF NOT EXISTS run_snapshots_duplicate_insert_guard
            BEFORE INSERT ON run_snapshots
            WHEN EXISTS (SELECT 1 FROM run_snapshots WHERE id = NEW.id)
              OR EXISTS (
                   SELECT 1 FROM run_snapshots
                    WHERE attempt_id = NEW.attempt_id)
            BEGIN SELECT RAISE(ABORT, 'run snapshot identity already exists'); END
            """,
        ]

    @staticmethod
    def _v6_trigger_statements() -> list[str]:
        """Freeze identities that v5 did not yet protect completely.

        This is a separate migration trigger so existing v5 databases receive
        the guard; changing an older CREATE TRIGGER IF NOT EXISTS statement
        would leave already-created databases unchanged.
        """
        return [
            """
            CREATE TRIGGER plans_duplicate_insert_guard
            BEFORE INSERT ON plans
            WHEN EXISTS (SELECT 1 FROM plans WHERE id = NEW.id)
            BEGIN SELECT RAISE(ABORT, 'plan identity already exists'); END
            """,
            """
            CREATE TRIGGER plans_identity_immutable
            BEFORE UPDATE OF id ON plans
            WHEN NEW.id IS NOT OLD.id
            BEGIN SELECT RAISE(ABORT, 'plan identity is immutable'); END
            """,
            """
            CREATE TRIGGER plans_immutable_delete
            BEFORE DELETE ON plans
            BEGIN SELECT RAISE(ABORT, 'plans require a status transition'); END
            """,
            """
            CREATE TRIGGER run_attempts_identity_immutable
            BEFORE UPDATE OF id ON run_attempts
            WHEN NEW.id IS NOT OLD.id
            BEGIN SELECT RAISE(ABORT, 'run attempt identity is immutable'); END
            """,
        ]

    @staticmethod
    def _v3_table_statements() -> list[str]:
        return [
            """
            CREATE TABLE IF NOT EXISTS run_requests (
                request_id TEXT PRIMARY KEY,
                fingerprint_sha256 TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
                job_id TEXT,
                attempt_id TEXT NOT NULL UNIQUE REFERENCES run_attempts(id)
                    ON DELETE RESTRICT,
                plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
                plan_version_id TEXT NOT NULL REFERENCES plan_versions(id)
                    ON DELETE RESTRICT,
                engine_build_id TEXT NOT NULL REFERENCES engine_builds(id)
                    ON DELETE RESTRICT,
                snapshot_id TEXT REFERENCES run_snapshots(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT,
                CHECK (status != 'completed' OR snapshot_id IS NOT NULL)
            )
            """,
            "CREATE INDEX IF NOT EXISTS run_requests_attempt_idx "
            "ON run_requests(attempt_id)",
        ]

    @staticmethod
    def _schema_statements() -> list[str]:
        return [
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL,
                app_release_id TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS engine_builds (
                id TEXT PRIMARY KEY,
                engine_version TEXT NOT NULL,
                code_manifest_sha256 TEXT,
                data_manifest_sha256 TEXT,
                protocol_version TEXT NOT NULL,
                environment_json TEXT NOT NULL,
                source_manifest_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS plans (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                source_key TEXT,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'archived', 'deleted')),
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS plan_versions (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
                parent_version_id TEXT REFERENCES plan_versions(id)
                    ON DELETE RESTRICT,
                source_kind TEXT NOT NULL,
                source_config_json TEXT NOT NULL,
                source_config_sha256 TEXT NOT NULL,
                normalized_config_json TEXT NOT NULL,
                normalized_config_sha256 TEXT NOT NULL,
                config_schema_version INTEGER NOT NULL,
                canonicalizer_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS run_attempts (
                id TEXT PRIMARY KEY,
                job_id TEXT,
                plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
                plan_version_id TEXT NOT NULL REFERENCES plan_versions(id)
                    ON DELETE RESTRICT,
                engine_build_id TEXT NOT NULL REFERENCES engine_builds(id)
                    ON DELETE RESTRICT,
                status TEXT NOT NULL
                    CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
                precision TEXT NOT NULL,
                requested_paths INTEGER NOT NULL,
                effective_paths INTEGER NOT NULL,
                dist_paths INTEGER NOT NULL,
                requested_dist_paths INTEGER NOT NULL,
                effective_dist_paths INTEGER NOT NULL,
                seed INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT,
                snapshot_id TEXT,
                execution_mode TEXT,
                CHECK (status != 'completed' OR snapshot_id IS NOT NULL),
                CHECK (execution_mode IS NULL OR execution_mode = 'sequential'
                      OR execution_mode LIKE 'chunked-%')
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS run_snapshots (
                id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL UNIQUE REFERENCES run_attempts(id)
                    ON DELETE RESTRICT,
                plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
                plan_version_id TEXT NOT NULL REFERENCES plan_versions(id)
                    ON DELETE RESTRICT,
                engine_build_id TEXT NOT NULL REFERENCES engine_builds(id)
                    ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                resolved_input_json TEXT NOT NULL,
                resolved_input_sha256 TEXT NOT NULL,
                protocol_json TEXT NOT NULL,
                protocol_sha256 TEXT NOT NULL,
                replay_payload_json TEXT NOT NULL,
                replay_payload_sha256 TEXT NOT NULL,
                result_json TEXT NOT NULL,
                result_archive_sha256 TEXT NOT NULL,
                deterministic_result_sha256 TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS run_requests (
                request_id TEXT PRIMARY KEY,
                fingerprint_sha256 TEXT NOT NULL,
                status TEXT NOT NULL
                    CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
                job_id TEXT,
                attempt_id TEXT NOT NULL UNIQUE REFERENCES run_attempts(id)
                    ON DELETE RESTRICT,
                plan_id TEXT NOT NULL REFERENCES plans(id) ON DELETE RESTRICT,
                plan_version_id TEXT NOT NULL REFERENCES plan_versions(id)
                    ON DELETE RESTRICT,
                engine_build_id TEXT NOT NULL REFERENCES engine_builds(id)
                    ON DELETE RESTRICT,
                snapshot_id TEXT REFERENCES run_snapshots(id) ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT,
                CHECK (status != 'completed' OR snapshot_id IS NOT NULL)
            )
            """,
            *PersistenceStore._trigger_statements(),
            *PersistenceStore._request_trigger_statements(),
            *PersistenceStore._v5_trigger_statements(),
            *PersistenceStore._v6_trigger_statements(),
            "CREATE INDEX IF NOT EXISTS plan_versions_plan_idx ON plan_versions(plan_id, created_at, id)",
            "CREATE INDEX IF NOT EXISTS run_snapshots_plan_idx ON run_snapshots(plan_id, created_at, id)",
            "CREATE INDEX IF NOT EXISTS run_attempts_plan_idx ON run_attempts(plan_id, started_at, id)",
            "CREATE INDEX IF NOT EXISTS run_requests_attempt_idx ON run_requests(attempt_id)",
        ]

    @staticmethod
    def _v7_table_statements() -> list[str]:
        """Install the additive restore/evidence surface for archive v7.

        The final attribution flow tables are intentionally absent.  This
        migration only makes a restored archive able to retain migration
        evidence, recovered drafts, and the authority read model.
        """
        return [
            """
            CREATE TABLE migration_operations (
              operation_id TEXT PRIMARY KEY, envelope_sha256 TEXT NOT NULL,
              control_operation_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
              retry_of_operation_id TEXT REFERENCES migration_operations(operation_id),
              projection_version TEXT NOT NULL, normalizer_version TEXT NOT NULL,
              config_schema_version TEXT NOT NULL, status TEXT NOT NULL CHECK (status IN
                ('previewed','raw_backed_up','imported','verified','cutover_marked',
                 'source_changed','failed','manual_recovery_required')),
              target_count INTEGER NOT NULL CHECK (target_count >= 0),
              target_hash TEXT NOT NULL, error_code TEXT, created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL,
              UNIQUE (envelope_sha256, projection_version, normalizer_version,
                      config_schema_version, attempt_id)
            )
            """,
            """
            CREATE TABLE migration_source_records (
              operation_id TEXT NOT NULL REFERENCES migration_operations(operation_id),
              source_record_id TEXT NOT NULL, source_key TEXT NOT NULL,
              json_pointer TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL,
              source_kind TEXT NOT NULL CHECK (source_kind IN
                ('plan','draft','legacy_checkin','unknown')),
              archive_resolution TEXT NOT NULL CHECK (archive_resolution IN
                ('created','reused_validated','evidence_only','quarantined',
                 'structural_missing')),
              target_count INTEGER NOT NULL CHECK (target_count >= 0),
              target_hash TEXT NOT NULL,
              PRIMARY KEY (operation_id, source_record_id),
              UNIQUE (operation_id, source_key, json_pointer)
            )
            """,
            """
            CREATE TABLE migration_source_targets (
              operation_id TEXT NOT NULL, source_record_id TEXT NOT NULL,
              target_ordinal INTEGER NOT NULL CHECK (target_ordinal >= 0),
              target_kind TEXT NOT NULL CHECK (target_kind IN
                ('plan','plan_version','recovered_draft','legacy_checkin_evidence')),
              target_id TEXT NOT NULL, target_hash TEXT NOT NULL,
              PRIMARY KEY (operation_id, source_record_id, target_ordinal),
              FOREIGN KEY (operation_id, source_record_id)
                REFERENCES migration_source_records(operation_id, source_record_id)
            )
            """,
            """
            CREATE TABLE recovered_drafts (
              draft_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL
                REFERENCES migration_operations(operation_id), source_key TEXT NOT NULL,
              json_pointer TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL,
              raw_json TEXT NOT NULL, normalized_json TEXT NOT NULL,
              status TEXT NOT NULL CHECK (status IN ('recovered','quarantined')),
              created_at TEXT NOT NULL,
              UNIQUE (operation_id, source_key, json_pointer)
            )
            """,
            """
            CREATE TABLE recovered_draft_events (
              event_id TEXT PRIMARY KEY, draft_id TEXT NOT NULL
                REFERENCES recovered_drafts(draft_id),
              migration_operation_id TEXT NOT NULL
                REFERENCES migration_operations(operation_id),
              control_operation_id TEXT NOT NULL,
              status TEXT NOT NULL CHECK (status='user_saved'),
              target_plan_id TEXT NOT NULL REFERENCES plans(id),
              target_plan_version_id TEXT NOT NULL REFERENCES plan_versions(id),
              target_hash TEXT NOT NULL, receipt_sha256 TEXT NOT NULL,
              created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE migration_authority (
              singleton_id INTEGER PRIMARY KEY CHECK (singleton_id=1),
              status TEXT NOT NULL CHECK (status IN
                ('legacy_authoritative','sqlite_preferred','source_changed',
                 'manual_recovery_required')),
              operation_id TEXT,
              operation_kind TEXT CHECK (operation_kind IS NULL OR operation_kind IN
                ('migration','restore','raw_restore','recovery','archive_write',
                 'observation')),
              envelope_sha256 TEXT, target_count INTEGER NOT NULL,
              target_hash TEXT NOT NULL, legacy_digest_last_seen TEXT,
              updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE migration_authority_events (
              event_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL,
              operation_kind TEXT NOT NULL CHECK (operation_kind IN
                ('migration','restore','raw_restore','recovery','archive_write',
                 'observation')),
              from_status TEXT NOT NULL, to_status TEXT NOT NULL,
              external_receipt_sha256 TEXT NOT NULL, expected_generation TEXT NOT NULL,
              new_generation TEXT NOT NULL, envelope_sha256 TEXT,
              external_receipt_mac TEXT NOT NULL,
              target_count INTEGER NOT NULL CHECK (target_count >= 0),
              target_hash TEXT NOT NULL, legacy_digest_last_seen TEXT,
              created_at TEXT NOT NULL,
              CHECK ((from_status='legacy_authoritative' AND to_status='sqlite_preferred') OR
                     (from_status='sqlite_preferred' AND to_status='source_changed') OR
                     (from_status='source_changed' AND to_status='sqlite_preferred') OR
                     (to_status='manual_recovery_required'))
            )
            """,
            """
            CREATE TABLE legacy_checkin_evidence (
              evidence_id TEXT PRIMARY KEY, operation_id TEXT NOT NULL
                REFERENCES migration_operations(operation_id), source_key TEXT NOT NULL,
              json_pointer TEXT NOT NULL, raw_record_sha256 TEXT NOT NULL,
              observed_date TEXT, observed_age REAL, actual_total_nominal REAL,
              status TEXT NOT NULL CHECK (status='incomplete_inputs'),
              raw_json TEXT NOT NULL,
              UNIQUE (operation_id, source_key, json_pointer)
            )
            """,
        ]

    @staticmethod
    def _v7_trigger_statements() -> list[str]:
        """Install finite v7 identity, append-only, and state guards."""
        return [
            "CREATE UNIQUE INDEX migration_source_pointer_uq ON "
            "migration_source_records(operation_id, source_key, json_pointer)",
            """
            CREATE TRIGGER migration_operation_initial_state
            BEFORE INSERT ON migration_operations
            WHEN NEW.status != 'previewed'
            BEGIN SELECT RAISE(ABORT,'migration operation must start previewed'); END
            """,
            """
            CREATE TRIGGER migration_operation_no_delete
            BEFORE DELETE ON migration_operations
            BEGIN SELECT RAISE(ABORT,'migration operations are append-only'); END
            """,
            """
            CREATE TRIGGER migration_source_no_update
            BEFORE UPDATE ON migration_source_records
            BEGIN SELECT RAISE(ABORT,'migration source records are immutable'); END
            """,
            """
            CREATE TRIGGER migration_source_no_delete
            BEFORE DELETE ON migration_source_records
            BEGIN SELECT RAISE(ABORT,'migration source records are append-only'); END
            """,
            """
            CREATE TRIGGER migration_source_target_no_update
            BEFORE UPDATE ON migration_source_targets
            BEGIN SELECT RAISE(ABORT,'migration target map is immutable'); END
            """,
            """
            CREATE TRIGGER migration_source_target_no_delete
            BEFORE DELETE ON migration_source_targets
            BEGIN SELECT RAISE(ABORT,'migration target map is append-only'); END
            """,
            """
            CREATE TRIGGER migration_operation_transition
            BEFORE UPDATE OF status ON migration_operations
            WHEN NOT (
              (OLD.status='previewed' AND NEW.status IN
                 ('raw_backed_up','failed','manual_recovery_required')) OR
              (OLD.status='raw_backed_up' AND NEW.status IN
                 ('imported','failed','manual_recovery_required')) OR
              (OLD.status='imported' AND NEW.status IN
                 ('verified','source_changed','failed','manual_recovery_required')) OR
              (OLD.status='verified' AND NEW.status IN
                 ('cutover_marked','source_changed','failed','manual_recovery_required')))
            BEGIN SELECT RAISE(ABORT,'invalid migration operation transition'); END
            """,
            """
            CREATE TRIGGER migration_operation_identity
            BEFORE UPDATE ON migration_operations
            WHEN NEW.operation_id IS NOT OLD.operation_id OR
                 NEW.attempt_id IS NOT OLD.attempt_id OR
                 NEW.retry_of_operation_id IS NOT OLD.retry_of_operation_id OR
                 NEW.envelope_sha256 IS NOT OLD.envelope_sha256 OR
                 NEW.projection_version IS NOT OLD.projection_version OR
                 NEW.normalizer_version IS NOT OLD.normalizer_version OR
                 NEW.config_schema_version IS NOT OLD.config_schema_version OR
                 NEW.target_count IS NOT OLD.target_count OR
                 NEW.target_hash IS NOT OLD.target_hash OR
                 NEW.created_at IS NOT OLD.created_at
            BEGIN SELECT RAISE(ABORT,'migration operation identity is immutable'); END
            """,
            """
            CREATE TRIGGER migration_operation_duplicate
            BEFORE INSERT ON migration_operations
            WHEN EXISTS (SELECT 1 FROM migration_operations
                         WHERE operation_id=NEW.operation_id)
              OR EXISTS (SELECT 1 FROM migration_operations
                         WHERE envelope_sha256=NEW.envelope_sha256
                           AND projection_version=NEW.projection_version
                           AND normalizer_version=NEW.normalizer_version
                           AND config_schema_version=NEW.config_schema_version
                           AND attempt_id=NEW.attempt_id)
            BEGIN SELECT RAISE(ABORT,'migration operation identity already exists'); END
            """,
            """
            CREATE TRIGGER migration_source_record_duplicate
            BEFORE INSERT ON migration_source_records
            WHEN EXISTS (SELECT 1 FROM migration_source_records
                         WHERE operation_id=NEW.operation_id
                           AND source_record_id=NEW.source_record_id)
              OR EXISTS (SELECT 1 FROM migration_source_records
                         WHERE operation_id=NEW.operation_id
                           AND source_key=NEW.source_key
                           AND json_pointer=NEW.json_pointer)
            BEGIN SELECT RAISE(ABORT,'migration source identity already exists'); END
            """,
            """
            CREATE TRIGGER migration_source_operation_root
            BEFORE INSERT ON migration_source_records
            WHEN NOT EXISTS (SELECT 1 FROM migration_operations o
                             WHERE o.operation_id=NEW.operation_id
                               AND o.status IN ('previewed','raw_backed_up',
                                                'imported','verified'))
            BEGIN SELECT RAISE(ABORT,'migration source has no live operation root'); END
            """,
            """
            CREATE TRIGGER migration_source_target_duplicate
            BEFORE INSERT ON migration_source_targets
            WHEN EXISTS (SELECT 1 FROM migration_source_targets
                         WHERE operation_id=NEW.operation_id
                           AND source_record_id=NEW.source_record_id
                           AND target_ordinal=NEW.target_ordinal)
            BEGIN SELECT RAISE(ABORT,'migration target identity already exists'); END
            """,
            """
            CREATE TRIGGER migration_source_target_root
            BEFORE INSERT ON migration_source_targets
            WHEN NOT EXISTS (SELECT 1 FROM migration_source_records s
                             WHERE s.operation_id=NEW.operation_id
                               AND s.source_record_id=NEW.source_record_id)
            BEGIN SELECT RAISE(ABORT,'migration target has no source root'); END
            """,
            """
            CREATE TRIGGER authority_singleton
            BEFORE INSERT ON migration_authority
            WHEN EXISTS (SELECT 1 FROM migration_authority
                         WHERE singleton_id=NEW.singleton_id)
            BEGIN SELECT RAISE(ABORT,'authority singleton already exists'); END
            """,
            """
            CREATE TRIGGER authority_no_delete
            BEFORE DELETE ON migration_authority
            BEGIN SELECT RAISE(ABORT,'migration authority is append-only'); END
            """,
            """
            CREATE TRIGGER authority_event_duplicate
            BEFORE INSERT ON migration_authority_events
            WHEN EXISTS (SELECT 1 FROM migration_authority_events
                         WHERE event_id=NEW.event_id)
            BEGIN SELECT RAISE(ABORT,'authority event identity already exists'); END
            """,
            """
            CREATE TRIGGER authority_event_no_update
            BEFORE UPDATE ON migration_authority_events
            BEGIN SELECT RAISE(ABORT,'authority events are immutable'); END
            """,
            """
            CREATE TRIGGER authority_event_no_delete
            BEFORE DELETE ON migration_authority_events
            BEGIN SELECT RAISE(ABORT,'authority events are append-only'); END
            """,
            """
            CREATE TRIGGER migration_authority_update_guard
            BEFORE UPDATE ON migration_authority
            WHEN NOT EXISTS (SELECT 1 FROM migration_authority_events e
                             WHERE e.from_status=OLD.status
                               AND e.to_status=NEW.status
                               AND e.operation_id=NEW.operation_id
                               AND e.operation_kind IS NEW.operation_kind
                               AND e.envelope_sha256 IS NEW.envelope_sha256
                               AND e.target_count=NEW.target_count
                               AND e.target_hash=NEW.target_hash
                               AND e.legacy_digest_last_seen IS NEW.legacy_digest_last_seen)
            BEGIN SELECT RAISE(ABORT,'invalid authority transition'); END
            """,
        ]

    @staticmethod
    def _schema_v7_complete(conn: sqlite3.Connection) -> bool:
        required = {
            "migration_operations", "migration_source_records",
            "migration_source_targets", "recovered_drafts",
            "recovered_draft_events", "migration_authority",
            "migration_authority_events", "legacy_checkin_evidence",
        }
        actual = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not required.issubset(actual):
            return False
        required_triggers = {
            "migration_operation_initial_state", "migration_operation_no_delete",
            "migration_source_no_update", "migration_source_no_delete",
            "migration_source_target_no_update", "migration_source_target_no_delete",
            "migration_operation_transition", "migration_operation_identity",
            "migration_operation_duplicate", "migration_source_record_duplicate",
            "migration_source_operation_root", "migration_source_target_duplicate",
            "migration_source_target_root", "authority_singleton", "authority_no_delete",
            "authority_event_duplicate", "authority_event_no_update",
            "authority_event_no_delete", "migration_authority_update_guard",
        }
        triggers = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        if not required_triggers.issubset(triggers):
            return False
        indexes = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'")}
        return "migration_source_pointer_uq" in indexes

    @staticmethod
    def _v8_table_statements() -> list[str]:
        """Install the additive M2 lineage surface for archive schema v8."""
        return [
            """
            CREATE TABLE legacy_checkin_lineage (
              evidence_id TEXT PRIMARY KEY REFERENCES legacy_checkin_evidence(evidence_id),
              operation_id TEXT NOT NULL REFERENCES migration_operations(operation_id),
              target_plan_id TEXT NOT NULL REFERENCES plans(id),
              target_plan_version_id TEXT NOT NULL REFERENCES plan_versions(id),
              target_hash TEXT NOT NULL CHECK (length(target_hash)=64),
              created_at TEXT NOT NULL,
              UNIQUE (operation_id, evidence_id)
            )
            """,
        ]

    @staticmethod
    def _v8_trigger_statements() -> list[str]:
        """Install immutable lineage guards and the one M2 root fill edge."""
        return [
            "DROP TRIGGER IF EXISTS migration_operation_identity",
            f"""
            CREATE TRIGGER migration_operation_identity
            BEFORE UPDATE ON migration_operations
            WHEN NEW.operation_id IS NOT OLD.operation_id OR
                 NEW.attempt_id IS NOT OLD.attempt_id OR
                 NEW.retry_of_operation_id IS NOT OLD.retry_of_operation_id OR
                 NEW.envelope_sha256 IS NOT OLD.envelope_sha256 OR
                 NEW.projection_version IS NOT OLD.projection_version OR
                 NEW.normalizer_version IS NOT OLD.normalizer_version OR
                 NEW.config_schema_version IS NOT OLD.config_schema_version OR
                 ((NEW.target_count IS NOT OLD.target_count OR
                   NEW.target_hash IS NOT OLD.target_hash) AND NOT (
                     OLD.status='raw_backed_up' AND NEW.status='imported' AND
                     OLD.target_count=0 AND
                     OLD.target_hash='{MIGRATION_EMPTY_TARGET_HASH}')) OR
                 NEW.created_at IS NOT OLD.created_at
            BEGIN SELECT RAISE(ABORT,'migration operation identity is immutable'); END
            """,
            """
            CREATE TRIGGER legacy_checkin_lineage_duplicate
            BEFORE INSERT ON legacy_checkin_lineage
            WHEN EXISTS (SELECT 1 FROM legacy_checkin_lineage
                         WHERE evidence_id=NEW.evidence_id)
              OR EXISTS (SELECT 1 FROM legacy_checkin_lineage
                         WHERE operation_id=NEW.operation_id
                           AND evidence_id=NEW.evidence_id)
            BEGIN SELECT RAISE(ABORT,'legacy checkin lineage identity already exists'); END
            """,
            """
            CREATE TRIGGER legacy_checkin_lineage_root
            BEFORE INSERT ON legacy_checkin_lineage
            WHEN NOT EXISTS (SELECT 1 FROM legacy_checkin_evidence e
                             JOIN migration_operations o
                               ON o.operation_id=e.operation_id
                             JOIN plans p ON p.id=NEW.target_plan_id
                             JOIN plan_versions v
                               ON v.id=NEW.target_plan_version_id
                              AND v.plan_id=NEW.target_plan_id
                             WHERE e.evidence_id=NEW.evidence_id
                               AND e.operation_id=NEW.operation_id
                               AND o.status IN ('raw_backed_up','imported','verified'))
            BEGIN SELECT RAISE(ABORT,'legacy checkin lineage root mismatch'); END
            """,
            """
            CREATE TRIGGER legacy_checkin_lineage_no_update
            BEFORE UPDATE ON legacy_checkin_lineage
            BEGIN SELECT RAISE(ABORT,'legacy checkin lineage is immutable'); END
            """,
            """
            CREATE TRIGGER legacy_checkin_lineage_no_delete
            BEFORE DELETE ON legacy_checkin_lineage
            BEGIN SELECT RAISE(ABORT,'legacy checkin lineage is append-only'); END
            """,
        ]

    @staticmethod
    def _v9_table_statements() -> list[str]:
        """Phase 2's CheckIn ledger: a header and an immutable raw ledger.

        `flow_line_v2` is deliberately absent. §2 of the attribution protocol
        says the union join and aggregation "are recomputed on read", so the
        derived rows are a function of these tables rather than a third table
        that could drift out of agreement with them.
        """
        return [
            """
            CREATE TABLE checkins (
              checkin_id TEXT PRIMARY KEY,
              plan_id TEXT NOT NULL REFERENCES plans(id),
              plan_version_id TEXT NOT NULL REFERENCES plan_versions(id),
              forecast_period_start TEXT NOT NULL,
              forecast_period_end TEXT NOT NULL,
              portfolio_currency TEXT NOT NULL,
              portfolio_currency_exponent INTEGER NOT NULL
                  CHECK (portfolio_currency_exponent BETWEEN 0 AND 6),
              portfolio_timezone TEXT NOT NULL,
              opening_value_minor INTEGER NOT NULL,
              closing_value_minor INTEGER NOT NULL,
              starting_state_hash TEXT NOT NULL CHECK (length(starting_state_hash)=64),
              household_scope_hash TEXT NOT NULL CHECK (length(household_scope_hash)=64),
              model_vintage TEXT NOT NULL,
              observation_state TEXT NOT NULL
                  CHECK (observation_state IN
                         ('observed','estimated','unknown','unavailable','declined')),
              source_kind TEXT NOT NULL,
              source_sha256 TEXT CHECK (source_sha256 IS NULL
                                        OR length(source_sha256)=64),
              created_at TEXT NOT NULL,
              supersedes_checkin_id TEXT REFERENCES checkins(checkin_id),
              CHECK (forecast_period_end > forecast_period_start)
            )
            """,
            """
            CREATE TABLE transaction_line_v2 (
              transaction_id TEXT PRIMARY KEY,
              checkin_id TEXT NOT NULL REFERENCES checkins(checkin_id),
              side TEXT NOT NULL CHECK (side IN ('actual','expected')),
              category TEXT NOT NULL
                  CHECK (category IN ('net_contribution','income','spending',
                                      'tax','fee','life_event')),
              source_or_schedule_id TEXT NOT NULL,
              source_event_id TEXT,
              component_leg_id TEXT,
              period_start TEXT NOT NULL,
              period_end TEXT NOT NULL,
              timing_bucket TEXT NOT NULL
                  CHECK (timing_bucket IN ('exact','local_noon','unknown')),
              amount_portfolio_minor INTEGER NOT NULL,
              source_currency TEXT,
              source_amount_minor INTEGER,
              source_currency_exponent INTEGER,
              fx_numerator INTEGER CHECK (fx_numerator IS NULL OR fx_numerator > 0),
              fx_denominator INTEGER CHECK (fx_denominator IS NULL OR fx_denominator > 0),
              fx_vintage TEXT,
              occurred_at TEXT,
              source_timezone TEXT,
              date_only_value TEXT,
              timing_state TEXT NOT NULL
                  CHECK (timing_state IN ('exact','estimated_local_noon','unknown')),
              observation_state TEXT NOT NULL
                  CHECK (observation_state IN
                         ('observed','estimated','unknown','unavailable','declined')),
              is_internal_transfer INTEGER NOT NULL DEFAULT 0
                  CHECK (is_internal_transfer IN (0,1)),
              transfer_group_id TEXT,
              absence_proof_id TEXT,
              source_pointer TEXT,
              source_sha256 TEXT CHECK (source_sha256 IS NULL
                                        OR length(source_sha256)=64),
              supersedes_transaction_id TEXT
                  REFERENCES transaction_line_v2(transaction_id),
              created_at TEXT NOT NULL,
              -- §3: a flow with a weight needs an instant; only `unknown`
              -- timing may omit it.
              CHECK (timing_state = 'unknown' OR occurred_at IS NOT NULL),
              -- §2: an internal transfer is paired, so it must name its group.
              CHECK (is_internal_transfer = 0 OR transfer_group_id IS NOT NULL),
              -- §2: FX is an exact rational or absent entirely.
              CHECK ((fx_numerator IS NULL) = (fx_denominator IS NULL))
            )
            """,
            "CREATE INDEX transaction_line_v2_by_checkin "
            "ON transaction_line_v2(checkin_id, side)",
            # The grain, as an index rather than a unique constraint: several
            # raw rows legitimately share it (that is what a correction is).
            "CREATE INDEX transaction_line_v2_grain ON transaction_line_v2("
            "checkin_id, side, category, source_or_schedule_id, source_event_id, "
            "component_leg_id, period_start, period_end, timing_bucket)",
        ]

    @staticmethod
    def _v9_trigger_statements() -> list[str]:
        """Raw ledger rows are immutable, and a CheckIn is append-only.

        §2 calls `transaction_line_v2` "one immutable row per source
        transaction leg and correction tip". A correction is a new row that
        supersedes an old one; it is never an UPDATE of the old one.
        """
        return [
            """
            CREATE TRIGGER transaction_line_v2_no_update
            BEFORE UPDATE ON transaction_line_v2
            BEGIN SELECT RAISE(ABORT,'raw ledger rows are immutable; a correction is a new row'); END
            """,
            """
            CREATE TRIGGER transaction_line_v2_no_delete
            BEFORE DELETE ON transaction_line_v2
            BEGIN SELECT RAISE(ABORT,'raw ledger rows are immutable; superseded rows are retained'); END
            """,
            """
            CREATE TRIGGER transaction_line_v2_duplicate
            BEFORE INSERT ON transaction_line_v2
            WHEN EXISTS (SELECT 1 FROM transaction_line_v2
                         WHERE transaction_id = NEW.transaction_id)
            BEGIN SELECT RAISE(ABORT,'duplicate raw ledger transaction id'); END
            """,
            """
            CREATE TRIGGER transaction_line_v2_supersede_same_checkin
            BEFORE INSERT ON transaction_line_v2
            WHEN NEW.supersedes_transaction_id IS NOT NULL AND NOT EXISTS (
                 SELECT 1 FROM transaction_line_v2
                 WHERE transaction_id = NEW.supersedes_transaction_id
                   AND checkin_id = NEW.checkin_id AND side = NEW.side)
            BEGIN SELECT RAISE(ABORT,'a correction must supersede a row in the same checkin side'); END
            """,
            """
            CREATE TRIGGER checkins_no_update
            BEFORE UPDATE ON checkins
            BEGIN SELECT RAISE(ABORT,'a CheckIn is immutable; supersede it instead'); END
            """,
            """
            CREATE TRIGGER checkins_no_delete
            BEFORE DELETE ON checkins
            BEGIN SELECT RAISE(ABORT,'a CheckIn is immutable; superseded CheckIns are retained'); END
            """,
            """
            CREATE TRIGGER checkins_duplicate
            BEFORE INSERT ON checkins
            WHEN EXISTS (SELECT 1 FROM checkins WHERE checkin_id = NEW.checkin_id)
            BEGIN SELECT RAISE(ABORT,'duplicate checkin id'); END
            """,
        ]

    @staticmethod
    def _v10_table_statements() -> list[str]:
        """Phase 4's decision record: an immutable packet and its state ledger.

        ROADMAP 4.0 Phase 4 opened with "the review view has nothing to
        review": a DecisionPacket only ever lived in `app.py`'s in-memory job
        table, and `set_choice_state` moved a dict that died with the process.
        The user ruled on 2026-08-14 that packets land in the archive, choice
        state included. This is that landing.

        Two tables rather than one, for the reason `_v9_table_statements`
        gives about `flow_line_v2`: the current state is a FUNCTION of the
        transitions, so it is recomputed on read instead of being a column
        that can disagree with the history beside it. A record that can say
        `chosen` while its own history says otherwise is not a record.

        The body is stored WITHOUT `choice_state` -- that field is the ledger
        below, and holding it in both places is the same drift by another
        route. `body_sha256` is over what is stored, so an archived packet can
        be re-verified rather than trusted.

        The precision CHECK repeats `decision_packet.build_packet`'s refusal
        on purpose. Every packet today comes from that function, but the
        archive is the durable side and should not be able to hold a Robust
        claim computed at a precision that cannot carry one, whatever writes
        it.
        """
        states = "', '".join(DECISION_STATES)
        return [
            f"""
            CREATE TABLE decision_packets (
              packet_id TEXT PRIMARY KEY,
              plan_id TEXT NOT NULL REFERENCES plans(id),
              plan_version_id TEXT NOT NULL REFERENCES plan_versions(id),
              question_id TEXT NOT NULL,
              question TEXT NOT NULL,
              packet_format TEXT NOT NULL,
              engine_version TEXT NOT NULL,
              precision TEXT NOT NULL CHECK (precision IN ('standard','official')),
              paths INTEGER NOT NULL CHECK (paths > 0),
              seed INTEGER NOT NULL,
              true_tax INTEGER NOT NULL CHECK (true_tax IN (0,1)),
              review_months INTEGER NOT NULL CHECK (review_months > 0),
              body_json TEXT NOT NULL,
              body_sha256 TEXT NOT NULL CHECK (length(body_sha256)=64),
              created_at TEXT NOT NULL
            )
            """,
            f"""
            CREATE TABLE decision_packet_events (
              event_id TEXT PRIMARY KEY,
              packet_id TEXT NOT NULL REFERENCES decision_packets(packet_id),
              seq INTEGER NOT NULL CHECK (seq > 0),
              from_state TEXT NOT NULL CHECK (from_state IN ('{states}')),
              to_state TEXT NOT NULL CHECK (to_state IN ('{states}')),
              reason TEXT NOT NULL CHECK (length(trim(reason)) > 0),
              at TEXT NOT NULL,
              created_at TEXT NOT NULL,
              UNIQUE (packet_id, seq),
              -- A transition to the state it is already in records nothing.
              CHECK (from_state <> to_state)
            )
            """,
            "CREATE INDEX decision_packets_by_plan "
            "ON decision_packets(plan_id, created_at)",
            "CREATE INDEX decision_packet_events_by_packet "
            "ON decision_packet_events(packet_id, seq)",
        ]

    @staticmethod
    def _v10_trigger_statements() -> list[str]:
        """A decision record cannot be rewritten, and its history cannot be forged.

        The sequence guard is the one worth reading. Python's `_TRANSITIONS`
        decides which moves are LEGAL; this decides that the history is a
        chain -- an appended event must start from the state the packet is
        actually in, and must take the next sequence number. Without it a
        writer that skipped the seam could append `open -> chosen` to a packet
        already declined, and every reader downstream would believe it.

        `superseded` is final here as well as in Python, because that is the
        one transition rule whose whole purpose is that the record cannot be
        walked back later.
        """
        return [
            """
            CREATE TRIGGER decision_packets_no_update
            BEFORE UPDATE ON decision_packets
            BEGIN SELECT RAISE(ABORT,'an archived decision packet is immutable; a new decision is a new packet'); END
            """,
            """
            CREATE TRIGGER decision_packets_no_delete
            BEFORE DELETE ON decision_packets
            BEGIN SELECT RAISE(ABORT,'archived decision packets are retained; supersede instead'); END
            """,
            """
            CREATE TRIGGER decision_packets_duplicate
            BEFORE INSERT ON decision_packets
            WHEN EXISTS (SELECT 1 FROM decision_packets WHERE packet_id = NEW.packet_id)
            BEGIN SELECT RAISE(ABORT,'duplicate decision packet id'); END
            """,
            """
            CREATE TRIGGER decision_packet_events_no_update
            BEFORE UPDATE ON decision_packet_events
            BEGIN SELECT RAISE(ABORT,'a decision transition is immutable; changing your mind is a new transition'); END
            """,
            """
            CREATE TRIGGER decision_packet_events_no_delete
            BEFORE DELETE ON decision_packet_events
            BEGIN SELECT RAISE(ABORT,'decision transitions are append-only'); END
            """,
            """
            CREATE TRIGGER decision_packet_events_duplicate
            BEFORE INSERT ON decision_packet_events
            WHEN EXISTS (SELECT 1 FROM decision_packet_events WHERE event_id = NEW.event_id)
            BEGIN SELECT RAISE(ABORT,'duplicate decision transition id'); END
            """,
            """
            CREATE TRIGGER decision_packet_events_sequence
            BEFORE INSERT ON decision_packet_events
            WHEN NEW.from_state IS NOT COALESCE(
                   (SELECT to_state FROM decision_packet_events
                     WHERE packet_id = NEW.packet_id
                     ORDER BY seq DESC LIMIT 1), 'open')
              OR NEW.seq IS NOT (SELECT COUNT(*) + 1 FROM decision_packet_events
                                  WHERE packet_id = NEW.packet_id)
            BEGIN SELECT RAISE(ABORT,'decision transition does not continue this packet''s history'); END
            """,
            """
            CREATE TRIGGER decision_packet_events_terminal
            BEFORE INSERT ON decision_packet_events
            WHEN EXISTS (SELECT 1 FROM decision_packet_events
                         WHERE packet_id = NEW.packet_id AND to_state = 'superseded')
            BEGIN SELECT RAISE(ABORT,'a superseded decision is final'); END
            """,
        ]

    @classmethod
    def _schema_v10_complete(cls, conn: sqlite3.Connection) -> bool:
        if not cls._schema_v9_complete(conn):
            return False
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"decision_packets", "decision_packet_events"}.issubset(tables):
            return False
        triggers = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        return {
            "decision_packets_no_update",
            "decision_packets_no_delete",
            "decision_packets_duplicate",
            "decision_packet_events_no_update",
            "decision_packet_events_no_delete",
            "decision_packet_events_duplicate",
            "decision_packet_events_sequence",
            "decision_packet_events_terminal",
        }.issubset(triggers)

    @classmethod
    def install_v10_schema(cls, conn: sqlite3.Connection, *,
                           app_release_id: str) -> None:
        """Add the Phase 4 decision record without rewriting v9 ledger tables."""
        if cls._schema_v10_complete(conn):
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) != 10:
                raise PersistenceError("v10 archive user_version mismatch")
            return
        if (int(conn.execute("PRAGMA user_version").fetchone()[0]) != 9
                or not cls._schema_v9_complete(conn)):
            raise PersistenceError("v9 archive is incomplete before v10 migration")
        cls._validate_state_rows(conn)
        for statement in cls._v10_table_statements():
            conn.execute(statement)
        for statement in cls._v10_trigger_statements():
            conn.execute(statement)
        now = utc_now()
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at, app_release_id) "
            "VALUES (10, ?, ?)", (now, app_release_id))
        conn.execute("PRAGMA user_version = 10")

    @classmethod
    def _schema_v9_complete(cls, conn: sqlite3.Connection) -> bool:
        if not cls._schema_v8_complete(conn):
            return False
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"checkins", "transaction_line_v2"}.issubset(tables):
            return False
        triggers = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        return {
            "transaction_line_v2_no_update",
            "transaction_line_v2_no_delete",
            "transaction_line_v2_duplicate",
            "transaction_line_v2_supersede_same_checkin",
            "checkins_no_update",
            "checkins_no_delete",
            "checkins_duplicate",
        }.issubset(triggers)

    @classmethod
    def install_v9_schema(cls, conn: sqlite3.Connection, *,
                          app_release_id: str) -> None:
        """Add the Phase 2 CheckIn ledger without rewriting v8 business tables."""
        if cls._schema_v9_complete(conn):
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) != 9:
                raise PersistenceError("v9 archive user_version mismatch")
            return
        if (int(conn.execute("PRAGMA user_version").fetchone()[0]) != 8
                or not cls._schema_v8_complete(conn)):
            raise PersistenceError("v8 archive is incomplete before v9 migration")
        cls._validate_state_rows(conn)
        for statement in cls._v9_table_statements():
            conn.execute(statement)
        for statement in cls._v9_trigger_statements():
            conn.execute(statement)
        now = utc_now()
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at, app_release_id) "
            "VALUES (9, ?, ?)", (now, app_release_id))
        conn.execute("PRAGMA user_version = 9")

    @classmethod
    def _schema_v8_complete(cls, conn: sqlite3.Connection) -> bool:
        if not cls._schema_v7_complete(conn):
            return False
        tables = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "legacy_checkin_lineage" not in tables:
            return False
        triggers = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        return {
            "migration_operation_identity",
            "legacy_checkin_lineage_duplicate",
            "legacy_checkin_lineage_root",
            "legacy_checkin_lineage_no_update",
            "legacy_checkin_lineage_no_delete",
        }.issubset(triggers)

    @classmethod
    def install_v8_schema(cls, conn: sqlite3.Connection, *, app_release_id: str) -> None:
        """Add the M2 lineage table without rewriting v7 business tables."""
        if cls._schema_v8_complete(conn):
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) != 8:
                raise PersistenceError("v8 archive user_version mismatch")
            return
        if (int(conn.execute("PRAGMA user_version").fetchone()[0]) != 7
                or not cls._schema_v7_complete(conn)):
            raise PersistenceError("v7 archive is incomplete before v8 migration")
        cls._validate_state_rows(conn)
        for statement in cls._v8_table_statements():
            conn.execute(statement)
        for statement in cls._v8_trigger_statements():
            conn.execute(statement)
        now = utc_now()
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at, app_release_id) "
            "VALUES (8, ?, ?)", (now, app_release_id))
        conn.execute("PRAGMA user_version = 8")

    @classmethod
    def install_v7_schema(cls, conn: sqlite3.Connection, *, app_release_id: str) -> None:
        """Atomically add the restore-only v7 evidence surface to a v6 DB."""
        if not cls._schema_complete(conn, include_v2=True, include_v3=True,
                                    include_v4=True, include_v5=True,
                                    include_v6=True):
            raise PersistenceError("v6 archive is incomplete before v7 migration")
        cls._validate_state_rows(conn)
        if cls._schema_v7_complete(conn):
            if int(conn.execute("PRAGMA user_version").fetchone()[0]) != 7:
                raise PersistenceError("v7 archive user_version mismatch")
            return
        for statement in cls._v7_table_statements():
            conn.execute(statement)
        for statement in cls._v7_trigger_statements():
            conn.execute(statement)
        now = utc_now()
        conn.execute(
            "INSERT INTO schema_migrations(version, applied_at, app_release_id) "
            "VALUES (7, ?, ?)", (now, app_release_id))
        conn.execute(
            "INSERT INTO migration_authority "
            "(singleton_id,status,operation_id,operation_kind,envelope_sha256,"
            "target_count,target_hash,legacy_digest_last_seen,updated_at) "
            "VALUES (1,'legacy_authoritative',NULL,NULL,NULL,0,?,NULL,?)",
            ("4f53cda18c2baa0c0354bb5f9a3ecbe5ed12ab4d8e11ba873c2f11161202b945", now))
        conn.execute("PRAGMA user_version = 7")

    @staticmethod
    def _required_columns(include_v2: bool, include_v3: bool = False) -> dict[str, set[str]]:
        required = {
            "schema_migrations": {"version", "applied_at", "app_release_id"},
            "engine_builds": {"id", "engine_version", "code_manifest_sha256",
                               "data_manifest_sha256", "protocol_version",
                               "environment_json", "source_manifest_json", "created_at"},
            "plans": {"id", "display_name", "source_key", "status", "created_at"},
            "plan_versions": {"id", "plan_id", "parent_version_id", "source_kind",
                              "source_config_json", "source_config_sha256",
                              "normalized_config_json", "normalized_config_sha256",
                              "config_schema_version", "canonicalizer_version", "created_at"},
            "run_attempts": {"id", "job_id", "plan_id", "plan_version_id",
                             "engine_build_id", "status", "precision", "requested_paths",
                             "effective_paths", "dist_paths", "seed", "started_at",
                             "finished_at", "error", "snapshot_id"},
            "run_snapshots": {"id", "attempt_id", "plan_id", "plan_version_id",
                              "engine_build_id", "created_at", "resolved_input_json",
                              "resolved_input_sha256", "protocol_json", "protocol_sha256",
                              "replay_payload_json", "replay_payload_sha256", "result_json",
                              "result_archive_sha256", "deterministic_result_sha256"},
        }
        if include_v2:
            required["run_attempts"].update({
                "requested_dist_paths", "effective_dist_paths", "execution_mode"
            })
        if include_v3:
            required["run_requests"] = {
                "request_id", "fingerprint_sha256", "status", "job_id",
                "attempt_id", "plan_id", "plan_version_id", "engine_build_id",
                "snapshot_id", "created_at", "finished_at", "error",
            }
        return required

    @staticmethod
    def _schema_complete(conn: sqlite3.Connection, *, include_v2: bool,
                         include_v3: bool = False,
                         include_v4: bool = False,
                         include_v5: bool = False,
                         include_v6: bool = False) -> bool:
        required = PersistenceStore._required_columns(include_v2, include_v3)
        for table, columns in required.items():
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,)).fetchone()
            if exists is None:
                return False
            actual = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
            if not columns.issubset(actual):
                return False
        if include_v2:
            required_foreign_keys = {
                "plan_versions": {
                    ("plans", "plan_id", "id"),
                    ("plan_versions", "parent_version_id", "id"),
                },
                "run_attempts": {
                    ("plans", "plan_id", "id"),
                    ("plan_versions", "plan_version_id", "id"),
                    ("engine_builds", "engine_build_id", "id"),
                },
                "run_snapshots": {
                    ("run_attempts", "attempt_id", "id"),
                    ("plans", "plan_id", "id"),
                    ("plan_versions", "plan_version_id", "id"),
                    ("engine_builds", "engine_build_id", "id"),
                },
            }
            if include_v3:
                required_foreign_keys["run_requests"] = {
                    ("run_attempts", "attempt_id", "id"),
                    ("plans", "plan_id", "id"),
                    ("plan_versions", "plan_version_id", "id"),
                    ("engine_builds", "engine_build_id", "id"),
                    ("run_snapshots", "snapshot_id", "id"),
                }
            for table, required_keys in required_foreign_keys.items():
                actual_keys = {
                    (row[2], row[3], row[4])
                    for row in conn.execute(f"PRAGMA foreign_key_list({table})")
                }
                if not required_keys.issubset(actual_keys):
                    return False
        base_triggers = {
            "plan_versions_immutable_update", "plan_versions_immutable_delete",
            "run_snapshots_immutable_update", "run_snapshots_immutable_delete",
            "engine_builds_immutable_update", "engine_builds_immutable_delete",
        }
        if include_v2:
            base_triggers.update({
                "plan_versions_parent_plan_guard",
                "run_attempts_plan_lineage_insert_guard",
                "run_attempts_plan_lineage_update_guard",
                "run_attempts_running_bindings_immutable",
                "run_attempts_terminal_immutable",
                "run_attempts_completed_guard",
                "run_attempts_completed_insert_guard",
                "run_attempts_snapshot_ref_insert_guard",
                "run_attempts_snapshot_ref_update_guard",
                "run_attempts_immutable_delete",
                "run_snapshots_binding_guard",
            })
        if include_v3:
            base_triggers.update({
                "run_requests_binding_immutable",
                "run_requests_terminal_immutable",
                "run_requests_completed_guard",
                "run_requests_attempt_status_guard",
            })
        if include_v4:
            base_triggers.update({
                "run_attempts_request_insert_contract",
                "run_attempts_request_update_contract",
                "run_attempts_request_mirror",
                "run_requests_insert_guard",
                "run_requests_delete_guard",
                "run_requests_state_lineage_guard",
            })
        if include_v5:
            base_triggers.update({
                "schema_migrations_duplicate_insert_guard",
                "schema_migrations_immutable_update",
                "schema_migrations_immutable_delete",
                "engine_builds_duplicate_insert_guard",
                "plan_versions_duplicate_insert_guard",
                "run_attempts_duplicate_insert_guard",
                "run_snapshots_duplicate_insert_guard",
            })
        if include_v6:
            base_triggers.update({
                "plans_duplicate_insert_guard",
                "plans_identity_immutable",
                "plans_immutable_delete",
                "run_attempts_identity_immutable",
            })
        actual_triggers = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'")}
        return base_triggers.issubset(actual_triggers)

    @staticmethod
    def _validate_state_rows(conn: sqlite3.Connection) -> None:
        """Validate every v3+ state row without join-eliding corruption.

        Each query is rooted in the row being validated and uses NOT EXISTS
        for its required lineage.  A broken relation therefore remains visible
        to the validator instead of disappearing through an INNER JOIN.
        """
        bad_version = conn.execute(
            """
            SELECT v.id
              FROM plan_versions v
             WHERE NOT EXISTS (
                       SELECT 1 FROM plans p WHERE p.id = v.plan_id)
                OR (v.parent_version_id IS NOT NULL AND NOT EXISTS (
                       SELECT 1 FROM plan_versions parent
                        WHERE parent.id = v.parent_version_id
                          AND parent.plan_id = v.plan_id))
             LIMIT 1
            """).fetchone()
        if bad_version is not None:
            raise PersistenceError("schema contains invalid plan version lineage")

        bad_attempt = conn.execute(
            """
            SELECT a.id
              FROM run_attempts a
             WHERE NOT EXISTS (
                       SELECT 1 FROM plans p WHERE p.id = a.plan_id)
                OR NOT EXISTS (
                       SELECT 1 FROM plan_versions v
                        WHERE v.id = a.plan_version_id
                          AND v.plan_id = a.plan_id)
                OR NOT EXISTS (
                       SELECT 1 FROM engine_builds b
                        WHERE b.id = a.engine_build_id)
                OR a.status NOT IN ('running', 'completed', 'failed', 'cancelled')
                OR (a.status = 'running' AND (
                       a.finished_at IS NOT NULL OR a.error IS NOT NULL OR
                       a.snapshot_id IS NOT NULL))
                OR (a.status IN ('failed', 'cancelled') AND (
                       a.finished_at IS NULL OR a.snapshot_id IS NOT NULL))
                OR (a.status = 'completed' AND (
                       a.finished_at IS NULL OR a.snapshot_id IS NULL OR
                       a.error IS NOT NULL))
                OR (a.snapshot_id IS NOT NULL AND NOT EXISTS (
                       SELECT 1 FROM run_snapshots s
                        WHERE s.id = a.snapshot_id
                          AND s.attempt_id = a.id
                          AND s.plan_id = a.plan_id
                          AND s.plan_version_id = a.plan_version_id
                          AND s.engine_build_id = a.engine_build_id))
             LIMIT 1
            """).fetchone()
        if bad_attempt is not None:
            raise PersistenceError("schema contains invalid attempt state or lineage")

        bad_snapshot = conn.execute(
            """
            SELECT s.id
              FROM run_snapshots s
             WHERE NOT EXISTS (
                       SELECT 1 FROM run_attempts a
                        WHERE a.id = s.attempt_id
                          AND a.status = 'completed'
                          AND a.snapshot_id = s.id
                          AND a.plan_id = s.plan_id
                          AND a.plan_version_id = s.plan_version_id
                          AND a.engine_build_id = s.engine_build_id)
                OR NOT EXISTS (
                       SELECT 1 FROM plans p WHERE p.id = s.plan_id)
                OR NOT EXISTS (
                       SELECT 1 FROM plan_versions v
                        WHERE v.id = s.plan_version_id
                          AND v.plan_id = s.plan_id
                          AND v.normalized_config_sha256 =
                              s.resolved_input_sha256)
                OR NOT EXISTS (
                       SELECT 1 FROM engine_builds b
                        WHERE b.id = s.engine_build_id)
             LIMIT 1
            """).fetchone()
        if bad_snapshot is not None:
            raise PersistenceError("schema contains invalid run snapshot lineage")

        bad_request = conn.execute(
            """
            SELECT r.request_id
              FROM run_requests r
             WHERE r.request_id IS NULL
                OR length(r.request_id) < 20
                OR length(r.request_id) > 84
                OR substr(r.request_id, 1, 4) != 'req_'
                OR substr(r.request_id, 5) GLOB '*[^A-Za-z0-9]*'
                OR r.fingerprint_sha256 IS NULL
                OR length(r.fingerprint_sha256) != 64
                OR r.fingerprint_sha256 GLOB '*[^0-9a-f]*'
                OR r.status NOT IN ('running', 'completed', 'failed', 'cancelled')
                OR NOT EXISTS (
                       SELECT 1 FROM run_attempts a
                        WHERE a.id = r.attempt_id
                          AND a.job_id IS r.job_id
                          AND a.plan_id = r.plan_id
                          AND a.plan_version_id = r.plan_version_id
                          AND a.engine_build_id = r.engine_build_id
                          AND a.status IS r.status
                          AND a.snapshot_id IS r.snapshot_id
                          AND a.finished_at IS r.finished_at
                          AND a.error IS r.error)
                OR (r.status = 'completed' AND NOT EXISTS (
                       SELECT 1 FROM run_snapshots s
                        WHERE s.id = r.snapshot_id
                          AND s.attempt_id = r.attempt_id
                          AND s.plan_id = r.plan_id
                          AND s.plan_version_id = r.plan_version_id
                          AND s.engine_build_id = r.engine_build_id))
             LIMIT 1
            """).fetchone()
        if bad_request is not None:
            raise PersistenceError("schema contains invalid run request state")

    @staticmethod
    def _v2_table_statements(suffix: str = "") -> list[str]:
        names = {name: f"{name}{suffix}" for name in (
            "plans", "engine_builds", "plan_versions", "run_attempts",
            "run_snapshots")}
        return [
            f"""
            CREATE TABLE {names['plans']} (
                id TEXT PRIMARY KEY,
                display_name TEXT NOT NULL,
                source_key TEXT,
                status TEXT NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'archived', 'deleted')),
                created_at TEXT NOT NULL
            )
            """,
            f"""
            CREATE TABLE {names['engine_builds']} (
                id TEXT PRIMARY KEY,
                engine_version TEXT NOT NULL,
                code_manifest_sha256 TEXT,
                data_manifest_sha256 TEXT,
                protocol_version TEXT NOT NULL,
                environment_json TEXT NOT NULL,
                source_manifest_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            f"""
            CREATE TABLE {names['plan_versions']} (
                id TEXT PRIMARY KEY,
                plan_id TEXT NOT NULL REFERENCES {names['plans']}(id)
                    ON DELETE RESTRICT,
                parent_version_id TEXT REFERENCES {names['plan_versions']}(id)
                    ON DELETE RESTRICT,
                source_kind TEXT NOT NULL,
                source_config_json TEXT NOT NULL,
                source_config_sha256 TEXT NOT NULL,
                normalized_config_json TEXT NOT NULL,
                normalized_config_sha256 TEXT NOT NULL,
                config_schema_version INTEGER NOT NULL,
                canonicalizer_version TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            f"""
            CREATE TABLE {names['run_attempts']} (
                id TEXT PRIMARY KEY,
                job_id TEXT,
                plan_id TEXT NOT NULL REFERENCES {names['plans']}(id)
                    ON DELETE RESTRICT,
                plan_version_id TEXT NOT NULL REFERENCES {names['plan_versions']}(id)
                    ON DELETE RESTRICT,
                engine_build_id TEXT NOT NULL REFERENCES {names['engine_builds']}(id)
                    ON DELETE RESTRICT,
                status TEXT NOT NULL
                    CHECK (status IN ('running', 'completed', 'failed', 'cancelled')),
                precision TEXT NOT NULL,
                requested_paths INTEGER NOT NULL,
                effective_paths INTEGER NOT NULL,
                dist_paths INTEGER NOT NULL,
                requested_dist_paths INTEGER NOT NULL,
                effective_dist_paths INTEGER NOT NULL,
                seed INTEGER NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                error TEXT,
                snapshot_id TEXT,
                execution_mode TEXT,
                CHECK (status != 'completed' OR snapshot_id IS NOT NULL),
                CHECK (execution_mode IS NULL OR execution_mode = 'sequential'
                      OR execution_mode LIKE 'chunked-%')
            )
            """,
            f"""
            CREATE TABLE {names['run_snapshots']} (
                id TEXT PRIMARY KEY,
                attempt_id TEXT NOT NULL UNIQUE REFERENCES {names['run_attempts']}(id)
                    ON DELETE RESTRICT,
                plan_id TEXT NOT NULL REFERENCES {names['plans']}(id)
                    ON DELETE RESTRICT,
                plan_version_id TEXT NOT NULL REFERENCES {names['plan_versions']}(id)
                    ON DELETE RESTRICT,
                engine_build_id TEXT NOT NULL REFERENCES {names['engine_builds']}(id)
                    ON DELETE RESTRICT,
                created_at TEXT NOT NULL,
                resolved_input_json TEXT NOT NULL,
                resolved_input_sha256 TEXT NOT NULL,
                protocol_json TEXT NOT NULL,
                protocol_sha256 TEXT NOT NULL,
                replay_payload_json TEXT NOT NULL,
                replay_payload_sha256 TEXT NOT NULL,
                result_json TEXT NOT NULL,
                result_archive_sha256 TEXT NOT NULL,
                deterministic_result_sha256 TEXT NOT NULL
            )
            """,
        ]

    @staticmethod
    def _rebuild_v1_to_v2(conn: sqlite3.Connection) -> None:
        """Rebuild the v1 prototype tables so v2 foreign keys are real."""
        suffix = "_phase0_v2"
        names = {name: f"{name}{suffix}" for name in (
            "plans", "engine_builds", "plan_versions", "run_attempts",
            "run_snapshots")}
        for name in names.values():
            if conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                    (name,)).fetchone() is not None:
                raise PersistenceError(f"unexpected migration table already exists: {name}")
        for statement in PersistenceStore._v2_table_statements(suffix):
            conn.execute(statement)

        conn.execute(
            f"INSERT INTO {names['plans']} "
            "(id, display_name, source_key, status, created_at) "
            "SELECT id, display_name, source_key, status, created_at FROM plans")
        conn.execute(
            f"INSERT INTO {names['engine_builds']} "
            "(id, engine_version, code_manifest_sha256, data_manifest_sha256, "
            "protocol_version, environment_json, source_manifest_json, created_at) "
            "SELECT id, engine_version, code_manifest_sha256, data_manifest_sha256, "
            "protocol_version, environment_json, source_manifest_json, created_at "
            "FROM engine_builds")
        conn.execute(
            f"INSERT INTO {names['plan_versions']} "
            "(id, plan_id, parent_version_id, source_kind, source_config_json, "
            "source_config_sha256, normalized_config_json, normalized_config_sha256, "
            "config_schema_version, canonicalizer_version, created_at) "
            "SELECT id, plan_id, parent_version_id, source_kind, source_config_json, "
            "source_config_sha256, normalized_config_json, normalized_config_sha256, "
            "config_schema_version, canonicalizer_version, created_at FROM plan_versions")
        conn.execute(
            f"INSERT INTO {names['run_attempts']} "
            "(id, job_id, plan_id, plan_version_id, engine_build_id, status, precision, "
            "requested_paths, effective_paths, dist_paths, requested_dist_paths, "
            "effective_dist_paths, seed, started_at, finished_at, error, snapshot_id, "
            "execution_mode) "
            "SELECT id, job_id, plan_id, plan_version_id, engine_build_id, status, precision, "
            "requested_paths, effective_paths, dist_paths, dist_paths, dist_paths, seed, "
            "started_at, finished_at, error, snapshot_id, NULL FROM run_attempts")
        conn.execute(
            f"INSERT INTO {names['run_snapshots']} "
            "(id, attempt_id, plan_id, plan_version_id, engine_build_id, created_at, "
            "resolved_input_json, resolved_input_sha256, protocol_json, protocol_sha256, "
            "replay_payload_json, replay_payload_sha256, result_json, "
            "result_archive_sha256, deterministic_result_sha256) "
            "SELECT id, attempt_id, plan_id, plan_version_id, engine_build_id, created_at, "
            "resolved_input_json, resolved_input_sha256, protocol_json, protocol_sha256, "
            "replay_payload_json, replay_payload_sha256, result_json, "
            "result_archive_sha256, deterministic_result_sha256 FROM run_snapshots")

        for row in conn.execute(
                f"SELECT attempt_id, protocol_json FROM {names['run_snapshots']}"):
            try:
                mode = json.loads(row[1]).get("execution_mode")
            except (TypeError, ValueError, AttributeError) as exc:
                raise PersistenceError(
                    "v1 contains an invalid archived protocol") from exc
            if mode is not None:
                if mode != "sequential" and not str(mode).startswith("chunked-"):
                    raise PersistenceError("v1 contains an unknown execution mode")
                conn.execute(
                    f"UPDATE {names['run_attempts']} SET execution_mode = ? "
                    "WHERE id = ?", (str(mode), row[0]))

        bad_completed = conn.execute(
            f"SELECT a.id FROM {names['run_attempts']} a "
            f"LEFT JOIN {names['run_snapshots']} s "
            "ON s.id = a.snapshot_id AND s.attempt_id = a.id "
            "WHERE a.status = 'completed' AND s.id IS NULL LIMIT 1").fetchone()
        if bad_completed is not None:
            raise PersistenceError("v1 contains a completed attempt without its snapshot")
        bad_parent_lineage = conn.execute(
            f"SELECT child.id FROM {names['plan_versions']} child "
            f"JOIN {names['plan_versions']} parent "
            "ON parent.id = child.parent_version_id "
            "WHERE child.plan_id != parent.plan_id LIMIT 1").fetchone()
        if bad_parent_lineage is not None:
            raise PersistenceError("v1 contains a cross-plan parent version")
        bad_attempt_lineage = conn.execute(
            f"SELECT a.id FROM {names['run_attempts']} a "
            f"JOIN {names['plan_versions']} p ON p.id = a.plan_version_id "
            "WHERE a.plan_id != p.plan_id LIMIT 1").fetchone()
        if bad_attempt_lineage is not None:
            raise PersistenceError("v1 contains an attempt with mismatched plan lineage")
        bad_snapshot_binding = conn.execute(
            f"SELECT s.id FROM {names['run_snapshots']} s "
            f"JOIN {names['run_attempts']} a ON a.id = s.attempt_id "
            f"JOIN {names['plan_versions']} p ON p.id = s.plan_version_id "
            "WHERE s.plan_id != a.plan_id OR s.plan_id != p.plan_id "
            "OR s.plan_version_id != a.plan_version_id "
            "OR s.engine_build_id != a.engine_build_id "
            "OR s.resolved_input_sha256 != p.normalized_config_sha256 "
            "OR a.status != 'completed' OR a.snapshot_id != s.id LIMIT 1"
        ).fetchone()
        if bad_snapshot_binding is not None:
            raise PersistenceError("v1 contains a snapshot binding mismatch")

        for name in ("run_snapshots", "run_attempts", "plan_versions",
                     "engine_builds", "plans"):
            conn.execute(f"DROP TABLE {name}")
        for name in ("plans", "engine_builds", "plan_versions", "run_attempts",
                     "run_snapshots"):
            conn.execute(f"ALTER TABLE {names[name]} RENAME TO {name}")

    def initialize(self) -> None:
        with self._transaction() as conn:
            preexisting_tables = [row[0] for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name NOT LIKE 'sqlite_%'")]
            conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations ("
                         "version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL, "
                         "app_release_id TEXT NOT NULL)")
            rows = conn.execute("SELECT version FROM schema_migrations "
                                "ORDER BY version").fetchall()
            versions = [int(row[0]) for row in rows]
            current = versions[-1] if versions else 0
            user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            if current in (7, 8, 9, 10):
                # A post-cutover archive carries the formal migration's
                # additive v7/v8 schema, from Phase 2 the v9 CheckIn ledger,
                # and from Phase 4 the v10 decision record.  It is accepted
                # and served as it stands, never migrated back down: this
                # store owns the v1-v6 lineage, and the formal projection owns
                # everything above it.
                # v7 was already accepted here; v8 was not, which meant a
                # completed cutover produced an archive the app refused to
                # open (M5 in PHASE_0_EXIT_CONTRACT.md). v9 and v10 are added
                # to both this guard and the module-level one for the same
                # reason -- accepting a version in one place and not the other
                # is exactly how that defect happened.
                complete = {
                    7: self._schema_v7_complete,
                    8: self._schema_v8_complete,
                    9: self._schema_v9_complete,
                    10: self._schema_v10_complete,
                }[current](conn)
                if (versions != list(range(1, current + 1))
                        or user_version != current or not complete):
                    raise PersistenceError(
                        f"schema v{current} is incomplete or corrupt")
                if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise PersistenceError(
                        f"schema v{current} contains foreign-key violations")
                self._validate_state_rows(conn)
                return
            if current > DB_SCHEMA_VERSION:
                raise UnsupportedSchemaError(
                    f"database schema {current} is newer than supported {DB_SCHEMA_VERSION}")
            if versions != list(range(1, current + 1)):
                raise PersistenceError("schema migration lineage is incomplete")
            if current == 0 and user_version != 0:
                if user_version > DB_SCHEMA_VERSION:
                    raise UnsupportedSchemaError(
                        f"database user_version {user_version} is newer than supported "
                        f"{DB_SCHEMA_VERSION}")
                raise PersistenceError(
                    f"schema migration/user_version mismatch: {current}/{user_version}")
            if current and user_version != current:
                raise PersistenceError(
                    f"schema migration/user_version mismatch: {current}/{user_version}")
            if current == 0:
                if preexisting_tables:
                    raise PersistenceError(
                        "schema migration lineage is missing for an existing database")
                for statement in self._schema_statements():
                    conn.execute(statement)
                if not self._schema_complete(
                        conn, include_v2=True, include_v3=True, include_v4=True,
                        include_v5=True, include_v6=True):
                    raise PersistenceError("fresh schema failed completeness checks")
                now = utc_now()
                conn.execute("INSERT INTO schema_migrations(version, applied_at, app_release_id) "
                             "VALUES (1, ?, ?)", (now, self.app_release_id))
                conn.execute("INSERT INTO schema_migrations(version, applied_at, app_release_id) "
                             "VALUES (2, ?, ?)", (now, self.app_release_id))
                conn.execute("INSERT INTO schema_migrations(version, applied_at, app_release_id) "
                             "VALUES (3, ?, ?)", (now, self.app_release_id))
                conn.execute("INSERT INTO schema_migrations(version, applied_at, app_release_id) "
                             "VALUES (4, ?, ?)", (now, self.app_release_id))
                conn.execute("INSERT INTO schema_migrations(version, applied_at, app_release_id) "
                             "VALUES (5, ?, ?)", (now, self.app_release_id))
                conn.execute("INSERT INTO schema_migrations(version, applied_at, app_release_id) "
                             "VALUES (6, ?, ?)", (now, self.app_release_id))
                conn.execute("PRAGMA user_version = 6")
                return
            if current == 1:
                if not self._schema_complete(conn, include_v2=False):
                    raise PersistenceError("schema v1 is incomplete or corrupt")
                self._rebuild_v1_to_v2(conn)
                for statement in self._trigger_statements():
                    conn.execute(statement)
                conn.execute("CREATE INDEX IF NOT EXISTS plan_versions_plan_idx "
                             "ON plan_versions(plan_id, created_at, id)")
                conn.execute("CREATE INDEX IF NOT EXISTS run_snapshots_plan_idx "
                             "ON run_snapshots(plan_id, created_at, id)")
                conn.execute("CREATE INDEX IF NOT EXISTS run_attempts_plan_idx "
                             "ON run_attempts(plan_id, started_at, id)")
                conn.execute("INSERT INTO schema_migrations(version, applied_at, app_release_id) "
                             "VALUES (2, ?, ?)", (utc_now(), self.app_release_id))
                conn.execute("PRAGMA user_version = 2")
                current = 2
            if current == 2 and not self._schema_complete(conn, include_v2=True):
                raise PersistenceError("schema v2 is incomplete or corrupt")
            if current == 2 and conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise PersistenceError("schema contains foreign-key violations")
            if current == 2:
                for statement in self._v3_table_statements():
                    conn.execute(statement)
                for statement in self._request_trigger_statements():
                    conn.execute(statement)
                conn.execute("INSERT INTO schema_migrations(version, applied_at, app_release_id) "
                             "VALUES (3, ?, ?)", (utc_now(), self.app_release_id))
                conn.execute("PRAGMA user_version = 3")
                current = 3
            if current == 3 and not self._schema_complete(
                    conn, include_v2=True, include_v3=True):
                raise PersistenceError("schema v3 is incomplete or corrupt")
            if current == 3 and conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise PersistenceError("schema contains foreign-key violations")
            if current == 3:
                self._validate_state_rows(conn)
                for name in (
                        "run_attempts_request_insert_contract",
                        "run_attempts_request_update_contract",
                        "run_attempts_request_mirror",
                        "run_requests_insert_guard",
                        "run_requests_delete_guard",
                        "run_requests_binding_immutable",
                        "run_requests_state_lineage_guard",
                        "run_requests_terminal_immutable",
                        "run_requests_completed_guard",
                        "run_requests_attempt_status_guard"):
                    conn.execute(f"DROP TRIGGER IF EXISTS {name}")
                for statement in self._request_trigger_statements():
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at, app_release_id) "
                    "VALUES (4, ?, ?)", (utc_now(), self.app_release_id))
                conn.execute("PRAGMA user_version = 4")
                current = 4
            if current == 4 and not self._schema_complete(
                    conn, include_v2=True, include_v3=True, include_v4=True):
                raise PersistenceError("schema v4 is incomplete or corrupt")
            if current == 4 and conn.execute(
                    "PRAGMA foreign_key_check").fetchone() is not None:
                raise PersistenceError("schema contains foreign-key violations")
            if current == 4:
                # A v4 database may have been exposed to REPLACE while
                # recursive_triggers was disabled.  Reject any observable
                # request/attempt drift before installing the v5 guards.
                self._validate_state_rows(conn)
                for statement in self._v5_trigger_statements():
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at, app_release_id) "
                    "VALUES (5, ?, ?)", (utc_now(), self.app_release_id))
                conn.execute("PRAGMA user_version = 5")
                current = 5
            if current == 5 and not self._schema_complete(
                    conn, include_v2=True, include_v3=True, include_v4=True,
                    include_v5=True):
                raise PersistenceError("schema v5 is incomplete or corrupt")
            if current == 5 and conn.execute(
                    "PRAGMA foreign_key_check").fetchone() is not None:
                raise PersistenceError("schema contains foreign-key violations")
            if current == 5:
                # Validate databases that may already contain v4-era drift or
                # v5 attempt-id reuse before installing the v6 identity guard.
                self._validate_state_rows(conn)
                for name in (
                        "plans_duplicate_insert_guard",
                        "plans_identity_immutable",
                        "plans_immutable_delete",
                        "run_attempts_identity_immutable"):
                    conn.execute(f"DROP TRIGGER IF EXISTS {name}")
                for statement in self._v6_trigger_statements():
                    conn.execute(statement)
                conn.execute(
                    "INSERT INTO schema_migrations(version, applied_at, app_release_id) "
                    "VALUES (6, ?, ?)", (utc_now(), self.app_release_id))
                conn.execute("PRAGMA user_version = 6")
                current = 6
            if current == 6 and not self._schema_complete(
                    conn, include_v2=True, include_v3=True, include_v4=True,
                    include_v5=True, include_v6=True):
                raise PersistenceError("schema v6 is incomplete or corrupt")
            if current == 6 and conn.execute(
                    "PRAGMA foreign_key_check").fetchone() is not None:
                raise PersistenceError("schema contains foreign-key violations")
            if current == 6:
                self._validate_state_rows(conn)

    def _insert_engine_build(self, conn: sqlite3.Connection, *,
                             engine_version: str, protocol_version: str,
                             source_root: Optional[str], metadata: Optional[dict]) -> dict:
        environment, manifest = build_environment(source_root, metadata)
        build_id = make_engine_build_id(engine_version, protocol_version,
                                        environment, manifest)
        data_hash = environment.get("data_manifest_sha256")
        environment_json = canonical_json_text(environment)
        manifest_json = canonical_json_text(manifest)
        conn.execute(
            "INSERT INTO engine_builds "
            "(id, engine_version, code_manifest_sha256, data_manifest_sha256, "
            "protocol_version, environment_json, source_manifest_json, created_at) "
            "SELECT ?, ?, ?, ?, ?, ?, ?, ? "
            "WHERE NOT EXISTS (SELECT 1 FROM engine_builds WHERE id = ?)",
            (build_id, engine_version, manifest.get("sha256"), data_hash,
             protocol_version, environment_json, manifest_json, utc_now(),
             build_id))
        row = conn.execute("SELECT * FROM engine_builds WHERE id = ?",
                           (build_id,)).fetchone()
        if row is None:
            raise PersistenceError("failed to record engine build")
        expected = {
            "engine_version": engine_version,
            "code_manifest_sha256": manifest.get("sha256"),
            "data_manifest_sha256": data_hash,
            "protocol_version": protocol_version,
            "environment_json": environment_json,
            "source_manifest_json": manifest_json,
        }
        if any(row[key] != value for key, value in expected.items()):
            raise PersistenceError("existing engine build identity is inconsistent")
        return dict(row)

    def create_plan(self, display_name: str, *, source_key: Optional[str] = None,
                    plan_id: Optional[str] = None) -> str:
        pid = plan_id or _uuid("plan")
        name = str(display_name or "Untitled plan").strip() or "Untitled plan"
        with self._transaction() as conn:
            conn.execute("INSERT INTO plans(id, display_name, source_key, created_at) "
                         "VALUES (?, ?, ?, ?)",
                         (pid, name, source_key, utc_now()))
        return pid

    @staticmethod
    def _insert_plan_version(conn: sqlite3.Connection, *, plan_id: str,
                             source_config: dict, normalized_config: dict,
                             source_kind: str, parent_version_id: Optional[str] = None,
                             version_id: Optional[str] = None) -> dict:
        source_config = _sanitize_runtime_metadata(source_config)
        normalized_config = _sanitize_runtime_metadata(normalized_config)
        if parent_version_id is not None:
            parent = conn.execute(
                "SELECT plan_id FROM plan_versions WHERE id = ?",
                (parent_version_id,)).fetchone()
            if parent is None:
                raise PersistenceError(f"unknown parent plan version {parent_version_id}")
            if parent["plan_id"] != plan_id:
                raise PersistenceError("parent plan version belongs to another plan")
        sid = version_id or _uuid("ver")
        source_json = canonical_json_text(source_config)
        normalized_json = canonical_json_text(normalized_config)
        row = {
            "id": sid,
            "plan_id": plan_id,
            "parent_version_id": parent_version_id,
            "source_kind": source_kind,
            "source_config_json": source_json,
            "source_config_sha256": sha256_bytes(source_json.encode("utf-8")),
            "normalized_config_json": normalized_json,
            "normalized_config_sha256": sha256_bytes(normalized_json.encode("utf-8")),
            "config_schema_version": int(normalized_config.get(
                "config_version", CONFIG_SCHEMA_VERSION)),
            "canonicalizer_version": CANONICALIZER_VERSION,
            "created_at": utc_now(),
        }
        conn.execute(
            "INSERT INTO plan_versions "
            "(id, plan_id, parent_version_id, source_kind, source_config_json, "
            "source_config_sha256, normalized_config_json, normalized_config_sha256, "
            "config_schema_version, canonicalizer_version, created_at) "
            "VALUES (:id, :plan_id, :parent_version_id, :source_kind, "
            ":source_config_json, :source_config_sha256, :normalized_config_json, "
            ":normalized_config_sha256, :config_schema_version, "
            ":canonicalizer_version, :created_at)", row)
        return row

    def create_plan_version(self, plan_id: str, source_config: dict,
                            normalized_config: dict, *,
                            source_kind: str = "user",
                            parent_version_id: Optional[str] = None,
                            version_id: Optional[str] = None) -> dict:
        """Create one immutable child version.

        `version_id` lets a caller holding a stable external request identity name
        the row it is about to create.  `_insert_plan_version` already accepted
        one; not passing it through here meant the §6 storage seam had no way to
        make a plan-version's id deterministic, and therefore no way to make a
        duplicate request collide with the row it already produced instead of
        creating a second one beside it.
        """
        source_config = _sanitize_runtime_metadata(source_config)
        normalized_config = _sanitize_runtime_metadata(normalized_config)
        try:
            normalized_version = int(normalized_config.get(
                "config_version", CONFIG_SCHEMA_VERSION))
        except (AttributeError, TypeError, ValueError) as exc:
            raise PersistenceError("normalized config_version must be an integer") from exc
        if normalized_version > CONFIG_SCHEMA_VERSION:
            raise PersistenceError(
                f"config schema {normalized_version} is newer than supported "
                f"{CONFIG_SCHEMA_VERSION}")
        if normalized_version < CONFIG_SCHEMA_VERSION:
            normalized_config = copy.deepcopy(normalized_config)
            normalized_config["config_version"] = CONFIG_SCHEMA_VERSION
        with self._transaction() as conn:
            if conn.execute("SELECT 1 FROM plans WHERE id = ?", (plan_id,)).fetchone() is None:
                raise PersistenceError(f"unknown plan {plan_id}")
            if parent_version_id is None:
                row = conn.execute(
                    "SELECT id FROM plan_versions WHERE plan_id = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT 1", (plan_id,)
                ).fetchone()
                parent_version_id = row[0] if row else None
            return self._insert_plan_version(
                conn, plan_id=plan_id, source_config=source_config,
                normalized_config=normalized_config, source_kind=source_kind,
                parent_version_id=parent_version_id, version_id=version_id)

    @staticmethod
    def _protocol_from_rows(attempt: sqlite3.Row, build: sqlite3.Row,
                            execution_mode: Optional[str]) -> dict:
        mode = execution_mode or attempt["execution_mode"]
        if mode is not None:
            mode = str(mode)
            if mode != "sequential" and not mode.startswith("chunked-"):
                raise PersistenceError(f"unknown execution mode: {mode!r}")
        environment = json.loads(build["environment_json"])
        return {
            "protocol_version": build["protocol_version"],
            "canonicalizer_version": CANONICALIZER_VERSION,
            "result_schema_version": RESULT_SCHEMA_VERSION,
            "precision": attempt["precision"],
            "requested_paths": int(attempt["requested_paths"]),
            "paths": int(attempt["effective_paths"]),
            "requested_dist_paths": int(attempt["requested_dist_paths"]),
            "dist_paths": int(attempt["effective_dist_paths"]),
            "seed": int(attempt["seed"]),
            "bit_generator": "PCG64",
            "stream_map_version": STREAM_MAP_VERSION,
            "chunk_size": CHUNK_SIZE,
            "chunk_seed_rule": "base_seed_plus_chunk_index",
            "execution_mode": mode,
            "engine": build["engine_version"],
            "engine_build_id": build["id"],
            "data_manifest_sha256": environment.get("data_manifest_sha256"),
        }

    def adopt_run_context(self, context: dict) -> dict:
        """Re-register a run context's receipt token on this store instance.

        The token proves a result payload was produced by *this process* for
        *this attempt*: `prepare_run` mints an anonymous object, keeps it in
        `_receipt_tokens`, and `make_engine_receipt`/`save_run_snapshot` require
        the caller's context to hold the identical object. That check is what
        stops a client-supplied payload being archived as an engine result.

        The token lives on the store instance, and once formal-run writes go
        through the archive-write seam the attempt is prepared on one staged
        copy and its snapshot committed on another — different `PersistenceStore`
        objects for the same archive, within the same process. Re-registering the
        token preserves exactly what it asserts (same process, same attempt) and
        nothing is loosened: the object cannot be forged from outside the process
        because it is an identity comparison, not a value one.

        Refused unless the attempt is on this store and still running, so an
        adopted context cannot resurrect a finished attempt.
        """
        attempt_id = context.get("attempt_id")
        token = context.get("_receipt_token")
        if not attempt_id or token is None:
            raise PersistenceError("run context carries no receipt token")
        conn = self._connect()
        try:
            row = conn.execute("SELECT status FROM run_attempts WHERE id = ?",
                               (attempt_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise PersistenceError(f"unknown run attempt {attempt_id}")
        if row[0] != "running":
            raise PersistenceError(
                f"run attempt {attempt_id} is no longer running")
        self._receipt_tokens[attempt_id] = token
        return context

    def protocol_for_attempt(self, context: dict,
                             execution_mode: Optional[str] = None) -> dict:
        """Re-derive protocol from DB state instead of trusting context fields."""
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT a.*, b.engine_version, b.protocol_version, b.environment_json, "
                "b.id AS build_id FROM run_attempts a JOIN engine_builds b "
                "ON b.id = a.engine_build_id WHERE a.id = ?",
                (context["attempt_id"],)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise PersistenceError(f"unknown run attempt {context['attempt_id']}")
        build = {"id": row["build_id"], "engine_version": row["engine_version"],
                 "protocol_version": row["protocol_version"],
                 "environment_json": row["environment_json"]}
        return self._protocol_from_rows(row, build, execution_mode)

    def _existing_request_context(self, conn: sqlite3.Connection,
                                  request: sqlite3.Row) -> dict:
        """Return a durable replay handle without creating another attempt."""
        attempt = conn.execute(
            "SELECT a.*, b.id AS _build_id, b.engine_version AS _engine_version, "
            "b.protocol_version AS _protocol_version, "
            "b.environment_json AS _environment_json "
            "FROM run_attempts a JOIN engine_builds b "
            "ON b.id = a.engine_build_id WHERE a.id = ?",
            (request["attempt_id"],)).fetchone()
        if attempt is None:
            raise PersistenceError("idempotency request references a missing attempt")
        binding_fields = ("plan_id", "plan_version_id", "engine_build_id")
        if any(request[field] != attempt[field] for field in binding_fields):
            raise PersistenceError("idempotency request binding mismatch")
        if request["status"] != attempt["status"]:
            raise PersistenceError("idempotency request status mismatch")
        build = {
            "id": attempt["_build_id"],
            "engine_version": attempt["_engine_version"],
            "protocol_version": attempt["_protocol_version"],
            "environment_json": attempt["_environment_json"],
        }
        protocol = self._protocol_from_rows(attempt, build,
                                            attempt["execution_mode"])
        snapshot_id = request["snapshot_id"] or attempt["snapshot_id"]
        if request["status"] == "completed" and not snapshot_id:
            raise PersistenceError("completed idempotency request has no snapshot")
        return {
            "_idempotent_replay": True,
            "request_id": request["request_id"],
            "request_status": request["status"],
            "attempt_id": request["attempt_id"],
            "job_id": attempt["job_id"],
            "plan_id": request["plan_id"],
            "plan_version_id": request["plan_version_id"],
            "engine_build_id": request["engine_build_id"],
            "snapshot_id": snapshot_id,
            "error": request["error"] or attempt["error"],
            "archive": {
                "plan_id": request["plan_id"],
                "plan_version_id": request["plan_version_id"],
                "protocol": protocol,
                "timeline_protocol": TIMELINE_PROTOCOL_VERSION,
            },
        }

    def prepare_run(self, source_config: dict, *,
                    default_factory: Callable[[], dict],
                    engine_version: str, paths: int, dist_paths: int,
                    seed: int, precision: str = "standard",
                    requested_paths: Optional[int] = None,
                    requested_dist_paths: Optional[int] = None,
                    job_id: Optional[str] = None,
                    plan_id: Optional[str] = None,
                    plan_version_id: Optional[str] = None,
                    request_id: Optional[str] = None,
                    source_root: Optional[str] = None,
                    metadata: Optional[dict] = None) -> dict:
        """Create a PlanVersion and running attempt before engine execution."""
        request_id = validate_request_id(request_id)
        effective_paths = int(paths)
        effective_dist = int(dist_paths)
        precision = validate_precision(precision, effective_paths)
        requested_paths = effective_paths if requested_paths is None else int(requested_paths)
        requested_dist_paths = (effective_dist if requested_dist_paths is None
                                else int(requested_dist_paths))
        if effective_paths < 1 or effective_dist < 1:
            raise PersistenceError("effective paths must be positive")
        if requested_paths < 1 or requested_dist_paths < 1:
            raise PersistenceError("requested paths must be positive")
        resolved = normalize_config(source_config, default_factory)
        resolved_hash = sha256_json(resolved)
        source_copy = _sanitize_runtime_metadata(source_config or {})
        with self._transaction() as conn:
            build = self._insert_engine_build(
                conn, engine_version=engine_version,
                protocol_version=PROTOCOL_VERSION, source_root=source_root,
                metadata={"app_release_id": self.app_release_id, **(metadata or {})})
            if request_id:
                fingerprint = _archive_request_fingerprint(
                    source_config=source_copy,
                    normalized_config_sha256=resolved_hash,
                    provided_plan_id=plan_id,
                    provided_plan_version_id=plan_version_id,
                    precision=precision,
                    requested_paths=requested_paths,
                    effective_paths=effective_paths,
                    requested_dist_paths=requested_dist_paths,
                    effective_dist_paths=effective_dist,
                    seed=seed,
                    build=build)
                existing = conn.execute(
                    "SELECT * FROM run_requests WHERE request_id = ?",
                    (request_id,)).fetchone()
                if existing is not None:
                    if existing["fingerprint_sha256"] != fingerprint:
                        raise IdempotencyConflictError(
                            "request_id conflicts with a different archive request")
                    return self._existing_request_context(conn, existing)
            if plan_version_id:
                if plan_id is None:
                    raise PersistenceError(
                        "plan_id is required when plan_version_id is provided")
                pvr = conn.execute("SELECT * FROM plan_versions WHERE id = ?",
                                   (plan_version_id,)).fetchone()
                if pvr is None:
                    raise PersistenceError(f"unknown plan version {plan_version_id}")
                if plan_id is not None and pvr["plan_id"] != plan_id:
                    raise PersistenceError("plan and plan version belong to different plans")
                if pvr["normalized_config_sha256"] != sha256_json(resolved):
                    raise PersistenceError(
                        "resolved config does not match the requested plan version")
                plan_id = pvr["plan_id"]
                version = dict(pvr)
            else:
                if plan_id is None:
                    plan_id = _uuid("plan")
                    name = str((resolved.get("name") or "Untitled plan")).strip()
                    conn.execute("INSERT INTO plans(id, display_name, created_at) "
                                 "VALUES (?, ?, ?)",
                                 (plan_id, name or "Untitled plan", utc_now()))
                elif conn.execute("SELECT 1 FROM plans WHERE id = ?", (plan_id,)).fetchone() is None:
                    raise PersistenceError(f"unknown plan {plan_id}")
                latest = conn.execute(
                    "SELECT * FROM plan_versions WHERE plan_id = ? "
                    "ORDER BY created_at DESC, id DESC LIMIT 1", (plan_id,)
                ).fetchone()
                if latest is not None and latest["normalized_config_sha256"] == resolved_hash:
                    # A PlanVersion represents an input state, not an execution
                    # attempt.  Repeated runs of the same normalized config
                    # append attempts/snapshots without manufacturing copies.
                    version = dict(latest)
                else:
                    version = self._insert_plan_version(
                        conn, plan_id=plan_id, source_config=source_copy,
                        normalized_config=resolved, source_kind="run",
                        parent_version_id=(latest["id"] if latest else None))

            attempt_id = _uuid("attempt")
            conn.execute(
                "INSERT INTO run_attempts "
                "(id, job_id, plan_id, plan_version_id, engine_build_id, status, "
                "precision, requested_paths, effective_paths, dist_paths, "
                "requested_dist_paths, effective_dist_paths, seed, started_at) "
                "VALUES (?, ?, ?, ?, ?, 'running', ?, ?, ?, ?, ?, ?, ?, ?)",
                (attempt_id, job_id, plan_id, version["id"], build["id"],
                 precision, requested_paths, effective_paths, effective_dist,
                 requested_dist_paths, effective_dist, int(seed), utc_now()))

            if request_id:
                conn.execute(
                    "INSERT INTO run_requests "
                    "(request_id, fingerprint_sha256, status, job_id, attempt_id, "
                    "plan_id, plan_version_id, engine_build_id, created_at) "
                    "VALUES (?, ?, 'running', ?, ?, ?, ?, ?, ?)",
                    (request_id, fingerprint, job_id, attempt_id, plan_id,
                     version["id"], build["id"], utc_now()))

            attempt = conn.execute("SELECT * FROM run_attempts WHERE id = ?",
                                   (attempt_id,)).fetchone()

        receipt_token = object()
        self._receipt_tokens[attempt_id] = receipt_token
        protocol = self._protocol_from_rows(attempt, build, None)
        return {
            "attempt_id": attempt_id,
            "plan_id": plan_id,
            "plan_version_id": version["id"],
            "engine_build_id": build["id"],
            "request_id": request_id,
            "resolved_config": resolved,
            "protocol": protocol,
            "_receipt_token": receipt_token,
        }

    def recover_running_attempts(self, *, exclude_ids: Optional[set[str]] = None,
                                 reason: str = "process_interrupted") -> int:
        """Fail closed any attempts left running by an earlier process.

        This is intentionally a writable-startup operation.  The read-only
        timeline path never calls it, so inspecting history cannot mutate the
        database.  The current process can protect its live attempt ids while
        a lazy archive store is being reused.
        """
        excluded = {str(value) for value in (exclude_ids or set()) if value}
        with self._transaction() as conn:
            if excluded:
                marks = ", ".join("?" for _ in excluded)
                rows = conn.execute(
                    f"SELECT id FROM run_attempts WHERE status = 'running' "
                    f"AND id NOT IN ({marks})", tuple(sorted(excluded))).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id FROM run_attempts WHERE status = 'running'").fetchall()
            if not rows:
                return 0
            ids = [row[0] for row in rows]
            now = utc_now()
            conn.executemany(
                "UPDATE run_attempts SET status = 'failed', finished_at = ?, "
                "error = ? WHERE id = ? AND status = 'running'",
                [(now, str(reason)[:500], attempt_id) for attempt_id in ids])
            return len(ids)

    def validate_archive_lineage(self, *, plan_id: Optional[str],
                                 plan_version_id: Optional[str],
                                 source_config: dict,
                                 default_factory: Callable[[], dict]) -> None:
        """Preflight client-supplied server refs before creating a job."""
        if plan_version_id is not None and plan_id is None:
            raise PersistenceError(
                "plan_id is required when plan_version_id is provided")
        conn = self._connect()
        try:
            if plan_id is not None and conn.execute(
                    "SELECT 1 FROM plans WHERE id = ?", (plan_id,)).fetchone() is None:
                raise PersistenceError(f"unknown plan {plan_id}")
            if plan_version_id is None:
                return
            version = conn.execute(
                "SELECT plan_id, normalized_config_sha256 FROM plan_versions "
                "WHERE id = ?", (plan_version_id,)).fetchone()
            if version is None:
                raise PersistenceError(f"unknown plan version {plan_version_id}")
            if version["plan_id"] != plan_id:
                raise PersistenceError("plan and plan version belong to different plans")
            resolved = normalize_config(source_config, default_factory)
            if version["normalized_config_sha256"] != sha256_json(resolved):
                raise PersistenceError(
                    "resolved config does not match the requested plan version")
        finally:
            conn.close()

    def make_engine_receipt(self, context: dict, result: dict,
                            execution_mode: str) -> EngineResultReceipt:
        attempt_id = context.get("attempt_id")
        token = context.get("_receipt_token")
        if (not attempt_id or token is None
                or self._receipt_tokens.get(attempt_id) is not token):
            raise PersistenceError("run context is not an active server receipt")
        return EngineResultReceipt(attempt_id, execution_mode, result, token,
                                   _RECEIPT_SENTINEL)

    def _after_snapshot_insert(self, conn: sqlite3.Connection, *,
                               snapshot_id: str, attempt_id: str) -> None:
        """Hook for adversarial transaction tests; production implementation is a no-op."""
        del conn, snapshot_id, attempt_id

    def save_run_snapshot(self, context: dict, *,
                          receipt: EngineResultReceipt) -> str:
        """Commit a DB-bound engine result and close its attempt atomically."""
        attempt_id = context["attempt_id"]
        if not isinstance(receipt, EngineResultReceipt):
            raise PersistenceError("snapshot requires a server engine receipt")
        if (receipt.attempt_id != attempt_id
                or self._receipt_tokens.get(attempt_id) is not receipt._token):
            raise PersistenceError("engine receipt does not match run attempt")
        result = receipt.result
        execution_mode = receipt.execution_mode
        snapshot_id = _uuid("snap")
        with self._transaction() as conn:
            attempt = conn.execute("SELECT * FROM run_attempts WHERE id = ?",
                                   (attempt_id,)).fetchone()
            if attempt is None:
                raise PersistenceError(f"unknown run attempt {attempt_id}")
            if attempt["status"] != "running":
                raise PersistenceError("run attempt is not running")
            for key in ("plan_id", "plan_version_id", "engine_build_id"):
                if context.get(key) is not None and context[key] != attempt[key]:
                    raise PersistenceError(f"run context {key} does not match database")
            version = conn.execute("SELECT * FROM plan_versions WHERE id = ?",
                                   (attempt["plan_version_id"],)).fetchone()
            build = conn.execute("SELECT * FROM engine_builds WHERE id = ?",
                                 (attempt["engine_build_id"],)).fetchone()
            if version is None or build is None:
                raise PersistenceError("run references missing immutable metadata")
            if version["plan_id"] != attempt["plan_id"]:
                raise PersistenceError("run attempt plan lineage mismatch")
            resolved_json = version["normalized_config_json"]
            resolved_hash = sha256_bytes(resolved_json.encode("utf-8"))
            if resolved_hash != version["normalized_config_sha256"]:
                raise PersistenceError("plan version normalized config hash mismatch")
            protocol = self._protocol_from_rows(attempt, build, execution_mode)
            protocol_json = canonical_json_text(protocol)
            result_protocol = dict(((result.get("meta") or {}).get("protocol") or {}))
            result_protocol.pop("elapsed_s", None)
            result_protocol.pop("snapshot_id", None)
            if canonical_json_text(result_protocol) != protocol_json:
                raise PersistenceError("result protocol does not match run attempt")
            result_json = canonical_json_text(result)
            replay_payload = {
                "resolved_input_sha256": resolved_hash,
                "engine_build_id": attempt["engine_build_id"],
                "plan_version_id": attempt["plan_version_id"],
                "protocol": protocol,
            }
            replay_json = canonical_json_text(replay_payload)
            conn.execute(
                "INSERT INTO run_snapshots "
                "(id, attempt_id, plan_id, plan_version_id, engine_build_id, created_at, "
                "resolved_input_json, resolved_input_sha256, protocol_json, protocol_sha256, "
                "replay_payload_json, replay_payload_sha256, result_json, "
                "result_archive_sha256, deterministic_result_sha256) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (snapshot_id, attempt_id, attempt["plan_id"], attempt["plan_version_id"],
                 attempt["engine_build_id"], utc_now(), resolved_json, resolved_hash,
                 protocol_json, sha256_bytes(protocol_json.encode("utf-8")), replay_json,
                 sha256_bytes(replay_json.encode("utf-8")), result_json,
                 sha256_bytes(result_json.encode("utf-8")),
                 deterministic_result_sha256(result)))
            self._after_snapshot_insert(
                conn, snapshot_id=snapshot_id, attempt_id=attempt_id)
            finished_at = utc_now()
            conn.execute(
                "UPDATE run_attempts SET status = 'completed', finished_at = ?, "
                "snapshot_id = ?, execution_mode = ? WHERE id = ?",
                (finished_at, snapshot_id, protocol["execution_mode"], attempt_id))
            if context.get("request_id"):
                request_row = conn.execute(
                    "SELECT status, snapshot_id, finished_at FROM run_requests "
                    "WHERE request_id = ?", (context["request_id"],)).fetchone()
                if (request_row is None or request_row["status"] != "completed"
                        or request_row["snapshot_id"] != snapshot_id
                        or request_row["finished_at"] != finished_at):
                    raise PersistenceError("idempotency request mirror was not completed")
        self._receipt_tokens.pop(attempt_id, None)
        return snapshot_id

    def current_engine_build_id(self, engine_version: str, *,
                                source_root: str,
                                metadata: Optional[dict] = None) -> str:
        """The build identity this runtime would record for a run started now.

        Deliberately a thin wrapper over the same two calls
        `_insert_engine_build` makes, so build identity keeps exactly one
        definition. Unlike `runtime_build_id_for_attempt` this verifies
        nothing against an archived row: the caller wants to know whether the
        build has *moved*, and refusing when it has would answer the question
        by raising it.
        """
        if not source_root:
            raise PersistenceError("build identity requires source_root")
        environment, manifest = build_environment(
            source_root, {"app_release_id": self.app_release_id,
                          **(metadata or {})})
        return make_engine_build_id(engine_version, PROTOCOL_VERSION,
                                    environment, manifest)

    def runtime_build_id_for_attempt(self, context: dict, *,
                                     source_root: str,
                                     metadata: Optional[dict] = None) -> str:
        """Compute and verify the build identity of the current replay runtime."""
        if not source_root:
            raise PersistenceError("replay build preflight requires source_root")
        conn = self._connect()
        try:
            row = conn.execute(
                "SELECT a.engine_build_id, b.* FROM run_attempts a "
                "JOIN engine_builds b ON b.id = a.engine_build_id "
                "WHERE a.id = ?", (context["attempt_id"],)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise PersistenceError("unknown attempt for replay build preflight")
        runtime_metadata = {"app_release_id": self.app_release_id,
                            **(metadata or {})}
        environment, manifest = build_environment(source_root, runtime_metadata)
        stored_environment = json.loads(row["environment_json"])
        if (environment != stored_environment
                or manifest.get("sha256") != row["code_manifest_sha256"]):
            raise PersistenceError(
                "replay runtime environment or source manifest does not match build")
        build_id = make_engine_build_id(
            row["engine_version"], row["protocol_version"], environment, manifest)
        if build_id != row["engine_build_id"]:
            raise PersistenceError("replay runtime build id does not match snapshot build")
        return build_id

    def finish_attempt(self, attempt_id: str, status: str, *,
                       error: Optional[str] = None) -> None:
        if status not in {"failed", "cancelled"}:
            raise ValueError("finish_attempt only accepts failed or cancelled")
        with self._transaction() as conn:
            row = conn.execute("SELECT status FROM run_attempts WHERE id = ?",
                               (attempt_id,)).fetchone()
            if row is None:
                raise PersistenceError(f"unknown run attempt {attempt_id}")
            if row[0] != "running":
                return
            finished_at = utc_now()
            bounded_error = str(error or "")[:500]
            conn.execute(
                "UPDATE run_attempts SET status = ?, finished_at = ?, error = ? "
                "WHERE id = ?", (status, finished_at, bounded_error, attempt_id))
        self._receipt_tokens.pop(attempt_id, None)

    @staticmethod
    def _decode_row(row: sqlite3.Row) -> dict:
        out = dict(row)
        for key in ("resolved_input_json", "protocol_json", "replay_payload_json", "result_json"):
            if key in out and out[key] is not None:
                out[key[:-5] if key.endswith("_json") else key] = json.loads(out[key])
        return out

    def get_snapshot(self, snapshot_id: str) -> dict:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM run_snapshots WHERE id = ?",
                               (snapshot_id,)).fetchone()
            attempt = None
            version = None
            build = None
            if row is not None:
                attempt = conn.execute("SELECT * FROM run_attempts WHERE id = ?",
                                       (row["attempt_id"],)).fetchone()
                version = conn.execute("SELECT * FROM plan_versions WHERE id = ?",
                                       (row["plan_version_id"],)).fetchone()
                build = conn.execute("SELECT * FROM engine_builds WHERE id = ?",
                                     (row["engine_build_id"],)).fetchone()
        finally:
            conn.close()
        if row is None:
            raise PersistenceError(f"unknown snapshot {snapshot_id}")
        if attempt is None or version is None or build is None:
            raise PersistenceError("snapshot references missing immutable metadata")
        if (attempt["status"] != "completed" or attempt["snapshot_id"] != snapshot_id
                or attempt["plan_id"] != row["plan_id"]
                or attempt["plan_version_id"] != row["plan_version_id"]
                or attempt["engine_build_id"] != row["engine_build_id"]
                or version["plan_id"] != row["plan_id"]):
            raise PersistenceError("snapshot/attempt binding mismatch")
        if row["resolved_input_sha256"] != version["normalized_config_sha256"]:
            raise PersistenceError("snapshot/plan version input binding mismatch")
        out = self._decode_row(row)
        if out["resolved_input_sha256"] != sha256_bytes(
                out["resolved_input_json"].encode("utf-8")):
            raise PersistenceError("snapshot resolved input archive hash mismatch")
        if out["protocol_sha256"] != sha256_bytes(out["protocol_json"].encode("utf-8")):
            raise PersistenceError("snapshot protocol hash mismatch")
        if out["replay_payload_sha256"] != sha256_bytes(
                out["replay_payload_json"].encode("utf-8")):
            raise PersistenceError("snapshot replay payload hash mismatch")
        expected_protocol = self._protocol_from_rows(
            attempt, build, attempt["execution_mode"])
        if out["protocol"] != expected_protocol:
            raise PersistenceError("snapshot protocol/attempt binding mismatch")
        replay = out["replay_payload"]
        if (replay.get("resolved_input_sha256") != out["resolved_input_sha256"]
                or replay.get("engine_build_id") != out["engine_build_id"]
                or replay.get("plan_version_id") != out["plan_version_id"]
                or replay.get("protocol") != out["protocol"]):
            raise PersistenceError("snapshot replay payload binding mismatch")
        result_protocol = dict(((out["result"].get("meta") or {}).get("protocol") or {}))
        result_protocol.pop("elapsed_s", None)
        result_protocol.pop("snapshot_id", None)
        if canonical_json_text(result_protocol) != out["protocol_json"]:
            raise PersistenceError("snapshot result protocol mismatch")
        if out["result_archive_sha256"] != sha256_bytes(out["result_json"].encode("utf-8")):
            raise PersistenceError("snapshot result archive hash mismatch")
        if out["deterministic_result_sha256"] != deterministic_result_sha256(out["result"]):
            raise PersistenceError("snapshot deterministic result hash mismatch")
        return out

    @staticmethod
    def _timeline_rows(conn: sqlite3.Connection, plan_id: str) -> list[dict]:
        plan = conn.execute("SELECT 1 FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if plan is None:
            raise PlanNotFoundError(f"unknown plan {plan_id}")
        rows = conn.execute(
            "SELECT 'plan_version' AS kind, pv.id, pv.plan_id, pv.id AS plan_version_id, "
            "pv.created_at AS recorded_at, "
            "CASE WHEN pv.source_kind IN ('run', 'user', 'import', 'test') "
            "THEN pv.source_kind ELSE 'other' END AS source, "
            "pv.normalized_config_sha256 AS content_sha256, NULL AS status, "
            "NULL AS error_code, NULL AS attempt_id, NULL AS snapshot_id, "
            "NULL AS precision, NULL AS seed, pv.parent_version_id "
            "FROM plan_versions pv "
            "WHERE pv.plan_id = ? "
            "UNION ALL "
            "SELECT 'run_snapshot' AS kind, rs.id, rs.plan_id, rs.plan_version_id, "
            "rs.created_at AS recorded_at, 'engine_run' AS source, "
            "rs.deterministic_result_sha256 AS content_sha256, 'completed' AS status, "
            "NULL AS error_code, rs.attempt_id, rs.id AS snapshot_id, "
            "ra.precision, ra.seed, NULL AS parent_version_id "
            "FROM run_snapshots rs "
            "JOIN run_attempts ra ON ra.id = rs.attempt_id "
            "WHERE rs.plan_id = ? "
            "UNION ALL "
            "SELECT 'run_attempt' AS kind, ra.id, ra.plan_id, ra.plan_version_id, "
            "COALESCE(ra.finished_at, ra.started_at) AS recorded_at, "
            "'engine_run_attempt' AS source, NULL AS content_sha256, ra.status, "
            "CASE WHEN ra.status = 'cancelled' THEN 'cancelled' "
            "WHEN ra.error LIKE 'process_interrupted%' THEN 'process_interrupted' "
            "WHEN ra.error LIKE '%snapshot%' THEN 'snapshot_commit_failed' "
            "WHEN ra.status = 'running' THEN 'running' ELSE 'run_failed' END "
            "AS error_code, ra.id AS attempt_id, ra.snapshot_id, ra.precision, ra.seed, "
            "NULL AS parent_version_id "
            "FROM run_attempts ra "
            "WHERE ra.plan_id = ? AND ra.status != 'completed' "
            "ORDER BY 5 ASC, 2 ASC", (plan_id, plan_id, plan_id)).fetchall()
        return [dict(row) for row in rows]

    def timeline(self, plan_id: str) -> list[dict]:
        conn = self._connect()
        try:
            return self._timeline_rows(conn, plan_id)
        finally:
            conn.close()


def read_timeline(path: str, plan_id: str) -> list[dict]:
    """Read a timeline from an existing SQLite file without any write path."""
    conn = _readonly_connect(path)
    try:
        _readonly_schema_preflight(conn)
        try:
            timeline = PersistenceStore._timeline_rows(conn, plan_id)
            _validate_readonly_sidecars(path)
            return timeline
        except PlanNotFoundError:
            raise
        except sqlite3.Error as exc:
            raise PersistenceError("timeline database is not readable") from exc
    finally:
        conn.close()


def replay_snapshot(store: PersistenceStore, snapshot_id: str,
                    runner: Callable[[dict, int, int, int], dict], *,
                    source_root: Optional[str] = None,
                    runtime_metadata: Optional[dict] = None) -> tuple[dict, str]:
    """Replay a snapshot after preflighting the current source/runtime build."""
    snapshot = store.get_snapshot(snapshot_id)
    if not source_root:
        raise PersistenceError(
            "replay requires an explicit current source_root build preflight")
    runtime_build_id = store.runtime_build_id_for_attempt(
        {"attempt_id": snapshot["attempt_id"]},
        source_root=source_root, metadata=runtime_metadata)
    if runtime_build_id != snapshot["engine_build_id"]:
        raise PersistenceError("replay runtime build does not match snapshot build")
    protocol = snapshot["protocol"]
    runner_module = sys.modules.get(getattr(runner, "__module__", ""))
    runner_version = getattr(runner_module, "ENGINE_VERSION", None)
    if runner_version is not None and runner_version != protocol["engine"]:
        raise PersistenceError(
            f"replay runner engine mismatch: {runner_version!r} != {protocol['engine']!r}")
    # Replay the layout the snapshot RECORDED, not the one today's threshold
    # would pick.
    #
    # `_run_chunked_stats` gives chunk i the seed `seed + i`, so the chunk
    # layout decides the numbers: the same (n, seed) run sequentially and
    # chunked produce different results, correctly. Measured on a marginal
    # plan at 20,000 paths -- 0.955450 sequential against 0.955900 chunked,
    # different deterministic digests.
    #
    # Before this, the layout came from `MP_THRESHOLD` at replay time, so
    # moving that constant made every archived snapshot fail to replay. The
    # mode is an input now, which is what lets the threshold change at all.
    #
    # A runner that does not accept it is called the old way rather than
    # refused: `replay_snapshot` takes any callable, several tests pass
    # deliberately-tampered ones, and breaking those would be this change
    # deciding what a runner is allowed to be.
    mode = protocol["execution_mode"]
    try:
        result = runner(snapshot["resolved_input"], int(protocol["paths"]),
                        int(protocol["seed"]), int(protocol["dist_paths"]),
                        execution_mode=mode)
    except TypeError:
        result = runner(snapshot["resolved_input"], int(protocol["paths"]),
                        int(protocol["seed"]), int(protocol["dist_paths"]))
    actual_mode = result.get("mode", "sequential") if isinstance(result, dict) else None
    if actual_mode != mode:
        raise PersistenceError(
            f"replay execution mode mismatch: {actual_mode!r} != {mode!r}")
    actual_hash = deterministic_result_sha256(result)
    if actual_hash != snapshot["deterministic_result_sha256"]:
        raise PersistenceError(
            "replay deterministic result does not match archived snapshot")
    return result, actual_hash


def default_database_path(app_support_root: Optional[str] = None) -> str:
    """Return the intended future production path without opening it."""
    root = (Path(app_support_root).expanduser() if app_support_root else
            Path.home() / "Library" / "Application Support" /
            "com.local.fire-modeling")
    return str(root / "fire-modeling.sqlite3")
