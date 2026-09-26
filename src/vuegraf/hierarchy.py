# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

# Device hierarchy and per-level Net Balance. Opt-in, per account.
#
# Emporia lets devices be nested -- a smart plug under a circuit or directly under a
# panel, a subpanel fed from a circuit -- so an installation is a tree, while Vuegraf
# records flat series. When an account opts in, this module builds that tree once at
# startup and derives, from each batch of hourly and daily readings, a
# '<node> Net Balance' series for every panel and for every circuit with something
# beneath it: the part of that node's usage not itemized by anything below it. Summing
# the Net Balances and the leaf readings of a subtree gives its root's total, with
# nothing counted twice. Raw readings are never modified; only new series are added.
#
# How Emporia reports nesting, as measured against a live account (the recorded day in
# the tests is the evidence):
#
#   * getDeviceListUsages, which live collection uses, deducts a nested device from the
#     circuit it hangs under, floored at zero. It does so only when the nested device is
#     part of the same request, which it always is for Vuegraf. A device's mains is never
#     deducted. Its 'Balance' is the mains minus its raw circuits minus any device nested
#     directly on the mains, and is not floored.
#   * getChartUsage, which --historydays uses, reports every channel raw and returns no
#     Balance.
#
# A live batch therefore already holds Emporia's own remainder for every node Emporia
# knows about, and a history batch is first brought to that same form. Links that exist
# only in the Vuegraf config are unknown to Emporia, so they are subtracted here for
# both kinds of batch.

from dataclasses import dataclass, field
import logging
import time
from types import SimpleNamespace

from vuegraf.device import lookupChannelName, lookupDeviceName


logger = logging.getLogger('vuegraf.hierarchy')

NET_BALANCE_SUFFIX = ' Net Balance'

# Where a batch of readings came from; see the module notes for why it matters.
SOURCE_LIVE = 'live'
SOURCE_HISTORY = 'history'

DEVICE = 'device'
CIRCUIT = 'circuit'
VIRTUAL = 'virtual'

MAINS_CHANNEL = '1,2,3'
BALANCE_CHANNEL = 'Balance'

# A node that keeps reading short is a standing condition, so it is reported at most this often.
WARN_INTERVAL_SECS = 3600


@dataclass
class Node:
    """One device, circuit or virtual grouping in an account's tree."""
    name: str                   # display name; the prefix of its Net Balance series
    kind: str                   # DEVICE, CIRCUIT or VIRTUAL
    deviceName: str             # series deviceName for this node's reading and its Net Balance
    readingKey: tuple = None    # (deviceName, chanName) of the node's own reading; None for VIRTUAL
    balanceKey: tuple = None    # DEVICE only: the series Emporia's Balance channel is written to
    parent: tuple = None        # node id of the parent, or None at the top
    emporiaEdge: bool = False   # Emporia knows the link to the parent, so live data already deducts it
    circuits: list = field(default_factory=list)  # DEVICE only: ids of its own numbered channels
    children: list = field(default_factory=list)  # ids of devices (or virtual nodes) attached beneath


@dataclass
class Hierarchy:
    nodes: dict                 # node id -> Node
    accountRoot: tuple = None   # id of the synthesized whole-account node, if there is one
    warnedUntil: dict = field(default_factory=dict)  # node id -> monotonic time its warning is quiet until
    missingParts: set = field(default_factory=set)   # parts of a virtual total that last reported nothing


def isHierarchyConfigured(account):
    """True when an account opts in: "hierarchyEnabled": true, or any device declaring a
    'parent' or marked 'virtual'."""
    if account.get('hierarchyEnabled') is True:
        return True
    return any('parent' in device or device.get('virtual') for device in account.get('devices') or [])


def deviceId(gid):
    return (DEVICE, gid)


def circuitId(gid, channelNum):
    return (CIRCUIT, gid, channelNum)


def virtualId(name):
    return (VIRTUAL, name)


