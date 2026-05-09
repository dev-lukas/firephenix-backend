import unittest

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

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchone(self):
        return FakeDatabase.fetchone_result

    def fetchall(self):
        return []

    def close(self):
        pass


class FakeDatabase:
    instances = []
    fetchone_result = None

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


if __name__ == "__main__":
    unittest.main()
