#include "scheduler.h"
#include "config.h"
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

    flags.readDs18b20   = isDue(rtc.lastDs18b20ReadS,   DS18B20_INTERVAL_MIN);
    flags.readSht3x     = isDue(rtc.lastSht3xReadS,     SHT3X_INTERVAL_MIN);
    flags.readIna219    = INA219_ENABLED   && isDue(rtc.lastIna219ReadS,    INA219_INTERVAL_MIN);
    flags.readLc709203f = LC709203F_ENABLED && isDue(rtc.lastLc709203fReadS, LC709203F_INTERVAL_MIN);

    // Photo: due if we haven't taken one today AND we are in/past the target window
    if (time_manager::isTrusted()) {
        struct tm now_tm;
        time_manager::nowLocal(now_tm);

        bool sameDay = (now_tm.tm_year + 1900 == rtc.lastPhotoYear) &&
                       (now_tm.tm_yday          == rtc.lastPhotoDayOfYear);

        if (!sameDay) {
            int currentMinutes = now_tm.tm_hour * 60 + now_tm.tm_min;
            int targetMinutes  = PHOTO_HOUR * 60 + PHOTO_MINUTE;
            int windowEnd      = targetMinutes + PHOTO_WINDOW_MIN;

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

    consider(secondsUntilDue(rtc.lastDs18b20ReadS,   DS18B20_INTERVAL_MIN));
    consider(secondsUntilDue(rtc.lastSht3xReadS,     SHT3X_INTERVAL_MIN));

    if (INA219_ENABLED) {
        consider(secondsUntilDue(rtc.lastIna219ReadS, INA219_INTERVAL_MIN));
    }

    if (LC709203F_ENABLED) {
        consider(secondsUntilDue(rtc.lastLc709203fReadS, LC709203F_INTERVAL_MIN));
    }

    // Also consider the upcoming photo window
    if (time_manager::isTrusted()) {
        struct tm now_tm;
        time_manager::nowLocal(now_tm);
        int currentMinutes = now_tm.tm_hour * 60 + now_tm.tm_min;
        int targetMinutes  = PHOTO_HOUR * 60 + PHOTO_MINUTE;

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

void scheduler::deepSleep(uint32_t seconds) {
    // Persist epoch so we can restore after power cycle
    RtcState& rtc      = getRtcState();
    rtc.lastEpochS     = time_manager::nowEpoch();
    rtc.lastEpochSetMs = 0;  // millis() will be ~0 after wakeup
    nvs::saveEpoch(rtc.lastEpochS);
    nvs::end();

    Serial.printf("[SLEEP] Entering deep sleep for %u seconds\n", seconds);
    Serial.flush();
    delay(100);

    esp_sleep_enable_timer_wakeup((uint64_t)seconds * 1000000ULL);
    esp_deep_sleep_start();
}
