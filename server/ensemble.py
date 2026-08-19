"""Phase 3 · the assumption ensemble, and the noise floor it is read against.

ROADMAP asks for an ensemble layer above the Monte Carlo path layer, with
sampling uncertainty shown separately from assumption uncertainty. The second
half is the hard half, and this module exists mostly to get it right.

Why there are no common random numbers
--------------------------------------
The obvious design runs every variant at one seed so the draws are shared and
the difference is purely the assumption. This engine cannot do that, and the
reason is measured rather than assumed: several of its samplers are
*conditional*. `sample_inheritance` draws an occurrence and returns early when
it does not fire; `sample_eldercare_events` draws severity only inside the
branch. A path whose branch does not fire consumes fewer draws, so every later
draw shifts. `tests/test_attribution_crn.py` pins both facts against the engine
source, and the attribution protocol's CRN map excludes the 2.0 runner for
exactly this reason.

So a variant's difference from the baseline contains the assumption effect AND
whatever the streams did after they desynchronised. Reporting that difference
as "the effect of this assumption" would be wrong, and wrong in the flattering
direction: it makes every assumption look influential.

What this does instead
----------------------
It measures the noise. The baseline is run at several seeds; the spread across
those runs is what this engine's own randomness does to the metric when nothing
about the plan changed. A variant's effect is reported against that spread, and
is called `distinguishable` only when it lands outside it.

That is a weaker claim than CRN would support and it is the true one. A
narrower one would need either an engine whose draws are unconditional, or a
per-stream address map for the runner -- both of which are changes to the 2.0
engine that Phase 2's protocol work explicitly ruled out.

Nothing here writes a snapshot. ROADMAP: "ensemble 中间运行不写 snapshot，只有
进入 packet 的终判运行存档".
"""
from __future__ import annotations

import copy
import math
import multiprocessing
import os
from typing import Callable, Optional

#: How many seeds the baseline is run at to establish the noise floor. Three is
#: the minimum that gives a spread rather than a difference, and every extra
#: seed costs a full run; the caller can raise it.
DEFAULT_NOISE_SEEDS = 3

#: Metrics an ensemble reads. Deliberately few and all top-level: a pack's
#: effect on a number nobody looks at is not evidence of anything.
METRICS = ("lifetime_success", "terminal_real_p50", "fire_age_p50",
           "mean_real_consumption")


#: `run_full` reports two scenarios; a pack is read in the one it acts on.
SCENARIOS = ("home", "relocation")


class EnsembleError(RuntimeError):
    """A pack or run the ensemble will not silently work around."""


# ---------------------------------------------------------------------------
# Assumption packs.
# ---------------------------------------------------------------------------

#: Leaves the engine reads but `default_config()` does not set, with the line
#: that reads each. A pack may target one of these even though it is absent
#: from the config.
#:
#: The allowlist exists because both obvious rules are wrong. "Must be present
#: in the config" rejects `equity_mu_shift` -- read at engine_adapter.py:574 with an
#: implicit default of 0.0, exposed in the wizard, and the single most useful
#: thing an assumption ensemble can perturb. "Anything goes" lets a typo become
#: a pack that silently does nothing, reports an effect of zero, and tells the
#: user that assumption does not matter -- a wrong conclusion stated
#: confidently, which is the failure this project keeps meeting.
#:
#: `tests/test_ensemble.py` checks each entry against the engine source, so an
#: entry that stops being read fails rather than going quiet.
ENGINE_READ_UNSET_LEAVES = {
    "returns.equity_mu_shift": {
        "read_at": "server/engine_adapter.py",
        "reader": 'cfg.get("returns") or {}).get("equity_mu_shift", 0.0)',
        "baseline": 0.0,
        "note": "moves the whole regime mixture's mean; the sensitivity "
                "mu-band composes on top of it",
    },
}


