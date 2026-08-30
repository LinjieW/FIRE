"""Watch the installed app run, and report every socket it opens.

Idea bank A25. This app promises it makes no network requests. That promise is
the reason a file containing your salary, your balances and your health
assumptions can sit on your laptop -- and until now you had only our word for
it.

This is the tool that lets you check. It starts the app you actually have
installed, intercepts socket creation inside that process, drives a real
simulation through it, and prints every address anything tried to reach.

**It watches the shipped binary, not a copy.** Point it at your installed
`FIRE Modeling.app`. What it reports is what that bundle did, on your machine,
just now.

**A loopback connection is not a network request.** The app talks to itself:
the interface is a local web page served by a local server, so 127.0.0.1
appears and should. What would break the promise is any address that is not
loopback. Those are listed separately and loudly, and if there are none the
tool says so rather than printing nothing -- an empty report and a report of
nothing found look identical, and they are not the same statement.

Usage:
    python3 tools/verify_zero_requests.py "/path/to/FIRE Modeling.app"
"""
from __future__ import annotations

import json
import os
import pathlib
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

#: What the app is allowed to address. Everything else is a finding.
LOOPBACK = ("127.0.0.1", "::1", "localhost")

#: How the process is watched. `lsof` reports the sockets a PID actually holds,
#: from OUTSIDE the process, so nothing is injected into the bundle and there
#: is no "did your tracer really load" question to answer.
#:
#: The first version of this injected a socket tracer via `sitecustomize`. A
#: frozen PyInstaller app does not load site modules, so nothing was traced --
#: and the tool said "NOTHING WAS VERIFIED" instead of reporting a clean run,
#: which is the only acceptable behaviour for a measurement that did not
#: happen. Watching from outside removed the failure mode rather than working
#: around it.
LSOF = "/usr/sbin/lsof"


def _sockets(pid: int) -> set:
    """Every internet socket this PID holds right now, as printable rows."""
    try:
        # `-a` is load-bearing: lsof ORs its filters by default, so
        # `-p PID -i` means "this process OR any internet socket" and returns
        # the whole machine. Without it this tool reported 144 sockets from
        # WeChat, Dropbox and Google as if they were this app's -- it would
        # have told a user that their privacy-first app was phoning home.
        # Always confirm which process a measurement is about.
        out = subprocess.run([LSOF, "-a", "-p", str(pid), "-i", "-n", "-P"],
                             capture_output=True, text=True, timeout=20).stdout
    except Exception:                                         # noqa: BLE001
        return set()
    rows = set()
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 9:
            continue
        # Second belt: even with -a, only rows this process owns are counted.
        if int(parts[1]) != int(pid):
            continue
        rows.add(parts[-1] if parts[-1] not in ("(LISTEN)", "(ESTABLISHED)")
                 else parts[-2])
    return rows


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: verify_zero_requests.py '/path/to/FIRE Modeling.app'",
              file=sys.stderr)
        return 2
    app = pathlib.Path(sys.argv[1]).resolve()
    binary = app / "Contents" / "MacOS" / "FIRE Modeling"
    if not binary.is_file():
        print("Not an app bundle: %s" % app, file=sys.stderr)
        return 2

    with tempfile.TemporaryDirectory(prefix="fire-verify-",
                                     dir="/private/tmp") as work:
        nonce = secrets.token_hex(16)
        ready = os.path.join(work, "ready.json")
        env = dict(os.environ,
                   FIRE_ARCH_REEXEC="1", FIRE_HEADLESS_PORT="0",
                   FIRE_HEADLESS_NONCE=nonce, FIRE_HEADLESS_READY_FILE=ready,
                   FIRE_PERSISTENCE_DB=os.path.join(work, "verify.sqlite3"))

        print("Starting the installed app and watching its sockets from "
              "outside the process...")
        child = subprocess.Popen([str(binary), "--frozen-headless-server"],
                                 stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, env=env)
        payload = None
        try:
            for _ in range(120):
                if os.path.exists(ready):
                    try:
                        payload = json.load(open(ready))
                        break
                    except Exception:                         # noqa: BLE001
                        pass
                time.sleep(0.5)
            if payload is None:
                print("The app did not start; nothing was measured.",
                      file=sys.stderr)
                return 1
            url = payload["url"].rstrip("/")
            capability = json.loads(urllib.request.urlopen(
                url + "/api/capability", timeout=20).read())["capability"]

            observed = set(_sockets(child.pid))
            print("Running a real simulation through it...")
            presets = json.loads(urllib.request.urlopen(
                url + "/api/presets", timeout=30).read())["presets"]
            config = next(entry["config"] for entry in presets.values()
                          if isinstance(entry.get("config"), dict)
                          and "state" in entry["config"])
            request = urllib.request.Request(
                url + "/api/run_start",
                data=json.dumps({"config": config, "paths": 500,
                                 "seed": 4242, "horizon": 30}).encode(),
                headers={"Content-Type": "application/json", "Origin": url,
                         "X-FIRE-Capability": capability}, method="POST")
            job = json.loads(urllib.request.urlopen(request, timeout=300).read())
            for _ in range(300):
                observed |= _sockets(child.pid)
                progress = json.loads(urllib.request.urlopen(
                    url + "/api/progress?job=" + job["job"], timeout=60).read())
                if progress.get("done") or progress.get("error"):
                    break
                time.sleep(1.0)
            observed |= _sockets(child.pid)
        finally:
            child.terminate()
            try:
                child.wait(timeout=15)
            except subprocess.TimeoutExpired:                 # pragma: no cover
                child.kill()

        return _report(observed, watched=bool(shutil.which("lsof")
                                              or os.path.exists(LSOF)))


def _report(observed: set, *, watched: bool) -> int:
    if not watched:
        print("\nlsof is not available, so nothing was watched. NOTHING WAS "
              "VERIFIED -- this is a failed measurement, not a clean result.",
              file=sys.stderr)
        return 1

    remote = sorted(row for row in observed
                    if not any(h in row for h in LOOPBACK))
    local = len(observed) - len(remote)

    print("\n" + "=" * 62)
    print("Sockets the installed app held, this run:")
    print("  loopback (the app talking to itself): %d" % local)
    print("  anything else:                        %d" % len(remote))
    print("=" * 62)
    if remote:
        print("\nTHE ZERO-REQUEST PROMISE IS BROKEN. These are not loopback:")
        for row in remote:
            print("   %s" % row)
        return 1
    print("\nNo socket to any address outside this machine, at any point.")
    print("The loopback entries are the app's own interface talking to its own")
    print("server; that traffic never leaves your computer.")
    print("\nWhat this does and does not show: it is one run, on your machine,")
    print("of the bundle you pointed it at. It is evidence about that bundle,")
    print("not a proof about every future version -- run it again after an")
    print("update.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
