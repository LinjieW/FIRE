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

PRIVACY: parsing happens in the local server process. The API returns the
AIME/PIA estimate and coarse coverage stats (year count, span) ONLY — the
year-by-year earnings history is never echoed back, logged, or stored.

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

    earnings, birth_year = {}, None
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
    return {"earnings": earnings, "birth_year": birth_year}


def bend_points(eligibility_year: int) -> tuple:
    base_year = max(min(eligibility_year - 2, AWI_MAX_YEAR), min(AWI))
    base = AWI[base_year]
    scale = base / AWI[1977]
    return (round(_BEND1_1979 * scale), round(_BEND2_1979 * scale))


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
    base_year = eligibility_year - 2
    awi_base = AWI[min(base_year, AWI_MAX_YEAR)]

    rec = {}
    for y, v in earnings.items():
        try:
            yf, vf = float(y), float(v)
        except (TypeError, ValueError):
            continue
        if (math.isfinite(yf) and yf.is_integer() and 1900 <= yf <= 2200
                and math.isfinite(vf) and 0 < vf <= 1_000_000_000):
            rec[int(yf)] = vf
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

    def factor(y: int) -> float:
        if y >= base_year or y > AWI_MAX_YEAR:
            return 1.0
        return awi_base / AWI.get(y, awi_base)

    indexed = sorted((v * factor(y) for y, v in rec.items()), reverse=True)
    top35 = indexed[:35]
    aime = int(sum(top35) / 420.0)          # floored to the dollar
    b1, b2 = bend_points(eligibility_year)
    pia = (0.90 * min(aime, b1)
           + 0.32 * max(0.0, min(aime, b2) - b1)
           + 0.15 * max(0.0, aime - b2))
    pia = int(pia * 10) / 10.0              # round DOWN to the dime
    pia_at_eligibility = pia
    cola_years = []
    for year in range(eligibility_year, current_year):
        if year in COLA:
            pia = int(pia * (1.0 + COLA[year]) * 10) / 10.0
            cola_years.append(year)
    return {
        "aime_monthly": aime,
        "pia_monthly": pia,
        "pia_monthly_at_eligibility": pia_at_eligibility,
        "bend_points": [b1, b2],
        "eligibility_year": eligibility_year,
        "years_with_earnings": len(rec),
        "projected_years": projected_years,
        "zeros_in_top35": max(0, 35 - len(top35)) + sum(1 for v in top35 if v == 0),
        "span": [min(rec), max(rec)] if rec else None,
        "awi_vintage": AWI_MAX_YEAR,
        "cola_through_year": (cola_years[-1] if cola_years else None),
    }


def import_statement(text: str, birth_year_fallback: int = None,
                     project: bool = False) -> dict:
    """Full pipeline for the API: parse + estimate. Coarse aggregates only."""
    p = parse_ssa_xml(text)
    if "error" in p:
        return p
    birth_year = p["birth_year"] or birth_year_fallback
    if not birth_year:
        return {"error": "birth year not found in the statement — "
                         "fill your current age first and retry"}
    evaluated_on = datetime.date.today()
    try:
        out = estimate_pia(
            p["earnings"], birth_year, project=project,
            current_year=evaluated_on.year)
    except (TypeError, ValueError):
        return {"error": "statement contains invalid numeric values"}
    out["birth_year"] = birth_year
    out["rule_pack"] = rule_pack_for_ssa_import(as_of=evaluated_on)
    return out
