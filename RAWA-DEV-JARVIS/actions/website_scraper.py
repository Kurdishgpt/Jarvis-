"""Website mirror / source scraper (بردنی وێبسایت)."""

from __future__ import annotations

import mimetypes
import os
import re
import sys
import ctypes
from collections import deque
from pathlib import Path
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
TIMEOUT = 30
LAST_SCRAPE_FILE = Path(__file__).resolve().parent.parent / ".last_scrape_folder"

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("application/javascript", ".mjs")

ASSET_EXTS = {
    ".css", ".js", ".mjs", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg",
    ".ico", ".avif", ".bmp", ".woff", ".woff2", ".ttf", ".eot", ".otf",
    ".mp4", ".webm", ".mp3", ".wav", ".json", ".map", ".txt", ".xml",
}
SKIP_LINK_REL = {
    "canonical", "alternate", "preconnect", "dns-prefetch", "pingback",
    "me", "author", "shortlink",
}

CSS_URL_RE = re.compile(r"""url\(\s*['"]?([^'")]+)['"]?\s*\)""", re.IGNORECASE)
IMPORT_RE = re.compile(
    r"""@import\s+(?:url\(\s*)?['"]?([^"')\s]+)['"]?\s*\)?""",
    re.IGNORECASE,
)
NEXT_STATIC_RE = re.compile(r"/_next/static/[A-Za-z0-9._\-()/]+")
STATIC_BUNDLE_RE = re.compile(
    r"(?<![A-Za-z0-9/])static/(?:chunks|css|media)/[A-Za-z0-9._\-()/]+"
)
NEXT_IMAGE_RE = re.compile(r"/_next/image\?url=([^&\"'\s]+)")
QUOTED_ASSET_RE = re.compile(
    r"""['"](/[^\"']+\.(?:css|js|mjs|png|jpe?g|gif|webp|svg|ico|avif|woff2?|ttf|eot|otf|mp4|webm|json))(?:\?[^\"']*)?['"]"""
)
WEBPACK_U_RE = re.compile(
    r'"static/chunks/"\+\(\(\{([^}]+)\}\)\[e\]\|\|e\)\+"\."\+\(\{([^}]+)\}\)',
)
SRCSET_SPLIT_RE = re.compile(r"\s*,\s*")

MIRROR_RUN_PY = r'''
"""Open this scraped site in the browser (local HTTP server).

From this folder run:  python run.py
Stop with Enter in the terminal.
"""

from __future__ import annotations

import http.server
import mimetypes
import os
import socketserver
import sys
import threading
import webbrowser
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parent
DEFAULT_PORT = 8765
OPEN_PATH = __OPEN_PATH__

mimetypes.add_type("font/woff2", ".woff2")
mimetypes.add_type("font/woff", ".woff")
mimetypes.add_type("image/avif", ".avif")
mimetypes.add_type("application/javascript", ".mjs")


class MirrorHTTPRequestHandler(http.server.SimpleHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        path = unquote(parsed.path)

        if path.rstrip("/").endswith("/_next/image"):
            qs = parse_qs(parsed.query)
            src = unquote((qs.get("url") or [""])[0])
            if src:
                if src.startswith(("http://", "https://")):
                    src = urlparse(src).path
                if not src.startswith("/"):
                    src = "/" + src
                self.path = src
                path = unquote(urlparse(self.path).path)

        if path.startswith("/api/"):
            local = Path(self.translate_path(self.path))
            if not local.is_file():
                body = b"{}"
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

        fs = Path(self.translate_path(self.path))
        if fs.is_dir() and (fs / "index.html").is_file():
            slash = "" if path.endswith("/") else "/"
            self.path = path + slash + "index.html"

        return super().do_GET()


def _layout_console_left() -> None:
    """Keep CMD on the left half — not fullscreen."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
        hwnd = kernel32.GetConsoleWindow()
        if not hwnd:
            return
        sw = user32.GetSystemMetrics(0)
        sh = user32.GetSystemMetrics(1)
        w = max(640, sw // 2 - 12)
        h = max(420, sh - 72)
        user32.MoveWindow(hwnd, 8, 8, w, h, True)
    except Exception:
        pass


def _open_browser_right(url: str) -> None:
    """Open browser windowed on the right — CMD stays visible on the left."""
    if sys.platform == "win32":
        try:
            import ctypes
            user32 = ctypes.windll.user32
            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            left = sw // 2 + 4
            top = 8
            width = max(640, sw // 2 - 16)
            height = max(420, sh - 72)
            size = f"--window-size={width},{height}"
            pos = f"--window-position={left},{top}"
            candidates = [
                os.path.expandvars(r"%ProgramFiles%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe"),
                os.path.expandvars(r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"),
                os.path.expandvars(r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe"),
            ]
            for exe in candidates:
                if exe and os.path.isfile(exe):
                    import subprocess
                    subprocess.Popen(
                        [exe, "--new-window", size, pos, url],
                        close_fds=True,
                    )
                    return
        except Exception:
            pass
    try:
        import webbrowser
        webbrowser.open(url, new=1)
    except Exception:
        pass


def main() -> None:
    os.chdir(ROOT)
    if not any(ROOT.rglob("index.html")):
        print("Missing index.html in this folder.", file=sys.stderr)
        sys.exit(1)

    class ReuseAddrTCPServer(socketserver.TCPServer):
        allow_reuse_address = True

    port = DEFAULT_PORT
    httpd = None
    for _ in range(40):
        try:
            httpd = ReuseAddrTCPServer(("", port), MirrorHTTPRequestHandler)
            break
        except OSError:
            port += 1

    if httpd is None:
        print("Could not bind a local port.", file=sys.stderr)
        sys.exit(1)

    page = OPEN_PATH if OPEN_PATH.startswith("/") else "/" + OPEN_PATH
    url = f"http://127.0.0.1:{port}{page}"
    print(f"Serving folder: {ROOT}")
    print(f"Open: {url}")

    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    _layout_console_left()
    _open_browser_right(url)

    input("Press Enter to stop the server...\n")
    httpd.shutdown()
    httpd.server_close()


if __name__ == "__main__":
    main()
'''.lstrip("\n")


