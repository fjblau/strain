import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from Phidget22.PhidgetException import PhidgetException

from bridge_common import (
    CAL_FILE,
    average_ratio,
    load_calibration,
    open_channel,
    save_calibration,
)


def prompt_float(message):
    while True:
        try:
            return float(input(message).strip())
        except ValueError:
            print("  Please enter a number.")


def prompt_optional_float(message):
    raw = input(message).strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def calibrate_channel(channel, gain, samples, existing=None):
    print()
    print("=" * 48)
    print("Calibrating channel {}".format(channel))
    print("=" * 48)

    ch, interval = open_channel(channel, gain=gain)
    try:
        unit = input("Unit label (e.g. g, kg, N, lb): ").strip() or "g"
        known = prompt_float("Known reference weight in {}: ".format(unit))
        cap_default = (existing or {}).get("capacity")
        cap_msg = "Rated capacity in {} (blank to skip{}): ".format(
            unit, ", current {}".format(cap_default) if cap_default else ""
        )
        capacity = prompt_optional_float(cap_msg)
        if capacity is None:
            capacity = cap_default

        input("\nRemove ALL load from channel {}, then press Enter...".format(channel))
        print("  Sampling zero...", flush=True)
        zero = average_ratio(ch, samples, interval)

        input(
            "\nPlace the {}{} reference on channel {}, then press Enter...".format(
                known, unit, channel
            )
        )
        print("  Sampling load...", flush=True)
        loaded = average_ratio(ch, samples, interval)
    finally:
        ch.close()

    span = loaded - zero
    if span == 0:
        raise ZeroDivisionError(
            "No change between zero and load on channel {} - check gauge wiring.".format(channel)
        )
    scale = known / span

    print()
    print("  zero ratio   : {:.9f} V/V".format(zero))
    print("  loaded ratio : {:.9f} V/V".format(loaded))
    print("  scale        : {:.4f} {} per (V/V)".format(scale, unit))
    if capacity:
        print("  capacity     : {} {}".format(capacity, unit))
    entry = {"zero": zero, "scale": scale, "unit": unit, "gain": gain}
    if capacity:
        entry["capacity"] = capacity
    return entry


def main():
    parser = argparse.ArgumentParser(
        description="Two-point calibration for a PhidgetBridge 1046."
    )
    parser.add_argument(
        "--channels", type=int, nargs="+", default=[0, 3],
        help="Channels to calibrate. Default: 0 3",
    )
    parser.add_argument(
        "--gain", type=int, default=128, choices=[1, 2, 4, 8, 16, 32, 64, 128],
        help="Bridge gain. Default: 128",
    )
    parser.add_argument(
        "--samples", type=int, default=50,
        help="Samples averaged per point. Default: 50",
    )
    args = parser.parse_args()

    cal = load_calibration()
    try:
        for channel in args.channels:
            existing = cal.get(str(channel))
            cal[str(channel)] = calibrate_channel(channel, args.gain, args.samples, existing)
            save_calibration(cal)
    except (KeyboardInterrupt, SystemExit):
        print("\nCancelled.")
        sys.exit(0)
    except PhidgetException as exc:
        print("Phidget error (code {}): {}".format(exc.code, exc.details), file=sys.stderr)
        sys.exit(1)

    print("\nSaved calibration to {}".format(CAL_FILE))


if __name__ == "__main__":
    main()
