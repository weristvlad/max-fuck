# max-fuck

Неофициальный Python API для мессенджера **MAX** (ex VK Teams, ex ICQ).

Работает через реверс-инжиниринг WebSocket-протокола веб-версии (`web.max.ru`). Официального пользовательского API нет, бот-API требует юрлицо — эта библиотека решает проблему.

## Установка

```bash
git clone https://github.com/weristvlad/max-fuck
cd max-fuck
pip install -r requirements.txt
```

Зависимости: `websockets`, `qrcode`, `aiohttp`, `aiortc`

## Быстрый старт

```python
import asyncio
from max_api import MaxClient

async def main():
    async with MaxClient() as client:
        await client.auto_login()

        # Отправить сообщение
        await client.send_message(188165680, "привет из API")

        # Получить чаты
        chats = await client.get_chats()
        for chat in chats[:5]:
            print(chat["id"], chat.get("lastMessage", {}).get("text", "")[:50])

asyncio.run(main())
```

## Авторизация

### Первый запуск

`auto_login()` показывает QR-код прямо в терминале:

```
┌─ MAX Login ─────────────────────────────┐
│ Scan this QR code in MAX app on phone:  │
│ Settings → Devices → Scan QR code       │
└─────────────────────────────────────────┘

█▀▀▀▀▀█ ▄▄▄█▄ █▀▀▀▀▀█
█ ███ █ ▀▄  █  █ ███ █
...

QR scanned! Completing auth...
2FA required. Email: o***@m*****.ru
Password hint: ваша подсказка
Enter password: ********

Token saved. Next login will be automatic.
```

1. Сканируешь QR в приложении MAX на телефоне (Настройки → Устройства → Сканировать QR)
2. Если включена 2FA — вводишь пароль (скрыт звёздочками). Если 2FA нет — этот шаг пропускается
3. Токен сохраняется в `~/.max_token.json`

### Последующие запуски

Полностью автоматические — токен подхватывается из файла и обновляется при каждом подключении. QR сканировать не нужно.

### Ручной логин

```python
# Логин по токену напрямую (если есть)
await client.login("your_token_here")

# QR с кастомным обработчиком пароля
token = await client._login_qr(lambda email, hint: "my_password")

# Только ссылка без QR (для кастомных клиентов / своего рендера QR)
await client.auto_login(show_qr=False)

# Сброс сохранённого токена
from max_api import clear_token
clear_token()
```

## API Reference

### MaxClient

#### Подключение

```python
# Как контекстный менеджер (рекомендуется)
async with MaxClient() as client:
    await client.auto_login()
    ...

# Или вручную
client = MaxClient()
await client.connect()
await client.auto_login()
...
await client.disconnect()
```

#### Авторизация

| Метод | Описание |
|-------|----------|
| `auto_login(password=None, show_qr=True)` | Умный логин: сохранённый токен или QR. `password` — чтобы не вводить вручную, `show_qr=False` — только ссылка без QR (для кастомных клиентов) |
| `login(token)` | Логин по токену напрямую |
| `refresh_token()` | Обновить токен (вызывается автоматически) |
| `logout()` | Отключиться и удалить сохранённый токен |

#### Чаты

| Метод | Описание |
|-------|----------|
| `get_chats()` | Список всех чатов |
| `get_chats_updates(marker)` | Чаты, обновлённые после `marker` (timestamp) |
| `get_folders()` | Папки чатов |
| `subscribe_chat(chat_id)` | Подписаться на real-time события чата |

#### Сообщения

| Метод | Описание |
|-------|----------|
| `get_messages(chat_id, from_ts=0, forward=30, backward=30)` | Получить сообщения из чата |
| `get_media_messages(chat_id, message_id, attach_types=["PHOTO","VIDEO"])` | Получить медиа-сообщения вокруг указанного |
| `send_message(chat_id, text, reply_to=None)` | Отправить текстовое сообщение |
| `mark_read(chat_id, message_id)` | Пометить сообщение как прочитанное |
| `send_typing(chat_id)` | Отправить индикатор "печатает..." |

#### Отправка файлов и медиа

