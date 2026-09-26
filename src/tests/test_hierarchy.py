# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

"""The device hierarchy and its Net Balance series, end to end.

Collection runs for real -- pyemvue's parsing, Vuegraf's extraction, the derivation --
against SimulatedEmporia, whose rules test_emporia_simulator checks against an hour
measured from a live account. That hour (measured_hour) is also used directly here, as
the realistic case; the rest are synthetic trees. Readings are kWh from Emporia and
watts in the points, so an hourly value of 0.0328 kWh is a 32.8 W point.
"""

import datetime
import logging
from unittest.mock import MagicMock, patch

import pytest
from pyemvue.enums import Scale

from tests import measured_hour as measured
from tests.emporia import MAINS, SimulatedEmporia, makeVue
from vuegraf.collect import collectHistoryUsage, collectUsage
from vuegraf.device import populateDevices
from vuegraf.hierarchy import (CIRCUIT, DEVICE, NET_BALANCE_SUFFIX, VIRTUAL, buildHierarchy, computeNetBalances,
                               derivedSeriesKey)


UTC = datetime.timezone.utc
TOLERANCE_W = 0.01  # the measured values are rounded to 0.1 Wh, and a balance sums a panel's worth of them

MEASURED_TZ = measured.TIMEZONE
MEASURED_HOUR = measured.HOUR

# The measured account's own config, anonymized. Emporia nests Subpanel A1 under A Feed,
# Subpanel G1 (and an offline predecessor) under G Feed, and three plugs under Panel C:
# two on circuits and one on the mains. Restating those is allowed. Plug Config C is a
# link only the config knows about.
MEASURED_DEVICES = [
    {'name': 'Panel A', 'channels': {'6': 'A Feed'}},
    {'name': 'Subpanel A1', 'parent': 'A Feed'},
    {'name': 'Panel C', 'channels': {'2': 'C Plug Circuit 2', '15': 'C Plug Circuit 15'}},
    {'name': 'Plug On C15', 'parent': 'C Plug Circuit 15'},
    {'name': 'Plug On C2', 'parent': 'C Plug Circuit 2'},
    {'name': 'Plug On C Mains', 'parent': 'Panel C'},
    {'name': 'Plug Config C', 'parent': 'Panel C'},
    {'name': 'Panel G', 'channels': {'2': 'G Feed'}},
    {'name': 'Subpanel G1', 'parent': 'G Feed'},
]


@pytest.fixture(autouse=True)
def noApiRetryDelay():
    """pyemvue retries a usage request that has any empty channel, sleeping between tries."""
    with patch('pyemvue.pyemvue.time.sleep'):
        yield


@pytest.fixture
def measuredSim():
    return measured.simulator()


def makeConfig(timezone='UTC', **sections):
    config = {'influxDb': {'version': 2}, 'detailedDataEnabled': False, 'detailedDataSecondsEnabled': False,
              'timezone': timezone, 'hierarchyBalanceEpsilonWatts': 5.0, 'hierarchyNegativeBalanceAbort': False}
    config.update(sections)
    return config


def makeAccount(backend, devices=(), name='Home', **settings):
    """An account as initDeviceAccount leaves it, talking to a fake Emporia."""
    account = {'name': name, 'email': 'unused', 'password': 'unused', 'devices': [dict(d) for d in devices]}
    account.update(settings)
    account['vue'] = makeVue(backend)
    populateDevices(account)
    account['hierarchy'] = buildHierarchy(account)
    return account


def collectHour(config, account, hourStartUTC):
    points = []
    collectUsage(config, account, hourStartUTC, hourStartUTC, False, points, None, Scale.HOUR.value)
    return points


def collectDay(config, account, dayInstantUTC):
    points = []
    collectUsage(config, account, dayInstantUTC, dayInstantUTC, False, points, None, Scale.DAY.value)
    return points


def collectHistory(config, account, startUTC, stopUTC):
    pause = MagicMock()
    pause.wait.return_value = False
    points = []
    collectHistoryUsage(config, account, startUTC, stopUTC, points, pause)
    return points


def collectMeasuredHistory(config, account):
    return collectHistory(config, account, MEASURED_HOUR, MEASURED_HOUR + datetime.timedelta(hours=1))


def isDerived(point, account):
    hierarchy = account.get('hierarchy')
    if hierarchy is None:
        return point.chanName.endswith(NET_BALANCE_SUFFIX)
    keys = {derivedSeriesKey(node) for node in hierarchy.nodes.values()} - {None}
    return (point.deviceName, point.chanName) in keys


def netBalances(points, detailed=None, timestamp=None):
    """{chanName: watts} of the Net Balance points, optionally for one resolution/period."""
    return {p.chanName[:-len(NET_BALANCE_SUFFIX)]: p.usageWatts for p in points
            if p.chanName.endswith(NET_BALANCE_SUFFIX) and (detailed is None or p.detailed == detailed)
            and (timestamp is None or p.timestamp == timestamp)}


def readingsOf(points, detailed=None, timestamp=None):
    return {(p.deviceName, p.chanName): p.usageWatts for p in points
            if (detailed is None or p.detailed == detailed) and (timestamp is None or p.timestamp == timestamp)}


def subtreeTotal(account, points, rootName, detailed, timestamp):
    """Sum of the Net Balances and leaf readings under a node, which should be its total."""
    hierarchy = account['hierarchy']
    readings = readingsOf(points, detailed, timestamp)
    byName = {node.name: nodeId for nodeId, node in hierarchy.nodes.items() if node.kind != CIRCUIT}

    def walk(nodeId):
        node = hierarchy.nodes[nodeId]
        key = derivedSeriesKey(node)
        if node.kind == VIRTUAL:
            own = 0.0  # its derived series is a total, not a remainder
        elif key is not None:
            own = readings.get(key, 0.0)
        else:
            own = readings.get(node.readingKey, 0.0)
        return own + sum(walk(childId) for childId in node.circuits + node.children)

    return walk(byName[rootName])


def liveW(gid, chan):
    return measured.liveKwh(gid, chan) * 1000


# ---------------------------------------------------------------------------
# Opt-out: an account that does not opt in is collected exactly as before
# ---------------------------------------------------------------------------

def test_an_account_that_does_not_opt_in_has_no_hierarchy(measuredSim):
    devices = [{'name': d['name'], 'channels': d['channels']} for d in MEASURED_DEVICES if 'channels' in d]
    account = makeAccount(measuredSim, devices)
    assert account['hierarchy'] is None


def test_opting_out_adds_nothing_live_or_in_history(measuredSim):
    config = makeConfig(MEASURED_TZ)
    account = makeAccount(measuredSim)
    for points in (collectHour(config, account, MEASURED_HOUR), collectMeasuredHistory(config, account)):
        assert points
        assert not [p for p in points if p.chanName.endswith(NET_BALANCE_SUFFIX)]


def test_opting_out_writes_exactly_what_emporia_reported(measuredSim):
    """The regression reported against the first implementation: a circuit with a plug
    nested beneath it must keep Emporia's reading, not have the plug added back."""
    readings = readingsOf(collectHour(makeConfig(MEASURED_TZ), makeAccount(measuredSim), MEASURED_HOUR))
    assert readings[('Panel C', 'Panel C-15')] == pytest.approx(liveW(1003, '15'), abs=TOLERANCE_W)
    assert readings[('Panel C', 'Panel C')] == pytest.approx(liveW(1003, MAINS), abs=TOLERANCE_W)
    assert readings[('Panel C', 'Panel C-Balance')] == pytest.approx(liveW(1003, 'Balance'), abs=TOLERANCE_W)
    assert readings[('Panel G', 'Panel G-2')] == pytest.approx(liveW(1008, '2'), abs=TOLERANCE_W)
    assert readings[('Panel A', 'Panel A-6')] == 0.0


def test_opting_in_never_changes_a_raw_reading():
    config = makeConfig(MEASURED_TZ)
    plainDevices = [{'name': d['name'], 'channels': d['channels']} for d in MEASURED_DEVICES if 'channels' in d]
    optOut = makeAccount(measured.simulator(), plainDevices)
    optIn = makeAccount(measured.simulator(), MEASURED_DEVICES)
    for collect in (lambda a: collectHour(config, a, MEASURED_HOUR), lambda a: collectMeasuredHistory(config, a)):
        before = collect(optOut)
        after = collect(optIn)
        derived = [p for p in after if isDerived(p, optIn)]
        assert derived
        assert [p for p in after if not isDerived(p, optIn)] == before
        assert after[len(before):] == derived  # appended, after every raw point of the batch


