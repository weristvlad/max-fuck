# max-fuck

Неофициальный Python API для мессенджера **MAX** (ex VK Teams, ex ICQ).

Работает через реверс-инжиниринг WebSocket-протокола веб-версии (`web.max.ru`) + дизассемблирование APK v26.11.0. Официального пользовательского API нет, бот-API требует юрлицо — эта библиотека решает проблему.

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
            print(chat.get("chatId"), chat.get("title"))

asyncio.run(main())
```

## Авторизация

### QR-код (первый запуск)

`auto_login()` показывает QR-код прямо в терминале:

```
┌─ MAX Login ─────────────────────────────┐
│ Scan this QR code in MAX app on phone:  │
│ Settings → Devices → Scan QR code       │
└─────────────────────────────────────────┘

█▀▀▀▀▀█ ▄▄▄█▄ █▀▀▀▀▀█
█ ███ █ ▀▄  █  █ ███ █
...

Token saved. Next login will be automatic.
```

1. Сканируешь QR в приложении MAX (Настройки → Устройства → Сканировать QR)
2. Если включена 2FA — вводишь пароль
3. Токен сохраняется в `~/.max_token.json`

### SMS-авторизация (новое)

Вход по номеру телефона без существующей сессии:

```python
async with MaxClient() as client:
    # Автоматически: если нет токена → SMS
    await client.auto_login(phone="+79001234567")

    # Или напрямую:
    await client.login_sms("+79001234567")
```

Флоу: номер → SMS-код → 2FA (если есть) → сессия.

### Последующие запуски

Автоматические — токен из `~/.max_token.json`, обновляется при каждом подключении.

### Все методы авторизации

```python
# Smart login: токен → SMS (если phone задан) → QR
await client.auto_login(phone="+7...", password="2fa_pass")

# SMS напрямую
await client.login_sms("+79001234567", password="2fa_pass")

# QR без рендера (только ссылка)
await client.auto_login(show_qr=False)

# Токен напрямую
await client.login("your_token_here")

# Сброс сохранённого токена
from max_api import clear_token
clear_token()
```

## API Reference

### Подключение

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

### Чаты

| Метод | Описание |
|-------|----------|
| `get_chats(chat_ids=None)` | Список чатов. `[0]` = все |
| `get_chats_updates(marker=0)` | Чаты, обновлённые после `marker` |
| `create_chat(title, member_ids, chat_type="GROUP")` | Создать чат/канал |
| `update_chat(chat_id, title=None, about=None)` | Изменить название/описание |
| `delete_chat(chat_id)` | Удалить чат |
| `clear_chat(chat_id)` | Очистить историю |
| `hide_chat(chat_id)` | Скрыть из списка |
| `join_chat(chat_link)` | Вступить по ссылке |
| `leave_chat(chat_id)` | Покинуть чат |
| `check_chat_link(link)` | Проверить ссылку без вступления |
| `subscribe_chat(chat_id, subscribe=True)` | Подписка на real-time события |
| `get_chat_members_list(chat_id)` | Список участников (группы/каналы) |
| `get_chat_members(chat_id)` | Инфо обо всех участниках чата |
| `update_chat_members(chat_id, add=[], remove=[])` | Добавить/удалить участников |
| `get_common_chats(user_ids)` | Общие чаты с пользователем |

### Папки

| Метод | Описание |
|-------|----------|
| `get_folders()` | Получить все папки |
| `get_folder(folder_id)` | Получить папку по ID (строка) |
| `update_folder(folder_id, title=None, chat_ids=None)` | Обновить папку |
| `reorder_folders(folder_ids)` | Изменить порядок папок |

### Сообщения

| Метод | Описание |
|-------|----------|
| `get_messages(chat_id, from_ts=0, forward=30, backward=30)` | Получить сообщения |
| `get_message(chat_id, message_ids)` | Получить сообщение(я) по ID (int или list[int]) |
| `get_media_messages(chat_id, message_id, attach_types=["PHOTO","VIDEO"])` | Медиа-сообщения |
| `send_message(chat_id, text, reply_to=None, elements=None, send_time=None)` | Отправить текст |
| `edit_message(chat_id, message_id, text, elements=None)` | Редактировать |
| `delete_message(chat_id, message_id, for_all=True)` | Удалить |
| `delete_message_range(chat_id, from_id, to_id)` | Удалить диапазон |
| `forward_messages(chat_id, from_chat_id, message_ids)` | Переслать |
| `mark_read(chat_id, message_id)` | Пометить прочитанным |
| `send_typing(chat_id)` | Индикатор "печатает..." |
| `get_message_link(chat_id, message_id)` | Получить ссылку на сообщение |
| `get_link_info(url)` | Превью ссылки (Open Graph) |
| `search_messages(chat_id, query, count=30)` | Поиск по чату |
| `search_chats(query, count=30)` | Поиск чатов по названию |
| `pin_message(chat_id)` | Показать закреплённое |
| `unpin_message(chat_id)` | Скрыть закреплённое |

### Форматирование текста

```python
from max_api import parse_formatted_text

