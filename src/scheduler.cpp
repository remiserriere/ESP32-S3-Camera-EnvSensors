#include "scheduler.h"
#include "config.h"
#include "device_config.h"
#include "persistence.h"
#include "time_manager.h"
#include <Arduino.h>
#include <esp_sleep.h>
#include <time.h>

// Helper: seconds since last read vs. required interval
static bool isDue(int64_t lastReadS, uint32_t intervalMin) {
    if (lastReadS == 0) return true;   // never read
    int64_t now     = time_manager::nowEpoch();
    int64_t elapsed = now - lastReadS;
    return (elapsed >= (int64_t)(intervalMin * 60));
}

// Helper: next read due in seconds (0 = overdue)
static uint32_t secondsUntilDue(int64_t lastReadS, uint32_t intervalMin) {
    if (lastReadS == 0) return 0;
    int64_t now       = time_manager::nowEpoch();
    int64_t next      = lastReadS + (int64_t)(intervalMin * 60);
    int64_t remaining = next - now;
    return (remaining > 0) ? (uint32_t)remaining : 0;
}

TaskFlags scheduler::evaluate() {
    TaskFlags flags = {};
    RtcState& rtc   = getRtcState();

    flags.readDs18b20 = g_deviceConfig.ds18b20Enabled
                        && isDue(rtc.lastDs18b20ReadS, g_deviceConfig.ds18b20IntervalMin);
    flags.readSht3x   = g_deviceConfig.sht3xEnabled
                        && isDue(rtc.lastSht3xReadS,   g_deviceConfig.sht3xIntervalMin);
    flags.readIna219  = g_deviceConfig.ina219Enabled
                        && isDue(rtc.lastIna219ReadS,  g_deviceConfig.ina219IntervalMin);

    // Photo: due if we haven't taken one today AND we are in/past the target window
    if (time_manager::isTrusted()) {
        struct tm now_tm;
        time_manager::nowLocal(now_tm);

        bool sameDay = (now_tm.tm_year + 1900 == rtc.lastPhotoYear) &&
                       (now_tm.tm_yday          == rtc.lastPhotoDayOfYear);

        if (!sameDay) {
            int currentMinutes = now_tm.tm_hour * 60 + now_tm.tm_min;
            int targetMinutes  = g_deviceConfig.photoHour * 60 + g_deviceConfig.photoMinute;
            int windowEnd      = targetMinutes + g_deviceConfig.photoWindowMin;

            flags.takePhoto = (currentMinutes >= targetMinutes && currentMinutes < windowEnd);
        }
    }

    return flags;
}

uint32_t scheduler::nextSleepSeconds(const TaskFlags& completed) {
    RtcState& rtc = getRtcState();
    uint32_t  minSleep = SCHEDULER_MAX_SLEEP_S;

    auto consider = [&](uint32_t s) {
        if (s < minSleep) minSleep = s;
    };

    if (g_deviceConfig.ds18b20Enabled)
        consider(secondsUntilDue(rtc.lastDs18b20ReadS, g_deviceConfig.ds18b20IntervalMin));
    if (g_deviceConfig.sht3xEnabled)
        consider(secondsUntilDue(rtc.lastSht3xReadS,   g_deviceConfig.sht3xIntervalMin));
    if (g_deviceConfig.ina219Enabled)
        consider(secondsUntilDue(rtc.lastIna219ReadS,  g_deviceConfig.ina219IntervalMin));

    // Also consider the upcoming photo window
    if (time_manager::isTrusted()) {
        struct tm now_tm;
        time_manager::nowLocal(now_tm);
        int currentMinutes = now_tm.tm_hour * 60 + now_tm.tm_min;
        int targetMinutes  = g_deviceConfig.photoHour * 60 + g_deviceConfig.photoMinute;

        int minutesToPhoto;
        if (currentMinutes < targetMinutes) {
            minutesToPhoto = targetMinutes - currentMinutes;
        } else {
            // Target passed for today – sleep until tomorrow's window
            minutesToPhoto = (24 * 60 - currentMinutes) + targetMinutes;
        }
        consider((uint32_t)(minutesToPhoto * 60));
    }

    // Enforce minimum granularity
    if (minSleep < SCHEDULER_MIN_WAKE_INTERVAL_S)
        minSleep = SCHEDULER_MIN_WAKE_INTERVAL_S;

    return minSleep;
}

uint32_t scheduler::secondsUntilNextPhoto() {
    if (!time_manager::isTrusted()) return UINT32_MAX;

    RtcState& rtc = getRtcState();
    struct tm now_tm;
    time_manager::nowLocal(now_tm);

    int currentMinutes = now_tm.tm_hour * 60 + now_tm.tm_min;
    int targetMinutes  = g_deviceConfig.photoHour * 60 + g_deviceConfig.photoMinute;

    bool takenToday = (now_tm.tm_year + 1900 == rtc.lastPhotoYear) &&
                      (now_tm.tm_yday          == rtc.lastPhotoDayOfYear);

    int minutesUntil;
    if (!takenToday) {
        if (currentMinutes <= targetMinutes) {
            // Window not yet reached today
            minutesUntil = targetMinutes - currentMinutes;
        } else {
            // Window already passed today without a photo – wait until tomorrow
            minutesUntil = (24 * 60 - currentMinutes) + targetMinutes;
        }
    } else {
        // Already taken today – next opportunity is tomorrow
        minutesUntil = (24 * 60 - currentMinutes) + targetMinutes;
    }

    return (uint32_t)(minutesUntil * 60);
}

void scheduler::deepSleep(uint32_t seconds) {
    RtcState& rtc  = getRtcState();
    int64_t   now  = time_manager::nowEpoch();

    // NVS: save the actual current time for power-cycle recovery.
    nvs::saveEpoch(now);
    nvs::end();

    // RTC: save the *projected wake time* (now + sleep duration).
    // On wakeup, millis() restarts from ~0, so:
    //   estimated = lastEpochS + millis()/1000 ≈ lastEpochS = projected wake time  ✓
    // Without this, the device would perpetually think it is still at sleep time.
    rtc.lastEpochS     = now + (int64_t)seconds;
    rtc.lastEpochSetMs = 0;

    Serial.printf("[SLEEP] Entering deep sleep for %u seconds\r\n", seconds);
    Serial.flush();
    delay(100);

    esp_sleep_enable_timer_wakeup((uint64_t)seconds * 1000000ULL);
    esp_deep_sleep_start();
}
