"""Phase 3 · folding the decision lab's four analyses into the packet.

ROADMAP asks for goal-seek, frontier, A/B and stress test to be gathered into
the saveable DecisionPacket. They already exist -- `decision_lab.run_goalseek`,
`run_frontier`, `run_sensitivity`, `run_backtest` -- so the work is not writing
them. It is working out what they are allowed to say once they are inside a
document whose whole point is that its numbers can be re-checked.

The answer is: less than it looks, and the reason is measured.

`build_packet` refuses any precision below Standard, because a formal packet
carries a `Robust` claim (decision_packet.py:591). Standard is 10,000 paths
(app.py PRECISION_BY_PATHS). But:

  * `run_goalseek` caps at `min(paths, 2000)` (decision_lab.py:135), and 2,000
    is exactly the `quick` tier;
  * `run_frontier` caps identically (decision_lab.py:216);
  * `run_sensitivity` caps at SENS_CAP = 5,000 (decision_lab.py:16,277), which
    is not a named tier at all -- the map is exact-match on 2k/10k/30k/100k;
  * `run_backtest` is deterministic and has no path count to compare.

So **none of the four can meet the packet's own protocol**, no matter what the
caller asks for. Attaching their numbers as if they shared it would let quick
precision in through the back door of a document that had just refused it.

They are attached as CONTEXT instead: each carries its own protocol, the
specific ways it differs, and a flag saying it is not evidence for the verdict.
`assert_verdicts_unchanged` exists so that "context cannot move the verdict" is
enforced rather than intended.

One further correction is recorded here. `run_sensitivity` documents itself as
using "common random numbers: same seed everywhere" (decision_lab.py:274).
That is not true of this engine, and the codebase already knows why:
`ensemble.py` and `tests/test_attribution_crn.py` establish that conditional
samplers desynchronise the draw sequence between two configs at one seed. The
tornado's rows therefore contain sampler drift on top of the parameter effect,
and a reader who took the CRN claim at face value would read every small row as
a real sensitivity.
"""
from __future__ import annotations

import copy
from typing import Optional

#: Mirrors `app.PRECISION_BY_PATHS`. Duplicated rather than imported because
#: importing `app` pulls in the whole HTTP server; `tests/test_packet_evidence.py`
#: reads app.py's source and fails if the two ever diverge.
PRECISION_BY_PATHS = {2_000: "quick", 10_000: "standard",
                      30_000: "deep", 100_000: "official"}

#: What `build_packet` will accept. Anything else cannot carry `Robust`.
PACKET_PRECISIONS = ("standard", "official")

ANALYSIS_KINDS = ("goal_seek", "frontier", "sensitivity", "backtest", "sweep")

