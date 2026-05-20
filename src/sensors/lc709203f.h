#pragma once
#include <cstdint>

struct Lc709203fReading {
    float batteryVoltageV;   // V
    float batteryPercent;    // %  (State of Charge)
    float cellTemperatureC;  // °C (thermistor on module)
    bool  valid;
};

namespace lc709203f {
    // Initialise the LC709203F fuel gauge. Returns true on success.
    bool begin();

    // Read battery voltage, SoC, and cell temperature.
    Lc709203fReading read();
}
