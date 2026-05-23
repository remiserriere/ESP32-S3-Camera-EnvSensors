from __future__ import annotations

import io
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

from gauge_service.app import create_app
from gauge_service.config import ServiceConfig

MAX_ASYNC_RESPONSE_TIME_SECONDS = 0.35


def _generate_image(level: float) -> bytes:
    image = np.full((640, 640, 3), 255, dtype=np.uint8)
    center = (320, 320)
    radius = 190
    low_angle = 225.0
    high_angle = 315.0

    cv2.circle(image, center, radius, (20, 20, 20), 4)
    cv2.putText(image, '5%', (120, 490), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3, cv2.LINE_AA)
    cv2.putText(image, '95%', (400, 490), cv2.FONT_HERSHEY_SIMPLEX, 1.1, (0, 0, 0), 3, cv2.LINE_AA)

    needle_angle = low_angle + ((level - 5.0) / 90.0) * (high_angle - low_angle)
    tip = (
        int(center[0] + math.cos(math.radians(needle_angle)) * radius * 0.7),
        int(center[1] - math.sin(math.radians(needle_angle)) * radius * 0.7),
    )
    cv2.line(image, center, tip, (0, 0, 255), 8)
    cv2.circle(image, center, 10, (40, 40, 40), -1)

    ok, encoded = cv2.imencode('.jpg', image)
    assert ok
    return encoded.tobytes()


def test_upload_persists_and_prunes_records(tmp_path: Path) -> None:
    config = ServiceConfig(
        data_dir=tmp_path,
        max_snapshots=1,
        serve_history_limit=10,
        mqtt_enabled=False,
        analysis_expected_span_deg=90,
        analysis_default_low_angle=225,
        analysis_default_high_angle=315,
        upload_debug_mode=True,
    )
    app = create_app(config)
    client = app.test_client()

    payload1 = {
        'metadata': json.dumps({'device_id': 'esp32-demo', 'timestamp': 1}),
        'image': (io.BytesIO(_generate_image(35)), 'photo.jpg'),
    }
    response1 = client.post('/upload', data=payload1, content_type='multipart/form-data')
    assert response1.status_code == 200
    record1 = response1.get_json()
    assert record1['image_name'].endswith('.jpg')
    assert record1['metadata']['device_id'] == 'esp32-demo'

    payload2 = {
        'metadata': json.dumps({'device_id': 'esp32-demo', 'timestamp': 2}),
        'image': (io.BytesIO(_generate_image(60)), 'photo.jpg'),
    }
    response2 = client.post('/upload', data=payload2, content_type='multipart/form-data')
    assert response2.status_code == 200

    api_latest = client.get('/api/latest')
    assert api_latest.status_code == 200
    latest_record = api_latest.get_json()
    assert latest_record['metadata']['timestamp'] == 2

    api_photos = client.get('/api/photos')
    assert api_photos.status_code == 200
    assert len(api_photos.get_json()) == 1

    page = client.get('/')
    assert page.status_code == 200
    assert b'Latest image' in page.data


def test_upload_returns_immediately_when_not_in_debug_mode(tmp_path: Path) -> None:
    config = ServiceConfig(
        data_dir=tmp_path,
        max_snapshots=5,
        serve_history_limit=10,
        mqtt_enabled=False,
        upload_debug_mode=False,
    )
    app = create_app(config)
    client = app.test_client()

    started = time.perf_counter()
    response = client.post(
        '/upload',
        data={
            'metadata': json.dumps({'device_id': 'esp32-demo', 'timestamp': 3}),
            'image': (io.BytesIO(_generate_image(45)), 'photo.jpg'),
        },
        content_type='multipart/form-data',
    )
    elapsed = time.perf_counter() - started
    assert response.status_code == 200
    body = response.get_json()
    assert body['status'] == 'accepted'
    assert elapsed < MAX_ASYNC_RESPONSE_TIME_SECONDS
