"""Debug: check what initiate_call returns."""
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import json
from max_api import MaxClient

CALLEE_IDS = [910157745201]


async def main():
    async with MaxClient() as client:
        await client.auto_login()
        print("Calling initiate_call...")
        result = await client.initiate_call(CALLEE_IDS, is_video=True)
        print(json.dumps(result, indent=2, default=str))


asyncio.run(main())
