# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

"""Unit tests for the device hierarchy and the per-level Net Balance calculation."""

import datetime

import pytest
from pyemvue.device import VueDevice, VueDeviceChannel

# Local imports
from vuegraf import hierarchy
from vuegraf.collect import Point
from vuegraf.device import validateHierarchy


HOUR_TS = datetime.datetime(2026, 7, 1, 10, 0, 0, tzinfo=datetime.UTC)
DAY_TS = datetime.datetime(2026, 7, 1, 23, 59, 59, tzinfo=datetime.UTC)


def makeDevice(gid, name, channelNums):
    device = VueDevice()
    device.device_gid = gid
    device.device_name = name
    device.channels = []
    for num in channelNums:
        chan = VueDeviceChannel()
        chan.device_gid = gid
        chan.channel_num = num
        chan.name = name if num == '1,2,3' else None
        device.channels.append(chan)
    return device


def registerDevices(account, devices):
    for device in devices:
        account['deviceIdMap'][device.device_gid] = device
        for chan in device.channels:
            account['channelIdMap']['{}-{}'.format(device.device_gid, chan.channel_num)] = chan
    return account


def buildAccount():
    """An account shaped like a real install, exercising every kind of edge.

    Main Panel (gid 100)
      +- Heat Pump      (circuit 1)
      +- Garage Feed    (circuit 2)
      |    +- Garage Subpanel (gid 200, explicit parent 'Garage Feed')
      |         +- Garage Lights (circuit 1)
      +- EV Charger     (gid 300, explicit parent 'Main Panel')
    """
    account = {
        'name': 'Home',
        'deviceIdMap': {},
        'channelIdMap': {},
        'devices': [
            {'name': 'Main Panel', 'channels': ['Heat Pump', 'Garage Feed']},
            {'name': 'Garage Subpanel', 'parent': 'Garage Feed', 'channels': ['Garage Lights']},
            {'name': 'EV Charger', 'parent': 'Main Panel'},
        ],
    }
    return registerDevices(account, [makeDevice(100, 'Main Panel', ['1,2,3', '1', '2']),
                                     makeDevice(200, 'Garage Subpanel', ['1,2,3', '1']),
                                     makeDevice(300, 'EV Charger', ['1,2,3'])])


def buildConfig(**overrides):
    config = {
        'influxDb': {},
        'hierarchyBalanceEpsilonWatts': 5.0,
        'hierarchyNegativeBalanceAbort': False,
        'hierarchyAggregateSettleSecs': 120,
    }
    config.update(overrides)
    return config


def buildValidatedConfig(account=None, **overrides):
    config = buildConfig(**overrides)
    validateHierarchy(config, account if account is not None else buildAccount())
    return config


# Which Vue device measures each node in the fixtures above. A device's mains series is
# tagged with the device's own name; a circuit's series is tagged with the name of the
# device whose channel it is, exactly as collect.extractDataPoints tags them.
NODE_DEVICES = {
    'Heat Pump': 'Main Panel',
    'Garage Feed': 'Main Panel',
    'Garage Lights': 'Garage Subpanel',
    'A Lights': 'Panel A',
    'B Lights': 'Panel B',
}


def aggregatePoints(totals, detailed='Hour', accountName='Home', timestamp=HOUR_TS):
    """One reading per node, all at the same instant, as a settled rollup would be."""
    return [Point(accountName, NODE_DEVICES.get(name, name), name, watts, timestamp, detailed)
            for name, watts in totals.items()]


HOME_TOTALS = {
    'Main Panel': 10000.0,
    'Heat Pump': 2000.0,
    'Garage Feed': 3000.0,
    'Garage Subpanel': 2800.0,
    'Garage Lights': 500.0,
    'EV Charger': 4000.0,
}


def balancesByNode(points):
    """Emitted Net Balance points, keyed by the node name they describe."""
    return {pt.chanName[:-len(hierarchy.NET_BALANCE_SUFFIX)]: pt
            for pt in points if pt.chanName.endswith(hierarchy.NET_BALANCE_SUFFIX)}


