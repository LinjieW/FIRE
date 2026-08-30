# FIRE Modeling — macOS app

An interactive desktop app for **FIRE (Financial Independence / Retire Early)**
Monte Carlo analysis. You describe your finances; it answers with distributions
and error bars, lets you ask it questions (goal seeker), compare withdrawal
strategies, replay real history against your plan, and track forecast versus
actual year after year.

Runs entirely on your Mac — **no internet, no accounts, no external-host
requests**. The interface talks only to the bundled engine over your Mac's
loopback connection. Broker-CSV and SSA text exist transiently in that local
request and in memory, are cleared after parsing, and are never saved into
plans, browser storage, or logs.

The local HTTP surface keeps read routes loopback-only and requires a
server-generated in-memory capability, canonical Host/Origin, strict JSON
framing, and `application/json` for every POST. That is a browser-origin and
CSRF boundary — not OS-level authentication against another process owned by
the same user.

`FIRE Modeling.app` is **self-contained and universal2**: it bundles its own
Python and numpy for both Apple Silicon and Intel, so it double-clicks and runs
on **any Mac with nothing installed**.

## Status of this snapshot

This is a **public candidate (prerelease), not a GA release.** The app is
**signed ad-hoc and is not notarized**, so first launch needs a one-time
right-click → Open (see *Launch*).

Version **10.0** closed nine phases of accumulation-phase work. Relative to the
previous public candidate (7.0) the engine gained: a family workspace and
decision archive; a historical calibration backtest of the engine's own
forecast distribution; career breaks, layoffs and human-capital shocks for both
earners; long-term disability with accumulation-phase health premiums; RSU
vesting and retained single-stock concentration; a section 423 ESPP with both
disposition paths; traditional defined-benefit pension accrual; strict HSA/HDHP
eligibility; SIMPLE and 403(b) plan shapes; second promotions for both earners;
temporary childcare and commuting costs; and working-years housing cost taken
before contributions.

**What this snapshot does not claim.** It is not investment, tax, or legal
advice. It is not a statement that the tax tables are current, that the maths
is optimal, or that any figure predicts your outcome. Monte Carlo bands reflect
*sampling* only — not uncertainty in the assumptions themselves. The app's own
limitations panel is the complete, configuration-specific list, and it is worth
reading before trusting any number here.

## Downloads

From this repository's **Releases** page, tag `v10.0-public-candidate-1`:

| Asset | SHA-256 |
| --- | --- |
| `FIRE-Modeling-10.0-macOS-universal2.zip` (47 MB compressed, ~166 MB unpacked) | `d8c52b7e4e02ab3fe649bda5b958c932198e106e0411aca9b2a16239d56d6a3f` |
| `fire-modeling-10.0-source.tar.gz` | listed in `SHA256SUMS.txt` on the same release |

Verify before you run it:

```bash
shasum -a 256 FIRE-Modeling-10.0-macOS-universal2.zip
```

`SHA256SUMS.txt` on the release covers both assets. The app zip's hash is
printed here as well so it can be checked against a page you are already
reading, rather than only against a file downloaded from the same place.

**Correspondence.** The app was frozen from the source commit this snapshot
publishes; `engine/`, `server/` and `web/` are byte-identical between the two.
The only file that differs is `tools/release_identity.py`, a release-only tool
that contributes nothing to the runtime manifest (84 entries, zero of them
under `tests/`).

---

## 中文说明（下载、首次启动、边界）

**这是什么。** 一个在你自己的 Mac 上运行的 FIRE（财务独立／提前退休）蒙特卡洛分析桌面
应用。你填写自己的情况，它给出分布与误差带，并且可以反过来问它问题（目标求解）、比较提取
策略、用真实历史回放你的计划、逐年记录预测与实际的差距。

**隐私。** **不联网、无账号、不向任何外部主机发请求。** 界面只通过本机 loopback 与内置引擎
通信。券商 CSV 与 SSA 文本只在那一次本地请求和内存中短暂存在，解析后即清除，**不会**写进
计划、浏览器存储或日志。本机 HTTP 面要求服务端生成的内存能力令牌、规范的 Host/Origin、严格
JSON 框架与 `application/json`；这是浏览器来源与 CSRF 边界，**不是**针对同一用户下其他进程的
操作系统级认证。

**下载与校验。** 见本页 *Downloads* 一节：下载后请自行核对 SHA-256，与该节列出的值一致再运行。

