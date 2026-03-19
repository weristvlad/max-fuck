"""Debug: check token refresh response and token TTL."""
import sys, os; sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import asyncio
import json
from max_api import MaxClient
from max_api.auth import load_token, save_token


async def main():
    async with MaxClient() as client:
        await client.auto_login()

        print("\n=== Token refresh debug ===")
        print(f"Current token (first 30 chars): {client._token[:30]}...")

        # Call refresh and see full response
        result = await client._request(158, {})  # TOKEN_REFRESH
        print(f"\nRefresh response:")
        print(json.dumps(result, indent=2, default=str))

        if "token" in result:
            new_token = result["token"]
            print(f"\nNew token (first 30 chars): {new_token[:30]}...")
            print(f"Same as before: {new_token == client._token}")
            save_token(new_token)

        # Also check what login returns — maybe there's session info
        print("\n=== Login response structure ===")
        result2 = await client._request(1, {"interactive": True})  # PING
        print(json.dumps(result2, indent=2, default=str)[:500])


asyncio.run(main())
