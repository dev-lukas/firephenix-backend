import asyncio
import os
import discord
from discord.ext import commands
from app.utils.database import DatabaseManager
from app.utils.logger import RankingLogger
from app.config import Config

logging = RankingLogger(__name__).get_logger()


class DiscordBot:
    def __init__(self):
        self.token = Config.DISCORD_TOKEN
        
        self.intents = discord.Intents.default()
        self.intents.voice_states = True
        self.intents.message_content = True
        self.intents.members = True

        self.bot = commands.Bot(command_prefix='!', intents=self.intents)

        self.setup_events()

    def setup_events(self):
        
        @self.bot.event
        async def on_ready():
            await self.bot.add_cog(self.TimeTracker(self.bot))

    def run(self):
        try:
            self.bot.run(self.token)
        except Exception as e:
            logging.error(f"Error running the bot: {e}")

    class TimeTracker(commands.Cog):

        def __init__(self, bot: commands.Bot):
            self.excluded_role_id = Config.DISCORD_EXCLUDED_ROLE_ID

            self.database = DatabaseManager(
                host=os.getenv("DB_HOST"),
                user=os.getenv("DB_USER"),
                password=os.getenv("DB_PASSWORD"),
                database=os.getenv("DB_NAME")
            )
            self.bot = bot
            self.connected_users = set()
            self.bg_task = self.bot.loop.create_task(self.scan_voice_channels())
            self.bg_task = self.bot.loop.create_task(self.update_time())
            logging.info("Discord Bot started successfully.")

        async def update_time(self):
            """Background task that runs every minute to update the time spent in voice chat for each user.
            """
            await self.bot.wait_until_ready()
            while not self.bot.is_closed():
                if self.connected_users:
                    self.database.update_times(self.connected_users, "discord")
                await asyncio.sleep(60)

        async def scan_voice_channels(self):
            """Scan all voice channels and add connected users to the set"""
            await self.bot.wait_until_ready()
            
            for guild in self.bot.guilds:
                for voice_channel in guild.voice_channels:
                    for member in voice_channel.members:
                        if not member.bot:  # Ignore bots
                            self.connected_users.add(member.id)
              
            logging.info(f"Initial voice channel scan complete. Found {len(self.connected_users)} users.")

        @commands.Cog.listener()
        async def on_voice_state_update(self, member, before, after):
            """on_voice_state_update event handler that tracks each connected user.
            It triggers when a user joins or leaves a voice channel."""
            if before.channel is None and after.channel is not None:
                if not discord.utils.get(member.roles, name=self.excluded_role_id):
                    self.connected_users.add(member.id)

            elif before.channel is not None and after.channel is None:
                if not discord.utils.get(member.roles, name=self.excluded_role_id):
                    self.connected_users.remove(member.id)