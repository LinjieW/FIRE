"""FIRE Modeling — UI smoke test in a REAL (hidden) WKWebView.

Drives the exact rendering engine the shipped app uses (pywebview/WKWebView),
so it exercises production behavior Playwright/Chromium would not — including
the WebKit quirks this app has already been bitten by (keep-alive, blob URLs).

Flows covered:
  1. quick estimate -> computing -> results verdict appears
  2. wizard walk (8 steps) -> precision page (seed input present)
  3. save plan -> welcome list shows it
  4. language toggle on the help page switches content immediately
  5. A/B: save A & B -> A/B tab renders the compare table
  6. English stress page, including dynamic options and backtest labels, has no CJK

Run:  .build/venv/bin/python tests/ui_smoke.py     (~60s; opens NO visible window)
Not part of the build gate (it needs a GUI session); run before releases.
"""
import hashlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
def _free_port() -> int:
    """A port the OS says is free, not a number we hope is.

    The fixed 8798 could connect to whatever was already listening — including a
    compatible service left over from another run — and report a pass for it.
    """
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


PORT = _free_port()
URL = f"http://127.0.0.1:{PORT}/"
# §F scenarios each need their own server and archive; see drive_storage_seam.
SEAM_PORT = _free_port()
SEAM_URL = f"http://127.0.0.1:{SEAM_PORT}/"

# Re-exec under the build venv if pywebview isn't importable here.
try:
    import webview  # noqa: F401
except ImportError:
    venv_py = os.path.join(ROOT, ".build", "venv", "bin", "python")
    if os.path.exists(venv_py) and os.path.abspath(sys.executable) != os.path.abspath(venv_py):
        os.execv(venv_py, [venv_py, os.path.abspath(__file__)] + sys.argv[1:])
    print("pywebview not available — run ./build-app.sh once to create .build/venv")
    sys.exit(2)

import webview  # noqa: E402


def check_storage_seam_source():
    """Static gate: the two legacy keys may only be written inside the seam.

    M4's "centralized legacy writers" is not a one-off refactor — it only holds
    if the next edit cannot quietly reintroduce a direct write.  A writer added
    outside `legacyStore` would bypass the migration fence and silently drop or
    resurrect user data during cutover, which is exactly what the fence exists
    to prevent.  This runs before the GUI so it gates even on headless boxes.
    """
    src = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read().splitlines()
    start = end = None
    for i, line in enumerate(src):
        if start is None and "M4 · legacy storage seam" in line:
            start = i
        elif start is not None and "window.FIRELegacyStore" in line:
            end = i
            break
    check("legacy storage seam block is present", start is not None and end is not None)
    if start is None or end is None:
        return
    stray = [
        i + 1 for i, line in enumerate(src)
        if re.search(r'localStorage\.(setItem|removeItem)\(\s*(?:"|\')(fire_draft|fire_plans_v1)', line)
        and not (start <= i <= end)
    ]
    check("no legacy write bypasses the storage seam", not stray,
          "web/app.js lines " + ",".join(map(str, stray)))


def check_privacy_copy():
    """E5: the privacy text must not still say plans live in localStorage.

    A static check, like the storage-seam scan above, and for the same reason: it
    is a statement about the source, it must hold on a headless box, and the help
    view is not rendered in every scenario. After a cutover, plans live in the
    SQLite archive under app-support and the browser storage is no longer written
    — telling the user otherwise in the privacy section is a false statement about
    where their data is.
    """
    src = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    check("privacy copy no longer claims plans remain in local storage",
          "remain in this app WebView's local storage" not in src
          and "计划、草稿和偏好保存在该应用 WebView 的本机存储中" not in src)
    check("privacy copy describes where plans live after a cutover",
          "SQLite archive under the app-support directory" in src
          and "迁移到本地数据库之后" in src)


def check_income_stream_copy():
    """Phase 1: the visible contract must match the structured cash channel."""
    src = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    check("income UI discloses the MAGI/ACA/IRMAA approximation",
          "may overstate ACA subsidies and understate tax and IRMAA" in src
          and "可能高估 ACA 补贴并低估税与 IRMAA" in src)
    check("ACA limitation explains direction and eligibility boundary",
          "generally understates PTC below 300% FPL" in src
          and "can overstate it below 100% FPL" in src
          and "可能高估 PTC" in src)
    check("HSA controls require eligibility facts and expose family coverage",
          "HSA coverage tier" in src and "HDHP out-of-pocket max" in src
          and '"family"' in src and "eligible through age" in src)
    check("FRA limitation states the 1960-and-later cohort",
          "1960-and-later birth cohort" in src
          and "1960 年及以后出生 cohort" in src)
    check("income limitations no longer describe the removed anonymous flow",
          "These generic streams have no member ownership" not in src
          and "Part-time starts at a fixed age" not in src
          and "这些通用收入流没有成员归属" not in src)
    check("housing copy distinguishes realized-CPI Monte Carlo from deterministic mean inflation",
          "anchored to realized US CPI at purchase" in src
          and "stochastic inflation therefore changes its real burden" in src
          and "deterministic chart still uses configured mean inflation" in src
          and "随机通胀不触及按揭" not in src
          and "按期望通胀" not in src)
    check("IRMAA copy distinguishes modeled t-2 history from fallback",
          "modeled final MAGI and filing status from two tax years before" in src
          and "explicitly fall back to the current-year MAGI proxy" in src
          and "有可用的模型历史时使用保费年前两年的最终 MAGI" in src
          and "退回当年 MAGI 代理" in src
          and "two-year lookback not modeled" not in src
          and "两年回溯未建模" not in src)


def check_rule_pack_source():
    """Phase 1: one result-bound vintage receipt, no browser limit copies."""
    src = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    check("rule-pack UI reads result metadata",
          "state.data.meta.rule_pack" in src
          and "rulePackConclusionStatus" in src)
    check("rule-pack UI marks stale formal conclusions bilingually",
          "正式结论标为 stale" in src
          and "conclusion marked stale" in src)
    check("rule-pack UI has a fail-closed receipt validator",
          "function isValidRulePackReceipt" in src
          and "rule vintage unrecorded" in src
          and "conclusion_status" in src
          and "RULE_PACK_EVALUATION_BASIS" in src
          and 'rp.pack_id !== `us-offline-${rp.content_sha256.slice(0, 16)}`' in src)
    check("SSA import validator binds status to its frozen dates",
          "function isValidSsaRulePackReceipt" in src
          and 'c.status === (rp.evaluated_on > c.maintenance_due_on ? "stale" : "current")' in src
          and "rule pack 未记录" in src)
    check("browser quick allocator has no copied annual caps",
          "Math.min(23500" not in src
          and "spouse_pretax_401k_limit_y1: 23500" not in src)


def check_roth_grid_contract_source():
    """Phase 1: the Roth panel describes a tested directional grid, not an optimizer.

    The backend still calls its response member ``best`` for compatibility.  The
    visible product contract must not inherit that implementation name: users
    need to know exactly what was tested, how a point was selected, and what the
    terminal-tax proxy does (before and after the request runs).  Keep these
    checks source-level so the disclosure cannot disappear on a headless build.
    """
    app = open(os.path.join(ROOT, "web", "app.js"), encoding="utf-8").read()
    index = open(os.path.join(ROOT, "web", "index.html"), encoding="utf-8").read()
    # Re-pinned when the directional mark was half-lifted: the flat-liquidation
    # sentence became FALSE once the optimizer began comparing on the measured
    # cost basis, so it had to go. The coverage limit stayed, because only
    # eight points were ever tested. Both facts are pinned below -- what is
    # gone must stay gone, and what survived must stay stated.
    zh = (
        "固定比较 8 个年转换基准档（$0–$100k）",
        "每档按全局增长率逐年增长",
        "只在已测试档位中先比较三分支成功率，再在成功率相同的档位中比较全路径税后终值 P50（失败路径记 0）",
        "终值不再是平率清算代理",
        "剩下的方向性只有一条",
    )
    en = (
        "Compare exactly 8 tested base annual levels ($0–$100k)",
        "each growing by the global rate",
        "Among tested levels, maximize three-branch success first; only equal-success levels use unconditional after-tax terminal P50 (failed paths = 0)",
        "Terminal value is no longer a flat liquidation proxy",
        "Coverage is the only directional limit left",
    )
    gone = ("终值采用账户桶平率清算折扣代理",
            "Terminal value uses flat account-bucket liquidation haircuts")
    for phrase in gone:
        check("the false flat-proxy claim stays gone from app.js",
              phrase not in app, phrase)
    for phrase in zh:
        check("Roth Chinese contract is present in app.js", phrase in app)
    for phrase in en:
        check("Roth English contract is present in app.js", phrase in app)
    for phrase in ("Roth 转换额度对比", "固定比较 8 个年转换基准档", "对比 8 个转换档位"):
        check("Roth HTML fallback keeps the pre-run Chinese contract", phrase in index)
    check("Roth HTML fallback has no old optimizer wording",
          "Roth 转换优化器" not in index and "优化转换额度" not in index)
    check("Roth i18n has no old optimizer wording",
          "Roth conversion optimizer" not in app
          and "Optimize conversions" not in app
          and "Best conversion" not in app
          and "Best after-tax terminal P50" not in app
          and 'tt("最优"' not in app)
    check("Roth selected-point labels are bilingual",
          "已选档位" in app and "selected grid point" in app
          and "已选年转换" in app and "Selected annual conversion" in app
          and "已选档位税后终值 P50" in app
          and "Selected point after-tax terminal P50" in app)
    check("Roth x-axis names the requested base annual conversion",
          "请求的基准年转换额" in app and "requested base annual conversion" in app)


def check_destination_catalog_source():
    """Behavior-neutral split: catalog ownership and classic load order."""
    catalog_path = os.path.join(ROOT, "web", "destination_catalog.js")
    app_path = os.path.join(ROOT, "web", "app.js")
    index_path = os.path.join(ROOT, "web", "index.html")
    catalog = open(catalog_path, encoding="utf-8").read()
    app = open(app_path, encoding="utf-8").read()
    index = open(index_path, encoding="utf-8").read()
    check("destination catalog module exists", os.path.isfile(catalog_path))
    check("catalog exports the three static references",
          all(token in catalog for token in (
              "const DEST_VINTAGE", "const REGIONS", "const DEST",
              "global.FIREDestinationCatalog")))
    check("app no longer owns destination declarations",
          "const DEST_VINTAGE" not in app and "const REGIONS" not in app
          and "const DEST =" not in app)
    check("catalog loads synchronously before app.js",
          index.index('src="destination_catalog.js"')
          < index.index('src="app.js"')
          and 'type="module"' not in index)
    check("app consumes and deletes the transient catalog namespace",
          "delete window.FIREDestinationCatalog" in app)


def wait_server(timeout=20, url=None):
    target = url or (URL + "api/presets")
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            urllib.request.urlopen(target, timeout=1)
            return True
        except Exception:
            time.sleep(0.4)
    return False


RESULTS = []
#: Observations about defects that are known and *not* fixed. These are reported
#: separately and are never counted as passing acceptance checks, because that is
#: how "118/118" came to include two checks that passed only while the product was
#: broken: they asserted a latch, so closing the latch would have failed them and
#: leaving it open kept the number clean. A count that rewards a defect is worse
#: than no count.
OPEN_BLOCKERS = []


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name + (f"  [{detail}]" if detail and not ok else ""))


def note_open_blocker(name, still_open, detail=""):
    """Record a known-open defect without letting it score.

    `still_open` true means the defect is still present: reported, and the run
    will exit nonzero, because a release gate must not be green while a known
    Phase 0 blocker is open. `still_open` false means it has been closed
    elsewhere and this observation is now stale and should be deleted — also
    reported, and also not a pass.
    """
    OPEN_BLOCKERS.append((name, bool(still_open), detail))
    print(("  OPEN  " if still_open else "  STALE ") + name
          + (f"  [{detail}]" if detail else ""))


def js(window, code):
    """Evaluate and return; swallow None-window races."""
    return window.evaluate_js(code)


def wait_js(window, code, timeout, poll=0.5):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if js(window, code):
            return True
        time.sleep(poll)
    return False


