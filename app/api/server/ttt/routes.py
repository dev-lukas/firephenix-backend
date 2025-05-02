from flask import Blueprint, jsonify, request
from app.utils.security import limiter, handle_errors
from sourceserver.sourceserver import SourceServer

server_ttt_bp = Blueprint('server_ttt', __name__, url_prefix='/api/server/ttt')

@server_ttt_bp.route('/', methods=['GET'])
@handle_errors
@limiter.limit("10 per minute")
def get_ranking():
    address = request.args.get('address', 'gaming.firephenix.de')
    port = int(request.args.get('port', '27015'))
    
    try:
        connectionString = f"{address}:{port}"
        server = SourceServer(connectionString)
        server_info = server.info

        return jsonify({
            'online': True,
            'current_map': server_info['map'],
            'players': server_info['players'],
            'max_players': server_info['max_players']
        })
    except Exception as e:
        print(f"Error connecting to server: {e}")
        return jsonify({
            'online': False,
            'current_map': None,
            'players': 0,
            'max_players': 0
        })