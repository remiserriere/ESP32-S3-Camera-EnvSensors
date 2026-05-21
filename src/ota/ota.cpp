#include "ota.h"
#include "../config.h"
#include "../device_config.h"
#include "../version.h"
#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <Update.h>
#include <ArduinoOTA.h>
#include <Preferences.h>

// ─────────────────────────────────────────────────────────────────────────────
//  Internal helpers
// ─────────────────────────────────────────────────────────────────────────────

static constexpr const char* NVS_NS_OTA   = "ota";
static constexpr const char* NVS_KEY_MAINT = "maint";

// Very small hand-rolled JSON field extractor for the known manifest format.
// Extracts the string value of "key" from a flat JSON object.
static String jsonExtractString(const String& json, const char* key) {
    String search = String("\"") + key + "\":\"";
    int start = json.indexOf(search);
    if (start < 0) return "";
    start += search.length();
    int end = json.indexOf('"', start);
    if (end < 0) return "";
    return json.substring(start, end);
}

// Compare two version strings of the form "vX.Y.Z" or "X.Y.Z".
// Returns true if `remote` is strictly newer than `local`.
// Falls back to plain string inequality if parsing fails.
static bool isNewer(const String& local, const String& remote) {
    if (local == remote) return false;

    auto stripV = [](const String& s) -> String {
        return s.startsWith("v") || s.startsWith("V") ? s.substring(1) : s;
    };

    String l = stripV(local);
    String r = stripV(remote);

    // Parse major.minor.patch
    int lMaj = 0, lMin = 0, lPat = 0;
    int rMaj = 0, rMin = 0, rPat = 0;
    bool lOk = (sscanf(l.c_str(), "%d.%d.%d", &lMaj, &lMin, &lPat) == 3);
    bool rOk = (sscanf(r.c_str(), "%d.%d.%d", &rMaj, &rMin, &rPat) == 3);

    if (!lOk || !rOk) {
        // Non-semver strings: just check inequality
        return (l != r);
    }

    if (rMaj != lMaj) return rMaj > lMaj;
    if (rMin != lMin) return rMin > lMin;
    return rPat > lPat;
}

// ─────────────────────────────────────────────────────────────────────────────
//  HTTP OTA
// ─────────────────────────────────────────────────────────────────────────────

bool ota::checkAndApply() {
    if (!g_deviceConfig.otaEnabled) return false;

    Serial.printf("[OTA] Current version: %s\n", FIRMWARE_VERSION);
    Serial.printf("[OTA] Checking manifest: %s\n", g_deviceConfig.otaManifestUrl);

    HTTPClient http;
    http.begin(g_deviceConfig.otaManifestUrl);
    http.setTimeout(10000);
    int code = http.GET();

    if (code != 200) {
        Serial.printf("[OTA] Manifest fetch failed (HTTP %d)\n", code);
        http.end();
        return false;
    }

    String body = http.getString();
    http.end();

    String remoteVersion = jsonExtractString(body, "version");
    String binaryUrl     = jsonExtractString(body, "url");
    String notes         = jsonExtractString(body, "notes");

    Serial.printf("[OTA] Remote version: %s\n", remoteVersion.c_str());

    if (remoteVersion.isEmpty() || binaryUrl.isEmpty()) {
        Serial.println("[OTA] Malformed manifest – aborting");
        return false;
    }

    if (!isNewer(FIRMWARE_VERSION, remoteVersion)) {
        Serial.println("[OTA] Already up-to-date");
        return false;
    }

    Serial.printf("[OTA] Update available: %s → %s\n", FIRMWARE_VERSION, remoteVersion.c_str());
    if (!notes.isEmpty()) Serial.printf("[OTA] Notes: %s\n", notes.c_str());
    Serial.printf("[OTA] Downloading: %s\n", binaryUrl.c_str());

    HTTPClient dlHttp;
    dlHttp.begin(binaryUrl);
    dlHttp.setTimeout(OTA_DOWNLOAD_TIMEOUT_MS);
    int dlCode = dlHttp.GET();

    if (dlCode != 200) {
        Serial.printf("[OTA] Binary download failed (HTTP %d)\n", dlCode);
        dlHttp.end();
        return false;
    }

    int contentLength = dlHttp.getSize();
    if (contentLength <= 0) {
        Serial.println("[OTA] Content-Length unknown – aborting (streaming not supported)");
        dlHttp.end();
        return false;
    }

    Serial.printf("[OTA] Binary size: %d bytes\n", contentLength);

    if (!Update.begin(contentLength)) {
        Serial.printf("[OTA] Not enough partition space (error %d)\n", Update.getError());
        dlHttp.end();
        return false;
    }

    WiFiClient* stream = dlHttp.getStreamPtr();
    size_t written = Update.writeStream(*stream);
    dlHttp.end();

    if (written != (size_t)contentLength) {
        Serial.printf("[OTA] Write incomplete: %zu / %d bytes\n", written, contentLength);
        Update.abort();
        return false;
    }

    if (!Update.end(true)) {
        Serial.printf("[OTA] Finalise failed (error %d)\n", Update.getError());
        return false;
    }

    Serial.println("[OTA] Flash successful – restarting...");
    Serial.flush();
    delay(500);
    ESP.restart();
    return true;  // unreachable; keeps compiler happy
}

