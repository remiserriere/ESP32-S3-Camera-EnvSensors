#include "mqtt_config.h"
#include "../device_config.h"
#include "../version.h"
#include <Arduino.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <string.h>

// ─────────────────────────────────────────────────────────────────────────────
//  Constants
// ─────────────────────────────────────────────────────────────────────────────

static constexpr uint32_t MQTT_CONFIG_TIMEOUT_MS  = 5000;  // wait for retained msg

// ─────────────────────────────────────────────────────────────────────────────
//  Module-level state (valid only during syncFromBroker())
// ─────────────────────────────────────────────────────────────────────────────

static WiFiClient   s_wifiClient;
static PubSubClient s_mqtt(s_wifiClient);

static bool   s_configReceived = false;
static String s_configPayload;

// ─────────────────────────────────────────────────────────────────────────────
//  Hand-rolled JSON helpers (avoids pulling in a heavy JSON library)
// ─────────────────────────────────────────────────────────────────────────────

// Returns the raw string value of `key` from a flat JSON object, or "" if absent.
static String jsonStr(const String& json, const char* key) {
    String needle = String("\"") + key + "\":\"";
    int start = json.indexOf(needle);
    if (start < 0) return "";
    start += needle.length();
    int end = json.indexOf('"', start);
    return (end < 0) ? "" : json.substring(start, end);
}

// Returns the numeric value of `key` (int or float represented as string),
// or `defaultVal` if the key is absent.
static int jsonInt(const String& json, const char* key, int defaultVal) {
    String needle = String("\"") + key + "\":";
    int start = json.indexOf(needle);
    if (start < 0) return defaultVal;
    start += needle.length();
    // Skip whitespace
    while (start < (int)json.length() && json[start] == ' ') start++;
    // Check for "true"/"false" booleans
    if (json.substring(start, start + 4) == "true")  return 1;
    if (json.substring(start, start + 5) == "false") return 0;
    return json.substring(start).toInt();
}

// Returns true/false for a JSON boolean field; `defaultVal` if absent.
static bool jsonBool(const String& json, const char* key, bool defaultVal) {
    String needle = String("\"") + key + "\":";
    int start = json.indexOf(needle);
    if (start < 0) return defaultVal;
    start += needle.length();
    while (start < (int)json.length() && json[start] == ' ') start++;
    String rest = json.substring(start, start + 5);
    if (rest.startsWith("true"))  return true;
    if (rest.startsWith("false")) return false;
    return (bool)json.substring(start).toInt();
}

// ─────────────────────────────────────────────────────────────────────────────
//  Apply incoming JSON config payload to g_deviceConfig
// ─────────────────────────────────────────────────────────────────────────────