# ---------------------------------------------------------------------------
# validateHierarchy - opt in
# ---------------------------------------------------------------------------

def test_hierarchy_inactive_without_any_parent():
    account = buildAccount()
    for device in account['devices']:
        device.pop('parent', None)
    config = buildConfig()
    assert validateHierarchy(config, account) is None
    assert config['hierarchy']['Home'] is None
    assert hierarchy.isHierarchyActive(config) is False


def test_hierarchy_enabled_by_account_flag_alone():
    account = buildAccount()
    for device in account['devices']:
        device.pop('parent', None)
    account['hierarchyEnabled'] = True
    config = buildConfig()
    assert validateHierarchy(config, account) is not None
    assert hierarchy.isHierarchyActive(config) is True


def test_hierarchy_inactive_for_unnamed_account():
    account = buildAccount()
    del account['name']
    config = buildConfig()
    assert validateHierarchy(config, account) is None
    assert config['hierarchy'][None] is None


# ---------------------------------------------------------------------------
# validateHierarchy - tree construction
# ---------------------------------------------------------------------------

def test_tree_merges_device_circuit_and_explicit_edges():
    config = buildValidatedConfig()
    tree = config['hierarchy']['Home']
    nodes = tree['nodes']

    assert tree['roots'] == ['Main Panel']
    assert tree['virtualRoot'] is None

    # A circuit is implicitly contained by its device.
    assert nodes['Heat Pump'] == {'parent': 'Main Panel', 'children': [], 'kind': 'circuit',
                                  'deviceName': 'Main Panel', 'explicit': False}
    assert sorted(nodes['Main Panel']['children']) == ['EV Charger', 'Garage Feed', 'Heat Pump']

    # An explicit config edge crosses Vue devices, attaching a subpanel to a circuit.
    assert nodes['Garage Feed']['children'] == ['Garage Subpanel']
    assert nodes['Garage Subpanel']['parent'] == 'Garage Feed'
    assert nodes['Garage Subpanel']['explicit'] is True
    assert nodes['Garage Lights']['parent'] == 'Garage Subpanel'

    # The device mains channel is the device node itself, not a separate circuit.
    assert 'Main Panel-1,2,3' not in nodes
    assert nodes['Main Panel']['kind'] == 'device'


def test_tree_includes_config_only_device_not_yet_discovered():
    account = buildAccount()
    account['devices'].append({'name': 'Offline Plug', 'parent': 'Heat Pump'})
    nodes = buildValidatedConfig(account)['hierarchy']['Home']['nodes']
    assert nodes['Offline Plug']['kind'] == 'device'
    assert nodes['Heat Pump']['children'] == ['Offline Plug']


def test_unresolvable_parent_is_rejected_by_name():
    account = buildAccount()
    account['devices'][1]['parent'] = 'No Such Circuit'
    with pytest.raises(ValueError, match='parent "No Such Circuit" of "Garage Subpanel" not found'):
        validateHierarchy(buildConfig(), account)


def test_cycle_is_rejected_with_the_offending_path():
    account = buildAccount()
    # Garage Subpanel -> Garage Feed, and now Main Panel -> Garage Lights closes the loop.
    account['devices'][0]['parent'] = 'Garage Lights'
    with pytest.raises(ValueError, match='Hierarchy cycle detected'):
        validateHierarchy(buildConfig(), account)


def test_duplicate_node_name_is_rejected():
    """A name shared by a device and a channel would silently corrupt the balances."""
    account = buildAccount()
    account['devices'][0]['channels'] = ['EV Charger', 'Garage Feed']
    with pytest.raises(ValueError, match='"EV Charger" refers to both a device and a circuit'):
        validateHierarchy(buildConfig(), account)


# ---------------------------------------------------------------------------
# validateHierarchy - virtual root
# ---------------------------------------------------------------------------

def buildDualPanelAccount():
    """Two independent service mains, which Emporia cannot express a root above."""
    account = {
        'name': 'Home',
        'deviceIdMap': {},
        'channelIdMap': {},
        'hierarchyEnabled': True,
        'devices': [
            {'name': 'Panel A', 'channels': ['A Lights']},
            {'name': 'Panel B', 'channels': ['B Lights']},
        ],
    }
    return registerDevices(account, [makeDevice(100, 'Panel A', ['1,2,3', '1']),
                                     makeDevice(200, 'Panel B', ['1,2,3', '1'])])


