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
 *   - Runtime configuration via NVS + serial CLI (3-second window at boot)
 */

#include <Arduino.h>
#include <Wire.h>
#include <vector>
#include "driver/gpio.h"   // gpio_reset_pin – reclaims JTAG pins as normal GPIO

#include "config.h"
#include "version.h"
#include "device_config.h"
#include "persistence.h"
#include "time_manager.h"
#include "scheduler.h"
#include "serial_cli.h"

#include "sensors/ds18b20.h"
#include "sensors/sht3x.h"
#include "sensors/ina219.h"

#include "bthome/bthome.h"
#include "camera/camera_module.h"
#include "uploader/uploader.h"
#include "ota/ota.h"
#include "mqtt_config/mqtt_config.h"

// ─────────────────────────────────────────────
//  Sensor reading results (populated per wake)
// ─────────────────────────────────────────────
static std::vector<Ds18b20Reading> ds18b20Readings;
static Sht3xReading  sht3xReading  = {};
static Ina219Reading ina219Reading = {};

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

    if (!anyRead) return;

    // ── Publish over BLE/BTHome ──────────────────
    BtHomePayload payload = {};

    // DS18B20 temperatures in stored address order (consistent HA entity mapping)
    for (auto& r : ds18b20Readings) {
        if (r.valid) payload.temperatures.push_back(r.temperatureC);
    }
    // SHT3x temperature appended after DS18B20s
    if (sht3xReading.valid) {
        payload.temperatures.push_back(sht3xReading.temperatureC);
    }

    if (sht3xReading.valid) {
        payload.humidity    = sht3xReading.humidityPct;
        payload.hasHumidity = true;
    }

    if (ina219Reading.valid) {
        payload.voltage    = ina219Reading.busVoltageV;
        payload.hasVoltage = true;
        payload.currentA   = ina219Reading.currentMa / 1000.0f;
        payload.hasCurrent = true;
        payload.powerW     = ina219Reading.powerMw / 1000.0f;
        payload.hasPower   = true;
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
    if (g_deviceConfig.otaEnabled) {
        ota::checkAndApply();
    }

    // ── MQTT config sync ─────────────────────────────────────────────────
    // Subscribe to retained config topic; apply any overrides to NVS.
    // No-op if mqttEnabled is false or broker is not set.
    mqtt_config::syncFromBroker();

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
    meta.deviceId   = g_deviceConfig.deviceName;
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
        Serial.printf("[PHOTO] Upload failed (HTTP %d), retry %u\r\n", code, rtc.photoRetryCount);
    }
}

// ─────────────────────────────────────────────
//  Arduino setup() – runs on every wake/boot
// ─────────────────────────────────────────────
void setup() {
    // ── Reclaim JTAG pins (GPIO 39-42) as normal GPIO ────────────────────
    // ESP32-S3 reserves GPIO 39-42 for the external JTAG interface at reset.
    // gpio_reset_pin() routes each pad through the GPIO matrix instead,
    // making them fully usable as regular IO (OneWire, etc.).
    // No efuse is burned – reversible by re-flashing with JTAG enabled.
    gpio_reset_pin(GPIO_NUM_39);
    gpio_reset_pin(GPIO_NUM_40);
    gpio_reset_pin(GPIO_NUM_41);
    gpio_reset_pin(GPIO_NUM_42);

    Serial.begin(115200);
    delay(100);  // settle UART (CH340 on USB-SERIAL port, stays alive during deep sleep)

    // ── Distinguish cold boot from deep-sleep timer wake ─────────────────
    // Use the RTC memory magic sentinel – reliable across all reset sources
    // (power-on, reset pin, watchdog, JTAG, deep sleep timer).
    // esp_sleep_get_wakeup_cause() is NOT used because it returns UNDEFINED
    // for JTAG-triggered resets even after a deep sleep cycle.
    // magic is set by time_manager::init() on first cold boot, persists in RTC RAM.
    bool isColdBoot = (getRtcState().magic != RTC_MAGIC);

    // ── Load runtime configuration from NVS (must be first) ─────────────
    device_config::load();

    Serial.printf("\r\n\r\n=== Boot #%u  fw:%s  dev:%s  [%s] ===\r\n",
                  getRtcState().bootCount + 1, FIRMWARE_VERSION,
                  g_deviceConfig.deviceName,
                  isColdBoot ? "COLD BOOT" : "DEEP SLEEP WAKE");

    // ── Serial CLI + web config window (cold boot only) ──────────────────
    // On deep-sleep wakes this is skipped entirely to save time and power.
    if (isColdBoot) {
        serial_cli::offerConfigWindow();
    }

    // ── Open runtime NVS namespace ───────────────────────────────────────
    nvs::begin();

    // ── Maintenance mode check (cold boot only) ──────────────────────────
    // Triggered by holding OTA_MAINTENANCE_GPIO low at boot, or by a NVS flag.
    // On deep-sleep wakes, skip (press reset to enter maintenance mode).
    if (isColdBoot && ota::isMaintenanceModeRequested()) {
        Serial.println("[MAIN] Maintenance mode requested – entering ArduinoOTA standby");
        ota::enterMaintenanceMode(OTA_MAINTENANCE_TIMEOUT_MS);
        // Falls through if no OTA push arrives within the timeout.
    }

    // ── Time init ────────────────────────────────────────────────────────
    bool timeOk = time_manager::init();

    if (!timeOk) {
        Serial.println("[MAIN] Time not trusted – syncing NTP...");
        if (uploader::wifiConnect()) {
            time_manager::syncNtp();
            uploader::wifiDisconnect();
        } else {
            Serial.println("[MAIN] WiFi unavailable – will retry next wake");
        }
    }

    // ── Display current time ─────────────────────────────────────────────
    if (time_manager::isTrusted()) {
        struct tm now_tm;
        time_manager::nowLocal(now_tm);
        char timeBuf[32];
        strftime(timeBuf, sizeof(timeBuf), "%Y-%m-%d %H:%M:%S", &now_tm);
        Serial.printf("[TIME] %s\r\n", timeBuf);
    } else {
        Serial.println("[TIME] Heure inconnue (NTP non synchronisé)");
    }

    // ── I2C init for sensors ─────────────────────────────────────────────
    initI2C();

    // ── Determine which tasks are due ────────────────────────────────────
    TaskFlags flags = scheduler::evaluate();

    Serial.printf("[SCHED] Tasks: DS18B20=%d SHT3x=%d INA219=%d Photo=%d\r\n",
                  flags.readDs18b20, flags.readSht3x, flags.readIna219, flags.takePhoto);

    // ── Run sensor tasks (publishes via BTHome BLE) ───────────────────────
    runSensorTasks(flags);

    // ── Run photo task (uses WiFi + camera) ──────────────────────────────
    if (flags.takePhoto) {
        runPhotoTask();
    }

    // ── Sleep until next event ───────────────────────────────────────────
    uint32_t sleepSecs = scheduler::nextSleepSeconds(flags);
    scheduler::deepSleep(sleepSecs);  // does not return
}

void loop() {
    // Never reached – firmware runs in setup() and returns to deep sleep
}

