"""
Android phone control over Wi‑Fi ADB (wireless debugging).
Unlocks the phone by waking the screen and entering the saved PIN.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import time
from pathlib import Path


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


CONFIG_PATH = _base_dir() / "config" / "phone.json"

_DEFAULT_ADB_CANDIDATES = [
    Path.home() / "AppData/Local/Android/Sdk/platform-tools/adb.exe",
    Path(r"C:\Android\platform-tools\adb.exe"),
    Path(r"C:\platform-tools\adb.exe"),
]

# Keycodes (numeric — more reliable than KEYCODE_* names on some builds)
KEYCODE_HOME = "3"
KEYCODE_POWER = "26"
KEYCODE_ENTER = "66"
KEYCODE_BACK = "4"
KEYCODE_MENU = "82"
KEYCODE_WAKEUP = "224"
KEYCODE_SLEEP = "223"


def _load_cfg() -> dict:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_cfg(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _find_adb(cfg: dict | None = None) -> str:
    cfg = cfg or _load_cfg()
    custom = (cfg.get("adb_path") or "").strip()
    if custom and Path(custom).exists():
        return custom
    for p in _DEFAULT_ADB_CANDIDATES:
        if p.exists():
            return str(p)
    from shutil import which
    w = which("adb")
    if w:
        return w
    raise FileNotFoundError(
        "adb.exe not found. Install Android platform-tools or set adb_path in config/phone.json"
    )


def _adb(adb: str, *args: str, timeout: float = 20.0) -> subprocess.CompletedProcess:
    creation = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        return subprocess.run(
            [adb, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=creation,
        )
    except subprocess.TimeoutExpired as e:
        cmd = " ".join(args)
        print(f"[Phone] adb {cmd!r} → TIMEOUT ({timeout}s)")
        return subprocess.CompletedProcess(
            args=[adb, *args],
            returncode=124,
            stdout="",
            stderr=f"TIMEOUT after {timeout}s: {cmd}",
        )


def _shell(adb: str, serial: str, *args: str, timeout: float = 12.0) -> str:
    try:
        r = _adb(adb, "-s", serial, "shell", *args, timeout=timeout)
    except subprocess.TimeoutExpired:
        cmd = " ".join(args)
        print(f"[Phone] shell {cmd!r} → TIMEOUT ({timeout}s)")
        return f"TIMEOUT after {timeout}s"
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    cmd = " ".join(args)
    print(f"[Phone] shell {cmd!r} → rc={r.returncode} {out[:160]!r}")
    return out


def _is_inject_blocked(out: str) -> bool:
    low = (out or "").lower()
    return (
        "securityexception" in low
        or "injecting input events" in low
        or "injections of input" in low
    )


def _security_settings_help() -> str:
    return (
        "Phone still blocks ADB taps/keys (SecurityException). "
        "JARVIS is connected, but Android refuses input until you enable this:\n"
        "Xiaomi / Redmi / POCO / HyperOS:\n"
        "  Settings → Additional settings → Developer options\n"
        "  → ON: USB debugging\n"
        "  → ON: USB debugging (Security settings)  ← this one is required\n"
        "  → ON: Wireless debugging\n"
        "  → REBOOT the phone once after turning Security settings ON\n"
        "  → Open Wireless debugging again, re-pair if needed, then unlock\n"
        "Samsung: Developer options → Disable permission monitoring (if shown)\n"
        "Without that setting, unlock is impossible over Wi‑Fi ADB."
    )


def _serial(cfg: dict) -> str:
    host = (cfg.get("host") or "").strip()
    port = int(cfg.get("connect_port") or 0)
    if not host or not port:
        raise ValueError("Phone host/connect_port missing in config/phone.json")
    return f"{host}:{port}"


def pair_phone(cfg: dict | None = None) -> str:
    cfg = cfg or _load_cfg()
    adb = _find_adb(cfg)
    host = (cfg.get("host") or "").strip()
    pair_port = int(cfg.get("pair_port") or 0)
    code = str(cfg.get("pair_code") or "").strip()
    if not host or not pair_port or not code:
        return "Pairing needs host, pair_port, and pair_code in config/phone.json"

    target = f"{host}:{pair_port}"
    print(f"[Phone] Pairing {target} …")
    r = _adb(adb, "pair", target, code, timeout=30)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    print(f"[Phone] pair → {out}")
    if r.returncode != 0 and "Successfully" not in out:
        return f"Pair failed: {out or r.returncode}"
    return f"Paired: {out or 'ok'}"


def connect_phone(cfg: dict | None = None) -> str:
    cfg = cfg or _load_cfg()
    try:
        adb = _find_adb(cfg)
        serial = _serial(cfg)
    except Exception as e:
        return f"Connect failed: {e}"
    print(f"[Phone] Connecting {serial} …")
    r = _adb(adb, "connect", serial, timeout=8)
    out = ((r.stdout or "") + (r.stderr or "")).strip()
    print(f"[Phone] connect → {out}")
    if "timeout" in out.lower():
        return (
            f"Connect failed: phone ADB timed out ({serial}). "
            "Check Wireless debugging / same Wi‑Fi, or play songs on PC with youtube_video."
        )
    if "connected" in out.lower() or "already connected" in out.lower():
        return f"Connected to {serial}"
    pair_msg = pair_phone(cfg)
    r2 = _adb(adb, "connect", serial, timeout=8)
    out2 = ((r2.stdout or "") + (r2.stderr or "")).strip()
    print(f"[Phone] connect2 → {out2}")
    if "connected" in out2.lower() or "already connected" in out2.lower():
        return f"Connected to {serial} (after pair: {pair_msg})"
    return f"Connect failed: {out2 or out}"


def _ensure_device(adb: str, serial: str) -> bool:
    r = _adb(adb, "devices")
    for line in (r.stdout or "").splitlines():
        if line.startswith(serial) and "\tdevice" in line:
            return True
    return False


def _shell_alive(adb: str, serial: str) -> bool:
    out = _shell(adb, serial, "echo", "jarvis_ping")
    return "jarvis_ping" in out


def _screen_size(adb: str, serial: str) -> tuple[int, int]:
    out = _shell(adb, serial, "wm", "size")
    # Physical size: 1080x2400  OR  Override size: ...
    m = re.search(r"(\d+)x(\d+)", out)
    if m:
        return int(m.group(1)), int(m.group(2))
    return 1080, 2400


def _is_screen_on(adb: str, serial: str) -> bool:
    out = _shell(adb, serial, "dumpsys", "power")
    low = out.lower()
    if "display power: state=on" in low or "mholdingdisplayname=true" in low.replace(" ", ""):
        return True
    if "mScreenOn=true" in out or "mWakefulness=Awake" in out:
        return True
    # Fallback: dumpsys display
    out2 = _shell(adb, serial, "dumpsys", "display")
    if "mScreenState=ON" in out2 or "SCREEN_STATE_ON" in out2:
        return True
    return False


def _wake(adb: str, serial: str) -> str | None:
    """
    Wake screen WITHOUT using POWER key.
    Xiaomi/HyperOS: double POWER opens Camera — never use POWER for unlock.
    """
    for args, t in (
        (("cmd", "window", "dismiss-keyguard"), 6.0),
        (("wm", "dismiss-keyguard"), 6.0),
    ):
        out = _shell(adb, serial, *args, timeout=t)
        if _is_inject_blocked(out):
            return _security_settings_help()
        time.sleep(0.1)

    # Prefer WAKEUP only (safe). Retry a few times — no POWER.
    for _ in range(3):
        out = _shell(adb, serial, "input", "keyevent", KEYCODE_WAKEUP)
        if _is_inject_blocked(out):
            return _security_settings_help()
        time.sleep(0.4)
        if _is_screen_on(adb, serial):
            return None
    return None


def _swipe_up(adb: str, serial: str, w: int, h: int) -> None:
    """Center swipe up — avoid bottom corners (camera / flash gestures)."""
    x = w // 2
    y1 = int(h * 0.75)
    y2 = int(h * 0.35)
    _shell(adb, serial, "input", "swipe", str(x), str(y1), str(x), str(y2), "280")
    time.sleep(0.4)


def _enter_pin(adb: str, serial: str, pin: str) -> None:
    """Enter PIN via text + keyevents only (no random corner taps → no camera)."""
    _shell(adb, serial, "input", "text", pin)
    time.sleep(0.3)
    _shell(adb, serial, "input", "keyevent", KEYCODE_ENTER)
    time.sleep(0.35)

    for ch in pin:
        if "0" <= ch <= "9":
            code = str(7 + (ord(ch) - ord("0")))
            _shell(adb, serial, "input", "keyevent", code)
            time.sleep(0.16)
    time.sleep(0.2)
    _shell(adb, serial, "input", "keyevent", KEYCODE_ENTER)


def _go_home(adb: str, serial: str) -> None:
    _shell(adb, serial, "input", "keyevent", KEYCODE_HOME)
    time.sleep(0.25)


def _force_stop_camera(adb: str, serial: str) -> None:
    """If unlock accidentally opened Camera, close common camera packages."""
    for pkg in (
        "com.android.camera",
        "com.android.camera2",
        "com.miui.camera",
        "com.xiaomi.camera",
        "com.google.android.GoogleCamera",
        "com.sec.android.app.camera",
        "com.huawei.camera",
        "com.oppo.camera",
        "com.oneplus.camera",
    ):
        _shell(adb, serial, "am", "force-stop", pkg, timeout=6.0)


_APP_PACKAGES = {
    "youtube": [
        "com.google.android.youtube",
        "com.vanced.android.youtube",
        "app.revanced.android.youtube",
    ],
    "chrome": ["com.android.chrome", "com.chrome.beta"],
    "instagram": ["com.instagram.android"],
    "whatsapp": ["com.whatsapp", "com.whatsapp.w4b"],
    "telegram": ["org.telegram.messenger", "org.thunderdog.challegram"],
    "settings": ["com.android.settings"],
    "camera": ["com.android.camera", "com.android.camera2", "com.miui.camera"],
}


def _prepare_device(player=None) -> tuple[str | None, str | None, str | None]:
    """Returns (error_msg, adb, serial)."""
    cfg = _load_cfg()
    try:
        adb = _find_adb(cfg)
        serial = _serial(cfg)
    except Exception as e:
        return str(e), None, None

    conn = connect_phone(cfg)
    if "fail" in conn.lower() and "connected" not in conn.lower():
        return conn, None, None
    if not _ensure_device(adb, serial):
        _adb(adb, "disconnect", serial, timeout=10)
        connect_phone(cfg)
        if not _ensure_device(adb, serial):
            return f"Phone not online ({serial}).", None, None
    if not _shell_alive(adb, serial):
        return "ADB shell not responding. Toggle Wireless debugging.", None, None
    return None, adb, serial


def unlock_phone(parameters: dict | None = None, player=None) -> str:
    """Wake phone + enter PIN to unlock."""
    params = parameters or {}
    cfg = _load_cfg()

    if params.get("host"):
        cfg["host"] = str(params["host"]).strip()
    if params.get("connect_port"):
        cfg["connect_port"] = int(params["connect_port"])
    if params.get("pair_port"):
        cfg["pair_port"] = int(params["pair_port"])
    if params.get("pair_code"):
        cfg["pair_code"] = str(params["pair_code"]).strip()
    if params.get("pin"):
        cfg["pin"] = str(params["pin"]).strip()
        _save_cfg(cfg)

    pin = str(cfg.get("pin") or "").strip()
    if not pin:
        return "Phone PIN is not set in config/phone.json"

    try:
        adb = _find_adb(cfg)
        serial = _serial(cfg)
    except Exception as e:
        return str(e)

    conn = connect_phone(cfg)
    if "fail" in conn.lower() and "connected" not in conn.lower():
        _log(player, conn)
        return conn

    if not _ensure_device(adb, serial):
        _adb(adb, "disconnect", serial, timeout=10)
        conn = connect_phone(cfg)
        if not _ensure_device(adb, serial):
            msg = f"Phone not online over ADB ({serial}). Enable Wireless debugging. {conn}"
            _log(player, msg)
            return msg

    # Critical: "connected" is not enough — shell must respond
    if not _shell_alive(adb, serial):
        msg = (
            f"ADB shows connected but shell is dead on {serial}. "
            "Toggle Wireless debugging on the phone, then try again."
        )
        _log(player, msg)
        return msg

    try:
        w, h = _screen_size(adb, serial)
        print(f"[Phone] Screen {w}x{h}")

        blocked = _wake(adb, serial)
        if blocked:
            _log(player, blocked)
            return blocked

        time.sleep(0.4)
        screen_on = _is_screen_on(adb, serial)
        print(f"[Phone] screen_on={screen_on}")

        # Show PIN pad — center swipe only (no MENU / no POWER / no corner taps)
        probe = _shell(adb, serial, "input", "keyevent", KEYCODE_WAKEUP)
        if _is_inject_blocked(probe):
            msg = _security_settings_help()
            _log(player, msg)
            return msg
        time.sleep(0.2)
        _swipe_up(adb, serial, w, h)
        time.sleep(0.45)

        _enter_pin(adb, serial, pin)
        time.sleep(0.5)

        # Leave Camera if it opened; land on Home launcher
        _force_stop_camera(adb, serial)
        _go_home(adb, serial)

        screen_on2 = _is_screen_on(adb, serial)
        msg = (
            f"Phone unlocked → Home "
            f"(screen_was_on={screen_on}, screen_now_on={screen_on2}, size={w}x{h})."
        )
        _log(player, msg)
        return msg
    except subprocess.TimeoutExpired:
        return "ADB unlock timed out."
    except Exception as e:
        return f"Unlock failed: {e}"


def play_youtube_on_phone(query: str = "", url: str = "", player=None) -> str:
    """
    Play / search a video in the YouTube APP on the Android phone.
    Used when user says: play X on mobile youtube / لە مۆبایل یوتیوب.
    """
    from urllib.parse import quote_plus

    err, adb, serial = _prepare_device(player)
    if err:
        _log(player, err)
        return err

    query = (query or "").strip()
    url = (url or "").strip()

    # Resolve a watch URL when possible (autoplays better than search page)
    watch = url
    if not watch and query:
        try:
            from actions.youtube_video import _scrape_first_video_url
            watch = _scrape_first_video_url(query) or ""
            if watch:
                print(f"[Phone] YouTube scrape → {watch}")
        except Exception as e:
            print(f"[Phone] YouTube scrape failed: {e}")
            watch = ""

    if not watch and query:
        watch = (
            "https://www.youtube.com/results?search_query="
            + quote_plus(query)
        )
    if not watch:
        watch = "https://www.youtube.com"

    # Open inside YouTube package when installed
    opened = False
    last_out = ""
    for pkg in _APP_PACKAGES["youtube"]:
        out = _shell(
            adb, serial,
            "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", watch,
            "-p", pkg,
            timeout=12.0,
        )
        last_out = out
        if (
            "Error" not in out
            and "Exception" not in out
            and "does not exist" not in out.lower()
            and "No activity" not in out
        ):
            opened = True
            msg = f"Playing on phone YouTube ({pkg}): {query or watch}"
            _log(player, msg)
            # Nudge play if needed (center tap)
            time.sleep(2.0)
            w, h = _screen_size(adb, serial)
            _shell(adb, serial, "input", "tap", str(w // 2), str(h // 2), timeout=6.0)
            return msg

    # Fallback: VIEW without package (phone picks YouTube / browser)
    _shell(
        adb, serial,
        "am", "start", "-a", "android.intent.action.VIEW", "-d", watch,
        timeout=12.0,
    )
    msg = f"Opened on phone: {query or watch}" + (f" ({last_out[:80]})" if not opened else "")
    _log(player, msg)
    return msg


def open_phone_app(app_name: str = "", url: str = "", player=None) -> str:
    """Open an app or URL on the Android phone (not Windows)."""
    err, adb, serial = _prepare_device(player)
    if err:
        _log(player, err)
        return err

    name = (app_name or "").strip().lower()
    url = (url or "").strip()

    # YouTube shortcuts
    if name in ("youtube", "yt", "یوتیوب", "يوتيوب") or (
        "youtube" in name and "http" not in name
    ):
        # Prefer native YouTube app
        for pkg in _APP_PACKAGES["youtube"]:
            out = _shell(
                adb, serial,
                "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1",
                timeout=10.0,
            )
            if "No activities" not in out and "Error" not in out and "aborted" not in out.lower():
                msg = f"Opened YouTube app on phone ({pkg})."
                _log(player, msg)
                return msg
        # Fallback: open YouTube site in phone browser
        url = url or "https://www.youtube.com"
        _shell(
            adb, serial,
            "am", "start", "-a", "android.intent.action.VIEW", "-d", url,
            timeout=10.0,
        )
        msg = "Opened YouTube in phone browser."
        _log(player, msg)
        return msg

    if url.startswith("http://") or url.startswith("https://") or url.startswith("vnd."):
        _shell(
            adb, serial,
            "am", "start", "-a", "android.intent.action.VIEW", "-d", url,
            timeout=10.0,
        )
        msg = f"Opened URL on phone: {url}"
        _log(player, msg)
        return msg

    # Known app aliases
    packages = _APP_PACKAGES.get(name)
    if not packages and name:
        # Treat as package name if it looks like one
        if "." in name:
            packages = [name]
        else:
            # Try monkey with resolved package via pm
            out = _shell(adb, serial, "pm", "list", "packages", timeout=15.0)
            hits = [
                line.split(":", 1)[-1].strip()
                for line in out.splitlines()
                if name in line.lower()
            ]
            packages = hits[:3] if hits else []

    if packages:
        for pkg in packages:
            out = _shell(
                adb, serial,
                "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1",
                timeout=10.0,
            )
            if "No activities" not in out and "aborted" not in out.lower():
                msg = f"Opened {pkg} on phone."
                _log(player, msg)
                return msg

    return f"Could not open app on phone: {app_name or url or '(empty)'}"


def phone_status(player=None) -> str:
    cfg = _load_cfg()
    try:
        adb = _find_adb(cfg)
        serial = _serial(cfg)
    except Exception as e:
        return str(e)
    r = _adb(adb, "devices")
    out = (r.stdout or "").strip()
    online = _ensure_device(adb, serial)
    alive = _shell_alive(adb, serial) if online else False
    on = _is_screen_on(adb, serial) if alive else False
    msg = (
        f"Target {serial} — listed={online} shell={alive} screen_on={on}\n{out}"
    )
    _log(player, msg)
    return msg


def phone_control(parameters: dict | None = None, player=None, **_kwargs) -> str:
    try:
        return _phone_control_inner(parameters=parameters, player=player, **_kwargs)
    except subprocess.TimeoutExpired as e:
        msg = (
            f"Phone ADB timed out ({getattr(e, 'timeout', '?')}s). "
            "Phone offline — for songs on PC use youtube_video instead."
        )
        _log(player, msg)
        return msg
    except Exception as e:
        msg = f"Phone control failed: {e}"
        _log(player, msg)
        return msg


def _phone_control_inner(parameters: dict | None = None, player=None, **_kwargs) -> str:
    params = parameters or {}
    action = (params.get("action") or "unlock").lower().strip()
    app = (params.get("app") or params.get("app_name") or "").strip()
    url = (params.get("url") or "").strip()
    query = (
        params.get("query")
        or params.get("search")
        or params.get("song")
        or params.get("video")
        or ""
    )
    query = str(query).strip()

    if action in ("unlock", "wake"):
        return unlock_phone(params, player=player)

    # Play / search on phone YouTube
    if action in ("play", "play_youtube", "youtube_play", "search_youtube"):
        return play_youtube_on_phone(query=query, url=url, player=player)
    if action in ("youtube", "open_youtube"):
        if query or (url and "watch" in url) or (url and "results" in url):
            return play_youtube_on_phone(query=query, url=url, player=player)
        return open_phone_app(app_name="youtube", url=url, player=player)

    if action in ("open", "open_app", "launch", "start"):
        # "open/play youtube <song> on phone"
        if app.lower() in ("youtube", "yt", "یوتیوب") and (query or url):
            return play_youtube_on_phone(query=query, url=url, player=player)
        if not app and not url:
            return "Specify app=youtube (or another app) / url to open on the phone."
        return open_phone_app(app_name=app, url=url, player=player)
        
    if action in ("close", "close_app", "stop", "force_stop"):
        if not app:
            return "Specify app=youtube (or another app) to close on the phone."
        err, adb, serial = _prepare_device(player)
        if err:
            return err
            
        name = app.lower().strip()
        packages = _APP_PACKAGES.get(name)
        if not packages:
            if "." in name:
                packages = [name]
            else:
                out = _shell(adb, serial, "pm", "list", "packages", timeout=15.0)
                packages = [
                    line.split(":", 1)[-1].strip()
                    for line in out.splitlines()
                    if name in line.lower()
                ][:3]
                
        if not packages:
            return f"Could not find app to close: {app}"
            
        successes = []
        for pkg in packages:
            _shell(adb, serial, "am", "force-stop", pkg, timeout=6.0)
            successes.append(pkg)
            
        msg = f"Closed {app} on phone ({', '.join(successes)})."
        _log(player, msg)
        return msg

    if action == "home":
        err, adb, serial = _prepare_device(player)
        if err:
            return err
        _go_home(adb, serial)
        return "Went to phone Home screen."
    if action == "pair":
        return pair_phone()
    if action == "connect":
        return connect_phone()
    if action in ("status", "devices"):
        return phone_status(player=player)
    # If user said open X without explicit action naming
    if app or url:
        return open_phone_app(app_name=app, url=url, player=player)
    return f"Unknown phone action: {action}"


def _log(player, msg: str) -> None:
    print(f"[Phone] {msg}")
    if player:
        try:
            player.write_log(f"SYS: {msg}")
        except Exception:
            pass
