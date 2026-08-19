"""Phase 3 · the adverse assumption packs `Robust` is defined against.

ROADMAP names seven families of assumption a plan can be wrong about: expected
return / volatility / correlation / valuation reversion, inflation, tax and rule
changes, a Social Security haircut, healthcare and long-term care, an income
interruption, and relocation FX / PPP. This module is that list, expressed as
`AssumptionPack`s against config leaves that exist.

Three things it is careful about.

**Every path here was read out of `default_config()` before it was written
down.** A sibling module once shipped `state.retire_age` and
`returns.equity_mean_shift`; neither exists, and a pack that writes a leaf the
engine never reads produces a run identical to the baseline -- which arrives as
"this assumption makes no difference", the most confident possible way to be
wrong. `returns.equity_mu_shift` is the one path here the engine reads without
`default_config()` setting it, and it is allow-listed in
`ensemble.ENGINE_READ_UNSET_LEAVES` with the line that reads it.

**A pack whose subsystem is switched off is skipped, not run.** `layoff`,
`relocation`, `tax_true` and `eldercare` are all off in a default plan.
Perturbing a subsystem that never executes gives an effect of exactly zero, and
zero prints as "tested, no impact". For a plan that is not relocating, FX risk
genuinely does not apply -- but "does not apply" and "we checked and it was
fine" are different sentences and must not arrive as the same number. Hence
`requires`, checked before the run.

**Where the engine cannot express a risk, the pack says so instead of pretending
it can.** There is no valuation state in this engine -- no CAPE, no
mean-reversion term -- so "valuation reversion" cannot be modelled as a
path-dependent snapback. It is approximated by a lower central mean, and the
rationale says that is what it is.
"""
from __future__ import annotations

from ensemble import AssumptionPack, LeafCondition, missing_leaves

#: ROADMAP's seven families, in its order. `select_packs` groups by these so a
#: caller can report which families a study actually reached.
FAMILIES = (
    "return_expectations",
    "inflation",
    "tax_and_rules",
    "social_security",
    "healthcare_and_ltc",
    # Added with the parent lifecycle module. A family of its own rather than a
    # third care pack, because what can be wrong here is not a health
    # assumption: it is money expected from somebody else's death. Note the
    # consequence -- `families_missing` now has eight slots, so a plan that
    # models no parents reports one more uncovered family than it used to, the
    # same way a plan with no relocation already reports `relocation`.
    "parent_lifecycle",
    "income_interruption",
    "relocation",
)

