# ESP32-S3-Camera-EnvSensors

Custom PlatformIO/Arduino firmware for the **Freenove ESP32-S3 WROOM** with OV2640 camera. Reads environmental sensors, publishes data over **BTHome BLE** (compatible with Home Assistant), and captures a daily JPEG photo uploaded via HTTP POST — all with a deep-sleep-first power strategy.

---

## Architecture

```
Boot / Wake
    │
    ├─ Restore time from RTC memory / NVS
    │    └─ If untrusted → connect WiFi → NTP sync → disconnect WiFi
    │
    ├─ Evaluate scheduler (which tasks are due?)
    │
    ├─ Sensor tasks (DS18B20, SHT3x, INA219, LC709203F)
    │    └─ Publish all readings via BTHome BLE advertisement
    │
    ├─ Photo task (if scheduled window reached, not yet taken today)
    │    ├─ Connect WiFi
    │    ├─ NTP re-sync if time is stale (> 6 h since last sync)
    │    ├─ Capture JPEG from OV2640
    │    ├─ HTTP POST multipart upload
    │    └─ Disconnect WiFi
    │
    └─ Deep sleep until next scheduled event
```

**Key design principles:**
- WiFi is only active for NTP sync and photo upload — BLE is used for all sensor telemetry.
- State is stored in RTC-retained memory (survives deep sleep) and NVS/Preferences (survives power cycles).
- The scheduler computes the shortest sleep interval covering all upcoming sensor and photo events.

---

## Supported Sensors

| Sensor | Interface | Measurement | Config constant |
|---|---|---|---|
| DS18B20 | OneWire | Temperature (°C) | `DS18B20_PIN`, `DS18B20_INTERVAL_MIN` |
| SHT3x (SHT30/31/35) | I²C | Temperature + Humidity | `SHT3X_I2C_ADDR`, `SHT3X_INTERVAL_MIN` |
| INA219 | I²C | Bus voltage, current, power | `INA219_I2C_ADDR`, `INA219_ENABLED` |
| LC709203F | I²C | LiPo SoC (%), voltage, temperature | `LC709203F_APA`, `LC709203F_ENABLED` |

### Wiring notes

- **DS18B20**: Connect data line to `DS18B20_PIN` (default GPIO 14) with a 4.7 kΩ pull-up to 3.3 V. Multiple sensors can be chained on the same bus.
- **SHT3x / INA219 / LC709203F**: All share the I²C bus on `I2C_SDA_PIN` (default GPIO 3) and `I2C_SCL_PIN` (default GPIO 2). Verify these GPIOs against your board's pinout — the Freenove ESP32-S3 WROOM exposes I²C on header pins that may differ.
- **Camera**: The OV2640 uses a dedicated SCCB (I²C-like) bus on `CAM_PIN_SIOD` / `CAM_PIN_SIOC` (GPIO 4 / 5), independent from the sensor I²C bus.

