#include "ds18b20.h"
#include "../config.h"
#include <Arduino.h>
#include <OneWire.h>
#include <DallasTemperature.h>

static OneWire           oneWire(DS18B20_PIN);
static DallasTemperature sensors(&oneWire);
static uint8_t           _count = 0;

uint8_t ds18b20::begin() {
    sensors.begin();
    sensors.setResolution(DS18B20_RESOLUTION);
    _count = sensors.getDeviceCount();
    Serial.printf("[DS18B20] Found %u sensor(s)\n", _count);
    return _count;
}

std::vector<Ds18b20Reading> ds18b20::readAll() {
    std::vector<Ds18b20Reading> results;
    if (_count == 0) return results;

    sensors.requestTemperatures();

    for (uint8_t i = 0; i < _count; i++) {
        Ds18b20Reading r = {};
        sensors.getAddress(r.address, i);
        float t = sensors.getTempCByIndex(i);
        r.temperatureC = t;
        r.valid = (t != DEVICE_DISCONNECTED_C);
        results.push_back(r);
        Serial.printf("[DS18B20] Sensor %u: %.2f°C%s\n", i, t, r.valid ? "" : " (INVALID)");
    }
    return results;
}

uint8_t ds18b20::sensorCount() { return _count; }
