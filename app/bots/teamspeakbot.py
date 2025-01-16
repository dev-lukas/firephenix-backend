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

    def get_online_users(self):
        return list(self.connected_users)

    def update_time(self):
        """Background thread to update minutes every 60 seconds"""
        logging.info("Teamspeak Time Thread started successfully.")
        while True:
            if datetime.now().minute == 0:
                self.database.log_usage_stats(
                    user_count=len(self.connected_users),
                    platform='teamspeak'
                )
            if self.connected_users:
                self.database.update_times(self.connected_users, "teamspeak")
            time.sleep(60)
        
    def get_client_data(self, client_id, ts3conn):
        """Get client name and unique identifier for a client"""
        try:
            client_info = ts3conn.exec_("clientinfo", clid=client_id)[0]
            return client_info["client_unique_identifier"], client_info["client_nickname"]
        except ts3.query.TS3QueryError as err:
            print(f"Error getting client info: {err}")
            return None

    def run(self):
        try:
            update_thread = threading.Thread(target=self.update_time)
            update_thread.daemon = True
            update_thread.start()
            with ts3.query.TS3ServerConnection(f"telnet://{self.username}:{self.password}@{self.host}:{self.port}") as ts3conn:
                # Select virtual server
                ts3conn.exec_("use", sid=self.server_id)
                
                # Register for events
                ts3conn.exec_("servernotifyregister", event="server")
                
                # Get initial client list
                clients = ts3conn.exec_("clientlist")
                for client in clients:
                    if client.get("client_type") == "0":  # Regular clients only
                        if self.excluded_role_id not in client.get("client_servergroups", "").split(","):
                            uid, name = self.get_client_data(client["clid"], ts3conn)
                            self.connected_users.add(uid)
                            self.client_uid_map[client["clid"]] = uid
                            self.database.update_user_name(uid, name, "teamspeak")
                
                # Event loop
                while True:
                    ts3conn.send_keepalive()
                    try:
                        event = ts3conn.wait_for_event(timeout=240)
                    except ts3.query.TS3TimeoutError:
                        continue
                    else:
                        logging.debug(f"Event: {event[0]['reasonid']}")
                        if event[0]["reasonid"] == "0":  # Client connected
                            if event[0].get("client_type") == "0":
                                if self.excluded_role_id not in event[0].get("client_servergroups", "").split(","):
                                    uid, name = self.get_client_data(event[0]["clid"], ts3conn)
                                    self.connected_users.add(uid)
                                    self.client_uid_map[event[0]["clid"]] = uid
                                    self.database.update_user_name(uid, name, "teamspeak")
                                
                        elif event[0]["reasonid"] == "8":  # Client disconnected
                            uid = self.client_uid_map.get(event[0]["clid"])
                            self.client_uid_map.pop(event[0]["clid"], None)
                            if uid in self.connected_users:
                                self.connected_users.remove(uid)
                            
        except ts3.query.TS3QueryError as err:
            logging.error(f"TS3 Query Error: {err}")
        except Exception as e:
            logging.error(f"Error: {e}")