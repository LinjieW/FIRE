"""Phase 2 · reading and writing the v9 CheckIn ledger.

Thin, deliberately. The schema and its immutability triggers live in
`persistence.py` alongside the rest of the archive; the grain rules and the
waterfall live in `attribution.py`. This module only moves rows between them,
so there is no third place for the rules to drift to.

Two things it will not do, both from §2 of the attribution protocol:

  * It never stores `flow_line_v2`. The union join and aggregation "are
    recomputed on read", so the derived rows are always a function of the raw
    ledger rather than a table that can fall out of agreement with it.
  * It never rewrites a raw row. A correction is a new row that supersedes an
    old one, and the superseded row is retained. The database enforces this too
    -- the triggers reject UPDATE and DELETE outright -- but doing it here as
    well means the failure is a clear Python error rather than a SQLite abort
    from three layers down.
"""

from __future__ import annotations

import sqlite3
from typing import Iterable, Optional

from attribution import (LedgerError, Period, derive_flow_lines,
                         realized_waterfall, to_flow_lines,
                         transfer_group_states)

#: Columns of `transaction_line_v2`, in declaration order.
RAW_COLUMNS = (
    "transaction_id", "checkin_id", "side", "category", "source_or_schedule_id",
    "source_event_id", "component_leg_id", "period_start", "period_end",
    "timing_bucket", "amount_portfolio_minor", "source_currency",
    "source_amount_minor", "source_currency_exponent", "fx_numerator",
    "fx_denominator", "fx_vintage", "occurred_at", "source_timezone",
    "date_only_value", "timing_state", "observation_state",
    "is_internal_transfer", "transfer_group_id", "absence_proof_id",
    "source_pointer", "source_sha256", "supersedes_transaction_id",
    "created_at",
)

CHECKIN_COLUMNS = (
    "checkin_id", "plan_id", "plan_version_id", "forecast_period_start",
    "forecast_period_end", "portfolio_currency", "portfolio_currency_exponent",
    "portfolio_timezone", "opening_value_minor", "closing_value_minor",
    "starting_state_hash", "household_scope_hash", "model_vintage",
    "observation_state", "source_kind", "source_sha256", "created_at",
    "supersedes_checkin_id",
)

SIDES = ("actual", "expected")


def _placeholders(columns):
    return ", ".join("?" for _ in columns)


def insert_checkin(conn: sqlite3.Connection, header: dict) -> str:
    """Append one CheckIn header. Immutable once written."""
    missing = [c for c in CHECKIN_COLUMNS
               if c not in header and c not in ("source_sha256",
                                                "supersedes_checkin_id")]
    if missing:
        raise LedgerError("checkin header missing %s" % ", ".join(missing))
    values = [header.get(c) for c in CHECKIN_COLUMNS]
    conn.execute(
        "INSERT INTO checkins (%s) VALUES (%s)"
        % (", ".join(CHECKIN_COLUMNS), _placeholders(CHECKIN_COLUMNS)), values)
    return header["checkin_id"]


def append_raw_lines(conn: sqlite3.Connection, checkin_id: str, side: str,
                     rows: Iterable[dict]) -> int:
    """Append raw ledger rows. Never updates; a correction is a new row."""
    if side not in SIDES:
        raise LedgerError("unknown ledger side %r" % (side,))
    count = 0
    for row in rows:
        record = dict(row)
        record["checkin_id"] = checkin_id
        record["side"] = side
        record.setdefault("is_internal_transfer", 0)
        record.setdefault("timing_state", "exact")
        record.setdefault("observation_state", "observed")
        record.setdefault("timing_bucket", "exact")
        unknown = set(record) - set(RAW_COLUMNS)
        if unknown:
            raise LedgerError("unknown ledger column(s): %s"
                              % ", ".join(sorted(unknown)))
        conn.execute(
            "INSERT INTO transaction_line_v2 (%s) VALUES (%s)"
            % (", ".join(RAW_COLUMNS), _placeholders(RAW_COLUMNS)),
            [record.get(c) for c in RAW_COLUMNS])
        count += 1
    return count


