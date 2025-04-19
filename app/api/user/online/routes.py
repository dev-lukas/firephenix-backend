from flask import Blueprint, jsonify, request
from app.utils.database import DatabaseManager
from app.utils.valkey_manager import ValkeyManager
from app.utils.security import limiter, handle_errors

valkey_manager = ValkeyManager()

user_online_bp = Blueprint('/api/user/online', __name__)

@user_online_bp.route('/api/user/online', methods=['GET'])
@handle_errors
@limiter.limit("10 per minute")
def get_connected_users():
    platform = request.args.get('platform')
    if platform not in ['discord', 'teamspeak']:
        return jsonify({'error': 'Invalid platform'}), 400
    
    online_users = valkey_manager.get_online_users(platform)
    id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'
    placeholders = ','.join(['?'] * len(online_users))
    
    if not online_users:
        return jsonify({'users': []})
    
    query = f"""
        SELECT {id_column}, name 
        FROM user
        WHERE {id_column} IN ({placeholders})
    """
    
    db = DatabaseManager()
    results = db.execute_query(query, tuple(online_users))
    db.close()

    user_data = []
    for user_id, name in results:
        user_data.append({
            'id': str(user_id),
            'name': name
        })
    
    response = jsonify({
        'users': user_data
    })

    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"

    return response