#include "time_manager.h"
#include "config.h"
#include "persistence.h"
#include <Arduino.h>
#include <WiFi.h>
#include <esp_sntp.h>
#include <time.h>

static bool _trusted = false;

bool time_manager::init() {
    RtcState& rtc = getRtcState();

    // On first cold boot (magic mismatch), try to restore from NVS
    if (rtc.magic != RTC_MAGIC) {
        rtc.magic              = RTC_MAGIC;
        rtc.bootCount          = 0;
        rtc.lastEpochS         = nvs::loadEpoch();
        rtc.lastEpochSetMs     = 0;
        rtc.timeTrusted        = nvs::loadTimeTrusted();
        rtc.lastDs18b20ReadS   = 0;
        rtc.lastSht3xReadS     = 0;
        rtc.lastIna219ReadS    = 0;
        rtc.lastLc709203fReadS = 0;
        rtc.photoRetryCount    = 0;
        nvs::loadLastPhoto(rtc.lastPhotoYear, rtc.lastPhotoDayOfYear);
    }
    rtc.bootCount++;

    _trusted = rtc.timeTrusted && (rtc.lastEpochS > 0);

    if (_trusted && rtc.lastEpochS > 0) {
        // Estimate current time: RTC epoch + time elapsed since we set it
        uint32_t elapsedMs = millis() - rtc.lastEpochSetMs;
        int64_t  estimated = rtc.lastEpochS + (int64_t)(elapsedMs / 1000);

        // Push to system clock so time() / localtime() work
        struct timeval tv = { .tv_sec  = (time_t)estimated, .tv_usec = 0 };
        settimeofday(&tv, nullptr);
        setenv("TZ", NTP_TIMEZONE, 1);
        tzset();
    }

    return _trusted;
}

bool time_manager::syncNtp() {
    Serial.println("[NTP] Starting synchronisation...");

    setenv("TZ", NTP_TIMEZONE, 1);
    tzset();

    configTzTime(NTP_TIMEZONE, NTP_SERVER_1, NTP_SERVER_2);

    // Wait for sync
    uint32_t start = millis();
    while (sntp_get_sync_status() == SNTP_SYNC_STATUS_RESET) {
        if (millis() - start > NTP_SYNC_TIMEOUT_MS) {
            Serial.println("[NTP] Sync timeout");
            return false;
        }
        delay(200);
    }

    time_t now = time(nullptr);
    if (now < 1000000000L) {
        Serial.println("[NTP] Sync returned invalid time");
        return false;
    }

    // Persist
    RtcState& rtc       = getRtcState();
    rtc.lastEpochS      = (int64_t)now;
    rtc.lastEpochSetMs  = millis();
    rtc.timeTrusted     = true;
    _trusted            = true;

    nvs::saveEpoch(rtc.lastEpochS);
    nvs::saveTimeTrusted(true);

    char buf[32];
    strftime(buf, sizeof(buf), "%Y-%m-%d %H:%M:%S", localtime(&now));
    Serial.printf("[NTP] Synced: %s\n", buf);
    return true;
}

bool time_manager::isTrusted() {
    return _trusted;
}

bool time_manager::needsResync() {
    if (!_trusted) return true;
    RtcState& rtc   = getRtcState();
    int64_t   now   = time_manager::nowEpoch();
    int64_t   age   = now - rtc.lastEpochS;
    return (age > (int64_t)NTP_MAX_AGE_BEFORE_RESYNC_S);
}

int64_t time_manager::nowEpoch() {
    return (int64_t)time(nullptr);
}

void time_manager::nowLocal(struct tm& t) {
    time_t now = (time_t)time_manager::nowEpoch();
    localtime_r(&now, &t);
}
