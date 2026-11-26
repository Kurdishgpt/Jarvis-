import time
import subprocess
import platform
import shutil
from pathlib import Path

try:
    import psutil
    _PSUTIL = True
except ImportError:
    _PSUTIL = False

_SYSTEM = platform.system()

# Direct-launch targets (never Windows Start Menu search — looks like an internal module)
_CHAWDERE_ALIASES = {
    "chawdere",
    "چاودێری",
    "چاودێریکردن",
    "بەشی چاودێری",
    "بەشی چاودێریکردن",
    "برۆ بەشی چاودێریکردن",
    "بڕۆ بەشی چاودێریکردن",
    "surveillance",
    "surveillance section",
    "monitoring",
    "monitoring section",
}


def _resolve_lnk(path: Path) -> tuple[str, str, str] | None:
    """Return (target, args, working_dir) for a .lnk, or None."""
    if not path.exists():
        return None
    try:
        import win32com.client  # type: ignore
        shell = win32com.client.Dispatch("WScript.Shell")
        sc = shell.CreateShortcut(str(path))
        return (sc.TargetPath or "", sc.Arguments or "", sc.WorkingDirectory or "")
    except Exception:
        pass
    try:
        # PowerShell fallback
        ps = (
            f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{str(path)}');"
            "$s.TargetPath+'|'+ $s.Arguments+'|'+$s.WorkingDirectory"
        )
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=8,
            creationflags=subprocess.CREATE_NO_WINDOW if _SYSTEM == "Windows" else 0,
        )
        line = (r.stdout or "").strip().splitlines()
        if not line:
            return None
        parts = line[-1].split("|", 2)
        if len(parts) >= 3:
            return parts[0], parts[1], parts[2]
    except Exception:
        pass
    return None


def _launch_chawdere() -> bool:
    """
    Open chawdere (eDEX) by direct command — no Start Menu search UI.
    Prefer the desktop shortcut target; fall back to known install path.
    """
    candidates = [
        Path.home() / "OneDrive" / "Desktop" / "chawdere.lnk",
        Path.home() / "Desktop" / "chawdere.lnk",
        Path(r"C:\Users\rawan\OneDrive\Desktop\chawdere.lnk"),
    ]
    target = args = work = ""
    for lnk in candidates:
        resolved = _resolve_lnk(lnk)
        if resolved and resolved[0] and Path(resolved[0]).exists():
            target, args, work = resolved
            break

    if not target:
        # Hard fallback from known layout on this machine
        root = Path(r"C:\Users\rawan\Downloads\edex-ui-master\edex-ui-master")
        electron = root / "node_modules" / "electron" / "dist" / "electron.exe"
        if electron.exists():
            target = str(electron)
            args = "src --nointro"
            work = str(root)

    if not target or not Path(target).exists():
        print("[open_app] chawdere executable not found")
        return False

    cmd = [target, *([a for a in args.split() if a] if args else [])]
    cwd = work if work and Path(work).exists() else str(Path(target).parent)
    try:
        # Direct process launch — no Win+Search UI
        subprocess.Popen(
            cmd,
            cwd=cwd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[open_app] chawdere launched via command: {cmd} (cwd={cwd})")
        time.sleep(1.2)
        return True
    except Exception as e:
        print(f"[open_app] chawdere direct launch failed: {e}")
        return False


def _is_chawdere_request(raw: str) -> bool:
    key = (raw or "").lower().strip()
    if key in {a.lower() for a in _CHAWDERE_ALIASES}:
        return True
    # Fuzzy: any request mentioning the Kurdish monitoring section
    return any(
        token in key
        for token in (
            "chawdere",
            "چاودێری",
            "surveillance",
            "monitoring section",
        )
    )


def _close_chawdere() -> bool:
    """
    Force-close the surveillance module (chawdere / eDEX electron).
    Matches processes by command line so we don't kill unrelated Electron apps.
    """
    needles = (
        "edex-ui-master",
        "edex-ui",
        "--nointro",
    )
    killed = 0

    if _PSUTIL:
        try:
            for proc in psutil.process_iter(["pid", "name", "cmdline"]):
                try:
                    name = (proc.info.get("name") or "").lower()
                    cmdline = " ".join(proc.info.get("cmdline") or []).lower()
                    if not cmdline:
                        continue
                    if "electron" not in name and "electron" not in cmdline:
                        continue
                    if any(n in cmdline for n in needles):
                        proc.kill()
                        killed += 1
                        print(f"[open_app] killed chawdere pid={proc.pid}")
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
        except Exception as e:
            print(f"[open_app] psutil close failed: {e}")

    if killed == 0 and _SYSTEM == "Windows":
        # PowerShell fallback — kill electron processes tied to edex-ui-master
        try:
            ps = (
                "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
                "Where-Object { $_.Name -match '^(electron|chawdere)\\.exe$' -and "
                "$_.CommandLine -and ("
                "$_.CommandLine -match 'edex-ui' -or $_.CommandLine -match '--nointro'"
                ") } | "
                "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; $_.ProcessId }"
            )
            r = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps],
                capture_output=True,
                text=True,
                timeout=12,
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            ids = [ln.strip() for ln in (r.stdout or "").splitlines() if ln.strip().isdigit()]
            killed = len(ids)
            print(f"[open_app] powershell closed {killed} chawdere process(es)")
        except Exception as e:
            print(f"[open_app] powershell close failed: {e}")

    return killed > 0


