#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$DIR/.bridge.pid"
PY="$DIR/.venv/bin/python"
[[ -x "$PY" ]] || PY="python3"

tool="${1:-}"
[[ $# -ge 1 ]] && shift || true

case "$tool" in
    plot|read|modbus) ;;
    *)
        echo "Usage: $(basename "$0") {plot|read|modbus} [extra args...]" >&2
        echo "  plot    live web plot (bridge_plot.py)" >&2
        echo "  read    live CLI readout (bridge_read.py)" >&2
        echo "  modbus  Modbus server for PLC (bridge_modbus.py)" >&2
        exit 2
        ;;
esac

script="$DIR/bridge_${tool}.py"
log="$DIR/${tool}.log"

"$DIR/bridge_stop.sh"

echo "Starting bridge_${tool}.py ..."
nohup env PYTHONUNBUFFERED=1 "$PY" "$script" "$@" >"$log" 2>&1 &
pid=$!
echo "$pid" > "$PIDFILE"

sleep 2
if kill -0 "$pid" 2>/dev/null; then
    echo "Started bridge_${tool}.py (pid $pid)"
    echo "Log: $log"
    echo "Stop with: $DIR/bridge_stop.sh"
    echo "---- startup output ----"
    tail -n 15 "$log" || true
else
    echo "FAILED to start bridge_${tool}.py. Last log lines:" >&2
    tail -n 20 "$log" >&2 || true
    rm -f "$PIDFILE"
    exit 1
fi
