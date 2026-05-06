import time
import json
import uuid
import valkey
from app.config import Config
from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

class ValkeyManager:
    ALLOWED_GAMESERVER_COMMANDS = {"status", "healthcheck", "restart", "start", "stop", "grant_season_skin"}

    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(ValkeyManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.valkey = valkey.Valkey(
            host=Config.VALKEY_HOST,
            port=Config.VALKEY_PORT,
            db=Config.VALKEY_DB,
            decode_responses=True
        )

        self._initialized = True
        logging.info("Valkey manager initialized")
    
    def publish_command(self, platform: str, command, **kwargs):
        """Publish a command to the specified platform channel"""
        message = {'command': command, **kwargs}
        self.valkey.publish(f'{platform}:commands', json.dumps(message))
        
    def get_online_users(self, platform):
        """Get list of online users for the specified platform"""
        users = self.valkey.get(f'{platform}:online_users')
        if users:
            return json.loads(users)
        return []
        
    def create_owned_channel(self, platform: str, user_id, channel_name: str):
        """Send command to create an owned channel and wait for response"""
        message_id = f"{platform}:channel:{user_id}:{int(time.time())}"
        self.publish_command(
            platform, 
            'create_owned_channel', 
            platform_id=user_id, 
            channel_name=channel_name,
            message_id=message_id
        )
        
        for _ in range(30):
            result = self.valkey.get(message_id)
            if result:
                self.valkey.delete(message_id)
                return json.loads(result).get('channel_id')
            time.sleep(1)
            
        return None
    
    def set_move_shield(self, platform: str, user_id, add: bool):
        """Send command to add or remove MoveShield and wait for response"""
        message_id = f"{platform}:moveshield:{user_id}:{int(time.time())}"
        if add:
            command = 'add_move_shield'
        else:
            command = 'remove_move_shield'
        self.publish_command(
            platform, 
            command, 
            platform_id=user_id, 
            add=add,
            message_id=message_id
        )
        
        for _ in range(30):
            result = self.valkey.get(message_id)
            if result:
                self.valkey.delete(message_id)
                return json.loads(result).get('result')
            time.sleep(1)
            
        return False
    
    def set_apex_channel(self, platform: str, channel_id):
        """Send command to set a channel as Apex and wait for response"""
        message_id = f"{platform}:apex_channel:{channel_id}:{int(time.time())}"
        self.publish_command(
            platform, 
            'set_apex_channel', 
            channel_id=channel_id,
            message_id=message_id
        )
        
        for _ in range(30):
            result = self.valkey.get(message_id)
            if result:
                self.valkey.delete(message_id)
                return json.loads(result).get('result')
            time.sleep(1)
            
        return False
    
    def unlock_skin(self, platform: str, tier: int, player_id: str):
        """Send command to unlock a skin and wait for response"""
        message_id = f"{platform}:skin:{tier}:{player_id}:{int(time.time())}"
        self.publish_command(
            platform, 
            'unlock_skin', 
            tier=tier, 
            player_id=player_id,
            message_id=message_id
        )
        
        for _ in range(30):
            result = self.valkey.get(message_id)
            if result:
                self.valkey.delete(message_id)
                return json.loads(result).get('result')
            time.sleep(1)
            
        return False

    def gameserver_command(
        self,
        server_id: str,
        command: str,
        data: dict | None = None,
        timeout_seconds: float = 3,
        poll_interval_seconds: float = 0.1,
    ):
        """Send a command to a catalog-managed game server and wait for response."""
        if command not in self.ALLOWED_GAMESERVER_COMMANDS:
            return {"ok": False, "error": "command_not_allowed", "command": command}, 400

        message_id = uuid.uuid4().hex
        channel = f"gameserver:{server_id}:commands"
        response_key = f"gameserver:{server_id}:responses:{message_id}"
        status_key = f"gameserver:{server_id}:status"
        payload = {
            "version": 1,
            "message_id": message_id,
            "command": command,
            "server": server_id,
            "created_at": int(time.time()),
        }
        if data:
            payload.update(data)

        self.valkey.publish(channel, json.dumps(payload, separators=(",", ":")))

        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            raw = self.valkey.get(response_key)
            if raw:
                self.valkey.delete(response_key)
                try:
                    response = json.loads(raw)
                except json.JSONDecodeError:
                    return {"ok": False, "error": "invalid_manager_response"}, 502
                if response.get("error"):
                    return response, 502
                return response, 200
            time.sleep(poll_interval_seconds)

        if not self.valkey.get(status_key):
            return {"ok": False, "error": "manager_unavailable", "server": server_id}, 503
        return {"ok": False, "error": "manager_timeout", "server": server_id}, 504
