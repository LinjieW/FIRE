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
    ("457", "pretax_401k"),
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

BUCKETS = ("pretax_401k", "roth_ira", "hsa", "taxable")


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
