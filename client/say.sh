#!/bin/sh
# Клиент silero-tts-service для агентов с TTS «внешней командой» (Hermes command-провайдер,
# OpenClaw, скрипты). Torch и модели не нужны: только curl и python3.
#
#   say.sh <файл с текстом | -> <выходной файл> [голос] [скорость]
#   формат — по расширению выхода: .ogg/.opus .mp3 .wav .flac .aac
#
# Адрес и токен: переменные SILERO_TTS_URL / SILERO_TTS_KEY или файл
#   ${SILERO_TTS_CONF:-~/.config/silero-tts/client.env}  (права 600):
#     SILERO_TTS_URL=http://127.0.0.1:7470
#     SILERO_TTS_KEY=stts_...
#
# Author: Ilya Byven aka @AstroiLL (https://astroill.info). License: MIT.
set -eu

CONF=${SILERO_TTS_CONF:-${XDG_CONFIG_HOME:-$HOME/.config}/silero-tts/client.env}
if [ -f "$CONF" ]; then
    URL_ENV=${SILERO_TTS_URL:-}; KEY_ENV=${SILERO_TTS_KEY:-}
    # shellcheck disable=SC1090
    . "$CONF"
    SILERO_TTS_URL=${URL_ENV:-${SILERO_TTS_URL:-}}; SILERO_TTS_KEY=${KEY_ENV:-${SILERO_TTS_KEY:-}}
fi
: "${SILERO_TTS_URL:?нет SILERO_TTS_URL (см. $CONF)}"
: "${SILERO_TTS_KEY:?нет SILERO_TTS_KEY (см. $CONF)}"

[ $# -ge 2 ] || { echo "usage: say.sh <text_file|-> <out> [voice] [speed]" >&2; exit 2; }
IN=$1; OUT=$2; VOICE=${3:-${SILERO_VOICE:-}}; SPEED=${4:-${SILERO_SPEED:-1.0}}

case "$OUT" in
    *.ogg|*.oga|*.opus) FMT=opus ;;
    *.mp3) FMT=mp3 ;; *.flac) FMT=flac ;; *.aac|*.m4a) FMT=aac ;;
    *) FMT=wav ;;
esac

BASE=${SILERO_TTS_URL%/}; BASE=${BASE%/v1}     # адрес можно писать и с /v1, и без
BODY=$(mktemp "${TMPDIR:-/tmp}/stts_XXXXXX.json")
trap 'rm -f "$BODY"' EXIT
if [ "$IN" = "-" ]; then IN=/dev/stdin; fi
python3 - "$IN" "$VOICE" "$FMT" "$SPEED" > "$BODY" <<'PY'
import json, sys
src, voice, fmt, speed = sys.argv[1:5]
text = open(src, encoding="utf-8").read()
print(json.dumps({"model": "silero", "input": text, "voice": voice, "response_format": fmt,
                  "speed": float(speed)}, ensure_ascii=False))
PY

CODE=$(curl -sS -o "$OUT" -w '%{http_code}' --max-time 300 \
    -H "Authorization: Bearer $SILERO_TTS_KEY" -H 'Content-Type: application/json' \
    --data-binary @"$BODY" "$BASE/v1/audio/speech")
if [ "$CODE" != 200 ]; then
    echo "silero-tts-service: HTTP $CODE: $(head -c 300 "$OUT" 2>/dev/null)" >&2
    rm -f "$OUT"
    exit 1
fi
