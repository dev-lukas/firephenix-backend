from flask import Blueprint, jsonify, request, session
from app.utils.database import DatabaseManager
from app.utils.valkey_manager import ValkeyManager
from app.utils.security import limiter, login_required, handle_errors

valkey_manager = ValkeyManager()

user_profile_channel_apex_bp = Blueprint('/api/user/profile/channel/apex', __name__)

@user_profile_channel_apex_bp.route('/api/user/profile/channel/apex', methods=['POST'])
@login_required
@handle_errors
@limiter.limit("3 per 10 minutes")
def create_channel():
    platform = request.json.get('platform')
    steam_id = session.get('steam_id')
    
    if not all([platform, steam_id]):
        return jsonify({'error': 'Missing required fields'}), 400
        
    if platform not in ['discord', 'teamspeak']:
        return jsonify({'error': 'Invalid platform'}), 400
    
    db = DatabaseManager()
    query = f"""SELECT level, discord_id, teamspeak_id, {platform}_channel
    FROM user
    WHERE steam_id = ? AND steam_id IS NOT NULL
    """

    db.cursor.execute(query, (steam_id,))
    user_data = db.cursor.fetchone()

    if not user_data:
        return jsonify({'error': 'This account has not yet linked his steamid with an account'}), 404

    level, discord_id, teamspeak_id, platform_channel = user_data

    if platform == 'discord' and discord_id is None or platform == 'teamspeak' and teamspeak_id is None:
        return jsonify({
            'error': 'This account has not yet linked the needed account'
        }), 400
    
    if not platform_channel:
        return jsonify({'error': 'This account has no channel on this plattform'}), 400
    if level < 20:
        return jsonify({
            'error': 'This account has not reached level 20'
        }), 400
    
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

    apex_achievement = 0
    rank_apex_achievement = 0

    if special_achievements_data:
        for achievement in special_achievements_data:
                achievement_type = achievement[0]
                if achievement_type == 200:
                    apex_achievement = 1
                elif achievement_type == 300:
                    rank_apex_achievement = 1
        
    if not apex_achievement and not rank_apex_achievement:
        return jsonify({'error': 'This account has not the requirements to unlock the Apex channel'}), 400
    
    query = """SELECT unlocked_at
    FROM unlockables
    WHERE platform = ? AND steam_id = ? AND unlockable_type = 1
    """

    db.cursor.execute(query, (platform, steam_id))
    unlockable_data = db.cursor.fetchone()

    if unlockable_data:
        return jsonify({'error': 'This account has already upgraded his channel to an apex channel'}), 404

    if not valkey_manager.set_apex_channel(platform, platform_channel):
        return jsonify({'error': 'Error moving channel'}), 500

    db.execute_query("""
        INSERT INTO unlockables (steam_id, platform, unlockable_type)
        VALUES (?, ?, 1)
    """, (steam_id, platform))

    db.close()

    return jsonify({'message': 'Channel successfully promoted to Apex'}), 200
