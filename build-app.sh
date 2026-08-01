#!/bin/bash
# Build and validate a self-contained universal2 FIRE Modeling.app.
# The installed app is not touched until the frozen candidate passes every gate.
set -euo pipefail
cd "$(dirname "$0")"

PROJ="$(pwd)"
BUILD="$PROJ/.build"
VENV="$BUILD/venv"
PY_SYS="/Library/Frameworks/Python.framework/Versions/3.10/bin/python3"
ICON="$PROJ/AppIcon.icns"
LOCK="$PROJ/build-requirements.lock"
NUMPY_VERSION="2.2.6"
CANDIDATE_DIST="$BUILD/candidate-dist"
CANDIDATE="$CANDIDATE_DIST/FIRE Modeling.app"
INSTALLED="$PROJ/FIRE Modeling.app"
FROZEN_IDENTITY_DIR="$BUILD/frozen-identity"
FROZEN_IDENTITY="$FROZEN_IDENTITY_DIR/frozen_build_identity.json"
export PYINSTALLER_CONFIG_DIR="$BUILD/pyinstaller-config"

fail() {
  echo "!! $*" >&2
  exit 1
}

arch_available() {
  /usr/bin/arch "-$1" /usr/bin/true >/dev/null 2>&1
}

if arch_available arm64; then
  BUILD_ARCH="arm64"
elif arch_available x86_64; then
  BUILD_ARCH="x86_64"
else
  fail "neither arm64 nor x86_64 execution is available"
fi

run_build_arch() {
  /usr/bin/arch "-$BUILD_ARCH" "$@"
}

# Inspect every Mach-O, not only files with executable permission or extensions.
verify_universal_machos() {
  local root="$1" label="$2" path description arches
  local found=0 failures=0
  while IFS= read -r -d '' path; do
    description="$(/usr/bin/file -b "$path" 2>/dev/null || true)"
    case "$description" in
      *Mach-O*) ;;
      *) continue ;;
    esac
    found=$((found + 1))
    arches="$(/usr/bin/lipo -archs "$path" 2>/dev/null || true)"
    case " $arches " in *" arm64 "*) ;; *) echo "!! $label missing arm64: $path ($arches)" >&2; failures=$((failures + 1));; esac
    case " $arches " in *" x86_64 "*) ;; *) echo "!! $label missing x86_64: $path ($arches)" >&2; failures=$((failures + 1));; esac
  done < <(find "$root" -type f -print0)
  [ "$found" -gt 0 ] || { echo "!! no Mach-O files found in $label" >&2; return 1; }
  [ "$failures" -eq 0 ] || return 1
  echo "==> universal2 audit passed: $label ($found Mach-O files)"
}

validate_locked_dependencies() {
  "$VENV/bin/python" - "$LOCK" <<'PY'
import importlib.metadata
import pathlib
import re
import sys

def canonical(name):
    return re.sub(r"[-_.]+", "-", name).lower()

lock = pathlib.Path(sys.argv[1])
expected = {}
for raw in lock.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([^\s]+)", line)
    if not match:
        raise SystemExit(f"invalid unpinned lock entry: {raw!r}")
    expected[canonical(match.group(1))] = match.group(2)

installed = {
    canonical(dist.metadata["Name"]): dist.version
    for dist in importlib.metadata.distributions()
    if dist.metadata["Name"]
}
bad = [f"{name}: expected {version}, got {installed.get(name, 'missing')}"
       for name, version in expected.items() if installed.get(name) != version]
allowed = set(expected) | {"numpy", "pip", "setuptools"}
bad.extend(f"unexpected unpinned package: {name}=={version}"
           for name, version in installed.items() if name not in allowed)
if bad:
    print("\n".join(bad), file=sys.stderr)
    raise SystemExit(1)
PY
}

recreate_venv() {
  rm -rf "$VENV"
  run_build_arch "$PY_SYS" -m venv "$VENV"
}

validate_venv_base() {
  local arches
  [ -x "$VENV/bin/python" ] || return 1
  "$VENV/bin/python" -c 'import sys; raise SystemExit(sys.version_info[:2] != (3, 10))' || return 1
  arches="$(/usr/bin/lipo -archs "$VENV/bin/python" 2>/dev/null || true)"
  case " $arches " in *" arm64 "*) ;; *) return 1;; esac
  case " $arches " in *" x86_64 "*) ;; *) return 1;; esac
}

