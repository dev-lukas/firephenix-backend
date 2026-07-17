import atsq
from atsq import TargetMode
from app.config import Config
from app.utils.logger import RankingLogger
from app.utils.security import generate_verification_code

logging = RankingLogger(__name__).get_logger()

class ChannelManager:
    """Manages TeamSpeak channel operations"""

    def __init__(self, config, client: atsq.Client):
        self.config = config
        self.client = client

    async def create_owned_channel(self, user_id, channel_name):
        """Creates a new owned channel for the user"""
        try:
            cid = await self.client.channel_create(
                channel_name,
                cpid=self.config.TS3_PARENT_CHANNEL,
                channel_flag_permanent=1,
                channel_codec=4,
                channel_codec_quality=10
            )
            cldbid = await self.client.client_dbid_from_uid(user_id)

            await self.client.set_client_channel_group(
                cgid=self.config.TS3_OWNER_GROUP_ID,
                cid=cid,
                cldbid=cldbid
            )

            permissions = [
                ("i_channel_needed_modify_power", 75),
                ("i_channel_needed_delete_power", 75),
                ("b_channel_modify_name", 1),
                ("b_channel_modify_topic", 1),
                ("b_channel_modify_description", 1),
                ("b_channel_modify_password", 1),
                ("b_channel_modify_codec", 1),
                ("b_channel_modify_codec_quality", 1),
                ("b_channel_modify_codec_latency_factor", 1),
                ("b_channel_modify_needed_talk_power", 1),
                ("b_channel_modify_maxclients", 1),
                ("b_channel_modify_make_temporary", 0),
                ("b_channel_modify_maxfamilyclients", 0),
            ]

            for perm_name, perm_value in permissions:
                try:
                    await self.client.channel_add_perm(cid, perm_name, perm_value)
                except atsq.QueryError as perm_error:
                    logging.debug(f"Could not set channel permission {perm_name}: {perm_error}")

            client_permissions = [
                ("i_channel_needed_modify_power", 0),
                ("i_channel_needed_delete_power", 0),
                ("i_channel_modify_power", 76),
                ("b_channel_modify_make_temporary", 0),
                ("b_channel_modify_maxfamilyclients", 0),
            ]

            for perm_name, perm_value in client_permissions:
                try:
                    await self.client.channel_client_add_perm(cid, cldbid, perm_name, perm_value)
                except atsq.QueryError as perm_error:
                    logging.debug(f"Could not set client permission {perm_name}: {perm_error}")

            return cid
        except atsq.QueryError as e:
            if "channel name is already in use" in str(e).lower():
                try:
                    number = generate_verification_code()
                    cid = await self.create_owned_channel(user_id, f"{channel_name} ({number})")
                    return cid
                except Exception as e:
                    logging.error(f"Error creating owned channel: {e}")
                    return None
            logging.error(f"Error creating owned channel: {e}")
            return None

    async def move_channel_apex(self, channel_id):
        """Moves a channel to a new location"""
        try:
            await self.client.channel_move(cid=channel_id, cpid=Config.TS3_APEX_PARENT_CHANNEL)
            return True
        except atsq.QueryError as e:
            logging.error(f"Error moving channel: {e}")
            return False

    async def send_verification(self, user_id, code):
        """Send verification code to TeamSpeak user"""
        try:
            cldbid = await self.client.client_dbid_from_uid(user_id)

            clients = await self.client.client_list()
            for client in clients:
                if client.get("client_database_id") == cldbid:
                    await self.client.send_text_message(
                        target=client["clid"],
                        msg=f"Dein Verifikations-Code lautet: {code}",
                        targetmode=TargetMode.CLIENT
                    )
                    return True
            return False
        except atsq.QueryError as e:
            logging.error(f"Error sending verification message: {e}")
            return False