#: Measured facts about each analysis, with the line that fixes each one. These
#: are not configuration -- they are what the code does, and the test suite
#: checks every number here against the source.
ANALYSIS_PROTOCOLS = {
    "goal_seek": {
        "path_cap": 2_000,
        "source": "server/decision_lab.py:135",
        "seeds": 1,
        "scenario": "home",
        "deterministic": False,
        "limits": [
            "paths are capped at 2,000, which is the `quick` tier -- raising "
            "the request does not raise the run",
            "one seed, so nothing here distinguishes an effect from the "
            "engine's own sampling spread",
            "home scenario only (relocation_on=False), so no relocation-side "
            "assumption can appear",
        ],
    },
    "frontier": {
        "path_cap": 2_000,
        "source": "server/decision_lab.py:216",
        "seeds": 1,
        "scenario": "home",
        "deterministic": False,
        "limits": [
            "paths are capped at 2,000, the `quick` tier",
            "one seed; the dominance test compares points that each carry "
            "their own sampling error, so a thin frontier is partly noise",
            "home scenario only",
        ],
    },
    "sensitivity": {
        "path_cap": 5_000,
        "source": "server/decision_lab.py:16,277",
        "seeds": 1,
        "scenario": "home",
        "deterministic": False,
        "limits": [
            "paths are capped at 5,000, which is not a named precision tier "
            "at all -- it sits between `quick` and `standard`",
            "its docstring claims common random numbers; this engine cannot "
            "provide them, because conditional samplers desynchronise the "
            "draw sequence between two configs at one seed. Each tornado row "
            "therefore mixes the parameter's effect with sampler drift, and "
            "small rows should not be read as small sensitivities",
            "home scenario only",
        ],
    },
    "sweep": {
        "path_cap": 8_000,
        "source": "server/decision_lab.py:13,68",
        "seeds": 1,
        "scenario": "home",
        "deterministic": False,
        "limits": [
            "paths are capped at 8,000 per point, which is not a named tier",
            "one seed per point",
        ],
    },
    "backtest": {
        "path_cap": None,
        "source": "server/decision_lab.py:328 -> engine_adapter.backtest",
        "seeds": 1,
        "scenario": "home",
        "deterministic": True,
        "limits": [
            "stylized adverse openings, NOT literal index history -- the "
            "sequences are constructed, so this answers 'how does the "
            "withdrawal rule behave under a bad start', not 'what would have "
            "happened in 1966'",
            "no Monte Carlo path count, so it cannot be compared to the "
            "packet's precision at all",
        ],
    },
}


class EvidenceError(RuntimeError):
    """An analysis this module will not present as more than it is."""


def precision_of(paths) -> Optional[str]:
    """The named tier for a path count, or `None`.

    `None` is the common case for these analyses and is the point: a run at
    5,000 paths has no tier, and saying "between quick and standard" out loud
    is better than rounding it to whichever neighbour flatters it.
    """
    if paths is None:
        return None
    return PRECISION_BY_PATHS.get(int(paths))


def admissibility(kind: str, packet_protocol: dict) -> dict:
    """Whether an analysis of this kind could ever meet the packet's protocol.

    Answered from the analysis's cap, not from the paths a caller happened to
    request: asking `run_goalseek` for 10,000 paths returns a 2,000-path run,
    so the request is not what the numbers were computed at.
    """
    if kind not in ANALYSIS_PROTOCOLS:
        raise EvidenceError(
            "unknown analysis kind %r; the lab provides %s"
            % (kind, ", ".join(sorted(ANALYSIS_PROTOCOLS))))
    spec = ANALYSIS_PROTOCOLS[kind]
    packet_precision = str(packet_protocol.get("precision") or "")
    differences = []

    cap = spec["path_cap"]
    if spec["deterministic"]:
        differences.append(
            "this analysis is deterministic and has no path count, so it "
            "cannot be compared to the packet's %s precision" % packet_precision)
    else:
        cap_tier = precision_of(cap)
        if cap_tier in PACKET_PRECISIONS:
            pass
        elif cap_tier is None:
            differences.append(
                "capped at %s paths, which is not a named precision tier; the "
                "packet was computed at %s" % ("{:,}".format(cap),
                                               packet_precision))
        else:
            differences.append(
                "capped at %s paths (`%s`); the packet was computed at %s, and "
                "a formal packet refuses anything below Standard"
                % ("{:,}".format(cap), cap_tier, packet_precision))

    if spec["seeds"] < 2 and not spec["deterministic"]:
        # Skipped for a deterministic analysis: it has no draws, so "one seed"
        # is not a limitation of it and listing it would pad the differences
        # with a complaint that does not apply.
        differences.append(
            "run at a single seed, so it carries no measure of this engine's "
            "own sampling spread")
    if spec["scenario"] == "home" and packet_protocol.get("scenario_coverage"):
        differences.append(
            "home scenario only, while the packet's axes covered %s"
            % ", ".join(packet_protocol["scenario_coverage"]))

    return {"kind": kind, "meets_packet_protocol": not differences,
            "differences": differences, "protocol": copy.deepcopy(spec)}