def drive(window):
    try:
        time.sleep(2.5)                                     # first paint
        js(window, "localStorage.clear()")
        js(window, "location.reload()")
        time.sleep(2.5)

        # ---- Phase 0D: raw localStorage envelope helper in real WKWebView --
        # The migration bridge is intentionally not part of init.  Exercise
        # only its explicit read/hash helper and prove it does not mutate the
        # source keys or enumerate unrelated UI preferences.
        migration_raw = '{"v":2,"config":{"secret":"中文💰"}}'
        migration_raw_js = json.dumps(migration_raw, ensure_ascii=False)
        expected_hash = hashlib.sha256(migration_raw.encode("utf-8")).hexdigest()
        js(window, f'localStorage.clear(); localStorage.setItem("fire_lang", "en"); localStorage.setItem("fire_theme", "dark"); localStorage.setItem("fire_draft", {migration_raw_js});')
        before = js(window, 'JSON.stringify([localStorage.length, localStorage.getItem("fire_draft"), localStorage.getItem("fire_plans_v1"), localStorage.getItem("fire_lang"), localStorage.getItem("fire_theme")])')
        js(window, 'window.__migrationProbe = null; FIREMigration.readEnvelope().then(x => window.__migrationProbe = JSON.stringify(x)).catch(e => window.__migrationProbe = "ERR:" + e.message)')
        ready = wait_js(window, 'window.__migrationProbe || ""', timeout=8)
        raw_probe = js(window, 'window.__migrationProbe || ""') or ""
        try:
            migration_probe = json.loads(raw_probe) if ready and not raw_probe.startswith("ERR:") else {}
        except Exception:
            migration_probe = {}
        check("migration helper available in WKWebView", bool(migration_probe))
        by_key = {item.get("key"): item for item in migration_probe.get("entries", [])}
        check("migration envelope uses known UTF-8 digest", by_key.get("fire_draft", {}).get("raw_sha256") == expected_hash)
        check("migration envelope preserves missing key", by_key.get("fire_plans_v1", {}).get("present") is False)
        js(window, 'window.__migrationPreview = null; FIREMigration.preview().then(x => window.__migrationPreview = JSON.stringify(x)).catch(e => window.__migrationPreview = "ERR:" + e.message)')
        preview_ready = wait_js(window, 'window.__migrationPreview || ""', timeout=8)
        raw_preview = js(window, 'window.__migrationPreview || ""') or ""
        try:
            migration_preview = json.loads(raw_preview) if preview_ready and not raw_preview.startswith("ERR:") else {}
        except Exception:
            migration_preview = {}
        check("migration helper reaches server preview", bool(migration_preview.get("envelope_sha256")))
        check("migration preview reports clean draft", migration_preview.get("reconciliation", {}).get("outcome") == "clean")
        check("migration read/preview is non-mutating", before == js(window, 'JSON.stringify([localStorage.length, localStorage.getItem("fire_draft"), localStorage.getItem("fire_plans_v1"), localStorage.getItem("fire_lang"), localStorage.getItem("fire_theme")])'))
        js(window, "localStorage.clear(); location.reload()")
        time.sleep(2.5)

        # ---- M4: canonical envelope, real-parser cross-check, fence gate ----
        # The formal envelope is built in JS and parsed in Python.  A one-byte
        # escaping difference would stay invisible until cutover, where the
        # digest comparison decides whether the migration may finalize at all.
        # So check it three independent ways: against the frozen goldens, then
        # through a real localStorage round-trip, then against the server's own
        # parser.  This runs in WKWebView because that is the engine that ships.
        vectors = json.load(open(os.path.join(ROOT, "tests", "formal_migration_vectors.json"),
                                 encoding="utf-8"))
        js(window, "window.__vecs = " + json.dumps(vectors, ensure_ascii=False) + ";")
        js(window, """
          window.__golden = null;
          (async () => {
            const out = [];
            for (const v of window.__vecs) {
              out.push({name: v.name,
                        canonical: FIRELegacyStore.canonicalText(v.envelope) === v.canonical,
                        digest: (await FIRELegacyStore.digest(v.envelope)) === v.envelope_sha256});
            }
            window.__golden = JSON.stringify(out);
          })().catch(e => window.__golden = "ERR:" + e.message);
        """)
        wait_js(window, 'window.__golden || ""', timeout=10)
        raw_golden = js(window, 'window.__golden || ""') or ""
        try:
            golden = json.loads(raw_golden) if not raw_golden.startswith("ERR:") else []
        except Exception:
            golden = []
        check("envelope goldens evaluated in WKWebView", len(golden) == len(vectors), raw_golden[:200])
        for row in golden:
            check("canonical envelope matches golden [%s]" % row["name"], row["canonical"])
            check("envelope digest matches golden [%s]" % row["name"], row["digest"])

        # Round-trip the hardest vector (emoji + NUL) through the real keys and
        # hand the JS-built envelope to the server parser.  If the two sides
        # disagree about a byte, preview returns a different sha256 or rejects.
        hard = [v for v in vectors if v["name"] == "unicode_emoji_nul_and_json_text"][0]
        draft_val = json.dumps(hard["envelope"]["keys"][0]["value"], ensure_ascii=False)
        plans_val = json.dumps(hard["envelope"]["keys"][1]["value"], ensure_ascii=False)
        js(window, "localStorage.clear();"
                   "localStorage.setItem('fire_draft', %s);"
                   "localStorage.setItem('fire_plans_v1', %s);" % (draft_val, plans_val))
        js(window, """
          window.__rt = null;
          (async () => {
            const env = await FIRELegacyStore.readEnvelope();
            const digest = await FIRELegacyStore.digest(env);
            const cap = (await (await fetch("/api/capability")).json()).capability;
            const p = await (await fetch("/api/migration/preview", {
              method: "POST",
              headers: {"Content-Type": "application/json", "X-FIRE-Capability": cap},
              body: JSON.stringify({envelope: env}),
            })).json();
            window.__rt = JSON.stringify({digest, server: p.envelope_sha256 || null, err: p.error || null});
          })().catch(e => window.__rt = "ERR:" + e.message);
        """)
        wait_js(window, 'window.__rt || ""', timeout=12)
        raw_rt = js(window, 'window.__rt || ""') or ""
        try:
            rt = json.loads(raw_rt) if not raw_rt.startswith("ERR:") else {}
        except Exception:
            rt = {}
        check("localStorage round-trip reproduces the golden digest",
              rt.get("digest") == hard["envelope_sha256"], raw_rt[:220])
        check("server parser agrees with the JS-built envelope",
              bool(rt.get("server")) and rt.get("server") == rt.get("digest"), raw_rt[:220])

        # The gate itself.  For each authority state prove the write is refused
        # AND that nothing reached localStorage — a refusal that still writes
        # would be worse than having no gate at all.
        gate_rows = []
        for status, refusal in (("sqlite_preferred", "sqlite_authoritative"),
                                ("source_changed", "source_changed"),
                                ("manual_recovery_required", "manual_recovery_required")):
            js(window, 'localStorage.setItem("fire_draft", "SENTINEL");'
                       'FIRELegacyStore.authority().fence = null;'
                       'FIRELegacyStore.authority().status = %s;' % json.dumps(status))
            gate_rows.append((status, refusal, json.loads(js(window,
                'JSON.stringify([FIRELegacyStore.writeDraft("MUTATED").code,'
                ' localStorage.getItem("fire_draft")])'))))
        for status, refusal, row in gate_rows:
            check("write refused under %s" % status, row[0] == refusal, str(row))
            check("refused write leaves storage untouched under %s" % status,
                  row[1] == "SENTINEL", str(row))

        js(window, 'localStorage.setItem("fire_draft", "SENTINEL");'
                   'FIRELegacyStore.authority().status = "legacy_authoritative";'
                   'FIRELegacyStore.authority().fence = {fence_state: "held",'
                   ' page_instance_id: "some-other-page"};')
        fenced = json.loads(js(window,
            'JSON.stringify([FIRELegacyStore.writeDraft("MUTATED").code,'
            ' localStorage.getItem("fire_draft")])'))
        check("a held fence blocks the legacy writer", fenced[0] == "migration_fenced", str(fenced))
        check("fenced write leaves storage untouched", fenced[1] == "SENTINEL", str(fenced))
        js(window, 'FIRELegacyStore.authority().fence = null;')
        allowed = json.loads(js(window,
            'JSON.stringify([FIRELegacyStore.writeDraft("MUTATED").ok,'
            ' localStorage.getItem("fire_draft")])'))
        check("legacy write proceeds once the fence is gone",
              allowed[0] is True and allowed[1] == "MUTATED", str(allowed))

        # Startup ordering: init() must reach the authority seam before any
        # plan or draft is read, and must never infer authority locally.
        js(window, "localStorage.clear(); location.reload()")
        time.sleep(2.5)
        try:
            auth = json.loads(js(window, "JSON.stringify(FIRELegacyStore.authority())") or "{}")
        except Exception:
            auth = {}
        check("startup read reached the authority seam", auth.get("seamReachable") is True, str(auth))
        check("startup authority is legacy before any cutover",
              auth.get("status") == "legacy_authoritative", str(auth))
        check("page fence id is issued per page instance",
              bool(js(window, "FIRELegacyStore.pageInstanceId()")))

        js(window, "localStorage.clear(); location.reload()")
        time.sleep(2.5)

        # ---- flow 2: wizard walk -> precision ----
        js(window, 'document.getElementById("startFresh").click()')

        # ---- E37: the governmental 457(b) is a field a user can fill ----
        # Driven rather than grepped: the whole arc from E34 to E37 was about
        # an account that existed everywhere except where somebody could use
        # it, and four of the five defects were silent.
        b457 = '.field[data-path="initial.gov_457b"] input[type="number"]'
        for _ in range(8):
            if js(window, '!!document.querySelector(%r)' % b457):
                break
            js(window, 'document.getElementById("wizNext").click()')
            time.sleep(0.2)
        check("the wizard has a governmental 457(b) balance field",
              bool(js(window, '!!document.querySelector(%r)' % b457)))
        rows_js = ('[...document.querySelectorAll("#wizHoldings tr")]'
                   '.map(r=>r.textContent).join("|")')
        before_rows = js(window, rows_js) or ""
        js(window, 'const b=document.querySelector(%r);'
                   ' b.value="150000";'
                   ' b.dispatchEvent(new Event("input", {bubbles:true}));' % b457)
        time.sleep(0.4)
        after_rows = js(window, rows_js) or ""
        # The TOTAL is the claim, not the row: three of the six places the
        # page listed the buckets are sums, and missing one understates net
        # worth. Measured: $205K before, $355K after, on a $150,000 entry.
        check("a 457(b) balance reaches the sidebar's simulated total",
              before_rows != after_rows and "$150K" in after_rows
              and "$205K" in before_rows and "$355K" in after_rows,
              (before_rows[-160:], after_rows[-160:]))
        js(window, 'const b=document.querySelector(%r);'
                   ' b.value="0";'
                   ' b.dispatchEvent(new Event("input", {bubbles:true}));' % b457)
        time.sleep(0.3)
        js(window, 'document.getElementById("startFresh").click()')

        # ---- E33: the sidebar's savings figure is the ENGINE's ----
        # This is the seam that failed. The page used to compute the figure
        # itself, and at a $45,000 salary it said the household saved 87% of
        # gross while the engine had them $3,662 a year short. Both sides had
        # tests; nobody tested that the two numbers were the same number.
        cell = 'document.getElementById("wizSavingsCell")'
        check("the wizard sidebar has a savings cell", bool(js(window, f'!!{cell}')))
        wait_js(window, f'{cell} && /[0-9]/.test({cell}.textContent)', timeout=12)
        savings_text = js(window, f'{cell} ? {cell}.textContent : ""') or ""
        check("the sidebar savings figure arrives from the server",
              bool(re.search(r"[0-9]", savings_text)), savings_text)
        # Find the savings-mode control, stepping forward until it appears.
        mode_sel = '.field[data-path="contributions.savings_mode"] select'
        for _ in range(8):
            if js(window, f'!!document.querySelector({mode_sel!r})'):
                break
            js(window, 'document.getElementById("wizNext").click()')
            time.sleep(0.2)
        check("the savings-mode control is reachable in the wizard",
              bool(js(window, f'!!document.querySelector({mode_sel!r})')))

        def savings_at(rate):
            js(window, f"""const m=document.querySelector({mode_sel!r});
              m.value="savings_rate"; m.dispatchEvent(new Event("change", {{bubbles:true}}));""")
            time.sleep(0.3)
            js(window, f"""const r=document.querySelector('.field[data-path="contributions.savings_rate"] input');
              if (r) {{ r.value="{rate}"; r.dispatchEvent(new Event("input", {{bubbles:true}})); }}""")
            # The request is debounced, so poll rather than sleep a guess.
            time.sleep(1.2)
            return js(window, f'{cell} ? {cell}.textContent : ""') or ""

        low, high = savings_at(10), savings_at(50)
        # The whole defect in one assertion: this used to return the same
        # number for both, because the page's own function never read
        # `savings_mode` at all -- the control the user had just moved did
        # nothing to the figure beside it.
        check("moving the savings-rate control moves the sidebar figure",
              low != high and bool(re.search(r"[0-9]", low)) and bool(re.search(r"[0-9]", high)),
              f"{low!r} vs {high!r}")
        js(window, f"""const m=document.querySelector({mode_sel!r});
          m.value="residual"; m.dispatchEvent(new Event("change", {{bubbles:true}}));""")
        time.sleep(0.3)
        js(window, 'document.getElementById("startFresh").click()')
        for _ in range(3):
            js(window, 'document.getElementById("wizNext").click()')
            time.sleep(0.15)
        premium_path = '.field[data-path="medical.premium_aca"]'
        check("medical premium uses the existing single numeric control",
              (js(window, f'document.querySelectorAll(\'{premium_path} input[type="number"]\').length') or 0) == 1)
        premium_guidance = js(window, f'document.querySelector(\'{premium_path}\').textContent') or ""
        check("medical premium reminds users to refresh after open enrollment",
              "开放投保期" in premium_guidance and "地点、年龄或覆盖家庭变化" in premium_guidance,
              premium_guidance[:240])
        js(window, f'''const p=document.querySelector('{premium_path} input[type="number"]');
          p.value="12345"; p.dispatchEvent(new Event("input", {{bubbles:true}}));''')
        check("editing the premium confirms user-quote provenance",
              js(window, f'document.querySelector(\'{premium_path} [data-medical-premium-confirm]\').checked') is True)
        js(window, f'''const c=document.querySelector('{premium_path} [data-medical-premium-confirm]');
          c.checked=false; c.dispatchEvent(new Event("change", {{bubbles:true}}));''')
        check("clearing quote confirmation preserves the numeric anchor",
              js(window, f'document.querySelector(\'{premium_path} input[type="number"]\').value') == "12345")
        js(window, 'document.getElementById("wizSave").click()')
        time.sleep(0.8)
        js(window, 'location.reload()')
        time.sleep(2.5)
        js(window, 'document.getElementById("resumeDraft").click()')
        for _ in range(3):
            js(window, 'document.getElementById("wizNext").click()')
            time.sleep(0.15)
        check("unconfirmed premium survives save and reload without becoming zero",
              js(window, f'document.querySelector(\'{premium_path} input[type="number"]\').value') == "12345"
              and js(window, f'document.querySelector(\'{premium_path} [data-medical-premium-confirm]\').checked') is False)
        js(window, f'''const p=document.querySelector('{premium_path} input[type="number"]');
          p.value="23456"; p.dispatchEvent(new Event("input", {{bubbles:true}}));''')
        for _ in range(3):
            js(window, 'document.getElementById("wizNext").click()')
            time.sleep(0.15)
        annual_path = '.field[data-path="medical.annual_trajectory_enabled"]'
        delta_path = '.field[data-path="medical.cpi_delta_routine"]'
        check("annual medical trajectory is visibly opt-in",
              js(window, f'document.querySelector(\'{annual_path} input[type="checkbox"]\').checked') is False)
        js(window, f'''const c=document.querySelector('{annual_path} input[type="checkbox"]');
          c.checked=true; c.dispatchEvent(new Event("change", {{bubbles:true}}));''')
        js(window, f'''const d=document.querySelector('{delta_path} input[type="number"]');
          d.value="0"; d.dispatchEvent(new Event("input", {{bubbles:true}}));''')
        check("annual medical accepts an explicit zero spread",
              js(window, f'document.querySelector(\'{delta_path} input[type="number"]\').value') == "0")
        js(window, f'''const d=document.querySelector('{delta_path} input[type="number"]');
          d.value=""; d.dispatchEvent(new Event("input", {{bubbles:true}}));''')
        check("annual medical keeps blank visibly invalid rather than writing zero",
              js(window, f'document.querySelector(\'{delta_path} input[type="number"]\').classList.contains("invalid")') is True)
        js(window, f'''const d=document.querySelector('{delta_path} input[type="number"]');
          d.value="0"; d.dispatchEvent(new Event("input", {{bubbles:true}}));''')
        for _ in range(2):
            js(window, 'document.getElementById("wizNext").click()')
            time.sleep(0.15)
        check("wizard walk reaches precision",
              js(window, '[...document.querySelectorAll(".view")].find(v=>v.classList.contains("show")).id') == "v-precision")
        check("seed input present", js(window, '!!document.getElementById("seedInput")'))

        # ---- flow 3: save plan -> welcome list ----
        js(window, 'document.getElementById("precPrev").click()')
        js(window, 'document.getElementById("wizSavePlan").click()')
        js(window, 'document.getElementById("restartBtn").click()')
        time.sleep(0.4)
        check("saved plan listed on welcome",
              (js(window, 'document.querySelectorAll("#plansList .plan-row").length') or 0) >= 1)
        js(window, 'document.querySelector("#plansList .plan-row [data-a=open]").click()')
        for _ in range(3):
            js(window, 'document.getElementById("wizNext").click()')
            time.sleep(0.15)
        check("confirmed premium survives plan save and open",
              js(window, f'document.querySelector(\'{premium_path} input[type="number"]\').value') == "23456"
              and js(window, f'document.querySelector(\'{premium_path} [data-medical-premium-confirm]\').checked') is True)
        for _ in range(3):
            js(window, 'document.getElementById("wizNext").click()')
            time.sleep(0.15)
        check("annual medical opt-in and explicit zero survive plan save and open",
              js(window, f'document.querySelector(\'{annual_path} input[type="checkbox"]\').checked') is True
              and js(window, f'document.querySelector(\'{delta_path} input[type="number"]\').value') == "0")
        js(window, 'document.getElementById("restartBtn").click()')
        time.sleep(0.3)

        # ---- flow 6: couple mode is a first-class path ----
        js(window, 'document.getElementById("startFresh").click()')
        js(window, 'const sel = document.querySelector(\'.field[data-path="household.enabled"] select\'); sel.value = "true"; sel.dispatchEvent(new Event("change"))')
        time.sleep(0.3)
        check("couple: spouse basics appear",
              js(window, '!!document.querySelector(\'.field[data-path="household.spouse_age_offset"]\') && getComputedStyle(document.querySelector(\'.field[data-path="household.spouse_age_offset"]\')).display !== "none"'))
        js(window, 'document.getElementById("wizNext").click()')     # -> portfolio
        time.sleep(0.2)
        check("couple: side-by-side balances",
              js(window, 'getComputedStyle(document.querySelector(\'.field[data-path="household.spouse_initial_pretax"]\')).display !== "none"'))
        check("couple: sidebar shows spouse rows",
              "配偶" in (js(window, 'document.getElementById("wizHoldings").textContent') or ""))
        js(window, 'document.getElementById("wizNext").click()')     # -> income
        time.sleep(0.2)
        check("couple: spouse earner block in income step",
              js(window, 'getComputedStyle(document.querySelector(\'.field[data-path="household.spouse_base_salary_pre"]\')).display !== "none"'))

        # Structured income is edited through real controls, then survives the
        # same legacy-draft round trip an old downloadable app uses. Re-query
        # after every change: each control rebuilds the step and invalidates the
        # previous DOM node.
        js(window, '''["pension","rental","parttime","equity"].forEach(kind => {
          const box = document.querySelector(`.field[data-path="income_streams.${kind}_enabled"] input`);
          if (!box.checked) { box.checked = true; box.dispatchEvent(new Event("change")); }
        })''')
        time.sleep(0.3)
        owner_options = json.loads(js(
            window,
            '''JSON.stringify(["pension","rental","parttime","equity"].map(kind =>
              [...document.querySelector(`.field[data-path="income_streams.${kind}_owner"] select`).options]
                .map(option => option.value)))''') or "[]")
        expected_owner_options = [
            "unspecified", "household", "primary", "spouse"]
        check("income owner controls expose the exact four-value contract",
              len(owner_options) == 4
              and all(values == expected_owner_options
                      for values in owner_options),
              str(owner_options))
        check("income owner controls default to unconfirmed",
              js(window, '''["pension","rental","parttime","equity"].every(kind =>
                document.querySelector(`.field[data-path="income_streams.${kind}_owner"] select`).value === "unspecified")'''))
        js(window, '''Object.entries({pension:"primary", rental:"household",
          parttime:"spouse", equity:"unspecified"}).forEach(([kind, value]) => {
            const sel = document.querySelector(`.field[data-path="income_streams.${kind}_owner"] select`);
            sel.value = value; sel.dispatchEvent(new Event("change"));
          })''')
        js(window, 'document.getElementById("wizSave").click()')
        time.sleep(0.5)
        js(window, "location.reload()")
        time.sleep(2.5)
        js(window, 'document.getElementById("resumeDraft").click()')
        js(window, 'document.getElementById("wizNext").click()')
        js(window, 'document.getElementById("wizNext").click()')
        restored_owners = json.loads(js(
            window,
            '''JSON.stringify(["pension","rental","parttime","equity"].map(kind =>
              document.querySelector(`.field[data-path="income_streams.${kind}_owner"] select`).value))''') or "[]")
        check("income ownership survives a real draft reload",
              restored_owners == [
                  "primary", "household", "spouse", "unspecified"],
              str(restored_owners))
        js(window, 'document.getElementById("wizNext").click()')  # -> assumptions / SSA import
        check("SSA import control is present in the real WebView",
              bool(js(window, 'document.getElementById("ssaiFile")')))
        ssa_rows = "".join(
            f'<osss:Earnings startYear="{year}" endYear="{year}">'
            f'<osss:FicaEarnings>{amount}</osss:FicaEarnings>'
            f'<osss:MedicareEarnings>{amount}</osss:MedicareEarnings>'
            f'</osss:Earnings>'
            for year, amount in ((2018, 120000), (2019, 120000), (2020, 120000)))
        ssa_xml = (
            '<osss:OnlineSocialSecurityStatementData xmlns:osss="http://ssa.gov/osss">'
            '<osss:UserInformation><osss:DateOfBirth>1996-05-01</osss:DateOfBirth>'
            f'</osss:UserInformation><osss:EarningsRecord>{ssa_rows}'
            '</osss:EarningsRecord></osss:OnlineSocialSecurityStatementData>')

        # Exercise the production SSA rendering path through a real file input.
        # The fetch shim only changes the response receipt; parser output and
        # all other UI behavior still come from the bundled local server.
        js(window, """
          window.__fireSsaMutation = null;
          if (!window.__fireSsaOriginalFetch) {
            window.__fireSsaOriginalFetch = window.fetch.bind(window);
            window.fetch = async function(input, init) {
              const response = await window.__fireSsaOriginalFetch(input, init);
              const url = typeof input === "string" ? input : ((input && input.url) || "");
              if (!String(url).endsWith("/api/import_ssa")) return response;
              const payload = await response.json();
              const mutation = window.__fireSsaMutation;
              if (payload && payload.rule_pack && mutation === "prefix") {
                payload.rule_pack.pack_id = "us-offline-0000000000000000";
              }
              if (payload && payload.rule_pack && mutation === "date") {
                payload.rule_pack.evaluated_on = "2027-01-01";
              }
              if (payload && payload.rule_pack && mutation === "partial") {
                payload.rule_pack.component.label = "";
              }
              window.__fireSsaMutation = null;
              return {ok: response.ok, status: response.status,
                json: async () => payload};
            };
          }
        """)

        def ssa_upload(mutation):
            js(window, "window.__fireSsaMutation = " + json.dumps(mutation) + ";"
                       "const input = document.getElementById('ssaiFile');"
                       "const dt = new DataTransfer();"
                       "dt.items.add(new File([" + json.dumps(ssa_xml, ensure_ascii=False) + "],"
                       "'statement.xml', {type: 'application/xml'}));"
                       "input.files = dt.files;"
                       "input.dispatchEvent(new Event('change', {bubbles: true}));")
            wait_js(window, 'window.__fireSsaMutation === null && !!document.querySelector("#ssaiResult .hint")', timeout=15)
            return js(window, 'document.getElementById("ssaiResult").textContent || ""') or ""

        valid_ssa = ssa_upload("valid")
        check("valid SSA receipt renders its frozen current status",
              "current" in valid_ssa, valid_ssa[:220])
        bad_ssa_hash = ssa_upload("prefix")
        check("SSA hash/prefix inconsistency is unrecorded",
              "rule pack 未记录" in bad_ssa_hash or "rule pack unrecorded" in bad_ssa_hash,
              bad_ssa_hash[:220])
        bad_ssa_date = ssa_upload("date")
        check("SSA date/status inconsistency is unrecorded",
              "rule pack 未记录" in bad_ssa_date or "rule pack unrecorded" in bad_ssa_date,
              bad_ssa_date[:220])
        bad_ssa_partial = ssa_upload("partial")
        check("SSA partial receipt is unrecorded",
              "rule pack 未记录" in bad_ssa_partial or "rule pack unrecorded" in bad_ssa_partial,
              bad_ssa_partial[:220])

        js(window, 'document.getElementById("restartBtn").click()')
        time.sleep(0.3)

        # Compatibility is not allowed to depend on Python normalization: old
        # browser drafts can contain an explicit null (or no leaf at all), so
        # exercise both through the actual WKWebView loader.
        js(window, 'document.getElementById("restartBtn").click()')
        legacy_owner_draft = {
            "v": 2,
            "config": {
                "income_streams": {
                    "pension_enabled": True,
                    "pension_annual_real": 1000,
                    "pension_owner": None,
                    "rental_enabled": True,
                    "rental_annual_net_real": 1000,
                },
            },
        }
        js(window, "FIRELegacyStore.writeDraft(" + json.dumps(
            json.dumps(legacy_owner_draft, ensure_ascii=False)) + ")")
        js(window, "location.reload()")
        time.sleep(2.5)
        js(window, 'document.getElementById("resumeDraft").click()')
        js(window, 'document.getElementById("wizNext").click()')
        js(window, 'document.getElementById("wizNext").click()')
        compatible_owners = json.loads(js(
            window,
            '''JSON.stringify(["pension","rental"].map(kind =>
              document.querySelector(`.field[data-path="income_streams.${kind}_owner"] select`).value))''') or "[]")
        check("old null and missing owners normalize in the browser",
              compatible_owners == ["unspecified", "unspecified"],
              str(compatible_owners))
        js(window, 'document.getElementById("restartBtn").click()')
        time.sleep(0.3)

        # ---- flow 7: Roadmap 5.0's new dials actually reach the engine ----
        # `test_ui_server_seams` already proves each control PATH is a real
        # config leaf. What it cannot see is whether typing into the box moves
        # the config that gets POSTED -- which is exactly the gap the decision
        # panel shipped through: the call was there, the payload was not, and
        # both sides' tests were green.
        js(window, 'localStorage.clear(); location.reload()')
        time.sleep(2.5)
        js(window, 'document.getElementById("startFresh").click()')
        time.sleep(0.4)
        for _ in range(3):
            js(window, 'document.getElementById("wizNext").click()')
            time.sleep(0.2)

        check("the 5.0 dials render on the assumptions step",
              js(window, '["blocky_spending.enabled", "ss_trust_fund.enabled"]'
                         '.every(p => !!document.querySelector('
                         '`.field[data-path="${p}"]`))'))

        # Off by default, dependants hidden: a plan that does not use these
        # should not be asked about them.
        # Absent OR display:none. With the module off the field is not
        # rendered at all, and an earlier version of this check assumed
        # "rendered but hidden" -- it crashed on null instead of failing,
        # which is a worse report than either outcome.
        check("the dependent dials stay away until the module is on",
              js(window, 'const f = document.querySelector('
                         '\'.field[data-path="ss_trust_fund.plan_start_year"]\');'
                         ' !f || getComputedStyle(f).display === "none"'))

        js(window, 'for (const p of ["blocky_spending.enabled",'
                   ' "ss_trust_fund.enabled"]) {'
                   ' const el = document.querySelector('
                   '`.field[data-path="${p}"] input[type="checkbox"]`);'
                   ' el.checked = true;'
                   ' el.dispatchEvent(new Event("change", {bubbles: true})); }')
        time.sleep(0.3)

        check("turning the trust fund on reveals the calendar anchor it needs",
              js(window, 'const f = document.querySelector('
                         '\'.field[data-path="ss_trust_fund.plan_start_year"]\');'
                         ' !!f && getComputedStyle(f).display !== "none"'))

        # The anchor arrives EMPTY. A prefilled year would be the app guessing
        # the calendar, which is the one thing this module refuses to do.
        check("the calendar anchor arrives empty rather than guessed",
              js(window, 'document.querySelector('
                         '\'.field[data-path="ss_trust_fund.plan_start_year"]'
                         ' input\').value === ""'))

        # The percent round-trip, read off the real controls: a config value
        # of 0.15 must show as 15, not 0.15 and not 15%. This is the one
        # failure mode the seam gate cannot see -- it checks that a control's
        # PATH is a real config leaf, not that the number is scaled right --
        # and getting it wrong would quietly run every plan at a hundredth of
        # what the user typed.
        check("the percent dials show percents, not fractions",
              js(window, 'const v = p => document.querySelector('
                         '`.field[data-path="${p}"] input`).value;'
                         ' v("blocky_spending.annual_probability") === "15"'
                         ' && v("blocky_spending.size_fraction") === "35"'),
              js(window, 'document.querySelector(\'.field[data-path='
                         '"blocky_spending.annual_probability"] input\').value'))

        check("the scenario select offers exactly the report's alternatives",
              js(window, 'const o = [...document.querySelector('
                         '\'.field[data-path="ss_trust_fund.scenario"] select\')'
                         '.options].map(x => x.value);'
                         ' o.length === 2 && o.includes("intermediate")'
                         ' && o.includes("range")'))

        # A dial gated on a NUMBER, not on a checkbox. Only checkboxes and
        # selects rebuilt the step, so typing an HSA amount left the plan in a
        # state the server refuses -- "contributions.hsa_coverage_tier must be
        # 'self_only' or 'family' for a non-zero HSA contribution" -- with
        # every control that could answer it absent from the DOM. Twenty-one
        # controls sat behind three numeric gates; ten of them here. It
        # shipped in v10.0-installed-30 and was found by typing into the page,
        # so the check types too.
        js(window, 'for (const b of document.querySelectorAll("#wizardRail button"))'
                   ' { if (/收入与储蓄|Income/.test(b.textContent)) { b.click(); break; } }')
        time.sleep(0.4)
        check("an HSA amount is a numeric gate and its facts are hidden first",
              js(window, 'const f = document.querySelector('
                         '\'.field[data-path="contributions.hsa_coverage_tier"]\');'
                         ' !f || getComputedStyle(f).display === "none"'))
        js(window, 'const el = document.querySelector('
                   '\'.field[data-path="contributions.hsa_limit_y1"] input\');'
                   ' el.value = "4400";'
                   ' el.dispatchEvent(new Event("input", {bubbles: true}));'
                   ' el.dispatchEvent(new Event("change", {bubbles: true}));')
        time.sleep(0.4)
        for path in ("contributions.hsa_coverage_tier",
                     "contributions.hsa_deductible_y1",
                     "contributions.hsa_out_of_pocket_max_y1",
                     "contributions.hsa_eligible_through_age"):
            check("typing an HSA amount reveals %s" % path.split(".")[-1],
                  js(window, 'const f = document.querySelector('
                             '`.field[data-path="%s"]`);'
                             ' !!f && getComputedStyle(f).display !== "none"'
                             % path))
        # And the revealed money/age boxes arrive EMPTY, not at 0 -- their
        # defaults are null and 0 would be an answer nobody gave (U39).
        for path in ("contributions.hsa_deductible_y1",
                     "contributions.hsa_eligible_through_age"):
            check("the revealed %s is blank, not a zero" % path.split(".")[-1],
                  js(window, 'const i = document.querySelector('
                             '`.field[data-path="%s"] input`);'
                             ' !!i && i.value === ""' % path))
        js(window, 'const el = document.querySelector('
                   '\'.field[data-path="contributions.hsa_limit_y1"] input\');'
                   ' el.value = "0";'
                   ' el.dispatchEvent(new Event("input", {bubbles: true}));'
                   ' el.dispatchEvent(new Event("change", {bubbles: true}));')
        time.sleep(0.4)

        js(window, 'document.getElementById("restartBtn").click()')
        time.sleep(0.3)

        # ---- flow 4: help language toggle ----
        js(window, 'document.querySelector(\'#langToggle [data-lang="zh"]\').click()')
        js(window, 'document.getElementById("helpBtn").click()')
        zh = js(window, 'document.querySelector("#helpBody .panel-title").textContent')
        js(window, 'document.querySelector(\'#langToggle [data-lang="en"]\').click()')
        en = js(window, 'document.querySelector("#helpBody .panel-title").textContent')
        check("help switches language immediately", zh != en and bool(zh) and bool(en),
              f"zh={zh!r} en={en!r}")
        js(window, 'document.querySelector(\'#langToggle [data-lang="zh"]\').click()')
        js(window, 'document.getElementById("helpBack").click()')

        # ---- flow 1: quick estimate -> results verdict ----
        js(window, 'document.getElementById("qIncome").value = 120000')
        js(window, 'document.getElementById("quickGo").click()')
        ok = wait_js(window,
                     '[...document.querySelectorAll(".view")].find(v=>v.classList.contains("show")).id === "v-results"',
                     timeout=60)
        check("quick estimate reaches results", ok)

        # The recap panel exists so a user can check what they told the model,
        # and nothing measured it. `initial.gov_457b` joined the array it reads
        # without a row of its own, so every label below shifted by one and a
        # user's Roth was shown as their HSA -- with a correct total, because
        # the total sums the array. Shipped in v10.0-installed-30 and found by
        # driving the page. This asserts the quick allocator's own split:
        # $150,000 -> 50% pre-tax, 15% Roth, 35% taxable, and no 457(b) or HSA
        # unless the user asked for one.
        recap = js(window, '(() => {const out = {};'
                   '[...document.querySelectorAll("#inputsRecapBody .recap-row")]'
                   '.forEach(r => { const k = (r.querySelector(".rk")||{}).textContent;'
                   ' const v = (r.querySelector(".rv")||{}).textContent;'
                   ' if (k) out[k.trim()] = (v||"").trim(); });'
                   'return JSON.stringify(out);})()') or "{}"
        recap = json.loads(recap)
        for label, expected in (("401(k) / IRA·pre", "$75,000"),
                                ("Roth", "$22,500"),
                                ("HSA", "$0"),
                                ("应税账户", "$52,500")):
            check("recap reports the %s balance the quick estimate built"
                  % label, recap.get(label) == expected,
                  "%s=%r" % (label, recap.get(label)))

        verdict = js(window, '(document.querySelector("#verdict .v-main")||{}).textContent || ""')
        check("verdict sentence rendered", len(verdict or "") > 20)
        check("verdict carries its sampling interval", "±" in (js(window, '(document.querySelector("#verdict .v-sub")||{}).textContent || ""') or ""))
        # The gauge used to have paths and a number in the DOM while the SVG
        # itself was 0×0 in WKWebView: it was a flex item with only max-width,
        # so WebKit collapsed its used size and painted nothing in the card.
        # DOM children and dash offsets both looked healthy, which is why the
        # earlier reveal checks could not see the user's blank gauge. Measure
        # the actual visible box on the real overview page.
        gauge_layout_raw = js(window, '''JSON.stringify((() => {
          const g = document.getElementById("gauge");
          const wrap = g && g.parentElement;
          const r = g && g.getBoundingClientRect();
          const wr = wrap && wrap.getBoundingClientRect();
          return {width: r ? r.width : 0, height: r ? r.height : 0,
                  wrapHeight: wr ? wr.height : 0,
                  paths: g ? g.querySelectorAll("path").length : 0,
                  text: (g && g.querySelector("text") || {}).textContent || ""};
        })())''') or "{}"
        try:
            gauge_layout = json.loads(gauge_layout_raw)
        except (TypeError, ValueError):
            gauge_layout = {}
        check("overview gauge occupies a visible WKWebView layout box",
              gauge_layout.get("width", 0) >= 200
              and gauge_layout.get("height", 0) >= 140
              and gauge_layout.get("wrapHeight", 0) >= 140
              and gauge_layout.get("paths", 0) >= 2
              and "%" in gauge_layout.get("text", ""),
              str(gauge_layout))
        # The tier in words, checked in the REAL page rather than in the
        # source: a colourblind reader's only way to know which of three
        # states this plan is in.
        tier_text = js(window, '(document.querySelector("#verdict .v-tier")||{}).textContent || ""') or ""
        check("verdict states its tier in words", tier_text.strip() != "")
        # Hover, driven rather than grepped: `attachHover` being present in
        # the source says nothing about a pointer producing a tooltip. The
        # tip element is created lazily on first hover, so its absence before
        # and presence after is the observable.
        js(window, 'document.querySelectorAll(".chart-tip").forEach(e => e.remove())')
        # The charts live on other result tabs, so a hover on the page the
        # smoke happens to be on finds only 16x16 icons and 0x0 elements with
        # no layout. My first version checked here and failed for that reason
        # -- the check was in the wrong place, not the feature. Walk the tabs
        # until a chart actually has a size.
        js(window, """(function(){
          var tabs = Array.prototype.slice.call(
            document.querySelectorAll("#resultTabs .rtab"));
          for (var i = 0; i < tabs.length; i++) {
            tabs[i].click();
            var big = Array.prototype.slice.call(document.querySelectorAll("svg"))
              .filter(function(e){ var r = e.getBoundingClientRect();
                                   return e.id !== "gauge" &&
                                          r.width > 200 && r.height > 80; });
            if (big.length) return "found:" + i;
          }
          return "none";
        })()""")
        moved = js(window, """(function(){
          var svgs = Array.prototype.slice.call(document.querySelectorAll("svg"));
          var svg = svgs.filter(function(e){ var r=e.getBoundingClientRect();
                                             if (e.id === "gauge") return false;
                                             return r.width > 200 && r.height > 80; })[0];
          if (!svg) return "no-svg:" + svgs.length;
          var r = svg.getBoundingClientRect();
          var ev = new PointerEvent("pointermove", {
            clientX: r.left + r.width * 0.5, clientY: r.top + r.height * 0.5,
            bubbles: true});
          svg.dispatchEvent(ev);
          var tip = document.querySelector(".chart-tip");
          return tip ? (tip.style.display || "") : "no-tip";
        })()""")
        check("a pointer over a chart produces a tooltip element",
              not str(moved).startswith("no-"), str(moved))
        check("verdict exposes the tier to assistive tech",
              (js(window, '(document.querySelector("#verdict")||{}).getAttribute && document.querySelector("#verdict").getAttribute("data-tier") || ""') or "") in ("strong", "watch", "fragile"))
        overview_pack = js(
            window,
            '(document.getElementById("rulePackStatus")||{}).textContent || ""') or ""
        pack_id_match = re.search(r"us-offline-[0-9a-f]{16}", overview_pack)
        check("headline result binds a current offline pack",
              "离线规则仍在应用维护窗口内" in overview_pack
              and pack_id_match is not None,
              overview_pack[:240])
        check("overview shows the exact result-bound pack receipt",
              pack_id_match is not None
              and "离线规则仍在应用维护窗口内" in overview_pack,
              overview_pack[:240])
        js(window, '([...document.querySelectorAll(".rtab")].find(b=>b.dataset.p==="concl")||{click(){}}).click()')
        conclusion_pack = js(
            window,
            '(document.getElementById("rulePackConclusionStatus")||{}).textContent || ""') or ""
        check("conclusions repeat the same current receipt",
              pack_id_match is not None and pack_id_match.group(0) in conclusion_pack
              and "离线规则仍在应用维护窗口内" in conclusion_pack,
              conclusion_pack[:240])

        # A real WKWebView mutation corpus for the result-bound validator. The
        # server still computes the run; this shim changes only the JSON receipt
        # returned to the browser, so an accepted mutation would be a product
        # rendering failure rather than a test-only helper result.
        js(window, """
          window.__fireResultMutation = null;
          if (!window.__fireResultOriginalFetch) {
            window.__fireResultOriginalFetch = window.fetch.bind(window);
            window.fetch = async function(input, init) {
              const response = await window.__fireResultOriginalFetch(input, init);
              const url = typeof input === "string" ? input : ((input && input.url) || "");
              if (!String(url).includes("/api/result?job=")) return response;
              const payload = await response.json();
              const rp = payload && payload.meta && payload.meta.rule_pack;
              const mutation = window.__fireResultMutation;
              if (rp && mutation === "prefix") {
                rp.pack_id = "us-offline-0000000000000000";
              } else if (rp && mutation === "date") {
                rp.evaluated_on = "2027-01-01";
              } else if (rp && mutation === "row") {
                const active = rp.components.find(c => c.applicability === "applicable");
                if (active) active.review_status = "within_recorded_window";
                const unused = rp.components.find(c => c.applicability === "not_used_at_run");
                if (unused) unused.status = "stale";
              } else if (rp && mutation === "array") {
                const ids = rp.applicable_component_ids || [];
                if (ids.length) ids.push(ids[0]);
                else rp.applicable_component_ids = [rp.components[0].id, rp.components[0].id];
              }
              window.__fireResultMutation = null;
              return {ok: response.ok, status: response.status,
                json: async () => payload};
            };
          }
        """)

        def tampered_result(mutation):
            js(window, "window.__fireResultMutation = " + json.dumps(mutation) + ";"
                       "document.getElementById('restartBtn').click();")
            time.sleep(0.3)
            js(window, 'document.getElementById("qIncome").value = 120000; document.getElementById("quickGo").click()')
            finished = wait_js(
                window,
                'window.__fireResultMutation === null && [...document.querySelectorAll(".view")].some(v=>v.classList.contains("show") && v.id === "v-results")',
                timeout=60)
            text_value = js(window, '(document.getElementById("rulePackStatus")||{}).textContent || ""') or ""
            check("WKWebView rejects malformed rule-pack receipt: " + mutation,
                  finished and ("规则年份未记录" in text_value
                                or "rule pack 未记录" in text_value
                                or "rule pack unrecorded" in text_value),
                  text_value[:240])

        for mutation in ("prefix", "date", "row", "array"):
            tampered_result(mutation)
        js(window, 'if (window.__fireResultOriginalFetch) window.fetch = window.__fireResultOriginalFetch;')

        # ---- flow 5: A/B ----
        js(window, 'document.getElementById("saveA").click()')
        js(window, 'document.getElementById("saveB").click()')
        time.sleep(0.3)
        js(window, '([...document.querySelectorAll(".rtab")].find(b=>b.dataset.p==="ab")||{click(){}}).click()')
        time.sleep(0.5)
        rows = js(window, 'document.querySelectorAll("#abTable tbody tr").length') or 0
        check("A/B compare table renders", rows >= 4, f"rows={rows}")

        # ---- flow 6: dynamic stress content remains localized ----
        js(window, '([...document.querySelectorAll(".rtab")].find(b=>b.dataset.p==="stress")||{click(){}}).click()')
        time.sleep(0.4)

        # B4 must cross the actual page seam, not merely prove that a button,
        # route and study each exist.  This exact missing-config gap kept the
        # decision panel broken for two releases while both sides tested green.
        js(window, 'document.getElementById("execRun").click()')
        execution_done = wait_js(
            window,
            '!document.getElementById("execRun").disabled && '
            'document.getElementById("execOut").textContent.trim().length > 0',
            timeout=120)
        execution_text = js(
            window,
            'document.getElementById("execOut").textContent || ""') or ""
        execution_rows = js(
            window,
            'document.querySelectorAll("#execOut tbody tr").length') or 0
        check("execution stress crosses current config, job, poll and result",
              execution_done and execution_rows == 4,
              execution_text[:360])
        check("execution stress renders numeric evidence without a verdict",
              "结果，不是裁定" in execution_text and "差异" in execution_text,
              execution_text[:360])
        check("execution stress renders the base-location proof boundary",
              "未搬迁的 base-location" in execution_text,
              execution_text[:360])

        # ---- Phase 1: Roth grid disclosure, before and after a run ----------
        # The endpoint is mocked only for this UI contract check.  The real
        # engine and endpoint shape are covered by the Roth audit; using eight
        # deterministic points here keeps the WKWebView test focused on what a
        # user is told, rather than spending another 8 x 1,500 paths.
        roth_zh_note = js(window, 'document.querySelector("[data-i18n=\\\"roth.note\\\"]").textContent || ""') or ""
        roth_zh_button = js(window, 'document.getElementById("rothRun").textContent || ""') or ""
        check("Roth pre-run Chinese disclosure states the fixed 8-level grid",
              "8 个年转换基准档" in roth_zh_note and "$0–$100k" in roth_zh_note,
              roth_zh_note[:240])
        check("Roth pre-run Chinese disclosure states global growth and points at the schedule panel",
              "全局增长率" in roth_zh_note and "哪几年转，转多少" in roth_zh_note,
              roth_zh_note[:240])
        check("Roth pre-run Chinese disclosure states objective and the ONE remaining limit",
              "三分支成功率" in roth_zh_note and "税后终值 P50" in roth_zh_note
              and "终值不再是平率清算代理" in roth_zh_note
              and "剩下的方向性只有一条" in roth_zh_note,
              roth_zh_note[:300])
        check("Roth pre-run Chinese control says compare, not optimize",
              "对比 8 个转换档位" in roth_zh_button and "优化" not in roth_zh_button,
              roth_zh_button)

        js(window, 'document.querySelector(\'#langToggle [data-lang="en"]\').click()')
        time.sleep(0.25)
        roth_en_note = js(window, 'document.querySelector("[data-i18n=\\\"roth.note\\\"]").textContent || ""') or ""
        roth_en_button = js(window, 'document.getElementById("rothRun").textContent || ""') or ""
        cjk = re.compile(r"[\u3400-\u9fff]")
        check("Roth pre-run English disclosure has the complete contract",
              all(phrase in roth_en_note for phrase in (
                  "8 tested base annual levels", "global rate",
                  "three-branch success", "unconditional after-tax terminal P50", "failed paths = 0",
                  "no longer a flat liquidation proxy",
                  "Coverage is the only directional limit left",
                  "which years to convert")),
              roth_en_note[:360])
        check("Roth pre-run English control says compare, not optimize",
              "Compare 8 conversion levels" in roth_en_button and "Optimize" not in roth_en_button,
              roth_en_button)
        check("Roth pre-run English disclosure contains no CJK",
              not cjk.search(roth_en_note + roth_en_button),
              (roth_en_note + " | " + roth_en_button)[:360])

        roth_points = [
            {"conversion": float(amount), "terminal_real_p50": 90_000 + i * 1_000,
             "terminal_after_tax_real_p50": 80_000 + i * 1_500,
             "lifetime_success": 0.70 + i * 0.01, "true_tax_p50": 10_000 - i * 100}
            for i, amount in enumerate((0, 12_000, 24_000, 36_000,
                                         48_000, 60_000, 80_000, 100_000))]
        roth_payload = {"n_paths": 12, "seed": 96_000,
                        "objective": "lifetime_success_then_unconditional_after_tax_terminal_p50",
                        "points": roth_points, "best": roth_points[3]}
        js(window, """
          window.__rothRealFetch = window.fetch.bind(window);
          window.fetch = function(input, init) {
            const url = typeof input === "string" ? input : ((input && input.url) || "");
            if (url.indexOf("/api/roth_opt") !== -1) {
              return Promise.resolve(new Response(window.__rothMockBody, {
                status: 200, headers: {"Content-Type": "application/json"}}));
            }
            return window.__rothRealFetch.call(this, input, init);
          };
        """)
        js(window, "window.__rothMockBody = " + json.dumps(
            json.dumps(roth_payload, ensure_ascii=False)) + ";")
        js(window, 'document.getElementById("rothRun").click()')
        roth_done = wait_js(window,
                            'document.querySelectorAll("#rothReadout .readout").length >= 4',
                            timeout=8)
        roth_en_cap = js(window, 'document.getElementById("rothCap").textContent || ""') or ""
        roth_en_readout = js(window, 'document.getElementById("rothReadout").textContent || ""') or ""
        roth_en_chart = js(window, 'document.getElementById("rothChart").textContent || ""') or ""
        check("Roth mocked run renders the selected grid point marker",
              roth_done and "selected grid point" in roth_en_chart,
              roth_en_chart[:220])
        check("Roth mocked run renders selected-point readouts",
              roth_done and "Selected annual conversion" in roth_en_readout
              and "Selected point after-tax terminal P50" in roth_en_readout,
              roth_en_readout[:260])
        check("Roth post-run English caption repeats the boundary contract",
              roth_done and all(phrase in roth_en_cap for phrase in (
                  "Exactly 8 tested base annual levels", "global rate",
                  "three-branch success", "unconditional after-tax terminal P50",
                  "failed paths = 0",
                  "no longer a flat liquidation proxy",
                  "Coverage is the only directional limit left",
                  "which years to convert")),
              roth_en_cap[:420])
        check("Roth post-run English output contains no CJK",
              not cjk.search(roth_en_cap + roth_en_readout + roth_en_chart),
              (roth_en_cap + " | " + roth_en_readout)[:420])
        js(window, 'document.querySelector(\'#langToggle [data-lang="zh"]\').click()')
        time.sleep(0.25)
        roth_zh_after = (js(window, 'document.getElementById("rothCap").textContent || ""') or "")
        roth_zh_after += " | " + (js(window, 'document.getElementById("rothReadout").textContent || ""') or "")
        check("Roth post-run Chinese output keeps the selected-grid wording",
              "已选档位" in roth_zh_after
              and "剩下的方向性只有一条" in roth_zh_after,
              roth_zh_after[:360])
        js(window, 'document.querySelector(\'#langToggle [data-lang="en"]\').click()')
        js(window, 'if (window.__rothRealFetch) window.fetch = window.__rothRealFetch;')

        js(window, 'document.getElementById("btRun").click()')
        wait_js(window, 'document.querySelectorAll("#btLegend .chip").length >= 3', timeout=12)
        js(window, 'document.querySelector(\'#langToggle [data-lang="en"]\').click()')
        options = js(window, '[...document.querySelectorAll("#gsMetric option,#gsLx option,#gsLy option")].map(x=>x.textContent).join(" | ")') or ""
        bt_labels = js(window, 'document.getElementById("btLegend").textContent') or ""
        visible_stress = js(window, 'document.getElementById("rp-stress").textContent') or ""
        cjk = re.compile(r"[\u3400-\u9fff]")
        check("English goal-seek options contain no CJK",
              bool(options) and not cjk.search(options), options)
        check("English backtest labels contain no CJK",
              bool(bt_labels) and not cjk.search(bt_labels), bt_labels)
        check("English stress page contains no CJK",
              not cjk.search(visible_stress), visible_stress[:160])

        # ---- Phase 0E: Standard archive -> saved-plan timeline in WKWebView --
        # This is the user-visible archive seam, not an API-only check.  The
        # server uses a temporary DB so the smoke cannot touch App Support.
        js(window, 'document.getElementById("restartBtn").click()')
        time.sleep(0.4)
        js(window, 'document.getElementById("startExample").click()')
        archive_ready = wait_js(window,
                                '[...document.querySelectorAll(".view")].find(v=>v.classList.contains("show")).id === "v-results"',
                                timeout=90)
        check("Standard archive run reaches results", archive_ready)

        # ---- Phase 2: the annual review, driven end to end -----------------
        # Piggybacks the archived run above rather than starting its own: the
        # review compares against an ARCHIVED forecast, and that is exactly
        # what the run above just produced. Everything here is the real path --
        # POST /api/checkin/record, POST /api/checkin/attribute, and the
        # rendered waterfall -- in the engine the app actually ships.
        review_tab = wait_js(
            window, '!![...document.querySelectorAll(".rtab")].find(t=>t.dataset.p==="review")',
            timeout=20)
        check("annual review tab appears after an archived run", review_tab)
        if review_tab:
            js(window, '[...document.querySelectorAll(".rtab")].find(t=>t.dataset.p==="review").click()')
            form_ready = wait_js(window, '!!document.getElementById("revOpening")', timeout=10)
            check("annual review form renders", form_ready)
            # The forecast list arrives asynchronously and the submit refuses
            # without a chosen forecast ("Choose an archived forecast first").
            # The candidate path won this race and the installed path lost it,
            # which is the worst way for a gate to behave: the same code passed
            # and failed in one run. Waiting for the control the form actually
            # depends on removes the timing from the question.
            picker_ready = wait_js(
                window,
                '(() => { const s = document.getElementById("revForecast");'
                ' return !!s && s.options.length > 0 && !!s.value; })()',
                timeout=20)
            check("annual review forecast picker is populated", picker_ready)
            js(window, '(() => {'
                       ' const set=(id,v)=>{const e=document.getElementById(id);e.value=v;'
                       '   e.dispatchEvent(new Event("input",{bubbles:true}));};'
                       ' set("revOpening","205000"); set("revClosing","282000");'
                       ' [["planned","net_contribution","72000"],'
                       '  ["actual","net_contribution","72000"],'
                       '  ["actual","spending","-8000"]].forEach(([s,k,v])=>{'
                       '   const e=document.querySelector("[data-rev-"+s+"=\\""+k+"\\"]");'
                       '   e.value=v; e.dispatchEvent(new Event("input",{bubbles:true}));});'
                       ' document.getElementById("revSubmit").click(); })()')
            attributed = wait_js(
                window,
                '(() => { const p=document.getElementById("revResultPanel");'
                ' return p && !p.classList.contains("hidden")'
                ' && document.querySelectorAll("#revWaterfall tr").length > 0; })()',
                timeout=30)
            rev_status = js(window, '(document.getElementById("revStatus")||{}).textContent || ""') or ""
            check("annual review returns a waterfall", attributed, rev_status[:160])
            # The lines must add up to the reported gap. A renderer that dropped
            # or double-counted one would still look like a waterfall.
            closes = js(window, '(() => {'
                        ' const cells=[...document.querySelectorAll("#revWaterfall .rev-val")]'
                        '   .map(c=>Number((c.textContent||"").replace(/[^0-9.]/g,""))'
                        '            * ((c.textContent||"").indexOf("\u2212")>=0?-1:1));'
                        ' const sum=cells.reduce((a,b)=>a+b,0);'
                        ' const v=document.getElementById("revVerdict").textContent||"";'
                        ' const m=v.match(/([0-9,]+)/); const gap=m?Number(m[1].replace(/,/g,"")):NaN;'
                        ' return String(Math.abs(Math.abs(sum)-gap) <= 2); })()')
            check("annual review waterfall reconciles to the reported gap",
                  closes == "true", str(closes))
            # §4: the model-update line must state which basis produced it.
            basis = js(window, '(document.getElementById("revDisclosure")||{}).textContent || ""') or ""
            check("annual review discloses the model-update basis",
                  "proven, not assumed" in basis or "证明出来的零" in basis,
                  basis[:160])
            # setLang contract: built content must re-render, in both directions.
            js(window, 'document.querySelector("#langToggle button[data-lang=zh]").click()')
            time.sleep(0.4)
            zh_verdict = js(window, '(document.getElementById("revVerdict")||{}).textContent || ""') or ""
            js(window, 'document.querySelector("#langToggle button[data-lang=en]").click()')
            time.sleep(0.4)
            en_page = js(window, '(document.getElementById("rp-review")||{}).textContent || ""') or ""
            check("annual review re-renders on language switch",
                  bool(cjk.search(zh_verdict)) and not cjk.search(en_page),
                  en_page[:160])
        # ---- Phase 3: the decision page -------------------------------
        # Deliberately stops at the cost estimate. A formal study is 22 full
        # Monte Carlo runs at Standard, which is minutes; what a smoke can
        # prove is that the page reaches the engine's own config, resolves its
        # labels, and reports the cost honestly before spending it.
        #
        # Every check here corresponds to a defect this page actually shipped
        # with: the lever read from the wrong state key so the button stayed
        # disabled forever, class names with no stylesheet entry, and
        # `data-i18n` tags with no dictionary entry that rendered the headings
        # as the literal strings `dec.title` and `dec.setup.title`. All three
        # passed every unit test that existed at the time.
        decide_tab = wait_js(
            window, '!![...document.querySelectorAll(".rtab")].find(t=>t.dataset.p==="decide")',
            timeout=20)
        check("decision tab appears once a run exists", decide_tab)
        if decide_tab:
            js(window, '[...document.querySelectorAll(".rtab")].find(t=>t.dataset.p==="decide").click()')
            setup_ready = wait_js(window, '!!document.getElementById("decPlan")', timeout=10)
            check("decision setup renders", setup_ready)
            # The headings come from data-i18n; an undefined key renders as the
            # key itself, which is how this shipped the first time.
            heading = js(window, '(document.querySelector("#rp-decide .sec-title")||{}).textContent || ""') or ""
            check("decision headings resolve rather than showing i18n keys",
                  bool(heading) and "dec." not in heading, heading[:80])
            # The lever is read out of the live config. Reading the wrong state
            # key returns undefined for every path and disables the button.
            lever_ok = js(window, '(() => {'
                          ' const b=document.getElementById("decPlan");'
                          ' const a=(document.getElementById("decAlternatives")||{}).textContent||"";'
                          ' return String(!!b && !b.disabled && a.indexOf("state.")>=0); })()')
            check("decision lever resolves from the live config",
                  lever_ok == "true", str(lever_ok))
            js(window, 'document.getElementById("decPlan").click()')
            costed = wait_js(
                window,
                '(() => { const p=document.getElementById("decCostPanel");'
                ' return p && !p.classList.contains("hidden")'
                ' && ((document.getElementById("decCost")||{}).textContent||"").length > 20; })()',
                timeout=30)
            cost_text = js(window, '(document.getElementById("decCost")||{}).textContent || ""') or ""
            check("decision run is costed before it is offered", costed, cost_text[:160])
            # The cost must be stated, and the Run button must stay hidden
            # until it has been.
            check("decision cost states the engine-run count",
                  bool(re.search(r"\d", cost_text)) and
                  js(window, 'String(!document.getElementById("decRun").classList.contains("hidden"))') == "true",
                  cost_text[:160])
            # Packs that do not apply to this plan are reported as skipped, not
            # folded into coverage. A default plan cannot reach four of the
            # seven families, and saying nothing would read as having tested
            # them.
            check("decision names the assumption families it cannot test",
                  ("cannot be tested here" in cost_text) or ("测不了" in cost_text),
                  cost_text[-200:])
            # The config the PAGE posts, not `default_config()`. Every unit
            # test for the pack library built on default_config(), which holds
            # every block the engine knows; the UI posts no `bonds` key at all.
            # A pack targeting `bonds.correlation_with_equity` therefore raised
            # inside the running study -- after the cost was quoted and the
            # user pressed Run -- and reached an installed app twice without a
            # single test failing.
            js(window, 'window.__decProbe = null;'
               '(async () => { try {'
               '  const r = await fetch("/api/decide/plan", {method:"POST",'
               '    headers:{"Content-Type":"application/json",'
               '             "X-FIRE-Capability": (await (await fetch("/api/capability",{cache:"no-store"})).json()).capability},'
               '    body: JSON.stringify({question:"higher_spending", paths:10000,'
               '      config: (typeof buildConfig === "function" ? buildConfig() : null),'
               '      alternatives:[{name:"less", changes:{"state.expenses_y0":40000}}]})});'
               '  window.__decProbe = JSON.stringify(await r.json());'
               '} catch (e) { window.__decProbe = "ERR:" + e.message; } })()')
            probe_ready = wait_js(window, 'window.__decProbe || ""', timeout=30)
            raw_probe = js(window, 'window.__decProbe || ""') or ""
            try:
                probe = json.loads(raw_probe) if probe_ready and not raw_probe.startswith("ERR:") else {}
            except ValueError:
                probe = {}
            check("the page's own config survives pack selection",
                  bool(probe) and not probe.get("error"), raw_probe[:200])
            check("the page's own config can be tested against real packs",
                  isinstance(probe.get("packs"), list) and len(probe["packs"]) > 0,
                  raw_probe[:200])

            js(window, 'document.querySelector("#langToggle button[data-lang=zh]").click()')
            time.sleep(0.4)
            zh_cost = js(window, '(document.getElementById("decCost")||{}).textContent || ""') or ""
            js(window, 'document.querySelector("#langToggle button[data-lang=en]").click()')
            time.sleep(0.4)
            en_decide = js(window, '(document.getElementById("rp-decide")||{}).textContent || ""') or ""
            # ---- Phase 4: the guardrail status the home page reads --------
            # The four regression families ROADMAP names must each be touched
            # by the real WebKit smoke, not only by unit suites: a unit suite
            # cannot see a route wired to keys that do not exist, which is how
            # this endpoint's first version read `period_end` off a payload
            # that has no such field.
            js(window, 'window.__grProbe = null;'
               '(async () => { try {'
               '  const cap = (await (await fetch("/api/capability",{cache:"no-store"})).json()).capability;'
               '  const plans = JSON.parse(localStorage.getItem("fire_plans_v1") || "[]");'
               '  const withArchive = (Array.isArray(plans) ? plans : []).filter(p => p && p.archive && p.archive.plan_id);'
               '  if (!withArchive.length) { window.__grProbe = "NOPLAN"; return; }'
               '  const r = await fetch("/api/guardrail/status?plan_id="'
               '    + encodeURIComponent(withArchive[0].archive.plan_id),'
               '    { headers: { "X-FIRE-Capability": cap } });'
               '  window.__grProbe = JSON.stringify(await r.json());'
               '} catch (e) { window.__grProbe = "ERR:" + e.message; } })()')
            gr_ready = wait_js(window, 'window.__grProbe || ""', timeout=30)
            raw_gr = js(window, 'window.__grProbe || ""') or ""
            try:
                gr = json.loads(raw_gr) if gr_ready and raw_gr.startswith("{") else {}
            except ValueError:
                gr = {}
            check("guardrail status answers for an archived plan",
                  bool(gr) and not gr.get("error"), raw_gr[:200])
            # A fresh plan has no comparable history, and the honest answer is
            # to withhold the status rather than show a green light.
            check("guardrail withholds a status without enough history",
                  gr.get("enough_history") is False and bool(gr.get("state_reason")),
                  raw_gr[:200])
            check("guardrail never claims to modify the plan",
                  gr.get("modifies_plan") is False, raw_gr[:200])

            check("decision page re-renders on language switch",
                  bool(cjk.search(zh_cost)) and not cjk.search(en_decide),
                  en_decide[:160])

        js(window, 'document.getElementById("restartBtn").click()')
        # Condition waits, not `sleep(0.4)`. `renderPlans()` reads the archive
        # through the §6 seam and re-renders when that resolves, so a fixed pause
        # is a race against an async round trip — one independent run of this file
        # reported 116/118 on exactly these two checks. Raising the sleep would
        # have hidden it; waiting for the condition is the fix.
        timeline_button = wait_js(
            window, '!!document.querySelector("#plansList [data-a=timeline]")',
            timeout=20)
        check("archived plan exposes Timeline action", bool(timeline_button))
        if timeline_button:
            js(window, 'document.querySelector("#plansList [data-a=timeline]").click()')
        # And wait for the *content*, not merely the container: the timeline node
        # is inserted first and filled when its own read resolves, so asserting on
        # `textContent` straight after the element appears is the same race again.
        timeline_ready = wait_js(
            window,
            '(() => { const n = document.querySelector("#plansList .plan-timeline");'
            ' if (!n) return false; const t = (n.textContent || "").toLowerCase();'
            ' return t.indexOf("snapshot") >= 0 && t.indexOf("standard") >= 0; })()',
            timeout=20)
        timeline_text = (js(window, '(document.querySelector("#plansList .plan-timeline")||{}).textContent || ""') or "").lower()
        check("WKWebView renders archived timeline", timeline_ready,
              timeline_text[:160])

        drive_storage_seam(window)

        js(window, "localStorage.clear()")
    except Exception as exc:                                # noqa: BLE001
        check("driver crashed", False, repr(exc))
    finally:
        window.destroy()


