from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# ── OTA modes ─────────────────────────────────────────────────────────────────

OTA_MODES = ("disabled", "github_auto", "service_auto", "manual")

# Sub-directory inside data_dir where firmware binaries are stored.
_FIRMWARE_DIR = "firmware"
_FIRMWARE_BIN = "firmware.bin"
_FIRMWARE_META = "firmware_meta.json"
_MANUAL_BIN = "manual.bin"
_MANUAL_META = "manual_meta.json"
_OTA_CONFIG_FILE = "ota_config.json"

# GitHub API headers
_GH_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "gauge-service-ota/1.0",
}


# ── OtaConfig ─────────────────────────────────────────────────────────────────

@dataclass
class OtaConfig:
    mode: str = "disabled"
    github_repo: str = ""
    # Populated automatically when mode=service_auto and a firmware is cached
    cached_version: str = ""
    cached_notes: str = ""
    # Populated by the user when mode=manual
    manual_version: str = ""
    manual_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode":            self.mode,
            "github_repo":     self.github_repo,
            "cached_version":  self.cached_version,
            "cached_notes":    self.cached_notes,
            "manual_version":  self.manual_version,
            "manual_notes":    self.manual_notes,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)

    @classmethod
    def from_dict(cls, data: dict) -> "OtaConfig":
        mode = data.get("mode", "disabled")
        if mode not in OTA_MODES:
            mode = "disabled"
        return cls(
            mode=mode,
            github_repo=data.get("github_repo", ""),
            cached_version=data.get("cached_version", ""),
            cached_notes=data.get("cached_notes", ""),
            manual_version=data.get("manual_version", ""),
            manual_notes=data.get("manual_notes", ""),
        )

    @classmethod
    def from_json(cls, raw: str) -> "OtaConfig":
        return cls.from_dict(json.loads(raw))


# ── Storage helpers ────────────────────────────────────────────────────────────

def _firmware_dir(data_dir: Path) -> Path:
    d = data_dir / _FIRMWARE_DIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def load_ota_config(data_dir: Path) -> OtaConfig:
    cfg_file = data_dir / _OTA_CONFIG_FILE
    if cfg_file.exists():
        try:
            raw = cfg_file.read_text(encoding="utf-8").strip()
            if raw:
                return OtaConfig.from_json(raw)
        except Exception:
            pass
    return OtaConfig()


def save_ota_config(data_dir: Path, cfg: OtaConfig) -> None:
    cfg_file = data_dir / _OTA_CONFIG_FILE
    cfg_file.parent.mkdir(parents=True, exist_ok=True)
    cfg_file.write_text(cfg.to_json(), encoding="utf-8")


# ── GitHub helpers ────────────────────────────────────────────────────────────

def _github_request(url: str) -> dict:
    """Fetch *url* from the GitHub API and return the parsed JSON dict."""
    req = urllib.request.Request(url, headers=_GH_HEADERS)
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _find_bin_asset(release: dict) -> str | None:
    """Return the browser_download_url of the first .bin asset in *release*."""
    for asset in release.get("assets", []):
        name: str = asset.get("name", "")
        if name.endswith(".bin"):
            return asset.get("browser_download_url", "")
    return None


def fetch_latest_github_release(github_repo: str) -> dict:
    """Return release metadata dict: version, bin_url, notes.

    Raises :class:`ValueError` if no .bin asset is found.
    Raises :class:`urllib.error.URLError` / :class:`Exception` on network error.
    """
    url = f"https://api.github.com/repos/{github_repo}/releases/latest"
    release = _github_request(url)
    tag = release.get("tag_name", "")
    notes = release.get("body", "")
    bin_url = _find_bin_asset(release)
    if not bin_url:
        raise ValueError(f"No .bin asset found in latest release of {github_repo!r}")
    return {"version": tag, "url": bin_url, "notes": notes}


# ── Manifest generation ────────────────────────────────────────────────────────

