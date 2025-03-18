import asyncio
import threading
import json
import time
import redis
import signal
import os
import sys
from app.bots.teamspeakbot import TeamspeakBot
from app.bots.discordbot import DiscordBot
from app.utils.logger import RankingLogger
from app.config import Config

logging = RankingLogger(__name__).get_logger()

class BotRunner:
    def __init__(self):
        self.ts = None
        self.dc = None
        self.redis = redis.Redis(
            host=Config.REDIS_HOST,
            port=Config.REDIS_PORT,
            db=Config.REDIS_DB,
            decode_responses=True
        )
        self.pubsub = self.redis.pubsub()
        self.pubsub_thread = None
        self.running = True
        
        # Redis-based locking mechanism
        self.lock_key = "botrunner:lock"
        self.lock_value = str(os.getpid())  # Using PID as lock value
        self.lock_expiry = 300  # 5 minutes (in seconds)
        self.refresh_thread = None
        logging.info(f"Using Redis key '{self.lock_key}' for single instance lock")
        
    def acquire_lock(self):
        """Try to acquire a Redis lock to ensure only one instance runs"""
        try:
            # Try to set the lock key with NX option (only if it doesn't exist)
            # and set an expiry time for safety in case the process crashes
            acquired = self.redis.set(
                self.lock_key, 
                self.lock_value,
                nx=True,
                ex=self.lock_expiry
            )
            
            if acquired:
                logging.info(f"Bot runner lock acquired successfully by PID {self.lock_value}")
                # Start a background thread to periodically refresh the lock
                self._start_refresh_thread()
                return True
            else:
                # Get the PID of the other instance
                other_pid = self.redis.get(self.lock_key)
                logging.error(f"Another BotRunner instance (PID {other_pid}) is already running. Exiting.")
                return False
                
        except Exception as e:
            logging.error(f"Error acquiring Redis lock: {str(e)}")
            return False
    
    def _start_refresh_thread(self):
        """Start a thread to periodically refresh the lock expiry time"""
        def refresh_lock():
            while self.running:
                try:
                    # Check if we still own the lock before refreshing
                    if self.redis.get(self.lock_key) == self.lock_value:
                        self.redis.expire(self.lock_key, self.lock_expiry)
                        logging.debug(f"Lock refreshed, extended for {self.lock_expiry} seconds")
                    else:
                        logging.error("Lock was taken by another process!")
                        self.running = False
                        break
                except Exception as e:
                    logging.error(f"Error refreshing lock: {e}")
                
                # Sleep for 1/3 of the expiry time
                time.sleep(self.lock_expiry / 3)
        
        self.refresh_thread = threading.Thread(target=refresh_lock, daemon=True)
        self.refresh_thread.start()
    
    def release_lock(self):
        """Release the Redis lock"""
        try:
            # Only delete the lock if we own it (compare with our PID)
            current_value = self.redis.get(self.lock_key)
            if (current_value == self.lock_value):
                self.redis.delete(self.lock_key)
                logging.info("Bot runner lock released")
            else:
                logging.warning("Cannot release lock as it's owned by another process")
        except Exception as e:
            logging.error(f"Error releasing Redis lock: {e}")
            
    def start_discord_bot(self):
        """Initialize and start Discord bot"""
        try:
            self.dc = DiscordBot()
            self.dc.setup_redis(self.redis)
            dc_thread = threading.Thread(target=self.dc.run, daemon=True)
            dc_thread.start()

            self.pubsub.subscribe(**{'discord:commands': self.handle_discord_command})
            return True
        
        except Exception as e:
            logging.error(f"Failed to start Discord bot: {e}")
            return False
            
    def start_teamspeak_bot(self):
        """Initialize and start TeamSpeak bot"""
        try:
            self.ts = TeamspeakBot()
            self.ts.setup_redis(self.redis)

            ts_thread = threading.Thread(target=self.ts.run, daemon=True)
            ts_thread.start()
            
            self.pubsub.subscribe(**{'teamspeak:commands': self.handle_teamspeak_command})
            return True
        except Exception as e:
            logging.error(f"Failed to start TeamSpeak bot: {e}")
            return False

    def update_online_users(self):
        """Update online users for all active platforms"""
        if self.dc:
            discord_users = self.dc.get_online_users()
            self.redis.set('discord:online_users', json.dumps(discord_users))
        
        if self.ts:
            teamspeak_users = self.ts.get_online_users()
            self.redis.set('teamspeak:online_users', json.dumps(teamspeak_users))

    def handle_discord_command(self, message):
        """Handle Redis commands for Discord bot"""
        if message['type'] != 'message':
            return
            
        try:
            data = json.loads(message['data'])
            command = data.get('command')
            
            if command == 'send_verification':
                user_id = data.get('platform_id')
                code = data.get('code')
                if self.dc:
                    self.dc.loop.create_task(self.dc.send_verification(int(user_id), code))
                    
            elif command == 'create_owned_channel':
                user_id = data.get('platform_id')
                channel_name = data.get('channel_name')
                message_id = data.get('message_id')
                
                if self.dc:
                    result = asyncio.run_coroutine_threadsafe(
                        self.dc.create_owned_channel(int(user_id), channel_name),
                        self.dc.bot.loop
                    ).result()
                    
                    self.redis.set(
                        message_id,
                        json.dumps({'channel_id': result}),
                        ex=30
                    )
        except Exception as e:
            logging.error(f"Error handling Discord command: {e}")

    def handle_teamspeak_command(self, message):
        """Handle Redis commands for TeamSpeak bot"""
        if message['type'] != 'message':
            return
            
        try:
            data = json.loads(message['data'])
            command = data.get('command')
            
            if command == 'send_verification':
                user_id = data.get('platform_id')
                code = data.get('code')
                if self.ts:
                    self.ts.send_verification(user_id, code)
                    
            elif command == 'create_owned_channel':
                user_id = data.get('platform_id')
                channel_name = data.get('channel_name')
                message_id = data.get('message_id')
                
                if self.ts:
                    result = self.ts.create_owned_channel(user_id, channel_name)
                    self.redis.set(
                        message_id,
                        json.dumps({'channel_id': result}),
                        ex=30
                    )
        except Exception as e:
            logging.error(f"Error handling TeamSpeak command: {e}")
    
    def run(self):
        """Main runner function"""
        logging.info("Starting bot runner...")
        
        if not self.acquire_lock():
            logging.error("Failed to acquire lock. Exiting.")
            sys.exit(1)
            
        discord_ok = self.start_discord_bot()
        teamspeak_ok = self.start_teamspeak_bot()
        
        if not discord_ok and not teamspeak_ok:
            logging.error("Failed to start any bots, exiting")
            self.release_lock()
            return
        
        self.pubsub_thread = self.pubsub.run_in_thread(sleep_time=0.001)
        
        signal.signal(signal.SIGINT, self.shutdown)
        signal.signal(signal.SIGTERM, self.shutdown)
        
        try:
            while self.running:
                self.update_online_users()
                time.sleep(10)
        except Exception as e:
            logging.error(f"Unexpected error: {e}")
        finally:
            self.shutdown()

    def shutdown(self, signum=None, frame=None):
        """Shutdown the bot runner gracefully"""
        logging.info("Shutting down bot runner...")
        self.running = False
        if self.ts:
            self.ts.stop()
        if self.pubsub_thread:
            self.pubsub_thread.stop()
        self.release_lock()

if __name__ == "__main__":
    runner = BotRunner()
    runner.run()
