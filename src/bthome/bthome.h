#pragma once
#include <cstdint>
#include <vector>

// Sensor payload for BTHome advertisement.
// All fields are optional – set valid=true for fields you want to include.
struct BtHomePayload {
    // Temperatures (BTHome Object ID 0x02, int16, 0.01°C).
    // Each entry creates a separate Temperature entity in Home Assistant.
    // Populate in order: DS18B20 sensors (by stored ROM index), then SHT3x.
    std::vector<float> temperatures;

    // Humidity (BTHome Object ID 0x03, uint16, 0.01%)
    float   humidity;            // %
    bool    hasHumidity;

    // Battery level (BTHome Object ID 0x01, uint8, 1%)
    uint8_t batteryPercent;
    bool    hasBattery;

    // Voltage (BTHome Object ID 0x0C, uint16, 0.001V)
    float   voltage;             // V
    bool    hasVoltage;

    // Current (BTHome Object ID 0x43, uint16, 0.001A)
    float   currentA;            // A
    bool    hasCurrent;

    // Power (BTHome Object ID 0x0B, uint24, 0.01W)
    float   powerW;              // W
    bool    hasPower;

    // Diagnostic timing (sent as Manufacturer Specific Data in BLE scan response).
    // Format: company 0xFFFF | uint16 LE next_wakeup_s | uint16 LE next_photo_s.
    // Values >= 0xFFFF mean "unknown".
    uint32_t nextWakeupS;        // seconds until next deep-sleep wakeup
    uint32_t nextPhotoS;         // seconds until next scheduled photo (UINT32_MAX = unknown)
    bool     hasDiag;
};

namespace bthome {
    // Initialise NimBLE stack. Call once per wake cycle before advertise().
    void begin();

    // Build a BTHome advertisement payload, broadcast for BTHOME_ADV_DURATION_MS ms, then stop.
    // WiFi must NOT be active at the same time (radio coexistence constraints).
    void advertise(const BtHomePayload& payload);

    // Stop advertising and release BLE resources to save power.
    void end();
}
