# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

# Contains logic relating to the device hierarchy (panel / subpanel / circuit / plug
# tree) and the per-level "Net Balance" series derived from it.
#
# Emporia allows devices to be nested -- smart plugs under a circuit, circuits feeding
# subpanels -- so a real installation is a tree of arbitrary depth. Vuegraf otherwise
# records every device and channel as an independent, flat series, with no notion that
# one reading is physically a subset of another. Summing those series double counts any
# sub-metered branch, and Emporia's own per-device 'Balance' channel nets out only that
# one device's circuits: it does not subtract a separately measured subpanel or plug.
#
# Given a tree, this module nets each node against its direct children:
#
#     Net Balance(node) = total(node) - sum(total(child) for each direct child)
#
# which answers "how much did this node use that is not already itemized below it".
# It is emitted as a new, distinctly named series ('<node> Net Balance'); the raw
# Emporia points, including the native 'Balance' channel, are passed through
# untouched, so the feature is purely additive and loses no data.
#
# Two useful properties follow. Summing every Net Balance series plus the totals of
# every leaf node reconstructs the whole-account total with nothing counted twice,
# and each intermediate node reports its own unmetered remainder.
#
# Scope: hourly and daily aggregates only. Balance is a subtraction across nodes, so
# it is only correct when a node and all of its children are sampled at the same
# instant. Hour/day points are collected once per period rollover (or once per history
# batch) from a single get_device_list_usage call, so every device lands one point at
# the same timestamp for an already-closed period -- aligned and settled by
# construction. Minute points are produced by a per-channel path that desynchronizes
# timestamps within a cycle, so they are deliberately excluded.
#
# The feature is opt-in per account: the tree is built by device.validateHierarchy
# after discovery, but only for accounts that declare a 'parent' on a device (or set
# "hierarchyEnabled": true). Otherwise applyBalances is a no-op and nothing changes.

import logging
import time

from vuegraf.config import getConfigValue
from vuegraf.destination import getTags


logger = logging.getLogger('vuegraf.hierarchy')

# Suffix for the new balance series. Emporia's native 'Balance' channel keeps its own
# name, so dashboards opt in to the more accurate series rather than being migrated.
NET_BALANCE_SUFFIX = ' Net Balance'

# How long to stay quiet about a node that keeps producing a negative balance. A
# misconfigured tree is a standing condition, not a transient one, and a multi-year
# backfill evaluates the same node for every hour in range; without throttling that is
# one warning per node per hour of history.
NEGATIVE_BALANCE_WARN_TTL_SEC = 3600  # 1 hour

# Ceiling on hierarchyAggregateSettleSecs. Deferring a rollup by as long as its own
# period would skip periods entirely, so the configured lag is clamped well under an hour.
MAX_SETTLE_SECS = 1800


def getAccountHierarchy(config, accountName):
    """Returns the validated hierarchy for an account, or None when it is inactive."""
    return (config.get('hierarchy') or {}).get(accountName)


def isHierarchyActive(config):
    """True when at least one account has a validated hierarchy."""
    return any((config.get('hierarchy') or {}).values())


def isAggregateSettled(config, periodEndUTC, nowUTC):
    """True when a just-closed hour/day period may be collected now.

    Collection already lags naturally, since it happens on the first cycle after the
    period rolls over. When a hierarchy is active this additionally waits out
    hierarchyAggregateSettleSecs, giving Emporia time to finalize the period's averages
    before they are netted against each other. Deferring only delays collection: the
    rollover condition stays true until the period is actually collected, so the next
    cycle picks it up.

    Always True when no hierarchy is active, so the existing timing is untouched.
    """
    if not isHierarchyActive(config):
        return True
    settleSecs = min(getConfigValue(config, 'hierarchyAggregateSettleSecs'), MAX_SETTLE_SECS)
    return (nowUTC - periodEndUTC).total_seconds() >= settleSecs


