import argparse
import os
import struct
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Phidget22.PhidgetException import PhidgetException

from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartSerialServer, StartTcpServer
from pymodbus.transaction import ModbusRtuFramer

from bridge_common import (
    capacity_status,
    load_calibration,
    open_channel,
)

HB_ADDR = 0
STATUS_ADDR = 1
NCH_ADDR = 2
RATE_ADDR = 3

CH_BASE = 10
CH_STRIDE = 10
O_NUM = 0
O_STATE = 1
O_WEIGHT_F = 2
O_WEIGHT_I = 4
O_CAP_F = 5
O_PCT = 7

TARE_BASE = 0
CLEARTARE_BASE = 100

HR_SIZE = 512
CO_SIZE = 256

STATE_OK = 0
STATE_WARN = 1
STATE_OVER = 2
BIT_CALIBRATED = 1 << 8
BIT_VALID = 1 << 9

ST_CAL_LOADED = 1 << 0
ST_ANY_OVER = 1 << 1
ST_ANY_WARN = 1 << 2
ST_ALL_ATTACHED = 1 << 3


def f32_to_regs(value):
    b = struct.pack(">f", float(value))
    hi = (b[0] << 8) | b[1]
    lo = (b[2] << 8) | b[3]
    return [hi, lo]


def to_int16(value):
    v = int(round(value))
    if v > 32767:
        v = 32767
    elif v < -32768:
        v = -32768
    return v & 0xFFFF


def to_uint16(value):
    v = int(round(value))
    if v < 0:
        v = 0
    elif v > 65535:
        v = 65535
    return v


def quick_ratio(ch, samples=5, gap=0.01):
    vals = []
    for _ in range(samples):
        vals.append(ch.getVoltageRatio())
        time.sleep(gap)
    return sum(vals) / len(vals)


def build_context():
    hr = ModbusSequentialDataBlock(0, [0] * HR_SIZE)
    co = ModbusSequentialDataBlock(0, [False] * CO_SIZE)
    di = ModbusSequentialDataBlock(0, [False] * 16)
    ir = ModbusSequentialDataBlock(0, [0] * 16)
    slave = ModbusSlaveContext(di=di, co=co, hr=hr, ir=ir, zero_mode=True)
    return ModbusServerContext(slaves=slave, single=True), slave, threading.Lock()


def print_register_map(channels, host, port, transport, unit_id):
    print("=" * 60)
    print("PhidgetBridge 1046 -> Modbus {} server".format(transport.upper()))
    print("=" * 60)
    if transport == "tcp":
        print("Listen        : {}:{}  unit/slave id {}".format(host, port, unit_id))
    else:
        print("Serial        : {}  unit/slave id {}".format(port, unit_id))
    print("Word order    : float32 = big-endian, high word first (ABCD)")
    print("Addressing    : zero-based holding registers (FC 3/16)")
    print("-" * 60)
    print("Holding registers (global):")
    print("  {:>4}  heartbeat (uint16, increments each update, wraps)".format(HB_ADDR))
    print("  {:>4}  status word (bit0 cal_loaded, bit1 any_over,".format(STATUS_ADDR))
    print("        bit2 any_warn, bit3 all_attached)")
    print("  {:>4}  channel count".format(NCH_ADDR))
    print("  {:>4}  update interval (ms)".format(RATE_ADDR))
    print("Holding registers (per channel, base = {} + index*{}):".format(CH_BASE, CH_STRIDE))
    print("  +{}  phidget channel number".format(O_NUM))
    print("  +{}  state (0 ok,1 warn,2 over | bit8 calibrated,bit9 valid)".format(O_STATE))
    print("  +{}  weight float32 (2 regs)".format(O_WEIGHT_F))
    print("  +{}  weight int16 (rounded, signed)".format(O_WEIGHT_I))
    print("  +{}  capacity float32 (2 regs)".format(O_CAP_F))
    print("  +{}  percent of capacity (tenths, 0-1000 = 0-100.0%)".format(O_PCT))
    print("Coils (FC 1/5/15):")
    print("  {:>4}+index  write 1 = tare channel (auto-cleared)".format(TARE_BASE))
    print("  {:>4}+index  write 1 = clear tare (auto-cleared)".format(CLEARTARE_BASE))
    print("-" * 60)
    for i, c in enumerate(channels):
        base = CH_BASE + i * CH_STRIDE
        print("  index {} -> phidget CH{}: base HR {}, tare coil {}, clear coil {}".format(
            i, c, base, TARE_BASE + i, CLEARTARE_BASE + i
        ))
    print("=" * 60)


