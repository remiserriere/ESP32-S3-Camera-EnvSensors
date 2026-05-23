#include "persistence.h"
#include <Arduino.h>
#include <Preferences.h>
#include <array>
#include <vector>

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

void nvs::saveDs18b20Addresses(const std::vector<std::array<uint8_t, 8>>& addrs) {
    uint8_t n = (uint8_t)addrs.size();
    prefs.putUChar("ds_n", n);
    for (uint8_t i = 0; i < n; i++) {
        char key[6];
        snprintf(key, sizeof(key), "ds_%u", i);
        prefs.putBytes(key, addrs[i].data(), 8);
    }
}

std::vector<std::array<uint8_t, 8>> nvs::loadDs18b20Addresses() {
    std::vector<std::array<uint8_t, 8>> addrs;
    uint8_t n = prefs.getUChar("ds_n", 0);
    for (uint8_t i = 0; i < n; i++) {
        char key[6];
        snprintf(key, sizeof(key), "ds_%u", i);
        std::array<uint8_t, 8> addr = {};
        if (prefs.getBytes(key, addr.data(), 8) == 8)
            addrs.push_back(addr);
    }
    return addrs;
}

void nvs::end() {
    prefs.end();
}
