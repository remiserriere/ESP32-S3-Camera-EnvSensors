from __future__ import annotations

import json
import ssl
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

import paho.mqtt.client as mqtt

from .config import ServiceConfig


# ── DeviceConfigPayload ────────────────────────────────────────────────────────
#
# All keys match mqtt_config.cpp's applyConfig() and device_config.cpp's
# load()/save() NVS keys exactly so the payload can be published as-is.
# ──────────────────────────────────────────────────────────────────────────────

@dataclass
class DeviceConfigPayload:
    # Sensors
    ds18_en:  bool  = False
    ds18_int: int   = 10

    sht_en:   bool  = False
    sht_int:  int   = 5

    ina_en:   bool  = False
    ina_int:  int   = 2

    # Daily photo schedule
    ph_hour:  int   = 14
    ph_min:   int   = 0
    ph_win:   int   = 10

    # Network
    wifi_ssid:  str = ""
    wifi_pass:  str = ""
    upload_ep:  str = ""

    # OTA
    ota_en:   bool  = True
    ota_url:  str   = ""

    # MQTT (device channel)
    mqtt_en:   bool = False
    mqtt_host: str  = ""
    mqtt_port: int  = 1883
    mqtt_user: str  = ""
    mqtt_pass: str  = ""
    mqtt_id:   str  = "ESP32-S3-Env"

    # Identity / BLE
    dev_name:  str  = "ESP32-S3-Env"
    boot_win:  int  = 30

    # NTP
    ntp_srv1:  str  = "pool.ntp.org"
    ntp_srv2:  str  = "time.google.com"
    ntp_tz:    str  = "CET-1CEST,M3.5.0,M10.5.0/3"

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceConfigPayload":
        known = {f.name for f in cls.__dataclass_fields__.values()}  # type: ignore[attr-defined]
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)

    @classmethod
    def from_json(cls, raw: str) -> "DeviceConfigPayload":
        return cls.from_dict(json.loads(raw))


# ── Storage helpers ─────────────────────────────────────────────────────────────

_FILENAME = "device_config.json"


def load_device_config(data_dir: Path) -> DeviceConfigPayload:
    """Load from *data_dir/device_config.json*; fall back to defaults if absent."""
    cfg_file = data_dir / _FILENAME
    if cfg_file.exists():
        try:
            raw = cfg_file.read_text(encoding="utf-8").strip()
            if raw:
                return DeviceConfigPayload.from_json(raw)
        except Exception:
            pass
    return DeviceConfigPayload()


def save_device_config(data_dir: Path, payload: DeviceConfigPayload) -> None:
    """Persist *payload* to *data_dir/device_config.json*."""
    cfg_file = data_dir / _FILENAME
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(payload.to_json(), encoding="utf-8")


# ── MQTT publish ────────────────────────────────────────────────────────────────

def publish_device_config(
    service_config: ServiceConfig,
    payload: DeviceConfigPayload,
) -> None:
    """Publish *payload* to ``<mqtt_id>/config/set`` with retain=True.

    Uses the service's MQTT connection settings (broker, port, credentials,
    SSL) to reach the shared broker.  The device subscribes to this retained
    topic at every wake-up via :func:`mqtt_config::syncFromBroker`.

    Raises :class:`Exception` on connection / publish failure.
    """
    if not service_config.mqtt_enabled:
        raise RuntimeError("MQTT is disabled in the service configuration")
    if not service_config.mqtt_host:
        raise RuntimeError("MQTT broker host is not configured")

    topic = f"{payload.mqtt_id}/config/set"
    json_payload = json.dumps(payload.to_dict())

    client = mqtt.Client(
        mqtt.CallbackAPIVersion.VERSION2,
        client_id=service_config.mqtt_client_id,
    )
    if service_config.mqtt_username:
        client.username_pw_set(service_config.mqtt_username, service_config.mqtt_password)
    if service_config.mqtt_use_ssl:
        client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    client.connect(service_config.mqtt_host, service_config.mqtt_port, 30)
    try:
        result = client.publish(topic, json_payload, retain=True)
    finally:
        client.disconnect()

    if result.rc != 0:
        raise RuntimeError(f"MQTT publish failed with code {result.rc}")