def load_raw_lines(conn: sqlite3.Connection, checkin_id: str,
                   side: str) -> list[dict]:
    """Every raw row for one side, superseded rows included.

    Superseded rows are part of the answer, not noise: the derivation needs
    them to compute a membership digest that changes when a correction lands.
    """
    if side not in SIDES:
        raise LedgerError("unknown ledger side %r" % (side,))
    cursor = conn.execute(
        "SELECT %s FROM transaction_line_v2 WHERE checkin_id = ? AND side = ? "
        "ORDER BY transaction_id" % ", ".join(RAW_COLUMNS),
        (checkin_id, side))
    out = []
    for values in cursor.fetchall():
        row = dict(zip(RAW_COLUMNS, values))
        row["is_internal_transfer"] = bool(row["is_internal_transfer"])
        out.append(row)
    return out


def derived_flow_lines(conn: sqlite3.Connection, checkin_id: str,
                       side: str) -> list[dict]:
    """`flow_line_v2`, recomputed on read. Never stored."""
    return derive_flow_lines(load_raw_lines(conn, checkin_id, side))


def load_checkin(conn: sqlite3.Connection,
                 checkin_id: str) -> Optional[dict]:
    row = conn.execute(
        "SELECT %s FROM checkins WHERE checkin_id = ?"
        % ", ".join(CHECKIN_COLUMNS), (checkin_id,)).fetchone()
    return dict(zip(CHECKIN_COLUMNS, row)) if row else None


def ledger_states(conn: sqlite3.Connection, checkin_id: str) -> dict:
    """A quick integrity read: transfer-group balance per side."""
    return {side: transfer_group_states(derived_flow_lines(conn, checkin_id,
                                                           side))
            for side in SIDES}


# ---------------------------------------------------------------------------
# Wiring the ledger to the waterfall.
# ---------------------------------------------------------------------------

def _to_currency(minor, exponent):
    """Minor units -> currency units.

    This boundary is the one place a factor of 100 can hide. The ledger stores
    `amount_portfolio_minor` as a signed integer because §2 makes that the
    authoritative amount; the waterfall works in currency units because §4's
    tolerance is written as `10 * 10^-portfolio_currency_exponent`, i.e. ten
    minor units expressed in currency. Converting in one named function keeps
    the two conventions from meeting anywhere else.
    """
    return minor / (10 ** exponent)


def attribute_checkin(conn: sqlite3.Connection, checkin_id: str, *,
                      r_new: float, f_oo: float, f_no: float):
    """Derive both ledger sides and run the realized waterfall over them.

    `r_new`, `f_oo` and `f_no` are forecast-side statistics and are passed in
    rather than read from here: §4 requires `r_new` to come from `F_new` and
    never to be derived from the observed close, and this module has no
    business inferring any of them from the ledger it just read.
    """
    header = load_checkin(conn, checkin_id)
    if header is None:
        raise LedgerError("no such checkin: %r" % (checkin_id,))

    exponent = header["portfolio_currency_exponent"]
    period = Period(_parse_instant(header["forecast_period_start"]),
                    _parse_instant(header["forecast_period_end"]))

    sides = {}
    for side in SIDES:
        derived = derive_flow_lines(load_raw_lines(conn, checkin_id, side))
        sides[side] = _as_flow_lines(derived, exponent)

    unbalanced = [gid for states in ledger_states(conn, checkin_id).values()
                  for gid, state in states.items() if state == "unbalanced"]

    result = realized_waterfall(
        opening=_to_currency(header["opening_value_minor"], exponent),
        closing=_to_currency(header["closing_value_minor"], exponent),
        actual_lines=sides["actual"], expected_lines=sides["expected"],
        period=period, r_new=r_new, f_oo=f_oo, f_no=f_no,
        currency_exponent=exponent)

    if unbalanced:
        # §2: an unbalanced transfer group blocks complete attribution.
        result.state = "incomplete"
        result.reasons.append("unbalanced_transfer_group")
    return result


