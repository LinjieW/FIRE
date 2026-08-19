"""Which approximations YOUR configuration actually hits.

ROADMAP 4.0 Phase 4, the first of the cheap-honesty items. The page already
carries a list of twenty-four limitations and renders all of them to everyone,
which is honest and nearly useless: a reader cannot tell which four sentences
are about the plan in front of them. Worse, a list that is always the same is
a list nobody reads twice, so it stops working exactly as the plan gets more
opt-in modules switched on and the caveats start to matter more.

This is the same set of facts keyed by configuration. The rules are
deterministic and each names a real config path, so "you turned this on, here
is what it approximates" is computed rather than remembered.

**The closure is the point, not the panel.** ROADMAP asks that every opt-in
module, when enabled, trigger at least one corresponding disclosure -- turning
"limitations first" from a habit into something a test can check. That test
lives in `tests/test_limitations.py` and it enumerates the module gates from
`default_config()` rather than from a list here, so a module added later is
uncovered loudly instead of quietly.

**What this is not.** It does not replace the general list. Some limitations
are properties of the whole approach -- annual time steps, no intra-year
sequence, US-only rules -- and are true whatever you configure. Those stay
where they are; conditioning them on a flag would imply you could switch them
off.
"""
from __future__ import annotations

from typing import Any, Callable, Optional


class Rule:
    """One disclosure, and the condition under which it applies.

    `when` receives the whole config and returns a bool. It is a callable
    rather than a declarative match because several of these are genuinely
    conditional on a combination -- LTC with a couple is a different statement
    from LTC alone -- and encoding that in data would produce a small language
    nobody else can read.

    `gates` names the config paths this rule speaks for. It is what the
    closure test reads, and it is checked against `default_config()` so a rule
    for a path that no longer exists fails rather than silently never firing.
    """

    def __init__(self, rule_id: str, gates: tuple, when: Callable[[dict], bool],
                 zh: str, en: str):
        self.id = rule_id
        self.gates = gates
        self.when = when
        self.zh = zh
        self.en = en


def _leaf(cfg: dict, path: str) -> Any:
    node: Any = cfg
    for part in path.split("."):
        if not isinstance(node, dict):
            return None
        node = node.get(part)
    return node


def _below_one(value) -> bool:
    """True only for a real number strictly under 1.0.

    Written as a helper rather than inline because the inline version reached
    for `_leaf(cfg, path, default)` -- a three-argument form this module's
    `_leaf` does not have. The TypeError was caught by `triggered`'s guard and
    reported as `applies: None`, so the rule appeared to fire on EVERY config,
    including ones with no `rule` section at all. A predicate that raises does
    not read as broken; it reads as universally true.
    """
    if value is None or isinstance(value, bool):
        return False
    try:
        return float(value) < 1.0
    except (TypeError, ValueError):
        return False


def _on(path: str) -> Callable[[dict], bool]:
    return lambda cfg: bool(_leaf(cfg, path))


def _mode_on(path: str) -> Callable[[dict], bool]:
    def check(cfg: dict) -> bool:
        value = _leaf(cfg, path)
        value = getattr(value, "value", value)
        return bool(value) and str(value) != "off"
    return check


