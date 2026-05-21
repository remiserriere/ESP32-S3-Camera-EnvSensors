#pragma once
#include <cstdint>

namespace ota {

    // ── HTTP OTA (production) ──────────────────────────────────────────────
    //
    // During a Wi-Fi session (e.g. after photo upload), call checkAndApply()
    // to fetch the OTA manifest, compare versions, and flash if newer.
    //
    // Manifest format (JSON, served by OTA_MANIFEST_URL):
    //   {
    //     "version": "v1.2.3",
    //     "url":     "http://server/firmware.bin",
    //     "notes":   "optional release notes"
    //   }
    //
    // Returns true if an update was applied (device will restart automatically).
    // Returns false if already up-to-date, manifest unreachable, or flash error.
    bool checkAndApply();

    // ── ArduinoOTA maintenance mode (development) ──────────────────────────
    //
    // Keeps the device awake with Wi-Fi up and advertises an ArduinoOTA service.
    // Allows `pio run --target upload --upload-port <device-IP>` from a PC.
    //
    // Triggered by:
    //   • Holding OTA_MAINTENANCE_GPIO low at boot (if GPIO >= 0)
    //   • Setting the NVS maintenance-mode flag via requestMaintenanceMode(true)
    //
    // The NVS flag is cleared automatically when maintenance mode is entered so
    // a stuck flag cannot prevent the device from sleeping forever.
    //
    // timeoutMs: give up if no OTA push arrives within this window (ms).
    void enterMaintenanceMode(uint32_t timeoutMs = 120000);

    // Returns true if the NVS maintenance-mode flag is set OR if the GPIO
    // trigger is active.  Call once on boot before nvs::end().
    bool isMaintenanceModeRequested();

    // Persist / clear the NVS maintenance-mode flag.
    // Set enable=true from an external system (e.g. Home Assistant webhook
    // calling the upload service which then calls a small REST endpoint you
    // expose, or simply via a serial command) to request the next wake cycle
    // to enter maintenance mode.
    void requestMaintenanceMode(bool enable);

} // namespace ota
