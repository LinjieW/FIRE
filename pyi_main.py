"""
pyi_main.py — PyInstaller entry point for the self-contained FIRE Modeling.app.

Frozen, this bundles Python + numpy + the engine + the web assets, so a friend
on any Mac can double-click with nothing installed. It points the server at the
bundled web/ dir, silences argv (LaunchServices passes a -psn_… arg), routes
output to a log file (a windowed .app has no console), then starts the server —
which opens the default browser to the local panel.
"""
import multiprocessing
multiprocessing.freeze_support()   # REQUIRED before anything else: spawn'd
                                   # workers re-enter this frozen entry point.

import os
import sys

_FROZEN = getattr(sys, "frozen", False)
_FROZEN_SMOKE = _FROZEN and "--frozen-smoke" in sys.argv
# Headless server mode: serve the bundled web and API on a port the caller
# chooses, print the URL, and never open a window. It exists so a promotion
# gate can drive the *shipped composition* — this frozen backend, these
# bundled web assets, a real WKWebView — instead of gating the two halves
# separately and calling the pair §8 compliance. `--frozen-smoke` proves the
# binary runs; `ui_smoke` against the repo server proves the JS works; neither
# proves the frozen routes and hidden imports the shipped app actually uses.
_FROZEN_HEADLESS = _FROZEN and "--frozen-headless-server" in sys.argv

if _FROZEN:
    base = sys._MEIPASS  # PyInstaller unpack dir
    os.environ.setdefault("FIRE_WEB_DIR", os.path.join(base, "web"))
    os.environ.setdefault(
        "FIRE_BUNDLED_IDENTITY",
        os.path.join(base, "release_identity", "frozen_build_identity.json"))
    # a windowed app has no stdout/stderr; keep a log for diagnosis
    if not (_FROZEN_SMOKE or _FROZEN_HEADLESS):
        try:
            logp = os.path.expanduser("~/Library/Logs/FIRE-Modeling.log")
            os.makedirs(os.path.dirname(logp), exist_ok=True)
            # cap unbounded growth: keep only the most recent ~200KB (audit P2-7)
            try:
                if os.path.getsize(logp) > 1_000_000:
                    with open(logp, "rb") as _r:
                        _r.seek(-200_000, 2)
                        tail = _r.read()
                    with open(logp, "wb") as _w:
                        _w.write(b"[log truncated]\n" + tail)
            except OSError:
                pass
            _f = open(logp, "a", buffering=1)
            sys.stdout = _f
            sys.stderr = _f
        except Exception:
            pass
    # LaunchServices may append a process-serial-number arg; argparse would choke
    sys.argv = sys.argv[:1]

# source-mode: make server/ and engine/ importable
_here = os.path.dirname(os.path.abspath(__file__))
for _sub in ("server", "engine"):
    _p = os.path.join(_here, _sub)
    if os.path.isdir(_p) and _p not in sys.path:
        sys.path.insert(0, _p)

import app  # noqa: E402  (server/app.py — bundled as a module when frozen)

