#pragma once
#include <cstdint>
#include <ctime>

namespace time_manager {
    // Call once at start of each wake cycle.
    // Restores best-known time from RTC state / NVS.
    // Returns true if time is currently trustworthy.
    bool init();

    // Perform NTP synchronisation (requires WiFi to be up).
    // Updates RTC state and NVS on success.
    // Returns true on success.
    bool syncNtp();

    // Returns true if time is trustworthy and no re-sync is needed.
    bool isTrusted();

    // Returns true if enough time has elapsed since last sync to warrant a re-sync
    // (used before photo scheduling to avoid large drift).
    bool needsResync();

    // Returns the current best-estimate epoch (seconds).
    // Uses RTC + elapsed millis() to estimate if NTP not recently done.
    int64_t nowEpoch();

    // Fills a tm struct with local time based on configured timezone.
    void nowLocal(struct tm& t);
}