text, elements = parse_formatted_text(
    "**жирный** *курсив* ~~зачёркнутый~~ ++подчёркнутый++ `код` [ссылка](https://max.ru)"
)
await client.send_message(chat_id, text, elements=elements)
```

Поддержка: `**bold**`, `*italic*`, `***bold italic***`, `~~strike~~`, `++underline++`, `^^highlight^^`, `` `code` ``, `[text](url)`

### Отправка файлов

| Метод | Описание |
|-------|----------|
| `send_photo(chat_id, file_path, text=None, reply_to=None)` | Фото |
| `send_file(chat_id, file_path, text=None, reply_to=None)` | Любой файл |
| `send_voice(chat_id, file_path, duration_ms=None, reply_to=None)` | Голосовое (OGG/MP3/WAV) |
| `send_video(chat_id, file_path, text=None, reply_to=None)` | Видео |
| `send_video_message(chat_id, file_path, reply_to=None)` | Кружок (480x480) |

```python
await client.send_photo(chat_id, "photo.png", text="подпись")
await client.send_file(chat_id, "document.pdf")
await client.send_voice(chat_id, "voice.ogg", duration_ms=5000)
await client.send_video(chat_id, "video.mp4")
await client.send_video_message(chat_id, "circle.mp4")
```

### Контакты и пользователи

| Метод | Описание |
|-------|----------|
| `get_user(user_id)` | Инфо о пользователе (имя, телефон, аватар) |
| `get_contacts(contact_ids)` | Инфо о нескольких пользователях |
| `find_user(query)` | Поиск в контактах |
| `contact_search(query, count=30)` | Поиск в своих контактах |
| `contact_by_phone(phone)` | Найти по номеру телефона |
| `mutual_contacts(user_id)` | Общие контакты |
| `get_user_score(user_id)` | Рейтинг/карма |
| `search(query, count=30, search_type=None)` | Публичный поиск. Типы: `ALL`, `CHANNELS`, `PUBLIC_CHATS` |

```python
user = await client.get_user(6725252)
print(user["names"][0]["name"])

members = await client.get_chat_members(chat_id)
for m in members:
    print(f'{m["id"]}: {m["names"][0]["name"]}')
```

### Реакции

| Метод | Описание |
|-------|----------|
| `react(chat_id, message_id, emoji)` | Поставить реакцию |
| `remove_reaction(chat_id, message_id)` | Убрать реакцию |
| `get_reactions(chat_id, message_ids)` | Сводка реакций |
| `get_detailed_reactions(chat_id, message_id, emoji=None)` | Кто поставил реакцию |
| `set_chat_reaction_settings(chat_id, emojis)` | Настроить доступные реакции |
| `get_chat_reaction_settings(chat_ids)` | Получить настройки реакций |

```python
await client.react(chat_id, msg_id, "👍")
reactions = await client.get_reactions(chat_id, [msg_id])
await client.remove_reaction(chat_id, msg_id)
```

### Черновики

| Метод | Описание |
|-------|----------|
| `save_draft(chat_id, text)` | Сохранить черновик |
| `discard_draft(chat_id)` | Удалить черновик |

### Звонки

| Метод | Описание |
|-------|----------|
| `call(user_ids, is_video=False, audio_output=None)` | Полноценный WebRTC-звонок |
| `initiate_call(user_ids, is_video=False)` | Низкоуровневый: только сигнализация |
| `get_call_history(count=100)` | История звонков |

```python
# Аудиозвонок
call = await client.call([user_id])
await call.wait(timeout=60)
await call.hangup()