def findCycle(nodes):
    """Detects a cycle in the parent pointers of a node map.

    nodes is {name: {'parent': name-or-None, ...}}, where every non-None parent is
    itself a key. Each node has at most one parent, so any cycle lies on a parent
    chain: walk each chain once, marking nodes settled as they are proven acyclic.
    Returns the cycle as a list of names with the first name repeated at the end, or
    None when the graph is acyclic.
    """
    settled = set()
    for start in nodes:
        if start in settled:
            continue
        path = []
        pathSet = set()
        name = start
        while name is not None and name not in settled:
            if name in pathSet:
                cycle = path[path.index(name):]
                return cycle + [name]
            path.append(name)
            pathSet.add(name)
            name = nodes[name]['parent']
        settled.update(path)
    return None


def _warnOnce(hierarchy, key, message):
    """Logs a hierarchy inconsistency the first time it is seen, then stays quiet."""
    if key not in hierarchy['warned']:
        hierarchy['warned'].add(key)
        logger.warning(message)


def _shouldWarnNegative(hierarchy, nodeName):
    """Rate limits the negative-balance warning for a single node. See the TTL above."""
    expiries = hierarchy['negativeWarnExpiry']
    now = time.time()
    if expiries.get(nodeName, 0.0) > now:
        return False
    expiries[nodeName] = now + NEGATIVE_BALANCE_WARN_TTL_SEC
    return True


def registerImplicitEdge(config, accountName, childName, parentName):
    """Records an Emporia-reported nested-device edge (a plug nested under a circuit).

    Called from the collection path, which is the only place these edges are visible:
    pyemvue populates channel.nested_devices from usage responses, not from
    get_devices(), so the tree cannot be fully resolved at discovery time.

    Explicit config edges win, because they are the only way to express a
    cross-Vue-device link and the user stated them deliberately; a disagreement is
    reported once and the config edge is kept. Edges that would create a cycle are
    refused. No-op when the hierarchy is inactive for the account.
    """
    hierarchy = getAccountHierarchy(config, accountName)
    if hierarchy is None:
        return

    nodes = hierarchy['nodes']
    parent = nodes.get(parentName)
    if parent is None:
        logger.debug('Ignoring nested device edge "{}" -> unknown node "{}"'.format(childName, parentName))
        return

    child = nodes.get(childName)
    if child is None:
        # A nested device Emporia did not return from get_devices(). Adding it keeps its
        # usage from being counted as unaccounted-for in the parent circuit's balance.
        child = {'parent': None, 'children': [], 'kind': 'device', 'deviceName': childName, 'explicit': False}
        nodes[childName] = child
    else:
        if child['parent'] == parentName:
            return  # already wired; the steady-state path on every subsequent cycle
        if child['explicit']:
            _warnOnce(hierarchy, (childName, parentName),
                      'Hierarchy conflict for "{}": config parent "{}" overrides Emporia nested parent "{}"'.format(
                          childName, child['parent'], parentName))
            return
        ancestorName = parentName
        while ancestorName is not None:
            if ancestorName == childName:
                _warnOnce(hierarchy, (childName, parentName),
                          'Ignoring nested device edge "{}" -> "{}": it would create a cycle'.format(
                              childName, parentName))
                return
            ancestorName = nodes[ancestorName]['parent']
        # Detach from the current parent, which keeps the (possibly virtual) rollup above
        # it honest. A validated tree always has one to detach from: in a single-root tree
        # every node descends from the root, so re-parenting the root itself would have
        # been refused as a cycle just above, and in a multi-root tree the roots hang off
        # the synthesized virtual root. The lookup is tolerant of a parentless node anyway,
        # so a tree that violated that invariant re-parents instead of raising KeyError in
        # the middle of a collection cycle.
        currentParent = nodes.get(child['parent'])
        if currentParent is not None:
            currentParent['children'].remove(childName)

    child['parent'] = parentName
    parent['children'].append(childName)
    logger.info('Registered Emporia nested device edge: "{}" -> "{}"'.format(childName, parentName))


