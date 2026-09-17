"""Roadmap 11 Review Day: one immutable household review and next year's letter.

This module does not run the engine and does not schedule a notification.  It
turns already-produced review receipts into a fixed agenda, archives the exact
one-page minutes, and emits a plain-text calendar event.  The calendar file is
the honest reminder boundary for an offline desktop app.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from typing import Any, Callable, Optional

import persistence as PERSISTENCE


class ReviewDayError(RuntimeError):
    def __init__(self, message: str, *, code: str, http_status: int):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _invalid(message: str) -> ReviewDayError:
    return ReviewDayError(message, code="invalid_request", http_status=400)


def _unavailable(message: str) -> ReviewDayError:
    return ReviewDayError(message, code="review_day_unavailable",
                          http_status=422)


def _text(body: dict, key: str) -> str:
    value = body.get(key)
    if not isinstance(value, str) or not value.strip():
        raise _invalid("%s must be a non-empty string" % key)
    return value.strip()


def _date(value: Any, key: str) -> str:
    if not isinstance(value, str):
        raise _invalid("%s must be an ISO date" % key)
    try:
        parsed = date.fromisoformat(value)
    except ValueError:
        raise _invalid("%s must be an ISO date" % key) from None
    return parsed.isoformat()


def _agenda(body: dict) -> dict:
    value = body.get("agenda")
    if not isinstance(value, dict):
        raise _invalid("agenda must be an object")
    required = ("attribution", "guardrail", "due_decisions")
    missing = [key for key in required if key not in value]
    if missing:
        raise _invalid("agenda is missing %s" % ", ".join(missing))
    if not isinstance(value["due_decisions"], list):
        raise _invalid("agenda.due_decisions must be a list")
    return value


def build_memo(*, review_year: int, next_review_date: str, agenda: dict,
               future_letter: str, language: str) -> str:
    """Render the one-page minutes from the same structured facts we archive."""
    zh = language == "zh"
    attribution = agenda.get("attribution") or {}
    guardrail = agenda.get("guardrail") or {}
    decisions = agenda.get("due_decisions") or []
    verdict = (attribution.get("memo") or {}).get("verdict")
    if not verdict:
        verdict = "not recorded" if not zh else "尚未记录"
    guard_state = guardrail.get("state") or (
        "not compiled" if not zh else "尚未编译")
    lines = [
        "# %s %d" % ("年度复盘纪要" if zh else "Annual Review Day Minutes",
                     review_year),
        "",
        "## %s" % ("1. 归因" if zh else "1. Attribution"),
        ("年度复核结论：%s。" if zh else "Annual review verdict: %s.") % verdict,
        "",
        "## %s" % ("2. 护栏" if zh else "2. Guardrail"),
        ("当前护栏状态：%s。" if zh else "Current guardrail state: %s.")
        % guard_state,
        "",
        "## %s" % ("3. 到期决定" if zh else "3. Decisions due"),
    ]
    if decisions:
        for item in decisions:
            question = str(item.get("question") or item.get("question_id")
                           or ("未命名决定" if zh else "Untitled decision"))
            state = str((item.get("choice_state") or {}).get("state") or "open")
            lines.append("- %s — %s" % (question, state))
    else:
        lines.append("- %s" % ("没有到期决定。" if zh
                                else "No decisions are due."))
    lines.extend([
        "",
        "## %s" % ("4. 下次日历" if zh else "4. Next calendar date"),
        next_review_date,
        "",
        "## %s" % ("给明年的信" if zh else "Letter to next year"),
        future_letter,
        "",
        ("本纪要只重排已有复核事实；它没有重新运行模型，也不评判过去的决定是否正确。"
         if zh else
         "These minutes only reorder existing review facts. They do not rerun "
         "the model or judge whether a past decision was right."),
        "",
    ])
    return "\n".join(lines)


def _ics_escape(value: Any) -> str:
    return (str(value).replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\r\n", "\\n")
            .replace("\r", "\\n").replace("\n", "\\n"))


def _fold_ics(line: str) -> list[str]:
    """Fold at 75 UTF-8 octets without splitting a code point."""
    out, current = [], ""
    limit = 75
    for char in line:
        candidate = current + char
        if current and len(candidate.encode("utf-8")) > limit:
            out.append(current)
            current = " " + char
            limit = 75
        else:
            current = candidate
    out.append(current)
    return out


def build_ics(entry: dict) -> str:
    """One local all-day event.  No recurrence and no network semantics."""
    day = _date(entry.get("next_review_date"), "next_review_date")
    compact = day.replace("-", "")
    review_id = _text(entry, "review_day_id")
    stamp = str(entry.get("created_at") or (day + "T00:00:00Z"))
    stamp = stamp.replace("-", "").replace(":", "")
    if "+0000" in stamp:
        stamp = stamp.replace("+0000", "Z")
    if "." in stamp:
        stamp = stamp.split(".", 1)[0] + "Z"
    stamp = stamp.replace("T000000Z", "T000000Z")
    language = entry.get("language")
    summary = "年度 FIRE 复盘" if language == "zh" else "Annual FIRE Review Day"
    description = ("打开 FIRE App，按议程复核归因、护栏与到期决定，并阅读上一封信。"
                   if language == "zh" else
                   "Open FIRE App; review attribution, guardrails and due "
                   "decisions; then read last year's letter.")
    raw = [
        "BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//FIRE App//Review Day//EN",
        "CALSCALE:GREGORIAN", "BEGIN:VEVENT",
        "UID:%s@fire-app.local" % _ics_escape(review_id),
        "DTSTAMP:%s" % stamp,
        "DTSTART;VALUE=DATE:%s" % compact,
        "SUMMARY:%s" % _ics_escape(summary),
        "DESCRIPTION:%s" % _ics_escape(description),
        "END:VEVENT", "END:VCALENDAR",
    ]
    return "\r\n".join(piece for line in raw for piece in _fold_ics(line)) + "\r\n"


class ReviewDaySeam:
    def __init__(self, store: Any,
                 write: Optional[Callable[[str, Callable], Any]] = None):
        self.store = store
        self._write = write

    def _archive_write(self, key: str, mutate: Callable[[Any], Any]) -> Any:
        return mutate(self.store) if self._write is None else self._write(key, mutate)

    @staticmethod
    def _conn(store: Any) -> sqlite3.Connection:
        conn = store._connect()
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @staticmethod
    def _has_table(conn: sqlite3.Connection) -> bool:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' "
            "AND name='review_day_entries'").fetchone() is not None

    def _install(self, store: Any, conn: sqlite3.Connection) -> bool:
        if self._has_table(conn):
            return False
        release = getattr(store, "app_release_id", "fire-modeling-3.0")
        installers = (
            (6, PERSISTENCE.PersistenceStore.install_v7_schema),
            (7, PERSISTENCE.PersistenceStore.install_v8_schema),
            (8, PERSISTENCE.PersistenceStore.install_v9_schema),
            (9, PERSISTENCE.PersistenceStore.install_v10_schema),
            (10, PERSISTENCE.PersistenceStore.install_v11_schema),
            (11, PERSISTENCE.PersistenceStore.install_v12_schema),
            (12, PERSISTENCE.PersistenceStore.install_v13_schema),
            (13, PERSISTENCE.PersistenceStore.install_v14_schema),
        )
        try:
            current = int(conn.execute("PRAGMA user_version").fetchone()[0])
            for predecessor, install in installers:
                if current <= predecessor:
                    install(conn, app_release_id=release)
                    current = int(conn.execute("PRAGMA user_version").fetchone()[0])
        except PERSISTENCE.PersistenceError as exc:
            conn.rollback()
            raise _unavailable("this archive cannot store Review Day: %s" % exc)
        return True

    def complete(self, body: dict) -> dict:
        if not isinstance(body, dict):
            raise _invalid("request body must be an object")
        plan_id = _text(body, "plan_id")
        plan_version_id = _text(body, "plan_version_id")
        letter = _text(body, "future_letter")
        language = body.get("language")
        if language not in ("zh", "en"):
            raise _invalid("language must be zh or en")
        year = body.get("review_year")
        if isinstance(year, bool) or not isinstance(year, int) or year < 2000:
            raise _invalid("review_year must be an integer of 2000 or later")
        next_date = _date(body.get("next_review_date"), "next_review_date")
        if int(next_date[:4]) <= year:
            raise _invalid("next_review_date must be after the review year")
        agenda = _agenda(body)
        canonical = {
            "plan_id": plan_id, "plan_version_id": plan_version_id,
            "review_year": year, "language": language,
            "next_review_date": next_date, "agenda": agenda,
            "future_letter": letter,
        }
        agenda_json = PERSISTENCE.canonical_json_text(agenda)
        identity = hashlib.sha256(
            PERSISTENCE.canonical_json_text(canonical).encode("utf-8")).hexdigest()
        review_id = "rdy_" + identity[:24]
        memo = build_memo(review_year=year, next_review_date=next_date,
                          agenda=agenda, future_letter=letter,
                          language=language)
        created_at = PERSISTENCE.utc_now()

        def mutate(store):
            conn = self._conn(store)
            try:
                installed = self._install(store, conn)
                existing = conn.execute(
                    "SELECT review_day_id, created_at FROM review_day_entries "
                    "WHERE review_day_id=?", (review_id,)).fetchone()
                if existing is not None:
                    return {"review_day_id": review_id, "already_archived": True,
                            "review_day_installed": installed, "memo": memo,
                            "ics": build_ics({"review_day_id": review_id,
                                              "next_review_date": next_date,
                                              "language": language,
                                              "created_at": existing["created_at"]})}
                try:
                    conn.execute(
                        "INSERT INTO review_day_entries "
                        "(review_day_id,plan_id,plan_version_id,review_year,language,"
                        "next_review_date,agenda_json,memo_markdown,future_letter,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (review_id, plan_id, plan_version_id, year, language,
                         next_date, agenda_json, memo, letter,
                         created_at))
                except sqlite3.IntegrityError as exc:
                    conn.rollback()
                    raise ReviewDayError(
                        "Review Day must attach to a plan version in this plan (%s)" % exc,
                        code="plan_version_unknown", http_status=422) from None
                conn.commit()
            finally:
                conn.close()
            return {"review_day_id": review_id, "already_archived": False,
                    "review_day_installed": installed, "memo": memo,
                    "ics": build_ics({"review_day_id": review_id,
                                      "next_review_date": next_date,
                                      "language": language,
                                      "created_at": created_at})}

        return self._archive_write("review-day:" + review_id, mutate)

    def history(self, plan_id: str, *, as_of: str) -> dict:
        plan_id = plan_id.strip() if isinstance(plan_id, str) else ""
        if not plan_id:
            raise _invalid("plan_id is required")
        as_of_date = _date(as_of, "as_of")
        conn = self._conn(self.store)
        try:
            if not self._has_table(conn):
                return {"plan_id": plan_id, "entries": [], "letter_to_open": None,
                        "review_day_installed": False}
            rows = [dict(row) for row in conn.execute(
                "SELECT review_day_id,plan_version_id,review_year,language,"
                "next_review_date,memo_markdown,future_letter,created_at "
                "FROM review_day_entries WHERE plan_id=? "
                "ORDER BY created_at DESC, review_day_id DESC", (plan_id,))]
        finally:
            conn.close()
        due = [row for row in rows if row["next_review_date"] <= as_of_date]
        due.sort(key=lambda row: (row["next_review_date"], row["created_at"]),
                 reverse=True)
        return {"plan_id": plan_id, "entries": rows,
                "letter_to_open": due[0] if due else None,
                "review_day_installed": True}
