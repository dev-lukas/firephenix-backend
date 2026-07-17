import asyncio
import atsq
import requests
from atsq import ClientType
from app.utils.logger import RankingLogger
from app.config import Config
from app.utils.async_database import get_async_db
from app.rankingsystem.bots.teamspeak.rank_manager import RankManager

logging = RankingLogger(__name__).get_logger()

class ClientManager:
    """Manages TeamSpeak client information and tracking"""

    def __init__(self, config, rank_manager: RankManager, client: atsq.Client):
        self.excluded_role_id = int(config.TS3_EXCLUDED_ROLE_ID)
        self.connected_users = set()
        self.client_uid_map = {}
        self.client_name_map = {}
        self.rank_manager = rank_manager
        self.client = client
        self.db = get_async_db()

    async def handle_initial_clients(self):
        """Process existing clients after connection. This serves as a full rescan."""
        self.connected_users.clear()
        self.client_uid_map.clear()
        self.client_name_map.clear()

        logging.debug("Starting initial client scan (handle_initial_clients).")
        try:
            clients = await self.client.client_list()
            for client in clients:
                if client.get("client_type") != ClientType.VOICE:
                    continue

                client_info_full = await self.client.client_info(client["clid"])
                cldbid = client_info_full.get("client_database_id")

                try:
                    groups_info = await self.client.server_groups_by_client(cldbid)
                    group_ids = [int(group.get("sgid", 0)) for group in groups_info]
                    if self.excluded_role_id in group_ids:
                        logging.debug(f"Excluding client {client_info_full.get('client_nickname')} due to excluded role.")
                        continue
                except atsq.QueryError as group_error:
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

                await self._capture_myteamspeak_identity(
                    uid, client_info_full.get("client_myteamspeak_id"), is_ts6=self._is_ts6()
                )
                await self.check_vpn_and_kick_if_needed(client_info_full, client["clid"])

            logging.info(f"Initial client scan complete. Tracking {len(self.connected_users)} users.")
        except atsq.QueryError as e:
            logging.error(f"Error handling initial clients: {e}")
        except Exception as ex:
            logging.error(f"Unexpected error in handle_initial_clients: {ex}")

    async def get_client_data(self, client_id):
        """Retrieve client information from TeamSpeak server"""
        try:
            client_info = await self.client.client_info(client_id)
            return (
                client_info["client_unique_identifier"],
                client_info["client_nickname"]
            )
        except atsq.QueryError as err:
            logging.error(f"Error getting client info: {err}")
            return None

    async def handle_client_connect(self, event):
        """Handle client connection event"""

        if event.get("client_type") != ClientType.VOICE:
            logging.debug(f"Ignoring connect event for non-user client_type: {event.get('client_type')}")
            return None

        clid = event.get("clid")
        if not clid:
            logging.warning("Connect event missing clid.")
            return None

        try:
            client_info = await self.client.client_info(clid)
            cldbid = client_info.get("client_database_id")
            uid = client_info.get("client_unique_identifier")
            name = client_info.get("client_nickname")

            if not cldbid or not uid or not name:
                logging.warning(f"Could not get full info for connecting clid {clid}. UID: {uid}, Name: {name}. Skipping.")
                return None

            # TS3->TS6 identity bridge: capture the stable myTeamSpeak account id (identical
            # across TS3/TS6) so returning users are recognised after the UID hash change.
            # Best-effort — never let it break connection handling.
            await self._capture_myteamspeak_identity(
                uid, client_info.get("client_myteamspeak_id"), is_ts6=self._is_ts6()
            )

            groups_info = await self.client.server_groups_by_client(cldbid)
            group_ids = [int(group.get("sgid", 0)) for group in groups_info]

            if self.excluded_role_id in group_ids:
                logging.debug(f"Ignoring connect for excluded user: {name} ({uid})")
                return None

            self.connected_users.add(uid)
            self.client_uid_map[clid] = uid
            self.client_name_map[uid] = name
            logging.debug(f"User connected: {name} ({uid})")

            await self.check_vpn_and_kick_if_needed(client_info, clid)
            return uid
        except atsq.QueryError as e:
            logging.warning(f"Error handling client connect for clid {clid} (user may have disconnected): {e}")
            return None
        except Exception as ex:
            logging.error(f"Unexpected error in handle_client_connect for clid {clid}: {ex}")
            return None

    def _is_ts6(self):
        """Whether the connected server is TS6-generation (atsq sniffs it at connect)."""
        try:
            return self.client.dialect == atsq.Dialect.TS6
        except Exception:
            return False

    async def _capture_myteamspeak_identity(self, uid, myteamspeak_id, is_ts6=False):
        """Persist the connecting client's myTeamSpeak account id and, if it maps to a
        prior TS identity under a different UID, bridge them (seamless recognition).
        Best-effort: any failure is logged and swallowed so connect handling continues."""
        if not myteamspeak_id:
            return
        try:
            result = await self.db.recognize_teamspeak_client(uid, myteamspeak_id, is_ts6=is_ts6)
            if result.get("merged"):
                logging.info(
                    f"Bridged TS identity via myTeamSpeak id: absorbed {result.get('absorbed_uid')} "
                    f"into {result.get('canonical_uid')}")
        except Exception as e:
            logging.error(f"myTeamSpeak identity capture failed for uid {uid}: {e}")

    async def _get_user_level(self, teamspeak_id):
        result = await self.db.execute_query(
            "SELECT level FROM user WHERE teamspeak_id = %s", (teamspeak_id,))
        return result[0][0] if result else 0

    async def check_vpn_and_kick_if_needed(self, client_info, clid):
        """Check if the user's IP is VPN/Tor and kick if level is too low."""
        try:
            ip = client_info.get("connection_client_ip")
            if not ip:
                logging.warning(f"No IP found for client {clid} ({client_info.get('client_nickname')}) during VPN check.")
                return

            level = await self._get_user_level(client_info["client_unique_identifier"])
            if level < 9:
                # requests is blocking; keep it off the event loop
                resp = await asyncio.to_thread(
                    requests.get, f"https://vpnapi.io/api/{ip}?key={Config.VPNAPI_API_KEY}", timeout=5
                )
                if resp.status_code != 200:
                    logging.warning(f"vpnapi.io error: {resp.status_code}")
                    return
                data = resp.json()
                is_vpn = data.get("security", {}).get("vpn", False)
                is_tor = data.get("security", {}).get("tor", False)
                if is_vpn or is_tor:
                    try:
                        await self.client.client_kick(clid, reasonid=5, reasonmsg="VPNs sind aus Abuse Gründen erst ab Level 9 erlaubt. Bei dringendem Bedarf bitte an admin@firephenix.de wenden.")
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
            logging.debug(f"User disconnected: {name} ({uid}). Reason ID: {event.get('reasonid', 'N/A')}")
        else:
            logging.debug(f"Received disconnect for untracked clid: {clid}")
        return uid

    def get_online_users(self):
        """Return list of currently connected users and their names"""
        return list(self.connected_users), self.client_name_map

    async def is_client_excluded(self, cldbid):
        """Check if client should be excluded from tracking"""
        try:
            groups_info = await self.client.server_groups_by_client(cldbid)
            group_ids = [int(group.get("sgid", 0)) for group in groups_info]
            return self.excluded_role_id in group_ids
        except atsq.QueryError:
            logging.error(f"Error checking if client {cldbid} is excluded")
            return False

    async def validate_connected_users(self):
        """Validate that all tracked users are actually still connected and remove stale entries"""
        try:
            # Get current clients from server
            current_clients = await self.client.client_list()
            current_uids = set()
            current_clid_to_uid = {}

            for client in current_clients:
                if client.get("client_type") != ClientType.VOICE:  # Skip non-user clients
                    continue

                try:
                    client_info = await self.client.client_info(client["clid"])
                    uid = client_info.get("client_unique_identifier")
                    cldbid = client_info.get("client_database_id")

                    if not uid or not cldbid:
                        continue

                    # Check if user should be excluded
                    groups_info = await self.client.server_groups_by_client(cldbid)
                    group_ids = [int(group.get("sgid", 0)) for group in groups_info]
                    if self.excluded_role_id in group_ids:
                        continue

                    current_uids.add(uid)
                    current_clid_to_uid[client["clid"]] = uid

                except atsq.QueryError as e:
                    logging.warning(f"Error getting info for client {client.get('clid')}: {e}")
                    continue

            # Find users in our tracking that are no longer connected
            stale_users = self.connected_users - current_uids
            if stale_users:
                logging.warning(f"Found {len(stale_users)} stale users, removing them")
                for uid in stale_users:
                    self.connected_users.discard(uid)
                    name = self.client_name_map.pop(uid, "Unknown")
                    logging.debug(f"Removed stale user: {name} ({uid})")

            # Find users connected but not in our tracking
            missing_users = current_uids - self.connected_users
            if missing_users:
                logging.warning(f"Found {len(missing_users)} missing users, adding them")
                for uid in missing_users:
                    self.connected_users.add(uid)
                    # Find the name for this UID
                    for clid, tracked_uid in current_clid_to_uid.items():
                        if tracked_uid == uid:
                            try:
                                client_info = await self.client.client_info(clid)
                                name = client_info.get("client_nickname", "Unknown")
                                self.client_name_map[uid] = name
                                self.client_uid_map[clid] = uid
                                logging.debug(f"Added missing user: {name} ({uid})")
                                break
                            except atsq.QueryError:
                                continue

            # Update client_uid_map to reflect current state
            self.client_uid_map = current_clid_to_uid.copy()

            logging.debug(f"Validation complete. Tracking {len(self.connected_users)} users.")

        except atsq.QueryError as e:
            logging.error(f"Error during user validation: {e}")
        except Exception as e:
            logging.error(f"Unexpected error during user validation: {e}")

    def cleanup_stale_mappings(self):
        """Clean up any inconsistencies in the mapping dictionaries"""
        # Remove UIDs from name_map that aren't in connected_users
        stale_names = set(self.client_name_map.keys()) - self.connected_users
        for uid in stale_names:
            self.client_name_map.pop(uid, None)
            logging.debug(f"Removed stale name mapping for UID: {uid}")

        # Remove CLIDs that map to UIDs not in connected_users
        stale_clids = []
        for clid, uid in self.client_uid_map.items():
            if uid not in self.connected_users:
                stale_clids.append(clid)

        for clid in stale_clids:
            self.client_uid_map.pop(clid, None)
            logging.debug(f"Removed stale CLID mapping: {clid}")
