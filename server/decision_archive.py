"""Phase 4 · the seam that gives a DecisionPacket somewhere to live.

ROADMAP 4.0 Phase 4 could not start because of this file's absence. The
decision review view -- "what you decided last year versus what actually
happened" -- was missing its first half: a packet existed only inside
`app.py`'s in-memory job table, and `decision_packet.set_choice_state` moved a
dict that died when the process did. The user ruled on 2026-08-14 that packets
land in the archive, `choice_state` included. This is where that happens.

Shaped after `checkin_seam.CheckinSeam`, deliberately: this module owns
validation and refusal, `app.py` owns routing and status codes, and every
write goes through the archive writer when a control journal owns the archive.
A decision record is an archive mutation like any other.

Three things about the shape, each of which is a decision rather than an
accident.

**The body and the state are separate tables.** The packet body is immutable
and the state ledger is append-only, and the current state is recomputed from
the ledger on read. Storing a `state` column beside the history it summarises
is how a record ends up saying `chosen` while its own history disagrees. This
is the same reasoning `persistence._v9_table_statements` gives for not
materialising `flow_line_v2`.

**Legality is not reimplemented here.** `set_state` reconstructs the packet,
calls the real `decision_packet.set_choice_state`, and archives the transition
that function produced. There is exactly one table of legal transitions in
this codebase and it stays in the module that owns the concept. The schema
enforces something different and complementary: that the history is an
unbroken chain and that `superseded` is final.

**It refuses when there is no plan to attach to.** A packet is a decision
about a specific archived plan version, and the foreign keys say so. A user
who has never done a formal run gets a named refusal, not a decision record
floating free of the plan it was computed from.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any, Callable, Optional

import decision_packet as DP
import persistence as PERSISTENCE

#: Packet fields a caller must supply. Anything else in the packet is carried
#: through untouched -- the body is stored whole so it can be re-read years
#: later by code that does not exist yet.
REQUIRED_PACKET_FIELDS = ("format", "question_id", "goal", "protocol",
                          "alternatives", "review_months")

#: How many packets a history call will carry. A local archive accumulates a
#: decision at a time, not a stream; this is a bound on a response, and the
#: response says when it bit rather than quietly returning a prefix.
MAX_PACKETS = 200


class DecisionArchiveError(RuntimeError):
    """A decision-archive refusal carrying its code and HTTP status."""

    def __init__(self, message: str, *, code: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _invalid(message: str) -> DecisionArchiveError:
    return DecisionArchiveError(message, code="invalid_request",
                                http_status=400)


def _unprocessable(message: str, code: str) -> DecisionArchiveError:
    """A well-formed request the archive will not answer.

    422 rather than 400: nothing is wrong with the request, something is
    missing from the archive. The front end should offer a formal run for
    this and a corrected form for a 400.
    """
    return DecisionArchiveError(message, code=code, http_status=422)


def _require_str(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid("%s must be a non-empty string" % key)
    return value.strip()


def packet_identity(plan_version_id: str, body: dict) -> tuple:
    """The archived id of a packet, and the digest of what will be stored.

    Derived from the content rather than allocated, so archiving the same
    packet twice is the same row instead of two records of one decision -- a
    user who clicks the button again has not made a second decision. The
    plan version is mixed in because the same analysis against a different
    archived plan is a different record.
    """
    text = PERSISTENCE.canonical_json_text(body)
    body_sha256 = hashlib.sha256(text.encode("utf-8")).hexdigest()
    packet_id = "dpk_" + hashlib.sha256(
        ("%s:%s" % (plan_version_id, body_sha256)).encode("utf-8")
    ).hexdigest()[:24]
    return packet_id, body_sha256, text


def strip_choice_state(packet: dict) -> dict:
    """The packet as it is stored: everything except its choice state.

    `choice_state` is the event ledger's business. Keeping a copy inside the
    immutable body would freeze the decision at the moment it was archived --
    the state it is guaranteed to be in, `open` -- and leave a stale field
    beside a live one for every reader to choose wrongly between.
    """
    return {key: value for key, value in packet.items()
            if key != "choice_state"}


def choice_state_from_events(rows) -> dict:
    """Rebuild `choice_state` from the ledger, in the in-memory shape.

    Same three keys `decision_packet.set_choice_state` writes, so a caller
    cannot tell an archived packet from a live one by its shape -- which is
    what lets the page render either with one function.
    """
    history = [{"from": row["from_state"], "to": row["to_state"],
                "reason": row["reason"], "at": row["at"]} for row in rows]
    if not history:
        return {"state": DP.OPEN, "reason": "", "history": []}
    return {"state": history[-1]["to"], "reason": history[-1]["reason"],
            "history": history}


class DecisionArchiveSeam:
    """Archiving, reading back and deciding, over one archive.

    `write` is the archive-write callable -- `(key, mutate) -> result` -- so
    that under a control journal every decision row lands through the same
    stage/measure/prebind/swap path a formal run uses, and without one the
    direct path is preserved unchanged.
    """

    def __init__(self, store: Any,
                 write: Optional[Callable[[str, Callable], Any]] = None):
        self.store = store
        self._write = write

    # ------------------------------------------------------------- plumbing

    def _archive_write(self, key: str, mutate: Callable[[Any], Any]) -> Any:
        if self._write is None:
            return mutate(self.store)
        return self._write(key, mutate)

    @staticmethod
    def _conn(store: Any) -> sqlite3.Connection:
        conn = store._connect()
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _has_tables(conn: sqlite3.Connection) -> bool:
        return conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='decision_packets'").fetchone() is not None

    def _require_v10(self, conn: sqlite3.Connection) -> None:
        if not self._has_tables(conn):
            raise _unprocessable(
                "this archive has no decision record yet; archiving a decision "
                "installs it",
                "decision_archive_unavailable")

    def _install(self, store: Any, conn: sqlite3.Connection) -> bool:
        """Bring an older archive up to v10, additively, or do nothing.

        Copied in spirit from `checkin_seam._install_ledger`, including the
        part that was learned the hard way there: start from where the archive
        actually is rather than always at the bottom of the chain, because an
        archive that arrived by migration sits at v8 and one that has recorded
        a check-in sits at v9. Each installer refuses anything that is not at
        its own predecessor, so selecting by current version applies that
        guard in the right order instead of weakening it.

        It happens inside the write rather than at startup for the same two
        reasons: a migration is a durable archive mutation and belongs inside
        the writer's transaction, and a user who never archives a decision
        never has their archive touched.
        """
        if self._has_tables(conn):
            return False
        release = getattr(store, "app_release_id", "fire-modeling-3.0")
        try:
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            for expected_before, install in (
                    (6, PERSISTENCE.PersistenceStore.install_v7_schema),
                    (7, PERSISTENCE.PersistenceStore.install_v8_schema),
                    (8, PERSISTENCE.PersistenceStore.install_v9_schema),
                    (9, PERSISTENCE.PersistenceStore.install_v10_schema)):
                if current <= expected_before:
                    install(conn, app_release_id=release)
                    current = conn.execute("PRAGMA user_version").fetchone()[0]
        except PERSISTENCE.PersistenceError as exc:
            conn.rollback()
            raise _unprocessable(
                "this archive cannot take the decision record: %s" % exc,
                "decision_archive_unavailable") from None
        return True

    # -------------------------------------------------------------- writing

    @staticmethod
    def _validate_packet(packet: Any) -> dict:
        """Every refusal below is by name, including the shape ones.

        The type checks are not decoration. Without them a `goal` that is a
        list reaches `.get("question")` and a `paths` of `"lots"` reaches
        `int()`, and both leave this module as an unhandled exception -- a 500
        from a seam whose entire contract is that it refuses by name and says
        which field. The page cannot send either; something that is not the
        page can.
        """
        if not isinstance(packet, dict):
            raise _invalid("packet must be an object")
        missing = [key for key in REQUIRED_PACKET_FIELDS if key not in packet]
        if missing:
            raise _invalid("packet is missing %s" % ", ".join(missing))
        if not isinstance(packet.get("goal"), dict):
            raise _invalid("packet.goal must be an object")
        protocol = packet.get("protocol")
        if not isinstance(protocol, dict):
            raise _invalid("packet.protocol must be an object")
        for key in ("precision", "paths", "seed", "engine_version"):
            if protocol.get(key) in (None, ""):
                raise _invalid(
                    "packet.protocol.%s is required: an archived packet whose "
                    "numbers cannot be reproduced is not a record of anything"
                    % key)
        for holder, name, key in ((protocol, "packet.protocol.paths", "paths"),
                                  (protocol, "packet.protocol.seed", "seed"),
                                  (packet, "packet.review_months",
                                   "review_months")):
            value = holder.get(key)
            # `bool` is an `int` in Python and would sail through into a
            # column that means something numeric.
            if isinstance(value, bool) or not isinstance(value, int):
                raise _invalid("%s must be an integer" % name)
        if int(packet["review_months"]) <= 0:
            raise _invalid("packet.review_months must be positive: a review "
                           "date that has already passed is not one")
        if int(protocol["paths"]) <= 0:
            raise _invalid("packet.protocol.paths must be positive")
        if protocol.get("precision") not in ("standard", "official"):
            raise _invalid(
                "a packet computed at %r precision cannot carry a Robust "
                "claim, so it is not archived as one"
                % protocol.get("precision"))
        if not protocol.get("true_tax"):
            raise _invalid(
                "an archived packet must have been computed with true tax; "
                "the approximation moves exactly the numbers a decision "
                "turns on")
        return packet

    def save(self, body: dict) -> dict:
        """Archive one packet against one archived plan version."""
        if not isinstance(body, dict):
            raise _invalid("request body must be an object")
        plan_id = _require_str(body, "plan_id")
        plan_version_id = _require_str(body, "plan_version_id")
        packet = self._validate_packet(body.get("packet"))
        stored_body = strip_choice_state(packet)
        packet_id, body_sha256, body_json = packet_identity(
            plan_version_id, stored_body)
        protocol = packet["protocol"]
        question = str(packet.get("goal", {}).get("question")
                       or packet.get("question") or packet["question_id"])

        def mutate(store):
            conn = self._conn(store)
            try:
                installed = self._install(store, conn)
                existing = conn.execute(
                    "SELECT plan_id, body_sha256 FROM decision_packets "
                    "WHERE packet_id = ?", (packet_id,)).fetchone()
                if existing is not None:
                    # The same analysis against the same plan version is the
                    # same decision. Saying so beats a duplicate-id refusal
                    # the user cannot act on.
                    conn.commit()
                    return {"packet_id": packet_id,
                            "already_archived": True,
                            "body_sha256": existing["body_sha256"],
                            "decision_archive_installed": installed}
                try:
                    conn.execute(
                        "INSERT INTO decision_packets "
                        "(packet_id, plan_id, plan_version_id, question_id, "
                        "question, packet_format, engine_version, precision, "
                        "paths, seed, true_tax, review_months, body_json, "
                        "body_sha256, created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (packet_id, plan_id, plan_version_id,
                         str(packet["question_id"]), question,
                         str(packet["format"]),
                         str(protocol["engine_version"]),
                         str(protocol["precision"]), int(protocol["paths"]),
                         int(protocol["seed"]),
                         1 if protocol.get("true_tax") else 0,
                         int(packet["review_months"]), body_json, body_sha256,
                         PERSISTENCE.utc_now()))
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    # The foreign keys are the check that matters here: a
                    # decision has to be about a plan version this archive
                    # actually holds.
                    raise _unprocessable(
                        "this archive has no such plan version to attach the "
                        "decision to; a decision is archived against the "
                        "formal run it was computed from (%s)" % exc,
                        "plan_version_unknown") from None
                conn.commit()
            finally:
                conn.close()
            return {"packet_id": packet_id, "already_archived": False,
                    "body_sha256": body_sha256,
                    "decision_archive_installed": installed}

        return self._archive_write("decision-save:" + packet_id, mutate)

    def set_state(self, body: dict) -> dict:
        """Record one decision transition against an archived packet."""
        if not isinstance(body, dict):
            raise _invalid("request body must be an object")
        packet_id = _require_str(body, "packet_id")
        state = _require_str(body, "state")
        reason = _require_str(body, "reason")
        if state not in DP._TRANSITIONS:
            # A word that is not a state at all is a malformed request (400),
            # not a move the packet refuses to make (409). Letting it through
            # would report "an open packet cannot become banana", which is
            # true and answers the wrong question.
            raise _invalid("%r is not a decision state; the states are %s"
                           % (state, ", ".join(sorted(DP._TRANSITIONS))))

        def mutate(store):
            conn = self._conn(store)
            try:
                self._require_v10(conn)
                packet = self._read(conn, packet_id)
                try:
                    # The one table of legal transitions, used rather than
                    # copied. A second copy here would be a second answer to
                    # "can this decision be walked back".
                    DP.set_choice_state(packet, state, reason=reason,
                                        at=PERSISTENCE.utc_now())
                except DP.PacketError as exc:
                    raise DecisionArchiveError(str(exc),
                                               code="illegal_transition",
                                               http_status=409) from None
                entry = packet["choice_state"]["history"][-1]
                seq = len(packet["choice_state"]["history"])
                try:
                    conn.execute(
                        "INSERT INTO decision_packet_events "
                        "(event_id, packet_id, seq, from_state, to_state, "
                        "reason, at, created_at) VALUES (?,?,?,?,?,?,?,?)",
                        ("dev_%s_%d" % (packet_id[4:], seq), packet_id, seq,
                         entry["from"], entry["to"], entry["reason"],
                         entry["at"], PERSISTENCE.utc_now()))
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    raise DecisionArchiveError(
                        "this transition does not continue the packet's "
                        "recorded history (%s)" % exc,
                        code="illegal_transition", http_status=409) from None
                conn.commit()
            finally:
                conn.close()
            return {"packet_id": packet_id,
                    "choice_state": packet["choice_state"]}

        return self._archive_write(
            "decision-state:%s:%s" % (packet_id, state), mutate)

    # -------------------------------------------------------------- reading

    def _read(self, conn: sqlite3.Connection, packet_id: str) -> dict:
        """One archived packet, with its choice state rebuilt from the ledger."""
        row = conn.execute(
            "SELECT body_json, body_sha256 FROM decision_packets "
            "WHERE packet_id = ?", (packet_id,)).fetchone()
        if row is None:
            raise DecisionArchiveError("no archived decision %s" % packet_id,
                                       code="packet_unknown", http_status=404)
        stored = json.loads(row["body_json"])
        digest = hashlib.sha256(row["body_json"].encode("utf-8")).hexdigest()
        if digest != row["body_sha256"]:
            # Immutability is enforced by trigger, so this means the file
            # itself was altered underneath the app. Reporting the packet
            # would be reporting a decision record that is not the one made.
            raise DecisionArchiveError(
                "archived decision %s does not match its recorded digest"
                % packet_id, code="packet_corrupt", http_status=409)
        events = conn.execute(
            "SELECT from_state, to_state, reason, at FROM "
            "decision_packet_events WHERE packet_id = ? ORDER BY seq",
            (packet_id,)).fetchall()
        stored["choice_state"] = choice_state_from_events(events)
        return stored

    def get(self, packet_id: str) -> dict:
        if not isinstance(packet_id, str) or not packet_id.strip():
            raise _invalid("packet_id is required")
        conn = self._conn(self.store)
        try:
            self._require_v10(conn)
            packet = self._read(conn, packet_id.strip())
        finally:
            conn.close()
        return {"packet_id": packet_id.strip(), "packet": packet}

    def history(self, plan_id: str) -> dict:
        """Every archived decision for one plan, newest first.

        Bodies are not carried: a list of decisions is a list, and each body
        holds a full baseline config. `get` fetches one.
        """
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise _invalid("plan_id is required")
        conn = self._conn(self.store)
        try:
            if not self._has_tables(conn):
                # Not a refusal: a user who has never archived a decision has
                # no decisions, which is a different thing from an error.
                return {"plan_id": plan_id, "packets": [], "truncated": False,
                        "decision_archive_installed": False}
            rows = conn.execute(
                "SELECT packet_id, plan_version_id, question_id, question, "
                "precision, paths, seed, engine_version, review_months, "
                "body_sha256, created_at FROM decision_packets "
                "WHERE plan_id = ? ORDER BY created_at DESC, packet_id DESC "
                "LIMIT ?", (plan_id, MAX_PACKETS + 1)).fetchall()
            truncated = len(rows) > MAX_PACKETS
            rows = rows[:MAX_PACKETS]
            out = []
            for row in rows:
                events = conn.execute(
                    "SELECT from_state, to_state, reason, at FROM "
                    "decision_packet_events WHERE packet_id = ? ORDER BY seq",
                    (row["packet_id"],)).fetchall()
                entry = dict(row)
                entry["choice_state"] = choice_state_from_events(events)
                out.append(entry)
        finally:
            conn.close()
        return {"plan_id": plan_id, "packets": out, "truncated": truncated,
                "decision_archive_installed": True}