class LeafCondition:
    """A precondition on one config leaf that equality cannot express.

    Two of the library's packs need one. `relocation.relocation_age` must be
    *set*, not equal to anything in particular -- with it `None` the plan never
    actually relocates, so `relocation.enabled = True` alone leaves every
    relocation-side assumption inert. And a bond pack needs the glide to
    actually hold bonds; against the default All-equity glide (equity 1.0 at
    both ends) bond returns cannot move a single dollar.

    `description` is what the user is told, so it says what the leaf must be
    rather than naming a predicate.
    """

    __slots__ = ("test", "description")

    def __init__(self, test, description: str):
        self.test = test
        self.description = description


class AssumptionPack:
    """A named, declarative perturbation of one or more config leaves.

    Declarative on purpose: a pack that could run arbitrary code would be a
    pack whose effect cannot be described to the user, and "which assumption
    moved this" is the entire product of the ensemble. Each entry is a dotted
    path and a replacement value, so the pack can be printed, archived, and
    reversed.
    """

    __slots__ = ("name", "changes", "rationale", "requires", "scenario")

    def __init__(self, name: str, changes: dict, rationale: str = "",
                 requires: Optional[dict] = None,
                 scenario: str = "home"):
        if not name or not isinstance(name, str):
            raise EnsembleError("an assumption pack needs a name")
        if not isinstance(changes, dict) or not changes:
            raise EnsembleError("pack %r changes nothing" % name)
        for path, value in changes.items():
            if not isinstance(path, str) or "." not in path:
                raise EnsembleError(
                    "pack %r: %r is not a dotted config path" % (name, path))
            if isinstance(value, (dict, list)):
                raise EnsembleError(
                    "pack %r: %s replaces a container; packs perturb leaves so "
                    "their effect can be stated" % (name, path))
        self.name = name
        self.changes = dict(changes)
        self.rationale = rationale
        #: Config leaves that must already hold for this pack to mean anything.
        #: A layoff pack on a plan with `layoff.enabled = False` perturbs a
        #: subsystem that never runs: the effect is exactly zero, and zero
        #: reads as "this risk does not matter to you". It does not matter to
        #: that plan, which is a different statement from "we tested it and it
        #: was fine", and the two must not arrive as the same number.
        self.requires = dict(requires or {})
        if scenario not in SCENARIOS:
            raise EnsembleError("pack %r: unknown scenario %r; run_full reports "
                                "%s" % (name, scenario, ", ".join(SCENARIOS)))
        #: Which of `run_full`'s two scenarios this pack's effect lands in.
        #: The SS haircut applies only `in_china` and FX only bites after the
        #: move, so both are invisible in `home` -- correctly, since `home` is
        #: the never-relocate path. Reading them there would report zero.
        self.scenario = scenario

    def apply(self, cfg: dict) -> dict:
        """A copy of `cfg` with the pack applied, or a refusal.

        The leaf must either already exist in the config or be one of
        `ENGINE_READ_UNSET_LEAVES`. Anything else is refused: a pack targeting
        a path the engine never reads produces a run identical to the baseline,
        reports an effect of exactly zero, and tells the user that assumption
        does not matter. A typo would do that silently.
        """
        out = copy.deepcopy(cfg)
        for path, value in sorted(self.changes.items()):
            if not _engine_knows(path):
                raise EnsembleError(
                    "pack %r: %s is not a leaf this engine reads; a pack that "
                    "reaches nothing reports an effect of zero and reads as "
                    "'this assumption does not matter'" % (self.name, path))
            parts = path.split(".")
            node = out
            for part in parts[:-1]:
                if not isinstance(node, dict):
                    raise EnsembleError(
                        "pack %r: %s is not addressable" % (self.name, path))
                # Created when absent: the engine defaults the whole block, so
                # a sparse config is a client that did not send it, not a plan
                # without it.
                node = node.setdefault(part, {})
            if not isinstance(node, dict):
                raise EnsembleError(
                    "pack %r: %s is not addressable" % (self.name, path))
            node[parts[-1]] = value
        return out

    def applicable(self, cfg: dict):
        """`(True, "")` or `(False, why)` for this plan.

        Checked before running rather than inferred from a zero effect
        afterwards, because those two zeros mean different things.
        """
        for path, expected in sorted(self.requires.items()):
            node, parts = cfg, path.split(".")
            for part in parts[:-1]:
                node = node.get(part) if isinstance(node, dict) else None
            value = node.get(parts[-1]) if isinstance(node, dict) else None
            if isinstance(expected, LeafCondition):
                if not expected.test(value):
                    return False, ("this plan has %s = %r; the pack needs it %s"
                                   % (path, value, expected.description))
                continue
            wanted = expected if isinstance(expected, tuple) else (expected,)
            if value not in wanted:
                return False, ("this plan has %s = %r, and the pack only means "
                               "something when it is %s" %
                               (path, value, " or ".join(repr(w) for w in wanted)))
        return True, ""

    def describe(self) -> dict:
        return {"name": self.name, "rationale": self.rationale,
                "changes": dict(sorted(self.changes.items())),
                "scenario": self.scenario,
                "requires": {k: (v.description if isinstance(v, LeafCondition)
                                 else list(v) if isinstance(v, tuple) else v)
                             for k, v in sorted(self.requires.items())}}