def test_every_device_is_requested_together(measuredSim):
    """Emporia deducts a nested device only when it is requested alongside its parent."""
    account = makeAccount(measuredSim, MEASURED_DEVICES)
    collectHour(makeConfig(MEASURED_TZ), account, MEASURED_HOUR)
    usageRequests = [path for path in account['vue'].auth.log if 'getDeviceListUsages' in path]
    assert len(usageRequests) >= 1
    for path in usageRequests:
        gids = sorted(int(g) for g in path.split('deviceGids=')[1].split('&')[0].split('+'))
        assert gids == sorted(gid for gid, _, _, _ in measured.DEVICES)


def test_only_the_account_that_opts_in_gets_net_balances():
    config = makeConfig(MEASURED_TZ)
    optIn = makeAccount(measured.simulator(), MEASURED_DEVICES, name='Opted In')
    optOut = makeAccount(measured.simulator(), name='Opted Out')
    points = []
    for account in (optIn, optOut):
        collectUsage(config, account, MEASURED_HOUR, MEASURED_HOUR, False, points, None, Scale.HOUR.value)
    assert {p.accountName for p in points if p.chanName.endswith(NET_BALANCE_SUFFIX)} == {'Opted In'}


@pytest.mark.parametrize('settings, devices, expected', [
    ({}, [], False),
    ({'hierarchyEnabled': False}, [], False),
    ({'hierarchyEnabled': 'true'}, [], False),  # only a real boolean true opts in
    ({'hierarchyEnabled': True}, [], True),
    ({}, [{'name': 'Plug Config C', 'parent': 'Panel C'}], True),
    ({}, [{'name': 'Service', 'virtual': True}], True),
    ({}, [{'name': 'Panel C', 'channels': {'2': 'C Plug Circuit 2'}}], False),
])
def test_what_opts_an_account_in(measuredSim, settings, devices, expected):
    assert (makeAccount(measuredSim, devices, **settings)['hierarchy'] is not None) is expected


# ---------------------------------------------------------------------------
# Reality: the measured hour, checked against what Emporia and the app report
# ---------------------------------------------------------------------------

@pytest.fixture
def measuredHour(measuredSim):
    account = makeAccount(measuredSim, MEASURED_DEVICES)
    points = collectHour(makeConfig(MEASURED_TZ), account, MEASURED_HOUR)
    return account, points, netBalances(points)


# Read from the Emporia app's history view for the measured hour, in kWh to three
# decimals, keyed by the anonymized series names. The history view shows circuits raw, as
# --historydays records them.
APP_HISTORY_KWH = {
    ('Panel C', 'Panel C'): 1.304,
    ('Panel C', 'C Plug Circuit 15'): 0.113,
    ('Plug On C15', 'Plug On C15'): 0.080,
    ('Panel C', 'C Plug Circuit 2'): 0.351,
    ('Plug On C2', 'Plug On C2'): 0.331,
    ('Plug On C Mains', 'Plug On C Mains'): 0.547,
    ('Plug Config C', 'Plug Config C'): 0.001,
    ('Panel G', 'Panel G'): 0.074,
    ('Panel G', 'G Feed'): 0.031,
    ('Subpanel G1', 'Subpanel G1'): 0.018,
    ('Panel A', 'Panel A'): 1.536,
    ('Panel A', 'A Feed'): 0.000,
    ('Subpanel A1', 'Subpanel A1'): 0.036,
}


@pytest.fixture
def historyHour(measuredSim):
    points = collectMeasuredHistory(makeConfig(MEASURED_TZ), makeAccount(measuredSim, MEASURED_DEVICES))
    return readingsOf(points, 'Hour', MEASURED_HOUR), netBalances(points, 'Hour', MEASURED_HOUR)


def test_history_readings_match_the_apps_history_view(historyHour):
    readings, _ = historyHour
    for key, kwh in APP_HISTORY_KWH.items():
        assert readings[key] == pytest.approx(kwh * 1000, abs=0.5), key  # the app rounds to 1 Wh


def test_net_balances_follow_from_the_apps_history_view(historyHour):
    """Each circuit's Net Balance is its raw reading, as the app shows it, less what the
    app nests beneath it; a feed circuit that read nothing stays at zero."""
    _, balances = historyHour
    app = {key[1]: kwh * 1000 for key, kwh in APP_HISTORY_KWH.items()}
    rounding = 1.0  # two values, each rounded to 1 Wh by the app
    assert balances['C Plug Circuit 15'] == pytest.approx(app['C Plug Circuit 15'] - app['Plug On C15'], abs=rounding)
    assert balances['C Plug Circuit 2'] == pytest.approx(app['C Plug Circuit 2'] - app['Plug On C2'], abs=rounding)
    assert balances['G Feed'] == pytest.approx(app['G Feed'] - app['Subpanel G1'], abs=rounding)
    assert balances['A Feed'] == 0.0


def test_a_plug_on_a_circuit_leaves_what_the_plug_does_not_account_for(measuredHour):
    _, _, balances = measuredHour
    # Emporia's live circuit reading already excludes the plug: 113.0 W raw, less 80.2 W.
    assert balances['C Plug Circuit 15'] == pytest.approx(liveW(1003, '15'), abs=TOLERANCE_W)
    assert balances['C Plug Circuit 15'] == pytest.approx(32.851, abs=TOLERANCE_W)
    assert balances['C Plug Circuit 2'] == pytest.approx(19.919, abs=TOLERANCE_W)


def test_a_plug_on_the_mains_is_taken_out_of_the_panel_balance(measuredHour):
    _, _, balances = measuredHour
    # Emporia's Balance already excludes the plug on the mains; the config-only plug
    # (1.18 W) is taken out here.
    assert balances['Panel C'] == pytest.approx(liveW(1003, 'Balance') - liveW(1007, MAINS), abs=TOLERANCE_W)
    assert balances['Panel C'] == pytest.approx(27.372, abs=TOLERANCE_W)


def test_a_subpanel_on_a_circuit_and_its_offline_predecessor(measuredHour):
    _, _, balances = measuredHour
    # Two devices hang under G Feed; the old one is offline and contributes nothing.
    assert balances['G Feed'] == pytest.approx(liveW(1008, '2'), abs=TOLERANCE_W)
    assert balances['G Feed'] == pytest.approx(13.308, abs=TOLERANCE_W)
    assert balances['Panel G'] == pytest.approx(liveW(1008, 'Balance'), abs=TOLERANCE_W)
    assert 'Subpanel G1 Old' not in balances


def test_a_subpanel_that_reads_more_than_its_feed_circuit(measuredHour):
    """Measured: A Feed reads 0 W while Subpanel A1 draws 36 W. Emporia floors the circuit
    at zero and leaves its panel's Balance alone; so does the Net Balance."""
    _, _, balances = measuredHour
    assert balances['A Feed'] == 0.0
    assert balances['Panel A'] == pytest.approx(liveW(1001, 'Balance'), abs=TOLERANCE_W)
    # A subpanel's own Balance can be negative in Emporia, and is passed through as-is.
    assert balances['Subpanel A1'] == pytest.approx(liveW(1002, 'Balance'), abs=TOLERANCE_W)
    assert balances['Subpanel A1'] < 0


def test_a_feed_circuit_reading_less_than_its_subpanel_is_reported_live_and_in_history(measuredSim, caplog):
    """Emporia floors A Feed at zero in live data, hiding the 36 W the subpanel draws beyond
    it; the hidden amount shows up as the gap between Panel A's Balance and the Balance
    rebuilt from its circuits. History has the raw feed reading and measures it directly."""
    config = makeConfig(MEASURED_TZ)
    account = makeAccount(measuredSim, MEASURED_DEVICES)
    shortfall = liveW(1002, MAINS)  # the feed itself read 0
    with caplog.at_level(logging.WARNING, logger='vuegraf.hierarchy'):
        collectHour(config, account, MEASURED_HOUR)
        live = [r.getMessage() for r in caplog.records]
        caplog.clear()
        account['hierarchy'].warnedUntil.clear()
        collectMeasuredHistory(config, account)
        history = [r.getMessage() for r in caplog.records]
    assert live == ['Net Balance: "Panel A" has circuits reading less than the devices nested beneath them (A Feed) by '
                    '{:.1f}; totals built from this tree will not add up'.format(shortfall)]
    assert history == ['Net Balance: "A Feed" reads less than the devices beneath it by {:.1f}; totals built from this '
                       'tree will not add up'.format(shortfall)]


