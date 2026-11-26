"""
Cinematic LOCATION FINDER mode — theatrical / video-only.

Keeps the Live WebSocket alive by returning quickly with a speak-script,
while visuals/browsers run on a background thread. Photo stays hidden
until the trace completes.
"""
from __future__ import annotations

import html
import os
import platform
import shutil
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from urllib.parse import quote_plus

_BASE = Path(__file__).resolve().parent.parent
_PHOTO = _BASE / "assets" / "targets" / "mohammed_rozhgar.png"

TARGET = {
    "name": "Mohammed Rozhgar",
    "name_ku": "محەممەد ڕۆژگار",
    "city": "Hawler (Erbil)",
    "district": "Mamostaian",
    "region": "Kurdistan — Iraq",
    "address": "Residential home · Mamostaian quarter",
    "lat": 36.17992,
    "lon": 44.033828,
    "ip": "37.239.148.92",
    "isp": "Newroz Telecom · Erbil NOC",
    "device": "Android · last seen 4m ago",
    "socials": {
        "Instagram": "mohammed.rozhgar",
        "Facebook": "Mohammed Rozhgar",
        "TikTok": "@mohammedrozhgar",
        "Snapchat": "m.rozhgar",
    },
    "photo": str(_PHOTO),
}

_CLOSE_TITLE_HINTS = (
    "instagram",
    "facebook",
    "tiktok",
    "google maps",
    "trace protocol",
    "jarvis_location_dossier",
    "mohammed rozhgar",
)

_STEPS = [
    {"id": "social", "label": "SCANNING SOCIAL NETWORKS", "ui": "Looking for social media…", "delay": 1.5},
    {"id": "match", "label": "PROFILE MATCH LOCKED", "ui": "Social profiles matched…", "delay": 1.6, "open_social": True},
    {"id": "ip", "label": "TRACING IP / NETWORK NODE", "ui": "Finding his IP address…", "delay": 1.5},
    {"id": "geo", "label": "HOME GEOLOCATION LOCK", "ui": "Finding his home location…", "delay": 1.6, "open_map": True},
    {"id": "lock", "label": "RESIDENCE PIN CONFIRMED", "ui": "Home pin confirmed on map", "delay": 1.2, "open_dossier": True},
]

_lock = threading.Lock()
_running = False

# Dedicated browser session for the cinematic scan (safe to kill without
# touching the user's normal Chrome/Edge windows).
_trace_profile_dir: Path | None = None
_trace_browser_proc: subprocess.Popen | None = None
_trace_browser_exe: str | None = None


def _log(msg: str, player=None) -> None:
    print(f"[LocationFinder] {msg}")
    if player is not None:
        try:
            player.write_log(f"LOC: {msg}")
        except Exception:
            pass


def _ui(player, **payload) -> None:
    if player is None:
        return
    try:
        player.show_location_finder(payload)
    except Exception as e:
        print(f"[LocationFinder] UI update failed: {e}")


