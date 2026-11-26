"""
Circular index-finger motion → discrete Windows volume steps.
Clockwise = up, counter-clockwise = down.
"""
from __future__ import annotations

import math
import time
from collections import deque

from actions.hand_gesture.volume_bridge import change_volume

# ~6× more sensitive than a full slow circle (0.14 rad baseline → ~0.023)
ANGLE_STEP = 0.023
MIN_RADIUS = 0.018
JITTER = 0.004
BOOTSTRAP_ROTATION = 0.05
MAX_STEPS_PER_FLUSH = 12
FLUSH_INTERVAL = 0.08


class VolumeController:
    def __init__(self) -> None:
        self._recent: deque[tuple[float, float]] = deque(maxlen=24)
        self._last_flush = 0.0
        self.reset()

    def reset(self) -> None:
        self.center: tuple[float, float] | None = None
        self.prev_angle: float | None = None
        self.accum = 0.0
        self.pending_up = 0
        self.pending_down = 0
        self.total_rotation = 0.0
        self._recent.clear()

    def update(self, x: float, y: float) -> str | None:
        """
        Feed normalized fingertip (mirrored). Returns status hint or None.
        """
        self._recent.append((x, y))

        if self.center is None:
            self.center = (x, y)
        else:
            cx, cy = self.center
            self.center = (cx * 0.82 + x * 0.18, cy * 0.82 + y * 0.18)

        cx, cy = self.center
        dx, dy = x - cx, y - cy
        radius = math.hypot(dx, dy)
        if radius < MIN_RADIUS:
            return None

        angle = math.atan2(dy, dx)
        if self.prev_angle is not None:
            delta = angle - self.prev_angle
            while delta > math.pi:
                delta -= 2 * math.pi
            while delta < -math.pi:
                delta += 2 * math.pi

            if abs(delta) < JITTER:
                self.prev_angle = angle
                return None

            if not self._is_circular_motion():
                self.prev_angle = angle
                return None

            self.accum += delta
            self.total_rotation += abs(delta)

            if self.total_rotation < BOOTSTRAP_ROTATION:
                self.prev_angle = angle
                return "VOLUME (warming up)"

            while self.accum >= ANGLE_STEP:
                self.pending_up += 1
                self.accum -= ANGLE_STEP
            while self.accum <= -ANGLE_STEP:
                self.pending_down += 1
                self.accum += ANGLE_STEP

            self._maybe_flush()

        self.prev_angle = angle
        if self.pending_up or self.pending_down:
            return "VOLUME"
        return "VOLUME (ready)"

    def _is_circular_motion(self) -> bool:
        """Reject mostly straight swipes."""
        if len(self._recent) < 8:
            return True
        pts = list(self._recent)
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        span_x = max(xs) - min(xs)
        span_y = max(ys) - min(ys)
        path = 0.0
        for i in range(1, len(pts)):
            path += math.hypot(pts[i][0] - pts[i - 1][0], pts[i][1] - pts[i - 1][1])
        direct = math.hypot(pts[-1][0] - pts[0][0], pts[-1][1] - pts[0][1])
        if path < 0.02:
            return False
        # Straight line: path ≈ direct; circle: path >> direct
        return path < direct * 2.8 or direct < 0.08

    def _maybe_flush(self) -> None:
        now = time.monotonic()
        if now - self._last_flush < FLUSH_INTERVAL:
            return
        if not self.pending_up and not self.pending_down:
            return

        up = min(self.pending_up, MAX_STEPS_PER_FLUSH)
        down = min(self.pending_down, MAX_STEPS_PER_FLUSH)
        self.pending_up -= up
        self.pending_down -= down

        if up:
            change_volume("up", up)
        if down:
            change_volume("down", down)

        self._last_flush = now

    def on_hand_lost(self) -> None:
        self._maybe_flush()
        self.reset()
