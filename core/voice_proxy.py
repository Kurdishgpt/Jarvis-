"""WebSocket proxy session — replaces direct google.genai live.connect()."""
from __future__ import annotations

import asyncio
import base64
import json
import traceback
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

import websockets
from google.genai import types


@dataclass
class _Transcription:
    text: str = ""


@dataclass
class _ServerContent:
    output_transcription: _Transcription | None = None
    input_transcription: _Transcription | None = None
    turn_complete: bool = False


@dataclass
class _FunctionCall:
    id: str
    name: str
    args: dict


@dataclass
class _ToolCall:
    function_calls: list[_FunctionCall] = field(default_factory=list)


@dataclass
class LiveResponse:
    data: bytes | None = None
    server_content: _ServerContent | None = None
    tool_call: _ToolCall | None = None


class QuotaLimitReached(Exception):
    """Daily voice quota exhausted."""

    def __init__(self, message: str = "Daily voice limit reached.", *, resets_at: str = ""):
        super().__init__(message)
        self.resets_at = resets_at
        self.code = 429


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _db64(text: str) -> bytes:
    return base64.b64decode(text)


def _json_safe(obj: Any) -> Any:
    """Recursively convert SDK / pydantic objects to JSON-serializable data."""
    if obj is None or isinstance(obj, (str, int, float, bool)):
        return obj
    if isinstance(obj, bytes):
        return _b64(obj)
    if isinstance(obj, dict):
        return {k: _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if hasattr(obj, "model_dump"):
        try:
            return _json_safe(obj.model_dump(mode="json", exclude_none=True, by_alias=True))
        except TypeError:
            return _json_safe(obj.model_dump(exclude_none=True))
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


def live_config_to_setup(
    model: str,
    config: types.LiveConnectConfig,
    *,
    tool_declarations: list[dict] | None = None,
) -> dict:
    """Convert LiveConnectConfig to Gemini Bidi setup JSON."""
    model_name = model.removeprefix("models/")

    speech = {}
    if config.speech_config and config.speech_config.voice_config:
        pvc = config.speech_config.voice_config.prebuilt_voice_config
        if pvc and pvc.voice_name:
            speech = {
                "voiceConfig": {
                    "prebuiltVoiceConfig": {"voiceName": pvc.voice_name}
                }
            }

    gen_cfg: dict[str, Any] = {"responseModalities": ["AUDIO"]}
    if speech:
        gen_cfg["speechConfig"] = speech

    setup: dict[str, Any] = {
        "model": model_name,
        "generationConfig": gen_cfg,
    }

    instr = config.system_instruction
    if instr:
        if isinstance(instr, str):
            setup["systemInstruction"] = {"parts": [{"text": instr}]}
        elif isinstance(instr, dict):
            setup["systemInstruction"] = instr
        elif hasattr(instr, "parts"):
            parts = []
            for p in instr.parts or []:
                if hasattr(p, "text") and p.text:
                    parts.append({"text": p.text})
            if parts:
                setup["systemInstruction"] = {"parts": parts}

    if tool_declarations is not None:
        setup["tools"] = [{"functionDeclarations": tool_declarations}]
    elif config.tools:
        setup["tools"] = _json_safe(config.tools)

    if config.output_audio_transcription is not None:
        setup["outputAudioTranscription"] = _json_safe(
            config.output_audio_transcription or {}
        )
    if config.input_audio_transcription is not None:
        setup["inputAudioTranscription"] = _json_safe(
            config.input_audio_transcription or {}
        )

    return {"setup": setup}


def _parse_args(raw: Any) -> dict:
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return dict(parsed) if isinstance(parsed, dict) else {}
        except Exception:
            return {}
    if hasattr(raw, "items"):
        return dict(raw)
    return {}


def _extract_function_calls(payload: dict) -> list[_FunctionCall]:
    """Collect function calls from all Gemini Live wire formats."""
    calls: list[_FunctionCall] = []
    seen: set[tuple[str, str]] = set()

    def _add(fc: dict) -> None:
        if not fc:
            return
        name = (fc.get("name") or "").strip()
        if not name:
            return
        fid = (fc.get("id") or fc.get("callId") or name).strip()
        key = (fid, name)
        if key in seen:
            return
        seen.add(key)
        calls.append(_FunctionCall(id=fid, name=name, args=_parse_args(fc.get("args"))))

    tc = payload.get("toolCall") or payload.get("tool_call")
    if isinstance(tc, dict):
        for fc in tc.get("functionCalls") or tc.get("function_calls") or []:
            if isinstance(fc, dict):
                _add(fc)

    sc = payload.get("serverContent") or payload.get("server_content") or {}
    if isinstance(sc, dict):
        tc2 = sc.get("toolCall") or sc.get("tool_call")
        if isinstance(tc2, dict):
            for fc in tc2.get("functionCalls") or tc2.get("function_calls") or []:
                if isinstance(fc, dict):
                    _add(fc)
        model_turn = sc.get("modelTurn") or sc.get("model_turn") or {}
        if isinstance(model_turn, dict):
            for part in model_turn.get("parts") or []:
                if not isinstance(part, dict):
                    continue
                fc = part.get("functionCall") or part.get("function_call")
                if isinstance(fc, dict):
                    _add(fc)

    return calls


def _parse_gemini(payload: dict) -> LiveResponse | None:
    resp = LiveResponse()

    sc = payload.get("serverContent") or payload.get("server_content")
    if sc:
        out = sc.get("outputTranscription") or sc.get("output_transcription") or {}
        inn = sc.get("inputTranscription") or sc.get("input_transcription") or {}
        resp.server_content = _ServerContent(
            output_transcription=_Transcription(text=out.get("text") or "")
            if out.get("text")
            else None,
            input_transcription=_Transcription(text=inn.get("text") or "")
            if inn.get("text")
            else None,
            turn_complete=bool(sc.get("turnComplete") or sc.get("turn_complete")),
        )

        model_turn = sc.get("modelTurn") or sc.get("model_turn") or {}
        audio_parts: list[bytes] = []
        for part in model_turn.get("parts") or []:
            if not isinstance(part, dict):
                continue
            inline = part.get("inlineData") or part.get("inline_data") or {}
            mime = (inline.get("mimeType") or inline.get("mime_type") or "").lower()
            raw = inline.get("data")
            if raw and "audio" in mime:
                audio_parts.append(_db64(raw))
        if audio_parts:
            resp.data = b"".join(audio_parts)

    fn_calls = _extract_function_calls(payload)
    if fn_calls:
        resp.tool_call = _ToolCall(function_calls=fn_calls)
        print(f"[VoiceProxy] tool call(s): {[c.name for c in fn_calls]}")

    if resp.data or resp.server_content or resp.tool_call:
        return resp
    return None


class ProxyLiveSession:
    """Drop-in async context manager mimicking genai live session."""

    def __init__(
        self,
        ws_url: str,
        model: str,
        config: types.LiveConnectConfig,
        *,
        tool_declarations: list[dict] | None = None,
        setup_preloaded: bool = False,
        access_token: str | None = None,
        on_quota: Callable[[dict], None] | None = None,
        on_status: Callable[[str], None] | None = None,
    ):
        self._ws_url = ws_url
        self._model = model
        self._config = config
        self._tool_declarations = tool_declarations
        self._setup_preloaded = setup_preloaded
        self._on_quota = on_quota
        self._on_status = on_status
        self._ws: websockets.WebSocketClientProtocol | None = None
        self._recv_q: asyncio.Queue[Any] = asyncio.Queue()
        self._reader_task: asyncio.Task | None = None
        self._closed = False
        self._ready = asyncio.Event()
        self._connecting = asyncio.Event()
        self._setup_sent = bool(setup_preloaded)
        self._setup_confirmed = False
        self._ws_closed = asyncio.Event()
        self._close_reason = ""
        self._access_token = access_token or ""
        self._dbg_msgs = 0

    async def _ensure_setup(self) -> None:
        if self._setup_sent or self._setup_preloaded:
            return
        payload = live_config_to_setup(
            self._model,
            self._config,
            tool_declarations=self._tool_declarations,
        )
        n_tools = 0
        if self._tool_declarations:
            n_tools = len(self._tool_declarations)
        elif payload.get("setup", {}).get("tools"):
            n_tools = -1
        await self._send_raw(payload)
        self._setup_sent = True
        print(f"[VoiceProxy] sent Live setup ({n_tools} tools + system prompt)")

    async def _ready_fallback(self) -> None:
        await asyncio.sleep(25)
        if self._ws_closed.is_set() or self._ready.is_set():
            return
        if self._setup_preloaded:
            print("[VoiceProxy] ready/setupComplete timeout — continuing (HTTP setup)")
        elif self._setup_sent:
            print("[VoiceProxy] setupComplete not received — continuing anyway")
        self._ready.set()

    async def __aenter__(self) -> "ProxyLiveSession":
        headers = {}
        if self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        self._ws = await websockets.connect(
            self._ws_url,
            max_size=16 * 1024 * 1024,
            ping_interval=20,
            ping_timeout=60,
            additional_headers=headers or None,
        )
        self._reader_task = asyncio.create_task(self._read_loop())
        fallback_task = asyncio.create_task(self._ready_fallback())

        if self._setup_preloaded:
            print("[VoiceProxy] setup preloaded via live/start — waiting for setupComplete")
        else:
            await self._ensure_setup()

        try:
            await asyncio.wait_for(self._connecting.wait(), timeout=8)
        except asyncio.TimeoutError:
            pass

        wait_ready = asyncio.create_task(self._ready.wait())
        wait_closed = asyncio.create_task(self._ws_closed.wait())
        try:
            done, pending = await asyncio.wait(
                {wait_ready, wait_closed},
                timeout=45,
                return_when=asyncio.FIRST_COMPLETED,
            )
        finally:
            for t in (wait_ready, wait_closed):
                if not t.done():
                    t.cancel()

        fallback_task.cancel()

        if self._ws_closed.is_set() and not self._ready.is_set():
            await self.close()
            raise RuntimeError(
                f"WebSocket closed before ready ({self._close_reason or 'no reason'})"
            )

        if not self._ready.is_set():
            await self.close()
            raise RuntimeError("Voice server did not become ready in time.")

        if self._setup_confirmed or self._setup_preloaded:
            print("[VoiceProxy] session ready — tools should be active")
        elif not self._setup_confirmed:
            print("[VoiceProxy] WARNING: setupComplete missing — tools may not work")
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self._ws and not self._closed:
                await self._ws.send(json.dumps({"type": "hangup"}))
        except Exception:
            pass
        if self._reader_task:
            self._reader_task.cancel()
            try:
                await self._reader_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
        self._ws = None

    async def _send_raw(self, payload: dict) -> None:
        if not self._ws or self._closed:
            return
        await self._ws.send(json.dumps(_json_safe(payload)))

    async def _read_loop(self) -> None:
        assert self._ws is not None
        try:
            async for raw in self._ws:
                if self._closed:
                    break
                if self._dbg_msgs < 8:
                    print(f"[VoiceProxy] ← {raw[:280]}")
                    self._dbg_msgs += 1
                try:
                    msg = json.loads(raw)
                except json.JSONDecodeError:
                    continue

                mtype = msg.get("type")
                if mtype == "connecting":
                    if not self._setup_preloaded:
                        await self._ensure_setup()
                    self._connecting.set()
                    if self._on_status:
                        self._on_status("connecting")
                    continue

                if mtype == "ready":
                    if not self._setup_preloaded and not self._setup_sent:
                        await self._ensure_setup()
                    self._ready.set()
                    if self._on_status:
                        self._on_status("ready")
                    continue

                if mtype == "quota":
                    if self._on_quota:
                        self._on_quota(msg)
                    continue

                if mtype == "error":
                    code = msg.get("code", 0)
                    if code == 429:
                        err = QuotaLimitReached(
                            msg.get("message") or "Daily voice limit reached.",
                            resets_at=msg.get("resets_at") or "",
                        )
                        await self._recv_q.put(err)
                        break
                    err = RuntimeError(msg.get("message") or "Voice server error")
                    err.code = code  # type: ignore[attr-defined]
                    await self._recv_q.put(err)
                    continue

                if mtype in ("killed", "replaced"):
                    err = RuntimeError(msg.get("message") or mtype)
                    err.code = 503 if mtype == "killed" else 409  # type: ignore[attr-defined]
                    await self._recv_q.put(err)
                    break

                if msg.get("setupComplete") is not None or msg.get("setup_complete") is not None:
                    self._setup_confirmed = True
                    self._ready.set()
                    print("[VoiceProxy] setupComplete — tools active")
                    continue

                parsed = _parse_gemini(msg)
                if parsed:
                    await self._recv_q.put(parsed)
        except asyncio.CancelledError:
            pass
        except websockets.ConnectionClosed as e:
            self._close_reason = f"code={e.code} reason={e.reason or 'none'}"
            print(f"[VoiceProxy] WebSocket closed: {self._close_reason}")
            self._ws_closed.set()
            if not self._ready.is_set():
                await self._recv_q.put(
                    RuntimeError(f"WebSocket closed: {self._close_reason}")
                )
        except Exception as e:
            print(f"[VoiceProxy] read error: {e}")
            traceback.print_exc()
            await self._recv_q.put(e)
        finally:
            await self._recv_q.put(StopAsyncIteration)

    async def send_realtime_input(
        self,
        *,
        audio: types.Blob | None = None,
        video: types.Blob | None = None,
        text: str | None = None,
        media: Any = None,
    ) -> None:
        if audio is not None:
            await self._send_raw(
                {"type": "audio", "audio": _b64(audio.data)}
            )
            return

        if video is not None:
            await self._send_raw(
                {
                    "realtimeInput": {
                        "mediaChunks": [
                            {
                                "mimeType": video.mime_type or "image/jpeg",
                                "data": _b64(video.data),
                            }
                        ]
                    }
                }
            )
            return

        if text:
            await self._send_raw({"realtimeInput": {"text": text}})
            return

        if media is not None:
            mime = getattr(media, "mime_type", None) or "audio/pcm;rate=16000"
            data = getattr(media, "data", media)
            if isinstance(data, (bytes, bytearray)):
                await self._send_raw({"type": "audio", "audio": _b64(bytes(data))})

    async def send_tool_response(self, *, function_responses) -> None:
        fr_list = []
        for fr in function_responses:
            resp = fr.response if hasattr(fr, "response") else {}
            if hasattr(resp, "model_dump"):
                resp = resp.model_dump(exclude_none=True)
            elif not isinstance(resp, dict):
                resp = {"result": str(resp)}
            fr_list.append(
                {
                    "id": fr.id,
                    "name": fr.name,
                    "response": resp,
                }
            )
        print(f"[VoiceProxy] tool response → {[x['name'] for x in fr_list]}")
        await self._send_raw({"toolResponse": {"functionResponses": fr_list}})

    async def receive(self) -> AsyncIterator[LiveResponse]:
        while True:
            item = await self._recv_q.get()
            if item is StopAsyncIteration:
                break
            if isinstance(item, Exception):
                raise item
            yield item