def _snapshot_plan_ids(db_path):
    """Which plans the run snapshots belong to, read straight from the archive."""
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {row[0] for row in conn.execute(
            "SELECT DISTINCT v.plan_id FROM run_snapshots s "
            "JOIN plan_versions v ON v.id = s.plan_version_id")}
    except sqlite3.Error:
        return set()
    finally:
        conn.close()


def _archive_authority(db_path):
    """The durable authority status, read from the control journal."""
    control = os.path.join(os.path.dirname(db_path), "recovery-control.sqlite3")
    if not os.path.exists(control):
        return None
    conn = sqlite3.connect(f"file:{control}?mode=ro", uri=True)
    try:
        row = conn.execute(
            "SELECT status FROM control_authority WHERE singleton_id=1").fetchone()
        return row and row[0]
    except sqlite3.Error:
        return "unreadable"
    finally:
        conn.close()


def _archive_count(db_path, table):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def _archive_user_version(db_path):
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error:
        return None
    finally:
        conn.close()


def _with_isolated_seam(window, port, run):
    """Run `run(window, db_path)` against a private server and archive.

    A cutover is irreversible, so any scenario that performs one needs its own
    archive; sharing would make the second scenario a post-cutover one whether it
    wanted to be or not. The port is passed in so two scenarios can run in the
    same session without colliding.
    """
    url = f"http://127.0.0.1:{port}/"
    with tempfile.TemporaryDirectory(
            prefix="fire-ui-smoke-f-", dir="/private/tmp") as dbdir:
        db_path = os.path.join(dbdir, "f.sqlite3")
        env = dict(os.environ, FIRE_ARCH_REEXEC="1", FIRE_PERSISTENCE_DB=db_path)
        srv = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server", "app.py"),
             "--port", str(port), "--no-open"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        try:
            if not wait_server(url=url + "api/presets"):
                check(f"§F isolated server came up on {port}", False)
                return
            window.load_url(url)
            time.sleep(3.0)
            run(window, db_path)
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                srv.kill()