#: family -> packs. Values are absolute replacements, not deltas: `apply` sets
#: the leaf, so a pack states the world it describes rather than an increment
#: that would compose differently depending on what the plan already held.
_LIBRARY = {
    "return_expectations": [
        AssumptionPack(
            "equity_return_lower",
            {"returns.equity_mu_shift": -0.02},
            "Equity returns run 2 percentage points below the regime mixture's "
            "central assumption for the whole horizon. The shift moves the "
            "entire mixture's mean (engine_adapter.py:571-574), so it is a posture "
            "change rather than a single bad decade.",
        ),
        AssumptionPack(
            "bond_regime_worse",
            {"bonds.mean": 0.02, "bonds.sigma": 0.08},
            "Bonds return less and swing more than the plan assumes -- the "
            "2022 shape, where the ballast moved with the risk asset instead "
            "of against it. Measured: against a glide holding bonds this moves "
            "the median terminal balance by roughly -460k; against the default "
            "All-equity glide it moves nothing, because there are no bonds.",
            requires={"glide.equity_end": LeafCondition(
                lambda v: isinstance(v, (int, float)) and v < 1.0,
                "below 1.0 -- the glide has to actually hold bonds, and the "
                "default All-equity glide holds none at either end")},
        ),
        AssumptionPack(
            "correlations_break",
            {"bonds.correlation_with_equity": 0.65,
             "returns.inflation_equity_corr": 0.2},
            "Diversification stops working when it is needed: bonds move with "
            "equities, and equities stop hedging inflation. Both defaults are "
            "helpful to the plan (0.15 and -0.3); this asks what a bad "
            "correlation regime costs.",
        ),
        AssumptionPack(
            "valuation_reversion",
            {"returns.equity_mu_shift": -0.035},
            "Today's valuations revert, taking a third of a percentage point "
            "more per year than `equity_return_lower`. NOTE: this engine has "
            "no valuation state -- no CAPE, no mean-reversion term -- so this "
            "cannot be a path-dependent snapback and is not modelled as one. "
            "It is a permanently lower central mean, which understates the "
            "early-sequence damage a real reversion would do.",
        ),
    ],
    "inflation": [
        AssumptionPack(
            "inflation_higher",
            {"returns.inflation_mu": 0.045, "returns.inflation_sigma": 0.03},
            "Inflation settles 1.5 points above the plan's 3% assumption and is "
            "half again as volatile. This bites twice: spending is indexed to "
            "it, and `returns.inflation_equity_corr` is negative, so the bad "
            "inflation draws land with the bad equity draws.",
        ),
    ],
    "tax_and_rules": [
        AssumptionPack(
            "tax_rules_tighten",
            {"tax_true.state_rate": 0.05,
             "tax_true.taxable_gain_fraction": 0.8},
            "A state income tax appears and a larger share of each taxable "
            "withdrawal is gain rather than basis -- the two rule changes that "
            "need no new law to reach a retiree, one legislative and one just "
            "the arithmetic of a long-held position.",
            requires={"tax_true.enabled": True},
        ),
    ],
    "social_security": [
        AssumptionPack(
            "social_security_haircut",
            {"ss_nra.haircut_fraction": 0.5},
            "Benefits are cut by half rather than the plan's 20%. The leaf "
            "already exists because a haircut is already assumed; this asks "
            "what a deeper one does. The engine applies it only during "
            "`in_china` years (fire_v9_6_model.py:496), so it is a "
            "relocation-scenario effect: measured at roughly -120k on the "
            "median terminal balance there, and exactly zero in `home`, which "
            "is the never-relocate path.",
            requires={"social_security.enabled": True,
                      "relocation.enabled": True,
                      "relocation.relocation_age": LeafCondition(
                          lambda v: v is not None,
                          "set to an age -- `None` means stay in the US "
                          "permanently, so no in-China haircut ever applies")},
            scenario="relocation",
        ),
    ],
    "healthcare_and_ltc": [
        AssumptionPack(
            "healthcare_inflation",
            {"medical.cpi_delta_premium": 0.045, "medical.cpi_delta_oop": 0.03,
             "medical.oop_y0": 2500.0},
            "Medical costs keep outrunning general inflation by more than the "
            "plan's 2 points on premiums, and out-of-pocket starts higher. It "
            "applies only when the opt-in annual medical trajectory consumes "
            "those yearly components; otherwise the pack is skipped rather "
            "than reporting a dead-knob zero.",
            requires={"medical.annual_trajectory_enabled": True},
        ),
        AssumptionPack(
            "long_term_care_shock",
            {"eldercare.annual_prob": 0.04, "eldercare.severity_log_mean": 11.8},
            "Long-term care is more likely each year and more expensive when "
            "it lands. Only meaningful once the eldercare shock is switched "
            "on; on a plan with `mode = off` this is skipped rather than run "
            "to a zero. NOTE the scope, which its name predates: it perturbs "
            "the ELDERCARE shock -- paying for a parent -- and does not reach "
            "the `ltc` module added in 4.0 Phase 2, which is the user's own "
            "care. A plan with `ltc.mode` on and `eldercare.mode` off has no "
            "pack in this family reaching its care assumptions, and this "
            "family reporting itself covered would say otherwise.",
            requires={"eldercare.mode": ("stochastic", "scenario")},
        ),
        # --- the user's OWN care (4.0 Phase 2's `ltc` module) ---------
        # Three separate packs rather than one, because they are three
        # different ways to be wrong and a plan can be robust to one and not
        # another. Bundling them would report a single effect that no single
        # assumption produced.
        #
        # MEASURED: these need Standard precision, and Standard is enough.
        #
        # The question a previous slice left open was whether a care assumption
        # separates from sampling at all. It does, and the crossover is between
        # 3,000 and 10,000 paths. Terminal p50, default plan with
        # `expenses_y0 * 1.9` and `ltc.mode = stochastic`, noise measured as the
        # seed-to-seed range over five seeds:
        #
        #     paths    noise      own_care_more_expensive    separable
        #      1,200   456,882           -100,444               no
        #      3,000   184,175           -142,219               no
        #     10,000    67,593           -129,456              YES
        #     30,000    48,967           -138,700              YES
        #
        # The noise shrinks as 1/sqrt(N) while the effect holds near -135k, so
        # the two cross once and stay crossed. Standard precision is 10,000
        # paths and a formal DecisionPacket already refuses anything less, so
        # care packs are usable exactly where packets are.
        #
        # What this does NOT license: reading these effects at Quick precision.
        # At 1,200 paths the largest of them is a quarter of the noise, and
        # `own_care_starts_earlier` even reads as an improvement there — the
        # tell that a number is one draw rather than a finding.
        AssumptionPack(
            "own_care_more_expensive",
            {"ltc.cost_nursing_home": 165_000.0,
             "ltc.cost_assisted_living": 92_000.0,
             "ltc.cost_home_care": 88_000.0,
             "ltc.cost_excess_inflation": 0.02},
            "Care costs about 40% more than the plan assumes and keeps "
            "outrunning general inflation by two points instead of one. Cost "
            "is the assumption most sensitive to geography -- a high-cost "
            "metro can be double the national figure the module defaults to -- "
            "and it is the one a user is least likely to have checked.",
            requires={"ltc.mode": ("stochastic", "scenario")},
        ),
        AssumptionPack(
            "own_care_starts_earlier",
            {"ltc.onset_age": 72.0, "ltc.scenario_onset_age": 72},
            "Care begins at 72 rather than 83 -- early-onset conditions, which "
            "are the version of this risk that actually breaks a plan, because "
            "the money leaves a portfolio that has had eleven fewer years to "
            "compound and it leaves inside the sequence-risk window rather "
            "than after it. First written as 78, which the library's own reach "
            "guard rejected: at 60 paths the shift touched nobody, because too "
            "few paths both live to that window and enter care. That is a real "
            "property of the risk rather than a wiring fault -- and an adverse "
            "pack that only bites in samples nobody runs is not adverse.",
            requires={"ltc.mode": ("stochastic", "scenario")},
        ),
        AssumptionPack(
            "own_care_lasts_longer",
            {"ltc.lifetime_risk": 0.70,
             "ltc.mix_nursing_home": 0.40, "ltc.mix_assisted_living": 0.30,
             "ltc.mix_home_care": 0.30,
             "ltc.scenario_years": 8.0},
            "More people enter care, and more of them enter at the expensive "
            "end. `lifetime_risk` is set explicitly here, which overrides the "
            "sex-derived default (0.0 means 'derive from sex'; a non-zero "
            "value wins). The mix still sums to 1.0 -- the engine refuses one "
            "that does not, so a pack that drifted would fail loudly rather "
            "than silently renormalise.",
            requires={"ltc.mode": ("stochastic", "scenario")},
        ),
    ],
    "parent_lifecycle": [
        # One pack, not the two that were drafted, and the second one's
        # measurement is why.
        #
        # `parents.cost_excess_inflation` 0.01 -> 0.02 was the obvious second
        # adverse assumption: care costs outrunning inflation twice as fast.
        # Measured against this engine's own noise floor -- the spread of the
        # baseline across four seeds -- it does not survive: effect -267 on a
        # floor of 1,810 at 1,200 paths, and -379 on a floor of 1,088 at
        # 10,000. Below the floor at both precisions, so shipping it would
        # report a number indistinguishable from a draw, which is the failure
        # three of eleven packs once had in the other direction. It is left out
        # rather than left in with a caveat.
        #
        # A third candidate, `parents.estate_share_of_care`, is not adverse in
        # either direction and never could be: the plan's net position is
        # `estate - care` at every setting of that dial, so it moves money
        # between two ledgers without changing the total. See its own comment
        # in `engine/parents_model.py`.
        AssumptionPack(
            "inheritance_never_arrives",
            {"parents.assume_zero_bequest": True},
            "The money you expect to inherit does not reach you -- spent late, "
            "left to somebody else, or consumed by care in a place this model "
            "does not cover. Adverse and measurable: median real spending "
            "falls about 8% at 1,200 paths and 10% at 10,000, against noise "
            "floors of 1,810 and 1,088, so this is an effect rather than a "
            "draw. Worth running because of how the loss shows up -- the "
            "success rate does not move at all, since the withdrawal rule "
            "absorbs it by cutting spending, so a plan that leans on an "
            "inheritance looks untouched on the headline number and is not.",
            requires={"parents.mode": ("stochastic", "scenario")},
        ),
    ],
    "income_interruption": [
        AssumptionPack(
            "income_interruption",
            {"layoff.p_annual": 0.08, "layoff.gap_months": 9.0,
             "layoff.bad_year_multiplier": 4.0},
            "Job loss is three times more likely per year, the gap is nine "
            "months instead of four, and it clusters harder into bad market "
            "years -- which is when it actually happens.",
            requires={"layoff.enabled": True},
        ),
    ],
    "relocation": [
        AssumptionPack(
            "relocation_fx_adverse",
            {"relocation.fx_sigma": 0.12, "relocation.fx_drift": -0.02,
             "relocation.ppp_kappa": 0.4},
            "The currency the spending is in moves against the currency the "
            "assets are in: twice the volatility, a 2%/yr adverse drift, and "
            "enough PPP pull to keep local costs from following the exchange "
            "rate down. Measured at roughly -6.7k on the median terminal "
            "balance in the relocation scenario; `home` never moves, because "
            "`home` is the path where the move does not happen.",
            requires={"relocation.enabled": True,
                      "relocation.relocation_age": LeafCondition(
                          lambda v: v is not None,
                          "set to an age -- with `relocation_age = None` the "
                          "plan never relocates and every FX assumption is "
                          "inert no matter what `enabled` says")},
            scenario="relocation",
        ),
    ],
}

