"""D2 SSA statement import — earnings XML -> AIME -> PIA (local-only).

Parses the "Your Social Security Statement" XML download from ssa.gov
(my Social Security → download your statement data) and computes the
Primary Insurance Amount with the real SSA method:

  * wage indexing: earnings in year y are multiplied by
    AWI(eligibility−2) / AWI(y); years at or after the indexing base
    (and years beyond the embedded series) count at face value;
  * AIME: highest 35 indexed years (zeros fill missing years), summed,
    divided by 420 months, floored to the dollar;
  * bend points: derived for ANY eligibility year from the 1979 statutory
    bend points ($180/$1,085) scaled by AWI(eligibility−2)/AWI(1977) and
    rounded to the dollar — the actual SSA formula, so no yearly table to
    maintain;
  * PIA: 90% / 32% / 15% of the three AIME brackets, rounded DOWN to the
    next lower dime (SSA convention).

DATA VINTAGE: the embedded AWI series is the official SSA national average
wage index, 1951–2024, and the COLA determination series is official through
2025, payable January 2026 (verified 2026-08-01 from ssa.gov).
Eligibility years whose indexing base falls beyond 2024 use the 2024 value
— fine for near-term estimates, increasingly conservative further out
(wages usually grow). Update the table when SSA publishes new years.

PRIVACY: parsing happens in the local server process. The raw XML, name,
calendar-keyed earnings history and tax rows are never logged or stored.  When
the user applies an import, the API also returns ``ssa_basis_v1``: exactly 35
already-indexed amounts with NO calendar years.  That local-only sufficient
statistic lets a saved plan insert simulated future covered earnings into the
real top-35 calculation without retaining the statement itself.

The FIRE-population default is project=False: PIA from the record as it
stands (early retirement = zeros fill the top-35). project=True appends
the latest year's earnings through age 61 (the ssa.gov statement's own
"continue working" assumption).
"""
import datetime
import math
import re
import xml.etree.ElementTree as ET

from fire_rule_pack import SSA_RULES, rule_pack_for_ssa_import

# SSA national average wage index and COLA series are derived from the
# canonical offline rule pack; this module contains no second data source.
AWI = {int(year): float(value) for year, value in SSA_RULES["awi_series"]}
AWI_MAX_YEAR = max(AWI)
_BEND1_1979 = SSA_RULES["bend1_1979"]
_BEND2_1979 = SSA_RULES["bend2_1979"]
assert AWI_MAX_YEAR == SSA_RULES["awi_through_year"]

# SSA COLAs effective for benefits payable the following January. A worker's
# PIA receives each adjustment from the year they attain 62, even if claiming
# is delayed. Official series through the latest determination available in
# this app's 2026 data vintage.
COLA = {int(year): float(value) for year, value in SSA_RULES["cola_series"]}
assert max(COLA) == SSA_RULES["cola_through_year"]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def parse_ssa_xml(text: str) -> dict:
    """Extract {year: fica_earnings} and the birth year (if present) from a
    statement XML, namespace-agnostic. Returns {'error': ...} on failure."""
    if not text or len(text) > 5_000_000:
        return {"error": "empty or oversized file"}
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return {"error": "not valid XML"}

    # Counted, not just skipped. A statement with three unparseable rows used
    # to come back reporting fewer earnings years with no way to tell that from
    # a career that genuinely had fewer -- and missing years lower the benefit,
    # so the plan reads worse for a reason the user cannot see.
    earnings, birth_year, skipped = {}, None, 0
    for el in root.iter():
        name = _local(el.tag)
        if name == "earnings":
            yr = el.get("startYear") or el.get("startyear") or el.get("year")
            fica = None
            for ch in el:
                if _local(ch.tag) in ("ficaearnings", "earningsamount"):
                    fica = ch.text
                    break
            if yr and fica is not None:
                try:
                    y, v = int(yr), float(str(fica).replace(",", ""))
                except ValueError:
                    skipped += 1
                    continue
                if (math.isfinite(v) and 1900 <= y <= 2200
                        and 0 <= v <= 1_000_000_000):
                    earnings[y] = max(earnings.get(y, 0.0), v)
        elif name in ("dateofbirth", "birthdate") and el.text:
            m = re.search(r"(19|20)\d{2}", el.text)
            if m:
                birth_year = int(m.group(0))
    if not earnings:
        return {"error": "no earnings records found — download the XML "
                         "statement from ssa.gov and retry"}
    return {"earnings": earnings, "birth_year": birth_year,
            "rows_skipped": skipped}