def attribute_checkin_from_snapshots(conn: sqlite3.Connection, checkin_id: str, *,
                                     old_snapshot: dict, new_vintage_snapshot: dict,
                                     equity_mean: float, retired: bool = False):
    """`attribute_checkin` with the F-chain and `r_new` taken from the archive.

    This is the shape a real annual review uses: the caller supplies two
    snapshots and the vintage's equity mean, and every forecast term is read
    rather than passed in. `r_new` comes from the new-vintage plan through §1's
    adapter, so it cannot be fitted to the observed close even by accident.
    """
    header = load_checkin(conn, checkin_id)
    if header is None:
        raise LedgerError("no such checkin: %r" % (checkin_id,))
    age = _age_at(new_vintage_snapshot, header)
    f_oo, f_no = f_chain(old_snapshot, new_vintage_snapshot, age=age)
    r_new = expected_portfolio_return(
        archived_plan_inputs(new_vintage_snapshot),
        age=age, retired=retired, equity_mean=equity_mean)
    return attribute_checkin(conn, checkin_id, r_new=r_new, f_oo=f_oo,
                             f_no=f_no)


def _age_at(snapshot: dict, header: dict) -> float:
    """The plan age at the period close, for the glide path's weight."""
    resolved = archived_plan_inputs(snapshot)
    state = resolved.get("state")
    if not isinstance(state, dict) or "start_age" not in state:
        raise ReturnAdapterRejected(
            "archived plan has no start_age, so the glide weight at the "
            "period close is unknown")
    start = _parse_instant(header["forecast_period_start"])
    end = _parse_instant(header["forecast_period_end"])
    years = (end - start).total_seconds() / (365.2425 * 86400)
    return float(state["start_age"]) + years


def _retired_at(snapshot: dict, age: float) -> bool:
    """Was this plan in retirement at the period close?

    Derived from the archived plan, ruled 2026-08-16. It used to arrive as a
    request field -- `body.get("retired", False)` -- and nothing ever sent it:
    `web/app.js` contains the word nowhere, and `standing()` calls `attribute`
    without it too. So every retired user's market line was computed with the
    ACCUMULATION friction, 0 bps instead of 50, and the missing half a percent
    of the opening portfolio came out the other side as unexplained residual:
    $5,000 on a $1M plan, against a tolerance of ten percent of the period's
    change. The better a plan tracked its forecast, the more likely that
    phantom pushed it out of tolerance.

    The archive knew all along. `_age_at` is computed two lines from where the
    flag was read, and the resolved input carries the retirement age.

    Missing `accum_years` is refused rather than assumed. The two answers
    differ by 50 bps of friction on the whole portfolio, which is exactly the
    size of the defect this replaces -- guessing would reproduce it quietly.
    """
    resolved = archived_plan_inputs(snapshot)
    state = resolved.get("state")
    if not isinstance(state, dict) or "accum_years" not in state:
        raise ReturnAdapterRejected(
            "archived plan has no accum_years, so whether it was retired at "
            "the period close is unknown; the two answers differ by 50 bps of "
            "friction on the whole portfolio and guessing one would put that "
            "difference into the residual as if it were unexplained")
    if "start_age" not in state:
        raise ReturnAdapterRejected(
            "archived plan has no start_age, so its retirement age is unknown")
    return float(age) >= float(state["start_age"]) + float(state["accum_years"])


def _as_flow_lines(derived_rows, exponent):
    converted = []
    for row in derived_rows:
        item = dict(row)
        item["amount_portfolio_minor"] = _to_currency(
            row["amount_portfolio_minor"], exponent)
        if isinstance(item.get("occurred_at"), str):
            item["occurred_at"] = _parse_instant(item["occurred_at"])
        converted.append(item)
    return to_flow_lines(converted, None)


# ---------------------------------------------------------------------------
# The F-chain (§1.2), as far as the engine currently permits.
# ---------------------------------------------------------------------------