def _validateConfigEntries(accountName, configDevices):
    seen = set()
    for entry in configDevices:
        name = entry.get('name')
        if not isinstance(name, str) or not name:
            raise ValueError('Hierarchy config error in account "{}": every device entry needs a name'.format(accountName))
        if name in seen:
            raise ValueError('Hierarchy config error in account "{}": device "{}" is listed more than once'.format(
                             accountName, name))
        seen.add(name)
        if 'parent' in entry and (not isinstance(entry['parent'], str) or not entry['parent']):
            raise ValueError('Hierarchy config error in account "{}": "parent" of "{}" must be a non-empty string'.format(
                             accountName, name))
        if entry.get('parent') == name:
            raise ValueError('Hierarchy config error in account "{}": "{}" cannot be its own parent'.format(accountName, name))
        if 'virtual' in entry and not isinstance(entry['virtual'], bool):
            raise ValueError('Hierarchy config error in account "{}": "virtual" of "{}" must be true or false'.format(
                             accountName, name))
        if entry.get('virtual') and 'channels' in entry:
            raise ValueError('Hierarchy config error in account "{}": virtual device "{}" cannot have channels'.format(
                             accountName, name))


def _attach(nodes, childId, parentId, emporiaEdge):
    child = nodes[childId]
    child.parent = parentId
    child.emporiaEdge = emporiaEdge
    nodes[parentId].children.append(childId)


def _describe(nodes, nodeId):
    node = nodes[nodeId]
    if node.kind == CIRCUIT:
        return 'circuit "{}" of "{}"'.format(node.name, node.deviceName)
    return '{} "{}"'.format(node.kind, node.name)


