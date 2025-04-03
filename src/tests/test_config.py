# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

import pytest
import argparse
import logging
from unittest.mock import patch, MagicMock, mock_open

# Local imports
from vuegraf import config


def test_set_config_default_new_key():
    """Test setConfigDefault when the key doesn't exist."""
    test_config = {}
    config.setConfigDefault(test_config, 'new_key', 'default_value')
    assert test_config['new_key'] == 'default_value'


def test_set_config_default_existing_key():
    """Test setConfigDefault when the key already exists."""
    test_config = {'existing_key': 'original_value'}
    config.setConfigDefault(test_config, 'existing_key', 'default_value')
    assert test_config['existing_key'] == 'original_value'


def test_get_config_value():
    """Test getConfigValue."""
    test_config = {'key': 'value'}
    assert config.getConfigValue(test_config, 'key') == 'value'


def test_get_config_value_missing_key():
    """Test getConfigValue when the key is missing."""
    test_config = {}
    with pytest.raises(KeyError):
        config.getConfigValue(test_config, 'missing_key')


def test_get_influx_version_default():
    """Test getInfluxVersion when version is not specified."""
    test_config = {'influxDb': {}}
    assert config.getInfluxVersion(test_config) == 1


def test_get_influx_version_specified():
    """Test getInfluxVersion when version is specified."""
    test_config = {'influxDb': {'version': 2}}
    assert config.getInfluxVersion(test_config) == 2


def test_get_influx_tag_defaults():
    """Test getInfluxTag with default values."""
    test_config = {'influxDb': {}}
    expected_tags = ('detailed', 'True', 'False', 'Hour', 'Day')
    assert config.getInfluxTag(test_config) == expected_tags


def test_get_influx_tag_specified():
    """Test getInfluxTag with specified values."""
    test_config = {
        'influxDb': {
            'tagName': 'custom_tag',
            'tagValue_second': 'false',
            'tagValue_minute': 'true',
            'tagValue_hour': 'hr',
            'tagValue_day': 'dy'
        }
    }
    expected_tags = ('custom_tag', 'false', 'true', 'hr', 'dy')
    assert config.getInfluxTag(test_config) == expected_tags


# Tests for initArgs
@patch('argparse.ArgumentParser.parse_args')
def test_init_args_defaults(mock_parse_args):
    """Test initArgs with default arguments."""
    # Simulate providing only the required config filename
    mock_parse_args.return_value = argparse.Namespace(
        configFilename='test.json',
        verbose=False,
        debug=False,
        historydays=0,
        resetdatabase=False,
        dryrun=False
    )
    args = config.initArgs()
    assert args.configFilename == 'test.json'
    assert not args.verbose
    assert not args.debug
    assert args.historydays == 0
    assert not args.resetdatabase
    assert not args.dryrun


@patch('argparse.ArgumentParser.parse_args')
def test_init_args_custom(mock_parse_args):
    """Test initArgs with custom arguments."""
    # Simulate providing custom arguments
    mock_parse_args.return_value = argparse.Namespace(
        configFilename='custom.json',
        verbose=True,
        debug=True,
        historydays=30,
        resetdatabase=True,
        dryrun=True
    )
    args = config.initArgs()
    assert args.configFilename == 'custom.json'
    assert args.verbose
    assert args.debug
    assert args.historydays == 30
    assert args.resetdatabase
    assert args.dryrun


# Tests for initLogging
@patch('logging.getLogger')
@patch('logging.StreamHandler')
@patch('logging.Formatter')
def test_init_logging_verbose(mock_formatter, mock_handler, mock_get_logger):
    """Test initLogging with verbose=True."""
    mock_logger = MagicMock()
    mock_get_logger.return_value = mock_logger
    mock_stream_handler_instance = MagicMock()
    mock_handler.return_value = mock_stream_handler_instance
    mock_formatter_instance = MagicMock()
    mock_formatter.return_value = mock_formatter_instance

    config.initLogging(True)

    mock_get_logger.assert_called_once_with('vuegraf')
    mock_logger.setLevel.assert_called_once_with(logging.DEBUG)
    mock_handler.assert_called_once_with()
    mock_formatter.assert_called_once_with('%(asctime)s | %(levelname)-5s | %(message)s')
    assert hasattr(mock_formatter_instance, 'converter')  # Check if converter was set
    mock_stream_handler_instance.setFormatter.assert_called_once_with(mock_formatter_instance)
    mock_logger.addHandler.assert_called_once_with(mock_stream_handler_instance)


