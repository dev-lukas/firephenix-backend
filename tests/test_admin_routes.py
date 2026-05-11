import unittest
from datetime import datetime

from flask import Flask

from app.api.admin import routes as admin_routes
from app.api.auth import routes as auth_routes
from app.config import Config


class FakeConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeCursor:
    def __init__(self):
        self.queries = []
        self.lastrowid = 456

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        if FakeDatabase.fetchone_results:
            return FakeDatabase.fetchone_results.pop(0)
        return FakeDatabase.fetchone_result

    def fetchall(self):
        return FakeDatabase.fetchall_result

    def close(self):
        pass


class FakeDatabase:
    instances = []
    fetchone_result = None
    fetchone_results = []
    fetchall_result = []

    def __init__(self):
        self.cursor = FakeCursor()
        self.conn = FakeConnection()
        self.closed = False
        FakeDatabase.instances.append(self)

    def close(self):
        self.closed = True


class StubValkeyManager:
    def __init__(self, response=None):
        self.response = response or ({"ok": True}, 200)
        self.calls = []

    def gameserver_command(self, server_id, command, data=None, **kwargs):
        self.calls.append((server_id, command, data, kwargs))
        return self.response


class StubAdminValkeyManager:
    def __init__(self, result=True):
        self.result = result
        self.calls = []

    def set_ignore_role(self, platform, user_id):
        self.calls.append((platform, user_id))
        return self.result


class AuthCheckAdminFlagTests(unittest.TestCase):
    def setUp(self):
        self.original_admins = Config.ADMIN_STEAM_IDS
        Config.ADMIN_STEAM_IDS = ["76561198000000000"]

    def tearDown(self):
        Config.ADMIN_STEAM_IDS = self.original_admins

    def make_app(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.config["RATELIMIT_ENABLED"] = False
        app.register_blueprint(auth_routes.auth_bp)
        return app

    def test_auth_check_returns_admin_flag_for_admin(self):
        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000000"
                session["csrf_token"] = "known-token"

            response = client.get("/api/auth/check")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["is_admin"])

    def test_auth_check_returns_false_admin_flag_for_non_admin(self):
        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000001"
                session["csrf_token"] = "known-token"

            response = client.get("/api/auth/check")

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.get_json()["is_admin"])


class AdminRouteGuardTests(unittest.TestCase):
    def setUp(self):
        self.original_admins = Config.ADMIN_STEAM_IDS
        Config.ADMIN_STEAM_IDS = ["76561198000000000"]

    def tearDown(self):
        Config.ADMIN_STEAM_IDS = self.original_admins

    def make_app(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.config["RATELIMIT_ENABLED"] = False
        app.register_blueprint(admin_routes.admin_bp)
        return app

    def test_admin_search_rejects_non_admin(self):
        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000001"

            response = client.get("/api/admin/players/search?q=test")

        self.assertEqual(response.status_code, 403)

    def test_admin_mutation_requires_csrf(self):
        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000000"
                session["csrf_token"] = "known-token"

            response = client.post("/api/admin/ranking/transfer", json={})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {"error": "CSRF token missing"})


