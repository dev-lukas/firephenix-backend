"""Async database manager for the bot process (asyncmy / MariaDB).

The bot runs Discord, TeamSpeak, valkey pubsub and the ranking tick on one
asyncio loop; mariadb's blocking connector would stall all of them, so every
bot-side database path goes through this asyncmy-backed manager instead.

The Flask API stays on the synchronous ``DatabaseManager`` (gunicorn workers
are sync); the bot-only business methods were MOVED here from there, so each
piece of logic exists exactly once. Placeholders are ``%s`` (asyncmy) rather
than ``?`` (mariadb connector), and literal ``%`` in SQL must be doubled.

Connections come from a small pool with autocommit enabled; multi-statement
methods open an explicit transaction. On any driver error the pool is
recreated and the operation retried once, mirroring the sync manager's
``ensure_connection`` — if the reconnect fails, ``DatabaseConnectionError``
is raised for callers that handle outages.
"""

import asyncio
from datetime import datetime
from typing import List, Optional, Set, Tuple, Union

import asyncmy
from asyncmy import errors as asyncmy_errors

from app.config import Config
from app.utils.logger import RankingLogger
from app.utils.database import (
    DatabaseConnectionError,
    SEASON_APEX_ACHIEVEMENT,
    _require_steam_id64,
    _ttt_win_breakdown,
    get_season_division_achievement_types,
    get_season_number_for_end_year,
    normalize_ttt_achievement_payload,
    parse_ttt_emitted_at,
    ttt_stats_from_row,
)

logging = RankingLogger(__name__).get_logger()


