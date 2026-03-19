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
APP_VERSION = "26.3.7"
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
        self._device_id = str(uuid.uuid4())
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
                # Refresh token immediately + start background refresh
                try:
                    await self.refresh_token()
                except Exception:
                    pass
                self._start_token_refresh_loop()
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
        save_token(token, login_token=token)
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

        # Confirm upload (fire-and-forget, no response expected)
        try:
            await self._request(Opcode.CHECK_FILE_UPLOAD, {"fileId": info["fileId"]})
        except Exception:
            pass  # Some servers don't support this opcode
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
        cid = -int(time.time() * 1000)
        attach: dict[str, Any] = {
            "_type": "AUDIO",
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
        cid = -int(time.time() * 1000)
        msg: dict[str, Any] = {
            "cid": cid,
            "attaches": [{"_type": "VIDEO", "fileId": info["fileId"]}],
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
        cid = -int(time.time() * 1000)
        msg: dict[str, Any] = {
            "cid": cid,
            "attaches": [{
                "_type": "VIDEO",
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

    async def find_user(self, query: str) -> list[dict]:
        """Search for users by name, nickname, or phone.

        Args:
            query: Search string (name, @nickname, phone number).

        Returns:
            List of matching user dicts.
        """
        return await self.search(query, search_type="CONTACT")

    # ── Search ──────────────────────────────────────────────────

    async def search(
        self, query: str, count: int = 30, search_type: str = "ALL"
    ) -> list[dict]:
        """Search contacts and chats.

        Args:
            query: Search string.
            count: Max results.
            search_type: "ALL", "CONTACT", "CHAT", or "GROUP".
        """
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
