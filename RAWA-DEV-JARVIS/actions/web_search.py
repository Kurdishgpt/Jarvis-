#web_search.py
import json
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from html import unescape
from pathlib import Path


def _get_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


BASE_DIR        = _get_base_dir()
API_CONFIG_PATH = BASE_DIR / "config" / "api_keys.json"

# Free RSS sources — no Gemini Search grounding quota needed
_RSS_FEEDS = [
    "https://feeds.bbci.co.uk/news/world/rss.xml",
    "https://rss.nytimes.com/services/xml/rss/nyt/World.xml",
    "https://www.aljazeera.com/xml/rss/all.xml",
]

_KURDISTAN_RSS_FEEDS = [
    "https://www.rudaw.net/english/rss",
    "https://www.kurdistan24.net/en/rss",
    "https://basnews.com/en/rss",
]

_KURDISTAN_KEYWORDS = {
    "kurd", "kurds", "kurdish", "kurdistan", "krg", "peshmerga", "barzani", "talabani",
    "erbil", "hawler", "hawlêr", "sulaymaniyah", "slemani", "sulaimani", "duhok", "dohuk",
    "kirkuk", "halabja", "zaxo", "raparin", "garmian", "soran", "penjwen", "koya",
    "کورد", "کوردستان", "هەولێر", "سلێمانی", "دهۆک", "کەرکوک",
}


def _get_api_key() -> str:
    with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)["gemini_api_key"]


