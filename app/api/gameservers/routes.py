from flask import Blueprint, jsonify

from app.utils.security import admin_required, csrf_required, handle_errors
from app.config import Config
from app.utils.source_server import (
    SourceServerQueryError,
    SourceServerTimeout,
    query_source_server,
)
from app.utils.valkey_manager import ValkeyManager


gameservers_bp = Blueprint('gameservers', __name__)
valkey_manager = ValkeyManager()

TTT_COMMAND_TIMEOUT_SECONDS = {
    'restart': 240,
    'start': 240,
    'stop': 240,
}


def run_ttt_command(command: str):
    payload, status_code = valkey_manager.gameserver_command(
        'ttt',
        command,
        timeout_seconds=TTT_COMMAND_TIMEOUT_SECONDS.get(command, 3),
    )
    return jsonify(payload), status_code


@gameservers_bp.route('/api/gameservers/ttt/status')
@handle_errors
def ttt_status():
    try:
        payload = query_source_server(
            Config.TTT_STATUS_HOST,
            Config.TTT_STATUS_PORT,
            timeout_seconds=Config.TTT_STATUS_TIMEOUT_SECONDS,
        )
        return jsonify({
            **payload,
            "server": "ttt",
            "host": Config.TTT_STATUS_HOST,
            "port": Config.TTT_STATUS_PORT,
        })
    except SourceServerTimeout:
        return jsonify({
            "ok": False,
            "status": "offline",
            "error": "source_query_timeout",
            "server": "ttt",
            "host": Config.TTT_STATUS_HOST,
            "port": Config.TTT_STATUS_PORT,
        })
    except SourceServerQueryError as error:
        return jsonify({
            "ok": False,
            "status": "unknown",
            "error": str(error),
            "server": "ttt",
            "host": Config.TTT_STATUS_HOST,
            "port": Config.TTT_STATUS_PORT,
        })


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
