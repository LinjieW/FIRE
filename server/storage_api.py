"""Post-cutover storage seam — the read half (PHASE_0_EXIT_CONTRACT.md §6).

Once the authority CAS has moved to `sqlite_preferred`, the browser stops
reading plans out of localStorage and reads them from here instead.  Two rules
shape everything below.

First, these three read endpoints are *pure*.  §6 spells that out because an
earlier draft let a GET record a digest observation, which made a read able to
move authority.  They allocate no generation, append no event, and touch no
archive bytes; digest observation lives in its own explicit POST.

Second, a reader must prove which authority it thinks it is reading under.
After cutover the caller carries the exact external receipt hash, and a caller
holding a stale one is refused rather than quietly served fresher data — a
stale tab that reads successfully is a tab that will later write successfully.

Writes add a third rule: nothing edits the live archive in place.  Each one is
copied to a staging image, mutated there, measured, and swapped in under a
prebound generation through the same prepare/apply seam the cutover uses, so an
ordinary save inherits the same rollback and post-commit readback.

All five write endpoints share `_write`; §6's post-cutover surface is complete.

Idempotency follows §6 as revised on 2026-07-25: a repeated key is refused and
the caller resynchronises, rather than being handed the original response.  A
retry under the authority the client still holds is stale before it is duplicate
— it gets 412 with the current receipt and re-reads.  Answering it as though
nothing had changed would be the weaker behaviour, not the kinder one.

That leaves the question the earlier version got wrong: *which* key is the one
being repeated.  Two live here, at different layers, and conflating them was the
defect.

The **internal** key hashes the endpoint, the epoch, the authority receipt, and
the whole body.  That is what a compare-and-swap needs: it makes an archive write
refuse to land on a generation it was not computed against.  It is useless as a
record of "has this caller already asked for this", because every successful
write advances the epoch — so once the caller resynchronised, the same
`request_id` hashed to a different internal key, derived a different object id,
and produced a twin.  The caller believed it was retrying one action and got two.

The **external** key is the caller's own `Idempotency-Key`, which the HTTP layer
requires and requires to equal `request_id` in the body.  It hashes nothing but
the endpoint and that id, so no amount of resynchronisation can make the server
forget it.  It is recorded in the control journal's append-only
`control_external_requests`, and every object id is derived from it.

Both halves are needed.  The ledger is what refuses the duplicate; deriving ids
from the external key is what makes "no twin" structural rather than merely
checked, so the window between a committed write and its ledger record resolves
to the existing object instead of a second one.  A caller that genuinely wants a
second action sends a second `Idempotency-Key` — the intent is stated, not
inferred from a hash of the body.
"""
from __future__ import annotations

import json
import os
import pathlib
import sqlite3
from typing import Any, Optional

import persistence as PERSISTENCE
import recovery as RECOVERY


def _plan_exists(store: Any, plan_id: str) -> bool:
    with store._transaction() as conn:
        return conn.execute("SELECT 1 FROM plans WHERE id=?",
                            (plan_id,)).fetchone() is not None


def _version_exists(store: Any, version_id: str) -> Optional[str]:
    """The plan owning `version_id`, or None. The replay check for a version."""
    with store._transaction() as conn:
        row = conn.execute("SELECT plan_id FROM plan_versions WHERE id=?",
                           (version_id,)).fetchone()
    return None if row is None else row[0]


def _require_tip(store: Any, plan_id: str, expected: Any) -> str:
    """Compare-and-swap on the unique current version.

    §6 is deliberate here: zero or several childless versions is a conflict, and
    is never resolved by picking the newest created_at. Guessing would let two
    tabs that disagree about history both "succeed" and silently fork a plan.
    """
    with store._transaction() as conn:
        if conn.execute("SELECT 1 FROM plans WHERE id=?",
                        (plan_id,)).fetchone() is None:
            raise StorageError("plan is unknown", code="version_conflict",
                               http_status=409)
        rows = conn.execute(
            "SELECT v.id FROM plan_versions v WHERE v.plan_id=? AND NOT EXISTS "
            "(SELECT 1 FROM plan_versions c WHERE c.parent_version_id=v.id)",
            (plan_id,)).fetchall()
    if len(rows) != 1:
        raise StorageError("plan has no unique current version",
                           code="version_conflict", http_status=409)
    if rows[0][0] != expected:
        raise StorageError("expected current version is stale",
                           code="version_conflict", http_status=409)
    return rows[0][0]


def _current_tip(store: Any, plan_id: str) -> Optional[str]:
    """The unique childless version of a plan, or None when it is not unique."""
    with store._transaction() as conn:
        rows = conn.execute(
            "SELECT v.id FROM plan_versions v WHERE v.plan_id=? AND NOT EXISTS "
            "(SELECT 1 FROM plan_versions c WHERE c.parent_version_id=v.id)",
            (plan_id,)).fetchall()
    return rows[0][0] if len(rows) == 1 else None


