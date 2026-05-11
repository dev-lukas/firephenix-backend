import re
import unittest

from flask import Flask, jsonify, request

from app import apply_proxy_fix
from app.config import Config
from app.utils.database import (
    SEASON_APEX_ACHIEVEMENT,
    can_claim_season_skin,
    can_upgrade_apex_channel,
    get_season_number_for_end_year,
    get_best_division_from_season_achievements,
    get_season_division_achievement_types,
    is_season_division_achievement_type,
)
from app.utils.security import csrf_required, generate_verification_code, login_required
from app.utils.steam import steamid64_to_steam2


class ConfigThresholdTests(unittest.TestCase):
    def test_level_lookup_uses_highest_reached_threshold(self):
        self.assertEqual(Config.get_level_for_minutes(0), 1)
        self.assertEqual(Config.get_level_for_minutes(599), 2)
        self.assertEqual(Config.get_level_for_minutes(600), 3)
        self.assertEqual(Config.get_level_for_minutes(1_800_000), 25)

    def test_division_lookup_uses_highest_reached_threshold(self):
        self.assertEqual(Config.get_division_for_minutes(0), 1)
        self.assertEqual(Config.get_division_for_minutes(2_999), 1)
        self.assertEqual(Config.get_division_for_minutes(3_000), 2)
        self.assertEqual(Config.get_division_for_minutes(24_000), 5)


class SeasonRewardHelperTests(unittest.TestCase):
    def test_steamid64_to_steam2_conversion(self):
        self.assertEqual(steamid64_to_steam2("76561198000000000"), "STEAM_0:0:19867136")
        self.assertEqual(steamid64_to_steam2("76561198000000001"), "STEAM_0:1:19867136")

    def test_ttt_season_reward_uuids_are_hardcoded(self):
        self.assertEqual(
            Config.TTT_SEASON_REWARD_ITEM_UUIDS,
            {
                2: "66C32AD2-0232-4AF0-9F5E-B90D06DD61BA",
                3: "36648F60-EA1F-449A-94AD-98914B3BF8AC",
                4: "E2223E93-6831-4C3E-A295-3086153172F6",
                5: "E5FF810F-AEC9-4F36-9333-36CA21F82B64",
                6: "7FEBD81C-6F6D-4C6F-871F-84CD6D42D517",
            },
        )

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
