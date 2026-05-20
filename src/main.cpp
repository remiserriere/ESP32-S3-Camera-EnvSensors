/**
 * ESP32-S3 WROOM Camera + Environment Sensors – Custom Firmware
 *
 * Architecture:
 *   - Deep sleep between actions (power-first design)
 *   - Sensor readings via BTHome BLE (no Wi-Fi for sensor telemetry)
 *   - Wi-Fi + HTTP POST only for daily photo upload
 *   - NTP sync only on first boot and opportunistically before photo
 *   - State kept in RTC memory (boot-to-boot) and NVS (power-cycle resilient)
 *   - OTA: HTTP manifest check during Wi-Fi session + ArduinoOTA maintenance mode
 */

#include <Arduino.h>
#include <Wire.h>
#include <vector>

#include "config.h"
#include "version.h"
#include "persistence.h"
#include "time_manager.h"
#include "scheduler.h"

#include "sensors/ds18b20.h"
#include "sensors/sht3x.h"
#include "sensors/ina219.h"

#include "bthome/bthome.h"
#include "camera/camera_module.h"
#include "uploader/uploader.h"
#include "ota/ota.h"

// ─────────────────────────────────────────────
//  Sensor reading results (populated per wake)
// ─────────────────────────────────────────────
static std::vector<Ds18b20Reading> ds18b20Readings;
static Sht3xReading     sht3xReading  = {};
static Ina219Reading    ina219Reading  = {};
static Lc709203fReading lc709Reading  = {};

// ─────────────────────────────────────────────
//  Helpers
// ─────────────────────────────────────────────

static void initI2C() {
    Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN, I2C_FREQ_HZ);
}

static void runSensorTasks(const TaskFlags& flags) {
    RtcState& rtc = getRtcState();
    bool anyRead  = false;

    if (flags.readDs18b20) {
        ds18b20::begin();
        ds18b20Readings = ds18b20::readAll();
        rtc.lastDs18b20ReadS = time_manager::nowEpoch();
        anyRead = true;
    }

    if (flags.readSht3x) {
        if (sht3x::begin()) {
            sht3xReading = sht3x::read();
            rtc.lastSht3xReadS = time_manager::nowEpoch();
            anyRead = true;
        }
    }

    if (flags.readIna219) {
        if (ina219::begin()) {
            ina219Reading = ina219::read();
            rtc.lastIna219ReadS = time_manager::nowEpoch();
            anyRead = true;
        }
    }

    if (flags.readLc709203f) {
        if (lc709203f::begin()) {
            lc709Reading = lc709203f::read();
            rtc.lastLc709203fReadS = time_manager::nowEpoch();
            anyRead = true;
        }
    }

    if (!anyRead) return;

    // ── Publish over BLE/BTHome ──────────────────
    BtHomePayload payload = {};

    // Use first DS18B20 as primary temperature (or SHT3x if DS18B20 absent)
    if (!ds18b20Readings.empty() && ds18b20Readings[0].valid) {
        payload.temperature    = ds18b20Readings[0].temperatureC;
        payload.hasTemperature = true;
    } else if (sht3xReading.valid) {
        payload.temperature    = sht3xReading.temperatureC;
        payload.hasTemperature = true;
    }

    if (sht3xReading.valid) {
        payload.humidity    = sht3xReading.humidityPct;
        payload.hasHumidity = true;
    }

    if (lc709Reading.valid) {
        payload.batteryPercent = (uint8_t)constrain(lc709Reading.batteryPercent, 0, 100);
        payload.hasBattery     = true;
        payload.voltage        = lc709Reading.batteryVoltageV;
        payload.hasVoltage     = true;
    }

    if (ina219Reading.valid) {
        payload.currentA   = ina219Reading.currentMa / 1000.0f;
        payload.hasCurrent = true;
        payload.powerW     = ina219Reading.powerMw / 1000.0f;
        payload.hasPower   = true;
        if (!payload.hasVoltage) {
            payload.voltage    = ina219Reading.busVoltageV;
            payload.hasVoltage = true;
        }
    }

    bthome::begin();
    bthome::advertise(payload);
    bthome::end();
}

