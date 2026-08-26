import sys
import time
from datetime import timedelta

from HX711 import (
    SimpleHX711,
    AdvancedHX711,
    Mass,
    Options,
    ReadType,
    StrategyType,
    Rate,
    GpioException,
    TimeoutException,
)

DOUT_PIN = 5
PD_SCK_PIN = 6
REFERENCE_UNIT = 92
OFFSET = 0
SAMPLES = 5


def read_weight_simple():
    with SimpleHX711(DOUT_PIN, PD_SCK_PIN, REFERENCE_UNIT, OFFSET) as hx:
        hx.setUnit(Mass.Unit.G)
        hx.zero()
        while True:
            weight = hx.weight(SAMPLES)
            print(weight)
            time.sleep(0.5)


def read_weight_timed():
    with AdvancedHX711(DOUT_PIN, PD_SCK_PIN, REFERENCE_UNIT, OFFSET, Rate.HZ_80) as hx:
        hx.setUnit(Mass.Unit.G)
        hx.zero()
        while True:
            weight = hx.weight(timedelta(seconds=1))
            print(weight)
            time.sleep(0.5)


def read_raw_values():
    with SimpleHX711(DOUT_PIN, PD_SCK_PIN, REFERENCE_UNIT, OFFSET) as hx:
        values = hx.getValues(SAMPLES)
        return [int(v) for v in values]


def read_with_options():
    with SimpleHX711(DOUT_PIN, PD_SCK_PIN, REFERENCE_UNIT, OFFSET) as hx:
        hx.setUnit(Mass.Unit.G)
        hx.zero(Options(timedelta(seconds=1), ReadType.Average))
        opts = Options()
        opts.stratType = StrategyType.Samples
        opts.readType = ReadType.Median
        opts.samples = SAMPLES
        while True:
            print(hx.weight(opts))
            time.sleep(0.5)


def main():
    try:
        read_weight_simple()
    except (KeyboardInterrupt, SystemExit):
        print("Cleaning...")
        print("Bye!")
        sys.exit(0)
    except (GpioException, TimeoutException) as exc:
        print("Failed to connect to HX711 chip: {}".format(exc), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
