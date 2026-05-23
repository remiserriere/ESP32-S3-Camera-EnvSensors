from __future__ import annotations

import atexit
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

_RECORD_ID_RE = re.compile(r'^\d{8}T\d{12}Z$')

from .analyzer import GaugeReader
from .calibration import GaugeCalibration
from .config import ServiceConfig, _FIELD_ENV_MAP, _FIELDS_NEED_REBOOT, _VALID_NEEDLE_METHODS
from .mqtt import MqttPublisher
from .storage import StorageManager


def create_app(config: ServiceConfig | None = None) -> Flask:
    service_config = config or ServiceConfig.load()
    app = Flask(__name__)
    storage = StorageManager(service_config)
    reader = GaugeReader(service_config)
    mqtt = MqttPublisher(service_config)
    analyzer_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="gauge-reader")
    atexit.register(lambda: analyzer_pool.shutdown(wait=False, cancel_futures=True))

    app.config["SERVICE_CONFIG"] = service_config
    app.extensions["storage"] = storage
    app.extensions["reader"] = reader
    app.extensions["mqtt"] = mqtt

    try:
        mqtt.publish_discovery()
    except Exception as exc:  # pragma: no cover - startup connectivity is environment-dependent
        app.logger.warning("MQTT autodiscovery publish failed: %s", exc)

    # ------------------------------------------------------------------
    # Calibration helpers
    # ------------------------------------------------------------------

    def _load_calibration() -> GaugeCalibration | None:
        """Load calibration from persisted file first, then env var fallback."""
        cal_file = service_config.calibration_file
        if cal_file.exists():
            try:
                raw = cal_file.read_text(encoding="utf-8").strip()
                if raw:
                    return GaugeCalibration.from_json(raw)
            except Exception as exc:
                app.logger.warning("Failed to load calibration file: %s", exc)
        raw_env = service_config.gauge_config_json.strip()
        if raw_env:
            try:
                return GaugeCalibration.from_json(raw_env)
            except Exception as exc:
                app.logger.warning("Failed to parse GAUGE_CONFIG env var: %s", exc)
        return None

    def _save_calibration(raw_json: str) -> None:
        cal_file = service_config.calibration_file
        cal_file.parent.mkdir(parents=True, exist_ok=True)
        cal_file.write_text(raw_json, encoding="utf-8")

    def _calibration_status(cal: GaugeCalibration | None) -> dict[str, Any]:
        if cal is None:
            return {"configured": False, "errors": ["No calibration found"]}
        errors = cal.validate()
        return {"configured": len(errors) == 0, "errors": errors}

    # ------------------------------------------------------------------
    # Service config helpers
    # ------------------------------------------------------------------

    def _load_service_config_file() -> dict:
        """Read service_config.json from disk (empty dict if missing/invalid)."""
        cfg_file = service_config.config_file
        if cfg_file.exists():
            try:
                raw = cfg_file.read_text(encoding="utf-8")
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, dict) else {}
            except Exception as exc:
                app.logger.warning("Failed to read service config file: %s", exc)
        return {}

    def _save_service_config_file(data: dict) -> None:
        cfg_file = service_config.config_file
        cfg_file.parent.mkdir(parents=True, exist_ok=True)
        cfg_file.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _apply_hot_reload(data: dict) -> list[str]:
        """Apply editable, hot-reloadable fields directly to the live config.

        Returns the list of fields that were updated in memory.
        """
        updated: list[str] = []
        locked = service_config.env_overrides()
        for field, value in data.items():
            if field in _FIELDS_NEED_REBOOT:
                continue  # skip — requires restart
            if field in locked:
                continue  # env var wins, cannot override at runtime
            if not hasattr(service_config, field):
                continue
            try:
                setattr(service_config, field, value)
                updated.append(field)
            except Exception as exc:
                app.logger.warning("Hot-reload failed for %s: %s", field, exc)
        return updated

    def _validate_config_body(body: dict) -> list[str]:
        """Return a list of validation error strings (empty = OK)."""
        errors: list[str] = []
        if "max_snapshots" in body:
            try:
                if int(body["max_snapshots"]) < 0:
                    errors.append("max_snapshots must be >= 0")
            except (TypeError, ValueError):
                errors.append("max_snapshots must be an integer")
        if "serve_history_limit" in body:
            try:
                if int(body["serve_history_limit"]) < 1:
                    errors.append("serve_history_limit must be >= 1")
            except (TypeError, ValueError):
                errors.append("serve_history_limit must be an integer")
        if "mqtt_port" in body:
            try:
                p = int(body["mqtt_port"])
                if not (1 <= p <= 65535):
                    errors.append("mqtt_port must be between 1 and 65535")
            except (TypeError, ValueError):
                errors.append("mqtt_port must be an integer")
        if "needle_detection_method" in body:
            if body["needle_detection_method"] not in _VALID_NEEDLE_METHODS:
                errors.append(
                    f"needle_detection_method must be one of {sorted(_VALID_NEEDLE_METHODS)}"
                )
        return errors

    # ------------------------------------------------------------------
    # Web UI routes
    # ------------------------------------------------------------------

    @app.get("/")
    def index() -> str:
        cal = _load_calibration()
        cal_status = _calibration_status(cal)
        latest = storage.latest_record()
        records = storage.list_records(limit=service_config.serve_history_limit)
        return render_template(
            "index.html",
            latest=latest,
            records=records,
            config=service_config,
            calibration_configured=cal_status["configured"],
            calibration_errors=cal_status["errors"],
        )

    @app.get("/setup")
    def setup() -> str:
        cal = _load_calibration()
        latest = storage.latest_record()
        return render_template(
            "setup.html",
            config=service_config,
            latest=latest,
            existing_calibration=cal.to_json() if cal else "",
        )

    @app.get("/config")
    def config_page() -> str:
        return render_template(
            "config.html",
            config=service_config,
            env_overrides=service_config.env_overrides(),
            fields_need_reboot=_FIELDS_NEED_REBOOT,
        )

    # ------------------------------------------------------------------
    # Health / API
    # ------------------------------------------------------------------

    @app.get("/health")
    def health() -> Any:
        return jsonify({"status": "ok"})

    @app.post("/api/reboot")
    def reboot() -> Any:
        """Gracefully exit the process so Docker / K8s can restart it.

        The response is sent first; the process is terminated in a daemon thread
        after a short delay so the HTTP response has time to reach the client.
        The container restart policy (``restart: unless-stopped`` in Compose, or
        the K8s restart policy) will bring the service back automatically.
        """
        def _do_exit() -> None:
            import os as _os
            import time
            time.sleep(0.4)
            # os._exit() terminates the whole process from any thread.
            # sys.exit() would only raise SystemExit in this daemon thread and
            # leave the main process running with a dead thread-pool, causing
            # every subsequent /upload to fail with "cannot schedule new futures
            # after shutdown".
            _os._exit(0)

        t = threading.Thread(target=_do_exit, daemon=True, name="reboot-trigger")
        t.start()
        app.logger.info("Reboot requested via /api/reboot — exiting in 400 ms")
        return jsonify({"status": "restarting"}), 200

    @app.get("/api/latest")
    def api_latest() -> Any:
        latest = storage.latest_record(require_analysis=True)
        return jsonify(latest or {})

    @app.get("/api/photos")
    def api_photos() -> Any:
        return jsonify(storage.list_records(limit=service_config.serve_history_limit))

    @app.get("/photos/<path:filename>")
    def photos(filename: str) -> Any:
        return send_from_directory(service_config.photos_dir, filename)

    # ------------------------------------------------------------------
    # Service config API
    # ------------------------------------------------------------------

    @app.get("/api/config")
    def get_service_config() -> Any:
        """Return current effective config + which fields are env-locked."""
        return jsonify({
            "config": service_config.to_dict(),
            "env_overrides": sorted(service_config.env_overrides()),
            "fields_need_reboot": sorted(_FIELDS_NEED_REBOOT),
        })

    @app.get("/api/config/file")
    def get_service_config_file() -> Any:
        """Serve service_config.json so it can be downloaded or volume-mounted."""
        cfg_file = service_config.config_file
        if cfg_file.exists():
            try:
                raw = cfg_file.read_text(encoding="utf-8").strip()
                if raw:
                    return Response(
                        raw,
                        status=200,
                        mimetype="application/json",
                        headers={"Content-Disposition": "inline; filename=service_config.json"},
                    )
            except Exception as exc:
                app.logger.warning("Failed to read service config file: %s", exc)
                return jsonify({"error": "Failed to read config file"}), 500
        # No file yet: return the current in-memory config so the user can
        # download it as a starting point.
        return Response(
            service_config.to_json(),
            status=200,
            mimetype="application/json",
            headers={"Content-Disposition": "inline; filename=service_config.json"},
        )

    @app.post("/api/config")
    def save_service_config() -> Any:
        """Persist editable service configuration and hot-reload safe fields."""
        body = request.get_json(silent=True) or {}
        if not body:
            return jsonify({"error": "Empty or invalid JSON body"}), 400

        # Drop fields that cannot be changed via the UI.
        body.pop("host", None)
        body.pop("port", None)
        body.pop("data_dir", None)
        body.pop("gauge_config_json", None)

        # Also ignore fields locked by env vars.
        locked = service_config.env_overrides()
        for field in locked:
            body.pop(field, None)

        validation_errors = _validate_config_body(body)
        if validation_errors:
            return jsonify({"error": "Validation failed", "details": validation_errors}), 422

        # Merge with existing file content so we don't lose fields not in body.
        existing = _load_service_config_file()
        existing.update(body)

        try:
            _save_service_config_file(existing)
        except Exception as exc:
            app.logger.error("Failed to save service config: %s", exc)
            return jsonify({"error": "Failed to persist config"}), 500

        # Hot-reload compatible fields immediately.
        hot_reloaded = _apply_hot_reload(body)

        needs_reboot = [f for f in body if f in _FIELDS_NEED_REBOOT and f not in locked]
        return jsonify({
            "ok": True,
            "hot_reloaded": hot_reloaded,
            "reboot_required": bool(needs_reboot),
            "reboot_fields": needs_reboot,
        })

    # ------------------------------------------------------------------
    # Calibration API
    # ------------------------------------------------------------------

    @app.get("/api/calibration/file")
    def get_calibration_file() -> Any:
        """Serve the raw calibration.json file so it can be opened in a browser
        tab or downloaded, and used as a volume-mounted file in Docker / K8s."""
        cal_file = service_config.calibration_file
        if cal_file.exists():
            try:
                raw = cal_file.read_text(encoding="utf-8").strip()
                if raw:
                    return Response(
                        raw,
                        status=200,
                        mimetype="application/json",
                        headers={"Content-Disposition": "inline; filename=calibration.json"},
                    )
            except Exception as exc:
                app.logger.warning("Failed to read calibration file: %s", exc)
                return jsonify({"error": "Failed to read calibration file"}), 500
        # Fall back to env var so the endpoint is still useful even without a
        # persisted file (e.g. user injected GAUGE_CONFIG directly)
        raw_env = service_config.gauge_config_json.strip()
        if raw_env:
            return Response(
                raw_env,
                status=200,
                mimetype="application/json",
                headers={"Content-Disposition": "inline; filename=calibration.json"},
            )
        return jsonify({"error": "No calibration available"}), 404

    @app.get("/api/calibration")
    def get_calibration() -> Any:
        cal = _load_calibration()
        if cal is None:
            return jsonify({"calibration": None, "configured": False, "errors": ["No calibration"]})
        errors = cal.validate()
        return jsonify({
            "calibration": cal.to_dict(),
            "configured": len(errors) == 0,
            "errors": errors,
        })

    @app.post("/api/calibration")
    def save_calibration_route() -> Any:
        body = request.get_json(silent=True) or {}
        raw = body.get("config", "")
        if not raw:
            return jsonify({"error": "Missing 'config' field"}), 400
        try:
            cal = GaugeCalibration.from_json(raw)
        except Exception as exc:
            app.logger.warning("Invalid calibration JSON: %s", exc)
            return jsonify({"error": "Invalid calibration JSON"}), 400
        errors = cal.validate()
        if errors:
            return jsonify({"error": "Calibration validation failed", "details": errors}), 422
        try:
            _save_calibration(raw)
        except Exception as exc:
            app.logger.error("Failed to save calibration: %s", exc)
            return jsonify({"error": "Failed to persist calibration"}), 500
        return jsonify({"ok": True, "errors": []})

    @app.post("/api/calibration/simulate")
    def calibration_simulate() -> Any:
        """Analyse the latest stored image with a given calibration (not saved)."""
        body = request.get_json(silent=True) or {}
        raw = body.get("config", "")
        if not raw:
            return jsonify({"error": "Missing 'config' field"}), 400
        # Optional needle detection method override for this simulation only.
        # Accepted values: "auto", "dark_radial", "hsv_color", "radial_sweep".
        from .config import _VALID_NEEDLE_METHODS
        method_override = body.get("method", "").strip().lower() or None
        if method_override and method_override not in _VALID_NEEDLE_METHODS:
            return jsonify({"error": f"Invalid method '{method_override}'. Valid values: {sorted(_VALID_NEEDLE_METHODS)}"}), 400
        try:
            cal = GaugeCalibration.from_json(raw)
        except Exception as exc:
            app.logger.warning("Invalid calibration JSON for simulate: %s", exc)
            return jsonify({"error": "Invalid calibration JSON"}), 400
        errors = cal.validate()
        if errors:
            return jsonify({"error": "Calibration validation failed", "details": errors}), 422

        latest = storage.latest_record()
        if latest is None:
            return jsonify({"error": "No image available for simulation"}), 404

        image_bytes = storage.get_image_bytes(latest["image_name"])
        if image_bytes is None:
            return jsonify({"error": "Image file not found"}), 404

        try:
            result = reader.analyze(image_bytes, cal, needle_method=method_override)
        except Exception as exc:
            app.logger.error("Simulation analysis failed: %s", exc)
            return jsonify({"error": "Analysis failed"}), 500

        # Generate debug image and store as temp file (reuse record ID as key)
        try:
            debug_bytes = reader.draw_debug_image(image_bytes, result, cal)
            debug_name = f"debug_sim_{latest['id']}.jpg"
            debug_path = service_config.photos_dir / debug_name
            debug_path.write_bytes(debug_bytes)
        except Exception as exc:
            app.logger.warning("Could not generate debug image: %s", exc)
            debug_name = None

        return jsonify({
            "result": result,
            "debug_image_url": f"/photos/{debug_name}" if debug_name else None,
            "image_url": latest["image_url"],
            "received_at": latest["received_at"],
        })

    # ------------------------------------------------------------------
    # Debug image
    # ------------------------------------------------------------------

    @app.get("/debug_image/<record_id>")
    def debug_image(record_id: str) -> Any:
        if not _RECORD_ID_RE.match(record_id):
            return jsonify({"error": "Invalid record ID"}), 400
        record = storage.get_record(record_id)
        if record is None:
            return jsonify({"error": "Record not found"}), 404
        if not record.get("analysis"):
            return jsonify({"error": "Analysis not ready"}), 409
        image_bytes = storage.get_image_bytes(record["image_name"])
        if image_bytes is None:
            return jsonify({"error": "Image file not found"}), 404
        cal = _load_calibration()
        if cal is None:
            return jsonify({"error": "No calibration configured"}), 409
        try:
            debug_bytes = reader.draw_debug_image(image_bytes, record["analysis"], cal)
        except Exception as exc:
            app.logger.error("Debug image generation failed: %s", exc)
            return jsonify({"error": "Failed to generate debug image"}), 500
        return Response(debug_bytes, mimetype="image/jpeg")

    # ------------------------------------------------------------------
    # Photo upload
    # ------------------------------------------------------------------

    def _process_async(record_id: str, payload: bytes) -> None:
        try:
            cal = _load_calibration()
            if cal is None or cal.validate():
                # No valid calibration – store without analysis
                storage.update_record_analysis(record_id, None, status="no_calibration")
                return

            prev_pct = _get_prev_percentage()
            analysis = reader.analyze(payload, cal, prev_percentage=prev_pct)
            record = storage.update_record_analysis(record_id, analysis, status="ready")
            if record is None:
                app.logger.warning("Record %s disappeared before analysis completion", record_id)
                return
            try:
                mqtt.publish_state(record)
            except Exception as exc:  # pragma: no cover
                app.logger.warning("MQTT state publish failed: %s", exc)
        except Exception:
            app.logger.exception("Async analysis failed for record %s", record_id)
            storage.mark_record_failed(record_id, "Async analysis exception")

    def _get_prev_percentage() -> float | None:
        latest = storage.latest_record(require_analysis=True)
        if latest and latest.get("analysis"):
            pct = latest["analysis"].get("percentage")
            return float(pct) if pct is not None else None
        return None

    @app.post("/upload")
    def upload() -> Any:
        image = request.files.get("image")
        if image is None:
            return jsonify({"error": "Missing image field"}), 400

        metadata = _load_metadata(request.form.get("metadata"))
        payload = image.read()

        if service_config.upload_debug_mode:
            cal = _load_calibration()
            analysis: dict[str, Any] | None = None
            status = "no_calibration"
            if cal is not None and not cal.validate():
                try:
                    prev_pct = _get_prev_percentage()
                    analysis = reader.analyze(payload, cal, prev_percentage=prev_pct)
                    status = "ready"
                except Exception as exc:
                    app.logger.error("Sync analysis failed: %s", exc)
                    status = "failed"
            record = storage.save_record(payload, metadata, analysis, status=status)
            if analysis:
                try:
                    mqtt.publish_state(record)
                except Exception as exc:  # pragma: no cover
                    app.logger.warning("MQTT state publish failed: %s", exc)
            return jsonify(record), 200

        record = storage.save_record(payload, metadata, analysis=None, status="pending")
        analyzer_pool.submit(_process_async, record["id"], payload)
        return jsonify({"id": record["id"], "status": "accepted"}), 200

    return app


def _load_metadata(raw_value: str | None) -> dict[str, Any]:
    if not raw_value:
        return {}
    try:
        data = json.loads(raw_value)
    except json.JSONDecodeError:
        return {"raw_metadata": raw_value}
    return data if isinstance(data, dict) else {"metadata": data}


if __name__ == "__main__":
    cfg = ServiceConfig.from_env()
    create_app(cfg).run(host=cfg.host, port=cfg.port)
