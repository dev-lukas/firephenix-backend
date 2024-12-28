import threading
import ts3
import time
import os
from dotenv import load_dotenv
from database import DatabaseManager
from logger import RankingLogger

logging = RankingLogger(__name__).get_logger()


class TeamspeakBot:
    def __init__(self):
        load_dotenv()
        self.host = os.getenv('TS3_HOST')
        self.port = int(os.getenv('TS3_PORT', '10011'))
        self.username = os.getenv('TS3_USERNAME')
        self.password = os.getenv('TS3_PASSWORD')
        self.server_id = int(os.getenv('TS3_SERVER_ID', '1'))
        self.excluded_role_id = os.getenv('TS3_EXCLUDED_ROLE_ID')
        
        self.database = DatabaseManager(
            host=os.getenv("DB_HOST"),
            user=os.getenv("DB_USER"),
            password=os.getenv("DB_PASSWORD"),
            database=os.getenv("DB_NAME")
        )

        self.connected_users = set()
        self.client_uid_map = {}

    def update_time(self):
        """Background thread to update minutes every 60 seconds"""
        logging.info("Teamspeak Time Thread started successfully.")
        while True:
            if self.connected_users:
                self.database.update_times(self.connected_users, "teamspeak")
            time.sleep(60)

    def get_client_uid(self, client_id, ts3conn):
        """Get unique identifier for a client"""
        try:
            client_info = ts3conn.exec_("clientinfo", clid=client_id)[0]
            return client_info["client_unique_identifier"]
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
                            uid = self.get_client_uid(client["clid"], ts3conn)
                            self.connected_users.add(uid)
                            self.client_uid_map[client["clid"]] = uid
                
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
                                    uid = self.get_client_uid(event[0]["clid"], ts3conn)
                                    self.connected_users.add(uid)
                                    self.client_uid_map[event[0]["clid"]] = uid
                                
                        elif event[0]["reasonid"] == "8":  # Client disconnected
                            uid = self.client_uid_map.get(event[0]["clid"])
                            self.client_uid_map.pop(event[0]["clid"], None)
                            if uid in self.connected_users:
                                self.connected_users.remove(uid)
                            
        except ts3.query.TS3QueryError as err:
            logging.error(f"TS3 Query Error: {err}")
        except Exception as e:
            logging.error(f"Error: {e}")

if __name__ == "__main__":
    bot = TeamspeakBot()
    bot.run()