if _FROZEN_SMOKE:
    # This path is deliberately inside the frozen executable. It proves that
    # bundled imports, NumPy native code, engine execution, static assets, and
    # server resources all work before a candidate replaces the live app. It
    # intentionally opens no GUI or socket, so the gate also runs in locked-down
    # build environments.
    import json as _json
    import os as _os
    import platform as _platform
    import stat as _stat
    import tempfile as _tempfile
    import time as _time

    import engine_adapter as _engine
    import numpy as _numpy

    _summary = _engine.summary(_engine.default_config(), 16, 96000, False)
    _persistence_ok = False
    _persistence_error = None
    _build_identity_ok = False
    _identity_error = None
    try:
        from persistence import PersistenceStore as _PersistenceStore
        from persistence import read_timeline as _read_timeline

        # macOS exposes /var as a symlink to /private/var.  The persistence
        # path contract deliberately rejects that alias, so the frozen smoke
        # must create its disposable database under a canonical root too.
        with _tempfile.TemporaryDirectory(
                prefix="fire-frozen-", dir="/private/tmp") as _td:
            _db = _os.path.join(_td, "fire-modeling.sqlite3")
            _store = _PersistenceStore(_db, app_release_id="frozen-smoke")
            _job = app.start_run_job(
                _engine.default_config(), 10_000, 96_000, 1_500,
                store=_store, precision="standard", archive=True)
            _state = {}
            _deadline = _time.time() + 90
            while _time.time() < _deadline:
                with app._JOBS_LOCK:
                    _state = dict(app._JOBS.get(_job) or {})
                if _state.get("done"):
                    break
                _time.sleep(0.05)
            if not _state.get("done") or _state.get("error"):
                _detail = _state.get("error") or "timeout"
                raise RuntimeError(
                    "frozen standard archive did not complete: "
                    + str(_detail)[:160])
            _archive_meta = (_state.get("result") or {}).get("meta") or {}
            _plan_id = (_state.get("archive") or {}).get("plan_id")
            _snapshot_id = _archive_meta.get("snapshot_id")
            _timeline = _read_timeline(_db, _plan_id)
            _modes = {_row.get("kind") for _row in _timeline}
            with open(os.environ["FIRE_BUNDLED_IDENTITY"], encoding="utf-8") as _identity_file:
                _identity = _json.load(_identity_file)
            with _store._connect() as _conn:
                _build_row = _conn.execute(
                    "SELECT b.code_manifest_sha256, b.data_manifest_sha256, "
                    "b.source_manifest_json, b.environment_json "
                    "FROM run_attempts a JOIN engine_builds b "
                    "ON b.id = a.engine_build_id WHERE a.job_id = ?",
                    (_job,)).fetchone()
            if _build_row is None:
                raise RuntimeError("frozen engine build receipt missing")
            _build_environment = _json.loads(_build_row["environment_json"])
            _build_manifest = _json.loads(_build_row["source_manifest_json"])
            _build_identity_ok = bool(
                _build_row
                and _build_row["code_manifest_sha256"]
                and _build_row["data_manifest_sha256"]
                and _build_row["code_manifest_sha256"] == _identity["code_manifest_sha256"]
                and _build_row["data_manifest_sha256"] == _identity["data_manifest_sha256"]
                and _build_environment.get("build_identity_sha256") == _identity["identity_sha256"]
                and _build_manifest.get("status") == "bundled")
            _paths = [_db, _db + "-wal", _db + "-shm"]
            _secure = all(
                (not _os.path.exists(_path)
                 or not (_stat.S_IMODE(_os.stat(_path).st_mode) & 0o077))
                for _path in _paths)
            _persistence_ok = bool(
                _snapshot_id and _plan_id and {"plan_version", "run_snapshot"} <= _modes
                and _secure and _build_identity_ok)
    except Exception as _exc:  # pragma: no cover - exercised in frozen binary
        _persistence_error = str(_exc)[:200]
    with open(os.path.join(os.environ["FIRE_WEB_DIR"], "index.html"), "rb") as _web_file:
        _html = _web_file.read()
    _payload = {
        "ok": (b"<html" in _html.lower()
               and bool(app.PRESETS_MOD.PRESETS)
               and hasattr(app, "Handler")
               and "terminal_real_p50" in _summary
               and _persistence_ok),
        "persistence": _persistence_ok,
        "persistence_error": _persistence_error,
        "build_identity": _build_identity_ok,
        "identity_error": _identity_error,
        "machine": _platform.machine(),
        "engine": _engine.ENGINE_VERSION,
        "numpy": _numpy.__version__,
    }
    print("FIRE_FROZEN_SMOKE=" + _json.dumps(_payload, sort_keys=True), flush=True)
    raise SystemExit(0 if _payload["ok"] else 1)
elif _FROZEN_HEADLESS:
    # Serve and block; no window, no browser. The port comes from
    # FIRE_HEADLESS_PORT (0 = pick a free one) and the chosen URL is printed on a
    # single line so the caller can wait for it rather than poll a guessed port.
    # A caller-supplied nonce is echoed back so it can prove it is talking to the
    # child it started and not to some other server that happens to be listening.
    import json as _hj
    _port = int(os.environ.get("FIRE_HEADLESS_PORT", "0") or 0)
    _httpd, _url = app.serve_background(_port, reuse=False)
    _ready = _hj.dumps({
        "url": _url,
        "nonce": os.environ.get("FIRE_HEADLESS_NONCE", ""),
        "pid": os.getpid(),
        "frozen": True,
    }, sort_keys=True)
    # Written to a file, not printed. A `--windowed` PyInstaller bundle — which is
    # what ships — has no usable stdout, so a caller waiting on a printed line
    # waits forever even though the server is up. The path is supplied by the
    # caller, so it is also how the caller knows the file is this run's.
    _ready_path = os.environ.get("FIRE_HEADLESS_READY_FILE")
    if _ready_path:
        with open(_ready_path, "w", encoding="utf-8") as _rf:
            _rf.write(_ready)
    try:
        print("FIRE_HEADLESS_READY=" + _ready, flush=True)
    except Exception:
        pass
    try:
        import time as _t
        while True:
            _t.sleep(3600)
    except KeyboardInterrupt:
        pass
elif _FROZEN:
    # Native-window mode: serve on a daemon thread, then open a real macOS
    # window (WKWebView via pywebview) on the main thread. Closing the window
    # (or Cmd+Q, or the in-page 退出 button) exits the process.
    httpd, url = app.serve_background()
    try:
        import webview
        try:
            webview.settings["ALLOW_DOWNLOADS"] = True
        except Exception:
            pass
        webview.create_window(
            "FIRE Modeling", url,
            width=1340, height=920, min_size=(980, 660))
        webview.start()              # blocks until the last window closes
    except Exception:
        # Degrade gracefully if the native window can't start on this machine:
        # classic browser mode, quit via the in-page 退出 button or the Dock.
        import time as _t
        import webbrowser as _wb
        _wb.open(url)
        while True:
            _t.sleep(3600)
    finally:
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        # Let Python unwind so log buffers and framework cleanup run.  The HTTP
        # worker is a daemon thread, so a hard os._exit is unnecessary here.
        for _stream in (sys.stdout, sys.stderr):
            try:
                _stream.flush()
            except Exception:
                pass
else:
    app.main()
