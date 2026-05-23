from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable

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

        corrected = self._apply_mirror_correction(image)
        frame = self._resize_if_needed(corrected)
        circle, circle_confidence, circle_estimated = self._detect_circle(frame)
        needle_angle, needle_confidence, needle_source = self._detect_needle(frame, circle)
        scale = self._detect_scale(frame, circle)

        span = self._choose_span(float(scale["low_angle"]), float(scale["high_angle"]), needle_angle)
        percentage, non_linear = self._estimate_percentage_from_markers(needle_angle, span, list(scale["markers"]))

        label_confidence = float(scale["confidence"])
        confidence = max(5.0, min(99.0, (circle_confidence * 0.25 + needle_confidence * 0.45 + label_confidence * 0.30) * 100.0))
        estimated = bool(scale["estimated"]) or circle_estimated or needle_source != "red_line"
        estimated = estimated or abs(span["span"] - self.config.analysis_expected_span_deg) > max(12.0, self.config.analysis_expected_span_deg * 0.15)

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
                "scale": scale["source"],
                "span_degrees": round(span["span"], 2),
                "circle_confidence": round(circle_confidence, 3),
                "needle_confidence": round(needle_confidence, 3),
                "scale_confidence": round(label_confidence, 3),
                "scale_mode": "non_linear_markers" if non_linear else "linear_fallback",
                "ocr_labels": scale["ocr_labels"],
            },
        ).as_dict()

    def draw_debug_image(self, image_bytes: bytes, analysis: dict) -> bytes:
        """Return JPEG bytes of the corrected+resized image with a full debug overlay."""
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Unable to decode image payload")

        # Apply the same pre-processing as analyze() so overlay coords match exactly.
        corrected = self._apply_mirror_correction(image)
        frame = self._resize_if_needed(corrected)

        cx = float(analysis["circle"]["x"])
        cy = float(analysis["circle"]["y"])
        cr = float(analysis["circle"]["r"])
        needle_angle = float(analysis["needle_angle"])
        low_angle = float(analysis["low_angle"])
        high_angle = float(analysis["high_angle"])
        percentage = float(analysis["percentage"])
        span_deg = float(analysis["source"]["span_degrees"])

        h, w = frame.shape[:2]
        font_scale = max(0.45, min(1.4, w / 900.0))

        def angle_pt(deg: float, r_ratio: float = 1.0) -> tuple[int, int]:
            rad = math.radians(deg)
            return (int(round(cx + math.cos(rad) * cr * r_ratio)),
                    int(round(cy - math.sin(rad) * cr * r_ratio)))

        out = frame.copy()

        # ── Detected circle (blue) ───────────────────────────────────────────
        cv2.circle(out, (int(cx), int(cy)), int(cr), (200, 80, 0), 2)

        # ── Center dot (white) ──────────────────────────────────────────────
        cv2.circle(out, (int(cx), int(cy)), max(5, int(cr * 0.018)), (255, 255, 255), -1)

        # ── Scale arc – detected span (cyan) ────────────────────────────────
        arc_angles = np.linspace(low_angle, low_angle + span_deg, 200)
        arc_pts = np.array([angle_pt(a, 0.82) for a in arc_angles], dtype=np.int32)
        cv2.polylines(out, [arc_pts], False, (220, 200, 0), 3)

        # ── Expected span arc (grey) – for comparison ───────────────────────
        exp_angles = np.linspace(low_angle, low_angle + self.config.analysis_expected_span_deg, 120)
        exp_pts = np.array([angle_pt(a, 0.88) for a in exp_angles], dtype=np.int32)
        cv2.polylines(out, [exp_pts], False, (130, 130, 130), 1)

        # ── Tick marks every 10 % along detected arc ────────────────────────
        for frac in np.linspace(0.0, 1.0, 11):
            tick_angle = low_angle + frac * span_deg
            cv2.line(out, angle_pt(tick_angle, 0.77), angle_pt(tick_angle, 0.86), (180, 180, 0), 2)

        # ── Low angle marker (green) ─────────────────────────────────────────
        cv2.line(out, angle_pt(low_angle, 0.58), angle_pt(low_angle, 0.96), (0, 210, 60), 3)
        lbl_pt = angle_pt(low_angle, 1.10)
        cv2.putText(out, f"L {low_angle:.1f}deg", lbl_pt,
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale * 0.68, (0, 210, 60), 2, cv2.LINE_AA)

        # ── High angle marker (orange-red) ──────────────────────────────────
        cv2.line(out, angle_pt(high_angle, 0.58), angle_pt(high_angle, 0.96), (0, 80, 230), 3)
        lbl_pt = angle_pt(high_angle, 1.10)
        cv2.putText(out, f"H {high_angle:.1f}deg", lbl_pt,
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale * 0.68, (0, 80, 230), 2, cv2.LINE_AA)

        # ── Needle (bright orange) ───────────────────────────────────────────
        needle_w = max(2, int(cr * 0.009))
        cv2.line(out, angle_pt(needle_angle + 180, 0.18), angle_pt(needle_angle, 0.87),
                 (0, 140, 255), needle_w)
        cv2.circle(out, angle_pt(needle_angle, 0.87), max(5, int(cr * 0.022)), (0, 140, 255), -1)
        n_lbl = angle_pt(needle_angle, 1.02)
        cv2.putText(out, f"N {needle_angle:.1f}deg", n_lbl,
                    cv2.FONT_HERSHEY_SIMPLEX, font_scale * 0.68, (0, 140, 255), 2, cv2.LINE_AA)

        # ── Info panel (top-left) ────────────────────────────────────────────
        src = analysis["source"]
        info_lines = [
            f"Reading:    {percentage:.1f}%",
            f"Confidence: {analysis['confidence']:.1f}%  estimated={analysis['estimated']}",
            f"Needle:     {needle_angle:.1f}deg  [{src['needle']}  conf={src['needle_confidence']:.2f}]",
            f"Low:        {low_angle:.1f}deg",
            f"High:       {high_angle:.1f}deg",
            f"Scale src:  {src['scale']}  conf={src['scale_confidence']:.2f}",
            f"Span det.:  {span_deg:.1f}deg   expected={self.config.analysis_expected_span_deg:.0f}deg",
            f"Circle:     ({cx:.0f},{cy:.0f}) r={cr:.0f}  conf={src['circle_confidence']:.2f}",
            f"Mirror:     {self.config.analysis_mirror_mode or 'none'}",
        ]
        line_h = max(22, int(font_scale * 28))
        bx, by = 8, 8
        bw = max(380, int(font_scale * 480))
        bh = len(info_lines) * line_h + 14
        cv2.rectangle(out, (bx, by), (bx + bw, by + bh), (18, 18, 18), -1)
        cv2.rectangle(out, (bx, by), (bx + bw, by + bh), (150, 150, 150), 1)
        for i, line in enumerate(info_lines):
            cv2.putText(out, line, (bx + 8, by + line_h * (i + 1)),
                        cv2.FONT_HERSHEY_SIMPLEX, font_scale * 0.54, (225, 225, 225), 1, cv2.LINE_AA)

        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not ok:
            raise ValueError("Failed to encode debug image")
        return bytes(buf)

    def _apply_mirror_correction(self, image: np.ndarray) -> np.ndarray:
        mode = self.config.analysis_mirror_mode
        if mode in {"none", ""}:
            return image
        if mode in {"horizontal", "h"}:
            return cv2.flip(image, 1)
        if mode in {"vertical", "v"}:
            return cv2.flip(image, 0)
        if mode in {"both", "hv", "vh"}:
            return cv2.flip(image, -1)
        return image

    def _resize_if_needed(self, image: np.ndarray) -> np.ndarray:
        height, width = image.shape[:2]
        largest = max(height, width)
        if largest <= 1400:
            return image
        scale = 1400.0 / largest
        return cv2.resize(image, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)

    def _detect_circle(self, image: np.ndarray) -> tuple[tuple[float, float, float], float, bool]:
        gray_raw = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        gray = cv2.GaussianBlur(gray_raw, (9, 9), 2)
        red_circle = self._detect_red_arc_circle(image)
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
        hough_circle: tuple[float, float, float] | None = None
        if circles is not None and len(circles[0]) > 0:
            circle = max(circles[0], key=lambda item: item[2])
            hough_circle = (float(circle[0]), float(circle[1]), float(circle[2]))

        # Use gray_raw (unblurred) for hub detection: sharper dark regions give a better threshold.
        # Radius: if the red-arc circle's radius greatly exceeds the Hough estimate (>20%), it is
        # likely inflated by the red needle pixels rather than representing the gauge face.
        # In that case use the Hough radius directly; otherwise blend equally.
        # Hub: it IS the needle pivot → give it dominant weight for the center once found.
        if red_circle and hough_circle:
            rx, ry, rr = red_circle
            hx, hy, hr = hough_circle
            blended_r = hr if rr > hr * 1.2 else hr * 0.5 + rr * 0.5
            blended = (hx * 0.35 + rx * 0.65, hy * 0.35 + ry * 0.65, blended_r)
            hub = self._detect_black_hub_center(gray_raw, blended)
            if hub is not None:
                blended = (blended[0] * 0.30 + hub[0] * 0.70, blended[1] * 0.30 + hub[1] * 0.70, blended[2])
            return (float(blended[0]), float(blended[1]), float(blended[2])), 0.95, False
        if red_circle:
            hub = self._detect_black_hub_center(gray_raw, red_circle)
            if hub is not None:
                red_circle = (red_circle[0] * 0.30 + hub[0] * 0.70, red_circle[1] * 0.30 + hub[1] * 0.70, red_circle[2])
            return (float(red_circle[0]), float(red_circle[1]), float(red_circle[2])), 0.9, False
        if hough_circle:
            hub = self._detect_black_hub_center(gray_raw, hough_circle)
            if hub is not None:
                hough_circle = (hough_circle[0] * 0.30 + hub[0] * 0.70, hough_circle[1] * 0.30 + hub[1] * 0.70, hough_circle[2])
            return (float(hough_circle[0]), float(hough_circle[1]), float(hough_circle[2])), 0.86, False

        edges = cv2.Canny(gray, 50, 150)
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        if contours:
            contour = max(contours, key=cv2.contourArea)
            (x, y), radius = cv2.minEnclosingCircle(contour)
            return (float(x), float(y), float(radius)), 0.55, True

        h, w = image.shape[:2]
        return (w / 2.0, h / 2.0, min(h, w) * 0.35), 0.3, True

    def _detect_red_arc_circle(self, image: np.ndarray) -> tuple[float, float, float] | None:
        hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
        red_mask = cv2.inRange(hsv, np.array([0, 60, 50]), np.array([15, 255, 255]))
        red_mask |= cv2.inRange(hsv, np.array([165, 60, 50]), np.array([180, 255, 255]))
        red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8), iterations=1)
        red_mask = cv2.morphologyEx(red_mask, cv2.MORPH_CLOSE, np.ones((5, 5), np.uint8), iterations=2)
        points = np.column_stack(np.where(red_mask > 0))
        if points.shape[0] < 120:
            return None
        pts = np.array([[float(col), float(row)] for row, col in points], dtype=np.float32)
        # Algebraic least-squares circle fit is more robust than minEnclosingCircle:
        # minEnclosingCircle is dominated by the single farthest outlier point (e.g. needle tip),
        # whereas the algebraic fit minimises squared distances across all points.
        # max_r prevents degenerate solutions when red pixels are nearly collinear (e.g. needle only).
        max_r = min(image.shape[:2]) * 0.48
        result = self._fit_circle_algebraic(pts, max_r=max_r)
        if result is not None:
            x, y, r = result
        else:
            (ex, ey), er = cv2.minEnclosingCircle(pts)
            x, y, r = float(ex), float(ey), float(er)
        if r <= min(image.shape[:2]) * 0.1:
            return None
        return float(x), float(y), float(r)

    def _fit_circle_algebraic(self, pts: np.ndarray, max_r: float | None = None) -> tuple[float, float, float] | None:
        """Algebraic least-squares circle fit to 2-D points (columns: x, y).

        Solves the linearised equation  2·cx·x + 2·cy·y + (r²−cx²−cy²) = x²+y²
        in the least-squares sense.  Much less sensitive to outliers than
        minEnclosingCircle which is anchored to the single farthest point.
        Returns None if the fit is degenerate (e.g. nearly collinear points
        yield r > max_r) or numerically invalid.
        """
        n = len(pts)
        if n < 10:
            return None
        # Sample for speed on large point clouds.
        rng = np.random.default_rng(42)
        sample = pts[rng.choice(n, min(n, 800), replace=False)]
        x, y = sample[:, 0], sample[:, 1]
        A = np.column_stack([2.0 * x, 2.0 * y, np.ones(len(sample))])
        b = x ** 2 + y ** 2
        try:
            result, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
        except np.linalg.LinAlgError:
            return None
        cx, cy = float(result[0]), float(result[1])
        r_sq = float(result[2]) + cx * cx + cy * cy
        if r_sq <= 0.0:
            return None
        r = math.sqrt(r_sq)
        if max_r is not None and r > max_r:
            return None
        return cx, cy, r

    def _detect_black_hub_center(self, gray: np.ndarray, circle: tuple[float, float, float]) -> tuple[float, float] | None:
        cx, cy, r = circle
        mask = self._inner_mask(gray.shape[:2], circle, 0.0, 0.33)
        _, dark = cv2.threshold(gray, 95, 255, cv2.THRESH_BINARY_INV)
        dark = cv2.bitwise_and(dark, dark, mask=mask)
        contours, _ = cv2.findContours(dark, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        best: tuple[float, float, float] | None = None
        for contour in contours:
            area = cv2.contourArea(contour)
            if area < max(16.0, r * r * 0.0012) or area > r * r * 0.08:
                continue
            perimeter = cv2.arcLength(contour, True)
            if perimeter <= 0:
                continue
            circularity = 4.0 * math.pi * area / (perimeter * perimeter)
            if circularity < 0.45:
                continue
            moment = cv2.moments(contour)
            if moment["m00"] == 0:
                continue
            x = moment["m10"] / moment["m00"]
            y = moment["m01"] / moment["m00"]
            distance = math.hypot(x - cx, y - cy)
            if distance > r * 0.34:
                continue
            score = circularity - distance / max(1.0, r)
            if best is None or score > best[2]:
                best = (x, y, score)
        if best is None:
            return None
        return float(best[0]), float(best[1])

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

    def _detect_scale(self, image: np.ndarray, circle: tuple[float, float, float]) -> dict[str, Any]:
        candidates = self._detect_label_candidates(image, circle)
        best_by_value: dict[int, dict[str, Any]] = {}
        for candidate in candidates:
            value = int(candidate["value"])
            existing = best_by_value.get(value)
            if existing is None or candidate["score"] > existing["score"]:
                best_by_value[value] = candidate

        low = best_by_value.get(int(self.config.analysis_low_percent))
        high = best_by_value.get(int(self.config.analysis_high_percent))
        ocr_labels = [
            {
                "value": int(item["value"]),
                "score": round(float(item["score"]), 3),
                "angle": round(float(item["angle"]), 2),
                "bbox": item["bbox"],
            }
            for item in sorted(best_by_value.values(), key=lambda i: i["value"])
        ]

        if low and high:
            markers = [
                {"percent": int(item["value"]), "angle": float(item["angle"]), "score": float(item["score"])}
                for item in sorted(best_by_value.values(), key=lambda i: i["value"])
                if 5 <= int(item["value"]) <= 95
            ]
            confidence = min(0.97, (float(low["score"]) + float(high["score"])) / 2.0 + min(0.22, len(markers) * 0.015))
            return {
                "low_angle": float(low["angle"]),
                "high_angle": float(high["angle"]),
                "confidence": confidence,
                "source": "strict_ocr_5_95",
                "estimated": False,
                "markers": markers,
                "ocr_labels": ocr_labels,
            }

        return {
            "low_angle": float(self.config.analysis_default_low_angle),
            "high_angle": float(self.config.analysis_default_high_angle),
            "confidence": 0.15,
            "source": "missing_5_95_configured_defaults",
            "estimated": True,
            "markers": [
                {"percent": float(self.config.analysis_low_percent), "angle": float(self.config.analysis_default_low_angle), "score": 0.1},
                {"percent": float(self.config.analysis_high_percent), "angle": float(self.config.analysis_default_high_angle), "score": 0.1},
            ],
            "ocr_labels": ocr_labels,
        }

    def _detect_tick_endpoints(self, image: np.ndarray, circle: tuple[float, float, float]) -> tuple[float, float, float, str, bool] | None:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        _, dark = cv2.threshold(gray, 175, 255, cv2.THRESH_BINARY_INV)
        angles: list[float] = []
        strengths: list[float] = []
        cx, cy, radius = circle

        for degree in range(360):
            angle = math.radians(degree)
            samples: list[float] = []
            for radial in np.linspace(radius * 0.70, radius * 0.92, 32):
                x = int(round(cx + math.cos(angle) * radial))
                y = int(round(cy - math.sin(angle) * radial))
                if 0 <= x < dark.shape[1] and 0 <= y < dark.shape[0]:
                    samples.append(float(dark[y, x]) / 255.0)
            if samples:
                angles.append(float(degree))
                strengths.append(float(sum(samples) / len(samples)))

        profile = np.array(strengths, dtype=np.float32)
        if profile.size == 0:
            return None

        kernel = np.ones(9, dtype=np.float32) / 9.0
        profile = np.convolve(np.r_[profile[-4:], profile, profile[:4]], kernel, mode="valid")
        threshold = max(0.08, float(np.percentile(profile, 94)))
        strong = np.where(profile >= threshold)[0]
        if len(strong) == 0:
            return None

        clusters = self._cluster_angles(strong.tolist(), profile)
        if len(clusters) >= 2:
            best_pair: tuple[dict, dict] | None = None
            best_score = -1e9
            for idx, first in enumerate(clusters):
                for second in clusters[idx + 1:]:
                    span = min(
                        self._normalize_angle(second["angle"] - first["angle"]),
                        self._normalize_angle(first["angle"] - second["angle"]),
                    )
                    score = (first["strength"] + second["strength"]) - abs(span - self.config.analysis_expected_span_deg) / 180.0
                    if score > best_score:
                        best_score = score
                        best_pair = (first, second)
            if best_pair is not None:
                confidence = min(0.78, 0.45 + (best_pair[0]["strength"] + best_pair[1]["strength"]) / 4.0)
                return best_pair[0]["angle"], best_pair[1]["angle"], confidence, "tick_arc", False

        if len(clusters) == 1:
            angle = clusters[0]["angle"]
            estimated_angle = self._normalize_angle(angle + self.config.analysis_expected_span_deg)
            confidence = min(0.55, 0.25 + clusters[0]["strength"] / 3.0)
            return angle, estimated_angle, confidence, "tick_arc+estimate", True

        return None

    def _cluster_angles(self, strong_angles: list[int], profile: np.ndarray) -> list[dict]:
        if not strong_angles:
            return []
        angles = sorted(set(strong_angles))
        groups: list[list[int]] = [[angles[0]]]
        for angle in angles[1:]:
            if angle - groups[-1][-1] <= 8:
                groups[-1].append(angle)
            else:
                groups.append([angle])
        if len(groups) > 1 and (groups[0][0] + 360) - groups[-1][-1] <= 8:
            groups[0] = groups[-1] + groups[0]
            groups.pop()

        clusters: list[dict] = []
        for group in groups:
            weights = np.array([profile[idx % 360] for idx in group], dtype=np.float32)
            weighted_angle = float(np.average(np.array(group, dtype=np.float32) % 360.0, weights=weights))
            clusters.append(
                {
                    "angle": self._normalize_angle(weighted_angle),
                    "strength": float(weights.mean()) if weights.size else 0.0,
                    "count": len(group),
                }
            )
        return clusters

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
            # Reject degenerate contours that are far too thin to contain printed digits
            # (e.g. edge artifacts from sharp brightness transitions produce tall, thin strips).
            if w < 0.12 * h or h < 0.12 * w:
                continue
            center_x = x + w / 2.0
            center_y = y + h / 2.0
            distance = math.hypot(center_x - cx, center_y - cy)
            if distance < radius * 0.55 or distance > radius * 1.1:
                continue
            patch = gray[max(y - 8, 0): y + h + 8, max(x - 8, 0): x + w + 8]
            value, score = self._classify_patch(patch)
            if value is None:
                continue
            candidates.append(
                {
                    "value": int(value),
                    "score": score,
                    "angle": self._angle_from_center(cx, cy, center_x, center_y),
                    "bbox": [int(x), int(y), int(w), int(h)],
                }
            )
        candidates.sort(key=lambda item: item["score"], reverse=True)
        deduped: list[dict] = []
        for candidate in candidates:
            if any(abs(self._angular_distance(candidate["angle"], existing["angle"])) < 8 for existing in deduped if existing["value"] == candidate["value"]):
                continue
            deduped.append(candidate)
        return deduped

    def _classify_patch(self, patch: np.ndarray) -> tuple[int | None, float]:
        _, binary = cv2.threshold(patch, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
        binary = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        best_label: int | None = None
        best_score = 0.0
        for rotated in (binary, cv2.rotate(binary, cv2.ROTATE_90_CLOCKWISE), cv2.rotate(binary, cv2.ROTATE_180), cv2.rotate(binary, cv2.ROTATE_90_COUNTERCLOCKWISE)):
            for label, templates in self.templates.items():
                for template in templates:
                    resized = cv2.resize(rotated, (template.shape[1], template.shape[0]), interpolation=cv2.INTER_AREA)
                    score = float(cv2.matchTemplate(resized, template, cv2.TM_CCOEFF_NORMED).max())
                    if score > best_score:
                        best_score = score
                        best_label = label
        if best_label is None or best_score < 0.22:
            return None, best_score
        return int(best_label), best_score

    def _build_templates(self) -> dict[int, list[np.ndarray]]:
        templates: dict[int, list[np.ndarray]] = {}
        # Typical printed markings observed on Rochester-style LPG dial gauges.
        labels = [5, 10, 15, 20, 30, 40, 50, 60, 70, 80, 85, 90, 95]
        for value in labels:
            forms = [f"{value}"]
            if value in {5, 95}:
                forms.append(f"{value}%")
            rendered: list[np.ndarray] = []
            for text in forms:
                for scale, thickness in ((1.4, 3), (1.7, 4), (2.0, 4)):
                    canvas = np.full((96, 224), 255, dtype=np.uint8)
                    cv2.putText(canvas, text, (8, 70), cv2.FONT_HERSHEY_SIMPLEX, scale, 0, thickness, cv2.LINE_AA)
                    _, binary = cv2.threshold(canvas, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
                    rendered.append(binary)
            templates[value] = rendered
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

    def _estimate_percentage_from_markers(self, needle_angle: float, span: dict[str, float], markers: list[dict[str, float]]) -> tuple[float, bool]:
        if not markers:
            fraction = np.clip(span["distance_to_needle"] / max(1e-6, span["span"]), 0.0, 1.0)
            value = self.config.analysis_low_percent + fraction * (self.config.analysis_high_percent - self.config.analysis_low_percent)
            return float(value), False

        forward = self._normalize_angle(span["high"] - span["low"])
        reverse = self._normalize_angle(span["low"] - span["high"])
        if abs(forward - span["span"]) <= abs(reverse - span["span"]):
            to_pos = lambda ang: self._normalize_angle(ang - span["low"])
        else:
            to_pos = lambda ang: self._normalize_angle(span["low"] - ang)

        weighted: list[tuple[float, float]] = []
        for marker in markers:
            pos = to_pos(float(marker["angle"]))
            if pos <= span["span"] + 1e-6:
                weighted.append((pos, float(marker["percent"])))
        if len(weighted) < 2:
            fraction = np.clip(span["distance_to_needle"] / max(1e-6, span["span"]), 0.0, 1.0)
            value = self.config.analysis_low_percent + fraction * (self.config.analysis_high_percent - self.config.analysis_low_percent)
            return float(value), False

        weighted = sorted(weighted, key=lambda item: item[0])
        needle_pos = np.clip(span["distance_to_needle"], 0.0, span["span"])

        if needle_pos <= weighted[0][0]:
            return float(weighted[0][1]), True
        for (p0, v0), (p1, v1) in zip(weighted, weighted[1:]):
            if p0 <= needle_pos <= p1:
                if abs(p1 - p0) < 1e-6:
                    return float(v0), True
                ratio = (needle_pos - p0) / (p1 - p0)
                return float(v0 + ratio * (v1 - v0)), True
        return float(weighted[-1][1]), True

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
