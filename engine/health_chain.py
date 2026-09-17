"""Roadmap 11 health-state wiring without invented transition rates.

The chain owns one stable, age-indexed child stream. Each column is still
judged against the module that supplied its probability (SSA disability,
LTSS care, or the mortality table); sharing the stream does not turn those
marginals into an evidence-free correlation coefficient.

Age-indexing is deliberate. Turning one constituent module off must not move
the draws used by another constituent module, so consumers read a named cell
rather than advancing a shared cursor.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np


SEED_OFFSET = 21_000_000
DOMAINS = (
    "disability",
    "ltc_onset",
    "ltc_duration",
    "ltc_level",
    "primary_mortality",
    "spouse_mortality",
)


@dataclass(frozen=True)
class HealthChainParams:
    enabled: bool = False


@dataclass
class HealthChainPath:
    """One household path's named health transitions and absorbing deaths."""

    seed: int
    path_index: int
    first_age: int
    last_age: int
    _draws: dict = field(init=False, repr=False)
    primary_death_age: Optional[int] = field(default=None, init=False)
    spouse_death_age: Optional[int] = field(default=None, init=False)
    disability_age: Optional[int] = field(default=None, init=False)
    ltc_onset_age: Optional[int] = field(default=None, init=False)
    ltc_years: Optional[float] = field(default=None, init=False)
    ltc_level: Optional[str] = field(default=None, init=False)

    def __post_init__(self):
        if int(self.last_age) < int(self.first_age):
            raise ValueError("health-chain last_age precedes first_age")
        rng = np.random.default_rng(
            [int(self.seed), int(self.path_index), SEED_OFFSET])
        matrix = rng.random((self.last_age - self.first_age + 1,
                             len(DOMAINS)))
        self._draws = {
            domain: matrix[:, index] for index, domain in enumerate(DOMAINS)
        }

    def draw(self, domain: str, age: int) -> float:
        if domain not in self._draws:
            raise ValueError("unknown health-chain transition %r" % domain)
        if not self.first_age <= int(age) <= self.last_age:
            raise ValueError("health-chain age %s outside %s-%s" % (
                age, self.first_age, self.last_age))
        return float(self._draws[domain][int(age) - self.first_age])

    def prepare_mortality(
        self,
        primary_rate: Callable[[int], float],
        *,
        enabled: bool,
        spouse_rate: Optional[Callable[[int], float]] = None,
        spouse_age_offset: int = 0,
    ) -> None:
        """Resolve absorbing death states from the existing mortality tables."""
        if not enabled:
            return
        # Both accumulation and retirement check death at year-end, beginning
        # at start_age + 1. A draw at first_age would create a death no engine
        # year ever consumes and leave the path falsely alive thereafter.
        for age in range(self.first_age + 1, self.last_age + 1):
            if (self.primary_death_age is None
                    and self.draw("primary_mortality", age)
                    < float(primary_rate(age))):
                self.primary_death_age = age
            if (spouse_rate is not None and self.spouse_death_age is None
                    and self.draw("spouse_mortality", age)
                    < float(spouse_rate(max(1, age + spouse_age_offset)))):
                self.spouse_death_age = age
            if (self.primary_death_age is not None
                    and (spouse_rate is None
                         or self.spouse_death_age is not None)):
                break

    def alive(self, age: int, person: str = "primary") -> bool:
        death_age = (self.primary_death_age if person == "primary"
                     else self.spouse_death_age)
        return death_age is None or int(age) < int(death_age)

    def dies_at(self, age: int, person: str = "primary") -> bool:
        death_age = (self.primary_death_age if person == "primary"
                     else self.spouse_death_age)
        return death_age is not None and int(age) == int(death_age)

    def disability_draw(self, age: int) -> float:
        return self.draw("disability", age)

    def ltc_draw(self, domain: str, age: int) -> float:
        return self.draw(domain, age)

    def record_disability(self, age: Optional[int]) -> None:
        self.disability_age = None if age is None else int(age)

    def record_ltc(self, *, onset_age=None, years=None, level=None) -> None:
        self.ltc_onset_age = None if onset_age is None else int(onset_age)
        self.ltc_years = None if years is None else float(years)
        self.ltc_level = level

    def annual_state(self, age: int) -> str:
        """Return the dominant state; death absorbs every later state."""
        age = int(age)
        if not self.alive(age):
            return "dead"
        if (self.ltc_onset_age is not None and self.ltc_years is not None
                and self.ltc_onset_age <= age
                < self.ltc_onset_age + self.ltc_years):
            return "long_term_care"
        if self.disability_age is not None and age >= self.disability_age:
            return "disabled"
        return "healthy"

    def summary(self, *, medical_trajectory: bool) -> dict:
        transitions = []
        if self.disability_age is not None:
            transitions.append({"age": self.disability_age,
                                "to": "disabled", "source": "SSA"})
        if self.ltc_onset_age is not None:
            transitions.append({"age": self.ltc_onset_age,
                                "to": "long_term_care", "source": "LTSS"})
        if self.primary_death_age is not None:
            transitions.append({"age": self.primary_death_age,
                                "to": "dead", "source": "mortality_table"})
        transitions.sort(key=lambda row: (row["age"], row["to"]))
        return {
            "enabled": True,
            "stream": "age_indexed_shared_child",
            "marginals": "preserved_from_source_modules",
            "invented_health_mortality_multiplier": False,
            "medical_trajectory_driven_by_alive_state": bool(
                medical_trajectory),
            "transitions": transitions,
            "spouse_death_age": self.spouse_death_age,
        }
