"""
FIRE Model v9.8-rc — Analyst-A · 2026-07-01
=========================================

Fork of v9.6.1. Implements the fixes adjudicated after the 2026-07-01 full
audit (`v96_full_audit_2026-07-01.md`). Semantics decided by user:

  F-1 = Option A ("GK governs throughout")
  F-3 = GROSS basis (GK target contains FULL-price premium)

FIX F-1A · Shanghai retirement is GK-governed (replaces the v6-legacy
  fixed-real CNY track that silently overrode GK output post-relocation):
  - At relocation, the current-year GK target (US basis) is translated to a
    China lifestyle level L: strip US healthcare components (routine +
    premium_full + oop), apply col_effective to the NON-health portion only,
    add China healthcare cost. GK rule_state is RE-SEEDED on (portfolio, L).
  - Post-relocation each year, GK runs on:
      inflation input      = CN inflation (2.5% deterministic)
      cpi index (freeze)   = cpi_track (US CPI to relocation, CN after)
      portfolio input      = accounts.total × (fx_t / fx_at_reloc)
        -> implied SWR seen by guardrails == actual USD need / portfolio,
           so guardrails respond to BOTH market and FX moves.
  - Actual USD withdrawal target = L_t × (fx_at_reloc / fx_t).
  - Health step-downs (US Medicare @65, CN senior @60) are absorbed into the
    GK-governed total budget (composition shifts, total governed).
  - guardrail_triggers counter is preserved across the re-seed.

FIX F-2 · SS offsets withdrawals in BOTH regions (unified line):
  portfolio_withdrawal_needed = max(0, adjusted_target − ss_income).
  Any SS surplus beyond the target is credited to accounts.taxable
  (ss_surplus_credited_nominal) — no cash inflow may vanish.

FIX F-3 · GROSS ACA basis: the retirement seed uses initial_FULL_premium
  (was: subsidized initial_aca_paid). The annual loop subtraction of
  premium_savings is now consistent with the seed. Robust to subsidy-policy
  changes: if subsidies shrink, savings shrink, withdrawals rise, lifestyle
  target unchanged.

FIX F-4 · GK inflation-freeze deflator basis: initialize() stores the true
  cpi_at_init; the freeze compares tentative/cpi_cum against
  initial_w_nominal/cpi_at_init (same-real-basis). Implemented in
  GuytonKlingerRuleV98 / GK_STANDARD_V98 (fire_v9_1_model untouched).

FIX F-13 · fund_shock_or_purchase_v98: taxable leg grossed up by
  1/(1−withdrawal_tax_taxable), consistent with the regular withdrawal path.

FIX F-14 · Medicare gate: the ACA subsidy machinery only runs for
  age < medical.medicare_age. At 65+ adjusted_target = target (no phantom
  "Medicare subsidy").

CASH-CONSERVATION EXPORTS (for test_v98_invariants.py):
  total_wd_received_nominal, total_ss_applied_nominal,
  ss_surplus_credited_nominal, total_income_applied_nominal. In years with an
  actual structured-income receipt, and in all material-shortfall exits:
  Σ nominal_consumption
    == Σ wd_received + Σ ss_applied + Σ structured_income_applied.
  Successful years with no structured-income receipt retain the historical
  withdrawal convention of recording the target when delivered cash is short
  by at most $1; aggregate diagnostics therefore allow at most $1 for each such
  year, plus floating-point epsilon.

Recording conventions (unchanged deflator: US CPI, headline-consistent with
cpi_stitch / extract_site_data):
  real_consumption_path    = recorded consumption / US-CPI, using delivered
                             cash except for the <=$1 compatibility convention
                             described above
  lifestyle_real_path      = consumption ÷ col_effective while in China
                             (US-lifestyle-equivalent, secondary/approx.)

Known approximations carried (documented, out of v9.8 scope):
  - Rental income (if property enabled) still indexed at US CPI.
  - Property purchase priced at US CPI, no FX channel (audit F-15, P2).
  - Withdrawal ordering unchanged (audit F-12, P2).

Usage:
    from fire_v9_8_model import (
        simulate_lifecycle_v98, run_lifecycle_mc_v98, GK_STANDARD_V98,
    )
    from fire_v95_actual_baseline import INITIAL_STACK_ACTUAL, match_excludes_bonus

    with match_excludes_bonus():
        res = run_lifecycle_mc_v98(n_paths=40_000, seed=96_000,
                                   rule=GK_STANDARD_V98,
                                   initial=INITIAL_STACK_ACTUAL)
"""

from __future__ import annotations
import copy
import numpy as np
from dataclasses import dataclass, replace
from typing import Optional, Sequence

from fire_rule_pack import SSA_TRUST_FUND
from fire_v9_3_model import (
    BondParams, DEFAULT_BOND_PARAMS,
    GlidePath, GLIDE_ALL_EQUITY,
    sample_bond_returns, blended_return,
    EldercareShockParams, sample_eldercare_events,
    InheritanceParams, sample_inheritance,
    OBBBAParams,
    ShanghaiPropertyParams,
    project_stratified_v93,
)
from fire_v6_model import (
    State, AccountStack, TaxParams, RelocationParams,
    STATE, TAX_US,
    find_fire_crossing, effective_ordinary_rate,
)
# 4.0 Phase 2 · the user's own long-term care. Distinct from the eldercare
# shock above, which is paying for a parent. Imported unconditionally because
# the module is pure Python with no cost to import; nothing is DRAWN unless a
# plan turns it on.
import ltc_model as LTC
# 4.0 Phase 2 · the parent lifecycle. Replaces the eldercare shock and the
# inheritance draw with one parent who has one death, when the plan opts in;
# both of those stay exactly as they were for a plan that does not.
import parents_model as PARENTS
from fire_v7_model import (
    TaxParamsChina, TAX_CN, V7Config, sample_lifetime_v7,
)
import fire_v8_model                       # read _HOUSEHOLD global at call time
from fire_v8_model import (
    PromotionParams, V8ContributionParams, sample_promotion_event,
)
from fire_v9_1_model import (
    MortalityParams, MORTALITY_MALE, MORTALITY_FEMALE,
    annual_mortality_rate,
    MedicalParams, DEFAULT_MEDICAL, compute_medical_components,
    MedicalHousehold,
    ACAParams,
    estimate_magi_proxy, compute_aca_premium_paid,
    WithdrawalRule, FixedRealRule, GuytonKlingerRule,
)
from fire_v9_2_model import (
    RothLadderParams,
    update_seasoning_queue, execute_roth_conversion,
    SocialSecurityParams, compute_ss_annual_income,
    FTCParams, apply_ftc_to_tax_cn,
)
from fire_v9_4_model import (
    EARLY_WD_PENALTY_AGE, EARLY_WD_PENALTY_RATE,
    withdraw_with_seasoning_v94,
)
from fire_tax_true import (TrueTaxParams, solve_retirement_year,
                           irmaa_annual_surcharge_real,
                           dividend_drag_rate_real)
from fire_v9_6_model import (
    ChinaHealthcareParams, china_healthcare_cost_nominal,
    SSNRAHaircutParams, compute_dynamic_col_reduction,
)


# ============================================================
# v9.8+ opt-in: LIFE EVENTS (children, education, sales, windfalls)
# ============================================================
# 4.0 Phase 2 added the two guaranteed-income instruments. They are separate
# kinds rather than a pension with different numbers because the decision the
# packet exists to inform is exactly "annuity or ladder", and a bucket that
# merges them cannot answer it.
INCOME_STREAM_KINDS = ("pension", "rental", "parttime", "equity",
                       "annuity", "tips_ladder")
INCOME_STREAM_OWNERS = ("unspecified", "household", "primary", "spouse")


@dataclass(frozen=True)
class IncomeStreamSpec:
    """Structured, after-tax annual cash flow on the primary-age timeline.

    ``unspecified`` is the compatibility value for old plans: it preserves the
    old last-survivor behavior without inventing shared ownership.  The adapter
    validates kind-specific combinations; this engine object stays deliberately
    small and picklable for chunked Monte Carlo workers.
    """
    kind: str
    annual_real: float
    owner: str = "unspecified"
    start_age: int = 0
    end_age: Optional[int] = None
    duration_years: Optional[int] = None
    cola: bool = True
    after_fire_only: bool = False
    nominal_anchor_cpi: Optional[float] = None

    def __post_init__(self):
        if self.kind not in INCOME_STREAM_KINDS:
            raise ValueError(f"unsupported income stream kind: {self.kind!r}")
        if self.owner not in INCOME_STREAM_OWNERS:
            raise ValueError(f"unsupported income stream owner: {self.owner!r}")
        annual_real = float(self.annual_real)
        if not np.isfinite(annual_real):
            raise ValueError("income stream annual_real must be finite")
        if annual_real <= 0:
            raise ValueError("income stream annual_real must be positive")
        if self.duration_years is not None and int(self.duration_years) <= 0:
            raise ValueError("income stream duration_years must be positive")
        if self.end_age is not None and int(self.end_age) < int(self.start_age):
            raise ValueError("income stream end_age precedes start_age")
        if self.kind == "pension":
            if (self.end_age is not None or self.duration_years is not None
                    or self.after_fire_only):
                raise ValueError("pension has an open-ended non-retirement-only schedule")
        elif self.kind == "rental":
            if self.end_age is None or self.duration_years is not None:
                raise ValueError("rental requires an inclusive end_age")
            if self.after_fire_only or not self.cola:
                raise ValueError("rental is a real, non-retirement-only stream")
        elif self.kind == "parttime":
            if (self.duration_years is None or self.end_age is not None
                    or not self.after_fire_only or not self.cola):
                raise ValueError("parttime requires a retirement-only duration")
        elif self.kind == "equity":
            if (self.duration_years is None or self.end_age is not None
                    or self.after_fire_only or not self.cola):
                raise ValueError("equity requires a working/retirement duration")
        elif self.kind == "annuity":
            # Open-ended on purpose: an annuity ends at a death, and an
            # `end_age` here would be a guess at the lifespan the contract
            # exists to stop the buyer having to guess.
            if (self.end_age is not None or self.duration_years is not None
                    or self.after_fire_only):
                raise ValueError("an annuity runs until a death, not to an age")
        elif self.kind == "tips_ladder":
            # The mirror image: a ladder ends when its rungs run out, whether
            # or not anyone is alive, so it MUST carry an end age.
            if self.end_age is None or self.duration_years is not None:
                raise ValueError("a TIPS ladder requires an inclusive end_age")
            if self.after_fire_only or not self.cola:
                raise ValueError("a TIPS ladder is inflation-linked by "
                                 "construction; that is what makes it a TIPS "
                                 "ladder rather than a bond ladder")
        if self.nominal_anchor_cpi is not None:
            nominal_anchor_cpi = float(self.nominal_anchor_cpi)
            if not np.isfinite(nominal_anchor_cpi):
                raise ValueError(
                    "income stream nominal_anchor_cpi must be finite")
            if nominal_anchor_cpi <= 0:
                raise ValueError(
                    "income stream nominal_anchor_cpi must be positive")


@dataclass(frozen=True)
class HousingMortgageSpec:
    """Internal fixed-rate mortgage schedule for realized-CPI resolution.

    ``payments`` are the schedule's annual nominal amounts in the purchase-CPI
    anchor units. ``carrying_by_age`` contains only the same housing plan's
    positive tax/maintenance/insurance rows. The resolver merges those rows
    with the mortgage payment before generic event funding, while refunds and
    unrelated user events remain separate. The user config and public JSON
    never carry this object; the server adapter creates it from housing inputs.
    """

    purchase_age: int
    payments: tuple[float, ...]
    carrying_by_age: tuple[tuple[int, float], ...] = ()

    def __post_init__(self):
        purchase_age = int(self.purchase_age)
        payments = tuple(float(payment) for payment in self.payments)
        carrying_by_age = tuple(
            (int(age), float(amount)) for age, amount in self.carrying_by_age)
        if purchase_age < 0:
            raise ValueError("housing mortgage purchase_age must be non-negative")
        if any(not np.isfinite(payment) or payment <= 0.0
               for payment in payments):
            raise ValueError("housing mortgage payments must be finite and positive")
        if any(age < 0 or not np.isfinite(amount) or amount < 0.0
               for age, amount in carrying_by_age):
            raise ValueError(
                "housing mortgage carrying costs must be finite and non-negative")
        object.__setattr__(self, "purchase_age", purchase_age)
        object.__setattr__(self, "payments", payments)
        object.__setattr__(self, "carrying_by_age", carrying_by_age)


def _income_owner_is_alive(owner: str, household_on: bool,
                           primary_alive: bool, spouse_alive: bool) -> bool:
    """Return whether an otherwise scheduled payment belongs to a survivor."""
    if not household_on:
        return bool(primary_alive)
    if owner == "primary":
        return bool(primary_alive)
    if owner == "spouse":
        return bool(spouse_alive)
    # Explicit household and legacy-unassigned streams share numeric survival
    # behavior; only the former claims confirmed joint ownership.
    return bool(primary_alive or spouse_alive)


def _income_schedule_active(stream: IncomeStreamSpec, age: int,
                            retirement_start_age: Optional[int] = None) -> bool:
    """Evaluate a stream's primary-timeline payment window."""
    start_age = int(stream.start_age)
    if stream.after_fire_only:
        if retirement_start_age is None:
            return False
        start_age = max(start_age, int(retirement_start_age) + 1)
    if age < start_age:
        return False
    if stream.end_age is not None and age > int(stream.end_age):
        return False
    if (stream.duration_years is not None
            and age >= start_age + max(0, int(stream.duration_years))):
        return False
    return True


def _income_nominal(stream: IncomeStreamSpec, cpi_cumulative: float,
                    nominal_anchor_cpi: Optional[float] = None) -> float:
    # Keyed on `cola`, not on the kind. It used to read
    # `kind == "pension" and not cola`, which was correct while the pension was
    # the only stream that could switch COLA off — and silently wrong the
    # moment a fixed annuity arrived, because that stream would have been paid
    # as though it were inflation-linked. Losing purchasing power is the whole
    # difference between two annuity quotes that look alike, and it would have
    # gone the flattering way. Behaviour-neutral for every existing config:
    # the pension is still the only OTHER stream whose adapter sets `cola`.
    if not stream.cola:
        anchor = (stream.nominal_anchor_cpi
                  if stream.nominal_anchor_cpi is not None
                  else nominal_anchor_cpi)
        if anchor is None:
            raise ValueError("non-COLA pension is missing its CPI anchor")
        return float(stream.annual_real) * float(anchor)
    return float(stream.annual_real) * float(cpi_cumulative)


def _allocate_income_by_kind(received_by_kind: dict,
                             applied_total: float) -> tuple[dict, dict]:
    """Allocate pooled applied/surplus cash proportionally across stream kinds."""
    received_total = float(sum(received_by_kind.values()))
    if received_total <= 0.0:
        return {}, {}
    applied_total = min(max(float(applied_total), 0.0), received_total)
    ratio = applied_total / received_total
    applied = {
        kind: amount * ratio for kind, amount in received_by_kind.items()
    }
    surplus = {
        kind: received_by_kind[kind] - applied[kind]
        for kind in received_by_kind
    }
    return applied, surplus


def _cpi_at_primary_age(start_age: int, age: int,
                        inflations: Sequence[float]) -> float:
    """Realized CPI from model start through the requested primary age."""
    years = max(0, min(int(age) - int(start_age), len(inflations)))
    cpi = 1.0
    for inf in inflations[:years]:
        cpi *= 1.0 + float(inf)
    return cpi


