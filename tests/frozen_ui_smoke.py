"""§8 shipped-composition gate: the frozen binary, its bundled web, a real WebView.

Contract 2256-2265 asks for the composition the user actually launches to be
gated. Two existing smokes each cover half of it and their sum is not the whole:

* `frozen_smoke.py` runs the bundle's own binary, but headlessly and without HTTP
  or a WebView — it proves the process starts and can persist, not that the app
  works;
* `ui_smoke.py` drives a real WKWebView against the bundle's web assets, but the
  server behind them is the repository's `server/app.py`.

So nothing exercised the frozen routes, the PyInstaller hidden imports the HTTP
surface needs, or the full UI API as shipped. Source-identity equality does not
substitute: it proves the inputs matched, not that freezing them produced a
working app.

This drives the bundle's `--frozen-headless-server` mode — same binary, same
bundled web, no window — and points a real WKWebView at it.

Usage:  frozen_ui_smoke.py /path/to/FIRE Modeling.app
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent
# This gate compares the built bundle with the registry in the source tree that
# produced it.  When invoked as a script, Python adds tests/ rather than the
# sibling server/ directory to sys.path.
sys.path.insert(0, str(ROOT / "server"))

try:
    import webview  # noqa: F401
except ImportError:
    venv_py = ROOT / ".build" / "venv" / "bin" / "python"
    if venv_py.exists() and os.path.abspath(sys.executable) != str(venv_py):
        os.execv(str(venv_py), [str(venv_py), os.path.abspath(__file__)] + sys.argv[1:])
    print("pywebview not available — run ./build-app.sh once to create .build/venv")
    sys.exit(2)

import webview  # noqa: E402

RESULTS = []

B15_CREATE_LINK_JS = '''(() => {
  const row=document.querySelector("[data-family-create]");
  const plans=FIREPlanStore.list();
  const household=plans.find(x =>
    ((((x.config||{}).parents||{}).parents||[]).length));
  const parent=plans.find(x => x.id !== household.id &&
    ((x.config||{}).state||{}).start_age === 72);
  if (!row || !household || !parent) return false;
  row.querySelector("[data-household]").value=household.id;
  row.querySelector("[data-parent]").value=parent.id;
  row.querySelector("[data-create]").click();
  return true;
})()'''

B15_DELETE_PARENT_JS = '''(() => {
  const plans=FIREPlanStore.list();
  const index=plans.findIndex(x =>
    !((((x.config||{}).parents||{}).parents||[]).length) &&
    ((x.config||{}).state||{}).start_age === 72);
  const row=document.querySelectorAll("#plansList .plan-row")[index];
  if (!row) return false;
  row.querySelector("[data-a=del]").click(); return true;
})()'''


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name
          + (f"  [{detail}]" if detail and not ok else ""))


def _linked_endpoint_statuses(conn):
    return conn.execute(
        "SELECT hp.status,pp.status FROM parent_identity_links l "
        "JOIN plans hp ON hp.id=l.household_plan_id "
        "JOIN plans pp ON pp.id=l.parent_plan_id"
    ).fetchall()


def start_frozen_server(app_path: pathlib.Path, db_dir: str):
    """Launch the bundle's own binary in headless server mode.

    The port is chosen by the OS and the URL is read from the child's own stdout,
    never guessed — and a nonce the child echoes back proves we are talking to the
    process we started rather than to some other server that happens to be
    listening on a port we picked.
    """
    executable = app_path / "Contents" / "MacOS" / "FIRE Modeling"
    if not executable.is_file():
        return None, None, None
    nonce = secrets.token_hex(16)
    ready_file = os.path.join(db_dir, "ready.json")
    env = dict(os.environ,
               FIRE_ARCH_REEXEC="1",
               FIRE_HEADLESS_PORT="0",
               FIRE_HEADLESS_NONCE=nonce,
               FIRE_HEADLESS_READY_FILE=ready_file,
               FIRE_PERSISTENCE_DB=os.path.join(db_dir, "frozen-ui.sqlite3"))
    proc = subprocess.Popen([str(executable), "--frozen-headless-server"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            env=env)
    # A handshake file rather than stdout: the shipped bundle is built
    # `--windowed` and has no usable stdout, so a caller waiting on a printed
    # line waits forever while the server is already up.
    deadline = time.time() + 120
    while time.time() < deadline:
        if os.path.exists(ready_file):
            try:
                with open(ready_file, encoding="utf-8") as handle:
                    return proc, json.loads(handle.read()), nonce
            except (ValueError, OSError):
                pass          # still being written
        if proc.poll() is not None:
            return proc, None, nonce
        time.sleep(0.5)
    return proc, None, nonce


def _json_request(base_url, path, payload=None, capability=None, timeout=60):
    """Reach the frozen server without mistaking its trust fence for a test.

    Every POST carries the authority Origin and the capability issued by this
    exact child.  A 403 is returned to the caller as evidence of a failed
    composition; it is never accepted as proof that the target route ran.
    """
    origin = base_url.rstrip("/")
    headers = {"Origin": origin}
    data = None
    method = "GET"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["X-FIRE-Capability"] = capability or ""
        method = "POST"
    req = urllib.request.Request(
        origin + "/" + path.lstrip("/"), data=data, headers=headers,
        method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            return response.status, json.loads(raw or b"{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw or b"{}")
        except (TypeError, ValueError):
            body = {"raw": raw[:400].decode("utf-8", "replace")}
        return exc.code, body


def _preset_config(presets_payload):
    presets = (presets_payload or {}).get("presets") or {}
    entries = (list(presets.values()) if isinstance(presets, dict)
               else list(presets))
    for entry in entries:
        cfg = entry.get("config") if isinstance(entry, dict) else None
        if isinstance(cfg, dict) and isinstance(cfg.get("state"), dict):
            return cfg
    return None


def _job_result(base_url, capability, route, payload, timeout=180):
    status, started = _json_request(
        base_url, route, payload, capability, timeout=30)
    if status != 200 or not isinstance(started, dict) or not started.get("job"):
        return status, started
    job = started["job"]
    deadline = time.time() + timeout
    progress = {}
    while time.time() < deadline:
        poll_status, progress = _json_request(
            base_url, "/api/progress?job=" + str(job), timeout=30)
        if poll_status != 200:
            return poll_status, progress
        if progress.get("done") or progress.get("error"):
            break
        time.sleep(0.1)
    if not progress.get("done") or progress.get("error"):
        return 500, progress
    return _json_request(base_url, "/api/result?job=" + str(job), timeout=30)


def _expected_correlation_fragments():
    """What THIS repository's registry says the frozen bundle must disclose.

    The bundle is built from this tree, so the two must agree exactly; any
    drift is either a stale bundle or a disclosure that stopped describing its
    own ledger, and both are worth failing a promotion for.
    """
    import correlation_registry as CORRELATION           # noqa: PLC0415
    summary = CORRELATION.summary()
    stance = summary["by_stance"]
    return (
        "Across %d sampling modules" % summary["modules"],
        "%d apply numeric relationships" % stance[CORRELATION.MODELLED_NUMERIC],
        "%d are structural stages" % stance[CORRELATION.STRUCTURALLY_LINKED],
        "%d are disclosed deliberate independences"
        % stance[CORRELATION.INDEPENDENT_BY_DESIGN],
        "%d were checked" % stance[CORRELATION.EXAMINED_UNRESOLVED],
        "NOT zero correlation",
        "no coefficient is guessed",
    )


def _drive_frozen_api_discriminators(base_url, capability, base_config):
    """The two server-side 7.x additions, through the frozen binary."""
    if not isinstance(base_config, dict):
        check("the frozen bundle supplies a real config for new-path probes",
              False, repr(base_config))
        return

    status, disclosed = _json_request(
        base_url, "/api/limitations",
        {"config": base_config, "language": "en"}, capability)
    rows = disclosed.get("triggered") if isinstance(disclosed, dict) else None
    correlation = next(
        (row for row in (rows or [])
         if isinstance(row, dict) and row.get("id") == "correlation_assumptions"),
        None)
    text = (correlation or {}).get("text") or ""
    # DERIVED, not retyped. This assertion used to carry its own copy of five
    # numbers ("19 sampling modules, 3 numeric, 9 structural, 4 independences,
    # 3 examined"). The registry grew to 25/4/9/8/4 over three slices, the
    # registry's OWN suite was updated each time, and this second copy rotted
    # silently -- because this file only runs against a BUILT bundle at
    # promotion time, where it failed the sixteenth install. Lesson 23b, and
    # its own remedy: derive the fact instead of storing it twice.
    expected = _expected_correlation_fragments()
    missing = [fragment for fragment in expected if fragment not in text]
    check("the frozen correlation ledger matches this repo's registry",
          status == 200 and correlation is not None and not missing,
          "status=%s missing=%s disclosure=%s" % (status, missing, text[:500]))

    cfg = json.loads(json.dumps(base_config))
    cfg["state"].update({
        "start_age": 75, "accum_years": 1, "retire_horizon": 30,
        "expenses_y0": 25_000, "swr_pref": 0.04,
    })
    cfg["initial"] = {
        "pretax_401k": 500_000, "roth_ira": 0, "hsa": 0,
        "taxable": 0,
    }
    cfg["social_security"]["enabled"] = False
    cfg["roth_ladder"]["enabled"] = False
    status, result = _job_result(
        base_url, capability, "/api/execution_simplification/start",
        {"config": cfg, "paths": 8, "seed": 15}, timeout=180)
    deltas = result.get("deltas") if isinstance(result, dict) else None
    policy = result.get("policy") if isinstance(result, dict) else None
    numeric_deltas = (isinstance(deltas, dict) and bool(deltas)
                      and all(isinstance(value, (int, float))
                              and not isinstance(value, bool)
                              for value in deltas.values()))
    check("the frozen B4 route returns the path-isolated seed-15 receipt",
          status == 200 and result.get("pairing_protocol")
          == "path_indexed_substreams"
          and result.get("exposed_paths") == 3
          and (result.get("baseline") or {}).get("lifetime_success") == 1.0
          and abs((result.get("simplified") or {}).get("lifetime_success", -1)
                  - (2 / 3)) < 1e-12
          and numeric_deltas and result.get("categorical_verdict") is None
          and (policy or {}).get("relocation") == "base_location_only"
          and (policy or {}).get("implicit_rebalancing_remains") is True,
          "status=%s result=%s" % (status, str(result)[:800]))


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: frozen_ui_smoke.py /path/to/FIRE Modeling.app", file=sys.stderr)
        return 2
    app_path = pathlib.Path(sys.argv[1]).resolve()

    with tempfile.TemporaryDirectory(prefix="fire-frozen-ui-",
                                     dir="/private/tmp") as db_dir:
        proc, payload, nonce = start_frozen_server(app_path, db_dir)
        try:
            check("the frozen binary started in headless server mode",
                  payload is not None,
                  "no FIRE_HEADLESS_READY line from the child")
            if payload is None:
                return _report()
            check("the child echoed the nonce it was given, so this is our process",
                  payload.get("nonce") == nonce, str(payload.get("nonce")))
            check("the child reports itself frozen", payload.get("frozen") is True)
            check("the child's pid is the process we started",
                  payload.get("pid") == proc.pid,
                  f"{payload.get('pid')} vs {proc.pid}")
            url = payload["url"]

            # The API the shipped app serves — frozen routes, frozen imports.
            served = {}
            for path, name in (("api/presets", "presets"),
                               ("api/capability", "capability"),
                               ("api/storage/state", "storage state")):
                try:
                    with urllib.request.urlopen(url + path, timeout=15) as r:
                        body = json.loads(r.read())
                    served[name] = body
                    check(f"the frozen server serves {name}", bool(body))
                except Exception as exc:                      # noqa: BLE001
                    check(f"the frozen server serves {name}", False, repr(exc))

            capability = (served.get("capability") or {}).get("capability")
            _drive_frozen_api_discriminators(
                url, capability, _preset_config(served.get("presets")))

            state = {"url": url, "db": os.path.join(db_dir, "frozen-ui.sqlite3")}
            window = webview.create_window("frozen-ui-smoke", url, hidden=True,
                                           width=1200, height=850)
            # A deadline on the WHOLE driver, because the thing that goes wrong
            # here goes wrong by not finishing. A modal dialog parks the WebKit
            # main thread forever, and `webview.start()` never returns; upstream,
            # `promote.py` is in `waitpid` and cannot tell that apart from slow
            # work. That combination sat for 68 minutes and only ended because a
            # person noticed the clock.
            #
            # `os._exit` rather than an exception: the hang is in the GUI loop on
            # the main thread, so there is nothing for an exception raised on a
            # watchdog thread to unwind. Exiting non-zero is what makes the
            # promotion FAIL, which is the correct outcome and the one that
            # leaves the installed app untouched.
            def _watchdog(limit=_DRIVE_DEADLINE_SECONDS):
                if _drive_done.wait(limit):
                    return
                sys.stderr.write(
                    "FROZEN UI SMOKE: no progress for %ds -- almost always a "
                    "modal dialog with no stub in place. Failing rather than "
                    "hanging.\n" % limit)
                sys.stderr.flush()
                os._exit(3)

            threading.Thread(target=_watchdog, daemon=True).start()
            try:
                webview.start(_drive_webview, (window, state))
            finally:
                _drive_done.set()
        finally:
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
    return _report()


def _drive_parent_identity_composition(window, state, js, wait_for):
    """B15 through the shipped page, frozen HTTP seam and disposable archive."""
    visible = wait_for(
        '(() => { const b=document.getElementById("familyLinkBox");'
        ' return !!b && b.offsetParent !== null && '
        '!!b.querySelector("[data-family-create]"); })()', 20)
    check("the frozen B15 family controls are reachable after cutover", visible)
    if not visible:
        return

    created = js(B15_CREATE_LINK_JS)
    linked = bool(created) and wait_for(
        'document.querySelectorAll("[data-link]").length === 1', 30)
    check("the frozen B15 page creates and renders one real link", linked)
    if not linked:
        return

    db_path = state["db"]
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            schema = conn.execute("PRAGMA user_version").fetchone()[0]
    except sqlite3.Error as exc:
        schema = None
        schema_detail = repr(exc)
    else:
        schema_detail = str(schema)
    check("the frozen B15 write installs archive schema v12",
          schema == 12, schema_detail)

    def evaluate_once():
        return js('''(() => {
          const row=document.querySelector("[data-link]");
          if (!row || !row.querySelector("[data-evaluate]")) return false;
          const confirmed=row.querySelector("[data-confirm]");
          confirmed.checked=true;
          confirmed.dispatchEvent(new Event("change"));
          row.querySelector("[data-date]").value="2026-08-20";
          row.querySelector("[data-evaluate]").click();
          return true;
        })()''')

    first = bool(evaluate_once()) and wait_for(
        'document.querySelectorAll("[data-link] .family-evaluation").length === 1',
        30)
    second = bool(first and evaluate_once()) and wait_for(
        'document.querySelectorAll("[data-link] .family-evaluation").length === 2',
        30)
    check("the frozen B15 page renders two immutable comparison receipts", second)
    if not second:
        return

    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            before = conn.execute(
                "SELECT evaluation_id,household_plan_version_id,"
                "parent_plan_version_id,as_of_date,as_of_basis,applicable,"
                "finding,delta_years,reason_code FROM "
                "parent_identity_evaluations ORDER BY created_at,evaluation_id"
            ).fetchall()
    except sqlite3.Error as exc:
        before = []
        receipt_detail = repr(exc)
    else:
        receipt_detail = repr(before)
    exact = (len(before) == 2 and all(
        row[1] and row[2] and row[3] == "2026-08-20"
        and row[4] == "user_confirmed_same_date" and row[5] == 1
        and row[6] == "match" and row[7] == 0
        and row[8] == "measured_match" for row in before))
    audit_text = js(
        'document.getElementById("familyLinkBody").textContent') or ""
    pins_visible = (exact and before[0][1] in audit_text
                    and before[0][2] in audit_text
                    and "2026-08-20" in audit_text
                    and "measured_match" in audit_text)
    check("the frozen B15 receipts carry exact pins, common date and measured match",
          pins_visible, receipt_detail + " ui=" + audit_text[:500])

    # Deactivate the parent endpoint through the real plan-row control.  The
    # extra wizard plan created by the standing CRUD smoke means selection must
    # follow config identity, never row position.
    parent_deleted = js(B15_DELETE_PARENT_JS)
    parent_read_only = bool(parent_deleted) and wait_for(
        '(() => { const row=document.querySelector("[data-link]");'
        ' return !!row && !row.querySelector("[data-evaluate]") && '
        'row.querySelectorAll(".family-evaluation").length === 2; })()', 30)

    household_deleted = js('''(() => {
      const plans=FIREPlanStore.list();
      const index=plans.findIndex(x =>
        ((((x.config||{}).parents||{}).parents||[]).length));
      const row=document.querySelectorAll("#plansList .plan-row")[index];
      if (!row) return false;
      row.querySelector("[data-a=del]").click(); return true;
    })()''')
    # What this asserts is that BOTH parent-bearing plans are gone and the link
    # went read-only with its history intact -- not that the plan list is
    # empty. It did say `.plan-row").length === 0`, which cannot hold: the
    # standing CRUD smoke saves a wizard plan before this runs, so a third,
    # unrelated plan is always present. The comment above already knew about
    # that plan -- it is why selection follows config identity rather than row
    # position -- and the emptiness check was simply left behind.
    #
    # It went unnoticed because the driver never got here: the dialog stub was
    # wiped by an earlier reload and the delete below hung on its confirm, so
    # this line had no chance to fail until the hang was fixed.
    both_read_only = bool(household_deleted) and wait_for(
        '(() => { const row=document.querySelector("[data-link]");'
        ' return !!row && !row.querySelector("[data-evaluate]") && '
        'row.querySelectorAll(".family-evaluation").length === 2 && '
        'FIREPlanStore.list().every(p => '
        '  !((((p.config||{}).parents||{}).parents||[]).length)); })()',
        30)
    link_text = js(
        'document.querySelector("[data-link]").textContent') or ""
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) as conn:
            after = conn.execute(
                "SELECT evaluation_id,household_plan_version_id,"
                "parent_plan_version_id,as_of_date,as_of_basis,applicable,"
                "finding,delta_years,reason_code FROM "
                "parent_identity_evaluations ORDER BY created_at,evaluation_id"
            ).fetchall()
            endpoint_statuses = _linked_endpoint_statuses(conn)
    except sqlite3.Error as exc:
        after, endpoint_statuses = [], []
        lifecycle_detail = repr(exc)
    else:
        lifecycle_detail = (
            "parent_read_only=%s both_read_only=%s endpoint_statuses=%s "
            "rows=%s ui=%s" % (
                parent_read_only, both_read_only, repr(endpoint_statuses),
                repr(after), link_text[:500]))
    check("the frozen B15 lifecycle becomes read-only and retains exact history",
          parent_read_only and both_read_only
          and endpoint_statuses == [("deleted", "deleted")]
          and after == before,
          lifecycle_detail)


#: Modal dialogs are the one thing that can stop this driver dead.
#: `window.confirm` blocks the WebKit main thread until somebody clicks, and
#: nobody is going to: this runs headless inside a promotion. It cost a
#: 68-minute hang -- the promote orchestrator sat in `waitpid` while WebKit sat
#: in `runJavaScriptConfirm` -- before anything timed out, because nothing here
#: had a deadline.
#:
#: Stubbed as one string so it can be re-applied. THAT is the part that was
#: missing: the stub was installed exactly once, and two later `location.reload()`
#: calls wiped it, leaving every confirm-guarded control after them able to hang
#: the run. A page reload resets the JS context; anything installed into it has
#: to be reinstalled, and the only safe way to get that right every time is to
#: make reloading and reinstalling the same call.
#: Whole-driver deadline. Generous -- the real run does two cutovers, several
#: reloads and a review walk -- but finite, which is the only property that
#: matters.
_DRIVE_DEADLINE_SECONDS = 600
_drive_done = threading.Event()

_DIALOG_STUBS = ("window.confirm = () => true;"
                 " window.alert = () => undefined;"
                 " window.prompt = () => null;")


def _drive_webview(window, state):
    try:
        time.sleep(3.0)
        def js(code):
            return window.evaluate_js(code)

        def reload_page(settle=3.0):
            """Reload AND restore the dialog stubs, because a reload drops them."""
            js("location.reload()")
            time.sleep(settle)
            js(_DIALOG_STUBS)

        # Before the first check, not after the first reload: a dialog raised
        # during startup would otherwise block with no stub in place.
        js(_DIALOG_STUBS)

        def wait_for(expr, timeout):
            deadline = time.time() + timeout
            while time.time() < deadline:
                try:
                    if js(expr):
                        return True
                except Exception:                            # noqa: BLE001
                    pass
                time.sleep(0.5)
            return False

        check("the frozen server's own web assets render in a real WKWebView",
              bool(js('!!document.getElementById("v-welcome")')))
        check("the bundled app.js loaded and initialised its seams",
              bool(js('!!(window.FIRELegacyStore && window.FIREStorage'
                      ' && window.FIREPlanStore)')))
        check("the storage authority seam answers through the frozen server",
              js('(() => { const a = FIREStorage.authority();'
                 ' return a && a.status; })()') in
              ("legacy_authoritative", "unknown", "legacy_assumed"),
              str(js('JSON.stringify(FIREStorage.authority())')))

        # A real cutover, driven through the product control flow, against the
        # frozen backend.
        js('localStorage.clear();'
           ' localStorage.setItem("fire_plans_v1", '
           + json.dumps(json.dumps([
               {"id": "family-household", "name": "Household",
                "config": {"config_version": 2,
                           "state": {"start_age": 45},
                           "parents": {"mode": "scenario", "parents": [
                               {"label": "Mom", "current_age": 72,
                                "sex": "female"}]}}},
               {"id": "family-parent", "name": "Parent",
                "config": {"config_version": 2,
                           "state": {"start_age": 72},
                           "mortality": {"sex": "female"}}},
           ]))
           + ');')
        reload_page()
        migrate_visible = js(
            '(() => { const b = document.getElementById("migrateBtn");'
            ' return !!b && b.offsetParent !== null; })()')
        check("the migrate control is offered by the frozen app", bool(migrate_visible))
        if migrate_visible:
            js('document.getElementById("migrateBtn").click()')
            deadline = time.time() + 90
            while time.time() < deadline:
                if js('FIREStorage.authority().status') == "sqlite_preferred":
                    break
                time.sleep(1.0)
            check("a cutover completes end to end against the frozen backend",
                  js('FIREStorage.authority().status') == "sqlite_preferred",
                  str(js('FIREStorage.authority().status')))

            # CRUD through the real UI, then a reload, all frozen-served.
            js('document.getElementById("startFresh").click()')
            time.sleep(0.5)
            js('document.getElementById("wizSavePlan").click()')
            time.sleep(3.0)
            # Counted after the reload, and again after a second one. The
            # pre-reload count is not a fair comparison — the list re-renders
            # asynchronously once the write returns — so what is asserted is that
            # the saved plan is there when the app is restarted, and stays there.
            reload_page()
            rows_after = js(
                'document.querySelectorAll("#plansList .plan-row").length')
            reload_page()
            rows_again = js(
                'document.querySelectorAll("#plansList .plan-row").length')
            check("a save through the frozen app survives a reload",
                  rows_after >= 2 and rows_again == rows_after,
                  f"after reload {rows_after}, after second reload {rows_again}")
            check("the reloaded list came from the archive, not the legacy key",
                  js('FIREStorage.authority().status') == "sqlite_preferred"
                  and js('localStorage.getItem("fire_plans_v1")') is not None,
                  str(js('FIREStorage.authority().status')))
            check("the legacy key is not written after cutover in the frozen app",
                  js('FIRELegacyStore.writeDraft("X").code')
                  == "sqlite_authoritative",
                  str(js('FIRELegacyStore.writeDraft("X").code')))

            _drive_parent_identity_composition(window, state, js, wait_for)

            # ----------------------------------------- the five-minute review
            # ROADMAP Phase 4 acceptance: the object is the whole review line,
            # walked in the INSTALLED bundle, and the test must assert the
            # review page RENDERED rather than that a request did not error.
            #
            # The roadmap names the failure this guards: the review tab is
            # gated on `archiveRefForReview()`, so a smoke with no archived
            # plan can be entirely green having never drawn the page.
            #
            # The sequence is `ui_smoke`'s, which already drives it against
            # the repository build -- ported rather than reinvented, because
            # every identifier here is one I would otherwise be guessing at.
            review_start = time.time()
            js('document.getElementById("restartBtn").click()')
            time.sleep(0.4)
            js('document.getElementById("startExample").click()')
            reached = wait_for(
                '[...document.querySelectorAll(".view")].find('
                'v=>v.classList.contains("show")).id === "v-results"', 420)
            check("an archived Standard run reaches results in the frozen app",
                  reached)

            if reached:
                # The gauge, on the ONE path that used to lose it: the first
                # results render after a computation is the only render with
                # animate on, and its reveal hid the arc before filling it
                # back in. If rAF stalled in between -- window hidden or
                # unfocused -- the arc stayed hidden and the empty catch said
                # nothing. Checked here rather than in the HTTP driver
                # because only a real WKWebView runs rAF at all.
                wait_for('!!document.querySelector("#gauge path")', 60)
                gauge = js(
                    '(() => { const g = document.getElementById("gauge");'
                    ' if (!g) return {ok:false, why:"no #gauge"};'
                    ' const paths = [...g.querySelectorAll("path")];'
                    ' const t = g.querySelector("text");'
                    ' const arc = paths[paths.length - 1];'
                    ' const off = arc ? getComputedStyle(arc).strokeDashoffset : "none";'
                    ' return {ok: paths.length >= 1 && !!t, kids: g.children.length,'
                    '         text: t ? t.textContent : null, offset: off};})()')
                check("the gauge is drawn in the frozen app",
                      bool(gauge) and gauge.get("ok"),
                      str(gauge))
                geometry = js(
                    '(() => { const g=document.getElementById("gauge");'
                    ' const svg=g&&(g.matches("svg")?g:g.querySelector("svg"));'
                    ' const wrap=g&&g.closest(".gauge-wrap");'
                    ' if(!svg||!wrap) return null;'
                    ' const b=svg.getBoundingClientRect();'
                    ' const w=wrap.getBoundingClientRect();'
                    ' return {width:b.width,height:b.height,wrapHeight:w.height,'
                    ' paths:svg.querySelectorAll("path").length,'
                    ' text:(svg.querySelector("text")||{}).textContent||""};})()')
                check("the frozen gauge occupies a nonzero visible WKWebView box",
                      bool(geometry)
                      and geometry.get("width", 0) >= 200
                      and geometry.get("height", 0) >= 140
                      and geometry.get("wrapHeight", 0) >= 140
                      and geometry.get("paths", 0) >= 2
                      and "%" in geometry.get("text", ""),
                      str(geometry))
                # The number must have finished counting up, and the arc must
                # not be sitting at its hidden offset. "Drawn" is not enough:
                # the defect left a gauge whose elements all existed.
                settled = wait_for(
                    '(() => { const g=document.getElementById("gauge");'
                    ' const t=g&&g.querySelector("text");'
                    ' const ps=g?[...g.querySelectorAll("path")]:[];'
                    ' const a=ps[ps.length-1];'
                    ' if(!t||!a) return false;'
                    ' const off=parseFloat(getComputedStyle(a).strokeDashoffset)||0;'
                    ' return /\\d/.test(t.textContent||"") && off < 1;})()', 90)
                # NOTE what this proves. Run against the build that still had
                # the defect, both of these PASS: the failure needs rAF to
                # stall between hiding the arc and filling it, which does not
                # happen on a machine driving the window deliberately. So this
                # is a regression guard, not a discriminator -- it will catch
                # the day somebody removes the settle path, and it cannot
                # confirm the fix shipped. The unit gate on ordering
                # (tests/test_gauge_reveal.py) is what pins the fix itself.
                check("the reveal settles instead of leaving the arc hidden",
                      settled,
                      "the arc is still at its hidden dash offset, or the "
                      "number never finished counting")

                tab = wait_for(
                    '!![...document.querySelectorAll(".rtab")].find('
                    't=>t.dataset.p==="review")', 30)
                check("the annual review tab appears after an archived run",
                      tab, "gated on archiveRefForReview()")
                if tab:
                    js('[...document.querySelectorAll(".rtab")].find('
                       't=>t.dataset.p==="review").click()')
                    rendered = wait_for(
                        '!!document.getElementById("revOpening")', 20)
                    check("the annual review page actually rendered",
                          rendered,
                          "asserting the form exists on screen, not that a "
                          "request returned 200")
                    elapsed = time.time() - review_start
                    check("the review line completes inside five minutes",
                          elapsed < 300,
                          "%.0fs from restart to a rendered review" % elapsed)
    except Exception as exc:                                  # noqa: BLE001
        check("frozen ui driver crashed", False, repr(exc))
    finally:
        window.destroy()


def _report() -> int:
    failed = [r for r in RESULTS if not r[1]]
    print(f"\nFROZEN UI SMOKE: {len(RESULTS) - len(failed)}/{len(RESULTS)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
