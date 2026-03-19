"""Echo bot — auto-login, then reply to every incoming message."""

import asyncio
import sys
sys.path.insert(0, "..")

from max_api import MaxClient


async def main():
    client = MaxClient()
    await client.connect()

    # Auto-login (QR first time, token after)
    login_data = await client.auto_login()

    # Get own user ID from login response to avoid echoing own messages
    my_id = None
    profile = login_data.get("profile", {})
    contact = profile.get("contact", {})
    my_id = contact.get("id")
    print(f"Logged in as user {my_id}. Listening for messages...\n")

    async def on_message(payload):
        msg = payload.get("message", {})
        chat_id = payload.get("chatId")
        sender = msg.get("sender")
        text = msg.get("text", "")

        if sender == my_id or not text:
            return

        print(f"[chat:{chat_id}] {sender}: {text}")
        await client.send_message(chat_id, f"Echo: {text}")

    client.on_message(on_message)

    try:
        await asyncio.Future()  # run forever
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        await client.disconnect()


asyncio.run(main())
