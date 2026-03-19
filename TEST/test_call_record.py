"""Test: call user 193148548 with mp3 audio + jpg video, record incoming audio."""
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import fractions
import json
import time

import av
from aiortc import MediaStreamTrack
from aiortc.contrib.media import MediaPlayer, MediaRecorder
from av import VideoFrame

from max_api import MaxClient
from max_api.calls import MaxCall

CALLEE_IDS = [193148548]

AUDIO_FILE = "TEST/voicecall/voice.mp3"
IMAGE_FILE = "TEST/voicecall/image.jpg"
RECORDING_FILE = "call_recording.wav"

CALL_DURATION = 60  # seconds


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

        print(f"Calling user {CALLEE_IDS[0]}...")
        print(f"  Audio out: {AUDIO_FILE}")
        print(f"  Video out: {IMAGE_FILE}")
        print(f"  Recording: {RECORDING_FILE}")

        result = await client.initiate_call(CALLEE_IDS, is_video=True)

        if result.get("rejectedParticipants"):
            for rp in result["rejectedParticipants"]:
                print(f"  REJECTED: {rp}")
            return

        caller_params = json.loads(result["internalCallerParams"])
        base_url = caller_params["endpoint"]
        if "&platform=" not in base_url:
            base_url += "&platform=WEB&appVersion=1.1&version=5&device=browser&capabilities=2A03F&clientType=ONE_ME&tgt=start"

        turn_config = caller_params.get("turn", {})
        stun_config = caller_params.get("stun", {})
        id_obj = caller_params.get("id", {})
        my_internal_id = id_obj.get("internal", 0)

        print(f"  Conversation: {result['conversationId']}")

        call = MaxCall(
            signaling_url=base_url,
            turn_config=turn_config,
            stun_config=stun_config,
            my_user_id=my_internal_id,
            audio_output=RECORDING_FILE,
        )

        call._player = MediaPlayer(AUDIO_FILE)
        call._image_player = type('FakePlayer', (), {
            'video': ImageVideoTrack(IMAGE_FILE),
            'audio': None,
        })()

        await call.start_with_custom_media()

        print(f"\nRinging! Auto-hangup in {CALL_DURATION}s. Answer the call!")
        print(f"Incoming audio will be saved to: {RECORDING_FILE}\n")

        try:
            await call.wait(timeout=CALL_DURATION)
        except asyncio.TimeoutError:
            print(f"\n{CALL_DURATION}s elapsed.")
        finally:
            await call.hangup()

        print(f"\nDone! Check {RECORDING_FILE} for the recording.")


asyncio.run(main())
