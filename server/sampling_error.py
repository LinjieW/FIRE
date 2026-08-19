"""How much of the number on screen is just the sampler.

ROADMAP 4.0 Phase 4, the second cheap-honesty item: show the sampling error
everywhere a rate is shown, and say plainly when more paths would not change
anything.

The page has always carried a sentence saying confidence intervals reflect
sampling and not assumption uncertainty. It has never carried an interval.
So the caveat was true and unusable: a reader could not tell whether 87.3%
and 88.1% were different answers or the same answer sampled twice.

**Exact, not normal-approximate.** A success rate near 1.0 with 10,000 paths
is exactly where the Wald interval misbehaves -- it can reach above 1, and it
is anti-conservative in the tail that matters here, which is the tail every
FIRE plan lives in. Clopper-Pearson is exact and conservative, and it degrades
gracefully at k=0 and k=n rather than collapsing to zero width.

**The numerics were already here and already checked.** `_betacf`, `betainc`
and `beta_ppf` were written for `tests/test_attribution_power.py` because
SciPy is absent, and that suite validates them against closed forms
(`I_x(a, 1) = x^a`), against symmetry, and at the degenerate ends. They moved
here rather than being copied, and that suite now imports them -- so its
checks validate the code that ships instead of a second copy that agrees with
it today.

**What this does NOT say.** The interval is about the sampler alone. Running
a million paths would shrink it to nothing and would not make the return
assumption any more true. That sentence already exists on the page; this
module gives it something to stand next to.
"""
from __future__ import annotations

import math
from typing import Optional

#: Path counts the app itself offers, used to answer "what would more paths
#: buy me". Kept here rather than imported from `app` so this module has no
#: dependency on the server; a test pins the two lists together.
OFFERED_PATHS = (2_000, 10_000, 30_000, 100_000)


def _betacf(a: float, b: float, x: float) -> float:
    tiny, eps, maxit = 1e-300, 3e-16, 500
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, maxit + 1):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        de = d * c
        h *= de
        if abs(de - 1.0) < eps:
            break
    return h


def betainc(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    front = math.exp(math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
                     + a * math.log(x) + b * math.log1p(-x))
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - front * _betacf(b, a, 1.0 - x) / b


def beta_ppf(q: float, a: float, b: float) -> float:
    lo, hi = 0.0, 1.0
    for _ in range(200):
        mid = 0.5 * (lo + hi)
        if betainc(a, b, mid) < q:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def clopper_pearson(k: int, n: int, alpha: float = 0.05) -> tuple:
    """Exact binomial interval. k=0 pins the lower end, k=n the upper end."""
    lower = 0.0 if k == 0 else beta_ppf(alpha / 2.0, k, n - k + 1)
    upper = 1.0 if k == n else beta_ppf(1.0 - alpha / 2.0, k + 1, n - k)
    return lower, upper


def success_interval(rate: Optional[float], paths: Optional[int],
                     alpha: float = 0.05) -> dict:
    """The 95% interval around a success rate, and what more paths would buy.

    `None` in, `applicable: False` out with the reason. A rate this cannot
    bound is one where a printed interval would be invented, and an invented
    interval is worse than none: it is the number a reader trusts most.
    """
    if rate is None or paths is None:
        missing = [name for name, value in (("rate", rate), ("paths", paths))
                   if value is None]
        return {"applicable": False, "missing": missing,
                "reason": "a rate and a path count are both needed; neither "
                          "is guessed"}
    try:
        rate = float(rate)
        paths = int(paths)
    except (TypeError, ValueError):
        return {"applicable": False, "missing": [],
                "reason": "rate and paths must be numbers"}
    if not (0.0 <= rate <= 1.0) or paths <= 0:
        return {"applicable": False, "missing": [],
                "reason": "a rate outside 0..1, or a non-positive path count, "
                          "cannot be bounded"}

    successes = int(round(rate * paths))
    lower, upper = clopper_pearson(successes, paths, alpha)
    half = (upper - lower) / 2.0

    # What the next tiers up would buy, computed the same exact way rather
    # than scaled by 1/sqrt(n): the square-root rule is a normal-approximation
    # fact, and quoting it beside an exact interval would mix two methods in
    # one sentence.
    projections = []
    for candidate in OFFERED_PATHS:
        if candidate <= paths:
            continue
        c_lo, c_hi = clopper_pearson(int(round(rate * candidate)),
                                     candidate, alpha)
        projections.append({"paths": candidate,
                            "half_width_pp": (c_hi - c_lo) / 2.0 * 100.0})

    return {
        "applicable": True,
        "rate": rate,
        "paths": paths,
        "confidence": 1.0 - alpha,
        "lower": lower,
        "upper": upper,
        "half_width_pp": half * 100.0,
        "method": "clopper-pearson-exact",
        "projections": projections,
        #: The judgment stays with the reader, and this is the sentence that
        #: leaves it there. ROADMAP asks the app to volunteer "100k's CI is
        #: already far below your decision margin", but the app does not know
        #: the margin -- only the person deciding does. So it states the width
        #: and the condition rather than the conclusion.
        "guidance": (
            "This interval is the sampler's, not the assumptions'. Running "
            "more paths shrinks it and makes no assumption truer. If your "
            "decision does not turn on a difference smaller than %.1f "
            "percentage points, more paths will not change it."
            % (half * 100.0)),
    }
