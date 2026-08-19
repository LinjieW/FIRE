"""4.0 Phase 2 · guaranteed income: SPIA and a TIPS ladder, as cash flows.

ROADMAP calls this "mainly a compilation layer", and that is right: the engine
already carries `IncomeStreamSpec` with a start age, an end age, a COLA flag
and an owner. What this module adds is not new cash machinery but three things
the existing streams do not have to think about.

**The premium is an outflow, and it is the whole point.** Buying an annuity
converts a lump sum into a stream. A compiler that emitted only the stream
would make every plan that touches this module look better, for free — the same
shape as a bequest arriving with no cost attached, except worse, because that
one at least is real money arriving. So a purchase compiles to BOTH: a
one-time outflow in the purchase year and the income that follows.

**The quote is the user's, never ours.** The user states the premium and the
payout they were actually offered. This module does not derive a payout from a
mortality table and an interest rate, because doing that would be inventing a
quote and printing it as though a company had made it. ROADMAP puts this as
"no product data in the repository" and backs it with a test asserting no quote
table exists; `test_guaranteed_income.py` holds that.

**Three different reasons a stream stops.** A single-life annuity stops when
the buyer dies. A joint-life annuity runs to the second death. A TIPS ladder
runs for its stated years and does not care whether anyone is alive. Those are
not one `end_age` with different numbers in it, and collapsing them would make
a ladder pay a dead person or an annuity outlive its owner.

What this module deliberately does NOT model, all of it disclosed rather than
approximated: insurer credit risk and state guaranty limits, surrender or
commutation, period-certain riders, deferred (QLAC) tax treatment, and the
inflation basis risk between a stated COLA cap and realised inflation. Each is
a real thing that a real annuity contract says, which is why they belong to the
user's own contract and not to a default in here.
"""
from __future__ import annotations

from dataclasses import dataclass, field

#: How a stream ends. Kept as words rather than as an `end_age` convention,
#: because the three are genuinely different questions and a number cannot say
#: which one is being asked.
UNTIL_DEATH = "until_death"            #: single life
UNTIL_SECOND_DEATH = "until_second_death"   #: joint life
FOR_A_TERM = "for_a_term"              #: a ladder, alive or not
TERMINATIONS = (UNTIL_DEATH, UNTIL_SECOND_DEATH, FOR_A_TERM)


class GuaranteedIncomeError(ValueError):
    """A guaranteed-income configuration this module refuses rather than
    models badly."""


@dataclass
class Annuity:
    """One SPIA, exactly as the user was quoted it.

    `annual_payout_real` is what the contract pays in TODAY's money if
    `cola` is true, and in the purchase year's money if it is false — the
    difference between those two is most of what an annuity decision is about,
    so it is a flag rather than something inferred.
    """

    label: str = "annuity"
    premium: float = 0.0
    annual_payout_real: float = 0.0
    purchase_age: int = 65
    #: When income starts. Later than `purchase_age` makes it deferred; the
    #: gap is unpaid, which is the trade a deferred annuity is.
    start_age: int = 65
    cola: bool = False
    termination: str = UNTIL_DEATH
    owner: str = "unspecified"


@dataclass
class TipsLadder:
    """A ladder of inflation-linked bonds held to maturity.

    Modelled as a term-certain real income stream because that is what holding
    a TIPS to maturity is. It is NOT an annuity: it stops on its own schedule
    whether or not the holder is alive, and it has no mortality credit — which
    is exactly the comparison the decision packet exists to make.
    """

    label: str = "tips_ladder"
    #: Total cost today of buying the rungs.
    cost: float = 0.0
    annual_real: float = 0.0
    start_age: int = 65
    years: int = 10
    owner: str = "unspecified"


@dataclass
class GuaranteedIncomeParams:
    mode: str = "off"
    annuities: list = field(default_factory=list)
    ladders: list = field(default_factory=list)


OFF = "off"
ON = "on"
MODES = (OFF, ON)


def _positive(value: float, what: str) -> float:
    number = float(value)
    if number <= 0:
        raise GuaranteedIncomeError(
            "%s must be positive, got %r — a guaranteed-income instrument with "
            "no cost or no payout is not a cheaper version of one, it is a "
            "line that does nothing and reports an effect of exactly zero"
            % (what, value))
    return number


