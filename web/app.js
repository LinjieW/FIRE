/* app.js — staged FIRE flow app: welcome → wizard → precision → computing → paginated results.
   Bilingual (中/EN), destination library, real progress bar, dynamic conclusions.
   Charts come from charts.js; the engine is the v9.8 Python backend. */
(function () {
  "use strict";
  const destinationCatalog = window.FIREDestinationCatalog;
  if (!destinationCatalog || typeof destinationCatalog.DEST_VINTAGE !== "string"
      || !Array.isArray(destinationCatalog.REGIONS)
      || !Array.isArray(destinationCatalog.DEST)) {
    throw new Error("destination catalog unavailable");
  }
  const { DEST_VINTAGE, REGIONS, DEST } = destinationCatalog;
  delete window.FIREDestinationCatalog;
  const C = window.Charts;
  const $ = id => document.getElementById(id);
  const money = C.money, pct = C.pct;

  // =========================================================== i18n
  let L = localStorage.getItem("fire_lang") || "zh";
  const tt = (zh, en) => (L === "zh" ? zh : en);
  const T = {
    "nav.restart": ["重新开始", "Restart"], "nav.prev": ["上一步", "Back"],
    "nav.save": ["暂存草稿", "Save draft"], "nav.next": ["下一步", "Next"],
    "welcome.eyebrow": ["蒙特卡洛生命周期引擎", "Monte Carlo Lifecycle Engine"],
    "recap.title": ["回顾你输入的数字", "Review what you entered"],
    "welcome.title": ["FIRE 分析", "FIRE Analysis"],
    "welcome.subtitle": ["A staged, quantitative roadmap to financial independence.",
      "A staged, quantitative roadmap to financial independence."],
    "welcome.lead": ["分步录入你的情况，选择运算精度，看进度条跑真实的蒙特卡洛，然后分页阅读结果与结论。默认预填一份去身份的示例，所有数字都可改成你自己的。",
      "Enter your situation step by step, pick a precision, watch a real Monte Carlo run, then read results and conclusions page by page. A de-identified example is pre-filled — every number is editable."],
    "welcome.start": ["开始分析", "Start"],
    "welcome.example": ["载入示例并直接看结果", "Load example → results"],
    "welcome.resume": ["继续上次草稿", "Resume draft"],
    "welcome.note": ["预填数字为去身份示例，非任何真实个人。本工具为个人财务建模，不构成投资/税务/法律建议。",
      "Pre-filled numbers are a de-identified example, not a real person. This is a personal-finance model, not investment/tax/legal advice."],
    "side.derived": ["派生指标", "Derived"], "side.holdings": ["持仓构成", "Holdings"],
    "prec.title": ["选择运算精度", "Choose precision"],
    "prec.kicker": ["路径越多，分位数越稳、耗时越长。可先用 Quick 试跑，定稿再用 Deep/Official。",
      "More paths → steadier percentiles, longer runtime. Try Quick first; use Deep/Official when settled."],
    "prec.run": ["开始计算", "Run"],
    "compute.eyebrow": ["正在运行蒙特卡洛", "RUNNING MONTE CARLO"],
    "compute.init": ["准备中…", "Preparing…"],
    "results.edit": ["改参数重跑", "Edit & re-run"],
    "ov.title": ["核心结果", "Headline results"],
    "ov.kicker": ["三分支成功率＝到达 FI 且终身偿付，或退休前身故；它不是单纯的偿付率。数字来自你选择精度的完整蒙特卡洛。",
      "Lifetime success uses three-branch semantics (reached FI & solvent, or died before retiring). Numbers come from the full run at your chosen precision."],
    "ov.success": ["三分支成功率", "Three-branch success"], "ov.fire": ["FIRE 年龄 · P50", "FIRE age · P50"],
    "ov.cons": ["退休期年消费 · P50 (real)", "Retirement spend · P50 (real)"],
    "ov.term": ["P50 终值 · real", "P50 terminal · real"], "ov.term.note": ["存活路径的实际购买力遗产", "Purchasing-power legacy, solvent paths"],
    "ov.ss": ["P50 社保终生 · real", "P50 lifetime SS · real"], "ov.ss.note": ["今日美元累计", "Cumulative, today's $"],
    "ov.reached": ["到达 FI 率", "Reached-FI rate"], "ov.reached.note": ["FIRE 前身故不计为失败", "Death before FIRE isn't failure"],
    "ov.solv": ["FIRE 后偿付率", "Post-FIRE solvency"], "ov.solv.note": ["到达 FI 后不耗尽的条件概率", "P(not depleted | reached FI)"],
    "traj.title": ["生命周期财富轨迹", "Lifecycle wealth trajectory"],
    "traj.kicker": ["逐年财富分布扇形（含退休提取段）。百分位在每个年龄条件于仍活跃的路径。",
      "Per-age wealth fan (incl. the drawdown phase). Percentiles at each age condition on still-active paths."],
    "traj.cursor": ["年龄游标", "Age cursor"],
    "unit.label": ["量纲", "Unit"], "unit.real": ["real", "real"], "unit.real2": ["real（购买力）", "real (purchasing power)"], "unit.nom": ["名义", "nominal"],
    "fwd.title": ["前向累积模拟器", "Forward accumulation simulator"], "fwd.tag": ["浏览器内实时", "runs in-browser"],
    "fwd.note": ["一个真正在浏览器里跑的简化蒙特卡洛：拖旋钮重跑 2,000 条 real 累积路径。单资产演示，Python 引擎才是 source of truth。",
      "A real in-browser Monte Carlo: drag a knob to re-run 2,000 real accumulation paths. A single-asset demo — the Python engine is the source of truth."],
    "fwd.mu": ["真实 μ 中枢", "Real μ center"], "fwd.sd": ["σ 缩放", "σ scale"], "fwd.sav": ["年度储蓄", "Annual savings"], "fwd.swr": ["SWR", "SWR"],
    "fwd.run": ["▶ 重新模拟 2,000 路径", "▶ Re-simulate 2,000 paths"], "fwd.reset": ["复位到 config", "Reset to config"],
    "fwd.hist": ["FIRE 年龄分布", "FIRE-age distribution"], "fwd.fan": ["累积财富扇形（real, log）", "Accumulation fan (real, log)"],
    "dist.title": ["分布", "Distributions"],
    "dist.kicker": ["终值、退休消费与里程碑达成年龄的分布。终值强烈右偏，P50 远低于均值。",
      "Terminal wealth, retirement consumption, and milestone-age distributions. Terminal is strongly right-skewed — P50 ≪ mean."],
    "dist.term": ["终值分布", "Terminal distribution"], "dist.cons": ["退休消费轨迹", "Retirement consumption"], "dist.mile": ["里程碑达成分布", "Milestone-age distribution"],
    "stress.title": ["敏感性与压力测试", "Sensitivity & stress"],
    "stress.kicker": ["精度 ≠ 准确：你不知道的收益率 μ，影响远大于你精确知道的任何东西。下面按需计算。",
      "Precision ≠ accuracy: the return μ you don't know moves results more than anything you know precisely. Compute on demand below."],
    "stress.torn": ["敏感性 tornado", "Sensitivity tornado"], "stress.torn.tag": ["terminal real P50 摆动", "swing of terminal real P50"],
    "stress.torn.run": ["▶ 计算敏感性（约 15–40s）", "▶ Compute sensitivity (~15–40s)"],
    "stress.swr": ["SWR 的反直觉", "The SWR counterintuition"], "stress.swr.run": ["▶ 扫描 SWR", "▶ Sweep SWR"],
    "stress.claim": ["社保领取年龄", "Social Security claim age"], "stress.claim.run": ["▶ 扫描领取年龄", "▶ Sweep claim age"],
    "roth.title": ["Roth 转换额度对比", "Roth conversion grid"], "roth.tag": ["自动启用真实税表", "forces the true tax engine"],
    "roth.note": ["固定比较 8 个年转换基准档（$0–$100k）；每档按全局增长率逐年增长。只在已测试档位中先比较三分支成功率，再在成功率相同的档位中比较全路径税后终值 P50（失败路径记 0）。终值不再是平率清算代理：它按逐年追踪的成本基础计算，并按遗产的基础提升口径处理未实现利得。剩下的方向性只有一条：只测了这 8 个点，档位之间与边界之外没有测过。想要逐年不同的计划，用「哪几年转，转多少」那个面板，它搜的是年龄窗口而不是单一额度，并且不给推荐。另有两个数字是启发式而非校准过的政策：转换额被「可用应税账户的 4 倍」封顶（为生活开支留缓冲），以及转换税按一个平率计。两者都是取的合理值，没有对照真实转换成本校准过，将来可能会改。", "Compare exactly 8 tested base annual levels ($0–$100k), each growing by the global rate. Among tested levels, maximize three-branch success first; only equal-success levels use unconditional after-tax terminal P50 (failed paths = 0). Terminal value is no longer a flat liquidation proxy: it is computed from the cost basis tracked year by year, treating unrealised gains under the bequest step-up. Coverage is the only directional limit left: only these 8 points were tested, and anything between or beyond them was not. For a schedule that differs year to year, use the \u0022which years to convert\u0022 panel, which searches age windows rather than one amount and makes no recommendation. Two further numbers are heuristics rather than calibrated policy: the conversion is capped at 4x available taxable (to leave a buffer for living expenses), and the tax on it is charged at a flat rate. Both were chosen to be reasonable, neither has been checked against what a real conversion costs, and both may change."],
    "roth.run": ["▶ 对比 8 个转换档位（约 60–90 秒）", "▶ Compare 8 conversion levels (about 60–90 s)"],
    "strat.title": ["提取策略对比", "Withdrawal strategy compare"], "strat.tag": ["同一组路径 × 5 种花钱法", "same paths × 5 spending rules"],
    "strat.note": ["同一配置、同一随机序列，只换「怎么花钱」：GK 护栏（当前默认）、固定实际额、VPW 变比例、地板+上行、ABW 摊销。看清消费稳定性、破产风险与遗产之间的三角取舍。", "Same config, same random sequence — only the spending rule changes: GK guardrails (current default), fixed real, VPW, floor+upside, ABW amortization. See the consumption-stability / ruin-risk / bequest triangle clearly."],
    "strat.run": ["▶ 对比五种策略（约 60–90 秒）", "▶ Compare five strategies (~60–90s)"],
    "live.title": ["实时试验", "Live experiment"], "live.tag": ["拖杠杆 · 约 2 秒重算", "drag levers · ~2s recompute"],
    "live.open": ["▶ 展开实时试验", "▶ Open live experiment"], "live.close": ["▼ 收起实时试验", "▼ Close live experiment"],
    "live.note": ["同一随机序列下的快速对比（1,500 路径）：拖动杠杆，指标卡显示相对当前基线的变化。图表不实时刷新——满意后「应用到计划」做完整重跑。", "Fast comparison under the SAME random sequence (1,500 paths): drag a lever, the cards show deltas vs the current baseline. Charts do not live-update — when satisfied, 'apply to plan' for a full re-run."],
    "live.apply": ["✓ 应用到计划并完整重跑", "✓ Apply to plan & full re-run"], "live.reset": ["重置杠杆", "Reset levers"],
    "story.title": ["一条具体的人生", "One concrete life"], "story.tag": ["分布说服头脑，故事说服人心", "distributions persuade the head; stories persuade the heart"],
    "story.note": ["从同一批模拟里抽三条真实路径：中位、第 90 分位、倒霉的那条（若有破产路径优先讲它）。逐年财富曲线 + 大事记——「换一条命」重抽一批。", "Three real paths from ONE batch: the median, the 90th percentile, and the unlucky one (a ruined path if any exist). Year-by-year wealth curve + life events — 'reroll a life' draws a fresh batch."],
    "story.run": ["▶ 抽三条人生（约 3 秒）", "▶ Draw three lives (~3s)"], "story.reroll": ["⟲ 换一条命", "⟲ Reroll a life"],
    "story.typical": ["典型", "Typical"], "story.lucky": ["幸运", "Lucky"], "story.unlucky": ["倒霉", "Unlucky"],
    "gs.title": ["目标求解器", "Goal seeker"], "gs.tag": ["给目标，找可行组合", "set a goal, find what works"],
    "gs.note": ["反过来问：「要达到 95% 成功率，开销和 SWR 的哪些组合可行？」选一个目标、两根杠杆，求解器扫描可行域并给出离你现在最近的一组改法。", "Ask it backwards: 'which spending × SWR combinations reach 95% success?' Pick one goal and two levers — the seeker maps the feasible region and names the change closest to where you are."],
    "gs.goal": ["目标", "Goal"], "gs.lever1": ["杠杆 X", "Lever X"], "gs.lever2": ["杠杆 Y", "Lever Y"],
    "gs.run": ["▶ 搜索可行域（约 2 分钟）", "▶ Map the feasible region (~2 min)"], "gs.cancel": ["✕ 取消", "✕ Cancel"],
    "ef.title": ["效率前沿", "Efficient frontier"], "ef.tag": ["消费 × FIRE 年龄 × 成功率", "spend × FIRE age × success"],
    "ef.note": ["扫描「开销 × SWR」网格，把每个组合的结果画成一个点：横轴年消费、纵轴 FIRE 年龄、颜色是成功率。没有别的组合能同时「花得多、退得早、更稳」的点构成前沿——看你离它有多远。", "Sweeps the spending × SWR grid and plots each combination: consumption on X, FIRE age on Y, success as color. Points no other combination beats on all three at once form the frontier — see how far you sit from it."],
    "ef.run": ["▶ 扫描前沿（约 1 分钟）", "▶ Sweep the frontier (~1 min)"], "ef.cancel": ["✕ 取消", "✕ Cancel"],
    "hz.title": ["住房 · 租 vs 买", "Housing · rent vs buy"], "hz.tag": ["按揭真算 + 蒙特卡洛对比", "real amortization + MC comparison"],
    "hz.note": ["用「高级 → 住房」里的参数：左图是确定性净值对比（买方房净值 vs 租方投资差额），右卡是两种安排各跑一遍蒙特卡洛的结果。买方的房净值不计入模拟组合——它是非流动资产，单独列示。", "Uses Advanced → Housing parameters: the chart is the deterministic net-worth comparison (buyer's home equity vs renter's invested difference); the table runs each arrangement through Monte Carlo. Home equity never enters the simulated portfolio — it is illiquid and shown separately."],
    "hz.run": ["▶ 对比租与买（约 10 秒）", "▶ Compare rent vs buy (~10s)"],
    "drill.age": ["⤵ 下钻该年龄的完整截面（约 3 秒）", "⤵ Drill into this age's full cross-section (~3s)"],
    "drill.term.hint": ["点直方图的任意一段，看那部分路径有什么共同点。", "Click any part of the histogram to see what those paths have in common."],
    "ci.title": ["预测 vs 实际", "Forecast vs actual"], "ci.tag": ["每年记一笔，看计划兑现没有", "one check-in a year — is the plan tracking?"],
    "ci.age": ["年龄", "Age"], "ci.amt": ["实际总资产（名义 $）", "Actual total (nominal $)"], "ci.add": ["＋ 记录", "＋ Record"],
    "stress.bt": ["序列风险回测", "Sequence-of-returns backtest"], "stress.bt.tag": ["风格化坏开局", "stylized bad openings"], "stress.bt.run": ["▶ 运行回测", "▶ Run backtest"],
    "reloc.title": ["搬迁对比", "Relocation comparison"],
    "reloc.kicker": ["留在本土 vs 搬到你选的目的地：两条中位轨迹叠在一张图（real，log）。累积段重合，分歧发生在搬迁之后。",
      "Stay vs relocate to your chosen destination: two median trajectories on one chart (real, log). Accumulation overlaps; divergence begins after relocation."],
    "reloc.table": ["全周期对比", "Whole-horizon comparison"],
    "concl.title": ["结论", "Conclusions"],
    "rsch.win.none": ["不转换", "No conversion"],
    "rsch.win.early": ["退休后头几年", "First years of retirement"],
    "rsch.win.middle": ["退休中段", "Middle of retirement"],
    "rsch.win.to_rmd": ["一直转到 RMD 前", "Right up to RMDs"],
    "fr.title": ["第二把尺子：你已经有的，够不够你欠的", "A second ruler: does what you have cover what you owe"],
    "fr.kicker": ["这一页里没有抽样。把地板支出与可自由支配支出各自折现成现值，把组合、已建模收入流与社保也折现成现值，然后相除。它问的是「如果你今天就停止储蓄，手上的够不够」——未来缴款不算在资产里，那是蒙特卡洛回答的另一个问题。折现率与地板都由你填，不填就不算。", "No sampling on this page. Discount your floor and discretionary spending into present values, discount the portfolio, modelled income streams and Social Security the same way, and divide. It asks whether what you ALREADY have covers what you owe, so future contributions are not counted as an asset -- that is the question the Monte Carlo answers. The discount rate and the floor are yours to supply; without them it does not compute."],
    "fr.run": ["▶ 算 funded ratio", "▶ Compute funded ratio"],
    "fr.running": ["正在折现…", "Discounting…"],
    "rsch.title": ["哪几年转，转多少", "Which years to convert, and how much"],
    "rsch.kicker": ["搜的是逐年的转换计划（从哪一岁到哪一岁、每年转多少），不是单一年度额度——真实的 Roth 梯本来就不是平的，同样的钱早转和晚转差很多。这里不替你选：每个方案的成功率与税后终值都列出来，标星的是没有被任何其他方案全面压过的那些。选哪个是你的偏好，不是这个工具的。", "This searches per-year conversion schedules -- which ages, how much each year -- not a single annual amount. Real Roth ladders are not flat, and the same money converted early or late gives very different answers. It does not choose for you: every candidate's survival rate and after-tax terminal wealth are listed, and the starred ones are those no other candidate beats on both. Which to pick is your preference, not this tool's."],
    "rsch.run": ["▶ 搜索转换计划", "▶ Search conversion schedules"],
    "rsch.running": ["正在给每个计划定价…", "Pricing each schedule…"],
    "aloc.title": ["同样的钱，换个账户装", "The same money, held in a different account"],
    "aloc.kicker": ["把同一份组合按三种典型摆法各跑一遍，用同一个种子、同一批路径——所以差额只可能来自分派怎么被课税，不可能是市场噪音。只算税这一半：本引擎每年抽一个混合收益率发给所有账户，因此它算不出「把高收益资产放进 Roth」本身值多少，结论不要当成资产摆放的全部。", "Three typical placements of one portfolio, each run on the same seed and the same paths — so any gap comes from how distributions are taxed and cannot be market noise. This is the tax half only: the engine draws one blended return a year for every account, so it cannot price what putting the higher-returning asset in the Roth is worth on its own."],
    "aloc.run": ["▶ 比较三种摆法", "▶ Compare three placements"],
    "aloc.running": ["正在跑三种摆法…", "Running three placements…"],
    "beq.title": ["这个计划靠继承吗", "Does this plan lean on an inheritance?"],
    "beq.kicker": ["把计划原样跑一遍，再用同一个种子跑一遍「遗产没到账」，只差这一件事。看的是中位消费而不是成功率——护栏会靠砍消费吸收损失，所以成功率可能一动不动而生活水平掉一截。",
      "Runs the plan as configured, then again at the same seed with the bequest never credited — one difference, nothing else. It reads median consumption rather than the success rate, because the guardrail absorbs a lost inheritance by spending less: the success rate can sit still while your standard of living drops."],
    "beq.run": ["▶ 检查是否依赖继承", "▶ Check for inheritance dependence"],
    "beq.running": ["正在把计划跑两遍…", "Running the plan twice…"],
    "concl.kicker": ["以下每条都由你这次跑出的数字生成，随输入改变；均为教育性质的情景解读，非个人化投资建议。计算越多板块（敏感性/SWR/搬迁），解读越完整。",
      "Each statement below is generated from your numbers and changes with inputs — educational scenario readings, not personalized investment advice. Compute more panels for a fuller set."],
    "concl.method": ["方法与诚实度", "Method & honesty"],
    "concl.inv": ["现金流对账", "Cash-accounting check"], "concl.inv.sub": ["结构化收入实际到账年按现金精确对账；无到账的成功年份保留每年不超过 $1 的历史提取容差（每次运行抽样 ≤400 条路径）", "Actual structured-income receipt years reconcile to delivered cash; successful no-receipt years retain at most $1/year of historical withdrawal tolerance (≤400 sampled paths/run)"],
    "concl.proto": ["运行协议", "Run protocol"], "concl.proto.sub": ["仅反映抽样误差，不含输入假设本身的不确定性", "Sampling error only — not uncertainty in the assumptions themselves"],
    "concl.sem": ["成功语义", "Success semantics"], "concl.sem.val": ["三分支", "Three-branch"], "concl.sem.sub": ["到达 FI 且偿付，或退休前身故", "Reached FI & solvent, or died before retiring"],
    "concl.honesty.pill": ["诚实度", "HONESTY"], "concl.honesty.h": ["精度 ≠ 准确度", "Precision ≠ accuracy"],
    "concl.honesty.p": ["蒙特卡洛的置信区间只反映抽样，不反映输入假设本身的不确定性（收益率、通胀、汇率都是估计值）。序列风险回测用的是风格化序列，非逐年真实指数数据。",
      "Monte Carlo confidence intervals reflect sampling only, not uncertainty in the assumptions (returns, inflation, FX are estimates). The backtest uses stylized sequences, not literal index data."],
    "concl.report": ["打开完整 HTML 报告", "Open full HTML report"], "concl.json": ["下载结果 JSON", "Download results JSON"],
    "compute.cancel": ["取消", "Cancel"], "nav.quit": ["退出", "Quit"],
    "concl.print": ["打印 / 存 PDF", "Print / save PDF"],
    "ab.title": ["方案 A/B 对比", "Scenario A/B compare"],
    "ab.kicker": ["两套完整配置的并排对比：中位财富轨迹叠加，关键指标逐项对照。保存方案后改参数重跑即可对比任何假设的效果。", "Two full configurations side by side: median trajectories overlaid, key metrics compared. Save a slot, change anything, re-run, compare."],
    "ab.loadA": ["载入 A 的配置", "Load config A"], "ab.loadB": ["载入 B 的配置", "Load config B"],
    "solver.hint": ["拖动图中的 FIRE 竖线（或输入目标年龄）反解：要提前退休需要什么", "Drag the FIRE line on the chart (or type a target age) to solve: what would it take"],
    "solver.go": ["求解", "Solve"],
    "quick.title": ["60 秒速估", "60-second estimate"],
    "quick.sub": ["5 个数字，先看个大概——再进完整向导精修", "5 numbers for a first read — refine in the full wizard after"],
    "quick.age": ["当前年龄", "Age"], "quick.income": ["税前年收入 $", "Gross income $"],
    "quick.spend": ["当前年开销 $", "Spending now $"], "quick.port": ["现有可投资产 $", "Investable assets $"],
    "quick.ret": ["退休后年支出 $", "Retirement spend $"], "quick.spouse": ["配偶税前收入 $（可选）", "Spouse income $ (optional)"], "quick.go": ["速估 →", "Estimate →"],
    "quick.import": ["导入朋友的配置…", "Import a friend's config…"],
    "prec.seed": ["随机种子", "Random seed"],
    "prec.seed.hint": ["同种子同精度=完全可复现；换个种子重跑是最朴素的稳健性检查", "Same seed & precision = exact reproducibility; re-running with a new seed is the simplest robustness check"],
    "persona.head": ["或从一个像你的人开始：", "Or start from someone like you:"],
    "plans.head": ["我的计划：", "My plans:"],
    "plans.save": ["存为计划", "Save plan"], "plans.open": ["打开", "Open"],
    "results.saveA": ["存为 A", "Save A"], "results.saveB": ["存为 B", "Save B"],
    "plans.dup": ["复制", "Duplicate"], "plans.del": ["删除", "Delete"],
    "plans.migrate": ["迁移到本地数据库", "Move into the local database"],
    "recovered.head": ["迁移过来的草稿：", "Drafts carried over:"],
    "recovered.open": ["打开", "Open"],
    "recovered.save": ["保存为计划", "Save as plan"],
    "diag.logs": ["查看运行日志", "View logs"], "diag.copy": ["复制诊断信息", "Copy diagnostics"],
    "diag.logs.title": ["运行日志（最近 300 行）", "Run log (last 300 lines)"],
    "diag.robust": ["稳健性检查（3 种子）", "Robustness check (3 seeds)"],
    "help.title": ["使用指南", "User guide"], "help.back": ["← 返回", "← Back"],
    "dec.title": ["决策", "Decide"],
    "dec.kicker": ["问一个具体的决定，用同一套假设在三条轴上各跑一遍——不同种子、不同收益模型、不同不利假设。只有方向在所有真正看到这个改动的点上都不变，才叫稳健。",
      "Ask one concrete decision and run it across all three axes: different seeds, different return models, different adverse assumptions. It is only robust if the direction holds at every point that actually saw the change."],
    "dec.setup.title": ["你要决定什么", "What are you deciding?"],
    "dec.plan": ["先算要花多少 →", "Cost it first →"],
    "dec.run": ["开始", "Run it"],
    "dec.cancel": ["取消", "Cancel"],
    "dec.cost.title": ["这次要跑多少", "What this run costs"],
    "concl.briefing": ["导出简报包（给外部 AI）", "Export briefing pack (for an external AI)"],
    "rev.spend.import": ["从记账软件导入年度支出…", "Import a year of spending…"],
    "dec.review.title": ["到期复核：当年的决定，和它底下变过的东西", "Review: what you decided, and what has moved under it"],
    "dec.review.run": ["看已归档的决定", "Show archived decisions"],
    "ann.title": ["年金 / TIPS：多花多少的许可", "Annuity / TIPS: the licence to spend"],
    "ann.plan": ["先算要花多少 →", "Cost it first →"],
    "ann.run": ["开始", "Run it"],
    "ann.cancel": ["取消", "Cancel"],
    "dec.verdict.title": ["结论", "The verdict"],
    "dec.axes.title": ["三条轴上各自看到了什么", "What each axis saw"],
    "dec.sens.title": ["哪些假设最要命，什么条件下会反转", "Which assumptions matter, and what would flip it"],
    "dec.context.title": ["附带的实验室结果（不作为结论依据）", "Lab runs kept alongside (not evidence for the verdict)"],
    "dec.choice.title": ["你的决定", "Your decision"],
    "rev.title": ["年度复核", "Annual review"],
    "rev.kicker": ["把过去一年真实发生的事填进去，看看实际和当初的预测差在哪里——差多少来自市场，多少来自你自己的行为。填完两分钟，读完三分钟。",
      "Enter what actually happened over the past year and see where it diverged from the forecast — how much was the market, and how much was you. Two minutes to fill in, three to read."],
    "rev.form.title": ["这一年发生了什么", "What happened this year"],
    "rev.submit": ["解释这个差距 →", "Explain the gap →"],
    "rev.result.title": ["差距从哪来", "Where the gap came from"],
    "rev.history.title": ["往年复核", "Earlier reviews"],
    "rev.drift.title": ["计划本身漂移了多少", "How far the plan itself has moved"],
    "rev.import": ["从券商流水导入…", "Import from a broker export…"],
    "rev.memo.title": ["这次复核的结论", "What this review concludes"],
    "stand.title": ["你现在在哪", "Where you stand"],
    "rev.rebase.title": ["要不要以今天为新起点", "Re-baseline from today?"],
    "lim.title": ["模型未捕捉 / 近似处理的（局限声明）", "What the model does not capture (limitations)"],
    "lim.intro": ["以下是本模型明确不建模或做了简化近似的项。它们对一个可信的核心结论通常不是必需，但会影响精确数字——请据此解读，不要把点估计当成承诺。",
      "Below is what this model explicitly does NOT model, or approximates. These usually aren't required for a sound headline conclusion, but they move the exact numbers — read accordingly and don't treat a point estimate as a promise."],
  };
  const t = k => (T[k] ? T[k][L === "zh" ? 0 : 1] : k);

  function applyI18n() {
    if (window.Charts && Charts.setLang) Charts.setLang(L);
    document.documentElement.lang = L === "zh" ? "zh-CN" : "en";
    document.querySelectorAll("[data-i18n]").forEach(e => { e.textContent = t(e.dataset.i18n); });
    document.querySelectorAll("#langToggle button").forEach(b =>
      b.setAttribute("aria-pressed", b.dataset.lang === L ? "true" : "false"));
  }

  // =========================================================== destination library
  // Region-grouped major world cities. Values are ILLUSTRATIVE defaults (all editable):
  //   col = cost-of-living vs home(=1.0) · fx = local-currency vol vs USD · infl = local inflation
  //   hcW/hcS = destination annual healthcare (working / senior, today's $)
  //   hair = US-SS haircut abroad · tax = destination effective tax on pretax withdrawals
  // City-library data vintage: bump when the illustrative defaults are
  // reviewed. tests/test_regression.py alarms when this exceeds 18 months.
  function applyDest(id) {
    const d = DEST.find(x => x.id === id);
    set(state.config, "relocation.destination", id);
    if (!d || d.custom) return;                 // custom: keep whatever's in the fields
    set(state.config, "relocation.col_ratio", d.col);
    set(state.config, "relocation.fx_sigma", d.fx);
    set(state.config, "state.inflation_cn", d.infl);
    set(state.config, "china_healthcare.cost_working_age_real", d.hcW);
    set(state.config, "china_healthcare.cost_senior_real", d.hcS);
    set(state.config, "ss_nra.haircut_fraction", d.hair);
    set(state.config, "tax_cn.withdrawal_tax_traditional", d.tax);
  }

  // =========================================================== field help (ⓘ tooltips)
  //: Roadmap 7.0 side slice. Topics for the help page, and the ONLY
  //: place the grouping lives. The page renders them from `HELP` --
  //: the same table the field tooltips read -- so a help page cannot
  //: drift from what a control actually says. Writing a second set of
  //: explanations would be a fifth instance of one-fact-in-two-places
  //: in a single week.
  const HELP_TOPICS = [
    { id: "where", title: ["你现在在哪里", "Where you are now"],
      blurb: ["起点的那几个数字：年龄、支出、已有资产。改它们，别的一切都跟着动。",
              "The handful of numbers everything else moves from: age, spending, what you already have."],
      blocks: ["state", "initial", "name", "milestones"] },
    { id: "earning", title: ["还在工作的那些年", "While you are still working"],
      blurb: ["工资怎么长、会不会升职、会不会失业 —— 积累期的全部输入。",
              "How pay grows, whether a promotion lands, whether a layoff does."],
      blocks: ["contributions", "promotion", "layoff", "human_capital"] },
    { id: "income", title: ["退休后的收入", "Income after you stop"],
      blurb: ["养老金、租金、年金 —— 不靠卖资产就有的钱。",
              "Pensions, rent, annuities: money that arrives without selling anything."],
      blocks: ["income_streams", "guaranteed_income"] },
    { id: "markets", title: ["钱怎么长，怎么取", "How the money grows, and comes out"],
      blurb: ["市场假设、股债配置、每年取多少，以及支出本身的形状。",
              "Market assumptions, the equity/bond mix, how much comes out each year, and the shape of the spending itself."],
      blocks: ["returns", "bonds", "glide", "rule", "blocky_spending"] },
    { id: "tax", title: ["税", "Tax"],
      blurb: ["美国税、逐年真实税表、Roth 转换、以及跨境那一层。",
              "US tax, the year-by-year real tax path, Roth conversions, and the cross-border layer."],
      blocks: ["tax_us", "tax_true", "tax_cn", "obbba", "roth_ladder"] },
    { id: "medical", title: ["医疗", "Medical"],
      blurb: ["退休前最大的一块不确定性，也是数据最难养的一块。",
              "The largest uncertainty before 65, and the hardest data to keep current."],
      blocks: ["medical", "aca", "china_healthcare"] },
    { id: "housing", title: ["住房", "Housing"],
      blurb: ["自住房、买卖、缩表，以及它的价值为什么默认不进净资产。",
              "The home, buying and selling, downsizing, and why its value stays out of net worth by default."],
      blocks: ["housing", "house_price", "sh_property"] },
    { id: "family", title: ["家庭与代际", "Family and generations"],
      blurb: ["配偶、父母、长期护理、继承与遗产。",
              "A spouse, parents, long-term care, inheritance and estate."],
      blocks: ["household", "parents", "eldercare", "inheritance", "estate", "mortality"] },
    { id: "cross", title: ["社保与跨境", "Social Security and moving"],
      blurb: ["领取年龄、信托基金枯竭、以及搬到别的国家。",
              "Claiming age, trust fund depletion, and moving somewhere else."],
      blocks: ["social_security", "ss_nra", "ss_trust_fund", "relocation"] },
  ];

  //: Entries whose home is elsewhere but which belong in the
  //: caveats section too. POINTERS, not ownership: every HELP key
  //: has exactly one topic, which is what makes the coverage gate
  //: a count rather than a judgement.
  const HELP_CAVEAT_KEYS = ["blocky_spending.enabled", "rule.cut_realisation", "ss_trust_fund.enabled", "house_price.enabled", "human_capital.enabled", "bonds.correlation_with_equity", "layoff.bad_year_multiplier"];

  //: Two entries whose block says housing but whose subject is
  //: today's balance sheet. Cash is not a housing question.
  const HELP_KEY_TOPIC = {"other_assets.cash": "where", "other_assets.other_liquid": "where"};
  const HELP_BLOCK_FALLBACK = {"other_assets": "housing"};

  //: The help page, rendered from `HELP` rather than written twice.
  //:
  //: 202 bilingual explanations already existed and were reachable only by
  //: hovering the exact control they belong to. The content was never thin;
  //: it had no entrance. This gives it one, grouped by what a reader is
  //: thinking about rather than by config block.
  function helpTopicOf(key) {
    if (HELP_KEY_TOPIC[key]) return HELP_KEY_TOPIC[key];
    const block = key.split(".")[0];
    for (const t of HELP_TOPICS) if (t.blocks.indexOf(block) >= 0) return t.id;
    return HELP_BLOCK_FALLBACK[block] || null;
  }

  function helpEntriesByTopic() {
    const out = {};
    for (const t of HELP_TOPICS) out[t.id] = [];
    for (const key of Object.keys(HELP)) {
      const topic = helpTopicOf(key);
      if (topic && out[topic]) out[topic].push(key);
    }
    return out;
  }

  //: The label a control carries, so an entry reads as the thing the user
  //: saw rather than as a config path. Falling back to the path is honest:
  //: an entry with no control is a real state worth seeing.
  function helpLabelOf(key) {
    for (const step of (typeof STEPS !== "undefined" ? STEPS : [])) {
      for (const f of (step.fields || [])) {
        if (f.p === key) return lbl(f);
      }
    }
    return key;
  }

  function renderHelpTopics() {
    const grouped = helpEntriesByTopic();
    const parts = [];
    for (const t of HELP_TOPICS) {
      const keys = grouped[t.id] || [];
      if (!keys.length) continue;
      const rows = keys.map(k => {
        const help = HELP[k];
        return `<div class="help-entry">` +
               `<div class="help-entry-t">${helpLabelOf(k)}</div>` +
               `<div class="help-entry-b">${tt(help[0], help[1])}</div></div>`;
      }).join("");
      parts.push(`<details class="help-topic"><summary><b>${tt(t.title[0], t.title[1])}</b>` +
                 ` <span class="mut">${keys.length}</span></summary>` +
                 `<p class="help-blurb">${tt(t.blurb[0], t.blurb[1])}</p>${rows}</details>`);
    }
    const caveats = HELP_CAVEAT_KEYS.filter(k => HELP[k]).map(k =>
      `<li><b>${helpLabelOf(k)}</b> — ${tt(HELP[k][0], HELP[k][1]).slice(0, 160)}…</li>`).join("");
    parts.push(`<details class="help-topic"><summary><b>` +
      tt("这个模型可能在哪里骗你", "Where this model may be flattering you") +
      `</b></summary><p class="help-blurb">` +
      tt("这几个旋钮是刻意加进来戳破乐观假设的。它们都默认关闭 —— 也就是说，" +
         "你现在看到的结果，是把它们全部假设为「不会发生」算出来的。",
         "These dials exist to puncture optimistic assumptions. All of them " +
         "default to off, which means the numbers you are looking at assume " +
         "every one of these does not happen.") +
      `</p><ul class="lim">${caveats}</ul></details>`);
    return parts.join("");
  }

  const HELP = {
    "state.start_age": ["你现在的实际年龄，模拟从这里开始。", "Your current age; the simulation starts here."],
    "state.accum_years": ["最多再工作多少年就停止缴款、开始退休判定。例：25。", "Max more years you'd keep working before drawdown. e.g. 25."],
    "state.retire_horizon": ["退休后要覆盖多少年（够长以覆盖长寿）。例：50。", "Years to cover in retirement (long enough for longevity). e.g. 50."],
    "state.expenses_y0": ["退休后每年花费，用今日购买力、税后口径。例：$40,000。", "Annual retirement spending in today's dollars, after-tax. e.g. $40,000."],
    "state.swr_pref": ["首年从组合提取的比例，越低越保守。经典 4%，本模型默认更稳的 3.33%。", "First-year withdrawal rate; lower = safer. Classic 4%; this model defaults to 3.33%."],
    "state.inflation": ["长期年通胀假设，用于把今日美元换算成名义。例：3%。", "Long-run inflation, converts today's $ to nominal. e.g. 3%."],
    "milestones.0": ["你想追踪的财富里程碑（名义余额首次跨越）。例：$1,000,000。", "A wealth milestone to track (first nominal crossing). e.g. $1,000,000."],
    "milestones.1": ["第二个财富里程碑。例：$3,000,000。", "A second wealth milestone. e.g. $3,000,000."],
    "initial.pretax_401k": ["401(k)/传统 IRA 等税前账户今日余额（提取时按普通所得计税）。", "Today's 401(k)/traditional IRA balance (taxed as ordinary income on withdrawal)."],
    "initial.roth_ira": ["Roth 账户今日余额（合规提取免税）。", "Today's Roth balance (qualified withdrawals tax-free)."],
    "initial.hsa": ["HSA 今日余额（医疗用途免税）。", "Today's HSA balance (tax-free for medical)."],
    "initial.taxable": ["普通券商/应税账户今日余额（提取按资本利得计税）。", "Today's taxable brokerage balance (capital-gains taxed)."],
    "other_assets.cash": ["现金、活期/定期存款、货币基金。会并入应税桶参与模拟。", "Cash, savings/CDs, money-market funds. Folded into the taxable bucket for simulation."],
    "other_assets.other_liquid": ["其他随时可变现的资产：加密货币、另一家券商、股票期权已归属部分等。并入应税桶。", "Other liquid holdings: crypto, another brokerage, vested equity, etc. Folded into taxable."],
    "other_assets.home_equity": ["自住房市值减按揭余额。默认不计入 FI 组合（非流动、要住），只做记录——除非你计划出售。", "Home value minus mortgage. Excluded from the FI portfolio by default (illiquid; you live in it) — unless you plan a sale."],
    "other_assets.sell_home_enabled": ["勾选后，出售净得会在指定年龄作为一次性收入进入模拟（缩表/搬家场景）。", "If checked, sale proceeds enter the simulation as a one-time inflow at the chosen age (downsizing/relocating)."],
    "other_assets.sell_home_age": ["计划出售的年龄。", "Age at which you plan to sell."],
    "other_assets.sell_home_net_real": ["扣除税费、中介、还贷后的净得，今日美元。", "Net of taxes, fees and payoff, in today's dollars."],
    "contributions.base_salary_pre": ["税前基础年薪，不含奖金/加班。", "Pre-tax base salary, excl. bonus/overtime."],
    "contributions.bonus_pre": ["税前年终奖预估。没有就填 0。", "Estimated pre-tax annual bonus. 0 if none."],
    "contributions.ot_income_pre": ["税前加班/额外收入。没有就填 0。", "Pre-tax overtime/extra income. 0 if none."],
    "contributions.salary_growth_pre": ["常规年涨薪率（晋升单列在高级步）。例：3.5%。", "Routine annual raise rate (promotions are separate, in Advanced). e.g. 3.5%."],
    "contributions.pretax_401k_limit_y1": ["今年计划缴入 401(k) 的税前额，常按 IRS 上限。", "Planned pre-tax 401(k) contribution this year (often the IRS limit)."],
    "contributions.roth_ira_limit_y1": ["今年计划缴入 Roth IRA 的额度。", "Planned Roth IRA contribution this year."],
    "contributions.hsa_limit_y1": ["今年计划缴入 HSA 的额度。2026 年 $4,400 是 self-only 参考值；本模型未建模 family HSA 上限。", "Planned HSA contribution this year. The 2026 $4,400 reference is self-only; this model does not model a family HSA cap."],
    "contributions.match_rate": ["雇主匹配比例（占匹配基数）。例：5%–6%。", "Employer match rate (of the match base). e.g. 5–6%."],
    "contributions.match_excludes_bonus": ["匹配基数是否不含奖金（多数公司如此）。", "Whether the match base excludes bonus (most companies do)."],
    "contributions.annual_spending_now": ["在职期间每年实际生活成本（今日$）——工资扣掉税、退休账户缴款和这笔开销后，剩余才进应税账户。留空＝与退休支出相同。这是决定储蓄率的关键数字。", "Your actual annual living cost while working (today's $) — salary minus taxes, retributions and THIS is what lands in taxable. Blank = same as retirement spending. This drives your savings rate."],
    "income_streams.pension_enabled": ["联邦/州雇员、军人、教师等的固定养老金或商业年金。请输入今日美元、税后可花金额；退休后先覆盖年度开销，剩余才进入应税组合。", "Defined-benefit pension or annuity (federal/state/military/teacher). Enter today's-dollar, after-tax spendable cash; in retirement it covers annual spending first and only the surplus enters taxable."],
    "income_streams.pension_cola": ["有 COLA＝金额随通胀走（军人/联邦多数有）；无 COLA＝名义固定，购买力逐年缩水（多数私营年金）。", "With COLA = keeps pace with inflation (most federal/military); without = nominally fixed, so purchasing power erodes (most private annuities)."],
    "income_streams.rental_enabled": ["出租房的税后净现金流（租金 − 按揭/维护/空置/税）。房产本体价值请放在「其他资产」。", "After-tax net cash flow from rentals (rent − mortgage/upkeep/vacancy/tax). Put the property value itself under Other Assets."],
    "income_streams.parttime_enabled": ["Barista/Coast FIRE：只在实际退休后开始，起点是「录入的最早年龄」与「实际 FIRE 后第一年」中较晚者，并准确持续所填年数。", "Barista/Coast FIRE: starts only after actual retirement, at the later of the entered earliest age and the first year after actual FIRE, for exactly the entered number of years."],
    "income_streams.equity_enabled": ["RSU 等股权按年归属的税后价值（今日$）；从下一个 modeled year 起准确归属 N 次。FIRE 前并入应税组合，退休后先覆盖开销。", "After-tax value of RSU/equity vesting per year (today's $), paid exactly N times starting next modeled year. It enters taxable before FIRE and covers spending first after retirement."],
    "income_streams.pension_owner": ["收入归属只决定成员死亡后是否继续；所有起止年龄始终按你的年龄轴。未选择归属或旧计划显示「未确认归属」并沿用末生存者语义，不代表共同所有；单人模式下所有有效选择都按本人。", "Ownership only controls whether cash continues after a member dies; every age remains on your age timeline. An unselected owner or old plan shows Unconfirmed ownership and retains last-survivor behavior, which does not mean shared ownership; in single-person mode every valid choice behaves as you."],
    "income_streams.rental_owner": ["收入归属只决定成员死亡后是否继续；所有起止年龄始终按你的年龄轴。未选择归属或旧计划显示「未确认归属」并沿用末生存者语义，不代表共同所有；单人模式下所有有效选择都按本人。", "Ownership only controls whether cash continues after a member dies; every age remains on your age timeline. An unselected owner or old plan shows Unconfirmed ownership and retains last-survivor behavior, which does not mean shared ownership; in single-person mode every valid choice behaves as you."],
    "income_streams.parttime_owner": ["收入归属只决定成员死亡后是否继续；所有起止年龄始终按你的年龄轴。未选择归属或旧计划显示「未确认归属」并沿用末生存者语义，不代表共同所有；单人模式下所有有效选择都按本人。", "Ownership only controls whether cash continues after a member dies; every age remains on your age timeline. An unselected owner or old plan shows Unconfirmed ownership and retains last-survivor behavior, which does not mean shared ownership; in single-person mode every valid choice behaves as you."],
    "income_streams.equity_owner": ["收入归属只决定成员死亡后是否继续；所有起止年龄始终按你的年龄轴。未选择归属或旧计划显示「未确认归属」并沿用末生存者语义，不代表共同所有；单人模式下所有有效选择都按本人。", "Ownership only controls whether cash continues after a member dies; every age remains on your age timeline. An unselected owner or old plan shows Unconfirmed ownership and retains last-survivor behavior, which does not mean shared ownership; in single-person mode every valid choice behaves as you."],
    "contributions.marginal_tax_pre": ["你目前的边际税率（联邦+州合计估）。例：24%–32%。", "Your current marginal tax rate (fed+state est.). e.g. 24–32%."],
    "returns.return_distribution": ["股票收益的抽样分布。Student-t 尾部更厚（更真实的极端），正态更平滑。", "Equity-return distribution. Student-t = fatter tails (realistic extremes); Normal = smoother."],
    "returns.inflation_mu": ["收益模型内部的通胀均值，一般与上面的通胀一致。", "Mean inflation inside the return model; usually matches the inflation above."],
    "estate.exemption_real": ["选填。填一个数字，结论页就会告诉你有多少比例的路径终值超过它。这个数字由你自己填——它由立法决定、会变，而本 App 不联网、不携带税表，内置一个数字只会悄悄过期。留空＝不显示这一行。本 App 永远不建模遗产税：不做豁免额指数化、不做配偶可携带、不做州遗产税/继承税、不做信托或赠与安排。它只数路径，不算税。", "Optional. Enter a number and the conclusions page will tell you what fraction of paths end above it. This number is YOURS to enter: it is set by legislation, it moves, and this app makes no network requests and ships no tax tables — a bundled figure would age silently into a wrong one. Blank = the line is not shown. This app will NEVER model estate tax: no exemption indexing, no portability, no state estate or inheritance tax, no trusts or gifting. It counts paths; it does not compute a tax."],
    "social_security.enabled": ["是否把美国社保计入退休收入。", "Whether to include US Social Security as retirement income."],
    "social_security.pia_monthly_y0": ["你在 FRA 领取时的估计月社保（PIA），今日美元。FRA 67 与 70%/100%/124% 示意适用于 1960 年及以后出生 cohort；较早 cohort 可能不同。可在 ssa.gov 查。", "Estimated monthly SS at FRA (your PIA), today's $. FRA 67 and the 70%/100%/124% illustration apply to the 1960-and-later birth cohort; earlier cohorts may differ. Check ssa.gov."],
    "social_security.claim_age": ["开始领社保的年龄（62–70）。晚领每月更多。", "Age you start claiming SS (62–70). Later = higher monthly."],
    "medical.premium_aca": ["退休后到 65 岁前的自购医保未补贴全价：年度、家庭合计、今日美元。可用 healthcare.gov/see-plans/ 查询你当前地点与年龄的报价；本 App 不联网获取或核验。", "Unsubsidized full-price self-bought coverage before 65: annual, household total, today's dollars. You can quote your current location and age at healthcare.gov/see-plans/; this App does not fetch or verify it."],
    "medical.premium_aca_age_end": ["可选，且只在「逐年医疗轨迹」开启时有效：同一个县、同一个计划、同一覆盖家庭，把年龄改成 Medicare 年龄前一年之后的年度家庭全价报价（今日美元）。它只用来提供年龄效应：两个报价之比是纯年龄倍数，与通胀无关，App 按每年固定百分比在两个年龄之间取值，再与 CPI 和「保费超额通胀」相乘，不重复计入。留空 = 你没报过第二个年龄的价，属于「未测量」，保费维持单锚点行为；App 不会替你猜曲线，也不会把留空写成 $0。两个报价年龄之外不外推，按最近的报价持平。填了却关掉逐年轨迹会被明确拒绝，而不是被悄悄忽略。", "Optional, and only in effect when the Annual medical trajectory is on: the annual household full-price quote for the same county, plan and covered household, re-priced at the year before your Medicare age (today's dollars). Its only job is the AGE effect — the ratio of the two quotes is a pure age multiple with no inflation in it, and the App moves between the two ages at one fixed percentage a year before multiplying by CPI and the premium inflation spread, so nothing is double-counted. Blank means you never priced a second age: that is unmeasured, the premium keeps its single-anchor behaviour, and the App neither guesses a curve nor writes $0. Outside the two quoted ages it holds flat rather than extrapolating. Filling it in while the trajectory is off is refused by name rather than silently ignored."],
    "medical.household_share_primary": ["可选，且只在「逐年医疗轨迹」开启并且启用了「夫妻共同」时有效：上面那些医疗数字里属于你本人的比例，剩下的归配偶。上面四个数字仍然是「年度家庭合计」，口径不变，这里只是把它拆开。拆开之后两件事会变：① 夫妻各自按自己的年龄从 ACA 切到 Medicare（用你填的年龄差），不再一起切；② 其中一人去世后，美国医疗篮子降到在世那一位的份额，而不是继续按两人份付。留空 = 你没拆过，属于「未测量」，不是「五五开」—— 留空时保持现行的家庭整体口径。注意一处已知局限：ACA 补贴的开关仍按计划持有人的年龄判断，所以「持有人已进 Medicare、配偶还在买 ACA」那几年不会算补贴（偏保守，会把成本报高）；反过来那几年的补贴只按 ACA 那一部分算，不会误补到 Medicare 保费上。", "Optional, and only in effect when the Annual medical trajectory is on AND the couple/household is enabled: your own share of the medical figures above, with the remainder covering your spouse. Those four figures stay ANNUAL HOUSEHOLD TOTALS — their meaning does not change; this only splits them. Splitting changes two things: (1) each of you moves from ACA to Medicare at your OWN age using the age gap you entered, instead of both switching together; (2) after the first death the US medical basket drops to the surviving member's share instead of continuing to pay for two. Blank means you never split it: unmeasured, not fifty-fifty, and the current household-wide treatment is kept. One known limitation: the ACA subsidy still switches off on the plan holder's age, so in years when the holder is on Medicare while the spouse is still buying a bridge policy no subsidy is modelled (conservative — it overstates cost); in the opposite years the subsidy is computed against the ACA portion only, so it can never pay down a Medicare premium."],
    "medical.eol_peak_real": ["可选，且只在开启死亡率抽样时有效：「一个人」的临终医疗支出，今日美元。按裁定「每一次死亡各计一次」——单身计一次；夫妻计两次，先走那位的峰进当年支出（路径不终止），第二位的峰在路径终止那一年从组合扣。留空 = 你没给过这个数，属于「未测量」，运行结果逐位不变；而开启后某条路径在窗口内没死，是「测过、这条路径没发生」的 0，两者在结果里分开报。两点要知道：它不走 ACA 补贴（临终护理不是保费，让补贴去付它是错的），也不按目的地生活成本折算（你填多少就是多少，按地理重新定价是你没说过的关系）。关闭死亡率抽样时填它会被明确拒绝，因为那样它永远不会触发。", "Optional, and only in effect when mortality sampling is on: ONE person's end-of-life medical spending, in today's dollars. Charged once per death by ruling: once for a single person; twice for a couple, where the first death's peak joins that year's spending (the run continues) and the second is drawn from the portfolio in the year the run ends. Blank means you never supplied a figure — unmeasured, and the run is unchanged bit for bit; whereas a path that never died inside the horizon reports a measured zero. The two are reported separately. Two things to know: it does not pass through the ACA subsidy (terminal care is not a premium, and letting an affordability cap pay for it would be wrong), and it is not re-priced by destination cost of living — the figure is the one you typed. Filling it in with mortality sampling off is refused by name, because it could never fire."],
    "medical.medicare_age": ["开始享 Medicare 的年龄（美国一般 65）。", "Age Medicare starts (US typically 65)."],
    "tax_true.enabled": ["2.0 真税引擎：逐年真算联邦税——普通税档+标准扣除、资本利得堆叠、社保两档应税规则（名义门槛的「税收鱼雷」）、RMD 强制提取、IRMAA、真 MAGI 的 ACA 补贴。关闭=沿用平率近似。2026 年表。", "The 2.0 true tax engine: real yearly federal math — brackets+std deduction, LTCG stacking, the two-tier SS taxation rule (nominal thresholds: the 'tax torpedo'), RMDs, IRMAA, true-MAGI ACA. Off = flat approximations. 2026 tables."],
    "tax_true.rmd_age": ["强制最低提取的起始年龄。SECURE 2.0：1960 年后出生为 75。", "Age RMDs begin. SECURE 2.0: 75 for those born after 1960."],
    "tax_true.taxable_gain_fraction": ["应税账户每笔提取中算作长期资本利得的比例（成本基础代理——引擎不逐笔追踪 lot）。长期持有者通常 50–80%。", "Share of each taxable withdrawal treated as LTCG (cost-basis proxy — the engine doesn't track lots). Long holders: typically 50–80%."],
    "tax_true.irmaa_enabled": ["65 岁后按 MAGI 分档的 Medicare B/D 附加费（2026 档；有可用的模型历史时使用保费年前两年的最终 MAGI 与当年报税身份，否则明确退回当年 MAGI 代理）。夫妻按两人计。", "Post-65 Medicare B/D surcharges by MAGI tier (2026; use the modeled final MAGI and filing status from two tax years before the premium year when available, otherwise explicitly fall back to the current-year MAGI proxy). Couples pay per person."],
    "roth_ladder.enabled": ["是否退休后每年把部分税前转成 Roth（降低未来税/RMD）。", "Convert some pretax→Roth each retirement year (lowers future tax/RMDs)?"],
    "roth_ladder.annual_conversion_y0": ["每年转换额，今日美元。例：$40,000。", "Annual conversion amount, today's $. e.g. $40,000."],
    "relocation.enabled": ["勾选后同时计算「留在本土」和「搬到目的地」两条路并对比。", "When checked, runs both 'stay' and 'relocate' and compares them."],
    "relocation.destination": ["选一个城市自动填下面的生活成本/汇率/税/医疗；或选「自定义」手填。", "Pick a city to auto-fill cost/FX/tax/healthcare below, or 'Custom' to enter your own."],
    "relocation.relocation_age": ["计划搬迁的年龄。", "Age you plan to relocate."],
    "relocation.col_ratio": ["目的地生活成本相对本土的比例。0.60 = 便宜 40%。", "Destination cost of living vs home. 0.60 = 40% cheaper."],
    "state.inflation_cn": ["目的地本地通胀。", "Local inflation at the destination."],
    "relocation.fx_sigma": ["本币兑美元的年化波动，越高搬迁后的消费/遗产区间越宽。", "Local-currency vs USD volatility; higher = wider post-move ranges."],
    "relocation.ppp_kappa": ["购买力平价锚：κ>0 时汇率对数值每年向初始汇率回归 κ 比例，长期分布收窄——学界对实际汇率均值回归有较强证据（半衰期约 3–5 年 ≈ κ 0.15–0.25）。0 保持纯随机游走。", "PPP anchor: with κ>0 the log FX reverts toward the initial rate by κ per year, narrowing long-run dispersion — real-exchange-rate mean reversion has solid evidence (3–5y half-life ≈ κ 0.15–0.25). 0 keeps the pure random walk."],
    "china_healthcare.cost_working_age_real": ["目的地退休~65 岁的自费医疗/保险年额，今日美元。", "Destination annual healthcare/insurance, retire–65, today's $."],
    "china_healthcare.cost_senior_real": ["目的地 65 岁以上的自费医疗年额，今日美元。", "Destination annual healthcare, 65+, today's $."],
    "ss_nra.haircut_fraction": ["海外领美国社保的折减比例，多数国家为 0。", "Haircut on US SS while abroad; 0 for most countries."],
    "tax_cn.withdrawal_tax_traditional": ["目的地对税前账户提取的有效税率，免税地填 0。", "Destination effective tax on pretax withdrawals; 0 for tax-free locales."],
    "layoff.enabled": ["建模'某年失业几个月'：该年所有缴款按比例损失。坏市场年份概率放大（裁员与熊市相关）——这是积累期最被低估的风险。", "Models 'laid off for some months in a year': that year's contributions shrink proportionally. Probability multiplies in bad market years (layoffs correlate with bear markets) — the most underrated accumulation risk."],
    "layoff.p_annual": ["正常年份的失业概率。美国白领长期均值约 2–4%。", "Layoff probability in a normal year. US white-collar long-run ≈2–4%."],
    "layoff.return_threshold": ["多低的股市年算「坏年」。当年收益 ≤ 这个值时，失业概率会被放大（默认 3 倍，并有上限），因为裁员和熊市同时发生。", "How bad a market year counts as a 'bad year'. When the year's return is at or below this, layoff probability is multiplied (3x by default, capped) — layoffs and bear markets arrive together."],
    "layoff.gap_months": ["失业后空窗多少个月。该年的全部缴款按 空窗月数/12 的比例损失（不只是工资部分）。", "Months out of work after a layoff. That year loses this fraction (months/12) of ALL contributions, not just the salary portion."],
    "housing.enabled": ["把住房从「开销里的一个数字」升级为逐年现金流：租金、按揭、房产税、维护、房价升值分别建模。关闭 = 住房成本仍留在年开销里。", "Upgrades housing from one number inside expenses to modelled yearly cash flows: rent, mortgage, property tax, maintenance and appreciation each separately. Off = housing stays lumped in annual expenses."],
    "housing.replace_annual": ["你的年开销里已经包含多少住房成本。这笔钱会被退回，再换成下面这套逐年住房现金流 —— 填 0 会让住房被算两次。", "How much housing cost your annual expenses ALREADY contain. It is refunded and replaced by the modelled cash flows below — leaving it at 0 double-counts housing."],
    "housing.mode": ["长期租房，还是先租、到某个年龄买房。买房会在购房年产生首付支出并开始按揭。", "Rent for good, or rent until a chosen age and then buy. Buying creates a down-payment outflow in the purchase year and starts a mortgage."],
    "housing.monthly_rent": ["今日美元的月租。模型自己处理通胀，所以填现在的市价，不要预估未来。", "Monthly rent in today's dollars. The model handles inflation, so enter today's market rate rather than a future guess."],
    "housing.rent_growth_real": ["房租超出通胀的年增速。0 = 房租与通胀同步。房源紧张的城市为正，人口流出的城市可为负。", "Annual rent growth ABOVE inflation. 0 = rent simply tracks CPI. Positive in supply-constrained cities, negative where population is leaving."],
    "housing.purchase_age": ["计划买房的年龄。在此之前按上面的月租付租金，从这一年起转为按揭。", "The age you plan to buy. Rent is paid until then, and the mortgage starts that year."],
    "housing.price": ["今日美元的房价。到购房那年，模型会按下面的实际升值率把它推算到当时的价格。", "Home price in today's dollars. The model grows it to the purchase year at the real appreciation rate below."],
    "housing.down_pct": ["首付占房价的比例。其余部分为贷款额。20% 以下通常需要额外的贷款保险，本模型不建模那笔费用。", "Down payment as a share of price; the rest is borrowed. Below 20% usually triggers mortgage insurance, which this model does not include."],
    "housing.rate": ["按揭的名义年利率（不是实际利率）—— 就是贷款合同上那个数字。", "Nominal annual mortgage rate, not a real one — the number on the loan agreement."],
    "housing.term_years": ["贷款年限。30 年月供低、总利息高；15 年反之。", "Loan term. 30 years means a lower payment and more total interest; 15 years the reverse."],
    "housing.tax_pct": ["房产税，按房子的当期价值计（不是买入价），所以房价涨了这笔也会涨。美国多数州约 0.5–2%。", "Property tax as a share of the home's CURRENT value, not its purchase price — so it rises as the home appreciates. Roughly 0.5–2% across US states."],
    "housing.maint_pct": ["维护与大修的年成本，同样按房子的当期价值计。经验值约 1%，老房子更高。", "Yearly maintenance and repairs, also as a share of current value. Rule of thumb ≈1%, higher for older homes."],
    "housing.appreciation_real": ["房价超出通胀的年增速。0 = 房子只是保值。美国长期实际涨幅接近 0–1%，把它设高是租买对比里最容易骗自己的地方。", "Home-price growth ABOVE inflation. 0 = the house merely holds its value. US long-run real appreciation is near 0–1%; setting it high is the easiest way to fool yourself in a rent-vs-buy comparison."],
    "housing.refi_enabled": ["计划中途再融资。本模型只改利率并按剩余年限重新摊销，不建模手续费与结清成本。", "Plan a refinance. The model changes the rate and re-amortizes over the remaining term; it does not model fees or closing costs."],
    "housing.refi_age": ["再融资发生的年龄。当年年末按新利率重算月供。", "The age the refinance happens. The payment is recalculated at the new rate from the end of that year."],
    "housing.refi_rate": ["再融资后的名义年利率。", "Nominal annual rate after refinancing."],
    "income_streams.pension_annual_real": ["今日美元的年领取额，税后可花金额。退休后先用来覆盖当年开销，有剩余才进入应税组合。", "Yearly amount in today's dollars, after tax and spendable. In retirement it covers that year's spending first; only the surplus enters the taxable portfolio."],
    "income_streams.pension_start_age": ["开始领取的年龄。在此之前这笔收入不存在，不会提前帮你撑住提取。", "The age payments begin. Before it the income does not exist and cannot cushion withdrawals."],
    "income_streams.rental_annual_net_real": ["今日美元的净租金：已扣掉空置、维修、物业与税。填毛租金会高估这条现金流。", "NET rent in today's dollars — after vacancy, repairs, management and tax. Entering gross rent overstates this stream."],
    "income_streams.rental_start_age": ["这份租金收入从哪一年开始计入。", "The age this rental income starts counting."],
    "income_streams.rental_end_age": ["租金停止的年龄（卖房或不再出租）。房产卖出所得本身不在这里 —— 那是一次性事件，用人生事件录入。", "The age rent stops (you sell or stop letting). The sale proceeds are NOT included here — that is a one-off, entered as a life event."],
    "income_streams.parttime_annual_real": ["今日美元的兼职年收入。用来检验「不是完全不工作，而是少工作」能把 FIRE 提前多少。", "Part-time income per year in today's dollars. Use it to test how much 'working less' rather than 'not working' moves your FIRE date."],
    "income_streams.parttime_start_age": ["最早开始兼职的年龄。模型不会让它早于你实际 FIRE 的后一年 —— 否则它就变成了在职收入。", "Earliest age part-time work can start. The model never begins it before the year after your actual FIRE, otherwise it is just salary."],
    "income_streams.parttime_years": ["打算兼职多少年。兼职期结束后完全依赖组合提取。", "How many years you plan to work part-time. After that the portfolio carries everything."],
    "income_streams.equity_annual_real": ["今日美元的年归属价值（RSU/期权）。请按税后、且按你真的会卖出的金额填，未卖出的持股是集中风险而不是现金流。", "Yearly vesting value in today's dollars (RSU/options). Enter it after tax and only what you would actually sell — unsold shares are concentration risk, not cash flow."],
    "income_streams.equity_years": ["还剩几年归属，从下一个模拟年份算起。归属结束后这条收入消失。", "How many vesting years remain, counted from the next modelled year. The stream ends after that."],
    "household.spouse_initial_roth": ["配偶 Roth IRA 的现有余额。与你的账户合并成一个家庭资产堆一起模拟，不分开追踪。", "Spouse's CURRENT Roth IRA balance. It is merged into one household stack with yours and not tracked separately."],
    "household.spouse_initial_hsa": ["配偶 HSA 的现有余额，同样并入家庭资产堆。", "Spouse's current HSA balance, likewise merged into the household stack."],
    "household.spouse_initial_taxable": ["配偶应税账户的现有余额，并入家庭应税桶（提取时税务处理与你的相同）。", "Spouse's current taxable balance, merged into the household taxable bucket — withdrawals are taxed the same way as yours."],
    "household.spouse_bonus_pre": ["配偶的年度奖金（今日美元、税前）。与工资一样按下面的薪资增速逐年增长。", "Spouse's annual bonus in today's pre-tax dollars. It grows each year at the salary growth rate below, like the salary itself."],
    "household.spouse_salary_growth_pre": ["配偶薪资的名义年增速。只影响积累期，退休后不再使用。", "Nominal annual growth of the spouse's salary. It affects accumulation only and is unused after retirement."],
    "household.spouse_pretax_401k_limit_y1": ["配偶第一年实际缴入 401k 的金额，不是法定上限。之后每年按 IRS 的指数化幅度增长。", "The amount the spouse ACTUALLY contributes to a 401k in year one — not the statutory cap. It grows each year with IRS indexation."],
    "household.spouse_roth_ira_limit_y1": ["配偶第一年实际缴入 Roth IRA 的金额，同样按 IRS 指数化逐年增长。", "The amount the spouse actually contributes to a Roth IRA in year one, likewise growing with IRS indexation."],
    "household.spouse_hsa_limit_y1": ["配偶第一年实际缴入 HSA 的金额，同样按 IRS 指数化逐年增长。", "The amount the spouse actually contributes to an HSA in year one, likewise growing with IRS indexation."],
    "household.spouse_marginal_tax_pre": ["配偶积累期收入的边际税率，用于估算他/她税后能存下多少。", "Marginal tax rate on the spouse's income during accumulation, used to estimate how much they can actually save."],
    "household.spouse_claim_age": ["配偶开始领社保的年龄。62 最早但金额最低，70 最高；夫妻两人的领取年龄可以不同。", "The age the spouse claims Social Security. 62 is earliest and smallest, 70 is largest; the two of you can claim at different ages."],
    "tax_true.state_archetype": ["按「类型」而不是按「州」选。选了它就取代下面那个平率，因为一个平率说不出三件真正决定「要不要为了税搬家」的事：普通收入与资本利得可能不同税率、社保福利在有些地方要再交一次州税、以及退休收入（养老金/IRA 提取）可能有一大笔免税额。这些是原型，不是任何一个州的税法：税率是为了把类型彼此分开而取的整数，不承诺复现任何一份真实报税表；App 也不会把任何一行标上州名。要精确到某个州，请咨询税务师。留空＝沿用下面的平率，与没有这个功能时逐位一致。", "Chosen by TYPE, not by state. Selecting one replaces the flat rate below, because a single flat rate cannot express the three things that actually decide whether moving is worth it: ordinary income and capital gains can be taxed differently, Social Security benefits are taxed again by some states, and retirement income (pensions, IRA withdrawals) can carry a large exemption. These are archetypes, not any state's tax code: the rates are round numbers chosen to separate one type from another, they do not reproduce any real filing, and the app never labels a row with a state name. For a specific state, ask a tax professional. Leaving it blank keeps the flat rate below and is bit-identical to before this existed."],
    "tax_true.state_rate": ["真实税引擎里的州税平率，同时作用于普通收入与长期资本利得。无州所得税填 0。", "Flat state rate inside the true-tax engine, applied to both ordinary income and long-term capital gains. Enter 0 if your state has no income tax."],
    "rule.cut_realisation": ["护栏触发后，你实际砍下去的比例。默认 100% 是这个引擎一直以来的假设——触发即全额砍到位——而那个假设从没对照真实行为校准过。调到 100% 以下，模型就假设你砍不满；没砍掉的那部分留在支出里变成风险，这正是这个拨盘存在的意义。加息方向刻意不打折：该涨没涨是另一种行为、另一套文献，用一个数同时建模两者等于断言它们是一回事。", "How much of a triggered guardrail cut you actually make. The default of 100% is what this engine has always assumed — a triggered cut landing in full — and that assumption has never been checked against real behaviour. Below 100% the model assumes you do not cut all the way; the part you do not cut stays in your spending and shows up as risk, which is the point of the dial. Raises are deliberately not damped: failing to take a raise is a different behaviour with a different literature."],
    "blocky_spending.enabled": ["真实支出不是一条平线：屋顶、车、婚礼、医疗自付超额。开启后，每年按下面的概率落一次大额。平滑支出会低估顺序风险——大额落在坏十年里，正是一个计划撑住或撑不住的那个事件。默认关闭，关闭时结果与以前逐位相同。", "Real spending is not a flat line: a roof, a car, a wedding, a medical excess. With this on, a lump lands each year with the probability below. Smooth spending understates sequence risk, because a lump arriving in a bad decade is exactly the event a plan survives or does not. Off by default, and bit-identical when off."],
    "blocky_spending.annual_probability": ["每个退休年份落一次大额的概率。默认 15%（约七年一次）是占位数字，不是发现——本 App 不携带任何人的实际支出史。按你自己的生活填：房子旧、车老、家里人多，这个数就该更高。到达时点与市场抽样独立：现实中修屋顶和熊市可能同时来，这个模型不建模那种相关性。", "Chance that a lump lands in any given retirement year. The default of 15% (roughly once every seven years) is a placeholder, not a finding — this app ships nobody's spending history. Set it from your own life: an older house, an older car, more people, and it should be higher. Arrivals are INDEPENDENT of market draws: in reality a roof and a bear market can arrive together, and that correlation is not modelled."],
    "blocky_spending.size_fraction": ["大额落下时，当年支出增加的比例。默认 35% 同样是占位数字。注意它不会表现为成功率下降：默认规则是 Guyton-Klinger 护栏，它靠事后削减吸收大额，所以块状支出表现为「剩得更少、过得更颠簸」而不是「计划失败」。", "How much that year's spending rises when a lump lands. The 35% default is a placeholder too. Note it will NOT show up as a lower success rate: the default rule is Guyton-Klinger, which absorbs a lump by cutting later, so lumps show up as less money left over and a rougher ride rather than as a failed plan."],
    "other_assets.sale_liquidity_discount": ["房子值多少和你卖了能拿到多少是两个数：佣金、买家要求的修缮、按时间表卖而不是从容卖的代价。默认 0 —— 一个默认就打折的模型会让每份计划悄悄变穷，而且没人知道是哪个数字动了。美国经纪佣金常见 5–6%，加上过户与修缮，8–10% 是常被引用的区间，但那不是本 App 给你的数，是你自己判断的起点。", "What the house is worth and what you get for it are two numbers: commission, the repairs a buyer demands, the cost of selling on a schedule rather than at leisure. Zero by default -- a model that discounted by default would make every plan quietly poorer with nobody knowing which figure moved. US agent commissions commonly run 5-6%, and 8-10% all-in is a frequently quoted range, but that is a starting point for your judgement, not a number this app is giving you."],
    "other_assets.downsize_enabled": ["缩表不是清仓：你卖掉旧房，然后要住在别处。勾上之后模型会在同一年扣掉新住处的价格，并且从那年起按新房算房产税与维护 —— 不勾的话，计划会拿着卖房款、然后免费住一辈子，那是能算出来的最漂亮的账。", "Downsizing is not liquidating: you sell the old place and then have to live somewhere. With this on the model charges the new home's price in the same year and, from then on, computes property tax and maintenance from the new house. Without it a plan banks the proceeds and lives somewhere free for life, which is the most flattering arithmetic available."],
    "other_assets.downsize_new_price_real": ["新住处的价格，今日美元。年龄用的是上面那个卖房年龄 —— 不另设一个，因为两个可能对不上的年龄比一个年龄更糟。填 0 = 你还没想好换到哪，模型不会替你假设一套免费的房子。", "Price of the new place in today's dollars. The age is the sale age above rather than a second field: two ages that can disagree are worse than one. Zero means you have not decided yet, and the model will not assume a free house on your behalf."],
    "human_capital.enabled": ["工资在本模型里一直是一条确定性曲线：每条路径同样的增长率。开启后它按随机过程走，分成持久与暂时两种冲击。关闭时逐位不变。", "Wages have always followed a deterministic curve here: the same growth rate on every path. With this on they follow a stochastic process split into permanent and transitory shocks. Bit-identical when off."],
    "human_capital.permanent_sigma": ["持久冲击的标准差 —— 丢掉的那个层级，你带着它走完整个职业生涯。它和暂时冲击刻意分开：用一个「工资波动率」同时表示两者，等于断言丢一次晋升和少发一次奖金是同一件事，而对一个计划来说它们完全不同。这个数字没有来源，行业与周期差异极大，本 App 不替你选。", "Standard deviation of the permanent shock -- the level you lose and then carry for the rest of your career. Deliberately separate from the transitory one: a single 'wage volatility' dial would assert that losing a promotion and missing a bonus are the same event, and to a plan they are nothing alike. The number has no source; dispersion by occupation and cycle is enormous and this app will not pick one for you."],
    "human_capital.transitory_sigma": ["暂时冲击的标准差 —— 一个你缓得过来的坏年份，不改变之后的水平。同样没有来源。", "Standard deviation of the transitory shock -- a bad year you recover from, leaving your level unchanged afterwards. Also unsourced."],
    "layoff.gap_months_per_year_of_age": ["每比下面那个年龄大一岁，找工作多花几个月。默认 0 —— 那是这个模型一直以来的假设：55 岁和 30 岁找工作一样快。那不是中性默认，那是个主张，而且对正在盘算「能不能辞职」的人来说是乐观的那个。这个数字没有来源：再就业速度随年龄下降是被测量过的，但按职业和周期差异极大。", "Extra months of job search per year of age past the age below. Zero by default, which is what this model has always assumed: a 55-year-old finds work as fast as a 30-year-old. That is not a neutral default, it is a claim, and it is the optimistic one for exactly the people working out whether they can afford to quit. The figure is unsourced: re-employment slowing with age is measured, but it varies enormously by occupation and cycle."],
    "layoff.decay_from_age": ["从哪一岁开始变难。注意一个算术上的事：如果你的积累期大部分在这个年龄之前，一个温和的数值可能完全看不出效果 —— 那不是坏了，是量太小。", "The age at which it starts getting harder. Note an arithmetic point: if most of your accumulation happens before this age, a gentle value may show no visible effect at all -- that is smallness, not a fault."],
    "bonds.correlation_with_equity": ["股票与债券一起动的程度，本模型里最基础的一条假设之一。直到 6.0 之前它在界面上根本不存在 —— 你既看不到也改不了，而它影响每一条路径。默认 0.15 是个温和正相关，实现用高斯 copula；那个数字没有外部来源，是本 App 的选择。它要紧的地方在于：相关性越高，「用债券对冲股票」越不管用，而那正是很多计划赖以撑过坏十年的东西。历史上这个数在不同十年里正负都出现过。", "How much stocks and bonds move together — one of the most basic assumptions in this model. Until 6.0 it did not exist in the interface at all: you could neither see nor change it, and it affects every path. The 0.15 default is a mild positive correlation implemented as a Gaussian copula; that number has no external source, it is this app's choice. It matters because the higher it is, the less bonds hedge stocks — and that hedge is what many plans rely on to survive a bad decade. Historically this figure has been both positive and negative in different decades."],
    "house_price.enabled": ["自住房的价值目前是一条确定性曲线 —— 每条路径完全相同。开启后它按随机过程走，用独立子生成器抽样。它通过两个门影响结果：① 你计划的卖房净得从一个点变成分布；② 下面那个默认关闭的开关。关闭时逐位不变。", "The house's value is a deterministic curve today: identical on every path. With this on it follows a stochastic process drawn from a separate generator. It reaches results through two doors: planned sale proceeds become a distribution rather than a point, and the switch below. Bit-identical when off."],
    "house_price.sigma_real": ["房价的年度实际波动率。这个数字没有默认自任何房价指数 —— 各地差异极大，本 App 不替你选一个。10% 是个占位数，按你所在市场的历史自行判断。", "Annual real volatility of house prices. This is not defaulted from any index: dispersion between markets is enormous and this app will not pick one for you. 10% is a placeholder; judge it against your own market's history."],
    "house_price.drift_real": ["房价的年度实际漂移，默认 0。默认为零是刻意的：开启一个模块应该让不确定性出现，而不是悄悄把中心情形挪到别处。第一版默认 1%，让一个只要求「加入不确定性」的计划中位终值涨了 24%。你相信实际增值就自己填。", "Annual real drift, zero by default. That default is deliberate: switching a module on should make the spread appear, not walk the central case somewhere else. The first version defaulted to 1% and moved median terminal wealth 24% on a plan that had only asked for uncertainty. Set it if you believe in real appreciation."],
    "house_price.include_in_net_worth": ["默认关闭，因为「自住房不计入模拟」是本 App 一直以来对你的承诺 —— 它非流动、你要住在里面。开启后房子的抽样价值会被报告，但单独列示、永不并入可支配财富：折进组合里的数字会被读成「你本可以花的钱」。", "Off by default, because \\u201chome equity is excluded from the sim\\u201d is a promise this app has always made you: it is illiquid and you live in it. With this on the drawn value is reported, but SEPARATELY and never folded into spendable wealth -- a number inside the portfolio reads as money you could have spent."],
    "ss_trust_fund.enabled": ["到今天为止，本 App 的每一份计划都把社保足额支付了五十年——那不是中性默认值，那是一个用算术说出口的立法预测。开启后，模型呈现国会不作为的机械后果：储备耗尽后只能用当年工薪税支付。这不是对立法的预测：历史上每次基金临近枯竭国会都动了手，而那件事无法预测，本模型也不试图预测。", "Until now every plan in this app has paid Social Security in full for fifty years — not a neutral default, but a legislative prediction stated as arithmetic. With this on, the model shows the mechanical consequence of Congress NOT acting: once reserves run out, benefits come from that year's payroll tax alone. This is NOT a prediction about legislation: Congress has acted every prior time a fund neared depletion, and that is precisely what cannot be forecast."],
    "ss_trust_fund.plan_start_year": ["必填，没有默认值。 引擎只认年龄，不认公历年，而信托基金枯竭是日历事件。填你这份计划的第 0 年对应哪一年（通常就是今年）。为什么不替你猜：写死一个年份会按你计划的偏移量静默错开一个联邦事件；按今天的日期算则会让同一份计划在不同年份给出不同答案，并毁掉每一次归档回放。留空 = 该模块拒绝运行，而不是悄悄用一个数。", "REQUIRED, with no default. This engine works in ages, not calendar years, and trust fund depletion is a CALENDAR event. Enter the calendar year your plan's year zero represents (usually this year). Why it is not guessed for you: a hardcoded year would mis-time a federal event by however far your plan is offset, silently; reading today's date would make the same plan answer differently in different years and break every archived replay. Left blank, the module refuses to run rather than quietly picking a number."],
    "ss_trust_fund.scenario": ["Trustees Report 发布三套方案，枯竭年份分别是 2031Q3（高成本）、2032Q4（中间）、2035Q3（低成本）。「中间方案」用报告的最佳估计；「三套方案抽样」每条路径抽一套——那是报告自己的区间，不是本 App 编的。但注意：报告不给任何方案赋概率，等概率对待是本 App 的选择。 另外，若你退休时距枯竭已久，三套方案的差别不到 1%——区间只对临近枯竭才退休的人有分量。", "The Trustees Report publishes three alternatives, with depletion in 2031 Q3 (high-cost), 2032 Q4 (intermediate) and 2035 Q3 (low-cost). \\u201cIntermediate\\u201d uses the report's best estimate; \\u201csample all three\\u201d draws one per path — that range is the report's own, not this app's invention. But note: the report attaches no probability to any alternative, so weighting them equally is THIS APP'S choice. Also, if you retire long after depletion the three differ by under 1% — the range matters most to someone retiring near it."],
    "ss_trust_fund.cola_delta_annual": ["社保的年度生活成本调整（COLA）与本计划通胀假设之间的年度差。COLA 按 CPI-W 定，而 CPI-E（面向老年人的实验性指数）通常高于 CPI-W——所以正值代表「给付跟得上得比计划假设的更好」。它与信托削减方向相反，这正是两者并存的意义：一份计划可以同时带上这两种风险，而不让其中一个掩盖另一个。默认 0，即引擎一直以来的假设。", "Annual gap between the cost-of-living adjustment Social Security actually pays and this plan's inflation assumption. COLAs are set by CPI-W, while CPI-E — the experimental elderly index — has generally run ABOVE it, so a positive value is the case where benefits keep up better than the plan assumes. It pushes the OPPOSITE way from depletion, which is the point of having both: a plan can carry the two risks without one hiding the other. Zero is what the engine has always assumed."],
    "tax_us.dividend_yield": ["应税账户每年分派出来的股息/利息占账户价值的比例（不是总回报，是其中以现金分派的那一部分）。它乘以下面的税率，得到每年对应税账户的税拖累——这就是「资产摆放」差异的来源：税前与 Roth 账户不承担它。全市场指数基金近年约 1.3%–2.0%；债券比重高的组合更高。填 0 = 你的持仓不分派，拖累为零，且这是一个测量结果而不是「没测」。", "Share of your taxable account paid out each year as dividends and interest — not total return, just the part distributed as cash. Multiplied by the rates below, it is the annual tax drag on the taxable account, and it is where asset-location differences come from: pretax and Roth accounts do not carry it. A total-market index fund has run roughly 1.3%-2.0% lately; bond-heavy portfolios are higher. Enter 0 if your holdings distribute nothing: the drag is then zero, and that is a measurement rather than a blank."],
    "tax_us.dividend_qualified_fraction": ["上面那份分派里,按合格股息/长期资本利得税率课税的比例;其余部分（利息、非合格股息、多数债券与货币基金收益）按普通收入课税,通常更贵。宽基股票基金通常接近 100%,债券与现金部分接近 0%。它只影响税率的加权,不影响分派的多少。", "Of that distribution, the share taxed at qualified-dividend / long-term capital-gains rates. The rest — interest, non-qualified dividends, most bond and money-market income — is ordinary income and usually costs more. Broad stock funds sit near 100%; bonds and cash near 0%. This weights the rate only; it does not change how much is distributed."],
    "tax_us.dividend_tax_rate": ["合格部分适用的税率,用于「真实逐年税表」关闭时的平率近似。开启真税后,这个数字只在退休第一年使用——从第二年起,合格部分按真实的 0%/15%/20% 分档叠在你上一年的普通收入之上计算,非合格部分按你当年的普通税档,所以低收入年份可能真的落在 0% 档、完全不付这笔拖累。这是单一写死数字表达不了的情形。", "Rate applied to the qualified share, used as the flat approximation while True year-by-year taxes is off. With it on, this number is used only for the first retirement year: from the second, the qualified share goes through the real 0%/15%/20% brackets stacked on last year's ordinary income and the rest through your ordinary bracket — so a low-income year can genuinely land in the 0% bracket and pay no drag at all. That is the case a single hardcoded number could not express."],
    "tax_us.withdrawal_tax_traditional": ["本土退休期从税前账户（401k/传统 IRA）提取时的税率。这些提取按普通收入课税，通常高于资本利得税率。", "Tax rate on withdrawals from pretax accounts (401k / traditional IRA) while at home. These are taxed as ordinary income, usually above capital-gains rates."],
    "layoff.bad_year_multiplier": ["市场坏年（收益低于阈值）概率乘以该倍数。2008 式年份裁员率约为平时 3 倍。", "Multiplier applied in bad market years. 2008-style years saw ≈3× layoff rates."],
    "promotion.enabled": ["是否建模一次晋升带来的收入阶跃。", "Model a one-time income step-up from a promotion?"],
    "returns.stochastic_inflation": ["让通胀逐年随机波动，而非固定值。", "Let inflation vary randomly year to year instead of a fixed value."],
    "returns.model": ["默认：每条路径抽一次市场 regime 并终身固定。Markov：regime 逐年按存续概率切换（削弱「终身好/坏运」的极端）。历史块：直接从 1928–2024 真实年度序列抽连续块重演——你的计划遇上真实的 1929/1973/2008。", "Default: one regime per path, fixed for life. Markov: the regime switches annually by a persistence probability (tempers lifetime-luck extremes). Blocks: replay consecutive blocks of the real 1928–2024 annual series — your plan meets the actual 1929/1973/2008."],
    "returns.persistence": ["每年停留在当前 regime 的概率。0.85 ≈ 平均 6–7 年一段；越高越接近终身固定。", "Probability of staying in the current regime each year. 0.85 ≈ 6–7-year spells; higher ≈ lifetime-fixed."],
    "returns.inflation_ar1": ["通胀的年自相关（AR(1) 的 φ）。0 = 逐年独立；0.7–0.9 ≈ 通胀有惯性（70 年代式的持续高通胀成为可能）。", "Annual inflation autocorrelation (AR(1) φ). 0 = independent years; 0.7–0.9 ≈ persistent inflation (1970s-style episodes become possible)."],
    "returns.block_years": ["每次从历史抽取的连续年数。短块 ≈ 打散序列；5–10 年保留衰退-复苏的真实节奏。", "Consecutive years drawn per block. Short blocks shuffle history; 5–10 years preserve real crash-recovery sequencing."],
    "returns.equity_mu_shift": ["你对长期股票收益的看法。模型默认一个 regime 混合（≈9.4%）；不确定 μ 是本模型最大的风险，所以这里给「姿态」而非精确数字。", "Your view on long-run equity returns. Defaults to a regime mixture (≈9.4%). Unknown μ is the single biggest risk here — a posture, not a precise number."],
    "glide.equity_start": ["现在的股票占比，其余为债券。100% = 全股（历史长期最优但更波动）。", "Equity share now; rest is bonds. 100% = all-equity (historically best long-run, but more volatile)."],
    "glide.equity_end": ["退休末期的股票占比。低于起点 = 做「股债滑降」逐步降风险；相同 = 全程不变。", "Equity share in late retirement. Below the start = a glide path de-risking over time; same = constant."],
    "bonds.mean": ["债券的年化预期收益。例：3%。", "Bonds' expected annual return. e.g. 3%."],
    "eldercare.mode": ["赡养冲击：off 关；stochastic 随机触发；scenario 固定一次。", "Eldercare shock: off; stochastic (random); scenario (one fixed event)."],
    "inheritance.mode": ["继承：off/随机/固定情景。基线假设为 $0。", "Inheritance: off / stochastic / scenario. Baseline assumes $0."],
    "guaranteed_income.mode": ["把你<b>自己拿到的报价</b>编译成现金流：年金（SPIA）是交一笔保费换一份终身收入，TIPS 阶梯是买一串到期日换一段定期收入。开启后保费会在购买那年真的从账户扣走，收入按你填的条款发放。<b>本仓库不内置任何报价表</b> —— 年金的定价取决于你的年龄、性别、州、公司和当天利率，我们给不出比你自己那张报价单更准的数；一律你自己填。", "Compiles the quotes <b>you were actually given</b> into cash flows: an annuity (SPIA) trades a lump sum for lifetime income, a TIPS ladder buys a run of maturities for a fixed term of income. With it on, the premium really leaves the account in the purchase year and the income is paid on the terms you entered. <b>No quote table ships in this repository</b> — annuity pricing depends on your age, sex, state, insurer and the rates that day, and we cannot beat the quote sheet in your hand. You supply all of it."],
    "parents.mode": ["把「赡养冲击」与「继承」合并成一位父母、一次死亡：护理只发生在他生前、遗产落在他去世那年。开启后会<b>取代</b>上面那两项，三者同开会被拒绝（同一次衰老付两次钱、同一次死亡继承两遍）。随机=抽死亡年龄与是否需要护理；情景=你直接说定。", "Merges the eldercare shock and the inheritance draw into one parent with one death: care happens only while they are alive, and the bequest lands in the year they die. Turning it on <b>replaces</b> those two — having all three on is refused, because it would pay for one decline twice and inherit from one death twice. Stochastic draws the death age and whether care is needed; scenario lets you state both."],
    "parents.estate_share_of_care": ["父母自己的积蓄先付多少护理费，剩下的才落到你头上。1 = 先花光他们的钱；0 = 你全付、遗产原封不动。<b>注意这不改变你的净得失</b> —— 无论取值多少，你的净头寸都是「遗产减护理费」；它改的是钱什么时候动（护理逐年出、遗产一次进），以及护理超过遗产时超出部分总归落到你头上。", "How much of the care bill your parent's own savings cover before yours do. 1 = theirs is spent first; 0 = you pay it all and inherit the estate untouched. <b>This does not change what you end up with</b> — your net position is the estate minus the care at every setting. What it changes is when the money moves (care leaves year by year, a bequest arrives at once) and that care beyond the estate always falls to you."],
    "parents.cost_excess_inflation": ["父母护理费<b>超出</b>一般通胀的年增速。1% 表示到他 83 岁时，护理费约是今天的 1.7 倍。与你自己的长期护理模块用同一组默认值。", "How fast your parent's care costs rise <b>above</b> general inflation. 1% means care at 83 costs about 1.7x today's figure. Shares its defaults with your own long-term-care module."],
    "parents.scenario_death_age": ["情景模式下父母去世的年龄，你直接指定而不是抽取。遗产就在这一年到账。", "The age your parent dies in scenario mode — stated by you rather than drawn. The bequest arrives that year."],
    "parents.scenario_care_years": ["情景模式下的护理年数。它会被死亡年龄截断：说 5 年但去世年龄只留得下 2 年，就只算 2 年，并如实告诉你被截断了。", "Years of care in scenario mode. It is truncated by the death age: say 5 years when only 2 fit before they die and only 2 are charged, and the result says it was cut short."],
    "parents.scenario_care_level": ["情景模式下的护理级别，决定年成本。居家最低、养老院最高；三档的默认成本与你自己的长期护理模块相同，都可改。", "Care level in scenario mode, which sets the annual cost. Home care is cheapest and a nursing home dearest; all three default to the same figures as your own long-term-care module and all are editable."],
    "obbba.mode": ["OBBBA 税法情景：2028 sunset / 永久 / 关。", "OBBBA tax provisions: 2028 sunset / permanent / off."],
    "sh_property.enabled": ["是否在目的地购房：一次性大额支出 + 之后降低生活成本。", "Buy property at the destination: one-off outflow + lower ongoing cost."],
    "rule.upper_guardrail": ["Guyton-Klinger 上护栏：提取率高出初始过多时下调消费。默认 20%。", "GK upper guardrail: cut spending if the withdrawal rate runs too far above initial. Default 20%."],
    "rule.lower_guardrail": ["GK 下护栏：提取率低于初始过多时上调消费。默认 20%。", "GK lower guardrail: raise spending if the rate runs too far below initial. Default 20%."],
    "rule.adjustment_pct": ["触发护栏时消费调整的幅度。默认 10%。", "How much spending is adjusted when a guardrail triggers. Default 10%."],
    "mortality.enabled": ["是否建模死亡率（路径可能在退休期结束）。", "Model mortality (paths can end during retirement)?"],
    "mortality.sex": ["用哪张死亡率表。女性预期寿命更长，退休期更可能更长。", "Which mortality table. Females live longer on average, so retirement tends to run longer."],
    "returns.expense_ratio": ["你所有基金的加权费率 + 任何顾问费，按年计，同时拖累积累期和退休期。指数基金约 0.03–0.10%；含顾问约 +1%。", "Weighted expense ratio of your funds + any advisor fee, annual — drags both accumulation and retirement. Index funds ≈0.03–0.10%; with an advisor ≈+1%."],
    "returns.rebalance_cost": ["再平衡的换手/税成本（额外年拖累）。模型已假设每年再平衡到目标配置；全在免税账户里约 0，大额应税账户实现收益约 0.1–0.3%。", "Turnover/tax cost of rebalancing (extra annual drag). The model already rebalances to target yearly; ≈0 if all tax-advantaged, ≈0.1–0.3% if a large taxable account realizes gains."],
    "state.spending_decline": ["退休消费的年实际递减率（消费「微笑」曲线）。真实退休者约每年实际 −1%（go-go→slow-go→no-go）。0 = 恒定实际值（保守）。", "Real annual decline of retirement spending (the 'smile'). Real retirees spend ≈−1%/yr (go-go→slow-go→no-go). 0 = flat real (conservative)."],
    "tax_us.progressive": ["开启后，税前提取按累进有效税率（税档+标准扣除）计税，随提取额变化；关闭则用上面的固定税率。", "When on, pretax withdrawals use a size-aware effective rate (brackets + standard deduction) instead of the flat rate above."],
    "tax_us.state_rate": ["累进模式下叠加的州税（联邦有效税率之上）。无州所得税填 0。", "State tax added on top of the federal effective rate in progressive mode. 0 if no state income tax."],
    "tax_us.std_deduction": ["累进模式下的标准扣除（今日美元），先从应税收入里减掉。", "Standard deduction (today's $) subtracted from taxable income first, in progressive mode."],
    "household.enabled": ["开启后把配偶作为第二收入方计入积累，并在退休期做联合死亡率、遗属支出、遗属社保与联合报税。若一方在 FIRE 前去世，死亡当年的缴款保留，此后停止该方的工资相关缴款并重算 FIRE；FIRE 前家庭开销仍按全额只扣一次。关闭=单人。", "Adds a spouse as a second earner in accumulation, and joint mortality / survivor spending / survivor SS / joint filing in retirement. If one member dies before FIRE, that death year's contributions remain, later wage-related contributions stop, and FIRE is recalculated; the full pre-FIRE household expense is still charged exactly once. Off = single."],
    "household.spouse_base_salary_pre": ["配偶税前基础年薪。引擎把配偶作为独立的第二收入方：各自缴款上限、各自匹配、剩余进家庭应税。", "Spouse's pre-tax base salary. The engine models a true second earner: own limits, own match, residual to household taxable."],
    "household.spouse_sex": ["配偶用哪张死亡率表——夫妻模式按联合寿命模拟，路径持续到第二人身故。", "Spouse's mortality table — couple mode simulates joint longevity; a path runs until the second death."],
    "household.spouse_initial_pretax": ["配偶名下的税前退休账户余额。与你的账户合并入家庭组合，提取时同规则。", "Spouse's pretax retirement balance. Merged into the household stack; same withdrawal rules."],
    "household.spouse_age_offset": ["配偶与你的年龄差（配偶更小填负数）。影响联合死亡率与退休期长度。", "Spouse's age minus yours (negative = younger). Affects joint mortality and horizon length."],
    "household.spouse_match_rate": ["配偶的雇主匹配率（按其基础薪资）。", "Spouse's employer match rate (on their base salary)."],
    "household.spouse_pia_monthly_y0": ["配偶在 FRA 的社保月额（今日$）。丧偶后遗属领两者中较高的一份。", "Spouse's SS at FRA (today's $/mo). After a death, the survivor keeps the higher of the two benefits."],
    "household.survivor_spending_frac": ["仅用于退休期：第一位身故后，退休支出降到原来的这个比例（经验约 0.65–0.75）；FIRE 前家庭开销不会因此下调。", "Retirement only: after the first death, retirement spending drops to this fraction (empirically ≈0.65–0.75); pre-FIRE household spending is not reduced by this setting."],
    "name": ["给这份计划起个名字（仅显示用）。", "A label for this plan (display only)."],
    "promotion.base_salary_post": ["晋升后的基础年薪。", "Base salary after the promotion."],
    "promotion.timing_min": ["晋升最早发生在第几年。", "Earliest year the promotion happens."],
    "promotion.timing_max": ["晋升最晚发生在第几年（在区间内随机）。", "Latest year (random within the window)."],
    "promotion.bonus_pct_min": ["晋升后奖金占薪资的下限。", "Lower bound of post-promo bonus, as % of salary."],
    "promotion.bonus_pct_max": ["晋升后奖金占薪资的上限。", "Upper bound of post-promo bonus %."],
    "promotion.marginal_tax_post": ["晋升后的边际税率。", "Marginal tax rate after the promotion."],
    "returns.return_df": ["Student-t 的自由度，越小尾部越厚（极端更频繁）。典型 5–8。", "Student-t degrees of freedom; smaller = fatter tails. Typical 5–8."],
    "returns.inflation_sigma": ["通胀逐年波动的标准差（需先开启随机通胀）。", "Std-dev of year-to-year inflation (needs stochastic inflation on)."],
    "returns.inflation_equity_corr": ["通胀与股票收益的相关性（通常略负）。", "Correlation between inflation and equity returns (usually slightly negative)."],
    "returns.friction_retire": ["退休期年化费用/摩擦（费率、税拖累等）。例：0.5%。", "Annualized retirement-phase fees/friction (expense ratios, tax drag). e.g. 0.5%."],
    "bonds.sigma": ["债券收益的年化波动。", "Bonds' annualized volatility."],
    "medical.non_medical_y0": ["退休后的非医疗基础支出（若与总支出分列）。", "Non-medical baseline spending in retirement (if itemized)."],
    "medical.routine_y0": ["常规/自付医疗年支出。", "Routine / out-of-pocket medical spending per year."],
    "medical.premium_working": ["在职期间的医保年保费。", "Annual health premium while still working."],
    "medical.premium_medicare": ["65 岁后 Medicare 相关年支出。", "Annual Medicare-related cost after 65."],
    "medical.annual_trajectory_enabled": ["可选：逐年把常规医疗、自付额与保费按每条路径已实现的 CPI 加各自确定性差额重算，并作为不随 GK、退休支出微笑或遗属比例缩放的家庭现金需要。65 岁前使用 ACA 全价锚点并减既有补贴；到 Medicare 年龄后改用单一家庭 Medicare 锚点。搬迁后改走目的地医疗，不再叠加美国医疗。若另外填了「ACA 保费 · 末年报价」，65 岁前的保费还会按你自己两个报价之间的年龄倍数逐年走。不是随机医疗通胀、逐人 Medicare 或 EOL 峰。", "Optional: rebuild routine care, out-of-pocket cost, and premiums each year from each path's realized CPI plus deterministic spreads, as a household cash need not scaled by GK, the spending smile, or survivor spending. Before Medicare age it uses the full-price ACA anchor minus the existing subsidy; at Medicare age it switches to one household Medicare anchor. After relocation, destination healthcare replaces the US basket. If you also fill in the ACA final-year quote, the pre-Medicare premium additionally follows the age multiple between your own two quotes. This is not stochastic medical inflation, per-person Medicare, or an end-of-life peak."],
    "medical.cpi_delta_routine": ["常规医疗相对每条路径一般 CPI 的确定性年差额；0 表示只随一般 CPI。", "Deterministic annual spread over each path's general CPI for routine care; 0 means general CPI only."],
    "medical.cpi_delta_premium": ["保费相对每条路径一般 CPI 的确定性年差额；0 表示只随一般 CPI。", "Deterministic annual spread over each path's general CPI for premiums; 0 means general CPI only."],
    "medical.cpi_delta_oop": ["自付额相对每条路径一般 CPI 的确定性年差额；0 表示只随一般 CPI。", "Deterministic annual spread over each path's general CPI for out-of-pocket cost; 0 means general CPI only."],
    "medical.oop_y0": ["自付医疗年度家庭合计，今日美元。", "Annual household-total out-of-pocket medical cost, today's dollars."],
    "aca.cap_pct_ira": ["ACA 保费占收入的上限（MAGI 联动的封顶比例）。8.5% 是 2021–2025 IRA 历史反事实，不是 2026 官方值。", "ACA premium cap as a % of income (MAGI-linked). 8.5% is a 2021–2025 historical IRA counterfactual, not a current 2026 official value."],
    "eldercare.scenario_age": ["情景模式下赡养冲击发生的年龄。", "Age the eldercare shock hits (scenario mode)."],
    "eldercare.scenario_amount": ["赡养冲击的一次性金额（今日美元）。", "One-off eldercare cost, today's $."],
    "inheritance.scenario_amount": ["情景继承到账金额（今日美元）。基线为 $0。", "Scenario inheritance amount, today's $. Baseline is $0."],
    "sh_property.purchase_amount_y0": ["购房一次性金额（今日美元）。", "One-off property purchase amount, today's $."],
    "sh_property.col_reduction": ["购房后生活成本的下调比例。", "Cost-of-living reduction after buying property."],
    "tax_us.withdrawal_tax_taxable": ["本土应税账户提取的有效税率（资本利得）。", "Home effective tax on taxable-account withdrawals (cap gains)."],
  };
  function helpIcon(hp) {
    return `<span class="help-i" tabindex="0" role="note" aria-label="${tt("说明", "info")}">i<span class="help-pop">${hp[L === "zh" ? 0 : 1]}</span></span>`;
  }

  // =========================================================== state + helpers
  const state = {
    view: "welcome", config: null, presets: {}, step: 0, data: null,
    rulePack: null, rulePackDefaults: null,
    paths: 10000, fanUnit: "real", termUnit: "real", page: "overview",
    job: null, poll: null, od: { sens: null, swr: null, claim: null, bt: null },
    slots: { A: null, B: null }, solving: false, seed: 96000, revision: 0,
    localPlanId: null, archiveRef: null, archiveConfigJson: null,
  };
  function newArchiveRequestId() {
    if (!globalThis.crypto || typeof globalThis.crypto.getRandomValues !== "function") {
      throw new Error("secure archive request id unavailable");
    }
    const bytes = new Uint8Array(16);
    globalThis.crypto.getRandomValues(bytes);
    return "req_" + Array.from(bytes, b => b.toString(16).padStart(2, "0")).join("");
  }
  const CV = (n, f) => (getComputedStyle(document.documentElement).getPropertyValue(n).trim() || f);
  const get = (o, p) => p.split(".").reduce((a, k) => (a == null ? undefined : a[k]), o);
  const esc = v => String(v == null ? "" : v).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
  function set(o, p, v) {
    const ks = p.split("."); let a = o;
    for (let i = 0; i < ks.length - 1; i++) { if (a[ks[i]] == null || typeof a[ks[i]] !== "object") a[ks[i]] = {}; a = a[ks[i]]; }
    a[ks[ks.length - 1]] = v;
  }
  function clearMedicalPremiumProvenance() {
    const provenance = get(state.config, "meta.input_provenance");
    if (!provenance || typeof provenance !== "object") return;
    delete provenance.medical_premium_aca;
  }
  function medicalPremiumIsUserQuote() {
    return get(state.config, "meta.input_provenance.medical_premium_aca") === "user_quote";
  }
  function setMedicalPremiumUserQuote(confirmed) {
    if (confirmed) set(state.config, "meta.input_provenance.medical_premium_aca", "user_quote");
    else clearMedicalPremiumProvenance();
  }
  // The second ACA quote gets its own provenance key rather than sharing the
  // first one's. They are two separate quotes taken at two separate ages, and
  // one confirmation standing in for both would let a stale second anchor
  // inherit the first one's "yes, this is mine".
  function medicalAgeEndIsUserQuote() {
    return get(state.config, "meta.input_provenance.medical_premium_aca_age_end") === "user_quote";
  }
  function setMedicalAgeEndUserQuote(confirmed) {
    if (confirmed) {
      set(state.config, "meta.input_provenance.medical_premium_aca_age_end", "user_quote");
      return;
    }
    const provenance = get(state.config, "meta.input_provenance");
    if (!provenance || typeof provenance !== "object") return;
    delete provenance.medical_premium_aca_age_end;
  }
  const INCOME_OWNER_OPTIONS = [
    ["unspecified", ["未确认归属", "Unconfirmed ownership"]],
    ["household", ["家庭共同", "Household / shared"]],
    ["primary", ["你", "You"]],
    ["spouse", ["配偶", "Spouse"]],
  ];
  function incomeOwnerLabel(v) {
    const opt = INCOME_OWNER_OPTIONS.find(([value]) => value === v);
    return opt ? opt[1][L === "zh" ? 0 : 1] : tt("无效归属", "Invalid ownership");
  }

  // =========================================================== wizard schema
  const STEPS = [
    { id: "basics", title: ["基本与时间", "Basics & timing"], kicker: ["年龄、退休期与支出目标——决定 FI 门槛（支出 ÷ SWR）。", "Ages, horizon and spending — these set the FI number (spend ÷ SWR)."], fields: [
      { p: "household.enabled", label: ["为谁规划", "Planning for"], type: "select", bool: true,
        options: [["false", ["单人", "Just me"]], ["true", ["夫妻共同", "Me + spouse"]]] },
      { p: "name", label: ["计划名", "Plan name"], type: "text" },
      { p: "household.spouse_age_offset", label: ["配偶年龄差（更小填负数）", "Spouse age offset (younger = negative)"], type: "num", min: -20, max: 20, showIf: c => get(c, "household.enabled") },
      { p: "mortality.sex", label: ["你的性别（死亡率表）", "Your sex (mortality)"], type: "select", options: [["male", ["男", "Male"]], ["female", ["女", "Female"]]], showIf: c => get(c, "household.enabled") },
      { p: "household.spouse_sex", label: ["配偶性别（死亡率表）", "Spouse sex (mortality)"], type: "select", options: [["female", ["女", "Female"]], ["male", ["男", "Male"]]], showIf: c => get(c, "household.enabled") },
      { p: "state.start_age", label: ["当前年龄", "Current age"], type: "num", min: 18, max: 70 },
      { p: "state.accum_years", label: ["最长工作年数", "Max work years"], type: "num", min: 5, max: 45 },
      { p: "state.retire_horizon", label: ["退休期年数", "Retirement horizon (yrs)"], type: "num", min: 20, max: 70 },
      { p: "state.expenses_y0", label: ["退休年支出（今日 $）", "Retirement spend (today $)"], type: "num", money: true, min: 1000 },
      { p: "state.swr_pref", label: ["安全提取率 SWR", "Safe withdrawal rate"], type: "num", pct: true, step: 0.01, min: 0.5, max: 10 },
      { p: "state.inflation", label: ["通胀", "Inflation"], type: "num", pct: true, step: 0.1, min: 0, max: 10 },
      { p: "milestones.0", label: ["里程碑 1", "Milestone 1"], type: "num", money: true, min: 1000 },
      { p: "milestones.1", label: ["里程碑 2", "Milestone 2"], type: "num", money: true, min: 1000 },
    ]},
    { id: "portfolio", title: ["当前持仓", "Portfolio today"], custom: "csvimport", kicker: ["按账户类型填今日余额——提取时税务处理不同。", "Today's balances by account type — taxed differently on withdrawal."], fields: [
      { p: "initial.pretax_401k", label: ["税前 401k/IRA", "Pretax 401k/IRA"], type: "num", money: true },
      { p: "household.spouse_initial_pretax", label: ["配偶 · 税前 401k/IRA", "Spouse · pretax"], type: "num", money: true, showIf: c => get(c, "household.enabled") },
      { p: "initial.roth_ira", label: ["Roth IRA", "Roth IRA"], type: "num", money: true },
      { p: "household.spouse_initial_roth", label: ["配偶 · Roth IRA", "Spouse · Roth IRA"], type: "num", money: true, showIf: c => get(c, "household.enabled") },
      { p: "initial.hsa", label: ["HSA", "HSA"], type: "num", money: true },
      { p: "household.spouse_initial_hsa", label: ["配偶 · HSA", "Spouse · HSA"], type: "num", money: true, showIf: c => get(c, "household.enabled") },
      { p: "initial.taxable", label: ["应税账户", "Taxable brokerage"], type: "num", money: true },
      { p: "household.spouse_initial_taxable", label: ["配偶 · 应税账户", "Spouse · taxable"], type: "num", money: true, showIf: c => get(c, "household.enabled") },
      { p: "other_assets.cash", label: ["现金 / 活期存款", "Cash / savings"], type: "num", money: true },
      { p: "other_assets.other_liquid", label: ["其他流动资产（加密/他券商）", "Other liquid (crypto/other brokerage)"], type: "num", money: true },
      { p: "other_assets.home_equity", label: ["自住房净值（不计入模拟）", "Home equity (excluded from sim)"], type: "num", money: true },
      { p: "other_assets.sell_home_enabled", label: ["计划某年出售房产变现", "Plan to sell the home"], type: "check", showIf: c => (+get(c, "other_assets.home_equity") || 0) > 0 },
      { p: "other_assets.sell_home_age", label: ["出售年龄", "Sale age"], type: "num", min: 30, max: 95, showIf: c => get(c, "other_assets.sell_home_enabled") },
      { p: "other_assets.sell_home_net_real", label: ["净得（今日 $，税费后）", "Net proceeds (today $, after costs)"], type: "num", money: true, showIf: c => get(c, "other_assets.sell_home_enabled") },
    ]},
    { id: "income", title: ["收入与储蓄", "Income & savings"], kicker: ["额外收入请填今日美元、税后可花现金：退休后先覆盖年度开销，剩余才进应税账户。它们不直接进入 MAGI/ACA/IRMAA，结果可能高估 ACA 补贴并低估税与 IRMAA。", "Enter extra income as today's-dollar, after-tax spendable cash: in retirement it covers annual spending first and only the surplus enters taxable. It does not directly enter MAGI/ACA/IRMAA, so results may overstate ACA subsidies and understate tax and IRMAA."], fields: [
      { p: "contributions.base_salary_pre", label: ["基础薪资", "Base salary"], type: "num", money: true },
      { p: "contributions.bonus_pre", label: ["年终奖", "Bonus"], type: "num", money: true },
      { p: "contributions.ot_income_pre", label: ["加班收入", "Overtime income"], type: "num", money: true },
      { p: "contributions.salary_growth_pre", label: ["薪资增长", "Salary growth"], type: "num", pct: true, step: 0.1 },
      { p: "contributions.pretax_401k_limit_y1", label: ["401k 缴款上限", "401k limit"], type: "num", money: true },
      { p: "contributions.roth_ira_limit_y1", label: ["Roth IRA 上限", "Roth IRA limit"], type: "num", money: true },
      { p: "contributions.hsa_limit_y1", label: ["HSA 上限", "HSA limit"], type: "num", money: true },
      { p: "contributions.match_rate", label: ["雇主匹配率", "Employer match rate"], type: "num", pct: true, step: 0.5 },
      { p: "contributions.match_excludes_bonus", label: ["匹配不含年终奖", "Match excludes bonus"], type: "check" },
      { p: "contributions.marginal_tax_pre", label: ["边际税率", "Marginal tax rate"], type: "num", pct: true, step: 1 },
      { p: "contributions.annual_spending_now", label: ["当前年生活开销（今日$，家庭合计）", "Current annual spending (today $, household)"], type: "num", money: true },
      { p: "household.spouse_base_salary_pre", label: ["配偶 · 基础薪资", "Spouse · base salary"], type: "num", money: true, showIf: c => get(c, "household.enabled") },
      { p: "household.spouse_bonus_pre", label: ["配偶 · 年终奖", "Spouse · bonus"], type: "num", money: true, showIf: c => get(c, "household.enabled") },
      { p: "household.spouse_salary_growth_pre", label: ["配偶 · 薪资增长", "Spouse · salary growth"], type: "num", pct: true, step: 0.1, showIf: c => get(c, "household.enabled") },
      { p: "household.spouse_pretax_401k_limit_y1", label: ["配偶 · 401k 缴款", "Spouse · 401k contribution"], type: "num", money: true, showIf: c => get(c, "household.enabled") },
      { p: "household.spouse_roth_ira_limit_y1", label: ["配偶 · Roth IRA", "Spouse · Roth IRA"], type: "num", money: true, showIf: c => get(c, "household.enabled") },
      { p: "household.spouse_hsa_limit_y1", label: ["配偶 · HSA", "Spouse · HSA"], type: "num", money: true, showIf: c => get(c, "household.enabled") },
      { p: "household.spouse_match_rate", label: ["配偶 · 雇主匹配率", "Spouse · match rate"], type: "num", pct: true, step: 0.5, showIf: c => get(c, "household.enabled") },
      { p: "household.spouse_marginal_tax_pre", label: ["配偶 · 边际税率", "Spouse · marginal tax"], type: "num", pct: true, step: 1, showIf: c => get(c, "household.enabled") },
      { p: "income_streams.pension_enabled", label: ["有养老金/年金", "Pension / annuity"], type: "check" },
      { p: "income_streams.pension_owner", label: ["归属成员（年龄仍按你的年龄轴）", "Owner (ages stay on your timeline)"], type: "select", options: INCOME_OWNER_OPTIONS, showIf: c => get(c, "income_streams.pension_enabled") },
      { p: "income_streams.pension_annual_real", label: ["年金年额（今日$）", "Pension per year (today $)"], type: "num", money: true, showIf: c => get(c, "income_streams.pension_enabled") },
      { p: "income_streams.pension_start_age", label: ["起领年龄", "Pension start age"], type: "num", min: 40, max: 75, showIf: c => get(c, "income_streams.pension_enabled") },
      { p: "income_streams.pension_cola", label: ["随通胀调整（COLA）", "Inflation-adjusted (COLA)"], type: "check", showIf: c => get(c, "income_streams.pension_enabled") },
      { p: "income_streams.rental_enabled", label: ["有出租房净收入", "Rental net income"], type: "check" },
      { p: "income_streams.rental_owner", label: ["归属成员（年龄仍按你的年龄轴）", "Owner (ages stay on your timeline)"], type: "select", options: INCOME_OWNER_OPTIONS, showIf: c => get(c, "income_streams.rental_enabled") },
      { p: "income_streams.rental_annual_net_real", label: ["年净租金（今日$）", "Net rent per year (today $)"], type: "num", money: true, showIf: c => get(c, "income_streams.rental_enabled") },
      { p: "income_streams.rental_start_age", label: ["起始年龄", "From age"], type: "num", min: 20, max: 80, showIf: c => get(c, "income_streams.rental_enabled") },
      { p: "income_streams.rental_end_age", label: ["结束年龄（出售/停租）", "To age (sell/stop)"], type: "num", min: 30, max: 100, showIf: c => get(c, "income_streams.rental_enabled") },
      { p: "income_streams.parttime_enabled", label: ["退休后兼职（Barista FIRE）", "Part-time after FIRE (Barista)"], type: "check" },
      { p: "income_streams.parttime_owner", label: ["归属成员（年龄仍按你的年龄轴）", "Owner (ages stay on your timeline)"], type: "select", options: INCOME_OWNER_OPTIONS, showIf: c => get(c, "income_streams.parttime_enabled") },
      { p: "income_streams.parttime_annual_real", label: ["兼职年收入（今日$）", "Part-time per year (today $)"], type: "num", money: true, showIf: c => get(c, "income_streams.parttime_enabled") },
      { p: "income_streams.parttime_start_age", label: ["最早开始年龄（不会早于实际 FIRE 后一年）", "Earliest start (never before the year after actual FIRE)"], type: "num", min: 30, max: 70, showIf: c => get(c, "income_streams.parttime_enabled") },
      { p: "income_streams.parttime_years", label: ["持续年数", "Years"], type: "num", min: 1, max: 30, showIf: c => get(c, "income_streams.parttime_enabled") },
      { p: "income_streams.equity_enabled", label: ["有 RSU/股权归属", "RSU / equity vesting"], type: "check" },
      { p: "income_streams.equity_owner", label: ["归属成员（年龄仍按你的年龄轴）", "Owner (ages stay on your timeline)"], type: "select", options: INCOME_OWNER_OPTIONS, showIf: c => get(c, "income_streams.equity_enabled") },
      { p: "income_streams.equity_annual_real", label: ["年归属价值（今日$）", "Vesting per year (today $)"], type: "num", money: true, showIf: c => get(c, "income_streams.equity_enabled") },
      { p: "income_streams.equity_years", label: ["归属年数（从下一模拟年起）", "Vesting years (starting next modeled year)"], type: "num", min: 1, max: 15, showIf: c => get(c, "income_streams.equity_enabled") },
    ]},
    { id: "assumptions", title: ["假设：收益/社保/医疗", "Assumptions"], custom: "ssaimport", kicker: ["收益分布、社保与医疗的关键项。更细的可在「高级」里调。", "Return model, Social Security and healthcare essentials — finer knobs live in Advanced."], fields: [
      { p: "returns.equity_mu_shift", label: ["预期收益姿态", "Expected-return posture"], type: "select", options: [
        ["-0.015", ["保守 · 混合 μ≈7.9%", "Conservative · μ≈7.9%"]],
        ["-0.0075", ["偏保守 · μ≈8.6%", "Cautious · μ≈8.6%"]],
        ["0", ["基准 · regime 混合 μ≈9.4%", "Base · regime mixture μ≈9.4%"]],
        ["0.0075", ["偏乐观 · μ≈10.1%", "Optimistic · μ≈10.1%"]],
        ["0.015", ["乐观 · μ≈10.9%", "Bullish · μ≈10.9%"]],
      ] },
      { p: "glide.equity_start", label: ["股票占比 · 现在", "Equity % · now"], type: "num", pct: true, step: 1 },
      { p: "glide.equity_end", label: ["股票占比 · 退休末期", "Equity % · late retirement"], type: "num", pct: true, step: 1 },
      { p: "returns.return_distribution", label: ["收益分布", "Return distribution"], type: "select", options: [["student_t", ["Student-t", "Student-t"]], ["normal", ["正态", "Normal"]]] },
      { p: "returns.inflation_mu", label: ["收益模型内通胀均值", "Inflation μ (returns)"], type: "num", pct: true, step: 0.1 },
      { p: "returns.expense_ratio", label: ["综合费率（基金+顾问）", "All-in fee (fund+advisor)"], type: "num", pct: true, step: 0.05 },
      { p: "state.spending_decline", label: ["退休消费年递减 (real)", "Spending decline/yr (real)"], type: "num", pct: true, step: 0.1 },
      { p: "tax_us.progressive", label: ["累进税（按提取额）", "Progressive tax (by withdrawal)"], type: "check" },
      { p: "social_security.enabled", label: ["计入社保", "Include Social Security"], type: "check" },
      { p: "social_security.pia_monthly_y0", label: ["PIA 月额（今日 $）", "PIA monthly (today $)"], type: "num", money: true, showIf: c => get(c, "social_security.enabled") },
      { p: "social_security.claim_age", label: ["领取年龄", "Claim age"], type: "num", min: 62, max: 70, showIf: c => get(c, "social_security.enabled") },
      { p: "household.spouse_pia_monthly_y0", label: ["配偶 · PIA 月额（今日$）", "Spouse · PIA monthly"], type: "num", money: true, showIf: c => get(c, "social_security.enabled") && get(c, "household.enabled") },
      { p: "household.spouse_claim_age", label: ["配偶 · 领取年龄", "Spouse · claim age"], type: "num", min: 62, max: 70, showIf: c => get(c, "social_security.enabled") && get(c, "household.enabled") },
      { p: "medical.premium_aca", label: ["ACA 保费（年）", "ACA premium (yr)"], type: "num", money: true },
      { p: "medical.medicare_age", label: ["Medicare 年龄", "Medicare age"], type: "num", min: 60, max: 70 },
      { p: "roth_ladder.enabled", label: ["启用 Roth 转换梯", "Roth conversion ladder"], type: "check" },
      { p: "roth_ladder.annual_conversion_y0", label: ["年转换额（今日 $）", "Annual conversion (today $)"], type: "num", money: true, showIf: c => get(c, "roth_ladder.enabled") },
      { p: "tax_true.enabled", label: ["真实逐年税表（RMD/IRMAA/社保应税/利得堆叠）", "True year-by-year taxes (RMD/IRMAA/SS/LTCG)"], type: "check" },
      { p: "tax_true.rmd_age", label: ["RMD 起始年龄", "RMD start age"], type: "num", min: 70, max: 80, showIf: c => get(c, "tax_true.enabled") },
      { p: "tax_true.taxable_gain_fraction", label: ["应税提取的利得占比", "Gain share of taxable withdrawals"], type: "num", pct: true, step: 5, showIf: c => get(c, "tax_true.enabled") },
      { p: "tax_true.state_rate", label: ["州税（平率）", "State tax (flat)"], type: "num", pct: true, step: 0.5, showIf: c => get(c, "tax_true.enabled") },
      { p: "tax_true.state_archetype", label: ["州税形态（原型）", "State tax shape (archetype)"], type: "select", optionalSelect: true, options: [["", ["用下面的州税平率", "Use the flat state rate below"]], ["no_income_tax", ["无州所得税", "No state income tax"]], ["flat_low", ["低平率 ≈3%", "Flat, low ≈3%"]], ["flat_moderate", ["中平率 ≈5%", "Flat, moderate ≈5%"]], ["progressive_moderate", ["累进 · 中 ≈5.5%", "Progressive, moderate ≈5.5%"]], ["progressive_high", ["累进 · 高 ≈9.3%", "Progressive, high ≈9.3%"]], ["retirement_income_exempt", ["退休收入基本免税", "Retirement income largely exempt"]], ["retirement_income_partially_exempt", ["退休收入部分免税", "Retirement income partly exempt"]], ["taxes_social_security_moderate", ["对社保征税 · 中", "Taxes Social Security, moderate"]], ["taxes_social_security_high", ["对社保征税 · 高", "Taxes Social Security, high"]], ["preferential_capital_gains", ["资本利得优惠税率", "Preferential capital-gains rate"]]], showIf: c => get(c, "tax_true.enabled") },
      { p: "tax_true.irmaa_enabled", label: ["计入 IRMAA 附加费", "Include IRMAA surcharges"], type: "check", showIf: c => get(c, "tax_true.enabled") },
      { p: "blocky_spending.enabled", label: ["支出按块状到达（大额偶发）", "Spending arrives in lumps"], type: "check" },
      { p: "blocky_spending.annual_probability", label: ["每年落一次大额的概率", "Chance of a lump each year"], type: "num", pct: true, step: 1, showIf: c => get(c, "blocky_spending.enabled") },
      { p: "blocky_spending.size_fraction", label: ["大额相当于当年支出的比例", "Lump size, as a share of that year's spending"], type: "num", pct: true, step: 5, showIf: c => get(c, "blocky_spending.enabled") },
      { p: "other_assets.sale_liquidity_discount", label: ["卖房折价（佣金/修缮/时机）", "Sale discount (commission/repairs/timing)"], type: "num", pct: true, step: 1 },
      { p: "other_assets.downsize_enabled", label: ["卖房后换一套更小的", "Buy a smaller place after selling"], type: "check" },
      { p: "other_assets.downsize_new_price_real", label: ["新住处价格（今日 $）", "New home price (today $)"], type: "num", money: true, showIf: c => get(c, "other_assets.downsize_enabled") },
      { p: "human_capital.enabled", label: ["职业路径按随机过程建模", "Model the career path as a process"], type: "check" },
      { p: "human_capital.permanent_sigma", label: ["持久工资冲击（带着走的）", "Permanent wage shock (carried)"], type: "num", pct: true, step: 1, showIf: c => get(c, "human_capital.enabled") },
      { p: "human_capital.transitory_sigma", label: ["暂时工资冲击（缓得过来的）", "Transitory wage shock (recovered)"], type: "num", pct: true, step: 1, showIf: c => get(c, "human_capital.enabled") },
      { p: "layoff.gap_months_per_year_of_age", label: ["找工作时长随龄增加（月/岁）", "Extra search months per year of age"], type: "num", step: 0.5, showIf: c => get(c, "layoff.enabled") },
      { p: "layoff.decay_from_age", label: ["从几岁开始变难", "Age it starts getting harder"], type: "num", min: 30, max: 70, showIf: c => get(c, "layoff.enabled") },
      { p: "bonds.correlation_with_equity", label: ["股债相关性", "Equity/bond correlation"], type: "num", pct: true, step: 5 },
      { p: "house_price.enabled", label: ["房价按随机过程建模", "Model the house price as a process"], type: "check" },
      { p: "house_price.sigma_real", label: ["房价年度实际波动率", "Annual real house-price volatility"], type: "num", pct: true, step: 1, showIf: c => get(c, "house_price.enabled") },
      { p: "house_price.drift_real", label: ["房价年度实际漂移", "Annual real house-price drift"], type: "num", pct: true, step: 0.5, showIf: c => get(c, "house_price.enabled") },
      { p: "house_price.include_in_net_worth", label: ["把自住房计入净资产（单独列示）", "Count the home in net worth (shown separately)"], type: "check", showIf: c => get(c, "house_price.enabled") },
      { p: "ss_trust_fund.enabled", label: ["建模社保信托基金枯竭", "Model Social Security trust fund depletion"], type: "check" },
      { p: "ss_trust_fund.plan_start_year", label: ["本计划第 0 年对应的公历年（必填）", "Calendar year of this plan's year zero (required)"], type: "num", min: 1900, max: 2200, step: 1, optionalNum: true, showIf: c => get(c, "ss_trust_fund.enabled") },
      { p: "ss_trust_fund.scenario", label: ["用哪一套官方方案", "Which published scenario"], type: "select", options: [["intermediate", ["中间方案（报告的最佳估计）", "Intermediate (the report's best estimate)"]], ["range", ["三套方案抽样（报告自己的区间）", "Sample all three (the report's own range)"]]], showIf: c => get(c, "ss_trust_fund.enabled") },
      { p: "ss_trust_fund.cola_delta_annual", label: ["COLA 与本计划通胀的年度偏差", "Annual COLA vs. this plan's inflation"], type: "num", pct: true, step: 0.1, showIf: c => get(c, "ss_trust_fund.enabled") },
      { p: "tax_us.dividend_yield", label: ["应税账户分派率（股息/利息）", "Taxable distribution yield"], type: "num", pct: true, step: 0.1 },
      { p: "tax_us.dividend_qualified_fraction", label: ["其中合格股息占比", "Qualified share of that"], type: "num", pct: true, step: 5 },
      { p: "tax_us.dividend_tax_rate", label: ["合格部分税率（平率近似）", "Qualified rate (flat approximation)"], type: "num", pct: true, step: 1 },
    ]},
    { id: "family", title: ["家庭与人生事件", "Family & life events"], custom: "family", kicker: ["子女、教育、大额支出与继承/变现——都会编译成逐年现金流进入引擎。配偶收入与联合建模在「高级 → 家庭/配偶」。", "Children, education, big-ticket costs and windfalls — compiled into yearly cash flows for the engine. Spouse income & joint modeling live in Advanced → Household."], fields: [] },
    { id: "relocation", title: ["搬迁目的地（可选）", "Relocation (optional)"], kicker: ["想比较搬到别处？选一个目的地或自定义——会自动填入生活成本/汇率/税/医疗。", "Comparing a move? Pick a destination or go custom — it fills cost-of-living / FX / tax / healthcare."], fields: [
      { p: "relocation.enabled", label: ["建模搬迁情景", "Model a relocation"], type: "check" },
      { p: "relocation.destination", label: ["目的地", "Destination"], type: "dest", showIf: c => get(c, "relocation.enabled") },
      { p: "relocation.relocation_age", label: ["搬迁年龄", "Relocation age"], type: "num", min: 30, max: 80, showIf: c => get(c, "relocation.enabled") },
      { p: "relocation.col_ratio", label: ["生活成本比（相对本土）", "Cost-of-living ratio"], type: "num", pct: true, step: 1, showIf: c => get(c, "relocation.enabled") },
      { p: "state.inflation_cn", label: ["目的地通胀", "Destination inflation"], type: "num", pct: true, step: 0.1, showIf: c => get(c, "relocation.enabled") },
      { p: "relocation.fx_sigma", label: ["汇率波动", "FX volatility"], type: "num", pct: true, step: 0.5, showIf: c => get(c, "relocation.enabled") },
      { p: "relocation.ppp_kappa", label: ["PPP 回归速度 κ（0=随机游走）", "PPP reversion κ (0 = random walk)"], type: "num", min: 0, max: 0.5, step: 0.05, showIf: c => get(c, "relocation.enabled") },
      { p: "china_healthcare.cost_working_age_real", label: ["目的地医疗·壮年", "Dest. healthcare · working"], type: "num", money: true, showIf: c => get(c, "relocation.enabled") },
      { p: "china_healthcare.cost_senior_real", label: ["目的地医疗·老年", "Dest. healthcare · senior"], type: "num", money: true, showIf: c => get(c, "relocation.enabled") },
      { p: "ss_nra.haircut_fraction", label: ["社保海外折减", "SS abroad haircut"], type: "num", pct: true, step: 1, showIf: c => get(c, "relocation.enabled") },
      { p: "tax_cn.withdrawal_tax_traditional", label: ["目的地税前提取税", "Dest. pretax withdrawal tax"], type: "num", pct: true, step: 0.5, showIf: c => get(c, "relocation.enabled") },
    ]},
    { id: "advanced", title: ["高级（可选，全部参数）", "Advanced (optional)"], advanced: true, kicker: ["v9.8 的其余全部可调参数，按主题折叠。不改也没关系——默认即官方基线。", "Every remaining v9.8 parameter, grouped. Leave as-is — defaults are the official baseline."], groups: [
      { title: ["晋升", "Promotion"], fields: [
        { p: "promotion.enabled", label: ["启用晋升", "Enabled"], type: "check" },
        { p: "promotion.base_salary_post", label: ["晋升后薪资", "Post-promo salary"], type: "num", money: true },
        { p: "promotion.timing_min", label: ["最早（第N年）", "Earliest (yr)"], type: "num", min: 1, max: 20 },
        { p: "promotion.timing_max", label: ["最晚（第N年）", "Latest (yr)"], type: "num", min: 1, max: 20 },
        { p: "promotion.bonus_pct_min", label: ["奖金% 下限", "Bonus % min"], type: "num", pct: true, step: 1 },
        { p: "promotion.bonus_pct_max", label: ["奖金% 上限", "Bonus % max"], type: "num", pct: true, step: 1 },
        { p: "promotion.marginal_tax_post", label: ["边际税率（晋升后）", "Marginal tax (post)"], type: "num", pct: true, step: 1 },
      ]},
      { title: ["失业风险", "Layoff risk"], fields: [
        { p: "layoff.enabled", label: ["建模失业风险", "Model layoff risk"], type: "check" },
        { p: "layoff.p_annual", label: ["年失业概率", "Annual layoff prob."], type: "num", pct: true, step: 0.5 },
        { p: "layoff.return_threshold", label: ["坏年阈值（收益 ≤）", "Bad-year threshold (return ≤)"], type: "num", pct: true, step: 1 },
        { p: "layoff.bad_year_multiplier", label: ["坏年概率倍数", "Bad-year multiplier"], type: "num", min: 1, max: 10, step: 0.5 },
        { p: "layoff.gap_months", label: ["失业空窗（月）", "Gap (months)"], type: "num", min: 1, max: 12, step: 0.5 },
      ]},
      { title: ["住房（租/购/按揭）", "Housing (rent/buy/mortgage)"], fields: [
        { p: "housing.enabled", label: ["建模住房现金流", "Model housing cash flows"], type: "check" },
        { p: "housing.replace_annual", label: ["年开销中的住房预算（将被替换）", "Housing budget inside expenses (replaced)"], type: "num", money: true },
        { p: "housing.mode", label: ["住房安排", "Arrangement"], type: "select", options: [["rent", ["长期租房", "Rent long-term"]], ["buy", ["购房（先租后买）", "Buy (rent until purchase)"]]] },
        { p: "housing.monthly_rent", label: ["月租（今日$）", "Monthly rent (today's $)"], type: "num", money: true },
        { p: "housing.rent_growth_real", label: ["房租实际增速（超通胀）", "Real rent growth (above CPI)"], type: "num", pct: true, step: 0.1 },
        { p: "housing.purchase_age", label: ["购房年龄（仅购房）", "Purchase age (buy only)"], type: "num", min: 20, max: 80 },
        { p: "housing.price", label: ["房价（今日$，仅购房）", "Home price (today's $, buy only)"], type: "num", money: true },
        { p: "housing.down_pct", label: ["首付比例", "Down payment %"], type: "num", pct: true, step: 1 },
        { p: "housing.rate", label: ["按揭利率（名义）", "Mortgage rate (nominal)"], type: "num", pct: true, step: 0.125 },
        { p: "housing.term_years", label: ["贷款年限", "Term (years)"], type: "num", min: 5, max: 40 },
        { p: "housing.tax_pct", label: ["房产税率（房价%）", "Property tax (% of value)"], type: "num", pct: true, step: 0.1 },
        { p: "housing.maint_pct", label: ["维护成本率（房价%）", "Maintenance (% of value)"], type: "num", pct: true, step: 0.1 },
        { p: "housing.appreciation_real", label: ["房价实际增速", "Real appreciation"], type: "num", pct: true, step: 0.25 },
        { p: "housing.refi_enabled", label: ["计划再融资", "Plan a refinance"], type: "check" },
        { p: "housing.refi_age", label: ["再融资年龄", "Refi age"], type: "num", min: 20, max: 80 },
        { p: "housing.refi_rate", label: ["再融资利率", "Refi rate"], type: "num", pct: true, step: 0.125 },
      ]},
      { title: ["收益与通胀", "Returns & inflation"], fields: [
        { p: "returns.model", label: ["收益生成模型", "Return generator"], type: "select", options: [["iid", ["默认 · 终身固定 regime", "Default · lifetime regime"]], ["markov", ["Markov · regime 年切换", "Markov · annual regime switching"]], ["blocks", ["历史块重演 1928–2024", "Historical blocks 1928–2024"]]] },
        { p: "returns.persistence", label: ["regime 年存续概率（仅 Markov）", "Regime persistence/yr (Markov only)"], type: "num", pct: true, step: 1 },
        { p: "returns.inflation_ar1", label: ["通胀自相关 φ（仅 Markov）", "Inflation AR(1) φ (Markov only)"], type: "num", min: 0, max: 0.95, step: 0.05 },
        { p: "returns.block_years", label: ["历史块长·年（仅历史块）", "Block length, yrs (blocks only)"], type: "num", min: 1, max: 30 },
        { p: "returns.return_df", label: ["t 自由度", "t degrees of freedom"], type: "num", min: 3, max: 30, step: 0.5 },
        { p: "returns.stochastic_inflation", label: ["随机通胀", "Stochastic inflation"], type: "check" },
        { p: "returns.inflation_sigma", label: ["通胀波动", "Inflation σ"], type: "num", pct: true, step: 0.1 },
        { p: "returns.inflation_equity_corr", label: ["通胀-股票相关", "Inflation-equity corr"], type: "num", step: 0.05 },
        { p: "returns.friction_retire", label: ["退休期摩擦", "Retirement friction"], type: "num", pct: true, step: 0.1 },
        { p: "returns.rebalance_cost", label: ["再平衡成本/换手拖累", "Rebalancing/turnover cost"], type: "num", pct: true, step: 0.05 },
      ]},
      { title: ["债券与股债滑降", "Bonds & glide"], fields: [
        { p: "bonds.mean", label: ["债券均值", "Bond mean"], type: "num", pct: true, step: 0.1 },
        { p: "bonds.sigma", label: ["债券波动", "Bond σ"], type: "num", pct: true, step: 0.1 },
        { p: "glide.equity_start", label: ["股票占比·起", "Equity start"], type: "num", pct: true, step: 1 },
        { p: "glide.equity_end", label: ["股票占比·终", "Equity end"], type: "num", pct: true, step: 1 },
      ]},
      { title: ["医疗与 ACA", "Medical & ACA"], fields: [
        { p: "medical.annual_trajectory_enabled", label: ["逐年医疗轨迹", "Annual medical trajectory"], type: "check" },
        { p: "medical.premium_aca_age_end", label: ["ACA 保费 · 末年报价", "ACA premium · final-year quote"], type: "num", money: true, optionalNum: true },
        { p: "medical.household_share_primary", label: ["医疗中属于本人的比例", "Your share of household medical"], type: "num", pct: true, step: 1, optionalNum: true },
        { p: "medical.eol_peak_real", label: ["临终医疗支出峰（每人一次）", "End-of-life peak (per death)"], type: "num", money: true, optionalNum: true },
        { p: "medical.non_medical_y0", label: ["非医疗支出", "Non-medical y0"], type: "num", money: true },
        { p: "medical.routine_y0", label: ["常规医疗", "Routine"], type: "num", money: true },
        { p: "medical.premium_working", label: ["在职保费", "Working premium"], type: "num", money: true },
        { p: "medical.premium_medicare", label: ["Medicare 保费", "Medicare premium"], type: "num", money: true },
        { p: "medical.oop_y0", label: ["自付医疗", "Out of pocket"], type: "num", money: true },
        { p: "medical.cpi_delta_routine", label: ["常规医疗超额通胀", "Routine inflation spread"], type: "num", pct: true, step: 0.1 },
        { p: "medical.cpi_delta_premium", label: ["保费超额通胀", "Premium inflation spread"], type: "num", pct: true, step: 0.1 },
        { p: "medical.cpi_delta_oop", label: ["自付超额通胀", "OOP inflation spread"], type: "num", pct: true, step: 0.1 },
        { p: "aca.cap_pct_ira", label: ["MAGI 保费上限", "MAGI premium cap"], type: "num", pct: true, step: 0.1 },
      ]},
      { title: ["冲击 · Eldercare / 继承", "Shocks · eldercare / inheritance"], fields: [
        { p: "eldercare.mode", label: ["Eldercare 模式", "Eldercare mode"], type: "select", options: [["off", ["关", "off"]], ["stochastic", ["随机", "stochastic"]], ["scenario", ["情景", "scenario"]]] },
        { p: "eldercare.scenario_age", label: ["情景年龄", "Scenario age"], type: "num", min: 40, max: 90 },
        { p: "eldercare.scenario_amount", label: ["情景金额", "Scenario amount"], type: "num", money: true },
        { p: "inheritance.mode", label: ["继承模式", "Inheritance mode"], type: "select", options: [["off", ["关", "off"]], ["stochastic", ["随机", "stochastic"]], ["scenario", ["情景", "scenario"]]] },
        { p: "inheritance.scenario_amount", label: ["继承金额", "Inheritance amount"], type: "num", money: true },
      ]},
      { title: ["父母生命周期（取代上面两项）", "Parent lifecycle (replaces the two above)"], fields: [
        { p: "parents.mode", label: ["父母模块", "Parent module"], type: "select", options: [["off", ["关", "off"]], ["stochastic", ["随机", "stochastic"]], ["scenario", ["情景", "scenario"]]] },
        { p: "parents.estate_share_of_care", label: ["父母积蓄先承担的护理比例", "Share of care the estate pays first"], type: "num", step: 0.05, min: 0, max: 1 !== "off" },
        { p: "parents.cost_excess_inflation", label: ["护理费超额通胀", "Care cost excess inflation"], type: "num", pct: true, step: 0.25 !== "off" },
        { p: "parents.scenario_death_age", label: ["情景 · 父母去世年龄", "Scenario · parent dies at"], type: "num", min: 60, max: 105 },
        { p: "parents.scenario_care_years", label: ["情景 · 护理年数", "Scenario · years of care"], type: "num", step: 0.5, min: 0, max: 20 },
        { p: "parents.scenario_care_level", label: ["情景 · 护理级别", "Scenario · care level"], type: "select", options: [["home_care", ["居家护理", "Home care"]], ["assisted_living", ["辅助生活", "Assisted living"]], ["nursing_home", ["养老院", "Nursing home"]]] },
      ]},
      { title: ["保底收入（年金 / TIPS 阶梯）", "Guaranteed income (annuity / TIPS ladder)"], fields: [
        { p: "guaranteed_income.mode", label: ["保底收入模块", "Guaranteed income module"], type: "select", options: [["off", ["关", "off"]], ["on", ["开", "on"]]] },
      ]},
      { title: ["OBBBA · 房产", "OBBBA · property"], fields: [
        { p: "obbba.mode", label: ["OBBBA 模式", "OBBBA mode"], type: "select", options: [["off", ["关", "off"]], ["sunsets", ["2028 sunset", "sunsets"]], ["permanent", ["永久", "permanent"]]] },
        { p: "sh_property.enabled", label: ["购置海外房产", "Buy property abroad"], type: "check" },
        { p: "sh_property.purchase_amount_y0", label: ["购置金额", "Purchase amount"], type: "num", money: true },
        { p: "sh_property.col_reduction", label: ["生活成本下调", "CoL reduction"], type: "num", pct: true, step: 1 },
      ]},
      { title: ["税率与提取护栏", "Taxes & GK guardrails"], fields: [
        { p: "tax_us.withdrawal_tax_traditional", label: ["本土税前提取税", "Home pretax wd tax"], type: "num", pct: true, step: 0.5 },
        { p: "tax_us.withdrawal_tax_taxable", label: ["本土应税提取税", "Home taxable wd tax"], type: "num", pct: true, step: 0.5 },
        { p: "tax_us.state_rate", label: ["州税附加（累进模式）", "State add-on (progressive)"], type: "num", pct: true, step: 0.5 },
        { p: "tax_us.std_deduction", label: ["标准扣除（今日$，累进模式）", "Std deduction (today's $, progressive)"], type: "num", money: true },
        { p: "rule.upper_guardrail", label: ["GK 上护栏", "GK upper guardrail"], type: "num", pct: true, step: 1 },
        { p: "rule.lower_guardrail", label: ["GK 下护栏", "GK lower guardrail"], type: "num", pct: true, step: 1 },
        { p: "rule.adjustment_pct", label: ["GK 调整幅度", "GK adjustment"], type: "num", pct: true, step: 1 },
        { p: "rule.cut_realisation", label: ["削减的实际执行比例", "How much of a cut actually happens"], type: "num", pct: true, step: 5 },
        { p: "estate.exemption_real", label: ["遗产税豁免额（今日$，选填）", "Estate tax exemption (today $, optional)"], type: "num", money: true, optional: true },
        { p: "mortality.enabled", label: ["启用死亡率", "Mortality enabled"], type: "check" },
        { p: "mortality.sex", label: ["性别（死亡率表）", "Sex (mortality table)"], type: "select", options: [["male", ["男", "Male"]], ["female", ["女", "Female"]]] },
      ]},
      { title: ["家庭 / 配偶", "Household / spouse"], fields: [
        { p: "household.enabled", label: ["建模配偶/家庭", "Model a spouse/household"], type: "check" },
        { p: "household.spouse_age_offset", label: ["配偶年龄差（相对你）", "Spouse age offset"], type: "num", min: -20, max: 20 },
        { p: "household.spouse_base_salary_pre", label: ["配偶基础薪资", "Spouse base salary"], type: "num", money: true },
        { p: "household.spouse_bonus_pre", label: ["配偶年终奖", "Spouse bonus"], type: "num", money: true },
        { p: "household.spouse_pretax_401k_limit_y1", label: ["配偶 401k 缴款", "Spouse 401k contribution"], type: "num", money: true },
        { p: "household.spouse_roth_ira_limit_y1", label: ["配偶 Roth IRA", "Spouse Roth IRA"], type: "num", money: true },
        { p: "household.spouse_hsa_limit_y1", label: ["配偶 HSA", "Spouse HSA"], type: "num", money: true },
        { p: "household.spouse_match_rate", label: ["配偶雇主匹配率", "Spouse match rate"], type: "num", pct: true, step: 0.5 },
        { p: "household.spouse_initial_pretax", label: ["配偶税前账户余额", "Spouse pretax balance"], type: "num", money: true },
        { p: "household.spouse_initial_roth", label: ["配偶 Roth 余额", "Spouse Roth balance"], type: "num", money: true },
        { p: "household.spouse_initial_taxable", label: ["配偶应税余额", "Spouse taxable balance"], type: "num", money: true },
        { p: "household.spouse_pia_monthly_y0", label: ["配偶社保月额 PIA", "Spouse SS PIA/mo"], type: "num", money: true },
        { p: "household.spouse_claim_age", label: ["配偶社保领取年龄", "Spouse SS claim age"], type: "num", min: 62, max: 70 },
        { p: "household.spouse_sex", label: ["配偶性别（死亡率）", "Spouse sex (mortality)"], type: "select", options: [["female", ["女", "Female"]], ["male", ["男", "Male"]]] },
        { p: "household.survivor_spending_frac", label: ["丧偶后支出比例", "Survivor spending fraction"], type: "num", pct: true, step: 1 },
      ]},
    ]},
    { id: "review", title: ["复核", "Review"], custom: "review", kicker: ["提交前最后看一眼：所有输入的一页摘要，异常值会标出来。点任何一组可回去修改。", "One last look before running: a one-page recap of everything you entered, with anomalies flagged. Click any group to edit."], fields: [] },
  ];

  // =========================================================== field rendering
  function readF(f) {
    let v = get(state.config, f.p);
    if (f.type === "select" && f.bool) return String(!!v);
    // An optional quote that was never given stays BLANK. Rendering it as 0
    // would put a measured-looking zero in front of the user and, worse, make
    // "never priced" and "priced at nothing" look the same on screen.
    if (f.optionalNum && v == null) return "";
    // An optional SELECT that was never chosen renders as its blank option,
    // not as 0. Without this a null archetype rendered as the number 0, which
    // matches no option, and the control then wrote "" back -- a value the
    // preflight refuses, so the wizard could not reach the run.
    if (f.optionalSelect && v == null) return "";
    if (v == null) return f.type === "check" ? false : (f.type === "text" ? "" : 0);
    if (f.pct) v = +(v * 100).toFixed(6);
    return v;
  }
  function writeF(f, raw) {
    let v = raw;
    if (f.optionalNum) {
      // `null`, never 0. The engine reads null as "the user never gave this"
      // and leaves the whole mechanism out; a 0 would be a reading — a quote
      // saying the coverage is free, or a household whose entire medical bill
      // belongs to one member — and the adapter refuses those by name.
      let blank = (raw === "" || raw == null || !Number.isFinite(+raw));
      set(state.config, f.p,
          blank ? null : (f.pct ? +raw / 100 : +raw));
      return;
    }
    if (f.type === "check") v = !!raw;
    else if (f.type === "select" && f.bool) v = (raw === "true");
    // Blank means "not chosen", which is `null`. Writing "" would be a value
    // the engine has no archetype for, and the refusal is deliberate.
    else if (f.type === "select" && f.optionalSelect) v = (raw === "" ? null : raw);
    else if (f.type !== "text" && f.type !== "select" && f.type !== "dest") v = raw === "" ? 0 : +raw;
    if (f.pct) v = v / 100;
    set(state.config, f.p, v);
  }
  const lbl = f => f.label[L === "zh" ? 0 : 1];
  const fmtV = (f, v) => {
    if (f.type === "select" && f.options) {
      const opt = f.options.find(([value]) => String(value) === String(v));
      if (opt) return Array.isArray(opt[1]) ? opt[1][L === "zh" ? 0 : 1] : opt[1];
    }
    return f.money ? money(v) : f.pct ? (+v).toFixed(2) + "%" : v;
  };

  function fieldEl(f) {
    const w = document.createElement("div");
    w.className = "field" + (f.type === "check" ? " check" : "");
    w.dataset.path = f.p;
    if (f.type === "check") {
      const inp = document.createElement("input"); inp.type = "checkbox"; inp.id = "f_" + f.p; inp.checked = !!readF(f);
      const la = document.createElement("label"); la.htmlFor = inp.id;
      la.innerHTML = lbl(f) + (HELP[f.p] ? helpIcon(HELP[f.p]) : "");
      inp.addEventListener("change", () => { writeF(f, inp.checked); buildStep(); onWizChange(); });
      w.appendChild(inp); w.appendChild(la); return w;
    }
    const la = document.createElement("label"); la.innerHTML = `<span>${lbl(f)}${HELP[f.p] ? helpIcon(HELP[f.p]) : ""}</span>`;
    if (f.type === "num" && (f.money || f.pct)) { const s = document.createElement("span"); s.className = "val"; s.id = "v_" + f.p; s.textContent = fmtV(f, readF(f)); la.appendChild(s); }
    w.appendChild(la);
    if (f.type === "select") {
      const sel = document.createElement("select");
      f.options.forEach(([v, txt]) => { const o = document.createElement("option"); o.value = v; o.textContent = Array.isArray(txt) ? txt[L === "zh" ? 0 : 1] : txt; sel.appendChild(o); });
      const rv = readF(f);        // 0 is a valid value (e.g. posture "base") — don't fall through
      sel.value = (rv === "" || rv == null) ? f.options[0][0] : String(rv);
      sel.addEventListener("change", () => { writeF(f, sel.value); buildStep(); onWizChange(); });
      w.appendChild(sel);
    } else if (f.type === "dest") {
      const search = document.createElement("input");
      search.type = "text"; search.placeholder = tt("搜索城市/国家…", "Search city/country…");
      search.className = "dest-search";
      w.appendChild(search);
      const sel = document.createElement("select");
      REGIONS.forEach(([rid, rname]) => {
        const cities = DEST.filter(d => d.region === rid);
        if (!cities.length) return;
        const og = document.createElement("optgroup"); og.label = rname[L === "zh" ? 0 : 1];
        cities.forEach(d => { const o = document.createElement("option"); o.value = d.id; o.textContent = d.name[L === "zh" ? 0 : 1]; og.appendChild(o); });
        sel.appendChild(og);
      });
      const cu = DEST.find(d => d.custom);
      const oc = document.createElement("option"); oc.value = cu.id; oc.textContent = cu.name[L === "zh" ? 0 : 1]; sel.appendChild(oc);
      sel.value = get(state.config, "relocation.destination") || "custom";
      sel.addEventListener("change", () => { applyDest(sel.value); buildStep(); onWizChange(); });
      const rebuild = q => {
        const cur = sel.value; sel.innerHTML = "";
        REGIONS.forEach(([rid, rname]) => {
          const cities = DEST.filter(d => d.region === rid && (!q || d.name.join(" ").toLowerCase().includes(q)));
          if (!cities.length) return;
          const og = document.createElement("optgroup"); og.label = rname[L === "zh" ? 0 : 1];
          cities.forEach(d => { const o = document.createElement("option"); o.value = d.id; o.textContent = d.name[L === "zh" ? 0 : 1]; og.appendChild(o); });
          sel.appendChild(og);
        });
        const cu = DEST.find(d => d.custom);
        const oc = document.createElement("option"); oc.value = cu.id; oc.textContent = cu.name[L === "zh" ? 0 : 1]; sel.appendChild(oc);
        sel.value = [...sel.options].some(o => o.value === cur) ? cur : (sel.options[0] && sel.options[0].value) || "custom";
      };
      search.addEventListener("input", () => rebuild(search.value.trim().toLowerCase()));
      w.appendChild(sel);
      const cur = DEST.find(d => d.id === (get(state.config, "relocation.destination") || "custom"));
      const info = document.createElement("div");
      info.className = "dest-info";
      info.innerHTML = (!cur || cur.custom)
        ? tt("自定义：下方各字段手动填写。", "Custom: fill the fields below yourself.")
        // vintage shown so users know how fresh the illustrative values are
        : tt(`已填入示意默认值（均可改）：生活成本 <b>${Math.round(cur.col * 100)}%</b> · 汇率波动 <b>${(cur.fx * 100).toFixed(0)}%</b> · 通胀 <b>${(cur.infl * 100).toFixed(1)}%</b> · 医疗 <b>${money(cur.hcW)}/${money(cur.hcS)}</b> · 提取税 <b>${(cur.tax * 100).toFixed(1)}%</b> · 社保折减 <b>${Math.round(cur.hair * 100)}%</b> · 口径 ${DEST_VINTAGE}`,
             `Illustrative defaults applied (all editable): CoL <b>${Math.round(cur.col * 100)}%</b> · FX vol <b>${(cur.fx * 100).toFixed(0)}%</b> · inflation <b>${(cur.infl * 100).toFixed(1)}%</b> · healthcare <b>${money(cur.hcW)}/${money(cur.hcS)}</b> · wd tax <b>${(cur.tax * 100).toFixed(1)}%</b> · SS haircut <b>${Math.round(cur.hair * 100)}%</b> · vintage ${DEST_VINTAGE}`);
      w.appendChild(info);
    } else {
      const inp = document.createElement("input"); inp.type = f.type === "text" ? "text" : "number";
      if (f.min != null) inp.min = f.min; if (f.max != null) inp.max = f.max; if (f.step != null) inp.step = f.step;
      inp.value = readF(f);
      inp.addEventListener("input", () => {
        if (f.p === "medical.premium_aca") {
          const candidate = +inp.value;
          // A cleared control is not a measured zero. Keep the last stored
          // anchor and let the field remain visibly invalid until corrected.
          if (inp.value === "" || !Number.isFinite(candidate) || candidate <= 0) {
            inp.classList.add("invalid");
            return;
          }
          inp.classList.remove("invalid");
          writeF(f, inp.value);
          setMedicalPremiumUserQuote(true);
          const confirm = w.querySelector('[data-medical-premium-confirm]');
          if (confirm) confirm.checked = true;
        } else if (f.p === "medical.premium_aca_age_end") {
          const confirm = w.querySelector('[data-medical-age-end-confirm]');
          if (inp.value === "") {
            // Clearing it is a legitimate answer -- "I have not priced a
            // second age" -- and it turns the curve off rather than flattening
            // it. This is the one branch here that writes on a blank.
            inp.classList.remove("invalid");
            writeF(f, "");
            setMedicalAgeEndUserQuote(false);
            if (confirm) confirm.checked = false;
          } else if (!Number.isFinite(+inp.value) || +inp.value <= 0) {
            inp.classList.add("invalid");
            return;
          } else {
            inp.classList.remove("invalid");
            writeF(f, inp.value);
            setMedicalAgeEndUserQuote(true);
            if (confirm) confirm.checked = true;
          }
        } else if (f.p === "medical.household_share_primary") {
          if (inp.value === "") {
            // Blank turns the split off rather than assuming an even one.
            inp.classList.remove("invalid");
            writeF(f, "");
          } else if (!Number.isFinite(+inp.value)
                     || +inp.value < 0 || +inp.value > 100) {
            inp.classList.add("invalid");
            return;
          } else {
            inp.classList.remove("invalid");
            writeF(f, inp.value);
          }
        } else if (get(state.config, "medical.annual_trajectory_enabled")
                   && ["medical.non_medical_y0", "medical.routine_y0",
                       "medical.premium_medicare", "medical.oop_y0",
                       "medical.cpi_delta_routine", "medical.cpi_delta_premium",
                       "medical.cpi_delta_oop"].includes(f.p)
                   && (inp.value === "" || !Number.isFinite(+inp.value))) {
          // Blank is unknown, not a measured zero. Preserve the stored value
          // and keep the control visibly invalid until the user supplies one.
          inp.classList.add("invalid");
          return;
        } else {
          inp.classList.remove("invalid");
          writeF(f, inp.value);
        }
        const vv = $("v_" + f.p); if (vv) vv.textContent = fmtV(f, +inp.value || 0); onWizChange();
      });
      w.appendChild(inp);
      if (f.p === "medical.premium_aca") {
        const source = document.createElement("div");
        source.className = "dest-info";
        const confirmId = "confirm_medical_premium_aca";
        source.innerHTML = `<label for="${confirmId}"><input id="${confirmId}" data-medical-premium-confirm type="checkbox" ${medicalPremiumIsUserQuote() ? "checked" : ""}> ${tt("这是我按当前地点与年龄取得的家庭年度全价报价", "This is my annual household full-price quote for my current location and age")}</label><div>${tt("查询指引（可复制）：", "Quote guide (copyable):")} <code>https://www.healthcare.gov/see-plans/</code><br>${tt("每次开放投保期后请重新查询并更新；地点、年龄或覆盖家庭变化时也请更新。", "Recheck and update after every Open Enrollment period, and whenever your location, age, or covered household changes.")}<br>${tt("App 不会联网获取或核验此报价；取消确认会恢复为未确认的旧值/代理值，不会写成 $0。", "The App does not fetch or verify this quote. Clearing confirmation restores unconfirmed legacy/proxy status; it never writes $0.")}</div>`;
        const confirm = source.querySelector("[data-medical-premium-confirm]");
        confirm.addEventListener("change", () => {
          if (confirm.checked) {
            const value = +get(state.config, "medical.premium_aca");
            if (!Number.isFinite(value) || value <= 0) {
              confirm.checked = false;
              inp.classList.add("invalid");
              return;
            }
          }
          setMedicalPremiumUserQuote(confirm.checked);
          onWizChange();
        });
        w.appendChild(source);
      }
      if (f.p === "medical.premium_aca_age_end") {
        const source = document.createElement("div");
        source.className = "dest-info";
        const confirmId = "confirm_medical_premium_aca_age_end";
        const lastBridgeAge = (+get(state.config, "medical.medicare_age") || 65) - 1;
        source.innerHTML = `<label for="${confirmId}"><input id="${confirmId}" data-medical-age-end-confirm type="checkbox" ${medicalAgeEndIsUserQuote() ? "checked" : ""}> ${tt(`这是我把同一个县、同一个计划、同一覆盖家庭的年龄改成 ${lastBridgeAge} 岁后取得的家庭年度全价报价`, `This is my annual household full-price quote for the same county, plan and covered household, re-priced at age ${lastBridgeAge}`)}</label><div>${tt("查询指引（可复制）：", "Quote guide (copyable):")} <code>https://www.healthcare.gov/see-plans/</code><br>${tt(`在同一个报价页把年龄改成 ${lastBridgeAge} 岁即可；两个报价之比就是纯年龄效应，App 按每年固定百分比在两者之间取值，不含通胀（通胀由「保费超额通胀」另算）。`, `Change the age to ${lastBridgeAge} on the same quote page. The ratio of the two quotes is the pure age effect; the App moves between them at one fixed percentage a year and adds no inflation of its own (that is the premium inflation spread).`)}<br>${tt("留空表示你没有报过第二个年龄的价 —— 那是「未测量」，不是「没有年龄效应」；留空时保费按现有单锚点方式走，App 不会替你猜一条曲线。", "Leaving it blank means you never priced a second age. That is unmeasured, not measured-and-flat: the premium keeps its single-anchor behaviour and the App does not guess a curve for you.")}<br>${tt("App 不会联网获取或核验此报价；清空只会恢复成未填写，绝不会写成 $0。", "The App does not fetch or verify this quote. Clearing it restores the unfilled state; it never writes $0.")}</div>`;
        const confirm = source.querySelector("[data-medical-age-end-confirm]");
        confirm.addEventListener("change", () => {
          if (confirm.checked) {
            const value = +get(state.config, "medical.premium_aca_age_end");
            if (!Number.isFinite(value) || value <= 0) {
              confirm.checked = false;
              inp.classList.add("invalid");
              return;
            }
          }
          setMedicalAgeEndUserQuote(confirm.checked);
          onWizChange();
        });
        w.appendChild(source);
      }
    }
    return w;
  }
  function fieldsVisible(fields) { return fields.filter(f => !f.showIf || f.showIf(state.config)); }

  // Per-field min/max + hard cross-field rules. Rail navigation and the final
  // Run button use the same validator, so there is no route around the wizard.
  function validateStep(stepIndex = state.step, focus = true) {
    const st = STEPS[stepIndex];
    const fields = st.advanced ? st.groups.flatMap(g => g.fields) : fieldsVisible(st.fields);
    if (focus) document.querySelectorAll(".field input.invalid").forEach(e => e.classList.remove("invalid"));
    const bad = [];
    fields.forEach(f => {
      if (f.type !== "num") return;
      const el = focus ? document.querySelector(`.field[data-path="${f.p}"] input`) : null;
      const raw = get(state.config, f.p);
      const v = f.pct ? (+raw * 100) : +raw;
      const premiumControlInvalid = f.p === "medical.premium_aca" && el
        && (el.value === "" || !Number.isFinite(+el.value) || +el.value <= 0);
      // Blank is valid here and only here: it is the "never priced" answer.
      // Anything typed has to be a usable quote, because a 0 or a stray
      // character would otherwise reach the engine as free coverage.
      const ageEndControlInvalid = f.p === "medical.premium_aca_age_end" && el
        && el.value !== "" && (!Number.isFinite(+el.value) || +el.value <= 0);
      const shareControlInvalid = f.p === "medical.household_share_primary" && el
        && el.value !== "" && (!Number.isFinite(+el.value)
                               || +el.value < 0 || +el.value > 100);
      const annualMedicalBlank = !!get(state.config, "medical.annual_trajectory_enabled")
        && ["medical.non_medical_y0", "medical.routine_y0",
            "medical.premium_medicare", "medical.oop_y0",
            "medical.cpi_delta_routine", "medical.cpi_delta_premium",
            "medical.cpi_delta_oop"].includes(f.p)
        && el && el.value === "";
      if (premiumControlInvalid || ageEndControlInvalid || shareControlInvalid || annualMedicalBlank || !isFinite(v) || (f.min != null && v < f.min) || (f.max != null && v > f.max)) bad.push(el || f.p);
    });
    if (st.id === "basics") {
      const m1 = +get(state.config, "milestones.0") || 0, m2 = +get(state.config, "milestones.1") || 0;
      if (m2 && m1 && m2 <= m1) {
        const el = document.querySelector('.field[data-path="milestones.1"] input');
        bad.push(el || "milestones.1");
      }
    }
    if (st.id === "advanced" && get(state.config, "housing.enabled") && get(state.config, "housing.mode") === "buy") {
      const purchaseAge = +get(state.config, "housing.purchase_age");
      const startAge = +get(state.config, "state.start_age");
      const endAge = startAge + (+get(state.config, "state.accum_years") || 0) + (+get(state.config, "state.retire_horizon") || 0);
      if (!isFinite(purchaseAge) || purchaseAge < startAge || purchaseAge > endAge) {
        const el = focus ? document.querySelector('.field[data-path="housing.purchase_age"] input') : null;
        bad.push(el || "housing.purchase_age");
      }
    }
    if (focus) bad.filter(el => el instanceof Element).forEach(el => el.classList.add("invalid"));
    if (bad.length) {
      if (focus) toast(tt("有字段超出合理范围；购房年龄还必须在模拟期间内。请修正标红项", "Some fields are out of range; purchase age must also fall within the modeled period. Fix the highlighted items."), true);
      return false;
    }
    return true;
  }
  function validateAllSteps() {
    for (let i = 0; i < STEPS.length; i++) {
      if (!validateStep(i, false)) { state.step = i; goto("wizard"); validateStep(i, true); return false; }
    }
    return validateBasics();
  }
  function validateBasics() {
    const sw = +get(state.config, "state.swr_pref") || 0, ex = +get(state.config, "state.expenses_y0") || 0;
    return sw > 0 && ex > 0;
  }

  // =========================================================== wizard view
  function buildRail() {
    $("wizardRail").innerHTML = STEPS.map((s, i) =>
      `<button class="rail-step${i === state.step ? " active" : ""}${i < state.step ? " done" : ""}" data-i="${i}">
        <span class="rail-dot">${i < state.step ? "✓" : i + 1}</span><span class="rail-t">${s.title[L === "zh" ? 0 : 1]}</span></button>`).join("");
    $("wizardRail").querySelectorAll(".rail-step").forEach(b =>
      b.addEventListener("click", () => { if (!validateStep()) return; saveDraft(true); state.step = +b.dataset.i; buildStep(); buildRail(); updateStepsMini(); }));
    edgeFade($("wizardRail"));
  }
  function buildStep() {
    const s = STEPS[state.step];
    const host = $("wizStep");
    // A ticked field rebuilds the whole step (dependent fields appear/vanish) — capture which
    // fgroups were open so a checkbox doesn't collapse the group you're working in. Same-step
    // only, keyed by group order; navigation to a different step keeps the defaults.
    const sameStep = host._lastStep === state.step;
    const wasOpen = sameStep ? [...host.querySelectorAll("details.fgroup")].map(d => d.open) : null;
    host.innerHTML = "";
    // Step NAVIGATION rises (§5.4 continuity); a language-switch rebuild of the same step
    // must not replay — same content in new words is not an arrival.
    if (host._lastStep !== state.step) { host._lastStep = state.step; riseIn(host); }
    const h = document.createElement("div"); h.className = "sec-head";
    h.innerHTML = `<span class="sec-num font-en">${romNum(state.step + 1)}</span><h2 class="sec-title">${s.title[L === "zh" ? 0 : 1]}</h2>`;
    host.appendChild(h);
    const k = document.createElement("p"); k.className = "sec-kicker"; k.textContent = s.kicker[L === "zh" ? 0 : 1]; host.appendChild(k);
    if (s.advanced) {
      s.groups.forEach((g, gi) => {
        const d = document.createElement("details"); d.className = "fgroup";
        if (wasOpen && wasOpen[gi]) {
          // Survive a checkbox-driven rebuild WITHOUT replaying the entrance — a restored-open
          // group is not a fresh open, so suppress detailsRise (the replay read as a page flash).
          d.open = true; d.classList.add("no-rise");
          // Setting .open above queues ONE deferred toggle event; swallow just that programmatic
          // one (else it'd clear no-rise a task later and the flash returns), and re-enable the
          // entrance only on a genuine later user open/close.
          let skipProgrammatic = true;
          d.addEventListener("toggle", () => {
            if (skipProgrammatic) { skipProgrammatic = false; return; }
            d.classList.remove("no-rise");
          });
        }
        d.innerHTML = `<summary>${g.title[L === "zh" ? 0 : 1]}</summary>`;
        const body = document.createElement("div"); body.className = "fgroup-body";
        g.fields.forEach(f => body.appendChild(fieldEl(f)));
        d.appendChild(body); host.appendChild(d);
      });
    } else {
      const grid = document.createElement("div"); grid.className = "field-grid";
      fieldsVisible(s.fields).forEach(f => grid.appendChild(fieldEl(f)));
      host.appendChild(grid);
      if (s.custom === "family") renderFamilyEditors(host);
      if (s.custom === "review") renderReviewStep(host);
      if (s.custom === "csvimport") renderCsvImport(host);
      if (s.custom === "ssaimport") renderSsaImport(host);
    }
    $("wizNext").textContent = state.step === STEPS.length - 1 ? tt("去选精度 →", "To precision →") : t("nav.next");
    $("wizPrev").style.visibility = state.step === 0 ? "hidden" : "visible";
    renderWizSide();
  }
  // ---------- D1 broker CSV import (portfolio step) ----------
  const CSVI = { data: null };
  function renderCsvImport(host) {
    const box = document.createElement("div");
    box.id = "csviBox";
    box.innerHTML = `
      <div class="compute-row" style="margin-top:16px">
        <button class="btn-ghost sm" id="csviBtn">📄 ${tt("从券商持仓 CSV 导入…（主流券商导出格式）", "Import broker positions CSV… (major-brokerage exports)")}</button>
        <input type="file" id="csviFile" accept=".csv,.txt" style="display:none">
        <span class="hint" id="csviHint"></span>
      </div>
      <div id="csviResult"></div>
      <p class="cap">${tt("完全本机解析：文件不上传、不落盘，服务端只返回账户级汇总（不含任何持仓明细）。", "Parsed entirely on this machine: the file is never uploaded or stored; only account-level totals come back (no position details).")}</p>`;
    host.appendChild(box);
    $("csviBtn").addEventListener("click", () => $("csviFile").click());
    $("csviFile").addEventListener("change", async (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      $("csviHint").textContent = tt("解析中…", "parsing…");
      try {
        const text = await file.text();
        const r = await postJSON("/api/import_csv", { text });
        CSVI.data = r;
        renderCsvResult();
        $("csviHint").textContent = "";
      } catch (err) { toast(err.message, true); $("csviHint").textContent = ""; }
      e.target.value = "";
    });
    if (CSVI.data) renderCsvResult();
  }
  function renderCsvResult() {
    const r = CSVI.data, hostEl = $("csviResult");
    if (!r || !hostEl) return;
    const B_LAB = { pretax_401k: tt("税前 401k/IRA", "Pretax 401k/IRA"), roth_ira: "Roth IRA", hsa: "HSA", taxable: tt("应税账户", "Taxable"), cash: tt("现金", "Cash"), skip: tt("忽略", "Skip") };
    const opts = b => Object.entries(B_LAB).map(([k, la]) => `<option value="${k}" ${k === (b || "skip") ? "selected" : ""}>${la}</option>`).join("");
    hostEl.innerHTML = `
      <table class="ed-table" style="margin-top:10px"><thead><tr>
        <th>${tt("账户", "Account")}</th><th>${tt("持仓数", "Positions")}</th><th>${tt("金额", "Total")}</th><th>${tt("归入", "Bucket")}</th>
      </tr></thead><tbody>` +
      r.accounts.map((a, i) => `<tr><td>${esc(a.label)}</td><td>${esc(a.positions)}</td><td class="real">${money(a.total)}</td><td><select data-i="${i}" class="csviSel">${opts(a.bucket)}</select></td></tr>`).join("") +
      `</tbody></table>
      <div class="compute-row"><button class="btn-run sm" id="csviApply">✓ ${tt("应用到上方持仓（覆盖对应桶）", "Apply to the fields above (overwrites those buckets)")}</button>
      ${r.warnings && r.warnings.length ? `<span class="hint">${tt("有未识别账户，请先在「归入」里指认", "Unclassified accounts — assign them first")}</span>` : ""}</div>`;
    $("csviApply").addEventListener("click", () => {
      const sums = {};
      hostEl.querySelectorAll(".csviSel").forEach(sel => {
        const b = sel.value, a = r.accounts[+sel.dataset.i];
        if (b !== "skip") sums[b] = (sums[b] || 0) + a.total;
      });
      let applied = 0;
      for (const [b, v] of Object.entries(sums)) {
        if (b === "cash") set(state.config, "other_assets.cash", Math.round(v));
        else set(state.config, "initial." + b, Math.round(v));
        applied++;
      }
      if (!applied) { toast(tt("没有可应用的账户", "Nothing to apply"), true); return; }
      saveDraft(true); buildStep();
      toast(tt("已导入 " + applied + " 个桶——请核对上方字段", "Imported into " + applied + " bucket(s) — verify the fields above"));
    });
  }

  // ---------- D2 SSA statement import (assumptions step) ----------
  const SSAI = { data: null, text: null };
  function renderSsaImport(host) {
    const box = document.createElement("div");
    box.innerHTML = `
      <div class="compute-row" style="margin-top:16px">
        <button class="btn-ghost sm" id="ssaiBtn">📄 ${tt("从 SSA 记录导入 PIA…（ssa.gov 的 XML 报表）", "Import PIA from SSA record… (ssa.gov XML statement)")}</button>
        <input type="file" id="ssaiFile" accept=".xml" style="display:none">
        <label class="hint" style="cursor:pointer"><input type="checkbox" id="ssaiProj"> ${tt("假设继续当前收入工作到 62 岁", "Assume current earnings continue to 62")}</label>
        <span class="hint" id="ssaiHint"></span>
      </div>
      <div id="ssaiResult"></div>
      <p class="cap">${tt("完全本机解析：逐年收入历史不回显、不落盘、不打日志，只返回 AIME/PIA 与覆盖统计。FIRE 人群默认不投影未来收入——提前退休意味着前 35 年里有零。", "Parsed entirely on this machine: the year-by-year earnings history is never echoed, stored, or logged — only AIME/PIA and coverage stats come back. Default: no future-earnings projection — early retirement means zeros in the top-35.")}</p>`;
    host.appendChild(box);
    $("ssaiBtn").addEventListener("click", () => $("ssaiFile").click());
    $("ssaiProj").addEventListener("change", () => { if (SSAI.text) ssaiParse(); });
    $("ssaiFile").addEventListener("change", async (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      SSAI.text = await file.text();
      ssaiParse();
      e.target.value = "";
    });
    if (SSAI.data) renderSsaResult();
  }
  async function ssaiParse() {
    $("ssaiHint").textContent = tt("解析中…", "parsing…");
    try {
      const by = new Date().getFullYear() - (+get(state.config, "state.start_age") || 30);
      const r = await postJSON("/api/import_ssa", { text: SSAI.text, birth_year: by, project: $("ssaiProj").checked });
      SSAI.data = r;
      SSAI.text = null; // raw statement is no longer needed after a successful parse
      renderSsaResult();
      $("ssaiHint").textContent = "";
    } catch (err) { toast(err.message, true); $("ssaiHint").textContent = ""; }
    finally { SSAI.text = null; }
  }
  function renderSsaResult() {
    const r = SSAI.data, el = $("ssaiResult");
    if (!r || !el) return;
    const importReceipt = r.rule_pack || null;
    const importPack = ((importReceipt || {}).component || {});
    const importStatus = isValidSsaRulePackReceipt(importReceipt)
      ? ` · rule pack <b>${esc(importPack.status)}</b>`
      : ` · ${tt("rule pack 未记录", "rule pack unrecorded")}`;
    el.innerHTML = `
      <div class="readout-grid" style="margin-top:12px">
        <div class="readout"><div class="lab">PIA ${tt("月额（今日$）", "monthly (today $)")}</div><div class="num accent">${money(r.pia_monthly)}</div></div>
        <div class="readout"><div class="lab">AIME</div><div class="num">${money(r.aime_monthly)}</div></div>
        <div class="readout"><div class="lab">${tt("有收入年数", "Years w/ earnings")}</div><div class="num">${r.years_with_earnings}${r.rows_skipped ? tt(`（另有 ${r.rows_skipped} 行无法解析，已跳过）`, ` (${r.rows_skipped} row(s) unreadable and skipped)`) : ""}${r.projected_years ? tt(`（含投影 ${r.projected_years}）`, ` (incl. ${r.projected_years} projected)`) : ""}</div></div>
        <div class="readout"><div class="lab">${tt("前 35 年中的零", "Zeros in top-35")}</div><div class="num">${r.zeros_in_top35}</div></div>
      </div>
      <div class="compute-row"><button class="btn-run sm" id="ssaiApply">✓ ${tt("应用 PIA 到社保设置", "Apply PIA to Social Security")}</button>
      <span class="hint">${tt(`弯点 $${r.bend_points[0]}/$${r.bend_points[1]}（资格年 ${r.eligibility_year}）· AWI 表至 ${r.awi_vintage}`, `bends $${r.bend_points[0]}/$${r.bend_points[1]} (eligibility ${r.eligibility_year}) · AWI through ${r.awi_vintage}`)}${importStatus}</span></div>`;
    $("ssaiApply").addEventListener("click", () => {
      set(state.config, "social_security.pia_monthly_y0", Math.round(r.pia_monthly));
      set(state.config, "social_security.enabled", true);
      saveDraft(true); buildStep();
      toast(tt("PIA 已应用：$" + Math.round(r.pia_monthly) + "/月", "PIA applied: $" + Math.round(r.pia_monthly) + "/mo"));
    });
  }

  function renderFamilyEditors(host) {
    const kids = get(state.config, "children") || [];
    const evs = get(state.config, "life_events") || [];
    const box = document.createElement("div");
    box.innerHTML = `
      <div class="panel-title sm" style="margin-top:6px">${tt("子女", "Children")}
        <span class="tag">${tt("每个孩子＝出生→成年逐年成本＋大学四年", "each child = yearly cost to adulthood + 4 college years")}</span></div>
      <table class="ed-table" id="kidTable"><thead><tr>
        <th>${tt("你当时年龄", "Your age at birth")}</th><th>${tt("年成本（今日$）", "Annual cost (today $)")}</th>
        <th>${tt("抚养年数", "Support years")}</th><th>${tt("大学总额（今日$）", "College total (today $)")}</th><th></th>
      </tr></thead><tbody></tbody></table>
      <button class="btn-ghost sm" id="addKid">＋ ${tt("添加子女", "Add child")}</button>
      <div class="panel-title sm" style="margin-top:22px">${tt("自定义人生事件", "Custom life events")}
        <span class="tag">${tt("正数＝支出（购房/婚礼/赡养）· 负数＝收入（继承/变现）", "positive = outflow (home/wedding/eldercare) · negative = inflow (inheritance/sale)")}</span></div>
      <table class="ed-table" id="evTable"><thead><tr>
        <th>${tt("年龄", "Age")}</th><th>${tt("金额（今日$）", "Amount (today $)")}</th><th>${tt("备注", "Label")}</th><th></th>
      </tr></thead><tbody></tbody></table>
      <button class="btn-ghost sm" id="addEv">＋ ${tt("添加事件", "Add event")}</button>
      <div class="panel-title sm" style="margin-top:22px">${tt("父母", "Parents")}
        <span class="tag">${tt("每位父母＝一次死亡，护理与遗产都由它派生", "each parent = one death, with care and bequest derived from it")}</span></div>
      <p class="cap" style="margin-top:0">${tt("只有在上面「高级 → 父母生命周期」把模块打开后才会生效；开启它会取代赡养冲击与继承。", "Only has effect once the module is switched on under Advanced → Parent lifecycle; switching it on replaces the eldercare shock and the inheritance draw.")}</p>
      <table class="ed-table" id="parTable"><thead><tr>
        <th>${tt("称呼", "Label")}</th><th>${tt("现在年龄", "Age now")}</th><th>${tt("性别（死亡率表）", "Sex (mortality)")}</th>
        <th>${tt("预计遗产（今日$，护理前）", "Estate (today $, before care)")}</th><th>${tt("终身需要护理的概率", "Lifetime care risk")}</th><th></th>
      </tr></thead><tbody></tbody></table>
      <button class="btn-ghost sm" id="addPar">＋ ${tt("添加父母", "Add parent")}</button>

      <div class="panel-title sm" style="margin-top:22px">${tt("保底收入 · 你自己拿到的报价", "Guaranteed income · the quotes you were given")}</div>
      <p class="cap">${tt("这里只接受<b>你手上那张报价单</b>上的数字。本仓库不内置任何年金费率表——年金定价取决于你的年龄、性别、州、公司和当天利率，我们给不出比它更准的数。没有报价的方案<b>不会被推算出来</b>，只会被如实说成「没能比较」。", "Only numbers off <b>the quote sheet in your hand</b> go here. No annuity rate table ships in this repository — pricing depends on your age, sex, state, insurer and the rates that day, and we cannot beat your own quote. An option you hold no quote for is <b>not derived</b>; it is reported as one that could not be compared.")}</p>
      <table class="ed-table" id="annTable"><thead><tr>
        <th>${tt("称呼", "Label")}</th><th>${tt("保费", "Premium")}</th>
        <th>${tt("年领（勾 COLA 填今日购买力）", "Annual payout (today's $ if COLA)")}</th>
        <th>${tt("购买年龄", "Purchase age")}</th><th>${tt("起领年龄", "Income from")}</th>
        <th>${tt("随通胀调整", "COLA")}</th><th>${tt("终止于", "Ends")}</th><th></th>
      </tr></thead><tbody></tbody></table>
      <button class="btn-ghost sm" id="addAnn">＋ ${tt("添加年金报价", "Add annuity quote")}</button>
      <p class="cap" style="margin-top:10px">${tt("购买年龄与起领年龄不同就是<b>递延</b>年金——中间那段是不付钱的，那正是递延换来的东西。", "A purchase age earlier than the start age makes it <b>deferred</b> — the unpaid gap between them is exactly what deferring buys.")}</p>

      <table class="ed-table" id="ladTable" style="margin-top:16px"><thead><tr>
        <th>${tt("称呼", "Label")}</th><th>${tt("总成本", "Total cost")}</th>
        <th>${tt("每年实际收入", "Annual real income")}</th>
        <th>${tt("起始年龄", "Start age")}</th><th>${tt("年数", "Years")}</th><th></th>
      </tr></thead><tbody></tbody></table>
      <button class="btn-ghost sm" id="addLad">＋ ${tt("添加 TIPS 阶梯", "Add TIPS ladder")}</button>
      <p class="cap" style="margin-top:10px">${tt("TIPS 阶梯<b>不是年金</b>：没有长寿信用，年限一到就停，无论你是否还在世。这正是这个决策要比较的两种东西。", "A TIPS ladder is <b>not an annuity</b>: no mortality credit, and it stops on schedule whether or not you are alive. That is exactly the comparison this decision is about.")}</p>
      <p class="cap" style="margin-top:14px">${tt("FIRE 前的支出从应税账户扣除（不动退休账户）；FIRE 后走引擎的账户提取顺序。任何未付足的强制支出都会记录缺口并判该路径失败。", "Pre-FIRE outflows draw only from taxable; post-FIRE they follow the engine withdrawal order. Any mandatory outflow not paid in full is recorded and fails that path.")}</p>`;
    host.appendChild(box);
    const kidBody = box.querySelector("#kidTable tbody");
    const evBody = box.querySelector("#evTable tbody");
    const num = (v, cb, w) => { const i = document.createElement("input"); i.type = "number"; i.value = v; if (w) i.style.width = w; i.addEventListener("input", () => { cb(+i.value || 0); onWizChange(); }); const td = document.createElement("td"); td.appendChild(i); return td; };
    const txt = (v, cb) => { const i = document.createElement("input"); i.type = "text"; i.value = v || ""; i.addEventListener("input", () => { cb(i.value); onWizChange(); }); const td = document.createElement("td"); td.appendChild(i); return td; };
    const del = (fn) => { const b = document.createElement("button"); b.className = "btn-ghost sm"; b.textContent = "✕"; b.addEventListener("click", () => { fn(); buildStep(); onWizChange(); }); const td = document.createElement("td"); td.appendChild(b); return td; };
    kids.forEach((k, i) => {
      const tr = document.createElement("tr");
      tr.appendChild(num(k.parent_age_at_birth, v => k.parent_age_at_birth = v));
      tr.appendChild(num(k.annual_cost_real, v => k.annual_cost_real = v));
      tr.appendChild(num(k.support_years, v => k.support_years = v));
      tr.appendChild(num(k.college_total_real, v => k.college_total_real = v));
      tr.appendChild(del(() => kids.splice(i, 1)));
      kidBody.appendChild(tr);
    });
    evs.forEach((e, i) => {
      const tr = document.createElement("tr");
      tr.appendChild(num(e.age, v => e.age = v));
      tr.appendChild(num(e.amount_real, v => e.amount_real = v));
      tr.appendChild(txt(e.label, v => e.label = v));
      tr.appendChild(del(() => evs.splice(i, 1)));
      evBody.appendChild(tr);
    });
    box.querySelector("#addKid").addEventListener("click", () => {
      if (!Array.isArray(get(state.config, "children"))) set(state.config, "children", []);
      const base = +get(state.config, "state.start_age") || 30;
      get(state.config, "children").push({ parent_age_at_birth: base + 2, annual_cost_real: 15000, support_years: 22, college_total_real: 120000 });
      buildStep(); onWizChange();
    });
    box.querySelector("#addEv").addEventListener("click", () => {
      if (!Array.isArray(get(state.config, "life_events"))) set(state.config, "life_events", []);
      const base = +get(state.config, "state.start_age") || 30;
      get(state.config, "life_events").push({ age: base + 10, amount_real: 50000, label: "" });
      buildStep(); onWizChange();
    });
    const parents = get(state.config, "parents.parents") || [];
    const parBody = box.querySelector("#parTable tbody");
    const sel = (v, cb) => {
      const i = document.createElement("select");
      [["female", tt("女", "Female")], ["male", tt("男", "Male")]].forEach(([val, lab]) => {
        const o = document.createElement("option"); o.value = val; o.textContent = lab;
        if (val === v) o.selected = true; i.appendChild(o);
      });
      i.addEventListener("change", () => { cb(i.value); onWizChange(); });
      const td = document.createElement("td"); td.appendChild(i); return td;
    };
    parents.forEach((pa, i) => {
      const tr = document.createElement("tr");
      tr.appendChild(txt(pa.label, v => pa.label = v));
      tr.appendChild(num(pa.current_age, v => pa.current_age = v));
      tr.appendChild(sel(pa.sex, v => pa.sex = v));
      tr.appendChild(num(pa.estate_y0, v => pa.estate_y0 = v));
      // Blank means "never needs paid care", which is NOT the same as 0 risk;
      // the engine treats null that way and says so in the result.
      tr.appendChild(num(pa.care_lifetime_risk == null ? "" : pa.care_lifetime_risk,
                         v => pa.care_lifetime_risk = (v === 0 ? null : v), "90px"));
      tr.appendChild(del(() => parents.splice(i, 1)));
      parBody.appendChild(tr);
    });
    // Guaranteed income: two row editors over the same block. Written out
    // rather than shared with the parent table because the columns differ in
    // kind — one carries a boolean the payout's meaning depends on.
    const chk = (v, cb) => {
      const i = document.createElement("input"); i.type = "checkbox"; i.checked = !!v;
      i.addEventListener("change", () => { cb(i.checked); onWizChange(); });
      const td = document.createElement("td"); td.appendChild(i); return td;
    };
    const pick = (v, options, cb) => {
      const i = document.createElement("select");
      options.forEach(([val, lab]) => {
        const o = document.createElement("option"); o.value = val; o.textContent = lab;
        if (val === v) o.selected = true; i.appendChild(o);
      });
      i.addEventListener("change", () => { cb(i.value); onWizChange(); });
      const td = document.createElement("td"); td.appendChild(i); return td;
    };
    const giBlock = () => {
      if (!get(state.config, "guaranteed_income")) set(state.config, "guaranteed_income", { mode: "off", annuities: [], ladders: [] });
      const g = get(state.config, "guaranteed_income");
      if (!Array.isArray(g.annuities)) g.annuities = [];
      if (!Array.isArray(g.ladders)) g.ladders = [];
      return g;
    };
    const annuities = get(state.config, "guaranteed_income.annuities") || [];
    const ladders = get(state.config, "guaranteed_income.ladders") || [];
    const annBody = box.querySelector("#annTable tbody");
    annuities.forEach((a, i) => {
      const tr = document.createElement("tr");
      tr.appendChild(txt(a.label, v => a.label = v));
      tr.appendChild(num(a.premium, v => a.premium = v));
      tr.appendChild(num(a.annual_payout_real, v => a.annual_payout_real = v));
      tr.appendChild(num(a.purchase_age, v => a.purchase_age = v, "80px"));
      tr.appendChild(num(a.start_age, v => a.start_age = v, "80px"));
      tr.appendChild(chk(a.cola, v => a.cola = v));
      tr.appendChild(pick(a.termination || "until_death", [
        ["until_death", tt("本人身故", "your death")],
        ["until_second_death", tt("二人皆故", "the second death")],
        ["for_a_term", tt("固定年限", "a fixed term")],
      ], v => a.termination = v));
      tr.appendChild(del(() => annuities.splice(i, 1)));
      annBody.appendChild(tr);
    });
    box.querySelector("#addAnn").addEventListener("click", () => {
      // Zeros, not plausible-looking figures. A prefilled premium and payout
      // would be a quote this repository does not have, and the user would
      // have to notice it was ours before it became theirs.
      giBlock().annuities.push({ label: tt("年金报价", "Annuity quote"),
        premium: 0, annual_payout_real: 0,
        purchase_age: +get(state.config, "state.start_age") || 65,
        start_age: +get(state.config, "state.start_age") || 65,
        cola: false, termination: "until_death" });
      buildStep(); onWizChange();
    });
    const ladBody = box.querySelector("#ladTable tbody");
    ladders.forEach((l, i) => {
      const tr = document.createElement("tr");
      tr.appendChild(txt(l.label, v => l.label = v));
      tr.appendChild(num(l.cost, v => l.cost = v));
      tr.appendChild(num(l.annual_real, v => l.annual_real = v));
      tr.appendChild(num(l.start_age, v => l.start_age = v, "80px"));
      tr.appendChild(num(l.years, v => l.years = v, "80px"));
      tr.appendChild(del(() => ladders.splice(i, 1)));
      ladBody.appendChild(tr);
    });
    box.querySelector("#addLad").addEventListener("click", () => {
      giBlock().ladders.push({ label: tt("TIPS 阶梯", "TIPS ladder"), cost: 0,
        annual_real: 0,
        start_age: +get(state.config, "state.start_age") || 65, years: 10 });
      buildStep(); onWizChange();
    });
    box.querySelector("#addPar").addEventListener("click", () => {
      if (!get(state.config, "parents")) set(state.config, "parents", { mode: "off", parents: [] });
      if (!Array.isArray(get(state.config, "parents.parents"))) set(state.config, "parents.parents", []);
      get(state.config, "parents.parents").push(
        { label: tt("父母", "Parent"), current_age: 70, sex: "female",
          estate_y0: 0, care_lifetime_risk: 0.58 });
      buildStep(); onWizChange();
    });
  }

  function reviewAnomalies() {
    const out = [];
    let gross = (+get(state.config, "contributions.base_salary_pre") || 0) + (+get(state.config, "contributions.bonus_pre") || 0) + (+get(state.config, "contributions.ot_income_pre") || 0);
    if (get(state.config, "household.enabled")) gross += (+get(state.config, "household.spouse_base_salary_pre") || 0) + (+get(state.config, "household.spouse_bonus_pre") || 0);
    const sav = estSavings(), sr = gross > 0 ? sav / gross : 0;
    const swr = (+get(state.config, "state.swr_pref") || 0) * 100;
    const exp = +get(state.config, "state.expenses_y0") || 0;
    const spendNow = +get(state.config, "contributions.annual_spending_now") || exp;
    const age = +get(state.config, "state.start_age") || 0;
    if (sr > 0.75) out.push(tt(`储蓄率 ≈ ${(sr * 100).toFixed(0)}%——高于绝大多数人，确认当前开销 ${money(spendNow)} 没填漏。`, `Savings rate ≈ ${(sr * 100).toFixed(0)}% — unusually high; confirm current spending ${money(spendNow)} isn't missing anything.`));
    if (sr < 0.05 && gross > 0) out.push(tt(`储蓄率 ≈ ${(sr * 100).toFixed(0)}%——几乎存不下钱，FIRE 会非常远。`, `Savings rate ≈ ${(sr * 100).toFixed(0)}% — almost nothing saved; FIRE will be very far.`));
    if (swr > 4.5) out.push(tt(`SWR ${swr.toFixed(2)}% 偏激进（经典研究多在 3–4%）。`, `SWR ${swr.toFixed(2)}% is aggressive (classic studies: 3–4%).`));
    if (swr < 2.5 && swr > 0) out.push(tt(`SWR ${swr.toFixed(2)}% 非常保守——FIRE 门槛会很高。`, `SWR ${swr.toFixed(2)}% is very conservative — a high FI number.`));
    if (exp > 0 && exp < 15000) out.push(tt(`退休年支出 ${money(exp)} 低于多数人的生存线，确认单位没错。`, `Retirement spend ${money(exp)} is below subsistence for most — check the units.`));
    if (age >= 40 && (+get(state.config, "state.accum_years") || 25) > 30) out.push(tt("工作年数与年龄组合意味着 70+ 才停——确认这是本意。", "Age + max work years imply working past 70 — confirm that's intended."));
    // Caught here rather than as a 400 after the user presses run: the server
    // refuses this combination, and meeting a refusal at submit time is the
    // shape of defect this round already fixed once elsewhere.
    const parentsOn = (get(state.config, "parents.mode") || "off") !== "off";
    if (parentsOn) {
      ["eldercare", "inheritance"].forEach(other => {
        if ((get(state.config, other + ".mode") || "off") !== "off") {
          out.push(tt(`父母生命周期模块已开启，${other === "eldercare" ? "赡养冲击" : "继承"}也还开着——两者说的是同一位父母，同时开会为一次衰老付两次钱、为一次死亡继承两遍。运行会被拒绝；请关掉其中一个。`,
                      `The parent lifecycle module is on and so is the ${other === "eldercare" ? "eldercare shock" : "inheritance draw"} — both describe the same parent, so together they would pay for one decline twice and inherit from one death twice. The run will be refused; switch one of them off.`));
        }
      });
      if (!((get(state.config, "parents.parents") || []).length)) {
        out.push(tt("父母生命周期模块已开启，但还没有添加任何父母——这样它会报出零支出与零遗产，而那两个零都不是测量结果。请在「家庭与人生事件」里添加，或把模块关掉。",
                    "The parent lifecycle module is on but no parents are listed — it would report zero support cost and zero inheritance, and neither zero would be a measurement. Add one under Family & life events, or switch the module off."));
      }
    }
    // Guaranteed income: the same shape of warning, and for the same reason.
    // Every one of these is a refusal the adapter will issue; meeting it after
    // pressing run is the defect this project already fixed once at the seam.
    if ((get(state.config, "guaranteed_income.mode") || "off") !== "off") {
      const anns = get(state.config, "guaranteed_income.annuities") || [];
      const lads = get(state.config, "guaranteed_income.ladders") || [];
      if (!anns.length && !lads.length) {
        out.push(tt("保底收入模块已开启，但一份报价都没填——它会报出零保费与零收入，而那两个零都不是测量结果。请填入你自己的报价，或把模块关掉。",
                    "Guaranteed income is on with no quote entered — it would report zero premium and zero income, and neither zero would be a measurement. Enter your own quote or switch the module off."));
      }
      anns.forEach((a, i) => {
        if (!(+a.premium > 0) || !(+a.annual_payout_real > 0)) {
          out.push(tt(`第 ${i + 1} 份年金报价的保费或年领是 0——没有成本或没有收益的年金不是「更便宜的年金」，是一条什么都不做、却报出正好为零的效果的行。运行会被拒绝。`,
                      `Annuity quote ${i + 1} has a zero premium or a zero payout — an annuity with no cost or no payout is not a cheaper one, it is a line that does nothing and reports an effect of exactly zero. The run will be refused.`));
        }
        if (+a.start_age < +a.purchase_age) {
          out.push(tt(`第 ${i + 1} 份年金的起领年龄早于购买年龄——收入不能在保费之前开始。`,
                      `Annuity quote ${i + 1} starts income before the premium is paid.`));
        }
      });
      lads.forEach((l, i) => {
        if (!(+l.cost > 0) || !(+l.annual_real > 0) || !(+l.years > 0)) {
          out.push(tt(`第 ${i + 1} 条 TIPS 阶梯的成本、年收入或年数是 0——同上，运行会被拒绝。`,
                      `TIPS ladder ${i + 1} has a zero cost, income or term — as above, the run will be refused.`));
        }
      });
    }
    return out;
  }
  function renderReviewStep(host) {
    const dflt = state.presets[Object.keys(state.presets)[0]];
    const dcfg = dflt ? dflt.config : {};
    const an = reviewAnomalies();
    const box = document.createElement("div");
    let html = "";
    if (an.length) html += `<div class="callout warn" style="margin-bottom:16px"><h5><span class="pill">${tt("请确认", "CHECK")}</span>${tt("有几个数值不太寻常", "A few values look unusual")}</h5>${an.map(a => `<p>${a}</p>`).join("")}</div>`;
    STEPS.forEach((st, i) => {
      if (st.custom === "review") return;
      let rows = "";
      if (st.custom === "family") {
        const kids = (get(state.config, "children") || []).length;
        const evs = (get(state.config, "life_events") || []).length;
        rows = `<tr><td>${tt("子女", "Children")}</td><td class="real">${kids}</td></tr><tr><td>${tt("自定义事件", "Custom events")}</td><td class="real">${evs}</td></tr>`;
      } else if (st.advanced || st.groups) {
        let changed = 0;
        (st.groups || []).forEach(gp => gp.fields.forEach(f => {
          const a = get(state.config, f.p), b = get(dcfg, f.p);
          if (JSON.stringify(a) !== JSON.stringify(b)) changed++;
        }));
        rows = `<tr><td>${tt("与默认不同的高级参数", "Advanced params changed")}</td><td class="real">${changed}</td></tr>`;
      } else {
        fieldsVisible(st.fields).forEach(f => {
          let v = readF(f);
          const shown = f.type === "check" ? (v ? "✓" : "—") : fmtV(f, v);
          const dv = get(dcfg, f.p);
          const isDiff = JSON.stringify(get(state.config, f.p)) !== JSON.stringify(dv);
          rows += `<tr><td>${lbl(f)}</td><td class="${isDiff ? "real" : "nom"}">${esc(shown)}</td></tr>`;
        });
      }
      html += `<details class="fgroup" ${an.length && i < 2 ? "open" : ""}><summary>${st.title[L === "zh" ? 0 : 1]} <span class="tag" style="margin-left:auto;cursor:pointer" data-goto="${i}">${tt("编辑 →", "edit →")}</span></summary><div class="fgroup-body"><table class="cmp-table"><tbody>${rows}</tbody></table></div></details>`;
    });
    html += `<div class="compute-row" style="margin-top:18px">
      <button class="btn-ghost sm" id="expCfg">${tt("导出配置 (.json)", "Export config (.json)")}</button>
      <button class="btn-ghost sm" id="impCfg">${tt("导入配置", "Import config")}</button>
      <input type="file" id="impFile" accept="application/json" style="display:none">
    </div>`;
    box.innerHTML = html;
    host.appendChild(box);
    box.querySelectorAll("[data-goto]").forEach(el2 => el2.addEventListener("click", e => {
      e.preventDefault(); e.stopPropagation();
      state.step = +el2.dataset.goto; buildStep(); buildRail(); updateStepsMini();
    }));
    box.querySelector("#expCfg").addEventListener("click", exportConfig);
    box.querySelector("#impCfg").addEventListener("click", () => box.querySelector("#impFile").click());
    box.querySelector("#impFile").addEventListener("change", ev => importConfig(ev.target.files[0]));
  }
  function exportConfig() {
    const a = document.createElement("a");
    a.href = URL.createObjectURL(new Blob([JSON.stringify({ v: DRAFT_V, config: state.config }, null, 1)], { type: "application/json" }));
    a.download = ((get(state.config, "name") || "fire-config").replace(/[^\w\u4e00-\u9fa5-]+/g, "_")) + ".json";
    a.click();
    toast(tt("配置已导出——发给朋友即可复现你的分析", "Config exported — share it to reproduce your analysis"));
  }
  function importConfig(file) {
    if (!file) return;
    const rd = new FileReader();
    rd.onload = () => {
      try {
        const j = JSON.parse(rd.result);
        const cfg = j && j.config ? j.config : j;
        if (!cfg || typeof cfg !== "object" || !cfg.state) throw new Error(tt("不是有效的配置文件", "Not a valid config file"));
        clearActivePlanRef(); state.config = normalizeConfig(cfg);
        state.step = 0; goto("wizard");
        toast(tt("配置已导入", "Config imported"));
      } catch (e) { toast(e.message, true); }
    };
    rd.readAsText(file);
  }

  function onWizChange() { renderWizSide(); $("saveHint").textContent = tt("有未保存改动", "unsaved changes"); }
  function estSavings() {
    const c = get(state.config, "contributions") || {}, st = get(state.config, "state") || {};
    const base = +c.base_salary_pre || 0, ot = +c.ot_income_pre || 0, bonus = +c.bonus_pre || 0;
    const gross = base + ot + bonus, matchBase = base + ot + (c.match_excludes_bonus ? 0 : bonus);
    const match = (+c.match_rate || 0) * matchBase, preTax = +c.pretax_401k_limit_y1 || 0, roth = +c.roth_ira_limit_y1 || 0, hsa = +c.hsa_limit_y1 || 0;
    const afterTax = Math.max(0, gross - preTax - hsa) * (1 - (+c.marginal_tax_pre || 0.24));
    const spendNow = +c.annual_spending_now || +st.expenses_y0 || 0;
    let total = preTax + roth + hsa + match + Math.max(0, afterTax - spendNow - roth);
    const hh = get(state.config, "household") || {};
    if (hh.enabled) {
      const sGross = (+hh.spouse_base_salary_pre || 0) + (+hh.spouse_bonus_pre || 0);
      const sPre = +hh.spouse_pretax_401k_limit_y1 || 0, sRoth = +hh.spouse_roth_ira_limit_y1 || 0, sHsa = +hh.spouse_hsa_limit_y1 || 0;
      const sMatch = (+hh.spouse_match_rate || 0) * (+hh.spouse_base_salary_pre || 0);
      // spouse residual: no second expense subtraction (household spending is counted once)
      const sAfter = Math.max(0, sGross - sPre - sHsa) * (1 - (+hh.spouse_marginal_tax_pre || 0.24));
      total += sPre + sRoth + sHsa + sMatch + Math.max(0, sAfter - sRoth);
    }
    return total;
  }
  function renderWizSide() {
    const buckets = [["initial.pretax_401k", tt("税前 401k", "Pretax 401k")], ["initial.roth_ira", "Roth IRA"], ["initial.hsa", "HSA"], ["initial.taxable", tt("应税", "Taxable")], ["other_assets.cash", tt("现金", "Cash")], ["other_assets.other_liquid", tt("其他流动", "Other liquid")]];
    if (get(state.config, "household.enabled")) {
      buckets.push(["household.spouse_initial_pretax", tt("配偶税前", "Sp. pretax")],
                   ["household.spouse_initial_roth", tt("配偶 Roth", "Sp. Roth")],
                   ["household.spouse_initial_hsa", tt("配偶 HSA", "Sp. HSA")],
                   ["household.spouse_initial_taxable", tt("配偶应税", "Sp. taxable")]);
    }
    const tot = buckets.reduce((a, [p]) => a + (+get(state.config, p) || 0), 0) || 1;
    const homeEq = +get(state.config, "other_assets.home_equity") || 0;
    $("wizHoldings").innerHTML = `<tbody>` + buckets.map(([p, la]) => `<tr><td>${la}</td><td class="real">${money(+get(state.config, p) || 0)}</td></tr>`).join("") +
      `<tr><td><b>${tt("合计（计入模拟）", "Total (simulated)")}</b></td><td class="real"><b>${money(tot)}</b></td></tr>` +
      (homeEq ? `<tr><td class="muted">${tt("房产净值（不计入）", "Home equity (excluded)")}</td><td class="nom">${money(homeEq)}</td></tr>` : "") + `</tbody>`;
    const spend = +get(state.config, "state.expenses_y0") || 0;
    const _swrRaw = +get(state.config, "state.swr_pref");
    const swr = isFinite(_swrRaw) ? _swrRaw : 0;   // 0 must show as 0, not fall back (audit P2-1)
    const der = [[tt("年度储蓄 ≈", "Savings ≈"), money(estSavings()), "accent"], [tt("FI 数", "FI number"), swr > 0 ? money(spend / swr) : "—", ""], [tt("现价组合", "Portfolio"), money(tot), "home"], ["SWR", (swr * 100).toFixed(2) + "%", ""]];
    $("wizDerived").innerHTML = der.map(([la, v, c]) => `<div class="readout"><div class="lab">${la}</div><div class="num ${c}">${v}</div></div>`).join("");
  }
  const romNum = n => ["Ⅰ", "Ⅱ", "Ⅲ", "Ⅳ", "Ⅴ", "Ⅵ", "Ⅶ"][n - 1] || n;

  // =========================================================== precision view
  const TIERS = [
    { n: 2000, name: ["Quick", "Quick"], t: ["约 5 秒 · 成功率±~0.5pp", "~5 s · success ±~0.5pp"] },
    { n: 10000, name: ["Standard", "Standard"], t: ["约 30 秒 · ±~0.2pp", "~30 s · ±~0.2pp"] },
    { n: 30000, name: ["Deep", "Deep"], t: ["约 10 秒 · 并行 · ±~0.13pp", "~10 s · parallel · ±~0.13pp"] },
    { n: 100000, name: ["Official", "Official"], t: ["约 30 秒 · 并行 · ±~0.07pp", "~30 s · parallel · ±~0.07pp"] },
  ];
  function buildPrecision() {
    $("precGrid").innerHTML = TIERS.map(x =>
      `<button class="prec-card${state.paths === x.n ? " sel" : ""}" data-n="${x.n}">
        <div class="prec-name">${x.name[L === "zh" ? 0 : 1]}</div>
        <div class="prec-n mono">${x.n.toLocaleString()}</div>
        <div class="prec-t">${x.t[L === "zh" ? 0 : 1]}</div></button>`).join("");
    $("precGrid").querySelectorAll(".prec-card").forEach(b =>
      b.addEventListener("click", () => { state.paths = +b.dataset.n; buildPrecision(); }));
  }

  // =========================================================== computing (progress)
  const FACTS = [
    [["为什么是 Guyton-Klinger？", "Why Guyton-Klinger?"], ["固定 4% 提取在坏序列下会破产、好序列下过度节俭。GK 用护栏动态调节：市场差时少花、好时多花——把破产风险换成消费波动。", "A fixed 4% rule goes broke in bad sequences and over-saves in good ones. GK adjusts inside guardrails — spend less in bad markets, more in good — trading ruin risk for spending variance."]],
    [["三分支成功语义", "Three-branch success"], ["「到达 FI 且终身偿付」或「退休前身故」都算成功——把'没攒够'与'没活到'分开，比单一成功率诚实。", "Reached FI & stayed solvent, or died before retiring — separating 'didn't save enough' from 'didn't live to see it' is more honest than one blended rate."]],
    [["精度 ≠ 准确", "Precision ≠ accuracy"], ["更多路径让分位数更稳，但真实的不确定性来自收益率假设本身——跑完后去「敏感性」页看看。", "More paths steady the percentiles, but the real uncertainty is the return assumption itself — check the Sensitivity page after."]],
    [["现金流对账", "Cash accounting"], ["结构化收入实际到账的年份按到手现金精确对账；无到账的成功年份保留每年不超过 $1 的历史提取容差，超过容差的现金缺口会判为失败。", "Years with an actual structured-income receipt reconcile to delivered cash; successful no-receipt years retain at most $1 of historical withdrawal tolerance, and larger cash gaps fail."]],
    [["现金流如何入账", "How cash flows"], ["子女/购房/自定义事件编译成事件现金流；养老金/租金/兼职/RSU 走结构化税后现金通道，退休后先抵消费——和你手填的每一笔一致。", "Children, housing, and custom items compile into event cash flows; pension, rental, part-time, and RSU use a structured after-tax cash channel that covers retirement spending first — exactly as entered."]],
  ];
  let factTimer = null, factIdx = 0;
  function startFacts() {
    clearInterval(factTimer); factIdx = Math.floor(Math.random() * FACTS.length);
    const show = () => { const f = FACTS[factIdx % FACTS.length]; factIdx++;
      $("computeFact").innerHTML = `<div class="cf-t">${f[0][L === "zh" ? 0 : 1]}</div>${f[1][L === "zh" ? 0 : 1]}`; };
    show(); factTimer = setInterval(show, 7000);
  }
  function stopFacts() { clearInterval(factTimer); $("computeFact").innerHTML = ""; }

  // Filled by the panels that already fetch them, so the report carries
  // exactly what the screen showed rather than re-deriving it.
  let PERSONAL_LIMITATIONS = [];

  const OPTIONAL_CLEAR_IDS = [
    "fanDrillChart", "fanDrillLegend", "fanDrillCap", "termDrillCards", "termDrillCap",
    "storyChart", "storyLog", "storyCap", "fireSolver", "tornChart", "ruChart", "tornCap", "ruCap",
    "swrChart", "swrLegend", "swrCap", "claimChart", "claimTable", "claimCap", "rothChart",
    "rothLegend", "rothReadout", "rothCap", "stratTable", "stratCap", "btConsChart", "btLegend",
    "btReadout", "btCap", "hzChart", "hzLegend", "hzTable", "hzCap", "gsMap", "gsXAxis",
    "gsYAxis", "gsLegend", "gsReadout", "gsCap", "efChart", "efLegend", "efReadout", "efCap",
    "robustOut", "liveCards", "liveCap", "liveHint", "liveSliders", "fwdReadout", "fwdHist", "fwdFan", "kStat",
    // Phase 3/4 panels. The list stopped growing when they were added, so a
    // re-run left four containers showing the PREVIOUS run's numbers -- no
    // error, no stale marker, just last time's answer under this time's
    // inputs. `tests/test_ui_server_seams.py` now derives this set from the
    // page's own `<panel>Run`/`<panel>Out` convention rather than trusting
    // that whoever adds the next panel remembers this line.
    "frOut", "rschOut", "alocOut", "beqOut", "decReviewOut"
  ];
  const OPTIONAL_HINT_IDS = ["fanDrillHint", "storyHint", "sensHint", "claimCap", "rothHint", "stratHint", "hzHint", "gsHint", "efHint", "decReviewHint"];
  function clearOptionalResults() {
    state.od = { sens: null, swr: null, claim: null, bt: null, solver: null, roth: null, strat: null, hz: null };
    state.solving = false;
    clearInterval(GS.poll); GS.poll = null; GS.job = null; GS.data = null;
    clearInterval(EF.poll); EF.poll = null; EF.job = null; EF.data = null;
    STORY.data = null; STORY.busy = false; STORY.reroll = 0;
    clearTimeout(LV.timer); LV.ver++; LV.open = false; LV.base = null; LV.overrides = {}; LV.inflight = false;
    state._fwdInit = false;
    CSVI.data = null; SSAI.data = null; SSAI.text = null;
    OPTIONAL_CLEAR_IDS.forEach(id => { const el = $(id); if (el) el.innerHTML = ""; });
    OPTIONAL_HINT_IDS.forEach(id => { const el = $(id); if (el) el.textContent = ""; });
    ["fanDrill", "termDrill", "storyTabs", "storyReroll", "gsMapWrap"].forEach(id => { const el = $(id); if (el) el.style.display = "none"; });
    if ($("liveBody")) $("liveBody").classList.add("hidden");
    if ($("liveToggle")) $("liveToggle").textContent = t("live.open");
    ["sensRun", "swrRun", "claimRun", "rothRun", "stratRun", "btRun", "hzRun", "fanDrillBtn", "storyRun", "gsRun", "efRun", "robustBtn"].forEach(id => {
      const el = $(id); if (el) { el.disabled = false; el.classList.remove("loading"); }
    });
    document.querySelectorAll(".chart.loading").forEach(el => el.classList.remove("loading"));
    if ($("swrRun")) $("swrRun").textContent = t("stress.swr.run");
    if ($("claimRun")) $("claimRun").textContent = t("stress.claim.run");
    if ($("btRun")) $("btRun").textContent = t("stress.bt.run");
    if ($("beqRun") && !$("beqRun").disabled) $("beqRun").textContent = t("beq.run");
    const termHint = $("termHint"); if (termHint) termHint.textContent = t("drill.term.hint");
  }

  async function runJob() {
    const revision = ++state.revision;
    clearInterval(state.poll); state.poll = null; state.job = null;
    clearOptionalResults();
    goto("computing");
    $("computePct").textContent = "0%"; $("progressFill").style.width = "0%";
    $("computeStage").textContent = t("compute.init");
    $("computeMeta").textContent = `${state.paths.toLocaleString()} paths · seed ${state.seed || 96000}`;
    $("computePaths").textContent = "";
    state._prog = [];
    const reloc = !!(get(state.config, "relocation.enabled"));
    const dn = state.paths < 3000 ? 800 : 1500;
    state._workTotal = (state.paths + dn) * (reloc ? 2 : 1);
    startFacts();
    try {
      const formal = state.paths === 10000 || state.paths === 100000;
      const body = { config: state.config, paths: state.paths, seed: state.seed || 96000,
                     dist_paths: state.paths < 3000 ? 800 : 1500 };
      if (formal) {
        body.archive = true;
        body.request_id = newArchiveRequestId();
        // Mirrors server/app.py ARCHIVE_REF_RE, underscores included: migrated
        // plans are `plan_mig_<hex>`, and excluding `_` here silently dropped
        // `plan_id` from the request so a run against a migrated Plan forked a
        // new one. tests/test_regression.py pins the two against each other.
        if (state.archiveRef && /^plan_[A-Za-z0-9_]{16,80}$/.test(state.archiveRef.plan_id || "")) {
          body.plan_id = state.archiveRef.plan_id;
        }
        // The UI deliberately sends only plan_id on a normal follow-up run.
        // A changed config must create a child PlanVersion; an exact-version
        // replay remains an explicit server/API contract, not an accidental UI path.
      }
      let r;
      try {
        r = await postJSON("/api/run_start", body);
      } catch (e) {
        // A formal run has a durable request id, so a transport failure can
        // be retried without starting a second engine run.  Do not retry
        // synchronous validation/conflict responses.
        if (!formal || e.stale || (e.httpStatus && e.httpStatus < 500)) throw e;
        await new Promise(resolve => setTimeout(resolve, 250));
        r = await postJSON("/api/run_start", body);
      }
      if (formal && r.archive) rememberArchiveContext(r.archive);
      if (revision !== state.revision) return;
      state.job = r.job;
      state.poll = setInterval(() => pollJob(revision, r.job), 450);
    } catch (e) { if (revision === state.revision) { toast(e.message, true); goto("precision"); } }
  }
  const STAGE_LABEL = {
    run_home: ["计算 · 本土", "Computing · home"], run_reloc: ["计算 · 搬迁", "Computing · relocation"],
    dist_home: ["分布采样 · 本土", "Distribution sample · home"], dist_reloc: ["分布采样 · 搬迁", "Distribution sample · relocation"],
    done: ["完成", "Done"], init: ["准备中…", "Preparing…"], cancelled: ["已取消", "Cancelled"],
  };
  async function fetchJSONRetry(url, tries, gapMs) {
    let last;
    for (let i = 0; i < tries; i++) {
      try { return await (await fetch(url)).json(); }
      catch (e) { last = e; await new Promise(r => setTimeout(r, gapMs || 400)); }
    }
    throw last;
  }
  async function pollJob(revision, job) {
    if (revision !== state.revision) return;
    try {
      const j = await (await fetch("/api/progress?job=" + job)).json();
      if (revision !== state.revision) return;
      state._pollErrs = 0;
      if (j.error === "cancelled") { clearInterval(state.poll); toast(tt("已取消", "Cancelled")); goto("precision"); return; }
      if (j.error) throw new Error(j.error);
      const p = Math.round((j.pct || 0) * 100);
      $("computePct").textContent = p + "%"; $("progressFill").style.width = p + "%";
      state._prog.push({ t: performance.now(), pct: j.pct || 0 });
      if (state._prog.length > 40) state._prog.shift();
      const done = Math.round((j.pct || 0) * (state._workTotal || 0));
      let eta = "";
      if (state._prog.length >= 4 && j.pct > 0.02 && j.pct < 0.995) {
        const a0 = state._prog[0], a1 = state._prog[state._prog.length - 1];
        const rate = (a1.pct - a0.pct) / Math.max(1, a1.t - a0.t);
        if (rate > 0) { const secs = Math.round((1 - j.pct) / rate / 1000); eta = ` · ≈${secs}s ${tt("剩余", "left")}`; }
      }
      $("computePaths").textContent = tt(`已模拟 ${done.toLocaleString()} / ${(state._workTotal || 0).toLocaleString()} 条人生轨迹`, `${done.toLocaleString()} / ${(state._workTotal || 0).toLocaleString()} life paths simulated`) + eta;
      $("computeStage").textContent = (STAGE_LABEL[j.stage] || [j.stage, j.stage])[L === "zh" ? 0 : 1];
      if (j.done) {
        clearInterval(state.poll);
        const res = await fetchJSONRetry("/api/result?job=" + job, 4, 500);
        if (revision !== state.revision) return;
        if (res.error) throw new Error(res.error);
        stopFacts();
        state.data = res;
        state._fanAnimData = null;
        state._verdictCounted = false;   // §5.8: count up once on this first results render
        goto("results"); showPage("overview");
      }
    } catch (e) {
      if (revision !== state.revision) return;
      // A single dropped localhost fetch (WKWebView stale keep-alive, wake from
      // sleep) must not abort a 30s compute — skip the tick unless persistent.
      state._pollErrs = (state._pollErrs || 0) + 1;
      if (state._pollErrs < 8 && !String(e.message || "").match(/cancelled/)) return;
      clearInterval(state.poll); toast(e.message, true); goto("precision");
    }
  }

  function cancelJob() {
    stopFacts();
    if (state.job) postJSON("/api/cancel", { job: state.job }).catch(() => {});
  }
  async function quitApp() {
    if (!confirm(tt("退出 FIRE Modeling？本地服务将停止。", "Quit FIRE Modeling? The local server will stop."))) return;
    try { await postJSON("/api/shutdown", {}); } catch (e) {}
    document.body.innerHTML = `<div style="font-family:var(--sans),sans-serif;padding:90px 20px;text-align:center;color:#6B645B;font-size:15px">${tt("已退出。可以关闭此标签页。", "Quit. You can close this tab.")}</div>`;
  }

  // =========================================================== results: tabs + pages
  function resultTabs() {
    const tabs = [["overview", tt("概览", "Overview")], ["trajectory", tt("轨迹", "Trajectory")], ["dist", tt("分布", "Distributions")], ["stress", tt("敏感性 & 压力", "Sensitivity & stress")]];
    if (state.data && state.data.relocation) tabs.push(["reloc", tt("搬迁对比", "Relocation")]);
    if (state.slots.A && state.slots.B) tabs.push(["ab", "A/B"]);
    // The review needs an archived PLAN, not an archived run in this session:
    // the forecast being reviewed is last year's, and it is chosen from the
    // archive. `archiveRefForReview()` is what says a plan exists to look in.
    if (archiveRefForReview()) tabs.push(["review", tt("年度复核", "Annual review")]);
    // Phase 3. Needs a result to decide *against*: the study compares
    // alternatives to the plan that is on screen, so without one there is no
    // baseline and the tab would open onto a form that cannot be submitted.
    if (state.data) tabs.push(["decide", tt("决策", "Decide")]);
    tabs.push(["concl", tt("结论", "Conclusions")]);
    $("saveA").textContent = state.slots.A ? "A ✓" : tt("存为 A", "Save A");
    $("saveB").textContent = state.slots.B ? "B ✓" : tt("存为 B", "Save B");
    const kpi = state.data ? `<span class="rtab-kpi mono">${pct(state.data.home.lifetime_success, 1)} · FIRE ${state.data.home.fire_age.p50 != null ? Math.round(state.data.home.fire_age.p50) : "—"}</span>` : "";
    $("resultTabs").innerHTML = tabs.map(([k, la]) => `<button class="rtab${state.page === k ? " active" : ""}" role="tab" aria-selected="${state.page === k}" data-p="${k}">${la}</button>`).join("") + kpi;
    $("resultTabs").querySelectorAll(".rtab").forEach(b => b.addEventListener("click", () => showPage(b.dataset.p)));
    edgeFade($("resultTabs"));
  }
  function showPage(p) {
    state.page = p; resultTabs();
    document.querySelectorAll(".rpage").forEach(e => e.classList.remove("show"));
    const map = { overview: "rp-overview", trajectory: "rp-trajectory", dist: "rp-dist", stress: "rp-stress", reloc: "rp-reloc", ab: "rp-ab", review: "rp-review", decide: "rp-decide", concl: "rp-concl" };
    const el = $(map[p]); if (el) el.classList.add("show");
    window.scrollTo(0, 0);
    renderPageNext(p);
    if (p === "overview") {
      renderVerdict(); renderRulePackStatus(); renderInputsRecap(); renderCore(); initLivePanel();
      // §5.8 reveal, once per computation: the verdict sentence AND the hero cards. Previously
      // only the verdict's <b> animated, so the gauge % and the big card figures sat static.
      if (!state._verdictCounted) {
        state._verdictCounted = true;
        document.querySelectorAll("#rp-overview .v-main b, #rp-overview .hcard .big, #rp-overview .metric .val").forEach(countUp);
      }
    }
    else if (p === "ab") renderAB();
    else if (p === "trajectory") { renderFan(); renderSolverCard(); renderCiTable(); if ($("ciAge") && !$("ciAge").value) $("ciAge").value = +get(state.config, "state.start_age") || 30; if (!state._fwdInit) { resetFwd(); state._fwdInit = true; } }
    else if (p === "dist") { renderTerm(); renderCons(); renderMileDist(); initStoryPanel(); renderStory(); }
    else if (p === "stress") { renderSensPanel(); renderSwrPanel(); renderClaimPanel(); renderBtPanel(); renderRothPanel(); initGoalseekPanel(); initFrontierPanel(); initHousingPanel(); renderStrategiesPanel(); renderGoalseek(); renderFrontier(); renderHousing(); }
    else if (p === "reloc") renderCompare();
    else if (p === "review") { renderReviewForm(); loadReviewForecasts(); renderReviewHistory(); }
    else if (p === "decide") { renderDecideSetup(); renderAnnSetup(); }
    else if (p === "concl") { renderRulePackStatus(); renderHonesty(); renderConclusions(); renderLimitations(); }
    repositionSegments(); // fanUnit/termUnit/storyTabs may have just become visible
    paintAllSliders();    // C4: paint sliders rendered in this page
    measureChrome();      // topbar height can differ once results chrome (restart btn) is present
    updateChromeScroll(); // refresh the collapsed large-title echo for the new page
  }

  // §5.8 gate: each keyed chart reveal animates ONCE PER RESULT OBJECT. Tab returns, cursor
  // drags and language switches re-render with the same object and must not replay the show —
  // a reveal narrates "new data arrived", nothing else.
  function animOnce(key, obj) {
    if (obj == null) return false;
    state._animSeen = state._animSeen || {};
    if (state._animSeen[key] === obj) return false;
    state._animSeen[key] = obj;
    return true;
  }

  // Block entrance for on-demand answers: the WHOLE result unit (chart, axes, legend, caption,
  // readouts) rises into place together, then the marks draw within it. Without this only the
  // bars/lines animated while the surrounding block popped — the pop was what read as abrupt.
  // odEnter now fades + rises (foreground-triggered, so the background-tab-invisibility rule that
  // keeps viewEnter transform-only doesn't apply). Cleanup clears the inline animation back to the
  // element's natural state (opacity:1); a fallback timer guarantees that even if animationend is
  // missed (throttled tab), the block can never be stranded faded.
  function riseIn() {
    if (window.Motion && Motion.prefersReducedMotion()) return;
    let i = 0;
    for (const el of arguments) {
      if (!el) continue;
      const delay = i++ * 70;
      el.style.animation = "none"; void el.offsetWidth;   // restart cleanly on re-runs
      el.style.animation = "odEnter .32s cubic-bezier(.32,.72,0,1) " + delay + "ms both";
      const done = () => { el.style.animation = ""; el.removeEventListener("animationend", onEnd); clearTimeout(el._riseT); };
      const onEnd = e => { if (e.target === el) done(); };
      el.addEventListener("animationend", onEnd);
      clearTimeout(el._riseT);
      el._riseT = setTimeout(done, delay + 500);          // safety net: never leave it faded
    }
  }

  // Slim progress strip for on-demand runs. Jobs that report a real pct get a determinate
  // fill; one-shot requests get an honest indeterminate slide — no fabricated percentages
  // in a finance app. The strip lives just under the Run button's row and leaves when the
  // run settles (done, cancelled, or failed).
  function odProgress(btn, v) {
    if (!btn) return;
    const row = btn.parentElement; if (!row) return;
    let bar = row._odBar;
    if (v == null || v === false) { if (bar) { bar.remove(); row._odBar = null; } return; }
    if (!bar) {
      bar = document.createElement("div"); bar.className = "od-progress";
      bar.appendChild(document.createElement("i"));
      row.parentNode.insertBefore(bar, row.nextSibling); row._odBar = bar;
    }
    if (v === true) { bar.classList.add("ind"); bar.firstChild.style.width = ""; }
    else { bar.classList.remove("ind"); bar.firstChild.style.width = Math.round(Math.max(0, Math.min(1, v)) * 100) + "%"; }
  }

  // §5.8 count-up: tween a number element 0→its value, preserving format. The final value is
  // already in the DOM (correct); this only overlays via rAF, so a paused frame or reduced-motion
  // leaves the exact value untouched — never a wrong number.
  function countUp(el, idx) {
    if (window.Motion && Motion.prefersReducedMotion()) return;
    // The true final value: normally the current text — but if a LIVE tween still owns this
    // element (its last write is what's on screen), the current text is mid-climb, and locking
    // it in as the target would freeze a partial number as "the result". Recover the real one.
    // (A re-render that changed the text breaks the countupLast match, so fresh content wins.)
    const live = el.dataset.countupOrig != null && el.textContent === el.dataset.countupLast;
    const orig = live ? el.dataset.countupOrig : el.textContent;
    const m = orig.match(/^(\D*?)([\d,]+(?:\.\d+)?)(.*)$/);
    if (!m) return;
    const prefix = m[1], numStr = m[2], suffix = m[3];
    const target = parseFloat(numStr.replace(/,/g, ""));
    if (!isFinite(target)) return;
    const hadComma = numStr.indexOf(",") >= 0, decimals = (numStr.split(".")[1] || "").length;
    // Fixed comma grouping, NOT toLocaleString: the element's original text was formatted with
    // commas, and the tween must reproduce that byte-for-byte — a locale that groups with
    // spaces/periods (or in 万) would make the number flicker through foreign formatting.
    const group = s => s.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    const fmt = v => { let s = v.toFixed(decimals); if (hadComma) { const p = s.split("."); p[0] = group(p[0]); s = p.join("."); } return prefix + s + suffix; };
    // Cancellation token: each countUp on an element bumps the generation; any older tween's
    // next frame sees the mismatch and stops dead. Before this, correctness rested on three
    // coincidences (once-per-computation flag, full re-render replacing the node, and the old
    // loop finishing first) — now a stale loop can never fight the current text.
    const gen = String((+el.dataset.countupGen || 0) + 1);
    el.dataset.countupGen = gen;
    el.dataset.countupOrig = orig;                        // the value this tween is climbing toward
    const write = s => { el.dataset.countupLast = s; el.textContent = s; };
    // 600ms was too quick to register; a longer ramp + a small stagger per figure makes the
    // verdict read as a deliberate reveal instead of a flicker you miss.
    const dur = 900, t0 = performance.now() + (idx || 0) * 70;
    const step = now => {
      if (el.dataset.countupGen !== gen) return;         // superseded — let the newer run own the text
      // Zeroing happens INSIDE rAF on purpose: if rAF never runs (paused tab / starved), the
      // element is never touched and keeps its correct final value. Never a wrong number.
      if (now < t0) { write(fmt(0)); requestAnimationFrame(step); return; }
      // ease-in-out, not ease-out: ease-out cubic is so front-loaded it reaches 87% of the
      // value by the halfway point, so the climb was over before you could register it.
      const t = Math.min(1, (now - t0) / dur);
      const e = t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
      write(fmt(target * e));
      if (t < 1) requestAnimationFrame(step);
      else { el.textContent = orig; delete el.dataset.countupOrig; delete el.dataset.countupLast; } // restore exact original
    };
    requestAnimationFrame(step);
  }

  // Read-only recap of the inputs behind this result — so you can sanity-check what you told the
  // model without leaving the results. Reads state.config; every label goes through tt() so it
  // re-renders on a language switch (the overview branch of showPage re-runs this).
  function renderInputsRecap() {
    const host = $("inputsRecapBody"); if (!host) return;
    const g = p => +get(state.config, p) || 0;
    const on = p => !!get(state.config, p);
    const full = v => (v < 0 ? "-" : "") + "$" + Math.round(Math.abs(v)).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    const yr = n => tt(n + " 岁", "age " + n);
    const income = g("contributions.base_salary_pre") + g("contributions.bonus_pre") + g("contributions.ot_income_pre");
    const spouseInc = on("household.enabled") ? g("household.spouse_base_salary_pre") + g("household.spouse_bonus_pre") : 0;
    const assets = ["initial.pretax_401k", "initial.roth_ira", "initial.hsa", "initial.taxable"].map(g);
    const assetTotal = assets.reduce((a, b) => a + b, 0);
    const row = (k, v) => v == null ? "" : `<div class="recap-row"><span class="rk">${k}</span><span class="rv">${v}</span></div>`;
    const sec = (title, rows) => { const body = rows.filter(Boolean).join(""); return body ? `<div class="recap-sec"><div class="recap-h">${title}</div>${body}</div>` : ""; };

    const spendNow = g("contributions.annual_spending_now");
    const now = sec(tt("现在", "Today"), [
      row(tt("年龄", "Age"), yr(g("state.start_age"))),
      // 0 means the field was left unset in this config — skip rather than show a misleading $0
      row(tt("税前年收入", "Gross income / yr"), income > 0 ? full(income) + (spouseInc ? " + " + full(spouseInc) + tt("（配偶）", " (spouse)") : "") : null),
      row(tt("当前年开销", "Spending now / yr"), spendNow > 0 ? full(spendNow) : null),
    ]);
    const asset = sec(tt("投资资产", "Investable assets"), [
      row(tt("合计", "Total"), full(assetTotal)),
      row("401(k) / IRA·pre", full(assets[0])),
      row("Roth", full(assets[1])),
      row("HSA", full(assets[2])),
      row(tt("应税账户", "Taxable"), full(assets[3])),
    ]);
    const ret = sec(tt("退休假设", "Retirement assumptions"), [
      row(tt("目标年开销", "Target spend / yr"), full(g("state.expenses_y0"))),
      row(tt("安全提取率", "Safe withdrawal rate"), (g("state.swr_pref") * 100).toFixed(2).replace(/\.?0+$/, "") + "%"),
      row(tt("最长工作年数", "Max work years"), g("state.accum_years") || "—"),
      row(tt("退休期年数", "Retirement horizon"), g("state.retire_horizon") || "—"),
    ]);
    const extras = [];
    if (on("social_security.enabled")) extras.push(row(tt("社保", "Social Security"), tt("领取 ", "claim ") + yr(g("social_security.claim_age"))));
    if (on("income_streams.pension_enabled")) extras.push(row(tt("养老金", "Pension"), full(g("income_streams.pension_annual_real")) + tt("/年 · ", "/yr · ") + incomeOwnerLabel(get(state.config, "income_streams.pension_owner"))));
    if (on("income_streams.rental_enabled")) extras.push(row(tt("净租金", "Net rental"), full(g("income_streams.rental_annual_net_real")) + tt("/年 · ", "/yr · ") + incomeOwnerLabel(get(state.config, "income_streams.rental_owner"))));
    if (on("income_streams.parttime_enabled")) extras.push(row(tt("兼职收入", "Part-time"), full(g("income_streams.parttime_annual_real")) + tt("/年 · ", "/yr · ") + incomeOwnerLabel(get(state.config, "income_streams.parttime_owner"))));
    if (on("income_streams.equity_enabled")) extras.push(row(tt("RSU / 股权", "RSU / equity"), full(g("income_streams.equity_annual_real")) + tt("/年 · ", "/yr · ") + incomeOwnerLabel(get(state.config, "income_streams.equity_owner"))));
    if (on("household.enabled")) extras.push(row(tt("家庭", "Household"), tt("含配偶", "with spouse")));
    const extra = sec(tt("附加收入 / 情形", "Extra income / setup"), extras);

    host.innerHTML = `<div class="recap-grid">${now}${asset}${ret}${extra}</div>` +
      `<p class="recap-note">${tt("这些是本次运行所依据的数字。要改，用右上「改参数重跑」。", "The figures this run was built on. To change them, use “Edit & re-run” at the top right.")}</p>`;
  }

  const RULE_PACK_LABELS = {
    us_federal_tax: ["美国联邦所得税", "US federal income tax"],
    medicare_irmaa: ["Medicare IRMAA", "Medicare IRMAA"],
    contribution_limits: ["美国缴款限额", "US contribution limits"],
    aca_marketplace: ["ACA Marketplace", "ACA marketplace"],
    ssa_benefit_rules: ["社保领取规则", "Social Security benefit rules"],
    ssa_statement_import: ["SSA 账单导入", "SSA statement import"],
    us_state_archetypes: ["州所得税原型", "State income-tax archetypes"],
    ssa_trust_fund: ["社保信托基金枯竭", "Social Security trust fund depletion"],
  };
  const RULE_PACK_IDS = new Set(Object.keys(RULE_PACK_LABELS));
  const RULE_PACK_STATUSES = new Set(["current", "stale", "review_required"]);
  const RULE_PACK_COMPONENT_STATUSES = new Set(["current", "stale", "review_required", "not_used_at_run"]);
  const RULE_PACK_REVIEW_STATUSES = new Set(["within_recorded_window", "stale", "review_required"]);
  const RULE_PACK_SOURCES = new Set(["pack", "matches_pack_value", "user_or_legacy_override"]);
  const RULE_PACK_SOURCE_BY_COMPONENT = {
    medicare_irmaa: new Set(["pack"]),
    ssa_statement_import: new Set(["pack"]),
    contribution_limits: new Set(["matches_pack_value", "user_or_legacy_override"]),
    aca_marketplace: new Set(["matches_pack_value", "user_or_legacy_override"]),
    ssa_benefit_rules: new Set(["matches_pack_value", "user_or_legacy_override"]),
    us_federal_tax: RULE_PACK_SOURCES,
    // Read from the pack or not read at all: there is no user-supplied value
    // for an archetype to match or override. Adding a component to the pack
    // without adding it HERE too makes the page reject every receipt and show
    // "vintage unrecorded" -- the server-side list is a separate copy of the
    // same fact, and this round I updated one and not the other.
    us_state_archetypes: new Set(["pack"]),
    // Same reasoning, and the same trap: a Trustees Report projection is read
    // from the pack or not at all. There is no user-supplied depletion year,
    // because a plan able to edit the actuary's number could publish a
    // federal projection nobody made, wearing the pack's vintage.
    ssa_trust_fund: new Set(["pack"]),
  };
  const RULE_PACK_EVALUATION_BASIS = "config_applicability_not_path_instrumentation";
  function validIsoDate(value) {
    if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return false;
    const year = Number(value.slice(0, 4));
    if (year < 1 || year > 9999) return false;
    const parsed = new Date(value + "T00:00:00Z");
    return !Number.isNaN(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value;
  }
  // Purely local date arithmetic — no clock but this machine's, no network.
  // Sorted worst-first so the notice can name the component that is furthest
  // past its date rather than an arbitrary one.
  function rulePackOverdueDays(rp) {
    const today = new Date();
    return (rp && Array.isArray(rp.components) ? rp.components : [])
      .filter(c => c && c.status === "stale" && validIsoDate(c.maintenance_due_on))
      .map(c => ({
        id: c.id,
        days: Math.max(0, Math.round(
          (today - new Date(c.maintenance_due_on + "T00:00:00Z")) / 86400000)),
      }))
      .sort((a, b) => b.days - a.days);
  }

  function isValidRulePackReceipt(rp) {
    if (!rp || typeof rp !== "object" || rp.schema_version !== 1 ||
        rp.delivery !== "offline_embedded" || rp.runtime_network_refresh !== false ||
        typeof rp.pack_id !== "string" || !/^us-offline-[0-9a-f]{16}$/.test(rp.pack_id) ||
        typeof rp.content_sha256 !== "string" || !/^[0-9a-f]{64}$/.test(rp.content_sha256) ||
        !validIsoDate(rp.evaluated_on) || !RULE_PACK_STATUSES.has(rp.status) ||
        rp.pack_id !== `us-offline-${rp.content_sha256.slice(0, 16)}` ||
        rp.conclusion_status !== rp.status || rp.evaluation_basis !== RULE_PACK_EVALUATION_BASIS ||
        !Array.isArray(rp.components) || rp.components.length !== RULE_PACK_IDS.size) return false;
    const ids = rp.components.map(row => row && row.id);
    if (new Set(ids).size !== ids.length ||
        !ids.every(id => typeof id === "string" && RULE_PACK_IDS.has(id))) return false;
    const evaluatedOn = rp.evaluated_on;
    for (const row of rp.components) {
      if (!row || typeof row.label !== "string" || !row.label ||
          typeof row.source_vintage !== "string" || !row.source_vintage ||
          !validIsoDate(row.maintenance_due_on) ||
          !["applicable", "not_used_at_run"].includes(row.applicability) ||
          !RULE_PACK_COMPONENT_STATUSES.has(row.status) ||
          !RULE_PACK_REVIEW_STATUSES.has(row.review_status) ||
          !RULE_PACK_SOURCES.has(row.effective_source) ||
          !Array.isArray(row.mismatched_fields)) return false;
      if ((row.applicability === "applicable" && row.status === "not_used_at_run") ||
          (row.applicability === "not_used_at_run" && row.status !== "not_used_at_run")) return false;
      const sourceSet = RULE_PACK_SOURCE_BY_COMPONENT[row.id];
      if (!sourceSet || !sourceSet.has(row.effective_source)) return false;
      if (!Array.isArray(row.mismatched_fields) ||
          row.mismatched_fields.some(field => typeof field !== "string" || !field) ||
          new Set(row.mismatched_fields).size !== row.mismatched_fields.length) return false;
      const override = row.effective_source === "user_or_legacy_override";
      if (override !== (row.mismatched_fields.length > 0)) return false;
      const pastDue = evaluatedOn > row.maintenance_due_on;
      const expectedReview = pastDue ? "stale" :
        (override ? "review_required" : "within_recorded_window");
      const expectedStatus = row.applicability === "not_used_at_run" ? "not_used_at_run" :
        (pastDue ? "stale" : (override ? "review_required" : "current"));
      if (row.review_status !== expectedReview || row.status !== expectedStatus) return false;
    }
    const idsFor = (predicate) => new Set(rp.components.filter(predicate).map(row => row.id));
    const asSet = (value) => Array.isArray(value) ? new Set(value) : null;
    const sameSet = (a, b) => a && a.size === b.size && [...a].every(id => b.has(id));
    const applicable = asSet(rp.applicable_component_ids);
    const stale = asSet(rp.stale_component_ids);
    const review = asSet(rp.review_required_component_ids);
    const uniqueKnown = (value) => Array.isArray(value) &&
      value.every(id => typeof id === "string" && RULE_PACK_IDS.has(id)) &&
      new Set(value).size === value.length;
    if (!uniqueKnown(rp.applicable_component_ids) ||
        !uniqueKnown(rp.stale_component_ids) ||
        !uniqueKnown(rp.review_required_component_ids)) return false;
    if (!sameSet(applicable, idsFor(row => row.applicability === "applicable")) ||
        !sameSet(stale, idsFor(row => row.applicability === "applicable" && row.status === "stale")) ||
        !sameSet(review, idsFor(row => row.applicability === "applicable" && row.status === "review_required"))) return false;
    const expectedOverall = stale.size ? "stale" : (review.size ? "review_required" : "current");
    if (rp.status !== expectedOverall || rp.conclusion_status !== expectedOverall) return false;
    return true;
  }
  function isValidSsaRulePackReceipt(rp) {
    const c = rp && rp.component;
    return !!(rp && typeof rp === "object" &&
      typeof rp.pack_id === "string" && /^us-offline-[0-9a-f]{16}$/.test(rp.pack_id) &&
      typeof rp.content_sha256 === "string" && /^[0-9a-f]{64}$/.test(rp.content_sha256) &&
      rp.pack_id === `us-offline-${rp.content_sha256.slice(0, 16)}` &&
      validIsoDate(rp.evaluated_on) && c && typeof c === "object" &&
      c.id === "ssa_statement_import" && typeof c.label === "string" && !!c.label &&
      typeof c.source_vintage === "string" && !!c.source_vintage &&
      validIsoDate(c.maintenance_due_on) &&
      c.status === (rp.evaluated_on > c.maintenance_due_on ? "stale" : "current"));
  }
  function renderRulePackStatus() {
    const targets = [$("rulePackStatus"), $("rulePackConclusionStatus")].filter(Boolean);
    if (!targets.length) return;
    const rp = state.data && state.data.meta && state.data.meta.rule_pack;
    let tone = "warn", title, body;
    if (!isValidRulePackReceipt(rp)) {
      title = tt("历史结果 · 规则年份未记录", "Legacy result · rule vintage unrecorded");
      body = tt(
        "这份结果早于离线 rule-pack 合同；应用不会按今天的日期猜测它用了哪一版规则。重要决定前请用当前版本重跑。",
        "This result predates the offline rule-pack contract. The app will not guess its vintage using today’s date; re-run in a current build before an important decision.");
    } else {
      const relevant = rp.components.filter(row => row.applicability === "applicable");
      const list = relevant.map(row => {
        const label = (RULE_PACK_LABELS[row.id] || [row.label || row.id, row.label || row.id])[L === "zh" ? 0 : 1];
        return `${esc(label)} <span class="data">${esc(row.source_vintage || "—")}</span>`;
      }).join(" · ");
      const receipt = `<span class="data">${esc(rp.pack_id)}</span> · ${tt("评估于", "evaluated")} <span class="data">${esc(rp.evaluated_on || "—")}</span>`;
      if (rp.status === "stale") {
        // ROADMAP asks for the stale notice to become a concrete action, not a
        // reminder. Two things make it concrete: how far past the date each
        // component is, and where the reader can change the figure themselves.
        //
        // What it deliberately does NOT say is "a new pack is available". The
        // app makes no network requests, so it cannot know that — and runtime
        // pack import is deferred with multi-vintage coexistence, so there is
        // nothing to import even if it did. Naming an action that does not
        // exist is worse than naming a smaller one that does.
        const overdue = rulePackOverdueDays(rp);
        const worst = overdue.length ? overdue[0] : null;
        title = tt("离线规则已过应用维护期 · 正式结论标为 stale", "Offline rules are past the app review date · conclusion marked stale");
        body = tt(
          `本次运行可能受这些年度规则影响：${list || "—"}${worst ? `（最久的一项 <b>${esc(worst.id)}</b> 已过期 ${worst.days} 天）` : ""}。`
          + `<b>可以做的：</b>在「高级」里按当年官方数字直接改写受影响的项 —— 改过的值会标为 <span class="data">user_or_legacy_override</span> 并如实进入结论。`
          + `本应用不联网，因此<b>无法知道</b>是否存在更新的数字，也不会替你去取。${receipt}`,
          `This run may be affected by: ${list || "—"}${worst ? ` (the oldest, <b>${esc(worst.id)}</b>, is ${worst.days} days past)` : ""}. `
          + `<b>What you can do:</b> override the affected figures with this year's official numbers under Advanced — an edited value is marked <span class="data">user_or_legacy_override</span> and carried honestly into the conclusion. `
          + `This app makes no network requests, so it <b>cannot know</b> whether newer figures exist, and will not fetch them for you. ${receipt}`);
      } else if (rp.status === "review_required") {
        title = tt("计划值与当前 rule pack 不同 · 需要复核", "Plan values differ from this rule pack · review required");
        body = tt(
          `差异可能是你主动修改的，也可能是旧版本默认值；应用不会猜来源。${list || "—"}。${receipt}`,
          `A difference may be an intentional override or a legacy default; the app will not guess which. ${list || "—"}. ${receipt}`);
      } else {
        tone = "";
        title = tt("离线规则仍在应用维护窗口内", "Offline rules are within the app review window");
        body = tt(
          `${list || "—"}。这只表示未超过应用的复核期限，不代表税务核验。${receipt}`,
          `${list || "—"}. This means only that the app’s review date has not passed; it is not tax verification. ${receipt}`);
      }
    }
    targets.forEach(host => {
      host.className = "callout" + (tone ? " " + tone : "");
      host.innerHTML = `<h5><span class="pill">${tt("RULE PACK", "RULE PACK")}</span><span>${title}</span></h5><p>${body}</p>`;
    });
  }

  //: Rich / Broke / Dead. The three-branch rule is the thing readers
  //: misread most: "dying before the money runs out counts as success" is
  //: correct and sounds like a trick, because the alternative counts every
  //: death as a failure. On most plans the headline rate turns out to be
  //: carried by mortality rather than by the portfolio outlasting anything,
  //: and there was previously no way to see that from the number.
  const OUTCOME_WORDS = {
    alive: ["活着，且钱没花完", "Alive, with money left"],
    broke: ["钱先花完了", "Ran out of money"],
    dead: ["人先走了，钱还在", "Died first, money remained"],
    dead_in_accumulation: ["在积累期身故", "Died during accumulation"],
    never_reached_fi: ["从未达到财务独立", "Never reached FI"],
  };

  function renderOutcomeLayers() {
    const host = $("outcomeLayers");
    if (!host) return;
    // From `meta`, not from the engine block: `home` is pinned bit-identical
    // by five suites and a derived view does not belong inside it.
    const v = ((state.data && state.data.meta
                && state.data.meta.outcome_layers) || {}).home || null;
    if (!v || !v.applicable) {
      host.innerHTML = v && v.reason
        ? `<p class="cap">${esc(v.reason)}</p>` : "";
      return;
    }
    const order = ["alive", "dead", "broke", "dead_in_accumulation",
                   "never_reached_fi"];
    const rows = order.filter(k => v.layers[k]).map(k => {
      const row = v.layers[k];
      const words = OUTCOME_WORDS[k] || [k, k];
      const age = row.median_age == null
        ? tt("——", "—")
        : tt(`中位 ${row.median_age} 岁`, `median age ${row.median_age}`);
      return `<tr><td>${esc(words[L === "zh" ? 0 : 1])}</td>` +
        `<td>${pct(row.share, 1)}</td><td>${row.paths.toLocaleString()}</td>` +
        `<td>${age}</td></tr>`;
    }).join("");
    host.innerHTML =
      `<div class="panel"><div class="panel-title sm">${tt(
        "这 " + v.n_paths.toLocaleString() + " 条路径最后怎么样了",
        "How these " + v.n_paths.toLocaleString() + " paths ended")}</div>` +
      `<table class="mini"><thead><tr><th>${tt("结局", "Outcome")}</th>` +
      `<th>${tt("占比", "Share")}</th><th>${tt("路径", "Paths")}</th>` +
      `<th>${tt("发生年龄", "Age")}</th></tr></thead><tbody>${rows}</tbody></table>` +
      `<p class="cap">${esc(v.basis)}</p></div>`;
  }

  //: The sampled worlds, not a valuation. ROADMAP asks for CAPE
  //: stratification; this engine has no valuation input at all, and one of
  //: the three sampled regimes happens to be `highCAPE`. The panel says
  //: which of the two it is, because a reader who thinks they are seeing
  //: "my plan at today's valuation" is reading something not computed here.
  const REGIME_WORDS = {
    historical: ["历史序列", "Historical"],
    aiPersists: ["AI 生产力持续", "AI productivity persists"],
    highCAPE: ["高估值起点", "High valuation"],
  };

  function renderRegimeConditional() {
    const host = $("regimeConditional");
    if (!host) return;
    const v = ((state.data && state.data.meta
                && state.data.meta.regime_conditional) || {}).home || null;
    if (!v || !v.applicable) {
      host.innerHTML = v && v.reason ? `<p class="cap">${esc(v.reason)}</p>` : "";
      return;
    }
    const rows = Object.keys(v.regimes).sort().map(k => {
      const row = v.regimes[k];
      const words = REGIME_WORDS[k] || [k, k];
      return `<tr><td>${esc(words[L === "zh" ? 0 : 1])}</td>` +
        `<td>${pct(row.share_of_paths, 1)}</td>` +
        `<td>${row.success_rate == null ? tt("——", "—") : pct(row.success_rate, 1)}</td></tr>`;
    }).join("");
    host.innerHTML =
      `<div class="panel"><div class="panel-title sm">${tt(
        "抽到不同的世界时，这个计划各是什么表现",
        "How the plan did in each sampled world")}</div>` +
      `<table class="mini"><thead><tr><th>${tt("抽到的世界", "Sampled world")}</th>` +
      `<th>${tt("占比", "Share")}</th><th>${tt("该世界内成功率", "Success within it")}</th>` +
      `</tr></thead><tbody>${rows}</tbody></table>` +
      `<p class="cap">${tt(
        "注意：这是<b>抽样抽到的市场世界</b>，不是你填的估值——本模型没有估值输入，每条路径都从同一个组合出发。它回答的是「世界不利时这个计划怎么样」，不是「按今天的估值这个计划怎么样」。仅呈现，不作为规则。",
        "Note: these are the market worlds the sampler drew, <b>not a valuation you entered</b> — this model has no valuation input and every path starts from the same portfolio. It answers \"how did the plan do when the world was unfavourable\", not \"how does it do at today's valuation\". Shown, never applied as a rule.")}</p></div>`;
  }

  function renderVerdict() {
    const s = D(), fa = s.fire_age, mc = s.mean_real_consumption, tr = s.terminal_real;
    const pr = (state.data.meta.protocol || {});
    const el2 = $("verdict");
    const ls = s.lifetime_success;
    // The tier is stated in TEXT as well as in colour.
    //
    // ROADMAP files this under presentation, but calls it what it is: for a
    // colourblind reader a three-tier scale carried by hue alone is an
    // ACCURACY problem, not an aesthetic one. Sage, ochre and terracotta are
    // already gentler than red/amber/green and are still three colours that
    // deuteranopia can flatten into one -- at which point the reader cannot
    // tell which of three states their plan is in, and nothing on screen
    // tells them. `aria-label` carries it for a screen reader; `data-tier`
    // carries it for anything that inspects the page; and the badge below
    // carries it for someone simply looking.
    const tier = ls >= 0.9 ? "strong" : ls >= 0.75 ? "watch" : "fragile";
    const tierText = {
      strong: tt("稳", "Strong"),
      watch: tt("需要留意", "Watch"),
      fragile: tt("脆弱", "Fragile"),
    }[tier];
    el2.className = "verdict" + (tier === "strong" ? ""
                                 : tier === "watch" ? " warn" : " bad");
    el2.setAttribute("data-tier", tier);
    el2.setAttribute("aria-label", tierText);
    if (fa.p50 == null) {
      el2.innerHTML = `<div class="v-tier">${esc(tierText)}</div><div class="v-main">${tt("按当前输入，样本内未能达到财务独立——储蓄与支出的差距太大。", "With these inputs, the sample never reaches financial independence — the gap between savings and spending is too wide.")}</div>
        <div class="v-sub">${tt("试试降低退休支出、提高储蓄，或用「敏感性」页找到最有效的杠杆。", "Try lower retirement spending, higher savings, or find the strongest lever on the Sensitivity page.")}</div>`;
      return;
    }
    const _n = (pr.paths || 0) || 1;
    // The interval comes from the server, exact (Clopper-Pearson), rather
    // than being recomputed here as `sqrt(p(1-p)/n)`.
    //
    // What shipped before was ONE standard error of a normal approximation,
    // labelled "SE" and read by everyone as a confidence interval -- it is
    // about 68%, not 95%, so the honest width was roughly double what the
    // page showed. And the normal approximation is worst exactly where FIRE
    // plans live: near 1.0, where it can reach above 100% and understates
    // the tail that matters. `server/sampling_error.py` is now the only
    // implementation, and the numerics it uses were already validated
    // against closed forms by tests/test_attribution_power.py.
    // From `meta`, not from the engine block: a server-derived value
    // inside the deterministic payload breaks snapshot replay.
    const _ci = ((state.data.meta || {}).sampling_error || {}).home || null;
    const _ciText = (_ci && _ci.applicable)
      ? tt(`95% 区间 ±${_ci.half_width_pp < 0.005 ? "<0.01" : _ci.half_width_pp.toFixed(2)}pp`,
           `95% interval ±${_ci.half_width_pp < 0.005 ? "<0.01" : _ci.half_width_pp.toFixed(2)}pp`)
      : tt("抽样区间未算出", "sampling interval not computed");
    el2.innerHTML = `<div class="v-tier">${esc(tierText)}</div><div class="v-main">${tt(
      `三分支成功率为 <b>${pct(ls, 1)}</b>（FIRE 后偿付率 <b>${pct(s.post_fire_solvency, 1)}</b>）——中位 <b>${Math.round(fa.p50)} 岁</b>${get(state.config, "household.enabled") ? "（以你的年龄计）" : ""}达到财务独立，此后每年可持续消费约 <b>${money(mc.p50)}</b>（今日购买力），并留下中位 <b>${money(tr.p50)}</b> 的实际遗产。`,
      `Three-branch success is <b>${pct(ls, 1)}</b> (post-FIRE solvency <b>${pct(s.post_fire_solvency, 1)}</b>) — financial independence at a median age of <b>${Math.round(fa.p50)}</b>, sustaining about <b>${money(mc.p50)}</b>/yr in today's dollars, leaving a median real estate of <b>${money(tr.p50)}</b>.`)}</div>
      <div class="v-sub">${tt(`${(pr.paths || 0).toLocaleString()} 条路径 · seed ${pr.seed} · 成功率 ${_ciText} · 这是给定假设下的精度，不是对现实的保证——`, `${(pr.paths || 0).toLocaleString()} paths · seed ${pr.seed} · success rate ${_ciText} · precision given the assumptions, not a promise — `)}<a id="verdictSens">${tt("看哪些假设最要命 →", "see which assumptions dominate →")}</a></div>`;
    if (state.quick) {
      const d2 = document.createElement("div");
      d2.className = "v-sub";
      d2.innerHTML = tt("速估口径：代表性账户结构（税前 50/Roth 15/应税 35）+ 默认假设。", "Quick-estimate basis: representative account mix (50/15/35) + default assumptions. ") + ` <a id="quickRefine">${tt("进完整向导精修 →", "Refine in the full wizard →")}</a>`;
      el2.appendChild(d2);
      const qa = $("quickRefine"); if (qa) qa.addEventListener("click", () => { state.quick = false; state.step = 0; goto("wizard"); });
    }
    const a = $("verdictSens"); if (a) a.addEventListener("click", () => showPage("stress"));
    const dp = $("decPlan"); if (dp) dp.addEventListener("click", decidePlan);
    const dr = $("decRun"); if (dr) dr.addEventListener("click", decideRun);
    const drv = $("decReviewRun");
    if (drv) drv.addEventListener("click", runDecisionReview);
    const dc = $("decCancel");
    if (dc) dc.addEventListener("click", async () => {
      // Same cancel channel as every other job; the poll loop sees
      // error === "cancelled" and puts the panel back to idle.
      if (DEC.job) await postJSON("/api/cancel", { job: DEC.job });
    });
    const ap = $("annPlan"); if (ap) ap.addEventListener("click", annuityPlan);
    const ar = $("annRun"); if (ar) ar.addEventListener("click", annuityRun);
    const ac = $("annCancel");
    if (ac) ac.addEventListener("click", async () => {
      await cancelAnnuityOperation();
    });
  }

  // ---------- S1 goal seeker (stress page) ----------
  const GS = { job: null, poll: null, data: null, revision: 0 };
  const GS_METRICS = {
    lifetime_success: { zh: "终身成功率 ≥", en: "lifetime success ≥", unit: "%", to: v => v / 100, from: v => v * 100, def: 95, fmt: v => pct(v, 1) },
    fire_age_p50: { zh: "FIRE 年龄 P50 ≤", en: "FIRE age P50 ≤", unit: ["岁", "yrs"], to: v => v, from: v => v, def: 40, fmt: v => Math.round(v) },
    terminal_real_p50: { zh: "遗产 P50 real ≥", en: "estate P50 real ≥", unit: "$", to: v => v, from: v => v, def: 2000000, fmt: money },
    cons_p50: { zh: "消费 P50 real ≥", en: "spend P50 real ≥", unit: "$", to: v => v, from: v => v, def: 50000, fmt: money },
  };
  const GS_LEVERS = {
    expenses: { zh: "当前年开销", en: "Annual expenses", path: "state.expenses_y0", range: c => { const v = +get(c, "state.expenses_y0") || 45000; return [Math.round(v * 0.6), Math.round(v * 1.2)]; }, fmt: money },
    salary: { zh: "税前收入", en: "Gross salary", path: "contributions.base_salary_pre", range: c => { const v = +get(c, "contributions.base_salary_pre") || 120000; return [Math.round(v * 0.8), Math.round(v * 1.6)]; }, fmt: money },
    swr: { zh: "SWR 偏好", en: "SWR preference", path: "state.swr_pref", range: () => [0.028, 0.055], fmt: v => pct(v, 1) },
    equity: { zh: "退休期股比", en: "Retirement equity %", path: "glide.equity_start", range: () => [0.4, 1.0], fmt: v => pct(v, 0) },
  };
  function gsFillRanges() {
    const kx = $("gsLx").value, ky = $("gsLy").value;
    const [x0, x1] = GS_LEVERS[kx].range(state.config); $("gsLxMin").value = x0; $("gsLxMax").value = x1;
    const [y0, y1] = GS_LEVERS[ky].range(state.config); $("gsLyMin").value = y0; $("gsLyMax").value = y1;
  }
  function gsSyncTargetUnit() {
    const m = GS_METRICS[$("gsMetric").value];
    $("gsTargetUnit").textContent = Array.isArray(m.unit) ? m.unit[L === "zh" ? 0 : 1] : m.unit;
  }
  function gsRefreshOptions() {
    // Rebuilt on EVERY entry/language switch (setLang contract, audited
    // 2026-07-10: built content must re-render or it sticks in the old
    // language). Selected values survive the rebuild.
    const ms = $("gsMetric"), lx = $("gsLx"), ly = $("gsLy");
    const keep = [ms.value, lx.value, ly.value];
    ms.innerHTML = Object.entries(GS_METRICS).map(([k, m]) => `<option value="${k}">${L === "zh" ? m.zh : m.en}</option>`).join("");
    const lopts = Object.entries(GS_LEVERS).map(([k, m]) => `<option value="${k}">${L === "zh" ? m.zh : m.en}</option>`).join("");
    lx.innerHTML = lopts; ly.innerHTML = lopts;
    ms.value = keep[0] || "lifetime_success";
    lx.value = keep[1] || "expenses"; ly.value = keep[2] || "swr";
    gsSyncTargetUnit();
  }
  function initGoalseekPanel() {
    const b = $("gsRun"); if (!b) return;
    gsRefreshOptions();
    if (b._wired) return; b._wired = true;
    $("gsTarget").value = GS_METRICS[$("gsMetric").value].def;
    $("gsMetric").addEventListener("change", () => { $("gsTarget").value = GS_METRICS[$("gsMetric").value].def; gsSyncTargetUnit(); });
    $("gsLx").addEventListener("change", gsFillRanges); $("gsLy").addEventListener("change", gsFillRanges);
    gsFillRanges();
    b.addEventListener("click", runGoalseek);
    $("gsCancel").addEventListener("click", () => { if (GS.job) postJSON("/api/cancel", { job: GS.job }).catch(() => {}); });
  }
  async function runGoalseek() {
    const revision = state.revision;
    const kx = $("gsLx").value, ky = $("gsLy").value;
    if (kx === ky) { toast(tt("两根杠杆必须不同", "Pick two different levers"), true); return; }
    const m = GS_METRICS[$("gsMetric").value];
    const body = {
      config: state.config, seed: state.seed || 96000, paths: 1200, grid: 8,
      goal: { metric: $("gsMetric").value, value: m.to(+$("gsTarget").value) },
      levers: [{ key: kx, min: +$("gsLxMin").value, max: +$("gsLxMax").value },
               { key: ky, min: +$("gsLyMin").value, max: +$("gsLyMax").value }],
    };
    $("gsRun").disabled = true; $("gsCancel").style.display = "";
    $("gsHint").textContent = tt("搜索中… 0%", "searching… 0%");
    odProgress($("gsRun"), 0);                     // job reports real pct — determinate from the start
    try {
      const r = await postJSON("/api/goalseek", body);
      if (revision !== state.revision) return;
      GS.job = r.job;
      GS.revision = revision;
      clearInterval(GS.poll);
      GS.poll = setInterval(pollGoalseek, 600);
    } catch (e) { if (revision === state.revision) { toast(e.message, true); gsIdle(); } }
  }
  function gsIdle() { $("gsRun").disabled = false; $("gsCancel").style.display = "none"; odProgress($("gsRun"), false); }
  async function pollGoalseek() {
    if (GS.revision !== state.revision) { clearInterval(GS.poll); return; }
    try {
      const j = await (await fetch("/api/progress?job=" + GS.job)).json();
      if (GS.revision !== state.revision) return;
      if (j.error === "cancelled") { clearInterval(GS.poll); $("gsHint").textContent = tt("已取消", "cancelled"); gsIdle(); return; }
      if (j.error) throw new Error(j.error);
      $("gsHint").textContent = tt(`搜索中… ${Math.round((j.pct || 0) * 100)}%`, `searching… ${Math.round((j.pct || 0) * 100)}%`);
      odProgress($("gsRun"), j.pct || 0);
      if (j.done) {
        clearInterval(GS.poll);
        const res = await (await fetch("/api/result?job=" + GS.job)).json();
        if (GS.revision !== state.revision) return;
        if (res.error) throw new Error(res.error);
        GS.data = res; renderGoalseek(); $("gsHint").textContent = ""; gsIdle();
      }
    } catch (e) { clearInterval(GS.poll); toast(e.message, true); gsIdle(); }
  }
  function renderGoalseek() {
    const r = GS.data; if (!r) return;
    const kx = r.levers[0].key, ky = r.levers[1].key;
    const fx = GS_LEVERS[kx].fmt, fy = GS_LEVERS[ky].fmt, fm = GS_METRICS[r.goal.metric].fmt;
    const xs = r.levers[0].values, ys = r.levers[1].values, g = xs.length;
    // nearest / current cell indices
    const ci = p => p ? [xs.reduce((b, v, i) => Math.abs(v - p.x) < Math.abs(xs[b] - p.x) ? i : b, 0),
                         ys.reduce((b, v, j) => Math.abs(v - p.y) < Math.abs(ys[b] - p.y) ? j : b, 0)] : null;
    const cur = ci(r.current), near = ci(r.nearest);
    const map = $("gsMap");
    map.style.gridTemplateColumns = `repeat(${g}, 1fr)`;
    let html = "";
    for (let j = g - 1; j >= 0; j--)          // y grows upward
      for (let i = 0; i < g; i++) {
        const ok = r.feasible[j][i];
        const marks = (cur && cur[0] === i && cur[1] === j ? "◉" : "") + (near && near[0] === i && near[1] === j ? "★" : "");
        html += `<div class="gs-cell ${ok ? "ok" : "bad"}" title="${fx(xs[i])} × ${fy(ys[j])} → ${fm(r.z[j][i])}">${marks}</div>`;
      }
    map.innerHTML = html;
    $("gsXAxis").innerHTML = `<span>${fx(xs[0])}</span><span>${fx(xs[Math.floor(g / 2)])}</span><span>${fx(xs[g - 1])}</span>`;
    $("gsYAxis").innerHTML = `<span>${fy(ys[g - 1])}</span><span>${fy(ys[Math.floor(g / 2)])}</span><span>${fy(ys[0])}</span>`;
    $("gsLegend").innerHTML = `<span class="chip"><span class="swl" style="border-color:${CV("--good", "#6F8A5F")}"></span>${tt("可行", "feasible")}</span><span class="chip"><span class="swl" style="border-color:${CV("--bad", "#AF746A")}"></span>${tt("不可行", "infeasible")}</span><span class="chip">◉ ${tt("你在这里", "you are here")}</span><span class="chip">★ ${tt("最近可行点", "nearest feasible")}</span>`;
    $("gsMapWrap").style.display = "";
    const cards = [];
    if (r.current) cards.push([tt("你现在", "You now"), fm(r.current.v), r.current.ok ? "home" : "accent"]);
    if (r.current && r.current.ok) {
      cards.push([tt("结论", "Verdict"), tt("已在可行域内", "already feasible"), "home"]);
    } else if (r.nearest) {
      cards.push([tt("最近可行 · " + (L === "zh" ? GS_LEVERS[kx].zh : GS_LEVERS[kx].en), "Nearest · " + (L === "zh" ? GS_LEVERS[kx].zh : GS_LEVERS[kx].en)), fx(r.nearest.x), "accent"]);
      cards.push([tt("最近可行 · " + (L === "zh" ? GS_LEVERS[ky].zh : GS_LEVERS[ky].en), "Nearest · " + (L === "zh" ? GS_LEVERS[ky].zh : GS_LEVERS[ky].en)), fy(r.nearest.y), "accent"]);
      cards.push([tt("该点指标", "Metric there"), fm(r.nearest.v), "home"]);
    } else {
      cards.push([tt("结论", "Verdict"), tt("范围内无可行组合", "nothing feasible in range"), ""]);
    }
    $("gsReadout").innerHTML = cards.map(([la, v, c2]) => `<div class="readout"><div class="lab">${la}</div><div class="num ${c2}">${v}</div></div>`).join("");
    $("gsCap").innerHTML = tt(
      `${r.n_paths.toLocaleString()} 路径/点 · ${r.evals} 次评估 · seed ${r.seed} · 本国情景。成功率抽样噪声约 ±1pp，边界附近的格子可能翻转——把它当地形图，不当合同。`,
      `${r.n_paths.toLocaleString()} paths/point · ${r.evals} evals · seed ${r.seed} · home scenario. Success-rate noise ≈ ±1pp — cells near the boundary can flip. Read it as terrain, not a contract.`);
    if (animOnce("gs", r)) riseIn($("gsMapWrap"), $("gsReadout"), $("gsCap"));
  }

  // ---------- S2 efficient frontier (stress page) ----------
  const EF = { job: null, poll: null, data: null, revision: 0 };
  function initFrontierPanel() {
    const b = $("efRun"); if (!b || b._wired) return; b._wired = true;
    b.addEventListener("click", runFrontier);
    $("efCancel").addEventListener("click", () => { if (EF.job) postJSON("/api/cancel", { job: EF.job }).catch(() => {}); });
  }
  async function runFrontier() {
    const revision = state.revision;
    $("efRun").disabled = true; $("efCancel").style.display = "";
    $("efHint").textContent = tt("扫描中… 0%", "sweeping… 0%");
    odProgress($("efRun"), 0);
    try {
      const r = await postJSON("/api/frontier", { config: state.config, seed: state.seed || 96000, paths: 1200, grid: 7 });
      if (revision !== state.revision) return;
      EF.job = r.job;
      EF.revision = revision;
      clearInterval(EF.poll);
      EF.poll = setInterval(pollFrontier, 600);
    } catch (e) { if (revision === state.revision) { toast(e.message, true); efIdle(); } }
  }
  function efIdle() { $("efRun").disabled = false; $("efCancel").style.display = "none"; odProgress($("efRun"), false); }
  async function pollFrontier() {
    if (EF.revision !== state.revision) { clearInterval(EF.poll); return; }
    try {
      const j = await (await fetch("/api/progress?job=" + EF.job)).json();
      if (EF.revision !== state.revision) return;
      if (j.error === "cancelled") { clearInterval(EF.poll); $("efHint").textContent = tt("已取消", "cancelled"); efIdle(); return; }
      if (j.error) throw new Error(j.error);
      $("efHint").textContent = tt(`扫描中… ${Math.round((j.pct || 0) * 100)}%`, `sweeping… ${Math.round((j.pct || 0) * 100)}%`);
      odProgress($("efRun"), j.pct || 0);
      if (j.done) {
        clearInterval(EF.poll);
        const res = await (await fetch("/api/result?job=" + EF.job)).json();
        if (EF.revision !== state.revision) return;
        if (res.error) throw new Error(res.error);
        EF.data = res; renderFrontier(); $("efHint").textContent = ""; efIdle();
      }
    } catch (e) { clearInterval(EF.poll); toast(e.message, true); efIdle(); }
  }
  function efColor(s) {
    return s >= 0.95 ? CV("--ch-home", "#2A4A3A") : s >= 0.80 ? CV("--ch-gold", "#8A6420") : CV("--bad", "#9A2A2A");
  }
  function renderFrontier() {
    const r = EF.data; if (!r) return;
    const pts = r.points.filter(p => p.fire_age_p50 != null && p.cons_p50 != null);
    if (!pts.length) { $("efReadout").innerHTML = ""; $("efCap").textContent = tt("网格内没有达到 FI 的组合。", "No combination in the grid reaches FI."); return; }
    const cur = r.current, nf = r.nearest_frontier;
    const all = pts.concat(cur.fire_age_p50 != null ? [cur] : []);
    const xs = all.map(p => p.cons_p50), ys = all.map(p => p.fire_age_p50);
    const x0 = Math.min(...xs), x1 = Math.max(...xs), y0 = Math.min(...ys), y1 = Math.max(...ys);
    const W = 760, H = 330, L2 = 62, R2 = 14, T2 = 12, B2 = 34;
    const X = v => L2 + (v - x0) / Math.max(x1 - x0, 1e-9) * (W - L2 - R2);
    const Y = v => T2 + (v - y0) / Math.max(y1 - y0, 1e-9) * (H - T2 - B2);   // early FIRE (small age) at top
    let svg = "";
    // axes
    svg += `<line x1="${L2}" y1="${H - B2}" x2="${W - R2}" y2="${H - B2}" stroke="var(--rule-medium)"/>`;
    svg += `<line x1="${L2}" y1="${T2}" x2="${L2}" y2="${H - B2}" stroke="var(--rule-medium)"/>`;
    [[x0, 0], [(x0 + x1) / 2, 0.5], [x1, 1]].forEach(([v]) => {
      svg += `<text x="${X(v)}" y="${H - B2 + 18}" text-anchor="middle" font-size="10.5" fill="var(--ink-muted)" font-family="var(--mono)">${money(v)}</text>`;
    });
    [y0, (y0 + y1) / 2, y1].forEach(v => {
      svg += `<text x="${L2 - 8}" y="${Y(v) + 3.5}" text-anchor="end" font-size="10.5" fill="var(--ink-muted)" font-family="var(--mono)">${Math.round(v)}</text>`;
    });
    svg += `<text x="${(L2 + W - R2) / 2}" y="${H - 4}" text-anchor="middle" font-size="10.5" fill="var(--ink-muted)">${tt("年消费 P50（今日购买力）", "annual consumption P50 (today's $)")}</text>`;
    svg += `<text x="14" y="${(T2 + H - B2) / 2}" transform="rotate(-90 14 ${(T2 + H - B2) / 2})" text-anchor="middle" font-size="10.5" fill="var(--ink-muted)">${tt("FIRE 年龄 P50", "FIRE age P50")}</text>`;
    // frontier line (sorted by consumption)
    const fr = pts.filter(p => p.frontier).sort((a, b) => a.cons_p50 - b.cons_p50);
    if (fr.length > 1)
      svg += `<polyline fill="none" stroke="var(--ink-muted)" stroke-dasharray="4 4" stroke-width="1" points="${fr.map(p => X(p.cons_p50) + "," + Y(p.fire_age_p50)).join(" ")}"/>`;
    // points
    for (const p of pts) {
      const tip = `${money(p.expenses)} × SWR ${pct(p.swr, 1)} → ${money(p.cons_p50)}/yr · FIRE ${Math.round(p.fire_age_p50)} · ${pct(p.lifetime_success, 1)}`;
      svg += `<circle cx="${X(p.cons_p50)}" cy="${Y(p.fire_age_p50)}" r="${p.frontier ? 6 : 4}" fill="${efColor(p.lifetime_success)}" fill-opacity="${p.frontier ? 0.95 : 0.45}" ${p.frontier ? 'stroke="var(--ink)" stroke-width="1.2"' : ""}><title>${tip}</title></circle>`;
    }
    if (nf) svg += `<text x="${X(nf.cons_p50)}" y="${Y(nf.fire_age_p50) - 9}" text-anchor="middle" font-size="12" fill="var(--ink)">★</text>`;
    if (cur.fire_age_p50 != null) {
      svg += `<circle cx="${X(cur.cons_p50)}" cy="${Y(cur.fire_age_p50)}" r="8" fill="none" stroke="${CV("--ch-reloc", "#722F37")}" stroke-width="2.2"/>`;
      svg += `<text x="${X(cur.cons_p50)}" y="${Y(cur.fire_age_p50) - 11}" text-anchor="middle" font-size="10.5" fill="${CV("--ch-reloc", "#722F37")}">${tt("你", "you")}</text>`;
    }
    const el = $("efChart"); el.style.display = ""; el.innerHTML = svg;
    $("efLegend").innerHTML = [
      `<span class="chip"><span class="swl" style="border-color:${CV("--ch-home", "#2A4A3A")}"></span>${tt("成功率 ≥95%", "success ≥95%")}</span>`,
      `<span class="chip"><span class="swl" style="border-color:${CV("--ch-gold", "#8A6420")}"></span>80–95%</span>`,
      `<span class="chip"><span class="swl" style="border-color:${CV("--bad", "#9A2A2A")}"></span>&lt;80%</span>`,
      `<span class="chip">${tt("描边大点 = 前沿", "outlined = frontier")}</span>`,
      `<span class="chip">◯ ${tt("你在这里", "you are here")} · ★ ${tt("最近前沿点", "nearest frontier")}</span>`,
    ].join("");
    const cards = [];
    const onFront = cur.dominated_by === 0;
    cards.push([tt("你现在", "You now"),
                cur.fire_age_p50 == null ? tt("未达 FI", "no FI") : `${money(cur.cons_p50)} · ${Math.round(cur.fire_age_p50)} · ${pct(cur.lifetime_success, 1)}`,
                onFront ? "home" : "accent"]);
    if (onFront) {
      cards.push([tt("结论", "Verdict"), tt("已在前沿上", "on the frontier"), "home"]);
    } else if (nf) {
      cards.push([tt("最近前沿点配置", "Nearest frontier config"), `${money(nf.expenses)} + SWR ${pct(nf.swr, 1)}`, "accent"]);
      cards.push([tt("它的结果", "Its outcome"), `${money(nf.cons_p50)} · ${Math.round(nf.fire_age_p50)} · ${pct(nf.lifetime_success, 1)}`, "home"]);
      cards.push([tt("被支配次数", "Dominated by"), String(cur.dominated_by), ""]);
    }
    $("efReadout").innerHTML = cards.map(([la, v, c2]) => `<div class="readout"><div class="lab">${la}</div><div class="num ${c2}" style="font-size:15px">${v}</div></div>`).join("");
    $("efCap").innerHTML = tt(
      `${r.n_paths.toLocaleString()} 路径/点 · ${r.evals} 次评估 · seed ${r.seed} · 本国情景。三元组口径：年消费 P50 · FIRE 年龄 P50 · 终身成功率。前沿 = 没有其他网格点在三项上同时不差且至少一项更好。网格粗、有抽样噪声——当地形图，不当合同。`,
      `${r.n_paths.toLocaleString()} paths/point · ${r.evals} evals · seed ${r.seed} · home scenario. Triple: consumption P50 · FIRE age P50 · lifetime success. Frontier = no other grid point is at-least-as-good on all three and better on one. Coarse grid, sampling noise — terrain, not contract.`);
    if (animOnce("ef", r)) riseIn($("efChart"), $("efReadout"), $("efCap"));
  }

  // ---------- E5 housing rent-vs-buy (stress page) ----------
  async function runHousing() {
    const revision = state.revision;
    const btn = $("hzRun"); btn.disabled = true;
    $("hzHint").textContent = tt("计算中…", "computing…");
    odProgress(btn, true);
    try {
      const det = await postJSON("/api/housing", { config: state.config });
      const mc = await postJSON("/api/rentbuy", { config: state.config, paths: 1500, seed: state.seed || 96000 });
      state.od.hz = { det, mc }; renderHousing();
      $("hzHint").textContent = "";
    } catch (e) { if (revision === state.revision) { toast(e.message, true); $("hzHint").textContent = ""; } }
    finally { if (revision === state.revision) { btn.disabled = false; odProgress(btn, false); } }
  }
  function renderHousing() {
    const d = state.od.hz; if (!d) return;
    const fresh = animOnce("hz", d);
    const det = d.det, mc = d.mc;
    C.lines($("hzChart"), {
      series: [
        { name: tt("买方房净值", "buyer home equity"), color: CV("--ch-home", "#2A4A3A"), points: det.ages.map((a, i) => [a, det.buy_equity[i]]) },
        { name: tt("租方投资差额", "renter invested diff"), color: CV("--ch-gold", "#8A6420"), points: det.ages.map((a, i) => [a, det.rent_fund[i]]) },
      ], yLeft: { fmt: money }, xfmt: x => x, xLabel: "age", animate: fresh,
    });
    $("hzChart").style.display = "";
    $("hzLegend").innerHTML = `<span class="chip"><span class="swl" style="border-color:${CV("--ch-home", "#2A4A3A")}"></span>${tt("买方房净值（房价−剩余按揭，real）", "buyer equity (value − balance, real)")}</span><span class="chip"><span class="swl" style="border-color:${CV("--ch-gold", "#8A6420")}"></span>${tt("租方投资差额（现金流差按 " + pct(det.assumed_r_real, 1) + " 实际复利）", "renter fund (cash-flow diff at " + pct(det.assumed_r_real, 1) + " real)")}</span>`;
    const rows = [
      [tt("三分支成功率", "three-branch success"), pct(mc.rent.lifetime_success, 1), pct(mc.buy.lifetime_success, 1)],
      [tt("FIRE 年龄 P50", "FIRE age P50"), mc.rent.fire_age_p50, mc.buy.fire_age_p50],
      [tt("消费 P50 real", "spend P50 real"), money(mc.rent.cons_p50), money(mc.buy.cons_p50)],
      [tt("组合终值 P50 real", "portfolio terminal P50"), money(mc.rent.terminal_real_p50), money(mc.buy.terminal_real_p50)],
      [tt("+ 房净值（终点，确定性）", "+ home equity (end, deterministic)"), money(0), money(det.buy_equity[det.buy_equity.length - 1])],
    ];
    $("hzTable").innerHTML = `<thead><tr><th></th><th>${tt("租", "Rent")}</th><th>${tt("买", "Buy")}</th></tr></thead><tbody>` +
      rows.map(r => `<tr><td>${r[0]}</td><td class="real">${r[1]}</td><td class="real">${r[2]}</td></tr>`).join("") + `</tbody>`;
    $("hzCap").innerHTML = tt(
      `${mc.n_paths.toLocaleString()} 路径/安排，seed ${mc.seed}。蒙特卡洛按揭以购房年已实现的美国 CPI 锚定名义合同，再按每条路径之后的已实现 CPI 折实；右侧随机通胀因此会改变按揭的实际负担。左侧确定性图仍使用配置的平均通胀（${pct(det.inflation_mu, 1)}）。房产税/维护随房价实际值走：升值不是免费午餐。`,
      `${mc.n_paths.toLocaleString()} paths/arrangement, seed ${mc.seed}. In Monte Carlo, the mortgage is a nominal contract anchored to realized US CPI at purchase, then deflated by each path's subsequent realized CPI; stochastic inflation therefore changes its real burden. The deterministic chart still uses configured mean inflation (${pct(det.inflation_mu, 1)}). Tax/maintenance track the real home value: appreciation is not a free lunch.`);
    if (fresh) riseIn($("hzChart"), $("hzLegend"), $("hzTable"), $("hzCap"));
  }
  function initHousingPanel() {
    const b = $("hzRun"); if (!b || b._wired) return; b._wired = true;
    b.addEventListener("click", runHousing);
  }

  // ---------- I4 forecast vs actual (trajectory page) ----------
  function ciPercentile(age, nominal) {
    const d = DD(); const rows = (d && d.fan_nom) || [];
    if (!rows.length) return null;
    const r = rows.reduce((b, x) => Math.abs(x.age - age) < Math.abs(b.age - age) ? x : b, rows[0]);
    if (Math.abs(r.age - age) > 1) return null;
    const ks = [["p10", 10], ["p25", 25], ["p50", 50], ["p75", 75], ["p90", 90]].filter(([k]) => r[k] != null);
    if (!ks.length) return null;
    if (nominal < r[ks[0][0]]) return "≤P" + ks[0][1];   // ≤/≥: HTML-safe
    for (let i = 0; i < ks.length - 1; i++) {
      const [k0, q0] = ks[i], [k1, q1] = ks[i + 1];
      if (nominal <= r[k1]) {
        const f = (nominal - r[k0]) / Math.max(r[k1] - r[k0], 1e-9);
        return "P" + Math.round(q0 + f * (q1 - q0));
      }
    }
    return "≥P" + ks[ks.length - 1][1];
  }
  function ciOverlay() {
    const ci = get(state.config, "checkins") || [];
    const pi = +get(state.config, "returns.inflation_mu") || 0.03;
    const sa = +get(state.config, "state.start_age") || 30;
    return ci.map(c => ({
      age: +c.age,
      value: state.fanUnit === "real"
        ? c.actual_total_nominal / Math.pow(1 + pi, Math.max(+c.age - sa, 0))
        : +c.actual_total_nominal,
      label: ciPercentile(+c.age, +c.actual_total_nominal) || "",
    }));
  }
  function renderCiTable() {
    const ci = get(state.config, "checkins") || [];
    const tb = $("ciTable"); if (!tb) return;
    if (!ci.length) { tb.style.display = "none"; $("ciCap").textContent = ""; return; }
    tb.style.display = "";
    tb.innerHTML = `<thead><tr><th>${tt("日期", "Date")}</th><th>${tt("年龄", "Age")}</th><th>${tt("实际（名义）", "Actual (nominal)")}</th><th>${tt("落在预测带", "Within forecast")}</th><th></th></tr></thead><tbody>` +
      ci.map((c, i) => `<tr><td>${c.date || "—"}</td><td>${c.age}</td><td class="real">${money(c.actual_total_nominal)}</td><td>${ciPercentile(+c.age, +c.actual_total_nominal) || "—"}</td><td><button class="btn-ghost sm ciDel" data-i="${i}">✕</button></td></tr>`).join("") + `</tbody>`;
    tb.querySelectorAll(".ciDel").forEach(b => b.addEventListener("click", () => {
      ci.splice(+b.dataset.i, 1); set(state.config, "checkins", ci);
      saveDraft(true); renderCiTable(); renderFan();
    }));
    $("ciCap").innerHTML = tt(
      "记录为名义值；real 口径下按假设通胀折算显示（近似）。分位来自当前预测带——重跑后会按新预测重新评。跟踪几年后：持续 <P25 说明假设偏乐观，该修的是假设，不是运气。",
      "Check-ins are nominal; the real view deflates at assumed inflation (approximate). Percentiles come from the CURRENT forecast bands — they re-grade after each re-run. After a few years: consistently <P25 means the assumptions are optimistic — fix the assumptions, not the luck.");
  }
  function ciAdd() {
    const age = +$("ciAge").value, amt = +$("ciAmt").value;
    if (!age || !(amt > 0)) { toast(tt("填年龄和金额", "Fill age and amount"), true); return; }
    const ci = get(state.config, "checkins") || [];
    const i = ci.findIndex(c => +c.age === age);
    const row = { date: new Date().toISOString().slice(0, 10), age, actual_total_nominal: amt };
    if (i >= 0) ci[i] = row; else { ci.push(row); ci.sort((a, b) => a.age - b.age); }
    set(state.config, "checkins", ci);
    saveDraft(true); renderCiTable(); renderFan();
    toast(tt("已记录——记得「存为计划」以持久保存", "Recorded — use 'Save as plan' to persist"));
  }

  // ---------- I3 drill-down (fan age slice + terminal bucket) ----------
  const REGIME_LAB = { highCAPE: ["高估值开局", "high-CAPE"], aiPersists: ["AI 红利持续", "AI persists"], historical: ["历史均值", "historical"] };
  const regLab = k => (REGIME_LAB[k] ? REGIME_LAB[k][L === "zh" ? 0 : 1] : k);
  function regimeChips(regimes, total) {
    return Object.entries(regimes || {}).sort((a, b) => b[1] - a[1])
      .map(([k, c]) => `<span class="chip">${regLab(k)} ${pct(c / Math.max(total, 1), 0)}</span>`).join("");
  }
  async function runFanDrill() {
    const revision = state.revision;
    const age = +$("fanCursor").value;
    const btn = $("fanDrillBtn"); btn.disabled = true;
    $("fanDrillHint").textContent = tt("计算中…", "computing…");
    odProgress(btn, true);
    try {
      const r = await postJSON("/api/drill", { config: state.config, kind: "age_slice", age, paths: 200, seed: state.seed || 96000 });
      $("fanDrill").style.display = "";
      riseIn($("fanDrill"));                                   // the whole drill block enters, not just its bars
      if (r.hist) C.histogram($("fanDrillChart"), r.hist, { color: CV("--ch-gold", "#8A6420"), animate: true }); // each drill = an explicit fresh reveal
      $("fanDrillLegend").innerHTML = regimeChips(r.regimes, r.alive);
      $("fanDrillCap").innerHTML = tt(
        `${r.age} 岁截面：${r.alive}/${r.n} 条路径仍在样本中（其余已破产/身故/未达此龄）。这是扇形带在该年龄的完整分布——分位数隐藏了它的形状。独立小样本，非主运行。`,
        `Age ${r.age} cross-section: ${r.alive}/${r.n} paths still in sample (rest ruined/died/not there yet). The full distribution the fan's percentiles compress — independent small batch, not the headline run.`);
      $("fanDrillHint").textContent = "";
    } catch (e) { if (revision === state.revision) { toast(e.message, true); $("fanDrillHint").textContent = ""; } }
    finally { if (revision === state.revision) { btn.disabled = false; odProgress(btn, false); } }
  }
  async function runTermDrill(lo, hi) {
    const revision = state.revision;
    $("termHint").textContent = tt("计算中…", "computing…");
    try {
      const r = await postJSON("/api/drill", { config: state.config, kind: "term_bucket", lo, hi, paths: 200, seed: state.seed || 96000 });
      $("termDrill").style.display = "";
      riseIn($("termDrill"));
      const b = r.bucket, al = r.all;
      if (!b) { $("termDrillCards").innerHTML = ""; $("termDrillCap").textContent = tt("这一段里没有样本路径。", "No sample paths in this bucket."); $("termHint").textContent = t("drill.term.hint"); return; }
      const f5 = v => v == null ? "—" : (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "%";
      $("termDrillCards").innerHTML = [
        [tt("占达 FI 路径", "Share of FI paths"), pct(r.share, 0), "accent"],
        [tt("FIRE 年龄 P50（桶 vs 全体）", "FIRE age P50 (bucket vs all)"), `${Math.round(b.fire_age_p50)} vs ${Math.round(al.fire_age_p50)}`, ""],
        [tt("退休头5年年化（桶 vs 全体）", "First 5y ann. (bucket vs all)"), `${f5(b.first5_annual_p50)} vs ${f5(al.first5_annual_p50)}`, "home"],
        [tt("桶内破产率", "Ruin rate in bucket"), pct(b.ruin_rate, 1), b.ruin_rate > 0 ? "accent" : ""],
      ].map(([la, v, c2]) => `<div class="readout"><div class="lab">${la}</div><div class="num ${c2}" style="font-size:15px">${v}</div></div>`).join("");
      $("termDrillCap").innerHTML = tt(
        `终值 ${money(lo)}–${money(hi)} 的 ${b.count} 条路径（独立小样本）· regime 构成：`,
        `${b.count} paths ending ${money(lo)}–${money(hi)} (independent small batch) · regimes: `) + regimeChips(b.regimes, b.count);
      $("termHint").textContent = t("drill.term.hint");
    } catch (e) { if (revision === state.revision) { toast(e.message, true); $("termHint").textContent = t("drill.term.hint"); } }
  }
  function termClickToBucket(ev) {
    const d = DD(), hist = state.termUnit === "real" ? d.terminal_real_hist : d.terminal_nom_hist;
    if (!hist || state.termUnit !== "real") return;      // drill is real-basis only
    const svg = $("termChart"), rect = svg.getBoundingClientRect();
    const xv = (ev.clientX - rect.left) / rect.width * 760;
    const n = hist.counts.length, mL = 20, mR = 16;
    const bin = Math.floor((xv - mL) / (760 - mL - mR) * n);
    if (bin < 0 || bin >= n) return;
    runTermDrill(hist.edges[bin], hist.edges[bin + 1]);
  }
  function termKeyToBucket(ev) {
    if (ev.key !== "Enter" && ev.key !== " ") return;
    ev.preventDefault();
    if (state.termUnit !== "real") return;
    const d = DD(), hist = d.terminal_real_hist;
    if (!hist || !hist.counts || !hist.counts.length) return;
    const bin = hist.counts.reduce((best, count, i) => count > hist.counts[best] ? i : best, 0);
    runTermDrill(hist.edges[bin], hist.edges[bin + 1]);
  }

  // ---------- I2 story mode (dist page) ----------
  const STORY = { pick: "typical", reroll: 0, data: null, busy: false };
  function storyEventText(e) {
    switch (e.kind) {
      case "promotion": return tt("获得晋升——收入台阶上移", "Promoted — income steps up");
      case "milestone": return tt(`组合首次突破 ${money(e.v)}`, `Portfolio first crosses ${money(e.v)}`);
      case "crash": return tt(`坏年：实际财富缩水 ${Math.abs(e.v * 100).toFixed(0)}%`, `Bad year: real wealth drops ${Math.abs(e.v * 100).toFixed(0)}%`);
      case "fire": return tt(`达到财务独立${e.v ? `（组合 ${money(e.v)} real）` : ""}——辞职`, `Financial independence${e.v ? ` (${money(e.v)} real)` : ""} — quits`);
      case "ss_claim": return tt("开始领取社保", "Starts claiming Social Security");
      default: return e.kind;
    }
  }
  function storyEndingText(en) {
    if (!en) return "";
    if (en.kind === "ruin") return tt("组合耗尽——这条人生破产了", "The portfolio runs dry — this life goes broke");
    if (en.kind === "died") return tt(`去世，留下 ${money(en.legacy_real)}（今日购买力）`, `Dies, leaving ${money(en.legacy_real)} (today's $)`);
    if (en.kind === "horizon") return tt(`模拟终点：遗产 ${money(en.legacy_real)}（今日购买力）`, `Horizon: estate ${money(en.legacy_real)} (today's $)`);
    return "";
  }
  async function runStory() {
    if (STORY.busy) return;
    const revision = state.revision;
    STORY.busy = true;
    $("storyHint").textContent = tt("抽样中…", "drawing…");
    $("storyChart").classList.add("loading");
    try {
      const seed = (state.seed || 96000) + STORY.reroll * 7919;
      const r = await postJSON("/api/story", { config: state.config, paths: 150, seed });
      STORY.data = r;
      $("storyTabs").style.display = ""; $("storyReroll").style.display = "";
      renderStory(true);                                       // draw the wealth line in
      riseIn($("storyChart"), $("storyTabs"), $("storyLog"), $("storyCap"));  // the whole life arrives together
      $("storyHint").textContent = "";
    } catch (e) { if (revision === state.revision) { toast(e.message, true); $("storyHint").textContent = ""; } }
    finally { if (revision === state.revision) { STORY.busy = false; $("storyChart").classList.remove("loading"); } }
  }
  function renderStory(animate) {
    const d = STORY.data; if (!d) return;
    if (!d.stories) { $("storyLog").innerHTML = `<li>${tt("这批样本没有达到 FI 的路径。", "No path in this batch reached FI.")}</li>`; return; }
    const s = d.stories[STORY.pick];
    const evAges = new Set(s.events.map(e => e.age));
    C.lines($("storyChart"), {
      series: [{ name: tt("实际财富", "real wealth"), color: CV("--ch-home", "#2A4A3A"),
                 points: s.curve, dots: false }],
      yLeft: { fmt: money, min: 0 }, xfmt: x => x, xLabel: "age",
      animate: !!animate,   // draw the wealth line in on a fresh Run; pick-tab switches redraw instantly
      markers: (s.fire_age ? [{ x: s.fire_age, color: CV("--ch-gold", "#8A6420"), label: "FIRE" }] : [])
        .concat(s.ending && s.ending.age ? [{ x: s.ending.age, color: CV("--ch-reloc", "#722F37"), label: s.ending.kind === "ruin" ? tt("破产", "ruin") : s.ending.kind === "died" ? tt("终", "end") : "" }] : []),
    });
    const rows = s.events.map(e => `<li><span class="st-age">${e.age}${tt(" 岁", "")}</span>${storyEventText(e)}</li>`);
    rows.push(`<li class="st-end"><span class="st-age">${s.ending.age || ""}${s.ending.age ? tt(" 岁", "") : ""}</span><b>${storyEndingText(s.ending)}</b></li>`);
    $("storyLog").innerHTML = rows.join("");
    const gk = s.guardrail_triggers;
    $("storyCap").innerHTML = tt(
      `一条真实模拟路径（150 条中的${STORY.pick === "typical" ? "中位" : STORY.pick === "lucky" ? "第 90 分位" : "倒霉那条"}，regime: ${s.regime}${gk != null ? `，GK 护栏触发 ${gk} 次` : ""}）。这是一种可能，不是预测。`,
      `One real simulated path (the ${STORY.pick} of 150; regime: ${s.regime}${gk != null ? `; GK guardrails fired ${gk}×` : ""}). A possibility, not a prediction.`);
  }
  function initStoryPanel() {
    const b = $("storyRun"); if (!b || b._wired) return; b._wired = true;
    b.addEventListener("click", runStory);
    $("storyReroll").addEventListener("click", () => { STORY.reroll++; runStory(); });
    $("storyTabs").querySelectorAll("button").forEach(tb => tb.addEventListener("click", () => {
      STORY.pick = tb.dataset.k;
      $("storyTabs").querySelectorAll("button").forEach(x => x.setAttribute("aria-pressed", x === tb ? "true" : "false"));
      renderStory();
    }));
  }

  // ---------- I1 live-tweak panel (overview) ----------
  const LIVE_LEVERS = [
    { p: "state.expenses_y0", label: ["当前年开销", "Spending now"], money: true,
      rng: v => [Math.max(12000, Math.round(v * 0.5 / 1000) * 1000), Math.round(v * 2 / 1000) * 1000, 1000] },
    { p: "contributions.base_salary_pre", label: ["税前年收入", "Gross salary"], money: true,
      rng: v => [0, Math.max(50000, Math.round(v * 2 / 5000) * 5000), 5000] },
    { p: "state.swr_pref", label: ["SWR 偏好", "SWR preference"], pct: true, rng: () => [0.02, 0.06, 0.001] },
    { p: "glide.equity_start", label: ["股票占比 · 现在", "Equity % now"], pct: true, rng: () => [0.3, 1.0, 0.05] },
    { p: "returns.expense_ratio", label: ["综合费率", "All-in fee"], pct: true, rng: () => [0, 0.015, 0.0005] },
  ];
  const LIVE_CARDS = [
    ["lifetime_success", ["三分支成功率", "Three-branch success"], v => pct(v, 1), (a, b) => (b - a) * 100, "pp"],
    ["fire_age_p50", ["FIRE 年龄 P50", "FIRE age P50"], v => v == null ? "—" : Math.round(v), (a, b) => b - a, "yr"],
    ["cons_p50", ["消费 P50 (real)", "Consumption P50"], money, (a, b) => b - a, "$"],
    ["terminal_real_p50", ["终值 P50 (real)", "Terminal P50"], money, (a, b) => b - a, "$"],
  ];
  const LV = { open: false, base: null, overrides: {}, ver: 0, timer: null, inflight: false };

  function liveFmtVal(f, v) { return f.money ? money(v) : f.pct ? (v * 100).toFixed(1) + "%" : v; }
  function buildLiveSliders() {
    const host = $("liveSliders"); host.innerHTML = "";
    LIVE_LEVERS.forEach(f => {
      const cur = LV.overrides[f.p] != null ? LV.overrides[f.p] : (+get(state.config, f.p) || 0);
      const base = +get(state.config, f.p) || 0;
      const [mn, mx, stp] = f.rng(base || 1);
      const row = document.createElement("div"); row.className = "live-row";
      row.innerHTML = `<span class="live-lab">${f.label[L === "zh" ? 0 : 1]}</span>
        <input type="range" min="${mn}" max="${mx}" step="${stp}" value="${cur}">
        <span class="live-val">${liveFmtVal(f, cur)}</span>`;
      const inp = row.querySelector("input");
      inp.addEventListener("input", () => {
        const v = +inp.value;
        LV.overrides[f.p] = v;
        row.querySelector(".live-val").textContent = liveFmtVal(f, v);
        clearTimeout(LV.timer);
        LV.timer = setTimeout(fireLive, 500);          // I1: debounce 500ms
      });
      host.appendChild(row);
    });
  }
  function liveCfg() {
    const c = JSON.parse(JSON.stringify(state.config));
    Object.entries(LV.overrides).forEach(([p, v]) => set(c, p, v));
    return c;
  }
  async function fireLive() {
    const my = ++LV.ver;
    $("liveHint").textContent = tt("计算中…", "computing…");
    $("liveCards").classList.add("loading");
    try {
      const r = await postJSON("/api/live", { config: liveCfg(), paths: 1500, seed: state.seed || 96000 });
      if (my !== LV.ver) return;                       // a newer drag superseded us
      if (!LV.base && !Object.keys(LV.overrides).length) LV.base = r.summary;
      renderLiveCards(r.summary);
      $("liveHint").textContent = tt("已算", "done");
    } catch (e) { if (my === LV.ver) { toast(e.message, true); $("liveHint").textContent = ""; } }
    finally { if (my === LV.ver) $("liveCards").classList.remove("loading"); }
  }
  function renderLiveCards(s) {
    const b = LV.base || s;
    $("liveCards").innerHTML = LIVE_CARDS.map(([k, lab, fmt, dfn, unit]) => {
      const dv = (b[k] == null || s[k] == null) ? null : dfn(b[k], s[k]);
      const good = k === "fire_age_p50" ? (dv || 0) < 0 : (dv || 0) > 0;
      const dTxt = dv == null || Math.abs(dv) < 1e-9 ? "" :
        `<div class="sub" style="color:${good ? "var(--forest,#2A4A3A)" : "var(--bad,#9A2A2A)"}">${dv > 0 ? "▲" : "▼"} ${unit === "$" ? money(Math.abs(dv)) : Math.abs(dv).toFixed(1) + " " + unit}</div>`;
      return `<div class="readout"><div class="lab">${lab[L === "zh" ? 0 : 1]}</div><div class="num">${fmt(s[k])}</div>${dTxt}</div>`;
    }).join("");
    $("liveCap").innerHTML = tt(
      "1,500 路径 · 与基线同 seed（共同随机数，差值比单次读数稳）。成功率抽样噪声约 ±1pp——小于它的差不作数。",
      "1,500 paths · same seed as baseline (common random numbers — deltas beat single readings). Success-rate noise ≈ ±1pp; smaller deltas don't count.");
  }
  function initLivePanel() {
    const tg = $("liveToggle"); if (!tg || tg._wired) return; tg._wired = true;
    tg.addEventListener("click", () => {
      LV.open = !LV.open;
      $("liveBody").classList.toggle("hidden", !LV.open);
      tg.textContent = t(LV.open ? "live.close" : "live.open");
      if (LV.open) { LV.base = null; LV.overrides = {}; buildLiveSliders(); fireLive(); riseIn($("liveBody")); }
    });
    $("liveApply").addEventListener("click", () => {
      Object.entries(LV.overrides).forEach(([p, v]) => set(state.config, p, v));
      LV.open = false; $("liveBody").classList.add("hidden");
      tg.textContent = t("live.open");
      LV.overrides = {}; LV.base = null;
      runJob();
    });
    $("liveReset").addEventListener("click", () => {
      LV.overrides = {}; buildLiveSliders(); fireLive();
    });
  }

  function renderPageNext(p) {
    const chain = {
      overview: ["trajectory", tt("财富轨迹长什么样？", "What does the wealth path look like?")],
      trajectory: ["dist", tt("终点和消费的分布如何？", "How are outcomes and spending distributed?")],
      dist: ["stress", tt("哪些假设最要命？", "Which assumptions dominate?")],
      stress: [state.data && state.data.relocation ? "reloc" : (state.slots.A && state.slots.B ? "ab" : "concl"), tt("继续 →", "Continue →")],
      reloc: [state.slots.A && state.slots.B ? "ab" : "concl", tt("所以，结论是什么？", "So — what's the conclusion?")],
      ab: ["concl", tt("所以，结论是什么？", "So — what's the conclusion?")],
      concl: null,
    };
    const nx = chain[p];
    $("pageNext").innerHTML = nx ? `<button id="pnBtn"><div class="pn-q">${nx[1]}</div><div class="pn-a">→ ${nx[0].toUpperCase()}</div></button>` : "";
    const b = $("pnBtn"); if (b) b.addEventListener("click", () => showPage(nx[0]));
  }

  // ---------- A/B scenario slots ----------
  function saveSlot(k) {
    if (!state.data) { toast(tt("先跑一次再保存", "Run first, then save"), true); return; }
    state.slots[k] = { config: JSON.parse(JSON.stringify(state.config)), data: state.data };
    toast(tt(`已存为方案 ${k}`, `Saved as scenario ${k}`));
    resultTabs();
  }
  function renderAB() {
    const A = state.slots.A, B = state.slots.B;
    if (!A || !B) return;
    const ah = A.data.home, bh = B.data.home;
    const dls = bh.lifetime_success - ah.lifetime_success;
    const dfa = (bh.fire_age.p50 || 0) - (ah.fire_age.p50 || 0);
    const dcons = bh.mean_real_consumption.p50 - ah.mean_real_consumption.p50;
    const dterm = bh.terminal_real.p50 - ah.terminal_real.p50;
    const sgn = v => (v >= 0 ? "+" : "");
    $("abVerdict").className = "verdict";
    $("abVerdict").innerHTML = `<div class="v-main">${tt(
      `B 相对 A：三分支成功率 <b>${sgn(dls)}${(dls * 100).toFixed(2)}pp</b>，FIRE 中位 <b>${sgn(dfa)}${dfa.toFixed(0)} 年</b>，年消费 <b>${sgn(dcons)}${money(dcons)}</b>，实际遗产 <b>${sgn(dterm)}${money(dterm)}</b>。`,
      `B vs A: three-branch success <b>${sgn(dls)}${(dls * 100).toFixed(2)}pp</b>, median FIRE <b>${sgn(dfa)}${dfa.toFixed(0)} yr</b>, spending <b>${sgn(dcons)}${money(dcons)}</b>/yr, real legacy <b>${sgn(dterm)}${money(dterm)}</b>.`)}</div>`;
    const med = d => (d.data.dist.home.fan_real || []).map(x => [x.age, x.p50]);
    C.lines($("abChart"), { series: [
      { name: "A", color: CV("--ch-home", "#2A4A3A"), points: med(A) },
      { name: "B", color: CV("--ch-reloc", "#722F37"), points: med(B) },
    ], yLeft: { log: true }, xfmt: x => x, xLabel: "age",
      markers: [ah.fire_age.p50 != null ? { x: ah.fire_age.p50, color: CV("--ch-home", "#2A4A3A"), label: "A·FIRE" } : null,
                bh.fire_age.p50 != null ? { x: bh.fire_age.p50, color: CV("--ch-reloc", "#722F37"), label: "B·FIRE" } : null].filter(Boolean),
      animate: animOnce("ab", state.data) });
    $("abLegend").innerHTML = `<span class="chip"><span class="swl" style="border-color:${CV("--ch-home", "#2A4A3A")}"></span>A · ${esc(A.config.name || "")}</span><span class="chip"><span class="swl" style="border-color:${CV("--ch-reloc", "#722F37")}"></span>B · ${esc(B.config.name || "")}</span>`;
    const rows = [
      [tt("三分支成功率", "Three-branch success"), pct(ah.lifetime_success), pct(bh.lifetime_success), sgn(dls) + (dls * 100).toFixed(2) + "pp"],
      [tt("FIRE 年龄 P50", "FIRE age P50"), ah.fire_age.p50, bh.fire_age.p50, sgn(dfa) + dfa.toFixed(0)],
      [tt("P50 年消费 real", "P50 spend real"), money(ah.mean_real_consumption.p50), money(bh.mean_real_consumption.p50), sgn(dcons) + money(dcons)],
      [tt("P50 终值 real", "P50 terminal real"), money(ah.terminal_real.p50), money(bh.terminal_real.p50), sgn(dterm) + money(dterm)],
      [tt("精度", "Precision"), `${(A.data.meta.protocol || {}).paths ? A.data.meta.protocol.paths.toLocaleString() : "—"}`, `${(B.data.meta.protocol || {}).paths ? B.data.meta.protocol.paths.toLocaleString() : "—"}`, ""],
    ];
    $("abTable").innerHTML = `<thead><tr><th></th><th>A</th><th>B</th><th>Δ (B−A)</th></tr></thead><tbody>` +
      rows.map(r => `<tr><td>${r[0]}</td><td>${r[1]}</td><td>${r[2]}</td><td class="real">${r[3]}</td></tr>`).join("") + `</tbody>`;
    $("abCap").textContent = tt("两方案各自独立完整运行（种子与精度见上表）。注意：Δ 含两次运行的抽样噪声（Quick 档成功率 Δ 的噪声约 ±0.7pp）——小差异请用更高精度确认。中位轨迹为示意分布样本。", "Each scenario is an independent run (seed & precision above). Note: deltas carry sampling noise from both runs (≈±0.7pp on success at Quick) — confirm small differences at higher precision. Median trajectories are illustrative samples.");
  }

  const D = () => state.data.home;
  const DD = () => state.data.dist.home;

  function renderCore() {
    const s = D();
    // renderCore runs before the count-up gate flips, so this is true only on the first
    // results render after a computation — same "reveal once, don't replay on tab-return" rule.
    C.gauge($("gauge"), s.lifetime_success, { animate: !state._verdictCounted });
    $("branch").innerHTML = `${tt("到达 FI", "Reached FI")} <b>${pct(s.reached_fi_rate)}</b> · ${tt("FIRE 后偿付", "post-FIRE")} <b>${pct(s.post_fire_solvency)}</b><br>${tt("退休前身故", "died pre-FIRE")} <b>${pct(s.died_during_accum_rate)}</b> · ${tt("真·积累失败", "true accum. failure")} <b>${pct(s.true_accumulation_failure_rate)}</b>` +
      ((s.event_shortfall_rate || 0) > 0 ? `<br>${tt("强制事件支付失败", "mandatory-event failure")} <b>${pct(s.event_shortfall_rate)}</b>` : "");
    const fa = s.fire_age;
    $("fireBig").textContent = fa.p50 != null ? Math.round(fa.p50) : "—";
    $("fireSub").textContent = fa.p50 != null ? `P10–P90 ${Math.round(fa.p10)}–${Math.round(fa.p90)} · ${tt("最早", "earliest")} ${fa.min}` : tt("样本内未到达 FI", "never reaches FI in sample");
    const mc = s.mean_real_consumption;
    $("consBig").textContent = money(mc.p50); $("consSub").textContent = `P10 ${money(mc.p10)} · P90 ${money(mc.p90)}`;
    setMetric("term50", money(s.terminal_real.p50)); setMetric("ss50", money(s.ss_total_real.p50));
    setMetric("reached", pct(s.reached_fi_rate)); setMetric("solv", pct(s.post_fire_solvency));
    const ms = s.milestones || {}, keys = Object.keys(ms).sort((a, b) => +a - +b);
    const tile = (age, ttl, sub) => `<div class="mile"><div class="age">${age != null ? Math.round(age) : "—"}</div><div class="ttl">${ttl}<small>${sub}</small></div></div>`;
    let html = keys.slice(0, 2).map(k => { const la = +k >= 1e6 ? "$" + (+k / 1e6) + "M" : "$" + (+k / 1e3) + "K"; return tile(ms[k].median_age, `${la} ${tt("里程碑", "milestone")}`, `P50 · ${pct(ms[k].reach_probability, 0)} ${tt("达成", "reach")}`); }).join("");
    html += tile(fa.p50, tt("FIRE 达成", "FIRE reached"), "financial independence · P50");
    $("milestones").innerHTML = html;
  }
  function setMetric(k, v) { const e = document.querySelector(`.metric .val[data-k="${k}"]`); if (e) { e.textContent = v; e.className = "val home"; } }

  function legendFan() { const p = C.PAL.home; return `<span class="chip"><span class="sw" style="background:${p.band2}"></span>P10–P90</span><span class="chip"><span class="sw" style="background:${p.band}"></span>P25–P75</span><span class="chip"><span class="swl" style="border-color:${p.line}"></span>${tt("中位", "median")}</span>`; }
  function syncCursor(el, rows) {
    if (!rows || !rows.length) return;
    const lo = rows[0].age, hi = rows[rows.length - 1].age;
    el.min = lo; el.max = hi;
    if (+el.value < lo || +el.value > hi) el.value = Math.round((lo + hi) / 2);
  }
  function renderFan() {
    const d = DD(), rows = state.fanUnit === "real" ? d.fan_real : d.fan_nom;
    syncCursor($("fanCursor"), rows);
    const animate = state._fanAnimData !== state.data;
    C.fan($("fanChart"), rows, { log: true, pal: "home", fireAge: d.fire_age_p50, cursorAge: +$("fanCursor").value, onFireDrag: a => solveFire(a), animate, overlay: ciOverlay(),
      // §3.3 slider a11y — localized here so the label re-renders with the language switch
      fireLabel: tt("FIRE 目标年龄：左右方向键 ±1 岁，Shift ±5 岁，回车提交", "FIRE target age: arrow keys ±1 year, Shift ±5, Enter to apply") });
    if (animate) state._fanAnimData = state.data;
    $("fanLegend").innerHTML = legendFan(); updateFanReadout();
    const tail = rows.length ? rows[rows.length - 1] : null;
    $("fanCap").innerHTML = `${state.fanUnit === "real" ? `<span class="green">${tt("今日美元", "today's $")}</span>` : tt("名义", "nominal")}${tt("口径，", " · ")}${d.n_paths.toLocaleString()} ${tt("条示意路径。尾部随存活路径变薄而淡出。", "sample paths; the tail fades as fewer paths survive.")}`;
  }
  function updateFanReadout() {
    const d = DD(), rows = state.fanUnit === "real" ? d.fan_real : d.fan_nom, a = +$("fanCursor").value;
    $("fanCursorVal").textContent = a;
    if (!rows.length) { $("fanReadout").innerHTML = ""; return; }
    const r = rows.reduce((b, x) => Math.abs(x.age - a) < Math.abs(b.age - a) ? x : b, rows[0]);
    $("fanReadout").innerHTML = [["P10", r.p10], ["P25", r.p25], ["P50", r.p50], ["P75", r.p75], ["P90", r.p90]].map(([la, v], i) => `<div class="readout"><div class="lab">${la} @ ${r.age}</div><div class="num ${i === 2 ? "home" : ""}">${money(v)}</div></div>`).join("");
  }
  function renderTerm() {
    const d = DD(), s = D(), hist = state.termUnit === "real" ? d.terminal_real_hist : d.terminal_nom_hist;
    const animate = state._termAnimData !== state.data; if (animate) state._termAnimData = state.data;  // §5.8 rise-in once per run
    C.histogram($("termChart"), hist, { color: CV("--forest-light", "#3D6852"), animate });
    const tr = s.terminal_real, tn = s.terminal_nominal;
    $("termTable").innerHTML = `<thead><tr><th>${tt("口径", "basis")}</th><th>P10</th><th>P50</th><th>P90</th></tr></thead><tbody><tr><td>real</td><td class="real">${money(tr.p10)}</td><td class="real">${money(tr.p50)}</td><td class="real">${money(tr.p90)}</td></tr><tr><td class="muted">${tt("名义", "nominal")}</td><td class="nom">${money(tn.p10)}</td><td class="nom">${money(tn.p50)}</td><td class="nom">${money(tn.p90)}</td></tr></tbody>`;
    const canDrill = state.termUnit === "real";
    $("termChart").setAttribute("aria-disabled", String(!canDrill));
    $("termChart").style.cursor = canDrill ? "crosshair" : "default";
    $("termHint").textContent = canDrill
      ? tt("点击直方图任意一段；键盘按 Enter/空格可下钻样本最多的一段。", "Click any histogram bucket; with the chart focused, Enter/Space drills into the largest bucket.")
      : tt("名义模式仅供查看；下钻接口使用 real 口径，请切回 real 后操作。", "Nominal mode is view-only; drill-down uses real values, so switch back to real to drill.");
    $("termCap").innerHTML = tt(`直方图为示意样本（存活路径），表格分位来自完整运行。强烈右偏——P50 远低于均值（$${hist ? Math.round(hist.mean).toLocaleString() : "—"}）。`, `Histogram is an illustrative sample (solvent paths); table percentiles are from the full run. Strongly right-skewed — P50 ≪ mean ($${hist ? Math.round(hist.mean).toLocaleString() : "—"}).`);
  }
  function renderCons() {
    const d = DD(); C.fan($("consChart"), d.consumption, { log: false, pal: "home" });
    $("consLegend").innerHTML = legendFan();
    $("consCap").innerHTML = tt("横轴退休后年龄，纵轴该年可持续 real 提取额。GK 在上下护栏间动态调整——这是消费的分布，不是单一曲线。", "X: retirement age, Y: sustainable real withdrawal. GK adjusts between guardrails — this is the distribution of consumption, not one curve.");
  }
  function renderMileDist() {
    const d = DD(), ms = d.milestones || {}, keys = Object.keys(ms).sort((a, b) => +a - +b);
    $("mileDist").innerHTML = keys.map((k, i) => { const la = +k >= 1e6 ? "$" + (+k / 1e6) + "M" : "$" + (+k / 1e3) + "K"; const h = ms[k]; return `<div style="margin-bottom:16px"><div class="panel-title sm">${la} <span class="tag">${h ? pct(h.reach_frac, 0) + " " + tt("达成 · P50", "reach · P50") + " " + Math.round(h.p50) : tt("样本不足", "insufficient")}</span></div><svg id="mileC${i}" class="chart" viewBox="0 0 760 150"></svg></div>`; }).join("");
    keys.forEach((k, i) => C.ageBars($("mileC" + i), ms[k], { color: CV("--ch-gold", "#8A6420"), animate: animOnce("mile" + i, state.data) }));
  }

  // ---------------------------------------------------------- E5 · bequest
  // The verdict is composed HERE from the numbers rather than printed from the
  // server's `reason`. That string is English-only and this is a headline
  // honesty statement, not a detail row: the page composes its own bilingual
  // conclusions from figures everywhere else it makes a claim this size, and a
  // Chinese reader should not meet an English paragraph at the top of a verdict.
  // The server's `reason` stays available as a diagnostic; nothing here reads it.
  function bequestVerdictHtml(v) {
    if (!v || v.applicable === false) {
      return `<div class="callout"><p>${tt(
        "没有开启父母生命周期模块，所以这个计划里没有被建模的遗产可依赖。这是「不适用」，不是「查过了，不依赖」。",
        "No parent lifecycle module is on, so this plan has no modelled bequest to depend on. That is <i>not applicable</i>, which is a different fact from <i>checked, and no</i>.")}</p></div>`;
    }
    const pct = n => (n * 100).toFixed(1) + "%";
    const drop = Math.round((v.consumption_drop_pct || 0) * 100);
    const withC = money(v.consumption_with), withoutC = money(v.consumption_without);
    const material = Math.round((v.material_drop || 0) * 100);
    let cls, pill, head, body;
    if (v.depends_on_bequest && !v.success_unchanged) {
      cls = "callout warn"; pill = tt("依赖继承", "Leans on it");
      head = tt("这个计划达标，靠的是继承。", "This plan only clears its bar because it inherits.");
      body = tt(`带遗产成功率 ${pct(v.with_bequest)}，不带 ${pct(v.without_bequest)}。按第二个数字做计划——遗产不是你能安排时间的东西。`,
                `${pct(v.with_bequest)} with the bequest, ${pct(v.without_bequest)} without. Plan on the second number: an inheritance is not yours to schedule.`);
    } else if (v.depends_on_bequest) {
      cls = "callout warn"; pill = tt("依赖继承", "Leans on it");
      head = tt("没有这笔继承，计划不会失败——但会缩水。",
                "Without the inheritance this plan does not fail — it shrinks.");
      body = tt(`中位实际消费从每年 ${withC} 降到 ${withoutC}，少 ${drop}%，而成功率不变（${pct(v.with_bequest)}）——因为提取规则是靠<b>砍消费</b>吸收这笔损失的，不是靠耗尽资产。<b>成功率没动，不等于这笔继承不重要。</b>`,
                `Median real spending falls from ${withC} to ${withoutC} a year, ${drop}% less, while the success rate is unchanged at ${pct(v.with_bequest)} — because the withdrawal rule absorbs the loss by <b>cutting spending</b> rather than by running out. <b>An unchanged success rate here does not mean the inheritance is unimportant.</b>`);
    } else {
      cls = "callout"; pill = tt("不依赖", "Stands on its own");
      head = tt("这个计划不靠这笔继承。", "This plan does not lean on the inheritance.");
      // Leads with the delta rather than two figures. At this rounding the
      // before and after both print as the same number, and "from $51K to
      // $51K, 1%" reads as a rounding artifact rather than as a small effect
      // — which is the opposite of what the sentence is there to say.
      body = tt(`失去它，中位实际消费每年少 ${money(v.consumption_drop)}（${withC} → ${withoutC}），只有 ${drop}%，低于算作实质性的 ${material}%。`,
                `Losing it costs ${money(v.consumption_drop)} a year of median real spending (${withC} → ${withoutC}) — ${drop}%, below the ${material}% that would count as material.`);
    }
    return `<div class="${cls}"><h5><span class="pill">${pill}</span>${head}</h5><p>${body}</p></div>`;
  }

  async function runBequestCheck() {
    const btn = $("beqRun"); const out = $("beqOut");
    if (!btn || !out) return;
    const revision = state.revision;
    btn.disabled = true; btn.textContent = t("beq.running");
    out.innerHTML = "";
    try {
      const started = await postJSON("/api/bequest/start", {
        config: state.config, paths: Math.min(state.paths || 2000, 2000),
        seed: state.seed || 96000 });
      if (!started.job) throw new Error(started.error || "no job");
      for (;;) {
        await new Promise(r => setTimeout(r, 500));
        const progress = await revGet("/api/progress?job=" + encodeURIComponent(started.job));
        if (progress.error) throw new Error(progress.error);
        if (progress.done) break;
      }
      const verdict = await revGet("/api/result?job=" + encodeURIComponent(started.job));
      if (revision !== state.revision) return;   // a newer run superseded this
      out.innerHTML = bequestVerdictHtml(verdict);
    } catch (e) {
      out.innerHTML = `<div class="callout warn"><p>${esc(e.message || String(e))}</p></div>`;
    } finally {
      btn.disabled = false; btn.textContent = t("beq.run");
    }
  }

  // ============================================================ funded ratio
  // No sampling here at all. The number the user reads is a ratio of two
  // discounted sums, and both inputs to it are theirs.

  function fundedRatioHtml(v) {
    if (!v || v.applicable === false) {
      const missing = (v && v.missing || []).join(", ");
      return `<div class="callout"><p>${tt(
        `还差两个数才能算：${esc(missing)}。折现率请查今天的 TIPS 实际收益率（TreasuryDirect），地板是你自己认为不可再砍的年支出。两个都不填就不算 —— 猜出来的比率会随折现率大幅移动，而它看起来和量出来的一模一样。`,
        `Two inputs are missing: ${esc(missing)}. Read today's real TIPS yield from TreasuryDirect, and set the floor to the annual spending you consider non-negotiable. Neither is guessed: the ratio moves more with the discount rate than with anything else, and a guessed one looks exactly like a measured one.`)}</p></div>`;
    }
    const floorPct = (v.floor_funded_ratio * 100).toFixed(0);
    const totalPct = (v.total_funded_ratio * 100).toFixed(0);
    const covered = v.floor_funded_ratio >= 1.0;
    const head = covered
      ? tt(`地板已覆盖：${floorPct}%`, `Floor is covered: ${floorPct}%`)
      : tt(`地板未覆盖：${floorPct}%`, `Floor is NOT covered: ${floorPct}%`);
    const ss = v.assets.social_security;
    const rows = [
      [tt("组合（今天）", "Portfolio today"), money(v.assets.portfolio_today)],
      ...(v.assets.income_streams || []).map(s => [
        tt(`收入流 · ${esc(s.kind)}（${s.start_age}–${s.end_age}）`,
           `Income · ${esc(s.kind)} (${s.start_age}–${s.end_age})`),
        money(s.present_value)]),
      [ss ? tt(`社保（${ss.claim_age} 岁领取）`, `Social Security (claim ${ss.claim_age})`)
          : tt("社保", "Social Security"),
       ss ? money(ss.present_value) : tt("未建模", "not modelled")],
      [tt("资产合计（现值）", "Assets, present value"), money(v.assets.total)],
      [tt("地板负债（现值）", "Floor liability, PV"), money(v.liabilities.floor_present_value)],
      [tt("可自由支配（现值）", "Discretionary, PV"), money(v.liabilities.discretionary_present_value)],
    ].map(([k, val]) => `<tr><td>${k}</td><td class="num">${val}</td></tr>`).join("");
    return `<div class="callout"><p><b>${head}</b></p>
      <p>${tt(`连可自由支配一起算是 ${totalPct}%。折现率 ${(v.discount_rate_real*100).toFixed(2)}%（实际），地板 ${money(v.floor_annual_real)}/年，负债窗口 ${v.retire_age}–${v.horizon_end_age} 岁。`,
              `Including discretionary spending it is ${totalPct}%. Real discount rate ${(v.discount_rate_real*100).toFixed(2)}%, floor ${money(v.floor_annual_real)}/yr, liability window ages ${v.retire_age}–${v.horizon_end_age}.`)}</p>
      <table class="tbl"><tbody>${rows}</tbody></table>
      <p class="panel-note">${esc(v.basis || "")}</p></div>`;
  }

  async function runFundedRatio() {
    const btn = $("frRun"); const out = $("frOut");
    if (!btn || !out) return;
    btn.disabled = true; btn.textContent = t("fr.running");
    out.innerHTML = "";
    try {
      out.innerHTML = fundedRatioHtml(
        await postJSON("/api/funded_ratio", { config: state.config }));
    } catch (e) {
      out.innerHTML = `<div class="callout warn"><p>${esc(e.message || String(e))}</p></div>`;
    } finally {
      btn.disabled = false; btn.textContent = t("fr.run");
    }
  }

  // ==================================================== roth schedule search
  // A frontier, not a recommendation. The per-year table is the deliverable:
  // the acceptance bar for this work is "more explainable than MaxiFi", and a
  // number with no visible schedule behind it fails that by construction.

  function rothScheduleHtml(v) {
    if (!v || !(v.points || []).length) {
      return `<div class="callout warn"><p>${esc(tt("没有可定价的方案。", "No candidate schedules could be priced."))}</p></div>`;
    }
    const pts = v.points.slice().sort((a, b) =>
      (b.lifetime_success || 0) - (a.lifetime_success || 0));
    const rows = pts.map(p => {
      const star = p.on_frontier ? " ★" : "";
      const window = p.amount > 0 ? `${p.start_age}–${p.end_age}` : "—";
      return `<tr><td>${esc(rothLabel(p.label))}${star}</td>
        <td class="num">${window}</td>
        <td class="num">${p.amount > 0 ? money(p.amount) : "—"}</td>
        <td class="num">${((p.lifetime_success || 0) * 100).toFixed(1)}%</td>
        <td class="num">${money(p.terminal_after_tax_real_p50 || 0)}</td>
        <td class="num">${(p.schedule || []).length || "—"}</td></tr>`;
    }).join("");
    const front = pts.filter(p => p.on_frontier);
    const head = front.length === 1
      ? tt(`只有一个方案没有被压过：${esc(rothLabel(front[0].label))}`,
           `One candidate is not beaten by any other: ${esc(rothLabel(front[0].label))}`)
      : tt(`${front.length} 个方案彼此互不压制 —— 它们之间是取舍，不是排名`,
           `${front.length} candidates do not beat each other -- between them it is a trade-off, not a ranking`);
    const detail = front.map(p => scheduleTable(p)).join("");
    return `<div class="callout"><p><b>${head}</b></p>
      <table class="tbl"><thead><tr>
        <th>${tt("方案", "Schedule")}</th><th>${tt("年龄窗口", "Ages")}</th>
        <th>${tt("每年（今日$）", "Per year")}</th><th>${tt("成功率", "Survival")}</th>
        <th>${tt("税后终值", "After-tax terminal")}</th>
        <th>${tt("实际转换年数", "Years run")}</th></tr></thead>
      <tbody>${rows}</tbody></table>
      <p class="panel-note">${tt("★ = 没有被任何其他方案在两个口径上同时压过。这里没有目标函数，也没有「最优」。",
                                 "★ = not beaten by any other candidate on both measures. There is no objective function here and no \u0022best\u0022.")}</p>
      ${detail}
      <p class="panel-note">${esc(v.schedule_basis || "")}</p>
      <p class="panel-note">${esc(v.scale_note || "")}</p></div>`;
  }

  function scheduleTable(p) {
    if (!(p.schedule || []).length) return "";
    const rows = p.schedule.map(r =>
      `<tr><td>${r.age}</td><td class="num">${money(r.converted_nominal)}</td></tr>`).join("");
    return `<div style="margin-top:10px"><div class="panel-title sm">${
      esc(rothLabel(p.label))} · ${
      tt("逐年实际转换额（名义）", "what was actually converted each year (nominal)")}</div>
      <table class="tbl"><thead><tr><th>${tt("年龄", "Age")}</th><th>${tt("转换额", "Converted")}</th></tr></thead>
      <tbody>${rows}</tbody></table></div>`;
  }

  // Composed through the i18n table rather than a local map: a hardcoded
  // string here does not re-render on a language switch, which is a standing
  // rule in this project and is what the CJK lint caught.
  function rothLabel(key) {
    if (key === "no_conversion") return t("rsch.win.none");
    const m = String(key).match(/^(early|middle|to_rmd)_(\d+)pct$/);
    if (!m) return String(key);
    return `${t("rsch.win." + m[1])} · ${m[2]}%`;
  }

  async function runRothSchedule() {
    const btn = $("rschRun"); const out = $("rschOut");
    if (!btn || !out) return;
    const revision = state.revision;
    btn.disabled = true; btn.textContent = t("rsch.running");
    out.innerHTML = "";
    try {
      const started = await postJSON("/api/roth_schedule/start", {
        config: state.config, paths: Math.min(state.paths || 1200, 2000),
        seed: state.seed || 96000 });
      if (!started.job) throw new Error(started.error || "no job");
      for (;;) {
        await new Promise(r => setTimeout(r, 600));
        const progress = await revGet("/api/progress?job=" + encodeURIComponent(started.job));
        if (progress.error) throw new Error(progress.error);
        if (progress.done) break;
      }
      const verdict = await revGet("/api/result?job=" + encodeURIComponent(started.job));
      if (revision !== state.revision) return;   // a newer run superseded this
      out.innerHTML = rothScheduleHtml(verdict);
    } catch (e) {
      out.innerHTML = `<div class="callout warn"><p>${esc(e.message || String(e))}</p></div>`;
    } finally {
      btn.disabled = false; btn.textContent = t("rsch.run");
    }
  }

  // ========================================================= asset location
  // Three placements of the same portfolio, compared on one set of paths.
  // Composed from numbers here, like the bequest verdict and for the same
  // reason: the server's labels are bilingual data, but the sentence that
  // says what the numbers MEAN has to be written in the reader's language,
  // not translated at the last moment from English.

  function assetLocationHtml(v) {
    if (!v || v.applicable === false) {
      return `<div class="callout"><p>${tt(
        "这个计划的应税账户是空的，所以没有「摆放」这个决定可做。这跟「摆了但没差别」不是同一件事——后者是量出来的，前者是问题不成立。",
        "This plan holds nothing in a taxable account, so there is no placement decision to make. That is a different statement from \u0022we compared placements and found no difference\u0022: one is a measurement, the other is a question that does not arise.")}</p></div>`;
    }
    const arms = (v.arms || []).slice().sort(
      (a, b) => (b.terminal_real_p50 || 0) - (a.terminal_real_p50 || 0));
    const best = arms[0], worst = arms[arms.length - 1];
    const rows = arms.map(a => {
      const meta = ALOC_LABELS[a.key] || {};
      return `<tr><td>${esc(tt(meta.zh || a.key, meta.en || a.key))}</td>
        <td class="num">${(a.dividend_yield * 100).toFixed(2)}%</td>
        <td class="num">${(a.dividend_qualified_fraction * 100).toFixed(0)}%</td>
        <td class="num">${(a.effective_drag * 100).toFixed(3)}%</td>
        <td class="num">${money(a.terminal_real_p50)}</td></tr>`;
    }).join("");
    const spread = v.spread_terminal_real || 0;
    const head = tt(
      `摆放差额：${money(spread)}（中位终值）`,
      `Placement is worth ${money(spread)} of median terminal wealth`);
    const body = tt(
      `最好的是「${esc(tt((ALOC_LABELS[best.key] || {}).zh, (ALOC_LABELS[best.key] || {}).en))}」，最差的是「${esc(tt((ALOC_LABELS[worst.key] || {}).zh, (ALOC_LABELS[worst.key] || {}).en))}」。三条臂用同一个种子、同一批路径，唯一的差别是应税账户里那些分派怎么被课税，所以这个差额不可能是市场噪音。`,
      `Best is \u0022${esc(tt((ALOC_LABELS[best.key] || {}).zh, (ALOC_LABELS[best.key] || {}).en))}\u0022, worst is \u0022${esc(tt((ALOC_LABELS[worst.key] || {}).zh, (ALOC_LABELS[worst.key] || {}).en))}\u0022. All three arms share one seed and one set of paths, and differ only in how the taxable account's distributions are taxed, so this gap cannot be market noise.`);
    const caveat = v.prices_return_differences ? "" : `<p class="panel-note">${tt(
      "只算了税这一半。本引擎每年抽一个混合收益率发给所有账户，没有逐账户的资产配置，所以它算不出「把预期收益更高的资产放进 Roth」本身值多少——那是资产摆放的另一半，这里没有算，也不要当成算过了。",
      "This is the tax half only. The engine draws one blended return a year and applies it to every account, so it cannot price what putting the higher-returning asset in the Roth is worth on its own. That is the other half of asset location, it is not measured here, and it should not be read as though it were.")}</p>`;
    return `<div class="callout"><p><b>${head}</b></p><p>${body}</p>
      <table class="tbl"><thead><tr>
        <th>${tt("摆法", "Placement")}</th><th>${tt("分派率", "Yield")}</th>
        <th>${tt("合格占比", "Qualified")}</th><th>${tt("实际拖累", "Drag")}</th>
        <th>${tt("中位终值", "Median terminal")}</th></tr></thead>
      <tbody>${rows}</tbody></table>${caveat}</div>`;
  }

  const ALOC_LABELS = {
    bonds_in_taxable: { zh: "债券放在应税账户", en: "Bonds in taxable" },
    equities_in_taxable: { zh: "宽基股票放在应税账户", en: "Equities in taxable" },
    blended: { zh: "不做摆放（混合持有）", en: "No location strategy" },
  };

  async function runAssetLocation() {
    const btn = $("alocRun"); const out = $("alocOut");
    if (!btn || !out) return;
    const revision = state.revision;
    btn.disabled = true; btn.textContent = t("aloc.running");
    out.innerHTML = "";
    try {
      const started = await postJSON("/api/asset_location/start", {
        config: state.config, paths: Math.min(state.paths || 2000, 2000),
        seed: state.seed || 96000 });
      if (!started.job) throw new Error(started.error || "no job");
      for (;;) {
        await new Promise(r => setTimeout(r, 500));
        const progress = await revGet("/api/progress?job=" + encodeURIComponent(started.job));
        if (progress.error) throw new Error(progress.error);
        if (progress.done) break;
      }
      const verdict = await revGet("/api/result?job=" + encodeURIComponent(started.job));
      if (revision !== state.revision) return;   // a newer run superseded this
      out.innerHTML = assetLocationHtml(verdict);
    } catch (e) {
      out.innerHTML = `<div class="callout warn"><p>${esc(e.message || String(e))}</p></div>`;
    } finally {
      btn.disabled = false; btn.textContent = t("aloc.run");
    }
  }

  // =========================================================== annual review
  // Phase 2's user-facing half. The server owns the decomposition (§3 Modified
  // Dietz, §4's waterfall); this owns the two-minute form in front of it and
  // the disclosure behind it.
  //
  // Two rules from the protocol shape the rendering and are not style choices.
  // A component that came back `unknown` is never drawn as a number or as a
  // zero-length bar -- it is drawn as the word, because a zero-length bar reads
  // as "nothing happened here" when it means "we do not know". And the fixed
  // display order is the server's `components` array; this never sorts it.

  const REV_CATEGORIES = [
    ["net_contribution", "净投入", "Net contributions"],
    ["spending", "从组合里花掉的", "Spent from the portfolio"],
    ["income", "投进组合的其他收入", "Other income into the portfolio"],
    ["tax", "税", "Tax"],
    ["fee", "费用", "Fees"],
    ["life_event", "大额一次性事件", "Large one-off events"],
  ];

  const REV = { busy: false, result: null, error: null, form: null,
               forecasts: null, chosen: null, loading: false,
               imported: null, lastCheckinId: null,
               counterfactualId: null };

  // Exact dollars, not the app's usual $8K abbreviation. This panel is the one
  // place the user reconciles against numbers they typed in themselves, and a
  // waterfall whose lines are rounded to the nearest thousand cannot be checked
  // against a residual quoted to the cent. The sign goes in front of the
  // currency symbol rather than after it -- `C.moneyFull` emits `$-8,476`,
  // which in a column of gains and losses is a glyph the eye skips.
  const revMoney = v => (v == null || isNaN(v)) ? "—"
    : (v < 0 ? "\u2212" : "") + C.moneyFull(Math.abs(v));

  function archivedSnapshotId() {
    // The forecast being REVIEWED, which is deliberately not "the run I just
    // finished". Attributing a past year against today's projection scores it
    // against a forecast that already knows how that year went; §1.2 calls
    // F_oo the snapshot that was current at the observation.
    if (REV.chosen) return REV.chosen.snapshot_id;
    const meta = state.data && state.data.meta;
    return (meta && meta.snapshot_id) || null;
  }

  function chosenForecast() {
    return REV.chosen || null;
  }

  function currentBaselineForecast() {
    // The newest archived forecast: what the plan says today. Shown beside the
    // one under review so plan drift is visible rather than inferred.
    const list = (REV.forecasts && REV.forecasts.forecasts) || [];
    return list.length ? list[0] : null;
  }

  async function loadReviewForecasts() {
    const ref = archiveRefForReview();
    if (!ref || !ref.plan_id || REV.loading) return;
    REV.loading = true;
    try {
      REV.forecasts = await revGet("/api/checkin/forecasts?plan_id="
                                   + encodeURIComponent(ref.plan_id));
      const list = REV.forecasts.forecasts || [];
      if (!REV.chosen && list.length) {
        // Default to the OLDEST forecast that still has a usable curve: a
        // review looks back, so the useful default is the earliest thing on
        // record, not the most recent. The user can pick any of them.
        const usable = list.filter(f => f.series_available);
        REV.chosen = usable.length ? usable[usable.length - 1] : list[list.length - 1];
      }
    } catch (e) {
      REV.forecasts = null;
    } finally {
      REV.loading = false;
      renderReviewForm();
    }
  }

  function archiveRefForReview() {
    const meta = (state.data && state.data.meta) || {};
    return meta.archive || null;
  }

  function revDefaultPeriod() {
    // Defaults to the twelve months ending today, which is what "annual
    // review" means to the person clicking it. Both ends stay editable.
    const end = new Date();
    const start = new Date(end.getTime());
    start.setFullYear(start.getFullYear() - 1);
    const iso = d => d.toISOString().slice(0, 10);
    return { start: iso(start), end: iso(end) };
  }

  function revMidpoint(startISO, endISO) {
    const a = Date.parse(startISO + "T00:00:00Z"), b = Date.parse(endISO + "T00:00:00Z");
    if (!isFinite(a) || !isFinite(b) || b <= a) return startISO;
    return new Date(a + (b - a) / 2).toISOString().slice(0, 10);
  }

  function revForecastLabel(f, index, list) {
    // Two runs of one plan on the same day are the normal case, and a
    // date-only label made them indistinguishable — a picker whose entries
    // read identically is not a choice. Minutes disambiguate, and the newest
    // is named as the baseline because picking it is the one mistake this
    // control exists to prevent.
    const stamp = String(f.created_at || "");
    const when = stamp.slice(0, 10) + (stamp.length > 15 ? " " + stamp.slice(11, 16) : "");
    const version = String(f.plan_version_id || "").slice(-8);
    const marks = [];
    if (index === 0) marks.push(tt("最新 · 当前基线", "newest · today's baseline"));
    if (index === list.length - 1 && list.length > 1) marks.push(tt("最早", "earliest"));
    if (!f.build_matches_current) marks.push(tt("引擎版本已变", "engine build changed"));
    if (!f.series_available) marks.push(tt("无逐期预测，无法复核", "no per-period forecast — cannot be reviewed"));
    const suffix = marks.length ? "  (" + marks.join(" · ") + ")" : "";
    return `${when} · ${f.precision || "?"} · v${version}${suffix}`;
  }

  function revForecastPicker() {
    const list = (REV.forecasts && REV.forecasts.forecasts) || [];
    if (REV.loading) {
      return `<p class="cap">${tt("正在读取归档的预测…", "Loading archived forecasts…")}</p>`;
    }
    if (!list.length) {
      return `<div class="callout"><p>${tt(
        "这个计划还没有归档的预测可供复核。用 Standard（10,000 路径）或 Official 精度跑一次，运行会被归档，明年就能拿它来对账。",
        "This plan has no archived forecast to review against yet. Run it at Standard (10,000 paths) or Official precision — that run is archived, and next year you can reconcile against it.")}</p></div>`;
    }
    const chosen = REV.chosen || {};
    const options = list.map((f, i) =>
      `<option value="${f.snapshot_id}"${f.snapshot_id === chosen.snapshot_id ? " selected" : ""}${f.series_available ? "" : " disabled"}>${revForecastLabel(f, i, list)}</option>`).join("");
    const baseline = currentBaselineForecast();
    const isBaseline = baseline && chosen.snapshot_id === baseline.snapshot_id;
    return `<div class="rev-grid">
        <label class="rev-field" style="grid-column:1/-1">
          <span>${tt("对着哪一份预测复核", "Which forecast are you reviewing against")}</span>
          <select class="rev-in" id="revForecast">${options}</select>
        </label>
      </div>
      <p class="cap">${isBaseline ? tt(
        "你选的是<b>最新</b>的那份预测。年度复核通常应该选<b>期初时</b>那份 —— 拿今天的预测去评判已经发生的一年，等于用一个已经知道结果的模型打分。",
        "You have selected the <b>newest</b> forecast. An annual review normally wants the one that was current <b>at the start of the period</b> — scoring a year that has already happened against today's projection means scoring it against a model that already knows how it went.")
        : tt("这份预测是当时归档的，不会被后来的重跑改写。",
             "This forecast was archived at the time and is not rewritten by later runs.")}</p>`;
  }

  function renderReviewForm() {
    const host = $("revForm"); if (!host) return;
    const f = REV.form || (REV.form = Object.assign(revDefaultPeriod(), {
      opening: "", closing: "", rows: {},
    }));
    const row = (key, zh, en) => {
      const v = f.rows[key] || (f.rows[key] = { planned: "", actual: "" });
      return `<tr>
        <td class="rev-cat">${tt(zh, en)}</td>
        <td><input class="rev-in mono" data-rev-planned="${key}" inputmode="decimal" value="${v.planned}" placeholder="0"></td>
        <td><input class="rev-in mono" data-rev-actual="${key}" inputmode="decimal" value="${v.actual}" placeholder="0"></td>
      </tr>`;
    };
    host.innerHTML = `
      ${revForecastPicker()}
      <div class="rev-grid">
        <label class="rev-field"><span>${tt("这段时间从", "Period from")}</span>
          <input type="date" class="rev-in" id="revStart" value="${f.start}"></label>
        <label class="rev-field"><span>${tt("到", "to")}</span>
          <input type="date" class="rev-in" id="revEnd" value="${f.end}"></label>
        <label class="rev-field"><span>${tt("期初组合总值 $", "Portfolio at the start $")}</span>
          <input class="rev-in mono" id="revOpening" inputmode="decimal" value="${f.opening}" placeholder="0"></label>
        <label class="rev-field"><span>${tt("期末组合总值 $", "Portfolio at the end $")}</span>
          <input class="rev-in mono" id="revClosing" inputmode="decimal" value="${f.closing}" placeholder="0"></label>
      </div>
      <table class="cmp-table rev-table">
        <thead><tr>
          <th>${tt("现金流", "Cash flow")}</th>
          <th>${tt("当初打算的 $", "What you planned $")}</th>
          <th>${tt("实际发生的 $", "What actually happened $")}</th>
        </tr></thead>
        <tbody>${REV_CATEGORIES.map(c => row(c[0], c[1], c[2])).join("")}</tbody>
      </table>
      <p class="cap">${tt(
        "花出去的填负数。「当初打算的」是你自己的计划口径，不是引擎替你推出来的——这条差别会写进下面的披露里。每笔流量默认记在区间中点；净投入按协议固定用半期口径，与时间戳无关。",
        "Enter money leaving as a negative. \"What you planned\" is your own statement of the plan, not something the engine derived for you — that distinction is carried in the disclosure below. Each flow defaults to the midpoint of the period; net contributions use the protocol's fixed half-period convention regardless of timestamp.")}</p>`;

    host.querySelectorAll("input").forEach(el => {
      el.addEventListener("input", () => {
        if (el.id === "revStart") f.start = el.value;
        else if (el.id === "revEnd") f.end = el.value;
        else if (el.id === "revOpening") f.opening = el.value;
        else if (el.id === "revClosing") f.closing = el.value;
        else if (el.dataset.revPlanned) f.rows[el.dataset.revPlanned].planned = el.value;
        else if (el.dataset.revActual) f.rows[el.dataset.revActual].actual = el.value;
      });
    });
    bindReviewImport();
    bindSpendingImport();
    const notice = $("revImportNotice");
    if (notice) notice.innerHTML = revImportNotice();
    const picker = $("revForecast");
    if (picker) {
      picker.addEventListener("change", () => {
        const list = (REV.forecasts && REV.forecasts.forecasts) || [];
        REV.chosen = list.find(f => f.snapshot_id === picker.value) || REV.chosen;
        // The result belongs to the forecast it was computed against.
        REV.result = null;
        $("revResultPanel").classList.add("hidden");
        renderReviewForm();
      });
    }
    const submit = $("revSubmit");
    if (submit && !submit._revBound) { submit._revBound = true; submit.addEventListener("click", submitReview); }
    if ($("revFormNote")) {
      $("revFormNote").textContent = REV.busy
        ? tt("计算中…", "Working…")
        : tt("只在本机计算，不上传任何东西。", "Computed locally; nothing is uploaded.");
    }
    if (REV.result) renderReviewResult();
    if (REV.error) revShowStatus(REV.error, true);
  }

  // Broker import. Fills the form; it never submits. The parser proposes and
  // the user confirms, because the ledger is append-only and a mis-parsed
  // import cannot be taken back.
  // A year of budgeting-app export -> this year's actual spending.
  //
  // Aggregates only, and the server never returns a transaction row. What
  // comes back is a category breakdown plus counts of what was skipped, which
  // is enough to notice a wrong total without the file's diary of what was
  // bought where.
  function bindSpendingImport() {
    const button = $("revSpendBtn"), file = $("revSpendFile");
    if (!button || !file || button._spendBound) return;
    button._spendBound = true;
    button.addEventListener("click", () => file.click());
    file.addEventListener("change", async e => {
      const chosen = e.target.files && e.target.files[0];
      e.target.value = "";
      if (!chosen) return;
      revShowStatus(tt("正在解析…", "Parsing…"), false);
      try {
        applySpendingImport(await postJSON("/api/spending_import",
                                           { text: await chosen.text() }));
      } catch (err) {
        if (err.stale) return;
        revShowStatus(err.message || String(err), true);
      }
    });
  }

  function applySpendingImport(parsed) {
    const rows = (parsed.by_category || []).slice(0, 8).map(row =>
      `<li>${esc(row.category)} — ${money(row.amount)}</li>`).join("");
    const skipped = parsed.rows || {};
    // The offered figure is null when the export is not a year. Filling the
    // field anyway would put nine months into an annual box, which is wrong
    // by a quarter and looks like a frugal year.
    const offered = parsed.annual_total_for_checkin;
    $("revImportNotice").innerHTML =
      `<div class="callout${offered === null ? " warn" : ""}">` +
      `<p>${tt(`读到 ${skipped.counted} 笔支出，合计 <b>${money(parsed.annual_total)}</b>，` +
               `覆盖 ${parsed.period.days} 天（${esc(parsed.period.start)} 到 ${esc(parsed.period.end)}）。`,
               `Read ${skipped.counted} spending rows totalling <b>${money(parsed.annual_total)}</b>, ` +
               `covering ${parsed.period.days} days (${esc(parsed.period.start)} to ${esc(parsed.period.end)}).`)}</p>` +
      (parsed.period_note ? `<p><b>${esc(parsed.period_note)}</b></p>` : "") +
      `<ul class="cap">${rows}</ul>` +
      `<p class="cap">${tt(`已排除：自有账户间转账 ${skipped.skipped_transfers} 笔、收入 ${skipped.skipped_inflows} 笔、无日期 ${skipped.skipped_undated} 笔。` +
                           `排除转账是因为把它算进支出会把一年虚高好几千。`,
                           `Excluded: ${skipped.skipped_transfers} transfer(s) between your own accounts, ` +
                           `${skipped.skipped_inflows} inflow(s), ${skipped.skipped_undated} undated row(s). ` +
                           `Transfers are excluded because counting them overstates a year by thousands.`)}</p>` +
      `<p class="cap">${esc(parsed.note)}</p></div>`;
    // Into the form MODEL and re-render, the way the broker import beside
    // this one does. The first version reached for `$("rev_spending")`, which
    // does not exist -- the review form is a model object, not DOM ids -- and
    // the `if (target)` guard would have made a dead path look like a working
    // one. That is the E13 shape exactly, and it is the failure this project
    // has now paid for six times.
    if (offered !== null) {
      const f = REV.form;
      if (f) {
        f.rows = f.rows || {};
        f.rows.spending = f.rows.spending || { planned: "", actual: "" };
        f.rows.spending.actual = String(Math.round(offered * 100) / 100);
        renderReviewForm();
      }
    }
    revShowStatus("", false);
  }

  function bindReviewImport() {
    const button = $("revImportBtn"), file = $("revImportFile");
    if (!button || !file || button._revBound) return;
    button._revBound = true;
    button.addEventListener("click", () => file.click());
    file.addEventListener("change", async e => {
      const chosen = e.target.files && e.target.files[0];
      e.target.value = "";
      if (!chosen) return;
      revShowStatus(tt("正在解析…", "Parsing…"), false);
      try {
        const parsed = await postJSON("/api/checkin/import_csv",
                                      { text: await chosen.text() });
        applyImportedTransactions(parsed);
      } catch (err) {
        if (err.stale) return;
        revShowStatus(err.message || String(err), true);
      }
    });
  }

  function applyImportedTransactions(parsed) {
    const f = REV.form; if (!f) return;
    // Sum into the six category rows. The form is per-category totals, not a
    // ledger view, so the import aggregates; the per-row detail stays in the
    // notice below so a wrong total can be traced back.
    const totals = {};
    (parsed.lines || []).forEach(line => {
      totals[line.category] = (totals[line.category] || 0) + line.amount;
    });
    REV_CATEGORIES.forEach(([key]) => {
      if (totals[key] != null) {
        f.rows[key] = f.rows[key] || { planned: "", actual: "" };
        f.rows[key].actual = String(Math.round(totals[key] * 100) / 100);
      }
    });
    if (parsed.period) { f.start = parsed.period.first; f.end = parsed.period.last; }
    REV.imported = parsed;
    renderReviewForm();
    revShowStatus("", false);
  }

  function revImportNotice() {
    const parsed = REV.imported; if (!parsed) return "";
    const unmapped = parsed.unmapped || [];
    const head = tt(
      `导入了 ${(parsed.lines || []).length} 笔现金流，已按类别汇总填入下面的「实际发生的」一列。`,
      `Imported ${(parsed.lines || []).length} cash flows, summed by category into the "actually happened" column below.`);
    const skipped = parsed.skipped_not_a_flow
      ? tt(`另有 ${parsed.skipped_not_a_flow} 笔是组合内部的买卖或调仓，不进出组合，已排除。`,
           ` ${parsed.skipped_not_a_flow} row(s) were trades inside the portfolio, which move nothing in or out, and were excluded.`)
      : "";
    if (!unmapped.length) {
      return `<div class="callout"><p>${head}${skipped}</p></div>`;
    }
    // The important half. These are NOT in the totals above, and saying so
    // plainly is the point: a row silently guessed into a category produces a
    // waterfall that looks right and blames the wrong thing.
    const rows = unmapped.slice(0, 12).map(u => `<li><span class="mono">${revMoney(u.amount)}</span> · ${u.occurred_at.slice(0, 10)} · ${u.action} — <i>${u.reason}</i></li>`).join("");
    return `<div class="callout"><p>${head}${skipped}</p>
      <p><b>${tt(`${unmapped.length} 笔没有归类，没有计入上面的数字`,
                 `${unmapped.length} row(s) could not be classified and are NOT included in the numbers above`)}</b> —
        ${tt("这些词有两种读法，软件不替你猜。请自己判断后手工加进对应的行。",
             "each of these can mean two different things, and the software will not guess. Decide yourself and add them to the right row by hand.")}</p>
      <ul class="cap" style="margin:6px 0 0 18px">${rows}</ul>
      ${unmapped.length > 12 ? `<p class="cap">${tt("（只列前 12 笔）", "(first 12 shown)")}</p>` : ""}</div>`;
  }

  function revShowStatus(message, isError) {
    const box = $("revStatus"); if (!box) return;
    box.textContent = message;
    box.classList.toggle("hidden", !message);
    box.classList.toggle("bad", !!isError);
  }

  function revMinor(text) {
    // Currency -> integer minor units. The server refuses a non-integer, so
    // rounding here rather than there keeps the refusal about real problems.
    const n = Number(String(text == null ? "" : text).replace(/[,\s$]/g, ""));
    return isFinite(n) ? Math.round(n * 100) : NaN;
  }

  function revBuildBody() {
    const f = REV.form, ref = archiveRefForReview();
    if (!ref || !ref.plan_id || !ref.plan_version_id) {
      throw new Error(tt("这次运行没有归档，无法和预测对照。",
                         "This run was not archived, so there is nothing to compare against."));
    }
    const opening = revMinor(f.opening), closing = revMinor(f.closing);
    if (!isFinite(opening) || opening <= 0) {
      throw new Error(tt("请填一个大于零的期初组合总值。",
                         "Enter a portfolio value at the start of the period."));
    }
    if (!isFinite(closing) || closing < 0) {
      throw new Error(tt("请填一个期末组合总值。",
                         "Enter a portfolio value at the end of the period."));
    }
    const at = revMidpoint(f.start, f.end) + "T12:00:00+00:00";
    const expected = [], actual = [];
    REV_CATEGORIES.forEach(([key]) => {
      const r = f.rows[key] || {};
      const p = revMinor(r.planned), a = revMinor(r.actual);
      if (isFinite(p) && p !== 0) expected.push({ category: key, amount_portfolio_minor: p, occurred_at: at });
      if (isFinite(a) && a !== 0) actual.push({ category: key, amount_portfolio_minor: a, occurred_at: at });
    });
    const forecast = chosenForecast();
    if (!forecast) {
      throw new Error(tt("请先选择一份归档的预测。",
                         "Choose an archived forecast first."));
    }
    return {
      plan_id: ref.plan_id,
      // The version the CHOSEN forecast was made under, not today's. A
      // check-in belongs to the plan that forecast it; recording it against
      // this year's version would make the seam's plan-version check pass by
      // comparing a thing to itself.
      plan_version_id: forecast.plan_version_id,
      forecast_period_start: f.start + "T00:00:00+00:00",
      forecast_period_end: f.end + "T00:00:00+00:00",
      opening_value_minor: opening,
      closing_value_minor: closing,
      expected: expected,
      actual: actual,
    };
  }

  async function submitReview() {
    if (REV.busy) return;
    REV.error = null; REV.result = null; revShowStatus("", false);
    let body;
    try { body = revBuildBody(); }
    catch (e) { REV.error = e.message; revShowStatus(e.message, true); return; }
    REV.busy = true; renderReviewForm();
    try {
      let recorded;
      try {
        recorded = await postJSON("/api/checkin/record", body);
      } catch (e) {
        // A period recorded twice is not an error the user needs to fix; the
        // ledger keeps the first one and this reads it back.
        //
        // Branch on the NAME, not the status. `/api/checkin/record` answers
        // 409 for two unrelated reasons: `checkin_exists`, which is the one
        // meant here, and `recovery_conflict`, which the archive raises when
        // it will not accept the write at all. Treating the second as "you
        // already recorded this" sent the user looking at their own date
        // fields -- "Could not find a check-in for that period." -- for an
        // archive-level problem no edit to the form can fix, and it did so
        // after writing nothing.
        if (e.httpStatus !== 409 || e.code !== "checkin_exists") throw e;
        recorded = null;
      }
      const checkinId = recorded ? recorded.checkin_id : await revFindCheckin(body);
      REV.lastCheckinId = checkinId;
      REV.result = await postJSON("/api/checkin/attribute", {
        checkin_id: checkinId,
        forecast_snapshot_id: archivedSnapshotId(),
      });
      renderReviewResult();
      renderReviewHistory();
    } catch (e) {
      if (e.stale) return;
      REV.error = e.message || String(e);
      revShowStatus(REV.error, true);
    } finally {
      REV.busy = false;
      if ($("revFormNote")) {
        $("revFormNote").textContent = tt("只在本机计算，不上传任何东西。",
                                          "Computed locally; nothing is uploaded.");
      }
    }
  }

  async function revGet(url) {
    const r = await fetch(url, { cache: "no-store" });
    const j = await r.json();
    if (!r.ok || j.error) throw new Error(j.error || ("HTTP " + r.status));
    return j;
  }

  async function revFindCheckin(body) {
    const list = await revGet("/api/checkin/history?plan_id=" + encodeURIComponent(body.plan_id));
    const hit = (list.checkins || []).find(c =>
      c.forecast_period_start === body.forecast_period_start &&
      c.forecast_period_end === body.forecast_period_end);
    if (!hit) throw new Error(tt("找不到这段时间的复核记录。",
                                 "Could not find a check-in for that period."));
    return hit.checkin_id;
  }

  const REV_LABEL = {
    market: ["市场", "Market"],
    net_contribution: ["净投入", "Net contributions"],
    income: ["其他收入", "Other income"],
    spending: ["支出", "Spending"],
    tax: ["税", "Tax"],
    fee: ["费用", "Fees"],
    life_event: ["大额事件", "Life events"],
    model_update: ["模型更新", "Model update"],
  };

  function renderReviewResult() {
    const r = REV.result; if (!r) return;
    $("revResultPanel").classList.remove("hidden");
    const rows = r.components || [];
    const known = rows.filter(c => c.value != null).map(c => Math.abs(c.value));
    const scale = Math.max.apply(null, known.concat([Math.abs(r.residual) || 0, 1]));

    const biggest = rows.filter(c => c.value != null)
      .reduce((a, b) => (a && Math.abs(a.value) >= Math.abs(b.value) ? a : b), null);
    const gap = r.y_actual;
    // `y_actual` is measured against F_no, so it is unknown exactly when F_no
    // is. Saying so beats printing a dash where a number belongs.
    const verdictBody = gap == null
      ? tt(`和当初预测差了多少<b>算不出来</b> —— 那个差是相对「旧计划在新引擎版本下会预测什么」来量的，而那次重跑还没做。下面的分项照常，市场和行为仍然分得开。`,
           `How far you landed from the forecast <b>cannot be computed</b> — that gap is measured against what the old plan would predict under the current engine build, and that re-run has not happened. The lines below still stand: market and behaviour are still separated.`)
      : biggest && Math.abs(gap) > (r.tolerance || 0)
      ? tt(`实际比当初的预测${gap < 0 ? "低" : "高"} <b>${revMoney(Math.abs(gap))}</b>，其中最大的一块是<b>${(REV_LABEL[biggest.key] || [biggest.key, biggest.key])[0]}</b>（${revMoney(biggest.value)}）。`,
           `You came in <b>${revMoney(Math.abs(gap))}</b> ${gap < 0 ? "below" : "above"} the forecast; the largest single piece is <b>${(REV_LABEL[biggest.key] || [biggest.key, biggest.key])[1]}</b> (${revMoney(biggest.value)}).`)
      : tt(`实际和当初的预测相差 <b>${revMoney(Math.abs(gap))}</b>，在对账容差之内。`,
           `You landed within <b>${revMoney(Math.abs(gap))}</b> of the forecast, inside the reconciliation tolerance.`);
    $("revVerdict").innerHTML = `<div class="v-main">${verdictBody}</div>`;

    const bar = (value) => {
      if (value == null) return `<span class="rev-unknown">${tt("未知", "unknown")}</span>`;
      const w = Math.min(100, (Math.abs(value) / scale) * 50);
      const side = value < 0 ? "left" : "right";
      return `<span class="rev-bar rev-${side}" style="width:${w}%"></span>`;
    };
    const line = (key, label, value, state) => `
      <tr class="${state === "unknown" ? "rev-row-unknown" : ""}">
        <td class="rev-cat">${label}</td>
        <td class="rev-barcell"><span class="rev-axis"></span>${bar(value)}</td>
        <td class="mono rev-val">${value == null ? tt("未知", "unknown") : revMoney(value)}</td>
      </tr>`;

    $("revWaterfall").innerHTML = `<table class="cmp-table rev-table">
      <tbody>
        ${rows.map(c => line(c.key, (REV_LABEL[c.key] || [c.key, c.key])[L === "zh" ? 0 : 1],
                             c.value, c.state)).join("")}
        ${line("residual", tt("残差（口径与时点差异）", "Residual (convention and timing)"),
               r.residual, r.residual == null ? "unknown" : "complete")}
      </tbody></table>
      ${r.unsplit_update_and_residual != null ? `<p class="cap">${tt(
        `模型更新与残差<b>拆不开</b>，但它们的<b>和是已知的</b>：<span class="mono">${revMoney(r.unsplit_update_and_residual)}</span>。因为 <code>A1 − F_oo</code> 两端都有据可查 —— 收盘值是实测的，旧预测是归档的。这不是把模型更新塞进残差：两行都仍然是「未知」，没有任何数值被分配给其中任何一行。`,
        `The model update and the residual <b>cannot be separated</b>, but their <b>sum is known</b>: <span class="mono">${revMoney(r.unsplit_update_and_residual)}</span>. Both ends of <code>A1 − F_oo</code> are on record — the close was observed and the old forecast is archived. This is not the residual absorbing the model update: both lines stay unknown, and no value is attributed to either.`)}</p>` : ""}`;

    const basis = r.forecast && r.forecast.model_update_basis;
    const basisText = basis === "same_build_determinism"
      ? tt("模型更新一项是精确的零：做这次预测的引擎版本和现在这个是同一个，归档合同保证同版本会复现逐字节相同的结果——所以这是证明出来的零，不是假设。",
           "The model-update line is exactly zero: the engine build that made this forecast is the one running now, and the archive's replay contract guarantees an identical result — so that zero is proven, not assumed.")
      : tt("模型更新一项来自把归档的旧计划在新引擎版本下重跑一次的实测差异。",
           "The model-update line is the measured difference from re-running the archived plan under the current engine build.");
    const offer = $("revCounterfactual");
    if (offer) {
      offer.innerHTML = revCounterfactualOffer(r);
      const button = $("revRunCf");
      if (button) button.addEventListener("click", revRunCounterfactual);
    }
    renderReviewMemo(r);
    renderForecastDrift();
    renderRebaselineOffer();
    $("revDisclosure").innerHTML = [
      basisText,
      r.residual == null ? "" :
      tt(`对账口径：残差 ${revMoney(r.residual)}，容差 ${revMoney(r.tolerance)}，${r.within_tolerance ? "在容差内" : "<b>超出容差</b>——这不代表数据有错，但说明有一部分变化没有被这 ${(r.components || []).length} 个分项解释掉"}。`,
         `Reconciliation: residual ${revMoney(r.residual)} against a tolerance of ${revMoney(r.tolerance)} — ${r.within_tolerance ? "inside tolerance" : "<b>outside tolerance</b>, which does not mean the data is wrong, but does mean some of the change is not explained by the ${(r.components || []).length} lines above"}.`),
      tt("「当初打算的」一列是你自己填的计划口径。引擎并不逐项存储它当年假设的现金流，所以这里不会假装那是引擎的数字。",
         "The \"planned\" column is your own statement of the plan. The engine does not store its assumed cash flows line by line, so this does not pretend those numbers came from it."),
    ].join(" ");
  }

  // Gap codes become sentences here rather than on the server, so neither
  // language leaks into the other. `detail` is the protocol's own English and
  // is shown only when the code is one this build does not know.
  const REV_GAP = {
    residual_outside_tolerance: [
      "残差超出对账容差 —— 有一部分变化没有被这 ${(r.components || []).length} 个分项解释掉。",
      "The residual is outside the reconciliation tolerance: part of the change is not explained by the lines above."],
    waterfall_incomplete: [
      "归因本身不完整。", "The attribution itself is incomplete."],
    component_unknown: [
      "有一行算不出来。", "One of the lines could not be computed."],
  };
  function revGapText(gap) {
    const pair = REV_GAP[gap.code];
    const base = pair ? pair[L === "zh" ? 0 : 1] : (gap.detail || gap.code);
    if (gap.code === "component_unknown" && gap.component) {
      const name = (REV_LABEL[gap.component] || [gap.component, gap.component])[L === "zh" ? 0 : 1];
      return tt(`「${name}」这一行算不出来。`, `The ${name} line could not be computed.`);
    }
    if (gap.code === "waterfall_incomplete" && gap.detail) {
      return base + ` <span class="cap">${gap.detail}</span>`;
    }
    return base;
  }

  const REV_VERDICT = {
    no_action: ["无需行动", "No action needed"],
    keep_watching: ["继续观察", "Keep watching"],
    adjust_one_lever: ["调整一个杠杆", "Adjust one lever"],
  };
  const REV_LEVER = {
    spending: ["支出", "spending"],
    savings_rate: ["储蓄率", "savings rate"],
    income: ["收入", "income"],
    tax_placement: ["账户与税务安排", "tax placement"],
    costs: ["费用", "costs"],
    one_off_events: ["一次性事件", "one-off events"],
  };

  // The memo. Everything it says comes from the server's own reading; this
  // renders it and adds no judgment of its own -- in particular it never
  // upgrades a verdict, because the one thing the memo must not do is turn
  // "we could not measure it" into "it is fine".
  // When the model-update line is unknown, the missing thing is one engine
  // run: the archived plan under the current build. Offering it as a button
  // rather than doing it automatically is deliberate -- it is a full Monte
  // Carlo run at the old protocol's path count, and starting one without
  // asking is not a thing to do behind someone's back.
  function revCounterfactualOffer(result) {
    const basis = result && result.forecast && result.forecast.model_update_basis;
    if (basis !== "build_moved_not_yet_rerun") return "";
    return `<div class="callout"><p>${tt(
      "「模型更新」这一行还是未知的：做这份预测的引擎版本和现在这个不是同一个，要知道差别有多大，需要把<b>当初那份计划</b>在<b>现在的引擎</b>下按<b>当初的种子和路径数</b>重跑一次。协议不允许用今天的输入去反推它。",
      "The model-update line is still unknown: the engine build that made this forecast is not the one running now. Measuring the difference means re-running <b>that same archived plan</b> under <b>the current build</b> at <b>the original seed and path count</b>. The protocol does not allow inferring it from today's inputs.")}</p>
      <div class="compute-row"><button class="btn-ghost sm" id="revRunCf">${tt(
        "重跑一次，把这一项算出来", "Run it and fill this line in")}</button>
        <span class="cap" id="revCfHint"></span></div></div>`;
  }

  async function revRunCounterfactual() {
    const snapshotId = archivedSnapshotId();
    const hint = $("revCfHint"), button = $("revRunCf");
    if (!snapshotId || !hint) return;
    if (button) button.disabled = true;
    hint.textContent = tt("正在重跑…", "Re-running…");
    try {
      const started = await postJSON("/api/checkin/counterfactual_start",
                                    { forecast_snapshot_id: snapshotId });
      const snapshot = await revAwaitJob(started.job, hint);
      REV.counterfactualId = snapshot;
      hint.textContent = tt("重跑完成，正在重算归因…", "Done; re-attributing…");
      REV.result = await postJSON("/api/checkin/attribute", {
        checkin_id: REV.lastCheckinId,
        forecast_snapshot_id: snapshotId,
        counterfactual_snapshot_id: snapshot,
      });
      renderReviewResult();
    } catch (e) {
      if (e.stale) return;
      hint.textContent = e.message || String(e);
      if (button) button.disabled = false;
    }
  }

  async function revAwaitJob(job, hint) {
    for (;;) {
      await new Promise(resolve => setTimeout(resolve, 500));
      const progress = await revGet("/api/progress?job=" + encodeURIComponent(job));
      if (progress.error) throw new Error(progress.error);
      if (hint && progress.pct != null) {
        hint.textContent = tt(`正在重跑… ${Math.round(progress.pct * 100)}%`,
                              `Re-running… ${Math.round(progress.pct * 100)}%`);
      }
      if (progress.done) break;
    }
    const result = await revGet("/api/result?job=" + encodeURIComponent(job));
    const id = result && result.meta && result.meta.snapshot_id;
    if (!id) {
      throw new Error(tt("重跑没有产生归档快照，无法作为反事实使用。",
                         "The re-run produced no archived snapshot, so it cannot be used as the counterfactual."));
    }
    return id;
  }

  function renderReviewMemo(result) {
    const host = $("revMemo"); if (!host) return;
    const memo = result && result.memo;
    if (!memo) { $("revMemoPanel").classList.add("hidden"); return; }
    $("revMemoPanel").classList.remove("hidden");
    const zh = L === "zh";
    const label = pair => pair[zh ? 0 : 1];
    const verdict = label(REV_VERDICT[memo.verdict] || [memo.verdict, memo.verdict]);

    // "spending below plan" reads as the opposite of what a negative spending
    // line means, so each item says what it did to the portfolio instead.
    const happened = (memo.happened || []).map(h => {
      const name = label(REV_LABEL[h.kind] || [h.kind, h.kind]);
      const pctOfOpening = Math.abs(h.share_of_opening * 100).toFixed(1);
      // Phrased as the effect on the PORTFOLIO. "支出比计划少了" reads as
      // "spent less than planned", which is the opposite of what a negative
      // spending line means; naming the portfolio removes the ambiguity
      // instead of relying on the reader to resolve it.
      const lower = h.effect === "left_you_lower";
      return `<li>${tt(
        `${name} 让组合比计划${lower ? "低" : "高"}了 <span class="mono">${revMoney(Math.abs(h.value))}</span>（相当于期初组合的 ${pctOfOpening}%）`,
        `${name} left the portfolio <span class="mono">${revMoney(Math.abs(h.value))}</span> ${lower ? "lower" : "higher"} than plan (${pctOfOpening}% of the opening portfolio)`)}</li>`;
    }).join("");

    const lever = memo.lever
      ? `<p>${tt("要动的话，先动这一个：", "If you change one thing, change this:")}
           <b>${label(REV_LEVER[memo.lever.lever] || [memo.lever.lever, memo.lever.lever])}</b>
           — ${tt("它是本期偏离最大的一项。", "it is this period's largest deviation.")}</p>`
      : "";

    const noLever = memo.market_moved_but_has_no_lever
      ? `<p>${tt("市场本身动得不小，但这份结论<b>不会</b>因此建议你改变资产配置 —— 一年的回报说明不了配置错了，而按一年的回报改配置是这里最贵的一个动作。",
                 "The market itself moved substantially, and this conclusion deliberately does <b>not</b> suggest changing your allocation — one year of returns cannot show an allocation is wrong, and changing it because of one year is the most expensive move available here.")}</p>`
      : "";

    const gaps = (memo.gaps || []).length
      ? `<p><b>${tt("有没测出来的部分：", "Not measured:")}</b></p>
         <ul class="cap" style="margin:4px 0 0 18px">${memo.gaps.map(g => `<li>${revGapText(g)}</li>`).join("")}</ul>
         <p class="cap">${tt("所以这次的结论最多只能是「继续观察」 —— 「没测出来」和「测了没问题」不是一回事。",
                             "So this reading can be no stronger than \u201ckeep watching\u201d: \u201cwe could not measure it\u201d and \u201cwe measured it and it is fine\u201d are not the same statement.")}</p>`
      : "";

    host.innerHTML = `
      <div class="v-main"><b>${verdict}</b></div>
      ${happened ? `<p>${tt("这一期发生了什么：", "What happened this period:")}</p>
                    <ul class="cap" style="margin:4px 0 8px 18px">${happened}</ul>`
                 : `<p class="cap">${tt("这一期没有任何一项偏离超过期初组合的 1%。",
                                        "Nothing deviated by more than 1% of the opening portfolio this period.")}</p>`}
      ${lever}${noLever}${gaps}
      <p class="cap">${tt(`下次复核：<b>${memo.next_review_months} 个月后</b>。判定口径是各分项相对<b>期初组合总值</b>的占比 —— 低于 ${(memo.basis.noise_threshold * 100).toFixed(0)}% 不提，超过 ${(memo.basis.material_threshold * 100).toFixed(0)}% 才算重大。同样的美元金额，对不同规模的组合不是同一件事。`,
        `Next review: <b>in ${memo.next_review_months} months</b>. The call is made on each line's share of the <b>portfolio value at the start of the period</b> — under ${(memo.basis.noise_threshold * 100).toFixed(0)}% is not worth mentioning, over ${(memo.basis.material_threshold * 100).toFixed(0)}% is material. The same dollar amount is not the same fact at a different scale.`)}</p>`;
  }

  // ROADMAP's remaining criterion: the old forecast and the new baseline,
  // shown together. The waterfall above explains this period's actual-versus-
  // forecast gap; this explains how far the plan itself has moved since, which
  // is a different question and must not be folded into the same lines.
  function renderForecastDrift() {
    const host = $("revDrift"); if (!host) return;
    const reviewed = chosenForecast();
    const baseline = currentBaselineForecast();
    if (!reviewed || !baseline || reviewed.snapshot_id === baseline.snapshot_id) {
      host.innerHTML = "";
      $("revDriftPanel").classList.add("hidden");
      return;
    }
    $("revDriftPanel").classList.remove("hidden");

    // Both curves are per-age. Compare them on the ages they share, so a plan
    // whose start age moved does not silently line up two different years.
    const byAge = new Map(baseline.series.map(p => [p.age, p.value]));
    const shared = reviewed.series
      .filter(p => byAge.has(p.age))
      .map(p => ({ age: p.age, old: p.value, now: byAge.get(p.age) }));
    if (!shared.length) {
      host.innerHTML = `<p class="cap">${tt(
        "两份预测没有共同的年龄区间，无法并置。",
        "The two forecasts share no ages, so they cannot be placed side by side.")}</p>`;
      return;
    }

    const target = reviewNominalCloseAge(reviewed);
    const closeAge = shared.reduce((best, p) =>
      Math.abs(p.age - target) < Math.abs(best.age - target) ? p : best,
      shared[0]);
    const later = shared[shared.length - 1];
    const flat = reviewed.plan_version_id === baseline.plan_version_id
      && shared.every(p => p.old === p.now);
    const row = (label, p) => `<tr>
        <td class="rev-cat">${label}</td>
        <td class="mono rev-val">${revMoney(p.old)}</td>
        <td class="mono rev-val">${revMoney(p.now)}</td>
        <td class="mono rev-val">${revMoney(p.now - p.old)}</td>
      </tr>`;
    host.innerHTML = `<table class="cmp-table rev-table">
      <thead><tr>
        <th></th>
        <th>${tt("当初的预测", "The forecast reviewed")}</th>
        <th>${tt("现在的基线", "Today's baseline")}</th>
        <th>${tt("差", "Drift")}</th>
      </tr></thead>
      <tbody>
        ${row(tt("本期末（%s 岁）", "at the period close (age %s)")
                .replace("%s", String(Math.round(closeAge.age))), closeAge)}
        ${row(tt("最远可比年龄（%s 岁）", "at the furthest shared age (%s)")
                .replace("%s", String(Math.round(later.age))), later)}
      </tbody></table>
      <p class="cap">${tt(
        "左列是你正在复核的那份归档预测，右列是这个计划现在的预测。两者的差是<b>计划漂移</b> —— 它不是上面瀑布里的任何一项：瀑布解释的是这一期实际与预测的差，这张表解释的是你的计划本身变了多少。",
        "The left column is the archived forecast under review; the right is what this plan projects now. The gap between them is <b>plan drift</b>, and it is deliberately not a line in the waterfall above: the waterfall explains this period's actual-versus-forecast gap, while this table explains how much the plan itself moved.")}
        ${flat ? tt(
          "这里全是 0，因为两次运行用的是同一份计划版本 —— 归档合同保证同输入同协议会复现逐字节相同的结果，所以这个零是<b>算出来的</b>，不是巧合。",
          "Everything here is zero because both runs used the same plan version — the archive's contract guarantees identical inputs under the same protocol reproduce an identical result, so that zero is <b>derived</b>, not a coincidence.") : ""}</p>`;
  }

  function reviewNominalCloseAge(forecast) {
    const f = REV.form || {};
    const start = forecast.start_age;
    if (start == null || !f.start || !f.end) return start == null ? 0 : start;
    const days = (Date.parse(f.end + "T00:00:00Z") - Date.parse(f.start + "T00:00:00Z"))
                 / 86400000;
    return start + (isFinite(days) ? days / 365.2425 : 1);
  }

  // The delivery item ROADMAP words as "update ... and future assumptions".
  //
  // Deliberately a route into the wizard rather than a second editing surface.
  // Every assumption already has a field there, with its own hint text and the
  // review step that flags anomalies; a parallel set of inputs here would be a
  // second place for them to disagree. What was missing was the connection --
  // and the reassurance that re-baselining does not overwrite the forecast you
  // just reviewed, which is exactly what the archive guarantees.
  function renderRebaselineOffer() {
    const host = $("revRebaseline"); if (!host) return;
    if (!REV.result) { $("revRebaselinePanel").classList.add("hidden"); return; }
    $("revRebaselinePanel").classList.remove("hidden");
    const memo = REV.result.memo || {};
    const lever = memo.lever
      ? tt(`这次复核指向<b>${(REV_LEVER[memo.lever.lever] || [memo.lever.lever, memo.lever.lever])[L === "zh" ? 0 : 1]}</b>。`,
           `This review points at <b>${(REV_LEVER[memo.lever.lever] || [memo.lever.lever, memo.lever.lever])[1]}</b>.`)
      : "";
    host.innerHTML = `<p>${lever} ${tt(
      "如果你想据此改预期收益、通胀、支出计划或退休年龄，回向导改完再跑一次就行。<b>这不会覆盖你刚复核的那份预测</b> —— 它已经归档，之后仍然可以拿来对账，两份会并排显示在上面的漂移表里。",
      "If you want to change expected return, inflation, the spending plan or the retirement age off the back of this, go back to the wizard, change them, and run again. <b>That does not overwrite the forecast you just reviewed</b> — it is archived, stays available to reconcile against, and the two appear side by side in the drift table above.")}</p>
      <div class="compute-row"><button class="btn-ghost sm" id="revRebase">${tt(
        "改假设并重新建立基线 →", "Change assumptions and re-baseline →")}</button></div>`;
    const button = $("revRebase");
    if (button) {
      button.addEventListener("click", () => { state.step = 0; goto("wizard"); });
    }
  }

  async function renderReviewHistory() {
    const ref = archiveRefForReview(); if (!ref || !ref.plan_id) return;
    const host = $("revHistory"); if (!host) return;
    let list;
    try { list = await revGet("/api/checkin/history?plan_id=" + encodeURIComponent(ref.plan_id)); }
    catch (e) { return; }
    const rows = (list.checkins || []);
    $("revHistoryPanel").classList.toggle("hidden", rows.length === 0);
    if (!rows.length) return;
    host.innerHTML = `<table class="cmp-table rev-table"><thead><tr>
        <th>${tt("区间", "Period")}</th><th>${tt("期初", "Start")}</th><th>${tt("期末", "End")}</th>
      </tr></thead><tbody>${rows.map(c => `<tr>
        <td>${c.forecast_period_start.slice(0, 10)} → ${c.forecast_period_end.slice(0, 10)}</td>
        <td class="mono">${revMoney(c.opening_value_minor / Math.pow(10, c.portfolio_currency_exponent))}</td>
        <td class="mono">${revMoney(c.closing_value_minor / Math.pow(10, c.portfolio_currency_exponent))}</td>
      </tr>`).join("")}</tbody></table>`;
  }

  function renderCompare() {
    if (!state.data.relocation) return;
    const destId = get(state.config, "relocation.destination") || "custom";
    const dname = (DEST.find(x => x.id === destId) || { name: [tt("目的地", "destination")] }).name[L === "zh" ? 0 : 1];
    $("relocDestTitle").textContent = tt("本土 vs ", "Home vs ") + dname;
    const h = state.data.dist.home, r = state.data.dist.relocation;
    syncCursor($("cmpCursor"), h.fan_real);
    const meds = d => (d.fan_real || []).map(x => [x.age, x.p50]);
    C.lines($("cmpChart"), { series: [{ name: tt("本土", "Home"), color: CV("--ch-home", "#2A4A3A"), points: meds(h) }, { name: dname, color: CV("--ch-reloc", "#722F37"), points: meds(r) }], yLeft: { log: true }, xfmt: x => x, xLabel: "age", markers: [{ x: h.fire_age_p50, color: CV("--ch-gold", "#8A6420"), label: "FIRE" }], cursorX: +$("cmpCursor").value, animate: animOnce("cmp", state.data) });
    $("cmpLegend").innerHTML = `<span class="chip"><span class="swl" style="border-color:${CV("--ch-home", "#7E9070")}"></span>${tt("本土中位", "Home median")}</span><span class="chip"><span class="swl" style="border-color:${CV("--ch-reloc", "#93859B")}"></span>${tt("搬迁中位", "Relocation median")}</span>`;
    updateCmpReadout();
    const H = state.data.home, R = state.data.relocation;
    const dm = v => (v >= 0 ? "+" : "") + money(v), dp = v => (v >= 0 ? "+" : "") + pct(v, 2);
    const rc = R.mean_lifestyle_real ? R.mean_lifestyle_real.p50 : R.mean_real_consumption.p50;
    $("cmpTable").innerHTML = `<thead><tr><th>${tt("口径", "metric")}</th><th>${tt("本土", "Home")}</th><th>${dname}</th><th>Δ</th></tr></thead><tbody>` +
      `<tr><td>${tt("三分支成功率", "Three-branch success")}</td><td>${pct(H.lifetime_success)}</td><td class="real">${pct(R.lifetime_success)}</td><td>${dp(R.lifetime_success - H.lifetime_success)}</td></tr>` +
      `<tr><td>${tt("P50 消费 real", "P50 consumption real")}</td><td>${money(H.mean_real_consumption.p50)}</td><td class="real">${money(rc)}</td><td>${dm(rc - H.mean_real_consumption.p50)}</td></tr>` +
      `<tr><td>${tt("P50 终值 real", "P50 terminal real")}</td><td>${money(H.terminal_real.p50)}</td><td class="real">${money(R.terminal_real.p50)}</td><td>${dm(R.terminal_real.p50 - H.terminal_real.p50)}</td></tr></tbody>`;
    $("cmpCap").innerHTML = tt("搬迁消费按「本土生活水准等价」列示（destination 现金 ÷ 生活成本比）。汇率为随机游走，是搬迁情景张口的主驱动。", "Relocation consumption shown at home-purchasing-power equivalence (destination cash ÷ cost-of-living ratio). FX is a random walk — the main driver of the relocation band width.");
  }
  function updateCmpReadout() {
    if (!state.data.relocation) return;
    const a = +$("cmpCursor").value; $("cmpCursorVal").textContent = a;
    const at = d => { const rows = d.fan_real || []; return rows.length ? rows.reduce((b, x) => Math.abs(x.age - a) < Math.abs(b.age - a) ? x : b, rows[0]) : null; };
    const hr = at(state.data.dist.home), rr = at(state.data.dist.relocation);
    if (!hr || !rr) { $("cmpReadout").innerHTML = ""; return; }
    $("cmpReadout").innerHTML = `<div class="readout-grid"><div class="readout"><div class="lab">${tt("本土", "Home")} P50 @ ${a}</div><div class="num home">${money(hr.p50)}</div></div><div class="readout"><div class="lab">${tt("搬迁", "Reloc")} P50 @ ${a}</div><div class="num relocation">${money(rr.p50)}</div></div><div class="readout"><div class="lab">Δ</div><div class="num accent">${(rr.p50 - hr.p50 >= 0 ? "+" : "") + money(rr.p50 - hr.p50)}</div></div></div>`;
  }
  function renderHonesty() {
    const s = D();
    $("invVal").textContent = s.invariant_max_rel_error != null ? s.invariant_max_rel_error.toExponential(2) : "—";
    const p = state.data.meta.protocol || {};
    $("protoVal").textContent = `${(p.paths || 0).toLocaleString()} ${tt("路径", "paths")} · seed ${p.seed} · ${p.elapsed_s}s`;
  }

  // =========================================================== dynamic conclusions
  function card(tone, title, body) { return { tone, title, body }; }
  //: The same tone words the conclusion cards use, in text. Without these a
  //: card's tone is a left border colour and nothing else, which for a
  //: colourblind reader is no tone at all.
  const TONE_TEXT = {
    good: ["有利", "Favourable"],
    warn: ["需要留意", "Watch"],
    neutral: ["中性", "Neutral"],
  };
  const cardHtml = c => {
    const words = TONE_TEXT[c.tone] || TONE_TEXT.neutral;
    const label = words[L === "zh" ? 0 : 1];
    return `<div class="concl-card ${c.tone}" data-tone="${esc(c.tone || "neutral")}"` +
      ` aria-label="${esc(label)}"><div class="concl-tone">${esc(label)}</div>` +
      `<div class="concl-t">${c.title}</div><div class="concl-b">${c.body}</div></div>`;
  };
  // ---------------------------------------------------------------- Phase 3
  // The decision panel. Its job is to keep the packet's carefulness intact on
  // the way to the screen: a `robust` badge with the qualification stripped, a
  // downside shown as a delta instead of a level, or a lab run displayed next
  // to the verdict without its caveat would each undo the work behind it.
  const DEC = { plan: null, packet: null, job: null, poll: null, revision: 0,
                // What the archive holds for the packet on screen, or null.
                // Cleared with the packet: a new computation is a new
                // decision record, and carrying the old id over would
                // attach one packet's state to another's numbers.
                archived: null };

  // ROADMAP names four high-frequency decisions and the backend defines all
  // four; exposing two would leave half the feature unreachable. Each entry's
  // lever is a real config path — `decLeafValue` returns null for anything the
  // plan does not hold, and the panel then says so instead of offering a
  // change it cannot make.
  const DEC_QUESTIONS = [
    ["higher_spending", ["能不能长期多花一点", "Can I permanently spend more?"],
     "state.expenses_y0", 0.9,
     ["把年支出降到现在的 90%", "cut annual spending to 90% of today's"]],
    ["earlier_fire", ["能不能更早退休", "Can I reach FI earlier?"],
     "state.accum_years", 0.8,
     ["把积累期缩短 20%", "shorten the accumulation period by 20%"]],
    ["coast_or_barista", ["能不能停止储蓄，或转成兼职", "Can I stop saving, or go part-time?"],
     "contributions.base_salary_pre", 0.6,
     ["把税前工资降到现在的 60%（兼职）", "drop pre-tax salary to 60% of today's (part-time)"]],
    ["large_life_choice", ["能不能承担一次大额人生选择", "Can I take on a large life choice?"],
     "state.expenses_y0", 1.15,
     ["把年支出永久抬高 15%（买房/搬迁/休假的持续成本）",
      "raise annual spending permanently by 15% (the ongoing cost of a house, a move, or a sabbatical)"]],
  ];

  function decQuestion() {
    return DEC_QUESTIONS.find(q => q[0] === (DEC.question || DEC_QUESTIONS[0][0]))
           || DEC_QUESTIONS[0];
  }

  function decAlternative() {
    const [, , lever, factor] = decQuestion();
    const current = decLeafValue(lever);
    if (current == null) return null;
    return { name: "adjust", changes: { [lever]: current * factor } };
  }

  function decLeafValue(path) {
    let node = state.config;
    for (const part of path.split(".")) {
      if (!node || typeof node !== "object") return null;
      node = node[part];
    }
    return typeof node === "number" ? node : null;
  }

  function renderDecideSetup() {
    DEC.question = DEC.question || DEC_QUESTIONS[0][0];
    $("decQuestion").innerHTML =
      `<div class="cap" style="margin-bottom:6px">${tt("问题", "Question")}</div>` +
      DEC_QUESTIONS.map(([key, label]) =>
        `<label class="live-row"><input type="radio" name="decQ" value="${esc(key)}"${
          key === DEC.question ? " checked" : ""}> ${esc(tt(label[0], label[1]))}</label>`).join("");
    $("decQuestion").querySelectorAll("input[name=decQ]").forEach(el =>
      el.addEventListener("change", () => { DEC.question = el.value; DEC.plan = null; renderDecideSetup(); }));

    const [, , lever, , blurb] = decQuestion();
    const alt = decAlternative();
    $("decAlternatives").innerHTML = alt
      ? `<p class="cap">${tt("要检验的改动：", "The change under test: ")}<b>${
          esc(tt(blurb[0], blurb[1]))}</b> <span class="mono">(${esc(lever)})</span></p>`
      : `<p class="cap">${tt(`这个计划里没有 ${lever}，换一个问题。`,
                             `This plan has no ${lever}; pick another question.`)}</p>`;
    $("decRun").classList.add("hidden");
    $("decCancel").classList.add("hidden");
    $("decPlan").disabled = !alt;
    if (DEC.plan) renderDecideCost();
  }

  async function decidePlan() {
    const alt = decAlternative();
    if (!alt) return;
    $("decHint").textContent = tt("正在数要跑多少…", "counting the runs…");
    try {
      // `config` was missing here from the day this panel was wired, and the
      // cost estimate is why that was invisible: this route never applies an
      // alternative, so it answered happily -- about `{}`. The adverse-pack
      // count on screen ("this plan can test 4 of them") was therefore
      // computed against the server's empty config rather than the user's
      // plan, which is a number about somebody else.
      DEC.plan = await postJSON("/api/decide/plan",
        { config: state.config, question: DEC.question,
          alternatives: [alt], run_paths: runPathCount() });
      if (DEC.plan.error) throw new Error(DEC.plan.error);
      $("decHint").textContent = "";
      renderDecideCost();
      $("decRun").classList.remove("hidden");
    } catch (err) {
      $("decHint").textContent = String(err.message || err);
    }
  }

  // The run on screen, in paths. This was read as `state.data.meta.n_paths`
  // for its whole life and `meta` has never had that key -- it lives under
  // `home`, while `meta` carries `protocol.paths`. So the value was always 0,
  // and every study ran at the floor no matter what the user had paid for.
  function runPathCount() {
    const meta = (state.data && state.data.meta) || {};
    const protocol = meta.protocol || {};
    const home = (state.data && state.data.home) || {};
    return Number(protocol.paths || home.n_paths || 0) || 0;
  }

  // WHICH precision the study runs at is the server's answer, not the page's.
  // Ruled 2026-08-16: round up to the smallest tier that can carry a Robust
  // claim. The rule lives beside `PRECISION_BY_PATHS` in server/app.py; the
  // page sends the run's own count and reads back what was chosen, because a
  // second copy of a tier table here is the exact shape of every seam defect
  // found this week.
  function decChosenPaths() {
    return (DEC.plan && DEC.plan.paths) || null;
  }

  //: True when the study runs at a different precision from the run on
  //: screen, which the cost panel says out loud rather than letting the user
  //: assume the multiple is against their own run.
  function decPathsDiffer() {
    const run = runPathCount(), chosen = decChosenPaths();
    return run > 0 && chosen !== null && chosen !== run;
  }

  function renderDecideCost() {
    const p = DEC.plan;
    if (!p) return;
    $("decCostPanel").classList.remove("hidden");
    const missing = p.families_missing || [];
    const skipped = p.packs_skipped || [];
    $("decCost").innerHTML =
      // The multiple is computed here from the two path counts rather than
      // taken as `engine_runs`. Those are the same number only when the study
      // runs at the headline run's precision, and the whole point of the
      // 2026-08-16 ruling is that it sometimes does not: after a Deep run the
      // study escalates to Official, which is 3.3x per engine run on top of
      // the run count. Printing `engine_runs` there would understate it by
      // exactly the factor the user is entitled to know about.
      `<p>${(() => {
        const run = runPathCount();
        const factor = run > 0 ? (p.total_simulated_paths / run) : null;
        const times = factor === null ? null
          : (factor >= 10 ? Math.round(factor) : factor.toFixed(1));
        return tt(`要跑 <b>${p.engine_runs}</b> 次引擎（${p.arms} 条臂 × ${p.points_per_arm} 个点），` +
               `每次 ${p.paths.toLocaleString()} 条路径，合计 ${p.total_simulated_paths.toLocaleString()} 条。` +
               (times === null ? "" : `大约相当于你刚才那次运行的 ${times} 倍时间。`),
               `<b>${p.engine_runs}</b> engine runs (${p.arms} arms × ${p.points_per_arm} points), ` +
               `${p.paths.toLocaleString()} paths each, ${p.total_simulated_paths.toLocaleString()} in total` +
               (times === null ? "." : ` — roughly ${times}× the run you just sat through.`));
      })()}</p>` +
      // A time, when this machine has been timed. Absent otherwise -- and
      // absent says "not measured here yet", which a made-up number could
      // not. The same study measured 42s idle and 5,314s under load, so the
      // figure travels with what it assumes rather than alone.
      ((p.time_estimate && p.time_estimate.applicable)
        ? `<p class="cap">${tt(
            `大约 <b>${Math.round(p.time_estimate.seconds / 60)} 分钟</b>（按你这台机器最近 ${p.time_estimate.samples} 次同类研究实测）。这个估计假设机器不忙 —— 同一个研究在空闲时 42 秒、在跑测试套件时 5314 秒，所以把它当下限而不是承诺。`,
            `About <b>${Math.round(p.time_estimate.seconds / 60)} min</b>, from the last ${p.time_estimate.samples} study/studies timed on THIS machine. It assumes the machine is not busy — the same study measured 42s idle and 5,314s while a test suite ran, so treat it as a floor rather than a promise.`)}</p>`
        : `<p class="cap">${tt(
            "这台机器还没跑过同类研究，所以没有时间估计 —— 跑完一次之后这里会填上。",
            "Nothing of this shape has been timed on this machine yet, so there is no estimate. It fills in after one run.")}</p>`) +
      (decPathsDiffer()
        ? `<p class="cap">${tt(
            `注意：这次研究按 <b>${(decChosenPaths() || 0).toLocaleString()}</b> 条路径跑，而你刚才那次是 ${runPathCount().toLocaleString()} 条。` +
            `正式 packet 只能在 1 万或 10 万两档上成立 —— 3 万那档承载不了「稳健」这个结论，` +
            `所以这里往上取到能承载的那一档，而不是悄悄往下调。`,
            `Note: this study runs at <b>${(decChosenPaths() || 0).toLocaleString()}</b> paths, while the run on `
            + `screen was ${runPathCount().toLocaleString()}. A formal packet only holds at 10,000 or `
            + `100,000 — 30,000 cannot carry a Robust claim — so this rounds UP to a tier that can, `
            + `rather than quietly running quieter than your plan.`)}</p>`
        : "") +
      `<p class="cap">${tt(`不利假设：这个计划能测 ${p.packs.length} 个。`,
                           `Adverse assumptions: ${p.packs.length} apply to this plan.`)}</p>` +
      (skipped.length
        ? `<details><summary class="cap">${tt(`另有 ${skipped.length} 个测不了 —— ${
              missing.length} 个族完全没覆盖`, `${skipped.length} cannot be tested here — ${
              missing.length} families not covered at all`)}</summary>` +
          `<ul class="cap">${skipped.map(s =>
              `<li><b>${esc(s.pack)}</b> — ${esc(s.reason)}</li>`).join("")}</ul>` +
          `<p class="cap">${tt("跳过不等于「测过了没事」：这些风险对这个计划不适用，不是被检验后通过的。",
                               "Skipped is not the same as tested-and-fine: these risks do not apply to this plan, they were not checked and cleared.")}</p></details>`
        : "");
  }

  async function decideRun() {
    const alt = decAlternative();
    if (!alt) return;
    const revision = state.revision;
    DEC.revision = revision;
    $("decRun").disabled = true;
    $("decCancel").classList.remove("hidden");
    $("decHint").textContent = tt("运行中… 0%", "running… 0%");
    try {
      // Without `config` the server reads `{}`, and applying an alternative
      // that names a real config path to an empty dict raises "state.
      // expenses_y0 is not in the config" -- AFTER the job id exists, so the
      // user waits and then gets an error. This panel has never once produced
      // a packet from the UI; found on 2026-08-15 by clicking the button, and
      // pinned by `test_ui_server_seams`.
      const started = await postJSON("/api/decide/start", {
        config: state.config,
        question: DEC.question, alternatives: [alt],
        run_paths: runPathCount(), constraints: decConstraints(),
      });
      if (started.error) throw new Error(started.error);
      DEC.job = started.job;
      clearInterval(DEC.poll);
      DEC.poll = setInterval(() => decidePoll(revision), 900);
    } catch (err) {
      decideIdle(String(err.message || err));
    }
  }

  function decConstraints() {
    // The one constraint every plan has an opinion about. Sent explicitly so
    // the packet reports whether it still holds under each adverse assumption
    // rather than only reporting the direction.
    const base = state.data && state.data.home && state.data.home.lifetime_success;
    if (typeof base !== "number") return [];
    return [{ kind: "success_threshold", metric: "lifetime_success",
              threshold: Math.max(0.5, Math.min(base, 0.9)) }];
  }

  async function decidePoll(revision) {
    if (revision !== state.revision) {
      clearInterval(DEC.poll);
      decideIdle(tt("计划改过了，这次决策研究作废——重新开始一次。",
                    "the plan changed, so this decision study no longer describes it — start it again."));
      return;
    }
    try {
      const j = await (await fetch("/api/progress?job=" + DEC.job)).json();
      if (revision !== state.revision) return;
      if (j.error === "cancelled") { clearInterval(DEC.poll); decideIdle(tt("已取消", "cancelled")); return; }
      if (j.error) throw new Error(j.error);
      $("decHint").textContent = tt(`运行中… ${Math.round((j.pct || 0) * 100)}% (${j.stage || ""})`,
                                    `running… ${Math.round((j.pct || 0) * 100)}% (${j.stage || ""})`);
      if (!j.done) return;
      clearInterval(DEC.poll);
      const r = await (await fetch("/api/result?job=" + DEC.job)).json();
      if (revision !== state.revision) return;
      DEC.packet = r;
      DEC.archived = null;
      decideIdle("");
      renderDecidePacket();
    } catch (err) {
      clearInterval(DEC.poll);
      decideIdle(String(err.message || err));
    }
  }

  function decideIdle(hint) {
    $("decRun").disabled = false;
    $("decCancel").classList.add("hidden");
    $("decHint").textContent = hint || "";
  }

  const DEC_VERDICT_LABEL = {
    robust: ["稳健", "Robust"], directional: ["有方向", "Directional"],
    unstable: ["不稳定", "Unstable"], inconclusive: ["无法判定", "Inconclusive"],
  };

  // ---- annuity: the licence-to-spend half -------------------------------
  //
  // The tail verdict is /api/decide's job and is shown beside this rather than
  // folded into it: an annuity can improve one reading and worsen the other,
  // and a single number would be a set of weights the user never chose. When
  // the tail half has not been run this says so — it does not leave a blank
  // that reads as "no effect".
  const ANN = {
    plan: null, request: null, result: null, tailPacket: null, error: null,
    licenseJob: null, tailJob: null, poll: null, pollBusy: false,
    planRevision: null, generation: 0, phase: null,
    cancelRequested: false, confirmed: false,
  };

  function annQuotes() {
    return (get(state.config, "guaranteed_income.annuities") || [])
      .filter(a => +a.premium > 0 && +a.annual_payout_real > 0);
  }

  function renderAnnSetup() {
    if (ANN.plan && ANN.planRevision !== state.revision && !ANN.phase) {
      ANN.plan = null; ANN.request = null; ANN.result = null;
      ANN.tailPacket = null; ANN.error = null;
    }
    const quotes = annQuotes();
    const age = +get(state.config, "state.start_age") || 0;
    $("annSetup").innerHTML = quotes.length
      ? `<p class="cap">${tt(`用你填的 <b>${quotes.length}</b> 份报价，在<b>同等成功率约束</b>下分别找出计划还撑得住的最高年支出，再和「不买」比。这回答的是「买了之后我能多花多少」，而不是「失败率降了几个点」。`,
                             `Using your <b>${quotes.length}</b> quote(s), it finds the highest annual spending each plan still carries <b>at the same success constraint</b>, and compares that with not buying. It answers "how much more can I spend", not "how many points did the failure rate move".`)}</p>` +
        `<label class="live-row" style="margin-top:8px"><input type="checkbox" id="annOwn"${
          ANN.confirmed ? " checked" : ""}> <b>${
          tt("这些是我自己拿到的报价，不是本软件给出的估算。", "These are quotes I was actually given, not figures this app produced.")}</b></label>` +
        `<p class="cap">${tt("必须勾选才能开始。年金定价取决于你的年龄、性别、州、公司和当天利率——本仓库不内置任何费率表，也不会替你推算一份没有的报价。", "Required before this can run. Annuity pricing depends on your age, sex, state, insurer and the rates that day — no rate table ships here, and no missing quote is derived on your behalf.")}</p>`
      : `<p class="cap">${tt(`还没有可用的报价。到「家庭与人生事件」里填入你自己拿到的年金报价（保费与年领都要大于 0），并把保底收入模块打开。你现在 ${age} 岁。`,
                             `No usable quote yet. Enter the annuity quotes you were given under Family & life events (premium and payout both above zero) and switch the guaranteed-income module on. You are ${age}.`)}</p>`;
    const own = $("annOwn");
    if (own) own.addEventListener("change", () => {
      ANN.confirmed = own.checked;
      $("annPlan").disabled = !own.checked || !!ANN.phase;
    });
    $("annPlan").disabled = !quotes.length || !ANN.confirmed || !!ANN.phase;
    $("annRun").classList.toggle("hidden", !annCanRun() || !!ANN.phase);
    $("annRun").disabled = !annCanRun() || !!ANN.phase;
    $("annCancel").classList.toggle("hidden", !ANN.phase);
    if (ANN.plan) renderAnnCost();
    if (ANN.result) renderAnnResult();
  }

  async function annuityPlan() {
    const revision = state.revision;
    const generation = ++ANN.generation;
    const request = {
      quotes: JSON.parse(JSON.stringify(annQuotes())),
      config: JSON.parse(JSON.stringify(state.config)),
      paths: annPaths(), decidePaths: runPathCount(),
      threshold: annThreshold(), seed: state.seed || 96000,
    };
    ANN.plan = null; ANN.request = request; ANN.result = null;
    ANN.tailPacket = null; ANN.error = null; ANN.planRevision = revision;
    $("annCost").innerHTML = ""; $("annResult").innerHTML = "";
    $("annRun").classList.add("hidden");
    $("annHint").textContent = tt("正在数要跑多少…", "counting the runs…");
    try {
      ANN.plan = await postJSON("/api/annuity/plan",
        { quotes: request.quotes, config: request.config, paths: request.paths,
          decide_run_paths: request.decidePaths,
          success_threshold: request.threshold });
      if (generation !== ANN.generation || revision !== state.revision) return;
      if (ANN.plan.error) throw new Error(ANN.plan.error);
      $("annHint").textContent = "";
      renderAnnCost();
      $("annRun").classList.toggle("hidden", !annCanRun());
      $("annRun").disabled = !annCanRun();
    } catch (err) {
      if (generation !== ANN.generation) return;
      $("annHint").textContent = String(err.message || err);
    }
  }

  function annCanRun() {
    const p = ANN.plan;
    return !!(p && ANN.request && ANN.planRevision === state.revision &&
      p.decide && p.decide.plan && !p.decide.unavailable_reason &&
      (p.decide_alternatives || []).length);
  }

  function annPaths() {
    // Same defect, same read, same fix. The floor differs because the
    // spending search is not a formal packet: 2,000 is a real tier here.
    return Math.max(runPathCount(), 2000);
  }

  function renderAnnCost() {
    const p = ANN.plan;
    if (!p) return;
    const dp = p.decide && p.decide.plan;
    const missing = dp ? (dp.families_missing || []) : [];
    const unavailable = p.decide && p.decide.unavailable_reason;
    $("annCost").innerHTML =
      `<p><b>${tt("可多花多少", "Licence to spend")}</b> — ${tt(`最多 <b>${p.engine_runs_at_most}</b> 次引擎（${p.searches} 次搜索 × 每次至多 ${
                 Math.round(p.engine_runs_at_most / Math.max(p.searches, 1))} 步），每次 ${
                 (p.paths || 0).toLocaleString()} 条路径。搜索提前收敛就会更少——报的是上限。`,
               `At most <b>${p.engine_runs_at_most}</b> engine runs (${p.searches} searches × up to ${
                 Math.round(p.engine_runs_at_most / Math.max(p.searches, 1))} steps each), ${
                 (p.paths || 0).toLocaleString()} paths per run. Fewer if a search converges early — this is the ceiling, not the estimate.`)}</p>` +
      (dp
        ? `<p><b>${tt("尾部失败率", "Tail failure rate")}</b> — ${tt(`<b>${dp.engine_runs}</b> 次引擎（${dp.arms} 条臂 × ${dp.points_per_arm} 个点），` +
            `每次 ${dp.paths.toLocaleString()} 条路径，合计 ${dp.total_simulated_paths.toLocaleString()} 条。` +
            `不利假设覆盖 ${dp.packs.length} 个；${missing.length} 个族未覆盖。`,
            `<b>${dp.engine_runs}</b> engine runs (${dp.arms} arms × ${dp.points_per_arm} points), ` +
            `${dp.paths.toLocaleString()} paths each and ${dp.total_simulated_paths.toLocaleString()} total. ` +
            `${dp.packs.length} adverse packs apply; ${missing.length} families are not covered.`)}</p>`
        : `<p class="cap">${esc(unavailable || tt("尾部成本不可用，不能开始。",
                                                   "Tail cost is unavailable; the run cannot start."))}</p>`) +
      annDropped(p.dropped_from_both_sides || []) +
      annNotCompared(p.not_compared || []);
  }

  // Both sides are the plan WITHOUT guaranteed income, plus the one
  // instrument each arm is about. That is what makes the arms comparable, and
  // it means a ladder the user already holds is not in either side — so it is
  // said, not dropped quietly.
  function annDropped(list) {
    if (!list.length) return "";
    return `<p class="cap" style="margin-top:10px">${tt(
      `注意：比较的两边都<b>不含</b>你计划里已有的保底收入（${list.join("、")}）——两边一致，所以可比，但都不是你正在跑的那个计划。`,
      `Note: neither side of this comparison includes the guaranteed income already in your plan (${list.join("; ")}). They match each other, which is what makes them comparable, and neither is the plan you are running.`)}</p>`;
  }

  // Named, never omitted. An arm that is absent looks exactly like an arm that
  // lost, and the whole ruling behind this feature is that we do not invent
  // the quote that would fill it.
  function annNotCompared(list) {
    if (!list.length) return "";
    return `<div class="callout" style="margin-top:10px"><b>${
      tt(`没能比较 ${list.length} 个方案`, `${list.length} option(s) could not be compared`)}</b>` +
      `<ul class="cap">${list.map(e =>
        `<li><b>${esc(e.name)}</b> — ${esc(e.reason)}</li>`).join("")}</ul></div>`;
  }

  async function annuityRun() {
    if (!annCanRun()) return;
    const revision = ANN.planRevision;
    const generation = ++ANN.generation;
    ANN.result = null; ANN.tailPacket = null; ANN.error = null;
    ANN.licenseJob = null; ANN.tailJob = null; ANN.cancelRequested = false;
    ANN.phase = "license";
    $("annResult").innerHTML = "";
    $("annRun").disabled = true;
    $("annCancel").classList.remove("hidden");
    $("annHint").textContent = tt("可多花多少… 0%", "licence to spend… 0%");
    try {
      const q = ANN.request;
      const started = await postJSON("/api/annuity/start", {
        quotes: q.quotes, config: q.config, paths: q.paths, seed: q.seed,
        success_threshold: q.threshold,
      });
      if (annuityDiscardLateStart(started, revision, generation, false)) return;
      if (started.error) throw new Error(started.error);
      ANN.licenseJob = started.job;
      if (ANN.cancelRequested) {
        postJSON("/api/cancel", { job: ANN.licenseJob }).catch(() => {});
      }
      annuitySchedule(revision, generation);
    } catch (err) {
      if (generation === ANN.generation) annuityFail(String(err.message || err), false);
    }
  }

  function annThreshold() {
    const base = state.data && state.data.home && state.data.home.lifetime_success;
    return typeof base === "number" ? Math.max(0.5, Math.min(base, 0.9)) : 0.9;
  }

  function annuitySchedule(revision, generation) {
    clearInterval(ANN.poll);
    ANN.poll = setInterval(() => annuityPoll(revision, generation), 900);
  }

  async function annuityPoll(revision, generation) {
    // A plan edit mid-study invalidates the answer, and the panel has to SAY
    // so and become usable again. Returning early without calling
    // `annuityIdle` leaves the Run button disabled by `annuityRun` and the
    // hint frozen at whatever percentage it reached — the panel then looks
    // like it is still working and cannot be restarted without a reload.
    // Driving the real page is what showed it: edit the plan, and the panel
    // sits at "running… 50%" forever.
    if (generation !== ANN.generation) return;
    if (revision !== state.revision) return annuityInvalidate(generation,
      tt("计划改过了，这次比较作废——重新开始一次。",
         "the plan changed, so this comparison no longer describes it — start it again."));
    if (ANN.pollBusy) return;
    const job = ANN.phase === "license" ? ANN.licenseJob
              : ANN.phase === "tail" ? ANN.tailJob : null;
    if (!job) return;
    ANN.pollBusy = true;
    try {
      const phase = ANN.phase;
      const j = await (await fetch("/api/progress?job=" + job)).json();
      if (generation !== ANN.generation) return;
      if (revision !== state.revision) return annuityInvalidate(generation,
        tt("计划改过了，这次比较作废——重新开始一次。",
           "the plan changed, so this comparison no longer describes it — start it again."));
      if (j.error === "cancelled") {
        clearInterval(ANN.poll); ANN.phase = null;
        annuityIdle(tt("已取消", "cancelled")); return;
      }
      if (j.error) throw new Error(j.error);
      const label = phase === "license" ? tt("可多花多少", "licence to spend")
                                         : tt("尾部失败率", "tail failure rate");
      $("annHint").textContent = ANN.cancelRequested ? tt("正在取消…", "cancelling…")
        : `${label}… ${Math.round((j.pct || 0) * 100)}% (${j.stage || ""})`;
      if (!j.done) return;
      clearInterval(ANN.poll);
      const r = await (await fetch("/api/result?job=" + job)).json();
      if (generation !== ANN.generation) return;
      if (revision !== state.revision) return annuityInvalidate(generation,
        tt("计划改过了，这次比较作废——重新开始一次。",
           "the plan changed, so this comparison no longer describes it — start it again."));
      if (r.error) throw new Error(r.error);
      if (phase === "license") {
        ANN.licenseJob = null; ANN.result = r;
        if (ANN.cancelRequested) {
          ANN.phase = null; annuityIdle(tt("已取消", "cancelled")); return;
        }
        await annuityStartTail(revision, generation);
      } else {
        ANN.tailJob = null; ANN.tailPacket = r;
        annJoinedReadings(); // fail closed before publishing the combined table
        ANN.phase = null; annuityIdle(""); renderAnnResult();
      }
    } catch (err) {
      if (generation === ANN.generation) {
        annuityFail(String(err.message || err), !!ANN.result);
      }
    } finally {
      ANN.pollBusy = false;
    }
  }

  async function annuityStartTail(revision, generation) {
    if (generation !== ANN.generation || revision !== state.revision ||
        ANN.cancelRequested) return;
    ANN.phase = "tail-starting";
    $("annHint").textContent = tt("正在开始尾部研究…", "starting the tail study…");
    const d = ANN.plan.decide;
    try {
      const started = await postJSON("/api/decide/start", {
        config: d.baseline_config, question: d.question,
        alternatives: ANN.plan.decide_alternatives,
        paths: d.paths, constraints: d.constraints, seed: ANN.request.seed,
      });
      if (annuityDiscardLateStart(started, revision, generation, true)) return;
      if (started.error) throw new Error(started.error);
      ANN.tailJob = started.job; ANN.phase = "tail";
      if (ANN.cancelRequested) {
        postJSON("/api/cancel", { job: ANN.tailJob }).catch(() => {});
      }
      annuitySchedule(revision, generation);
    } catch (err) {
      if (generation === ANN.generation) annuityFail(String(err.message || err), true);
    }
  }

  // A /start response can arrive after the plan changed or Cancel was pressed.
  // The server has already created the job at that point, so dropping the
  // response would orphan real work and leave the panel stuck in *-starting.
  function annuityDiscardLateStart(started, revision, generation, partial) {
    const staleGeneration = generation !== ANN.generation;
    const staleRevision = revision !== state.revision;
    if (!staleGeneration && !staleRevision && !ANN.cancelRequested) return false;
    if (started && started.job) {
      postJSON("/api/cancel", { job: started.job }).catch(() => {});
    }
    if (staleGeneration) return true; // the newer generation owns the panel
    if (staleRevision) {
      annuityInvalidate(generation,
        tt("计划改过了，这次比较作废——重新开始一次。",
           "the plan changed, so this comparison no longer describes it — start it again."));
      return true;
    }
    ANN.phase = null;
    annuityIdle(tt("已取消", "cancelled"));
    if (partial) renderAnnResult();
    return true;
  }

  function annuityFail(message, partial) {
    clearInterval(ANN.poll); ANN.phase = null;
    ANN.licenseJob = null; ANN.tailJob = null;
    ANN.tailPacket = null;
    ANN.error = message || tt("比较失败", "comparison failed");
    annuityIdle(tt("比较未完成：", "comparison incomplete: ") + ANN.error);
    if (partial) renderAnnResult();
  }

  function annuityInvalidate(generation, message) {
    if (generation !== ANN.generation) return;
    const jobs = [ANN.licenseJob, ANN.tailJob].filter(Boolean);
    ++ANN.generation; clearInterval(ANN.poll);
    ANN.plan = null; ANN.request = null; ANN.result = null;
    ANN.tailPacket = null; ANN.error = null; ANN.phase = null;
    ANN.licenseJob = null; ANN.tailJob = null; ANN.cancelRequested = false;
    $("annCost").innerHTML = ""; $("annResult").innerHTML = "";
    jobs.forEach(job => postJSON("/api/cancel", { job }).catch(() => {}));
    annuityIdle(message);
  }

  async function cancelAnnuityOperation() {
    if (!ANN.phase) return;
    ANN.cancelRequested = true;
    $("annHint").textContent = tt("正在取消…", "cancelling…");
    const jobs = [ANN.licenseJob, ANN.tailJob].filter(Boolean);
    await Promise.all(jobs.map(job => postJSON("/api/cancel", { job }).catch(() => null)));
  }

  function annuityIdle(hint) {
    $("annRun").disabled = !annCanRun();
    $("annRun").classList.toggle("hidden", !annCanRun());
    $("annCancel").classList.add("hidden");
    $("annPlan").disabled = !annQuotes().length || !ANN.confirmed;
    $("annHint").textContent = hint || "";
  }

  // Exact dollars with the sign in front, never the app's $8K abbreviation.
  // Driving the real page showed why: `money()` rounds to thousands, so a
  // measured −521/yr rendered as "+$0/yr ($53K → $53K)". The engine refused to
  // report a false zero, the packet refused to report a false zero, and the
  // formatter printed one anyway.
  const annMoney = v => (v == null || isNaN(v)) ? "—"
    : (v < 0 ? "\u2212" : "+") + C.moneyFull(Math.abs(v));

  function annNameMap(items, nameOf, label) {
    const out = new Map();
    for (const item of items || []) {
      const name = nameOf(item);
      if (typeof name !== "string" || !name || out.has(name)) {
        throw new Error(`${label}: ${!name ? "missing" : "duplicate"} arm name ${name || ""}`);
      }
      out.set(name, item);
    }
    return out;
  }

  function annJoinedReadings() {
    const expected = annNameMap(ANN.plan && ANN.plan.decide_alternatives,
                                a => a && a.name, "plan");
    const licence = annNameMap(ANN.result && ANN.result.license_to_spend,
                               a => a && a.name, "licence-to-spend result");
    const tail = annNameMap(ANN.tailPacket && ANN.tailPacket.alternatives,
                            a => a && a.alternative && a.alternative.name,
                            "tail packet");
    const names = [...expected.keys()].sort();
    const same = other => JSON.stringify([...other.keys()].sort()) === JSON.stringify(names);
    if (!same(licence) || !same(tail)) {
      throw new Error("the plan, licence result and tail packet do not contain the same arms");
    }
    return names.map(name => ({ name, reading: licence.get(name), tail: tail.get(name) }));
  }

  function annTailMetric(alt) {
    const gc = (alt && alt.gain_and_cost) || {};
    return [...(gc.gains || []), ...(gc.costs || []), ...(gc.unchanged || [])]
      .find(e => e && e.metric === "lifetime_success") || null;
  }

  function annTailCell(alt) {
    const metric = annTailMetric(alt);
    const label = DEC_VERDICT_LABEL[alt.verdict] || [alt.verdict, alt.verdict];
    const verdict = esc(tt(label[0], label[1]));
    if (!metric || typeof metric.baseline !== "number" ||
        typeof metric.alternative !== "number") {
      return `<b>${verdict}</b> · <span class="cap">${tt(
        "失败率量不出来——packet 没有带回可比较的 lifetime_success。",
        "failure rate not measurable — the packet did not return comparable lifetime_success values.")}</span>`;
    }
    const failure = 1 - metric.alternative;
    const baselineFailure = 1 - metric.baseline;
    const direction = metric.alternative > metric.baseline
      ? tt("方向改善终身成功率", "the direction improves lifetime success")
      : metric.alternative < metric.baseline
        ? tt("方向降低终身成功率——这不是购买建议", "the direction worsens lifetime success — this is not a recommendation to buy")
        : tt("成功率与不买相同——没有支持购买的证据", "lifetime success is unchanged from not buying — there is no evidence here to recommend buying");
    const before = alt.verdict_before_tier_cap
      ? DEC_VERDICT_LABEL[alt.verdict_before_tier_cap] || [alt.verdict_before_tier_cap,
                                                           alt.verdict_before_tier_cap]
      : null;
    return `<b>${verdict}</b> · ${tt("失败率", "failure rate")} <b>${(failure * 100).toFixed(1)}%</b> ` +
      `<span class="cap">(${tt("不买", "not buying")} ${(baselineFailure * 100).toFixed(1)}%)</span>` +
      `<br><span class="cap"><b>${esc(direction)}</b> — ${esc(alt.why || "")}${
        alt.qualification ? " — " + esc(alt.qualification) : ""}${
        before ? " — " + esc(tt(`证据本身是「${before[0]}」，因 tier 点数下调`,
                                  `evidence alone said “${before[1]}”, lowered by the tier's point count`)) : ""}</span>`;
  }

  function renderAnnResult() {
    const r = ANN.result;
    if (!r) return;
    // `null` and 0 are different answers and the page must not blur them: a
    // reading that could not be measured carries the reason instead of a
    // figure, and never a dash that reads as "no benefit".
    const joined = ANN.tailPacket ? annJoinedReadings()
      : (r.license_to_spend || []).map(reading => ({ name: reading.name, reading, tail: null }));
    const rows = joined.map(({ reading, tail }) => {
      const measured = typeof reading.delta === "number";
      const spend = measured
        ? `<b>${annMoney(reading.delta)}</b>` +
          (typeof reading.pct === "number"
            ? ` <span class="cap">(${(reading.pct * 100).toFixed(1)}%)</span>` : "")
        : `<span class="cap">${tt("量不出来", "not measurable")} — ${esc(reading.reason || "")}</span>`;
      // Shown even when the reading succeeded: a +4,200 pinned to ±50 and one
      // pinned to ±3,000 are not the same claim, and the number alone cannot
      // say which it is.
      const grid = typeof reading.resolution === "number" && reading.resolution > 0
        ? ` <span class="cap">${tt(`（搜索分辨率 ±${C.moneyFull(reading.resolution)}）`,
                                   `(search resolved to ±${C.moneyFull(reading.resolution)})`)}</span>`
        : "";
      return `<tr><td>${esc(reading.name)}</td><td>${spend}${measured ? grid : ""}</td>` +
             `<td>${tail ? annTailCell(tail) : `<span class="cap">${tt(
               "尾部研究未完成", "tail study incomplete")} — ${esc(ANN.error || tt(
               "没有结果", "no result"))}</span>`}</td></tr>`;
    }).join("");
    // The consumption reading, beside the licence and never instead of it.
    // Guardrails absorb a shock by cutting spending later, so success — and
    // therefore the spending ceiling — can sit still while the plan becomes a
    // different plan. Showing only the flattened metric would print "below
    // resolution" for essentially every annuity, and that reads as "makes no
    // difference".
    const consRows = (r.consumption || []).map(c =>
      typeof c.delta === "number"
        ? `<li><b>${esc(c.name)}</b> — ${tt("中位消费", "median consumption")} ` +
          `<b>${annMoney(c.delta)}</b>/${tt("年", "yr")} ` +
          `<span class="cap">(${C.moneyFull(c.baseline)} → ${C.moneyFull(c.arm)})</span></li>`
        : `<li><b>${esc(c.name)}</b> — <span class="cap">${esc(c.reason || "")}</span></li>`
    ).join("");
    $("annResult").innerHTML =
      `<p class="cap" style="margin-top:14px">${tt(
         `在成功率 ≥ ${((r.success_threshold || 0) * 100).toFixed(0)}% 的约束下，相对「不买」每年可多花：`,
         `Extra annual spending the plan carries at a success constraint of ${((r.success_threshold || 0) * 100).toFixed(0)}%, against not buying:`)}</p>` +
      `<table class="ed-table"><thead><tr><th>${tt("方案", "Option")}</th>` +
      `<th>${tt("可多花（每年）", "Licence to spend (per year)")}</th>` +
      `<th>${tt("尾部失败率", "Tail failure rate")}</th></tr></thead><tbody>${rows}</tbody></table>` +
      (consRows
        ? `<p class="cap" style="margin-top:12px">${tt(
             `在你当前的年支出 ${C.moneyFull(r.spending_measured_at || 0)} 下，中位消费的变化（这个口径护栏压不平）：`,
             `Change in median consumption at your current annual spending of ${C.moneyFull(r.spending_measured_at || 0)} — the reading the guardrails cannot flatten:`)}</p>` +
          `<ul class="cap">${consRows}</ul>`
        : "") +
      annDropped(r.dropped_from_both_sides || []) +
      annNotCompared(r.arms_not_compared || []) +
      `<p class="cap">${tt("两个口径并排放，是因为它们回答不同的问题：一份年金可以让你敢多花，同时让尾部更差。把它们压成一个分数，等于替你选了一组你没选过的权重。",
                           "The two readings sit side by side because they answer different questions: an annuity can let you spend more and make the tail worse at the same time. Collapsing them into one score would be choosing weights on your behalf that you never chose.")}</p>`;
  }

  function renderDecidePacket() {
    const packet = DEC.packet;
    const alt = (packet.alternatives || [])[0];
    if (!alt) { $("decHint").textContent = tt("这次运行没有产出可判定的方案。", "This run produced no judgeable alternative."); return; }

    $("decVerdictPanel").classList.remove("hidden");
    const label = DEC_VERDICT_LABEL[alt.verdict] || [alt.verdict, alt.verdict];
    // The qualification travels WITH the badge. "Robust" on its own, when only
    // one of three return models saw the change, is the sentence this whole
    // phase exists to prevent.
    $("decVerdict").innerHTML =
      `<div class="v-main"><b>${esc(tt(label[0], label[1]))}</b></div>` +
      `<div class="v-sub">${esc(alt.why || "")}${
        alt.qualification ? " — " + esc(alt.qualification) : ""}</div>` +
      // The tier cap, shown wherever it applied. A verdict lowered by axis
      // sizes rather than by the evidence is a different situation from one
      // the evidence lowered, and the reader cannot tell them apart from the
      // badge alone.
      (alt.verdict_before_tier_cap
        ? `<div class="v-sub">${tt(
            `证据本身支持「${esc((DEC_VERDICT_LABEL[alt.verdict_before_tier_cap]||[])[0] || "")}」，` +
            `是轴上的点数不够才降到这里。`,
            `The evidence itself supported "${esc((DEC_VERDICT_LABEL[alt.verdict_before_tier_cap]||[])[1] || "")}"; ` +
            `it is the number of points on the axes that lowered this, not the numbers.`)}</div>`
        : "");

    const gc = alt.gain_and_cost || {};
    const row = e => `<tr><td>${esc(decMetricLabel(e.metric))}</td>` +
      `<td class="mono num">${decFmt(e.metric, e.baseline)}</td>` +
      `<td class="mono num">${decFmt(e.metric, e.alternative)}</td>` +
      `<td class="mono num">${e.delta > 0 ? "+" : ""}${decFmt(e.metric, e.delta, true)}</td></tr>`;
    $("decGainCost").innerHTML =
      `<table class="cmp-table"><thead><tr><th>${tt("指标", "Metric")}</th>` +
      `<th class="num">${tt("现在", "Baseline")}</th><th class="num">${tt("改动后", "With change")}</th>` +
      `<th class="num">${tt("差", "Delta")}</th></tr></thead><tbody>` +
      (gc.gains || []).map(row).join("") + (gc.costs || []).map(row).join("") +
      (gc.unchanged || []).map(row).join("") + `</tbody></table>` +
      `<p class="cap">${tt("收益和代价分开列，不合成一个分数——怎么权衡是你的偏好，不是算出来的。",
                           "Gains and costs are listed separately and never netted: weighting them is your preference, not a measurement.")}</p>`;

    // "If I pick the wrong one, how much do I lose" -- per metric, never
    // summed. A combined regret number would need weights between a year of
    // retirement and $200k of terminal wealth, which is the single combined
    // score this packet refuses everywhere else.
    //
    // Reworded rather than loosening the gate that caught it: that check is
    // named `..._on_screen` and a comment does not render, so scanning
    // comments is arguably over-broad -- but the same sentence says the same
    // thing without the banned word, so nothing is lost by obeying it, and a
    // gate left strict is worth more than a comment left as first written.
    const rg = packet.regret;
    $("decRegret").innerHTML = !rg ? "" :
      `<table class="cmp-table"><thead><tr><th>${tt("指标", "Metric")}</th>` +
      `<th>${tt("这组里最好的", "Best on this list")}</th>` +
      `<th class="num">${tt("选错最多损失", "Worst-case regret")}</th></tr></thead><tbody>` +
      (rg.per_metric || []).map(m =>
        `<tr><td>${esc(decMetricLabel(m.metric))}</td>` +
        `<td>${esc(m.best_option)}</td>` +
        `<td class="mono num">${decFmt(m.metric, m.worst_case_regret, true)}</td></tr>`).join("") +
      `</tbody></table>` +
      ((rg.unknown || []).length
        ? `<p class="cap">${tt(`另有 ${rg.unknown.length} 个指标这次没测到，因此不参与比较——不是「没有差别」。`,
                               `${rg.unknown.length} metric(s) were not reported by this run and are left out of the comparison — which is not the same as no difference.`)}</p>`
        : "") +
      `<p class="cap">${esc(rg.scope_note)} ${esc(rg.no_scalar_note)}</p>`;

    const d = alt.downside;
    $("decDownside").innerHTML = d
      ? `<p class="cap">${tt(`最坏的落点：在 <b>${esc(d.at)}</b> 下，${
            decMetricLabel(packet.goal.objective_metric)} 落到 <b>${decFmt(packet.goal.objective_metric, d.value)}</b>` +
            `（同一点上不做这个改动是 ${decFmt(packet.goal.objective_metric, d.baseline_here)}）。这是水平，不是变化量。`,
            `Worst landing: under <b>${esc(d.at)}</b>, ${
            decMetricLabel(packet.goal.objective_metric)} reaches <b>${decFmt(packet.goal.objective_metric, d.value)}</b>` +
            ` (${decFmt(packet.goal.objective_metric, d.baseline_here)} without the change at the same point). That is the level, not the delta.`)}</p>`
      : "";

    $("decAxesPanel").classList.remove("hidden");
    $("decAxes").innerHTML =
      `<table class="cmp-table"><thead><tr><th>${tt("轴", "Axis")}</th><th class="num">${tt("同向", "Agree")}</th>` +
      `<th class="num">${tt("反向", "Disagree")}</th><th class="num">${tt("没看到改动", "Unreached")}</th></tr></thead><tbody>` +
      Object.entries(alt.axes || {}).map(([name, v]) =>
        `<tr><td>${esc(decAxisLabel(name))}</td><td class="num mono">${v.agree.length}</td>` +
        `<td class="num mono">${v.disagree.length}</td><td class="num mono">${v.unreached.length}</td></tr>`).join("") +
      `</tbody></table>` +
      `<p class="cap">${tt("「没看到改动」的点不算同意——两边数字一样只说明这个配置没被改动触及。",
                           "An unreached point is not agreement: identical numbers mean that configuration never saw the change.")}</p>`;

    $("decSensPanel").classList.remove("hidden");
    const sens = alt.sensitive_assumptions || [];
    $("decSensitive").innerHTML = sens.length
      ? `<table class="cmp-table"><thead><tr><th>${tt("假设", "Assumption")}</th>` +
        `<th class="num">${tt("对收益的影响", "Shift in benefit")}</th></tr></thead><tbody>` +
        sens.slice(0, 6).map(s => `<tr><td>${esc(s.at)}</td>` +
          `<td class="num mono">${s.shift > 0 ? "+" : ""}${decFmt(packet.goal.objective_metric, s.shift, true)}</td></tr>`).join("") +
        `</tbody></table><p class="cap">${tt("种子不在这张表里：种子之间的差异是引擎自己的随机性，不是一个你可以持有看法的假设。",
                                             "Seeds are deliberately absent: the spread between seeds is the engine's own randomness, not an assumption you can hold a view about.")}</p>`
      : `<p class="cap">${tt("没有任何被测假设移动了这个改动的效果。", "No tested assumption moved the effect of this change.")}</p>`;

    const rc = alt.reversal_conditions || {};
    $("decReversal").innerHTML = rc.reverses
      ? `<p class="cap"><b>${tt("会反转：", "It reverses: ")}</b></p><ul class="cap">` +
        (rc.conditions || []).map(c => `<li>${esc(c.condition)}</li>`).join("") + `</ul>`
      : rc.nearest_margin
        ? `<p class="cap">${tt(`没有反转。最接近的是 <b>${esc(rc.nearest_margin.at)}</b>，` +
              `在那里还剩 ${Math.round((rc.nearest_margin.fraction_of_anchor_effect || 0) * 100)}% 的收益。`,
              `No reversal. The closest was <b>${esc(rc.nearest_margin.at)}</b>, where ${
              Math.round((rc.nearest_margin.fraction_of_anchor_effect || 0) * 100)}% of the benefit survived.`)}</p>`
        : "";

    const support = packet.supporting_analyses;
    if (support && (support.analyses || []).length) {
      $("decContextPanel").classList.remove("hidden");
      $("decContext").innerHTML =
        support.analyses.map(a =>
          `<p class="cap"><b>${esc(a.kind)}</b> — ${
            (a.protocol.limits || []).map(esc).join("; ")}</p>`).join("") +
        `<p class="cap">${esc(support.disclosure)}</p>`;
    }

    if (packet.tier) {
      $("decSensitive").insertAdjacentHTML("beforebegin",
        `<p class="cap">${esc(packet.tier.disclosure)}</p>` +
        (packet.tier.shortfall.length
          ? `<p class="cap">${tt(
              `本次未达 ${esc(packet.tier.precision)} 档的下限：` +
              packet.tier.shortfall.map(s => `${esc(s.axis)} 有 ${s.have} 个、需要 ${s.needs} 个`).join("；"),
              `Below the ${esc(packet.tier.precision)} tier's floor: ` +
              packet.tier.shortfall.map(s => `${esc(s.axis)} had ${s.have}, needs ${s.needs}`).join("; "))}</p>`
          : ""));
    }

    $("decChoicePanel").classList.remove("hidden");
    $("decChoice").innerHTML =
      `<p class="cap">${tt(`当前状态：<b>${esc((packet.choice_state || {}).state || "open")}</b>。` +
            `这份 packet 记录的是「在这些假设下、用这个协议算出来的结论」，不是建议。`,
            `Current state: <b>${esc((packet.choice_state || {}).state || "open")}</b>. ` +
            `This packet records what the numbers say under these assumptions and this protocol — it is not advice.`)}</p>` +
      `<p class="cap mono">${esc(packet.protocol.precision)} · ${
        Number(packet.protocol.paths || 0).toLocaleString()} paths · seed ${esc(packet.protocol.seed)} · ${
        esc(packet.protocol.engine_version)}</p>` +
      // ROADMAP: a packet carries a review date, and must be exportable
      // offline so its core numbers can be re-checked away from this app.
      `<p class="cap">${tt(`复核日期：<b>${packet.review_months} 个月后</b>。`,
                           `Review date: <b>in ${packet.review_months} months</b>.`)}</p>` +
      `<div class="compute-row" style="margin-top:10px">` +
      `<button class="btn-ghost sm" id="decExport">${tt("导出这份 packet (.json)", "Export this packet (.json)")}</button>` +
      `<span class="cap">${tt("含 baseline 配置、每个方案的改动、协议与全部判定 —— 离线即可复核。",
                             "Carries the baseline config, each alternative's changes, the protocol and every verdict — enough to re-check offline.")}</span></div>` +
      ((packet.families_missing || []).length
        ? (() => {
            // Total comes from the server, which reads it off the family tuple.
            // This was `7 -` written into the page, and it did not track that
            // tuple: adding an eighth family made the count report one FEWER
            // covered family than the plan had, understating the checking that
            // was actually done.
            const total = packet.families_total || 0;
            const covered = total - packet.families_missing.length;
            return `<p class="cap">${tt(`注意：${total} 类不利假设里，这个计划只覆盖了 ${
              covered} 类。未覆盖的是没被检验，不是通过了检验。`,
              `Note: of ${total} adverse-assumption families this plan covered ${
              covered}. The rest were not tested — that is not the same as passing.`)}</p>`;
          })()
        : "");
    const exportBtn = $("decExport");
    if (exportBtn) exportBtn.addEventListener("click", decExportPacket);
    renderDecArchive();
  }

  // ------------------------------------------------------- decision archive
  // Phase 4. Until this existed a packet lived in one job and died with the
  // process, so "what you decided last year" had nothing behind it. Archiving
  // is explicit: this app does not decide that a computation was a decision.

  function renderDecArchive() {
    const ref = archiveRefForReview();
    const host = $("decArchive");
    if (!host) return;
    if (!ref || !ref.plan_id || !ref.plan_version_id) {
      // Said plainly rather than by a disabled button with no explanation:
      // the archive attaches a decision to an archived plan version, and a
      // user who has only run in-memory has none.
      host.innerHTML = `<p class="cap">${tt(
        "这份决定还不能存档：存档要挂在一次已归档的计划版本上，所以要<b>先做一次正式运行</b>（勾选归档）。",
        "This decision cannot be archived yet: a decision is attached to an archived plan version, so it "
        + "needs a formal run first (with archiving on).")}</p>`;
      return;
    }
    const saved = DEC.archived;
    host.innerHTML =
      `<div class="compute-row">` +
      (saved
        ? ""
        : `<button class="btn-ghost sm" id="decArchiveSave">${tt(
            "把这份决定存进档案", "Archive this decision")}</button>`) +
      `<span class="cap">${
        saved
          ? tt(`已存档 · <span class="mono">${esc(saved.packet_id)}</span>`,
               `Archived · <span class="mono">${esc(saved.packet_id)}</span>`)
          : tt("存档后明年复核时才有「当年你决定了什么」可对照。",
               "Archiving is what gives next year's review something to compare against.")
      }</span></div>` +
      (saved ? decChoiceControlsHtml(saved) : "");
    const save = $("decArchiveSave");
    if (save) save.addEventListener("click", decArchiveSave);
    (DEC.archived ? DEC_STATES : []).forEach(state => {
      const btn = $("decState_" + state.key);
      if (btn) btn.addEventListener("click", () => decArchiveDecide(state.key));
    });
  }

  //: The moves a user can make on an archived decision. `superseded` is not
  //: offered: it is what a NEW packet for the same question does to the old
  //: one, not a button, and the archive treats it as final.
  const DEC_STATES = [
    // `["zh", "en"]` pairs rather than named fields: the panel's bilingual
    // gate reads a CJK literal followed immediately by a non-CJK one, and a
    // `zh:`/`en:` shape puts a key between them. It caught this on the first
    // run, which is the gate doing its job rather than being fussy -- a
    // string only one language ever sees is the standing rule's whole target.
    { key: "chosen", label: ["我选了这个", "I chose this"] },
    { key: "declined", label: ["我没选", "I declined it"] },
    { key: "deferred", label: ["先放着", "I put it down for now"] },
  ];

  function decChoiceControlsHtml(saved) {
    const cs = saved.choice_state || { state: "open", history: [] };
    const rows = (cs.history || []).map(h =>
      `<p class="cap mono">${esc(h.at)} · ${esc(h.from)} → ${esc(h.to)} · ${esc(h.reason)}</p>`
    ).join("");
    return `<p class="cap" style="margin-top:10px">${tt(
        `档案里的状态：<b>${esc(cs.state)}</b>。`,
        `State in the archive: <b>${esc(cs.state)}</b>.`)}</p>` +
      // An input rather than `window.prompt`. The reason is archived
      // immutably beside the decision, so it deserves a field with room in
      // it — and a native dialog is the one control on this page that cannot
      // be driven, which would leave this path verified by reading alone.
      `<input id="decStateReason" type="text" style="width:100%;margin:8px 0"` +
      ` placeholder="${tt("为什么？这句话会和决定一起存进档案，之后不能改",
                          "Why? Archived alongside the decision, and not editable afterwards")}">` +
      `<div class="compute-row">` +
      DEC_STATES.map(s =>
        `<button class="btn-ghost sm" id="decState_${s.key}">${tt(s.label[0], s.label[1])}</button>`
      ).join("") + `</div>` +
      `<p class="cap" id="decStateHint">${tt(
        "每次改变都要写一句理由，并且只会追加——档案里的决定不会被改写。",
        "Every change needs a reason, and only ever appends: an archived decision is never rewritten.")}</p>` +
      rows;
  }

  // ---- the review view -------------------------------------------------
  //
  // It shows what was decided and what has moved under it. It does NOT say
  // whether the decision was right, and the server says so in the payload:
  // a packet is a claim about thousands of paths over decades, and what has
  // happened since is one sample from that distribution. The page prints
  // that sentence rather than paraphrasing it, because a paraphrase is where
  // "we cannot score this" quietly becomes "here is how it did".

  function decReviewChangeRows(entry) {
    if (entry.config_changes === null) {
      return `<p class="cap">${esc(entry.config_changes_note || "")}</p>`;
    }
    if (!entry.config_changes.length) {
      return `<p class="cap">${tt("这个计划自那次决定以来没有改过。",
                                  "The plan has not changed since this decision.")}</p>`;
    }
    const rows = entry.config_changes.slice(0, 12).map(row =>
      `<tr><td><code>${esc(row.path)}</code></td>` +
      `<td>${esc(row.appeared ? "—" : String(row.was))}</td>` +
      `<td>${esc(row.removed ? "—" : String(row.now))}</td></tr>`).join("");
    const more = entry.config_changes.length > 12
      ? `<p class="cap">${tt(`另有 ${entry.config_changes.length - 12} 项未列出。`,
                             `${entry.config_changes.length - 12} more not shown.`)}</p>`
      : "";
    return `<p class="cap">${esc(entry.config_changes_note || "")}</p>` +
      `<table class="rev-table"><thead><tr>` +
      `<th>${tt("字段", "Field")}</th><th>${tt("决定时", "Then")}</th>` +
      `<th>${tt("现在", "Now")}</th></tr></thead><tbody>${rows}</tbody></table>` + more;
  }

  function decReviewHtml(v) {
    if (!v.decision_archive_installed || !v.packets.length) {
      return `<p class="cap">${tt("还没有归档过的决定。跑一次决策研究、把结论存档，它就会出现在这里。",
                                  "No archived decisions yet. Run a decision study and archive its packet, and it will appear here.")}</p>`;
    }
    const cards = v.packets.map(entry => {
      const st = entry.choice_state || {};
      const due = entry.due
        ? `<b>${tt("到期该复核了", "Review due")}</b>`
        : `<span class="cap">${tt("复核日", "Review on")} ${esc(String(entry.review_due_at).slice(0, 10))}</span>`;
      const checkins = entry.checkins_since === null
        ? `<p class="cap">${tt("这个档案里读不到 check-in 记录。",
                               "This archive cannot report check-ins.")}</p>`
        : `<p class="cap">${tt(`那次决定之后记录了 ${entry.checkins_since.length} 次复核。`,
                               `${entry.checkins_since.length} check-in(s) recorded since that decision.`)}</p>`;
      const engine = entry.engine_moved
        ? `<p class="cap">${tt(`引擎已从 ${entry.engine_version} 变为 ${entry.engine_version_now} —— 今天重算未必给出同样的数。`,
                               `The engine has moved from ${entry.engine_version} to ${entry.engine_version_now}; recomputing today need not give the same numbers.`)}</p>`
        : "";
      return `<div class="panel"><div class="panel-title sm">${esc(entry.question || entry.question_id)}</div>` +
        `<p class="cap">${tt("决定于", "Decided")} ${esc(String(entry.created_at).slice(0, 10))} · ` +
        `${tt("状态", "State")}: <b>${esc(st.state || "open")}</b>` +
        (st.reason ? ` · ${esc(st.reason)}` : "") + ` · ${due}</p>` +
        decReviewChangeRows(entry) + engine + checkins + `</div>`;
    }).join("");
    return cards +
      `<p class="cap"><b>${tt("这一页不评判那个决定对不对。", "This page does not say whether the decision was right.")}</b> ` +
      `${esc(v.why_not_scored)}</p>`;
  }

  async function runDecisionReview() {
    const ref = archiveRefForReview();
    if (!ref || !ref.plan_id) {
      $("decReviewHint").textContent = tt(
        "先保存一个计划：归档的决定挂在计划上。",
        "Save a plan first: archived decisions hang off one.");
      return;
    }
    $("decReviewHint").textContent = tt("读取中…", "Loading…");
    try {
      const v = await revGet("/api/decision/review?plan_id=" +
                             encodeURIComponent(ref.plan_id));
      $("decReviewOut").innerHTML = decReviewHtml(v);
      $("decReviewHint").textContent = "";
    } catch (err) {
      $("decReviewHint").textContent = String(err.message || err);
    }
  }

  async function decArchiveSave() {
    const ref = archiveRefForReview();
    try {
      const out = await postJSON("/api/decision/archive", {
        plan_id: ref.plan_id, plan_version_id: ref.plan_version_id,
        packet: DEC.packet,
      });
      DEC.archived = { packet_id: out.packet_id,
                       choice_state: { state: "open", reason: "", history: [] } };
      // Read the state back rather than assuming `open`: if this packet was
      // archived and decided in an earlier session, the archive already knows
      // something this page does not.
      try {
        const got = await revGet("/api/decision/archive/get?packet_id="
                                  + encodeURIComponent(out.packet_id));
        DEC.archived.choice_state = (got.packet || {}).choice_state
          || DEC.archived.choice_state;
      } catch (e) { /* the save is what mattered; the state re-read is a bonus */ }
      renderDecArchive();
      toast(out.already_archived
        ? tt("这份决定已经在档案里了", "This decision was already archived")
        : tt("决定已存档", "Decision archived"));
    } catch (err) {
      toast(tt("存档失败：", "Could not archive: ") + (err.message || err));
    }
  }

  async function decArchiveDecide(state) {
    const field = $("decStateReason");
    const reason = String((field && field.value) || "").trim();
    if (!reason) {
      // Said, not swallowed. A button that does nothing when a field is empty
      // reads as a broken button.
      $("decStateHint").textContent = tt(
        "先写一句理由 —— 没有理由的状态，之后没人能据它做事。",
        "Write a reason first: a state without one is a fact nobody can act on later.");
      if (field) field.focus();
      return;
    }
    try {
      const out = await postJSON("/api/decision/archive/state", {
        packet_id: DEC.archived.packet_id, state, reason: reason.trim(),
      });
      DEC.archived.choice_state = out.choice_state;
      renderDecArchive();
      toast(tt("已记录", "Recorded"));
    } catch (err) {
      toast(tt("没有记录：", "Not recorded: ") + (err.message || err));
    }
  }

  function decExportPacket() {
    // The packet as computed, not a summary of it. Re-checking offline means
    // recomputing from the same config and protocol, so anything dropped here
    // is a number the reader has to take on trust.
    const blob = new Blob([JSON.stringify(DEC.packet, null, 2)],
                          { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `fire-decision-${DEC.packet.question_id}-${DEC.packet.protocol.seed}.json`;
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(a.href);
    toast(tt("packet 已导出", "Packet exported"));
  }

  function decAxisLabel(name) {
    return { seeds: tt("种子", "Seeds"),
             return_models: tt("收益模型", "Return models"),
             adverse_packs: tt("不利假设", "Adverse assumptions") }[name] || name;
  }

  function decMetricLabel(metric) {
    return { lifetime_success: tt("成功率", "Lifetime success"),
             terminal_real_p50: tt("终值中位数（实际购买力）", "Terminal real P50"),
             fire_age_p50: tt("FIRE 年龄中位数", "FIRE age P50"),
             mean_real_consumption: tt("消费中位数（实际购买力）", "Real consumption P50") }[metric] || metric;
  }

  function decFmt(metric, value, isDelta) {
    if (value == null) return "—";
    if (metric === "lifetime_success") return (value * 100).toFixed(1) + "pp";
    if (metric === "fire_age_p50") return (isDelta ? value.toFixed(1) : Math.round(value));
    return C.money(value);
  }

  function renderConclusions() {
    const cards = buildConclusionCards();
    $("conclusions").innerHTML = cards.map(cardHtml).join("");
  }
  function buildConclusionCards() {
    const s = D(), fa = s.fire_age, mc = s.mean_real_consumption, mn = s.min_real_consumption, tr = s.terminal_real;
    const out = [];
    // 1 solvency
    const ls = s.lifetime_success;
    if (ls >= 0.95) out.push(card("good", tt("破产几乎不是约束", "Ruin is barely the constraint"),
      tt(`在你的参数下三分支成功率 <b>${pct(ls)}</b>。其中包含退休前身故；判断破产风险请同时看 FIRE 后偿付率，差异更多落在<b>消费与遗产分布</b>。`, `Three-branch success is <b>${pct(ls)}</b> under your inputs. It includes death before retirement; use post-FIRE solvency for depletion risk. The remaining difference lives in the <b>consumption and legacy distributions</b>.`)));
    else out.push(card("warn", tt("成功率与偿付需要关注", "Success and solvency need attention"),
      tt(`三分支成功率 <b>${pct(ls)}</b>，FIRE 后偿付率 <b>${pct(s.post_fire_solvency)}</b>。可探索的方向：更低的 SWR、更晚的退休时点、或更高的储蓄——用「敏感性/SWR」板块量化各自的影响后自行权衡。`, `Three-branch success is <b>${pct(ls)}</b> (post-FIRE solvency <b>${pct(s.post_fire_solvency)}</b>). Directions to explore: a lower SWR, a later retirement date, or higher savings — quantify each in Sensitivity/SWR and weigh them yourself.`)));
    // 2 fire timing
    if (fa.p50 != null) out.push(card("neutral", tt("FIRE 时点", "FIRE timing"),
      tt(`预计 <b>P50 ${Math.round(fa.p50)} 岁</b>达到财务独立（P10–P90 ${Math.round(fa.p10)}–${Math.round(fa.p90)}，最早 ${fa.min}）。到达 FI 率 ${pct(s.reached_fi_rate)}——其补集多为 FIRE 前身故，而非攒不够。`, `Financial independence at <b>P50 age ${Math.round(fa.p50)}</b> (P10–P90 ${Math.round(fa.p10)}–${Math.round(fa.p90)}, earliest ${fa.min}). Reached-FI rate ${pct(s.reached_fi_rate)} — the complement is mostly death before FIRE, not under-saving.`)));
    // 3 consumption + floor
    out.push(card("neutral", tt("退休消费与地板", "Retirement consumption & floor"),
      tt(`可持续 real 消费 <b>P50 ${money(mc.p50)}</b>（P10 ${money(mc.p10)}–P90 ${money(mc.p90)}）。最坏年份地板约 <b>${money(mn.p10)}</b>（P10）——这是 GK 护栏把尾部风险转成的消费波动。`, `Sustainable real spending <b>P50 ${money(mc.p50)}</b> (P10 ${money(mc.p10)}–P90 ${money(mc.p90)}). Worst-year floor ≈ <b>${money(mn.p10)}</b> (P10) — GK converts tail risk into consumption variance.`)));
    // 4 legacy
    out.push(card("neutral", tt("遗产规模", "Legacy"),
      tt(`存活路径的实际购买力终值 <b>P50 ${money(tr.p50)}</b>（P10 ${money(tr.p10)}–P90 ${money(tr.p90)}）。强右偏，P50 远低于均值——不要用 P50 当「预期继承」。`, `Purchasing-power terminal on solvent paths <b>P50 ${money(tr.p50)}</b> (P10 ${money(tr.p10)}–P90 ${money(tr.p90)}). Right-skewed — don't read P50 as an expected bequest.`)));
    // 5 sensitivity (if computed)
    if (state.od.sens) {
      const b = state.od.sens.mu_band, lo = b[0], hi = b[b.length - 1];
      out.push(card("warn", tt("精度 ≠ 准确", "Precision ≠ accuracy"),
        tt(`把收益率 μ 在 ±1.5pp 内挪动，终值 real P50 从 <b>${money(lo.terminal_real_p50)}</b> 变到 <b>${money(hi.terminal_real_p50)}</b>——盖过任何单一决策杠杆，而三分支成功率几乎不动。遗产/消费的真实区间由参数不确定性主导。`, `Moving return μ within ±1.5pp swings terminal real P50 from <b>${money(lo.terminal_real_p50)}</b> to <b>${money(hi.terminal_real_p50)}</b> — dwarfing any single lever, while three-branch success barely moves. Parameter uncertainty dominates the real range of legacy/consumption.`)));
    }
    // 6 SWR (if computed)
    if (state.od.swr) {
      const pts = state.od.swr.points, cur = +get(state.config, "state.swr_pref");
      const drop = pts.find(p => p.lifetime_success < 0.90);
      out.push(card(drop ? "warn" : "good", tt("SWR 权衡", "SWR trade-off"),
        drop ? tt(`三分支成功率在 SWR ≈ <b>${(drop.value * 100).toFixed(1)}%</b> 起明显跌破 90%。你当前 ${(cur * 100).toFixed(2)}%；抬 SWR 提消费但侵蚀成功率。`, `Three-branch success drops below 90% around SWR ≈ <b>${(drop.value * 100).toFixed(1)}%</b>. You're at ${(cur * 100).toFixed(2)}%; raising SWR lifts spending but erodes success.`)
          : tt(`在扫描范围内三分支成功率都很稳（即使较高 SWR）。你当前 ${(cur * 100).toFixed(2)}%；另看 FIRE 后偿付率判断实际耗尽风险。`, `Three-branch success stays robust across the swept range (even at higher SWR). You're at ${(cur * 100).toFixed(2)}%; use post-FIRE solvency to assess depletion risk.`)));
    }
    // 7 relocation (if enabled)
    if (state.data.relocation) {
      const H = state.data.home, R = state.data.relocation;
      const rc = (R.mean_lifestyle_real ? R.mean_lifestyle_real.p50 : R.mean_real_consumption.p50);
      const dCons = rc - H.mean_real_consumption.p50, dTerm = R.terminal_real.p50 - H.terminal_real.p50, dS = R.lifetime_success - H.lifetime_success;
      const destId = get(state.config, "relocation.destination") || "custom";
      const dname = (DEST.find(x => x.id === destId) || { name: [tt("目的地", "destination")] }).name[L === "zh" ? 0 : 1];
      const nearNeutral = Math.abs(dS) < 0.01;
      out.push(card(nearNeutral ? "neutral" : (dS > 0 ? "good" : "warn"), tt("搬迁：" + dname, "Relocation: " + dname),
        tt(`三分支成功率差 <b>${(dS * 100 >= 0 ? "+" : "") + (dS * 100).toFixed(2)}pp</b>${nearNeutral ? "（近似财务中性）" : ""}；生活水准等价消费 <b>${(dCons >= 0 ? "+" : "") + money(dCons)}/yr</b>，实际期末组合 <b>${(dTerm >= 0 ? "+" : "") + money(dTerm)}</b>。若财务近中性，决定因素应是家庭/身份而非这张图。`, `Three-branch success differs by <b>${(dS * 100 >= 0 ? "+" : "") + (dS * 100).toFixed(2)}pp</b>${nearNeutral ? " (≈ financially neutral)" : ""}; lifestyle-equivalent spending <b>${(dCons >= 0 ? "+" : "") + money(dCons)}/yr</b>, real terminal <b>${(dTerm >= 0 ? "+" : "") + money(dTerm)}</b>. If near-neutral, the deciding factor is family/status, not this chart.`)));
    }
    // 8 backtest (if computed)
    if (state.od.bt) {
      const sc = state.od.bt.scenarios, all = Object.values(sc), surv = all.filter(x => x.survived).length;
      out.push(card(surv === all.length ? "good" : "warn", tt("序列风险", "Sequence risk"),
        tt(`在 ${all.length} 个风格化坏开局里，${surv} 个存活。GK 护栏通过在坏年份下调消费保住组合——代价是消费波动而非破产。（示意序列，非真实指数）`, `Across ${all.length} stylized bad openings, ${surv} survived. GK guardrails preserve the portfolio by cutting consumption in bad years — variance, not ruin. (Stylized, not literal index data.)`)));
    } else {
      out.push(card("neutral", tt("想要更完整的结论？", "Want fuller conclusions?"),
        tt(`到「敏感性 & 压力」页跑一下 tornado / SWR / 回测，这里会自动补上对应的结论。`, `Run the tornado / SWR / backtest on the Sensitivity & stress page — matching conclusions appear here automatically.`)));
    }
    return out;
  }
  const LIMITATIONS = [
    ["收益（默认模式）：年度 iid 抽样，市场 regime 每条路径抽一次并终身固定——序列风险以这种风格化方式建模。可在「高级」切换收益 2.0：Markov regime 年切换（含 AR(1) 通胀惯性）或 1928–2024 历史块重演。历史块模式的局限：只有 97 年样本可抽、历史表为 Damodaran/BLS 口径的年度近似（已在测试中钉住）、μ 敏感性分析对它不适用、随机通胀开关被历史 CPI 取代。",
      "Returns (default): annual iid draws with one lifetime regime per path — sequence risk modeled in this stylized way. Returns 2.0 (Advanced): Markov annual regime switching (with AR(1) inflation persistence) or 1928–2024 historical block replay. Blocks caveats: only 97 sample years, the table is a Damodaran/BLS-style annual approximation (pinned by tests), μ-sensitivity does not apply to it, and the stochastic-inflation toggle is superseded by historical CPI."],
    ["通胀：默认确定性（可在「高级」里开随机）；Markov 收益模式下可加 AR(1) 通胀惯性、历史块模式直接重演真实 CPI 序列——默认 iid 模式仍不建模自相关，也不建模与利率的联动。",
      "Inflation: deterministic by default (stochastic optional in Advanced); Markov returns mode adds optional AR(1) inflation persistence, and blocks mode replays the real CPI sequence — the default iid mode still has no autocorrelation, and no rate linkage in any mode."],
    ["税（默认模式）：平率/渐进近似——不含 RMD、利得堆叠等。开启「真实逐年税表」后为 2026 年表的真算：税档+利得堆叠+社保应税+RMD+IRMAA+NIIT+真 MAGI 的 ACA；IRMAA 在有可用模型历史时使用保费年前两年的最终 MAGI 与报税身份，否则退回当年 MAGI 代理；NIIT 为 3.8%，按「净投资收益」与「MAGI 超过门槛的部分」两者中的较小值计征，其中 Roth 转换与税前提取本身不是投资收益、但会抬高 MAGI 从而把已实现的资本利得推过门槛。<b>NIIT 的 20 万/25 万门槛是 2013 年立法定死的名义金额，法律上不随通胀调整，本模型也刻意不调整</b>——所以在长期限里它会自己变成一笔越来越普遍的税，这是「不维护」写成的加税，不是本模型的近似误差；仍不含州税细档、逐笔成本基础、PY−3/生活变故重新裁定、分开配偶税务身份与逐人 Medicare 年龄。终值给两个数：<b>遗产口径（默认头条）</b>按美国法的基础提升计 —— 计划末尾剩下的钱按定义就是遗产，应税账户的未实现利得永不计税；<b>变现口径</b>假设末年全部卖出，只对已测量的利得计税。两个数里税前 401k 都仍按普通收入扣税：继承人拿它要交所得税，没有基础提升。真税关闭时没有成本基础可测，变现口径退回你填的「应税提取的利得占比」代理，那是代理不是测量。实测提醒：本引擎优先提取应税账户，所以默认计划里它在末尾往往已经空了 —— 那时两个数相同，基础提升无从发生，这是正确答案而不是没算。<b>真实逐年税表只在本国（美国）税基下生效</b>：一旦设定了搬迁年龄，搬迁当年之后的税前提取改用目的地的平坦有效税率，上面列的税档、利得堆叠、社保应税、RMD、IRMAA 与真 MAGI 的 ACA 全部不再参与计算。",
      "Taxes (default): flat/progressive approximations — no RMD or gain stacking. True yearly taxes use 2026 brackets, LTCG stacking, SS taxation, RMD, IRMAA, NIIT, and true-MAGI ACA; when modeled history exists, IRMAA uses final MAGI and filing status from two tax years before the premium year, otherwise it falls back to the current-year MAGI proxy. NIIT is 3.8% of the lesser of net investment income and the amount by which MAGI clears the threshold — a Roth conversion or pretax withdrawal is not investment income itself, but it lifts MAGI and can drag realised gains over the line. <b>Its $200k/$250k thresholds were fixed in statute in 2013, are not indexed to inflation, and are deliberately not indexed here</b>: over a long horizon it becomes a tax almost every modelled household eventually pays, which is a tax increase written as an absence of maintenance rather than an approximation in this model. PY−3 recovery, life-changing-event redeterminations, separate spouse tax status, split-age Medicare eligibility, state-bracket detail, and per-lot basis remain outside this contract. Terminal value is reported two ways. The <b>bequest figure (the headline)</b> applies the US basis step-up at death: money left at the end of a plan is an inheritance by definition, and unrealised gains in a taxable account are never taxed. The <b>liquidated figure</b> assumes a final-year sale and taxes only the MEASURED gain. Both still tax a pretax 401(k) as ordinary income, because heirs get no step-up on it. With true taxes off no basis is tracked, so the liquidated figure falls back to your configured gain-share proxy — a proxy, not a measurement. Measured caveat: the engine withdraws from the taxable account first, so on the default plan it is usually empty by the end, the two figures then agree, and there is no step-up to apply — that is the right answer rather than a missing calculation. <b>True yearly taxes apply on the home (US) basis only</b>: once a relocation age is set, pretax withdrawals from that year onward use the destination's flat effective rate, and the brackets, gain stacking, SS taxation, RMD, IRMAA and true-MAGI ACA listed above stop applying."],
    ["医疗：默认采用 2026 恢复的 ACA 400% FPL 补贴悬崖（2025 FPL、300–400% 档 9.96%）；这里保留 9.96% 平率代理，不代表完整的 IRS 分段适用比例表：300% FPL 以下通常会低估 PTC，但 100% FPL 以下可能高估 PTC，因为模型没有判断 Medicaid/ACA eligibility。可选的「逐年医疗轨迹」把常规医疗、自付额与保费按每条路径已实现 CPI 加确定性差额重算；它不是独立随机抽样。65 岁前使用用户提供且未经 App 核验的年度家庭 ACA 全价锚点并减既有补贴；到设置年龄后切换为单一年度家庭 Medicare 锚点，IRMAA 仍另加。年龄费率曲线是可选的，且由你自己的两个报价定义：填了「ACA 保费 · 末年报价」之后，65 岁前保费按两个报价之比、每年固定百分比逐年走，两个报价年龄之外持平不外推；没填就是没测过年龄效应（保费维持单锚点，不代表年龄效应为零）。App 不内置任何 CMS 或州×年龄保费表，两个报价都是你自己填的、未经 App 核验。逐人语义同样是可选的：填了「医疗中属于本人的比例」（需同时启用夫妻共同）之后，上面那些家庭合计按该比例拆成两人份，夫妻各自按自己的年龄从 ACA 切 Medicare，首位死亡后美国医疗篮子降到在世那一位的份额；没填就是没拆过，保持家庭整体口径，不代表五五开。拆开后仍有一处已知偏保守：ACA 补贴的开关仍按计划持有人的年龄判断，所以「持有人已进 Medicare、配偶仍在买 ACA」的年份不计补贴；反向年份的补贴只按 ACA 那一部分计算，不会误补到 Medicare 保费上。临终医疗支出峰也是可选的：填了「临终医疗支出峰」（需开启死亡率抽样）之后，每一次死亡各计一次——夫妻先走那位的峰进当年支出，第二位的峰在路径终止那年从组合扣，因此夫妻会产生两次而不是一次。它不走 ACA 补贴，也不按目的地生活成本折算；没填就是没给过这个数（未测量），与「开了但这条路径没死」的 0 分开报。它建模的是死亡当年的一笔支出，不含临终前若干年的费用爬升。它仍不含 Part B/D/Medigap 分层；搬迁后改用目的地医疗，不再叠加美国篮子。长期护理尾部默认不建模（部分由 eldercare 冲击近似）。",
      "Healthcare: defaults use the restored 2026 ACA subsidy cliff at 400% FPL (2025 FPL; 9.96% in the 300–400% band), a flat proxy rather than the full IRS schedule: it generally understates PTC below 300% FPL but can overstate it below 100% FPL because Medicaid/ACA eligibility is not modeled. The optional Annual medical trajectory rebuilds routine care, out-of-pocket cost, and premiums from each path's realized CPI plus deterministic spreads; it is not an independent stochastic draw. Before 65 it uses the user-supplied, App-unverified annual household ACA full-price anchor minus the existing subsidy; at the configured age it switches to one annual household Medicare anchor, with IRMAA still added separately. The age-rating curve is OPTIONAL and defined by your own two quotes: fill in the ACA final-year quote and the pre-Medicare premium follows the ratio between them at one fixed percentage a year, held flat outside the two quoted ages rather than extrapolated; leave it blank and the age effect is simply unmeasured (the premium keeps its single anchor, which does not mean the age effect is zero). No CMS or state-by-age premium table ships in the App: both quotes are yours and neither is verified by the App. Per-person treatment is optional in the same way: fill in your share of household medical (the couple/household must also be enabled) and those household totals are split in that ratio, each spouse moves from ACA to Medicare at their own age, and after the first death the US basket drops to the surviving member's share. Leave it blank and the household-wide treatment is kept — that is unmeasured, not fifty-fifty. One conservative gap remains once split: the ACA subsidy still switches off on the plan holder's age, so no subsidy is modelled in years when the holder is on Medicare while the spouse is still buying a bridge policy; in the opposite years the subsidy is computed against the ACA portion alone and can never pay down a Medicare premium. The end-of-life peak is optional too: fill it in (mortality sampling must be on) and it is charged once per death — for a couple the first death's peak joins that year's spending and the second is drawn in the year the run ends, so a couple produces two rather than one. It does not pass through the ACA subsidy and is not re-priced by destination cost of living. Blank means no figure was ever supplied (unmeasured), reported separately from a path that was armed and simply never died. It models one charge in the year of death and does NOT model the ramp-up in the years before it. It still has no Part B/D/Medigap tiers; after relocation, destination healthcare replaces rather than stacks with the US basket. Long-term-care tails remain off unless separately enabled."],
    ["父母生命周期模块（可选，默认关）：把原本互不相识的「赡养冲击」与「继承」合并为<b>一位父母、一次死亡</b>。开启后它<b>取代</b>那两个旧模块（三者同开会为一次衰老付两次钱、为一次死亡继承两次，所以同开会被直接拒绝并说明）。它修的是一个真实的不自洽：旧的赡养冲击在 40–70 岁窗口内<b>逐年独立</b>触发，可以命中 31 次 —— 隐含一位永不去世的父母；旧的继承则把到账年龄在 55–80 上<b>均匀</b>抽取，与护理毫无关系，于是可以 57 岁继承、68 岁还在付护理费。现在：父母的死亡年龄用<b>你计划自己那张死亡率表</b>抽一次，护理只能发生在生前且<b>在死亡处截断</b>，遗产<b>落在死亡当年</b>。护理费先由父母自己的积蓄承担（`estate_share_of_care` 拨盘，默认 1.0 = 先花光父母的钱，剩下的才落到你头上），所以久病必然减少遗产 —— 这是一个可核对的机制，不是一个相关系数。五点须知：(1) 护理过程<b>直接复用 LTC 模块</b>的进入率、时长分布与费用，本模块<b>不引入任何新的经验常数</b>；(2) 只建模退休期 —— 退休前的父母护理、以及超出模拟期的部分，都不计费；(3) 父母的死亡在你的模拟窗口之外时，遗产报为<b>未知（None）而不是 0</b>，因为「没留下」和「窗口内没发生」是两回事；(4) 开启后<b>终值通常会上升</b>，因为遗产是一笔流入 —— 一份靠遗产才成立的计划应当被单独审视；(5) 父母之间不建模相互照护、再婚、多次婚姻或非婚生继承顺序。父母的现有年龄、积蓄、护理风险<b>全部由你自己填</b>，本模块不内置任何关于「典型父母」的数据。",
     "Parent lifecycle module (optional, off by default): merges the eldercare shock and the inheritance draw — until now two strangers — into <b>one parent with one death</b>. Switching it on <b>replaces</b> those two (running all three would bill you twice for one decline and credit you twice for one death, so that combination is refused outright with an explanation). It fixes a real incoherence: the old eldercare shock fired an <b>independent yearly</b> Bernoulli across a 40–70 window and could hit 31 times, implying a parent who never dies, while the old inheritance drew its arrival age <b>uniformly</b> over 55–80 with nothing connecting it to the care — so a path could inherit at 57 and keep paying for care until 68. Now: the parent's death age is drawn once from <b>your plan's own mortality table</b>, care can only happen while they are alive and is <b>truncated at death</b>, and the bequest lands <b>on the death itself</b>. Care is paid from the parent's own savings first (the <code>estate_share_of_care</code> dial, default 1.0 = their money is spent before yours), so a long decline necessarily leaves less to inherit — a mechanism you can check against your own family, not a correlation coefficient. Five things worth knowing: (1) the care process is <b>reused wholesale from the LTC module</b> — entry, duration, cost — so this module introduces <b>no new empirical constant</b>; (2) only retirement is modelled: a parent needing care before you retire, or past the simulation horizon, is not charged; (3) when a parent's death falls outside your modelled window the bequest is reported as <b>unknown (None), not 0</b>, because \"left nothing\" and \"did not happen in the window\" are different facts; (4) turning this on usually <b>raises</b> terminal wealth, because a bequest is an inflow — a plan that only works because of one deserves separate scrutiny; (5) care between parents, remarriage, multiple marriages and non-marital inheritance order are all unmodelled. Each parent's age, estate and care risk are <b>entirely yours to state</b>; this module ships no data about a \"typical parent\"."],
    ["长期护理（LTC）模块（可选，默认关）：建模<b>你自己</b>的护理（eldercare 冲击是为<b>父母</b>付钱，两者并存不合并）。两种模式：随机（按年龄的进入概率 + 时长分布，约 24% 的照护期超 2 年，这条长尾就是本模块存在的理由）或场景（你直接指定「假设 N 年」，就按 N 年算，不做任何调整）。默认关时引擎<b>一次都不抽</b>，结果与本模块不存在时逐位一致。几处必须知道的口径：① 进入概率按<b>你这份计划自己的死亡率表</b>标定，使「65 岁仍在世者中约有 47%（男）/ 58%（女）最终需要护理」在模拟里真的成立——不标定的话本模块只会报出约五分之一的发生率，方向恰好是让计划显得更安全；② 护理成本在原有生活开销之上<b>叠加</b>，不下调既有开销（机构费用通常已含食宿，因此偏保守）——要抵消请直接调低年成本参数；③ 年成本按「今日美元 + 每年 1% 超额通胀」从今天起复利，到 83 岁约为今天的 1.7 倍；④ 只在退休段建模，退休前进入护理、以及超出模拟年限的护理年份都不计费；⑤ 付不起时按 eldercare 同一条通道扣到账户见底（该路径随后会因消费缺口判负），不单独记录缺口年龄；⑥ <b>夫妻序贯护理尚未接入</b>——模型里有，引擎里没有，有一条测试专门断言这一点；⑦ Medicaid spend-down、长护险保单条款、非正式照护替代、照护期内的级别升级一律不建模。所有成本与概率都是可改参数，不是权威数字。",
      "Long-term care module (optional, off by default): models <b>your own</b> care — the eldercare shock models paying for a <b>parent</b>, and the two coexist rather than merge. Two modes: stochastic (age-based entry probability plus a duration distribution in which about 24% of episodes run past two years — that tail is the whole reason to model this) or scenario (you say \"assume N years\" and get exactly N years, with no adjustment). With the module off the engine takes <b>no draw at all</b>, so results are bit-identical to a build without it. Six things worth knowing: (1) entry probability is calibrated against <b>this plan's own mortality table</b> so that \"about 47% of men / 58% of women alive at 65 eventually need care\" actually holds in the simulation — uncalibrated, the module would report roughly a fifth of that incidence, in the direction that makes plans look safer; (2) care cost is <b>added on top of</b> existing spending rather than replacing part of it (facility fees usually include room and board, so this is conservative) — to offset it, lower the annual cost parameter directly; (3) annual cost compounds at 1%/yr in excess of general inflation from today, so by 83 it is about 1.7× today's figure; (4) only retirement is modeled: care starting before retirement, and care years past the simulation horizon, are not charged; (5) an unaffordable care year draws the accounts down through the same channel as an eldercare shock (that path then fails on the consumption shortfall) rather than recording a separate shortfall age; (6) <b>couple-sequential care is not wired in</b> — it exists in the model and not in the engine, and a test asserts exactly that. Medicaid spend-down, LTC insurance policy terms, informal care substitution, and level escalation within an episode are all unmodeled. Every cost and probability is an editable parameter, not an authority."],
    ["汇率：默认纯对数随机游走（会高估搬迁情景的长期汇率离散度）；可在搬迁设置里开 PPP 均值回归锚（κ>0，向初始汇率回归）——锚定的是「初始汇率≈公允」这一假设本身，若当前汇率显著偏离购买力平价，结果会系统性偏向锚点。",
      "FX: pure lognormal random walk by default (overstates long-run dispersion in the relocation scenario); an optional PPP mean-reversion anchor (κ>0, toward the initial rate) can be enabled in relocation settings — it anchors on the ASSUMPTION that the initial rate is fair value; if today's rate is far from PPP, results tilt toward the anchor systematically."],
    ["社保：已按领取年龄精算调整；FRA 67 与 70%/100%/124% 示意适用于 1960 年及以后出生 cohort，较早 cohort 的 FRA/调整比例可能不同。家庭模式下建模两份福利 + 遗属取较高者。未建模：收入测试、WEP/GPO、在世配偶福利（较高者的 50%）。",
      "Social Security: actuarially adjusted by claim age; FRA 67 and the 70%/100%/124% illustration apply to the 1960-and-later birth cohort, and earlier cohorts can have different FRA/adjustments. In household mode, two benefits + survivor keeps the higher. Not modeled: earnings test, WEP/GPO, or the spousal benefit (50% of the higher earner's while both alive)."],
    ["家庭：默认单人；家庭模式（可选）已建模配偶作为第二收入方、联合末生存者寿命、遗属支出下调、遗属社保与联合报税档。FIRE 前死亡按年末发生：死亡当年的缴款保留，此后停止死者工资相关缴款，并按修正后的积累路径重算 FIRE。FIRE 前家庭开销仍按全额且只扣一次；“丧偶后支出比例”仅在退休期生效。养老金、租金、兼职与 RSU/股权可归属你、配偶或家庭；归属成员身故后停止，家庭共同收入延续到末位生存者。尚未选择归属或旧计划显示「未确认归属」时沿用相同数字行为，但绝不代表共同所有；单人模式下所有有效归属都按本人。所有年龄仍按你的年龄轴。子女现金流按录入成本编译为 CPI 事件；未建模离婚/再婚、托育阶段、税收抵免、奖助学金与配偶独立税务身份。",
      "Household: optional household mode models a second earner, last-survivor longevity, survivor spending, survivor SS, and joint-filing brackets. Pre-FIRE death occurs at year-end: that death year's contributions remain, later wage-related contributions from the deceased stop, and FIRE is recalculated from the corrected accumulation path. The full pre-FIRE household expense is still charged exactly once; the survivor-spending setting applies only in retirement. Pension, rental, part-time, and RSU/equity can belong to you, your spouse, or the household: member-owned cash stops after that member dies, while shared cash follows the last survivor. An unchosen or legacy owner appears as Unconfirmed and keeps the same numeric behavior, but never claims shared ownership; in single-person mode every valid owner behaves as you. All ages remain on your timeline. Divorce/remarriage, childcare stages, credits/aid, and separate spouse tax status are unmodeled."],
    ["消费：退休消费默认 real 恒定并由 GK 护栏动态调整；可在「假设」里开启年龄相关的消费下滑（「退休消费微笑」，约 −1%/yr real）。",
      "Spending: real-constant by default, adjusted by GK guardrails; an age-related spending decline (the “retirement smile”, ≈−1%/yr real) can be turned on in Assumptions."],
    ["房产 / 租金：出租房净收入按录入的起止年龄（含首尾）提供税后可花现金，并按美国 CPI 指数化；退休前进入应税账户，退休后先覆盖开销、剩余再进应税。未单独建模出租房价值、升值、出售所得、空置、维修或税——这些应已包含在你填的净租金里。住房租买模块另算自住现金流，且无汇率通道。",
      "Property / rent: entered net rental income provides after-tax spendable cash over the inclusive start/end ages and is indexed to US CPI; before retirement it enters taxable, and after retirement it covers spending before any surplus enters taxable. Rental-property value, appreciation, sale proceeds, vacancy, repairs, and tax are not separately modeled; those should already be reflected in the net-rent input. The rent-vs-buy module separately models primary-home cash flows and has no FX channel."],
    ["住房模块（可选）：住房现金流走事件通道，不进 GK 消费预算——FI 门槛仍按你填的全口径年开销算，「年开销中的住房预算」逐年退回以免双计。蒙特卡洛中的固定名义按揭以购房年已实现 CPI 锚定，再按每条路径的后续已实现 CPI 折实；确定性图仍使用配置的平均通胀。买方房净值不进模拟组合（非流动），只在对比页单独列示；未建模：出售换现、房贷利息抵税、PMI、HELOC。",
      "Housing module (optional): housing cash flows use the event channel, outside the GK budget — the FI threshold stays on your full expenses figure, with the declared in-expenses housing budget refunded yearly to avoid double counting. Monte Carlo anchors the fixed nominal mortgage to realized CPI at purchase and deflates it with each path's subsequent realized CPI; the deterministic chart still uses configured mean inflation. Home equity never enters the simulated portfolio (illiquid) — it is shown separately on the comparison panel. Not modeled: sale proceeds, mortgage-interest deduction, PMI, HELOC."],
    ["城市库参数：内置目的地的生活成本 / 汇率 / 税 / 医疗为<b>示意默认值</b>，非精确报价——请按你的真实情况核对修改。一个目的地提供的是生活成本比、汇率波动、当地通胀、医疗成本、社保海外折减和<b>一个平坦提取税率</b>这几个参数，<b>不是一套税制</b>：当地税档、当地资本利得规则、税收协定、财富税与弃籍税均不建模，模型内部只区分「本国」与「已搬迁」两种税基。",
      "City library: each destination's cost / FX / tax / healthcare are <b>illustrative defaults</b>, not precise quotes — verify and edit for your real situation. A destination supplies cost of living, FX volatility, local inflation, healthcare costs, an SS-abroad haircut and <b>one flat withdrawal-tax rate</b> — <b>not a tax system</b>: local brackets, local capital-gains rules, tax treaties, wealth taxes and exit taxes are not modeled, and the model distinguishes only two tax bases, home and relocated."],
    ["年度规则的精确 vintage、维护状态与计划值差异现在随每次结果绑定，并显示在概览、结论、JSON 与报告中。`current` 只表示未超过本应用的复核期限；`stale` 或 `review_required` 的正式结论必须先核对当年官方数字。死亡率仍为 SSA 类拟合。",
      "Exact annual-rule vintages, review status, and plan-value differences are now bound to each result and shown in the overview, conclusions, JSON, and report. `current` means only that this app's review date has not passed; a `stale` or `review_required` conclusion needs current official figures checked first. Mortality remains an SSA-like fit."],
    ["精度 ≠ 准确：所有区间只反映蒙特卡洛<b>抽样</b>，不反映输入假设本身的不确定性。",
      "Precision ≠ accuracy: all intervals reflect Monte Carlo <b>sampling</b> only, not uncertainty in the assumptions themselves."],
    ["其他资产：现金/其他流动资产并入应税桶（同等税务处理）；自住房净值默认不计入模拟（非流动），除非设定「某年出售变现」。普通「其他资产」不模拟房产增值；出租净现金流仅在启用收入流时建模。",
      "Other assets: cash/other liquid fold into the taxable bucket (same tax treatment); home equity is excluded (illiquid) unless you set a planned sale. The generic Other Assets section does not model appreciation; net rental cash flow is modeled only when its income stream is enabled."],
    ["收入流（年金/租金/兼职/RSU）：统一按今日美元、税后可花现金处理；退休前进入应税账户，退休后先抵年度消费需求，剩余才进入应税账户。兼职从「录入的最早年龄」与「实际 FIRE 后第一年」中较晚者起，RSU 从下一模拟年起归属准确 N 次。非 COLA 年金在首次计划支付时按当时已实现 CPI 锚定名义金额，之后名义固定。重要近似：这些金额不直接计入普通收入、MAGI、ACA 或 IRMAA；只会通过减少组合提取间接影响它们，因此可能高估 ACA 补贴，并低估税与 IRMAA。",
      "Income streams (pension/rental/part-time/RSU) are today's-dollar, after-tax spendable cash: before retirement they enter taxable; after retirement they cover annual spending first and only the surplus enters taxable. Part-time starts at the later of its entered earliest age and the first year after actual FIRE; RSU pays exactly N times starting next modeled year. A non-COLA pension is anchored to realized CPI at its first scheduled payment and stays nominally fixed thereafter. Important approximation: these amounts do not directly enter ordinary income, MAGI, ACA, or IRMAA; they affect those only indirectly by reducing portfolio withdrawals, so the model may overstate ACA subsidies and understate tax and IRMAA."],
    ["人生事件：FIRE 前的支出只从应税账户扣（不动退休账户），FIRE 后按引擎提取顺序融资；任何未付足的强制支出都会记录年龄/缺口并把该路径判为失败，不再静默截断。事件金额为今日美元、按 CPI 调整。",
      "Life events: pre-FIRE outflows draw only from taxable; post-FIRE they use the engine withdrawal order. Any mandatory outflow that cannot be paid in full records its age/shortfall and fails that path instead of being silently truncated. Amounts are today's $, CPI-indexed."],
    ["序列回测：只把三条人工构造的坏开局套在 FI 门槛后的退休阶段；不是历史指数逐年回放、不覆盖积累期，也不给发生概率。若要抽样真实历史顺序，请改用高级设置里的历史块收益模式。",
      "Sequence backtest: applies three hand-built bad openings only to retirement starting at the FI target. It is not a year-by-year historical-index replay, does not cover accumulation, and assigns no occurrence probability. Use historical-block returns in Advanced to sample actual historical ordering."],
    ["隐私：界面通过本机回环 HTTP 与随应用运行的本地引擎通信，不连接外部主机。导入原文会短暂出现在这条本机请求与浏览器内存中，解析后即清除；不会写入计划、本机存储或日志。计划与偏好的存放位置取决于是否已迁移：迁移前在该应用 WebView 的本机存储中；迁移到本地数据库之后，计划存放在本机应用支持目录下的 SQLite 归档里，旧的浏览器存储不再被写入；尚未保存的向导草稿也一并放在同一目录下的一个私有文件中，因此重启后仍能继续。两种情况都只在本机，除非你主动导出。",
      "Privacy: the UI talks over loopback HTTP to the local engine bundled with the app; it does not contact external hosts. Import text briefly exists in that local request and browser memory, then is cleared after parsing; it is not written to plans, local storage, or logs. Where plans and preferences live depends on whether you have migrated: before migration, in this app WebView's local storage; after migrating to the local database, plans live in a SQLite archive under the app-support directory on this machine, the old browser storage is no longer written to, and an unsaved wizard draft is kept in a private file in that same directory so it survives a restart. Either way nothing leaves this machine unless you export it."],
    ["提取规则细节：消费微笑与生存者降档在提取规则输出之后按比例缩放（对策略库所有规则同样成立）——规则按未缩放预算评估，行为偏保守（结构性设计，已在引擎中注明）。",
      "Rule detail: the spending smile and survivor step-down scale the budget AFTER the withdrawal rule (true for every strategy-library rule alike) — rules evaluate the unscaled budget, a mildly conservative bias by construction (noted in the engine)."],
    ["策略对比页：VPW/ABW 的「数学上不破产」指提取额按当前组合的比例计算（含上限）——组合可以缩水到很小，消费随之深跌，「不破产」不等于「够花」。地板+上行的地板是预算口径：ACA 补贴省下的保费会让实际支出显示低于地板；搬迁后地板按目的地基准重新锚定。VPW/ABW 的假设实际收益率与摊销终年是库默认值，非最优化结果；对比在本国情景、库默认参数下进行。",
      "Strategy compare: VPW/ABW \"cannot deplete\" means withdrawals are a (capped) percentage of the current portfolio — the portfolio can still shrink badly and consumption falls with it; \"no ruin\" is not \"enough to live on\". The floor+upside floor is budget-basis: ACA premium savings can show spending below the floor; after relocation the floor re-anchors to the destination-basis budget. VPW/ABW assumed real returns and amortization horizons are library defaults, not optimized; the comparison runs the home scenario with library defaults."],
    ["目标求解器与效率前沿：都是粗网格扫描（1,200 路径/点，仅本国情景），边界受抽样噪声影响（成功率约 ±1pp）——「最近可行点」「前沿」都是网格上的近似，不是最优解；前沿的三元组只看消费/FIRE 年龄/成功率，不含遗产等其他维度。",
      "Goal seeker & efficient frontier: both are coarse-grid sweeps (1,200 paths/point, home scenario only); cell edges carry sampling noise (≈±1pp on success) — 'nearest feasible' and 'frontier' are grid approximations, not optima; the frontier triple covers consumption/FIRE age/success only (no estate dimension)."],
    ["非建议：本工具为个人财务建模，不构成投资、税务或法律建议。",
      "Not advice: this is personal financial modeling, not investment, tax, or legal advice."],
  ];
  // Which of the approximations are about THIS plan. The general list below
  // it stays whole: some limitations are properties of the approach rather
  // than of a setting, and conditioning those on a flag would imply you could
  // switch them off. Rendered above the general list because a reader who
  // stops after four lines should have read the four about their own plan.
  // One line, and permanently only a line. ROADMAP: note the fraction of
  // paths above the exemption and point at a professional -- "modelling it is
  // permanently out of scope". So this reports a COUNT, computed exactly over
  // the full headline path array inside the engine run, and says in the same
  // breath that no estate tax is computed anywhere.
  //
  // The exemption is the user's own number. It is legislated, it moves, this
  // app makes no network requests, and a bundled figure would age silently
  // into a wrong one -- the same reasoning already ruled for medical
  // premiums, annuity quotes, LTC premiums and the funded-ratio discount rate.
  function renderEstateLine() {
    const host = $("estateLine");
    if (!host) return;
    const ex = state.data && state.data.home && state.data.home.estate_exposure;
    if (!ex || !ex.applicable) {
      host.innerHTML = "";
      return;
    }
    const pctAbove = ex.fraction_above * 100;
    const shown = pctAbove > 0 && pctAbove < 0.1 ? "<0.1" : pctAbove.toFixed(1);
    host.innerHTML =
      `<div class="callout warn"><h5><span class="pill">${tt("遗产税", "ESTATE TAX")}</span>` +
      `<span>${tt(`${shown}% 的路径终值超过你填的豁免额`,
                  `${shown}% of paths end above the exemption you entered`)}</span></h5>` +
      `<p>${tt(`${ex.paths_above} / ${ex.paths_counted} 条路径的实际终值高于 ${money(ex.exemption_real)}。` +
               `<b>本 App 不建模遗产税，也永远不会</b>：不做豁免额指数化、不做配偶可携带、` +
               `不做州遗产税或继承税、不做信托与赠与安排。这是在数路径，不是在算税。` +
               `如果这个比例对你不算小，那是该问专业人士的问题。`,
               `${ex.paths_above} of ${ex.paths_counted} paths end above ${money(ex.exemption_real)} in real terms. ` +
               `<b>This app does not model estate tax and never will</b>: no exemption indexing, ` +
               `no portability, no state estate or inheritance tax, no trusts or gifting. ` +
               `This counts paths; it does not compute a tax. If that fraction is not small ` +
               `to you, it is a question for a professional.`)}</p></div>`;
  }

  async function renderPersonalLimitations() {
    const host = $("limPersonal");
    if (!host || !state.config) return;
    try {
      const v = await postJSON("/api/limitations",
                               { config: state.config, language: L });
      PERSONAL_LIMITATIONS = (v.triggered || []).map(row => row.text);
      if (!v.triggered.length) {
        host.innerHTML = "";
        return;
      }
      host.innerHTML =
        `<div class="panel"><div class="panel-title sm">${tt(
           `你这份配置触发了 ${v.count} 条`,
           `${v.count} of these are about your configuration`)}</div>` +
        `<ul class="lim">${v.triggered.map(row =>
           `<li>${esc(row.text)}</li>`).join("")}</ul>` +
        `<p class="cap">${tt("除此之外，下面那份通用清单同样适用 —— 它们与你怎么配置无关。",
                             "These are in addition to the general list below, which holds whatever you configure.")}</p></div>`;
    } catch (err) {
      // A disclosure panel that fails silently is the one failure this
      // particular panel must not have: an empty space reads as "nothing
      // applies to you".
      host.innerHTML = `<p class="cap">${tt(
        "没能算出针对你这份配置的局限清单（下面的通用清单仍然适用）：",
        "Could not compute the limitations specific to your configuration (the general list below still applies): ")}${esc(String(err.message || err))}</p>`;
    }
  }

  function medicalPremiumDisclosure() {
    const amount = +get(state.config, "medical.premium_aca");
    if (medicalPremiumIsUserQuote()) {
      const localized = tt(
        `ACA 全价保费锚点：<b>${money(amount)}/年，家庭合计</b>，由你按当前地点与年龄提供；本 App 未联网获取、未核验。`,
        `ACA full-premium anchor: <b>${money(amount)}/yr, household total</b>, supplied by you for your current location and age; this App did not fetch or verify it.`);
      return [localized, localized];
    }
    const localized = tt(
      `ACA 全价保费锚点：<b>${money(amount)}/年，家庭合计</b>，是未确认的旧值/代理值（缺省为 $8,000），不是用户报价。补贴仍采用既有 400% FPL 悬崖与 9.96% 平率代理。`,
      `ACA full-premium anchor: <b>${money(amount)}/yr, household total</b>, an unconfirmed legacy/proxy value (default $8,000), not a user quote. Subsidies still use the existing 400% FPL cliff and 9.96% flat proxy.`);
    return [localized, localized];
  }
  //: Topics for the 24 general limitations. The grouping lives here and
  //: nowhere else; the entries themselves stay in `LIMITATIONS`, which
  //: is what the report and the briefing pack read. Presentation only --
  //: `limitationsForExport()` returns the same lines in the same order
  //: whether or not anything is collapsed, and a test pins that.
  //:
  //: Entry 24 ("not advice") is deliberately ABSENT from every group.
  //: Collapsing a disclaimer behind a triangle makes it something a
  //: reader has to go looking for, which is the opposite of what a
  //: disclaimer is for.
  const LIMITATION_TOPICS = [
    { id: "engine", title: ["引擎怎么抽样", "How the engine samples"], items: [1, 2, 7, 15, 19] },
    { id: "tax", title: ["税与医疗", "Tax and medical"], items: [3, 4, 14] },
    { id: "life", title: ["人生事件与家庭", "Life events and family"], items: [5, 6, 9, 18] },
    { id: "money", title: ["消费、住房与其他资产", "Spending, housing, other assets"], items: [10, 11, 12, 16, 17] },
    { id: "rules", title: ["规则与工具的边界", "Where the rules and tools stop"], items: [8, 13, 21, 22, 23] },
    { id: "privacy", title: ["隐私", "Privacy"], items: [20] },
  ];
  //: One-indexed to match the list a reader sees; the last entry
  //: never collapses.
  const LIMITATION_ALWAYS_VISIBLE = [24];

  //: The lines the report and the briefing pack receive. Its own function so
  //: the grouping below can only change HOW they are laid out, never WHAT is
  //: disclosed -- and so a test can compare the two directly.
  function limitationsForExport() {
    return LIMITATIONS.concat([medicalPremiumDisclosure()]);
  }

  function renderLimitations() {
    const all = limitationsForExport();
    const grouped = LIMITATION_TOPICS.map(t => {
      const rows = t.items.map(n => all[n - 1]).filter(Boolean)
        .map(x => `<li>${x[L === "zh" ? 0 : 1]}</li>`).join("");
      return `<details class="help-topic"><summary><b>${tt(t.title[0], t.title[1])}</b>` +
             ` <span class="mut">${t.items.length}</span></summary>` +
             `<ul class="lim">${rows}</ul></details>`;
    }).join("");
    //: Anything no group claims stays visible and uncollapsed: the "not
    //: advice" line, and any disclosure added later that nobody has filed.
    //: A new one therefore defaults to SHOWN rather than hidden, which is
    //: the safe direction for this particular panel.
    const claimed = new Set();
    LIMITATION_TOPICS.forEach(t => t.items.forEach(n => claimed.add(n)));
    const loose = all.map((x, i) => claimed.has(i + 1)
                          ? null : `<li>${x[L === "zh" ? 0 : 1]}</li>`)
                     .filter(Boolean).join("");
    $("limitations").innerHTML =
      `<div class="lim-groups">${grouped}</div>` +
      (loose ? `<ul class="lim lim-always">${loose}</ul>` : "");
    renderEstateLine();
    renderPersonalLimitations();
    renderOutcomeLayers();
    renderRegimeConditional();
  }

  // ---------- inverse solver: what would it take to FIRE at age T ----------
  async function solveFire(target) {
    if (state.solving) return;
    const revision = state.revision;
    const s = D(), cur = s.fire_age.p50;
    if (cur == null) { toast(tt("先得有可达的 FIRE 才能反解", "Need a reachable FIRE first"), true); return; }
    target = Math.round(+target);
    $("fireTarget").value = target;
    state.solving = true;
    const box = $("fireSolver");
    box.innerHTML = `<div class="solver-card">${tt(`求解中——在 ±60% 范围扫描两条杠杆（各 6 点 × 2,000 路径）…`, "Solving — sweeping two levers across ±60% (6 points × 2,000 paths each)…")}</div>`;
    // Dragging the FIRE line sets a TARGET and solves; the answer lands down here, so bring it
    // into view — otherwise the drag looks like it did nothing (the line itself never moves).
    try { box.scrollIntoView({ block: "nearest" }); } catch (_) {}
    try {
      const sal = +get(state.config, "contributions.base_salary_pre") || 100000;
      const exp = +get(state.config, "state.expenses_y0") || 40000;
      const swr = +get(state.config, "state.swr_pref") || 0.0333;
      const salVals = [0.85, 1.0, 1.15, 1.3, 1.45, 1.6].map(f => Math.round(sal * f));
      const expVals = [0.60, 0.72, 0.84, 0.94, 1.05, 1.15].map(f => Math.round(exp * f));
      const [rs, re] = await Promise.all([
        postJSON("/api/sweep", { config: state.config, param: "contributions.base_salary_pre", values: salVals, paths: 2000, seed: 96000 }),
        postJSON("/api/sweep", { config: state.config, param: "state.expenses_y0", values: expVals, paths: 2000, seed: 96000 }),
      ]);
      const solve = (pts, increasing) => {
        const arr = pts.filter(p => p.fire_age_p50 != null).map(p => ({ v: p.value, a: p.fire_age_p50, ls: p.lifetime_success }));
        arr.sort((x, y) => x.v - y.v);
        for (let i = 0; i < arr.length - 1; i++) {
          const [lo, hi] = [arr[i], arr[i + 1]];
          const [a1, a2] = [lo.a, hi.a];
          if ((target - a1) * (target - a2) <= 0 && a1 !== a2) {
            const f = (target - a1) / (a2 - a1);
            return { v: lo.v + f * (hi.v - lo.v), ls: lo.ls + f * (hi.ls - lo.ls) };
          }
        }
        return null;
      };
      const salHit = solve(rs.points), expHit = solve(re.points);
      state.od.solver = { target, cur, sal, exp, swr, salHit, expHit };
      renderSolverCard();
      return;
    } catch (e) { if (revision === state.revision) { box.innerHTML = ""; toast(e.message, true); } }
    finally { if (revision === state.revision) state.solving = false; }
  }
  function renderSolverCard() {
    const box = $("fireSolver");
    const d = state.od.solver;
    if (!d) { box.innerHTML = ""; return; }
    const { target, cur, sal, exp, swr, salHit, expHit } = d;
      const rows = [];
      rows.push(`<div class="sc-t">${tt(`目标：P50 FIRE = ${target} 岁（当前 ${Math.round(cur)}）`, `Target: P50 FIRE = ${target} (now ${Math.round(cur)})`)}</div>`);
      if (salHit) rows.push(tt(
        `① 收入侧：基础薪资约 <b>${money(salHit.v)}</b>（当前 ${money(sal)}，Δ<b>${(salHit.v >= sal ? "+" : "") + money(salHit.v - sal)}</b>/年；引擎按税后可储蓄部分入账）。该方案三分支成功率 ≈ <b>${pct(salHit.ls, 1)}</b>。`,
        `① Income side: base salary ≈ <b>${money(salHit.v)}</b> (now ${money(sal)}, Δ<b>${(salHit.v >= sal ? "+" : "") + money(salHit.v - sal)}</b>/yr; the engine saves the after-tax remainder). Three-branch success ≈ <b>${pct(salHit.ls, 1)}</b>.`));
      else rows.push(tt(`① 收入侧：在薪资 ±60% 的扫描范围内达不到 ${target} 岁。`, `① Income side: unreachable within ±60% salary.`));
      if (expHit) rows.push(tt(
        `② 支出侧：退休年支出约 <b>${money(expHit.v)}</b>（当前 ${money(exp)}；FI 门槛随之变为 <b>${money(expHit.v / swr)}</b>）。该方案三分支成功率 ≈ <b>${pct(expHit.ls, 1)}</b>。`,
        `② Spending side: retirement spend ≈ <b>${money(expHit.v)}</b> (now ${money(exp)}; the FI number becomes <b>${money(expHit.v / swr)}</b>). Three-branch success ≈ <b>${pct(expHit.ls, 1)}</b>.`));
      else rows.push(tt(`② 支出侧：在扫描范围内达不到 ${target} 岁。`, `② Spending side: unreachable within the swept range.`));
      rows.push(`<span style="font-size:11.5px;color:var(--ink-muted)">${tt("单杠杆、其他不变、2,000 路径粗算——把它当方向而非精确承诺；改完参数用完整精度重跑验证。", "One lever at a time, everything else fixed, 2,000-path estimates — treat as direction, not promise; re-run at full precision after changing inputs.")}</span>`);
      box.innerHTML = `<div class="solver-card">${rows.join("<br>")}</div>`;
  }

  // =========================================================== forward JS Monte Carlo
  let spare = null;
  function gauss() { if (spare != null) { const s = spare; spare = null; return s; } const u = Math.random() || 1e-9, v = Math.random(); const r = Math.sqrt(-2 * Math.log(u)); spare = r * Math.sin(2 * Math.PI * v); return r * Math.cos(2 * Math.PI * v); }
  function drawR(m, s) { const sl = Math.sqrt(Math.log(1 + s * s / ((1 + m) * (1 + m)))); const ml = Math.log(1 + m) - 0.5 * sl * sl; return Math.exp(ml + sl * gauss()) - 1; }
  const REG = [[-0.02, 0.40], [0.02, 0.20], [0.0, 0.40]];
  function pickReg() { let r = Math.random(), c = 0; for (const [o, p] of REG) { c += p; if (r < c) return o; } return 0; }
  function runFwd() {
    const mu = +$("kMu").value / 100, sd = +$("kSd").value / 100, sav = +$("kSav").value, swr = +$("kSwr").value / 100;
    const N = 2000, startAge = +get(state.config, "state.start_age") || 30, maxY = +get(state.config, "state.accum_years") || 25;
    const w0 = ["initial.pretax_401k", "initial.roth_ira", "initial.hsa", "initial.taxable"].reduce((a, p) => a + (+get(state.config, p) || 0), 0);
    const fireTarget = (+get(state.config, "state.expenses_y0") || 40000) / swr, t0 = performance.now();
    const fireAges = [], byYear = Array.from({ length: maxY + 1 }, () => []);
    for (let i = 0; i < N; i++) {
      const off = pickReg(); let w = w0, fired = null; byYear[0].push(w);
      for (let y = 1; y <= maxY; y++) { w = w * (1 + drawR(mu + off, sd)) + sav; byYear[y].push(w); if (fired == null && w >= fireTarget) fired = startAge + y; }
      if (fired != null) fireAges.push(fired);
    }
    const q = (arr, p) => { if (!arr.length) return null; const a = arr.slice().sort((x, y) => x - y); return a[Math.max(0, Math.min(a.length - 1, Math.floor(p / 100 * a.length)))]; };
    const fp50 = q(fireAges, 50), fp10 = q(fireAges, 10), fp90 = q(fireAges, 90);
    $("fwdReadout").innerHTML = [["FIRE P50", fp50 != null ? fp50 : "—", "accent"], ["FIRE P10–P90", fp10 != null ? fp10 + "–" + fp90 : "—", ""], [tt("达成率", "reach rate"), pct(fireAges.length / N, 0), ""], ["P(FIRE ≤ 40)", pct(fireAges.filter(x => x <= 40).length / N, 0), ""]].map(([la, v, c]) => `<div class="readout"><div class="lab">${la}</div><div class="num ${c}">${v}</div></div>`).join("");
    if (fireAges.length) { const amin = Math.min.apply(null, fireAges), amax = Math.max.apply(null, fireAges); const ages = [], counts = []; for (let a = amin; a <= amax; a++) { ages.push(a); counts.push(fireAges.filter(x => x === a).length); } C.ageBars($("fwdHist"), { ages, counts, p10: fp10, p50: fp50, p90: fp90 }, { color: CV("--ch-gold", "#8A6420"), animate: true }); } else C.ageBars($("fwdHist"), null, {});
    const rows = byYear.map((vals, y) => ({ age: startAge + y, p10: q(vals, 10), p25: q(vals, 25), p50: q(vals, 50), p75: q(vals, 75), p90: q(vals, 90) })).filter(r => r.p90 > 0);
    C.fan($("fwdFan"), rows, { log: true, pal: "gold", fireAge: fp50, animate: true }); // runFwd only fires on an explicit run — every render IS a fresh result
    $("kStat").textContent = `${N} ${tt("路径", "paths")} · ${(performance.now() - t0).toFixed(0)}ms · FIRE ${tt("线", "line")} ${money(fireTarget)}`;
    riseIn($("fwdReadout"), $("fwdHist"), $("fwdFan"));       // explicit run = fresh answer block
  }
  function resetFwd() {
    const swr = (+get(state.config, "state.swr_pref") || 0.0333) * 100;
    const _shift = (+get(state.config, "returns.equity_mu_shift") || 0) * 100;
    $("kMu").value = (6.4 + _shift).toFixed(1); $("kSd").value = 17.6; $("kSav").value = Math.min(140000, Math.max(20000, estSavings())); $("kSwr").value = swr.toFixed(2);
    syncK(); runFwd();
  }
  function syncK() { $("kMuVal").textContent = (+$("kMu").value).toFixed(1) + "%"; $("kSdVal").textContent = (+$("kSd").value).toFixed(1) + "%"; $("kSavVal").textContent = money(+$("kSav").value); $("kSwrVal").textContent = (+$("kSwr").value).toFixed(2) + "%"; }

  // =========================================================== on-demand analyses
  // Server returns stable keys; display names are localized here (audit P1-2).
  const TORN_LABELS = {
    mu: ["收益率 μ (±1.5pp)", "Return μ (±1.5pp)"],
    expenses: ["退休支出 (±10%)", "Retirement spend (±10%)"],
    swr: ["SWR (±0.5pp)", "SWR (±0.5pp)"],
    salary: ["基础薪资 (±10%)", "Base salary (±10%)"],
    portfolio: ["起始组合 (±10%)", "Starting portfolio (±10%)"],
    inflation: ["通胀 (±0.5pp)", "Inflation (±0.5pp)"],
    salary_growth: ["薪资增长 (±1pp)", "Salary growth (±1pp)"],
  };
  const BT_LABELS = {
    crash: ["深度崩盘开局（大萧条式）", "Deep-crash opening (1929-style)"],
    lost_decade: ["失去的十年（2000 式）", "Lost decade (2000-style)"],
    stagflation: ["滞胀开局（1970 式）", "Stagflation opening (1970s-style)"],
  };
  const odLabel = (map, key, fb) => (map[key] ? map[key][L === "zh" ? 0 : 1] : (fb || key));

  async function runSens() {
    const revision = state.revision;
    const btn = $("sensRun"); btn.disabled = true; $("tornChart").classList.add("loading"); $("sensHint").textContent = tt("计算中…", "computing…");
    odProgress(btn, true);
    try {
      const r = await postJSON("/api/sensitivity", { config: state.config, seed: 96000 });
      state.od.sens = r;
      renderSensPanel();
      if (state.page === "concl") renderConclusions();
    } catch (e) { if (revision === state.revision) toast(e.message, true); } finally { if (revision === state.revision) { btn.disabled = false; odProgress(btn, false); $("tornChart").classList.remove("loading"); $("ruChart").classList.remove("loading"); $("sensHint").textContent = state.od.sens ? tt("已算", "done") : ""; } }
  }
  function renderSensPanel() {
    $("tornChart").classList.remove("loading"); $("ruChart").classList.remove("loading");
    const r = state.od.sens; if (!r) return;
    const fresh = animOnce("sens", r);          // one gate for the whole panel's reveal
    const rows = r.rows.map(x => ({ lo: x.lo, hi: x.hi, label: odLabel(TORN_LABELS, x.key, x.label) }));
    C.tornado($("tornChart"), rows, r.center, { animate: fresh });
    $("tornCap").innerHTML = tt(`每行：把该假设 ±扰动后终值 real P50 的两端（共同随机数，N=${r.n_paths.toLocaleString()}）。收益率 μ 通常最大。`, `Each row: two ends of terminal real P50 under ± perturbation (common random numbers, N=${r.n_paths.toLocaleString()}). Return μ usually dominates.`);
    C.lines($("ruChart"), { series: [{ name: tt("终值", "terminal"), color: CV("--ch-home", "#2A4A3A"), axis: "left", dots: true, points: r.mu_band.map(b => [b.mu * 100, b.terminal_real_p50]) }, { name: tt("三分支成功率", "three-branch success"), color: CV("--ch-gold", "#8A6420"), axis: "right", dots: true, points: r.mu_band.map(b => [b.mu * 100, b.lifetime_success]) }], yLeft: { fmt: money }, yRight: { fmt: v => pct(v, 0), min: 0, max: 1 }, xfmt: x => x.toFixed(1) + "%", xLabel: "μ", animate: fresh });
    $("ruCap").innerHTML = tt(`森林绿=终值(左轴)，金=三分支成功率(右轴)。μ 从 ${(r.mu_band[0].mu * 100).toFixed(1)}% 到 ${(r.mu_band[r.mu_band.length - 1].mu * 100).toFixed(1)}%，终值从 ${money(r.mu_band[0].terminal_real_p50)} 变到 ${money(r.mu_band[r.mu_band.length - 1].terminal_real_p50)}。`, `Forest=terminal (left), gold=three-branch success (right). μ from ${(r.mu_band[0].mu * 100).toFixed(1)}% to ${(r.mu_band[r.mu_band.length - 1].mu * 100).toFixed(1)}%: terminal ${money(r.mu_band[0].terminal_real_p50)} → ${money(r.mu_band[r.mu_band.length - 1].terminal_real_p50)}.`);
    if (fresh) riseIn($("tornChart"), $("tornCap"), $("ruChart"), $("ruCap"));
  }

  async function runSwr() {
    const revision = state.revision;
    const btn = $("swrRun"); btn.disabled = true; $("swrChart").classList.add("loading"); btn.textContent = tt("扫描中…", "sweeping…");
    odProgress(btn, true);
    try {
      const vals = [0.025, 0.03, 0.0333, 0.035, 0.04, 0.045, 0.05, 0.055];
      const r = await postJSON("/api/sweep", { config: state.config, param: "state.swr_pref", values: vals, paths: 4000, seed: 96000 });
      state.od.swr = r;
      renderSwrPanel();
      if (state.page === "concl") renderConclusions();
    } catch (e) { if (revision === state.revision) toast(e.message, true); } finally { if (revision === state.revision) { btn.disabled = false; odProgress(btn, false); $("swrChart").classList.remove("loading"); btn.textContent = t("stress.swr.run"); } }
  }
  function renderSwrPanel() {
    $("swrChart").classList.remove("loading");
    const r = state.od.swr; if (!r) return;
    const fresh = animOnce("swr", r);
    C.lines($("swrChart"), { series: [{ name: tt("三分支成功率", "three-branch success"), color: CV("--ch-home", "#2A4A3A"), axis: "left", dots: true, points: r.points.map(p => [p.value * 100, p.lifetime_success]) }, { name: tt("P50 消费", "P50 spend"), color: CV("--ch-gold", "#8A6420"), axis: "right", dots: true, points: r.points.map(p => [p.value * 100, p.cons_p50]) }], yLeft: { fmt: v => pct(v, 0), min: 0, max: 1 }, yRight: { fmt: money }, xfmt: x => x.toFixed(1) + "%", xLabel: "SWR", markers: [{ x: (+get(state.config, "state.swr_pref") || 0.0333) * 100, color: CV("--ch-reloc", "#722F37"), label: tt("当前", "current") }], animate: fresh });
    $("swrLegend").innerHTML = `<span class="chip"><span class="swl" style="border-color:${CV("--ch-home", "#7E9070")}"></span>${tt("三分支成功率(左)", "three-branch success (L)")}</span><span class="chip"><span class="swl" style="border-color:${CV("--ch-gold", "#B29868")}"></span>${tt("P50 消费(右)", "P50 spend (R)")}</span>`;
    $("swrCap").innerHTML = tt(`N=${r.n_paths.toLocaleString()}/点。抬高 SWR 提升消费但侵蚀偿付——红线是你当前的 SWR。`, `N=${r.n_paths.toLocaleString()}/point. Higher SWR lifts spending but erodes solvency — the red line is your current SWR.`);
    if (fresh) riseIn($("swrChart"), $("swrLegend"), $("swrCap"));
  }

  async function runClaim() {
    if (!get(state.config, "social_security.enabled")) { $("claimCap").textContent = tt("社保未启用。", "Social Security disabled."); return; }
    const revision = state.revision;
    const btn = $("claimRun"); btn.disabled = true; $("claimChart").classList.add("loading"); btn.textContent = tt("扫描中…", "sweeping…");
    odProgress(btn, true);
    try {
      const vals = [62, 63, 64, 65, 66, 67, 68, 69, 70];
      const r = await postJSON("/api/sweep", { config: state.config, param: "social_security.claim_age", values: vals, paths: 3000, seed: 96000 });
      state.od.claim = r;
      renderClaimPanel();
    } catch (e) { if (revision === state.revision) toast(e.message, true); } finally { if (revision === state.revision) { btn.disabled = false; odProgress(btn, false); $("claimChart").classList.remove("loading"); btn.textContent = t("stress.claim.run"); } }
  }
  function renderClaimPanel() {
    $("claimChart").classList.remove("loading");
    const r = state.od.claim; if (!r) return;
    const fresh = animOnce("claim", r);
    const success = r.points.map(p => p.lifetime_success).filter(Number.isFinite);
    let sy0 = success.length ? Math.min(...success) : 0, sy1 = success.length ? Math.max(...success) : 1;
    const pad = Math.max((sy1 - sy0) * 0.15, 0.01);
    sy0 = Math.max(0, sy0 - pad); sy1 = Math.min(1, sy1 + pad);
    if (sy1 <= sy0) { sy0 = Math.max(0, sy0 - 0.01); sy1 = Math.min(1, sy1 + 0.01); }
    C.lines($("claimChart"), { series: [{ name: tt("终生社保", "lifetime SS"), color: CV("--ch-gold", "#8A6420"), axis: "left", dots: true, points: r.points.map(p => [p.value, p.ss_p50]) }, { name: tt("三分支成功率", "three-branch success"), color: CV("--ch-home", "#2A4A3A"), axis: "right", dots: true, points: r.points.map(p => [p.value, p.lifetime_success]) }], yLeft: { fmt: money }, yRight: { fmt: v => pct(v, 0), min: sy0, max: sy1 }, xfmt: x => x, xLabel: tt("领取年龄", "claim age"), animate: fresh });
    $("claimTable").innerHTML = `<thead><tr><th>${tt("领取年龄", "claim age")}</th><th>${tt("终生社保 real", "lifetime SS real")}</th><th>${tt("三分支成功率", "three-branch success")}</th></tr></thead><tbody>` + r.points.map(p => `<tr><td>${p.value}</td><td class="real">${money(p.ss_p50)}</td><td>${pct(p.lifetime_success)}</td></tr>`).join("") + `</tbody>`;
    $("claimCap").innerHTML = tt("口径：已按 SSA 精算调整（约 62 岁=PIA 的 70%、67 岁=100%、70 岁=124%）。晚领月金更高但领取年数更少——两者权衡。请以自身 PIA 复核。", "Note: actuarially adjusted per SSA (≈70% of PIA at 62, 100% at 67, 124% at 70). Later claim = higher monthly but fewer years — a trade-off. Verify with your own PIA.");
    if (fresh) riseIn($("claimChart"), $("claimTable"), $("claimCap"));
  }

  async function runRoth() {
    const revision = state.revision;
    const btn = $("rothRun"); btn.disabled = true; $("rothChart").classList.add("loading");
    odProgress(btn, true);
    $("rothHint").textContent = tt("8 个档位 × 1,500 路径…", "8 levels × 1,500 paths…");
    try {
      const r = await postJSON("/api/roth_opt", { config: state.config, paths: 1500, seed: state.seed || 96000 });
      state.od.roth = r; renderRothPanel();
      if (state.page === "concl") renderConclusions();
    } catch (e) { if (revision === state.revision) toast(e.message, true); }
    finally { if (revision === state.revision) { btn.disabled = false; odProgress(btn, false); $("rothChart").classList.remove("loading"); $("rothHint").textContent = state.od.roth ? tt("已算", "done") : ""; } }
  }
  function renderRothPanel() {
    $("rothChart").classList.remove("loading");
    const r = state.od.roth;
    if (!r) return;
    const fresh = animOnce("roth", r);
    const pts = r.points;
    C.lines($("rothChart"), { series: [
      { name: tt("税后终值 real P50", "after-tax terminal real P50"), color: CV("--ch-home", "#2A4A3A"), axis: "left", dots: true, points: pts.map(p2 => [p2.conversion / 1000, p2.terminal_after_tax_real_p50]) },
      { name: tt("三分支成功率", "three-branch success"), color: CV("--ch-gold", "#8A6420"), axis: "right", dots: true, points: pts.map(p2 => [p2.conversion / 1000, p2.lifetime_success]) },
    ], yLeft: { fmt: money }, yRight: { fmt: v => pct(v, 0), min: 0, max: 1 },
      xfmt: x => "$" + x + "K", xLabel: tt("请求的基准年转换额", "requested base annual conversion"),
      markers: [{ x: r.best.conversion / 1000, color: CV("--ch-reloc", "#722F37"), label: tt("已选档位", "selected grid point") }], animate: fresh });
    $("rothLegend").innerHTML = `<span class="chip"><span class="swl" style="border-color:${CV("--ch-home", "#2A4A3A")}"></span>${tt("税后终值(左)", "after-tax terminal (L)")}</span><span class="chip"><span class="swl" style="border-color:${CV("--ch-gold", "#8A6420")}"></span>${tt("三分支成功率(右)", "three-branch success (R)")}</span>`;
    $("rothReadout").innerHTML = [
      [tt("已选年转换", "Selected annual conversion"), money(r.best.conversion), "accent"],
      [tt("已选档位税后终值 P50", "Selected point after-tax terminal P50"), money(r.best.terminal_after_tax_real_p50), "home"],
      [tt("该档三分支成功率", "Its three-branch success"), pct(r.best.lifetime_success, 1), ""],
      [tt("终身税负 P50 (real)", "Lifetime tax P50 (real)"), money(r.best.true_tax_p50), ""],
    ].map(([la, v, c2]) => `<div class="readout"><div class="lab">${la}</div><div class="num ${c2}">${v}</div></div>`).join("");
    $("rothCap").innerHTML = tt(`真税模式强制开启（${r.n_paths.toLocaleString()} 路径/档，seed ${r.seed}）。固定比较 8 个年转换基准档（$0–$100k），每档按全局增长率逐年增长；只在已测试档位中先比较三分支成功率，再在成功率相同的档位中比较全路径税后终值 P50（失败路径记 0）。终值按逐年追踪的成本基础计算，未实现利得按遗产基础提升处理，不再是平率清算代理。剩下的方向性只有一条：只测了这 8 个点。逐年不同的计划见「哪几年转，转多少」。`, `True-tax mode forced (${r.n_paths.toLocaleString()} paths/level, seed ${r.seed}). Exactly 8 tested base annual levels ($0–$100k) are compared, each growing by the global rate. Among tested levels, maximize three-branch success first; only equal-success levels use unconditional after-tax terminal P50 (failed paths = 0). Terminal value comes from the cost basis tracked year by year, with unrealised gains under the bequest step-up -- no longer a flat liquidation proxy. Coverage is the only directional limit left: just these 8 points. For a year-by-year schedule, see the \u0022which years to convert\u0022 panel.`);
    if (fresh) riseIn($("rothChart"), $("rothLegend"), $("rothReadout"), $("rothCap"));
  }
  async function runStrategies() {
    const revision = state.revision;
    const btn = $("stratRun"); btn.disabled = true;
    $("stratHint").textContent = tt("5 种策略 × 2,000 路径…", "5 rules × 2,000 paths…");
    odProgress(btn, true);
    try {
      const r = await postJSON("/api/strategies", { config: state.config, paths: 2000, seed: state.seed || 96000 });
      state.od.strat = r; renderStrategiesPanel();
    } catch (e) { if (revision === state.revision) toast(e.message, true); }
    finally { if (revision === state.revision) { btn.disabled = false; odProgress(btn, false); $("stratHint").textContent = state.od.strat ? tt("已算", "done") : ""; } }
  }
  function renderStrategiesPanel() {
    const r = state.od.strat; if (!r) return;
    const li = L === "zh" ? 0 : 1;
    const name = p => (r.labels[p.type] || [p.type, p.type])[li];
    const head = [tt("策略", "strategy"), tt("三分支成功率", "three-branch success"), tt("FIRE后偿付", "post-FIRE solvency"),
                  tt("均值消费 P50", "mean cons P50"), tt("最低消费 P50", "min cons P50"), tt("遗产 P50 real", "estate P50 real")];
    const bestTerm = Math.max(...r.points.map(p => p.terminal_real_p50 || 0));
    const bestMin = Math.max(...r.points.map(p => p.min_cons_p50 || 0));
    $("stratTable").innerHTML = `<thead><tr>${head.map(h => `<th>${h}</th>`).join("")}</tr></thead><tbody>` +
      r.points.map(p => `<tr><td>${name(p)}${p.type === "gk" ? ` <span class="tag">${tt("当前", "current")}</span>` : ""}</td>` +
        `<td>${pct(p.lifetime_success, 1)}</td><td>${pct(p.post_fire_solvency, 1)}</td>` +
        `<td class="real">${money(p.cons_p50)}</td>` +
        `<td class="real${p.min_cons_p50 === bestMin ? " hl" : ""}">${money(p.min_cons_p50)}</td>` +
        `<td class="real${p.terminal_real_p50 === bestTerm ? " hl" : ""}">${money(p.terminal_real_p50)}</td></tr>`).join("") + `</tbody>`;
    $("stratCap").innerHTML = tt(
      `${r.n_paths.toLocaleString()} 路径/策略，seed ${r.seed}，本国情景。没有「最优」策略——VPW/ABW 数学上不破产但消费随市场深跌；地板+上行守住底线消费；GK/固定实际额稳消费但承担序列风险。各策略参数用库默认值（可在配置 JSON 的 rule 里改）。`,
      `${r.n_paths.toLocaleString()} paths/rule, seed ${r.seed}, home scenario. No "best" rule — VPW/ABW cannot deplete but consumption tracks crashes; floor+upside defends a spending floor; GK/fixed-real keep spending steady at the cost of sequence risk. Library defaults per rule (editable under rule in the config JSON).`);
    renderStrategySpread(r);
    if (animOnce("strat", r)) riseIn($("stratTable"), $("stratCap"));
  }

  // The spending fan, as one number per rule plus the band itself.
  //
  // ROADMAP asks for this because a success rate cannot show it: on the
  // default plan gk, vpw and abw ALL report 100% success while their spending
  // varies about 7.8x between the 10th and 90th percentile, and fixed_real
  // reports 91% with spending that barely moves at 1.06x. The 100% is bought
  // by cutting spending in bad decades, and the table above cannot say so.
  function renderStrategySpread(r) {
    const host = $("stratSpread");
    if (!host) return;
    const li = L === "zh" ? 0 : 1;
    const rows = (r.points || []).map(p => {
      const fan = p.spending_fan || [];
      const ratios = fan.filter(x => x.p10 > 0).map(x => x.p90 / x.p10).sort((a, b) => a - b);
      const mid = ratios.length ? ratios[Math.floor(ratios.length / 2)] : null;
      const name = (r.labels[p.type] || [p.type, p.type])[li];
      return { name, mid, years: fan.length };
    });
    if (!rows.some(x => x.mid != null)) { host.innerHTML = ""; return; }
    host.innerHTML =
      `<div class="panel"><div class="panel-title sm">${tt(
        "同样的成功率，支出的波动可以完全不同",
        "Same success rate, very different spending volatility")}</div>` +
      `<table class="mini"><thead><tr><th>${tt("策略", "Rule")}</th>` +
      `<th>${tt("支出 P90 / P10", "Spending P90 / P10")}</th>` +
      `<th>${tt("覆盖年数", "Years")}</th></tr></thead><tbody>` +
      rows.map(x => `<tr><td>${esc(x.name)}</td>` +
        `<td>${x.mid == null ? tt("——", "—") : x.mid.toFixed(2) + "×"}</td>` +
        `<td>${x.years}</td></tr>`).join("") + `</tbody></table>` +
      `<p class="cap">${tt(
        `一个 <b>7×</b> 的比值表示：同一条规则下，顺境年份的支出可能是逆境年份的七倍。<b>成功率看不出这件事</b> —— 「不破产」可以是靠大幅削减支出换来的。` +
        `扇形取自 ${r.spending_fan_paths} 条路径的独立较小样本（上表的成功率用的是 ${r.n_paths.toLocaleString()} 条）：` +
        `分位带需要的路径数远少于尾部概率。`,
        `A ratio of <b>7×</b> means spending in good years can be seven times what it is in bad ones under the same rule. ` +
        `<b>A success rate cannot show this</b> — "never depleting" can be bought by cutting spending hard. ` +
        `The fans come from a separate, smaller sample of ${r.spending_fan_paths} paths (the success rates above use ${r.n_paths.toLocaleString()}): ` +
        `a percentile band needs far fewer paths than a tail probability.`)}</p></div>`;
  }
  async function runBt() {
    const revision = state.revision;
    const btn = $("btRun"); btn.disabled = true; $("btConsChart").classList.add("loading"); btn.textContent = tt("运行中…", "running…");
    odProgress(btn, true);
    try {
      const ra = state.data && state.data.home.fire_age.p50 ? Math.round(state.data.home.fire_age.p50) : null;
      const r = await postJSON("/api/backtest", { config: state.config, retire_age: ra, seed: 96000 });
      state.od.bt = r;
      renderBtPanel();
      if (state.page === "concl") renderConclusions();
    } catch (e) { if (revision === state.revision) toast(e.message, true); } finally { if (revision === state.revision) { btn.disabled = false; odProgress(btn, false); $("btConsChart").classList.remove("loading"); btn.textContent = t("stress.bt.run"); } }
  }
  function renderBtPanel() {
    $("btConsChart").classList.remove("loading");
    const r = state.od.bt; if (!r) return;
    const fresh = animOnce("bt", r);
    const cols = { crash: CV("--ch-reloc", "#722F37"), lost_decade: CV("--ch-gold", "#8A6420"), stagflation: CV("--forest-light", "#3D6852") };
    C.lines($("btConsChart"), { series: Object.entries(r.scenarios).map(([k, v]) => ({ name: odLabel(BT_LABELS, k, v.label), color: cols[k] || "#555", points: v.real_cons.map((c, i) => [v.start_age + 1 + i, c]) })), yLeft: { fmt: money, min: 0 }, xfmt: x => x, xLabel: "age", animate: fresh });
    $("btLegend").innerHTML = Object.entries(r.scenarios).map(([k, v]) => `<span class="chip"><span class="swl" style="border-color:${cols[k]}"></span>${odLabel(BT_LABELS, k, v.label)}</span>`).join("");
    $("btReadout").innerHTML = Object.entries(r.scenarios).map(([k, v]) => `<div class="readout"><div class="lab">${odLabel(BT_LABELS, k, v.label)}</div><div class="num" style="font-size:15px;color:${v.survived ? CV("--ch-home", "#2A4A3A") : CV("--bad", "#9A2A2A")}">${v.survived ? tt("存活", "survived") : tt("破产 @", "ruin @") + v.shortfall_age}</div><div class="sub">${tt("终值 real", "terminal real")} ${money(v.terminal_real)}</div></div>`).join("");
    // What this panel did NOT charge for. A plan with a module switched on that
    // silently vanishes from a survival test reads as "it survived", which is
    // the one reading the result cannot support.
    const omitted = (r.not_modelled || []).map(x => tt(
      `<b>本页未计入长期护理</b>（你已开启 ${x.mode} 模式）：这里是固定坏序列、且关闭死亡率，护理不在任何情景中扣费。下面的存活结论属于<b>一份没有护理成本的计划</b>；护理的代价出现在主蒙特卡洛运行里。`,
      `<b>Long-term care is not charged on this page</b> (you have it on, ${x.mode} mode): these are fixed stress sequences with mortality off, so no scenario below pays for care. The survival results are for <b>a plan without the care cost</b> — that appears in the headline Monte Carlo run.`)).join("");
    $("btCap").innerHTML = (omitted ? `<div class="callout warn" style="margin-bottom:14px"><h5><span class="pill">${tt("未计入", "NOT CHARGED")}</span>${tt("这份压力测试少算了一样东西", "This stress test leaves something out")}</h5>${omitted}</div>` : "") + tt(`在 FI 数 ${money(r.target)}（退休年龄 ${r.retire_age}）起套上风格化坏序列。GK 在坏年份下调消费保住组合。<b>示意序列，非真实指数。</b>`, `From the FI number ${money(r.target)} (retire age ${r.retire_age}) under stylized bad sequences. GK cuts consumption in bad years to preserve the portfolio. <b>Stylized, not literal index data.</b>`);
    if (fresh) riseIn($("btConsChart"), $("btLegend"), $("btReadout"), $("btCap"));
  }

  // =========================================================== exports
  const inNativeWindow = () => !!window.pywebview;   // WKWebView can't window.open blob URLs
  function reportExtra() {
    const ab = (state.slots.A && state.slots.A.data && state.slots.B && state.slots.B.data)
      ? { a: { label: state.slots.A.config.name || "A", s: state.slots.A.data.home },
          b: { label: state.slots.B.config.name || "B", s: state.slots.B.data.home } }
      : null;
    return {
      lang: L,
      verdict_html: ($("verdict") && $("verdict").innerHTML) || "",
      conclusions: buildConclusionCards(),
      // The personalized ones FIRST, then the general list. A reader of the
      // formal document should meet the caveats about their own plan before
      // the ones that hold for everybody, and the report previously carried
      // only the general list -- so the sentences most likely to matter were
      // the ones it did not have.
      // Same function the panel renders from, so a regrouping can never
      // change what an export discloses.
      limitations: (PERSONAL_LIMITATIONS || []).concat(
        limitationsForExport().map(x => x[L === "zh" ? 0 : 1])),
      ab,
    };
  }
  // The pack the user carries elsewhere. The app still makes zero network
  // requests -- it writes two files and stops.
  //
  // The confirm is not ceremony. This export is NOT de-identified: the ruling
  // of 2026-08-14 withdrew that promise because the pipeline it named does
  // not exist, and the honest replacement is to make the user say yes while
  // looking at what the file contains.
  async function exportBriefing() {
    if (!state.data) { toast(tt("先跑一次", "Run the model first")); return; }
    const ok = confirm(tt(
      "这份导出未经脱敏，包含你的真实数字（余额、年龄、薪资、所在地）。\n\n" +
      "离开本 App 的数据不受本 App 的隐私承诺保护 —— 你粘贴给谁，对方就拿到了这些数字。\n\n" +
      "确定要导出吗？",
      "This export is NOT de-identified. It contains your real figures — balances, ages, salary, location.\n\n" +
      "Data that leaves this app is not covered by this app's privacy properties: whoever you paste it into has these numbers.\n\n" +
      "Export anyway?"));
    if (!ok) return;
    try {
      const pack = await postJSON("/api/briefing_pack", {
        config: state.config, language: L,
        packet: (DEC && DEC.packet) || null,
        sampling_error: ((state.data.meta || {}).sampling_error || {}).home || null,
      });
      const r = await postJSON("/api/save_file", {
        kind: "briefing", pack, results: {},
        name: get(state.config, "name") || "fire",
      });
      toast(tt("已保存到 ", "Saved to ") + r.path.replace(/^.*\/(Downloads\/)/, "~/$1"));
    } catch (err) {
      toast(tt("导出失败：", "Export failed: ") + (err.message || err));
    }
  }

  async function saveViaServer(kind) {
    const clean = Object.assign({}, state.data); delete clean.dist;
    const r = await postJSON("/api/save_file", {
      kind, results: kind === "json" ? state.data : clean,
      extra: kind === "report" ? reportExtra() : undefined,
      // Sent so the server can derive the guardrail dollarisation beside the
      // policies it reads, rather than this page holding a second copy of
      // that arithmetic.
      config: state.config,
      name: get(state.config, "name") || "fire",
    });
    toast(tt("已保存到 ", "Saved to ") + r.path.replace(/^.*\/(Downloads\/)/, "~/$1"));
  }
  async function openReport() {
    try {
      if (inNativeWindow()) return await saveViaServer("report");
      const clean = Object.assign({}, state.data); delete clean.dist;
      const r = await postJSON("/api/report", { results: clean, extra: reportExtra() });
      window.open(URL.createObjectURL(new Blob([r.html], { type: "text/html" })), "_blank");
    } catch (e) { toast(e.message, true); }
  }
  async function downloadJson() {
    try {
      if (inNativeWindow()) return await saveViaServer("json");
      const a = document.createElement("a"); a.href = URL.createObjectURL(new Blob([JSON.stringify(state.data, null, 1)], { type: "application/json" }));
      a.download = (get(state.config, "name") || "fire").replace(/\s+/g, "_") + "_results.json"; a.click();
    } catch (e) { toast(e.message, true); }
  }

  // =========================================================== router + draft
  function goto(view) {
    state.view = view;
    document.querySelectorAll(".view").forEach(v => v.classList.remove("show"));
    $("v-" + view).classList.add("show");
    $("stepsMini").style.display = view === "wizard" ? "" : "none";
    $("restartBtn").style.display = (view === "welcome") ? "none" : "";
    if (view === "welcome") { renderPlans(); renderRecoveredDrafts(); renderStanding(); }
    if (view === "wizard") { buildRail(); buildStep(); updateStepsMini(); }
    if (view === "help") buildHelp();
    if (view === "precision") buildPrecision();
    if (view === "results") resultTabs();
  }
  function updateStepsMini() { $("stepsMini").innerHTML = STEPS.map((s, i) => `<span class="sm-dot${i === state.step ? " on" : ""}"></span>`).join(""); }
  const DRAFT_V = 2;
  function normalizeConfig(cfg) {
    // one door for every config that enters the app: merge onto current
    // defaults (additive schema migration) and stamp the version
    const base = JSON.parse(JSON.stringify(state.presets[Object.keys(state.presets)[0]].config));
    const out = deepMerge(base, cfg || {});
    const streams = out.income_streams || (out.income_streams = {});
    ["pension", "rental", "parttime", "equity"].forEach(kind => {
      const field = kind + "_owner";
      if (streams[field] == null || streams[field] === "") streams[field] = "unspecified";
    });
    out.config_version = base.config_version || 2;
    return out;
  }
  function deepMerge(base, over) {
    if (over === null || typeof over !== "object" || Array.isArray(over)) return over;
    const out = (base && typeof base === "object" && !Array.isArray(base)) ? base : {};
    for (const k in over) {
      if (k === "__proto__" || k === "constructor" || k === "prototype") continue;
      out[k] = deepMerge(out[k], over[k]);
    }
    return out;
  }
  //: The working draft under SQLite authority.
  //
  //: Before the cutover this lived in localStorage's `fire_draft` and survived a
  //: restart. After it, `fire_draft` may not be written at all — that is the B1
  //: fence — so for one round the working draft was session state and a restart
  //: lost it. The ruling (2026-07-27) called that a blocker, and it now lives in
  //: a side-store beside the archive: `GET`/`POST /api/storage/working-draft`.
  //
  //: That pair is deliberately *not* a §6 seam — no receipt, no generation, no
  //: archive byte — which is what lets it keep working while the archive is
  //: latched or `source_changed`. Refusing there would throw away what the user
  //: is typing at the one moment they most need time to work the problem.
  //
  //: `_workingDraft` is an in-memory mirror so `loadDraft()` can stay
  //: synchronous for its many call sites. The server copy is what survives.
  let _workingDraft = null;
  let _draftFlush = null;

  //: Which store owns the working draft right now.
  //
  //: Deliberately NOT `storageIsAuthoritative()`. That answers "is the archive
  //: the plan authority", and gating the draft on it was the defect: under
  //: `source_changed` or a latch it fell through to the legacy path, which is
  //: fenced, so a save wrote nothing and the hint said 未保存 — the capability
  //: the side-store exists to preserve, lost in exactly the states where the
  //: user most needs it.
  //
  //: §6's working-draft boundary is what makes the wider set correct: the
  //: side-store carries no receipt, allocates no generation and touches no
  //: archive byte, so it stays available when the archive does not. Once a
  //: cutover has happened the legacy keys are closed for good, and any seam
  //: reporting one of these three states is that fact.
  const POST_CUTOVER_STATES = ["sqlite_preferred", "source_changed",
                               "manual_recovery_required"];
  function workingDraftIsServerSide() {
    return [storageAuthority.status, legacyAuthority.status,
            storageAuthority.refusalCode, legacyAuthority.refusalCode]
      .some(s => POST_CUTOVER_STATES.indexOf(s) !== -1);
  }

  //: True when plans are read-only but the draft still saves. The UI has to say
  //: those as two sentences: "your plans are read-only" and "your draft is
  //: kept" are different facts, and reporting only the first loses work while
  //: reporting only the second reads as "storage is fine".
  function plansAreReadOnly() {
    const refusal = legacyWriteRefusal();
    return refusal === "source_changed" || refusal === "manual_recovery_required"
        || storageAuthority.status === "source_changed"
        || storageAuthority.status === "manual_recovery_required"
        || legacyAuthority.status === "manual_recovery_required"
        || legacyAuthority.status === "source_changed";
  }

  async function postWorkingDraft(draft) {
    // Not `postJSON`: that one aborts on a `state.revision` change, which is a
    // run-scoped concern and would make an ordinary draft save fail whenever the
    // user navigated while it was in flight.
    const capability = await ensureFireCapability();
    const r = await fetch("/api/storage/working-draft", { method: "POST",
      headers: { "Content-Type": "application/json",
                 "X-FIRE-Capability": capability },
      body: JSON.stringify({ draft }) });
    const j = await r.json().catch(() => ({}));
    if (!r.ok || j.error) throw new Error(j.error || `HTTP ${r.status}`);
    return j;
  }

  async function refreshWorkingDraft() {
    if (!workingDraftIsServerSide()) { _workingDraft = null; return null; }
    try {
      const r = await fetch("/api/storage/working-draft", { cache: "no-store" });
      const j = await r.json();
      _workingDraft = j && j.draft ? JSON.stringify(j.draft) : null;
    } catch (e) {
      // Unreachable is not the same as empty, but for a draft the safe
      // presentation is the same: offer nothing rather than offer something
      // that may not be there.
      _workingDraft = null;
    }
    return _workingDraft;
  }

  function saveDraft(silent) {
    if (workingDraftIsServerSide()) {
      const envelope = { v: DRAFT_V, config: state.config };
      _workingDraft = JSON.stringify(envelope);
      $("resumeDraft").style.display = "";
      // Two facts, said as two sentences. When the archive is read-only the
      // draft still saves, and reporting only one of those either loses the
      // user's work in their head or tells them storage is fine when it is not.
      const readOnly = plansAreReadOnly();
      const savedHint = readOnly ? tt("草稿已保存（计划只读）", "draft saved (plans read-only)")
                                 : tt("已保存", "saved");
      const savedToast = readOnly
        ? tt("计划当前只读；草稿已保留。", "Plans are read-only right now; your draft is kept.")
        : tt("草稿已保存", "Draft saved");
      if (silent) {
        // Autosaves coalesce: dragging a slider is one write, not one a frame.
        // The hint is set from the *result*, never ahead of it — an autosave
        // that did not land must not read as one that did.
        clearTimeout(_draftFlush);
        _draftFlush = setTimeout(() => {
          postWorkingDraft(envelope)
            .then(() => { $("saveHint").textContent = savedHint; })
            .catch(() => { $("saveHint").textContent = tt("未保存", "not saved"); });
        }, 400);
        return;
      }
      clearTimeout(_draftFlush);
      _draftFlush = null;
      $("saveHint").textContent = tt("保存中…", "saving…");
      postWorkingDraft(envelope).then(() => {
        toast(savedToast);
        $("saveHint").textContent = savedHint;
      }).catch(e => {
        toast(tt("草稿未能保存：", "Draft could not be saved: ") + e.message, true);
        $("saveHint").textContent = tt("未保存", "not saved");
      });
      return;
    }
    // Goes through the legacy storage seam (see "M4 · legacy storage seam"
    // below) so one migration fence can stop every legacy writer at once.
    const r = legacyStore.writeDraft(JSON.stringify({ v: DRAFT_V, config: state.config }));
    if (!r.ok) {
      // A refused write has to be visible.  Silently dropping the save is how a
      // fenced or already-migrated app ends up looking like it saved when it
      // did not — the one failure mode this whole slice exists to prevent.
      if (r.code !== "storage_full") toast(legacyStore.refusalMessage(r.code), true);
      $("saveHint").textContent = tt("未保存", "not saved");
      return;
    }
    if (!silent) toast(tt("草稿已保存", "Draft saved"));
    $("saveHint").textContent = tt("已保存", "saved");
  }
  function loadDraft() {
    try {
      const raw = JSON.parse(workingDraftIsServerSide()
                             ? _workingDraft : legacyStore.readDraftRaw());
      const cfg = raw && raw.v ? raw.config : raw;      // legacy drafts = bare config
      if (!cfg || typeof cfg !== "object") return null;
      return normalizeConfig(cfg);
    } catch (e) { return null; }
  }

  // =========================================================== ui bits
  let tT = null, tT2 = null;
  // Toast enters rising and leaves sinking along the same path (§7 spatial symmetry).
  // The exit is a two-beat: .leaving fades/sinks, then .hidden removes it — both timers
  // cancelled on a fresh toast so rapid messages never fight the wind-down.
  function toast(m, err) {
    if (!m) return; const t2 = $("toast");
    t2.textContent = m; t2.className = "toast" + (err ? " err" : "");
    clearTimeout(tT); clearTimeout(tT2);
    tT = setTimeout(() => {
      t2.classList.add("leaving");
      tT2 = setTimeout(() => { t2.classList.add("hidden"); t2.classList.remove("leaving"); }, 260);
    }, 3600);
  }
  let FIRE_CAPABILITY = null;
  let FIRE_CAPABILITY_PROMISE = null;
  async function ensureFireCapability() {
    if (FIRE_CAPABILITY) return FIRE_CAPABILITY;
    if (!FIRE_CAPABILITY_PROMISE) {
      FIRE_CAPABILITY_PROMISE = fetch("/api/capability", { cache: "no-store" })
        .then(async r => {
          const j = await r.json();
          if (!r.ok || !j.capability) throw new Error(j.error || "Local server capability unavailable");
          FIRE_CAPABILITY = j.capability;
          return FIRE_CAPABILITY;
        })
        .finally(() => { FIRE_CAPABILITY_PROMISE = null; });
    }
    return FIRE_CAPABILITY_PROMISE;
  }
  async function postJSON(url, body) {
    const revision = state.revision;
    const capability = await ensureFireCapability();
    const r = await fetch(url, { method: "POST", headers: {
      "Content-Type": "application/json",
      "X-FIRE-Capability": capability,
    }, body: JSON.stringify(body) });
    const j = await r.json();
    if (revision !== state.revision) { const e = new Error(""); e.stale = true; throw e; }
    if (!r.ok || j.error) {
      const e = new Error(j.error || `HTTP ${r.status}`);
      e.httpStatus = r.status;
      // The server names every refusal it makes; throwing away the name left
      // callers guessing from the status alone, and one of them guessed
      // wrong for every 409 that was not the one it had in mind.
      e.code = j.code || null;
      throw e;
    }
    return j;
  }

  // ====================================================== migration bridge
  // This is an explicit, shadow-only seam.  It reads exactly the two legacy
  // data keys, preserves each raw string byte-for-byte for the server-side
  // UTF-8 hash, and never enumerates or mutates localStorage.  The helpers are
  // intentionally not called from init(); a future migration UI must opt in.
  const MIGRATION_STORAGE_KEYS = Object.freeze(["fire_draft", "fire_plans_v1"]);
  async function sha256StorageString(raw) {
    if (typeof TextEncoder !== "function" || !globalThis.crypto
        || !globalThis.crypto.subtle) {
      throw new Error("SHA-256 unavailable for migration envelope");
    }
    const digest = await globalThis.crypto.subtle.digest(
      "SHA-256", new TextEncoder().encode(raw));
    return Array.from(new Uint8Array(digest), b => b.toString(16).padStart(2, "0")).join("");
  }
  async function readMigrationEnvelope() {
    const entries = [];
    for (const key of MIGRATION_STORAGE_KEYS) {
      const raw = localStorage.getItem(key);
      entries.push({
        key,
        present: raw !== null,
        raw,
        raw_sha256: raw === null ? null : await sha256StorageString(raw),
      });
    }
    return { envelope_version: 1, entries };
  }
  async function previewShadowMigration() {
    return postJSON("/api/migration/shadow_preview", {
      envelope: await readMigrationEnvelope(),
    });
  }
  async function stageShadowMigration() {
    return postJSON("/api/migration/shadow_stage", {
      envelope: await readMigrationEnvelope(),
    });
  }
  window.FIREMigration = Object.freeze({
    readEnvelope: readMigrationEnvelope,
    preview: previewShadowMigration,
    stage: stageShadowMigration,
  });

  // ==================================================== M4 · legacy storage seam
  // The single door to the two legacy localStorage keys.  Every other read and
  // write in this file goes through `legacyStore`; a direct setItem on either
  // key anywhere else is a gate failure (tests/ui_smoke.py scans for it).
  // Three jobs:
  //   1. centralize the writers, so one fence can stop all of them at once;
  //   2. build the canonical `fire-localstorage-envelope-v1` text the server
  //      parser accepts, byte-for-byte (server/formal_migration.py);
  //   3. hold the page-bound fence and the authority read that must happen
  //      before any plan or draft is touched.
  // Contract: PHASE_0_EXIT_CONTRACT.md §6.  Design and open questions:
  // DESIGN_M4_BROWSER_CUTOVER_2026-07-25.md.  The shadow bridge above is left
  // alone on purpose — the contract calls it compatibility scaffolding, not
  // migration evidence, so the formal seam is built beside it, not on it.
  const FORMAL_ENVELOPE_FORMAT = "fire-localstorage-envelope-v1";

  // One id per page instance.  Regenerated on every load and deliberately NOT
  // persisted anywhere: a fence that survives a reload is no longer page-bound,
  // which is the whole point of binding it.
  let _pageInstanceId = null;
  function pageInstanceId() {
    if (_pageInstanceId) return _pageInstanceId;
    const c = globalThis.crypto;
    if (c && typeof c.randomUUID === "function") return (_pageInstanceId = c.randomUUID());
    if (c && typeof c.getRandomValues === "function") {
      const b = new Uint8Array(16); c.getRandomValues(b);
      return (_pageInstanceId = Array.from(b, x => x.toString(16).padStart(2, "0")).join(""));
    }
    throw new Error("secure randomness unavailable for the migration page fence");
  }

  // Python encodes the envelope as UTF-8 and rejects what it cannot encode
  // (reason_code `value_utf8_invalid`).  A JS string can hold an unpaired
  // surrogate that JSON.stringify would escape instead of failing on, and the
  // two sides would then disagree about the bytes.  Refuse it here with the
  // same meaning.  Under /u a well-formed pair is one non-surrogate code
  // point, so this only matches lone surrogates.
  function assertUtf8Encodable(key, value) {
    if (/\p{Surrogate}/u.test(value)) {
      throw new Error(`legacy key ${key} holds unpaired UTF-16 surrogates`);
    }
  }

  // Mirror of server/persistence.py canonical_json_bytes for the exact shapes
  // this envelope uses: keys sorted (format < key_sha256 < keys), no spaces,
  // non-ASCII left raw.  JSON.stringify cannot be used on the whole object —
  // its key order is insertion order, not sorted — so the structure is
  // assembled by hand and only the leaf strings go through it for escaping.
  // Verified byte-identical against all three vectors in
  // tests/formal_migration_vectors.json, including the emoji + NUL case;
  // ui_smoke re-checks it inside the real WKWebView, which is where an
  // engine-specific escaping drift would actually bite.
  function canonicalEnvelopeText(envelope) {
    const q = JSON.stringify;
    const hashes = MIGRATION_STORAGE_KEYS.map(name => {
      const h = envelope.key_sha256[name];
      return `${q(name)}:${h === null ? "null" : q(h)}`;
    }).join(",");
    const entries = envelope.keys.map(e =>
      `{"name":${q(e.name)},"present":${e.present},"value":${e.value === null ? "null" : q(e.value)}}`
    ).join(",");
    return `{"format":${q(envelope.format)},"key_sha256":{${hashes}},"keys":[${entries}]}`;
  }

  // The one sanctioned raw read of both keys, in the fixed contract order.
  async function readFormalEnvelope() {
    const keys = [];
    const key_sha256 = {};
    for (const name of MIGRATION_STORAGE_KEYS) {
      const raw = localStorage.getItem(name);
      const present = raw !== null;
      if (present) assertUtf8Encodable(name, raw);
      keys.push({ name, present, value: present ? raw : null });
      key_sha256[name] = present ? await sha256StorageString(raw) : null;
    }
    return { format: FORMAL_ENVELOPE_FORMAT, key_sha256, keys };
  }
  async function formalEnvelopeDigest(envelope) {
    return sha256StorageString(canonicalEnvelopeText(envelope || await readFormalEnvelope()));
  }

  // In-memory only.  `status` starts unknown and is filled by the startup read;
  // nothing here is persisted, so a reload always re-asks the server.
  // A fence in any of these states keeps the legacy writers closed. Shared by
  // both seams so the two cannot drift apart: `/api/storage/state` reports one
  // `fence_state` and `/api/migration/authority` reports one per operation, and
  // they have to mean the same thing.
  const FENCE_VETO_STATES = ["held", "invalid", "expired"];

  const legacyAuthority = {
    status: "unknown",
    generation: null,
    fence: null,
    seamReachable: false,
    refusalCode: null,
  };

  async function refreshLegacyAuthority() {
    try {
      const r = await fetch("/api/migration/authority", { cache: "no-store" });
      const j = await r.json();
      // Structured refusal, not unreachability — the same distinction the §6
      // read needs. Both refresh paths threw a plain `Error`, so both turned a
      // server-reported latch into "we could not reach the seam".
      if (!r.ok || j.error) throw storageError(r.status, j);
      legacyAuthority.status = (j.authority && j.authority.status) || "unknown";
      legacyAuthority.generation = j.generation || null;
      const ops = Array.isArray(j.operations) ? j.operations : [];
      // `held`, `invalid` and `expired` all veto. Matching only `held` threw
      // away the two states that mean "a fence exists and something is wrong
      // with it", which is a stronger reason to keep the writers closed rather
      // than a weaker one. The contract (2222-2223) hands the writers back only
      // when a `retry_nonce` retry records the old operation as failed — never
      // on a timer and never because a fence went bad.
      legacyAuthority.fence = ops.find(
        o => o && FENCE_VETO_STATES.indexOf(o.fence_state) !== -1) || null;
      legacyAuthority.seamReachable = true;
    } catch (e) {
      // The seam is unreachable or erroring.  We deliberately do NOT latch the
      // app read-only here: before any cutover has happened localStorage really
      // is authoritative, and turning a server hiccup into "you cannot save"
      // would be strictly worse than today's behaviour, which has no gate at
      // all.  The gate only ever tightens on a *successful* read that reports a
      // non-legacy authority.  Once GPT lands the cheap pure-read
      // `GET /api/storage/state` (contract §6) this can afford to fail closed —
      // see DESIGN_M4_BROWSER_CUTOVER_2026-07-25.md §9 open question 1.
      const code = e && e.code;
      const body = (e && e.payload) || null;
      if (code === "manual_recovery_required" || (e && e.httpStatus === 423)) {
        // The seam answered and the answer is a latch. Reachable, and recorded
        // as such: the write gate must refuse for the stated reason rather than
        // for "unknown", and the banner must say a person is needed.
        legacyAuthority.seamReachable = true;
        legacyAuthority.status = "manual_recovery_required";
        legacyAuthority.fence = null;
        legacyAuthority.refusalCode = code || "manual_recovery_required";
        if (body) applyAuthorityPayload(body);
      } else {
        legacyAuthority.seamReachable = false;
        legacyAuthority.fence = null;
        legacyAuthority.refusalCode = null;
        // Deliberately no `legacy_assumed` downgrade, and no invented latch
        // either. `seamReachable` is false, and that alone is what
        // `legacyWriteRefusal` reads: a status invented from a failed read must
        // never be able to open the legacy writers, and must equally never
        // claim a fault the server never reported.
      }
    }
    // Rendered from the refresh itself, not only from the handful of call sites
    // that happened to remember. A banner that depends on someone else calling
    // it can leave a latched user looking at nothing, which is the defect this
    // closes rather than a tidiness point.
    try { renderStorageBanner(); } catch (e) { /* pre-DOM init */ }
    return legacyAuthority;
  }

  // Synchronous gate against the last successful read.  Returns null when the
  // write may proceed, otherwise a refusal code.  Kept synchronous on purpose:
  // storePlans is used read-modify-write and immediately followed by a re-read
  // in renderPlans, so an async write would render stale rows.  The contract's
  // stricter "check immediately before the mutation" wording needs the live
  // per-write check to be affordable, which it becomes once `/api/storage/*`
  // exists; swapping this one function is the whole change.
  function legacyWriteRefusal() {
    // A legacy write requires a *successful* authority read that says legacy is
    // authoritative. Nothing else opens this gate.
    //
    // The previous rule was "no successful read yet, so assume legacy", and it
    // was fail-open in the one case that matters. A page that loads after a
    // cutover starts at `unknown`; if both authority GETs then fail, it decided
    // localStorage was authoritative and let every legacy writer through. So a
    // cut-over user with a flaky local server could press Save draft, be told
    // "saved", and have `fire_draft` rewritten underneath an archive that is the
    // real authority — silently diverging, in the exact direction the cutover
    // exists to prevent. DESIGN_M4_BROWSER_CUTOVER_2026-07-25.md:209-212 forbids
    // it, and a reproduction in a real WKWebView confirmed it.
    //
    // Reads are unaffected: localStorage still reads, so an unreachable server
    // does not hide anyone's data. Only writes are refused, and they say why.
    // Both seams get a veto, and neither gets to override the other's veto. A
    // stale reading from one must not reopen the gate the other has closed, so
    // the non-legacy verdicts are collected first from both.
    // Ordered by how actionable the answer is, not by which seam answered
    // first. A latch outranks everything: with the archive latched, "plans have
    // moved — save from the new surface" is advice that cannot work, and it is
    // exactly what a cut-over page returned whenever the §6 read had last
    // succeeded and the migration seam then reported 423. Drift outranks the
    // plain moved notice for the same reason.
    const seen = [storageAuthority.status, legacyAuthority.status];
    for (const code of ["manual_recovery_required", "source_changed"]) {
      if (seen.indexOf(code) !== -1) return code;
    }
    if (seen.indexOf("sqlite_preferred") !== -1) return "sqlite_authoritative";
    // The gate opens only on a read that actually succeeded and said legacy.
    const legacyOk = legacyAuthority.status === "legacy_authoritative"
                     && legacyAuthority.seamReachable;
    const storageOk = storageAuthority.status === "legacy_authoritative"
                      && storageAuthority.reachable;
    // BOTH, not either. `||` was fail-open in the case that matters: one seam
    // unreachable and the other reporting legacy opened the gate, so a page
    // whose `/api/storage/state` was down rewrote `fire_draft` on the strength
    // of a `/api/migration/authority` answer alone — and the mirror of that.
    // An unreachable seam is not a seam that agrees; it is a seam with no
    // opinion, and one opinion is not the two this gate is defined over.
    //
    // The older comment above reasoned that failing closed would be worse than
    // 2.0's no-gate behaviour "once GPT lands the cheap pure-read
    // `GET /api/storage/state`". That endpoint exists now, so the condition
    // that argument was waiting on has been met.
    if (legacyOk && storageOk) {
      // A fence stops this writer whether it belongs to this page or another
      // one, and *either* seam may report it. Reading only
      // `legacyAuthority.fence` was a real bypass, not a tidiness problem:
      // `/api/storage/state` reports `fence_state` and the value was assigned
      // to `storageAuthority.fenceState` and never read, so a genuine held
      // fence plus a failing `/api/migration/authority` left
      // `legacyAuthority.fence` null while `storageOk` was true — the gate
      // opened and writeDraft() rewrote localStorage under a live cutover.
      //
      // So the composition is the same rule as the status vetoes above: either
      // seam may veto, and neither may reopen a fence the other has reported.
      // `unknown` is not a veto — it is the absence of a reading, and the
      // status branch already refuses when nothing has been read successfully.
      //
      // `expired` fences closed, and that is the contract's reading rather than
      // a choice made here: 2222-2223 says the writers stay fenced until a
      // `retry_nonce` retry records the old operation as failed. Expiry stops
      // the operation being finalizable; it does not hand the writers back on a
      // timer. The design note that said otherwise has been corrected.
      const fenced = legacyAuthority.fence
        || FENCE_VETO_STATES.indexOf(storageAuthority.fenceState) !== -1;
      return fenced ? "migration_fenced" : null;
    }
    // Everything else — unknown, unreachable, or a status this build does not
    // recognise — is read-only.
    return "authority_unavailable";
  }
  function refusalMessage(code) {
    if (code === "authority_unavailable") {
      return tt("无法确认本机存储归属，已暂时切换为只读，未写入任何数据。请重试或重启应用。",
                "Cannot confirm which store is authoritative — temporarily read-only, "
                + "nothing has been written. Retry or restart the app.");
    }
    if (code === "migration_fenced") return tt("迁移进行中，暂时无法保存", "Migration in progress — saving is paused");
    if (code === "sqlite_authoritative") return tt("计划已迁移到本地数据库，请从新界面保存", "Plans have moved to the local database — save from the new surface");
    if (code === "source_changed") return tt("检测到存储已在别处改动，已停止写入", "Storage changed elsewhere — writing has stopped");
    if (code === "manual_recovery_required") return tt("需要手工恢复，写入已锁定", "Manual recovery required — writing is locked");
    return tt("暂时无法保存", "Cannot save right now");
  }

  // ============================================== M4 §F · post-cutover seam
  // Everything above this line is the pre-cutover world. §F is the other side:
  // once the authority CAS has moved to `sqlite_preferred`, plans and drafts
  // live in the archive, the legacy keys stop being written, and every call
  // carries a proof of which authority it believes it is acting under.
  //
  // Contract: PHASE_0_EXIT_CONTRACT.md §6. The three things a caller sends:
  //   * `X-FIRE-Authority-Receipt` — the exact external receipt hash. A caller
  //     holding a stale one is refused rather than served fresher data, because
  //     a stale tab that reads successfully is a tab that will later write
  //     successfully.
  //   * `X-FIRE-Legacy-Digest` — *this page's freshly read* two-key digest, so
  //     drift in the legacy source is reported by the server rather than
  //     absorbed by a write that happened to notice it.
  //   * `Idempotency-Key` — on writes, mandatory and exactly equal to the body's
  //     `request_id`. It is the caller's statement of which action this is, and
  //     it is what a duplicate is refused against.

  // The live view of the storage authority. It does not replace anything above:
  // the pre-cutover gate still reads `legacyAuthority`. This is filled by §6's
  // pure read, which — unlike `/api/migration/authority` — is cheap enough to
  // call immediately before every write, which is what the contract's stricter
  // wording actually requires.
  const storageAuthority = {
    status: "unknown",
    generation: null,
    receipt: null,
    legacyDigestLastSeen: null,
    fenceState: "unknown",
    reachable: false,
    sourceChanged: false,
    // The exact contract code from the last structured refusal, or null. Kept
    // because "reachable but refusing, and here is why" is a state the browser
    // previously could not represent: a 423 became `reachable: false` and the
    // user was told to retry.
    refusalCode: null,
  };

  function storageIsAuthoritative() {
    return storageAuthority.status === "sqlite_preferred";
  }

  // Startup ordering, and it is not incidental. §F requires a *fresh* two-key
  // envelope digest to be read before state/observe is called: the digest is the
  // evidence of what the legacy source looks like right now, so reading it after
  // asking the server would report drift against the wrong bytes.
  async function readFreshLegacyDigest() {
    const envelope = await readFormalEnvelope();
    return { envelope, digest: await formalEnvelopeDigest(envelope) };
  }

  function applyAuthorityPayload(payload) {
    if (!payload || typeof payload !== "object") return;
    if (payload.authority_status) storageAuthority.status = payload.authority_status;
    if (payload.generation_id) storageAuthority.generation = payload.generation_id;
    if (payload.authority_receipt) storageAuthority.receipt = payload.authority_receipt;
    if (Object.prototype.hasOwnProperty.call(payload, "legacy_digest_last_seen")) {
      storageAuthority.legacyDigestLastSeen = payload.legacy_digest_last_seen;
    }
    if (payload.authority_status === "source_changed") {
      storageAuthority.sourceChanged = true;
    }
  }

  // A §6 refusal carries the current authority so a refused caller can
  // resynchronise without a second round-trip. Errors keep `code` and `payload`
  // so callers act on the distinction rather than on a message string.
  function storageError(status, body) {
    const e = new Error((body && (body.error || body.code)) || `HTTP ${status}`);
    e.httpStatus = status;
    e.code = body && body.code;
    e.payload = body || {};
    return e;
  }

  async function refreshStorageAuthority(freshDigest) {
    try {
      const r = await fetch("/api/storage/state", { cache: "no-store" });
      const j = await r.json();
      // A structured non-2xx is an *answer*, not a failure to reach the seam, and
      // the two must not be collapsed. This threw a plain `Error`, so a 423
      // carrying `code: "manual_recovery_required"` landed in the catch below and
      // came out as `reachable: false` — reported to the user as
      // `authority_unavailable`, "cannot confirm which store is authoritative,
      // retry or restart". The server had confirmed it exactly: the archive is
      // latched and needs a person. Telling someone to retry is the one piece of
      // advice that cannot help, and it hides a state they have to act on.
      if (!r.ok || j.error) throw storageError(r.status, j);
      storageAuthority.status = j.authority_status || "unknown";
      storageAuthority.generation = j.generation_id || null;
      storageAuthority.receipt = j.receipt_sha256 || null;
      storageAuthority.legacyDigestLastSeen = j.legacy_digest_last_seen || null;
      storageAuthority.fenceState = j.fence_state || "unknown";
      storageAuthority.reachable = true;
      storageAuthority.sourceChanged = j.authority_status === "source_changed";
      // This read is pure and cannot move authority; drift is reported through
      // the explicit observation POST and nowhere else. That is exactly why a
      // GET is allowed to be cheap enough to call this often.
      if (typeof freshDigest === "string" && storageIsAuthoritative()
          && storageAuthority.legacyDigestLastSeen
          && freshDigest !== storageAuthority.legacyDigestLastSeen) {
        await observeLegacyDigest(freshDigest);
      }
    } catch (e) {
      if (e && e.payload && typeof e.payload === "object"
          && (e.payload.authority_status || e.code)) {
        // The seam answered. It is reachable, and what it said is preserved:
        // §6 requires every error response to carry the current authority, so
        // this is the authoritative reading, not a guess assembled from a
        // failure. `refusalCode` is what the banner shows, so the user is told
        // the actual state — a latch needs a person, not a retry.
        storageAuthority.reachable = true;
        storageAuthority.refusalCode = e.code || null;
        applyAuthorityPayload(e.payload);
        if (e.code === "manual_recovery_required"
            || e.httpStatus === 423) {
          storageAuthority.status = "manual_recovery_required";
        }
      } else {
        // A transport or JSON failure: nothing was established, and nothing is
        // invented. In particular a latch is never inferred from a failed read —
        // claiming manual recovery when the server simply could not be reached
        // would be the mirror of the bug above, and would send the user hunting
        // for a fault that does not exist.
        storageAuthority.reachable = false;
        storageAuthority.refusalCode = null;
        // No downgrade, and no invented status. `legacy_assumed` used to be set
        // here and then treated as permission to write, which is the fail-open
        // an earlier repair removed. An unreachable seam leaves the status
        // exactly as it was — `unknown` on a fresh page — and
        // `legacyWriteRefusal` refuses anything that is not a successful
        // `legacy_authoritative` read.
      }
    }
    // The banner tracks whatever the last read established, including "we could
    // not establish anything". Rendering it only at init and after a cutover left
    // the read-only state invisible in exactly the case A1 is about.
    if (typeof renderStorageBanner === "function") renderStorageBanner();
    return storageAuthority;
  }

  // The only call that can move authority off `sqlite_preferred`. Drift comes
  // back as a refusal, not a success: the caller has to stop writing, and a 200
  // would invite it not to.
  async function observeLegacyDigest(digest) {
    const requestId = `observe:${pageInstanceId()}:${digest.slice(0, 16)}`;
    try {
      await storagePost("/api/storage/observe", {
        request_id: requestId,
        authority_receipt: storageAuthority.receipt,
        expected_generation: storageAuthority.generation,
        legacy_digest: digest,
      });
      return { drift: false };
    } catch (e) {
      if (e.code === "source_changed") {
        // Read-only recovery from here: reads still serve the recovery view,
        // writes do not.
        storageAuthority.status = "source_changed";
        storageAuthority.sourceChanged = true;
        applyAuthorityPayload(e.payload);
        return { drift: true };
      }
      throw e;
    }
  }

  function storageReadHeaders(freshDigest) {
    const headers = {};
    if (storageAuthority.receipt) {
      headers["X-FIRE-Authority-Receipt"] = storageAuthority.receipt;
    }
    // Always this page's freshly read digest, never the value the server last
    // told us. Echoing the server's own last-seen digest back at it would make
    // drift undetectable by construction.
    if (freshDigest) headers["X-FIRE-Legacy-Digest"] = freshDigest;
    return headers;
  }

  async function storageGet(path, freshDigest) {
    const r = await fetch(path, { cache: "no-store",
                                  headers: storageReadHeaders(freshDigest) });
    const j = await r.json();
    if (!r.ok || j.error) {
      applyAuthorityPayload(j);
      throw storageError(r.status, j);
    }
    return j;
  }

  async function storagePost(path, body) {
    const capability = await ensureFireCapability();
    const r = await fetch(path, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "X-FIRE-Capability": capability,
        // Exactly the body's `request_id`. The server requires both and requires
        // them equal: the header is what makes the request identity visible in
        // front of the seam, the body copy is what the signed request
        // fingerprint covers, and if they could differ then the value that was
        // authenticated and the value deduplicated on would be two strings.
        "Idempotency-Key": String(body.request_id),
      },
      body: JSON.stringify(body),
    });
    const j = await r.json();
    if (!r.ok || j.error) {
      applyAuthorityPayload(j);
      throw storageError(r.status, j);
    }
    applyAuthorityPayload(j);
    return j;
  }

  let _storageWriteSequence = 0;

  // A write proves authority immediately before it mutates, per §6 — which is
  // what the pre-cutover synchronous gate could not afford. A stale tab is
  // refused with 412 and the current receipt; it resynchronises and decides with
  // fresh facts rather than retrying blindly.
  async function storageWrite(path, action, options) {
    const opts = options || {};
    const { digest } = await readFreshLegacyDigest();
    // The digest is *passed*, which is what makes this an observation and not just
    // a read. Calling `refreshStorageAuthority()` bare read the fresh digest and
    // then threw it away, so a first write after drift was refused by the server
    // (correctly) while the durable authority never moved to `source_changed` and
    // no read-only banner appeared. Drift was only ever noticed if something
    // happened to call `FIREStorage.state()` first — which the old smoke did,
    // hiding the gap.
    await refreshStorageAuthority(digest);
    if (!storageIsAuthoritative()) {
      throw storageError(409, {
        code: storageAuthority.sourceChanged ? "source_changed"
                                             : "sqlite_not_authoritative",
        authority_status: storageAuthority.status,
      });
    }
    const requestId = opts.requestId
      || `${action.kind}:${pageInstanceId()}:${++_storageWriteSequence}`;
    const body = Object.assign({
      request_id: requestId,
      authority_receipt: storageAuthority.receipt,
      expected_generation: storageAuthority.generation,
      legacy_digest: digest,
    }, action.body);
    try {
      return await storagePost(path, body);
    } catch (e) {
      if (e.code === "stale_authority" && !opts.noResync) {
        // Resync with the digest too, for the same reason.
        const fresh = await readFreshLegacyDigest();
        // Resynchronise, then hand the refusal to the caller. Automatically
        // retrying the same request id would be refused by §6, and retrying
        // under a *new* key would be this code inventing an intent the user
        // never expressed. Resync is ours to do; deciding is not.
        await refreshStorageAuthority(fresh.digest);
      }
      throw e;
    }
  }

  // The public finalize flow driven from the browser, with the two-key readback
  // §F requires: read the envelope fresh, drive the five migration endpoints,
  // then read both keys back and confirm the digest the cutover was performed
  // against is still the digest of what is in localStorage.
  async function runPublicCutover() {
    const before = await readFreshLegacyDigest();
    const page = pageInstanceId();
    const preview = await postJSON("/api/migration/preview",
                                   { envelope: before.envelope });
    const operationId = preview.operation_id;
    await postJSON("/api/migration/stage",
                   { operation_id: operationId, envelope: before.envelope });
    await postJSON("/api/migration/import",
                   { operation_id: operationId, envelope: before.envelope });
    const verified = await postJSON("/api/migration/verify", {
      operation_id: operationId, envelope: before.envelope,
      page_instance_id: page,
    });
    if (!verified.cutover_eligible) {
      throw new Error("migration is not cutover-eligible");
    }
    // The fence is live from here and is bound to this page: a second tab
    // holding no fence cannot finalize, because the server compares the page
    // instance encoded in the fence id against the one presented.
    const receipt = await postJSON("/api/migration/finalize", {
      operation_id: operationId, envelope: before.envelope,
      page_instance_id: page,
      legacy_fence_id: verified.legacy_fence_id,
      legacy_fence_digest: verified.legacy_fence_digest,
    });
    // Two-key readback: re-read both legacy keys and require the digest to be
    // the one the cutover was performed against. Anything else means the source
    // moved underneath the cutover, and the result cannot be treated as final.
    const after = await readFreshLegacyDigest();
    const readbackMatches = after.digest === before.digest;
    await refreshStorageAuthority(after.digest);
    return { receipt, operation_id: operationId,
             fence_id: verified.legacy_fence_id,
             envelope_digest_before: before.digest,
             envelope_digest_after: after.digest,
             readback_matches: readbackMatches,
             authority_status: storageAuthority.status };
  }

  // Reads and writes for plans and drafts once SQLite is the authority. Each
  // sends the three headers; each write is a CAS against the version tip the
  // caller believes is current, never resolved by guessing.
  const storageApi = Object.freeze({
    async state() {
      const { digest } = await readFreshLegacyDigest();
      return refreshStorageAuthority(digest);
    },
    async plans() {
      const { digest } = await readFreshLegacyDigest();
      return storageGet("/api/storage/plans", digest);
    },
    async recoveredDrafts() {
      const { digest } = await readFreshLegacyDigest();
      return storageGet("/api/storage/recovered-drafts", digest);
    },
    createPlan(plan, options) {
      return storageWrite("/api/storage/plan",
                          { kind: "plan", body: { plan } }, options);
    },
    createPlanVersion(planId, expectedTip, sourceConfig, normalizedConfig, options) {
      return storageWrite("/api/storage/plan-version", { kind: "plan-version", body: {
        plan_id: planId,
        expected_current_version_id: expectedTip,
        source_config: sourceConfig,
        normalized_config: normalizedConfig,
      } }, options);
    },
    duplicatePlan(sourcePlanId, expectedTip, displayName, options) {
      return storageWrite("/api/storage/plan-duplicate", { kind: "plan-duplicate", body: {
        source_plan_id: sourcePlanId,
        expected_current_version_id: expectedTip,
        display_name: displayName,
      } }, options);
    },
    setPlanStatus(planId, expectedTip, status, options) {
      return storageWrite("/api/storage/plan-status", { kind: "plan-status", body: {
        plan_id: planId,
        expected_current_version_id: expectedTip,
        status,
      } }, options);
    },
    saveDraft(draftId, normalizedConfig, displayName, options) {
      return storageWrite("/api/storage/draft", { kind: "draft", body: {
        draft_id: draftId,
        normalized_config: normalizedConfig,
        display_name: displayName,
      } }, options);
    },
    observe: observeLegacyDigest,
    cutover: runPublicCutover,
    // Exposed so a test can present a deliberately stale receipt. `storageWrite`
    // cannot be made to do that — it re-reads the authority immediately before
    // it mutates, which is §6's requirement and a property worth keeping.
    post: storagePost,
    authority: () => storageAuthority,
    isAuthoritative: storageIsAuthoritative,
    freshDigest: readFreshLegacyDigest,
    refreshAuthority: refreshStorageAuthority,
  });
  window.FIREStorage = storageApi;

  const legacyStore = Object.freeze({
    // reads
    readDraftRaw: () => localStorage.getItem("fire_draft"),
    hasDraft: () => localStorage.getItem("fire_draft") !== null,
    readPlansRaw: () => localStorage.getItem("fire_plans_v1"),
    // writes — every one of them passes the gate first
    writeDraft(text) {
      const refusal = legacyWriteRefusal();
      if (refusal) return { ok: false, code: refusal };
      try { localStorage.setItem("fire_draft", text); return { ok: true }; }
      catch (e) { return { ok: false, code: "storage_full" }; }
    },
    writePlans(text) {
      const refusal = legacyWriteRefusal();
      if (refusal) return { ok: false, code: refusal };
      try { localStorage.setItem("fire_plans_v1", text); return { ok: true }; }
      catch (e) { return { ok: false, code: "storage_full" }; }
    },
    // migration surface
    pageInstanceId,
    readEnvelope: readFormalEnvelope,
    canonicalText: canonicalEnvelopeText,
    digest: formalEnvelopeDigest,
    refreshAuthority: refreshLegacyAuthority,
    authority: () => legacyAuthority,
    refusal: legacyWriteRefusal,
    refusalMessage,
  });
  window.FIRELegacyStore = legacyStore;

  function setLang(l) {
    L = l; localStorage.setItem("fire_lang", l); applyI18n();
    // Every view whose content is BUILT (not data-i18n tagged) re-renders here —
    // if you add a rendered surface, add its branch or it will stick in the old
    // language until its next natural re-render (audited 2026-07-10).
    if (state.view === "welcome") {
      renderPersonas(); renderPlans(); renderRecoveredDrafts(); renderStanding();
    }
    if (state.view === "wizard") { buildRail(); buildStep(); $("saveHint").textContent = ""; }
    if (state.view === "precision") buildPrecision();
    if (state.view === "computing") startFacts();
    if (state.view === "help") buildHelp();
    if (state.view === "results") {
      // drill-down results are cheap to re-request but carry no stored
      // params — clear them instead of leaving stale-language content
      ["fanDrill", "termDrill"].forEach(id => { const e = $(id); if (e) e.style.display = "none"; });
      const th = $("termHint"); if (th) th.textContent = t("drill.term.hint");
      resultTabs(); showPage(state.page);
    }
  }

  // =========================================================== help / guide
  function buildHelp() {
    const S = (zh, en) => tt(zh, en);
    const sec = (t2, body) => `<div class="panel"><div class="panel-title sm">${t2}</div><div class="panel-note" style="margin:8px 0 0;font-size:13px;line-height:1.8">${body}</div></div>`;
    $("helpBody").innerHTML = [
      // The topic index goes FIRST. The three-steps prose below is for
      // somebody arriving; a reader who opened help while staring at a
      // control is looking for that control, and should not scroll past an
      // introduction to reach 202 explanations that already exist.
      `<div class="help-index">${renderHelpTopics()}</div>`,
      sec(S("三步上手", "Three steps"), S(
        "① 用「60 秒速估」或选一个像你的原型开始;② 在向导里把数字改成你自己的（每个字段都有 ⓘ 说明,最后有复核页帮你抓异常）;③ 选精度跑一次,从「结论」页倒着读回来。",
        "① Start with the 60-second estimate or a persona; ② replace the numbers with yours in the wizard (every field has an ⓘ hint; the review step flags anomalies); ③ pick a precision, run, and read the results starting from Conclusions.")),
      sec(S("六个结果页各在回答什么", "What each results page answers"), S(
        "<b>概览</b>——一句话裁决:成功率、FIRE 年龄、可持续消费、遗产。<b>轨迹</b>——财富随年龄的分布扇形;拖动 FIRE 竖线可反解「提前退休需要什么」。<b>分布</b>——终值/消费/里程碑的完整分布,不只中位数。<b>敏感性 & 压力</b>——哪个假设动一下结果动多少;坏序列开局的压力测试。<b>A/B</b>——两套方案并排。<b>结论</b>——由你的数字生成的解读 + 模型局限的完整清单。",
        "<b>Overview</b> — the one-sentence verdict. <b>Trajectory</b> — the wealth fan by age; drag the FIRE line to solve 'what would earlier take'. <b>Distributions</b> — full distributions, not just medians. <b>Sensitivity & stress</b> — which assumptions move results, plus bad-sequence stress tests. <b>A/B</b> — two plans side by side. <b>Conclusions</b> — readings generated from your numbers + the full list of model limitations.")),
      sec(S("术语表", "Glossary"), S(
        "<b>SWR</b> 安全提取率——退休首年从组合提取的比例,门槛=年支出÷SWR。<b>Guyton-Klinger (GK)</b>——动态提取规则:市场差时少花、好时多花,把破产风险换成消费波动。<b>三分支成功</b>——「到达 FI 且终身不耗尽」或「退休前身故」都算成功,把没攒够与没活到分开。<b>real vs 名义</b>——real=今日购买力,名义=未来票面数字。<b>P10/P50/P90</b>——10%/50%/90% 分位:P50 是「一半情形比这好」。<b>regime</b>——每条路径抽一次的市场情景(高估值/AI 持续/历史均值)。",
        "<b>SWR</b> — first-year withdrawal rate; FI number = spending ÷ SWR. <b>Guyton-Klinger</b> — dynamic withdrawals: spend less in bad markets, more in good, trading ruin for variance. <b>Three-branch success</b> — reached FI & never depleted, or died before retiring. <b>Real vs nominal</b> — today's purchasing power vs future face value. <b>P10/P50/P90</b> — percentiles; P50 = half of scenarios do better. <b>Regime</b> — a market scenario drawn once per path.")),
      sec(S("常见问题", "FAQ"), S(
        "<b>数字可信吗？</b>每次运行都自检现金流对账;结构化收入实际到账年按到手现金精确记录,无到账的成功年份保留每年不超过 $1 的历史提取容差,超过容差的现金缺口判为失败。协议(路径数/种子/模式)也印在结果里,可复现。<b>为什么两次结果略有不同？</b>同种子同精度完全一致;不同精度是不同的抽样。<b>我的数据在哪里？</b>界面只通过本机回环连接与随 app 运行的引擎通信,不连接外部主机;迁移后计划在 App Support 下的 SQLite 档案中,未保存草稿在同目录的私有文件中。<b>城市库的数字准吗？</b>是示意默认值,用于探索,请按自己的真实情况修改。<b>这是投资建议吗？</b>不是——这是教育性质的情景分析工具,决策请结合自身情况与专业人士。",
        "<b>Can I trust the numbers?</b> Every run checks cash accounting. Years with an actual structured-income receipt record delivered cash exactly; successful no-receipt years retain at most $1/year of historical withdrawal tolerance, and larger cash gaps fail. The run protocol (paths/seed/mode) is printed for reproducibility. <b>Why do two runs differ?</b> Same seed & precision = identical; different precision = different sampling. <b>Where is my data?</b> The UI talks only to the bundled engine over loopback and contacts no external host; after migration, plans live in the SQLite archive under App Support and the unsaved draft lives in a private file beside it. <b>Are city defaults accurate?</b> Illustrative — edit them to your reality. <b>Is this investment advice?</b> No — an educational scenario tool; decide with your own context and professionals.")),
    ].join("");
  }

  // =========================================================== saved plans
  // Authority-aware, and this is the seam that makes §F a product path rather
  // than an API wrapper. Before cutover these read and write the legacy key;
  // after it they read and write the archive through §6, and the legacy key is
  // never written again.
  //
  // `list()` stays synchronous because renderPlans is read-modify-write and is
  // followed immediately by a re-render, so an async read here would paint stale
  // rows. The SQLite side therefore keeps a cached projection that `refresh()`
  // fills, and every mutation refreshes before returning.
  const PLANS_KEY = "fire_plans_v1";
  let _sqlitePlans = [];

  function sqlitePlanToRecord(plan) {
    // The UI's record shape, built from the archive row rather than from a
    // localStorage blob. `id` is the server plan id, so opening, renaming,
    // duplicating and deleting all address the archive object directly.
    const config = plan.normalized_config || {};
    return {
      id: plan.plan_id,
      // The version config's name wins over `plans.display_name`. §6 has no
      // endpoint that mutates the plans row, and it should not: a rename is a new
      // immutable version, so the current version's name *is* the current name.
      // `display_name` is what the plan was created as.
      name: get(config, "name") || plan.display_name || "",
      ts: Date.parse(plan.version_created_at || plan.created_at || "") || Date.now(),
      config,
      status: plan.status,
      current_version_id: plan.current_version_id,
      server: true,
      // A server-owned plan *is* its own archive lineage — its id is the archive
      // `plan_id` and its tip is the archive `plan_version_id`. Leaving this out
      // cost two things that both look like data loss to a user: the Timeline
      // button disappeared (renderPlans only shows it when `archive.plan_id`
      // exists), and opening a migrated plan left `archiveRef` null, so the next
      // formal run sent no `plan_id` and the server created a *second* Plan
      // beside the one the user thought they were running.
      archive: {
        plan_id: plan.plan_id,
        plan_version_id: plan.current_version_id || null,
      },
    };
  }

  const planStore = {
    isServer: () => storageIsAuthoritative(),

    list() {
      if (storageIsAuthoritative()) return _sqlitePlans.slice();
      try { return JSON.parse(legacyStore.readPlansRaw()) || []; }
      catch (e) { return []; }
    },

    async refresh() {
      if (!storageIsAuthoritative()) { _sqlitePlans = []; return this.list(); }
      try {
        const payload = await storageApi.plans();
        _sqlitePlans = (payload.plans || [])
          .filter(plan => plan.status !== "deleted")
          .map(sqlitePlanToRecord)
          .sort((a, b) => b.ts - a.ts);
      } catch (e) {
        // A read that fails must not silently present an empty plan list as
        // "you have no plans"; that is indistinguishable from data loss.
        _sqlitePlans = [];
        toast(storageRefusalMessage(e), true);
      }
      return this.list();
    },

    async save(record) {
      if (!storageIsAuthoritative()) {
        const plans = this.list();
        plans.unshift(record);
        return this._writeLegacy(plans);
      }
      try {
        await storageApi.createPlan({
          display_name: record.name,
          normalized_config: record.config,
        });
        await this.refresh();
        return true;
      } catch (e) { toast(storageRefusalMessage(e), true); return false; }
    },

    async rename(record, name) {
      if (!storageIsAuthoritative()) {
        const plans = this.list();
        const found = plans.find(x => x.id === record.id);
        if (found) { found.name = name; if (found.config) found.config.name = name; }
        return this._writeLegacy(plans);
      }
      // A rename is a new immutable version, CAS'd against the tip the UI is
      // showing. There is no in-place edit of a version by design.
      try {
        const config = Object.assign({}, record.config, { name });
        await storageApi.createPlanVersion(record.id, record.current_version_id,
                                          config, config);
        await this.refresh();
        return true;
      } catch (e) { toast(storageRefusalMessage(e), true); return false; }
    },

    async duplicate(record) {
      if (!storageIsAuthoritative()) {
        const plans = this.list();
        plans.unshift({ id: String(Date.now()), name: record.name + " · copy",
                        ts: Date.now(),
                        config: JSON.parse(JSON.stringify(record.config)) });
        return this._writeLegacy(plans);
      }
      try {
        await storageApi.duplicatePlan(record.id, record.current_version_id,
                                      record.name + " · copy");
        await this.refresh();
        return true;
      } catch (e) { toast(storageRefusalMessage(e), true); return false; }
    },

    async remove(record) {
      if (!storageIsAuthoritative()) {
        return this._writeLegacy(this.list().filter(x => x.id !== record.id));
      }
      // §6 makes plan status the only deletion boundary: no row is removed, the
      // tombstone lives in one place, and history stays readable behind it.
      try {
        await storageApi.setPlanStatus(record.id, record.current_version_id,
                                      "deleted");
        await this.refresh();
        return true;
      } catch (e) { toast(storageRefusalMessage(e), true); return false; }
    },

    _writeLegacy(plans) {
      const r = legacyStore.writePlans(JSON.stringify(plans));
      if (!r.ok) toast(r.code === "storage_full"
        ? tt("存储空间不足", "Storage full")
        : legacyStore.refusalMessage(r.code), true);
      return r.ok;
    },
  };
  window.FIREPlanStore = planStore;

  //: Drafts a cutover carried over, and the two things a user can do with one.
  //:
  //: The migration imports `fire_draft` into `recovered_drafts` as immutable
  //: evidence. Before this existed there was no read that returned a `draft_id`,
  //: so `POST /api/storage/draft` — which promotes one to a real Plan — could not
  //: be called by the product at all, and a user's unsaved work survived the
  //: cutover into a place they could not reach. The evidence stays immutable:
  //: promotion appends a `user_saved` event and never rewrites the draft row.
  const recoveredStore = {
    items: [],
    async refresh() {
      if (!storageIsAuthoritative()) { recoveredStore.items = []; return []; }
      try {
        const r = await storageApi.recoveredDrafts();
        recoveredStore.items = Array.isArray(r.recovered_drafts)
          ? r.recovered_drafts : [];
      } catch (e) {
        // A read that fails leaves the list empty rather than stale: showing a
        // draft that may already have been promoted invites a second copy.
        recoveredStore.items = [];
      }
      return recoveredStore.items;
    },
    async promote(draftId, displayName) {
      const item = recoveredStore.items.find(d => d && d.draft_id === draftId);
      if (!item) throw new Error("recovered draft is no longer listed");
      // The promotion §6 always had and nothing could call, because no read
      // handed the browser a `draft_id`. It creates a *new* Plan; §6 is explicit
      // that a recovered draft is never merged into an existing one, so there is
      // no plan_id or expected tip here. Note the argument order — config before
      // display name; there is deliberately no second wrapper for this call.
      const saved = await storageApi.saveDraft(
        draftId, item.normalized_config,
        displayName || tt("恢复的草稿", "Recovered draft"));
      // Both lists move: the draft leaves this one and a Plan joins the other.
      await recoveredStore.refresh();
      await planStore.refresh();
      return saved;
    },
  };
  window.FIRERecoveredDrafts = recoveredStore;

  function renderRecoveredDrafts() {
    const box = document.getElementById("recoveredBox");
    const list = document.getElementById("recoveredList");
    if (!box || !list) return;
    const items = recoveredStore.items || [];
    box.style.display = items.length ? "" : "none";
    list.innerHTML = "";
    items.forEach(item => {
      const cfg = item.normalized_config || {};
      const when = item.created_at
        ? new Date(item.created_at).toLocaleDateString() : "";
      const row = document.createElement("div");
      row.className = "plan-row";
      row.dataset.draftId = item.draft_id;
      // `.pn` / `.pm` are the plan-row's own classes: the name carries the
      // `flex:1` that pushes the buttons right, and the meta is the mono
      // nowrap style. A recovered draft sits directly under the plan list, so
      // it has to be built out of the same two rules rather than a third one.
      const name = document.createElement("span");
      name.className = "pn";
      name.textContent = get(cfg, "name")
        || tt("未保存的草稿", "Unsaved draft");
      const meta = document.createElement("span");
      meta.className = "pm";
      meta.textContent = `${when} · ${get(cfg, "state.start_age") || "?"}`
        + `${tt("岁", "y")} · ${tt("支出", "spend ")}`
        + `${money(+get(cfg, "state.expenses_y0") || 0)}`;
      const open = document.createElement("button");
      open.className = "btn-ghost sm";
      open.dataset.a = "open-recovered";
      open.textContent = tt("打开", "Open");
      open.addEventListener("click", () => {
        // Opening is local: it loads the config into the wizard and promotes
        // nothing. The user decides whether it becomes a Plan.
        clearActivePlanRef();
        state.config = normalizeConfig(item.normalized_config);
        // Same reset the plan-open handler does. Without it, opening a draft
        // while quick mode is on inherits quick mode's shorter step set and
        // the wizard shows a different set of questions than the draft has.
        state.quick = false;
        state.step = 0;
        goto("wizard");
      });
      const save = document.createElement("button");
      save.className = "btn-ghost sm";
      save.dataset.a = "save-recovered";
      save.textContent = tt("保存为计划", "Save as plan");
      save.addEventListener("click", async () => {
        save.disabled = true;
        try {
          await recoveredStore.promote(item.draft_id);
          renderRecoveredDrafts();
          renderPlans();
          toast(tt("已保存为计划", "Saved as a plan"));
        } catch (e) {
          toast(storageRefusalMessage(e), true);
          save.disabled = false;
        }
      });
      row.appendChild(name);
      row.appendChild(meta);
      row.appendChild(open);
      row.appendChild(save);
      list.appendChild(row);
    });
  }

  function storageRefusalMessage(error) {
    const code = error && error.code;
    if (code === "source_changed") {
      return tt("检测到存储已在别处改动，已切换为只读",
                "Storage changed elsewhere — switched to read-only");
    }
    if (code === "stale_authority") {
      return tt("页面数据已过期，请重新载入", "This page is out of date — reload");
    }
    if (code === "idempotency_conflict") {
      return tt("该操作已经执行过", "That action has already been performed");
    }
    if (code === "version_conflict") {
      return tt("计划已在别处修改，请重新载入", "Plan changed elsewhere — reload");
    }
    if (code === "manual_recovery_required") {
      return tt("需要手工恢复，写入已锁定", "Manual recovery required — writing is locked");
    }
    return (error && error.message) || tt("暂时无法保存", "Cannot save right now");
  }

  const loadPlans = () => planStore.list();

  //: The read-only / fail-closed states, made visible.
  //
  //: `source_changed` and `manual_recovery_required` are not "saving is a bit
  //: broken"; they are states in which the app must not write and the user has to
  //: be told why, because the alternative is a UI that looks normal and quietly
  //: refuses every save.
  function renderStorageBanner() {
    let box = document.getElementById("storageBanner");
    const refusal = legacyWriteRefusal();
    // The exact code the server sent, from whichever seam reported it. Shown so
    // the message is about what happened rather than a generic apology.
    const code = storageAuthority.refusalCode || legacyAuthority.refusalCode || "";
    // Whichever seam knows. Keying this on `storageAuthority.status` alone left
    // the worst case silent: the migration seam says 423 and the §6 read is
    // unreachable, so the write gate correctly refuses as
    // `manual_recovery_required` while the banner rendered nothing — a user
    // locked out with no explanation on screen.
    const latched = refusal === "manual_recovery_required"
                    || storageAuthority.status === "manual_recovery_required"
                    || legacyAuthority.status === "manual_recovery_required";
    const drifted = refusal === "source_changed"
                    || storageAuthority.status === "source_changed"
                    || legacyAuthority.status === "source_changed";
    const readOnly = latched || drifted
                     || refusal === "authority_unavailable";
    if (!readOnly) { if (box) box.remove(); return; }
    if (!box) {
      box = document.createElement("div");
      box.id = "storageBanner";
      box.className = "toast err";
      box.style.cssText = "position:fixed;left:12px;right:12px;top:12px;z-index:9999;"
                        + "text-align:center;padding:10px 14px";
      document.body.appendChild(box);
    }
    // A latch outranks drift and both outrank "cannot tell": a stated fault is
    // more informative than the absence of a reading, and telling someone to
    // retry when the server has said a person is needed is the one message that
    // cannot help.
    box.textContent =
      !latched && !drifted ? refusalMessage(refusal)
      : drifted && !latched
      ? tt("检测到本机存储已在别处改动。已切换为只读，未写入任何数据。",
           "Local storage changed elsewhere. Switched to read-only; nothing has been written.")
      : tt("需要手工恢复：存档已被锁定，写入已停止，未写入任何数据。"
           + "重试无法解决，请查看恢复日志。"
           + (code ? "（" + code + "）" : ""),
           "Manual recovery required: the archive is latched, writing has stopped, "
           + "and nothing has been written. Retrying will not help — check the "
           + "recovery journal."
           + (code ? ` (${code})` : ""));
    box.classList.remove("hidden");
  }

  //: The production cutover control flow.
  //
  //: `storageApi.cutover()` is the mechanism; this is the product path — it
  //: decides *whether* to offer a cutover, asks the user, drives it, and then
  //: brings the UI over to the archive. Leaving only the mechanism exposed would
  //: mean the cutover was reachable in tests and nowhere else.
  async function offerCutover() {
    // The container, not the button. The button's own `display` was already
    // correct; its parent was `plansBox`, which renderPlans() hides when there
    // are no saved plans — so a draft-only user saw nothing to click and had no
    // route to a cutover at all. Toggling a child inside a hidden parent is not
    // visibility.
    const box = document.getElementById("migrateBox");
    if (!box) return;
    const eligible = storageAuthority.status === "legacy_authoritative"
                     && storageAuthority.reachable
                     && (legacyStore.hasDraft() || planStore.list().length > 0);
    box.style.display = eligible ? "" : "none";
  }

  async function runCutoverFromUi() {
    const button = document.getElementById("migrateBtn");
    if (!confirm(tt("把本机计划迁移到本地数据库？迁移后旧存储不再被写入。",
                    "Move your plans into the local database? The old storage stops being written after this."))) {
      return;
    }
    if (button) button.disabled = true;
    try {
      const result = await storageApi.cutover();
      if (!result.readback_matches) {
        toast(tt("迁移期间存储发生变化，已中止", "Storage changed during the migration — stopped"), true);
        return;
      }
      await planStore.refresh();
      // The cutover is the moment a recovered draft comes into existence, so the
      // list has to be re-read here and not only at the next startup — otherwise
      // the user's carried-over draft is invisible until they restart the app.
      await recoveredStore.refresh();
      // The working draft moves house at the same moment: `fire_draft` stops
      // being written and the side-store takes over. It starts empty — the
      // pre-cutover draft is already carried into `recovered_drafts` as
      // evidence and offered by the list above, and two copies of one draft is
      // worse than none — so Resume must stop offering the legacy one.
      await refreshWorkingDraft();
      $("resumeDraft").style.display = _workingDraft ? "" : "none";
      renderPlans();
      renderRecoveredDrafts();
      renderStorageBanner();
      await offerCutover();
      toast(tt("已迁移到本地数据库", "Moved into the local database"));
    } catch (e) {
      toast(storageRefusalMessage(e), true);
    } finally {
      if (button) button.disabled = false;
    }
  }
  window.FIRECutover = { offer: offerCutover, run: runCutoverFromUi };
  const cloneArchiveRef = ref => (ref && ref.plan_id) ? {
    plan_id: String(ref.plan_id),
    plan_version_id: ref.plan_version_id ? String(ref.plan_version_id) : null,
  } : null;
  function clearActivePlanRef() {
    state.localPlanId = null; state.archiveRef = null; state.archiveConfigJson = null;
  }
  function rememberArchiveContext(context) {
    const ref = cloneArchiveRef(context);
    if (!ref) return;
    if (planStore.isServer()) { state.archiveRef = ref; state.archiveConfigJson = JSON.stringify(state.config); return; }
    state.archiveRef = ref;
    state.archiveConfigJson = JSON.stringify(state.config);
    const plans = loadPlans();
    let plan = state.localPlanId && plans.find(x => x.id === state.localPlanId);
    if (!plan) {
      const now = Date.now();
      plan = { id: "auto_" + now, name: get(state.config, "name") || tt("正式分析", "Formal run"),
               ts: now, config: JSON.parse(JSON.stringify(state.config)), auto_archive: true };
      plans.unshift(plan); state.localPlanId = plan.id;
    }
    plan.archive = ref;
    // Legacy-only. The auto-archive back-reference is a localStorage convenience;
    // under SQLite authority the archive lineage is the plan's own version
    // history, so there is nothing to write back and nothing may be written to
    // the legacy key.
    if (!planStore.isServer()) planStore._writeLegacy(plans);
  }
  async function savePlan() {
    const name = (get(state.config, "name") || tt("未命名计划", "Untitled plan"));
    const config = JSON.parse(JSON.stringify(state.config));
    const archive = state.archiveRef && state.archiveConfigJson === JSON.stringify(state.config)
      ? cloneArchiveRef(state.archiveRef) : null;
    const id = String(Date.now());
    const record = { id, name, ts: Date.now(), config };
    if (archive) record.archive = archive;
    // Under SQLite authority this is an /api/storage/plan write; before cutover
    // it is the legacy key. The button does not know which, which is the point.
    const ok = await planStore.save(record);
    if (!ok) return;
    if (!planStore.isServer()) {
      state.localPlanId = id;
      state.archiveRef = archive;
      state.archiveConfigJson = archive ? JSON.stringify(state.config) : null;
    }
    toast(tt(`已保存计划「${name}」`, `Saved plan "${name}"`));
    if (state.view === "welcome") { renderPlans(); renderRecoveredDrafts(); renderStanding(); }
  }
  function timelineLabel(event) {
    if (event.kind === "plan_version") return tt("输入版本", "Input version");
    if (event.kind === "run_snapshot") return tt("正式快照", "Run snapshot");
    if (event.status === "cancelled") return tt("已取消", "Cancelled");
    if (event.status === "running") return tt("进行中", "Running");
    return tt("运行失败", "Run failed");
  }
  async function showPlanTimeline(plan, row, button) {
    const old = row.nextElementSibling;
    if (old && old.classList.contains("plan-timeline")) {
      old.remove(); button.textContent = tt("时间线", "Timeline"); return;
    }
    button.disabled = true;
    try {
      const planId = plan.archive && plan.archive.plan_id;
      const response = await fetch("/api/timeline?plan_id=" + encodeURIComponent(planId), { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || tt("时间线不可用", "Timeline unavailable"));
      const box = document.createElement("div"); box.className = "plan-timeline";
      const events = payload.timeline || [];
      const lines = events.map(event => {
        const time = event.recorded_at ? new Date(event.recorded_at).toLocaleString() : "—";
        const ref = event.snapshot_id ? `snapshot ${String(event.snapshot_id).slice(-12)}`
          : event.plan_version_id ? `version ${String(event.plan_version_id).slice(-12)}` : "";
        const runMeta = event.precision ? `${event.precision} · seed ${event.seed}` : "";
        const meta = [runMeta, ref, event.error_code].filter(Boolean).join(" · ");
        return `<div class="tl-row"><span class="tl-time">${esc(time)}</span><span class="tl-kind">${timelineLabel(event)}</span><span>${esc(meta || tt("已记录", "Recorded"))}</span></div>`;
      }).join("");
      box.innerHTML = `<div class="tl-head">${tt("Plan 时间线（MVP：尚不含 Check-in / Decision）", "Plan timeline (MVP: no Check-ins / Decisions yet)")}</div>${lines || `<div>${tt("还没有事件", "No events yet")}</div>`}`;
      row.parentNode.insertBefore(box, row.nextSibling);
      button.textContent = tt("收起", "Hide");
    } catch (e) { toast(e.message, true); }
    finally { button.disabled = false; }
  }
  // ROADMAP's last Phase 2 deliverable: the home page answers four questions —
  // where you stand, why it changed, what it implies, and when to look again.
  //
  // All four come from the latest check-in's own attribution and memo rather
  // than from a second calculation. A home page that disagreed with the review
  // page would be worse than one that said nothing.
  // ROADMAP asks for an optional local reminder that does not depend on a
  // cloud service. This is that, and it is worth being exact about what it can
  // and cannot be, because "reminder" usually implies something this cannot do.
  //
  // There is no background process. The app has no daemon, no push channel and
  // makes no network requests at all, so nothing can reach the user while the
  // app is closed. What this does is remember the due date locally and say so
  // the next time the app is opened. The UI says that in as many words rather
  // than letting "提醒" imply a notification that will arrive on its own.
  //
  // The Notification API is used only if it exists AND permission was already
  // granted. It is never requested unprompted: a permission dialog on startup
  // for a feature the user did not ask for is how an app teaches people to
  // click Deny.
  const REMIND_KEY = "fire_guardrail_due";

  function rememberReviewDue(months) {
    if (!months) return;
    try {
      const due = new Date();
      due.setMonth(due.getMonth() + Number(months));
      const existing = localStorage.getItem(REMIND_KEY);
      // Never push an existing due date further out: a status that recomputes
      // on every visit would otherwise reset the clock every time and the
      // reminder would never come due.
      if (!existing || new Date(existing) > due) {
        localStorage.setItem(REMIND_KEY, due.toISOString().slice(0, 10));
      }
    } catch (e) { /* storage unavailable: the banner simply never shows */ }
  }

  function reviewDueBanner() {
    let due = null;
    try { due = localStorage.getItem(REMIND_KEY); } catch (e) { return ""; }
    if (!due) return "";
    const days = Math.round((new Date(due) - new Date()) / 86400000);
    if (days > 0) return "";
    try {
      if (typeof Notification !== "undefined"
          && Notification.permission === "granted") {
        new Notification(tt("该做年度复核了", "Time for your annual review"));
      }
    } catch (e) { /* not available in this webview; the banner still shows */ }
    return `<div class="g-light g-watch"><b>${tt("该做年度复核了",
             "Time for your annual review")}</b><span class="cap">${tt(
             `复核日期是 ${due}，已经到了。这个提醒只在你打开 App 时出现 —— 它不联网，也没有后台进程，所以不会自己找上门。`,
             `The review date was ${due} and it has passed. This reminder only appears when you open the app — it makes no network requests and runs no background process, so it cannot reach you on its own.`)}</span></div>`;
  }

  async function renderStanding() {
    const panel = $("standingPanel"), host = $("standingBody");
    if (!panel || !host) return;
    const plans = loadPlans();
    const withArchive = plans.filter(p => p.archive && p.archive.plan_id);
    if (!withArchive.length) { panel.classList.add("hidden"); return; }
    // The most recently saved archived plan is the one the user is working on.
    const plan = withArchive[0];
    let standing;
    try {
      standing = await revGet("/api/checkin/standing?plan_id="
                              + encodeURIComponent(plan.archive.plan_id));
    } catch (e) { panel.classList.add("hidden"); return; }
    // Phase 4's light, alongside Phase 2's four answers rather than instead of
    // them. It answers a different question — not "what happened" but "does
    // this cross a line you drew in advance" — and it is deliberately allowed
    // to say neither: with fewer than two comparable periods it reports
    // unknown rather than On Track, because a green light on no evidence is
    // the reassuring-zero mistake in its most consequential place.
    let guard = null;
    try {
      guard = await revGet("/api/guardrail/status?plan_id="
                           + encodeURIComponent(plan.archive.plan_id));
    } catch (e) { guard = null; }
    if (guard && guard.enough_history) rememberReviewDue(guard.next_review_months);
    const gStates = {
      on_track: [["稳住了", "On track"], "g-ok"],
      watch:    [["留意", "Watch"], "g-watch"],
      action:   [["该动了", "Action"], "g-act"],
    };
    const gLight = (() => {
      if (!guard) return "";
      if (!guard.enough_history) {
        return `<div class="g-light g-unknown"><b>${tt("还看不出来", "Not enough history")}</b>`
             + `<span class="cap">${tt("需要至少两期既有复核记录、又有当初预测可比。少于两期就说「稳住了」，是在没有证据的地方给绿灯。",
                                       "Needs at least two periods with both a check-in and the forecast it was made against. Saying On track with less than that is a green light on no evidence.")}</span></div>`;
      }
      const [label, cls] = gStates[guard.state] || gStates.on_track;
      const acting = (guard.acting || []).length;
      const snoozed = (guard.snoozed_but_firing || []).length;
      return `<div class="g-light ${cls}"><b>${tt(label[0], label[1])}</b>`
           + `<span class="cap">${tt(
               `按你事先划的线判定 · ${guard.measured_observations} 期可比 · 下次复核 ${guard.next_review_months} 个月后`,
               `Against the lines you drew in advance · ${guard.measured_observations} comparable periods · next review in ${guard.next_review_months} months`)}`
           + (acting ? tt(`　·　${acting} 条触发`, `　·　${acting} triggered`) : "")
           + (snoozed ? tt(`　·　${snoozed} 条已静音但仍在响`, `　·　${snoozed} snoozed but still firing`) : "")
           + `</span></div>`;
    })();

    panel.classList.remove("hidden");
    const name = esc(plan.name || tt("未命名计划", "Untitled plan"));

    if (!standing.has_review) {

      host.innerHTML = reviewDueBanner() + gLight + `<p>${tt(
        `<b>${name}</b> 还没有做过复核。跑一次 Standard 或 Official 精度的运行会把预测归档；一年后回来，在结果页的「年度复核」里填上真实发生的数字，就能看到实际与预测的差距分成市场、行为和模型更新。`,
        `<b>${name}</b> has not been reviewed yet. A run at Standard or Official precision archives its forecast; come back later, fill in what actually happened under \u201cAnnual review\u201d on the results page, and the gap between plan and reality is split into market, behaviour and model update.`)}</p>
        ${standing.reason ? `<p class="cap">${standing.reason}</p>` : ""}`;
      return;
    }

    const memo = standing.memo || {};
    const zh = L === "zh";
    const label = pair => pair[zh ? 0 : 1];
    const verdict = label(REV_VERDICT[memo.verdict] || [memo.verdict, memo.verdict]);
    const why = (memo.happened || []).slice(0, 3).map(h =>
      tt(`${label(REV_LABEL[h.kind] || [h.kind, h.kind])} 让组合比计划${h.effect === "left_you_lower" ? "低" : "高"} <span class="mono">${revMoney(Math.abs(h.value))}</span>`,
         `${label(REV_LABEL[h.kind] || [h.kind, h.kind])} left it ${h.effect === "left_you_lower" ? "lower" : "higher"} by <span class="mono">${revMoney(Math.abs(h.value))}</span>`)).join(" · ");

    host.innerHTML = reviewDueBanner() + gLight + `
      <div class="rev-grid">
        <div class="rev-field"><span>${tt("当前状态", "Where you stand")}</span>
          <div><b>${name}</b> — ${tt("最近复核到", "reviewed through")} ${String(standing.as_of || "").slice(0, 10)}</div></div>
        <div class="rev-field"><span>${tt("变化原因", "Why it changed")}</span>
          <div>${why || tt("没有任何一项偏离超过期初组合的 1%。", "Nothing deviated by more than 1% of the opening portfolio.")}</div></div>
        <div class="rev-field"><span>${tt("重要决定", "The decision")}</span>
          <div><b>${verdict}</b>${memo.lever ? " — " + label(REV_LEVER[memo.lever.lever] || [memo.lever.lever, memo.lever.lever]) : ""}</div></div>
        <div class="rev-field"><span>${tt("下次检查", "Next check")}</span>
          <div>${tt(`${memo.next_review_months} 个月后`, `in ${memo.next_review_months} months`)}</div></div>
      </div>
      ${(memo.gaps || []).length ? `<p class="cap">${tt(
        "这次复核有没测出来的部分，所以结论最多只能是「继续观察」：",
        "Part of this review could not be measured, so the conclusion can be no stronger than \u201ckeep watching\u201d:")} ${memo.gaps.map(revGapText).join(" ")}</p>` : ""}`;
  }

  function renderPlans() {
    const box = $("plansBox"), list = $("plansList");
    const plans = loadPlans();
    box.style.display = plans.length ? "" : "none";
    list.innerHTML = "";
    plans.forEach(pl => {
      const c = pl.config || {};
      const meta = `${new Date(pl.ts).toLocaleDateString()} · ${get(c, "state.start_age") || "?"}${tt("岁", "y")} · ${money(+get(c, "contributions.base_salary_pre") || 0)} · ${tt("支出", "spend ")}${money(+get(c, "state.expenses_y0") || 0)}`;
      const row = document.createElement("div");
      row.className = "plan-row";
      const timelineButton = pl.archive && pl.archive.plan_id
        ? `<button class="btn-ghost sm" data-a="timeline">${tt("时间线", "Timeline")}</button>` : "";
      row.innerHTML = `<input class="pn" value="${esc(pl.name || "")}">` +
        `<span class="pm">${meta}</span>` +
        `<button class="btn-ghost sm" data-a="open">${t("plans.open")}</button>` +
        `<button class="btn-ghost sm" data-a="dup">${t("plans.dup")}</button>` +
        `${timelineButton}<button class="btn-ghost sm" data-a="del">${t("plans.del")}</button>`;
      row.querySelector(".pn").addEventListener("change", async ev => {
        const next = ev.target.value;
        pl.name = next; if (pl.config) pl.config.name = next;
        if (await planStore.rename(pl, next)) renderPlans();
      });
      row.querySelector('[data-a="open"]').addEventListener("click", () => {
        state.config = normalizeConfig(pl.config);
        state.localPlanId = pl.id; state.archiveRef = cloneArchiveRef(pl.archive); state.archiveConfigJson = null;
        state.quick = false; state.step = 0; goto("wizard");
      });
      row.querySelector('[data-a="dup"]').addEventListener("click", async () => {
        if (await planStore.duplicate(pl)) renderPlans();
      });
      const timeline = row.querySelector('[data-a="timeline"]');
      if (timeline) timeline.addEventListener("click", () => showPlanTimeline(pl, row, timeline));
      row.querySelector('[data-a="del"]').addEventListener("click", async () => {
        if (!confirm(tt(`删除计划「${pl.name}」？`, `Delete plan "${pl.name}"?`))) return;
        if (await planStore.remove(pl)) renderPlans();
      });
      list.appendChild(row);
    });
  }

  // =========================================================== diagnostics
  // A close that is still winding down owns a pending timer + an animationend listener. Reopening
  // before either fires would let the old close land on the NEWLY opened sheet and hide it, so the
  // handles live on the modal element and every open disarms them first.
  function cancelLogClose(m) {
    if (m._closeT != null) { clearTimeout(m._closeT); m._closeT = null; }
    if (m._closeEnd) {
      const box = m.querySelector(".modal-box");
      if (box) box.removeEventListener("animationend", m._closeEnd);
      m._closeEnd = null;
    }
  }
  async function viewLogs() {
    try {
      const r = await (await fetch("/api/logs")).json();
      $("logBody").textContent = (r.lines || []).join("\n") || "(empty)";
      const m = $("logModal");
      cancelLogClose(m);
      m.classList.remove("hidden", "closing");
    } catch (e) { toast(e.message, true); }
  }
  // §5.5 Sheet exit: play the reverse-path close animation, then hide (Esc / scrim / ✕ share this path).
  function closeLog() {
    const m = $("logModal");
    if (m.classList.contains("hidden") || m.classList.contains("closing")) return;
    if (window.Motion && Motion.prefersReducedMotion()) { m.classList.add("hidden"); return; }
    const box = m.querySelector(".modal-box");
    const finish = () => { cancelLogClose(m); m.classList.remove("closing"); m.classList.add("hidden"); };
    m._closeEnd = e => { if (e.target === box) finish(); };
    if (box) box.addEventListener("animationend", m._closeEnd); else m._closeEnd = null;
    m._closeT = setTimeout(finish, 400); // fallback if animationend never fires (paused rAF, etc.)
    m.classList.add("closing");
  }
  async function copyDiag() {
    const pr = (state.data && state.data.meta.protocol) || {};
    const on = [];
    ["household.enabled", "layoff.enabled", "relocation.enabled", "roth_ladder.enabled",
     "income_streams.pension_enabled", "income_streams.rental_enabled",
     "income_streams.parttime_enabled", "income_streams.equity_enabled",
     "tax_us.progressive"].forEach(k => {
      if (get(state.config, k)) on.push(k.split(".")[0]);
    });
    const txt = [
      "FIRE Modeling diagnostics",
      `engine ${pr.engine || "v9.8-rc"} · ${pr.paths || "-"} paths · seed ${pr.seed || "-"} · mode ${pr.mode || "-"} · ${pr.elapsed_s || "-"}s`,
      `modules on: ${on.join(", ") || "none"}`,
      `ua: ${navigator.userAgent}`,
      `lang ${L} · theme ${document.documentElement.dataset.theme}`,
    ].join("\n");
    try { await navigator.clipboard.writeText(txt); toast(tt("已复制诊断信息", "Diagnostics copied")); }
    catch (e) { toast(txt.slice(0, 120), false); }
  }

  async function runRobustness() {
    const revision = state.revision;
    const btn = $("robustBtn"); btn.disabled = true;
    const out = $("robustOut");
    out.innerHTML = `<p class="cap">${tt("跑 3 个独立种子（各 2,000 路径，本土情景）…约 10 秒", "Running 3 independent seeds (2,000 paths each, home scenario)… ~10s")}</p>`;
    try {
      const r = await postJSON("/api/robustness", { config: state.config, paths: 2000, seed: state.seed || 96000 });
      const ps = r.points;
      const ls = ps.map(p2 => p2.lifetime_success);
      const spread = (Math.max(...ls) - Math.min(...ls)) * 100;
      const seOne = Math.sqrt(Math.max(ls[0] * (1 - ls[0]), 1e-12) / r.n_paths) * 100;
      const rows = ps.map(p2 => `<tr><td class="mono">${p2.seed}</td><td>${pct(p2.lifetime_success)}</td><td>${p2.fire_age_p50 != null ? Math.round(p2.fire_age_p50) : "—"}</td><td class="real">${money(p2.terminal_real_p50)}</td><td>${money(p2.cons_p50)}</td></tr>`).join("");
      out.innerHTML = `<table class="cmp-table" style="margin-top:14px"><thead><tr><th>seed</th><th>${tt("三分支成功率", "three-branch success")}</th><th>FIRE P50</th><th>${tt("终值 P50", "terminal P50")}</th><th>${tt("消费 P50", "spend P50")}</th></tr></thead><tbody>${rows}</tbody></table>
        <p class="cap">${tt(`三分支成功率跨种子极差 ${spread.toFixed(2)}pp（单次运行二项 SE ≈ ±${seOne.toFixed(2)}pp，2,000 路径）。若极差与 SE 同量级＝结论对随机性稳健；若远超，说明该配置处在敏感边缘，建议用更高精度确认。`, `Three-branch success range across seeds: ${spread.toFixed(2)}pp (single-run binomial SE ≈ ±${seOne.toFixed(2)}pp at 2,000 paths). Range ≈ SE ⇒ the conclusion is robust to randomness; range ≫ SE ⇒ the config sits on a sensitive edge — confirm at higher precision.`)}</p>`;
    } catch (e) { if (revision === state.revision) { out.innerHTML = ""; toast(e.message, true); } }
    finally { if (revision === state.revision) btn.disabled = false; }
  }

  // =========================================================== personas + quick estimate
  const PERSONAS = [
    { id: "tech", name: ["科技双职工 · RSU", "Tech couple · RSU"], spouseUsesPackLimits: true, patch: { name: "Tech couple · RSU", state: { start_age: 29, expenses_y0: 80000 }, contributions: { base_salary_pre: 165000, bonus_pre: 20000, ot_income_pre: 0, annual_spending_now: 90000 }, income_streams: { equity_enabled: true, equity_annual_real: 40000, equity_years: 4 }, household: { enabled: true, spouse_age_offset: -1, spouse_base_salary_pre: 140000, spouse_match_rate: 0.04, spouse_pia_monthly_y0: 1600, spouse_claim_age: 67 }, initial: { pretax_401k: 180000, roth_ira: 60000, hsa: 20000, taxable: 140000 }, milestones: [2000000, 5000000] } },
    { id: "lean", name: ["单身极简 Lean", "Single · lean"], patch: { name: "Single · lean FIRE", state: { start_age: 28, expenses_y0: 32000, swr_pref: 0.035 }, contributions: { base_salary_pre: 85000, bonus_pre: 0, ot_income_pre: 0, annual_spending_now: 30000, pretax_401k_limit_y1: 20000 }, initial: { pretax_401k: 30000, roth_ira: 15000, hsa: 5000, taxable: 15000 }, milestones: [500000, 1000000] } },
    { id: "barista", name: ["Barista 转型", "Barista FIRE"], patch: { name: "Barista FIRE", state: { start_age: 32, expenses_y0: 40000 }, contributions: { base_salary_pre: 95000, bonus_pre: 0, ot_income_pre: 0, annual_spending_now: 38000 }, income_streams: { parttime_enabled: true, parttime_annual_real: 22000, parttime_start_age: 42, parttime_years: 12 }, initial: { pretax_401k: 90000, roth_ira: 35000, hsa: 10000, taxable: 45000 } } },
    { id: "fed", name: ["联邦雇员 · 年金", "Federal · pension"], patch: { name: "Federal · pension", state: { start_age: 35, expenses_y0: 55000 }, contributions: { base_salary_pre: 110000, bonus_pre: 0, ot_income_pre: 0, annual_spending_now: 55000 }, income_streams: { pension_enabled: true, pension_annual_real: 30000, pension_start_age: 62, pension_cola: true }, initial: { pretax_401k: 150000, roth_ira: 40000, hsa: 15000, taxable: 60000 } } },
    { id: "landlord", name: ["房东 · 租金流", "Landlord · rentals"], patch: { name: "Landlord", state: { start_age: 38, expenses_y0: 60000 }, contributions: { base_salary_pre: 120000, annual_spending_now: 60000 }, income_streams: { rental_enabled: true, rental_annual_net_real: 18000, rental_start_age: 38, rental_end_age: 75 }, other_assets: { home_equity: 250000 }, initial: { pretax_401k: 200000, roth_ira: 50000, hsa: 15000, taxable: 90000 } } },
    { id: "late", name: ["40+ 晚起步", "Late starter 40+"], patch: { name: "Late starter", state: { start_age: 45, accum_years: 20, expenses_y0: 70000 }, contributions: { base_salary_pre: 160000, bonus_pre: 15000, annual_spending_now: 75000 }, initial: { pretax_401k: 180000, roth_ira: 30000, hsa: 10000, taxable: 60000 }, milestones: [1000000, 2000000] } },
    { id: "expat", name: ["回流海外", "Expat return"], patch: { name: "Expat return", state: { start_age: 30, expenses_y0: 50000, inflation_cn: 0.025 }, contributions: { base_salary_pre: 135000, annual_spending_now: 50000 }, relocation: { enabled: true, relocation_age: 45, col_ratio: 0.72, destination: "shanghai" }, ss_nra: { haircut_fraction: 0.20 }, china_healthcare: { cost_working_age_real: 2500, cost_senior_real: 1000 }, tax_cn: { withdrawal_tax_traditional: 0.089 } } },
    { id: "fat", name: ["FatFIRE 高收入", "FatFIRE"], patch: { name: "FatFIRE", state: { start_age: 34, expenses_y0: 120000, swr_pref: 0.0325 }, contributions: { base_salary_pre: 320000, bonus_pre: 60000, ot_income_pre: 0, annual_spending_now: 130000 }, initial: { pretax_401k: 350000, roth_ira: 80000, hsa: 25000, taxable: 250000 }, milestones: [3000000, 10000000] } },
  ];
  function renderPersonas() {
    $("personas").innerHTML = PERSONAS.map(pp => `<button class="persona-chip" data-p="${pp.id}">${pp.name[L === "zh" ? 0 : 1]}</button>`).join("");
    $("personas").querySelectorAll(".persona-chip").forEach(b => b.addEventListener("click", () => {
      const pp = PERSONAS.find(x => x.id === b.dataset.p);
      clearActivePlanRef(); state.config = normalizeConfig(pp.patch);
      if (pp.spouseUsesPackLimits) {
        const limits = (state.rulePackDefaults || {}).contribution_limits || {};
        state.config.household.spouse_pretax_401k_limit_y1 = +limits.pretax_401k_limit_y1 || 0;
        state.config.household.spouse_roth_ira_limit_y1 = +limits.roth_ira_limit_y1 || 0;
      }
      state.quick = false; state.step = 0;
      goto("wizard");
      toast(tt("已载入原型——过一遍向导确认数字", "Persona loaded — walk the wizard to confirm the numbers"));
    }));
  }
  function quickRun() {
    const age = +$("qAge").value || 30, income = +$("qIncome").value || 0;
    const spend = +$("qSpend").value || 0, port = +$("qPort").value || 0, ret = +$("qRet").value || 0;
    const spouseInc = +$("qSpouse").value || 0;
    if (income <= 0 || ret <= 0) { toast(tt("请把 5 个数字填完整", "Please fill all five numbers"), true); return; }
    // representative allocator: 401k -> roth -> hsa from the after-tax savings budget
    const limits = (state.rulePackDefaults || {}).contribution_limits || {};
    const preCap = +limits.pretax_401k_limit_y1 || 0;
    const rothCap = +limits.roth_ira_limit_y1 || 0;
    const hsaCap = +limits.hsa_limit_y1 || 0;
    const budget = Math.max(0, income * 0.76 - spend);
    const pre = Math.min(preCap, Math.round(budget));
    const roth = Math.min(rothCap, Math.round(Math.max(0, budget - pre)));
    const hsa = Math.min(hsaCap, Math.round(Math.max(0, budget - pre - roth)));
    clearActivePlanRef(); state.config = normalizeConfig({
      name: tt("速估", "Quick estimate"),
      state: { start_age: age, expenses_y0: ret },
      contributions: { base_salary_pre: income, bonus_pre: 0, ot_income_pre: 0,
                       annual_spending_now: spend, pretax_401k_limit_y1: pre,
                       roth_ira_limit_y1: roth, hsa_limit_y1: hsa },
      initial: { pretax_401k: Math.round(port * 0.5), roth_ira: Math.round(port * 0.15),
                 hsa: 0, taxable: Math.round(port * 0.35) },
      household: spouseInc > 0 ? {
        enabled: true, spouse_age_offset: 0,
        spouse_base_salary_pre: spouseInc, spouse_bonus_pre: 0,
        spouse_pretax_401k_limit_y1: Math.min(preCap, Math.round(spouseInc * 0.76 * 0.5)),
        spouse_roth_ira_limit_y1: Math.min(rothCap, Math.round(spouseInc * 0.05)),
        spouse_hsa_limit_y1: 0, spouse_match_rate: 0.04,
      } : { enabled: false },
      relocation: { enabled: false },   // quick mode: home-only for speed & clarity
    });
    state.quick = true; state.paths = 2000;
    runJob();
  }

  // =========================================================== init
  async function init() {
    applyI18n();
    const j = await (await fetch("/api/presets")).json();
    state.presets = j.presets;
    state.rulePack = j.rule_pack || null;
    state.rulePackDefaults = j.rule_pack_defaults || null;
    const firstKey = Object.keys(state.presets)[0];
    state.config = normalizeConfig(state.presets[firstKey].config);
    $("tb-engine").textContent = "v9.8";
    // Contract §6 startup ordering, and the order is the contract: read a fresh
    // two-key legacy digest, then ask the §6 state seam, and only then touch a
    // plan or a draft. The digest first because it is the evidence of what the
    // legacy source looks like *now* — asking the server first would report drift
    // against bytes we had not looked at yet. SQLite authority is never inferred
    // from imported rows existing; it is only ever what the server says.
    const startup = await readFreshLegacyDigest();
    await refreshStorageAuthority(startup.digest);
    // The expensive migration-authority seam is still consulted, because the
    // page fence lives there, but it no longer decides whether the legacy
    // writers are open — refreshStorageAuthority already did.
    await legacyStore.refreshAuthority();
    await planStore.refresh();
    await recoveredStore.refresh();
    // Read the side-store before deciding whether to offer Resume: this is the
    // whole of ruling row 3 — after a restart the draft comes from disk, not
    // from a variable that a restart just cleared.
    await refreshWorkingDraft();
    if (workingDraftIsServerSide() ? Boolean(_workingDraft) : legacyStore.hasDraft()) {
      $("resumeDraft").style.display = "";
    }

    renderPersonas();
    renderPlans();
    renderRecoveredDrafts();
    renderStorageBanner();
    await offerCutover();
    const migrateButton = document.getElementById("migrateBtn");
    if (migrateButton) migrateButton.addEventListener("click", runCutoverFromUi);
    $("helpBtn").addEventListener("click", () => { state._helpFrom = state.view; goto("help"); });
    $("helpBack").addEventListener("click", () => goto(state._helpFrom || "welcome"));
    $("wizSavePlan").addEventListener("click", savePlan);
    $("resSavePlan").addEventListener("click", savePlan);
    $("viewLogs").addEventListener("click", viewLogs);
    $("logClose").addEventListener("click", closeLog);
    $("logModal").addEventListener("click", e => { if (e.target === $("logModal")) closeLog(); });
    $("copyDiag").addEventListener("click", copyDiag);
    $("robustBtn").addEventListener("click", runRobustness);
    $("quickGo").addEventListener("click", quickRun);
    $("impCfgWelcome").addEventListener("click", () => $("impFileWelcome").click());
    $("impFileWelcome").addEventListener("change", ev => importConfig(ev.target.files[0]));
    document.querySelectorAll("#langToggle button").forEach(b => b.addEventListener("click", () => setLang(b.dataset.lang)));
    $("restartBtn").addEventListener("click", () => goto("welcome"));
    $("startFresh").addEventListener("click", () => { clearActivePlanRef(); state.quick = false; state.step = 0; goto("wizard"); });
    $("startExample").addEventListener("click", () => { clearActivePlanRef(); state.paths = 10000; runJob(); });
    $("resumeDraft").addEventListener("click", () => { clearActivePlanRef(); const d = loadDraft(); if (d) state.config = normalizeConfig(d); state.step = 0; goto("wizard"); });
    $("wizPrev").addEventListener("click", () => { if (state.step > 0) { state.step--; buildStep(); buildRail(); updateStepsMini(); } });
    $("wizNext").addEventListener("click", () => { if (!validateStep()) return; saveDraft(true); if (state.step < STEPS.length - 1) { state.step++; buildStep(); buildRail(); updateStepsMini(); } else goto("precision"); });
    $("wizSave").addEventListener("click", () => saveDraft(false));
    $("precPrev").addEventListener("click", () => { state.step = STEPS.length - 1; goto("wizard"); });
    $("seedInput").addEventListener("change", () => { state.seed = Math.max(1, Math.round(+$("seedInput").value || 96000)); });
    $("precRun").addEventListener("click", () => { if (!validateAllSteps()) return; runJob(); });
    $("editParams").addEventListener("click", () => { state.step = 0; goto("wizard"); });
    $("editParams2").addEventListener("click", () => { state.step = 0; goto("wizard"); });
    bindTabs("fanUnit", u => { state.fanUnit = u; renderFan(); });
    bindTabs("termUnit", u => { state.termUnit = u; renderTerm(); });
    $("fanCursor").addEventListener("input", updateFanReadout);
    $("cmpCursor").addEventListener("input", () => { if (state.data && state.data.relocation) updateCmpReadout(); });
    ["kMu", "kSd", "kSav", "kSwr"].forEach(id => $(id).addEventListener("input", syncK));
    $("kRun").addEventListener("click", runFwd); $("kReset").addEventListener("click", resetFwd);
    $("sensRun").addEventListener("click", runSens); $("swrRun").addEventListener("click", runSwr);
    $("claimRun").addEventListener("click", runClaim); $("btRun").addEventListener("click", runBt);
    if ($("beqRun")) $("beqRun").addEventListener("click", runBequestCheck);
    if ($("alocRun")) $("alocRun").addEventListener("click", runAssetLocation);
    if ($("rschRun")) $("rschRun").addEventListener("click", runRothSchedule);
    if ($("frRun")) $("frRun").addEventListener("click", runFundedRatio); $("rothRun").addEventListener("click", runRoth); $("stratRun").addEventListener("click", runStrategies);
    $("fanDrillBtn").addEventListener("click", runFanDrill); $("termChart").addEventListener("click", termClickToBucket); $("termChart").addEventListener("keydown", termKeyToBucket);
    $("ciAdd").addEventListener("click", ciAdd);
    const dlb = $("dlBriefing");
    if (dlb) dlb.addEventListener("click", exportBriefing);
    $("dlReport").addEventListener("click", openReport); $("dlJson").addEventListener("click", downloadJson);
    $("cancelRun").addEventListener("click", cancelJob);
    $("printBtn").addEventListener("click", () => window.print());
    $("themeToggle").addEventListener("click", () => {
      const t = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
      const root = document.documentElement;
      // §14: dark↔light is a brightness jump — cross-fade the flip instead of flashing it.
      // The easing class arms broad color transitions for just this moment, then disarms.
      root.classList.add("theme-easing");
      clearTimeout(root._themeT);
      root._themeT = setTimeout(() => root.classList.remove("theme-easing"), 380);
      root.dataset.theme = t;
      localStorage.setItem("fire_theme", t);
      if (state.view === "results") { showPage(state.page); if (state.page === "trajectory" && state._fwdInit) runFwd(); }
    });
    $("saveA").addEventListener("click", () => saveSlot("A"));
    $("saveB").addEventListener("click", () => saveSlot("B"));
    $("loadA").addEventListener("click", () => { if (state.slots.A) { clearActivePlanRef(); state.config = normalizeConfig(state.slots.A.config); state.step = 0; goto("wizard"); } });
    $("loadB").addEventListener("click", () => { if (state.slots.B) { clearActivePlanRef(); state.config = normalizeConfig(state.slots.B.config); state.step = 0; goto("wizard"); } });
    $("fireSolveBtn").addEventListener("click", () => { const v = +$("fireTarget").value; if (v) solveFire(v); });
    $("fireTarget").addEventListener("keydown", e => { if (e.key === "Enter") { e.stopPropagation(); const v = +$("fireTarget").value; if (v) solveFire(v); } });
    $("quitBtn").addEventListener("click", quitApp);

    document.addEventListener("keydown", e => {
      if (e.key === "Enter" && state.view === "wizard" && e.target.tagName === "INPUT" && e.target.type !== "checkbox") { e.preventDefault(); $("wizNext").click(); }
      if (e.key === "Escape" && state.view === "computing") cancelJob();
      if (e.key === "Escape") closeLog();
    });

    // Keep help bubbles inside the viewport. The bubble is CSS-centered on its tiny ? icon, so an
    // icon near a window edge pushes it off-screen. Just before hover/focus reveals it, measure
    // (transform-independent, via offsetWidth + the icon's center) and clamp the horizontal shift
    // into --hx; the CSS folds --hx into the box transform and counter-shifts the arrow so it keeps
    // pointing at the icon. Delegated + capture so it also covers dynamically built wizard fields.
    const positionHelp = icon => {
      const pop = icon.querySelector(".help-pop"); if (!pop) return;
      const ir = icon.getBoundingClientRect(), center = ir.left + ir.width / 2, w = pop.offsetWidth, M = 8;
      let dx = 0;
      if (center - w / 2 < M) dx = M - (center - w / 2);
      else if (center + w / 2 > window.innerWidth - M) dx = (window.innerWidth - M) - (center + w / 2);
      pop.style.setProperty("--hx", dx.toFixed(1) + "px");
    };
    const helpFrom = e => { const i = e.target.closest && e.target.closest(".help-i"); if (i) positionHelp(i); };
    document.addEventListener("pointerover", helpFrom, true);
    document.addEventListener("focusin", helpFrom, true);

    // Phase B: chrome scroll-edge effect — fade in the separator only once content scrolls under the bar.
    window.addEventListener("scroll", updateChromeScroll, { passive: true });
    updateChromeScroll();
    // Publish the real topbar height so the results tab bar can stick flush beneath it at any
    // width (it wraps on narrow viewports). Re-measure on resize.
    measureChrome();
    window.addEventListener("resize", measureChrome);

    initSegmented();

    // C4: paint the filled portion of every range slider (delegated for live drag + initial)
    document.addEventListener("input", e => { if (e.target && e.target.type === "range") paintSlider(e.target); }, true);
    paintAllSliders();

    goto("welcome");
  }
  function bindTabs(id, cb) {
    document.querySelectorAll(`#${id} button`).forEach(b => b.addEventListener("click", () => {
      document.querySelectorAll(`#${id} button`).forEach(x => x.setAttribute("aria-pressed", "false"));
      b.setAttribute("aria-pressed", "true"); cb(b.dataset.unit);
    }));
  }

  // C2: iOS-style segmented controls — a sliding thumb (Motion SNAPPY spring) rides
  // behind the active segment. Generic over the four groups: aria-pressed toggles
  // (lang/tabs) and .active class (rtabs, whose innerHTML is regenerated per switch).
  const segGroups = []; // reposition callbacks, invoked on page/view transitions
  function initSegmented() {
    if (!window.Motion) return; // graceful: CSS pill still renders, just no thumb
    const ariaOn = b => b.getAttribute("aria-pressed") === "true";
    const classOn = b => b.classList.contains("active");
    // showHook = re-place on page transitions (lives inside an rpage that toggles visibility)
    [
      ["langToggle", "button", ariaOn, false],
      ["fanUnit", "button", ariaOn, true],
      ["termUnit", "button", ariaOn, true],
      ["storyTabs", "button", ariaOn, true],
      ["resultTabs", ".rtab", classOn, false],
      ["wizardRail", ".rail-step", classOn, true],   // vertical thumb (2D spring)
    ].forEach(([id, sel, isActive, showHook]) => { const el = $(id); if (el) setupSegGroup(el, sel, isActive, showHook); });
  }
  // Re-place thumbs when a container may have just become visible (ResizeObserver is
  // unreliable for visibility via an ancestor's display toggle), e.g. after showPage().
  function repositionSegments() { segGroups.forEach(fn => fn()); }

  // C4: set --fill% so the CSS gradient fills the slider up to the current value.
  function paintSlider(el) {
    const lo = parseFloat(el.min), hi = parseFloat(el.max), v = parseFloat(el.value);
    const min = isNaN(lo) ? 0 : lo, max = isNaN(hi) ? 100 : hi;
    const pct = max > min ? Math.max(0, Math.min(100, ((v - min) / (max - min)) * 100)) : 0;
    el.style.setProperty("--fill", pct + "%");
  }
  function paintAllSliders(root) { (root || document).querySelectorAll('input[type=range]').forEach(paintSlider); }

  // Chrome scroll state + Apple's large-title collapse: once the page's big title
  // scrolls under the bar, echo it compactly in the chrome (and drop it again on scroll-up).
  function measureChrome() {
    const tb = document.querySelector(".topbar");
    if (tb) document.documentElement.style.setProperty("--topbar-h", tb.offsetHeight + "px");
  }
  function updateChromeScroll() {
    const y = window.scrollY || document.documentElement.scrollTop || 0;
    const tb = document.querySelector(".topbar");
    if (tb) tb.classList.toggle("scrolled", y > 2);
    const rt = document.querySelector(".results-tabbar");
    if (rt) rt.classList.toggle("scrolled", rt.offsetParent !== null && rt.getBoundingClientRect().top <= 41);
    if (!tb) return;
    const slot = $("topbarTitle");
    if (!slot) return;
    let big = document.querySelector(".rpage.show .sec-title");
    if (!big || big.offsetParent === null) {
      big = null;
      document.querySelectorAll(".view.show .sec-title").forEach(t => { if (!big && t.offsetParent !== null) big = t; });
    }
    let show = false;
    if (big) {
      show = big.getBoundingClientRect().bottom < tb.getBoundingClientRect().bottom + 4;
      if (show && slot.textContent !== big.textContent) slot.textContent = big.textContent;
    }
    tb.classList.toggle("title-shown", show);
    // §3.6 two-way cross: only the ECHOED title fades (a page can hold several .sec-title,
    // and the ones still fully in view must stay at full strength)
    document.querySelectorAll(".sec-title.echoed").forEach(t => { if (t !== big || !show) t.classList.remove("echoed"); });
    if (big && show) big.classList.add("echoed");
  }

  // State-driven edge fades for horizontal scrollers (.rtabs / .wizard-rail): fade ONLY the
  // side that actually hides content. An always-on mask ate the tail of whatever sat flush
  // at the edge (the KPI chip's last digits) even when nothing was scrolled away.
  function edgeFade(el) {
    if (!el) return;
    const update = () => {
      const can = el.scrollWidth - el.clientWidth > 1;
      el.classList.toggle("fade-l", can && el.scrollLeft > 2);
      el.classList.toggle("fade-r", can && el.scrollLeft + el.clientWidth < el.scrollWidth - 2);
    };
    if (!el._edgeFadeWired) {
      el._edgeFadeWired = true;
      el.addEventListener("scroll", update, { passive: true });
      window.addEventListener("resize", update);
    }
    update();
  }

  function setupSegGroup(container, segSel, isActive, showHook) {
    // 2D thumb: X + Y springs. Horizontal pills have a constant Y (Y spring never moves);
    // the vertical wizard rail moves in Y. Same controller serves both.
    const sx = Motion.spring("SNAPPY"), sy = Motion.spring("SNAPPY");
    let thumb = null, placed = false, pending = false, pendAnim = true;
    const paint = () => { if (thumb) thumb.style.transform = `translate(${sx.value()}px,${sy.value()}px)`; };
    sx.onFrame(paint); sy.onFrame(paint);

    function ensureThumb() {
      thumb = container.querySelector(":scope > .seg-thumb");
      if (!thumb) {
        thumb = document.createElement("div");
        thumb.className = "seg-thumb";
        thumb.style.transform = `translate(${sx.value()}px,${sy.value()}px)`; // seed to avoid a 0,0 flash on re-create
        container.insertBefore(thumb, container.firstChild);
      }
    }
    function activeSeg() {
      const list = container.querySelectorAll(segSel);
      for (let i = 0; i < list.length; i++) if (isActive(list[i])) return list[i];
      return null;
    }
    function reposition(animate) {
      ensureThumb();
      const a = activeSeg();
      if (!a || !a.offsetWidth) { thumb.style.opacity = "0"; return; }
      thumb.style.opacity = "1";
      thumb.style.width = a.offsetWidth + "px";
      thumb.style.height = a.offsetHeight + "px";
      if (animate && placed) { sx.to(a.offsetLeft); sy.to(a.offsetTop); }
      else { sx.set(a.offsetLeft); sy.set(a.offsetTop); }
      placed = true;
    }
    function schedule(animate) {
      pendAnim = pending ? (pendAnim && animate) : animate;
      if (pending) return;
      pending = true;
      Promise.resolve().then(() => { pending = false; const an = pendAnim; pendAnim = true; reposition(an); });
    }
    new MutationObserver(() => schedule(true)).observe(container, {
      childList: true, subtree: true, attributes: true,
      attributeFilter: ["aria-pressed", "class", "aria-selected"],
    });
    if (window.ResizeObserver) new ResizeObserver(() => schedule(false)).observe(container);
    if (showHook) segGroups.push(() => reposition(false));
    reposition(false);
  }
  window.addEventListener("DOMContentLoaded", init);
})();
