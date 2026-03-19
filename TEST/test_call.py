"""Test: make an audio call to Влад."""
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
from max_api import MaxClient

# Влад's user ID from HAR (externalId in MAX)
# You need the MAX internal user IDs, not chat IDs
# From HAR: externalId "6725252" → internal id 910157745201
CALLEE_IDS = [910157745201]

CALL_DURATION = 15  # seconds


async def main():
    async with MaxClient() as client:
        await client.auto_login()

        print("Starting audio call...")
        call = await client.call(CALLEE_IDS, is_video=False)

        print(f"\nCall is ringing! Will auto-hangup in {CALL_DURATION}s...")
        print("(Answer on your phone to test audio)\n")

        try:
            await call.wait(timeout=CALL_DURATION)
        except asyncio.TimeoutError:
            print(f"\n{CALL_DURATION}s elapsed.")
        finally:
            await call.hangup()

        print("Done!")


asyncio.run(main())