#: The rules, in the order they will be shown. Ordering is by how much the
#: approximation can move a number, as far as that is knowable, rather than by
#: config order -- a reader who stops after three should have read the three
#: that matter most.
RULES = [
    Rule("tax_true", ("tax_true.enabled",), _on("tax_true.enabled"),
         "真实逐年税表已开启：用的是 2026 年的联邦表，且**不随年份更新**。"
         "州税走平率或原型，不是任何一个州的真实税法。这些数字会随立法变化，"
         "而这个 App 不联网、不会自己更新。",
         "True year-by-year taxes are on: these are 2026 federal tables and "
         "they do NOT roll forward with the calendar. State tax is a flat rate "
         "or an archetype, not any state's real code. Legislation moves and "
         "this app makes no network requests, so it will not update itself."),
    Rule("ltc", ("ltc.mode",), _mode_on("ltc.mode"),
         "长期护理已开启：进入概率与时长来自公开分布的近似，"
         "**Medicaid spend-down 明确不建模**。护理成本按医疗通胀增长，"
         "而你所在地区的实际价格可能与全国分布相差很远。",
         "Long-term care is on: entry probability and duration are "
         "approximations from published distributions, and Medicaid "
         "spend-down is explicitly NOT modelled. Care costs grow with medical "
         "inflation, and local prices can differ sharply from national ones."),
    Rule("parents", ("parents.mode",), _mode_on("parents.mode"),
         "父母生命周期已开启：赡养时长与遗产时点由同一条父母死亡率抽样联动，"
         "两者的相关性用一个保守拨盘表示，**不是从数据估计出来的**。",
         "The parent lifecycle is on: support duration and inheritance timing "
         "are driven by one shared parent-mortality draw, and the correlation "
         "between them is a conservative dial rather than an estimate from "
         "data."),
    Rule("guaranteed_income", ("guaranteed_income.mode",),
         _mode_on("guaranteed_income.mode"),
         "保底收入工具已开启：**报价率是你自己填的**，本 App 不带保险产品库、"
         "不做比价，也无法核实你拿到的报价是否有竞争力。",
         "A guaranteed-income tool is on: the quote rate is one YOU entered. "
         "This app carries no product database, does no shopping, and cannot "
         "tell you whether the quote you were given is competitive."),
    Rule("household", ("household.enabled",), _on("household.enabled"),
         "家庭/夫妻模式已开启：两人的死亡抽样与遗属规则已建模，"
         "但**遗属地板检查是单独一件事**，成功率本身不告诉你"
         "「任一方先逝时活着的那位是否仍被覆盖」。",
         "Household mode is on: two-life mortality and survivor rules are "
         "modelled, but the survivor floor check is a separate question — a "
         "success rate does not tell you whether the survivor stays covered "
         "when either spouse dies first."),
    Rule("mortality", ("mortality.enabled",), _on("mortality.enabled"),
         "死亡抽样已开启：用的是全体人口生命表，"
         "**不按你的健康状况、收入或队列调整**。"
         "「没花完就去世」在结果里算成功，这与「钱够花」是两个不同的问题。",
         "Mortality sampling is on: it uses population life tables and is NOT "
         "adjusted for your health, income or birth cohort. Dying with money "
         "left counts as success here, which is a different question from "
         "whether the money would have lasted."),
    Rule("layoff", ("layoff.enabled",), _on("layoff.enabled"),
         "失业冲击已开启：只作用于积累期，按当年缴款乘以中断比例削减，"
         "**不建模再就业时的薪资折损**，也不建模失业保险。",
         "Layoff shocks are on: they act only during accumulation and reduce "
         "that year's contributions by the interrupted fraction. Wage scarring "
         "on re-employment is not modelled, and neither is unemployment "
         "insurance."),
    Rule("human_capital", ("human_capital.enabled",),
         _on("human_capital.enabled"),
         "职业路径已按随机过程建模，分成**持久冲击**（丢掉的层级，你带着走）与"
         "**暂时冲击**（缓过来的坏年份）—— 用一个「工资波动率」同时表示两者，"
         "等于断言丢一次晋升和少发一次奖金是同一件事。"
         "**两个波动率都没有来源** —— 它们随行业与周期差异极大，"
         "本 App 不替你选一个。职业与市场在本模型里**独立抽样**，"
         "而现实中裁员潮和熊市同时来 —— 那条相关性已在相关性注册表里登记为"
         "「刻意独立、无出处」，不是被忽略。",
         "The career path is modelled as a stochastic process, split into "
         "PERMANENT shocks (a level you carry) and TRANSITORY ones (a bad "
         "year you recover from). One 'wage volatility' dial would assert "
         "that losing a promotion and missing a bonus are the same event. "
         "Neither volatility has a source -- they vary enormously by "
         "occupation and cycle, and this app will not pick one for you. "
         "Careers are drawn independently of markets here, while in reality "
         "layoffs and bear markets arrive together; that correlation is "
         "registered in the correlation registry as a deliberate "
         "independence with no source, rather than overlooked."),
    Rule("downsize", ("other_assets.downsize_enabled",),
         _on("other_assets.downsize_enabled"),
         "缩表已建模为「卖出 + 买入」：新住处的价格在同一年扣除，"
         "此后房产税与维护按**新房**计算。**搬家的摩擦成本没有单独建模** —— "
         "中介以外的搬迁费、重新装修、两地重叠期的持有，都不在里面；"
         "把它们算进「卖房折价」是最接近的做法，但那是你的估计不是本 App 的。",
         "Downsizing is modelled as a sale AND a purchase: the new home's "
         "price is charged in the same year, and property tax and "
         "maintenance are computed from the NEW house from then on. Moving "
         "friction beyond the agent is NOT separately modelled -- removal "
         "costs, making the new place liveable, any overlap where you carry "
         "both -- and folding those into the sale discount is the closest "
         "available approximation, but it is your estimate rather than this "
         "app's."),
    Rule("house_price", ("house_price.enabled",), _on("house_price.enabled"),
         "自住房价格已按随机过程建模。**波动率与漂移都不来自任何房价指数** —— "
         "各地差异极大，本 App 不替你选。漂移默认 0：开启一个模块应该让不确定性出现，"
         "而不是悄悄把中心情形挪走。价格与市场、通胀**独立抽样**，"
         "而现实中它们几乎肯定相关（利率、就业）—— 那些系数本项目不靠猜来填，"
         "所以这是一条已登记的刻意独立，不是一个发现。"
         "若同时开了「计入净资产」：房子的价值**单独列示、永不并入可支配财富**，"
         "因为它非流动、你要住在里面。",
         "The house price is modelled as a stochastic process. Neither the "
         "volatility nor the drift comes from any house-price index -- "
         "dispersion between markets is enormous and this app will not pick "
         "one for you. Drift defaults to zero, because switching a module on "
         "should make uncertainty appear rather than quietly move the central "
         "case. The price is drawn independently of markets and inflation, "
         "which is almost certainly untrue of the world (rates, employment); "
         "those coefficients will not be filled in by guessing, so this is a "
         "registered deliberate independence rather than a finding. If the "
         "net-worth switch is also on, the home's value is reported "
         "SEPARATELY and never folded into spendable wealth, because it is "
         "illiquid and you live in it."),
    Rule("correlation_assumptions", ("bonds.correlation_with_equity",),
         # Fires whenever the plan actually carries the parameter, rather than
         # always. A config missing whole sections has asserted nothing about
         # how its pieces move together, and disclosing to it would be the
         # false positive this suite exists to prevent. Every real plan has
         # this leaf, so in practice it is always on -- which is correct: the
         # equity/bond correlation applies to every path that draws returns.
         lambda cfg: _leaf(cfg, "bonds.correlation_with_equity") is not None,
         "本模型对「各部分如何一起动」的假设已逐条登记（`server/correlation_registry.py`）："
         "17 个随机模块里，3 条建模了相关性、2 条刻意独立、"
         "**12 条只是「模型里独立」—— 是否本该相关，本项目没有检验过**。"
         "其中股债相关性（0.15）没有外部来源、**页面上也没有控件**；"
         "失业与市场那条（坏年份 ×3）**在本仓库里没有任何出处**。"
         "登记它们不是为它们辩护，是让它们不再隐形。",
         "This model's assumptions about how its pieces move together are "
         "listed one by one (server/correlation_registry.py). Of seventeen "
         "sampling modules, three model a correlation, two are deliberately "
         "independent, and twelve are independent only IN THE MODEL -- "
         "whether they should be correlated has not been examined. The "
         "equity/bond correlation (0.15) has no external source and no UI "
         "control at all, and the layoff/market one (3x in a bad year) has no "
         "provenance anywhere in this repository. Listing them is not a "
         "defence of them; it is so that they stop being invisible."),
    Rule("relocation", ("relocation.enabled",), _on("relocation.enabled"),
         "跨国搬迁已开启：生活成本比与汇率波动是**风格化的**，"
         "签证、医保资格与税务居民身份的实际规则不在建模范围内。",
         "Relocation is on: the cost-of-living ratio and FX volatility are "
         "stylized. Visas, healthcare eligibility and tax-residency rules are "
         "outside what this models."),
    Rule("housing", ("housing.enabled", "housing.mode"), _on("housing.enabled"),
         "住房模块已开启：房价按一个独立的实际增长率演化，"
         "**不建模地区差异、房产税重估或强制性大修**。",
         "Housing is on: prices follow a single real growth rate. Regional "
         "divergence, property-tax reassessment and forced major repairs are "
         "not modelled."),
    Rule("medical_trajectory", ("medical.annual_trajectory_enabled",),
         _on("medical.annual_trajectory_enabled"),
         "医疗费用轨迹已开启：保费**由你自己填**（本 App 不携带 CMS 保费表），"
         "医疗通胀作为 ensemble 的一个维度而不是单点真理。",
         "The medical cost trajectory is on: premiums are values YOU entered "
         "(this app ships no CMS premium table), and medical inflation enters "
         "as an ensemble dimension rather than as a single truth."),
    Rule("cut_realisation", ("rule.cut_realisation",),
         lambda cfg: _below_one(_leaf(cfg, "rule.cut_realisation")),
         "你把「护栏触发后实际砍多少」调到了 100% 以下：模型现在假设**你砍不满**。"
         "没砍掉的那部分留在支出里，变成风险 —— 这正是这个拨盘存在的意义。"
         "**默认值 1.0 是这个引擎一直以来的假设**（触发即全额砍到位），"
         "而那个假设从来没有对照真实行为校准过。",
         "You have set how much of a triggered guardrail cut actually happens "
         "below 100%: the model now assumes you do NOT cut all the way. The "
         "part you do not cut stays in your spending and shows up as risk, "
         "which is the point of the dial. The default of 1.0 is what this "
         "engine has always assumed -- a triggered cut landing in full -- and "
         "that assumption has never been calibrated against real behaviour."),
    Rule("ss_trust_fund", ("ss_trust_fund.enabled",),
         _on("ss_trust_fund.enabled"),
         "社保信托基金枯竭已开启：模型呈现的是**国会不作为**的机械后果 —— "
         "储备耗尽后只能用当年工薪税支付，低于承诺给付。**这不是对立法的预测**："
         "历史上每一次基金临近枯竭，国会都动了手，而那件事无法预测，本模型也不试图预测。"
         "数字来自 **2026 年 OASDI Trustees Report**（intermediate 假设于 2026 年 2 月确定），"
         "三个枯竭年份是报告自己的三套方案；**把三者等概率对待是本 App 的选择，不是报告的** —— "
         "报告不给任何方案赋概率。可付比例只有 intermediate 一条路径是公开的"
         "（枯竭当年 78%、2100 年 62%），**其余两套方案的比例报告未给出，本 App 不编**，"
         "三套方案统一套用 intermediate 的可付路径。"
         "**两个端点之间的直线是本 App 的插值，不是报告的** —— "
         "看 2060 年那个数，就是在看没有任何人公布过的数字。"
         "2100 年之后按 62% 持平，不外推。DI 基金在 75 年投影期内始终为正，不建模其枯竭；"
         "常被引用的「合并 2034 年」是**理论值**（法律不允许两基金互借），本模型不据此削减。",
         "Social Security trust fund depletion is on: the model shows the "
         "mechanical consequence of Congress NOT acting -- once reserves run "
         "out, benefits come from that year's payroll tax alone, which is "
         "less than what is scheduled. This is NOT a prediction about "
         "legislation: Congress has acted every prior time a fund neared "
         "depletion, and that is precisely what cannot be forecast. The "
         "figures are the 2026 OASDI Trustees Report's (intermediate "
         "assumptions set February 2026). The three depletion years are the "
         "report's own alternatives; weighting them equally is THIS APP'S "
         "choice, not the report's, which attaches no probability to any of "
         "them. Only the intermediate payable path is published (78% at "
         "depletion, 62% in 2100); the other two alternatives' percentages "
         "are not, and are not invented -- all three scenarios use the "
         "intermediate path. The straight line between those two endpoints "
         "is THIS APP'S interpolation, not the report's: the number you see "
         "for 2060 is one nobody published. Past 2100 the share holds at 62% "
         "rather than extrapolating. DI reserves are projected positive "
         "throughout the 75-year period, so no DI depletion is modelled, and "
         "the widely quoted combined 2034 date is theoretical -- the law "
         "does not permit interfund borrowing -- so it never reduces a "
         "benefit here."),
    Rule("blocky_spending", ("blocky_spending.enabled",),
         _on("blocky_spending.enabled"),
         "块状支出已开启：支出不再是一条平线，每年按一个概率落一次大额。"
         "**那个概率和金额是你（或默认值）填的，不是校准出来的** —— "
         "本 App 不携带任何人的实际支出史，默认的「约七年一次、当年支出 +35%」"
         "是一个占位数字，不是发现。到达时点与市场抽样**独立**："
         "现实中修屋顶和熊市可能同时来，这个模型不建模那种相关性。",
         "Blocky spending is on: spending is no longer a flat line, and a "
         "lump lands with a probability each year. That probability and size "
         "are values YOU (or the defaults) supplied, not calibrated ones -- "
         "this app ships nobody's actual spending history, and the default "
         "of roughly once every seven years at +35% is a placeholder rather "
         "than a finding. Arrivals are INDEPENDENT of market draws: in real "
         "life a roof and a bear market can arrive together, and that "
         "correlation is not modelled."),
    Rule("roth_ladder", ("roth_ladder.enabled",), _on("roth_ladder.enabled"),
         "Roth 转换阶梯已开启：转换额被「可用应税账户的 4 倍」封顶、"
         "转换税按一个平率计 —— **两者都是启发式，没有对照真实转换成本校准过**。",
         "The Roth conversion ladder is on: conversions are capped at 4x "
         "available taxable and taxed at a flat rate. Both are heuristics and "
         "neither has been checked against what a real conversion costs."),
    Rule("social_security", ("social_security.enabled",),
         _on("social_security.enabled"),
         "社保已开启：按现行规则计算，**不建模未来立法削减**。"
         "如果你想看削减情景，把 PIA 自己调低，而不是指望模型替你打折。",
         "Social Security is on: computed under current rules, with NO future "
         "legislative cut modelled. To see a cut scenario, lower the PIA "
         "yourself rather than expecting the model to discount it for you."),
    Rule("eldercare", ("eldercare.mode",), _mode_on("eldercare.mode"),
         "赡养支出已开启：这是旧的独立模块。"
         "若同时开启父母生命周期，两者可能重复计入同一笔负担。",
         "Eldercare support is on: this is the older standalone module. With "
         "the parent lifecycle also on, the two can double-count the same "
         "burden."),
    Rule("inheritance", ("inheritance.mode",), _mode_on("inheritance.mode"),
         "预期遗产已开启：**一个依赖遗产才成立的计划应当被单独检验**——"
         "把它关掉再跑一次，看结论是否还成立。",
         "Expected inheritance is on: a plan that only works because of an "
         "inheritance deserves to be checked without one. Turn it off and run "
         "again to see whether the conclusion survives."),
    Rule("obbba", ("obbba.mode",), _mode_on("obbba.mode"),
         "OBBBA 情景已开启：这是对一项**立法**的情景假设，不是预测，"
         "也不代表本 App 对它是否发生持任何看法。",
         "An OBBBA scenario is on: it is a scenario about legislation, not a "
         "forecast, and implies no view from this app about whether it "
         "happens."),
    Rule("ftc", ("ftc.enabled",), _on("ftc.enabled"),
         "外国税收抵免已开启：按简化规则计算，"
         "不建模分篮、结转，也不建模税收协定的具体条款。",
         "The foreign tax credit is on: computed under simplified rules, with "
         "no basketing, no carryforward and no treaty specifics."),
    Rule("sh_property", ("sh_property.enabled",), _on("sh_property.enabled"),
         "自住房产已开启：按一个实际增长率演化，交易成本为风格化估计。",
         "A primary residence is on: it follows one real growth rate and its "
         "transaction costs are a stylized estimate."),
    Rule("promotion", ("promotion.enabled",), _on("promotion.enabled"),
         "晋升/加薪路径已开启：按确定性的增长曲线，"
         "**不建模裁员、行业衰退或职业中断**（失业是单独的模块）。",
         "A promotion path is on: it follows a deterministic growth curve and "
         "models neither redundancy, sector decline nor career breaks — "
         "layoff is a separate module."),
]


