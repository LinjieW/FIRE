"""The review view: what you decided, and what has moved under it since.

ROADMAP 4.0 Phase 4, the last link in the 3.0 decision loop. Its opening
condition was that a DecisionPacket died with the process, so there was
nothing to review; archive schema v10 fixed that half, and this is the other.

**It does not score the decision, and that is the design rather than a gap.**

The roadmap words it as "the decision you made then vs what actually
happened", and read literally that is the most misleading thing this app
could build. A packet's numbers are `lifetime_success` and a terminal
distribution: claims about thousands of paths over decades. One year of
lived outcome is one sample from that distribution. It cannot confirm the
claim and it cannot refute it, and a view that put the two side by side
would invite exactly that reading -- "I chose the robust option and my
portfolio fell, so the model was wrong", or worse, the reverse. A single
good year is not evidence that a plan is safe.

So the comparison this makes is a different one, and everything in it is
observable:

**What you decided, when, and why.** From the packet and its own transition
ledger. `deferred` with a reason is a decision; `open` a year later is
information too.

**Whether the plan you decided about is still the plan you have.** The packet
names the plan version it was computed against. If your plan has moved since,
the packet's numbers describe something you no longer own -- and the leaves
that changed are listed, because "your plan changed" is not actionable and
"you decided this when your spending assumption was $40k and it is now $52k"
is.

**Whether the model that produced it has moved.** Same reasoning, one level
down: a packet computed under a different engine version is not directly
comparable with what the app would say today.

**What has been recorded since.** The check-ins whose period began after the
decision, so the review has the user's own observations in front of it. Their
attribution is `checkin_seam`'s job and is not duplicated here.

What a reader should take from all this is not a verdict but a question:
knowing what you know now, would you decide the same way? The app has no
business answering that one.
"""
from __future__ import annotations

import sqlite3
from typing import Any, Optional

import decision_archive as DA


