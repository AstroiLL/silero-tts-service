#!/usr/bin/env python3
"""Словарь произношения silero-tts-service (ударения и чтение английских слов).

    lexicon.py add "GitHub" "гитх+аб"      добавить/заменить правило
    lexicon.py list [фильтр]               показать словарь
    lexicon.py remove "GitHub"             удалить правило
    lexicon.py test "текст с GitHub"       что реально уйдёт в синтез

Свои слова пишутся в lexicon.local.json (не попадает в git), общий словарь — lexicon.json;
`--base` заставляет add/remove править общий. Свой главнее общего. Применяется сразу,
без перезапуска сервиса.
Знак `+` ставится ПЕРЕД ударной гласной: `сомкн+уты`, `г+ермес`.
Ключи сравниваются без учёта регистра, по границам слова: «Git» не сломает «GitHub».

Author: Ilya Byven aka @AstroiLL (https://astroill.info). License: MIT.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

BASE = Path(__file__).parent
sys.path.insert(0, str(BASE))
import silero_tts as st  # noqa: E402

PATH = st.LOCAL_LEXICON_PATH


def load() -> dict:
    if not PATH.exists():
        return {"words": {}}
    data = json.loads(PATH.read_text(encoding="utf-8"))
    data.setdefault("words", {})
    return data


def save(data: dict) -> None:
    PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    global PATH
    args = sys.argv[1:]
    if "--base" in args:
        args.remove("--base")
        PATH = st.LEXICON_PATH
    cmd, rest = (args[0], args[1:]) if args else ("", [])
    if cmd == "add" and len(rest) == 2:
        data = load()
        old = data["words"].get(rest[0])
        data["words"][rest[0]] = rest[1]
        save(data)
        print(f"{'изменено' if old else 'добавлено'}: {rest[0]} -> {rest[1]}" + (f"  (было: {old})" if old else ""))
        return 0
    if cmd == "remove" and len(rest) == 1:
        data = load()
        for key in list(data["words"]):
            if key.lower() == rest[0].lower():
                print(f"удалено: {key} -> {data['words'].pop(key)}")
                save(data)
                return 0
        print(f"не найдено в {PATH.name}: {rest[0]}", file=sys.stderr)
        return 1
    if cmd == "list":
        items = sorted(st.load_lexicon().items(), key=lambda kv: kv[0].lower())
        if rest:
            low = rest[0].lower()
            items = [(k, v) for k, v in items if low in k.lower() or low in v.lower()]
        width = max((len(k) for k, _ in items), default=0)
        for key, value in items:
            print(f"{key.ljust(width)}  ->  {value}")
        print(f"\nвсего: {len(items)}   (общий {st.LEXICON_PATH.name} + свой {st.LOCAL_LEXICON_PATH.name})")
        return 0
    if cmd == "test" and rest:
        text = " ".join(rest)
        print("исходный :", text)
        print("в синтез :", st.prepare_text(text) or "(нечего произносить)")
        return 0
    print(__doc__)
    return 1


if __name__ == "__main__":
    sys.exit(main())