def updater(slave, chans, cal, warn_frac, interval_s, tare, stop, ds_lock):
    heartbeat = 0
    while not stop.is_set():
        any_over = False
        any_warn = False
        all_attached = True

        for i, (c, ch) in enumerate(chans):
            base = CH_BASE + i * CH_STRIDE
            with ds_lock:
                tare_coil = slave.getValues(1, TARE_BASE + i, count=1)[0]
                clear_coil = slave.getValues(1, CLEARTARE_BASE + i, count=1)[0]
            entry = cal.get(str(c))

            try:
                ratio = ch.getVoltageRatio()
                attached = True
            except PhidgetException:
                attached = False
                all_attached = False

            if attached and entry is not None:
                if tare_coil:
                    tare[c] = quick_ratio(ch) - entry["zero"]
                    with ds_lock:
                        slave.setValues(5, TARE_BASE + i, [False])
                if clear_coil:
                    tare[c] = 0.0
                    with ds_lock:
                        slave.setValues(5, CLEARTARE_BASE + i, [False])

            regs = [0] * CH_STRIDE
            regs[O_NUM] = to_uint16(c)

            if entry is None:
                regs[O_STATE] = (BIT_VALID if attached else 0)
                regs[O_WEIGHT_F], regs[O_WEIGHT_F + 1] = f32_to_regs(0.0)
                regs[O_CAP_F], regs[O_CAP_F + 1] = f32_to_regs(0.0)
            else:
                weight = entry["scale"] * (ratio - entry["zero"] - tare.get(c, 0.0)) if attached else 0.0
                state = STATE_OK
                pct = 0
                cap = entry.get("capacity", 0.0) or 0.0
                status = capacity_status(cal, c, weight, warn_frac)
                if status is not None:
                    pct = to_uint16(status["frac"] * 1000)
                    if status["state"] == "over":
                        state = STATE_OVER
                        any_over = True
                    elif status["state"] == "warn":
                        state = STATE_WARN
                        any_warn = True
                state |= BIT_CALIBRATED
                if attached:
                    state |= BIT_VALID
                regs[O_STATE] = state
                regs[O_WEIGHT_F], regs[O_WEIGHT_F + 1] = f32_to_regs(weight)
                regs[O_WEIGHT_I] = to_int16(weight)
                regs[O_CAP_F], regs[O_CAP_F + 1] = f32_to_regs(cap)
                regs[O_PCT] = pct

            with ds_lock:
                slave.setValues(3, base, regs)

        heartbeat = (heartbeat + 1) & 0xFFFF
        status_word = 0
        if cal:
            status_word |= ST_CAL_LOADED
        if any_over:
            status_word |= ST_ANY_OVER
        if any_warn:
            status_word |= ST_ANY_WARN
        if all_attached:
            status_word |= ST_ALL_ATTACHED
        with ds_lock:
            slave.setValues(3, HB_ADDR, [heartbeat, status_word])
        time.sleep(interval_s)


def main():
    parser = argparse.ArgumentParser(
        description="Expose PhidgetBridge 1046 weights to a PLC over Modbus."
    )
    parser.add_argument("--channels", type=int, nargs="+", default=[0, 3])
    parser.add_argument("--gain", type=int, default=128, choices=[1, 2, 4, 8, 16, 32, 64, 128])
    parser.add_argument("--interval", type=int, default=200, help="Update interval ms. Default: 200")
    parser.add_argument(
        "--warn-frac", type=float, default=0.9, dest="warn_frac",
        help="Fraction of capacity at which warn triggers. Default: 0.9",
    )
    parser.add_argument("--transport", choices=["tcp", "rtu"], default="tcp")
    parser.add_argument("--host", default="0.0.0.0", help="TCP bind address. Default: 0.0.0.0")
    parser.add_argument("--port", default=None, help="TCP port (default 502) or serial device for RTU.")
    parser.add_argument("--baud", type=int, default=19200, help="RTU baud rate. Default: 19200")
    parser.add_argument("--unit-id", type=int, default=1, dest="unit_id", help="Modbus slave id. Default: 1")
    args = parser.parse_args()

    interval_s = args.interval / 1000.0
    cal = load_calibration()
    context, slave, ds_lock = build_context()

    tare = {}
    opened = []
    chans = []
    try:
        for c in args.channels:
            ch, _ = open_channel(c, gain=args.gain, interval_ms=args.interval)
            opened.append(ch)
            chans.append((c, ch))
            tare[c] = 0.0
    except PhidgetException as exc:
        print("Phidget error (code {}): {}".format(exc.code, exc.details), file=sys.stderr)
        sys.exit(1)

    slave.setValues(3, NCH_ADDR, [len(chans), args.interval])

    stop = threading.Event()
    t = threading.Thread(
        target=updater,
        args=(slave, chans, cal, args.warn_frac, interval_s, tare, stop, ds_lock),
        daemon=True,
    )
    t.start()

    if args.transport == "tcp":
        port = int(args.port) if args.port else 502
        print_register_map(args.channels, args.host, port, "tcp", args.unit_id)
        print("(Ctrl+C to stop)")
        try:
            StartTcpServer(context=context, address=(args.host, port))
        except (KeyboardInterrupt, SystemExit):
            print("\nStopping...")
        except PermissionError:
            print(
                "\nPermission denied binding port {}. Ports below 1024 need root;"
                " run with sudo or use --port 1502.".format(port),
                file=sys.stderr,
            )
        finally:
            stop.set()
            for ch in opened:
                ch.close()
    else:
        if not args.port:
            print("RTU requires --port (e.g. --port /dev/ttyUSB0).", file=sys.stderr)
            stop.set()
            for ch in opened:
                ch.close()
            sys.exit(1)
        print_register_map(args.channels, args.host, args.port, "rtu", args.unit_id)
        print("(Ctrl+C to stop)")
        try:
            StartSerialServer(
                context=context,
                framer=ModbusRtuFramer,
                port=args.port,
                baudrate=args.baud,
            )
        except (KeyboardInterrupt, SystemExit):
            print("\nStopping...")
        finally:
            stop.set()
            for ch in opened:
                ch.close()


if __name__ == "__main__":
    main()