**首次启动。** App **只做了 ad-hoc 签名、没有做 notarization**，所以第一次双击会被 Gatekeeper
拦下。**右键 → 打开 → 打开**一次即可，之后正常双击。它是 universal2 自包含包，Apple Silicon
与 Intel 都能直接跑，机器上不需要预装任何东西。

**这是候选版本，不是 GA。** 本快照是公开候选（prerelease）。它**不是**投资、税务或法律建议；
**不**声称税表是最新的、数学是最优的，也**不**声称任何数字能预测你的结果。蒙特卡洛的分布带
只反映**抽样**的不确定性，**不包含假设本身的不确定性**。

**模型边界。** 完整且随你的配置变化的局限清单在 App 内的「局限声明」面板里 —— 那才是完整
版本，本页只是摘要。几条值得先知道的：历史回放只有约 97 年真实数据；压力回测用的是风格化
序列；求解器与前沿是粗网格近似而不是最优解；结构化年度收入按税后现金处理，不进入 MAGI/ACA/
IRMAA，因此可能高估 ACA 补贴并低估税与 IRMAA。所有可选模块**默认关闭**，关闭时与从未存在
该模块的构建逐位相同。

---

## License and continuity

Source code is provided under the [MIT License](LICENSE). Those rights exist
from the moment you lawfully receive a copy — there is no waiting period and no
third-party source escrow, and nothing here promises that some custodian will
release anything later. The project keeps a continuity charter recording the
tested recovery entry points; it is an internal document and is not part of
this snapshot.

---

## Launch

**Double-click `FIRE Modeling.app`.** It appears in the Dock with its own icon,
starts a local server, and opens the analysis panel. Drag it into
**/Applications** to install like any app.

**Quitting**: close the native window, use the 退出/Quit button, or press ⌘Q.
If the app falls back to a browser tab, use 退出/Quit to stop its local service.
Double-clicking while it is running reuses the existing service rather than
starting a second copy.

> **First launch** (especially after AirDrop/download): the app is unsigned, so
> Gatekeeper blocks a plain double-click. **Right-click → Open → Open** once;
> after that it double-clicks normally.

---

## Sharing it with friends (any Mac, nothing to install)

Hand a friend **just `FIRE Modeling.app`** (AirDrop / zip / USB). They
**right-click → Open** the first time, and it runs — Apple Silicon **or Intel**,
no Python, no setup. (The bundle is ~170 MB; most of that is the dual-
architecture math library.)

To rebuild after editing source:

```bash
./build-app.sh        # gates: full regressions on both CPU slices, JS parsing,
                      # frozen-executable smoke, signing and Mach-O audit
                      # → universal2 FIRE Modeling.app only after all pass
```

---

## Using it

**Flow**: welcome (60-second quick estimate or full wizard) → 8-step wizard
(basics · portfolio · income · assumptions · family/events · relocation ·
advanced · review) → precision pick (Quick 2k → Official 100k) → live progress
→ a tabbed results report. 中文/EN throughout.

**Results pages:**

- **概览 Overview** — the one-sentence verdict (three-branch lifetime success,
  FIRE age, sustainable spend, estate) + **live-tweak sliders** (I1): drag
  income/expenses/SWR/equity/fees, delta cards re-compute in ~2 s against the
  same random paths, apply to the plan when satisfied.
- **轨迹 Trajectory** — lifecycle percentile fan (drag the FIRE line to solve
  "what would earlier take"), **age-slice drill-down** (I3), and
  **forecast-vs-actual check-ins** (I4): record your real total once a year,
  see each point graded against the forecast band (P14, ≤P10, …).
- **分布 Distributions** — terminal/consumption/milestone distributions;
  **click any histogram bucket** to see what those paths share (I3); **story
  mode** (I2): three concrete lives — typical, lucky, unlucky — with a
  year-by-year chronicle and a "reroll a life" button.
- **敏感性 & 压力 Sensitivity & stress** — tornado, SWR/claim-age sweeps,
  sequence-risk backtest, **Roth conversion grid** (E2), **withdrawal
  strategy compare** (E3: GK / fixed-real / VPW / floor+upside / ABW on the
  same paths), **goal seeker** (S1: pick a goal and two levers, get the
  feasible region and the nearest workable change), **efficient frontier**
  (S2: spend × FIRE-age × success Pareto surface with "you are here"),
  **housing rent-vs-buy** (E5: real amortization + MC comparison). The Roth
  conversion grid is a directional single-lever comparison using flat account-bucket
  terminal liquidation proxy, not a tax-optimal estate or multi-year scheduler.
