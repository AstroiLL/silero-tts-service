# Установка silero-tts-service (инструкция для ИИ-агента)

Цель: на сервере (или в сети) работает **один** сервис синтеза речи Silero, а агенты и программы
обращаются к нему по токенам.

Делай шаги по порядку и проверяй результат каждого. Токены не печатай в чат: записывай их сразу в
конфиг клиента. Если токен нужен человеку, передай его приватно.

## 0. Сервис уже есть?

```sh
curl -s http://127.0.0.1:7470/health
```
Ответ `{"ok":true,...}` значит, что сервис уже работает на этом сервере. Спроси пользователя и о
других машинах: синтез мог быть вынесен на мощный сервер в сети (`curl -s http://<адрес>:7470/health`).

Если сервис нашёлся, **не ставь второй.** Нужен только токен: его выпускает владелец сервиса
(шаг 5, на машине с сервисом). Дальше переходи к шагу 6.

Модели Silero бесплатны только для **некоммерческого** использования (CC BY-NC 4.0). Если сервис
ставят для бизнеса, предупреди пользователя: нужна лицензия Silero (hello@silero.ai).

## 1. Что нужно

| Что | Проверка |
|---|---|
| Linux с systemd, ~1.2 ГБ диска, ~1 ГБ RAM | `df -h`, `free -h` |
| Python 3.10+ и модуль venv **или** [uv](https://docs.astral.sh/uv/) | `python3 -m venv --help` / `uv --version` |
| ffmpeg, git | `ffmpeg -version`, `git --version` |

GPU не нужен. На обычном CPU фраза озвучивается примерно за 0,2 с.

## 2. Выбрать режим (спроси пользователя)

| Режим | Когда | Нужен root |
|---|---|---|
| **A. user-сервис** одного пользователя | свой сервер, один владелец; остальные пользователи ходят по токенам | нет (кроме `enable-linger`) |
| **B. системный сервис** от пользователя `silero-tts` | сервер, где работают несколько человек: сервис не зависит ни от чьей учётки | да |

`SILERO_HOST` в `.env`: `127.0.0.1`, если клиенты только на этом сервере; `0.0.0.0`, если нужен
доступ с других машин в сети. Доступ в обоих случаях только по токенам. Трафик идёт по HTTP,
это нормально для домашней сети. Для доступа из интернета нужен reverse proxy с HTTPS.

## 3. Скачать и установить

**Режим A:**
```sh
cd ~ && git clone https://github.com/AstroiLL/silero-tts-service.git && cd silero-tts-service
./install.sh          # venv, torch CPU, зависимости, .env, модель (~140 МБ); 1–3 минуты
```

**Режим B** (от root):
```sh
useradd --system --home-dir /opt/silero-tts-service --shell /usr/sbin/nologin silero-tts
git clone https://github.com/AstroiLL/silero-tts-service.git /opt/silero-tts-service
chown -R silero-tts: /opt/silero-tts-service
cd /opt/silero-tts-service && sudo -u silero-tts ./install.sh
```
Если системный Python старше 3.10: `PYTHON=python3.11 ./install.sh`.

## 4. Запустить

Сначала поправь `.env` (`SILERO_HOST`, голос по умолчанию `SILERO_VOICE`).

**Режим A:**
```sh
mkdir -p ~/.config/systemd/user
sed "s|@DIR@|$PWD|g" deploy/silero-tts.service > ~/.config/systemd/user/silero-tts.service
systemctl --user daemon-reload && systemctl --user enable --now silero-tts
loginctl enable-linger "$USER"     # чтобы жил без входа пользователя (может понадобиться sudo)
```

**Режим B:**
```sh
cp deploy/silero-tts-system.service /etc/systemd/system/silero-tts.service
systemctl daemon-reload && systemctl enable --now silero-tts
```

Проверка (модель грузится ~5 с):
```sh
curl -s http://127.0.0.1:7470/health        # {"ok":true,"model":"v5_5_ru","voices":[...]}
```

## 5. Выдать токены: по одному на каждого клиента

```sh
.venv/bin/python tokens.py add hermes-ivan        # режим B: sudo -u silero-tts .venv/bin/python tokens.py ...
.venv/bin/python tokens.py list
.venv/bin/python tokens.py remove hermes-ivan     # отзыв действует сразу
```
Команда `add` печатает токен **один раз**. Сразу запиши его в конфиг клиента (шаг 6). Имя токена
видно в журнале сервиса: так понятно, кто сколько озвучивает.

Проверка:
```sh
curl -s -o /tmp/stts.ogg -w '%{http_code}\n' http://127.0.0.1:7470/v1/audio/speech \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -d '{"input":"Проверка связи","response_format":"opus"}'      # 200, файл Ogg Opus
```

## 6. Подключить клиентов

Адрес сервиса ниже — `http://<хост>:7470`. Для клиента на том же сервере это `http://127.0.0.1:7470`.

**Hermes Agent: встроенный провайдер OpenAI** (рекомендуется; Telegram получает голосовые сообщения):
```sh
hermes config set tts.provider openai
hermes config set tts.openai.base_url http://127.0.0.1:7470/v1
hermes config set tts.openai.api_key <токен>
hermes config set tts.openai.model silero
hermes config set tts.openai.voice eugene
```
Если команда отвечает «not a recognized config key», повтори её с `--force`. Затем перезапусти gateway
Hermes и предупреди пользователя: разговор прервётся.

⚠️ **Если у агента уже настроен платный TTS** (OpenAI, ElevenLabs и т.п.), не меняй его молча. Спроси
пользователя, что использовать. Если секция `tts.openai` занята настоящим OpenAI, подключи Silero
command-провайдером (ниже), тогда OpenAI останется нетронутым.

**Hermes или другой агент: TTS внешней командой.** Используй `client/say.sh`. Torch клиенту не нужен:
```sh
mkdir -p ~/.config/silero-tts && install -m 600 /dev/null ~/.config/silero-tts/client.env
printf 'SILERO_TTS_URL=http://127.0.0.1:7470\nSILERO_TTS_KEY=%s\n' "<токен>" > ~/.config/silero-tts/client.env
hermes config set --force tts.providers.silero \
  '{"type":"command","command":"<путь>/client/say.sh {text_path} {output_path} {voice}","voice":"eugene","voice_compatible":true}'
hermes config set tts.provider silero
```
Файл `client/say.sh` можно скопировать куда угодно: он самодостаточен.

**[Чуйко](https://github.com/AstroiLL/chuyko)** (голосовой веб-интерфейс): в его `.env`
```
TTS_URL=http://127.0.0.1:7470/v1
TTS_KEY=<токен>
```

**Любой клиент OpenAI TTS** (Open WebUI, n8n, SDK): base URL `http://<хост>:7470/v1`, API key = токен,
модель любая, голос из `/v1/audio/voices`. Голоса OpenAI (`alloy` и др.) заменяются голосом по умолчанию.

## 7. Словарь ударений

Общий словарь лежит в `lexicon.json`, свои слова сервера в `lexicon.local.json` (вне git). Знак `+`
ставится перед ударной гласной. Правки применяются сразу.
```sh
.venv/bin/python lexicon.py add "Чуйко" "ч+уйко"
.venv/bin/python lexicon.py test "Чуйко запускает Docker"     # что уйдёт в синтез
```
Клиент может передать ударения только для своего запроса: поле `"lexicon": {"слово": "сл+ово"}`.

## Обновление

```sh
cd <папка сервиса> && git pull && ./install.sh && systemctl --user restart silero-tts   # режим B: sudo systemctl restart silero-tts
```
`.env`, `tokens.json`, `lexicon.local.json` и модель обновление не трогает.

## Если не работает

| Симптом | Причина / решение |
|---|---|
| `/health` не отвечает | `journalctl --user -u silero-tts -n 50` (режим B: `journalctl -u silero-tts`) |
| `401 bad token` | токен отозван или с опечаткой; выпусти новый (`tokens.py add`) |
| С другой машины не подключиться | `SILERO_HOST=0.0.0.0` в `.env` + перезапуск; файрвол, порт 7470 |
| Английские слова пропадают или звучат криво | добавь их в словарь (`lexicon.py add`) |
| `400 input longer than ...` | увеличь `SILERO_MAX_CHARS` в `.env` |
| Медленно на длинных текстах | `SILERO_THREADS` = число физических ядер; или вынеси сервис на мощную машину |
