"""4.0 Phase 2 · declarative glide schedules, and comparing defences honestly.

The engine's existing `GlidePath` is a single linear segment: one start age,
one end age, one equity weight at each. That expresses rising equity and a
plain de-risking ramp, but it cannot express a **bond tent** — equity high
during accumulation, dipping around retirement, rising again afterwards —
because that needs two segments with a hinge between them.

This module supplies multi-segment schedules **behind the same interface**.
The engine consumes exactly two things, `equity_pct(age)` and `name`
(fire_v9_3_model.py:528, :542, :747), so a piecewise schedule is a drop-in and
not one line of engine code changes. That matters beyond convenience: an engine
change would have to re-earn bit-identity, and this way there is nothing to
re-earn.

**The comparison is the deliverable, not the schedules.** ROADMAP is explicit
that "「防御策略对你差异不 material、无需折腾」这个结论本身就是产品价值". So
`compare` is built to be able to reach that conclusion rather than treating it
as a failure to find something — a tool that can only ever recommend action
will always recommend action.

**Directional contracts must be two-sided.** A defence that helps in a bear
sequence should also *lag* in a long bull. Testing only the favourable
direction cannot tell a working defence from one wired to nothing, which is the
same shape as an assumption pack that reaches no leaf and reports an effect of
exactly zero.
"""
from __future__ import annotations

from typing import Optional


class ScheduleError(ValueError):
    """A schedule this module refuses rather than interpolates through."""


class GlideSchedule:
    """Piecewise-linear equity weight by age.

    Deliberately interface-compatible with `GlidePath`: `equity_pct(age)` and
    `name`. Points are `(age, equity_weight)`, ordered, at least two of them.
    Before the first age the first weight holds; after the last, the last —
    flat extrapolation rather than continuing a trend off the end of the
    schedule, which is how a de-risking ramp ends up short equity at 100.
    """

    __slots__ = ("name", "points", "note")

    def __init__(self, name: str, points, note: str = ""):
        rows = [(float(age), float(weight)) for age, weight in points]
        if len(rows) < 2:
            raise ScheduleError(
                "a schedule needs at least two points; one point is a constant "
                "allocation, which `GlidePath` already expresses")
        for index in range(1, len(rows)):
            if rows[index][0] <= rows[index - 1][0]:
                raise ScheduleError(
                    "ages must strictly increase; got %g after %g"
                    % (rows[index][0], rows[index - 1][0]))
        for age, weight in rows:
            if not 0.0 <= weight <= 1.0:
                raise ScheduleError(
                    "equity weight at age %g is %g; it is a share, so it lives "
                    "in [0, 1] and a schedule that leaves it is a typo rather "
                    "than leverage" % (age, weight))
        self.name = name
        self.points = tuple(rows)
        self.note = note

    def equity_pct(self, age) -> float:
        age = float(age)
        points = self.points
        if age <= points[0][0]:
            return points[0][1]
        if age >= points[-1][0]:
            return points[-1][1]
        for index in range(1, len(points)):
            left_age, left_weight = points[index - 1]
            right_age, right_weight = points[index]
            if age <= right_age:
                span = right_age - left_age
                t = (age - left_age) / span
                return left_weight + t * (right_weight - left_weight)
        return points[-1][1]                      # unreachable; kept total

    def describe(self) -> dict:
        return {"name": self.name, "note": self.note,
                "points": [{"age": age, "equity": weight}
                           for age, weight in self.points]}


def bond_tent(fire_age: float, *, floor: float = 0.55,
              accumulation_equity: float = 1.0,
              late_equity: float = 0.95, ramp_years: float = 10.0,
              lead_years: float = 10.0) -> GlideSchedule:
    """Down into retirement, back up afterwards.

    The shape Kitces and Pfau describe: equity is reduced approaching the
    retirement date, held low through the years when a bad sequence does the
    most damage, then allowed to rise again once that window has passed. It is
    the one common defence the single-segment `GlidePath` cannot express at
    all, which is why this module exists.
    """
    if ramp_years <= 0 or lead_years <= 0:
        raise ScheduleError("the ramps must have positive length")
    return GlideSchedule(
        "Bond tent",
        [(fire_age - lead_years, accumulation_equity),
         (fire_age, floor),
         (fire_age + ramp_years, late_equity)],
        note="equity is cut approaching retirement and allowed back up once "
             "the sequence-risk window has passed")


def rising_equity(fire_age: float, *, start: float = 0.6, end: float = 1.0,
                  years: float = 20.0) -> GlideSchedule:
    """Start conservative at retirement and climb. Expressible by `GlidePath`
    too; provided here so a comparison can hold every arm in one vocabulary."""
    return GlideSchedule("Rising equity",
                         [(fire_age, start), (fire_age + years, end)],
                         note="conservative at retirement, climbing after")


def derisk(fire_age: float, *, start: float = 1.0, end: float = 0.5,
           years: float = 15.0) -> GlideSchedule:
    """The conventional ramp down. Also `GlidePath`-expressible."""
    return GlideSchedule("De-risking ramp",
                         [(fire_age, start), (fire_age + years, end)],
                         note="the conventional path: equity falls with age "
                              "and stays down")


def constant(weight: float, *, name: Optional[str] = None) -> GlideSchedule:
    """The do-nothing arm. Every comparison needs it, because "this defence
    beats that defence" is not the question a user is asking — "is any of this
    worth doing" is."""
    return GlideSchedule(name or ("Constant %.0f%% equity" % (weight * 100)),
                         [(0.0, weight), (120.0, weight)],
                         note="no glide at all; the arm that answers whether "
                              "any defence is worth the complexity")


TEMPLATES = ("bond_tent", "rising_equity", "derisk", "constant")