- **搬迁对比 Relocation** — home vs. destination full-cycle comparison
  (city library; optional **PPP mean-reversion FX anchor**, E6).
- **结论 Conclusions** — findings generated from your numbers + the full
  **limitations panel** (every approximation disclosed, kept in sync with the
  implementation by review).

**Data import (all local):** broker positions CSV → four buckets (D1, wizard
step 2); ssa.gov statement XML → real AIME/PIA via the actual SSA formula
(D2, wizard step 4). **Plans** and drafts persist in this app's local WebView
storage using a versioned schema. That is the authoritative source until you
migrate: the 3.0 cutover is an explicit, confirmed action, and until you take it
nothing about 2.0 storage changes. Formal Standard/Official runs additionally
create a local server reference and an immutable SQLite snapshot, without
deleting or rewriting the WebView record. Saved plans carry check-in history
because check-ins live in the plan configuration.

### 3.0 persistence bridge

The migration rehearsal below is still opt-in and still writes nothing. The UI
reads only the raw `fire_draft` and `fire_plans_v1` localStorage values and
sends their UTF-8 SHA-256 hashes to the loopback server. `preview` performs a
zero-write projection; `stage` can explicitly save a content-addressed,
no-replace raw envelope under the app-support directory with `0700`/`0600` permissions. Damaged
records are quarantined and the response reports `clean`, `partial`, or
`blocked` reconciliation instead of silently treating them as empty.

Neither shadow action changes or deletes localStorage or writes the formal
SQLite store. Until a cutover, the original localStorage source stays
authoritative. A separate opt-in recovery package endpoint captures the envelope
and archive for backup/restore evidence, with server-side R1–R3 reconciliation
and raw-restore contracts.

**The cutover itself now exists** and is a confirmed, one-way user action.
Taking it imports plans and versions into the SQLite archive, moves authority to
the archive under a compare-and-set, and stops the legacy keys from being
written — it never deletes them. Afterwards the app reads and writes plans
through the server storage seam; drafts the migration carried over are listed
with Open and Save-as-plan controls, and an unsaved wizard draft is kept in a
private file beside the archive so it survives a restart. If the legacy source
is seen to change out from under the archive, or the control journal latches for
manual recovery, writes are refused with a stated reason rather than quietly
dropped, and the UI distinguishes a latched seam from an unreachable one.

This implementation has now been independently reviewed, merged into `main`,
and verified against the complete Phase 0 exit gates. Phase 0's narrow contract
is closed. On 2026-07-28 the tagged candidate was promoted over the user's
previous app through the supported rollback orchestrator; the real installed
path then passed the complete gate set before the retained previous bundle was
removed.
The formal exit contract is revision 9. The server-only M1 candidate validates
the distinct formal envelope, creates an external migration intent, and supports
disposable `preview`/`stage` with replay after restart. M2 now adds a separate
formal projection plus disposable v8 `import`/`verify`: clean input creates
row-rooted Plan/PlanVersion, recovered-draft, and evidence-only legacy CheckIn
records in the staging image; a partial or quarantined input creates zero such
business rows and retains only source/quarantine evidence. `verify` requires a
fresh exact envelope readback.

M1/M2 do not write the live archive, change authority or generation, or read/write
WebKit localStorage; they are the disposable rehearsal that precedes a cutover.
The M3 server-side control seam has a prebound `archive_write` owner with external
idempotency receipts, generation CAS, authority-event binding, single-child
guards, startup replay, and preimage rollback/manual-latch handling; its real
SQLite tests cover concurrency, stale generations, lost responses, receipt
ordering, rollback, and migration-child ownership. Formal `verify` now requires
an explicit page identity and binds a short-lived, page-bound fence in the
external journal. An internal `preflight_finalize` returns evidence for the
second-envelope/fence/generation checks without creating an archive child, and
`POST /api/migration/finalize` is the public route the browser's fence/readback
adapter drives on top of it. Ordinary Plan/Run writes go through the same seam:
after a cutover, an archived formal run owns its archive write rather than
writing behind the journal's back. The server also persists a non-executable,
canonical `fire-authority-intent-v1` commitment that excludes final logical and
ack hashes, closing the design loop before the live CAS. The implementation
received an independent code/evidence review; the final clean-main candidate
and installed-path gates were then executed locally and are reported as such.

