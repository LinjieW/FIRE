"""Where every accumulation-phase number came from, one entry each.

Roadmap 10.0 Phase 0. `correlation_registry.py` already grades the evidence
behind RELATIONSHIPS between modules. Nothing graded the SCALARS, and the
scalars are where this engine quietly asserts things about a user's life.

Measured before this module was written (2026-08-22):

  * `engine/fire_v8_model.py` still opens with one real person's compensation
    -- `Next career level: Associate`, `$170,000`, `240 hrs x $93.75`, `the
    analyst's CurrEmployer plan matches 6%`. `default_config()` de-identifies
    the salary and spending but passes `_gd(PromotionParams)` through
    unchanged, so **every new user's plan projects a promotion to a $170,000
    Associate role in years 2-5 with a 15-25% bonus** unless they edit it.
  * `layoff.bad_year_multiplier = 3.0` is already graded `bare` next door, in
    the correlation registry. The other seven layoff scalars are not graded
    anywhere.
  * `state.contrib_growth = 0.035` was defined once and read nowhere, while
    sitting in `default_config()` as an editable leaf. Registering it is what
    got it looked at; ruling U28 then removed the field.

**The split this module exists to force.** Two kinds of number are mixed
together in the same dataclasses today, and they need opposite treatment:

  USER_FACT       Cannot have a general source, because it differs per person:
                  your employer's match, your promotion window, your industry's
                  bonus band. These must be user inputs, and their defaults must
                  be neutral or declared as examples -- never one real person's
                  actual figures.
  SOURCEABLE      A measurable external quantity: statutory limits, IRS
                  indexation history, age-earnings profiles, disability
                  incidence. These should ship as dated pack data with a
                  citation, not as a literal in a dataclass.
  MODELLING       A deliberate choice with no external referent (a horizon, a
                  stream offset). Honest as a literal, but it still has to say
                  it is a choice rather than a measurement.

Grading is separate from the stance, and reuses the neighbouring vocabulary so
one reader learns one scheme.

This module is pure data plus assertions. It reads no config and runs no
engine; `tests/test_assumption_registry.py` is what makes it binding.
"""
from __future__ import annotations

from typing import Optional

import correlation_registry as CORRELATION

# ---- stances -------------------------------------------------------------
#: A fact about one individual. No general source can exist.
USER_FACT = "user_fact"
#: An external quantity somebody has measured, or could.
SOURCEABLE = "sourceable"
#: A modelling choice with no external referent.
MODELLING = "modelling_choice"
#: Machinery, not an assumption about the world (stream offsets, indices).
MECHANISM = "mechanism"

STANCES = (USER_FACT, SOURCEABLE, MODELLING, MECHANISM)

# ---- provenance grades, shared with the correlation registry -------------
CITED = CORRELATION.CITED
EXPLAINED_UNCITED = CORRELATION.EXPLAINED_UNCITED
BARE = CORRELATION.BARE
NOT_A_NUMBER = CORRELATION.NOT_A_NUMBER

GRADES = (CITED, EXPLAINED_UNCITED, BARE, NOT_A_NUMBER)


class Assumption:
    """One numeric default, and what is known about where it came from."""

    def __init__(self, path: str, stance: str, grade: str, *,
                 code_ref: str, current_default=None,
                 external_source: str = "", evidence_gap: str = "",
                 carries_identity: bool = False,
                 note_cn: str = "", note_en: str = ""):
        assert stance in STANCES, stance
        assert grade in GRADES, grade
        assert code_ref, "%s has no stable code evidence" % path
        if stance == SOURCEABLE and grade == CITED:
            assert external_source, "%s claims a citation without naming it" % path
        if grade in (BARE, EXPLAINED_UNCITED):
            assert evidence_gap, (
                "%s is ungraded evidence with no stated gap -- say what is "
                "missing, or grade it honestly" % path)
        if carries_identity:
            assert stance == USER_FACT, (
                "%s carries a real person's figure but is not marked as a "
                "user fact" % path)
        self.path = path
        self.stance = stance
        self.grade = grade
        self.code_ref = code_ref
        self.current_default = current_default
        self.external_source = external_source
        self.evidence_gap = evidence_gap
        #: True when the shipped default is traceable to one real individual.
        #: This is the flag Roadmap 10.0 exists to drive to zero.
        self.carries_identity = carries_identity
        self.note_cn = note_cn
        self.note_en = note_en


def _a(path, stance, grade, **kw):
    return Assumption(path, stance, grade, **kw)


_V8 = "engine/fire_v8_model.py"

