import asyncio
import time
import atsq
from app.utils.database import DatabaseManager
from app.utils.logger import RankingLogger
from app.config import Config
from app.rankingsystem.bots.teamspeak.client_manager import ClientManager
from app.rankingsystem.bots.teamspeak.rank_manager import RankManager
from app.rankingsystem.bots.teamspeak.channel_manager import ChannelManager

logging = RankingLogger(__name__).get_logger()

class TeamspeakBot:
    """
    A TeamSpeak bot implementation using the Singleton pattern for managing
    user connections, ranks, and time tracking.

    Runs an asyncio event loop in its own thread (like the Discord bot); all
    query traffic shares one atsq client with automatic keepalive/reconnect.
    Other threads call the async methods via asyncio.run_coroutine_threadsafe
    against `self.loop`.
    """
    _instance = None
    VALIDATION_INTERVAL = 300  # Validate every 5 minutes

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(TeamspeakBot, cls).__new__(cls)
        return cls._instance

    def __init__(self):
        if hasattr(self, 'initialized'):
            return

        self.initialized = True
        self.running = False
        self.last_validation = 0
        self.database = DatabaseManager()
        # Created here (not in run()) so run_coroutine_threadsafe never races
        # against bot-thread startup; the loop starts running in run().
        self.loop = asyncio.new_event_loop()
        self.client = atsq.Client(
            Config.TS3_HOST,
            int(Config.TS3_PORT),
            username=Config.TS3_USERNAME,
            password=Config.TS3_PASSWORD,
            server_id=int(Config.TS3_SERVER_ID),
            register_events="server",
        )
        self.rank_manager = RankManager(Config, self.database, self.client)
        self.client_manager = ClientManager(Config, self.rank_manager, self.client)
        self.channel_manager = ChannelManager(Config, self.client)
        self._validation_task = None
        self._register_event_handlers()

    def _register_event_handlers(self):
        """Attach event handlers to the atsq client"""

        @self.client.on("cliententerview")
        async def on_client_enter(event):
            uid = await self.client_manager.handle_client_connect(event)
            if uid:
                await self.rank_manager.check_user_roles(uid)

        @self.client.on("clientleftview")
        async def on_client_leave(event):
            uid = self.client_manager.handle_client_disconnect(event)
            if uid:
                logging.debug(f"User disconnected with reason {event.get('reasonid')}: {uid}")

    def run(self):
        """Thread entrypoint: owns the event loop; atsq handles reconnection"""
        self.running = True
        asyncio.set_event_loop(self.loop)
        try:
            self.loop.run_until_complete(self.client.run_forever(on_ready=self._on_ready))
        except Exception as e:
            logging.error(f"TeamSpeak bot loop terminated unexpectedly: {e}")
        finally:
            if self._validation_task is not None:
                self._validation_task.cancel()
                self.loop.run_until_complete(
                    asyncio.gather(self._validation_task, return_exceptions=True)
                )
            self.loop.close()

    async def _on_ready(self, client):
        """Runs after every (re)connect: rescan clients and sync their roles"""
        await self.client_manager.handle_initial_clients()

        for uid in list(self.client_manager.connected_users):
            try:
                await self.rank_manager.check_user_roles(uid)
            except Exception as e:
                logging.error(f"Error checking roles for {uid}: {e}")

        if self._validation_task is None or self._validation_task.done():
            self._validation_task = asyncio.get_running_loop().create_task(self._validation_loop())

    async def _validation_loop(self):
        """Periodic validation to ensure our user tracking is accurate"""
        while self.running:
            await asyncio.sleep(self.VALIDATION_INTERVAL)
            try:
                logging.debug("Performing periodic user validation")
                await self.client_manager.validate_connected_users()
                self.last_validation = time.time()
            except Exception as e:
                logging.error(f"Error during periodic validation: {e}")

    def get_online_users(self):
        """Return list of currently connected users"""
        # While disconnected the tracked state is stale; report nobody online
        # so no time is credited (the reconnect rescan rebuilds it).
        if not self.client.connected:
            return [], {}
        return self.client_manager.get_online_users()

    async def create_owned_channel(self, user_id, channel_name):
        """Create a new owned channel for the user"""
        return await self.channel_manager.create_owned_channel(user_id, channel_name)

    async def send_verification(self, user_id, code):
        """Send verification code to TeamSpeak user"""
        return await self.channel_manager.send_verification(user_id, code)

    async def set_ranks(self, client_id, level=None, division=None):
        """Update user ranks in the TeamSpeak server"""
        return await self.rank_manager.set_ranks(client_id, level, division)

    async def check_ranks(self, user_id):
        """Check if user has the correct rank and/or division roles and update if necessary"""
        return await self.rank_manager.check_user_roles(user_id)

    async def set_server_group(self, client_id, group_id):
        """Set a server group for a user"""
        return await self.rank_manager.set_server_group(client_id, group_id)

    async def remove_server_group(self, client_id, group_id):
        """Remove a server group from a user"""
        return await self.rank_manager.remove_server_group(client_id, group_id)

    async def move_channel_apex(self, channel_id):
        """Move a channel to a new location"""
        return await self.channel_manager.move_channel_apex(channel_id)

    async def force_user_validation(self):
        """Manually trigger user validation - useful for testing or when inconsistencies are detected"""
        try:
            logging.info("Forcing user validation")
            await self.client_manager.validate_connected_users()
            self.last_validation = time.time()
            return True
        except Exception as e:
            logging.error(f"Error during forced validation: {e}")
            return False

    def stop(self):
        """Gracefully stop the bot (callable from any thread)"""
        self.running = False
        if not self.loop.is_closed():
            asyncio.run_coroutine_threadsafe(self.client.close(), self.loop)
