"""Compile one fixed-payment student loan into a finite nominal schedule.

The inputs are user facts.  This module carries no rate, balance, payment, or
forgiveness assumption of its own.  It deliberately compiles only the modeled
horizon: a payment below monthly interest is allowed to grow the balance, so an
"until paid" loop would not terminate for a valid plan.
"""
from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class StudentDebtConfig:
    enabled: bool = False
    balance: float = 0.0
    annual_rate: float = 0.0
    monthly_payment: float = 0.0


DEFAULTS = {
    field: getattr(StudentDebtConfig(), field)
    for field in ("enabled", "balance", "annual_rate", "monthly_payment")
}


class StudentDebtError(ValueError):
    """A student-debt input cannot be represented by this first slice."""

    def __init__(self, message: str, field: str):
        super().__init__(message)
        self.field = field


@dataclass(frozen=True)
class StudentDebtSchedule:
    opening_balance_nominal: float
    monthly_payment_nominal: float
    annual_payments_nominal: tuple[float, ...]
    annual_interest_nominal: tuple[float, ...]
    end_balances_nominal: tuple[float, ...]

    def payment_for_year(self, year_index: int) -> float:
        if 0 <= year_index < len(self.annual_payments_nominal):
            return self.annual_payments_nominal[year_index]
        return 0.0

    def balance_after_years(self, years: int) -> float:
        if years <= 0:
            return self.opening_balance_nominal
        if years <= len(self.end_balances_nominal):
            return self.end_balances_nominal[years - 1]
        return self.end_balances_nominal[-1] if self.end_balances_nominal else 0.0


def _number(raw: dict, key: str) -> float:
    value = raw.get(key, DEFAULTS[key])
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise StudentDebtError(
            f"student_debt.{key} must be a number", f"student_debt.{key}")
    value = float(value)
    if not math.isfinite(value):
        raise StudentDebtError(
            f"student_debt.{key} must be finite", f"student_debt.{key}")
    if value < 0.0:
        raise StudentDebtError(
            f"student_debt.{key} cannot be negative", f"student_debt.{key}")
    return value


def compile_schedule(raw: dict | None, horizon_years: int) -> StudentDebtSchedule | None:
    """Return the exact monthly schedule, or ``None`` when the module is off."""
    raw = dict(raw or {})
    if not bool(raw.get("enabled", False)):
        return None
    if isinstance(horizon_years, bool) or not isinstance(horizon_years, int) or horizon_years < 1:
        raise StudentDebtError(
            "student debt needs a positive whole-year model horizon", "state")

    balance = _number(raw, "balance")
    annual_rate = _number(raw, "annual_rate")
    monthly_payment = _number(raw, "monthly_payment")
    if balance <= 0.0:
        raise StudentDebtError(
            "student_debt.balance must be greater than zero when enabled",
            "student_debt.balance")
    if monthly_payment <= 0.0:
        raise StudentDebtError(
            "student_debt.monthly_payment must be greater than zero when enabled",
            "student_debt.monthly_payment")
    if annual_rate > 1.0:
        raise StudentDebtError(
            "student_debt.annual_rate must be entered as a decimal (6% = 0.06)",
            "student_debt.annual_rate")

    monthly_rate = annual_rate / 12.0
    annual_payments = []
    annual_interest = []
    end_balances = []
    for _year in range(horizon_years):
        paid_this_year = 0.0
        interest_this_year = 0.0
        for _month in range(12):
            if balance <= 0.0:
                break
            interest = balance * monthly_rate
            due = balance + interest
            paid = min(monthly_payment, due)
            balance = due - paid
            if balance < 1e-9:
                balance = 0.0
            paid_this_year += paid
            interest_this_year += interest
        annual_payments.append(paid_this_year)
        annual_interest.append(interest_this_year)
        end_balances.append(balance)

    return StudentDebtSchedule(
        opening_balance_nominal=float(_number(raw, "balance")),
        monthly_payment_nominal=monthly_payment,
        annual_payments_nominal=tuple(annual_payments),
        annual_interest_nominal=tuple(annual_interest),
        end_balances_nominal=tuple(end_balances),
    )