def attach_analyses(packet: dict, analyses: list) -> dict:
    """Fold the lab's analyses into the packet as context, never as evidence.

    Each entry is `{"kind": ..., "result": ...}`. The result is stored as given
    -- these are the lab's own payloads and rewriting them here would create a
    second shape to keep in sync -- but it is stored under a section that says
    what it is and what it is not.
    """
    if "alternatives" not in packet:
        raise EvidenceError("not a decision packet")
    attached, kinds = [], set()
    for entry in analyses:
        kind = (entry or {}).get("kind")
        if kind in kinds:
            raise EvidenceError(
                "two %r analyses attached; the packet would show one of them "
                "and there would be no way to tell which" % kind)
        kinds.add(kind)
        verdict = admissibility(kind, packet.get("protocol") or {})
        attached.append({**verdict, "result": (entry or {}).get("result")})
    packet["supporting_analyses"] = {
        "analyses": attached,
        "is_evidence_for_verdict": False,
        "disclosure": (
            "These are the decision lab's own runs, kept with the packet so a "
            "reader can see the shape of the problem. None of them meets this "
            "packet's protocol -- goal-seek and the frontier are hard-capped "
            "at the `quick` tier, the tornado at a path count with no tier at "
            "all, and the backtest is deterministic -- so none of them "
            "contributed to any verdict above. Each carries its own limits."),
    }
    return packet


def assert_verdicts_unchanged(before: dict, after: dict) -> None:
    """Enforce the rule the disclosure states.

    Written as a check rather than a comment because "context does not affect
    the conclusion" is exactly the kind of promise that stays true until
    someone adds a convenient line, and the packet is the one document where
    that would be invisible.
    """
    def verdicts(packet):
        return [(a.get("alternative", {}).get("name"), a.get("verdict"))
                for a in packet.get("alternatives") or []]

    if verdicts(before) != verdicts(after):
        raise EvidenceError(
            "attaching supporting analyses changed a verdict: %s became %s. "
            "These runs are below the packet's protocol and must not reach a "
            "conclusion." % (verdicts(before), verdicts(after)))


def head_to_head(alternatives: list, goal) -> list:
    """ROADMAP's A/B, between alternatives already judged at packet protocol.

    Unlike the four lab analyses this IS admissible, because it compares
    numbers the packet itself computed. It compares only at the anchor and only
    on the objective, and it reports ties rather than breaking them: two
    alternatives that land on the same objective differ somewhere the goal did
    not ask about, and picking one for the user there would be the utility
    score ROADMAP forbids.
    """
    pairs = []
    for i, left in enumerate(alternatives):
        for right in alternatives[i + 1:]:
            lv = (left.get("gain_and_cost") or {})
            rv = (right.get("gain_and_cost") or {})
            lname = left.get("alternative", {}).get("name")
            rname = right.get("alternative", {}).get("name")
            lo = _objective_delta(lv, goal.objective_metric)
            ro = _objective_delta(rv, goal.objective_metric)
            if lo is None or ro is None:
                winner, why = None, ("one side has no measurement on the "
                                     "objective, so they cannot be compared")
            elif lo == ro:
                winner, why = None, ("identical on the objective; they differ "
                                     "only where the goal did not ask, and "
                                     "choosing there is the user's call")
            else:
                better_left = goal.better(lo, ro)
                winner = (lname if better_left else rname)
                why = "further on %s at the anchor" % goal.objective_metric
            pairs.append({"left": lname, "right": rname, "winner": winner,
                          "left_objective_delta": lo,
                          "right_objective_delta": ro, "why": why})
    return pairs


def _objective_delta(gain_and_cost: dict, metric: str):
    for bucket in ("gains", "costs", "unchanged"):
        for entry in gain_and_cost.get(bucket) or []:
            if entry.get("metric") == metric:
                return entry.get("delta")
    return None
