"""
Briefing media layout: Radware threat map + ONE Trump/Iran YouTube in Picture-in-Picture.
Flow: open map → play video only in Chromium → PiP → hide Chromium → focus map again.
"""
from __future__ import annotations

import threading
import time
from urllib.parse import quote_plus

# Keep Playwright browser alive so the PiP window does not close
_pip_keepalive: dict | None = None
_pip_lock = threading.Lock()

THREAT_MAP_URL = "https://livethreatmap.radware.com/"
TRUMP_IRAN_QUERY = "Trump talking about Iran speech"


def open_threat_map_and_trump_pip(player=None) -> str:
    """
    1) Open Radware map in the default browser (Edge)
    2) Open ONE YouTube video in Chromium only → Picture-in-Picture
    3) Hide Chromium window, focus back to the map
    Never open YouTube in Edge — that caused double audio.
    """
    import webbrowser

    try:
        webbrowser.open(THREAT_MAP_URL)
        _log(player, f"Opened threat map: {THREAT_MAP_URL}")
    except Exception as e:
        _log(player, f"Threat map failed: {e}")

    video_url = _resolve_trump_iran_url()
    if "watch?v=" not in video_url and "youtu.be/" not in video_url:
        _log(player, "No direct YouTube watch URL — opening search in map browser only")
        try:
            webbrowser.open(video_url)
        except Exception:
            pass
        return "Opened threat map. Could not auto-PiP (no watch URL)."

    _log(player, f"PiP video (Chromium only): {video_url}")
    ok = _launch_youtube_pip_then_focus_map(video_url)
    if ok:
        return "Map open + one Trump/Iran video in PiP; focused back to map."
    return "Opened threat map. PiP failed — check Playwright/Chromium."


def _resolve_trump_iran_url() -> str:
    try:
        from actions.youtube_video import _scrape_first_video_url
        url = _scrape_first_video_url(TRUMP_IRAN_QUERY)
        if url:
            return url
    except Exception as e:
        print(f"[BriefingMedia] YouTube scrape failed: {e}")

    return (
        "https://www.youtube.com/results?search_query="
        + quote_plus(TRUMP_IRAN_QUERY)
    )


def _focus_threat_map_window() -> None:
    """Bring the browser tab/window showing the Radware map back to the front."""
    if sys_platform() != "win32":
        try:
            import webbrowser
            webbrowser.open(THREAT_MAP_URL)
        except Exception:
            pass
        return

    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
        )
        IsWindowVisible = user32.IsWindowVisible
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        SetForegroundWindow = user32.SetForegroundWindow
        ShowWindow = user32.ShowWindow
        SW_RESTORE = 9

        matches: list[int] = []

        def _each(hwnd, _lparam):
            if not IsWindowVisible(hwnd):
                return True
            n = GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value.lower()
            # Prefer Radware / threat map tab titles; also Edge/Chrome with that URL context
            if (
                "radware" in title
                or "threat map" in title
                or "livethreatmap" in title
                or "cyber threat" in title
            ):
                matches.append(hwnd)
            return True

        EnumWindows(EnumWindowsProc(_each), 0)

        if matches:
            hwnd = matches[0]
            ShowWindow(hwnd, SW_RESTORE)
            SetForegroundWindow(hwnd)
            print("[BriefingMedia] Focused threat-map window")
            return

        # Fallback: reopen/focus map URL in default browser
        import webbrowser
        webbrowser.open(THREAT_MAP_URL)
        print("[BriefingMedia] Re-opened map URL to regain focus")
    except Exception as e:
        print(f"[BriefingMedia] Focus map failed: {e}")
        try:
            import webbrowser
            webbrowser.open(THREAT_MAP_URL)
        except Exception:
            pass


def sys_platform() -> str:
    import sys
    return sys.platform


