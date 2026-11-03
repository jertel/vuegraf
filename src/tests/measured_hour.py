# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

"""One hour measured from a live Emporia account, as test data.

9-10 PM on 23 Sep 2026 (America/New_York), 01:00 UTC. The account has plugs nested on
circuits and on a mains, subpanels fed from circuits (one reading more than its feed
circuit's CT), an offline predecessor still nested, a subpanel and a plug Emporia does
not nest, and a plug placed nowhere. Names and device ids are generic; the readings are
as measured, in kWh.

RAW_KWH is what getChartUsage (history) returned for each channel: every channel raw.
LIVE_KWH is what getDeviceListUsages (live, every device requested together) returned
where that differs from raw -- the circuits with a device nested beneath them -- plus
each panel's Balance, which only the live endpoint reports. Every other channel read the
same on both. The Emporia app's history view showed the raw values.
"""

import datetime

from tests.emporia import MAINS, SimulatedEmporia


HOUR = datetime.datetime(2026, 9, 24, 1, 0, tzinfo=datetime.timezone.utc)
TIMEZONE = 'America/New_York'

# gid, name, the (gid, channel) Emporia nests it under, and whether it was online.
DEVICES = [
    (1001, 'Panel A', None, True),
    (1002, 'Subpanel A1', (1001, '6'), True),
    (1003, 'Panel C', None, True),
    (1004, 'Plug On C Mains', (1003, MAINS), True),
    (1005, 'Plug On C15', (1003, '15'), True),
    (1006, 'Plug On C2', (1003, '2'), True),
    (1007, 'Plug Config C', None, True),
    (1008, 'Panel G', None, True),
    (1009, 'Subpanel G1', (1008, '2'), True),
    (1010, 'Subpanel G1 Old', (1008, '2'), False),
    (1011, 'Subpanel G2', None, True),
    (1013, 'Plug Unplaced', None, True),
]

RAW_KWH = {
    1001: {MAINS: 1.5360088, '1': 0.0182918, '2': 0.0605049, '3': 0.0224990, '4': 0.0223850, '5': 0.0075240,
           '6': 0.0, '7': 1.3999838, '8': 0.0, '9': 0.0, '10': 0.0},
    1002: {MAINS: 0.0360899, '1': 0.0009507, '2': 0.0293991, '3': 0.0, '4': 0.0085239, '5': 0.0, '6': 0.0, '7': 0.0,
           '8': 0.0, '9': 0.0, '10': 0.0},
    1003: {MAINS: 1.3044559, '1': 0.0, '2': 0.3508334, '3': 0.0, '4': 0.0, '5': 0.0, '6': 0.0000073, '7': 0.0,
           '8': 0.0746058, '9': 0.0340196, '10': 0.0822325, '11': 0.0, '12': 0.0, '13': 0.0, '14': 0.0743160,
           '15': 0.1130126, '16': 0.0},
    1004: {MAINS: 0.5468722},
    1005: {MAINS: 0.0801614},
    1006: {MAINS: 0.3309141},
    1007: {MAINS: 0.0011842},
    1008: {MAINS: 0.0742385, '1': 0.0000028, '2': 0.0312490, '3': 0.0072557, '4': 0.0001221, '5': 0.0056136,
           '6': 0.0001086, '7': 0.0046100, '8': 0.0, '9': 0.0, '10': 0.0, '11': 0.0, '12': 0.0, '13': 0.0,
           '14': 0.0008616, '15': 0.0, '16': 0.0178138},
    1009: {MAINS: 0.0179407, '1': 0.0, '2': 0.0, '3': 0.0, '4': 0.0, '5': 0.0},
    1010: {},  # offline: nothing on either endpoint
    1011: {MAINS: 0.0090358, '15': 0.0063833, '16': 0.0},
    1013: {MAINS: 0.2470452},
}

LIVE_KWH = {
    (1001, '6'): 0.0,  # 0.0 raw, less Subpanel A1's 0.0361, floored
    (1003, '2'): 0.0199193,
    (1003, '15'): 0.0328513,
    (1008, '2'): 0.0133084,
    (1001, 'Balance'): 0.0048203,
    (1002, 'Balance'): -0.0027838,
    (1003, 'Balance'): 0.0285563,
    (1008, 'Balance'): 0.0066013,
    (1009, 'Balance'): 0.0179407,
    (1011, 'Balance'): 0.0026525,
}


def simulator(historyGaps=()):
    """A SimulatedEmporia holding the measured hour."""
    devices = [{'gid': gid, 'name': name, 'parent': parent, 'connected': connected,
                'circuits': {num: '{} Circuit {}'.format(name, num) for num in RAW_KWH[gid] if num != MAINS}}
               for gid, name, parent, connected in DEVICES]
    readings = {(gid, chan): kwh for gid, chans in RAW_KWH.items() for chan, kwh in chans.items()}
    return SimulatedEmporia(devices, {HOUR: readings}, historyGaps=historyGaps)


def liveKwh(gid, chan):
    """What the live endpoint reported for a channel in the measured hour."""
    return LIVE_KWH.get((gid, chan), RAW_KWH[gid].get(chan))