def compile_annuity(annuity: Annuity, *, horizon_end_age: int) -> dict:
    """`{"premium_event", "stream", "notes"}` for one SPIA.

    `premium_event` is `(age, amount)` with a POSITIVE amount, matching the
    life-event channel's convention that positive is an outflow. `stream` is
    the kwargs for an `IncomeStreamSpec`; the adapter builds the object, so
    this module stays free of the engine's dataclasses and can be tested
    without importing the engine chain.
    """
    if annuity.termination not in TERMINATIONS:
        raise GuaranteedIncomeError(
            "unknown termination %r; this module knows %s"
            % (annuity.termination, ", ".join(TERMINATIONS)))
    premium = _positive(annuity.premium, "an annuity premium")
    payout = _positive(annuity.annual_payout_real, "an annuity payout")
    if annuity.start_age < annuity.purchase_age:
        raise GuaranteedIncomeError(
            "income cannot start at %d when the annuity is bought at %d"
            % (annuity.start_age, annuity.purchase_age))

    notes = []
    deferral = int(annuity.start_age) - int(annuity.purchase_age)
    if deferral:
        notes.append("deferred %d years: the premium leaves at %d and nothing "
                     "is paid until %d" % (deferral, annuity.purchase_age,
                                           annuity.start_age))
    if not annuity.cola:
        notes.append("no COLA: the payout is fixed in nominal terms, so its "
                     "purchasing power falls every year — this is the single "
                     "biggest difference between two quotes that look alike")
    if annuity.start_age > horizon_end_age:
        notes.append("income starts at %d, past the end of the modelled "
                     "horizon (%d), so the premium is charged and NOTHING is "
                     "received inside this simulation"
                     % (annuity.start_age, horizon_end_age))

    return {
        "premium_event": (int(annuity.purchase_age), premium),
        "stream": {"kind": "annuity", "annual_real": payout,
                   "start_age": int(annuity.start_age),
                   "cola": bool(annuity.cola),
                   "owner": annuity.owner},
        "termination": annuity.termination,
        "notes": notes,
    }


def compile_ladder(ladder: TipsLadder, *, horizon_end_age: int) -> dict:
    """The same shape for a TIPS ladder, which ends on its own schedule."""
    cost = _positive(ladder.cost, "a TIPS ladder's cost")
    annual = _positive(ladder.annual_real, "a TIPS ladder's annual income")
    years = int(ladder.years)
    if years <= 0:
        raise GuaranteedIncomeError("a ladder must run for at least one year")

    notes = []
    end_age = int(ladder.start_age) + years - 1
    if end_age > horizon_end_age:
        notes.append("the ladder runs to %d, past the modelled horizon (%d); "
                     "the years beyond it are bought and not counted"
                     % (end_age, horizon_end_age))
    # The arithmetic a user should see rather than be asked to trust: a ladder
    # returns its own money back, so "income" here is not a yield.
    total = annual * years
    if total < cost:
        notes.append("the rungs pay back %s of a %s cost over %d years, which "
                     "is a negative real return — check the figures against "
                     "your own quote" % (_money(total), _money(cost), years))

    return {
        "premium_event": (int(ladder.start_age), cost),
        "stream": {"kind": "tips_ladder", "annual_real": annual,
                   "start_age": int(ladder.start_age),
                   "end_age": end_age, "cola": True,
                   "owner": ladder.owner},
        "termination": FOR_A_TERM,
        "notes": notes,
    }


def _money(value: float) -> str:
    return "$%s" % format(int(round(value)), ",d")


def compile_all(params: GuaranteedIncomeParams, *,
                horizon_end_age: int) -> dict:
    """Everything the adapter needs, or nothing at all when the module is off.

    OFF returns empty lists AND a reason, on the same principle as the other
    opt-in modules: a caller must be able to tell "no guaranteed income was
    modelled" from "guaranteed income was modelled and came to nothing".
    """
    meta = {"mode": OFF, "instruments": [],
            "total_premium": None, "reason": "the guaranteed-income module is off"}
    if params is None or params.mode == OFF:
        return {"premium_events": [], "streams": [], "meta": meta}
    if params.mode not in MODES:
        raise GuaranteedIncomeError(
            "unknown guaranteed-income mode %r; this module knows %s"
            % (params.mode, ", ".join(MODES)))
    if not params.annuities and not params.ladders:
        raise GuaranteedIncomeError(
            "the guaranteed-income module is on but lists no annuity and no "
            "ladder, so it would report a premium of zero and income of zero — "
            "neither of which was measured. Add an instrument, or switch the "
            "module off.")

    premium_events, streams, rows = [], [], []
    for annuity in params.annuities:
        built = compile_annuity(annuity, horizon_end_age=horizon_end_age)
        premium_events.append(built["premium_event"])
        streams.append(built["stream"])
        rows.append({"label": annuity.label, "kind": "annuity",
                     "premium": built["premium_event"][1],
                     "termination": built["termination"],
                     "notes": built["notes"]})
    for ladder in params.ladders:
        built = compile_ladder(ladder, horizon_end_age=horizon_end_age)
        premium_events.append(built["premium_event"])
        streams.append(built["stream"])
        rows.append({"label": ladder.label, "kind": "tips_ladder",
                     "premium": built["premium_event"][1],
                     "termination": built["termination"],
                     "notes": built["notes"]})

    meta = {"mode": ON, "instruments": rows,
            "total_premium": sum(amount for _, amount in premium_events),
            "reason": "each instrument charges its premium in the purchase "
                      "year and pays on its own schedule"}
    return {"premium_events": premium_events, "streams": streams, "meta": meta}