# С записью входящего аудио
call = await client.call([user_id], audio_output="recording.wav")
```

### Стикеры

| Метод | Описание |
|-------|----------|
| `get_sticker_sets(section_id, offset, count)` | Наборы стикеров |
| `sync_stickers(sticker_type, sync)` | Синхронизация |

### Сессии

| Метод | Описание |
|-------|----------|
| `get_sessions()` | Список активных сессий |
| `close_session(session_id)` | Закрыть сессию |

### Прочее

| Метод | Описание |
|-------|----------|
| `complain_reasons()` | Список причин для жалоб |
| `logout()` | Выход + удаление токена |

### События (real-time)

```python
client.on_message(handler)          # входящие сообщения
client.on_typing(handler)           # индикатор набора
client.on_presence(handler)         # онлайн/оффлайн
client.on_call(handler)             # входящие звонки
client.on_chat_update(handler)      # изменения чатов
client.on_reactions(handler)        # изменения реакций
client.on_delayed_message(handler)  # отложенные сообщения
client.on_mark(handler)             # прочитано в другой сессии
client.on_contact(handler)          # изменения контактов
client.on_location(handler)         # геолокация
client.on_folder_update(handler)    # изменения папок
client.on_delete_range(handler)     # пакетное удаление
client.on(opcode, handler)          # любой push по опкоду
```

Хендлеры могут быть `sync` или `async`.

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
    await asyncio.Future()

asyncio.run(main())
```

### SMS-логин + отправка

```python
import asyncio
from max_api import MaxClient

async def main():
    async with MaxClient() as client:
        await client.auto_login(phone="+79001234567")
        await client.send_message(chat_id, "залогинился по SMS!")

asyncio.run(main())
```

### Мониторинг с реакциями

```python
async with MaxClient() as client:
    await client.auto_login()

    async def handler(payload):
        chat = payload["chatId"]
        msg = payload["message"]
        text = msg.get("text", "")
        if text:
            print(f"[{chat}] {msg['sender']}: {text}")
            # Автореакция на все сообщения
            await client.react(chat, msg["id"], "👍")

    client.on_message(handler)
    await asyncio.Future()
```

## Протокол

WebSocket: `wss://ws-api.oneme.ru/websocket`, протокол v11, APP_VERSION 26.11.0.

```json
{"ver": 11, "cmd": 0, "seq": 1, "opcode": 64, "payload": {...}}
```

- `cmd` — 0: запрос, 1: ответ, 2: push, 3: ошибка
- `seq` — порядковый номер для матчинга запрос/ответ

### Полная карта опкодов

<details>
<summary>Развернуть (80+ опкодов)</summary>

