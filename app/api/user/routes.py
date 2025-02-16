from flask import Blueprint, jsonify, request, session
from app.utils.database import DatabaseManager
from app.utils.security import login_required

user_bp = Blueprint('/api/user', __name__)

@user_bp.route('/api/user', methods=['GET'])
@login_required
def get_connected_users():
    steam_id = session.get('steam_id')

    if not steam_id:
        return jsonify({'error': 'No steam ID in session'}), 401
    
    db = DatabaseManager()

    query = """
        SELECT 
            u.name, u.discord_id, u.teamspeak_id, u.level,
            u.division, u.discord_channel, u.teamspeak_channel,
            COALESCE(SUM(t.total_time), 0) as total_time,
            COALESCE(SUM(t.daily_time), 0) as daily_time,
            COALESCE(SUM(t.weekly_time), 0) as weekly_time,
            COALESCE(SUM(t.monthly_time), 0) as monthly_time
        FROM user u
        LEFT JOIN time t ON 
            (t.platform = 'discord' AND t.platform_uid = u.discord_id) OR
            (t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id)
        WHERE u.steam_id = ?
        GROUP BY u.id, u.name, u.discord_id, u.teamspeak_id, u.level,
                 u.division, u.discord_channel, u.teamspeak_channel
    """

    results = db.execute_query(query, (steam_id,))
    db.close()

    if not results:
        response = jsonify({
            'name': None,
            'discord_id': None,
            'teamspeak_id': None,
            'level': 0,
            'division': None,
            'discord_channel': None,
            'teamspeak_channel': None,
            'total_time': 0,
            'daily_time': 0,
            'weekly_time': 0,
            'monthly_time': 0
        })
    else:
        row = results[0]
        response = jsonify({
            'name': row[0],
            'discord_id': str(row[1]),
            'teamspeak_id': str(row[2]),
            'level': row[3],
            'division': row[4],
            'discord_channel': row[5],
            'teamspeak_channel': row[6],
            'total_time': row[7],
            'daily_time': row[8],
            'weekly_time': row[9],
            'monthly_time': row[10]
        })

    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"

    return response