def _edge_path() -> str | None:
    candidates = [
        shutil.which("msedge"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
        os.path.expandvars(r"%LocalAppData%\Microsoft\Edge\Application\msedge.exe"),
    ]
    for p in candidates:
        if p and Path(p).exists():
            return p
    return None


def _chrome_path() -> str | None:
    candidates = [
        shutil.which("chrome"),
        os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
        os.path.expandvars(r"%LocalAppData%\Google\Chrome\Application\chrome.exe"),
    ]
    for p in candidates:
        if p and Path(p).exists():
            return p
    return None


def _browser_exe() -> str | None:
    return _edge_path() or _chrome_path()


def _ensure_trace_browser() -> str | None:
    """Start (or reuse) a dedicated Edge/Chrome profile for this trace."""
    global _trace_profile_dir, _trace_browser_proc, _trace_browser_exe
    exe = _browser_exe()
    if not exe:
        return None
    _trace_browser_exe = exe
    if _trace_profile_dir is None:
        _trace_profile_dir = Path(tempfile.mkdtemp(prefix="jarvis_trace_browser_"))
    return exe


def _open_url(url: str) -> None:
    """Open URL inside the dedicated killable trace browser window."""
    global _trace_browser_proc
    try:
        exe = _ensure_trace_browser()
        if not exe or _trace_profile_dir is None:
            return
        flags = {}
        if platform.system() == "Windows":
            # Keep the browser window visible — do NOT use CREATE_NO_WINDOW here
            flags["creationflags"] = 0
        # First URL opens a new window on the isolated profile; later URLs add tabs
        if _trace_browser_proc is None or _trace_browser_proc.poll() is not None:
            _trace_browser_proc = subprocess.Popen(
                [
                    exe,
                    f"--user-data-dir={_trace_profile_dir}",
                    "--no-first-run",
                    "--no-default-browser-check",
                    "--new-window",
                    url,
                ],
                **flags,
            )
        else:
            subprocess.Popen(
                [
                    exe,
                    f"--user-data-dir={_trace_profile_dir}",
                    "--no-first-run",
                    "--new-tab",
                    url,
                ],
                **flags,
            )
    except Exception as e:
        print(f"[LocationFinder] open failed: {e}")


def _social_urls(handle_ig: str) -> list[str]:
    q = quote_plus(TARGET["name"])
    ig = quote_plus(handle_ig)
    return [
        f"https://www.instagram.com/{handle_ig}/",
        f"https://www.facebook.com/search/top/?q={q}",
        f"https://www.tiktok.com/search?q={ig}",
    ]


def _maps_url() -> str:
    lat, lon = TARGET["lat"], TARGET["lon"]
    label = quote_plus(f"Residence · {TARGET['district']}, Hawler")
    return (
        f"https://www.google.com/maps?q={lat},{lon}+({label})"
        f"&ll={lat},{lon}&z=20&t=k&hl=en"
    )


def _maps_embed_url() -> str:
    lat, lon = TARGET["lat"], TARGET["lon"]
    return (
        f"https://maps.google.com/maps?q={lat},{lon}"
        f"&ll={lat},{lon}&z=19&t=k&output=embed"
    )


def _kill_processes_using_profile(profile: Path) -> int:
    """Hard-kill Chromium processes whose command line uses our profile dir."""
    if platform.system() != "Windows":
        return 0
    killed = 0
    needle = str(profile).lower().replace("/", "\\")
    try:
        # PowerShell is reliable for command-line matching
        ps = (
            "Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | "
            "Where-Object { $_.Name -match '^(msedge|chrome)\\.exe$' -and "
            f"$_.CommandLine -and $_.CommandLine.ToLower().Contains('{needle.replace(chr(39), '')}') }} | "
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
    except Exception as e:
        print(f"[LocationFinder] profile kill failed: {e}")
    return killed


def _close_trace_browsers(player=None) -> int:
    """Force-close the dedicated trace browser, then any leftover matching windows."""
    global _trace_browser_proc, _trace_profile_dir
    closed = 0

    # 1) Kill the process tree we spawned
    proc = _trace_browser_proc
    _trace_browser_proc = None
    if proc is not None:
        try:
            if platform.system() == "Windows":
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=8,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                closed += 1
            else:
                proc.terminate()
                closed += 1
        except Exception as e:
            print(f"[LocationFinder] taskkill failed: {e}")
            try:
                proc.kill()
                closed += 1
            except Exception:
                pass

    # 2) Kill any Chromium still holding our temp profile
    if _trace_profile_dir is not None:
        closed += _kill_processes_using_profile(_trace_profile_dir)
        try:
            shutil.rmtree(_trace_profile_dir, ignore_errors=True)
        except Exception:
            pass
        _trace_profile_dir = None

    # 3) Fallback: close windows by title (instagram / maps / etc.)
    if platform.system() == "Windows":
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
                if "mark xlix" in title or "cursor" in title:
                    return True
                if any(h in title for h in _CLOSE_TITLE_HINTS):
                    matches.append(hwnd)
                return True

            EnumWindows(EnumWindowsProc(_each), 0)
            for hwnd in matches:
                try:
                    PostMessageW(hwnd, WM_CLOSE, 0, 0)
                    closed += 1
                    time.sleep(0.15)
                except Exception:
                    pass
        except Exception as e:
            print(f"[LocationFinder] title-close failed: {e}")

    _log(f"Closed trace browser session ({closed} actions)", player)
    return closed


def _focus_jarvis(player=None) -> None:
    if player is None:
        return
    try:
        if hasattr(player, "focus_main_window"):
            player.focus_main_window()
    except Exception as e:
        print(f"[LocationFinder] focus failed: {e}")


def _build_dossier_html(query_number: str) -> Path:
    photo_uri = Path(TARGET["photo"]).resolve().as_uri() if Path(TARGET["photo"]).exists() else ""
    social_rows = "".join(
        f'<div class="row"><span class="k">{html.escape(k)}</span>'
        f'<span class="v">@{html.escape(v.lstrip("@"))}</span>'
        f'<span class="ok">MATCH</span></div>'
        for k, v in TARGET["socials"].items()
    )
    lat, lon = TARGET["lat"], TARGET["lon"]
    maps_embed = _maps_embed_url()
    number = html.escape(query_number or "••••••••••")
    name = html.escape(TARGET["name"])
    name_ku = html.escape(TARGET["name_ku"])
    city = html.escape(TARGET["city"])
    district = html.escape(TARGET["district"])
    region = html.escape(TARGET["region"])
    address = html.escape(TARGET["address"])
    ip = html.escape(TARGET["ip"])
    isp = html.escape(TARGET["isp"])
    device = html.escape(TARGET["device"])
    ig = html.escape(TARGET["socials"]["Instagram"])

    doc = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8"/>
<title>TRACE PROTOCOL — {name}</title>
<style>
  :root {{
    --cyan:#00e5ff; --amber:#ffb020; --green:#00ff9a; --red:#ff3355;
    --text:#b8f6ff; --muted:#3d8a9c; --line:rgba(0,229,255,.22);
  }}
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{
    min-height:100vh; font-family:Consolas,"Courier New",monospace; color:var(--text);
    background:radial-gradient(1200px 600px at 70% -10%,rgba(0,120,160,.25),transparent 55%),
      linear-gradient(180deg,#01060a 0%,#021018 55%,#01070c 100%);
  }}
  body::before {{
    content:""; position:fixed; inset:0; pointer-events:none; z-index:5;
    background:repeating-linear-gradient(0deg,transparent,transparent 2px,rgba(0,255,255,.03) 3px);
  }}
  header {{
    display:flex; justify-content:space-between; align-items:center;
    padding:18px 28px; border-bottom:1px solid var(--line); letter-spacing:2px; font-size:12px;
  }}
  .badge {{ color:var(--red); border:1px solid rgba(255,51,85,.5); padding:4px 10px; }}
  .wrap {{ display:grid; grid-template-columns:340px 1fr; gap:18px; padding:18px 22px 28px; position:relative; z-index:2; }}
  .card {{ background:rgba(0,18,28,.88); border:1px solid var(--line); border-radius:10px; padding:16px; }}
  .photo-wrap {{ position:relative; overflow:hidden; border-radius:8px; border:1px solid #007a99; aspect-ratio:3/4; background:#000; }}
  .photo-wrap img {{ width:100%; height:100%; object-fit:cover; display:block; animation:reveal 1.1s ease-out; }}
  @keyframes reveal {{ from {{ opacity:0; transform:scale(1.08); filter:blur(8px); }} to {{ opacity:1; transform:none; filter:none; }} }}
  .photo-scan {{ position:absolute; left:0; right:0; height:3px; background:var(--cyan); box-shadow:0 0 16px var(--cyan); animation:pscan 2.2s ease-in-out infinite; z-index:2; }}
  @keyframes pscan {{ 0%,100% {{ top:8%; }} 50% {{ top:88%; }} }}
  h1 {{ margin-top:14px; font-size:22px; color:#fff; }}
  .ku {{ color:var(--amber); font-size:14px; margin-top:4px; }}
  .meta {{ margin-top:12px; font-size:12px; line-height:1.7; color:var(--muted); }}
  .meta b {{ color:var(--cyan); }}
  .steps {{ display:flex; flex-direction:column; gap:10px; margin-bottom:14px; }}
  .step {{ display:flex; gap:12px; align-items:center; padding:10px 12px; border:1px solid rgba(0,229,255,.12); border-radius:6px; opacity:.35; }}
  .step.done {{ opacity:1; border-color:rgba(0,255,154,.4); }}
  .dot {{ width:10px; height:10px; border-radius:50%; background:var(--muted); }}
  .step.done .dot {{ background:var(--green); box-shadow:0 0 10px var(--green); }}
  .grid2 {{ display:grid; grid-template-columns:1fr 1fr; gap:14px; }}
  .row {{ display:grid; grid-template-columns:110px 1fr auto; gap:8px; padding:8px 0; border-bottom:1px solid rgba(0,229,255,.08); font-size:13px; }}
  .k {{ color:var(--muted); }} .v {{ color:#fff; }}
  .ok {{ color:var(--green); font-size:10px; border:1px solid rgba(0,255,154,.35); padding:2px 6px; }}
  .map {{ margin-top:14px; border-radius:8px; overflow:hidden; border:1px solid var(--line); height:340px; position:relative; }}
  .map iframe {{ width:100%; height:100%; border:0; }}
  .map-tag {{ position:absolute; top:12px; left:12px; z-index:2; background:rgba(0,8,14,.82); border:1px solid var(--cyan); color:var(--cyan); font-size:11px; padding:6px 10px; }}
  .lock {{ margin-top:14px; text-align:center; letter-spacing:3px; color:var(--green); font-size:14px; text-shadow:0 0 18px rgba(0,255,154,.45); }}
</style>
</head>
<body>
  <header>
    <div>◈ TRACE PROTOCOL · NODE 07 · LIVE</div>
    <div class="badge">● CLASSIFIED FEED</div>
    <div>HOME LOCK</div>
  </header>
  <div class="wrap">
    <section class="card">
      <div class="photo-wrap">
        <div class="photo-scan"></div>
        {"<img src='" + photo_uri + "' alt='target'/>" if photo_uri else ""}
      </div>
      <h1>{name}</h1>
      <div class="ku">{name_ku}</div>
      <div class="meta">
        <div><b>QUERY</b> · {number}</div>
        <div><b>HOME</b> · {address}</div>
        <div><b>LOC</b> · {district}, {city}</div>
        <div><b>REGION</b> · {region}</div>
        <div><b>IP</b> · {ip}</div>
        <div><b>ISP</b> · {isp}</div>
        <div><b>DEVICE</b> · {device}</div>
        <div><b>GPS</b> · {lat}, {lon}</div>
      </div>
      <div class="lock">◆ TARGET LOCKED — HOME · MAMOSTAIAN / HAWLER</div>
    </section>
    <section class="card">
      <div class="steps">
        <div class="step done"><span class="dot"></span><span>SCANNING SOCIAL NETWORKS</span></div>
        <div class="step done"><span class="dot"></span><span>PROFILE MATCH LOCKED</span></div>
        <div class="step done"><span class="dot"></span><span>TRACING IP / NETWORK NODE</span></div>
        <div class="step done"><span class="dot"></span><span>HOME GEOLOCATION LOCK</span></div>
        <div class="step done"><span class="dot"></span><span>RESIDENCE PIN CONFIRMED</span></div>
      </div>
      <div class="grid2">
        <div>
          <div style="color:var(--cyan);letter-spacing:2px;font-size:11px;margin-bottom:8px">SOCIAL HITS</div>
          {social_rows}
        </div>
        <div>
          <div style="color:var(--cyan);letter-spacing:2px;font-size:11px;margin-bottom:8px">SIGNAL LOG</div>
          <div style="background:rgba(0,0,0,.45);border:1px solid var(--line);border-radius:6px;padding:10px;line-height:1.65;font-size:12px">
            &gt; HIT · Instagram @{ig}<br/>
            &gt; IP {ip} · {isp}<br/>
            &gt; HOME · {district}, Hawler<br/>
            &gt; GPS {lat}, {lon}<br/>
            &gt; satellite pin… DONE
          </div>
        </div>
      </div>
      <div class="map">
        <div class="map-tag">SATELLITE · HOME PIN · {district.upper()}</div>
        <iframe src="{maps_embed}" loading="lazy"></iframe>
      </div>
    </section>
  </div>
</body>
</html>
"""
    out = Path(tempfile.gettempdir()) / "jarvis_location_dossier.html"
    out.write_text(doc, encoding="utf-8")
    return out


def _base_payload(query: str, **extra) -> dict:
    payload = {
        "visible": True,
        "query": query,
        "photo": TARGET["photo"],
        "reveal_photo": False,
        "steps": [s["ui"] for s in _STEPS],
        "done": False,
    }
    payload.update(extra)
    return payload


def _run_cinematic(query: str, player=None) -> None:
    """Background: UI steps → browsers → reveal photo → wait for speech → close."""
    global _running
    try:
        _ui(
            player,
            **_base_payload(
                query,
                phase="boot",
                title="LOCATION FINDER",
                subtitle="TRACE PROTOCOL ONLINE",
                name="UNKNOWN SUBJECT",
                name_ku="… خەریکی شیکردنەوە",
                step=-1,
                detail="Decrypting signal / loading face match…",
            ),
        )
        time.sleep(0.8)

        for idx, step in enumerate(_STEPS):
            _ui(
                player,
                **_base_payload(
                    query,
                    phase=step["id"],
                    title="LOCATION FINDER",
                    subtitle=step["label"],
                    name="UNKNOWN SUBJECT",
                    name_ku="… خەریکی شیکردنەوە",
                    step=idx,
                    detail=step["ui"],
                    ip=TARGET["ip"] if idx >= 2 else "",
                    isp=TARGET["isp"] if idx >= 2 else "",
                    location=(
                        f"{TARGET['address']} · {TARGET['district']}"
                        if idx >= 3
                        else ""
                    ),
                    socials=TARGET["socials"] if idx >= 1 else {},
                    lat=TARGET["lat"] if idx >= 3 else None,
                    lon=TARGET["lon"] if idx >= 3 else None,
                ),
            )
            _log(step["ui"], player)

            if step.get("open_social"):
                for url in _social_urls(TARGET["socials"]["Instagram"]):
                    _open_url(url)
                    time.sleep(0.4)

            if step.get("open_map"):
                _open_url(_maps_url())

            if step.get("open_dossier"):
                # Brief beat on the map tab, then kill browsers and show in-app dossier
                time.sleep(1.4)
                _ui(
                    player,
                    **_base_payload(
                        query,
                        phase="secure",
                        title="SECURING FEED",
                        subtitle="Closing trace browsers…",
                        name="UNKNOWN SUBJECT",
                        name_ku="… خەریکی شیکردنەوە",
                        step=idx,
                        detail="Shutting down open-source windows",
                        ip=TARGET["ip"],
                        isp=TARGET["isp"],
                        location=f"{TARGET['address']} · {TARGET['district']}",
                        socials=TARGET["socials"],
                        lat=TARGET["lat"],
                        lon=TARGET["lon"],
                    ),
                )
                _close_trace_browsers(player)
                time.sleep(0.35)

                dossier = _build_dossier_html(query)
                shown_in_app = False
                if player is not None:
                    try:
                        player.show_location_dossier(str(dossier.resolve()))
                        shown_in_app = True
                        _log("Dossier opened in-app", player)
                    except Exception as e:
                        print(f"[LocationFinder] in-app dossier failed: {e}")
                if not shown_in_app:
                    # Last resort only — should not happen with WebEngine installed
                    _open_url(dossier.resolve().as_uri())
                _focus_jarvis(player)

            time.sleep(step["delay"])

        loc_line = f"{TARGET['address']} — {TARGET['district']}, {TARGET['city']}"
        # Reveal face ONLY now (inside the in-app dossier)
        _ui(
            player,
            **_base_payload(
                query,
                phase="lock",
                title="TARGET LOCKED",
                subtitle=loc_line,
                name=TARGET["name"],
                name_ku=TARGET["name_ku"],
                step=len(_STEPS),
                detail="HERE IS THE PERSON YOU WERE LOOKING FOR",
                ip=TARGET["ip"],
                isp=TARGET["isp"],
                location=loc_line,
                socials=TARGET["socials"],
                lat=TARGET["lat"],
                lon=TARGET["lon"],
                reveal_photo=True,
                done=True,
            ),
        )
        if player is not None:
            try:
                social_txt = ", ".join(f"{k}: {v}" for k, v in TARGET["socials"].items())
                player.show_content(
                    "LOCATION LOCK",
                    (
                        f"NAME: {TARGET['name']} ({TARGET['name_ku']})\n"
                        f"QUERY: {query}\n"
                        f"SOCIAL: {social_txt}\n"
                        f"IP: {TARGET['ip']}  ·  {TARGET['isp']}\n"
                        f"HOME: {loc_line}\n"
                        f"GPS: {TARGET['lat']}, {TARGET['lon']}\n"
                        f"DEVICE: {TARGET['device']}"
                    ),
                )
            except Exception:
                pass

        _focus_jarvis(player)
        _log("Face revealed — holding for voice readout (browsers already closed)", player)
        # Hold on the beautiful in-app dossier while he reads the findings
        time.sleep(22.0)

        _ui(
            player,
            **_base_payload(
                query,
                phase="complete",
                title="TRACE COMPLETE",
                subtitle=f"{TARGET['name']} · {TARGET['district']}",
                name=TARGET["name"],
                name_ku=TARGET["name_ku"],
                step=len(_STEPS),
                detail="Dossier secured — browsers closed",
                ip=TARGET["ip"],
                isp=TARGET["isp"],
                location=loc_line,
                socials=TARGET["socials"],
                lat=TARGET["lat"],
                lon=TARGET["lon"],
                reveal_photo=True,
                done=True,
            ),
        )
        _log("Trace complete — in-app dossier locked", player)
    finally:
        with _lock:
            _running = False


def _speak_script(query: str) -> str:
    s = TARGET["socials"]
    return (
        "LOCATION FINDER is running on screen (face hidden until the end — do not mention fakeness).\n"
        "Speak ONLY Kurdish Sorani. Slow, calm, cinematic. Pause between sentences.\n"
        "CRITICAL NAME RULE: During steps 1–4 you do NOT know the person's name yet. "
        "NEVER say محەممەد / ڕۆژگار / Mohammed / any name before step 5. "
        "You are searching FROM THE NUMBER only.\n"
        "Follow this exact beat:\n"
        f"1) Say you will search social media for this number: {query}. "
        "Example meaning: گەورەم، ئێستا بەدوای سۆشیال میدیای ئەم ژمارەیەدا دەگەڕێم.\n"
        "2) Short line: social accounts found linked to this number "
        "(NO name — just say accounts / profiles matched).\n"
        "3) Short line: tracing his IP now (still NO name).\n"
        "4) Short line: home location locked — مامۆستایان، هەولێر — map opening (still NO name).\n"
        "5) ONLY NOW reveal identity. Say clearly:\n"
        "   گەورەم، ئەمە ئەو کەسەیە کە بەدوایدا دەگەڕایت.\n"
        "   Then READ the findings aloud:\n"
        f"   - ناو: {TARGET['name_ku']} / {TARGET['name']}\n"
        f"   - ژمارە: {query}\n"
        f"   - ئینستاگرام: {s['Instagram']}\n"
        f"   - فەیسبووک: {s['Facebook']}\n"
        f"   - تیک تۆک: {s['TikTok']}\n"
        f"   - سنەپچات: {s['Snapchat']}\n"
        f"   - IP: {TARGET['ip']} ({TARGET['isp']})\n"
        f"   - ماڵ: گەڕەکی مامۆستایان، هەولێر، کوردستان\n"
        f"   - GPS: {TARGET['lat']}, {TARGET['lon']}\n"
        "Do NOT say the name before step 5. Do NOT skip the readout. "
        "Do NOT speak English. Do NOT call any tools."
    )


def location_finder(parameters: dict | None = None, player=None, speak=None) -> str:
    """
    Start cinematic trace in a background thread and return immediately
    so Gemini Live can speak the full report (avoids keepalive timeout).
    """
    global _running
    params = parameters or {}
    query = (
        params.get("query")
        or params.get("number")
        or params.get("phone")
        or params.get("username")
        or params.get("image")
        or "unknown"
    )
    query = str(query).strip() or "unknown"

    with _lock:
        if _running:
            return _speak_script(query)
        _running = True

    _log(f"Trace started (async) — query={query!r}", player)
    # Show loading overlay immediately (NO face yet)
    _ui(
        player,
        **_base_payload(
            query,
            phase="boot",
            title="LOCATION FINDER",
            subtitle="TRACE PROTOCOL ONLINE",
            name="UNKNOWN SUBJECT",
            name_ku="… خەریکی شیکردنەوە",
            step=-1,
            detail="Decrypting signal / loading face match…",
        ),
    )

    threading.Thread(
        target=_run_cinematic,
        kwargs={"query": query, "player": player},
        daemon=True,
        name="location-finder",
    ).start()

    # Tiny pause so overlay paints before the model starts talking
    time.sleep(0.35)
    return _speak_script(query)
