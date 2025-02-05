from flask import Blueprint, jsonify, request
from app.config import Config
from app.utils.logger import RankingLogger
from app.utils.database import DatabaseManager

ranking_profile_bp = Blueprint('ranking_profile', __name__)

@ranking_profile_bp.route('/api/ranking/profile', methods=['GET'])
def get_ranking():
    logging = RankingLogger(__name__).get_logger()
    
    try:
        user_id = int(request.args.get('id', 1))
        db = DatabaseManager()
        
        query = """
        WITH user_stats AS (
            SELECT 
                COUNT(*) as total_users,
                AVG(total_time) as mean_time,
                MAX(total_time) as best_time
            FROM user_time
            WHERE total_time > 0
        )
        SELECT 
            id,
            RANK() OVER (ORDER BY total_time DESC) as rank,
            COALESCE(name, 'Unknown') as name,
            COALESCE(level, 1) as level,
            COALESCE(division, 1) as division,
            total_time,
            monthly_time,
            weekly_time,
            (SELECT total_users FROM user_stats) as total_users,
            (SELECT mean_time FROM user_stats) as mean_time,
            (SELECT best_time FROM user_stats) as best_time
        FROM user_time
        WHERE id = ?
        """
        
        db.cursor.execute(query, (user_id,))
        user_data = db.cursor.fetchone()
        db.close()
        if not user_data:
            return jsonify({'error': 'User not found'}), 404

        # Extract user data
        id, rank, name, level, division, total_time, monthly_time, weekly_time, total_users, mean_time, best_time = user_data
        
        # Calculate time to next level
        time_to_next = 0
        if level < 25:  # Max level is 25
            next_level_req = Config.get_level_requirement(level + 1)
            time_to_next = max(0, next_level_req - total_time)
        
        # Calculate rank percentage (top percentage)
        rank_percentage = (rank / total_users) * 100 if total_users > 0 else 0
        
        return jsonify({
            'id': id,
            'rank': rank,
            'name': name,
            'level': level,
            'division': division,
            'total_time': total_time,
            'monthly_time': monthly_time,
            'weekly_time': weekly_time,
            'time_to_next_level': time_to_next,
            'rank_percentage': rank_percentage,
            'mean_total_time': mean_time,
            'best_player_time': best_time
        })
    
    except Exception as e:
        logging.error(f"Error getting ranking: {e}")
        return jsonify({'error': 'Internal Server Error'}), 500

