from __future__ import annotations

import base64
import io
import json
import math
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

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
    assert b'Reboot' in page.data


def test_index_page_has_reboot_button(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    app = create_app(config)
    client = app.test_client()

    page = client.get('/')
    assert b'rebootService' in page.data
    assert b'/api/reboot' in page.data


def test_reboot_endpoint_returns_200(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/reboot should return 200 without actually calling sys.exit."""
    import threading as _threading

    exited: list[bool] = []

    def _patched_start(self):
        # Don't start the exit thread in the test; just record that it was triggered
        exited.append(True)

    monkeypatch.setattr(_threading.Thread, 'start', _patched_start)

    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    resp = client.post('/api/reboot')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['status'] == 'restarting'
    assert exited  # the exit thread was triggered


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


# ─────────────────────────────────────────────────────────────────────────────
# Service config page and API
# ─────────────────────────────────────────────────────────────────────────────

def test_config_page_renders(tmp_path: Path) -> None:
    """GET /config renders without error and contains key headings."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    resp = client.get('/config')
    assert resp.status_code == 200
    body = resp.data
    assert b'Settings' in body
    assert b'MQTT' in body
    assert b'Analysis' in body


def test_config_page_shows_settings_link_on_index(tmp_path: Path) -> None:
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    resp = client.get('/')
    assert resp.status_code == 200
    assert b'/config' in resp.data


def test_config_api_get(tmp_path: Path) -> None:
    """GET /api/config returns current config and env_overrides list."""
    config = _base_config(tmp_path, device_name="TestGauge", max_snapshots=7)
    flask_app = create_app(config)
    client = flask_app.test_client()

    resp = client.get('/api/config')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'config' in data
    assert data['config']['device_name'] == 'TestGauge'
    assert data['config']['max_snapshots'] == 7
    assert 'env_overrides' in data
    assert isinstance(data['env_overrides'], list)
    assert 'fields_need_reboot' in data


def test_config_api_save_hot_reload(tmp_path: Path) -> None:
    """POST /api/config saves to file and hot-reloads non-MQTT fields."""
    config = _base_config(tmp_path, device_name="Before")
    flask_app = create_app(config)
    client = flask_app.test_client()

    resp = client.post(
        '/api/config',
        data=json.dumps({"device_name": "After", "max_snapshots": 99}),
        content_type='application/json',
    )
    assert resp.status_code == 200
    result = resp.get_json()
    assert result['ok'] is True
    assert 'device_name' in result['hot_reloaded']
    assert 'max_snapshots' in result['hot_reloaded']

    # Live config updated
    assert config.device_name == 'After'
    assert config.max_snapshots == 99

    # Persisted to file
    cfg_file = tmp_path / 'service_config.json'
    assert cfg_file.exists()
    saved = json.loads(cfg_file.read_text())
    assert saved['device_name'] == 'After'
    assert saved['max_snapshots'] == 99


def test_config_api_save_mqtt_requires_reboot(tmp_path: Path) -> None:
    """POST /api/config with MQTT fields returns reboot_required=True."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    resp = client.post(
        '/api/config',
        data=json.dumps({"mqtt_host": "newhost", "mqtt_port": 1884}),
        content_type='application/json',
    )
    assert resp.status_code == 200
    result = resp.get_json()
    assert result['reboot_required'] is True
    assert 'mqtt_host' in result['reboot_fields']


def test_config_api_save_validation_errors(tmp_path: Path) -> None:
    """POST /api/config rejects invalid values with 422."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    resp = client.post(
        '/api/config',
        data=json.dumps({"max_snapshots": -5, "mqtt_port": 99999}),
        content_type='application/json',
    )
    assert resp.status_code == 422
    result = resp.get_json()
    assert 'details' in result
    assert len(result['details']) >= 2


def test_config_file_endpoint_no_file(tmp_path: Path) -> None:
    """GET /api/config/file returns the in-memory config when no file exists."""
    config = _base_config(tmp_path, device_name="InMemory")
    flask_app = create_app(config)
    client = flask_app.test_client()

    resp = client.get('/api/config/file')
    assert resp.status_code == 200
    assert resp.content_type.startswith('application/json')
    data = resp.get_json()
    assert data['device_name'] == 'InMemory'


def test_config_file_endpoint_after_save(tmp_path: Path) -> None:
    """GET /api/config/file returns the persisted file content after save."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    client.post(
        '/api/config',
        data=json.dumps({"device_name": "Saved"}),
        content_type='application/json',
    )

    resp = client.get('/api/config/file')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['device_name'] == 'Saved'


def test_config_load_from_file(tmp_path: Path) -> None:
    """ServiceConfig.load() reads values from service_config.json."""
    cfg_file = tmp_path / 'service_config.json'
    cfg_file.write_text(json.dumps({
        "device_name": "FromFile",
        "max_snapshots": 42,
        "needle_detection_method": "dark_radial",
    }), encoding='utf-8')

    loaded = ServiceConfig.load(data_dir=tmp_path)
    assert loaded.device_name == 'FromFile'
    assert loaded.max_snapshots == 42
    assert loaded.needle_detection_method == 'dark_radial'


def test_config_env_takes_priority_over_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Env var overrides the value in service_config.json."""
    cfg_file = tmp_path / 'service_config.json'
    cfg_file.write_text(json.dumps({"device_name": "FromFile"}), encoding='utf-8')

    monkeypatch.setenv('DEVICE_NAME', 'FromEnv')
    loaded = ServiceConfig.load(data_dir=tmp_path)
    assert loaded.device_name == 'FromEnv'
    assert 'device_name' in loaded.env_overrides()


def test_config_page_shows_locked_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Config page marks env-locked fields with the lock badge."""
    monkeypatch.setenv('DEVICE_NAME', 'EnvDevice')
    config = ServiceConfig.load(data_dir=tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    resp = client.get('/config')
    assert resp.status_code == 200
    body = resp.data
    # The badge for env-locked fields should appear
    assert b'badge-env' in body or b'REBOOT' in body


# ─────────────────────────────────────────────────────────────────────────────
# Device config API
# ─────────────────────────────────────────────────────────────────────────────

def test_device_page_renders(tmp_path: Path) -> None:
    """GET /device returns a 200 with the device config page."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()
    resp = client.get('/device')
    assert resp.status_code == 200
    assert b'device_cfg' not in resp.data  # template rendered, no raw variable names
    assert b'OTA' in resp.data


def test_device_config_api_get_default(tmp_path: Path) -> None:
    """GET /api/device-config returns defaults when no file exists."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()
    resp = client.get('/api/device-config')
    assert resp.status_code == 200
    data = resp.get_json()
    assert 'ds18_en' in data
    assert 'mqtt_id' in data
    assert data['mqtt_port'] == 1883


def test_device_config_api_save(tmp_path: Path) -> None:
    """POST /api/device-config saves and returns ok."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    payload = {
        'ds18_en': True, 'ds18_int': 15,
        'sht_en': False, 'sht_int': 5,
        'ina_en': False, 'ina_int': 2,
        'ph_hour': 10, 'ph_min': 30, 'ph_win': 5,
        'wifi_ssid': 'TestNet', 'wifi_pass': 's3cr3t', 'upload_ep': 'http://host/upload',
        'ota_en': True, 'ota_url': 'http://host/api/ota/manifest',
        'mqtt_en': False, 'mqtt_host': '', 'mqtt_port': 1883,
        'mqtt_user': '', 'mqtt_pass': '', 'mqtt_id': 'my-esp32',
        'dev_name': 'TestDevice', 'boot_win': 30,
        'ntp_srv1': 'pool.ntp.org', 'ntp_srv2': 'time.google.com',
        'ntp_tz': 'CET-1CEST,M3.5.0,M10.5.0/3',
        'push_to_device': False,
    }
    resp = client.post(
        '/api/device-config',
        data=json.dumps(payload),
        content_type='application/json',
    )
    assert resp.status_code == 200
    result = resp.get_json()
    assert result['ok'] is True
    assert result['published'] is False

    # Verify persisted
    cfg_file = tmp_path / 'device_config.json'
    assert cfg_file.exists()
    saved = json.loads(cfg_file.read_text())
    assert saved['ds18_en'] is True
    assert saved['wifi_ssid'] == 'TestNet'
    assert saved['mqtt_id'] == 'my-esp32'


