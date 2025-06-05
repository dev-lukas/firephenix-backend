import time
import ts3
from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

class ConnectionManager:
    """Manages TeamSpeak server connections and reconnection logic"""
    
    INITIAL_RECONNECT_DELAY = 30
    MAX_RECONNECT_DELAY = 300
    BANNED_WAIT_TIME = 300
    KEEPALIVE_TIMEOUT = 240
    
    def __init__(self, config):
        self.host = config.TS3_HOST
        self.port = int(config.TS3_PORT)
        self.username = config.TS3_USERNAME
        self.password = config.TS3_PASSWORD
        self.server_id = int(config.TS3_SERVER_ID)
        self.reconnect_delay = self.INITIAL_RECONNECT_DELAY
        
    def connect(self):
        """Establish connection to TeamSpeak server"""
        try:
            ts3conn = ts3.query.TS3ServerConnection(
                f"telnet://{self.username}:{self.password}@{self.host}:{self.port}"
            )
            ts3conn.exec_("use", sid=self.server_id)
            ts3conn.exec_("servernotifyregister", event="server")
            logging.debug("Successfully connected to TeamSpeak server")
            self.reconnect_delay = self.INITIAL_RECONNECT_DELAY
            return ts3conn
        except ts3.query.TS3QueryError as e:
            logging.error(f"TS3 connection error: {e}")
            self._handle_connection_error(e)
            return None
    
    def _handle_connection_error(self, error):
        """Handle connection errors with exponential backoff"""
        if isinstance(error, ts3.query.TS3QueryError):
            if "banned" in str(error).lower():
                logging.warning("Bot is banned, waiting longer before reconnect")
                time.sleep(self.BANNED_WAIT_TIME)
                return        time.sleep(self.reconnect_delay)
        self.reconnect_delay = min(self.MAX_RECONNECT_DELAY, 
                               self.reconnect_delay * 2)
    
    def wait_for_event(self, ts3conn):
        """Wait for and return server events"""
        try:
            event = ts3conn.wait_for_event(timeout=self.KEEPALIVE_TIMEOUT)
            return event[0] if event else None
        except ts3.query.TS3TimeoutError:
            return None
        except Exception as e:
            logging.warning(f"Error waiting for event: {e}")
            return None
        
    def send_keepalive(self, ts3conn):
        """Send keepalive to prevent timeout"""
        ts3conn.send_keepalive()
    
    def __enter__(self):
        """Context manager entry"""
        self.connection = self.connect()
        return self.connection
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        if hasattr(self, 'connection') and self.connection:
            try:
                self.connection.close()
            except Exception as e:
                logging.warning(f"Error closing TS3 connection: {e}")
            finally:
                self.connection = None