def resolve_housing_mortgage_events(
        mortgage: Optional[HousingMortgageSpec], start_age: int,
        inflations: Sequence[float]) -> list[tuple[int, float]]:
    """Resolve fixed nominal mortgage payments into path-real events.

    The schedule is frozen in purchase-year nominal units.  For a payment
    ``P_k`` due ``k`` years after purchase, the event is expressed in the
    model's today's-dollar event units as ``P_k * C(purchase) / C(age)``.
    The existing event machinery then multiplies by that same path's CPI to
    recover the nominal cash amount.  No RNG is consumed here; callers pass the
    already sampled inflation vector.
    """
    if mortgage is None or (not mortgage.payments and not mortgage.carrying_by_age):
        return []
    start_age = int(start_age)
    inflations = tuple(float(inf) for inf in inflations)
    if any(not np.isfinite(inf) or 1.0 + inf <= 0.0 for inf in inflations):
        raise ValueError("housing mortgage inflation path must be finite and > -100%")
    purchase_cpi = _cpi_at_primary_age(
        start_age, mortgage.purchase_age, inflations)
    if not np.isfinite(purchase_cpi) or purchase_cpi <= 0.0:
        raise ValueError("housing mortgage purchase CPI must be finite and positive")

    carrying_by_age = dict(mortgage.carrying_by_age)
    events = []
    cpi = 1.0
    for years, inf in enumerate(inflations, start=1):
        age = start_age + years
        cpi *= 1.0 + inf
        schedule_year = age - mortgage.purchase_age
        amount_nominal_anchor = carrying_by_age.get(age, 0.0) * cpi
        if 1 <= schedule_year <= len(mortgage.payments):
            amount_nominal_anchor += mortgage.payments[schedule_year - 1] \
                * purchase_cpi
        if amount_nominal_anchor <= 0.0:
            continue
        amount_real = amount_nominal_anchor / cpi
        if not np.isfinite(amount_real) or amount_real <= 0.0:
            raise ValueError("resolved housing mortgage event must be finite and positive")
        events.append((age, float(amount_real)))
    return events


def _anchor_non_cola_pensions(income_streams, state, inflations):
    """Freeze nominal pension starts before FIRE/death can suppress payment."""
    if not income_streams:
        return income_streams
    anchored = []
    first_modeled_age = int(state.start_age) + 1
    for stream in income_streams:
        if (stream.kind == "pension" and not stream.cola
                and stream.nominal_anchor_cpi is None):
            first_payment_age = max(first_modeled_age, int(stream.start_age))
            anchored.append(replace(
                stream,
                nominal_anchor_cpi=_cpi_at_primary_age(
                    state.start_age, first_payment_age, inflations),
            ))
        else:
            anchored.append(stream)
    return tuple(anchored)


def _apply_life_events_accum_v98(accum_path, events_by_age, returns, state,
                                 friction, drag_taxable, wd_taxable):
    """Apply life-event cash flows to the ACCUMULATION path (guarded; empty =>
    the path is returned untouched, bit-identical). Mirrors the v9.3 OBBBA
    wrapper pattern: the certified accumulation core is untouched; a taxable-
    side delta is compounded forward at the same effective taxable return.
    Outflows are funded from TAXABLE only (working-age spending does not raid
    retirement accounts), grossed up by the taxable withdrawal tax; inflows
    land in taxable. If taxable cannot cover an outflow, taxable floors at 0
    and the shortfall is recorded.  The adapter treats any unpaid mandatory
    outflow as a financial failure; keeping the path here lets reporting show
    the age and amount of the shortfall instead of silently truncating it.
    Returns (new_path, meta)."""
    meta = {
        "underfunded_years": 0,
        "underfunded_ages": [],
        "funding_shortfall_nominal_by_age": {},
        "shortfall_nominal_by_age": {},
        "shortfall_real_by_age": {},
        "out_real_by_age": {},
        "in_real_by_age": {},
        "out_real": 0.0,
        "in_real": 0.0,
    }
    if not events_by_age:
        return accum_path, meta
    exp0 = max(float(state.expenses_y0), 1e-9)
    delta = 0.0
    new_path = [accum_path[0]]
    for i in range(1, len(accum_path)):
        step = accum_path[i]
        r_tax = 1.0 + returns[i - 1] - friction - drag_taxable
        delta *= r_tax
        cpi_i = float(step["expenses"]) / exp0
        for amt_real in events_by_age.get(int(step["age"]), ()):  
            if amt_real > 0:
                delta -= (amt_real * cpi_i) / max(1.0 - wd_taxable, 1e-3)
                meta["out_real"] += amt_real
                meta["out_real_by_age"][int(step["age"])] = (
                    meta["out_real_by_age"].get(int(step["age"]), 0.0) + amt_real)
            else:
                delta += -amt_real * cpi_i
                meta["in_real"] += -amt_real
                meta["in_real_by_age"][int(step["age"])] = (
                    meta["in_real_by_age"].get(int(step["age"]), 0.0) - amt_real)
        base = step["accounts"]
        new_taxable = base.taxable + delta
        shortfall_nominal = 0.0
        if new_taxable < 0.0:
            funding_shortfall_nominal = -new_taxable
            shortfall_nominal = funding_shortfall_nominal * max(0.0, 1.0 - wd_taxable)
            age = int(step["age"])
            meta["underfunded_years"] += 1
            meta["underfunded_ages"].append(age)
            meta["funding_shortfall_nominal_by_age"][age] = funding_shortfall_nominal
            meta["shortfall_nominal_by_age"][age] = shortfall_nominal
            meta["shortfall_real_by_age"][age] = shortfall_nominal / cpi_i
            delta = -base.taxable          # floor: no phantom debt carries on
            new_taxable = 0.0
        new_accounts = base.copy()
        new_accounts.taxable = new_taxable
        new_step = dict(step)
        new_step["accounts"] = new_accounts
        new_step["total"] = new_accounts.total
        new_step["life_event_shortfall_nominal"] = shortfall_nominal
        new_step["life_event_shortfall_real"] = shortfall_nominal / cpi_i
        new_path.append(new_step)
    return new_path, meta


def _apply_income_streams_accum_v98(
        accum_path, income_streams, returns, state, friction, drag_taxable,
        household_on=False, alive_by_year=None):
    """Credit scheduled working-year income to taxable, preserving ownership.

    This runs after mandatory life events so a same-year income receipt cannot
    retroactively erase an event shortfall. Part-time is retirement-only.
    """
    if not income_streams:
        return accum_path, None
    delta = 0.0
    received_nominal_by_age = {}
    received_real_by_age = {}
    received_nominal_by_kind_age = {}
    received_real_by_kind_age = {}
    exp0 = max(float(state.expenses_y0), 1e-9)
    # A non-COLA stream is paid a fixed NOMINAL amount, so it needs the price
    # level of its first payment. The retirement loop records that; these
    # accumulation loops never did, and until `_income_nominal` was keyed on
    # `cola` instead of `kind == "pension"` it did not show, because no working
    # config paid a non-COLA stream before FIRE without also setting the anchor
    # on the spec. A fixed annuity bought at 65 by a 65-year-old does exactly
    # that, and the run died with "missing its CPI anchor". Recorded here and
    # handed to the retirement phase so ONE anchor covers the whole life of the
    # stream: anchoring again at the first retirement payment would quietly
    # re-base the annuity upward at FIRE.
    nominal_anchors = {
        idx: float(stream.nominal_anchor_cpi)
        for idx, stream in enumerate(income_streams)
        if stream.nominal_anchor_cpi is not None
    }

    new_path = [accum_path[0]]
    for i in range(1, len(accum_path)):
        step = accum_path[i]
        age = int(step["age"])
        r_tax = 1.0 + returns[i - 1] - friction - drag_taxable
        delta *= r_tax
        cpi_i = float(step["expenses"]) / exp0
        if alive_by_year is not None:
            primary_alive, spouse_alive = alive_by_year[i - 1]
        else:
            primary_alive, spouse_alive = True, True
        for stream_idx, stream in enumerate(income_streams):
            if not _income_schedule_active(stream, age):
                continue
            if not _income_owner_is_alive(
                    stream.owner, household_on,
                    primary_alive, spouse_alive):
                continue
            if not stream.cola and stream_idx not in nominal_anchors:
                nominal_anchors[stream_idx] = cpi_i
            amount_nominal = _income_nominal(stream, cpi_i,
                                             nominal_anchors.get(stream_idx))
            amount_real = amount_nominal / max(cpi_i, 1e-9)
            delta += amount_nominal
            received_nominal_by_age[age] = (
                received_nominal_by_age.get(age, 0.0) + amount_nominal)
            received_real_by_age[age] = (
                received_real_by_age.get(age, 0.0) + amount_real)
            received_nominal_by_kind_age.setdefault(stream.kind, {})[age] = (
                received_nominal_by_kind_age.setdefault(
                    stream.kind, {}).get(age, 0.0) + amount_nominal)
            received_real_by_kind_age.setdefault(stream.kind, {})[age] = (
                received_real_by_kind_age.setdefault(
                    stream.kind, {}).get(age, 0.0) + amount_real)
        base = step["accounts"]
        new_accounts = base.copy()
        new_accounts.taxable = base.taxable + delta
        new_step = dict(step)
        new_step["accounts"] = new_accounts
        new_step["total"] = new_accounts.total
        new_path.append(new_step)
    return new_path, {
        "received_nominal_by_age": received_nominal_by_age,
        "received_real_by_age": received_real_by_age,
        "received_nominal_by_kind_age": received_nominal_by_kind_age,
        "received_real_by_kind_age": received_real_by_kind_age,
        "nominal_anchor_by_index": dict(nominal_anchors),
    }


def _apply_events_and_income_accum_v98(
        accum_path, events_by_age, income_streams, returns, state, friction,
        drag_taxable, wd_taxable, household_on=False, alive_by_year=None):
    """Interleave event spending and structured income on each modeled year.

    Prior-year income is ordinary taxable cash and can fund a later mandatory
    event. Current-year income arrives only after that year's events, so it
    cannot retroactively erase a shortfall already incurred. The single delta
    also receives the same taxable return/drag as either standalone overlay.
    """
    event_meta = {
        "underfunded_years": 0,
        "underfunded_ages": [],
        "funding_shortfall_nominal_by_age": {},
        "shortfall_nominal_by_age": {},
        "shortfall_real_by_age": {},
        "out_real_by_age": {},
        "in_real_by_age": {},
        "out_real": 0.0,
        "in_real": 0.0,
    }
    received_nominal_by_age = {}
    received_real_by_age = {}
    received_nominal_by_kind_age = {}
    received_real_by_kind_age = {}
    exp0 = max(float(state.expenses_y0), 1e-9)
    # Same anchor bookkeeping as the income-only overlay above, and for the
    # same reason: one anchor per stream for the whole life of the stream.
    nominal_anchors = {
        idx: float(stream.nominal_anchor_cpi)
        for idx, stream in enumerate(income_streams)
        if stream.nominal_anchor_cpi is not None
    }
    delta = 0.0
    new_path = [accum_path[0]]
    for i in range(1, len(accum_path)):
        step = accum_path[i]
        age = int(step["age"])
        r_tax = 1.0 + returns[i - 1] - friction - drag_taxable
        delta *= r_tax
        cpi_i = float(step["expenses"]) / exp0

        # Preserve the generic event channel's existing within-year behavior.
        for amount_real in events_by_age.get(age, ()):
            if amount_real > 0:
                delta -= (
                    amount_real * cpi_i
                    / max(1.0 - wd_taxable, 1e-3)
                )
                event_meta["out_real"] += amount_real
                event_meta["out_real_by_age"][age] = (
                    event_meta["out_real_by_age"].get(age, 0.0)
                    + amount_real
                )
            else:
                delta += -amount_real * cpi_i
                event_meta["in_real"] += -amount_real
                event_meta["in_real_by_age"][age] = (
                    event_meta["in_real_by_age"].get(age, 0.0)
                    - amount_real
                )

        base = step["accounts"]
        before_income_taxable = base.taxable + delta
        shortfall_nominal = 0.0
        if before_income_taxable < 0.0:
            funding_shortfall_nominal = -before_income_taxable
            shortfall_nominal = (
                funding_shortfall_nominal * max(0.0, 1.0 - wd_taxable)
            )
            event_meta["underfunded_years"] += 1
            event_meta["underfunded_ages"].append(age)
            event_meta["funding_shortfall_nominal_by_age"][age] = (
                funding_shortfall_nominal
            )
            event_meta["shortfall_nominal_by_age"][age] = shortfall_nominal
            event_meta["shortfall_real_by_age"][age] = (
                shortfall_nominal / cpi_i
            )
            delta = -base.taxable

        if alive_by_year is not None:
            primary_alive, spouse_alive = alive_by_year[i - 1]
        else:
            primary_alive, spouse_alive = True, True
        for stream_idx, stream in enumerate(income_streams):
            if not _income_schedule_active(stream, age):
                continue
            if not _income_owner_is_alive(
                    stream.owner, household_on,
                    primary_alive, spouse_alive):
                continue
            if not stream.cola and stream_idx not in nominal_anchors:
                nominal_anchors[stream_idx] = cpi_i
            amount_nominal = _income_nominal(stream, cpi_i,
                                             nominal_anchors.get(stream_idx))
            amount_real = amount_nominal / max(cpi_i, 1e-9)
            delta += amount_nominal
            received_nominal_by_age[age] = (
                received_nominal_by_age.get(age, 0.0) + amount_nominal
            )
            received_real_by_age[age] = (
                received_real_by_age.get(age, 0.0) + amount_real
            )
            nominal_by_age = received_nominal_by_kind_age.setdefault(
                stream.kind, {})
            nominal_by_age[age] = (
                nominal_by_age.get(age, 0.0) + amount_nominal
            )
            real_by_age = received_real_by_kind_age.setdefault(
                stream.kind, {})
            real_by_age[age] = real_by_age.get(age, 0.0) + amount_real

        new_accounts = base.copy()
        new_accounts.taxable = base.taxable + delta
        new_step = dict(step)
        new_step["accounts"] = new_accounts
        new_step["total"] = new_accounts.total
        new_step["life_event_shortfall_nominal"] = shortfall_nominal
        new_step["life_event_shortfall_real"] = shortfall_nominal / cpi_i
        new_path.append(new_step)

    income_meta = {
        "received_nominal_by_age": received_nominal_by_age,
        "received_real_by_age": received_real_by_age,
        "received_nominal_by_kind_age": received_nominal_by_kind_age,
        "received_real_by_kind_age": received_real_by_kind_age,
        "nominal_anchor_by_index": dict(nominal_anchors),
    }
    return new_path, event_meta, income_meta


def _income_accum_meta_through_age(meta: Optional[dict], max_age: int):
    if meta is None:
        return None
    nominal_by_age = {
        age: amount
        for age, amount in meta["received_nominal_by_age"].items()
        if age <= max_age
    }
    real_by_age = {
        age: amount
        for age, amount in meta["received_real_by_age"].items()
        if age <= max_age
    }
    nominal_by_kind = {
        kind: sum(amount for age, amount in by_age.items() if age <= max_age)
        for kind, by_age in meta["received_nominal_by_kind_age"].items()
    }
    real_by_kind = {
        kind: sum(amount for age, amount in by_age.items() if age <= max_age)
        for kind, by_age in meta["received_real_by_kind_age"].items()
    }
    return {
        "received_nominal": sum(nominal_by_age.values()),
        "received_real": sum(real_by_age.values()),
        "received_nominal_by_kind": nominal_by_kind,
        "received_real_by_kind": real_by_kind,
        "received_nominal_by_age": nominal_by_age,
        "received_real_by_age": real_by_age,
    }


