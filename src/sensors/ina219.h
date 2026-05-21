#pragma once
#include <cstdint>

struct Ina219Reading {
    float busVoltageV;    // V
    float shuntVoltageMv; // mV
    float currentMa;      // mA
    float powerMw;        // mW
    bool  valid;
};

namespace ina219 {
    // Initialise the INA219. Returns true on success.
    bool begin();

    // Read voltage, current, and power.
    Ina219Reading read();
}
