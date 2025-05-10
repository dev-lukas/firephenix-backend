import ts3
import requests
from app.utils.logger import RankingLogger
from app.config import Config
from app.utils.database import DatabaseManager

logging = RankingLogger(__name__).get_logger()

class ClientManager:
    """Manages TeamSpeak client information and tracking"""
    
    def __init__(self, config):
        self.excluded_role_id = config.TS3_EXCLUDED_ROLE_ID
        self.connected_users = set()
        self.client_uid_map = {}
        self.client_name_map = {}
    
    def handle_initial_clients(self, ts3conn):
        """Process existing clients after connection. This serves as a full rescan."""
        self.connected_users.clear()
        self.client_uid_map.clear()
        self.client_name_map.clear()
        
        logging.debug("Starting initial client scan (handle_initial_clients).")
        try:
            clients = ts3conn.exec_("clientlist")
            for client in clients:
                if client.get("client_type") != "0": 
                    continue
                
                client_info_full = ts3conn.exec_("clientinfo", clid=client["clid"])[0]
                cldbid = client_info_full.get("client_database_id")
                
                try:
                    groups_info = ts3conn.exec_("servergroupsbyclientid", cldbid=cldbid)
                    group_ids = [int(group.get("sgid", 0)) for group in groups_info]
                    if self.excluded_role_id in group_ids:
                        logging.debug(f"Excluding client {client_info_full.get('client_nickname')} due to excluded role.")
                        continue
                except ts3.query.TS3QueryError as group_error:
                    logging.warning(f"Could not get group info for cldbid {cldbid} (client: {client.get('client_nickname')}): {group_error}. Skipping client.")
                    continue

                uid = client_info_full.get("client_unique_identifier")
                name = client_info_full.get("client_nickname")

                if not uid or not name:
                    logging.warning(f"Client with clid {client['clid']} has no UID or Name. Skipping.")
                    continue

                self.connected_users.add(uid)
                self.client_uid_map[client["clid"]] = uid
                self.client_name_map[uid] = name
                logging.debug(f"Tracking initial client: {name} ({uid})")
                
                self.check_vpn_and_kick_if_needed(client_info_full, client["clid"], ts3conn)

            logging.info(f"Initial client scan complete. Tracking {len(self.connected_users)} users.")
        except ts3.query.TS3QueryError as e:
            logging.error(f"Error handling initial clients: {e}")
        except Exception as ex:
            logging.error(f"Unexpected error in handle_initial_clients: {ex}")
    
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
            logging.debug(f"Ignoring connect event for non-user client_type: {event.get('client_type')}")
            return None

        clid = event.get("clid")
        if not clid:
            logging.warning("Connect event missing clid.")
            return None

        try:
            client_info = ts3conn.exec_("clientinfo", clid=clid)[0]
            cldbid = client_info.get("client_database_id")
            uid = client_info.get("client_unique_identifier")
            name = client_info.get("client_nickname")

            if not cldbid or not uid or not name:
                logging.warning(f"Could not get full info for connecting clid {clid}. UID: {uid}, Name: {name}. Skipping.")
                return None

            groups_info = ts3conn.exec_("servergroupsbyclientid", cldbid=cldbid)
            group_ids = [int(group.get("sgid", 0)) for group in groups_info]
            
            if self.excluded_role_id in group_ids:
                logging.debug(f"Ignoring connect for excluded user: {name} ({uid})")
                return None 
            
            self.connected_users.add(uid)
            self.client_uid_map[clid] = uid
            self.client_name_map[uid] = name
            logging.info(f"User connected: {name} ({uid})")
            
            self.check_vpn_and_kick_if_needed(client_info, clid, ts3conn)
            return uid
        except ts3.query.TS3QueryError as e:
            logging.warning(f"Error handling client connect for clid {clid} (user may have disconnected): {e}")
        except Exception as ex:
            logging.error(f"Unexpected error in handle_client_connect for clid {clid}: {ex}")
        return None

    def check_vpn_and_kick_if_needed(self, client_info, clid, ts3conn):
        """Check if the user's IP is VPN/Tor and kick if level is too low."""
        try:
            ip = client_info.get("connection_client_ip")
            if not ip:
                logging.warning(f"No IP found for client {clid} ({client_info.get('client_nickname')}) during VPN check.")
                return
            
            db = DatabaseManager()
            result = db.execute_query("SELECT level FROM user WHERE teamspeak_id = ?", (client_info["client_unique_identifier"],))
            db.close()
            level = result[0][0] if result else 0
            if level < 9:
                resp = requests.get(f"https://vpnapi.io/api/{ip}?key={Config.VPNAPI_API_KEY}", timeout=5)
                if resp.status_code != 200:
                    logging.warning(f"vpnapi.io error: {resp.status_code}")
                    return
                data = resp.json()
                is_vpn = data.get("security", {}).get("vpn", False)
                is_tor = data.get("security", {}).get("tor", False)
                if is_vpn or is_tor:
                    try:
                        ts3conn.exec_("clientkick", clid=clid, reasonid=5, reasonmsg="VPNs sind aus Abuse Gründen erst ab Level 9 erlaubt. Bei dringendem Bedarf bitte an admin@firephenix.de wenden.")
                        logging.info(f"Kicked user {clid} for VPN/Tor usage (level {level})")
                    except Exception as e:
                        logging.error(f"Failed to kick user {clid}: {e}")
        except Exception as e:
            logging.error(f"Error in VPN check: {e}")
    
    def handle_client_disconnect(self, event):
        """Handle client disconnection event"""
        clid = event.get("clid")
        if not clid:
            logging.warning("Disconnect event missing clid.")
            return None
            
        uid = self.client_uid_map.pop(clid, None)
        if uid:
            if uid in self.connected_users:
                self.connected_users.remove(uid)
            name = self.client_name_map.pop(uid, "Unknown") 
            logging.info(f"User disconnected: {name} ({uid}). Reason ID: {event.get('reasonid', 'N/A')}")
        else:
            logging.debug(f"Received disconnect for untracked clid: {clid}")
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
