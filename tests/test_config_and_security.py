import re
import unittest

from flask import Flask, jsonify, request

from app import apply_proxy_fix
from app.config import Config
from app.utils.database import (
    DatabaseManager,
    SEASON_APEX_ACHIEVEMENT,
    can_claim_season_skin,
    can_upgrade_apex_channel,
    get_season_number_for_end_year,
    get_best_division_from_season_achievements,
    get_season_division_achievement_types,
    get_ttt_season_reward_key,
    get_ttt_season_skin_unlockable_type,
    parse_ttt_season_skin_unlockable_type,
    is_season_division_achievement_type,
)
from app.utils.security import csrf_required, generate_verification_code, login_required
from app.utils.steam import steamid64_to_steam2


class FakeCursor:
    def __init__(self, rows):
        self.rows = rows
        self.executed = []

    def execute(self, query, params=None):
        self.executed.append((query, params))

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self):
        self.commits = 0

    def commit(self):
        self.commits += 1


class DatabaseQueryHelperTests(unittest.TestCase):
    def test_execute_query_returns_rows_for_cte_selects(self):
        manager = DatabaseManager.__new__(DatabaseManager)
        manager.conn = FakeConnection()
        manager.cursor = FakeCursor([(1, "Player")])

        rows = manager.execute_query("""
            WITH ranked_users AS (
                SELECT 1 AS id, 'Player' AS name
            )
            SELECT * FROM ranked_users
        """)

        self.assertEqual(rows, [(1, "Player")])
        self.assertEqual(manager.conn.commits, 0)


class ConfigThresholdTests(unittest.TestCase):
    def test_valkey_connection_kwargs_include_acl_credentials_when_configured(self):
        original_settings = (
            Config.VALKEY_HOST,
            Config.VALKEY_PORT,
            Config.VALKEY_DB,
            Config.VALKEY_USERNAME,
            Config.VALKEY_PASSWORD,
        )
        try:
            Config.VALKEY_HOST = "valkey"
            Config.VALKEY_PORT = 6379
            Config.VALKEY_DB = 0
            Config.VALKEY_USERNAME = "backend"
            Config.VALKEY_PASSWORD = "secret"

            kwargs = Config.valkey_connection_kwargs()
        finally:
            (
                Config.VALKEY_HOST,
                Config.VALKEY_PORT,
                Config.VALKEY_DB,
                Config.VALKEY_USERNAME,
                Config.VALKEY_PASSWORD,
            ) = original_settings

        self.assertEqual(kwargs["host"], "valkey")
        self.assertEqual(kwargs["port"], 6379)
        self.assertEqual(kwargs["db"], 0)
        self.assertEqual(kwargs["username"], "backend")
        self.assertEqual(kwargs["password"], "secret")
        self.assertTrue(kwargs["decode_responses"])

    def test_valkey_connection_kwargs_omit_acl_credentials_when_unset(self):
        original_settings = (Config.VALKEY_USERNAME, Config.VALKEY_PASSWORD)
        try:
            Config.VALKEY_USERNAME = None
            Config.VALKEY_PASSWORD = None

            kwargs = Config.valkey_connection_kwargs()
        finally:
            Config.VALKEY_USERNAME, Config.VALKEY_PASSWORD = original_settings

        self.assertNotIn("username", kwargs)
        self.assertNotIn("password", kwargs)

    def test_level_lookup_uses_highest_reached_threshold(self):
        self.assertEqual(Config.get_level_for_minutes(0), 1)
        self.assertEqual(Config.get_level_for_minutes(599), 2)
        self.assertEqual(Config.get_level_for_minutes(600), 3)
        self.assertEqual(Config.get_level_for_minutes(1_800_000), 25)

    def test_division_lookup_uses_highest_reached_threshold(self):
        self.assertEqual(Config.get_division_for_minutes(0), 1)
        self.assertEqual(Config.get_division_for_minutes(1_499), 1)
        self.assertEqual(Config.get_division_for_minutes(1_500), 2)
        self.assertEqual(Config.get_division_for_minutes(9_000), 5)

    def test_ttt_achievement_levels_use_configured_thresholds(self):
        levels = Config.get_ttt_achievement_levels({
            "rounds_played": 50,
            "rounds_won": 24,
            "kills": 250,
        })

        self.assertEqual(levels, {
            "rounds_played": 3,
            "rounds_won": 2,
            "kills": 4,
        })