def _strip_html(text: str) -> str:
    text = unescape(text or "")
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _rss_news(query: str = "", max_results: int = 8) -> list[dict]:
    """Fetch live headlines from public RSS feeds (no API key / no Gemini grounding)."""
    items: list[dict] = []
    keywords = [w.lower() for w in re.findall(r"[a-zA-Z\u0600-\u06FF]{3,}", query or "")]
    # Drop generic briefing words so we don't over-filter
    skip = {"top", "world", "news", "today", "latest", "headline", "headlines"}
    keywords = [k for k in keywords if k not in skip]

    for feed_url in _RSS_FEEDS:
        try:
            req = urllib.request.Request(
                feed_url,
                headers={"User-Agent": "Mozilla/5.0 (Mark-L news)"},
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                raw = resp.read()
            root = ET.fromstring(raw)
            # RSS 2.0: channel/item ; Atom: entry
            nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            for node in nodes:
                title = _strip_html(
                    (node.findtext("title")
                     or node.findtext("{http://www.w3.org/2005/Atom}title")
                     or "")
                )
                link = (
                    node.findtext("link")
                    or (node.find("{http://www.w3.org/2005/Atom}link").get("href")
                        if node.find("{http://www.w3.org/2005/Atom}link") is not None else "")
                    or ""
                )
                snippet = _strip_html(
                    node.findtext("description")
                    or node.findtext("{http://www.w3.org/2005/Atom}summary")
                    or ""
                )[:180]
                source = feed_url.split("/")[2].replace("www.", "").replace("feeds.", "")
                if not title or len(title) < 12:
                    continue
                if keywords:
                    blob = f"{title} {snippet}".lower()
                    if not any(k in blob for k in keywords):
                        continue
                items.append({
                    "title": title,
                    "snippet": snippet,
                    "url": link,
                    "source": source,
                })
                if len(items) >= max_results:
                    return items
        except Exception as e:
            print(f"[WebSearch] RSS feed failed ({feed_url}): {e}")
            continue

    # If keyword filter emptied results, retry without filter
    if not items and keywords:
        return _rss_news("", max_results=max_results)
    return items[:max_results]


def _rss_from_feeds(feed_urls: list[str], max_results: int = 8) -> list[dict]:
    items: list[dict] = []
    for feed_url in feed_urls:
        try:
            req = urllib.request.Request(
                feed_url,
                headers={"User-Agent": "Mozilla/5.0 (Mark-L news)"},
            )
            with urllib.request.urlopen(req, timeout=6) as resp:
                raw = resp.read()
            root = ET.fromstring(raw)
            nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
            for node in nodes:
                title = _strip_html(
                    (node.findtext("title")
                     or node.findtext("{http://www.w3.org/2005/Atom}title")
                     or "")
                )
                link = (
                    node.findtext("link")
                    or (node.find("{http://www.w3.org/2005/Atom}link").get("href")
                        if node.find("{http://www.w3.org/2005/Atom}link") is not None else "")
                    or ""
                )
                snippet = _strip_html(
                    node.findtext("description")
                    or node.findtext("{http://www.w3.org/2005/Atom}summary")
                    or ""
                )[:180]
                source = feed_url.split("/")[2].replace("www.", "")
                if not title or len(title) < 12:
                    continue
                items.append({
                    "title": title,
                    "snippet": snippet,
                    "url": link,
                    "source": source,
                })
                if len(items) >= max_results:
                    return items
        except Exception as e:
            print(f"[WebSearch] Kurdistan RSS failed ({feed_url}): {e}")
    return items[:max_results]


def _kurdistan_news(max_results: int = 4) -> str:
    """Startup briefing: Kurd / Kurdistan headlines only — never generic world news."""
    items = _rss_from_feeds(_KURDISTAN_RSS_FEEDS, max_results=max_results)
    if len(items) < 2:
        filtered: list[dict] = []
        for feed_url in _RSS_FEEDS:
            try:
                req = urllib.request.Request(
                    feed_url,
                    headers={"User-Agent": "Mozilla/5.0 (Mark-L news)"},
                )
                with urllib.request.urlopen(req, timeout=6) as resp:
                    raw = resp.read()
                root = ET.fromstring(raw)
                nodes = root.findall(".//item") or root.findall(".//{http://www.w3.org/2005/Atom}entry")
                for node in nodes:
                    title = _strip_html(
                        (node.findtext("title")
                         or node.findtext("{http://www.w3.org/2005/Atom}title")
                         or "")
                    )
                    snippet = _strip_html(
                        node.findtext("description")
                        or node.findtext("{http://www.w3.org/2005/Atom}summary")
                        or ""
                    )[:180]
                    blob = f"{title} {snippet}".lower()
                    if not any(k in blob for k in _KURDISTAN_KEYWORDS):
                        continue
                    filtered.append({
                        "title": title,
                        "snippet": snippet,
                        "url": "",
                        "source": feed_url.split("/")[2].replace("www.", ""),
                    })
                    if len(filtered) >= max_results:
                        break
            except Exception:
                continue
            if len(filtered) >= max_results:
                break
        items = filtered or items

    if items:
        return _format_news("Kurdistan", items[:max_results])

    return (
        "Kurdistan briefing (no live fetch): focus on KRG — Erbil, Sulaymaniyah, Duhok, Kirkuk; "
        "Kurdish politics and local Kurdistan news only. Do NOT mention world or non-Kurd news."
    )


def _gemini_search(query: str) -> str:
    from actions.gemini_text import generate_text

    # Grounded search — often hits a separate Search-grounding quota (429)
    response = generate_text(
        query,
        config={"tools": [{"google_search": {}}]},
    )

    text = ""
    for part in response.candidates[0].content.parts:
        if hasattr(part, "text") and part.text:
            text += part.text

    text = text.strip()
    if not text:
        raise ValueError("Gemini returned an empty response.")
    return text


def _ddg_search(query: str, max_results: int = 6) -> list[dict]:
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    with DDGS() as ddgs:
        for r in ddgs.text(query, max_results=max_results):
            results.append({
                "title":   r.get("title",  ""),
                "snippet": r.get("body",   ""),
                "url":     r.get("href",   ""),
            })
    return results


def _ddg_news(query: str, max_results: int = 8) -> list[dict]:
    """DDG news search — returns actual articles, not website homepages."""
    try:
        from ddgs import DDGS
    except ImportError:
        from duckduckgo_search import DDGS

    results = []
    try:
        with DDGS() as ddgs:
            for r in ddgs.news(query, max_results=max_results):
                results.append({
                    "title":   r.get("title",  ""),
                    "snippet": r.get("body",   ""),
                    "url":     r.get("url",    ""),
                    "source":  r.get("source", ""),
                })
    except Exception as e:
        print(f"[WebSearch] DDG news() failed ({e}) — falling back to text search")
        results = _ddg_search(query, max_results=max_results)
    return results


def _format_ddg(query: str, results: list[dict]) -> str:
    if not results:
        return f"No results found for: {query}"

    lines = [f"Search results for: {query}\n"]
    for i, r in enumerate(results, 1):
        if r.get("title"):   lines.append(f"{i}. {r['title']}")
        if r.get("snippet"): lines.append(f"   {r['snippet']}")
        if r.get("url"):     lines.append(f"   Source: {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


def _format_news(query: str, results: list[dict]) -> str:
    if not results:
        return f"No news found for: {query}"

    lines = [f"Latest news: {query}\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        if not title:
            continue
        src = f"  [{r['source']}]" if r.get("source") else ""
        lines.append(f"{i}. {title}{src}")
        if r.get("snippet"):
            lines.append(f"   {r['snippet'][:140]}")
        if r.get("url"):
            lines.append(f"   {r['url']}")
        lines.append("")
    return "\n".join(lines).strip()


# ── Briefing helper ────────────────────────────────────────────────────────────

def _gemini_headlines(n: int = 5) -> tuple[list[str], str]:
    """Headlines from RSS first (avoids Search-grounding 429)."""
    results = _rss_news("", max_results=n)
    if not results:
        return [], ""
    headlines = [r["title"] for r in results if r.get("title")]
    raw = _format_news("top world news today", results)
    return headlines[:n], raw


# ── Modes ──────────────────────────────────────────────────────────────────────

def _search(query: str) -> str:
    """Default search — Gemini grounded, then DDG / RSS fallbacks."""
    try:
        return _gemini_search(query)
    except Exception as e:
        print(f"[WebSearch] Gemini grounded search failed ({e}) — trying DDG/RSS...")
    try:
        results = _ddg_search(query)
        if results:
            return _format_ddg(query, results)
    except Exception as e:
        print(f"[WebSearch] DDG failed ({e})")
    results = _rss_news(query, max_results=6)
    return _format_news(query, results) if results else f"No results found for: {query}"


def _news(query: str) -> str:
    """
    News: RSS first (reliable, no Gemini Search quota), then DDG, then Gemini grounding.
    """
    ddg_query = query if query else "world news today"
    label = query if query else "top world news today"

    # 1) RSS — primary path (BBC / NYT / Al Jazeera)
    try:
        rss = _rss_news(query, max_results=8)
        if rss:
            print(f"[WebSearch] OK RSS news ({len(rss)} headlines)")
            return _format_news(label, rss)
    except Exception as e:
        print(f"[WebSearch] RSS news failed ({e})")

    # 2) DDG news
    try:
        results = _ddg_news(ddg_query, max_results=8)
        if results:
            print(f"[WebSearch] OK DDG news ({len(results)} headlines)")
            return _format_news(ddg_query, results)
    except Exception as e:
        print(f"[WebSearch] DDG news failed ({e})")

    # 3) Gemini Search grounding (often 429 on free tier — last resort)
    try:
        gemini_query = f"latest news today: {query}" if query else "top world news today"
        return _gemini_search(gemini_query)
    except Exception as e:
        print(f"[WebSearch] Gemini news failed ({e})")

    return f"No news found for: {query}"

def _research(query: str) -> str:
    """
    Deep dive — asks Gemini for a comprehensive answer with context.
    Falls back to a wider DDG fetch.
    """
    research_query = (
        f"Comprehensive, detailed explanation of: {query}. "
        "Include background context, key facts, current state, and important nuances."
    )
    try:
        return _gemini_search(research_query)
    except Exception as e:
        print(f"[WebSearch] Research Gemini failed ({e}) — DDG fallback...")
        results = _ddg_search(query, max_results=10)
        return _format_ddg(query, results)


def _price(query: str) -> str:
    """Product price lookup — searches for current market prices."""
    price_query = f"current price of {query} — how much does it cost today"
    try:
        return _gemini_search(price_query)
    except Exception as e:
        print(f"[WebSearch] Price Gemini failed ({e}) — DDG fallback...")
        results = _ddg_search(f"{query} price buy", max_results=6)
        return _format_ddg(query, results)


def _compare(items: list[str], aspect: str) -> str:
    query = (
        f"Compare {', '.join(items)} in terms of {aspect}. "
        "Give specific facts and data."
    )
    try:
        return _gemini_search(query)
    except Exception as e:
        print(f"[WebSearch] Gemini compare failed: {e} — falling back to DDG")

    all_results: dict[str, list] = {}
    for item in items:
        try:
            all_results[item] = _ddg_search(f"{item} {aspect}", max_results=3)
        except Exception:
            all_results[item] = []

    lines = [f"Comparison — {aspect.upper()}", "─" * 40]
    for item in items:
        lines.append(f"\n▸ {item}")
        for r in all_results.get(item, [])[:2]:
            if r.get("snippet"):
                lines.append(f"  • {r['snippet']}")
            if r.get("url"):
                lines.append(f"    {r['url']}")
    return "\n".join(lines)


# ── Public entry point ─────────────────────────────────────────────────────────

def web_search(
    parameters:     dict,
    response=None,
    player=None,
    session_memory=None,
) -> str:
    params = parameters or {}
    query  = params.get("query", "").strip()
    mode   = params.get("mode",  "search").lower().strip()
    items  = params.get("items", [])
    aspect = params.get("aspect", "general").strip() or "general"

    if not query and not items:
        return "Please provide a search query."

    if items and mode not in ("compare",):
        mode = "compare"

    if player:
        player.write_log(f"[Search:{mode}] {query or ', '.join(items)}")

    print(f"[WebSearch] 🔍 mode={mode!r}  query={query!r}")

    try:
        if mode == "compare" and items:
            return _compare(items, aspect)
        if mode == "news":
            return _news(query)
        if mode == "research":
            return _research(query)
        if mode == "price":
            return _price(query)
        return _search(query)

    except Exception as e:
        print(f"[WebSearch] ❌ All backends failed: {e}")
        return f"Search failed: {e}"
