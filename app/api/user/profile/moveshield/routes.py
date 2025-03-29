from flask import Blueprint, jsonify, request, session
from app.utils.database import DatabaseManager
from app.utils.security import limiter, login_required
from app.utils.redis_manager import RedisManager

user_profile_moveshield_bp = Blueprint('/api/user/profile/moveshield/', __name__)

redis_manager = RedisManager()

@user_profile_moveshield_bp.route('/api/user/profile/moveshield', methods=['POST'])
@login_required
@limiter.limit("1 per minute")
def set_move_shield():
    platform = request.json.get('platform')
    steam_id = session.get('steam_id')
    
    if not all([platform, steam_id]):
        return jsonify({'error': 'Missing required fields'}), 400
        
    if platform not in ['discord', 'teamspeak']:
        return jsonify({'error': 'Invalid platform'}), 400
    
    db = DatabaseManager()

    query = f"""SELECT {platform}_id, {platform}_moveable, level
    FROM user
    WHERE steam_id = ? AND steam_id IS NOT NULL
    """

    db.cursor.execute(query, (steam_id,))
    user_data = db.cursor.fetchone()
    if not user_data:
        return jsonify({'error': 'This account has not yet linked his steamid with an account'}), 404

    id, moveable, level = user_data

    if not id:
        return jsonify({'error': 'This account has not yet linked his steamid with an account'}), 404

    if not moveable:
        return jsonify({
            'error': 'This account already has a move shield'
        }), 400
    
    if level < 10:
        return jsonify({
            'error': 'This account has not reached level 10'
        }), 400
    
    if redis_manager.set_move_shield(platform, id, add=True):
        db.execute_query(f"""
            UPDATE user
                SET {platform}_moveable = 0
                WHERE steam_id = ?
        """, (steam_id,))
    else:
        return jsonify({'error': 'Error creating channel'}), 500
    
    db.close()
    return jsonify({'message': 'Move shield activated'})

@user_profile_moveshield_bp.route('/api/user/profile/moveshield', methods=['DELETE'])
@login_required
@limiter.limit("1 per minute")
def remove_move_shield():
    platform = request.json.get('platform')
    steam_id = session.get('steam_id')
    
    if not all([platform, steam_id]):
        return jsonify({'error': 'Missing required fields'}), 400
        
    if platform not in ['discord', 'teamspeak']:
        return jsonify({'error': 'Invalid platform'}), 400
    
    db = DatabaseManager()

    query = f"""SELECT {platform}_id, {platform}_moveable, level
    FROM user
    WHERE steam_id = ? AND steam_id IS NOT NULL
    """

    db.cursor.execute(query, (steam_id,))
    user_data = db.cursor.fetchone()
    if not user_data:
        return jsonify({'error': 'This account has not yet linked his steamid with an account'}), 404

    id, moveable, level = user_data

    if not id:
        return jsonify({'error': 'This account has not yet linked his steamid with an account'}), 404

    if moveable:
        return jsonify({
            'error': 'This account already has no move shield'
        }), 400
    
    if level < 10:
        return jsonify({
            'error': 'This account has not reached level 10'
        }), 400
    
    if redis_manager.set_move_shield(platform, id, add=False):
        db.execute_query(f"""
            UPDATE user
                SET {platform}_moveable = 1
                WHERE steam_id = ?
        """, (steam_id,))
    else:
        return jsonify({'error': 'Error creating channel'}), 500
    
    db.close()
    return jsonify({'message': 'Move shield activated'})
