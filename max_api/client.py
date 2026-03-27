"""MAX messenger unofficial API client via WebSocket."""

import asyncio
import getpass
import json
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Optional

import websockets

from .auth import clear_token, load_token, print_qr_terminal, save_token
from .opcodes import Cmd, Opcode

WS_URL = "wss://ws-api.oneme.ru/websocket"
PROTOCOL_VERSION = 11
APP_VERSION = "26.11.0"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/146.0.0.0 Safari/537.36"
)


class MaxClient:
    """Async client for MAX messenger WebSocket API.

    Usage:
        async with MaxClient() as client:
            await client.login(token)
            chats = await client.get_chats()
            await client.send_message(chat_id, "Hello!")
    """

    def __init__(self, token_refresh_interval: int = 600):
        """
        Args:
            token_refresh_interval: Seconds between automatic token refreshes. Default 600 (10 min).
        """
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._seq = 0
        self._pending: dict[int, asyncio.Future] = {}
        self._device_id: Optional[str] = None  # Set in auto_login or connect
        self._token: Optional[str] = None
        self._recv_task: Optional[asyncio.Task] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._refresh_interval = token_refresh_interval
        self._handlers: dict[int, list[Callable]] = {}

    async def __aenter__(self):
        await self.connect()
        return self

    async def __aexit__(self, *args):
        await self.disconnect()

    # ── Connection ──────────────────────────────────────────────

    async def connect(self):
        if self._device_id is None:
            # Try to load persisted device_id before generating a new one
            _, saved_device_id = load_token()
            self._device_id = saved_device_id or str(uuid.uuid4())
        self._ws = await websockets.connect(
            WS_URL,
            additional_headers={
                "Origin": "https://web.max.ru",
                "User-Agent": USER_AGENT,
            },
        )
        self._recv_task = asyncio.create_task(self._recv_loop())
        await self._send_init()

    async def disconnect(self):
        if self._refresh_task:
            self._refresh_task.cancel()
            try:
                await self._refresh_task
            except asyncio.CancelledError:
                pass
        # Refresh token one last time before closing so it stays fresh for next run
        if self._ws and self._token:
            try:
                await self.refresh_token()
            except Exception:
                pass
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
                "headerUserAgent": USER_AGENT,
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
        return await self._request(Opcode.LOGIN, {
            "token": token,
            "chatsCount": 40,
            "interactive": True,
            "chatsSync": 0,
            "contactsSync": 0,
            "presenceSync": -1,
            "draftsSync": 0,
        })

    async def auto_login(
        self,
        password: str | None = None,
        show_qr: bool = True,
        phone: str | None = None,
    ) -> dict:
        """Smart login: uses saved token if available, then SMS or QR.

        First run:  SMS (if phone given) or QR code → 2FA password if needed
        Next runs:  instant login from saved token (auto-refreshes if needed)

        Args:
            password: 2FA password. If None, will prompt in terminal.
            show_qr: If True (default), renders QR code in terminal.
            phone: Phone number for SMS login (e.g. "+79001234567").
                If provided, uses SMS flow instead of QR when no saved token.

        Returns:
            Login response with profile info.
        """
        # Try saved token first (device_id already loaded in connect())
        saved, _ = load_token()
        if saved:
            try:
                result = await self.login(saved)
                print("Logged in with saved token.")
                try:
                    await self.refresh_token()
                except Exception:
                    pass
                self._start_token_refresh_loop()
                return result
            except MaxAPIError:
                print("Saved token expired.")
                clear_token()

        def password_callback(email_hint, pw_hint):
            if password:
                return password
            print(f"\n2FA required. Email: {email_hint}")
            if pw_hint:
                print(f"Password hint: {pw_hint}")
            return getpass.getpass("Enter password: ")

        if phone:
            print(f"Starting SMS login for {phone}...")
            token = await self._login_sms(phone, password_callback)
        else:
            print("Starting QR login...")
            token = await self._login_qr(password_callback, show_qr=show_qr)

        save_token(token, login_token=token, device_id=self._device_id)
        print("Token saved. Next login will be automatic.")
        self._start_token_refresh_loop()
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

    async def _login_sms(
        self,
        phone: str,
        password_callback: Callable[[str, str], str],
    ) -> str:
        """SMS login via binary TCP to api.oneme.ru:443.

        SMS auth is NOT available via WebSocket (phone-auth-enabled=false for WEB).
        Requires binary TCP protocol with deviceType=ANDROID.
        See docs/max-sms-auth-flow.md for protocol details.

        Args:
            phone: Phone number (e.g. "+79001234567").
            password_callback: Called with (email_hint, password_hint) if 2FA needed.
        """
        import ssl
        import struct

        host = "api.oneme.ru"
        port = 443

        ssl_ctx = ssl.create_default_context()
        reader, writer = await asyncio.open_connection(host, port, ssl=ssl_ctx)

        tcp_seq = 0

        async def tcp_send(opcode: int, payload: dict) -> dict:
            nonlocal tcp_seq
            data = json.dumps({
                "ver": PROTOCOL_VERSION,
                "cmd": Cmd.REQUEST,
                "seq": tcp_seq,
                "opcode": opcode,
                "payload": payload,
            }).encode()
            # Binary frame: 4-byte big-endian length + data
            writer.write(struct.pack(">I", len(data)) + data)
            await writer.drain()

            # Read response
            header = await reader.readexactly(4)
            resp_len = struct.unpack(">I", header)[0]
            resp_data = await reader.readexactly(resp_len)
            resp = json.loads(resp_data)
            tcp_seq += 1

            payload = resp.get("payload", {})
            if resp.get("cmd") == Cmd.ERROR:
                raise MaxAPIError(payload)
            return payload

        try:
            # Step 1: INIT with ANDROID deviceType (required for SMS)
            await tcp_send(Opcode.INIT, {
                "userAgent": {
                    "deviceType": "ANDROID",
                    "locale": "ru",
                    "appVersion": APP_VERSION,
                },
                "deviceId": self._device_id,
            })

            # Step 2: Request SMS code
            auth_req = await tcp_send(Opcode.AUTH_REQUEST, {
                "phone": phone,
                "type": "START_AUTH",
            })
            verify_token = auth_req["verifyToken"]
            code_length = auth_req.get("codeLength", 6)
            print(f"SMS code sent to {phone} ({code_length} digits)")

            # Step 3: Get code from user
            code = input(f"Enter {code_length}-digit SMS code: ").strip()

            # Step 4: Verify SMS code
            result = await tcp_send(Opcode.AUTH, {
                "token": verify_token,
                "verifyCode": code,
                "authTokenType": "CHECK_CODE",
            })

            # Step 5: Handle 2FA if needed
            if "passwordChallenge" in result:
                challenge = result["passwordChallenge"]
                email = challenge.get("email", "")
                hint = challenge.get("hint", "")
                pw = password_callback(email, hint)

                result = await tcp_send(Opcode.PASSWORD_AUTH, {
                    "trackId": result.get("trackId", ""),
                    "password": pw,
                })

            # Step 6: Extract token
            token = result["tokenAttrs"]["LOGIN"]["token"]

        finally:
            writer.close()
            await writer.wait_closed()

        # Step 7: Login via WebSocket with the obtained token
        await self.login(token)
        return token

    async def login_sms(self, phone: str, password: str | None = None) -> dict:
        """Login via SMS verification code.

        Uses binary TCP protocol to api.oneme.ru:443 (SMS auth is blocked
        on WebSocket for WEB clients). See docs/max-sms-auth-flow.md.

        Args:
            phone: Phone number (e.g. "+79001234567").
            password: 2FA password. If None, will prompt in terminal.

        Returns:
            Login response.
        """
        def password_callback(email_hint, pw_hint):
            if password:
                return password
            print(f"\n2FA required. Email: {email_hint}")
            if pw_hint:
                print(f"Password hint: {pw_hint}")
            return getpass.getpass("Enter password: ")

        token = await self._login_sms(phone, password_callback)
        save_token(token, login_token=token, device_id=self._device_id)
        print("Token saved. Next login will be automatic.")
        self._start_token_refresh_loop()
        return await self._request(Opcode.PING, {"interactive": True})

    async def refresh_token(self) -> dict:
        """Refresh the session token. Also saves to disk with TTL info."""
        result = await self._request(Opcode.TOKEN_REFRESH, {})
        if "token" in result:
            self._token = result["token"]
            save_token(
                result["token"],
                lifetime_ts=result.get("token_lifetime_ts"),
                refresh_ts=result.get("token_refresh_ts"),
            )
        return result

    def _start_token_refresh_loop(self):
        """Start background task that refreshes the token periodically."""
        if self._refresh_task:
            self._refresh_task.cancel()
        self._refresh_task = asyncio.create_task(self._token_refresh_loop())

    async def _token_refresh_loop(self):
        """Background loop: refresh token every N seconds to keep session alive."""
        try:
            while True:
                await asyncio.sleep(self._refresh_interval)
                try:
                    await self.refresh_token()
                except Exception:
                    pass
        except asyncio.CancelledError:
            pass

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

    async def create_chat(
        self,
        title: str,
        member_ids: list[int],
        chat_type: str = "GROUP",
    ) -> dict:
        """Create a new group chat or channel.

        Args:
            title: Chat title.
            member_ids: List of user IDs to add.
            chat_type: "GROUP" or "CHANNEL".
        """
        return await self._request(Opcode.CREATE_CHAT, {
            "title": title,
            "memberIds": member_ids,
            "type": chat_type,
        })

    async def update_chat(
        self,
        chat_id: int,
        title: str | None = None,
        about: str | None = None,
    ) -> dict:
        """Update chat settings (title, description).

        Args:
            chat_id: Chat ID.
            title: New title (optional).
            about: New description (optional).
        """
        payload: dict[str, Any] = {"chatId": chat_id}
        if title is not None:
            payload["title"] = title
        if about is not None:
            payload["about"] = about
        return await self._request(Opcode.UPDATE_CHAT, payload)

    async def delete_chat(self, chat_id: int) -> dict:
        """Delete a chat."""
        return await self._request(Opcode.DELETE_CHAT, {"chatId": chat_id})

    async def join_chat(self, chat_link: str) -> dict:
        """Join a chat by invite link or public link.

        Args:
            chat_link: Invite link or public chat link.
        """
        return await self._request(Opcode.JOIN_CHAT, {"link": chat_link})

    async def leave_chat(self, chat_id: int) -> dict:
        """Leave a chat."""
        return await self._request(Opcode.LEAVE_CHAT, {"chatId": chat_id})

    async def get_chat_members_list(self, chat_id: int) -> dict:
        """Get list of chat members with roles.

        Args:
            chat_id: Chat ID.

        Returns:
            dict with member list and their roles.
        """
        return await self._request(Opcode.GET_CHAT_MEMBERS, {"chatId": chat_id})

    async def update_chat_members(
        self,
        chat_id: int,
        add: list[int] | None = None,
        remove: list[int] | None = None,
    ) -> dict:
        """Add or remove members from a chat.

        Args:
            chat_id: Chat ID.
            add: List of user IDs to add.
            remove: List of user IDs to remove.
        """
        payload: dict[str, Any] = {"chatId": chat_id}
        if add:
            payload["addMemberIds"] = add
        if remove:
            payload["removeMemberIds"] = remove
        return await self._request(Opcode.UPDATE_CHAT_MEMBERS, payload)

    async def clear_chat(self, chat_id: int) -> dict:
        """Clear all messages in a chat."""
        return await self._request(Opcode.CLEAR_CHAT, {"chatId": chat_id})

    async def hide_chat(self, chat_id: int) -> dict:
        """Hide a chat from the chat list."""
        return await self._request(Opcode.CHAT_HIDE, {"chatId": chat_id})

    async def check_chat_link(self, link: str) -> dict:
        """Check an invite link without joining. Returns chat info."""
        return await self._request(Opcode.CHECK_CHAT_LINK, {"link": link})

    async def get_common_chats(self, user_ids: list[int] | int, count: int = 50) -> dict:
        """Get common chats with other user(s)."""
        if isinstance(user_ids, int):
            user_ids = [user_ids]
        return await self._request(Opcode.GET_COMMON_CHATS, {
            "userIds": user_ids,
            "count": count,
        })

    async def get_folder(self, folder_id: str) -> dict:
        """Get a chat folder by ID (e.g. "all.chat.folder" or custom ID)."""
        return await self._request(Opcode.FOLDERS_GET_BY_ID, {"folderId": folder_id})

    async def update_folder(
        self, folder_id: str, title: str | None = None, chat_ids: list[int] | None = None
    ) -> dict:
        """Update a chat folder."""
        payload: dict[str, Any] = {"folderId": folder_id}
        if title is not None:
            payload["title"] = title
        if chat_ids is not None:
            payload["chatIds"] = chat_ids
        return await self._request(Opcode.FOLDERS_UPDATE, payload)

    async def reorder_folders(self, folder_ids: list[str]) -> dict:
        """Reorder chat folders."""
        return await self._request(Opcode.FOLDERS_REORDER, {"folderIds": folder_ids})

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

    async def get_media_messages(
        self,
        chat_id: int,
        message_id: str,
        attach_types: list[str] | None = None,
        forward: int = 25,
        backward: int = 25,
    ) -> list[dict]:
        """Get media messages around a given message, filtered by type.

        Args:
            chat_id: Chat ID.
            message_id: Message ID to fetch around.
            attach_types: Filter by attach types, e.g. ["PHOTO", "VIDEO"]. Default: ["PHOTO", "VIDEO"].
            forward: Number of messages after.
            backward: Number of messages before.
        """
        if attach_types is None:
            attach_types = ["PHOTO", "VIDEO"]
        result = await self._request(Opcode.GET_MEDIA_MESSAGES, {
            "chatId": chat_id,
            "messageId": message_id,
            "attachTypes": attach_types,
            "forward": forward,
            "backward": backward,
        })
        return result.get("messages", [])

    async def send_message(
        self,
        chat_id: int,
        text: str,
        reply_to: str | None = None,
        elements: list[dict] | None = None,
        send_time: int | None = None,
    ) -> dict:
        """Send a text message with optional formatting.

        Args:
            chat_id: Chat ID.
            text: Message text (plain text, or use parse_formatted_text() for markup).
            reply_to: Message ID to reply to (optional).
            elements: Formatting elements list. Each element is a dict with:
                - type: "STRONG" (bold), "EMPHASIZED" (italic), "LINK", "CODE",
                  "STRIKETHROUGH", "UNDERLINE", "MONOSPACED"
                - from: character offset in text
                - length: number of characters
                - attributes: dict, e.g. {"url": "..."} for LINK type
                You can use parse_formatted_text() to build text+elements from markup.
            send_time: Unix timestamp in ms for scheduled sending.
                If set, the message will be delivered at that time (delayed/scheduled post).
        """
        cid = -int(time.time() * 1000)
        msg: dict[str, Any] = {
            "text": text,
            "cid": cid,
            "elements": elements or [],
            "attaches": [],
        }
        if reply_to:
            msg["replyToMessageId"] = reply_to
        if send_time:
            msg["sendTime"] = send_time

        return await self._request(Opcode.SEND_MESSAGE, {
            "chatId": chat_id,
            "message": msg,
            "notify": True,
        })

    async def edit_message(
        self,
        chat_id: int,
        message_id: str,
        text: str,
        elements: list[dict] | None = None,
    ) -> dict:
        """Edit an existing message.

        Args:
            chat_id: Chat ID.
            message_id: ID of message to edit.
            text: New message text.
            elements: New formatting elements (optional).
        """
        return await self._request(Opcode.EDIT_MESSAGE, {
            "chatId": chat_id,
            "messageId": message_id,
            "text": text,
            "elements": elements or [],
        })

    async def delete_message(
        self,
        chat_id: int,
        message_id: str | int,
        for_all: bool = True,
    ) -> dict:
        """Delete a message.

        Args:
            chat_id: Chat ID.
            message_id: ID of message to delete.
            for_all: If True, delete for everyone. If False, only for yourself.
        """
        return await self._request(Opcode.DELETE_MESSAGE, {
            "chatId": chat_id,
            "messageIds": [int(message_id)],
            "forAll": for_all,
        })

    async def forward_messages(
        self,
        chat_id: int,
        from_chat_id: int,
        message_ids: list[str | int],
    ) -> dict:
        """Forward messages to another chat.

        Forwards one message at a time using the link mechanism.
        For multiple messages, sends multiple requests.

        Args:
            chat_id: Destination chat ID.
            from_chat_id: Source chat ID.
            message_ids: List of message IDs to forward.
        """
        result = None
        for mid in message_ids:
            cid = -int(time.time() * 1000)
            result = await self._request(Opcode.SEND_MESSAGE, {
                "chatId": chat_id,
                "message": {
                    "cid": cid,
                    "link": {
                        "type": "FORWARD",
                        "chatId": from_chat_id,
                        "messageId": int(mid),
                    },
                },
                "notify": True,
            })
        return result

    async def pin_message(self, chat_id: int) -> dict:
        """Show the pinned message panel in a chat."""
        return await self._request(Opcode.CHAT_PIN_SET_VISIBILITY, {
            "chatId": chat_id,
            "show": True,
        })

    async def unpin_message(self, chat_id: int) -> dict:
        """Hide the pinned message panel in a chat."""
        return await self._request(Opcode.CHAT_PIN_SET_VISIBILITY, {
            "chatId": chat_id,
            "show": False,
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

    async def get_message(self, chat_id: int, message_ids: list[int] | int) -> dict:
        """Get messages by IDs.

        Args:
            chat_id: Chat ID.
            message_ids: Single message ID (int) or list of message IDs.
        """
        if isinstance(message_ids, int):
            message_ids = [message_ids]
        return await self._request(Opcode.GET_MESSAGE, {
            "chatId": chat_id,
            "messageIds": message_ids,
        })

    async def search_messages(self, chat_id: int, query: str, count: int = 30) -> dict:
        """Search messages within a specific chat."""
        return await self._request(Opcode.SEARCH_MESSAGES, {
            "chatId": chat_id,
            "query": query,
            "count": count,
        })

    async def search_chats(self, query: str, count: int = 30) -> dict:
        """Search chats by name."""
        return await self._request(Opcode.SEARCH_CHATS, {
            "query": query,
            "count": count,
        })

    async def delete_message_range(
        self, chat_id: int, from_message_id: str, to_message_id: str
    ) -> dict:
        """Delete a range of messages in a chat (payload format guessed from APK)."""
        return await self._request(Opcode.MSG_DELETE_RANGE, {
            "chatId": chat_id,
            "fromMessageId": from_message_id,
            "toMessageId": to_message_id,
        })

    async def get_message_link(self, chat_id: int, message_id: str) -> dict:
        """Get a shareable link for a message."""
        return await self._request(Opcode.GET_MESSAGE_LINK, {
            "chatId": chat_id,
            "messageId": message_id,
        })

    async def get_link_info(self, url: str) -> dict:
        """Get Open Graph / link preview data for a URL."""
        return await self._request(Opcode.GET_LINK_INFO, {"link": url})

    async def get_last_mentions(self) -> dict:
        """Get recent @mentions of the current user."""
        return await self._request(Opcode.GET_LAST_MENTIONS, {})

    # ── File / Photo / Video uploads ─────────────────────────────

    async def _get_image_upload_url(self) -> str:
        """Get a signed URL for uploading an image to iu.oneme.ru."""
        result = await self._request(Opcode.GET_IMAGE_UPLOAD_URL, {"count": 1})
        return result["url"]

    async def _get_file_upload_url(self) -> dict:
        """Get a signed URL + fileId for uploading a file to fu.oneme.ru.

        Returns:
            dict with keys: url, fileId, token
        """
        result = await self._request(Opcode.GET_FILE_UPLOAD_URL, {"count": 1})
        return result["info"][0]

    async def _upload_image(self, file_path: str | Path) -> str:
        """Upload an image and return its photoToken.

        Args:
            file_path: Path to image file (png, jpg, webp, etc.)

        Returns:
            photoToken string to use in send_photo().
        """
        import aiohttp

        file_path = Path(file_path)
        upload_url = await self._get_image_upload_url()

        async with aiohttp.ClientSession() as session:
            data = aiohttp.FormData()
            data.add_field(
                "file",
                file_path.read_bytes(),
                filename=file_path.name,
                content_type=_guess_mime(file_path),
            )
            async with session.post(
                upload_url,
                data=data,
                headers={"Origin": "https://web.max.ru"},
            ) as resp:
                resp.raise_for_status()
                result = await resp.json()

        # Response: {"photos": {"<photoId>": {"token": "..."}}}
        photos = result["photos"]
        token = next(iter(photos.values()))["token"]
        return token

    async def _upload_file(self, file_path: str | Path) -> dict:
        """Upload a file and return its fileId.

        Args:
            file_path: Path to any file.

        Returns:
            dict with fileId and token.
        """
        import aiohttp
        from urllib.parse import quote

        file_path = Path(file_path)
        info = await self._get_file_upload_url()
        file_data = file_path.read_bytes()
        encoded_name = quote(file_path.name)

        async with aiohttp.ClientSession() as session:
            async with session.post(
                info["url"],
                data=file_data,
                headers={
                    "Origin": "https://web.max.ru",
                    "Content-Type": _guess_mime(file_path),
                    "Content-Disposition": f"attachment; filename={encoded_name}",
                    "Content-Range": f"0-{len(file_data) - 1}/{len(file_data)}",
                },
            ) as resp:
                resp.raise_for_status()

        return {"fileId": info["fileId"], "token": info["token"]}

    async def send_photo(
        self,
        chat_id: int,
        file_path: str | Path,
        text: str | None = None,
        reply_to: str | None = None,
    ) -> dict:
        """Upload and send a photo.

        Args:
            chat_id: Chat ID.
            file_path: Path to image file.
            text: Optional caption text.
            reply_to: Message ID to reply to (optional).
        """
        token = await self._upload_image(file_path)
        cid = -int(time.time() * 1000)
        msg: dict[str, Any] = {
            "cid": cid,
            "attaches": [{"_type": "PHOTO", "photoToken": token}],
        }
        if text:
            msg["text"] = text
            msg["elements"] = []
        if reply_to:
            msg["replyToMessageId"] = reply_to

        return await self._request(Opcode.SEND_MESSAGE, {
            "chatId": chat_id,
            "message": msg,
            "notify": True,
        })

    async def send_file(
        self,
        chat_id: int,
        file_path: str | Path,
        text: str | None = None,
        reply_to: str | None = None,
    ) -> dict:
        """Upload and send a file.

        Args:
            chat_id: Chat ID.
            file_path: Path to file.
            text: Optional caption text.
            reply_to: Message ID to reply to (optional).
        """
        info = await self._upload_file(file_path)
        await asyncio.sleep(1)  # Wait for server to process the upload
        cid = -int(time.time() * 1000)
        msg: dict[str, Any] = {
            "cid": cid,
            "attaches": [{"_type": "FILE", "fileId": info["fileId"]}],
        }
        if text:
            msg["text"] = text
            msg["elements"] = []
        if reply_to:
            msg["replyToMessageId"] = reply_to

        return await self._request(Opcode.SEND_MESSAGE, {
            "chatId": chat_id,
            "message": msg,
            "notify": True,
        })

    async def send_voice(
        self,
        chat_id: int,
        file_path: str | Path,
        duration_ms: int | None = None,
        reply_to: str | None = None,
    ) -> dict:
        """Upload and send a voice message.

        The file should ideally be in OGG Opus or MP3 format.
        Uses the file upload endpoint (fu.oneme.ru).

        Args:
            chat_id: Chat ID.
            file_path: Path to audio file.
            duration_ms: Duration in milliseconds. If None, you should provide it.
            reply_to: Message ID to reply to (optional).
        """
        info = await self._upload_file(file_path)
        await asyncio.sleep(1)  # Wait for server to process the upload
        cid = -int(time.time() * 1000)
        attach: dict[str, Any] = {
            "_type": "FILE",
            "fileId": info["fileId"],
        }
        if duration_ms is not None:
            attach["duration"] = duration_ms
        msg: dict[str, Any] = {
            "cid": cid,
            "attaches": [attach],
        }
        if reply_to:
            msg["replyToMessageId"] = reply_to

        return await self._request(Opcode.SEND_MESSAGE, {
            "chatId": chat_id,
            "message": msg,
            "notify": True,
        })

    async def send_video(
        self,
        chat_id: int,
        file_path: str | Path,
        text: str | None = None,
        reply_to: str | None = None,
    ) -> dict:
        """Upload and send a video.

        Uses the file upload endpoint (fu.oneme.ru).

        Args:
            chat_id: Chat ID.
            file_path: Path to video file (mp4).
            text: Optional caption text.
            reply_to: Message ID to reply to (optional).
        """
        info = await self._upload_file(file_path)
        await asyncio.sleep(1)  # Wait for server to process the upload
        cid = -int(time.time() * 1000)
        msg: dict[str, Any] = {
            "cid": cid,
            "attaches": [{"_type": "FILE", "fileId": info["fileId"]}],
        }
        if text:
            msg["text"] = text
            msg["elements"] = []
        if reply_to:
            msg["replyToMessageId"] = reply_to

        return await self._request(Opcode.SEND_MESSAGE, {
            "chatId": chat_id,
            "message": msg,
            "notify": True,
        })

    async def send_video_message(
        self,
        chat_id: int,
        file_path: str | Path,
        reply_to: str | None = None,
    ) -> dict:
        """Upload and send a video message (kruzhok / circle video).

        The video should ideally be 480x480 square, short duration.
        Uses the file upload endpoint (fu.oneme.ru).

        Args:
            chat_id: Chat ID.
            file_path: Path to video file (mp4, square 480x480).
            reply_to: Message ID to reply to (optional).
        """
        info = await self._upload_file(file_path)
        await asyncio.sleep(1)  # Wait for server to process the upload
        cid = -int(time.time() * 1000)
        msg: dict[str, Any] = {
            "cid": cid,
            "attaches": [{
                "_type": "FILE",
                "fileId": info["fileId"],
                "videoType": 1,
            }],
        }
        if reply_to:
            msg["replyToMessageId"] = reply_to

        return await self._request(Opcode.SEND_MESSAGE, {
            "chatId": chat_id,
            "message": msg,
            "notify": True,
        })

    # ── Stickers ─────────────────────────────────────────────────

    async def get_sticker_sets(
        self, section_id: str = "NEW_STICKER_SETS", offset: int = 0, count: int = 100
    ) -> dict:
        """Get list of sticker set IDs.

        Args:
            section_id: Section to fetch. Default "NEW_STICKER_SETS".
            offset: Pagination offset.
            count: Number of sets to return.
        """
        return await self._request(Opcode.GET_STICKER_SETS, {
            "sectionId": section_id,
            "from": offset,
            "count": count,
        })

    async def sync_stickers(self, sticker_type: str = "STICKER", sync: int = 0) -> dict:
        """Sync sticker data.

        Args:
            sticker_type: "STICKER", "FAVORITE_STICKER", or "ANIMOJI".
            sync: Sync marker (0 for full sync).
        """
        return await self._request(Opcode.STICKER_SYNC, {
            "type": sticker_type,
            "sync": sync,
        })

    # ── Contacts & Users ──────────────────────────────────────

    async def get_contacts(self, contact_ids: list[int]) -> list[dict]:
        """Get contact info by IDs.

        Returns list of contacts with fields like:
            id, names, phone, link, options, avatar, etc.
        """
        result = await self._request(
            Opcode.GET_CONTACTS, {"contactIds": contact_ids}
        )
        return result.get("contacts", [])

    async def get_user(self, user_id: int) -> dict | None:
        """Get info about a single user by ID.

        Args:
            user_id: MAX user ID (external ID).

        Returns:
            User dict or None if not found.
        """
        contacts = await self.get_contacts([user_id])
        return contacts[0] if contacts else None

    async def get_chat_members(self, chat_id: int) -> list[dict]:
        """Get info about all participants in a chat.

        Args:
            chat_id: Chat ID.

        Returns:
            List of user info dicts for all chat participants.
        """
        chats = await self.get_chats(chat_ids=[chat_id])
        if not chats:
            return []
        participants = chats[0].get("participants", {})
        user_ids = [int(uid) for uid in participants.keys()]
        if not user_ids:
            return []
        return await self.get_contacts(user_ids)

    async def find_user(self, query: str, count: int = 30) -> dict:
        """Search for users by name, nickname, or phone.

        Uses contact_search (opcode 37) to search within contacts.

        Args:
            query: Search string (name, @nickname, phone number).
            count: Max results.
        """
        return await self.contact_search(query, count=count)

    async def contact_add(self, user_id: int) -> dict:
        """Add a user to contacts."""
        return await self._request(Opcode.CONTACT_ADD, {"contactId": user_id})

    async def contact_search(self, query: str, count: int = 30) -> dict:
        """Search within your own contact list."""
        return await self._request(Opcode.CONTACT_SEARCH, {"query": query, "count": count})

    async def contact_by_phone(self, phone: str) -> dict:
        """Find a contact by phone number."""
        return await self._request(Opcode.CONTACT_INFO_BY_PHONE, {"phone": phone})

    async def mutual_contacts(self, user_id: int) -> dict:
        """Get mutual contacts with another user."""
        return await self._request(Opcode.CONTACT_MUTUAL, {"userId": user_id})

    # ── Search ──────────────────────────────────────────────────

    async def search(
        self, query: str, count: int = 30, search_type: str | None = None
    ) -> list[dict]:
        """Public search for chats and channels.

        Args:
            query: Search string.
            count: Max results.
            search_type: "ALL", "CHANNELS", or "PUBLIC_CHATS". None for all.
        """
        payload: dict[str, Any] = {
            "query": query,
            "count": count,
        }
        if search_type:
            payload["type"] = search_type
        result = await self._request(Opcode.SEARCH, payload)
        return result.get("result", [])

    # ── Stats & Reactions ───────────────────────────────────────

    async def get_message_stats(
        self, chat_id: int, message_ids: list[int] | int
    ) -> dict:
        """Get message stats (views count for channel posts).

        Args:
            chat_id: Chat ID.
            message_ids: Single message ID (int) or list of message IDs.
        """
        if isinstance(message_ids, int):
            message_ids = [message_ids]
        return await self._request(Opcode.GET_MESSAGE_STATS, {
            "chatId": chat_id,
            "messageIds": message_ids,
        })

    async def get_reactions(
        self, chat_id: int, message_ids: list[str]
    ) -> dict:
        """Get reactions summary for messages.

        Returns:
            dict with reaction counts per message.
        """
        return await self._request(Opcode.GET_REACTIONS, {
            "chatId": chat_id,
            "messageIds": message_ids,
        })

    async def get_detailed_reactions(
        self, chat_id: int, message_id: str, emoji: str | None = None
    ) -> dict:
        """Get detailed reactions (who reacted) for a message.

        Args:
            chat_id: Chat ID.
            message_id: Message ID.
            emoji: Filter by specific emoji (optional).

        Returns:
            dict with list of users who reacted.
        """
        payload: dict[str, Any] = {
            "chatId": chat_id,
            "messageId": message_id,
        }
        if emoji:
            payload["emoji"] = emoji
        return await self._request(Opcode.GET_DETAILED_REACTIONS, payload)

    # ── Calls ──────────────────────────────────────────────────

    async def get_call_history(self, count: int = 100) -> dict:
        """Get call history.

        Args:
            count: Number of calls to fetch.

        Returns:
            dict with 'history' list of call records.
        """
        return await self._request(Opcode.GET_CALL_HISTORY, {
            "forward": False,
            "count": count,
        })

    async def initiate_call(
        self, user_ids: list[int], is_video: bool = False
    ) -> dict:
        """Initiate an outgoing call (low-level, returns raw params).

        Args:
            user_ids: List of user IDs to call.
            is_video: True for video call, False for audio.

        Returns:
            dict with conversationId and WebRTC connection params.
        """
        conversation_id = str(uuid.uuid4()).upper()
        return await self._request(Opcode.INITIATE_CALL, {
            "conversationId": conversation_id,
            "calleeIds": user_ids,
            "internalParams": json.dumps({
                "deviceId": self._device_id,
                "sdkVersion": "2.8.10-beta.5",
                "clientAppKey": "CNHIJPLGDIHBABABA",
                "platform": "WEB",
                "protocolVersion": 5,
                "capabilities": "2A03F",
            }),
            "isVideo": is_video,
        })

    async def call(
        self,
        user_ids: list[int],
        is_video: bool = False,
        audio_output: str | None = None,
    ):
        """Make an audio/video call with full WebRTC support.

        Initiates the call via MAX WS, then connects to the
        videowebrtc signaling server and establishes a peer connection.

        Args:
            user_ids: List of user IDs to call.
            is_video: True for video call, False for audio only.
            audio_output: File path to record incoming audio (optional).

        Returns:
            MaxCall instance. Use call.wait() to block, call.hangup() to end.
        """
        from .calls import MaxCall

        # Step 1: Initiate call via MAX WebSocket (opcode 78)
        result = await self.initiate_call(user_ids, is_video=is_video)
        caller_params = json.loads(result["internalCallerParams"])

        # Step 2: Extract signaling URL and TURN/STUN from response
        signaling_url = caller_params["endpoint"]
        turn_config = caller_params.get("turn", {})
        stun_config = caller_params.get("stun", {})
        id_obj = caller_params.get("id", {})
        my_internal_id = id_obj.get("internal", 0) if isinstance(id_obj, dict) else 0

        # Add required URL params if missing (matching web client behavior)
        if "&platform=" not in signaling_url:
            signaling_url += (
                "&platform=WEB&appVersion=1.1&version=5"
                "&device=browser&capabilities=2A03F&clientType=ONE_ME&tgt=start"
            )

        print(f"[Call] Conversation: {result['conversationId']}")
        print(f"[Call] Signaling: {signaling_url[:80]}...")

        # Step 3: Create and start the WebRTC call
        call = MaxCall(
            signaling_url=signaling_url,
            turn_config=turn_config,
            stun_config=stun_config,
            my_user_id=my_internal_id,
            audio_output=audio_output,
        )
        await call.start(audio_only=not is_video)
        return call

    # ── Chat state ─────────────────────────────────────────────

    async def subscribe_chat(self, chat_id: int, subscribe: bool = True) -> dict:
        """Subscribe/unsubscribe to real-time events for a chat."""
        return await self._request(Opcode.SUBSCRIBE_CHAT, {
            "chatId": chat_id,
            "subscribe": subscribe,
        })

    # ── Media ──────────────────────────────────────────────────

    async def get_video_url(
        self,
        video_id: int,
        token: str,
        chat_id: int,
        message_id: str,
    ) -> dict:
        """Get playable video URL from CDN.

        Args:
            video_id: Video ID from message attach.
            token: Token from message attach.
            chat_id: Chat ID containing the message.
            message_id: Message ID containing the video.

        Returns:
            dict with video URLs by quality (MP4_480, MP4_720, etc.)
        """
        return await self._request(Opcode.GET_VIDEO, {
            "videoId": video_id,
            "token": token,
            "chatId": chat_id,
            "messageId": message_id,
        })

    # ── Reactions ─────────────────────────────────────────────

    async def react(
        self, chat_id: int, message_id: str | int, emoji: str
    ) -> dict:
        """Add a reaction to a message.

        Args:
            chat_id: Chat ID.
            message_id: Message ID.
            emoji: Reaction emoji (e.g. "👍", "❤️", "⚡️").
        """
        return await self._request(Opcode.REACT, {
            "chatId": chat_id,
            "messageId": int(message_id),
            "reaction": {
                "reactionType": "EMOJI",
                "id": emoji,
            },
        })

    async def remove_reaction(
        self, chat_id: int, message_id: str | int
    ) -> dict:
        """Remove your reaction from a message."""
        return await self._request(Opcode.CANCEL_REACTION, {
            "chatId": chat_id,
            "messageId": int(message_id),
        })

    async def set_chat_reaction_settings(
        self, chat_id: int, emojis: list[str]
    ) -> dict:
        """Set allowed reaction emojis for a chat."""
        return await self._request(Opcode.CHAT_REACTIONS_SETTINGS_SET, {
            "chatId": chat_id,
            "emojis": emojis,
        })

    async def get_chat_reaction_settings(self, chat_ids: list[int] | int) -> dict:
        """Get reaction settings for chat(s)."""
        if isinstance(chat_ids, int):
            chat_ids = [chat_ids]
        return await self._request(Opcode.REACTIONS_SETTINGS_GET_BY_CHAT_ID, {
            "chatIds": chat_ids,
        })

    # ── Social ───────────────────────────────────────────────

    async def get_user_score(self, user_id: int) -> dict:
        """Get user's score/karma."""
        return await self._request(Opcode.GET_USER_SCORE, {"contactId": user_id})

    async def complain_reasons(self) -> dict:
        """Get list of available complaint reasons."""
        return await self._request(Opcode.COMPLAIN_REASONS_GET, {"complainSync": 0})

    # ── Sessions ─────────────────────────────────────────────

    async def get_sessions(self) -> dict:
        """Get list of active sessions (devices)."""
        return await self._request(Opcode.GET_SESSIONS, {})

    async def close_session(self, session_id: str) -> dict:
        """Close/terminate another session.

        Args:
            session_id: Session ID to close.
        """
        return await self._request(Opcode.CLOSE_SESSION, {
            "sessionId": session_id,
        })

    # ── Drafts ──────────────────────────────────────────────

    async def save_draft(self, chat_id: int, text: str) -> dict:
        """Save a draft message for a chat.

        Args:
            chat_id: Chat ID.
            text: Draft text.
        """
        return await self._request(Opcode.DRAFT_SAVE, {
            "chatId": chat_id,
            "draft": {
                "text": text,
                "elements": [],
                "attaches": [],
            },
        })

    async def discard_draft(self, chat_id: int) -> dict:
        """Discard the draft for a chat."""
        return await self._request(Opcode.DRAFT_DISCARD, {
            "chatId": chat_id,
            "time": 0,
        })

    # ── Events ──────────────────────────────────────────────────

    def on_message(self, callback: Callable[[dict], Any]):
        """Register handler for incoming messages (opcode 128)."""
        self._handlers.setdefault(Opcode.PUSH_NEW_MESSAGE, []).append(callback)

    def on_presence(self, callback: Callable[[dict], Any]):
        """Register handler for presence updates (opcode 132)."""
        self._handlers.setdefault(Opcode.PUSH_PRESENCE, []).append(callback)

    def on_call(self, callback: Callable[[dict], Any]):
        """Register handler for incoming calls (opcode 137)."""
        self._handlers.setdefault(Opcode.PUSH_INCOMING_CALL, []).append(callback)

    def on_typing(self, callback: Callable[[dict], Any]):
        """Register handler for typing indicators (opcode 129)."""
        self._handlers.setdefault(Opcode.PUSH_TYPING, []).append(callback)

    def on_chat_update(self, callback: Callable[[dict], Any]):
        """Register handler for chat updates (opcode 135)."""
        self._handlers.setdefault(Opcode.PUSH_CHAT, []).append(callback)

    def on_delayed_message(self, callback: Callable[[dict], Any]):
        """Register handler for delayed/scheduled message updates (opcode 154)."""
        self._handlers.setdefault(Opcode.PUSH_MSG_DELAYED, []).append(callback)

    def on_reactions(self, callback: Callable[[dict], Any]):
        """Register handler for reaction changes (opcode 155)."""
        self._handlers.setdefault(Opcode.PUSH_REACTIONS_CHANGED, []).append(callback)

    def on_mark(self, callback: Callable[[dict], Any]):
        """Register handler for read receipts from other sessions (opcode 130)."""
        self._handlers.setdefault(Opcode.PUSH_MARK, []).append(callback)

    def on_contact(self, callback: Callable[[dict], Any]):
        """Register handler for contact list changes (opcode 131)."""
        self._handlers.setdefault(Opcode.PUSH_CONTACT, []).append(callback)

    def on_location(self, callback: Callable[[dict], Any]):
        """Register handler for location sharing updates (opcode 147)."""
        self._handlers.setdefault(Opcode.PUSH_LOCATION, []).append(callback)

    def on_folder_update(self, callback: Callable[[dict], Any]):
        """Register handler for folder changes (opcode 277)."""
        self._handlers.setdefault(Opcode.PUSH_FOLDERS, []).append(callback)

    def on_delete_range(self, callback: Callable[[dict], Any]):
        """Register handler for batch message deletions (opcode 140)."""
        self._handlers.setdefault(Opcode.PUSH_MSG_DELETE_RANGE, []).append(callback)

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


def parse_formatted_text(text: str) -> tuple[str, list[dict]]:
    """Parse text with simple markup into plain text + MAX elements.

    Supported markup:
        **bold**           → STRONG
        *italic*           → EMPHASIZED
        ***bold italic***  → STRONG + EMPHASIZED
        ~~strikethrough~~  → STRIKETHROUGH
        ++underline++      → UNDERLINE
        ^^highlighted^^    → HIGHLIGHTED
        `code`             → MONOSPACED
        [link text](url)   → LINK with url

    Returns:
        (plain_text, elements) tuple ready for send_message().

    Example:
        text, elements = parse_formatted_text("Hello **world** and [click here](https://example.com)")
        await client.send_message(chat_id, text, elements=elements)
    """
    import re

    elements = []
    result = []
    pos = 0

    markup_re = re.compile(
        r'\*\*\*(.+?)\*\*\*'    # group 1: bold+italic content
        r'|\*\*(.+?)\*\*'        # group 2: bold content
        r'|\*(.+?)\*'            # group 3: italic content
        r'|~~(.+?)~~'            # group 4: strikethrough content
        r'|\+\+(.+?)\+\+'       # group 5: underline content
        r'|\^\^(.+?)\^\^'       # group 6: highlighted content
        r'|`(.+?)`'              # group 7: code/monospaced content
        r'|\[(.+?)\]\((.+?)\)'   # group 8: link text, group 9: url
    )

    for match in markup_re.finditer(text):
        if match.start() > pos:
            result.append(text[pos:match.start()])
        pos = match.end()

        if match.group(1) is not None:  # ***bold italic***
            content = match.group(1)
            offset = sum(len(r) for r in result)
            elements.append({"type": "STRONG", "from": offset, "length": len(content)})
            elements.append({"type": "EMPHASIZED", "from": offset, "length": len(content)})
            result.append(content)
        elif match.group(2) is not None:  # **bold**
            content = match.group(2)
            offset = sum(len(r) for r in result)
            elements.append({"type": "STRONG", "from": offset, "length": len(content)})
            result.append(content)
        elif match.group(3) is not None:  # *italic*
            content = match.group(3)
            offset = sum(len(r) for r in result)
            elements.append({"type": "EMPHASIZED", "from": offset, "length": len(content)})
            result.append(content)
        elif match.group(4) is not None:  # ~~strikethrough~~
            content = match.group(4)
            offset = sum(len(r) for r in result)
            elements.append({"type": "STRIKETHROUGH", "from": offset, "length": len(content)})
            result.append(content)
        elif match.group(5) is not None:  # ++underline++
            content = match.group(5)
            offset = sum(len(r) for r in result)
            elements.append({"type": "UNDERLINE", "from": offset, "length": len(content)})
            result.append(content)
        elif match.group(6) is not None:  # ^^highlighted^^
            content = match.group(6)
            offset = sum(len(r) for r in result)
            elements.append({"type": "HIGHLIGHTED", "from": offset, "length": len(content)})
            result.append(content)
        elif match.group(7) is not None:  # `code`
            content = match.group(7)
            offset = sum(len(r) for r in result)
            elements.append({"type": "MONOSPACED", "from": offset, "length": len(content)})
            result.append(content)
        elif match.group(8) is not None:  # [text](url)
            content = match.group(8)
            url = match.group(9)
            offset = sum(len(r) for r in result)
            elements.append({
                "type": "LINK",
                "from": offset,
                "length": len(content),
                "attributes": {"url": url},
            })
            result.append(content)

    if pos < len(text):
        result.append(text[pos:])

    return ''.join(result), elements


class MaxAPIError(Exception):
    """Error from MAX API."""

    def __init__(self, payload: dict):
        self.error = payload.get("error", "unknown")
        self.message = payload.get("message", "")
        self.localized = payload.get("localizedMessage", "")
        super().__init__(f"[{self.error}] {self.localized or self.message}")


def _guess_mime(path: Path) -> str:
    """Guess MIME type from file extension."""
    ext = path.suffix.lower()
    mime_map = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".mp4": "video/mp4",
        ".mov": "video/quicktime",
        ".avi": "video/x-msvideo",
        ".mp3": "audio/mpeg",
        ".ogg": "audio/ogg",
        ".opus": "audio/opus",
        ".wav": "audio/wav",
        ".pdf": "application/pdf",
        ".zip": "application/zip",
    }
    return mime_map.get(ext, "application/octet-stream")
