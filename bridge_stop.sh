#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LEGACY_PIDFILE="$DIR/.bridge.pid"

target="${1:-all}"
case "$target" in
    all)                       tool_re='plot|read|modbus|calibrate' ;;
    plot|read|modbus|calibrate) tool_re="$target" ;;
    *)
        echo "Usage: $(basename "$0") [all|plot|read|modbus|calibrate]" >&2
        exit 2
        ;;
esac

# PIDs of real bridge processes: argv[0] must be a python interpreter and one
# argument must be the script itself. Substring matching on the whole command
# line (pgrep -f) also hits shells and editors that merely mention the name,
# and killing those would take out an innocent terminal.
bridge_pids() {
    local re="$1" pid args a base
    for pid in /proc/[0-9]*; do
        pid="${pid#/proc/}"
        [[ -r "/proc/$pid/cmdline" ]] || continue
        mapfile -d '' -t args < "/proc/$pid/cmdline" 2>/dev/null || continue
        (( ${#args[@]} >= 2 )) || continue
        [[ "$(basename -- "${args[0]}")" == python* ]] || continue
        for a in "${args[@]:1}"; do
            base="$(basename -- "$a")"
            if [[ "$base" =~ ^bridge_($re)\.py$ ]]; then
                echo "$pid"
                break
            fi
        done
    done
}

anyleft() { bridge_pids "$tool_re"; }

pidfiles=()
if [[ "$target" == "all" ]]; then
    for f in "$DIR"/.bridge.*.pid "$LEGACY_PIDFILE"; do
        [[ -f "$f" ]] && pidfiles+=("$f")
    done
else
    [[ -f "$DIR/.bridge.${target}.pid" ]] && pidfiles+=("$DIR/.bridge.${target}.pid")
fi

# Pidfiles are only used for cleanup: the /proc scan already finds every real
# bridge process, and a stale pidfile must never be trusted to name a target.
targets="$(anyleft | sort -u | tr '\n' ' ')"

cleanup_pidfiles() {
    for f in ${pidfiles+"${pidfiles[@]}"}; do
        rm -f "$f"
    done
    [[ "$target" == "all" ]] && rm -f "$LEGACY_PIDFILE"
    return 0
}

if [[ -z "${targets// /}" ]]; then
    if [[ "$target" == "all" ]]; then
        echo "No bridge process running. Phidget is free."
    else
        echo "No bridge_${target}.py process running."
    fi
    cleanup_pidfiles
    exit 0
fi

echo "Stopping bridge process(es): $targets"
kill $targets 2>/dev/null || true

for _ in $(seq 1 20); do
    [[ -z "$(anyleft)" ]] && break
    sleep 0.25
done

if [[ -n "$(anyleft)" ]]; then
    echo "Process(es) did not exit, forcing: $(anyleft)"
    kill -9 $(anyleft) 2>/dev/null || true
    sleep 0.5
fi

cleanup_pidfiles

if [[ -z "$(anyleft)" ]]; then
    if [[ "$target" == "all" ]]; then
        echo "Stopped. Phidget is free."
    else
        echo "Stopped bridge_${target}.py."
    fi
else
    echo "WARNING: still running: $(anyleft)" >&2
    exit 1
fi