class DecisionReviewError(RuntimeError):
    """A refusal with a name and a status, in the shape the seams use."""

    def __init__(self, message: str, *, code: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _invalid(message: str) -> DecisionReviewError:
    return DecisionReviewError(message, code="invalid_request", http_status=400)


def leaves(node: Any, prefix: str = "") -> dict:
    """Every leaf of a config, as `path -> value`.

    An array is a leaf, matching the walk the attribution inventory and
    `test_ui_server_seams` already use. A second set of rules for what counts
    as a leaf would make two answers to one question.
    """
    if isinstance(node, dict) and node:
        out = {}
        for key, value in node.items():
            out.update(leaves(value, "%s.%s" % (prefix, key) if prefix else key))
        return out
    return {prefix: node}


def config_changes(before: dict, after: dict) -> list:
    """Leaves that differ, as `{path, was, now}`, sorted by path.

    Added and removed leaves are reported with `None` on the missing side and
    a flag saying which, rather than being skipped: a leaf that appeared
    between two versions is a change to the plan even though neither value is
    "wrong".
    """
    old, new = leaves(before), leaves(after)
    rows = []
    for path in sorted(set(old) | set(new)):
        was, now = old.get(path), new.get(path)
        if was == now:
            continue
        rows.append({"path": path, "was": was, "now": now,
                     "appeared": path not in old,
                     "removed": path not in new})
    return rows


def _instant(iso: str):
    """Parse an ISO stamp, accepting both spellings this archive contains.

    `persistence.utc_now()` writes `...Z`; `datetime.isoformat()` writes
    `...+00:00`. They denote the same instant and sort DIFFERENTLY as
    strings -- `Z` is 0x5A and `+` is 0x2B -- so a `due` decided by comparing
    the raw text would be wrong whenever the two spellings met. The first
    version of this module compared strings and would have been wrong exactly
    when a caller passed a stored timestamp, which is the likely caller.
    """
    from datetime import datetime
    return datetime.fromisoformat(str(iso).replace("Z", "+00:00"))


def _add_months(iso: str, months: int) -> str:
    """`created_at` plus N months, keeping the day where the month allows.

    Calendar arithmetic rather than 30-day blocks, because a review date is a
    date a person put in a calendar. The clamp matters at month ends: a
    decision made on the 31st reviewed in six months lands on the 30th, not
    on the 1st of the following month.
    """
    stamp = _instant(iso)
    total = stamp.month - 1 + int(months)
    year = stamp.year + total // 12
    month = total % 12 + 1
    day = stamp.day
    while day > 1:
        try:
            return stamp.replace(year=year, month=month, day=day).isoformat()
        except ValueError:
            day -= 1
    return stamp.replace(year=year, month=month, day=1).isoformat()


def _plan_versions(conn: sqlite3.Connection, plan_id: str) -> list:
    return conn.execute(
        "SELECT id, normalized_config_json, normalized_config_sha256, "
        "created_at FROM plan_versions WHERE plan_id = ? "
        "ORDER BY created_at DESC, id DESC", (plan_id,)).fetchall()


def _checkins_since(conn: sqlite3.Connection, plan_id: str,
                    since: str) -> Optional[list]:
    """Check-ins whose forecast period began after the decision.

    `None` -- not `[]` -- when the ledger is not installed. A user who has
    never recorded a check-in has no observations; an archive without the
    ledger cannot answer the question at all, and reporting both as an empty
    list would say "nothing happened" for a case where nothing was measured.
    """
    try:
        rows = conn.execute(
            "SELECT checkin_id, forecast_period_start, forecast_period_end, "
            "observation_state, created_at FROM checkins WHERE plan_id = ? "
            "ORDER BY forecast_period_start", (plan_id,)).fetchall()
    except sqlite3.OperationalError:
        return None
    # Filtered here rather than in SQL, and for the reason `_instant` gives:
    # a check-in header carries `...+00:00` while `created_at` carries
    # `...Z`, so `forecast_period_start >= since` in SQLite is a comparison
    # between two spellings of the same clock. It agrees with the truth for
    # most pairs and disagrees for the ones that share a prefix, which is the
    # worst possible failure mode -- right until it is quietly wrong.
    cutoff = _instant(since)
    return [dict(row) for row in rows
            if _instant(row["forecast_period_start"]) >= cutoff]


def review(store: Any, plan_id: str, *, as_of: str) -> dict:
    """Every archived decision for a plan, with what has moved under it.

    `as_of` is supplied rather than read from the clock, for the same reason
    `set_choice_state` takes its timestamp from the caller: the answer should
    be a function of its inputs, so a test can ask what this looked like on a
    given day without moving the machine's clock.
    """
    if not isinstance(plan_id, str) or not plan_id.strip():
        raise _invalid("plan_id is required")
    if not isinstance(as_of, str) or not as_of.strip():
        raise _invalid("as_of is required: whether a review is due is a "
                       "question about a date, and guessing today's from the "
                       "server clock would make this answer unreproducible")

    import engine_adapter as ENG
    conn = DA.DecisionArchiveSeam._conn(store)
    try:
        if not DA.DecisionArchiveSeam._has_tables(conn):
            return {"plan_id": plan_id, "as_of": as_of, "packets": [],
                    "decision_archive_installed": False,
                    "scored": False, "why_not_scored": WHY_NOT_SCORED}
        rows = conn.execute(
            "SELECT packet_id, plan_version_id, question_id, question, "
            "precision, paths, seed, engine_version, review_months, "
            "created_at FROM decision_packets WHERE plan_id = ? "
            "ORDER BY created_at DESC, packet_id DESC", (plan_id,)).fetchall()

        versions = _plan_versions(conn, plan_id)
        current = versions[0] if versions else None
        by_id = {row["id"]: row for row in versions}

        packets = []
        for row in rows:
            events = conn.execute(
                "SELECT from_state, to_state, reason, at FROM "
                "decision_packet_events WHERE packet_id = ? ORDER BY seq",
                (row["packet_id"],)).fetchall()
            due_at = _add_months(row["created_at"], row["review_months"])
            entry = dict(row)
            entry["choice_state"] = DA.choice_state_from_events(events)
            entry["review_due_at"] = due_at
            entry["due"] = _instant(as_of) >= _instant(due_at)
            entry["engine_version_now"] = ENG.ENGINE_VERSION
            entry["engine_moved"] = row["engine_version"] != ENG.ENGINE_VERSION

            decided_on = by_id.get(row["plan_version_id"])
            if current is None or decided_on is None:
                # The version the packet names is not in this archive. Say so
                # rather than reporting "no changes", which is what a missing
                # row would otherwise look like.
                entry["plan_version_is_current"] = None
                entry["config_changes"] = None
                entry["config_changes_note"] = (
                    "the plan version this decision was computed against is "
                    "not in this archive, so what has changed since cannot "
                    "be read")
            elif current["id"] == row["plan_version_id"]:
                entry["plan_version_is_current"] = True
                entry["config_changes"] = []
                entry["config_changes_note"] = None
            else:
                import json
                entry["plan_version_is_current"] = False
                entry["config_changes"] = config_changes(
                    json.loads(decided_on["normalized_config_json"]),
                    json.loads(current["normalized_config_json"]))
                entry["config_changes_note"] = (
                    "this decision was computed against an earlier version of "
                    "the plan; the leaves below are what has changed since")
            entry["checkins_since"] = _checkins_since(
                conn, plan_id, row["created_at"])
            packets.append(entry)
    finally:
        conn.close()

    return {"plan_id": plan_id, "as_of": as_of, "packets": packets,
            "decision_archive_installed": True,
            "scored": False, "why_not_scored": WHY_NOT_SCORED}


#: Said in the payload, not only in this module's docstring, because the page
#: is not the only thing that will read this and the omission is the part a
#: reader is most likely to fill in for themselves.
WHY_NOT_SCORED = (
    "This does not say whether the decision was right. A packet's numbers are "
    "claims about thousands of paths over decades; what has happened since is "
    "one sample from that distribution, and one sample neither confirms nor "
    "refutes it. A good year is not evidence the plan is safe, and a bad one "
    "is not evidence the decision was wrong. What this shows instead is what "
    "has moved underneath the decision, so you can ask the question the app "
    "cannot answer: knowing what you know now, would you decide the same way?")
