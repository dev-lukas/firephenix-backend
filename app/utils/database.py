from datetime import datetime
import sys
from typing import List, Optional, Set, Tuple, Union
import mariadb
from app.utils.logger import RankingLogger
from app.config import Config

logging = RankingLogger(__name__).get_logger()

__all__ = ['DatabaseManager']

class DatabaseManager:
    def __init__(self):
        try:
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
        except mariadb.Error as e:
            logging.error(f"Error connecting to the database: {e}")
            sys.exit(1)

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
                    current_streak INT DEFAULT 1,
                    longest_streak INT DEFAULT 1,
                    last_login DATE NOT NULL,
                    PRIMARY KEY (platform, platform_uid)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci;
            """)
            self.conn.commit()
        except mariadb.Error as e:
            logging.error(f"Error creating tables: {e}")
            self.conn.rollback()
            raise
    
    def execute_query(self, query: str, params: tuple = None) -> Optional[List[Tuple]]:
        """
        Generic wrapper for executing SQL queries
        Returns query results or None on error
        """
        try:
            if params:
                self.cursor.execute(query, params)
            else:
                self.cursor.execute(query)
                
            if query.strip().upper().startswith('SELECT'):
                return self.cursor.fetchall()
            else:
                self.conn.commit()
                return None
                
        except mariadb.Error as e:
            logging.error(f"Query execution error: {e}")
            self.conn.rollback()
            return None

    def update_times(self, platform_uids: Set[Union[int, str]], platform: str) -> None:
        """Batch update time values for multiple users"""
        if not platform_uids:
            return
        try:
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
        except mariadb.Error as e:
            logging.error(f"Error updating times: {e}")
            self.conn.rollback()
            raise
    

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
        
        try:
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
        except mariadb.Error as e:
            logging.error(f"Error updating times: {e}")
            self.conn.rollback()
            raise

    def update_ranks(self, users: Set[Union[int, str]], platform: str) -> List[Tuple[Union[int, str], int]]:
        rankups = []
        
        try:
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

        except mariadb.Error as e:
            logging.error(f"Rank update error: {e}")
            self.conn.rollback()
            raise

        return rankups
    
    def update_seasonal_ranks(self, users: Set[Union[int, str]], platform: str) -> List[Tuple[Union[int, str], int]]:
        rankups = []
        
        try:
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

            rankups = self._update_top_division_ranks(platform, rankups)

            self.conn.commit()

        except mariadb.Error as e:
            logging.error(f"Seasonal rank update error: {e}")
            self.conn.rollback()
            raise

        return rankups

    def _update_top_division_ranks(self, platform: str, rankups: List[Tuple[Union[int, str], int]]) -> List[Tuple[Union[int, str], int]]:
        """
        Update the top division (Division 6) based on season time.
        Only the top Config.TOP_DIVISION_PLAYER_AMOUNT players can be in Division 6.
        """
        try:
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
                        logging.info(f"Promoted user {platform_uid} to Division 6")
                        rankups.append((platform_uid, 6))
                    else:
                        logging.info(f"Demoted user {platform_uid} to Division 5")
                        rankups.append((platform_uid, 5))
            
            return rankups

        except mariadb.Error as e:
            logging.error(f"Top division rank update error: {e}")
            raise
    
    def update_user_name(self, user_id: str, name: str, platform: str):
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

    def log_usage_stats(self, user_count: int, platform: str) -> None:
        query = """
            INSERT INTO usage_stats (timestamp, user_count, platform)
            VALUES (DATE_FORMAT(NOW(), '%Y-%m-%d %H:%i:00'), ?, ?)
        """
        self.execute_query(query, (user_count, platform))

    def get_user_roles(self, user_id: Union[int, str], platform: str) -> Tuple[Optional[int], Optional[int]]:
        """
        Get the rank (level) of a user based on their TeamSpeak or Discord ID.

        Args:
            user_id: The user's unique identifier (TeamSpeak UID or Discord ID)
            platform: The platform ('discord' or 'teamspeak')

        Returns:
            Optional[int]: The user's rank (level) or None if not found
        """
        id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'

        try:
            query = f"""
                SELECT level, division
                FROM user
                WHERE {id_column} = ?
            """
            self.cursor.execute(query, (str(user_id),))
            
            result = self.cursor.fetchone()
            return result if result else (None, None)
            
        except mariadb.Error as e:
            logging.error(f"Rank fetch failed for {platform} {user_id}: {e}")
            return None, None

    def update_login_streak(self, platform_uid: str, platform: str) -> Tuple[int, int]:
        """
        Update login streak for a user. Returns tuple of (current_streak, longest_streak)
        """
        try:
            # Get current streak info
            self.cursor.execute("""
                SELECT current_streak, longest_streak, last_login 
                FROM login_streak 
                WHERE platform = ? AND platform_uid = ?
            """, (platform, str(platform_uid)))
            
            result = self.cursor.fetchone()
            today = datetime.now().date()
            
            if not result:
                # First time login
                self.cursor.execute("""
                    INSERT INTO login_streak 
                    (platform_uid, platform, current_streak, longest_streak, last_login)
                    VALUES (?, ?, 1, 1, ?)
                """, (str(platform_uid), platform, today))
                self.conn.commit()
                return (1, 1)
                
            current_streak, longest_streak, last_login = result
            
            if last_login == today:
                # Already logged in today
                return (current_streak, longest_streak)
                
            if (today - last_login).days == 1:
                # Consecutive day login
                current_streak += 1
                longest_streak = max(longest_streak, current_streak)
            else:
                # Streak broken
                current_streak = 1
                
            self.cursor.execute("""
                UPDATE login_streak 
                SET current_streak = ?,
                    longest_streak = ?,
                    last_login = ?
                WHERE platform = ? AND platform_uid = ?
            """, (current_streak, longest_streak, today, platform, str(platform_uid)))
            
            self.conn.commit()
            return (current_streak, longest_streak)
            
        except mariadb.Error as e:
            logging.error(f"Error updating login streak: {e}")
            self.conn.rollback()
            return (0, 0)

    def close(self) -> None:
        """Close database connection"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
        except mariadb.Error as e:
            logging.error(f"Error closing database connection: {e}")