def _drive_draft_only_cutover(window, db_path):
    """A2: no saved plans, one draft — the user with no route to a cutover.

    renderPlans() hides `plansBox` when there are no plans, and the migrate button
    used to live inside it. Its own `display` was correct and it was unclickable
    anyway, so `offsetParent === null` is the check that catches it; reading the
    button's own style does not.
    """
    legacy_draft = json.dumps(json.dumps(
        {"v": 2, "config": {"config_version": 2}}, ensure_ascii=False))
    js(window, f'localStorage.clear();'
               f'localStorage.setItem("fire_draft", {legacy_draft});')
    js(window, "location.reload()")
    time.sleep(3.0)
    check("§F A2 the draft-only fixture really has no saved plans",
          js(window, 'document.querySelectorAll("#plansList .plan-row").length') == 0)
    check("§F A2 the resume-draft control is offered, so a draft is present",
          js(window, '(document.getElementById("resumeDraft")||{}).style.display')
          != "none")
    check("§F A2 the migrate control is actually visible for a draft-only user",
          js(window, '(() => { const b = document.getElementById("migrateBtn");'
                     ' return !!b && b.offsetParent !== null'
                     ' && getComputedStyle(b).display !== "none"'
                     ' && b.getBoundingClientRect().height > 0; })()'))

    js(window, "window.confirm = () => true;")
    js(window, 'document.getElementById("migrateBtn").click()')
    wait_js(window, 'FIREStorage.authority().status === "sqlite_preferred"',
            timeout=60)
    check("§F A2 a real click on the migrate control completes the cutover",
          js(window, 'FIREStorage.authority().status') == "sqlite_preferred",
          str(js(window, 'FIREStorage.authority().status')))
    check("§F A2 the draft-only cutover imported the draft as evidence",
          _archive_count(db_path, "recovered_drafts") == 1,
          str(_archive_count(db_path, "recovered_drafts")))
    check("§F A2 the migrate control retires itself after the cutover",
          js(window, '(() => { const b = document.getElementById("migrateBtn");'
                     ' return !b || b.offsetParent === null; })()'))


