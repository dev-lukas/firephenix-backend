import asyncio
from datetime import datetime, timedelta
import json
import os
import valkey
import valkey.asyncio as avalkey
from app.config import Config
from app.rankingsystem.bots.discord.bot import DiscordBot
from app.rankingsystem.bots.teamspeak.bot import TeamspeakBot
from app.utils.async_database import get_async_db
from app.utils.database import (
    DatabaseManager,
    DatabaseConnectionError,
    SEASON_RESET_DAY,
    SEASON_RESET_MONTH,
)
from app.utils.logger import RankingLogger
from app.utils.ttt_achievement_consumer import TttAchievementStreamConsumer

logging = RankingLogger(__name__).get_logger()

#: Ceiling for handling one website/pubsub command (the valkey result keys
#: expire after 30s, so answering later is pointless anyway).
COMMAND_TIMEOUT = 30


class RankingSystem:
    """Main class for the ranking system.

    Runs everything on ONE asyncio event loop: the Discord and TeamSpeak bots,
    the valkey pubsub command listener, the TTT achievement stream consumer
    and the ranking main loop are sibling tasks. Database access is natively
    async (asyncmy); there are no cross-thread bridges.
    """

    def __init__(self):
        self.ts = None
        self.dc = None
        # The sync manager creates/migrates the schema on connect; the async
        # manager assumes it exists.
        DatabaseManager().close()
        self.database = get_async_db()

        self.valkey = avalkey.Valkey(**Config.valkey_connection_kwargs())
        self.ttt_achievement_consumer = TttAchievementStreamConsumer(self.valkey, self.database)
        self.running = True
        self.platforms = ['discord', 'teamspeak']
        self._loop = None
        self._stop_event = None

    # -- lifecycle ---------------------------------------------------------

    def run(self):
        """Blocking entrypoint used by bot_runner."""
        try:
            asyncio.run(self.run_async())
        except KeyboardInterrupt:
            pass
        finally:
            self._remove_pid_file()

    async def run_async(self):
        self._loop = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()
        if not self.running:  # shutdown requested before startup finished
            return

        self.dc = DiscordBot()
        self.ts = TeamspeakBot()

        tasks = [
            asyncio.create_task(self.dc.run_async(), name="discord-bot"),
            asyncio.create_task(self.ts.run_async(), name="teamspeak-bot"),
            asyncio.create_task(self._command_listener(), name="valkey-commands"),
            asyncio.create_task(
                self.ttt_achievement_consumer.run_forever(lambda: self.running),
                name="ttt-achievements",
            ),
            asyncio.create_task(self._main_loop(), name="ranking-tick"),
        ]
        try:
            await self._stop_event.wait()
        finally:
            self.running = False
            await self._shutdown_tasks(tasks)

    def shutdown(self):
        """Request shutdown. Safe to call from signal handlers and other threads."""
        self.running = False
        if self._loop is not None and self._stop_event is not None and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop_event.set)

    async def _shutdown_tasks(self, tasks):
        for bot in (self.ts, self.dc):
            if bot is None:
                continue
            try:
                await bot.stop()
            except Exception as e:
                logging.error(f"Error stopping bot: {e}")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        try:
            await self.valkey.aclose()
        except Exception as e:
            logging.debug(f"Error closing valkey client: {e}")
        try:
            await self.database.close()
        except Exception as e:
            logging.debug(f"Error closing database pool: {e}")

    def _remove_pid_file(self):
        try:
            if os.path.exists(Config.PID_FILE):
                os.remove(Config.PID_FILE)
                logging.debug(f"Removed PID file {Config.PID_FILE}")
        except Exception as e:
            logging.error(f"Failed to remove PID file: {e}")

    # -- ranking tick ------------------------------------------------------

    def _get_online_users(self, platform):
        return self.ts.get_online_users() if platform == 'teamspeak' else self.dc.get_online_users()

    async def _main_loop(self):
        """Main loop for the ranksystem"""
        last_users = {platform: [] for platform in self.platforms}
        while self.running:
            try:
                now = datetime.now()
                next_full_run = now.replace(second=0, microsecond=0) + timedelta(minutes=1)
                time_until_full_run = (next_full_run - now).total_seconds()
                logging.debug(f"Time until next full run: {time_until_full_run} seconds")
                valkey_update_count = max(1, int(time_until_full_run / Config.VALKEY_UPDATE_INTERVAL))

                resets = await self.database.get_last_resets()
                last_daily, last_weekly, last_monthly, last_season = resets if resets else (None, None, None, None)
                today = now.date()
                weekday = now.weekday()
                first_of_month = now.day == 1
                season_reset_due = (now.month, now.day) >= (SEASON_RESET_MONTH, SEASON_RESET_DAY)

                if not last_daily or (last_daily.date() != today):
                    await self.database.reset_time('daily')
                    logging.info(f"Performed daily time reset at {now}")

                if weekday == 0 and (not last_weekly or last_weekly.date() != today):
                    await self.database.reset_time('weekly')
                    logging.info(f"Performed weekly time reset at {now}")

                if first_of_month and (not last_monthly or last_monthly.month != now.month or last_monthly.year != now.year):
                    await self.database.reset_time('monthly')
                    logging.info(f"Performed monthly time reset at {now}")

                if season_reset_due and (not last_season or last_season.year < now.year):
                    result = await self.database.close_season(now)
                    logging.info(
                        f"Closed season at {now}: "
                        f"{result['participants']} participants, "
                        f"{result['achievement_rows']} achievement rows"
                    )

                for _ in range(valkey_update_count):
                    if not self.running:
                        return
                    for platform in self.platforms:
                        connected_users, names = self._get_online_users(platform)
                        try:
                            await self.valkey.set(f'{platform}:online_users', json.dumps(connected_users), ex=20)
                        except valkey.ConnectionError as e:
                            logging.error(f"Valkey connection error: {e}")
                            break
                    time_left = (next_full_run - datetime.now()).total_seconds()
                    if time_left < Config.VALKEY_UPDATE_INTERVAL:
                        break
                    await asyncio.sleep(Config.VALKEY_UPDATE_INTERVAL)
                time_left = (next_full_run - datetime.now()).total_seconds()
                if time_left > 0:
                    await asyncio.sleep(time_left)

                for platform in self.platforms:
                    try:
                        connected_users, names = self._get_online_users(platform)

                        if datetime.now().minute == 0:
                            await self.database.log_usage_stats(
                                user_count=len(connected_users),
                                platform=platform
                            )

                        if connected_users and names:
                            for user_id in connected_users:
                                if user_id not in last_users[platform]:
                                    await self.database.update_user_name(user_id, names[user_id], platform)
                                    await self.database.update_login_streak(user_id, platform)

                            last_users[platform] = connected_users
                            await self.database.update_times(connected_users, platform)
                            await self.database.update_heatmap(connected_users, platform)
                            await self.database.update_ranks(connected_users, platform)
                            await self.database.update_seasonal_ranks(connected_users, platform)
                            for user_id in connected_users:
                                if platform == 'discord':
                                    asyncio.create_task(self.dc.check_ranks(user_id, check_type="both"))
                                elif platform == 'teamspeak':
                                    asyncio.create_task(self.ts.check_ranks(user_id))

                    except DatabaseConnectionError:
                        logging.error("Database connection error")
                        continue
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.error(f"Error in main loop: {e}")
                await asyncio.sleep(1)
                continue

    # -- website commands (valkey pubsub) ----------------------------------

    async def _command_listener(self):
        """Push-based pubsub listener; every command runs as its own task so a
        slow one never delays the others."""
        while self.running:
            pubsub = self.valkey.pubsub()
            try:
                await pubsub.subscribe('discord:commands', 'teamspeak:commands')
                async for message in pubsub.listen():
                    if message.get('type') != 'message':
                        continue
                    asyncio.create_task(self._handle_command(message['channel'], message['data']))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logging.error(f"Valkey command listener error: {e}")
                await asyncio.sleep(3)
            finally:
                try:
                    await pubsub.aclose()
                except Exception:
                    pass

    async def _handle_command(self, channel, data):
        try:
            async with asyncio.timeout(COMMAND_TIMEOUT):
                if channel == 'discord:commands':
                    await self.handle_discord_command(data)
                elif channel == 'teamspeak:commands':
                    await self.handle_teamspeak_command(data)
        except TimeoutError:
            logging.error(f"Timed out handling command on {channel}")
        except Exception as e:
            logging.error(f"Error handling command on {channel}: {e}")

    async def handle_discord_command(self, data):
        """Handle valkey commands for the Discord bot"""
        payload = json.loads(data)
        command = payload.get('command')
        if not self.dc:
            return

        if command == 'send_verification':
            await self.dc.send_verification(int(payload.get('platform_id')), payload.get('code'))

        elif command == 'create_owned_channel':
            result = await self.dc.create_owned_channel(
                int(payload.get('platform_id')), payload.get('channel_name'))
            await self.valkey.set(payload.get('message_id'), json.dumps({'channel_id': result}), ex=30)

        elif command == 'check_ranks':
            await self.dc.check_ranks(int(payload.get('platform_id')))

        elif command == 'add_move_shield':
            result = await self.dc.set_user_group(
                int(payload.get('platform_id')), Config.DISCORD_MOVE_BLOCK_ID)
            await self.valkey.set(payload.get('message_id'), json.dumps({'result': result}), ex=30)

        elif command == 'remove_move_shield':
            result = await self.dc.remove_user_group(
                int(payload.get('platform_id')), Config.DISCORD_MOVE_BLOCK_ID)
            await self.valkey.set(payload.get('message_id'), json.dumps({'result': result}), ex=30)

        elif command == 'add_ignore_role':
            user_id = int(payload.get('platform_id'))
            result = await self.dc.set_user_group(user_id, int(Config.DISCORD_EXCLUDED_ROLE_ID))
            if result and self.dc.time_tracker:
                self.dc.time_tracker.remove_tracked_user(user_id)
            await self.valkey.set(payload.get('message_id'), json.dumps({'result': result}), ex=30)

        elif command == 'set_apex_channel':
            result = await self.dc.move_channel_apex(int(payload.get('channel_id')))
            await self.valkey.set(payload.get('message_id'), json.dumps({'result': result}), ex=30)

    async def handle_teamspeak_command(self, data):
        """Handle valkey commands for the TeamSpeak bot"""
        payload = json.loads(data)
        command = payload.get('command')
        if not self.ts:
            return

        if command == 'send_verification':
            await self.ts.send_verification(payload.get('platform_id'), payload.get('code'))

        elif command == 'create_owned_channel':
            result = await self.ts.create_owned_channel(
                payload.get('platform_id'), payload.get('channel_name'))
            await self.valkey.set(payload.get('message_id'), json.dumps({'channel_id': result}), ex=30)

        elif command == 'check_ranks':
            await self.ts.check_ranks(payload.get('platform_id'))

        elif command == 'add_move_shield':
            response = await self.ts.set_server_group(payload.get('platform_id'), Config.TS3_MOVE_BLOCK_ID)
            result = response.get('ok', False) if isinstance(response, dict) else bool(response)
            json_data = json.dumps({'result': result, **response} if isinstance(response, dict) else {'result': result})
            await self.valkey.set(payload.get('message_id'), json_data, ex=30)

        elif command == 'remove_move_shield':
            result = await self.ts.remove_server_group(payload.get('platform_id'), Config.TS3_MOVE_BLOCK_ID)
            await self.valkey.set(payload.get('message_id'), json.dumps({'result': result}), ex=30)

        elif command == 'add_ignore_role':
            response = await self.ts.set_server_group(
                payload.get('platform_id'), int(Config.TS3_EXCLUDED_ROLE_ID))
            result = response.get('ok', False) if isinstance(response, dict) else bool(response)
            if result:
                # Full rescan can take a while on a busy server; do not hold up
                # the command response for it.
                asyncio.create_task(self.ts.force_user_validation())
            json_data = json.dumps({'result': result, **response} if isinstance(response, dict) else {'result': result})
            await self.valkey.set(payload.get('message_id'), json_data, ex=30)

        elif command == 'set_apex_channel':
            result = await self.ts.move_channel_apex(payload.get('channel_id'))
            await self.valkey.set(payload.get('message_id'), json.dumps({'result': result}), ex=30)
