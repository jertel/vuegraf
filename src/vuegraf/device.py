# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

# Contains logic relating to Emporia Vue devices and channels.

import logging
from pyemvue import PyEmVue


logger = logging.getLogger('vuegraf.device')


def populateDevices(account):
    deviceIdMap = {}
    account['deviceIdMap'] = deviceIdMap
    channelIdMap = {}
    account['channelIdMap'] = channelIdMap
    devices = account['vue'].get_devices()
    for device in devices:
        device = account['vue'].populate_device_properties(device)
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


def initDeviceAccount(config, account):
    if 'vue' not in account:
        account['vue'] = PyEmVue()
        account['vue'].login(username=account['email'], password=account['password'])
        logger.info('Emporia Login completed sucessfully')
        populateDevices(account)