def test_leaves_and_unplaced_devices_get_no_net_balance(measuredHour):
    _, _, balances = measuredHour
    for leaf in ('Plug On C15', 'Plug On C2', 'Plug On C Mains', 'Plug Config C', 'Plug Unplaced', 'Panel C-1'):
        assert leaf not in balances


@pytest.mark.parametrize('root', ['Panel C', 'Panel G'])
def test_a_panels_net_balances_and_leaves_add_up_to_its_mains(measuredHour, root):
    account, points, _ = measuredHour
    mains = readingsOf(points)[(root, root)]
    assert subtreeTotal(account, points, root, 'Hour', MEASURED_HOUR) == pytest.approx(mains, abs=TOLERANCE_W)


def test_a_floored_circuit_is_the_one_place_the_sum_exceeds_the_mains(measuredHour):
    """The subpanel's 36 W is counted once below A Feed, which itself measured nothing, so
    Panel A's tree sums to its mains plus exactly the part the floor hid."""
    account, points, _ = measuredHour
    readings = readingsOf(points)
    excess = readings[('Subpanel A1', 'Subpanel A1')] - readings[('Panel A', 'A Feed')]
    total = subtreeTotal(account, points, 'Panel A', 'Hour', MEASURED_HOUR)
    assert total == pytest.approx(readings[('Panel A', 'Panel A')] + excess, abs=TOLERANCE_W)


def test_live_and_history_agree_for_every_node(historyHour, measuredHour):
    """Collected live or through --historydays, the measured hour gives the same Net
    Balances, although the two endpoints report nested circuits differently."""
    _, _, liveBalances = measuredHour
    _, historyBalances = historyHour
    assert set(liveBalances) == set(historyBalances)
    for name, watts in historyBalances.items():
        assert liveBalances[name] == pytest.approx(watts, abs=TOLERANCE_W), name


def test_a_mains_missing_from_history_skips_only_that_panel():
    """Emporia was seen to return no history at all for one panel's mains for a whole day,
    though the live endpoint had it. That panel has no Net Balance in history; the rest do."""
    config = makeConfig(MEASURED_TZ)
    account = makeAccount(measured.simulator(historyGaps={(1003, MAINS)}), MEASURED_DEVICES)
    history = netBalances(collectMeasuredHistory(config, account), 'Hour', MEASURED_HOUR)
    assert 'Panel C' not in history and 'Panel G' in history and 'C Plug Circuit 15' in history
    assert 'Panel C' in netBalances(collectHour(config, account, MEASURED_HOUR))


def test_an_offline_panel_gets_no_net_balance():
    sim = SimulatedEmporia([{'gid': 1, 'name': 'Panel Offline', 'parent': None, 'circuits': {'1': 'Loads'},
                             'connected': False}], {MEASURED_HOUR: {}})
    account = makeAccount(sim, hierarchyEnabled=True)
    assert netBalances(collectHour(makeConfig(), account, MEASURED_HOUR)) == {}


def test_a_subpanel_placed_on_the_wrong_mains_is_reported(measuredSim, caplog):
    """Measured: Subpanel G2 draws more than Panel G's whole unmetered remainder, so it
    cannot be fed from Panel G's mains. Placing it there by config gives a negative Net
    Balance -- 2.4 W here, reported once past a 1 W tolerance however often it is seen."""
    devices = MEASURED_DEVICES + [{'name': 'Subpanel G2', 'parent': 'Panel G'}]
    account = makeAccount(measuredSim, devices)
    config = makeConfig(MEASURED_TZ, hierarchyBalanceEpsilonWatts=1.0)
    with caplog.at_level(logging.WARNING, logger='vuegraf.hierarchy'):
        balances = netBalances(collectHour(config, account, MEASURED_HOUR))
        collectHour(config, account, MEASURED_HOUR)
    expected = liveW(1008, 'Balance') - liveW(1011, MAINS)
    assert balances['Panel G'] == pytest.approx(expected, abs=TOLERANCE_W) and expected < -1.0
    warnings = [r.getMessage() for r in caplog.records if '"Panel G"' in r.getMessage()]
    assert warnings == ['Net Balance: "Panel G" reads less than its circuits and the devices on its mains by {:.1f}; check '
                        'that Subpanel G2 is fed from the mains, not from one of its circuits; totals built from this tree '
                        'will not add up'.format(-expected)]


# ---------------------------------------------------------------------------
# Synthetic trees, through the simulator
# ---------------------------------------------------------------------------

T0 = datetime.datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
DAY0 = datetime.datetime(2026, 1, 15, 0, 0, tzinfo=UTC)


def panel(gid, name, circuits, parent=None):
    return {'gid': gid, 'name': name, 'circuits': circuits, 'parent': parent}


def plug(gid, name, parent=None):
    return {'gid': gid, 'name': name, 'circuits': {}, 'parent': parent}


def simulated(devices, raw, scale='1H', at=T0):
    return SimulatedEmporia(devices, {at: raw}, scale)


def simAccount(sim, extra=(), name='Home', **settings):
    """An account whose config names every simulated circuit, plus any extra entries."""
    entries = {d['name']: {'name': d['name'], 'channels': dict(d['circuits'])} for d in sim.devs.values() if d['circuits']}
    separate = []
    for entry in extra:
        if entry.get('virtual') or 'name' not in entry:
            separate.append(entry)  # kept as its own entry, as a user would write it
        else:
            entries.setdefault(entry['name'], {}).update(entry)
    return makeAccount(sim, list(entries.values()) + separate, name=name, **settings)


def liveAndHistory(sim, account, at=T0):
    """Net Balances of one hour, collected live and through history."""
    config = makeConfig()
    live = collectHour(config, account, at)
    history = collectHistory(config, account, at, at + datetime.timedelta(hours=1))
    return live, netBalances(live), netBalances(history, 'Hour', at)


# One panel, like the report against the first implementation: a room circuit with one
# plug and a bedroom circuit with six, all nested in the Emporia app.
CLARA = [panel(1, 'House', {'1': 'Room', '2': 'Bedrooms', '3': 'Kitchen'}), plug(2, 'Desk', (1, '1'))] + \
        [plug(10 + n, 'Bed {}'.format(n), (1, '2')) for n in range(1, 7)]
CLARA_RAW = {(1, MAINS): 0.700, (1, '1'): 0.02445, (1, '2'): 0.500, (1, '3'): 0.100, (2, MAINS): 0.0043,
             **{(10 + n, MAINS): 0.045 for n in range(1, 7)}}


def test_opted_out_flat_series_already_add_up_to_the_mains():
    """What the reporter observed: circuits and plugs sum to the mains exactly, because
    Emporia already deducts the plugs from the circuits they are nested under."""
    sim = simulated(CLARA, CLARA_RAW)
    account = simAccount(sim)
    readings = readingsOf(collectHour(makeConfig(), account, T0))
    assert readings[('House', 'Room')] == pytest.approx(20.15)
    assert readings[('House', 'Bedrooms')] == pytest.approx(230.0)
    circuits = sum(readings[('House', c)] for c in ('Room', 'Bedrooms', 'Kitchen'))
    plugs = readings[('Desk', 'Desk')] + sum(readings[('Bed {}'.format(n), 'Bed {}'.format(n))] for n in range(1, 7))
    assert circuits + plugs + readings[('House', 'House-Balance')] == pytest.approx(readings[('House', 'House')])


def test_plugs_nested_in_the_app_need_no_config():
    sim = simulated(CLARA, CLARA_RAW)
    account = simAccount(sim, hierarchyEnabled=True)
    live, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert liveBalances == pytest.approx({'Room': 20.15, 'Bedrooms': 230.0, 'House': 75.55})
    assert historyBalances == pytest.approx(liveBalances)
    assert subtreeTotal(account, live, 'House', 'Hour', T0) == pytest.approx(700.0)


# Main -> Barn Feed -> Barn -> Workshop Feed -> Workshop -> Bench -> Heater (a plug).
CASCADE_RAW = {(10, MAINS): 0.90, (10, '1'): 0.56, (10, '2'): 0.20,
               (11, MAINS): 0.55, (11, '1'): 0.42, (11, '2'): 0.10,
               (12, MAINS): 0.40, (12, '1'): 0.35, (13, MAINS): 0.30}
