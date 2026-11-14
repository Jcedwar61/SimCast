#!/usr/bin/env bash
# Run SimCast's tests. Each one uses a throwaway settings folder, never your real one.
#   tools/run_tests.sh          offline tests (a minute or two)
#   tools/run_tests.sh --gui    + the real window (needs a display; on Linux: xvfb-run and python3-tk)
# PYTHON=... picks the interpreter (the window test needs tkinter).
set -uo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; REPO="$(cd "$HERE/.." && pwd)"
PY="${PYTHON:-python3}"; GUI=0
for a in "$@"; do case "$a" in --gui) GUI=1;; esac; done
pass=0; fail=0; failed=()
run() {
  local name="$1"; shift
  local log; log="$(mktemp)"
  if "$@" >"$log" 2>&1; then pass=$((pass+1)); echo "PASS  $name"
  else fail=$((fail+1)); failed+=("$name"); echo "FAIL  $name"; grep -E "^FAIL |Traceback|Error" "$log" | head -8 | sed 's/^/        /'; fi
  rm -f "$log"
}
cd "$HERE/tests"
run "syntax (pyflakes)" bash -c "'$PY' -m pyflakes '$REPO/sim_cast.py' '$REPO/simcast' | grep . && exit 1 || exit 0"
run "demo race (--test)" bash -c "APPDATA=\$(mktemp -d) '$PY' '$REPO/sim_cast.py' --test --laps 4 >/dev/null"
run "test_simcast" "$PY" test_simcast.py
if [ "$GUI" = 1 ]; then
  if command -v xvfb-run >/dev/null && [ -z "${DISPLAY:-}" ]; then run "gui_test_simcast" xvfb-run -a -s "-screen 0 1400x950x24" "$PY" gui_test_simcast.py
  else run "gui_test_simcast" "$PY" gui_test_simcast.py; fi
fi
echo; echo "$pass passed, $fail failed${failed:+: ${failed[*]}}"
[ "$fail" = 0 ]
