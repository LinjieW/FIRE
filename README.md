# FIRE Modeling

An offline, self-contained macOS app for exploring **Financial Independence /
Retire Early (FIRE)** plans with Monte Carlo analysis.

This repository is a **public release snapshot** for downloading the app and
inspecting the runtime source. It intentionally contains the files needed for
the application and its build smoke checks, not the private development
history, workstream logs, prompts, or internal audit archive.

## Download

The current public candidate is:

`v3.0-public-candidate-1`

Download the universal2 bundle from the [GitHub Release](https://github.com/LinjieW/FIRE/releases/tag/v3.0-public-candidate-1):

`FIRE-Modeling-v3.0-public-candidate-1-macos-universal2.zip`

The bundle runs on Apple Silicon and Intel Macs. It includes its own Python,
NumPy, and web UI; no separate Python installation or account is required.

### Verify the download

After downloading, verify the SHA-256 recorded in the Release notes:

```bash
shasum -a 256 FIRE-Modeling-v3.0-public-candidate-1-macos-universal2.zip
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

## What this release is (and is not)

This is a **pre-Phase-2 candidate**, not a promise of universal equivalence,
tax advice, investment advice, or a GA/enterprise release. It is the frozen
universal2 candidate corresponding to the behavior-neutral pre-Phase-2 source
split. The candidate was built and smoke-tested in an isolated disposable
worktree; it was not used to replace the maintainer's installed app.

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

The public snapshot is intentionally squashed onto the repository's initial
MIT-licensed commit. Internal development history and operational documents
remain outside this public repository.

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

当前公开候选版本是 `v3.0-public-candidate-1`。请从
[GitHub Release](https://github.com/LinjieW/FIRE/releases/tag/v3.0-public-candidate-1)
下载通用 universal2 App：

`FIRE-Modeling-v3.0-public-candidate-1-macos-universal2.zip`

同一个包支持 Apple Silicon 和 Intel Mac；App 已内置 Python、NumPy 和网页界面，
不需要另装 Python，也不需要账号。

### 校验下载

下载后，按照 Release 说明中的 SHA-256 校验值执行：

```bash
shasum -a 256 FIRE-Modeling-v3.0-public-candidate-1-macos-universal2.zip
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

### 这次发布是什么（以及不是什么）

这是 3.0 的 **pre-Phase-2 候选版本**，不是 GA，也不是财务、税务、法律或投资建议，
更不是对未来结果的保证。模型仍然是情景分析：税务、ACA/IRMAA、Social Security、
死亡率、住房、跨地区搬迁和收益率假设都有明确边界。

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