def _drive_fence_composition(window, db_path):
    """B1: a real held fence, with one authority seam unavailable.

    `storageAuthority.fenceState` was assigned from `/api/storage/state` and never
    read; `legacyWriteRefusal()` consulted only `legacyAuthority.fence`. So with a
    genuine verified fence, if `/api/migration/authority` failed while
    `/api/storage/state` succeeded and reported `legacy_authoritative` with
    `fence_state=held`, the gate opened: `legacyAuthority.fence` was null because
    that read had failed, and `storageOk` was true because the other had not.

    The fence here is real — preview, stage, import, verify against the running
    server, stopping short of finalize — and the endpoint failure is injected at
    the transport by refusing one URL in `fetch`. Nothing reaches into the
    authority objects: the product fills them from the product's own responses,
    which is the only arrangement in which this tests the composition rather than
    restating it.
    """
    legacy_plans = json.dumps(json.dumps(
        [{"id": "legacy-plan", "name": "Imported plan",
          "config": {"config_version": 2}}], ensure_ascii=False))
    legacy_draft = json.dumps(json.dumps(
        {"v": 2, "config": {"config_version": 2}}, ensure_ascii=False))
    js(window, f'localStorage.clear();'
               f'localStorage.setItem("fire_plans_v1", {legacy_plans});'
               f'localStorage.setItem("fire_draft", {legacy_draft});')
    js(window, "location.reload()")
    time.sleep(3.0)

    def probe(name, expression, timeout=30):
        js(window, f'window.{name} = null; (async () => {{ try {{ '
                   f'window.{name} = JSON.stringify(await ({expression})); '
                   f'}} catch (e) {{ window.{name} = JSON.stringify('
                   f'{{__error: e.message, code: e.code || null}}); }} }})();')
        if not wait_js(window, f'window.{name} || ""', timeout=timeout):
            return {}
        try:
            return json.loads(js(window, f'window.{name} || ""') or "")
        except Exception:
            return {}

    def compose(name, drop_url):
        """Refresh both seams with `drop_url` failing, and return the refusal.

        Both refreshes run under the patch, so each authority object holds what
        the product would really be holding: one populated from a live response,
        the other left as an unreachable read.
        """
        js(window, 'window.__b1Real = window.__b1Real || window.fetch;'
                   'window.fetch = function (input, init) {'
                   '  const url = (typeof input === "string") ? input'
                   '    : (input && input.url) || "";'
                   f'  if (url.indexOf({json.dumps(drop_url)}) !== -1) {{'
                   '    return Promise.reject(new TypeError("injected: down"));'
                   '  }'
                   '  return window.__b1Real.call(this, input, init);'
                   '};')
        js(window, f'window.{name} = null; (async () => {{'
                   ' try {{ await window.FIREStorage.state(); }} catch (e) {{}}'
                   ' try {{ await window.FIRELegacyStore.refreshAuthority(); }}'
                   ' catch (e) {{}}'
                   f' window.{name} = "done"; }})();'.replace("{{", "{").replace("}}", "}"))
        wait_js(window, f'window.{name} || ""', timeout=25)
        return js(window, '(window.FIRELegacyStore.refusal() || "")')

    def restore_fetch():
        js(window, 'if (window.__b1Real) { window.fetch = window.__b1Real; }')

    # A genuine fence: the real preflight up to `verify`, which is what issues
    # `legacy_fence_id`. Not finalized, so the fence stays held.
    fence = probe("__b1Fence", """(async () => {
      const envelope = await window.FIRELegacyStore.readEnvelope();
      const cap = await (await fetch("/api/capability",
                                     {cache: "no-store"})).json();
      const post = async (path, body) => {
        const r = await fetch(path, {method: "POST",
          headers: {"Content-Type": "application/json",
                    "X-FIRE-Capability": cap.capability},
          body: JSON.stringify(body)});
        const j = await r.json();
        if (!r.ok) throw new Error(j.error || ("HTTP " + r.status));
        return j;
      };
      const preview = await post("/api/migration/preview", {envelope});
      const id = preview.operation_id;
      await post("/api/migration/stage", {operation_id: id, envelope});
      await post("/api/migration/import", {operation_id: id, envelope});
      const verified = await post("/api/migration/verify",
        {operation_id: id, envelope,
         page_instance_id: window.FIRELegacyStore.pageInstanceId()});
      return {operation_id: id, fence: verified.legacy_fence_id || null};
    })()""")
    check("§B1 a real verified fence is held after verify",
          bool(fence.get("fence")), str(fence))

    state = probe("__b1State", "FIREStorage.state()")
    check("§B1 the §6 state read reports the held fence",
          state.get("fenceState") == "held", str(state))
    check("§B1 the §6 state read still reports legacy authority",
          state.get("status") == "legacy_authoritative", str(state))

    before_draft = js(window, 'localStorage.getItem("fire_draft") || ""')
    before_plans = js(window, 'localStorage.getItem("fire_plans_v1") || ""')

    # ---- direction 1: migration authority down, storage reports held ----
    #
    # The refusal *code* changed in round 5 and this records why rather than
    # quietly following it. B2 made both seams mandatory — the gate opens only
    # when both are read successfully and both say legacy — so a scenario with
    # one seam down now refuses as `authority_unavailable` before the fence is
    # ever consulted. That is stricter, not weaker: the write is still refused
    # and the bytes are still untouched, which is what these checks are for.
    #
    # The fence composition itself did not stop being tested. It moved to
    # §C B3, where both seams are reachable and one carries the fence, which is
    # the only arrangement in which the fence is what does the refusing.
    CLOSED = ("migration_fenced", "authority_unavailable")
    refusal = compose("__b1Dir1", "/api/migration/authority")
    check("§B1 migration-authority unavailable + storage held is still refused",
          refusal in CLOSED, repr(refusal))
    wrote = js(window, 'JSON.stringify(window.FIRELegacyStore.writeDraft('
               'JSON.stringify({v: 2, config: {config_version: 2}, t: 1})))')
    check("§B1 writeDraft is refused under the composed fence",
          any(f'"{code}"' in (wrote or "") for code in CLOSED), str(wrote))
    check("§B1 fire_draft is byte-identical after the refused write",
          js(window, 'localStorage.getItem("fire_draft") || ""') == before_draft)
    wrote_plans = js(window, 'JSON.stringify(window.FIRELegacyStore.writePlans('
                     'JSON.stringify([{id: "x", name: "x"}])))')
    check("§B1 writePlans is refused under the composed fence",
          any(f'"{code}"' in (wrote_plans or "") for code in CLOSED),
          str(wrote_plans))
    check("§B1 fire_plans_v1 is byte-identical after the refused write",
          js(window, 'localStorage.getItem("fire_plans_v1") || ""') == before_plans)

    # The real control, not only the API: the button a user would press.
    js(window, 'document.getElementById("startFresh").click()')
    time.sleep(0.5)
    js(window, 'document.getElementById("wizSave").click()')
    time.sleep(1.0)
    check("§B1 the real Save-draft control also preserves fire_draft",
          js(window, 'localStorage.getItem("fire_draft") || ""') == before_draft)
    restore_fetch()

    # ---- direction 2: storage down, migration reports held ----
    refusal2 = compose("__b1Dir2", "/api/storage/state")
    check("§B1 storage unavailable + migration held is still refused",
          refusal2 in CLOSED, repr(refusal2))
    check("§B1 fire_draft survives the mirrored direction too",
          js(window, 'localStorage.getItem("fire_draft") || ""') == before_draft)
    restore_fetch()

    # ---- another page / a reload sees the same fence ----
    js(window, "location.reload()")
    time.sleep(3.0)
    reloaded = probe("__b1Reload", "FIREStorage.state()")
    check("§B1 a reloaded page still sees the fence held",
          reloaded.get("fenceState") == "held", str(reloaded))
    js(window, 'window.__b1R = null;'
               ' (async () => { try { await window.FIRELegacyStore.refreshAuthority();'
               ' } catch (e) {} window.__b1R = "done"; })();')
    wait_js(window, 'window.__b1R || ""', timeout=25)
    check("§B1 a reloaded page refuses the legacy write",
          js(window, '(window.FIRELegacyStore.refusal() || "")') == "migration_fenced")

    # ---- an invalid fence fails closed ----
    invalid = probe("__b1Invalid", """(async () => {
      const r = await fetch("/api/storage/state", {cache: "no-store"});
      return await r.json();
    })()""")
    check("§B1 the seam reports a fence state the browser can compose",
          invalid.get("fence_state") in ("held", "invalid", "expired"),
          str(invalid.get("fence_state")))


def _drive_manual_latch_reporting(window, db_path):
    """B2: a server-reported latch must not read as an unreachable seam.

    Both refresh paths threw a plain `Error`, and both catch blocks treat any
    throw as "we could not reach the seam". So a 423 carrying
    `code: manual_recovery_required` — the server stating precisely what is wrong
    — came out as `reachable: false`, and the user was shown
    `authority_unavailable`: "cannot confirm which store is authoritative, retry or
    restart". Retrying is the one action that cannot help, and the state they
    actually have to act on was hidden.

    Both halves are driven here, because the mirror mistake is just as bad:
    inferring a latch from a failed read would send someone hunting a fault that
    does not exist. A real 423 comes from a really latched journal; the
    unreachable case comes from a really refused connection.
    """
    legacy_plans = json.dumps(json.dumps(
        [{"id": "legacy-plan", "name": "Imported plan",
          "config": {"config_version": 2}}], ensure_ascii=False))
    js(window, f'localStorage.clear();'
               f'localStorage.setItem("fire_plans_v1", {legacy_plans});')
    js(window, "location.reload()")
    time.sleep(3.0)

    def probe(name, expression, timeout=30):
        js(window, f'window.{name} = null; (async () => {{ try {{ '
                   f'window.{name} = JSON.stringify(await ({expression})); '
                   f'}} catch (e) {{ window.{name} = JSON.stringify('
                   f'{{__error: e.message, code: e.code || null}}); }} }})();')
        if not wait_js(window, f'window.{name} || ""', timeout=timeout):
            return {}
        try:
            return json.loads(js(window, f'window.{name} || ""') or "")
        except Exception:
            return {}

    # ---- a real transport failure, on a journal that is NOT latched --------
    # This half runs first, and deliberately so. Run after the latch it would be
    # order-dependent: the status the previous read established is *kept* on an
    # unreachable seam — correctly, since inventing a downgrade is the fail-open
    # this codebase already removed once — so a still-latched journal would make
    # the mirror assertion look like an invented latch when it was a remembered
    # one. Testing "a connection failure must not present as a latch" requires
    # there to be no latch to remember.
    js(window, 'window.__b2Real = window.__b2Real || window.fetch;'
               'window.fetch = function (input, init) {'
               '  const url = (typeof input === "string") ? input'
               '    : (input && input.url) || "";'
               '  if (url.indexOf("/api/storage/state") !== -1'
               '      || url.indexOf("/api/migration/authority") !== -1) {'
               '    return Promise.reject(new TypeError("injected: down"));'
               '  }'
               '  return window.__b2Real.call(this, input, init);'
               '};')
    unreachable = probe("__b2Unreach", "FIREStorage.state()")
    check("§B2 a transport failure is reported as unreachable",
          unreachable.get("reachable") is False, str(unreachable))
    check("§B2 a transport failure invents no latch",
          unreachable.get("status") != "manual_recovery_required",
          str(unreachable.get("status")))
    check("§B2 a transport failure carries no refusal code",
          not unreachable.get("refusalCode"),
          str(unreachable.get("refusalCode")))
    js(window, 'window.__b2A = null; (async () => { try {'
               ' await window.FIRELegacyStore.refreshAuthority(); } catch (e) {}'
               ' window.__b2A = "done"; })();')
    wait_js(window, 'window.__b2A || ""', timeout=20)
    check("§B2 an unreachable seam is still read-only",
          js(window, '(window.FIRELegacyStore.refusal() || "")')
          == "authority_unavailable",
          str(js(window, '(window.FIRELegacyStore.refusal() || "")')))
    js(window, 'if (window.__b2Real) { window.fetch = window.__b2Real; }')

    # ---- a real latch, in the journal the server is actually serving -------
    control = os.path.join(os.path.dirname(db_path), "recovery-control.sqlite3")
    if not os.path.exists(control):
        probe("__b2Seed", "FIREStorage.state()")
    conn = sqlite3.connect(control)
    try:
        # The transition trigger refuses an unreceipted status change, which is
        # the invariant doing its job. Dropping it is how a test reaches a state
        # the product reaches through a real fault.
        conn.execute("DROP TRIGGER IF EXISTS control_authority_transition")
        conn.execute("UPDATE control_authority SET status='manual_recovery_required'")
        conn.commit()
    finally:
        conn.close()

    latched = probe("__b2Latched", "FIREStorage.state()")
    check("§B2 a 423 latch is reported as reachable, not unreachable",
          latched.get("reachable") is True, str(latched))
    check("§B2 a 423 latch sets the status to manual_recovery_required",
          latched.get("status") == "manual_recovery_required", str(latched))
    check("§B2 a 423 latch preserves the exact refusal code",
          latched.get("refusalCode") == "manual_recovery_required",
          str(latched.get("refusalCode")))
    check("§B2 the write gate refuses for the stated reason, not for 'unknown'",
          js(window, '(window.FIRELegacyStore.refusal() || "")')
          == "manual_recovery_required",
          str(js(window, '(window.FIRELegacyStore.refusal() || "")')))
    banner = js(window, '(() => { const b = '
                'document.getElementById("storageBanner");'
                ' return b ? b.textContent : ""; })()') or ""
    check("§B2 the banner says manual recovery rather than 'retry or restart'",
          ("手工恢复" in banner or "Manual recovery" in banner)
          and "重试或重启" not in banner and "Retry or restart" not in banner,
          banner[:160])
    check("§B2 the banner carries the contract code",
          "manual_recovery_required" in banner, banner[:160])


