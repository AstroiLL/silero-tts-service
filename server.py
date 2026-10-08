#!/usr/bin/env python3
"""silero-tts-service — русский синтез речи Silero одним сервисом на сервер (или на всю сеть).

Модель загружается один раз и держится в памяти; клиентов сколько угодно, у каждого свой токен.
API совместим с OpenAI (`POST /v1/audio/speech`), поэтому подходит любой клиент OpenAI TTS:
Hermes Agent (tts.openai.base_url), Open WebUI, n8n, Chuyko, curl.

    GET  /health               без токена: жив ли сервис, модель, голоса
    GET  /v1/models            список моделей (для клиентов OpenAI)
    GET  /v1/audio/voices      голоса
    POST /v1/audio/speech      {"input": "...", "voice": "eugene", "response_format": "opus", "speed": 1.0}
                               + необязательно "lexicon": {"слово": "сл+ово"} — ударения только для запроса

Токен: `Authorization: Bearer <токен>`, выдаётся `tokens.py add <имя>`.
Настройки — .env рядом с server.py (см. .env.example).

Author: Ilya Byven aka @AstroiLL (https://astroill.info). License: MIT.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent


def _load_env(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


# .env грузим ДО импорта своих модулей: они читают SILERO_* при импорте.
_load_env(BASE / ".env")
sys.path.insert(0, str(BASE))

from contextlib import asynccontextmanager  # noqa: E402

from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse, Response  # noqa: E402

import silero_tts as st  # noqa: E402
import tokens  # noqa: E402

HOST = os.environ.get("SILERO_HOST") or "127.0.0.1"
PORT = int(os.environ.get("SILERO_PORT") or 7470)
DEFAULT_SPEED = float(os.environ.get("SILERO_SPEED") or 1.0)
MAX_CHARS = int(os.environ.get("SILERO_MAX_CHARS") or 10000)
THREADS = int(os.environ.get("SILERO_THREADS") or 0) or None

engine = st.Engine(st.DEFAULT_MODEL, THREADS)


@asynccontextmanager
async def lifespan(_app):
    # Загрузка модели + пробный синтез: первый настоящий запрос не ждёт прогрева (~1 с).
    await asyncio.to_thread(lambda: engine.synth_pcm("прогрев", st.DEFAULT_VOICE))
    n = len(tokens.load())
    print(f"[tts] {engine.model_name} загружена, голоса: {', '.join(engine.voices)}; "
          f"http://{HOST}:{PORT}; токенов: {n}", flush=True)
    if not n:
        print("[tts] токенов нет — выпусти: .venv/bin/python tokens.py add <имя>", flush=True)
    yield


app = FastAPI(title="silero-tts-service", docs_url=None, redoc_url=None, lifespan=lifespan)


def _err(status: int, message: str, kind: str = "invalid_request_error") -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": kind}}, status_code=status)


def _client(request: Request) -> str | None:
    auth = request.headers.get("authorization", "")
    token = auth[7:].strip() if auth.lower().startswith("bearer ") else ""
    return tokens.check(token)


@app.get("/health")
async def health():
    return {"ok": True, "service": "silero-tts-service", "model": engine.model_name,
            "voices": engine.voices, "default_voice": st.DEFAULT_VOICE,
            "formats": list(st.FORMATS)}


@app.get("/v1/models")
async def models(request: Request):
    if not _client(request):
        return _err(401, "bad token", "authentication_error")
    return {"object": "list", "data": [{"id": f"silero-{engine.model_name}", "object": "model",
                                        "owned_by": "silero"}]}


@app.get("/v1/audio/voices")
async def voices(request: Request):
    if not _client(request):
        return _err(401, "bad token", "authentication_error")
    return {"voices": engine.voices, "default": st.DEFAULT_VOICE}


@app.post("/v1/audio/speech")
async def speech(request: Request):
    client = _client(request)
    if not client:
        return _err(401, "bad token", "authentication_error")
    try:
        body = await request.json()
    except ValueError:
        return _err(400, "body must be JSON")
    text = str(body.get("input") or "")
    if not text.strip():
        return _err(400, "input is empty")
    if len(text) > MAX_CHARS:
        return _err(400, f"input longer than {MAX_CHARS} chars")
    fmt = str(body.get("response_format") or "mp3").lower()
    if fmt not in st.FORMATS:
        return _err(400, f"response_format must be one of {', '.join(st.FORMATS)}")
    voice = str(body.get("voice") or "")
    if voice not in engine.voices:          # alloy и прочие голоса OpenAI → голос по умолчанию
        voice = st.DEFAULT_VOICE
    try:
        speed = float(body.get("speed") or DEFAULT_SPEED)
    except (TypeError, ValueError):
        return _err(400, "speed must be a number")
    speed = max(0.25, min(4.0, speed))
    lex = body.get("lexicon")
    lex = {str(k): str(v) for k, v in list(lex.items())[:500]} if isinstance(lex, dict) else None

    started = time.monotonic()
    prepared = st.prepare_text(text, lex)

    def work() -> bytes:
        pcm = engine.synth_pcm(prepared, voice) if prepared else b"\0\0" * (st.SAMPLE_RATE // 10)
        return st.encode(pcm, fmt, speed)

    try:
        audio = await asyncio.to_thread(work)
    except Exception as exc:  # noqa: BLE001 — клиенту понятная ошибка, детали в журнал
        print(f"[tts] {client}: ошибка синтеза: {exc!r}", file=sys.stderr, flush=True)
        return _err(500, "synthesis failed", "server_error")
    print(f"[tts] {client}: {len(text)} симв, {voice}, {fmt}, {time.monotonic() - started:.2f} с",
          flush=True)
    return Response(audio, media_type=st.FORMATS[fmt][1])


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="warning")
