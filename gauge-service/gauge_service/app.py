from __future__ import annotations

import atexit
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, render_template, request, send_from_directory

_RECORD_ID_RE = re.compile(r'^\d{8}T\d{12}Z$')

from .analyzer import GaugeReader
from .calibration import GaugeCalibration
from .config import ServiceConfig
from .mqtt import MqttPublisher
from .storage import StorageManager


def create_app(config: ServiceConfig | None = None) -> Flask:
    service_config = config or ServiceConfig.from_env()
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

    # ------------------------------------------------------------------
    # Health / API
    # ------------------------------------------------------------------

    @app.get("/health")
    def health() -> Any:
        return jsonify({"status": "ok"})

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
    # Calibration API
    # ------------------------------------------------------------------

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
            result = reader.analyze(image_bytes, cal)
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
