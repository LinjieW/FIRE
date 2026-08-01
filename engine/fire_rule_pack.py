"""Canonical offline rule pack for calendar-varying US model inputs.

This module is a dependency leaf: it imports no FIRE engine modules and never
uses the network.  Runtime modules import their existing numeric defaults from
here, while ``rule_pack_for_run`` produces result-bound vintage/status metadata.

Important vocabulary:

* ``maintenance_due_on`` is FIRE Modeling's review deadline, not a claim that a
  law is legally valid through that date.
* ``current`` means inside that declared maintenance window, not independently
  verified tax advice.
* pack metadata belongs to the runtime/result, never to a user's plan config.
"""
from __future__ import annotations

import copy
import datetime as _dt
import hashlib
import json
from typing import Any, Mapping


RULE_PACK_SCHEMA_VERSION = 1


# JSON-safe source of truth.  Infinity is represented as ``None`` in the
# canonical payload and converted only in the tax module's runtime convenience
# table.  That keeps the content hash strict (allow_nan=False).
_PACK_PAYLOAD: dict[str, Any] = {
    "schema_version": RULE_PACK_SCHEMA_VERSION,
    "delivery": "offline_embedded",
    "runtime_network_refresh": False,
    "assembled_on": "2026-08-01",
    "external_refresh_status": "official_primary_sources_and_explicit_assumptions_verified_2026-08-01",
    "maintenance_semantics": (
        "maintenance_due_on is an app review deadline, not legal validity"
    ),
    # A machine-readable field/group ledger keeps a component-level receipt
    # from implying that every value in that component came from one source.
    # `source_class` is deliberately explicit for product and historical
    # assumptions that are not current official law.
    "field_source_ledger": [
        {
            "component_id": "us_federal_tax",
            "field_group": "federal_brackets_deductions_ltcg",
            "fields": [
                "ordinary_single", "ordinary_mfj", "std_deduction_single",
                "std_deduction_mfj", "ltcg_single", "ltcg_mfj",
            ],
            "source_class": "official_primary",
            "sources": [
                "https://www.irs.gov/newsroom/irs-releases-tax-inflation-adjustments-for-tax-year-2026-including-amendments-from-the-one-big-beautiful-bill",
                "https://www.irs.gov/irb/2025-45_IRB",
            ],
            "note": "2026 federal thresholds; Rev. Proc. 2025-32 is published in IRB 2025-45.",
        },
        {
            "component_id": "us_federal_tax",
            "field_group": "social_security_provisional_income",
            "fields": ["ss_provisional_single", "ss_provisional_mfj"],
            "source_class": "official_primary",
            "sources": ["https://www.irs.gov/publications/p915"],
            "note": "Nominal statutory thresholds; not CPI-indexed by the runtime.",
        },
        {
            "component_id": "us_federal_tax",
            "field_group": "rmd_divisors",
            "fields": ["rmd_divisors"],
            "source_class": "official_primary",
            "sources": ["https://www.irs.gov/publications/p590b"],
            "note": "Uniform Lifetime Table values used by this optional model path.",
        },
        {
            "component_id": "us_federal_tax",
            "field_group": "early_withdrawal_penalty",
            "fields": ["early_withdrawal_age", "early_withdrawal_rate"],
            "source_class": "official_primary",
            "sources": [
                "https://www.irs.gov/taxtopics/tc558",
                "https://www.irs.gov/retirement-plans/plan-participant-employee/retirement-topics-tax-on-early-distributions",
            ],
            "note": "59 1/2 and 10% federal penalty reference; exceptions and state tax are outside this model.",
        },
        {
            "component_id": "medicare_irmaa",
            "field_group": "irmaa_single_mfj",
            "fields": ["single", "mfj"],
            "source_class": "official_primary",
            "sources": [
                "https://www.cms.gov/newsroom/fact-sheets/2026-medicare-parts-b-premiums-deductibles",
            ],
            "note": "Part B+D monthly amounts converted to annual per-person tiers; current-year MAGI is a two-year-lookback proxy.",
        },
        {
            "component_id": "contribution_limits",
            "field_group": "retirement_and_ira_limits",
            "fields": ["pretax_401k_limit_y1", "roth_ira_limit_y1"],
            "source_class": "official_primary",
            "sources": [
                "https://www.irs.gov/retirement-plans/plan-participant-employee/retirement-topics-401k-and-profit-sharing-plan-contribution-limits",
                "https://www.irs.gov/retirement-plans/plan-participant-employee/retirement-topics-ira-contribution-limits",
            ],
            "note": "2026 nominal first-year reference limits; plan values remain user-editable.",
        },
        {
            "component_id": "contribution_limits",
            "field_group": "hsa_limit",
            "fields": ["hsa_limit_y1"],
            "source_class": "official_primary",
            "sources": ["https://www.irs.gov/irb/2025-21_IRB"],
            "note": "Rev. Proc. 2025-19; $4,400 is the self-only modeled cap and no family cap is implemented.",
        },
        {
            "component_id": "contribution_limits",
            "field_group": "limit_growth",
            "fields": ["irs_limit_growth"],
            "source_class": "product_assumption",
            "sources": [],
            "note": "3% annual growth is a FIRE modeling assumption, not an IRS forecast.",
        },
        {
            "component_id": "aca_marketplace",
            "field_group": "fpl_and_cliff",
            "fields": ["fpl_single_y0", "fpl_additional_person_y0", "fpl_threshold"],
            "source_class": "official_primary",
            "sources": [
                "https://www.healthcare.gov/glossary/federal-poverty-level-fpl/",
                "https://www.cms.gov/newsroom/fact-sheets/plan-year-2026-marketplace-plans-prices-fact-sheet",
            ],
            "note": "2026 coverage uses the modeled 2025 FPL basis and a 400% cliff.",
        },
        {
            "component_id": "aca_marketplace",
            "field_group": "default_scenario",
            "fields": ["default_scenario"],
            "source_class": "product_assumption",
            "sources": [],
            "note": "B_pre_IRA is a product scenario label, not an official policy field.",
        },
        {
            "component_id": "aca_marketplace",
            "field_group": "aca_pre_ira_cap",
            "fields": ["cap_pct_pre_ira"],
            "source_class": "official_primary",
            "sources": ["https://www.irs.gov/irb/2025-32_IRB"],
            "note": "Rev. Proc. 2025-25 applicable-percentage reference; runtime retains a flat 9.96% proxy rather than the full piecewise schedule.",
        },
        {
            "component_id": "aca_marketplace",
            "field_group": "aca_ira_counterfactual",
            "fields": ["cap_pct_ira"],
            "source_class": "historical_counterfactual",
            "sources": [],
            "note": "8.5% is a 2021-2025 historical IRA-era counterfactual, not a current 2026 official value.",
        },
        {
            "component_id": "ssa_benefit_rules",
            "field_group": "ssa_claiming_rules",
            "fields": [
                "fra_age", "earliest_claim_age", "latest_credit_age",
                "early_first_36_monthly_pct", "early_after_36_monthly_pct",
                "delayed_credit_annual_pct",
            ],
            "source_class": "official_primary",
            "sources": [
                "https://www.ssa.gov/benefits/retirement/planner/1960.html",
                "https://www.ssa.gov/benefits/retirement/planner/applying2.html",
                "https://www.ssa.gov/benefits/retirement/planner/delayret.html",
            ],
            "note": "FRA 67 and the 70%/100%/124% illustration apply to the 1960-and-later cohort; older cohorts and survivor rules differ.",
        },
        {
            "component_id": "ssa_statement_import",
            "field_group": "ssa_statement_series",
            "fields": [
                "awi_through_year", "cola_through_year", "bend1_1979",
                "bend2_1979", "awi_series", "cola_series",
            ],
            "source_class": "official_primary",
            "sources": [
                "https://www.ssa.gov/OACT/cola/AWI.html",
                "https://www.ssa.gov/cola/factsheets/2026.html",
                "https://www.ssa.gov/oact/COLA/bendpoints.html",
            ],
            "note": "AWI through 2024 and COLA determination through 2025, payable January 2026; future AWI is capped, not forecast.",
        },
    ],
    "components": [
        {
            "id": "us_federal_tax",
            "label": "US federal income tax",
            "source_vintage": "2026",
            "coverage_year": 2026,
            "maintenance_due_on": "2026-12-31",
            "provenance_status": "official_irs_rev_proc_2025_32",
            "provenance": {
                "sources": [
                    "https://www.irs.gov/irb/2025-45_IRB",
                    "https://www.irs.gov/newsroom/irs-releases-tax-inflation-adjustments-for-tax-year-2026-including-amendments-from-the-one-big-beautiful-bill",
                ],
                "verified_as_of": "2026-08-01",
                "conversion": "Official 2026 nominal thresholds are stored as real-dollar reference values under Rev. Proc. 2025-32 (published in IRB 2025-45); Social Security provisional-income thresholds remain nominal statutory amounts; open RMD bound uses null in canonical JSON.",
            },
            "scope": (
                "Ordinary brackets, standard deduction, LTCG stacking, "
                "Social Security provisional-income thresholds, RMD divisors, "
                "and early-withdrawal penalty."
            ),
            "values": {
                "ordinary_single": [
                    [0.0, 0.10], [12_400.0, 0.12], [50_400.0, 0.22],
                    [105_700.0, 0.24], [201_775.0, 0.32],
                    [256_225.0, 0.35], [640_600.0, 0.37],
                ],
                "ordinary_mfj": [
                    [0.0, 0.10], [24_800.0, 0.12], [100_800.0, 0.22],
                    [211_400.0, 0.24], [403_550.0, 0.32],
                    [512_450.0, 0.35], [768_700.0, 0.37],
                ],
                "std_deduction_single": 16_100.0,
                "std_deduction_mfj": 32_200.0,
                "ltcg_single": [
                    [0.0, 0.00], [49_450.0, 0.15], [545_500.0, 0.20],
                ],
                "ltcg_mfj": [
                    [0.0, 0.00], [98_900.0, 0.15], [613_700.0, 0.20],
                ],
                "ss_provisional_single": [25_000.0, 34_000.0],
                "ss_provisional_mfj": [32_000.0, 44_000.0],
                "rmd_divisors": {
                    "72": 27.4, "73": 26.5, "74": 25.5, "75": 24.6,
                    "76": 23.7, "77": 22.9, "78": 22.0, "79": 21.1,
                    "80": 20.2, "81": 19.4, "82": 18.5, "83": 17.7,
                    "84": 16.8, "85": 16.0, "86": 15.2, "87": 14.4,
                    "88": 13.7, "89": 12.9, "90": 12.2, "91": 11.5,
                    "92": 10.8, "93": 10.1, "94": 9.5, "95": 8.9,
                    "96": 8.4, "97": 7.8, "98": 7.3, "99": 6.8,
                    "100": 6.4, "101": 6.0, "102": 5.6, "103": 5.2,
                    "104": 4.9, "105": 4.6, "106": 4.3, "107": 4.1,
                    "108": 3.9, "109": 3.7, "110": 3.5, "111": 3.4,
                    "112": 3.3, "113": 3.1, "114": 3.0, "115": 2.9,
                    "116": 2.8, "117": 2.7, "118": 2.5, "119": 2.3,
                    "120": 2.0,
                },
                "early_withdrawal_age": 59.5,
                "early_withdrawal_rate": 0.10,
            },
        },
        {
            "id": "medicare_irmaa",
            "label": "Medicare IRMAA",
            "source_vintage": "2026",
            "coverage_year": 2026,
            "maintenance_due_on": "2026-12-31",
            "provenance_status": "official_cms_2026_irmaa",
            "provenance": {
                "sources": [
                    "https://www.cms.gov/newsroom/fact-sheets/2026-medicare-parts-b-premiums-deductibles",
                ],
                "verified_as_of": "2026-08-01",
                "conversion": "CMS monthly Part B and Part D IRMAA surcharges are summed and multiplied by 12 into annual per-person surcharge tiers. The published intermediate thresholds are strict lower bounds for the next tier (an exact threshold remains in the preceding tier); the final $500,000 single/$750,000 MFJ threshold is inclusive for the top tier. The two-year MAGI lookback remains outside this refresh.",
            },
            "scope": "Annual Part B and Part D surcharge tiers above the standard premium.",
            "values": {
                "single": [
                    [0.0, 0.0], [109_000.0, 1_148.4],
                    [137_000.0, 2_884.8], [171_000.0, 4_620.0],
                    [205_000.0, 6_355.2], [500_000.0, 6_936.0],
                ],
                "mfj": [
                    [0.0, 0.0], [218_000.0, 1_148.4],
                    [274_000.0, 2_884.8], [342_000.0, 4_620.0],
                    [410_000.0, 6_355.2], [750_000.0, 6_936.0],
                ],
            },
        },
        {
            "id": "contribution_limits",
            "label": "US contribution limits",
            "source_vintage": "2026",
            "coverage_year": 2026,
            "maintenance_due_on": "2026-12-31",
            "provenance_status": "official_irs_2026_limits_with_product_growth_assumption",
            "provenance": {
                "sources": [
                    "https://www.irs.gov/retirement-plans/plan-participant-employee/retirement-topics-401k-and-profit-sharing-plan-contribution-limits",
                    "https://www.irs.gov/retirement-plans/plan-participant-employee/retirement-topics-ira-contribution-limits",
                    "https://www.irs.gov/irb/2025-21_IRB",
                ],
                "verified_as_of": "2026-08-01",
                "conversion": "First-year employee limits use 2026 IRS nominal limits; Rev. Proc. 2025-19 supplies the HSA amount; the 3% annual growth remains an explicit FIRE product modeling assumption, not an IRS value; HSA is the existing self-only modeled field and no family cap is modeled.",
            },
            "scope": "First-year 401(k), Roth IRA, HSA limits and modeled annual limit growth.",
            "values": {
                "irs_limit_growth": 0.030,
                "pretax_401k_limit_y1": 24_500.0,
                "roth_ira_limit_y1": 7_500.0,
                "hsa_limit_y1": 4_400.0,
            },
        },
        {
            "id": "aca_marketplace",
            "label": "ACA marketplace",
            "source_vintage": "2026 coverage / 2025 FPL",
            "coverage_year": 2026,
            "series_through": 2025,
            "maintenance_due_on": "2026-12-31",
            "provenance_status": "official_healthcare_gov_2025_fpl_cms_2026_coverage",
            "provenance": {
                "sources": [
                    "https://www.healthcare.gov/glossary/federal-poverty-level-fpl/",
                    "https://www.cms.gov/newsroom/fact-sheets/plan-year-2026-marketplace-plans-prices-fact-sheet",
                    "https://www.irs.gov/affordable-care-act/individuals-and-families/questions-and-answers-on-the-premium-tax-credit",
                ],
                "verified_as_of": "2026-08-01",
                "conversion": "2026 Marketplace coverage retains the 2025 FPL basis used for the enrollment-year model; Rev. Proc. 2025-25 supplies the applicable-percentage reference. The existing 9.96% value is a flat proxy, not the full IRS piecewise schedule; it generally understates PTC below 300% FPL but can overstate it below 100% FPL because eligibility is not modeled. The 8.5% IRA value is a 2021-2025 historical counterfactual, not a current 2026 official value.",
            },
            "scope": "Marketplace FPL basis, 400% cliff, and contribution-rate assumptions.",
            "values": {
                "default_scenario": "B_pre_IRA",
                "fpl_single_y0": 15_650.0,
                "fpl_additional_person_y0": 5_500.0,
                "fpl_threshold": 4.0,
                "cap_pct_ira": 0.085,
                "cap_pct_pre_ira": 0.0996,
            },
        },
        {
            "id": "ssa_benefit_rules",
            "label": "Social Security benefit rules",
            "source_vintage": "2026 statutory rules",
            "maintenance_due_on": "2026-12-31",
            "provenance_status": "official_ssa_rules_verified_2026",
            "provenance": {
                "sources": [
                    "https://www.ssa.gov/cola/factsheets/2026.html",
                    "https://www.ssa.gov/benefits/retirement/planner/1960.html",
                    "https://www.ssa.gov/benefits/retirement/planner/applying2.html",
                    "https://www.ssa.gov/benefits/retirement/planner/delayret.html",
                ],
                "verified_as_of": "2026-08-01",
            "conversion": "This component retains the existing FRA/claiming-age/delayed-credit rule inputs; FRA 67 and the 70%/100%/124% illustration apply to the 1960-and-later birth cohort, not all ages or older cohorts. The 2026 SSA fact sheet's 2.8% COLA belongs to the separate statement-import component and is payable in January 2026.",
            },
            "scope": "FRA, claiming-age reduction, and delayed-retirement-credit rules.",
            "values": {
                "fra_age": 67,
                "earliest_claim_age": 62,
                "latest_credit_age": 70,
                "early_first_36_monthly_pct": 5.0 / 9.0 / 100.0,
                "early_after_36_monthly_pct": 5.0 / 12.0 / 100.0,
                "delayed_credit_annual_pct": 0.08,
            },
        },
        {
            "id": "ssa_statement_import",
            "label": "SSA statement import",
            "source_vintage": "AWI through 2024 / COLA through 2025 (payable January 2026)",
            "series_through": {"awi": 2024, "cola": 2025},
            "maintenance_due_on": "2026-12-31",
            "provenance_status": "official_ssa_series_verified_2026",
            "provenance": {
                "sources": [
                    "https://www.ssa.gov/OACT/cola/AWI.html",
                    "https://www.ssa.gov/cola/factsheets/2026.html",
                    "https://www.ssa.gov/oact/COLA/bendpoints.html",
                ],
                "verified_as_of": "2026-08-01",
                "conversion": "AWI remains capped at the latest official published 2024 series; the 2025 determination's 2.8% COLA is payable in January 2026. Future AWI values are not forecast.",
            },
            "scope": "Local statement import AWI, COLA, and statutory bend-point derivation.",
            "values": {
                "awi_through_year": 2024,
                "cola_through_year": 2025,
                "bend1_1979": 180.0,
                "bend2_1979": 1_085.0,
                "awi_series": [
                    [1951, 2799.16], [1952, 2973.32], [1953, 3139.44],
                    [1954, 3155.64], [1955, 3301.44], [1956, 3532.36],
                    [1957, 3641.72], [1958, 3673.80], [1959, 3855.80],
                    [1960, 4007.12], [1961, 4086.76], [1962, 4291.40],
                    [1963, 4396.64], [1964, 4576.32], [1965, 4658.72],
                    [1966, 4938.36], [1967, 5213.44], [1968, 5571.76],
                    [1969, 5893.76], [1970, 6186.24], [1971, 6497.08],
                    [1972, 7133.80], [1973, 7580.16], [1974, 8030.76],
                    [1975, 8630.92], [1976, 9226.48], [1977, 9779.44],
                    [1978, 10556.03], [1979, 11479.46], [1980, 12513.46],
                    [1981, 13773.10], [1982, 14531.34], [1983, 15239.24],
                    [1984, 16135.07], [1985, 16822.51], [1986, 17321.82],
                    [1987, 18426.51], [1988, 19334.04], [1989, 20099.55],
                    [1990, 21027.98], [1991, 21811.60], [1992, 22935.42],
                    [1993, 23132.67], [1994, 23753.53], [1995, 24705.66],
                    [1996, 25913.90], [1997, 27426.00], [1998, 28861.44],
                    [1999, 30469.84], [2000, 32154.82], [2001, 32921.92],
                    [2002, 33252.09], [2003, 34064.95], [2004, 35648.55],
                    [2005, 36952.94], [2006, 38651.41], [2007, 40405.48],
                    [2008, 41334.97], [2009, 40711.61], [2010, 41673.83],
                    [2011, 42979.61], [2012, 44321.67], [2013, 44888.16],
                    [2014, 46481.52], [2015, 48098.63], [2016, 48642.15],
                    [2017, 50321.89], [2018, 52145.80], [2019, 54099.99],
                    [2020, 55628.60], [2021, 60575.07], [2022, 63795.13],
                    [2023, 66621.80], [2024, 69846.57],
                ],
                "cola_series": [
                    [1975, 0.080], [1976, 0.064], [1977, 0.059],
                    [1978, 0.065], [1979, 0.099], [1980, 0.143],
                    [1981, 0.112], [1982, 0.074], [1983, 0.035],
                    [1984, 0.035], [1985, 0.031], [1986, 0.013],
                    [1987, 0.042], [1988, 0.040], [1989, 0.047],
                    [1990, 0.054], [1991, 0.037], [1992, 0.030],
                    [1993, 0.026], [1994, 0.028], [1995, 0.026],
                    [1996, 0.029], [1997, 0.021], [1998, 0.013],
                    [1999, 0.025], [2000, 0.035], [2001, 0.026],
                    [2002, 0.014], [2003, 0.021], [2004, 0.027],
                    [2005, 0.041], [2006, 0.033], [2007, 0.023],
                    [2008, 0.058], [2009, 0.000], [2010, 0.000],
                    [2011, 0.036], [2012, 0.017], [2013, 0.015],
                    [2014, 0.017], [2015, 0.000], [2016, 0.003],
                    [2017, 0.020], [2018, 0.028], [2019, 0.016],
                    [2020, 0.013], [2021, 0.059], [2022, 0.087],
                    [2023, 0.032], [2024, 0.025], [2025, 0.028],
                ],
            },
        },
    ],
}