def test_multiple_roots_synthesize_a_virtual_root():
    tree = buildValidatedConfig(buildDualPanelAccount())['hierarchy']['Home']
    assert tree['virtualRoot'] == 'Home Panel'
    assert tree['roots'] == ['Home Panel']
    assert sorted(tree['nodes']['Home Panel']['children']) == ['Panel A', 'Panel B']
    assert tree['nodes']['Panel A']['parent'] == 'Home Panel'
    assert tree['nodes']['Home Panel']['kind'] == 'virtual'


def test_virtual_root_name_collision_is_rejected():
    account = buildDualPanelAccount()
    account['devices'].append({'name': 'Home Panel'})
    with pytest.raises(ValueError, match='Cannot synthesize virtual root "Home Panel"'):
        validateHierarchy(buildConfig(), account)


# ---------------------------------------------------------------------------
# findCycle
# ---------------------------------------------------------------------------

def test_find_cycle_returns_none_for_a_tree():
    nodes = {'a': {'parent': None}, 'b': {'parent': 'a'}, 'c': {'parent': 'b'}, 'd': {'parent': 'a'}}
    assert hierarchy.findCycle(nodes) is None


def test_find_cycle_returns_the_loop():
    nodes = {'a': {'parent': 'c'}, 'b': {'parent': 'a'}, 'c': {'parent': 'b'}}
    cycle = hierarchy.findCycle(nodes)
    assert cycle[0] == cycle[-1]
    assert sorted(cycle[:-1]) == ['a', 'b', 'c']


def test_find_cycle_detects_a_loop_that_hangs_off_a_clean_chain():
    """The chain from 'x' settles first; the cycle is only reachable from 'p'."""
    nodes = {'x': {'parent': None}, 'y': {'parent': 'x'},
             'p': {'parent': 'q'}, 'q': {'parent': 'p'}}
    assert hierarchy.findCycle(nodes) is not None


def test_find_cycle_reuses_settled_nodes():
    """'c' walks into 'b', already proven acyclic, and stops there."""
    nodes = {'a': {'parent': None}, 'b': {'parent': 'a'}, 'c': {'parent': 'b'}}
    assert hierarchy.findCycle(nodes) is None


# ---------------------------------------------------------------------------
# applyBalances - math
# ---------------------------------------------------------------------------

def test_no_op_when_no_account_has_a_hierarchy():
    config = buildConfig()
    config['hierarchy'] = {'Home': None}
    points = aggregatePoints(HOME_TOTALS)
    assert hierarchy.applyBalances(config, points) is points
    assert len(points) == len(HOME_TOTALS)


def test_balance_nets_each_node_against_its_direct_children():
    config = buildValidatedConfig()
    points = aggregatePoints(HOME_TOTALS)
    hierarchy.applyBalances(config, points)
    balances = balancesByNode(points)

    assert balances['Main Panel'].usageWatts == 1000.0     # 10000 - (2000 + 3000 + 4000)
    assert balances['Garage Feed'].usageWatts == 200.0     # 3000 - 2800
    assert balances['Garage Subpanel'].usageWatts == 2300.0  # 2800 - 500

    # Leaves have nothing below them, so they get no balance series.
    assert 'Heat Pump' not in balances
    assert 'Garage Lights' not in balances
    assert 'EV Charger' not in balances


def test_balances_plus_leaf_totals_reconstruct_the_root_total():
    """The point of the feature: a decomposition that counts nothing twice."""
    config = buildValidatedConfig()
    points = aggregatePoints(HOME_TOTALS)
    hierarchy.applyBalances(config, points)

    balanceSum = sum(pt.usageWatts for pt in points if pt.chanName.endswith(hierarchy.NET_BALANCE_SUFFIX))
    leafSum = HOME_TOTALS['Heat Pump'] + HOME_TOTALS['Garage Lights'] + HOME_TOTALS['EV Charger']
    assert balanceSum + leafSum == HOME_TOTALS['Main Panel']


