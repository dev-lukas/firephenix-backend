from datetime import datetime
from typing import List, Optional, Set, Tuple, Union, Callable
import mariadb
from app.utils.logger import RankingLogger
from app.config import Config

logging = RankingLogger(__name__).get_logger()

__all__ = ['DatabaseManager']

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
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_steam (steam_id),
                    INDEX idx_discord (discord_id),
                    INDEX idx_teamspeak (teamspeak_id)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
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
                CREATE TABLE IF NOT EXISTS reset_log (
                    id INT PRIMARY KEY DEFAULT 1,
                    last_daily_reset DATETIME,
                    last_weekly_reset DATETIME,
                    last_monthly_reset DATETIME
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)
            self.cursor.execute("""
                INSERT IGNORE INTO reset_log (id, last_daily_reset, last_weekly_reset, last_monthly_reset)
                VALUES (1, NULL, NULL, NULL)
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
            
        if query.strip().upper().startswith('SELECT'):
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
            WHERE u.{id_column} IN ({placeholders})
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
            WHERE u.{id_column} IN ({placeholders})
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
            WHERE u.division IN (5, 6) AND u.{id_column} IS NOT NULL
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
        self.cursor.execute("SELECT last_daily_reset, last_weekly_reset, last_monthly_reset FROM reset_log WHERE id = 1")
        return self.cursor.fetchone()

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

    def close(self) -> None:
        """Close database connection"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
        except mariadb.Error as e:
            logging.error(f"Error closing database connection: {e}")