def buildHierarchy(account):
    """Builds and validates the tree for an account that opted in; None otherwise.

    Called once, after device discovery. Emporia's own nesting comes from get_devices(),
    where every nested device names its parent device and parent channel. The config adds
    links Emporia does not know about -- a subpanel wired from a circuit on another Vue,
    or a plug the app does not nest -- and virtual nodes, such as one grouping the panels
    of a split-feed service. A config 'parent' may name a device, a circuit, or a virtual
    node.

    Raises ValueError, naming what to fix, when a parent does not resolve, is ambiguous,
    contradicts the nesting Emporia reports, or closes a cycle. A config link that
    disagrees with Emporia is refused rather than preferred, because Emporia deducts
    according to its own nesting regardless, and no balance could then be right.
    """
    if not isHierarchyConfigured(account):
        return None

    accountName = account.get('name')
    configDevices = account.get('devices') or []
    _validateConfigEntries(accountName, configDevices)

    nodes = {}
    for gid, device in account['deviceIdMap'].items():
        name = lookupDeviceName(account, gid)
        mains = SimpleNamespace(device_gid=gid, channel_num=MAINS_CHANNEL)
        balance = SimpleNamespace(device_gid=gid, channel_num=BALANCE_CHANNEL)
        nodes[deviceId(gid)] = Node(name, DEVICE, name, readingKey=(name, lookupChannelName(account, mains)),
                                    balanceKey=(name, lookupChannelName(account, balance)))

    for chan in account['channelIdMap'].values():
        if deviceId(chan.device_gid) not in nodes:
            continue
        try:
            int(chan.channel_num)
        except (TypeError, ValueError):
            continue  # the mains and derived channels are not circuits
        devName = lookupDeviceName(account, chan.device_gid)
        chanName = lookupChannelName(account, chan)
        cid = circuitId(chan.device_gid, chan.channel_num)
        nodes[cid] = Node(chanName, CIRCUIT, devName, readingKey=(devName, chanName), parent=deviceId(chan.device_gid))
        nodes[deviceId(chan.device_gid)].circuits.append(cid)

    for entry in configDevices:
        if entry.get('virtual'):
            if any(node.name == entry['name'] for node in nodes.values()):
                raise ValueError('Hierarchy config error in account "{}": virtual device "{}" has the name of a real '
                                 'device or circuit; choose another name'.format(accountName, entry['name']))
            nodes[virtualId(entry['name'])] = Node(entry['name'], VIRTUAL, entry['name'])

    # Emporia's nesting.
    for gid, device in account['deviceIdMap'].items():
        parentGid = getattr(device, 'parent_device_gid', None)
        if not parentGid:
            continue
        if deviceId(parentGid) not in nodes:
            logger.warning('Device "{}" is nested under device {}, which this account does not list; treating it as '
                           'top level'.format(nodes[deviceId(gid)].name, parentGid))
            continue
        parentChannel = getattr(device, 'parent_channel_num', None)
        parentId = deviceId(parentGid)
        if parentChannel and parentChannel != MAINS_CHANNEL:
            if circuitId(parentGid, parentChannel) in nodes:
                parentId = circuitId(parentGid, parentChannel)
            else:
                logger.warning('Device "{}" is nested under channel {} of "{}", which was not discovered; attaching it '
                               'to that device instead'.format(nodes[deviceId(gid)].name, parentChannel,
                                                               nodes[parentId].name))
        _attach(nodes, deviceId(gid), parentId, emporiaEdge=True)

    # Config links.
    byName = {}
    for nodeId, node in nodes.items():
        byName.setdefault(node.name, []).append(nodeId)

    def resolve(name, role):
        matches = byName.get(name, [])
        if not matches:
            raise ValueError('Hierarchy config error in account "{}": {} "{}" is not a known device, circuit or '
                             'virtual device'.format(accountName, role, name))
        if len(matches) > 1:
            raise ValueError('Hierarchy config error in account "{}": {} "{}" is ambiguous; it names {}. Rename one of '
                             'them so the name is unique'.format(accountName, role, name,
                                                                 ' and '.join(_describe(nodes, m) for m in matches)))
        return matches[0]

    for entry in configDevices:
        if 'parent' not in entry:
            continue
        childMatches = [m for m in byName.get(entry['name'], []) if m[0] in (DEVICE, VIRTUAL)]
        if not childMatches:
            logger.warning('Hierarchy: configured device "{}" was not discovered in account "{}"; ignoring its parent'.format(
                           entry['name'], accountName))
            continue
        if len(childMatches) > 1:
            raise ValueError('Hierarchy config error in account "{}": "{}" is ambiguous; it names {}. Rename one of them '
                             'in the Emporia app so the name is unique'.format(
                                 accountName, entry['name'], ' and '.join(_describe(nodes, m) for m in childMatches)))
        childId = childMatches[0]
        parentId = resolve(entry['parent'], 'parent')
        if childId[0] == VIRTUAL and parentId[0] != VIRTUAL:
            raise ValueError('Hierarchy config error in account "{}": virtual device "{}" can only be placed under '
                             'another virtual device'.format(accountName, entry['name']))
        child = nodes[childId]
        if child.emporiaEdge:
            if child.parent != parentId:
                raise ValueError('Hierarchy config error in account "{}": config places "{}" under {}, but Emporia nests '
                                 'it under {}. Emporia deducts by its own nesting, so change the nesting in the Emporia '
                                 'app or remove this "parent"'.format(accountName, entry['name'],
                                                                      _describe(nodes, parentId),
                                                                      _describe(nodes, child.parent)))
            continue  # the config restates what Emporia already knows
        _attach(nodes, childId, parentId, emporiaEdge=False)

    for nodeId in nodes:
        seen = [nodeId]
        parentId = nodes[nodeId].parent
        while parentId is not None:
            if parentId in seen:
                cycle = seen[seen.index(parentId):] + [parentId]
                raise ValueError('Hierarchy config error in account "{}": parents form a cycle: {}'.format(
                                 accountName, ' -> '.join(nodes[n].name for n in cycle)))
            seen.append(parentId)
            parentId = nodes[parentId].parent

    for nodeId, node in nodes.items():
        if node.kind == VIRTUAL and not node.children:
            logger.warning('Hierarchy: virtual device "{}" in account "{}" has nothing under it'.format(node.name, accountName))

    hierarchy = Hierarchy(nodes, accountRoot=_synthesizeAccountRoot(account, nodes))
    _checkSeriesNames(accountName, hierarchy)
    logger.info('Hierarchy built; account="{}"; devices={}; circuits={}; virtual={}; linked={}'.format(
                accountName, sum(1 for n in nodes.values() if n.kind == DEVICE),
                sum(1 for n in nodes.values() if n.kind == CIRCUIT),
                sum(1 for n in nodes.values() if n.kind == VIRTUAL),
                sum(1 for n in nodes.values() if n.kind != CIRCUIT and n.parent is not None)))
    return hierarchy


