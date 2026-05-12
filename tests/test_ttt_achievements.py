import json
import unittest
from datetime import datetime

from flask import Flask

from app.api.ranking.profile.achievements import routes as achievement_routes
from app.api.ranking.top import routes as top_routes
from app.api.user import routes as user_routes
from app.utils.database import DatabaseManager, zero_ttt_player_stats
from app.utils.ttt_achievement_consumer import TttAchievementStreamConsumer


def valid_ttt_event(**overrides):
    event = {
        "version": 1,
        "event_id": "round1_76561198000000000",
        "server": "ttt",
        "round_id": "round1",
        "steam_id64": "76561198000000000",
        "name": "Player",
        "map": "ttt_rooftops",
        "base_role": 0,
        "sub_role": 0,
        "team": "innocents",
        "win_team": "innocent",
        "rounds_played": 1,
        "rounds_won": 1,
        "kills": 3,
        "deaths": 0,
        "emitted_at": "2026-05-11T12:00:00Z",
    }
    event.update(overrides)
    return event


class FakeConn:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeCursor:
    def __init__(self):
        self.rowcount = 0
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))
        self.rowcount = 1


class DatabaseTttIngestTests(unittest.TestCase):
    def make_db(self):
        db = object.__new__(DatabaseManager)
        db.conn = FakeConn()
        db.cursor = FakeCursor()
        return db

    def test_ingest_ttt_achievement_event_updates_stats(self):
        db = self.make_db()

        result = db.ingest_ttt_achievement_event(valid_ttt_event(base_role=2, kills=4))

        self.assertEqual(result, {"ok": True, "event_id": "round1_76561198000000000"})
        self.assertEqual(len(db.cursor.executed), 1)
        self.assertNotIn("ttt_round_stat_events", db.cursor.executed[0][0])
        stats_params = db.cursor.executed[0][1]
        self.assertEqual(stats_params[0], "76561198000000000")
        self.assertEqual(stats_params[2], 1)
        self.assertEqual(stats_params[3], 1)
        self.assertEqual(stats_params[4], 0)
        self.assertEqual(stats_params[5], 1)
        self.assertEqual(stats_params[6], 0)
        self.assertEqual(stats_params[7], 4)
        self.assertIsInstance(stats_params[9], datetime)
        self.assertEqual(db.conn.commits, 1)


class FakeStreamValkey:
    def __init__(self, order=None):
        self.acks = []
        self.deletes = []
        self.order = order

    def xack(self, stream, group, message_id):
        if self.order is not None:
            self.order.append("ack")
        self.acks.append((stream, group, message_id))

    def xdel(self, stream, message_id):
        if self.order is not None:
            self.order.append("delete")
        self.deletes.append((stream, message_id))


class StreamConsumerTests(unittest.TestCase):
    def test_valid_stream_event_is_acknowledged_and_deleted_after_ingest(self):
        order = []

        class FakeDb:
            def ingest_ttt_achievement_event(self, payload):
                order.append("ingest")

        valkey_client = FakeStreamValkey(order)
        consumer = TttAchievementStreamConsumer(valkey_client, FakeDb())

        handled = consumer.handle_message(
            "gameserver:ttt:achievement_events",
            "1-0",
            {"payload": json.dumps(valid_ttt_event())},
        )

        self.assertTrue(handled)
        self.assertEqual(order, ["ingest", "ack", "delete"])
        self.assertEqual(valkey_client.acks[0][2], "1-0")
        self.assertEqual(valkey_client.deletes, [("gameserver:ttt:achievement_events", "1-0")])

    def test_malformed_stream_event_is_acknowledged_and_deleted_without_ingest(self):
        class FakeDb:
            def ingest_ttt_achievement_event(self, payload):
                raise AssertionError("malformed events must not be ingested")

        valkey_client = FakeStreamValkey()
        consumer = TttAchievementStreamConsumer(valkey_client, FakeDb())

        handled = consumer.handle_message(
            "gameserver:ttt:achievement_events",
            "1-1",
            {"payload": "{not json"},
        )

        self.assertFalse(handled)
        self.assertEqual(valkey_client.acks, [("gameserver:ttt:achievement_events", "firephenix-backend", "1-1")])
        self.assertEqual(valkey_client.deletes, [("gameserver:ttt:achievement_events", "1-1")])

    def test_database_failure_leaves_stream_event_unacked(self):
        class FakeDb:
            def ingest_ttt_achievement_event(self, payload):
                raise RuntimeError("database down")

        valkey_client = FakeStreamValkey()
        consumer = TttAchievementStreamConsumer(valkey_client, FakeDb())

        handled = consumer.handle_message(
            "gameserver:ttt:achievement_events",
            "1-2",
            {"payload": json.dumps(valid_ttt_event())},
        )

        self.assertFalse(handled)
        self.assertEqual(valkey_client.acks, [])
        self.assertEqual(valkey_client.deletes, [])


