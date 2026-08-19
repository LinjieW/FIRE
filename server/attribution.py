"""Phase 2 · realized variance attribution: Modified Dietz and the waterfall.

Implements ATTRIBUTION_ROBUSTNESS_PROTOCOL.md §3 (Modified Dietz and exact
timing) and §4 (the non-tautological realized waterfall). Nothing here is
inferred: every formula below is the one written in the protocol, and the
places the protocol refuses to answer -- an ill-conditioned rate, a flow on the
closing boundary, an unknown timing state -- are refusals here too rather than
best guesses.

Scope note. This is written under the user's 2026-08-02 override of the
protocol-first block: revision 8 has NOT been independently reviewed. This
module deliberately depends only on the parts of revision 8 that survived the
adversarial pass -- §1 estimands, §2 grain, §3 Modified Dietz, §4 waterfall --
and touches none of the power-gate, registry or CRN machinery, where the open
findings O1/O3/O4 sit.

The module is pure: it takes an opening value, a closing value, a ledger and a
forecast, and returns numbers. It does not read or write the archive, does not
know about SQLite, and holds no state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# The six categories from §2. Tax and fee stay separate from spending; the
# protocol is explicit that they are "never silently reclassified".
CATEGORIES = ("net_contribution", "income", "spending", "tax", "fee",
              "life_event")

#: §4's fixed display order. Callers render in this order; it is not a
#: presentation preference and may not be reordered by the UI.
WATERFALL_ORDER = ("market", "net_contribution", "income", "spending", "tax",
                   "fee", "life_event", "model_update", "residual")

#: §3 timing states. Only `exact` and `estimated_local_noon` can carry a weight.
TIMING_EXACT = "exact"
TIMING_LOCAL_NOON = "estimated_local_noon"
TIMING_UNKNOWN = "unknown"

_ILL_CONDITIONED = "unknown/ill_conditioned"


class LedgerError(ValueError):
    """A ledger line the protocol says must be rejected, not coerced."""


@dataclass(frozen=True)
class FlowLine:
    """One `flow_line_v2` row, reduced to what the waterfall needs.

    `amount` is in portfolio minor units, signed positive into the portfolio,
    matching `amount_portfolio_minor` in §2's durable row contract.
    """
    category: str
    amount: int
    occurred_at: Optional[datetime] = None
    timing_state: str = TIMING_EXACT

    def __post_init__(self):
        if self.category not in CATEGORIES:
            raise LedgerError("unknown category %r" % (self.category,))
        if self.timing_state not in (TIMING_EXACT, TIMING_LOCAL_NOON,
                                     TIMING_UNKNOWN):
            raise LedgerError("unknown timing_state %r" % (self.timing_state,))
        if self.timing_state != TIMING_UNKNOWN and self.occurred_at is None:
            raise LedgerError("a timed flow needs occurred_at")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            # §3: instants are timezone-aware. A naive datetime would silently
            # pick up the host timezone and move the weight.
            raise LedgerError("occurred_at must be timezone-aware")


@dataclass(frozen=True)
class Period:
    """The half-open normalized interval `[t0, t1)` from §3."""
    start: datetime
    end: datetime

    def __post_init__(self):
        if self.start.tzinfo is None or self.end.tzinfo is None:
            raise LedgerError("period bounds must be timezone-aware")
        if self.end <= self.start:
            raise LedgerError("period end must be after start")

    @property
    def seconds(self) -> float:
        """Actual elapsed seconds between UTC instants -- leap days count and a
        DST change does not become an extra investment year."""
        return (self.end.astimezone(timezone.utc)
                - self.start.astimezone(timezone.utc)).total_seconds()


def exposure_weight(line: FlowLine, period: Period) -> Optional[float]:
    """§3: `e_i = (t1 - s_i) / (t1 - t0)`.

    A flow exactly at `t0` has full remaining exposure (weight 1). A flow
    exactly at `t1` belongs to the next interval and is *rejected*, not given
    weight zero. A flow outside the interval is rejected rather than clipped.
    An `unknown` timing state has no weight at all.
    """
    if line.timing_state == TIMING_UNKNOWN:
        return None
    s = line.occurred_at.astimezone(timezone.utc)
    t0 = period.start.astimezone(timezone.utc)
    t1 = period.end.astimezone(timezone.utc)
    if s >= t1:
        raise LedgerError(
            "flow at or after the closing boundary belongs to the next "
            "interval; split it explicitly rather than clipping")
    if s < t0:
        raise LedgerError(
            "flow before the opening boundary is outside this interval; split "
            "it explicitly rather than clipping")
    return (t1 - s).total_seconds() / period.seconds


def modified_dietz(opening: float, closing: float, lines, period: Period):
    """§3's realized rate, or a refusal.

    `r_MD = (A1 - A0 - sum(X_actual)) / (A0 + sum(e_i * X_actual))`

    Returns `(rate, None)` or `(None, reason)`. The rate is a measurement; there
    is no fallback that fits it to make a memo balance.
    """
    total = 0.0
    weighted = 0.0
    for line in lines:
        total += line.amount
        weight = exposure_weight(line, period)
        if weight is None:
            return None, "unknown_timing_in_ledger"
        weighted += weight * line.amount
    denominator = opening + weighted
    if denominator <= 0 or abs(denominator) < 1e-9 * max(abs(opening), 1.0):
        return None, _ILL_CONDITIONED
    return (closing - opening - total) / denominator, None


@dataclass
class WaterfallResult:
    components: dict = field(default_factory=dict)
    r_md: Optional[float] = None
    r_new: Optional[float] = None
    y_actual: Optional[float] = 0.0
    residual: Optional[float] = 0.0
    #: `(F_no - F_oo) + R_actual` when `F_no` is unknown. Their sum is still
    #: observable -- it is `A1 - F_oo` minus the components, both ends of which
    #: are archived or measured -- but the split between them is not. Reporting
    #: the sum under its own name is not the residual absorbing the model
    #: update that §4 forbids: nothing is attributed to either line, and the
    #: two stay `None`.
    unsplit_update_and_residual: Optional[float] = None
    residual_over_opening: Optional[float] = None
    within_tolerance: bool = False
    tolerance: float = 0.0
    state: str = "complete"
    reasons: list = field(default_factory=list)

    def ordered(self):
        """The §4 display order, which the UI may not reorder."""
        return [(k, self.components.get(k)) for k in WATERFALL_ORDER]


def realized_waterfall(*, opening, closing, actual_lines, expected_lines,
                       period, r_new, f_oo, f_no,
                       currency_exponent=2):
    """§4's decomposition, with the residual computed rather than plugged.

    `r_new` is read from `F_new` and is never derived from `closing` -- the
    protocol calls that out explicitly, because a rate fitted from the closing
    value would make the market line absorb every flow error and the residual
    vanish by construction.
    """
    result = WaterfallResult()
    result.r_new = r_new
    # `F_no` is the archived old plan re-run under the current vintage. When
    # the engine build has moved and that re-run has not happened, §1.2 forbids
    # reconstructing it -- so the terms anchored on it are withheld rather than
    # substituted. The component lines do not depend on it and are still
    # reported: market-versus-behaviour separation, which is the thing the user
    # came for, survives a missing counterfactual.
    unknown_f_no = f_no is None
    # `Y_actual` is the deviation of the observed close from the NEW-vintage
    # forecast of the archived old plan -- not from the opening value. §1.2
    # states `A1 - F_oo = Y_actual + Y_update`, and substituting §4's residual
    # into §4's reconciliation gives the same thing: `Y_actual = A1 - F_no`.
    # An earlier draft here used `closing - opening`, which made the residual
    # equal the entire portfolio movement and left the waterfall meaningless.
    result.y_actual = None if unknown_f_no else closing - f_no
    if unknown_f_no:
        result.state = "incomplete"
        result.reasons.append(
            "F_no is unknown: the engine build has changed since this forecast "
            "was archived, and the archived plan has not been re-run under the "
            "current build. The model-update line and the residual cannot be "
            "separated without it.")

    r_md, reason = modified_dietz(opening, closing, actual_lines, period)
    result.r_md = r_md
    if r_md is None:
        result.state = "incomplete"
        result.reasons.append(reason)
        # §3: "no market conclusion is shown". Everything downstream of the
        # rate is withheld rather than computed from a substitute.
        return result

    # (1) Opening value x the actual-versus-new-forecast return contrast. It is
    # explicitly not allowed to absorb a flow.
    components = {"market": opening * (r_md - r_new)}

    # (2) Net contributions at the fixed half-period convention, regardless of
    # observed timestamp. The timestamp stays evidence for data quality.
    contrib_actual = sum(l.amount for l in actual_lines
                         if l.category == "net_contribution")
    contrib_expected = sum(l.amount for l in expected_lines
                           if l.category == "net_contribution")
    components["net_contribution"] = (contrib_actual * (1 + 0.5 * r_md)
                                      - contrib_expected * (1 + 0.5 * r_new))

    # (3)-(6) The timed categories, each at its own exposure weight.
    for category in ("income", "spending", "tax", "fee", "life_event"):
        total = 0.0
        for line in actual_lines:
            if line.category != category:
                continue
            weight = exposure_weight(line, period)
            total += line.amount * (1 + weight * r_md)
        for line in expected_lines:
            if line.category != category:
                continue
            weight = exposure_weight(line, period)
            total -= line.amount * (1 + weight * r_new)
        components[category] = total

    # (7) The model-update counterfactual, on archived old inputs. §4: it is
    # visible in the waterfall but is NOT added to the residual and NOT counted
    # twice, and it may not absorb a plan or state change.
    components["model_update"] = None if unknown_f_no else f_no - f_oo

    flow_and_market = sum(components[k] for k in
                          ("market", "net_contribution", "income", "spending",
                           "tax", "fee", "life_event"))
    if unknown_f_no:
        # `A1 - F_oo = components + (F_no - F_oo) + R_actual` is §4's own
        # identity, and its left side is known: `A1` was observed and `F_oo` is
        # archived. So the SUM of the two withheld terms is known even though
        # neither is. Saying so is more useful than reporting nothing, and it
        # is not the absorption §4 forbids -- both lines stay `None` and no
        # value is attributed to either.
        result.unsplit_update_and_residual = (closing - f_oo) - flow_and_market
        result.residual = None
        components["residual"] = None
        result.components = components
        result.residual_over_opening = None
        return result

    result.residual = result.y_actual - flow_and_market
    components["residual"] = result.residual
    result.components = components

    # §4's continuous tolerance -- no $1,000 discontinuity.
    minor_unit = 10.0 ** (-currency_exponent)
    result.tolerance = max(10 * minor_unit, 0.10 * abs(result.y_actual))
    result.within_tolerance = abs(result.residual) <= result.tolerance
    result.residual_over_opening = result.residual / max(abs(opening), 1.0)
    return result


def reconciles(result: WaterfallResult, *, closing, f_oo) -> bool:
    """§4's explicit old-snapshot identity.

    `A1 - F_oo = (M + V_contribution + V_income + V_spending + V_tax + V_fee +
                  V_events) + (F_no - F_oo) + R_actual`

    An identity that holds by construction proves little on its own, which is
    why the tests check component magnitudes and signs independently. It is
    still worth asserting: a future refactor that folds the model-update item
    into the residual, or lets the market line absorb a flow, breaks it.
    """
    if result.state != "complete":
        return False
    total = sum(result.components[k] for k in
                ("market", "net_contribution", "income", "spending", "tax",
                 "fee", "life_event"))
    rhs = total + result.components["model_update"] + result.residual
    return abs((closing - f_oo) - rhs) <= 1e-6 * max(abs(closing - f_oo), 1.0)


# ---------------------------------------------------------------------------
# §2 grain: raw transaction_line_v2 -> derived flow_line_v2.
#
# The derived rows are NOT persisted. §2 is explicit that the union join and
# aggregation "are recomputed on read", so only the immutable raw ledger is
# durable and everything below is a function of it. Keeping the derivation in
# one place also means the condition-2 tests bind this code rather than a
# second copy of the same rules living in the test module.
# ---------------------------------------------------------------------------

#: Raw rows group by this. `source_event_id` is in it because per-event leg
#: reconciliation must survive grouping; `active_tip` is not, because the row
#: contract calls it derived and a derived attribute cannot key its own group.
FLOW_GRAIN_V2 = ("category", "source_or_schedule_id", "source_event_id",
                 "component_leg_id", "period_start", "period_end",
                 "timing_bucket")

#: Expected unions with actual on this: the grain minus `source_event_id`,
#: because the expected side comes from a schedule and has no source events.
BRIDGE_JOIN_V2 = ("category", "source_or_schedule_id", "period_start",
                  "period_end", "timing_bucket", "component_leg_id")

#: A nullable field inside a key would let two unrelated rows collide on null.
ABSENT = " absent"


class SentinelCollision(LedgerError):
    """A real ID spelled exactly like the absent sentinel."""


def grain_key(row, fields=FLOW_GRAIN_V2):
    """The canonical key tuple for one raw row, with absence made total."""
    out = []
    for name in fields:
        value = row.get(name)
        if value == ABSENT:
            raise SentinelCollision(
                "%s is literally the absent sentinel %r" % (name, ABSENT))
        out.append(ABSENT if value is None else value)
    return tuple(out)


def membership_sha256(transaction_ids):
    import hashlib as _h
    import json as _j
    blob = _j.dumps(sorted(transaction_ids), ensure_ascii=False,
                    sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _h.sha256(blob).hexdigest()


def derive_flow_lines(raw_rows):
    """Group immutable raw rows into derived flow rows, deterministically.

    Order-independent by construction: rows are keyed, members sorted by
    `transaction_id`, output sorted by key. Corrections stay inside their group
    and move the tip; every superseded row is retained.
    """
    groups = {}
    for row in raw_rows:
        groups.setdefault(grain_key(row), []).append(row)

    out = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda r: r["transaction_id"])
        superseded = {m.get("supersedes_transaction_id") for m in members
                      if m.get("supersedes_transaction_id")}
        live = [m for m in members if m["transaction_id"] not in superseded]
        if len(live) != 1:
            raise LedgerError(
                "group %r has %d live tips; a correction lineage must leave "
                "exactly one" % (key, len(live)))
        tip = live[0]
        ids = sorted(m["transaction_id"] for m in members)
        fields = dict(zip(FLOW_GRAIN_V2, key))
        out.append({
            "key": key,
            "category": fields["category"],
            "active_tip": tip["transaction_id"],
            "amount_portfolio_minor": tip["amount_portfolio_minor"],
            "is_internal_transfer": bool(tip.get("is_internal_transfer")),
            "transfer_group_id": tip.get("transfer_group_id"),
            "absence_proof_id": tip.get("absence_proof_id"),
            "occurred_at": tip.get("occurred_at"),
            "timing_state": tip.get("timing_state", TIMING_EXACT),
            "raw_transaction_ids": ids,
            "join_membership_sha256": membership_sha256(ids),
        })
    return out


def transfer_group_states(flow_rows):
    """Internal transfer groups must sum to zero in portfolio minor units."""
    sums = {}
    for row in flow_rows:
        if row["is_internal_transfer"]:
            gid = row["transfer_group_id"]
            sums[gid] = sums.get(gid, 0) + row["amount_portfolio_minor"]
    return {gid: ("balanced" if total == 0 else "unbalanced")
            for gid, total in sums.items()}


def to_flow_lines(derived_rows, period):
    """Project derived rows onto the `FlowLine` objects the waterfall consumes.

    Internal transfers are dropped: §2 excludes both legs from external
    category totals.
    """
    lines = []
    for row in derived_rows:
        if row["is_internal_transfer"]:
            continue
        lines.append(FlowLine(
            category=row["category"],
            amount=row["amount_portfolio_minor"],
            occurred_at=row.get("occurred_at"),
            timing_state=row.get("timing_state", TIMING_EXACT),
        ))
    return lines