def triggered(cfg: dict, language: str = "zh") -> dict:
    """The disclosures this configuration actually triggers.

    Returns the rules in `RULES` order, so a reader who stops after three has
    read the three judged most able to move a number.
    """
    if not isinstance(cfg, dict):
        raise TypeError("cfg must be a config dict")
    rows = []
    for rule in RULES:
        try:
            fires = rule.when(cfg)
        except Exception:                                   # noqa: BLE001
            # REACHED FOR REAL ON 2026-08-16, having been unreachable when
            # written. A new rule called `_leaf` with a three-argument form
            # this module does not have; the TypeError landed here and the
            # rule reported `applies: None` on every config, which a test
            # read as "it fires everywhere".
            #
            # That is why this branch exists and why it must stay: without
            # it the raise would have propagated out of `triggered` and taken
            # the whole disclosure panel down. With it, the damage was one
            # rule reporting unknown -- loud enough for three tests to catch
            # in the same run.
            #
            # It stays because the alternative for a future predicate that
            # does raise is to report nothing, and "we could not check" would
            # then be indistinguishable from "it does not apply to you" --
            # the distinction this whole project is built around. A test
            # states the branch is currently unreachable rather than
            # pretending to exercise it.
            rows.append({"id": rule.id, "applies": None,
                         "text": rule.zh if language == "zh" else rule.en,
                         "reason": "this rule could not be evaluated against "
                                   "the configuration given"})
            continue
        if fires:
            rows.append({"id": rule.id, "applies": True,
                         "text": rule.zh if language == "zh" else rule.en,
                         "gates": list(rule.gates)})
    return {
        "triggered": rows,
        "count": len(rows),
        "total_rules": len(RULES),
        "basis": ("These are the approximations your CURRENT configuration "
                  "hits. They are in addition to the general limitations, "
                  "which hold whatever you configure and are listed "
                  "separately -- conditioning those on a flag would imply you "
                  "could switch them off."),
    }
