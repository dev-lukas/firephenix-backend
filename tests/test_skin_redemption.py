import unittest

from flask import Flask

from app.api.user.profile.skins import routes as skin_routes
from app.config import Config


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.fetchone_result = None
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))
        if "FROM user" in query:
            self.fetchone_result = self.db.user_row
        elif "FROM unlockables" in query:
            self.fetchone_result = self.db.unlock_row

    def fetchone(self):
        return self.fetchone_result

    def close(self):
        pass


class FakeDatabase:
    instances = []
    user_row = ("ts-user", "discord-user")
    achievements = [(1001,), (1002,), (1003,)]
    unlock_row = None

    def __init__(self):
        self.cursor = FakeCursor(self)
        self.inserts = []
        self.closed = False
        FakeDatabase.instances.append(self)

    def execute_query(self, query, params=None):
        if query.strip().upper().startswith("SELECT"):
            return self.achievements
        self.inserts.append((query, params))
        return None

    def close(self):
        self.closed = True


class StubValkeyManager:
    def __init__(self, response=None):
        self.response = response or ({"ok": True}, 200)
        self.calls = []

    def gameserver_command(self, server_id, command, data=None, **kwargs):
        self.calls.append((server_id, command, data, kwargs))
        return self.response


class SkinRedemptionRouteTests(unittest.TestCase):
    def setUp(self):
        self.original_db = skin_routes.DatabaseManager
        self.original_valkey_manager = skin_routes.valkey_manager
        self.original_rewards = Config.TTT_SEASON_REWARD_ITEM_UUIDS
        skin_routes.DatabaseManager = FakeDatabase
        FakeDatabase.instances = []
        FakeDatabase.user_row = ("ts-user", "discord-user")
        FakeDatabase.achievements = [(1001,), (1002,), (1003,)]
        FakeDatabase.unlock_row = None
        Config.TTT_SEASON_REWARD_ITEM_UUIDS = {
            2: "66C32AD2-0232-4AF0-9F5E-B90D06DD61BA",
            3: "36648F60-EA1F-449A-94AD-98914B3BF8AC",
            4: "E2223E93-6831-4C3E-A295-3086153172F6",
            5: "E5FF810F-AEC9-4F36-9333-36CA21F82B64",
            6: "7FEBD81C-6F6D-4C6F-871F-84CD6D42D517",
        }

    def tearDown(self):
        skin_routes.DatabaseManager = self.original_db
        skin_routes.valkey_manager = self.original_valkey_manager
        Config.TTT_SEASON_REWARD_ITEM_UUIDS = self.original_rewards

    def make_app(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.config["RATELIMIT_ENABLED"] = False
        app.register_blueprint(skin_routes.user_profile_skins_bp)
        return app

    def post_skin(self, client, body):
        with client.session_transaction() as session:
            session["steam_id"] = "76561198000000000"
            session["csrf_token"] = "known-token"
        return client.post(
            "/api/user/profile/skins",
            json=body,
            headers={"X-CSRF-Token": "known-token"},
        )

    def test_uses_session_steam_id_and_inserts_after_ttt_success(self):
        stub = StubValkeyManager()
        skin_routes.valkey_manager = stub

        with self.make_app().test_client() as client:
            response = self.post_skin(client, {
                "platform": "garrysmod",
                "tier": 2,
                "steam_id": "76561198000000001",
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(stub.calls[0][0], "ttt")
        self.assertEqual(stub.calls[0][1], "grant_season_skin")
        command_payload = stub.calls[0][2]
        self.assertEqual(command_payload["steam_id64"], "76561198000000000")
        self.assertEqual(command_payload["steam_id2"], "STEAM_0:0:19867136")
        self.assertEqual(command_payload["item_uuid"], Config.TTT_SEASON_REWARD_ITEM_UUIDS[2])
        self.assertEqual(stub.calls[0][3]["timeout_seconds"], 25)
        self.assertEqual(FakeDatabase.instances[0].inserts[0][1], ("76561198000000000", 12))

    def test_rejects_duplicate_without_calling_ttt(self):
        FakeDatabase.unlock_row = ("2026-05-06",)
        stub = StubValkeyManager()
        skin_routes.valkey_manager = stub

        with self.make_app().test_client() as client:
            response = self.post_skin(client, {"platform": "garrysmod", "tier": 2})

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.get_json()["error"], "This skin has already been unlocked")
        self.assertEqual(stub.calls, [])
        self.assertEqual(FakeDatabase.instances[0].inserts, [])

    def test_ttt_failure_does_not_insert_unlockable(self):
        stub = StubValkeyManager(({"ok": False, "error": "invalid_reward_item"}, 502))
        skin_routes.valkey_manager = stub

        with self.make_app().test_client() as client:
            response = self.post_skin(client, {"platform": "garrysmod", "tier": 2})

        self.assertEqual(response.status_code, 502)
        self.assertEqual(response.get_json()["error"], "Reward item is not configured on the TTT server")
        self.assertEqual(FakeDatabase.instances[0].inserts, [])


if __name__ == "__main__":
    unittest.main()