def test_raw_points_are_passed_through_untouched():
    config = buildValidatedConfig()
    points = aggregatePoints(HOME_TOTALS)
    original = list(points)
    hierarchy.applyBalances(config, points)
    assert points[:len(original)] == original


def test_balance_point_carries_the_owning_device_name():
    config = buildValidatedConfig()
    points = aggregatePoints(HOME_TOTALS)
    hierarchy.applyBalances(config, points)
    balances = balancesByNode(points)
    # A circuit's balance belongs to the device that measures it.
    assert balances['Garage Feed'].deviceName == 'Main Panel'
    assert balances['Garage Subpanel'].deviceName == 'Garage Subpanel'
    assert balances['Main Panel'].detailed == 'Hour'
    assert balances['Main Panel'].timestamp == HOUR_TS


def test_missing_child_contributes_zero():
    """Nothing was itemized below the node, so all of its usage is its own balance."""
    config = buildValidatedConfig()
    totals = dict(HOME_TOTALS)
    del totals['Garage Subpanel']
    points = aggregatePoints(totals)
    hierarchy.applyBalances(config, points)
    assert balancesByNode(points)['Garage Feed'].usageWatts == 3000.0


def test_same_channel_name_on_another_device_does_not_clobber_a_total():
    """Readings are indexed by (device, channel), so a same-named channel elsewhere
    in the account cannot overwrite a node's total and skew its balance."""
    config = buildValidatedConfig()
    points = aggregatePoints(HOME_TOTALS)
    # Another panel, outside the tree, that also calls one of its circuits 'Garage Feed'.
    points.append(Point('Home', 'Shop Panel', 'Garage Feed', 99999.0, HOUR_TS, 'Hour'))
    hierarchy.applyBalances(config, points)
    balances = balancesByNode(points)

    assert balances['Garage Feed'].usageWatts == 200.0     # 3000 - 2800, as if the other panel did not exist
    assert balances['Main Panel'].usageWatts == 1000.0     # 10000 - (2000 + 3000 + 4000)


def test_node_without_its_own_total_is_skipped():
    config = buildValidatedConfig()
    totals = dict(HOME_TOTALS)
    del totals['Garage Feed']
    points = aggregatePoints(totals)
    hierarchy.applyBalances(config, points)
    assert 'Garage Feed' not in balancesByNode(points)


def test_daily_aggregates_are_balanced_too():
    config = buildValidatedConfig()
    points = aggregatePoints(HOME_TOTALS, detailed='Day', timestamp=DAY_TS)
    hierarchy.applyBalances(config, points)
    balance = balancesByNode(points)['Main Panel']
    assert balance.detailed == 'Day'
    assert balance.timestamp == DAY_TS


def test_each_timestamp_is_balanced_independently():
    config = buildValidatedConfig()
    laterTs = HOUR_TS + datetime.timedelta(hours=1)
    points = aggregatePoints(HOME_TOTALS) + aggregatePoints(HOME_TOTALS, timestamp=laterTs)
    hierarchy.applyBalances(config, points)
    emitted = [pt for pt in points if pt.chanName == 'Main Panel' + hierarchy.NET_BALANCE_SUFFIX]
    assert sorted(pt.timestamp for pt in emitted) == [HOUR_TS, laterTs]


def test_minute_and_second_points_are_ignored():
    """Minute points are not time aligned across devices, so they are out of scope."""
    config = buildValidatedConfig()
    points = aggregatePoints(HOME_TOTALS, detailed='False') + aggregatePoints(HOME_TOTALS, detailed='True')
    hierarchy.applyBalances(config, points)
    assert balancesByNode(points) == {}


def test_points_from_an_account_without_a_hierarchy_are_ignored():
    config = buildValidatedConfig()
    config['hierarchy']['Other'] = None
    points = aggregatePoints(HOME_TOTALS, accountName='Other')
    hierarchy.applyBalances(config, points)
    assert balancesByNode(points) == {}


# ---------------------------------------------------------------------------
# applyBalances - virtual root
# ---------------------------------------------------------------------------

