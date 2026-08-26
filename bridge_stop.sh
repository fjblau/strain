#!/usr/bin/env bash
set -euo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PIDFILE="$DIR/.bridge.pid"
PATTERN='bridge_(plot|read|modbus|calibrate)\.py'

anyleft() { pgrep -f "$PATTERN" 2>/dev/null || true; }

targets=""
if [[ -f "$PIDFILE" ]]; then
    pid="$(cat "$PIDFILE" 2>/dev/null || true)"
    [[ -n "${pid:-}" ]] && targets="$pid"
fi
targets="$targets $(anyleft)"
targets="$(echo "$targets" | tr ' ' '\n' | grep -E '^[0-9]+$' | sort -u | tr '\n' ' ' || true)"

if [[ -z "${targets// /}" ]]; then
    echo "No bridge process running. Phidget is free."
    rm -f "$PIDFILE"
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

rm -f "$PIDFILE"

if [[ -z "$(anyleft)" ]]; then
    echo "Stopped. Phidget is free."
else
    echo "WARNING: still running: $(anyleft)" >&2
    exit 1
fi