#: Flat, in family order.
ALL_PACKS = tuple(pack for family in FAMILIES for pack in _LIBRARY[family])

_FAMILY_OF = {pack.name: family
              for family in FAMILIES for pack in _LIBRARY[family]}


def pack_by_name(name: str) -> AssumptionPack:
    for pack in ALL_PACKS:
        if pack.name == name:
            return pack
    raise KeyError("no assumption pack named %r; the library holds %s"
                   % (name, ", ".join(p.name for p in ALL_PACKS)))


def family_of(name: str) -> str:
    return _FAMILY_OF[name]


def select_packs(config: dict, packs=None) -> dict:
    """Split the library into what this plan can be tested against and what it
    cannot, with a reason for each exclusion.

    The `skipped` list is the point of this function. A study that silently
    dropped the FX pack would report robustness across six families while
    claiming seven, and the caller has no way to tell from the verdict alone.
    """
    candidates = tuple(packs if packs is not None else ALL_PACKS)
    applicable, skipped = [], []
    for pack in candidates:
        gone = missing_leaves(pack, config)
        if gone:
            skipped.append({"pack": pack.name, "family": _FAMILY_OF.get(pack.name),
                            "reason": "%s is not a leaf this engine reads, so "
                                      "the pack would change nothing"
                                      % ", ".join(gone)})
            continue
        # Trial-apply, and throw the result away. Selection has to be total:
        # anything `apply` would refuse later is refused HERE, while the caller
        # is still deciding whether to spend the runs. `correlations_break`
        # shipped raising inside a running study instead — after the cost was
        # quoted, after the user pressed Run — because nothing on the quoting
        # path ever called `apply`, so the quote could not tell the difference
        # between a study that would start and one that would not.
        try:
            pack.apply(config)
        except Exception as exc:                        # noqa: BLE001
            skipped.append({"pack": pack.name, "family": _FAMILY_OF.get(pack.name),
                            "reason": str(exc)})
            continue
        ok, why = pack.applicable(config)
        if ok:
            applicable.append(pack)
        else:
            skipped.append({"pack": pack.name, "family": _FAMILY_OF.get(pack.name),
                            "reason": why})
    covered = sorted({_FAMILY_OF[p.name] for p in applicable
                      if p.name in _FAMILY_OF})
    return {
        "applicable": applicable,
        "skipped": skipped,
        "families_covered": covered,
        "families_missing": [f for f in FAMILIES if f not in covered],
        # Sent alongside, because the page used to subtract from a hardcoded 7.
        # That number did not track this tuple, so adding the eighth family
        # made the UI report one fewer covered family than it had -- an error
        # in the direction that understates how much was checked.
        "families_total": len(FAMILIES),
    }
