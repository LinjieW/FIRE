# FIRE Modeling

An offline, self-contained macOS app for exploring **Financial Independence /
Retire Early (FIRE)** plans with Monte Carlo analysis.

This repository is a **public release snapshot** for downloading the app and
inspecting the runtime source. It intentionally contains the files needed for
the application and its build smoke checks, not the private development
history, workstream logs, prompts, or internal audit archive.

## Download

The current public candidate is:

`v14.0-public-candidate-2`

Download the universal2 bundle from the [GitHub Release](https://github.com/LinjieW/FIRE/releases/tag/v14.0-public-candidate-2):

`FIRE-Modeling-14.0-macOS-universal2.zip` (47 MB compressed, ~166 MB unpacked)

The bundle runs on Apple Silicon and Intel Macs. It includes its own Python,
NumPy, and web UI; no separate Python installation or account is required.

### Verify the download

After downloading, verify the SHA-256:

```bash
shasum -a 256 FIRE-Modeling-14.0-macOS-universal2.zip
```

It should print:

```
2d2ac3783d882c9a8df8ee5f4247265834025da00737a69c2f4e460ad3e8fdce
```

`SHA256SUMS.txt` on the same release covers the app zip and the source archive.

### First launch

The app is ad-hoc signed for local sharing. On first launch after downloading
or AirDropping it, use **right-click → Open → Open** if macOS Gatekeeper asks
for confirmation. Then double-clicking works normally.

## Highlights

- **Offline by default.** The UI talks to the bundled server over the Mac's
  loopback interface. The app does not require an account or an external API.
- **Universal2 desktop bundle.** One download supports Apple Silicon and Intel.
- **Bilingual interface.** Switch between Chinese and English in the app.
- **Quick-to-deep workflow.** Start with a quick estimate or guided setup, then
  rerun the full model, save the configuration, and continue refining it.
- **Monte Carlo planning.** Explore FIRE timing, sustainable spending,
  success probabilities, portfolio paths, and milestone distributions.
- **Household-aware scenarios.** Optionally model a second earner, survivor
  rules, and member-attributed pension, rental, part-time, and equity cash flows.
- **Goal-seeking and efficient frontiers.** Search expense/SWR combinations for
  a target success rate and see which trade-offs are nondominated.
- **Stress and comparison lab.** Run sensitivity tornadoes, SWR and Social
  Security claiming-age scans, Roth-conversion comparisons, strategy
  comparisons, rent-versus-buy scenarios, and stylized sequence-risk backtests.
- **Decision tools.** Explore withdrawal strategies, housing choices,
  relocation assumptions, and interactive what-if experiments before applying
  a change to the saved plan.
- **Local continuity.** Plans, versions, drafts, run snapshots, and timeline
  information stay on the local machine. No cloud sync is provided.
- **Local data helpers.** Optional broker-CSV and SSA XML import paths keep
  source data on the machine while feeding the scenario model.
- **Transparent assumptions.** The UI includes limitations and provenance for
  tax, healthcare, Social Security, housing, and return assumptions.
- **Honest tails, switched off by default.** Several modules exist to puncture
  optimistic defaults, and every one of them ships OFF so an existing plan
  reproduces exactly: lumpy spending that does not arrive on a smooth line, a
  Social Security trust-fund depletion path drawn from the Trustees Report's
  own alternatives, a stochastic house price with sale discount and downsizing,
  a career with permanent and transitory wage shocks, and a dial for how much
  of a triggered guardrail cut you would actually make.
- **A written account of what moves together.** Nineteen sampling modules are
  listed with their stance: three model a correlation, four are deliberately
  independent, and twelve are independent only in the model -- whether they
  should be correlated has not been examined, and the app says so rather than
  implying a finding.
- **Formal decision studies.** Compare a decision against adverse assumption
  packs across seeds and return models, and get a packet that states the
  precision tier it can carry rather than a single confident answer.
- **Your data outlives the app.** `tools/recover_without_app.py` reads the
  archive with nothing but the Python standard library, so a plan can be
  recovered without this application existing at all.
- **The zero-request promise is checkable.** `tools/verify_zero_requests.py`
  watches the installed bundle's sockets from outside the process and reports
  every address it held.
- **A help page you can browse.** Every field explanation is reachable by topic
  rather than only by hovering the control it belongs to.
- **The working years are not a smooth curve.** Promotions (up to two per
  earner), bonus and 1099 profit drawn as ranges rather than constants, planned
  career breaks, layoffs, human-capital shocks and long-term disability all
  reach the contribution waterfall itself, so a bad year changes what was
  actually saved rather than being applied to the total afterwards.
- **Equity compensation as earned income.** RSU vesting, optionally keeping
  shares as single-stock concentration, and a section 423 ESPP with both the
  sell-immediately and hold-to-qualifying-disposition paths — taxed as what
  they are rather than as untaxed cash.
- **Accounts with their real shapes.** Governmental 457(b) in its own bucket,
  SIMPLE and 403(b) limits, the 403(b) 15-year catch-up, the section 415(c)
  annual-additions cap, HSA contributions that require their HDHP eligibility
  facts before they count, and traditional defined-benefit pension accrual.
- **A household workspace.** Link plans between people, compare them, and keep
  an immutable decision archive of what was decided and when.
- **The engine's own forecasts, checked against history.** A calibration
  backtest replays the engine's predicted distribution against the historical
  record and reports where that ruler does and does not apply — including
  saying "not applicable" rather than zero when it cannot measure something.
- **A plan can start from a retirement that already happened.** Off by default.
  Turned on, it takes your actual retirement date, balances and spending, and
  the same engine runs the retirement from today instead of from a predicted
  FIRE year. A configuration that says it is already retired without giving
  its balances is refused by name rather than quietly filled in from a sample.
- **An execution view for the year you are in.** How much you can draw, which
  buckets it comes from in the account schema's own order, which guardrail band
  you are in and how far the trigger is, this year's tax tier with the distance
  to the next IRMAA threshold, and whether a required distribution is due. Every
  number is a receipt from the engine: the page does no arithmetic of its own,
  so it cannot tell a different story from the model it is reporting.
- **An annual review that leaves something behind.** A full-screen agenda read
  in a fixed order, a larger-type view for going through it with a partner, a
  letter to next year's self, and a calendar `.ics` file. There are no
  notifications, no cloud and no background process — what persists is an
  immutable local record and one all-day event you put in your own calendar.
- **A ten-year rehearsal that scores discipline, not timing.** Live through a
  bear-market opening, an inflationary decade or a long bull one year at a
  time, pausing each year to follow your rule, hold spending anyway, or sell in
  a panic. The score reads your adherence and your spending floor and never the
  ending balance, so a lucky run cannot beat a disciplined one. Nothing is
  saved: closing it forgets the whole rehearsal.
- **Disability, long-term care and mortality on one chain.** Off by default.
  Turned on, the three read their own columns of a single stream of draws per
  path, so switching one of them off does not move another's results. Each
  transition keeps its own sourced table rather than being melted into a new
  one, and death absorbs — it stops later onsets and truncates care already
  under way.
- **A second country's account rules, as data rather than code.** Canada's
  non-registered, RRSP, RRIF and TFSA shapes ship as a rule pack in which every
  number carries an official source and a vintage, including the half-inclusion
  of capital gains and the ordinary RRIF minimum-withdrawal table. Choosing
  RRIF at 71 gives a runnable year-by-year account worksheet; cash-out and
  annuity are disclosed as legal choices that are not modelled, and provincial
  tax, CPP and OAS are outside the pack. The engine holds no Canadian branch,
  and the US path is bit-identical to a build without any of this.
- **The page stops moving while you fill it in.** Each wizard section asks its
  switches first and opens the rest once you say that part is answered, so
  fields stop appearing and disappearing underneath you as you type.
- **Results you can read.** Headline numbers lead each result page, the
  question navigation points at the page that answers it, saving says whether
  it saved, and the pages hold up in Chinese and English, light and dark, and
  on a narrow window.
- **A feedback pack you carry yourself.** Tick the sections you want, preview
  exactly what they contain, and only then export a Markdown and JSON pair.
  The app sends nothing anywhere: amounts are left out unless you tick them,
  the files state plainly that they are not de-identified, and an empty
  selection is refused rather than quietly exporting everything.
- **Milestones counted along the life you actually live.** A milestone age is
  the first crossing on the path the plan describes -- accumulation up to
  retirement, then the retirement portfolio -- so the overview and the
  distribution page answer the same question instead of two different ones.
- **Controls you can drive from the keyboard, on pages that hold together.**
  Dropdown menus browse with the arrow keys without committing anything, Enter
  or Space picks, and Escape puts the previous value back; a field keeps its
  focus when the page re-renders around it. Dialogs open in 200 milliseconds
  and stay sharp throughout, and on a narrow window tables fold into labelled
  groups instead of scrolling sideways.

## What this release is (and is not)

This is a **public candidate**, not a promise of universal equivalence, tax
advice, investment advice, or a GA/enterprise release. It is the frozen
universal2 candidate built from the private `main`, eleven development versions
on from the v3.0 snapshot and four on from v10.0.

Every module added since then that changes what a plan predicts ships **off by
default**, so a plan saved under an earlier build reproduces its numbers after
upgrading. What those modules add is the ability to ask harder questions --
lumpy spending, a Social Security trust-fund cut, a house price that is not a
smooth line, a career that does not grow at a constant rate -- not a quiet
change to the answers you already have.

The model remains an approximation. Tax, ACA/IRMAA, Social Security, mortality,
housing, China/US relocation, and return assumptions have explicit limits in
the app. Results are scenario analysis, not a forecast or recommendation.

## Source snapshot

The public source contains the runtime and build inputs used by the candidate:

- `engine/` — lifecycle, tax, returns, rule-pack, and housing model code;
- `server/` — local HTTP app, persistence, migration, recovery, and report
  adapters;
- `web/` — the bundled browser UI;
- `build-app.sh`, the dependency lock, PyInstaller entry point, and the
  identity/build helpers;
- a small set of regression, JavaScript, frozen-bundle, and UI smoke checks;
- `tools/recover_without_app.py` and `tools/verify_zero_requests.py`, so the two
  promises above can be checked rather than taken on faith.

The public file set is computed from the built candidate's runtime manifest --
the exact files the application loads, ninety-nine of them in this candidate --
rather than from a hand-maintained list, plus a small set of regression,
JavaScript, frozen-bundle and UI smoke checks and the helper they share. Internal development history and operational documents (workstream
logs, handoffs, roadmaps, audits, prompts) remain outside this public
repository.

## Building from source (maintainer-oriented)

On a compatible macOS build host with the pinned universal2 toolchain and the
locally merged universal2 NumPy wheel available:

```bash
BUILD_ONLY=1 ./build-app.sh
```

The script validates the JavaScript, regression, frozen-bundle, signing, and
universal2 gates and leaves a candidate under `.build/`. It does not install or
replace an existing app.

## Local data and privacy

Plans and imported data are intended to remain local to the machine. The app
does not upload broker CSV contents, Social Security statement data, plans, or
run results. Do not put sensitive personal data into a public issue or pull
request.

## License

MIT. See [LICENSE](LICENSE).

## 中文说明

FIRE Modeling 是一个离线运行的 macOS 桌面应用，用蒙特卡洛分析帮助你探索
**财务独立 / 提前退休（FIRE）** 方案。

本仓库是一个**公开发布快照**：包含下载 App、查看运行时代码和执行构建烟测所需的
文件，不包含私有开发历史、工作流日志、提示词或内部审计资料。

### 下载

当前公开候选版本是 `v14.0-public-candidate-2`。请从
[GitHub Release](https://github.com/LinjieW/FIRE/releases/tag/v14.0-public-candidate-2)
下载通用 universal2 App：

`FIRE-Modeling-14.0-macOS-universal2.zip`（压缩后 47 MB，解压后约 166 MB）

同一个包支持 Apple Silicon 和 Intel Mac；App 已内置 Python、NumPy 和网页界面，
不需要另装 Python，也不需要账号。

### 校验下载

下载后请自行核对 SHA-256：

```bash
shasum -a 256 FIRE-Modeling-14.0-macOS-universal2.zip
```

应当输出：

```
2d2ac3783d882c9a8df8ee5f4247265834025da00737a69c2f4e460ad3e8fdce
```

同一个 Release 里的 `SHA256SUMS.txt` 同时覆盖 App 压缩包与源码归档。

### 首次启动

App 使用 ad-hoc 签名，适合小规模本地分享，并未使用 Developer ID 签名或 notarization。
如果 macOS 首次阻止打开，请右键 App，选择“打开”，再确认一次。

### 功能亮点

- **默认离线运行。** 界面通过 Mac 本机 loopback 与内置 server 通信，不需要账号或外部 API。
- **通用桌面 App。** 一个下载包同时支持 Apple Silicon 和 Intel。
- **中英双语界面。** 可以在 App 内切换中文和 English。
- **从速估到完整分析。** 可以先快速估算或使用向导，再运行完整模型、保存配置并继续迭代。
- **蒙特卡洛生命周期分析。** 查看 FIRE 时间、可持续支出、成功概率、投资组合路径和里程碑分布。
- **家庭场景建模。** 可选第二位收入者、幸存者规则，以及按成员归属的养老金、租金、兼职和股权现金流。
- **目标求解与效率前沿。** 针对目标成功率扫描“开销 × SWR”组合，查看不可被同时改进的权衡点。
- **压力测试与方案比较。** 支持敏感性 tornado、SWR 扫描、社保领取年龄扫描、Roth 转换比较、策略比较、租买对比和风格化序列风险回测。
- **交互式 what-if 实验。** 先调整杠杆观察影响，再选择是否应用到保存的计划。
- **本地连续性。** 计划、版本、草稿、运行快照和时间线保存在本机，不提供云同步。
- **本地数据导入。** 可选的券商 CSV 和 SSA XML 导入路径不会把源数据上传到云端。
- **假设透明。** 界面提供税务、医疗、社保、住房和收益率假设的限制与来源说明。
- **诚实的尾部，且默认全部关闭。** 有几个模块专门用来戳破乐观的默认值，
  而它们**一律默认关闭**，所以已有的计划升级后逐位复现：块状支出（真实支出不是一条平线）、
  社保信托基金枯竭（用 Trustees Report 自己的三套方案）、随机房价与卖房折价/换小房、
  带持久与暂时冲击的职业路径，以及「护栏触发后你实际砍得下去多少」这个拨盘。
- **把「什么和什么一起动」写成账。** 19 个随机模块逐条列出立场：3 条建模了相关性、
  4 条刻意独立、**12 条只是「模型里独立」—— 是否本该相关，本项目没有检验过**，
  App 直说这一点，而不是把它当成一个结论。
- **正式决策研究。** 拿一个决定去对多套不利假设、多个随机种子与收益模型做对比，
  产出的 packet 会声明它能承载到哪一档精度，而不是给你一个自信的单一答案。
- **你的数据比这个 App 活得久。** `tools/recover_without_app.py` 只用 Python 标准库
  读取档案，**不需要这个应用还存在**就能把计划取回来。
- **「零请求」承诺可以自己验。** `tools/verify_zero_requests.py` 从进程外部观察
  已安装 bundle 的套接字，列出它握过的每一个地址。
- **可浏览的帮助页。** 每个字段的说明都能按主题找到，而不是只能悬停在对应控件上看。
- **工作的那些年不是一条平滑曲线。** 晋升（每人最多两次）、按区间抽样而非常数的奖金与
  1099 净利润、计划内的职业间隔、失业、人力资本冲击、长期伤残 —— 这些都直接进入**缴款
  瀑布本身**，所以糟糕的一年改变的是那一年**实际存下了多少**，而不是事后在总额上打个折。
- **股权薪酬按劳动收入处理。** RSU 归属、可选地把股票留着形成单只股票集中度，以及
  §423 ESPP 的两条处置路径（购入即卖 / 持有到合格处置）—— 按它们**本来的性质**计税，
  而不是当作不上税的现金。
- **账户有它们真实的形状。** 政府 457(b) 独立成桶、SIMPLE 与 403(b) 的专属限额、
  403(b) 的 15 年补缴、§415(c) 年度合计上限、**必须先给出 HDHP 资格事实才算数**的 HSA 缴款，
  以及传统 DB 养老金的服务年限公式。
- **家庭工作台。** 在人与人之间关联计划、相互比较，并保留一份不可变的决策档案，
  记录当时决定了什么、什么时候决定的。
- **引擎自己的预测，拿历史校过。** 校准回测把引擎预测的分布放回历史记录里比对，
  并说明这把尺子在哪里适用、在哪里不适用 —— 量不了的时候它写「不适用」，而不是写 0。
- **计划可以从「已经退休了」开始。** 默认关闭。打开后它接受你实际的退休日期、实际余额与
  实际支出，同一个引擎按「从今天起的退休期」跑，而不是从预测出来的 FIRE 年起跑。
  一份说自己已经退休、却没给余额的配置会被**点名拒绝**，而不是拿一份样例余额悄悄补上。
- **面向「今年」的执行视图。** 今年可以取多少、按账户 schema 自己的顺序取自哪些桶、
  guardrail 现在处在哪条带上、距离触发还有多远、今年的税档与下一个 IRMAA 门槛还差多少、
  以及最低提取额到没到期。每个数字都是引擎给出的回执：**页面自己不做任何算术**，
  所以它不可能和它正在汇报的那个模型讲两个故事。
- **会留下东西的年度复盘。** 一份固定顺序阅读的全屏议程、一个放大字号供两个人一起看的版式、
  一封写给明年自己的信，以及一个日历 `.ics` 文件。没有通知、没有云、没有后台进程 ——
  留下来的是一份不可变的本地纪要，和一个由你自己放进日历的全天事件。
- **一场十年演练，评的是纪律不是择时。** 在熊市开局、通胀十年或长牛里逐年过日子，
  每年暂停一次，选择照你承诺的规则走、硬扛原支出，还是恐慌卖出。评分只看你的规则依从度
  和支出底线，**永远不读期末余额**，所以一次运气好的路径赢不过一次守纪律的路径。
  它什么都不保存：关掉就忘掉整场演练。
- **伤残、长期护理与死亡接在同一条链上。** 默认关闭。打开后三者各读同一条逐路径子流里
  自己的那一列，所以关掉其中一个不会移动另一个的结果。每条转移仍用它自己有出处的表，
  没有熔成一张新表；死亡是吸收态 —— 它会阻止之后的发生，并截断已经开始的护理。
- **第二个国家的账户规则，是数据不是代码。** 加拿大的非注册账户、RRSP、RRIF 与 TFSA 形状
  以规则包发布，**每一个数字都带官方来源与年份**，包括资本利得的一半计入，以及普通 RRIF
  完整的最低提取因子表。71 岁明确选择 RRIF 时有一条可运行的逐年账户工作单；取现与年金
  作为**合法但未建模**的选项如实披露，省级税、CPP 与 OAS 不在这个包的范围内。
  引擎里没有任何加拿大分支，美国路径与一个完全没有这些东西的构建**逐位相同**。
- **填的时候页面不再动。** 向导每一节先问这一节的开关，等你说「这节答好了」再展开其余字段，
  于是不会在你打字的时候，底下不停地冒出或消失一些框。
- **结果页能读下去。** 每页把核心数字放在最前，问题导航指向真正回答它的那一页，
  保存会告诉你到底存没存上；中英文、深浅外观、窄窗口下都照样成立。
- **可以自己带走的反馈包。** 你逐节勾选、先看预览确认内容，然后才导出 Markdown 与 JSON 两份。
  **App 不向任何地方发送任何东西**：金额默认不含、勾了才含，两份产物开头都写明「未经脱敏」，
  一项都没勾时它会点名拒绝，而不是默默把全部导出去。
- **里程碑按你真实活过的那条路算。** 里程碑年龄是计划描述的那条路径上的首次跨越 ——
  积累期到退休，之后接退休期的组合 —— 所以概览页与分布页回答的是同一个问题，
  而不是各答各的。
- **能用键盘操作的控件，和不会散架的页面。** 下拉菜单用方向键连续浏览、不会中途提交，
  回车或空格确认，Esc 恢复原值；页面在周围重建时，输入框保持焦点。弹窗 200 毫秒打开、
  全程清晰不模糊；窄窗口下表格折成带标签的分组，而不是横向滚动。

### 这次发布是什么（以及不是什么）

这是一个**公开候选版**，不是通用等价性的承诺，也不是税务建议、投资建议或 GA/企业版。
它是从私有 `main` 构建的冻结 universal2 候选，相对 v3.0 快照已经过了十一个开发版本，
相对 v10.0 过了四个。

自那以后新增的、会改变计划预测的模块**一律默认关闭** —— 所以在旧版本下保存的计划，
升级后数字逐位复现。这些模块带来的是**问更难的问题的能力**：块状支出、社保削减、
不是一条平滑曲线的房价、不按固定速率增长的职业收入、一年的间隔期、一次失业、一次伤残、
一份 ESPP —— 而不是悄悄改变你已有的答案。

模型仍然是近似。税、ACA/IRMAA、社保、死亡率、住房、中美搬迁与收益假设在 App 内
都有明确的局限说明。结果是情景分析，不是预测或建议。

### 源码快照

公开源码包含候选 App 使用的运行时和构建输入：

- `engine/`：生命周期、税务、收益、规则包和住房模型；
- `server/`：本地 HTTP App、持久化、迁移、恢复和报告适配器；
- `web/`：内置网页界面；
- `build-app.sh`、依赖锁定文件、PyInstaller 入口和身份/构建辅助工具；
- 少量回归、JavaScript、冻结包和 UI 烟测文件；
- `tools/recover_without_app.py` 与 `tools/verify_zero_requests.py` —— 上面那两条承诺
  因此可以被验证，而不是只能相信。

公开文件集由构建出来的候选包自己的 runtime manifest 算出 —— 也就是 App 真正加载的那些文件，
本次候选是 99 个 —— 而不是靠一份手工维护的清单；再加上少量回归、JavaScript、冻结包与 UI 烟测，
以及它们共用的那个辅助模块。私有开发历史与运维文档（工作日志、交接、路线图、审计、提示词）
不在此仓库中。

### 从源码构建（维护者向）

在具备兼容 macOS universal2 工具链，并准备好本地合并的 universal2 NumPy wheel 的机器上：

```bash
BUILD_ONLY=1 ./build-app.sh
```

脚本会执行 JavaScript、回归、冻结包、签名和 universal2 闸门，并把候选包留在 `.build/`；
不会安装或替换现有 App。公开快照不声称提供一键、跨机器完全可复现的构建环境。

### 本地数据与隐私

计划和导入数据设计为保存在本机。App 不会上传券商 CSV、SSA XML、
计划或运行结果。请不要把敏感个人数据放进公开 issue 或 pull request。

### 许可证

MIT，见 [LICENSE](LICENSE)。