def test_virtual_root_emits_the_whole_account_rollup():
    config = buildValidatedConfig(buildDualPanelAccount())
    points = aggregatePoints({'Panel A': 4000.0, 'A Lights': 1000.0, 'Panel B': 6000.0, 'B Lights': 2000.0})
    hierarchy.applyBalances(config, points)
    rollup = [pt for pt in points if pt.chanName == 'Home Panel']
    assert len(rollup) == 1
    assert rollup[0].usageWatts == 10000.0
    assert rollup[0].deviceName == 'Home Panel'
    # It has no measured total of its own, so it gets no balance series.
    assert 'Home Panel' not in balancesByNode(points)


def test_virtual_root_is_skipped_when_no_root_reported():
    config = buildValidatedConfig(buildDualPanelAccount())
    points = aggregatePoints({'A Lights': 1000.0})
    hierarchy.applyBalances(config, points)
    assert [pt for pt in points if pt.chanName == 'Home Panel'] == []


# ---------------------------------------------------------------------------
# applyBalances - negative handling
# ---------------------------------------------------------------------------

def negativeTotals():
    """Garage Feed measures less than the subpanel it feeds: a wrong tree, or noise."""
    totals = dict(HOME_TOTALS)
    totals['Garage Feed'] = 2000.0  # child reads 2800
    return totals


def test_negative_balance_is_clamped_and_warned(caplog):
    config = buildValidatedConfig()
    points = aggregatePoints(negativeTotals())
    with caplog.at_level('WARNING', logger='vuegraf.hierarchy'):
        hierarchy.applyBalances(config, points)
    assert balancesByNode(points)['Garage Feed'].usageWatts == 0.0
    assert 'Negative net balance for node "Garage Feed"' in caplog.text


def test_small_negative_within_epsilon_is_silently_clamped(caplog):
    config = buildValidatedConfig()
    totals = dict(HOME_TOTALS)
    totals['Garage Feed'] = 3298.0  # -2W against a 3300W child; rounding noise
    totals['Garage Subpanel'] = 3300.0
    points = aggregatePoints(totals)
    with caplog.at_level('WARNING', logger='vuegraf.hierarchy'):
        hierarchy.applyBalances(config, points)
    assert balancesByNode(points)['Garage Feed'].usageWatts == 0.0
    assert 'Negative net balance' not in caplog.text


def test_negative_balance_can_abort_the_cycle():
    config = buildValidatedConfig(hierarchyNegativeBalanceAbort=True)
    points = aggregatePoints(negativeTotals())
    with pytest.raises(ValueError, match='Negative net balance for node "Garage Feed"'):
        hierarchy.applyBalances(config, points)


def test_repeat_negative_warnings_are_throttled_and_summarized(caplog):
    """A backfill evaluates the same bad node once per hour of history."""
    config = buildValidatedConfig()
    points = []
    for hours in range(5):
        points.extend(aggregatePoints(negativeTotals(), timestamp=HOUR_TS + datetime.timedelta(hours=hours)))
    with caplog.at_level('INFO', logger='vuegraf.hierarchy'):
        hierarchy.applyBalances(config, points)

    warnings = [r for r in caplog.records if r.levelname == 'WARNING']
    assert len(warnings) == 1
    assert 'Suppressed 4 repeat negative net balance warnings' in caplog.text


def test_negative_warning_returns_after_the_throttle_expires(caplog):
    config = buildValidatedConfig()
    tree = config['hierarchy']['Home']
    hierarchy.applyBalances(config, aggregatePoints(negativeTotals()))

    tree['negativeWarnExpiry']['Garage Feed'] = 0.0  # pretend the TTL elapsed
    with caplog.at_level('WARNING', logger='vuegraf.hierarchy'):
        hierarchy.applyBalances(config, aggregatePoints(negativeTotals()))
    assert 'Negative net balance for node "Garage Feed"' in caplog.text


# ---------------------------------------------------------------------------
# registerImplicitEdge
# ---------------------------------------------------------------------------

