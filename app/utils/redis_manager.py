from datetime import time
import redis
import json
from app.config import Config
from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

class RedisManager:
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(RedisManager, cls).__new__(cls)
            cls._instance._initialized = False
        return cls._instance
    
    def __init__(self):
        if self._initialized:
            return
            
        self.redis = redis.Redis(
            host=Config.REDIS_HOST,
            port=Config.REDIS_PORT,
            db=Config.REDIS_DB,
            decode_responses=True
        )
        self._initialized = True
        logging.info("Redis manager initialized")
    
    def publish_command(self, platform, command, **kwargs):
        """Publish a command to the specified platform channel"""
        message = {'command': command, **kwargs}
        self.redis.publish(f'{platform}:commands', json.dumps(message))
        
    def get_online_users(self, platform):
        """Get list of online users for the specified platform"""
        users = self.redis.get(f'{platform}:online_users')
        if users:
            return json.loads(users)
        return []
    
    def set_online_users(self, platform, users):
        """Update the list of online users for the specified platform"""
        self.redis.set(f'{platform}:online_users', json.dumps(users))
        
    def create_owned_channel(self, platform, user_id, channel_name):
        """Send command to create an owned channel and wait for response"""
        message_id = f"{platform}:channel:{user_id}:{int(time.time())}"
        self.publish_command(
            platform, 
            'create_owned_channel', 
            user_id=user_id, 
            channel_name=channel_name,
            message_id=message_id
        )
        
        for _ in range(30):
            result = self.redis.get(message_id)
            if result:
                self.redis.delete(message_id)
                return json.loads(result).get('channel_id')
            time.sleep(1)
            
        return None
