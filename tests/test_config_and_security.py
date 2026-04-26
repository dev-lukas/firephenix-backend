import re
import unittest

from flask import Flask, jsonify

from app.config import Config
from app.utils.security import csrf_required, generate_verification_code, login_required


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


if __name__ == '__main__':
    unittest.main()
