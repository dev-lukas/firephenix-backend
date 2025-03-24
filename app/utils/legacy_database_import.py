import mariadb
from app.config import Config
from app.utils.logger import RankingLogger

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

        cursor.execute("SELECT uuid, name, count FROM bak_user")
        bak_users = cursor.fetchall()

        for uuid, name, count in bak_users:
            logging.info(f"Importing bak_user data for {name} ({uuid})")
            # Create user entry
            cursor.execute("""
                INSERT INTO user (teamspeak_id, name, created_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON DUPLICATE KEY UPDATE name = VALUES(name)
            """, (uuid, name))

            # Create time entry
            total_time = (count + 59) // 60  # Round up count / 60
            cursor.execute("""
                INSERT INTO time (platform_uid, platform, total_time, last_update)
                VALUES (?, 'teamspeak', ?, CURRENT_TIMESTAMP)
                ON DUPLICATE KEY UPDATE total_time = VALUES(total_time)
            """, (uuid, total_time))

        conn.commit()
    except mariadb.Error as e:
        logging.error(f"Error importing bak_user data: {e}")
        conn.rollback()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

if __name__ == '__main__':
    import_bak_user_data()