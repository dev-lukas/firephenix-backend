from flask import Blueprint, jsonify, request
from app.utils.database import DatabaseManager
from app.utils.security import limiter, handle_errors

ranking_usage_bp = Blueprint('ranking_usage', __name__)

@ranking_usage_bp.route('/api/ranking/usage', methods=['GET'])
@handle_errors
@limiter.limit("10 per minute")
def get_usage():
    period = request.args.get('period', 'daily')
    hours = 24 * 7 if period == 'weekly' else 24
    db = DatabaseManager()
    
    query = """
    SELECT 
        timestamp,
        SUM(user_count) as total_users
    FROM usage_stats 
    WHERE timestamp >= DATE_SUB(NOW(), INTERVAL ? HOUR)
    GROUP BY timestamp
    ORDER BY timestamp ASC
    """

    result = db.execute_query(query, (hours,))
    db.close()
    stats = {
        'labels': [stat[0].strftime('%H' if period == 'daily' else '%a %H') 
                    for stat in result],
        'data': [stat[1] for stat in result]
    }

    return jsonify(stats)