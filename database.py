import sys
from typing import List, Optional, Set, Tuple
import mariadb
from logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

__all__ = ['DatabaseManager']

class DatabaseManager:
    def __init__(self, host, user, password, database, port: int = 3306):
        try:
            self.conn = mariadb.connect(
                host=host,
                port=port,
                user=user,
                password=password,
                database=database
            )
            self.conn.autocommit = False
            self.cursor = self.conn.cursor()
            self.create_tables()
            logging.info("Successfully connected to MariaDB")
        except mariadb.Error as e:
            logging.error(f"Error connecting to the database: {e}")
            sys.exit(1)

    def create_tables(self):
        try:
            self.cursor.execute("""
                CREATE TABLE IF NOT EXISTS user_time (
                    id INT AUTO_INCREMENT PRIMARY KEY,
                    discord_uid BIGINT UNIQUE,
                    teamspeak_uid INT UNIQUE,
                    total_time INT DEFAULT 0,
                    daily_time INT DEFAULT 0,
                    weekly_time INT DEFAULT 0,
                    monthly_time INT DEFAULT 0,
                    last_update TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    INDEX idx_discord_uid (discord_uid),
                    INDEX idx_teamspeak_uid (teamspeak_uid)
                ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_520_ci
            """)
            self.conn.commit()
        except mariadb.Error as e:
            logging.error(f"Error creating tables: {e}")
            self.conn.rollback()
            raise

    def update_times(self, users: Set[int], platform: str, minutes: int = 1) -> None:
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

    def get_user_times(self, user_id: int, platform: str) -> Optional[Tuple[int, int, int, int]]:
        """Get time values for a specific user"""
        try:
            id_column = "discord_uid" if platform == "discord" else "teamspeak_uid"
            self.cursor.execute(f"""
                SELECT total_time, daily_time, weekly_time, monthly_time 
                FROM user_time 
                WHERE {id_column} = ?
            """, (user_id,))
            return self.cursor.fetchone()
        except mariadb.Error as e:
            logging.error(f"Error getting user times: {e}")
            raise

    def get_top_users(self, timeframe: str, platform: str, limit: int = 10) -> List[Tuple[int, int]]:
        """Get top users for a specific timeframe and platform"""
        try:
            time_column = f"{timeframe}_time"
            platform_column = f"{platform}_uid"
            
            self.cursor.execute(f"""
                SELECT {platform_column}, {time_column}
                FROM user_time
                WHERE {platform_column} IS NOT NULL
                AND {time_column} > 0
                ORDER BY {time_column} DESC
                LIMIT ?
            """, (limit,))
            return self.cursor.fetchall()
        except mariadb.Error as e:
            logging.error(f"Error getting top users: {e}")
            raise

    def close(self) -> None:
        """Close database connection"""
        try:
            if self.cursor:
                self.cursor.close()
            if self.conn:
                self.conn.close()
            logging.info("Database connection closed")
        except mariadb.Error as e:
            logging.error(f"Error closing database connection: {e}")