def _canonical_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        allow_nan=False).encode("utf-8")


RULE_PACK_SHA256 = hashlib.sha256(_canonical_bytes(_PACK_PAYLOAD)).hexdigest()
RULE_PACK_ID = f"us-offline-{RULE_PACK_SHA256[:16]}"


def _component(component_id: str) -> dict[str, Any]:
    for row in _PACK_PAYLOAD["components"]:
        if row["id"] == component_id:
            return row
    raise KeyError(component_id)


def canonical_pack_payload() -> dict[str, Any]:
    """Return an isolated copy of the exact content-addressed payload."""
    return copy.deepcopy(_PACK_PAYLOAD)


# Runtime conveniences.  These are derived once from the canonical payload;
# the tests bind each consumer back to them so no module can quietly drift.
_FEDERAL_VALUES = _component("us_federal_tax")["values"]
US_FEDERAL_RULES = {
    "ordinary_single": tuple(tuple(row) for row in _FEDERAL_VALUES["ordinary_single"]),
    "ordinary_mfj": tuple(tuple(row) for row in _FEDERAL_VALUES["ordinary_mfj"]),
    "std_deduction_single": _FEDERAL_VALUES["std_deduction_single"],
    "std_deduction_mfj": _FEDERAL_VALUES["std_deduction_mfj"],
    "ltcg_single": tuple(tuple(row) for row in _FEDERAL_VALUES["ltcg_single"]),
    "ltcg_mfj": tuple(tuple(row) for row in _FEDERAL_VALUES["ltcg_mfj"]),
    "ss_provisional_single": tuple(_FEDERAL_VALUES["ss_provisional_single"]),
    "ss_provisional_mfj": tuple(_FEDERAL_VALUES["ss_provisional_mfj"]),
    "rmd_divisors": {
        int(age): divisor
        for age, divisor in _FEDERAL_VALUES["rmd_divisors"].items()
    },
    "early_withdrawal_age": _FEDERAL_VALUES["early_withdrawal_age"],
    "early_withdrawal_rate": _FEDERAL_VALUES["early_withdrawal_rate"],
}

