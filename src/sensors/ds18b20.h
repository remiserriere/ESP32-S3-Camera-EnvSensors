#pragma once
#include <array>
#include <cstdint>
#include <vector>

struct Ds18b20Reading {
    float temperatureC;
    uint8_t address[8];  // OneWire ROM address
    bool valid;
};

namespace ds18b20 {
    // Initialise the OneWire bus and discover sensors.
    // Returns number of sensors found.
    uint8_t begin();

    // Read all sensors on the bus.
    // Blocks for conversion time (~750 ms at 12-bit resolution).
    std::vector<Ds18b20Reading> readAll();

    // Returns the number of sensors discovered during begin().
    uint8_t sensorCount();

    // Scan the OneWire bus, save discovered ROM addresses to NVS for consistent
    // sensor ordering across boots. Returns the number of sensors found.
    uint8_t discoverAndStore();

    // Returns the ROM addresses used by the last begin() or discoverAndStore() call.
    // Empty if no sensors found or begin() not yet called.
    const std::vector<std::array<uint8_t, 8>>& storedAddresses();
}