def _synthesizeAccountRoot(account, nodes):
    """Adds '<account> Panel' above the top-level panels when there is more than one.

    Independent service feeds, such as the two panels of a split service, have no meter above
    them, so this node's total is the account's whole-home figure. A panel counts as
    top level when nothing -- Emporia or the config -- places it beneath another; a
    top-level virtual device counts too. Smart plugs left at the top are not included:
    they draw from some circuit that nothing identifies, so adding them would count them
    twice. Whether a panel is reporting is decided period by period, when the total is
    computed, not here. Returns the node id, or None when there is at most one top-level
    panel.
    """
    accountName = account.get('name')
    roots, unplaced = [], []
    for nodeId, node in nodes.items():
        if node.parent is not None or node.kind == CIRCUIT:
            continue
        if node.kind == DEVICE and not node.circuits:
            unplaced.append(node.name)
        else:
            roots.append(nodeId)
    if len(roots) < 2:
        return None
    name = '{} Panel'.format(accountName)
    if any(node.name == name for node in nodes.values()):
        raise ValueError('Hierarchy config error in account "{}": cannot add the whole-account total "{}", because that '
                         'name is already in use; rename that device, or group the panels under a virtual device of '
                         'your own'.format(accountName, name))
    rootId = virtualId(name)
    nodes[rootId] = Node(name, VIRTUAL, name)
    for nodeId in roots:
        _attach(nodes, nodeId, rootId, emporiaEdge=False)
    logger.info('Hierarchy: "{}" totals {} top-level panels in account "{}"{}'.format(
                name, len(roots), accountName,
                '; left out, as unplaced: {}'.format(', '.join(sorted(unplaced))) if unplaced else ''))
    return rootId


def derivedSeriesKey(node):
    """The (deviceName, chanName) a node's derived reading is written to, or None if it has none.

    A virtual node's derived reading is its total, written like a device's mains. Every
    device with circuits or children, and every circuit with children, gets a Net Balance.
    """
    if node.kind == VIRTUAL:
        return (node.name, node.name) if node.children else None
    if node.children or node.circuits:
        return (node.deviceName, node.name + NET_BALANCE_SUFFIX)
    return None


def _checkSeriesNames(accountName, hierarchy):
    """Refuses a tree in which two readings would be written to the same series.

    Readings are matched to nodes by (device name, channel name), so two circuits of one
    panel given the same name, or two devices with the same name, cannot be told apart:
    one reading would silently stand in for both. The same goes for a derived series that
    would land on an existing one.
    """
    raw = {}
    for nodeId in sorted(hierarchy.nodes, key=str):
        node = hierarchy.nodes[nodeId]
        for key in (node.readingKey, node.balanceKey):
            if key is None:
                continue
            if key in raw and raw[key] != nodeId:
                raise ValueError('Hierarchy config error in account "{}": {} and {} would both be written as "{}" / "{}", '
                                 'so their readings cannot be told apart; rename one of them, in the Emporia app or in '
                                 'its "channels"'.format(accountName, _describe(hierarchy.nodes, raw[key]),
                                                         _describe(hierarchy.nodes, nodeId), key[0], key[1]))
            raw[key] = nodeId
    claimed = {}
    for node in hierarchy.nodes.values():
        key = derivedSeriesKey(node)
        if key is None:
            continue
        if key in raw or key in claimed:
            raise ValueError('Hierarchy config error in account "{}": the derived series "{}" / "{}" for "{}" would collide '
                             'with an existing series; rename the device or circuit'.format(
                                 accountName, key[0], key[1], node.name))
        claimed[key] = node.name


