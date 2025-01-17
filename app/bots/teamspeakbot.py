import threading
import ts3
import time
from datetime import datetime
from app.utils.database import DatabaseManager
from app.utils.logger import RankingLogger
from app.config import Config

logging = RankingLogger(__name__).get_logger()

class TeamspeakBot:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TeamspeakBot, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, 'initialized'):
            self.initialized = True
            self.host = Config.TS3_HOST
            self.port = int(Config.TS3_PORT)
            self.username = Config.TS3_USERNAME
            self.password = Config.TS3_PASSWORD
            self.server_id = int(Config.TS3_SERVER_ID)
            self.excluded_role_id = Config.TS3_EXCLUDED_ROLE_ID
            self.database = DatabaseManager()
            self.connected_users = set()
            self.client_uid_map = {}
            self.running = False
            self.reconnect_delay = 30  # Start with 30 seconds delay[14]

    def connect_to_server(self):
        """Establish connection to TeamSpeak server"""
        ts3conn = ts3.query.TS3ServerConnection(
            f"telnet://{self.username}:{self.password}@{self.host}:{self.port}"
        )
        ts3conn.exec_("use", sid=self.server_id)
        ts3conn.exec_("servernotifyregister", event="server")
        return ts3conn

    def handle_initial_clients(self, ts3conn):
        """Handle initial client list after connection"""
        self.connected_users.clear()
        self.client_uid_map.clear()
        clients = ts3conn.exec_("clientlist")
        for client in clients:
            if client.get("client_type") == "0":
                if self.excluded_role_id not in client.get("client_servergroups", "").split(","):
                    uid, name = self.get_client_data(client["clid"], ts3conn)
                    if uid:
                        self.connected_users.add(uid)
                        self.client_uid_map[client["clid"]] = uid
                        self.database.update_user_name(uid, name, "teamspeak")

    def get_online_users(self):
        return list(self.connected_users)

    def get_client_data(self, client_id, ts3conn):
        """Get client name and unique identifier for a client"""
        try:
            client_info = ts3conn.exec_("clientinfo", clid=client_id)[0]
            return client_info["client_unique_identifier"], client_info["client_nickname"]
        except ts3.query.TS3QueryError as err:
            print(f"Error getting client info: {err}")
            return None

    def update_time(self):
        """Background thread to update minutes every 60 seconds"""
        logging.info("Teamspeak Time Thread started successfully.")
        while self.running:
            if datetime.now().minute == 0:
                self.database.log_usage_stats(
                    user_count=len(self.connected_users),
                    platform='teamspeak'
                )
            if self.connected_users:
                self.database.update_times(self.connected_users, "teamspeak")
                self.database.update_ranks(self.connected_users, "teamspeak")
            time.sleep(60)

    def run(self):
        """Main bot loop with reconnection logic"""
        self.running = True
        update_thread = threading.Thread(target=self.update_time, daemon=True)
        update_thread.start()

        while self.running:
            try:
                with self.connect_to_server() as ts3conn:
                    logging.info("Successfully connected to TeamSpeak server")
                    self.reconnect_delay = 30
                    self.handle_initial_clients(ts3conn)
                    
                    while self.running:
                        ts3conn.send_keepalive()
                        try:
                            event = ts3conn.wait_for_event(timeout=240)
                            if event:
                                self.handle_event(event[0], ts3conn)
                        except ts3.query.TS3TimeoutError:
                            continue

            except ts3.query.TS3QueryError as err:
                logging.error(f"TS3 Query Error: {err}")
                if "banned" in str(err).lower():
                    logging.warning("Bot is banned, waiting longer before reconnect")
                    time.sleep(300)
                else:
                    time.sleep(self.reconnect_delay)
                    self.reconnect_delay = min(300, self.reconnect_delay * 2)
                    
            except Exception as e:
                logging.error(f"Unexpected error: {e}")
                time.sleep(self.reconnect_delay)
                self.reconnect_delay = min(300, self.reconnect_delay * 2)

    def handle_event(self, event, ts3conn):
        """Handle TeamSpeak server events"""
        logging.debug(f"Event: {event['reasonid']}")
        if event["reasonid"] == "0":  # Client connected
            if event.get("client_type") == "0":
                if self.excluded_role_id not in event.get("client_servergroups", "").split(","):
                    uid, name = self.get_client_data(event["clid"], ts3conn)
                    if uid:
                        self.connected_users.add(uid)
                        self.client_uid_map[event["clid"]] = uid
                        self.database.update_user_name(uid, name, "teamspeak")
                    
        elif event["reasonid"] == "8":  # Client disconnected
            uid = self.client_uid_map.pop(event["clid"], None)
            if uid in self.connected_users:
                self.connected_users.remove(uid)

    def stop(self):
        """Gracefully stop the bot"""
        self.running = False