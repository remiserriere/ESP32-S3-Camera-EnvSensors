from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


_VALID_NEEDLE_METHODS = {"auto", "dark_radial", "hsv_color", "radial_sweep"}


def _get_needle_method(name: str, default: str) -> str:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip().lower()
    if value not in _VALID_NEEDLE_METHODS:
        return default
    return value


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
    # 0 = keep all snapshots (unlimited); positive = keep at most N
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
    # JSON string produced by the setup wizard; empty string = no calibration
    gauge_config_json: str = ""
    # Maximum allowed percentage change between two consecutive readings.
    # 0.0 (default) = disabled; e.g. 10.0 flags readings that jump by >10%.
    max_delta_percent: float = 0.0
    # true  → synchronous detailed analysis response on POST /upload
    # false → immediate HTTP 200 + async background analysis
    upload_debug_mode: bool = False
    # Needle detection method used by the analyser.
    # "auto"         → run all three methods and pick the most confident one.
    # "dark_radial"  → darkness-score sweep (best for black / dark needles).
    # "hsv_color"    → HSV saturation sweep (best for coloured needles).
    # "radial_sweep" → variance-based sweep (generic fallback).
    needle_detection_method: str = "auto"

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        return cls(
            host=os.getenv("APP_HOST", "0.0.0.0"),
            port=_get_int("APP_PORT", 8081),
            data_dir=Path(os.getenv("DATA_DIR", "/data")),
            max_snapshots=max(0, _get_int("MAX_SNAPSHOTS", 30)),
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
            gauge_config_json=os.getenv("GAUGE_CONFIG", "").strip(),
            max_delta_percent=_get_float("MAX_DELTA_PERCENT", 0.0),
            upload_debug_mode=_get_bool("UPLOAD_DEBUG_MODE", False),
            needle_detection_method=_get_needle_method("NEEDLE_DETECTION_METHOD", "auto"),
        )

    @property
    def photos_dir(self) -> Path:
        return self.data_dir / "photos"

    @property
    def records_dir(self) -> Path:
        return self.data_dir / "records"

    @property
    def calibration_file(self) -> Path:
        """Persisted calibration JSON file (overrides GAUGE_CONFIG env var)."""
        return self.data_dir / "calibration.json"

    @property
    def state_topic(self) -> str:
        return f"{self.mqtt_topic_root}/state"
