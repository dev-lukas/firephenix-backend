import asyncio
import types
import unittest

from app.config import Config
from app.rankingsystem.bots.discord.client_manager import ClientManager


class FakeBot:
    async def wait_until_ready(self):
        return None


class DiscordVoiceScanTests(unittest.TestCase):
    def test_periodic_scan_ignores_excluded_role_members(self):
        original_excluded_role_id = Config.DISCORD_EXCLUDED_ROLE_ID
        Config.DISCORD_EXCLUDED_ROLE_ID = "1234"
        try:
            manager = ClientManager.__new__(ClientManager)
            manager.bot = FakeBot()
            manager.excluded_role_id = int(Config.DISCORD_EXCLUDED_ROLE_ID)
            manager.connected_users = {1, 2}
            manager.user_name_map = {1: "Old excluded", 2: "Old normal"}

            excluded_member = types.SimpleNamespace(
                id=1,
                bot=False,
                display_name="Excluded",
                roles=[types.SimpleNamespace(id=1234)],
            )
            normal_member = types.SimpleNamespace(
                id=2,
                bot=False,
                display_name="Normal",
                roles=[],
            )
            manager.guild = types.SimpleNamespace(
                voice_channels=[
                    types.SimpleNamespace(members=[excluded_member, normal_member])
                ]
            )

            asyncio.run(manager.scan_voice_channels())

            self.assertEqual(manager.connected_users, {2})
            self.assertNotIn(1, manager.user_name_map)
            self.assertEqual(manager.user_name_map[2], "Normal")
        finally:
            Config.DISCORD_EXCLUDED_ROLE_ID = original_excluded_role_id


if __name__ == "__main__":
    unittest.main()
