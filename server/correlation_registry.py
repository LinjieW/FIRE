"""What this engine assumes about how its random pieces move together.

Roadmap 6.0 Phase 2 (idea-bank A15, second half), completed as a relationship
ledger in 7.0. The RNG census counts modules and call sites; this file says
which *relationship* each draw participates in, what the code actually does,
and where the evidence stops.

**The deliverable is an account, not a parameter.** Nothing here adds a
correlation, tunes one, or proposes a value. Three of the four correlations
this engine already applies were found by the census rather than by anyone
remembering them, and one of those has no provenance anywhere in the
repository. An account of that is worth more than a fifth number.

**A sequential RNG draw is not evidence of real-world independence.** Some of
the old module-level labels also hid the opposite error: onset and progression,
or a preview and its replay, are stages of one process rather than separate
world events. The four stances below therefore distinguish applied numbers,
code-defined structural links, deliberate independence, and bounded research
that did not yield a portable relationship. The last one is not zero.

**Provenance is graded separately from the stance**, because a modelled
correlation with no source and a modelled correlation from a paper are the
same arithmetic and very different claims.
"""
from __future__ import annotations

from typing import Optional

# ---- stances -------------------------------------------------------------
#: A numeric relationship the engine actually applies.
MODELLED_NUMERIC = "modelled_numeric"
#: Drawn independently ON PURPOSE, with a reason, and disclosed to the user.
INDEPENDENT_BY_DESIGN = "independent_by_design"
#: Two census labels are stages or outputs of one code-defined process.
STRUCTURALLY_LINKED = "structurally_linked"
#: A bounded set of primary/official sources was examined but does not support
#: a portable joint distribution or coefficient for this user's plan. This is
#: NOT independence, and it is not a claim that no usable public evidence can
#: exist.
EXAMINED_UNRESOLVED = "examined_unresolved"

