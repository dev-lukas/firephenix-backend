import os
import logging
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Logger Level
    LOGGER_LEVEL = logging.INFO
    # Website
    SECRET_KEY = os.getenv('SECRET_KEY')
    STEAM_OPENID_URL = 'https://steamcommunity.com/openid/login'
    SITE_URL = 'https://firephenix.de'
    # Discord
    DISCORD_TOKEN=os.getenv("DISCORD_TOKEN")
    DISCORD_EXCLUDED_ROLE_ID="12312312312"
    DISCORD_GUILD_ID=280753556917846016
    DISCORD_ADMIN_ROLE_ID=280755819459641344
    DISCORD_MODERATOR_ROLE_ID=1355132242174808065
    DISCORD_MOVE_BLOCK_ID=1355153601529516072
    DISCORD_PARENT_CHANNEL=1329604014756855880
    DISCORD_APEX_PARENT_CHANNEL=1363569345724285088
    DISCORD_CHAT_CHANNEL=292753223536869376
    DISCORD_EMBER_STICKER=1376678129250074716
    # TeamSpeak
    TS3_HOST="127.0.0.1"
    TS3_PORT="10011"
    TS3_USERNAME="serveradmin"
    TS3_SERVER_ID="1"
    TS3_EXCLUDED_ROLE_ID="40"
    TS3_PASSWORD=os.getenv('TS3_PASSWORD')
    TS3_PARENT_CHANNEL = 51
    TS3_APEX_PARENT_CHANNEL = 47
    TS3_OWNER_GROUP_ID = 5
    TS3_MOVE_BLOCK_ID = 41
    # Database
    DB_HOST="127.0.0.1"
    DB_PORT="3306"
    DB_USER=os.getenv("DB_USER")
    DB_NAME="firephenix"
    DB_PASSWORD=os.getenv("DB_PASSWORD")
    # Valkey
    VALKEY_HOST = 'localhost'
    VALKEY_PORT = 6379
    VALKEY_DB = 0
    VALKEY_UPDATE_INTERVAL = 2
    # Limiter
    LIMITER_STORAGE_URI=f"redis://{VALKEY_HOST}:{VALKEY_PORT}"
    # Lock Socket - needed for Cross-Platform Locking
    PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_runner.pid")
    # OpenRouter
    OPENROUTER_MODEL = "google/gemini-2.0-flash-exp:free"
    OPENROUTER_ALTERNATE_MODELS = ['meta-llama/llama-3.3-8b-instruct:free', 'meta-llama/llama-3.2-3b-instruct:free'] 
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    # VPNApi.io
    VPNAPI_API_KEY = os.getenv("VPNAPI_API_KEY")
    # Rankingsystem
    LEVEL_REQUIREMENTS = {
        1: 0,
        2: 300,     
        3: 600,     
        4: 1200,     
        5: 2400,     
        6: 3000,    
        7: 3600,    
        8: 4200,    
        9: 4800,    
        10: 5400,  
        11: 6000,   
        12: 7500,   
        13: 9000,   
        14: 10500,   
        15: 12000,   
        16: 15000,   
        17: 18000,   
        18: 24000,  
        19: 36000,  
        20: 72000,  
        21: 150000, 
        22: 300000, 
        23: 600000,  
        24: 1200000,  
        25: 1800000   
    }

    # Seasonal Division Requirements
    TOP_DIVISION_PLAYER_AMOUNT = 10
    DIVISION_REQUIREMENTS = {
        1: 0,
        2: 3000,
        3: 9000,
        4: 18000,
        5: 24000
    }

    TEAMSPEAK_DIVISION_MAP = {
        1: 34,
        2: 35,
        3: 36,
        4: 37,
        5: 38,
        6: 39
    }
    # TeamSpeak Level Maps
    TEAMSPEAK_LEVEL_MAP = {
        1: 9,
        2: 10,
        3: 11,
        4: 12,
        5: 13,
        6: 14,
        7: 15,
        8: 16,
        9: 17,
        10: 18,
        11: 19,
        12: 20,
        13: 21,
        14: 22,
        15: 23,
        16: 24,
        17: 25,
        18: 26,
        19: 27,
        20: 28,
        21: 29,
        22: 30,
        23: 31,
        24: 32,
        25: 33
    }

    DISCORD_DIVISION_MAP = {
        1: 1353806491487830216,
        2: 1353806610342084779,
        3: 1353806666591768678,
        4: 1353806722006777897,
        5: 1353806807793143829,
        6: 1353806901032517715
    }

    DISCORD_LEVEL_MAP = {
        1: 1330572683917922446,
        2: 1330572966102171688,
        3: 1330573023581048893,
        4: 1330573059672903872,
        5: 1330573098801430568,
        6: 1330573147115749377,
        7: 1330573199053950996,
        8: 1330573228342644868,
        9: 1330573288614789220,
        10: 1330573319392591912,
        11: 1330573352653553794,
        12: 1330573400283811922,
        13: 1330573429187022848,
        14: 1330573456806383637,
        15: 1330573486736801852,
        16: 1330573521578889266,
        17: 1330573581733593210,
        18: 1330573613597982861,
        19: 1330573641334653041,
        20: 1330573667536732192,
        21: 1330573839477772391,
        22: 1330573896671559740,
        23: 1330573939847467121,
        24: 1330574116641706176,
        25: 1330574154415603744
    }

    @classmethod
    def get_division_requirement(cls, division: int) -> int:
        return cls.DIVISION_REQUIREMENTS.get(division, 0)
    
    @classmethod
    def get_division_for_minutes(cls, minutes: int) -> int:
        for division, requirement in sorted(cls.DIVISION_REQUIREMENTS.items(), reverse=True):
            if minutes >= requirement:
                return division
        return 0
    
    @classmethod
    def get_level_requirement(cls, level: int) -> int:
        return cls.LEVEL_REQUIREMENTS.get(level, 0)
    
    @classmethod
    def get_level_for_minutes(cls, minutes: int) -> int:
        for level, requirement in sorted(cls.LEVEL_REQUIREMENTS.items(), reverse=True):
            if minutes >= requirement:
                return level
        return 0