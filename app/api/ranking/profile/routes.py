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
                COUNT(DISTINCT u.id) as total_users,
                AVG(COALESCE(d.total_time, 0) + COALESCE(t.total_time, 0)) as mean_time,
                MAX(COALESCE(d.total_time, 0) + COALESCE(t.total_time, 0)) as best_time
            FROM user u
            LEFT JOIN time d ON d.platform = 'discord' AND d.platform_uid = u.discord_id
            LEFT JOIN time t ON t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id
            WHERE COALESCE(d.total_time, 0) + COALESCE(t.total_time, 0) > 0
        )
        SELECT 
            u.id,
            RANK() OVER (ORDER BY (COALESCE(d.total_time, 0) + COALESCE(t.total_time, 0)) DESC) as rank,
            COALESCE(u.name, 'Unknown') as name,
            COALESCE(u.level, 1) as level,
            COALESCE(u.division, 1) as division,
            COALESCE(d.total_time, 0) + COALESCE(t.total_time, 0) as total_time,
            COALESCE(d.monthly_time, 0) + COALESCE(t.monthly_time, 0) as monthly_time,
            COALESCE(d.weekly_time, 0) + COALESCE(t.weekly_time, 0) as weekly_time,
            (SELECT total_users FROM user_stats) as total_users,
            (SELECT mean_time FROM user_stats) as mean_time,
            (SELECT best_time FROM user_stats) as best_time
        FROM user u
        LEFT JOIN time d ON d.platform = 'discord' AND d.platform_uid = u.discord_id
        LEFT JOIN time t ON t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id
        WHERE u.id = ?
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

