"""Basic usage — auto-login, list chats, read messages."""

import asyncio
import sys
sys.path.insert(0, "..")

from max_api import MaxClient


async def main():
    async with MaxClient() as client:
        # First run:  shows QR in terminal, you scan with phone, enter 2FA
        # Next runs:  instant login from saved token (~/.max_token.json)
        await client.auto_login()

        # Get all chats
        chats = await client.get_chats()
        print(f"\n{'='*50}")
        print(f"You have {len(chats)} chats:\n")
        for chat in chats[:10]:
            last = chat.get("lastMessage", {})
            text = last.get("text", "")[:60].replace("\n", " ")
            chat_type = chat.get("type", "?")
            print(f"  [{chat['id']:>12}] ({chat_type:>7}) {text}")

        # Read messages from first chat
        if chats:
            chat_id = chats[0]["id"]
            messages = await client.get_messages(chat_id)
            print(f"\n{'='*50}")
            print(f"Last messages in chat {chat_id}:\n")
            for msg in messages[-5:]:
                sender = msg.get("sender", "?")
                text = msg.get("text", "")[:80].replace("\n", " ")
                print(f"  [{sender}] {text}")


asyncio.run(main())
