from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_NEEDLE_METHODS = {"auto", "dark_radial", "hsv_color", "radial_sweep"}

# Maps each editable ServiceConfig field to its env-var name.
# Fields not listed here (host, port, data_dir, gauge_config_json) are
# bootstrap-only and not exposed on the config page.
_FIELD_ENV_MAP: dict[str, str] = {
    "device_name":                    "DEVICE_NAME",
    "max_snapshots":                   "MAX_SNAPSHOTS",
    "serve_history_limit":             "SERVE_HISTORY_LIMIT",
    "mqtt_enabled":                    "MQTT_ENABLED",
    "mqtt_host":                       "MQTT_HOST",
    "mqtt_port":                       "MQTT_PORT",
    "mqtt_username":                   "MQTT_USERNAME",
    "mqtt_password":                   "MQTT_PASSWORD",
    "mqtt_use_ssl":                    "MQTT_USE_SSL",
    "mqtt_client_id":                  "MQTT_CLIENT_ID",
    "mqtt_topic_root":                 "MQTT_TOPIC_ROOT",
    "home_assistant_discovery_prefix": "HA_DISCOVERY_PREFIX",
    "max_delta_percent":               "MAX_DELTA_PERCENT",
    "upload_debug_mode":               "UPLOAD_DEBUG_MODE",
    "needle_detection_method":         "NEEDLE_DETECTION_METHOD",
    "ota_mode":                        "OTA_MODE",
    "github_repo":                     "GITHUB_REPO",
}

# Fields whose change requires a service reboot to take effect.
_FIELDS_NEED_REBOOT: frozenset[str] = frozenset({
    "mqtt_enabled",
    "mqtt_host",
    "mqtt_port",
    "mqtt_username",
    "mqtt_password",
    "mqtt_use_ssl",
    "mqtt_client_id",
    "mqtt_topic_root",
    "home_assistant_discovery_prefix",
})

_UNSET = object()


# ---------------------------------------------------------------------------
# Internal reader: env var → file dict → hardcoded default
# ---------------------------------------------------------------------------

class _EnvReader:
    """Reads config values with priority: env var > file dict > default."""

    def __init__(self, file_data: dict) -> None:
        self._f = file_data

    def str(self, env: str, field: str, file_key: str, default: str) -> str:
        v = os.getenv(env)
        if v is not None:
            return v.strip()
        return str(self._f.get(file_key, default))

    def str_opt(self, env: str, field: str, file_key: str) -> str | None:
        v = os.getenv(env)
        if v is not None:
            return v.strip() or None
        fv = self._f.get(file_key)
        return str(fv) if fv else None

    def bool_(self, env: str, field: str, file_key: str, default: bool) -> bool:
        v = os.getenv(env)
        if v is not None:
            return v.strip().lower() in {"1", "true", "yes", "on"}
        fv = self._f.get(file_key, _UNSET)
        return bool(fv) if fv is not _UNSET else default

    def int_(self, env: str, field: str, file_key: str, default: int) -> int:
        v = os.getenv(env)
        if v is not None:
            return int(v)
        fv = self._f.get(file_key, _UNSET)
        return int(fv) if fv is not _UNSET else default

    def float_(self, env: str, field: str, file_key: str, default: float) -> float:
        v = os.getenv(env)
        if v is not None:
            return float(v)
        fv = self._f.get(file_key, _UNSET)
        return float(fv) if fv is not _UNSET else default

    def needle_method(self, env: str, field: str, file_key: str, default: str) -> str:
        v = os.getenv(env)
        if v is not None:
            v = v.strip().lower()
            return v if v in _VALID_NEEDLE_METHODS else default
        fv = str(self._f.get(file_key, "")).lower()
        return fv if fv in _VALID_NEEDLE_METHODS else default