#: The canonical realized metric (§1). A p50 is explicitly NOT a substitute:
#: "A p50, age, rate, or conditional metric requires a separate metric-specific
#: packet and cannot reuse this scalar waterfall."
CANONICAL_METRIC = "closing_portfolio_nominal"
CANONICAL_STATISTIC = "mean"


class FChainUnavailable(LedgerError):
    """The archived snapshot cannot supply a term of the F-chain.

    §1.2 requires `F_oo` to come from the old snapshot's stored output
    statistic, and says that when the required fields do not match,
    `Y_update = unknown` and "no reconstruction from today's defaults is
    allowed". So this is raised rather than substituted around.
    """


#: How far the requested age may sit from the engine's own annual grid point
#: before the read is refused. A check-in year measured in real calendar days
#: lands a fraction of a day off a 365.2425-day year, which is drift; anything
#: approaching half a year is a different row and must not be returned quietly.
AGE_GRID_TOLERANCE_YEARS = 0.05


def forecast_statistic(snapshot: dict, *, metric=CANONICAL_METRIC,
                       statistic=CANONICAL_STATISTIC, age=None,
                       scenario="home"):
    """Read one forecast statistic out of an archived RunSnapshot.

    Returns the value, or raises `FChainUnavailable` naming precisely what is
    missing. It never falls back to a percentile: the engine currently reports
    `terminal_nominal` as p10/p50/p90 and §1 rules a p50 out of this waterfall
    explicitly, so substituting one would produce a number that looks right and
    answers a different question.

    The engine emits this series as a list of per-age rows under
    `dist[scenario]`, so both that shape and a pre-selected `{statistic: value}`
    block are accepted. For the list form `age` is required and must land on the
    engine's own annual grid within `AGE_GRID_TOLERANCE_YEARS`: reading the
    neighbouring year's forecast would silently attribute a year of compounding
    to the model-update term.
    """
    result = snapshot.get("result")
    if not isinstance(result, dict):
        raise FChainUnavailable("snapshot carries no result payload")
    series = result.get("forecast_period_statistics")
    if series is None:
        # The engine's own payload nests the per-scenario distribution blocks.
        dist = result.get("dist")
        if isinstance(dist, dict) and isinstance(dist.get(scenario), dict):
            series = dist[scenario].get("forecast_period_statistics")
    if not isinstance(series, dict):
        raise FChainUnavailable(
            "archived result has no per-period forecast statistics; the engine "
            "summary reports terminal values and milestone crossings only, so "
            "the closing value for a check-in period was never computed")
    block = series.get(metric)
    if isinstance(block, list):
        block = _row_at_age(block, metric, age)
    if not isinstance(block, dict) or statistic not in block:
        raise FChainUnavailable(
            "archived result has no %s of %s" % (statistic, metric))
    return float(block[statistic])


def forecast_series(snapshot: dict, *, metric=CANONICAL_METRIC,
                    statistic=CANONICAL_STATISTIC, scenario="home") -> list:
    """The whole per-age forecast series, for showing two of them together.

    `forecast_statistic` answers "what did this plan predict for one age";
    a rolling review also has to show the old forecast and the current
    baseline side by side, which needs the curve rather than the point.
    Returns `[]` rather than raising when the snapshot predates the series --
    an archive can legitimately hold runs from before the engine emitted it,
    and a missing curve is a thing to disclose, not an error to raise inside a
    list endpoint.
    """
    result = snapshot.get("result")
    if not isinstance(result, dict):
        return []
    series = result.get("forecast_period_statistics")
    if series is None:
        dist = result.get("dist")
        if isinstance(dist, dict) and isinstance(dist.get(scenario), dict):
            series = dist[scenario].get("forecast_period_statistics")
    if not isinstance(series, dict):
        return []
    rows = series.get(metric)
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if isinstance(row, dict) and "age" in row and statistic in row:
            out.append({"age": float(row["age"]),
                        "value": float(row[statistic])})
    return out


