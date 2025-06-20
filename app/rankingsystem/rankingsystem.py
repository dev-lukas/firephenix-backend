import asyncio
from datetime import datetime, timedelta
import json
import os
import threading
import time
import valkey
from app.config import Config
from app.rankingsystem.bots.discord.bot import DiscordBot
from app.rankingsystem.bots.teamspeak.bot import TeamspeakBot
from app.utils.database import DatabaseManager, DatabaseConnectionError
from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

class RankingSystem:
    """Main class for the ranking system. Initializes and runs the Discord and TeamSpeak bots.
    Also runs the main loop for updating online users in the database."""
    def __init__(self):
        self.ts = None
        self.dc = None
        self.database = DatabaseManager()

        self.valkey = valkey.Valkey(
            host=Config.VALKEY_HOST,
            port=Config.VALKEY_PORT,
            db=Config.VALKEY_DB,
            decode_responses=True
        )

        self.pubsub = self.valkey.pubsub()
        self.pubsub_thread = None
        self.running = True
        self.platforms = ['discord', 'teamspeak']

    def main_loop(self):
        """Main loop for the ranksystem"""
        last_users = {platform: [] for platform in self.platforms}
        while self.running:
            try:
                now = datetime.now()
                next_full_run = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
                time_until_full_run = (next_full_run - now).total_seconds()
                logging.debug(f"Time until next full run: {time_until_full_run} seconds")
                valkey_update_count = max(1, int(time_until_full_run / Config.VALKEY_UPDATE_INTERVAL))
                
                resets = self.database.get_last_resets()
                last_daily, last_weekly, last_monthly = resets if resets else (None, None, None)
                today = now.date()
                weekday = now.weekday()
                first_of_month = now.day == 1

                
                if not last_daily or (last_daily.date() != today):
                    self.database.reset_time('daily')
                    logging.info(f"Performed daily time reset at {now}")

                if weekday == 0 and (not last_weekly or last_weekly.date() != today):
                    self.database.reset_time('weekly')
                    logging.info(f"Performed weekly time reset at {now}")

                if first_of_month and (not last_monthly or last_monthly.month != now.month or last_monthly.year != now.year):
                    self.database.reset_time('monthly')
                    logging.info(f"Performed monthly time reset at {now}")


                for _ in range(valkey_update_count):
                    if not self.running:
                        exit()
                    for platform in self.platforms:
                        connected_users, names = self.ts.get_online_users() if platform == 'teamspeak' else self.dc.get_online_users()
                        try:
                            self.valkey.set(f'{platform}:online_users', json.dumps(connected_users), ex=20)
                        except valkey.ConnectionError as e:
                            logging.error(f"Valkey connection error: {e}")
                            break
                    time_left = (next_full_run - datetime.now()).total_seconds()
                    if time_left < Config.VALKEY_UPDATE_INTERVAL:
                        break
                    time.sleep(Config.VALKEY_UPDATE_INTERVAL)
                time_left = (next_full_run - datetime.now()).total_seconds()
                if time_left > 0:
                    time.sleep(time_left)
                
                for platform in self.platforms:
                    try:    
                        connected_users, names = self.ts.get_online_users() if platform == 'teamspeak' else self.dc.get_online_users()
                        
                        if datetime.now().minute == 0:
                            self.database.log_usage_stats(
                                user_count=len(connected_users),
                                platform=platform
                            )

                        if connected_users and names:
                            for user_id in connected_users:
                                if user_id not in last_users[platform]:
                                    self.database.update_user_name(user_id, names[user_id], platform)
                                    self.database.update_login_streak(user_id, platform) 

                            last_users[platform] = connected_users
                            self.database.update_times(connected_users, platform)
                            self.database.update_heatmap(connected_users, platform)
                            self.database.update_ranks(connected_users, platform)
                            self.database.update_seasonal_ranks(connected_users, platform)
                            for user_id in connected_users:
                                if platform == 'discord':
                                    self.dc.bot.loop.create_task(self.dc.check_ranks(user_id, check_type="both"))
                                elif platform == 'teamspeak':
                                    self.ts.check_ranks(user_id)

                    except DatabaseConnectionError:
                        logging.error("Database connection error")
                        continue
            except Exception as e:
                logging.error(f"Error in main loop for {platform}: {e}")
                continue

    def run(self) -> bool:
        """Main runner function"""         
        discord_ok = self.start_discord_bot()
        teamspeak_ok = self.start_teamspeak_bot()
        
        if not discord_ok and not teamspeak_ok:
            logging.error("Failed to start any bots, exiting")
            return False
        
        self.pubsub_thread = self.pubsub.run_in_thread(sleep_time=0.001)
        
        try:
            self.main_loop()
        except Exception as e:
            import traceback
            logging.error(f"Unexpected error: {e}\nStack trace:\n{traceback.format_exc()}")
        finally:
            self.shutdown()

    def shutdown(self):
        """Shutdown the bot runner gracefully"""
        self.running = False
        if self.ts:
            self.ts.stop()
        if self.pubsub_thread:
            self.pubsub_thread.stop()
        try:
            if os.path.exists(Config.PID_FILE):
                os.remove(Config.PID_FILE)
                logging.debug(f"Removed PID file {Config.PID_FILE}")
        except Exception as e:
            logging.error(f"Failed to remove PID file: {e}")

    def start_discord_bot(self) -> bool:
        """Initialize and start Discord bot"""
        try:
            self.dc = DiscordBot()
            dc_thread = threading.Thread(target=self.dc.run, daemon=True)
            dc_thread.start()

            self.pubsub.subscribe(**{'discord:commands': self.handle_discord_command})
            return True
        
        except Exception as e:
            logging.error(f"Failed to start Discord bot: {e}")
            return False
            
    def start_teamspeak_bot(self) -> bool:
        """Initialize and start TeamSpeak bot"""
        try:
            self.ts = TeamspeakBot()
            ts_thread = threading.Thread(target=self.ts.run, daemon=True)
            ts_thread.start()
            
            self.pubsub.subscribe(**{'teamspeak:commands': self.handle_teamspeak_command})
            return True
        except Exception as e:
            logging.error(f"Failed to start TeamSpeak bot: {e}")
            return False

    def handle_discord_command(self, message):
        """Handle Valkey commands for Discord bot"""
        if message['type'] != 'message':
            return
            
        try:
            data = json.loads(message['data'])
            command = data.get('command')
            
            if command == 'send_verification':
                user_id = data.get('platform_id')
                code = data.get('code')
                if self.dc:
                    self.dc.bot.loop.create_task(self.dc.send_verification(int(user_id), code))
                    
            elif command == 'create_owned_channel':
                user_id = data.get('platform_id')
                channel_name = data.get('channel_name')
                message_id = data.get('message_id')
                
                if self.dc:
                    result = asyncio.run_coroutine_threadsafe(
                        self.dc.create_owned_channel(int(user_id), channel_name),
                        self.dc.bot.loop
                    ).result()
                    
                    self.valkey.set(
                        message_id,
                        json.dumps({'channel_id': result}),
                        ex=30
                    )

            elif command == 'check_ranks':
                user_id = data.get('platform_id')
                if self.dc:
                    self.dc.bot.loop.create_task(self.dc.check_ranks(int(user_id)))  

            elif command == 'add_move_shield':
                user_id = data.get('platform_id')
                message_id = data.get('message_id')
                if self.dc:
                    result = asyncio.run_coroutine_threadsafe(
                        self.dc.set_user_group(int(user_id), Config.DISCORD_MOVE_BLOCK_ID),
                        self.dc.bot.loop
                    ).result()

                    self.valkey.set(
                        message_id,
                        json.dumps({'result': result}),
                        ex=30
                    )

            elif command == 'remove_move_shield':
                user_id = data.get('platform_id')
                message_id = data.get('message_id')
                if self.dc:
                    result = asyncio.run_coroutine_threadsafe(
                        self.dc.remove_user_group(int(user_id), Config.DISCORD_MOVE_BLOCK_ID),
                        self.dc.bot.loop
                    ).result()

                    self.valkey.set(
                        message_id,
                        json.dumps({'result': result}),
                        ex=30
                    )

            elif command == 'set_apex_channel':
                channel_id = data.get('channel_id')
                message_id = data.get('message_id')
                if self.dc:
                    result = asyncio.run_coroutine_threadsafe(
                        self.dc.move_channel_apex(int(channel_id)),
                        self.dc.bot.loop
                    ).result()

                    self.valkey.set(
                        message_id,
                        json.dumps({'result': result}),
                        ex=30
                    )

        except Exception as e:
            logging.error(f"Error handling Discord command: {e}")

    def handle_teamspeak_command(self, message):
        """Handle Valkey commands for TeamSpeak bot"""
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
                    json_data = json.dumps({'channel_id': result})
                    self.valkey.set(message_id, json_data, ex=30)

            elif command == 'check_ranks':
                user_id = data.get('platform_id')
                if self.ts:
                    self.ts.check_ranks(user_id)

            elif command == 'add_move_shield':
                user_id = data.get('platform_id')
                message_id = data.get('message_id')
                if self.ts:
                    result = self.ts.set_server_group(user_id, Config.TS3_MOVE_BLOCK_ID)
                    json_data = json.dumps({'result': result})
                    self.valkey.set(message_id, json_data, ex=30)

            elif command == 'remove_move_shield':
                user_id = data.get('platform_id')
                message_id = data.get('message_id')
                if self.ts:
                    result = self.ts.remove_server_group(user_id, Config.TS3_MOVE_BLOCK_ID)
                    json_data = json.dumps({'result': result})
                    self.valkey.set(message_id, json_data, ex=30)

            elif command == 'set_apex_channel':
                channel_id = data.get('channel_id')
                message_id = data.get('message_id')
                if self.ts:
                    result = self.ts.move_channel_apex(channel_id)
                    json_data = json.dumps({'result': result})
                    self.valkey.set(message_id, json_data, ex=30)

        except Exception as e:
            logging.error(f"Error handling TeamSpeak command: {e}")