CASCADE_BALANCES = {'Main': 140.0, 'Barn Feed': 10.0, 'Barn': 30.0, 'Workshop Feed': 20.0, 'Workshop': 50.0, 'Bench': 50.0}


def cascade(workshopParent=(11, '1')):
    return [panel(10, 'Main', {'1': 'Barn Feed', '2': 'Lights'}),
            panel(11, 'Barn', {'1': 'Workshop Feed', '2': 'Barn Lights'}, (10, '1')),
            panel(12, 'Workshop', {'1': 'Bench'}, workshopParent),
            plug(13, 'Heater', (12, '1'))]


def test_nested_panels_cascade():
    sim = simulated(cascade(), CASCADE_RAW)
    account = simAccount(sim, hierarchyEnabled=True)
    live, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert liveBalances == pytest.approx(CASCADE_BALANCES)
    assert historyBalances == pytest.approx(CASCADE_BALANCES)
    assert subtreeTotal(account, live, 'Main', 'Hour', T0) == pytest.approx(900.0)
    assert subtreeTotal(account, live, 'Barn', 'Hour', T0) == pytest.approx(550.0)


def test_a_cascade_with_a_link_only_the_config_knows():
    """The Workshop is not nested in the app, so Emporia reports Workshop Feed raw; the
    config supplies the link and the balances come out the same."""
    sim = simulated(cascade(workshopParent=None), CASCADE_RAW)
    account = simAccount(sim, [{'name': 'Workshop', 'parent': 'Workshop Feed'}])
    live, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert readingsOf(live)[('Barn', 'Workshop Feed')] == pytest.approx(420.0)  # raw: Emporia deducted nothing
    assert liveBalances == pytest.approx(CASCADE_BALANCES)
    assert historyBalances == pytest.approx(CASCADE_BALANCES)
    assert subtreeTotal(account, live, 'Main', 'Hour', T0) == pytest.approx(900.0)


SPLIT = [panel(20, 'Panel L1', {'1': 'L1 Loads'}), panel(21, 'Panel L2', {'1': 'L2 Loads'}), plug(22, 'Roaming'),
         panel(23, 'Barn Panel', {'1': 'Barn Loads'})]
SPLIT_RAW = {(20, MAINS): 0.5, (20, '1'): 0.3, (21, MAINS): 0.4, (21, '1'): 0.4, (22, MAINS): 0.05,
             (23, MAINS): 0.25, (23, '1'): 0.2}
SERVICE = [{'name': 'Service', 'virtual': True}, {'name': 'Panel L1', 'parent': 'Service'},
           {'name': 'Panel L2', 'parent': 'Service'}]


def test_a_virtual_top_level_for_a_split_feed_service():
    sim = simulated(SPLIT, SPLIT_RAW)
    account = simAccount(sim, SERVICE)
    live, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert readingsOf(live)[('Service', 'Service')] == pytest.approx(900.0)  # an unplaced plug is not in it
    assert liveBalances == pytest.approx({'Panel L1': 200.0, 'Panel L2': 0.0, 'Barn Panel': 50.0})
    assert historyBalances == pytest.approx(liveBalances)
    assert subtreeTotal(account, live, 'Service', 'Hour', T0) == pytest.approx(900.0)


def test_virtual_nodes_can_nest():
    sim = simulated(SPLIT, SPLIT_RAW)
    service = [dict(SERVICE[0], parent='Campus')] + SERVICE[1:]
    account = simAccount(sim, service + [{'name': 'Campus', 'virtual': True}, {'name': 'Barn Panel', 'parent': 'Campus'}])
    readings = readingsOf(collectHour(makeConfig(), account, T0))
    assert readings[('Service', 'Service')] == pytest.approx(900.0)
    assert readings[('Campus', 'Campus')] == pytest.approx(1150.0)


def test_a_virtual_total_leaves_out_a_part_that_reported_nothing_and_says_so(caplog):
    """Panel L2 reports nothing for an hour: the total is Panel L1's alone, with one
    warning. When Panel L2 reports again it is back in, and that is noted."""
    t1 = T0 + datetime.timedelta(hours=1)
    readings = {T0: {k: v for k, v in SPLIT_RAW.items() if k[0] != 21}, t1: dict(SPLIT_RAW)}
    account = simAccount(SimulatedEmporia(SPLIT, readings), SERVICE)
    with caplog.at_level(logging.INFO, logger='vuegraf.hierarchy'):
        first = collectHour(makeConfig(), account, T0)
        again = collectHour(makeConfig(), account, T0)
        back = collectHour(makeConfig(), account, t1)
    assert readingsOf(first)[('Service', 'Service')] == pytest.approx(500.0)
    assert readingsOf(again)[('Service', 'Service')] == pytest.approx(500.0)
    assert readingsOf(back)[('Service', 'Service')] == pytest.approx(900.0)
    assert netBalances(first)['Panel L1'] == pytest.approx(200.0)
    messages = [r.getMessage() for r in caplog.records if 'Panel L2' in r.getMessage()]
    assert messages == ['Net Balance: "Panel L2" reported nothing, so the total "Service" leaves it out until it does',
                        'Net Balance: "Panel L2" is reporting again and is back in the total "Service"']


def test_a_virtual_total_is_skipped_when_no_part_reported():
    account = simAccount(simulated(SPLIT, {(23, MAINS): 0.25, (23, '1'): 0.2}), SERVICE)
    assert ('Service', 'Service') not in readingsOf(collectHour(makeConfig(), account, T0))


OFFICE = [panel(30, 'Panel P', {'1': 'Office', '2': 'Hall'}), plug(31, 'Lamp')]


@pytest.mark.parametrize('emporiaNests', [True, False])
def test_a_plug_on_a_circuit_whether_or_not_the_app_nests_it(emporiaNests):
    devices = [OFFICE[0], plug(31, 'Lamp', (30, '1') if emporiaNests else None)]
    sim = simulated(devices, {(30, MAINS): 0.5, (30, '1'): 0.2, (30, '2'): 0.1, (31, MAINS): 0.05})
    account = simAccount(sim, [{'name': 'Lamp', 'parent': 'Office'}])
    live, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert readingsOf(live)[('Panel P', 'Office')] == pytest.approx(150.0 if emporiaNests else 200.0)
    assert liveBalances == pytest.approx({'Office': 150.0, 'Panel P': 200.0})
    assert historyBalances == pytest.approx(liveBalances)


@pytest.mark.parametrize('emporiaNests', [True, False])
def test_a_plug_on_the_mains_whether_or_not_the_app_nests_it(emporiaNests):
    devices = [OFFICE[0], plug(31, 'Lamp', (30, MAINS) if emporiaNests else None)]
    sim = simulated(devices, {(30, MAINS): 0.5, (30, '1'): 0.2, (30, '2'): 0.1, (31, MAINS): 0.05})
    account = simAccount(sim, [{'name': 'Lamp', 'parent': 'Panel P'}])
    live, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert readingsOf(live)[('Panel P', 'Panel P-Balance')] == pytest.approx(150.0 if emporiaNests else 200.0)
    assert liveBalances == pytest.approx({'Panel P': 150.0})
    assert historyBalances == pytest.approx(liveBalances)


@pytest.mark.parametrize('emporiaNests', [True, False])
def test_a_circuit_reading_less_than_what_hangs_under_it_is_floored(emporiaNests):
    devices = [OFFICE[0], plug(31, 'Lamp', (30, '1') if emporiaNests else None)]
    sim = simulated(devices, {(30, MAINS): 0.5, (30, '1'): 0.05, (30, '2'): 0.1, (31, MAINS): 0.2})
    account = simAccount(sim, [{'name': 'Lamp', 'parent': 'Office'}])
    _, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert liveBalances['Office'] == 0.0
    assert liveBalances['Panel P'] == pytest.approx(350.0)  # the panel's remainder is unaffected
    assert historyBalances == pytest.approx(liveBalances)