def _life_event_meta_through_age(meta: dict, max_age: int) -> dict:
    """Return accumulation-event diagnostics only through the FIRE/censor age."""
    out = dict(meta)
    for key in ("funding_shortfall_nominal_by_age", "shortfall_nominal_by_age",
                "shortfall_real_by_age", "out_real_by_age", "in_real_by_age"):
        out[key] = {age: amount for age, amount in meta.get(key, {}).items()
                    if age <= max_age}
    out["underfunded_ages"] = [age for age in meta.get("underfunded_ages", [])
                                if age <= max_age]
    out["underfunded_years"] = len(out["underfunded_ages"])
    out["out_real"] = sum(out["out_real_by_age"].values())
    out["in_real"] = sum(out["in_real_by_age"].values())
    return out


# ============================================================
# FIX F-4 · GK rule with corrected inflation-freeze basis
# ============================================================
def ss_payable_share(calendar_year: int, depletion_year: int,
                    payable_at_depletion: float, payable_2100: float) -> float:
    """The share of a SCHEDULED benefit actually payable in a given year.

    1.0 until reserves run out, then the report's declining path. The Trustees
    publish two endpoints -- the share at depletion and the share in 2100 --
    and the straight line between them is THIS APP'S, not theirs. It is
    disclosed as an interpolation in the rule pack's provenance and in the
    limitations panel, because a reader looking at year 2060 is looking at a
    number nobody published.

    Deliberately NOT a flat step. A constant 78% would say the shortfall stops
    growing, and the report's own endpoints say it does not.
    """
    if calendar_year < depletion_year:
        return 1.0
    if depletion_year >= 2100 or calendar_year >= 2100:
        return payable_2100
    fraction = (calendar_year - depletion_year) / float(2100 - depletion_year)
    return payable_at_depletion + fraction * (payable_2100 - payable_at_depletion)


@dataclass(frozen=True)
class SSTrustFundParams:
    """What happens to a benefit if Congress does nothing.

    Roadmap 5.0 Phase 5 (idea-bank A11). The OASI trust fund's reserves are
    projected to run out, and current law has no mechanism to borrow: at that
    point benefits are paid from incoming payroll tax alone, which is less
    than what is scheduled. Every plan in this app has so far paid Social
    Security in full for fifty years, which is a legislative prediction stated
    as an arithmetic default.

    **This does not predict legislation.** It models the mechanical
    consequence of no action, which is what the Trustees publish and the only
    thing anyone can source. Congress acting -- and it has, every prior time a
    fund neared depletion -- is exactly what this cannot forecast, and the
    disclosure says so.

    **The numbers are the report's, not this dataclass's.** Depletion years and
    payable shares live in the rule pack (`SSA_TRUST_FUND`) so they carry a
    vintage and go stale on a schedule. Putting them here as defaults would
    make an actuarial projection look like a constant.

    **A calendar year is REQUIRED and never guessed.** This engine runs in
    ages and years-from-start; it has no idea what year it is. Depletion is a
    calendar event, so `plan_start_year` says which calendar year the plan's
    year zero is. Left as `None` the module refuses to run and says why --
    defaulting it to a hardcoded year would mis-time a federal event by
    however many years the user's plan is offset, silently, and defaulting it
    to the clock would make the same plan produce different answers in
    different years and break every archived replay.
    """
    enabled: bool = False
    #: Calendar year of the plan's year zero. REQUIRED when enabled.
    plan_start_year: Optional[int] = None
    #: "intermediate" uses the Trustees' best estimate. "range" samples across
    #: the three published alternatives -- which is the report's OWN range,
    #: not a spread invented here.
    scenario: str = "intermediate"
    #: Annual divergence between the plan's CPI and the COLA the benefit
    #: actually receives. CPI-E, the experimental elderly index, has generally
    #: run ABOVE CPI-W, so a positive value here is the case where benefits
    #: keep up better than the plan's inflation assumes. 0.0 is "no
    #: divergence", which is what the engine has always assumed.
    cola_delta_annual: float = 0.0
    #: Offset for this module's own generator; see BlockySpendingParams.
    seed_offset: int = 90_002


@dataclass(frozen=True)
class HumanCapitalParams:
    """Wages that do not grow along a line, and a door back in that narrows.

    Roadmap 6.0 (idea-bank A13). "Can I afford to quit" is half a question
    about markets and half a question about whether you could get back in.
    This engine answered neither: wages compounded at a fixed rate on every
    path, and a layoff cost a flat four months at any age.

    Both gaps were measured before this was written. Changing
    `contributions.salary_growth_pre` shifts the whole curve on every path,
    and `LayoffParams.gap_months` is a constant with no age term.

    **The shock decomposition is the point, not the volatility.** A permanent
    shock is a level change you carry for the rest of your career; a transitory
    one is a bad year you recover from. A single "wage volatility" number
    treats a lost promotion and a one-off missed bonus as the same event, and
    they are not remotely the same for a plan.

    Off by default and bit-identical when off: no draw is taken, so the shared
    stream is untouched. Drawn from a separate child generator for the same
    reason as the other 6.0 modules -- and note the registry entry, which says
    plainly that treating a career independently of markets is almost
    certainly untrue and will not be fixed by inventing a coefficient.
    """
    enabled: bool = False
    #: Standard deviation of the PERMANENT annual shock to log wages: the part
    #: you do not recover from. Zero by default so switching this on is opt-in
    #: per component.
    permanent_sigma: float = 0.08
    #: Standard deviation of the TRANSITORY shock: a bad year that reverses.
    transitory_sigma: float = 0.05
    #: The return-to-work half of A13 lives on `LayoffParams`, NOT here.
    #:
    #: It was briefly on both. The mechanism reads the layoff copy, so the
    #: three fields here were a second set of boxes for one fact -- the fourth
    #: time in a day, and caught by the attribution pin like the previous
    #: three. The search-decay dials are `layoff.gap_months_per_year_of_age`,
    #: `layoff.decay_from_age` and `layoff.max_gap_months`, beside the layoff
    #: probability they modify.
    seed_offset: int = 90_017


@dataclass(frozen=True)
class HousePriceProcess:
    """A house whose value is uncertain, for the two places that can use it.

    Roadmap 6.0 Phase 3 (idea-bank A12). Measuring before building changed
    what this is. The idea bank said the engine had Monte Carlo switched off
    for half the balance sheet; it turned out the house's VALUE is not on the
    balance sheet at all, and that is deliberate and disclosed -- the control
    is literally named "home equity (excluded from sim)" because it is
    illiquid and you live in it.

    So this does not quietly put the house on the balance sheet. User ruling
    2026-08-17: do both, and default to today's behaviour.

    ``enabled`` draws a real price path. It reaches results through two doors,
    both of which stay shut unless asked:

    * ``sale_age``/``sale_base_real`` -- proceeds from a planned sale scale
      with the drawn path instead of being a point estimate. This is the
      decision-relevant uncertainty for most plans: not what the house is
      worth on paper, but what you actually get when you downsize.
    * ``include_in_net_worth`` -- OFF by default. When on, the house's drawn
      value is reported, and reported SEPARATELY from spendable wealth,
      because the reason it was excluded has not stopped being true.

    Off by default and bit-identical when off: no draw is taken, so the shared
    stream is untouched and every existing plan reproduces.

    Drawn from a separate child generator, for the same two reasons as blocky
    spending: house prices are not the market's draw sequence, and taking them
    from the shared stream would mean switching this on reshuffled mortality
    and returns too.
    """
    enabled: bool = False
    #: Annual real volatility of home prices. Deliberately not defaulted from
    #: any index -- see the limitations text. A user with a view sets it.
    sigma_real: float = 0.10
    #: Real drift, and it defaults to ZERO on purpose.
    #:
    #: The first version defaulted to the deterministic module's 1% and moved
    #: the median terminal wealth 29% on a plan that only asked for
    #: uncertainty -- thirty-five years of compounding that the flat
    #: today's-dollar figure the user typed never had. Switching a module on
    #: must make the spread appear, not walk the central case somewhere else,
    #: or its measured effect is a mixture of this module and a silent raise.
    #: A user who believes in real appreciation sets it deliberately, and the
    #: limitations text says what that does.
    drift_real: float = 0.0
    #: Off: the existing "excluded from sim" promise is unchanged.
    include_in_net_worth: bool = False
    #: Share of the sale price lost to commission, repairs and timing.
    #: Zero by default for the same reason drift is: a module switched on must
    #: add uncertainty, not quietly make the plan poorer.
    liquidity_discount: float = 0.0
    sale_age: Optional[int] = None
    sale_base_real: float = 0.0
    equity_base_real: float = 0.0
    seed_offset: int = 90_011


@dataclass(frozen=True)
class BlockySpendingParams:
    """Spending that arrives in lumps rather than smoothly.

    Roadmap 5.0 Phase 4 (idea-bank A14), the half that is not the sticky cut.
    A roof, a car, a wedding, a medical excess: real spending is not the flat
    annual line every path here draws. Smooth spending understates sequence
    risk, because a lump landing in a bad decade is exactly the event a plan
    survives or does not.

    OFF by default and bit-identical when off -- no draw is taken at all,
    so the shared generator's sequence is untouched and every existing plan
    reproduces.

    When ON, the draws come from a SEPARATE generator seeded from the run's
    own seed. Two reasons, and the second is the load-bearing one: a lump
    arriving is independent of what the market did, and drawing from the
    shared stream would shift every later draw so that switching this on
    would change mortality and returns too -- making its measured effect a
    mixture of this module and a reshuffle.
    """
    enabled: bool = False
    #: Chance per retirement year that a lump lands. 0.15 is roughly "once
    #: every seven years", which is a placeholder and says so in the panel:
    #: this is not calibrated against anybody's spending history.
    annual_probability: float = 0.15
    #: Size as a fraction of that year's planned spending.
    size_fraction: float = 0.35
    #: Draws are offset from the run seed so this module cannot consume from
    #: the shared stream.
    seed_offset: int = 90_001


@dataclass
class GuytonKlingerRuleV98(GuytonKlingerRule):
    """GK with the freeze deflator anchored to cpi_at_init (F-4 fix).

    v9.1 compared tentative/cpi_cumulative (y0-real) against
    initial_w_nominal (init-year NOMINAL), suppressing ~1/3 of freezes in
    the first retirement decade. Here: same-real-basis comparison.
    initialize() accepts cpi_at_init and stores it in state.
    """
    name: str = "GK v9.8"

    def initialize(self, fire_portfolio_nominal: float,
                   fire_expenses_nominal: float,
                   initial_swr: float,
                   cpi_at_init: float = 1.0) -> dict:
        st = super().initialize(fire_portfolio_nominal,
                                fire_expenses_nominal, initial_swr)
        st['cpi_at_init'] = float(cpi_at_init)
        return st

    def compute_target_withdrawal(self, year_in_retirement, age,
                                   portfolio_nominal, inflation_this_year,
                                   cpi_cumulative, state):
        if year_in_retirement == 0:
            target = state['initial_w_nominal']
            new_state = dict(state)
            new_state['prev_w_nominal'] = target
            new_state['prev_portfolio_nominal'] = portfolio_nominal
            return target, new_state

        prev_w = state['prev_w_nominal']
        prev_portfolio = state['prev_portfolio_nominal']
        initial_w_nominal = state['initial_w_nominal']
        initial_swr = state['initial_swr']
        triggers = state.get('guardrail_triggers', 0)

        tentative = prev_w * (1 + inflation_this_year)

        # Inflation Freeze Rule — F-4: same-real-basis comparison
        if self.inflation_freeze_enabled:
            cpi_at_init = max(state.get('cpi_at_init', 1.0), 1e-12)
            tentative_real = tentative / cpi_cumulative
            initial_w_real = initial_w_nominal / cpi_at_init
            if portfolio_nominal < prev_portfolio and tentative_real > initial_w_real:
                tentative = prev_w

        current_implied_swr = tentative / max(portfolio_nominal, 1.0)
        if current_implied_swr > initial_swr * (1 + self.upper_guardrail):
            # The cut, times how much of it actually happens.
            #
            # `cut_realisation` is inherited from the v9.1 rule; this
            # generation OVERRIDES the method, so changing it there alone had
            # no effect on any live run. The chain's layering is documented in
            # AGENTS.md and this is what it costs: the same arithmetic exists
            # in two generations and only the newest one runs.
            #
            # At the default of 1.0 the expression is exactly
            # `tentative * (1 - adjustment_pct)`, bit for bit.
            tentative *= (1 - self.adjustment_pct * self.cut_realisation)
            triggers += 1
        elif current_implied_swr < initial_swr * (1 - self.lower_guardrail):
            tentative *= (1 + self.adjustment_pct)
            triggers += 1

        new_state = dict(state)
        new_state['prev_w_nominal'] = tentative
        new_state['prev_portfolio_nominal'] = portfolio_nominal
        new_state['guardrail_triggers'] = triggers
        return tentative, new_state


GK_STANDARD_V98 = GuytonKlingerRuleV98(
    name="GK Standard v9.8 (±20%, freeze-basis fixed)",
    upper_guardrail=0.20, lower_guardrail=0.20, adjustment_pct=0.10,
    inflation_freeze_enabled=True,
)


def _init_rule(rule: WithdrawalRule, portfolio: float, w: float,
               swr: float, cpi_at_init: float) -> dict:
    """Initialize any rule; guarantee cpi_at_init lands in state."""
    try:
        st = rule.initialize(portfolio, w, swr, cpi_at_init=cpi_at_init)
    except TypeError:
        st = rule.initialize(portfolio, w, swr)
        st['cpi_at_init'] = float(cpi_at_init)
    return st


# ============================================================
# FIX F-13 · shock/purchase funding with taxable gross-up
# ============================================================
def fund_shock_or_purchase_v98(
    accounts: AccountStack,
    cost_nominal: float,
    tax_us: TaxParams,
    current_age: float,
    roth_locked: float,
) -> tuple[AccountStack, float, float]:
    """v9.4 fund_shock_or_purchase + taxable-leg tax gross-up (F-13)."""
    accounts = accounts.copy()
    remaining = cost_nominal
    penalty = 0.0

    # Taxable — grossed up for cap-gains withdrawal tax (F-13)
    if remaining > 0 and accounts.taxable > 0:
        rate = tax_us.withdrawal_tax_taxable
        gross_needed = remaining / max(1 - rate, 0.001)
        gross_take = min(gross_needed, accounts.taxable)
        accounts.taxable -= gross_take
        remaining -= gross_take * (1 - rate)

    # 401k with potential early-withdrawal penalty
    if remaining > 0 and accounts.pretax_401k > 0:
        base_rate = tax_us.withdrawal_tax_traditional
        effective_rate = (base_rate + EARLY_WD_PENALTY_RATE
                          if current_age < EARLY_WD_PENALTY_AGE else base_rate)
        gross_needed = remaining / max(1 - effective_rate, 0.001)
        gross_take = min(gross_needed, accounts.pretax_401k)
        accounts.pretax_401k -= gross_take
        remaining -= gross_take * (1 - effective_rate)
        if current_age < EARLY_WD_PENALTY_AGE:
            penalty += gross_take * EARLY_WD_PENALTY_RATE

    # Roth (last resort, unlocked portion)
    if remaining > 0:
        accessible_roth = max(0.0, accounts.roth_ira - roth_locked)
        if accessible_roth > 0:
            take = min(remaining, accessible_roth)
            accounts.roth_ira -= take
            remaining -= take

    paid = cost_nominal - max(remaining, 0)
    return accounts, paid, penalty