# ---------------------------------------------------------------------------
# Reading a run.
# ---------------------------------------------------------------------------

def read_metrics(result: dict, scenario: str = "home") -> dict:
    """The ensemble's metrics out of one `run_full` payload.

    Missing metrics come back as `None` rather than 0.0: a pack whose effect is
    computed against a missing baseline value would report the whole metric as
    its effect.

    `scenario` selects between `run_full`'s two payloads. It defaults to `home`
    because most assumptions bite there, but a relocation-side assumption read
    in `home` reports exactly zero -- `home` is the never-relocate path, so it
    is right that FX cannot touch it, and wrong to publish that zero as
    "FX does not matter".

    `mean_real_consumption` is the engine's percentile block, not a scalar; the
    p50 is taken here. Before this it fell through the numeric coercion below
    and came back `None` on every single run, which made one of the four
    advertised METRICS unusable everywhere without ever saying so.
    """
    payload = (result or {}).get(scenario) or {}
    out = {}
    out["lifetime_success"] = payload.get("lifetime_success")
    terminal = payload.get("terminal_real") or {}
    out["terminal_real_p50"] = terminal.get("p50")
    fire_age = payload.get("fire_age") or {}
    out["fire_age_p50"] = fire_age.get("p50")
    consumption = payload.get("mean_real_consumption") or {}
    out["mean_real_consumption"] = consumption.get("p50") \
        if isinstance(consumption, dict) else consumption
    return {k: (float(v) if isinstance(v, (int, float)) else None)
            for k, v in out.items()}


def _spread(values: list) -> dict:
    """Sampling spread of one metric across seeds. `None` when unusable."""
    usable = [v for v in values if isinstance(v, (int, float))]
    if len(usable) < 2:
        return {"n": len(usable), "mean": None, "sd": None,
                "min": None, "max": None, "range": None}
    mean = sum(usable) / len(usable)
    variance = sum((v - mean) ** 2 for v in usable) / (len(usable) - 1)
    return {"n": len(usable), "mean": mean, "sd": math.sqrt(variance),
            "min": min(usable), "max": max(usable),
            "range": max(usable) - min(usable)}


# ---------------------------------------------------------------------------
# The parallel runner.
# ---------------------------------------------------------------------------

