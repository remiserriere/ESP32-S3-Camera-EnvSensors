#include "bthome.h"
#include "../config.h"
#include "../device_config.h"
#include <Arduino.h>
#include <NimBLEDevice.h>

// BTHome v2 specification:
//   Service UUID  : 0xFCD2
//   Device Info   : bit7=0 (not encrypted), bits6-5=version=2 → 0x40
//   Object format : [ID][VALUE...]  (little-endian, objects in ascending ID order)
//
// Object IDs used:
//   0x01 – Battery          uint8,   factor 1     %
//   0x02 – Temperature      int16,   factor 0.01  °C  (one entry per sensor, ascending)
//   0x03 – Humidity         uint16,  factor 0.01  %
//   0x0B – Power            uint24,  factor 0.01  W
//   0x0C – Voltage          uint16,  factor 0.001 V
//   0x43 – Current          uint16,  factor 0.001 A

static constexpr uint8_t BTHOME_DEVICE_INFO = 0x40;  // version 2, non-encrypted

static void appendU8(std::vector<uint8_t>& buf, uint8_t id, uint8_t val) {
    buf.push_back(id);
    buf.push_back(val);
}

static void appendI16(std::vector<uint8_t>& buf, uint8_t id, int16_t val) {
    buf.push_back(id);
    buf.push_back((uint8_t)(val & 0xFF));
    buf.push_back((uint8_t)((val >> 8) & 0xFF));
}

static void appendU16(std::vector<uint8_t>& buf, uint8_t id, uint16_t val) {
    buf.push_back(id);
    buf.push_back((uint8_t)(val & 0xFF));
    buf.push_back((uint8_t)((val >> 8) & 0xFF));
}

static void appendU24(std::vector<uint8_t>& buf, uint8_t id, uint32_t val) {
    buf.push_back(id);
    buf.push_back((uint8_t)(val & 0xFF));
    buf.push_back((uint8_t)((val >> 8) & 0xFF));
    buf.push_back((uint8_t)((val >> 16) & 0xFF));
}

static std::vector<uint8_t> buildServiceData(const BtHomePayload& p) {
    std::vector<uint8_t> data;
    data.push_back(BTHOME_DEVICE_INFO);

    // Objects MUST be in ascending Object ID order per BTHome spec
    if (p.hasBattery) {
        appendU8(data, 0x01, p.batteryPercent);
    }
    for (float t : p.temperatures) {
        int16_t raw = (int16_t)roundf(t * 100.0f);
        appendI16(data, 0x02, raw);
    }
    if (p.hasHumidity) {
        uint16_t raw = (uint16_t)roundf(p.humidity * 100.0f);
        appendU16(data, 0x03, raw);
    }
    // 0x0B Power MUST come before 0x0C Voltage (ascending ID order required by BTHome spec)
    if (p.hasPower) {
        uint32_t raw = (uint32_t)roundf(p.powerW * 100.0f);
        appendU24(data, 0x0B, raw);
    }
    if (p.hasVoltage) {
        uint16_t raw = (uint16_t)roundf(p.voltage * 1000.0f);
        appendU16(data, 0x0C, raw);
    }
    if (p.hasCurrent) {
        uint16_t raw = (uint16_t)roundf(p.currentA * 1000.0f);
        appendU16(data, 0x43, raw);
    }
    return data;
}

void bthome::begin() {
    NimBLEDevice::init(g_deviceConfig.deviceName);
}

void bthome::advertise(const BtHomePayload& payload) {
    std::vector<uint8_t> serviceData = buildServiceData(payload);

    NimBLEAdvertising* pAdv = NimBLEDevice::getAdvertising();
    pAdv->reset();

    // Main advertisement packet: flags + BTHome service data only.
    // Keeping the name out of the main packet is critical — a long device name
    // (e.g. "rssa-gazmeter01" = 15 chars) pushes the total over the 31-byte BLE limit,
    // causing "Advertisement data length exceeded" and the service data being dropped.
    NimBLEAdvertisementData advData;
    advData.setFlags(0x06);  // BR/EDR not supported, LE General Discoverable
    advData.setServiceData(NimBLEUUID((uint16_t)BTHOME_SERVICE_UUID),
                           std::string(reinterpret_cast<const char*>(serviceData.data()),
                                       serviceData.size()));

    // Scan response packet: name only (sent on explicit scan request).
    // Home Assistant reads the name from scan responses fine.
    NimBLEAdvertisementData scanResp;
    scanResp.setName(g_deviceConfig.deviceName);

    pAdv->setScanResponse(true);
    pAdv->setAdvertisementData(advData);
    pAdv->setScanResponseData(scanResp);

    // NimBLE start() takes duration in seconds; round up to at least 1 s
    uint32_t advSeconds = (BTHOME_ADV_DURATION_MS + 999) / 1000;
    pAdv->start(advSeconds, nullptr);

    Serial.printf("[BTHome] Advertising for %d ms (%zu bytes service data)\n",
                  BTHOME_ADV_DURATION_MS, serviceData.size());
    delay(BTHOME_ADV_DURATION_MS + 100);
    pAdv->stop();
}

void bthome::end() {
    NimBLEDevice::deinit(true);
}