def test_a_negative_panel_balance_is_written_and_reported(caplog):
    """Emporia's own Balance goes negative when the circuits read more than the mains; the
    Net Balance is written as-is, like the Balance, and reported once past the tolerance."""
    sim = simulated([OFFICE[0]], {(30, MAINS): 0.25, (30, '1'): 0.2, (30, '2'): 0.1})
    account = simAccount(sim, hierarchyEnabled=True)
    with caplog.at_level(logging.WARNING, logger='vuegraf.hierarchy'):
        _, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert liveBalances == pytest.approx({'Panel P': -50.0})
    assert historyBalances == pytest.approx(liveBalances)
    assert [r.getMessage() for r in caplog.records] == [
        'Net Balance: "Panel P" reads less than its circuits and the devices on its mains by 50.0; '
        'totals built from this tree will not add up']  # live and history, reported once


def test_a_shortfall_within_the_tolerance_is_measurement_noise(caplog):
    sim = simulated([OFFICE[0]], {(30, MAINS): 0.297, (30, '1'): 0.2, (30, '2'): 0.1})
    account = simAccount(sim, hierarchyEnabled=True)
    with caplog.at_level(logging.WARNING, logger='vuegraf.hierarchy'):
        _, liveBalances, _ = liveAndHistory(sim, account)
    assert liveBalances == pytest.approx({'Panel P': -3.0})
    assert not caplog.records


def test_an_offline_nested_device_counts_as_nothing_itemized():
    devices = [OFFICE[0], plug(31, 'Lamp', (30, '1'))]
    sim = simulated(devices, {(30, MAINS): 0.5, (30, '1'): 0.2, (30, '2'): 0.1})
    account = simAccount(sim, hierarchyEnabled=True)
    _, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert liveBalances == pytest.approx({'Office': 200.0, 'Panel P': 200.0})
    assert historyBalances == pytest.approx(liveBalances)


def test_a_circuit_with_no_reading_leaves_its_load_in_the_panel_balance():
    sim = simulated([OFFICE[0]], {(30, MAINS): 0.5, (30, '1'): 0.2})
    account = simAccount(sim, hierarchyEnabled=True)
    _, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert liveBalances == pytest.approx({'Panel P': 300.0})
    assert historyBalances == pytest.approx(liveBalances)


def test_a_circuit_with_no_reading_gets_no_net_balance_of_its_own(caplog):
    """Emporia leaves out a channel with no data, and its Balance leaves that circuit out
    too, so the panel's remainder still includes what was drawn through it."""
    devices = [OFFICE[0], plug(31, 'Lamp', (30, '1'))]
    sim = simulated(devices, {(30, MAINS): 0.5, (30, '2'): 0.1, (31, MAINS): 0.05})
    account = simAccount(sim, hierarchyEnabled=True)
    with caplog.at_level(logging.WARNING, logger='vuegraf.hierarchy'):
        _, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert liveBalances == pytest.approx({'Panel P': 400.0})
    assert historyBalances == pytest.approx(liveBalances)
    assert not caplog.records


def test_daily_rollups_get_net_balances_too():
    sim = simulated(cascade(), CASCADE_RAW, scale='1D', at=DAY0)
    account = simAccount(sim, hierarchyEnabled=True)
    config = makeConfig()
    dayInstant = DAY0.replace(hour=23, minute=59, second=59)
    live = collectDay(config, account, dayInstant)
    history = collectHistory(config, account, DAY0, DAY0 + datetime.timedelta(days=1))
    # A daily point is the day's kWh x 1000, like an hourly one, so the same inputs give the same numbers.
    assert netBalances(live, 'Day') == pytest.approx(CASCADE_BALANCES)
    assert netBalances(history, 'Day', live[0].timestamp) == pytest.approx(netBalances(live, 'Day'))


def test_derived_points_follow_the_configured_resolution_tags():
    """Net Balances are found by the destination's own tag values, so a renamed tag on a
    VictoriaMetrics-only install still works."""
    sim = simulated(cascade(), CASCADE_RAW)
    account = simAccount(sim, hierarchyEnabled=True)
    config = makeConfig(victoriaMetrics={'url': 'http://vm', 'tagValue_hour': 'H', 'tagValue_day': 'D'})
    del config['influxDb']
    points = collectHour(config, account, T0)
    assert netBalances(points, 'H') == pytest.approx(CASCADE_BALANCES)


def test_history_derives_each_period_once_across_batches():
    """A 30-day backfill runs in two batches; each hour gets one Net Balance per node."""
    hours = [DAY0 + datetime.timedelta(hours=h) for h in range(30 * 24)]
    readings = {h: {(30, MAINS): 0.5, (30, '1'): 0.2, (30, '2'): 0.1, (31, MAINS): 0.05} for h in hours}
    sim = SimulatedEmporia([OFFICE[0], plug(31, 'Lamp', (30, '1'))], readings)
    account = simAccount(sim, hierarchyEnabled=True)
    points = collectHistory(makeConfig(), account, DAY0, hours[-1] + datetime.timedelta(hours=1))
    derived = [(p.chanName, p.timestamp) for p in points if p.detailed == 'Hour' and isDerived(p, account)]
    assert len(derived) == len(set(derived)) == 2 * len(hours)
    assert all(p.usageWatts == pytest.approx(150.0) for p in points if p.detailed == 'Hour' and p.chanName == 'Office Net Balance')


# ---------------------------------------------------------------------------
# Building the tree: what it accepts, and what it refuses with a useful message
# ---------------------------------------------------------------------------

TWO_PANELS = [panel(40, 'Panel X', {'1': 'Lights', '2': 'X Feed'}), panel(41, 'Panel Y', {'1': 'Y Lights', '2': 'Y Feed'}),
              plug(42, 'Lamp'), panel(43, 'Sub', {'1': 'Sub Loads'})]


def build(devices, extra=(), **settings):
    return simAccount(simulated(devices, {}), extra, **settings)['hierarchy']


def nodeNamed(hierarchy, name, kind=None):
    matches = [n for n in hierarchy.nodes.values() if n.name == name and (kind is None or n.kind == kind)]
    assert len(matches) == 1, matches
    return matches[0]


def test_the_tree_combines_emporia_nesting_and_config_links():
    devices = TWO_PANELS[:2] + [plug(42, 'Lamp', (40, '1')), panel(43, 'Sub', {'1': 'Sub Loads'})]
    hierarchy = build(devices, [{'name': 'Sub', 'parent': 'Y Feed'}, {'name': 'Lamp', 'parent': 'Lights'}])
    lamp, sub = nodeNamed(hierarchy, 'Lamp'), nodeNamed(hierarchy, 'Sub')
    assert hierarchy.nodes[lamp.parent].name == 'Lights' and lamp.emporiaEdge  # restated, still Emporia's
    assert hierarchy.nodes[sub.parent].name == 'Y Feed' and not sub.emporiaEdge
    assert [hierarchy.nodes[c].name for c in nodeNamed(hierarchy, 'Panel X').circuits] == ['Lights', 'X Feed']


@pytest.mark.parametrize('extra, message', [
    ([{'name': 'Lamp', 'parent': 'Nowhere'}], 'parent "Nowhere" is not a known device, circuit or virtual device'),
    ([{'name': 'Lamp', 'parent': 'Lamp'}], '"Lamp" cannot be its own parent'),
    ([{'name': 'Lamp', 'parent': ''}], '"parent" of "Lamp" must be a non-empty string'),
    ([{'name': 'Lamp', 'parent': 7}], '"parent" of "Lamp" must be a non-empty string'),
    ([{'parent': 'Lights'}], 'every device entry needs a name'),
    ([{'name': 'Service', 'virtual': 'yes'}], '"virtual" of "Service" must be true or false'),
    ([{'name': 'Service', 'virtual': True, 'channels': ['a']}], 'virtual device "Service" cannot have channels'),
    ([{'name': 'Lamp', 'virtual': True}], 'virtual device "Lamp" has the name of a real device or circuit'),
    ([{'name': 'Service', 'virtual': True, 'parent': 'Panel X'}], 'virtual device "Service" can only be placed under another'),
    ([{'name': 'Sub', 'parent': 'Y Feed'}, {'name': 'Panel Y', 'parent': 'Sub Loads'}], 'parents form a cycle'),
    ([{'name': 'Panel X', 'parent': 'X Feed'}], 'parents form a cycle: Panel X -> X Feed -> Panel X'),
])
def test_config_errors_are_refused_with_a_message(extra, message):
    with pytest.raises(ValueError, match=message.replace('(', r'\(').replace(')', r'\)')):
        build(TWO_PANELS, extra)


