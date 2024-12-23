import mysql.connector
from datetime import datetime

class DatabaseManager:
    def __init__(self, host, user, password, database):
        self.connection = None
        self.cursor = None
        self.connect(host, user, password, database)

    def connect(self, host, user, password, database):
        try:
            self.connection = mysql.connector.connect(
                host=host,
                user=user,
                password=password,
                database=database
            )
            self.cursor = self.connection.cursor()
            self.create_tables()
            print("Connected to the database successfully.")
        except mysql.connector.Error as err:
            print(f"Error: {err}")

    def create_tables(self):
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
            )
        """)
        self.connection.commit()

    def add_user_if_not_exists(self, user_id: int, platform: str):
        """Add new user if they don't exist in the database"""
        if platform == "discord":
            self.cursor.execute("""
                INSERT IGNORE INTO user_time (discord_uid)
                VALUES (%s)
            """, (user_id,))
        else:  # teamspeak
            self.cursor.execute("""
                INSERT IGNORE INTO user_time (teamspeak_uid)
                VALUES (%s)
            """, (user_id,))
        self.connection.commit()

    def update_times(self, user_id: int, platform: str, minutes: int = 1):
        """Update time values for a user"""
        self.add_user_if_not_exists(user_id, platform)
        
        if platform == "discord":
            self.cursor.execute("""
                UPDATE user_time 
                SET total_time = total_time + %s,
                    daily_time = daily_time + %s,
                    weekly_time = weekly_time + %s,
                    monthly_time = monthly_time + %s,
                    last_update = CURRENT_TIMESTAMP
                WHERE discord_uid = %s
            """, (minutes, minutes, minutes, minutes, user_id))
        else:  # teamspeak
            self.cursor.execute("""
                UPDATE user_time 
                SET total_time = total_time + %s,
                    daily_time = daily_time + %s,
                    weekly_time = weekly_time + %s,
                    monthly_time = monthly_time + %s,
                    last_update = CURRENT_TIMESTAMP
                WHERE teamspeak_uid = %s
            """, (minutes, minutes, minutes, minutes, user_id))
        self.connection.commit()

    def get_user_times(self, user_id: int, platform: str):
        """Get all time values for a user"""
        if platform == "discord":
            self.cursor.execute("""
                SELECT total_time, daily_time, weekly_time, monthly_time 
                FROM user_time 
                WHERE discord_uid = %s
            """, (user_id,))
        else:  # teamspeak
            self.cursor.execute("""
                SELECT total_time, daily_time, weekly_time, monthly_time 
                FROM user_time 
                WHERE teamspeak_uid = %s
            """, (user_id,))
        return self.cursor.fetchone()

    def reset_daily_times(self):
        """Reset all daily times to 0"""
        self.cursor.execute("UPDATE user_time SET daily_time = 0")
        self.connection.commit()

    def reset_weekly_times(self):
        """Reset all weekly times to 0"""
        self.cursor.execute("UPDATE user_time SET weekly_time = 0")
        self.connection.commit()

    def reset_monthly_times(self):
        """Reset all monthly times to 0"""
        self.cursor.execute("UPDATE user_time SET monthly_time = 0")
        self.connection.commit()

    def get_top_users(self, timeframe: str, platform: str, limit: int = 10):
        """Get top users for a specific timeframe and platform"""
        time_column = f"{timeframe}_time"
        platform_column = f"{platform}_uid"
        
        self.cursor.execute(f"""
            SELECT {platform_column}, {time_column}
            FROM user_time
            WHERE {platform_column} IS NOT NULL
            AND {time_column} > 0
            ORDER BY {time_column} DESC
            LIMIT %s
        """, (limit,))
        return self.cursor.fetchall()

    def close(self):
        """Close database connection"""
        if self.cursor:
            self.cursor.close()
        if self.connection:
            self.connection.close()