def folder_name_for_url(page_url: str) -> str:
    p = urlparse(page_url)
    host = p.netloc or "site"
    safe = re.sub(r'[<>:"/\\|?*]', "_", host)
    return safe or "site"


def same_origin(abs_url: str, origin: str) -> bool:
    a = urlparse(abs_url)
    o = urlparse(origin)
    if a.scheme in ("data", "javascript", "mailto", "tel", "blob"):
        return False
    if not a.netloc:
        return True
    return a.scheme in ("http", "https") and a.netloc == o.netloc


def local_relpath(abs_url: str, origin: str) -> str | None:
    if not same_origin(abs_url, origin):
        return None
    p = urlparse(abs_url)
    path = unquote(p.path) or "/"

    if path.rstrip("/").endswith("/_next/image"):
        src = unquote((parse_qs(p.query).get("url") or [""])[0])
        if not src:
            return None
        return local_relpath(urljoin(origin, src), origin)

    if path.endswith("/"):
        rel = path.lstrip("/") + "index.html"
    else:
        rel = path.lstrip("/")
        if not rel:
            rel = "index.html"
        elif not Path(rel).suffix:
            rel = rel + "/index.html"
    rel = rel.replace("\\", "/")
    if ".." in Path(rel).parts:
        return None
    return rel


def looks_like_asset(abs_url: str) -> bool:
    path = urlparse(abs_url).path
    if "/_next/" in path:
        return True
    return Path(path).suffix.lower() in ASSET_EXTS


def srcset_urls(value: str) -> list[str]:
    out: list[str] = []
    for part in SRCSET_SPLIT_RE.split(value or ""):
        part = part.strip()
        if not part:
            continue
        out.append(part.split()[0])
    return out


def webpack_chunk_paths(js_text: str) -> set[str]:
    paths: set[str] = set()
    for m in re.finditer(r"static/(?:chunks|css|media)/[A-Za-z0-9._\-()/]+", js_text):
        paths.add(m.group(0))
    m = WEBPACK_U_RE.search(js_text)
    if m:
        name_map = dict(re.findall(r'(\d+):"([^"]+)"', m.group(1)))
        hash_map = dict(re.findall(r'(\d+):"([^"]+)"', m.group(2)))
        for cid, digest in hash_map.items():
            name = name_map.get(cid, cid)
            paths.add(f"static/chunks/{name}.{digest}.js")
    return paths


def clean_asset_path(path: str) -> str | None:
    path = path.strip()
    while path.endswith(")") and path.count("(") < path.count(")"):
        path = path[:-1]
    path = path.rstrip("/")
    if not path or not Path(path).suffix:
        return None
    return path