class NegativeBalanceError(ValueError):
    """A node read less than what is beneath it, with hierarchyNegativeBalanceAbort set."""


def computeNetBalances(hierarchy, readings, source, epsilon=0.0, abort=False):
    """Derives the Net Balance (and virtual total) readings for one aligned batch.

    readings maps (deviceName, chanName) to a value -- every reading collected for one
    period at one resolution -- and source says which endpoint produced them. Returns a
    new {(deviceName, chanName): value} map of derived readings; the input is untouched.
    Any unit works, as long as epsilon is in the same one.

    A node with no reading of its own gets no derived reading, but whatever reported
    beneath it still comes off its parent; a child with nothing reporting at all counts as
    zero, as nothing of the parent was itemized by it. A virtual total is the sum
    of the parts that reported, and is only skipped when none did; a part that stops
    reporting is noted once, and again when it comes back.

    When a node reads less than what is beneath it by more than epsilon, the tree or a
    meter is wrong, and summing leaves and Net Balances will not give the root's total.
    That is reported (at most hourly per node), or raised as NegativeBalanceError when
    abort is set. A circuit's Net Balance is still floored at zero, since Emporia floors
    the circuit itself in live data and the true shortfall is only visible as the gap it
    leaves in the panel's Balance; a panel's is not, as Emporia's Balance is not.
    """
    nodes = hierarchy.nodes
    shortfalls = []  # (node id, amount, what is wrong, what to check)

    missing = set()

    def total(nodeId):
        node = nodes[nodeId]
        if node.kind == VIRTUAL:
            parts = {childId: total(childId) for childId in node.children}
            missing.update(childId for childId, part in parts.items() if part is None)
            reported = [part for part in parts.values() if part is not None]
            return sum(reported) if reported else None
        return readings.get(node.readingKey)

    def deducted(childId):
        """What Emporia takes off a parent for this child: its own reading, if it has one."""
        return total(childId) or 0.0

    def known(nodeId):
        """A node's usage as far as the readings show it: its own reading or, when it has
        none, whatever reported beneath it. That still has to come off its parent, or it
        would be counted there and again as its own leaves."""
        node = nodes[nodeId]  # a device or circuit: a virtual node is never beneath one
        reading = readings.get(node.readingKey)
        if node.kind == CIRCUIT:
            if reading is None:
                return sum(known(childId) for childId in node.children)
            if source == SOURCE_LIVE:  # recover the circuit's own reading from Emporia's net one
                reading += sum(deducted(childId) for childId in node.children if nodes[childId].emporiaEdge)
            return reading
        if reading is not None:
            return reading
        return sum(known(childId) for childId in node.circuits + node.children)

    def notYetDeducted(childIds):
        """The part of these children's usage Emporia has not already taken off their parent."""
        return sum(known(childId) - (deducted(childId) if nodes[childId].emporiaEdge else 0.0) for childId in childIds)

    def circuitNetBalance(nodeId, node):
        reading = readings.get(node.readingKey)
        if reading is None:
            return None
        if source == SOURCE_HISTORY:
            remainder = reading - sum(known(childId) for childId in node.children)
        else:  # live readings arrive with what Emporia nests beneath them already taken off
            remainder = reading - notYetDeducted(node.children)
        if remainder < -epsilon:
            shortfalls.append((nodeId, -remainder, 'reads less than the devices beneath it', ''))
        return max(remainder, 0.0)

    def rebuiltBalance(node):
        """Emporia's Balance, computed from the readings: the mains less each circuit's own
        reading (live circuits are net of what Emporia nests beneath them, so that is added
        back) and less what Emporia nests on the mains."""
        mains = readings.get(node.readingKey)
        if mains is None:
            return None
        circuits = 0.0
        for cid in node.circuits:
            circuit = nodes[cid]
            reading = readings.get(circuit.readingKey)
            if reading is None:
                continue  # a circuit that reported nothing is left out of Emporia's Balance too
            if source == SOURCE_LIVE:
                reading += sum(deducted(childId) for childId in circuit.children if nodes[childId].emporiaEdge)
            circuits += reading
        return mains - circuits - sum(deducted(childId) for childId in node.children if nodes[childId].emporiaEdge)

    def deviceNetBalance(nodeId, node):
        balance = rebuiltBalance(node)
        reported = readings.get(node.balanceKey) if source == SOURCE_LIVE else None
        if reported is not None:
            # Emporia computes Balance from each circuit's own reading. Rebuilding it from
            # the net readings comes out lower by exactly what Emporia floored away where a
            # circuit reads less than what it nests.
            if balance is not None and reported - balance > epsilon:
                floored = [nodes[c].name for c in node.circuits if readings.get(nodes[c].readingKey) == 0.0 and
                           sum(deducted(childId) for childId in nodes[c].children if nodes[childId].emporiaEdge) > 0]
                shortfalls.append((nodeId, reported - balance, 'has circuits reading less than the devices nested beneath '
                                   'them ({})'.format(', '.join(floored) or 'unidentified'), ''))
            balance = reported
        if balance is None:
            return None
        # A circuit that reported nothing is left out of the Balance, so whatever reported
        # beneath it is still inside the remainder, and is taken out here.
        unread = [childId for cid in node.circuits if readings.get(nodes[cid].readingKey) is None
                  for childId in nodes[cid].children]
        value = balance - notYetDeducted(node.children) - sum(known(childId) for childId in unread)
        if value < -epsilon:
            configured = [nodes[c].name for c in node.children if not nodes[c].emporiaEdge]
            shortfalls.append((nodeId, -value, 'reads less than its circuits and the devices on its mains',
                               '; check that {} is fed from the mains, not from one of its circuits'.format(', '.join(configured))
                               if configured else ''))
        return value

    derived = {}
    for nodeId, node in nodes.items():
        key = derivedSeriesKey(node)
        if key is None:
            continue
        if node.kind == VIRTUAL:
            value = total(nodeId)
        elif node.kind == CIRCUIT:
            value = circuitNetBalance(nodeId, node)
        else:
            value = deviceNetBalance(nodeId, node)
        if value is not None:
            derived[key] = value

    for nodeId in sorted(missing - hierarchy.missingParts):
        logger.warning('Net Balance: "{}" reported nothing, so the total "{}" leaves it out until it does'.format(
                       nodes[nodeId].name, nodes[nodes[nodeId].parent].name))
    for nodeId in sorted(hierarchy.missingParts - missing):
        logger.info('Net Balance: "{}" is reporting again and is back in the total "{}"'.format(
                    nodes[nodeId].name, nodes[nodes[nodeId].parent].name))
    hierarchy.missingParts = missing

    messages = ['"{}" {} by {:.1f}{}'.format(nodes[nodeId].name, why, amount, hint) for nodeId, amount, why, hint in shortfalls]
    if messages and abort:
        raise NegativeBalanceError('Net Balance is negative, so the period is not recorded: ' + '; '.join(messages))
    now = time.monotonic()
    for (nodeId, _, _, _), message in zip(shortfalls, messages):
        if hierarchy.warnedUntil.get(nodeId, 0.0) <= now:
            hierarchy.warnedUntil[nodeId] = now + WARN_INTERVAL_SECS
            logger.warning('Net Balance: {}; totals built from this tree will not add up'.format(message))
    return derived