_IRMAA_VALUES = _component("medicare_irmaa")["values"]


def _runtime_irmaa(rows: list[list[Any]]) -> tuple[tuple[float, float], ...]:
    """Convert canonical strict-lower-bound tiers to runtime tuples."""
    return tuple((float(low), surcharge) for low, surcharge in rows)


IRMAA_RULES = {
    "single": _runtime_irmaa(_IRMAA_VALUES["single"]),
    "mfj": _runtime_irmaa(_IRMAA_VALUES["mfj"]),
}
CONTRIBUTION_LIMIT_RULES = dict(
    _component("contribution_limits")["values"])
ACA_MARKETPLACE_RULES = dict(_component("aca_marketplace")["values"])
SSA_RULES = {
    **_component("ssa_benefit_rules")["values"],
    **_component("ssa_statement_import")["values"],
}


def _as_date(value: str | _dt.date) -> _dt.date:
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    if isinstance(value, str):
        try:
            return _dt.date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("as_of must be an explicit ISO date or date object")


def _group(config: Mapping[str, Any], name: str) -> Mapping[str, Any]:
    value = config.get(name) if isinstance(config, Mapping) else None
    return value if isinstance(value, Mapping) else {}


def _editable_value_state(
        configured: Mapping[str, Any],
        reference: Mapping[str, Any],
) -> tuple[str, list[str], dict[str, Any]]:
    """Compare values without pretending to know who authored a mismatch."""
    actual = {field: configured.get(field) for field in reference}
    mismatches = [
        field for field, expected in reference.items()
        if actual.get(field) != expected
    ]
    status = ("matches_pack_value" if not mismatches
              else "user_or_legacy_override")
    return status, mismatches, actual


