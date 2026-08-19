"""Guided plan updates for the five transitions, proposing and never applying.

ROADMAP 4.0 Phase 4's flagship: widowhood, divorce, a disability
determination, an inheritance received, remarriage. Each type carries the list
of fields that must change, walked item by item, and the acceptance criterion
is one sentence: **every step is a proposal the user confirms, and no step
edits a number by itself.** ROADMAP asks that this be pinned "as assertions on
the module's public surface", the way 3.0 pinned `guardrails` never modifying
a plan.

So the surface is deliberately two functions that cannot be confused:

  `propose(kind, cfg)` reads and returns a checklist. It has no way to write.
  `apply_confirmed(cfg, kind, confirmed)` returns a NEW config carrying only
  the paths the caller named, and reports everything it did not touch.

A path the user did not confirm cannot be applied even if it was proposed, and
a path that was never proposed cannot be applied at all. Both are tested.

**Three kinds of change, because pretending they are one would lie twice.**

`edit` is a real config leaf with a computable new value -- the only kind
`apply_confirmed` will ever write.

`derived` is something that follows automatically and that the user must NOT
be asked to set. Filing status is the example that made this necessary:
ROADMAP lists "switch filing status" among widowhood's fields, and there IS no
such field -- `TrueTaxParams.filing_jointly` is set by the adapter from
`household.enabled`. Proposing it would be a checklist item pointing at
nothing, which is the shape this project has shipped before. It is shown, so
the user knows it happens, and it is not theirs to confirm.

`manual` is a number only the user has: a survivor benefit from an SSA letter,
a settlement, an inheritance's actual amount. The app states what to fetch and
where from, and refuses to invent a placeholder -- a plausible default here
would be a number somebody plans around.
"""
from __future__ import annotations

import copy
from typing import Any, Optional

WIDOWHOOD = "widowhood"
DIVORCE = "divorce"
DISABILITY = "disability"
INHERITANCE_RECEIVED = "inheritance_received"
REMARRIAGE = "remarriage"

KINDS = (WIDOWHOOD, DIVORCE, DISABILITY, INHERITANCE_RECEIVED, REMARRIAGE)

EDIT, DERIVED, MANUAL = "edit", "derived", "manual"


class TransitionError(RuntimeError):
    def __init__(self, message: str, *, code: str, http_status: int = 400):
        super().__init__(message)
        self.code = code
        self.http_status = http_status


def _leaf(cfg: dict, path: str, default=None):
    node: Any = cfg
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
    return default if node is None else node


def _set_leaf(cfg: dict, path: str, value) -> None:
    node = cfg
    parts = path.split(".")
    for part in parts[:-1]:
        node = node.setdefault(part, {})
    node[parts[-1]] = value


def _change(path, current, proposed, why, kind=EDIT):
    return {"path": path, "current": current, "proposed": proposed,
            "why": why, "kind": kind}


def _widowhood(cfg: dict) -> list:
    rows = []
    if _leaf(cfg, "household.enabled"):
        rows.append(_change(
            "household.enabled", True, False,
            "The household module models two lives. Leaving it on keeps a "
            "second mortality draw and a second set of balances running."))
        for path in ("household.spouse_base_salary_pre",
                     "household.spouse_bonus_pre"):
            value = _leaf(cfg, path, 0.0)
            if value:
                rows.append(_change(
                    path, value, 0.0,
                    "Future income from a spouse who has died is income the "
                    "plan will never receive; leaving it in overstates every "
                    "year after this one."))
    rows.append(_change(
        "tax filing status", "married filing jointly", "single",
        "This follows automatically from turning the household off -- the "
        "adapter sets `filing_jointly` from `household.enabled`, and there is "
        "no separate field. It is listed so you know it happens, not for you "
        "to set.", DERIVED))
    rows.append(_change(
        "social_security.pia_monthly_y0", _leaf(cfg, "social_security.pia_monthly_y0"),
        None,
        "A survivor benefit is usually the higher of the two records, and "
        "only SSA can tell you the figure for your case. Fetch it from your "
        "SSA statement or letter; this app will not guess a number you will "
        "plan around.", MANUAL))
    return rows


def _divorce(cfg: dict) -> list:
    rows = []
    if _leaf(cfg, "household.enabled"):
        rows.append(_change(
            "household.enabled", True, False,
            "One household becomes one person. The spouse's balances, income "
            "and mortality all leave the plan with them."))
    rows.append(_change(
        "initial.taxable", _leaf(cfg, "initial.taxable"), None,
        "Only the settlement says how the accounts divide, and a split this "
        "app assumed would be a number you plan around. Enter each balance "
        "as it stands after the division.", MANUAL))
    rows.append(_change(
        "state.expenses_y0", _leaf(cfg, "state.expenses_y0"), None,
        "Household spending does not halve: rent, utilities and insurance "
        "largely do not. Enter what one household now costs.", MANUAL))
    return rows