### Historical Phase 0E checkpoint: opt-in formal-run archive

> This subsection preserves the dated build-up from schema v6 to the completed
> Phase 0 exit. Statements below that an intermediate candidate was unpromoted
> or cutover was unfinished describe that checkpoint; they are superseded by
> the current Phase 0 closure and installed-release record above.

The main UI now sends an explicit archive request for **Standard (10,000
paths)** and **Official (100,000 paths)** runs. The server lazily opens
`~/Library/Application Support/com.local.fire-modeling/fire-modeling.sqlite3`,
creates or reuses the matching immutable `PlanVersion`, records a server-owned
`RunAttempt`, and commits a `RunSnapshot` only after the engine result is
complete. Repeating the same normalized inputs reuses the latest version;
changed inputs append a child version. Quick/Deep runs and legacy API requests
without `archive: true` do not open SQLite and keep the old `{job}` response.

Saved-plan rows retain their server reference outside the engine config. A
small Timeline action displays version, time, precision/seed, snapshot lineage,
and completed/failed/cancelled attempts. Timeline reads open an existing DB in
read-only mode; a missing DB or unknown plan is a 404 and does not create one.
Errors are exposed only as bounded reason codes. Formal archive POSTs now carry
a server-owned `request_id`: the same key and fingerprint replay the existing
job/snapshot after a transport retry or process restart, a changed input returns
409, and terminal failures are cached rather than silently rerun. Quick/Deep and
legacy non-archive calls keep the old `{job}` contract and do not create a key.
The archive seam is still opt-in and is not localStorage cutover or formal
CheckIn/Decision support. The separate backup/restore package vertical slice
now includes server-side R1–R3 validator, journal reconciliation/resolver, and
raw-restore outcome contracts, but it does not yet complete browser
localStorage import/cutover or the Phase 0 restore/GA exit conditions. The
current schema-v6 source gates
include 44/44 persistence and 70/70 archive+persistence+trust contracts,
86/86 engine regression, 29/29 migration/release tests, persistence full smoke,
UI 24/24, and a universal2 candidate with real SQLite archive,
read-only timeline, permission, and embedded frozen build-identity smoke on both
CPU slices; the request-id contract is covered by the source HTTP/persistence
gates and included in that candidate. The candidate remains unpromoted.
Phase 0E holds a private process-lifetime writer lock for archive stores and
fails closed if another process owns it. Timeline reads make no logical DB
writes; SQLite WAL sidecars are checked for regular-file/private permissions and
unsafe sidecars fail closed. A user-directed Sol Ultra full audit on 2026-07-16
returned `APPROVE WITH CONDITIONS`: the DB/lock/read-only paths must reject
symlinks before open/chmod, `run_requests` still needs INSERT/DELETE guards, and
the frozen runtime identity must be separated from the broader docs/tests
evidence before claiming a new current-worktree-corresponding candidate. At that
audit checkpoint, the then-existing candidate's runtime/data entries still
matched and both frozen CPU smokes passed, but its broad embedded identity
predated later documentation/test edits.
C1 path hardening and C2 request-contract hardening are implemented. A
first final Sol Ultra review found that schema v4 still allowed
`INSERT OR REPLACE` to bypass the `run_attempts` DELETE trigger; schema v5 now
adds duplicate-identity guards across all immutable tables and is covered by
39/39 targeted persistence contracts,
including writable/read-only DB, lock/WAL, parent symlink, hardlink, and
non-regular-object adversarial cases. It uses canonical macOS paths and does not
silently resolve user symlinks; Python `sqlite3` pathname-open TOCTOU, ACL,
network-filesystem, and OS-crash durability limits remain explicit. C3 now
separates frozen runtime/build inputs from broad docs/tests evidence and is
covered by 12/12 release-identity tests. The rebuilt v5 candidate's generated,
embedded, and current identities are byte-identical at
`0b5e78b718311af39041532d46f628b421bffd1da1ded04b2cb731d4107a4f30`;
92 regular-file Mach-O binaries are universal2, and codesign, bundle JavaScript,
and both CPU frozen smokes pass. A second final Sol Ultra review nevertheless
returned `BLOCK`: schema v5 still permits a RunAttempt ID to be renamed and
reused, and its migration validator can omit plan-lineage-corrupt rows. This
candidate remains unpromoted and is only historical evidence for the now-known
flawed v5 runtime. Schema v6 now makes RunAttempt and stable Plan identities
unconditionally immutable and validates each PlanVersion, RunAttempt,
RunSnapshot, and RunRequest from its own row before migrating v4/v5 data. Its
source gates pass. The rebuilt v6 candidate's generated, embedded, and current
identity is byte-identical at
`4b29a7ed99cfc5326e3ade35725a5aa80b4de2c04079d6dd5bbb61d22790caa5`;
92 regular-file Mach-O binaries, codesign, bundled JavaScript, and both frozen
CPU smokes pass. Sol Ultra closing review returned `APPROVE WITH CONDITIONS`
with no P0–P2; its sole stale-roadmap documentation condition is now closed.
The C1/C2/C3 hardening gate is complete. The candidate remains unpromoted, and
durability/backup/cutover remain separate later work.