def refs_from_text(text: str, file_url: str, origin: str) -> set[str]:
    found: set[str] = set()
    base_next = urljoin(origin, "/_next/")

    for m in NEXT_STATIC_RE.finditer(text):
        cleaned = clean_asset_path(m.group(0))
        if cleaned:
            found.add(urljoin(origin, cleaned))
    for m in STATIC_BUNDLE_RE.finditer(text):
        cleaned = clean_asset_path(m.group(0))
        if cleaned:
            found.add(urljoin(base_next, cleaned))
    for m in NEXT_IMAGE_RE.finditer(text):
        src = unquote(m.group(1))
        found.add(urljoin(origin, src))
    for m in QUOTED_ASSET_RE.finditer(text):
        found.add(urljoin(origin, m.group(1)))

    if file_url.endswith(".css") or "/_next/static/css/" in file_url:
        for m in CSS_URL_RE.finditer(text):
            raw = m.group(1).strip()
            if raw.startswith(("data:", "#")) or not raw:
                continue
            found.add(urljoin(file_url, raw))
        for m in IMPORT_RE.finditer(text):
            raw = m.group(1).strip()
            if raw.startswith("data:") or not raw:
                continue
            found.add(urljoin(file_url, raw))

    if file_url.endswith((".js", ".mjs")) or "/_next/static/chunks/" in file_url:
        for rel in webpack_chunk_paths(text):
            found.add(urljoin(base_next, rel))

    return found


def html_seed_urls(html: str, page_url: str, origin: str) -> set[str]:
    found = refs_from_text(html, page_url, origin)
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup.find_all("link"):
        rel = tag.get("rel") or []
        if isinstance(rel, list):
            rel_l = [str(x).lower() for x in rel]
        else:
            rel_l = [str(rel).lower()]
        if SKIP_LINK_REL.intersection(rel_l):
            continue
        href = tag.get("href")
        if href:
            found.add(urljoin(page_url, href))

    for tag in soup.find_all("script"):
        src = tag.get("src")
        if src:
            found.add(urljoin(page_url, src))

    for tag in soup.find_all(["img", "source", "video", "audio", "use", "image"]):
        for attr in ("src", "href", "poster", "data-src"):
            val = tag.get(attr)
            if val:
                found.add(urljoin(page_url, val))
        for attr in ("srcset", "data-srcset"):
            val = tag.get(attr)
            if val:
                for u in srcset_urls(val):
                    found.add(urljoin(page_url, u))

    for tag in soup.find_all("meta"):
        key = (tag.get("property") or tag.get("name") or "").lower()
        if key in {"og:image", "twitter:image", "og:image:url"}:
            content = tag.get("content")
            if content:
                found.add(urljoin(page_url, content))

    return found


def page_open_path(page_url: str) -> str:
    path = urlparse(page_url).path or "/"
    if not path.startswith("/"):
        path = "/" + path
    if path != "/" and path.endswith("/"):
        path = path.rstrip("/")
    return path or "/"


def write_mirror_runner(site_dir: Path, open_path: str = "/") -> None:
    text = MIRROR_RUN_PY.replace("__OPEN_PATH__", repr(open_path))
    (site_dir / "run.py").write_text(text, encoding="utf-8")


