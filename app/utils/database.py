from datetime import datetime, timezone
from typing import List, Optional, Set, Tuple, Union, Callable, Iterable
import mariadb
from app.utils.logger import RankingLogger
from app.config import Config

logging = RankingLogger(__name__).get_logger()

__all__ = [
    'DatabaseManager',
    'build_ttt_achievement_payload',
    'get_ttt_season_reward_item_uuid',
    'get_ttt_season_reward_key',
    'get_ttt_season_skin_unlockable_type',
    'normalize_ttt_achievement_payload',
    'parse_ttt_season_skin_unlockable_type',
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


def get_ttt_season_reward_item_uuid(season_number: int, tier: int) -> Optional[str]:
    if tier not in range(2, 7):
        return None

    return Config.TTT_SEASON_REWARD_ITEM_UUIDS.get(season_number, {}).get(tier)


def get_ttt_season_reward_key(season_number: int, tier: int) -> str:
    return f'season_{season_number}_tier_{tier}'


def get_ttt_season_skin_unlockable_type(season_number: int, tier: int) -> int:
    return (season_number * 10) + tier


def parse_ttt_season_skin_unlockable_type(unlockable_type: int) -> Tuple[int, int] | None:
    season_number = unlockable_type // 10
    tier = unlockable_type % 10

    if season_number < 1 or tier not in range(2, 7):
        return None

    return season_number, tier


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
    """Synchronous manager for the Flask API (gunicorn workers are sync).

    The bot-side business methods (time/rank/streak tracking, season close,
    the TS3->TS6 identity bridge, TTT ingest) live in
    ``app.utils.async_database.AsyncDatabaseManager`` — the bot process runs
    on one asyncio loop and must not block on mariadb. Placeholders here are
    ``?`` (mariadb connector); the async manager uses ``%s`` (asyncmy).
    """

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
            # Stable myTeamSpeak account id, captured live from client_myteamspeak_id on
            # connect. Unlike teamspeak_id (the SHA-1/SHA-256 UID) it is identical across
            # TS3 and TS6, so it bridges a returning user's old UID to their new one.
            self.cursor.execute("""
                ALTER TABLE user
                ADD COLUMN IF NOT EXISTS myteamspeak_id VARCHAR(255) NULL
            """)
            self.cursor.execute("""
                ALTER TABLE user
                ADD INDEX IF NOT EXISTS idx_myteamspeak (myteamspeak_id)
            """)
            # TeamSpeak 6 UID (SHA-256 fingerprint). Distinct from teamspeak_id, which holds
            # the legacy TS3 UID (SHA-1). Kept separate so both can coexist during the TS3→TS6
            # transition (either can identify a user); populated when a user is recognised on TS6.
            self.cursor.execute("""
                ALTER TABLE user
                ADD COLUMN IF NOT EXISTS teamspeak6_id VARCHAR(255) NULL
            """)
            self.cursor.execute("""
                ALTER TABLE user
                ADD INDEX IF NOT EXISTS idx_teamspeak6 (teamspeak6_id)
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
                    attempts INT NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    INDEX idx_user_platform (steam_id, platform),
                    INDEX idx_expires (expires_at)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)
            self.cursor.execute("""
                ALTER TABLE verification
                ADD COLUMN IF NOT EXISTS attempts INT NOT NULL DEFAULT 0
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
                    UNIQUE KEY unique_user_unlockable (steam_id, platform, unlockable_type),
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

    def close(self) -> None:
        """Close database connection"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
        except mariadb.Error as e:
            logging.error(f"Error closing database connection: {e}")
