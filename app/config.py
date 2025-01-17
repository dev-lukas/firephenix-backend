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