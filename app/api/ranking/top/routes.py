from flask import Blueprint, jsonify, request
from app.utils.database import DatabaseManager
from app.utils.security import limiter, handle_errors

ranking_top_bp = Blueprint('ranking_top', __name__)

@ranking_top_bp.route('/api/ranking/top', methods=['GET'])
@handle_errors
@limiter.limit("10 per minute")
def get_top_ranking():
    period = request.args.get('period', 'total')  # 'total', 'weekly', or 'monthly'
    
    time_column = {
        'weekly': 'weekly_time',
        'monthly': 'monthly_time',
        'total': 'total_time'
    }.get(period, 'total_time')

    db = DatabaseManager()
    query = f"""
    SELECT 
        user.id,
        COALESCE(user.name, 'Unknown') as name,
        COALESCE(user.level, 0) as level,
        (COALESCE(discord_time.{time_column}, 0) + 
            COALESCE(teamspeak_time.{time_column}, 0)) as minutes
    FROM user
    LEFT JOIN time AS discord_time 
        ON user.discord_id = discord_time.platform_uid 
        AND discord_time.platform = 'discord'
    LEFT JOIN time AS teamspeak_time 
        ON user.teamspeak_id = teamspeak_time.platform_uid 
        AND teamspeak_time.platform = 'teamspeak'
    WHERE (COALESCE(discord_time.{time_column}, 0) + 
            COALESCE(teamspeak_time.{time_column}, 0)) > 0
    ORDER BY minutes DESC
    LIMIT 10
    """
    
    result = db.execute_query(query)
    db.close()
    
    top_players = [
        {
            'id': row[0],
            'name': row[1],
            'level': row[2],
            'minutes': row[3]
        }
        for row in result
    ] if result else []
    
    return jsonify(top_players)