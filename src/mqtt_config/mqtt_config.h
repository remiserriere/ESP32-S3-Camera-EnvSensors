#pragma once
#include <cstdint>

// ─────────────────────────────────────────────────────────────────────────────
//  MQTT config channel
//
//  During the daily Wi-Fi session (photo task), the device connects to the MQTT
//  broker, subscribes to a *retained* config topic, and applies any overrides
//  that arrive to g_deviceConfig + NVS.
//
//  Topics (prefix = g_deviceConfig.mqttClientId):
//
//    Subscribe (retained):  <clientId>/config/set
//      Payload: JSON object with any subset of config keys (see README).
//      Publish a retained message from Home Assistant or any MQTT client to
//      reconfigure the device on its next daily wake-up.
//
//    Publish (status):      <clientId>/config/status
//      Payload: JSON object echoing the applied config + firmware version.
//      Published after successfully applying a config update.
//
//    Publish (telemetry):   <clientId>/telemetry
//      Payload: JSON object with last sensor readings (optional, future use).
//
//  Call mqtt_config::syncFromBroker() once per Wi-Fi session (e.g. after OTA
//  check and before the camera is initialised).  The function is non-blocking:
//  it connects, waits at most MQTT_CONFIG_TIMEOUT_MS for the retained message,
//  applies changes if any arrived, and disconnects.
//
//  If the broker is unreachable or mqttEnabled is false the function is a no-op.
// ─────────────────────────────────────────────────────────────────────────────

namespace mqtt_config {

    // Connect to broker, receive retained config topic, apply + save to NVS.
    // Returns true if a config update was received and applied.
    bool syncFromBroker();

} // namespace mqtt_config
