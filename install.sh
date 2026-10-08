#!/bin/sh
# silero-tts-service — установка/обновление: venv, torch (CPU), зависимости, .env, модель Silero.
# Запуск из папки репозитория от того пользователя, от чьего имени будет работать сервис:
#   ./install.sh
# Повторный запуск = обновление: .env, токены и скачанную модель не трогает.
set -eu
cd "$(dirname "$0")"

command -v ffmpeg >/dev/null || { echo "Нужен ffmpeg (apt install ffmpeg)"; exit 1; }
PY=${PYTHON:-python3}
"$PY" -c 'import sys; assert sys.version_info >= (3,10), sys.version' \
  || { echo "Нужен Python 3.10+ (задай PYTHON=python3.11)"; exit 1; }

# venv: через uv, если он есть (быстрее, не нужен пакет python3-venv), иначе venv+pip.
[ -x .venv/bin/python ] || rm -rf .venv
if command -v uv >/dev/null; then
  [ -d .venv ] || uv venv -q --python "$PY" .venv
  PIP="uv pip install -q --python .venv/bin/python"
else
  [ -d .venv ] || "$PY" -m venv .venv \
    || { echo "Не удалось создать venv: поставь python3-venv (apt) или uv (https://docs.astral.sh/uv/)"; exit 1; }
  .venv/bin/pip install -q --upgrade pip
  PIP=".venv/bin/pip install -q"
fi
$PIP torch --index-url https://download.pytorch.org/whl/cpu
$PIP -r requirements.txt

if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
  echo "Создан .env — проверь SILERO_HOST (127.0.0.1 или 0.0.0.0 для сети)."
fi

# Модель заранее (~140 МБ), чтобы первый запуск был быстрым.
.venv/bin/python -c 'import silero_tts as s; print("модель:", s.ensure_model())'
echo "Готово. Дальше: токен (.venv/bin/python tokens.py add <имя>) и сервис (deploy/)."
