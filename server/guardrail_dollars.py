"""What a guardrail costs in dollars, for the ones that are dollars.

ROADMAP 4.0 Phase 4, inside the advisor-grade report: "each guardrail states
the concrete dollar spending adjustment at the trigger ('trigger it and cut
$400 a month')". That sentence is the Kitces / Income Lab advisor-side
convention, and it works because a percentage is a policy while a dollar
figure is a decision someone can picture.

**Only two of the five actions are dollar amounts, and the other three say so
rather than getting a number.** `cut_permanent_spending` is money by
construction: the rule's adjustment percentage against current spending.
`pause_large_events` is money when the plan actually schedules events, and is
nothing when it does not. The remaining three -- defer FIRE, extend part-time
work, reassess a conversion or a relocation -- are not spending adjustments at
all, and inventing a dollar figure for them would be the most confident kind
of wrong: a number, in a formal report, for something nobody measured.

So this returns `amount_monthly: None` with a reason for those, and a reader
who sees a blank sees "this action is not a dollar amount" rather than
"$0/month", which would read as costless.
"""
from __future__ import annotations

from typing import Any, Optional

import guardrails as G

#: The two actions that denominate in money, and how each is derived.
_DOLLAR_ACTIONS = (G.CUT_PERMANENT_SPENDING, G.PAUSE_LARGE_EVENTS)

_WHY_NOT_MONEY = {
    G.DEFER_FIRE:
        "deferring retirement changes WHEN, not how much per month; its cost "
        "is a year of your life rather than a spending line",
    G.EXTEND_PART_TIME:
        "part-time work adds income rather than cutting spending, and how "
        "much depends on work you have not chosen yet",
    G.REASSESS_CONVERSION_OR_RELOCATION:
        "a reassessment is a decision to make, not an amount; its size is "
        "whatever the reassessment concludes",
}


def _leaf(cfg: dict, path: str, default=None):
    node: Any = cfg
    for part in path.split("."):
        if not isinstance(node, dict):
            return default
        node = node.get(part)
    return default if node is None else node


def dollarise(policy, cfg: dict) -> dict:
    """One policy's trigger, in money where money is the right unit."""
    action = getattr(policy, "action", None)
    spending = float(_leaf(cfg, "state.expenses_y0", 0.0) or 0.0)

    if action == G.CUT_PERMANENT_SPENDING:
        pct = float(_leaf(cfg, "rule.adjustment_pct", 0.0) or 0.0)
        if pct <= 0 or spending <= 0:
            return _not_priced(
                policy, "the plan does not state both a spending level and an "
                        "adjustment size, so the cut has no dollar value yet")
        annual = spending * pct
        return {
            "policy_id": getattr(policy, "policy_id", None),
            "action": action,
            "priced": True,
            "amount_annual": annual,
            "amount_monthly": annual / 12.0,
            "basis": ("%.0f%% of %s a year in today's dollars"
                      % (pct * 100.0, _round(spending))),
        }

    if action == G.PAUSE_LARGE_EVENTS:
        events = _leaf(cfg, "events.items", None)
        total = 0.0
        for event in (events or ()):
            if isinstance(event, dict):
                total += abs(float(event.get("amount_real") or 0.0))
        if total <= 0:
            # Zero here is a real measurement -- there are no events to pause
            # -- and it is reported as such rather than as an unknown.
            return {
                "policy_id": getattr(policy, "policy_id", None),
                "action": action, "priced": True,
                "amount_annual": 0.0, "amount_monthly": 0.0,
                "basis": "this plan schedules no large events, so pausing "
                         "them frees nothing",
            }
        return {
            "policy_id": getattr(policy, "policy_id", None),
            "action": action, "priced": True,
            "amount_annual": total, "amount_monthly": total / 12.0,
            "basis": "the scheduled large events this plan carries",
        }

    return _not_priced(policy, _WHY_NOT_MONEY.get(
        action, "this action is not a spending adjustment"))


def _not_priced(policy, why: str) -> dict:
    return {
        "policy_id": getattr(policy, "policy_id", None),
        "action": getattr(policy, "action", None),
        "priced": False,
        "amount_annual": None,
        "amount_monthly": None,
        "why_not_priced": why,
    }


def dollarise_all(policies: list, cfg: dict) -> dict:
    rows = [dollarise(policy, cfg) for policy in (policies or ())]
    return {
        "policies": rows,
        "priced": sum(1 for row in rows if row["priced"]),
        "not_priced": sum(1 for row in rows if not row["priced"]),
        "note": ("A blank amount means the action is not a spending "
                 "adjustment, NOT that it is free. Three of the five "
                 "guardrail actions change when you retire or what work you "
                 "do rather than what you spend, and putting a dollar figure "
                 "on those would be inventing one."),
    }


def _round(value: float) -> str:
    return "${:,.0f}".format(value)
