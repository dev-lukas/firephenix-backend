from flask import Blueprint, jsonify, request
from app.utils.logger import RankingLogger
from app.utils.database import DatabaseManager

ranking_top_bp = Blueprint('ranking_top', __name__)

@ranking_top_bp.route('/api/ranking/top', methods=['GET'])
def get_top_ranking():
    logging = RankingLogger(__name__).get_logger()
    
    try:
        period = request.args.get('period', 'total')  # 'total', 'weekly', or 'monthly'
        
        if period == 'weekly':
            time_column = 'weekly_time'
        elif period == 'monthly':
            time_column = 'monthly_time'
        else:
            time_column = 'total_time'
        
        db = DatabaseManager()
        query = f"""
        SELECT 
            id,
            COALESCE(name, 'Unknown') as name,
            COALESCE(level, 0) as level,
            {time_column} as minutes
        FROM user_time
        WHERE {time_column} > 0
        ORDER BY {time_column} DESC
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
    
    except Exception as e:
        logging.error(f"Error getting ranking stats: {e}")
        return jsonify({'error': 'Internal Server Error'}), 500