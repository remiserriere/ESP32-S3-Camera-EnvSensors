#pragma once
#include <cstdint>

// ─────────────────────────────────────────────
//  Wi-Fi
// ─────────────────────────────────────────────
#define WIFI_SSID      "YOUR_SSID"
#define WIFI_PASSWORD  "YOUR_WIFI_PASSWORD"
#define WIFI_TIMEOUT_MS 15000

// ─────────────────────────────────────────────
//  NTP
// ─────────────────────────────────────────────
#define NTP_SERVER_1   "pool.ntp.org"
#define NTP_SERVER_2   "time.google.com"
#define NTP_TIMEZONE   "CET-1CEST,M3.5.0,M10.5.0/3"  // Europe/Paris – adjust as needed
#define NTP_SYNC_TIMEOUT_MS 10000
// Maximum age of RTC-retained time before forcing a new NTP sync (seconds)
#define NTP_MAX_AGE_BEFORE_RESYNC_S (3600UL * 6)  // 6 hours

// ─────────────────────────────────────────────
//  DS18B20 (OneWire, chained)
// ─────────────────────────────────────────────
#define DS18B20_PIN            14       // GPIO pin for OneWire bus – adjust to wiring
#define DS18B20_RESOLUTION     12       // 9..12 bits
#define DS18B20_INTERVAL_MIN   10       // Read every N minutes

// ─────────────────────────────────────────────
//  SHT3x (I2C)
// ─────────────────────────────────────────────
#define SHT3X_I2C_ADDR         0x44     // or 0x45 depending on ADDR pin
#define SHT3X_INTERVAL_MIN     5        // Read every N minutes

// ─────────────────────────────────────────────
//  INA219 (I2C – voltage/current measurement)
// ─────────────────────────────────────────────
#define INA219_I2C_ADDR        0x40     // default I2C address
#define INA219_INTERVAL_MIN    2        // Read every N minutes
#define INA219_ENABLED         true     // set false if not connected

// ─────────────────────────────────────────────
//  LC709203F (I2C – LiPo battery gauge)
// ─────────────────────────────────────────────
#define LC709203F_I2C_ADDR     0x0B     // fixed address
#define LC709203F_INTERVAL_MIN 5        // Read every N minutes
#define LC709203F_ENABLED      true     // set false if not connected
// Battery pack capacity in mAh – required for LC709203F calibration
#define LC709203F_APA          0x30     // APA value for ~3000 mAh pack; see datasheet table

// ─────────────────────────────────────────────
//  I2C Bus
// ─────────────────────────────────────────────
#define I2C_SDA_PIN            3        // TODO: verify against Freenove pinout
#define I2C_SCL_PIN            2        // TODO: verify against Freenove pinout
#define I2C_FREQ_HZ            400000

// ─────────────────────────────────────────────
//  Photo schedule
// ─────────────────────────────────────────────
#define PHOTO_HOUR             14       // Target hour (24 h clock)
#define PHOTO_MINUTE           0        // Target minute
// How many minutes past the target we still consider "in window"
#define PHOTO_WINDOW_MIN       10

// ─────────────────────────────────────────────
//  HTTP upload
// ─────────────────────────────────────────────
#define UPLOAD_ENDPOINT        "http://192.168.1.100:8080/upload"  // Adjust to your service
#define UPLOAD_TIMEOUT_MS      20000
#define UPLOAD_MAX_RETRIES     2

// ─────────────────────────────────────────────
//  BTHome BLE
// ─────────────────────────────────────────────
#define BTHOME_DEVICE_NAME     "ESP32-S3-Env"
// BTHome service UUID (little-endian 16-bit UUID in 128-bit form handled by NimBLE)
#define BTHOME_SERVICE_UUID    0xFCD2
// Advertising duration in ms – keep short to save energy
#define BTHOME_ADV_DURATION_MS 3000

// ─────────────────────────────────────────────
//  Deep sleep / scheduler
// ─────────────────────────────────────────────
// Minimum granularity of the scheduler wake interval (seconds)
#define SCHEDULER_MIN_WAKE_INTERVAL_S 60
// Maximum sleep duration before forced re-evaluation (seconds)
#define SCHEDULER_MAX_SLEEP_S (3600UL * 4)

// ─────────────────────────────────────────────
//  OTA (Over-The-Air firmware update)
// ─────────────────────────────────────────────
// Set false to disable all OTA logic (saves a few KB of flash)
#define OTA_ENABLED             true

// URL of the OTA manifest JSON served by your local update server or CI.
// Format: { "version": "v1.2.3", "url": "http://host/firmware.bin", "notes": "..." }
//
// For self-hosted (HTTP): use your local server URL (default below).
// For GitHub Releases-based OTA: point at the manifest.json release asset,
//   e.g. "https://github.com/owner/repo/releases/latest/download/manifest.json"
//   NOTE: HTTPS URLs require WiFiClientSecure instead of HTTPClient.
//         See the Known Limitations section in README.md.
#define OTA_MANIFEST_URL        "http://192.168.1.100:8080/firmware/manifest.json"

// Password required for ArduinoOTA push (maintenance mode).
// Change before deploying – leave empty string "" to disable password check.
#define OTA_DEVICE_PASSWORD     "esp32ota"

// GPIO held LOW at boot triggers ArduinoOTA maintenance mode.
// GPIO 0 is the BOOT button on most ESP32-S3 devkits.
// Set to -1 to disable the GPIO trigger.
#define OTA_MAINTENANCE_GPIO    0

// How long to wait for an ArduinoOTA push before giving up (ms)
#define OTA_MAINTENANCE_TIMEOUT_MS 120000

// Timeout for downloading a firmware binary during HTTP OTA (ms).
// Firmware binaries can be significantly larger than photo uploads,
// so this is kept separate from UPLOAD_TIMEOUT_MS.
#define OTA_DOWNLOAD_TIMEOUT_MS 60000

// ─────────────────────────────────────────────
//  Camera – Freenove ESP32-S3 WROOM
//  TODO: validate pin assignments against Freenove schematic/examples
// ─────────────────────────────────────────────
#define CAM_PIN_PWDN   -1
#define CAM_PIN_RESET  -1
#define CAM_PIN_XCLK   15
#define CAM_PIN_SIOD    4   // I2C SDA for camera (separate from sensor bus)
#define CAM_PIN_SIOC    5   // I2C SCL for camera
#define CAM_PIN_D7     16
#define CAM_PIN_D6     17
#define CAM_PIN_D5     18
#define CAM_PIN_D4     12
#define CAM_PIN_D3     10
#define CAM_PIN_D2      8
#define CAM_PIN_D1      9
#define CAM_PIN_D0     11
#define CAM_PIN_VSYNC   6
#define CAM_PIN_HREF    7
#define CAM_PIN_PCLK   13
