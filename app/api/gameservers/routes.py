from flask import Blueprint, jsonify

from app.utils.gameserver_manager import GameServerCommandClient
from app.utils.security import admin_required, csrf_required, handle_errors


gameservers_bp = Blueprint('gameservers', __name__)


def run_ttt_command(command: str):
    payload, status_code = GameServerCommandClient().command('ttt', command)
    return jsonify(payload), status_code


@gameservers_bp.route('/api/gameservers/ttt/status')
@admin_required
@handle_errors
def ttt_status():
    return run_ttt_command('status')


@gameservers_bp.route('/api/gameservers/ttt/restart', methods=['POST'])
@admin_required
@csrf_required
@handle_errors
def ttt_restart():
    return run_ttt_command('restart')


@gameservers_bp.route('/api/gameservers/ttt/start', methods=['POST'])
@admin_required
@csrf_required
@handle_errors
def ttt_start():
    return run_ttt_command('start')


@gameservers_bp.route('/api/gameservers/ttt/stop', methods=['POST'])
@admin_required
@csrf_required
@handle_errors
def ttt_stop():
    return run_ttt_command('stop')
