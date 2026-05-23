from __future__ import annotations

import base64
import io
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np

from gauge_service.app import create_app
from gauge_service.calibration import GaugeCalibration, CircleParams, PatchRegion, TickMark
from gauge_service.config import ServiceConfig

MAX_ASYNC_RESPONSE_TIME_SECONDS = 0.35


def _generate_image(level: float = 50.0) -> bytes:
    image = np.full((480, 480, 3), 200, dtype=np.uint8)
    center = (240, 240)
    radius = 160
    cv2.circle(image, center, radius, (20, 20, 20), 4)
    # Red needle at fixed angle (not calibration-sensitive for these tests)
    angle_deg = 225 + ((level / 100.0) * 90.0)
    rad = math.radians(angle_deg)
    tip = (
        int(center[0] + math.cos(rad) * radius * 0.7),
        int(center[1] - math.sin(rad) * radius * 0.7),
    )
    cv2.line(image, center, tip, (0, 0, 220), 8)
    cv2.circle(image, center, 10, (40, 40, 40), -1)
    ok, encoded = cv2.imencode('.jpg', image)
    assert ok
    return encoded.tobytes()


def _make_calibration(image_bytes: bytes) -> str:
    """Build a minimal valid calibration JSON for the test image."""
    img = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
    h, w = img.shape[:2]

    # Extract two small template patches from the corners
    def patch_b64(x, y, pw, ph) -> str:
        tmp = np.zeros((ph, pw, 3), dtype=np.uint8)
        tmp[:] = img[y:y+ph, x:x+pw]
        ok, enc = cv2.imencode('.jpg', tmp)
        assert ok
        return base64.b64encode(enc.tobytes()).decode()

    patches = [
        {"x": 5, "y": 5, "w": 40, "h": 40, "template_b64": patch_b64(5, 5, 40, 40)},
        {"x": w-45, "y": 5, "w": 40, "h": 40, "template_b64": patch_b64(w-45, 5, 40, 40)},
    ]
    circle = {"cx": 240.0, "cy": 240.0, "r": 160.0}
    # Two ticks: 0% at 225°, 100% at 315°
    ticks = [
        {"px": 240 + 160 * math.cos(math.radians(225)), "py": 240 - 160 * math.sin(math.radians(225)), "value": 0.0},
        {"px": 240 + 160 * math.cos(math.radians(315)), "py": 240 - 160 * math.sin(math.radians(315)), "value": 100.0},
    ]
    cal = {"image_size": [w, h], "patches": patches, "circle": circle, "ticks": ticks}
    return json.dumps(cal)


def _base_config(tmp_path: Path, **kwargs) -> ServiceConfig:
    defaults = dict(
        data_dir=tmp_path,
        max_snapshots=10,
        serve_history_limit=10,
        mqtt_enabled=False,
        upload_debug_mode=True,
    )
    defaults.update(kwargs)
    return ServiceConfig(**defaults)


# ─────────────────────────────────────────────────────────────────────────────

def test_upload_persists_record_without_calibration(tmp_path: Path) -> None:
    """Without calibration, upload still stores the image (status=no_calibration)."""
    config = _base_config(tmp_path, gauge_config_json="")
    app = create_app(config)
    client = app.test_client()

    img_bytes = _generate_image()
    resp = client.post(
        '/upload',
        data={
            'metadata': json.dumps({'device_id': 'esp32-test', 'timestamp': 1}),
            'image': (io.BytesIO(img_bytes), 'photo.jpg'),
        },
        content_type='multipart/form-data',
    )
    assert resp.status_code == 200
    record = resp.get_json()
    # Without calibration, analysis is absent or status reflects that
    assert record['image_name'].endswith('.jpg')
    assert record['status'] in ('no_calibration', 'ready', 'pending')


def test_upload_prunes_old_records(tmp_path: Path) -> None:
    config = _base_config(tmp_path, max_snapshots=1)
    app = create_app(config)
    client = app.test_client()

    img = _generate_image()
    for ts in (1, 2):
        client.post(
            '/upload',
            data={'metadata': json.dumps({'timestamp': ts}), 'image': (io.BytesIO(img), 'photo.jpg')},
            content_type='multipart/form-data',
        )

    api = client.get('/api/photos')
    assert api.status_code == 200
    assert len(api.get_json()) == 1


def test_upload_returns_immediately_in_async_mode(tmp_path: Path) -> None:
    config = _base_config(tmp_path, upload_debug_mode=False)
    app = create_app(config)
    client = app.test_client()

    started = time.perf_counter()
    resp = client.post(
        '/upload',
        data={
            'metadata': json.dumps({'timestamp': 3}),
            'image': (io.BytesIO(_generate_image()), 'photo.jpg'),
        },
        content_type='multipart/form-data',
    )
    elapsed = time.perf_counter() - started
    assert resp.status_code == 200
    assert resp.get_json()['status'] == 'accepted'
    assert elapsed < MAX_ASYNC_RESPONSE_TIME_SECONDS


