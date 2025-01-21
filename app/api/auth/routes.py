# auth/steam.py
from flask import Blueprint, request, redirect, session, jsonify
import requests
from urllib.parse import urlencode
from functools import wraps
from app.config import Config
from app.utils.security import rate_limit

auth_bp = Blueprint('/api/auth', __name__)

def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if 'steam_id' not in session:
            return jsonify({'error': 'Unauthorized'}), 401
        return f(*args, **kwargs)
    return decorated_function

@auth_bp.route('/api/auth')
@rate_limit(max_requests=5, window=60)
def steam_login():
    params = {
        'openid.ns': 'http://specs.openid.net/auth/2.0',
        'openid.identity': 'http://specs.openid.net/auth/2.0/identifier_select',
        'openid.claimed_id': 'http://specs.openid.net/auth/2.0/identifier_select',
        'openid.mode': 'checkid_setup',
        'openid.return_to': f'{Config.SITE_URL}/api/auth/callback',
        'openid.realm': Config.SITE_URL
    }
    return redirect(f'{Config.STEAM_OPENID_URL}?{urlencode(params)}')

@auth_bp.route('/api/auth/callback')
def steam_callback():
    try:
        params = {
            'openid.assoc_handle': request.args.get('openid.assoc_handle'),
            'openid.signed': request.args.get('openid.signed'),
            'openid.sig': request.args.get('openid.sig'),
            'openid.ns': request.args.get('openid.ns'),
            'openid.mode': 'check_authentication'
        }

        signed_params = request.args.get('openid.signed').split(',')
        for param in signed_params:
            params[f'openid.{param}'] = request.args.get(f'openid.{param}')

        response = requests.post(Config.STEAM_OPENID_URL, data=params)
        
        if 'is_valid:true' not in response.text:
            return jsonify({'error': 'Invalid Steam login'}), 401

        steam_id = request.args.get('openid.claimed_id').split('/')[-1]
        
        session['steam_id'] = steam_id

        return redirect(f'/profile')

    except Exception as e:
        return jsonify({'error': str(e)}), 500

@auth_bp.route('/api/auth/check')
def check_auth():
    """Check if user is authenticated"""
    return jsonify({
        'authenticated': 'steam_id' in session,
        'steam_id': session.get('steam_id')
    })

@auth_bp.route('/api/auth/logout')
def logout():
    """Logout user"""
    session.clear()
    return jsonify({'message': 'Logged out successfully'})