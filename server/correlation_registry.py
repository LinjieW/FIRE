"""What this engine assumes about how its random pieces move together.

Roadmap 6.0 Phase 2 (idea-bank A15, second half). Phase 1 counted the draws:
seventeen modules across nineteen call sites. This says, for each of them,
what the model assumes about its relationship to everything else -- and, where
that assumption came from.

**The deliverable is an account, not a parameter.** Nothing here adds a
correlation, tunes one, or proposes a value. Three of the four correlations
this engine already applies were found by the census rather than by anyone
remembering them, and one of those has no provenance anywhere in the
repository. An account of that is worth more than a fifth number.

**"Nobody checked" is a stance, and it is the most common one.** Most of these
modules draw one after another from a shared stream, which makes them
independent *in the model*. Whether they are independent *in the world* is a
separate question that has not been asked for most of them. Recording that as
`INDEPENDENT_UNEXAMINED` rather than as `INDEPENDENT` is the same discipline
this repository applies to a zero that might be an unmeasured zero: the two
look identical in output and mean opposite things.

**Provenance is graded separately from the stance**, because a modelled
correlation with no source and a modelled correlation from a paper are the
same arithmetic and very different claims.
"""
from __future__ import annotations

from typing import Optional

# ---- stances -------------------------------------------------------------
#: A relationship the engine actually applies.
MODELLED = "modelled"
#: Drawn independently ON PURPOSE, with a reason, and disclosed to the user.
INDEPENDENT_BY_DESIGN = "independent_by_design"
#: Drawn independently because that is what sequential draws do. Whether the
#: world agrees has not been examined. NOT the same as `INDEPENDENT_BY_DESIGN`.
INDEPENDENT_UNEXAMINED = "independent_unexamined"

STANCES = (MODELLED, INDEPENDENT_BY_DESIGN, INDEPENDENT_UNEXAMINED)

# ---- provenance grades ---------------------------------------------------
#: Traceable to a named external source.
CITED = "cited"
#: Explained in the code that implements it, but no external source given.
EXPLAINED_UNCITED = "explained_uncited"
#: A bare number. No explanation, no source, anywhere.
BARE = "bare"
#: Provenance is not applicable -- there is no number to source.
NOT_A_NUMBER = "not_a_number"

GRADES = (CITED, EXPLAINED_UNCITED, BARE, NOT_A_NUMBER)


class Entry:
    """One stance, and where it came from."""

    def __init__(self, module: str, stance: str, grade: str, *,
                 relates_to: tuple = (), params: tuple = (),
                 note_cn: str = "", note_en: str = "",
                 ui_control: Optional[bool] = None):
        assert stance in STANCES, stance
        assert grade in GRADES, grade
        self.module = module
        self.stance = stance
        self.grade = grade
        self.relates_to = relates_to
        self.params = params
        self.note_cn = note_cn
        self.note_en = note_en
        #: None means "not checked here" rather than "no control" -- the
        #: distinction this whole module exists to preserve.
        self.ui_control = ui_control