def _worker(task):
    """One whole variant, run sequentially inside its own process.

    Deliberately one variant per process rather than nesting the engine's own
    chunk pool: `_run_chunked_stats` already spawns a Pool, and a pool inside a
    pool is how a machine ends up with cpu_count^2 workers and a job that never
    finishes. Process-level parallelism over VARIANTS is what ROADMAP asks for.

    **`execution_mode="sequential"` is what makes that paragraph true.**

    It used to be prose alone, and the code relied on `paths` happening to sit
    below `MP_THRESHOLD`. Roadmap 5.0 lowered the threshold from 20,000 to
    5,000 for good measured reasons, and at that moment every formal decision
    study began asking a daemonic pool worker to spawn its own pool. Python
    refuses outright -- `daemonic processes are not allowed to have children`
    -- so Standard (10,000 paths) and Official (100,000) both died, which is
    every tier a packet is allowed to use.

    The comment stayed true-sounding the whole time. That is the shape this
    repository keeps paying for: a stated property nothing enforced. It is
    enforced here now.
    """
    import sys
    root = task["root"]
    for path in (os.path.join(root, "server"), os.path.join(root, "engine")):
        if path not in sys.path:
            sys.path.insert(0, path)
    import engine_adapter as ENG
    result = ENG.run_full(task["config"], task["paths"], task["seed"],
                          task["dist_paths"], execution_mode="sequential")
    scenario = task.get("scenario") or "home"
    return {"label": task["label"], "seed": task["seed"], "scenario": scenario,
            "metrics": read_metrics(result, scenario)}


def _engine_knows(path: str) -> bool:
    """Is `path` a leaf this ENGINE reads -- as opposed to one this request
    happened to send?

    The guard exists to catch a pack aimed at nothing: `state.retire_age` does
    not exist, so writing it changes no run, reports an effect of exactly zero,
    and tells the user that assumption does not matter. That is a defect in the
    pack, and `default_config()` is what settles it.

    It is NOT a claim that the caller must have posted the block. The UI posts
    no `bonds` key at all, and the engine defaults the whole block before it
    runs -- so writing `bonds.correlation_with_equity` into a config that lacks
    it is the correct perturbation, not an error. Judging against the request
    instead of against the engine is what made `correlations_break` raise
    inside a running study, twice, in an installed app.

    The tempting fix -- merge `default_config()` into the request first -- was
    measured and rejected: it also merges `initial`, handing the user a
    starting portfolio they never entered. Lifetime success moved from 0.750 to
    0.925 on a real UI-shaped config. Silently editing the plan is worse than
    the crash it would have prevented.
    """
    import engine_adapter as ENG
    if path in ENGINE_READ_UNSET_LEAVES:
        return True
    node, parts = ENG.default_config(), path.split(".")
    for part in parts[:-1]:
        node = node.get(part) if isinstance(node, dict) else None
    return isinstance(node, dict) and parts[-1] in node


def missing_leaves(pack, cfg: dict) -> list:
    """Change targets the engine has no leaf for, checked before running.

    `apply` raises on these, which is right for a programming error and wrong
    as the way a user meets one, minutes into a job they paid for.
    """
    return [path for path in sorted(pack.changes) if not _engine_knows(path)]


def _indexed_worker(pair):
    """`(index, task)` -> `(index, result)`, so completions can be consumed as
    they land and still be reassembled in task order."""
    index, task = pair
    return index, _worker(task)