| Метод | Описание |
|-------|----------|
| `send_photo(chat_id, file_path, text=None, reply_to=None)` | Загрузить и отправить фото. Поддерживает подпись |
| `send_file(chat_id, file_path, text=None, reply_to=None)` | Загрузить и отправить файл любого типа |
| `send_voice(chat_id, file_path, duration_ms=None, reply_to=None)` | Отправить голосовое сообщение (OGG/MP3) |
| `send_video(chat_id, file_path, text=None, reply_to=None)` | Отправить видео |
| `send_video_message(chat_id, file_path, reply_to=None)` | Отправить кружок (видеосообщение 480x480) |

```python
# Фото
await client.send_photo(chat_id, "photo.png")
await client.send_photo(chat_id, "photo.png", text="с подписью")

# Файл
await client.send_file(chat_id, "document.pdf")

# Голосовое
await client.send_voice(chat_id, "voice.ogg", duration_ms=5000)

# Видео
await client.send_video(chat_id, "video.mp4")

# Кружок (видеосообщение)
await client.send_video_message(chat_id, "circle.mp4")
```

#### Загрузка медиа — как это работает

Отправка файлов идёт в 2 этапа:

1. **Upload** — файл загружается на сервер MAX через HTTP:
   - Фото → `iu.oneme.ru` (multipart/form-data), получаешь `photoToken`
   - Файлы/видео/голосовые → `fu.oneme.ru` (raw binary + Content-Range), получаешь `fileId`
2. **Send** — через WebSocket (opcode 64) отправляется сообщение с аттачем

Всё это автоматизировано в методах `send_photo()`, `send_file()`, `send_voice()`, `send_video()`, `send_video_message()`.

> **Кружки (видеосообщения):** отправляются как обычное видео с `videoType: 1`. Видео должно быть квадратным 480x480. Метод `send_video_message()` делает это автоматически.
>
> **Голосовые:** загружаются как файл, но отправляются с типом `AUDIO`. Рекомендуемый формат — OGG Opus или MP3.

#### Пользователи и поиск

| Метод | Описание |
|-------|----------|
| `get_user(user_id)` | Получить инфу о пользователе по ID (имя, телефон, аватар, ник) |
| `get_contacts(contact_ids)` | Получить инфу о нескольких пользователях разом |
| `get_chat_members(chat_id)` | Получить инфу обо всех участниках чата |
| `find_user(query)` | Поиск пользователей по имени, нику или телефону |
| `search(query, count=30, search_type="ALL")` | Поиск контактов и чатов (низкоуровневый) |
| `get_reactions(chat_id, message_ids)` | Реакции на сообщения |

```python
# Получить инфу о пользователе
user = await client.get_user(6725252)
print(user["names"][0]["name"])  # имя
print(user.get("phone"))         # телефон (если доступен)
print(user.get("link"))          # ссылка на профиль

# Все участники чата
members = await client.get_chat_members(13796912)
for m in members:
    print(f'{m["id"]}: {m["names"][0]["name"]}')

# Поиск по имени/нику
results = await client.find_user("Влад")
for r in results:
    print(r)
```

#### Медиа

| Метод | Описание |
|-------|----------|
| `get_video_url(video_id, token, chat_id, message_id)` | Получить прямую ссылку на видео для скачивания/воспроизведения |

```python
# Получить URL видео из сообщения
for attach in message.get("attaches", []):
    if attach["_type"] == "VIDEO":
        urls = await client.get_video_url(
            attach["videoId"], attach["token"], chat_id, message["id"]
        )
        print(urls)  # {"MP4_480": "https://...", "MP4_720": "https://..."}
```

#### Звонки

| Метод | Описание |
|-------|----------|
| `initiate_call(user_ids, is_video=False)` | Начать звонок (аудио/видео). Возвращает WebRTC параметры |
| `get_call_history(count=100)` | История звонков |

```python
# Аудиозвонок с WebRTC (полноценный, с микрофоном)
call = await client.call([remote_user_id], is_video=False)
await call.wait(timeout=60)  # ждать до 60 секунд
await call.hangup()

# Видеозвонок
call = await client.call([remote_user_id], is_video=True)

# Записать входящее аудио в файл
call = await client.call([remote_user_id], audio_output="recording.wav")

# Низкоуровневый initiate (только сигнализация, без WebRTC)
result = await client.initiate_call([user_id], is_video=True)
# result содержит conversationId, WebRTC endpoint, TURN/STUN

# История звонков
calls = await client.get_call_history()
for call in calls.get("history", []):
    attach = call["message"]["attaches"][0]
    print(f'{attach["callType"]} — {attach["hangupType"]} — {attach["duration"]}ms')
```

