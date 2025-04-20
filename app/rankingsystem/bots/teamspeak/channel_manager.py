import ts3
from app.config import Config
from app.utils.logger import RankingLogger
from app.utils.security import generate_verification_code

logging = RankingLogger(__name__).get_logger()

class ChannelManager:
    """Manages TeamSpeak channel operations"""
    
    def __init__(self, config, connection_manager):
        self.config = config
        self.connection_manager = connection_manager
    
    def create_owned_channel(self, user_id, channel_name):
        """Creates a new owned channel for the user"""
        try:
            with self.connection_manager.connect() as ts3conn:
                channel = ts3conn.exec_(
                    "channelcreate",
                    channel_name=channel_name,
                    cpid=self.config.TS3_PARENT_CHANNEL,
                    channel_flag_permanent=1,
                    channel_codec=4,
                    channel_codec_quality=10
                )
                cid = channel[0]["cid"]
                
                db_info = ts3conn.exec_("clientgetdbidfromuid", cluid=user_id)[0]
                cldbid = db_info["cldbid"]

                ts3conn.exec_("setclientchannelgroup", 
                            cgid=self.config.TS3_OWNER_GROUP_ID,
                            cldbid=cldbid,
                            cid=cid)
                return cid
        except ts3.query.TS3QueryError as e:
            if "channel name is already in use" in str(e).lower():
                try:
                    number = generate_verification_code()
                    cid = self.create_owned_channel(user_id, f"{channel_name} ({number})")
                    return cid
                except Exception as e:
                    logging.error(f"Error creating owned channel: {e}")
                    return None
            logging.error(f"Error creating owned channel: {e}")
            return None
        
    def move_channel_apex(self, channel_id):
        """Moves a channel to a new location"""
        try:
            with self.connection_manager.connect() as ts3conn:
                ts3conn.exec_("channelmove", cid=channel_id, cpid=Config.TS3_APEX_PARENT_CHANNEL)
                return True
        except ts3.query.TS3QueryError as e:
            logging.error(f"Error moving channel: {e}")
            return False
    
    def send_verification(self, user_id, code):
        """Send verification code to TeamSpeak user"""
        try:
            with self.connection_manager.connect() as ts3conn:
                db_info = ts3conn.exec_("clientgetdbidfromuid", cluid=user_id)[0]
                cldbid = db_info.get("cldbid")
                
                clients = ts3conn.exec_("clientlist")
                for client in clients:
                    if client.get("client_database_id") == cldbid:
                        ts3conn.exec_("sendtextmessage", 
                                    targetmode=1, 
                                    target=client["clid"], 
                                    msg=f"Dein Verifikations-Code lautet: {code}")
                        return True
                return False
        except ts3.query.TS3QueryError as e:
            logging.error(f"Error sending verification message: {e}")
            return False
