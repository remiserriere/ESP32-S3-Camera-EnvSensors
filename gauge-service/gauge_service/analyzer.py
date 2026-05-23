"""Gauge reader based on user-provided calibration.

Algorithm overview
------------------
1. Decode image and resize to calibration image dimensions.
2. Drift compensation: template-match each reference patch, compute mean
   (dx, dy) translation offset and apply it to all calibration coordinates.
3. Needle detection – three methods are run concurrently; the most confident
   result wins:
   a. dark_radial – scores each candidate angle by the mean *darkness*
      (background brightness minus pixel brightness) in the mid-radial zone,
      skipping the central hub and the outer rim.  Best for black/dark needles.
   b. hsv_color   – finds the dominant direction of high-saturation pixels in
      the gauge ROI.  Best for coloured needles (red, blue, …).
   c. radial_sweep – fallback variance-based scan (hub-excluded).
4. Interpolate needle angle against calibrated tick marks (linear between the
   two bracketing ticks; extrapolation clamped to the scale extremes).
5. Optionally check the value against the previous reading (delta limit).
"""
from __future__ import annotations

import base64
import math
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from .calibration import GaugeCalibration, CircleParams, TickMark
from .config import ServiceConfig

# Pixel tolerance for template-match search area (±SEARCH_MARGIN px)
_SEARCH_MARGIN = 60
# Minimum template-match correlation to trust a drift estimate
_MATCH_THRESHOLD = 0.35
# Number of radial samples per candidate angle in each sweep method
_RADIAL_SAMPLES = 50
# Angular resolution for radial sweep (degrees)
_SWEEP_STEP_DEG = 0.5
# Needle colour: look for pixels whose saturation is high (coloured)
# and that stand out from the dial background – tunable.
_MIN_SATURATION = 60   # HSV S channel, 0-255
_MIN_VALUE_HSV = 40    # HSV V channel minimum to ignore near-black corners
# Margin beyond the tick-angle range to sweep (degrees)
_SWEEP_MARGIN_DEG = 10.0
# Radial zone used by dark & sweep methods.
# HUB_SKIP: skip the central hub (large black base). 0.25 = skip inner 25 %
# of radius. Increase if the hub is very large.
_HUB_SKIP_FRAC = 0.25
# OUTER_SKIP: stop before the dial rim/tick marks (typically at 90-95 %).
_OUTER_SKIP_FRAC = 0.88


