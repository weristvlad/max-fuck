"""Test: login via QR and send a message."""
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
from max_api import MaxClient

CHAT_ID = 188165680
MESSAGE = "hello from max-fuck API 🤙"


async def main():
    async with MaxClient() as client:
        await client.auto_login()
        print(f"\nSending message to chat {CHAT_ID}...")
        result = await client.send_message(CHAT_ID, MESSAGE)
        print(f"Sent! Message ID: {result.get('message', {}).get('id')}")


asyncio.run(main())