// ─────────────────────────────────────────────────────────────────────────────
//  ArduinoOTA maintenance mode
// ─────────────────────────────────────────────────────────────────────────────

bool ota::isMaintenanceModeRequested() {
    // GPIO trigger: active-low (BOOT button on most ESP32-S3 devkits is GPIO 0)
    if (OTA_MAINTENANCE_GPIO >= 0) {
        pinMode(OTA_MAINTENANCE_GPIO, INPUT_PULLUP);
        if (digitalRead(OTA_MAINTENANCE_GPIO) == LOW) {
            Serial.println("[OTA] Maintenance mode triggered by GPIO");
            return true;
        }
    }

    // NVS flag
    Preferences p;
    p.begin(NVS_NS_OTA, true);  // read-only
    bool flag = p.getBool(NVS_KEY_MAINT, false);
    p.end();
    if (flag) Serial.println("[OTA] Maintenance mode triggered by NVS flag");
    return flag;
}

void ota::requestMaintenanceMode(bool enable) {
    Preferences p;
    p.begin(NVS_NS_OTA, false);
    p.putBool(NVS_KEY_MAINT, enable);
    p.end();
    Serial.printf("[OTA] Maintenance mode NVS flag set to: %s\n", enable ? "true" : "false");
}

void ota::enterMaintenanceMode(uint32_t timeoutMs) {
    // Clear the NVS flag immediately so a crash doesn't loop forever
    requestMaintenanceMode(false);

    Serial.println("[OTA] Entering ArduinoOTA maintenance mode...");
    Serial.printf("[OTA] Timeout: %u ms\n", timeoutMs);

    // Wi-Fi should already be up; if not, try to connect
    if (WiFi.status() != WL_CONNECTED) {
        WiFi.mode(WIFI_STA);
        WiFi.begin(g_deviceConfig.wifiSsid, g_deviceConfig.wifiPassword);
        uint32_t t0 = millis();
        while (WiFi.status() != WL_CONNECTED && millis() - t0 < WIFI_TIMEOUT_MS) {
            delay(300);
        }
        if (WiFi.status() != WL_CONNECTED) {
            Serial.println("[OTA] Wi-Fi unavailable – aborting maintenance mode");
            return;
        }
    }

    Serial.printf("[OTA] IP: %s  – use: pio run -t upload --upload-port %s\n",
                  WiFi.localIP().toString().c_str(),
                  WiFi.localIP().toString().c_str());

    ArduinoOTA.setHostname(g_deviceConfig.deviceName);
    ArduinoOTA.setPassword(OTA_DEVICE_PASSWORD);

    ArduinoOTA.onStart([]() {
        Serial.printf("[OTA] ArduinoOTA start (%s)\n",
                      ArduinoOTA.getCommand() == U_FLASH ? "firmware" : "filesystem");
    });
    ArduinoOTA.onEnd([]() {
        Serial.println("\n[OTA] ArduinoOTA complete – restarting");
    });
    ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
        Serial.printf("[OTA] Progress: %u%%\r", progress * 100 / total);
    });
    ArduinoOTA.onError([](ota_error_t error) {
        Serial.printf("[OTA] Error [%u]: ", error);
        switch (error) {
            case OTA_AUTH_ERROR:    Serial.println("Auth failed");      break;
            case OTA_BEGIN_ERROR:   Serial.println("Begin failed");     break;
            case OTA_CONNECT_ERROR: Serial.println("Connect failed");   break;
            case OTA_RECEIVE_ERROR: Serial.println("Receive failed");   break;
            case OTA_END_ERROR:     Serial.println("End failed");       break;
            default:                Serial.println("Unknown error");    break;
        }
    });

    ArduinoOTA.begin();

    uint32_t deadline = millis() + timeoutMs;
    while (millis() < deadline) {
        ArduinoOTA.handle();
        delay(10);
    }

    Serial.println("[OTA] Maintenance mode timeout – resuming normal operation");
    ArduinoOTA.end();
    WiFi.disconnect(true);
    WiFi.mode(WIFI_OFF);
}