static void runPhotoTask() {
    RtcState& rtc = getRtcState();

    if (!uploader::wifiConnect()) {
        Serial.println("[PHOTO] WiFi failed – aborting photo task");
        rtc.photoRetryCount++;
        return;
    }

    // NTP re-sync if needed (Wi-Fi is already up – no extra cost)
    if (time_manager::needsResync()) {
        time_manager::syncNtp();
    }

    // ── OTA check while Wi-Fi is already connected ───────────────────────
    // checkAndApply() resets the device if a new firmware is flashed,
    // so the lines below are only reached when there is no pending update.
    if (OTA_ENABLED) {
        ota::checkAndApply();
    }

    // Capture
    if (!camera_module::begin()) {
        Serial.println("[PHOTO] Camera init failed");
        uploader::wifiDisconnect();
        rtc.photoRetryCount++;
        return;
    }

    CameraFrame frame = camera_module::capture();

    if (!frame.valid) {
        Serial.println("[PHOTO] Capture failed");
        camera_module::end();
        uploader::wifiDisconnect();
        rtc.photoRetryCount++;
        return;
    }

    // Upload
    UploadMetadata meta = {};
    meta.deviceId   = BTHOME_DEVICE_NAME;
    meta.timestampS = time_manager::nowEpoch();
    meta.latitude   = 0.0f;
    meta.longitude  = 0.0f;

    int code = uploader::uploadPhoto(frame.buf, frame.len, meta);
    camera_module::releaseFrame(frame);
    camera_module::end();
    uploader::wifiDisconnect();

    if (code >= 200 && code < 300) {
        // Mark photo as taken for today
        struct tm now_tm;
        time_manager::nowLocal(now_tm);
        rtc.lastPhotoDayOfYear = now_tm.tm_yday;
        rtc.lastPhotoYear      = now_tm.tm_year + 1900;
        rtc.photoRetryCount    = 0;
        nvs::saveLastPhoto(rtc.lastPhotoYear, rtc.lastPhotoDayOfYear);
        Serial.println("[PHOTO] Upload successful");
    } else {
        rtc.photoRetryCount++;
        Serial.printf("[PHOTO] Upload failed (HTTP %d), retry %u\n", code, rtc.photoRetryCount);
    }
}

// ─────────────────────────────────────────────
//  Arduino setup() – runs on every wake/boot
// ─────────────────────────────────────────────
void setup() {
    Serial.begin(115200);
    delay(200);  // settle USB CDC

    Serial.printf("\n\n=== Boot #%u  fw:%s ===\n",
                  getRtcState().bootCount + 1, FIRMWARE_VERSION);

    // Open NVS early – needed by ota::isMaintenanceModeRequested()
    nvs::begin();

    // ── Maintenance mode check (before anything else) ────────────────────
    // Triggered by holding OTA_MAINTENANCE_GPIO low at boot, or by a NVS flag
    // set remotely (e.g. via a Home Assistant automation).
    if (ota::isMaintenanceModeRequested()) {
        Serial.println("[MAIN] Maintenance mode requested – entering ArduinoOTA standby");
        ota::enterMaintenanceMode(OTA_MAINTENANCE_TIMEOUT_MS);
        // Returns after timeout or after a successful OTA (which resets the device).
        // Fall through to normal operation if no update was pushed.
    }

    // Restore / estimate current time from RTC memory + NVS
    bool timeOk = time_manager::init();

    if (!timeOk) {
        // First boot or stale time – perform NTP sync
        Serial.println("[MAIN] Time not trusted – syncing NTP...");
        if (uploader::wifiConnect()) {
            time_manager::syncNtp();
            uploader::wifiDisconnect();
        } else {
            Serial.println("[MAIN] WiFi unavailable – will retry next wake");
        }
    }

    // Initialise I2C for sensors
    initI2C();

    // Determine which tasks are due
    TaskFlags flags = scheduler::evaluate();

    Serial.printf("[SCHED] Tasks: DS18B20=%d SHT3x=%d INA219=%d LC709=%d Photo=%d\n",
                  flags.readDs18b20, flags.readSht3x, flags.readIna219,
                  flags.readLc709203f, flags.takePhoto);

    // Run sensor tasks (publishes via BTHome BLE)
    runSensorTasks(flags);

    // Run photo task (uses WiFi, camera) + OTA check during the same Wi-Fi session
    if (flags.takePhoto) {
        runPhotoTask();
    }

    // Compute next wake interval and sleep
    uint32_t sleepSecs = scheduler::nextSleepSeconds(flags);
    scheduler::deepSleep(sleepSecs);  // does not return
}

void loop() {
    // Never reached – firmware runs in setup() and returns to deep sleep
}
