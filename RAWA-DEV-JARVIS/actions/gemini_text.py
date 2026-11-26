"""
Shared Gemini text-model helper with automatic fallback on quota (429).

Prefer Gemini 3.1 / 3.5 Flash-Lite (high daily limits). Avoid burned Gemini 2.5 Flash.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

# Order matters: high-quota lite models first. Never use burned/retired 2.5 Flash.
TEXT_MODELS = [
    "gemini-3.1-flash-lite",   # ~500 RPD — plain text works
    "gemini-3.5-flash-lite",   # ~500 RPD
    "gemini-3.6-flash",
    "gemini-3.5-flash",
    "gemini-3-flash-preview",
]

# Google Search grounding often shares a separate (tight) quota — keep this short
SEARCH_MODELS = [
    "gemini-3.1-flash-lite",
    "gemini-2.5-flash",  # only model family that still has Search grounding on free tier
]


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).resolve().parent.parent


def get_api_key() -> str:
    path = _base_dir() / "config" / "api_keys.json"
    try:
        with open(path, "r", encoding="utf-8") as f:
            return (json.load(f).get("gemini_api_key") or "").strip()
    except Exception:
        return ""


def _is_quota_error(exc: BaseException) -> bool:
    s = str(exc).lower()
    return any(
        k in s
        for k in (
            "429",
            "resource_exhausted",
            "quota",
            "rate limit",
            "rate_limit",
            "exceeded your current quota",
        )
    )


def _uses_google_search(config: dict | None) -> bool:
    if not config:
        return False
    tools = config.get("tools") or []
    for t in tools:
        if isinstance(t, dict) and "google_search" in t:
            return True
    return False


def generate_text(
    contents,
    *,
    config: dict | None = None,
    models: list[str] | None = None,
):
    """
    Call generate_content trying TEXT_MODELS until one succeeds.
    On 429 / quota errors, automatically tries the next model.
    """
    from google import genai

    api_key = get_api_key()
    if not api_key:
        raise RuntimeError(
            "No local gemini_api_key — voice uses rawandios.dev; "
            "text/vision tools use DDG/RSS fallbacks or need backend text API."
        )
    client = genai.Client(api_key=api_key)
    last_err: BaseException | None = None
    if models is not None:
        chain = models
    elif _uses_google_search(config):
        chain = SEARCH_MODELS
    else:
        chain = TEXT_MODELS

    for model in chain:
        try:
            print(f"[Gemini] → {model}")
            kwargs = {"model": model, "contents": contents}
            if config is not None:
                kwargs["config"] = config
            resp = client.models.generate_content(**kwargs)
            print(f"[Gemini] OK {model}")
            return resp
        except Exception as e:
            last_err = e
            brief = str(e).replace("\n", " ")[:220]
            if _is_quota_error(e):
                print(f"[Gemini] {model} quota/rate-limited — {brief}")
                # Search grounding quota is usually shared — don't burn latency on every model
                if _uses_google_search(config):
                    break
                continue
            print(f"[Gemini] {model} failed — {brief}")
            continue

    raise RuntimeError(f"All Gemini text models failed. Last error: {last_err}")
