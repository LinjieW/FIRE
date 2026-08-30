"""D1 broker-CSV import — local-only parsing into the four account buckets.

PRIVACY: this runs inside the local server process; nothing leaves the
machine. The parser returns ACCOUNT-LEVEL aggregates only (label, bucket
guess, total, position count) — individual positions, symbols, and raw rows
are never echoed back, logged, or stored.

Format handling is deliberately generic rather than per-broker exact:
real brokerage exports drift year to year, so we detect a header row by
column-name candidates, split multi-account files on their non-CSV
section-title lines ("Positions for account X …"), and classify accounts
by name keywords. Anything unrecognized lands in `unassigned` for the user to
assign manually in the UI — never silently guessed into a bucket.
"""
import csv
import io
import math
import re

# column-name candidates (lowercased, punctuation-insensitive substring match)
_VALUE_COLS = ("current value", "market value", "mkt val", "total value",
               "value", "balance", "amount")
_ACCT_COLS = ("account name", "account type", "account number", "account")
_SYM_COLS = ("symbol", "ticker", "investment name", "fund name", "description")

# account-name keyword -> bucket (order matters: roth before generic ira)
_BUCKET_RULES = (
    ("roth", "roth_ira"),
    ("hsa", "hsa"),
    ("health savings", "hsa"),
    ("401", "pretax_401k"),
    ("403", "pretax_401k"),
    # OPEN_ITEMS E37. This used to fold a 457 into the pre-tax 401(k), which
    # charged it a 10% early-withdrawal penalty it does not have -- the one
    # feature that makes a governmental 457(b) worth modelling separately, and
    # the one an early retiree cares about most. It has its own bucket now.
    #
    # **This changes an import that already worked**: money a user imported
    # from a 457 moves from the penalised bucket to an unpenalised one. That
    # is the correction, not a side effect, and `limitations` says so.
    ("457", "gov_457b"),
    ("traditional", "pretax_401k"),
    ("rollover", "pretax_401k"),
    ("sep", "pretax_401k"),
    ("ira", "pretax_401k"),
    ("individual", "taxable"),
    ("brokerage", "taxable"),
    ("joint", "taxable"),
    ("taxable", "taxable"),
    ("trust", "taxable"),
)

#: Derived from the declaration rather than written out, so a bucket the
#: importer can produce is always a bucket the engine can hold.
def _buckets():
    import os
    import sys
    server = os.path.dirname(os.path.abspath(__file__))
    if server not in sys.path:
        sys.path.insert(0, server)
    import account_schema as _schema
    return tuple(account.field for account in _schema.US_ACCOUNT_TYPES
                 if account.field)


BUCKETS = _buckets()


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).strip()


def _money(s) -> float:
    """'$1,234.56' / '(123.45)' / '--' / '1234' -> float (0.0 if blank)."""
    t = str(s or "").strip().replace("$", "").replace(",", "")
    if not t or t in ("--", "-", "n/a", "na"):
        return 0.0
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        v = float(t)
    except ValueError:
        return 0.0
    if not math.isfinite(v):
        return 0.0
    return -v if neg else v


def _safe_label(label: str) -> str:
    """Return a short display label without control characters/account IDs."""
    label = re.sub(r"[\x00-\x1f\x7f]+", " ", str(label or ""))
    label = re.sub(r"[<>&\"']+", " ", label)
    label = re.sub(r"(?<![A-Za-z])(?:[A-Za-z]?\d[ -]?){3,}(?![A-Za-z])",
                   " account ", label)
    label = re.sub(r"\s+", " ", label).strip(" .,-_")
    return label[:80] or "unlabeled"


def classify_account(label: str):
    lab = _norm(label)
    for kw, bucket in _BUCKET_RULES:
        if kw in lab:
            return bucket
    return None


def _find_col(header: list, candidates) -> int:
    hn = [_norm(h) for h in header]
    for cand in candidates:                  # candidate priority order
        for i, h in enumerate(hn):
            if h and cand in h:
                return i
    return -1


def _is_header(row: list) -> bool:
    if len(row) < 2:
        return False
    hits = sum(1 for group in (_VALUE_COLS, _ACCT_COLS, _SYM_COLS)
               if _find_col(row, group) >= 0)
    return hits >= 2