# ============================================================
# v9.8 RETIREMENT SIMULATOR (F-1A + F-2 + F-3 + F-14)
# ============================================================
def compose_annual_medical_target(target_nominal: float,
                                  initial_nonmedical_share: float,
                                  components: dict,
                                  enabled: bool) -> float:
    """Replace the legacy medical share with this year's complete basket.

    ``target_nominal`` has already passed through the rule and any lifestyle
    scaling.  Therefore only its non-medical share remains discretionary; the
    current medical basket is added once and is never scaled with it.
    """
    if not enabled:
        return target_nominal
    return (target_nominal * initial_nonmedical_share
            + components['routine']
            + components['premium_full']
            + components['oop'])


def simulate_retirement_v98(
    starting_accounts: AccountStack,
    starting_age: int,
    fire_year_cpi_cumulative: float,
    equity_returns: Sequence[float],
    bond_returns: Sequence[float],
    inflations: Sequence[float],
    rule: WithdrawalRule,
    glide_path: GlidePath,
    relocation: RelocationParams,
    sh_property: ShanghaiPropertyParams,
    medical: MedicalParams,
    aca: ACAParams,
    mortality: MortalityParams,
    roth_ladder: RothLadderParams,
    ss: SocialSecurityParams,
    ftc: FTCParams,
    eldercare_events: list,
    inheritance_event: Optional[tuple],
    ltc_events: Optional[list] = None,
    # 4.0 Phase 2 · the parent lifecycle. Two lists rather than one because the
    # two sides land in different places: the plan's share of a parent's care
    # is an outflow funded exactly as the eldercare shock is, and a bequest is
    # an inflow credited exactly as the legacy inheritance is. They are named
    # apart from those two so a caller can tell which module produced a number
    # -- and so the zero-bequest honesty check has something to zero.
    # `None` (not `[]`) means the module never ran, and draws nothing.
    parent_care_events: Optional[list] = None,
    parent_bequests: Optional[list] = None,
    #: What the accumulation phase already anchored, `{stream index: CPI}`.
    #: `None` means there was no accumulation phase (direct retirement or
    #: backtest callers), not that nothing was anchored.
    income_nominal_anchors: Optional[dict] = None,
    state: State = None,
    tax_us: TaxParams = None,
    tax_cn: TaxParamsChina = None,
    friction: float = 0.005,
    rng: np.random.Generator = None,
    china_healthcare: ChinaHealthcareParams = None,
    ss_nra: SSNRAHaircutParams = None,
    life_events: Optional[list] = None,
    income_streams: Optional[Sequence[IncomeStreamSpec]] = None,
    tax_true: Optional[TrueTaxParams] = None,
    primary_alive_at_start: bool = True,
    spouse_alive_at_start: bool = True,
    blocky_rng: Optional[np.random.Generator] = None,
    blocky_spending: Optional["BlockySpendingParams"] = None,
    ss_trust_fund: Optional["SSTrustFundParams"] = None,
    ss_trust_fund_depletion_year: Optional[int] = None,
) -> dict:
    state = state or STATE
    tax_us = tax_us or TAX_US
    tax_cn = tax_cn or TAX_CN
    china_healthcare = china_healthcare or ChinaHealthcareParams()
    ss_nra = ss_nra or SSNRAHaircutParams()
    rng = rng or np.random.default_rng()
    income_streams = tuple(income_streams or ())
    _income_on = bool(income_streams)

    tax_cn_effective = apply_ftc_to_tax_cn(tax_cn, ftc)
    accounts = starting_accounts.copy()

    # ---- household (couple): joint mortality / survivor spend+SS / MFJ tax ----
    _hh = fire_v8_model._HOUSEHOLD
    household_on = _hh is not None and getattr(_hh, "enabled", False)
    primary_alive = bool(primary_alive_at_start)
    spouse_alive = bool(spouse_alive_at_start)
    if household_on:
        spouse_base_mort = (MORTALITY_FEMALE if _hh.spouse_sex == "female"
                            else MORTALITY_MALE)
        spouse_mort = replace(
            spouse_base_mort, enabled=mortality.enabled,
            cap_age=mortality.cap_age)
        spouse_ss = replace(ss, pia_monthly_y0=_hh.spouse_pia_monthly_y0,
                            claim_age=_hh.spouse_claim_age)

    def _medical_household(p_alive, s_alive):
        """The per-person medical split for this year, or `None`.

        `None` covers both ways a plan can decline to split: no household at
        all, and a household whose share was never measured. Neither is a
        50/50 assumption, and neither reaches the per-person code path.
        """
        share = getattr(medical, "household_share_primary", None)
        if share is None or not household_on:
            return None
        return MedicalHousehold(
            share_primary=float(share),
            spouse_age_offset=_hh.spouse_age_offset,
            primary_alive=bool(p_alive),
            spouse_alive=bool(s_alive),
        )

    initial_components = compute_medical_components(
        year_in_simulation=starting_age - state.start_age,
        age=starting_age, in_retirement=True,
        med=medical, cpi_cumulative=fire_year_cpi_cumulative,
        household=_medical_household(primary_alive, spouse_alive),
    )
    initial_full_premium = initial_components['premium_full']
    annual_medical_trajectory = bool(getattr(
        medical, 'annual_trajectory_enabled', False))

    starts_in_china = (
        relocation.relocation_age is not None
        and relocation.relocation_age <= starting_age
    )

    # FIX 2 (v9.5 lineage): dynamic CoL reduction if property enabled
    if sh_property.enabled:
        dynamic_col_reduction = compute_dynamic_col_reduction(
            sh_property.purchase_amount_y0)
    else:
        dynamic_col_reduction = sh_property.col_reduction
    col_effective = relocation.col_ratio

    if starts_in_china:
        # China-basis seed (F-1A): col applies to the NON-health portion only.
        initial_china_health = china_healthcare_cost_nominal(
            starting_age, fire_year_cpi_cumulative, china_healthcare)
        initial_total_expenses = (
            initial_components['non_medical'] * col_effective
            + initial_china_health
        )
    else:
        # FIX F-3 · GROSS basis: seed with the FULL premium.
        initial_total_expenses = (
            initial_components['non_medical']
            + initial_components['routine']
            + initial_full_premium
            + initial_components['oop']
        )

    # The enabled annual trajectory lets the withdrawal rule continue to
    # govern only the discretionary/non-medical share.  The zero-total branch
    # is explicit: a zero budget has no non-medical share, rather than an
    # epsilon that could turn an unmeasured zero into a plausible number.
    if annual_medical_trajectory:
        initial_nonmedical_share = (
            initial_components['non_medical'] / initial_total_expenses
            if initial_total_expenses > 0 else 0.0)

    initial_swr = initial_total_expenses / max(starting_accounts.total, 1.0)
    # FIX F-4 activation: pass the true CPI at rule initialization.
    rule_state = _init_rule(rule, starting_accounts.total,
                            initial_total_expenses, initial_swr,
                            fire_year_cpi_cumulative)

    in_china = starts_in_china
    fx_rate = relocation.fx_initial
    fx_at_relocation = relocation.fx_initial if starts_in_china else None
    relocation_done = starts_in_china
    gk_reseeded = starts_in_china        # China-basis rule already seeded
    reseed_year_idx = 0                  # rule year counter anchor
    cpi_track = fire_year_cpi_cumulative # CPI index fed to the rule
    property_fully_paid = False

    survived_financially = True
    shortfall_age = None
    age_at_death = None
    cpi_cumulative = fire_year_cpi_cumulative
    cpi_at_ss_claim = None
    cpi_at_spouse_ss_claim = None
    real_consumption_path = []
    nominal_consumption_path = []
    lifestyle_real_path = []
    portfolio_path = [accounts.total]

    seasoning_queue: list = []
    total_conversions = 0.0
    # Per-year record of what was ACTUALLY converted, not what was
    # requested. The two differ whenever the engine's caps bite -- the
    # pretax balance running out, or the 4x taxable buffer -- and a
    # schedule table that reported the request would be showing the user
    # a plan the engine did not run.
    conversion_by_age: list[tuple[int, float]] = []
    ss_payments_received_real = 0.0
    total_wd_received = 0.0
    total_ss_applied = 0.0
    ss_surplus_credited = 0.0
    total_income_received = 0.0
    # Per-year record of the income actually received, nominal. The
    # totals beside it were never enough for a guardrail: a policy about
    # income being INTERRUPTED needs to see the year it stopped, and a
    # lifetime total cannot show that. Without this series the
    # income-interruption trigger was correctly reported as
    # unobservable, which is honest and is not the same as closed.
    income_received_path: list[float] = []
    total_income_applied = 0.0
    income_surplus_credited = 0.0
    income_received_by_kind = {}
    income_applied_by_kind = {}
    income_surplus_by_kind = {}
    # Seeded with whatever the accumulation phase already anchored. Without
    # this a stream paid before FIRE and after it would be anchored twice --
    # re-based at the first retirement payment -- and a fixed annuity would
    # silently grow with inflation across the FIRE boundary, which is the one
    # thing a fixed annuity does not do.
    _income_anchor_by_index = dict(income_nominal_anchors or {})
    # No re-seeding from `stream.nominal_anchor_cpi` here. The original code
    # built this dict from the spec anchors, and a mutation removing that
    # changed nothing measurable — `_income_nominal` already prefers a spec
    # anchor over anything handed to it, and nothing else reads this dict. A
    # line whose removal no test can notice is a line that reads as load-
    # bearing and is not.
    # `None` (not `[]`) means the caller never modelled eldercare, and the
    # totals stay None so an unmeasured zero cannot print as a measured one.
    # The backtest passed `[]` and so reported 0.0 spent on a parent it had
    # never simulated; the LTC channel beside it already used None correctly.
    eldercare_on = eldercare_events is not None
    eldercare_total_real = 0.0 if eldercare_on else None
    eldercare_count = 0 if eldercare_on else None
    inheritance_received_real = 0.0
    sh_property_purchased_nominal = 0.0
    total_early_wd_penalty = 0.0

    eldercare_by_age = {}
    for age, amt in (eldercare_events or []):
        eldercare_by_age.setdefault(age, []).append(amt)

    # 4.0 Phase 2 long-term care. `None` means the module did not run, and the
    # totals below stay None to say so: a 0.0 printed for "care cost" is
    # indistinguishable from care that was modelled and happened to be free,
    # and this project has already paid for that confusion once.
    ltc_on = ltc_events is not None
    ltc_by_age = {}
    for age, amt in (ltc_events or []):
        ltc_by_age.setdefault(int(age), []).append(float(amt))
    ltc_total_real = 0.0 if ltc_on else None
    ltc_years_paid = 0 if ltc_on else None

    # Parent lifecycle. `None` means the module never ran, and the totals stay
    # None so a caller cannot read "0 paid toward a parent's care" as a
    # measurement when nothing was measured.
    parents_on = parent_care_events is not None or parent_bequests is not None
    parent_care_by_age = {}
    for age, amt in (parent_care_events or []):
        parent_care_by_age.setdefault(int(age), []).append(float(amt))
    parent_bequest_by_age = {}
    for age, amt in (parent_bequests or []):
        parent_bequest_by_age.setdefault(int(age), []).append(float(amt))
    parent_care_total_real = 0.0 if parents_on else None
    parent_bequests_received_real = 0.0 if parents_on else None

    # v9.8+ opt-in life events (None/empty => zero ops). Outflows use the same
    # funding machinery as eldercare (outside the consumption identity, so the
    # cash-conservation invariant is unaffected); inflows land in taxable like
    # an inheritance.
    life_by_age = {}
    for age, amt in (life_events or []):
        life_by_age.setdefault(int(age), []).append(float(amt))
    life_event_out_real = 0.0
    life_event_in_real = 0.0
    life_event_shortfalls = []

    # v9.8+ opt-in TRUE tax engine (E1). None/disabled => zero ops, bit-identical.
    _tt_on = tax_true is not None and getattr(tax_true, "enabled", False)
    tt_tax_total = 0.0
    tt_tax_total_real = 0.0
    tt_flow_err_max = 0.0
    # Aggregate cost basis of the taxable bucket. `taxable_gain_fraction` is no
    # longer applied to every withdrawal as though the money had just been
    # bought at that gain; it seeds the OPENING basis, and from there the basis
    # is carried: growth does not raise it, a withdrawal retires it pro rata,
    # and anything deposited back (RMD/SS excess, income surplus) arrives with
    # full basis because it was already taxed on the way in.
    #
    # `None` when the true-tax path is off, and it stays None -- a 0.0 here
    # would read as "measured, and the whole bucket is gain".
    # Last year's ordinary taxable income and filing status, used to price
    # THIS year's distributions. The drag is charged at the top of the year,
    # before the year's own solve exists, so a lookback is the only honest
    # option -- the same shape as IRMAA's statutory t-2 lookback already
    # modelled here. `None` until the first solve completes, and while it is
    # `None` the flat derived rate applies rather than a guessed bracket.
    _tt_prior_income = None
    taxable_basis = (max(0.0, accounts.taxable)
                     * (1.0 - float(tax_true.taxable_gain_fraction))
                     if _tt_on else None)
    tt_gain_fraction_last = None
    # IRMAA administrative lookback is intentionally a function-local ledger:
    # each retirement path owns its modeled tax-year records, and no prior
    # MAGI state can leak across paths, chunks, or calls.  Values are written
    # only after the year's final TRUE-tax solve below.
    irmaa_history = {}

    # 4.0 E6 · end-of-life spending peak, once per death (ruling R3). Consumes
    # no new randomness: it reads the mortality draws the loop already takes.
    # `None` when unpriced or when nothing can die; `0` once armed means the
    # sampler ran and this path produced no death inside the horizon, which is
    # a measurement rather than an absence.
    _eol_peak_real = getattr(medical, "eol_peak_real", None)
    eol_armed = _eol_peak_real is not None and mortality.enabled
    eol_peaks_charged = 0 if eol_armed else None
    eol_total_real = 0.0 if eol_armed else None

    for year_idx, (eq_r, bd_r, inf) in enumerate(
        zip(equity_returns, bond_returns, inflations)
    ):
        current_age = starting_age + year_idx + 1
        cpi_cumulative *= (1 + inf)
        pretax_prior_year_end = accounts.pretax_401k

        eq_pct = glide_path.equity_pct(current_age)
        port_r = blended_return(eq_r, bd_r, eq_pct)
        r_eff = port_r - friction

        accounts.pretax_401k *= (1 + r_eff)
        accounts.roth_ira *= (1 + r_eff)
        accounts.hsa *= (1 + r_eff)
        # Bracket-aware when the true-tax engine is on: qualified distributions
        # stack on last year's ordinary income through the real 0/15/20 rates,
        # the non-qualified remainder is ordinary. Off, or before the first
        # solve, the flat derived rate stands. A retiree living on basis lands
        # in the 0% bracket and pays no drag at all -- which the single
        # hardcoded number could not say.
        taxable_drag = tax_us.drag_taxable
        # An explicit `drag_taxable` outranks the brackets. Without this the
        # override silently stops working the moment the true-tax engine is
        # on -- and a plan saved with both would no longer reproduce, which
        # is the one guarantee the override exists to provide.
        if (_tt_on and not getattr(tax_us, 'drag_taxable_explicit', False)
                and _tt_prior_income is not None and accounts.taxable > 0.0):
            _prior_ordinary, _prior_mfj = _tt_prior_income
            _dividends_real = ((accounts.taxable / cpi_cumulative)
                               * tax_us.dividend_yield)
            taxable_drag = tax_us.dividend_yield * dividend_drag_rate_real(
                _dividends_real, tax_us.dividend_qualified_fraction,
                _prior_ordinary, _prior_mfj)
        accounts.taxable *= (1 + r_eff - taxable_drag)

        seasoning_queue, roth_locked = update_seasoning_queue(
            seasoning_queue, current_age, r_eff,
            roth_ladder.senior_age_threshold, roth_ladder.seasoning_years,
        )

        deaths_this_year = 0
        if household_on:
            spouse_age = current_age + _hh.spouse_age_offset
            if primary_alive and rng.random() < annual_mortality_rate(current_age, mortality):
                primary_alive = False
                deaths_this_year += 1
            if spouse_alive and rng.random() < annual_mortality_rate(max(1, spouse_age), spouse_mort):
                spouse_alive = False
                deaths_this_year += 1
            terminal = not primary_alive and not spouse_alive
        elif mortality.enabled:
            if rng.random() < annual_mortality_rate(current_age, mortality):
                deaths_this_year = 1
            terminal = deaths_this_year > 0
        else:
            terminal = False

        if terminal:
            age_at_death = current_age
            # The loop is about to end, and until this slice a terminal year
            # recorded no consumption at all -- the break happens before the
            # spending block. So the peak has to move real money here and be
            # recorded here, or `total_wd_received_nominal` and
            # `sum(nominal_consumption_path)` stop agreeing.
            if eol_armed and deaths_this_year:
                accounts, received_eol, _penalty = withdraw_with_seasoning_v94(
                    accounts,
                    deaths_this_year * _eol_peak_real * cpi_cumulative,
                    tax_us, roth_locked, current_age)
                total_wd_received += received_eol
                eol_peaks_charged += deaths_this_year
                eol_total_real += received_eol / cpi_cumulative
                real_consumption_path.append(received_eol / cpi_cumulative)
                nominal_consumption_path.append(received_eol)
                lifestyle_real_path.append(
                    received_eol / (col_effective if in_china else 1.0)
                    / cpi_cumulative)
            portfolio_path.append(accounts.total)
            break

        # Relocation event
        if (relocation.relocation_age is not None and not relocation_done
                and current_age >= relocation.relocation_age):
            in_china = True
            relocation_done = True
            fx_at_relocation = fx_rate

            if sh_property.enabled:
                purchase_nominal = sh_property.purchase_amount_y0 * cpi_cumulative
                accounts, paid, penalty = fund_shock_or_purchase_v98(
                    accounts, purchase_nominal, tax_us, current_age, roth_locked,
                )
                total_early_wd_penalty += penalty
                sh_property_purchased_nominal = paid
                if paid >= purchase_nominal - 1.0:
                    col_effective = max(
                        0.10, relocation.col_ratio - dynamic_col_reduction)
                    property_fully_paid = True
                else:
                    col_effective = relocation.col_ratio
                    property_fully_paid = False

        if relocation.relocation_age is not None and relocation.fx_sigma > 0:
            z = rng.standard_normal()
            _fx_drift = relocation.fx_drift
            _kappa = getattr(relocation, "ppp_kappa", 0.0) or 0.0
            if _kappa > 0.0:
                # E6 PPP anchor: pull log-fx toward the anchor. Same z draw
                # as the pure walk => kappa=0 stays bit-identical.
                _anchor = getattr(relocation, "fx_ppp", None) or relocation.fx_initial
                _fx_drift = _fx_drift + _kappa * (np.log(_anchor) - np.log(fx_rate))
            fx_rate = fx_rate * np.exp(_fx_drift + relocation.fx_sigma * z)

        if inheritance_event is not None:
            inh_age, inh_amount_y0 = inheritance_event
            if current_age == inh_age:
                accounts.taxable += inh_amount_y0 * cpi_cumulative
                inheritance_received_real += inh_amount_y0

        # A parent's bequest, credited the same way and in the same place, so
        # the two modules cannot disagree about what receiving money means.
        # Counted into its own total: a plan needs to be able to ask "how much
        # of this rests on inheriting", and a figure merged with the legacy
        # module's could not answer it.
        if current_age in parent_bequest_by_age:
            for amt_y0 in parent_bequest_by_age[current_age]:
                accounts.taxable += amt_y0 * cpi_cumulative
                parent_bequests_received_real += amt_y0

        if current_age in eldercare_by_age:
            for amt_y0 in eldercare_by_age[current_age]:
                shock_nominal = amt_y0 * cpi_cumulative
                accounts, paid, penalty = fund_shock_or_purchase_v98(
                    accounts, shock_nominal, tax_us, current_age, roth_locked,
                )
                total_early_wd_penalty += penalty
                eldercare_total_real += amt_y0
                eldercare_count += 1

        # The plan's share of a parent's care, funded through the very same
        # call as the eldercare shock above — it is the same event, arriving
        # from a module that knows which parent it belongs to and when they
        # die. Kept in its own total for the same reason the bequest is.
        if current_age in parent_care_by_age:
            for amt_y0 in parent_care_by_age[current_age]:
                care_nominal = amt_y0 * cpi_cumulative
                accounts, paid, penalty = fund_shock_or_purchase_v98(
                    accounts, care_nominal, tax_us, current_age, roth_locked,
                )
                total_early_wd_penalty += penalty
                parent_care_total_real += amt_y0

        # Long-term care, funded exactly as the eldercare shock is: a mandatory
        # outflow met from the account stack, outside the consumption identity,
        # so the cash-conservation invariant reads the same with the module on.
        if current_age in ltc_by_age:
            for amt_y0 in ltc_by_age[current_age]:
                care_nominal = amt_y0 * cpi_cumulative
                accounts, paid, penalty = fund_shock_or_purchase_v98(
                    accounts, care_nominal, tax_us, current_age, roth_locked,
                )
                total_early_wd_penalty += penalty
                ltc_total_real += amt_y0
                ltc_years_paid += 1

        if current_age in life_by_age:
            for amt_y0 in life_by_age[current_age]:
                ev_nominal = amt_y0 * cpi_cumulative
                if ev_nominal > 0:
                    accounts, paid, penalty = fund_shock_or_purchase_v98(
                        accounts, ev_nominal, tax_us, current_age, roth_locked,
                    )
                    total_early_wd_penalty += penalty
                    life_event_out_real += amt_y0
                    if paid < ev_nominal - 1.0:
                        life_event_shortfalls.append({
                            "age": current_age,
                            "phase": "retirement",
                            "mandatory_outflow_real": amt_y0,
                            "shortfall_real": max(0.0, ev_nominal - paid)
                            / max(cpi_cumulative, 1e-9),
                        })
                        if shortfall_age is None:
                            shortfall_age = current_age
                else:
                    accounts.taxable += -ev_nominal
                    life_event_in_real += -amt_y0

        # Structured income is evaluated after mandatory events so it cannot
        # retroactively cure a same-year event shortfall. It remains uncredited
        # until the spending target is settled below: cash covers consumption
        # first and only the surplus enters taxable.
        annual_income_by_kind = {}
        for stream_idx, stream in enumerate(income_streams):
            if not _income_schedule_active(
                    stream, current_age, retirement_start_age=starting_age):
                continue
            if not stream.cola and stream_idx not in _income_anchor_by_index:
                # Direct retirement/backtest callers may not have a pre-FIRE
                # path. Anchor at the first scheduled modeled payment, even if
                # owner death suppresses that payment.
                #
                # Keyed on `cola`, not on `kind == "pension"` — the other half
                # of the same fix as `_income_nominal`. Anchoring only pensions
                # while paying every non-COLA stream off an anchor meant a
                # fixed annuity hit "missing its CPI anchor" and the run died;
                # had the read side been left keyed on kind too, it would
                # instead have been paid as though inflation-linked, which is
                # the quiet version of the same bug. Anchoring at the first
                # payment is right for an annuity for the same reason it is
                # right for a pension: the quote is fixed in the dollars of the
                # year the money starts arriving.
                _income_anchor_by_index[stream_idx] = cpi_cumulative
            if not _income_owner_is_alive(
                    stream.owner, household_on,
                    primary_alive, spouse_alive):
                continue
            amount_nominal = _income_nominal(
                stream, cpi_cumulative,
                _income_anchor_by_index.get(stream_idx),
            )
            annual_income_by_kind[stream.kind] = (
                annual_income_by_kind.get(stream.kind, 0.0) + amount_nominal)
        annual_income_available = float(sum(annual_income_by_kind.values()))
        income_cash_this_year = annual_income_available > 0.0

        sim_year = current_age - state.start_age

        accounts, seasoning_queue, conversion_this_year = execute_roth_conversion(
            accounts, seasoning_queue, current_age, sim_year, roth_ladder,
        )
        total_conversions += conversion_this_year
        if conversion_this_year > 0:
            conversion_by_age.append((int(current_age),
                                      float(conversion_this_year)))
        roth_locked += conversion_this_year
        if _tt_on and not in_china and conversion_this_year > 0:
            # refund the ladder's flat tax — the true engine taxes the
            # conversion inside the real brackets below instead. In China the
            # true US solver is inactive, so refunding would make it tax-free.
            accounts.taxable += conversion_this_year * roth_ladder.federal_tax_rate

        components = compute_medical_components(
            year_in_simulation=sim_year, age=current_age, in_retirement=True,
            med=medical, cpi_cumulative=cpi_cumulative,
            household=_medical_household(primary_alive, spouse_alive),
        )
        full_premium = components['premium_full']
        # Only the exchange-priced part of the basket is eligible for a
        # marketplace subsidy. On every single-anchor pre-Medicare year this
        # IS the whole premium, so nothing moves there; it diverges only once
        # one member is on Medicare while another is still on the bridge, and
        # in that year charging the subsidy against the whole basket would let
        # the affordability cap pay down a Medicare premium.
        aca_priced_premium = components['premium_aca_portion']
        current_medical_gross = (
            components['routine'] + full_premium + components['oop'])

        # ---- FIX F-1A · target computation ----
        _trajectory_relocation_year = False
        if in_china and not gk_reseeded:
            # Relocation year: last US-basis GK call, translate, RE-SEED.
            target_us, rule_state = rule.compute_target_withdrawal(
                year_in_retirement=year_idx, age=current_age,
                portfolio_nominal=accounts.total, inflation_this_year=inf,
                cpi_cumulative=cpi_cumulative, state=rule_state,
            )
            china_health_nominal = china_healthcare_cost_nominal(
                current_age, cpi_cumulative, china_healthcare)
            us_health = current_medical_gross
            if annual_medical_trajectory:
                _trajectory_relocation_year = True
                nonmedical_us = target_us * initial_nonmedical_share
                if getattr(state, "spending_decline", 0.0) > 0.0 and year_idx > 0:
                    nonmedical_us *= max(
                        getattr(state, "spending_decline_floor", 0.55),
                        (1.0 - state.spending_decline) ** year_idx)
                if household_on and not (primary_alive and spouse_alive):
                    nonmedical_us *= _hh.survivor_spending_frac
                L = nonmedical_us * col_effective + china_health_nominal
            else:
                L = (max(0.0, target_us - us_health) * col_effective
                     + china_health_nominal)
            fx_ratio = fx_rate / max(fx_at_relocation, 1e-9)
            prev_triggers = rule_state.get('guardrail_triggers', 0)
            rule_state = _init_rule(
                rule, accounts.total * fx_ratio, L,
                L / max(accounts.total * fx_ratio, 1.0), cpi_cumulative)
            rule_state['guardrail_triggers'] = prev_triggers
            cpi_track = cpi_cumulative
            gk_reseeded = True
            reseed_year_idx = year_idx
            target_nominal = L * (fx_at_relocation / fx_rate)
        elif in_china and gk_reseeded:
            cn_inf = (state.inflation_cn if relocation.use_cn_inflation
                      else state.inflation)
            cpi_track *= (1 + cn_inf)
            fx_ratio = fx_rate / max(fx_at_relocation, 1e-9)
            target_L, rule_state = rule.compute_target_withdrawal(
                year_in_retirement=year_idx - reseed_year_idx,
                age=current_age,
                portfolio_nominal=accounts.total * fx_ratio,
                inflation_this_year=cn_inf,
                cpi_cumulative=cpi_track, state=rule_state,
            )
            target_nominal = target_L * (fx_at_relocation / fx_rate)
        else:
            target_nominal, rule_state = rule.compute_target_withdrawal(
                year_in_retirement=year_idx, age=current_age,
                portfolio_nominal=accounts.total, inflation_this_year=inf,
                cpi_cumulative=cpi_cumulative, state=rule_state,
            )
            cpi_track = cpi_cumulative

        # ---- v9.8+ opt-in: retirement spending "smile" (real age-decline) ----
        # Scales the guardrail-adjusted budget by a compounding real decline
        # (default 0 => no change). Applied consistently downstream, so the
        # cash-accounting convention is preserved: actual receipt years use
        # delivered cash exactly; successful no-receipt years retain the
        # historical <=$1 target-recording tolerance.
        # DESIGN NOTE (also applies to the survivor scaling below): the scale
        # is applied AFTER the GK rule call, so guardrails keep evaluating the
        # UNSCALED budget against the portfolio — a mildly conservative bias
        # by construction (disclosed in the app's limitations panel).
        if (not _trajectory_relocation_year
                and getattr(state, "spending_decline", 0.0) > 0.0
                and year_idx > 0):
            _decay = max(getattr(state, "spending_decline_floor", 0.55),
                         (1.0 - state.spending_decline) ** year_idx)
            target_nominal *= _decay

        # ---- household: after the first death, spend drops to survivor level ----
        if (not _trajectory_relocation_year and household_on
                and not (primary_alive and spouse_alive)):
            target_nominal *= _hh.survivor_spending_frac

        if annual_medical_trajectory and not in_china:
            # The rule and lifestyle modifiers own only the initial
            # non-medical share.  The current US medical basket is mandatory:
            # replace the legacy medical share rather than adding a second
            # basket, and let ACA reduce only its full-price premium below.
            target_nominal = compose_annual_medical_target(
                target_nominal, initial_nonmedical_share, components, True)

        # ---- blocky spending: a lump, or nothing ----
        # Placed AFTER the medical basket and the survivor adjustment so it
        # scales with what this year actually costs, and BEFORE Social
        # Security so the income side sees the real need. It is spending, so
        # it flows through the same withdrawal it always did -- nothing here
        # invents a new cash channel for the attribution to miss.
        if blocky_rng is not None and blocky_spending is not None:
            if blocky_rng.random() < blocky_spending.annual_probability:
                target_nominal *= (1.0 + blocky_spending.size_fraction)

        # ---- Social Security (haircut when in China) ----
        spouse_age = (current_age + _hh.spouse_age_offset
                      if household_on else None)
        if ss.enabled and cpi_at_ss_claim is None and current_age >= ss.claim_age:
            cpi_at_ss_claim = cpi_cumulative
        if (household_on and spouse_ss.enabled and cpi_at_spouse_ss_claim is None
                and spouse_age >= spouse_ss.claim_age):
            cpi_at_spouse_ss_claim = cpi_cumulative
        if household_on:
            # Compute both earned benefit records independently of who died.
            # Both alive receive both; a survivor receives the higher record.
            b_p = compute_ss_annual_income(
                current_age, cpi_at_ss_claim or cpi_cumulative,
                cpi_cumulative, ss)
            b_s = compute_ss_annual_income(
                spouse_age, cpi_at_spouse_ss_claim or cpi_cumulative,
                cpi_cumulative, spouse_ss)
            ss_income_gross = ((b_p + b_s) if (primary_alive and spouse_alive)
                               else max(b_p, b_s))
        else:
            ss_income_gross = compute_ss_annual_income(
                current_age, cpi_at_ss_claim or cpi_cumulative, cpi_cumulative, ss,
            )
        # ---- trust fund depletion + COLA divergence ----
        # Applied to the GROSS scheduled benefit, before the China
        # withholding haircut: a reduced benefit is what the program pays,
        # and withholding is taken from what is actually paid. Reversing the
        # order would withhold tax on money nobody received.
        if ss_trust_fund is not None and ss_trust_fund.enabled:
            if ss_trust_fund.cola_delta_annual:
                # COLAs accrue from age 62 whether or not claiming is
                # delayed, so the divergence compounds from 62 rather than
                # from the claim age.
                cola_years = max(0, current_age - 62)
                ss_income_gross *= ((1.0 + ss_trust_fund.cola_delta_annual)
                                    ** cola_years)
            if ss_trust_fund_depletion_year is not None:
                calendar_year = (ss_trust_fund.plan_start_year
                                 + (current_age - state.start_age))
                ss_income_gross *= ss_payable_share(
                    calendar_year, ss_trust_fund_depletion_year,
                    float(SSA_TRUST_FUND["oasi_payable_at_depletion_intermediate"]),
                    float(SSA_TRUST_FUND["oasi_payable_2100_intermediate"]))

        if in_china:
            ss_income = ss_income_gross * (1 - ss_nra.haircut_fraction)
        else:
            ss_income = ss_income_gross
        ss_payments_received_real += ss_income / cpi_cumulative

        # ---- Healthcare adjustment (US, pre-Medicare only: F-3 + F-14) ----
        if in_china:
            adjusted_target = target_nominal  # rule owns the China budget
        elif current_age < medical.medicare_age:
            _aca_household_size = max(
                int(getattr(aca, "household_size", 1) or 1),
                ((int(primary_alive) + int(spouse_alive)) if household_on else 1))
            _provisional_portfolio_need = max(
                0.0, target_nominal - annual_income_available)
            magi_proxy = estimate_magi_proxy(
                taxable_wd_nominal=_provisional_portfolio_need * 0.5,
                pretax_401k_wd_nominal=_provisional_portfolio_need * 0.3,
            ) + conversion_this_year
            aca_paid = compute_aca_premium_paid(
                aca_priced_premium, magi_proxy, cpi_cumulative, aca,
                household_size=_aca_household_size,
            )
            premium_savings = aca_priced_premium - aca_paid
            adjusted_target = max(0.0, target_nominal - premium_savings)
        else:
            adjusted_target = target_nominal  # F-14: no ACA machinery at 65+

        if in_china and sh_property.enabled and sh_property.rental_income_y0 > 0:
            rental_nominal = sh_property.rental_income_y0 * cpi_cumulative
            adjusted_target = max(0.0, adjusted_target - rental_nominal)

        # ---- 4.0 E6 · end-of-life peak for a death that did NOT end the run --
        # Only a couple reaches this: the first death leaves a survivor, so the
        # loop carries on and the peak is an ordinary line in this year's cash
        # need. It is added AFTER the ACA adjustment on purpose -- terminal care
        # is not a marketplace premium, and running it through the affordability
        # cap would let a subsidy pay down a funeral. It is also deliberately
        # not scaled by the destination cost of living: the figure is the one
        # the user typed, and re-pricing it by geography would be a relationship
        # they never stated. Both are disclosed in the limitations panel.
        if eol_armed and deaths_this_year:
            _eol_nominal = deaths_this_year * _eol_peak_real * cpi_cumulative
            adjusted_target += _eol_nominal
            eol_peaks_charged += deaths_this_year
            eol_total_real += _eol_nominal / cpi_cumulative

        # ---- FIX F-2 · SS offsets withdrawals in both regions ----
        material_true_tax_shortfall = False
        strict_cash_year = income_cash_this_year
        if _tt_on and not in_china:
            # ---- TRUE tax path (E1): real brackets / SS torpedo / RMD / IRMAA ----
            tax_true_year = tax_true
            if household_on:
                tax_true_year = replace(
                    tax_true,
                    filing_jointly=(tax_true.filing_jointly
                                    and primary_alive and spouse_alive))
            need1_total = adjusted_target
            income_applied_1 = min(
                annual_income_available, need1_total)
            need1 = max(0.0, need1_total - income_applied_1)
            # Measured once, from the balance both solves start from. The
            # second solve re-runs from this same `accounts`, so taking the
            # fraction after the first would price the re-solve off a bucket
            # that was never actually drawn down.
            _tax_before = max(0.0, accounts.taxable)
            _gain_frac = (0.0 if _tax_before <= 0.0 else
                          max(0.0, min(1.0,
                                       (_tax_before - taxable_basis)
                                       / _tax_before)))
            res_tt = solve_retirement_year(
                accounts, need1, ss_income, conversion_this_year,
                roth_locked, current_age, cpi_cumulative, tax_true_year,
                rmd_balance_prior_year_end=pretax_prior_year_end,
                gain_fraction=_gain_frac)
            if current_age < medical.medicare_age:
                # ACA: re-derive the subsidy from TRUE MAGI (one extra pass)
                aca_paid_true = compute_aca_premium_paid(
                    aca_priced_premium, res_tt["magi_aca_nominal"],
                    cpi_cumulative, aca,
                    household_size=_aca_household_size)
                need2_total = max(
                    0.0, target_nominal - (aca_priced_premium - aca_paid_true))
            else:
                need2_total = need1_total
                if tax_true_year.irmaa_enabled:
                    _persons = ((int(primary_alive) + int(spouse_alive))
                                if household_on
                                else (2 if tax_true_year.filing_jointly else 1))
                    _lookback = irmaa_history.get(current_age - 2)
                    if _lookback is None:
                        # No modeled return exists for the exact source year.
                        # Preserve the old numerical proxy, but the UI/docs
                        # explicitly label this as a current-year fallback.
                        _irmaa_magi_real = (
                            res_tt["magi_agi_nominal"] / cpi_cumulative)
                        _irmaa_filing_jointly = tax_true_year.filing_jointly
                    else:
                        # MAGI is nominal in the source-year record; compare it
                        # with the premium-year indexed threshold basis. Do not
                        # divide by source-year CPI or use a nearest year.
                        _irmaa_magi_real = (
                            _lookback["magi_agi_nominal"] / cpi_cumulative)
                        _irmaa_filing_jointly = _lookback["filing_jointly"]
                    need2_total = need1_total + irmaa_annual_surcharge_real(
                        _irmaa_magi_real, _irmaa_filing_jointly,
                        _persons) * cpi_cumulative
            income_applied = min(annual_income_available, need2_total)
            income_surplus = annual_income_available - income_applied
            need2 = max(0.0, need2_total - income_applied)
            if abs(need2 - need1) > 1.0:
                res_tt = solve_retirement_year(
                    accounts, need2, ss_income, conversion_this_year,
                    roth_locked, current_age, cpi_cumulative, tax_true_year,
                    rmd_balance_prior_year_end=pretax_prior_year_end,
                    gain_fraction=_gain_frac)
            # Store the final result, after any ACA/IRMAA-driven re-solve, so
            # the exact t−2 record carries both its final MAGI and tax-year
            # filing status into a future premium year.
            irmaa_history[current_age] = {
                "magi_agi_nominal": float(res_tt["magi_agi_nominal"]),
                "filing_jointly": bool(tax_true_year.filing_jointly),
            }
            # ACA/IRMAA can move the final need by <=$1 without triggering a
            # second solve. Judge material insolvency against that final need,
            # not the preliminary solver's now-stale ``shortfall``.
            material_true_tax_shortfall = (
                max(0.0, need2 - res_tt["delivered"]) > 1.0
            )
            strict_cash_year = (
                income_cash_this_year or material_true_tax_shortfall
            )
            adjusted_target = need2_total
            accounts = res_tt["accounts"]
            # Retire basis in the same proportion as the shares sold, then
            # credit what came back in. `deposit_back` is RMD/SS money that has
            # already been taxed as ordinary income, and `income_surplus` is
            # earned income -- taxing either again as gain when it is later
            # withdrawn would be the double-count this slice exists to remove.
            #
            # MEASURED, so nobody has to trust the paragraph above: of the
            # four bookkeeping steps here, only the pro-rata retirement is
            # observable today. Deleting the `deposit_back` credit, the
            # `income_surplus` credit, or the clamp leaves every output
            # bit-identical across a plain true-tax run, an RMD-heavy one that
            # deposits 5.6M back, and one seeded at 100% gain. They are kept
            # because they are correct -- money that was taxed on the way in
            # carries basis on the way out -- and because the dividend/interest
            # drag slice will make later taxable withdrawals routine, which is
            # when they start to bite. They are NOT covered by mutation, and
            # saying so here is cheaper than a reader assuming they are.
            _w_tax = float(res_tt["taxable_wd"])
            if _tax_before > 0.0 and _w_tax > 0.0:
                taxable_basis -= taxable_basis * min(1.0, _w_tax / _tax_before)
            taxable_basis += float(res_tt["deposit_back"])
            tt_gain_fraction_last = _gain_frac
            _tt_prior_income = (float(res_tt["ordinary_taxable_real"]),
                                bool(tax_true_year.filing_jointly))
            if income_surplus > 0:
                accounts.taxable += income_surplus
                taxable_basis += income_surplus
            taxable_basis = min(max(0.0, taxable_basis),
                                max(0.0, accounts.taxable))
            tt_tax_total += res_tt["tax_total"]
            tt_tax_total_real += res_tt["tax_total"] / cpi_cumulative
            tt_flow_err_max = max(tt_flow_err_max, res_tt["flow_err"])
            penalty_this_yr = res_tt["penalty"]
            if strict_cash_year:
                # ``delivered`` is net of the tax generated by SS itself.
                # Do not claim gross SS funded consumption that never arrived,
                # either in an active-income year or a material insolvency.
                ss_applied = min(ss_income, need2, res_tt["delivered"])
            else:
                # Preserve the historical no-income result shape and ledgers.
                ss_applied = min(ss_income, need2)
            received = max(0.0, res_tt["delivered"] - ss_applied)
            portfolio_withdrawal_needed = max(0.0, need2 - ss_applied)
            total_early_wd_penalty += penalty_this_yr
            total_wd_received += received
            total_ss_applied += ss_applied
        else:
            income_applied = min(annual_income_available, adjusted_target)
            income_surplus = annual_income_available - income_applied
            if income_surplus > 0:
                accounts.taxable += income_surplus
            need_after_income = max(0.0, adjusted_target - income_applied)
            ss_applied = min(ss_income, need_after_income)
            ss_surplus = ss_income - ss_applied
            if ss_surplus > 0:
                accounts.taxable += ss_surplus
                ss_surplus_credited += ss_surplus
            portfolio_withdrawal_needed = max(
                0.0, need_after_income - ss_applied)
            tax_to_use = tax_cn_effective if in_china else tax_us
            # ---- v9.8+ opt-in: progressive US tax on the traditional bucket ----
            # Replaces the flat traditional rate with a size-aware effective ordinary
            # rate (brackets + std deduction), using this year's need (real) as the
            # taxable-income proxy. Default OFF => flat rate, unchanged.
            if (not in_china) and getattr(tax_us, "progressive", False):
                _real_inc = portfolio_withdrawal_needed / max(cpi_cumulative, 1e-9)
                _eff = effective_ordinary_rate(
                    _real_inc, std_deduction=getattr(tax_us, "std_deduction", 14_600.0),
                    state_rate=getattr(tax_us, "state_rate", 0.0),
                    filing_jointly=(household_on and primary_alive and spouse_alive))
                tax_to_use = replace(tax_us, withdrawal_tax_traditional=_eff)

            accounts, received, penalty_this_yr = withdraw_with_seasoning_v94(
                accounts, portfolio_withdrawal_needed, tax_to_use, roth_locked,
                current_age,
            )
            total_early_wd_penalty += penalty_this_yr
            total_wd_received += received
            total_ss_applied += ss_applied

        # Appended every year, INCLUDING years with no income and plans with
        # no streams at all. A shorter series would make "this household
        # receives nothing" indistinguishable from "nobody looked", and the
        # reader downstream reports the second as unmeasured.
        income_received_path.append(float(annual_income_available)
                                    if _income_on else 0.0)
        if _income_on:
            annual_applied_by_kind, annual_surplus_by_kind = (
                _allocate_income_by_kind(
                    annual_income_by_kind, income_applied)
            )
            total_income_received += annual_income_available
            total_income_applied += income_applied
            income_surplus_credited += income_surplus
            for kind, amount in annual_income_by_kind.items():
                income_received_by_kind[kind] = (
                    income_received_by_kind.get(kind, 0.0) + amount)
            for kind, amount in annual_applied_by_kind.items():
                income_applied_by_kind[kind] = (
                    income_applied_by_kind.get(kind, 0.0) + amount)
            for kind, amount in annual_surplus_by_kind.items():
                income_surplus_by_kind[kind] = (
                    income_surplus_by_kind.get(kind, 0.0) + amount)

        if (material_true_tax_shortfall
                or received < portfolio_withdrawal_needed - 1.0):
            survived_financially = False
            shortfall_age = current_age
            portfolio_path.append(accounts.total)
            consumed = received + ss_applied + income_applied
            real_consumption_path.append(max(0.0, consumed) / cpi_cumulative)
            nominal_consumption_path.append(max(0.0, consumed))
            lifestyle_real_path.append(
                max(0.0, consumed) / (col_effective if in_china else 1.0)
                / cpi_cumulative)
            break

        if income_cash_this_year:
            # Any year that actually receives structured income uses only cash
            # that arrived, across both flat- and true-tax withdrawal paths.
            # This keeps the active-income ledger exact even when the legacy
            # withdrawal helper accepts a sub-dollar solvency tolerance.
            consumed = max(
                0.0, received + ss_applied + income_applied)
        else:
            # With no structured receipt, retain the historical target-recording
            # convention for successful <=$1 tolerance years. Material true-tax
            # shortfalls already exited through the failure branch above.
            consumed = max(0.0, adjusted_target)
        real_consumption_path.append(consumed / cpi_cumulative)
        nominal_consumption_path.append(consumed)
        lifestyle_real_path.append(
            consumed / (col_effective if in_china else 1.0) / cpi_cumulative)
        portfolio_path.append(accounts.total)

    years_in_retirement = len(portfolio_path) - 1
    died_during_retirement = age_at_death is not None

    result = {
        'survived_financially': survived_financially,
        'died_during_retirement': died_during_retirement,
        'age_at_death': age_at_death,
        'shortfall_age': shortfall_age,
        'years_in_retirement': years_in_retirement,
        'terminal_balance': accounts.total if survived_financially else 0.0,
        'final_accounts': accounts,
        'fx_at_relocation': fx_at_relocation,
        'final_fx': fx_rate,
        'in_china_at_end': in_china,
        'guardrail_triggers': rule_state.get('guardrail_triggers', 0),
        'real_consumption_path': real_consumption_path,
        'nominal_consumption_path': nominal_consumption_path,
        'lifestyle_real_path': lifestyle_real_path,
        'portfolio_path': list(portfolio_path),
        'mean_real_consumption': (
            float(np.mean(real_consumption_path)) if real_consumption_path else 0.0
        ),
        'min_real_consumption': (
            float(np.min(real_consumption_path)) if real_consumption_path else 0.0
        ),
        'mean_lifestyle_real': (
            float(np.mean(lifestyle_real_path)) if lifestyle_real_path else 0.0
        ),
        'total_roth_conversions_nominal': total_conversions,
        'roth_conversion_by_age': conversion_by_age,
        'ss_total_received_real': ss_payments_received_real,
        'total_wd_received_nominal': total_wd_received,
        'total_ss_applied_nominal': total_ss_applied,
        'ss_surplus_credited_nominal': ss_surplus_credited,
        'final_roth_locked': roth_locked if seasoning_queue else 0.0,
        'eldercare_total_real': eldercare_total_real,
        # `None` = the peak was never priced, or nothing in this run can die.
        # `0` = the sampler ran and this path reached the horizon alive, which
        # is a reading rather than a missing one.
        'eol_peaks_charged': eol_peaks_charged,
        'eol_total_real': eol_total_real,
        # The taxable bucket's cost basis at the end of the run, for pricing
        # what is left. `None` when the true-tax engine was off: no basis was
        # tracked, so there is nothing measured to report, and a 0 here would
        # say "all of it is gain" -- the most expensive possible guess stated
        # as a fact.
        'taxable_basis_end': taxable_basis,
        'eldercare_event_count': eldercare_count,
        # None, not 0.0, when the module did not run. See where these are
        # initialised: an unmeasured zero and a measured zero must not print
        # the same.
        'ltc_total_real': ltc_total_real,
        'ltc_years_paid': ltc_years_paid,
        # Same rule, same reason: None until the parent module actually ran.
        'parent_care_total_real': parent_care_total_real,
        'parent_bequests_received_real': parent_bequests_received_real,
        'life_event_out_real': life_event_out_real,
        'life_event_in_real': life_event_in_real,
        'life_event_shortfalls': life_event_shortfalls,
        'true_tax_total_nominal': tt_tax_total,
        'true_tax_total_real': tt_tax_total_real,
        'true_tax_flow_err': tt_flow_err_max,
        'inheritance_received_real': inheritance_received_real,
        'sh_property_purchased_nominal': sh_property_purchased_nominal,
        'sh_property_fully_paid': property_fully_paid,
        'total_early_wd_penalty_nominal': total_early_wd_penalty,
        'glide_path_name': glide_path.name,
        'col_reduction_applied': dynamic_col_reduction if sh_property.enabled else 0.0,
        'lifetime_success': survived_financially and not life_event_shortfalls,
        # Outside the `_income_on` block on purpose. A plan with no income
        # streams receives zero every year -- that is a measurement -- and
        # hiding the key would make it indistinguishable from a run that never
        # recorded one, which is what the guardrail study would then report.
        'income_received_path_nominal': list(income_received_path),
    }
    if _income_on:
        result.update({
            'total_income_received_nominal': total_income_received,
            'total_income_applied_nominal': total_income_applied,
            'income_surplus_credited_nominal': income_surplus_credited,
            'income_received_by_kind_nominal': income_received_by_kind,
            'income_applied_by_kind_nominal': income_applied_by_kind,
            'income_surplus_by_kind_nominal': income_surplus_by_kind,
        })
    return result


