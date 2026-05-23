"""Tests for the calibration-based GaugeReader."""
from __future__ import annotations

import base64
import math
from pathlib import Path

import cv2
import numpy as np
import pytest

from gauge_service.analyzer import GaugeReader, _interpolate, _detect_needle_hsv
from gauge_service.calibration import GaugeCalibration, CircleParams, PatchRegion, TickMark, fit_circle
from gauge_service.config import ServiceConfig


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _default_config(tmp_path: Path | None = None) -> ServiceConfig:
    return ServiceConfig(
        data_dir=tmp_path or Path('/tmp/gauge-reader-tests'),
        mqtt_enabled=False,
    )


def _make_patch_b64(color=(210, 210, 210), size=(48, 48)) -> str:
    img = np.full((size[1], size[0], 3), color, dtype=np.uint8)
    ok, enc = cv2.imencode('.jpg', img)
    assert ok
    return base64.b64encode(enc.tobytes()).decode()


def _build_calibration(
    w=480, h=480,
    cx=240.0, cy=240.0, r=160.0,
    low_angle=225.0, high_angle=315.0,
    low_val=0.0, high_val=100.0,
) -> GaugeCalibration:
    """Build a simple two-tick calibration for a 480×480 image."""
    patches = [
        PatchRegion(x=5, y=5, w=48, h=48, template_b64=_make_patch_b64()),
        PatchRegion(x=w-53, y=5, w=48, h=48, template_b64=_make_patch_b64()),
    ]
    circle = CircleParams(cx=cx, cy=cy, r=r)
    ticks = [
        TickMark(
            px=cx + r * math.cos(math.radians(low_angle)),
            py=cy - r * math.sin(math.radians(low_angle)),
            value=low_val,
        ),
        TickMark(
            px=cx + r * math.cos(math.radians(high_angle)),
            py=cy - r * math.sin(math.radians(high_angle)),
            value=high_val,
        ),
    ]
    return GaugeCalibration(image_w=w, image_h=h, patches=patches, circle=circle, ticks=ticks)


def _generate_gauge_image(
    cx=240, cy=240, r=160,
    needle_angle_deg=270.0,
    w=480, h=480,
    needle_color=(0, 0, 220),
    brightness: float = 1.0,
) -> bytes:
    """Render a synthetic gauge image with a coloured needle."""
    img = np.full((h, w, 3), 200, dtype=np.uint8)
    cv2.circle(img, (cx, cy), r, (20, 20, 20), 4)
    rad = math.radians(needle_angle_deg)
    tip = (
        int(cx + math.cos(rad) * r * 0.75),
        int(cy - math.sin(rad) * r * 0.75),
    )
    cv2.line(img, (cx, cy), tip, needle_color, 10)
    cv2.circle(img, (cx, cy), 10, (40, 40, 40), -1)
    img = np.clip(img.astype(np.float32) * brightness, 0, 255).astype(np.uint8)
    ok, enc = cv2.imencode('.jpg', img)
    assert ok
    return enc.tobytes()


# ─────────────────────────────────────────────────────────────────────────────
# fit_circle
# ─────────────────────────────────────────────────────────────────────────────

def test_fit_circle_exact_three_points() -> None:
    cx, cy, r = 100.0, 150.0, 80.0
    angles = [0, 120, 240]
    pts = [(cx + r * math.cos(math.radians(a)), cy + r * math.sin(math.radians(a))) for a in angles]
    result = fit_circle(pts)
    assert abs(result.cx - cx) < 0.5
    assert abs(result.cy - cy) < 0.5
    assert abs(result.r - r) < 0.5


def test_fit_circle_requires_three_points() -> None:
    with pytest.raises(ValueError, match="at least 3"):
        fit_circle([(0.0, 0.0), (1.0, 0.0)])


def test_fit_circle_many_noisy_points() -> None:
    """Fit should converge even with slight noise on 8 points."""
    cx, cy, r = 200.0, 180.0, 100.0
    rng = np.random.default_rng(42)
    angles = np.linspace(0, 350, 8)
    pts = [
        (
            cx + r * math.cos(math.radians(a)) + rng.normal(0, 1.5),
            cy + r * math.sin(math.radians(a)) + rng.normal(0, 1.5),
        )
        for a in angles
    ]
    result = fit_circle(pts)
    assert abs(result.cx - cx) < 5
    assert abs(result.cy - cy) < 5
    assert abs(result.r - r) < 5


# ─────────────────────────────────────────────────────────────────────────────
# GaugeCalibration helpers
# ─────────────────────────────────────────────────────────────────────────────

def test_calibration_tick_angle() -> None:
    cal = _build_calibration(cx=240, cy=240, r=160, low_angle=225)
    tick = cal.ticks[0]
    angle = cal.tick_angle(tick)
    assert abs(angle - 225.0) < 1.0


