#include "device_config.h"
#include <Arduino.h>
#include <Preferences.h>
#include <string.h>

DeviceConfig g_deviceConfig;

static constexpr const char* CFG_NS = "dev_cfg";

// ─────────────────────────────────────────────────────────────────────────────

void device_config::resetToDefaults() {
    g_deviceConfig = {};   // zero-init all fields first

    g_deviceConfig.ds18b20Enabled     = true;
    g_deviceConfig.ds18b20IntervalMin = DS18B20_INTERVAL_MIN;

    g_deviceConfig.sht3xEnabled       = true;
    g_deviceConfig.sht3xIntervalMin   = SHT3X_INTERVAL_MIN;

    g_deviceConfig.ina219Enabled      = INA219_ENABLED;
    g_deviceConfig.ina219IntervalMin  = INA219_INTERVAL_MIN;

    g_deviceConfig.photoHour          = PHOTO_HOUR;
    g_deviceConfig.photoMinute        = PHOTO_MINUTE;
    g_deviceConfig.photoWindowMin     = PHOTO_WINDOW_MIN;

    strncpy(g_deviceConfig.wifiSsid,       WIFI_SSID,          sizeof(g_deviceConfig.wifiSsid)       - 1);
    strncpy(g_deviceConfig.wifiPassword,   WIFI_PASSWORD,      sizeof(g_deviceConfig.wifiPassword)   - 1);
    strncpy(g_deviceConfig.uploadEndpoint, UPLOAD_ENDPOINT,    sizeof(g_deviceConfig.uploadEndpoint) - 1);

    g_deviceConfig.otaEnabled         = OTA_ENABLED;
    strncpy(g_deviceConfig.otaManifestUrl, OTA_MANIFEST_URL,   sizeof(g_deviceConfig.otaManifestUrl) - 1);

    strncpy(g_deviceConfig.deviceName,     BTHOME_DEVICE_NAME, sizeof(g_deviceConfig.deviceName)     - 1);
}

void device_config::load() {
    resetToDefaults();   // always start from compile-time defaults

    Preferences p;
    if (!p.begin(CFG_NS, true)) {
        // Namespace doesn't exist yet (first boot) – defaults are fine
        Serial.println("[Config] First boot – using compile-time defaults");
        return;
    }

    // Read each field; if the key is absent the default from resetToDefaults() is kept.
    g_deviceConfig.ds18b20Enabled     = p.getBool ("ds18_en",  g_deviceConfig.ds18b20Enabled);
    g_deviceConfig.ds18b20IntervalMin = p.getUChar("ds18_int", g_deviceConfig.ds18b20IntervalMin);

    g_deviceConfig.sht3xEnabled       = p.getBool ("sht_en",   g_deviceConfig.sht3xEnabled);
    g_deviceConfig.sht3xIntervalMin   = p.getUChar("sht_int",  g_deviceConfig.sht3xIntervalMin);

    g_deviceConfig.ina219Enabled      = p.getBool ("ina_en",   g_deviceConfig.ina219Enabled);
    g_deviceConfig.ina219IntervalMin  = p.getUChar("ina_int",  g_deviceConfig.ina219IntervalMin);

    g_deviceConfig.photoHour          = p.getUChar("ph_hour",  g_deviceConfig.photoHour);
    g_deviceConfig.photoMinute        = p.getUChar("ph_min",   g_deviceConfig.photoMinute);
    g_deviceConfig.photoWindowMin     = p.getUChar("ph_win",   g_deviceConfig.photoWindowMin);

    auto readStr = [&](const char* key, char* buf, size_t maxLen) {
        String s = p.getString(key, buf);
        strncpy(buf, s.c_str(), maxLen - 1);
        buf[maxLen - 1] = '\0';
    };

    readStr("wifi_ssid",  g_deviceConfig.wifiSsid,       sizeof(g_deviceConfig.wifiSsid));
    readStr("wifi_pass",  g_deviceConfig.wifiPassword,   sizeof(g_deviceConfig.wifiPassword));
    readStr("upload_ep",  g_deviceConfig.uploadEndpoint, sizeof(g_deviceConfig.uploadEndpoint));

    g_deviceConfig.otaEnabled         = p.getBool("ota_en",  g_deviceConfig.otaEnabled);
    readStr("ota_url",    g_deviceConfig.otaManifestUrl,  sizeof(g_deviceConfig.otaManifestUrl));
    readStr("dev_name",   g_deviceConfig.deviceName,      sizeof(g_deviceConfig.deviceName));

    p.end();
    Serial.println("[Config] Loaded from NVS");
}

void device_config::save() {
    Preferences p;
    p.begin(CFG_NS, false);

    p.putBool ("ds18_en",  g_deviceConfig.ds18b20Enabled);
    p.putUChar("ds18_int", g_deviceConfig.ds18b20IntervalMin);

    p.putBool ("sht_en",   g_deviceConfig.sht3xEnabled);
    p.putUChar("sht_int",  g_deviceConfig.sht3xIntervalMin);

    p.putBool ("ina_en",   g_deviceConfig.ina219Enabled);
    p.putUChar("ina_int",  g_deviceConfig.ina219IntervalMin);

    p.putUChar("ph_hour",  g_deviceConfig.photoHour);
    p.putUChar("ph_min",   g_deviceConfig.photoMinute);
    p.putUChar("ph_win",   g_deviceConfig.photoWindowMin);

    p.putString("wifi_ssid", g_deviceConfig.wifiSsid);
    p.putString("wifi_pass", g_deviceConfig.wifiPassword);
    p.putString("upload_ep", g_deviceConfig.uploadEndpoint);

    p.putBool  ("ota_en",  g_deviceConfig.otaEnabled);
    p.putString("ota_url", g_deviceConfig.otaManifestUrl);
    p.putString("dev_name",g_deviceConfig.deviceName);

    p.end();
    Serial.println("[Config] Saved to NVS");
}

void device_config::print() {
    Serial.println(F("\n┌─── Device Configuration ────────────────────────────────┐"));
    Serial.printf ("│  DS18B20  : %-8s  interval: %3d min                  │\n",
                   g_deviceConfig.ds18b20Enabled ? "ENABLED" : "DISABLED",
                   g_deviceConfig.ds18b20IntervalMin);
    Serial.printf ("│  SHT3x    : %-8s  interval: %3d min                  │\n",
                   g_deviceConfig.sht3xEnabled ? "ENABLED" : "DISABLED",
                   g_deviceConfig.sht3xIntervalMin);
    Serial.printf ("│  INA219   : %-8s  interval: %3d min                  │\n",
                   g_deviceConfig.ina219Enabled ? "ENABLED" : "DISABLED",
                   g_deviceConfig.ina219IntervalMin);
    Serial.printf ("│  Photo    : %02d:%02d  window: +%d min                      │\n",
                   g_deviceConfig.photoHour, g_deviceConfig.photoMinute,
                   g_deviceConfig.photoWindowMin);
    Serial.printf ("│  WiFi     : %-47s│\n", g_deviceConfig.wifiSsid);
    Serial.printf ("│  Endpoint : %-47s│\n", g_deviceConfig.uploadEndpoint);
    Serial.printf ("│  OTA      : %-8s  %-38s│\n",
                   g_deviceConfig.otaEnabled ? "ENABLED" : "DISABLED",
                   g_deviceConfig.otaManifestUrl);
    Serial.printf ("│  BLE name : %-47s│\n", g_deviceConfig.deviceName);
    Serial.println(F("└─────────────────────────────────────────────────────────┘"));
}
