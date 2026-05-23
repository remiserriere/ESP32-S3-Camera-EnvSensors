from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable

import cv2
import numpy as np

from .config import ServiceConfig


@dataclass(slots=True)
class DetectionResult:
    percentage: float
    confidence: float
    estimated: bool
    needle_angle: float
    low_angle: float
    high_angle: float
    circle_x: float
    circle_y: float
    circle_r: float
    source: dict

    def as_dict(self) -> dict:
        return {
            "percentage": round(self.percentage, 2),
            "confidence": round(self.confidence, 2),
            "estimated": self.estimated,
            "needle_angle": round(self.needle_angle, 2),
            "low_angle": round(self.low_angle, 2),
            "high_angle": round(self.high_angle, 2),
            "circle": {
                "x": round(self.circle_x, 2),
                "y": round(self.circle_y, 2),
                "r": round(self.circle_r, 2),
            },
            "source": self.source,
        }


class GaugeAnalyzer:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self.templates = self._build_templates()

    def analyze(self, image_bytes: bytes) -> dict:
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Unable to decode image payload")

        frame = self._resize_if_needed(image)
        circle, circle_confidence, circle_estimated = self._detect_circle(frame)
        needle_angle, needle_confidence, needle_source = self._detect_needle(frame, circle)
        low_angle, high_angle, label_confidence, label_source, estimated = self._detect_scale(frame, circle)

        span = self._choose_span(low_angle, high_angle, needle_angle)
        fraction = np.clip(span["distance_to_needle"] / span["span"], 0.0, 1.0)
        percentage = self.config.analysis_low_percent + fraction * (self.config.analysis_high_percent - self.config.analysis_low_percent)

        confidence = max(5.0, min(99.0, (circle_confidence * 0.25 + needle_confidence * 0.45 + label_confidence * 0.30) * 100.0))
        estimated = estimated or circle_estimated or needle_source != "red_line"

        return DetectionResult(
            percentage=float(np.clip(percentage, self.config.analysis_low_percent, self.config.analysis_high_percent)),
            confidence=confidence,
            estimated=estimated,
            needle_angle=needle_angle,
            low_angle=span["low"],
            high_angle=span["high"],
            circle_x=float(circle[0]),
            circle_y=float(circle[1]),
            circle_r=float(circle[2]),
            source={
                "needle": needle_source,
                "scale": label_source,
                "span_degrees": round(span["span"], 2),
                "circle_confidence": round(circle_confidence, 3),
                "needle_confidence": round(needle_confidence, 3),
                "scale_confidence": round(label_confidence, 3),
            },
        ).as_dict()

    def _resize_if_needed(self, image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        largest = max(height, width)
        if largest <= 1400:
            return image
        scale = 1400.0 / largest
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    def _detect_circle(self, image: np.ndarray) -> tuple[tuple[float, float, float], float, bool]:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray, (9, 9), 2)
        circles = cv2.HoughCircles(
            gray,
            cv2.HOUGH_GRADIENT,
            dp=1.2,
            minDist=min(image.shape[:2]) * 0.2,
            param1=120,
            param2=28,
            minRadius=int(min(image.shape[:2]) * 0.12),
            maxRadius=int(min(image.shape[:2]) * 0.48),
        )
        if circles is not None and len(circles[0]) > 0:
            circle = max(circles[0], key=lambda item: item[2])
            return (float(circle[0]), float(circle[1]), float(circle[2])), 0.92, False

        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            contour = max(contours, key=cv2.contourArea)
            (x, y), radius = cv2.minEnclosingCircle(contour)
            return (float(x), float(y), float(radius)), 0.55, True

        h, w = image.shape[:2]
        return (w / 2.0, h / 2.0, min(h, w) * 0.35), 0.3, True

    def _detect_needle(self, image: np.ndarray, circle: tuple[float, float, float]) -> tuple[float, float, str]:
        cx, cy, radius = circle
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        red_mask = cv2.inRange(hsv, np.array([0, 70, 40]), np.array([15, 255, 255]))
        red_mask |= cv2.inRange(hsv, np.array([165, 70, 40]), np.array([180, 255, 255]))
        red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8))

        candidates = list(self._candidate_lines(red_mask, circle, prefer_mask=True))
        if candidates:
            best = max(candidates, key=lambda item: item[0])
            return best[1], min(best[0], 0.98), "red_line"

        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        inverted = cv2.threshold(gray, 110, 255, cv2.THRESH_BINARY_INV)[1]
        inverted = cv2.bitwise_and(inverted, inverted, mask=self._inner_mask(image.shape[:2], circle, 0.12, 0.95))
        candidates = list(self._candidate_lines(inverted, circle, prefer_mask=False))
        if candidates:
            best = max(candidates, key=lambda item: item[0])
            return best[1], min(best[0], 0.75), "dark_line"

        return self.config.analysis_default_low_angle, 0.15, "fallback"

    def _candidate_lines(self, mask: np.ndarray, circle: tuple[float, float, float], prefer_mask: bool) -> Iterable[tuple[float, float]]:
        cx, cy, radius = circle
        masked = cv2.bitwise_and(mask, mask, mask=self._inner_mask(mask.shape[:2], circle, 0.05, 0.98))
        lines = cv2.HoughLinesP(masked, 1, np.pi / 180.0, threshold=25, minLineLength=int(radius * 0.35), maxLineGap=18)
        if lines is None:
            return []
        results: list[tuple[float, float]] = []
        for raw in lines[:, 0, :]:
            x1, y1, x2, y2 = map(float, raw)
            d1 = math.hypot(x1 - cx, y1 - cy)
            d2 = math.hypot(x2 - cx, y2 - cy)
            near = min(d1, d2)
            far = max(d1, d2)
            if near > radius * 0.35 or far < radius * 0.45:
                continue
            tip_x, tip_y = (x1, y1) if d1 > d2 else (x2, y2)
            angle = self._angle_from_center(cx, cy, tip_x, tip_y)
            length = math.hypot(x2 - x1, y2 - y1)
            score = min(1.0, length / max(radius, 1.0))
            if prefer_mask:
                score += 0.2
            results.append((score, angle))
        return results

    def _detect_scale(self, image: np.ndarray, circle: tuple[float, float, float]) -> tuple[float, float, float, str, bool]:
        candidates = self._detect_label_candidates(image, circle)
        low = next((candidate for candidate in candidates if candidate["kind"] == "low"), None)
        high = next((candidate for candidate in candidates if candidate["kind"] == "high"), None)

        estimated = False
        source = "labels"
        confidence = 0.25
        if low and high:
            confidence = min(0.95, (low["score"] + high["score"]) / 2.0)
            return low["angle"], high["angle"], confidence, source, estimated

        if low and not high:
            high_angle = self._normalize_angle(low["angle"] + self.config.analysis_expected_span_deg)
            return low["angle"], high_angle, min(0.7, low["score"]), "low_label+estimate", True

        if high and not low:
            low_angle = self._normalize_angle(high["angle"] - self.config.analysis_expected_span_deg)
            return low_angle, high["angle"], min(0.7, high["score"]), "high_label+estimate", True

        return (
            self.config.analysis_default_low_angle,
            self.config.analysis_default_high_angle,
            0.2,
            "configured_defaults",
            True,
        )

    def _detect_label_candidates(self, image: np.ndarray, circle: tuple[float, float, float]) -> list[dict]:
        cx, cy, radius = circle
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.equalizeHist(gray)
        thresh = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY_INV, 31, 7)
        ring_mask = self._inner_mask(image.shape[:2], circle, 0.55, 1.05)
        inner_exclusion = self._inner_mask(image.shape[:2], circle, 0.0, 0.45)
        mask = cv2.bitwise_and(thresh, thresh, mask=ring_mask)
        mask[inner_exclusion > 0] = 0
        kernel = np.ones((3, 3), np.uint8)
        mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
        contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        candidates: list[dict] = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            if area < image.shape[0] * image.shape[1] * 0.001 or area > image.shape[0] * image.shape[1] * 0.07:
                continue
            center_x = x + w / 2.0
            center_y = y + h / 2.0
            distance = math.hypot(center_x - cx, center_y - cy)
            if distance < radius * 0.55 or distance > radius * 1.1:
                continue
            patch = gray[max(y - 8, 0): y + h + 8, max(x - 8, 0): x + w + 8]
            kind, score = self._classify_patch(patch)
            if kind is None:
                continue
            candidates.append(
                {
                    "kind": kind,
                    "score": score,
                    "angle": self._angle_from_center(cx, cy, center_x, center_y),
                    "bbox": [int(x), int(y), int(w), int(h)],
                }
            )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        deduped: list[dict] = []
        for candidate in candidates:
            if any(abs(self._angular_distance(candidate["angle"], existing["angle"])) < 8 for existing in deduped if existing["kind"] == candidate["kind"]):
                continue
            deduped.append(candidate)
        return deduped

    def _classify_patch(self, patch: np.ndarray) -> tuple[str | None, float]:
        _, binary = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        best_label = None
        best_score = 0.0
        for rotated in (binary, cv2.rotate(binary, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(binary, cv2.ROTATE_180), cv2.rotate(binary, cv2.ROTATE_90_COUNTERCLOCKWISE)):
            for label, template in self.templates.items():
                resized = cv2.resize(rotated, (template.shape[1], template.shape[0]), interpolation=cv2.INTER_AREA)
                score = float(cv2.matchTemplate(resized, template, cv2.TM_CCOEFF_NORMED).max())
                if score > best_score:
                    best_score = score
                    best_label = label
        if best_label is None or best_score < 0.35:
            return None, best_score
        return ("high" if best_label.startswith("95") else "low"), best_score

    def _build_templates(self) -> dict[str, np.ndarray]:
        templates: dict[str, np.ndarray] = {}
        for label in ("5", "5%", "95", "95%"):
            canvas = np.full((80, 180), 255, dtype=np.uint8)
            cv2.putText(canvas, label, (6, 58), cv2.FONT_HERSHEY_SIMPLEX, 1.8, 0, 4, cv2.LINE_AA)
            _, binary = cv2.threshold(canvas, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
            templates[label] = binary
        return templates

    def _choose_span(self, low_angle: float, high_angle: float, needle_angle: float) -> dict[str, float]:
        cw_span = self._normalize_angle(low_angle - high_angle)
        ccw_span = self._normalize_angle(high_angle - low_angle)
        candidates = [
            {
                "low": low_angle,
                "high": high_angle,
                "span": ccw_span if ccw_span > 0 else 360.0,
                "distance_to_needle": self._normalize_angle(needle_angle - low_angle),
            },
            {
                "low": high_angle,
                "high": low_angle,
                "span": cw_span if cw_span > 0 else 360.0,
                "distance_to_needle": self._normalize_angle(needle_angle - high_angle),
            },
        ]
        for candidate in candidates:
            candidate["contains_needle"] = candidate["distance_to_needle"] <= candidate["span"]
            candidate["span_delta"] = abs(candidate["span"] - self.config.analysis_expected_span_deg)
        candidates.sort(key=lambda item: (not item["contains_needle"], item["span_delta"]))
        return candidates[0]

    def _inner_mask(self, shape: tuple[int, int], circle: tuple[float, float, float], min_ratio: float, max_ratio: float) -> np.ndarray:
        height, width = shape
        cx, cy, radius = circle
        yy, xx = np.indices((height, width))
        distances = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
        return np.where((distances >= radius * min_ratio) & (distances <= radius * max_ratio), 255, 0).astype(np.uint8)

    def _angle_from_center(self, cx: float, cy: float, x: float, y: float) -> float:
        return self._normalize_angle(math.degrees(math.atan2(cy - y, x - cx)))

    def _angular_distance(self, a: float, b: float) -> float:
        diff = abs(self._normalize_angle(a - b))
        return min(diff, 360.0 - diff)

    def _normalize_angle(self, angle: float) -> float:
        return angle % 360.0