class AdminSeasonSkinGrantTests(unittest.TestCase):
    def setUp(self):
        self.original_admins = Config.ADMIN_STEAM_IDS
        self.original_db = admin_routes.DatabaseManager
        self.original_valkey = admin_routes.valkey_manager
        self.original_rewards = Config.TTT_SEASON_REWARD_ITEM_UUIDS
        Config.ADMIN_STEAM_IDS = ["76561198000000000"]
        Config.TTT_SEASON_REWARD_ITEM_UUIDS = {
            2: "66C32AD2-0232-4AF0-9F5E-B90D06DD61BA",
        }
        FakeDatabase.instances = []
        FakeDatabase.fetchone_result = None
        FakeDatabase.fetchone_results = []
        FakeDatabase.fetchall_result = []
        admin_routes.DatabaseManager = FakeDatabase

    def tearDown(self):
        Config.ADMIN_STEAM_IDS = self.original_admins
        Config.TTT_SEASON_REWARD_ITEM_UUIDS = self.original_rewards
        admin_routes.DatabaseManager = self.original_db
        admin_routes.valkey_manager = self.original_valkey

    def make_app(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.config["RATELIMIT_ENABLED"] = False
        app.register_blueprint(admin_routes.admin_bp)
        return app

    def post_as_admin(self, client, body):
        with client.session_transaction() as session:
            session["steam_id"] = "76561198000000000"
            session["csrf_token"] = "known-token"
        return client.post(
            "/api/admin/ttt/season-skin",
            json=body,
            headers={"X-CSRF-Token": "known-token"},
        )

    def post_endpoint_as_admin(self, client, path, body):
        with client.session_transaction() as session:
            session["steam_id"] = "76561198000000000"
            session["csrf_token"] = "known-token"
        return client.post(
            path,
            json=body,
            headers={"X-CSRF-Token": "known-token"},
        )

    def get_endpoint_as_admin(self, client, path):
        with client.session_transaction() as session:
            session["steam_id"] = "76561198000000000"
            session["csrf_token"] = "known-token"
        return client.get(path)

    def test_grant_season_skin_publishes_configured_reward(self):
        stub = StubValkeyManager()
        admin_routes.valkey_manager = stub

        with self.make_app().test_client() as client:
            response = self.post_as_admin(client, {
                "steam_id64": "76561198000000000",
                "tier": 2,
                "reason": "manual correction",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(stub.calls[0][0], "ttt")
        self.assertEqual(stub.calls[0][1], "grant_season_skin")
        payload = stub.calls[0][2]
        self.assertEqual(payload["steam_id64"], "76561198000000000")
        self.assertEqual(payload["steam_id2"], "STEAM_0:0:19867136")
        self.assertEqual(payload["item_uuid"], Config.TTT_SEASON_REWARD_ITEM_UUIDS[2])
        self.assertEqual(stub.calls[0][3]["timeout_seconds"], 60)
        self.assertIn("INSERT INTO admin_audit_log", FakeDatabase.instances[0].cursor.queries[0][0])

    def test_season_skin_validation_writes_failed_audit(self):
        with self.make_app().test_client() as client:
            response = self.post_as_admin(client, {
                "steam_id64": "76561198000000000",
                "tier": 9,
                "reason": "bad tier",
            })

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "tier is not configured")
        self.assertIn("INSERT INTO admin_audit_log", FakeDatabase.instances[0].cursor.queries[0][0])

    def test_ignore_role_uses_selected_platform_id(self):
        FakeDatabase.fetchone_result = ("discord-user",)
        stub = StubAdminValkeyManager({"ok": True, "result": True})
        admin_routes.valkey_manager = stub

        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000000"
                session["csrf_token"] = "known-token"
            response = client.post(
                "/api/admin/ranking/ignore-role",
                json={"user_id": 123, "platform": "discord", "reason": "manual ignore"},
                headers={"X-CSRF-Token": "known-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(stub.calls, [("discord", "discord-user")])
        self.assertEqual(response.get_json()["role_id"], Config.DISCORD_EXCLUDED_ROLE_ID)
        self.assertEqual(response.get_json()["command_response"]["ok"], True)
        queries = FakeDatabase.instances[0].cursor.queries
        self.assertTrue(
            any("SET ranking_disabled = 1" in query for query, _ in queries)
        )
        self.assertTrue(
            any(params == ("manual ignore", 123) for _, params in queries if params)
        )
        self.assertIn("INSERT INTO admin_audit_log", queries[-1][0])
        self.assertTrue(response.get_json()["ranking_disabled"])

    def test_ignore_role_failure_returns_bot_error_details(self):
        FakeDatabase.fetchone_result = ("teamspeak-user",)
        stub = StubAdminValkeyManager({
            "ok": False,
            "error": "servergroup_add_failed",
            "details": "insufficient client permissions",
        })
        admin_routes.valkey_manager = stub

        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000000"
                session["csrf_token"] = "known-token"
            response = client.post(
                "/api/admin/ranking/ignore-role",
                json={"user_id": 123, "platform": "teamspeak", "reason": "manual ignore"},
                headers={"X-CSRF-Token": "known-token"},
            )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "servergroup_add_failed")
        self.assertEqual(response.get_json()["details"]["details"], "insufficient client permissions")
        queries = FakeDatabase.instances[0].cursor.queries
        self.assertFalse(any("SET ranking_disabled = 1" in query for query, _ in queries))
        self.assertIn("INSERT INTO admin_audit_log", queries[-1][0])

    def test_audit_log_defaults_to_five_entries_and_reports_more(self):
        FakeDatabase.fetchall_result = [
            (
                idx,
                "76561198000000000",
                "ranking_time_update",
                "{}",
                "{}",
                "success",
                datetime(2026, 5, 1, 12, idx),
            )
            for idx in range(6)
        ]

        with self.make_app().test_client() as client:
            response = self.get_endpoint_as_admin(client, "/api/admin/audit-log")

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(len(payload["entries"]), 5)
        self.assertTrue(payload["has_more"])
        self.assertEqual(FakeDatabase.instances[0].cursor.queries[0][1], (6,))

    def test_time_update_caps_period_counters_and_recalculates_rank(self):
        FakeDatabase.fetchone_results = [
            (123, "76561198000000000", "discord-user", "teamspeak-user", "Player", 1, 1, 0),
            (300, 120, 80, 200, 90),
            (100, 40),
        ]

        with self.make_app().test_client() as client:
            response = self.post_endpoint_as_admin(
                client,
                "/api/admin/players/123/time",
                {
                    "platform": "teamspeak",
                    "total_time": 100,
                    "season_time": 40,
                    "reason": "manual correction",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["new_time"]["daily_time"], 100)
        self.assertEqual(payload["new_time"]["weekly_time"], 80)
        self.assertEqual(payload["new_time"]["monthly_time"], 100)
        queries = FakeDatabase.instances[0].cursor.queries
        time_insert = next(query for query in queries if "INSERT INTO time" in query[0])
        self.assertEqual(
            time_insert[1],
            ("teamspeak-user", "teamspeak", 100, 100, 80, 100, 40),
        )
        self.assertTrue(any("UPDATE user" in query and "SET level = ?" in query for query, _ in queries))

    def test_time_update_rejects_disabled_or_stale_targets(self):
        FakeDatabase.fetchone_results = [
            (123, "76561198000000000", "discord-user", None, "Player", 1, 1, 1),
        ]

        with self.make_app().test_client() as client:
            response = self.post_endpoint_as_admin(
                client,
                "/api/admin/players/123/time",
                {
                    "platform": "discord",
                    "total_time": 100,
                    "season_time": 40,
                    "reason": "manual correction",
                },
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "ranking-disabled users cannot be edited")

    def test_join_date_rejects_future_dates_before_mutating_user(self):
        with self.make_app().test_client() as client:
            response = self.post_endpoint_as_admin(
                client,
                "/api/admin/players/123/join-date",
                {"created_at": "2999-01-01", "reason": "bad date"},
            )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "created_at must not be in the future")
        queries = FakeDatabase.instances[0].cursor.queries
        self.assertFalse(any("UPDATE user" in query and "SET created_at" in query for query, _ in queries))

    def test_special_achievement_grant_uses_selected_users_platform_id(self):
        FakeDatabase.fetchone_results = [
            (123, "76561198000000000", "discord-user", "teamspeak-user", "Player", 1, 1, 0),
            None,
        ]

        with self.make_app().test_client() as client:
            response = self.post_endpoint_as_admin(
                client,
                "/api/admin/special-achievements/grant",
                {
                    "user_id": 123,
                    "platform": "discord",
                    "platform_id": "malicious-ignored",
                    "achievement_type": 1,
                    "reason": "manual grant",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertEqual(payload["platform_uid"], "discord-user")
        self.assertTrue(payload["created"])
        queries = FakeDatabase.instances[0].cursor.queries
        grant_insert = next(
            query for query in queries if "INSERT INTO special_achievements" in query[0]
        )
        self.assertEqual(grant_insert[1], ("discord", "discord-user", 1))

    def test_special_achievement_revoke_is_idempotent_and_audited(self):
        FakeDatabase.fetchone_results = [
            (123, "76561198000000000", "discord-user", "teamspeak-user", "Player", 1, 1, 0),
            None,
        ]

        with self.make_app().test_client() as client:
            response = self.post_endpoint_as_admin(
                client,
                "/api/admin/special-achievements/revoke",
                {
                    "user_id": 123,
                    "platform": "teamspeak",
                    "achievement_type": 2,
                    "reason": "manual revoke",
                },
            )

        self.assertEqual(response.status_code, 200)
        payload = response.get_json()
        self.assertFalse(payload["deleted"])
        queries = FakeDatabase.instances[0].cursor.queries
        self.assertFalse(any("DELETE FROM special_achievements" in query for query, _ in queries))
        self.assertIn("INSERT INTO admin_audit_log", queries[-1][0])

    def test_unlink_disables_original_user_when_no_platform_link_remains(self):
        FakeDatabase.fetchone_results = [
            (123, "76561198000000000", None, "teamspeak-user", "Player", 1, 1, 0),
            None,
            (0, 0),
            (0, 0),
        ]

        with self.make_app().test_client() as client:
            response = self.post_endpoint_as_admin(
                client,
                "/api/admin/steam/unlink",
                {
                    "user_id": 123,
                    "platform": "teamspeak",
                    "reason": "split stale steam shell",
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()["original_user_disabled"])
        queries = FakeDatabase.instances[0].cursor.queries
        original_update = next(
            query for query in queries if "ranking_disabled = CASE" in query[0]
        )
        self.assertEqual(original_update[1][0:3], (1, 1, 1))


if __name__ == "__main__":
    unittest.main()