def test_a_config_parent_that_contradicts_emporia_is_refused():
    devices = TWO_PANELS[:2] + [plug(42, 'Lamp', (40, '1'))]
    with pytest.raises(ValueError, match='Emporia nests it under circuit "Lights" of "Panel X".*change the nesting in the Emporia app'):
        build(devices, [{'name': 'Lamp', 'parent': 'Y Lights'}])
    with pytest.raises(ValueError, match='config places "Lamp" under device "Panel X", but Emporia nests it under circuit'):
        build(devices, [{'name': 'Lamp', 'parent': 'Panel X'}])


def test_an_ambiguous_name_is_refused_only_when_a_parent_uses_it():
    devices = [panel(40, 'Panel X', {'1': 'Lights'}), panel(41, 'Panel Y', {'1': 'Lights'}), plug(42, 'Lamp')]
    assert build(devices, hierarchyEnabled=True) is not None
    with pytest.raises(ValueError, match='"Lights" is ambiguous; it names circuit "Lights" of "Panel X" and circuit "Lights" of "Panel Y"'):
        build(devices, [{'name': 'Lamp', 'parent': 'Lights'}])


def test_a_derived_series_that_would_overwrite_another_is_refused():
    devices = [panel(40, 'Panel X', {'1': 'Panel X Net Balance'})]
    with pytest.raises(ValueError, match='derived series "Panel X" / "Panel X Net Balance".*would collide'):
        build(devices, hierarchyEnabled=True)


def test_warnings_for_what_cannot_be_placed(caplog):
    devices = TWO_PANELS[:2] + [plug(42, 'Lamp', (99, '1')), plug(44, 'Clock', (40, '9'))]
    with caplog.at_level(logging.WARNING, logger='vuegraf.hierarchy'):
        hierarchy = build(devices, [{'name': 'Ghost', 'parent': 'Panel X'}, {'name': 'Empty', 'virtual': True}])
    text = caplog.text
    assert 'configured device "Ghost" was not discovered' in text
    assert 'virtual device "Empty" in account "Home" has nothing under it' in text
    assert '"Lamp" is nested under device 99, which this account does not list' in text
    assert '"Clock" is nested under channel 9 of "Panel X", which was not discovered' in text
    assert nodeNamed(hierarchy, 'Lamp').parent is None
    assert hierarchy.nodes[nodeNamed(hierarchy, 'Clock').parent].name == 'Panel X'
    assert derivedSeriesKey(nodeNamed(hierarchy, 'Empty')) is None


def test_the_tree_is_built_once_at_login():
    sim = simulated(CLARA, CLARA_RAW)
    account = {'name': 'Home', 'email': 'e', 'password': 'p', 'hierarchyEnabled': True}
    vue = makeVue(sim)
    vue.login = MagicMock()
    with patch('vuegraf.device.PyEmVue', return_value=vue):
        from vuegraf.device import initDeviceAccount
        initDeviceAccount({}, account)
        first = account['hierarchy']
        initDeviceAccount({}, account)
    assert first is not None and account['hierarchy'] is first
    assert nodeNamed(first, 'Desk').kind == DEVICE


def test_a_device_given_a_parent_twice_is_refused():
    devices = [{'name': 'Lamp', 'parent': 'Lights'}, {'name': 'Lamp', 'parent': 'Y Lights'},
               {'name': 'Panel X', 'channels': {'1': 'Lights'}}, {'name': 'Panel Y', 'channels': {'1': 'Y Lights'}}]
    account = {'name': 'Home', 'devices': devices}
    account['vue'] = makeVue(simulated(TWO_PANELS, {}))
    populateDevices(account)
    with pytest.raises(ValueError, match='device "Lamp" is listed more than once'):
        buildHierarchy(account)


def test_a_device_emporia_lists_without_a_name_is_left_out():
    """Discovery only maps a device by its named entry, so its channels have no device to
    belong to; the tree skips them rather than failing."""
    sim = simulated([panel(40, 'Panel X', {'1': 'Lights'}), panel(41, '', {'1': 'Orphan'})], {})
    hierarchy = makeAccount(sim, [{'name': 'Panel X', 'channels': {'1': 'Lights'}}], hierarchyEnabled=True)['hierarchy']
    assert {n.name for n in hierarchy.nodes.values()} == {'Panel X', 'Lights'}


def test_only_aggregates_of_the_account_are_used():
    """appendNetBalances takes whatever slice it is given: other accounts' points and
    minute points in it are ignored rather than netted."""
    from vuegraf.collect import Point, appendNetBalances
    sim = simulated([OFFICE[0]], {(30, MAINS): 0.5, (30, '1'): 0.2, (30, '2'): 0.1})
    account = simAccount(sim, hierarchyEnabled=True)
    points = [Point('Home', 'Panel P', 'Panel P', 500.0, T0, 'Hour'), Point('Home', 'Panel P', 'Office', 200.0, T0, 'Hour'),
              Point('Home', 'Panel P', 'Hall', 100.0, T0, 'False'),        # a minute reading
              Point('Other', 'Panel P', 'Hall', 100.0, T0, 'Hour')]        # another account's
    appendNetBalances(makeConfig(), account, points, 0, 'history')
    assert netBalances(points) == pytest.approx({'Panel P': 300.0})


# ---------------------------------------------------------------------------
# Review follow-ups: live rollups, the whole-account total, aborting, unique names
# ---------------------------------------------------------------------------


def test_an_account_total_is_synthesized_over_several_top_level_panels():
    """Panel L1, Panel L2 and Barn Panel have nothing above them, so '<account> Panel'
    totals them; the unplaced plug is left out."""
    sim = simulated(SPLIT, SPLIT_RAW)
    account = simAccount(sim, hierarchyEnabled=True)
    root = account['hierarchy'].nodes[account['hierarchy'].accountRoot]
    assert root.name == 'Home Panel'
    live, liveBalances, historyBalances = liveAndHistory(sim, account)
    readings = readingsOf(live)
    assert readings[('Home Panel', 'Home Panel')] == pytest.approx(500.0 + 400.0 + 250.0)
    assert subtreeTotal(account, live, 'Home Panel', 'Hour', T0) == pytest.approx(1150.0)
    history = readingsOf(collectHistory(makeConfig(), account, T0, T0 + datetime.timedelta(hours=1)), 'Hour', T0)
    assert history[('Home Panel', 'Home Panel')] == pytest.approx(1150.0)


def test_an_account_total_includes_a_declared_virtual_device_as_one_part():
    sim = simulated(SPLIT, SPLIT_RAW)
    account = simAccount(sim, SERVICE)
    readings = readingsOf(collectHour(makeConfig(), account, T0))
    assert readings[('Service', 'Service')] == pytest.approx(900.0)
    assert readings[('Home Panel', 'Home Panel')] == pytest.approx(1150.0)  # Service + Barn Panel


def test_no_account_total_for_a_single_top_level_panel_or_virtual_device():
    assert build(cascade(), hierarchyEnabled=True).accountRoot is None
    everything = SERVICE + [{'name': 'Barn Panel', 'parent': 'Service'}]
    assert simAccount(simulated(SPLIT, SPLIT_RAW), everything)['hierarchy'].accountRoot is None


def test_the_account_total_counts_a_panel_whenever_it_reports(caplog):
    """Emporia reporting a panel as offline at startup does not keep it out of the total:
    only whether it reports in a given period does."""
    devices = SPLIT[:3] + [dict(SPLIT[3], connected=False)]
    with caplog.at_level(logging.INFO, logger='vuegraf.hierarchy'):
        account = simAccount(simulated(devices, SPLIT_RAW), hierarchyEnabled=True)
    assert readingsOf(collectHour(makeConfig(), account, T0))[('Home Panel', 'Home Panel')] == pytest.approx(1150.0)
    assert '"Home Panel" totals 3 top-level panels in account "Home"; left out, as unplaced: Roaming' in caplog.text


def test_the_account_total_name_must_be_free():
    devices = SPLIT[:2] + [plug(22, 'Home Panel')]
    with pytest.raises(ValueError, match='cannot add the whole-account total "Home Panel", because that name is already in use'):
        build(devices, hierarchyEnabled=True)


def test_the_measured_account_gets_a_total_of_its_independent_panels(measuredSim):
    account = makeAccount(measuredSim, MEASURED_DEVICES)
    hierarchy = account['hierarchy']
    parts = sorted(hierarchy.nodes[c].name for c in hierarchy.nodes[hierarchy.accountRoot].children)
    assert parts == ['Panel A', 'Panel C', 'Panel G', 'Subpanel G2']  # not the unplaced plug
    readings = readingsOf(collectHour(makeConfig(MEASURED_TZ), account, MEASURED_HOUR))
    assert readings[('Home Panel', 'Home Panel')] == pytest.approx(sum(readings[(p, p)] for p in parts))


