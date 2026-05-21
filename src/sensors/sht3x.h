#pragma once
#include <cstdint>

struct Sht3xReading {
    float temperatureC;
    float humidityPct;
    bool  valid;
};

namespace sht3x {
    // Initialise the SHT3x sensor over I2C.
    // Returns true on success.
    bool begin();

    // Read temperature and humidity.
    Sht3xReading read();
}