class FakeUserDatabase:
    instances = []
    user_row = None
    ttt_stats = zero_ttt_player_stats("76561198000000000")

    def __init__(self):
        self.closed = False
        FakeUserDatabase.instances.append(self)

    def execute_query(self, query, params=None):
        if "FROM activity_heatmap" in query:
            return []
        if "FROM login_streak" in query:
            return []
        if "FROM special_achievements" in query:
            return []
        if "FROM unlockables" in query:
            return []
        if "WHERE u.steam_id = ?" in query:
            return [self.user_row] if self.user_row else []
        return []

    def get_ttt_player_stats(self, steam_id):
        return self.ttt_stats

    def close(self):
        self.closed = True


class UserRouteTttStatsTests(unittest.TestCase):
    def setUp(self):
        self.original_db = user_routes.DatabaseManager
        user_routes.DatabaseManager = FakeUserDatabase
        FakeUserDatabase.instances = []
        FakeUserDatabase.user_row = None
        FakeUserDatabase.ttt_stats = zero_ttt_player_stats("76561198000000000")

    def tearDown(self):
        user_routes.DatabaseManager = self.original_db

    def make_app(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.config["RATELIMIT_ENABLED"] = False
        app.register_blueprint(user_routes.user_bp)
        return app

    def test_user_route_returns_zero_ttt_stats_without_user_row(self):
        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000000"

            response = client.get("/api/user")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ttt_stats"]["rounds_played"], 0)

    def test_user_route_returns_populated_ttt_stats(self):
        FakeUserDatabase.user_row = (
            7,
            "Player",
            "discord-id",
            "teamspeak-id",
            3,
            2,
            None,
            None,
            1,
            1,
            600,
            10,
            20,
            30,
            40,
        )
        FakeUserDatabase.ttt_stats = {
            **zero_ttt_player_stats("76561198000000000"),
            "rounds_played": 10,
            "rounds_won": 2,
            "kills": 25,
        }

        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000000"

            response = client.get("/api/user")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["ttt_stats"]["rounds_played"], 10)
        self.assertEqual(response.get_json()["ttt_stats"]["kills"], 25)


class FakeAchievementDatabase:
    instances = []
    ttt_stats = zero_ttt_player_stats("76561198000000000")

    def __init__(self):
        self.closed = False
        FakeAchievementDatabase.instances.append(self)

    def execute_query(self, query, params=None):
        if "SUM(logins)" in query:
            return [(None, None)]
        if "FROM activity_heatmap" in query:
            return []
        if "FROM special_achievements" in query:
            return []
        if "WHERE u.id = ?" in query:
            return [("76561198000000000", "discord-id", "teamspeak-id", 600)]
        return []

    def get_ttt_player_stats(self, steam_id):
        return self.ttt_stats

    def close(self):
        self.closed = True


class AchievementRouteTttStatsTests(unittest.TestCase):
    def setUp(self):
        self.original_db = achievement_routes.DatabaseManager
        achievement_routes.DatabaseManager = FakeAchievementDatabase
        FakeAchievementDatabase.instances = []
        FakeAchievementDatabase.ttt_stats = {
            **zero_ttt_player_stats("76561198000000000"),
            "rounds_played": 50,
            "rounds_won": 10,
            "kills": 25,
        }

    def tearDown(self):
        achievement_routes.DatabaseManager = self.original_db

    def make_app(self):
        app = Flask(__name__)
        app.config["RATELIMIT_ENABLED"] = False
        app.register_blueprint(achievement_routes.user_ranking_profile_achievements_bp)
        return app

    def test_profile_achievements_include_ttt_counters_and_levels(self):
        with self.make_app().test_client() as client:
            response = client.get("/api/ranking/profile/achievements?id=7")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()["ttt"]
        self.assertEqual(payload["stats"]["rounds_played"], 50)
        self.assertEqual(payload["stats"]["rounds_won"], 10)
        self.assertEqual(payload["stats"]["kills"], 25)
        self.assertEqual(payload["achievements"], {
            "rounds_played": 3,
            "rounds_won": 2,
            "kills": 2,
        })
        self.assertEqual(payload["achievement_level"], 7)


class FakeTopDatabase:
    def execute_query(self, query, params=None):
        if "ttt_rounds_played" in query:
            return [
                (1, "TTT Player", 1, None, None, 0, 0, 0, 0, 0, 100, 50, 250),
            ]
        if query.strip().startswith("SELECT platform, platform_id, achievement_type"):
            return []
        return []

    def close(self):
        pass


class HallOfFameTttAchievementTests(unittest.TestCase):
    def setUp(self):
        self.original_db = top_routes.DatabaseManager
        top_routes.DatabaseManager = FakeTopDatabase

    def tearDown(self):
        top_routes.DatabaseManager = self.original_db

    def make_app(self):
        app = Flask(__name__)
        app.config["RATELIMIT_ENABLED"] = False
        app.register_blueprint(top_routes.ranking_top_bp)
        return app

    def test_most_achievements_includes_ttt_levels(self):
        with self.make_app().test_client() as client:
            response = client.get("/api/ranking/hall-of-fame")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["most_achievements"], [
            {"id": 1, "name": "TTT Player", "level": 1, "value": 12},
        ])


if __name__ == "__main__":
    unittest.main()
