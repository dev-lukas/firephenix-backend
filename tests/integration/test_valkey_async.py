"""Regression guard for the bot's async valkey blocking reads.

valkey.asyncio defaults socket_timeout to 5 seconds (the sync client defaults
to None), which kills any blocking read longer than that: the TTT consumer's
XREADGROUP block=5000 and the pubsub command listener both died with
"Timeout reading from ..." in production. Config.valkey_connection_kwargs()
must keep blocking reads alive indefinitely.
"""

import asyncio
import unittest

import valkey.asyncio as avalkey

from app.config import Config
from tests.integration.harness import skip_unless_integration


@skip_unless_integration
class AsyncValkeyBlockingReadTests(unittest.TestCase):
    def test_blocking_xreadgroup_survives_past_five_seconds(self):
        async def go():
            client = avalkey.Valkey(**Config.valkey_connection_kwargs())
            try:
                try:
                    await client.xgroup_create(
                        "test:async-blocking", "g", id="0", mkstream=True)
                except Exception:
                    pass  # BUSYGROUP on rerun
                # block for 6s > the 5s asyncio default socket_timeout; must
                # return empty, not raise TimeoutError
                rows = await client.xreadgroup(
                    "g", "c", {"test:async-blocking": ">"}, count=1, block=6000)
                self.assertEqual(rows or [], [])
            finally:
                try:
                    await client.delete("test:async-blocking")
                finally:
                    await client.aclose()

        asyncio.run(go())


if __name__ == "__main__":
    unittest.main()