def test_aborting_drops_the_live_period_and_reports_every_shortfall(caplog):
    config = makeConfig(hierarchyNegativeBalanceAbort=True)
    sim = simulated([OFFICE[0], plug(31, 'Lamp')], {(30, MAINS): 0.25, (30, '1'): 0.2, (30, '2'): 0.1, (31, MAINS): 0.3})
    account = simAccount(sim, [{'name': 'Lamp', 'parent': 'Office'}])
    points = ['earlier point']
    with caplog.at_level(logging.ERROR, logger='vuegraf.data'):
        collectUsage(config, account, T0, T0, False, points, None, Scale.HOUR.value)
    assert points == ['earlier point']  # the hour's raw readings are dropped too
    [message] = [r.getMessage() for r in caplog.records]
    assert message.startswith('Not recording a period that does not balance: Hour 2026-01-15T10:00:00+00:00: ')
    assert '"Office" reads less than the devices beneath it by 100.0' in message
    assert '"Panel P" reads less than its circuits and the devices on its mains by 50.0' in message


def test_an_aborted_period_does_not_hold_back_later_hours_or_the_day():
    """A closed period that does not balance never will, so it is dropped rather than
    retried: each later hour, and the day, is still collected once."""
    from vuegraf import vuegraf
    hours = [T0 + datetime.timedelta(hours=h) for h in range(4)]
    bad = {(30, MAINS): 0.25, (30, '1'): 0.2, (30, '2'): 0.1}  # the panel reads less than its circuits
    sim = SimulatedEmporia([OFFICE[0]], {h: bad for h in hours})
    account = simAccount(sim, hierarchyEnabled=True)
    config = makeConfig(hierarchyNegativeBalanceAbort=True, accounts=[account], args=MagicMock(historydays=0), lagSecs=0,
                        maxHistoryDays=30, updateIntervalSecs=60, detailedIntervalSecs=3600,
                        detailedDataHoursEnabled=True, detailedDataDaysEnabled=True)
    dayEnd = DAY0.replace(hour=23, minute=59, second=59)
    days = [dayEnd] * 3 + [dayEnd + datetime.timedelta(days=1)]
    cycles = [len(hours) - 1]

    def wait(_):
        cycles[0] -= 1
        if cycles[0] == 0:
            vuegraf.running = False

    with patch('vuegraf.vuegraf.initConfig', return_value=config), patch('vuegraf.vuegraf.initConnection'), \
            patch('vuegraf.vuegraf.initDeviceAccount'), patch('vuegraf.vuegraf.writeDataPoints'), \
            patch('vuegraf.vuegraf.getTimeNow', return_value=hours[-1]), \
            patch('vuegraf.vuegraf.getCurrentHourUTC', side_effect=hours), \
            patch('vuegraf.vuegraf.getCurrentDayLocal', side_effect=days), \
            patch('vuegraf.vuegraf.pauseEvent') as pause, patch('vuegraf.collect.getLastDBTimeStamp', return_value=(None, None, False)):
        pause.wait.side_effect = wait
        vuegraf.run()
    requests = [path.split('instant=')[1].split('&')[0] + ' ' + path.split('scale=')[1].split('&')[0]
                for path in account['vue'].auth.log if 'getDeviceListUsages' in path and 'scale=1MIN' not in path]
    # pyemvue itself repeats a request whose response has empty channels, as this day's does
    # here (the simulator holds hourly readings only); those repeats are one collection.
    rollups = [r for i, r in enumerate(requests) if i == 0 or r != requests[i - 1]]
    assert rollups == ['2026-01-15T10:00:00Z 1H', '2026-01-15T11:00:00Z 1H', '2026-01-15T12:00:00Z 1H',
                       '2026-01-15T23:59:59Z 1D']


def test_aborting_in_history_drops_only_the_periods_that_do_not_balance(caplog):
    """One bad hour in a backfill loses that hour, raw readings included, and nothing
    else: the hours around it, and the rest of the backfill, are recorded."""
    good = {(30, MAINS): 0.5, (30, '1'): 0.2, (30, '2'): 0.1, (31, MAINS): 0.05}
    bad = {**good, (31, MAINS): 0.3}  # the lamp reads more than its circuit
    hours = [T0 + datetime.timedelta(hours=h) for h in range(3)]
    sim = SimulatedEmporia([OFFICE[0], plug(31, 'Lamp')], {hours[0]: good, hours[1]: bad, hours[2]: good})
    account = simAccount(sim, [{'name': 'Lamp', 'parent': 'Office'}])
    with caplog.at_level(logging.ERROR, logger='vuegraf.data'):
        points = collectHistory(makeConfig(hierarchyNegativeBalanceAbort=True), account, hours[0],
                                hours[2] + datetime.timedelta(hours=1))
    hourly = {p.timestamp for p in points if p.detailed == 'Hour'}
    assert hourly == {hours[0], hours[2]}
    assert netBalances(points, 'Hour', hours[2]) == pytest.approx({'Office': 150.0, 'Panel P': 200.0})
    assert [r.getMessage()[:60] for r in caplog.records] == ['Not recording history periods that do not balance: Hour 2026']


def test_aborting_leaves_a_balanced_period_alone():
    config = makeConfig(hierarchyNegativeBalanceAbort=True)
    account = simAccount(simulated(cascade(), CASCADE_RAW), hierarchyEnabled=True)
    assert netBalances(collectHour(config, account, T0)) == pytest.approx(CASCADE_BALANCES)


def test_the_tolerance_scales_with_the_period():
    """A daily reading is 24 hours of watt-hours, so a shortfall of 50 Wh in a day is an
    average of about 2 W -- within a 5 W tolerance -- where 50 Wh in an hour is not."""
    raw = {(30, MAINS): 0.25, (30, '1'): 0.2, (30, '2'): 0.1}
    config = makeConfig(hierarchyNegativeBalanceAbort=True)
    daily = simAccount(simulated([OFFICE[0]], raw, scale='1D', at=DAY0), hierarchyEnabled=True)
    assert netBalances(collectDay(config, daily, DAY0.replace(hour=23, minute=59, second=59)), 'Day') == \
        pytest.approx({'Panel P': -50.0})
    hourly = simAccount(simulated([OFFICE[0]], raw), hierarchyEnabled=True)
    assert collectHour(config, hourly, T0) == []  # dropped


@pytest.mark.parametrize('emporiaNests', [True, False])
def test_a_circuit_overdrawn_within_the_tolerance_is_not_reported(emporiaNests, caplog):
    """A plug reading 2 W more than its circuit is CT tolerance, whether Emporia floors the
    circuit (live) or Vuegraf does (history, or a link only the config knows)."""
    devices = [OFFICE[0], plug(31, 'Lamp', (30, '1') if emporiaNests else None)]
    sim = simulated(devices, {(30, MAINS): 0.5, (30, '1'): 0.2, (30, '2'): 0.1, (31, MAINS): 0.202})
    account = simAccount(sim, [{'name': 'Lamp', 'parent': 'Office'}])
    with caplog.at_level(logging.WARNING, logger='vuegraf.hierarchy'):
        _, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert liveBalances['Office'] == 0.0 and historyBalances['Office'] == 0.0
    assert not caplog.records


def test_a_repeated_shortfall_is_reported_again_after_an_hour(caplog):
    sim = simulated([OFFICE[0]], {(30, MAINS): 0.25, (30, '1'): 0.2, (30, '2'): 0.1})
    account = simAccount(sim, hierarchyEnabled=True)
    with caplog.at_level(logging.WARNING, logger='vuegraf.hierarchy'), \
            patch('vuegraf.hierarchy.time.monotonic', side_effect=[1000.0, 1000.0 + 3599, 1000.0 + 3600]):
        for _ in range(3):
            collectHour(makeConfig(), account, T0)
    assert len(caplog.records) == 2


