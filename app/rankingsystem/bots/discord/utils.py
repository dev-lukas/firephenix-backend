import discord
from app.utils.logger import RankingLogger
from app.config import Config

logging = RankingLogger(__name__).get_logger()

async def set_ranks(bot, user_id, level: int = None, division: int = None):
    """
    Set Discord role(s) for a user based on their level and/or division.
    
    Args:
        bot: Discord bot instance
        user_id: Discord user ID
        level: User's level (optional)
        division: User's division (optional)
    
    Returns:
        bool: True if successful, None if an error occurred
    """            
    guild = bot.get_guild(Config.DISCORD_GUILD_ID)
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
            logging.debug(f"Removing roles: {level_roles_to_remove}")
            if level_roles_to_remove:
                await member.remove_roles(*level_roles_to_remove)
                
            level_role = discord.utils.get(guild.roles, id=Config.DISCORD_LEVEL_MAP.get(level))
            if level_role:
                await member.add_roles(level_role)
                logging.debug(f"User {user_id} updated to level {level}")
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
                logging.debug(f"User {user_id} updated to division {division}")
            else:
                logging.error(f"Could not find division role for division {division}")
        
        return True
        
    except discord.Forbidden:
        logging.error(f"Bot lacks permission to modify roles for user {user_id}")
        return None
    except Exception as e:
        logging.error(f"Error setting roles for user {user_id}: {e}")
        return None

async def send_verification(bot, user_id, code) -> bool:
    """Send verification code to Discord user"""
    try:
        user = await bot.fetch_user(user_id)
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

async def create_owned_channel(bot, user_id: int, channel_name: str) -> int:
    """
    Creates a permanent voice channel with owner permissions under configured parent
    """
    try:
        guild = bot.get_guild(Config.DISCORD_GUILD_ID)
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
