"""
Webcam discovery for Windows Camo Studio / virtual cameras.
Always prefer DirectShow (CAP_DSHOW) — MSMF often returns a black Camo feed.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import cv2
import numpy as np

_BASE = Path(__file__).resolve().parent.parent.parent
_CFG = _BASE / "config" / "api_keys.json"

_PREFERRED_NAMES = (
    "camo",
    "reincubate",
    "iphone",
    "android",
)


def list_camera_names() -> list[str]:
    if sys.platform != "win32":
        return []
    try:
        from pygrabber.dshow_graph import FilterGraph
        return list(FilterGraph().get_input_devices() or [])
    except Exception as e:
        print(f"[Camera] Device list failed: {e}")
        return []


def _read_cfg() -> dict:
    try:
        return json.loads(_CFG.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_camera_index(idx: int, name: str = "") -> None:
    try:
        cfg = _read_cfg()
        cfg["camera_index"] = int(idx)
        if name:
            cfg["camera_name"] = name
        _CFG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[Camera] Could not save camera_index: {e}")


def resolve_camera_index(prefer_name: str | None = None) -> tuple[int, str]:
    names = list_camera_names()
    cfg = _read_cfg()
    saved = cfg.get("camera_index")
    prefer = (prefer_name or cfg.get("camera_name") or "camo").strip().lower()

    if names:
        print(f"[Camera] Devices: {names}")
        if prefer:
            for i, n in enumerate(names):
                if prefer in n.lower():
                    return i, n
        for key in _PREFERRED_NAMES:
            for i, n in enumerate(names):
                if key in n.lower():
                    return i, n
        if saved is not None:
            try:
                si = int(saved)
                if 0 <= si < len(names):
                    return si, names[si]
            except Exception:
                pass
        return 0, names[0]

    try:
        return int(saved if saved is not None else 0), ""
    except Exception:
        return 0, ""


def _score_frame(frame: np.ndarray) -> float:
    if frame is None or frame.size == 0:
        return -1.0
    mean = float(np.mean(frame))
    std = float(np.std(frame))
    if mean < 3 and std < 2:
        return 0.0
    return mean + std * 0.5


def _configure_cap(cap: cv2.VideoCapture, width: int, height: int) -> None:
    try:
        # MJPG is the most reliable FourCC for Camo / virtual cams on DirectShow
        cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
    except Exception:
        pass
    try:
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        cap.set(cv2.CAP_PROP_FPS, 30)
    except Exception:
        pass


def _open_index(
    idx: int,
    name: str,
    *,
    width: int,
    height: int,
    wait_sec: float,
) -> tuple[cv2.VideoCapture | None, float]:
    """Open one index with DSHOW and wait up to wait_sec for a real frame."""
    if sys.platform == "win32":
        cap = cv2.VideoCapture(idx, cv2.CAP_DSHOW)
    else:
        cap = cv2.VideoCapture(idx)

    if not cap.isOpened():
        try:
            cap.release()
        except Exception:
            pass
        return None, 0.0

    _configure_cap(cap, width, height)

    best = 0.0
    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        ok, frame = cap.read()
        if ok and frame is not None:
            s = _score_frame(frame)
            if s > best:
                best = s
            if s > 20:
                break
        time.sleep(0.05)

    label = name or f"index {idx}"
    print(f"[Camera] idx={idx} ({label}) best_score={best:.1f}")
    if best < 5:
        cap.release()
        return None, best
    return cap, best


def open_camera(
    prefer_name: str | None = "camo",
    width: int = 1280,
    height: int = 720,
) -> tuple[cv2.VideoCapture | None, int, str]:
    """
    Open Camo (or best webcam) via DirectShow.
    Camo often needs a longer warmup — we wait before giving up on it.
    """
    primary_idx, primary_name = resolve_camera_index(prefer_name)
    names = list_camera_names()

    # Try preferred Camo first with a long wait (phone stream can lag)
    is_camo = bool(primary_name and "camo" in primary_name.lower())
    wait = 6.0 if is_camo else 2.5

    cap, score = _open_index(
        primary_idx, primary_name, width=width, height=height, wait_sec=wait
    )
    if cap is not None:
        _save_camera_index(primary_idx, primary_name)
        print(f"[Camera] USING idx={primary_idx} name={primary_name!r} score={score:.1f}")
        return cap, primary_idx, primary_name

    # Camo often opens but stays black until the phone unlocks — still bind to it
    if is_camo and sys.platform == "win32":
        print("[Camera] Camo opened black — keeping Camo device (unlock phone / resume Camo)")
        cap2 = cv2.VideoCapture(primary_idx, cv2.CAP_DSHOW)
        if cap2.isOpened():
            _configure_cap(cap2, width, height)
            _save_camera_index(primary_idx, primary_name)
            return cap2, primary_idx, primary_name

    print(f"[Camera] Preferred device failed ({primary_name or primary_idx}) — scanning others")

    # Fallback: other devices (skip empty ByteCast placeholders if possible)
    order: list[tuple[int, str]] = []
    if names:
        for i, n in enumerate(names):
            if i == primary_idx:
                continue
            low = n.lower()
            # Deprioritize empty virtual placeholders
            if "bytecast" in low:
                order.append((i, n))
            else:
                order.insert(0, (i, n))
    else:
        for i in range(8):
            if i != primary_idx:
                order.append((i, ""))

    best: tuple[cv2.VideoCapture, int, str, float] | None = None
    for idx, name in order:
        c, s = _open_index(idx, name, width=width, height=height, wait_sec=2.0)
        if c is None:
            continue
        if best is None or s > best[3]:
            if best is not None:
                try:
                    best[0].release()
                except Exception:
                    pass
            best = (c, idx, name, s)
        else:
            c.release()

    if best is None:
        print("[Camera] No usable camera. Open Camo Studio, unlock phone, enable webcam.")
        return None, primary_idx, primary_name

    cap, idx, name, score = best
    _save_camera_index(idx, name)
    print(f"[Camera] USING fallback idx={idx} name={name!r} score={score:.1f}")
    return cap, idx, name