#### Стикеры

| Метод | Описание |
|-------|----------|
| `get_sticker_sets(section_id, offset, count)` | Получить список ID наборов стикеров |
| `sync_stickers(sticker_type, sync)` | Синхронизировать стикеры/анимодзи |

#### События (real-time)

```python
# Обработчик входящих сообщений
def on_msg(payload):
    msg = payload["message"]
    print(f'{msg["sender"]}: {msg["text"]}')

client.on_message(on_msg)   # новые сообщения
client.on_presence(handler)  # статус онлайн/оффлайн
client.on_call(handler)      # входящие звонки
client.on(opcode, handler)   # любой серверный push по опкоду
```

Хендлеры могут быть синхронными или `async`.

##### Типы аттачей во входящих сообщениях

| `_type` | Описание | Ключевые поля |
|---------|----------|---------------|
| `PHOTO` | Фото | `photoId`, `width`, `height`, `baseUrl` |
| `FILE` | Файл | `fileId`, `name`, `size`, `token` |
| `VIDEO` | Видео / кружок | `videoId`, `videoType` (0=видео, 1=кружок), `width`, `height`, `duration`, `token` |
| `AUDIO` | Голосовое | `audioId`, `duration`, `url`, `wave` |
| `CALL` | Звонок | `callType`, `hangupType`, `duration`, `conversationId` |
| `SHARE` | Ссылка | `url`, `host` |
| `CONTROL` | Системное | — |

## Примеры

### Эхо-бот

```python
import asyncio
from max_api import MaxClient

async def main():
    client = MaxClient()
    await client.connect()
    await client.auto_login()

    async def on_message(payload):
        msg = payload.get("message", {})
        chat_id = payload.get("chatId")
        text = msg.get("text", "")
        if text:
            await client.send_message(chat_id, f"Echo: {text}")

    client.on_message(on_message)
    await asyncio.Future()  # работает вечно

asyncio.run(main())
```

### Прочитать последние сообщения

```python
async with MaxClient() as client:
    await client.auto_login()
    messages = await client.get_messages(188165680, backward=10)
    for msg in messages:
        print(f'[{msg["sender"]}] {msg["text"]}')
```

### Отправить фото с подписью

```python
async with MaxClient() as client:
    await client.auto_login()
    await client.send_photo(chat_id, "screenshot.png", text="смотри что нашёл")
```

### Мониторинг всех входящих с медиа

```python
async with MaxClient() as client:
    await client.auto_login()

    def handler(payload):
        chat = payload["chatId"]
        msg = payload["message"]
        text = msg.get("text", "")
        attaches = msg.get("attaches", [])

        if text:
            print(f"[{chat}] {msg['sender']}: {text}")
        for a in attaches:
            t = a["_type"]
            if t == "PHOTO":
                print(f"[{chat}] фото: {a['baseUrl']}")
            elif t == "VIDEO":
                kind = "кружок" if a.get("videoType") == 1 else "видео"
                print(f"[{chat}] {kind}: {a['duration']}ms")
            elif t == "AUDIO":
                print(f"[{chat}] голосовое: {a['duration']}ms")

    client.on_message(handler)
    await asyncio.Future()
```

## Протокол

Всё общение идёт через WebSocket на `wss://ws-api.oneme.ru/websocket`.

Формат сообщений:
```json
{"ver": 11, "cmd": 0, "seq": 1, "opcode": 64, "payload": {...}}
```

- `ver` — версия протокола (11)
- `cmd` — 0: запрос, 1: ответ, 3: ошибка
- `seq` — порядковый номер для матчинга запрос/ответ
- `opcode` — код операции
- `payload` — данные

### Карта опкодов