def close_chawdere(player=None) -> str:
    """Public helper — close surveillance section."""
    ok = _close_chawdere()
    if player:
        try:
            player.write_log("LOC: Surveillance section closed" if ok else "LOC: Surveillance close — nothing running")
        except Exception:
            pass
    if ok:
        return (
            "Surveillance / monitoring section closed. "
            "Confirm briefly in Sorani that بەشی چاودێریکردن داخرا. "
            "Do NOT say chawdere/edex/electron."
        )
    return "Monitoring section was not running (or already closed)."

_APP_ALIASES: dict[str, dict[str, str]] = {

    "chrome":             {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "google chrome":      {"Windows": "chrome",                  "Darwin": "Google Chrome",        "Linux": "google-chrome"},
    "firefox":            {"Windows": "firefox",                 "Darwin": "Firefox",              "Linux": "firefox"},
    "edge":               {"Windows": "msedge",                  "Darwin": "Microsoft Edge",       "Linux": "microsoft-edge"},
    "brave":              {"Windows": "brave",                   "Darwin": "Brave Browser",        "Linux": "brave-browser"},
    "safari":             {"Windows": "msedge",                  "Darwin": "Safari",               "Linux": "firefox"},
    "opera":              {"Windows": "opera",                   "Darwin": "Opera",                "Linux": "opera"},
    "whatsapp":           {"Windows": "WhatsApp",                "Darwin": "WhatsApp",             "Linux": "whatsapp"},
    "telegram":           {"Windows": "Telegram",                "Darwin": "Telegram",             "Linux": "telegram"},
    "discord":            {"Windows": "Discord",                 "Darwin": "Discord",              "Linux": "discord"},
    "slack":              {"Windows": "Slack",                   "Darwin": "Slack",                "Linux": "slack"},
    "zoom":               {"Windows": "Zoom",                    "Darwin": "zoom.us",              "Linux": "zoom"},
    "teams":              {"Windows": "msteams",                 "Darwin": "Microsoft Teams",      "Linux": "teams"},
    "skype":              {"Windows": "skype",                   "Darwin": "Skype",                "Linux": "skype"},
    "signal":             {"Windows": "signal",                  "Darwin": "Signal",               "Linux": "signal"},
    "spotify":            {"Windows": "Spotify",                 "Darwin": "Spotify",              "Linux": "spotify"},
    "vlc":                {"Windows": "vlc",                     "Darwin": "VLC",                  "Linux": "vlc"},
    "netflix":            {"Windows": "Netflix",                 "Darwin": "Netflix",              "Linux": "firefox"},
    "vscode":             {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "visual studio code": {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "code":               {"Windows": "code",                    "Darwin": "Visual Studio Code",   "Linux": "code"},
    "terminal":           {"Windows": "wt",                      "Darwin": "Terminal",             "Linux": "x-terminal-emulator"},
    "cmd":                {"Windows": "cmd.exe",                 "Darwin": "Terminal",             "Linux": "bash"},
    "powershell":         {"Windows": "powershell.exe",          "Darwin": "Terminal",             "Linux": "bash"},
    "postman":            {"Windows": "Postman",                 "Darwin": "Postman",              "Linux": "postman"},
    "git":                {"Windows": "git-bash",                "Darwin": "Terminal",             "Linux": "bash"},
    "figma":              {"Windows": "Figma",                   "Darwin": "Figma",                "Linux": "figma"},
    "blender":            {"Windows": "blender",                 "Darwin": "Blender",              "Linux": "blender"},
    "word":               {"Windows": "winword",                 "Darwin": "Microsoft Word",       "Linux": "libreoffice --writer"},
    "excel":              {"Windows": "excel",                   "Darwin": "Microsoft Excel",      "Linux": "libreoffice --calc"},
    "powerpoint":         {"Windows": "powerpnt",                "Darwin": "Microsoft PowerPoint", "Linux": "libreoffice --impress"},
    "libreoffice":        {"Windows": "soffice",                 "Darwin": "LibreOffice",          "Linux": "libreoffice"},
    "notepad":            {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "textedit":           {"Windows": "notepad.exe",             "Darwin": "TextEdit",             "Linux": "gedit"},
    "explorer":           {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "file explorer":      {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "finder":             {"Windows": "explorer.exe",            "Darwin": "Finder",               "Linux": "nautilus"},
    "task manager":       {"Windows": "taskmgr.exe",             "Darwin": "Activity Monitor",     "Linux": "gnome-system-monitor"},
    "settings":           {"Windows": "ms-settings:",            "Darwin": "System Preferences",   "Linux": "gnome-control-center"},
    "calculator":         {"Windows": "calc.exe",                "Darwin": "Calculator",           "Linux": "gnome-calculator"},
    "paint":              {"Windows": "mspaint.exe",             "Darwin": "Preview",              "Linux": "gimp"},
    "instagram":          {"Windows": "Instagram",               "Darwin": "Instagram",            "Linux": "firefox"},
    "tiktok":             {"Windows": "TikTok",                  "Darwin": "TikTok",               "Linux": "firefox"},
    "notion":             {"Windows": "Notion",                  "Darwin": "Notion",               "Linux": "notion"},
    "obsidian":           {"Windows": "Obsidian",                "Darwin": "Obsidian",             "Linux": "obsidian"},
    "capcut":             {"Windows": "CapCut",                  "Darwin": "CapCut",               "Linux": "capcut"},
    "steam":              {"Windows": "steam",                   "Darwin": "Steam",                "Linux": "steam"},
    "epic":               {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
    "epic games":         {"Windows": "EpicGamesLauncher",       "Darwin": "Epic Games Launcher",  "Linux": "legendary"},
}


def _normalize(raw: str) -> str:
    key = raw.lower().strip()

    if key in _APP_ALIASES:
        return _APP_ALIASES[key].get(_SYSTEM, raw)

    for alias_key, os_map in _APP_ALIASES.items():
        if alias_key in key or key in alias_key:
            return os_map.get(_SYSTEM, raw)

    return raw  

def _launch_windows(app_name: str) -> bool:

    if shutil.which(app_name) or shutil.which(app_name.split(".")[0]):
        try:
            subprocess.Popen(
                app_name,
                shell=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            time.sleep(1.5)
            return True
        except Exception as e:
            print(f"[open_app] subprocess failed: {e}")

    if ":" in app_name:
        try:
            subprocess.Popen(f"start {app_name}", shell=True)
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.PAUSE = 0.1
        pyautogui.press("win")
        time.sleep(0.7)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.9)
        pyautogui.press("enter")
        time.sleep(2.5)
        return True
    except Exception as e:
        print(f"[open_app] Start Menu search failed: {e}")

    return False


def _launch_macos(app_name: str) -> bool:

    try:
        result = subprocess.run(
            ["open", "-a", app_name],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["open", "-a", f"{app_name}.app"],
            capture_output=True, timeout=8
        )
        if result.returncode == 0:
            time.sleep(1.0)
            return True
    except Exception:
        pass

    binary = shutil.which(app_name) or shutil.which(app_name.lower())
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        import pyautogui
        pyautogui.hotkey("command", "space")
        time.sleep(0.6)
        pyautogui.write(app_name, interval=0.05)
        time.sleep(0.8)
        pyautogui.press("enter")
        time.sleep(1.5)
        return True
    except Exception as e:
        print(f"[open_app] Spotlight failed: {e}")

    return False


_LINUX_TERMINAL_FALLBACKS = [
    "x-terminal-emulator", "gnome-terminal", "konsole", "xfce4-terminal",
    "xterm", "lxterminal", "mate-terminal", "tilix", "alacritty", "kitty",
]

def _launch_linux(app_name: str) -> bool:

    # terminal emulators: try common ones in order
    if app_name in ("x-terminal-emulator", "gnome-terminal", "terminal"):
        for term in _LINUX_TERMINAL_FALLBACKS:
            if shutil.which(term):
                try:
                    subprocess.Popen([term], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    time.sleep(1.0)
                    return True
                except Exception:
                    continue

    binary = (
        shutil.which(app_name) or
        shutil.which(app_name.lower()) or
        shutil.which(app_name.lower().replace(" ", "-")) or
        shutil.which(app_name.lower().replace(" ", "_"))
    )
    if binary:
        try:
            subprocess.Popen(
                [binary],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
            time.sleep(1.0)
            return True
        except Exception:
            pass

    try:
        subprocess.run(
            ["xdg-open", app_name],
            capture_output=True, timeout=5
        )
        return True
    except Exception:
        pass

    for desktop_name in [
        app_name.lower(),
        app_name.lower().replace(" ", "-"),
        app_name.lower().replace(" ", ""),
    ]:
        try:
            result = subprocess.run(
                ["gtk-launch", desktop_name],
                capture_output=True, timeout=5
            )
            if result.returncode == 0:
                return True
        except Exception:
            pass

    return False


_OS_LAUNCHERS = {
    "Windows": _launch_windows,
    "Darwin":  _launch_macos,
    "Linux":   _launch_linux,
}

def open_app(
    parameters=None,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    app_name = (params.get("app_name") or params.get("app") or "").strip()
    action = (params.get("action") or "open").lower().strip()
    # Also treat "close chawdere" style names
    close_hint = any(
        w in (app_name + " " + action).lower()
        for w in ("close", "داخە", "دەخەم", "quit", "exit", "kill", "stop")
    )

    if not app_name and action not in ("close", "quit"):
        return "No application name provided."

    if player:
        player.write_log(f"[open_app] {action} {app_name}".strip())

    # Internal monitoring module — direct command open/close (never Start Menu)
    if _is_chawdere_request(app_name) or (
        close_hint and any(t in app_name for t in ("چاودێری", "chawdere", "surveillance"))
    ):
        if action in ("close", "quit", "exit", "kill", "stop") or close_hint:
            print(f"[open_app] Closing surveillance module via '{app_name}'")
            return close_chawdere(player=player)
        print(f"[open_app] Surveillance module requested via '{app_name}'")
        if _launch_chawdere():
            return (
                "Surveillance / monitoring section opened via direct command. "
                "Confirm briefly in Sorani that بەشی چاودێریکردن is open. "
                "Do NOT say app names like chawdere, edex, or electron."
            )
        return "Could not open the monitoring section (direct launch failed)."

    launcher = _OS_LAUNCHERS.get(_SYSTEM)
    if launcher is None:
        return f"Unsupported operating system: {_SYSTEM}"

    normalized = _normalize(app_name)
    print(f"[open_app] Launching: '{app_name}' → '{normalized}' ({_SYSTEM})")

    try:
        if launcher(normalized):
            return f"Opened {app_name}."
        if normalized.lower() != app_name.lower():
            if launcher(app_name):
                return f"Opened {app_name}."
        return (
            f"Could not confirm that {app_name} launched. "
            f"It may still be loading, or it might not be installed."
        )
    except Exception as e:
        print(f"[open_app] Error: {e}")
        return f"Failed to open {app_name}: {e}"