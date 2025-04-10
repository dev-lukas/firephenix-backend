from flask import Blueprint, jsonify, session
from app.config import Config
from app.utils.database import DatabaseManager
from app.utils.security import login_required, handle_errors
from app.utils.security import limiter

user_achievements_bp = Blueprint('/api/user/achievements', __name__)

@user_achievements_bp.route('/api/user/achievements', methods=['GET'])
@login_required
@handle_errors
@limiter.limit("10 per minute")
def get_achievements():
    steam_id = session.get('steam_id')

    if not steam_id:
        return jsonify({'error': 'No steam ID in session'}), 401
    
    db = DatabaseManager()

    query = """
        SELECT 
            u.discord_id, 
            u.teamspeak_id, 
            u.division,
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.total_time ELSE 0 END) + 
                     SUM(CASE WHEN t.platform = 'teamspeak' THEN t.total_time ELSE 0 END), 0) as total_time
        FROM user u
        LEFT JOIN time t ON 
            (t.platform = 'discord' AND t.platform_uid = u.discord_id) OR
            (t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id)
        GROUP BY u.name, u.discord_id, u.teamspeak_id, u.level,
                 u.division, u.discord_channel, u.teamspeak_channel
        WHERE u.steam_id = ?
    """

    results = db.execute_query(query, (steam_id,))

    if not results:
        return jsonify({'error': 'User has no linked account yet'}), 404

    user_data = results[0]
    discord_id = str(user_data[0]) if user_data[0] else None
    teamspeak_id = str(user_data[1]) if user_data[1] else None
    division, total_time = user_data[2], user_data[3]

    streak_query = """
    SELECT 
        SUM(logins) as total_logins,
        MAX(longest_streak) as max_longest_streak
    FROM login_streak
    WHERE (platform = 'discord' AND platform_uid = ?)
        OR (platform = 'teamspeak' AND platform_uid = ?)
    """
    
    streak_data = db.execute_query(streak_query, (discord_id, teamspeak_id))

    if not streak_data:
        total_logins = 0
        longest_streak = 0
    else:
        total_logins = streak_data[0][0]
        longest_streak = streak_data[0][1]

    response = jsonify({
        'test': 0,
    })

    db.close()
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"

    return response