#: Keyed by the module names `tests/test_rng_census.py` observes. A census
#: module with no entry here is a gate failure, which is the mechanism: a new
#: sampling module cannot arrive without somebody stating its stance.
REGISTRY = {
    "market_returns": Entry(
        "market_returns", MODELLED, EXPLAINED_UNCITED,
        relates_to=("bonds",), params=("bonds.correlation_with_equity",),
        ui_control=True,
        note_cn="股票与债券通过高斯 copula 相关（默认 0.15）。实现处的 docstring "
                "说明了方法，但**没有给出这个数字的外部来源** —— 它是本 App 的选择。"
                "**6.0 之前它在界面上根本不存在**：这份注册表记录了那个缺口，"
                "然后同一版把控件补上了。相关性越高，用债券对冲股票越不管用，"
                "而那正是很多计划赖以撑过坏十年的东西。",
        note_en="Equity and bonds are correlated through a Gaussian copula "
                "(0.15 by default). The implementing docstring explains the "
                "method but gives no external source for the number. It is "
                "the most basic assumption in this model and there is no UI "
                "control for it at all: the user can neither see nor change "
                "it."),
    "inflation": Entry(
        "inflation", MODELLED, EXPLAINED_UNCITED,
        relates_to=("market_returns",),
        params=("returns.inflation_equity_corr",), ui_control=True,
        note_cn="通胀与股票回报相关（默认 −0.3）。数值可在配置面调整，"
                "但**没有外部来源**。",
        note_en="Inflation is correlated with equity returns (-0.3 by "
                "default). The value is adjustable but has no external "
                "source."),
    "layoff": Entry(
        "layoff", MODELLED, BARE,
        relates_to=("market_returns",),
        params=("layoff.bad_year_multiplier", "layoff.return_threshold"),
        ui_control=True,
        note_cn="市场坏年份（回报 ≤ −10%）时失业概率乘以 3.0。"
                "**这两个数字在本仓库里没有任何出处** —— 不是「来源存疑」，"
                "是连一句解释都没有，而它在影响每一份开了失业模块的计划。"
                "登记它不是为了辩护它，是为了让它不再隐形。",
        note_en="In a bad market year (returns at or below -10%) the annual "
                "layoff probability is multiplied by 3.0. Neither number has "
                "any provenance in this repository -- not a doubtful source, "
                "no explanation at all -- and it affects every plan with the "
                "layoff module on. Listing it is not a defence of it; it is "
                "so that it stops being invisible."),
    "blocky_spending": Entry(
        "blocky_spending", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("market_returns",), ui_control=True,
        note_cn="大额支出的到达与市场抽样**刻意独立**，用独立子生成器实现，"
                "并已在控件说明里向用户明写。现实中修屋顶和熊市可能同时来 —— "
                "**这个模型不建模那种相关性**。",
        note_en="Lump arrivals are deliberately independent of market draws, "
                "implemented with a separate child generator and disclosed to "
                "the user in the control's help. In reality a roof and a bear "
                "market can arrive together; that correlation is not "
                "modelled."),
    "human_capital": Entry(
        "human_capital", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("market_returns", "layoff"), ui_control=True,
        note_cn="工资冲击与市场、与失业**独立抽样**。**这几乎肯定不是真的** —— "
                "裁员潮与熊市同时来，而且失业概率本身已经与市场相关"
                "（本注册表里的 `layoff` 条目）。所以这里的独立性与那一条"
                "**互相矛盾**：同一个引擎里，失业与市场相关、而工资与市场无关。"
                "登记这个矛盾而不是掩盖它 —— 消掉它需要真实的联合数据，本项目不靠猜填。",
        note_en="Wage shocks are drawn independently of markets and of the "
                "layoff module. That is almost certainly untrue -- layoffs and "
                "bear markets arrive together -- and it CONTRADICTS this "
                "registry's own layoff entry, where the probability already "
                "moves with markets. So the same engine says unemployment is "
                "correlated with markets and wages are not. The contradiction "
                "is registered rather than hidden: resolving it needs real "
                "joint data, which will not be guessed."),
    "house_price": Entry(
        "house_price", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("market_returns", "inflation"), ui_control=True,
        note_cn="房价路径与市场、通胀**独立抽样**，用独立子生成器实现。"
                "**这几乎肯定不是真的** —— 房价与利率、就业、通胀在现实中相关，"
                "而本项目不会靠猜来填这些系数。登记为「刻意独立」是因为"
                "**它是被选择的，不是被忽略的**，并且这条披露就是那个选择本身。"
                "波动率也没有默认自任何指数：用户有看法才填。",
        note_en="The house price path is drawn independently of markets and "
                "inflation, with a separate child generator. That is almost "
                "certainly not true of the world -- house prices move with "
                "rates, employment and inflation -- and this project will not "
                "fill those coefficients in by guessing. It is registered as "
                "deliberate rather than unexamined because it was chosen, and "
                "this disclosure is the choice. The volatility is not "
                "defaulted from any index either."),
    "ss_trust_fund": Entry(
        "ss_trust_fund", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("market_returns",), ui_control=True,
        note_cn="信托基金枯竭方案的抽样与市场独立，用独立子生成器实现。"
                "枯竭时点由联邦精算投影决定，**与本计划的市场路径无关** —— "
                "那是有意的：报告的三套方案是宏观情景，不是这条路径的函数。",
        note_en="The trust fund scenario is drawn independently of markets, "
                "with a separate child generator. Depletion timing comes from "
                "federal actuarial projections and does not depend on this "
                "plan's market path, which is deliberate: the report's three "
                "alternatives are macro scenarios, not a function of one "
                "path."),
}

#: Every other census module draws sequentially from the shared stream, which
#: makes it independent IN THE MODEL. Whether it should be is unexamined.
#: Listed by name so the gate can tell "unexamined" from "forgotten".
UNEXAMINED = (
    "return_regime", "promotion_timing", "promotion_bonus", "mortality",
    "accumulation_preview", "accumulation_resume", "ltc_onset",
    "ltc_progression", "parent_mortality", "parent_care_entry",
    "parent_care_timing", "parent_care_level",
)

for _name in UNEXAMINED:
    REGISTRY[_name] = Entry(
        _name, INDEPENDENT_UNEXAMINED, NOT_A_NUMBER,
        note_cn="与其他模块独立抽样。**这是模型里的独立，不是世界里的独立** —— "
                "本项目没有检验过它是否应该相关，也不打算靠猜来填。",
        note_en="Drawn independently of the other modules. That is "
                "independence IN THE MODEL, not a finding about the world: "
                "whether it should be correlated has not been examined, and "
                "will not be filled in by guessing.")


def summary() -> dict:
    """Counts by stance and by grade, for the disclosure and for a reader."""
    by_stance: dict = {}
    by_grade: dict = {}
    for entry in REGISTRY.values():
        by_stance[entry.stance] = by_stance.get(entry.stance, 0) + 1
        if entry.grade != NOT_A_NUMBER:
            by_grade[entry.grade] = by_grade.get(entry.grade, 0) + 1
    return {
        "modules": len(REGISTRY),
        "by_stance": by_stance,
        "by_grade": by_grade,
        "modelled": sorted(e.module for e in REGISTRY.values()
                           if e.stance == MODELLED),
        "bare": sorted(e.module for e in REGISTRY.values()
                       if e.grade == BARE),
        "no_ui_control": sorted(e.module for e in REGISTRY.values()
                                if e.ui_control is False),
    }
