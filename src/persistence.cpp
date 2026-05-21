#include "persistence.h"
#include <Arduino.h>
#include <Preferences.h>

// RTC-retained memory region – survives deep sleep
RTC_DATA_ATTR static RtcState rtcState;

RtcState& getRtcState() {
    return rtcState;
}

// ─────────────────────────────────────────────
//  NVS helpers
// ─────────────────────────────────────────────
static Preferences prefs;
static constexpr const char* NVS_NS = "env_fw";

void nvs::begin() {
    prefs.begin(NVS_NS, false);
}

void nvs::saveEpoch(int64_t epochS) {
    prefs.putLong64("epoch", epochS);
}

int64_t nvs::loadEpoch() {
    return prefs.getLong64("epoch", 0LL);
}

void nvs::saveTimeTrusted(bool trusted) {
    prefs.putBool("time_ok", trusted);
}

bool nvs::loadTimeTrusted() {
    return prefs.getBool("time_ok", false);
}

void nvs::saveLastPhoto(int32_t year, int32_t dayOfYear) {
    prefs.putInt("photo_yr", year);
    prefs.putInt("photo_dy", dayOfYear);
}

void nvs::loadLastPhoto(int32_t& year, int32_t& dayOfYear) {
    year      = prefs.getInt("photo_yr", -1);
    dayOfYear = prefs.getInt("photo_dy", -1);
}

void nvs::end() {
    prefs.end();
}
