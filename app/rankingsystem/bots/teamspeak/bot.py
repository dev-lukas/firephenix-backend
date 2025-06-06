import time
import ts3
from app.utils.database import DatabaseManager
from app.utils.logger import RankingLogger
from app.config import Config
from app.rankingsystem.bots.teamspeak.connection import ConnectionManager
from app.rankingsystem.bots.teamspeak.client_manager import ClientManager
from app.rankingsystem.bots.teamspeak.rank_manager import RankManager
from app.rankingsystem.bots.teamspeak.channel_manager import ChannelManager

logging = RankingLogger(__name__).get_logger()

class TeamspeakBot:
    """
    A TeamSpeak bot implementation using the Singleton pattern for managing
    user connections, ranks, and time tracking.
    """
    _instance = None
    VALIDATION_INTERVAL = 300  # Validate every 5 minutes

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TeamspeakBot, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, 'initialized'):
            return

        self.initialized = True
        self.running = False
        self.last_validation = 0
        self.database = DatabaseManager()
        self.connection_manager = ConnectionManager(Config)
        self.rank_manager = RankManager(Config, self.database, self.connection_manager)
        self.client_manager = ClientManager(Config, self.rank_manager)
        self.channel_manager = ChannelManager(Config, self.connection_manager)

    def run(self):
        """Main bot execution loop with reconnection handling"""
        self.running = True
        while self.running:
            try:
                self._run_connection_loop()
            except Exception:
                logging.error(f"Bot could not connect to TeamSpeak server. Retrying in {self.connection_manager.reconnect_delay} seconds.")
                self.client_manager.connected_users.clear()
                self.client_manager.client_uid_map.clear()
                self.client_manager.client_name_map.clear()
                time.sleep(self.connection_manager.reconnect_delay)

    def _run_connection_loop(self):
        """Handle main connection loop"""
        with self.connection_manager.connect() as ts3conn:
            if not ts3conn:
                return
                
            self.client_manager.handle_initial_clients(ts3conn)
            
            for uid in self.client_manager.connected_users:
                try:
                    self.rank_manager.check_user_roles(uid, ts3conn)
                except Exception as e:
                    logging.error(f"Error checking roles for {uid}: {e}")
            
            while self.running:
                try:
                    self.connection_manager.send_keepalive(ts3conn)
                    
                    # Periodic validation to ensure our user tracking is accurate
                    current_time = time.time()
                    if current_time - self.last_validation > self.VALIDATION_INTERVAL:
                        logging.debug("Performing periodic user validation")
                        self.client_manager.validate_connected_users(ts3conn)
                        self.last_validation = current_time
                    
                    event = self.connection_manager.wait_for_event(ts3conn)
                    if event:
                        self._handle_event(event, ts3conn)
                        
                except ts3.query.TS3QueryError as e:
                    logging.error(f"TS3 Query error in main loop: {e}")
                    break  # Exit the loop to trigger reconnection
                except Exception as e:
                    logging.error(f"Unexpected error in main loop: {e}")
                    break  # Exit the loop to trigger reconnection

    def _handle_event(self, event, ts3conn):
        """Process TeamSpeak server events"""
        try:
            reasonid = event.get("reasonid")
            
            # Handle connection events
            if reasonid == "0":  # Client connected
                uid = self.client_manager.handle_client_connect(event, ts3conn)
                if uid:
                    self.rank_manager.check_user_roles(uid, ts3conn)
            
            # Handle all disconnect events
            elif reasonid in ["8", "3", "5", "6", "10", "11"]:
                # 8 = quit, 3 = connection lost, 5 = kicked, 6 = banned, 10 = server stopped, 11 = server left
                uid = self.client_manager.handle_client_disconnect(event)
                if uid:
                    logging.debug(f"User disconnected with reason {reasonid}: {uid}")
            
            # Log unhandled events for debugging
            else:
                logging.debug(f"Unhandled event with reasonid {reasonid}: {event}")
                
        except Exception as e:
            logging.error(f"Error handling event: {e}")
            logging.debug(f"Event data: {event}")

    def get_online_users(self):
        """Return list of currently connected users"""
        return self.client_manager.get_online_users()
    
    def create_owned_channel(self, user_id, channel_name):
        """Create a new owned channel for the user"""
        return self.channel_manager.create_owned_channel(user_id, channel_name)

    def send_verification(self, user_id, code):
        """Send verification code to TeamSpeak user"""
        return self.channel_manager.send_verification(user_id, code)

    def set_ranks(self, client_id, level=None, division=None):
        """Update user ranks in the TeamSpeak server"""
        return self.rank_manager.set_ranks(client_id, level, division)
    
    def check_ranks(self, user_id):
        """Check if user has the correct rank and/or division roles and update if necessary"""
        with self.connection_manager.connect() as ts3conn:
            return self.rank_manager.check_user_roles(user_id, ts3conn)

    def set_server_group(self, client_id, group_id):
        """Set a server group for a user"""
        return self.rank_manager.set_server_group(client_id, group_id)
    
    def remove_server_group(self, client_id, group_id):
        """Remove a server group from a user"""
        return self.rank_manager.remove_server_group(client_id, group_id)
    
    def move_channel_apex(self, channel_id):
        """Move a channel to a new location"""
        return self.channel_manager.move_channel_apex(channel_id)

    def force_user_validation(self):
        """Manually trigger user validation - useful for testing or when inconsistencies are detected"""
        try:
            with self.connection_manager.connect() as ts3conn:
                if ts3conn:
                    logging.info("Forcing user validation")
                    self.client_manager.validate_connected_users(ts3conn)
                    self.last_validation = time.time()
                    return True
                else:
                    logging.error("Could not connect to TeamSpeak server for validation")
                    return False
        except Exception as e:
            logging.error(f"Error during forced validation: {e}")
            return False

    def stop(self):
        """Gracefully stop the bot"""
        self.running = False