def test_implicit_edge_is_ignored_when_hierarchy_is_inactive():
    config = buildConfig()
    config['hierarchy'] = {'Home': None}
    hierarchy.registerImplicitEdge(config, 'Home', 'Some Plug', 'Heat Pump')  # must not raise
    assert config['hierarchy']['Home'] is None


def buildPlugAccount():
    """A plug that Emporia nests under a circuit, which is visible only in usage data."""
    account = {
        'name': 'Home',
        'deviceIdMap': {},
        'channelIdMap': {},
        'hierarchyEnabled': True,
        'devices': [
            {'name': 'Main Panel', 'channels': ['Heat Pump', 'Media Circuit']},
            {'name': 'TV Console'},
        ],
    }
    return registerDevices(account, [makeDevice(100, 'Main Panel', ['1,2,3', '1', '2']),
                                     makeDevice(300, 'TV Console', ['1,2,3'])])


def test_implicit_edge_attaches_a_plug_to_the_circuit_that_feeds_it():
    config = buildValidatedConfig(buildPlugAccount())
    nodes = config['hierarchy']['Home']['nodes']
    assert nodes['TV Console']['parent'] == 'Home Panel'  # a top-level node until Emporia says otherwise

    hierarchy.registerImplicitEdge(config, 'Home', 'TV Console', 'Media Circuit')
    assert nodes['TV Console']['parent'] == 'Media Circuit'
    assert nodes['Media Circuit']['children'] == ['TV Console']
    # Detached from its previous parent, so it is not counted twice.
    assert nodes['Home Panel']['children'] == ['Main Panel']


def test_implicit_edge_registers_a_device_emporia_did_not_list():
    config = buildValidatedConfig()
    nodes = config['hierarchy']['Home']['nodes']
    hierarchy.registerImplicitEdge(config, 'Home', 'Hidden Plug', 'Heat Pump')
    assert nodes['Hidden Plug'] == {'parent': 'Heat Pump', 'children': [], 'kind': 'device',
                                    'deviceName': 'Hidden Plug', 'explicit': False}
    assert nodes['Heat Pump']['children'] == ['Hidden Plug']


def test_implicit_edge_is_idempotent_across_collection_cycles():
    config = buildValidatedConfig()
    nodes = config['hierarchy']['Home']['nodes']
    for _ in range(3):
        hierarchy.registerImplicitEdge(config, 'Home', 'Garage Subpanel', 'Garage Feed')
    assert nodes['Garage Feed']['children'] == ['Garage Subpanel']


def test_implicit_edge_with_unknown_parent_is_ignored():
    config = buildValidatedConfig()
    nodes = config['hierarchy']['Home']['nodes']
    hierarchy.registerImplicitEdge(config, 'Home', 'EV Charger', 'Unknown Circuit')
    assert nodes['EV Charger']['parent'] == 'Main Panel'
    assert 'Unknown Circuit' not in nodes


def test_explicit_config_edge_wins_over_a_conflicting_nested_edge(caplog):
    config = buildValidatedConfig()
    nodes = config['hierarchy']['Home']['nodes']
    with caplog.at_level('WARNING', logger='vuegraf.hierarchy'):
        hierarchy.registerImplicitEdge(config, 'Home', 'Garage Subpanel', 'Heat Pump')
        hierarchy.registerImplicitEdge(config, 'Home', 'Garage Subpanel', 'Heat Pump')
    assert nodes['Garage Subpanel']['parent'] == 'Garage Feed'
    assert len([r for r in caplog.records if r.levelname == 'WARNING']) == 1
    assert 'config parent "Garage Feed" overrides' in caplog.text


def test_implicit_edge_that_would_create_a_cycle_is_refused(caplog):
    config = buildValidatedConfig()
    nodes = config['hierarchy']['Home']['nodes']
    with caplog.at_level('WARNING', logger='vuegraf.hierarchy'):
        hierarchy.registerImplicitEdge(config, 'Home', 'Main Panel', 'Garage Lights')
        hierarchy.registerImplicitEdge(config, 'Home', 'Main Panel', 'Garage Lights')
    assert nodes['Main Panel']['parent'] is None
    assert len([r for r in caplog.records if r.levelname == 'WARNING']) == 1
    assert 'would create a cycle' in caplog.text