def _drive_round5_compositions(window, db_path):
    """The four defects Codex's round-5 review reproduced, driven for real.

    Composition rather than restatement: each scenario injects real seam
    responses and then drives the product's own control or its own gate
    function. The review's own reproductions are the shape of these tests —
    `write.ok=true, byte_identical=false` becomes a byte-level assertion, and
    `posts=0` becomes a request counter, because "the hint says saved" and "a
    request left the page" are different claims and only the second one is the
    feature.

    B2 and B3 run before the cutover and B1 and B4 after it, because that is
    where each defect lives. B3 deliberately has *both* seams reachable and
    reporting legacy: with B2 fixed, a scenario with one seam down refuses as
    `authority_unavailable` and would never reach the fence veto at all, so the
    test would pass while proving nothing about fences.
    """
    js(window, "localStorage.clear(); location.reload()")
    time.sleep(2.5)
    legacy_plans = json.dumps(json.dumps(
        [{"id": "legacy-plan", "name": "Imported plan",
          "config": {"config_version": 2}}], ensure_ascii=False))
    legacy_draft = json.dumps(json.dumps(
        {"v": 2, "config": {"config_version": 2}}, ensure_ascii=False))
    js(window, f'localStorage.setItem("fire_plans_v1", {legacy_plans});'
               f'localStorage.setItem("fire_draft", {legacy_draft});')
    js(window, "location.reload()")
    time.sleep(3.0)

    # A programmable fetch: a rule either drops a URL or answers it with a
    # canned response, and POSTs are counted per watched URL.
    #
    # Re-installed after every reload rather than once at the top. A reload
    # takes the patched `fetch` and `window.__r5` with it, and the first version
    # of this driver crashed on the next `posts()` — reported as a failed check
    # by the harness rather than silently skipping the rest, which is why the
    # crash was visible at all.
    def install():
        js(window, 'window.__r5Real = window.__r5Real || window.fetch;'
                   'window.__r5 = {rules: [], posts: {}};'
                   'window.fetch = function (input, init) {'
                   '  const url = (typeof input === "string") ? input'
                   '    : (input && input.url) || "";'
                   '  const method = ((init && init.method)'
                   '    || (input && input.method) || "GET").toUpperCase();'
                   '  if (method === "POST") {'
                   '    for (const k of Object.keys(window.__r5.posts)) {'
                   '      if (url.indexOf(k) !== -1) window.__r5.posts[k] += 1; } }'
                   '  for (const rule of window.__r5.rules) {'
                   '    if (url.indexOf(rule.url) === -1) continue;'
                   '    if (rule.drop) {'
                   '      return Promise.reject(new TypeError("injected: down")); }'
                   '    return Promise.resolve(new Response(JSON.stringify(rule.body),'
                   '      {status: rule.status,'
                   '       headers: {"Content-Type": "application/json"}}));'
                   '  }'
                   '  return window.__r5Real.call(this, input, init);'
                   '};')

    install()

    def rules(specs):
        js(window, f'window.__r5.rules = {json.dumps(specs)};')

    def watch(url):
        js(window, f'window.__r5.posts[{json.dumps(url)}] = 0;')

    def posts(url):
        return js(window, f'window.__r5.posts[{json.dumps(url)}] || 0') or 0

    def refresh_both(tag):
        js(window, f'window.{tag} = null; (async () => {{'
                   ' try { await window.FIREStorage.state(); } catch (e) {}'
                   ' try { await window.FIRELegacyStore.refreshAuthority(); }'
                   ' catch (e) {}'
                   f' window.{tag} = "done"; }})();')
        wait_js(window, f'window.{tag} || ""', timeout=25)

    def refusal():
        return js(window, '(window.FIRELegacyStore.refusal() || "")')

    def legacy_bytes():
        return (js(window, 'localStorage.getItem("fire_draft") || ""'),
                js(window, 'localStorage.getItem("fire_plans_v1") || ""'))

    def attempt_legacy_writes(nonce):
        """Both legacy writers, through the seam the product uses.

        The nonce matters. Writing identical content every time made the
        byte-identity checks pass for the wrong reason on a build where the
        writes succeed: the first scenario mutated the keys, and every later
        scenario then compared a mutated `before` against the same mutation.
        Distinct content per scenario means any successful write is visible.
        """
        js(window, 'window.__r5W = JSON.stringify(['
                   ' window.FIRELegacyStore.writeDraft('
                   f'   JSON.stringify({{v: 2, config: {{config_version: 2}},'
                   f'                    t: {json.dumps(nonce)}}})),'
                   ' window.FIRELegacyStore.writePlans('
                   f'   JSON.stringify([{{id: {json.dumps(nonce)},'
                   f'                     name: {json.dumps(nonce)}, config: {{}}}}]))]);')
        try:
            return json.loads(js(window, 'window.__r5W || "[]"') or "[]")
        except Exception:
            return []

    LEGACY_STATE = {"format": "fire-storage-state-v1",
                    "authority_status": "legacy_authoritative",
                    "generation_id": None, "receipt_sha256": None,
                    "legacy_digest_last_seen": None, "fence_state": "none"}

    def legacy_authority_body(fence_state=None):
        ops = ([{"operation_id": "op_injected", "fence_state": fence_state}]
               if fence_state else [])
        return {"authority": {"status": "legacy_authoritative"},
                "generation": None, "operations": ops}

    # ---- B2: one seam unreachable must not be overridden by the other ------
    for tag, down, alive in (("Dir1", "/api/storage/state",
                              "/api/migration/authority"),
                             ("Dir2", "/api/migration/authority",
                              "/api/storage/state")):
        before = legacy_bytes()
        rules([{"url": down, "drop": True},
               {"url": alive, "status": 200,
                "body": (LEGACY_STATE if alive.endswith("state")
                         else legacy_authority_body())}])
        refresh_both(f"__r5B2{tag}")
        code = refusal()
        check(f"§C B2 {down} down + the other seam legacy is authority_unavailable",
              code == "authority_unavailable", repr(code))
        wrote = attempt_legacy_writes(f"b2-{tag}")
        check(f"§C B2 {tag} both legacy writers are refused",
              all(isinstance(w, dict) and w.get("ok") is False for w in wrote),
              str(wrote))
        check(f"§C B2 {tag} fire_draft and fire_plans_v1 are byte-identical",
              legacy_bytes() == before, str(before[0][:24]))

    # ---- B3: invalid and expired fences veto exactly as held does ----------
    for fence_state in ("invalid", "expired"):
        before = legacy_bytes()
        rules([{"url": "/api/storage/state", "status": 200,
                "body": dict(LEGACY_STATE, fence_state="none")},
               {"url": "/api/migration/authority", "status": 200,
                "body": legacy_authority_body(fence_state)}])
        refresh_both(f"__r5B3{fence_state}")
        code = refusal()
        check(f"§C B3 a {fence_state} fence still vetoes the legacy writer",
              code == "migration_fenced", repr(code))
        wrote = attempt_legacy_writes(f"b3-{fence_state}")
        check(f"§C B3 {fence_state}: both legacy writers are refused",
              all(isinstance(w, dict) and w.get("ok") is False for w in wrote),
              str(wrote))
        check(f"§C B3 {fence_state}: fire_draft and fire_plans_v1 unchanged",
              legacy_bytes() == before, str(before[0][:24]))

    # ---- the real cutover, through the control the user presses ------------
    rules([])
    js(window, "window.confirm = () => true;")
    js(window, 'document.getElementById("migrateBtn").click()')
    moved = wait_js(window, 'window.FIREPlanStore.isServer() === true', timeout=45)
    check("§C the cutover completed so the post-cutover cases are real", moved)
    if not moved:
        return

    # ---- B1: the working draft survives a read-only archive ----------------
    WD = "/api/storage/working-draft"
    for tag, spec in (
            ("source_changed",
             [{"url": "/api/storage/state", "status": 200,
               "body": dict(LEGACY_STATE, authority_status="source_changed")}]),
            ("manual_recovery_required",
             [{"url": "/api/storage/state", "status": 423,
               "body": {"error": "manual recovery required",
                        "code": "manual_recovery_required"}}])):
        watch(WD)
        rules(spec)
        refresh_both(f"__r5B1{tag}")
        js(window, 'document.getElementById("restartBtn").click()')
        time.sleep(0.5)
        js(window, 'document.getElementById("startFresh").click()')
        time.sleep(0.5)
        js(window, 'const el = document.querySelector('
                   '\'.field[data-path="state.start_age"] input\');'
                   ' el.value = "47"; el.dispatchEvent(new Event("input"))')
        time.sleep(0.3)
        js(window, 'document.getElementById("wizSave").click()')
        time.sleep(1.5)
        check(f"§C B1 {tag}: the save reached the working-draft endpoint",
              posts(WD) >= 1, f"posts={posts(WD)}")
        hint = js(window, 'document.getElementById("saveHint").textContent || ""')
        check(f"§C B1 {tag}: the hint does not say 未保存",
              "未保存" not in (hint or "") and "not saved" not in (hint or ""),
              repr(hint))
        check(f"§C B1 {tag}: the UI states plans-read-only and draft-kept apart",
              ("只读" in (hint or "") or "read-only" in (hint or "")),
              repr(hint))
        # Saved is only saved if it comes back. Drop the stub, reload, and read
        # it from the side-store through a page that has no memory of it.
        rules([])
        js(window, "location.reload()")
        resumed = wait_js(
            window, '(() => { const b = document.getElementById("resumeDraft");'
                    ' return !!b && b.offsetParent !== null; })()', timeout=25)
        check(f"§C B1 {tag}: the draft survives a reload", resumed)
        js(window, 'document.getElementById("resumeDraft").click()')
        time.sleep(0.6)
        check(f"§C B1 {tag}: the reloaded draft holds what was typed",
              js(window, '(() => { const el = document.querySelector('
                         '\'.field[data-path="state.start_age"] input\');'
                         ' return el ? String(el.value) : ""; })()') == "47")
        js(window, 'document.getElementById("restartBtn").click()')
        time.sleep(0.4)
        install()          # the reload above took the patched fetch with it

    # ---- B4: a mixed-seam latch reaches the user ---------------------------
    before = legacy_bytes()
    rules([{"url": "/api/migration/authority", "status": 423,
            "body": {"error": "manual recovery required",
                     "code": "manual_recovery_required",
                     "authority_status": "sqlite_preferred"}},
           {"url": "/api/storage/state", "drop": True}])
    refresh_both("__r5B4")
    code = refusal()
    check("§C B4 a migration latch plus an unreachable storage seam refuses as a latch",
          code == "manual_recovery_required", repr(code))
    # Deliberately not calling the renderer from here. If the banner needed a
    # test to poke it, the product would still be able to leave a latched user
    # looking at nothing — so the authority refresh has to render it, and that
    # is what this asserts.
    banner = js(window, '(() => { const b = document.getElementById("storageBanner");'
                        ' return b ? (b.textContent || "") : ""; })()')
    check("§C B4 the banner is shown without the storage seam being reachable",
          bool(banner), repr(banner))
    check("§C B4 the banner says manual recovery rather than 'retry or restart'",
          ("手工恢复" in (banner or "") or "Manual recovery" in (banner or ""))
          and "重启应用" not in (banner or ""), repr(banner))
    check("§C B4 the legacy keys are byte-identical through all of it",
          legacy_bytes() == before, str(before[0][:24]))
    rules([])
    js(window, 'if (window.__r5Real) { window.fetch = window.__r5Real; }')


def drive_storage_seam(window):
    """M4 §F on its own server and its own archive.

    §F cannot share the main smoke's server. It is the *post*-cutover world, and
    a cutover is irreversible: run it before the legacy UI flows and it stops
    them, which is correct behaviour and useless as a test setup; run it after
    and the engine flows have already moved the live archive out from under the
    control journal, so startup reconciliation latches for manual recovery before
    §F gets a chance to begin. Neither is a defect — they are the contract
    working. So this gets a fresh port and a fresh database, and the window is
    pointed at it for the duration.
    """
    # Two scenarios, two archives. A2's draft-only case performs its own cutover,
    # so it cannot share an archive with the plan-bearing flow.
    _with_isolated_seam(window, SEAM_PORT, _drive_draft_only_cutover)
    _with_isolated_seam(window, _free_port(), _drive_storage_seam_checks)
    # B15 has to be driven as a product composition, not only through its API:
    # the defect class this repository has paid for is a backend and a page that
    # are each green while the control between them cannot complete one action.
    _with_isolated_seam(window, _free_port(), _drive_parent_identity)
    _with_isolated_seam(window, _free_port(), _drive_archive_lineage)
    # B1 needs a fence that is *held*, so it must not share an archive with a
    # scenario that finalizes one.
    _with_isolated_seam(window, _free_port(), _drive_fence_composition)
    # B2 latches its own journal on purpose, so it needs its own archive.
    _with_isolated_seam(window, _free_port(), _drive_manual_latch_reporting)
    # Round 5's four blockers. Their own scenario, their own archive: two of
    # them run before a cutover and two after, and a shared archive would make
    # the first pair post-cutover whether they wanted to be or not.
    _with_isolated_seam(window, _free_port(), _drive_round5_compositions)


def _drive_parent_identity(window, db_path):
    """B15: link, compare, translate and end through real WKWebView controls."""
    plans = [
        {"id": "family-household", "name": "Household",
         "config": {"config_version": 2, "state": {"start_age": 45},
                    "parents": {"mode": "scenario", "parents": [
                        {"label": "Mom", "current_age": 72,
                         "sex": "female"}]}}},
        {"id": "family-parent", "name": "Parent",
         "config": {"config_version": 2, "state": {"start_age": 72},
                    "mortality": {"sex": "female"}}},
    ]
    encoded = json.dumps(json.dumps(plans, ensure_ascii=False))
    js(window, 'localStorage.clear(); localStorage.setItem("fire_plans_v1", '
               + encoded + '); location.reload()')
    time.sleep(3.0)
    js(window, "window.confirm = () => true")
    js(window, 'document.getElementById("migrateBtn").click()')
    cutover = wait_js(
        window, 'FIREStorage.authority().status === "sqlite_preferred"',
        timeout=35)
    check("B15 family link cutover reaches SQLite authority", cutover)
    visible = wait_js(
        window, '(() => { const b=document.getElementById("familyLinkBox"); '
                'return !!b && b.offsetParent !== null && '
                '!!b.querySelector("[data-family-create]"); })()', timeout=20)
    check("B15 family controls are reachable only after SQLite authority", visible)

    # Select by the persisted config shape, not by migration-generated ids or
    # display names: normalization may supply a config-level name which is what
    # the plan row intentionally renders after cutover.
    js(window, '''(() => {
      const row=document.querySelector("[data-family-create]");
      const h=row.querySelector("[data-household]");
      const p=row.querySelector("[data-parent]");
      const plans=FIREPlanStore.list();
      const household=plans.find(x => (((x.config||{}).parents||{}).parents||[]).length);
      const parent=plans.find(x => x.id !== household.id);
      h.value=household.id; p.value=parent.id;
      row.querySelector("[data-create]").click();
    })()''')
    linked = wait_js(window, 'document.querySelectorAll("[data-link]").length === 1',
                     timeout=25)
    check("B15 a real Link plans click persists and renders one link", linked)
    check("B15 link write installed archive schema v12",
          _archive_user_version(db_path) == 12,
          str(_archive_user_version(db_path)))
    check("8.0 review queue treats a new link as pending, not a match",
          js(window, '''(() =>
            document.querySelectorAll('[data-queue-group="na"] .family-queue-item').length === 1
            && document.querySelectorAll('[data-queue-group="match"] .family-queue-item').length === 0
          )()'''))
    js(window, '''document.querySelector(
      '[data-queue-group="na"] [data-queue-link]').click()''')
    check("8.0 pending action focuses review controls without auto-evaluating",
          js(window, '''(() => {
            const row=document.querySelector('[data-link]');
            return document.activeElement === row.querySelector('[data-slot]')
              && row.querySelectorAll('[data-evaluation]').length === 0;
          })()''') and _archive_count(
              db_path, "parent_identity_evaluations") == 0)

    js(window, '''(() => {
      const row=document.querySelector("[data-link]");
      const c=row.querySelector("[data-confirm]");
      c.checked=true; c.dispatchEvent(new Event("change"));
      row.querySelector("[data-date]").value="2026-08-20";
      row.querySelector("[data-evaluate]").click();
    })()''')
    evaluated = wait_js(
        window, 'document.querySelector("[data-link]").textContent.indexOf("年龄一致") >= 0',
        timeout=25)
    check("B15 a real Compare click shows the measured match", evaluated)
    check("B15 the same Compare click shows the sex sub-result",
          "性别一致" in
          (js(window, 'document.getElementById("familyLinkBody").textContent') or ""))
    check("8.0 review queue projects age and sex as two current matches",
          js(window, '''(() =>
            document.querySelectorAll('[data-queue-group="match"] .family-queue-item').length === 2
            && document.querySelectorAll('[data-queue-group="na"] .family-queue-item').length === 0
          )()'''))
    check("8.0 evidence brief defaults to no selected receipts",
          js(window, '''(() =>
            document.querySelectorAll('[data-evidence-select]').length === 1
            && document.querySelectorAll('[data-evidence-select]:checked').length === 0
            && document.querySelector('[data-family-export]').disabled
          )()'''))
    js(window, '''(() => {
      const c=document.querySelector('[data-evidence-select]');
      c.checked=true; c.dispatchEvent(new Event('change'));
    })()''')
    check("8.0 one explicit receipt selection carries its age and sex root",
          js(window, '''(() => {
            const entries=FIREFamilyLinks.selectedEntries();
            return entries.length === 1
              && entries[0].evaluation.evaluation_id
              && entries[0].evaluation.sex_evaluation.sex_evaluation_id
              && !document.querySelector('[data-family-export]').disabled;
          })()'''))
    js(window, '''document.querySelector(
      '[data-queue-group="match"] [data-queue-link]').click()''')
    check("8.0 queue action navigates to and opens immutable evidence",
          js(window, '''(() => {
            const row=document.querySelector('[data-link]');
            const details=row.querySelector('[data-evaluation]');
            return details.open && row.classList.contains('family-queue-target')
              && details.contains(document.activeElement);
          })()'''))
    # Re-evaluate through the freshly rendered controls. The audit requirement
    # is plural history, not a latest-value card that happens to have a table
    # behind it.
    js(window, '''(() => {
      const row=document.querySelector("[data-link]");
      const c=row.querySelector("[data-confirm]");
      c.checked=true; c.dispatchEvent(new Event("change"));
      row.querySelector("[data-date]").value="2026-08-20";
      row.querySelector("[data-evaluate]").click();
    })()''')
    two_rows = wait_js(
        window, 'document.querySelectorAll("[data-link] .family-evaluation").length === 2',
        timeout=25)
    check("B15 both immutable evaluations render in the product history", two_rows)
    check("8.0 adding history does not silently select it or lose the prior choice",
          js(window, '''document.querySelectorAll('[data-evidence-select]').length === 2
            && document.querySelectorAll('[data-evidence-select]:checked').length === 1
            && FIREFamilyLinks.selectedEntries().length === 1'''))
    check("B15 both comparisons are immutable archive evaluations",
          _archive_count(db_path, "parent_identity_evaluations") == 2,
          str(_archive_count(db_path, "parent_identity_evaluations")))
    check("B15 the same two actions persisted two sex evidence rows",
          _archive_count(db_path, "parent_identity_sex_evaluations") == 2,
          str(_archive_count(db_path, "parent_identity_sex_evaluations")))
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        pins = conn.execute(
            "SELECT household_plan_version_id,parent_plan_version_id "
            "FROM parent_identity_evaluations ORDER BY evaluation_id LIMIT 1"
        ).fetchone()
    finally:
        conn.close()
    audit_text = js(window, 'document.getElementById("familyLinkBody").textContent') or ""
    check("B15 exact version pins and common date are available in the UI audit",
          bool(pins) and pins[0] in audit_text and pins[1] in audit_text
          and "2026-08-20" in audit_text, audit_text[:500])
    check("B15 reason and freshness are available in the UI audit",
          "measured_match" in audit_text and "current" in audit_text,
          audit_text[:500])

    js(window, 'document.querySelector("#langToggle [data-lang=en]").click()')
    check("B15 dynamic family disclosure switches to English",
          "does not verify identity or consent" in
          (js(window, 'document.getElementById("familyLinkBody").textContent') or ""))
    check("8.0 dynamic review queue switches all group headings to English",
          all(label in
              (js(window, 'document.getElementById("familyLinkBody").textContent') or "")
              for label in ("Current contradictions", "Stale evidence",
                            "Current matches", "N/A and pending")))
    check("8.0 language rerender preserves explicit evidence selection",
          js(window, '''document.querySelectorAll('[data-evidence-select]:checked').length === 1
            && FIREFamilyLinks.selectedEntries().length === 1
            && document.getElementById('familyLinkBody').textContent
                 .indexOf('Include in family evidence brief') >= 0'''))

    # Soft-delete the parent endpoint through the real plan-row control. The
    # link becomes read-only, keeps both evaluations, and explains why Compare
    # is no longer offered.
    js(window, '''(() => {
      const plans=FIREPlanStore.list();
      const index=plans.findIndex(x => !((((x.config||{}).parents||{}).parents||[]).length));
      document.querySelectorAll("#plansList .plan-row")[index]
        .querySelector("[data-a=del]").click();
    })()''')
    inactive = wait_js(
        window, '(() => { const row=document.querySelector("[data-link]"); '
                'return !!row && !row.querySelector("[data-evaluate]") && '
                'row.textContent.indexOf("Cannot compare") >= 0 && '
                'row.querySelectorAll(".family-evaluation").length === 2; })()',
        timeout=25)
    check("B15 inactive endpoint keeps history and removes Compare with a reason",
          inactive)
    check("8.0 lifecycle-stale matches move only to stale, never contradiction",
          js(window, '''(() =>
            document.querySelectorAll('[data-queue-group="stale"] .family-queue-item').length === 4
            && document.querySelectorAll('[data-queue-group="contradiction"] .family-queue-item').length === 0
            && document.querySelectorAll('[data-queue-group="match"] .family-queue-item').length === 0
          )()'''))

    # Delete the remaining endpoint too. Ordinary plan rows disappear, but the
    # family audit uses the unfiltered server projection and must remain found.
    js(window, '''(() => {
      document.querySelector("#plansList .plan-row [data-a=del]").click();
    })()''')
    both_deleted = wait_js(
        window, 'document.querySelectorAll("#plansList .plan-row").length === 0 '
                '&& document.querySelectorAll("[data-link] .family-evaluation").length === 2',
        timeout=25)
    check("B15 history remains discoverable after both endpoints are deleted",
          both_deleted)

    js(window, 'document.querySelector("[data-link] [data-end]").click()')
    ended = wait_js(
        window, 'document.querySelector("[data-link]").textContent.indexOf("ended") >= 0',
        timeout=25)
    check("B15 a real End link click retains and marks the history", ended)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM parent_identity_links WHERE ended_at IS NOT NULL"
        ).fetchone()[0]
    finally:
        conn.close()
    check("B15 ended link remains stored rather than deleted", count == 1, str(count))


