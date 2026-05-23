from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value is not None else default


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    return float(value) if value is not None else default


@dataclass(slots=True)
class ServiceConfig:
    host: str = "0.0.0.0"
    port: int = 8081
    data_dir: Path = Path("/data")
    max_snapshots: int = 30
    serve_history_limit: int = 100
    device_name: str = "Gaz Tank Gauge"
    mqtt_enabled: bool = False
    mqtt_host: str = "localhost"
    mqtt_port: int = 1883
    mqtt_username: str | None = None
    mqtt_password: str | None = None
    mqtt_use_ssl: bool = False
    mqtt_client_id: str = "gaz-tank-gauge-service"
    mqtt_topic_root: str = "home/gaz_tank_gauge"
    home_assistant_discovery_prefix: str = "homeassistant"
    analysis_low_percent: float = 5.0
    analysis_high_percent: float = 95.0
    analysis_default_low_angle: float = 225.0
    analysis_default_high_angle: float = 315.0
    analysis_expected_span_deg: float = 90.0
    analysis_mirror_mode: str = "none"
    upload_debug_mode: bool = False

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        return cls(
            host=os.getenv("APP_HOST", "0.0.0.0"),
            port=_get_int("APP_PORT", 8081),
            data_dir=Path(os.getenv("DATA_DIR", "/data")),
            max_snapshots=max(1, _get_int("MAX_SNAPSHOTS", 30)),
            serve_history_limit=max(1, _get_int("SERVE_HISTORY_LIMIT", 100)),
            device_name=os.getenv("DEVICE_NAME", "Gaz Tank Gauge"),
            mqtt_enabled=_get_bool("MQTT_ENABLED", False),
            mqtt_host=os.getenv("MQTT_HOST", "localhost"),
            mqtt_port=_get_int("MQTT_PORT", 1883),
            mqtt_username=os.getenv("MQTT_USERNAME") or None,
            mqtt_password=os.getenv("MQTT_PASSWORD") or None,
            mqtt_use_ssl=_get_bool("MQTT_USE_SSL", False),
            mqtt_client_id=os.getenv("MQTT_CLIENT_ID", "gaz-tank-gauge-service"),
            mqtt_topic_root=os.getenv("MQTT_TOPIC_ROOT", "home/gaz_tank_gauge").rstrip("/"),
            home_assistant_discovery_prefix=os.getenv("HA_DISCOVERY_PREFIX", "homeassistant").rstrip("/"),
            analysis_low_percent=_get_float("ANALYSIS_LOW_PERCENT", 5.0),
            analysis_high_percent=_get_float("ANALYSIS_HIGH_PERCENT", 95.0),
            analysis_default_low_angle=_get_float("ANALYSIS_DEFAULT_LOW_ANGLE", 225.0),
            analysis_default_high_angle=_get_float("ANALYSIS_DEFAULT_HIGH_ANGLE", 315.0),
            analysis_expected_span_deg=_get_float("ANALYSIS_EXPECTED_SPAN_DEG", 90.0),
            analysis_mirror_mode=(os.getenv("ANALYSIS_MIRROR_MODE", "none").strip().lower()),
            upload_debug_mode=_get_bool("UPLOAD_DEBUG_MODE", False),
        )

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def records_dir(self) -> Path:
        return self.data_dir / "records"

    @property
    def state_topic(self) -> str:
        return f"{self.mqtt_topic_root}/state"
