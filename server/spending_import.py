"""A year of budgeting-app exports, reduced to what a check-in needs.

ROADMAP 4.0 Phase 4: parse the annual export from YNAB / Monarch / Empower and
so on, aggregate it into a yearly total plus a category breakdown, and offer it
as the actual spending for that year's check-in. Four hard boundaries, and
ROADMAP asks for them "as contract tests": once a year, aggregates only, the
transaction stream is never persisted, no continuous sync.

**Aggregates only, and this is a deliberate difference from the broker
importer.** `csv_import.parse_transactions_csv` returns per-row dates, amounts
and action text, on the argument that a user cannot check a classification
they cannot see. That argument does not carry here. A year of personal
spending is the most revealing data this app will ever touch -- it is a record
of what somebody did, not of what they own -- and a check-in needs one number
from it.

So the per-row detail never leaves this function. What the user gets instead
is a category breakdown, plus counts of every row that was skipped and why,
which is enough to notice that something is wrong without shipping the diary
that shows what.

**Nothing here writes anything.** No caching, no credentials, no polling, no
network. This is a pure function from text to numbers, and the four boundary
flags in the payload state that so a caller cannot present it otherwise.

**Transfers are excluded and counted.** Money moved between a user's own
accounts is not spending, and counting it is the classic way an import
overstates a year by thousands. Excluded rows are reported, because silently
dropping the largest category would be worse than including it.
"""
from __future__ import annotations

import csv
import io
from typing import Optional

from csv_import import _money, _norm, _parse_date

#: Same ceiling as the broker importer, and for the same reason: a file this
#: large is not a year of one household's spending, and treating it as one
#: would spend minutes proving it.
MAX_ROWS = 50_000
MAX_BYTES = 5_000_000

#: A year, with slack for exports that run to the 366th day or start a day
#: early. Outside this the aggregate is not "a year" and saying it is would
#: put a nine-month total into an annual field.
MIN_DAYS, MAX_DAYS = 300, 380

_DATE_COLUMNS = ("date", "transaction date", "posted date", "date posted")
_CATEGORY_COLUMNS = ("category", "category group", "categories", "tag")
_OUTFLOW_COLUMNS = ("outflow", "debit", "spent", "withdrawal")
_INFLOW_COLUMNS = ("inflow", "credit", "deposit", "received")
_AMOUNT_COLUMNS = ("amount", "value", "transaction amount")
_PAYEE_COLUMNS = ("payee", "description", "merchant", "name")

#: Category or payee text that means "this is not spending". Matched on the
#: normalized value, and every hit is counted rather than quietly dropped.
_TRANSFER_MARKERS = ("transfer", "credit card payment", "payment to",
                     "balance adjustment", "starting balance",
                     "reconciliation")


def _find(header: list, candidates) -> Optional[int]:
    normalized = [_norm(cell) for cell in header]
    for candidate in candidates:
        if candidate in normalized:
            return normalized.index(candidate)
    return None


def parse_spending_csv(text: str) -> dict:
    """A year's export -> an annual total and a category breakdown.

    Returns `{"error": ...}` rather than raising, matching the other importer,
    because every one of these conditions is something a user's file can
    legitimately be and none of them is a programming fault.
    """
    if not text or len(text) > MAX_BYTES:
        return {"error": "empty or oversized file"}
    try:
        rows = list(csv.reader(
            io.StringIO(text.replace("\r\n", "\n").replace("\r", "\n")),
            strict=True))
    except csv.Error:
        return {"error": "malformed CSV"}
    rows = [r for r in rows if r and any(str(c).strip() for c in r)]
    if len(rows) > MAX_ROWS:
        return {"error": "more than %d rows" % MAX_ROWS}
    if len(rows) < 2:
        return {"error": "no rows to read"}

    header, body = rows[0], rows[1:]
    date_at = _find(header, _DATE_COLUMNS)
    if date_at is None:
        return {"error": "no date column found; this does not look like a "
                         "transactions export"}
    outflow_at = _find(header, _OUTFLOW_COLUMNS)
    inflow_at = _find(header, _INFLOW_COLUMNS)
    amount_at = _find(header, _AMOUNT_COLUMNS)
    if outflow_at is None and amount_at is None:
        return {"error": "no amount column found (looked for outflow, debit, "
                         "spent, withdrawal, amount, value)"}
    category_at = _find(header, _CATEGORY_COLUMNS)
    payee_at = _find(header, _PAYEE_COLUMNS)

    totals: dict = {}
    total = 0.0
    counted = skipped_transfer = skipped_undated = skipped_inflow = 0
    first = last = None

    for row in body:
        def cell(index):
            return row[index] if index is not None and index < len(row) else ""

        when = _parse_date(cell(date_at))
        if when is None:
            skipped_undated += 1
            continue

        marker = "%s %s" % (_norm(cell(category_at)), _norm(cell(payee_at)))
        if any(needle in marker for needle in _TRANSFER_MARKERS):
            skipped_transfer += 1
            continue

        if outflow_at is not None:
            spent = _money(cell(outflow_at))
            if inflow_at is not None:
                spent -= _money(cell(inflow_at))
        else:
            # One signed column: negative is money out, which is the
            # convention every export in this family uses.
            spent = -_money(cell(amount_at))
        if spent <= 0:
            skipped_inflow += 1
            continue

        category = (str(cell(category_at)).strip() or "uncategorised")[:60]
        totals[category] = totals.get(category, 0.0) + spent
        total += spent
        counted += 1
        first = when if first is None or when < first else first
        last = when if last is None or when > last else last

    if not counted:
        return {"error": "no spending rows found once transfers and inflows "
                         "were excluded"}

    span = _days_between(first, last)
    covers_a_year = MIN_DAYS <= span <= MAX_DAYS

    return {
        "annual_total": total,
        "by_category": sorted(
            ({"category": name, "amount": amount} for name, amount
             in totals.items()),
            key=lambda row: -row["amount"]),
        "period": {"start": first, "end": last, "days": span},
        "covers_a_year": covers_a_year,
        #: `None`, not a total, when the span is not a year. A nine-month
        #: figure dropped into an annual field is wrong by a quarter and
        #: reads exactly like a frugal year.
        "annual_total_for_checkin": total if covers_a_year else None,
        "period_note": (
            None if covers_a_year else
            "This export covers %d days, so it is not a year. The total is "
            "shown but is NOT offered for the check-in: an annual field "
            "holding nine months of spending is wrong by a quarter and looks "
            "like a frugal year." % span),
        "rows": {
            "counted": counted,
            "skipped_transfers": skipped_transfer,
            "skipped_inflows": skipped_inflow,
            "skipped_undated": skipped_undated,
        },
        #: The four boundaries ROADMAP asks be written as contract tests,
        #: stated in the payload so a caller cannot claim otherwise.
        "boundaries": {
            "aggregate_only": True,
            "transactions_persisted": False,
            "continuous_sync": False,
            "network_access": False,
        },
        "note": ("Aggregated here and nowhere else: no transaction row leaves "
                 "this function, nothing is written to disk, and there is no "
                 "sync -- you export a file and import it once a year. "
                 "Transfers between your own accounts are excluded and "
                 "counted above, because counting them would overstate the "
                 "year."),
    }


def _days_between(first: Optional[str], last: Optional[str]) -> int:
    if not first or not last:
        return 0
    from datetime import date
    start = date(*(int(p) for p in first.split("-")))
    end = date(*(int(p) for p in last.split("-")))
    return (end - start).days + 1
