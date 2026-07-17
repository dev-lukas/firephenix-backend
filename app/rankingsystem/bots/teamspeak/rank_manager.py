import atsq
from app.utils.database import DatabaseConnectionError
from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

class RankManager:
    """Manages TeamSpeak user ranks and server groups"""

    def __init__(self, config, db_manager, client: atsq.Client):
        self.config = config
        self.db = db_manager
        self.client = client

    async def check_user_roles(self, uid):
        """Check if user rank needs to be updated"""
        try:
            logging.debug(f"Checking rank for user: {uid}")
            cldbid = await self.client.client_dbid_from_uid(uid)

            try:
                rank, division = await self.db.get_user_roles(uid, "teamspeak")
            except DatabaseConnectionError:
                logging.error("Database connection error in check_user_roles. Skipping.")
                return

            if rank is None or division is None:
                if await self.db.has_time_entry(uid, "teamspeak"):
                    logging.warning(f"User {uid} has no rank or division set in the database, but has time entries. Seems like the database failed to fetch. Skipping.")
                    return
                else:
                    logging.info(f"User {uid} has no rank or time set in the database yet; welcome new user!")
                    rank = 1
                    division = 1

            groups_info = await self.client.server_groups_by_client(cldbid)
            group_ids = [int(group.get("sgid", 0)) for group in groups_info]

            logging.debug(f"User {uid} database rank and division: {rank} and {division}")
            logging.debug(f"User {uid} should have group {self.config.TEAMSPEAK_LEVEL_MAP.get(rank)} and {self.config.TEAMSPEAK_DIVISION_MAP.get(division)}")
            logging.debug(f"User {uid} has groups: {group_ids}")

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
                await self.set_ranks(uid, level=rank)

            if not correct_division or division_roles_count > 1:
                logging.debug(f"Setting division for user {uid} to {division} (had {division_roles_count} division roles)")
                await self.set_ranks(uid, division=division)

        except atsq.QueryError as e:
            logging.error(f"Error checking user roles: {e}")
        except Exception as e:
            logging.error(f"Error getting server groups for client {uid}: {e}")

    async def set_ranks(self, client_id, level=None, division=None):
        """Update user ranks in the TeamSpeak server"""
        if level is None and division is None:
            logging.warning(f"No rank type specified for user {client_id}")
            return None

        try:
            cldbid = await self.client.client_dbid_from_uid(client_id)

            if level is not None:
                await self._update_server_group(
                    cldbid,
                    self.config.TEAMSPEAK_LEVEL_MAP,
                    level,
                    "level",
                    client_id
                )

            if division is not None:
                await self._update_server_group(
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

    async def set_server_group(self, client_id, group_id):
        """Set a specific server group for a user and return command details."""
        cldbid = None
        step = "client_lookup"
        try:
            cldbid = await self.client.client_dbid_from_uid(client_id)
            if not cldbid:
                return {"ok": False, "error": "client_dbid_missing"}

            step = "servergroup_lookup"
            groups_info = await self.client.server_groups_by_client(cldbid)
            group_ids = [int(group.get("sgid", 0)) for group in groups_info]

            if group_id in group_ids:
                return {
                    "ok": True,
                    "already_present": True,
                    "cldbid": cldbid,
                    "group_id": group_id,
                }

            step = "servergroup_add"
            try:
                await self.client.server_group_add_client(sgid=group_id, cldbid=cldbid)
            except Exception as e:
                try:
                    step = "servergroup_recheck"
                    refreshed_groups = await self.client.server_groups_by_client(cldbid)
                    refreshed_group_ids = [int(group.get("sgid", 0)) for group in refreshed_groups]
                    if group_id in refreshed_group_ids:
                        logging.info(
                            f"TeamSpeak group {group_id} for {client_id} is present after add error; treating as success."
                        )
                        return {
                            "ok": True,
                            "already_present": True,
                            "recovered_after_add_error": True,
                            "cldbid": cldbid,
                            "group_id": group_id,
                            "details": str(e),
                        }
                except Exception as refresh_error:
                    logging.error(
                        f"Failed to re-check TeamSpeak groups after add error for {client_id} ({cldbid}): {refresh_error}"
                    )
                logging.error(f"Failed to add TeamSpeak group {group_id} for {client_id} ({cldbid}): {e}")
                return {
                    "ok": False,
                    "error": "servergroup_add_failed",
                    "cldbid": cldbid,
                    "group_id": group_id,
                    "details": str(e),
                }

            logging.debug(f"Set server group {group_id} for user {client_id}")

            return {
                "ok": True,
                "already_present": False,
                "cldbid": cldbid,
                "group_id": group_id,
            }

        except Exception as e:
            logging.error(f"Failed to set TeamSpeak group {group_id} for user {client_id} at {step}: {e}")
            return {
                "ok": False,
                "error": f"{step}_failed",
                "cldbid": cldbid,
                "group_id": group_id,
                "details": str(e),
            }

    async def remove_server_group(self, client_id, group_id):
        """Remove a specific server group from a user"""
        try:
            cldbid = await self.client.client_dbid_from_uid(client_id)

            groups_info = await self.client.server_groups_by_client(cldbid)
            group_ids = [int(group.get("sgid", 0)) for group in groups_info]

            if group_id not in group_ids:
                return True

            await self.client.server_group_del_client(sgid=group_id, cldbid=cldbid)
            logging.debug(f"Removed server group {group_id} from user {client_id}")

            return True

        except Exception as e:
            logging.error(f"Failed to remove server group for user {client_id}: {e}")
            return False


    async def _update_server_group(self, cldbid, group_map, new_value, rank_type, client_id):
        """Update a specific server group type for a user"""
        try:
            groups_info = await self.client.server_groups_by_client(cldbid)

            for group in groups_info:
                group_id = int(group.get("sgid", 0))
                if group_id in group_map.values():
                    await self.client.server_group_del_client(sgid=group_id, cldbid=cldbid)

            if new_value in group_map:
                new_group_id = group_map[new_value]
                await self.client.server_group_add_client(sgid=new_group_id, cldbid=cldbid)

                logging.debug(f"Updated {rank_type} for user {client_id} to {new_value}")
            else:
                logging.error(f"Invalid {rank_type} value: {new_value}")

        except atsq.QueryError as err:
            logging.error(f"TS3 Query Error updating {rank_type}: {err}")
