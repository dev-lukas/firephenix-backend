import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Discord
    DISCORD_TOKEN=os.getenv("DISCORD_TOKEN")
    DISCORD_EXCLUDED_ROLE_ID="12312312312"
    # TeamSpeak
    TS3_HOST="127.0.0.1"
    TS3_PORT="10011"
    TS3_USERNAME="serveradmin"
    TS3_SERVER_ID="1"
    TS3_EXCLUDED_ROLE_ID="1231231231"
    TS3_PASSWORD=os.getenv('TS3_PASSWORD')
    # Database
    DB_HOST="127.0.0.1"
    DB_PORT="3306"
    DB_USER="root"
    DB_NAME="firephenix"
    DB_PASSWORD=os.getenv("DB_PASSWORD")