def generate_manifest(
    data_dir: Path,
    cfg: OtaConfig,
    service_base_url: str,
) -> dict | None:
    """Return the OTA manifest dict or *None* if OTA is disabled.

    *service_base_url* is used to build the firmware URL for modes that serve
    the binary locally (``service_auto``, ``manual``).
    """
    if cfg.mode == "disabled":
        return None

    if cfg.mode == "github_auto":
        if not cfg.github_repo:
            return None
        info = fetch_latest_github_release(cfg.github_repo)
        return {
            "version": info["version"],
            "url":     info["url"],
            "notes":   (info["notes"] or "")[:200],
        }

    if cfg.mode == "service_auto":
        fw_file = _firmware_dir(data_dir) / _FIRMWARE_BIN
        if not fw_file.exists():
            return None
        return {
            "version": cfg.cached_version or "unknown",
            "url":     f"{service_base_url.rstrip('/')}/api/ota/firmware",
            "notes":   cfg.cached_notes[:200] if cfg.cached_notes else "",
        }

    if cfg.mode == "manual":
        fw_file = _firmware_dir(data_dir) / _MANUAL_BIN
        if not fw_file.exists():
            return None
        return {
            "version": cfg.manual_version or "manual",
            "url":     f"{service_base_url.rstrip('/')}/api/ota/firmware",
            "notes":   cfg.manual_notes[:200] if cfg.manual_notes else "",
        }

    return None


# ── Firmware fetch / serve ─────────────────────────────────────────────────────

def fetch_and_cache_firmware(data_dir: Path, cfg: OtaConfig) -> dict:
    """Download the latest firmware binary from GitHub and cache it locally.

    Returns a dict with version / notes / size_bytes.
    Raises on network error or if no .bin asset is found.
    """
    if not cfg.github_repo:
        raise ValueError("github_repo is not configured")

    info = fetch_latest_github_release(cfg.github_repo)
    fw_dir = _firmware_dir(data_dir)
    tmp_path = fw_dir / "_firmware.tmp"
    dest_path = fw_dir / _FIRMWARE_BIN

    req = urllib.request.Request(info["url"], headers=_GH_HEADERS)
    with urllib.request.urlopen(req, timeout=60) as resp:
        with open(tmp_path, "wb") as f:
            shutil.copyfileobj(resp, f)

    shutil.move(str(tmp_path), str(dest_path))

    size = dest_path.stat().st_size
    meta = {
        "version": info["version"],
        "notes":   info["notes"],
        "size":    size,
        "source":  info["url"],
    }
    (fw_dir / _FIRMWARE_META).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return meta


def save_manual_firmware(
    data_dir: Path,
    file_bytes: bytes,
    version: str,
    notes: str = "",
) -> dict:
    """Store a manually-uploaded firmware binary.

    Returns a dict with version / size_bytes.
    """
    fw_dir = _firmware_dir(data_dir)
    dest_path = fw_dir / _MANUAL_BIN
    dest_path.write_bytes(file_bytes)

    size = len(file_bytes)
    meta = {"version": version, "notes": notes, "size": size}
    (fw_dir / _MANUAL_META).write_text(json.dumps(meta, indent=2), encoding="utf-8")

    return meta


def get_firmware_path(data_dir: Path, cfg: OtaConfig) -> Path | None:
    """Return the path to the firmware binary to serve, or *None* if unavailable."""
    if cfg.mode == "service_auto":
        p = _firmware_dir(data_dir) / _FIRMWARE_BIN
        return p if p.exists() else None
    if cfg.mode == "manual":
        p = _firmware_dir(data_dir) / _MANUAL_BIN
        return p if p.exists() else None
    return None


def ota_status(data_dir: Path, cfg: OtaConfig) -> dict[str, Any]:
    """Return a status dict suitable for the /api/ota/status endpoint."""
    fw_dir = _firmware_dir(data_dir)

    cached_meta: dict = {}
    meta_file = fw_dir / _FIRMWARE_META
    if meta_file.exists():
        try:
            cached_meta = json.loads(meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    manual_meta: dict = {}
    manual_meta_file = fw_dir / _MANUAL_META
    if manual_meta_file.exists():
        try:
            manual_meta = json.loads(manual_meta_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    return {
        "mode":           cfg.mode,
        "github_repo":    cfg.github_repo,
        "cached_version": cfg.cached_version,
        "cached_notes":   cfg.cached_notes,
        "cached_size":    cached_meta.get("size"),
        "manual_version": cfg.manual_version,
        "manual_notes":   cfg.manual_notes,
        "manual_size":    manual_meta.get("size"),
        "firmware_ready": (fw_dir / _FIRMWARE_BIN).exists() if cfg.mode == "service_auto"
                          else (fw_dir / _MANUAL_BIN).exists() if cfg.mode == "manual"
                          else None,
    }