def _disability(cfg: dict) -> list:
    rows = []
    salary = _leaf(cfg, "contributions.base_salary_pre", 0.0)
    if salary:
        rows.append(_change(
            "contributions.base_salary_pre", salary, 0.0,
            "Employment income stops at the determination. Replacement "
            "income -- employer LTD after its waiting period, plus SSDI -- is "
            "a different figure entered below.", EDIT))
    rows.append(_change(
        "income_streams.parttime_annual_real", None, None,
        "Your LTD policy's benefit and your SSDI award are both numbers only "
        "your paperwork has. Enter them as an income stream; the app models "
        "neither the waiting period nor the offset rules.", MANUAL))
    rows.append(_change(
        "medical premiums", None, None,
        "Employer coverage usually ends, and the ACA path this app models "
        "needs your own premium. There is no premium table in this app.",
        MANUAL))
    return rows


def _inheritance_received(cfg: dict) -> list:
    rows = []
    mode = _leaf(cfg, "inheritance.mode", "off")
    mode = getattr(mode, "value", mode)
    if str(mode) != "off":
        rows.append(_change(
            "inheritance.mode", mode, "off",
            "THE DOUBLE-COUNT. The expected inheritance is a forecast of "
            "money arriving; once it has arrived it is a balance. Leaving the "
            "forecast on after adding the balance counts the same money "
            "twice, and the plan looks better than it is by exactly the size "
            "of the bequest."))
    rows.append(_change(
        "initial.taxable", _leaf(cfg, "initial.taxable"), None,
        "Add the amount that actually arrived, after tax and after any "
        "estate settlement costs. The received figure is rarely the expected "
        "one.", MANUAL))
    return rows


def _remarriage(cfg: dict) -> list:
    rows = []
    if not _leaf(cfg, "household.enabled"):
        rows.append(_change(
            "household.enabled", False, True,
            "Two lives again: a second mortality draw, a second set of "
            "balances, and joint filing."))
    rows.append(_change(
        "household.spouse_base_salary_pre",
        _leaf(cfg, "household.spouse_base_salary_pre", 0.0), None,
        "Your spouse's income, balances and Social Security record are all "
        "theirs to supply. Every household field below starts empty rather "
        "than at a default, because a default here is somebody's finances "
        "invented by an app.", MANUAL))
    rows.append(_change(
        "tax filing status", "single", "married filing jointly",
        "Follows automatically from turning the household on; there is no "
        "separate field.", DERIVED))
    return rows


_BUILDERS = {
    WIDOWHOOD: _widowhood,
    DIVORCE: _divorce,
    DISABILITY: _disability,
    INHERITANCE_RECEIVED: _inheritance_received,
    REMARRIAGE: _remarriage,
}