def test_a_duplicate_emporia_device_name_is_refused():
    """Two devices named Lamp write to the same series, so one reading would stand in for
    both. Naming one as a child is reported as ambiguous; otherwise as a collision."""
    devices = TWO_PANELS[:2] + [plug(42, 'Lamp'), plug(44, 'Lamp')]
    with pytest.raises(ValueError, match='device "Lamp" and device "Lamp" would both be written as "Lamp" / "Lamp"'):
        build(devices, hierarchyEnabled=True)
    with pytest.raises(ValueError, match='"Lamp" is ambiguous; it names device "Lamp" and device "Lamp"'):
        build(devices, [{'name': 'Lamp', 'parent': 'Lights'}])


def test_two_circuits_of_one_panel_with_the_same_name_are_refused():
    """Loads (200 Wh) and Loads (100 Wh) under a 500 Wh mains would be one series, and
    the Net Balance would come out 200 Wh live but 300 Wh from history."""
    sim = simulated([panel(30, 'Panel P', {'1': 'Loads', '2': 'Loads'})], {(30, MAINS): 0.5, (30, '1'): 0.2, (30, '2'): 0.1})
    with pytest.raises(ValueError, match='circuit "Loads" of "Panel P" and circuit "Loads" of "Panel P" would both be '
                                         'written as "Panel P" / "Loads"'):
        simAccount(sim, hierarchyEnabled=True)
    assert simAccount(sim)['hierarchy'] is None  # without the hierarchy, nothing changes


def test_the_same_circuit_name_on_different_panels_is_fine():
    assert build([panel(40, 'Panel X', {'1': 'Lights'}), panel(41, 'Panel Y', {'1': 'Lights'})], hierarchyEnabled=True)


@pytest.mark.parametrize('emporiaNests', [True, False])
def test_what_reports_beneath_an_unread_circuit_still_comes_off_the_panel(emporiaNests):
    """The Office circuit reports nothing, but the Lamp on it does: 500 Wh mains, 100 Wh
    Hall, 50 Wh Lamp. The Lamp is inside the panel's remainder, so it is taken out of the
    panel's Net Balance, which is 350, and the pieces add up to the mains."""
    readings = {('Panel P', 'Panel P'): 500.0, ('Panel P', 'Hall'): 100.0, ('Lamp', 'Lamp'): 50.0}
    for source in ('live', 'history'):
        sim = simulated([OFFICE[0], plug(31, 'Lamp', (30, '1') if emporiaNests else None)], {})
        account = simAccount(sim, [{'name': 'Lamp', 'parent': 'Office'}])
        live = {**readings, ('Panel P', 'Panel P-Balance'): 400.0} if source == 'live' else readings
        derived = computeNetBalances(account['hierarchy'], live, source)
        assert derived == pytest.approx({('Panel P', 'Panel P Net Balance'): 350.0}), source


def test_a_config_linked_plug_under_an_unread_circuit_adds_up(caplog):
    """The same case end to end, where Emporia does not know the Lamp is on Office."""
    sim = simulated([OFFICE[0], plug(31, 'Lamp')], {(30, MAINS): 0.5, (30, '2'): 0.1, (31, MAINS): 0.05})
    account = simAccount(sim, [{'name': 'Lamp', 'parent': 'Office'}], hierarchyNegativeBalanceAbort=True)
    with caplog.at_level(logging.WARNING, logger='vuegraf.hierarchy'):
        live, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert liveBalances == pytest.approx({'Panel P': 350.0})
    assert historyBalances == pytest.approx(liveBalances)
    assert subtreeTotal(account, live, 'Panel P', 'Hour', T0) == pytest.approx(500.0)
    assert not caplog.records


@pytest.mark.parametrize('emporiaNests', [True, False])
def test_a_subpanel_with_no_mains_reading_is_counted_by_its_circuits(emporiaNests):
    """The subpanel's mains reports nothing but its circuit does (250 Wh). Emporia takes
    nothing off the feed for it, so its circuits' readings come off the feed instead."""
    devices = [panel(60, 'Main', {'1': 'Sub Feed', '2': 'Other'}),
               panel(61, 'Sub', {'1': 'Sub Loads'}, (60, '1') if emporiaNests else None)]
    sim = simulated(devices, {(60, MAINS): 1.0, (60, '1'): 0.4, (60, '2'): 0.3, (61, '1'): 0.25})
    account = simAccount(sim, [] if emporiaNests else [{'name': 'Sub', 'parent': 'Sub Feed'}], hierarchyEnabled=True)
    live, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert liveBalances == pytest.approx({'Sub Feed': 150.0, 'Main': 300.0})
    assert historyBalances == pytest.approx(liveBalances)
    assert subtreeTotal(account, live, 'Main', 'Hour', T0) == pytest.approx(1000.0)


def test_a_hierarchy_without_hourly_or_daily_data_is_reported(caplog):
    sim = simulated(CLARA, CLARA_RAW)
    for hours, days, warned in ((True, True, False), (False, True, False), (False, False, True)):
        vue = makeVue(sim)
        vue.login = MagicMock()
        account = {'name': 'Home', 'email': 'e', 'password': 'p', 'hierarchyEnabled': True}
        caplog.clear()
        with patch('vuegraf.device.PyEmVue', return_value=vue), caplog.at_level(logging.WARNING, logger='vuegraf.device'):
            from vuegraf.device import initDeviceAccount
            initDeviceAccount({'detailedDataHoursEnabled': hours, 'detailedDataDaysEnabled': days}, account)
        assert ('Net Balances will only be written by --historydays' in caplog.text) is warned


def test_the_readme_example_of_one_circuit_series_with_two_meanings():
    """The worked example in the README, 'Caution: one circuit series, two meanings'."""
    devices = [panel(50, 'Main Panel', {'1': 'Office', '2': 'Other'}), plug(51, 'Desk', (50, '1'))]
    sim = simulated(devices, {(50, MAINS): 1.0, (50, '1'): 0.113, (50, '2'): 0.7, (51, MAINS): 0.08})
    optOut = simAccount(sim)
    live = readingsOf(collectHour(makeConfig(), optOut, T0))
    backfilled = readingsOf(collectHistory(makeConfig(), optOut, T0, T0 + datetime.timedelta(hours=1)), 'Hour', T0)
    assert live[('Main Panel', 'Office')] == pytest.approx(33.0)
    assert backfilled[('Main Panel', 'Office')] == pytest.approx(113.0)
    assert live[('Main Panel', 'Main Panel-Balance')] == pytest.approx(187.0)
    assert ('Main Panel', 'Main Panel-Balance') not in backfilled
    liveCircuitsAndPlug = live[('Main Panel', 'Office')] + live[('Main Panel', 'Other')] + live[('Desk', 'Desk')]
    assert liveCircuitsAndPlug + live[('Main Panel', 'Main Panel-Balance')] == pytest.approx(1000.0)
    backfilledCircuits = backfilled[('Main Panel', 'Office')] + backfilled[('Main Panel', 'Other')]
    assert backfilledCircuits + backfilled[('Desk', 'Desk')] == pytest.approx(893.0)
    assert backfilledCircuits + backfilled[('Desk', 'Desk')] + (1000.0 - backfilledCircuits) == pytest.approx(1080.0)

    _, liveBalances, historyBalances = liveAndHistory(sim, simAccount(sim, hierarchyEnabled=True))
    assert liveBalances == pytest.approx({'Office': 33.0, 'Main Panel': 187.0})
    assert historyBalances == pytest.approx(liveBalances)


def test_whatever_reports_deep_beneath_unread_readings_comes_off_the_feed():
    """The subpanel's mains and one of its circuits report nothing, but a lamp on that
    circuit (50 Wh) and the subpanel's other circuit (100 Wh) do. Both come off Sub Feed,
    and the pieces still add up to the 1000 Wh mains."""
    devices = [panel(60, 'Main', {'1': 'Sub Feed', '2': 'Other'}), panel(61, 'Sub', {'1': 'Sub Loads', '2': 'Sub Other'}),
               plug(62, 'Lamp')]
    sim = simulated(devices, {(60, MAINS): 1.0, (60, '1'): 0.4, (60, '2'): 0.3, (61, '2'): 0.1, (62, MAINS): 0.05})
    account = simAccount(sim, [{'name': 'Sub', 'parent': 'Sub Feed'}, {'name': 'Lamp', 'parent': 'Sub Loads'}])
    live, liveBalances, historyBalances = liveAndHistory(sim, account)
    assert liveBalances == pytest.approx({'Sub Feed': 250.0, 'Main': 300.0})
    assert historyBalances == pytest.approx(liveBalances)
    assert subtreeTotal(account, live, 'Main', 'Hour', T0) == pytest.approx(1000.0)