def test_device_config_api_save_push_no_mqtt(tmp_path: Path) -> None:
    """POST /api/device-config with push_to_device=True and MQTT disabled returns publish_error."""
    config = _base_config(tmp_path, mqtt_enabled=False)
    flask_app = create_app(config)
    client = flask_app.test_client()

    payload = {
        'ds18_en': False, 'ds18_int': 10, 'sht_en': False, 'sht_int': 5,
        'ina_en': False, 'ina_int': 2, 'ph_hour': 14, 'ph_min': 0, 'ph_win': 10,
        'wifi_ssid': '', 'wifi_pass': '', 'upload_ep': '', 'ota_en': False, 'ota_url': '',
        'mqtt_en': False, 'mqtt_host': '', 'mqtt_port': 1883, 'mqtt_user': '',
        'mqtt_pass': '', 'mqtt_id': 'esp32', 'dev_name': 'ESP', 'boot_win': 30,
        'ntp_srv1': 'pool.ntp.org', 'ntp_srv2': 'time.google.com', 'ntp_tz': 'UTC',
        'push_to_device': True,
    }
    resp = client.post(
        '/api/device-config',
        data=json.dumps(payload),
        content_type='application/json',
    )
    assert resp.status_code == 200
    result = resp.get_json()
    assert result['ok'] is True
    assert result['published'] is False
    assert result['publish_error'] is not None  # MQTT disabled → error message


