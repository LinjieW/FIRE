"""Formal-run archive writes, through the same seam §6 uses (S1).

Why this module exists
----------------------
After the cutover the archive is the authoritative store, and the control journal
is what makes that claim checkable: it records the archive's logical identity and
who changed it to what. Anything that writes the live file without an
`archive_write` operation makes those two disagree, and the next startup
reconciliation — correctly — concludes that an unowned writer has been at work and
latches `manual_recovery_required`.

The formal-run path did exactly that. `prepare_run`, `save_run_snapshot`,
`finish_attempt` and `recover_running_attempts` opened the live archive and wrote,
so a successful Standard or Official run took the storage seam down behind it: the
run returned 200 and every subsequent §6 call returned 423.

`assert_archive_write_allowed` was not a defence and was never meant to be one. It
bootstraps and returns, which reports on the state *before* the mutation and says
nothing about the mutation itself — so it could not have caught this even in
principle.

What it does
------------
The same seven steps as `StorageApi._write`, for callers that have no HTTP body:
stage a copy of the archive, mutate the copy, measure it, prebind an `archive_write`
intent against that exact measurement, swap, read back, acknowledge. The live file
is never written in place, so a failure anywhere leaves the archive as it was.

Two properties this has to preserve and does:

* **No journal, no change.** With no control database, there is nothing to own the
  write and nothing to latch, and the 2.0 behaviour is the behaviour. Callers keep
  a `writer is None` branch that is the untouched original code rather than a
  reimplementation of it.
* **The engine does not run under the writer lease.** A run is minutes of Monte
  Carlo between two short durable steps. Each step is its own operation, taken and
  released; nothing is held across the computation. That is why this is a `write()`
  per mutation rather than a transaction spanning the run.
"""
from __future__ import annotations

import os
import pathlib
from typing import Any, Callable, Optional

import persistence as PERSISTENCE
import recovery as RECOVERY

# Returned by a mutation that turned out to have nothing to do. Committing an
# archive_write for it would advance the generation for no change, and a generation
# that moves without a reason is indistinguishable from one that moved for a reason
# nobody recorded.
NOTHING_TO_DO = object()


class ArchiveWriteRefused(RuntimeError):
    """The archive is not in a state that accepts a formal-run write."""

    def __init__(self, message: str, *, code: str, http_status: int = 409):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def control_journal_path(archive_path: str) -> pathlib.Path:
    archive = pathlib.Path(os.path.abspath(os.path.expanduser(archive_path)))
    return archive.parent / "recovery-control.sqlite3"


def journal_exists(archive_path: str) -> bool:
    """Whether a control journal owns this archive. Opt-in, exactly as before."""
    return control_journal_path(archive_path).exists()


