# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

"""Proves that SimulatedEmporia behaves like the real Emporia API.

The synthetic hierarchy tests are only as good as the simulator's rules. Here it is fed
the raw readings of an hour measured from a live account and must reproduce what the
live endpoint actually returned for that hour, channel by channel.
"""

import pytest

from tests import measured_hour as measured
from tests.emporia import MAINS


TOLERANCE_KWH = 2e-6  # the measured values are rounded to 0.1 Wh


def flatten(devices, out=None, parentKey=None):
    """{(gid, channel): (usage, (parentGid, parentChannel) or None)} for a usage response."""
    out = {} if out is None else out
    for device in devices:
        for chan in device['channelUsages']:
            out[(device['deviceGid'], chan['channelNum'])] = (chan['usage'], parentKey)
            flatten(chan.get('nestedDevices') or [], out, (device['deviceGid'], chan['channelNum']))
    return out


def simulatedLive():
    sim = measured.simulator()
    gids = [gid for gid, _, _, _ in measured.DEVICES]
    return flatten(sim.deviceListUsages(gids, measured.HOUR, '1H')['deviceListUsages']['devices'])


def test_simulator_reproduces_the_measured_live_hour():
    live = simulatedLive()
    for gid, name, parent, _ in measured.DEVICES:
        for chan in measured.RAW_KWH[gid]:
            assert live[(gid, chan)][0] == pytest.approx(measured.liveKwh(gid, chan), abs=TOLERANCE_KWH), (name, chan)
        # A nested device is reported inside the channel it hangs under.
        assert live[(gid, MAINS)][1] == parent, name
    for (gid, chan), kwh in measured.LIVE_KWH.items():
        assert live[(gid, chan)][0] == pytest.approx(kwh, abs=TOLERANCE_KWH), (gid, chan)
    assert live[(1010, MAINS)][0] is None  # the offline device is still listed, with no reading


def test_the_measurement_shows_each_deduction_rule():
    """The behaviours the hierarchy relies on, read straight from the measured values."""
    raw, live = measured.RAW_KWH, measured.liveKwh

    # A plug on a circuit: the live circuit is the raw circuit less the plug.
    assert live(1003, '15') == pytest.approx(raw[1003]['15'] - raw[1005][MAINS], abs=TOLERANCE_KWH)
    # Two devices on one circuit, one of them offline: only the reporting one is deducted.
    assert live(1008, '2') == pytest.approx(raw[1008]['2'] - raw[1009][MAINS], abs=TOLERANCE_KWH)
    # A subpanel reading more than its feed circuit: the circuit is floored at zero.
    assert raw[1001]['6'] < raw[1002][MAINS] and live(1001, '6') == 0.0
    # A plug on the mains: the mains is not deducted, the Balance is.
    assert live(1003, MAINS) == raw[1003][MAINS]
    circuits = sum(kwh for chan, kwh in raw[1003].items() if chan != MAINS)
    assert live(1003, 'Balance') == pytest.approx(raw[1003][MAINS] - circuits - raw[1004][MAINS], abs=TOLERANCE_KWH)
    # A Balance is not floored.
    assert live(1002, 'Balance') < 0