# ============================================================
# v9.8 LIFECYCLE
# ============================================================
@dataclass(frozen=True)
class _HouseholdAccumMortalitySchedule:
    alive_at_start: tuple[tuple[bool, bool], ...]
    alive_after_year: tuple[tuple[bool, bool], ...]
    draw_counts_after_year: tuple[int, ...]
    last_survivor_death_age: Optional[int]


def _sample_household_accum_mortality_schedule(
    rng: np.random.Generator,
    n_years: int,
    start_age: int,
    mortality: MortalityParams,
    household,
):
    """Preview the full household accumulation mortality schedule.

    Death is checked at each year-end, so ``alive_at_start`` controls that
    year's contributions and a death only removes future contributions. The
    preview temporarily snapshots/restores the generator state and records the
    cumulative draw count after each year. Once the corrected FIRE/censor year
    is known, the caller replays exactly that many draws on the real shared
    stream. This avoids projecting twice (and therefore avoids consuming the
    independent layoff stream twice).
    """
    # Snapshot/restore the small BitGenerator state instead of deep-copying the
    # whole Generator for every Monte Carlo path. The preview is synchronous
    # and accumulation uses a separate layoff RNG, so the official shared
    # stream remains observationally untouched until `_resume...` advances it.
    rng_state = copy.deepcopy(rng.bit_generator.state)
    try:
        preview_draws = rng.random(max(0, 2 * n_years))
    finally:
        rng.bit_generator.state = rng_state
    preview_idx = 0
    alive_at_start = []
    alive_after_year = []
    draw_counts_after_year = [0]
    draw_count = 0
    primary_alive = True
    spouse_alive = True
    last_survivor_death_age = None
    spouse_base_mort = (
        MORTALITY_FEMALE
        if household.spouse_sex == "female"
        else MORTALITY_MALE
    )
    spouse_mortality = replace(
        spouse_base_mort,
        enabled=mortality.enabled,
        cap_age=mortality.cap_age,
    )

    for year_idx in range(n_years):
        alive_at_start.append((primary_alive, spouse_alive))
        age = start_age + year_idx + 1
        spouse_age = max(1, age + household.spouse_age_offset)
        if primary_alive:
            primary_draw = preview_draws[preview_idx]
            preview_idx += 1
            draw_count += 1
            if primary_draw < annual_mortality_rate(age, mortality):
                primary_alive = False
        if spouse_alive:
            spouse_draw = preview_draws[preview_idx]
            preview_idx += 1
            draw_count += 1
            if spouse_draw < annual_mortality_rate(
                    spouse_age, spouse_mortality):
                spouse_alive = False
        if (last_survivor_death_age is None
                and not primary_alive and not spouse_alive):
            last_survivor_death_age = age
        alive_after_year.append((primary_alive, spouse_alive))
        draw_counts_after_year.append(draw_count)

    return _HouseholdAccumMortalitySchedule(
        alive_at_start=tuple(alive_at_start),
        alive_after_year=tuple(alive_after_year),
        draw_counts_after_year=tuple(draw_counts_after_year),
        last_survivor_death_age=last_survivor_death_age,
    )