class GaugeReader:
    """Read a gauge level from an image using a pre-defined calibration."""

    def __init__(self, config: ServiceConfig) -> None:
        self.config = config

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def analyze(
        self,
        image_bytes: bytes,
        calibration: GaugeCalibration,
        prev_percentage: float | None = None,
        needle_method: str | None = None,
    ) -> dict[str, Any]:
        """Analyse *image_bytes* using *calibration*.

        *needle_method* overrides ``config.needle_detection_method`` for this
        call only (useful for the simulation endpoint so the UI can try each
        method without changing the service configuration).

        Returns a dict suitable for storage in the record JSON.  Always
        returns a valid dict; errors are surfaced via the ``status`` field.
        """
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            return _error_result("Unable to decode image payload")

        # Resize to calibration dimensions so template coordinates match
        image = _resize_to(image, calibration.image_w, calibration.image_h)

        # ── 1. Drift + rotation compensation ─────────────────────────────
        M, drift_confidence = self._compute_transform(image, calibration)

        adj_cal = _warp_calibration(calibration, M)

        # ── 2. Needle detection ───────────────────────────────────────────
        effective_method = needle_method or self.config.needle_detection_method
        circle = adj_cal.circle
        needle_angle, needle_confidence, needle_source = self._detect_needle(
            image, adj_cal, method=effective_method
        )

        # ── 3. Interpolate value ──────────────────────────────────────────
        percentage, estimated = _interpolate(needle_angle, adj_cal)

        # ── 4. Confidence score ───────────────────────────────────────────
        confidence = max(5.0, min(99.0, needle_confidence * 100.0))
        if drift_confidence < _MATCH_THRESHOLD:
            estimated = True
            confidence = max(5.0, confidence * 0.7)

        # ── 5. Delta limit check ──────────────────────────────────────────
        warning: str | None = None
        if (
            prev_percentage is not None
            and self.config.max_delta_percent > 0
            and abs(percentage - prev_percentage) > self.config.max_delta_percent
        ):
            warning = (
                f"delta_exceeded: {abs(percentage - prev_percentage):.1f}% > "
                f"{self.config.max_delta_percent:.1f}%"
            )
            estimated = True

        return {
            "percentage": round(float(np.clip(percentage, -9999, 9999)), 2),
            "confidence": round(confidence, 2),
            "estimated": estimated,
            "needle_angle": round(needle_angle, 2),
            "drift": {
                "dx": round(float(M[0, 2]), 2),
                "dy": round(float(M[1, 2]), 2),
                "rotation_deg": round(math.degrees(math.atan2(float(M[1, 0]), float(M[0, 0]))), 3),
                "matrix": M.tolist(),
            },
            "source": needle_source,
            "warning": warning,
            "status": "ready",
        }

    def draw_debug_image(
        self, image_bytes: bytes, result: dict[str, Any], calibration: GaugeCalibration
    ) -> bytes:
        """Return JPEG bytes of the image with a calibration + analysis overlay."""
        image = cv2.imdecode(np.frombuffer(image_bytes, dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError("Unable to decode image payload")
        image = _resize_to(image, calibration.image_w, calibration.image_h)

        drift = result.get("drift", {})
        dx = float(drift.get("dx", 0.0))
        dy = float(drift.get("dy", 0.0))
        rotation_deg_disp = float(drift.get("rotation_deg", 0.0))
        matrix = drift.get("matrix")
        if matrix is not None:
            adj_cal = _warp_calibration(calibration, np.array(matrix, dtype=np.float64))
        else:
            adj_cal = _shift_calibration(calibration, dx, dy)

        out = image.copy()
        cx = int(round(adj_cal.circle.cx))
        cy = int(round(adj_cal.circle.cy))
        cr = int(round(adj_cal.circle.r))
        h, w = out.shape[:2]
        font = cv2.FONT_HERSHEY_SIMPLEX
        fs = max(0.45, min(1.3, w / 900.0))

        # Reference patches – draw as rotated rectangles when a matrix is available
        M_draw = np.array(matrix, dtype=np.float64) if matrix is not None else None
        for p in calibration.patches:
            # The 4 corners of the original (calibration) patch rectangle
            corners = np.array([
                [p.x,        p.y       ],
                [p.x + p.w,  p.y       ],
                [p.x + p.w,  p.y + p.h ],
                [p.x,        p.y + p.h ],
            ], dtype=np.float64)
            if M_draw is not None:
                # Apply affine transform to each corner: [x', y'] = M * [x, y, 1]^T
                ones = np.ones((4, 1), dtype=np.float64)
                pts_h = np.hstack([corners, ones])  # (4, 3)
                transformed = (M_draw @ pts_h.T).T   # (4, 2)
            else:
                transformed = corners
            pts_int = transformed.round().astype(np.int32).reshape((-1, 1, 2))
            cv2.polylines(out, [pts_int], isClosed=True, color=(220, 120, 0), thickness=2)

        # Gauge circle
        cv2.circle(out, (cx, cy), cr, (30, 200, 30), 2)
        cv2.circle(out, (cx, cy), max(4, cr // 20), (255, 255, 255), -1)

        # Tick marks and labels
        for tick in adj_cal.ticks:
            angle_rad = math.radians(adj_cal.tick_angle(tick))
            tx1 = int(cx + math.cos(angle_rad) * cr * 0.75)
            ty1 = int(cy - math.sin(angle_rad) * cr * 0.75)
            tx2 = int(cx + math.cos(angle_rad) * cr * 0.95)
            ty2 = int(cy - math.sin(angle_rad) * cr * 0.95)
            cv2.line(out, (tx1, ty1), (tx2, ty2), (0, 255, 255), 2)
            lx = int(cx + math.cos(angle_rad) * cr * 1.08)
            ly = int(cy - math.sin(angle_rad) * cr * 1.08)
            cv2.putText(out, str(tick.value), (lx - 12, ly + 5),
                        font, fs * 0.6, (0, 255, 255), 1, cv2.LINE_AA)

        # Needle line
        needle_angle_deg = float(result.get("needle_angle", 0.0))
        na_rad = math.radians(needle_angle_deg)
        nx = int(cx + math.cos(na_rad) * cr * 0.85)
        ny = int(cy - math.sin(na_rad) * cr * 0.85)
        cv2.line(out, (cx, cy), (nx, ny), (0, 80, 255), max(2, cr // 40))
        cv2.circle(out, (nx, ny), max(4, cr // 30), (0, 80, 255), -1)

        # Info box
        pct = result.get("percentage", "?")
        conf = result.get("confidence", "?")
        drift_info = f"drift tx={dx:+.1f} ty={dy:+.1f} rot={rotation_deg_disp:+.2f}\u00b0"
        warning = result.get("warning") or ""
        lines = [
            f"Reading:    {pct}%  conf={conf}%",
            f"Needle:     {needle_angle_deg:.1f}° [{result.get('source','')}]",
            f"Drift:      {drift_info}",
            f"Estimated:  {result.get('estimated', '?')}",
        ]
        if warning:
            lines.append(f"Warning:    {warning}")
        lh = max(22, int(fs * 28))
        bx, by = 8, 8
        bw = max(340, int(fs * 420))
        bh = len(lines) * lh + 14
        cv2.rectangle(out, (bx, by), (bx + bw, by + bh), (15, 15, 15), -1)
        cv2.rectangle(out, (bx, by), (bx + bw, by + bh), (140, 140, 140), 1)
        for i, line in enumerate(lines):
            cv2.putText(out, line, (bx + 8, by + lh * (i + 1)),
                        font, fs * 0.52, (220, 220, 220), 1, cv2.LINE_AA)

        ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 88])
        if not ok:
            raise ValueError("Failed to encode debug image")
        return bytes(buf)

    # ------------------------------------------------------------------
    # Internal: drift compensation
    # ------------------------------------------------------------------

    def _compute_transform(
        self, image: np.ndarray, cal: GaugeCalibration
    ) -> tuple[np.ndarray, float]:
        """Return (M_2x3, confidence) affine transform from calibration to current image.

        *M* is a 2×3 NumPy matrix mapping calibration pixel coordinates to
        current-image pixel coordinates.  It handles translation **and**
        in-plane rotation/scale caused by a camera tilt.

        Strategy
        --------
        - 0 good matches  → identity transform (no correction).
        - 1 good match    → pure translation (rotation cannot be estimated
          from a single correspondence).
        - ≥2 good matches → RANSAC partial-affine via
          ``cv2.estimateAffinePartial2D`` (translation + rotation + isotropic
          scale, 4 DOF).  Falls back to mean translation if the estimator
          returns ``None``.
        """
        h, w = image.shape[:2]
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

        src_pts: list[tuple[float, float]] = []
        dst_pts: list[tuple[float, float]] = []
        confidences: list[float] = []

        for patch in cal.patches:
            template_bytes = patch.template_bytes()
            if template_bytes is None:
                continue
            tmpl_arr = cv2.imdecode(
                np.frombuffer(template_bytes, dtype=np.uint8), cv2.IMREAD_GRAYSCALE
            )
            if tmpl_arr is None:
                continue

            th, tw = tmpl_arr.shape[:2]

            # Search area: patch expected position ± SEARCH_MARGIN
            sx1 = max(0, patch.x - _SEARCH_MARGIN)
            sy1 = max(0, patch.y - _SEARCH_MARGIN)
            sx2 = min(w, patch.x + patch.w + _SEARCH_MARGIN)
            sy2 = min(h, patch.y + patch.h + _SEARCH_MARGIN)

            # Ensure the search region is large enough to contain the template
            if (sx2 - sx1) < tw or (sy2 - sy1) < th:
                continue

            roi = gray[sy1:sy2, sx1:sx2]
            match_result = cv2.matchTemplate(roi, tmpl_arr, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(match_result)

            confidences.append(max_val)
            if max_val < _MATCH_THRESHOLD:
                continue

            # Use the top-left corner of the matched template as the correspondence point
            src_pts.append((float(patch.x), float(patch.y)))
            dst_pts.append((float(sx1 + max_loc[0]), float(sy1 + max_loc[1])))

        avg_conf = float(np.mean(confidences)) if confidences else 0.0
        _identity = np.array([[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]], dtype=np.float64)

        if not src_pts:
            return _identity, avg_conf

        if len(src_pts) == 1:
            # Only one patch matched: estimate translation only
            dx = dst_pts[0][0] - src_pts[0][0]
            dy = dst_pts[0][1] - src_pts[0][1]
            M = np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float64)
            return M, avg_conf

        # ≥2 patches: estimate rotation + isotropic scale + translation (4 DOF)
        src_arr = np.array(src_pts, dtype=np.float32)
        dst_arr = np.array(dst_pts, dtype=np.float32)
        M_est, _ = cv2.estimateAffinePartial2D(src_arr, dst_arr, method=cv2.RANSAC)
        if M_est is None:
            # Estimator failed: fall back to mean translation
            dx = float(np.mean([d[0] - s[0] for s, d in zip(src_pts, dst_pts)]))
            dy = float(np.mean([d[1] - s[1] for s, d in zip(src_pts, dst_pts)]))
            return np.array([[1.0, 0.0, dx], [0.0, 1.0, dy]], dtype=np.float64), avg_conf

        return M_est.astype(np.float64), avg_conf

    # ------------------------------------------------------------------
    # Internal: needle detection
    # ------------------------------------------------------------------

    def _detect_needle(
        self, image: np.ndarray, cal: GaugeCalibration, method: str = "auto"
    ) -> tuple[float, float, str]:
        """Return (needle_angle_deg, confidence_0_1, source_str).

        *method* controls which detection strategy is used:
        - ``"auto"``         – run all three; return the most confident result.
        - ``"dark_radial"``  – darkness-score radial sweep only.
        - ``"hsv_color"``    – HSV saturation sweep only.
        - ``"radial_sweep"`` – variance-based radial sweep only.
        """
        circle = cal.circle
        cx, cy, cr = circle.cx, circle.cy, circle.r

        # Compute tick angle range + margin
        angles_vals = cal.sorted_ticks_by_angle()
        if not angles_vals:
            return 0.0, 0.0, "no_ticks"
        a_min = angles_vals[0][0] - _SWEEP_MARGIN_DEG
        a_max = angles_vals[-1][0] + _SWEEP_MARGIN_DEG

        if method == "dark_radial":
            angle, conf = _detect_needle_dark(image, cx, cy, cr, a_min, a_max)
            return angle, conf, "dark_radial"

        if method == "hsv_color":
            angle, conf = _detect_needle_hsv(image, cx, cy, cr, a_min, a_max)
            return angle, conf, "hsv_color"

        if method == "radial_sweep":
            angle, conf = _detect_needle_radial_sweep(image, cx, cy, cr, a_min, a_max)
            return angle, conf, "radial_sweep"

        # ── auto: run all three, pick the most confident ──────────────────
        # ── a. Dark-needle radial method (primary for black needles) ──────
        angle_dark, conf_dark = _detect_needle_dark(image, cx, cy, cr, a_min, a_max)

        # ── b. HSV colour method (primary for coloured needles) ───────────
        angle_hsv, conf_hsv = _detect_needle_hsv(image, cx, cy, cr, a_min, a_max)

        # ── c. Radial sweep (variance-based fallback) ─────────────────────
        angle_sweep, conf_sweep = _detect_needle_radial_sweep(
            image, cx, cy, cr, a_min, a_max
        )

        # Pick the method with the highest confidence
        candidates = [
            (conf_dark,  angle_dark,  "dark_radial"),
            (conf_hsv,   angle_hsv,   "hsv_color"),
            (conf_sweep, angle_sweep, "radial_sweep"),
        ]
        best_conf, best_angle, best_source = max(candidates, key=lambda t: t[0])
        return best_angle, best_conf, best_source


# ---------------------------------------------------------------------------
# Standalone helpers (module-level so they can be tested independently)
# ---------------------------------------------------------------------------

def _resize_to(image: np.ndarray, target_w: int, target_h: int) -> np.ndarray:
    h, w = image.shape[:2]
    if w == target_w and h == target_h:
        return image
    return cv2.resize(image, (target_w, target_h), interpolation=cv2.INTER_LINEAR)


def _shift_calibration(cal: GaugeCalibration, dx: float, dy: float) -> GaugeCalibration:
    """Return a copy of *cal* with all pixel coordinates shifted by (dx, dy)."""
    from .calibration import CircleParams, TickMark, PatchRegion

    new_circle = CircleParams(
        cx=cal.circle.cx + dx,
        cy=cal.circle.cy + dy,
        r=cal.circle.r,
    )
    new_ticks = [
        TickMark(px=t.px + dx, py=t.py + dy, value=t.value)
        for t in cal.ticks
    ]
    # Patches themselves are also shifted for the debug overlay, but we keep
    # template_b64 unchanged (it still matches to original template).
    new_patches = [
        PatchRegion(
            x=int(round(p.x + dx)),
            y=int(round(p.y + dy)),
            w=p.w,
            h=p.h,
            template_b64=p.template_b64,
        )
        for p in cal.patches
    ]
    from .calibration import GaugeCalibration as GC
    return GC(
        image_w=cal.image_w,
        image_h=cal.image_h,
        patches=new_patches,
        circle=new_circle,
        ticks=new_ticks,
    )


def _warp_calibration(cal: GaugeCalibration, M: np.ndarray) -> GaugeCalibration:
    """Return a copy of *cal* with all pixel coordinates transformed by the
    2×3 affine matrix *M*.

    Unlike :func:`_shift_calibration`, this handles not only translation but
    also in-plane rotation and isotropic scale (all four DOF estimated by
    ``cv2.estimateAffinePartial2D``).  The circle radius is left unchanged
    because a small camera tilt does not materially change the apparent radius.
    """
    from .calibration import CircleParams, TickMark, PatchRegion
    from .calibration import GaugeCalibration as GC

    def _pt(x: float, y: float) -> tuple[float, float]:
        nx = M[0, 0] * x + M[0, 1] * y + M[0, 2]
        ny = M[1, 0] * x + M[1, 1] * y + M[1, 2]
        return float(nx), float(ny)

    ncx, ncy = _pt(cal.circle.cx, cal.circle.cy)
    new_circle = CircleParams(cx=ncx, cy=ncy, r=cal.circle.r)

    new_ticks = []
    for t in cal.ticks:
        nx, ny = _pt(t.px, t.py)
        new_ticks.append(TickMark(px=nx, py=ny, value=t.value))

    new_patches = []
    for p in cal.patches:
        nx, ny = _pt(float(p.x), float(p.y))
        new_patches.append(PatchRegion(
            x=int(round(nx)),
            y=int(round(ny)),
            w=p.w,
            h=p.h,
            template_b64=p.template_b64,
        ))

    return GC(
        image_w=cal.image_w,
        image_h=cal.image_h,
        patches=new_patches,
        circle=new_circle,
        ticks=new_ticks,
    )


def _detect_needle_dark(
    image: np.ndarray,
    cx: float, cy: float, cr: float,
    a_min: float, a_max: float,
) -> tuple[float, float]:
    """Detect a DARK (e.g. black) needle on a lighter dial background.

    Strategy
    --------
    For each candidate angle, sample *_RADIAL_SAMPLES* pixels along the
    radius in the annular zone [HUB_SKIP, OUTER_SKIP].  The score is the
    *mean darkness* of those pixels relative to the dial background, i.e.
    how much darker the radial path is compared to the bright dial face.

    Skipping the central hub is critical when the needle has a large black
    base: without this exclusion every radial direction would score high
    because the hub boundary crosses all angles.

    CLAHE normalisation is applied first to reduce sensitivity to absolute
    brightness (lighting variation between shots).

    Returns (angle_deg, confidence_0_1).
    """
    h_img, w_img = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Local contrast normalisation (compensates scene brightness variation)
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    icx = int(round(cx))
    icy = int(round(cy))

    # Dial background brightness: 75th percentile of the annular mid-zone
    # (not the hub, not the outer ticks).  Needle pixels will be much darker.
    bg_mask = np.zeros((h_img, w_img), dtype=np.uint8)
    cv2.circle(bg_mask, (icx, icy), int(round(cr * _OUTER_SKIP_FRAC)), 255, -1)
    cv2.circle(bg_mask, (icx, icy), int(round(cr * _HUB_SKIP_FRAC)), 0, -1)
    bg_pixels = gray[bg_mask > 0]
    # Use 75th percentile as "bright dial" reference; needle pixels should be well below
    bg_bright = float(np.percentile(bg_pixels, 75)) if len(bg_pixels) > 0 else 200.0

    # Radial samples: from just outside the hub to just inside the rim
    r_fracs = np.linspace(_HUB_SKIP_FRAC + 0.02, _OUTER_SKIP_FRAC, _RADIAL_SAMPLES)

    candidate_angles = np.arange(a_min, a_max + _SWEEP_STEP_DEG, _SWEEP_STEP_DEG)
    scores = np.zeros(len(candidate_angles), dtype=np.float32)

    for i, angle_deg in enumerate(candidate_angles):
        rad = math.radians(angle_deg)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        xs = np.clip(cx + r_fracs * cr * cos_a, 0, w_img - 1).astype(np.int32)
        ys = np.clip(cy - r_fracs * cr * sin_a, 0, h_img - 1).astype(np.int32)
        vals = gray[ys, xs].astype(np.float32)

        # How much darker than the bright dial background?
        # Only count negative deviations (pixels darker than background).
        darkness = np.maximum(0.0, bg_bright - vals)
        scores[i] = float(np.mean(darkness))

    if len(scores) == 0:
        return (a_min + a_max) / 2.0, 0.0

    # Smooth across angles: suppresses single-pixel spikes from tick marks / numbers
    kernel_size = max(3, int(len(scores) // 15) | 1)
    smoothed = cv2.GaussianBlur(scores.reshape(1, -1), (1, kernel_size), 0).flatten()

    best_idx = int(np.argmax(smoothed))
    best_angle = float(candidate_angles[best_idx])

    # Confidence: how much the needle direction stands out above the noise floor
    peak = float(smoothed[best_idx])
    baseline = float(np.percentile(smoothed, 25))  # lower quartile = "no needle" baseline
    if peak < 1.0 or baseline < 0.5:
        confidence = 0.0
    else:
        snr = peak / baseline
        # snr=1 → 0 confidence; snr=4 → 1.0 confidence (capped at 0.92)
        confidence = min(0.92, max(0.0, (snr - 1.0) / 3.0))

    return best_angle, confidence


def _detect_needle_hsv(
    image: np.ndarray,
    cx: float, cy: float, cr: float,
    a_min: float, a_max: float,
) -> tuple[float, float]:
    """Detect needle angle using HSV colour in the circular ROI.

    Works best for coloured (e.g. red) needles.  Returns (angle_deg, confidence).
    """
    h_img, w_img = image.shape[:2]
    # Build circular mask
    mask = np.zeros((h_img, w_img), dtype=np.uint8)
    cv2.circle(mask, (int(round(cx)), int(round(cy))), int(round(cr * 0.92)), 255, -1)

    # Convert to HSV for brightness-invariant colour selection
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)

    # Find "coloured" pixels (high saturation, moderate-to-high value)
    s_ch = hsv[:, :, 1]
    v_ch = hsv[:, :, 2]
    colored_mask = (
        (s_ch.astype(np.int32) >= _MIN_SATURATION) &
        (v_ch.astype(np.int32) >= _MIN_VALUE_HSV)
    ).astype(np.uint8) * 255
    combined = cv2.bitwise_and(colored_mask, mask)

    # Find the dominant hue angle of coloured pixels inside the circle
    if cv2.countNonZero(combined) < 10:
        return 0.0, 0.0

    # For each coloured pixel, compute angle from circle centre
    ys, xs = np.where(combined > 0)
    # Angle in image coords: math convention (0=right, CCW)
    dx_arr = xs.astype(np.float32) - cx
    dy_arr = cy - ys.astype(np.float32)  # flip Y
    angles = np.degrees(np.arctan2(dy_arr, dx_arr)) % 360.0

    # Filter to calibrated angular range
    in_range = (angles >= a_min) & (angles <= a_max)
    if in_range.sum() < 5:
        return 0.0, 0.0

    angles_filt = angles[in_range]
    # Weighted by saturation of coloured pixels (more saturated = more needle-like)
    weights = s_ch[ys[in_range], xs[in_range]].astype(np.float32)
    if weights.sum() == 0:
        return 0.0, 0.0

    # Circular mean
    sin_sum = float(np.sum(np.sin(np.radians(angles_filt)) * weights))
    cos_sum = float(np.sum(np.cos(np.radians(angles_filt)) * weights))
    mean_angle = math.degrees(math.atan2(sin_sum, cos_sum)) % 360.0

    # Confidence: ratio of in-range coloured pixels vs all coloured pixels in circle
    total_in_circle = cv2.countNonZero(cv2.bitwise_and(colored_mask, mask))
    ratio = float(in_range.sum()) / max(1, total_in_circle)
    confidence = min(0.95, ratio * 1.5)  # scale up a bit
    return mean_angle, confidence


def _detect_needle_radial_sweep(
    image: np.ndarray,
    cx: float, cy: float, cr: float,
    a_min: float, a_max: float,
) -> tuple[float, float]:
    """Detect needle angle by radial contrast sweep (variance-based fallback).

    For each candidate angle, sample *_RADIAL_SAMPLES* pixels from the hub
    boundary outward (hub excluded) and compute a contrast score (variance).
    The angle with the highest score is taken as the needle direction.
    Brightness-normalised using CLAHE before sampling.
    Returns (angle_deg, confidence_0_1).
    """
    h_img, w_img = image.shape[:2]
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)

    # Apply local contrast normalisation to reduce lighting variation impact
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    gray = clahe.apply(gray)

    candidate_angles = np.arange(a_min, a_max + _SWEEP_STEP_DEG, _SWEEP_STEP_DEG)
    scores: list[float] = []

    # Skip the hub region (same bounds as dark method for consistency)
    r_fracs = np.linspace(_HUB_SKIP_FRAC + 0.02, _OUTER_SKIP_FRAC, _RADIAL_SAMPLES)

    for angle_deg in candidate_angles:
        rad = math.radians(angle_deg)
        cos_a = math.cos(rad)
        sin_a = math.sin(rad)

        xs = np.clip(cx + r_fracs * cr * cos_a, 0, w_img - 1).astype(np.int32)
        ys = np.clip(cy - r_fracs * cr * sin_a, 0, h_img - 1).astype(np.int32)
        vals = gray[ys, xs].astype(np.float32)

        # Score = variance (any high-contrast line scores well)
        scores.append(float(np.var(vals)))

    if not scores:
        return (a_min + a_max) / 2.0, 0.0

    scores_arr = np.array(scores, dtype=np.float32)
    # Smooth to remove noise
    kernel_size = max(3, int(len(scores) // 20) | 1)
    smoothed = cv2.GaussianBlur(scores_arr.reshape(1, -1), (1, kernel_size), 0).flatten()

    best_idx = int(np.argmax(smoothed))
    best_angle = float(candidate_angles[best_idx])

    # Confidence: how much does the peak stand out?
    peak = float(smoothed[best_idx])
    median = float(np.median(smoothed))
    if median < 1e-6:
        confidence = 0.0
    else:
        snr = peak / median
        confidence = min(0.90, max(0.0, (snr - 1.0) / 4.0))

    return best_angle, confidence


def _interpolate(
    needle_angle: float, cal: GaugeCalibration
) -> tuple[float, bool]:
    """Linearly interpolate needle angle against calibrated tick marks.

    Returns (percentage, estimated).  *estimated* is True when the needle
    is outside the calibrated scale range (extrapolation).
    """
    pairs = cal.sorted_ticks_by_angle()  # [(angle, value), ...]
    if len(pairs) < 2:
        return 0.0, True

    # If outside range, extrapolate from the nearest two ticks but flag as estimated
    if needle_angle <= pairs[0][0]:
        a0, v0 = pairs[0]
        a1, v1 = pairs[1]
        estimated = True
    elif needle_angle >= pairs[-1][0]:
        a0, v0 = pairs[-2]
        a1, v1 = pairs[-1]
        estimated = True
    else:
        estimated = False
        # Find bracketing ticks
        for i in range(len(pairs) - 1):
            if pairs[i][0] <= needle_angle <= pairs[i + 1][0]:
                a0, v0 = pairs[i]
                a1, v1 = pairs[i + 1]
                break
        else:
            a0, v0 = pairs[-2]
            a1, v1 = pairs[-1]

    span = a1 - a0
    if abs(span) < 1e-6:
        return float(v0), True

    frac = (needle_angle - a0) / span
    value = v0 + frac * (v1 - v0)
    return float(value), estimated


def _error_result(msg: str) -> dict[str, Any]:
    return {
        "percentage": None,
        "confidence": 0.0,
        "estimated": True,
        "needle_angle": None,
        "drift": {"dx": 0.0, "dy": 0.0, "rotation_deg": 0.0, "matrix": [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0]]},
        "source": "error",
        "warning": msg,
        "status": "failed",
    }
