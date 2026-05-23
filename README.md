# ESP32-S3-Camera-EnvSensors

Custom PlatformIO/Arduino firmware for the **Freenove ESP32-S3 WROOM** with OV2640 camera.

- Reads environmental and power sensors (DS18B20, SHT3x, INA219)
- Publishes readings over **BTHome BLE** — detected automatically by Home Assistant
- Takes a **daily JPEG photo** at a configurable time and uploads it via HTTP POST
- Runs on **battery + solar** thanks to a deep-sleep-first power strategy
- Fully configurable at runtime via a **serial CLI** — no recompile needed for day-to-day changes
- **Remote configuration via MQTT** — publish a retained JSON payload to reconfigure the device on its next daily wake
- Supports **OTA firmware updates** (HTTP manifest or ArduinoOTA maintenance mode)

---

## Table of Contents

1. [Architecture](#architecture)
2. [Project Structure](#project-structure)
3. [Supported Sensors & Wiring](#supported-sensors--wiring)
4. [Configuration System](#configuration-system)
   - [Two-layer design](#two-layer-design)
   - [Compile-time defaults — `src/config.h`](#compile-time-defaults--srcconfigh)
   - [Runtime configuration — Serial CLI](#runtime-configuration--serial-cli)
   - [Runtime configuration — NVS](#runtime-configuration--nvs)
5. [MQTT Config Channel](#mqtt-config-channel)
   - [How it works](#how-it-works)
   - [Topics](#topics)
   - [Payload reference](#payload-reference)
   - [Home Assistant examples](#home-assistant-examples)
6. [Power Strategy](#power-strategy)
7. [BTHome / Home Assistant Integration](#bthome--home-assistant-integration)
8. [NTP & Time Synchronisation](#ntp--time-synchronisation)
9. [HTTP Photo Upload Service](#http-photo-upload-service)
10. [Build & Flash](#build--flash)
11. [CI / CD & Releases](#ci--cd--releases)
12. [OTA Firmware Updates](#ota-firmware-updates)
13. [Known Limitations / TODOs](#known-limitations--todos)

---

## Architecture

```
Boot / Wake
    │
    ├─ device_config::load()           Load runtime config from NVS (or defaults)
    │
    ├─ serial_cli::offerConfigWindow() 3-second window → press a key to open menu
    │
    ├─ ota::isMaintenanceModeRequested()
    │    └─ If yes → ArduinoOTA standby (GPIO trigger or NVS flag)
    │
    ├─ time_manager::init()            Restore epoch from RTC memory / NVS
    │    └─ If untrusted → WiFi → NTP sync → WiFi off
    │
    ├─ scheduler::evaluate()           Which tasks are due this wake?
    │
    ├─ Sensor tasks  (if due)
    │    ├─ DS18B20  — OneWire temperature
    │    ├─ SHT3x    — I²C temperature + humidity
    │    ├─ INA219   — I²C voltage / current / power
    │    └─ bthome::advertise()        Publish all readings over BLE (3 s)
    │
    ├─ Photo task  (if scheduled window reached & not yet taken today)
    │    ├─ WiFi connect
    │    ├─ NTP re-sync if > 6 h since last sync
    │    ├─ ota::checkAndApply()       HTTP OTA check (resets if update found)
    │    ├─ mqtt_config::syncFromBroker() ← receive retained config, apply to NVS
    │    ├─ camera_module::capture()   JPEG from OV2640
    │    ├─ uploader::uploadPhoto()    HTTP POST multipart/form-data
    │    └─ WiFi disconnect
    │
    └─ scheduler::deepSleep()          Sleep until next sensor or photo event
```

**Key principles:**
- WiFi is only alive during NTP sync and photo upload — BLE handles all sensor telemetry.
- OTA check and MQTT config sync piggyback on the photo upload Wi-Fi session — no extra wake.
- RTC memory survives deep sleep; NVS/Preferences survives hard resets.
- The scheduler always picks the **shortest** sleep that covers every upcoming event.

---

## Project Structure

```
├── platformio.ini              Board, framework, libraries, version injection
├── scripts/
│   └── set_version.py          PlatformIO pre-build: injects FIRMWARE_VERSION
└── src/
    ├── config.h                Compile-time constants & defaults (GPIO pins, timeouts…)
    ├── version.h               FIRMWARE_VERSION macro (set by build script)
    ├── device_config.h/.cpp    Runtime DeviceConfig struct — loaded from NVS at boot
    ├── serial_cli.h/.cpp       Interactive config menu available at boot via USB serial
    ├── persistence.h/.cpp      RTC memory accessor + NVS (Preferences) helpers
    ├── time_manager.h/.cpp     NTP sync, epoch estimation, drift tracking
    ├── scheduler.h/.cpp        Wake-decision engine + deep sleep
    ├── main.cpp                Boot sequence, task orchestration
    ├── sensors/
    │   ├── ds18b20.h/.cpp      OneWire DS18B20 driver (chained sensors)
    │   ├── sht3x.h/.cpp        I²C SHT3x driver
    │   └── ina219.h/.cpp       I²C INA219 voltage/current driver
    ├── bthome/
    │   └── bthome.h/.cpp       BTHome v2 BLE advertisement builder
    ├── camera/
    │   └── camera_module.h/.cpp  ESP32 camera init + JPEG capture
    ├── uploader/
    │   └── uploader.h/.cpp     WiFi connect/disconnect + HTTP POST upload
    ├── mqtt_config/
    │   └── mqtt_config.h/.cpp  MQTT config channel (subscribe retained, apply, ACK)
    └── ota/
        └── ota.h/.cpp          HTTP OTA (manifest check + flash) + ArduinoOTA mode
```

---

## Supported Sensors & Wiring

| Sensor | Bus | What it measures |
|---|---|---|
| DS18B20 (1 or more, chained) | OneWire | Temperature (°C) |
| SHT3x (SHT30 / SHT31 / SHT35) | I²C | Temperature (°C) + Humidity (%) |
| INA219 | I²C | Bus voltage (V), current (mA), power (mW) |

### GPIO defaults (verify against your board!)

| Signal | Default GPIO | Note |
|---|---|---|
| DS18B20 data | 41 | 4.7 kΩ pull-up to 3.3 V required; JTAG disabled at boot via gpio_reset_pin() |
| I²C SDA (sensors) | 3 | shared by SHT3x, INA219 |
| I²C SCL (sensors) | 2 | shared by SHT3x, INA219 |
| Camera SCCB SDA | 4 | independent bus |
| Camera SCCB SCL | 5 | independent bus |

> ⚠️ **TODO**: Validate all GPIO numbers against the [Freenove ESP32-S3 WROOM schematic](https://github.com/Freenove/Freenove_ESP32_S3_WROOM_Board) before first flash.

### Wiring summary

- **DS18B20**: Data → GPIO 41 + 4.7 kΩ to 3.3 V. Multiple sensors can share the same wire. GPIO 39–42 are freed from JTAG at boot (gpio_reset_pin()) and usable as normal IO.
- **SHT3x**: SDA/SCL on GPIO 3/2. Default I²C address 0x44 (ADDR pin low); change to 0x45 if needed.
- **INA219**: SDA/SCL on GPIO 3/2. Default address 0x40; up to four units with address pins.
- **Camera**: Uses its own SCCB bus (GPIO 4/5) — does not conflict with the sensor I²C bus.

---

## Configuration System

### Two-layer design

Configuration is split into two layers:

| Layer | File / mechanism | Requires reflash? | Survives reset? |
|---|---|---|---|
| **Compile-time defaults** | `src/config.h` — `#define` constants | Yes | — |
| **Runtime overrides** | NVS (flash storage) — `DeviceConfig` struct | **No** | Yes |

At every boot, `device_config::load()` populates the global `g_deviceConfig` struct:
1. Starts from the compile-time defaults in `config.h`.
2. Applies any values previously saved to NVS (NVS keys override defaults).
3. All modules (`scheduler`, `uploader`, `bthome`, `ota`, `mqtt_config`…) read `g_deviceConfig`.

This means **you only need to edit `config.h` and reflash when you change hardware** (GPIO pins, I²C addresses, bus speeds, camera pins). Everything else is runtime-configurable.

---

### Compile-time defaults — `src/config.h`

These are the values used on first flash and whenever no NVS override exists.
Edit this file for hardware-level changes, then rebuild and flash once.

```cpp
// ── Wi-Fi (initial defaults — can be changed later via serial CLI or MQTT) ──
#define WIFI_SSID       "YOUR_SSID"
#define WIFI_PASSWORD   "YOUR_WIFI_PASSWORD"

// ── NTP ─────────────────────────────────────────────────────────────────────
#define NTP_SERVER_1    "pool.ntp.org"
#define NTP_SERVER_2    "time.google.com"
#define NTP_TIMEZONE    "CET-1CEST,M3.5.0,M10.5.0/3"  // POSIX TZ – Europe/Paris

// ── Sensor intervals (minutes) ───────────────────────────────────────────────
#define DS18B20_INTERVAL_MIN   10
#define SHT3X_INTERVAL_MIN      5
#define INA219_INTERVAL_MIN     2
#define INA219_ENABLED       true   // set false if sensor not connected

// ── Daily photo ──────────────────────────────────────────────────────────────
#define PHOTO_HOUR         14    // 14:00 local time
#define PHOTO_MINUTE        0
#define PHOTO_WINDOW_MIN   10    // accept trigger up to 14:10

// ── HTTP upload ──────────────────────────────────────────────────────────────
#define UPLOAD_ENDPOINT  "http://192.168.1.100:8080/upload"

// ── BTHome / BLE ─────────────────────────────────────────────────────────────
#define BTHOME_DEVICE_NAME  "ESP32-S3-Env"   // visible in Home Assistant

// ── OTA ──────────────────────────────────────────────────────────────────────
#define OTA_ENABLED       true
#define OTA_MANIFEST_URL  "http://192.168.1.100:8080/firmware/manifest.json"
#define OTA_DEVICE_PASSWORD  "esp32ota"     // ArduinoOTA push password
#define OTA_MAINTENANCE_GPIO  0             // hold BOOT button = maintenance mode

// ── Hardware pins (require reflash to change) ────────────────────────────────
#define DS18B20_PIN    14
#define I2C_SDA_PIN     3
#define I2C_SCL_PIN     2
#define SHT3X_I2C_ADDR  0x44
#define INA219_I2C_ADDR 0x40
// Camera pins: see bottom of config.h
```

---

### Runtime configuration — Serial CLI

The easiest way to reconfigure the device without reflashing, including setting up MQTT.

#### How to open the menu

1. Connect the ESP32-S3 to a computer via USB.
2. Open a serial terminal at **115200 baud** (PlatformIO monitor, minicom, PuTTY…).
3. **Power-cycle or reset** the device.
4. Within **3 seconds** of the boot message, press **any key**.

The menu appears:

```
╔══════════════════════════════════════════════════════╗
║          ESP32-S3  –  Configuration                 ║
╠══════════════════════════════════════════════════════╣
║  [1]  Capteurs (présence & intervalles)             ║
║  [2]  Planification photo                           ║
║  [3]  Réseau (Wi-Fi / endpoint)                     ║
║  [4]  OTA                                           ║
║  [5]  MQTT (config à distance)                      ║
║  [6]  Nom BLE                                       ║
║  [P]  Afficher la configuration actuelle            ║
║  [S]  Sauvegarder et reprendre le boot              ║
║  [R]  Réinitialiser aux valeurs par défaut          ║
║  [X]  Reprendre sans sauvegarder                   ║
╚══════════════════════════════════════════════════════╝
```

#### What you can configure at runtime

**[1] Sensors — presence & intervals** — enable/disable each sensor, set polling interval (1–240 min).

**[2] Daily photo schedule** — hour (0–23), minute (0–59), acceptance window (min).

**[3] Network** — Wi-Fi SSID, password (hidden input), HTTP upload endpoint.

**[4] OTA** — enable/disable, manifest URL.

**[5] MQTT** — enable/disable, broker hostname/IP, port, username, password (hidden), client ID.

**[6] BLE device name** — the name shown in Home Assistant.

**[7] Boot window** — duration (seconds) of the serial CLI + web config server at each boot/wake.

**[8] NTP & Timezone** — NTP server 1, NTP server 2, POSIX TZ string (e.g. `CET-1CEST,M3.5.0,M10.5.0/3`).

#### Menu actions

| Key | Action |
|---|---|
| `P` | Print current configuration |
| `S` | **Save** to NVS and resume boot |
| `R` | Reset all fields to compile-time defaults (needs `S` to persist) |
| `X` | Resume boot without saving |

> **Tip**: If you miss the 3-second window, simply reset the board again.

#### Factory reset

1. Open the serial CLI → press `R` → press `S`.

Or erase the entire NVS partition:

```bash
pio run -t erase        # erases full flash including NVS
```

---

### Runtime configuration — NVS

`g_deviceConfig` is backed by NVS namespace `dev_cfg`, separate from the operational state (`env_fw`).

**NVS keys used by `DeviceConfig`:**

| Key | Type | Field |
|---|---|---|
| `ds18_en` | bool | DS18B20 enabled |
| `ds18_int` | uint8 | DS18B20 interval (min) |
| `sht_en` | bool | SHT3x enabled |
| `sht_int` | uint8 | SHT3x interval (min) |
| `ina_en` | bool | INA219 enabled |
| `ina_int` | uint8 | INA219 interval (min) |
| `ph_hour` | uint8 | Photo hour |
| `ph_min` | uint8 | Photo minute |
| `ph_win` | uint8 | Photo window (min) |
| `wifi_ssid` | string | Wi-Fi SSID |
| `wifi_pass` | string | Wi-Fi password |
| `upload_ep` | string | Upload endpoint URL |
| `ota_en` | bool | OTA enabled |
| `ota_url` | string | OTA manifest URL |
| `mqtt_en` | bool | MQTT enabled |
| `mqtt_host` | string | MQTT broker hostname/IP |
| `mqtt_port` | uint16 | MQTT broker port |
| `mqtt_user` | string | MQTT username |
| `mqtt_pass` | string | MQTT password |
| `mqtt_id` | string | MQTT client ID |
| `dev_name` | string | BLE device name |
| `boot_win` | uint8 | Boot window (s) |
| `ntp_srv1` | string | NTP server 1 |
| `ntp_srv2` | string | NTP server 2 |
| `ntp_tz` | string | POSIX timezone string |

Missing keys fall back to `config.h` defaults — adding new keys in a future firmware update is always safe.

---

## MQTT Config Channel

### How it works

The device does not maintain a persistent MQTT connection (that would drain the battery).
Instead, it **subscribes once per day** during the Wi-Fi session that already runs for the photo upload.

```
Daily Wi-Fi session
    ├─ NTP sync (if needed)
    ├─ OTA check
    ├─ MQTT connect → subscribe <clientId>/config/set (retained)
    │      │
    │      ├─ Retained message present → apply overrides → save NVS → publish ACK
    │      └─ No retained message      → nothing to do   → publish status
    ├─ MQTT disconnect
    ├─ Camera capture
    └─ HTTP upload
```

The **retained** flag on the MQTT topic is essential: the broker stores the last config payload and delivers it immediately when the device subscribes, even if it was published days ago. This means you can push a config change from Home Assistant at any time and it will be applied on the next daily wake, without any synchronisation overhead.

### Topics

All topics are prefixed with the **MQTT client ID** (default: `ESP32-S3-Env`, configurable).

| Topic | Direction | Retained | Description |
|---|---|---|---|
| `<clientId>/config/set` | HA → ESP32 | **Yes** | Config payload to apply |
| `<clientId>/config/status` | ESP32 → HA | No | ACK after applying config |

### Payload reference

Publish a JSON object to `<clientId>/config/set` with **any subset** of the following keys.
Only the keys present in the payload are applied — omitted keys are left unchanged.

```json
{
  "ds18_en":   true,
  "ds18_int":  10,
  "sht_en":    true,
  "sht_int":   5,
  "ina_en":    true,
  "ina_int":   2,
  "ph_hour":   14,
  "ph_min":    0,
  "ph_win":    10,
  "upload_ep": "http://192.168.1.100:8080/upload",
  "ota_en":    true,
  "ota_url":   "http://192.168.1.100:8080/firmware/manifest.json",
  "mqtt_en":   true,
  "mqtt_host": "192.168.1.10",
  "mqtt_port": 1883,
  "mqtt_user": "esp32",
  "mqtt_pass": "secret",
  "mqtt_id":   "ESP32-S3-Env",
  "dev_name":  "ESP32-S3-Env",
  "wifi_ssid": "MyNetwork",
  "wifi_pass": "MyPassword"
}
```

> **Tip**: You can change only one field at a time — e.g. just `{"ph_hour": 8}` to move the photo to 08:00.

The device publishes a non-retained ACK to `<clientId>/config/status`:

```json
{
  "fw": "v1.2.3",
  "updated": true,
  "ds18_en": true,
  "sht_en": true,
  "ina_en": true,
  "ph_hour": 8,
  "ph_min": 0
}
```

### Home Assistant examples

#### MQTT service call — change photo time to 08:30

```yaml
service: mqtt.publish
data:
  topic: "ESP32-S3-Env/config/set"
  payload: '{"ph_hour": 8, "ph_min": 30}'
  retain: true
```

#### Button card to disable INA219

```yaml
type: button
name: Disable INA219
tap_action:
  action: call-service
  service: mqtt.publish
  service_data:
    topic: "ESP32-S3-Env/config/set"
    payload: '{"ina_en": false}'
    retain: true
```

#### Automation — move photo time in winter

```yaml
automation:
  trigger:
    - platform: sun
      event: sunset
      offset: "-01:00:00"
  action:
    - service: mqtt.publish
      data:
        topic: "ESP32-S3-Env/config/set"
        payload: '{"ph_hour": 15, "ph_min": 0}'
        retain: true
```

#### Watch for config ACK

```yaml
sensor:
  - platform: mqtt
    name: "ESP32-S3-Env Config Status"
    state_topic: "ESP32-S3-Env/config/status"
    value_template: "{{ value_json.fw }}"
    json_attributes_topic: "ESP32-S3-Env/config/status"
```

#### Clear config (stop sending updates)

To stop applying config changes, publish an empty retained message to clear the topic:

```yaml
service: mqtt.publish
data:
  topic: "ESP32-S3-Env/config/set"
  payload: ""
  retain: true
```

An empty payload is silently ignored by the device.

---

## Power Strategy

| Wake phase | Radio state | Typical current |
|---|---|---|
| Deep sleep | All off | ~20 µA |
| Sensor read + BLE advert (3 s) | BLE only | ~30–80 mA |
| NTP sync | Wi-Fi only | ~80–150 mA |
| MQTT config sync (< 5 s) | Wi-Fi only | ~80–150 mA |
| Photo upload + OTA check | Wi-Fi only | ~100–200 mA |
| Camera capture | Wi-Fi + CAM | ~150–250 mA |

**Rules:**
- BLE and Wi-Fi are **never active simultaneously**.
- MQTT sync, OTA check, NTP, and photo upload all share the **same single daily Wi-Fi session** — no extra connection is needed for any of them.
- The serial CLI window (3 s) adds a small constant overhead per boot.

**Rough energy estimate** (7–10 Wh battery, one photo per day, sensors every 5–10 min):
- ~96 sensor wakes/day × 3 s × ~50 mA avg = ~4 mAh
- ~1 photo+MQTT+OTA wake/day × 35 s × ~200 mA avg = ~2 mAh
- ~1440 min sleep × ~0.02 mA = ~0.5 mAh
- **Total ≈ 6–7 mAh/day** — comfortable for a 2000 mAh pack with a small solar panel.

---

## BTHome / Home Assistant Integration

The firmware uses [BTHome v2](https://bthome.io/) — a standard BLE advertisement format natively supported by Home Assistant since version 2023.6.

**No MQTT, no ESPHome, no custom integration required.**

Home Assistant with Bluetooth discovers the device automatically and creates entities.

### BTHome payload structure

The BLE advertisement carries typed measurement objects in ascending object-ID order.
Each object type corresponds to one entity in Home Assistant:

| BTHome object ID | HA entity name | Unit | Source |
|---|---|---|---|
| `0x02` (×N) | Temperature *(then Temperature 2, 3…)* | °C | one entry **per DS18B20**, in stored ROM order; **SHT3x temperature appended last** |
| `0x03` | Humidity | % | SHT3x |
| `0x0B` | Power | W | INA219 |
| `0x0C` | Voltage | V | INA219 bus voltage |
| `0x43` | Current | A | INA219 |

> **Note**: BTHome v2 does not carry sensor names in the packet. HA auto-numbers duplicate types
> (`Temperature`, `Temperature 2`, …). The firmware guarantees a **stable emission order** so the
> mapping never changes after the first discovery (see below).

### Multiple DS18B20 sensors — stable ordering

The order in which DS18B20 temperatures appear in the BTHome packet is determined by their
8-byte **1-Wire ROM address** and is locked in NVS the first time you run the discovery command:

```
Boot serial CLI → [D] Diagnostics → [I] Découverte DS18B20
```

- On first run (empty NVS) the sensors are enumerated in **ascending ROM address order** — this is
  the order imposed by the 1-Wire search algorithm.
- The ROM addresses are then **saved to NVS** (`ds_n`, `ds_0` … `ds_N`) so the order persists
  across reboots, deep sleeps, and even bus re-enumeration if you add a new sensor.
- If a new sensor is detected (count mismatch), the firmware falls back to live discovery order
  and prints a warning — run `[I]` again to lock the new order.

**Result for N DS18B20 + SHT3x enabled:**

| BTHome slot | HA entity | ROM address (example) |
|---|---|---|
| Temperature | DS18B20 #1 | `28:FF:12:34:56:78:90:AB` |
| Temperature 2 | DS18B20 #2 | `28:FF:AA:BB:CC:DD:EE:FF` |
| … | … | … |
| Temperature N+1 | SHT3x | — |
| Humidity | SHT3x | — |

### Renaming entities in Home Assistant

BTHome does not transmit sensor names. The recommended approach is to **rename the entities once
in the HA UI** — the names survive firmware updates because they are tied to the BTHome device
MAC address, not to entity IDs that could change.

1. **Settings → Devices & Services → [your device]**
2. Click any entity (e.g. "Temperature 2") → ✏️ pencil icon
3. Set a friendly name (e.g. "SHT3x Temperature") → **Update**

Suggested naming when SHT3x and one DS18B20 are both enabled:

| HA entity (auto) | Suggested rename | Rationale |
|---|---|---|
| Temperature | DS18B20 Temperature | always slot 0 |
| Temperature 2 | SHT3x Temperature | always last temperature slot |
| Humidity | SHT3x Humidity | only humidity source |
| Voltage | INA219 Voltage | only voltage source |
| Current | INA219 Current | only current source |
| Power | INA219 Power | only power source |

### Setup in Home Assistant

1. Go to **Settings → Devices & Services → Add Integration → Bluetooth**.
2. Make sure a Bluetooth adapter is available (built-in, USB dongle, or ESPHome BLE proxy).
3. The device appears as `ESP32-S3-Env` (or whatever `deviceName` is set to in `[6] Nom BLE`).
4. Accept — all sensor entities are created automatically.

### BLE payload size limit

Each BLE advertisement packet is limited to **31 bytes**.
The main packet contains flags (3 B) + service data header (4 B) + one byte per object ID + values.
Current usage with all sensors enabled (1 DS18B20 + SHT3x + INA219):

| Object | Bytes |
|---|---|
| Device info byte | 1 |
| Temperature ×1 (DS18B20) | 3 |
| Temperature ×1 (SHT3x) | 3 |
| Humidity | 3 |
| Power | 4 |
| Voltage | 3 |
| Current | 3 |
| **Subtotal service data** | **20** |
| Flags AD element | 3 |
| Service UUID + length | 4 |
| **Total** | **27 / 31 bytes** |

With 4 bytes remaining, up to **1 additional DS18B20** (3 B) fits within the limit.
The device name is moved to the scan-response packet to keep the main packet within budget.

### BLE proxy (optional, recommended)

If the ESP32 is not in direct Bluetooth range of the HA host, add a cheap ESP32 running the [ESPHome BLE proxy](https://esphome.io/components/bluetooth_proxy.html) firmware nearby.

---

## NTP & Time Synchronisation

| Situation | Action |
|---|---|
| Cold boot (no RTC sentinel) | Connect Wi-Fi → NTP sync → disconnect |
| Warm boot (deep sleep wakeup) | Estimate time: `lastEpoch + elapsed_ms`. No Wi-Fi needed. |
| Before photo task | If last NTP sync > 6 h ago, re-sync while Wi-Fi is already up |
| Time not trusted | Photo task is skipped — avoids spurious photos |

**Timezone & NTP servers**: now runtime-configurable — no recompile needed.
Change via serial CLI `[8]`, the web UI (NTP & Fuseau horaire section), or keep `config.h` defaults (`NTP_SERVER_1`, `NTP_SERVER_2`, `NTP_TIMEZONE`).
Examples: `"CET-1CEST,M3.5.0,M10.5.0/3"` (Paris), `"UTC0"`, `"EST5EDT,M3.2.0,M11.1.0"` (New York).

---

## HTTP Photo Upload Service

The photo task sends a `multipart/form-data` POST to `g_deviceConfig.uploadEndpoint`:

| Part name | Content-Type | Content |
|---|---|---|
| `metadata` | `application/json` | `{"device_id":"…","timestamp":1234567890,"lat":0.0,"lon":0.0}` |
| `image` | `image/jpeg` | Raw JPEG bytes (filename: `photo.jpg`) |

### Minimal Python/Flask receiver

```python
from flask import Flask, request
import os, json, time

app = Flask(__name__)
PHOTO_DIR = "photos"
os.makedirs(PHOTO_DIR, exist_ok=True)

@app.route("/upload", methods=["POST"])
def upload():
    meta = json.loads(request.form.get("metadata", "{}"))
    img  = request.files.get("image")
    if img:
        ts   = meta.get("timestamp", int(time.time()))
        name = f"{meta.get('device_id', 'esp32')}_{ts}.jpg"
        img.save(os.path.join(PHOTO_DIR, name))
        print(f"Saved {name}")
    return "", 200

app.run(host="0.0.0.0", port=8080)
```

### Docker Compose example

```yaml
services:
  photo-receiver:
    image: python:3.11-slim
    working_dir: /app
    volumes:
      - ./receiver:/app
      - ./photos:/app/photos
    command: >
      sh -c "pip install flask -q && python receiver.py"
    ports:
      - "8080:8080"
    restart: unless-stopped
```

---

## Build & Flash

### First flash (USB cable required)

```bash
# 1. Clone the repo
git clone https://github.com/<your-username>/ESP32-S3-Camera-EnvSensors.git
cd ESP32-S3-Camera-EnvSensors

# 2. (Optional) edit src/config.h with your Wi-Fi SSID/password and endpoint.
#    Everything else can be changed later via serial CLI or MQTT.

# 3. Build + flash + open monitor
pio run -e freenove_esp32s3 --target upload --target monitor
```

### Configure via serial CLI after first flash

On first boot the device will use `config.h` defaults. To configure MQTT (and anything else) without reflashing:

1. Open serial monitor at 115200 baud.
2. Reset the device → press any key within 3 seconds.
3. Navigate to `[5] MQTT` and enter broker/port/credentials.
4. Press `S` to save → the device resumes boot with MQTT enabled.

From the next daily photo wake onward, the device will sync config from the broker.

### Build options

```bash
pio run -e freenove_esp32s3                              # build only
pio run -e freenove_esp32s3 --target upload             # build + flash
pio device monitor --baud 115200                        # serial monitor
pio run -t upload --upload-port <device-IP>             # OTA push (maintenance mode)
```

> **Board note**: `esp32-s3-devkitc-1` is the closest stock board definition.
> `board_build.arduino.memory_type = qio_opi` enables the octal PSRAM needed for UXGA JPEG buffers.
> `board_build.partitions = default.csv` provides two OTA slots.

---

## CI / CD & Releases

### Continuous Integration

Every push and pull request triggers **CI – Build Firmware** (`.github/workflows/ci.yml`):

- Builds with PlatformIO on `ubuntu-latest`
- Embeds `FIRMWARE_VERSION = ci-<8-char-sha>`
- Uploads `firmware.bin` + `firmware.elf` as artifacts (7-day retention)

### Creating a Release

```bash
git tag v1.2.3
git push origin v1.2.3
```

The **Release** workflow (`.github/workflows/release.yml`) will:

1. Build with `FIRMWARE_VERSION = v1.2.3` baked in
2. Generate `manifest.json` pointing at the binary download URL
3. Create a GitHub Release with `firmware-v1.2.3.bin`, `firmware-v1.2.3.elf`, `manifest.json`

Tags containing `-alpha`, `-beta`, or `-rc` are automatically marked as pre-releases.

---

## OTA Firmware Updates

### 1 — HTTP OTA (automatic, during photo session)

During the daily Wi-Fi session, the device fetches `g_deviceConfig.otaManifestUrl` and compares the remote version to `FIRMWARE_VERSION`. If newer, it downloads and flashes, then reboots.

**Self-hosted manifest** (plain HTTP — recommended):

```json
{
  "version": "v1.2.3",
  "url": "http://192.168.1.100:8080/firmware/firmware.bin",
  "notes": "Optional description"
}
```

Configure the manifest URL via MQTT (`{"ota_url": "..."}`) or the serial CLI `[4] OTA`.

### 2 — ArduinoOTA (manual, maintenance mode)

**Trigger maintenance mode:**

| Method | How |
|---|---|
| GPIO | Hold the **BOOT button** (GPIO 0) during power-on |
| NVS flag | Call `ota::requestMaintenanceMode(true)` then reset |
| MQTT | (future) publish `{"ota_maintenance": true}` — not yet wired |

Once in maintenance mode the device connects to Wi-Fi and waits up to 2 minutes for a push:

```bash
pio run -t upload --upload-port 192.168.1.42
```

Password: `OTA_DEVICE_PASSWORD` from `config.h` (default: `esp32ota`).

---

## Known Limitations / TODOs

| # | Issue | Status |
|---|---|---|
| 1 | **Camera GPIO mapping** not validated against Freenove schematic — wrong pins cause `esp_camera_init` error `0x105` | ⚠️ TODO |
| 2 | **Sensor I²C pins** (GPIO 2/3) not verified against Freenove header labels | ⚠️ TODO |
| 3 | **HTTPS upload** — `HTTPClient` does not support TLS; use plain `http://` for now | 🔜 Enhancement |
| 4 | ~~**HTTPS OTA** — same TLS limitation; use self-hosted HTTP manifest~~ — `WiFiClientSecure` now used automatically when URL starts with `https://` | ✅ Done |
| 5 | **MQTT over TLS** — `PubSubClient` supports `WiFiClientSecure`; not wired yet | 🔜 Enhancement |
| 6 | **Photo retry back-off** — `photoRetryCount` tracked but no exponential delay implemented | 🔜 Enhancement |
| 7 | ~~**Multi-DS18B20 BTHome** — only the first sensor's temperature is advertised~~ — all sensors emitted in stable NVS address order | ✅ Done |
| 8 | **GPS coordinates** — hardcoded to 0 in upload metadata | 🔜 Enhancement |
| 9 | **OTA partition size** — `default.csv` limits the app to ~1.3 MB; switch to `default_8MB.csv` for 8 MB flash boards if firmware grows | ℹ️ Note |
| 10 | **Serial CLI password** is not echoed but transmitted in plain text over USB | ℹ️ Note |


---

## Table of Contents

1. [Architecture](#architecture)
2. [Project Structure](#project-structure)
3. [Supported Sensors & Wiring](#supported-sensors--wiring)
4. [Configuration System](#configuration-system)
   - [Two-layer design](#two-layer-design)
   - [Compile-time defaults — `src/config.h`](#compile-time-defaults--srcconfigh)
   - [Runtime configuration — Serial CLI](#runtime-configuration--serial-cli)
   - [Runtime configuration — NVS](#runtime-configuration--nvs)
5. [Power Strategy](#power-strategy)
6. [BTHome / Home Assistant Integration](#bthome--home-assistant-integration)
7. [NTP & Time Synchronisation](#ntp--time-synchronisation)
8. [HTTP Photo Upload Service](#http-photo-upload-service)
9. [Build & Flash](#build--flash)
10. [CI / CD & Releases](#ci--cd--releases)
11. [OTA Firmware Updates](#ota-firmware-updates)
12. [Known Limitations / TODOs](#known-limitations--todos)

---

## Architecture

```
Boot / Wake
    │
    ├─ device_config::load()          Load runtime config from NVS (or defaults)
    │
    ├─ serial_cli::offerConfigWindow() 3-second window → press a key to open menu
    │
    ├─ ota::isMaintenanceModeRequested()
    │    └─ If yes → ArduinoOTA standby (GPIO trigger or NVS flag)
    │
    ├─ time_manager::init()           Restore epoch from RTC memory / NVS
    │    └─ If untrusted → WiFi → NTP sync → WiFi off
    │
    ├─ scheduler::evaluate()          Which tasks are due this wake?
    │
    ├─ Sensor tasks  (if due)
    │    ├─ DS18B20  — OneWire temperature
    │    ├─ SHT3x    — I²C temperature + humidity
    │    ├─ INA219   — I²C voltage / current / power
    │    └─ bthome::advertise()       Publish all readings over BLE (3 s)
    │
    ├─ Photo task  (if scheduled window reached & not yet taken today)
    │    ├─ WiFi connect
    │    ├─ NTP re-sync if > 6 h since last sync
    │    ├─ ota::checkAndApply()      HTTP OTA check (resets if update found)
    │    ├─ camera_module::capture()  JPEG from OV2640
    │    ├─ uploader::uploadPhoto()   HTTP POST multipart/form-data
    │    └─ WiFi disconnect
    │
    └─ scheduler::deepSleep()         Sleep until next sensor or photo event
```

**Key principles:**
- WiFi is only alive during NTP sync and photo upload — BLE handles all sensor telemetry.
- OTA check piggybacks on the photo upload Wi-Fi session — no extra wake needed.
- RTC memory survives deep sleep; NVS/Preferences survives hard resets.
- The scheduler always picks the **shortest** sleep that covers every upcoming event.

---

## Project Structure

```
├── platformio.ini              Board, framework, libraries, version injection
├── scripts/
│   └── set_version.py          PlatformIO pre-build: injects FIRMWARE_VERSION
└── src/
    ├── config.h                Compile-time constants & defaults (GPIO pins, timeouts…)
    ├── version.h               FIRMWARE_VERSION macro (set by build script)
    ├── device_config.h/.cpp    Runtime DeviceConfig struct — loaded from NVS at boot
    ├── serial_cli.h/.cpp       Interactive config menu available at boot via USB serial
    ├── persistence.h/.cpp      RTC memory accessor + NVS (Preferences) helpers
    ├── time_manager.h/.cpp     NTP sync, epoch estimation, drift tracking
    ├── scheduler.h/.cpp        Wake-decision engine + deep sleep
    ├── main.cpp                Boot sequence, task orchestration
    ├── sensors/
    │   ├── ds18b20.h/.cpp      OneWire DS18B20 driver (chained sensors)
    │   ├── sht3x.h/.cpp        I²C SHT3x driver
    │   └── ina219.h/.cpp       I²C INA219 voltage/current driver
    ├── bthome/
    │   └── bthome.h/.cpp       BTHome v2 BLE advertisement builder
    ├── camera/
    │   └── camera_module.h/.cpp  ESP32 camera init + JPEG capture
    ├── uploader/
    │   └── uploader.h/.cpp     WiFi connect/disconnect + HTTP POST upload
    └── ota/
        └── ota.h/.cpp          HTTP OTA (manifest check + flash) + ArduinoOTA mode
```

---

## Supported Sensors & Wiring

| Sensor | Bus | What it measures |
|---|---|---|
| DS18B20 (1 or more, chained) | OneWire | Temperature (°C) |
| SHT3x (SHT30 / SHT31 / SHT35) | I²C | Temperature (°C) + Humidity (%) |
| INA219 | I²C | Bus voltage (V), current (mA), power (mW) |

### GPIO defaults (verify against your board!)

| Signal | Default GPIO | Note |
|---|---|---|
| DS18B20 data | 41 | 4.7 kΩ pull-up to 3.3 V required; JTAG disabled at boot via gpio_reset_pin() |
| I²C SDA (sensors) | 3 | shared by SHT3x, INA219 |
| I²C SCL (sensors) | 2 | shared by SHT3x, INA219 |
| Camera SCCB SDA | 4 | independent bus |
| Camera SCCB SCL | 5 | independent bus |

> ⚠️ **TODO**: Validate all GPIO numbers against the [Freenove ESP32-S3 WROOM schematic](https://github.com/Freenove/Freenove_ESP32_S3_WROOM_Board) before first flash.

### Wiring summary

- **DS18B20**: Data → GPIO 41 + 4.7 kΩ to 3.3 V. Multiple sensors can share the same wire. GPIO 39–42 are freed from JTAG at boot (gpio_reset_pin()) and usable as normal IO.
- **SHT3x**: SDA/SCL on GPIO 3/2. Default I²C address 0x44 (ADDR pin low); change to 0x45 if needed.
- **INA219**: SDA/SCL on GPIO 3/2. Default address 0x40; up to four units with address pins.
- **Camera**: Uses its own SCCB bus (GPIO 4/5) — does not conflict with the sensor I²C bus.

---

## Configuration System

### Two-layer design

Configuration is split into two layers:

| Layer | File / mechanism | Requires reflash? | Survives reset? |
|---|---|---|---|
| **Compile-time defaults** | `src/config.h` — `#define` constants | Yes | — |
| **Runtime overrides** | NVS (flash storage) — `DeviceConfig` struct | **No** | Yes |

At every boot, `device_config::load()` populates the global `g_deviceConfig` struct:
1. Starts from the compile-time defaults in `config.h`.
2. Applies any values previously saved to NVS (NVS keys override defaults).
3. All modules (`scheduler`, `uploader`, `bthome`, `ota`…) read `g_deviceConfig` — never the `#define` constants directly.

This means **you only need to edit `config.h` and reflash when you change hardware** (GPIO pins, I²C addresses, bus speeds, camera pins). Everything else is runtime-configurable.

---

### Compile-time defaults — `src/config.h`

These are the values used on first flash and whenever no NVS override exists.
Edit this file for hardware-level changes, then rebuild and flash once.

```cpp
// ── Wi-Fi (initial defaults — can be changed later via serial CLI) ──────────
#define WIFI_SSID       "YOUR_SSID"
#define WIFI_PASSWORD   "YOUR_WIFI_PASSWORD"

// ── NTP ─────────────────────────────────────────────────────────────────────
#define NTP_SERVER_1    "pool.ntp.org"
#define NTP_SERVER_2    "time.google.com"
#define NTP_TIMEZONE    "CET-1CEST,M3.5.0,M10.5.0/3"  // POSIX TZ – Europe/Paris

// ── Sensor intervals (minutes) ───────────────────────────────────────────────
#define DS18B20_INTERVAL_MIN   10
#define SHT3X_INTERVAL_MIN      5
#define INA219_INTERVAL_MIN     2
#define INA219_ENABLED       true   // set false if sensor not connected

// ── Daily photo ──────────────────────────────────────────────────────────────
#define PHOTO_HOUR         14    // 14:00 local time
#define PHOTO_MINUTE        0
#define PHOTO_WINDOW_MIN   10    // accept trigger up to 14:10

// ── HTTP upload ──────────────────────────────────────────────────────────────
#define UPLOAD_ENDPOINT  "http://192.168.1.100:8080/upload"

// ── BTHome / BLE ─────────────────────────────────────────────────────────────
#define BTHOME_DEVICE_NAME  "ESP32-S3-Env"   // visible in Home Assistant

// ── OTA ──────────────────────────────────────────────────────────────────────
#define OTA_ENABLED       true
#define OTA_MANIFEST_URL  "http://192.168.1.100:8080/firmware/manifest.json"
#define OTA_DEVICE_PASSWORD  "esp32ota"     // ArduinoOTA push password
#define OTA_MAINTENANCE_GPIO  0             // hold BOOT button = maintenance mode

// ── Hardware pins (require reflash to change) ────────────────────────────────
#define DS18B20_PIN    14
#define I2C_SDA_PIN     3
#define I2C_SCL_PIN     2
#define SHT3X_I2C_ADDR  0x44
#define INA219_I2C_ADDR 0x40
// Camera pins: see bottom of config.h
```

---

### Runtime configuration — Serial CLI

The easiest way to reconfigure the device without reflashing.

#### How to open the menu

1. Connect the ESP32-S3 to a computer via USB.
2. Open a serial terminal at **115200 baud** (PlatformIO monitor, minicom, PuTTY…).
3. **Power-cycle or reset** the device.
4. Within **3 seconds** of the boot message, press **any key**.

The menu appears immediately:

```
╔══════════════════════════════════════════════════════╗
║          ESP32-S3  –  Configuration                 ║
╠══════════════════════════════════════════════════════╣
║  [1]  Capteurs (présence & intervalles)             ║
║  [2]  Planification photo                           ║
║  [3]  Réseau (Wi-Fi / endpoint)                     ║
║  [4]  OTA                                           ║
║  [5]  Nom BLE                                       ║
║  [P]  Afficher la configuration actuelle            ║
║  [S]  Sauvegarder et reprendre le boot              ║
║  [R]  Réinitialiser aux valeurs par défaut          ║
║  [X]  Reprendre sans sauvegarder                   ║
╚══════════════════════════════════════════════════════╝
Choix:
```

#### What you can configure at runtime

**[1] Sensors — presence & intervals**

Enable or disable each sensor individually, and set its polling interval (1–240 min).
Disabled sensors are completely skipped by the scheduler — they consume nothing.

```
DS18B20 activé [y] (y/n, Enter=keep): n        ← disable if not wired
SHT3x   activé [y] (y/n, Enter=keep): y
  Intervalle SHT3x (min) [5] (1-240, Enter=keep): 10
INA219  activé [y] (y/n, Enter=keep): y
  Intervalle INA219 (min) [2] (1-240, Enter=keep): 5
```

**[2] Daily photo schedule**

```
Heure (0-23)               [14] : 8    ← change to 08:00
Minute (0-59)               [0] : 30   ← 08:30
Fenêtre d'acceptation (min) [10]: 15   ← accept up to 08:45
```

**[3] Network**

```
SSID Wi-Fi          [MyNetwork]     : NewNetwork
Mot de passe Wi-Fi  [***]           : (hidden input)
Endpoint upload     [http://...]    : http://192.168.1.200:8080/upload
```

**[4] OTA**

```
OTA activé      [y] : y
URL manifest OTA [http://...] : http://192.168.1.100:8080/firmware/manifest.json
```

**[5] MQTT** — enable/disable, broker hostname/IP, port, username, password (hidden), client ID.

**[6] BLE device name**

```
Nom BLE de l'appareil [ESP32-S3-Env] : Jardin-ESP32
```

**[7] Boot window** — duration of the serial CLI + web config server at each boot (0–180 s).

**[8] NTP & Timezone**

```
Serveur NTP 1       [172.22.7.1]                          : pool.ntp.org
Serveur NTP 2       [time.google.com]                     : (Enter=keep)
Fuseau horaire (TZ) [CET-1CEST,M3.5.0,M10.5.0/3]         : America/New_York
```

> POSIX TZ examples: `CET-1CEST,M3.5.0,M10.5.0/3` (Paris), `UTC0`, `EST5EDT,M3.2.0,M11.1.0` (New York).

#### Menu actions

| Key | Action |
|---|---|
| `P` | Print current configuration (including unsaved changes) |
| `S` | **Save** to NVS and resume boot |
| `R` | Reset all fields to compile-time defaults (still needs `S` to persist) |
| `X` | Resume boot without saving — changes are discarded |

> **Tip**: If you miss the 3-second window, simply reset the board again.
> The 3-second timer only runs once per physical reset.

#### Factory reset

To wipe all NVS overrides and return to `config.h` defaults permanently:

1. Open the serial CLI (reset + press key within 3 s).
2. Press `R` → all fields reset to defaults in memory.
3. Press `S` → defaults are saved to NVS (overwriting previous overrides).

Alternatively, erase the entire NVS partition from the command line:

```bash
pio run -t erase        # erases full flash including NVS
# — or —
esptool.py erase_flash  # same effect
```

After erasing, the device will use `config.h` defaults on next boot.

---

### Runtime configuration — NVS

`g_deviceConfig` is backed by a dedicated NVS namespace (`dev_cfg`), separate from the firmware's operational state (`env_fw`). The two namespaces never conflict.

**NVS keys used by `DeviceConfig`:**

| Key | Type | Field |
|---|---|---|
| `ds18_en` | bool | DS18B20 enabled |
| `ds18_int` | uint8 | DS18B20 interval (min) |
| `sht_en` | bool | SHT3x enabled |
| `sht_int` | uint8 | SHT3x interval (min) |
| `ina_en` | bool | INA219 enabled |
| `ina_int` | uint8 | INA219 interval (min) |
| `ph_hour` | uint8 | Photo hour |
| `ph_min` | uint8 | Photo minute |
| `ph_win` | uint8 | Photo window (min) |
| `wifi_ssid` | string | Wi-Fi SSID |
| `wifi_pass` | string | Wi-Fi password |
| `upload_ep` | string | Upload endpoint URL |
| `ota_en` | bool | OTA enabled |
| `ota_url` | string | OTA manifest URL |
| `dev_name` | string | BLE device name |
| `boot_win` | uint8 | Boot window (s) |
| `ntp_srv1` | string | NTP server 1 |
| `ntp_srv2` | string | NTP server 2 |
| `ntp_tz` | string | POSIX timezone string |

Missing keys are silently ignored — the default from `resetToDefaults()` (itself sourced from `config.h`) is used instead. This means adding a new config field in a firmware update is safe: the old NVS data is still valid for all existing keys.

---

## Power Strategy

| Wake phase | Radio state | Typical current |
|---|---|---|
| Deep sleep | All off | ~20 µA |
| Sensor read + BLE advert (3 s) | BLE only | ~30–80 mA |
| NTP sync | Wi-Fi only | ~80–150 mA |
| Photo upload + OTA check | Wi-Fi only | ~100–200 mA |
| Camera capture | Wi-Fi + CAM | ~150–250 mA |

**Rules:**
- BLE and Wi-Fi are **never active simultaneously**.
- Sensors are read first; BLE advertisement finishes before Wi-Fi is started.
- Wi-Fi stays on only for the duration of the photo task (NTP + OTA check + upload), then disconnects immediately.
- The serial CLI window (3 s) adds a small constant overhead per boot — disable it by setting `windowMs = 0` in `main.cpp` if needed for ultra-low-power deployments.

**Rough energy estimate** (7–10 Wh battery, one photo per day, sensors every 5–10 min):
- ~96 sensor wakes/day × 3 s × ~50 mA avg = ~4 mAh
- ~1 photo wake/day × 30 s × ~200 mA avg = ~1.7 mAh
- ~1440 min sleep × ~0.02 mA = ~0.5 mAh
- **Total ≈ 6–7 mAh/day** — comfortable for a 2000 mAh pack with a small solar panel.

---

## BTHome / Home Assistant Integration

The firmware uses [BTHome v2](https://bthome.io/) — a standard BLE advertisement format natively supported by Home Assistant since version 2023.6.

**No MQTT, no ESPHome, no custom integration required.**

Home Assistant with Bluetooth discovers the device automatically and creates entities:

| BTHome object ID | Measurement | Source |
|---|---|---|
| `0x02` — Temperature | °C | DS18B20 (primary) or SHT3x (fallback) |
| `0x03` — Humidity | % | SHT3x |
| `0x0C` — Voltage | V | INA219 bus voltage |
| `0x43` — Current | A | INA219 |
| `0x0D` — Power | W | INA219 |

### Setup in Home Assistant

1. Go to **Settings → Devices & Services → Add Integration → Bluetooth**.
2. Make sure a Bluetooth adapter is available (built-in, USB dongle, or ESPHome BLE proxy).
3. The device appears as `ESP32-S3-Env` (or whatever `deviceName` is set to).
4. Accept the pairing — all sensor entities are created automatically.

> The device advertises for **3 seconds** per wake and then stops (saves energy). HA's passive BLE scanning picks it up during the advertisement window.

### BLE proxy (optional, recommended)

If the ESP32 is not in direct Bluetooth range of the Home Assistant host, add a cheap ESP32 running the [ESPHome BLE proxy](https://esphome.io/components/bluetooth_proxy.html) firmware near the sensor. No further configuration needed.

---

## NTP & Time Synchronisation

The firmware avoids NTP on every wake — it only syncs when needed:

| Situation | Action |
|---|---|
| Cold boot (no RTC sentinel) | Connect Wi-Fi → NTP sync → disconnect |
| Warm boot (deep sleep wakeup) | Estimate time: `lastEpoch + elapsed_ms`. No Wi-Fi needed. |
| Before photo task | If last NTP sync > 6 h ago, re-sync while Wi-Fi is already up |
| Time not trusted | Photo task is skipped — avoids spurious photos |

After a successful sync, the epoch is saved to both RTC memory and NVS. On a power-cycle (RTC lost), the NVS epoch is used as starting point — typically off by only a few seconds/minutes depending on how long the device was unpowered.

**Timezone & NTP servers**: now runtime-configurable — no recompile needed.
Change via serial CLI `[8]`, the web UI (NTP & Fuseau horaire section), or keep `config.h` defaults (`NTP_SERVER_1`, `NTP_SERVER_2`, `NTP_TIMEZONE`).
Examples: `"CET-1CEST,M3.5.0,M10.5.0/3"` (Paris), `"UTC0"`, `"EST5EDT,M3.2.0,M11.1.0"` (New York).

---

## HTTP Photo Upload Service

The photo task sends a single `multipart/form-data` POST to `g_deviceConfig.uploadEndpoint`:

| Part name | Content-Type | Content |
|---|---|---|
| `metadata` | `application/json` | `{"device_id":"…","timestamp":1234567890,"lat":0.0,"lon":0.0}` |
| `image` | `image/jpeg` | Raw JPEG bytes (filename: `photo.jpg`) |

### Minimal Python/Flask receiver

```python
from flask import Flask, request
import os, json, time

app = Flask(__name__)
PHOTO_DIR = "photos"
os.makedirs(PHOTO_DIR, exist_ok=True)

@app.route("/upload", methods=["POST"])
def upload():
    meta = json.loads(request.form.get("metadata", "{}"))
    img  = request.files.get("image")
    if img:
        ts   = meta.get("timestamp", int(time.time()))
        name = f"{meta.get('device_id', 'esp32')}_{ts}.jpg"
        img.save(os.path.join(PHOTO_DIR, name))
        print(f"Saved {name}")
    return "", 200

app.run(host="0.0.0.0", port=8080)
```

### Docker Compose example

```yaml
services:
  photo-receiver:
    image: python:3.11-slim
    working_dir: /app
    volumes:
      - ./receiver:/app
      - ./photos:/app/photos
    command: >
      sh -c "pip install flask -q && python receiver.py"
    ports:
      - "8080:8080"
    restart: unless-stopped
```

---

## Build & Flash

### Prerequisites

- [PlatformIO](https://platformio.org/) CLI or IDE extension
- Python 3.8+

### First flash (USB cable required)

```bash
# 1. Clone the repo
git clone https://github.com/<your-username>/ESP32-S3-Camera-EnvSensors.git
cd ESP32-S3-Camera-EnvSensors

# 2. (Optional) edit src/config.h with your Wi-Fi SSID/password and endpoint
#    Everything else can be changed later via the serial CLI.

# 3. Build + flash + open monitor
pio run -e freenove_esp32s3 --target upload --target monitor
```

The first build downloads the ESP32 Arduino core and all library dependencies (~5–10 min). Subsequent builds are incremental and much faster.

### Monitor output on first boot

```
=== Boot #1  fw:v1.0.0  dev:ESP32-S3-Env ===
[Config] First boot – using compile-time defaults
[Config] Appuyez sur une touche dans 3000 ms pour ouvrir le menu de configuration...
[Config] Aucune touche – configuration actuelle conservée.
[MAIN] Time not trusted – syncing NTP...
[WiFi] Connecting to MyNetwork ... OK (IP: 192.168.1.42)
[NTP] Sync OK — 2025-05-20 14:03:21
[WiFi] Disconnected
[SCHED] Tasks: DS18B20=1 SHT3x=1 INA219=1 Photo=0
[DS18B20] 2 sensors found. T[0]=21.75°C T[1]=19.50°C
[SHT3x] T=22.10°C H=58.3%
[INA219] V=3.82V I=142mA P=542mW
[BTHome] Advertising for 3000 ms (14 bytes service data)
[SLEEP] Entering deep sleep for 120 seconds
```

### Build options

```bash
# Build only (no flash)
pio run -e freenove_esp32s3

# Flash only (already built)
pio run -e freenove_esp32s3 --target upload

# Open serial monitor only
pio device monitor --baud 115200

# OTA push to a device in maintenance mode
pio run -t upload --upload-port <device-IP>
```

> **Board note**: `esp32-s3-devkitc-1` is the closest stock board definition.
> `board_build.arduino.memory_type = qio_opi` enables the octal PSRAM needed for UXGA JPEG buffers.
> `board_build.partitions = default.csv` provides two OTA slots required for HTTP OTA.

---

## CI / CD & Releases

### Continuous Integration

Every push and pull request triggers **CI – Build Firmware** (`.github/workflows/ci.yml`):

- Builds the firmware with PlatformIO on `ubuntu-latest`
- Embeds `FIRMWARE_VERSION = ci-<8-char-sha>`
- Uploads `firmware.bin` + `firmware.elf` as artifacts (7-day retention)
- PlatformIO package cache is keyed on `platformio.ini`

### Creating a Release

```bash
git tag v1.2.3
git push origin v1.2.3
```

The **Release** workflow (`.github/workflows/release.yml`) will:

1. Build with `FIRMWARE_VERSION = v1.2.3` baked in
2. Generate `manifest.json` pointing at the binary download URL
3. Create a GitHub Release with:
   - `firmware-v1.2.3.bin` — flashable binary
   - `firmware-v1.2.3.elf` — with debug symbols
   - `manifest.json` — OTA manifest for running devices

Tags containing `-alpha`, `-beta`, or `-rc` are automatically marked as pre-releases.

---

## OTA Firmware Updates

### 1 — HTTP OTA (automatic, during photo session)

During the daily Wi-Fi session, the device fetches `g_deviceConfig.otaManifestUrl` and compares the remote version to `FIRMWARE_VERSION`. If newer, it downloads and flashes the binary, then reboots.

**Self-hosted manifest** (default, plain HTTP):

```json
{
  "version": "v1.2.3",
  "url": "http://192.168.1.100:8080/firmware/firmware.bin",
  "notes": "Optional description"
}
```

Configure the URL via the serial CLI ([4] OTA) or set it in `config.h` before first flash.

**GitHub Releases manifest** (requires HTTPS — see limitations):

```
https://github.com/<your-username>/ESP32-S3-Camera-EnvSensors/releases/latest/download/manifest.json
```

### 2 — ArduinoOTA (manual, maintenance mode)

Lets you push a new `.bin` over Wi-Fi from PlatformIO or the Arduino IDE.

**Trigger maintenance mode:**

| Method | How |
|---|---|
| GPIO | Hold the **BOOT button** (GPIO 0) during power-on |
| NVS flag | Call `ota::requestMaintenanceMode(true)` then reset |

Once in maintenance mode the device:
- Connects to Wi-Fi
- Prints its IP address on the serial monitor
- Waits up to 2 minutes for a push

```bash
# Push via PlatformIO
pio run -t upload --upload-port 192.168.1.42
```

Password is set by `OTA_DEVICE_PASSWORD` in `config.h` (default: `esp32ota`).

If no push arrives within `OTA_MAINTENANCE_TIMEOUT_MS` (default: 120 s), the device falls through to normal operation.

**Disable OTA entirely**: set `OTA_ENABLED = false` in `config.h` or via the serial CLI.

---

## Known Limitations / TODOs

| # | Issue | Status |
|---|---|---|
| 1 | **Camera GPIO mapping** not validated against Freenove schematic — wrong pins cause `esp_camera_init` error `0x105` | ⚠️ TODO |
| 2 | **Sensor I²C pins** (GPIO 2/3) not verified against Freenove header labels | ⚠️ TODO |
| 3 | **HTTPS upload** — `HTTPClient` does not support TLS; use plain `http://` for now | 🔜 Enhancement |
| 4 | **HTTPS OTA from GitHub** — same TLS limitation; use self-hosted HTTP manifest | 🔜 Enhancement |
| 5 | **Photo retry back-off** — `photoRetryCount` is tracked but no exponential delay is implemented | 🔜 Enhancement |
| 6 | **Multi-DS18B20 BTHome** — only the first sensor's temperature is advertised | 🔜 Enhancement |
| 7 | **GPS coordinates** — hardcoded to 0 in upload metadata | 🔜 Enhancement |
| 8 | **OTA partition size** — `default.csv` limits the app to ~1.3 MB; switch to `default_8MB.csv` for 8 MB flash boards if firmware grows | ℹ️ Note |
| 9 | **Serial CLI password field** is not echoed but is transmitted in plain text over USB | ℹ️ Note |
