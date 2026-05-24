#pragma once
#include <cstdint>
#include "config.h"

// ─────────────────────────────────────────────────────────────────────────────
//  DeviceConfig – runtime-configurable device settings
//
//  Loaded from NVS at every boot (device_config::load()).
//  Editable at any time via the serial CLI (3-second window at boot).
//  Persisted to NVS with device_config::save().
//
//  All fields fall back to the compile-time defaults in config.h when NVS
//  has no stored value (i.e. after a fresh flash or a factory reset).
//
//  Hardware-level constants (GPIO pins, bus speeds, timeouts…) are intentionally
//  kept as #defines in config.h – they require a recompile anyway.
// ─────────────────────────────────────────────────────────────────────────────

struct DeviceConfig {
    // ── Sensor presence & polling intervals ──────────────────────────────────
    bool    ds18b20Enabled;         // default: DS18B20_ENABLED
    uint8_t ds18b20IntervalMin;     // default: DS18B20_INTERVAL_MIN

    bool    sht3xEnabled;           // default: SHT3X_ENABLED
    uint8_t sht3xIntervalMin;       // default: SHT3X_INTERVAL_MIN

    bool    ina219Enabled;          // default: INA219_ENABLED
    uint8_t ina219IntervalMin;      // default: INA219_INTERVAL_MIN

    // ── Daily photo schedule ─────────────────────────────────────────────────
    uint8_t photoHour;              // default: PHOTO_HOUR     (0-23)
    uint8_t photoMinute;            // default: PHOTO_MINUTE   (0-59)
    uint8_t photoWindowMin;         // default: PHOTO_WINDOW_MIN
    bool    coldBootPhotoEn;        // default: false – take a photo on every cold boot
    // ── Network ──────────────────────────────────────────────────────────────
    char    wifiSsid[64];           // default: WIFI_SSID
    char    wifiPassword[64];       // default: WIFI_PASSWORD
    char    uploadEndpoint[128];    // default: UPLOAD_ENDPOINT

    // ── OTA ──────────────────────────────────────────────────────────────────
    bool    otaEnabled;             // default: OTA_ENABLED
    char    otaManifestUrl[128];    // default: OTA_MANIFEST_URL

    // ── MQTT (config channel + optional telemetry) ────────────────────────────
    bool    mqttEnabled;            // default: false  – enable MQTT config sync
    char    mqttBroker[64];         // default: ""     – broker hostname or IP
    uint16_t mqttPort;              // default: 1883
    char    mqttUser[32];           // default: ""     – leave blank if no auth
    char    mqttPassword[32];       // default: ""
    char    mqttClientId[32];       // default: deviceName

    // ── BLE ──────────────────────────────────────────────────────────────────
    char    deviceName[32];         // default: BTHOME_DEVICE_NAME
    bool    diagEn;                 // default: false – add next-wakeup/next-photo counters to BLE scan response
    // ── Boot configuration window ────────────────────────────────────────────
    // Seconds CLI + web config server are available at every boot/wake.
    // 5 s are always reserved for CLI regardless of this value.
    // 0 = web disabled (only the 5-second CLI safety window).
    uint8_t bootWindowSec;          // default: BOOT_WINDOW_SEC

    // ── NTP & Timezone ───────────────────────────────────────────────────────
    char    ntpServer1[64];         // default: NTP_SERVER_1
    char    ntpServer2[64];         // default: NTP_SERVER_2
    char    ntpTimezone[64];        // default: NTP_TIMEZONE  (POSIX TZ string)
};

// Global config instance – populated by device_config::load() at startup.
// All modules read this instead of the #define constants.
extern DeviceConfig g_deviceConfig;

namespace device_config {
    // Load from NVS, falling back to compile-time defaults where no value is stored.
    // Always call this first thing in setup().
    void load();

    // Persist current g_deviceConfig to NVS.
    void save();

    // Reset g_deviceConfig to compile-time defaults.
    // Does NOT save automatically – call save() afterwards if desired.
    void resetToDefaults();

    // Pretty-print current config to Serial.
    void print();
}
