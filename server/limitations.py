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

import correlation_registry as CORRELATION


_CORRELATION_SUMMARY = CORRELATION.summary()
_CORRELATION_SHAPE = _CORRELATION_SUMMARY["by_stance"]
#: Derived, never typed. This sentence said "22 sampling modules" while
#: the ledger below it listed 25, and it said so to the USER: the four
#: stance counts were interpolated from the registry from the start, and
#: the total beside them was a literal that nobody updated for three
#: slices. It survived every ordinary gate because the only test that
#: read it is the frozen UI smoke, which runs against a BUILT bundle at
#: promotion time -- so the promotion is where it finally failed.
_CORRELATION_MODULES = _CORRELATION_SUMMARY["modules"]


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
    Rule("simple_retirement_tax", ("tax_true.enabled",),
         # Fires when the true-tax engine is OFF, which is every plan this app
         # ships. Requires the key to be present, like every rule here: an
         # absent block means "not applicable", not "off".
         lambda cfg: _leaf(cfg, "tax_true.enabled") is False,
         "真实逐年税表**关着**,所以退休阶段的税是**每个账户一个平率**:"
         "税前账户按普通所得的平率,应税账户按资本利得的平率。没有税率分级、"
         "没有标准扣除、没有社保计税门槛、没有 RMD、没有 IRMAA ——"
         "那些都在真实税表那一侧。"
         "**应税账户现在只对利得计税,不再连本金一起征**(2026-08-24 修,OPEN_ITEMS E32);"
         "在此之前每一块曾经缴进应税账户的、早已完过税的本金,取出时会被再征一次。"
         "但要知道它从哪里知道「多少算利得」:**开局那一笔的成本基础是假设出来的** ——"
         "用 `tax_true.taxable_gain_fraction`(默认 0.5,意思是「你现在的应税账户里有一半是浮盈」),"
         "而这个数在真实税表关着时你并不会被问到。此后每一年它是**算出来的**:"
         "卖出按比例退役基础,没花完的收入按已完税计入基础。"
         "**如果你今天的应税账户浮盈比例远不是一半,请把那个数改成你自己的。**",
         "True year-by-year taxes are OFF, so the retirement years are taxed "
         "with ONE FLAT RATE PER ACCOUNT: the ordinary rate on pre-tax money, "
         "the capital-gains rate on the taxable account. No brackets, no "
         "standard deduction, no Social Security taxation thresholds, no RMDs "
         "and no IRMAA -- those live on the true-tax side. "
         "The taxable account is now taxed on its GAIN rather than on the "
         "whole sale (fixed 2026-08-24, OPEN_ITEMS E32); before that, every "
         "dollar of principal -- money already taxed on the way in -- was "
         "taxed a second time on the way out. "
         "But be clear where it gets 'how much of this is gain': the OPENING "
         "cost basis is an assumption, `tax_true.taxable_gain_fraction` "
         "(default 0.5, i.e. half of today's taxable account is unrealised "
         "gain), and with the true-tax engine off you are never asked for it. "
         "From there it is measured: sales retire basis in proportion, and "
         "income you did not spend is credited as basis because it was "
         "already taxed. If your own account is nowhere near half gain, set "
         "that number to yours."),
    Rule("gov_457b", ("initial.gov_457b", "contributions.gov_457b_y1"),
         lambda cfg: ((_leaf(cfg, "initial.gov_457b") or 0) > 0
                      or (_leaf(cfg, "contributions.gov_457b_y1") or 0) > 0),
         "你填了**政府 457(b)** 余额。本模型给它的唯一特殊待遇,也是它唯一真正特殊的地方:"
         "**离职后任何年龄提取都没有 10% 提前提取罚金**。提取时按普通所得计税,与税前 401(k) 相同,"
         "并且**排在 401(k) 之前**被取(两个出口税一样,只有一个有罚金)。"
         "三件**没有**建模的事,写在这里而不是让你自己发现:"
         "(1) **正常退休年龄前三年的特殊补缴不建模** —— 它是「两倍基本限额」与"
         "「基本限额加历年未用额度」的较小者,且不能与 50 岁补缴叠加;"
         "算它需要一个正常退休年龄和一份历年未用额度的历史,这个 App 两样都没有。"
         "(2) **非政府(tax-exempt)457(b) 完全不建模,也不该填在这里** ——"
         "那种计划的钱在法律上仍是雇主的一般财产,雇主破产时债权人可以拿走它,"
         "而且离职后的提取时间往往由计划文件锁定。把它当成政府 457(b) 填,"
         "会让这个模型高估你的安全度。"
         "(3) **CSV 导入的行为在 2026-08-24 变了**:名字里含「457」的账户此前被折进"
         "税前 401(k)(于是被算了它没有的 10% 罚金),现在归入本桶。"
         "如果你在那之前导入过并保存了计划,重新导入才会拿到新的归类。"
         "(4) **缴款侧不给它补缴额度。** 457(b) 允许 50 岁补缴,"
         "但「同时参加 401(k) 和 457(b) 的人能不能各补一次」在本次核对过的 IRS 一手页面上"
         "**没有明说**,所以这里少算而不是多算 —— 与 HSA 工资税那条同一个方向。"
         "它的缴款限额本身**确实是各算各的**(§457(e)(15),2026 年 $24,500,Notice 2025-67),"
         "两边可以都缴满,超过会被**点名拒绝**而不是悄悄削平。"
         "(5) **它不进 415(c) 年度合计。** 那个上限管的是同一个固定缴款计划里的年度增加额,"
         "而 457(b) 是另一个计划。",
         "You have entered a **governmental 457(b)** balance. The one special "
         "thing this model gives it is the one thing that is actually "
         "special: **no 10% early-withdrawal penalty at any age once you have "
         "separated from that employer**. Withdrawals are taxed as ordinary "
         "income exactly like a pre-tax 401(k), and it is drawn BEFORE the "
         "401(k) -- same exit tax, and only one of them carries a penalty. "
         "Three things it does NOT do, stated here rather than left to be "
         "discovered: "
         "(1) The special catch-up for the three years before normal "
         "retirement age is NOT modelled. It is the lesser of twice the basic "
         "limit and the basic limit plus your unused room from prior years, "
         "and it cannot be combined with the age-50 catch-up; computing it "
         "needs a normal retirement age and a history of unused room, and "
         "this app has neither. "
         "(2) A non-governmental (tax-exempt) 457(b) is not modelled at all "
         "and does not belong in this field. That money remains the "
         "employer's general asset -- creditors can reach it in a bankruptcy "
         "-- and the plan document usually fixes when you may take it after "
         "leaving. Entering one here would make this model overstate how safe "
         "you are. "
         "(3) The CSV importer changed on 2026-08-24: an account whose name "
         "contains '457' used to be folded into the pre-tax 401(k), and was "
         "therefore charged a 10% penalty it does not have. It lands in this "
         "bucket now. A plan you imported and saved before that date keeps "
         "its old classification until you import again. "
         "(4) NO catch-up is credited on the contribution side. A "
         "governmental 457(b) may allow the age-50 catch-up, but whether "
         "somebody in both a 401(k) and a 457(b) gets one in EACH is not "
         "stated on any first-party IRS page this was checked against, so the "
         "model under-credits rather than invents room -- the same direction "
         "it takes on the HSA payroll-tax question. The LIMIT itself really "
         "is separate (section 457(e)(15), $24,500 for 2026, Notice 2025-67): "
         "both plans can be filled, and a figure above it is refused by name "
         "rather than quietly capped. "
         "(5) It does not enter the section 415(c) annual additions test. "
         "That cap governs additions to one defined-contribution plan, and a "
         "457(b) is a different plan."),
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
    Rule("student_debt", ("student_debt.enabled",),
         _on("student_debt.enabled"),
         "学生贷款已开启：这里只建模**一笔固定名义月供**。未偿余额按用户裁定 A "
         "提高 FIRE 门槛，退休后仍按同一合同表继续付，不会在 FIRE 时假装一次还清。"
         "当前生活开销被视为已包含当前月供；结清后释放的现金只在 residual 模式增加储蓄，"
         "savings-rate 模式仍以你填的储蓄率为准。**没有建模** IDR 随收入重算、宽免、"
         "学生贷款利息抵税、额外还款优化、多笔贷款、拖欠或违约后果。",
         "Student debt is on. This first slice models ONE fixed nominal "
         "monthly payment. Under user choice A, the outstanding balance raises "
         "the FIRE threshold and the same schedule keeps paying in retirement; "
         "the loan is not pretended away at FIRE. Current working spending is "
         "treated as already containing today's payment. Cash freed after "
         "payoff raises saving only in residual mode; savings-rate mode keeps "
         "the rate you stated authoritative. IDR recalculation, forgiveness, "
         "the student-loan interest deduction, extra-payment optimization, "
         "multiple loans, delinquency, and default consequences are NOT modelled."),
    Rule("lifestyle_creep", ("lifestyle_creep.mode",),
         _mode_on("lifestyle_creep.mode"),
         "生活方式膨胀已开启：积累期会抽一次永久的真实生活开销上调，"
         "同时抬高工作期预算与 FIRE 支出目标。默认 15%、2–5 年及截断正态分布"
         "来自旧引擎合同，**没有外部校准**，不代表你的职业或家庭。它使用独立随机流，"
         "所以不会改变同 seed 的市场、通胀或失业抽样；这只是模型设计，**不是现实中彼此独立的证据**。"
         "学生贷款的固定名义月供不会被一起放大，savings-rate 模式仍以你填的储蓄率为准。",
         "Lifestyle creep is on. One permanent real-spending step is sampled "
         "during accumulation and raises both the working budget and the FIRE "
         "spending target. The 15% default, years 2-5 window, and clipped-normal "
         "shape were inherited from the old engine contract and are not calibrated "
         "to your career or household. It is sampled independently so enabling it "
         "does not move same-seed market, inflation, or layoff draws; that is a "
         "model design choice, not evidence that these events are independent in "
         "real life. Fixed nominal student-loan payments are excluded from the "
         "step, and savings-rate mode keeps the rate you stated authoritative."),
    Rule("career_break", ("career_break.enabled",),
         _on("career_break.enabled"),
         "计划职业休假已开启。**三件事你应该知道；其中两件没有建模，都让休假看起来比实际便宜：**"
         "① 晋升时钟照常走 —— 休假跨过晋升年时，复工后按届时应有的职级工资计算，"
         "本模型**没有**建模休假对晋升时点的影响（用户裁定 U26）。"
         "「复工工资折价」是本模型表达工资疤痕的**唯一**位置；"
         "如果你认为空档会让职级或加薪推迟，请自己把它折进那个数字。"
         "② 休假年的生活费**从应税账户里取**（用户裁定 U27）——但**只从那里取**："
         "401(k)/IRA/HSA 在 59.5 岁前提取有罚金和税，本 App 不计算它们，所以那些账户"
         "永远不会被动用；应税账户取空之后仍未覆盖的部分，会单独如实列出。"
         "③ 休假期的**净新增家庭医保保费由你填写**，并在 residual 模式下先于缴款扣；"
         "savings-rate 模式仍以你填的储蓄率为准，保费占用残差生活费。App 不会替你选择"
         "配偶计划、COBRA 或 Marketplace，也不计算积累期 ACA subsidy、deductible 或 OOP。",
         "A planned career break is on. THREE things to know; two of them are not "
         "modelled, and both make a break look cheaper than it is. (1) The "
         "promotion clock "
         "keeps running: if the break spans your promotion year, you return on "
         "the salary that level would have reached by then, because the effect "
         "of a break on promotion TIMING is not modelled (user ruling U26). "
         "The return-to-work pay discount is the ONLY place wage scarring "
         "lives here — if you believe a gap would delay your level or your "
         "raises, fold that into that number yourself. (2) Break-year living "
         "costs ARE drawn from your taxable account (ruling U27) -- but only "
         "from there. A 401(k)/IRA/HSA withdrawal before 59.5 carries penalties "
         "and tax this app does not compute, so those accounts are never "
         "raided, and anything the taxable account could not fund is reported "
         "separately as still unfunded. (3) You supply the net-new annual "
         "household health premium during the break, and residual mode pays it "
         "before saving. Savings-rate mode keeps your stated rate authoritative "
         "and absorbs the premium inside residual spending. The app does not "
         "choose a spouse plan, COBRA or Marketplace coverage, or compute an "
         "accumulation-phase ACA subsidy, deductible, or out-of-pocket cost."),
    Rule("self_employed", ("contributions.employment_type",
                            "contributions.self_employed_profit_mode"),
         lambda cfg: _leaf(cfg, "contributions.employment_type")
         == "self_employed" and _leaf(
             cfg, "contributions.self_employed_profit_mode") != "uniform",
         "你已声明自雇,所以工作年份按**自雇税(SECA)**收:社保 12.4%、医保 2.9%,"
         "税基是净利润的 92.35%,自雇税的一半在算所得税前扣掉。"
         "雇主那一份现在可以填了(「雇主非选择性缴款」),SEP / Solo 401(k) 的雇主那一半"
         "按 `r/(1+r)` 换算,并受 415(c) 上限约束。"
         "**还剩两件事**:"
         "(1) **收入被当成平滑的** —— 自雇收入的波动、淡旺季、坏年份都不在这里;"
         "(2) **除 SIMPLE 递延限额外,计划类型本身没有建模** —— 引擎只有「匹配率」和"
         "「非选择性比例」两个雇主机制,SEP / Solo 401(k) 的资格条件、SIMPLE 的雇主强制"
         "二选一、以及 403(b)/457(b) 的特殊补缴,仍要你自己换算。",
         "You have said you are self-employed, so the working years are charged "
         "self-employment tax: 12.4% Social Security and 2.9% Medicare on 92.35% "
         "of net profit, with half of it deducted before income tax. "
         "The employer side can now be entered (\"employer contribution "
         "regardless of deferral\"): a SEP or Solo 401(k) employer half "
         "converts as r/(1+r) and is capped by section 415(c). Two things "
         "remain. (1) Your income is modelled as smooth -- the variability, "
         "seasonality and bad years of self-employment are not here. (2) Plan "
         "types other than SIMPLE deferral limits are not modelled: the engine "
         "has a match rate and a non-elective rate, and you have to translate "
         "SEP/Solo eligibility and employer terms into them yourself. A SIMPLE "
         "employer's mandatory choice between matching and non-elective, and "
         "the special 403(b)/457(b) catch-ups, are not applied for you."),
    Rule("simple_workplace_plan",
         ("contributions.workplace_plan_type",
          "contributions.simple_higher_limit",
          "household.spouse_workplace_plan_type",
          "household.spouse_simple_higher_limit"),
         lambda cfg: (
             _leaf(cfg, "contributions.workplace_plan_type") == "simple"
             or (_leaf(cfg, "household.enabled")
                 and _leaf(cfg, "household.spouse_workplace_plan_type")
                 == "simple")),
         "你已选择 SIMPLE。模型会直接读取 rule pack 的 2026 基础递延限额、50+ 补缴和"
         "60–63 补缴,并按每个人自己的计划分别计算;「较高基础限额」只在你确认计划文件适用"
         "时开启。**仍未自动执行**雇主必须在匹配与 2% 非选择性缴款之间二选一、较高限额的"
         "雇主资格/选举条件、参加其他计划时的共享递延上限,以及 SIMPLE IRA 开户头两年的"
         "提前提取 25% 附加税。请按计划文件填写匹配/非选择性比例。",
         "You selected SIMPLE. The model reads the 2026 base, age-50 and "
         "age-60--63 deferral limits from the dated rule pack and applies "
         "each earner's plan independently; the higher base is used only "
         "when you confirm it applies. It does not automatically enforce the "
         "employer's match-versus-2%-nonelective choice, the employer "
         "eligibility/election rules for the higher limit, the shared limit "
         "when you participate in another plan, or the 25% additional tax on "
         "early SIMPLE IRA withdrawals during the first two years. Enter the "
         "match/non-elective terms from your plan documents."),
    Rule("special_403b_catchup",
         ("contributions.catchup_403b_15yr_enabled",
          "contributions.catchup_403b_15yr_schedule_nominal",
          "contributions.catchup_403b_15yr_prior_used_nominal",
          "household.spouse_catchup_403b_15yr_enabled",
          "household.spouse_catchup_403b_15yr_schedule_nominal",
          "household.spouse_catchup_403b_15yr_prior_used_nominal"),
         lambda cfg: (
             bool(_leaf(cfg, "contributions.catchup_403b_15yr_enabled"))
             or bool(_leaf(
                 cfg, "household.spouse_catchup_403b_15yr_enabled"))),
         "你已开启 403(b) 15 年服务补缴。逐年额外额度必须来自计划管理员或 IRS worksheet,"
         "因为它依赖同一合格雇主的服务年限、历年特殊补缴与历年递延;模型不会从工资路径猜"
         "这些历史。引擎按名义美元逐年加入,并直接读取 rule pack 的每年 $3,000 / 终身"
         "$15,000 上限;普通 age-50 补缴在它之后另加。schedule 是你对未来可用额度的情景,"
         "不是模型对雇主记录的预测。",
         "You enabled the 403(b) 15-years-of-service catch-up. Each annual "
         "amount must come from your plan administrator or IRS worksheet, "
         "because it depends on service with the same qualified employer, "
         "prior special catch-ups and prior deferrals; the model does not "
         "infer that history from wages. The nominal schedule is subject to "
         "the rule pack's $3,000 annual and $15,000 lifetime ceilings, then "
         "the ordinary age-50 catch-up is added separately. This schedule is "
         "your scenario for future available room, not a prediction of the "
         "employer's records."),
    Rule("hsa_hdhp_eligibility",
         ("contributions.hsa_limit_y1",
          "contributions.hsa_coverage_tier",
          "contributions.hsa_deductible_y1",
          "contributions.hsa_out_of_pocket_max_y1",
          "contributions.hsa_disqualifying_other_coverage",
          "contributions.hsa_medicare_enrolled",
          "contributions.hsa_claimed_as_dependent",
          "contributions.hsa_eligible_through_age",
          "household.spouse_hsa_limit_y1",
          "household.spouse_hsa_coverage_tier",
          "household.spouse_hsa_deductible_y1",
          "household.spouse_hsa_out_of_pocket_max_y1",
          "household.spouse_hsa_disqualifying_other_coverage",
          "household.spouse_hsa_medicare_enrolled",
          "household.spouse_hsa_claimed_as_dependent",
          "household.spouse_hsa_eligible_through_age"),
         lambda cfg: (float(_leaf(cfg, "contributions.hsa_limit_y1") or 0) > 0
                      or float(_leaf(
                          cfg, "household.spouse_hsa_limit_y1") or 0) > 0),
         "非零 HSA 缴款只在你明确填写 HDHP coverage tier、deductible、自付上限、其他保障、"
         "Medicare、dependent 身份与资格截止年龄后运行。模型按 2026 rule pack 校验资格与"
         "本人/家庭上限，家庭保障下夫妻共享基础额度；55+ 的 $1,000 补缴按个人增加。"
         "deductible 与自付上限只校验第一年，未来默认沿用这份保障形状直到你填写的截止年龄，"
         "不会预测雇主换计划或 Medicare 实际加入日。",
         "A non-zero HSA contribution runs only after you state the HDHP "
         "coverage tier, deductible, out-of-pocket maximum, other coverage, "
         "Medicare and dependent status, and the last eligible age. The 2026 "
         "rule pack supplies eligibility and contribution ceilings; spouses "
         "share the family base limit and each eligible person age 55+ gets "
         "their own $1,000 catch-up. The deductible and out-of-pocket facts "
         "are checked for year one; the model carries that coverage shape "
         "through your stated end age and does not predict plan changes or "
         "the actual Medicare enrollment date."),
    Rule("temporary_accumulation_expenses",
         ("contributions.childcare_schedule_real",
          "contributions.commuting_schedule_real"),
         lambda cfg: bool(_leaf(cfg, "contributions.childcare_schedule_real"))
         or bool(_leaf(cfg, "contributions.commuting_schedule_real")),
         "托育/育儿与通勤按你填写的逐年今日美元 schedule 进入工作期可负担瀑布；列表结束后"
         "成本归零，不进入 FIRE 退休支出目标。模型不猜孩子年龄、补贴、税收抵免、雇主福利或"
         "通勤方式。若你选择“按储蓄率”，该储蓄率仍是权威输入，因此这些 schedule 只在"
         "“填写生活开销”模式改变缴款。",
         "Childcare and commuting use your annual real-dollar schedules in "
         "the working-years affordability waterfall. Costs become zero after "
         "the list and do not raise the retirement target. The model does not "
         "guess child ages, subsidies, tax credits, employer benefits or commute "
         "mode. Under stated-savings-rate mode that rate remains authoritative, "
         "so these schedules change contributions only in stated-spending mode."),
    Rule("variable_income", ("contributions.employment_type",
                              "contributions.bonus_mode_pre",
                              "contributions.self_employed_profit_mode"),
         lambda cfg: (
             (_leaf(cfg, "contributions.employment_type") == "w2"
              and _leaf(cfg, "contributions.bonus_mode_pre") == "uniform_pct")
             or (_leaf(cfg, "contributions.employment_type") == "self_employed"
                 and _leaf(cfg, "contributions.self_employed_profit_mode")
                 == "uniform")),
         "你已开启年度可变收入。W-2 只让**晋升前奖金/佣金占基础工资的比例**在用户区间内"
         "逐年独立均匀抽样;晋升后仍读既有晋升奖金。自雇/1099 则让**整笔主申报人净利润**"
         "乘用户区间内的年度因子。两者都进入税、缴款、雇主缴款与社保 covered earnings。"
         "这是 bounded uniform,不是行业校准:不建季度/月度波动、客户集中、经济周期相关性或"
         "负利润/亏损;与市场、晋升、人力资本及失业使用独立子流。",
         "Annual variable income is enabled. For W-2, only the PRE-promotion "
         "bonus/commission share of base pay is drawn independently and uniformly "
         "within the user's bounds; post-promotion bonus keeps its existing model. "
         "For self-employed/1099, the whole primary net-profit line receives the "
         "annual user-bounded factor. Both reach tax, contributions, employer money "
         "and Social Security covered earnings. This is bounded uniform, not an "
         "industry calibration: quarterly/monthly seasonality, client concentration, "
         "business-cycle correlation and negative profit/loss are not modelled; the "
         "path uses a child stream independent of markets, promotion, human capital "
         "and layoff."),
    Rule("rsu_vest", ("contributions.rsu_vest_enabled",),
         lambda cfg: bool(_leaf(cfg, "contributions.rsu_vest_enabled")),
         "你已按**归属年份**填入 RSU 归属价值。它被当作归属当年的普通 W-2 工资:"
         "缴所得税与 FICA、计入可负担缴款与雇主缴款口径、并进入社保 covered earnings ——"
         "这正是它与「股权收入流」那条税后现金的区别,两者不能同时开。"
         "**本刀假设归属即卖出**:不建股价过程、不建归属后继续持有的单只股票集中度、"
         "不建 forfeiture/加速归属/cliff、83(b) 或 ISO；ESPP 由独立 §423 合同处理。"
         "也不建年内归属时点(按年计)。"
         "归属收入与基础工资一样会被计划休假与失业按比例缩减、被伤残归零 ——"
         "这是保守处理,不是对你的授予协议的解读。表填到第几年就到第几年,"
         "其后各年归属为 0,那是你说的 0。计划是否把归属计入 match 基数,"
         "跟随既有的「match 排除奖金」开关,不另设第二个开关。",
         "You have entered RSU vest values by vest year. They are treated as "
         "ordinary W-2 wages in the year they vest: income tax and FICA are "
         "charged, contribution affordability and the employer-money base see "
         "them, and they build Social Security covered earnings -- which is "
         "exactly what separates them from the after-tax \"equity income "
         "stream\", and why the two cannot both be on. This slice ASSUMES SALE "
         "AT VEST: no share price process, no post-vest single-stock "
         "concentration, no forfeiture, accelerated vesting or cliff, no "
         "83(b) or ISO; ESPP has its own section 423 contract. There is no "
         "within-year vest timing (annual steps). "
         "Vest income is scaled by a planned break and by a layoff and zeroed "
         "by disability, exactly like base pay -- a conservative treatment, "
         "not a reading of your grant agreement. The schedule ends where you "
         "ended it and later years vest 0, which is your zero rather than an "
         "unmeasured one. Whether the plan counts vest income in the match "
         "base follows the existing \"match excludes bonus\" switch; no second "
         "switch is introduced."),
    Rule("espp", ("contributions.espp_enabled",
                  "contributions.espp_lookback_enabled"),
         lambda cfg: bool(_leaf(cfg, "contributions.espp_enabled")),
         "你开启了 §423 ESPP。每一行是**同一公历年授予并购买**的一批股票；grant-date FMV "
         "按名义美元受每年 $25,000 上限约束，折扣最多 15%。立即卖出按 disqualifying "
         "disposition；qualifying hold 在年度粒度下要求最后一批之后至少两步，普通收入按 "
         "lesser-of 规则、其余正收益按长期资本利得。ESPP 普通收入只进所得税/MAGI，**不进 "
         "FICA、社保 covered earnings、match 或工资缴款空间**。持有的股票在卖出前离开分散"
         "组合，出售价值完全由你逐批填写；模型不猜公司股价、波动率或分红。你填的批次就在你填的"
         "那一年发生：它**不会**被计划休假、失业或伤残按比例缩减（RSU 归属会）——因为 Form 3922 "
         "上的金额是既成事实，按失业比例缩放它会造出一份谁的对账单都对不上的批次；哪一年你没"
         "参加，就把那一行填 0。第一刀不建跨年度 "
         "offering 的 $25,000 accrual、退休后处置、AMT、资本损失抵扣/carryforward、分批卖出或"
         "死亡后的 lot 处置。",
         "You enabled a section 423 ESPP. Each row is one lot granted and "
         "purchased in the SAME calendar year; nominal grant-date FMV is "
         "limited to $25,000 per year and the discount to 15%. Immediate sale "
         "is a disqualifying disposition. At annual grain, qualifying hold "
         "requires at least two steps after the final lot; ordinary income "
         "uses the lesser-of rule and the remaining positive gain is LTCG. "
         "ESPP ordinary income enters income tax and MAGI but NOT FICA, Social "
         "Security covered earnings, match or wage-based contribution room. "
         "Held shares leave the diversified portfolio until sale, and every "
         "sale value is yours to state: no company-stock price, volatility or "
         "dividend is guessed. The lots you state happen in the years you "
         "state them: unlike RSU vesting they are NOT scaled down by a "
         "planned break, a layoff or disability, because a Form 3922 amount "
         "is a fact that already happened and prorating it would invent a "
         "lot no statement matches -- put a zero in any year you did not "
         "participate. Multi-year-offering $25,000 accrual, retirement-"
         "year dispositions, AMT, capital-loss deductions/carryforwards, staged "
         "sales and post-death lot handling are outside this first contract."),
    Rule("rsu_retained", ("contributions.rsu_retained_enabled",),
         lambda cfg: bool(_leaf(cfg, "contributions.rsu_retained_enabled")),
         "你选择了把部分已归属股票留着不卖。模型这样表达它:留下的钱在当年**离开**分散组合"
         "(不再按组合收益复利、也不再可支用),到你填的卖出年龄再按你填的倍数回到应税账户。"
         "**这个差额就是集中的代价本身。**这一半刻意**不问任何波动率**:倍数是你自己陈述的情景,"
         "不是概率分布,更不是对某只股票的预测 —— 它不会告诉你「跌成这样的可能性有多大」。"
         "不建股价路径、分红、成本基础与资本利得(卖出按实际购买力的一笔流入处理)、wash sale、"
         "分批卖出或税务筹划。",
         "You have chosen to keep some vested shares rather than sell them. The "
         "model expresses that by having the kept money LEAVE the diversified "
         "stack in the year you keep it -- it stops compounding at the "
         "portfolio's return and stops being available -- and return to taxable "
         "at your stated sale age, multiplied by the figure you stated. THAT "
         "DIFFERENCE IS THE COST OF CONCENTRATION. This half deliberately asks "
         "for NO VOLATILITY: the multiple is a scenario you state, not a "
         "probability distribution and not a forecast for any stock, so it "
         "cannot tell you how likely such a fall is. No share price path, no "
         "dividends, no cost basis or capital gains (the sale is a single "
         "purchasing-power inflow), no wash sales, staged selling or tax "
         "planning."),
    Rule("rsu_retained_sigma", ("contributions.rsu_retained_sigma_enabled",),
         lambda cfg: bool(
             _leaf(cfg, "contributions.rsu_retained_sigma_enabled")),
         "你给保留的股票填了自己的波动率,所以它按 lognormal 实际路径走到卖出年,"
         "**这时不再读取你填的固定倍数**。σ 与 drift 是**你自己的数字,不是校准值**,"
         "本模型不发布任何单只股票的波动率。"
         "**必须知道的一件事**:drift = 0 锚的是**股票路径自己的中位数**(中位因子 ≈ 1),"
         "而 lognormal 复利多年后右尾很长 —— 均值远高于中位数。"
         "因此打开它有可能把**计划的**中位数抬高。**那是这个分布在说话,不是「拿着更划算」的证据**,"
         "更不是对你那只股票的预测。同时:这条路径走独立子流,"
         "而现实里雇主股票当然和市场、和你的工资相关 —— 这里刻意不猜那个系数,"
         "所以它是「刻意独立」,不是「零相关是真的」。不建分红、成本基础与资本利得、分批卖出。",
         "You have supplied your own volatility for the kept shares, so their "
         "value follows a lognormal real path to the sale age and YOUR FIXED "
         "MULTIPLE IS NO LONGER READ. Sigma and drift are YOUR figures, not a "
         "calibration; this model publishes no volatility for any single "
         "stock. ONE THING YOU MUST KNOW: drift = 0 anchors the MEDIAN OF THE "
         "STOCK'S OWN PATH (median factor about 1), and a lognormal compounded "
         "over many years has a long right tail, so its mean sits far above "
         "its median. Switching this on can therefore RAISE your plan's median. "
         "THAT IS THE DISTRIBUTION SPEAKING, NOT EVIDENCE THAT HOLDING IS "
         "BETTER, and it is not a forecast for your stock. Also: this path uses "
         "an independent child stream, while in reality employer stock is "
         "correlated with the market and with your own pay. No coefficient is "
         "guessed, so that is a deliberate independence rather than a claim "
         "that the correlation is zero. No dividends, cost basis or capital "
         "gains, and no staged selling."),
    Rule("spouse_variable_income", ("household.spouse_bonus_mode_pre",),
         lambda cfg: (_leaf(cfg, "household.spouse_bonus_mode_pre")
                      == "uniform_pct"),
         "配偶的奖金改成按**配偶自己的基础工资**的百分比,在你填的上下界内逐年独立均匀抽样。"
         "这条只管**配偶晋升前**;晋升后由配偶自己的晋升奖金合同接手。"
         "它进入家庭 MAGI、配偶自己的可负担缴款上限与家庭合计劳动收入。"
         "**但它不进入配偶的雇主匹配** —— 匹配按配偶的**基础工资**算,"
         "奖金不在基数里(实测:奖金从 5% 抬到 20%,配偶 gross 从 126,000 变 144,000,"
         "匹配一分不动)。这与配偶休假、配偶失业不同,那两条缩的是基础工资,匹配会跟着动。"
         "**两个人的奖金在现实里当然相关**(同一个经济、常在同一行业,奖金池一起缩),"
         "这里走独立子流、**不猜那个系数**,所以是「刻意独立」不是「零相关是真的」。"
         "上下界是你自己的薪酬方案,不是行业分布。配偶仍被建模为 W-2,"
         "**主申报人那条 1099 整笔利润因子不镜像到配偶**。",
         "The spouse's bonus becomes a share of THEIR OWN base pay, drawn "
         "independently and uniformly within your bounds each year. THIS IS "
         "THE PRE-PROMOTION CONTRACT; after promotion the spouse's own "
         "promotion-bonus contract takes over. It reaches household "
         "MAGI, their own affordability ceiling and the household's combined "
         "earned income. BUT IT DOES NOT REACH THEIR EMPLOYER MATCH: that "
         "match is computed on the spouse's BASE PAY, and a bonus is not in "
         "that base (measured: lifting the bonus from 5% to 20% moves their "
         "gross from 126,000 to 144,000 and the match not at all). This is "
         "unlike the spouse's break and layoff, which scale base pay, so the "
         "match moves with them. TWO PEOPLE'S BONUSES ARE OF "
         "COURSE CORRELATED in reality (one economy, often one industry, bonus "
         "pools shrinking together); this uses an independent child stream and "
         "GUESSES NO COEFFICIENT, so it is a deliberate independence rather "
         "than a claim the correlation is zero. The bounds come from your own "
         "compensation plan, not an industry distribution. The spouse is still "
         "modelled as W-2, and the primary's 1099 whole-profit factor is NOT "
         "mirrored onto them."),
    Rule("spouse_layoff", ("spouse_layoff.enabled",),
         lambda cfg: bool(_leaf(cfg, "spouse_layoff.enabled")),
         "配偶现在也有自己的失业风险。**坏年景是共用的**:同一年的市场回报低于你填的阈值时,"
         "两人的失业概率同时被各自的倍数抬高 —— **这一层是结构,不是猜出来的系数**,"
         "衰退本来就同时压两个人。**但给定那一年之后,两人是否真的被裁是各自独立抽的**;"
         "现实里同一个家庭的两次失业还会通过行业、雇主、地区进一步相关,"
         "**那一层没有建模,因为本项目不猜它**。配偶失业按空窗占全年的比例缩减配偶收入,"
         "并同时影响家庭 MAGI、配偶缴款与雇主匹配。"
         "**没有配偶侧的空窗医保保费字段**(积累期医保是家庭级、只定价一次),"
         "也不建遣散费、失业金或再就业时的降薪 —— 主申报人侧同样没有。",
         "The spouse now carries their own layoff risk. THE BAD YEAR IS "
         "SHARED: when a year's market return falls below your threshold, both "
         "people's probabilities are lifted by their own multipliers -- THAT "
         "LAYER IS STRUCTURE, NOT A GUESSED COEFFICIENT, because a recession "
         "presses on both of you anyway. BUT GIVEN THAT YEAR, whether each is "
         "actually let go is drawn independently. In reality two layoffs in "
         "one household are further correlated through industry, employer and "
         "region; THAT LAYER IS NOT MODELLED, because this project will not "
         "guess it. A spouse layoff reduces their pay by the share of the year "
         "the gap covers, and that reaches household MAGI, their own "
         "contributions and their employer match. There is NO spouse-side gap "
         "premium field (accumulation health is a household figure, priced "
         "once), and no severance, unemployment insurance or pay cut on "
         "re-employment -- the primary side has none of those either."),
    Rule("spouse_promotion", ("spouse_promotion.enabled",),
         lambda cfg: bool(_leaf(cfg, "spouse_promotion.enabled")),
         "配偶晋升已开启:晋升年与晋升后奖金走**独立 child stream**,不会重排主申报人的晋升或市场。"
         "晋升后的基础工资、增长率、奖金区间与平率税率全部来自你填写的配偶薪酬事实;"
         "本 App **没有行业默认**。工资先乘配偶自己的人力资本因子,再按晋升曲线计算,最后才应用"
         "配偶休假与失业,所以两者会同时缩减晋升后的工资、缴款与雇主匹配。"
         "晋升时钟在休假期间继续(U26),不顺延、不取消。**夫妻晋升在现实里相关**(同一经济周期、"
         "有时同一行业),这里不猜系数,所以两条晋升流刻意独立。配偶没有 OT 输入,因此没有一个"
         "永远不起作用的 `ot_eliminated` 控件;配偶仍按 W-2 建模。",
         "Spouse promotion is on. Its timing and post-promotion bonus use an "
         "INDEPENDENT CHILD STREAM, so they do not reshuffle the primary's "
         "promotion or markets. Post-promotion base pay, growth, bonus bounds "
         "and flat-tax rate are all spouse compensation facts you supplied; "
         "NO INDUSTRY DEFAULTS are shipped. The spouse's human-capital factor "
         "is applied first, then the promotion curve, then spouse break and "
         "layoff, so those events reduce post-promotion pay, contributions and "
         "match together. The promotion clock keeps running through a break "
         "(U26): it is neither delayed nor cancelled. A couple's promotions "
         "are correlated in reality (one economy, sometimes one industry); no "
         "coefficient is guessed, so the two promotion streams are deliberately "
         "independent. There is no spouse overtime input, hence no inert "
         "ot_eliminated control; the spouse remains modelled as W-2."),
    Rule("second_promotion", ("second_promotion.enabled",),
         lambda cfg: bool(_leaf(cfg, "second_promotion.enabled")),
         "主申报人的第二次晋升已开启。它仍按积累期绝对年份填写，且整个第二次时间窗口必须晚于"
         "第一次；模型不会在冲突后顺延或重抽。第二次的时间与奖金走独立 child stream，不重排"
         "第一次晋升或市场路径。两段晋升之间沿第一次晋升后的增长率推进，此后换成第二次的"
         "工资、增长、奖金和平率税率。所有数字都由用户填写，没有行业默认。休假不暂停晋升时钟；"
         "范围止于两次晋升，不是通用职业状态机。",
         "The primary earner's second promotion is on. It still uses an "
         "absolute accumulation year, and its entire timing window must follow "
         "the first; the model does not delay or redraw a conflicting event. "
         "Timing and bonus use an independent child stream, so the first "
         "promotion and markets are not reshuffled. The first post-promotion "
         "growth curve runs between events, after which the second salary, "
         "growth, bonus and flat-tax rate apply. Every figure is user-supplied; "
         "there is no industry default. A break does not pause the clock, and "
         "this stops at two promotions rather than becoming a career state machine."),
    Rule("spouse_second_promotion", ("spouse_second_promotion.enabled",),
         lambda cfg: bool(_leaf(cfg, "spouse_second_promotion.enabled")),
         "配偶的第二次晋升已开启。它必须晚于配偶第一次晋升的整个时间窗口，并走自己的独立"
         "child stream。工资依次经过晋升前增长、第一次晋升后增长和第二次晋升后增长，再与配偶"
         "人力资本、休假和失业组合。数字全部来自用户自己的配偶薪酬情景；没有行业默认，也没有"
         "配偶 OT 或 1099。夫妻与两次晋升在现实里可能相关，本模型不猜这些系数。",
         "The spouse's second promotion is on. Its whole timing window must "
         "follow the spouse's first promotion, and it uses its own independent "
         "child stream. Pay runs through pre-promotion growth, first-post "
         "growth and second-post growth before composing with spouse human "
         "capital, break and layoff. All figures come from your spouse "
         "compensation scenario; there are no industry defaults and no spouse "
         "overtime or 1099 path. Promotions may be correlated in reality, but "
         "this model guesses none of those coefficients."),
    Rule("spouse_human_capital", ("spouse_human_capital.enabled",),
         lambda cfg: bool(_leaf(cfg, "spouse_human_capital.enabled")),
         "配偶现在也有自己的工资冲击:永久性的会**累积**(升职没拿到、换了赛道,留下痕迹),"
         "一次性的只影响当年。两人用的是**同一套模型**(同一个抽样实现),"
         "但走**各自独立的随机流** —— 打开一边不会改变另一边的抽样。"
         "锚的是**因子自己的中位数**(σ 只加宽,不移动中心),不是计划的中位数。"
         "**夫妻收入冲击在现实里当然相关** —— 同一个经济周期、常在同一行业、有时同一家公司;"
         "本模型刻意不猜那个系数,所以这里是「刻意独立」,不是「零相关是真的」。"
         "σ 是你自己填的,不是对任何人收入波动的校准。配偶仍被建模为 W-2;"
         "晋升在自己的独立流上与这个因子组合。",
         "The spouse now has wage shocks of their own: permanent ones "
         "ACCUMULATE (a promotion missed, a track changed -- they leave a "
         "mark), transitory ones hit a single year. Both people use THE SAME "
         "MODEL (one sampling implementation) on SEPARATE random streams, so "
         "switching one on does not change the other's draws. What is anchored "
         "is the MEDIAN OF THE FACTOR ITSELF -- sigma widens the spread without "
         "moving the centre -- not the median of your plan. A COUPLE'S INCOME "
         "SHOCKS ARE OF COURSE CORRELATED in reality: one economy, often one "
         "industry, sometimes one employer. No coefficient is guessed, so this "
         "is a deliberate independence rather than a claim that the "
         "correlation is zero. The sigmas are your own figures, not a "
         "calibration of anyone's income volatility. The spouse is still "
         "modelled as W-2; promotion composes with this factor on its own "
         "separate stream."),
    Rule("spouse_career_break", ("spouse_career_break.enabled",),
         lambda cfg: bool(_leaf(cfg, "spouse_career_break.enabled")),
         "配偶的计划休假与主申报人的是**两个人的两个决定**:可以各自发生、同时发生或都不发生。"
         "开始年龄按**配偶自己的年龄**读(用 `household.spouse_age_offset` 换算),"
         "休假期间配偶的基础工资与奖金同比例缩减,复工后 `return_wage_factor` **永久**生效。"
         "缩减同时到达家庭 MAGI、配偶自己的可负担缴款、配偶雇主匹配与家庭合计劳动收入。"
         "**刻意没有配偶侧的医保保费字段**:积累期医保已经是家庭级的一个用户自填数字,"
         "再加一份就是同一笔家庭成本的两个控件。"
         "配偶仍按 W-2 建模;失业、人力资本、晋升与可变收入是可组合的独立合同。",
         "The spouse's planned break and the primary's are TWO DECISIONS BY TWO "
         "PEOPLE: either, both or neither can happen. The start age is read in "
         "the SPOUSE'S OWN AGE (converted using household.spouse_age_offset). "
         "During the break the spouse's base pay and bonus scale together, and "
         "on return the wage factor applies PERMANENTLY. The reduction reaches "
         "household MAGI, the spouse's own affordability solve, their employer "
         "match and the household's combined earned income. There is "
         "DELIBERATELY NO spouse-side medical premium: accumulation health is "
         "already one household figure you price once, and a second would be "
         "two controls for one cost. The spouse is still modelled as W-2; "
         "layoff, human-capital shocks, promotion and variable income are "
         "separate contracts that compose."),
    Rule("accumulation_tax_schedule", ("contributions.tax_model",),
         # Requires the key to be PRESENT, not defaulted. Every rule in this
         # module treats an absent section as "not applicable", and a rule
         # that fires on a config with no contributions block at all would be
         # a false positive of exactly the kind the module's own test hunts.
         lambda cfg: _leaf(cfg, "contributions.tax_model") == "schedule",
         "工作年份的税用的是随本 App 发布的 **2026 联邦税率表 + FICA**,税率跟着收入走。"
         "有三件事它**没有**算,写在这里而不是让你自己发现:"
         "(1) **州所得税只有一个平率**。工作阶段现在会按你选的州档案(或州税平率)"
         "收州税 —— 与退休阶段同一个数,这一点 2026-08-23 之前是错的,当时同一个人"
         "在两个阶段被按不同的税制对待。但它是**一个平率乘以工资减税前递延**:"
         "没有州的分级税率表、没有州标准扣除、没有任何州级抵免,"
         "而且只在**真实税引擎打开时**才生效。"
         "(2) **HSA 按只免所得税、不免工资税处理**。通过雇主 section 125 计划扣缴的 HSA "
         "实际上连 7.65% 的工资税一起免,本模型不给这个优惠 —— 这个选择让结果偏保守,"
         "不是偏乐观。(3) **不分申报身份**:每个人都按单身税率表和单身标准扣除额算,"
         "各算各的工资。2026 年联合申报的税率区间在 35% 档以下正好是单身的两倍,"
         "所以两个收入相当的人合起来正好等于一张联合报表;收入越悬殊,算出来的税越偏高。"
         "另外**不算逐项扣除、不算任何税收抵免**。",
         "The working years are taxed with the 2026 federal brackets plus FICA "
         "shipped with this app, so the rate follows the income. Three things "
         "it does NOT do, stated here rather than left to be discovered: "
         "(1) State income tax is ONE FLAT RATE. The working years now pay "
         "the state archetype (or flat state rate) you chose -- the same one "
         "the retirement years pay, which was not true before 2026-08-23, "
         "when one person was taxed under two different systems across their "
         "life. But it is a single rate on wages net of pre-tax deferrals: no "
         "state brackets, no state standard deduction, no state credits, and "
         "it applies only when the true-tax engine is on. "
         "(2) HSA money is treated as deductible from income tax but NOT "
         "exempt from payroll tax. Contributions payroll-deducted through a "
         "section 125 plan really are exempt, worth 7.65%; withholding that "
         "makes the result conservative, not optimistic. "
         "(3) No filing statuses: every earner is put on the single schedule "
         "with the single standard deduction, on their own wages. The 2026 "
         "joint brackets are exactly twice the single ones below the 35% band, "
         "so two similar incomes reproduce a joint return and the further "
         "apart they are the more tax is charged. No itemising and no credits "
         "either."),
    Rule("accumulation_tax_flat", ("contributions.tax_model",),
         lambda cfg: _leaf(cfg, "contributions.tax_model") == "flat",
         "你选择了**自己填一个平率**,所以工作年份的税就是收入乘这个数,"
         "不管收入是多少、也不管哪一年 —— 这正是 2026-08-23 之前的做法。"
         "如果这个数是你从自己报税表上算出来的实际税负比例,它比任何税表都准 ——"
         "**也正因如此,选平率时工作阶段不会再另外加州税**:你那个数里已经含了它,"
         "再加一次就是收两遍。"
         "如果它只是个估计,请记住**一个平率只可能在一个收入上对**:"
         "原来那个 24% 在年收入 $150,000 处与真实税负只差 $373/年,"
         "在 $45,000 处一年多算 $4,138,在 $500,000 处**少算 $36,344**"
         "(按引擎在各收入上真正记的递延额算)。",
         "You have chosen to state one flat rate, so the working years are "
         "taxed by multiplying income by it -- at every income and in every "
         "year, which is exactly what this app did before 2026-08-23. If that "
         "number is your real effective rate off an actual return, it beats "
         "any table -- and for exactly that reason the working years add NO "
         "state tax on top of it: your figure already contains your state's, "
         "and charging it again would be charging it twice. If it is an "
         "estimate, remember that a flat rate can only "
         "be right at ONE income: the 24% this replaced was within $373/yr of "
         "the real bill at $150,000 of gross, charged $4,138/yr too much at "
         "$45,000, and $36,344/yr too little at $500,000 -- measured at the "
         "deferral the engine books at each of those incomes."),
    Rule("backdoor_roth", ("contributions.backdoor_roth",),
         _on("contributions.backdoor_roth"),
         "你已声明使用「后门 Roth」,所以本模型**不再对你套用 Roth 收入 phase-out**,"
         "按全额额度计算。**有一件事本 App 没算**:如果你持有税前的传统 IRA 余额,"
         "pro-rata 规则会让这笔转换产生应税收入,而这里**不计算那笔税**。"
         "如果你有这样的余额,这个开关会让结果偏乐观 —— 乐观的幅度正好是那笔没被算的税。",
         "You have said you use a backdoor Roth, so the Roth income phase-out "
         "is NOT applied to you and the full contribution room is modelled. "
         "One thing this app does not compute: if you hold a pre-tax "
         "traditional IRA balance, the pro-rata rule makes that conversion "
         "taxable, and the tax is not calculated here. If you do hold such a "
         "balance this switch makes the result optimistic, by exactly the tax "
         "that was not charged."),
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
         "失业冲击已开启：只作用于积累期，先按实际空窗缩主申报人工资，再重算税、生活费、"
         "缴款与社保 covered earnings；配偶工资不缩。空窗期净新增家庭医保费由你按月填写，"
         "但不自动选择配偶计划、COBRA/Marketplace 或计算 ACA subsidy。savings-rate 模式仍以"
         "你填的储蓄率为准。**不建模再就业工资折损**，也不建模失业保险。",
         "Layoff shocks act only during accumulation. Primary pay is reduced "
         "for the actual gap before tax, living costs, saving and Social "
         "Security covered earnings are recomputed; spouse pay is unchanged. "
         "You supply the net-new monthly household health premium, but the app "
         "does not choose spouse, COBRA or Marketplace coverage or compute an "
         "ACA subsidy. Savings-rate mode keeps your stated rate authoritative. "
         "Re-employment wage scarring and unemployment insurance are not modelled."),
    Rule("disability", ("disability.enabled",), _on("disability.enabled"),
         "SSDI 压力已开启：发生率来自 **2026 SSA Trustees Report 的 disabled-worker "
         "award 表**，分母是已具 disability insurance 且尚未领残障金的人，不是全人口"
         "伤残率。首刀在命中后保守地把主收入置零到计划退休，不建模康复、复工、死亡终止、"
         "五个月等待期或 24 个月 Medicare 等待期。SSDI/LTD 必须填写扣税且扣完保单 offset 后"
         "真正可花的金额；新增医保保费也由用户填写。",
         "The SSDI stress is on. Incidence comes from the 2026 SSA Trustees "
         "Report disabled-worker AWARD table, whose denominator is workers "
         "insured for disability and not already receiving benefits; it is not "
         "general-population disability incidence. This first slice "
         "conservatively sets primary earnings to zero through planned "
         "retirement after an award. Recovery, return to work, death "
         "termination, the five-month cash waiting period and the 24-month "
         "Medicare wait are not modelled. SSDI/LTD must be entered as "
         "spendable cash after tax and policy offsets, and the user supplies "
         "the extra health premium."),
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
         ("本模型对「各部分如何一起动」的假设已逐条登记"
          "（`server/correlation_registry.py`）：%d 个随机模块中，%d 条应用数值相关，"
          "%d 条是同一过程内部的结构关系，%d 条为已披露的刻意独立，%d 条查过"
          "原始/官方来源但没有得到可移植的联合分布。最后一类**不是零相关**：晋升"
          "时点、奖金、宏观死亡率与伤残发生率证据随行业、群体、国家与周期而变，本 App 不猜"
          "系数。股债相关性（默认 0.15）已有页面控件但没有外部来源；失业与市场"
          "那条（坏年份 ×3）在本仓库里仍没有出处。登记不是辩护，是让证据边界可见。"
          % (_CORRELATION_MODULES,
             _CORRELATION_SHAPE[CORRELATION.MODELLED_NUMERIC],
             _CORRELATION_SHAPE[CORRELATION.STRUCTURALLY_LINKED],
             _CORRELATION_SHAPE[CORRELATION.INDEPENDENT_BY_DESIGN],
             _CORRELATION_SHAPE[CORRELATION.EXAMINED_UNRESOLVED])),
         ("This model's joint-movement assumptions are listed relationship by "
          "relationship (server/correlation_registry.py). Across %d sampling "
          "modules, %d apply numeric relationships, %d are structural stages "
          "of one process, %d are disclosed deliberate independences, and %d "
          "were checked against primary or official sources without yielding "
          "a portable joint distribution. The last category is NOT zero "
          "correlation: evidence for promotion timing, bonuses, macro "
          "mortality and disability incidence varies by industry, population, "
          "country and cycle, so no "
          "coefficient is guessed. Equity/bond correlation (0.15 by default) "
          "now has a UI control but no external source; the layoff/market rule "
          "(3x in a bad year) still has no provenance in this repository. The "
          "ledger makes those boundaries visible; it does not defend them."
          % (_CORRELATION_MODULES,
             _CORRELATION_SHAPE[CORRELATION.MODELLED_NUMERIC],
             _CORRELATION_SHAPE[CORRELATION.STRUCTURALLY_LINKED],
             _CORRELATION_SHAPE[CORRELATION.INDEPENDENT_BY_DESIGN],
             _CORRELATION_SHAPE[CORRELATION.EXAMINED_UNRESOLVED]))),
    Rule("relocation", ("relocation.enabled",), _on("relocation.enabled"),
         "跨国搬迁已开启：生活成本比与汇率波动是**风格化的**，"
         "签证、医保资格与税务居民身份的实际规则不在建模范围内。",
         "Relocation is on: the cost-of-living ratio and FX volatility are "
         "stylized. Visas, healthcare eligibility and tax-residency rules are "
         "outside what this models."),
    Rule("pension", ("income_streams.pension_enabled",
                     "income_streams.pension_amount_mode"),
         _on("income_streams.pension_enabled"),
         "养老金按你填写的**税后可花今日美元**进入现金流。传统 DB 模式只执行计划公式："
         "计入工龄 × 每年 accrual rate × final-average salary；不会从模拟工资、职业休假、"
         "失业或伤残路径推导工龄或最终平均工资，也不自动计算提前退休折减、遗属选择、"
         "vesting、计划上限或一次性领取。若计划管理员报价已含这些条款，请用直接年额模式。",
         "Pension cash enters as TODAY'S-DOLLAR spendable after-tax income. "
         "Traditional DB mode applies only the stated plan formula: credited "
         "service x annual accrual rate x final-average salary. It does not "
         "infer service or final-average pay from simulated wages, breaks, "
         "layoffs or disability, and does not calculate early-retirement "
         "reductions, survivor elections, vesting, plan caps or lump sums. "
         "Use the direct annual amount when an administrator quote already "
         "incorporates those terms."),
    Rule("housing", ("housing.enabled", "housing.mode"), _on("housing.enabled"),
         "住房模块已开启：工作年份的替换后净住房成本会先降低可负担缴款，退休年份继续走"
         "现金事件；按揭的 realized-CPI 偏差仍由事件层补差。房价按一个独立的实际增长率演化，"
         "**不建模地区差异、房产税重估或强制性大修**。",
         "Housing is on: net replacement housing cost reduces affordable "
         "contributions during working years; retirement years remain cash "
         "events, and the event layer reconciles realized-CPI mortgage drift. "
         "Prices follow a single real growth rate. Regional "
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
    Rule("social_security_income_path", ("social_security.enabled",),
         lambda cfg: isinstance(
             _leaf(cfg, "social_security.ssa_basis_v1"), dict),
         "已导入社保收入基础：模拟会把主申报人的逐年覆盖收入接进 35 年 AIME。"
         "这里的**精确**指按当前规则包做 top-35 替换，不是预测未来法律："
         "未来社保工资基数沿用缴款模型已有的 3% 年增长代理，AWI 只到规则包标明的年份；"
         "配偶工资不会进入本人的记录。",
         "An imported Social Security earnings basis is active: the simulation "
         "feeds the primary worker's annual covered earnings into the 35-year "
         "AIME. **Exact** here means exact top-35 replacement under the current "
         "rule pack, not a forecast of future law: the future taxable wage base "
         "uses the contribution model's existing 3% annual-growth proxy, and "
         "AWI stops at the vintage named by the pack. Spouse earnings never "
         "enter the primary record."),
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
