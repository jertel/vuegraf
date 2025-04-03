# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

import datetime
import logging
from unittest import TestCase
from unittest.mock import MagicMock, patch

from pyemvue.device import VueDeviceChannel, VueDevice
from pyemvue.enums import Scale, Unit

from vuegraf import collect


# Basic config structure for tests
MOCK_CONFIG = {  # Basic config structure for tests
    'influx': {
        'tags': {
            'second': 'Seconds',
            'minute': 'Minutes',
            'hour': 'Hours',
            'day': 'Days',
            'month': 'Months'
        }
    },
    'emporia': {
        'accounts': [
            {
                'name': 'TestAccount',
                'email': 'test@example.com',
                'password': 'password',
                'devices': [
                    {'gid': 12345, 'name': 'TestDevice1'},
                ],
                'channels': [
                    {'deviceGid': 12345, 'channelNum': '1,2,3', 'name': 'TestChannel1'},
                    {'deviceGid': 12345, 'channelNum': 'Balance', 'name': 'BalanceChannel'},
                    {'deviceGid': 12345, 'channelNum': 'TotalUsage', 'name': 'TotalUsageChannel'},
                ]
            }
        ]
    },
    'history': {
        'enabled': True,
        'start': '2024-01-01T00:00:00Z',
        'batchSizeDays': 7
    },
    'data': {
        'detailedDataEnabled': True,
        'detailedDataSecondsEnabled': True,
        'detailedDataMinutesHistoryEnabled': True,
        'detailedDataMinutesHistoryDays': 1,
        'detailedDataSecondsHistoryEnabled': True,
        'detailedDataSecondsHistoryHours': 1,
        'timezone': 'UTC'
    }
}

MOCK_ACCOUNT_INFO = {
    'name': 'TestAccount',
    'vue': MagicMock(),
    'deviceIdMap': {12345: 'TestDevice1'},
    'channelIdMap': {
        (12345, '1,2,3'): 'TestChannel1',
        (12345, 'Balance'): 'BalanceChannel',
        (12345, 'TotalUsage'): 'TotalUsageChannel',
    }
}

# Disable logging during tests
logging.disable(logging.CRITICAL)  # Disable logging during tests