def parse_broker_csv(text: str) -> dict:
    """Parse a positions-export CSV from any major US brokerage (or any
    table with recognizable columns). Returns account aggregates + a bucket
    suggestion, or {'error': ...} when nothing parseable is found."""
    if not text or len(text) > 5_000_000:
        return {"error": "empty or oversized file"}

    accounts: dict = {}       # label -> {"total": float, "positions": int}
    warnings: list = []
    section_label = None      # Schwab-style "Positions for account X" title
    header, vi, ai, si = None, -1, -1, -1

    # Parse the complete stream so RFC-4180 quoted fields may contain newlines.
    # A line-at-a-time reader silently split those records into corrupt rows.
    try:
        rows = csv.reader(io.StringIO(text.replace("\r\n", "\n").replace("\r", "\n")),
                          strict=True)
        rows = list(rows)
    except csv.Error:
        return {"error": "malformed CSV"}

    for row in rows:
        if not row or not any(str(cell).strip() for cell in row):
            header = None      # blank record ends a section's table
            continue

        if header is None:
            if _is_header(row):
                header = row
                vi = _find_col(row, _VALUE_COLS)
                ai = _find_col(row, _ACCT_COLS)
                si = _find_col(row, _SYM_COLS)
                continue
            # non-table line: a Schwab-style section title?
            joined = " ".join(row)
            m = re.search(r"account\s+(.{2,60}?)(?:\s+as of|\s*$)",
                          joined, re.IGNORECASE)
            if m and "positions" in joined.lower():
                section_label = m.group(1).strip().strip('."')
            continue

        # data row under a known header
        if vi < 0 or vi >= len(row):
            continue
        val = _money(row[vi])
        if val == 0.0:
            continue
        sym = _norm(row[si]) if 0 <= si < len(row) else ""
        if any(k in sym for k in ("total", "subtotal", "account total")):
            continue                               # summary rows
        label = (row[ai].strip() if 0 <= ai < len(row) and row[ai].strip()
                 else (section_label or "unlabeled"))
        acc = accounts.setdefault(label, {"total": 0.0, "positions": 0})
        new_total = acc["total"] + val
        if not math.isfinite(new_total):
            warnings.append("one or more non-finite account totals were ignored")
            continue
        acc["total"] = new_total
        acc["positions"] += 1

    if not accounts:
        return {"error": "unrecognized format: no value column found "
                         "(export a positions CSV and retry)"}

    out_accounts = []
    suggestion = {b: 0.0 for b in BUCKETS}
    suggestion["unassigned"] = 0.0
    used_labels = set()
    for label, acc in accounts.items():
        bucket = classify_account(label)
        display = _safe_label(label)
        base, suffix = display, 2
        while display in used_labels:
            display = f"{base[:72]} {suffix}"
            suffix += 1
        used_labels.add(display)
        out_accounts.append({"label": display, "bucket": bucket,
                             "total": round(acc["total"], 2),
                             "positions": acc["positions"]})
        suggestion[bucket or "unassigned"] += acc["total"]
    for k in suggestion:
        suggestion[k] = round(suggestion[k], 2)
    if suggestion["unassigned"] > 0:
        warnings.append("some accounts could not be classified — assign "
                        "them manually before applying")
    out_accounts.sort(key=lambda a: -a["total"])
    return {"accounts": out_accounts, "suggestion": suggestion,
            "warnings": warnings}


# ---------------------------------------------------------------------------
# Phase 2 · transaction exports for the annual review.
#
# A different parser from `parse_broker_csv` above, and deliberately so: that
# one reads a POSITIONS export (what you hold) to seed starting balances; this
# reads a TRANSACTIONS export (what moved, and when) to seed a check-in's flow
# lines. They share the privacy posture and the "never silently guess" rule and
# nothing else.
#
# The mapping below is the whole risk surface. A word that means two different
# things -- "transfer", which is a contribution from outside and an internal
# move between your own accounts, or "distribution", which is a fund payout and
# a retirement withdrawal -- must not be resolved by guessing. Those land in
# `unmapped` for the user to classify, because a wrong category does not look
# wrong: it produces a plausible waterfall attributing the user's own behaviour
# to the wrong line.
# ---------------------------------------------------------------------------

_DATE_COLS = ("trade date", "settlement date", "run date", "date", "posted",
              "activity date", "transaction date")
_ACTION_COLS = ("action", "activity", "transaction type", "type",
                "description", "transaction", "memo")
_AMOUNT_COLS = ("amount", "net amount", "total amount", "transaction amount",
                "credit", "debit", "cash flow")

# ---------------------------------------------------------------------------
# How a needle is matched, and why it is not a plain substring.
#
# Real exports put the SECURITY NAME in the same field as the action:
# "DIVIDEND RECEIVED ISHARES RUSSELL 2000 ETF". A bare substring search finds
# "sell" inside "ru-SELL" and "buy" inside "BEST BUY", so the first version of
# this file silently discarded every row naming a Russell fund, Best Buy, or
# Intercontinental Exchange ("exchange in"). Two independent reviewers found
# the same class of defect from different angles.
#
# So: a single-word needle must match at the START of the action, where the
# verb actually is; a multi-word needle may match anywhere, because two words
# in sequence are not something a security name produces by accident.
# ---------------------------------------------------------------------------

