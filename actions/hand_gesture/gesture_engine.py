"""
Hand gesture engine — webcam loop + volume + show-hand vision trigger.
Optimized for Camo Studio (phone-as-webcam) on Windows via DirectShow.
"""
from __future__ import annotations

import threading
import time
from typing import Callable

import cv2
import numpy as np

from actions.hand_gesture.camera_utils import open_camera
from actions.hand_gesture.hand_tracker import GestureMode, HandTracker
from actions.hand_gesture.volume_controller import VolumeController


class HandGestureEngine:
    """
    Runs hand tracking in a background thread.
    Callbacks:
      on_frame(jpeg_bytes)
      on_status(str)
      on_show_hand(jpeg_bytes)
    """

    def __init__(
        self,
        on_frame: Callable[[bytes], None] | None = None,
        on_status: Callable[[str], None] | None = None,
        on_show_hand: Callable[[bytes], None] | None = None,
    ) -> None:
        self._on_frame = on_frame
        self._on_status = on_status
        self._on_show_hand = on_show_hand
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._preview_visible = True
        self._tracker: HandTracker | None = None
        self._volume = VolumeController()
        self.active = False
        self.last_error = ""
        self.device_name = ""

    @property
    def preview_visible(self) -> bool:
        return self._preview_visible

    def set_preview_visible(self, visible: bool) -> None:
        self._preview_visible = visible

    def start(self) -> str:
        if self.active:
            return "Hand gestures already running"
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="hand-gesture")
        self._thread.start()
        self.active = True
        self._emit_status("HAND CAM · STARTING")
        return "Hand gesture camera starting (Camo / webcam)…"

    def stop(self) -> str:
        if not self.active and not (self._thread and self._thread.is_alive()):
            return "Hand gestures not running"
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5.0)
            self._thread = None
        if self._tracker:
            try:
                self._tracker.close()
            except Exception:
                pass
            self._tracker = None
        self._volume.on_hand_lost()
        self.active = False
        self._emit_status("HAND CAM · OFF")
        return "Hand gesture camera stopped"

    def _emit_status(self, msg: str) -> None:
        if self._on_status:
            try:
                self._on_status(msg)
            except Exception:
                pass

    def _emit_frame(self, frame: np.ndarray) -> None:
        if not self._preview_visible or not self._on_frame:
            return
        try:
            # Slightly higher quality so Camo feed looks clear
            ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 80])
            if ok:
                self._on_frame(buf.tobytes())
        except Exception as e:
            print(f"[HandGesture] frame callback failed: {e}")

    def _loop(self) -> None:
        # Open Camo / webcam FIRST so preview is not black while model loads
        self._emit_status("HAND CAM · OPENING")
        cap, idx, name = open_camera(prefer_name="camo")
        if cap is None:
            self.last_error = "No camera (start Camo Studio + phone)"
            self._emit_status("HAND CAM · NO CAMERA")
            self.active = False
            print("[HandGesture] Failed to open camera — is Camo Studio running?")
            return

        self.device_name = name or f"index {idx}"
        self._emit_status(f"HAND CAM · {self.device_name}")

        # Push a few raw preview frames immediately (before MediaPipe loads)
        for _ in range(5):
            if self._stop.is_set():
                break
            ok, frame = cap.read()
            if ok and frame is not None and float(np.mean(frame)) > 5:
                self._emit_frame(cv2.flip(frame, 1))
            time.sleep(0.03)

        try:
            self._tracker = HandTracker(max_hands=2)
        except Exception as e:
            self.last_error = str(e)
            print(f"[HandGesture] Tracker init failed: {e}")
            # Keep showing raw camera even without tracking
            self._emit_status(f"CAM ONLY · {self.device_name}")
            while not self._stop.is_set():
                ok, frame = cap.read()
                if ok and frame is not None:
                    self._emit_frame(cv2.flip(frame, 1))
                time.sleep(0.04)
            cap.release()
            self.active = False
            return

        tracker = self._tracker
        print(f"[HandGesture] Loop running on {self.device_name!r}")
        self._emit_status(f"1 HAND READY · {self.device_name}")

        while not self._stop.is_set():
            ok, frame = cap.read()
            if not ok or frame is None:
                time.sleep(0.05)
                continue

            # Soft skip only true black (Camo disconnect / phone locked)
            mean = float(np.mean(frame))
            if mean < 1.5:
                self._emit_status("CAMO BLACK · unlock phone / resume Camo")
                # Still push so UI isn't a dead panel
                self._emit_frame(cv2.flip(frame, 1))
                time.sleep(0.08)
                continue

            frame = cv2.flip(frame, 1)
            now = time.monotonic()

            try:
                state, show_hit = tracker.process(frame, mirror=False, now=now)
            except Exception as e:
                print(f"[HandGesture] process error: {e}")
                self._emit_frame(frame)
                time.sleep(0.05)
                continue

            if state.hand_count == 0:
                self._volume.on_hand_lost()
            elif state.mode == GestureMode.VOLUME and state.index_tip:
                self._volume.update(state.index_tip[0], state.index_tip[1])
            else:
                self._volume.on_hand_lost()

            if show_hit and self._on_show_hand:
                _, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
                try:
                    self._on_show_hand(buf.tobytes())
                except Exception as e:
                    print(f"[HandGesture] show callback failed: {e}")

            try:
                tracker.draw_overlay(frame, state)
            except Exception:
                pass

            label = state.label
            if self.device_name:
                label = f"{state.label} · {self.device_name}"
            self._emit_status(label)
            self._emit_frame(frame)
            time.sleep(0.02)

        cap.release()
        try:
            tracker.close()
        except Exception:
            pass
        self.active = False
        print("[HandGesture] Loop stopped")
