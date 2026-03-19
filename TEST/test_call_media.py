"""Test: call Влад with mp3 audio and jpg image as video stream."""
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import fractions
import json
import time

import av
from aiortc import MediaStreamTrack
from aiortc.contrib.media import MediaPlayer
from av import VideoFrame

from max_api import MaxClient
from max_api.calls import MaxCall

# Влад's external user ID (not chat ID!)
CALLEE_IDS = [6725252]

AUDIO_FILE = "TEST/voicecall/voice.mp3"
IMAGE_FILE = "TEST/voicecall/image.jpg"

CALL_DURATION = 30  # seconds


class ImageVideoTrack(MediaStreamTrack):
    """A video track that streams a static image at 15 fps."""

    kind = "video"

    def __init__(self, image_path: str, fps: int = 15):
        super().__init__()
        container = av.open(image_path)
        frame = next(container.decode(video=0))
        self._frame = frame.to_ndarray(format="bgr24")
        container.close()
        self._fps = fps
        self._time_base = fractions.Fraction(1, fps)
        self._pts = 0
        self._start = time.time()

    async def recv(self):
        pts = self._pts
        self._pts += 1
        wait = self._start + (pts / self._fps) - time.time()
        if wait > 0:
            await asyncio.sleep(wait)
        frame = VideoFrame.from_ndarray(self._frame, format="bgr24")
        frame.pts = pts
        frame.time_base = self._time_base
        return frame


async def main():
    async with MaxClient() as client:
        await client.auto_login()

        print("Initiating video call to Влад (6725252)...")
        print(f"  Audio: {AUDIO_FILE}")
        print(f"  Video: {IMAGE_FILE} (static image → video stream)")

        # Step 1: Initiate call via MAX WS (opcode 78)
        result = await client.initiate_call(CALLEE_IDS, is_video=True)

        if result.get("rejectedParticipants"):
            for rp in result["rejectedParticipants"]:
                print(f"  REJECTED: {rp}")
            return

        caller_params = json.loads(result["internalCallerParams"])

        # Build signaling URL with all required params (matching web client)
        base_url = caller_params["endpoint"]
        if "&platform=" not in base_url:
            base_url += "&platform=WEB&appVersion=1.1&version=5&device=browser&capabilities=2A03F&clientType=ONE_ME&tgt=start"

        turn_config = caller_params.get("turn", {})
        stun_config = caller_params.get("stun", {})
        id_obj = caller_params.get("id", {})
        my_internal_id = id_obj.get("internal", 0)

        print(f"  Conversation: {result['conversationId']}")
        print(f"  My internal ID: {my_internal_id}")

        # Step 2: Create call with custom media
        call = MaxCall(
            signaling_url=base_url,
            turn_config=turn_config,
            stun_config=stun_config,
            my_user_id=my_internal_id,
            audio_output="call_recording.wav",
        )

        # Set up audio from mp3 file
        call._player = MediaPlayer(AUDIO_FILE)

        # Set up video from static image
        call._image_player = type('FakePlayer', (), {
            'video': ImageVideoTrack(IMAGE_FILE),
            'audio': None,
        })()

        print("\n  Starting WebRTC...")
        await call.start_with_custom_media()

        print(f"\n  Call ringing! Auto-hangup in {CALL_DURATION}s...")
        print("  (Answer on phone to hear audio and see image)\n")

        try:
            await call.wait(timeout=CALL_DURATION)
        except asyncio.TimeoutError:
            print(f"\n  {CALL_DURATION}s elapsed.")
        finally:
            await call.hangup()

        print("Done!")


asyncio.run(main())