def applyBalances(config, usageDataPoints):
    """Appends per-node Net Balance points for the hourly/daily aggregates in the list.

    Points are grouped by (account, timestamp, detailed tag) so that only time-aligned
    samples are netted, and each group is indexed by (device name, channel name) -- a
    device's total is its mains ('1,2,3') series, which carries the device name, and a
    circuit's total is its channel series. Indexing by the pair rather than the channel
    name alone keeps two panels that use the same channel name from overwriting each
    other's reading (device.validateHierarchy rejects such a config outright, so this is
    a second line of defense). For every node with children:

        raw = total(node) - sum(total(child) for each direct child)

    A missing child contributes 0, which is the right answer rather than a fallback:
    if a child reported nothing for the period, none of the node's usage was itemized
    below it. A node whose own total is absent is skipped, since there is nothing to
    net against. Results below -epsilon are warned about (or raised, aborting the
    collection cycle, when hierarchyNegativeBalanceAbort is set) and then clamped to
    zero; the epsilon absorbs rounding noise without hiding a genuinely wrong tree.

    The virtual root of a multi-panel account has no measured total, so instead of a
    balance it emits the sum of its roots under its own name: a whole-account total
    that counts no sub-metered branch twice. Its balance is zero by definition.

    Minute and second points are ignored (see the module notes on alignment). Raw
    points are never modified; this pass only appends. Returns usageDataPoints.
    """
    hierarchies = config.get('hierarchy') or {}
    if not any(hierarchies.values()):
        return usageDataPoints

    from vuegraf.collect import Point  # local import avoids a circular import at module load

    _, _, _, tagValueHour, tagValueDay = getTags(config)
    aggregateTags = (tagValueHour, tagValueDay)

    groups = {}
    for pt in usageDataPoints:
        if pt.detailed in aggregateTags and hierarchies.get(pt.accountName) is not None:
            groups.setdefault((pt.accountName, pt.timestamp, pt.detailed), {})[(pt.deviceName, pt.chanName)] = pt.usageWatts
    if not groups:
        return usageDataPoints

    epsilonWatts = getConfigValue(config, 'hierarchyBalanceEpsilonWatts')
    abortOnNegative = getConfigValue(config, 'hierarchyNegativeBalanceAbort')

    balancePoints = []
    suppressedWarnings = 0
    for (accountName, timestamp, detailed), totals in groups.items():
        hierarchy = hierarchies[accountName]
        nodes = hierarchy['nodes']

        def nodeTotal(nodeName, default=None):
            """This node's reading for the group, looked up by (device, channel)."""
            return totals.get((nodes[nodeName]['deviceName'], nodeName), default)

        for name, node in nodes.items():
            children = node['children']
            if not children:
                continue

            if node['kind'] == 'virtual':
                rootTotals = [t for t in (nodeTotal(childName) for childName in children) if t is not None]
                if rootTotals:
                    balancePoints.append(Point(accountName, name, name, sum(rootTotals), timestamp, detailed))
                continue

            total = nodeTotal(name)
            if total is None:
                continue

            raw = total - sum(nodeTotal(childName, 0.0) for childName in children)
            if raw < -epsilonWatts:
                message = ('Negative net balance for node "{}" in account "{}" at {} ({}): {:.1f}W; a child is '
                           'measuring more than its parent, so check the hierarchy configuration'.format(
                               name, accountName, timestamp, detailed, raw))
                if abortOnNegative:
                    raise ValueError(message)
                if _shouldWarnNegative(hierarchy, name):
                    logger.warning(message)
                else:
                    suppressedWarnings += 1

            balancePoints.append(Point(accountName, node['deviceName'], name + NET_BALANCE_SUFFIX,
                                       max(raw, 0.0), timestamp, detailed))

    if suppressedWarnings > 0:
        logger.info('Suppressed {} repeat negative net balance warnings; each node is reported at most once per {}s'.format(
                    suppressedWarnings, NEGATIVE_BALANCE_WARN_TTL_SEC))

    usageDataPoints.extend(balancePoints)
    return usageDataPoints