def _resume_household_accum_mortality(
    rng: np.random.Generator,
    schedule: _HouseholdAccumMortalitySchedule,
    stop_year: int,
) -> tuple[bool, bool, Optional[int]]:
    """Advance the real shared stream through ``stop_year`` and return survivors."""
    if stop_year < 0 or stop_year >= len(schedule.draw_counts_after_year):
        raise ValueError("household mortality stop year is outside the schedule")
    draw_count = schedule.draw_counts_after_year[stop_year]
    if draw_count:
        rng.random(draw_count)
    primary_alive, spouse_alive = (
        schedule.alive_after_year[stop_year - 1]
        if stop_year else (True, True)
    )
    last_death = (
        schedule.last_survivor_death_age
        if not primary_alive and not spouse_alive else None
    )
    return primary_alive, spouse_alive, last_death


def simulate_lifecycle_v98(
    config: V7Config = None,
    promo_params: PromotionParams = None,
    contrib_params: V8ContributionParams = None,
    rule: WithdrawalRule = None,
    glide_path: GlidePath = None,
    bond_params: BondParams = None,
    medical: MedicalParams = None,
    aca: ACAParams = None,
    mortality: MortalityParams = None,
    roth_ladder: RothLadderParams = None,
    ss: SocialSecurityParams = None,
    ftc: FTCParams = None,
    obbba: OBBBAParams = None,
    eldercare: EldercareShockParams = None,
    inheritance: InheritanceParams = None,
    ltc: Optional["LTC.LtcParams"] = None,
    parents: Optional["PARENTS.ParentsParams"] = None,
    sh_property: ShanghaiPropertyParams = None,
    blocky_spending: BlockySpendingParams = None,
    house_price: HousePriceProcess = None,
    human_capital: HumanCapitalParams = None,
    ss_trust_fund: SSTrustFundParams = None,
    initial: AccountStack = None,
    state: State = None,
    tax_us: TaxParams = None,
    tax_cn: TaxParamsChina = None,
    fire_swr: float = None,
    relocation: RelocationParams = None,
    regimes: Optional[list] = None,
    rng: np.random.Generator = None,
    china_healthcare: ChinaHealthcareParams = None,
    ss_nra: SSNRAHaircutParams = None,
    life_events: Optional[list] = None,
    income_streams: Optional[Sequence[IncomeStreamSpec]] = None,
    housing_mortgage: Optional[HousingMortgageSpec] = None,
    tax_true: Optional[TrueTaxParams] = None,
    returns_x=None,
) -> dict:
    """v9.8 lifecycle: v9.6 accumulation (bit-identical) + v9.8 retirement.
    life_events: optional [(age, amount_real)] — + = outflow, − = inflow;
    None/empty => bit-identical to the unextended engine.
    income_streams: optional structured after-tax annual cash flows. None/empty
    adds no income ledgers and does not move RNG order. Structured income itself
    causes no omitted-vs-None drift; separately documented core correctness
    fixes in this engine revision may intentionally change historical arithmetic.
    returns_x: optional fire_returns_x.ReturnsXParams — E4 returns 2.0
    (markov regime switching / historical block bootstrap). None or
    enabled=False => the v7 sampler runs untouched (bit-identical).
    ltc: optional ltc_model.LtcParams — the user's OWN long-term care, drawn on
    its own stream (params.rng) so the shared stream keeps its draw order.
    None or mode='off' => not one draw is taken and the run is bit-identical."""
    config = config or V7Config()
    promo_params = promo_params or PromotionParams()

    # Blocky spending draws from a generator of its own, derived from the
    # shared generator's state AT ENTRY -- before anything has drawn.
    #
    # Reading `.state` consumes nothing, which is what lets this stay
    # bit-identical when off: an unbuilt or unused generator cannot shift the
    # shared stream, so every existing plan reproduces exactly.
    #
    # Derived at entry rather than at the retirement call so this module's
    # numbers depend on the run's seed alone. Derived later, they would depend
    # on how many draws accumulation happened to take -- and then editing an
    # unrelated module would silently reshuffle this one.
    # One root for every module that wants a stream of its own, read from the
    # shared generator's state AT ENTRY -- before anything has drawn. Reading
    # `.state` consumes nothing, which is what lets each of these modules stay
    # bit-identical when off: an unbuilt generator cannot shift the shared
    # stream, so every existing plan reproduces exactly.
    #
    # Read at entry rather than at each use so a module's numbers depend on
    # the run's seed alone. Read later, they would depend on how many draws
    # accumulation happened to take -- and then editing an unrelated module
    # would silently reshuffle this one.
    _child_seed_root = None
    if rng is not None:
        _entry_state = rng.bit_generator.state
        _child_seed_root = int(
            _entry_state.get("state", {}).get("state", 0)
            if isinstance(_entry_state, dict) else 0)

    # The trust fund's depletion year is ONE draw for the whole path, not one
    # per year. It is a single systemic event: drawing it annually would turn
    # a date into a per-year coin flip, which is a different -- and wrong --
    # model of the same words.
    _sstf = ss_trust_fund or SSTrustFundParams()
    _sstf_year = None
    if _sstf.enabled:
        if _sstf.plan_start_year is None:
            raise ValueError(
                "ss_trust_fund is enabled but plan_start_year is unset. "
                "Reserve depletion is a CALENDAR event and this engine runs "
                "in ages, so the year the plan's year zero represents has to "
                "be stated. It is not defaulted: a hardcoded year would "
                "mis-time a federal event by however far the plan is offset, "
                "and reading the clock would make the same plan answer "
                "differently in different years.")
        if _sstf.scenario == "range" and _child_seed_root is not None:
            # The three alternatives are the REPORT'S range. Weighting them
            # equally is this app's choice, not the report's -- the Trustees
            # attach no probability to any alternative -- and the limitations
            # panel says exactly that.
            _sstf_rng = np.random.default_rng(
                (_child_seed_root + _sstf.seed_offset) % (2 ** 63))
            _sstf_year = int(_sstf_rng.choice([
                SSA_TRUST_FUND["oasi_depletion_year_high_cost"],
                SSA_TRUST_FUND["oasi_depletion_year_intermediate"],
                SSA_TRUST_FUND["oasi_depletion_year_low_cost"]]))
        else:
            _sstf_year = int(SSA_TRUST_FUND["oasi_depletion_year_intermediate"])

    # The house price's own generator, derived the same way and for the same
    # reasons as blocky spending's: at entry, from state that reading does not
    # consume, so switching this on cannot reshuffle any other module.
    _hc = human_capital or HumanCapitalParams()
    _hc_factors = None
    _hc_rng = None
    if _hc.enabled and rng is not None:
        _hc_entry = rng.bit_generator.state
        _hc_root = (_hc_entry.get("state", {}).get("state", 0)
                    if isinstance(_hc_entry, dict) else 0)
        _hc_rng = np.random.default_rng(
            (int(_hc_root) + _hc.seed_offset) % (2 ** 63))
        # Permanent shocks accumulate; transitory ones do not. That split is
        # the whole point: a lost promotion and a one-off missed bonus are the
        # same number to a single "wage volatility" dial and nothing like the
        # same event to a plan. Median-anchored (loc=0) so switching this on
        # adds spread without walking the career's central case, the rule the
        # house price module had to learn the hard way.
        _hc_years = max(1, int(state.accum_years) + 1)
        _perm = _hc_rng.normal(loc=0.0, scale=_hc.permanent_sigma,
                               size=_hc_years) if _hc.permanent_sigma else None
        _tran = _hc_rng.normal(loc=0.0, scale=_hc.transitory_sigma,
                               size=_hc_years) if _hc.transitory_sigma else None
        _factors = []
        _level = 0.0
        for _k in range(_hc_years):
            if _perm is not None:
                _level += float(_perm[_k])
            _shock = _level + (float(_tran[_k]) if _tran is not None else 0.0)
            _factors.append(float(np.exp(_shock)))
        _hc_factors = tuple(_factors)

    _house = house_price or HousePriceProcess()
    _house_rng = None
    if _house.enabled and rng is not None:
        _h_entry = rng.bit_generator.state
        _h_root = (_h_entry.get("state", {}).get("state", 0)
                   if isinstance(_h_entry, dict) else 0)
        _house_rng = np.random.default_rng(
            (int(_h_root) + _house.seed_offset) % (2 ** 63))

    _blocky = blocky_spending or BlockySpendingParams()
    _blocky_rng = None
    if _blocky.enabled and _child_seed_root is not None:
        _blocky_rng = np.random.default_rng(
            (_child_seed_root + _blocky.seed_offset) % (2 ** 63))
    rule = rule or FixedRealRule()
    glide_path = glide_path or GLIDE_ALL_EQUITY
    bond_params = bond_params or DEFAULT_BOND_PARAMS
    medical = medical or DEFAULT_MEDICAL
    aca = aca or ACAParams()
    mortality = mortality or MORTALITY_MALE
    roth_ladder = roth_ladder or RothLadderParams()
    ss = ss or SocialSecurityParams()
    ftc = ftc or FTCParams()
    obbba = obbba or OBBBAParams()
    eldercare = eldercare or EldercareShockParams()
    inheritance = inheritance or InheritanceParams()
    sh_property = sh_property or ShanghaiPropertyParams()
    state = state or STATE
    fire_swr = fire_swr or state.swr_pref
    relocation = relocation or RelocationParams()
    rng = rng or np.random.default_rng()

    total_years = state.accum_years + state.retire_horizon

    if returns_x is not None and getattr(returns_x, "enabled", False):
        # E4 returns 2.0 — replaces BOTH the v7 sampler and the bond sampler
        # (blocks carries historical bonds; markov reuses the v9.3 sampler
        # inside). Guarded import keeps the off path free of the module.
        from fire_returns_x import sample_lifetime_x
        regime, all_equity_returns, all_bond_returns, all_inflations = \
            sample_lifetime_x(total_years, rng, config, bond_params,
                              returns_x, regimes=regimes)
    else:
        regime, all_equity_returns, all_inflations = sample_lifetime_v7(
            total_years, rng, config, regimes=regimes,
        )
        all_bond_returns = sample_bond_returns(all_equity_returns, bond_params, rng)

    income_streams = _anchor_non_cola_pensions(
        tuple(income_streams or ()), state, all_inflations)

    # Housing mortgage payments are fixed in nominal purchase-year units, so
    # resolve them only after the complete path CPI has been sampled. The
    # resolver emits one housing-owned positive event per age containing both
    # carrying cost and mortgage; refunds and unrelated user life events remain
    # separate in the existing channel, preserving shortfall semantics.
    resolved_housing_events = resolve_housing_mortgage_events(
        housing_mortgage, state.start_age, all_inflations)
    effective_life_events = list(life_events or ())

    # ---- the house's own price path, when this plan asked for one ----
    # Placed here because this is where per-path event resolution already
    # happens; the mortgage above resolves against this path's inflation for
    # the same structural reason. A lognormal real path with the module's own
    # drift, so switching it on leaves the MEAN where the deterministic curve
    # put it and only adds spread -- a plan that turns this on should see
    # uncertainty appear, not its central case move.
    _house_value_factor = None
    if _house_rng is not None:
        _n_house_years = max(1, len(all_inflations))
        # `loc` is the drift itself, not drift minus half the variance: that
        # correction centres the MEAN, and every number this model reports is
        # a percentile. Anchoring the MEDIAN is what makes "drift zero leaves
        # p50 where it was" exactly true rather than nearly true.
        _shocks = _house_rng.normal(loc=_house.drift_real,
                                    scale=_house.sigma_real,
                                    size=_n_house_years)
        _house_value_factor = tuple(
            float(np.exp(np.sum(_shocks[:k + 1]))) for k in range(_n_house_years))

        # A planned sale scales with the path rather than being a point.
        if _house.sale_age is not None and _house.sale_base_real:
            _sale_k = int(_house.sale_age) - int(state.start_age)
            if 0 <= _sale_k < _n_house_years:
                # The discount applies to the DRAWN value, not to the
                # typed one: what a seller loses to commission and repairs is
                # a share of what the place turned out to be worth.
                _drawn = (_house.sale_base_real * _house_value_factor[_sale_k]
                          * (1.0 - _house.liquidity_discount))
                # Negative is an inflow in this event convention.
                effective_life_events.append((int(_house.sale_age), -_drawn))

    if resolved_housing_events:
        effective_life_events.extend(resolved_housing_events)
        effective_life_events.sort(key=lambda item: (int(item[0]), float(item[1])))
    life_events = effective_life_events or None

    promo_year, bonus_pcts = sample_promotion_event(promo_params, rng)

    accum_returns = all_equity_returns[:state.accum_years]
    accum_inflations = all_inflations[:state.accum_years]
    _hh_accum = fire_v8_model._HOUSEHOLD
    _household_accum = (
        _hh_accum is not None and getattr(_hh_accum, "enabled", False)
    )
    _accum_mortality_schedule = None
    alive_by_year = None
    if _household_accum and mortality.enabled:
        _accum_mortality_schedule = (
            _sample_household_accum_mortality_schedule(
                rng, state.accum_years, state.start_age, mortality, _hh_accum,
            )
        )
        alive_by_year = _accum_mortality_schedule.alive_at_start

    # A13's wage factors are per PATH, so they are set here rather than in the
    # adapter's per-RUN layoff context. Same module-global idiom as `_LAYOFF`,
    # and restored in a finally so a raised path cannot leak its career into
    # the next one -- which would be a correlation nobody registered.
    _wage_prev = fire_v8_model._WAGE_FACTORS
    fire_v8_model._WAGE_FACTORS = _hc_factors
    try:
        accum_path = project_stratified_v93(
            accum_returns, accum_inflations,
            promo_year, bonus_pcts,
            initial, contrib_params, promo_params,
            tax_us, state, friction=config.friction_accum,
            obbba=obbba,
            alive_by_year=alive_by_year,
        )
    finally:
        fire_v8_model._WAGE_FACTORS = _wage_prev

    _ev_meta = {
        "underfunded_years": 0, "underfunded_ages": [],
        "funding_shortfall_nominal_by_age": {},
        "shortfall_nominal_by_age": {}, "shortfall_real_by_age": {},
        "out_real_by_age": {}, "in_real_by_age": {},
        "out_real": 0.0, "in_real": 0.0,
    }
    _ev_by_age = {}
    if life_events:
        for _a, _amt in life_events:
            _ev_by_age.setdefault(int(_a), []).append(float(_amt))

    _income_accum_meta = None
    _tx = tax_us if tax_us is not None else TAX_US
    if life_events and income_streams:
        (
            accum_path,
            _ev_meta,
            _income_accum_meta,
        ) = _apply_events_and_income_accum_v98(
            accum_path, _ev_by_age, income_streams, accum_returns, state,
            config.friction_accum, _tx.drag_taxable,
            _tx.withdrawal_tax_taxable,
            household_on=_household_accum,
            alive_by_year=alive_by_year,
        )
    elif life_events:
        accum_path, _ev_meta = _apply_life_events_accum_v98(
            accum_path, _ev_by_age, accum_returns, state,
            config.friction_accum, _tx.drag_taxable,
            _tx.withdrawal_tax_taxable,
        )
    elif income_streams:
        accum_path, _income_accum_meta = _apply_income_streams_accum_v98(
            accum_path, income_streams, accum_returns, state,
            config.friction_accum, _tx.drag_taxable,
            household_on=_household_accum,
            alive_by_year=alive_by_year,
        )

    fire_step = find_fire_crossing(accum_path, fire_swr)
    fire_age = fire_step['age'] if fire_step is not None else None
    fire_year_idx = ((fire_age - state.start_age) if fire_age is not None
                     else max(0, len(accum_path) - 1))

    death_in_accum = None
    primary_alive_accum = True
    spouse_alive_accum = True
    if _household_accum and mortality.enabled:
        (
            primary_alive_accum,
            spouse_alive_accum,
            death_in_accum,
        ) = _resume_household_accum_mortality(
            rng, _accum_mortality_schedule, fire_year_idx,
        )
    elif mortality.enabled:
        for i in range(fire_year_idx):
            age = state.start_age + i + 1
            q = annual_mortality_rate(age, mortality)
            if rng.random() < q:
                death_in_accum = age
                break

    if death_in_accum is not None:
        event_meta = _life_event_meta_through_age(_ev_meta, death_in_accum)
        result = {
            'regime': regime.name, 'died_during_accum': True,
            'age_at_death': death_in_accum, 'reached_fire': False,
            'lifetime_success': True, 'fire_age': None,
            'accum_path': accum_path, 'withdrawal': None,
            'promotion_year': promo_year,
            'censored_no_fire': False, 'censor_age': None,
            'accum_life_event_meta': event_meta,
        }
        if income_streams:
            result['accum_income_meta'] = _income_accum_meta_through_age(
                _income_accum_meta, death_in_accum)
        return result

    if fire_step is None:
        censor_age = int(accum_path[-1]['age'])
        event_meta = _life_event_meta_through_age(_ev_meta, censor_age)
        result = {
            'regime': regime.name, 'died_during_accum': False,
            'age_at_death': None, 'reached_fire': False,
            'lifetime_success': False, 'fire_age': None,
            'accum_path': accum_path, 'withdrawal': None,
            'promotion_year': promo_year,
            'censored_no_fire': True, 'censor_age': censor_age,
            'accum_life_event_meta': event_meta,
        }
        if income_streams:
            result['accum_income_meta'] = _income_accum_meta_through_age(
                _income_accum_meta, censor_age)
        return result

    cpi_cum_at_fire = fire_step['expenses'] / state.expenses_y0

    eldercare_events = sample_eldercare_events(
        rng, eldercare, fire_age, fire_age + state.retire_horizon,
    )
    inheritance_event = sample_inheritance(rng, inheritance)

    # Long-term care. Sampled here, before the withdrawal loop, on the same
    # shape as eldercare above — and on its OWN generator, so `rng` reaches the
    # loop having advanced by exactly as many draws as it did before this
    # module existed. Off takes this function no further than its first line.
    # The window opens at fire_age + 1, not fire_age: the withdrawal loop's
    # first modelled year is fire_age + 1, so an onset drawn at fire_age itself
    # would lose its first year of cost without saying so.
    ltc_events, ltc_meta = LTC.sample_ltc_events(
        ltc, fire_age + 1, fire_age + state.retire_horizon,
        anchor_age=state.start_age,
        calibration=(
            LTC.calibration_for(
                (lambda age: annual_mortality_rate(age, mortality))
                if mortality.enabled else (lambda age: 0.0),
                risk=float(ltc.lifetime_risk), cap_age=int(mortality.cap_age),
                onset_age=ltc.onset_age, onset_spread=ltc.onset_spread)
            if (ltc is not None and ltc.mode == LTC.STOCHASTIC
                and ltc.lifetime_risk > 0.0) else None),
    )

    # Parents. Sampled here for the same reasons as long-term care above — once,
    # before the withdrawal loop, on its OWN generator so `rng` reaches the loop
    # having advanced by exactly as many draws as it did before this module
    # existed. The window is the plan holder's retirement, expressed in their
    # ages; a parent who dies outside it is reported rather than clamped into it.
    # Each parent on their own table, resolved by their stated sex, with the
    # plan's own `enabled` and `cap_age` still governing: a plan that has
    # switched mortality off is not a plan where other people stop dying, but
    # it IS one where this engine models nobody dying, and the parents module
    # must not quietly reintroduce it.
    def _parent_death_rate(sex):
        if not mortality.enabled:
            return lambda age: 0.0
        # No fallback: the adapter refuses a sex this engine has no table for,
        # so reaching here with one would mean that check was removed. A quiet
        # default would run somebody's mother on the plan holder's lifespan.
        table = {"male": MORTALITY_MALE, "female": MORTALITY_FEMALE}[str(sex)]
        return lambda age: annual_mortality_rate(age, table)

    parent_care_events, parent_bequests, parents_meta = PARENTS.sample_parents(
        parents, _parent_death_rate,
        first_age=fire_age + 1, last_age=fire_age + state.retire_horizon,
        anchor_age=state.start_age, cap_age=int(mortality.cap_age))

    wd_equity = all_equity_returns[fire_year_idx:fire_year_idx + state.retire_horizon]
    wd_bond = all_bond_returns[fire_year_idx:fire_year_idx + state.retire_horizon]
    wd_inflations = all_inflations[fire_year_idx:fire_year_idx + state.retire_horizon]

    # Whichever accumulation overlay ran recorded the first-payment price
    # level for every non-COLA stream it paid. Handing it forward keeps ONE
    # anchor per stream across the FIRE boundary.
    _income_nominal_anchors = (_income_accum_meta or {}).get(
        "nominal_anchor_by_index") or {}
    wd_result = simulate_retirement_v98(
        starting_accounts=fire_step['accounts'],
        starting_age=fire_age,
        fire_year_cpi_cumulative=cpi_cum_at_fire,
        income_nominal_anchors=_income_nominal_anchors,
        equity_returns=wd_equity, bond_returns=wd_bond, inflations=wd_inflations,
        rule=rule, glide_path=glide_path,
        relocation=relocation, sh_property=sh_property,
        medical=medical, aca=aca, mortality=mortality,
        roth_ladder=roth_ladder, ss=ss, ftc=ftc,
        blocky_rng=_blocky_rng, blocky_spending=_blocky,
        ss_trust_fund=_sstf, ss_trust_fund_depletion_year=_sstf_year,
        eldercare_events=eldercare_events, inheritance_event=inheritance_event,
        # None when the module did not run, so the totals it returns can be
        # None too. An empty list would make "off" and "no care drawn" report
        # the same 0.0.
        ltc_events=(ltc_events if ltc_meta["mode"] != LTC.OFF else None),
        # None, not [], when the module is off: the retirement function reads
        # `is not None` to decide whether it measured anything at all.
        parent_care_events=(parent_care_events
                            if parents_meta["mode"] != PARENTS.OFF else None),
        parent_bequests=(parent_bequests
                         if parents_meta["mode"] != PARENTS.OFF else None),
        state=state, tax_us=tax_us, tax_cn=tax_cn,
        friction=config.friction_retire, rng=rng,
        china_healthcare=china_healthcare, ss_nra=ss_nra,
        life_events=([(a, amt) for (a, amt) in life_events if a > fire_age]
                     if life_events else None),
        income_streams=income_streams or None,
        tax_true=tax_true,
        primary_alive_at_start=primary_alive_accum,
        spouse_alive_at_start=spouse_alive_accum,
    )

    result = {
        'regime': regime.name,
        'died_during_accum': False,
        'reached_fire': True,
        'fire_age': fire_age,
        # Reported SEPARATELY from spendable wealth and never added into it.
        # The reason the house was excluded -- illiquid, you live in it -- has
        # not stopped being true because a plan asked to see its value, and a
        # figure folded into the portfolio would read as money that could have
        # been spent. `None` when unasked-for, so a reader can tell "this plan
        # did not model it" from "it modelled out at zero".
        'terminal_home_value_real': (
            float(_house.equity_base_real * _house_value_factor[-1])
            if (_house.include_in_net_worth and _house_value_factor
                and _house.equity_base_real) else None),
        'fire_balance': fire_step['total'],
        'fire_accounts': fire_step['accounts'].copy(),
        'fire_expenses': fire_step['expenses'],
        'lifetime_success': wd_result['lifetime_success'],
        'accum_path': accum_path,
        'withdrawal': wd_result,
        'relocation_age': relocation.relocation_age,
        'promotion_year': promo_year,
        'rule_name': rule.name,
        'glide_path_name': glide_path.name,
        'eldercare_events_sampled': eldercare_events,
        'inheritance_event_sampled': inheritance_event,
        # Both, always. The events alone cannot say whether an empty list means
        # "no care was drawn" or "the module never ran", and those read the same
        # in a report while meaning opposite things.
        'ltc_events_sampled': ltc_events,
        'ltc_meta': ltc_meta,
        'parent_care_events_sampled': parent_care_events,
        'parent_bequests_sampled': parent_bequests,
        'parents_meta': parents_meta,
        'censored_no_fire': False,
        'censor_age': None,
        'accum_life_event_meta': _life_event_meta_through_age(_ev_meta, fire_age),
    }
    if income_streams:
        result['accum_income_meta'] = _income_accum_meta_through_age(
            _income_accum_meta, fire_age)
    return result


def run_lifecycle_mc_v98(
    config: V7Config = None,
    n_paths: int = None,
    seed: int = None,
    per_path_substreams: bool = False,
    **kwargs,
) -> list:
    """Sequential shared stream (official protocol) or per-path substreams
    (CRN pairing, seed [seed, i]) — matching the v9.7 convention."""
    config = config or V7Config()
    n_paths = n_paths or config.n_paths
    seed = seed if seed is not None else config.seed
    if per_path_substreams:
        out = []
        for i in range(n_paths):
            rng = np.random.default_rng([seed, i])
            out.append(simulate_lifecycle_v98(config=config, rng=rng, **kwargs))
        return out
    rng = np.random.default_rng(seed)
    return [simulate_lifecycle_v98(config=config, rng=rng, **kwargs)
            for _ in range(n_paths)]