def _matches(text: str, needle: str) -> bool:
    if " " in needle:
        return re.search(r"\b" + re.escape(needle), text) is not None
    return text.startswith(needle)


#: Rows that move nothing across the portfolio boundary.
#:
#: Trades are obvious. Less obvious, and the reason this list is longer than it
#: looks: a dividend, interest payment or fund capital-gain distribution paid
#: INTO the same account is portfolio return, not an external flow -- it is
#: already inside the closing value. Booking one as an inflow makes Modified
#: Dietz subtract it from the numerator, so the market line shrinks and the
#: income line grows by exactly the same amount. The total still reconciles and
#: the residual does not move, which is precisely why it would never be caught
#: by looking at the result. Money the user actually takes OUT shows up as its
#: own withdrawal row.
_INTERNAL = (
    "you bought", "you sold", "bought", "sold", "buy", "sell",
    "reinvest", "reinvestment", "exchange in", "exchange out",
    "merger", "split", "name change", "redemption", "purchase",
    "dividend received", "qualified dividend", "ordinary dividend",
    "dividend", "capital gain", "interest earned", "interest income",
    "interest received",
)

#: Unambiguous phrases resolved BEFORE the ambiguity gate below.
#:
#: "REQUIRED MINIMUM DISTRIBUTION" is the least ambiguous string a retirement
#: export contains, and the bare word "distribution" in the ambiguity list was
#: swallowing it -- along with "LONG-TERM CAPITAL GAINS DISTRIBUTION" -- so two
#: rules written for exactly those cases were unreachable.
_RESOLVED_FIRST = (
    ("required minimum distribution", "spending"),
    ("required minimum", "spending"),
    ("capital gains distribution", ""),
    ("capital gain distribution", ""),
    ("dividend distribution", ""),
)

#: Words the user has to resolve, each with the reason, because "we could not
#: tell" is only useful if it says what the two readings were.
_AMBIGUOUS = (
    ("rollover", "a rollover moves money between your own accounts (no net "
                 "flow) or brings it in from an outside plan (a contribution)"),
    ("transfer", "a transfer can be new money arriving from a bank or an "
                 "internal move between accounts you already own"),
    ("distribution", "a distribution can be a fund paying out (which stays "
                     "inside the portfolio) or you taking money out"),
    ("journal", "a journal entry is an internal bookkeeping move whose real "
                "meaning depends on both sides"),
    ("conversion", "a Roth conversion moves money between your own accounts "
                   "and is not a contribution or a withdrawal"),
    ("recharacter", "a recharacterization re-labels a prior contribution "
                    "rather than adding money"),
)

#: Action words -> ledger category, first match wins in this order.
#:
#: Tax rules precede everything that mentions a dividend: brokers title a
#: withholding row after the payment it was taken from ("NRA TAX WITHHELD ON
#: DIVIDEND"), and resolving that to income books a cost as a gain.
_ACTION_RULES = (
    ("foreign tax", "tax"),
    ("tax withheld", "tax"),
    ("withholding", "tax"),
    ("federal tax", "tax"),
    ("state tax", "tax"),
    ("nra tax", "tax"),
    ("employee contribution", "net_contribution"),
    ("employer contribution", "net_contribution"),
    ("employee deferral", "net_contribution"),
    ("elective deferral", "net_contribution"),
    ("employer match", "net_contribution"),
    ("safe harbor match", "net_contribution"),
    ("safe harbor", "net_contribution"),
    ("profit sharing", "net_contribution"),
    ("payroll contribution", "net_contribution"),
    ("payroll deduction", "net_contribution"),
    ("contribution", "net_contribution"),
    # Interest the broker CHARGES is a cost. The bare word alone cannot tell
    # the two apart, and the earned side is internal return anyway, so only the
    # cost side has a rule.
    ("margin interest", "fee"),
    ("interest charged", "fee"),
    ("interest expense", "fee"),
    ("interest paid", "fee"),
    ("advisory fee", "fee"),
    ("management fee", "fee"),
    ("account fee", "fee"),
    ("service fee", "fee"),
    ("maintenance fee", "fee"),
    ("expense ratio", "fee"),
    ("commission", "fee"),
    ("fee", "fee"),
    ("rmd", "spending"),
    ("cash withdrawal", "spending"),
    ("withdrawal", "spending"),
    ("check paid", "spending"),
    ("bill payment", "spending"),
)

MAX_TRANSACTION_ROWS = 20_000