def _row_at_age(rows: list, metric: str, age):
    """The per-age row for a check-in period's close, or a refusal."""
    if age is None:
        raise FChainUnavailable(
            "%s is a per-age series, so reading it needs the age the check-in "
            "period closes at" % metric)
    graded = [row for row in rows if isinstance(row, dict) and "age" in row]
    if not graded:
        raise FChainUnavailable("%s carries no aged rows" % metric)
    nearest = min(graded, key=lambda row: abs(float(row["age"]) - float(age)))
    gap = abs(float(nearest["age"]) - float(age))
    if gap > AGE_GRID_TOLERANCE_YEARS:
        raise FChainUnavailable(
            "%s has no row within %g years of age %g; the nearest is %g, and "
            "returning it would attribute a different year's forecast to this "
            "period" % (metric, AGE_GRID_TOLERANCE_YEARS, float(age),
                        float(nearest["age"])))
    return nearest


class ReturnAdapterRejected(LedgerError):
    """§1's `r_new` adapter refusing rather than guessing a weight or friction."""


def equity_weight_at(glide: dict, age: float) -> float:
    """The glide path's equity weight at one age, linear between its anchors."""
    for field in ("start_age", "end_age", "equity_start", "equity_end"):
        if field not in glide:
            raise ReturnAdapterRejected("glide path is missing %s" % field)
    start, end = float(glide["start_age"]), float(glide["end_age"])
    a, b = float(glide["equity_start"]), float(glide["equity_end"])
    if end < start:
        raise ReturnAdapterRejected("glide path ends before it starts")
    if age <= start:
        return a
    if age >= end or end == start:
        return b
    return a + (b - a) * ((age - start) / (end - start))


def expected_portfolio_return(config: dict, *, age: float, retired: bool,
                              equity_mean: float, bond_mean: Optional[float] = None):
    """§1's `r_new` adapter: the deterministic nominal portfolio return.

    "For iid/markov it is the allocation-weighted arithmetic return after
    declared friction." No observed value enters here, and in particular the
    closing portfolio value does not -- §4 says `r_new` is "read from `F_new`
    and never calculated from `A1`", because a rate fitted to the close would
    let the market line absorb every flow error.

    Refuses rather than defaulting when a weight or a friction term is absent:
    "The adapter rejects missing weights, currency conversion, or friction
    semantics."
    """
    returns = config.get("returns")
    if not isinstance(returns, dict):
        raise ReturnAdapterRejected("config has no returns block")
    glide = config.get("glide")
    if not isinstance(glide, dict):
        raise ReturnAdapterRejected("config has no glide path, so the "
                                    "allocation weights are unknown")
    w_equity = equity_weight_at(glide, age)
    if not 0.0 <= w_equity <= 1.0:
        raise ReturnAdapterRejected("equity weight %r is outside [0,1]"
                                    % (w_equity,))
    if bond_mean is None:
        bonds = config.get("bonds")
        if not isinstance(bonds, dict) or "mean" not in bonds:
            raise ReturnAdapterRejected(
                "config has no bond mean, so the non-equity leg is unknown")
        bond_mean = float(bonds["mean"])
        bond_mean -= float(bonds.get("drag", 0.0) or 0.0)

    friction_key = "friction_retire" if retired else "friction_accum"
    for key in (friction_key, "expense_ratio", "rebalance_cost"):
        if key not in returns:
            raise ReturnAdapterRejected(
                "declared friction term %s is missing; the adapter will not "
                "assume zero" % key)
    friction = (float(returns[friction_key]) + float(returns["expense_ratio"])
                + float(returns["rebalance_cost"]))
    gross = w_equity * float(equity_mean) + (1.0 - w_equity) * float(bond_mean)
    return gross - friction