def _drive_storage_seam_checks(window, db_path):
        # ---- M4 §F: the post-cutover seam, driven end to end in WKWebView ----
        # Everything above this point is the pre-cutover world. §F is the other
        # side of the authority CAS, and it is tested here rather than against a
        # mock because the parts that break are the ones only a real engine has:
        # the exact header set, the ordering of the fresh digest read against the
        # state read, and what a page does when it is told it is stale.
        js(window, "localStorage.clear(); location.reload()")
        time.sleep(2.5)
        legacy_plans = json.dumps(json.dumps(
            [{"id": "legacy-plan", "name": "Imported plan",
              "config": {"config_version": 2}}], ensure_ascii=False))
        legacy_draft = json.dumps(json.dumps(
            {"v": 2, "config": {"config_version": 2}}, ensure_ascii=False))
        js(window, f'localStorage.setItem("fire_plans_v1", {legacy_plans});'
                   f'localStorage.setItem("fire_draft", {legacy_draft});')
        # Reload so init() runs with the legacy data present: whether to offer a
        # cutover is decided at startup from what the server says and what is
        # actually there, not from a later poke at localStorage.
        js(window, "location.reload()")
        time.sleep(3.0)

        def probe(name, expression, timeout=25):
            """Run an async §F call and bring its result back as JSON."""
            js(window, f'window.{name} = null; (async () => {{ try {{ '
                       f'window.{name} = JSON.stringify(await ({expression})); '
                       f'}} catch (e) {{ window.{name} = JSON.stringify('
                       f'{{__error: e.message, code: e.code || null, '
                       f'status: e.httpStatus || null, payload: e.payload || null}});'
                       f' }} }})();')
            if not wait_js(window, f'window.{name} || ""', timeout=timeout):
                return {}
            raw = js(window, f'window.{name} || ""') or ""
            try:
                return json.loads(raw)
            except Exception:
                return {}

        state_before = probe("__fState0", "FIREStorage.state()")
        check("§F state read reports legacy authority before cutover",
              state_before.get("status") == "legacy_authoritative", str(state_before))
        check("§F state read reaches the pure §6 seam",
              state_before.get("reachable") is True, str(state_before))

        # A write before cutover is refused locally rather than sent: SQLite is
        # not the authority yet, so there is nothing to write through.
        early = probe("__fEarly", 'FIREStorage.createPlan('
                      '{display_name: "Too early", '
                      'normalized_config: {config_version: 2}})')
        check("§F write is refused before SQLite is authoritative",
              early.get("code") == "sqlite_not_authoritative", str(early))

        # The cutover, driven through the real product control flow: the button
        # the user would press. `FIREStorage.cutover()` is the mechanism; if the
        # test called it directly it would prove the mechanism works and say
        # nothing about whether the app can ever reach it.
        js(window, "window.confirm = () => true;")
        check("§F the migrate control is offered under legacy authority",
              js(window, '(() => { const b = document.getElementById("migrateBtn");'
                         ' return !!b && b.offsetParent !== null; })()'))
        js(window, 'window.__fCut = null;'
                   ' (async () => { try { await window.FIRECutover.run();'
                   ' window.__fCut = "done"; } catch (e) {'
                   ' window.__fCut = "ERR:" + e.message; } })();')
        wait_js(window, 'window.__fCut || ""', timeout=60)
        check("§F the UI cutover control completes",
              js(window, "window.__fCut") == "done",
              str(js(window, "window.__fCut")))
        cutover = probe("__fCutover2", "FIREStorage.authority()", timeout=20)
        check("§F the UI cutover flips authority to sqlite_preferred",
              cutover.get("status") == "sqlite_preferred", str(cutover))
        # offsetParent, not the button's own style: A2 moved the decision to the
        # container precisely because a child's style says nothing about whether it
        # can be clicked.
        check("§F the migrate control retires itself after cutover",
              js(window, '(() => { const b = document.getElementById("migrateBtn");'
                         ' return !b || b.offsetParent === null; })()'))

        # Legacy writes must now stop — every one of them, not just the ones a
        # caller remembered to gate.
        stopped = json.loads(js(window,
            'JSON.stringify([FIRELegacyStore.writeDraft("AFTER").code,'
            ' FIRELegacyStore.writePlans("AFTER").code,'
            ' localStorage.getItem("fire_draft")])') or "[]")
        check("§F every supported legacy write stops after SQLite authority",
              stopped[:2] == ["sqlite_authoritative", "sqlite_authoritative"],
              str(stopped))
        check("§F a refused legacy write leaves the source unchanged",
              json.loads(stopped[2]) == {"v": 2, "config": {"config_version": 2}},
              str(stopped))

        # ---- real UI CRUD after cutover, asserted against SQLite directly ----
        # The actor is the UI. The assertions read the archive file with sqlite3,
        # so nothing about them depends on the browser telling the truth about
        # itself, and they also read localStorage to prove the legacy key is
        # never written again.
        def archive_plans():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                return [(r[0], r[1], r[2]) for r in conn.execute(
                    "SELECT id, display_name, status FROM plans "
                    "ORDER BY created_at, id")]
            finally:
                conn.close()

        def archive_version_count():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                return conn.execute(
                    "SELECT COUNT(*) FROM plan_versions").fetchone()[0]
            finally:
                conn.close()

        legacy_before = js(window, 'localStorage.getItem("fire_plans_v1")')
        imported = archive_plans()
        check("§F the migration imported the legacy plan into SQLite",
              len(imported) == 1, str(imported))

        # SAVE, through the real button the user presses.
        js(window, 'document.getElementById("startFresh").click()')
        time.sleep(0.4)
        js(window, 'document.getElementById("wizSavePlan").click()')
        time.sleep(2.5)
        after_save = archive_plans()
        check("§F a real Save plan click writes a row into SQLite",
              len(after_save) == len(imported) + 1, str(after_save))
        check("§F a real Save plan click does not touch the legacy key",
              js(window, 'localStorage.getItem("fire_plans_v1")') == legacy_before)

        # LOAD, by reloading the page: the list must come back from SQLite.
        js(window, "location.reload()")
        time.sleep(3.0)
        rows = js(window, 'document.querySelectorAll("#plansList .plan-row").length')
        check("§F after reload the plan list is restored from SQLite",
              rows == len(after_save), f"{rows} rows vs {len(after_save)} in SQLite")
        check("§F the restored rows did not come from the legacy key",
              js(window, 'localStorage.getItem("fire_plans_v1")') == legacy_before)

        # EDIT, through the real rename input.
        versions_before = archive_version_count()
        js(window, '(() => { const i = document.querySelector("#plansList .pn");'
                   ' i.value = "Renamed by UI";'
                   ' i.dispatchEvent(new Event("change")); })()')
        time.sleep(2.5)
        renamed = archive_plans()
        check("§F a real rename writes a new immutable version in SQLite",
              archive_version_count() == versions_before + 1,
              f"{versions_before} -> {archive_version_count()}")
        def archive_version_names():
            conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
            try:
                return [json.loads(r[0]).get("name") for r in conn.execute(
                    "SELECT normalized_config_json FROM plan_versions")]
            finally:
                conn.close()

        # On the version, not on `plans.display_name`: §6 has no endpoint that
        # mutates the plans row, and a rename is a new immutable version, so the
        # current version's name is the current name.
        check("§F the rename is a new version carrying the new name in SQLite",
              "Renamed by UI" in archive_version_names(),
              str(archive_version_names()))
        check("§F the renamed plan shows the new name in the UI",
              "Renamed by UI" in (js(window,
                  '[...document.querySelectorAll("#plansList .pn")]'
                  '.map(i => i.value).join("|")') or ""))

        # DUPLICATE, through the real button.
        js(window, "window.confirm = () => true;")
        js(window, 'document.querySelector("#plansList [data-a=dup]").click()')
        time.sleep(2.5)
        duplicated = archive_plans()
        check("§F a real Duplicate click creates a plan in SQLite",
              len(duplicated) == len(renamed) + 1, str(duplicated))

        # DELETE, through the real button. §6 makes status the only tombstone.
        js(window, "window.confirm = () => true;")
        js(window, 'document.querySelector("#plansList [data-a=del]").click()')
        time.sleep(2.5)
        deleted = archive_plans()
        check("§F a real Delete click tombstones by status, removing no row",
              len(deleted) == len(duplicated)
              and any(row[2] == "deleted" for row in deleted), str(deleted))
        check("§F a deleted plan disappears from the UI list",
              js(window, 'document.querySelectorAll("#plansList .plan-row").length')
              == len([r for r in deleted if r[2] != "deleted"]))
        check("§F none of the real UI writes touched the legacy key",
              js(window, 'localStorage.getItem("fire_plans_v1")') == legacy_before)

        # ---- A3, decided: the carried-over draft is reachable ----------------
        # The user's ruling (2026-07-27) is that a migrated draft must be
        # visible, openable and savable. So these are ordinary `check`s now, and
        # they are driven through the product's own controls rather than the API:
        # the whole defect was that the API existed and nothing could reach it,
        # so an API-only test would have passed against the broken build.
        check("§F A3 the migration records the draft as durable evidence",
              _archive_count(db_path, "recovered_drafts") == 1,
              str(_archive_count(db_path, "recovered_drafts")))

        listed = probe("__a3List", "FIREStorage.recoveredDrafts()")
        drafts = listed.get("recovered_drafts") or []
        check("§F A3 a §6 read now hands the browser a draft_id",
              len(drafts) == 1 and bool(drafts[0].get("draft_id")), str(listed))
        check("§F A3 the recovered draft carries an openable config",
              bool((drafts[0] or {}).get("normalized_config"))
              if drafts else False, str(drafts[:1]))

        # Visible: the row is on screen, and `offsetParent` rather than the row's
        # own style, for A2's reason — a child inside a hidden container has a
        # perfectly correct style and is still unreachable.
        check("§F A3 the recovered draft is visible to the user",
              js(window, '(() => { const r = document.querySelector('
                         '"#recoveredList .plan-row"); return !!r && '
                         'r.offsetParent !== null; })()'))

        # Openable: the real Open control loads it into the wizard.
        js(window, 'document.querySelector('
                   '"#recoveredList [data-a=open-recovered]").click()')
        time.sleep(1.0)
        check("§F A3 the Open control loads the draft into the wizard",
              js(window, '[...document.querySelectorAll(".view")]'
                         '.find(v => v.classList.contains("show")).id'
                         ' === "v-wizard"'),
              str(js(window, '[...document.querySelectorAll(".view")]'
                            '.find(v => v.classList.contains("show")).id')))
        js(window, 'document.getElementById("restartBtn").click()')
        plans_before = _archive_count(db_path, "plans")

        # Savable: the real Save-as-plan control promotes it.
        wait_js(window, '!!document.querySelector('
                        '"#recoveredList [data-a=save-recovered]")', timeout=20)
        js(window, 'document.querySelector('
                   '"#recoveredList [data-a=save-recovered]").click()')
        promoted = wait_js(
            window,
            '!document.querySelector("#recoveredList [data-a=save-recovered]")',
            timeout=30)
        check("§F A3 the Save-as-plan control promotes the draft", promoted)
        check("§F A3 promotion creates a Plan",
              _archive_count(db_path, "plans") == plans_before + 1,
              f"{plans_before} -> {_archive_count(db_path, 'plans')}")
        check("§F A3 promotion is recorded as an append-only user_saved event",
              _archive_count(db_path, "recovered_draft_events") == 1,
              str(_archive_count(db_path, "recovered_draft_events")))
        # The evidence is evidence: promotion appends beside the draft row and
        # never rewrites it. This is what makes the immutable table safe to keep
        # immutable, which the ruling requires.
        check("§F A3 the immutable draft row itself is untouched",
              _archive_count(db_path, "recovered_drafts") == 1,
              str(_archive_count(db_path, "recovered_drafts")))
        # And a promoted draft is not offered twice — that would invite a second
        # copy of work the user has already kept.
        relisted = probe("__a3Relist", "FIREStorage.recoveredDrafts()")
        check("§F A3 a promoted draft is no longer offered",
              (relisted.get("recovered_drafts") or []) == [], str(relisted))

        # The draft path: under SQLite authority the legacy draft key stays put.
        draft_before = js(window, 'localStorage.getItem("fire_draft")')
        plans_before_draft = _archive_count(db_path, "plans")
        drafts_before_draft = _archive_count(db_path, "recovered_drafts")
        js(window, 'document.getElementById("startFresh").click()')
        time.sleep(0.4)
        # A distinctive value, typed into a real field. Without one, every check
        # below could be satisfied by a *fresh* wizard, and "restored the draft"
        # would be indistinguishable from "started over" — which is exactly how
        # a check passes while the feature is missing.
        # `input`, not `change`: numeric wizard fields bind the former, and a
        # `change` event would set the DOM value without ever reaching the
        # config — the draft would then be fresh and the check would be testing
        # the test.
        js(window, 'const el = document.querySelector('
                   '\'.field[data-path="state.start_age"] input\');'
                   ' el.value = "41"; el.dispatchEvent(new Event("input"))')
        time.sleep(0.3)
        js(window, 'document.getElementById("wizSave").click()')
        time.sleep(1.2)
        check("§F saving a draft after cutover does not write the legacy key",
              js(window, 'localStorage.getItem("fire_draft")') == draft_before)

        # ---- Ruling row 3: the saved draft survives a restart ----------------
        #
        # The save above went through the real Save-draft control. Three things
        # have to be true, and the third is the one that matters.
        side_store = os.path.join(os.path.dirname(db_path),
                                  "working-draft.json")
        check("§F row 3 the draft lands in a private file beside the archive",
              os.path.isfile(side_store)
              and (os.stat(side_store).st_mode & 0o777) == 0o600,
              side_store)
        # Read from disk, not through the browser: this is the one assertion
        # that does not depend on any JS still being loaded.
        stored = {}
        if os.path.isfile(side_store):
            with open(side_store, encoding="utf-8") as fh:
                stored = json.load(fh)
        check("§F row 3 the stored draft carries what the user typed",
              str((((stored.get("draft") or {}).get("config") or {})
                   .get("state") or {}).get("start_age")) == "41",
              json.dumps(stored)[:200])
        # It is a side-store, not the archive: nothing about the archive moved.
        check("§F row 3 saving a draft creates no archive row",
              _archive_count(db_path, "plans") == plans_before_draft
              and _archive_count(db_path, "recovered_drafts")
              == drafts_before_draft,
              f"plans {plans_before_draft}"
              f"->{_archive_count(db_path, 'plans')}, "
              f"recovered_drafts {drafts_before_draft}"
              f"->{_archive_count(db_path, 'recovered_drafts')}")

        # A reload is the test, not a re-render: it destroys every JS variable,
        # so a Resume that still appears can only have come from the server —
        # which is precisely what the previous round could not do. Asserting
        # against a re-rendered page would pass on the broken build too.
        js(window, "location.reload()")
        resumed = wait_js(
            window,
            '(() => { const b = document.getElementById("resumeDraft");'
            ' return !!b && b.offsetParent !== null; })()', timeout=25)
        check("§F row 3 a reloaded page still offers the saved draft", resumed)
        # Resume, and assert the *value*, not the view. A click on a hidden
        # button still fires its handler and still reaches the wizard, so
        # "the wizard opened" passes on a build with no draft at all — the T1
        # failure mode. The typed age is what only a restored draft can produce.
        js(window, 'document.getElementById("resumeDraft").click()')
        time.sleep(0.6)
        check("§F row 3 the resumed wizard holds the saved value, not a fresh one",
              js(window, '(() => { const el = document.querySelector('
                         '\'.field[data-path="state.start_age"] input\');'
                         ' return el ? String(el.value) : ""; })()') == "41")
        # Back to welcome: the checks below this point read the plan list, and
        # leaving the wizard open would fail them for an unrelated reason.
        js(window, 'document.getElementById("restartBtn").click()')
        wait_js(window,
                '[...document.querySelectorAll(".view")]'
                '.find(v => v.classList.contains("show")).id === "v-welcome"',
                timeout=10)

        # Reads now come from the archive, under the three headers. The SQLite
        # side is re-read here rather than reusing the `deleted` snapshot: the
        # A3 promotion above created a Plan after that snapshot was taken, so
        # comparing against it would fail this check for a reason that has
        # nothing to do with what it tests.
        in_sqlite = archive_plans()
        plans = probe("__fPlans", "FIREStorage.plans()")
        served = plans.get("plans") if isinstance(plans.get("plans"), list) else None
        check("§F plans are served from the archive after cutover",
              served is not None and len(served) == len(in_sqlite),
              f"served {served and len(served)} vs {len(in_sqlite)} rows in SQLite")
        check("§F each served plan carries its current version's config",
              bool(served) and all(isinstance(p.get("normalized_config"), dict)
                                   and p.get("current_version_id")
                                   for p in served))

        # A write, then the version CAS against the tip it returned.
        created = probe("__fCreate", 'FIREStorage.createPlan('
                        '{display_name: "From SQLite", '
                        'normalized_config: {config_version: 2}})')
        check("§F a plan write commits through the storage seam",
              bool(created.get("plan_id")), str(created))
        tip = created.get("current_version_id")
        versioned = probe("__fVersion", 'FIREStorage.createPlanVersion('
                          + json.dumps(created.get("plan_id")) + ", "
                          + json.dumps(tip) + ", {config_version: 2}, "
                          "{config_version: 2})")
        check("§F a version CAS against the current tip succeeds",
              bool(versioned.get("plan_version_id")), str(versioned))
        stale_cas = probe("__fStaleCas", 'FIREStorage.createPlanVersion('
                          + json.dumps(created.get("plan_id")) + ", "
                          + json.dumps(tip) + ", {config_version: 2}, "
                          "{config_version: 2})")
        check("§F a version CAS against a stale tip is a conflict",
              stale_cas.get("code") == "version_conflict", str(stale_cas))

        # The exact Idempotency-Key: a repeated external key is refused and no
        # twin appears, which is the property §6's revision exists to protect.
        before_count = len((probe("__fPlansB", "FIREStorage.plans()")
                            or {}).get("plans") or [])
        first_dup = probe("__fDup1", 'FIREStorage.createPlan('
                          '{display_name: "Once", '
                          'normalized_config: {config_version: 2}}, '
                          '{requestId: "ui-smoke-fixed-key"})')
        check("§F a keyed write succeeds once", bool(first_dup.get("plan_id")),
              str(first_dup))
        second_dup = probe("__fDup2", 'FIREStorage.createPlan('
                           '{display_name: "Once", '
                           'normalized_config: {config_version: 2}}, '
                           '{requestId: "ui-smoke-fixed-key"})')
        check("§F the same Idempotency-Key is refused after a resync",
              second_dup.get("code") == "idempotency_conflict", str(second_dup))
        after_count = len((probe("__fPlansC", "FIREStorage.plans()")
                           or {}).get("plans") or [])
        check("§F a refused duplicate creates no twin",
              after_count == before_count + 1,
              f"{before_count} -> {after_count}")

        # A stale tab: hold an old receipt, then try to write. 412 with the
        # current authority, and the page resynchronises rather than guessing.
        # Sent through the raw poster on purpose. `storageWrite` re-reads the
        # authority immediately before it mutates — that is §6's requirement —
        # so a tampered receipt cannot survive it, which is itself the property
        # worth having. What is under test here is the server's refusal and what
        # it hands back, so the stale receipt is presented directly.
        stale_write = probe("__fStale", '(async () => { '
                            'const a = FIREStorage.authority(); '
                            'const fresh = await FIREStorage.freshDigest(); '
                            'return await FIREStorage.post("/api/storage/plan", { '
                            'request_id: "stale-tab-probe", '
                            'authority_receipt: "0".repeat(64), '
                            'expected_generation: a.generation, '
                            'legacy_digest: fresh.digest, '
                            'plan: {display_name: "Stale tab", '
                            'normalized_config: {config_version: 2}} }); })()')
        check("§F a stale tab write is refused with 412 stale_authority",
              stale_write.get("code") == "stale_authority"
              and stale_write.get("status") == 412, str(stale_write))
        check("§F the 412 carries what the caller needs to resynchronise",
              bool((stale_write.get("payload") or {}).get("authority_receipt"))
              and bool((stale_write.get("payload") or {}).get("generation_id")),
              str(stale_write))

        # Reload/restart: nothing about the authority is persisted in the page,
        # so a fresh load must re-derive sqlite_preferred from the server.
        js(window, "location.reload()")
        time.sleep(2.5)
        after_reload = probe("__fReload", "FIREStorage.state()")
        check("§F authority survives a reload by being re-read, not remembered",
              after_reload.get("status") == "sqlite_preferred", str(after_reload))
        check("§F legacy writes stay stopped after a reload",
              js(window, 'FIRELegacyStore.writeDraft("X").code')
              == "sqlite_authoritative")

        # ---- A1: a fresh page whose authority reads fail must be read-only ----
        # The state under test is the dangerous one: the cutover is already
        # durable in the archive, and a *new* page cannot reach either authority
        # seam. It starts at `unknown`, so the old rule — "no successful read yet,
        # assume legacy" — let every legacy writer through and told the user
        # "saved" while rewriting `fire_draft` underneath the real authority.
        js(window, 'localStorage.setItem("fire_draft", '
                   + json.dumps(json.dumps({"v": 2, "config": {"config_version": 2}}))
                   + ');')
        draft_marker = js(window, 'localStorage.getItem("fire_draft")')
        plans_marker = js(window, 'localStorage.getItem("fire_plans_v1")')

        # Break both authority GETs for the whole page lifetime, before init runs.
        js(window, """
          window.__fFailAuthority = true;
          const realFetch = window.fetch;
          window.fetch = function (url, opts) {
            const u = String(url);
            if (window.__fFailAuthority
                && (u.indexOf("/api/storage/state") >= 0
                    || u.indexOf("/api/migration/authority") >= 0)) {
              return Promise.reject(new Error("injected authority seam failure"));
            }
            return realFetch.call(this, url, opts);
          };
        """)
        # A reload would drop the patch, so re-run init's authority path directly
        # on a page that has never had a successful read — the same starting state
        # a fresh load has.
        js(window, 'FIREStorage.authority().status = "unknown";'
                   'FIREStorage.authority().reachable = false;'
                   'FIRELegacyStore.authority().status = "unknown";'
                   'FIRELegacyStore.authority().seamReachable = false;')
        probe("__fFail1", "FIREStorage.state()")
        probe("__fFail2", "FIRELegacyStore.refreshAuthority()")
        check("§F A1 a failed authority read does not invent a writable status",
              js(window, "FIREStorage.authority().status") in ("unknown", None),
              str(js(window, "FIREStorage.authority().status")))
        check("§F A1 the legacy write gate refuses when authority is unknown",
              js(window, "FIRELegacyStore.refusal()") == "authority_unavailable",
              str(js(window, "FIRELegacyStore.refusal()")))

        # Every real legacy writer must refuse, and leave both keys untouched.
        refusals = json.loads(js(window,
            'JSON.stringify([FIRELegacyStore.writeDraft("HIJACK").code,'
            ' FIRELegacyStore.writePlans("HIJACK").code])') or "[]")
        check("§F A1 both legacy writers refuse on an unresolved authority",
              refusals == ["authority_unavailable", "authority_unavailable"],
              str(refusals))
        check("§F A1 fire_draft is byte-identical after the refused write",
              js(window, 'localStorage.getItem("fire_draft")') == draft_marker)
        check("§F A1 fire_plans_v1 is byte-identical after the refused write",
              js(window, 'localStorage.getItem("fire_plans_v1")') == plans_marker)

        # And the real product path: the Save-draft button must not report success.
        js(window, 'document.getElementById("startFresh").click()')
        time.sleep(0.4)
        js(window, 'document.getElementById("wizSave").click()')
        time.sleep(0.6)
        hint = js(window, '(document.getElementById("saveHint")||{}).textContent || ""')
        check("§F A1 the real Save draft button does not claim success",
              "saved" not in hint.lower() and "已保存" not in hint, repr(hint))
        check("§F A1 the real Save draft button did not write fire_draft",
              js(window, 'localStorage.getItem("fire_draft")') == draft_marker)
        check("§F A1 an unresolved authority raises the read-only banner",
              bool(js(window, '!!document.getElementById("storageBanner")')))

        js(window, "window.__fFailAuthority = false;")
        probe("__fFail3", "FIREStorage.state()")
        js(window, 'document.getElementById("restartBtn").click()')
        time.sleep(0.5)

        # ---- A5: the first real write after drift must observe it ----
        # `storageWrite` read a fresh digest and then called
        # `refreshStorageAuthority()` *without* it, so the digest was thrown away.
        # A first write after drift was refused by the server — correctly — while
        # the durable authority never moved to `source_changed` and no read-only
        # banner appeared. The old smoke hid this by calling `FIREStorage.state()`
        # explicitly before touching anything.
        #
        # So here the drift's *first* observer is a real UI save, with no state
        # read in between.
        js(window, 'localStorage.setItem("fire_plans_v1", "[]")')
        before_a5 = len([r for r in archive_plans() if r[2] != "deleted"])
        js(window, 'document.getElementById("startFresh").click()')
        time.sleep(0.4)
        js(window, 'window.__fA5 = null;'
                   ' (async () => { try { await FIREPlanStore.save('
                   '   {id: "a5", name: "After drift", ts: Date.now(),'
                   '    config: {config_version: 2}});'
                   '   window.__fA5 = "returned"; }'
                   ' catch (e) { window.__fA5 = "threw:" + (e.code || e.message); }'
                   ' })();')
        wait_js(window, 'window.__fA5 || ""', timeout=30)
        check("§F A5 a real save is the first thing to notice the drift",
              js(window, 'FIREStorage.authority().status') == "source_changed",
              str(js(window, 'FIREStorage.authority().status')))
        check("§F A5 the observation moved the durable authority, not just the page",
              _archive_authority(db_path) == "source_changed",
              str(_archive_authority(db_path)))
        check("§F A5 the read-only banner is visible after that write",
              bool(js(window, '!!document.getElementById("storageBanner")')))
        check("§F A5 no business object was written by the refused save",
              len([r for r in archive_plans() if r[2] != "deleted"]) == before_a5,
              f"{before_a5} -> "
              f"{len([r for r in archive_plans() if r[2] != 'deleted'])}")
        js(window, 'document.getElementById("restartBtn").click()')
        time.sleep(0.4)

        # Source drift: mutate a legacy key behind the seam's back. The fresh
        # two-key digest read on the next state call must notice, the explicit
        # observation must move authority to source_changed, and the page must
        # fall to read-only recovery rather than to a dead page.
        js(window, 'localStorage.setItem("fire_plans_v1", "[]")')
        drifted = probe("__fDrift", "FIREStorage.state()")
        check("§F a fresh digest read notices source drift",
              drifted.get("status") == "source_changed", str(drifted))
        drift_write = probe("__fDriftWrite", 'FIREStorage.createPlan('
                            '{display_name: "After drift", '
                            'normalized_config: {config_version: 2}})')
        check("§F writes stop after drift",
              drift_write.get("code") == "source_changed", str(drift_write))
        check("§F legacy writes are also stopped by drift",
              js(window, 'FIRELegacyStore.writeDraft("X").code') == "source_changed")



