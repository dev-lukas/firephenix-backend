import time
import discord
from discord.ext import commands
from app.utils.logger import RankingLogger
from app.config import Config
from app.rankingsystem.bots.discord.client_manager import ClientManager
from app.rankingsystem.bots.discord.utils import set_ranks, send_verification, create_owned_channel

logging = RankingLogger(__name__).get_logger()


class DiscordBot:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(DiscordBot, cls).__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.initialized = True
            self.token = Config.DISCORD_TOKEN
            
            self.intents = discord.Intents.default()
            self.intents.voice_states = True
            self.intents.message_content = True
            self.intents.members = True

            self.bot = commands.Bot(command_prefix='!', intents=self.intents)
            self.time_tracker = None

            self.setup_events()

    def setup_events(self):
        @self.bot.event
        async def on_ready():
            self.time_tracker = ClientManager(self.bot)
            await self.bot.add_cog(self.time_tracker)

    def run(self):
        while True:
            try:
                self.bot.run(self.token)
            except discord.errors.ConnectionClosed:
                logging.error("Connection to Discord lost. Reconnecting in 5 seconds.")
            except discord.errors.GatewayNotFound:
                logging.error("Gateway not found. Reconnecting in 5 seconds.")
                time.sleep(30)
            except Exception as e:
                logging.error(f"Error running the bot: {e}")
                time.sleep(60)

    def get_online_users(self):
        if self.time_tracker:
            return list(self.time_tracker.connected_users), self.time_tracker.user_name_map
        return list(), {}
    
    async def set_ranks(self, user_id, level: int = None, division: int = None):
        """Set Discord role(s) for a user based on their level and/or division."""
        return await set_ranks(self.bot, user_id, level, division)

    async def send_verification(self, user_id, code) -> bool:
        """Send verification code to Discord user"""
        return await send_verification(self.bot, user_id, code)
        
    async def create_owned_channel(self, user_id: int, channel_name: str) -> int:
        """Creates a permanent voice channel with owner permissions"""
        return await create_owned_channel(self.bot, user_id, channel_name)
