import asyncio
import types
import unittest

from app.config import Config
from app.rankingsystem.bots.discord import bot as discord_bot_module
from app.rankingsystem.bots.discord.bot import DiscordBot
from app.rankingsystem.bots.discord.client_manager import ClientManager


class FakeBot:
    async def wait_until_ready(self):
        return None


class FakeDiscordClient:
    instances = []

    def __init__(self, *args, **kwargs):
        self.events = {}
        FakeDiscordClient.instances.append(self)

    def event(self, func):
        self.events[func.__name__] = func
        return func


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

    def test_discord_bot_creates_fresh_client_instances(self):
        original_bot = discord_bot_module.commands.Bot
        FakeDiscordClient.instances = []
        discord_bot_module.commands.Bot = FakeDiscordClient
        try:
            bot = DiscordBot()
            first = bot.create_bot()
            second = bot.create_bot()
        finally:
            discord_bot_module.commands.Bot = original_bot

        self.assertIsNot(first, second)
        self.assertEqual(len(FakeDiscordClient.instances), 2)
        self.assertIn("on_ready", first.events)
        self.assertIn("on_ready", second.events)


if __name__ == "__main__":
    unittest.main()