def _component_applicability(config: Mapping[str, Any]) -> dict[str, bool]:
    state = _group(config, "state")
    true_tax = _group(config, "tax_true")
    tax_us = _group(config, "tax_us")
    ss = _group(config, "social_security")
    contributions = _group(config, "contributions")
    household = _group(config, "household")
    start_age = float(state.get("start_age", 0) or 0)
    accum_years = float(state.get("accum_years", 0) or 0)
    retire_horizon = float(state.get("retire_horizon", 0) or 0)
    primary_earnings = sum(
        float(contributions.get(field, 0) or 0)
        for field in ("base_salary_pre", "bonus_pre", "ot_income_pre")
    )
    spouse_earnings = (
        sum(float(household.get(field, 0) or 0)
            for field in ("spouse_base_salary_pre", "spouse_bonus_pre"))
        if household.get("enabled") else 0.0
    )
    primary_caps = any(
        float(contributions.get(field, 0) or 0) > 0
        for field in (
            "pretax_401k_limit_y1", "roth_ira_limit_y1", "hsa_limit_y1")
    )
    spouse_caps = any(
        float(household.get("spouse_" + field, 0) or 0) > 0
        for field in (
            "pretax_401k_limit_y1", "roth_ira_limit_y1", "hsa_limit_y1")
    )
    primary_active = primary_earnings > 0 and primary_caps
    spouse_active = bool(household.get("enabled")) and (
        spouse_earnings > 0 and spouse_caps)
    early_penalty_possible = bool(
        retire_horizon > 0
        and start_age + 1 < US_FEDERAL_RULES["early_withdrawal_age"])
    federal = bool(
        true_tax.get("enabled") or tax_us.get("progressive")
        or early_penalty_possible)
    return {
        "us_federal_tax": federal,
        "medicare_irmaa": bool(
            true_tax.get("enabled") and true_tax.get("irmaa_enabled", True)),
        "contribution_limits": bool(
            accum_years > 0 and (primary_active or spouse_active)),
        # Config-based conservative applicability.  It deliberately does not
        # claim to instrument which stochastic path first retired before 65.
        "aca_marketplace": bool(
            start_age + 1 < 65 and retire_horizon > 0),
        "ssa_benefit_rules": bool(ss.get("enabled", True)),
        "ssa_statement_import": False,
    }


