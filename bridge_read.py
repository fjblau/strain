import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Phidget22.PhidgetException import PhidgetException

from bridge_common import apply_calibration, load_calibration, open_channel


def main():
    parser = argparse.ArgumentParser(
        description="Live read PhidgetBridge 1046 channels (raw V/V and calibrated)."
    )
    parser.add_argument(
        "--channels", type=int, nargs="+", default=[0, 3],
        help="Channels to read. Default: 0 3",
    )
    parser.add_argument(
        "--gain", type=int, default=128, choices=[1, 2, 4, 8, 16, 32, 64, 128],
        help="Bridge gain. Default: 128",
    )
    parser.add_argument(
        "--interval", type=int, default=250,
        help="Sample/print interval in ms. Default: 250",
    )
    args = parser.parse_args()

    cal = load_calibration()
    channels = []
    try:
        for c in args.channels:
            ch, _ = open_channel(c, gain=args.gain, interval_ms=args.interval)
            channels.append((c, ch))
    except PhidgetException as exc:
        print("Phidget error (code {}): {}".format(exc.code, exc.details), file=sys.stderr)
        sys.exit(1)

    print("Reading channels {} (Ctrl+C to stop)".format(args.channels))
    try:
        while True:
            parts = []
            for c, ch in channels:
                ratio = ch.getVoltageRatio()
                weight = apply_calibration(cal, c, ratio)
                if weight is None:
                    parts.append("CH{}: {:+.9f} V/V".format(c, ratio))
                else:
                    unit = cal[str(c)]["unit"]
                    parts.append(
                        "CH{}: {:+.9f} V/V = {:+9.3f} {}".format(c, ratio, weight, unit)
                    )
            print("   ".join(parts))
            time.sleep(args.interval / 1000.0)
    except (KeyboardInterrupt, SystemExit):
        print("\nStopping...")
    finally:
        for _, ch in channels:
            ch.close()


if __name__ == "__main__":
    main()
