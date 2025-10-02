"""Send Vue usage data to an MQTT pubsub.

Uses the Eclipse paho-mqtt client:
https://eclipse.dev/paho/files/paho.mqtt.python/html/client.html
"""

from paho.mqtt import client

import logging


logger = logging.getLogger('vuegraf.mqtt')


def initMqttConnectionIfConfigured(config) -> None:
  mqtt_config = config.get("mqtt", {})
  if not mqtt_config:
    logger.info("No MQTT client config, skipping setup.")
    return
  mqtt_host = mqtt_config.get("host")
  if not mqtt_host:
    raise ValueError("Missing required \"host\" key within MQTT section.")
  port = mqtt_config.get("port", 1883)
  username = mqtt_config.get("username")
  password = mqtt_config.get("pw")
  if bool(username) != bool(password):
    raise ValueError(
        "MQTT config section contains one of username/pw but not the other."
    )
  topic = mqtt_config.get("topic")
  if not topic:
    topic = "vuegraf/topic"
    # Write back to the config so the publish call has access.
    mqtt_config["topic"] = topic

  mqttc = client.Client(client_id="vuegraf")
  mqttc.enable_logger()
  if username:
    mqttc.username_pw_set(username, password)
  mqttc.connect(mqtt_host, port=port)
  # Start a background thread to run the MQTT network loop.
  mqttc.loop_start()
  mqtt_config["client"] = mqttc
  logger.info(f"MQTT client set up to publish to {mqtt_host} on {topic}.")


def publishMqttMessagesIfConnected(config, usageDataPoints) -> None:
  mqttc = config.get("mqtt", {}).get("client")
  if not mqttc:
    logger.info("No MQTT client, skipping publish.")
    return
  topic = config["mqtt"]["topic"]

  # Use the default fire-and-forget QOS of 0, since we expect to send frequently
  # as Vue power values are updated.
  logger.info(f"MQTT client publish\n{usageDataPoints[0].to_line_protocol()}")
  msg_infos = []
  for point in usageDataPoints:
    msg_infos.append(mqttc.publish(topic, point.to_line_protocol()))
  for msg_info in msg_infos:
    msg_info.wait_for_publish()


def stopMqttIfConnected(config) -> None:
  mqttc = config.get("mqtt", {}).get("client")
  if not mqttc:
    logger.info("No MQTT client, skipping disconnect.")
    return

  mqttc.disconnect()  # also stops background thread
  logger.info("MQTT client disconnected.")
