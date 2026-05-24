#include "sht3x.h"
#include "../config.h"
#include <Arduino.h>
#include <Adafruit_SHT31.h>
#include <Wire.h>

static Adafruit_SHT31 _sht31;
static bool _ready = false;

bool sht3x::begin() {
    _ready = _sht31.begin(SHT3X_I2C_ADDR);
    if (_ready) {
        Serial.println("[SHT3x] Sensor initialised");
    } else {
        Serial.println("[SHT3x] Not found – check wiring/address");
    }
    return _ready;
}

Sht3xReading sht3x::read() {
    Sht3xReading r = {};
    if (!_ready) { return r; }

    r.temperatureC = _sht31.readTemperature();
    r.humidityPct  = _sht31.readHumidity();
    r.valid = !isnan(r.temperatureC) && !isnan(r.humidityPct);

    if (r.valid) {
        Serial.printf("[SHT3x] T=%.2f°C  RH=%.1f%%\r\n", r.temperatureC, r.humidityPct);
    } else {
        Serial.println("[SHT3x] Read failed");
    }
    return r;
}
