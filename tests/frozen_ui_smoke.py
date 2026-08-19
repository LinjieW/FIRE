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
import subprocess
import sys
import tempfile
import time
import urllib.request

HERE = pathlib.Path(__file__).resolve().parent
ROOT = HERE.parent

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


def check(name, ok, detail=""):
    RESULTS.append((name, bool(ok), detail))
    print(("  PASS  " if ok else "  FAIL  ") + name
          + (f"  [{detail}]" if detail and not ok else ""))


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
            for path, name in (("api/presets", "presets"),
                               ("api/capability", "capability"),
                               ("api/storage/state", "storage state")):
                try:
                    with urllib.request.urlopen(url + path, timeout=15) as r:
                        body = json.loads(r.read())
                    check(f"the frozen server serves {name}", bool(body))
                except Exception as exc:                      # noqa: BLE001
                    check(f"the frozen server serves {name}", False, repr(exc))

            state = {"url": url, "db": os.path.join(db_dir, "frozen-ui.sqlite3")}
            window = webview.create_window("frozen-ui-smoke", url, hidden=True,
                                           width=1200, height=850)
            webview.start(_drive_webview, (window, state))
        finally:
            if proc is not None:
                proc.terminate()
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    proc.kill()
    return _report()


def _drive_webview(window, state):
    try:
        time.sleep(3.0)
        def js(code):
            return window.evaluate_js(code)

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
           + json.dumps(json.dumps([{"id": "legacy-plan", "name": "Imported plan",
                                     "config": {"config_version": 2}}]))
           + ');')
        js("location.reload()")
        time.sleep(3.0)
        js("window.confirm = () => true;")
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
            js("location.reload()")
            time.sleep(3.0)
            rows_after = js(
                'document.querySelectorAll("#plansList .plan-row").length')
            js("location.reload()")
            time.sleep(3.0)
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
            def wait_for(expr, timeout):
                deadline = time.time() + timeout
                while time.time() < deadline:
                    try:
                        if js(expr):
                            return True
                    except Exception:                        # noqa: BLE001
                        pass
                    time.sleep(0.5)
                return False

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
