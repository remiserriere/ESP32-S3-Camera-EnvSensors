#pragma once
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
}
