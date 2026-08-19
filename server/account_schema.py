"""What an account IS, declared rather than hardcoded.

Roadmap 7.0 Phase 1 (idea-bank S8). This engine has always known exactly four
accounts by name -- `pretax_401k`, `roth_ira`, `hsa`, `taxable` -- across 492
references in 22 files. That is what makes it a US tool with a relocation
feature rather than a global engine, and generalising it is the version's
whole point.

**This module declares. It does not rewire.** Phase 1 delivers the vocabulary
and proves, dimension by dimension, that it says what the code already does.
Phase 2 is where the engine reads it. Splitting them is deliberate: a
representation nobody proved equivalent is a bad foundation for new semantics,
and the test beside this file is the proof.

**Six dimensions, not the three the idea bank listed.** Measured against
`withdraw_with_seasoning_v94` and the tax path, the four buckets differ by
order, withdrawal rate, early-withdrawal penalty, seasoning, contribution
limit and forced distribution. The two the idea bank missed are the awkward
ones:

* **Seasoning** is not "how withdrawals are taxed" but "this money cannot be
  touched yet" -- the Roth ladder's five-year rule, which the engine already
  models via `roth_locked_amount`. A schema without it cannot express a
  feature that already ships.
* **Ordering** was hardcoded in a function body. User ruling 2026-08-18: the
  type carries a DEFAULT and a strategy may override it. The default belongs
  to the jurisdiction because tax law makes some orders obviously better; the
  override belongs to the person. An absent override is today's order, which
  is what keeps this version's bit-identity gate intact.
"""
from __future__ import annotations

from typing import Optional

#: How a withdrawal from this account is taxed, named by which rate on
#: `TaxParams` it reads. Declared as a NAME rather than a number: the numbers
#: are a plan's settings and belong in config, while which rate applies is a
#: property of the account type.
RATE_TAXABLE = "withdrawal_tax_taxable"
RATE_TRADITIONAL = "withdrawal_tax_traditional"
RATE_ROTH = "withdrawal_tax_roth"
RATE_HSA = "withdrawal_tax_hsa"
#: No rate is applied at all -- not "a rate that happens to be zero".
#: The Roth path in `withdraw_with_seasoning_v94` takes the gross amount and
#: subtracts it, never reading a rate. Declaring RATE_ROTH there would have
#: been true in VALUE (it is 0.0) and false in MECHANISM, and phase 2 would
#: have started applying a multiplication the engine never performed.
RATE_NONE = None


class AccountType:
    """One account type, in six dimensions.

    `field` is the attribute name on `AccountStack` this type currently maps
    to. It exists so Phase 1 can be checked against today's engine and so
    Phase 2 has a migration path; a country pack's types will not have one,
    and that asymmetry is deliberate rather than an oversight.
    """

    def __init__(self, key: str, *, field: Optional[str], jurisdiction: str,
                 default_order: int, withdrawal_rate: Optional[str],
                 early_penalty_rate: float = 0.0,
                 early_penalty_age: Optional[float] = None,
                 seasoned: bool = False,
                 contribution_limited: bool = False,
                 forced_distribution: bool = False,
                 note_cn: str = "", note_en: str = ""):
        self.key = key
        self.field = field
        self.jurisdiction = jurisdiction
        #: Lower is drawn first. A DEFAULT -- see the module docstring.
        self.default_order = default_order
        self.withdrawal_rate = withdrawal_rate
        self.early_penalty_rate = early_penalty_rate
        self.early_penalty_age = early_penalty_age
        #: Money that cannot be touched until it has aged. True here means the
        #: engine tracks a locked amount for this type.
        self.seasoned = seasoned
        self.contribution_limited = contribution_limited
        self.forced_distribution = forced_distribution
        self.note_cn = note_cn
        self.note_en = note_en

    def __repr__(self) -> str:                                # pragma: no cover
        return "AccountType(%r, order=%d)" % (self.key, self.default_order)


