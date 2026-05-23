from __future__ import annotations

import json
from typing import Any

from flask import Flask, jsonify, render_template, request, send_from_directory

from .analyzer import GaugeAnalyzer
from .config import ServiceConfig
from .mqtt import MqttPublisher
from .storage import StorageManager


def create_app(config: ServiceConfig | None = None) -> Flask:
    service_config = config or ServiceConfig.from_env()
    app = Flask(__name__)
    storage = StorageManager(service_config)
    analyzer = GaugeAnalyzer(service_config)
    mqtt = MqttPublisher(service_config)

    app.config["SERVICE_CONFIG"] = service_config
    app.extensions["storage"] = storage
    app.extensions["analyzer"] = analyzer
    app.extensions["mqtt"] = mqtt

    try:
        mqtt.publish_discovery()
    except Exception as exc:  # pragma: no cover - startup connectivity is environment-dependent
        app.logger.warning("MQTT autodiscovery publish failed: %s", exc)

    @app.get("/")
    def index() -> str:
        latest = storage.latest_record()
        records = storage.list_records(limit=service_config.serve_history_limit)
        return render_template("index.html", latest=latest, records=records, config=service_config)

    @app.get("/health")
    def health() -> Any:
        return jsonify({"status": "ok"})

    @app.get("/api/latest")
    def api_latest() -> Any:
        latest = storage.latest_record()
        return jsonify(latest or {})

    @app.get("/api/photos")
    def api_photos() -> Any:
        return jsonify(storage.list_records(limit=service_config.serve_history_limit))

    @app.get("/photos/<path:filename>")
    def photos(filename: str) -> Any:
        return send_from_directory(service_config.photos_dir, filename)

    @app.post("/upload")
    def upload() -> Any:
        image = request.files.get("image")
        if image is None:
            return jsonify({"error": "Missing image field"}), 400

        metadata = _load_metadata(request.form.get("metadata"))
        payload = image.read()
        analysis = analyzer.analyze(payload)
        record = storage.save_record(payload, metadata, analysis)
        try:
            mqtt.publish_state(record)
        except Exception as exc:  # pragma: no cover - runtime connectivity is environment-dependent
            app.logger.warning("MQTT state publish failed: %s", exc)
        return jsonify(record), 200

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
