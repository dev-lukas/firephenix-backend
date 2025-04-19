from flask import Blueprint, jsonify
from app.utils.database import DatabaseManager
from app.utils.valkey_manager import ValkeyManager
from app.utils.security import limiter, handle_errors

valkey_manager = ValkeyManager()

ranking_user_bp = Blueprint('ranking_user', __name__)

@ranking_user_bp.route('/api/ranking/user', methods=['GET'])
@handle_errors
@limiter.limit("10 per minute")
def get_stats():
    db = DatabaseManager()
    query = """
    SELECT 
        COUNT(DISTINCT user.id) as total_users,
        COUNT(DISTINCT user.discord_id) as total_discord_users,
        COUNT(DISTINCT user.teamspeak_id) as total_teamspeak_users
    FROM user
    """
    
    result = db.execute_query(query)
    if result and result[0]:
        total_users, total_discord_users, total_teamspeak_users = result[0]
    else:
        total_users, total_discord_users, total_teamspeak_users = 0, 0, 0

    # Query for users per rank (level)
    query_ranks = "SELECT level, COUNT(*) as count FROM user GROUP BY level"
    result_ranks = db.execute_query(query_ranks)
    users_per_rank = {row[0]: row[1] for row in result_ranks} if result_ranks else {}

    # Query for users per division
    query_divisions = "SELECT division, COUNT(*) as count FROM user GROUP BY division"
    result_divisions = db.execute_query(query_divisions)
    users_per_division = {row[0]: row[1] for row in result_divisions} if result_divisions else {}

    rankings = {
        'total_users': total_users,
        'total_discord_users': total_discord_users,
        'total_teamspeak_users': total_teamspeak_users,
        'users_per_rank': users_per_rank,
        'users_per_division': users_per_division
    }

    return jsonify(rankings)