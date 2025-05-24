#!/usr/bin/env python3

import mariadb
from app.config import Config
from app.utils.logger import RankingLogger
from app.utils.database import DatabaseManager
import datetime

logging = RankingLogger(__name__).get_logger()

def import_bak_user_data():
    try:
        conn = mariadb.connect(
            host=Config.DB_HOST,
            port=int(Config.DB_PORT),
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            database=Config.DB_NAME,
        )
        cursor = conn.cursor()

        cursor.execute("SELECT uuid, name, count, lastseen, firstcon FROM bak_user")
        bak_users = cursor.fetchall()

        for uuid, name, count, last, first in bak_users:

            last_login = datetime.datetime.fromtimestamp(last)
            # If first is 0, use None (NULL in SQL), else convert as usual
            if first == 0:
                first_login = '2017-01-01 00:00:00'  # Use a default date for users with no first login
            else:
                first_login = datetime.datetime.fromtimestamp(first)
            logging.info(f"Importing bak_user data for {name} ({uuid})")
            
            # Filter out bots
            if name in ['FireBot', 'FireBot1', 'Pathfinder', 'Rechte Hand Spandaus']:
                logging.info(f"Skipping bot user: {name}")
                continue

            # Create user entry
            cursor.execute("""
                INSERT INTO user (teamspeak_id, name, created_at)
                VALUES (?, ?, ?)
                ON DUPLICATE KEY UPDATE name = VALUES(name)
            """, (uuid, name, first_login))

            # Create time entry
            total_time = (count + 59) // 60  # Round up count / 60
            cursor.execute("""
                INSERT INTO time (platform_uid, platform, total_time, last_update)
                VALUES (?, 'teamspeak', ?, ?)
                ON DUPLICATE KEY UPDATE total_time = VALUES(total_time)
            """, (uuid, total_time, last_login))

        cursor.execute("SELECT uuid, total_connections FROM bak_stats_user")
        bak_users = cursor.fetchall()

        for uuid, total_connections in bak_users:
            logging.info(f"Importing bak_stats_user data for {uuid}")
            # Create stats entry
            cursor.execute("""
                INSERT INTO login_streak (platform_uid, platform, logins, current_streak, longest_streak, last_login)
                VALUES (?, 'teamspeak', ?, 1, 1, CURRENT_TIMESTAMP)
                ON DUPLICATE KEY UPDATE logins = VALUES(logins)
            """, (uuid, total_connections))

        conn.commit()
    except mariadb.Error as e:
        logging.error(f"Error importing bak_user data: {e}")
        conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def recalculate_ranks():
    try:
        conn = mariadb.connect(
            host=Config.DB_HOST,
            port=int(Config.DB_PORT),
            user=Config.DB_USER,
            password=Config.DB_PASSWORD,
            database=Config.DB_NAME,
        )
        cursor = conn.cursor()
        db = DatabaseManager()
        for platform in ['discord', 'teamspeak']:
            cursor.execute(f"SELECT platform_uid FROM time WHERE platform = '{platform}'")
            users = cursor.fetchall()
            for user_id in users:
                db.update_ranks([user_id[0]], platform)
                db.update_seasonal_ranks([user_id[0]], platform)

        conn.commit()
    except mariadb.Error as e:
        logging.error(f"Error recalculating ranks: {e}")
        conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

if __name__ == '__main__':
    import_bak_user_data()
    recalculate_ranks()