| Opcode | Константа | Описание |
|--------|-----------|----------|
| 1 | `PING` | Keep-alive |
| 2 | `DEBUG` | Отладка |
| 3 | `RECONNECT` | Переподключение |
| 5 | `ANALYTICS` | Аналитика |
| 6 | `INIT` | Инициализация соединения |
| 16 | `PROFILE` | Профиль |
| 17 | `AUTH_REQUEST` | SMS-авторизация: отправка номера |
| 18 | `AUTH` | SMS-авторизация: подтверждение кодом |
| 19 | `LOGIN` | Логин по токену |
| 20 | `LOGOUT` | Выход |
| 21 | `SYNC` | Синхронизация |
| 22 | `CONFIG` | Конфигурация |
| 23 | `AUTH_CONFIRM` | Подтверждение авторизации |
| 25 | `PRESET_AVATARS` | Пресетные аватарки |
| 26 | `GET_STICKER_SETS` | Наборы стикеров |
| 27 | `STICKER_SYNC` | Синхронизация стикеров |
| 28 | `ANIMOJI` | Анимодзи |
| 29 | `ASSETS_ADD` | Добавить ассет |
| 32 | `GET_CONTACTS` | Информация о контактах |
| 33 | `CONTACT_ADD` | Добавить контакт |
| 34 | `CONTACT_UPDATE` | Обновить контакт |
| 35 | `CONTACT_PRESENCE` | Присутствие |
| 36 | `CONTACT_LIST` | Список контактов |
| 37 | `CONTACT_SEARCH` | Поиск в контактах |
| 38 | `CONTACT_MUTUAL` | Общие контакты |
| 39 | `CONTACT_PHOTOS` | Фото контакта |
| 40 | `CONTACT_SORT` | Сортировка |
| 41 | `CONTACT_CREATE` | Создать контакт |
| 42 | `CONTACT_VERIFY` | Верификация |
| 43 | `REMOVE_CONTACT_PHOTO` | Удалить фото |
| 44 | `OWN_CONTACT_SEARCH` | Поиск собственного контакта |
| 45 | `CONTACT_INFO_EXTERNAL` | Внешняя инфо |
| 46 | `CONTACT_INFO_BY_PHONE` | Поиск по телефону |
| 48 | `GET_CHATS` | Список чатов |
| 49 | `GET_MESSAGES` | Сообщения |
| 50 | `MARK_READ` | Прочитано |
| 51 | `GET_MEDIA_MESSAGES` | Медиа |
| 52 | `DELETE_CHAT` | Удалить чат |
| 53 | `GET_CHATS_UPDATES` | Обновления |
| 54 | `CLEAR_CHAT` | Очистить |
| 55 | `UPDATE_CHAT` | Обновить |
| 56 | `CHECK_CHAT_LINK` | Проверить ссылку |
| 57 | `JOIN_CHAT` | Вступить |
| 58 | `LEAVE_CHAT` | Покинуть |
| 59 | `GET_CHAT_MEMBERS` | Участники |
| 60 | `SEARCH` | Поиск |
| 61 | `CLOSE_CHAT` | Закрыть |
| 63 | `CREATE_CHAT` | Создать |
| 64 | `SEND_MESSAGE` | Отправить |
| 65 | `TYPING` | Печатает |
| 66 | `DELETE_MESSAGE` | Удалить сообщение |
| 67 | `EDIT_MESSAGE` | Редактировать |
| 68 | `SEARCH_CHATS` | Поиск чатов |
| 69 | `FORWARD_MESSAGE` | Переслать |
| 70 | `MSG_SHARE_PREVIEW` | Превью ссылки |
| 71 | `GET_MESSAGE` | Получить сообщение |
| 72 | `SEARCH_TOUCH` | Поиск (touch) |
| 73 | `SEARCH_MESSAGES` | Поиск в чате |
| 74 | `GET_MESSAGE_STATS` | Статистика |
| 75 | `SUBSCRIBE_CHAT` | Подписка |
| 76 | `VIDEO_CHAT_START` | Видеочат |
| 77 | `UPDATE_CHAT_MEMBERS` | Обновить участников |
| 78 | `INITIATE_CALL` | Начать звонок |
| 79 | `GET_CALL_HISTORY` | История звонков |
| 80 | `GET_IMAGE_UPLOAD_URL` | URL загрузки фото |
| 81 | `GET_STICKER_UPLOAD_URL` | URL загрузки стикера |
| 82 | `GET_VIDEO_UPLOAD_URL` | URL загрузки видео |
| 83 | `GET_VIDEO` | URL видео |
| 84 | `VIDEO_CHAT_CREATE_JOIN_LINK` | Ссылка на звонок |
| 86 | `CHAT_PIN_SET_VISIBILITY` | Закреп |
| 87 | `GET_FILE_UPLOAD_URL` | URL загрузки файла |
| 88 | `GET_FILE_DOWNLOAD_URL` | URL скачивания файла |
| 89 | `GET_LINK_INFO` | Превью ссылки |
| 90 | `GET_MESSAGE_LINK` | Ссылка на сообщение |
| 92 | `MSG_DELETE_RANGE` | Удалить диапазон |
| 96 | `GET_SESSIONS` | Сессии |
| 97 | `CLOSE_SESSION` | Закрыть сессию |
| 101 | `AUTH_LOGIN_RESTORE_PASSWORD` | Восстановление пароля |
| 103 | `GET_INBOUND_CALLS` | Входящие звонки |
| 112 | `AUTH_CREATE_TRACK` | Трек авторизации |
| 113 | `AUTH_CHECK_PASSWORD` | Проверка пароля |
| 115 | `PASSWORD_AUTH` | 2FA пароль |
| 127 | `GET_LAST_MENTIONS` | Последние упоминания |
| 158 | `TOKEN_REFRESH` | Обновление токена |
| 162 | `COMPLAIN_REASONS_GET` | Причины жалоб |
| 176 | `DRAFT_SAVE` | Сохранить черновик |
| 177 | `DRAFT_DISCARD` | Удалить черновик |
| 178 | `REACT` | Реакция |
| 179 | `CANCEL_REACTION` | Отмена реакции |
| 180 | `GET_REACTIONS` | Получить реакции |
| 181 | `GET_DETAILED_REACTIONS` | Детальные реакции |
| 193 | `STICKER_CREATE` | Создать стикер |
| 196 | `CHAT_HIDE` | Скрыть чат |
| 198 | `GET_COMMON_CHATS` | Общие чаты |
| 201 | `GET_USER_SCORE` | Рейтинг |
| 257 | `CHAT_REACTIONS_SETTINGS_SET` | Настройки реакций |
| 258 | `REACTIONS_SETTINGS_GET_BY_CHAT_ID` | Получить настройки |
| 259 | `ASSETS_REMOVE` | Удалить ассет |
| 272 | `GET_FOLDERS` | Папки |
| 273 | `FOLDERS_GET_BY_ID` | Папка по ID |
| 274 | `FOLDERS_UPDATE` | Обновить папку |
| 275 | `FOLDERS_REORDER` | Порядок папок |
| 288 | `QR_AUTH_INIT` | QR-авторизация |
| 289 | `QR_AUTH_POLL` | Поллинг QR |
| 291 | `QR_AUTH_COMPLETE` | Завершение QR |

