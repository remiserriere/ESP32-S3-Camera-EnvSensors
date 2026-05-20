#pragma once
#include <cstdint>

// ─────────────────────────────────────────────
//  RTC memory – survives deep sleep, lost on power cycle
// ─────────────────────────────────────────────
struct RtcState {
    uint32_t magic;                  // sentinel to detect cold boot
    uint32_t bootCount;              // total deep-sleep wake count
    int64_t  lastEpochS;             // last known Unix epoch (seconds), set after NTP
    uint32_t lastEpochSetMs;         // millis() when lastEpochS was set (for drift calc)
    bool     timeTrusted;            // true once we've done at least one successful NTP sync

    // Last successful sensor read timestamps (Unix epoch seconds, 0 = never)
    int64_t  lastDs18b20ReadS;
    int64_t  lastSht3xReadS;
    int64_t  lastIna219ReadS;
    int64_t  lastLc709203fReadS;

    // Photo tracking
    int32_t  lastPhotoDayOfYear;     // yday of last successful photo (-1 = never)
    int32_t  lastPhotoYear;          // year of last successful photo
    uint8_t  photoRetryCount;        // consecutive upload failures
};

static constexpr uint32_t RTC_MAGIC = 0xCAFEBEEF;

// Must be in RTC_DATA_ATTR (declared in persistence.cpp)
// Provide accessors so other TUs don't need the attribute themselves.
RtcState& getRtcState();

// ─────────────────────────────────────────────
//  NVS (Preferences) – survives power cycles
// ─────────────────────────────────────────────
namespace nvs {
    void begin();

    // Persist last known epoch so we can restore after hard reset
    void saveEpoch(int64_t epochS);
    int64_t loadEpoch();            // returns 0 if never saved

    // Flag for initial NTP sync completed
    void saveTimeTrusted(bool trusted);
    bool loadTimeTrusted();

    // Last photo metadata
    void saveLastPhoto(int32_t year, int32_t dayOfYear);
    void loadLastPhoto(int32_t& year, int32_t& dayOfYear);

    void end();
}
