"""WebRTC call support for MAX messenger.

Handles the signaling protocol on wss://videowebrtc.okcdn.ru
and manages the RTCPeerConnection via aiortc.
"""

import asyncio
import json
from typing import Optional, Callable, Any
from urllib.parse import urlencode

import websockets
from aiortc import (
    RTCPeerConnection,
    RTCSessionDescription,
    RTCIceCandidate,
    RTCConfiguration,
    RTCIceServer,
    MediaStreamTrack,
)
from aiortc.contrib.media import MediaPlayer, MediaRecorder


class MaxCall:
    """Manages a single WebRTC call session.

    Usage:
        call = MaxCall(call_params)
        await call.start()          # connect signaling + start WebRTC
        await call.wait()           # block until call ends
        await call.hangup()         # end the call
    """

    def __init__(
        self,
        signaling_url: str,
        turn_config: dict,
        stun_config: dict,
        my_user_id: int = 0,
        audio_input: str | None = None,
        audio_output: str | None = None,
        on_audio_track: Callable[[MediaStreamTrack], Any] | None = None,
    ):
        """
        Args:
            signaling_url: Full WSS URL for videowebrtc.okcdn.ru with all params.
            turn_config: {"urls": [...], "username": "...", "credential": "..."}
            stun_config: {"urls": [...]}
            my_user_id: Our internal user ID (to identify remote participants).
            audio_input: Audio input device/file for MediaPlayer. None for default mic.
            audio_output: File path to record incoming audio. None to skip recording.
            on_audio_track: Callback when remote audio track is received.
        """
        self._signaling_url = signaling_url
        self._turn_config = turn_config
        self._stun_config = stun_config
        self._my_user_id = my_user_id
        self._participant_id: int = 0  # Resolved from connection notification
        self._audio_input = audio_input
        self._audio_output = audio_output
        self._on_audio_track = on_audio_track

        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._pc: Optional[RTCPeerConnection] = None
        self._seq = 0
        self._connected = asyncio.Event()
        self._ended = asyncio.Event()
        self._signaling_ready = asyncio.Event()
        self._recv_task: Optional[asyncio.Task] = None
        self._player: Optional[MediaPlayer] = None
        self._recorder: Optional[MediaRecorder] = None

    async def start(self, audio_only: bool = True):
        """Connect to signaling server and start the WebRTC call.

        Args:
            audio_only: If True, only send/receive audio (no video).
        """
        # Configure ICE servers
        ice_servers = []
        if self._stun_config.get("urls"):
            urls = self._stun_config["urls"]
            if isinstance(urls, str):
                urls = [urls]
            ice_servers.append(RTCIceServer(urls=urls))
        if self._turn_config.get("urls"):
            urls = self._turn_config["urls"]
            if isinstance(urls, str):
                urls = [urls]
            ice_servers.append(RTCIceServer(
                urls=urls,
                username=self._turn_config.get("username", ""),
                credential=self._turn_config.get("credential", ""),
            ))

        config = RTCConfiguration(iceServers=ice_servers)
        self._pc = RTCPeerConnection(configuration=config)

        # Handle incoming tracks
        @self._pc.on("track")
        def on_track(track):
            print(f"[Call] Received remote {track.kind} track")
            if track.kind == "audio":
                if self._on_audio_track:
                    self._on_audio_track(track)
                if self._audio_output:
                    self._recorder = MediaRecorder(self._audio_output)
                    self._recorder.addTrack(track)
                    asyncio.ensure_future(self._recorder.start())

        @self._pc.on("connectionstatechange")
        async def on_state():
            state = self._pc.connectionState
            print(f"[Call] Connection state: {state}")
            if state == "connected":
                self._connected.set()
            elif state in ("failed", "closed", "disconnected"):
                self._ended.set()

        @self._pc.on("icecandidate")
        async def on_ice(candidate):
            if candidate:
                await self._send_ice_candidate(candidate)

        # Add local audio track
        if self._audio_input:
            self._player = MediaPlayer(self._audio_input)
        else:
            # Default microphone
            try:
                self._player = MediaPlayer(
                    "default:none", format="avfoundation", options={}
                )
            except Exception:
                try:
                    self._player = MediaPlayer(
                        ":0", format="avfoundation", options={}
                    )
                except Exception:
                    print("[Call] Warning: could not open microphone, sending silence")
                    self._player = None

        if self._player and self._player.audio:
            self._pc.addTrack(self._player.audio)
        else:
            # Add a silent audio track as fallback
            from aiortc.mediastreams import AudioStreamTrack
            self._pc.addTrack(AudioStreamTrack())

        if not audio_only:
            if self._player and self._player.video:
                self._pc.addTrack(self._player.video)

        # Connect to signaling WebSocket
        self._ws = await websockets.connect(
            self._signaling_url,
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

        # Wait for connection notification (gives us participant IDs)
        print("[Call] Waiting for signaling server...")
        await asyncio.wait_for(self._signaling_ready.wait(), timeout=10)

        # Create and send SDP offer
        offer = await self._pc.createOffer()
        await self._pc.setLocalDescription(offer)
        await self._send_sdp_offer(offer)

        print(f"[Call] SDP offer sent to participant {self._participant_id}, waiting for answer...")

    async def wait(self, timeout: float | None = None):
        """Wait until the call ends.

        Args:
            timeout: Maximum seconds to wait. None for unlimited.
        """
        if timeout:
            await asyncio.wait_for(self._ended.wait(), timeout)
        else:
            await self._ended.wait()

    async def hangup(self):
        """End the call and clean up."""
        print("[Call] Hanging up...")
        if self._recorder:
            await self._recorder.stop()
        if self._pc:
            await self._pc.close()
        if self._ws:
            await self._ws.close()
        if self._recv_task:
            self._recv_task.cancel()
            try:
                await self._recv_task
            except asyncio.CancelledError:
                pass
        self._ended.set()
        print("[Call] Call ended.")

    # ── Signaling protocol ──────────────────────────────────

    async def _send_sdp_offer(self, offer: RTCSessionDescription):
        """Send SDP offer to remote participant."""
        self._seq += 1
        msg = {
            "command": "transmit-data",
            "sequence": self._seq,
            "participantId": self._participant_id,
            "data": {
                "sdp": {
                    "type": offer.type,
                    "sdp": offer.sdp,
                },
            },
            "participantType": "USER",
        }
        await self._ws.send(json.dumps(msg))

    async def _send_ice_candidate(self, candidate):
        """Send ICE candidate to remote participant."""
        self._seq += 1

        # aiortc RTCIceCandidate → standard format
        candidate_str = (
            f"candidate:{candidate.foundation} {candidate.component} "
            f"{candidate.protocol} {candidate.priority} "
            f"{candidate.ip} {candidate.port} typ {candidate.type}"
        )
        if candidate.relatedAddress:
            candidate_str += f" raddr {candidate.relatedAddress} rport {candidate.relatedPort}"
        candidate_str += f" generation 0 ufrag {candidate.sdpMid or ''}"

        msg = {
            "command": "transmit-data",
            "sequence": self._seq,
            "participantId": self._participant_id,
            "data": {
                "candidate": {
                    "candidate": candidate_str,
                    "sdpMid": "0",
                    "sdpMLineIndex": 0,
                    "usernameFragment": "",
                },
            },
            "participantType": "USER",
        }
        await self._ws.send(json.dumps(msg))

    async def _recv_loop(self):
        """Receive and handle signaling messages."""
        try:
            async for raw in self._ws:
                try:
                    msg = json.loads(raw)
                except (json.JSONDecodeError, TypeError):
                    continue

                msg_type = msg.get("type")
                command = msg.get("command")
                notification = msg.get("notification")

                if msg_type == "notification":
                    if notification == "connection":
                        print("[Call] Connected to signaling server")
                        conv = msg.get("conversation", {})
                        participants = conv.get("participants", [])
                        for p in participants:
                            state = p.get("state")
                            internal_id = p.get("id", 0)
                            ext_id = p.get("externalId", {}).get("id", "?")
                            print(f"  Participant {ext_id}: {state} (id={internal_id})")
                            # The remote participant is the one that's not us
                            if internal_id and internal_id != self._my_user_id:
                                self._participant_id = internal_id
                        if not self._participant_id and len(participants) > 1:
                            # Fallback: use the second participant
                            self._participant_id = participants[1].get("id", 0)
                        self._signaling_ready.set()

                    elif notification == "settings-update":
                        print("[Call] Received call settings")

                    elif notification == "participant-joined":
                        print("[Call] Participant joined the call")

                    elif notification == "participant-left":
                        print("[Call] Participant left the call")
                        self._ended.set()

                    elif notification == "conversation-destroyed":
                        print("[Call] Call ended by server")
                        self._ended.set()

                    else:
                        print(f"[Call] Notification: {notification}")

                elif command == "transmit-data":
                    data = msg.get("data", {})

                    # SDP answer from remote
                    if "sdp" in data:
                        sdp = data["sdp"]
                        print(f"[Call] Received SDP {sdp['type']}")
                        answer = RTCSessionDescription(
                            sdp=sdp["sdp"], type=sdp["type"]
                        )
                        await self._pc.setRemoteDescription(answer)
                        print("[Call] Remote description set")

                    # ICE candidate from remote
                    if "candidate" in data:
                        c = data["candidate"]
                        candidate_str = c.get("candidate", "")
                        if candidate_str:
                            # Parse the candidate string for aiortc
                            try:
                                candidate = _parse_ice_candidate(
                                    candidate_str,
                                    c.get("sdpMid", "0"),
                                    c.get("sdpMLineIndex", 0),
                                )
                                if candidate:
                                    await self._pc.addIceCandidate(candidate)
                            except Exception as e:
                                print(f"[Call] Failed to add ICE candidate: {e}")

                else:
                    print(f"[Call] Unknown message: {json.dumps(msg)[:200]}")

        except websockets.ConnectionClosed:
            print("[Call] Signaling connection closed")
            self._ended.set()


def _parse_ice_candidate(
    candidate_str: str, sdp_mid: str, sdp_m_line_index: int
) -> RTCIceCandidate | None:
    """Parse an ICE candidate string into an RTCIceCandidate."""
    # Format: candidate:foundation component protocol priority ip port typ type [...]
    if not candidate_str.startswith("candidate:"):
        return None

    parts = candidate_str.split()
    if len(parts) < 8:
        return None

    foundation = parts[0].split(":")[1]
    component = int(parts[1])
    protocol = parts[2]
    priority = int(parts[3])
    ip = parts[4]
    port = int(parts[5])
    # parts[6] == "typ"
    candidate_type = parts[7]

    related_address = None
    related_port = None
    for i in range(8, len(parts) - 1):
        if parts[i] == "raddr":
            related_address = parts[i + 1]
        elif parts[i] == "rport":
            related_port = int(parts[i + 1])

    return RTCIceCandidate(
        component=component,
        foundation=foundation,
        ip=ip,
        port=port,
        priority=priority,
        protocol=protocol,
        type=candidate_type,
        relatedAddress=related_address,
        relatedPort=related_port,
        sdpMid=sdp_mid,
        sdpMLineIndex=sdp_m_line_index,
    )


def build_signaling_url(
    user_id: int,
    conversation_id: str,
    token: str,
    capabilities: str = "2A03F",
) -> str:
    """Build the videowebrtc signaling WebSocket URL.

    Args:
        user_id: Your internal user ID (from call initiation response).
        conversation_id: Call conversation UUID.
        token: Auth token from call initiation.
        capabilities: Feature bitmask.
    """
    params = {
        "userId": user_id,
        "entityType": "USER",
        "conversationId": conversation_id,
        "token": token,
        "platform": "WEB",
        "appVersion": "1.1",
        "version": "5",
        "device": "browser",
        "capabilities": capabilities,
        "clientType": "ONE_ME",
        "tgt": "start",
    }
    return f"wss://videowebrtc.okcdn.ru/ws2?{urlencode(params)}"
