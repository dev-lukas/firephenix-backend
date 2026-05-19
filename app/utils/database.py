from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple, Union, Callable, Iterable
import mariadb
from app.utils.logger import RankingLogger
from app.config import Config

logging = RankingLogger(__name__).get_logger()

__all__ = [
    'DatabaseManager',
    'build_ttt_achievement_payload',
    'normalize_ttt_achievement_payload',
    'sum_ttt_achievement_levels',
    'zero_ttt_player_stats',
]

SEASON_RESET_MONTH = 6
SEASON_RESET_DAY = 1
SEASON_ONE_END_YEAR = 2026
SEASON_DIVISION_ACHIEVEMENT_BASE = 1000
SEASON_DIVISION_ACHIEVEMENT_STEP = 10
SEASON_APEX_ACHIEVEMENT = 200


def get_season_number_for_end_year(end_year: int) -> int:
    return max(1, end_year - SEASON_ONE_END_YEAR + 1)


def get_season_division_achievement_base(season_number: int) -> int:
    return SEASON_DIVISION_ACHIEVEMENT_BASE + ((max(1, season_number) - 1) * SEASON_DIVISION_ACHIEVEMENT_STEP)


def get_season_division_achievement_types(division: int, season_number: int = 1) -> List[int]:
    """Return cumulative season division achievement markers for a division."""
    if division is None:
        return []

    base = get_season_division_achievement_base(season_number)
    capped_division = max(0, min(int(division), 6))
    return [
        base + earned_division
        for earned_division in range(1, capped_division + 1)
    ]


def get_division_from_season_achievement_type(achievement_type: int, season_number: int = 1) -> int:
    base = get_season_division_achievement_base(season_number)
    division = achievement_type - base
    return division if 1 <= division <= 6 else 0


def get_best_division_from_season_achievements(
    achievement_types: Iterable[int],
    season_number: int = 1,
) -> int:
    return max(
        (get_division_from_season_achievement_type(achievement_type, season_number)
         for achievement_type in achievement_types),
        default=0,
    )


def is_season_division_achievement_type(achievement_type: int) -> bool:
    return achievement_type >= SEASON_DIVISION_ACHIEVEMENT_BASE and 1 <= (achievement_type % 10) <= 6


def can_claim_season_skin(best_division: int, tier: int) -> bool:
    return tier in range(2, 7) and best_division >= tier


def can_upgrade_apex_channel(level: int, achievement_types: Iterable[int]) -> bool:
    return level >= 25 or SEASON_APEX_ACHIEVEMENT in set(achievement_types)


TTT_EVENT_COUNTER_FIELDS = ('rounds_played', 'rounds_won', 'kills', 'deaths')
TTT_EVENT_REQUIRED_FIELDS = {
    'version',
    'event_id',
    'server',
    'round_id',
    'steam_id64',
    'name',
    'map',
    'base_role',
    'sub_role',
    'team',
    'win_team',
    'rounds_played',
    'rounds_won',
    'kills',
    'deaths',
    'emitted_at',
}


def zero_ttt_player_stats(steam_id: str | int | None = None) -> dict:
    stats = {
        'steam_id': str(steam_id) if steam_id else None,
        'last_ttt_name': None,
        'rounds_played': 0,
        'rounds_won': 0,
        'innocent_wins': 0,
        'detective_wins': 0,
        'traitor_wins': 0,
        'kills': 0,
        'deaths': 0,
        'last_played_at': None,
    }
    return stats


def ttt_stats_from_row(row, steam_id: str | int | None = None) -> dict:
    if not row:
        return zero_ttt_player_stats(steam_id)

    stats = {
        'steam_id': str(row[0]) if row[0] is not None else (str(steam_id) if steam_id else None),
        'last_ttt_name': row[1],
        'rounds_played': int(row[2] or 0),
        'rounds_won': int(row[3] or 0),
        'innocent_wins': int(row[4] or 0),
        'detective_wins': int(row[5] or 0),
        'traitor_wins': int(row[6] or 0),
        'kills': int(row[7] or 0),
        'deaths': int(row[8] or 0),
        'last_played_at': row[9].isoformat() if hasattr(row[9], 'isoformat') else row[9],
    }
    return stats