# ---------------------------------------------------------------------------
# ServiceConfig
# ---------------------------------------------------------------------------

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
    # JSON string produced by the setup wizard; empty string = no calibration.
    # Managed exclusively via /setup – not exposed on the config page.
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
    # OTA firmware update mode served to the ESP32 device.
    # "disabled"     → /api/ota/manifest returns 404 (default).
    # "github_auto"  → manifest generated live from GitHub Releases API;
    #                   ESP32 downloads directly from GitHub.
    # "service_auto" → service downloads & caches the binary from GitHub;
    #                   ESP32 downloads from this service.
    # "manual"       → a user-uploaded binary is served by this service.
    ota_mode: str = "disabled"
    # GitHub repository used for github_auto and service_auto OTA modes.
    # Format: "owner/repo", e.g. "remiserriere/ESP32-S3-Camera-EnvSensors".
    github_repo: str = ""

    # ------------------------------------------------------------------
    # Factories
    # ------------------------------------------------------------------

    @classmethod
    def load(cls, data_dir: Path | None = None) -> "ServiceConfig":
        """Primary factory: file-based defaults overridden by env vars.

        Priority order for each field:
          1. Environment variable (if explicitly set)
          2. ``service_config.json`` in *data_dir* (if the file exists)
          3. Hardcoded default

        *data_dir* and the bootstrap fields (host, port) always come from
        env vars because they are needed before the config file can be located.
        """
        _data_dir = data_dir or Path(os.getenv("DATA_DIR", "/data"))

        # Load persisted file (may not exist on first run).
        file_data: dict = {}
        cfg_file = _data_dir / "service_config.json"
        if cfg_file.exists():
            try:
                raw = cfg_file.read_text(encoding="utf-8")
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    file_data = parsed
            except Exception:
                pass  # bad file → ignore, fall through to defaults

        r = _EnvReader(file_data)
        instance = cls(
            host=r.str("APP_HOST", "host", "host", "0.0.0.0"),
            port=r.int_("APP_PORT", "port", "port", 8081),
            data_dir=_data_dir,
            max_snapshots=max(0, r.int_("MAX_SNAPSHOTS", "max_snapshots", "max_snapshots", 30)),
            serve_history_limit=max(1, r.int_("SERVE_HISTORY_LIMIT", "serve_history_limit", "serve_history_limit", 100)),
            device_name=r.str("DEVICE_NAME", "device_name", "device_name", "Gaz Tank Gauge"),
            mqtt_enabled=r.bool_("MQTT_ENABLED", "mqtt_enabled", "mqtt_enabled", False),
            mqtt_host=r.str("MQTT_HOST", "mqtt_host", "mqtt_host", "localhost"),
            mqtt_port=r.int_("MQTT_PORT", "mqtt_port", "mqtt_port", 1883),
            mqtt_username=r.str_opt("MQTT_USERNAME", "mqtt_username", "mqtt_username"),
            mqtt_password=r.str_opt("MQTT_PASSWORD", "mqtt_password", "mqtt_password"),
            mqtt_use_ssl=r.bool_("MQTT_USE_SSL", "mqtt_use_ssl", "mqtt_use_ssl", False),
            mqtt_client_id=r.str("MQTT_CLIENT_ID", "mqtt_client_id", "mqtt_client_id", "gaz-tank-gauge-service"),
            mqtt_topic_root=r.str("MQTT_TOPIC_ROOT", "mqtt_topic_root", "mqtt_topic_root", "home/gaz_tank_gauge").rstrip("/"),
            home_assistant_discovery_prefix=r.str("HA_DISCOVERY_PREFIX", "home_assistant_discovery_prefix", "home_assistant_discovery_prefix", "homeassistant").rstrip("/"),
            gauge_config_json=r.str("GAUGE_CONFIG", "gauge_config_json", "gauge_config_json", "").strip(),
            max_delta_percent=r.float_("MAX_DELTA_PERCENT", "max_delta_percent", "max_delta_percent", 0.0),
            upload_debug_mode=r.bool_("UPLOAD_DEBUG_MODE", "upload_debug_mode", "upload_debug_mode", False),
            needle_detection_method=r.needle_method("NEEDLE_DETECTION_METHOD", "needle_detection_method", "needle_detection_method", "auto"),
            ota_mode=r.str("OTA_MODE", "ota_mode", "ota_mode", "disabled"),
            github_repo=r.str("GITHUB_REPO", "github_repo", "github_repo", ""),
        )
        return instance

    @classmethod
    def from_env(cls) -> "ServiceConfig":
        """Backward-compatible alias for :meth:`load`."""
        return cls.load()

    # ------------------------------------------------------------------
    # Serialisation (editable fields only – not host/port/data_dir)
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        """Return editable settings as a plain dict (suitable for JSON)."""
        return {
            "device_name":                    self.device_name,
            "max_snapshots":                   self.max_snapshots,
            "serve_history_limit":             self.serve_history_limit,
            "mqtt_enabled":                    self.mqtt_enabled,
            "mqtt_host":                       self.mqtt_host,
            "mqtt_port":                       self.mqtt_port,
            "mqtt_username":                   self.mqtt_username,
            "mqtt_password":                   self.mqtt_password,
            "mqtt_use_ssl":                    self.mqtt_use_ssl,
            "mqtt_client_id":                  self.mqtt_client_id,
            "mqtt_topic_root":                 self.mqtt_topic_root,
            "home_assistant_discovery_prefix": self.home_assistant_discovery_prefix,
            "max_delta_percent":               self.max_delta_percent,
            "upload_debug_mode":               self.upload_debug_mode,
            "needle_detection_method":         self.needle_detection_method,
            "ota_mode":                        self.ota_mode,
            "github_repo":                     self.github_repo,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    def env_overrides(self) -> set[str]:
        """Return editable field names that are currently locked by an env var.

        This is computed on-the-fly from the live environment, so it works
        correctly whether the instance was created via :meth:`load` or
        constructed directly in tests.
        """
        import os as _os
        return {field for field, env in _FIELD_ENV_MAP.items()
                if _os.getenv(env) is not None}

    # ------------------------------------------------------------------
    # Paths / derived properties
    # ------------------------------------------------------------------

    @property
    def config_file(self) -> Path:
        """Persisted service config JSON file."""
        return self.data_dir / "service_config.json"

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
