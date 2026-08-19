"""Which parts of this engine draw randomness, counted rather than guessed.

Roadmap 6.0 Phase 1 (idea-bank A15, first half). The correlation registry
cannot be written until we know what there is to register, and this afternoon
proved that nobody does: a keyword scan of `default_config()` returned
seventeen stochastic blocks and was wrong in both directions. `mortality`
samples and was missed. `obbba` does not sample and was listed. Both are
measured facts, and both are mutation tests for this module.

**Attribution is by the call site's SOURCE TEXT, not by function and not by
line number.** The live engine is one large file, and a single function draws
for several unrelated modules -- attributing by function would report
"simulate_retirement_v98 draws 40,000 times", which is true and useless.

Line numbers were the first attempt and they broke on first real use: adding a
dataclass near the top of the engine moved seven draw sites that had not
changed at all, and the gate reported them as vanished-and-arrived. A table
that churns for unrelated edits is a table people start rubber-stamping, which
is the failure this whole mechanism exists to prevent. The source line is
stable under insertions elsewhere, changes exactly when the drawing code
changes, and reads as itself. The line number is still reported, as
information rather than as identity.

**The wrapper does not change what the engine computes.** It forwards every
call to the real generator and returns its value unchanged; only a counter
moves. `verify_transparent()` is the check that this stays true -- it runs the
same plan with and without the wrapper and compares result digests, because a
census that perturbs the thing it measures is worse than no census.

**Not on the default path.** Nothing imports this during a normal run. It is
an entry point for the census gate and for a person asking the question.
"""
from __future__ import annotations

import linecache
import sys
from typing import Any, Optional

#: Generator methods that consume randomness. Kept explicit rather than
#: intercepting everything: `bit_generator`, `.state` and friends are read
#: constantly and consume nothing, and counting them would drown the signal.
DRAWING_METHODS = (
    "random", "standard_normal", "normal", "uniform", "integers", "choice",
    "poisson", "binomial", "exponential", "lognormal", "beta", "gamma",
    "multivariate_normal", "shuffle", "permutation", "geometric",
)


class CountingGenerator:
    """A transparent stand-in for `numpy.random.Generator`.

    Every attribute that is not a drawing method is forwarded untouched, so
    code reading `.bit_generator.state` (which this engine does, to derive
    child streams) behaves exactly as before.
    """

    def __init__(self, inner, sites: dict):
        object.__setattr__(self, "_inner", inner)
        object.__setattr__(self, "_sites", sites)

    def __getattr__(self, name: str) -> Any:
        inner = object.__getattribute__(self, "_inner")
        attribute = getattr(inner, name)
        if name not in DRAWING_METHODS:
            return attribute
        sites = object.__getattribute__(self, "_sites")

        def counted(*args, **kwargs):
            # Frame 1 is the caller. Deliberately not inspect.stack(), which
            # builds the whole stack and turns a census into a benchmark.
            frame = sys._getframe(1)
            source = linecache.getline(frame.f_code.co_filename,
                                       frame.f_lineno).strip()
            key = (frame.f_code.co_filename, source, name)
            row = sites.get(key)
            if row is None:
                sites[key] = {"calls": 1, "method": name,
                              "file": frame.f_code.co_filename,
                              "line": frame.f_lineno,
                              "source": source,
                              "function": frame.f_code.co_name}
            else:
                row["calls"] += 1
            return attribute(*args, **kwargs)

        return counted

    def __setattr__(self, name: str, value) -> None:   # pragma: no cover
        setattr(object.__getattribute__(self, "_inner"), name, value)


def census(run, *, config: dict, paths: int = 60, seed: int = 4242,
           horizon: int = 25) -> dict:
    """Run `run` with every generator counted, and report the draw sites.

    `run` is passed in rather than imported so this module has no dependency
    on the adapter -- and so a test can hand it something small.
    """
    import numpy as np

    sites: dict = {}
    real_default_rng = np.random.default_rng

    def counting_default_rng(*args, **kwargs):
        return CountingGenerator(real_default_rng(*args, **kwargs), sites)

    np.random.default_rng = counting_default_rng
    try:
        run(config, paths, seed, horizon)
    finally:
        np.random.default_rng = real_default_rng

    rows = sorted(sites.values(), key=lambda r: (r["file"], r["line"]))
    # linecache is warm from the run; drop it so a later edit to a source
    # file cannot make a second census report stale text.
    linecache.clearcache()
    return {
        "sites": rows,
        "total_calls": sum(r["calls"] for r in rows),
        "site_count": len(rows),
    }


def verify_transparent(run, *, config: dict, digest, paths: int = 60,
                       seed: int = 4242, horizon: int = 25) -> dict:
    """Prove the wrapper changed nothing, by running the plan both ways.

    Without this the census is an unfalsifiable claim about a program that is
    no longer the program being shipped. `digest` is passed in for the same
    reason `run` is: this module depends on neither the adapter nor
    persistence.
    """
    import numpy as np

    plain = digest(run(config, paths, seed, horizon))

    sites: dict = {}
    real_default_rng = np.random.default_rng
    np.random.default_rng = lambda *a, **k: CountingGenerator(
        real_default_rng(*a, **k), sites)
    try:
        wrapped = digest(run(config, paths, seed, horizon))
    finally:
        np.random.default_rng = real_default_rng

    return {"identical": plain == wrapped, "plain": plain, "wrapped": wrapped,
            "sites_seen": len(sites)}
