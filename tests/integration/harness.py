"""Shared harness for integration tests.

These tests run the real Flask app against a real MariaDB and Valkey
instance. They are skipped unless RUN_INTEGRATION_TESTS=1 is set, so the
plain unit-test discovery (``python -m unittest discover -s tests``) stays
green without any infrastructure.

Required environment (see scripts/run-integration-tests.sh and the CI stack
smoke): SECRET_KEY, DB_HOST, DB_PORT, DB_USER, DB_PASSWORD, VALKEY_HOST,
VALKEY_PORT, LIMITER_STORAGE_URI.
"""

import json
import os
import threading
import unittest

INTEGRATION_ENABLED = os.getenv("RUN_INTEGRATION_TESTS") == "1"

skip_unless_integration = unittest.skipUnless(
    INTEGRATION_ENABLED,
    "integration tests disabled (set RUN_INTEGRATION_TESTS=1)",
)

ADMIN_STEAM_ID = "76561198000000001"


def create_test_app():
    """Create the real Flask app with rate limiting disabled for tests."""
    from app import create_app
    from app.utils.security import limiter

    app = create_app()
    app.config["TESTING"] = True
    app.config["RATELIMIT_ENABLED"] = False
    limiter.enabled = False
    return app


def open_database():
    """Real DatabaseManager connected to the integration database."""
    from app.utils.database import DatabaseManager

    db = DatabaseManager()
    if db.conn is None:
        raise RuntimeError(
            "Could not connect to the integration database - is it running?"
        )
    return db


TABLES_TO_CLEAR = (
    "admin_audit_log",
    "unlockables",
    "special_achievements",
    "verification",
    "login_streak",
    "activity_heatmap",
    "usage_stats",
    "ttt_player_stats",
    "time",
    "user",
)


def reset_database(db):
    """Delete all rows so every test starts from a known-empty state."""
    for table in TABLES_TO_CLEAR:
        db.cursor.execute(f"DELETE FROM {table}")
    db.conn.commit()


def seed_user(db, *, steam_id=None, discord_id=None, teamspeak_id=None,
              name="Test User", level=1, division=1, ranking_disabled=0):
    db.cursor.execute(
        """
        INSERT INTO user (steam_id, discord_id, teamspeak_id, name, level,
                          division, ranking_disabled)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (steam_id, discord_id, teamspeak_id, name, level, division,
         ranking_disabled),
    )
    user_id = db.cursor.lastrowid
    db.conn.commit()
    return user_id


def seed_time(db, *, platform, platform_uid, total_time=0, daily_time=0,
              weekly_time=0, monthly_time=0, season_time=0):
    db.cursor.execute(
        """
        INSERT INTO time (platform_uid, platform, total_time, daily_time,
                          weekly_time, monthly_time, season_time)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (platform_uid, platform, total_time, daily_time, weekly_time,
         monthly_time, season_time),
    )
    db.conn.commit()


def seed_special_achievement(db, *, platform, platform_id, achievement_type):
    db.cursor.execute(
        """
        INSERT INTO special_achievements (platform, platform_id, achievement_type)
        VALUES (?, ?, ?)
        """,
        (platform, platform_id, achievement_type),
    )
    db.conn.commit()


def login(client, steam_id, *, with_csrf=True):
    """Establish an authenticated session the same way the auth callback does."""
    with client.session_transaction() as sess:
        sess["steam_id"] = str(steam_id)
        if with_csrf:
            sess["csrf_token"] = "integration-test-csrf-token"
    return {"X-CSRF-Token": "integration-test-csrf-token"} if with_csrf else {}


def admin_session(client, monkeypatched_config=None):
    """Log in as an admin. Config.ADMIN_STEAM_IDS must contain ADMIN_STEAM_ID."""
    from app.config import Config

    if ADMIN_STEAM_ID not in Config.ADMIN_STEAM_IDS:
        Config.ADMIN_STEAM_IDS.append(ADMIN_STEAM_ID)
    return login(client, ADMIN_STEAM_ID)


class FakeGameServerResponder:
    """Impersonates the game-server manager on the real Valkey instance.

    Subscribes to ``gameserver:<server>:commands`` and answers each command
    by writing the configured response payload to the response key, exactly
    like the production manager does.
    """

    def __init__(self, server_id="ttt", response=None, mark_online=True):
        from app.config import Config
        import valkey

        self.server_id = server_id
        self.response = response if response is not None else {"ok": True}
        self.valkey = valkey.Valkey(**Config.valkey_connection_kwargs())
        self.received = []
        self._pubsub = self.valkey.pubsub(ignore_subscribe_messages=True)
        self._pubsub.subscribe(f"gameserver:{server_id}:commands")
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, daemon=True)
        if mark_online:
            self.valkey.set(f"gameserver:{server_id}:status", "online", ex=60)

    def start(self):
        self._thread.start()
        return self

    def _run(self):
        while not self._stop.is_set():
            message = self._pubsub.get_message(timeout=0.1)
            if not message or message.get("type") != "message":
                continue
            payload = json.loads(message["data"])
            self.received.append(payload)
            response_key = (
                f"gameserver:{self.server_id}:responses:{payload['message_id']}"
            )
            self.valkey.set(response_key, json.dumps(self.response), ex=30)

    def stop(self):
        self._stop.set()
        self._thread.join(timeout=2)
        self._pubsub.close()
        self.valkey.delete(f"gameserver:{self.server_id}:status")
        self.valkey.close()


class IntegrationTestCase(unittest.TestCase):
    """Base class: real app + real DB, cleaned between tests."""

    @classmethod
    def setUpClass(cls):
        if not INTEGRATION_ENABLED:
            raise unittest.SkipTest("integration tests disabled")
        cls.app = create_test_app()
        cls.db = open_database()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "db", None) is not None:
            cls.db.close()

    def setUp(self):
        reset_database(self.db)
        self.client = self.app.test_client()

    def fetch_all(self, query, params=None):
        """Read verification data, ending any open REPEATABLE READ snapshot
        first so committed writes from the app's own connections are seen."""
        self.db.conn.rollback()
        self.db.cursor.execute(query, params or ())
        return self.db.cursor.fetchall()
