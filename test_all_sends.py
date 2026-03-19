"""Test: send every message type to Влад."""

import asyncio
import struct
import traceback
import wave
import tempfile
from pathlib import Path
from max_api import MaxClient

# Влад's chat from HAR
CHAT_ID = 13796912


def create_test_image(path: Path):
    """Create a minimal valid PNG (1x1 red pixel)."""
    import zlib
    raw = b'\x00\xff\x00\x00'  # filter byte + RGB
    compressed = zlib.compress(raw)

    def chunk(chunk_type, data):
        c = chunk_type + data
        crc = struct.pack('>I', zlib.crc32(c) & 0xffffffff)
        return struct.pack('>I', len(data)) + c + crc

    png = b'\x89PNG\r\n\x1a\n'
    png += chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 2, 0, 0, 0))
    png += chunk(b'IDAT', compressed)
    png += chunk(b'IEND', b'')
    path.write_bytes(png)


def create_test_audio(path: Path):
    """Create a minimal WAV file (0.5s of silence)."""
    with wave.open(str(path), 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(16000)
        f.writeframes(b'\x00\x00' * 8000)


async def run_test(name, coro):
    """Run a single test with error handling."""
    print(f"\n{name}...")
    try:
        result = await coro
        msg_id = result.get("message", {}).get("id", "?")
        print(f"  OK! Message ID: {msg_id}")
        return True
    except Exception as e:
        print(f"  FAIL: {e}")
        traceback.print_exc()
        return False


async def main():
    async with MaxClient() as client:
        await client.auto_login()

        print("=" * 50)
        print(f"Sending test messages to chat {CHAT_ID}")
        print("=" * 50)

        # 1. Text
        await run_test(
            "[1/6] Text message",
            client.send_message(CHAT_ID, "тест max-fuck API: текст"),
        )
        await asyncio.sleep(1)

        # 2. Photo
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            test_img = Path(f.name)
            create_test_image(test_img)
        try:
            await run_test(
                "[2/6] Photo with caption",
                client.send_photo(CHAT_ID, test_img, text="тест: фото"),
            )
        finally:
            test_img.unlink(missing_ok=True)
        await asyncio.sleep(1)

        # 3. File
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            test_file = Path(f.name)
            test_file.write_text("test file from max-fuck API\nhello world")
        try:
            await run_test(
                "[3/6] File",
                client.send_file(CHAT_ID, test_file, text="тест: файл"),
            )
        finally:
            test_file.unlink(missing_ok=True)
        await asyncio.sleep(1)

        # 4. Voice
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            test_audio = Path(f.name)
            create_test_audio(test_audio)
        try:
            await run_test(
                "[4/6] Voice message",
                client.send_voice(CHAT_ID, test_audio, duration_ms=500),
            )
        finally:
            test_audio.unlink(missing_ok=True)
        await asyncio.sleep(1)

        # 5. Reply
        print("\n[5/6] Reply to last message...")
        try:
            messages = await client.get_messages(CHAT_ID, backward=10)
            last_msg = None
            for m in reversed(messages):
                if m.get("type") == "USER" and m.get("text"):
                    last_msg = m
                    break
            if last_msg:
                result = await client.send_message(
                    CHAT_ID,
                    f"тест: реплай на «{last_msg['text'][:20]}»",
                    reply_to=last_msg["id"],
                )
                print(f"  OK! Replied to msg {last_msg['id']}")
            else:
                print("  SKIP: no text messages found")
        except Exception as e:
            print(f"  FAIL: {e}")
            traceback.print_exc()
        await asyncio.sleep(1)

        # 6. Photo without caption (just image)
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            test_img2 = Path(f.name)
            create_test_image(test_img2)
        try:
            await run_test(
                "[6/6] Photo without caption",
                client.send_photo(CHAT_ID, test_img2),
            )
        finally:
            test_img2.unlink(missing_ok=True)

        print("\n" + "=" * 50)
        print("Done!")


asyncio.run(main())