static bool applyConfig(const String& json) {
    bool changed = false;

    // ── Sensors ────────────────────────────────────────────────────────────
    auto tryBool = [&](const char* key, bool& field) {
        String needle = String("\"") + key + "\":";
        if (json.indexOf(needle) >= 0) { field = jsonBool(json, key, field); changed = true; }
    };
    auto tryU8 = [&](const char* key, uint8_t& field, uint8_t lo, uint8_t hi) {
        String needle = String("\"") + key + "\":";
        if (json.indexOf(needle) >= 0) {
            int v = jsonInt(json, key, (int)field);
            if (v >= lo && v <= hi) { field = (uint8_t)v; changed = true; }
            else Serial.printf("[MQTT-CFG] ! '%s'=%d out of range [%d,%d] – ignored\n", key, v, lo, hi);
        }
    };
    auto tryU16 = [&](const char* key, uint16_t& field, uint16_t lo, uint16_t hi) {
        String needle = String("\"") + key + "\":";
        if (json.indexOf(needle) >= 0) {
            int v = jsonInt(json, key, (int)field);
            if (v >= lo && v <= hi) { field = (uint16_t)v; changed = true; }
            else Serial.printf("[MQTT-CFG] ! '%s'=%d out of range [%d,%d] – ignored\n", key, v, lo, hi);
        }
    };
    auto tryStr = [&](const char* key, char* buf, size_t maxLen) {
        String val = jsonStr(json, key);
        if (!val.isEmpty()) {
            strncpy(buf, val.c_str(), maxLen - 1);
            buf[maxLen - 1] = '\0';
            changed = true;
        }
    };

    tryBool("ds18_en",  g_deviceConfig.ds18b20Enabled);
    tryU8  ("ds18_int", g_deviceConfig.ds18b20IntervalMin, 1, 240);

    tryBool("sht_en",   g_deviceConfig.sht3xEnabled);
    tryU8  ("sht_int",  g_deviceConfig.sht3xIntervalMin,   1, 240);

    tryBool("ina_en",   g_deviceConfig.ina219Enabled);
    tryU8  ("ina_int",  g_deviceConfig.ina219IntervalMin,  1, 240);

    // ── Photo schedule ─────────────────────────────────────────────────────
    tryU8("ph_hour", g_deviceConfig.photoHour,      0,  23);
    tryU8("ph_min",  g_deviceConfig.photoMinute,     0,  59);
    tryU8("ph_win",  g_deviceConfig.photoWindowMin,  1,  60);

    // ── Network ────────────────────────────────────────────────────────────
    tryStr("wifi_ssid",  g_deviceConfig.wifiSsid,       sizeof(g_deviceConfig.wifiSsid));
    tryStr("wifi_pass",  g_deviceConfig.wifiPassword,   sizeof(g_deviceConfig.wifiPassword));
    tryStr("upload_ep",  g_deviceConfig.uploadEndpoint, sizeof(g_deviceConfig.uploadEndpoint));

    // ── OTA ────────────────────────────────────────────────────────────────
    tryBool("ota_en",  g_deviceConfig.otaEnabled);
    tryStr ("ota_url", g_deviceConfig.otaManifestUrl, sizeof(g_deviceConfig.otaManifestUrl));

    // ── MQTT ───────────────────────────────────────────────────────────────
    tryBool ("mqtt_en",   g_deviceConfig.mqttEnabled);
    tryStr  ("mqtt_host", g_deviceConfig.mqttBroker,    sizeof(g_deviceConfig.mqttBroker));
    tryU16  ("mqtt_port", g_deviceConfig.mqttPort,       1, 65535);
    tryStr  ("mqtt_user", g_deviceConfig.mqttUser,      sizeof(g_deviceConfig.mqttUser));
    tryStr  ("mqtt_pass", g_deviceConfig.mqttPassword,  sizeof(g_deviceConfig.mqttPassword));
    tryStr  ("mqtt_id",   g_deviceConfig.mqttClientId,  sizeof(g_deviceConfig.mqttClientId));

    // ── BLE name ───────────────────────────────────────────────────────────
    tryStr("dev_name", g_deviceConfig.deviceName, sizeof(g_deviceConfig.deviceName));

    // ── Boot options ───────────────────────────────────────────────────────
    tryBool("cb_photo_en", g_deviceConfig.coldBootPhotoEn);
    tryBool("diag_en",     g_deviceConfig.diagEn);

    return changed;
}

// ─────────────────────────────────────────────────────────────────────────────
//  MQTT callback – called by PubSubClient when a message arrives
// ─────────────────────────────────────────────────────────────────────────────

static void onMessage(char* topic, byte* payload, unsigned int length) {
    s_configPayload = String((char*)payload, length);
    s_configReceived = true;
    Serial.printf("[MQTT-CFG] Received config payload (%u bytes) on %s\n", length, topic);
}

// ─────────────────────────────────────────────────────────────────────────────
//  Publish status ACK
// ─────────────────────────────────────────────────────────────────────────────

static void publishStatus(bool updated) {
    char topic[96];
    snprintf(topic, sizeof(topic), "%s/config/status", g_deviceConfig.mqttClientId);

    // Build a compact JSON status message
    char payload[256];
    snprintf(payload, sizeof(payload),
             "{\"fw\":\"%s\",\"updated\":%s,\"ds18_en\":%s,\"sht_en\":%s,"
             "\"ina_en\":%s,\"ph_hour\":%d,\"ph_min\":%d}",
             FIRMWARE_VERSION,
             updated ? "true" : "false",
             g_deviceConfig.ds18b20Enabled ? "true" : "false",
             g_deviceConfig.sht3xEnabled   ? "true" : "false",
             g_deviceConfig.ina219Enabled  ? "true" : "false",
             g_deviceConfig.photoHour,
             g_deviceConfig.photoMinute);

    s_mqtt.publish(topic, payload, false /* not retained */);
    Serial.printf("[MQTT-CFG] Status published to %s\n", topic);
}

// ─────────────────────────────────────────────────────────────────────────────
//  Public API
// ─────────────────────────────────────────────────────────────────────────────

