"""
Windows system volume bridge.
Uses PowerShell WScript.Shell.SendKeys with virtual volume keys.
"""
from __future__ import annotations

import platform
import subprocess
import sys
import threading
import time

_LOCK = threading.Lock()
_LAST_CALL = 0.0
_MIN_INTERVAL = 0.06  # rate-limit PowerShell (~16 calls/s max)


def _run_powershell_volume(direction: str, steps: int) -> dict:
    if platform.system() != "Windows":
        return {"ok": False, "error": "Volume bridge is Windows-only"}

    steps = max(1, min(int(steps), 20))
    key = "{VOLUMEUP}" if direction == "up" else "{VOLUMEDOWN}"
    ps = (
        "$ws = New-Object -ComObject WScript.Shell; "
        f"1..{steps} | ForEach-Object {{ $ws.SendKeys('{key}') }}"
    )
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=creation,
        )
        if r.returncode != 0:
            err = (r.stderr or r.stdout or "").strip()
            return {"ok": False, "error": err or f"exit {r.returncode}"}
        return {"ok": True, "direction": direction, "steps": steps}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def change_volume(direction: str, steps: int = 1) -> dict:
    """Thread-safe, rate-limited volume change."""
    global _LAST_CALL
    with _LOCK:
        now = time.monotonic()
        wait = _MIN_INTERVAL - (now - _LAST_CALL)
        if wait > 0:
            time.sleep(wait)
        result = _run_powershell_volume(direction, steps)
        _LAST_CALL = time.monotonic()
        return result
