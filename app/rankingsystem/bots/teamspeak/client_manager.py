import ts3
from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

class ClientManager:
    """Manages TeamSpeak client information and tracking"""
    
    def __init__(self, config):
        self.excluded_role_id = config.TS3_EXCLUDED_ROLE_ID
        self.connected_users = set()
        self.client_uid_map = {}
        self.client_name_map = {}
    
    def handle_initial_clients(self, ts3conn):
        """Process existing clients after connection"""
        self.connected_users.clear()
        self.client_uid_map.clear()
        self.client_name_map.clear()
        
        try:
            clients = ts3conn.exec_("clientlist")
            for client in clients:
                if client.get("client_type") != "0":
                    continue
                
                client_info = ts3conn.exec_("clientinfo", clid=client["clid"])[0]
                cldbid = client_info["client_database_id"]
                groups_info = ts3conn.exec_("servergroupsbyclientid", cldbid=cldbid)
                group_ids = [int(group.get("sgid", 0)) for group in groups_info]
                
                if self.excluded_role_id in group_ids:
                    continue

                client_data = self.get_client_data(client["clid"], ts3conn)
                if client_data:
                    uid, name = client_data
                    self.connected_users.add(uid)
                    self.client_uid_map[client["clid"]] = uid
                    self.client_name_map[uid] = name
        except ts3.query.TS3QueryError as e:
            logging.error(f"Error handling initial clients: {e}")
    
    def get_client_data(self, client_id, ts3conn):
        """Retrieve client information from TeamSpeak server"""
        try:
            client_info = ts3conn.exec_("clientinfo", clid=client_id)[0]
            return (
                client_info["client_unique_identifier"],
                client_info["client_nickname"]
            )
        except ts3.query.TS3QueryError as err:
            logging.error(f"Error getting client info: {err}")
            return None
    
    def handle_client_connect(self, event, ts3conn):
        """Handle client connection event"""
        if event.get("client_type") != "0":
            return

        try:
            client_info = ts3conn.exec_("clientinfo", clid=event["clid"])[0]
            cldbid = client_info["client_database_id"]
            groups_info = ts3conn.exec_("servergroupsbyclientid", cldbid=cldbid)
            group_ids = [int(group.get("sgid", 0)) for group in groups_info]
            
            if self.excluded_role_id in group_ids:
                return

            client_data = self.get_client_data(event["clid"], ts3conn)
            if client_data:
                uid, name = client_data
                self.connected_users.add(uid)
                self.client_uid_map[event["clid"]] = uid
                self.client_name_map[uid] = name
                logging.debug(f"User connected: {uid}")
                return uid
        except ts3.query.TS3QueryError as e:
            logging.error(f"Error handling client connect: {e}")
        return None
    
    def handle_client_disconnect(self, event):
        """Handle client disconnection event"""
        uid = self.client_uid_map.pop(event["clid"], None)
        if uid in self.connected_users:
            self.connected_users.remove(uid)
            self.client_name_map.pop(uid, None)
        return uid
    
    def get_online_users(self):
        """Return list of currently connected users and their names"""
        return list(self.connected_users), self.client_name_map
    
    def is_client_excluded(self, cldbid, ts3conn):
        """Check if client should be excluded from tracking"""
        try:
            groups_info = ts3conn.exec_("servergroupsbyclientid", cldbid=cldbid)
            group_ids = [int(group.get("sgid", 0)) for group in groups_info]
            return self.excluded_role_id in group_ids
        except ts3.query.TS3QueryError:
            logging.error(f"Error checking if client {cldbid} is excluded")
            return False
