# Copyright (c) Jason Ertel (jertel).
# This file is part of the Vuegraf project and is made available under the MIT License.

import copy
import datetime
import influxdb_client
from unittest.mock import MagicMock, patch
import pytest

# Local imports
from vuegraf import mqtt
from vuegraf.collect import Point
from vuegraf.time import getTimeNow

# Sample config for testing
SAMPLE_CONFIG_V2 = {
    'influxDb': {
        'version': 2,
        'url': 'http://localhost:8086',
        'bucket': 'vuegraf',
        'org': 'my-org',
        'token': 'my-token',
        'ssl_verify': True,
        'tagName': 'detail',
        'tagValue_second': '1s',
        'tagValue_minute': '1m',
        'tagValue_hour': '1h',
        'tagValue_day': '1d'
    },
    'addStationField': False,
    'detailedIntervalSecs': 3600,
    'args': MagicMock(debug=False, dryrun=False, resetdatabase=False)
}


def test_init_config_noop_if_absent():
  config = {}
  mqtt.initMqttConnectionIfConfigured(config)
  assert "mqtt" not in config


def test_init_config_error_missing_host():
  config = {
    "mqtt": {
      "port": 1,
    }
  }
  with pytest.raises(ValueError):
    mqtt.initMqttConnectionIfConfigured(config)


def test_init_config_error_missing_pw():
  config = {
    "mqtt": {
      "host": "unittest.mqtt.host",
      "username": "unittest_user",
    }
  }
  with pytest.raises(ValueError):
    mqtt.initMqttConnectionIfConfigured(config)


@patch('paho.mqtt.client.Client')
def test_init_config_minimal_valid(mock_client_cls):
  config = {
    "mqtt": {
      "host": "unittest.mqtt.host",
    }
  }
  mqtt.initMqttConnectionIfConfigured(config)
  mqttc = config["mqtt"]["client"]
  mqttc.connect.assert_called_once()

@patch('paho.mqtt.client.Client')
def test_init_config_complex_valid(mock_client_cls):
  config = {
    "mqtt": {
      "host": "unittest.mqtt.host",
      "port": 123,
      "username": "unittest_user",
      "pw": "unittest_pw",
    }
  }
  mqtt.initMqttConnectionIfConfigured(config)
  mqttc = config["mqtt"]["client"]
  mqttc.username_pw_set.assert_called_once_with("unittest_user", "unittest_pw")
  mqttc.connect.assert_called_once_with("unittest.mqtt.host", port=123)