def commit(store, seam, checkin_seam, *, plan_id: str, parent_version_id: str,
           kind: str, cfg: dict, confirmed: list, checkin_body: dict,
           jump_minor: Optional[int] = None, occurred_at: str = "") -> dict:
    """The confirmed transition, as ONE atomic write.

    ROADMAP: a completed transition atomically produces a transition-tagged
    PlanVersion and a CheckIn. Atomically is the word that costs something.

    `create_plan_version` opens its own connection through `_transaction()`,
    and `checkin_seam` opens another through `store._connect()`. Calling both
    in sequence is TWO transactions: a failure between them leaves a plan
    version describing a life event with no check-in recording it, and the
    next reconciliation sees an archive whose contents nobody agreed to. So
    this composes `PersistenceStore._insert_plan_version`, which takes a
    connection, with the ledger inserts on that SAME connection inside one
    BEGIN.

    Nothing here decides what changes: `apply_confirmed` does, and it only
    applies what the user confirmed. This writes the result down.
    """
    import checkin_ledger as LEDGER

    applied = apply_confirmed(cfg, kind, confirmed)
    if not applied["applied"]:
        raise TransitionError(
            "nothing was confirmed, so there is no transition to record; a "
            "version identical to its parent would say a life event changed "
            "the plan when it did not", code="nothing_confirmed")

    if jump_minor is not None:
        # Appended to the body and normalised by `checkin_seam.validate_lines`
        # below, NOT hand-assembled here.
        #
        # I built the row field by field first and met three NOT NULL columns
        # one at a time -- period_start, then created_at, then whatever came
        # next. Each fix was a guess at a schema that already has one
        # normaliser, and a row that satisfies the columns while skipping that
        # normaliser is a row nothing else in this system produces.
        checkin_body = dict(checkin_body)
        checkin_body["actual"] = list(checkin_body.get("actual") or []) + [
            asset_jump_line(kind, jump_minor, occurred_at)]

    def mutate(inner_store):
        conn = inner_store._connect()
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            conn.execute("BEGIN IMMEDIATE")
            # The ledger installer the check-in seam uses, called INSIDE this
            # transaction rather than reimplemented. A live archive sits at
            # v6; without this the ledger tables do not exist and the insert
            # below fails on an archive the user has never checked in from.
            checkin_seam._install_ledger(inner_store, conn)
            version = type(inner_store)._insert_plan_version(
                conn, plan_id=plan_id,
                source_config=applied["config"],
                normalized_config=applied["config"],
                # The tag ROADMAP asks for. `plan_versions.source_kind` has no
                # CHECK constraint and already carries `draft`, `run`, `user`,
                # `duplicate` and `legacy_checkin`, so this joins an existing
                # vocabulary rather than widening a constrained one.
                source_kind="transition:%s" % kind,
                parent_version_id=parent_version_id)
            import checkin_seam as CHECKIN_SEAM
            header = dict(checkin_body)
            header["plan_id"] = plan_id
            # `_insert_plan_version` returns the ROW it wrote, whose primary
            # key is `id` -- not `plan_version_id`, which is what the seam
            # layer above calls the same value. Reading the wrong key raised
            # KeyError before the ledger insert, so the first attempt at the
            # atomicity test never reached the failure it meant to inject.
            header["plan_version_id"] = version["id"]
            # Through the seam's own validator, so these rows are the same
            # shape every other check-in writes.
            actual = CHECKIN_SEAM.validate_lines(
                header.get("actual") or [], "actual", header)
            expected = CHECKIN_SEAM.validate_lines(
                header.get("expected") or [], "expected", header)
            LEDGER.insert_checkin(conn, header)
            LEDGER.append_raw_lines(conn, header["checkin_id"], "actual", actual)
            LEDGER.append_raw_lines(conn, header["checkin_id"], "expected",
                                    expected)
            conn.commit()
        except Exception:
            # One rollback for both. Without it the version survives its own
            # insert while the check-in that explains it does not.
            conn.rollback()
            raise
        finally:
            conn.close()
        return {"plan_version_id": version["id"],
                "checkin_id": header["checkin_id"],
                "source_kind": "transition:%s" % kind,
                "applied": applied["applied"],
                "manual_remaining": [row["path"] for row in applied["skipped"]
                                     if row["kind"] == MANUAL]}

    return seam.write("transition:%s:%s" % (kind, plan_id), mutate)


def as_alternative(cfg: dict, kind: str, confirmed: list) -> dict:
    """The transition, expressed as a decision alternative.

    ROADMAP: a confirmed transition emits a before/after DecisionPacket. This
    is how, and the important part is what it does NOT do: it builds no second
    study engine. A transition is exactly the shape `/api/decide` already
    takes -- a baseline config and a set of changed leaves -- so it goes
    through the same machinery, gets the same cost estimate before running,
    and produces the same packet with the same disclosures.

    `levers` is the confirmed paths rather than a fixed list: the goal must
    permit what the transition moves, and permitting anything else would let
    a later caller smuggle in a change the user never confirmed.

    Returns `applicable: False` when the transition has no computable edits.
    Widowhood on a single-person plan, or remarriage where every field is the
    spouse's to supply, changes nothing this app can evaluate -- and a packet
    comparing a plan with itself would report "no effect" for a life event
    that certainly has one.
    """
    applied = apply_confirmed(cfg, kind, confirmed)
    changes = {row["path"]: row["to"] for row in applied["applied"]}
    if not changes:
        return {
            "applicable": False,
            "reason": ("this transition changes no value the model can "
                       "evaluate -- the lines it proposes are either yours to "
                       "supply or follow automatically. A packet comparing "
                       "the plan with itself would report no effect for an "
                       "event that certainly has one."),
            "manual_remaining": [row["path"] for row in applied["skipped"]
                                 if row["kind"] == MANUAL],
        }
    return {
        "applicable": True,
        "alternative": {"name": "after %s" % kind, "changes": changes},
        "levers": sorted(changes),
        "baseline_config": copy.deepcopy(cfg),
        "after_config": applied["config"],
        "manual_remaining": [row["path"] for row in applied["skipped"]
                             if row["kind"] == MANUAL],
        "note": ("Anything still marked `manual` is NOT in this comparison. "
                 "The packet measures the changes you confirmed; a survivor "
                 "benefit or a settlement you have not entered yet is absent "
                 "from both sides of it."),
    }


