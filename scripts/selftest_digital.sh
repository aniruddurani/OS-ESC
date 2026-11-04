#!/usr/bin/env bash
# Prove the digital gates catch real faults: three known-bad inputs must each fail the
# gate built for them, and a failure must stop the stages after it.
# Run inside the container via `make dselftest`. Prints DSELFTEST PASS or exits 1.
set -u
cd "$(dirname "$0")/.."

ROOT=build/dselftest
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

nonzero() {  # nonzero <exit code> -> yes/no
  [ "$1" -ne 0 ] && echo yes || echo no
}

echo "lint_dirty: a file with warnings must fail dlint"
make dlint DRTL=tests/fixtures/digital/lint_dirty.v DBUILD=$ROOT/lint >/dev/null 2>&1
expect "make exit code is non-zero" "$(nonzero $?)" yes
expect "dlint status" "$(status_of $ROOT/lint/reports/dlint.json)" fail

echo "wkc_wrong: a wrong working-counter increment must fail dsim, and dsynth must not run"
mkdir -p $ROOT/rtl_wkc
cp designs/ecat_esc/rtl/*.v $ROOT/rtl_wkc/
expect "mutation present before" "$(grep -c "(is_rd ? 2'd2 : 2'd1)" $ROOT/rtl_wkc/dgram_exec.v)" 1
sed -i "s/(is_rd ? 2'd2 : 2'd1)/2'd1/" $ROOT/rtl_wkc/dgram_exec.v
expect "mutation applied" "$(grep -c "(is_rd ? 2'd2 : 2'd1)" $ROOT/rtl_wkc/dgram_exec.v)" 0
make dsim DRTL_DIR=$ROOT/rtl_wkc DBUILD=$ROOT/wkc >/dev/null 2>&1
expect "make exit code is non-zero" "$(nonzero $?)" yes
expect "dlint status (the mutant is lint-clean)" "$(status_of $ROOT/wkc/reports/dlint.json)" pass
expect "dsim status" "$(status_of $ROOT/wkc/reports/dsim.json)" fail
make dsynth DRTL_DIR=$ROOT/rtl_wkc DBUILD=$ROOT/wkc >/dev/null 2>&1
expect "make exit code is non-zero" "$(nonzero $?)" yes
expect "dsynth report was not written" "$([ ! -e $ROOT/wkc/reports/dsynth_default.json ] && echo yes || echo no)" yes

echo "tight_clock: an impossible 1 ns core clock must fail dpnr on setup timing"
make dpnr LLCFG=selftest_tight DBUILD=$ROOT/tight >/dev/null 2>&1
expect "make exit code is non-zero" "$(nonzero $?)" yes
expect "dsynth status" "$(status_of $ROOT/tight/reports/dsynth_selftest_tight.json)" pass
expect "dpnr status" "$(status_of $ROOT/tight/reports/dpnr_selftest_tight.json)" fail
expect "dpnr names setup timing" "$(grep -c 'timing__setup' $ROOT/tight/reports/dpnr_selftest_tight.json | sed 's/^[1-9].*/yes/;s/^0$/no/')" yes

if [ "$failures" -eq 0 ]; then
  echo "DSELFTEST PASS"
else
  echo "DSELFTEST FAIL ($failures check(s) failed)"
  exit 1
fi