bool mqtt_config::syncFromBroker() {
    if (!g_deviceConfig.mqttEnabled) return false;
    if (g_deviceConfig.mqttBroker[0] == '\0') {
        Serial.println("[MQTT-CFG] No broker configured – skipping");
        return false;
    }

    // Wi-Fi must already be connected (called during photo session)
    if (WiFi.status() != WL_CONNECTED) {
        Serial.println("[MQTT-CFG] Wi-Fi not connected – skipping");
        return false;
    }

    Serial.printf("[MQTT-CFG] Connecting to %s:%u as '%s'\n",
                  g_deviceConfig.mqttBroker,
                  g_deviceConfig.mqttPort,
                  g_deviceConfig.mqttClientId);

    s_mqtt.setServer(g_deviceConfig.mqttBroker, g_deviceConfig.mqttPort);
    s_mqtt.setCallback(onMessage);
    s_mqtt.setBufferSize(512);   // enough for the config payload

    s_configReceived = false;
    s_configPayload  = "";

    // Connect (with optional credentials)
    bool connected;
    if (g_deviceConfig.mqttUser[0] != '\0') {
        connected = s_mqtt.connect(g_deviceConfig.mqttClientId,
                                   g_deviceConfig.mqttUser,
                                   g_deviceConfig.mqttPassword);
    } else {
        connected = s_mqtt.connect(g_deviceConfig.mqttClientId);
    }

    if (!connected) {
        Serial.printf("[MQTT-CFG] Connection failed (state=%d)\n", s_mqtt.state());
        return false;
    }

    Serial.println("[MQTT-CFG] Connected");

    // Subscribe to the retained config topic
    char subTopic[96];
    snprintf(subTopic, sizeof(subTopic), "%s/config/set", g_deviceConfig.mqttClientId);
    s_mqtt.subscribe(subTopic);
    Serial.printf("[MQTT-CFG] Subscribed to %s\n", subTopic);

    // Poll briefly – a retained message arrives almost immediately
    uint32_t deadline = millis() + MQTT_CONFIG_TIMEOUT_MS;
    while (!s_configReceived && millis() < deadline) {
        s_mqtt.loop();
        delay(20);
    }

    bool updated = false;

    if (s_configReceived) {
        Serial.println("[MQTT-CFG] Applying config...");
        bool changed = applyConfig(s_configPayload);
        if (changed) {
            device_config::save();
            device_config::print();
            updated = true;
        } else {
            Serial.println("[MQTT-CFG] No recognised keys in payload – nothing changed");
        }
    } else {
        Serial.println("[MQTT-CFG] No retained config message (topic may be empty – that's fine)");
    }

    publishStatus(updated);

    s_mqtt.unsubscribe(subTopic);
    s_mqtt.disconnect();
    Serial.println("[MQTT-CFG] Disconnected");

    return updated;
}

bool mqtt_config::publishAlive() {
    if (!g_deviceConfig.mqttEnabled) return false;
    if (g_deviceConfig.mqttBroker[0] == '\0') return false;
    if (WiFi.status() != WL_CONNECTED) return false;

    s_mqtt.setServer(g_deviceConfig.mqttBroker, g_deviceConfig.mqttPort);
    s_mqtt.setBufferSize(256);

    bool connected;
    if (g_deviceConfig.mqttUser[0] != '\0') {
        connected = s_mqtt.connect(g_deviceConfig.mqttClientId,
                                   g_deviceConfig.mqttUser,
                                   g_deviceConfig.mqttPassword);
    } else {
        connected = s_mqtt.connect(g_deviceConfig.mqttClientId);
    }

    if (!connected) {
        Serial.printf("[MQTT-CFG] publishAlive: connexion échouée (state=%d)\n", s_mqtt.state());
        return false;
    }

    char topic[96];
    snprintf(topic, sizeof(topic), "%s/alive", g_deviceConfig.mqttClientId);

    char payload[128];
    snprintf(payload, sizeof(payload),
             "{\"fw\":\"%s\",\"uptime_s\":%lu}",
             FIRMWARE_VERSION, millis() / 1000UL);

    bool ok = s_mqtt.publish(topic, payload, false);
    s_mqtt.disconnect();
    Serial.printf("[MQTT-CFG] publishAlive → %s : %s\n", topic, ok ? "OK" : "échec publish");
    return ok;
}