class TestCollect(TestCase):

    def setUp(self):
        self.mock_config = MOCK_CONFIG.copy()
        self.mock_account = MOCK_ACCOUNT_INFO.copy()
        self.mock_account['vue'] = MagicMock()  # Reset mock for each test
        self.usage_data_points = []
        self.stop_time_utc = datetime.datetime(2024, 1, 10, 12, 0, 30, tzinfo=datetime.timezone.utc)
        self.detailed_start_time_utc = self.stop_time_utc - datetime.timedelta(hours=1)

        # Mock dependencies
        self.patcher_getConfigValue = patch('vuegraf.collect.getConfigValue', side_effect=self._mock_getConfigValue)
        self.patcher_getInfluxTag = patch('vuegraf.collect.getInfluxTag', return_value=(None, 'Seconds', 'Minutes', 'Hours', 'Days'))
        self.patcher_lookupDeviceName = patch('vuegraf.collect.lookupDeviceName', return_value='TestDevice1')
        self.patcher_lookupChannelName = patch('vuegraf.collect.lookupChannelName', side_effect=self._mock_lookupChannelName)
        self.patcher_createDataPoint = patch('vuegraf.collect.createDataPoint', side_effect=self._mock_createDataPoint)
        self.patcher_getLastDBTimeStamp = patch('vuegraf.collect.getLastDBTimeStamp')
        self.patcher_calculateHistoryTimeRange = patch('vuegraf.collect.calculateHistoryTimeRange')
        self.patcher_convertToLocalDayInUTC = patch('vuegraf.collect.convertToLocalDayInUTC',
                                                    side_effect=lambda cfg, dt: dt.replace(hour=0, minute=0, second=0, microsecond=0))

        self.mock_getConfigValue = self.patcher_getConfigValue.start()
        self.mock_getInfluxTag = self.patcher_getInfluxTag.start()
        self.mock_lookupDeviceName = self.patcher_lookupDeviceName.start()
        self.mock_lookupChannelName = self.patcher_lookupChannelName.start()
        self.mock_createDataPoint = self.patcher_createDataPoint.start()
        self.mock_getLastDBTimeStamp = self.patcher_getLastDBTimeStamp.start()
        self.mock_calculateHistoryTimeRange = self.patcher_calculateHistoryTimeRange.start()
        self.mock_convertToLocalDayInUTC = self.patcher_convertToLocalDayInUTC.start()

    def tearDown(self):
        self.patcher_getConfigValue.stop()
        self.patcher_getInfluxTag.stop()
        self.patcher_lookupDeviceName.stop()
        self.patcher_lookupChannelName.stop()
        self.patcher_createDataPoint.stop()
        self.patcher_getLastDBTimeStamp.stop()
        self.patcher_calculateHistoryTimeRange.stop()
        self.patcher_convertToLocalDayInUTC.stop()

    def _mock_getConfigValue(self, config, key, default=None):
        # Simplified mock for getConfigValue
        if key == 'detailedDataEnabled':
            return config.get('data', {}).get('detailedDataEnabled', False)
        if key == 'detailedDataSecondsEnabled':
            return config.get('data', {}).get('detailedDataSecondsEnabled', False)
        if key == 'detailedDataMinutesHistoryEnabled':
            return config.get('data', {}).get('detailedDataMinutesHistoryEnabled', False)
        if key == 'detailedDataMinutesHistoryDays':
            return config.get('data', {}).get('detailedDataMinutesHistoryDays', 1)
        if key == 'detailedDataSecondsHistoryEnabled':
            return config.get('data', {}).get('detailedDataSecondsHistoryEnabled', False)
        if key == 'detailedDataSecondsHistoryHours':
            return config.get('data', {}).get('detailedDataSecondsHistoryHours', 1)
        if key == 'timezone':
            return config.get('data', {}).get('timezone', 'UTC')
        return default  # Should not happen in these tests if config is set up

    def _mock_lookupChannelName(self, account, channel):
        # Simplified mock based on channel number
        if channel.channel_num == '1,2,3':
            return 'TestChannel1'
        if channel.channel_num == 'Balance':
            return 'BalanceChannel'
        if channel.channel_num == 'TotalUsage':
            return 'TotalUsageChannel'
        if channel.channel_num == 'NestedChan':
            return 'NestedChannel'
        return f'UnknownChannel_{channel.channel_num}'

    def _mock_createDataPoint(self, config, account_name, device_name, channel_name, watts, timestamp, point_type):
        # Just return a tuple representing the data point for easy assertion
        return (account_name, device_name, channel_name, watts, timestamp, point_type)

    def _create_mock_device(self, gid, channels_data):
        device = VueDevice()
        device.device_gid = gid
        device.channels = {}
        for chan_num, usage, nested_devices_data in channels_data:
            channel = VueDeviceChannel()
            channel.device_gid = gid
            channel.channel_num = chan_num
            channel.usage = usage
            channel.nested_devices = {}
            if nested_devices_data:
                for nested_gid, nested_channels_data in nested_devices_data.items():
                    channel.nested_devices[nested_gid] = self._create_mock_device(nested_gid, nested_channels_data)
            device.channels[chan_num] = channel
        return device

    # --- Tests for extractDataPoints ---

    def test_extractDataPoints_minute_basic(self):
        # Test basic minute data extraction when history is disabled
        self.mock_config['data']['detailedDataMinutesHistoryEnabled'] = False
        self.mock_getLastDBTimeStamp.return_value = (None, None, False)  # minuteHistoryEnabled = False

        mock_device = self._create_mock_device(12345, [('1,2,3', 0.01, None)])  # 0.01 kWh

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  False, self.usage_data_points, self.detailed_start_time_utc)

        expected_watts = 0.01 * 60 * 1000  # 600W
        expected_timestamp = self.stop_time_utc.replace(second=0, microsecond=0)
        expected_point = ('TestAccount', 'TestDevice1', 'TestChannel1', expected_watts, expected_timestamp, 'Minutes')

        self.assertEqual(len(self.usage_data_points), 1)
        self.assertEqual(self.usage_data_points[0], expected_point)
        self.mock_createDataPoint.assert_called_once_with(
            self.mock_config, 'TestAccount', 'TestDevice1', 'TestChannel1',
            expected_watts, expected_timestamp, 'Minutes'
        )
        self.mock_getLastDBTimeStamp.assert_called_once()  # Called for minutes check
        self.mock_account['vue'].get_chart_usage.assert_not_called()  # History not fetched

    def test_extractDataPoints_minute_usage_none(self):
        # Test minute data extraction when channel usage is None
        self.mock_config['data']['detailedDataMinutesHistoryEnabled'] = False
        self.mock_getLastDBTimeStamp.return_value = (None, None, False)  # minuteHistoryEnabled = False

        # Create a device with one channel having usage=None and another with usage
        mock_device = self._create_mock_device(12345, [
            ('1,2,3', None, None),          # Channel with None usage
            ('Balance', 0.02, None)         # Channel with valid usage
        ])

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  False, self.usage_data_points, self.detailed_start_time_utc)

        # Assert that only one data point (for 'Balance') was created
        self.assertEqual(len(self.usage_data_points), 1)

        # Check the point created is for the 'Balance' channel
        expected_watts_balance = 0.02 * 60 * 1000  # 1200W
        expected_timestamp_balance = self.stop_time_utc.replace(second=0, microsecond=0)
        expected_point_balance = ('TestAccount', 'TestDevice1', 'BalanceChannel', expected_watts_balance,
                                  expected_timestamp_balance, 'Minutes')
        self.assertEqual(self.usage_data_points[0], expected_point_balance)

        # Assert createDataPoint was called only once (for the 'Balance' channel)
        self.mock_createDataPoint.assert_called_once_with(
            self.mock_config, 'TestAccount', 'TestDevice1', 'BalanceChannel',
            expected_watts_balance, expected_timestamp_balance, 'Minutes'
        )
        # getLastDBTimeStamp should only be called for the channel with non-None usage
        self.assertEqual(self.mock_getLastDBTimeStamp.call_count, 1)
        self.mock_account['vue'].get_chart_usage.assert_not_called()

    def test_extractDataPoints_minute_history_collection(self):
        # Test minute history collection logic
        self.mock_config['data']['detailedDataMinutesHistoryEnabled'] = True
        minute_history_start = self.stop_time_utc.replace(second=0, microsecond=0) - datetime.timedelta(minutes=10)
        stop_time_min = self.stop_time_utc.replace(second=0, microsecond=0)
        self.mock_getLastDBTimeStamp.return_value = (minute_history_start, stop_time_min, True)  # minuteHistoryEnabled = True

        mock_device = self._create_mock_device(12345, [('1,2,3', 0.01, None)])  # Usage here is ignored when history is fetched

        # Mock return value for get_chart_usage (minute scale)
        mock_minute_usage = [0.005, 0.006, None, 0.007]  # kWh per minute
        mock_usage_start_time = minute_history_start
        self.mock_account['vue'].get_chart_usage.return_value = (mock_minute_usage, mock_usage_start_time)

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  False, self.usage_data_points, self.detailed_start_time_utc)

        self.mock_getLastDBTimeStamp.assert_called_once()
        self.mock_account['vue'].get_chart_usage.assert_called_once_with(
            mock_device.channels['1,2,3'],
            minute_history_start,
            stop_time_min,
            scale=Scale.MINUTE.value,
            unit=Unit.KWH.value
        )

        self.assertEqual(len(self.usage_data_points), 3)  # 3 valid points from mock_minute_usage

        # Check first point
        expected_watts_0 = 0.005 * 60 * 1000
        expected_ts_0 = mock_usage_start_time + datetime.timedelta(minutes=0)
        self.assertEqual(self.usage_data_points[0], (
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_0, expected_ts_0, 'Minutes'
        ))

        # Check second point
        expected_watts_1 = 0.006 * 60 * 1000
        expected_ts_1 = mock_usage_start_time + datetime.timedelta(minutes=1)
        self.assertEqual(self.usage_data_points[1], (
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_1, expected_ts_1, 'Minutes'
        ))

        # Check third point (index 3 because index 2 was None)
        expected_watts_3 = 0.007 * 60 * 1000
        expected_ts_3 = mock_usage_start_time + datetime.timedelta(minutes=3)
        self.assertEqual(self.usage_data_points[2], (
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_3, expected_ts_3, 'Minutes'
        ))

        self.assertEqual(self.mock_createDataPoint.call_count, 3)

    def test_extractDataPoints_minute_history_no_data_retry(self):
        # Test minute history collection when the first attempt returns no data
        self.mock_config['data']['detailedDataMinutesHistoryEnabled'] = True
        minute_history_start_initial = self.stop_time_utc.replace(second=0, microsecond=0) - datetime.timedelta(hours=12)
        stop_time_min_initial = self.stop_time_utc.replace(second=0, microsecond=0) - datetime.timedelta(hours=6)
        self.mock_getLastDBTimeStamp.return_value = (minute_history_start_initial, stop_time_min_initial, True)

        mock_device = self._create_mock_device(12345, [('1,2,3', 0.01, None)])

        # Mock return values for get_chart_usage
        # First call: all None
        mock_minute_usage_none = [None] * 10
        mock_usage_start_time_initial = minute_history_start_initial
        # Second call: some data
        mock_minute_usage_data = [8, 9]
        minute_history_start_next = stop_time_min_initial  # Next interval starts where the last one ended
        stop_time_min_next = stop_time_min_initial + (stop_time_min_initial - minute_history_start_initial)
        stop_time_min_next = min(
            stop_time_min_next,
            self.stop_time_utc.replace(second=0, microsecond=0)
        )  # Ensure it doesn't exceed stopTimeUTC
        mock_usage_start_time_next = minute_history_start_next

        self.mock_account['vue'].get_chart_usage.side_effect = [
            (mock_minute_usage_none, mock_usage_start_time_initial),
            (mock_minute_usage_data, mock_usage_start_time_next)
        ]

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  False, self.usage_data_points, self.detailed_start_time_utc)

        self.assertEqual(self.mock_account['vue'].get_chart_usage.call_count, 2)
        # Check first call args
        self.mock_account['vue'].get_chart_usage.assert_any_call(
            mock_device.channels['1,2,3'],
            minute_history_start_initial,
            stop_time_min_initial,
            scale=Scale.MINUTE.value,
            unit=Unit.KWH.value
        )
        # Check second call args
        self.mock_account['vue'].get_chart_usage.assert_any_call(
            mock_device.channels['1,2,3'],
            minute_history_start_next,
            stop_time_min_next,
            scale=Scale.MINUTE.value,
            unit=Unit.KWH.value
        )

        self.assertEqual(len(self.usage_data_points), 2)  # Points from the second call
        expected_watts_0 = 8 * 60 * 1000
        expected_ts_0 = mock_usage_start_time_next + datetime.timedelta(minutes=0)
        self.assertEqual(self.usage_data_points[0], (
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_0, expected_ts_0, 'Minutes'
        ))

        expected_watts_1 = 9 * 60 * 1000
        expected_ts_1 = mock_usage_start_time_next + datetime.timedelta(minutes=1)
        self.assertEqual(self.usage_data_points[1], (
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_1, expected_ts_1, 'Minutes'
        ))

    def test_extractDataPoints_minute_history_no_data_offline(self):
        # Test minute history collection when the device seems offline (keeps returning no data)
        self.mock_config['data']['detailedDataMinutesHistoryEnabled'] = True
        minute_history_start_initial = self.stop_time_utc.replace(second=0, microsecond=0) - datetime.timedelta(hours=12)
        stop_time_min_initial = self.stop_time_utc.replace(second=0, microsecond=0) - datetime.timedelta(hours=6)
        self.mock_getLastDBTimeStamp.return_value = (minute_history_start_initial, stop_time_min_initial, True)

        mock_device = self._create_mock_device(12345, [('1', 0.01, None)])

        # Mock return values for get_chart_usage - always None
        mock_minute_usage_none = [None] * 10
        mock_usage_start_time_initial = minute_history_start_initial
        minute_history_start_next = stop_time_min_initial
        stop_time_min_next = stop_time_min_initial + (stop_time_min_initial - minute_history_start_initial)
        mock_usage_start_time_next = minute_history_start_next

        # Simulate reaching the global stop time
        minute_history_start_final = stop_time_min_next
        stop_time_min_final = self.stop_time_utc.replace(second=0, microsecond=0)
        mock_usage_start_time_final = minute_history_start_final

        self.mock_account['vue'].get_chart_usage.side_effect = [
            (mock_minute_usage_none, mock_usage_start_time_initial),
            (mock_minute_usage_none, mock_usage_start_time_next),
            (mock_minute_usage_none, mock_usage_start_time_final),
        ]

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  False, self.usage_data_points, self.detailed_start_time_utc)

        # It should try multiple times until stopTimeMin reaches stopTimeUTC
        self.assertGreaterEqual(self.mock_account['vue'].get_chart_usage.call_count, 2)  # At least the initial and one retry
        # Check the last call went up to the global stop time

        self.mock_account['vue'].get_chart_usage.assert_any_call(
            mock_device.channels['1'],
            stop_time_min_initial,
            stop_time_min_final,
            scale=Scale.MINUTE.value,
            unit=Unit.KWH.value
        )
        self.assertEqual(len(self.usage_data_points), 0)  # No data points collected

    def test_extractDataPoints_minute_history_skipped_due_to_history_param(self):
        # Test scenario where minute history is enabled, channel not excluded,
        # but historyStartTimeUTC is provided (not None), making elif condition false.
        # This simulates a case where history collection is handled elsewhere or not needed now,
        # so extractDataPoints should skip minute processing (both simple avg and API fetch).
        self.mock_config['data']['detailedDataEnabled'] = True
        self.mock_config['data']['detailedDataMinutesHistoryEnabled'] = True
        self.mock_config['data']['detailedDataSecondsEnabled'] = False  # Disable seconds for simplicity

        # Mock getLastDBTimeStamp for minute check: history is enabled
        minute_history_start_db = self.stop_time_utc.replace(second=0, microsecond=0) - datetime.timedelta(minutes=30)
        stop_time_min_db = self.stop_time_utc.replace(second=0, microsecond=0)
        # Ensure minuteHistoryEnabled is True
        self.mock_getLastDBTimeStamp.return_value = (minute_history_start_db, stop_time_min_db, True)

        # Use a non-excluded channel
        mock_device = self._create_mock_device(12345, [('1,2,3', 0.01, None)])

        # Provide a non-None historyStartTimeUTC
        provided_history_start_time = self.stop_time_utc - datetime.timedelta(days=1)

        # Call extractDataPoints with a non-None historyStartTimeUTC
        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  True,  # collectDetails = True
                                  self.usage_data_points, None, None,
                                  provided_history_start_time)  # historyStartTimeUTC is NOT None

        # Assertions
        # getLastDBTimeStamp should still be called to check minute history status
        self.mock_getLastDBTimeStamp.assert_called_once_with(
            self.mock_config, 'TestDevice1', 'TestChannel1', 'Minutes',
            self.stop_time_utc, self.stop_time_utc, False
        )
        # get_chart_usage should NOT be called because historyStartTimeUTC was provided (elif is False)
        self.mock_account['vue'].get_chart_usage.assert_not_called()
        # createDataPoint should NOT be called for minutes because minuteHistoryEnabled was True (if is False)
        # and historyStartTimeUTC was not None (elif is False)
        minute_point_calls = [
            call for call in self.mock_createDataPoint.call_args_list
            if call[0][6] == 'Minutes'  # Check the point_type argument (index 6)
        ]
        self.assertEqual(len(minute_point_calls), 0)
        # Overall, no points should be added in this specific scenario for this channel's minute data
        self.assertEqual(len(self.usage_data_points), 0)

    def test_extractDataPoints_day_pointType(self):
        # Test extraction when pointType is 'Days'
        mock_device = self._create_mock_device(12345, [('1,2,3', 1.5, None)])  # 1.5 kWh for the day
        history_start_time = datetime.datetime(2024, 1, 9, 0, 0, 0, tzinfo=datetime.timezone.utc)

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  False, self.usage_data_points, self.detailed_start_time_utc,
                                  pointType='Days', historyStartTimeUTC=history_start_time)

        expected_watts = 1.5 * 1000  # 1500W (average for the day)
        # convertToLocalDayInUTC mock returns start of the day UTC
        expected_timestamp = history_start_time.replace(hour=0, minute=0, second=0, microsecond=0)
        expected_point = ('TestAccount', 'TestDevice1', 'TestChannel1', expected_watts, expected_timestamp, 'Days')

        self.assertEqual(len(self.usage_data_points), 1)
        self.assertEqual(self.usage_data_points[0], expected_point)
        self.mock_createDataPoint.assert_called_once_with(
            self.mock_config, 'TestAccount', 'TestDevice1', 'TestChannel1',
            expected_watts, expected_timestamp, 'Days'
        )
        self.mock_getLastDBTimeStamp.assert_not_called()  # Not called when pointType is specified
        self.mock_account['vue'].get_chart_usage.assert_not_called()  # Not called when pointType is specified

    def test_extractDataPoints_hour_pointType(self):
        # Test extraction when pointType is 'Hours'
        mock_device = self._create_mock_device(12345, [('1,2,3', 0.1, None)])  # 0.1 kWh for the hour
        history_start_time = datetime.datetime(2024, 1, 9, 10, 0, 0, tzinfo=datetime.timezone.utc)

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  False, self.usage_data_points, self.detailed_start_time_utc,
                                  pointType='Hours', historyStartTimeUTC=history_start_time)

        expected_watts = 0.1 * 1000  # 100W (average for the hour)
        expected_timestamp = history_start_time  # Timestamp is the start of the hour
        expected_point = ('TestAccount', 'TestDevice1', 'TestChannel1', expected_watts, expected_timestamp, 'Hours')

        self.assertEqual(len(self.usage_data_points), 1)
        self.assertEqual(self.usage_data_points[0], expected_point)
        self.mock_createDataPoint.assert_called_once_with(
            self.mock_config, 'TestAccount', 'TestDevice1', 'TestChannel1',
            expected_watts, expected_timestamp, 'Hours'
        )
        self.mock_getLastDBTimeStamp.assert_not_called()
        self.mock_account['vue'].get_chart_usage.assert_not_called()

    def test_extractDataPoints_seconds_collection(self):
        # Test seconds data collection
        self.mock_config['data']['detailedDataEnabled'] = True
        self.mock_config['data']['detailedDataSecondsEnabled'] = True
        self.mock_config['data']['detailedDataMinutesHistoryEnabled'] = False  # Disable minute history for simplicity
        self.mock_getLastDBTimeStamp.side_effect = [
            (None, None, False),  # Minute history check -> disabled
            (self.detailed_start_time_utc, self.stop_time_utc, True)  # Second history check -> enabled
        ]

        mock_device = self._create_mock_device(12345, [('1,2,3', 0.01, None)])  # Minute usage

        # Mock return value for get_chart_usage (second scale)
        mock_second_usage = [1, 2, None, 3]  # kWh per second
        mock_usage_start_time = self.detailed_start_time_utc
        self.mock_account['vue'].get_chart_usage.return_value = (mock_second_usage, mock_usage_start_time)

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  True, self.usage_data_points, self.detailed_start_time_utc)  # collectDetails = True

        # Check getLastDBTimeStamp calls
        self.assertEqual(self.mock_getLastDBTimeStamp.call_count, 2)
        self.mock_getLastDBTimeStamp.assert_any_call(
            self.mock_config, 'TestDevice1', 'TestChannel1', 'Minutes',
            self.stop_time_utc, self.stop_time_utc, False
        )
        self.mock_getLastDBTimeStamp.assert_any_call(
            self.mock_config, 'TestDevice1', 'TestChannel1', 'Seconds',
            self.detailed_start_time_utc, self.stop_time_utc, True
        )

        # Check get_chart_usage call for seconds
        self.mock_account['vue'].get_chart_usage.assert_called_once_with(
            mock_device.channels['1,2,3'],
            self.detailed_start_time_utc,
            self.stop_time_utc,
            scale=Scale.SECOND.value,
            unit=Unit.KWH.value
        )

        # 1 minute point + 3 second points
        self.assertEqual(len(self.usage_data_points), 4)

        # Check first second point
        expected_watts_s0 = 1 * 60 * 60 * 1000  # 3.6W
        expected_ts_s0 = mock_usage_start_time + datetime.timedelta(seconds=0)
        self.assertIn((
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_s0, expected_ts_s0, 'Seconds'
        ), self.usage_data_points)

        # Check second second point
        expected_watts_s1 = 2 * 60 * 60 * 1000  # 7.2W
        expected_ts_s1 = mock_usage_start_time + datetime.timedelta(seconds=1)
        self.assertIn((
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_s1, expected_ts_s1, 'Seconds'
        ), self.usage_data_points)

        # Check third second point (index 3)
        expected_watts_s3 = 3 * 60 * 60 * 1000  # 10.8W
        expected_ts_s3 = mock_usage_start_time + datetime.timedelta(seconds=3)
        self.assertIn((
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_s3, expected_ts_s3, 'Seconds'
        ), self.usage_data_points)

        self.assertEqual(self.mock_createDataPoint.call_count, 4)

    def test_getConfigValue_detailedDataMinutesHistoryDays_present(self):
        """Verify _mock_getConfigValue handles existing detailedDataMinutesHistoryDays."""
        # Ensure the key exists and is different from default for clarity
        self.mock_config['data']['detailedDataMinutesHistoryDays'] = 5
        # Call the patched function directly (via the mock object)
        result = self.mock_getConfigValue(self.mock_config, 'detailedDataMinutesHistoryDays')
        self.assertEqual(result, 5)

        # Test the default case as well within the mock context
        del self.mock_config['data']['detailedDataMinutesHistoryDays']
        result_default = self.mock_getConfigValue(self.mock_config, 'detailedDataMinutesHistoryDays')
        self.assertEqual(result_default, 1)

    def test_extractDataPoints_detailedDataDisabled(self):
        # Test that second details are NOT collected when detailedDataEnabled is False
        self.mock_config['data']['detailedDataEnabled'] = False
        self.mock_config['data']['detailedDataSecondsEnabled'] = True  # Keep this true to test the 'detailedDataEnabled' check
        self.mock_config['data']['detailedDataMinutesHistoryEnabled'] = False  # Disable minute history for simplicity
        self.mock_getLastDBTimeStamp.side_effect = [
            (None, None, False),  # Minute history check -> disabled
            # No second history check should occur now because detailedDataEnabled=False short-circuits detailedSecondsEnabled
        ]

        mock_device = self._create_mock_device(12345, [('1,2,3', 0.01, None)])  # Basic minute usage

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  True, self.usage_data_points, self.detailed_start_time_utc)  # collectDetails = True

        # Assertions
        # 1. getLastDBTimeStamp called only once (for minutes)
        self.assertEqual(self.mock_getLastDBTimeStamp.call_count, 1)
        self.mock_getLastDBTimeStamp.assert_called_once_with(
            self.mock_config, 'TestDevice1', 'TestChannel1', 'Minutes',
            self.stop_time_utc, self.stop_time_utc, False
        )

        # 2. get_chart_usage (for seconds) was NOT called
        self.mock_account['vue'].get_chart_usage.assert_not_called()

        # 3. Only the minute data point should be created
        self.assertEqual(len(self.usage_data_points), 1)
        expected_watts = 0.01 * 60 * 1000
        expected_timestamp = self.stop_time_utc.replace(second=0, microsecond=0)
        expected_point = ('TestAccount', 'TestDevice1', 'TestChannel1', expected_watts, expected_timestamp, 'Minutes')
        self.assertEqual(self.usage_data_points[0], expected_point)
        self.assertEqual(self.mock_createDataPoint.call_count, 1)  # Only called for the minute point

    def test_extractDataPoints_historical_hour_day_collection(self):
        # Test historical hour and day collection
        self.mock_config['data']['detailedDataMinutesHistoryEnabled'] = False  # Disable minute history
        self.mock_config['data']['detailedDataSecondsEnabled'] = False  # Disable second history
        self.mock_getLastDBTimeStamp.return_value = (None, None, False)  # Minute history disabled

        mock_device = self._create_mock_device(12345, [('1,2,3', 0.01, None)])  # Minute usage (ignored)

        history_start = datetime.datetime(2024, 1, 8, 0, 0, 0, tzinfo=datetime.timezone.utc)
        history_end = datetime.datetime(2024, 1, 9, 0, 0, 0, tzinfo=datetime.timezone.utc)

        # Mock return values for get_chart_usage (hour and day scales)
        mock_hour_usage = [0.1, 0.2, None, 0.3]  # kWh per hour
        mock_hour_start_time = history_start
        mock_day_usage = [3.5, None, 4.0]  # kWh per day (include None to test missing lines 146-147)
        mock_day_start_time = history_start

        self.mock_account['vue'].get_chart_usage.side_effect = [
            (mock_hour_usage, mock_hour_start_time),  # Hour call
            (mock_day_usage, mock_day_start_time)  # Day call
        ]

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  False, self.usage_data_points, self.detailed_start_time_utc,
                                  pointType='History',  # Indicates history collection mode internally
                                  historyStartTimeUTC=history_start, historyEndTimeUTC=history_end)

        # Check get_chart_usage calls
        self.assertEqual(self.mock_account['vue'].get_chart_usage.call_count, 2)
        # Hour call
        self.mock_account['vue'].get_chart_usage.assert_any_call(
            mock_device.channels['1,2,3'], history_start, history_end,
            scale=Scale.HOUR.value, unit=Unit.KWH.value
        )
        # Day call
        self.mock_account['vue'].get_chart_usage.assert_any_call(
            mock_device.channels['1,2,3'], history_start, history_end,
            scale=Scale.DAY.value, unit=Unit.KWH.value
        )

        # 3 hour points + 2 day points
        self.assertEqual(len(self.usage_data_points), 5)

        # Check first hour point
        expected_watts_h0 = 0.1 * 1000
        expected_ts_h0 = mock_hour_start_time + datetime.timedelta(hours=0)
        self.assertIn((
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_h0, expected_ts_h0, 'Hours'
        ), self.usage_data_points)

        # Check second hour point
        expected_watts_h1 = 0.2 * 1000
        expected_ts_h1 = mock_hour_start_time + datetime.timedelta(hours=1)
        self.assertIn((
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_h1, expected_ts_h1, 'Hours'
        ), self.usage_data_points)

        # Check third hour point (index 3)
        expected_watts_h3 = 0.3 * 1000
        expected_ts_h3 = mock_hour_start_time + datetime.timedelta(hours=3)
        self.assertIn((
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_h3, expected_ts_h3, 'Hours'
        ), self.usage_data_points)

        # Check first day point
        expected_watts_d0 = 3.5 * 1000
        # convertToLocalDayInUTC mock returns start of the day UTC after adding 6 hours
        expected_ts_d0 = (mock_day_start_time + datetime.timedelta(hours=6, days=0))
        expected_ts_d0 = expected_ts_d0.replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertIn((
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_d0, expected_ts_d0, 'Days'
        ), self.usage_data_points)

        # Check second day point
        expected_watts_d1 = 4.0 * 1000
        # Use days=2 because index 1 was None
        expected_ts_d1 = (mock_day_start_time + datetime.timedelta(hours=6, days=2))
        expected_ts_d1 = expected_ts_d1.replace(hour=0, minute=0, second=0, microsecond=0)
        self.assertIn((
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_d1, expected_ts_d1, 'Days'
        ), self.usage_data_points)

        self.assertEqual(self.mock_createDataPoint.call_count, 5)
        self.mock_getLastDBTimeStamp.assert_not_called()  # Not called during history collection

    def test_extractDataPoints_nested_device(self):
        # Test handling of nested devices
        self.mock_config['data']['detailedDataMinutesHistoryEnabled'] = False
        self.mock_getLastDBTimeStamp.return_value = (None, None, False)

        # Create a device with a nested device
        nested_device_channels = [('NestedChan', 0.005, None)]  # 0.005 kWh
        mock_device = self._create_mock_device(12345, [('1,2,3', 0.01, {67890: nested_device_channels})])  # 0.01 kWh

        # Mock lookupDeviceName for the nested device
        self.mock_lookupDeviceName.side_effect = lambda acc, gid: 'NestedDevice' if gid == 67890 else 'TestDevice1'
        # Mock lookupChannelName for the nested channel
        self.mock_lookupChannelName.side_effect = self._mock_lookupChannelName  # Use the helper

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  False, self.usage_data_points, self.detailed_start_time_utc)

        self.assertEqual(len(self.usage_data_points), 2)  # One for main channel, one for nested

        # Check main channel point
        expected_watts_main = 0.01 * 60 * 1000
        expected_timestamp_main = self.stop_time_utc.replace(second=0, microsecond=0)
        self.assertIn((
            'TestAccount', 'TestDevice1', 'TestChannel1', expected_watts_main, expected_timestamp_main, 'Minutes'
        ), self.usage_data_points)

        # Check nested channel point
        expected_watts_nested = 0.005 * 60 * 1000
        expected_timestamp_nested = self.stop_time_utc.replace(second=0, microsecond=0)
        # Note: lookupDeviceName is called recursively, so it should pick up 'NestedDevice'
        self.assertIn((
            'TestAccount', 'NestedDevice', 'NestedChannel', expected_watts_nested, expected_timestamp_nested, 'Minutes'
        ), self.usage_data_points)

        self.assertEqual(self.mock_createDataPoint.call_count, 2)
        # getLastDBTimeStamp should be called for both main and nested (if history enabled, but here it's disabled)
        self.assertEqual(self.mock_getLastDBTimeStamp.call_count, 2)

    def test_extractDataPoints_excluded_channels(self):
        self.mock_getLastDBTimeStamp.reset_mock()  # Ensure clean state for this test
        # Test that excluded channels ('Balance', 'TotalUsage') are handled correctly
        self.mock_config['data']['detailedDataEnabled'] = True  # Enable seconds
        self.mock_config['data']['detailedDataMinutesHistoryEnabled'] = True  # Enable history
        self.mock_config['data']['detailedDataSecondsEnabled'] = True  # Enable seconds
        self.mock_getLastDBTimeStamp.return_value = (None, None, True)  # Assume history needed, but it will be skipped

        # These return values aren't used for this test, but need to return something
        mock_minute_usage = [0.005]  # kWh per minute
        mock_usage_start_time = self.stop_time_utc
        self.mock_account['vue'].get_chart_usage.return_value = (mock_minute_usage, mock_usage_start_time)

        # Ensure correct mock behavior for this test, overriding potential side_effect from previous tests
        self.mock_getLastDBTimeStamp.return_value = (None, None, True)  # Assume history needed, but it will be skipped

        mock_device = self._create_mock_device(12345, [
            ('1,2,3', 0.01, None),
            ('Balance', 2, None),
            ('TotalUsage', 3, None)
        ])

        collect.extractDataPoints(self.mock_config, self.mock_account, mock_device, self.stop_time_utc,
                                  True, self.usage_data_points, self.detailed_start_time_utc)  # collectDetails = True

        # getLastDBTimeStamp should only be called once for the non-excluded channel ('1,2,3') for minutes,
        # once for seconds (since collectDetails is True) for ('1,2,3'), and the once for minutes for the
        # other two excluded channels.
        self.assertEqual(self.mock_getLastDBTimeStamp.call_count, 4)
        self.mock_getLastDBTimeStamp.assert_any_call(
            self.mock_config, 'TestDevice1', 'TestChannel1', 'Minutes',
            self.stop_time_utc, self.stop_time_utc, False
        )
        self.mock_getLastDBTimeStamp.assert_any_call(
            self.mock_config, 'TestDevice1', 'TestChannel1', 'Seconds',
            self.detailed_start_time_utc, self.stop_time_utc, True
        )

        # get_chart_usage should only be called for the non-excluded channel ('1,2,3') for minutes and seconds
        self.assertEqual(self.mock_account['vue'].get_chart_usage.call_count, 2)  # Once for minutes history, once for seconds

        # createDataPoint should be called for the non-excluded channel (minutes + seconds)
        # AND for the excluded channels (basic minute data only)
        # 2 history/detail points for 1,2,3 + 2 basic points for Balance/Total
        self.assertEqual(self.mock_createDataPoint.call_count, 2 + 2)

        # Check points for excluded channels (basic minute data)
        expected_watts_balance = 2 * 60 * 1000
        expected_ts = self.stop_time_utc.replace(second=0, microsecond=0)
        self.assertIn((
            'TestAccount', 'TestDevice1', 'BalanceChannel', expected_watts_balance, expected_ts, 'Minutes'
        ), self.usage_data_points)

        expected_watts_total = 3 * 60 * 1000
        self.assertIn((
            'TestAccount', 'TestDevice1', 'TotalUsageChannel', expected_watts_total, expected_ts, 'Minutes'
        ), self.usage_data_points)

    # --- Tests for collectUsage ---

    @patch('vuegraf.collect.extractDataPoints')
    def test_collectUsage_hour_scale(self, mock_extractDataPoints):
        mock_device_usage = {12345: self._create_mock_device(12345, [('1,2,3', 0.1, None)])}
        self.mock_account['vue'].get_device_list_usage.return_value = mock_device_usage
        start_time = self.stop_time_utc - datetime.timedelta(hours=1)

        collect.collectUsage(self.mock_config, self.mock_account, start_time, self.stop_time_utc,
                             False, self.usage_data_points, self.detailed_start_time_utc, Scale.HOUR.value)

        self.mock_account['vue'].get_device_list_usage.assert_called_once_with(
            [12345], self.stop_time_utc, scale=Scale.HOUR.value, unit=Unit.KWH.value
        )
        mock_extractDataPoints.assert_called_once_with(
            self.mock_config,
            self.mock_account,
            mock_device_usage[12345],
            self.stop_time_utc,
            False,
            self.usage_data_points,
            self.detailed_start_time_utc,
            'Hours',
            start_time
        )

    @patch('vuegraf.collect.extractDataPoints')
    def test_collectUsage_day_scale(self, mock_extractDataPoints):
        mock_device_usage = {12345: self._create_mock_device(12345, [('1,2,3', 2.4, None)])}
        self.mock_account['vue'].get_device_list_usage.return_value = mock_device_usage
        start_time = self.stop_time_utc - datetime.timedelta(days=1)

        collect.collectUsage(self.mock_config, self.mock_account, start_time, self.stop_time_utc,
                             False, self.usage_data_points, self.detailed_start_time_utc, Scale.DAY.value)

        self.mock_account['vue'].get_device_list_usage.assert_called_once_with(
            [12345], self.stop_time_utc, scale=Scale.DAY.value, unit=Unit.KWH.value
        )
        mock_extractDataPoints.assert_called_once_with(
            self.mock_config,
            self.mock_account,
            mock_device_usage[12345],
            self.stop_time_utc,
            False,
            self.usage_data_points,
            self.detailed_start_time_utc,
            'Days',
            start_time
        )

    @patch('vuegraf.collect.extractDataPoints')
    def test_collectUsage_minute_scale(self, mock_extractDataPoints):
        # Minute scale is default (None passed in)
        mock_device_usage = {12345: self._create_mock_device(12345, [('1,2,3', 0.01, None)])}
        self.mock_account['vue'].get_device_list_usage.return_value = mock_device_usage
        start_time = self.stop_time_utc - datetime.timedelta(minutes=1)  # Not really used when scale is None

        collect.collectUsage(self.mock_config, self.mock_account, start_time, self.stop_time_utc,
                             True, self.usage_data_points, self.detailed_start_time_utc, Scale.MINUTE.value)  # Pass Minute scale

        self.mock_account['vue'].get_device_list_usage.assert_called_once_with(
            [12345], self.stop_time_utc, scale=Scale.MINUTE.value, unit=Unit.KWH.value
        )
        # pointType should be None when scale is Minute
        mock_extractDataPoints.assert_called_once_with(
            self.mock_config,
            self.mock_account,
            mock_device_usage[12345],
            self.stop_time_utc,
            True,
            self.usage_data_points,
            self.detailed_start_time_utc,
            None,
            start_time
        )

    @patch('vuegraf.collect.extractDataPoints')
    def test_collectUsage_no_usage_data(self, mock_extractDataPoints):
        self.mock_account['vue'].get_device_list_usage.return_value = None  # API returns None
        start_time = self.stop_time_utc - datetime.timedelta(hours=1)

        collect.collectUsage(self.mock_config, self.mock_account, start_time, self.stop_time_utc,
                             False, self.usage_data_points, self.detailed_start_time_utc, Scale.HOUR.value)

        self.mock_account['vue'].get_device_list_usage.assert_called_once_with(
            [12345], self.stop_time_utc, scale=Scale.HOUR.value, unit=Unit.KWH.value
        )
        mock_extractDataPoints.assert_not_called()  # Should not be called if no usage data

    # --- Tests for collectHistoryUsage ---

    @patch('vuegraf.collect.extractDataPoints')
    def test_collectHistoryUsage_single_batch(self, mock_extractDataPoints):
        history_start = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        history_stop = datetime.datetime(2024, 1, 8, 0, 0, 0, tzinfo=datetime.timezone.utc)  # Matches batchSizeDays=7

        # Mock calculateHistoryTimeRange to return one batch and then signal completion
        self.mock_calculateHistoryTimeRange.side_effect = [
            (history_start, history_stop),  # First batch
            (history_stop, history_stop)  # Completion signal
        ]

        # Mock base usage data needed by collectHistoryUsage
        mock_base_usage = {12345: self._create_mock_device(12345, [('1,2,3', 0.01, None)])}
        self.mock_account['vue'].get_device_list_usage.return_value = mock_base_usage

        mock_pause_event = MagicMock()
        mock_pause_event.wait.return_value = False  # Don't pause

        collect.collectHistoryUsage(self.mock_config, self.mock_account, history_start, history_stop,
                                    self.usage_data_points, mock_pause_event)

        # Check get_device_list_usage called once for base data
        self.mock_account['vue'].get_device_list_usage.assert_called_once_with(
             [12345], history_stop, scale=Scale.MINUTE.value, unit=Unit.KWH.value
        )

        # Check calculateHistoryTimeRange calls
        self.assertEqual(self.mock_calculateHistoryTimeRange.call_count, 2)
        self.mock_calculateHistoryTimeRange.assert_any_call(self.mock_config, history_stop, history_start, 0)  # Batch 0
        self.mock_calculateHistoryTimeRange.assert_any_call(self.mock_config, history_stop, history_start, 1)  # Batch 1 (completion)

        # Check extractDataPoints call for the batch
        mock_extractDataPoints.assert_called_once_with(
            self.mock_config,
            self.mock_account,
            mock_base_usage[12345],
            history_stop,
            False,
            self.usage_data_points,
            None,
            'History',
            history_start,
            history_stop
        )

        # Check pause event wait
        mock_pause_event.wait.assert_called_once_with(5)

    @patch('vuegraf.collect.extractDataPoints')
    def test_collectHistoryUsage_multiple_batches(self, mock_extractDataPoints):
        history_start = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        history_stop = datetime.datetime(2024, 1, 15, 0, 0, 0, tzinfo=datetime.timezone.utc)  # 14 days -> 2 batches

        # Mock calculateHistoryTimeRange for two batches + completion
        batch1_start, batch1_end = history_start, history_start + datetime.timedelta(days=7)
        batch2_start, batch2_end = batch1_end, history_stop
        self.mock_calculateHistoryTimeRange.side_effect = [
            (batch1_start, batch1_end),  # Batch 0
            (batch2_start, batch2_end),  # Batch 1
            (history_stop, history_stop)  # Completion signal
        ]

        mock_base_usage = {12345: self._create_mock_device(12345, [('1,2,3', 0.01, None)])}
        self.mock_account['vue'].get_device_list_usage.return_value = mock_base_usage

        mock_pause_event = MagicMock()
        mock_pause_event.wait.return_value = False

        collect.collectHistoryUsage(self.mock_config, self.mock_account, history_start, history_stop,
                                    self.usage_data_points, mock_pause_event)

        self.assertEqual(self.mock_calculateHistoryTimeRange.call_count, 3)
        self.assertEqual(mock_extractDataPoints.call_count, 2)
        self.assertEqual(mock_pause_event.wait.call_count, 2)

        # Check extractDataPoints calls for each batch
        mock_extractDataPoints.assert_any_call(
            self.mock_config,
            self.mock_account,
            mock_base_usage[12345],
            history_stop,
            False,
            self.usage_data_points,
            None,
            'History',
            batch1_start,
            batch1_end
        )
        mock_extractDataPoints.assert_any_call(
            self.mock_config,
            self.mock_account,
            mock_base_usage[12345],
            history_stop,
            False,
            self.usage_data_points,
            None,
            'History',
            batch2_start,
            batch2_end
        )

    @patch('vuegraf.collect.extractDataPoints')
    def test_collectHistoryUsage_pause_event(self, mock_extractDataPoints):
        history_start = datetime.datetime(2024, 1, 1, 0, 0, 0, tzinfo=datetime.timezone.utc)
        history_stop = datetime.datetime(2024, 1, 15, 0, 0, 0, tzinfo=datetime.timezone.utc)  # 14 days -> 2 batches

        batch1_start, batch1_end = history_start, history_start + datetime.timedelta(days=7)
        batch2_start, batch2_end = batch1_end, history_stop
        self.mock_calculateHistoryTimeRange.side_effect = [
            (batch1_start, batch1_end),  # Batch 0
            (batch2_start, batch2_end),  # Batch 1 (will be interrupted)
        ]

        mock_base_usage = {12345: self._create_mock_device(12345, [('1,2,3', 0.01, None)])}
        self.mock_account['vue'].get_device_list_usage.return_value = mock_base_usage

        mock_pause_event = MagicMock()
        # Pause after the first batch completes but abort after the second pause
        mock_pause_event.wait.side_effect = [False, True]

        collect.collectHistoryUsage(self.mock_config, self.mock_account, history_start, history_stop,
                                    self.usage_data_points, mock_pause_event)

        # Should calculate time for 2 batches
        self.assertEqual(self.mock_calculateHistoryTimeRange.call_count, 2)
        # Should only call extractDataPoints twice
        self.assertEqual(mock_extractDataPoints.call_count, 2)
        self.assertEqual(mock_pause_event.wait.call_count, 2)

        # Check extractDataPoints call for the first batch only
        mock_extractDataPoints.assert_any_call(
            self.mock_config,
            self.mock_account,
            mock_base_usage[12345],
            history_stop,
            False,
            self.usage_data_points,
            None,
            'History',
            batch1_start,
            batch1_end
        )

        mock_extractDataPoints.assert_any_call(
            self.mock_config,
            self.mock_account,
            mock_base_usage[12345],
            history_stop,
            False,
            self.usage_data_points,
            None,
            'History',
            batch2_start,
            batch2_end
        )