def build_ttt_achievement_payload(stats: dict) -> dict:
    stats = stats or zero_ttt_player_stats()
    levels = Config.get_ttt_achievement_levels(stats)
    return {
        'stats': stats,
        'achievements': levels,
        'achievement_level': sum(levels.values()),
    }


def sum_ttt_achievement_levels(stats: dict) -> int:
    return sum(Config.get_ttt_achievement_levels(stats).values())


def _require_steam_id64(value) -> str:
    steam_id = str(value or '')
    if len(steam_id) != 17 or not steam_id.isdigit():
        raise ValueError('invalid_steam_id64')
    return steam_id


def _int_counter(payload: dict, field: str) -> int:
    try:
        value = int(payload.get(field))
    except (TypeError, ValueError):
        raise ValueError(f'invalid_{field}')
    if value < 0:
        raise ValueError(f'invalid_{field}')
    return value


def parse_ttt_emitted_at(value) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value

    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, timezone.utc).replace(tzinfo=None)

    text = str(value or '').strip()
    if not text:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    if text.endswith('Z'):
        text = text[:-1] + '+00:00'

    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def normalize_ttt_achievement_payload(payload: dict) -> dict:
    if not isinstance(payload, dict):
        raise ValueError('event_not_object')

    missing = TTT_EVENT_REQUIRED_FIELDS - payload.keys()
    if missing:
        raise ValueError(f"missing_fields:{','.join(sorted(missing))}")

    if payload.get('version') != 1:
        raise ValueError('invalid_version')

    event_id = str(payload.get('event_id') or '')
    if not event_id or len(event_id) > 160:
        raise ValueError('invalid_event_id')

    win_team = str(payload.get('win_team') or '')
    if win_team not in {'traitor', 'innocent', 'none'}:
        raise ValueError('invalid_win_team')

    normalized = {
        'version': 1,
        'event_id': event_id,
        'server': str(payload.get('server') or ''),
        'round_id': str(payload.get('round_id') or ''),
        'steam_id64': _require_steam_id64(payload.get('steam_id64')),
        'name': str(payload.get('name') or ''),
        'map': str(payload.get('map') or ''),
        'base_role': payload.get('base_role'),
        'sub_role': payload.get('sub_role'),
        'team': str(payload.get('team') or ''),
        'win_team': win_team,
        'emitted_at': payload.get('emitted_at'),
    }

    for field in TTT_EVENT_COUNTER_FIELDS:
        normalized[field] = _int_counter(payload, field)

    if normalized['rounds_played'] not in {0, 1}:
        raise ValueError('invalid_rounds_played')
    if normalized['rounds_won'] not in {0, 1}:
        raise ValueError('invalid_rounds_won')

    return normalized


def _role_id(value) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ttt_win_breakdown(event: dict) -> tuple[int, int, int]:
    if int(event.get('rounds_won') or 0) <= 0:
        return 0, 0, 0

    if event.get('win_team') == 'traitor':
        return 0, 0, 1

    if event.get('win_team') == 'innocent':
        base_role = _role_id(event.get('base_role'))
        if base_role == 2:
            return 0, 1, 0
        return 1, 0, 0

    return 0, 0, 0


class DatabaseConnectionError(Exception):
    """Custom exception for database connection errors."""
    def __init__(self, message="Failed to reconnect to the database"):
        super().__init__(message)

