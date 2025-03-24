import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Website
    SECRET_KEY = os.getenv('SECRET_KEY')
    STEAM_OPENID_URL = 'https://steamcommunity.com/openid/login'
    SITE_URL = 'http://localhost:5000'
    # Discord
    DISCORD_TOKEN=os.getenv("DISCORD_TOKEN")
    DISCORD_EXCLUDED_ROLE_ID="12312312312"
    DISCORD_GUILD_ID=280753556917846016
    DISCORD_PARENT_CHANNEL=1329604014756855880
    # TeamSpeak
    TS3_HOST="127.0.0.1"
    TS3_PORT="10011"
    TS3_USERNAME="serveradmin"
    TS3_SERVER_ID="1"
    TS3_EXCLUDED_ROLE_ID="1231231231"
    TS3_PASSWORD=os.getenv('TS3_PASSWORD')
    TS3_PARENT_CHANNEL = 4
    TS3_OWNER_GROUP_ID = 5
    # Database
    DB_HOST="127.0.0.1"
    DB_PORT="3306"
    DB_USER="root"
    DB_NAME="firephenix"
    DB_PASSWORD=os.getenv("DB_PASSWORD")
    # Redis
    REDIS_HOST = 'localhost'
    REDIS_PORT = 6379
    REDIS_DB = 0
    # Limiter
    LIMITER_STORAGE_URI="redis://localhost:6379"
    # Lock Socket - needed for Cross-Platform Locking
    PID_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_runner.pid")
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
        24: 900000,  
        25: 1800000   
    }

    # Seasonal Division Requirements
    TOP_DIVISION_PLAYER_AMOUNT = 10
    DIVISION_REQUIREMENTS = {
        1: 0,
        2: 3000,
        3: 6000,
        4: 12000,
        5: 24000
    }

    TEAMSPEAK_DIVISION_MAP = {
        1: 33,
        2: 34,
        3: 35,
        4: 36,
        5: 37,
        6: 38
    }
    # TeamSpeak Level Maps
    TEAMSPEAK_LEVEL_MAP = {
        1: 39,
        2: 9,
        3: 10,
        4: 11,
        5: 12,
        6: 13,
        7: 14,
        8: 15,
        9: 16,
        10: 17,
        11: 18,
        12: 19,
        13: 20,
        14: 21,
        15: 22,
        16: 23,
        17: 24,
        18: 25,
        19: 26,
        20: 27,
        21: 28,
        22: 29,
        23: 30,
        24: 31,
        25: 32
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