import time
import discord
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
            return list(self.time_tracker.connected_users), self.time_tracker.user_name_map
        return []
    
    async def set_ranks(self, user_id, level: int = None, division: int = None):
        """
        Set Discord role(s) for a user based on their level and/or division.
        
        Args:
            user_id: Discord user ID
            level: User's level (optional)
            division: User's division (optional)
        
        Returns:
            bool: True if successful, None if an error occurred
        """            
        guild = self.bot.get_guild(Config.DISCORD_GUILD_ID)
        if not guild:
            logging.error("Guild not found")
            return None
        
        try:
            member = await guild.fetch_member(user_id)
        except discord.NotFound:
            logging.error(f"User {user_id} not found in guild")
            return None
        except Exception as e:
            logging.error(f"Error fetching member {user_id}: {e}")
            return None
        
        try:
            if level is not None:
                level_roles_to_remove = [
                    role for role in member.roles 
                    if role.id in Config.DISCORD_LEVEL_MAP.values()
                ]
                if level_roles_to_remove:
                    await member.remove_roles(*level_roles_to_remove)
                    
                level_role = discord.utils.get(guild.roles, id=Config.DISCORD_LEVEL_MAP.get(level))
                if level_role:
                    await member.add_roles(level_role)
                    logging.info(f"User {user_id} updated to level {level}")
                else:
                    logging.error(f"Could not find level role for level {level}")
            
            if division is not None:
                division_roles_to_remove = [
                    role for role in member.roles 
                    if role.id in Config.DISCORD_DIVISION_MAP.values()
                ]
                if division_roles_to_remove:
                    await member.remove_roles(*division_roles_to_remove)
                    
                division_role = discord.utils.get(guild.roles, id=Config.DISCORD_DIVISION_MAP.get(division))
                if division_role:
                    await member.add_roles(division_role)
                    logging.info(f"User {user_id} updated to division {division}")
                else:
                    logging.error(f"Could not find division role for division {division}")
            
            return True
            
        except discord.Forbidden:
            logging.error(f"Bot lacks permission to modify roles for user {user_id}")
            return None
        except Exception as e:
            logging.error(f"Error setting roles for user {user_id}: {e}")
            return None

    async def send_verification(self, user_id, code) -> bool:
        """Send verification code to Discord user"""
        try:
            user = await self.bot.fetch_user(user_id)
            await user.send(f"Dein Verifikations-Code lautet: {code}")
            return True
        except discord.Forbidden:
            logging.error(f"No DMs possible for {user_id}")
            return False
        except discord.NotFound:
            logging.error(f"User {user_id} not found")
            return False
        except Exception as e:
            logging.error(f"Error sending verification message: {e}")
            return False
        
    async def create_owned_channel(self, user_id: int, channel_name: str) -> int:
        """
        Creates a permanent voice channel with owner permissions under configured parent
        """
        try:
            guild = self.bot.get_guild(Config.DISCORD_GUILD_ID)
            if not guild:
                logging.error("Guild not found")
                return None

            try:
                member = await guild.fetch_member(user_id)
            except discord.NotFound:
                logging.error(f"User {user_id} not found in guild")
                return None

            parent = guild.get_channel(Config.DISCORD_PARENT_CHANNEL)
            if not parent:
                logging.error(f"Parent channel {Config.DISCORD_PARENT_CHANNEL} not found")
                return None

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    view_channel=True,
                    connect=False
                ),
                member: discord.PermissionOverwrite(
                    connect=True,
                    manage_channels=True,
                    manage_permissions=True,
                    move_members=True
                )
            }

            channel = await guild.create_voice_channel(
                name=channel_name,
                category=parent,
                overwrites=overwrites
            )
            return channel.id
        except Exception as e:
            logging.error(f"Error creating permanent channel: {e}")
            return None

    class TimeTracker(commands.Cog):

        def __init__(self, bot: commands.Bot):
            self.excluded_role_id = Config.DISCORD_EXCLUDED_ROLE_ID
            self.database = DatabaseManager()
            self.bot = bot
            self.guild = self.bot.get_guild(Config.DISCORD_GUILD_ID)
            self.connected_users = set()
            self.user_name_map = {}
            self.bg_task = self.bot.loop.create_task(self.scan_voice_channels())
            self.bg_task = self.bot.loop.create_task(self.check_default_roles())
            logging.info("Discord Bot started successfully.")

        async def check_user_roles(self, user_id, check_type="both"):
            """
            Check if user has the correct rank and/or division roles and update if necessary
            
            Args:
                user_id: Discord user ID
                check_type: What to check - "rank", "division", or "both" (default)
            """
            try:
                member = await self.guild.fetch_member(user_id)
            except discord.NotFound:
                logging.error(f"User {user_id} not found anymore in guild")
                return None
            
            rank, division = self.database.get_user_roles(user_id, "discord")

            if check_type in ["rank", "both"]:
                correct_rank = False
                for role in member.roles:
                    if role.id == Config.DISCORD_LEVEL_MAP.get(rank):
                        correct_rank = True
                        break
                if not correct_rank:
                    await DiscordBot().set_ranks(user_id, level=rank)
            
            if check_type in ["division", "both"]:
                correct_division = False
                for role in member.roles:
                    if role.id == Config.DISCORD_DIVISION_MAP.get(division):
                        correct_division = True
                        break
                if not correct_division:
                    await DiscordBot().set_ranks(user_id, divsion=division)

        async def scan_voice_channels(self):
            """Scan all voice channels and add connected users to the set"""
            await self.bot.wait_until_ready()
            
            for voice_channel in self.guild.voice_channels:
                for member in voice_channel.members:
                    if not member.bot:  # Ignore bots
                        self.connected_users.add(member.id)
                        self.user_name_map[member.id] = member.display_name
              
            logging.info(f"Initial voice channel scan complete. Found {len(self.connected_users)} users.")

        async def check_default_roles(self):
            """Check all members for rank roles and gives user the base role if none present"""
            await self.bot.wait_until_ready()
            try:
                default_level_role = discord.utils.get(
                    self.guild.roles, 
                    id=Config.DISCORD_LEVEL_MAP[1]
                )
                default_division_role = discord.utils.get(
                    self.guild.roles, 
                    id=Config.DISCORD_DIVISION_MAP[1]
                )
                async for member in self.guild.fetch_members():
                    if not discord.utils.get(member.roles, name=self.excluded_role_id) and not member.bot:
                        has_rank = False
                        has_division = False
                        for role in member.roles:
                            if role.id in Config.DISCORD_LEVEL_MAP.values():
                                has_rank = True
                                break
                            if role.id in Config.DISCORD_DIVISION_MAP.values():
                                has_division = True
                                break

                        if not has_rank:
                            await member.add_roles(default_level_role)

                        if not has_division:
                            await member.add_roles(default_division_role)
                                                   
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
                        self.user_name_map[member.id] = member.display_name
                        await self.check_user_roles(member.id)

                elif before.channel is not None and after.channel is None:
                    if not discord.utils.get(member.roles, name=self.excluded_role_id):
                        self.connected_users.remove(member.id)
                        self.user_name_map.pop(member.id, None)
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