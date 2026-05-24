#include "ina219.h"
#include "../config.h"
#include <Arduino.h>
#include <Adafruit_INA219.h>

static Adafruit_INA219 _ina219(INA219_I2C_ADDR);
static bool _ready = false;

bool ina219::begin() {
    _ready = _ina219.begin();
    if (_ready) {
        Serial.println("[INA219] Sensor initialised");
    } else {
        Serial.println("[INA219] Not found – check wiring/address");
    }
    return _ready;
}

Ina219Reading ina219::read() {
    Ina219Reading r = {};
    if (!_ready) return r;

    r.busVoltageV    = _ina219.getBusVoltage_V();
    r.shuntVoltageMv = _ina219.getShuntVoltage_mV();
    r.currentMa      = _ina219.getCurrent_mA();
    r.powerMw        = _ina219.getPower_mW();
    r.valid = true;  // INA219 library doesn't return explicit validity flags; assume OK if begin() succeeded

    Serial.printf("[INA219] V=%.3fV  I=%.2fmA  P=%.2fmW\r\n",
                  r.busVoltageV, r.currentMa, r.powerMw);
    return r;
}
