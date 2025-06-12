import asyncio
import discord
from discord.ext import commands, tasks
from app.utils.database import DatabaseManager, DatabaseConnectionError
from app.utils.logger import RankingLogger
from app.config import Config
from app.rankingsystem.bots.discord.utils import set_ranks
from app.rankingsystem.bots.discord.aichat import handle_chat_message

logging = RankingLogger(__name__).get_logger()


class ClientManager(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.guild = self.bot.get_guild(Config.DISCORD_GUILD_ID)
        self.database = DatabaseManager()
        self.connected_users = set()
        self.user_name_map = {}
        self.scan_voice_channels_task.start()
        self.check_default_roles_task.start()
        logging.info("Discord Bot ClientManager initialized.")

    async def cog_load(self):
        await self.bot.wait_until_ready()
        self.guild = self.bot.get_guild(Config.DISCORD_GUILD_ID) 
        if not self.guild:
            logging.error("Discord guild not found after cog load. User tracking may be impaired.")
            return
        await self.scan_voice_channels() 
        logging.info("ClientManager cog loaded and initial voice channel scan complete.")

    async def cog_unload(self):
        self.scan_voice_channels_task.cancel()
        self.check_default_roles_task.cancel()
        logging.info("Discord Bot ClientManager unloaded, tasks cancelled, and user arrays flushed.")

    @tasks.loop(minutes=5)  
    async def scan_voice_channels_task(self):
        try:
            await self.scan_voice_channels()
        except Exception as e:
            logging.error(f"Error in periodic scan_voice_channels_task: {e}")

    @tasks.loop(hours=1)  
    async def check_default_roles_task(self):
        try:
            await self.check_default_roles()
        except Exception as e:
            logging.error(f"Error in periodic check_default_roles_task: {e}")

    @scan_voice_channels_task.before_loop
    async def before_scan_voice_channels_task(self):
        await self.bot.wait_until_ready()
        logging.info("Starting periodic voice channel scan task.")

    @check_default_roles_task.before_loop
    async def before_check_default_roles_task(self):
        await self.bot.wait_until_ready()
        logging.info("Starting periodic default roles check task.")

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

        try:
            rank, division = self.database.get_user_roles(user_id, "discord")
        except DatabaseConnectionError:
            logging.error(
                "Database connection error on check_user_roles - aborting checking user roles"
            )
            return None
        
        if rank is None or division is None:
            if self.database.has_time_entry(user_id, "discord"):
                logging.warning(f"User {user_id} has no rank or division set in the database, but has time entries. Seems like the database failed to fetch. Skipping.")
                return None
            else:
                logging.info(f"User {user_id} has no rank or time set in the database yet; welcome new user!")
                rank = 1
                division = 1

        if check_type in ["rank", "both"]:
            correct_rank = False
            rank_roles_count = 0
            for role in member.roles:
                if role.id in Config.DISCORD_LEVEL_MAP.values():
                    rank_roles_count += 1
                    if role.id == Config.DISCORD_LEVEL_MAP.get(rank):
                        correct_rank = True

            if not correct_rank or rank_roles_count > 1:
                logging.debug(
                    f"Setting rank for user {user_id} to {rank} (had {rank_roles_count} rank roles)"
                )
                await set_ranks(self.bot, user_id, level=rank)

        if check_type in ["division", "both"]:
            correct_division = False
            division_roles_count = 0
            for role in member.roles:
                if role.id in Config.DISCORD_DIVISION_MAP.values():
                    division_roles_count += 1
                    if role.id == Config.DISCORD_DIVISION_MAP.get(division):
                        correct_division = True

            if not correct_division or division_roles_count > 1:
                logging.debug(
                    f"Setting division for user {user_id} to {division} (had {division_roles_count} division roles)"
                )
                await set_ranks(self.bot, user_id, division=division)

    async def scan_voice_channels(self):
        """Scan all voice channels and add connected users to the set.
        This method also removes users who are in the set but no longer in a voice channel."""
        await self.bot.wait_until_ready()
        if not self.guild:
            logging.warning("Guild not available for voice channel scan.")
            return

        current_voice_users = set()
        for voice_channel in self.guild.voice_channels:
            for member in voice_channel.members:
                if not member.bot:  # Ignore bots
                    current_voice_users.add(member.id)
                    if member.id not in self.user_name_map or self.user_name_map[member.id] != member.display_name:
                        self.user_name_map[member.id] = member.display_name
                        logging.debug(f"Updated username for {member.id}: {member.display_name}")

        users_to_add = current_voice_users - self.connected_users
        for user_id in users_to_add:
            self.connected_users.add(user_id)
            logging.debug(f"User {user_id} added during voice channel scan.")

        users_to_remove = self.connected_users - current_voice_users
        for user_id in users_to_remove:
            self.connected_users.discard(user_id)
            self.user_name_map.pop(user_id, None)
            logging.debug(f"User {user_id} removed during voice channel scan (no longer in voice).")

        if users_to_add or users_to_remove:
            logging.info(
                f"Voice channel scan complete. Added: {len(users_to_add)}, Removed: {len(users_to_remove)}. Total connected: {len(self.connected_users)}"
            )
        else:
            logging.debug(
                f"Periodic voice channel scan complete. No changes. Total connected: {len(self.connected_users)}"
            )

    async def check_default_roles(self):
        """Check all members for rank roles and gives user the base role if none present"""
        await self.bot.wait_until_ready()
        try:
            default_level_role = discord.utils.get(
                self.guild.roles, id=Config.DISCORD_LEVEL_MAP[1]
            )
            default_division_role = discord.utils.get(
                self.guild.roles, id=Config.DISCORD_DIVISION_MAP[1]
            )
            async for member in self.guild.fetch_members():
                if (
                    not discord.utils.get(
                        member.roles, id=Config.DISCORD_EXCLUDED_ROLE_ID
                    )
                    and not member.bot
                ):
                    has_rank = False
                    has_division = False
                    for role in member.roles:
                        if role.id in Config.DISCORD_LEVEL_MAP.values():
                            has_rank = True
                            break
                    
                    for role in member.roles:
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
    async def on_ready(self):
        logging.info(f'{self.bot.user} has connected/reconnected to Discord!')
        if not self.guild:
            self.guild = self.bot.get_guild(Config.DISCORD_GUILD_ID)
        if not self.guild:
            logging.error("Discord guild not found on_ready. User tracking may be impaired.")
            return
        if not self.scan_voice_channels_task.is_running():
            self.scan_voice_channels_task.start()
        if not self.check_default_roles_task.is_running():
            self.check_default_roles_task.start()

    @commands.Cog.listener()
    async def on_resume(self):
        logging.info("Bot has resumed session. Re-scanning voice channels.")
        await self.bot.wait_until_ready() 
        if not self.guild:
            self.guild = self.bot.get_guild(Config.DISCORD_GUILD_ID)
        if not self.guild:
            logging.error("Discord guild not found on_resume. User tracking may be impaired.")
            return
        try:
            await self.scan_voice_channels()
        except Exception as e:
            logging.error(f"Error during on_resume voice channel scan: {e}")

    @commands.Cog.listener()
    async def on_voice_state_update(self, member, before, after):
        """on_voice_state_update event handler that tracks each connected user.
        It triggers when a user joins or leaves a voice channel."""
        try:
            if before.channel is None and after.channel is not None:
                if not discord.utils.get(
                    member.roles, id=Config.DISCORD_EXCLUDED_ROLE_ID
                ):
                    self.connected_users.add(member.id)
                    self.user_name_map[member.id] = member.display_name
                    await self.check_user_roles(member.id)

            elif before.channel is not None and after.channel is None:
                if not discord.utils.get(
                    member.roles, id=Config.DISCORD_EXCLUDED_ROLE_ID
                ):
                    self.connected_users.remove(member.id)
                    self.user_name_map.pop(member.id, None)

            elif before.channel and after.channel:
                if discord.utils.get(member.roles, id=Config.DISCORD_MOVE_BLOCK_ID):
                    await asyncio.sleep(2)
                    entry = await self.find_move_log(member.id)
                    logging.debug(f"Move log entry: {entry}")
                    if (
                        entry
                        and not discord.utils.get(
                            entry.user.roles, id=Config.DISCORD_ADMIN_ROLE_ID
                        )
                        and not discord.utils.get(
                            entry.user.roles, id=Config.DISCORD_MODERATOR_ROLE_ID
                        )
                        and not entry.user.bot
                    ):
                        await member.move_to(
                            before.channel, reason="Movement shield activation"
                        )
        except Exception as e:
            logging.error(f"Error updating voice state: {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member):
        try:
            if not member.bot:
                default_role = discord.utils.get(
                    member.guild.roles, id=Config.DISCORD_LEVEL_MAP[1]
                )
                await member.add_roles(default_role)
        except Exception as e:
            logging.error(f"Error adding default role to new member: {e}")

    async def find_move_log(self, member_id):
        """Find the move log channel for the member"""
        async for entry in self.guild.audit_logs(
            limit=5, action=discord.AuditLogAction.member_move
        ):
            if entry.target and entry.target.id == member_id:
                return entry
        return None
    
    @commands.Cog.listener()
    async def on_message(self, message):
        """Listen for chat messages in a specific channel and respond using OpenRouter."""
        if (
            message.channel.id == Config.DISCORD_CHAT_CHANNEL
            and not message.author.bot
        ):
            response = await handle_chat_message(message)
            if response:
                await message.channel.send(response)
            else:
                await message.channel.send(stickers=[discord.Object(id=Config.DISCORD_EMBER_STICKER)])
