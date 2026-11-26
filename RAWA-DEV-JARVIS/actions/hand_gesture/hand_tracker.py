"""
Webcam hand tracking with MediaPipe Tasks HandLandmarker.
Detects volume circle, open-palm show, pinch (reserved).
"""
from __future__ import annotations

import math
import urllib.request
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

import cv2
import numpy as np

try:
    import mediapipe as mp
    from mediapipe.tasks.python import BaseOptions
    from mediapipe.tasks.python import vision as mp_vision
except ImportError:
    mp = None  # type: ignore
    BaseOptions = None  # type: ignore
    mp_vision = None  # type: ignore

# Landmark indices (same as classic MediaPipe Hands)
WRIST = 0
THUMB_TIP = 4
INDEX_TIP = 8
INDEX_PIP = 6
INDEX_MCP = 5
MIDDLE_TIP = 12
MIDDLE_MCP = 9
RING_TIP = 16
PINKY_TIP = 20

_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task"
)
_MODEL_DIR = Path(__file__).resolve().parent / "models"
_MODEL_PATH = _MODEL_DIR / "hand_landmarker.task"


class GestureMode(str, Enum):
    IDLE = "idle"
    VOLUME = "volume"
    SHOW = "show"
    PINCH = "pinch"


@dataclass
class HandState:
    index_tip: tuple[float, float] | None = None
    thumb_tip: tuple[float, float] | None = None
    wrist: tuple[float, float] | None = None
    mode: GestureMode = GestureMode.IDLE
    hand_count: int = 0
    label: str = "NO HAND"
    trail: list[tuple[int, int]] = field(default_factory=list)
    landmarks_px: list[list[tuple[int, int]]] = field(default_factory=list)


def _ensure_model() -> Path:
    if _MODEL_PATH.exists() and _MODEL_PATH.stat().st_size > 100_000:
        return _MODEL_PATH
    _MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"[HandTracker] Downloading HandLandmarker model -> {_MODEL_PATH}")
    urllib.request.urlretrieve(_MODEL_URL, _MODEL_PATH)
    print("[HandTracker] Model ready")
    return _MODEL_PATH


class _LM:
    """Thin wrapper so landmark access matches .x / .y style."""

    __slots__ = ("x", "y", "z")

    def __init__(self, x: float, y: float, z: float = 0.0):
        self.x = x
        self.y = y
        self.z = z


