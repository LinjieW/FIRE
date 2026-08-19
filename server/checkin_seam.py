"""Phase 2 · the HTTP seam for annual check-ins and variance attribution.

Shaped after `storage_api.StorageSeam`: this module owns validation and
refusal, `app.py` owns only the routing and the status codes. Keeping the two
apart is what stops a refusal from arriving as a 500 -- and in this surface
almost everything *is* a refusal, because the attribution protocol would
rather report `unknown` than a plausible number.

Three endpoints, and the division between them is the protocol's:

  * `record` writes a check-in. The ledger tables live in the archive
    database, so this is an `archive_write` in the sense the archive seam
    means it, and it goes through the journal writer whenever one exists --
    the same two-branch shape a formal run uses.
  * `history` reads them back. Pure; it allocates no generation and writes
    nothing.
  * `attribute` decomposes one check-in. Also pure: everything it needs is
    already archived, which is the point of the model-update ruling below.

The model-update term and why this does not run the engine
----------------------------------------------------------
§4's waterfall needs `F_no`, the archived old plan re-run under the current
vintage. Re-running it is an engine job, and putting one inside an HTTP
request would be wrong for a run whose archived protocol may say twenty
thousand paths.

It is also, in the ordinary case, unnecessary. A desktop user who has not
updated the app between forecasting and reviewing is running the build that
produced the forecast, and the archive's own replay contract says that build
on those resolved inputs under that protocol reproduces a byte-identical
result. So `F_no == F_oo`, the model-update line is exactly zero, and it is
zero by proof rather than by the assumption §1.2 warns against. The response
says which of the two bases it used; it never conflates them.

When the build *has* moved, this refuses with `model_update_unavailable` and
names both builds. Reconstructing the term from today's inputs is precisely
what §1.2 forbids, so the honest answer is that the user needs to re-run --
which is an orchestration slice, not this one.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Callable, Optional

import checkin_ledger as LEDGER
import persistence as PERSISTENCE
import review_memo as MEMO
from attribution import CATEGORIES, LedgerError

#: Header fields a caller must supply. The rest are derived or defaulted here,
#: because they are properties of the archive rather than of the request.
REQUIRED_HEADER = (
    "plan_id", "plan_version_id", "forecast_period_start",
    "forecast_period_end", "opening_value_minor", "closing_value_minor",
)

#: Ledger-row fields a caller must supply per flow line.
REQUIRED_LINE = ("category", "amount_portfolio_minor", "occurred_at")

#: Fields a caller may supply per flow line. Anything else is refused by name
#: rather than dropped, so a typo in the front end is visible immediately.
OPTIONAL_LINE = (
    "transaction_id", "source_or_schedule_id", "source_event_id",
    "component_leg_id", "timing_bucket", "timing_state", "observation_state",
    "is_internal_transfer", "transfer_group_id", "absence_proof_id",
    "supersedes_transaction_id",
)

MAX_LINES_PER_SIDE = 2000


class CheckinError(RuntimeError):
    """A check-in seam refusal carrying its code and HTTP status."""

    def __init__(self, message: str, *, code: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.http_status = http_status
        self.payload: dict = {}

    def with_payload(self, payload: dict) -> "CheckinError":
        self.payload = payload
        return self


def _invalid(message: str) -> CheckinError:
    return CheckinError(message, code="invalid_request", http_status=400)


def _unprocessable(message: str, code: str) -> CheckinError:
    """A well-formed request the protocol will not answer.

    422 rather than 400 on purpose: the request is valid, and what is missing
    is evidence. The distinction matters to the front end, which should offer
    a re-run for one and a corrected form for the other.
    """
    return CheckinError(message, code=code, http_status=422)


# ---------------------------------------------------------------------------
# Request validation. Everything below refuses by name.
# ---------------------------------------------------------------------------

def _require_str(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid("%s must be a non-empty string" % key)
    return value


def _require_int(body: dict, key: str) -> int:
    value = body.get(key)
    # bool is an int in Python and would sail through; a boolean portfolio
    # value is a front-end bug worth naming rather than coercing to 0 or 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid("%s must be an integer number of minor units" % key)
    return value


def _require_instant(body: dict, key: str) -> str:
    text = _require_str(body, key)
    try:
        LEDGER._parse_instant(text)
    except (LedgerError, ValueError) as exc:
        raise _invalid("%s: %s" % (key, exc)) from None
    return text


def validate_header(body: dict) -> dict:
    """The check-in header, or a named refusal."""
    for key in REQUIRED_HEADER:
        if key not in body:
            raise _invalid("%s is required" % key)
    start = _require_instant(body, "forecast_period_start")
    end = _require_instant(body, "forecast_period_end")
    if LEDGER._parse_instant(end) <= LEDGER._parse_instant(start):
        raise _invalid("forecast_period_end must be after forecast_period_start")
    exponent = body.get("portfolio_currency_exponent", 2)
    if isinstance(exponent, bool) or not isinstance(exponent, int) \
            or not 0 <= exponent <= 6:
        raise _invalid("portfolio_currency_exponent must be an integer in 0..6")
    opening = _require_int(body, "opening_value_minor")
    if opening <= 0:
        # Modified Dietz divides by the opening value; §3 calls the near-zero
        # case ill-conditioned and refuses it rather than returning a rate.
        raise _invalid("opening_value_minor must be positive")
    if _require_int(body, "closing_value_minor") < 0:
        raise _invalid("closing_value_minor cannot be negative")
    return {
        "checkin_id": body.get("checkin_id") or _derive_checkin_id(body),
        "plan_id": _require_str(body, "plan_id"),
        "plan_version_id": _require_str(body, "plan_version_id"),
        "forecast_period_start": start,
        "forecast_period_end": end,
        "portfolio_currency": body.get("portfolio_currency") or "USD",
        "portfolio_currency_exponent": exponent,
        "portfolio_timezone": body.get("portfolio_timezone") or "UTC",
        "opening_value_minor": opening,
        "closing_value_minor": body["closing_value_minor"],
        "starting_state_hash": body.get("starting_state_hash") or ("0" * 64),
        "household_scope_hash": body.get("household_scope_hash") or ("0" * 64),
        "model_vintage": body.get("model_vintage") or "unrecorded",
        "observation_state": body.get("observation_state") or "observed",
        "source_kind": body.get("source_kind") or "manual",
        "source_sha256": body.get("source_sha256"),
        "created_at": PERSISTENCE.utc_now(),
        "supersedes_checkin_id": body.get("supersedes_checkin_id"),
    }


def _derive_checkin_id(body: dict) -> str:
    """A stable id from the plan version and the period it covers.

    Derived rather than random so that submitting the same period twice
    collides on the primary key instead of quietly creating a second check-in
    for the same year -- the ledger has no other way to notice.
    """
    return "checkin_" + PERSISTENCE.sha256_json({
        "plan_version_id": body.get("plan_version_id"),
        "start": body.get("forecast_period_start"),
        "end": body.get("forecast_period_end"),
    })[:32]


def validate_lines(rows: Any, side: str, header: dict) -> list:
    if not isinstance(rows, list):
        raise _invalid("%s must be a list of flow lines" % side)
    if len(rows) > MAX_LINES_PER_SIDE:
        raise _invalid("%s carries more than %d lines"
                       % (side, MAX_LINES_PER_SIDE))
    allowed = set(REQUIRED_LINE) | set(OPTIONAL_LINE)
    out = []
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise _invalid("%s[%d] must be an object" % (side, index))
        unknown = sorted(set(row) - allowed)
        if unknown:
            raise _invalid("%s[%d] has unknown field(s): %s"
                           % (side, index, ", ".join(unknown)))
        for key in REQUIRED_LINE:
            if key not in row:
                raise _invalid("%s[%d].%s is required" % (side, index, key))
        if row["category"] not in CATEGORIES:
            # Deliberately CATEGORIES, not WATERFALL_ORDER: `market`,
            # `model_update` and `residual` are computed lines. A caller that
            # could post a `market` flow line could hand-write the very number
            # the decomposition exists to derive.
            raise _invalid("%s[%d].category %r is not one of %s"
                           % (side, index, row["category"],
                              ", ".join(CATEGORIES)))
        amount = _require_int(row, "amount_portfolio_minor")
        occurred = _require_instant(row, "occurred_at")
        transaction_id = row.get("transaction_id") or "%s-%s-%d" % (
            header["checkin_id"], side, index)
        out.append({
            "transaction_id": transaction_id,
            "category": row["category"],
            "source_or_schedule_id": row.get("source_or_schedule_id")
            or row["category"],
            "source_event_id": row.get("source_event_id") or transaction_id,
            "component_leg_id": row.get("component_leg_id") or "main",
            "period_start": header["forecast_period_start"],
            "period_end": header["forecast_period_end"],
            "timing_bucket": row.get("timing_bucket") or "exact",
            "amount_portfolio_minor": amount,
            "occurred_at": occurred,
            "timing_state": row.get("timing_state") or "exact",
            "observation_state": row.get("observation_state") or "observed",
            "is_internal_transfer": 1 if row.get("is_internal_transfer") else 0,
            "transfer_group_id": row.get("transfer_group_id"),
            "absence_proof_id": row.get("absence_proof_id"),
            "supersedes_transaction_id": row.get("supersedes_transaction_id"),
            "created_at": header["created_at"],
        })
    return out


# ---------------------------------------------------------------------------
# The seam.
# ---------------------------------------------------------------------------

class CheckinSeam:
    """Check-in recording and attribution over one archive.

    `write` is the archive-write callable -- `(key, mutate) -> result` -- so
    that under a control journal every ledger row lands through the same
    stage/measure/prebind/swap path a formal run uses, and without one the 2.0
    direct path is preserved unchanged.
    """

    def __init__(self, store: Any, *, engine_version: str, source_root: str,
                 write: Optional[Callable[[str, Callable], Any]] = None,
                 metadata: Optional[dict] = None):
        self.store = store
        self.engine_version = engine_version
        self.source_root = source_root
        self.metadata = metadata
        self._write = write

    # ------------------------------------------------------------- plumbing

    def _archive_write(self, key: str, mutate: Callable[[Any], Any]) -> Any:
        if self._write is None:
            return mutate(self.store)
        return self._write(key, mutate)

    @staticmethod
    def _ledger_conn(store: Any) -> sqlite3.Connection:
        conn = store._connect()
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _has_ledger(conn: sqlite3.Connection) -> bool:
        return conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='checkins'").fetchone() is not None

    def _require_v9(self, conn: sqlite3.Connection) -> None:
        if not self._has_ledger(conn):
            raise _unprocessable(
                "this archive has no check-in ledger; record a check-in first, "
                "which installs it",
                "ledger_unavailable")

    def _install_ledger(self, store: Any, conn: sqlite3.Connection) -> bool:
        """Bring an older archive up to v9, additively, or do nothing.

        A live archive sits at v6: `install_v7_schema` and `install_v8_schema`
        are reached only by the restore path's staged migration, and nothing
        ever installed v9 at all. Without this the ledger could not exist in a
        running app, and the seam above would refuse every check-in forever.

        It happens here rather than at startup for two reasons. A migration is
        a durable archive mutation, and inside `record` it is already inside
        the writer's stage/measure/prebind/swap -- so it is owned, it is
        measured, and a failure rolls the whole thing back rather than leaving
        a half-migrated archive. And a user who never opens the annual review
        never has their archive touched.

        Each step is refused unless its predecessor is exactly in place; the
        installers enforce that themselves and are idempotent.
        """
        if self._has_ledger(conn):
            return False
        release = getattr(store, "app_release_id", "fire-modeling-3.0")
        try:
            # Start from where this archive actually is, not always at v7.
            #
            # A freshly created archive sits at v6 and the whole chain applies.
            # An archive that arrived by migrating a 2.0 install sits at **v8**
            # -- migration builds those tables itself -- so running the v7
            # installer first made it refuse ("v7 archive user_version
            # mismatch") and the ledger was never installed at all. The user
            # kept their plans and could not do an annual review: Phase 2 was
            # unreachable for exactly the people who had used the app longest.
            #
            # Each installer already refuses an archive that is not at its own
            # predecessor version, so selecting by current version applies the
            # same guard in the right order rather than weakening it.
            current = conn.execute("PRAGMA user_version").fetchone()[0]
            for expected_before, install in (
                    (6, PERSISTENCE.PersistenceStore.install_v7_schema),
                    (7, PERSISTENCE.PersistenceStore.install_v8_schema),
                    (8, PERSISTENCE.PersistenceStore.install_v9_schema)):
                if current <= expected_before:
                    install(conn, app_release_id=release)
                    current = conn.execute("PRAGMA user_version").fetchone()[0]
        except PERSISTENCE.PersistenceError as exc:
            conn.rollback()
            raise _unprocessable(
                "this archive cannot take the check-in ledger: %s" % exc,
                "ledger_unavailable") from None
        return True

    # -------------------------------------------------------------- writing

    def record(self, body: dict) -> dict:
        """Append one check-in and its two ledger sides."""
        if not isinstance(body, dict):
            raise _invalid("request body must be an object")
        header = validate_header(body)
        if "actual" not in body:
            raise _invalid("actual is required")
        if "expected" not in body:
            # Without the forecast side there is no per-category `V_i`, so the
            # waterfall would have a market line and nothing to compare
            # behaviour against. Accepting the request and producing a
            # decomposition that cannot separate them is the failure this
            # whole phase exists to avoid.
            raise _invalid("expected is required: the forecast flows are what "
                           "the actual flows are attributed against")
        actual = validate_lines(body["actual"], "actual", header)
        expected = validate_lines(body["expected"], "expected", header)

        def mutate(store):
            conn = self._ledger_conn(store)
            try:
                migrated = self._install_ledger(store, conn)
                try:
                    LEDGER.insert_checkin(conn, header)
                    LEDGER.append_raw_lines(conn, header["checkin_id"],
                                            "actual", actual)
                    LEDGER.append_raw_lines(conn, header["checkin_id"],
                                            "expected", expected)
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    raise CheckinError(
                        "a check-in for this plan version and period already "
                        "exists; a correction is a new superseding row, not a "
                        "rewrite (%s)" % exc,
                        code="checkin_exists", http_status=409) from None
                except LedgerError as exc:
                    conn.rollback()
                    raise _invalid(str(exc)) from None
                conn.commit()
            finally:
                conn.close()
            return {"checkin_id": header["checkin_id"],
                    "actual_lines": len(actual),
                    "expected_lines": len(expected),
                    "ledger_installed": migrated}

        return self._archive_write("checkin-record:" + header["checkin_id"],
                                   mutate)

    # -------------------------------------------------------------- reading

    def history(self, plan_id: str) -> dict:
        """Every recorded check-in for one plan, oldest period first."""
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise _invalid("plan_id is required")
        conn = self._ledger_conn(self.store)
        try:
            self._require_v9(conn)
            rows = conn.execute(
                "SELECT checkin_id, plan_version_id, forecast_period_start, "
                "forecast_period_end, portfolio_currency, "
                "portfolio_currency_exponent, opening_value_minor, "
                "closing_value_minor, observation_state, created_at, "
                "supersedes_checkin_id FROM checkins WHERE plan_id = ? "
                "ORDER BY forecast_period_start, created_at",
                (plan_id,)).fetchall()
            columns = [d[0] for d in conn.execute(
                "SELECT checkin_id, plan_version_id, forecast_period_start, "
                "forecast_period_end, portfolio_currency, "
                "portfolio_currency_exponent, opening_value_minor, "
                "closing_value_minor, observation_state, created_at, "
                "supersedes_checkin_id FROM checkins WHERE 0").description]
        finally:
            conn.close()
        return {"plan_id": plan_id,
                "checkins": [dict(zip(columns, row)) for row in rows]}

    # -------------------------------------------------------- old forecasts

    #: How many archived forecasts a list call will carry curves for. A local
    #: archive can accumulate a run per session; a rolling review only ever
    #: compares a handful, and the response carries a per-age curve each.
    MAX_FORECASTS = 24

    def forecasts(self, plan_id: str) -> dict:
        """The archived forecasts for one plan, newest first, with their curves.

        This is what makes a real annual review possible. Until it existed the
        UI could only attribute against the run the user had *just* finished,
        which is today's projection of a period that has already happened --
        a forecast that already knows about the market it is being scored
        against. §1.2 is explicit that `F_oo` is the snapshot that was current
        at the observation, and choosing it is a decision the archive has to
        surface rather than the UI guess.

        Pure. Reads `run_snapshots` and returns each one's own stored curve;
        nothing is recomputed, so a snapshot written before the engine emitted
        `forecast_period_statistics` comes back with an empty series and says
        so rather than being silently reconstructed.
        """
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise _invalid("plan_id is required")
        conn = self._ledger_conn(self.store)
        try:
            rows = conn.execute(
                "SELECT rs.id, rs.plan_version_id, rs.engine_build_id, "
                "rs.created_at, ra.precision, ra.seed "
                "FROM run_snapshots rs "
                "JOIN run_attempts ra ON ra.id = rs.attempt_id "
                "WHERE rs.plan_id = ? "
                "ORDER BY rs.created_at DESC, rs.id DESC LIMIT ?",
                (plan_id, self.MAX_FORECASTS + 1)).fetchall()
        finally:
            conn.close()
        truncated = len(rows) > self.MAX_FORECASTS
        rows = rows[:self.MAX_FORECASTS]

        current_build = self.current_build_id()
        out = []
        for snapshot_id, version_id, build_id, created_at, precision, seed in rows:
            try:
                snapshot = self.store.get_snapshot(snapshot_id)
            except PERSISTENCE.PersistenceError:
                # A snapshot whose immutable bindings no longer verify is not
                # something to offer as a forecast to review against.
                continue
            series = LEDGER.forecast_series(snapshot)
            out.append({
                "snapshot_id": snapshot_id,
                "plan_version_id": version_id,
                "engine_build_id": build_id,
                "created_at": created_at,
                "precision": precision,
                "seed": seed,
                "build_matches_current": build_id == current_build,
                "start_age": self._start_age(snapshot),
                "series": series,
                "series_available": bool(series),
            })
        return {"plan_id": plan_id, "forecasts": out,
                "current_engine_build_id": current_build,
                "truncated": truncated}

    @staticmethod
    def _start_age(snapshot: dict):
        try:
            state = LEDGER.archived_plan_inputs(snapshot).get("state")
        except LEDGER.FChainUnavailable:
            return None
        if isinstance(state, dict) and "start_age" in state:
            return float(state["start_age"])
        return None

    # ------------------------------------------------------------ attribute

    def attribute(self, body: dict) -> dict:
        """Decompose one check-in into market, behaviour and model update."""
        if not isinstance(body, dict):
            raise _invalid("request body must be an object")
        checkin_id = _require_str(body, "checkin_id")
        snapshot_id = _require_str(body, "forecast_snapshot_id")

        try:
            old_snapshot = self.store.get_snapshot(snapshot_id)
        except PERSISTENCE.PersistenceError as exc:
            raise CheckinError(str(exc), code="unknown_snapshot",
                               http_status=404) from None

        counterfactual = None
        counterfactual_id = body.get("counterfactual_snapshot_id")
        if counterfactual_id is not None:
            if not isinstance(counterfactual_id, str):
                raise _invalid("counterfactual_snapshot_id must be a string")
            try:
                counterfactual = self.store.get_snapshot(counterfactual_id)
            except PERSISTENCE.PersistenceError as exc:
                raise CheckinError(str(exc), code="unknown_snapshot",
                                   http_status=404) from None

        conn = self._ledger_conn(self.store)
        try:
            self._require_v9(conn)
            header = LEDGER.load_checkin(conn, checkin_id)
            if header is None:
                raise CheckinError("no such check-in: %s" % checkin_id,
                                   code="unknown_checkin", http_status=404)
            if header["plan_version_id"] != old_snapshot.get("plan_version_id"):
                raise _unprocessable(
                    "the check-in was recorded against plan version %s but the "
                    "forecast snapshot is for %s; attributing one to the other "
                    "would compare different plans"
                    % (header["plan_version_id"],
                       old_snapshot.get("plan_version_id")),
                    "plan_version_mismatch")
            current_build = self.current_build_id()
            age = LEDGER._age_at(old_snapshot, header)
            # Derived from the archive, not read from the request. Ruled
            # 2026-08-16; see `_retired_at` for what the request field cost.
            # `standing()` reaches this through the same call, so the home
            # page's line is fixed by the same change rather than needing a
            # second caller to remember a field.
            try:
                retired = LEDGER._retired_at(old_snapshot, age)
            except LEDGER.ReturnAdapterRejected as exc:
                raise _unprocessable(str(exc),
                                     "return_adapter_rejected") from None
            try:
                f_oo, f_no, basis = LEDGER.model_update_term(
                    old_snapshot, current_build_id=current_build, age=age,
                    new_vintage_snapshot=counterfactual)
                r_new = LEDGER.expected_portfolio_return(
                    LEDGER.archived_plan_inputs(old_snapshot),
                    age=age, retired=retired,
                    equity_mean=self.equity_mean(old_snapshot))
            except LEDGER.FChainUnavailable as exc:
                raise _unprocessable(str(exc),
                                     "model_update_unavailable") from None
            except LEDGER.ReturnAdapterRejected as exc:
                raise _unprocessable(str(exc),
                                     "return_adapter_rejected") from None
            try:
                result = LEDGER.attribute_checkin(conn, checkin_id,
                                                  r_new=r_new, f_oo=f_oo,
                                                  f_no=f_no)
            except LedgerError as exc:
                raise _unprocessable(str(exc), "ledger_rejected") from None
        finally:
            conn.close()
        rendered = self._render(header, result, basis=basis, f_oo=f_oo,
                                f_no=f_no,
                                r_new=r_new, snapshot_id=snapshot_id,
                                counterfactual_id=counterfactual_id,
                                current_build=current_build)
        # The memo reads the attribution; it never recomputes any of it. Built
        # here rather than in the browser so the three-way call has one
        # definition that tests can pin.
        rendered["memo"] = MEMO.build_memo(rendered)
        return rendered

    # ------------------------------------------------- the F_no counterfactual

    def counterfactual_plan(self, snapshot_id: str) -> dict:
        """What to run to turn the model-update line from unknown into a number.

        Returns the run parameters; it does not run anything. The engine call
        belongs to the job machinery `/api/run_start` already owns -- progress,
        cancellation, idempotency, archive ownership and the snapshot commit
        are all solved there, and a second copy of them here would be a second
        set of bugs.

        Everything is pinned from the archive. The config is the archived
        resolved input (§1.2 forbids rebuilding it from today's), the protocol
        is the old run's own paths/seed/dist_paths so the difference cannot
        contain Monte Carlo noise, and the plan version is the same one -- what
        differs is the build, which is the whole point.

        `request_id` is derived from the pair, so asking twice reuses the first
        answer through the archive's existing idempotency rather than running
        the engine again.
        """
        try:
            old = self.store.get_snapshot(snapshot_id)
        except PERSISTENCE.PersistenceError as exc:
            raise CheckinError(str(exc), code="unknown_snapshot",
                               http_status=404) from None
        current = self.current_build_id()
        try:
            request = LEDGER.counterfactual_request(old, current_build_id=current)
        except LEDGER.FChainUnavailable as exc:
            raise _unprocessable(str(exc), "counterfactual_unnecessary") from None

        protocol = request.get("protocol") or {}
        missing = [k for k in ("paths", "seed", "dist_paths")
                   if k not in protocol]
        if missing:
            raise _unprocessable(
                "the archived run protocol does not state %s, so the "
                "counterfactual cannot be held to it" % ", ".join(sorted(missing)),
                "counterfactual_unavailable")
        digest = PERSISTENCE.sha256_json({"old": snapshot_id,
                                          "build": current})
        return {
            "config": request["resolved_input"],
            "paths": int(protocol["paths"]),
            "seed": int(protocol["seed"]),
            "dist_paths": int(protocol["dist_paths"]),
            "precision": protocol.get("precision") or "standard",
            "plan_id": old.get("plan_id"),
            "plan_version_id": old.get("plan_version_id"),
            "request_id": "req_cf" + digest[:40],
            "old_snapshot_id": snapshot_id,
            "old_engine_build_id": old.get("engine_build_id"),
            "engine_build_id": current,
        }

    # ------------------------------------------------- the standing position

    def standing(self, plan_id: str) -> dict:
        """Where this plan stands, for the home page.

        ROADMAP asks the home page for four things: current state, why it
        changed, the decision it implies, and when to look again. All four come
        from the most recent check-in's attribution and memo, so this returns
        that rather than a second set of numbers computed a second way -- a
        home page that disagreed with the review page would be worse than one
        that says nothing.

        "No review yet" is a first-class answer, not an error. Most plans will
        be in that state, and it is the state the home page most needs to
        render calmly.
        """
        if not isinstance(plan_id, str) or not plan_id.strip():
            raise _invalid("plan_id is required")
        conn = self._ledger_conn(self.store)
        try:
            if not self._has_ledger(conn):
                return {"plan_id": plan_id, "has_review": False,
                        "reason": "no check-in has been recorded for any plan"}
            row = conn.execute(
                "SELECT checkin_id, plan_version_id, forecast_period_end "
                "FROM checkins WHERE plan_id = ? "
                "ORDER BY forecast_period_end DESC, created_at DESC LIMIT 1",
                (plan_id,)).fetchone()
        finally:
            conn.close()
        if row is None:
            return {"plan_id": plan_id, "has_review": False,
                    "reason": "no check-in has been recorded for this plan"}
        checkin_id, version_id, period_end = row

        # The forecast to score against is the one the check-in was recorded
        # under. That binding is why `record` stores the chosen forecast's plan
        # version rather than today's -- without it this would have to guess.
        snapshot_id = None
        for entry in self.forecasts(plan_id)["forecasts"]:
            if entry["plan_version_id"] == version_id and entry["series_available"]:
                snapshot_id = entry["snapshot_id"]
                break
        if snapshot_id is None:
            return {"plan_id": plan_id, "has_review": False,
                    "checkin_id": checkin_id,
                    "reason": "the forecast this check-in was recorded against "
                              "is no longer readable, so its review cannot be "
                              "reproduced"}
        try:
            attribution = self.attribute({"checkin_id": checkin_id,
                                          "forecast_snapshot_id": snapshot_id})
        except CheckinError as exc:
            # A refusal here is information, not a failure: the home page says
            # a review exists and cannot currently be read, and why.
            return {"plan_id": plan_id, "has_review": False,
                    "checkin_id": checkin_id,
                    "reason": str(exc), "code": exc.code}
        return {"plan_id": plan_id, "has_review": True,
                "checkin_id": checkin_id,
                "as_of": period_end,
                "memo": attribution.get("memo"),
                "opening": attribution.get("opening"),
                "closing": attribution.get("closing"),
                "state": attribution.get("state"),
                "components": attribution.get("components")}

    def current_build_id(self) -> str:
        return self.store.current_engine_build_id(
            self.engine_version, source_root=self.source_root,
            metadata=self.metadata)

    @staticmethod
    def equity_mean(snapshot: dict) -> float:
        """The vintage's equity mean, from the archived plan or the engine.

        The archived resolved input is preferred because it is the vintage the
        forecast was made under. Falling back to the running engine's constant
        is honest only because it is a compiled-in constant rather than a user
        input; if a plan ever carries its own, that one wins.
        """
        resolved = LEDGER.archived_plan_inputs(snapshot)
        returns = resolved.get("returns")
        if isinstance(returns, dict) and "equity_mean" in returns:
            return float(returns["equity_mean"])
        import engine_adapter as ENG
        return float(ENG.BASE_MU)

    @staticmethod
    def _render(header: dict, result: Any, *, basis: str, f_oo: float,
                f_no: float, r_new: float, snapshot_id: str,
                counterfactual_id: Optional[str], current_build: str) -> dict:
        """The response, with every non-number carried as null plus a reason.

        §4 forbids rendering an incomplete component as a number, and the
        residual must never absorb one. `ordered()` already returns the
        display order the protocol fixes, and it is returned as a list rather
        than an object so the front end cannot reorder it by accident.
        """
        exponent = header["portfolio_currency_exponent"]
        return {
            "checkin_id": header["checkin_id"],
            "plan_id": header["plan_id"],
            "plan_version_id": header["plan_version_id"],
            "period": {"start": header["forecast_period_start"],
                       "end": header["forecast_period_end"]},
            "currency": header["portfolio_currency"],
            "currency_exponent": exponent,
            "opening": LEDGER._to_currency(header["opening_value_minor"],
                                           exponent),
            "closing": LEDGER._to_currency(header["closing_value_minor"],
                                           exponent),
            "forecast": {
                "snapshot_id": snapshot_id,
                "counterfactual_snapshot_id": counterfactual_id,
                "f_oo": f_oo,
                "f_no": f_no,
                "model_update_basis": basis,
                "current_engine_build_id": current_build,
                "r_new": r_new,
                "r_md": result.r_md,
            },
            "y_actual": result.y_actual,
            "components": [{"key": key, "value": value,
                            "state": "unknown" if value is None else "complete"}
                           for key, value in result.ordered()
                           if key != "residual"],
            "residual": result.residual,
            "residual_over_opening": result.residual_over_opening,
            # Known even when the model-update line and the residual are not:
            # their sum is `A1 - F_oo` minus the components, and both ends of
            # that are archived or observed.
            "unsplit_update_and_residual": result.unsplit_update_and_residual,
            "tolerance": result.tolerance,
            "within_tolerance": result.within_tolerance,
            "state": result.state,
            "reasons": list(result.reasons),
        }