def f_chain(old_snapshot: dict, new_vintage_snapshot: Optional[dict] = None,
            *, age=None):
    """`(F_oo, F_no)` for the realized bridge, or a refusal.

    `F_oo` is the statistic stored in the archived snapshot that was current at
    the observation. `F_no` is the same archived old plan re-run under the
    current model/rule/data vintage, which is a second snapshot rather than a
    recomputation from today's inputs -- §1.2 forbids that reconstruction.

    Both must describe the same plan version and the same period; §1.2 says
    that when those fields do not match the answer is `unknown`, not a
    best-effort number.
    """
    f_oo = forecast_statistic(old_snapshot, age=age)
    if new_vintage_snapshot is None:
        raise FChainUnavailable(
            "no new-vintage counterfactual snapshot for the archived old plan")
    for field in ("plan_version_id",):
        if old_snapshot.get(field) != new_vintage_snapshot.get(field):
            raise FChainUnavailable(
                "F_oo and F_no disagree on %s; the counterfactual must hold "
                "the archived old plan fixed" % field)
    if (old_snapshot.get("engine_build_id")
            == new_vintage_snapshot.get("engine_build_id")):
        raise FChainUnavailable(
            "F_no shares the old build identity, so it is not a new-vintage "
            "counterfactual and the model-update term would be zero by "
            "construction")
    return f_oo, forecast_statistic(new_vintage_snapshot, age=age)


#: How `F_no` was obtained. Carried in the API response because the two are
#: not equally strong evidence and the user should not have to guess which.
SAME_BUILD_DETERMINISM = "same_build_determinism"
MEASURED_COUNTERFACTUAL = "measured_counterfactual"
#: The build moved and the counterfactual has not been run, so the
#: model-update line is `unknown` rather than a number or a refusal.
BUILD_MOVED_UNKNOWN = "build_moved_not_yet_rerun"


def model_update_term(old_snapshot: dict, *, current_build_id: str, age=None,
                      new_vintage_snapshot: Optional[dict] = None):
    """`(F_oo, F_no, basis)` for the model-update line.

    The common case for a desktop app is that the user has not updated it
    between making a forecast and reviewing it, and then there is no
    counterfactual to run: the archive's own replay contract states that the
    same build on the same resolved inputs under the same protocol reproduces
    a byte-identical result (`persistence.replay_snapshot` refuses when the
    hash differs). So `F_no == F_oo` exactly, and the model-update term is
    zero by *proof* rather than by the assumption §1.2 warns about. That is
    reported as `same_build_determinism`, not silently as a measurement.

    When the build has moved, nothing here can stand in for the re-run --
    §1.2 forbids reconstructing it from today's inputs -- so a counterfactual
    snapshot is required and its absence is a refusal.
    """
    f_oo = forecast_statistic(old_snapshot, age=age)
    old_build = old_snapshot.get("engine_build_id")
    if not old_build:
        raise FChainUnavailable("archived snapshot has no engine build id")
    if old_build == current_build_id:
        if new_vintage_snapshot is not None:
            raise FChainUnavailable(
                "a new-vintage counterfactual was supplied for a build that "
                "has not changed; one of the two is wrong and guessing which "
                "would put a fabricated number on the model-update line")
        return f_oo, f_oo, SAME_BUILD_DETERMINISM
    if new_vintage_snapshot is None:
        # Not a refusal. Refusing here would break the review for every user
        # the first time they update the app, which is the normal case for a
        # desktop program -- and the thing they came for, market versus
        # behaviour, does not depend on `F_no` at all. §1.2 forbids
        # RECONSTRUCTING it; it does not require withholding everything else.
        # The waterfall reports the terms that need it as unknown.
        return f_oo, None, BUILD_MOVED_UNKNOWN
    f_oo, f_no = f_chain(old_snapshot, new_vintage_snapshot, age=age)
    return f_oo, f_no, MEASURED_COUNTERFACTUAL