def bend_points(eligibility_year: int) -> tuple:
    base_year = max(min(eligibility_year - 2, AWI_MAX_YEAR), min(AWI))
    base = AWI[base_year]
    scale = base / AWI[1977]
    return (round(_BEND1_1979 * scale), round(_BEND2_1979 * scale))


def _record(earnings: dict) -> dict:
    """Finite positive annual amounts, with duplicate years resolved once."""
    rec = {}
    for y, v in (earnings or {}).items():
        try:
            yf, vf = float(y), float(v)
        except (TypeError, ValueError):
            continue
        if (math.isfinite(yf) and yf.is_integer() and 1900 <= yf <= 2200
                and math.isfinite(vf) and 0 < vf <= 1_000_000_000):
            rec[int(yf)] = vf
    return rec


def _index_factor(year: int, eligibility_year: int) -> float:
    base_year = eligibility_year - 2
    awi_base = AWI[min(base_year, AWI_MAX_YEAR)]
    if year >= base_year or year > AWI_MAX_YEAR:
        return 1.0
    return awi_base / AWI.get(year, awi_base)


def _pia_from_indexed(indexed: list[float], eligibility_year: int,
                      evaluated_year: int) -> dict:
    top35 = sorted((float(v) for v in indexed if float(v) >= 0.0),
                   reverse=True)[:35]
    top35 += [0.0] * (35 - len(top35))
    aime = int(sum(top35) / 420.0)
    b1, b2 = bend_points(eligibility_year)
    pia = (0.90 * min(aime, b1)
           + 0.32 * max(0.0, min(aime, b2) - b1)
           + 0.15 * max(0.0, aime - b2))
    pia = int(pia * 10) / 10.0
    pia_at_eligibility = pia
    cola_years = []
    for year in range(eligibility_year, evaluated_year):
        if year in COLA:
            pia = int(pia * (1.0 + COLA[year]) * 10) / 10.0
            cola_years.append(year)
    return {
        "aime_monthly": aime,
        "pia_monthly": pia,
        "pia_monthly_at_eligibility": pia_at_eligibility,
        "bend_points": [b1, b2],
        "cola_through_year": (cola_years[-1] if cola_years else None),
        "indexed_top35": top35,
    }


def make_ssa_basis_v1(earnings: dict, birth_year: int,
                      current_year: int = None) -> dict:
    """The yearless sufficient statistic persisted after an SSA import."""
    current_year = int(current_year or datetime.date.today().year)
    birth_year = int(birth_year)
    rec = _record(earnings)
    if not rec:
        raise ValueError("no finite earnings records found")
    eligibility_year = birth_year + 62
    indexed = [amount * _index_factor(year, eligibility_year)
               for year, amount in rec.items()]
    top35 = sorted(indexed, reverse=True)[:35]
    top35 += [0.0] * (35 - len(top35))
    return {
        "schema": 1,
        "birth_year": birth_year,
        "eligibility_year": eligibility_year,
        "projection_start_year": max(current_year, max(rec) + 1),
        "evaluated_year": current_year,
        "awi_vintage": AWI_MAX_YEAR,
        "indexed_top35": top35,
    }


