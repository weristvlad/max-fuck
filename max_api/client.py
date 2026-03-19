"""MAX messenger unofficial API client via WebSocket."""

import asyncio
import getpass
import json
import time
import uuid
from typing import Any, Callable, Optional

import websockets

from .auth import clear_token, load_token, print_qr_terminal, save_token
from .opcodes import Cmd, Opcode

WS_URL = "wss://ws-api.oneme.ru/websocket"
PROTOCOL_VERSION = 11
APP_VERSION = "26.3.7"


class MaxClient:
    """Async client for MAX messenger WebSocket API.

    Usage:
        async with MaxClient() as client:
            await client.login(token)
            chats = await client.get_chats()
            await client.send_message(chat_id, "Hello!")
    """

    def __init__(self):
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._seq = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._device_id = str(uuid.uuid4())
        self._token: Optional[str] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._handlers: dict[int, list[Callable]] = {}

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    # ── Connection ──────────────────────────────────────────────

    async def connect(self):
        self._ws = await websockets.connect(
            WS_URL,
            additional_headers={
                "Origin": "https://web.max.ru",
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/146.0.0.0 Safari/537.36"
                ),
            },
        )
        self._recv_task = asyncio.create_task(self._recv_loop())
        await self._send_init()

    async def disconnect(self):
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        if self._ws:
            await self._ws.close()

    async def _send_init(self):
        return await self._request(Opcode.INIT, {
            "userAgent": {
                "deviceType": "WEB",
                "locale": "en",
                "deviceLocale": "en",
                "osVersion": "macOS",
                "deviceName": "Chrome",
                "headerUserAgent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/146.0.0.0 Safari/537.36"
                ),
                "appVersion": APP_VERSION,
                "screen": "982x1512 2.0x",
                "timezone": "Asia/Barnaul",
            },
            "deviceId": self._device_id,
        })

    # ── Auth ────────────────────────────────────────────────────

    async def login(self, token: str) -> dict:
        """Login with an existing session token."""
        self._token = token
        return await self._request(Opcode.LOGIN, {"token": token})

    async def auto_login(
        self, password: str | None = None, show_qr: bool = True
    ) -> dict:
        """Smart login: uses saved token if available, QR code if not.

        First run:  shows QR in terminal → scan with phone → enter 2FA password
        Next runs:  instant login from saved token (auto-refreshes if needed)

        Args:
            password: 2FA password. If None, will prompt in terminal.
            show_qr: If True (default), renders QR code in terminal.
                If False, only prints the login link (useful for custom QR rendering).

        Returns:
            Login response with profile info.
        """
        # Try saved token first
        saved = load_token()
        if saved:
            try:
                result = await self.login(saved)
                print("Logged in with saved token.")
                # Refresh token in background for next time
                try:
                    refresh = await self.refresh_token()
                    if "token" in refresh:
                        save_token(refresh["token"])
                except Exception:
                    pass
                return result
            except MaxAPIError:
                print("Saved token expired. Starting QR login...")
                clear_token()

        # QR login flow
        def password_callback(email_hint, pw_hint):
            if password:
                return password
            print(f"\n2FA required. Email: {email_hint}")
            if pw_hint:
                print(f"Password hint: {pw_hint}")
            return getpass.getpass("Enter password: ")

        token = await self._login_qr(password_callback, show_qr=show_qr)
        save_token(token)
        print("Token saved. Next login will be automatic.")
        return await self._request(Opcode.PING, {"interactive": True})

    async def _login_qr(
        self,
        password_callback: Callable[[str, str], str],
        show_qr: bool = True,
    ) -> str:
        """Interactive QR code login flow. Returns session token.

        Args:
            password_callback: Called with (email_hint, password_hint) if 2FA needed.
            show_qr: If True, renders QR in terminal. If False, only prints the link.
        """
        # Step 1: Get QR code
        qr = await self._request(Opcode.QR_AUTH_INIT, {})
        track_id = qr["trackId"]
        qr_link = qr["qrLink"]

        print("\n┌─ MAX Login ─────────────────────────────┐")
        print("│ Scan this QR code in MAX app on phone:  │")
        print("│ Settings → Devices → Scan QR code       │")
        print("└─────────────────────────────────────────┘\n")
        if show_qr:
            print_qr_terminal(qr_link)
        print(f"\nLink: {qr_link}")
        print(f"Expires in {qr['ttl'] // 1000}s. Waiting...\n")

        # Step 2: Poll until scanned
        poll_interval = qr.get("pollingInterval", 5000) / 1000
        while True:
            status = await self._request(
                Opcode.QR_AUTH_POLL, {"trackId": track_id}
            )
            s = status.get("status", {})
            if s.get("loginAvailable"):
                print("QR scanned! Completing auth...")
                break
            await asyncio.sleep(poll_interval)

        # Step 3: Complete auth — may need password
        result = await self._request(
            Opcode.QR_AUTH_COMPLETE, {"trackId": track_id}
        )

        if "passwordChallenge" in result:
            challenge = result["passwordChallenge"]
            email = challenge.get("email", "")
            hint = challenge.get("hint", "")
            pw = password_callback(email, hint)

            result = await self._request(Opcode.PASSWORD_AUTH, {
                "trackId": track_id,
                "password": pw,
            })

        token = result["tokenAttrs"]["LOGIN"]["token"]
        await self.login(token)
        return token

    async def refresh_token(self) -> dict:
        """Refresh the session token. Also saves to disk."""
        result = await self._request(Opcode.TOKEN_REFRESH, {})
        if "token" in result:
            self._token = result["token"]
            save_token(result["token"])
        return result

    async def logout(self):
        """Disconnect and clear saved token."""
        clear_token()
        await self.disconnect()

    # ── Chats ───────────────────────────────────────────────────

    async def get_chats(self, chat_ids: list[int] | None = None) -> list[dict]:
        """Get chat list. Pass chat_ids=[0] for all chats."""
        if chat_ids is None:
            chat_ids = [0]
        result = await self._request(Opcode.GET_CHATS, {"chatIds": chat_ids})
        return result.get("chats", [])

    async def get_chats_updates(self, marker: int = 0) -> list[dict]:
        """Get chats updated since marker timestamp."""
        result = await self._request(
            Opcode.GET_CHATS_UPDATES, {"marker": marker}
        )
        return result.get("chats", [])

    async def get_folders(self) -> dict:
        """Get chat folders."""
        return await self._request(Opcode.GET_FOLDERS, {"folderSync": 0})

    # ── Messages ────────────────────────────────────────────────

    async def get_messages(
        self,
        chat_id: int,
        from_ts: int = 0,
        forward: int = 30,
        backward: int = 30,
    ) -> list[dict]:
        """Get messages from a chat.

        Args:
            chat_id: Chat ID.
            from_ts: Timestamp to fetch around. 0 for latest.
            forward: Number of messages after from_ts.
            backward: Number of messages before from_ts.
        """
        payload = {
            "chatId": chat_id,
            "forward": forward,
            "backward": backward,
            "getMessages": True,
        }
        if from_ts:
            payload["from"] = from_ts
        result = await self._request(Opcode.GET_MESSAGES, payload)
        return result.get("messages", [])

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to: str | None = None,
    ) -> dict:
        """Send a text message.

        Args:
            chat_id: Chat ID.
            text: Message text.
            reply_to: Message ID to reply to (optional).
        """
        cid = -int(time.time() * 1000)
        msg = {
            "text": text,
            "cid": cid,
            "elements": [],
            "attaches": [],
        }
        if reply_to:
            msg["replyToMessageId"] = reply_to

        return await self._request(Opcode.SEND_MESSAGE, {
            "chatId": chat_id,
            "message": msg,
            "notify": True,
        })

    async def mark_read(self, chat_id: int, message_id: str) -> dict:
        """Mark a message as read."""
        return await self._request(Opcode.MARK_READ, {
            "type": "READ_MESSAGE",
            "chatId": chat_id,
            "messageId": message_id,
            "mark": int(time.time() * 1000),
        })

    async def send_typing(self, chat_id: int):
        """Send typing indicator."""
        await self._request(Opcode.TYPING, {
            "chatId": chat_id,
            "type": "TEXT",
        })

    # ── Contacts ────────────────────────────────────────────────

    async def get_contacts(self, contact_ids: list[int]) -> list[dict]:
        """Get contact info by IDs."""
        result = await self._request(
            Opcode.GET_CONTACTS, {"contactIds": contact_ids}
        )
        return result.get("contacts", [])

    # ── Search ──────────────────────────────────────────────────

    async def search(
        self, query: str, count: int = 30, search_type: str = "ALL"
    ) -> list[dict]:
        """Search contacts and chats."""
        result = await self._request(Opcode.SEARCH, {
            "query": query,
            "count": count,
            "type": search_type,
        })
        return result.get("result", [])

    # ── Reactions ───────────────────────────────────────────────

    async def get_reactions(
        self, chat_id: int, message_ids: list[str]
    ) -> dict:
        """Get reactions for messages."""
        return await self._request(Opcode.GET_REACTIONS, {
            "chatId": chat_id,
            "messageIds": message_ids,
        })

    # ── Events ──────────────────────────────────────────────────

    def on_message(self, callback: Callable[[dict], Any]):
        """Register handler for incoming messages (opcode 128)."""
        self._handlers.setdefault(Opcode.PUSH_NEW_MESSAGE, []).append(callback)

    def on_presence(self, callback: Callable[[dict], Any]):
        """Register handler for presence updates (opcode 132)."""
        self._handlers.setdefault(Opcode.PUSH_PRESENCE, []).append(callback)

    def on(self, opcode: int, callback: Callable[[dict], Any]):
        """Register handler for any server push opcode."""
        self._handlers.setdefault(opcode, []).append(callback)

    # ── Protocol internals ──────────────────────────────────────

    async def _request(self, opcode: int, payload: dict) -> dict:
        """Send request and wait for response."""
        seq = self._seq
        self._seq += 1

        msg = {
            "ver": PROTOCOL_VERSION,
            "cmd": Cmd.REQUEST,
            "seq": seq,
            "opcode": opcode,
            "payload": payload,
        }

        future = asyncio.get_event_loop().create_future()
        self._pending[seq] = future

        await self._ws.send(json.dumps(msg))

        try:
            result = await asyncio.wait_for(future, timeout=30)
        except asyncio.TimeoutError:
            self._pending.pop(seq, None)
            raise TimeoutError(f"Request timeout: opcode={opcode} seq={seq}")

        if result.get("_error"):
            raise MaxAPIError(result)

        return result

    async def _recv_loop(self):
        """Background task: receive and dispatch WebSocket messages."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                cmd = msg.get("cmd")
                seq = msg.get("seq")
                opcode = msg.get("opcode")
                payload = msg.get("payload") or {}

                # Response to our request
                if cmd == Cmd.RESPONSE and seq in self._pending:
                    self._pending.pop(seq).set_result(payload)
                elif cmd == Cmd.ERROR and seq in self._pending:
                    payload["_error"] = True
                    self._pending.pop(seq).set_result(payload)

                # Server push — dispatch to handlers & auto-ack
                elif cmd == Cmd.REQUEST:
                    # Server pushes come as cmd=0 from server
                    await self._handle_push(opcode, seq, payload)

        except websockets.ConnectionClosed:
            # Cancel all pending requests
            for fut in self._pending.values():
                if not fut.done():
                    fut.set_exception(ConnectionError("WebSocket closed"))
            self._pending.clear()

    async def _handle_push(self, opcode: int, seq: int, payload: dict):
        """Handle server-initiated push messages."""
        # Auto-ack for new messages
        if opcode == Opcode.PUSH_NEW_MESSAGE:
            chat_id = payload.get("chatId")
            msg_id = payload.get("message", {}).get("id")
            if chat_id and msg_id:
                ack = {
                    "ver": PROTOCOL_VERSION,
                    "cmd": Cmd.RESPONSE,
                    "seq": seq,
                    "opcode": opcode,
                    "payload": {"chatId": chat_id, "messageId": msg_id},
                }
                await self._ws.send(json.dumps(ack))

        # Dispatch to registered handlers
        for handler in self._handlers.get(opcode, []):
            try:
                result = handler(payload)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                print(f"Handler error for opcode {opcode}: {e}")


class MaxAPIError(Exception):
    """Error from MAX API."""

    def __init__(self, payload: dict):
        self.error = payload.get("error", "unknown")
        self.message = payload.get("message", "")
        self.localized = payload.get("localizedMessage", "")
        super().__init__(f"[{self.error}] {self.localized or self.message}")
