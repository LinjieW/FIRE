# FIRE Modeling

An offline, self-contained macOS app for exploring **Financial Independence /
Retire Early (FIRE)** plans with Monte Carlo analysis.

This repository is a **public release snapshot** for downloading the app and
inspecting the runtime source. It intentionally contains the files needed for
the application and its build smoke checks, not the private development
history, workstream logs, prompts, or internal audit archive.

## Download

The current public candidate is:

`v7.0-public-candidate-1`

Download the universal2 bundle from the [GitHub Release](https://github.com/LinjieW/FIRE/releases/tag/v7.0-public-candidate-1):

`FIRE-Modeling-v7.0-public-candidate-1-macos-universal2.zip`

The bundle runs on Apple Silicon and Intel Macs. It includes its own Python,
NumPy, and web UI; no separate Python installation or account is required.

### Verify the download

After downloading, verify the SHA-256 recorded in the Release notes:

```bash
shasum -a 256 FIRE-Modeling-v7.0-public-candidate-1-macos-universal2.zip
```

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

## What this release is (and is not)

This is a **public candidate**, not a promise of universal equivalence, tax
advice, investment advice, or a GA/enterprise release. It is the frozen
universal2 candidate built from the private `main`, four development versions
on from the v3.0 snapshot.

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
- a small set of regression, JavaScript, frozen-bundle, and UI smoke checks.

The public file set is computed from the built candidate's runtime manifest --
the exact files the application loads -- rather than from a hand-maintained
list, plus a small set of regression, JavaScript, frozen-bundle and UI smoke
checks. Internal development history and operational documents (workstream
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

当前公开候选版本是 `v7.0-public-candidate-1`。请从
[GitHub Release](https://github.com/LinjieW/FIRE/releases/tag/v7.0-public-candidate-1)
下载通用 universal2 App：

`FIRE-Modeling-v7.0-public-candidate-1-macos-universal2.zip`

同一个包支持 Apple Silicon 和 Intel Mac；App 已内置 Python、NumPy 和网页界面，
不需要另装 Python，也不需要账号。

### 校验下载

下载后，按照 Release 说明中的 SHA-256 校验值执行：

```bash
shasum -a 256 FIRE-Modeling-v7.0-public-candidate-1-macos-universal2.zip
```

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

### 这次发布是什么（以及不是什么）

这是一个**公开候选版**，不是通用等价性的承诺，也不是税务建议、投资建议或 GA/企业版。
它是从私有 `main` 构建的冻结 universal2 候选，相对 v3.0 快照已经过了四个开发版本。

自那以后新增的、会改变计划预测的模块**一律默认关闭** —— 所以在旧版本下保存的计划，
升级后数字逐位复现。这些模块带来的是**问更难的问题的能力**：块状支出、社保削减、
不是一条平滑曲线的房价、不按固定速率增长的职业收入 —— 而不是悄悄改变你已有的答案。

模型仍然是近似。税、ACA/IRMAA、社保、死亡率、住房、中美搬迁与收益假设在 App 内
都有明确的局限说明。结果是情景分析，不是预测或建议。

### 源码快照

公开源码包含候选 App 使用的运行时和构建输入：

- `engine/`：生命周期、税务、收益、规则包和住房模型；
- `server/`：本地 HTTP App、持久化、迁移、恢复和报告适配器；
- `web/`：内置网页界面；
- `build-app.sh`、依赖锁定文件、PyInstaller 入口和身份/构建辅助工具；
- 少量回归、JavaScript、冻结包和 UI 烟测文件。

公开快照基于仓库最初的 MIT 许可提交压缩而成；私有开发历史和运维文档不在此仓库中。

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