STANCES = (MODELLED_NUMERIC, STRUCTURALLY_LINKED,
           INDEPENDENT_BY_DESIGN, EXAMINED_UNRESOLVED)

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
    """One relationship rooted at a census module, and its evidence ledger."""

    def __init__(self, module: str, stance: str, grade: str, *,
                 relates_to: tuple = (), params: tuple = (),
                 code_refs: tuple = (), external_sources: tuple = (),
                 evidence_gap: str = "", disclosure_ref: str = "",
                 note_cn: str = "", note_en: str = "",
                 ui_control: Optional[bool] = None):
        assert stance in STANCES, stance
        assert grade in GRADES, grade
        assert relates_to, "%s has no relationship target" % module
        assert code_refs, "%s has no stable code evidence" % module
        if stance == MODELLED_NUMERIC:
            assert params, "%s models a number but names no parameter" % module
        if stance == EXAMINED_UNRESOLVED:
            assert external_sources, "%s claims examination without a source" % module
            assert evidence_gap, "%s hides why the evidence was insufficient" % module
            assert disclosure_ref, "%s unresolved relationship is undisclosed" % module
        if stance == INDEPENDENT_BY_DESIGN:
            assert disclosure_ref, "%s deliberate independence is undisclosed" % module
        self.module = module
        self.stance = stance
        self.grade = grade
        self.relates_to = relates_to
        self.params = params
        self.code_refs = code_refs
        self.external_sources = external_sources
        self.evidence_gap = evidence_gap
        self.disclosure_ref = disclosure_ref
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
        "market_returns", MODELLED_NUMERIC, EXPLAINED_UNCITED,
        relates_to=("bonds",), params=("bonds.correlation_with_equity",),
        code_refs=("engine/fire_v7_model.py:sample_joint_return_inflation",),
        ui_control=True,
        note_cn="股票与债券通过高斯 copula 相关（默认 0.15）。实现处的 docstring "
                "说明了方法，但**没有给出这个数字的外部来源** —— 它是本 App 的选择。"
                "**6.0 之前它在界面上根本不存在**：这份注册表记录了那个缺口，"
                "然后同一版把控件补上了。相关性越高，用债券对冲股票越不管用，"
                "而那正是很多计划赖以撑过坏十年的东西。",
        note_en="Equity and bonds are correlated through a Gaussian copula "
                "(0.15 by default). The implementing docstring explains the "
                "method but gives no external source for the number. Before "
                "6.0 it had no UI control; the registry exposed that gap and "
                "the same release added the control. Higher correlation makes "
                "bonds a weaker hedge against bad equity decades."),
    "inflation": Entry(
        "inflation", MODELLED_NUMERIC, EXPLAINED_UNCITED,
        relates_to=("market_returns",),
        params=("returns.inflation_equity_corr",), ui_control=True,
        code_refs=("engine/fire_v7_model.py:sample_joint_return_inflation",),
        note_cn="通胀与股票回报相关（默认 −0.3）。数值可在配置面调整，"
                "但**没有外部来源**。",
        note_en="Inflation is correlated with equity returns (-0.3 by "
                "default). The value is adjustable but has no external "
                "source."),
    "layoff": Entry(
        "layoff", MODELLED_NUMERIC, BARE,
        relates_to=("market_returns",),
        params=("layoff.bad_year_multiplier", "layoff.return_threshold"),
        code_refs=("engine/fire_v8_model.py:project_stratified_v8",),
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
        code_refs=("engine/fire_v9_8_model.py:simulate_lifecycle_v98",),
        disclosure_ref="web/app.js:blocky_spending.annual_probability",
        note_cn="大额支出的到达与市场抽样**刻意独立**，用独立子生成器实现，"
                "并已在控件说明里向用户明写。现实中修屋顶和熊市可能同时来 —— "
                "**这个模型不建模那种相关性**。",
        note_en="Lump arrivals are deliberately independent of market draws, "
                "implemented with a separate child generator and disclosed to "
                "the user in the control's help. In reality a roof and a bear "
                "market can arrive together; that correlation is not "
                "modelled."),
    "spouse_layoff": Entry(
        "spouse_layoff", MODELLED_NUMERIC, BARE,
        relates_to=("market_returns", "layoff"),
        params=("spouse_layoff.bad_year_multiplier",
                "spouse_layoff.return_threshold"),
        code_refs=("engine/fire_v8_model.py:project_stratified_v8",),
        ui_control=True,
        note_cn="配偶的失业概率与主申报人**共用同一个坏年景信号**：同一年的市场回报"
                "低于阈值时，两人的概率同时被各自的倍数抬高。**这一层是结构，不是猜的系数** ——"
                "衰退同时压两个人，不需要编数字。**给定那一年之后，两人是否真的被裁是独立抽的**，"
                "而现实里同一个家庭的两次失业还会通过行业、雇主、地区进一步相关 ——"
                "**那一层本项目不猜，也因此没有建模**。倍数与阈值都是用户自己填的。",
        note_en="The spouse's layoff probability SHARES THE SAME BAD-YEAR "
                "SIGNAL as the primary's: when a year's market return falls "
                "below the threshold, both probabilities are lifted by their "
                "own multipliers. THAT LAYER IS STRUCTURE, NOT A GUESSED "
                "COEFFICIENT -- a recession presses on both people and needs "
                "no invented number. GIVEN THAT YEAR, whether each is actually "
                "let go is drawn independently. In reality two layoffs in one "
                "household are further correlated through industry, employer "
                "and region; THAT LAYER IS NOT MODELLED, because this project "
                "will not guess it. The multiplier and threshold are the "
                "user's own figures."),
    "human_capital": Entry(
        "human_capital", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("market_returns", "layoff", "spouse_human_capital"),
        ui_control=True,
        code_refs=("engine/fire_v9_8_model.py:simulate_lifecycle_v98",),
        disclosure_ref="server/limitations.py:human_capital",
        note_cn="**自 U35/B 起这一条覆盖两个人**：主申报人与配偶各有自己的工资冲击，**各走独立的 child stream**（实现是同一个 helper，所以两人语义相同，而两条流不同这件事是实测的，不是声称的）。**夫妻收入冲击在现实里当然相关** —— 同一个经济周期，常在同一行业，甚至同一家公司；这里刻意不猜那个系数。工资冲击与市场、与失业**独立抽样**。**这几乎肯定不是真的** —— "
                "裁员潮与熊市同时来，而且失业概率本身已经与市场相关"
                "（本注册表里的 `layoff` 条目）。所以这里的独立性与那一条"
                "**互相矛盾**：同一个引擎里，失业与市场相关、而工资与市场无关。"
                "登记这个矛盾而不是掩盖它 —— 消掉它需要真实的联合数据，本项目不靠猜填。",
        note_en="SINCE U35/B THIS COVERS BOTH EARNERS: the primary and the "
                "spouse each get their own wage shocks on their own child "
                "stream. One shared helper draws both, so the couple "
                "provably shares one model, and that the two STREAMS "
                "differ is measured rather than asserted. A couple's "
                "income shocks are of course correlated in reality -- one "
                "economy, often one industry, sometimes one employer -- "
                "and no coefficient is guessed here. "
                "Wage shocks are drawn independently of markets and of the "
                "layoff module. That is almost certainly untrue -- layoffs and "
                "bear markets arrive together -- and it CONTRADICTS this "
                "registry's own layoff entry, where the probability already "
                "moves with markets. So the same engine says unemployment is "
                "correlated with markets and wages are not. The contradiction "
                "is registered rather than hidden: resolving it needs real "
                "joint data, which will not be guessed."),
    "variable_income": Entry(
        "variable_income", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("market_returns", "promotion_bonus", "human_capital", "layoff"),
        ui_control=True,
        code_refs=("engine/fire_v9_8_model.py:simulate_lifecycle_v98",),
        disclosure_ref="server/limitations.py:variable_income",
        note_cn="W-2 奖金或 1099 净利润路径使用自己的稳定子流，与市场、晋升、"
                "人力资本和失业抽样刻意分开。这保证开启控件不移动别的同 seed 路径，"
                "**不是现实独立性的证据**；本刀没有编一个相关系数。",
        note_en="The W-2 bonus or 1099 net-profit path uses its own stable child "
                "stream, deliberately separate from market, promotion, human-"
                "capital and layoff draws. That preserves same-seed paths; it is "
                "not evidence of real-world independence, and no coefficient is "
                "invented."),
    "retained_stock": Entry(
        "retained_stock", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("market_returns", "variable_income"),
        ui_control=True,
        code_refs=("engine/fire_v9_8_model.py:simulate_lifecycle_v98",),
        disclosure_ref="server/limitations.py:rsu_retained_sigma",
        note_cn="保留股票的价值路径走自己的稳定子流。**现实里它当然和市场相关** ——"
                "那是你雇主的股票，它和大盘、和你自己的工资都不独立。这里刻意不猜那个系数，"
                "所以这条是「刻意独立」而不是「零相关是真的」。"
                "σ 与 drift 都是用户自己填的，不是校准值。",
        note_en="The kept shares' value path uses its own stable child stream. "
                "In reality it is of course correlated with the market -- it is "
                "your employer's stock, and it is not independent of the wider "
                "market or of your own pay. No coefficient is guessed here, so "
                "this is a DELIBERATE independence rather than a claim that the "
                "correlation is zero. Both sigma and drift are the user's own "
                "figures, not a calibration."),
    "spouse_variable_income": Entry(
        "spouse_variable_income", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("variable_income", "market_returns", "spouse_layoff"),
        ui_control=True,
        code_refs=("engine/fire_v9_8_model.py:simulate_lifecycle_v98",),
        disclosure_ref="server/limitations.py:spouse_variable_income",
        note_cn="配偶的奖金百分比走自己的稳定子流，与主申报人的奖金、与市场、"
                "与配偶失业都刻意分开抽。**现实里两个人的奖金当然相关** ——"
                "同一个经济，很多时候同一个行业，奖金池一起缩；"
                "这里不猜那个系数，所以是「刻意独立」，不是「零相关是真的」。"
                "上下界是用户自己填的，不是行业分布。",
        note_en="The spouse's bonus percentage uses its own stable child "
                "stream, deliberately separate from the primary's bonus, from "
                "markets and from the spouse's own layoff. TWO PEOPLE'S "
                "BONUSES ARE OF COURSE CORRELATED in reality -- one economy, "
                "often one industry, bonus pools shrinking together. No "
                "coefficient is guessed, so this is a deliberate independence "
                "rather than a claim that the correlation is zero. The bounds "
                "are the user's own figures, not an industry distribution."),
    "spouse_promotion": Entry(
        "spouse_promotion", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("promotion_timing", "promotion_bonus", "market_returns",
                    "spouse_variable_income", "human_capital"),
        ui_control=True,
        code_refs=("engine/fire_v8_model.py:sample_promotion_event",
                   "engine/fire_v9_8_model.py:simulate_lifecycle_v98"),
        disclosure_ref="server/limitations.py:spouse_promotion",
        note_cn="配偶晋升年与晋升后奖金使用 offset 90,022 的稳定独立子流,"
                "不会重排主申报人的晋升、配偶晋升前奖金或市场路径。"
                "现实里夫妻晋升会受同一经济周期和行业影响;这里没有可辩护的联合分布,"
                "所以不猜系数。这是刻意独立,不是零相关发现。",
        note_en="Spouse promotion timing and post-promotion bonus use the "
                "stable child stream at offset 90,022, so they do not "
                "reshuffle the primary's promotion, the spouse's pre-"
                "promotion bonus or markets. In reality a couple's "
                "promotions share economic and industry conditions; no "
                "defensible joint distribution is available here, so no "
                "coefficient is guessed. This is deliberate independence, "
                "not a finding of zero correlation."),
    "second_promotion": Entry(
        "second_promotion", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("promotion_timing", "promotion_bonus", "market_returns",
                    "human_capital", "spouse_promotion"),
        ui_control=True,
        code_refs=("engine/fire_v8_model.py:sample_promotion_event",
                   "engine/fire_v9_8_model.py:simulate_lifecycle_v98"),
        disclosure_ref="server/limitations.py:second_promotion",
        note_cn="主申报人的第二次晋升年与奖金使用 offset 90,023 的稳定子流,"
                "不会重排第一次晋升或市场路径。现实中同一个人的两次晋升当然相关;"
                "这里用不重叠的用户时间窗口表达先后,但不猜额外的联合概率。",
        note_en="The primary earner's second-promotion timing and bonus use "
                "the stable child stream at offset 90,023, so the first "
                "promotion and market path are not reshuffled. Two promotions "
                "in one career are of course related; user-supplied non-"
                "overlapping windows encode their order, while no additional "
                "joint probability is guessed."),
    "spouse_second_promotion": Entry(
        "spouse_second_promotion", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("spouse_promotion", "second_promotion", "market_returns",
                    "spouse_variable_income", "human_capital"),
        ui_control=True,
        code_refs=("engine/fire_v8_model.py:sample_promotion_event",
                   "engine/fire_v9_8_model.py:simulate_lifecycle_v98"),
        disclosure_ref="server/limitations.py:spouse_second_promotion",
        note_cn="配偶第二次晋升年与奖金使用 offset 90,024 的稳定子流。两个人、"
                "同一个人的两次晋升都可能受共同经济与行业影响;本模型没有可辩护的"
                "联合分布,所以不猜系数。",
        note_en="The spouse's second-promotion timing and bonus use the stable "
                "child stream at offset 90,024. Promotions across two people "
                "and within one career may share economic and industry "
                "drivers; no defensible joint distribution is available, so "
                "the model guesses no coefficient."),
    "lifestyle_creep": Entry(
        "lifestyle_creep", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("market_returns", "inflation", "layoff"), ui_control=True,
        code_refs=("engine/fire_v8_model.py:sample_lifestyle_creep",),
        disclosure_ref="server/limitations.py:lifestyle_creep",
        note_cn="生活开销上调使用自己的稳定子流，与市场、通胀和失业抽样刻意分开。"
                "这是为了让开启模块不移动别的同 seed 路径，**不是现实独立性的证据**。",
        note_en="The spending step uses its own stable child stream and is "
                "deliberately separate from market, inflation and layoff draws. "
                "That preserves same-seed paths when the module is enabled; it "
               "is not evidence of real-world independence."),
    "disability": Entry(
        "disability", EXAMINED_UNRESOLVED, NOT_A_NUMBER,
        relates_to=("market_returns", "layoff", "human_capital", "mortality"),
        code_refs=("engine/fire_v8_model.py:sample_ssdi_entitlement",),
        external_sources=(
            "https://www.ssa.gov/oact/TR/2026/2026_Long-Range_Disability_Assumptions.pdf",
        ),
        evidence_gap="SSA states that disabled-worker incidence changes with "
                     "economic cycles, accommodations and policy, but the "
                     "official table does not supply a portable joint "
                     "distribution with this engine's market, layoff, wage "
                     "or mortality draws for one household.",
        disclosure_ref="server/limitations.py:disability", ui_control=True,
        note_cn="SSA 官方材料明确说经济周期、工作便利与政策会改变 disabled-worker "
                "incidence；但年龄×性别边际表没有给出可移植到本计划市场、失业、工资与"
                "死亡抽样的联合分布。本刀使用独立子流，是已检查未解决的缺口，不是零相关发现。",
        note_en="SSA states that economic cycles, workplace accommodations "
                "and policy affect disabled-worker incidence. Its age-by-sex "
                "marginal table does not provide a portable joint distribution "
                "with this plan's market, layoff, wage or mortality draws. "
                "The independent child stream is an examined unresolved gap, "
                "not a zero-correlation finding."),
    "house_price": Entry(
        "house_price", INDEPENDENT_BY_DESIGN, NOT_A_NUMBER,
        relates_to=("market_returns", "inflation"), ui_control=True,
        code_refs=("engine/fire_v9_8_model.py:simulate_lifecycle_v98",),
        disclosure_ref="server/limitations.py:house_price",
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
        code_refs=("engine/fire_v9_8_model.py:simulate_lifecycle_v98",),
        disclosure_ref="server/limitations.py:correlation_assumptions",
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

#: The twelve labels that used to share one blanket
#: ``INDEPENDENT_UNEXAMINED`` sentence. Each is explicit now: most are stages
#: of one process, while the three world-facing relationships record the
#: bounded literature review and the reason no coefficient was imported.
REGISTRY.update({
    "return_regime": Entry(
        "return_regime", STRUCTURALLY_LINKED, NOT_A_NUMBER,
        relates_to=("market_returns",),
        code_refs=("engine/fire_v7_model.py:sample_lifetime_v7",
                   "engine/fire_v7_model.py:pick_regime_v7"),
        note_cn="regime 不是与回报并列的外部冲击；它先选中一套回报参数，随后整条"
                "市场路径都由该套参数生成。这是代码结构关系，不是相关系数。",
        note_en="The regime is not an outside shock beside returns. It selects "
                "the parameter family that generates the entire market path; "
                "that is a structural link, not a correlation coefficient."),
    "promotion_timing": Entry(
        "promotion_timing", EXAMINED_UNRESOLVED, NOT_A_NUMBER,
        relates_to=("market_returns", "layoff", "human_capital"),
        code_refs=("engine/fire_v8_model.py:sample_promotion_event",),
        external_sources=(
            "https://www.bls.gov/opub/mlr/2014/article/job-promotion-in-midcareer.htm",
            "https://www.bls.gov/osmr/research-papers/1995/ec950170.htm",
        ),
        evidence_gap="The studies show promotion rates vary with recessions, "
                     "worker history, cohort, training and occupation, but do "
                     "not measure this engine's equity-return, inflation or "
                     "layoff draws and do not identify a portable annual joint "
                     "distribution for this user's promotion window.",
        disclosure_ref="server/limitations.py:correlation_assumptions",
        note_cn="BLS/NLSY 研究说明晋升概率会随衰退、年龄组、培训与既往晋升而变。"
                "这些暴露不是本引擎的股票回报、通胀或失业抽样；把它们映射到登记目标"
                "是模型推断，不是论文直接估计。方向并非完全一致，也没有可移植到本用户"
                "行业与 2–5 年窗口的联合分布。"
                "因此现有独立抽样是已检查但未解决的模型缺口，不是零相关发现。",
        note_en="BLS/NLSY evidence says promotion probability varies with "
                "recessions, cohort, training and prior promotions. Those "
                "exposures are not this engine's equity-return, inflation or "
                "layoff draws; mapping them to the ledger targets is a model "
                "inference, not a relationship directly estimated by the "
                "papers. They do not yield a portable joint distribution for "
                "this user's industry and two-to-five-year window, so the current "
                "independent draw is an examined unresolved gap, not zero."),
    "promotion_bonus": Entry(
        "promotion_bonus", EXAMINED_UNRESOLVED, NOT_A_NUMBER,
        relates_to=("promotion_timing", "market_returns", "human_capital"),
        code_refs=("engine/fire_v8_model.py:sample_promotion_event",),
        external_sources=(
            "https://www.bls.gov/osmr/research-papers/1995/ec950170.htm",
            "https://www.nber.org/papers/w24343",
        ),
        evidence_gap="The reviewed worker-level promotion studies describe "
                     "wage and performance consequences, not a portable joint "
                     "distribution of bonus percentage, promotion timing and "
                     "aggregate market returns; they do not directly measure "
                     "this engine's market or human-capital draws.",
        disclosure_ref="server/limitations.py:correlation_assumptions",
        note_cn="已查的员工级晋升研究支持「晋升、绩效与工资结果有关」，但没有给出"
                "奖金比例 × 晋升时点 × 市场回报的可移植联合分布。这里仍逐年均匀抽"
                "奖金，是已检查未解决的缺口；15%–25% 不是文献系数。",
        note_en="The reviewed worker-level studies connect promotions with "
                "performance and wage outcomes, but do not provide a portable "
                "joint distribution of bonus percentage, promotion timing and "
                "market returns. The annual uniform draw remains an examined "
                "unresolved gap; 15%-25% is not a literature coefficient."),
    "mortality": Entry(
        "mortality", EXAMINED_UNRESOLVED, NOT_A_NUMBER,
        relates_to=("market_returns", "layoff", "inflation"),
        code_refs=("engine/fire_v9_8_model.py:simulate_retirement_v98",),
        external_sources=(
            "https://pubmed.ncbi.nlm.nih.gov/15482879/",
            "https://pubmed.ncbi.nlm.nih.gov/28772108/",
            "https://pubmed.ncbi.nlm.nih.gov/31607469/",
        ),
        evidence_gap="Longitudinal studies use recession, unemployment or GDP "
                     "exposures and find different signs and subgroup effects "
                     "across countries, ages, causes and safety nets. They do "
                     "not directly estimate mortality jointly with this "
                     "engine's equity-return, layoff or inflation draws and do "
                     "not justify one annual coefficient for this household.",
        disclosure_ref="server/limitations.py:correlation_assumptions",
        note_cn="纵向研究确实发现衰退、失业或 GDP 暴露与死亡率有关，但国家、年龄、"
                "性别、死因与社会保障不同，方向和显著性都不同。论文没有直接联合估计"
                "本引擎的股票回报、失业与通胀抽样；把宏观代理映射到这些目标是模型推断。"
                "它们不足以支持一条年度系数；现行独立抽样是已检查未解决，不是零效应。",
        note_en="Longitudinal evidence does relate economic cycles to mortality, "
                "using recession, unemployment or GDP exposures, but sign and "
                "significance vary by country, age, sex, cause and social "
                "protection. The papers do not jointly estimate mortality with "
                "this engine's equity-return, layoff or inflation draws; mapping "
                "the macro proxies to those targets is a model inference. They "
                "do not support one annual coefficient here; the independent "
                "draw is examined unresolved, not a zero-effect finding."),
    "accumulation_preview": Entry(
        "accumulation_preview", STRUCTURALLY_LINKED, NOT_A_NUMBER,
        relates_to=("accumulation_resume", "mortality"),
        code_refs=("engine/fire_v9_8_model.py:_sample_household_accum_mortality_schedule",),
        note_cn="preview 暂存并还原同一 RNG 状态，预看家庭死亡日程与累计抽样数；它"
                "不是另一个现实随机变量。",
        note_en="The preview snapshots and restores the same RNG state to "
                "compute the household mortality schedule and draw counts. It "
                "is not a second real-world random variable."),
    "accumulation_resume": Entry(
        "accumulation_resume", STRUCTURALLY_LINKED, NOT_A_NUMBER,
        relates_to=("accumulation_preview", "mortality"),
        code_refs=("engine/fire_v9_8_model.py:_resume_household_accum_mortality",),
        note_cn="resume 按 preview 记录的 draw_count 在真实共享流上重放抽样，故两者"
                "刻意是同一死亡日程的两阶段；称它们独立会把实现机制说反。",
        note_en="Resume advances the real shared stream by the draw count "
                "recorded by preview. They are deliberately two stages of one "
                "mortality schedule; calling them independent reverses the code."),
    "ltc_onset": Entry(
        "ltc_onset", STRUCTURALLY_LINKED, NOT_A_NUMBER,
        relates_to=("ltc_progression", "mortality"),
        code_refs=("engine/ltc_model.py:sample_ltc_events",
                   "engine/ltc_model.py:calibration_for"),
        note_cn="LTC 进入危险率先用本计划死亡率校准；只有抽到 onset 才会继续抽时长"
                "与护理等级。onset、progression 与 mortality 是一条条件过程。",
        note_en="The LTC entry hazard is calibrated from the plan's mortality; "
                "duration and care level are drawn only after onset. Onset, "
                "progression and mortality form one conditional process."),
    "ltc_progression": Entry(
        "ltc_progression", STRUCTURALLY_LINKED, NOT_A_NUMBER,
        relates_to=("ltc_onset", "mortality"),
        code_refs=("engine/ltc_model.py:sample_ltc_events",),
        note_cn="这里的 progression 是 onset 后的时长与护理等级，不是与 onset 并列"
                "独立发生的事件；没有 onset 就不会产生该输出。",
        note_en="Progression here is the duration and care level conditional on "
                "onset, not an event independent of onset; without onset this "
                "output does not occur."),
    "parent_mortality": Entry(
        "parent_mortality", STRUCTURALLY_LINKED, NOT_A_NUMBER,
        relates_to=("parent_care_entry", "parent_care_timing", "parent_care_level"),
        code_refs=("engine/parents_model.py:sample_parents",),
        note_cn="每位父母只抽一次死亡年龄；护理窗口由该死亡年龄截断，遗产也在同一"
                "死亡时点到账。这是共享父母对象，不是四个独立模块。",
        note_en="Each parent gets one death age. That death truncates the care "
                "window and dates the bequest, so this is one shared parent "
                "object rather than four independent modules."),
    "parent_care_entry": Entry(
        "parent_care_entry", STRUCTURALLY_LINKED, NOT_A_NUMBER,
        relates_to=("parent_mortality", "parent_care_timing", "parent_care_level"),
        code_refs=("engine/parents_model.py:sample_parents",),
        note_cn="是否进入护理决定同一父母对象后续是否使用时长与等级；三枚 uniform"
                "固定抽取是为保持 RNG 次序，不代表三个现实结果互相独立。",
        note_en="Care entry decides whether the same parent object uses the "
                "timing and level outcomes. Drawing three fixed uniforms keeps "
                "RNG order stable; it is not evidence that the outcomes are "
                "independent in the world."),
    "parent_care_timing": Entry(
        "parent_care_timing", STRUCTURALLY_LINKED, NOT_A_NUMBER,
        relates_to=("parent_mortality", "parent_care_entry", "parent_care_level"),
        code_refs=("engine/parents_model.py:sample_parents",),
        note_cn="护理时长先由 u_when 选出，再倒推 onset = death_age - years，且在死亡"
                "处截断；输出直接依赖父母死亡与护理进入。",
        note_en="The duration selected by u_when is converted to onset as "
                "death_age minus years and clipped at death. The output depends "
                "directly on parent death and care entry."),
    "parent_care_level": Entry(
        "parent_care_level", STRUCTURALLY_LINKED, NOT_A_NUMBER,
        relates_to=("parent_mortality", "parent_care_entry", "parent_care_timing"),
        code_refs=("engine/parents_model.py:sample_parents",),
        note_cn="护理等级只有在进入护理后才决定费用；费用又先消耗父母遗产，故与"
                "死亡时点、护理进入/时长及遗产输出结构相连。",
        note_en="Care level sets cost only after care entry, and that cost first "
                "reduces the parent's estate. It is structurally linked to "
                "death, care entry/timing and the bequest output."),
})


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
                           if e.stance == MODELLED_NUMERIC),
        "structurally_linked": sorted(
            e.module for e in REGISTRY.values()
            if e.stance == STRUCTURALLY_LINKED),
        "examined_unresolved": sorted(
            e.module for e in REGISTRY.values()
            if e.stance == EXAMINED_UNRESOLVED),
        "bare": sorted(e.module for e in REGISTRY.values()
                       if e.grade == BARE),
        "no_ui_control": sorted(e.module for e in REGISTRY.values()
                                if e.ui_control is False),
    }
