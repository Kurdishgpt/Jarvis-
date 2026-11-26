#!/usr/bin/env python3
"""Typed, text-reply entry point for running JARVIS from Termux."""

from __future__ import annotations

import argparse
import asyncio
import getpass
import os
import sys
from types import SimpleNamespace


LIVE_MODEL = os.environ.get("JARVIS_LIVE_MODEL", "gemini-3.1-flash-live-preview")


def is_exit_command(value: str) -> bool:
    return value.strip().casefold() in {"/exit", "/quit"}


def _system_instruction(assistant_name: str) -> str:
    return (
        f"You are {assistant_name}, a helpful assistant running in a text-only "
        "Termux terminal on Android. You have no access to the phone's files, "
        "apps, camera, microphone, or desktop controls, and no tools are enabled. "
        "Do not claim to perform device actions. Give clear instructions for the "
        "user to perform any requested action themselves, and answer in the "
        "language the user writes in."
    )


def _login(api, save_auth) -> str:
    email = input("Account email: ").strip()
    if not email:
        raise RuntimeError("Email cannot be empty.")

    password = getpass.getpass("Account password (hidden): ")
    if not password:
        raise RuntimeError("Password cannot be empty.")

    try:
        result = api.login(email, password)
    except Exception as exc:
        status = getattr(exc, "status", 0)
        if status:
            raise RuntimeError(f"Login failed (HTTP {status}). Check your account details.") from None
        raise RuntimeError("Login failed. Check your internet connection and try again.") from None

    token = str(result.get("access_token") or "").strip()
    if not token:
        raise RuntimeError("Login succeeded but the server returned no access token.")

    save_auth(token, email, "android")
    return token


def _make_live_config(types, assistant_name: str):
    return types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        output_audio_transcription={},
        system_instruction=_system_instruction(assistant_name),
    )


async def _connect(api, token: str, config, model: str):
    from core.voice_proxy import ProxyLiveSession, live_config_to_setup

    setup = live_config_to_setup(model, config).get("setup")
    last_error = None

    for use_preload in (True, False):
        start_info = await asyncio.to_thread(
            api.live_start,
            token,
            setup=setup if use_preload else None,
        )
        setup_preloaded = bool(start_info.get("setup_accepted")) and use_preload
        session_id = start_info.get("live_session_id") if setup_preloaded else None
        ws_url = api.ws_url(
            token,
            start_info.get("ws_path"),
            live_session_id=session_id,
        )
        proxy = ProxyLiveSession(
            ws_url,
            model,
            config,
            setup_preloaded=setup_preloaded,
            access_token=token,
        )
        try:
            await proxy.__aenter__()
            return proxy
        except RuntimeError as exc:
            last_error = exc
            await proxy.__aexit__(None, None, None)
            if not use_preload:
                raise

    raise RuntimeError("Could not connect to the voice service.") from last_error


async def _chat_loop(session, assistant_name: str) -> None:
    print(f"{assistant_name} is ready. Type /quit to exit.")

    while True:
        try:
            prompt = await asyncio.to_thread(input, "\nYou > ")
        except (EOFError, KeyboardInterrupt):
            print()
            return

        prompt = prompt.strip()
        if is_exit_command(prompt):
            return
        if not prompt:
            continue

        await session.send_realtime_input(text=prompt)
        received_text = False

        async for response in session.receive():
            if response.tool_call:
                unavailable = [
                    SimpleNamespace(
                        id=call.id,
                        name=call.name,
                        response={
                            "result": (
                                "Device and desktop actions are unavailable in "
                                "Termux mode. Give the user text instructions instead."
                            )
                        },
                    )
                    for call in response.tool_call.function_calls
                ]
                await session.send_tool_response(function_responses=unavailable)

            server_content = response.server_content
            if server_content:
                transcription = server_content.output_transcription
                if transcription and transcription.text:
                    print(transcription.text, end="", flush=True)
                    received_text = True
                if server_content.turn_complete:
                    break

        if not received_text:
            print("(No text reply was received.)", end="")
        print()


async def _run(api, token: str, config, model: str, assistant_name: str) -> None:
    from core.voice_proxy import QuotaLimitReached

    session = await _connect(api, token, config, model)
    try:
        await _chat_loop(session, assistant_name)
    except QuotaLimitReached:
        print("\nThe daily voice quota has been reached.")
    finally:
        await session.__aexit__(None, None, None)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run JARVIS in a text-only Termux terminal."
    )
    parser.add_argument(
        "--login",
        action="store_true",
        help="sign in again and replace the saved local session",
    )
    args = parser.parse_args(argv)

    try:
        from google.genai import types
        from core.voice_api import (
            VoiceAPI,
            VoiceAPIError,
            get_access_token,
            read_config,
            save_auth,
        )
    except ImportError as exc:
        print(
            "Termux dependencies are missing. Install them with "
            "'python -m pip install -r requirements-termux.txt'.",
            file=sys.stderr,
        )
        return 1

    api = VoiceAPI()
    try:
        token = _login(api, save_auth) if args.login else get_access_token()
        if not token:
            token = _login(api, save_auth)

        config_data = read_config()
        assistant_name = (config_data.get("assistant_name") or "JARVIS").strip()
        config = _make_live_config(types, assistant_name)
        asyncio.run(_run(api, token, config, LIVE_MODEL, assistant_name))
    except VoiceAPIError as exc:
        if exc.status == 401:
            print("Your saved login has expired. Run 'python termux.py --login'.")
        elif exc.status == 429:
            print("The daily voice quota has been reached.")
        else:
            print(f"Voice service request failed (HTTP {exc.status or 'network error'}).")
        return 1
    except (RuntimeError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nJARVIS stopped.")
    except Exception as exc:
        print(
            f"Termux session failed ({type(exc).__name__}). "
            "Check your connection and try again.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