class StorageError(RuntimeError):
    """A storage-seam refusal carrying its contract-defined code and status."""

    def __init__(self, message: str, *, code: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.payload: dict = {}

    def with_payload(self, payload: dict) -> "StorageError":
        """Carry the current authority back with a refusal.

        §6 requires every error response to include the current authority
        receipt, so a refused caller can resynchronise without a second
        round-trip guessing at what changed underneath it.
        """
        self.payload = payload
        return self


def _stale_authority(message: str) -> StorageError:
    return StorageError(message, code="stale_authority", http_status=412)


#: The fields every §6 write carries to prove which authority it acts under, as
#: opposed to what it is asking for.  They are deliberately excluded from the
#: action fingerprint below: all four change when the caller resynchronises, so a
#: fingerprint that included them could never report "same request, same body"
#: across the resync that the contract requires the caller to perform.
_AUTHORITY_PROOF_FIELDS = ("request_id", "authority_receipt",
                           "expected_generation", "legacy_digest")


#: The durable object each endpoint's external key actually creates. Recording
#: `plan_id` for every kind was wrong for `plan-version`: the object that key
#: creates is a `ver_*`, and the contract (2179-2188) requires a refusal to carry
#: "the id that key already produced". A duplicate plan-version replay reported the
#: *plan* id, which is an object the key did not create and which the caller
#: already had — useless for reconciling, and misleading about what exists.
_PRIMARY_OBJECT_FIELD = {
    "plan": "plan_id",
    "plan-version": "plan_version_id",
    "plan-duplicate": "plan_id",
    "plan-status": "plan_id",
    "draft": "plan_id",
    "observation": "operation_id",
}


def _primary_object_id(kind: str, result: dict):
    field = _PRIMARY_OBJECT_FIELD.get(kind)
    if field is None:
        raise StorageError(f"no primary object field is defined for {kind}",
                           code="invalid_projection", http_status=422)
    return result.get(field)


def _action_fingerprint(kind: str, body: dict) -> str:
    """Hash what the caller is asking for, not the epoch it asked under."""
    return RECOVERY._sha256_json({
        "format": "fire-storage-action-v1",
        "kind": kind,
        "action": {key: value for key, value in body.items()
                   if key not in _AUTHORITY_PROOF_FIELDS},
    })


def _sync_payload(state: dict, **extra: Any) -> dict:
    """What a refused caller needs in order to resynchronise, not just be told no.

    §6 requires every refusal to carry the current authority.  A 412 that says
    only "stale" leaves the caller guessing what it is now stale against, which
    costs a round-trip and invites it to retry blindly.
    """
    payload = {
        "authority_status": state["authority_status"],
        "generation_id": state["generation_id"],
        "authority_receipt": state["receipt_sha256"],
        "legacy_digest_last_seen": state["legacy_digest_last_seen"],
    }
    payload.update(extra)
    return payload


class StorageSeam:
    """Read-only view of the archive under an explicit authority proof."""

    def __init__(self, recovery_manager: "RECOVERY.BackupRestoreManager"):
        self.recovery = recovery_manager
        self.journal = recovery_manager.journal
        self._lock = recovery_manager._lock

    # ------------------------------------------------------------- authority

    def _authority_state(self) -> dict:
        # Same seeding/reconciliation entry the existing authority seam uses.
        # This is not a mutation in the sense §6 forbids: it seeds the control
        # row on a first-ever open and reconciles a surviving archive, which is
        # precisely what a startup read is for.  It records no observation,
        # allocates no generation, and writes no archive byte.
        snapshot = self.recovery._bootstrap()
        authority = snapshot.get("authority") or {}
        generation = snapshot.get("generation") or {}
        receipt = None
        operation_id = authority.get("operation_id")
        if operation_id:
            events = self.journal.authority_events_for_operation(operation_id)
            if events:
                # The latest event for the owning operation is the receipt the
                # contract names; events are already ordered by created_at.
                receipt = events[-1]["receipt_sha256"]
        return {
            "authority_status": authority.get("status"),
            "generation_id": generation.get("generation_id"),
            "receipt_sha256": receipt,
            "legacy_digest_last_seen": authority.get("legacy_digest_last_seen"),
            "fence_state": self._fence_state(),
        }

    def _fence_state(self) -> str:
        """Report whether any migration currently fences the legacy writers.

        Four values, and the distinction between the last two is the contract's
        rather than a convenience:

        `held`    — a verified operation with an unexpired fence.
        `invalid` — a fence id that cannot be parsed. Fails closed.
        `expired` — a verified operation whose five minutes have elapsed and that
                    nobody has retired. Contract 2222-2223: "Until then,
                    supported legacy writers remain fenced; after a failed
                    attempt they resume only under `legacy_authoritative` with a
                    newly observed digest." Expiry stops the operation being
                    *finalizable*; it does not release the writers, because the
                    page holding it may still be mid-cutover and the only thing
                    that retires it is an explicit `retry_nonce`.

                    This used to report `none`, which released the legacy writers
                    on a timer — the one release the contract does not grant.
                    `DESIGN_M4_BROWSER_CUTOVER_2026-07-25.md` line 166 said the
                    opposite ("放行"), and that line is corrected to match rather
                    than the two documents being left to disagree.
        `none`    — nothing verified holds a fence. A `retry_nonce` retry records
                    the old operation as `failed`, which is what takes it out of
                    this loop.
        """
        expired = False
        for operation in self.journal.list_operations(kind="migration"):
            fence_id = operation.get("legacy_fence_id")
            if fence_id is None or operation.get("state") != "verified":
                continue
            try:
                if RECOVERY._epoch_millis() <= RECOVERY._fence_expiry_ms(fence_id):
                    return "held"
                expired = True
            except RECOVERY.RecoveryError:
                return "invalid"
        return "expired" if expired else "none"

    def _require_read_authority(self, state: dict, *,
                                authority_receipt: Optional[str],
                                legacy_digest: Optional[str]) -> None:
        """Refuse a read whose caller cannot name the authority it is under."""
        status = state["authority_status"]
        if status == "manual_recovery_required":
            raise StorageError("archive is latched for manual recovery",
                               code="manual_recovery_required", http_status=423)
        if not isinstance(legacy_digest, str) or not RECOVERY._hex64(legacy_digest):
            # Required on every request, including the GETs: it is what makes a
            # later drift detectable rather than inferred.
            raise StorageError("a fresh legacy digest header is required",
                               code="invalid_projection", http_status=422)
        if status in ("sqlite_preferred", "source_changed"):
            expected = state["receipt_sha256"]
            if expected is None:
                raise StorageError("authority receipt is unavailable",
                                   code="manual_recovery_required",
                                   http_status=423)
            if authority_receipt != expected:
                raise _stale_authority(
                    "authority receipt does not match the current authority")
        elif authority_receipt is not None:
            # Presenting a receipt before any cutover means the caller believes
            # in an authority that does not exist yet.
            raise _stale_authority("no cutover authority has been established")

    # ------------------------------------------------------------- endpoints

    def state(self) -> dict:
        """`GET /api/storage/state` — the startup seam.  Pure, unauthenticated.

        Startup has to be able to ask "who is authoritative?" before it can
        possibly hold a receipt, so this one endpoint answers without a proof.
        It returns only the five fields §6 lists and nothing derived from the
        archive contents.
        """
        with self._lock:
            state = self._authority_state()
        return {"format": "fire-storage-state-v1", **state}

    def plans(self, *, authority_receipt: Optional[str] = None,
              legacy_digest: Optional[str] = None) -> dict:
        """`GET /api/storage/plans` — plans visible under the returned receipt."""
        with self._lock:
            state = self._authority_state()
            self._require_read_authority(
                state, authority_receipt=authority_receipt,
                legacy_digest=legacy_digest)
            if state["authority_status"] != "sqlite_preferred":
                # Before cutover the archive is not the source of truth, and
                # after drift it is not trustworthy; either way, serving rows
                # here would invite the UI to render them as current.
                raise StorageError(
                    "plans are served only under sqlite authority",
                    code="source_changed", http_status=409)
            return {"format": "fire-storage-plans-v1",
                    "authority_receipt": state["receipt_sha256"],
                    "generation_id": state["generation_id"],
                    "plans": self._read_plans()}

    def recovered_drafts(self, *, authority_receipt: Optional[str] = None,
                         legacy_digest: Optional[str] = None) -> dict:
        """`GET /api/storage/recovered-drafts` — the drafts a cutover carried over.

        The read A3 was missing. A cutover imports `fire_draft` into
        `recovered_drafts` as immutable evidence, and `POST /api/storage/draft`
        promotes one to a real Plan — but it needs a `draft_id`, and no endpoint
        in the exact seam list ever handed the browser one: the projection that
        mints draft ids is server-side and only its hash came back. So the
        endpoint existed, the product could not call it, and a user's unsaved work
        survived the migration into a place they could not reach.

        The evidence stays immutable. This is a read; promotion is still the
        explicit `POST`, still recorded as a `recovered_draft_events` row, and the
        draft row itself is never rewritten. That distinction is the whole reason
        this is a new endpoint rather than `recovered_drafts` being made writable.

        Same authority rules as `plans()`: a caller proves which receipt it is
        reading under, and rows are served only under sqlite authority — before a
        cutover there is nothing to serve, and after drift the archive is not
        trustworthy enough to render as current.

        Already-promoted drafts are excluded. Once a draft has become a Plan the
        Plan is the thing to open, and offering the draft again would invite a
        second copy of work the user has already kept.
        """
        with self._lock:
            state = self._authority_state()
            self._require_read_authority(
                state, authority_receipt=authority_receipt,
                legacy_digest=legacy_digest)
            if state["authority_status"] != "sqlite_preferred":
                raise StorageError(
                    "recovered drafts are served only under sqlite authority",
                    code="source_changed", http_status=409)
            return {"format": "fire-storage-recovered-drafts-v1",
                    "authority_receipt": state["receipt_sha256"],
                    "generation_id": state["generation_id"],
                    "recovered_drafts": self._read_recovered_drafts()}

    def _read_recovered_drafts(self) -> list[dict]:
        """Eligible, unpromoted recovered drafts, read-only."""
        path = str(self.recovery.archive_path)
        try:
            conn = PERSISTENCE._readonly_connect(path)
        except OSError as exc:
            raise StorageError("archive is unavailable",
                               code="cost_or_storage_unavailable",
                               http_status=503) from exc
        try:
            # The same validator and the same failure taxonomy as `_read_plans`.
            # A structurally invalid archive that `plans` refuses as
            # `invalid_projection` must not have its draft rows served here as
            # current, and one fault must not report under two codes depending
            # on which read the browser happened to call.
            RECOVERY.validate_archive_connection(conn)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT d.draft_id, d.source_key, d.created_at, "
                "       d.normalized_json, d.raw_record_sha256 "
                "FROM recovered_drafts d "
                # `status='recovered'` and not quarantined: a quarantined draft
                # never becomes a Plan, which is the point of quarantining it.
                "WHERE d.status='recovered' AND NOT EXISTS ("
                "  SELECT 1 FROM recovered_draft_events e "
                "  WHERE e.draft_id = d.draft_id) "
                "ORDER BY d.created_at, d.draft_id").fetchall()
            out = []
            for row in rows:
                try:
                    config = json.loads(row["normalized_json"])
                except (TypeError, ValueError):
                    # Unparseable evidence is not offered as openable. It is
                    # still retained — §7 can retrieve the raw bytes — but
                    # presenting it as a draft the user can resume would be a
                    # promise this cannot keep.
                    continue
                out.append({"draft_id": row["draft_id"],
                            "source_key": row["source_key"],
                            "created_at": row["created_at"],
                            "raw_record_sha256": row["raw_record_sha256"],
                            "normalized_config": config})
            return out
        except (PERSISTENCE.PersistenceError, RECOVERY.RecoveryError,
                sqlite3.Error) as exc:
            raise StorageError("archive state is not readable",
                               code="invalid_projection",
                               http_status=422) from exc
        finally:
            conn.close()

    def observe(self, body: Any) -> dict:
        """`POST /api/storage/observe` — the sole digest-observation seam.

        Split out of the reads on purpose: this is the only call that can move
        authority away from `sqlite_preferred`, and §6 keeps it an explicit,
        idempotent POST so that no read can ever have that power.
        """
        required = {"request_id", "authority_receipt", "expected_generation",
                    "legacy_digest"}
        if not isinstance(body, dict) or set(body) != required:
            raise StorageError("observation fields are invalid",
                               code="invalid_projection", http_status=422)
        digest = body["legacy_digest"]
        if not isinstance(digest, str) or not RECOVERY._hex64(digest):
            raise StorageError("observation digest is invalid",
                               code="invalid_projection", http_status=422)
        with self._lock:
            state = self._authority_state()
            if state["authority_status"] == "manual_recovery_required":
                raise StorageError("archive is latched for manual recovery",
                                   code="manual_recovery_required",
                                   http_status=423)
            if state["authority_status"] != "sqlite_preferred":
                raise StorageError("observation requires sqlite authority",
                                   code="source_changed", http_status=409)
            if body["authority_receipt"] != state["receipt_sha256"]:
                raise _stale_authority(
                    "authority receipt does not match the current authority"
                ).with_payload(_sync_payload(state))
            if body["expected_generation"] != state["generation_id"]:
                raise _stale_authority(
                    "expected generation does not match the current generation"
                ).with_payload(_sync_payload(state))
            # A drifting observation materialises an operation, an event, and a
            # new epoch, so its request id is spendable in exactly the way a
            # write's is.  A matching one creates nothing, which is why the
            # ledger is only consulted and written on the drift branch — a polled
            # seam must not burn a key for agreeing.
            self._require_unspent_request_id("observation", body, state)
            try:
                result = self.journal.record_observation(
                    idempotency_key=RECOVERY._sha256_json({
                        "kind": "observation",
                        "request_id": body["request_id"],
                        "expected_generation": body["expected_generation"],
                        "authority_receipt": body["authority_receipt"],
                        "legacy_digest": digest,
                    }),
                    request_fingerprint=RECOVERY._sha256_json(body),
                    legacy_digest=digest)
            except RECOVERY.RecoveryConflict as exc:
                raise StorageError(str(exc), code="idempotency_conflict",
                                   http_status=409).with_payload(
                                       _sync_payload(state)) from exc
            after = self._authority_state()
            if result["drift"]:
                # The observation seam is the one caller allowed to record this
                # in a transaction of its own: its entire effect *is* the control
                # journal, so there are no archive bytes that could outlive the
                # record. Everything that does commit bytes goes through
                # prepare/complete instead.
                self.journal.record_external_request(
                    request_kind="observation",
                    external_request_id=body["request_id"],
                    body_fingerprint=_action_fingerprint("observation", body),
                    observed_generation=after["generation_id"],
                    observed_authority_receipt=after["receipt_sha256"],
                    object_id=_primary_object_id("observation", result))
        response = {"format": "fire-storage-observation-v1",
                    "request_id": body["request_id"],
                    "drift": result["drift"],
                    "authority_status": after["authority_status"],
                    "generation_id": after["generation_id"],
                    "authority_receipt": after["receipt_sha256"]}
        if result["drift"]:
            # Drift is reported as a refusal, not as a successful observation:
            # the caller must stop writing, and a 200 invites it not to.
            raise StorageError("legacy source changed after cutover",
                               code="source_changed",
                               http_status=409).with_payload(response)
        return response

    # ----------------------------------------------------------- write path

    def _require_write_authority(self, state: dict, body: dict) -> None:
        """Every §6 write proves authority, generation, and a fresh digest."""
        status = state["authority_status"]
        if status == "manual_recovery_required":
            raise StorageError("archive is latched for manual recovery",
                               code="manual_recovery_required", http_status=423)
        if status == "source_changed":
            # §6: source_changed permits read-only recovery views and rejects
            # every write until a new fresh-envelope operation repairs it.
            raise StorageError("legacy source changed after cutover",
                               code="source_changed", http_status=409)
        if status != "sqlite_preferred":
            raise StorageError("writes require sqlite authority",
                               code="source_changed", http_status=409)
        if body["authority_receipt"] != state["receipt_sha256"]:
            raise _stale_authority(
                "authority receipt does not match the current authority"
            ).with_payload(_sync_payload(state))
        if body["expected_generation"] != state["generation_id"]:
            raise _stale_authority(
                "expected generation does not match the current generation"
            ).with_payload(_sync_payload(state))
        if body["legacy_digest"] != state["legacy_digest_last_seen"]:
            # Drift is reported by the observation seam, never absorbed by a
            # write that happens to notice it.
            raise StorageError("legacy digest does not match the finalized source",
                               code="source_changed",
                               http_status=409).with_payload(_sync_payload(state))

    def _require_unspent_request_id(self, kind: str, body: dict,
                                    state: dict) -> None:
        """One external request id, one action — across every resynchronisation.

        This is checked *after* the authority proof on purpose, so the sequence
        the contract describes is the sequence a caller sees: a retry under the
        authority it still holds is stale before it is duplicate and gets 412
        with the current receipt; once it has resynchronised and is no longer
        stale, the same request id is refused as the duplicate it always was.

        Both refusals are 409 `idempotency_conflict`, distinguished by
        `idempotency_state`, and both carry the object the id already produced so
        the caller can reconcile without guessing.  A caller that genuinely
        intends a second action issues a second `Idempotency-Key`; that is what
        makes the intent explicit rather than inferred from a hash of the body.
        """
        prior = self.journal.find_external_request(kind, body["request_id"])
        if prior is None:
            return
        replayed = prior["body_fingerprint"] == _action_fingerprint(kind, body)
        raise StorageError(
            "external request id has already been used",
            code="idempotency_conflict", http_status=409
        ).with_payload(_sync_payload(
            state,
            idempotency_state="replayed" if replayed else "body_conflict",
            request_id=body["request_id"],
            existing_object_id=prior["object_id"]))

    def _write(self, kind: str, body: dict, mutate: Any, *,
               close_store: Any = None, reopen_store: Any = None) -> dict:
        """Commit one §6 write as a single archive_write transaction.

        The archive is copied to a staging image, mutated there, measured, and
        only then swapped in under a prebound generation. Nothing writes the
        live file in place: the same prepare/apply seam the cutover uses gives
        every ordinary save the same rollback and readback guarantees.
        """
        with self._lock:
            state = self._authority_state()
            self._require_write_authority(state, body)
            self._require_unspent_request_id(kind, body, state)
            # Two keys, two jobs, and the internal one identifies an *attempt*.
            #
            # It hashes the epoch and the whole body — that is what makes the
            # archive-write CAS refuse to land on a generation it was not
            # computed against — plus the staged image's own logical identity,
            # which is what makes it per-attempt. Without that last component a
            # write that failed and rolled back left an operation row holding the
            # key, `control_operations` is UNIQUE on both the key and the request
            # fingerprint, and the honest retry was refused as a fingerprint
            # conflict for the rest of time. A rolled-back attempt left nothing
            # durable, so it must not consume the caller's ability to try again.
            #
            # Deduplication is not this key's job and never was: the external key
            # below is what refuses a duplicate, and it is epoch-free precisely so
            # that it cannot be defeated by a resynchronisation.
            object_key = self.journal.external_object_key(
                kind, body["request_id"])
            staging_root = self.recovery.support_root / ".storage-write-staging"
            RECOVERY._secure_dir(staging_root)
            staged = staging_root / (object_key[:32] + ".sqlite3")
            if staged.exists():
                staged.unlink()
            RECOVERY._sqlite_backup(self.recovery.archive_path, staged)
            try:
                return self._staged_write(kind, body, mutate, object_key,
                                          staged, state, close_store,
                                          reopen_store)
            finally:
                # A refused write is an ordinary outcome here — a stale tab or a
                # failed version CAS — and every one of them would otherwise
                # leave a full copy of the archive behind. The successful path
                # has already moved the file, so this only ever cleans up what
                # was not consumed.
                RECOVERY._remove_sidecars(staged)
                staged.unlink(missing_ok=True)
                # The writer lock is a separate file from the database and its
                # sidecars, so it outlives both unless it is named explicitly.
                pathlib.Path(str(staged) + ".lock").unlink(missing_ok=True)

    @staticmethod
    def _attempt_key(kind: str, body: dict, staged_logical: str) -> str:
        """The internal CAS identity of one attempt at one write."""
        return RECOVERY._sha256_json({
            "format": "fire-storage-attempt-v1",
            "kind": kind, "request_id": body["request_id"],
            "expected_generation": body["expected_generation"],
            "authority_receipt": body["authority_receipt"],
            "body": body, "staged_logical_sha256": staged_logical})

    def _staged_write(self, kind, body, mutate, object_key,
                      staged, state, close_store, reopen_store) -> dict:
            try:
                store = PERSISTENCE.PersistenceStore(
                    str(staged), app_release_id="fire-modeling-3.0")
            except PERSISTENCE.PersistenceError as exc:
                raise StorageError("staged archive is not writable",
                                   code="cost_or_storage_unavailable",
                                   http_status=503) from exc
            try:
                replayed, result = mutate(store, object_key)
            finally:
                store.close()
            if replayed:
                # Last-resort backstop beneath two refusals: the authority check
                # refuses a stale retry, and the request-id ledger refuses a
                # resynchronised one.  Reaching here means an object derived from
                # this external key already exists — so this resolves to it and
                # commits nothing, rather than twinning.  Nothing is recorded
                # here: the ledger is now written by the transaction that makes an
                # archive write succeed, and this path performs no archive write.
                return {"request_id": body["request_id"],
                        "operation_id": None,
                        "generation_id": state["generation_id"],
                        "authority_receipt": state["receipt_sha256"], **result}
            RECOVERY._checkpoint_delete_journal(staged)
            new_logical = RECOVERY.logical_identity(str(staged))
            # Derived only now, because the staged identity is what makes it name
            # this attempt rather than every attempt at this request.
            attempt_key = self._attempt_key(kind, body, new_logical)
            prepared = self.recovery.prepare_archive_write(
                idempotency_key=attempt_key,
                request_fingerprint=attempt_key,
                new_logical_sha256=new_logical,
                staged_db_sha256=RECOVERY._sha256(
                    RECOVERY._read_regular(staged, RECOVERY.MAX_ARCHIVE_BYTES)),
                staging_path=str(staged),
                # Carried into the child's durable payload before the bytes land,
                # and spent by the same transaction that makes them durable.
                # Recording it afterwards left a window in which the object
                # existed and the record of who asked for it did not, and a
                # caller who hit that window got a second object on retry.
                external_request={
                    "request_kind": kind,
                    "request_id": body["request_id"],
                    "body_fingerprint": _action_fingerprint(kind, body),
                    "object_id": _primary_object_id(kind, result),
                })
            self.recovery.apply_archive_write(
                prepared["operation_id"],
                lambda path: os.replace(str(staged), str(path)),
                close_store=close_store, reopen_store=reopen_store)
            after = self._authority_state()
            return {"request_id": body["request_id"],
                    "operation_id": prepared["operation_id"],
                    "generation_id": after["generation_id"],
                    "authority_receipt": after["receipt_sha256"], **result}

    def create_plan(self, body: Any, *, close_store: Any = None,
                    reopen_store: Any = None) -> dict:
        """`POST /api/storage/plan` — one Plan plus its immutable first version."""
        required = {"request_id", "authority_receipt", "expected_generation",
                    "legacy_digest", "plan"}
        if not isinstance(body, dict) or set(body) != required:
            raise StorageError("plan fields are invalid",
                               code="invalid_projection", http_status=422)
        plan = body["plan"]
        if (not isinstance(plan, dict)
                or not set(plan) <= {"display_name", "source_key", "normalized_config"}
                or not isinstance(plan.get("normalized_config"), dict)):
            raise StorageError("plan payload is invalid",
                               code="invalid_projection", http_status=422)

        def mutate(store, object_key):
            # Derived from the stable external request key, not from the internal
            # CAS key and not randomly.  The internal key hashes the epoch, so
            # the same request id named a different plan once the caller had
            # resynchronised — which is how a retry became a twin.  Existence of
            # this id is the last-resort replay check beneath the request-id
            # ledger, so no separate response record has to be kept.
            plan_id = "plan_" + object_key[:32]
            existing = store.timeline(plan_id) if _plan_exists(store, plan_id) else None
            if existing is not None:
                return True, {"plan_id": plan_id,
                              "plan_version_id": _current_tip(store, plan_id),
                              "current_version_id": _current_tip(store, plan_id)}
            store.create_plan(plan.get("display_name") or "Untitled plan",
                              source_key=plan.get("source_key"), plan_id=plan_id)
            version = store.create_plan_version(
                plan_id, plan["normalized_config"], plan["normalized_config"],
                source_kind="user")
            version_id = version.get("id") or version.get("version_id")
            return False, {"plan_id": plan_id, "plan_version_id": version_id,
                           "current_version_id": version_id}

        return self._write("plan", body, mutate,
                           close_store=close_store, reopen_store=reopen_store)

    def _version_body(self, body: Any, extra: set) -> dict:
        base = {"request_id", "authority_receipt", "expected_generation",
                "legacy_digest"}
        if not isinstance(body, dict) or set(body) != base | extra:
            raise StorageError("request fields are invalid",
                               code="invalid_projection", http_status=422)
        return body

    def create_plan_version(self, body: Any, **stores: Any) -> dict:
        """`POST /api/storage/plan-version` — one immutable child version."""
        body = self._version_body(body, {"plan_id", "expected_current_version_id",
                                         "source_config", "normalized_config"})

        def mutate(store, object_key):
            # The version id is derived from the stable external request key, so
            # a duplicate request names the row it already created instead of
            # inserting a second one beside it. Without this, plan-version was
            # the one endpoint with no structural backstop at all: `plan`,
            # `plan-duplicate` and `draft` all derived their plan id, but this one
            # took a fresh uuid, so any gap in the ledger check produced a twin.
            version_id = "ver_" + object_key[:32]
            existing = _version_exists(store, version_id)
            if existing is not None:
                return True, {"plan_id": existing,
                              "plan_version_id": version_id,
                              "current_version_id": version_id}
            tip = _require_tip(store, body["plan_id"],
                               body["expected_current_version_id"])
            version = store.create_plan_version(
                body["plan_id"], body["source_config"],
                body["normalized_config"], source_kind="user",
                parent_version_id=tip, version_id=version_id)
            created = version.get("id") or version.get("version_id")
            return False, {"plan_id": body["plan_id"],
                           "plan_version_id": created,
                           "current_version_id": created}

        return self._write("plan-version", body, mutate, **stores)

    def duplicate_plan(self, body: Any, **stores: Any) -> dict:
        """`POST /api/storage/plan-duplicate` — a new Plan with a copied version."""
        body = self._version_body(body, {"source_plan_id",
                                         "expected_current_version_id",
                                         "display_name"})

        def mutate(store, object_key):
            tip = _require_tip(store, body["source_plan_id"],
                               body["expected_current_version_id"])
            plan_id = "plan_" + object_key[:32]
            if _plan_exists(store, plan_id):
                return True, {"plan_id": plan_id,
                              "plan_version_id": _current_tip(store, plan_id),
                              "current_version_id": _current_tip(store, plan_id)}
            with store._transaction() as conn:
                source = conn.execute(
                    "SELECT source_config_json, normalized_config_json "
                    "FROM plan_versions WHERE id=?", (tip,)).fetchone()
            if source is None:
                raise StorageError("source version is missing",
                                   code="version_conflict", http_status=409)
            store.create_plan(body.get("display_name") or "Copy",
                              plan_id=plan_id)
            version = store.create_plan_version(
                plan_id, json.loads(source["source_config_json"]),
                json.loads(source["normalized_config_json"]),
                source_kind="duplicate")
            version_id = version.get("id") or version.get("version_id")
            return False, {"plan_id": plan_id, "plan_version_id": version_id,
                           "current_version_id": version_id}

        return self._write("plan-duplicate", body, mutate, **stores)

    def set_plan_status(self, body: Any, **stores: Any) -> dict:
        """`POST /api/storage/plan-status` — archive or soft-delete a Plan.

        §6 makes Plan status the only deletion boundary: no row is ever removed
        and `plan_versions` has no deleted column, so the tombstone lives in one
        place and history stays readable behind it.
        """
        body = self._version_body(body, {"plan_id", "expected_current_version_id",
                                         "status"})
        if body["status"] not in ("active", "archived", "deleted"):
            raise StorageError("plan status is invalid",
                               code="invalid_projection", http_status=422)

        def mutate(store, object_key):
            tip = _require_tip(store, body["plan_id"],
                               body["expected_current_version_id"])
            with store._transaction() as conn:
                current = conn.execute("SELECT status FROM plans WHERE id=?",
                                       (body["plan_id"],)).fetchone()[0]
                if current == body["status"]:
                    return True, {"plan_id": body["plan_id"],
                                  "current_version_id": tip,
                                  "status": body["status"]}
                conn.execute("UPDATE plans SET status=? WHERE id=?",
                             (body["status"], body["plan_id"]))
            return False, {"plan_id": body["plan_id"],
                           "current_version_id": tip,
                           "status": body["status"]}

        return self._write("plan-status", body, mutate, **stores)

    def save_draft(self, body: Any, **stores: Any) -> dict:
        """`POST /api/storage/draft` — promote a recovered draft to a real Plan.

        A migration imports `fire_draft` as evidence, never as a Plan: §6 is
        explicit that it "is not merged into an existing Plan; user save creates
        a new explicit Plan/PlanVersion through the server seam". This is that
        seam. The draft row itself is untouched — the promotion is recorded as
        an append-only `user_saved` event beside it, so the evidence of what was
        imported survives what the user later did with it.
        """
        body = self._version_body(body, {"draft_id", "normalized_config",
                                         "display_name"})
        if not isinstance(body["normalized_config"], dict):
            raise StorageError("draft config is invalid",
                               code="invalid_projection", http_status=422)

        def mutate(store, object_key):
            plan_id = "plan_" + object_key[:32]
            if _plan_exists(store, plan_id):
                tip = _current_tip(store, plan_id)
                return True, {"plan_id": plan_id, "plan_version_id": tip,
                              "current_version_id": tip, "draft_id": body["draft_id"]}
            with store._transaction() as conn:
                draft = conn.execute(
                    "SELECT draft_id, operation_id, status FROM recovered_drafts "
                    "WHERE draft_id=?", (body["draft_id"],)).fetchone()
            if draft is None:
                raise StorageError("recovered draft is unknown",
                                   code="invalid_projection", http_status=422)
            if draft["status"] != "recovered":
                # A quarantined draft never becomes a Plan; that is the whole
                # point of quarantining it.
                raise StorageError("recovered draft is not eligible",
                                   code="invalid_projection", http_status=422)
            store.create_plan(body.get("display_name") or "Recovered draft",
                              plan_id=plan_id)
            version = store.create_plan_version(
                plan_id, body["normalized_config"], body["normalized_config"],
                source_kind="draft")
            version_id = version.get("id") or version.get("version_id")
            with store._transaction() as conn:
                conn.execute(
                    "INSERT INTO recovered_draft_events(event_id,draft_id,"
                    "migration_operation_id,control_operation_id,status,"
                    "target_plan_id,target_plan_version_id,target_hash,"
                    "receipt_sha256,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                    # The external object key, not the per-attempt internal one:
                    # this event is the durable record of *which request* promoted
                    # the draft, and an attempt identity would change on a retry
                    # of the same request.
                    ("rde_" + object_key[:32], draft["draft_id"],
                     draft["operation_id"], object_key, "user_saved",
                     plan_id, version_id,
                     # Not a free-form hash: the v7 validator recomputes this
                     # from the two logical row hashes, so the event is bound to
                     # the exact rows it claims rather than to their ids.
                     RECOVERY._sha256_json({
                         "plan_row_hash": RECOVERY._logical_row_hash(
                             conn, "plans", plan_id),
                         "plan_version_row_hash": RECOVERY._logical_row_hash(
                             conn, "plan_versions", version_id)}),
                     object_key, PERSISTENCE.utc_now()))
            return False, {"plan_id": plan_id, "plan_version_id": version_id,
                           "current_version_id": version_id,
                           "draft_id": draft["draft_id"]}

        return self._write("draft", body, mutate, **stores)

    def _read_plans(self) -> list[dict]:
        """Read plans and their unique current version tip, read-only."""
        path = str(self.recovery.archive_path)
        try:
            conn = PERSISTENCE._readonly_connect(path)
        except OSError as exc:
            raise StorageError("archive is unavailable",
                               code="cost_or_storage_unavailable",
                               http_status=503) from exc
        try:
            RECOVERY.validate_archive_connection(conn)
            rows = conn.execute(
                "SELECT id, display_name, status, created_at FROM plans "
                "ORDER BY created_at, id").fetchall()
            plans = []
            for row in rows:
                tips = conn.execute(
                    "SELECT v.id FROM plan_versions v WHERE v.plan_id=? AND NOT EXISTS "
                    "(SELECT 1 FROM plan_versions c WHERE c.parent_version_id=v.id)",
                    (row["id"],)).fetchall()
                if len(tips) != 1:
                    # Zero or several tips is never resolved by guessing at
                    # created_at; §6 makes it an explicit conflict.
                    raise StorageError(
                        "plan version lineage has no unique current tip",
                        code="version_conflict", http_status=409)
                # The current tip's config travels with the plan. §6 defines this
                # seam as where the browser reads plans "instead of
                # localStorage", and a list without configs does not satisfy
                # that: the UI cannot render a plan row or open a plan without
                # the config, so it would have to keep reading the legacy key
                # after cutover — exactly what the cutover forbids. Still a pure
                # read: no generation, no event, no archive byte.
                version = conn.execute(
                    "SELECT normalized_config_json, source_config_json, "
                    "       config_schema_version, created_at "
                    "  FROM plan_versions WHERE id=?", (tips[0]["id"],)).fetchone()
                if version is None:
                    raise StorageError(
                        "plan version lineage names a version that is missing",
                        code="version_conflict", http_status=409)
                plans.append({"plan_id": row["id"],
                              "display_name": row["display_name"],
                              "status": row["status"],
                              "current_version_id": tips[0]["id"],
                              "created_at": row["created_at"],
                              "normalized_config": json.loads(
                                  version["normalized_config_json"]),
                              "config_schema_version":
                                  int(version["config_schema_version"]),
                              "version_created_at": version["created_at"]})
            return plans
        except (PERSISTENCE.PersistenceError, RECOVERY.RecoveryError,
                sqlite3.Error) as exc:
            raise StorageError("archive state is not readable",
                               code="invalid_projection",
                               http_status=422) from exc
        finally:
            conn.close()