| Opcode | Константа | Описание |
|--------|-----------|----------|
| 1 | `PING` | Keep-alive |
| 5 | `ANALYTICS` | Аналитика/телеметрия |
| 6 | `INIT` | Инициализация соединения |
| 19 | `LOGIN` | Логин по токену |
| 26 | `GET_STICKER_SETS` | Наборы стикеров |
| 27 | `STICKER_SYNC` | Синхронизация стикеров |
| 28 | `ANIMOJI` | Анимодзи/реакции |
| 32 | `GET_CONTACTS` | Информация о контактах |
| 48 | `GET_CHATS` | Список чатов |
| 49 | `GET_MESSAGES` | Сообщения из чата |
| 50 | `MARK_READ` | Пометить прочитанным |
| 51 | `GET_MEDIA_MESSAGES` | Медиа-сообщения по типу |
| 53 | `GET_CHATS_UPDATES` | Обновления чатов |
| 60 | `SEARCH` | Поиск |
| 64 | `SEND_MESSAGE` | Отправка сообщения |
| 65 | `TYPING` | Индикатор набора |
| 75 | `SUBSCRIBE_CHAT` | Подписка на события чата |
| 78 | `INITIATE_CALL` | Начало исходящего звонка |
| 79 | `GET_CALL_HISTORY` | История звонков |
| 80 | `GET_IMAGE_UPLOAD_URL` | Получить URL для загрузки фото |
| 83 | `GET_VIDEO` | Получить URL видео |
| 87 | `GET_FILE_UPLOAD_URL` | Получить URL для загрузки файла |
| 115 | `PASSWORD_AUTH` | 2FA пароль |
| 128 | `PUSH_NEW_MESSAGE` | Пуш: новое сообщение |
| 129 | `PUSH_CONTENT_ACK` | Пуш: подтверждение доставки медиа |
| 130 | `SET_CHAT_READ_STATE` | Пометить чат прочитанным/непрочитанным |
| 132 | `PUSH_PRESENCE` | Пуш: статус пользователя |
| 136 | `CHECK_FILE_UPLOAD` | Подтверждение загрузки файла |
| 137 | `PUSH_INCOMING_CALL` | Пуш: входящий звонок |
| 158 | `TOKEN_REFRESH` | Обновление токена |
| 177 | `GET_USER_STORIES` | Статусы/сторис пользователей |
| 180 | `GET_REACTIONS` | Реакции на сообщения |
| 272 | `GET_FOLDERS` | Папки чатов |
| 288 | `QR_AUTH_INIT` | Начало QR-авторизации |
| 289 | `QR_AUTH_POLL` | Поллинг статуса QR |
| 291 | `QR_AUTH_COMPLETE` | Завершение QR-авторизации |
| 292 | `PUSH_BANNERS` | Баннеры/промо |

### Загрузка файлов — эндпоинты

| Тип | WS opcode | HTTP endpoint | Формат загрузки |
|-----|-----------|---------------|-----------------|
| Фото | 80 | `iu.oneme.ru/uploadImage` | multipart/form-data, field `"file"` |
| Файлы | 87 | `fu.oneme.ru/api/upload.do` | raw binary + Content-Range |
| Видео | 87* | `vu.okcdn.ru/upload.do` | raw binary video/mp4 + Content-Range |

*Видео-загрузка использует отдельный CDN (`vu.okcdn.ru`), URL выдаётся сервером.

## Структура проекта

```
max-fuck/
├── max_api/
│   ├── __init__.py     # Экспорты
│   ├── client.py       # MaxClient — основной клиент
│   ├── auth.py         # Токены, QR-код в терминале
│   └── opcodes.py      # Все опкоды протокола
├── examples/
│   ├── basic.py        # Логин + чаты + сообщения
│   └── echo_bot.py     # Эхо-бот
├── test_send.py        # Тест отправки
└── requirements.txt
```

## Ограничения

- Работает от имени обычного аккаунта, не бота
- QR нужно сканировать один раз (потом токен переиспользуется)
- MAX может изменить протокол в любой момент — это реверс-инжиниринг
- Звонки: полноценная реализация через aiortc (WebRTC). Может потребовать настройки микрофона на вашей ОС
- Голосовые/кружки: отправка реализована, но точный формат может потребовать тестирования

## Лицензия

MIT. Используй на свой страх и риск.

## Disclaimer

Этот проект полностью навайбкоженный (vibe-coded). Автор не программист, но всё работает. Код написан с помощью AI, протестирован руками. Если что-то сломалось — issue / PR welcome.