def _value_evidence(
        component_id: str,
    config: Mapping[str, Any],
) -> tuple[str, list[str], dict[str, Any] | None]:
    if component_id == "contribution_limits":
        contributions = _group(config, "contributions")
        household = _group(config, "household")
        primary_earnings = sum(
            float(contributions.get(field, 0) or 0)
            for field in ("base_salary_pre", "bonus_pre", "ot_income_pre")
        )
        primary_caps = any(
            float(contributions.get(field, 0) or 0) > 0
            for field in (
                "pretax_401k_limit_y1", "roth_ira_limit_y1", "hsa_limit_y1")
        )
        spouse_earnings = sum(
            float(household.get(field, 0) or 0)
            for field in ("spouse_base_salary_pre", "spouse_bonus_pre")
        ) if household.get("enabled") else 0.0
        spouse_caps = any(
            float(household.get("spouse_" + field, 0) or 0) > 0
            for field in (
                "pretax_401k_limit_y1", "roth_ira_limit_y1", "hsa_limit_y1")
        )
        primary_active = primary_earnings > 0 and primary_caps
        spouse_active = bool(household.get("enabled")) and (
            spouse_earnings > 0 and spouse_caps)
        if not (primary_active or spouse_active):
            # Dormant fields are not evidence of an override.  Keep the
            # component's source vocabulary valid while making its non-use
            # explicit to the result-bound descriptor.
            return "matches_pack_value", [], {}

        configured: dict[str, Any] = {}
        reference: dict[str, Any] = {}
        growth_key = "contributions.irs_limit_growth"
        configured[growth_key] = contributions.get("irs_limit_growth")
        reference[growth_key] = CONTRIBUTION_LIMIT_RULES["irs_limit_growth"]
        if primary_active:
            for field in (
                    "pretax_401k_limit_y1", "roth_ira_limit_y1",
                    "hsa_limit_y1"):
                key = "contributions." + field
                configured[key] = contributions.get(field)
                reference[key] = CONTRIBUTION_LIMIT_RULES[field]
        if spouse_active:
            for field in (
                    "pretax_401k_limit_y1", "roth_ira_limit_y1",
                    "hsa_limit_y1"):
                key = "household.spouse_" + field
                configured[key] = household.get("spouse_" + field)
                reference[key] = CONTRIBUTION_LIMIT_RULES[field]
        return _editable_value_state(configured, reference)
    if component_id == "aca_marketplace":
        aca_reference = {
            key: ACA_MARKETPLACE_RULES[key]
            for key in (
                "fpl_single_y0", "fpl_additional_person_y0",
                "fpl_threshold", "cap_pct_ira", "cap_pct_pre_ira",
            )
        }
        configured = dict(_group(config, "aca"))
        configured["default_scenario"] = configured.pop(
            "scenario", ACA_MARKETPLACE_RULES["default_scenario"])
        return _editable_value_state(configured, {
            "default_scenario": ACA_MARKETPLACE_RULES["default_scenario"],
            **aca_reference,
        })
    if component_id == "us_federal_tax" and _group(
            config, "tax_us").get("progressive"):
        return _editable_value_state(
            _group(config, "tax_us"),
            {"std_deduction": US_FEDERAL_RULES["std_deduction_single"]})
    if component_id == "ssa_benefit_rules":
        return _editable_value_state(
            _group(config, "social_security"),
            {"fra_age": SSA_RULES["fra_age"]})
    return "pack", [], None