REGISTRY = {entry.path: entry for entry in (
    # ---- compensation: one person's pay, shipped as the model's -----------
    _a("contributions.base_salary_pre", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=130_000,
       carries_identity=True,
       evidence_gap="the engine default is the calibration individual's actual "
                    "salary; default_config() overrides it to 125,000 but any "
                    "caller that does not go through default_config() gets the "
                    "real figure",
       note_cn="一个真人的实际薪资。必须是用户输入，默认值不该是任何具体个人的数字。",
       note_en="One real person's actual salary. Must be a user input; the "
               "default should not be any specific individual's figure."),
    _a("contributions.bonus_pre", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=5_000,
       carries_identity=True,
       evidence_gap="same individual as base_salary_pre",
       note_cn="同上，来自同一个人。", note_en="Same individual as above."),
    _a("contributions.bonus_pct_min_pre", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=0.0,
       evidence_gap="user's own W-2 compensation range; zero is dormant while "
                    "bonus_mode_pre is fixed_amount",
       note_cn="用户自己的 W-2 奖金/佣金区间下限；默认模式不读取它。",
       note_en="User's own W-2 bonus/commission lower bound; dormant by default."),
    _a("contributions.bonus_pct_max_pre", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=0.0,
       evidence_gap="user's own W-2 compensation range; no industry band ships",
       note_cn="用户自己的 W-2 奖金/佣金区间上限；不附带行业默认。",
       note_en="User's own W-2 bonus/commission upper bound; no industry default."),
    _a("contributions.self_employed_profit_factor_min", USER_FACT,
       EXPLAINED_UNCITED, code_ref=_V8 + ":V8ContributionParams",
       current_default=1.0,
       evidence_gap="user's own business downside bound; fixed mode is default",
       note_cn="用户自己的经营净利润下界；默认固定模式不读取。",
       note_en="User's own business-profit downside bound; dormant by default."),
    _a("contributions.self_employed_profit_factor_max", USER_FACT,
       EXPLAINED_UNCITED, code_ref=_V8 + ":V8ContributionParams",
       current_default=1.0,
       evidence_gap="user's own business upside bound; no industry volatility ships",
       note_cn="用户自己的经营净利润上界；不附带行业波动率。",
       note_en="User's own business-profit upside bound; no industry volatility."),
    _a("contributions.ot_income_pre", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=22_500,
       carries_identity=True,
       evidence_gap="the code comment states the derivation outright: "
                    "'240 hrs x $93.75' -- one person's overtime rate",
       note_cn="代码注释直接写着 240 小时 × $93.75 —— 一个人的加班时薪。",
       note_en="The comment states it outright: 240 hrs x $93.75."),
    _a("contributions.match_rate", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=0.06,
       carries_identity=True,
       evidence_gap="the code names the employer: 'the analyst's CurrEmployer "
                    "plan matches 6% x (base + OT)'. Match rates vary by "
                    "employer and no general default is defensible",
       note_cn="代码点名了雇主。匹配率因雇主而异，没有可辩护的通用默认值。",
       note_en="The code names the employer. Match rates vary; no general "
               "default is defensible."),
    _a("contributions.salary_growth_pre", SOURCEABLE, BARE,
       code_ref=_V8 + ":V8ContributionParams", current_default=0.035,
       evidence_gap="a single real growth rate for an entire career. Wage "
                    "growth by age is a measured empirical regularity "
                    "(age-earnings profiles are steep early and flat after "
                    "~45), so unlike the entries above this one CAN be sourced "
                    "-- it simply is not",
       note_cn="整段职业生涯一个增速。工资增速随年龄的形状是被测量过的，"
               "所以这一条**是可以有出处的**，只是现在没有。",
       note_en="One growth rate for a whole career. Age-earnings profiles are "
               "measured, so this one CAN be sourced -- it just is not."),
    _a("contributions.marginal_tax_pre", USER_FACT, BARE,
       code_ref=_V8 + ":compute_contributions_for_year", current_default=0.24,
       evidence_gap="NO LONGER THE DEFAULT POSTURE. Since the schedule "
                    "landed this is read only when the user sets "
                    "contributions.tax_model to 'flat', which is how somebody "
                    "states an effective rate off their own return -- a fact, "
                    "not a guess, so the stance moved from sourceable to "
                    "user_fact. The GRADE stays bare on purpose: the shipped "
                    "0.24 still has no source, and anybody who selects 'flat' "
                    "without replacing it gets the same unsourced number. "
                    "Re-grading it here would have banked a win this slice "
                    "did not earn. The measurement that condemned it as a "
                    "DEFAULT is kept below, because it is the reason the "
                    "default changed. Applied as a constant regardless of "
                    "income. Measured "
                    "2026-08-22 and the result was NOT what it looked like: "
                    "24% of (gross - pretax deferrals) tracks federal + FICA "
                    "to within $223/yr at $150,000, the calibration gross. It "
                    "is a decent ALL-IN proxy that happens to be tuned to one "
                    "income, and it drifts both ways from there -- over-taxing "
                    "by ~$4,100/yr at $45,000-$60,000 and under-taxing by "
                    "~$3,535/yr at $250,000. Replacing it with the pack's real "
                    "brackets ALONE would be worse, not better: it would drop "
                    "payroll tax entirely and hand every plan a systematic "
                    "optimistic bias (+$188,892 terminal p50 on the shipped "
                    "default, almost all of it the missing FICA). An honest "
                    "fix needs brackets AND FICA. Both are now in the pack "
                    "and wired: tax_model='schedule' is the default. NOTE the "
                    "figures in this entry were measured at one deferral "
                    "assumption; the slice that shipped the schedule re-took "
                    "them at the deferral the engine actually books and got "
                    "$373/$4,138/$36,344 at $150k/$45k/$500k. Same "
                    "phenomenon, and those are the ones a plan gets",
       note_cn="**与收入无关的常数**,但它不是随手写的:实测 24% 作用于"
               "(gross − 税前递延)后,在 $150,000(校准收入)处与「联邦 + FICA」"
               "只差 $223/年。它是一个**调在某一个收入上的全口径近似**,"
               "离开那个点两边都偏 —— $45k–$60k 多收约 $4,100/年,$250k 少收约 $3,535/年。"
               "**只换成真实税率表会更糟**:那等于把工资税整个丢掉,给每个计划一个"
               "系统性乐观偏差。诚实的修法要同时有税率表和 FICA —— **两者现已入 pack 并接线,"
               "`tax_model=\"schedule\"` 已是默认**。这个平率现在只在用户主动选 `flat` 时读,"
               "那时它是用户从自己报税表上读出来的事实,不再是猜测。",
       note_en="A constant regardless of income -- but not a careless one. "
               "Measured: 24% of (gross minus pretax deferrals) tracks federal "
               "+ FICA to within $223/yr at $150,000, the calibration gross. "
               "It is an all-in proxy tuned to one income, drifting both ways "
               "from there. Swapping in the pack's real brackets ALONE would "
               "be worse -- it drops payroll tax and biases every plan "
               "optimistic. An honest fix needs brackets AND FICA -- both are "
               "now in the pack and wired, and the schedule is the default. "
               "This rate is read only when a user selects 'flat', where it "
               "is their own effective rate rather than anyone's guess."),
    _a("contributions.irs_limit_growth", SOURCEABLE, BARE,
       code_ref=_V8 + ":V8ContributionParams", current_default=0.03,
       evidence_gap="contribution limits are indexed to CPI in statutory "
                    "increments, not to a flat 3% forever. Over a 25-year "
                    "accumulation this compounds to 2.09x; if realised "
                    "indexation runs nearer 2%, contribution room is "
                    "overstated by roughly a quarter",
       note_cn="缴款限额按 CPI 分档指数化，不是永远 3%。25 年复利 2.09×；"
               "若实际指数化接近 2%，缴款额度被高估约四分之一。",
       note_en="Limits are CPI-indexed in statutory steps, not a flat 3% "
               "forever. Over 25 years this compounds to 2.09x."),
    _a("contributions.pretax_401k_limit_y1", SOURCEABLE, EXPLAINED_UNCITED,
       code_ref="engine/rule_pack_us_offline.json", current_default=24_500.0,
       evidence_gap="a statutory figure that belongs in the dated rule pack "
                    "with a citation rather than as a dataclass literal",
       note_cn="法定数字，应随带 vintage 的 rule pack 发布。",
       note_en="A statutory figure; belongs in the dated rule pack."),
    _a("contributions.gov_457b_y1", USER_FACT, NOT_A_NUMBER,
       code_ref="server/engine_adapter.py", current_default=0.0,
       note_cn="用户自己今年实际缴进政府 457(b) 的金额，不是法定上限。"
               "**零是刻意的中性值**：绝大多数人没有这个账户，而「没有」和"
               "「有但没缴」在结果上应当一样。上界不是假设 —— "
               "§457(e)(15) 的 2026 年限额在 dated pack 里，"
               "超过会被**点名拒绝**而不是悄悄削平。",
       note_en="What the user actually defers into a governmental 457(b) this "
               "year, not the statutory cap. Zero is deliberately neutral: "
               "most people have no such account, and 'none' and 'have one "
               "but contributed nothing' should look the same in the result. "
               "The ceiling is not an assumption -- the 2026 section "
               "457(e)(15) limit lives in the dated pack and a figure above "
               "it is refused by name rather than quietly capped."),
    _a("contributions.roth_ira_limit_y1", SOURCEABLE, EXPLAINED_UNCITED,
       code_ref="engine/rule_pack_us_offline.json", current_default=7_500.0,
       evidence_gap="statutory, and separately: no income phase-out is "
                    "applied, so a high earner is credited a contribution "
                    "they are not legally allowed to make",
       note_cn="法定数字；且**未做收入 phase-out**，高收入者被记入一笔"
               "法律上不能做的缴款。",
       note_en="Statutory, and no income phase-out is applied -- a high "
               "earner is credited a contribution they cannot legally make."),
    # Reclassified 2026-08-29. Both halves of the old entry had become false:
    # the default is no longer the statutory $4,400 (the eligibility slice made
    # it a neutral 0, because a shipped 4,400 asserted an HDHP nobody stated),
    # and HDHP eligibility IS now modelled. What is left is a user fact with
    # the statutory ceilings in the dated pack, which is `gov_457b_y1`'s shape.
    _a("contributions.hsa_limit_y1", USER_FACT, NOT_A_NUMBER,
       code_ref="server/engine_adapter.py", current_default=0.0,
       note_cn="你今年**打算**缴进 HSA 的金额，不是法定上限。**零是刻意的中性值**："
               "没有 HDHP 的人本来就不该被默认记一笔缴款，而这正是它从 4,400 改成 0 的"
               "理由。上界不是假设 —— 2026 年 self-only / family 上限与 55 岁补缴在 dated "
               "pack 里，超过会被**点名拒绝**；非零还必须先给出 HDHP 资格事实。",
       note_en="What you PLAN to put into an HSA this year, not the statutory "
               "cap. Zero is deliberately neutral: someone with no HDHP should "
               "not be credited a contribution by default, which is why this "
               "moved from 4,400 to 0. The ceiling is not an assumption -- the "
               "2026 self-only / family limits and the age-55 catch-up are in "
               "the dated pack and an excess is refused by name, and a "
               "non-zero amount must state its HDHP eligibility facts first."),

    _a("contributions.espp_discount_rate", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":V8ContributionParams", current_default=0.0,
       note_cn="你自己那份 §423 计划的折扣，来自 offering 文件。"
               "**零是刻意的中性值**：ESPP 默认关闭，关着时这个数根本不被读；"
               "而一个非零的出厂折扣会替所有人假设一份他们可能没有的计划。"
               "15% 的法定上限在 dated pack 里（`espp_section_423`），"
               "超过会被**点名拒绝**，不是悄悄削平。",
       note_en="The discount in your own section 423 plan, from its offering "
               "document. Zero is deliberately neutral: ESPP ships off and the "
               "number is not read while it is, and a non-zero factory "
               "discount would assume a plan the user may not have. The "
               "statutory 15% ceiling lives in the dated pack "
               "(`espp_section_423`) and an excess is refused by name rather "
               "than quietly clipped."),

    _a("contributions.savings_rate", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":V8ContributionParams", current_default=0.0,
       note_cn="用户自己的储蓄率,只在 `savings_mode == \"savings_rate\"` 时生效。"
               "**0.0 是刻意中性**:它在默认(residual)模式下根本不被读,"
               "所以这个默认值不可能影响任何未改过的计划。"
               "没有「典型储蓄率」可言 —— 这正是必须由用户说的那类数字。",
       note_en="The user's own savings rate, read only when `savings_mode` is "
               "\"savings_rate\". Zero is deliberately neutral: in the default "
               "residual mode it is never read, so this default cannot affect "
               "an untouched plan. There is no such thing as a typical savings "
               "rate -- this is exactly the class of number a user has to state."),

    # `contributions.tax_model` is deliberately NOT here. This registry
    # covers numeric defaults -- that is what its gate reads and what
    # `numeric_defaults()` collects -- and a mode string is not one, the same
    # reason `contributions.savings_mode` is absent. What the switch does, and
    # why the default moved off the flat rate, is recorded on
    # `contributions.marginal_tax_pre` below, which is the number it retired.

    _a("contributions.employer_nonelective_rate", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":V8ContributionParams", current_default=0.0,
       note_cn="雇主不看你缴多少也会给的那一份,按薪酬的百分比 —— SEP、SIMPLE 的非选择性缴款、"
               "profit-sharing、Solo 401(k) 的雇主那一半都是这个形状。"
               "**0.0 是刻意中性**:绝大多数计划没有这一项,而有没有、给多少,"
               "是**只有你的计划文件说得清**的事,不存在「典型值」。"
               "自雇时它按 `r/(1+r)` 换算(25% 的 SEP 实际是净收入的 20%),而且那笔钱是你自己的,"
               "要和你自己的递延抢同一批可支配美元。",
       note_en="Employer money that arrives whether or not you defer, as a "
               "share of pay -- the shape a SEP, a SIMPLE non-elective, "
               "profit-sharing and the employer half of a Solo 401(k) all "
               "take. Zero is deliberately neutral: most plans have none, and "
               "whether yours does is something only your plan document "
               "knows. There is no typical value. For a self-employed person "
               "the rate converts as r/(1+r) -- a 25% SEP is 20% of net "
               "earnings -- and the money is their own, competing for the "
               "same affordable dollars as their deferral."),

    # ---- catch-up and the annual additions cap: statutory, and CITED ------
    #
    # The first entries in this registry graded `cited`. They came from IRS
    # primary sources rather than memory, and they are in the dated pack with
    # their URLs -- which is exactly the treatment the `sourceable` stance was
    # defined to demand.
    _a("contributions.catch_up_workplace_age50", SOURCEABLE, CITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=8000.0,
       external_source="https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500",
       note_cn="2026 法定 50+ 补缴额,来自 IRS 一手来源,随 pack 发布并带核验日期。",
       note_en="The 2026 statutory age-50 catch-up, from an IRS primary source, "
               "shipped in the dated pack with its citation."),
    _a("contributions.catch_up_ira_age50", SOURCEABLE, CITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=1100.0,
       external_source="https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500",
       note_cn="2026 法定 IRA 50+ 补缴额。", note_en="The 2026 statutory IRA age-50 catch-up."),
    _a("contributions.secure2_catch_up_workplace", SOURCEABLE, CITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=11250.0,
       external_source="https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500",
       note_cn="SECURE 2.0 的 60–63 岁更高补缴额。**它替代 50+ 那一笔,不是叠加**,"
               "64 岁起回落 —— 这是本条最容易实现错的地方,已由定向测试钉住。",
       note_en="The SECURE 2.0 higher catch-up for ages 60-63. It REPLACES the "
               "age-50 amount rather than stacking, and 64+ reverts. That is "
               "the easy thing to get wrong, so a test pins it."),
    # `contributions.additions_limit_415c` is NOT registered, because it is not
    # on the surface. It was added, then removed once measured: the match is
    # capped by the deferral and the deferral by the 402(g) limit, so employee
    # plus employer tops out near $49,000 against a $72,000 cap and 415(c)
    # cannot bind. The value stays in the pack, cited, for the day employer
    # contributions beyond a match are modelled.
    _a("contributions.catch_up_age", SOURCEABLE, CITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=50,
       external_source="https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500",
       note_cn="法定年龄门槛,不是建模选择。", note_en="A statutory age boundary, not a modelling choice."),
    _a("contributions.secure2_catch_up_age_min", SOURCEABLE, CITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=60,
       external_source="https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500",
       note_cn="法定年龄门槛。", note_en="A statutory age boundary."),
    _a("contributions.secure2_catch_up_age_max", SOURCEABLE, CITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=63,
       external_source="https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500",
       note_cn="法定年龄门槛。", note_en="A statutory age boundary."),

    # ---- Roth phase-out: statutory, cited, and one honest omission -------
    _a("contributions.roth_phase_out_single_start", SOURCEABLE, CITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=153000.0,
       external_source="https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500",
       note_cn="2026 单身 Roth 缴款 phase-out 起点(MAGI)。",
       note_en="Start of the 2026 single-filer Roth contribution phase-out (MAGI)."),
    _a("contributions.roth_phase_out_single_end", SOURCEABLE, CITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=168000.0,
       external_source="https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500",
       note_cn="终点;超过它单身申报人不能直接缴 Roth。",
       note_en="End of the range; above it a single filer may not contribute "
               "to a Roth IRA directly at all."),
    _a("contributions.roth_phase_out_mfj_start", SOURCEABLE, CITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=242000.0,
       external_source="https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500",
       note_cn="2026 联合申报的起点。", note_en="Start of the 2026 joint-filer range."),
    _a("contributions.roth_phase_out_mfj_end", SOURCEABLE, CITED,
       code_ref=_V8 + ":V8ContributionParams", current_default=252000.0,
       external_source="https://www.irs.gov/newsroom/401k-limit-increases-to-24500-for-2026-ira-limit-increases-to-7500",
       note_cn="联合申报的终点。", note_en="End of the joint-filer range."),

    # ---- promotion: an entire career shape, shipped to everyone -----------
    _a("promotion.base_salary_post", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":PromotionParams", current_default=170_000,
       carries_identity=True,
       evidence_gap="the module header names the role: 'Next career level: "
                    "Associate'. default_config() passes PromotionParams "
                    "through UNCHANGED, so every new user's plan projects this "
                    "promotion unless they edit it",
       note_cn="模块头写明了职级。`default_config()` **原样**放行 PromotionParams，"
               "所以每个新用户默认都会被规划一次晋升到这个薪资。",
       note_en="The header names the role, and default_config() passes "
               "PromotionParams through unchanged -- so every new user's plan "
               "projects this promotion unless they edit it."),
    _a("promotion.timing_min", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":PromotionParams", current_default=2,
       carries_identity=True, evidence_gap="one person's promotion window",
       note_cn="一个人的晋升窗口。", note_en="One person's promotion window."),
    _a("promotion.timing_max", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":PromotionParams", current_default=5,
       carries_identity=True, evidence_gap="one person's promotion window",
       note_cn="同上。", note_en="Same."),
    _a("promotion.timing_fixed", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":PromotionParams", current_default=3,
       carries_identity=True, evidence_gap="one person's promotion window",
       note_cn="同上。", note_en="Same."),
    _a("promotion.bonus_pct_min", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":PromotionParams", current_default=0.15,
       carries_identity=True, evidence_gap="one industry's bonus band",
       note_cn="一个行业的奖金区间。", note_en="One industry's bonus band."),
    _a("promotion.bonus_pct_max", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":PromotionParams", current_default=0.25,
       carries_identity=True, evidence_gap="one industry's bonus band",
       note_cn="同上。", note_en="Same."),
    _a("promotion.bonus_pct_fixed", USER_FACT, EXPLAINED_UNCITED,
       code_ref=_V8 + ":PromotionParams", current_default=0.20,
       carries_identity=True, evidence_gap="one industry's bonus band",
       note_cn="同上。", note_en="Same."),
    _a("promotion.base_growth_post", SOURCEABLE, BARE,
       code_ref=_V8 + ":PromotionParams", current_default=0.035,
       evidence_gap="same age-earnings question as salary_growth_pre",
       note_cn="与 salary_growth_pre 同一个问题。",
       note_en="Same age-earnings question as salary_growth_pre."),
    _a("promotion.marginal_tax_post", USER_FACT, BARE,
       code_ref=_V8 + ":PromotionParams", current_default=0.28,
       evidence_gap="a second flat bracket guess, income-independent -- and "
                    "no longer the default posture: read only under "
                    "contributions.tax_model == 'flat'. See "
                    "contributions.marginal_tax_pre for the measurement",
       note_cn="第二个与收入无关的平率猜测,**已不再是默认**:只在 `tax_model=\"flat\"` 时读。",
       note_en="A second flat, income-independent guess -- no longer the "
               "default: read only under tax_model == 'flat'."),

    # ---- layoff: seven ungraded scalars beside one already graded bare ----
    _a("layoff.p_annual", SOURCEABLE, BARE,
       code_ref=_V8 + ":LayoffParams", current_default=0.025,
       evidence_gap="the field help says 'US white-collar long-run 2-4%' but "
                    "no source appears anywhere in the code",
       note_cn="控件说明写了区间，但代码里没有任何出处。",
       note_en="The field help states a range; no source appears in code."),
    _a("layoff.bad_year_multiplier", SOURCEABLE, BARE,
       code_ref=_V8 + ":project_stratified_v8", current_default=3.0,
       evidence_gap="already graded `bare` in correlation_registry as the "
                    "engine's one live uncited correlation; registered here "
                    "too so the scalar surface is complete rather than "
                    "partially covered by a neighbouring table",
       note_cn="`correlation_registry` 已把它标为 bare。这里再登记一次，"
               "是为了让标量面**完整**，而不是一半靠隔壁那张表。",
       note_en="Already graded bare next door. Registered here too so the "
               "scalar surface is complete rather than half-covered."),
    _a("layoff.return_threshold", MODELLING, BARE,
       code_ref=_V8 + ":project_stratified_v8", current_default=-0.10,
       evidence_gap="the cut-off defining a 'bad year' is a choice, and no "
                    "sensitivity to it has been measured",
       note_cn="「坏年」的分界是一个选择，且没量过对它的敏感性。",
       note_en="The 'bad year' cut-off is a choice; its sensitivity is "
               "unmeasured."),
    _a("layoff.p_cap", MODELLING, BARE,
       code_ref=_V8 + ":project_stratified_v8", current_default=0.50,
       evidence_gap="a guard rail on the multiplied probability, not measured",
       note_cn="对放大后概率的护栏，不是测量值。",
       note_en="A guard rail on the multiplied probability, not a measurement."),
    _a("layoff.gap_months", SOURCEABLE, BARE,
       code_ref=_V8 + ":LayoffParams", current_default=4.0,
       evidence_gap="median unemployment duration is published monthly by BLS; "
                    "this number cites none of it",
       note_cn="失业持续时间中位数是 BLS 每月公布的数据，这个数字没有引用它。",
       note_en="Median unemployment duration is published monthly by BLS; "
               "this cites none of it."),
    _a("layoff.gap_months_per_year_of_age", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":LayoffParams", current_default=0.0,
       note_cn="**默认 0 是刻意的中性**，且代码里写明了理由：挑一个非零值等于"
               "替用户断言他所在行业的再就业速度。这是本仓库处理这类参数的**正面样板**。",
       note_en="Zero is a deliberate neutral default and the code says why: a "
               "non-zero value would assert a fact about the user's industry. "
               "This is the repo's positive template for this class."),
    _a("layoff.decay_from_age", MODELLING, BARE,
       code_ref=_V8 + ":LayoffParams", current_default=45,
       evidence_gap="the age at which search lengthens is itself unsourced, "
                    "though it only bites when the dial above is non-zero",
       note_cn="只有上面那个拨盘非零时才生效，但这个年龄本身也没有出处。",
       note_en="Only bites when the dial above is non-zero, but the age "
               "itself is unsourced."),
    _a("layoff.max_gap_months", MODELLING, EXPLAINED_UNCITED,
       code_ref=_V8 + ":LayoffParams", current_default=12.0,
       evidence_gap="a ceiling of one full year, chosen so the decay cannot "
                    "erase a whole career; not measured",
       note_cn="一年的上限，防止衰减吃掉整段职业；不是测量值。",
       note_en="A one-year ceiling so the decay cannot erase a career; not "
               "measured."),
    _a("layoff.medical_premium_monthly_real", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":LayoffParams", current_default=0.0,
       note_cn="用户自己的空窗期家庭净新增月保费。0 是刻意中性：覆盖未变、"
               "新增成本已含生活费，或模块关闭时都不应凭空收费；不存在全国典型值。",
       note_en="The user's own net-new monthly household premium during a "
               "layoff gap. Zero is deliberately neutral: unchanged coverage, "
               "a cost already inside living expenses, or an off module must "
               "not create a charge. There is no national typical value."),

    # ---- career break: user facts by construction -------------------------
    _a("career_break.start_age", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":CareerBreakParams", current_default=35,
       note_cn="用户自己决定的计划，默认值只是一个占位。",
       note_en="A plan the user decides; the default is only a placeholder."),
    _a("career_break.years", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":CareerBreakParams", current_default=1,
       note_cn="同上。", note_en="Same."),
    _a("career_break.income_fraction", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":CareerBreakParams", current_default=0.0,
       note_cn="同上；0 = 无薪，是中性的起点。",
       note_en="Same; 0 = unpaid, a neutral starting point."),
    _a("career_break.return_wage_factor", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":CareerBreakParams", current_default=1.0,
       note_cn="**1.0 是刻意中性** —— U26 裁定它是本模型表达工资疤痕的唯一位置，"
               "默认不替用户假设任何疤痕。",
       note_en="1.0 is deliberately neutral: U26 made this the only place wage "
               "scarring is modelled, so the default assumes none."),
    _a("career_break.medical_premium_annual_real", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":CareerBreakParams", current_default=0.0,
       note_cn="用户自己的休假期家庭净新增年保费。0 是刻意中性：不会替用户"
               "猜配偶计划、COBRA 或 Marketplace 报价。",
       note_en="The user's own net-new annual household premium during the "
               "break. Zero is deliberately neutral: the app does not guess "
               "a spouse-plan, COBRA, or Marketplace quote."),

    # ---- student debt: three facts from one current loan statement --------
    _a("student_debt.balance", USER_FACT, NOT_A_NUMBER,
       code_ref="server/student_debt.py:StudentDebtConfig",
       current_default=0.0,
       note_cn="用户贷款账单上的当前余额。零是默认关闭模块的中性值，不代表典型余额。",
       note_en="The current balance on the user's loan statement. Zero is the "
               "neutral value for a disabled module, not a typical balance."),
    _a("student_debt.annual_rate", USER_FACT, NOT_A_NUMBER,
       code_ref="server/student_debt.py:StudentDebtConfig",
       current_default=0.0,
       note_cn="用户贷款合同上的名义年利率。零只在默认关闭时中性，不是市场利率假设。",
       note_en="The nominal annual rate in the user's loan contract. Zero is "
               "neutral only while disabled, not a market-rate assumption."),
    _a("student_debt.monthly_payment", USER_FACT, NOT_A_NUMBER,
       code_ref="server/student_debt.py:StudentDebtConfig",
       current_default=0.0,
       note_cn="用户当前合同的固定名义月供。零是默认关闭值，不是还款建议。",
       note_en="The fixed nominal monthly payment in the user's current "
               "contract. Zero disables the module; it is not payment advice."),

    # ---- lifestyle creep: inherited v2 defaults, no external calibration --
    _a("lifestyle_creep.magnitude", SOURCEABLE, BARE,
       code_ref=_V8 + ":LifestyleCreepParams", current_default=0.15,
       evidence_gap="inherited from the superseded v2 engine; no external "
                    "source or calibration is recorded",
       note_cn="旧 v2 合同留下的 15%，没有外部校准，不代表典型家庭。",
       note_en="The old v2 contract's 15%; externally uncalibrated and not a "
               "typical-household estimate."),
    _a("lifestyle_creep.sd", SOURCEABLE, BARE,
       code_ref=_V8 + ":LifestyleCreepParams", current_default=0.05,
       evidence_gap="inherited from v2 without a source for cross-household "
                    "dispersion",
       note_cn="截断正态的离散度来自旧 v2，无外部出处。",
       note_en="Clipped-normal dispersion inherited from v2, with no external source."),
    _a("lifestyle_creep.cap", SOURCEABLE, BARE,
       code_ref=_V8 + ":LifestyleCreepParams", current_default=0.25,
       evidence_gap="the 25% truncation point is inherited from v2 and is not "
                    "an observed behavioral boundary",
       note_cn="25% 截断点不是观测边界，只是继承的未校准选择。",
       note_en="The 25% cap is not an observed boundary; it is an inherited "
               "uncalibrated choice."),
    _a("lifestyle_creep.year_lo", SOURCEABLE, BARE,
       code_ref=_V8 + ":LifestyleCreepParams", current_default=2,
       evidence_gap="the earliest event year is inherited from v2 with no "
                    "career-stage evidence",
       note_cn="最早第 2 年来自旧 v2，没有职业阶段数据支持。",
       note_en="Year 2 is inherited from v2 without career-stage evidence."),
    _a("lifestyle_creep.year_hi", SOURCEABLE, BARE,
       code_ref=_V8 + ":LifestyleCreepParams", current_default=5,
       evidence_gap="the latest event year is inherited from v2 with no "
                    "career-stage evidence",
       note_cn="最晚第 5 年同样没有外部校准。",
       note_en="Year 5 likewise has no external calibration."),

    # ---- SSA disability stress: user cash facts, official incidence table --
    _a("disability.ssdi_monthly_real", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":DisabilityParams", current_default=0.0,
       note_cn="用户自己的 SSDI award/estimate，按扣税后可花金额填写。零是关闭模块的中性值。",
       note_en="The user's own spendable after-tax SSDI award or estimate. "
               "Zero is neutral while the module is off."),
    _a("disability.ltd_monthly_real", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":DisabilityParams", current_default=0.0,
       note_cn="用户保单实际支付、且已扣 SSDI offset 后的 LTD 可花金额；不存在通用默认。",
       note_en="Spendable LTD actually paid by the user's policy after its "
               "SSDI offset; no general default exists."),
    _a("disability.medical_premium_annual_real", USER_FACT, NOT_A_NUMBER,
       code_ref=_V8 + ":DisabilityParams", current_default=0.0,
       note_cn="失去在职覆盖后用户家庭真实新增的年度保费，不从全国表猜。",
       note_en="The household's actual extra annual premium after losing "
               "working coverage; it is not guessed from a national table."),

    # ---- human capital: unsourced BY ITS OWN DOCSTRING --------------------
    _a("human_capital.permanent_sigma", SOURCEABLE, BARE,
       code_ref="engine/fire_v9_8_model.py:HumanCapitalParams",
       current_default=0.08,
       evidence_gap="the class docstring states outright that the number has "
                    "no source and that dispersion by occupation and cycle is "
                    "enormous",
       note_cn="类的 docstring 自己写明无出处。",
       note_en="The class docstring says outright that it has no source."),
    _a("human_capital.transitory_sigma", SOURCEABLE, BARE,
       code_ref="engine/fire_v9_8_model.py:HumanCapitalParams",
       current_default=0.05, evidence_gap="same docstring, same admission",
       note_cn="同上。", note_en="Same docstring, same admission."),

    _a("human_capital.seed_offset", MECHANISM, NOT_A_NUMBER,
       code_ref="engine/fire_v9_8_model.py:HumanCapitalParams",
       current_default=90_017,
       note_cn="独立随机流的偏移量，是机制不是对世界的假设。"
               "登记它是因为闸门按「面上的每个数字」扫描，"
               "而「这不是一个假设」本身也需要有人说出来。",
       note_en="An offset for an independent random stream: machinery, not an "
               "assumption about the world. Registered because the gate sweeps "
               "every number on the surface, and 'this is not an assumption' "
               "is itself something somebody has to state."),

    # ---- state: the accumulation frame ------------------------------------
    _a("state.start_age", USER_FACT, NOT_A_NUMBER,
       code_ref="engine/fire_v6_model.py:State", current_default=27,
       note_cn="引擎默认 27 是校准个人的年龄；`default_config()` 覆盖为 30。",
       note_en="The engine default is the calibration individual's age; "
               "default_config() overrides it to 30."),
    _a("state.accum_years", USER_FACT, NOT_A_NUMBER,
       code_ref="engine/fire_v6_model.py:State", current_default=25,
       note_cn="用户自己的工作年限。", note_en="The user's own working horizon."),
    _a("state.expenses_y0", USER_FACT, EXPLAINED_UNCITED,
       code_ref="engine/fire_v6_model.py:State", current_default=40_440,
       carries_identity=True,
       evidence_gap="the precision gives it away: 40,440 is one household's "
                    "actual annual spending, not a round default",
       note_cn="精度出卖了它：40,440 是某个家庭的实际年支出，不是一个圆整默认值。",
       note_en="The precision gives it away -- 40,440 is one household's "
               "actual spending, not a round default."),
    _a("state.inflation", SOURCEABLE, EXPLAINED_UNCITED,
       code_ref="engine/fire_v6_model.py:State", current_default=0.030,
       evidence_gap="a long-run CPI assumption stated without a window or a "
                    "source series",
       note_cn="长期 CPI 假设，没有给出窗口或来源序列。",
       note_en="A long-run CPI assumption with no window or source series."),
    # `state.contrib_growth` USED to be registered here as a dead leaf. Ruling
    # U28 removed the field itself, so it is no longer on the surface and an
    # entry for it would fail `test_the_registry_describes_no_field_that_is_gone`
    # -- a stale entry reports coverage of something that does not exist.
    # The removal is recorded in `tests/test_attribution_inventory.py`
    # (LEAVES_REMOVED_SINCE_CAREER_BREAK), where it has to stay so the
    # historical pins remain reproducible.
)}


def summary() -> dict:
    """Counts by stance and grade, plus the identity-carrying defaults.

    `carries_identity` is the number Roadmap 10.0 is trying to drive to zero:
    a shipped default that is traceable to one real person is both a privacy
    matter and a modelling one -- it silently plans everybody's career as if
    it were that person's.
    """
    by_stance: dict = {}
    by_grade: dict = {}
    for entry in REGISTRY.values():
        by_stance[entry.stance] = by_stance.get(entry.stance, 0) + 1
        by_grade[entry.grade] = by_grade.get(entry.grade, 0) + 1
    identity = sorted(e.path for e in REGISTRY.values() if e.carries_identity)
    ungraded = sorted(e.path for e in REGISTRY.values() if e.grade == BARE)
    return {
        "assumptions": len(REGISTRY),
        "by_stance": dict(sorted(by_stance.items())),
        "by_grade": dict(sorted(by_grade.items())),
        "carries_identity": identity,
        "carries_identity_count": len(identity),
        "bare": ungraded,
        "bare_count": len(ungraded),
    }
