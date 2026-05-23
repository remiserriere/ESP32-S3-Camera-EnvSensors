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

    def save_record(self, image_bytes: bytes, metadata: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
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
        }

        (self.config.photos_dir / image_name).write_bytes(image_bytes)
        with (self.config.records_dir / f"{record_id}.json").open("w", encoding="utf-8") as handle:
            json.dump(record, handle, ensure_ascii=False, indent=2)

        self._prune()
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

    def latest_record(self) -> dict[str, Any] | None:
        records = self.list_records(limit=1)
        return records[0] if records else None

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