@patch('logging.getLogger')
@patch('logging.StreamHandler')
@patch('logging.Formatter')
def test_init_logging_not_verbose(mock_formatter, mock_handler, mock_get_logger):
    """Test initLogging with verbose=False."""
    mock_logger = MagicMock()
    mock_get_logger.return_value = mock_logger
    mock_stream_handler_instance = MagicMock()
    mock_handler.return_value = mock_stream_handler_instance
    mock_formatter_instance = MagicMock()
    mock_formatter.return_value = mock_formatter_instance

    config.initLogging(False)

    mock_get_logger.assert_called_once_with('vuegraf')
    mock_logger.setLevel.assert_called_once_with(logging.INFO)
    mock_handler.assert_called_once_with()
    mock_formatter.assert_called_once_with('%(asctime)s | %(levelname)-5s | %(message)s')
    assert hasattr(mock_formatter_instance, 'converter')  # Check if converter was set
    mock_stream_handler_instance.setFormatter.assert_called_once_with(mock_formatter_instance)
    mock_logger.addHandler.assert_called_once_with(mock_stream_handler_instance)


# Tests for initConfig
@patch('vuegraf.config.initArgs')
@patch('builtins.open', new_callable=mock_open,
       read_data='{"influxDb": {"host": "localhost"}, "accounts": [{"email": "test@example.com"}]}')
@patch('json.load')
@patch('vuegraf.config.initLogging')
@patch('vuegraf.config.logger')  # Patch the logger instance used within initConfig
def test_init_config_basic(mock_config_logger, mock_init_logging, mock_json_load, mock_file_open, mock_init_args):
    """Test initConfig with basic settings and verbose=False."""
    # Mock return values
    mock_args = argparse.Namespace(
        configFilename='test.json',
        verbose=False,
        debug=False,
        historydays=0,
        resetdatabase=False,
        dryrun=False
    )
    mock_init_args.return_value = mock_args
    mock_loaded_config = {"influxDb": {"host": "localhost"}, "accounts": [{"email": "test@example.com"}]}
    mock_json_load.return_value = mock_loaded_config
    mock_logger_instance = MagicMock()
    mock_init_logging.return_value = mock_logger_instance  # initLogging returns the logger instance

    # Call the function
    config_result = config.initConfig()

    # Assertions
    mock_init_args.assert_called_once()
    mock_file_open.assert_called_once_with('test.json')
    mock_json_load.assert_called_once_with(mock_file_open())
    mock_init_logging.assert_called_once_with(False)  # verbose is False

    # Check default values were set
    assert config_result['addStationField'] is False
    assert config_result['detailedIntervalSecs'] == 3600
    assert config_result['lagSecs'] == 5
    assert config_result['timezone'] is None
    assert config_result['maxHistoryDays'] == 720
    assert config_result['updateIntervalSecs'] == 60

    # Check args and logger are stored
    assert config_result['args'] == mock_args
    assert config_result['logger'] == mock_logger_instance

    # Check sensitive info removed from logged config string
    mock_config_logger.info.assert_called_once()
    log_message = mock_config_logger.info.call_args[0][0]
    assert 'influxDb' not in log_message
    assert 'accounts' not in log_message
    assert "'addStationField': False" in log_message  # Check a default value is logged


@patch('vuegraf.config.initArgs')
@patch('builtins.open', new_callable=mock_open,
       read_data='{"influxDb": {"host": "remote"}, "timezone": "UTC", "detailedDataEnabled": true}')
@patch('json.load')
@patch('vuegraf.config.initLogging')
@patch('vuegraf.config.logger')  # Patch the logger instance used within initConfig
def test_init_config_custom_verbose(mock_config_logger, mock_init_logging, mock_json_load, mock_file_open, mock_init_args):
    """Test initConfig with custom settings and verbose=True."""
    # Mock return values
    mock_args = argparse.Namespace(
        configFilename='custom.json',
        verbose=True,  # Set verbose to True
        debug=False,
        historydays=10,
        resetdatabase=False,
        dryrun=True
    )
    mock_init_args.return_value = mock_args
    # Simulate config file with some overrides
    mock_loaded_config = {"influxDb": {"host": "remote"}, "timezone": "UTC", "detailedDataEnabled": True}
    mock_json_load.return_value = mock_loaded_config
    mock_logger_instance = MagicMock()
    mock_init_logging.return_value = mock_logger_instance

    # Call the function
    config_result = config.initConfig()

    # Assertions
    mock_init_args.assert_called_once()
    mock_file_open.assert_called_once_with('custom.json')
    mock_json_load.assert_called_once_with(mock_file_open())
    mock_init_logging.assert_called_once_with(True)  # verbose is True

    # Check values from config file override defaults
    assert config_result['timezone'] == "UTC"
    assert config_result['detailedDataEnabled'] is True

    # Check default values that weren't overridden
    assert config_result['addStationField'] is False
    assert config_result['updateIntervalSecs'] == 60

    # Check args and logger are stored
    assert config_result['args'] == mock_args
    assert config_result['logger'] == mock_logger_instance

    # Check logging call
    mock_config_logger.info.assert_called_once()
    log_message = mock_config_logger.info.call_args[0][0]
    assert "'timezone': 'UTC'" in log_message
    assert "'detailedDataEnabled': True" in log_message