def test_index_page_renders(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    app = create_app(config)
    client = app.test_client()

    page = client.get('/')
    assert page.status_code == 200
    assert b'Setup' in page.data


def test_setup_page_renders(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    app = create_app(config)
    client = app.test_client()

    page = client.get('/setup')
    assert page.status_code == 200
    assert b'wizard' in page.data.lower() or b'step' in page.data.lower()


def test_calibration_save_and_load(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    app = create_app(config)
    client = app.test_client()

    img_bytes = _generate_image()
    cal_json = _make_calibration(img_bytes)

    # Save
    resp = client.post(
        '/api/calibration',
        data=json.dumps({'config': cal_json}),
        content_type='application/json',
    )
    assert resp.status_code == 200, resp.get_data(as_text=True)

    # Load
    resp = client.get('/api/calibration')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['configured'] is True
    assert data['calibration']['circle']['r'] == 160.0


def test_calibration_rejects_invalid_json(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    app = create_app(config)
    client = app.test_client()

    resp = client.post(
        '/api/calibration',
        data=json.dumps({'config': 'not-valid-json'}),
        content_type='application/json',
    )
    assert resp.status_code == 400


def test_calibration_simulate_requires_image(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    app = create_app(config)
    client = app.test_client()

    img_bytes = _generate_image()
    cal_json = _make_calibration(img_bytes)

    resp = client.post(
        '/api/calibration/simulate',
        data=json.dumps({'config': cal_json}),
        content_type='application/json',
    )
    # No image in storage yet → 404
    assert resp.status_code == 404


def test_calibration_simulate_with_image(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    app = create_app(config)
    client = app.test_client()

    img_bytes = _generate_image()
    # Upload an image first
    client.post(
        '/upload',
        data={'metadata': '{}', 'image': (io.BytesIO(img_bytes), 'photo.jpg')},
        content_type='multipart/form-data',
    )

    cal_json = _make_calibration(img_bytes)
    resp = client.post(
        '/api/calibration/simulate',
        data=json.dumps({'config': cal_json}),
        content_type='application/json',
    )
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'result' in data
    assert data['result']['status'] in ('ready', 'failed')


def test_upload_with_valid_calibration(tmp_path: Path) -> None:
    img_bytes = _generate_image()
    cal_json = _make_calibration(img_bytes)
    config = _base_config(tmp_path, gauge_config_json=cal_json, upload_debug_mode=True)
    app = create_app(config)
    client = app.test_client()

    resp = client.post(
        '/upload',
        data={'metadata': '{}', 'image': (io.BytesIO(img_bytes), 'photo.jpg')},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 200
    record = resp.get_json()
    assert record['status'] == 'ready'
    assert record['analysis'] is not None
    assert 'percentage' in record['analysis']


def test_calibration_file_endpoint_no_calibration(tmp_path: Path) -> None:
    """GET /api/calibration/file returns 404 when no calibration is configured."""
    config = _base_config(tmp_path, gauge_config_json="")
    app = create_app(config)
    client = app.test_client()

    resp = client.get('/api/calibration/file')
    assert resp.status_code == 404


def test_calibration_file_endpoint_from_env_var(tmp_path: Path) -> None:
    """GET /api/calibration/file returns the calibration JSON when set via env var."""
    img_bytes = _generate_image()
    cal_json = _make_calibration(img_bytes)
    config = _base_config(tmp_path, gauge_config_json=cal_json)
    app = create_app(config)
    client = app.test_client()

    resp = client.get('/api/calibration/file')
    assert resp.status_code == 200
    assert resp.content_type.startswith('application/json')
    data = resp.get_json()
    # The endpoint returns the raw calibration JSON – should be a valid object
    assert 'circle' in data
    assert 'ticks' in data


def test_calibration_file_endpoint_from_saved_file(tmp_path: Path) -> None:
    """GET /api/calibration/file returns the persisted calibration.json file."""
    img_bytes = _generate_image()
    cal_json = _make_calibration(img_bytes)
    config = _base_config(tmp_path, gauge_config_json="")
    app = create_app(config)
    client = app.test_client()

    # Save via API first
    client.post(
        '/api/calibration',
        data=json.dumps({'config': cal_json}),
        content_type='application/json',
    )

    resp = client.get('/api/calibration/file')
    assert resp.status_code == 200
    assert resp.content_type.startswith('application/json')
    data = resp.get_json()
    assert 'circle' in data
