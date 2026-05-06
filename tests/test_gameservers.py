import json
import unittest

from flask import Flask, jsonify

from app.api.gameservers import routes as gameserver_routes
from app.config import Config, parse_admin_steam_ids
from app.utils.security import admin_required
from app.utils.valkey_manager import ValkeyManager


class FakeValkey:
    def __init__(self, responses=None, status=None):
        self.responses = responses or {}
        self.status = status
        self.published = []
        self.deleted = []

    def publish(self, channel, payload):
        self.published.append((channel, json.loads(payload)))
        return 1

    def get(self, key):
        if key in self.responses:
            value = self.responses[key]
            if callable(value):
                return value()
            return value
        if key.endswith(":status"):
            return self.status
        return None

    def delete(self, key):
        self.deleted.append(key)


class ConfigAdminTests(unittest.TestCase):
    def test_parse_admin_steam_ids_accepts_commas_and_newlines(self):
        self.assertEqual(
            parse_admin_steam_ids("76561198000000000, 76561198000000001\n76561198000000002"),
            ["76561198000000000", "76561198000000001", "76561198000000002"],
        )


class AdminGuardTests(unittest.TestCase):
    def setUp(self):
        self.original_admins = Config.ADMIN_STEAM_IDS
        Config.ADMIN_STEAM_IDS = ["76561198000000000"]

    def tearDown(self):
        Config.ADMIN_STEAM_IDS = self.original_admins

    def make_app(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"

        @app.route("/admin")
        @admin_required
        def admin():
            return jsonify({"ok": True})

        return app

    def test_admin_required_rejects_missing_session(self):
        with self.make_app().test_client() as client:
            response = client.get("/admin")

        self.assertEqual(response.status_code, 401)

    def test_admin_required_rejects_non_admin(self):
        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000001"
            response = client.get("/admin")

        self.assertEqual(response.status_code, 403)

    def test_admin_required_allows_admin(self):
        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000000"
            response = client.get("/admin")

        self.assertEqual(response.status_code, 200)


class GameServerCommandTests(unittest.TestCase):
    def test_command_success_publishes_and_reads_response(self):
        response_json = json.dumps({"ok": True, "server": "ttt"})
        fake = FakeValkey()
        manager = ValkeyManager()
        original_valkey = manager.valkey
        manager.valkey = fake

        try:
            original_get = fake.get

            def get_after_publish(key):
                if ":responses:" in key:
                    return response_json
                return original_get(key)

            fake.get = get_after_publish
            payload, status = manager.gameserver_command("ttt", "status", timeout_seconds=0.01, poll_interval_seconds=0)
        finally:
            manager.valkey = original_valkey

        self.assertEqual(status, 200)
        self.assertEqual(payload["ok"], True)
        self.assertEqual(fake.published[0][0], "gameserver:ttt:commands")
        self.assertEqual(fake.published[0][1]["command"], "status")

    def test_command_publishes_extra_payload(self):
        fake = FakeValkey()
        manager = ValkeyManager()
        original_valkey = manager.valkey
        manager.valkey = fake
        try:
            fake.get = lambda key: json.dumps({"ok": True}) if ":responses:" in key else None

            payload, status = manager.gameserver_command("ttt", "grant_season_skin", {
                "steam_id64": "76561198000000000",
                "steam_id2": "STEAM_0:0:19867136",
                "tier": 2,
                "item_uuid": "66C32AD2-0232-4AF0-9F5E-B90D06DD61BA",
                "reward_key": "season_1_tier_2",
            }, timeout_seconds=0.01, poll_interval_seconds=0)
        finally:
            manager.valkey = original_valkey

        self.assertEqual(status, 200)
        self.assertEqual(payload["ok"], True)
        published = fake.published[0][1]
        self.assertEqual(published["command"], "grant_season_skin")
        self.assertEqual(published["steam_id64"], "76561198000000000")
        self.assertEqual(published["steam_id2"], "STEAM_0:0:19867136")

    def test_command_returns_manager_error(self):
        fake = FakeValkey()
        manager = ValkeyManager()
        original_valkey = manager.valkey
        manager.valkey = fake
        try:
            fake.get = lambda key: json.dumps({"ok": False, "error": "manager_error"}) if ":responses:" in key else None

            payload, status = manager.gameserver_command("ttt", "restart", timeout_seconds=0.01, poll_interval_seconds=0)
        finally:
            manager.valkey = original_valkey

        self.assertEqual(status, 502)
        self.assertEqual(payload["error"], "manager_error")

    def test_command_timeout_without_heartbeat_is_unavailable(self):
        fake = FakeValkey()
        manager = ValkeyManager()
        original_valkey = manager.valkey
        manager.valkey = fake

        try:
            payload, status = manager.gameserver_command("ttt", "restart", timeout_seconds=0.01, poll_interval_seconds=0)
        finally:
            manager.valkey = original_valkey

        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "manager_unavailable")

    def test_command_timeout_with_heartbeat_is_timeout(self):
        fake = FakeValkey(status=json.dumps({"manager": "online"}))
        manager = ValkeyManager()
        original_valkey = manager.valkey
        manager.valkey = fake

        try:
            payload, status = manager.gameserver_command("ttt", "restart", timeout_seconds=0.01, poll_interval_seconds=0)
        finally:
            manager.valkey = original_valkey

        self.assertEqual(status, 504)
        self.assertEqual(payload["error"], "manager_timeout")


class GameServerRouteTests(unittest.TestCase):
    def setUp(self):
        self.original_admins = Config.ADMIN_STEAM_IDS
        self.original_manager = gameserver_routes.valkey_manager
        Config.ADMIN_STEAM_IDS = ["76561198000000000"]

        class StubManager:
            def gameserver_command(self, server_id, command):
                return {"ok": True, "server": server_id, "command": command}, 200

        gameserver_routes.valkey_manager = StubManager()

    def tearDown(self):
        Config.ADMIN_STEAM_IDS = self.original_admins
        gameserver_routes.valkey_manager = self.original_manager

    def make_app(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.register_blueprint(gameserver_routes.gameservers_bp)
        return app

    def test_status_rejects_unauthenticated(self):
        with self.make_app().test_client() as client:
            response = client.get("/api/gameservers/ttt/status")

        self.assertEqual(response.status_code, 401)

    def test_status_rejects_non_admin(self):
        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000001"
            response = client.get("/api/gameservers/ttt/status")

        self.assertEqual(response.status_code, 403)

    def test_status_allows_admin(self):
        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000000"
            response = client.get("/api/gameservers/ttt/status")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["command"], "status")

    def test_restart_requires_csrf_for_admin(self):
        with self.make_app().test_client() as client:
            with client.session_transaction() as session:
                session["steam_id"] = "76561198000000000"
                session["csrf_token"] = "known-token"
            response = client.post(
                "/api/gameservers/ttt/restart",
                headers={"X-CSRF-Token": "known-token"},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["command"], "restart")


if __name__ == "__main__":
    unittest.main()
