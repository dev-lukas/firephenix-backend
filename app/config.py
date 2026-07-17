import os
import logging
from dotenv import load_dotenv

load_dotenv()


def parse_admin_steam_ids(value):
    if not value:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    return [
        item.strip()
        for item in str(value).replace("\n", ",").split(",")
        if item.strip()
    ]


class Config:
    # Logger Level
    LOGGER_LEVEL = logging.INFO
    # Website
    SECRET_KEY = os.getenv('SECRET_KEY')
    STEAM_OPENID_URL = 'https://steamcommunity.com/openid/login'
    SITE_URL = os.getenv("SITE_URL", "https://firephenix.de")
    CORS_ORIGINS = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", SITE_URL).split(",")
        if origin.strip()
    ]
    # Production runs behind one trusted Nginx proxy. Trust only X-Forwarded-For
    # so Flask-Limiter keys login/API limits by the real client IP.
    TRUST_PROXY_HEADERS = True
    PROXY_FIX_X_FOR = 1
    PROXY_FIX_X_PROTO = 0
    PROXY_FIX_X_HOST = 0
    PROXY_FIX_X_PORT = 0
    ADMIN_STEAM_IDS = parse_admin_steam_ids(
        os.getenv("admin_steam_ids", os.getenv("ADMIN_STEAM_IDS", ""))
    )
    # Discord
    DISCORD_TOKEN=os.getenv("DISCORD_TOKEN")
    DISCORD_EXCLUDED_ROLE_ID="12312312312"
    DISCORD_GUILD_ID=280753556917846016
    DISCORD_ADMIN_ROLE_ID=280755819459641344
    DISCORD_MODERATOR_ROLE_ID=1355132242174808065
    DISCORD_MOVE_BLOCK_ID=1355153601529516072
    DISCORD_PARENT_CHANNEL=1329604014756855880
    DISCORD_APEX_PARENT_CHANNEL=1363569345724285088
    DISCORD_EMBER_STICKER=1376678129250074716
    # TeamSpeak
    TS3_HOST=os.getenv("TS3_HOST", "127.0.0.1")
    # SSH ServerQuery port (atsq); the server must have query_ssh enabled
    TS3_PORT=os.getenv("TS3_PORT", "10022")
    TS3_USERNAME="serveradmin"
    TS3_SERVER_ID="1"
    TS3_EXCLUDED_ROLE_ID="40"
    TS3_PASSWORD=os.getenv('TS3_PASSWORD')
    TS3_PARENT_CHANNEL = 51
    TS3_APEX_PARENT_CHANNEL = 47
    TS3_OWNER_GROUP_ID = 5
    TS3_MOVE_BLOCK_ID = 41
    # Database
    DB_HOST=os.getenv("DB_HOST", "127.0.0.1")
    DB_PORT=os.getenv("DB_PORT", "3306")
    DB_USER=os.getenv("DB_USER")
    DB_NAME="firephenix"
    DB_PASSWORD=os.getenv("DB_PASSWORD")
    # Valkey
    VALKEY_HOST = os.getenv("VALKEY_HOST", "localhost")
    VALKEY_PORT = int(os.getenv("VALKEY_PORT", "6379"))
    VALKEY_DB = 0
    VALKEY_USERNAME = os.getenv("VALKEY_USERNAME") or None
    VALKEY_PASSWORD = os.getenv("VALKEY_PASSWORD") or None
    VALKEY_UPDATE_INTERVAL = 2
    # Public Source server status query. This is intentionally read-only and
    # does not use RCON credentials.
    TTT_STATUS_HOST = os.getenv("TTT_STATUS_HOST", "firephenix.de")
    TTT_STATUS_PORT = int(os.getenv("TTT_STATUS_PORT", "27015"))
    TTT_STATUS_TIMEOUT_SECONDS = float(os.getenv("TTT_STATUS_TIMEOUT_SECONDS", "2"))
    TTT_SEASON_REWARD_ITEM_UUIDS = {
        1: {
            2: "66C32AD2-0232-4AF0-9F5E-B90D06DD61BA",
            3: "36648F60-EA1F-449A-94AD-98914B3BF8AC",
            4: "E2223E93-6831-4C3E-A295-3086153172F6",
            5: "E5FF810F-AEC9-4F36-9333-36CA21F82B64",
            6: "7FEBD81C-6F6D-4C6F-871F-84CD6D42D517",
        },
        2: {
            2: "689C47CB-33C8-4C0D-A5AA-BF3596EE8496",
            3: "2D32BDCC-4540-49CC-AA22-6D2933E0C3D0",
            4: "DDF1F1C4-F48B-4CB7-BBBC-ED110E4CD732",
            5: "501DFCA2-474E-4C09-AFBD-BFE123B04A91",
            6: "CC6B3976-2EA6-499E-BE75-A54EF890F010",
        },
    }
    TTT_ROUNDS_PLAYED_THRESHOLDS = [1, 10, 50, 100]
    TTT_ROUNDS_WON_THRESHOLDS = [1, 10, 25, 50]
    TTT_KILLS_THRESHOLDS = [1, 25, 100, 250]
    # Limiter
    LIMITER_STORAGE_URI=os.getenv("LIMITER_STORAGE_URI", f"valkey://{VALKEY_HOST}:{VALKEY_PORT}")
    LIMITER_KEY_PREFIX = os.getenv("LIMITER_KEY_PREFIX", "firephenix:limiter")
    # Bot process management
    PID_FILE = os.getenv(
        "BOT_RUNNER_PID_FILE",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_runner.pid"),
    )
    # OpenRouter
    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
    # Preferred providers in priority order — the best available free model
    # from the highest-priority provider is selected automatically.
    OPENROUTER_PREFERRED_PROVIDERS = [
        "deepseek",
        "meta-llama",
        "openai",
        "google",
        "mistralai",
        "qwen",
    ]
    # Cache TTL for the free model list (seconds)
    OPENROUTER_MODEL_CACHE_TTL = 3600
    OPENROUTER_MIN_CONTEXT_LENGTH = 16000
    OPENROUTER_MODEL_FALLBACK_LIMIT = 3
    EMBER_CONTEXT_MESSAGE_LIMIT = 30
    EMBER_CONTEXT_CHAR_LIMIT = 6000
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

    @classmethod
    def valkey_connection_kwargs(cls):
        kwargs = {
            "host": cls.VALKEY_HOST,
            "port": cls.VALKEY_PORT,
            "db": cls.VALKEY_DB,
            "decode_responses": True,
            # valkey.asyncio defaults socket_timeout to 5 (sync defaults to
            # None), which kills blocking reads: XREADGROUP block=5000 and
            # pubsub listen() die with "Timeout reading from ..." without this.
            "socket_timeout": None,
        }
        if cls.VALKEY_USERNAME:
            kwargs["username"] = cls.VALKEY_USERNAME
        if cls.VALKEY_PASSWORD:
            kwargs["password"] = cls.VALKEY_PASSWORD
        return kwargs

    # Seasonal Division Requirements
    TOP_DIVISION_PLAYER_AMOUNT = 10
    DIVISION_REQUIREMENTS = {
        1: 0,
        2: 1500,
        3: 3000,
        4: 6000,
        5: 9000
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

    @classmethod
    def get_ttt_achievement_level(cls, value: int, thresholds: list[int]) -> int:
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = 0
        return sum(1 for threshold in thresholds if value >= threshold)

    @classmethod
    def get_ttt_achievement_levels(cls, stats: dict) -> dict:
        stats = stats or {}
        return {
            "rounds_played": cls.get_ttt_achievement_level(
                stats.get("rounds_played", 0),
                cls.TTT_ROUNDS_PLAYED_THRESHOLDS,
            ),
            "rounds_won": cls.get_ttt_achievement_level(
                stats.get("rounds_won", 0),
                cls.TTT_ROUNDS_WON_THRESHOLDS,
            ),
            "kills": cls.get_ttt_achievement_level(
                stats.get("kills", 0),
                cls.TTT_KILLS_THRESHOLDS,
            ),
        }