**Push-уведомления (от сервера):**

| Opcode | Константа | Описание |
|--------|-----------|----------|
| 128 | `PUSH_NEW_MESSAGE` | Новое сообщение |
| 129 | `PUSH_TYPING` | Печатает |
| 130 | `PUSH_MARK` | Прочитано |
| 131 | `PUSH_CONTACT` | Контакт |
| 132 | `PUSH_PRESENCE` | Присутствие |
| 134 | `PUSH_CONFIG` | Конфиг |
| 135 | `PUSH_CHAT` | Чат |
| 137 | `PUSH_INCOMING_CALL` | Входящий звонок |
| 140 | `PUSH_MSG_DELETE_RANGE` | Пакетное удаление |
| 147 | `PUSH_LOCATION` | Геолокация |
| 154 | `PUSH_MSG_DELAYED` | Отложенное |
| 155 | `PUSH_REACTIONS_CHANGED` | Реакции |
| 277 | `PUSH_FOLDERS` | Папки |

</details>

## Структура проекта

```
max-fuck/
├── max_api/
│   ├── __init__.py     # Экспорты
│   ├── client.py       # MaxClient — основной клиент
│   ├── calls.py        # MaxCall — WebRTC звонки
│   ├── auth.py         # Токены, QR-код в терминале
│   └── opcodes.py      # Все опкоды протокола (80+)
├── TEST/               # Тесты
│   ├── test_ultimate.py    # Полный тест всех методов
│   ├── test_all_sends.py   # Тест отправки всех типов
│   └── ...
├── APK/                # Декомпилированный APK + заметки
│   ├── fucking-max.md      # Полный отчёт по APK
│   └── max-decompiled/     # Исходники jadx
└── requirements.txt
```

## Ограничения

- Работает от имени обычного аккаунта, не бота
- Сервер может обновить протокол в любой момент (validation error + WS disconnect)
- Некоторые opcodes (`get_folder`, `get_message_stats`, `get_last_mentions`, `contact_add`) не работают на текущей версии сервера
- Звонки требуют `aiortc` (WebRTC)
- Сервер дропает WebSocket при validation error — будьте аккуратны с unknown opcodes

## Лицензия

MIT. Используй на свой страх и риск.
