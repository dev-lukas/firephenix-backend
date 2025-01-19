import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    # Discord
    DISCORD_TOKEN=os.getenv("DISCORD_TOKEN")
    DISCORD_EXCLUDED_ROLE_ID="12312312312"
    DISCORD_GUILD_ID=280753556917846016
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
        25: 1000000   
    }
    # TeamSpeak Level Maps
    TEAMSPEAK_LEVEL_MAP = {
        1: 8,
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
        21: 38,
        22: 39,
        23: 40,
        24: 218,
        25: 219
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
    
    # Optional helper methods
    @classmethod
    def get_level_requirement(cls, level: int) -> int:
        return cls.LEVEL_REQUIREMENTS.get(level, 0)
    
    @classmethod
    def get_level_for_minutes(cls, minutes: int) -> int:
        for level, requirement in sorted(cls.LEVEL_REQUIREMENTS.items(), reverse=True):
            if minutes >= requirement:
                return level
        return 0