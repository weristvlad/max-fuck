# MAX SMS Auth — Полный рабочий флоу

**Статус: ПРОВЕРЕНО, РАБОТАЕТ** (2026-03-27)

## Транспорт

**НЕ WebSocket, НЕ HTTP** — бинарный TCP к `api.oneme.ru:443` (обычный TLS 1.3).

WebSocket (`ws-api.oneme.ru`) имеет `phone-auth-enabled: false` — SMS запрещено для WEB клиентов.

## Флоу авторизации

```
┌─────────────┐          ┌─────────────────┐
│   Клиент    │          │ api.oneme.ru:443│
│ (TCP+TLS)   │          │  (binary proto)  │
└──────┬──────┘          └────────┬────────┘
       │                          │
       │ SESSION_INIT (op=6)      │
       │ deviceType="ANDROID"     │
       ├─────────────────────────►│
       │◄─────────────────────────┤ OK (location, phone-auth-enabled)
       │                          │
       │ AUTH_REQUEST (op=17)     │
       │ phone="+7...", type=     │
       │ "START_AUTH"             │
       ├─────────────────────────►│
       │◄─────────────────────────┤ OK (verifyToken, codeLength=6)
       │                          │       │
       │                          │       ▼ SMS отправлено
       │                          │
       │ AUTH_CONFIRM (op=18)     │
       │ token=verifyToken,       │
       │ verifyCode="123456",     │
       │ authTokenType=           │
       │ "CHECK_CODE"             │
       ├─────────────────────────►│
       │◄─────────────────────────┤ OK (auth_token, tokenAttrs.LOGIN)
       │                          │  ИЛИ: tokenAttrs.REGISTER (нет акка)
       │                          │
       │ LOGIN (op=19)            │
       │ token=auth_token         │
       ├─────────────────────────►│
       │◄─────────────────────────┤ OK (profile: {name, id, phone})
       │                          │
       ▼ Сессия активна           │
         Токен сохранён           │
```

## Критические детали

1. **deviceType MUST be "ANDROID"** — "WEB" блокирует SMS auth, любой другой тип тоже
2. **type: "START_AUTH"** — единственное работающее значение (из enum `cc0.java`)
3. **Одна TCP-сессия** — verifyToken привязан к TCP-соединению, нельзя подтвердить код в другом соединении
4. **Формат пакета** — 10 байт header + MessagePack payload (см. max-binary-tcp-protocol.md)
5. **Auth token** — длинная base64url строка (~600 символов), находится в теле ответа AUTH_CONFIRM

## Рабочий скрипт

`max-auth-step1.py` в корне проекта — интерактивный скрипт для SMS-авторизации.

## Токен

Сохраняется в `.max_auth_token.json`:
```json
{
  "token": "An_Sx6HQ9HDi_YF3NHy0qk...",
  "phone": "+79956992801"
}
```

Этот токен используется для:
1. LOGIN (opcode 19) — авторизация в сессии
2. `ResolveConfigMAX()` в Go — получение TURN credentials через звонок
3. Передаётся в iOS приложение через App Group UserDefaults

## Следующие шаги

1. Перенести бинарный TCP протокол в Swift (MaxAuthService.swift) для iOS приложения
2. Или: перенести в Go library (golib/vpnlib/) и вызывать через gomobile
3. Реализовать регистрацию нового аккаунта (tokenAttrs.REGISTER flow)