def _run_tasks(tasks: list, *, workers: Optional[int] = None,
               runner: Optional[Callable] = None,
               on_progress: Optional[Callable] = None) -> list:
    """Run variant tasks, in parallel processes unless a runner is injected.

    `on_progress(done, total)` is called as each task lands. It exists so a
    caller can report progress WITHOUT giving up the pool: the obvious way to
    get per-task progress is to inject a serial runner and count, which is how
    this shipped at first — and ROADMAP names process-level parallelism as an
    explicit Phase 3 deliverable that may not be substituted by waiting longer.

    Results always come back in task order. `run_study` zips them against the
    task list, so `imap_unordered`'s completion order is used for the progress
    callback and discarded for the result.

    A note on why the parallel path looked broken: two measurement harnesses
    written for this module lacked an `if __name__ == "__main__":` guard, so
    the spawned children re-imported the script, re-entered this function at
    module level, and died in `freeze_support`. The pool was never the problem.
    Measured on 10 cores at load 5.0: 22 tasks take 1.3s parallel against 3.6s
    serial.
    """
    if runner is not None:
        results = []
        for index, task in enumerate(tasks):
            results.append(runner(task))
            if on_progress is not None:
                on_progress(index + 1, len(tasks))
        return results
    if len(tasks) == 1:
        result = _worker(tasks[0])
        if on_progress is not None:
            on_progress(1, 1)
        return [result]
    count = workers or int(os.environ.get("FIRE_ENSEMBLE_WORKERS") or 0) \
        or max(1, min(len(tasks), (os.cpu_count() or 4) - 1))
    ctx = multiprocessing.get_context("spawn")
    with ctx.Pool(count) as pool:
        if on_progress is None:
            return pool.map(_worker, tasks)
        results = [None] * len(tasks)
        done = 0
        for index, result in pool.imap_unordered(_indexed_worker,
                                                 list(enumerate(tasks))):
            results[index] = result
            done += 1
            on_progress(done, len(tasks))
        return results


def run_ensemble(base_config: dict, packs: list, *, paths: int, seed: int,
                 dist_paths: int = 200, root: str,
                 noise_seeds: int = DEFAULT_NOISE_SEEDS,
                 workers: Optional[int] = None,
                 runner: Optional[Callable] = None) -> dict:
    """Run the baseline at several seeds and each pack once, then compare.

    Returns each pack's effect on each metric alongside the baseline's own
    spread across seeds, and calls the effect `distinguishable` only when it
    lands outside that spread. It does not rank packs, score them, or combine
    them into one number -- ROADMAP forbids a single utility score, and one
    ensemble cannot support one anyway.
    """
    if noise_seeds < 2:
        raise EnsembleError(
            "the noise floor needs at least two seeds; with one there is a "
            "number but no spread, and every pack effect would look real")
    for pack in packs:
        if not isinstance(pack, AssumptionPack):
            raise EnsembleError("packs must be AssumptionPack instances")

    tasks = [{"label": "baseline", "config": base_config, "paths": paths,
              "seed": seed + i, "dist_paths": dist_paths, "root": root}
             for i in range(noise_seeds)]
    for pack in packs:
        tasks.append({"label": pack.name, "config": pack.apply(base_config),
                      "paths": paths, "seed": seed, "dist_paths": dist_paths,
                      "root": root})

    results = _run_tasks(tasks, workers=workers, runner=runner)
    by_label = {}
    for item in results:
        by_label.setdefault(item["label"], []).append(item)

    baseline_runs = by_label.get("baseline") or []
    noise = {metric: _spread([r["metrics"].get(metric) for r in baseline_runs])
             for metric in METRICS}
    # The baseline value a pack is compared against is the run at the SAME seed
    # the packs used, not the mean across seeds: comparing a single-seed variant
    # to a multi-seed mean would fold part of the sampling spread into the
    # effect.
    anchor = next((r for r in baseline_runs if r["seed"] == seed), None)
    if anchor is None:
        raise EnsembleError("the baseline was not run at the anchor seed")

    effects = []
    for pack in packs:
        runs = by_label.get(pack.name) or []
        if not runs:
            raise EnsembleError("pack %r produced no run" % pack.name)
        metrics = runs[0]["metrics"]
        per_metric = {}
        for metric in METRICS:
            base_value = anchor["metrics"].get(metric)
            value = metrics.get(metric)
            spread = noise[metric]
            if base_value is None or value is None:
                per_metric[metric] = {
                    "value": value, "baseline": base_value, "effect": None,
                    "distinguishable": None,
                    "why": "the metric is missing on one side, so an effect "
                           "would be the whole metric rather than a change"}
                continue
            effect = value - base_value
            band = spread.get("range")
            per_metric[metric] = {
                "value": value, "baseline": base_value, "effect": effect,
                "noise_range": band,
                # Outside the baseline's own seed-to-seed range. Deliberately
                # the range and not a standard error: with three seeds a
                # standard error is a number computed from too little to mean
                # what its name suggests.
                "distinguishable": (None if band is None
                                    else abs(effect) > band),
            }
        effects.append({"pack": pack.describe(), "metrics": per_metric})

    return {
        "baseline_seed": seed,
        "paths": paths,
        "noise_seeds": [r["seed"] for r in baseline_runs],
        "sampling_noise": noise,
        "baseline_metrics": anchor["metrics"],
        "packs": effects,
        "common_random_numbers": False,
        "disclosure": (
            "This engine's inheritance and eldercare samplers are conditional, "
            "so two configs do not share a draw sequence and a variant's "
            "difference contains both the assumption's effect and the streams "
            "diverging. Effects are therefore read against the baseline's own "
            "seed-to-seed spread rather than presented as exact."),
    }


