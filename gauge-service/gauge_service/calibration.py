"""Gauge calibration data model and utilities.

The calibration is produced by the interactive setup wizard and stored either:
- as the ``GAUGE_CONFIG`` environment variable (JSON string), or
- in ``<DATA_DIR>/calibration.json`` (persisted via POST /api/calibration).

The file-based value always takes precedence over the env var.

JSON format
-----------
{
  "image_size": [width, height],
  "patches": [
    {"x": 12, "y": 8, "w": 96, "h": 96, "template_b64": "<base64 JPEG>"},
    ...   (≥2 required)
  ],
  "circle": {"cx": 320.5, "cy": 240.3, "r": 185.0},
  "ticks":  [
    {"px": 145.0, "py": 481.0, "value": 0.0},
    ...   (≥2 required, unique values)
  ]
}
"""
from __future__ import annotations

import base64
import json
import math
from dataclasses import dataclass, field
from typing import Any


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PatchRegion:
    """A rectangular reference region used for camera-drift compensation."""
    x: int
    y: int
    w: int
    h: int
    # Base-64 encoded JPEG of the reference template (captured at calibration time)
    template_b64: str = ""

    def template_bytes(self) -> bytes | None:
        """Return raw JPEG bytes of the template, or None if not set."""
        if not self.template_b64:
            return None
        return base64.b64decode(self.template_b64)


@dataclass
class CircleParams:
    """Fitted circle of the gauge dial."""
    cx: float
    cy: float
    r: float


@dataclass
class TickMark:
    """A user-defined tick on the gauge scale."""
    px: float   # pixel x on the calibration image
    py: float   # pixel y on the calibration image
    value: float  # physical value at this tick (user-entered)


@dataclass
class GaugeCalibration:
    """Complete calibration data for one gauge."""
    image_w: int
    image_h: int
    patches: list[PatchRegion]
    circle: CircleParams
    ticks: list[TickMark]

    # ------------------------------------------------------------------
    # Geometry helpers
    # ------------------------------------------------------------------

    def tick_angle(self, tick: TickMark) -> float:
        """Return the angle (degrees, math convention: 0°=right, CCW positive)
        from the circle centre to the given tick pixel position."""
        dx = tick.px - self.circle.cx
        dy = self.circle.cy - tick.py  # flip Y: image Y grows downward
        return math.degrees(math.atan2(dy, dx)) % 360.0

    def sorted_ticks_by_angle(self) -> list[tuple[float, float]]:
        """Return [(angle_deg, value), ...] sorted by angle (CCW)."""
        pairs = [(self.tick_angle(t), t.value) for t in self.ticks]
        pairs.sort(key=lambda p: p[0])
        return pairs

    def angle_range(self) -> tuple[float, float]:
        """Return (min_angle, max_angle) across all ticks."""
        angles = [self.tick_angle(t) for t in self.ticks]
        return min(angles), max(angles)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return {
            "image_size": [self.image_w, self.image_h],
            "patches": [
                {"x": p.x, "y": p.y, "w": p.w, "h": p.h, "template_b64": p.template_b64}
                for p in self.patches
            ],
            "circle": {"cx": self.circle.cx, "cy": self.circle.cy, "r": self.circle.r},
            "ticks": [{"px": t.px, "py": t.py, "value": t.value} for t in self.ticks],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), separators=(",", ":"))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GaugeCalibration":
        patches = [
            PatchRegion(
                x=int(p["x"]),
                y=int(p["y"]),
                w=int(p["w"]),
                h=int(p["h"]),
                template_b64=p.get("template_b64", ""),
            )
            for p in data["patches"]
        ]
        circle_d = data["circle"]
        circle = CircleParams(
            cx=float(circle_d["cx"]),
            cy=float(circle_d["cy"]),
            r=float(circle_d["r"]),
        )
        ticks = [
            TickMark(px=float(t["px"]), py=float(t["py"]), value=float(t["value"]))
            for t in data["ticks"]
        ]
        image_size = data["image_size"]
        return cls(
            image_w=int(image_size[0]),
            image_h=int(image_size[1]),
            patches=patches,
            circle=circle,
            ticks=ticks,
        )

    @classmethod
    def from_json(cls, raw: str) -> "GaugeCalibration":
        return cls.from_dict(json.loads(raw))

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def validate(self) -> list[str]:
        errors: list[str] = []
        if len(self.patches) < 2:
            errors.append("At least 2 reference patches are required (got "
                          f"{len(self.patches)}).")
        for i, p in enumerate(self.patches):
            if p.w <= 0 or p.h <= 0:
                errors.append(f"Patch {i} has invalid size ({p.w}×{p.h}).")
            if not p.template_b64:
                errors.append(f"Patch {i} has no template image data.")
        if self.circle.r <= 0:
            errors.append(f"Circle radius must be positive (got {self.circle.r}).")
        if len(self.ticks) < 2:
            errors.append(f"At least 2 tick marks are required (got {len(self.ticks)}).")
        values = [t.value for t in self.ticks]
        if len(set(values)) != len(values):
            errors.append("Tick values must all be distinct.")
        if self.image_w <= 0 or self.image_h <= 0:
            errors.append(f"Image size must be positive (got {self.image_w}×{self.image_h}).")
        return errors


# ---------------------------------------------------------------------------
# Circle fitting (Kasa algebraic method, O(N) via NumPy least-squares)
# ---------------------------------------------------------------------------

def fit_circle(points: list[tuple[float, float]]) -> CircleParams:
    """Fit a circle to N≥3 (x, y) points using the Kasa algebraic method.

    Minimises the algebraic distance  (xi-cx)² + (yi-cy)² - r²  in the
    least-squares sense.  Robust enough for 3–20 hand-clicked perimeter
    points with mild image distortion.
    """
    import numpy as np

    if len(points) < 3:
        raise ValueError("fit_circle requires at least 3 points")

    pts = np.array(points, dtype=np.float64)
    x, y = pts[:, 0], pts[:, 1]

    # Design matrix:  [2x, 2y, 1] · [cx, cy, c]^T = x²+y²
    A = np.column_stack([2 * x, 2 * y, np.ones(len(x))])
    b = x ** 2 + y ** 2
    result, _, _, _ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy, c = result
    r2 = c + cx ** 2 + cy ** 2
    r = float(math.sqrt(max(r2, 0.0)))
    return CircleParams(cx=float(cx), cy=float(cy), r=r)
