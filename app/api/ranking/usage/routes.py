from flask import Blueprint, jsonify, request
from app.utils.database import DatabaseManager
from app.utils.logger import RankingLogger

ranking_usage_bp = Blueprint('ranking_usage', __name__)

@ranking_usage_bp.route('/api/ranking/usage', methods=['GET'])
def get_usage_stats():

    logging = RankingLogger(__name__).get_logger()

    try:
        period = request.args.get('period', 'daily')  # 'daily' or 'weekly'
        hours = 24 if period == 'daily' else 24 * 7
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

        stats = {
            'labels': [stat[0].strftime('%H' if period == 'daily' else '%a %H') 
                      for stat in result],
            'data': [stat[1] for stat in result]
        }

        return jsonify(stats)
        
    except Exception as e:
        logging.error(f"Error in get_usage_stats: {e}")
        return jsonify({'error': 'Internal server error'}), 500