from flask import Blueprint, jsonify, request, session
from app.utils.database import DatabaseManager
from app.utils.security import limiter, login_required, handle_errors
from app.utils.valkey_manager import ValkeyManager

user_profile_moveshield_bp = Blueprint('/api/user/profile/skins', __name__)

valkey_manager = ValkeyManager()

@user_profile_moveshield_bp.route('/api/user/profile/skins', methods=['POST'])
@login_required
@handle_errors
@limiter.limit("1 per minute")
def set_skin():
    platform = request.json.get('platform')
    tier = request.json.get('tier')
    steam_id = session.get('steam_id')
    
    if not all([platform, steam_id, tier]):
        return jsonify({'error': 'Missing required fields'}), 400
        
    if platform not in ['garrysmod']:
        return jsonify({'error': 'Invalid platform'}), 400
    
    if tier not in [1, 2, 3, 4, 5, 6]:
        return jsonify({'error': 'Invalid tier'}), 400
    
    db = DatabaseManager()

    query = """SELECT teamspeak_id, discord_id
    FROM user
    WHERE steam_id = ? AND steam_id IS NOT NULL
    """

    db.cursor.execute(query, (steam_id,))
    user_data = db.cursor.fetchone()
    if not user_data:
        return jsonify({'error': 'This account has not yet linked his steamid with an account'}), 404

    teamspeak_id, discord_id = user_data

    if not teamspeak_id and not discord_id:
        return jsonify({'error': 'This account has not yet linked his steamid with an account'}), 404

    special_achievements_query = """
        SELECT achievement_type
        FROM special_achievements
        WHERE (platform = 'discord' AND platform_id = ?)
           OR (platform = 'teamspeak' AND platform_id = ?)
        """

    special_achievements_params = []
    if discord_id:
        special_achievements_params.append(discord_id)
    else:
        special_achievements_params.append(None)

    if teamspeak_id:
        special_achievements_params.append(teamspeak_id)
    else:
        special_achievements_params.append(None) 

    special_achievements_data = []
    if discord_id or teamspeak_id:
        special_achievements_data = db.execute_query(special_achievements_query, tuple(special_achievements_params))

    best_division_achieved = 0
    if special_achievements_data:
            for achievement in special_achievements_data:
                achievement_type = achievement[0]
                if 101 <= achievement_type <= 106:
                    best_division_achieved = max(best_division_achieved, achievement_type - 100)  

    if tier != best_division_achieved:
        return jsonify({
            'error': 'This account has not reached the needed tier'
        }), 400

    # Check if the skin was already gifted in the database

    if valkey_manager.unlock_skin(platform, tier, steam_id):
        # Update Database here
        pass
    else:
        return jsonify({'error': 'Error gifting skin'}), 500
    
    db.close()
    return jsonify({'message': 'Move shield activated'})