def _parse_date(text: str):
    """`YYYY-MM-DD` from the handful of shapes brokers actually emit, or None."""
    raw = (text or "").strip()
    if not raw:
        return None
    raw = raw.split(" ")[0].split("T")[0]
    patterns = (
        (r"^(\d{4})-(\d{2})-(\d{2})$", (1, 2, 3)),
        (r"^(\d{1,2})/(\d{1,2})/(\d{4})$", (3, 1, 2)),
        (r"^(\d{4})/(\d{1,2})/(\d{1,2})$", (1, 2, 3)),
    )
    for pattern, (y, m, d) in patterns:
        hit = re.match(pattern, raw)
        if hit:
            year, month, day = hit.group(y), hit.group(m), hit.group(d)
            try:
                if not (1 <= int(month) <= 12 and 1 <= int(day) <= 31):
                    return None
            except ValueError:
                return None
            return "%s-%02d-%02d" % (year, int(month), int(day))
    return None


def classify_transaction(action: str):
    """`(category, reason)`.

    `category` is a category name, `""` for a row that is not an external cash
    flow at all, or `None` when the user must decide.

    Order matters and is the whole design: internal rows first (a trade or a
    reinvestment is not a flow no matter what else the string says), then the
    phrases that are unambiguous despite containing an ambiguous word, then the
    ambiguity gate, then the general mapping. Getting this order wrong does not
    produce an error -- it produces a waterfall that reconciles and blames the
    wrong thing.
    """
    text = _norm(action)
    if not text:
        return None, "no action or description column value"
    for word in _INTERNAL:
        if _matches(text, word):
            return "", ("not a flow into or out of the portfolio -- a trade, "
                        "a reinvestment, or a payment that stayed in the "
                        "account and is already in its closing value")
    for phrase, category in _RESOLVED_FIRST:
        if _matches(text, phrase):
            if category:
                return category, ""
            return "", ("a fund distribution paid into the account, which is "
                        "portfolio return rather than an external flow")
    for word, reason in _AMBIGUOUS:
        if _matches(text, word):
            return None, reason
    for word, category in _ACTION_RULES:
        if _matches(text, word):
            return category, ""
    return None, "no rule matches this action"


def parse_transactions_csv(text: str) -> dict:
    """Transactions export -> proposed check-in flow lines.

    Proposed, never recorded. The ledger is append-only with immutability
    triggers, so a mis-parsed import cannot be taken back; the user confirms
    what this returns and the existing record endpoint does the writing.

    PRIVACY: same posture as the positions parser -- this runs in the local
    server process and nothing leaves the machine. It does return per-row
    dates, amounts and the action text, because the user cannot check a
    classification they cannot see; none of it is logged or stored.
    """
    if not text or len(text) > 5_000_000:
        return {"error": "empty or oversized file"}
    try:
        rows = list(csv.reader(
            io.StringIO(text.replace("\r\n", "\n").replace("\r", "\n")),
            strict=True))
    except csv.Error:
        return {"error": "malformed CSV"}
    if len(rows) > MAX_TRANSACTION_ROWS:
        return {"error": "more than %d rows" % MAX_TRANSACTION_ROWS}

    header = None
    di = ai = mi = -1
    lines, unmapped, skipped = [], [], 0
    warnings = []
    for row in rows:
        if not row or not any(str(cell).strip() for cell in row):
            header = None
            continue
        if header is None:
            if _find_col(row, _DATE_COLS) >= 0 and _find_col(row, _AMOUNT_COLS) >= 0:
                header = row
                di = _find_col(row, _DATE_COLS)
                ai = _find_col(row, _ACTION_COLS)
                mi = _find_col(row, _AMOUNT_COLS)
            continue
        if di >= len(row) or mi >= len(row):
            continue
        when = _parse_date(row[di])
        amount = _money(row[mi])
        if when is None or amount is None or amount == 0:
            continue
        action = row[ai] if 0 <= ai < len(row) else ""
        category, reason = classify_transaction(action)
        record = {"occurred_at": when + "T12:00:00+00:00",
                  "amount": amount, "action": _safe_label(str(action))}
        if category == "":
            skipped += 1
        elif category is None:
            unmapped.append({**record, "reason": reason})
        else:
            lines.append({**record, "category": category})

    if header is None:
        return {"error": "no table with a date column and an amount column"}
    if not lines and not unmapped:
        return {"error": "no dated cash-flow rows found"}
    if unmapped:
        warnings.append("%d row(s) need a category before they can be used"
                        % len(unmapped))
    if skipped:
        warnings.append("%d row(s) were trades or transfers inside the "
                        "portfolio and move nothing in or out" % skipped)
    dates = sorted(line["occurred_at"][:10] for line in lines + unmapped)
    return {"lines": lines, "unmapped": unmapped,
            "skipped_not_a_flow": skipped, "warnings": warnings,
            "period": {"first": dates[0], "last": dates[-1]} if dates else None,
            "categories": list(_CATEGORIES_FOR_UI)}


#: Kept beside the rules so the UI's picker cannot drift from what the seam
#: accepts; `tests/test_review_panel.py` pins that against `attribution`.
_CATEGORIES_FOR_UI = ("net_contribution", "income", "spending", "tax", "fee",
                      "life_event")