def rule_pack_for_run(
        config: Mapping[str, Any],
        *,
        as_of: str | _dt.date,
) -> dict[str, Any]:
    """Build immutable display/audit metadata for one headline run.

    The returned value is JSON-safe.  It is meant to be frozen into
    ``result.meta.rule_pack`` and must not be inserted into the plan config.
    """
    evaluated = _as_date(as_of)
    applicable = _component_applicability(config)
    rows = []
    stale_ids: list[str] = []
    review_ids: list[str] = []
    applicable_ids: list[str] = []
    for source in _PACK_PAYLOAD["components"]:
        component_id = source["id"]
        is_applicable = bool(applicable.get(component_id))
        effective_source, mismatches, configured = _value_evidence(
            component_id, config)
        maintenance_due = _dt.date.fromisoformat(
            source["maintenance_due_on"])
        if not is_applicable:
            review_status = (
                "stale" if evaluated > maintenance_due
                else ("review_required"
                      if effective_source == "user_or_legacy_override"
                      else "within_recorded_window")
            )
            status = "not_used_at_run"
        elif evaluated > maintenance_due:
            review_status = "stale"
            status = "stale"
            stale_ids.append(component_id)
        elif effective_source == "user_or_legacy_override":
            review_status = "review_required"
            status = "review_required"
            review_ids.append(component_id)
        else:
            review_status = "within_recorded_window"
            status = "current"
        if is_applicable:
            applicable_ids.append(component_id)
        row = {
            "id": component_id,
            "label": source["label"],
            "source_vintage": source["source_vintage"],
            "maintenance_due_on": source["maintenance_due_on"],
            "applicability": (
                "applicable" if is_applicable else "not_used_at_run"),
            "review_status": review_status,
            "status": status,
            "effective_source": effective_source,
            "mismatched_fields": mismatches,
        }
        if configured is not None:
            row["configured_values"] = configured
        rows.append(row)

    # A stale maintenance claim is more urgent than an input mismatch.  Keep
    # both id lists so the UI/report can still disclose both facts.
    if stale_ids:
        overall = "stale"
    elif review_ids:
        overall = "review_required"
    else:
        overall = "current"
    return {
        "schema_version": RULE_PACK_SCHEMA_VERSION,
        "pack_id": RULE_PACK_ID,
        "content_sha256": RULE_PACK_SHA256,
        "delivery": "offline_embedded",
        "runtime_network_refresh": False,
        "evaluated_on": evaluated.isoformat(),
        "evaluation_basis": "config_applicability_not_path_instrumentation",
        "status": overall,
        "conclusion_status": overall,
        "applicable_component_ids": applicable_ids,
        "stale_component_ids": stale_ids,
        "review_required_component_ids": review_ids,
        "components": rows,
    }


def rule_pack_reference_defaults() -> dict[str, Any]:
    """Small server-owned descriptor used by browser-only input helpers."""
    return {
        "pack_id": RULE_PACK_ID,
        "content_sha256": RULE_PACK_SHA256,
        "contribution_limits": copy.deepcopy(CONTRIBUTION_LIMIT_RULES),
    }


def rule_pack_for_ssa_import(
        *, as_of: str | _dt.date,
) -> dict[str, Any]:
    """Descriptor for the separate statement-import seam."""
    evaluated = _as_date(as_of)
    source = _component("ssa_statement_import")
    status = ("stale" if evaluated > _dt.date.fromisoformat(
        source["maintenance_due_on"]) else "current")
    return {
        "pack_id": RULE_PACK_ID,
        "content_sha256": RULE_PACK_SHA256,
        "evaluated_on": evaluated.isoformat(),
        "component": {
            "id": source["id"],
            "label": source["label"],
            "source_vintage": source["source_vintage"],
            "maintenance_due_on": source["maintenance_due_on"],
            "status": status,
        },
    }
