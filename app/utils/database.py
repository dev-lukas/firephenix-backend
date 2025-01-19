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
                database=Config.DB_NAME
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
                CREATE TABLE IF NOT EXISTS user_time (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    discord_uid BIGINT UNIQUE,
                    teamspeak_uid VARCHAR(128) UNIQUE,
                    name VARCHAR(255),
                    level INT DEFAULT 1,
                    division INT DEFAULT 1,
                    total_time INT DEFAULT 0,
                    daily_time INT DEFAULT 0,
                    weekly_time INT DEFAULT 0,
                    monthly_time INT DEFAULT 0,
                    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_discord_uid (discord_uid),
                    INDEX idx_teamspeak_uid (teamspeak_uid)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci
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

    def update_times(self, users: Set[Union[int, str]], platform: str, minutes: int = 1) -> None:
        """Batch update time values for multiple users"""
        try:
            # Convert set to list
            user_ids = list(users)
            
            # Prepare the platform-specific column name
            id_column = "discord_uid" if platform == "discord" else "teamspeak_uid"
            
            # Insert new users if they don't exist
            insert_query = f"INSERT IGNORE INTO user_time ({id_column}) VALUES (?)"
            self.cursor.executemany(insert_query, [(uid,) for uid in user_ids])
            
            # Update times for all users in one query
            placeholders = ','.join(['?'] * len(user_ids))
            update_query = f"""
                UPDATE user_time 
                SET total_time = total_time + ?,
                    daily_time = daily_time + ?,
                    weekly_time = weekly_time + ?,
                    monthly_time = monthly_time + ?,
                    last_update = CURRENT_TIMESTAMP
                WHERE {id_column} IN ({placeholders})
            """
            self.cursor.execute(update_query, (minutes, minutes, minutes, minutes, *user_ids))
            
            self.conn.commit()
            logging.debug(f"Updated {len(users)} users for platform {platform}")
        except mariadb.Error as e:
            logging.error(f"Error updating times: {e}")
            self.conn.rollback()
            raise

    def update_ranks(self, users: Set[Union[int, str]], platform: str) -> List[Tuple[Union[int, str], int]]:
        """
        Update ranks for a specific set of users based on their total time.
        Returns a list of users who ranked up and their new levels.
        """
        rankups = []  # List to store users who ranked up and their new levels

        try:
            id_column = "discord_uid" if platform == "discord" else "teamspeak_uid"
            user_ids = list(users)

            if not user_ids:
                return rankups

            placeholders = ','.join(['?'] * len(user_ids))
            select_query = f"""
                SELECT {id_column}, total_time, level 
                FROM user_time 
                WHERE {id_column} IN ({placeholders})
            """
            self.cursor.execute(select_query, user_ids)
            user_data = self.cursor.fetchall()

            for user_id, total_time, current_level in user_data:
                calculated_level = Config.get_level_for_minutes(total_time)
                if calculated_level != current_level:
                    update_query = """
                        UPDATE user_time 
                        SET level = ? 
                        WHERE {id_column} = ?
                    """.format(id_column=id_column)
                    self.cursor.execute(update_query, (calculated_level, user_id))
                    rankups.append((user_id, calculated_level))
                    logging.info(f"Updated user {user_id} from level {current_level} to {calculated_level}")

            self.conn.commit()

        except mariadb.Error as e:
            logging.error(f"Error updating ranks: {e}")
            self.conn.rollback()
            raise

        return rankups


    
    def update_user_name(self, user_id: str, name: str, platform: str):
        """
        Insert user if not exists, update name if changed
        platform should be either 'discord' or 'teamspeak'
        """
        query = """
        INSERT INTO user_time 
            (discord_uid, teamspeak_uid, name) 
        VALUES 
            (?, ?, ?)
        ON DUPLICATE KEY UPDATE 
            name = VALUES(name)
        """

        discord_uid = user_id if platform == 'discord' else None
        teamspeak_uid = user_id if platform == 'teamspeak' else None
    
        self.execute_query(query, (discord_uid, teamspeak_uid, name))

    def log_usage_stats(self, user_count: int, platform: str) -> None:
        query = """
        INSERT INTO usage_stats (timestamp, user_count, platform)
        VALUES (DATE_FORMAT(NOW(), '%Y-%m-%d %H:%i:00'), ?, ?)
        """
        self.execute_query(query, (user_count, platform))

    def get_user_rank(self, user_id: Union[int, str], platform: str) -> Optional[int]:
        """
        Get the rank (level) of a user based on their TeamSpeak or Discord ID.

        Args:
            user_id: The user's unique identifier (TeamSpeak UID or Discord ID)
            platform: The platform ('discord' or 'teamspeak')

        Returns:
            Optional[int]: The user's rank (level) or None if not found
        """
        id_column = "discord_uid" if platform == "discord" else "teamspeak_uid"
        query = f"SELECT level FROM user_time WHERE {id_column} = ?"
        
        try:
            result = self.execute_query(query, (user_id,))
            return result[0][0] if result else None
        except Exception as e:
            logging.error(f"Error retrieving rank for user {user_id}: {e}")
            return None

    def close(self) -> None:
        """Close database connection"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
        except mariadb.Error as e:
            logging.error(f"Error closing database connection: {e}")