validate_installed_numpy() {
  local numpy_root
  "$VENV/bin/python" -c "import numpy; raise SystemExit(numpy.__version__ != '$NUMPY_VERSION')" || return 1
  numpy_root="$("$VENV/bin/python" -c 'import pathlib, numpy; print(pathlib.Path(numpy.__file__).parent)')" || return 1
  verify_universal_machos "$numpy_root" "installed NumPy" >/dev/null || return 1
}

run_regression_for_arch() {
  local cpu="$1" log="$BUILD/regression-$1.log"
  echo "==> regression gate: $cpu ..."
  if ! /usr/bin/arch "-$cpu" "$VENV/bin/python" "$PROJ/tests/test_regression.py" >"$log" 2>&1; then
    tail -20 "$log" >&2
    fail "$cpu regression gate failed"
  fi
  tail -3 "$log"
}

mkdir -p "$BUILD"
[ -x "$PY_SYS" ] || fail "required universal2 Python 3.10 not found at $PY_SYS"
[ -f "$LOCK" ] || fail "missing build dependency lock: $LOCK"

# This gate is mandatory, including builds invoked with SKIP_TESTS=1.
echo "==> JavaScript syntax gate ..."
"$PY_SYS" "$PROJ/tests/js_syntax_check.py" "$PROJ/web"

# Recreate an incompatible venv; otherwise validate and repair exact pins.
VENV_RECREATED=0
if ! validate_venv_base; then
  echo "==> recreating incompatible build venv ..."
  recreate_venv
  VENV_RECREATED=1
fi

if ! validate_locked_dependencies >/dev/null 2>&1; then
  echo "==> repairing build environment from exact lock ..."
  if [ "$VENV_RECREATED" -eq 0 ]; then
    recreate_venv
  fi
  run_build_arch "$VENV/bin/python" -m pip install --quiet --upgrade -r "$LOCK"
fi
validate_locked_dependencies || fail "build dependency lock validation failed"
run_build_arch "$VENV/bin/python" -m pip check

# Require exactly one pinned, locally merged NumPy wheel and audit the wheel's
# native payload before trusting it for install or auto-repair.
shopt -s nullglob
U2_NUMPY=("$BUILD"/wheels/merged/numpy-"$NUMPY_VERSION"-*universal2.whl)
shopt -u nullglob
[ "${#U2_NUMPY[@]}" -eq 1 ] || fail "expected exactly one NumPy $NUMPY_VERSION universal2 wheel in .build/wheels/merged"
WHEEL_AUDIT="$BUILD/numpy-wheel-audit"
rm -rf "$WHEEL_AUDIT"
mkdir -p "$WHEEL_AUDIT"
/usr/bin/ditto -x -k "${U2_NUMPY[0]}" "$WHEEL_AUDIT"
verify_universal_machos "$WHEEL_AUDIT/numpy" "NumPy wheel" || fail "NumPy wheel is not fully universal2"
rm -rf "$WHEEL_AUDIT"

if ! validate_installed_numpy; then
  echo "==> repairing NumPy from audited universal2 wheel ..."
  run_build_arch "$VENV/bin/python" -m pip install --quiet --force-reinstall --no-deps "${U2_NUMPY[0]}"
fi
validate_installed_numpy || fail "installed NumPy is not the pinned universal2 build"

# A local-wheel install records the developer's absolute path. It is neither
# needed at runtime nor appropriate in reused/build metadata.
find "$VENV/lib" -path '*/numpy-*.dist-info/direct_url.json' -type f -delete

if [ "${SKIP_TESTS:-}" != "1" ]; then
  for cpu in arm64 x86_64; do
    if arch_available "$cpu"; then
      run_regression_for_arch "$cpu"
    else
      echo "==> regression gate: $cpu unavailable on this host (skipped)"
    fi
  done
else
  echo "==> regression gates explicitly bypassed with SKIP_TESTS=1"
fi

# Reuse the icon from the installed app without modifying that app.
if [ ! -f "$ICON" ] && [ -f "$INSTALLED/Contents/Resources/AppIcon.icns" ]; then
  cp "$INSTALLED/Contents/Resources/AppIcon.icns" "$ICON"
fi
ICON_FLAG=()
[ -f "$ICON" ] && ICON_FLAG=(--icon "$ICON")

echo "==> freezing isolated universal2 candidate ..."
rm -rf "$CANDIDATE_DIST" "$BUILD/work"
rm -rf "$FROZEN_IDENTITY_DIR"
mkdir -p "$FROZEN_IDENTITY_DIR"
run_build_arch "$VENV/bin/python" "$PROJ/tools/frozen_identity.py" \
  --root "$PROJ" --output "$FROZEN_IDENTITY"