class HandTracker:
    PINCH_DIST = 0.055
    SMOOTH = 0.35

    def __init__(self, max_hands: int = 2) -> None:
        if mp is None or mp_vision is None:
            raise RuntimeError("mediapipe is not installed. Run: pip install mediapipe")

        model = _ensure_model()
        options = mp_vision.HandLandmarkerOptions(
            base_options=BaseOptions(model_asset_path=str(model)),
            running_mode=mp_vision.RunningMode.VIDEO,
            num_hands=max_hands,
            min_hand_detection_confidence=0.5,
            min_hand_presence_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        self._landmarker = mp_vision.HandLandmarker.create_from_options(options)
        self._smooth_index: tuple[float, float] | None = None
        self._show_still_since: float | None = None
        self._show_last_fire = 0.0
        self._frame_ts_ms = 0
        self.state = HandState()

    @staticmethod
    def _lm(landmarks: list, idx: int, mirror: bool) -> tuple[float, float]:
        pt = landmarks[idx]
        x = 1.0 - pt.x if mirror else pt.x
        return x, pt.y

    @staticmethod
    def _dist(a: tuple[float, float], b: tuple[float, float]) -> float:
        return math.hypot(a[0] - b[0], a[1] - b[1])

    def _finger_extended(self, landmarks, tip: int, pip: int, mcp: int, mirror: bool) -> bool:
        tip_pt = self._lm(landmarks, tip, mirror)
        pip_pt = self._lm(landmarks, pip, mirror)
        mcp_pt = self._lm(landmarks, mcp, mirror)
        return self._dist(tip_pt, mcp_pt) > self._dist(pip_pt, mcp_pt) * 1.05

    def _is_pinching(self, landmarks, mirror: bool) -> bool:
        t = self._lm(landmarks, THUMB_TIP, mirror)
        i = self._lm(landmarks, INDEX_TIP, mirror)
        return self._dist(t, i) < self.PINCH_DIST

    def _is_index_open(self, landmarks, mirror: bool) -> bool:
        if self._is_pinching(landmarks, mirror):
            return False
        return self._finger_extended(landmarks, INDEX_TIP, INDEX_PIP, INDEX_MCP, mirror)

    def _is_open_palm(self, landmarks, mirror: bool) -> bool:
        if self._is_pinching(landmarks, mirror):
            return False
        fingers = (
            self._finger_extended(landmarks, INDEX_TIP, INDEX_PIP, INDEX_MCP, mirror),
            self._finger_extended(landmarks, MIDDLE_TIP, 10, MIDDLE_MCP, mirror),
            self._finger_extended(landmarks, RING_TIP, 14, 13, mirror),
            self._finger_extended(landmarks, PINKY_TIP, 18, 17, mirror),
        )
        return sum(fingers) >= 3

    def _smooth(self, x: float, y: float) -> tuple[float, float]:
        if self._smooth_index is None:
            self._smooth_index = (x, y)
        else:
            ox, oy = self._smooth_index
            self._smooth_index = (
                ox * (1 - self.SMOOTH) + x * self.SMOOTH,
                oy * (1 - self.SMOOTH) + y * self.SMOOTH,
            )
        return self._smooth_index

    def process(
        self,
        frame_bgr: np.ndarray,
        *,
        mirror: bool = True,
        now: float,
        show_hold_sec: float = 1.1,
        show_cooldown: float = 5.0,
    ) -> tuple[HandState, bool]:
        """Process one BGR frame. Returns (state, show_triggered)."""
        h, w = frame_bgr.shape[:2]
        rgb = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2RGB)
        mp_image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)

        # VIDEO mode needs strictly increasing timestamps
        self._frame_ts_ms += 33
        result = self._landmarker.detect_for_video(mp_image, self._frame_ts_ms)

        show_triggered = False
        self.state = HandState()
        hands = result.hand_landmarks or []
        self.state.hand_count = len(hands)

        if not hands:
            self._smooth_index = None
            self._show_still_since = None
            self.state.label = "NO HAND"
            return self.state, False

        # Pixel landmarks for overlay drawing (frame already mirrored by engine)
        for hand_lms in hands:
            pts = [(int(lm.x * w), int(lm.y * h)) for lm in hand_lms]
            self.state.landmarks_px.append(pts)

        hand = hands[0]
        # Wrap as objects with .x/.y for helper methods
        lms = [_LM(p.x, p.y, getattr(p, "z", 0.0)) for p in hand]

        ix, iy = self._lm(lms, INDEX_TIP, mirror)
        tx, ty = self._lm(lms, THUMB_TIP, mirror)
        wx, wy = self._lm(lms, WRIST, mirror)
        sx, sy = self._smooth(ix, iy)

        self.state.index_tip = (sx, sy)
        self.state.thumb_tip = (tx, ty)
        self.state.wrist = (wx, wy)

        pinching = self._is_pinching(lms, mirror)
        index_open = self._is_index_open(lms, mirror)
        open_palm = self._is_open_palm(lms, mirror)

        if self.state.hand_count == 1 and index_open and not open_palm:
            self.state.mode = GestureMode.VOLUME
            self.state.label = "1 HAND · VOLUME"
            px, py = int(sx * w), int(sy * h)
            self.state.trail.append((px, py))
            if len(self.state.trail) > 40:
                self.state.trail.pop(0)
            self._show_still_since = None
        elif open_palm and not pinching:
            self.state.mode = GestureMode.SHOW
            self.state.label = "1 HAND · SHOW"
            if self._show_still_since is None:
                self._show_still_since = now
            elif now - self._show_still_since >= show_hold_sec:
                if now - self._show_last_fire >= show_cooldown:
                    show_triggered = True
                    self._show_last_fire = now
                self._show_still_since = now
        elif pinching:
            self.state.mode = GestureMode.PINCH
            self.state.label = "1 HAND · PINCH"
            self._show_still_since = None
        else:
            self.state.label = f"{self.state.hand_count} HAND(S)"
            self._show_still_since = None

        return self.state, show_triggered

    def draw_overlay(self, frame_bgr: np.ndarray, state: HandState) -> None:
        h, w = frame_bgr.shape[:2]

        # Draw hand skeleton
        connections = getattr(
            mp_vision.HandLandmarksConnections,
            "HAND_CONNECTIONS",
            None,
        )
        conn_list = []
        if connections is not None:
            conn_list = [(c.start, c.end) for c in connections]
        else:
            # Fallback classic connections
            conn_list = [
                (0, 1), (1, 2), (2, 3), (3, 4),
                (0, 5), (5, 6), (6, 7), (7, 8),
                (5, 9), (9, 10), (10, 11), (11, 12),
                (9, 13), (13, 14), (14, 15), (15, 16),
                (13, 17), (17, 18), (18, 19), (19, 20),
                (0, 17),
            ]

        for pts in state.landmarks_px:
            for a, b in conn_list:
                if a < len(pts) and b < len(pts):
                    cv2.line(frame_bgr, pts[a], pts[b], (0, 180, 200), 2)
            for p in pts:
                cv2.circle(frame_bgr, p, 3, (0, 220, 255), -1)

        if len(state.trail) > 1:
            for i in range(1, len(state.trail)):
                alpha = i / len(state.trail)
                color = (0, int(180 + 75 * alpha), int(255 * alpha))
                cv2.line(frame_bgr, state.trail[i - 1], state.trail[i], color, 2)

        if state.index_tip and state.mode == GestureMode.VOLUME:
            cx, cy = int(state.index_tip[0] * w), int(state.index_tip[1] * h)
            cv2.circle(frame_bgr, (cx, cy), 10, (0, 220, 255), 2)

        cv2.rectangle(frame_bgr, (8, 8), (min(w - 8, 280), 36), (0, 0, 0), -1)
        cv2.putText(
            frame_bgr,
            state.label,
            (14, 28),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (0, 220, 255),
            1,
            cv2.LINE_AA,
        )

    def close(self) -> None:
        try:
            self._landmarker.close()
        except Exception:
            pass