def _drive_archive_lineage(window, db_path):
    """A4: a migrated Plan keeps its archive lineage, on its own archive.

    Its own scenario for two reasons, both of them the contract working rather
    than a test convenience:

    * the drift scenario ends in `source_changed`, and Phase 0 has no seam that
      returns authority to `sqlite_preferred`, so plans stop being served there;
    * an archived formal run writes the archive through `PersistenceStore`,
      outside the recovery journal's prepare/apply seam, so the next `_bootstrap`
      sees an identity the journal never authorised and latches
      `manual_recovery_required`. Every §6 storage write after that returns 423.

    The second one is a separate, newly found blocker — after a cutover, running a
    formal analysis locks the storage seam — recorded in WORKSTREAM_LOG rather
    than worked around. Isolating this scenario keeps that latch from masking
    anything else.

    What losing the lineage cost, and what these checks pin: the Timeline button
    vanished (renderPlans only renders it when `archive.plan_id` exists), and
    opening a migrated plan left `archiveRef` null, so the next formal run sent no
    `plan_id` and the server created a *second* Plan beside the one the user was
    looking at.
    """
    legacy_plans = json.dumps(json.dumps(
        [{"id": "legacy-plan", "name": "Imported plan",
          "config": {"config_version": 2}}], ensure_ascii=False))
    js(window, f'localStorage.clear();'
               f'localStorage.setItem("fire_plans_v1", {legacy_plans});')
    js(window, "location.reload()")
    time.sleep(3.0)
    js(window, "window.confirm = () => true;")
    js(window, 'document.getElementById("migrateBtn").click()')
    wait_js(window, 'FIREStorage.authority().status === "sqlite_preferred"',
            timeout=60)
    check("§F A4 the lineage scenario reached sqlite authority",
          js(window, 'FIREStorage.authority().status') == "sqlite_preferred",
          str(js(window, 'FIREStorage.authority().status')))

    def archive_plans():
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            return [(r[0], r[1], r[2]) for r in conn.execute(
                "SELECT id, display_name, status FROM plans ORDER BY created_at, id")]
        finally:
            conn.close()

    check("§F A4 a migrated plan exposes a Timeline action",
          js(window, '!!document.querySelector("#plansList [data-a=timeline]")'))
    check("§F A4 the record carries the server plan id as its archive ref",
          js(window, '(() => { const p = FIREPlanStore.list()[0];'
                     ' return !!p && !!p.archive'
                     ' && p.archive.plan_id === p.id'
                     ' && p.archive.plan_version_id === p.current_version_id;'
                     ' })()'))

    # Reload before the run: this is the "still true after a reload" property, and
    # it has to be checked here because the formal run below latches the journal.
    js(window, "location.reload()")
    time.sleep(3.0)
    check("§F A4 the lineage survives a reload",
          js(window, '(() => { const p = FIREPlanStore.list()[0];'
                     ' return !!p && !!p.archive && !!p.archive.plan_id'
                     ' && p.archive.plan_id === p.id; })()'))
    check("§F A4 the Timeline action is still offered after a reload",
          js(window, '!!document.querySelector("#plansList [data-a=timeline]")'))

    plans_before_run = [r[0] for r in archive_plans() if r[2] != "deleted"]
    js(window, 'document.querySelector("#plansList [data-a=open]").click()')
    time.sleep(0.8)
    check("§F A4 opening a migrated plan reaches the wizard",
          js(window, 'document.getElementById("v-wizard")'
                     '.classList.contains("show")'))
    js(window, '(() => { const b = document.getElementById("wizNext");'
               ' for (let i = 0; i < 9; i++) b.click(); })()')
    time.sleep(1.0)
    js(window, 'document.getElementById("precRun").click()')
    ran = wait_js(window,
                  'document.getElementById("v-results")'
                  '.classList.contains("show")', timeout=180)
    check("§F A4 a formal run from a migrated plan completes", ran)
    plans_after_run = [r[0] for r in archive_plans() if r[2] != "deleted"]
    check("§F A4 a formal run does not fork a second Plan",
          plans_after_run == plans_before_run,
          f"{len(plans_before_run)} -> {len(plans_after_run)}")
    if ran:
        check("§F A4 the new snapshot belongs to the original plan_id",
              bool(_snapshot_plan_ids(db_path))
              and _snapshot_plan_ids(db_path) <= set(plans_before_run),
              str(_snapshot_plan_ids(db_path)))
    # And the separate, newly found blocker, asserted rather than glossed: an
    # archived formal run writes the archive outside the recovery journal, so the
    # next bootstrap latches manual recovery and the §6 seam stops serving. This
    # is the state a user is left in after one formal run post-cutover.
    js(window, "location.reload()")
    time.sleep(3.0)
    # Asserted from the control journal, which is server truth. The browser cannot
    # tell "latched" from "unreachable" — both are read-only under the A1 rule, and
    # that is the safe behaviour, but it means the page's own status is not
    # evidence of which one happened.
    control = os.path.join(os.path.dirname(db_path), "recovery-control.sqlite3")
    latched = None
    if os.path.exists(control):
        conn = sqlite3.connect(f"file:{control}?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT status FROM control_authority WHERE singleton_id=1").fetchone()
            latched = row and row[0]
        except sqlite3.Error:
            latched = "unreadable"
        finally:
            conn.close()
    # Positive closure, not a description of the defect. These two used to assert
    # that the journal *had* latched and that the page had therefore gone
    # read-only — both true, both passing, and both passing *because* a formal run
    # broke the storage seam. S1 routes the run's writes through the archive-write
    # seam, so the assertion is now the outcome the user needs: authority survives
    # the run and the seam still accepts a write.
    check("§F a post-cutover formal run leaves authority at sqlite_preferred",
          latched == "sqlite_preferred",
          "control_authority=" + str(latched))
    check("§F the legacy write gate is not latched after a formal run",
          js(window, 'FIRELegacyStore.refusal()') == "sqlite_authoritative",
          str(js(window, 'FIRELegacyStore.refusal()')))
    # And the decisive one: a §6 write actually succeeds afterwards. Authority
    # reading correctly is necessary and not sufficient — the latch showed up as a
    # 423 on the next write, so that is what has to be exercised.
    js(window, 'window.__t1Write = null; (async () => { try {'
               ' const r = await FIREStorage.createPlan({display_name: "after run",'
               ' normalized_config: {config_version: 2}});'
               ' window.__t1Write = JSON.stringify({ok: true, id: r.plan_id || null});'
               ' } catch (e) { window.__t1Write = JSON.stringify('
               '{ok: false, code: e.code || null, msg: e.message}); } })();')
    wait_js(window, 'window.__t1Write || ""', timeout=30)
    wrote_after = js(window, 'window.__t1Write || ""') or ""
    check("§F a §6 write still succeeds after a post-cutover formal run",
          '"ok": true' in wrote_after or '"ok":true' in wrote_after,
          wrote_after)



def main():
    check_storage_seam_source()
    check_privacy_copy()
    check_income_stream_copy()
    check_rule_pack_source()
    check_roth_grid_contract_source()
    check_destination_catalog_source()
    with tempfile.TemporaryDirectory(
            prefix="fire-ui-smoke-", dir="/private/tmp") as dbdir:
        env = dict(os.environ, FIRE_ARCH_REEXEC="1",
                   FIRE_PERSISTENCE_DB=os.path.join(dbdir, "ui.sqlite3"))
        srv = subprocess.Popen(
            [sys.executable, os.path.join(ROOT, "server", "app.py"),
             "--port", str(PORT), "--no-open"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, env=env)
        try:
            if not wait_server():
                print("server did not come up"); sys.exit(2)
            window = webview.create_window("ui-smoke", URL, hidden=True,
                                           width=1200, height=850)
            webview.start(drive, window)
        finally:
            srv.terminate()
            try:
                srv.wait(timeout=5)
            except subprocess.TimeoutExpired:
                srv.kill()

    failed = [r for r in RESULTS if not r[1]]
    print(f"\nUI SMOKE: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    # Printed even when empty, so "no known-open blockers" is a stated result
    # rather than the absence of a line. The ledger having no entries is the
    # claim; a silent report cannot distinguish that from the ledger not existing.
    print(f"UI SMOKE known-open blockers: {len(OPEN_BLOCKERS)}")
    if OPEN_BLOCKERS:
        # Counted and printed apart from the acceptance total, so the headline
        # number cannot be read as "everything is fine" while something known is
        # open. A release gate must not be green in that state.
        still_open = [b for b in OPEN_BLOCKERS if b[1]]
        stale = [b for b in OPEN_BLOCKERS if not b[1]]
        print(f"  {len(still_open)} open, {len(stale)} stale "
              f"(not counted in the total above)")
        for name, _open, detail in OPEN_BLOCKERS:
            print("    - " + name + (f": {detail}" if detail else ""))
        if still_open:
            print("BLOCKING: a known Phase 0 blocker is still open, so this gate "
                  "cannot report a release-ready result")
    sys.exit(1 if (failed or OPEN_BLOCKERS) else 0)


if __name__ == "__main__":
    main()
