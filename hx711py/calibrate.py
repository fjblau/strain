import argparse
import sys

from HX711 import (
    SimpleHX711,
    Options,
    GpioException,
    TimeoutException,
)


def prompt_float(message):
    while True:
        try:
            return float(input(message).strip())
        except ValueError:
            print("  Please enter a number.")


def run(data_pin, clock_pin, samples):
    print("=" * 44)
    print("HX711 Calibration")
    print("=" * 44)
    print()
    print("You need an object whose exact weight you know")
    print("(a calibration weight, or e.g. your phone's spec weight).")
    print()

    unit = input("1. Unit you are measuring in (label only, e.g. g, kg, lb): ").strip() or "g"
    known_weight = prompt_float(
        "2. Weight of your reference object in {} (e.g. 100): ".format(unit)
    )

    input("\n3. Remove ALL weight from the load cell, then press Enter...")
    print("   Working...", flush=True)
    zero_value = float(hx.read(Options(samples)))

    input("\n4. Place the {}{} object on the load cell, then press Enter...".format(known_weight, unit))
    print("   Working...", flush=True)
    raw = float(hx.read(Options(samples)))

    reference_unit = round((raw - zero_value) / known_weight)
    if reference_unit == 0:
        reference_unit = 1

    print()
    print("-" * 44)
    print("Reference object : {}{}".format(known_weight, unit))
    print("Raw @ zero       : {}".format(int(zero_value)))
    print("Raw @ load       : {}".format(int(raw)))
    print("-" * 44)
    print("REFERENCE_UNIT = {}".format(reference_unit))
    print("OFFSET         = {}".format(int(zero_value)))
    print("-" * 44)
    print()
    print("Put these into example.py:")
    print("    REFERENCE_UNIT = {}".format(reference_unit))
    print("    OFFSET         = {}".format(int(zero_value)))
    print()
    print("Or set them at runtime:")
    print("    hx.setReferenceUnit({})".format(reference_unit))
    print("    hx.setOffset({})".format(int(zero_value)))


def main():
    parser = argparse.ArgumentParser(
        description="Interactively calibrate an HX711 load cell / strain gauge."
    )
    parser.add_argument("--data", type=int, default=5, help="DOUT GPIO pin (BCM). Default: 5")
    parser.add_argument("--clock", type=int, default=6, help="PD_SCK GPIO pin (BCM). Default: 6")
    parser.add_argument("--samples", type=int, default=15, help="Samples per reading. Default: 15")
    args = parser.parse_args()

    global hx
    try:
        with SimpleHX711(args.data, args.clock, 1, 0) as hx:
            run(args.data, args.clock, args.samples)
    except (KeyboardInterrupt, SystemExit):
        print("\nCancelled.")
        sys.exit(0)
    except (GpioException, TimeoutException) as exc:
        print("Failed to connect to HX711 chip: {}".format(exc), file=sys.stderr)
        print(
            "Check wiring: DOUT->GPIO{}, PD_SCK->GPIO{}, and 3.3V/GND.".format(
                args.data, args.clock
            ),
            file=sys.stderr,
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
