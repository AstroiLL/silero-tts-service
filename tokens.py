#!/usr/bin/env python3
"""Токены доступа к silero-tts-service: по одному на клиента (пользователь, сервер, программа).

    tokens.py add <имя>        создать токен (печатается ОДИН раз — сохрани у клиента)
    tokens.py list             кто имеет доступ
    tokens.py remove <имя>     отозвать доступ (действует сразу, без перезапуска)

В tokens.json хранятся только SHA-256 хэши, сами токены не восстановить: потерял — выпусти новый.
Если сервис работает от отдельного системного пользователя, запускай от его имени:
    sudo -u silero-tts .venv/bin/python tokens.py add chuyko-ivan

Author: Ilya Byven aka @AstroiLL (https://astroill.info). License: MIT.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import sys
from pathlib import Path

BASE = Path(__file__).parent
PATH = Path(os.environ.get("SILERO_TOKENS") or BASE / "tokens.json")
PREFIX = "stts_"

_cache: tuple[float, dict] | None = None


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def load() -> dict:
    """{имя: {sha256, created}}; перечитывается при изменении файла."""
    global _cache
    try:
        mtime = PATH.stat().st_mtime
    except OSError:
        return {}
    if _cache and _cache[0] == mtime:
        return _cache[1]
    try:
        data = json.loads(PATH.read_text(encoding="utf-8")).get("tokens", {})
    except (ValueError, OSError) as exc:
        print(f"[tokens] {PATH} не читается: {exc}", file=sys.stderr)
        data = {}
    _cache = (mtime, data)
    return data


def check(token: str) -> str | None:
    """Имя клиента, если токен верный, иначе None."""
    if not token:
        return None
    h = _hash(token)
    for name, rec in load().items():
        if hmac.compare_digest(h, str(rec.get("sha256", ""))):
            return name
    return None


def _save(tokens: dict) -> None:
    tmp = PATH.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump({"tokens": tokens}, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, PATH)


def main() -> int:
    args = sys.argv[1:]
    if not args or args[0] not in {"add", "list", "remove"}:
        print(__doc__)
        return 1
    tokens = dict(load())
    if args[0] == "list":
        if not tokens:
            print("токенов нет")
        for name, rec in sorted(tokens.items()):
            print(f"{name}  (создан {rec.get('created', '?')})")
        return 0
    if len(args) != 2:
        print(__doc__)
        return 1
    name = args[1]
    if args[0] == "add":
        if not re.fullmatch(r"[A-Za-z0-9_.@-]{1,64}", name):
            print("имя: латиница, цифры, _ . @ - (до 64 символов)", file=sys.stderr)
            return 1
        if name in tokens:
            print(f"{name} уже есть; чтобы перевыпустить — сначала remove", file=sys.stderr)
            return 1
        token = PREFIX + secrets.token_urlsafe(24)
        tokens[name] = {"sha256": _hash(token), "created": dt.date.today().isoformat()}
        _save(tokens)
        print(token)
        print(f"# токен для «{name}» — покажется только сейчас", file=sys.stderr)
        return 0
    if tokens.pop(name, None) is None:
        print(f"нет такого: {name}", file=sys.stderr)
        return 1
    _save(tokens)
    print(f"отозван: {name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
