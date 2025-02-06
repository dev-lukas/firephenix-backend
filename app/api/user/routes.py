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
        SELECT name, discord_uid, teamspeak_uid, level,
               division, discord_channel, teamspeak_channel,
               total_time, daily_time, weekly_time, monthly_time
        FROM user_time 
        WHERE steam_id = ?
    """

    results = db.execute_query(query, (steam_id,))
    db.close()

    if not results:
        response = jsonify({
            'name': None,
            'discord_uid': None,
            'teamspeak_uid': None,
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
            'discord_uid': str(row[1]),
            'teamspeak_uid': str(row[2]),
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