def test_device_config_publish_no_mqtt(tmp_path: Path) -> None:
    """POST /api/device-config/publish with MQTT disabled returns 502 with error."""
    config = _base_config(tmp_path, mqtt_enabled=False)
    flask_app = create_app(config)
    client = flask_app.test_client()
    resp = client.post('/api/device-config/publish')
    assert resp.status_code == 502
    result = resp.get_json()
    assert result['ok'] is False
    assert 'error' in result


def test_mqtt_discover_disabled(tmp_path: Path) -> None:
    """POST /api/mqtt/discover with MQTT disabled returns 400."""
    config = _base_config(tmp_path, mqtt_enabled=False)
    flask_app = create_app(config)
    client = flask_app.test_client()
    resp = client.post('/api/mqtt/discover')
    assert resp.status_code == 400
    result = resp.get_json()
    assert result['ok'] is False
    assert 'error' in result


def test_mqtt_discover_enabled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """POST /api/mqtt/discover with MQTT enabled calls publish_discovery and returns ok."""
    config = _base_config(tmp_path, mqtt_enabled=True)
    flask_app = create_app(config)
    client = flask_app.test_client()

    called = []
    from gauge_service import mqtt as mqtt_module

    def fake_publish_discovery(self: mqtt_module.MqttPublisher) -> None:
        called.append(True)

    monkeypatch.setattr(mqtt_module.MqttPublisher, 'publish_discovery', fake_publish_discovery)
    resp = client.post('/api/mqtt/discover')
    assert resp.status_code == 200
    result = resp.get_json()
    assert result['ok'] is True
    assert called


# ─────────────────────────────────────────────────────────────────────────────
# OTA management API
# ─────────────────────────────────────────────────────────────────────────────

def test_ota_status_default(tmp_path: Path) -> None:
    """GET /api/ota/status returns mode=disabled by default."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()
    resp = client.get('/api/ota/status')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['mode'] == 'disabled'


def test_ota_config_save_and_get(tmp_path: Path) -> None:
    """POST /api/ota/config persists, GET /api/ota/config reads back."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    resp = client.post(
        '/api/ota/config',
        data=json.dumps({'mode': 'service_auto', 'github_repo': 'owner/repo'}),
        content_type='application/json',
    )
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True

    resp = client.get('/api/ota/config')
    assert resp.status_code == 200
    data = resp.get_json()
    assert data['mode'] == 'service_auto'
    assert data['github_repo'] == 'owner/repo'


