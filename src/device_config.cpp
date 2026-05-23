#include "device_config.h"
#include <Arduino.h>
#include <Preferences.h>
#include <string.h>

DeviceConfig g_deviceConfig;

static constexpr const char* CFG_NS = "dev_cfg";

// ─────────────────────────────────────────────────────────────────────────────

void device_config::resetToDefaults() {
    g_deviceConfig = {};   // zero-init all fields first

    g_deviceConfig.ds18b20Enabled     = DS18B20_ENABLED;
    g_deviceConfig.ds18b20IntervalMin = DS18B20_INTERVAL_MIN;

    g_deviceConfig.sht3xEnabled       = SHT3X_ENABLED;
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

    g_deviceConfig.mqttEnabled        = false;
    g_deviceConfig.mqttBroker[0]      = '\0';
    g_deviceConfig.mqttPort           = 1883;
    g_deviceConfig.mqttUser[0]        = '\0';
    g_deviceConfig.mqttPassword[0]    = '\0';
    strncpy(g_deviceConfig.mqttClientId, BTHOME_DEVICE_NAME, sizeof(g_deviceConfig.mqttClientId) - 1);

    strncpy(g_deviceConfig.deviceName,     BTHOME_DEVICE_NAME, sizeof(g_deviceConfig.deviceName)     - 1);

    g_deviceConfig.bootWindowSec      = BOOT_WINDOW_SEC;

    strncpy(g_deviceConfig.ntpServer1,  NTP_SERVER_1, sizeof(g_deviceConfig.ntpServer1)  - 1);
    strncpy(g_deviceConfig.ntpServer2,  NTP_SERVER_2, sizeof(g_deviceConfig.ntpServer2)  - 1);
    strncpy(g_deviceConfig.ntpTimezone, NTP_TIMEZONE, sizeof(g_deviceConfig.ntpTimezone) - 1);
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

    g_deviceConfig.mqttEnabled        = p.getBool  ("mqtt_en",  g_deviceConfig.mqttEnabled);
    readStr("mqtt_host",  g_deviceConfig.mqttBroker,    sizeof(g_deviceConfig.mqttBroker));
    g_deviceConfig.mqttPort           = p.getUShort("mqtt_port", g_deviceConfig.mqttPort);
    readStr("mqtt_user",  g_deviceConfig.mqttUser,      sizeof(g_deviceConfig.mqttUser));
    readStr("mqtt_pass",  g_deviceConfig.mqttPassword,  sizeof(g_deviceConfig.mqttPassword));
    readStr("mqtt_id",    g_deviceConfig.mqttClientId,  sizeof(g_deviceConfig.mqttClientId));

    readStr("dev_name",   g_deviceConfig.deviceName,      sizeof(g_deviceConfig.deviceName));

    g_deviceConfig.bootWindowSec = p.getUChar("boot_win", g_deviceConfig.bootWindowSec);

    readStr("ntp_srv1", g_deviceConfig.ntpServer1,  sizeof(g_deviceConfig.ntpServer1));
    readStr("ntp_srv2", g_deviceConfig.ntpServer2,  sizeof(g_deviceConfig.ntpServer2));
    readStr("ntp_tz",   g_deviceConfig.ntpTimezone, sizeof(g_deviceConfig.ntpTimezone));

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

    p.putBool  ("mqtt_en",   g_deviceConfig.mqttEnabled);
    p.putString("mqtt_host", g_deviceConfig.mqttBroker);
    p.putUShort("mqtt_port", g_deviceConfig.mqttPort);
    p.putString("mqtt_user", g_deviceConfig.mqttUser);
    p.putString("mqtt_pass", g_deviceConfig.mqttPassword);
    p.putString("mqtt_id",   g_deviceConfig.mqttClientId);

    p.putString("dev_name",g_deviceConfig.deviceName);

    p.putUChar("boot_win", g_deviceConfig.bootWindowSec);

    p.putString("ntp_srv1", g_deviceConfig.ntpServer1);
    p.putString("ntp_srv2", g_deviceConfig.ntpServer2);
    p.putString("ntp_tz",   g_deviceConfig.ntpTimezone);

    p.end();
    Serial.println("[Config] Saved to NVS");
}

void device_config::print() {
    Serial.println(F("\r\n┌─── Device Configuration ────────────────────────────────┐"));
    Serial.printf ("│  DS18B20  : %-8s  interval: %3d min                  │\r\n",
                   g_deviceConfig.ds18b20Enabled ? "ENABLED" : "DISABLED",
                   g_deviceConfig.ds18b20IntervalMin);
    Serial.printf ("│  SHT3x    : %-8s  interval: %3d min                  │\r\n",
                   g_deviceConfig.sht3xEnabled ? "ENABLED" : "DISABLED",
                   g_deviceConfig.sht3xIntervalMin);
    Serial.printf ("│  INA219   : %-8s  interval: %3d min                  │\r\n",
                   g_deviceConfig.ina219Enabled ? "ENABLED" : "DISABLED",
                   g_deviceConfig.ina219IntervalMin);
    Serial.printf ("│  Photo    : %02d:%02d  window: +%d min                      │\r\n",
                   g_deviceConfig.photoHour, g_deviceConfig.photoMinute,
                   g_deviceConfig.photoWindowMin);
    Serial.printf ("│  WiFi     : %-47s│\r\n", g_deviceConfig.wifiSsid);
    Serial.printf ("│  Endpoint : %-47s│\r\n", g_deviceConfig.uploadEndpoint);
    Serial.printf ("│  OTA      : %-8s  %-38s│\r\n",
                   g_deviceConfig.otaEnabled ? "ENABLED" : "DISABLED",
                   g_deviceConfig.otaManifestUrl);
    // MQTT: broker+port length is variable, truncate for display
    char mqttHost[32];
    snprintf(mqttHost, sizeof(mqttHost), "%.24s:%u",
             g_deviceConfig.mqttBroker, g_deviceConfig.mqttPort);
    Serial.printf ("│  MQTT     : %-8s  %-38s│\r\n",
                   g_deviceConfig.mqttEnabled ? "ENABLED" : "DISABLED",
                   mqttHost);
    Serial.printf ("│  BLE name : %-47s│\r\n", g_deviceConfig.deviceName);
    Serial.printf ("│  Boot win : %-3u s  (0=web off, min 5 s CLI)             │\r\n",
                   g_deviceConfig.bootWindowSec);
    Serial.printf ("│  NTP 1    : %-47s│\r\n", g_deviceConfig.ntpServer1);
    Serial.printf ("│  NTP 2    : %-47s│\r\n", g_deviceConfig.ntpServer2);
    Serial.printf ("│  TZ       : %-47s│\r\n", g_deviceConfig.ntpTimezone);
    Serial.println(F("└─────────────────────────────────────────────────────────┘"));
}
