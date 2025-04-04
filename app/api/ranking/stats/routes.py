from flask import Blueprint, jsonify
from app.utils.database import DatabaseManager
from app.utils.redis_manager import RedisManager
from app.utils.security import limiter, handle_errors

redis_manager = RedisManager()

ranking_stats_bp = Blueprint('ranking_stats', __name__)

@ranking_stats_bp.route('/api/ranking/stats', methods=['GET'])
@handle_errors
@limiter.limit("10 per minute")
def get_stats():
    db = DatabaseManager()
    query = """
    SELECT 
        COUNT(DISTINCT user.id) as total_users,
        COALESCE(SUM(discord_time.total_time), 0) + 
        COALESCE(SUM(teamspeak_time.total_time), 0) as total_time
    FROM user
    LEFT JOIN time AS discord_time 
        ON user.discord_id = discord_time.platform_uid 
        AND discord_time.platform = 'discord'
    LEFT JOIN time AS teamspeak_time 
        ON user.teamspeak_id = teamspeak_time.platform_uid 
        AND teamspeak_time.platform = 'teamspeak'
    WHERE discord_time.platform_uid IS NOT NULL 
        OR teamspeak_time.platform_uid IS NOT NULL
    """
    
    result = db.execute_query(query)
    if result and result[0]:
        total_users, total_time = result[0]
    else:
        total_users, total_time = 0, 0

    db.close()

    online_users = (
        len(redis_manager.get_online_users('discord')) +
        len(redis_manager.get_online_users('teamspeak'))
    )

    rankings = {
        'total_users': total_users,
        'total_time': total_time,
        'online_users': online_users
    }

    return jsonify(rankings)