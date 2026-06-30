import unittest

from flask import Flask

from app.api.user.profile.verification import routes as verification_routes


class FakeConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1


class FakeCursor:
    def __init__(self, db):
        self.db = db
        self.queries = []

    def execute(self, query, params=None):
        self.queries.append((query, params))

    def fetchall(self):
        query = self.queries[-1][0]
        if "FROM user" in query and "WHERE steam_id = ?" in query:
            return self.db.existing_users
        if "FROM user" in query and "_id = ?" in query:
            return self.db.merge_users
        return []


class FakeDatabase:
    instances = []
    verification_result = [(1, "teamspeak-uid", "123456", 0)]
    existing_users = []
    merge_users = []

    def __init__(self):
        self.conn = FakeConnection()
        self.cursor = FakeCursor(self)
        self.existing_users = FakeDatabase.existing_users
        self.merge_users = FakeDatabase.merge_users
        self.execute_queries = []
        FakeDatabase.instances.append(self)

    def execute_query(self, query, params=None):
        self.execute_queries.append((query, params))
        if "FROM verification" in query:
            return FakeDatabase.verification_result
        return []

    def close(self):
        pass


class FakeValkeyManager:
    def __init__(self):
        self.commands = []

    def publish_command(self, platform, command, **kwargs):
        self.commands.append((platform, command, kwargs))


class VerificationMergeTests(unittest.TestCase):
    def setUp(self):
        self.original_db = verification_routes.DatabaseManager
        self.original_valkey = verification_routes.valkey_manager
        FakeDatabase.instances = []
        FakeDatabase.verification_result = [(1, "teamspeak-uid", "123456", 0)]
        FakeDatabase.existing_users = []
        FakeDatabase.merge_users = []
        verification_routes.DatabaseManager = FakeDatabase
        verification_routes.valkey_manager = FakeValkeyManager()

    def tearDown(self):
        verification_routes.DatabaseManager = self.original_db
        verification_routes.valkey_manager = self.original_valkey

    def make_app(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.config["RATELIMIT_ENABLED"] = False
        app.register_blueprint(verification_routes.user_profile_verification_bp)
        return app

    def post_verify(self, client):
        with client.session_transaction() as session:
            session["steam_id"] = "76561198000000000"
            session["csrf_token"] = "known-token"
        return client.post(
            "/api/user/profile/verification/verify",
            json={"platform": "teamspeak", "code": "123456"},
            headers={"X-CSRF-Token": "known-token"},
        )

    def test_merge_preserves_disabled_ranking_from_merged_platform_account(self):
        FakeDatabase.existing_users = [(
            1,
            "76561198000000000",
            "discord-id",
            None,
            "Primary",
            3,
            2,
            11,
            None,
            1,
            1,
            0,
            None,
            None,
        )]
        FakeDatabase.merge_users = [(
            7,
            5,
            None,
            22,
            1,
            1,
            1,
            "2026-05-08 20:10:00",
            "Bot Account",
        )]

        with self.make_app().test_client() as client:
            response = self.post_verify(client)

        self.assertEqual(response.status_code, 200)
        queries = FakeDatabase.instances[0].cursor.queries
        update_params = next(
            params for query, params in queries
            if "UPDATE user" in query and "ranking_disabled" in query
        )
        self.assertEqual(update_params[8], 1)
        self.assertEqual(update_params[9], 1)
        self.assertEqual(update_params[10], "2026-05-08 20:10:00")
        self.assertEqual(update_params[11], "Bot Account")
        self.assertEqual(update_params[12], 1)


class VerificationAttemptLimitTests(unittest.TestCase):
    def setUp(self):
        self.original_db = verification_routes.DatabaseManager
        self.original_valkey = verification_routes.valkey_manager
        FakeDatabase.instances = []
        FakeDatabase.existing_users = []
        FakeDatabase.merge_users = []
        verification_routes.DatabaseManager = FakeDatabase
        verification_routes.valkey_manager = FakeValkeyManager()

    def tearDown(self):
        verification_routes.DatabaseManager = self.original_db
        verification_routes.valkey_manager = self.original_valkey

    def make_app(self):
        app = Flask(__name__)
        app.secret_key = "test-secret"
        app.config["RATELIMIT_ENABLED"] = False
        app.register_blueprint(verification_routes.user_profile_verification_bp)
        return app

    def post_code(self, client, code):
        with client.session_transaction() as session:
            session["steam_id"] = "76561198000000000"
            session["csrf_token"] = "known-token"
        return client.post(
            "/api/user/profile/verification/verify",
            json={"platform": "teamspeak", "code": code},
            headers={"X-CSRF-Token": "known-token"},
        )

    def _executed(self, db):
        return [query for query, _ in db.execute_queries]

    def test_wrong_code_increments_attempt_counter(self):
        FakeDatabase.verification_result = [(7, "teamspeak-uid", "123456", 1)]

        with self.make_app().test_client() as client:
            response = self.post_code(client, "000000")

        self.assertEqual(response.status_code, 400)
        db = FakeDatabase.instances[0]
        update = next(
            (query, params)
            for query, params in db.execute_queries
            if "UPDATE verification SET attempts" in query
        )
        self.assertEqual(update[1], (2, 7))
        self.assertFalse(any("DELETE FROM verification" in q for q in self._executed(db)))

    def test_wrong_code_on_last_attempt_invalidates_code(self):
        FakeDatabase.verification_result = [(7, "teamspeak-uid", "123456", 4)]

        with self.make_app().test_client() as client:
            response = self.post_code(client, "000000")

        self.assertEqual(response.status_code, 400)
        db = FakeDatabase.instances[0]
        self.assertTrue(any("DELETE FROM verification" in q for q in self._executed(db)))
        self.assertFalse(any("UPDATE verification SET attempts" in q for q in self._executed(db)))

    def test_exhausted_code_is_rejected_and_cleared(self):
        FakeDatabase.verification_result = [(7, "teamspeak-uid", "123456", 5)]

        with self.make_app().test_client() as client:
            response = self.post_code(client, "123456")

        self.assertEqual(response.status_code, 429)
        db = FakeDatabase.instances[0]
        self.assertTrue(any("DELETE FROM verification" in q for q in self._executed(db)))

    def test_correct_code_within_limit_succeeds(self):
        FakeDatabase.verification_result = [(7, "teamspeak-uid", "123456", 2)]

        with self.make_app().test_client() as client:
            response = self.post_code(client, "123456")

        self.assertEqual(response.status_code, 200)
        db = FakeDatabase.instances[0]
        self.assertFalse(any("UPDATE verification SET attempts" in q for q in self._executed(db)))


if __name__ == "__main__":
    unittest.main()
