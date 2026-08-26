import json
import os
import time

from Phidget22.Devices.VoltageRatioInput import VoltageRatioInput
from Phidget22.BridgeGain import BridgeGain

CAL_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bridge_cal.json")

GAINS = {
    1: BridgeGain.BRIDGE_GAIN_1,
    2: BridgeGain.BRIDGE_GAIN_2,
    4: BridgeGain.BRIDGE_GAIN_4,
    8: BridgeGain.BRIDGE_GAIN_8,
    16: BridgeGain.BRIDGE_GAIN_16,
    32: BridgeGain.BRIDGE_GAIN_32,
    64: BridgeGain.BRIDGE_GAIN_64,
    128: BridgeGain.BRIDGE_GAIN_128,
}


def open_channel(channel, gain=128, interval_ms=None, timeout_ms=5000):
    ch = VoltageRatioInput()
    ch.setChannel(channel)
    ch.openWaitForAttachment(timeout_ms)
    if gain in GAINS:
        ch.setBridgeGain(GAINS[gain])
    min_interval = ch.getMinDataInterval()
    if interval_ms is None:
        interval_ms = min_interval
    interval_ms = max(interval_ms, min_interval)
    ch.setDataInterval(interval_ms)
    time.sleep(0.5)
    return ch, interval_ms


def average_ratio(ch, samples, interval_ms):
    step = max(interval_ms, 1) / 1000.0
    vals = []
    while len(vals) < samples:
        vals.append(ch.getVoltageRatio())
        time.sleep(step)
    return sum(vals) / len(vals)


def load_calibration():
    if os.path.exists(CAL_FILE):
        with open(CAL_FILE) as f:
            return json.load(f)
    return {}


def save_calibration(cal):
    with open(CAL_FILE, "w") as f:
        json.dump(cal, f, indent=2, sort_keys=True)


def apply_calibration(cal, channel, ratio):
    entry = cal.get(str(channel))
    if entry is None:
        return None
    return entry["scale"] * (ratio - entry["zero"])