# ---------------------------------------------------------------------------
# The judgment, as a call rather than as something to remember.
# ---------------------------------------------------------------------------

def claimable(measure, *, change, seeds=(4242, 5353, 6464, 7575),
              metric: str = "value") -> dict:
    """Can this effect be claimed, or is it inside the engine's own jitter?

    `measure(seed) -> float` runs the baseline; `change` is the already-measured
    figure with whatever was switched on. Returns a verdict plus the numbers
    behind it, and never a bare boolean — "not distinguishable" and "measured
    and small" have to stay apart, which is the whole point.

    This exists because the rule kept having to be remembered. It is written in
    `LESSONS.md` (entry 6) and in the assumption-pack library's comments, and
    it still has to be re-derived by hand every time somebody adds a module:
    run the baseline at several seeds, take the spread, and refuse to call an
    effect real unless it clears that spread. Every place that has skipped it
    has produced the same artefact — a difference of a few hundredths reported
    as a finding.

    Two measured cases from this project, both of which this call would have
    settled in one line:

      * `parents.estate_share_of_care` moved median consumption by +21 on
        72,324 — 0.03%, and in the FAVOURABLE direction. Not a risk lever at
        all, and the test asserting "the two ends differ" passed anyway.
      * `parents.cost_excess_inflation` 0.01 -> 0.02 moved it by -267 against a
        floor of 1,810 at 1,200 paths and -379 against 1,088 at 10,000. Real in
        sign, unclaimable at both precisions, so the pack was not shipped.

    Deliberately NOT a statistical test. The spread across a handful of seeds
    is a crude floor and this says so rather than dressing it as a p-value:
    the question being answered is "would I see this difference between two
    runs that changed nothing", and the honest instrument for that is two runs
    that changed nothing.
    """
    if len(seeds) < 2:
        raise EnsembleError(
            "a noise floor needs at least two seeds; with one there is a "
            "number but no spread, and every effect looks real")
    baseline = [float(measure(seed)) for seed in seeds]
    floor = max(baseline) - min(baseline)
    anchor = baseline[0]
    effect = float(change) - anchor
    clears = abs(effect) > floor
    if clears:
        reason = ("effect %.4g on a noise floor of %.4g across %d seeds — "
                  "outside the spread, so it is a finding"
                  % (effect, floor, len(seeds)))
    else:
        reason = ("effect %.4g does NOT clear the noise floor of %.4g across "
                  "%d seeds. This is not 'no effect' — it is an effect this "
                  "run cannot distinguish from a draw. Say that, or raise the "
                  "path count until the floor drops below it."
                  % (effect, floor, len(seeds)))
    return {"metric": metric, "claimable": clears, "effect": effect,
            "noise_floor": floor, "baseline": baseline, "anchor": anchor,
            "with_change": float(change), "seeds": list(seeds),
            "reason": reason}