def test_implicit_edge_re_parents_a_parentless_node_without_raising():
    """A validated tree has one parentless node and re-parenting it is refused as a
    cycle, so this only bites a tree that lost that invariant -- it must not KeyError
    in the middle of a collection cycle."""
    config = buildValidatedConfig(buildDualPanelAccount())
    nodes = config['hierarchy']['Home']['nodes']
    # Detach a leaf entirely, leaving a second parentless node behind.
    nodes['Panel B']['children'].remove('B Lights')
    nodes['B Lights']['parent'] = None

    hierarchy.registerImplicitEdge(config, 'Home', 'B Lights', 'Panel A')

    assert nodes['B Lights']['parent'] == 'Panel A'
    assert 'B Lights' in nodes['Panel A']['children']


def test_implicit_edge_keeps_the_virtual_rollup_from_double_counting():
    """Re-parenting a root under another panel must remove it from the virtual root."""
    config = buildValidatedConfig(buildDualPanelAccount())
    nodes = config['hierarchy']['Home']['nodes']
    hierarchy.registerImplicitEdge(config, 'Home', 'Panel B', 'A Lights')

    assert nodes['Home Panel']['children'] == ['Panel A']
    points = aggregatePoints({'Panel A': 4000.0, 'A Lights': 3000.0, 'Panel B': 2000.0, 'B Lights': 500.0})
    hierarchy.applyBalances(config, points)
    rollup = [pt for pt in points if pt.chanName == 'Home Panel']
    assert rollup[0].usageWatts == 4000.0  # Panel B is inside Panel A now, not beside it


# ---------------------------------------------------------------------------
# isAggregateSettled
# ---------------------------------------------------------------------------

def test_aggregate_always_settled_without_a_hierarchy():
    config = buildConfig()
    config['hierarchy'] = {'Home': None}
    assert hierarchy.isAggregateSettled(config, HOUR_TS, HOUR_TS) is True


def test_aggregate_not_settled_until_the_lag_elapses():
    config = buildValidatedConfig(hierarchyAggregateSettleSecs=120)
    assert hierarchy.isAggregateSettled(config, HOUR_TS, HOUR_TS + datetime.timedelta(seconds=119)) is False
    assert hierarchy.isAggregateSettled(config, HOUR_TS, HOUR_TS + datetime.timedelta(seconds=120)) is True


def test_settle_lag_is_capped_so_it_cannot_starve_the_rollover():
    config = buildValidatedConfig(hierarchyAggregateSettleSecs=99999)
    now = HOUR_TS + datetime.timedelta(seconds=hierarchy.MAX_SETTLE_SECS)
    assert hierarchy.isAggregateSettled(config, HOUR_TS, now) is True


def test_balances_are_emitted_on_a_victoria_metrics_only_install():
    """The balance pass reads its Hour/Day tags from the configured destination.

    Only hourly and daily aggregates are netted, so the pass has to recognise those
    tags -- which are per-destination settings. On a VictoriaMetrics-only install
    there is no influxDb section to read them from.
    """
    account = buildAccount()
    config = buildConfig(victoriaMetrics={'url': 'http://vm:8428'})
    del config['influxDb']
    validateHierarchy(config, account)

    points = aggregatePoints(HOME_TOTALS)
    hierarchy.applyBalances(config, points)

    balances = {pt.chanName: pt.usageWatts for pt in points if pt.chanName.endswith('Net Balance')}
    assert balances['Main Panel Net Balance'] == 1000.0  # 10000 - 2000 - 3000 - 4000


def test_a_renamed_victoria_metrics_hour_tag_is_recognised():
    account = buildAccount()
    config = buildConfig(victoriaMetrics={'url': 'http://vm:8428', 'tagValue_hour': 'hourly'})
    del config['influxDb']
    validateHierarchy(config, account)

    points = aggregatePoints(HOME_TOTALS, detailed='hourly')
    hierarchy.applyBalances(config, points)

    assert any(pt.chanName == 'Main Panel Net Balance' for pt in points)
