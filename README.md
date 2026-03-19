# max-fuck

Неофициальный Python API для мессенджера **MAX** (ex VK Teams, ex ICQ).

Работает через реверс-инжиниринг WebSocket-протокола веб-версии (`web.max.ru`). Официального пользовательского API нет, бот-API требует юрлицо — эта библиотека решает проблему.

## Установка

```bash
git clone <repo-url>
cd max-fuck
pip install -r requirements.txt
```

Зависимости: `websockets`, `qrcode`

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

#### Сообщения

| Метод | Описание |
|-------|----------|
| `get_messages(chat_id, from_ts=0, forward=30, backward=30)` | Получить сообщения из чата |
| `send_message(chat_id, text, reply_to=None)` | Отправить текстовое сообщение |
| `mark_read(chat_id, message_id)` | Пометить сообщение как прочитанное |
| `send_typing(chat_id)` | Отправить индикатор "печатает..." |

#### Контакты и поиск

| Метод | Описание |
|-------|----------|
| `get_contacts(contact_ids)` | Информация о контактах по ID |
| `search(query, count=30, search_type="ALL")` | Поиск контактов и чатов |
| `get_reactions(chat_id, message_ids)` | Реакции на сообщения |

#### События (real-time)

```python
# Обработчик входящих сообщений
def on_msg(payload):
    msg = payload["message"]
    print(f'{msg["sender"]}: {msg["text"]}')

client.on_message(on_msg)  # новые сообщения
client.on_presence(handler) # статус онлайн/оффлайн
client.on(opcode, handler)  # любой серверный push по опкоду
```

Хендлеры могут быть синхронными или `async`.

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

### Мониторинг всех входящих

```python
async with MaxClient() as client:
    await client.auto_login()

    def handler(payload):
        chat = payload["chatId"]
        msg = payload["message"]
        print(f"[{chat}] {msg['sender']}: {msg.get('text', '<media>')}")

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
| 6 | `INIT` | Инициализация соединения |
| 19 | `LOGIN` | Логин по токену |
| 32 | `GET_CONTACTS` | Информация о контактах |
| 48 | `GET_CHATS` | Список чатов |
| 49 | `GET_MESSAGES` | Сообщения из чата |
| 50 | `MARK_READ` | Пометить прочитанным |
| 53 | `GET_CHATS_UPDATES` | Обновления чатов |
| 60 | `SEARCH` | Поиск |
| 64 | `SEND_MESSAGE` | Отправка сообщения |
| 65 | `TYPING` | Индикатор набора |
| 75 | `SUBSCRIBE_CHAT` | Подписка на события чата |
| 83 | `GET_VIDEO` | Получить URL видео |
| 115 | `PASSWORD_AUTH` | 2FA пароль |
| 128 | `PUSH_NEW_MESSAGE` | Пуш: новое сообщение |
| 132 | `PUSH_PRESENCE` | Пуш: статус пользователя |
| 158 | `TOKEN_REFRESH` | Обновление токена |
| 180 | `GET_REACTIONS` | Реакции на сообщения |
| 272 | `GET_FOLDERS` | Папки чатов |
| 288 | `QR_AUTH_INIT` | Начало QR-авторизации |
| 289 | `QR_AUTH_POLL` | Поллинг статуса QR |
| 291 | `QR_AUTH_COMPLETE` | Завершение QR-авторизации |

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
- Нет отправки файлов/картинок (пока)
- Нет голосовых/видео звонков
- MAX может изменить протокол в любой момент — это реверс-инжиниринг

## Лицензия

MIT. Используй на свой страх и риск.

## Disclaimer

Этот проект полностью навайбкоженный (vibe-coded). Автор не программист, но всё работает. Код написан с помощью AI, протестирован руками. Если что-то сломалось — issue / PR welcome.
