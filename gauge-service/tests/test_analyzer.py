from __future__ import annotations

import math
from pathlib import Path

import cv2
import numpy as np

from gauge_service.analyzer import GaugeAnalyzer
from gauge_service.config import ServiceConfig


def _normalize(angle: float) -> float:
    return angle % 360.0


def _generate_gauge(percentage: float, rotation: float = 0.0, crop: bool = False, brightness: float = 1.0) -> bytes:
    image = np.full((720, 720, 3), 255, dtype=np.uint8)
    center = (360, 360)
    radius = 210
    low_angle = _normalize(225 + rotation)
    high_angle = _normalize(315 + rotation)

    cv2.circle(image, center, radius, (30, 30, 30), 4)
    for degree in range(0, 91, 10):
        angle = math.radians(low_angle + degree)
        x1 = int(center[0] + math.cos(angle) * radius * 0.78)
        y1 = int(center[1] - math.sin(angle) * radius * 0.78)
        x2 = int(center[0] + math.cos(angle) * radius * 0.94)
        y2 = int(center[1] - math.sin(angle) * radius * 0.94)
        cv2.line(image, (x1, y1), (x2, y2), (20, 20, 20), 3)

    for label, angle in (("5%", low_angle), ("95%", high_angle)):
        angle_rad = math.radians(angle)
        tx = int(center[0] + math.cos(angle_rad) * radius * 1.02) - 40
        ty = int(center[1] - math.sin(angle_rad) * radius * 1.02) + 14
        cv2.putText(image, label, (tx, ty), cv2.FONT_HERSHEY_SIMPLEX, 0.95, (0, 0, 0), 3, cv2.LINE_AA)

    needle_angle = low_angle + ((percentage - 5.0) / 90.0) * 90.0
    needle_angle_rad = math.radians(needle_angle)
    tip = (
        int(center[0] + math.cos(needle_angle_rad) * radius * 0.72),
        int(center[1] - math.sin(needle_angle_rad) * radius * 0.72),
    )
    cv2.line(image, center, tip, (0, 0, 255), 8)
    cv2.circle(image, center, 12, (40, 40, 40), -1)

    image = np.clip(image.astype(np.float32) * brightness, 0, 255).astype(np.uint8)

    if crop:
        image = image[:, :560]

    ok, encoded = cv2.imencode('.jpg', image)
    assert ok
    return encoded.tobytes()


def test_analyzer_reads_rotated_gauge() -> None:
    config = ServiceConfig(
        data_dir=Path('/tmp/gauge-test-1'),
        analysis_expected_span_deg=90,
        analysis_default_low_angle=225,
        analysis_default_high_angle=315,
    )
    analyzer = GaugeAnalyzer(config)

    result = analyzer.analyze(_generate_gauge(percentage=52, rotation=22, brightness=0.85))

    assert result['confidence'] > 35
    assert result['estimated'] is False
    assert abs(result['percentage'] - 52) < 12


def test_analyzer_estimates_when_frame_is_partial() -> None:
    config = ServiceConfig(
        data_dir=Path('/tmp/gauge-test-2'),
        analysis_expected_span_deg=90,
        analysis_default_low_angle=225,
        analysis_default_high_angle=315,
    )
    analyzer = GaugeAnalyzer(config)

    result = analyzer.analyze(_generate_gauge(percentage=80, rotation=12, crop=True))

    assert result['confidence'] > 10
    assert result['estimated'] is True
    assert 5 <= result['percentage'] <= 95