> ⚠️ **TODO**: Validate all GPIO assignments against the [Freenove ESP32-S3 WROOM schematic](https://github.com/Freenove/Freenove_ESP32_S3_WROOM_Board) before flashing.

---

## Power Strategy

| Phase | Radio | Estimated current |
|---|---|---|
| Deep sleep | Off | ~20 µA |
| Sensor read + BLE advert (3 s) | BLE only | ~30–80 mA |
| NTP sync + photo upload | WiFi only | ~100–200 mA |
| Camera capture | WiFi + CAM | ~150–250 mA |

WiFi and BLE are **never active simultaneously** — BLE advertising completes before WiFi is brought up for the photo task.

---

## Configuration (`src/config.h`)

All tuneable parameters live in a single header. Key settings:

```cpp
// Wi-Fi credentials
#define WIFI_SSID      "YOUR_SSID"
#define WIFI_PASSWORD  "YOUR_WIFI_PASSWORD"

// NTP timezone (POSIX TZ string)
#define NTP_TIMEZONE   "CET-1CEST,M3.5.0,M10.5.0/3"  // Europe/Paris

// Sensor read intervals (minutes)
#define DS18B20_INTERVAL_MIN   10
#define SHT3X_INTERVAL_MIN     5
#define INA219_INTERVAL_MIN    2
#define LC709203F_INTERVAL_MIN 5

// Daily photo window
#define PHOTO_HOUR    14   // 14:00 local time
#define PHOTO_MINUTE  0
#define PHOTO_WINDOW_MIN 10  // accept up to 14:10

// HTTP upload endpoint
#define UPLOAD_ENDPOINT "http://192.168.1.100:8080/upload"

// Disable optional sensors if not connected
#define INA219_ENABLED    true
#define LC709203F_ENABLED true

// LC709203F battery pack APA value (see datasheet table 1)
#define LC709203F_APA  0x30  // ~3000 mAh

// I²C pins (verify against board)
#define I2C_SDA_PIN  3
#define I2C_SCL_PIN  2
```

---

## Home Assistant / BTHome Integration

The firmware advertises sensor data using the [BTHome v2](https://bthome.io/) protocol over BLE. Home Assistant (≥ 2023.6) with a Bluetooth integration discovers the device automatically as `ESP32-S3-Env`.

**Advertised fields** (depending on connected sensors):

| BTHome Object | Sensor |
|---|---|
| Temperature (0x02) | DS18B20 primary, or SHT3x fallback |
| Humidity (0x03) | SHT3x |
| Battery % (0x01) | LC709203F SoC |
| Voltage (0x0C) | LC709203F cell voltage (or INA219 bus voltage) |
| Current (0x43) | INA219 current (A) |
| Power (0x0D) | INA219 power (W) |

No MQTT broker or custom integration required — BTHome passive scanning works out of the box.

---

## NTP / Time Synchronization Strategy

1. **Cold boot**: RTC magic sentinel is absent → restore last epoch from NVS → connect WiFi → sync NTP.
2. **Warm boot (after deep sleep)**: Estimate current time as `lastEpochS + elapsed_ms_since_set`. No WiFi needed.
3. **Before daily photo**: If last NTP sync is older than `NTP_MAX_AGE_BEFORE_RESYNC_S` (6 h), re-sync while WiFi is already up for the upload.
4. **Scheduler photo window**: Only triggered when `isTrusted()` is true — avoids spurious photos on untrusted clocks.

---

## HTTP Upload Service

The photo task POSTs a `multipart/form-data` request to `UPLOAD_ENDPOINT` with two parts:

| Part name | Content-Type | Content |
|---|---|---|
| `metadata` | `application/json` | `{"device_id":"…","timestamp":…,"lat":…,"lon":…}` |
| `image` | `image/jpeg` | Raw JPEG bytes, filename `photo.jpg` |

A minimal Python receiver example:
```python
from flask import Flask, request
app = Flask(__name__)

@app.route("/upload", methods=["POST"])
def upload():
    img = request.files["image"]
    img.save(f"photos/{request.form['metadata']}.jpg")
    return "", 200

app.run(host="0.0.0.0", port=8080)
```

---

## Known Limitations / TODOs

- **Camera GPIO mapping**: Pin assignments in `src/config.h` are based on community reports. Cross-check with the official Freenove schematic — incorrect pins will cause `esp_camera_init` to fail with `0x105`.
- **Sensor I²C pins**: GPIO 2/3 defaults need verification against the Freenove header labels.
- **HTTPS upload**: The uploader uses plain HTTP. Add `http.setInsecure()` or a CA certificate bundle for HTTPS endpoints.
- **Photo retry logic**: `photoRetryCount` is tracked but the scheduler does not yet implement exponential back-off for upload failures.
- **Multi-sensor BTHome**: Only the first DS18B20 temperature is advertised. Extend `bthome.cpp` with additional object IDs for multi-sensor payloads if needed.
- **GPS coordinates**: `latitude`/`longitude` in upload metadata are hardcoded to 0. Wire up a GPS module or set static coordinates in `main.cpp` if location tagging is needed.

---

## Build Instructions

```bash
# Install PlatformIO CLI (if not already installed)
pip install platformio

# Build firmware
pio run -e freenove_esp32s3

# Flash and open serial monitor
pio run -e freenove_esp32s3 --target upload --target monitor
```

The first build downloads the ESP32 Arduino core and all library dependencies (~5–10 minutes). Subsequent builds are incremental.

> **Board note**: `board = esp32-s3-devkitc-1` is used as the closest stock PlatformIO board definition. The `board_build.arduino.memory_type = qio_opi` flag enables the octal PSRAM required for UXGA JPEG frame buffers.