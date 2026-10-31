#!/usr/bin/env bash
# Check that the tools and PDK files the Makefile points at exist.
#
# Usage: check_env.sh OUT_JSON NAME=path [NAME=path ...]
# Writes {"status": "pass"|"error", "missing": [...], "versions": {...}}
# and exits 0 only on pass.
set -uo pipefail

out=$1
shift

missing=()

for pair in "$@"; do
  name=${pair%%=*}
  path=${pair#*=}
  if [ ! -e "$path" ]; then
    missing+=("$name=$path")
  fi
done

if [ -z "${SPICE_USERINIT_DIR:-}" ] || [ ! -f "$SPICE_USERINIT_DIR/.spiceinit" ]; then
  missing+=("SPICE_USERINIT_DIR/.spiceinit=${SPICE_USERINIT_DIR:-<unset>}/.spiceinit")
fi

declare -A versions
version_of() {
  case "$1" in
    xschem)  xschem -v 2>&1 | head -n 1 ;;
    magic)   magic --version 2>&1 | head -n 1 ;;
    netgen)  netgen -batch quit 2>&1 | grep -i -m 1 'netgen' ;;
    ngspice) ngspice -v 2>&1 | grep -i -m 1 'ngspice-' ;;
    klayout) klayout -v 2>&1 | head -n 1 ;;
    python3) python3 --version 2>&1 ;;
  esac
}

for tool in xschem magic netgen ngspice klayout python3; do
  if command -v "$tool" >/dev/null 2>&1; then
    versions[$tool]=$(version_of "$tool" | tr -d '\r' | sed 's/[[:space:]]*$//')
  else
    missing+=("tool:$tool")
  fi
done

status=pass
[ ${#missing[@]} -eq 0 ] || status=error

mkdir -p "$(dirname "$out")"
{
  printf '{\n  "status": "%s",\n  "missing": [' "$status"
  sep=""
  for m in "${missing[@]}"; do printf '%s"%s"' "$sep" "$m"; sep=", "; done
  printf '],\n  "versions": {'
  sep=""
  for t in "${!versions[@]}"; do
    v=${versions[$t]//\\/\\\\}
    v=${v//\"/\\\"}
    printf '%s\n    "%s": "%s"' "$sep" "$t" "$v"
    sep=","
  done
  printf '\n  }\n}\n'
} > "$out"

cat "$out"
[ "$status" = pass ]
