import asyncio
import discord
from discord.ext import commands
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

        try:
            rank, division = self.database.get_user_roles(user_id, "discord")
        except DatabaseConnectionError:
            logging.error(
                "Database connection error on check_user_roles - aborting checking user roles"
            )
            return None

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
        """Scan all voice channels and add connected users to the set"""
        await self.bot.wait_until_ready()

        for voice_channel in self.guild.voice_channels:
            for member in voice_channel.members:
                if not member.bot:  # Ignore bots
                    self.connected_users.add(member.id)
                    self.user_name_map[member.id] = member.display_name

        logging.debug(
            f"Initial voice channel scan complete. Found {len(self.connected_users)} users."
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
