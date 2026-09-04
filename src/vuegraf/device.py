# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

# Contains logic relating to Emporia Vue devices and channels.

import logging
from pyemvue import PyEmVue

from vuegraf.config import isHierarchyConfigured
from vuegraf.hierarchy import findCycle


logger = logging.getLogger('vuegraf.device')


def populateDevices(account):
    deviceIdMap = {}
    account['deviceIdMap'] = deviceIdMap
    channelIdMap = {}
    account['channelIdMap'] = channelIdMap
    devices = account['vue'].get_devices()
    for device in devices:
        # Only map the primary device. We get two device entries per device. The first contains all the
        # device details and has a single 1,2,3 channel for the mains. The second does not have the device
        # details but has the other channels for the individual circuits. So we use the (base) device with
        # an non-blank device name attribute as the map entry.
        if device.device_gid not in deviceIdMap and len(device.device_name) > 0:
            deviceIdMap[device.device_gid] = device

        for chan in device.channels:
            key = '{}-{}'.format(device.device_gid, chan.channel_num)
            if chan.name is None and chan.channel_num == '1,2,3':
                chan.name = device.device_name
            channelIdMap[key] = chan
            logger.info('Discovered new channel: {} ({})'.format(chan.name, chan.channel_num))


def lookupDeviceName(account, device_gid):
    if device_gid not in account['deviceIdMap']:
        populateDevices(account)

    deviceName = '{}'.format(device_gid)
    if device_gid in account['deviceIdMap']:
        deviceName = account['deviceIdMap'][device_gid].device_name
    return deviceName


def lookupChannelName(account, chan):
    if chan.device_gid not in account['deviceIdMap']:
        populateDevices(account)

    deviceName = lookupDeviceName(account, chan.device_gid)
    name = '{}-{}'.format(deviceName, chan.channel_num)

    try:
        num = int(chan.channel_num)
        if 'devices' in account:
            for device in account['devices']:
                if 'name' in device and device['name'] == deviceName:
                    if 'channels' in device:
                        if isinstance(device['channels'], list) and len(device['channels']) >= num:
                            name = device['channels'][num - 1]
                            break
                        elif isinstance(device['channels'], dict):
                            name = device['channels'][str(num)]
                            break
    except Exception:
        if chan.channel_num == '1,2,3':
            name = deviceName

    return name


def validateHierarchy(config, account):
    """Builds and validates the account's device tree once the real devices are known.

    Config load only checks structure (see config.validateHierarchyConfig), because a
    'parent' may name a channel that does not exist until discovery. This is the
    authoritative pass. The result is stored on config['hierarchy'] as

        {accountName: {'nodes': {name: {parent, children, kind, deviceName, explicit}},
                       'roots': [...], 'virtualRoot': name-or-None,
                       'warned': set(), 'negativeWarnExpiry': {}}}

    so that the balance pass is pure lookup. Accounts that did not opt in store None,
    which disables the feature for them.

    The nodes are: every device ('device'), whose total is its mains series -- the
    '1,2,3' channel, which carries the device name; every numbered channel ('circuit'),
    implicitly contained by its device; and, when more than one root remains, a
    synthesized '<account> Panel' root ('virtual') that rolls its roots up into a
    single double-count-free account total.

    Raises ValueError, naming the offending node, when a parent does not resolve, when
    the edges form a cycle, or when one name would refer to two different nodes.
    """
    accountName = account.get('name')
    hierarchies = config.setdefault('hierarchy', {})
    if accountName is None or not isHierarchyConfigured(account):
        hierarchies[accountName] = None
        return None

    configDevices = account.get('devices') or []
    nodes = {}

    def addNode(name, kind, deviceName, parent=None):
        existing = nodes.get(name)
        if existing is not None:
            raise ValueError('Hierarchy name conflict in account "{}": "{}" refers to both a {} and a {}; rename one so '
                             'that every node name is unique'.format(accountName, name, existing['kind'], kind))
        nodes[name] = {'parent': parent, 'children': [], 'kind': kind, 'deviceName': deviceName, 'explicit': False}

    # Device nodes: everything Emporia reported, plus any config-only entry (a plug that
    # happens to be offline right now still belongs in the tree).
    for device in account['deviceIdMap'].values():
        addNode(device.device_name, 'device', device.device_name)
    for device in configDevices:
        if device['name'] not in nodes:
            addNode(device['name'], 'device', device['name'])

    # Circuit nodes. Only numbered channels are circuits: '1,2,3' is the device mains
    # and already carries the device name, and 'Balance'/'TotalUsage' are derived.
    for chan in account['channelIdMap'].values():
        try:
            int(chan.channel_num)
        except (TypeError, ValueError):
            continue
        deviceName = lookupDeviceName(account, chan.device_gid)
        addNode(lookupChannelName(account, chan), 'circuit', deviceName, parent=deviceName)

    # Explicit config edges, the only way to express a cross-Vue-device link. They
    # override the implicit device containment assigned above.
    for device in configDevices:
        if 'parent' in device:
            nodes[device['name']]['parent'] = device['parent']
            nodes[device['name']]['explicit'] = True

    for name, node in nodes.items():
        parentName = node['parent']
        if parentName is not None:
            if parentName not in nodes:
                raise ValueError('Hierarchy parent "{}" of "{}" not found in account "{}"; it must name a device or a '
                                 'channel'.format(parentName, name, accountName))
            nodes[parentName]['children'].append(name)

    cycle = findCycle(nodes)
    if cycle is not None:
        raise ValueError('Hierarchy cycle detected in account "{}": {}'.format(accountName, ' -> '.join(cycle)))

    roots = [name for name, node in nodes.items() if node['parent'] is None]
    virtualRoot = None
    if len(roots) > 1:
        # Independent top-level panels (dual service mains). Emporia has no concept of a
        # root above them, so synthesize one to get a correct whole-account total.
        virtualRoot = '{} Panel'.format(accountName)
        if virtualRoot in nodes:
            raise ValueError('Cannot synthesize virtual root "{}" in account "{}": that name is already in use'.format(
                             virtualRoot, accountName))
        nodes[virtualRoot] = {'parent': None, 'children': list(roots), 'kind': 'virtual',
                              'deviceName': virtualRoot, 'explicit': False}
        for rootName in roots:
            nodes[rootName]['parent'] = virtualRoot
        roots = [virtualRoot]

    hierarchy = {'nodes': nodes, 'roots': roots, 'virtualRoot': virtualRoot, 'warned': set(), 'negativeWarnExpiry': {}}
    hierarchies[accountName] = hierarchy
    logger.info('Hierarchy validated; account="{}"; nodes={}; roots={}; virtualRoot={}'.format(
                accountName, len(nodes), roots, virtualRoot))
    return hierarchy


def initDeviceAccount(config, account):
    if 'vue' not in account:
        account['vue'] = PyEmVue()
        account['vue'].login(username=account['email'], password=account['password'])
        logger.info('Emporia Login completed sucessfully')
        populateDevices(account)
        validateHierarchy(config, account)