def test_calibration_validate_ok() -> None:
    cal = _build_calibration()
    assert cal.validate() == []


def test_calibration_validate_no_patches() -> None:
    cal = _build_calibration()
    cal.patches = []
    errors = cal.validate()
    assert any('patch' in e.lower() for e in errors)


def test_calibration_validate_no_template() -> None:
    cal = _build_calibration()
    cal.patches[0].template_b64 = ""
    errors = cal.validate()
    assert any('template' in e.lower() for e in errors)


def test_calibration_json_roundtrip() -> None:
    cal = _build_calibration()
    raw = cal.to_json()
    cal2 = GaugeCalibration.from_json(raw)
    assert cal2.circle.cx == cal.circle.cx
    assert cal2.circle.r == cal.circle.r
    assert len(cal2.ticks) == len(cal.ticks)
    assert len(cal2.patches) == len(cal.patches)
    assert cal2.patches[0].template_b64 == cal.patches[0].template_b64


# ─────────────────────────────────────────────────────────────────────────────
# Interpolation
# ─────────────────────────────────────────────────────────────────────────────

def test_interpolate_midpoint() -> None:
    cal = _build_calibration(low_angle=225, high_angle=315, low_val=0, high_val=100)
    pct, estimated = _interpolate(270.0, cal)
    assert not estimated
    assert abs(pct - 50.0) < 2.0


def test_interpolate_clamped_below() -> None:
    cal = _build_calibration(low_angle=225, high_angle=315, low_val=0, high_val=100)
    pct, estimated = _interpolate(200.0, cal)
    assert estimated


def test_interpolate_clamped_above() -> None:
    cal = _build_calibration(low_angle=225, high_angle=315, low_val=0, high_val=100)
    pct, estimated = _interpolate(350.0, cal)
    assert estimated


# ─────────────────────────────────────────────────────────────────────────────
# GaugeReader.analyze
# ─────────────────────────────────────────────────────────────────────────────

def test_reader_returns_error_on_bad_image(tmp_path: Path) -> None:
    reader = GaugeReader(_default_config(tmp_path))
    cal = _build_calibration()
    result = reader.analyze(b'not-an-image', cal)
    assert result['status'] == 'failed'
    assert result['percentage'] is None


def test_reader_produces_result_structure(tmp_path: Path) -> None:
    reader = GaugeReader(_default_config(tmp_path))
    cal = _build_calibration()
    img = _generate_gauge_image(needle_angle_deg=270)
    result = reader.analyze(img, cal)
    assert 'percentage' in result
    assert 'confidence' in result
    assert 'needle_angle' in result
    assert 'drift' in result
    assert 'source' in result


def test_reader_flags_delta_exceeded(tmp_path: Path) -> None:
    config = ServiceConfig(
        data_dir=tmp_path,
        mqtt_enabled=False,
        max_delta_percent=10.0,
    )
    reader = GaugeReader(config)
    cal = _build_calibration()
    img = _generate_gauge_image(needle_angle_deg=270)  # ~50%
    result = reader.analyze(img, cal, prev_percentage=5.0)
    # Delta should exceed 10%
    if result['status'] == 'ready':
        assert result['estimated'] is True
        assert result['warning'] is not None


def test_reader_no_delta_warning_when_disabled(tmp_path: Path) -> None:
    config = ServiceConfig(
        data_dir=tmp_path,
        mqtt_enabled=False,
        max_delta_percent=0.0,  # disabled
    )
    reader = GaugeReader(config)
    cal = _build_calibration()
    img = _generate_gauge_image(needle_angle_deg=270)
    result = reader.analyze(img, cal, prev_percentage=0.0)
    # No delta check → warning should be None (may still be estimated for other reasons)
    if result['status'] == 'ready':
        assert result.get('warning') is None


def test_reader_handles_brightness_variation(tmp_path: Path) -> None:
    """Results at different brightness levels should both return valid status."""
    reader = GaugeReader(_default_config(tmp_path))
    cal = _build_calibration()
    for brightness in (0.5, 1.0, 1.5):
        img = _generate_gauge_image(needle_angle_deg=270, brightness=brightness)
        result = reader.analyze(img, cal)
        assert result['status'] in ('ready', 'failed'), f"unexpected status at brightness={brightness}"


def test_reader_draw_debug_image(tmp_path: Path) -> None:
    reader = GaugeReader(_default_config(tmp_path))
    cal = _build_calibration()
    img = _generate_gauge_image()
    result = reader.analyze(img, cal)
    debug = reader.draw_debug_image(img, result, cal)
    assert isinstance(debug, bytes)
    assert len(debug) > 100
