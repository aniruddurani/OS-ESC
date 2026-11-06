#!/usr/bin/env bash
# Prove the FPGA board gates catch real faults (the ESC on a DE2-115). Runs on the Windows
# host through `./flow bselftest`, with the board cabled and powered (SW19 on RUN):
#   1. an RTL mutant (LRW working counter one short), built and loaded, fails bnet; with
#      that image still on the board, bnet and bsoem of the clean build are errors
#   2. a flipped expected byte and a dropped reply in the harness fail bnet
#   3. two clean bnet runs back to back both pass (the soft reset between runs works)
#   4. an impossible core clock (200 MHz) fails bbuild on setup slack
# `./flow bselftest STEP=nolink`, run with the board's cable unplugged, checks that bnet
# ends as error, never pass, without a link. Each step prints BSELFTEST PASS or exits 1.
set -u
cd "$(dirname "$0")/.."

STEP=${1:-main}
ROOT=build/board_selftest
MAKE="mingw32-make --no-print-directory -f board.mk"
CLEAN_ID=build/ecat_esc/board/de2_115/build_id.txt     # checks on the clean build's image
failures=0

status_of() {  # status_of <report.json> -> the report's status, or "none"
  python -c "import json,sys; print(json.load(open(sys.argv[1]))['status'])" "$1" 2>/dev/null || echo none
}

metric_of() {  # metric_of <report.json> <key> -> the metric's value, or "none"
  python -c "import json,sys; print(json.load(open(sys.argv[1]))['metrics'][sys.argv[2]])" "$1" "$2" 2>/dev/null || echo none
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

stamped() {  # stamped <dbuild> <stage>...: the prerequisites this check takes as given
  local d=$1; shift
  mkdir -p "$d/stamps"
  for s in "$@"; do touch "$d/stamps/$s.pass"; done
}

clean_board() {  # load the clean build again and check the link is back
  rm -f build/ecat_esc/stamps/bprog.pass
  $MAKE bprog >/dev/null 2>&1
  expect "clean build back on the board (bprog)" "$(status_of build/ecat_esc/reports/bprog.json)" pass
}

if [ "$STEP" = nolink ]; then
  rm -rf "$ROOT/nolink"
  echo "nolink: with the board's cable unplugged, bnet must end as error"
  nic=$(powershell -NoProfile -Command "(Get-NetAdapter -Name 'Ethernet').Status" | tr -d '\r')
  if [ "$nic" != Disconnected ]; then
    echo "  the board NIC is '$nic': unplug the board's cable first"
    exit 1
  fi
  stamped "$ROOT/nolink" dsim bbuild bprog
  $MAKE bnet DBUILD="$ROOT/nolink" BUILD_ID_FILE=$CLEAN_ID >/dev/null 2>&1
  expect "make exit code is non-zero" "$(nonzero $?)" yes
  expect "bnet status" "$(status_of $ROOT/nolink/reports/bnet.json)" error
  expect "no bnet pass stamp" "$([ ! -e $ROOT/nolink/stamps/bnet.pass ] && echo yes || echo no)" yes
else
  rm -rf "$ROOT"
  mkdir -p "$ROOT"

  echo "net_wkc: an LRW working counter one short, on the board, must fail bnet with mismatches"
  d=$ROOT/net_wkc
  mkdir -p "$d/rtl"
  cp designs/ecat_esc/rtl/*.v "$d/rtl/"
  pat="(wr_ok ? (is_rd ? 2'd2 : 2'd1)"
  expect "mutation present before" "$(grep -c -- "$pat" $d/rtl/dgram_exec.v)" 1
  sed -i -- "s/(wr_ok ? (is_rd ? 2'd2 : 2'd1)/(wr_ok ? (is_rd ? (cmd_l ? 2'd1 : 2'd2) : 2'd1)/" $d/rtl/dgram_exec.v
  expect "mutation applied" "$(grep -c -- "$pat" $d/rtl/dgram_exec.v)" 0
  stamped "$d" dsim
  $MAKE bbuild DBUILD="$d" DRTL_DIR="$d/rtl" >/dev/null 2>&1
  expect "the mutant builds (bbuild)" "$(status_of $d/reports/bbuild.json)" pass
  $MAKE bprog DBUILD="$d" DRTL_DIR="$d/rtl" >/dev/null 2>&1
  expect "the mutant is loaded (bprog)" "$(status_of $d/reports/bprog.json)" pass
  $MAKE bnet DBUILD="$d" DRTL_DIR="$d/rtl" >/dev/null 2>&1
  expect "make exit code is non-zero" "$(nonzero $?)" yes
  expect "bnet status" "$(status_of $d/reports/bnet.json)" fail
  expect "mismatches > 0" "$(metric_of $d/reports/bnet.json mismatches | sed 's/^[1-9][0-9]*$/yes/')" yes

  echo "wrong_image: with the mutant still loaded, the clean build's bnet and bsoem must be errors"
  d=$ROOT/wrong_image
  stamped "$d" dsim bbuild bprog
  $MAKE bnet DBUILD="$d" BUILD_ID_FILE=$CLEAN_ID >/dev/null 2>&1
  expect "bnet status" "$(status_of $d/reports/bnet.json)" error
  $MAKE bsoem DBUILD="$d" BUILD_ID_FILE=$CLEAN_ID SOEM_DIR=build/ecat_esc/board/soem >/dev/null 2>&1
  expect "bsoem status" "$(status_of $d/reports/bsoem.json)" error
  expect "bsoem names the image" "$(grep -c 'holds build' $d/reports/bsoem.json | sed 's/^[1-9].*/yes/;s/^0$/no/')" yes
  clean_board

  for sab in checker drop-reply; do
    d=$ROOT/net_$sab
    echo "net_$sab: --sabotage $sab must fail bnet"
    stamped "$d" dsim bbuild bprog
    $MAKE bnet DBUILD="$d" BUILD_ID_FILE=$CLEAN_ID BNET_ARGS="--sabotage $sab" >/dev/null 2>&1
    expect "make exit code is non-zero" "$(nonzero $?)" yes
    expect "bnet status" "$(status_of $d/reports/bnet.json)" fail
  done
  expect "net_checker mismatches" "$(metric_of $ROOT/net_checker/reports/bnet.json mismatches)" 1
  expect "net_drop-reply missing" "$(metric_of $ROOT/net_drop-reply/reports/bnet.json missing)" 1

  echo "back_to_back: two clean bnet runs in a row must both pass"
  for i in 1 2; do
    d=$ROOT/b2b_$i
    stamped "$d" dsim bbuild bprog
    $MAKE bnet DBUILD="$d" BUILD_ID_FILE=$CLEAN_ID >/dev/null 2>&1
    expect "bnet run $i" "$(status_of $d/reports/bnet.json)" pass
  done

  echo "tight_clock: a 200 MHz core clock must fail bbuild on setup slack"
  d=$ROOT/tight
  stamped "$d" dsim
  $MAKE bbuild DBUILD="$d" PLL_MUL=4 PLL_DIV=1 >/dev/null 2>&1
  expect "make exit code is non-zero" "$(nonzero $?)" yes
  expect "bbuild status" "$(status_of $d/reports/bbuild.json)" fail
  expect "bbuild names setup slack" "$(grep -c 'setup slack' $d/reports/bbuild.json | sed 's/^[1-9].*/yes/;s/^0$/no/')" yes
fi

if [ "$failures" -eq 0 ]; then
  echo "BSELFTEST PASS ($STEP)"
else
  echo "BSELFTEST FAIL ($STEP: $failures check(s) failed)"
  exit 1
fi