class JournaledArchiveWriter:
    """Runs one archive mutation as one prebound `archive_write` operation."""

    def __init__(self, manager: Any, archive_path: str):
        self.manager = manager
        self.archive_path = str(archive_path)

    # ------------------------------------------------------------- refusals

    def authority_status(self) -> Optional[str]:
        snapshot = self.manager._bootstrap()
        return (snapshot.get("authority") or {}).get("status")

    def require_writable(self) -> str:
        """Refuse before a caller has changed anything.

        The two refusing states are the two §6 already refuses, for the same
        reasons, and a formal run is not a special case of either:

        `source_changed` means the legacy payload moved under the archive and
        nobody has reconciled them, so there is no agreed answer to "which of
        these two is the user's data". Writing a new Plan into one of them
        during that disagreement makes the reconciliation harder, not easier.

        `manual_recovery_required` means the journal has already lost track of
        the archive once. A further unowned write is the last thing that helps.

        Raised *here*, from a method whose only job is to refuse, so the refusal
        can be placed before the first row changes rather than discovered
        halfway through a run.
        """
        status = self.authority_status()
        if status == "manual_recovery_required":
            raise ArchiveWriteRefused(
                "archive is latched for manual recovery",
                code="manual_recovery_required", http_status=423)
        if status == "source_changed":
            raise ArchiveWriteRefused(
                "legacy source changed after cutover; reconcile before "
                "archiving a run",
                code="source_changed", http_status=409)
        return status

    # ---------------------------------------------------------------- write

    def write(self, key: str, mutate: Callable[[Any], Any], *,
              close_store: Optional[Callable[[], None]] = None,
              reopen_store: Optional[Callable[[], Any]] = None) -> Any:
        """Stage, mutate, measure, prebind, swap, read back, acknowledge.

        `mutate` receives a `PersistenceStore` open on a *copy* of the archive and
        returns whatever the caller needs. Returning `NOTHING_TO_DO` commits
        nothing.
        """
        # Serialised against every other archive write in this process, and on
        # the same RLock `StorageApi._write` uses — they share the manager, so
        # this is one queue rather than two.
        #
        # Without it a §6 save that overlapped a run lost the generation CAS and
        # surfaced as `idempotency_conflict`, which is both a spurious failure
        # and a misleading name for it: the caller's request was fine, it had
        # simply measured a staged image against a generation that moved while
        # it was being measured. Serialising the short durable steps removes the
        # race rather than reporting it.
        #
        # This is emphatically *not* a lock held across the run. The engine
        # executes between two of these calls, holding nothing; each call is a
        # SQLite backup and a hash of a small database.
        with self.manager._lock:
            return self._locked_write(key, mutate, close_store, reopen_store)

    def _locked_write(self, key, mutate, close_store, reopen_store) -> Any:
        self.require_writable()
        staging_root = self.manager.support_root / ".run-write-staging"
        RECOVERY._secure_dir(staging_root)
        staged = staging_root / (RECOVERY._sha256(key.encode("utf-8"))[:32]
                                 + ".sqlite3")
        if staged.exists():
            staged.unlink()
        if os.path.exists(str(self.manager.archive_path)):
            RECOVERY._sqlite_backup(self.manager.archive_path, staged)
        else:
            # The archive does not exist yet, so the first formal run's own
            # schema creation *is* the first durable archive mutation — which the
            # contract asks to be owned like any other. There is nothing to copy;
            # the staged path is left absent and `PersistenceStore` creates the
            # schema there, and the swap installs that image as the archive under
            # a prebound generation.
            #
            # Backing up a missing file was the bug this replaces: the old direct
            # path created the archive as a side effect of opening a store, so
            # routing through the seam turned a first archive run into
            # `FileNotFoundError: f.sqlite3`.
            pass
        try:
            store = PERSISTENCE.PersistenceStore(
                str(staged), app_release_id="fire-modeling-3.0")
            try:
                result = mutate(store)
            finally:
                store.close()
            if result is NOTHING_TO_DO:
                return NOTHING_TO_DO

            RECOVERY._checkpoint_delete_journal(staged)
            new_logical = RECOVERY.logical_identity(str(staged))
            # The attempt key includes the staged identity for the same reason
            # §6's does: a rolled-back attempt must not consume the caller's
            # ability to try again, and `control_operations` is UNIQUE on both
            # the key and the fingerprint.
            attempt_key = RECOVERY._sha256_json({
                "format": "fire-run-archive-write-v1",
                "key": key, "staged_logical_sha256": new_logical})
            prepared = self.manager.prepare_archive_write(
                idempotency_key=attempt_key,
                request_fingerprint=attempt_key,
                new_logical_sha256=new_logical,
                staged_db_sha256=RECOVERY._sha256(
                    RECOVERY._read_regular(staged, RECOVERY.MAX_ARCHIVE_BYTES)),
                staging_path=str(staged))
            self.manager.apply_archive_write(
                prepared["operation_id"],
                lambda path: os.replace(str(staged), str(path)),
                close_store=close_store, reopen_store=reopen_store)
            return result
        finally:
            # A refused or failed write must not leave a full copy of the
            # archive behind. The successful path has already moved the file, so
            # this only ever removes what was not consumed — including the writer
            # lock, which is a separate file and outlives the database unless it
            # is named.
            RECOVERY._remove_sidecars(staged)
            staged.unlink(missing_ok=True)
            pathlib.Path(str(staged) + ".lock").unlink(missing_ok=True)
