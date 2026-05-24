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
static constexpr uint32_t MQTT_BUFFER_SIZE        = 1024;  // must be enough for the entire config payload

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
static String jsonStr_old(const String& json, const char* key) {
    String needle = String("\"") + key + "\":\"";
    int start = json.indexOf(needle);
    if (start < 0) return "";
    start += needle.length();
    int end = json.indexOf('"', start);
    return (end < 0) ? "" : json.substring(start, end);
}
static String jsonStr(const String& json, const char* key) {
    String needle = String("\"") + key + "\"";
    int pos = json.indexOf(needle);
    if (pos < 0) return "";

    pos = json.indexOf(':', pos);
    if (pos < 0) return "";

    pos++;

    while (pos < json.length() && isspace(json[pos]))
        pos++;

    if (json[pos] != '"')
        return "";

    pos++;

    int end = json.indexOf('"', pos);
    if (end < 0) return "";

    return json.substring(pos, end);
}

// Returns the numeric value of `key` (int or float represented as string),
// or `defaultVal` if the key is absent.
static int jsonInt_old(const String& json, const char* key, int defaultVal) {
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
static int jsonInt(const String& json, const char* key, int defaultVal) {
    String needle = String("\"") + key + "\"";

    int pos = json.indexOf(needle);
    if (pos < 0)
        return defaultVal;

    // Find ':' after the key
    pos = json.indexOf(':', pos);
    if (pos < 0)
        return defaultVal;

    pos++;

    // Skip whitespace
    while (pos < (int)json.length() && isspace((unsigned char)json[pos]))
        pos++;

    // Handle booleans
    if (json.substring(pos, pos + 4) == "true")
        return 1;

    if (json.substring(pos, pos + 5) == "false")
        return 0;

    // Extract numeric token
    int end = pos;

    while (end < (int)json.length()) {
        char c = json[end];

        if (!(isdigit((unsigned char)c) || c == '-' || c == '+'))
            break;

        end++;
    }

    if (end == pos)
        return defaultVal;

    return json.substring(pos, end).toInt();
}

// Returns true/false for a JSON boolean field; `defaultVal` if absent.
static bool jsonBool_old(const String& json, const char* key, bool defaultVal) {
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
static bool jsonBool(const String& json, const char* key, bool defaultVal) {
    String needle = String("\"") + key + "\"";

    int pos = json.indexOf(needle);
    if (pos < 0)
        return defaultVal;

    // Find ':' after the key
    pos = json.indexOf(':', pos);
    if (pos < 0)
        return defaultVal;

    pos++;

    // Skip whitespace
    while (pos < (int)json.length() && isspace((unsigned char)json[pos]))
        pos++;

    if (json.substring(pos, pos + 4) == "true")
        return true;

    if (json.substring(pos, pos + 5) == "false")
        return false;

    // Also support numeric bools
    if (json[pos] == '1')
        return true;

    if (json[pos] == '0')
        return false;

    return defaultVal;
}

// ─────────────────────────────────────────────────────────────────────────────
// Print received payload on Serial with line indent for debugging
// ─────────────────────────────────────────────────────────────────────────────

static void printPayload(const String& payload) {
    Serial.println("[MQTT-CFG] Payload received:");
    int idx = 0, lineStart = 0;
    while (idx < (int)payload.length()) {
        if (payload[idx] == '\\' && idx + 1 < (int)payload.length()) {
            if (payload[idx + 1] == 'n') {
                Serial.println(payload.substring(lineStart, idx));
                lineStart = idx + 2;
                idx += 2;
                continue;
            } else if (payload[idx + 1] == 't') {
                Serial.print("    "); // indent for tabs
                idx += 2;
                continue;
            }
        }
        idx++;
    }
    if (lineStart < (int)payload.length()) {
        Serial.println(payload.substring(lineStart));
    }
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
            else Serial.printf("[MQTT-CFG] ! '%s'=%d out of range [%d,%d] – ignored\r\n", key, v, lo, hi);
        }
    };
    auto tryU16 = [&](const char* key, uint16_t& field, uint16_t lo, uint16_t hi) {
        String needle = String("\"") + key + "\":";
        if (json.indexOf(needle) >= 0) {
            int v = jsonInt(json, key, (int)field);
            if (v >= lo && v <= hi) { field = (uint16_t)v; changed = true; }
            else Serial.printf("[MQTT-CFG] ! '%s'=%d out of range [%d,%d] – ignored\r\n", key, v, lo, hi);
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

    // ── NTP ────────────────────────────────────────────────────────────────
    tryStr("ntp_srv1", g_deviceConfig.ntpServer1,  sizeof(g_deviceConfig.ntpServer1));
    tryStr("ntp_srv2", g_deviceConfig.ntpServer2,  sizeof(g_deviceConfig.ntpServer2));
    tryStr("ntp_tz",   g_deviceConfig.ntpTimezone, sizeof(g_deviceConfig.ntpTimezone));

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
    Serial.printf("[MQTT-CFG] Received config payload (%u bytes) on %s\r\n", length, topic);
    printPayload(s_configPayload);
}

// ─────────────────────────────────────────────────────────────────────────────
//  Publish status ACK
// ─────────────────────────────────────────────────────────────────────────────

static void publishStatus(bool updated) {
    char topic[96];
    snprintf(topic, sizeof(topic), "%s/config/status", g_deviceConfig.mqttClientId);

    // Build a compact JSON status message
    char payload[1024];
    snprintf(payload, sizeof(payload),
        "{\"fw\":\"%s\","
        "\"updated\":%s,"
        "\"ds18_en\":%s,\"ds18_int\":%d,"
        "\"sht_en\":%s,\"sht_int\":%d,"
        "\"ina_en\":%s,\"ina_int\":%d,"
        "\"ph_hour\":%d,\"ph_min\":%d,\"ph_win\":%d,\"cb_photo_en\":%s,"
        "\"wifi_ssid\":\"%s\",\"wifi_pass\":\"%s\",\"upload_ep\":\"%s\","
        "\"ota_en\":%s,\"ota_url\":\"%s\","
        "\"mqtt_en\":%s,\"mqtt_host\":\"%s\",\"mqtt_port\":%u,\"mqtt_user\":\"%s\",\"mqtt_pass\":\"%s\",\"mqtt_id\":\"%s\","
        "\"ntp_srv1\":\"%s\",\"ntp_srv2\":\"%s\",\"ntp_tz\":\"%s\","
        "\"dev_name\":\"%s\",\"cb_photo_en\":%s,"
        "\"diag_en\":%s,"
        "\"boot_win\":%u}",
        
        FIRMWARE_VERSION,
        updated ? "true" : "false",
        g_deviceConfig.ds18b20Enabled ? "true" : "false", g_deviceConfig.ds18b20IntervalMin,
        g_deviceConfig.sht3xEnabled   ? "true" : "false", g_deviceConfig.sht3xIntervalMin,
        g_deviceConfig.ina219Enabled  ? "true" : "false", g_deviceConfig.ina219IntervalMin,
        g_deviceConfig.photoHour, g_deviceConfig.photoMinute, g_deviceConfig.photoWindowMin, g_deviceConfig.coldBootPhotoEn ? "true" : "false",
        g_deviceConfig.wifiSsid, g_deviceConfig.wifiPassword, g_deviceConfig.uploadEndpoint,
        g_deviceConfig.otaEnabled ? "true" : "false", g_deviceConfig.otaManifestUrl,
        g_deviceConfig.mqttEnabled ? "true" : "false", g_deviceConfig.mqttBroker, g_deviceConfig.mqttPort, g_deviceConfig.mqttUser, g_deviceConfig.mqttPassword, g_deviceConfig.mqttClientId,
        g_deviceConfig.ntpServer1, g_deviceConfig.ntpServer2, g_deviceConfig.ntpTimezone,
        g_deviceConfig.deviceName, g_deviceConfig.coldBootPhotoEn ? "true" : "false",
        g_deviceConfig.diagEn ? "true" : "false",
        g_deviceConfig.bootWindowSec);

    s_mqtt.publish(topic, payload, false /* not retained */);
    Serial.printf("[MQTT-CFG] Status published to %s\r\n", topic);
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

    Serial.printf("[MQTT-CFG] Connecting to %s:%u as '%s'\r\n",
                  g_deviceConfig.mqttBroker,
                  g_deviceConfig.mqttPort,
                  g_deviceConfig.mqttClientId);

    s_mqtt.setServer(g_deviceConfig.mqttBroker, g_deviceConfig.mqttPort);
    s_mqtt.setCallback(onMessage);
    s_mqtt.setBufferSize(MQTT_BUFFER_SIZE);   // must be enough for the entire config payload

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
        Serial.printf("[MQTT-CFG] Connection failed (state=%d)\r\n", s_mqtt.state());
        return false;
    }

    // Poll briefly to process the connection and trigger the retained message callback
    s_mqtt.loop();
    delay(50);
    Serial.println("[MQTT-CFG] Connected");

    // Subscribe to the retained config topic
    char subTopic[96];
    snprintf(subTopic, sizeof(subTopic), "%s/config/set", g_deviceConfig.mqttClientId);
    bool subscribed = s_mqtt.subscribe(subTopic);
    Serial.printf("[MQTT-CFG] Subscribed to %s: %s\r\n", subTopic, subscribed ? "OK" : "FAIL");
    delay(100);  // brief pause to ensure subscription is processed before we check for retained message

    // Poll briefly – a retained message arrives almost immediately
    uint32_t deadline = millis() + MQTT_CONFIG_TIMEOUT_MS;
    while (!s_configReceived && millis() < deadline) {
        s_mqtt.loop();
        delay(20);
    }
    
    // If we got a config message, apply it and save to NVS; otherwise just exit.
    bool updated = false;
    if (s_configReceived) {
        Serial.println("[MQTT-CFG] Applying config...");
        bool changed = applyConfig(s_configPayload);
        if (changed) {
            device_config::save();
            delay(50);
            device_config::load();
            device_config::print();
            updated = true;
        } else {
            Serial.println("[MQTT-CFG] No recognised keys in payload – nothing changed");
        }
    } else {
        Serial.println("[MQTT-CFG] No retained config message (topic may be empty – that's fine)");
    }

    // Publish an ACK status message whether we updated or not, so the broker can track that we're alive and received the config (even if it was a no-op).
    publishStatus(updated);

    // Clean up and disconnect
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
    s_mqtt.setBufferSize(MQTT_BUFFER_SIZE);

    bool connected;
    if (g_deviceConfig.mqttUser[0] != '\0') {
        connected = s_mqtt.connect(g_deviceConfig.mqttClientId,
                                   g_deviceConfig.mqttUser,
                                   g_deviceConfig.mqttPassword);
    } else {
        connected = s_mqtt.connect(g_deviceConfig.mqttClientId);
    }

    if (!connected) {
        Serial.printf("[MQTT-CFG] publishAlive: connexion échouée (state=%d)\r\n", s_mqtt.state());
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
    Serial.printf("[MQTT-CFG] publishAlive → %s : %s\r\n", topic, ok ? "OK" : "échec publish");
    return ok;
}
