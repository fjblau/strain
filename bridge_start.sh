#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$DIR/.venv/bin/python"

if [[ -d "$DIR/.venv" && ! -x "$PY" ]]; then
    echo "ERROR: $DIR/.venv exists but has no usable interpreter at $PY" >&2
    echo "Rebuild it: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi
[[ -x "$PY" ]] || PY="python3"

tool="${1:-}"
[[ $# -ge 1 ]] && shift || true

case "$tool" in
    plot|read|modbus) ;;
    *)
        echo "Usage: $(basename "$0") {plot|read|modbus} [extra args...]" >&2
        echo "  plot    live web plot (bridge_plot.py), --http-port default 8080" >&2
        echo "  read    live CLI readout (bridge_read.py)" >&2
        echo "  modbus  Modbus server for PLC (bridge_modbus.py), --port default 502" >&2
        echo >&2
        echo "Only one process may hold the Phidget. To run the web UI alongside" >&2
        echo "the Modbus gateway, start the gateway first, then:" >&2
        echo "  $(basename "$0") modbus --port 1502" >&2
        echo "  $(basename "$0") plot --source modbus --modbus-port 1502 --http-port 8080" >&2
        exit 2
        ;;
esac

script="$DIR/bridge_${tool}.py"
log="$DIR/${tool}.log"
pidfile="$DIR/.bridge.${tool}.pid"

# PIDs of real bridge processes: argv[0] must be a python interpreter and one
# argument must be the script itself. Substring matching on the whole command
# line (pgrep -f) also hits shells and editors that merely mention the name.
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

# Does this invocation open the Phidget directly?
wants_phidget() {
    local t="$1"; shift
    [[ "$t" != "plot" ]] && return 0
    local prev=""
    for a in "$@"; do
        [[ "$a" == "--source=modbus" ]] && return 1
        [[ "$prev" == "--source" && "$a" == "modbus" ]] && return 1
        prev="$a"
    done
    return 0
}

# Same check against an already-running process, from its /proc cmdline.
proc_wants_phidget() {
    local pid="$1" cmd
    [[ -r "/proc/$pid/cmdline" ]] || return 1
    cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline")"
    case "$cmd" in
        *bridge_plot.py*)
            [[ "$cmd" == *"--source modbus"* || "$cmd" == *"--source=modbus"* ]] && return 1
            return 0 ;;
        *bridge_modbus.py*|*bridge_read.py*|*bridge_calibrate.py*) return 0 ;;
    esac
    return 1
}

# Refuse before stopping anything, so a rejected start never kills a healthy
# process. Instances of this same tool are ignored: they get replaced below.
if wants_phidget "$tool" "$@"; then
    for pid in $(bridge_pids 'plot|read|modbus|calibrate'); do
        cmd="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)"
        [[ "$cmd" == *"bridge_${tool}.py"* ]] && continue
        if proc_wants_phidget "$pid"; then
            echo "ERROR: pid $pid already holds the Phidget:" >&2
            echo "  $cmd" >&2
            echo "Stop it first, or start the web UI against the gateway with:" >&2
            echo "  $(basename "$0") plot --source modbus --modbus-port 1502 --http-port 8080" >&2
            exit 1
        fi
    done
fi

# Stop only a previous instance of this same tool.
"$DIR/bridge_stop.sh" "$tool"

echo "Starting bridge_${tool}.py ..."
nohup env PYTHONUNBUFFERED=1 "$PY" "$script" "$@" >"$log" 2>&1 &
pid=$!
echo "$pid" > "$pidfile"

sleep 2
if kill -0 "$pid" 2>/dev/null; then
    echo "Started bridge_${tool}.py (pid $pid)"
    echo "Log: $log"
    echo "Stop with: $DIR/bridge_stop.sh $tool"
    echo "---- startup output ----"
    tail -n 15 "$log" || true
else
    echo "FAILED to start bridge_${tool}.py. Last log lines:" >&2
    tail -n 20 "$log" >&2 || true
    rm -f "$pidfile"
    exit 1
fi