class AsyncDatabaseManager:
    def __init__(self):
        self._pool = None
        self._pool_lock = asyncio.Lock()

    # -- connection handling ------------------------------------------------

    async def _ensure_pool(self):
        if self._pool is not None:
            return self._pool
        async with self._pool_lock:
            if self._pool is None:
                try:
                    self._pool = await asyncmy.create_pool(
                        host=Config.DB_HOST,
                        port=int(Config.DB_PORT),
                        user=Config.DB_USER,
                        password=Config.DB_PASSWORD,
                        db=Config.DB_NAME,
                        autocommit=True,
                        minsize=0,
                        maxsize=4,
                    )
                except asyncmy_errors.Error as e:
                    logging.error(f"Error connecting to database: {e}")
                    raise DatabaseConnectionError("No database connection available")
        return self._pool

    async def _dispose_pool(self):
        async with self._pool_lock:
            pool, self._pool = self._pool, None
        if pool is not None:
            try:
                pool.close()
                await pool.wait_closed()
            except Exception as e:
                logging.debug(f"Error disposing database pool: {e}")

    async def _run(self, op):
        """Run ``await op(conn)`` on a pooled connection; on a driver error
        recreate the pool and retry once (parity with ensure_connection)."""
        try:
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                return await op(conn)
        except asyncmy_errors.Error as e:
            logging.warning(f"Database operation failed, reconnecting: {e}")
            await self._dispose_pool()
            pool = await self._ensure_pool()
            async with pool.acquire() as conn:
                return await op(conn)

    async def close(self) -> None:
        await self._dispose_pool()

    # -- generic ------------------------------------------------------------

    async def execute_query(self, query: str, params: tuple = None) -> Optional[List[Tuple]]:
        """Generic wrapper for executing SQL (``%s`` placeholders)."""

        async def op(conn):
            async with conn.cursor() as cur:
                await cur.execute(query, params or None)
                if query.strip().upper().startswith(("SELECT", "WITH")):
                    # asyncmy returns a tuple of rows; keep the list contract
                    return list(await cur.fetchall())
                return None

        return await self._run(op)

    # -- time / activity tracking -------------------------------------------

    async def update_times(self, platform_uids: Set[Union[int, str]], platform: str) -> None:
        """Batch update time values for multiple users"""
        if not platform_uids:
            return

        unique_uids = [str(uid) for uid in platform_uids]
        query = f"""
            INSERT INTO time (platform_uid, platform, total_time, daily_time,
                            weekly_time, monthly_time, season_time, last_update)
            VALUES {','.join(['(%s, %s, 1, 1, 1, 1, 1, CURRENT_TIMESTAMP)'] * len(unique_uids))}
            ON DUPLICATE KEY UPDATE
                total_time = total_time + 1,
                daily_time = daily_time + 1,
                weekly_time = weekly_time + 1,
                monthly_time = monthly_time + 1,
                season_time = season_time + 1,
                last_update = CURRENT_TIMESTAMP
        """
        flat_params = [item for uid in unique_uids for item in (uid, platform)]

        async def op(conn):
            async with conn.cursor() as cur:
                await cur.execute(query, flat_params)

        await self._run(op)

    @staticmethod
    def get_time_category(hour: int) -> str:
        """
        Categorize an hour into time categories
        morning: 6-11 (6:00 AM - 11:59 AM)
        noon: 12-17 (12:00 PM - 5:59 PM)
        evening: 18-23 (6:00 PM - 11:59 PM)
        night: 0-5 (12:00 AM - 5:59 AM)
        """
        if 6 <= hour < 12:
            return 'morning'
        elif 12 <= hour < 18:
            return 'noon'
        elif 18 <= hour < 24:
            return 'evening'
        else:
            return 'night'

    async def update_heatmap(self, platform_uids: Set[Union[int, str]], platform: str):
        """Update the activity heatmap for multiple platform UIDs"""
        if not platform_uids:
            return

        now = datetime.now()
        day_of_week = now.weekday()
        time_category = self.get_time_category(now.hour)

        values = [(str(uid), platform, day_of_week, time_category) for uid in platform_uids]

        async def op(conn):
            async with conn.cursor() as cur:
                await cur.executemany("""
                    INSERT INTO activity_heatmap
                        (platform_uid, platform, day_of_week, time_category, activity_minutes)
                    VALUES (%s, %s, %s, %s, 1)
                    ON DUPLICATE KEY UPDATE
                        activity_minutes = activity_minutes + 1,
                        last_update = CURRENT_TIMESTAMP
                """, values)

        await self._run(op)

    # -- ranks --------------------------------------------------------------

    async def update_ranks(self, users: Set[Union[int, str]], platform: str) -> List[Tuple[Union[int, str], int]]:
        rankups = []
        user_ids = [str(uid) for uid in users]
        if not user_ids:
            return rankups

        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'
        placeholders = ','.join(['%s'] * len(user_ids))

        async def op(conn):
            await conn.begin()
            try:
                async with conn.cursor() as cur:
                    await cur.execute(f"""
                        SELECT
                            u.{id_column},
                            COALESCE(d.total_time, 0) + COALESCE(t.total_time, 0) AS total_time,
                            u.level
                        FROM user u
                        LEFT JOIN time d
                            ON d.platform = 'discord'
                            AND d.platform_uid = u.discord_id
                        LEFT JOIN time t
                            ON t.platform = 'teamspeak'
                            AND t.platform_uid = u.teamspeak_id
                        WHERE COALESCE(u.ranking_disabled, 0) = 0
                            AND u.{id_column} IN ({placeholders})
                    """, user_ids)
                    results = await cur.fetchall()

                    for platform_uid, total_time, level in results:
                        calculated_level = Config.get_level_for_minutes(total_time)
                        if calculated_level != level:
                            await cur.execute(
                                f"UPDATE user SET level = %s WHERE {id_column} = %s",
                                (calculated_level, platform_uid))
                            rankups.append((platform_uid, calculated_level))
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

        await self._run(op)
        return rankups

    async def update_seasonal_ranks(self, users: Set[Union[int, str]], platform: str) -> List[Tuple[Union[int, str], int]]:
        rankups = []
        user_ids = [str(uid) for uid in users]
        if not user_ids:
            return rankups

        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'
        placeholders = ','.join(['%s'] * len(user_ids))

        async def op(conn):
            await conn.begin()
            try:
                async with conn.cursor() as cur:
                    await cur.execute(f"""
                        SELECT
                            u.{id_column},
                            COALESCE(d.season_time, 0) + COALESCE(t.season_time, 0) AS season_time,
                            u.division
                        FROM user u
                        LEFT JOIN time d
                            ON d.platform = 'discord'
                            AND d.platform_uid = u.discord_id
                        LEFT JOIN time t
                            ON t.platform = 'teamspeak'
                            AND t.platform_uid = u.teamspeak_id
                        WHERE COALESCE(u.ranking_disabled, 0) = 0
                            AND u.{id_column} IN ({placeholders})
                    """, user_ids)
                    results = await cur.fetchall()

                    for platform_uid, season_time, division in results:
                        calculated_division = Config.get_division_for_minutes(season_time)
                        if calculated_division != division and division <= 5:
                            await cur.execute(
                                f"UPDATE user SET division = %s WHERE {id_column} = %s",
                                (calculated_division, platform_uid))
                            rankups.append((platform_uid, calculated_division))
                            logging.debug(f"Updated {platform} user {platform_uid} to division {calculated_division}")

                    await self._update_top_division_ranks(cur, platform, rankups)
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

        await self._run(op)
        if rankups:
            logging.debug(f"Rank updates for {platform} users: {rankups}")
        return rankups

    @staticmethod
    async def _update_top_division_ranks(cur, platform: str, rankups: List[Tuple[Union[int, str], int]]) -> None:
        """
        Update the top division (Division 6) based on season time.
        Only the top Config.TOP_DIVISION_PLAYER_AMOUNT players can be in Division 6.
        Runs on the caller's cursor inside its transaction.
        """
        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'

        await cur.execute(f"""
            SELECT u.id, u.{id_column}, COALESCE(d.season_time, 0) + COALESCE(t.season_time, 0) AS season_time, u.division
            FROM user u
            LEFT JOIN time d ON d.platform = 'discord' AND d.platform_uid = u.discord_id
            LEFT JOIN time t ON t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id
            WHERE COALESCE(u.ranking_disabled, 0) = 0
                AND u.division IN (5, 6) AND u.{id_column} IS NOT NULL
            ORDER BY season_time DESC
            LIMIT {Config.TOP_DIVISION_PLAYER_AMOUNT * 2}
        """)
        all_players = await cur.fetchall()

        for idx, (user_id, platform_uid, season_time, current_division) in enumerate(all_players):
            target_division = 6 if idx < Config.TOP_DIVISION_PLAYER_AMOUNT else 5

            if current_division != target_division:
                await cur.execute("UPDATE user SET division = %s WHERE id = %s", (target_division, user_id))

                if target_division == 6:
                    logging.debug(f"Promoted user {platform_uid} to Division 6")
                    rankups.append((platform_uid, 6))
                else:
                    logging.debug(f"Demoted user {platform_uid} to Division 5")
                    rankups.append((platform_uid, 5))

    # -- users / streaks / stats --------------------------------------------

    async def update_user_name(self, user_id: str, name: str, platform: str) -> None:
        """
        Insert user if not exists, update name if changed
        platform should be either 'discord' or 'teamspeak'
        """
        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'
        await self.execute_query(f"""
            INSERT INTO user
                ({id_column}, name, created_at)
            VALUES (%s, %s, CURRENT_TIMESTAMP)
            ON DUPLICATE KEY UPDATE
                name = VALUES(name)
        """, (str(user_id), name))

    async def log_usage_stats(self, user_count: int, platform: str) -> None:
        await self.execute_query("""
            INSERT INTO usage_stats (timestamp, user_count, platform)
            VALUES (DATE_FORMAT(NOW(), '%%Y-%%m-%%d %%H:%%i:00'), %s, %s)
        """, (user_count, platform))

    async def get_user_roles(self, user_id: Union[int, str], platform: str) -> Tuple[Optional[int], Optional[int]]:
        """
        Get the (level, division) of a user based on their TeamSpeak or Discord ID,
        or (None, None) if not found. Reads run autocommit, so results are fresh.
        """
        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'
        rows = await self.execute_query(f"""
            SELECT level, division
            FROM user
            WHERE {id_column} = %s
                AND COALESCE(ranking_disabled, 0) = 0
        """, (str(user_id),))
        return rows[0] if rows else (None, None)

    async def update_login_streak(self, platform_uid: str, platform: str) -> None:
        """Update login streak for a user"""

        async def op(conn):
            await conn.begin()
            try:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        SELECT current_streak, longest_streak, last_login
                        FROM login_streak
                        WHERE platform = %s AND platform_uid = %s
                    """, (platform, str(platform_uid)))
                    result = await cur.fetchone()
                    today = datetime.now().date()
                    if result:
                        current_streak, longest_streak, last_login = result
                    else:
                        current_streak = 0
                        longest_streak = 0
                        last_login = None

                    if last_login == today:
                        pass
                    elif last_login and (today - last_login).days == 1:
                        current_streak += 1
                        longest_streak = max(longest_streak, current_streak)
                    else:
                        current_streak = 1
                        longest_streak = max(longest_streak, current_streak)

                    await cur.execute("""
                        INSERT INTO login_streak
                            (platform_uid, platform, logins, current_streak, longest_streak, last_login)
                        VALUES (%s, %s, 1, %s, %s, %s)
                        ON DUPLICATE KEY UPDATE
                            logins = logins + 1,
                            current_streak = VALUES(current_streak),
                            longest_streak = VALUES(longest_streak),
                            last_login = VALUES(last_login)
                    """, (str(platform_uid), platform, current_streak, longest_streak, today))
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

        await self._run(op)

    async def has_time_entry(self, platform_uid: Union[int, str], platform: str) -> bool:
        """Check if a time entry exists for the given platform UID and platform."""
        if platform not in ('discord', 'teamspeak'):
            raise ValueError("platform must be 'discord' or 'teamspeak'")

        rows = await self.execute_query("""
            SELECT 1 FROM time
            WHERE platform_uid = %s AND platform = %s
            LIMIT 1
        """, (str(platform_uid), platform))
        return bool(rows)

    # -- resets / season ----------------------------------------------------

    async def reset_time(self, period: str):
        """
        Reset time counters for all users for the given period (daily, weekly, monthly)
        and update the reset_log table.
        """
        column = {
            'daily': ('daily_time', 'last_daily_reset'),
            'weekly': ('weekly_time', 'last_weekly_reset'),
            'monthly': ('monthly_time', 'last_monthly_reset'),
        }.get(period)
        if column is None:
            return
        time_column, log_column = column
        now = datetime.now()

        async def op(conn):
            await conn.begin()
            try:
                async with conn.cursor() as cur:
                    await cur.execute(f"UPDATE time SET {time_column} = 0")
                    await cur.execute(f"UPDATE reset_log SET {log_column} = %s WHERE id = 1", (now,))
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

        await self._run(op)

    async def get_last_resets(self):
        rows = await self.execute_query("""
            SELECT last_daily_reset, last_weekly_reset, last_monthly_reset, last_season_reset
            FROM reset_log
            WHERE id = 1
        """)
        return rows[0] if rows else None

    async def close_season(self, closed_at: Optional[datetime] = None) -> dict:
        """
        Award end-of-season markers from the current division state, then reset
        seasonal counters and divisions for the next season.
        """
        closed_at = closed_at or datetime.now()
        season_number = get_season_number_for_end_year(closed_at.year)

        async def op(conn):
            await conn.begin()
            try:
                async with conn.cursor() as cur:
                    await cur.execute("""
                        SELECT
                            u.id,
                            u.discord_id,
                            u.teamspeak_id,
                            COALESCE(u.division, 1) AS division,
                            COALESCE(d.season_time, 0) + COALESCE(t.season_time, 0) AS season_time
                        FROM user u
                        LEFT JOIN time d
                            ON d.platform = 'discord'
                            AND d.platform_uid = u.discord_id
                        LEFT JOIN time t
                            ON t.platform = 'teamspeak'
                            AND t.platform_uid = u.teamspeak_id
                        WHERE COALESCE(u.ranking_disabled, 0) = 0
                            AND COALESCE(d.season_time, 0) + COALESCE(t.season_time, 0) > 0
                        ORDER BY season_time DESC, u.id ASC
                    """)
                    participants = await cur.fetchall()

                    achievement_rows = []
                    for index, (_, discord_id, teamspeak_id, division, _) in enumerate(participants):
                        achievement_types = get_season_division_achievement_types(division, season_number)
                        if index == 0:
                            achievement_types.append(SEASON_APEX_ACHIEVEMENT)

                        platform_ids = []
                        if discord_id:
                            platform_ids.append(('discord', str(discord_id)))
                        if teamspeak_id:
                            platform_ids.append(('teamspeak', str(teamspeak_id)))

                        for platform, platform_id in platform_ids:
                            for achievement_type in achievement_types:
                                achievement_rows.append((platform, platform_id, achievement_type))

                    if achievement_rows:
                        await cur.executemany("""
                            INSERT IGNORE INTO special_achievements
                                (platform, platform_id, achievement_type)
                            VALUES (%s, %s, %s)
                        """, achievement_rows)

                    await cur.execute("UPDATE time SET season_time = 0")
                    await cur.execute("UPDATE user SET division = 1")
                    await cur.execute("""
                        UPDATE reset_log
                        SET last_season_reset = %s
                        WHERE id = 1
                    """, (closed_at,))
                await conn.commit()

                return {
                    'participants': len(participants),
                    'achievement_rows': len(achievement_rows),
                }
            except Exception:
                await conn.rollback()
                raise

        return await self._run(op)

    # ------------------------------------------------------------------ #
    # TS3 -> TS6 identity bridge (Layer 0: myTeamSpeak-id / "mytsid")
    #
    # A user's TS UID changes between TS3 (SHA-1) and TS6 (SHA-256), but their
    # myTeamSpeak account id (client_myteamspeak_id) is identical across both.
    # Captured live on connect while users are still on TS3, it lets us later
    # recognise a returning user on TS6 with zero user action. `teamspeak_id`
    # stays the canonical data key; `teamspeak6_id` is an identification alias.
    # ------------------------------------------------------------------ #

    async def capture_myteamspeak_id(self, ts_uid: Union[str, int], myteamspeak_id: Optional[str]) -> bool:
        """Backfill the stable myTeamSpeak account id for the user identified by a
        connecting TS UID (TS3 or TS6). No-op if the account id is empty (the user
        is not logged into myTeamSpeak) or already stored. Returns True if a row
        was updated."""
        if not myteamspeak_id:
            return False
        ts_uid = str(ts_uid)

        async def op(conn):
            async with conn.cursor() as cur:
                await cur.execute(
                    """
                    UPDATE user
                    SET myteamspeak_id = %s
                    WHERE (teamspeak_id = %s OR teamspeak6_id = %s)
                      AND (myteamspeak_id IS NULL OR myteamspeak_id <> %s)
                    """,
                    (myteamspeak_id, ts_uid, ts_uid, myteamspeak_id),
                )
                return cur.rowcount > 0

        return await self._run(op)

    async def find_user_by_myteamspeak_id(self, myteamspeak_id: Optional[str],
                                          exclude_uid: Optional[Union[str, int]] = None) -> Optional[Tuple]:
        """Return (id, teamspeak_id, teamspeak6_id) of the historical user row for a
        myTeamSpeak account, or None. Optionally ignore a row whose teamspeak_id or
        teamspeak6_id equals exclude_uid (the UID the user is connecting with now),
        so a match means a *different*, prior identity to bridge to."""
        if not myteamspeak_id:
            return None
        params = [myteamspeak_id]
        sql = "SELECT id, teamspeak_id, teamspeak6_id FROM user WHERE myteamspeak_id = %s"
        if exclude_uid is not None:
            exclude_uid = str(exclude_uid)
            sql += " AND COALESCE(teamspeak_id, '') <> %s AND COALESCE(teamspeak6_id, '') <> %s"
            params += [exclude_uid, exclude_uid]
        sql += " ORDER BY id LIMIT 1"

        async def op(conn):
            async with conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                return await cur.fetchone()

        return await self._run(op)

    @staticmethod
    async def _move_ts_uid_keyed_data(cur, source_uid: str, target_uid: str) -> None:
        """Cursor-level (no commit) move of all teamspeak UID-keyed rows from
        source_uid onto target_uid: SUM the counters, GREATEST the timestamps/
        streaks, INSERT IGNORE achievements, then delete the source rows. The
        source table is aliased in every INSERT..SELECT so the ON DUPLICATE KEY
        UPDATE column references stay unambiguous (self-referential merge)."""
        await cur.execute("""
            INSERT INTO time (platform_uid, platform, total_time, daily_time,
                              weekly_time, monthly_time, season_time, last_update)
            SELECT %s, platform, total_time, daily_time, weekly_time,
                   monthly_time, season_time, last_update
            FROM time src
            WHERE src.platform = 'teamspeak' AND src.platform_uid = %s
            ON DUPLICATE KEY UPDATE
                time.total_time   = time.total_time   + VALUES(total_time),
                time.daily_time   = time.daily_time   + VALUES(daily_time),
                time.weekly_time  = time.weekly_time  + VALUES(weekly_time),
                time.monthly_time = time.monthly_time + VALUES(monthly_time),
                time.season_time  = time.season_time  + VALUES(season_time),
                time.last_update  = GREATEST(time.last_update, VALUES(last_update))
        """, (target_uid, source_uid))
        await cur.execute("DELETE FROM time WHERE platform = 'teamspeak' AND platform_uid = %s", (source_uid,))

        await cur.execute("""
            INSERT INTO activity_heatmap (platform_uid, platform, day_of_week,
                                          time_category, activity_minutes, last_update)
            SELECT %s, platform, day_of_week, time_category, activity_minutes, last_update
            FROM activity_heatmap src
            WHERE src.platform = 'teamspeak' AND src.platform_uid = %s
            ON DUPLICATE KEY UPDATE
                activity_heatmap.activity_minutes = activity_heatmap.activity_minutes + VALUES(activity_minutes),
                activity_heatmap.last_update      = GREATEST(activity_heatmap.last_update, VALUES(last_update))
        """, (target_uid, source_uid))
        await cur.execute("DELETE FROM activity_heatmap WHERE platform = 'teamspeak' AND platform_uid = %s", (source_uid,))

        await cur.execute("""
            INSERT INTO login_streak (platform_uid, platform, logins,
                                      current_streak, longest_streak, last_login)
            SELECT %s, platform, logins, current_streak, longest_streak, last_login
            FROM login_streak src
            WHERE src.platform = 'teamspeak' AND src.platform_uid = %s
            ON DUPLICATE KEY UPDATE
                login_streak.logins         = login_streak.logins + VALUES(logins),
                login_streak.current_streak = GREATEST(login_streak.current_streak, VALUES(current_streak)),
                login_streak.longest_streak = GREATEST(login_streak.longest_streak, VALUES(longest_streak)),
                login_streak.last_login     = GREATEST(login_streak.last_login, VALUES(last_login))
        """, (target_uid, source_uid))
        await cur.execute("DELETE FROM login_streak WHERE platform = 'teamspeak' AND platform_uid = %s", (source_uid,))

        await cur.execute("""
            INSERT IGNORE INTO special_achievements (platform, platform_id, achievement_type, awarded_at)
            SELECT platform, %s, achievement_type, awarded_at
            FROM special_achievements src
            WHERE src.platform = 'teamspeak' AND src.platform_id = %s
        """, (target_uid, source_uid))
        await cur.execute("DELETE FROM special_achievements WHERE platform = 'teamspeak' AND platform_id = %s", (source_uid,))

    @staticmethod
    async def _recalculate_teamspeak_rank(cur, user_id: int) -> None:
        """Cursor-level (no commit) recompute of level/division from a user's total
        + season time across both platforms."""
        await cur.execute("""
            SELECT
                COALESCE(SUM(CASE WHEN t.platform = 'discord'   THEN t.total_time  ELSE 0 END), 0) +
                COALESCE(SUM(CASE WHEN t.platform = 'teamspeak' THEN t.total_time  ELSE 0 END), 0),
                COALESCE(SUM(CASE WHEN t.platform = 'discord'   THEN t.season_time ELSE 0 END), 0) +
                COALESCE(SUM(CASE WHEN t.platform = 'teamspeak' THEN t.season_time ELSE 0 END), 0)
            FROM user u
            LEFT JOIN time t ON
                (t.platform = 'discord'   AND t.platform_uid = u.discord_id) OR
                (t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id)
            WHERE u.id = %s
        """, (user_id,))
        total_time, season_time = await cur.fetchone() or (0, 0)
        await cur.execute(
            "UPDATE user SET level = %s, division = %s WHERE id = %s",
            (Config.get_level_for_minutes(total_time or 0),
             Config.get_division_for_minutes(season_time or 0), user_id))

    async def merge_teamspeak_identity(self, canonical_uid: Union[str, int],
                                       absorbed_uid: Union[str, int]) -> dict:
        """Absorb the ``absorbed_uid`` TeamSpeak identity (fresh/placeholder row +
        any accrued UID-keyed data) into the historical ``canonical_uid`` row, so a
        returning user's perks survive a UID change. Transactional and idempotent.

        Guard: refuses to merge when the two rows are linked to *different* Steam
        accounts (would fuse two real people)."""
        canonical_uid, absorbed_uid = str(canonical_uid), str(absorbed_uid)
        if canonical_uid == absorbed_uid:
            return {"merged": False, "reason": "same_uid"}

        async def op(conn):
            await conn.begin()
            try:
                async with conn.cursor() as cur:
                    await cur.execute(
                        "SELECT id, steam_id FROM user WHERE teamspeak_id = %s OR teamspeak6_id = %s FOR UPDATE",
                        (canonical_uid, canonical_uid))
                    canon = await cur.fetchone()
                    if canon is None:
                        await conn.rollback()
                        return {"merged": False, "reason": "canonical_not_found"}
                    canon_id, canon_steam = canon
                    await cur.execute(
                        "SELECT id, steam_id FROM user WHERE (teamspeak_id = %s OR teamspeak6_id = %s) AND id <> %s FOR UPDATE",
                        (absorbed_uid, absorbed_uid, canon_id))
                    absorbed_rows = await cur.fetchall()
                    for _aid, asteam in absorbed_rows:
                        if asteam is not None and canon_steam is not None and str(asteam) != str(canon_steam):
                            await conn.rollback()
                            return {"merged": False, "reason": "cross_account_conflict"}
                    await self._move_ts_uid_keyed_data(cur, absorbed_uid, canonical_uid)
                    # Drop the now-empty placeholder user row(s) for the absorbed UID.
                    await cur.execute(
                        "DELETE FROM user WHERE (teamspeak_id = %s OR teamspeak6_id = %s) AND id <> %s",
                        (absorbed_uid, absorbed_uid, canon_id))
                    await self._recalculate_teamspeak_rank(cur, canon_id)
                await conn.commit()
                return {"merged": True, "canonical_uid": canonical_uid,
                        "absorbed_uid": absorbed_uid, "user_id": canon_id}
            except Exception:
                await conn.rollback()
                raise

        return await self._run(op)

    async def recognize_teamspeak_client(self, connecting_uid: Union[str, int],
                                         myteamspeak_id: Optional[str], is_ts6: bool = False) -> dict:
        """Called on every TS connect. Captures the myTeamSpeak id, and if that
        account has a prior TS identity under a *different* UID, bridges them:
        records the new UID (into teamspeak6_id on TS6) and merges history so the
        user is recognised seamlessly. Returns a summary of what happened."""
        connecting_uid = str(connecting_uid)
        await self.capture_myteamspeak_id(connecting_uid, myteamspeak_id)
        prior = await self.find_user_by_myteamspeak_id(myteamspeak_id, exclude_uid=connecting_uid)
        if not prior:
            return {"recognized": False}
        pid, prior_ts3, prior_ts6 = prior
        canonical_uid = prior_ts3 or prior_ts6
        result = await self.merge_teamspeak_identity(canonical_uid, connecting_uid)
        if is_ts6 and result.get("merged"):
            # Record the new SHA-256 UID as an alias on the historical row.
            await self.execute_query(
                "UPDATE user SET teamspeak6_id = %s WHERE id = %s AND (teamspeak6_id IS NULL OR teamspeak6_id <> %s)",
                (connecting_uid, pid, connecting_uid))
        result["recognized"] = result.get("merged", False)
        result["canonical_uid"] = canonical_uid
        return result

    # -- TTT ----------------------------------------------------------------

    async def get_ttt_player_stats(self, steam_id: Union[int, str]) -> dict:
        steam_id = _require_steam_id64(steam_id)
        rows = await self.execute_query("""
            SELECT
                steam_id,
                last_ttt_name,
                rounds_played,
                rounds_won,
                innocent_wins,
                detective_wins,
                traitor_wins,
                kills,
                deaths,
                last_played_at
            FROM ttt_player_stats
            WHERE steam_id = %s
        """, (steam_id,))
        return ttt_stats_from_row(rows[0] if rows else None, steam_id)

    async def ingest_ttt_achievement_event(self, payload: dict) -> dict:
        event = normalize_ttt_achievement_payload(payload)
        emitted_at = parse_ttt_emitted_at(event.get('emitted_at'))
        innocent_wins, detective_wins, traitor_wins = _ttt_win_breakdown(event)

        await self.execute_query("""
            INSERT INTO ttt_player_stats (
                steam_id,
                last_ttt_name,
                rounds_played,
                rounds_won,
                innocent_wins,
                detective_wins,
                traitor_wins,
                kills,
                deaths,
                last_played_at
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                last_ttt_name = CASE
                    WHEN VALUES(last_ttt_name) IS NULL OR VALUES(last_ttt_name) = '' THEN last_ttt_name
                    ELSE VALUES(last_ttt_name)
                END,
                rounds_played = rounds_played + VALUES(rounds_played),
                rounds_won = rounds_won + VALUES(rounds_won),
                innocent_wins = innocent_wins + VALUES(innocent_wins),
                detective_wins = detective_wins + VALUES(detective_wins),
                traitor_wins = traitor_wins + VALUES(traitor_wins),
                kills = kills + VALUES(kills),
                deaths = deaths + VALUES(deaths),
                last_played_at = CASE
                    WHEN last_played_at IS NULL OR VALUES(last_played_at) > last_played_at
                        THEN VALUES(last_played_at)
                    ELSE last_played_at
                END,
                updated_at = CURRENT_TIMESTAMP
        """, (
            event['steam_id64'],
            event['name'],
            event['rounds_played'],
            event['rounds_won'],
            innocent_wins,
            detective_wins,
            traitor_wins,
            event['kills'],
            event['deaths'],
            emitted_at,
        ))
        return {'ok': True, 'event_id': event['event_id']}


_shared_instance: Optional[AsyncDatabaseManager] = None


def get_async_db() -> AsyncDatabaseManager:
    """Shared AsyncDatabaseManager for the bot process (lazy; pool connects on
    first use inside the running loop)."""
    global _shared_instance
    if _shared_instance is None:
        _shared_instance = AsyncDatabaseManager()
    return _shared_instance