def test_ota_config_invalid_mode(tmp_path: Path) -> None:
    """POST /api/ota/config with an unknown mode returns 422."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    resp = client.post(
        '/api/ota/config',
        data=json.dumps({'mode': 'invalid_mode'}),
        content_type='application/json',
    )
    assert resp.status_code == 422


def test_ota_manifest_disabled(tmp_path: Path) -> None:
    """GET /api/ota/manifest returns 404 when mode=disabled."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()
    resp = client.get('/api/ota/manifest')
    assert resp.status_code == 404


def test_ota_manifest_service_auto_no_firmware(tmp_path: Path) -> None:
    """GET /api/ota/manifest returns 404 when mode=service_auto but no firmware cached."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    client.post(
        '/api/ota/config',
        data=json.dumps({'mode': 'service_auto', 'github_repo': 'owner/repo'}),
        content_type='application/json',
    )

    resp = client.get('/api/ota/manifest')
    assert resp.status_code == 404


def test_ota_manifest_manual_with_firmware(tmp_path: Path) -> None:
    """GET /api/ota/manifest returns manifest JSON when mode=manual and firmware is uploaded."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    # Switch to manual mode and save
    client.post(
        '/api/ota/config',
        data=json.dumps({'mode': 'manual', 'manual_version': 'v9.9.9', 'manual_notes': 'test fw'}),
        content_type='application/json',
    )

    # Upload a dummy firmware file
    dummy_bin = b'\x00' * 512
    resp = client.post(
        '/api/ota/upload',
        data={
            'firmware': (io.BytesIO(dummy_bin), 'firmware.bin'),
            'version': 'v9.9.9',
            'notes': 'test fw',
        },
        content_type='multipart/form-data',
    )
    assert resp.status_code == 200
    assert resp.get_json()['ok'] is True

    # Now manifest should be available
    resp = client.get('/api/ota/manifest')
    assert resp.status_code == 200
    manifest = resp.get_json()
    assert manifest['version'] == 'v9.9.9'
    assert '/api/ota/firmware' in manifest['url']
    assert manifest['notes'] == 'test fw'


def test_ota_firmware_serve(tmp_path: Path) -> None:
    """GET /api/ota/firmware serves the uploaded binary."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    client.post(
        '/api/ota/config',
        data=json.dumps({'mode': 'manual', 'manual_version': 'v1.0.0'}),
        content_type='application/json',
    )

    dummy_bin = b'\xde\xad\xbe\xef' * 64
    client.post(
        '/api/ota/upload',
        data={
            'firmware': (io.BytesIO(dummy_bin), 'firmware.bin'),
            'version': 'v1.0.0',
        },
        content_type='multipart/form-data',
    )

    resp = client.get('/api/ota/firmware')
    assert resp.status_code == 200
    assert resp.data == dummy_bin


def test_ota_firmware_serve_no_firmware(tmp_path: Path) -> None:
    """GET /api/ota/firmware returns 404 when no firmware is available."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()
    # Default mode=disabled
    resp = client.get('/api/ota/firmware')
    assert resp.status_code == 404


def test_ota_upload_wrong_mode(tmp_path: Path) -> None:
    """POST /api/ota/upload returns 409 when mode is not manual."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    dummy_bin = b'\x00' * 16
    resp = client.post(
        '/api/ota/upload',
        data={'firmware': (io.BytesIO(dummy_bin), 'fw.bin'), 'version': 'v1.0.0'},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 409


def test_ota_fetch_wrong_mode(tmp_path: Path) -> None:
    """POST /api/ota/fetch returns 409 when mode is not service_auto."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()
    resp = client.post('/api/ota/fetch')
    assert resp.status_code == 409


def test_ota_upload_missing_version(tmp_path: Path) -> None:
    """POST /api/ota/upload without version returns 400."""
    config = _base_config(tmp_path)
    flask_app = create_app(config)
    client = flask_app.test_client()

    client.post(
        '/api/ota/config',
        data=json.dumps({'mode': 'manual'}),
        content_type='application/json',
    )

    dummy_bin = b'\x00' * 16
    resp = client.post(
        '/api/ota/upload',
        data={'firmware': (io.BytesIO(dummy_bin), 'fw.bin')},
        content_type='multipart/form-data',
    )
    assert resp.status_code == 400