class SeasonRewardHelperTests(unittest.TestCase):
    def test_steamid64_to_steam2_conversion(self):
        self.assertEqual(steamid64_to_steam2("76561198000000000"), "STEAM_0:0:19867136")
        self.assertEqual(steamid64_to_steam2("76561198000000001"), "STEAM_0:1:19867136")

    def test_ttt_season_reward_uuids_cover_every_claimable_tier_uniquely(self):
        all_uuids = []
        for season, tiers in Config.TTT_SEASON_REWARD_ITEM_UUIDS.items():
            self.assertEqual(sorted(tiers), [2, 3, 4, 5, 6],
                             f"season {season} must configure tiers 2-6")
            for tier, item_uuid in tiers.items():
                self.assertRegex(
                    item_uuid,
                    r"^[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}$",
                    f"season {season} tier {tier} must be an uppercase UUID",
                )
                all_uuids.append(item_uuid)
        self.assertEqual(len(all_uuids), len(set(all_uuids)),
                         "reward item UUIDs must be unique across seasons/tiers")

    def test_division_achievement_markers_are_cumulative(self):
        self.assertEqual(get_season_division_achievement_types(1), [1001])
        self.assertEqual(get_season_division_achievement_types(4), [1001, 1002, 1003, 1004])
        self.assertEqual(get_season_division_achievement_types(6), [1001, 1002, 1003, 1004, 1005, 1006])

    def test_division_achievement_markers_are_capped(self):
        self.assertEqual(get_season_division_achievement_types(0), [])
        self.assertEqual(get_season_division_achievement_types(7), [1001, 1002, 1003, 1004, 1005, 1006])

    def test_division_achievement_markers_are_season_specific(self):
        self.assertEqual(get_season_number_for_end_year(2026), 1)
        self.assertEqual(get_season_number_for_end_year(2027), 2)
        self.assertEqual(get_season_division_achievement_types(5, season_number=2), [1011, 1012, 1013, 1014, 1015])
        self.assertEqual(get_season_division_achievement_types(6, season_number=3), [1021, 1022, 1023, 1024, 1025, 1026])

    def test_best_division_reads_season_one_only_by_default(self):
        self.assertEqual(get_best_division_from_season_achievements([1001, 1002, 1005, 1016]), 5)
        self.assertEqual(get_best_division_from_season_achievements([1011, 1012], season_number=2), 2)

    def test_season_division_marker_detection_uses_high_range(self):
        self.assertTrue(is_season_division_achievement_type(1001))
        self.assertTrue(is_season_division_achievement_type(1016))
        self.assertFalse(is_season_division_achievement_type(101))
        self.assertFalse(is_season_division_achievement_type(200))

    def test_skin_claim_requires_tier_at_or_below_best_division(self):
        self.assertTrue(can_claim_season_skin(5, 2))
        self.assertTrue(can_claim_season_skin(5, 5))
        self.assertFalse(can_claim_season_skin(5, 6))
        self.assertFalse(can_claim_season_skin(6, 1))

    def test_season_skin_reward_identifiers_are_derived_from_season_and_tier(self):
        self.assertEqual(get_ttt_season_reward_key(1, 2), "season_1_tier_2")
        self.assertEqual(get_ttt_season_reward_key(2, 6), "season_2_tier_6")
        self.assertEqual(get_ttt_season_skin_unlockable_type(1, 2), 12)
        self.assertEqual(get_ttt_season_skin_unlockable_type(2, 6), 26)
        self.assertEqual(parse_ttt_season_skin_unlockable_type(23), (2, 3))
        self.assertIsNone(parse_ttt_season_skin_unlockable_type(21))

    def test_apex_upgrade_uses_season_apex_or_level_25(self):
        self.assertTrue(can_upgrade_apex_channel(10, [SEASON_APEX_ACHIEVEMENT]))
        self.assertTrue(can_upgrade_apex_channel(25, []))
        self.assertFalse(can_upgrade_apex_channel(24, [1001, 1002, 1003, 1004, 1005, 1006]))