def fetch_bytes(session: requests.Session, url: str) -> tuple[bytes, str | None]:
    r = session.get(url, timeout=TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("Content-Type", "")
    return r.content, ctype.split(";")[0].strip() if ctype else None


def list_source_tree(site_dir: Path, limit: int = 120) -> list[str]:
    files: list[str] = []
    for p in sorted(site_dir.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(site_dir).as_posix()
        files.append(rel)
        if len(files) >= limit:
            files.append("…")
            break
    return files


def scrape_website(url: str, out_root: Path | None = None) -> dict:
    raw = (url or "").strip()
    if not raw:
        raise ValueError("No URL given.")
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw

    if out_root is None:
        if getattr(sys, "frozen", False):
            out_root = Path(sys.executable).parent
        else:
            out_root = Path(__file__).resolve().parent.parent

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    r0 = session.get(raw, timeout=TIMEOUT, allow_redirects=True)
    r0.raise_for_status()
    final_url = r0.url
    origin = f"{urlparse(final_url).scheme}://{urlparse(final_url).netloc}"
    open_path = page_open_path(final_url)

    site_dir = out_root / folder_name_for_url(final_url)
    site_dir.mkdir(parents=True, exist_ok=True)

    html_bytes = r0.content
    try:
        html_text = html_bytes.decode(r0.encoding or "utf-8", errors="replace")
    except LookupError:
        html_text = html_bytes.decode("utf-8", errors="replace")

    html_rel = local_relpath(final_url, origin) or "index.html"
    html_dest = site_dir / html_rel
    html_dest.parent.mkdir(parents=True, exist_ok=True)
    html_dest.write_bytes(html_bytes)

    if html_rel != "index.html" and not (site_dir / "index.html").is_file():
        dest = open_path if open_path.startswith("/") else "/" + open_path
        (site_dir / "index.html").write_text(
            "<!DOCTYPE html>\n"
            f'<meta http-equiv="refresh" content="0; url={dest}">\n'
            f"<script>location.replace({dest!r})</script>\n",
            encoding="utf-8",
        )

    seen: set[str] = {final_url.split("#")[0]}
    queue: deque[str] = deque()

    def enqueue(abs_url: str) -> None:
        abs_url = abs_url.split("#")[0]
        if not abs_url or abs_url.startswith(("data:", "javascript:", "mailto:", "tel:")):
            return
        if abs_url in seen:
            return
        if not same_origin(abs_url, origin):
            return
        if not looks_like_asset(abs_url) and "/_next/" not in abs_url:
            return
        seen.add(abs_url)
        queue.append(abs_url)

    for u in html_seed_urls(html_text, final_url, origin):
        enqueue(u)

    saved = 1
    while queue:
        abs_url = queue.popleft()
        rel = local_relpath(abs_url, origin)
        if not rel:
            continue
        dest = site_dir / rel
        if dest.is_file() and dest.resolve() != html_dest.resolve():
            try:
                text = dest.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if Path(rel).suffix.lower() in {".css", ".js", ".mjs", ".html", ".json"}:
                for u in refs_from_text(text, abs_url, origin):
                    enqueue(u)
            continue

        try:
            data, ctype = fetch_bytes(session, abs_url)
        except Exception:
            continue

        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        saved += 1

        suffix = Path(rel).suffix.lower()
        scanable = suffix in {".css", ".js", ".mjs", ".html", ".json"}
        if ctype and ("javascript" in ctype or ctype.startswith("text/")):
            scanable = True
        if scanable:
            try:
                text = data.decode("utf-8", errors="ignore")
            except Exception:
                continue
            for u in refs_from_text(text, abs_url, origin):
                enqueue(u)

    write_mirror_runner(site_dir, open_path)
    tree = list_source_tree(site_dir)
    return {
        "ok": True,
        "url": final_url,
        "folder": str(site_dir),
        "folder_name": site_dir.name,
        "files": saved,
        "tree": tree,
        "open_path": open_path,
    }


def website_scraper(parameters: dict, player=None) -> str:
    url = (parameters or {}).get("url") or ""
    data = scrape_website(url)
    folder = data["folder"]
    tree = "\n".join(data["tree"][:80])
    if player:
        try:
            player.show_content(
                "SOURCE LOCKED",
                f"{data['url']}\n\n{folder}\n\n{tree}",
            )
        except Exception:
            pass
        try:
            if os.name == "nt":
                os.startfile(folder)  # noqa: S606
        except Exception:
            pass
    return (
        f"MIRROR COMPLETE. folder={folder} files≈{data['files']}\n"
        f"Source tree:\n{tree}\n"
        "Tell the user in cinematic Sorani that the website source has been taken "
        "and saved. Mention the folder name. Do not read the whole tree aloud."
    )


def remember_scrape_folder(folder: str | Path) -> None:
    path = str(Path(folder).resolve())
    try:
        LAST_SCRAPE_FILE.write_text(path, encoding="utf-8")
    except OSError:
        pass


def last_scrape_folder() -> str | None:
    try:
        raw = LAST_SCRAPE_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if raw and Path(raw).is_dir():
        return raw
    return None


def run_scraped_site(folder: str | None = None) -> str:
    """Open a visible terminal, cd into the mirror, run python run.py."""
    target = (folder or "").strip() or last_scrape_folder() or ""
    site_dir = Path(target).resolve() if target else None
    if site_dir is None or not site_dir.is_dir():
        return "No scraped website folder found. Scrape a site first."

    open_path = "/"
    runner = site_dir / "run.py"
    if runner.is_file():
        m = re.search(r"OPEN_PATH\s*=\s*(['\"])([^'\"]*)\1", runner.read_text(encoding="utf-8", errors="ignore"))
        if m:
            open_path = m.group(2) or "/"
    write_mirror_runner(site_dir, open_path)

    py = sys.executable or "python"
    if os.name == "nt":
        # Using cmd.exe with /c so we don't end up with nested/broken quotes.
        # It changes directory then calls python run.py.
        params = f'/c cd /d "{site_dir}" && "{py}" run.py'
        try:
            ctypes.windll.shell32.ShellExecuteW(
                None, "open", "cmd.exe", params, str(site_dir), 1
            )
        except Exception as e:
            return f"Could not open terminal: {e}"
    else:
        os.system(f'cd "{site_dir}" && "{py}" run.py &')

    return (
        f"TERMINAL LAUNCHED. cwd={site_dir} cmd=python run.py\n"
        "Browser opens windowed on the right; CMD stays on the left (not fullscreen).\n"
        "Tell the user in Sorani that a terminal opened and the scraped site is running. "
        "They can press Enter in that terminal to stop the server."
    )