def _minimize_chromium_host(page) -> None:
    """Minimize the Chromium window after PiP so only the floating video remains."""
    try:
        cdp = page.context.new_cdp_session(page)
        info = cdp.send("Browser.getWindowForTarget")
        window_id = info.get("windowId")
        if window_id is not None:
            cdp.send(
                "Browser.setWindowBounds",
                {"windowId": window_id, "bounds": {"windowState": "minimized"}},
            )
            print("[BriefingMedia] Chromium host minimized (CDP)")
            return
    except Exception as e:
        print(f"[BriefingMedia] CDP minimize failed: {e}")

    # Fallback via Win32: minimize Chromium/YouTube windows (never Edge)
    if sys_platform() == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            user32 = ctypes.windll.user32
            EnumWindows = user32.EnumWindows
            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
            )
            IsWindowVisible = user32.IsWindowVisible
            GetWindowTextW = user32.GetWindowTextW
            GetWindowTextLengthW = user32.GetWindowTextLengthW
            ShowWindow = user32.ShowWindow
            SW_MINIMIZE = 6

            def _each(hwnd, _lparam):
                if not IsWindowVisible(hwnd):
                    return True
                n = GetWindowTextLengthW(hwnd)
                if n <= 0:
                    return True
                buf = ctypes.create_unicode_buffer(n + 1)
                GetWindowTextW(hwnd, buf, n + 1)
                title = buf.value.lower()
                if "edge" in title or "microsoft edge" in title:
                    return True
                if "youtube" in title or title.endswith(" chromium"):
                    ShowWindow(hwnd, SW_MINIMIZE)
                return True

            EnumWindows(EnumWindowsProc(_each), 0)
            print("[BriefingMedia] Chromium host minimized (Win32)")
        except Exception as e:
            print(f"[BriefingMedia] Win32 minimize failed: {e}")


def _launch_youtube_pip_then_focus_map(video_url: str) -> bool:
    """Chromium-only YouTube → PiP → minimize host → focus map. No Edge YouTube."""
    global _pip_keepalive

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        print("[BriefingMedia] Playwright not installed — skip auto-PiP")
        return False

    ready_event = threading.Event()
    success_flag = [False]

    def _run():
        global _pip_keepalive
        pw = None
        context = None
        try:
            pw = sync_playwright().start()
            
            # Use a persistent profile so YouTube doesn't think it's a fresh bot every time.
            # Using MS Edge (built-in on Windows) bypasses most Chromium bot blocks.
            import os
            profile_dir = os.path.join(os.path.dirname(__file__), "..", ".jarvis_browser_profile")
            os.makedirs(profile_dir, exist_ok=True)
            
            context = pw.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                channel="msedge",
                headless=False,
                args=[
                    "--autoplay-policy=no-user-gesture-required",
                    "--disable-blink-features=AutomationControlled",
                    "--window-size=800,500",
                ],
                viewport={"width": 800, "height": 480},
                # Let Edge use its own natural User-Agent
            )
            
            page = context.pages[0] if context.pages else context.new_page()
            page.goto(video_url, wait_until="domcontentloaded", timeout=45_000)
            time.sleep(2.5)

            for sel in [
                "button[aria-label='Accept all']",
                "button:has-text('Accept all')",
                "button:has-text('I agree')",
            ]:
                try:
                    loc = page.locator(sel).first
                    if loc.count() and loc.is_visible():
                        loc.click(timeout=1500)
                        time.sleep(0.4)
                except Exception:
                    pass

            page.wait_for_selector("video", timeout=20_000)
            try:
                page.click("video", timeout=5_000)
            except Exception:
                page.keyboard.press("k")
            time.sleep(0.6)

            pip_ok = page.evaluate(
                """
                async () => {
                  const v = document.querySelector('video');
                  if (!v) return false;
                  // Mute so JARVIS briefing audio can be heard
                  v.muted = true;
                  v.volume = 0;
                  try { await v.play(); } catch (e) {}
                  if (document.pictureInPictureElement) return true;
                  try {
                    await v.requestPictureInPicture();
                    return true;
                  } catch (e) {
                    console.log(e);
                    return false;
                  }
                }
                """
            )
            print(f"[BriefingMedia] PiP → {pip_ok} (muted)")

            # Keep it muted even if YouTube tries to unmute
            try:
                page.evaluate(
                    """
                    () => {
                      const v = document.querySelector('video');
                      if (!v) return;
                      v.muted = true;
                      v.volume = 0;
                      v.addEventListener('volumechange', () => {
                        if (!v.muted || v.volume > 0) {
                          v.muted = true;
                          v.volume = 0;
                        }
                      });
                    }
                    """
                )
            except Exception:
                pass

            # Hide the Chromium window so only PiP remains visible
            time.sleep(0.5)
            _minimize_chromium_host(page)

            # Return user to the threat map
            time.sleep(0.4)
            _focus_threat_map_window()

            with _pip_lock:
                old = _pip_keepalive
                _pip_keepalive = {"pw": pw, "context": context, "page": page}
            if old:
                try:
                    if "browser" in old and old["browser"]:
                        old["browser"].close()
                    if "context" in old and old["context"]:
                        old["context"].close()
                except Exception:
                    pass
                try:
                    if "pw" in old and old["pw"]:
                        old["pw"].stop()
                except Exception:
                    pass
            
            success_flag[0] = True
            ready_event.set()

            while True:
                time.sleep(3)
                with _pip_lock:
                    if _pip_keepalive is None or _pip_keepalive.get("page") != page:
                        break
                try:
                    if page.is_closed():
                        break
                except Exception:
                    break
        except Exception as e:
            print(f"[BriefingMedia] PiP launch error: {e}")
            try:
                if context:
                    context.close()
            except Exception:
                pass
            try:
                if pw:
                    pw.stop()
            except Exception:
                pass
            # Still try to show the map
            _focus_threat_map_window()
            ready_event.set()

    threading.Thread(target=_run, daemon=True, name="briefing-pip").start()
    # Wait until PiP is fully loaded, up to a reasonable timeout (15s)
    ready_event.wait(timeout=25.0)
    return success_flag[0]


