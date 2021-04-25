# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0.

import argparse
from awscrt import io, mqtt, auth, http
from awsiot import mqtt_connection_builder
import sys
import threading
import time
import json

actions = {
  'lightOn': 'light.on',
  'lightOff': 'light.off',
  'noop': 'noop',
}

parser = argparse.ArgumentParser(description="Send and receive messages through and MQTT connection.")
parser.add_argument('--endpoint', required=True, help="Your AWS IoT custom endpoint, not including a port. " +
                            "Ex: \"abcd123456wxyz-ats.iot.us-east-1.amazonaws.com\"")
parser.add_argument('--cert', help="File path to your client certificate, in PEM format.")
parser.add_argument('--key', help="File path to your private key, in PEM format.")
parser.add_argument('--root-ca', help="File path to root certificate authority, in PEM format. " +
                    "Necessary if MQTT server uses a certificate that's not already in " +
                    "your trust store.")
parser.add_argument('--client-id', default="gardenClient", help="Client ID for MQTT connection.")
parser.add_argument('--verbosity', choices=[x.name for x in io.LogLevel], default=io.LogLevel.NoLogs.name,
  help='Logging level')
parser.add_argument('--action', choices=[x for x in actions.values()], default=io.LogLevel.NoLogs.name,
  help='Specify controller action.')

args = parser.parse_args()

io.init_logging(getattr(io.LogLevel, args.verbosity), 'stderr')

# Callback when connection is accidentally lost.
def on_connection_interrupted(connection, error, **kwargs):
  print("Connection interrupted. error: {}".format(error))

# Callback when an interrupted connection is re-established.
def on_connection_resumed(connection, return_code, session_present, **kwargs):
  print("Connection resumed. return_code: {} session_present: {}".format(return_code, session_present))
  resubscribe_future, _ = connection.resubscribe_existing_topics()
  resubscribe_future.add_done_callback(on_resubscribe_complete)

  if return_code == mqtt.ConnectReturnCode.ACCEPTED and not session_present:
    print("Session did not persist. Resubscribing to existing topics...")
    resubscribe_future, _ = connection.resubscribe_existing_topics()
    resubscribe_future.add_done_callback(on_resubscribe_complete)

def on_resubscribe_complete(resubscribe_future):
    resubscribe_results = resubscribe_future.result()
    print("Resubscribe results: {}".format(resubscribe_results))

    for topic, qos in resubscribe_results['topics']:
      if qos is None:
        sys.exit("Server rejected resubscribe to topic: {}".format(topic))

def on_sensor_payload_received(topic, payload):
  print("New sensorData payload:\n{}".format(payload))

if __name__ == '__main__':
  # Spin up networking resources
  event_loop_group = io.EventLoopGroup(1)
  host_resolver = io.DefaultHostResolver(event_loop_group)
  client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)

  mqtt_connection = mqtt_connection_builder.mtls_from_path(
    endpoint=args.endpoint,
    cert_filepath=args.cert,
    pri_key_filepath=args.key,
    client_bootstrap=client_bootstrap,
    ca_filepath=args.root_ca,
    on_connection_interrupted=on_connection_interrupted,
    on_connection_resumed=on_connection_resumed,
    client_id=args.client_id,
    clean_session=False,
    keep_alive_secs=6)

  print("Connecting to {} with client ID '{}'...".format(
    args.endpoint, args.client_id))
  connect_future = mqtt_connection.connect()
  connect_future.result()
  print("Connected!")

  # Subscribe
  print("Subscribing to topic garden/sensorData...")
  subscribe_future, packet_id = mqtt_connection.subscribe(
    topic="garden/sensorData",
    qos=mqtt.QoS.AT_LEAST_ONCE,
    callback=on_sensor_payload_received
  )

  subscribe_result = subscribe_future.result()
  print("Subscribed with {}".format(str(subscribe_result['qos'])))

  if (args.action == actions['lightOn']):
    print("Publishing message to topic garden/lightStatus...")
    mqtt_connection.publish(
      topic='garden/lightStatus',
      payload=json.dumps({
        'on': True,
      }),
      qos=mqtt.QoS.AT_LEAST_ONCE
    )
  elif (args.action == actions['lightOff']):
    print("Publishing message to topic garden/lightStatus...")
    mqtt_connection.publish(
      topic='garden/lightStatus',
      payload=json.dumps({
        'on': False,
      }),
      qos=mqtt.QoS.AT_LEAST_ONCE
    )

  # Disconnect
  print("Disconnecting...")
  disconnect_future = mqtt_connection.disconnect()
  disconnect_future.result()
  print("Disconnected!")
