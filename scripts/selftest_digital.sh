#!/usr/bin/env bash
# Prove the digital gates catch real faults: each known-bad input must fail the gate
# built for them (dlint, dsim, dpnr, and seven for dnet), and a failure must stop the
# stages after it.
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

# --- dnet: the network simulation must catch faults on its own -----------------------
# Each RTL mutant must be lint-clean (so a lint failure cannot pass for a catch). dsim is
# then skipped by stamping it, because dsim catches some of these too and the point here
# is that dnet does; the stamp lives only under build/dselftest.
metric_of() {  # metric_of <report.json> <key> -> the metric's value, or "none"
  python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['metrics'][sys.argv[2]])" "$1" "$2" 2>/dev/null || echo none
}

net_mutant() {  # net_mutant <name> <file> <present-pattern> <sed expression> [netsim args]
  local name=$1 file=$2 pat=$3 expr=$4 args=${5:-}
  mkdir -p $ROOT/rtl_$name
  cp designs/ecat_esc/rtl/*.v $ROOT/rtl_$name/
  expect "mutation present before" "$(grep -c -- "$pat" $ROOT/rtl_$name/$file)" 1
  sed -i -- "$expr" $ROOT/rtl_$name/$file
  expect "mutation applied" "$(grep -c -- "$pat" $ROOT/rtl_$name/$file)" 0
  make dlint DRTL_DIR=$ROOT/rtl_$name DBUILD=$ROOT/$name >/dev/null 2>&1
  expect "dlint status (the mutant is lint-clean)" "$(status_of $ROOT/$name/reports/dlint.json)" pass
  touch $ROOT/$name/stamps/dsim.pass
  make dnet DRTL_DIR=$ROOT/rtl_$name DBUILD=$ROOT/$name NETSIM_ARGS="$args" >/dev/null 2>&1
  expect "make exit code is non-zero" "$(nonzero $?)" yes
  expect "dnet status" "$(status_of $ROOT/$name/reports/dnet.json)" fail
  expect "no dnet pass stamp" "$([ ! -e $ROOT/$name/stamps/dnet.pass ] && echo yes || echo no)" yes
}

echo "net_wkc: a working counter one short on LRW only must fail dnet with mismatches"
net_mutant net_wkc dgram_exec.v "(wr_ok ? (is_rd ? 2'd2 : 2'd1)" \
  "s/(wr_ok ? (is_rd ? 2'd2 : 2'd1)/(wr_ok ? (is_rd ? (cmd_l ? 2'd1 : 2'd2) : 2'd1)/"
expect "mismatches > 0" "$(metric_of $ROOT/net_wkc/reports/dnet.json mismatches | sed 's/^[1-9][0-9]*$/yes/')" yes

echo "net_gap: a transmitter that always waits the full 12-byte gap must fail dnet at 100 ppm"
net_mutant net_gap mii_tx.v "GAP_MIN = 5'd15" "s/GAP_MIN = 5'd15/GAP_MIN = 5'd23/"
expect "flags_seen has overflow or underrun" "$(metric_of $ROOT/net_gap/reports/dnet.json flags_seen | python3 -c "import sys; v=sys.stdin.read().strip(); print('yes' if v.isdigit() and int(v) & 6 else 'no')")" yes

echo "net_txfifo: four-entry FIFOs must fail dnet with an overflow or underrun flag"
net_mutant net_txfifo esc_top.v "parameter FIFO_DEPTH  = 16" "s/parameter FIFO_DEPTH  = 16/parameter FIFO_DEPTH  = 4/"
expect "flags_seen has overflow or underrun" "$(metric_of $ROOT/net_txfifo/reports/dnet.json flags_seen | python3 -c "import sys; v=sys.stdin.read().strip(); print('yes' if v.isdigit() and int(v) & 7 else 'no')")" yes

echo "net_ramlat: process RAM one clock slower must fail dnet with a 76 MHz core (6 clocks per byte)"
# At 100 MHz a byte is 8 core clocks and the extra clock is harmless; 76 MHz is the lowest
# clean core clock measured (at exactly 75 MHz an RX clock faster than nominal leaves 5).
net_mutant net_ramlat pdram_flop.v "mem_rvalid <= !rst && rv2;" \
  "s/^    reg           rv1;/    reg           rv1; reg rv3; reg [7:0] rd3;/;s/        mem_rdata  <= pick2\[8\*grp2 +: 8\];/        rd3 <= pick2[8*grp2 +: 8]; mem_rdata <= rd3;/;s/        mem_rvalid <= !rst \&\& rv2;/        rv3 <= !rst \&\& rv2; mem_rvalid <= !rst \&\& rv3;/" \
  "--core-mhz 76"
expect "mismatches or late" "$(python3 -c "import json,sys; m=json.load(open(sys.argv[1]))['metrics']; print('yes' if m['mismatches'] > 0 or m['late'] > 0 else 'no')" $ROOT/net_ramlat/reports/dnet.json 2>/dev/null || echo no)" yes

echo "net_diagfcs: a CRC-failure counter that never counts must fail dnet's fault evidence"
net_mutant net_diagfcs diag.v "c_crc_failures <= sat_add(c_crc_failures, 2'd1)" \
  "s/c_crc_failures <= sat_add(c_crc_failures, 2'd1)/c_crc_failures <= sat_add(c_crc_failures, 2'd0)/"
expect "an error names crc in the fault evidence" "$(grep -c 'fault evidence: .*crc' $ROOT/net_diagfcs/reports/dnet.json | sed 's/^[1-9].*/yes/;s/^0$/no/')" yes

# The checker itself: the unchanged RTL and bench, with one fault injected into the harness.
for sab in checker drop-reply; do
  name=net_$sab
  echo "$name: --sabotage $sab must fail dnet"
  mkdir -p $ROOT/$name/stamps
  touch $ROOT/$name/stamps/dlint.pass $ROOT/$name/stamps/dsim.pass
  make dnet DBUILD=$ROOT/$name NETSIM_BIN=$(pwd)/build/ecat_esc/netsim/obj/Vesc_top \
    NETSIM_ARGS="--sabotage $sab" >/dev/null 2>&1
  expect "make exit code is non-zero" "$(nonzero $?)" yes
  expect "dnet status" "$(status_of $ROOT/$name/reports/dnet.json)" fail
done
expect "net_checker mismatches" "$(metric_of $ROOT/net_checker/reports/dnet.json mismatches)" 1
expect "net_drop-reply missing" "$(metric_of $ROOT/net_drop-reply/reports/dnet.json missing)" 1

if [ "$failures" -eq 0 ]; then
  echo "DSELFTEST PASS"
else
  echo "DSELFTEST FAIL ($failures check(s) failed)"
  exit 1
fi
