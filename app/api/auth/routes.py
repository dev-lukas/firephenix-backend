# auth/steam.py
from flask import Blueprint, request, redirect, session, jsonify
import requests
import secrets
from urllib.parse import urlencode
from app.config import Config
from app.utils.security import csrf_required, generate_csrf_token, limiter, handle_errors

auth_bp = Blueprint('/api/auth', __name__)

@auth_bp.route('/api/auth')
@handle_errors
@limiter.limit("3 per minute")
def steam_login():
    state = secrets.token_urlsafe(32)
    session['steam_openid_state'] = state
    site_url = Config.SITE_URL.rstrip('/')
    params = {
        'openid.ns': 'http://specs.openid.net/auth/2.0',
        'openid.identity': 'http://specs.openid.net/auth/2.0/identifier_select',
        'openid.claimed_id': 'http://specs.openid.net/auth/2.0/identifier_select',
        'openid.mode': 'checkid_setup',
        'openid.return_to': f'{site_url}/api/auth/callback?{urlencode({"state": state})}',
        'openid.realm': site_url
    }
    return redirect(f'{Config.STEAM_OPENID_URL}?{urlencode(params)}')

@auth_bp.route('/api/auth/callback')
@handle_errors
def steam_callback():
    expected_state = session.pop('steam_openid_state', None)
    if not expected_state or not secrets.compare_digest(expected_state, request.args.get('state', '')):
        return jsonify({'error': 'Invalid Steam login state'}), 400

    signed = request.args.get('openid.signed')
    claimed_id = request.args.get('openid.claimed_id')
    if not signed or not claimed_id:
        return jsonify({'error': 'Invalid Steam login'}), 400

    params = {
        'openid.assoc_handle': request.args.get('openid.assoc_handle'),
        'openid.signed': signed,
        'openid.sig': request.args.get('openid.sig'),
        'openid.ns': request.args.get('openid.ns'),
        'openid.mode': 'check_authentication'
    }

    signed_params = signed.split(',')
    for param in signed_params:
        params[f'openid.{param}'] = request.args.get(f'openid.{param}')

    response = requests.post(Config.STEAM_OPENID_URL, data=params, timeout=5)
    response.raise_for_status()

    if not any(line.strip() == 'is_valid:true' for line in response.text.splitlines()):
        return jsonify({'error': 'Invalid Steam login'}), 401

    steam_id = claimed_id.rstrip('/').split('/')[-1]
    if not steam_id.isdigit():
        return jsonify({'error': 'Invalid Steam login'}), 400

    session.clear()
    session.permanent = True
    session['steam_id'] = steam_id
    generate_csrf_token()

    return redirect('/profile')

@auth_bp.route('/api/auth/check')
@handle_errors
def check_auth():
    """Check if user is authenticated with steam"""
    response = jsonify({
        'authenticated': 'steam_id' in session,
        'steam_id': session.get('steam_id'),
        'csrf_token': session.get('csrf_token') if 'steam_id' in session else None,
        'is_admin': str(session.get('steam_id')) in Config.ADMIN_STEAM_IDS if 'steam_id' in session else False
    })

    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"
    return response

@auth_bp.route('/api/auth/logout', methods=['POST'])
@csrf_required
@handle_errors
def logout():
    """Logout user"""
    session.clear()
    return jsonify({'message': 'Logged out successfully'})