---

## What's new in 2.0 (vs 1.0)

| Pillar | Shipped |
|---|---|
| Engine truth | E1 true year-by-year taxes (2026 tables: brackets, LTCG stacking, SS torpedo, RMD, IRMAA, true-MAGI ACA) · E2 Roth conversion grid · E3 withdrawal-strategy library · E4 returns 2.0 (Markov regime switching + AR(1) inflation, 1928–2024 historical block bootstrap) · E5 housing (mortgage amortization, refi, rent-vs-buy) · E6 FX PPP anchor |
| Solvers | S1 universal goal-seek (feasible-region search) · S2 efficient frontier (Pareto surface) |
| Interaction | I1 live sliders · I2 single-path story mode · I3 chart drill-down · I4 forecast-vs-actual tracking |
| Data | D1 broker CSV import · D2 SSA XML → AIME/PIA · D3 versioned plan schema |
| Build | T2 universal2 (Intel + Apple Silicon in one bundle) |

Discipline held throughout: **every new module is opt-in and default-off**, and
each module's disabled seam is pinned, including RNG state. Deliberate
model-truth corrections are recorded separately instead of being hidden inside
an “OFF unchanged” claim: earlier work fixed two pre-existing RNG bugs
(disabled relocation consuming FX draws and no-FI paths skipping mortality),
and the current source also rejects a pre-existing US TRUE-tax false success
when delivered cash misses the annual need by more than $1. The resulting
goldens are pinned on both CPU architectures.

---

## What the model does

The engine is the vendored **`fire_v9_8_model` chain** (v6 → v9.8, each layer
auditable): regime-mixture Student-t returns (or Markov/historical-blocks in
2.0 mode); stratified accumulation across pretax/Roth/HSA/taxable; FIRE
crossing at your SWR; **Guyton-Klinger guardrail** withdrawals (plus the 2.0
strategy library); tax-efficient withdrawal ordering (flat approximation by
default, true bracket math opt-in); mortality; Social Security offsets;
optional relocation (FX, foreign inflation/tax/healthcare); and a
**cash-accounting check** comparing recorded consumption with portfolio cash,
Social Security applied to spending, and structured income applied to spending.
Actual structured-income receipt years and material-shortfall exits reconcile
to delivered cash. Successful years with no structured-income receipt retain
the historical withdrawal convention of recording the target when the delivery
gap is at most $1 per year; the displayed diagnostic includes that bounded
compatibility residual.

**Three-branch success** — "lifetime success" means you *reached FI and stayed
solvent* **or** *died before retiring*; it is not "% who reached FI".

**Household accumulation (current source):** mortality is applied at year-end,
so the death year's earned contributions remain; later wage-related
contributions from the deceased stop and the engine re-evaluates FIRE. The full
pre-FIRE household expense is charged exactly once regardless of which member
survives. The survivor-spending fraction applies only after retirement.

**Structured annual income (current source):** pension, rental, part-time, and
RSU/equity inputs are today's-dollar, after-tax spendable cash with an explicit
owner (`primary`, `spouse`, `household`, or `unspecified`). `unspecified` is the
default for an unchosen owner and for a legacy missing/null value: it preserves
last-survivor numeric behavior but does not claim shared ownership. Before
retirement these streams enter taxable; after retirement they cover spending
before Social Security and portfolio withdrawals, with any surplus credited to
taxable. All ages stay on the primary user's timeline. Part-time cannot start
before the year after actual FIRE, rental endpoints are inclusive, and equity
pays exactly the entered number of years starting next modeled year.