class DatabaseManager:
    def __init__(self):
        self.conn = None
        self.cursor = None
        self.connect()

    def connect(self) -> bool:
        """Establish database connection"""
        try:
            if self.conn:
                self.close()
            
            self.conn = mariadb.connect(
                host=Config.DB_HOST,
                port=int(Config.DB_PORT),
                user=Config.DB_USER,
                password=Config.DB_PASSWORD,
                database=Config.DB_NAME,
            )
            self.conn.autocommit = False
            self.cursor = self.conn.cursor()
            self.create_tables()
            return True
        except mariadb.Error as e:
            logging.error(f"Error connecting to database: {e}")
            return False

    def ensure_connection(func: Callable):
        """Decorator to ensure database connection"""
        def wrapper(self, *args, **kwargs):
            if self.conn is None or self.cursor is None:
                if not self.connect():
                    raise DatabaseConnectionError("No database connection available")
            try:
                return func(self, *args, **kwargs)
            except (mariadb.Error, AttributeError):
                if self.connect():
                    return func(self, *args, **kwargs)
                raise DatabaseConnectionError("Failed to reconnect to database")
        return wrapper

    def create_tables(self):
        try:
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS user (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    steam_id BIGINT UNIQUE,
                    discord_id VARCHAR(255) UNIQUE,
                    teamspeak_id VARCHAR(255) UNIQUE,
                    name VARCHAR(255),
                    level INT DEFAULT 1,
                    division INT DEFAULT 1,
                    discord_channel BIGINT,
                    teamspeak_channel BIGINT,
                    discord_moveable BOOL DEFAULT 1,
                    teamspeak_moveable BOOL DEFAULT 1,
                    ranking_disabled BOOLEAN DEFAULT 0,
                    ranking_disabled_at TIMESTAMP NULL,
                    ranking_disabled_reason VARCHAR(255) NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_steam (steam_id),
                    INDEX idx_discord (discord_id),
                    INDEX idx_teamspeak (teamspeak_id),
                    INDEX idx_ranking_disabled (ranking_disabled)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)
            self.cursor.execute("""
                ALTER TABLE user
                ADD COLUMN IF NOT EXISTS ranking_disabled BOOLEAN DEFAULT 0
            """)
            self.cursor.execute("""
                ALTER TABLE user
                ADD COLUMN IF NOT EXISTS ranking_disabled_at TIMESTAMP NULL
            """)
            self.cursor.execute("""
                ALTER TABLE user
                ADD COLUMN IF NOT EXISTS ranking_disabled_reason VARCHAR(255) NULL
            """)
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS time (
                    platform_uid VARCHAR(255) NOT NULL,
                    platform ENUM('discord', 'teamspeak') NOT NULL,
                    total_time INT DEFAULT 0,
                    daily_time INT DEFAULT 0,
                    weekly_time INT DEFAULT 0,
                    monthly_time INT DEFAULT 0,
                    season_time INT DEFAULT 0,
                    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (platform, platform_uid)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS usage_stats (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    timestamp DATETIME NOT NULL,
                    user_count INT NOT NULL,
                    platform ENUM('discord', 'teamspeak') NOT NULL,
                    INDEX idx_timestamp (timestamp),
                    INDEX idx_platform (platform)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS activity_heatmap (
                    platform_uid VARCHAR(255) NOT NULL,
                    platform ENUM('discord', 'teamspeak') NOT NULL,
                    day_of_week TINYINT NOT NULL CHECK (day_of_week BETWEEN 0 AND 6),
                    time_category ENUM('morning', 'noon', 'evening', 'night') NOT NULL,
                    activity_minutes INT DEFAULT 0,
                    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (platform, platform_uid, day_of_week, time_category)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS verification (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    steam_id BIGINT NOT NULL,
                    platform_id VARCHAR(255) NOT NULL,
                    platform ENUM('discord', 'teamspeak') NOT NULL,
                    verification_code VARCHAR(6) NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    INDEX idx_user_platform (steam_id, platform),
                    INDEX idx_expires (expires_at)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS login_streak (
                    platform_uid VARCHAR(255) NOT NULL,
                    platform ENUM('discord', 'teamspeak') NOT NULL,
                    logins INT DEFAULT 1,
                    current_streak INT DEFAULT 1,
                    longest_streak INT DEFAULT 1,
                    last_login DATE NOT NULL,
                    PRIMARY KEY (platform, platform_uid)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS special_achievements (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    platform ENUM('discord', 'teamspeak') NOT NULL,
                    platform_id VARCHAR(255) NOT NULL,
                    achievement_type INT NOT NULL,
                    awarded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_user_achievement (platform, platform_id, achievement_type),
                    INDEX idx_platform_id (platform_id),
                    INDEX idx_achievement_type (achievement_type)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS unlockables (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    steam_id BIGINT NOT NULL,
                    platform ENUM('discord', 'teamspeak', 'gameserver') NOT NULL,
                    unlockable_type INT NOT NULL,
                    unlocked_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE KEY unique_user_unlockable (steam_id, unlockable_type),
                    INDEX idx_platform_uid (steam_id),
                    INDEX idx_unlockable_type (unlockable_type)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS ttt_player_stats (
                    steam_id BIGINT NOT NULL PRIMARY KEY,
                    last_ttt_name VARCHAR(255),
                    rounds_played INT NOT NULL DEFAULT 0,
                    rounds_won INT NOT NULL DEFAULT 0,
                    innocent_wins INT NOT NULL DEFAULT 0,
                    detective_wins INT NOT NULL DEFAULT 0,
                    traitor_wins INT NOT NULL DEFAULT 0,
                    kills INT NOT NULL DEFAULT 0,
                    deaths INT NOT NULL DEFAULT 0,
                    last_played_at TIMESTAMP NULL,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
                    INDEX idx_ttt_last_played_at (last_played_at)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)

            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS reset_log (
                    id INT PRIMARY KEY DEFAULT 1,
                    last_daily_reset DATETIME,
                    last_weekly_reset DATETIME,
                    last_monthly_reset DATETIME,
                    last_season_reset DATETIME
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS admin_audit_log (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    admin_steam_id VARCHAR(32) NOT NULL,
                    action VARCHAR(64) NOT NULL,
                    target_identifiers JSON,
                    summary JSON,
                    result_status ENUM('success', 'failed') NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_admin_audit_created (created_at),
                    INDEX idx_admin_audit_action (action),
                    INDEX idx_admin_audit_admin (admin_steam_id)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)
            self.cursor.execute("""
                ALTER TABLE reset_log
                ADD COLUMN IF NOT EXISTS last_season_reset DATETIME
            """)
            self.cursor.execute("""
                INSERT IGNORE INTO reset_log (
                    id, last_daily_reset, last_weekly_reset, last_monthly_reset, last_season_reset
                )
                VALUES (1, NULL, NULL, NULL, NULL)
            """)

            self.conn.commit()
        except mariadb.Error as e:
            logging.error(f"Error creating tables: {e}")
            self.conn.rollback()
            raise
    
    @ensure_connection
    def execute_query(self, query: str, params: tuple = None) -> Optional[List[Tuple]]:
        """
        Generic wrapper for executing SQL queries
        Returns query results or raises MariaDB error
        """
        if params:
            self.cursor.execute(query, params)
        else:
            self.cursor.execute(query)
            
        query_type = query.strip().upper()
        if query_type.startswith('SELECT') or query_type.startswith('WITH'):
            return self.cursor.fetchall()
        else:
            self.conn.commit()
            return None

    @ensure_connection 
    def update_times(self, platform_uids: Set[Union[int, str]], platform: str) -> None:
        """Batch update time values for multiple users"""
        if not platform_uids:
            return
            
        unique_uids = [str(uid) for uid in platform_uids]
        params = [(uid,platform) for uid in unique_uids]
        query = f"""
            INSERT INTO time (platform_uid, platform, total_time, daily_time,
                            weekly_time, monthly_time, season_time, last_update)
            VALUES {','.join(['(?, ?, 1, 1, 1, 1, 1, CURRENT_TIMESTAMP)'] * len(platform_uids))}
            ON DUPLICATE KEY UPDATE
                total_time = total_time + 1,
                daily_time = daily_time + 1,
                weekly_time = weekly_time + 1,
                monthly_time = monthly_time + 1,
                season_time = season_time + 1,
                last_update = CURRENT_TIMESTAMP
        """
        flat_params = [item for pair in params for item in pair]
        self.cursor.execute(query, flat_params)
        self.conn.commit()
    

    def get_time_category(self, hour: int) -> str:
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
            
    @ensure_connection
    def update_heatmap(self, platform_uids: Set[Union[int, str]], platform: str):
        """
        Update the activity heatmap for multiple platform UIDs
        
        Args:
            platform: Platform name ('discord' or 'teamspeak')
            platform_uids: Set of platform user IDs
        """
        if not platform_uids:
            return
            
        now = datetime.now()
        day_of_week = now.weekday()
        time_category = self.get_time_category(now.hour)
        
        values = []
        for uid in platform_uids:
            values.append((str(uid), platform, day_of_week, time_category))
        
        if not values:
            return
            
        self.cursor.executemany("""
            INSERT INTO activity_heatmap 
                (platform_uid, platform, day_of_week, time_category, activity_minutes)
            VALUES (?, ?, ?, ?, 1)
            ON DUPLICATE KEY UPDATE
                activity_minutes = activity_minutes + 1,
                last_update = CURRENT_TIMESTAMP
        """, values)
        self.conn.commit()

    @ensure_connection
    def update_ranks(self, users: Set[Union[int, str]], platform: str) -> List[Tuple[Union[int, str], int]]:
        rankups = []
        
        user_ids = list(users)
        
        if not user_ids:
            return rankups

        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'

        placeholders = ','.join(['?'] * len(user_ids))
        query = f"""
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
        """
        
        self.cursor.execute(query, user_ids)
        results = self.cursor.fetchall()

        for platform_uid, total_time, level in results:
            calculated_level = Config.get_level_for_minutes(total_time)
            if calculated_level != level:
                update_query = f"""
                    UPDATE user
                    SET level = ?
                    WHERE {id_column} = ?
                """
                self.cursor.execute(update_query, 
                                (calculated_level, platform_uid))
                rankups.append((platform_uid, calculated_level))

        self.conn.commit()

        return rankups
    
    @ensure_connection
    def update_seasonal_ranks(self, users: Set[Union[int, str]], platform: str) -> List[Tuple[Union[int, str], int]]:
        rankups = []
        
        user_ids = list(users)
        
        if not user_ids:
            return rankups

        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'

        placeholders = ','.join(['?'] * len(user_ids))
        query = f"""
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
        """
        
        self.cursor.execute(query, user_ids)
        results = self.cursor.fetchall()

        for platform_uid, season_time, division in results:
            calculated_division = Config.get_division_for_minutes(season_time)
            if calculated_division != division and division <= 5:
                update_query = f"""
                    UPDATE user
                    SET division = ?
                    WHERE {id_column} = ?
                """
                self.cursor.execute(update_query, 
                                (calculated_division, platform_uid))
                rankups.append((platform_uid, calculated_division))
                logging.debug(f"Updated {platform} user {platform_uid} to division {calculated_division}")

        rankups = self._update_top_division_ranks(platform, rankups)

        self.conn.commit()

        if rankups:
            logging.debug(f"Rank updates for {platform} users: {rankups}")
        return rankups

    @ensure_connection
    def _update_top_division_ranks(self, platform: str, rankups: List[Tuple[Union[int, str], int]]) -> List[Tuple[Union[int, str], int]]:
        """
        Update the top division (Division 6) based on season time.
        Only the top Config.TOP_DIVISION_PLAYER_AMOUNT players can be in Division 6.
        """
        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'

        self.cursor.execute(f"""
            SELECT u.id, u.{id_column}, COALESCE(d.season_time, 0) + COALESCE(t.season_time, 0) AS season_time, u.division
            FROM user u
            LEFT JOIN time d ON d.platform = 'discord' AND d.platform_uid = u.discord_id
            LEFT JOIN time t ON t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id
            WHERE COALESCE(u.ranking_disabled, 0) = 0
                AND u.division IN (5, 6) AND u.{id_column} IS NOT NULL
            ORDER BY season_time DESC
            LIMIT {Config.TOP_DIVISION_PLAYER_AMOUNT * 2}
        """)
        all_players = self.cursor.fetchall()
        
        for idx, (user_id, platform_uid, season_time, current_division) in enumerate(all_players):
            target_division = 6 if idx < Config.TOP_DIVISION_PLAYER_AMOUNT else 5
            
            if current_division != target_division:
                self.cursor.execute("""
                    UPDATE user SET division = ? WHERE id = ?
                """, (target_division, user_id))
                
                if target_division == 6:
                    logging.debug(f"Promoted user {platform_uid} to Division 6")
                    rankups.append((platform_uid, 6))
                else:
                    logging.debug(f"Demoted user {platform_uid} to Division 5")
                    rankups.append((platform_uid, 5))
        
        self.conn.commit()
        return rankups
    
    @ensure_connection
    def update_user_name(self, user_id: str, name: str, platform: str) -> None:
        """
        Insert user if not exists, update name if changed
        platform should be either 'discord' or 'teamspeak'
        """
        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'
        query = f"""
            INSERT INTO user
                ({id_column}, name, created_at) 
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON DUPLICATE KEY UPDATE 
                name = VALUES(name)
        """
        self.execute_query(query, (str(user_id), name))

    @ensure_connection
    def log_usage_stats(self, user_count: int, platform: str) -> None:
        query = """
            INSERT INTO usage_stats (timestamp, user_count, platform)
            VALUES (DATE_FORMAT(NOW(), '%Y-%m-%d %H:%i:00'), ?, ?)
        """
        self.execute_query(query, (user_count, platform))

    @ensure_connection
    def get_user_roles(self, user_id: Union[int, str], platform: str) -> Tuple[Optional[int], Optional[int]]:
        """
        Get the rank (level) of a user based on their TeamSpeak or Discord ID.
        Forces fresh results by ensuring any pending transactions are committed.

        Args:
            user_id: The user's unique identifier (TeamSpeak UID or Discord ID)
            platform: The platform ('discord' or 'teamspeak')

        Returns:
            Tuple[Optional[int], Optional[int]]: The user's (level, division) or (None, None) if not found
        """
        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'

        # Force fresh data by committing any pending transactions
        if not self.conn.autocommit:
            self.conn.commit()

        query = f"""
            SELECT level, division
            FROM user
            WHERE {id_column} = ?
                AND COALESCE(ranking_disabled, 0) = 0
        """
        self.cursor.execute(query, (str(user_id),))
        
        result = self.cursor.fetchone()
        return result if result else (None, None)

    @ensure_connection
    def update_login_streak(self, platform_uid: str, platform: str) -> None:
        """Update login streak for a user"""
        self.cursor.execute("""
            SELECT current_streak, longest_streak, last_login 
            FROM login_streak 
            WHERE platform = ? AND platform_uid = ?
        """, (platform, str(platform_uid)))
        
        result = self.cursor.fetchone()
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
        
        self.cursor.execute("""
            INSERT INTO login_streak
                (platform_uid, platform, logins, current_streak, longest_streak, last_login) 
            VALUES (?, ?, 1, ?, ?, ?)
            ON DUPLICATE KEY UPDATE
                logins = logins + 1,
                current_streak = VALUES(current_streak),
                longest_streak = VALUES(longest_streak),
                last_login = VALUES(last_login)
        """, (str(platform_uid), platform, current_streak, longest_streak, today))
        
        self.conn.commit()

    @ensure_connection
    def reset_time(self, period: str):
        """
        Reset time counters for all users for the given period (daily, weekly, monthly)
        and update the reset_log table.
        """
        now = datetime.now()
        if period == 'daily':
            self.cursor.execute("""
                UPDATE time SET daily_time = 0
            """)
            self.cursor.execute("""
                UPDATE reset_log SET last_daily_reset = ? WHERE id = 1
            """, (now,))
        elif period == 'weekly':
            self.cursor.execute("""
                UPDATE time SET weekly_time = 0
            """)
            self.cursor.execute("""
                UPDATE reset_log SET last_weekly_reset = ? WHERE id = 1
            """, (now,))
        elif period == 'monthly':
            self.cursor.execute("""
                UPDATE time SET monthly_time = 0
            """)
            self.cursor.execute("""
                UPDATE reset_log SET last_monthly_reset = ? WHERE id = 1
            """, (now,))
        self.conn.commit()

    @ensure_connection
    def get_last_resets(self):
        self.cursor.execute("""
            SELECT last_daily_reset, last_weekly_reset, last_monthly_reset, last_season_reset
            FROM reset_log
            WHERE id = 1
        """)
        return self.cursor.fetchone()

    @ensure_connection
    def close_season(self, closed_at: Optional[datetime] = None) -> dict:
        """
        Award end-of-season markers from the current division state, then reset
        seasonal counters and divisions for the next season.
        """
        closed_at = closed_at or datetime.now()
        season_number = get_season_number_for_end_year(closed_at.year)

        try:
            self.cursor.execute("""
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
            participants = self.cursor.fetchall()

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
                self.cursor.executemany("""
                    INSERT IGNORE INTO special_achievements
                        (platform, platform_id, achievement_type)
                    VALUES (?, ?, ?)
                """, achievement_rows)

            self.cursor.execute("UPDATE time SET season_time = 0")
            self.cursor.execute("UPDATE user SET division = 1")
            self.cursor.execute("""
                UPDATE reset_log
                SET last_season_reset = ?
                WHERE id = 1
            """, (closed_at,))
            self.conn.commit()

            return {
                'participants': len(participants),
                'achievement_rows': len(achievement_rows),
            }
        except mariadb.Error:
            self.conn.rollback()
            raise

    @ensure_connection
    def get_platform_ids(self, platform: str, platform_id: str) -> Tuple[Optional[str], Optional[str]]:
        """
        Given a platform ('discord' or 'teamspeak') and the corresponding platform_id,
        fetch the user and return a tuple (discord_id, teamspeak_id).
        If one of the IDs is not set, return None for that value.
        """
        if platform not in ('discord', 'teamspeak'):
            raise ValueError("platform must be 'discord' or 'teamspeak'")
        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'
        query = f"""
            SELECT discord_id, teamspeak_id
            FROM user
            WHERE {id_column} = ?
        """
        self.cursor.execute(query, (str(platform_id),))
        result = self.cursor.fetchone()
        if result:
            return result[0], result[1]
        else:
            return None, None

    @ensure_connection
    def has_time_entry(self, platform_uid: Union[int, str], platform: str) -> bool:
        """
        Check if a time entry exists for the given platform UID and platform.
        
        Args:
            platform_uid: The platform user ID (Discord ID or TeamSpeak UID)
            platform: The platform ('discord' or 'teamspeak')
            
        Returns:
            bool: True if a time entry exists, False otherwise
        """
        if platform not in ('discord', 'teamspeak'):
            raise ValueError("platform must be 'discord' or 'teamspeak'")
            
        query = """
            SELECT 1 FROM time 
            WHERE platform_uid = ? AND platform = ?
            LIMIT 1
        """
        self.cursor.execute(query, (str(platform_uid), platform))
        result = self.cursor.fetchone()
        return result is not None

    @ensure_connection
    def get_ttt_player_stats(self, steam_id: Union[int, str]) -> dict:
        steam_id = _require_steam_id64(steam_id)
        self.cursor.execute("""
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
            WHERE steam_id = ?
        """, (steam_id,))
        return ttt_stats_from_row(self.cursor.fetchone(), steam_id)

    @ensure_connection
    def ingest_ttt_achievement_event(self, payload: dict) -> dict:
        event = normalize_ttt_achievement_payload(payload)
        emitted_at = parse_ttt_emitted_at(event.get('emitted_at'))
        innocent_wins, detective_wins, traitor_wins = _ttt_win_breakdown(event)

        try:
            self.cursor.execute("""
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
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            self.conn.commit()
            return {'ok': True, 'event_id': event['event_id']}
        except mariadb.Error:
            self.conn.rollback()
            raise

    def close(self) -> None:
        """Close database connection"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
        except mariadb.Error as e:
            logging.error(f"Error closing database connection: {e}")
