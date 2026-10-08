#!/usr/bin/env python3
"""Silero TTS (ru): подготовка текста и синтез — ядро silero-tts-service.

Модели и голоса — проект Silero Models: https://github.com/snakers4/silero-models
(© Silero Team, условия использования моделей — в их репозитории).

Этот модуль используют server.py (модель держится в памяти) и CLI для разовой проверки:

    silero_tts.py --text "привет" --out OUT.ogg [--voice eugene] [--speed 1.2]

Перед синтезом текст проходит словарь произношения (lexicon.json + lexicon.local.json,
ударения вида `г+ермес`) и транслитерацию латиницы (translit.py): русская модель Silero
молча выбрасывает латинские слова.

Темп НЕ задаётся через SSML: парсер SSML в Silero падает на латинице. Ускорение делает
ffmpeg atempo — он сохраняет высоту тона.

Author: Ilya Byven aka @AstroiLL (https://astroill.info). License: MIT.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import threading
from pathlib import Path

BASE_DIR = Path(__file__).parent
MODEL_DIR = Path(os.environ.get("SILERO_MODEL_DIR") or BASE_DIR / "models")
LEXICON_PATH = BASE_DIR / "lexicon.json"                       # общий словарь (в репозитории)
LOCAL_LEXICON_PATH = Path(os.environ.get("SILERO_LEXICON") or BASE_DIR / "lexicon.local.json")  # свой, вне git
DEFAULT_MODEL = os.environ.get("SILERO_MODEL") or "v5_5_ru"
DEFAULT_VOICE = os.environ.get("SILERO_VOICE") or "eugene"
SAMPLE_RATE = 48000
CHUNK_CHARS = 700          # Silero не любит длинные куски: режем по предложениям
PAUSE_SEC = 0.25           # тишина между кусками

sys.path.insert(0, str(BASE_DIR))
from translit import convert_text  # noqa: E402


# --- словарь произношения -------------------------------------------------

_lex_cache: tuple[tuple, dict] | None = None


def load_lexicon() -> dict:
    """Общий словарь + свой (lexicon.local.json); свой перекрывает общий.
    Перечитывается только при изменении файлов (правки применяются без перезапуска)."""
    global _lex_cache
    key = tuple((p, p.stat().st_mtime) if p.exists() else (p, 0)
                for p in (LEXICON_PATH, LOCAL_LEXICON_PATH))
    if _lex_cache and _lex_cache[0] == key:
        return _lex_cache[1]
    merged: dict = {}
    for path in (LEXICON_PATH, LOCAL_LEXICON_PATH):
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, OSError) as exc:
            print(f"[silero] lexicon {path.name} ignored ({exc})", file=sys.stderr)
            continue
        if isinstance(data, dict):
            merged.update(data.get("words", data))
    merged.pop("_comment", None)
    _lex_cache = (key, merged)
    return merged


def apply_lexicon(text: str, lexicon: dict) -> str:
    """Заменить словарные слова. Длинные ключи первыми — чтобы фразы били слова.
    Границы слова учитывают кириллицу и латиницу; сравнение регистронезависимое."""
    for src in sorted(lexicon, key=len, reverse=True):
        dst = lexicon[src]
        pattern = re.compile(
            r"(?<![0-9A-Za-zА-Яа-яЁё]){}(?![0-9A-Za-zА-Яа-яЁё])".format(re.escape(src)),
            re.IGNORECASE,
        )
        text = pattern.sub(lambda _m, d=dst: d, text)
    return text


_EMOJI = re.compile(r"[\U0001F000-\U0001FFFF\u2600-\u27BF\uFE0F\u200d]")


def prepare_text(text: str, extra_lexicon: dict | None = None) -> str:
    """Текст → то, что реально уйдёт в модель. Пустая строка = произносить нечего."""
    lex = load_lexicon()
    if extra_lexicon:
        lex = {**lex, **extra_lexicon}
    text = _EMOJI.sub(" ", text)
    text = convert_text(apply_lexicon(text, lex))
    text = re.sub(r"\s+", " ", text).strip()
    return text if re.search(r"[А-Яа-яЁё0-9]", text) else ""


def split_chunks(text: str, limit: int = CHUNK_CHARS) -> list[str]:
    """По предложениям, куски не длиннее limit (длинное предложение — по запятым/словам)."""
    parts = re.split(r"(?<=[.!?…;:])\s+", text)
    chunks, cur = [], ""
    for part in parts:
        while len(part) > limit:
            cut = max(part.rfind(",", 0, limit), part.rfind(" ", 0, limit))
            cut = cut if cut > limit // 3 else limit
            head, part = part[:cut + 1].strip(), part[cut + 1:].strip()
            if cur:
                chunks.append(cur)
                cur = ""
            chunks.append(head)
        if cur and len(cur) + 1 + len(part) > limit:
            chunks.append(cur)
            cur = part
        else:
            cur = f"{cur} {part}".strip()
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c]


# --- модель -----------------------------------------------------------------

def ensure_model(name: str = DEFAULT_MODEL) -> Path:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    path = MODEL_DIR / f"{name}.pt"
    if not path.exists():
        import torch
        url = f"https://models.silero.ai/models/tts/ru/{name}.pt"
        print(f"[silero] downloading {url} -> {path}", file=sys.stderr)
        tmp = path.with_suffix(".part")
        torch.hub.download_url_to_file(url, str(tmp), progress=False)
        tmp.rename(path)
    return path


class Engine:
    """Модель в памяти. Один синтез за раз (lock): CPU-модель параллель не ускоряет."""

    def __init__(self, model_name: str = DEFAULT_MODEL, threads: int | None = None):
        self.model_name = model_name
        self.threads = threads or max(1, (os.cpu_count() or 4) // 2)
        self._model = None
        self._lock = threading.Lock()

    def load(self):
        if self._model is None:
            import torch
            torch.set_num_threads(self.threads)
            m = torch.package.PackageImporter(str(ensure_model(self.model_name))).load_pickle(
                "tts_models", "model")
            m.to(torch.device("cpu"))
            self._model = m
        return self._model

    @property
    def voices(self) -> list[str]:
        sp = getattr(self.load(), "speakers", None) or ["aidar", "baya", "kseniya", "xenia", "eugene"]
        return [v for v in sp if v != "random"]

    def synth_pcm(self, text: str, voice: str) -> bytes:
        """Подготовленный текст → PCM s16le mono SAMPLE_RATE."""
        import numpy as np
        import torch
        model = self.load()
        pause = torch.zeros(int(SAMPLE_RATE * PAUSE_SEC))
        pieces = []
        with self._lock:
            for i, chunk in enumerate(split_chunks(text)):
                if i:
                    pieces.append(pause)
                pieces.append(model.apply_tts(text=chunk, speaker=voice, sample_rate=SAMPLE_RATE))
        audio = torch.cat(pieces) if pieces else torch.zeros(0)
        return (audio.clamp(-1, 1).numpy() * 32767).astype(np.int16).tobytes()


# --- кодирование ------------------------------------------------------------

# формат OpenAI → (аргументы ffmpeg, mime). pcm по спецификации OpenAI: 24 кГц s16le.
FORMATS = {
    "opus": (["-c:a", "libopus", "-b:a", "64k", "-f", "ogg"], "audio/ogg"),
    "mp3": (["-c:a", "libmp3lame", "-b:a", "128k", "-f", "mp3"], "audio/mpeg"),
    "aac": (["-c:a", "aac", "-b:a", "128k", "-f", "adts"], "audio/aac"),
    "flac": (["-c:a", "flac", "-f", "flac"], "audio/flac"),
    "wav": (["-c:a", "pcm_s16le", "-f", "wav"], "audio/wav"),
    "pcm": (["-ar", "24000", "-c:a", "pcm_s16le", "-f", "s16le"], "audio/pcm"),
}


def atempo_chain(speed: float) -> str:
    """ffmpeg atempo принимает 0.5..2.0 за фильтр — большее собираем цепочкой."""
    filters, remaining = [], speed
    while remaining > 2.0:
        filters.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        filters.append("atempo=0.5")
        remaining /= 0.5
    filters.append(f"atempo={remaining:.4f}")
    return ",".join(filters)


def encode(pcm: bytes, fmt: str = "opus", speed: float = 1.0) -> bytes:
    args, _ = FORMATS[fmt]
    cmd = ["ffmpeg", "-loglevel", "error", "-f", "s16le", "-ar", str(SAMPLE_RATE), "-ac", "1",
           "-i", "pipe:0"]
    if abs(speed - 1.0) > 0.01:
        cmd += ["-filter:a", atempo_chain(speed)]
    cmd += args + ["pipe:1"]
    return subprocess.run(cmd, input=pcm, capture_output=True, check=True).stdout


def format_from_path(path: str) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    return {"ogg": "opus", "oga": "opus", "m4a": "aac"}.get(ext, ext if ext in FORMATS else "wav")


# --- CLI: разовый синтез без сервера (грузит модель каждый раз, ~3 с) ------------

def main() -> int:
    p = argparse.ArgumentParser(description="Разовый синтез Silero (для проверки; в работе — server.py)")
    p.add_argument("--text")
    p.add_argument("--text-file")
    p.add_argument("--out", required=True, help="формат по расширению: .ogg .mp3 .wav .flac .aac")
    p.add_argument("--voice", default=DEFAULT_VOICE)
    p.add_argument("--speed", type=float, default=float(os.environ.get("SILERO_SPEED") or 1.0))
    a = p.parse_args()
    if a.text_file:
        text = Path(a.text_file).read_text(encoding="utf-8")
    else:
        text = a.text if a.text is not None else sys.stdin.read()
    text = prepare_text(text)
    if not text:
        print("[silero] произносить нечего", file=sys.stderr)
        return 1
    eng = Engine()
    voice = a.voice if a.voice in eng.voices else DEFAULT_VOICE
    Path(a.out).write_bytes(encode(eng.synth_pcm(text, voice), format_from_path(a.out), a.speed))
    return 0


if __name__ == "__main__":
    sys.exit(main())