[ -s "$FROZEN_IDENTITY" ] || fail "missing frozen runtime identity"
run_build_arch "$VENV/bin/pyinstaller" --noconfirm --clean --windowed \
  --target-arch universal2 \
  --name "FIRE Modeling" \
  "${ICON_FLAG[@]}" \
  --osx-bundle-identifier com.local.fire-modeling \
  --distpath "$CANDIDATE_DIST" --workpath "$BUILD/work" --specpath "$BUILD" \
  --add-data "$PROJ/web:web" \
  --add-data "$FROZEN_IDENTITY:release_identity" \
  --paths "$PROJ/server" --paths "$PROJ/engine" \
  --collect-all numpy \
  --collect-all webview --hidden-import webview.platforms.cocoa \
  --exclude-module numpy.f2py --exclude-module numpy.tests \
  --exclude-module numpy.distutils \
  --hidden-import app --hidden-import engine_v98 --hidden-import presets --hidden-import build_report \
  --hidden-import fire_v6_model --hidden-import fire_v7_model --hidden-import fire_v8_model \
  --hidden-import fire_v9_1_model --hidden-import fire_v9_2_model --hidden-import fire_v9_3_model \
  --hidden-import fire_v9_4_model --hidden-import fire_v9_5_model --hidden-import fire_v9_6_model \
  --hidden-import fire_v9_8_model --hidden-import fire_v95_actual_baseline \
  --hidden-import fire_tax_true --hidden-import fire_rules_x --hidden-import fire_returns_x \
  --hidden-import housing --hidden-import csv_import --hidden-import ssa_import \
  "$PROJ/pyi_main.py"
[ -d "$CANDIDATE" ] || fail "PyInstaller did not produce the candidate app"

echo "==> pruning NumPy development-only trees from candidate ..."
NUMPY_RES="$CANDIDATE/Contents/Resources/numpy"
NUMPY_FRAMEWORKS="$CANDIDATE/Contents/Frameworks/numpy"
[ -d "$NUMPY_RES" ] || fail "candidate is missing bundled NumPy resources"
for numpy_tree in "$NUMPY_RES" "$NUMPY_FRAMEWORKS"; do
  [ -d "$numpy_tree" ] || continue
  find "$numpy_tree" -type d -name tests -prune -exec rm -rf {} \;
  find "$numpy_tree" -type l -name tests -delete
  rm -rf "$numpy_tree/f2py" "$numpy_tree/distutils" "$numpy_tree/doc" "$numpy_tree/random/_examples"
done
find "$CANDIDATE" -name direct_url.json \( -type f -o -type l \) -delete

"$PY_SYS" "$PROJ/tests/js_syntax_check.py" "$CANDIDATE/Contents/Resources/web"
verify_universal_machos "$CANDIDATE" "frozen candidate" || fail "candidate contains a single-architecture Mach-O"

# Pruning changes the bundle after PyInstaller's signing pass, so refresh the
# local ad-hoc signature and verify it before launch.
/usr/bin/codesign --force --deep --sign - "$CANDIDATE"
/usr/bin/codesign --verify --deep --strict "$CANDIDATE"

echo "==> smoke-testing the actual frozen candidate ..."
"$VENV/bin/python" "$PROJ/tests/frozen_smoke.py" "$CANDIDATE"

if [ "${CANDIDATE_ONLY:-}" = "1" ]; then
  echo "==> candidate validated and retained without promotion: $CANDIDATE"
  du -sh "$CANDIDATE"
  exit 0
fi

# This script builds and validates a candidate. It does not install one.
#
# It used to end with its own promotion block: move the installed app aside, move
# the candidate in, and restore the old one if the `mv` failed. That block was
# not wrong so much as narrower than §8 — it rolled back a failed move, but ran
# no gates against the installed path afterwards, so a candidate that passed
# where it was built and then failed where it runs would have been left
# installed, and a rollback was never verified. §8 needs the move and the
# post-move gates in one orchestrator, which is tools/promote.py.
#
# While that block existed, the ordinary way to install the app was also the way
# to bypass every installed-path gate — and it was the *default*, reached by
# running this script with no arguments. So it is gone rather than merely
# discouraged. BUILD_ONLY=1 is still accepted and is now the only behaviour.
echo "==> validated candidate left at $CANDIDATE"
du -sh "$CANDIDATE"
echo "==> DONE (candidate not installed)"
echo
echo "This script no longer installs. To promote this candidate, run the §8"
echo "orchestrator, which is the only supported installation path:"
echo "    python3 tools/promote.py --tag <release-tag>"
echo "It re-checks preconditions, gates the candidate, retains the previous"
echo "install, gates the installed path, and verifies any rollback."
exit 0
