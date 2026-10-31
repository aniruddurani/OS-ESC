#!/usr/bin/env bash
# Prove the DRC and LVS gates catch real faults: build two known-bad layouts
# and check each fails the gate it was built to fail, and no other.
# Run inside the container via `make selftest`. Prints SELFTEST PASS or exits 1.
set -u
cd "$(dirname "$0")/.."

ROOT=build/selftest
rm -rf "$ROOT"
failures=0

status_of() {  # status_of <report.json> -> the report's status, or "none"
  python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['status'])" "$1" 2>/dev/null || echo none
}

expect() {  # expect <description> <actual> <expected>
  if [ "$2" = "$3" ]; then
    echo "  ok: $1 = $2"
  else
    echo "  FAIL: $1 = $2 (expected $3)"
    failures=$((failures + 1))
  fi
}

echo "drc_bad: a metal1 spacing violation must fail drc"
make layout drc LAYOUT_SCRIPT=tests/fixtures/inv_drc_bad.tcl BUILD=$ROOT/drc_bad >/dev/null 2>&1
expect "make exit code is non-zero" "$([ $? -ne 0 ] && echo yes || echo no)" yes
expect "layout status" "$(status_of $ROOT/drc_bad/reports/layout.json)" pass
expect "drc status" "$(status_of $ROOT/drc_bad/reports/drc.json)" fail
violations=$(python3 -c "
import json
m = json.load(open('$ROOT/drc_bad/reports/drc.json'))['metrics']
print((m['magic_violations'] or 0) + (m['klayout_violations'] or 0))" 2>/dev/null || echo 0)
expect "at least one violation reported" "$([ "$violations" -ge 1 ] && echo yes || echo no)" yes

echo "stop at first failure: after a failed drc, lvs must not run on that layout"
make lvs LAYOUT_SCRIPT=tests/fixtures/inv_drc_bad.tcl BUILD=$ROOT/drc_bad >/dev/null 2>&1
expect "make exit code is non-zero" "$([ $? -ne 0 ] && echo yes || echo no)" yes
expect "lvs.json was not written" "$([ ! -e $ROOT/drc_bad/reports/lvs.json ] && echo yes || echo no)" yes

echo "lvs_bad: a missing connection must pass drc and fail lvs"
make netlist layout drc lvs LAYOUT_SCRIPT=tests/fixtures/inv_lvs_bad.tcl BUILD=$ROOT/lvs_bad >/dev/null 2>&1
expect "make exit code is non-zero" "$([ $? -ne 0 ] && echo yes || echo no)" yes
expect "drc status" "$(status_of $ROOT/lvs_bad/reports/drc.json)" pass
expect "lvs status" "$(status_of $ROOT/lvs_bad/reports/lvs.json)" fail

if [ "$failures" -eq 0 ]; then
  echo "SELFTEST PASS"
else
  echo "SELFTEST FAIL ($failures check(s) failed)"
  exit 1
fi
