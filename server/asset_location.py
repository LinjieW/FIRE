"""Asset location: the same portfolio, held in a different order of accounts.

ROADMAP 4.0 Phase 3 asks for "2-3 typical placements compared on the same
paths, no optimiser". This is that, and the restraint is deliberate: a
comparison hands the user three runs and lets them read the difference, while
an optimiser would have to encode a preference nobody has ruled on.

What this can and cannot price
------------------------------
The classic asset-location argument has two halves:

1. **Tax drag differs by location.** Interest and non-qualified distributions
   are ordinary income; qualified dividends are not; and neither is charged at
   all inside a pretax or Roth account. This engine prices exactly this half,
   and only since the taxable drag became a yield times a rate -- before that
   it was one hardcoded number that could not tell the two holdings apart.

2. **Expected returns differ by location.** Putting the higher-returning asset
   in the Roth is worth something on its own.

**This engine cannot price the second half**, and the comparison says so rather
than letting the reader assume a total verdict. `simulate_lifecycle_v98` draws
ONE blended portfolio return per year from the glide path and applies it to
every bucket; there is no per-account allocation to differentiate. So these
arms differ in what the taxable bucket is assumed to DISTRIBUTE, not in what it
earns. A result here is a tax-side comparison, and reporting it as the whole of
asset location would overstate it in the direction users already over-believe.

Why the arms are what they are
------------------------------
Each archetype sets the two inputs the drag is derived from, and nothing else.
Same seed, same paths, same everything else -- so any difference between arms
is the tax treatment of distributions and cannot be market noise.
"""
from __future__ import annotations

import copy
from typing import Any, Optional

import engine_adapter as ENG

#: The placements, with the holding each one represents. Yields are annual
#: distribution rates on the taxable bucket; the qualified share decides how
#: much is taxed at LTCG rates rather than as ordinary income.
PLACEMENTS = (
    {
        "key": "bonds_in_taxable",
        "label_zh": "债券放在应税账户",
        "label_en": "Bonds in the taxable account",
        "dividend_yield": 0.035,
        "dividend_qualified_fraction": 0.0,
        "why_zh": "利息全额按普通收入课税，且债券的分派率远高于股票——"
                  "这是税上最贵的摆法，也是很多人默认的摆法。",
        "why_en": "Interest is ordinary income and bonds distribute far more "
                  "than equities. This is the most expensive placement, and a "
                  "very common default.",
    },
    {
        "key": "equities_in_taxable",
        "label_zh": "宽基股票放在应税账户",
        "label_en": "Broad equities in the taxable account",
        "dividend_yield": 0.013,
        "dividend_qualified_fraction": 1.0,
        "why_zh": "分派率低，且几乎全部是合格股息，走 0/15/20 分档。"
                  "把债券挪进税前账户之后通常就是这个形状。",
        "why_en": "A low distribution rate, almost all of it qualified and "
                  "taxed at 0/15/20. This is the usual shape once bonds have "
                  "been moved into the pretax account.",
    },
    {
        "key": "blended",
        "label_zh": "不做摆放（混合持有）",
        "label_en": "No location strategy (blended)",
        "dividend_yield": 0.017,
        "dividend_qualified_fraction": 0.90,
        "why_zh": "每个账户装同样的东西——没有刻意摆放时的基准。",
        "why_en": "The same mix in every account: the baseline when nothing "
                  "is placed deliberately.",
    },
)


def _arm(cfg: dict, placement: dict, paths: int, seed: int,
         horizon: int) -> dict:
    """One placement, run at the shared seed."""
    arm_cfg = copy.deepcopy(cfg)
    tax_us = arm_cfg.setdefault("tax_us", {})
    tax_us["dividend_yield"] = placement["dividend_yield"]
    tax_us["dividend_qualified_fraction"] = placement["dividend_qualified_fraction"]
    # An explicit override would pin the drag and make every arm identical,
    # which would read as "asset location does not matter" -- the most
    # confident possible way to be wrong here.
    tax_us["drag_taxable"] = None
    home = (ENG.run_full(arm_cfg, paths, seed, horizon) or {}).get("home") or {}
    terminal = home.get("terminal_real") or {}
    return {
        "key": placement["key"],
        "dividend_yield": placement["dividend_yield"],
        "dividend_qualified_fraction": placement["dividend_qualified_fraction"],
        "effective_drag": ENG.build_kwargs(arm_cfg, False)["tax_us"].drag_taxable,
        "lifetime_success": home.get("lifetime_success"),
        "terminal_real_p50": terminal.get("p50"),
        "terminal_real_p10": terminal.get("p10"),
    }


def compare_placements(cfg: dict, paths: int = 2_000, seed: int = 4242,
                       horizon: int = 50) -> dict:
    """Three placements on one set of paths.

    Returns `applicable: False` with `None` results when the plan has no
    taxable balance to place anything in. That is not a zero -- a household
    holding everything in a 401(k) has no asset-location decision to make, and
    reporting "no difference" would answer a question they never asked while
    looking like a measurement.
    """
    # Read through the engine's own completion rather than off the raw dict.
    # They agree today only because `AccountStack.taxable` happens to default
    # to 0; if that ever changed, a raw read would answer a question about the
    # request instead of about the plan the engine will actually run.
    taxable_now = float(ENG.build_kwargs(cfg, False)["initial"].taxable)
    if taxable_now <= 0.0:
        return {
            "applicable": False,
            "reason": "no taxable balance: nothing to place",
            "taxable_balance": taxable_now,
            "arms": None,
            "spread_terminal_real": None,
            "spread_success": None,
            "prices_return_differences": False,
        }

    arms = [_arm(cfg, placement, paths, seed, horizon)
            for placement in PLACEMENTS]
    terminals = [a["terminal_real_p50"] for a in arms
                 if a["terminal_real_p50"] is not None]
    successes = [a["lifetime_success"] for a in arms
                 if a["lifetime_success"] is not None]
    return {
        "applicable": True,
        "reason": None,
        "taxable_balance": taxable_now,
        "arms": arms,
        "spread_terminal_real": ((max(terminals) - min(terminals))
                                 if len(terminals) == len(arms) else None),
        "spread_success": ((max(successes) - min(successes))
                           if len(successes) == len(arms) else None),
        #: Stated in the payload, not just in this module's docstring: the
        #: caller renders a verdict and must not present a tax-side comparison
        #: as the whole of asset location.
        "prices_return_differences": False,
        "seed": int(seed),
        "paths": int(paths),
    }