def archived_plan_inputs(snapshot: dict) -> dict:
    """The archived old plan, exactly as it was resolved when the run happened.

    §1.2: `F_no` is "the new-vintage counterfactual for that same archived old
    plan". The inputs come from the snapshot, never from today's config --
    "no reconstruction from today's defaults is allowed" -- so this refuses a
    snapshot that cannot supply them rather than filling in.
    """
    resolved = snapshot.get("resolved_input")
    if resolved is None:
        raw = snapshot.get("resolved_input_json")
        if isinstance(raw, str):
            import json
            resolved = json.loads(raw)
    if not isinstance(resolved, dict):
        raise FChainUnavailable(
            "archived snapshot carries no resolved inputs, so the old plan "
            "cannot be re-run under a new vintage")
    return resolved


def counterfactual_request(old_snapshot: dict, *, current_build_id: str) -> dict:
    """What a caller must run to produce `F_no`, assembled from the archive.

    Returns the request rather than running it: the engine call, its worker
    budget and its snapshot write belong to the run orchestrator, and this
    module has no business owning any of them. What it does own is making the
    request impossible to build wrongly -- same archived plan, same period,
    same protocol, a *different* build.
    """
    old_build = old_snapshot.get("engine_build_id")
    if not old_build:
        raise FChainUnavailable("archived snapshot has no engine build id")
    if old_build == current_build_id:
        raise FChainUnavailable(
            "the current build is the archived build, so there is no "
            "new-vintage counterfactual to run and the model-update term is "
            "zero by construction rather than by measurement")
    return {
        "resolved_input": archived_plan_inputs(old_snapshot),
        "plan_id": old_snapshot.get("plan_id"),
        "plan_version_id": old_snapshot.get("plan_version_id"),
        "protocol": old_snapshot.get("protocol"),
        "engine_build_id": current_build_id,
        "purpose": "f_no_counterfactual",
    }


def run_counterfactual(request: dict, runner) -> dict:
    """Execute an `f_no_counterfactual` request and return an F_no snapshot.

    This is the mirror image of `persistence.replay_snapshot`, and the
    difference is the whole point. A replay demands that the build match and
    that the deterministic result hash come back identical; a counterfactual
    demands that the build *differ* and makes no claim about the result, since
    the number moving is exactly what is being measured.

    The protocol is carried from the archive rather than chosen here: paths,
    seed and dist_paths must be the ones the old run used, or the difference
    between `F_oo` and `F_no` would include Monte Carlo noise and get reported
    to the user as a model update.

    `runner` has `replay_snapshot`'s signature -- `(config, paths, seed,
    dist_paths) -> result` -- so `engine_adapter.run_full` can be passed directly.
    The returned dict is shaped for `f_chain`; writing it to the archive is the
    orchestrator's business, not this module's.
    """
    if request.get("purpose") != "f_no_counterfactual":
        raise FChainUnavailable(
            "run_counterfactual will only execute a request built by "
            "counterfactual_request; got purpose %r"
            % (request.get("purpose"),))
    protocol = request.get("protocol")
    if not isinstance(protocol, dict):
        raise FChainUnavailable(
            "the archived run protocol is missing, so the counterfactual "
            "cannot hold paths and seed fixed and any difference it reported "
            "would be partly Monte Carlo noise")
    missing = [k for k in ("paths", "seed", "dist_paths") if k not in protocol]
    if missing:
        raise FChainUnavailable(
            "archived protocol does not state %s, so the counterfactual "
            "cannot be run under the old run's own protocol"
            % ", ".join(sorted(missing)))
    result = runner(request["resolved_input"], int(protocol["paths"]),
                    int(protocol["seed"]), int(protocol["dist_paths"]))
    if not isinstance(result, dict):
        raise FChainUnavailable("counterfactual runner returned %r, not a run "
                                "result" % (type(result).__name__,))
    return {
        "engine_build_id": request["engine_build_id"],
        "plan_id": request.get("plan_id"),
        "plan_version_id": request.get("plan_version_id"),
        "protocol": protocol,
        "resolved_input": request["resolved_input"],
        "result": result,
        "purpose": "f_no_counterfactual",
    }


def _parse_instant(text):
    from datetime import datetime, timezone
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        raise LedgerError("stored instant %r has no timezone" % (text,))
    return value.astimezone(timezone.utc)
