import time
import asyncio
import discord
from discord.ext import commands
from app.utils.logger import RankingLogger
from app.config import Config
from app.rankingsystem.bots.discord.client_manager import ClientManager
from app.rankingsystem.bots.discord.utils import set_ranks, send_verification, create_owned_channel, set_user_group, remove_user_group, move_channel_apex

logging = RankingLogger(__name__).get_logger()


class DiscordBot:
    BASE_RECONNECT_DELAY = 5
    MAX_RECONNECT_DELAY = 300

    def __init__(self):
        self.token = Config.DISCORD_TOKEN
        self.intents = discord.Intents.default()
        self.intents.voice_states = True
        self.intents.message_content = True
        self.intents.members = True
        self.bot = None
        self.time_tracker = None
        self.running = True

    def create_bot(self):
        bot = commands.Bot(command_prefix='!', intents=self.intents)

        @bot.event
        async def on_ready():
            self.time_tracker = ClientManager(bot)
            try:
                await bot.add_cog(self.time_tracker)
            except discord.errors.ClientException:
                logging.error("ClientException: Cog already loaded")

        return bot

    def run(self):
        reconnect_delay = self.BASE_RECONNECT_DELAY
        while self.running:
            self.time_tracker = None
            self.bot = self.create_bot()
            try:
                self.bot.run(self.token)
                reconnect_delay = self.BASE_RECONNECT_DELAY
            except discord.errors.ConnectionClosed:
                logging.error("Connection to Discord lost.")
            except discord.errors.GatewayNotFound:
                logging.error("Discord gateway not found.")
            except asyncio.TimeoutError:
                logging.error("Discord connection timed out.")
            except asyncio.CancelledError:
                logging.error("Discord connection cancelled.")
            except Exception as e:
                logging.error(f"Error running the bot: {e}")
            finally:
                self.time_tracker = None
                self.bot = None

            if self.running:
                logging.info(f"Recreating Discord session in {reconnect_delay} seconds.")
                time.sleep(reconnect_delay)
                reconnect_delay = min(self.MAX_RECONNECT_DELAY, reconnect_delay * 2)

    def get_online_users(self):
        if self.time_tracker:
            return list(self.time_tracker.connected_users), self.time_tracker.user_name_map
        return list(), {}

    async def check_ranks(self, user_id, check_type="both"):
        """Check if user has the correct rank and/or division roles and update if necessary"""
        return await self.time_tracker.check_user_roles(user_id, check_type)
    
    async def set_ranks(self, user_id, level: int = None, division: int = None):
        """Set Discord role(s) for a user based on their level and/or division."""
        return await set_ranks(self.bot, user_id, level, division)

    async def send_verification(self, user_id, code) -> bool:
        """Send verification code to Discord user"""
        return await send_verification(self.bot, user_id, code)
        
    async def create_owned_channel(self, user_id: int, channel_name: str) -> int:
        """Creates a permanent voice channel with owner permissions"""
        return await create_owned_channel(self.bot, user_id, channel_name)
    
    async def set_user_group(self, user_id: int, group_id: int) -> bool:
        """Sets a specific user group for a given user"""
        return await set_user_group(self.bot, user_id, group_id)
    
    async def remove_user_group(self, user_id: int, group_id: int) -> bool:
        """Remove a specific user group for a given user"""
        return await remove_user_group(self.bot, user_id, group_id)
    
    async def move_channel_apex(self, channel_id: int) -> bool:
        """Move a channel to a new location"""
        return await move_channel_apex(self.bot, channel_id)

    def stop(self):
        self.running = False
