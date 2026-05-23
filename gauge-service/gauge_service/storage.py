from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import ServiceConfig


class StorageManager:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.config.photos_dir.mkdir(parents=True, exist_ok=True)
        self.config.records_dir.mkdir(parents=True, exist_ok=True)

    def save_record(
        self,
        image_bytes: bytes,
        metadata: dict[str, Any],
        analysis: dict[str, Any] | None,
        status: str = "ready",
        error: str | None = None,
    ) -> dict[str, Any]:
        received_at = datetime.now(UTC)
        record_id = received_at.strftime("%Y%m%dT%H%M%S%fZ")
        image_name = f"{record_id}.jpg"
        record = {
            "id": record_id,
            "received_at": received_at.isoformat().replace("+00:00", "Z"),
            "image_name": image_name,
            "image_url": f"/photos/{image_name}",
            "metadata": metadata,
            "analysis": analysis,
            "status": status,
            "error": error,
        }

        (self.config.photos_dir / image_name).write_bytes(image_bytes)
        with (self.config.records_dir / f"{record_id}.json").open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)

        self._prune()
        return record

    def update_record_analysis(self, record_id: str, analysis: dict[str, Any], status: str = "ready") -> dict[str, Any] | None:
        path = self.config.records_dir / f"{record_id}.json"
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as handle:
            record = json.load(handle)
        record["analysis"] = analysis
        record["status"] = status
        record["error"] = None
        with path.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        return record

    def mark_record_failed(self, record_id: str, error: str) -> dict[str, Any] | None:
        path = self.config.records_dir / f"{record_id}.json"
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as handle:
            record = json.load(handle)
        record["status"] = "failed"
        record["error"] = error
        with path.open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)
        return record

    def list_records(self, limit: int | None = None) -> list[dict[str, Any]]:
        files = sorted(self.config.records_dir.glob("*.json"), reverse=True)
        if limit is not None:
            files = files[:limit]
        records: list[dict[str, Any]] = []
        for path in files:
            with path.open(encoding="utf-8") as handle:
                records.append(json.load(handle))
        return records

    def latest_record(self, require_analysis: bool = False) -> dict[str, Any] | None:
        records = self.list_records(limit=None)
        if not require_analysis:
            return records[0] if records else None
        for record in records:
            if record.get("analysis"):
                return record
        return None

    def get_record(self, record_id: str) -> dict[str, Any] | None:
        path = self.config.records_dir / f"{record_id}.json"
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as handle:
            return json.load(handle)

    def get_image_bytes(self, image_name: str) -> bytes | None:
        path = self.config.photos_dir / image_name
        if not path.exists():
            return None
        return path.read_bytes()

    def _prune(self) -> None:
        files = sorted(self.config.records_dir.glob("*.json"))
        overflow = len(files) - self.config.max_snapshots
        if overflow <= 0:
            return
        for path in files[:overflow]:
            with path.open(encoding="utf-8") as handle:
                record = json.load(handle)
            image_path = self.config.photos_dir / record["image_name"]
            if image_path.exists():
                image_path.unlink()
            path.unlink()