class SecurityHelperTests(unittest.TestCase):
    def test_verification_code_is_six_digits(self):
        code = generate_verification_code()

        self.assertRegex(code, re.compile(r'^\d{6}$'))

    def test_login_required_rejects_missing_steam_session(self):
        app = Flask(__name__)
        app.secret_key = 'test-secret'

        @app.route('/protected')
        @login_required
        def protected():
            return jsonify({'ok': True})

        with app.test_client() as client:
            response = client.get('/protected')

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.get_json(), {'error': 'Unauthorized'})

    def test_login_required_allows_authenticated_session(self):
        app = Flask(__name__)
        app.secret_key = 'test-secret'

        @app.route('/protected')
        @login_required
        def protected():
            return jsonify({'ok': True})

        with app.test_client() as client:
            with client.session_transaction() as session:
                session['steam_id'] = '76561198000000000'

            response = client.get('/protected')

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'ok': True})

    def test_csrf_required_rejects_missing_token(self):
        app = Flask(__name__)
        app.secret_key = 'test-secret'

        @app.route('/protected', methods=['POST'])
        @csrf_required
        def protected():
            return jsonify({'ok': True})

        with app.test_client() as client:
            with client.session_transaction() as session:
                session['csrf_token'] = 'known-token'

            response = client.post('/protected')

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {'error': 'CSRF token missing'})

    def test_csrf_required_rejects_invalid_token(self):
        app = Flask(__name__)
        app.secret_key = 'test-secret'

        @app.route('/protected', methods=['POST'])
        @csrf_required
        def protected():
            return jsonify({'ok': True})

        with app.test_client() as client:
            with client.session_transaction() as session:
                session['csrf_token'] = 'known-token'

            response = client.post('/protected', headers={'X-CSRF-Token': 'bad-token'})

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.get_json(), {'error': 'CSRF token invalid'})

    def test_csrf_required_allows_valid_token(self):
        app = Flask(__name__)
        app.secret_key = 'test-secret'

        @app.route('/protected', methods=['POST'])
        @csrf_required
        def protected():
            return jsonify({'ok': True})

        with app.test_client() as client:
            with client.session_transaction() as session:
                session['csrf_token'] = 'known-token'

            response = client.post('/protected', headers={'X-CSRF-Token': 'known-token'})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'ok': True})


class ProxyFixTests(unittest.TestCase):
    def setUp(self):
        self.original_proxy_settings = (
            Config.TRUST_PROXY_HEADERS,
            Config.PROXY_FIX_X_FOR,
            Config.PROXY_FIX_X_PROTO,
            Config.PROXY_FIX_X_HOST,
            Config.PROXY_FIX_X_PORT,
        )

    def tearDown(self):
        (
            Config.TRUST_PROXY_HEADERS,
            Config.PROXY_FIX_X_FOR,
            Config.PROXY_FIX_X_PROTO,
            Config.PROXY_FIX_X_HOST,
            Config.PROXY_FIX_X_PORT,
        ) = self.original_proxy_settings

    def test_proxy_fix_uses_forwarded_client_address(self):
        Config.TRUST_PROXY_HEADERS = True
        Config.PROXY_FIX_X_FOR = 1
        Config.PROXY_FIX_X_PROTO = 1
        Config.PROXY_FIX_X_HOST = 1
        Config.PROXY_FIX_X_PORT = 1

        app = Flask(__name__)
        apply_proxy_fix(app)

        @app.route('/remote-address')
        def remote_address():
            return jsonify({'remote_addr': request.remote_addr})

        with app.test_client() as client:
            response = client.get(
                '/remote-address',
                headers={
                    'X-Forwarded-For': '198.51.100.23',
                    'X-Forwarded-Proto': 'https',
                    'X-Forwarded-Host': 'firephenix.de',
                    'X-Forwarded-Port': '443',
                },
                environ_overrides={'REMOTE_ADDR': '127.0.0.1'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'remote_addr': '198.51.100.23'})

    def test_proxy_fix_ignores_forwarded_client_address_when_disabled(self):
        Config.TRUST_PROXY_HEADERS = False

        app = Flask(__name__)
        apply_proxy_fix(app)

        @app.route('/remote-address')
        def remote_address():
            return jsonify({'remote_addr': request.remote_addr})

        with app.test_client() as client:
            response = client.get(
                '/remote-address',
                headers={'X-Forwarded-For': '198.51.100.23'},
                environ_overrides={'REMOTE_ADDR': '127.0.0.1'},
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json(), {'remote_addr': '127.0.0.1'})


if __name__ == '__main__':
    unittest.main()