def asset_jump_line(kind: str, amount_minor: int, occurred_at: str) -> dict:
    """The check-in flow line for a transition's asset jump.

    ROADMAP: the jump is "explicitly attributed to the life-events bucket,
    without polluting market or spending", pinned by a test that goes red if
    it is bucketed wrongly.

    Measured rather than assumed, against the waterfall's own pinned fixture:
    a $250,000 arrival recorded as `life_event` leaves `market` at 421 -> a
    few thousand, while the SAME arrival left out of the flow lines entirely
    puts 247,390 into `market` -- an inheritance reported as investment
    performance. That is the failure this line prevents, and it is reachable,
    which is what makes the test meaningful rather than decorative.

    `life_event` is already one of `attribution.CATEGORIES` and already
    accepted by `checkin_seam.validate_lines`, so nothing new is being
    introduced here; what was missing was anything that produced one.
    """
    if kind not in _BUILDERS:
        raise TransitionError("unknown transition %r" % kind,
                              code="unknown_transition")
    if not isinstance(amount_minor, int) or isinstance(amount_minor, bool):
        raise TransitionError(
            "the jump must be an integer number of minor units; a float here "
            "is a rounding decision nobody made", code="invalid_request")
    if amount_minor == 0:
        # Not a line. A zero jump is "nothing moved", and emitting a
        # zero-amount flow would put a row in an immutable ledger to say so.
        raise TransitionError(
            "a zero jump is not a life event; omit the line instead",
            code="invalid_request")
    return {
        "category": "life_event",
        "amount_portfolio_minor": int(amount_minor),
        "occurred_at": occurred_at,
        "timing_state": "exact",
        "observation_state": "observed",
        "source_or_schedule_id": "transition:%s" % kind,
        "source_event_id": None,
        "component_leg_id": None,
        "timing_bucket": "exact",
    }


def propose(kind: str, cfg: dict) -> dict:
    """The checklist for one transition. Reads; never writes.

    `cfg` is deep-copied before anything touches it, so a builder that
    reached for a mutation could not have one.
    """
    if kind not in _BUILDERS:
        raise TransitionError(
            "unknown transition %r; the five are %s" % (kind, ", ".join(KINDS)),
            code="unknown_transition")
    if not isinstance(cfg, dict):
        raise TransitionError("config must be an object", code="invalid_request")
    rows = _BUILDERS[kind](copy.deepcopy(cfg))
    return {
        "kind": kind,
        "changes": rows,
        "editable": [row["path"] for row in rows if row["kind"] == EDIT],
        "applies_anything": False,
        "note": ("Nothing here has been applied. Confirm the lines you want "
                 "and this returns a new plan carrying only those; the rest "
                 "stay exactly as they are. Lines marked `manual` need a "
                 "number only you have, and lines marked `derived` follow "
                 "automatically and are shown so you know they happen."),
    }


def apply_confirmed(cfg: dict, kind: str, confirmed: list) -> dict:
    """A NEW config carrying only the confirmed edits, plus what was skipped.

    Refuses a path that this transition did not propose. Without that, a
    caller could hand any path to a function whose whole promise is that it
    only does what the checklist showed.
    """
    plan = propose(kind, cfg)
    editable = set(plan["editable"])
    confirmed = [str(path) for path in (confirmed or [])]
    unknown = sorted(set(confirmed) - editable)
    if unknown:
        raise TransitionError(
            "these paths were not proposed for %s, so they cannot be applied: "
            "%s" % (kind, ", ".join(unknown)), code="unproposed_path")

    updated = copy.deepcopy(cfg)
    applied = []
    for row in plan["changes"]:
        if row["kind"] != EDIT or row["path"] not in confirmed:
            continue
        _set_leaf(updated, row["path"], row["proposed"])
        applied.append({"path": row["path"], "from": row["current"],
                        "to": row["proposed"]})

    skipped = [row for row in plan["changes"]
               if row["path"] not in {a["path"] for a in applied}]
    return {
        "config": updated,
        "applied": applied,
        "skipped": [{"path": row["path"], "kind": row["kind"],
                     "why": row["why"]} for row in skipped],
        "unchanged": not applied,
        "note": ("Only the lines you confirmed were changed. Anything marked "
                 "`manual` still needs your number, and nothing here has been "
                 "saved -- this is a plan you can look at before keeping it."),
    }
