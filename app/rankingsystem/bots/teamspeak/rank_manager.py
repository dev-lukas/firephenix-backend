import ts3
from app.utils.database import DatabaseConnectionError
from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

class RankManager:
    """Manages TeamSpeak user ranks and server groups"""
    
    def __init__(self, config, db_manager, connection_manager):
        self.config = config
        self.db = db_manager
        self.connection_manager = connection_manager
    
    def check_user_roles(self, uid, ts3conn):
        """Check if user rank needs to be updated"""
        try:
            logging.debug(f"Checking rank for user: {uid}")
            db_info = ts3conn.exec_("clientgetdbidfromuid", cluid=uid)[0]
            cldbid = db_info.get("cldbid")
            
            try:
                rank, division = self.db.get_user_roles(uid, "teamspeak")
            except DatabaseConnectionError:
                logging.error("Database connection error in check_user_roles. Skipping.")
                return
            
            if rank is None:
                rank = 1
            if division is None:
                division = 1

            groups_info = ts3conn.exec_("servergroupsbyclientid", cldbid=cldbid)
            group_ids = [int(group.get("sgid", 0)) for group in groups_info]
            
            correct_rank = False
            rank_roles_count = 0
            correct_division = False
            division_roles_count = 0
            for group in group_ids:
                if group in self.config.TEAMSPEAK_LEVEL_MAP.values():
                    rank_roles_count += 1
                    if group == self.config.TEAMSPEAK_LEVEL_MAP.get(rank):
                        correct_rank = True

                if group in self.config.TEAMSPEAK_DIVISION_MAP.values():
                    division_roles_count += 1
                    if group == self.config.TEAMSPEAK_DIVISION_MAP.get(division):
                        correct_division = True

            if not correct_rank or rank_roles_count > 1:
                logging.debug(f"Setting rank for user {uid} to {rank} (had {rank_roles_count} rank roles)")
                self.set_ranks(uid, level=rank)

            if not correct_division or division_roles_count > 1:
                logging.debug(f"Setting division for user {uid} to {division} (had {division_roles_count} division roles)")
                self.set_ranks(uid, division=division)

        except ts3.query.TS3QueryError as e:
            logging.error(f"Error checking user roles: {e}")
        except Exception as e:
            logging.error(f"Error getting server groups for client {uid}: {e}")
    
    def set_ranks(self, client_id, level=None, division=None):
        """Update user ranks in the TeamSpeak server"""
        if level is None and division is None:
            logging.warning(f"No rank type specified for user {client_id}")
            return None
            
        try:
            with self.connection_manager.connect() as ts3conn:
                db_info = ts3conn.exec_("clientgetdbidfromuid", cluid=client_id)[0]
                cldbid = db_info.get("cldbid")

                if level is not None:
                    self._update_server_group(
                        ts3conn, 
                        cldbid, 
                        self.config.TEAMSPEAK_LEVEL_MAP, 
                        level, 
                        "level",
                        client_id
                    )
                    
                if division is not None:
                    self._update_server_group(
                        ts3conn, 
                        cldbid, 
                        self.config.TEAMSPEAK_DIVISION_MAP, 
                        division,
                        "division",
                        client_id
                    )
                    
                return True
                    
        except Exception as e:
            logging.error(f"Rank update failed for user {client_id}: {e}")
            return None
    
    def set_server_group(self, client_id, group_id):
        """Set a specific server group for a user"""
        try:
            with self.connection_manager.connect() as ts3conn:
                db_info = ts3conn.exec_("clientgetdbidfromuid", cluid=client_id)[0]
                cldbid = db_info.get("cldbid")
                
                groups_info = ts3conn.exec_("servergroupsbyclientid", cldbid=cldbid)
                group_ids = [int(group.get("sgid", 0)) for group in groups_info]
                
                if group_id in group_ids:
                    return True
                
                ts3conn.exec_("servergroupaddclient", sgid=group_id, cldbid=cldbid)
                logging.debug(f"Set server group {group_id} for user {client_id}")
                
                return True
                
        except Exception as e:
            logging.error(f"Failed to set server group for user {client_id}: {e}")
            return False
        
    def remove_server_group(self, client_id, group_id):
        """Remove a specific server group from a user"""
        try:
            with self.connection_manager.connect() as ts3conn:
                db_info = ts3conn.exec_("clientgetdbidfromuid", cluid=client_id)[0]
                cldbid = db_info.get("cldbid")
                
                groups_info = ts3conn.exec_("servergroupsbyclientid", cldbid=cldbid)
                group_ids = [int(group.get("sgid", 0)) for group in groups_info]
                
                if group_id not in group_ids:
                    return True
                
                ts3conn.exec_("servergroupdelclient", sgid=group_id, cldbid=cldbid)
                logging.debug(f"Removed server group {group_id} from user {client_id}")
                
                return True
                
        except Exception as e:
            logging.error(f"Failed to remove server group for user {client_id}: {e}")
            return False
    

    def _update_server_group(self, ts3conn, cldbid, group_map, new_value, rank_type, client_id):
        """Update a specific server group type for a user"""
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
                
                logging.debug(f"Updated {rank_type} for user {client_id} to {new_value}")
            else:
                logging.error(f"Invalid {rank_type} value: {new_value}")
                
        except ts3.query.TS3QueryError as err:
            logging.error(f"TS3 Query Error updating {rank_type}: {err}")
