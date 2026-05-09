import asyncio
import logging as python_logging
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
        type(self).instances.append(self)

    def event(self, func):
        self.events[func.__name__] = func
        return func


class StopRun(BaseException):
    pass


class FakeRunDiscordClient(FakeDiscordClient):
    def run(self, token, **kwargs):
        self.run_token = token
        self.run_kwargs = kwargs
        raise StopRun()


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

    def test_discord_bot_disables_library_log_handler_on_run(self):
        original_bot = discord_bot_module.commands.Bot
        FakeRunDiscordClient.instances = []
        discord_bot_module.commands.Bot = FakeRunDiscordClient
        try:
            bot = DiscordBot()
            with self.assertRaises(StopRun):
                bot.run()
        finally:
            discord_bot_module.commands.Bot = original_bot

        self.assertEqual(len(FakeRunDiscordClient.instances), 1)
        self.assertIsNone(FakeRunDiscordClient.instances[0].run_kwargs.get("log_handler"))

    def test_discord_library_logger_is_configured_once(self):
        discord_logger = python_logging.getLogger("discord")
        original_handlers = list(discord_logger.handlers)
        original_level = discord_logger.level
        original_propagate = discord_logger.propagate
        original_configured = getattr(discord_logger, "_firephenix_configured", None)

        try:
            discord_logger.handlers[:] = []
            if hasattr(discord_logger, "_firephenix_configured"):
                delattr(discord_logger, "_firephenix_configured")

            DiscordBot()
            first_handlers = list(discord_logger.handlers)
            DiscordBot()

            self.assertEqual(len(first_handlers), 1)
            self.assertEqual(discord_logger.handlers, first_handlers)
            self.assertFalse(discord_logger.propagate)
            self.assertEqual(discord_logger.level, Config.LOGGER_LEVEL)
        finally:
            discord_logger.handlers[:] = original_handlers
            discord_logger.setLevel(original_level)
            discord_logger.propagate = original_propagate
            if original_configured is None:
                if hasattr(discord_logger, "_firephenix_configured"):
                    delattr(discord_logger, "_firephenix_configured")
            else:
                discord_logger._firephenix_configured = original_configured

    def test_discord_library_logger_adds_handler_when_null_handler_exists(self):
        discord_logger = python_logging.getLogger("discord")
        original_handlers = list(discord_logger.handlers)
        original_configured = getattr(discord_logger, "_firephenix_configured", None)

        try:
            null_handler = python_logging.NullHandler()
            discord_logger.handlers[:] = [null_handler]
            if hasattr(discord_logger, "_firephenix_configured"):
                delattr(discord_logger, "_firephenix_configured")

            DiscordBot()

            self.assertIn(null_handler, discord_logger.handlers)
            self.assertTrue(
                any(
                    getattr(handler, discord_bot_module.DISCORD_LOG_HANDLER_MARKER, False)
                    for handler in discord_logger.handlers
                )
            )
        finally:
            discord_logger.handlers[:] = original_handlers
            if original_configured is None:
                if hasattr(discord_logger, "_firephenix_configured"):
                    delattr(discord_logger, "_firephenix_configured")
            else:
                discord_logger._firephenix_configured = original_configured


if __name__ == "__main__":
    unittest.main()
