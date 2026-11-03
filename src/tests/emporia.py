# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

"""A stand-in for the Emporia cloud, for tests that need realistic API behaviour.

Tests drive a real PyEmVue whose transport is replaced here, so every response goes
through pyemvue's own parsing and then Vuegraf's real collection code.

SimulatedEmporia builds responses from raw per-channel readings using the rules measured
against a live account. test_emporia_simulator checks them against an hour measured
there (measured_hour):

  * getDeviceListUsages returns each requested device at the top level, unless it is
    nested under another requested device, in which case it appears only in that
    channel's nestedDevices. A circuit is reported net of the requested devices nested
    beneath it, floored at zero; the mains is never deducted; Balance is the mains minus
    the raw circuits minus the requested devices nested on the mains, unfloored. A
    channel with no data is left out.
  * getChartUsage returns every channel raw.
"""

import datetime
import json
from urllib.parse import parse_qs, urlparse

from pyemvue import PyEmVue


MAINS = '1,2,3'


def parseTime(text):
    return datetime.datetime.fromisoformat(text.replace('Z', '+00:00'))


def formatTime(moment):
    return moment.astimezone(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


class FakeResponse:
    def __init__(self, body, status=200):
        self.status_code = status
        self._body = body
        self.text = json.dumps(body) if body is not None else ''

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError('HTTP {}'.format(self.status_code))


class FakeAuth:
    """Routes PyEmVue's requests to a back end, and keeps a log of what was asked."""

    def __init__(self, backend):
        self.backend = backend
        self.log = []

    def request(self, method, path, **kwargs):
        self.log.append(path)
        if path == 'customers/devices':
            return FakeResponse(self.backend.devices())
        query = {k: v[0] for k, v in parse_qs(urlparse(path).query).items()}
        apiMethod = query.get('apiMethod')
        if apiMethod == 'getDeviceListUsages':
            gids = [int(g) for g in query['deviceGids'].replace(' ', '+').split('+')]
            return FakeResponse(self.backend.deviceListUsages(gids, parseTime(query['instant']), query['scale']))
        if apiMethod == 'getChartUsage':
            return FakeResponse(self.backend.chartUsage(int(query['deviceGid']), query['channel'],
                                                        parseTime(query['start']), parseTime(query['end']), query['scale']))
        raise AssertionError('Unexpected Emporia request: {}'.format(path))


def makeVue(backend):
    """A real PyEmVue talking to the given back end instead of the Emporia cloud."""
    vue = PyEmVue()
    vue.auth = FakeAuth(backend)
    return vue


class SimulatedEmporia:
    """Synthesizes Emporia responses from raw readings, per the rules in the module notes.

    devices: list of dicts
        {'gid': int, 'name': str, 'parent': (gid, channelNum) or None,
         'circuits': {channelNum: name}, 'model': str (optional), 'connected': bool (default True)}
      A parent channel of '1,2,3' nests the device on that panel's mains.
    readings: {periodStartUTC: {(gid, channelNum): kWh}} -- raw, as a CT measures it.
      A missing key means the channel reported nothing for that period.
    scale: the resolution the readings are at ('1H' or '1D'). Requests at another
      resolution get no data, except the minute request history collection makes only
      to learn the channels, which is answered from the latest period.
    historyGaps: (gid, channelNum) pairs the history endpoint returns nothing for, though
      the live one does -- which Emporia was seen to do for a whole day for one mains.
    """

    SCALE_SECONDS = {'1H': 3600, '1D': 86400, '1MIN': 60}

    def __init__(self, devices, readings, scale='1H', historyGaps=()):
        self.devs = {d['gid']: d for d in devices}
        self.readings = readings
        self.scale = scale
        self.historyGaps = set(historyGaps)

    def devices(self):
        out = []
        for d in self.devs.values():
            parentGid, parentChan = d.get('parent') or (None, None)
            entry = {'deviceGid': d['gid'], 'model': d.get('model', 'VUE002' if d.get('circuits') else 'SSO001'),
                     'firmware': 'test', 'manufacturerDeviceId': 'test-{}'.format(d['gid']),
                     'parentDeviceGid': parentGid, 'parentChannelNum': parentChan,
                     'deviceConnected': {'deviceGid': d['gid'], 'connected': d.get('connected', True)},
                     'locationProperties': {'deviceGid': d['gid'], 'deviceName': d['name'], 'displayName': d['name']},
                     'channels': [{'deviceGid': d['gid'], 'name': None, 'channelNum': MAINS, 'channelMultiplier': 1.0,
                                   'channelTypeGid': 1}]}
            if d.get('circuits'):
                entry['devices'] = [{'deviceGid': d['gid'], 'model': 'WAT001', 'channels': [
                    {'deviceGid': d['gid'], 'name': name, 'channelNum': num, 'channelMultiplier': 1.0, 'channelTypeGid': 1}
                    for num, name in d['circuits'].items()]}]
            out.append(entry)
        return {'devices': out}

    def _period(self, moment, scale):
        """The period of readings that answers a request at this moment and scale."""
        if scale == '1MIN':
            return max(self.readings, default=None)
        if scale != self.scale:
            return None
        for start in self.readings:
            if start <= moment < start + datetime.timedelta(seconds=self.SCALE_SECONDS[scale]):
                return start
        return None

    def _raw(self, period, gid, chan):
        return self.readings.get(period, {}).get((gid, chan)) if period is not None else None

    def _nestedUnder(self, gids, gid, chan):
        return [g for g in gids if self.devs[g].get('parent') == (gid, chan)]

    def _usageEntry(self, gids, period, gid):
        d = self.devs[gid]

        def mainsOf(g):
            return self._raw(period, g, MAINS) or 0.0

        def channel(num, usage, name=None):
            nested = self._nestedUnder(gids, gid, num)
            return {'deviceGid': gid, 'channelNum': num, 'name': name, 'usage': usage, 'percentage': 0.0,
                    'nestedDevices': [self._usageEntry(gids, period, g) for g in nested]}

        mains = self._raw(period, gid, MAINS)
        chans = [channel(MAINS, mains, d['name'])]
        rawCircuits = 0.0
        for num, name in (d.get('circuits') or {}).items():
            raw = self._raw(period, gid, num)
            if raw is None:
                continue  # a channel with no data is left out entirely, as Emporia does
            rawCircuits += raw
            chans.append(channel(num, max(raw - sum(mainsOf(g) for g in self._nestedUnder(gids, gid, num)), 0.0), name))
        if d.get('circuits') and mains is not None:
            balance = mains - rawCircuits - sum(mainsOf(g) for g in self._nestedUnder(gids, gid, MAINS))
            chans.append(channel('Balance', balance, 'Balance'))
        return {'deviceGid': gid, 'channelUsages': chans}

    def deviceListUsages(self, gids, instant, scale):
        period = self._period(instant, scale)
        requested = [g for g in gids if g in self.devs]
        top = [g for g in requested if not (self.devs[g].get('parent') and self.devs[g]['parent'][0] in requested)]
        return {'deviceListUsages': {'instant': formatTime(instant), 'scale': scale, 'energyUnit': 'KilowattHours',
                                     'devices': [self._usageEntry(requested, period, g) for g in top]}}

    def chartUsage(self, gid, channel, start, end, scale):
        step = datetime.timedelta(seconds=self.SCALE_SECONDS[scale])
        usage = []
        t = start
        while t < end:
            usage.append(None if (gid, channel) in self.historyGaps else self._raw(self._period(t, scale), gid, channel))
            t += step
        return {'firstUsageInstant': formatTime(start), 'usageList': usage}