def _close_threat_map_window() -> None:
    """Closes the Edge/browser windows showing the threat map."""
    if sys_platform() != "win32":
        return
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        EnumWindows = user32.EnumWindows
        EnumWindowsProc = ctypes.WINFUNCTYPE(
            ctypes.c_bool, wintypes.HWND, wintypes.LPARAM
        )
        IsWindowVisible = user32.IsWindowVisible
        GetWindowTextW = user32.GetWindowTextW
        GetWindowTextLengthW = user32.GetWindowTextLengthW
        PostMessageW = user32.PostMessageW
        WM_CLOSE = 0x0010

        def _each(hwnd, _lparam):
            if not IsWindowVisible(hwnd):
                return True
            n = GetWindowTextLengthW(hwnd)
            if n <= 0:
                return True
            buf = ctypes.create_unicode_buffer(n + 1)
            GetWindowTextW(hwnd, buf, n + 1)
            title = buf.value.lower()
            if (
                "radware" in title
                or "threat map" in title
                or "livethreatmap" in title
                or "cyber threat" in title
            ):
                PostMessageW(hwnd, WM_CLOSE, 0, 0)
            return True

        EnumWindows(EnumWindowsProc(_each), 0)
        print("[BriefingMedia] Closed threat-map window")
    except Exception as e:
        print(f"[BriefingMedia] Close map failed: {e}")

def close_briefing_media() -> None:
    """Closes the threat map and PiP video after the news phase."""
    global _pip_keepalive
    with _pip_lock:
        if _pip_keepalive:
            try:
                if "browser" in _pip_keepalive and _pip_keepalive["browser"]:
                    _pip_keepalive["browser"].close()
                if "context" in _pip_keepalive and _pip_keepalive["context"]:
                    _pip_keepalive["context"].close()
            except Exception:
                pass
            try:
                if "pw" in _pip_keepalive and _pip_keepalive["pw"]:
                    _pip_keepalive["pw"].stop()
            except Exception:
                pass
            _pip_keepalive = None
            print("[BriefingMedia] Closed PiP video")
    
    _close_threat_map_window()


def _log(player, msg: str) -> None:
    print(f"[BriefingMedia] {msg}")
    if player:
        try:
            player.write_log(f"SYS: {msg}")
        except Exception:
            pass