def estimate_pia_from_basis(basis: dict, future_earnings) -> dict:
    """Insert simulated annual covered earnings into a stored yearless top-35."""
    if not isinstance(basis, dict) or basis.get("schema") != 1:
        raise ValueError("social_security.ssa_basis_v1 is invalid")
    try:
        birth_year = int(basis["birth_year"])
        eligibility_year = int(basis["eligibility_year"])
        evaluated_year = int(basis["evaluated_year"])
        projection_start = int(basis["projection_start_year"])
        awi_vintage = int(basis["awi_vintage"])
        base = [float(v) for v in basis["indexed_top35"]]
    except (KeyError, TypeError, ValueError):
        raise ValueError("social_security.ssa_basis_v1 is invalid") from None
    if (len(base) != 35 or not all(math.isfinite(v) and v >= 0.0 for v in base)
            or not 1900 <= eligibility_year <= 2300
            or not 1900 <= birth_year <= 2200
            or eligibility_year != birth_year + 62
            or not 1900 <= evaluated_year <= 2300
            or not evaluated_year <= projection_start <= 2300
            or not 1900 <= awi_vintage <= 2300):
        raise ValueError("social_security.ssa_basis_v1 is invalid")

    candidates = [(value, False) for value in base]
    seen = set()
    for item in future_earnings or ():
        try:
            year, amount = int(item[0]), float(item[1])
        except (TypeError, ValueError, IndexError):
            raise ValueError("future covered earnings are invalid") from None
        if (year < projection_start or year in seen or not math.isfinite(amount)
                or amount < 0.0 or amount > 1_000_000_000):
            raise ValueError("future covered earnings are invalid")
        seen.add(year)
        candidates.append((amount * _index_factor(year, eligibility_year), True))
    selected = sorted(candidates, key=lambda item: item[0], reverse=True)[:35]
    out = _pia_from_indexed(
        [value for value, _ in selected], eligibility_year, evaluated_year)
    out["future_years_used"] = sum(1 for _, is_future in selected if is_future)
    return out


def estimate_pia(earnings: dict, birth_year: int, project: bool = False,
                 current_year: int = None) -> dict:
    """AIME + PIA from an earnings record. project=True extends the latest
    year's earnings through age 61 (ssa.gov's continue-working posture);
    False (default) takes the record as it stands — the honest FIRE case."""
    try:
        birth_value = float(birth_year)
    except (TypeError, ValueError):
        raise ValueError("invalid birth year") from None
    if not math.isfinite(birth_value) or not birth_value.is_integer():
        raise ValueError("invalid birth year")
    birth_year = int(birth_value)
    current_year = int(current_year or datetime.date.today().year)
    if not 1900 <= birth_year <= current_year:
        raise ValueError("invalid birth year")

    eligibility_year = birth_year + 62
    rec = _record(earnings)
    if not rec:
        raise ValueError("no finite earnings records found")
    projected_years = 0
    if project and rec:
        last_year = max(rec)
        last_val = rec[last_year]
        for y in range(last_year + 1, eligibility_year):
            if y not in rec:
                rec[y] = last_val
                projected_years += 1

    indexed = [v * _index_factor(y, eligibility_year) for y, v in rec.items()]
    calc = _pia_from_indexed(indexed, eligibility_year, current_year)
    return {
        **{k: v for k, v in calc.items() if k != "indexed_top35"},
        "eligibility_year": eligibility_year,
        "years_with_earnings": len(rec),
        "projected_years": projected_years,
        "zeros_in_top35": sum(1 for v in calc["indexed_top35"] if v == 0),
        "span": [min(rec), max(rec)] if rec else None,
        "awi_vintage": AWI_MAX_YEAR,
    }


def import_statement(text: str, birth_year_fallback: int = None,
                     project: bool = False, current_year: int = None) -> dict:
    """Full pipeline for the API: parse + estimate. Coarse aggregates only."""
    p = parse_ssa_xml(text)
    if "error" in p:
        return p
    birth_year = p["birth_year"] or birth_year_fallback
    if not birth_year:
        return {"error": "birth year not found in the statement — "
                         "fill your current age first and retry"}
    evaluated_on = datetime.date.today()
    evaluated_year = int(current_year or evaluated_on.year)
    try:
        out = estimate_pia(
            p["earnings"], birth_year, project=project,
            current_year=evaluated_year)
        # Always the record AS IMPORTED, never the optional flat projection.
        # The simulation supplies the future path; persisting projected years
        # here would count them twice.
        out["ssa_basis_v1"] = make_ssa_basis_v1(
            p["earnings"], birth_year, current_year=evaluated_year)
    except (TypeError, ValueError):
        return {"error": "statement contains invalid numeric values"}
    out["birth_year"] = birth_year
    out["rule_pack"] = rule_pack_for_ssa_import(as_of=evaluated_on)
    # Carried through so the page can say it. A dropped row lowers the benefit,
    # and "35 years counted" cannot be told apart from "38 years, three of them
    # unreadable" unless the loss is reported alongside the result.
    out["rows_skipped"] = p.get("rows_skipped", 0)
    return out