#: The United States, declared. Every value here is checked against the engine
#: by `tests/test_account_schema.py` rather than asserted -- a declaration that
#: merely looks right is the failure this whole phase exists to prevent.
US_ACCOUNT_TYPES = (
    AccountType(
        "us_taxable", field="taxable", jurisdiction="US", default_order=1,
        withdrawal_rate=RATE_TAXABLE,
        note_cn="应税账户。先取它，因为它没有年龄门槛也没有罚金 —— "
                "代价是它一路上都在交税，那部分由股息拖累与成本基础单独建模。",
        note_en="The taxable account. Drawn first because it has no age gate "
                "and no penalty; the cost is that it is taxed along the way, "
                "which the dividend drag and cost basis model separately."),
    AccountType(
        "us_pretax_401k", field="pretax_401k", jurisdiction="US",
        default_order=2, withdrawal_rate=RATE_TRADITIONAL,
        early_penalty_rate=0.10, early_penalty_age=59.5,
        contribution_limited=True, forced_distribution=True,
        note_cn="税前 401(k)。59.5 岁前取要付 10% 罚金，且到龄后必须开始提取（RMD）——"
                "「必须取」和「可以取」是两回事，这个 schema 分开表达它们。",
        note_en="The pre-tax 401(k). Withdrawing before 59.5 costs a 10% "
                "penalty, and past a certain age withdrawals become "
                "mandatory. 'May draw' and 'must draw' are different things "
                "and this schema keeps them apart."),
    AccountType(
        "us_hsa", field="hsa", jurisdiction="US", default_order=3,
        withdrawal_rate=RATE_HSA, contribution_limited=True,
        note_cn="HSA。合规医疗支出下提取免税，因此排在 Roth 之前但在应税与税前之后。",
        note_en="The HSA. Withdrawals for qualified medical costs are "
                "untaxed, which is why it sits ahead of the Roth but behind "
                "taxable and pre-tax."),
    AccountType(
        "us_roth_ira", field="roth_ira", jurisdiction="US", default_order=4,
        withdrawal_rate=RATE_NONE, seasoned=True, contribution_limited=True,
        note_cn="Roth IRA。最后取，因为它增长免税 —— 留得越久越值钱。"
                "**它是唯一带熟成的类型**：转换进来的钱五年内不能动，"
                "而那不是税率问题，是可及性问题。",
        note_en="The Roth IRA. Drawn last because it grows untaxed, so time "
                "in it is worth more than time anywhere else. It is the one "
                "SEASONED type: converted money cannot be touched for five "
                "years, which is not a question of rate but of access."),
)

BY_KEY = {account.key: account for account in US_ACCOUNT_TYPES}
BY_FIELD = {account.field: account for account in US_ACCOUNT_TYPES
            if account.field}


def default_order(jurisdiction: str = "US") -> tuple:
    """The draw order a plan uses when it has not asked for another."""
    types = [a for a in US_ACCOUNT_TYPES if a.jurisdiction == jurisdiction]
    return tuple(a.key for a in sorted(types, key=lambda a: a.default_order))


def resolve_order(override: Optional[list] = None,
                  jurisdiction: str = "US") -> tuple:
    """The order actually used: the override when given, else the default.

    An absent override is today's behaviour exactly, which is what lets this
    version's bit-identity gate hold while the capability arrives.

    An override that names an unknown account, or omits one, is refused rather
    than silently completed: a partial order would draw the missing account
    somewhere nobody chose, and "somewhere nobody chose" is how a plan quietly
    stops meaning what its owner thinks it means.
    """
    default = default_order(jurisdiction)
    if not override:
        return default
    named = tuple(str(key) for key in override)
    unknown = [key for key in named if key not in BY_KEY]
    if unknown:
        raise ValueError(
            "withdrawal order names accounts that do not exist: %s" % (unknown,))
    if len(set(named)) != len(named):
        raise ValueError("withdrawal order repeats an account: %s" % (named,))
    missing = [key for key in default if key not in named]
    if missing:
        raise ValueError(
            "withdrawal order leaves out %s -- a partial order would draw "
            "them somewhere nobody chose" % (missing,))
    return named
