#include "lc709203f.h"
#include "../config.h"
#include <Arduino.h>
#include <Adafruit_LC709203F.h>

static Adafruit_LC709203F _gauge;
static bool _ready = false;

bool lc709203f::begin() {
    _ready = _gauge.begin();
    if (_ready) {
        _gauge.setPackAPA(LC709203F_APA);
        _gauge.setThermistorB(3950);  // typical 10k NTC B-constant; adjust if known
        Serial.println("[LC709203F] Gauge initialised");
    } else {
        Serial.println("[LC709203F] Not found – check wiring");
    }
    return _ready;
}

Lc709203fReading lc709203f::read() {
    Lc709203fReading r = {};
    if (!_ready) return r;

    r.batteryVoltageV  = _gauge.cellVoltage();
    r.batteryPercent   = _gauge.cellPercent();
    r.cellTemperatureC = _gauge.getCellTemperature();
    r.valid = (r.batteryVoltageV > 0.0f);

    Serial.printf("[LC709203F] V=%.3fV  SoC=%.1f%%  T=%.1f°C\n",
                  r.batteryVoltageV, r.batteryPercent, r.cellTemperatureC);
    return r;
}
