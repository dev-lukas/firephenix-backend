import asyncio
import time
import discord
from datetime import datetime
from discord.ext import commands
from app.utils.database import DatabaseManager
from app.utils.logger import RankingLogger
from app.config import Config

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
            self.time_tracker = self.TimeTracker(self.bot)
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
            return list(self.time_tracker.connected_users)
        return []

    class TimeTracker(commands.Cog):

        def __init__(self, bot: commands.Bot):
            self.excluded_role_id = Config.DISCORD_EXCLUDED_ROLE_ID

            self.database = DatabaseManager()
            self.bot = bot
            self.guild = self.bot.get_guild(Config.DISCORD_GUILD_ID)
            self.connected_users = set()
            self.bg_task = self.bot.loop.create_task(self.scan_voice_channels())
            self.bg_task = self.bot.loop.create_task(self.check_default_roles())
            self.bg_task = self.bot.loop.create_task(self.update_time())
            self.monitor_task = self.bot.loop.create_task(self.monitor_background_tasks())
            logging.info("Discord Bot started successfully.")

        async def set_ranks(self, user_id, level):
            member = await self.guild.fetch_member(user_id)
            for role in member.roles:
                if role.id in Config.DISCORD_LEVEL_MAP.values():
                    await member.remove_roles(role)
            rankup = discord.utils.get(member.guild.roles, id=Config.DISCORD_LEVEL_MAP[level])
            await member.add_roles(rankup)
            logging.info(f"User {user_id} ranked up to level {level}")

        async def check_rank(self, user_id):
            """Check if user has the correct rank and update if necessary"""
            rank = self.database.get_user_rank(user_id, "discord")
            member = await self.guild.fetch_member(user_id)
            correct_rank = False
            for role in member.roles:
                if role.id == Config.DISCORD_LEVEL_MAP[rank]:
                    correct_rank = True
                    break
            if not correct_rank:
                await self.set_ranks(user_id, rank)

        async def update_time(self):
            """Background task that runs every minute to update the time spent in voice chat for each user.
            """
            await self.bot.wait_until_ready()
            while not self.bot.is_closed():
                try:
                    if datetime.now().minute == 0:
                        self.database.log_usage_stats(
                            user_count=len(self.connected_users),
                            platform='discord'
                        )
                    if self.connected_users:
                        self.database.update_times(self.connected_users, "discord")
                        upranked_user = self.database.update_ranks(self.connected_users, "discord")
                        for user_id, level in upranked_user:
                            await self.set_ranks(user_id, level)
                            
                except Exception as e:
                    logging.error(f"Error updating time: {e}")
                await asyncio.sleep(60)

        async def scan_voice_channels(self):
            """Scan all voice channels and add connected users to the set"""
            await self.bot.wait_until_ready()
            
            for voice_channel in self.guild.voice_channels:
                for member in voice_channel.members:
                    if not member.bot:  # Ignore bots
                        self.connected_users.add(member.id)
                        self.database.update_user_name(member.id, member.display_name, "discord")
              
            logging.info(f"Initial voice channel scan complete. Found {len(self.connected_users)} users.")

        async def check_default_roles(self):
            """Check all members for rank roles and gives user the base role if none present"""
            await self.bot.wait_until_ready()
            try:
                default_role = discord.utils.get(
                    self.guild.roles, 
                    id=Config.DISCORD_LEVEL_MAP[1]
                )
                async for member in self.guild.fetch_members():
                    if not discord.utils.get(member.roles, name=self.excluded_role_id) and not member.bot:
                        has_rank = False
                        for role in member.roles:
                            if role.id in Config.DISCORD_LEVEL_MAP.values():
                                has_rank = True
                                break
                        if not has_rank:
                            await member.add_roles(default_role)
            except Exception as e:
                logging.error(f"Error checking default roles: {e}")

        @commands.Cog.listener()
        async def on_voice_state_update(self, member, before, after):
            """on_voice_state_update event handler that tracks each connected user.
            It triggers when a user joins or leaves a voice channel."""
            try:
                if before.channel is None and after.channel is not None:
                    if not discord.utils.get(member.roles, name=self.excluded_role_id):
                        self.connected_users.add(member.id)
                        self.database.update_user_name(member.id, member.display_name, "discord")
                        self.check_rank(member.id)

                elif before.channel is not None and after.channel is None:
                    if not discord.utils.get(member.roles, name=self.excluded_role_id):
                        self.connected_users.remove(member.id)
            except Exception as e:
                logging.error(f"Error updating voice state: {e}")

        @commands.Cog.listener()
        async def on_member_join(self, member):
            try:
                if not member.bot:
                    default_role = discord.utils.get(
                        member.guild.roles, 
                        id=Config.DISCORD_LEVEL_MAP[1]
                    )
                    await member.add_roles(default_role)
            except Exception as e:
                logging.error(f"Error adding default role to new member: {e}")

        async def monitor_background_tasks(self):
            while not self.bot.is_closed():
                try:
                    if self.bg_task.done():
                        if self.bg_task.exception():
                            logging.error(f"Background task failed: {self.bg_task.exception()}")
                            self.bg_task = self.bot.loop.create_task(self.update_time())
                except Exception as e:
                    logging.error(f"Error in task monitor: {e}")
                await asyncio.sleep(10)