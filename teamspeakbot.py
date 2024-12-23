import threading
import ts3
from datetime import time
import os
from dotenv import load_dotenv

class TeamspeakBot:
    def __init__(self):
        load_dotenv()
        self.host = os.getenv('TS3_HOST')
        self.port = int(os.getenv('TS3_PORT', '10011'))
        self.username = os.getenv('TS3_USERNAME')
        self.password = os.getenv('TS3_PASSWORD')
        self.server_id = int(os.getenv('TS3_SERVER_ID', '1'))
        self.excluded_role_id = os.getenv('TS3_EXCLUDED_ROLE_ID')
        
        self.connected_users = set()

    def update_time(self):
        """Background thread to update minutes every 60 seconds"""
        while self.running:
            if self.connected_users:
                # self.update_database(self.connected_users)
                print("Users connected: {0}".format(self.connected_users))
            time.sleep(60)

    def run(self):
        try:
            update_thread = threading.Thread(target=self.update_time)
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
                        if self.excluded_role_id not in event[0].get("client_servergroups", "").split(","):
                            self.connected_users.add(client["clid"])
                
                print("Bot is running and tracking users...")
                
                # Event loop
                while True:
                    event = ts3conn.wait_for_event()
                    
                    if event[0]["reasonid"] == "0":  # Client connected
                        if event[0].get("client_type") == "0":
                            if self.excluded_role_id not in event[0].get("client_servergroups", "").split(","):
                                self.connected_users.add(event[0]["clid"])
                            
                    elif event[0]["reasonid"] == "8":  # Client disconnected
                        if event[0].get("client_type") == "0":
                            if self.excluded_role_id not in event[0].get("client_servergroups", "").split(","):
                                self.connected_users.remove(event[0]["clid"])
                            
        except ts3.query.TS3QueryError as err:
            print(f"TS3 Query Error: {err}")
        except Exception as e:
            print(f"Error: {e}")

def main():
    bot = TeamspeakBot()
    bot.run()

if __name__ == "__main__":
    main()