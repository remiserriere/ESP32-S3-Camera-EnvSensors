#include "ds18b20.h"
#include "../config.h"
#include "../persistence.h"
#include <Arduino.h>
#include <OneWire.h>
#include <DallasTemperature.h>

static OneWire           oneWire(DS18B20_PIN);
static DallasTemperature sensors(&oneWire);
static uint8_t           _count = 0;
static std::vector<std::array<uint8_t, 8>> _addresses;

uint8_t ds18b20::begin() {
    sensors.begin();
    sensors.setResolution(DS18B20_RESOLUTION);
    uint8_t busCount = sensors.getDeviceCount();

    // Restore NVS-stored address ordering for consistent sensor-to-entity mapping.
    // nvs::begin() is idempotent – safe if NVS is already open.
    nvs::begin();
    auto stored = nvs::loadDs18b20Addresses();

    if (!stored.empty() && (uint8_t)stored.size() == busCount) {
        _addresses = stored;
        _count     = busCount;
        Serial.printf("[DS18B20] %u sensor(s), order from NVS\n", _count);
    } else {
        _count = busCount;
        _addresses.resize(_count);
        for (uint8_t i = 0; i < _count; i++)
            sensors.getAddress(_addresses[i].data(), i);
        if (!stored.empty())
            Serial.printf("[DS18B20] Stored count (%zu) != bus (%u) – using discovery order\n",
                          stored.size(), busCount);
        else
            Serial.printf("[DS18B20] Found %u sensor(s)\n", _count);
    }
    return _count;
}

uint8_t ds18b20::discoverAndStore() {
    sensors.begin();
    sensors.setResolution(DS18B20_RESOLUTION);
    _count = sensors.getDeviceCount();
    _addresses.resize(_count);
    for (uint8_t i = 0; i < _count; i++)
        sensors.getAddress(_addresses[i].data(), i);
    nvs::begin();  // idempotent
    nvs::saveDs18b20Addresses(_addresses);
    Serial.printf("[DS18B20] Discovered and stored %u sensor(s)\n", _count);
    return _count;
}

std::vector<Ds18b20Reading> ds18b20::readAll() {
    std::vector<Ds18b20Reading> results;
    if (_count == 0) return results;

    sensors.requestTemperatures();

    // Read in stored address order for consistent Home Assistant entity mapping.
    for (size_t i = 0; i < _addresses.size(); i++) {
        Ds18b20Reading r = {};
        memcpy(r.address, _addresses[i].data(), 8);
        float t = sensors.getTempC(_addresses[i].data());
        r.temperatureC = t;
        r.valid = (t != DEVICE_DISCONNECTED_C);
        results.push_back(r);
        Serial.printf("[DS18B20] Sensor %zu [%02X..%02X]: %.2f C%s\n",
                      i, r.address[0], r.address[7], t, r.valid ? "" : " (INVALID)");
    }
    return results;
}

uint8_t ds18b20::sensorCount() { return _count; }

const std::vector<std::array<uint8_t, 8>>& ds18b20::storedAddresses() {
    return _addresses;
}