**Added since the 2.0 core (3.0 through 10.0).** Accumulation is no longer a
smooth curve: promotions (up to two per earner), bonus and 1099 profit drawn as
distributions, planned career breaks, layoffs, human-capital shocks, and
long-term disability all reach the contribution waterfall rather than being
applied afterwards. Equity compensation is modelled as earned income — RSU
vesting, optionally retaining shares as single-stock concentration, and a
section 423 ESPP with both immediate-sale and qualifying-hold dispositions.
Accounts carry their real shapes: governmental 457(b) in its own bucket,
SIMPLE and 403(b) limits, the 15-year 403(b) catch-up, section 415(c) annual
additions, verified HSA/HDHP eligibility, and traditional defined-benefit
pension accrual. Retirement adds long-term care as a multi-state process,
Social Security trust-fund depletion drawn from the Trustees Report, and a
terminal step-up. Every one of these ships **off by default**, and turning one
off is bit-identical to a build that never had it.

**Where the numbers come from.** Statutory figures live in a dated, offline
rule pack with a source URL and a review date per component, not as literals in
the engine; the app reports which pack components a run actually used. Numbers
that are modelling choices rather than measurements are listed in an assumption
registry with an evidence grade, and relationships the engine assumes between
modules are listed in a correlation registry — including ones graded
"examined, unresolved", which is the honest label for a coupling that is known
to exist and is not yet modelled.

**Honesty note.** Monte Carlo bands reflect *sampling* only, not the
uncertainty in the assumptions themselves. Historical-block mode replays real
1928–2024 annual data (Damodaran/BLS series, pinned by tests) but only ~97
years exist. The stress backtest uses stylized sequences. Solver/frontier
outputs are coarse-grid approximations, not optima. Structured annual income is
treated as after-tax cash and does not itself enter ordinary income, MAGI, ACA,
or IRMAA; it affects those only indirectly through lower portfolio withdrawals.
That can overstate ACA subsidies and understate tax or IRMAA. The full list
lives in the in-app limitations panel. Independently of that approximation,
US TRUE-tax paths now fail whenever the solver reports more than $1 of
undelivered annual need, even if gross Social Security equals the nominal
target. Not investment, tax, or legal advice.

---

## Layout

```
FIRE App/
  FIRE Modeling.app      ← self-contained universal2 app, ~166 MB (share this)
  build-app.sh           ← rollback-safe, locked universal2 release pipeline
  build-requirements.lock← exact build-tool versions
  pyi_main.py            ← frozen-app entry point
  engine/                ← vendored v6→v9.8 model chain (source of truth)
    fire_v9_8_model.py   ← lifecycle engine (accumulation + retirement)
    fire_tax_true.py     ← E1 true tax engine (2026 tables; ACA/IRMAA approximations disclosed)
    fire_rules_x.py      ← E3 strategy library (VPW / floor+upside / ABW)
    fire_returns_x.py    ← E4 returns 2.0 (markov / historical blocks)
  server/
    app.py               ← stdlib HTTP server + all /api/* routes
    decision_lab.py      ← synchronous sweep/frontier/sensitivity/goal-seek seam
    engine_adapter.py        ← config adapter · summary/story/drill · goldens
    housing.py           ← E5 mortgage math + event compiler
    csv_import.py        ← D1 broker-CSV parser (local, aggregate-only)
    ssa_import.py        ← D2 SSA XML → AIME/PIA (official AWI + bend points)
    presets.py           ← de-identified baseline + example configs
    build_report.py      ← standalone HTML report
  web/
    index.html, app.js, destination_catalog.js, charts.js, styles.css
                                      ← vanilla JS, no build step
  tests/
    test_regression.py   ← source regression: goldens · directional/adversarial contracts ·
                           invariants · security · de-identification · i18n lint
    js_syntax_check.py   ← mandatory source/bundle JavaScript parser gate
    frozen_smoke.py      ← executes the actual frozen app on both CPU slices
    ui_smoke.py          ← 332 checks driven through a real hidden WKWebView
  .build/wheels/merged/  ← delocate-merged universal2 numpy wheel (build dep)
```

**Verification culture**: `./build-app.sh` builds in isolation and refuses to
replace the installed app unless every gate passes. The corrected OFF-state
golden (`2,604,940.65397021 @ 800 paths / seed 96000`) is pinned on arm64 and
x86_64; every opt-in module carries directional tests and a matching disclosure.
