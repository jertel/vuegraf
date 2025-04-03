# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

import unittest
from unittest.mock import MagicMock, patch
from pyemvue.device import VueDevice, VueDeviceChannel

# Local imports
from vuegraf import device as device_module


class TestDeviceFunctions(unittest.TestCase):

    def setUp(self):
        """Set up test fixtures."""
        self.mock_vue = MagicMock()
        self.account = {
            'email': 'test@example.com',
            'password': 'password',
            'vue': self.mock_vue,
            'deviceIdMap': {},
            'channelIdMap': {},
            'devices': [  # Example config structure for lookupChannelName
                {
                    'name': 'Main Panel',
                    'channels': {
                        '1': 'Channel One',
                        '2': 'Channel Two'
                    }
                },
                {
                    'name': 'Subpanel',
                    'channels': ['Sub Ch 1', 'Sub Ch 2']
                }
            ]
        }

        # Mock devices and channels
        self.device1 = VueDevice()
        self.device1.device_gid = 123
        self.device1.device_name = 'Main Panel'

        self.channel1_1 = VueDeviceChannel()
        self.channel1_1.device_gid = 123
        self.channel1_1.name = 'Channel 1'
        self.channel1_1.channel_num = '1'
        self.channel1_1.channel_multiplier = 1.0
        self.channel1_1.channel_type_gid = 1

        self.channel1_2 = VueDeviceChannel()
        self.channel1_2.device_gid = 123
        self.channel1_2.name = 'Channel 2'
        self.channel1_2.channel_num = '2'
        self.channel1_2.channel_multiplier = 1.0
        self.channel1_2.channel_type_gid = 1

        self.channel1_main = VueDeviceChannel()
        self.channel1_main.device_gid = 123
        self.channel1_main.name = None
        self.channel1_main.channel_num = '1,2,3'
        self.channel1_main.channel_multiplier = 1.0
        self.channel1_main.channel_type_gid = 1

        self.device1.channels = [self.channel1_1, self.channel1_2, self.channel1_main]

        self.device2 = VueDevice()
        self.device2.device_gid = 456
        self.device2.device_name = 'Subpanel'

        self.channel2_1 = VueDeviceChannel()
        self.channel2_1.device_gid = 456
        self.channel2_1.name = 'Sub Channel 1'
        self.channel2_1.channel_num = '1'
        self.channel2_1.channel_multiplier = 1.0
        self.channel2_1.channel_type_gid = 1

        self.device2.channels = [self.channel2_1]

        self.mock_vue.get_devices.return_value = [self.device1, self.device2]
        self.mock_vue.populate_device_properties.side_effect = lambda dev: dev  # Just return the device passed in

    def test_populateDevices(self):
        """Test populating devices and channels."""
        device_module.populateDevices(self.account)

        self.mock_vue.get_devices.assert_called_once()
        self.assertEqual(len(self.account['deviceIdMap']), 2)
        self.assertIn(123, self.account['deviceIdMap'])
        self.assertIn(456, self.account['deviceIdMap'])
        self.assertEqual(self.account['deviceIdMap'][123].device_name, 'Main Panel')
        self.assertEqual(self.account['deviceIdMap'][456].device_name, 'Subpanel')

        self.assertEqual(len(self.account['channelIdMap']), 4)  # 3 from device1, 1 from device2
        self.assertIn('123-1', self.account['channelIdMap'])
        self.assertIn('123-2', self.account['channelIdMap'])
        self.assertIn('123-1,2,3', self.account['channelIdMap'])
        self.assertIn('456-1', self.account['channelIdMap'])
        # Check if main channel name was populated correctly
        self.assertEqual(self.account['channelIdMap']['123-1,2,3'].name, 'Main Panel')

    def test_lookupDeviceName_exists(self):
        """Test looking up an existing device name."""
        device_module.populateDevices(self.account)  # Populate first
        name = device_module.lookupDeviceName(self.account, 123)
        self.assertEqual(name, 'Main Panel')

    def test_lookupDeviceName_not_exists_needs_populate(self):
        """Test looking up a device name when map is empty (triggers populate)."""
        name = device_module.lookupDeviceName(self.account, 123)
        self.assertEqual(name, 'Main Panel')
        self.mock_vue.get_devices.assert_called_once()  # Ensure populate was called

    def test_lookupDeviceName_gid_not_found(self):
        """Test looking up a device GID that doesn't exist."""
        device_module.populateDevices(self.account)  # Populate first
        name = device_module.lookupDeviceName(self.account, 999)
        self.assertEqual(name, '999')  # Should return the GID as string

    def test_lookupChannelName_simple(self):
        """Test looking up a simple channel name."""
        device_module.populateDevices(self.account)  # Populate first
        channel_to_lookup = VueDeviceChannel()
        channel_to_lookup.device_gid = 123
        channel_to_lookup.channel_num = '1'
        name = device_module.lookupChannelName(self.account, channel_to_lookup)
        # It should find the name from the config structure
        self.assertEqual(name, 'Channel One')

    def test_lookupChannelName_main(self):
        """Test looking up the main channel name ('1,2,3')."""
        device_module.populateDevices(self.account)  # Populate first
        channel_to_lookup = VueDeviceChannel()
        channel_to_lookup.device_gid = 123
        channel_to_lookup.channel_num = '1,2,3'
        name = device_module.lookupChannelName(self.account, channel_to_lookup)
        # Should default to device name
        self.assertEqual(name, 'Main Panel')

    def test_lookupChannelName_from_list_config(self):
        """Test looking up channel name from list-based config."""
        device_module.populateDevices(self.account)  # Populate first
        channel_to_lookup = VueDeviceChannel()
        channel_to_lookup.device_gid = 456
        channel_to_lookup.channel_num = '2'  # Corresponds to 'Sub Ch 2'
        name = device_module.lookupChannelName(self.account, channel_to_lookup)
        self.assertEqual(name, 'Sub Ch 2')

    def test_lookupChannelName_needs_populate(self):
        """Test looking up channel name when map is empty (triggers populate)."""
        channel_to_lookup = VueDeviceChannel()
        channel_to_lookup.device_gid = 123
        channel_to_lookup.channel_num = '1'
        name = device_module.lookupChannelName(self.account, channel_to_lookup)
        self.assertEqual(name, 'Channel One')
        self.mock_vue.get_devices.assert_called_once()  # Ensure populate was called

    def test_lookupChannelName_device_not_in_config(self):
        """Test channel lookup when device name isn't in the 'devices' config."""
        device_module.populateDevices(self.account)  # Populate first
        # Add a device not present in self.account['devices']
        device3 = VueDevice()
        device3.device_gid = 789
        device3.device_name = 'Unknown Panel'
        channel3_1 = VueDeviceChannel()
        channel3_1.device_gid = 789
        channel3_1.name = 'Unknown Ch 1'
        channel3_1.channel_num = '1'
        device3.channels = [channel3_1]
        self.mock_vue.get_devices.return_value.append(device3)
        self.account['deviceIdMap'][789] = device3
        self.account['channelIdMap']['789-1'] = channel3_1

        channel_to_lookup = VueDeviceChannel()
        channel_to_lookup.device_gid = 789
        channel_to_lookup.channel_num = '1'
        name = device_module.lookupChannelName(self.account, channel_to_lookup)
        # Should fall back to deviceName-channelNum format
        self.assertEqual(name, 'Unknown Panel-1')

    def test_lookupChannelName_channel_not_in_config(self):
        """Test channel lookup when channel number isn't in the device's config."""
        device_module.populateDevices(self.account)  # Populate first
        channel_to_lookup = VueDeviceChannel()
        channel_to_lookup.device_gid = 123
        channel_to_lookup.channel_num = '5'  # Channel 5 doesn't exist in config
        name = device_module.lookupChannelName(self.account, channel_to_lookup)
        # Should fall back to deviceName-channelNum format
        self.assertEqual(name, 'Main Panel-5')

    def test_lookupChannelName_no_device(self):
        """Test looking up a simple channel name when 'devices' is missing from config."""
        del self.account["devices"]
        device_module.populateDevices(self.account)  # Populate first
        channel_to_lookup = VueDeviceChannel()
        channel_to_lookup.device_gid = 123
        channel_to_lookup.channel_num = '1'
        name = device_module.lookupChannelName(self.account, channel_to_lookup)
        # It should find the name from the config structure
        self.assertEqual(name, 'Main Panel-1')

    def test_lookupChannelName_no_channels(self):
        """Test looking up a simple channel name when 'channels' is missing from device."""
        del self.account["devices"][0]["channels"]
        device_module.populateDevices(self.account)  # Populate first
        channel_to_lookup = VueDeviceChannel()
        channel_to_lookup.device_gid = 123
        channel_to_lookup.channel_num = '1'
        name = device_module.lookupChannelName(self.account, channel_to_lookup)
        # It should find the name from the config structure
        self.assertEqual(name, 'Main Panel-1')

    def test_lookupChannelName_invalid_channels(self):
        """Test looking up a simple channel name when 'channels' is not a dict."""
        self.account["devices"][0]["channels"] = None
        device_module.populateDevices(self.account)  # Populate first
        channel_to_lookup = VueDeviceChannel()
        channel_to_lookup.device_gid = 123
        channel_to_lookup.channel_num = '1'
        name = device_module.lookupChannelName(self.account, channel_to_lookup)
        # It should find the name from the config structure
        self.assertEqual(name, 'Main Panel-1')

    @patch('vuegraf.device.PyEmVue')  # Patch the class in the device module
    def test_initDeviceAccount(self, mock_pyemvue_class):
        """Test initializing the device account."""
        # Arrange
        mock_instance = MagicMock()
        mock_pyemvue_class.return_value = mock_instance  # The constructor returns our mock
        mock_instance.get_devices.return_value = [self.device1]  # Mock devices for populateDevices
        mock_instance.populate_device_properties.side_effect = lambda dev: dev

        config = {}  # Not used directly by initDeviceAccount but passed
        account_to_init = {'email': 'init@example.com', 'password': 'newpassword'}

        # Act
        device_module.initDeviceAccount(config, account_to_init)

        # Assert
        mock_pyemvue_class.assert_called_once()  # Was constructor called?
        mock_instance.login.assert_called_once_with(username='init@example.com', password='newpassword')
        self.assertIn('vue', account_to_init)
        self.assertIs(account_to_init['vue'], mock_instance)
        # Check if populateDevices was called implicitly
        mock_instance.get_devices.assert_called_once()
        self.assertIn('deviceIdMap', account_to_init)
        self.assertIn('channelIdMap', account_to_init)
        self.assertIn(123, account_to_init['deviceIdMap'])

    @patch('vuegraf.device.PyEmVue')  # Patch the class in the device module
    def test_initDeviceAccount_reinit(self, mock_pyemvue_class):
        """Test initializing the device account."""
        config = {}
        account_already_initted = {
            'vue': 1
        }
        device_module.initDeviceAccount(config, account_already_initted)

        mock_pyemvue_class.assert_not_called()
        self.assertEqual(account_already_initted['vue'], 1)


if __name__ == '__main__':
    unittest.main()
