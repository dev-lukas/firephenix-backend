import ts3
import time
from app.utils.database import DatabaseManager
from app.utils.logger import RankingLogger
from app.config import Config
from app.utils.security import generate_verification_code

logging = RankingLogger(__name__).get_logger()

class TeamspeakBot:
    """
    A TeamSpeak bot implementation using the Singleton pattern for managing
    user connections, ranks, and time tracking.
    """
    _instance = None
    INITIAL_RECONNECT_DELAY = 30
    MAX_RECONNECT_DELAY = 300
    BANNED_WAIT_TIME = 300
    KEEPALIVE_TIMEOUT = 240
    UPDATE_INTERVAL = 1

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TeamspeakBot, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, 'initialized'):
            return

        self.initialized = True
        self._init_config()
        self._init_state()

    def _init_config(self):
        """Initialize configuration parameters"""
        self.host = Config.TS3_HOST
        self.port = int(Config.TS3_PORT)
        self.username = Config.TS3_USERNAME
        self.password = Config.TS3_PASSWORD
        self.server_id = int(Config.TS3_SERVER_ID)
        self.excluded_role_id = Config.TS3_EXCLUDED_ROLE_ID

    def _init_state(self):
        """Initialize state variables"""
        self.database = DatabaseManager()
        self.connected_users = set()
        self.client_uid_map = {}
        self.client_dbid_map = {}
        self.client_name_map = {}
        self.running = False
        self.reconnect_delay = self.INITIAL_RECONNECT_DELAY

    def connect_to_server(self):
        """Establish connection to TeamSpeak server"""
        ts3conn = ts3.query.TS3ServerConnection(
            f"telnet://{self.username}:{self.password}@{self.host}:{self.port}"
        )
        ts3conn.exec_("use", sid=self.server_id)
        ts3conn.exec_("servernotifyregister", event="server")
        return ts3conn

    def get_client_data(self, client_id, ts3conn):
        """
        Retrieve client information from TeamSpeak server
        
        Args:
            client_id: Client ID to query
            ts3conn: Active TeamSpeak connection
            
        Returns:
            tuple: (client_unique_identifier, client_nickname) or None if error
        """
        try:
            client_info = ts3conn.exec_("clientinfo", clid=client_id)[0]
            return (
                client_info["client_unique_identifier"],
                client_info["client_nickname"]
            )
        except ts3.query.TS3QueryError as err:
            logging.error(f"Error getting client info: {err}")
            return None

    def handle_initial_clients(self, ts3conn):
        """Process existing clients after connection"""
        self.connected_users.clear()
        self.client_uid_map.clear()
        self.client_name_map.clear()
        
        clients = ts3conn.exec_("clientlist")
        for client in clients:
            if client.get("client_type") != "0":
                continue
            
            client_info = ts3conn.exec_("clientinfo", clid=client["clid"])[0]
            cldbid = client_info["client_database_id"]
            groups_info = ts3conn.exec_("servergroupsbyclientid", 
                                      cldbid=cldbid)
            group_ids = [int(group.get("sgid", 0)) for group in groups_info]
            
            if self.excluded_role_id in group_ids:
                continue

            client_data = self.get_client_data(client["clid"], ts3conn)
            if client_data:
                uid, name = client_data
                self.connected_users.add(uid)
                self.client_uid_map[client["clid"]] = uid
                self.client_name_map[uid] = name

    def check_user_roles(self, uid, ts3conn):
        """Check if user rank needs to be updated
        Args:
            clid: Client ID
            uid: Unique identifier
        """
        try:
            logging.info("Checking rank for user: %s", uid)
            db_info = ts3conn.exec_("clientgetdbidfromuid", cluid=uid)[0]
            cldbid = db_info.get("cldbid")
            rank, division = self.database.get_user_roles(uid, "teamspeak")
            groups_info = ts3conn.exec_("servergroupsbyclientid", cldbid=cldbid)
            group_ids = [int(group.get("sgid", 0)) for group in groups_info]
            if Config.TEAMSPEAK_LEVEL_MAP[rank] not in group_ids:
                logging.debug(f"Rank {rank} update required for user: {uid}")
                self.set_ranks(uid, level=rank)
            if Config.TEAMSPEAK_DIVISION_MAP[division] not in group_ids:
                logging.debug(f"Division {division} update required for user: {uid}")
                self.set_ranks(uid, division=division)

        except Exception:
            logging.error(f"Error getting server groups for client {uid}")

    def set_ranks(self, client_id, level=None, division=None):
        """
        Update user ranks in the TeamSpeak server
        
        Args:
            client_id: TeamSpeak client unique ID
            level: New level rank (optional)
            division: New division rank (optional)
        """
        if level is None and division is None:
            logging.warning(f"No rank type specified for user {client_id}")
            return None
            
        try:
            with self.connect_to_server() as ts3conn:
                db_info = ts3conn.exec_("clientgetdbidfromuid", cluid=client_id)[0]
                cldbid = db_info.get("cldbid")

                if level is not None:
                    self._update_server_group(
                        ts3conn, 
                        cldbid, 
                        Config.TEAMSPEAK_LEVEL_MAP, 
                        level, 
                        "level",
                        client_id
                    )
                    
                if division is not None:
                    self._update_server_group(
                        ts3conn, 
                        cldbid, 
                        Config.TEAMSPEAK_DIVISION_MAP, 
                        division,
                        "division",
                        client_id
                    )
                    
                return True
                    
        except Exception as e:
            logging.error(f"Rank update failed for user {client_id}: {e}")
            return None

    def _update_server_group(self, ts3conn, cldbid, group_map, new_value, rank_type, client_id):
        """
        Update a specific server group type for a user
        
        Args:
            ts3conn: Active TeamSpeak connection
            cldbid: Client database ID
            group_map: Config map (TEAMSPEAK_LEVEL_MAP or TEAMSPEAK_DIVISION_MAP)
            new_value: New rank value
            rank_type: Type of rank ("level" or "division")
            client_id: Original client ID for logging
        """
        try:
            groups_info = ts3conn.exec_("servergroupsbyclientid", cldbid=cldbid)
            
            for group in groups_info:
                group_id = int(group.get("sgid", 0))
                if group_id in group_map.values():
                    ts3conn.exec_("servergroupdelclient", 
                                sgid=group_id, 
                                cldbid=cldbid)
            
            if new_value in group_map:
                new_group_id = group_map[new_value]
                ts3conn.exec_("servergroupaddclient", 
                            sgid=new_group_id, 
                            cldbid=cldbid)
                
                logging.info(f"Updated {rank_type} for user {client_id} to {new_value}")
            else:
                logging.error(f"Invalid {rank_type} value: {new_value}")
                
        except ts3.query.TS3QueryError as err:
            logging.error(f"TS3 Query Error updating {rank_type}: {err}")

    def handle_event(self, event, ts3conn):
        """
        Process TeamSpeak server events
        
        Args:
            event: TeamSpeak event data
            ts3conn: Active TeamSpeak connection
        """
        logging.debug(f"Event: {event['reasonid']}")
        
        if event["reasonid"] == "0":  # Client connected
            self._handle_client_connect(event, ts3conn)
        elif event["reasonid"] == "8":  # Client disconnected
            self._handle_client_disconnect(event)

    def _handle_client_connect(self, event, ts3conn):
        """Handle client connection event"""
        if event.get("client_type") != "0":
            return

        client_info = ts3conn.exec_("clientinfo", clid=event["clid"])[0]
        cldbid = client_info["client_database_id"]
        groups_info = ts3conn.exec_("servergroupsbyclientid", cldbid=cldbid)
        group_ids = [int(group.get("sgid", 0)) for group in groups_info]
        
        if self.excluded_role_id in group_ids:
            return

        logging.debug("Client connected: %s", event["clid"])
        client_data = self.get_client_data(event["clid"], ts3conn)
        if client_data:
            uid, name = client_data
            self.connected_users.add(uid)
            self.client_uid_map[event["clid"]] = uid
            self.client_name_map[uid] = name
            logging.debug("User connected: %s", uid)
            self.check_user_roles(uid, ts3conn)

    def _handle_client_disconnect(self, event):
        """Handle client disconnection event"""
        uid = self.client_uid_map.pop(event["clid"], None)
        if uid in self.connected_users:
            self.connected_users.remove(uid)
            self.client_name_map.pop(uid, None)

    def run(self):
        """Main bot execution loop with reconnection handling"""
        self.running = True
        while self.running:
            try:
                self._run_connection_loop()
            except Exception as e:
                self._handle_connection_error(e)

    def _run_connection_loop(self):
        """Handle main connection loop"""
        with self.connect_to_server() as ts3conn:
            logging.info("Successfully connected to TeamSpeak server")
            self.reconnect_delay = self.INITIAL_RECONNECT_DELAY
            self.handle_initial_clients(ts3conn)
            
            while self.running:
                ts3conn.send_keepalive()
                try:
                    event = ts3conn.wait_for_event(timeout=self.KEEPALIVE_TIMEOUT)
                    if event:
                        self.handle_event(event[0], ts3conn)
                except ts3.query.TS3TimeoutError:
                    continue

    def _handle_connection_error(self, error):
        """Handle connection errors and implement backoff strategy"""
        if isinstance(error, ts3.query.TS3QueryError):
            logging.error(f"TS3 Query Error: {error}")
            if "banned" in str(error).lower():
                logging.warning("Bot is banned, waiting longer before reconnect")
                time.sleep(self.BANNED_WAIT_TIME)
                return

        logging.error(f"Unexpected error: {error}")
        time.sleep(self.reconnect_delay)
        self.reconnect_delay = min(self.MAX_RECONNECT_DELAY, 
                                 self.reconnect_delay * 2)

    def get_online_users(self):
        """Return list of currently connected users"""
        return list(self.connected_users), self.client_name_map
    
    def create_owned_channel(self, user_id: str, channel_name: str) -> int:
        """create_owned_channel Creates a new owned channel for the user"""
        try:
            with self.connect_to_server() as ts3conn:
                channel = ts3conn.exec_(
                    "channelcreate",
                    channel_name=channel_name,
                    cpid=Config.TS3_PARENT_CHANNEL,
                    channel_flag_permanent=1,
                    channel_codec=4,
                    channel_codec_quality=10
                )
                cid = channel[0]["cid"]
                
                # Get client DBID from UID
                db_info = ts3conn.exec_("clientgetdbidfromuid", cluid=user_id)[0]
                cldbid = db_info["cldbid"]

                # Set channel permissions
                ts3conn.exec_("setclientchannelgroup", 
                            cgid=Config.TS3_OWNER_GROUP_ID,
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

    def send_verification(self, user_id, code):
        """Send verification code to TeamSpeak user"""
        try:
            with self.connect_to_server() as ts3conn:
                db_info = ts3conn.exec_("clientgetdbidfromuid", cluid=user_id)[0]
                cldbid = db_info.get("cldbid")
                
                # Get online clients with matching DBID
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

    def stop(self):
        """Gracefully stop the bot"""
        self.running = False
