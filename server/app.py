"""
app.py — local HTTP server for the FIRE Modeling desktop app.

Pure Python standard library + numpy (already installed). No web framework, no
CDN, no network access. Wraps the authoritative v9.8 lifecycle Monte Carlo
engine (engine_adapter) and serves an interactive single-page analysis panel.

Routes
  GET  /                  -> web/index.html
  GET  /<static>          -> web/<static> (css/js)
  GET  /api/presets       -> {presets: {...}}
  POST /api/run_start     -> start a headline Monte Carlo job -> {job}
                              or {job, archive} for explicit Standard/Official archive
  GET  /api/timeline?plan_id= -> read-only plan timeline (if archived)
  GET  /api/progress?job= -> {pct, stage, done, error}
  GET  /api/result?job=   -> the finished job's full payload
  POST /api/cancel       -> request cancellation of a running job
  POST /api/sweep         -> 1-D parameter sweep (SWR, claim age, ...)
  POST /api/sensitivity   -> tornado + return-mu uncertainty band
  POST /api/backtest      -> GK behaviour under stylized stress sequences
  POST /api/report        -> build the polished standalone HTML report
  POST /api/migration/shadow_preview -> validate/project raw localStorage (no write)
  POST /api/migration/shadow_stage   -> stage a raw localStorage backup (opt-in)
  GET  /api/migration/authority      -> formal migration authority/status seam
  POST /api/migration/preview        -> formal M1 disposable preview
  POST /api/migration/stage          -> formal M1 raw-envelope staging
  POST /api/migration/import         -> formal M2 disposable import
  POST /api/migration/verify         -> formal M2 fresh-read verification
  POST /api/backup/prepare            -> stage a content-addressed package
  POST /api/backup/finalize           -> publish a package after envelope re-read
  POST /api/restore/prepare           -> validate a managed package in staging
  POST /api/restore/commit            -> swap a staged archive with rollback
  POST /api/shutdown      -> quit the app (user-triggered from the page)

Run:  python3 server/app.py [--port 8765] [--no-open]
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import secrets
import signal
import sys
import threading
import time
import traceback
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
# Paths are overridable by env so the same code works when frozen into a
# self-contained .app (PyInstaller sets these to the bundled data locations).
ENGINE_DIR = os.environ.get("FIRE_ENGINE_DIR") or os.path.join(ROOT, "engine")
WEB_DIR = os.environ.get("FIRE_WEB_DIR") or os.path.join(ROOT, "web")
sys.path.insert(0, ENGINE_DIR)
sys.path.insert(0, HERE)


def _arch_self_heal():
    """A universal2 Python launched (e.g. from Finder) in an architecture whose
    numpy isn't installed will fail to import numpy even though it's present.
    If that happens, re-exec this script once under the other CPU architecture.
    No-op on the happy path, when frozen (numpy is bundled), and on non-macOS."""
    if getattr(sys, "frozen", False):
        return
    if os.environ.get("FIRE_ARCH_REEXEC") == "1":
        return
    try:
        import numpy  # noqa: F401
        return
    except Exception:
        pass
    if sys.platform != "darwin":
        return
    import platform
    other = "x86_64" if platform.machine() == "arm64" else "arm64"
    env = dict(os.environ, FIRE_ARCH_REEXEC="1")
    try:
        os.execvpe("arch", ["arch", "-" + other, sys.executable, *sys.argv], env)
    except Exception:
        pass  # fall through; the real numpy ImportError will surface below


_arch_self_heal()

import engine_adapter as ENG           # noqa: E402  (authoritative v9.8 engine chain)
import asset_location as AL        # noqa: E402
import roth_schedule as RSCH       # noqa: E402
import funded_ratio as FRATIO      # noqa: E402
import limitations as LIMITATIONS_MOD  # noqa: E402
import sampling_error as SAMPLING_ERROR  # noqa: E402
import throughput as THROUGHPUT  # noqa: E402
import briefing_pack as BRIEFING_PACK  # noqa: E402
import family_evidence as FAMILY_EVIDENCE  # noqa: E402
import life_transitions as LIFE_TRANSITIONS  # noqa: E402
from decision_lab import (  # noqa: E402
    SWEEP_CAP, SENS_CAP,
    _get_path, _set_path, _base_cfg, _scale_portfolio, _select_roth_best,
    GOALSEEK_METRICS, GOALSEEK_LEVERS, _set_equity, _GsCancelled, _gs_validate,
    run_sweep, run_goalseek, _dominates, run_frontier, run_sensitivity,
    run_backtest,
)
import presets as PRESETS_MOD       # noqa: E402
import build_report                 # noqa: E402
import migration_bridge as MIGRATION  # noqa: E402
import recovery as RECOVERY          # noqa: E402
import formal_migration as FORMAL_MIGRATION  # noqa: E402
import storage_api as STORAGE        # noqa: E402
import archive_seam as ARCHIVE_SEAM  # noqa: E402
import working_draft as WORKING_DRAFT  # noqa: E402
import checkin_seam as CHECKIN         # noqa: E402
import decision_archive as DECISION_ARCHIVE  # noqa: E402
import decision_review as DECISION_REVIEW  # noqa: E402
from persistence import (  # noqa: E402
    IdempotencyConflictError,
    PersistenceError,
    PersistenceStore,
    PlanNotFoundError,
    TIMELINE_PROTOCOL_VERSION,
    default_database_path,
    normalize_config as _normalize_persistence_config,
    read_timeline,
    utc_now,
    validate_request_id,
)

MAX_PATHS = 200_000                 # safety ceiling for the interactive tool
DIST_PATHS = 1500                   # illustrative-distribution sample size
# The formal envelope parser and HTTP gate share the same envelope limit; the
# formal module owns the explicit wrapper budget for preview/stage requests.
MAX_REQUEST_BYTES = (FORMAL_MIGRATION.MAX_FORMAL_ENVELOPE_BYTES
                     + FORMAL_MIGRATION.FORMAL_HTTP_WRAPPER_BUDGET_BYTES)
MIGRATION_SHADOW_DIR = (os.environ.get("FIRE_MIGRATION_SHADOW_DIR")
                        or MIGRATION.default_shadow_backup_dir())
#: Paths for the per-strategy spending fan. Small on purpose: it is a
#: percentile band, not a tail probability, and 300 costs ~0.3s per
#: rule against ~1.9s for that rule's success rate.
FAN_PATHS = 300

PRECISION_BY_PATHS = {2_000: "quick", 10_000: "standard",
                      30_000: "deep", 100_000: "official"}
ARCHIVE_PRECISIONS = frozenset(("standard", "official"))


def study_paths_for(run_paths) -> int:
    """The path count a formal decision study runs at, given the run on screen.

    Ruled 2026-08-16: round UP to the smallest precision that can carry a
    Robust claim, never down. An Official run is studied at Official; a Deep
    run -- 30,000 paths, which `build_packet` refuses by name -- escalates to
    Official rather than being quietly downgraded to Standard.

    This lives here, on the side that owns `PRECISION_BY_PATHS`, because the
    page had its own copy of the tier logic and its own copy of the tier list.
    That is the shape every defect found on 2026-08-15/16 has had: one fact,
    two places, and only one of them maintained. The page now sends the run's
    own path count and reads back the answer.
    """
    qualifying = sorted(paths for paths, name in PRECISION_BY_PATHS.items()
                        if name in ARCHIVE_PRECISIONS)
    try:
        run = int(run_paths or 0)
    except (TypeError, ValueError):
        run = 0
    for paths in qualifying:
        if paths >= run:
            return paths
    return qualifying[-1]
# Underscores are inside the character class because the server mints ids that
# contain them: `_m2_target_id` prefixes migrated objects `plan_mig_` /
# `ver_mig_` as a deliberate provenance marker. Without them this validator
# rejected the archive references the migration had just created, so a formal
# run against a migrated Plan could not name it and the server created a
# second Plan instead. Still bounded, and still no separators, dots or dashes.
ARCHIVE_REF_RE = re.compile(r"^(?:plan|ver)_[A-Za-z0-9_]{16,80}$")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
}


class RequestTooLarge(ValueError):
    pass


# Every §6 POST that can create or mutate a stored object.  Each one must carry
# an `Idempotency-Key` equal to its body's `request_id`; see
# _require_idempotency_key.
_IDEMPOTENT_POST_PATHS = (
    "/api/storage/plan", "/api/storage/plan-version",
    "/api/storage/plan-duplicate", "/api/storage/plan-status",
    "/api/storage/draft", "/api/storage/observe",
    "/api/storage/parent-identity-link",
    "/api/storage/parent-identity-evaluation",
    "/api/storage/parent-identity-link-end",
)

# Synchronous routes that actually start FIRE-engine work. Background starters
# keep their own preflight because it must happen before _new_job(), while the
# archive headline route must validate its resolved config rather than the raw
# partial request. Keeping this set explicit makes a newly added route fail the
# route-inventory contract instead of silently accepting an invalid plan.
_SYNC_ENGINE_PREFLIGHT_ROUTES = frozenset({
    "/api/roth_opt", "/api/drill", "/api/rentbuy", "/api/story",
    "/api/live", "/api/strategies", "/api/robustness", "/api/sweep",
    "/api/sensitivity", "/api/backtest",
    # OPEN_ITEMS E33. The wizard sidebar's savings figure. It belongs in this
    # set for the same reason as the rest -- it maps a config and runs engine
    # code -- and it is the cheapest member by a wide margin: one year, no
    # paths, no RNG.
    "/api/estimate/savings",
})


class TrustBoundaryError(ValueError):
    """A request failed the local HTTP trust-boundary contract."""

    def __init__(self, message: str, status: int):
        super().__init__(message)
        self.status = status


def _reject_json_constant(value):
    raise ValueError(f"non-finite JSON number: {value}")


def _reject_duplicate_json_pairs(pairs):
    """Reject last-value-wins JSON objects at the HTTP boundary."""
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON object key")
        result[key] = value
    return result


def _scrub_api_text(value) -> str:
    """Remove local absolute paths and control characters from API strings."""
    text = str(value or "")
    home = os.path.expanduser("~")
    for prefix, replacement in ((ROOT, "<app>"), (home, "~"),
                                (ENGINE_DIR, "<engine>"), (WEB_DIR, "<web>")):
        if prefix:
            text = text.replace(prefix, replacement)
    text = re.sub(r"(?:/[^\s:/]+){2,}", "<local path>", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)[:500]


def _public_error(exc: Exception) -> str:
    return _scrub_api_text(str(exc)) or "request failed"


# --------------------------------------------------------------- cfg helpers










# ------------------------------------------------------- progress-bar jobs
# The headline run can take tens of seconds, so it runs in a background thread
# that reports a smooth 0–100% via ENG.run_full's callback. The client starts a
# job (/api/run_start), polls /api/progress, then fetches /api/result.
_JOBS: dict = {}
_JOBS_LOCK = threading.Lock()
_JOB_SEQ = [0]
_ARCHIVE_STORE_LOCK = threading.Lock()
_ARCHIVE_STORE = None
_ARCHIVE_STORE_PATH = None
_ACTIVE_ARCHIVE_ATTEMPTS: set[str] = set()
_ORPHANS_RECONCILED_PATH = None
_RECOVERY_MANAGER_LOCK = threading.Lock()
_RECOVERY_MANAGER = None
_RECOVERY_MANAGER_PATH = None


def _persistence_database_path() -> str:
    """Resolve the app-support DB path without opening or creating it."""
    return os.path.abspath(os.path.expanduser(
        os.environ.get("FIRE_PERSISTENCE_DB") or default_database_path()))


def _archive_writer():
    """The archive-write seam for formal runs, or None when there is no journal.

    Opt-in exactly as the rest of Phase 0 is: no control database means nothing
    owns the archive, so the 2.0 path writes it directly and this returns None.
    """
    path = _persistence_database_path()
    if not ARCHIVE_SEAM.journal_exists(path):
        return None
    return ARCHIVE_SEAM.JournaledArchiveWriter(_recovery_manager(), path)


def _initialise_archive_via_seam(writer, path):
    """Create the archive as an owned archive_write, or do nothing.

    The first formal run on a fresh install has no archive to write into. The
    direct path created one by opening a store, which under a control journal is
    an unowned durable write and latches the seam at the next reconciliation —
    §8's "any schema-initialization write" is in the requirement for this reason.
    """
    if os.path.exists(path):
        return
    writer.write(
        "archive-schema-initialise",
        # Opening the staged store is the mutation: it creates the schema.
        lambda staged: True)


def _reconcile_orphan_attempts_via_seam(writer):
    """Fail closed any attempt a previous process left running.

    A durable archive mutation like any other, so under a journal it goes through
    the seam rather than straight at the live file. `NOTHING_TO_DO` when there are
    no orphans: an archive_write for a no-op would advance the generation for no
    change, and a generation that moved without a reason cannot be told from one
    that moved for a reason nobody recorded.

    No `close_store`/`reopen_store` is passed, and that is not an omission: this
    runs while `_ARCHIVE_STORE` is None and before it is opened, so there is no
    live handle for the swap to invalidate. Opening the store first and
    reconciling afterwards would leave that handle pointing at the file the swap
    replaced.
    """
    if not os.path.exists(_persistence_database_path()):
        # No archive, so no attempts and nothing to reconcile. Staging one just to
        # discover that would create the archive as a side effect of a read.
        return
    with _JOBS_LOCK:
        active = set(_ACTIVE_ARCHIVE_ATTEMPTS)

    def mutate(staged):
        if not staged.recover_running_attempts(exclude_ids=active):
            return ARCHIVE_SEAM.NOTHING_TO_DO
        return True

    writer.write("run-orphan-reconcile", mutate)


def _archive_store():
    """Lazily create one writable store after an explicit archive request."""
    global _ARCHIVE_STORE, _ARCHIVE_STORE_PATH, _ORPHANS_RECONCILED_PATH
    path = _persistence_database_path()
    with _ARCHIVE_STORE_LOCK:
        # Re-check even when the store is already cached.  A previous
        # unowned writer may have changed the logical identity between runs;
        # the next archive request must hit the recovery gate.
        RECOVERY.assert_archive_write_allowed(path)
        if _ARCHIVE_STORE is None or _ARCHIVE_STORE_PATH != path:
            # Orphan reconciliation is about attempts an *earlier process* left
            # running, so it belongs to the first open of this archive in this
            # process and not to every re-open. The store is now re-opened after
            # every archive-write swap, and reconciling there would fail the very
            # attempt the swap had just created: its id is not yet in
            # `_ACTIVE_ARCHIVE_ATTEMPTS`, because it does not exist until
            # `prepare_run` returns.
            first_open = _ORPHANS_RECONCILED_PATH != path
            writer = _archive_writer()
            if writer is None:
                # The untouched 2.0 path: no journal, no owner, direct write.
                store = PersistenceStore(path,
                                         app_release_id="fire-modeling-3.0")
                if first_open:
                    with _JOBS_LOCK:
                        active = set(_ACTIVE_ARCHIVE_ATTEMPTS)
                    store.recover_running_attempts(exclude_ids=active)
            else:
                # Creating the archive is itself a durable archive mutation, and
                # `PersistenceStore(path)` creates it as a side effect of being
                # opened. Under a journal that would be an unowned write — the
                # exact class this blocker is about — so the schema is
                # initialised through the seam first and only then opened.
                _initialise_archive_via_seam(writer, path)
                if first_open:
                    _reconcile_orphan_attempts_via_seam(writer)
                store = PersistenceStore(path,
                                         app_release_id="fire-modeling-3.0")
            _ORPHANS_RECONCILED_PATH = path
            _ARCHIVE_STORE = store
            _ARCHIVE_STORE_PATH = path
        return _ARCHIVE_STORE


def _recovery_manager():
    """Lazily open the external control journal for explicit recovery only."""
    global _RECOVERY_MANAGER, _RECOVERY_MANAGER_PATH
    path = _persistence_database_path()
    with _RECOVERY_MANAGER_LOCK:
        if _RECOVERY_MANAGER is None or _RECOVERY_MANAGER_PATH != path:
            if _RECOVERY_MANAGER is not None:
                _RECOVERY_MANAGER.close()
            _RECOVERY_MANAGER = RECOVERY.BackupRestoreManager(
                path, app_release_id="fire-modeling-3.0")
            _RECOVERY_MANAGER_PATH = path
        return _RECOVERY_MANAGER


#: The non-secret build metadata every engine-build identity in this process is
#: derived from. It lives here as one constant because `/api/run_start` records
#: a build id with it and the check-in seam recomputes one to ask whether the
#: build has moved since; if the two ever disagreed, every attribution would
#: report a model update that had not happened.
_BUILD_METADATA = {"bundle_version": "0.0.0", "git_tag": None,
                   "data_manifest_id": None}


def _formal_migration_manager():
    return FORMAL_MIGRATION.FormalMigrationManager(_recovery_manager())


def _checkin_seam():
    """The Phase 2 check-in seam, bound to this archive and this build.

    The ledger tables live in the archive database, so a recorded check-in is
    an archive mutation like any other: when a control journal owns the
    archive the write goes through it, and when none does the 2.0 direct path
    is left exactly as it was. That is the same two-branch shape `/api/run_start`
    uses, and it is here rather than inside the seam because which of the two
    applies is a property of this process's archive, not of the request.
    """
    writer = _archive_writer()

    def write(key, mutate):
        return writer.write(
            key, mutate,
            close_store=_close_archive_store_for_recovery,
            reopen_store=_reopen_archive_store_after_recovery)

    return CHECKIN.CheckinSeam(
        _archive_store(),
        engine_version=ENG.ENGINE_VERSION,
        source_root=ROOT,
        write=None if writer is None else write,
        metadata=dict(_BUILD_METADATA))


def _decision_archive_seam():
    """The Phase 4 decision record, bound to this archive.

    Same two-branch shape as `_checkin_seam`, and here for the same reason:
    whether a control journal owns the archive is a property of this process,
    not of the request.
    """
    writer = _archive_writer()

    def write(key, mutate):
        return writer.write(
            key, mutate,
            close_store=_close_archive_store_for_recovery,
            reopen_store=_reopen_archive_store_after_recovery)

    return DECISION_ARCHIVE.DecisionArchiveSeam(
        _archive_store(), write=None if writer is None else write)


def _close_archive_store_for_recovery():
    """Drain the normal writer before an explicit archive filesystem swap."""
    global _ARCHIVE_STORE
    with _ARCHIVE_STORE_LOCK:
        if _ARCHIVE_STORE is not None:
            _ARCHIVE_STORE.close()
            _ARCHIVE_STORE = None


def _reopen_archive_store_after_recovery():
    return _archive_store()


def _latched_authority_payload():
    """The current authority, read without going through the latched bootstrap.

    §6 requires every error response to carry the current authority receipt so a
    refused caller can resynchronise without a round-trip of guessing. A 423 was
    the one refusal that carried nothing, because the thing that raises it is
    `_bootstrap` — the very call that would normally assemble the payload.

    So it is read straight out of the control journal. Best-effort by design: a
    journal too damaged to answer must not turn a clear 423 into a 500.
    """
    try:
        snapshot = _recovery_manager().journal.snapshot()
    except Exception:                                      # noqa: BLE001
        return {}
    authority = (snapshot or {}).get("authority") or {}
    generation = (snapshot or {}).get("generation") or {}
    if not authority:
        return {}
    payload = {"authority_status": authority.get("status"),
               "legacy_digest_last_seen": authority.get("legacy_digest_last_seen")}
    if generation.get("generation_id"):
        payload["generation_id"] = generation["generation_id"]
    # The receipt too, when it can be had. §6 wants a refused caller able to
    # resynchronise without guessing, and the receipt is exactly what it would
    # otherwise have to guess at. Same derivation the §6 state read uses — the
    # latest authority event for the owning operation — and best-effort for the
    # same reason as everything else here: a journal too damaged to answer must
    # not turn a clear refusal into a 500.
    try:
        operation_id = authority.get("operation_id")
        if operation_id:
            events = _recovery_manager().journal.authority_events_for_operation(
                operation_id)
            if events:
                payload["receipt_sha256"] = events[-1]["receipt_sha256"]
    except Exception:                                      # noqa: BLE001
        pass
    return payload


def _validate_archive_ref(value, field: str, prefix: str):
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"{field} must be a server plan reference")
    if not ARCHIVE_REF_RE.fullmatch(value) or not value.startswith(prefix + "_"):
        raise ValueError(f"{field} is not a valid server plan reference")
    return value


def _archive_context(context: dict) -> dict:
    return {
        "plan_id": context["plan_id"],
        "plan_version_id": context["plan_version_id"],
        "protocol": copy.deepcopy(context["protocol"]),
        "timeline_protocol": TIMELINE_PROTOCOL_VERSION,
    }


def _begin_snapshot_commit(jid: str) -> bool:
    """Linearize cancellation against the immutable snapshot transaction."""
    with _JOBS_LOCK:
        job = _JOBS.get(jid)
        if not job or job.get("cancelled") or job.get("done"):
            return False
        job["commit_started"] = True
        return True


def _new_job() -> str:
    with _JOBS_LOCK:
        _JOB_SEQ[0] += 1
        jid = str(_JOB_SEQ[0])
        _JOBS[jid] = {"pct": 0.0, "stage": "init", "done": False,
                      "result": None, "error": None, "cancelled": False,
                      "commit_started": False, "archive": None}
        # Running jobs retain progress/result/cancel access even under bursts.
        while len(_JOBS) > 8:
            completed = [old for old in sorted(_JOBS, key=int)
                         if old != jid and _JOBS[old].get("done")]
            if not completed:
                break
            _JOBS.pop(completed[0], None)
        return jid


def _job_set(jid, **kw):
    with _JOBS_LOCK:
        if jid in _JOBS:
            _JOBS[jid].update(kw)


def _hydrate_idempotent_job(jid: str, context: dict,
                            store: PersistenceStore) -> str:
    """Replay a durable request without executing the engine a second time."""
    status = context["request_status"]
    if status == "running":
        existing_job = context.get("job_id")
        with _JOBS_LOCK:
            live = _JOBS.get(existing_job) if existing_job else None
            if live and not live.get("done"):
                _JOBS.pop(jid, None)
                return existing_job
        _job_set(jid, error="idempotent request is still running", done=True,
                 pct=1.0, stage="error", archive=context.get("archive"))
        return jid
    if status == "completed":
        try:
            snapshot = store.get_snapshot(context["snapshot_id"])
            result = copy.deepcopy(snapshot["result"])
            result.setdefault("meta", {})["snapshot_id"] = context["snapshot_id"]
        except PersistenceError:
            _job_set(jid, error="idempotent snapshot unavailable", done=True,
                     pct=1.0, stage="error", archive=context.get("archive"))
            return jid
        _job_set(jid, result=result, done=True, pct=1.0, stage="done",
                 archive=context.get("archive"))
        return jid
    _job_set(jid, error=context.get("error") or "idempotent request failed",
             done=True, pct=1.0, stage=status, archive=context.get("archive"))
    return jid


def _preflight_config(cfg: dict, *, store=None) -> None:
    """Raise ENG.ConfigIncomplete BEFORE a job exists, on the config that will
    actually run.

    A background job turns every refusal into a failed run: the client gets
    200, polls, and is told the computation died. For a plan that is merely
    missing a setting that is the wrong report — the same defect the archive
    branch of /api/run_start already guards against one line at a time
    ("refuse before anything durable happens").

    `store` is not None means the archive path, which runs
    `prepare_run`'s resolved config — `normalize_config` over the server
    defaults — not the raw request. Checking the raw config there would reject
    partial plans the archive path completes and runs today. If resolution
    itself fails, this says nothing: the existing path already reports that,
    and a pre-flight may only convert a would-be failure into an earlier and
    more useful one, never invent a new one.
    """
    if store is not None:
        try:
            cfg = _normalize_persistence_config(cfg, ENG.default_config)
        except Exception:                     # noqa: BLE001
            return
    ENG.check_config(cfg)


def start_run_job(cfg: dict, paths: int, seed: int, dist_paths=None, *,
                  store=None, precision: str = "standard",
                  plan_id: str = None, plan_version_id: str = None,
                  archive: bool = False, request_id: str = None,
                  writer=None) -> str:
    # Before _new_job() and before prepare_run: no job to poll, no Plan, no
    # Version, no attempt row left behind by a run that was never going to run.
    _preflight_config(cfg, store=store)
    requested_paths = int(paths)
    paths = max(200, min(requested_paths, MAX_PATHS))
    seed = int(seed)
    requested_dist_paths = int(dist_paths) if dist_paths else DIST_PATHS
    dn = max(1, min(requested_dist_paths, paths))
    jid = _new_job()

    # Phase 0 is opt-in at this seam.  The normal 2.0 route does not pass a
    # store, so its request/result path and RNG ordering remain unchanged until
    # the localStorage cutover has its own migration and frozen-app gates.
    persistence_context = None
    run_cfg = cfg

    def _prepare(target):
        return target.prepare_run(
            cfg, default_factory=ENG.default_config,
            engine_version=ENG.ENGINE_VERSION, paths=paths,
            dist_paths=dn, seed=seed, precision=precision, job_id=jid,
            requested_paths=requested_paths,
            requested_dist_paths=requested_dist_paths,
            plan_id=plan_id, plan_version_id=plan_version_id,
            request_id=request_id,
            source_root=ROOT,
            metadata=dict(_BUILD_METADATA))

    if store is not None:
        try:
            if writer is None:
                # The 2.0 path, unchanged: nothing owns this archive.
                persistence_context = _prepare(store)
            else:
                # Attempt preparation is a durable archive mutation — a Plan, a
                # Version, a request and an attempt row — so it is one
                # archive_write operation of its own, taken and released before
                # the engine starts. The ids it returns are valid afterwards
                # because the staged image it created them in *becomes* the
                # archive at the swap.
                persistence_context = writer.write(
                    "run-prepare:" + str(request_id), _prepare,
                    close_store=_close_archive_store_for_recovery,
                    reopen_store=_reopen_archive_store_after_recovery)
                store = _archive_store()
            if persistence_context.get("_idempotent_replay"):
                return _hydrate_idempotent_job(jid, persistence_context, store)
            run_cfg = persistence_context["resolved_config"]
            with _JOBS_LOCK:
                _ACTIVE_ARCHIVE_ATTEMPTS.add(persistence_context["attempt_id"])
                if archive:
                    _JOBS[jid]["archive"] = _archive_context(persistence_context)
        except IdempotencyConflictError:
            with _JOBS_LOCK:
                _JOBS.pop(jid, None)
            raise
        except Exception as exc:              # noqa: BLE001
            _job_set(jid, error=_public_error(exc), done=True, stage="error")
            return jid

    class _Cancelled(Exception):
        pass

    def _finish(attempt_id, status, error):
        """Close an attempt as failed or cancelled — also a durable write.

        A cancellation or an engine failure still changes rows the journal is
        accountable for, so it is an archive_write too. Leaving these two on the
        direct path would have re-opened the whole defect for every run that did
        not succeed, which is the half a success-path repair quietly misses.
        """
        if writer is None:
            store.finish_attempt(attempt_id, status, error=error)
            return
        writer.write(
            "run-finish:" + str(attempt_id) + ":" + status,
            lambda target: target.finish_attempt(attempt_id, status,
                                                 error=error) or True,
            close_store=_close_archive_store_for_recovery,
            reopen_store=_reopen_archive_store_after_recovery)

    def work():
        try:
            t0 = time.time()

            def ENG_cb(pct, stage):
                with _JOBS_LOCK:
                    if _JOBS.get(jid, {}).get("cancelled"):
                        raise _Cancelled()
                _job_set(jid, pct=float(pct), stage=str(stage))

            res = ENG.run_full(run_cfg, paths, seed, dn, ENG_cb)
            st = run_cfg.get("state") or {}
            mode = res.pop("mode", "sequential")
            elapsed_s = round(time.time() - t0, 2)
            # One observation of how fast THIS machine runs paths, so the
            # cost panels can quote a time instead of only a run count.
            # Never raises: a telemetry failure must not fail a run.
            try:
                THROUGHPUT.record(_persistence_database_path(),
                                  units=paths, elapsed_s=elapsed_s,
                                  kind=THROUGHPUT.RUN, mode=mode)
            except Exception:                                  # noqa: BLE001
                pass
            # Under `meta`, NOT inside the engine's own blocks.
            #
            # The first version put it in `res["home"]`, and two recorded
            # contracts caught it within the hour: `replay_snapshot` recomputes
            # the engine result and compares, so a server-derived key inside
            # the deterministic payload makes every archived run fail to
            # replay, and `test_confirmed_quote_runs_through_http_with_exact_
            # engine_output` compares server output against the engine's
            # directly. Both were right and neither was changed to
            # accommodate this. `meta` is where server-added facts already
            # live -- `current_age`, `protocol`, the wall-clock `elapsed_s` --
            # precisely because it is outside what replay pins.
            intervals = {}
            for section in ("home", "relocation"):
                block = res.get(section)
                if isinstance(block, dict):
                    intervals[section] = SAMPLING_ERROR.success_interval(
                        block.get("lifetime_success"), block.get("n_paths"))
            res["meta"].update({
                "sampling_error": intervals,
                "current_age": st.get("start_age"),
                "annual_retirement_spending": st.get("expenses_y0"),
                "safe_withdrawal_rate": st.get("swr_pref"),
                "relocation_enabled": bool((run_cfg.get("relocation") or {}).get("enabled", False)),
                "protocol": {"paths": paths, "seed": seed, "engine": ENG.ENGINE_VERSION,
                             "mode": mode,
                             "elapsed_s": elapsed_s},
            })
            for k in ("home", "relocation"):
                if k in res:
                    res[k]["seed"] = seed

            if persistence_context is not None:
                if not _begin_snapshot_commit(jid):
                    raise _Cancelled()
                # Store the complete server-produced payload before exposing
                # the job as done.  snapshot_id is added only to the response;
                # it is not part of the archived result hash.
                if archive:
                    res["meta"]["archive"] = {
                        "plan_id": persistence_context["plan_id"],
                        "plan_version_id": persistence_context["plan_version_id"],
                    }
                def _commit(target):
                    # Protocol, receipt and snapshot on one store instance: the
                    # receipt token is per-instance, so minting it and spending
                    # it have to happen against the same object.
                    target.adopt_run_context(persistence_context)
                    protocol_ = target.protocol_for_attempt(
                        persistence_context, execution_mode=mode)
                    protocol_["elapsed_s"] = elapsed_s
                    # Before the receipt, not after. `save_run_snapshot`
                    # re-derives the protocol from the attempt's own rows and
                    # compares it to the one carried in the payload, so the
                    # engine's summary has to be replaced by the DB-derived one
                    # first — the direct path has always done this in this order.
                    res["meta"]["protocol"] = protocol_
                    receipt_ = target.make_engine_receipt(
                        persistence_context, res, mode)
                    return (protocol_,
                            target.save_run_snapshot(persistence_context,
                                                     receipt=receipt_))

                if writer is None:
                    protocol = store.protocol_for_attempt(
                        persistence_context, execution_mode=mode)
                    protocol["elapsed_s"] = elapsed_s
                    res["meta"]["protocol"] = protocol
                    receipt = store.make_engine_receipt(
                        persistence_context, res, mode)
                    snapshot_id = store.save_run_snapshot(
                        persistence_context, receipt=receipt)
                else:
                    # Second operation, after the engine has finished. Nothing
                    # was held across the computation.
                    protocol, snapshot_id = writer.write(
                        "run-snapshot:" + str(persistence_context["attempt_id"]),
                        _commit,
                        close_store=_close_archive_store_for_recovery,
                        reopen_store=_reopen_archive_store_after_recovery)
                    res["meta"]["protocol"] = protocol
                res["meta"]["snapshot_id"] = snapshot_id
            _job_set(jid, result=res, done=True, pct=1.0, stage="done")
        except _Cancelled:
            if persistence_context is not None:
                _finish(persistence_context["attempt_id"], "cancelled",
                        "cancelled")
            _job_set(jid, error="cancelled", done=True, stage="cancelled")
        except Exception as exc:              # noqa: BLE001
            if persistence_context is not None:
                try:
                    _finish(persistence_context["attempt_id"], "failed",
                            _public_error(exc))
                except Exception:
                    traceback.print_exc()
            traceback.print_exc()
            _job_set(jid, error=_public_error(exc), done=True, stage="error")
        finally:
            if persistence_context is not None:
                with _JOBS_LOCK:
                    _ACTIVE_ARCHIVE_ATTEMPTS.discard(persistence_context["attempt_id"])

    threading.Thread(target=work, daemon=True).start()
    return jid


def _query_job(path: str):
    q = parse_qs(urlparse(path).query)
    return (q.get("job") or [""])[0]


# ---------------------------------------------------------------- /api/sweep


# ------------------------------------------------------------ /api/goalseek
# S1 universal goal-seek: ONE goal × TWO levers over a coarse grid with
# boundary refinement. Every evaluation goes through ENG.summary (engine lock
# + global-hook serialization inherited). ~80 evals at 1,200 paths ≈ 2 min,
# so it runs as a background job (same _JOBS channel as the headline run).

# metric -> the direction that makes a goal of that metric natural

# lever key -> (config path used for the CURRENT-value readout, setter)









def start_goalseek_job(cfg, goal, levers, paths, seed, grid) -> str:
    _gs_validate(goal, levers)     # bad input -> ValueError BEFORE the thread
    _preflight_config(cfg)         # and a config the engine will not map
    jid = _new_job()

    def work():
        try:
            out = run_goalseek(
                cfg, goal, levers, paths, seed, grid,
                cb=lambda p, s: _job_set(jid, pct=float(p), stage=str(s)),
                cancelled=lambda: _JOBS.get(jid, {}).get("cancelled"))
            _job_set(jid, result=out, done=True, pct=1.0, stage="done")
        except _GsCancelled:
            _job_set(jid, error="cancelled", done=True, stage="cancelled")
        except Exception as exc:              # noqa: BLE001
            traceback.print_exc()
            _job_set(jid, error=_public_error(exc), done=True, stage="error")

    threading.Thread(target=work, daemon=True).start()
    return jid


# ------------------------------------------------------------- /api/decide
# Phase 3: one decision run across all three axes `Robust` is defined over.
# Unlike the lab jobs above this one refuses to start quietly: a formal packet
# needs Standard precision, and 14 runs at 10,000 paths is minutes of machine
# time, so /api/decide/plan states the cost first and the client shows it
# before /api/decide/start is ever called.


def decide_plan(cfg, body) -> dict:
    """The cost, the packs this plan can actually be tested against, and the
    families it cannot. Runs no engine."""
    import assumption_packs as AP
    import decision_study as DS
    question = str(body.get("question") or "")
    alternatives = _decide_alternatives(body)
    chosen = AP.select_packs(cfg)
    seeds = int(body.get("seeds", 3))
    models = tuple(body.get("return_models") or DECIDE_RETURN_MODELS)
    plan = DS.plan_study(question, alternatives, seeds=seeds,
                         return_models=models,
                         adverse_packs=chosen["applicable"])
    paths = (study_paths_for(body.get("run_paths"))
             if body.get("run_paths") is not None
             else int(body.get("paths", 10_000)))
    return {
        **plan,
        "paths": paths,
        # Reported as a multiple of a run the user has already sat through
        # rather than as a fabricated seconds figure: the headline run at this
        # precision is their own reference point, and this machine's rate is
        # not something the server can know in advance.
        "equivalent_headline_runs": plan["engine_runs"],
        "total_simulated_paths": plan["engine_runs"] * paths,
        # A time, not only a run count -- calibrated on THIS machine from
        # previous STUDIES, and absent until one has been timed here. A
        # built-in constant would be my machine's speed presented as theirs,
        # and borrowing the single-run rate would be the 3x error the module
        # docstring records.
        "time_estimate": THROUGHPUT.estimate(
            _persistence_database_path(),
            units=plan["engine_runs"] * paths,
            kind=THROUGHPUT.STUDY),
        "packs": [p.describe() for p in chosen["applicable"]],
        "packs_skipped": chosen["skipped"],
        "families_covered": chosen["families_covered"],
        "families_missing": chosen["families_missing"],
        "families_total": chosen["families_total"],
    }


def _decide_alternatives(body) -> list:
    import decision_packet as DP
    out = []
    for entry in body.get("alternatives") or []:
        out.append(DP.Alternative(str((entry or {}).get("name") or ""),
                                  (entry or {}).get("changes") or {},
                                  str((entry or {}).get("rationale") or "")))
    return out


DECIDE_RETURN_MODELS = ("iid", "markov", "blocks")


def start_decide_job(cfg, body, seed) -> str:
    import decision_packet as DP
    import decision_study as DS
    import assumption_packs as AP
    _preflight_config(cfg)         # same rule as plan_study below, for the config
    alternatives = _decide_alternatives(body)
    chosen = AP.select_packs(cfg)
    constraints = [DP.Constraint(str(c.get("kind")), str(c.get("metric")),
                                 float(c.get("threshold")))
                   for c in (body.get("constraints") or [])]
    paths = (study_paths_for(body.get("run_paths"))
             if body.get("run_paths") is not None
             else int(body.get("paths", 10_000)))
    precision = PRECISION_BY_PATHS.get(paths)
    # The same refusal `build_packet` makes, made here instead of there.
    #
    # There, it lands after `run_study` has finished every engine run --
    # twenty-odd Monte Carlo runs, minutes of them -- because the packet is
    # assembled last. The user waits for the whole study and is then told the
    # precision it was run at cannot carry its conclusion. Nothing about that
    # verdict needed a single path to be drawn.
    #
    # Reachable rather than theoretical: the UI offers a Deep tier at 30,000
    # paths, `PRECISION_BY_PATHS[30_000]` is `deep`, and `build_packet`
    # refuses `deep` by name. The page now rounds up to a tier that qualifies
    # (ruled 2026-08-16), but the page is not the only thing that can call
    # this, and "the caller will send a good value" is what E13 assumed.
    if precision not in ARCHIVE_PRECISIONS:
        raise ValueError(
            "a formal decision packet needs Standard (10,000) or Official "
            "(100,000) paths; %s cannot carry a Robust claim, and running it "
            "first would not change that" % (precision or "%d paths" % paths))
    # Checked before the thread so the user meets the refusal immediately
    # rather than after minutes of computation.
    # The return value is kept, not discarded: the work below records how
    # long a study of this SHAPE took, and the shape is `engine_runs`. The
    # first version of that recording referenced a `plan` this scope never
    # had, and its blanket `except Exception` would have swallowed the
    # NameError -- a sample silently never written, which looks exactly like
    # a machine that has not been timed yet.
    plan = DS.plan_study(str(body.get("question") or ""), alternatives,
                         seeds=int(body.get("seeds", 3)),
                         return_models=DECIDE_RETURN_MODELS,
                         adverse_packs=chosen["applicable"])
    jid = _new_job()

    def work():
        try:
            # Progress WITHOUT giving up the pool. Injecting a serial runner to
            # count completions is how this shipped at first, and it silently
            # dropped the process-level parallelism ROADMAP names as an
            # explicit Phase 3 deliverable — 22 runs took 3.6s serial against
            # 1.3s parallel on this machine. `on_progress` reports each landing
            # while the pool keeps the work spread across cores.
            def on_progress(done, total):
                # Cooperative cancellation at the next completed engine
                # point. Raising from the pool consumer exits its context,
                # which terminates work that has not landed yet without
                # changing ensemble/process-pool architecture.
                if _JOBS.get(jid, {}).get("cancelled"):
                    raise _GsCancelled()
                _job_set(jid, pct=min(done / max(total, 1), 0.99),
                         stage="%d/%d" % (done, total))

            if _JOBS.get(jid, {}).get("cancelled"):
                raise _GsCancelled()
            study_t0 = time.time()
            packet = DS.run_study(
                cfg, str(body.get("question") or ""), alternatives,
                paths=paths, seed=seed, root=ROOT,
                adverse_packs=chosen["applicable"], constraints=constraints,
                seeds=int(body.get("seeds", 3)),
                return_models=DECIDE_RETURN_MODELS,
                protocol={"precision": precision, "true_tax": True,
                          "engine_version": ENG.ENGINE_VERSION},
                analyses=body.get("analyses"), on_progress=on_progress)
            if _JOBS.get(jid, {}).get("cancelled"):
                raise _GsCancelled()
            # What a study of this shape actually costs here. Recorded as its
            # own kind: a study's rate cannot be derived from a single run's,
            # which the module docstring measures at 7.5x rather than 20x.
            try:
                THROUGHPUT.record(
                    _persistence_database_path(),
                    units=(plan.get("engine_runs") or 0) * paths,
                    elapsed_s=time.time() - study_t0,
                    kind=THROUGHPUT.STUDY)
            except Exception:                                  # noqa: BLE001
                pass
            packet["packs_skipped"] = chosen["skipped"]
            packet["families_missing"] = chosen["families_missing"]
            packet["families_total"] = chosen["families_total"]
            _job_set(jid, result=packet, done=True, pct=1.0, stage="done")
        except _GsCancelled:
            _job_set(jid, error="cancelled", done=True, stage="cancelled")
        except Exception as exc:              # noqa: BLE001
            traceback.print_exc()
            _job_set(jid, error=_public_error(exc), done=True, stage="error")

    threading.Thread(target=work, daemon=True).start()
    return jid


# ------------------------------------------------------------- /api/annuity
# Phase 2: the license-to-spend half of the annuity decision. The robustness
# half is /api/decide, which already takes alternatives — this route builds the
# arms and hands the same list to that one rather than growing a second study
# engine. What it owns is the spending-ceiling search, which is expensive in a
# way the user should see priced before it starts: every arm costs several full
# runs, and the count is not obvious from the outside.


def _annuity_context(cfg, body):
    """The arms, built against the ages and blocks the engine will actually use.

    Two things this has to get right, and driving the route is what found both.

    The plan's current age is `state.start_age`; there is no `state.current_age`,
    and reading one gave 0, which made every arm "defer to N" — including the
    one starting today.

    And a request may post a partial config, or none: the engine fills the rest
    from its own defaults, but `Alternative.apply` refuses a path the config
    does not literally hold. So the block the arm switches on is seeded from
    `default_config()` when the request omitted it. Only that block — every
    other key the user sent stays exactly as sent.
    """
    import guaranteed_income_packet as GIP
    defaults = ENG.default_config()
    prepared = copy.deepcopy(cfg) if isinstance(cfg, dict) else {}
    if not isinstance(prepared.get("guaranteed_income"), dict):
        prepared["guaranteed_income"] = copy.deepcopy(
            defaults["guaranteed_income"])
    age = _get_path(prepared, "state.start_age")
    if not isinstance(age, (int, float)):
        age = defaults["state"]["start_age"]
    # Same treatment for the spending figure, and for a sharper reason: the
    # ceiling search only uses it to pick a bracket and falls back on its own,
    # but the consumption reading RUNS the plan at it. Absent, that was 0.0 and
    # the engine divided by it — a crash reached by the ordinary case of
    # posting no config, which every API test happened to avoid.
    spending = _get_path(prepared, "state.expenses_y0")
    if not isinstance(spending, (int, float)) or spending <= 0:
        prepared.setdefault("state", {})["expenses_y0"] = float(
            defaults["state"]["expenses_y0"])
    # The baseline is "don't buy", and it has to be FORCED off rather than
    # taken as posted. Reaching this panel at all requires switching the module
    # on and entering a quote, so the config that arrives here already holds
    # the annuity — and measuring it against itself produced a consumption
    # delta of exactly 0.0 to twelve digits. Every user would have hit that,
    # and it reads as "the annuity changes nothing". Same treatment
    # `_base_cfg` gives relocation, for the same reason.
    existing = prepared["guaranteed_income"]
    # What this clears is named rather than silently dropped: the rule here is
    # that a subsystem the user switched on and the run then ignored has to be
    # reported. Both sides of the comparison are the plan WITHOUT guaranteed
    # income, plus the one instrument the arm is about — comparable, but not
    # the plan the user is running if they already hold a ladder.
    dropped = []
    if existing.get("mode") != "off" and existing.get("annuities"):
        dropped.append("the %d annuity/annuities already in the plan"
                       % len(existing["annuities"]))
    if existing.get("mode") != "off" and existing.get("ladders"):
        dropped.append("the %d TIPS ladder(s) already in the plan"
                       % len(existing["ladders"]))
    prepared["guaranteed_income"] = {
        **existing, "mode": "off", "annuities": [], "ladders": [],
    }
    built = GIP.build_alternatives(
        body.get("quotes") or [], current_age=int(age),
        defer_age=int(body.get("defer_age", GIP.DEFER_AGE)))
    built["dropped_from_both_sides"] = dropped
    return prepared, built


def annuity_plan(cfg, body) -> dict:
    """The arms, the arms that cannot be built, and what the search will cost.

    Runs no engine.
    """
    prepared, built = _annuity_context(cfg, body)
    paths = int(body.get("paths", 2_000))
    budget = int(body.get("max_evaluations", 10))
    # One search for the baseline plus one per arm. Quoted as an upper bound
    # because the search stops early when the bracket closes, and a quote that
    # could be exceeded is worse than one that is beaten.
    searches = 1 + len(built["alternatives"]) if built["alternatives"] else 0
    # One more run per side, at the plan's own spending, for the consumption
    # reading. Cheap and necessary: the guardrails flatten `lifetime_success`,
    # so a packet carrying only the ceiling search reports "below resolution"
    # for essentially every annuity and the user learns nothing.
    consumption_runs = searches
    alternatives = [a.describe() for a in built["alternatives"]]
    threshold = float(body.get("success_threshold", 0.90))
    # The same rule the decide panel uses, from the same function. This was a
    # local clamp that rounded a non-qualifying precision DOWN to Standard,
    # which contradicts the 2026-08-16 ruling (round up to a tier that can
    # carry the claim) and was a second copy of a decision besides.
    requested_decide_paths = study_paths_for(
        body.get("decide_run_paths", body.get("decide_paths")))
    decide_body = {
        "question": "annuitization",
        "alternatives": alternatives,
        "paths": requested_decide_paths,
        "constraints": [{"kind": "success_threshold",
                         "metric": "lifetime_success",
                         "threshold": threshold}],
    }
    decide = {
        "question": "annuitization",
        "baseline_config": prepared,
        "paths": requested_decide_paths,
        "constraints": decide_body["constraints"],
        "plan": (decide_plan(prepared, decide_body)
                 if alternatives else None),
        "unavailable_reason": (None if alternatives else
                               "no comparable annuity arm was built from "
                               "the supplied quotes"),
    }
    return {
        "alternatives": alternatives,
        "not_compared": built["not_compared"],
        "baseline_is": built["baseline_is"],
        "dropped_from_both_sides": built["dropped_from_both_sides"],
        "comparison_is_partial": bool(built["not_compared"]),
        "paths": paths,
        "precision": PRECISION_BY_PATHS.get(paths),
        "engine_runs_at_most": searches * budget + consumption_runs,
        "searches": searches,
        # So the page can post the identical arms to /api/decide without
        # rebuilding them and drifting from what was priced here.
        "decide_alternatives": alternatives,
        "decide": decide,
    }


def start_annuity_job(cfg, body, seed) -> str:
    """Spending ceilings for the baseline and each arm, then the readings."""
    import guaranteed_income_packet as GIP
    _preflight_config(cfg)         # refusal before the thread, not inside it
    cfg, built = _annuity_context(cfg, body)
    if not built["alternatives"]:
        # Refused here rather than after minutes of computation, and refused
        # rather than returning an empty comparison that looks like a result.
        raise ValueError(
            "no arm can be compared: %s"
            % "; ".join(e["reason"] for e in built["not_compared"]))
    paths = int(body.get("paths", 2_000))
    threshold = float(body.get("success_threshold", 0.90))
    budget = int(body.get("max_evaluations", 10))
    spend_now = float(_get_path(cfg, "state.expenses_y0") or 0.0)
    low = float(body.get("low", spend_now * 0.6 if spend_now else 30_000.0))
    high = float(body.get("high", spend_now * 2.0 if spend_now else 200_000.0))
    tolerance = float(body.get("tolerance", 0.01))
    # `spending_ceiling` refuses an empty bracket -- but it refuses from
    # inside the worker, on the first arm, after the job id exists and the
    # user is watching a progress bar. Nothing about `high > low` needs a
    # path drawn. Asked here in the same words so the two cannot disagree
    # about what "empty" means.
    if not high > low:
        raise ValueError("the bracket is empty: low=%r high=%r" % (low, high))
    jid = _new_job()

    def work():
        try:
            total = 1 + len(built["alternatives"])
            done = [0]

            def ceiling_for(run_cfg, label):
                if _JOBS.get(jid, {}).get("cancelled"):
                    raise _GsCancelled()

                def evaluate(spending):
                    if _JOBS.get(jid, {}).get("cancelled"):
                        raise _GsCancelled()
                    trial = copy.deepcopy(run_cfg)
                    trial.setdefault("state", {})["expenses_y0"] = spending
                    return ENG.summary(trial, paths, seed).get(
                        "lifetime_success")

                out = GIP.spending_ceiling(
                    evaluate, low=low, high=high, threshold=threshold,
                    tolerance=tolerance, max_evaluations=budget)
                done[0] += 1
                _job_set(jid, pct=min(done[0] / total, 0.99),
                         stage="%s (%d/%d)" % (label, done[0], total))
                return out

            def consumption_at(run_cfg):
                """Median consumption at the plan's OWN spending — the metric
                the guardrails cannot flatten."""
                trial = copy.deepcopy(run_cfg)
                trial.setdefault("state", {})["expenses_y0"] = spend_now
                return ENG.summary(trial, paths, seed).get("cons_p50")

            baseline = ceiling_for(cfg, GIP.DO_NOTHING)
            base_cons = consumption_at(cfg)
            readings, ceilings, consumption = [], {GIP.DO_NOTHING: baseline}, []
            for alternative in built["alternatives"]:
                applied = alternative.apply(cfg)
                arm = ceiling_for(applied, alternative.name)
                ceilings[alternative.name] = arm
                readings.append(GIP.license_to_spend(baseline, arm,
                                                     name=alternative.name))
                consumption.append(GIP.consumption_reading(
                    base_cons, consumption_at(applied),
                    name=alternative.name))
            result = GIP.read_packet(
                {"question": str(body.get("question") or
                                 "annuitize, and how much"),
                 "baseline_is": built["baseline_is"],
                 "success_threshold": threshold,
                 "spending_bracket": [low, high],
                 "paths": paths,
                 "precision": PRECISION_BY_PATHS.get(paths),
                 "ceilings": ceilings,
                 "spending_measured_at": spend_now,
                 "dropped_from_both_sides": built["dropped_from_both_sides"]},
                readings, built["not_compared"], consumption)
            _job_set(jid, result=result, done=True, pct=1.0, stage="done")
        except _GsCancelled:
            _job_set(jid, error="cancelled", done=True, stage="cancelled")
        except Exception as exc:              # noqa: BLE001
            traceback.print_exc()
            _job_set(jid, error=_public_error(exc), done=True, stage="error")

    threading.Thread(target=work, daemon=True).start()
    return jid


# ------------------------------------------------------------ /api/frontier
# S2 efficient frontier: an expenses × SWR grid where every cell yields the
# outcome triple (consumption P50, FIRE age P50, lifetime success). Pareto-
# nondominated cells form the frontier; the caller's current position is
# compared against its nearest frontier point. Same job-mode skeleton as
# run_goalseek — deliberately NOT abstracted into a shared grid helper: the
# two loops differ in what they record, and S1 is already verified.





def start_bequest_job(cfg, body, seed) -> str:
    """Does this plan only work because somebody dies? (`OPEN_ITEMS.md` E5.)

    The engine side has been complete and pinned by four tests since the parent
    lifecycle landed; what was missing was any way to reach it. It runs the
    plan twice at ONE seed -- once as configured, once with the bequest not
    credited -- so it costs two full simulations and belongs on the background
    channel with everything else rather than blocking a request.

    Progress is deliberately coarse. `bequest_dependency` runs both halves
    internally and takes no callback, and inventing a smooth-looking bar over
    work this function cannot observe would be a progress indicator that
    reports confidence it does not have. The stage string says what is
    actually happening instead.
    """
    _preflight_config(cfg)         # refusal before the thread, not inside it
    paths = int(body.get("paths", 2_000))
    jid = _new_job()

    def work():
        try:
            _job_set(jid, pct=0.05,
                     stage="running the plan twice at one seed")
            if _JOBS.get(jid, {}).get("cancelled"):
                raise _GsCancelled()
            out = ENG.bequest_dependency(cfg, paths, seed)
            _job_set(jid, result=out, done=True, pct=1.0, stage="done")
        except _GsCancelled:
            _job_set(jid, error="cancelled", done=True, stage="cancelled")
        except Exception as exc:              # noqa: BLE001
            traceback.print_exc()
            _job_set(jid, error=_public_error(exc), done=True, stage="error")

    threading.Thread(target=work, daemon=True).start()
    return jid


def start_career_break_job(cfg, body, seed) -> str:
    """Keep working, or take the planned break (Roadmap 9.0 Phase 3).

    Two complete simulations at one seed, so it belongs on the background
    channel with the other paired comparisons rather than blocking a request.

    `_preflight_config` runs BEFORE the thread and is the whole reason an
    unusable break -- zero years, a start age before the plan begins, a break
    running past the last working year -- is named to the user instead of
    producing a run that quietly models something shorter than what they
    typed. Refusing inside the worker would surface as a failed job.

    Progress is coarse for the same reason it is coarse next door: the
    comparison runs both arms internally and takes no callback, and a smooth
    bar over work this function cannot see would report a confidence it does
    not have.
    """
    _preflight_config(cfg)         # refusal before the thread, not inside it
    paths = int(body.get("paths", 2_000))
    jid = _new_job()

    def work():
        try:
            _job_set(jid, pct=0.05,
                     stage="running the plan with and without the break")
            if _JOBS.get(jid, {}).get("cancelled"):
                raise _GsCancelled()
            out = ENG.career_break_comparison(cfg, paths, seed)
            _job_set(jid, result=out, done=True, pct=1.0, stage="done")
        except _GsCancelled:
            _job_set(jid, error="cancelled", done=True, stage="cancelled")
        except Exception as exc:              # noqa: BLE001
            traceback.print_exc()
            _job_set(jid, error=_public_error(exc), done=True, stage="error")

    threading.Thread(target=work, daemon=True).start()
    return jid


def start_execution_simplification_job(cfg, body, seed) -> str:
    """B4: paired age-80 execution-complexity stress.

    Two complete simulations belong on the background channel.  The transition
    age is intentionally not request-configurable: Sol reviewed age 80, and a
    hidden user-controlled age would be a saved-plan field by another name.
    """
    _preflight_config(cfg)
    paths = max(1, min(int(body.get("paths", 2_000)), 10_000))
    jid = _new_job()

    def work():
        try:
            _job_set(jid, pct=0.05,
                     stage="running paired age-80 execution paths")
            if _JOBS.get(jid, {}).get("cancelled"):
                raise _GsCancelled()
            out = ENG.execution_simplification_stress(
                cfg, paths, seed, transition_age=80)
            _job_set(jid, result=out, done=True, pct=1.0, stage="done")
        except _GsCancelled:
            _job_set(jid, error="cancelled", done=True, stage="cancelled")
        except Exception as exc:              # noqa: BLE001
            traceback.print_exc()
            _job_set(jid, error=_public_error(exc), done=True, stage="error")

    threading.Thread(target=work, daemon=True).start()
    return jid


def start_roth_schedule_job(cfg, body, seed) -> str:
    """Multi-year conversion schedules, priced and left unranked.

    Ten-ish full simulations plus two single-path probes, so it belongs on the
    background channel. There is deliberately no `best` in the result: the
    ruling was to expose the frontier, and a caller that wants one answer has
    to choose which axis it cares about.
    """
    _preflight_config(cfg)         # refusal before the thread, not inside it
    paths = int(body.get("paths", 1_200))
    jid = _new_job()

    def work():
        try:
            _job_set(jid, pct=0.02, stage="pricing conversion schedules")
            if _JOBS.get(jid, {}).get("cancelled"):
                raise _GsCancelled()
            out = RSCH.search(cfg, paths, seed)
            _job_set(jid, result=out, done=True, pct=1.0, stage="done")
        except _GsCancelled:
            _job_set(jid, error="cancelled", done=True, stage="cancelled")
        except Exception as exc:              # noqa: BLE001
            traceback.print_exc()
            _job_set(jid, error=_public_error(exc), done=True, stage="error")

    threading.Thread(target=work, daemon=True).start()
    return jid


def start_asset_location_job(cfg, body, seed) -> str:
    """Three placements of the same portfolio, on one set of paths.

    Three full simulations, so it belongs on the background channel with the
    other multi-run studies rather than blocking a request. Progress is per
    arm, which is real: the comparison genuinely completes one placement at a
    time, so the bar is reporting work that happened rather than interpolating
    over work it cannot see.
    """
    _preflight_config(cfg)         # refusal before the thread, not inside it
    paths = int(body.get("paths", 2_000))
    horizon = int(body.get("horizon", 50))
    jid = _new_job()

    def work():
        try:
            _job_set(jid, pct=0.02, stage="pricing the placements")
            if _JOBS.get(jid, {}).get("cancelled"):
                raise _GsCancelled()
            out = AL.compare_placements(cfg, paths, seed, horizon)
            _job_set(jid, result=out, done=True, pct=1.0, stage="done")
        except _GsCancelled:
            _job_set(jid, error="cancelled", done=True, stage="cancelled")
        except Exception as exc:              # noqa: BLE001
            traceback.print_exc()
            _job_set(jid, error=_public_error(exc), done=True, stage="error")

    threading.Thread(target=work, daemon=True).start()
    return jid


def start_frontier_job(cfg, paths, seed, grid, ranges) -> str:
    _preflight_config(cfg)         # refusal before the thread, not inside it
    jid = _new_job()

    def work():
        try:
            out = run_frontier(
                cfg, paths, seed, grid, ranges,
                cb=lambda p, s: _job_set(jid, pct=float(p), stage=str(s)),
                cancelled=lambda: _JOBS.get(jid, {}).get("cancelled"))
            _job_set(jid, result=out, done=True, pct=1.0, stage="done")
        except _GsCancelled:
            _job_set(jid, error="cancelled", done=True, stage="cancelled")
        except Exception as exc:              # noqa: BLE001
            traceback.print_exc()
            _job_set(jid, error=_public_error(exc), done=True, stage="error")

    threading.Thread(target=work, daemon=True).start()
    return jid


# ---------------------------------------------------------- /api/sensitivity


# -------------------------------------------------------------- /api/backtest


def _open_export(directory: str, stem: str, extension: str):
    """Atomically reserve an export name, adding a suffix on collision."""
    for suffix in range(1000):
        extra = "" if suffix == 0 else f"-{suffix}"
        path = os.path.join(directory, f"{stem}{extra}.{extension}")
        try:
            return open(path, "x", encoding="utf-8"), path
        except FileExistsError:
            continue
    raise RuntimeError("could not reserve a unique export name")


def _graceful_shutdown(httpd):
    global _RECOVERY_MANAGER, _RECOVERY_MANAGER_PATH
    with _RECOVERY_MANAGER_LOCK:
        if _RECOVERY_MANAGER is not None:
            _RECOVERY_MANAGER.close()
            _RECOVERY_MANAGER = None
            _RECOVERY_MANAGER_PATH = None
    try:
        httpd.shutdown()
    finally:
        httpd.server_close()
    # In source mode serve_forever returns and main exits naturally. The frozen
    # app's UI loop is outside this module, so ask the OS for normal termination
    # after sockets/files have been closed instead of bypassing cleanup.
    if getattr(sys, "frozen", False):
        os.kill(os.getpid(), signal.SIGTERM)


class Handler(BaseHTTPRequestHandler):
    server_version = "FIRE/1.0"
    # HTTP/1.1 keep-alive: WKWebView (the native window) reuses connections and
    # — unlike Chrome — surfaces a server-closed stale connection as a fetch
    # "Load failed" instead of retrying. Every response already carries an
    # explicit Content-Length (see _send), which 1.1 keep-alive requires.
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):   # quiet, single-line access log
        sys.stderr.write("  %s\n" % (fmt % args))

    # ---- helpers ----
    def _send(self, code: int, body: bytes, ctype: str, extra_headers=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        if getattr(self, "close_connection", False):
            self.send_header("Connection", "close")
        for key, value in (extra_headers or {}).items():
            self.send_header(str(key), str(value))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj, code: int = 200, headers=None):
        self._send(code, json.dumps(obj, allow_nan=False).encode("utf-8"),
                   "application/json; charset=utf-8", headers)

    def _header_values(self, name: str):
        get_all = getattr(self.headers, "get_all", None)
        values = get_all(name) if get_all else None
        if values is None:
            value = self.headers.get(name)
            values = [] if value is None else [value]
        return [str(value) for value in values]

    def _single_header(self, name: str, status: int):
        values = self._header_values(name)
        if len(values) != 1 or not values[0].strip():
            raise TrustBoundaryError(f"invalid {name} header", status)
        return values[0].strip()

    def _canonical_authority(self):
        port = getattr(getattr(self, "server", None), "server_port", None)
        if not isinstance(port, int) or not 1 <= port <= 65535:
            raise TrustBoundaryError("server authority unavailable", 500)
        return f"127.0.0.1:{port}"

    def _require_host(self):
        # The server binds IPv4 loopback only. Keep one canonical authority so
        # Host and Origin cannot disagree through localhost/IPv6 aliases.
        if self._single_header("Host", 400) != self._canonical_authority():
            raise TrustBoundaryError("invalid Host header", 400)

    def _require_origin(self):
        expected = "http://" + self._canonical_authority()
        if self._single_header("Origin", 403) != expected:
            raise TrustBoundaryError("invalid Origin header", 403)

    def _require_json_content_type(self):
        value = self._single_header("Content-Type", 400)
        if not re.fullmatch(
                r"application/json(?:\s*;\s*charset\s*=\s*(?:utf-8|\"utf-8\"))?",
                value, flags=re.IGNORECASE):
            raise TrustBoundaryError("JSON Content-Type required", 415)

    def _require_framing(self):
        # BaseHTTPRequestHandler does not provide a safe body-framing
        # contract for this surface. Reject transfer coding and duplicate or
        # malformed lengths before touching rfile.
        if self._header_values("Transfer-Encoding"):
            raise TrustBoundaryError("Transfer-Encoding is not supported", 400)
        lengths = self._header_values("Content-Length")
        if len(lengths) != 1 or not re.fullmatch(r"[0-9]+", lengths[0].strip()):
            raise TrustBoundaryError("invalid Content-Length header", 400)

    def _require_capability(self):
        expected = getattr(getattr(self, "server", None),
                           "fire_capability", None)
        if not isinstance(expected, str) or not expected:
            raise TrustBoundaryError("server capability unavailable", 500)
        values = self._header_values("X-FIRE-Capability")
        valid = (len(values) == 1 and values[0].isascii()
                 and secrets.compare_digest(values[0], expected))
        if not valid:
            raise TrustBoundaryError("invalid FIRE capability", 403)

    def _require_idempotency_key(self, body):
        """Every object-creating §6 POST states its own request identity.

        The header is mandatory and must equal `request_id` in the body.  Two
        copies of the same value is not redundancy: the header is what makes the
        identity visible to anything sitting in front of the seam, and the body
        copy is what the signed request fingerprint covers.  Letting them differ
        would mean the value that was authenticated and the value that was
        deduplicated on could be two different strings.

        Requiring it also forces the caller to *state* when it intends a new
        action.  Without a header the server can only guess from the body, and
        the guess it used to make — hash the epoch in — silently created a twin
        for anyone who retried after resynchronising.
        """
        header = self._single_header("Idempotency-Key", 400)
        if not isinstance(body, dict) or body.get("request_id") != header:
            raise TrustBoundaryError(
                "Idempotency-Key must equal the body request_id", 400)

    def _require_mutation_boundary(self):
        self._require_host()
        self._require_origin()
        self._require_json_content_type()
        self._require_capability()
        self._require_framing()

    def _reject_boundary(self, exc: TrustBoundaryError):
        # The request body has deliberately not been consumed. Close the
        # HTTP/1.1 connection so an attacker-controlled body cannot become the
        # next request on a keep-alive socket.
        self.close_connection = True
        return self._json({"error": str(exc)}, exc.status)

    def _read_body(self) -> dict:
        raw = self.headers.get("Content-Length", 0) or 0
        try:
            n = int(raw)
        except (TypeError, ValueError):
            raise ValueError("invalid Content-Length") from None
        if n > MAX_REQUEST_BYTES:
            self.close_connection = True
            raise RequestTooLarge("request body too large")
        if n < 0:
            raise ValueError("invalid Content-Length")
        if n <= 0:
            return {}
        try:
            body = json.loads(
                self.rfile.read(n).decode("utf-8"),
                parse_constant=_reject_json_constant,
                object_pairs_hook=_reject_duplicate_json_pairs)
        except RecursionError:
            raise ValueError("JSON nesting too deep") from None
        if not isinstance(body, dict):
            raise ValueError("request body must be a JSON object")
        return body

    # ---- routing ----
    def do_GET(self):
        path = self.path.split("?", 1)[0]
        try:
            self._require_host()
        except TrustBoundaryError as exc:
            return self._reject_boundary(exc)
        if path == "/api/capability":
            token = getattr(getattr(self, "server", None),
                            "fire_capability", None)
            if not isinstance(token, str) or not token:
                return self._json({"error": "server capability unavailable"}, 500)
            return self._json({"capability": token})
        if path == "/api/migration/authority":
            try:
                return self._json(_formal_migration_manager().authority())
            except RECOVERY.ManualRecoveryRequired as exc:
                # Carries the authority payload like every other refusal. It
                # did not, and the browser therefore had a latch it could name
                # but no authority to render — a mixed-seam latch (this seam
                # says 423, the §6 read is unreachable) left the user looking
                # at no banner at all.
                return self._json({"error": str(exc),
                                   "code": "manual_recovery_required",
                                   **_latched_authority_payload()}, 409)
            except FORMAL_MIGRATION.FormalMigrationConflict as exc:
                return self._json({"error": str(exc),
                                   "code": "migration_conflict",
                                   "reason_code": exc.reason_code}, 409)
            except FORMAL_MIGRATION.FormalMigrationError as exc:
                return self._json({"error": str(exc),
                                   "code": "migration_failed",
                                   "reason_code": exc.reason_code}, 400)
            except RECOVERY.RecoveryError as exc:
                return self._json({"error": _public_error(exc),
                                   "code": "recovery_failed"}, 409)
        if path == "/api/storage/working-draft":
            # Read half of the side-store; see the POST branch for why this is
            # outside §6. It answers without a receipt and without opening the
            # control journal, so a latched archive still returns the draft.
            return self._json({
                "format": WORKING_DRAFT.FORMAT,
                "draft": WORKING_DRAFT.read(_persistence_database_path())})
        if path in ("/api/storage/state", "/api/storage/plans",
                    "/api/storage/recovered-drafts"):
            # §6's post-cutover read seam.  All three are pure: no observation
            # is recorded, no generation allocated, no archive byte touched.
            # `state` answers without a proof because startup must be able to
            # ask who is authoritative before it can hold a receipt; `plans`
            # and `recovered-drafts` require the exact receipt so a stale tab
            # is refused a read rather than quietly served fresher rows.
            try:
                seam = STORAGE.StorageSeam(_recovery_manager())
                if path == "/api/storage/state":
                    return self._json(seam.state())
                reader = (seam.recovered_drafts
                          if path == "/api/storage/recovered-drafts"
                          else seam.plans)
                return self._json(reader(
                    authority_receipt=self.headers.get("X-FIRE-Authority-Receipt"),
                    legacy_digest=self.headers.get("X-FIRE-Legacy-Digest")))
            except STORAGE.StorageError as exc:
                return self._json({"error": str(exc), "code": exc.code,
                                   **exc.payload}, exc.http_status)
            except RECOVERY.ManualRecoveryRequired as exc:
                return self._json({"error": str(exc),
                                   "code": "manual_recovery_required",
                                   **_latched_authority_payload()}, 423)
            except RECOVERY.RecoveryError as exc:
                return self._json({"error": _public_error(exc),
                                   "code": "recovery_failed"}, 409)
        if path == "/api/storage/parent-identities":
            plan_id = (parse_qs(urlparse(self.path).query).get("plan_id")
                       or [""])[0]
            try:
                return self._json(
                    STORAGE.StorageSeam(_recovery_manager()).parent_identities(
                        plan_id,
                        authority_receipt=self.headers.get(
                            "X-FIRE-Authority-Receipt"),
                        legacy_digest=self.headers.get(
                            "X-FIRE-Legacy-Digest")))
            except STORAGE.StorageError as exc:
                return self._json({"error": str(exc), "code": exc.code,
                                   **exc.payload}, exc.http_status)
            except RECOVERY.ManualRecoveryRequired as exc:
                return self._json({"error": str(exc),
                                   "code": "manual_recovery_required",
                                   **_latched_authority_payload()}, 423)
            except RECOVERY.RecoveryError as exc:
                return self._json({"error": _public_error(exc),
                                   "code": "recovery_failed"}, 409)
        if path == "/api/guardrail/status":
            # Phase 4's home-page light, read from the plan's real check-in
            # history. Deliberately GET and deliberately cheap: it runs no
            # engine, because a status the home page cannot render instantly
            # is a status the home page will not render.
            plan_id = (parse_qs(urlparse(self.path).query).get("plan_id")
                       or [""])[0]
            try:
                import guardrail_seam as GSEAM
                seam = _checkin_seam()
                history = seam.history(plan_id).get("checkins") or []
                forecasts = seam.forecasts(plan_id).get("forecasts") or []
                # The join is an ordinal, not a key: a check-in stores dates,
                # a forecast stores a per-age curve. See
                # guardrail_seam.expected_from_forecasts.
                live = [row for row in history
                        if not row.get("supersedes_checkin_id")]
                expected = GSEAM.expected_from_forecasts(forecasts, len(live))
                return self._json(GSEAM.status_from_history(history, expected))
            except CHECKIN.CheckinError as exc:
                return self._json({"error": str(exc), "code": exc.code},
                                  exc.http_status)
        if path in ("/api/checkin/history", "/api/checkin/forecasts",
                    "/api/checkin/standing"):
            plan_id = (parse_qs(urlparse(self.path).query).get("plan_id")
                       or [""])[0]
            try:
                seam = _checkin_seam()
                if path == "/api/checkin/history":
                    return self._json(seam.history(plan_id))
                if path == "/api/checkin/standing":
                    return self._json(seam.standing(plan_id))
                return self._json(seam.forecasts(plan_id))
            except CHECKIN.CheckinError as exc:
                return self._json({"error": str(exc), "code": exc.code},
                                  exc.http_status)
        if path == "/api/decision/review":
            # Pure, and deliberately a GET: reviewing a decision changes
            # nothing about it. `as_of` is passed in rather than read from
            # the clock here so the answer is a function of its inputs --
            # the same reason `set_choice_state` takes its timestamp from
            # the caller.
            query = parse_qs(urlparse(self.path).query)
            try:
                return self._json(DECISION_REVIEW.review(
                    _archive_store(), (query.get("plan_id") or [""])[0],
                    as_of=(query.get("as_of") or
                           [utc_now()])[0]))
            except DECISION_REVIEW.DecisionReviewError as exc:
                return self._json({"error": str(exc), "code": exc.code},
                                  exc.http_status)
        if path in ("/api/decision/archive/list", "/api/decision/archive/get"):
            # Both pure. `list` answers with an empty list rather than a
            # refusal when the archive has no decision record yet: a user who
            # has never archived a decision has no decisions, which is not an
            # error condition.
            query = parse_qs(urlparse(self.path).query)
            try:
                seam = _decision_archive_seam()
                if path == "/api/decision/archive/list":
                    return self._json(
                        seam.history((query.get("plan_id") or [""])[0]))
                return self._json(
                    seam.get((query.get("packet_id") or [""])[0]))
            except DECISION_ARCHIVE.DecisionArchiveError as exc:
                return self._json({"error": str(exc), "code": exc.code},
                                  exc.http_status)
        if path == "/api/presets":
            return self._json({
                "presets": PRESETS_MOD.PRESETS,
                "rule_pack": ENG.current_rule_pack(ENG.default_config()),
                "rule_pack_defaults": ENG.rule_pack_reference_defaults(),
            })
        if path == "/api/logs":
            lp = os.path.expanduser("~/Library/Logs/FIRE-Modeling.log")
            try:
                with open(lp, encoding="utf-8", errors="ignore") as f:
                    tail = f.readlines()[-300:]
                return self._json({"path": "~/Library/Logs/FIRE-Modeling.log",
                                   "lines": [_scrub_api_text(l.rstrip()) for l in tail]})
            except FileNotFoundError:
                return self._json({"path": "~/Library/Logs/FIRE-Modeling.log",
                                   "lines": ["(dev mode: logs go to the terminal, not a file)"]})
        if path == "/api/timeline":
            query = parse_qs(urlparse(self.path).query, keep_blank_values=True)
            refs = query.get("plan_id") or []
            if len(refs) != 1:
                return self._json({"error": "plan_id is required"}, 400)
            try:
                plan_id = _validate_archive_ref(refs[0], "plan_id", "plan")
            except ValueError as exc:
                return self._json({"error": str(exc)}, 400)
            db_path = _persistence_database_path()
            try:
                timeline = read_timeline(db_path, plan_id)
            except (FileNotFoundError, PlanNotFoundError):
                return self._json({"error": "plan not found"}, 404)
            except PersistenceError:
                return self._json({"error": "timeline unavailable"}, 500)
            return self._json({"plan_id": plan_id,
                               "timeline_protocol": TIMELINE_PROTOCOL_VERSION,
                               "timeline": timeline})
        if path == "/api/progress":
            with _JOBS_LOCK:
                j = _JOBS.get(_query_job(self.path))
            if not j:
                return self._json({"error": "unknown job"}, 404)
            return self._json({"pct": j["pct"], "stage": j["stage"],
                               "done": j["done"], "error": j["error"]})
        if path == "/api/result":
            with _JOBS_LOCK:
                j = _JOBS.get(_query_job(self.path))
            if not j:
                return self._json({"error": "unknown job"}, 404)
            if not j["done"]:
                return self._json({"error": "not ready"}, 409)
            if j["error"]:
                return self._json({"error": j["error"]}, 500)
            return self._json(j["result"])
        if path == "/api/cancel":
            return self._json({"error": "cancel requires POST"}, 405,
                              headers={"Allow": "POST"})
        return self._serve_static(path)

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        try:
            self._require_mutation_boundary()
            body = self._read_body()
            if path in _IDEMPOTENT_POST_PATHS:
                # Checked here, in the boundary phase, because it is a property
                # of the request rather than of the seam behind it — and because
                # this is the only frame that turns a TrustBoundaryError into its
                # intended status instead of a 500.
                self._require_idempotency_key(body)
        except TrustBoundaryError as exc:
            return self._reject_boundary(exc)
        except RequestTooLarge as exc:
            return self._json({"error": str(exc)}, 413)
        except Exception as exc:
            return self._json({"error": f"bad request body: {_public_error(exc)}"}, 400)
        try:
            if path == "/api/cancel":
                with _JOBS_LOCK:
                    j = _JOBS.get(str(body.get("job") or ""))
                    if not j:
                        return self._json({"ok": False, "accepted": False,
                                           "status": "unknown_job"})
                    if j.get("done"):
                        return self._json({"ok": False, "accepted": False,
                                           "status": "done"})
                    if j.get("commit_started"):
                        return self._json({"ok": False, "accepted": False,
                                           "status": "already_committing"})
                    j["cancelled"] = True
                return self._json({"ok": True, "accepted": True,
                                   "status": "accepted"})
            if path == "/api/migration/preview":
                if set(body) not in ({"envelope"}, {"envelope", "retry_nonce"}):
                    return self._json({
                        "error": "invalid formal migration preview request",
                        "kind": "request",
                        "reason_code": "request_fields_invalid",
                    }, 400)
                try:
                    return self._json(_formal_migration_manager().preview(
                        body["envelope"], retry_nonce=body.get("retry_nonce")))
                except FORMAL_MIGRATION.FormalMigrationConflict as exc:
                    return self._json({"error": str(exc),
                                       "code": "migration_conflict",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 409)
                except RECOVERY.ManualRecoveryRequired as exc:
                    return self._json({"error": str(exc),
                                       "code": "manual_recovery_required"}, 409)
                except FORMAL_MIGRATION.FormalEnvelopeError as exc:
                    return self._json({"error": str(exc),
                                       "kind": "envelope",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 400)
                except FORMAL_MIGRATION.FormalMigrationError as exc:
                    return self._json({"error": str(exc),
                                       "code": "migration_failed",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 400)
                except RECOVERY.RecoveryError as exc:
                    return self._json({"error": _public_error(exc),
                                       "code": "recovery_failed"}, 400)
            if path == "/api/migration/stage":
                if set(body) != {"operation_id", "envelope"}:
                    return self._json({
                        "error": "operation_id and envelope are required",
                        "kind": "request",
                        "reason_code": "request_fields_invalid",
                    }, 400)
                try:
                    return self._json(_formal_migration_manager().stage(
                        body["operation_id"], body["envelope"]))
                except FORMAL_MIGRATION.FormalMigrationConflict as exc:
                    return self._json({"error": str(exc),
                                       "code": "migration_conflict",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 409)
                except RECOVERY.ManualRecoveryRequired as exc:
                    return self._json({"error": str(exc),
                                       "code": "manual_recovery_required"}, 409)
                except FORMAL_MIGRATION.FormalEnvelopeError as exc:
                    return self._json({"error": str(exc),
                                       "kind": "envelope",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 400)
                except FORMAL_MIGRATION.FormalMigrationError as exc:
                    return self._json({"error": str(exc),
                                       "code": "migration_failed",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 400)
                except RECOVERY.RecoveryError as exc:
                    return self._json({"error": _public_error(exc),
                                       "code": "recovery_failed"}, 400)
            if path == "/api/migration/import":
                if set(body) != {"operation_id", "envelope"}:
                    return self._json({
                        "error": "operation_id and envelope are required",
                        "kind": "request",
                        "reason_code": "request_fields_invalid",
                    }, 400)
                try:
                    return self._json(_formal_migration_manager().import_operation(
                        body["operation_id"], body["envelope"]))
                except FORMAL_MIGRATION.FormalMigrationConflict as exc:
                    return self._json({"error": str(exc),
                                       "code": "migration_conflict",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 409)
                except RECOVERY.ManualRecoveryRequired as exc:
                    return self._json({"error": str(exc),
                                       "code": "manual_recovery_required"}, 409)
                except FORMAL_MIGRATION.FormalEnvelopeError as exc:
                    return self._json({"error": str(exc),
                                       "kind": "envelope",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 400)
                except FORMAL_MIGRATION.FormalMigrationError as exc:
                    return self._json({"error": str(exc),
                                       "code": "migration_failed",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 400)
                except RECOVERY.RecoveryError as exc:
                    return self._json({"error": _public_error(exc),
                                       "code": "recovery_failed"}, 400)
            if path == "/api/migration/verify":
                required = {"operation_id", "envelope", "page_instance_id"}
                if set(body) != required:
                    return self._json({
                        "error": "operation_id, envelope, and page identity are required",
                        "kind": "request",
                        "reason_code": "request_fields_invalid",
                    }, 400)
                try:
                    return self._json(_formal_migration_manager().verify_operation(
                        body["operation_id"], body["envelope"],
                        page_instance_id=body["page_instance_id"]))
                except FORMAL_MIGRATION.FormalMigrationConflict as exc:
                    return self._json({"error": str(exc),
                                       "code": "migration_conflict",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 409)
                except RECOVERY.ManualRecoveryRequired as exc:
                    return self._json({"error": str(exc),
                                       "code": "manual_recovery_required"}, 409)
                except FORMAL_MIGRATION.FormalEnvelopeError as exc:
                    return self._json({"error": str(exc),
                                       "kind": "envelope",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 400)
                except FORMAL_MIGRATION.FormalMigrationError as exc:
                    return self._json({"error": str(exc),
                                       "code": "migration_failed",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 400)
                except RECOVERY.RecoveryError as exc:
                    return self._json({"error": _public_error(exc),
                                       "code": "recovery_failed"}, 400)
            if path in ("/api/storage/plan", "/api/storage/plan-version",
                        "/api/storage/plan-duplicate", "/api/storage/plan-status",
                        "/api/storage/draft",
                        "/api/storage/parent-identity-link",
                        "/api/storage/parent-identity-evaluation",
                        "/api/storage/parent-identity-link-end"):
                seam_method = {
                    "/api/storage/plan": "create_plan",
                    "/api/storage/plan-version": "create_plan_version",
                    "/api/storage/plan-duplicate": "duplicate_plan",
                    "/api/storage/plan-status": "set_plan_status",
                    "/api/storage/draft": "save_draft",
                    "/api/storage/parent-identity-link":
                        "create_parent_identity_link",
                    "/api/storage/parent-identity-evaluation":
                        "evaluate_parent_identity",
                    "/api/storage/parent-identity-link-end":
                        "end_parent_identity_link",
                }[path]
                try:
                    return self._json(
                        getattr(STORAGE.StorageSeam(_recovery_manager()),
                                seam_method)(
                            body,
                            close_store=_close_archive_store_for_recovery,
                            reopen_store=_reopen_archive_store_after_recovery))
                except STORAGE.StorageError as exc:
                    return self._json({"error": str(exc), "code": exc.code,
                                       **exc.payload}, exc.http_status)
                except RECOVERY.ManualRecoveryRequired as exc:
                    return self._json({"error": str(exc),
                                       "code": "manual_recovery_required",
                                       **_latched_authority_payload()}, 423)
                except RECOVERY.RecoveryConflict as exc:
                    return self._json({"error": str(exc),
                                       "code": "idempotency_conflict"}, 409)
                except RECOVERY.RecoveryError as exc:
                    return self._json({"error": _public_error(exc),
                                       "code": "recovery_failed"}, 400)
            if path == "/api/storage/working-draft":
                # NOT a §6 seam. The working draft is unsaved input, not
                # authoritative data: it never touches the archive, allocates no
                # generation, appends no journal row, and therefore carries no
                # authority receipt. That is also why it must keep working while
                # the archive is latched or `source_changed` — refusing here
                # would throw away what the user is typing at the one moment
                # they most need time (user decision, 2026-07-27).
                if set(body) != {"draft"}:
                    return self._json({"error": "draft is required",
                                       "code": "invalid_request"}, 400)
                try:
                    if body["draft"] is None:
                        WORKING_DRAFT.clear(_persistence_database_path())
                    else:
                        WORKING_DRAFT.write(_persistence_database_path(),
                                            body["draft"])
                except WORKING_DRAFT.WorkingDraftError as exc:
                    # A refused draft save has to be visible. Reporting success
                    # for a write that did not land is the failure mode this
                    # whole slice exists to remove.
                    return self._json({"error": str(exc),
                                       "code": "cost_or_storage_unavailable"},
                                      503)
                return self._json({"format": WORKING_DRAFT.FORMAT, "ok": True})
            if path == "/api/storage/observe":
                # The only call that can move authority off sqlite_preferred.
                # Kept an explicit POST so that no read ever has that power.
                try:
                    return self._json(
                        STORAGE.StorageSeam(_recovery_manager()).observe(body))
                except STORAGE.StorageError as exc:
                    return self._json({"error": str(exc), "code": exc.code,
                                       **exc.payload}, exc.http_status)
                except RECOVERY.ManualRecoveryRequired as exc:
                    return self._json({"error": str(exc),
                                       "code": "manual_recovery_required",
                                       **_latched_authority_payload()}, 423)
                except RECOVERY.RecoveryConflict as exc:
                    return self._json({"error": str(exc),
                                       "code": "recovery_conflict"}, 409)
                except RECOVERY.RecoveryError as exc:
                    return self._json({"error": _public_error(exc),
                                       "code": "recovery_failed"}, 400)
            if path == "/api/migration/finalize":
                # The one irreversible route in the formal migration surface.
                # It hands the archive store's close/reopen callbacks through
                # because the live database file is replaced underneath it,
                # exactly as the restore routes do.
                required = {"operation_id", "envelope", "page_instance_id",
                            "legacy_fence_id", "legacy_fence_digest"}
                if set(body) != required:
                    return self._json({
                        "error": "operation_id, envelope, page identity, and "
                                 "fence are required",
                        "kind": "request",
                        "reason_code": "request_fields_invalid",
                    }, 400)
                try:
                    return self._json(_formal_migration_manager().finalize(
                        body["operation_id"], body["envelope"],
                        legacy_fence_id=body["legacy_fence_id"],
                        legacy_fence_digest=body["legacy_fence_digest"],
                        page_instance_id=body["page_instance_id"],
                        close_store=_close_archive_store_for_recovery,
                        reopen_store=_reopen_archive_store_after_recovery))
                except FORMAL_MIGRATION.FormalMigrationConflict as exc:
                    return self._json({"error": str(exc),
                                       "code": "migration_conflict",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 409)
                except RECOVERY.RecoveryConflict as exc:
                    return self._json({"error": str(exc),
                                       "code": "recovery_conflict"}, 409)
                except RECOVERY.ManualRecoveryRequired as exc:
                    return self._json({"error": str(exc),
                                       "code": "manual_recovery_required"}, 409)
                except FORMAL_MIGRATION.FormalEnvelopeError as exc:
                    return self._json({"error": str(exc),
                                       "kind": "envelope",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 400)
                except FORMAL_MIGRATION.FormalMigrationError as exc:
                    return self._json({"error": str(exc),
                                       "code": "migration_failed",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 400)
                except RECOVERY.RecoveryError as exc:
                    return self._json({"error": _public_error(exc),
                                       "code": "recovery_failed"}, 400)
            if path in ("/api/migration/shadow_preview",
                        "/api/migration/shadow_stage"):
                # This route is deliberately dispatched before the generic
                # config/seed parsing below.  It receives raw browser storage,
                # not an engine config, and never changes localStorage.
                if set(body) != {"envelope"}:
                    return self._json({
                        "error": "invalid migration request",
                        "kind": "request",
                        "reason_code": "request_fields_invalid",
                    }, 400)
                try:
                    projection = MIGRATION.project_envelope(
                        body.get("envelope"),
                        normalizer=lambda value: _normalize_persistence_config(
                            value, default_factory=ENG.default_config),
                        default_factory=ENG.default_config)
                except MIGRATION.MigrationEnvelopeError as exc:
                    return self._json({
                        "error": "invalid migration envelope",
                        "kind": "envelope",
                        "reason_code": exc.reason_code,
                        "source_pointer": exc.pointer,
                    }, 400)
                response = MIGRATION.public_projection(projection)
                if path == "/api/migration/shadow_preview":
                    # Preview is a pure projection: no backup, SQLite, or
                    # localStorage side effect is allowed here.
                    return self._json(response)
                try:
                    response["backup_status"] = MIGRATION.persist_raw_backup(
                        projection["_canonical_envelope"],
                        directory=MIGRATION_SHADOW_DIR)
                except MIGRATION.ShadowBackupError as exc:
                    return self._json({
                        "error": "shadow backup failed",
                        "kind": "backup",
                        "reason_code": exc.reason_code,
                        "envelope_sha256": projection["envelope_sha256"],
                    }, 500)
                return self._json(response)
            if path in ("/api/backup/prepare", "/api/backup/finalize",
                        "/api/restore/prepare", "/api/restore/commit",
                        "/api/restore/raw-prepare", "/api/restore/raw-finalize",
                        "/api/recovery/resolve"):
                manager = _recovery_manager()
                try:
                    if path == "/api/backup/prepare":
                        if set(body) != {"envelope"}:
                            return self._json({"error": "envelope is required"}, 400)
                        projection = MIGRATION.project_envelope(
                            body["envelope"],
                            normalizer=lambda value: _normalize_persistence_config(
                                value, default_factory=ENG.default_config),
                            default_factory=ENG.default_config)
                        return self._json(manager.prepare_backup(
                            body["envelope"], projection=projection))
                    if path == "/api/backup/finalize":
                        if set(body) != {"operation_id", "envelope"}:
                            return self._json({"error": "operation_id and envelope are required"}, 400)
                        return self._json(manager.finalize_backup(
                            body["operation_id"], body["envelope"]))
                    if path == "/api/restore/prepare":
                        if set(body) != {"backup_id"}:
                            return self._json({"error": "backup_id is required"}, 400)
                        return self._json(manager.prepare_restore(body["backup_id"]))
                    if path == "/api/restore/raw-prepare":
                        if set(body) != {"backup_id", "current_envelope"}:
                            return self._json({
                                "error": "backup_id and current_envelope are required"
                            }, 400)
                        return self._json(manager.prepare_raw_restore(
                            body["backup_id"], body["current_envelope"]))
                    if path == "/api/restore/raw-finalize":
                        if set(body) != {"operation_id", "readback_envelope"}:
                            return self._json({
                                "error": "operation_id and readback_envelope are required"
                            }, 400)
                        return self._json(manager.finalize_raw_restore(
                            body["operation_id"], body["readback_envelope"]))
                    if path == "/api/recovery/resolve":
                        if set(body) != {"operation_id", "artifact",
                                         "expected_generation"}:
                            return self._json({
                                "error": "operation_id, artifact, and expected_generation are required"
                            }, 400)
                        return self._json(manager.resolve_manual(
                            body["operation_id"], artifact=body["artifact"],
                            expected_generation=body["expected_generation"],
                            close_store=_close_archive_store_for_recovery,
                            reopen_store=_reopen_archive_store_after_recovery))
                    if set(body) != {"operation_id"}:
                        return self._json({"error": "operation_id is required"}, 400)
                    return self._json(manager.commit_restore(
                        body["operation_id"],
                        close_store=_close_archive_store_for_recovery,
                        reopen_store=_reopen_archive_store_after_recovery))
                except MIGRATION.MigrationEnvelopeError as exc:
                    return self._json({"error": "invalid recovery envelope",
                                       "reason_code": exc.reason_code,
                                       "source_pointer": exc.pointer}, 400)
                except RECOVERY.RecoveryConflict as exc:
                    return self._json({"error": str(exc), "code": "recovery_conflict"}, 409)
                except RECOVERY.ManualRecoveryRequired as exc:
                    return self._json({"error": str(exc),
                                       "code": "manual_recovery_required"}, 409)
                except RECOVERY.RecoveryError as exc:
                    return self._json({"error": _public_error(exc),
                                       "code": "recovery_failed"}, 400)
            cfg = body.get("config") or {}
            seed = int(body.get("seed", 96000))
            if path in _SYNC_ENGINE_PREFLIGHT_ROUTES:
                _preflight_config(cfg)
            if path == "/api/checkin/counterfactual_start":
                # Turns the model-update line from `unknown` into a number, by
                # re-running the archived plan under the current build. It goes
                # through the SAME job path a normal run uses -- progress,
                # cancellation, idempotency, archive ownership and the snapshot
                # commit are solved there, and a second copy would be a second
                # set of bugs. The plan below is entirely pinned from the
                # archive; the only thing that differs is the build.
                try:
                    plan = _checkin_seam().counterfactual_plan(
                        str(body.get("forecast_snapshot_id") or ""))
                except CHECKIN.CheckinError as exc:
                    return self._json({"error": str(exc), "code": exc.code},
                                      exc.http_status)
                writer = _archive_writer()
                # Ask whether the archive will accept a write BEFORE minting a
                # job id, exactly as the `/api/run_start` archive branch does.
                # Without this the refusal still happens -- it is the first
                # statement inside the writer -- but it happens inside the
                # worker thread, where `start_run_job`'s blanket handler turns
                # it into a job error. The user is shown "re-running... 0%",
                # waits, and then gets a message with no code and no status
                # for a condition that was knowable before they were shown a
                # progress bar at all. That is the failure this project's
                # first rule exists to prevent, and the sibling route was
                # already doing it right.
                if writer is not None:
                    writer.require_writable()
                jid = start_run_job(
                    plan["config"], plan["paths"], plan["seed"],
                    plan["dist_paths"], store=_archive_store(),
                    precision=plan["precision"], plan_id=plan["plan_id"],
                    plan_version_id=plan["plan_version_id"], archive=True,
                    request_id=plan["request_id"], writer=writer)
                return self._json({"job": jid,
                                   "old_snapshot_id": plan["old_snapshot_id"],
                                   "engine_build_id": plan["engine_build_id"]})
            if path in ("/api/checkin/record", "/api/checkin/attribute"):
                # Phase 2. `record` is an archive_write; `attribute` is pure.
                # Both refuse far more often than they answer, which is the
                # protocol's design: an unattributable period is reported as
                # such rather than decomposed into plausible numbers.
                try:
                    seam = _checkin_seam()
                    if path == "/api/checkin/record":
                        return self._json(seam.record(body))
                    return self._json(seam.attribute(body))
                except CHECKIN.CheckinError as exc:
                    return self._json({"error": str(exc), "code": exc.code,
                                       **exc.payload}, exc.http_status)
                except RECOVERY.ManualRecoveryRequired as exc:
                    return self._json({"error": str(exc),
                                       "code": "manual_recovery_required",
                                       **_latched_authority_payload()}, 423)
                except RECOVERY.RecoveryConflict as exc:
                    return self._json({"error": str(exc),
                                       "code": "recovery_conflict"}, 409)
                except RECOVERY.RecoveryError as exc:
                    return self._json({"error": _public_error(exc),
                                       "code": "recovery_failed"}, 400)
            if path in ("/api/decision/archive",
                        "/api/decision/archive/state"):
                # Phase 4. Both are archive writes: a decision record and a
                # decision are durable things, which is the whole point of
                # the slice -- before this, a packet died with the process
                # and there was nothing for next year's review to review.
                try:
                    seam = _decision_archive_seam()
                    if path == "/api/decision/archive":
                        return self._json(seam.save(body))
                    return self._json(seam.set_state(body))
                except DECISION_ARCHIVE.DecisionArchiveError as exc:
                    return self._json({"error": str(exc), "code": exc.code},
                                      exc.http_status)
                except RECOVERY.ManualRecoveryRequired as exc:
                    return self._json({"error": str(exc),
                                       "code": "manual_recovery_required",
                                       **_latched_authority_payload()}, 423)
                except RECOVERY.RecoveryConflict as exc:
                    return self._json({"error": str(exc),
                                       "code": "recovery_conflict"}, 409)
                except RECOVERY.RecoveryError as exc:
                    return self._json({"error": _public_error(exc),
                                       "code": "recovery_failed"}, 400)
            if path == "/api/run_start":
                archive = body.get("archive", False)
                if not isinstance(archive, bool):
                    return self._json({"error": "archive must be a boolean"}, 400)
                request_id = body.get("request_id")
                if not archive and request_id is not None:
                    return self._json({
                        "error": "request_id is supported only for archive runs",
                        "code": "request_id_requires_archive",
                    }, 400)
                if archive:
                    try:
                        request_id = validate_request_id(request_id)
                    except PersistenceError as exc:
                        return self._json({
                            "error": str(exc),
                            "code": "request_id_invalid",
                        }, 400)
                raw_paths = body.get("paths", 10000)
                if archive and (isinstance(raw_paths, bool)
                                or not isinstance(raw_paths, int)):
                    return self._json({
                        "error": "archive paths must be an integer",
                        "code": "archive_paths_must_be_integer",
                    }, 400)
                requested_paths = int(raw_paths)
                if archive:
                    # The archive tier is server-derived from exact paths.  A
                    # client cannot label a Quick/Deep/test run as formal.
                    precision = PRECISION_BY_PATHS.get(requested_paths)
                    if precision not in ARCHIVE_PRECISIONS:
                        return self._json({
                            "error": "archive is supported only for Standard or Official paths",
                            "code": "archive_precision_required",
                        }, 400)
                    try:
                        plan_id = _validate_archive_ref(
                            body.get("plan_id"), "plan_id", "plan")
                        plan_version_id = _validate_archive_ref(
                            body.get("plan_version_id"), "plan_version_id", "ver")
                    except ValueError as exc:
                        return self._json({"error": str(exc),
                                           "code": "archive_lineage_invalid"}, 400)
                    if plan_version_id is not None and plan_id is None:
                        return self._json({
                            "error": "plan_id is required with plan_version_id",
                            "code": "archive_lineage_incomplete",
                        }, 400)
                    # Refuse before anything durable happens. The states that
                    # forbid a §6 write forbid a formal run for the same reasons,
                    # and this has to be asked *here* rather than discovered
                    # partway through: under drift the old path returned 200 and
                    # left behind a Plan, a Version, a request and an attempt in
                    # an archive nobody had agreed was authoritative.
                    try:
                        writer = _archive_writer()
                        if writer is not None:
                            writer.require_writable()
                    except ARCHIVE_SEAM.ArchiveWriteRefused as exc:
                        return self._json({"error": str(exc),
                                           "code": exc.code}, exc.http_status)
                    except (PersistenceError, RECOVERY.RecoveryError):
                        return self._json({
                            "error": "archive store unavailable",
                            "code": "archive_store_unavailable",
                        }, 503)
                    try:
                        store = _archive_store()
                    except (PersistenceError, RECOVERY.RecoveryError):
                        return self._json({
                            "error": "archive store unavailable",
                            "code": "archive_store_unavailable",
                        }, 503)
                    try:
                        store.validate_archive_lineage(
                            plan_id=plan_id, plan_version_id=plan_version_id,
                            source_config=cfg, default_factory=ENG.default_config)
                    except PersistenceError:
                        return self._json({"error": "invalid plan reference",
                                           "code": "archive_lineage_invalid"}, 400)
                    try:
                        jid = start_run_job(
                            cfg, requested_paths, seed, body.get("dist_paths"),
                            store=store, precision=precision, plan_id=plan_id,
                            plan_version_id=plan_version_id, archive=True,
                            request_id=request_id, writer=writer)
                    except IdempotencyConflictError:
                        return self._json({
                            "error": "request_id conflicts with a different archive request",
                            "code": "request_id_conflict",
                        }, 409)
                    with _JOBS_LOCK:
                        archive_context = copy.deepcopy(
                            _JOBS.get(jid, {}).get("archive"))
                    response = {"job": jid, "archive": archive_context}
                    if request_id is not None:
                        response["request_id"] = request_id
                    return self._json(response)
                precision = (body.get("precision") or
                             PRECISION_BY_PATHS.get(requested_paths, "standard"))
                jid = start_run_job(cfg, requested_paths, seed,
                                    body.get("dist_paths"),
                                    precision=precision)
                return self._json({"job": jid})
            if path == "/api/roth_opt":
                # E2: Roth-conversion fixed-amount grid comparison. Forces the TRUE tax
                # engine (bracket interactions are the whole point) and sweeps
                # the annual conversion amount; objective = unconditional
                # after-tax terminal real P50 (directional single-lever grid).
                n = max(500, min(int(body.get("paths", 1500)), 4000))
                grid = body.get("grid") or [0, 12_000, 24_000, 36_000, 48_000,
                                            60_000, 80_000, 100_000]
                pts = []
                for amt in grid:
                    c = copy.deepcopy(cfg)
                    c.setdefault("tax_true", {})["enabled"] = True
                    rl = c.setdefault("roth_ladder", {})
                    rl["enabled"] = amt > 0
                    rl["annual_conversion_y0"] = float(amt)
                    st = ENG.summary(c, n, seed, relocation_on=False)
                    pts.append({"conversion": float(amt),
                                "terminal_real_p50": st["terminal_real_p50"],
                                "terminal_after_tax_real_p50":
                                    st["terminal_after_tax_real_p50"],
                                "lifetime_success": st["lifetime_success"],
                                "true_tax_p50": st.get("true_tax_p50")})
                # Solvency is lexicographically primary; wealth then uses every
                # path (failures are zero) and discounts terminal tax liability.
                best = _select_roth_best(pts)
                return self._json({"n_paths": n, "seed": seed,
                                   "objective": "lifetime_success_then_unconditional_after_tax_terminal_p50",
                                   "points": pts, "best": best})
            if path == "/api/drill":
                # I3: chart drill-down — one small synchronous batch.
                try:
                    out = ENG.drill(cfg, str(body.get("kind")), seed,
                                    int(body.get("paths", 200)),
                                    **{k: body[k] for k in ("age", "lo", "hi")
                                       if k in body})
                except ENG.ConfigIncomplete:
                    raise          # carries code+field; the boundary answers it
                except (ValueError, KeyError) as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json(out)
            if path == "/api/import_ssa":
                # D2: SSA statement XML -> AIME/PIA plus a local-only,
                # yearless top-35 sufficient statistic. Raw XML, names and
                # calendar-keyed earnings history are never echoed or stored.
                import ssa_import
                r = ssa_import.import_statement(
                    str(body.get("text") or ""),
                    birth_year_fallback=body.get("birth_year"),
                    project=bool(body.get("project")))
                return self._json(r, 400 if "error" in r else 200)
            if path == "/api/import_csv":
                # D1: local-only broker-CSV parsing. Account-level aggregates
                # only — positions never echoed, logged, or stored.
                import csv_import
                r = csv_import.parse_broker_csv(str(body.get("text") or ""))
                return self._json(r, 400 if "error" in r else 200)
            if path == "/api/checkin/import_csv":
                # Phase 2. Parses a broker TRANSACTIONS export into proposed
                # check-in flow lines; it proposes, never records. The ledger
                # is append-only with immutability triggers, so a mis-parsed
                # import cannot be taken back — the user confirms and
                # /api/checkin/record does the writing. Local-only, like the
                # positions parser beside it.
                import csv_import
                r = csv_import.parse_transactions_csv(str(body.get("text") or ""))
                return self._json(r, 400 if "error" in r else 200)
            if path == "/api/spending_import":
                # Phase 4. A year of budgeting-app export -> an annual total
                # and a category breakdown, offered as the check-in's actual
                # spending. Aggregates ONLY: unlike the broker importer beside
                # it, no transaction row is returned, because a year of
                # personal spending is a record of what somebody did rather
                # than of what they own, and the check-in needs one number.
                #
                # Nothing is written here, and no config is involved, so this
                # is deliberately outside the preflight: there is nothing to
                # preflight against.
                import spending_import as SPEND
                r = SPEND.parse_spending_csv(str(body.get("text") or ""))
                return self._json(r, 400 if "error" in r else 200)
            if path == "/api/housing":
                # E5: deterministic mortgage schedule + rent-vs-buy net-worth
                # comparison (no MC — pure math, instant).
                import housing as HOUSING
                return self._json(HOUSING.rent_vs_buy_deterministic(cfg))
            if path == "/api/rentbuy":
                # E5: the probabilistic comparison — same config under
                # mode=rent vs mode=buy, each through summary().
                n = max(500, min(int(body.get("paths", 1500)), 3000))
                out = {}
                for m in ("rent", "buy"):
                    c = copy.deepcopy(cfg)
                    hz = c.setdefault("housing", {})
                    hz["enabled"] = True
                    hz["mode"] = m
                    out[m] = ENG.summary(c, n, seed, relocation_on=False)
                return self._json({"n_paths": n, "seed": seed, **out})
            if path == "/api/decide/plan":
                # Synchronous and cheap: it runs no engine, it only counts.
                try:
                    return self._json(decide_plan(cfg, body))
                except Exception as exc:      # noqa: BLE001
                    return self._json({"error": _public_error(exc)}, 400)
            if path == "/api/decide/start":
                # Background job on the same polling channel as the headline
                # run: poll /api/progress, fetch /api/result, /api/cancel.
                try:
                    jid = start_decide_job(cfg, body, seed)
                except ENG.ConfigIncomplete:
                    raise          # carries code+field; the boundary answers it
                except Exception as exc:      # noqa: BLE001
                    return self._json({"error": _public_error(exc)}, 400)
                return self._json({"job": jid})
            if path == "/api/annuity/plan":
                # Synchronous and cheap: it runs no engine, it only counts.
                try:
                    return self._json(annuity_plan(cfg, body))
                except Exception as exc:      # noqa: BLE001
                    return self._json({"error": _public_error(exc)}, 400)
            if path == "/api/annuity/start":
                # Background job on the same polling channel as everything
                # else: /api/progress, /api/result, /api/cancel.
                try:
                    jid = start_annuity_job(cfg, body, seed)
                except ENG.ConfigIncomplete:
                    raise          # carries code+field; the boundary answers it
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"job": jid})
            if path == "/api/briefing_pack":
                # Assembles what already exists; runs no engine. Behind the
                # preflight anyway, because a config this cannot parse would
                # produce a pack whose limitations section is wrong, and this
                # is the one export designed to be read somewhere else.
                _preflight_config(cfg)
                lang = str(body.get("language") or "zh")
                return self._json(BRIEFING_PACK.build(
                    config=cfg,
                    packet=body.get("packet"),
                    memo=body.get("memo"),
                    attribution=body.get("attribution"),
                    sampling_error=(body.get("sampling_error")),
                    limitations=LIMITATIONS_MOD.triggered(cfg, lang),
                    language=lang))
            if path == "/api/transition/propose":
                # Reads and returns a checklist; it cannot write. The split
                # between this and `apply` is the feature's whole contract.
                _preflight_config(cfg)
                try:
                    return self._json(LIFE_TRANSITIONS.propose(
                        str(body.get("kind") or ""), cfg))
                except LIFE_TRANSITIONS.TransitionError as exc:
                    return self._json({"error": str(exc), "code": exc.code},
                                      exc.http_status)
            if path == "/api/transition/commit":
                # The atomic write. Without a route this whole slice would be
                # a library nothing calls -- "both sides correct, nobody
                # looking at the seam", which this project has paid for six
                # times.
                _preflight_config(cfg)
                writer = _archive_writer()
                if writer is None:
                    return self._json({
                        "error": ("this archive has no control journal, so a "
                                  "transition cannot be written atomically; "
                                  "run a formal migration first"),
                        "code": "no_control_journal"}, 409)
                try:
                    return self._json(LIFE_TRANSITIONS.commit(
                        _archive_store(), writer, _checkin_seam(),
                        plan_id=str(body.get("plan_id") or ""),
                        parent_version_id=str(body.get("plan_version_id") or ""),
                        kind=str(body.get("kind") or ""),
                        cfg=cfg, confirmed=body.get("confirmed") or [],
                        checkin_body=body.get("checkin") or {},
                        jump_minor=body.get("jump_minor"),
                        occurred_at=str(body.get("occurred_at") or "")))
                except LIFE_TRANSITIONS.TransitionError as exc:
                    return self._json({"error": str(exc), "code": exc.code},
                                      exc.http_status)
                except CHECKIN.CheckinError as exc:
                    return self._json({"error": str(exc), "code": exc.code},
                                      exc.http_status)
            if path == "/api/transition/packet_plan":
                # What a before/after packet for this transition would cost,
                # BEFORE anything runs. Twenty-odd engine runs is not a thing
                # to start quietly, and this project's first rule is that a
                # refusal or a price lands before the user is shown progress.
                #
                # No second study engine: the transition becomes an ordinary
                # decision alternative and goes through `plan_study`, which is
                # the same call the decide panel makes.
                _preflight_config(cfg)
                try:
                    shaped = LIFE_TRANSITIONS.as_alternative(
                        cfg, str(body.get("kind") or ""),
                        body.get("confirmed") or [])
                except LIFE_TRANSITIONS.TransitionError as exc:
                    return self._json({"error": str(exc), "code": exc.code},
                                      exc.http_status)
                if not shaped["applicable"]:
                    return self._json(shaped)
                import assumption_packs as AP
                import decision_packet as DP
                import decision_study as DS
                chosen = AP.select_packs(cfg)
                plan = DS.plan_study(
                    "transition", [DP.Alternative(
                        shaped["alternative"]["name"],
                        shaped["alternative"]["changes"], shaped["levers"])],
                    seeds=int(body.get("seeds", 3)),
                    return_models=DECIDE_RETURN_MODELS,
                    adverse_packs=chosen["applicable"])
                return self._json({**shaped, "plan": plan,
                                   "paths": int(body.get("paths", 10_000))})
            if path == "/api/transition/apply":
                # Returns a NEW config carrying only the confirmed paths. It
                # saves nothing: the user looks at the result and keeps it
                # through the normal save, so a wizard cannot write a plan
                # nobody reviewed.
                _preflight_config(cfg)
                try:
                    return self._json(LIFE_TRANSITIONS.apply_confirmed(
                        cfg, str(body.get("kind") or ""),
                        body.get("confirmed") or []))
                except LIFE_TRANSITIONS.TransitionError as exc:
                    return self._json({"error": str(exc), "code": exc.code},
                                      exc.http_status)
            if path == "/api/limitations":
                # Pure and cheap: it runs no engine, it reads flags. Still
                # behind the preflight, because a config this cannot parse is
                # a config whose disclosures would be wrong, and quietly
                # returning the four that happen to evaluate would be the
                # worst answer available.
                _preflight_config(cfg)
                return self._json(LIMITATIONS_MOD.triggered(
                    cfg, str(body.get("language") or "zh")))
            if path == "/api/funded_ratio":
                # Synchronous on purpose: this runs no simulation at all, it
                # discounts two sets of cash flows. A background job would be
                # ceremony around arithmetic.
                try:
                    # The refusal comes first even though there is no job to
                    # mint here: without it a malformed discount rate computes
                    # a confident wrong ratio instead of being named.
                    _preflight_config(cfg)
                    return self._json(FRATIO.compute(cfg))
                except ENG.ConfigIncomplete:
                    raise          # carries code+field; the boundary answers it
                except Exception as exc:      # noqa: BLE001
                    return self._json({"error": _public_error(exc)}, 400)
            if path == "/api/roth_schedule/start":
                try:
                    jid = start_roth_schedule_job(cfg, body, seed)
                except ENG.ConfigIncomplete:
                    raise          # carries code+field; the boundary answers it
                except Exception as exc:      # noqa: BLE001
                    return self._json({"error": _public_error(exc)}, 400)
                return self._json({"job": jid})
            if path == "/api/asset_location/start":
                try:
                    jid = start_asset_location_job(cfg, body, seed)
                except ENG.ConfigIncomplete:
                    raise          # carries code+field; the boundary answers it
                except Exception as exc:      # noqa: BLE001
                    return self._json({"error": _public_error(exc)}, 400)
                return self._json({"job": jid})
            if path == "/api/bequest/start":
                # Background job on the same polling channel as everything
                # else: /api/progress, /api/result, /api/cancel.
                try:
                    jid = start_bequest_job(cfg, body, seed)
                except ENG.ConfigIncomplete:
                    raise          # carries code+field; the boundary answers it
                except Exception as exc:      # noqa: BLE001
                    return self._json({"error": _public_error(exc)}, 400)
                return self._json({"job": jid})
            if path == "/api/career_break/start":
                # Same polling channel as every other paired comparison:
                # /api/progress, /api/result, /api/cancel.
                try:
                    jid = start_career_break_job(cfg, body, seed)
                except ENG.ConfigIncomplete:
                    raise          # carries code+field; the boundary answers it
                except Exception as exc:      # noqa: BLE001
                    return self._json({"error": _public_error(exc)}, 400)
                return self._json({"job": jid})
            if path == "/api/execution_simplification/start":
                try:
                    jid = start_execution_simplification_job(cfg, body, seed)
                except ENG.ConfigIncomplete:
                    raise
                except Exception as exc:      # noqa: BLE001
                    return self._json({"error": _public_error(exc)}, 400)
                return self._json({"job": jid})
            if path == "/api/frontier":
                # S2: background job — same polling channel.
                jid = start_frontier_job(cfg, body.get("paths", 1200), seed,
                                         body.get("grid", 7),
                                         body.get("ranges"))
                return self._json({"job": jid})
            if path == "/api/goalseek":
                # S1: background job — poll /api/progress, fetch /api/result,
                # cancel via /api/cancel (same channel as the headline run).
                try:
                    jid = start_goalseek_job(
                        cfg, body.get("goal") or {}, body.get("levers") or [],
                        body.get("paths", 1200), seed, body.get("grid", 8))
                except ENG.ConfigIncomplete:
                    raise          # carries code+field; the boundary answers it
                except ValueError as exc:
                    return self._json({"error": str(exc)}, 400)
                return self._json({"job": jid})
            if path == "/api/story":
                # I2: single-path story mode — one batch, three lives picked
                # from the same distribution (typical/lucky/unlucky). Reroll
                # = the client bumps the seed.
                n = max(60, min(int(body.get("paths", 150)), 500))
                return self._json(ENG.story(cfg, n, seed))
            if path == "/api/estimate/savings":
                # OPEN_ITEMS E33. Deliberately synchronous and deliberately
                # NOT a job: this is a single year of arithmetic, and a job
                # would turn every keystroke in the wizard into a polling
                # loop. Deliberately thin, too -- the answer comes from
                # `ENG.estimate_first_year_savings`, which runs the engine's
                # own contribution code under the engine lock. Anything
                # computed here instead would be the third implementation of
                # a number that already has one too many.
                return self._json(ENG.estimate_first_year_savings(cfg))
            if path == "/api/live":
                # I1: live-tweak mode — one SYNCHRONOUS Quick evaluation for
                # the overview sliders (~1.5s @1500). Same seed as the panel's
                # baseline call => common-random-numbers deltas. Goes through
                # summary() so engine-lock serialization is inherited.
                n = max(400, min(int(body.get("paths", 1500)), 3000))
                st = ENG.summary(cfg, n, seed, relocation_on=False)
                return self._json({"n_paths": n, "seed": seed, "summary": st})
            if path == "/api/strategies":
                # E3: withdrawal-strategy comparison — the SAME config and
                # seed under N spending rules. Each evaluation goes through
                # summary() so the engine lock and global-hook serialization
                # (household/match ctx) are inherited, not reimplemented.
                n = max(500, min(int(body.get("paths", 2000)), 4000))
                types = body.get("rules") or ["gk", "fixed_real", "vpw",
                                              "floor_upside", "abw"]
                params = body.get("params") or {}
                labels = {"gk": ["GK 护栏", "GK guardrails"],
                          "fixed_real": ["固定实际额", "Fixed real"]}
                for k, (_c, zh, en) in ENG.STRATEGY_LIBRARY.items():
                    labels[k] = [zh, en]
                pts = []
                for rt in types:
                    if rt not in labels:
                        return self._json({"error": f"unknown rule {rt}"}, 400)
                    c = copy.deepcopy(cfg)
                    rd = c.setdefault("rule", {})
                    rd["type"] = rt
                    rd.update(params.get(rt) or {})
                    st = ENG.summary(c, n, seed, relocation_on=False)
                    # The spending fan for THIS rule. ROADMAP's presentation
                    # item: "a spending-path percentile fan per withdrawal
                    # strategy, turning ABW/VPW's spending volatility into a
                    # visible comparison dimension". The volatility is the
                    # whole point of choosing between these rules and a
                    # success rate cannot show it -- two rules can survive
                    # equally often while one of them cuts your spending by a
                    # third in a bad decade.
                    #
                    # A SEPARATE, SMALLER run: `summary` and `lifecycle_sample`
                    # each draw their own paths, and a fan is a distribution
                    # view, which is what the smaller illustrative sample is
                    # for. The count is reported beside it rather than left to
                    # look like the headline's -- the estate line went the
                    # other way for the opposite reason, because a FRACTION
                    # off a small binned sample would be wrong where a shape
                    # is not.
                    fan = ENG.lifecycle_sample(c, FAN_PATHS, seed, False)
                    pts.append({"type": rt, **st,
                                "spending_fan": fan.get("consumption") or [],
                                "spending_fan_paths": FAN_PATHS})
                return self._json({"n_paths": n, "seed": seed,
                                   "points": pts, "labels": labels,
                                   "spending_fan_paths": FAN_PATHS,
                                   "spending_fan_basis": (
                                       "Each rule's spending fan is drawn from "
                                       "a separate, smaller sample than the "
                                       "success rates above it. It shows the "
                                       "SHAPE of spending under that rule, not "
                                       "a rate; a percentile band needs far "
                                       "fewer paths than a tail probability.")})
            if path == "/api/robustness":
                # Multi-seed robustness: same config & paths, three independent
                # seeds — the simplest honest check that a headline number is
                # not a lucky draw. Home-only, sequential (engine lock).
                n = max(500, min(int(body.get("paths", 2000)), 5000))
                base_seed = seed
                pts = []
                for i in range(3):
                    s2 = ENG.summary(cfg, n, base_seed + i * 1013, relocation_on=False)
                    pts.append({"seed": base_seed + i * 1013,
                                "lifetime_success": s2["lifetime_success"],
                                "fire_age_p50": s2["fire_age_p50"],
                                "terminal_real_p50": s2["terminal_real_p50"],
                                "cons_p50": s2["cons_p50"]})
                return self._json({"n_paths": n, "points": pts})
            if path == "/api/save_file":
                # Native-window mode: WKWebView can't window.open blob URLs,
                # so exports are written server-side into ~/Downloads.
                kind = body.get("kind")
                results = body.get("results") or {}
                name = str(body.get("name") or "fire").strip() or "fire"
                safe = "".join(ch if (ch.isalnum() or ch in "-_ ") else "_"
                               for ch in name)[:60].strip() or "fire"
                ddir = os.path.expanduser("~/Downloads")
                os.makedirs(ddir, exist_ok=True)
                stamp = time.strftime("%Y%m%d-%H%M%S")
                if kind == "report":
                    # Guardrail dollarisation is derived HERE rather than on
                    # the page: the derivation belongs next to the policies it
                    # reads, and a second copy in JavaScript would be the shape
                    # every seam defect this week has had. The page sends its
                    # config; the report gets the priced rows.
                    extra = dict(body.get("extra") or {})
                    if cfg and not extra.get("guardrail_dollars"):
                        import guardrails as GUARD
                        import guardrail_dollars as GDOLLARS
                        baseline = (results.get("home") or {})
                        try:
                            policies = GUARD.default_policies(baseline)
                        except Exception:              # noqa: BLE001
                            policies = []
                        extra["guardrail_dollars"] = GDOLLARS.dollarise_all(
                            policies, cfg)
                    f, out = _open_export(ddir, f"{safe}_report_{stamp}", "html")
                    with f:
                        f.write(build_report.build(results, extra))
                elif kind == "json":
                    f, out = _open_export(ddir, f"{safe}_results_{stamp}", "json")
                    with f:
                        json.dump(results, f, indent=1, ensure_ascii=False)
                elif kind == "briefing":
                    # Two files, deliberately: the Markdown is what a person
                    # pastes and the JSON is what a machine parses, and
                    # collapsing them would make one of the two audiences read
                    # the wrong one. Both carry the same not-de-identified
                    # statement, because whichever one travels is the one that
                    # has to say it.
                    pack = body.get("pack") or {}
                    f, out = _open_export(ddir, f"{safe}_briefing_{stamp}", "md")
                    with f:
                        f.write(str(pack.get("markdown") or ""))
                    fj, outj = _open_export(ddir, f"{safe}_briefing_{stamp}",
                                            "json")
                    with fj:
                        json.dump(pack.get("json") or {}, fj, indent=1,
                                  ensure_ascii=False)
                    return self._json({
                        "path": "~/Downloads/" + os.path.basename(out),
                        "json_path": "~/Downloads/" + os.path.basename(outj)})
                elif kind == "family_evidence":
                    try:
                        pack = FAMILY_EVIDENCE.build(
                            body.get("evidence"),
                            language=str(body.get("language") or "zh"))
                    except (TypeError, ValueError) as exc:
                        return self._json({
                            "error": str(exc),
                            "code": "invalid_family_evidence"}, 400)
                    f, out = _open_export(
                        ddir, f"{safe}_family_evidence_{stamp}", "md")
                    with f:
                        f.write(pack["markdown"])
                    fj, outj = _open_export(
                        ddir, f"{safe}_family_evidence_{stamp}", "json")
                    with fj:
                        json.dump(pack["json"], fj, indent=1,
                                  ensure_ascii=False)
                    return self._json({
                        "path": "~/Downloads/" + os.path.basename(out),
                        "json_path": "~/Downloads/" + os.path.basename(outj)})
                else:
                    return self._json({"error": "unknown kind"}, 400)
                return self._json({"path": "~/Downloads/" + os.path.basename(out)})
            if path == "/api/shutdown":
                # User-triggered quit from the page. Reply first, then stop.
                threading.Timer(0.3, _graceful_shutdown, args=(self.server,)).start()
                return self._json({"ok": True})
            if path == "/api/sweep":
                out = run_sweep(cfg, body["param"], body["values"],
                                body.get("paths", 3000), seed)
                return self._json(out)
            if path == "/api/sensitivity":
                out = run_sensitivity(cfg, body.get("paths", 1500), seed)
                return self._json(out)
            if path == "/api/backtest":
                out = run_backtest(cfg, body.get("retire_age"), seed)
                return self._json(out)
            if path == "/api/report":
                results = body.get("results")
                if not results:
                    return self._json({"error": "no results to render"}, 400)
                return self._json({"html": build_report.build(results, body.get("extra"))})
            return self._json({"error": "unknown endpoint"}, 404)
        except ENG.ConfigIncomplete as exc:
            # One clause for every config-taking route, current and future. A
            # per-route list is the shape that silently stops covering the route
            # added after it, and this endpoint set has already grown twice
            # under this seam. 400, not 500: the request is answerable, the plan
            # is what is missing, and `field` says which part of it.
            return self._json({"error": _public_error(exc),
                               "code": exc.code, "field": exc.field}, 400)
        except PersistenceError as exc:
            # Not a blanket 4xx for everything the persistence layer can
            # raise -- a corrupt archive really is a server fault and should
            # stay a 500. This is the narrower statement `/api/run_start`
            # already makes at its two `archive_store_unavailable` sites: if
            # the store cannot be OPENED, that is a named, actionable
            # condition rather than an anonymous stack trace, and it should
            # reach the client as one.
            #
            # Found by driving install #12: a disposable archive under a path
            # the persistence layer refuses produced
            # `500 {"error": "unsafe SQLite parent path: <local path>"}` from
            # `/api/decision/archive`. The real app uses Application Support
            # and never meets it, which is why nothing had noticed -- and the
            # route that DOES handle it was found by comparing two servers,
            # not by a test.
            return self._json({"error": _public_error(exc),
                               "code": "archive_store_unavailable"}, 503)
        except ARCHIVE_SEAM.ArchiveWriteRefused as exc:
            # Same reasoning as the clause above, and the same defect it was
            # written for. This refusal already carries its own status and
            # code -- `require_writable` raises it as a 409 precisely so the
            # front end can act on it -- but for a long time exactly ONE route
            # caught it: the archive branch of `/api/run_start`. Every other
            # write went through `writer.write()`, whose first statement is
            # that same `require_writable()`, and got a 500 with a traceback.
            #
            # So under a `source_changed` or `manual_recovery_required`
            # archive, recording a check-in and archiving a decision both
            # answered "server error" for a condition the app understands
            # perfectly and reports properly one route over. It is not a
            # RecoveryError subclass, which is why the four RECOVERY clauses
            # in those branches never saw it.
            return self._json({"error": str(exc), "code": exc.code},
                              exc.http_status)
        except Exception as exc:
            traceback.print_exc()
            return self._json({"error": _public_error(exc)}, 500)

    def _serve_static(self, path: str):
        if path in ("/", ""):
            path = "/index.html"
        rel = os.path.normpath(path.lstrip("/"))
        if rel.startswith(".."):
            return self._send(403, b"forbidden", "text/plain")
        full = os.path.join(WEB_DIR, rel)
        if not os.path.isfile(full):
            return self._send(404, b"not found", "text/plain")
        ext = os.path.splitext(full)[1].lower()
        with open(full, "rb") as f:
            self._send(200, f.read(),
                       CONTENT_TYPES.get(ext, "application/octet-stream"))


def _existing_instance(port: int) -> bool:
    """True if a FIRE Modeling server already answers on this port — so a
    second double-click reuses the running instance instead of starting a
    twin on the next port (audit P2-5). Require the current capability
    bootstrap too, so a newly launched build never reuses a pre-boundary
    server that still exposes unprotected POST routes."""
    import urllib.request
    try:
        with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/capability", timeout=0.25) as r:
            if "FIRE" not in (r.headers.get("Server") or ""):
                return False
            payload = json.loads(r.read().decode("utf-8"))
            return isinstance(payload.get("capability"), str) and bool(payload["capability"])
    except Exception:
        return False


def _find_existing_instance(port_start: int, count: int = 20):
    for port in range(port_start, port_start + count):
        if _existing_instance(port):
            return port
    return None


def _new_httpd(port: int):
    """Create one loopback server and its in-memory request capability."""
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    httpd.fire_capability = secrets.token_urlsafe(32)
    return httpd


def serve_background(port_start: int = 8765, *, reuse: bool = True):
    """Bind and serve on a daemon thread — for the native-window (pywebview)
    entry point. Returns (httpd, url). If another instance is already running and
    `reuse` is set, returns (None, its url) so the caller can still open a window
    onto it.

    `reuse=False` exists for the promotion gate. Handing back somebody else's
    already-running server is right for a user double-clicking the app and wrong
    for a gate: it would test whatever was listening instead of the bundle under
    test, and report a pass for it. A gate needs the child it started or nothing.
    """
    if reuse:
        existing_port = _find_existing_instance(port_start)
        if existing_port is not None:
            return None, f"http://127.0.0.1:{existing_port}/"
    httpd = None
    port = port_start
    if port_start == 0:
        # Let the OS pick, so two gates can run without agreeing on a number.
        httpd = _new_httpd(0)
        port = httpd.server_port
    else:
        for p in range(port_start, port_start + 20):
            try:
                httpd = _new_httpd(p)
                port = p
                break
            except OSError:
                continue
    if httpd is None:
        raise RuntimeError("could not bind a port in range")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{port}/"
    print("\n  FIRE Modeling  —  engine", ENG.ENGINE_VERSION, flush=True)
    print(f"  Serving at {url} (native window)", flush=True)
    return httpd, url


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()

    existing_port = _find_existing_instance(args.port)
    if existing_port is not None:
        url = f"http://127.0.0.1:{existing_port}/"
        print(f"  FIRE Modeling already running — reusing {url}", flush=True)
        if not args.no_open:
            webbrowser.open(url)
        return

    port = args.port
    httpd = None
    for p in range(args.port, args.port + 20):
        try:
            httpd = _new_httpd(p)
            port = p
            break
        except OSError:
            continue
    if httpd is None:
        print("Could not bind a port in range.", file=sys.stderr)
        sys.exit(1)

    url = f"http://127.0.0.1:{port}/"
    print("\n  FIRE Modeling  —  engine", ENG.ENGINE_VERSION, flush=True)
    print(f"  Serving at {url}", flush=True)
    print(f"  READY {url}", flush=True)
    print("  Press Ctrl+C to stop.\n", flush=True)
    if not args.no_open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n  Stopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
