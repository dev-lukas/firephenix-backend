from flask import Blueprint, jsonify
from app.utils.logger import RankingLogger
from app.utils.database import DatabaseManager
from app.bots.discordbot import DiscordBot
from app.bots.teamspeakbot import TeamspeakBot
from app.utils.security import limiter

ranking_stats_bp = Blueprint('ranking_stats', __name__)

@ranking_stats_bp.route('/api/ranking/stats', methods=['GET'])
@limiter.limit("10 per minute")
def get_stats():

    logging = RankingLogger(__name__).get_logger()

    try:
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
            len(DiscordBot().get_online_users()) +
            len(TeamspeakBot().get_online_users())
        )

        rankings = {
            'total_users': total_users,
            'total_time': total_time,
            'online_users': online_users
        }

        return jsonify(rankings)
    
    except Exception as e:
        logging.error(f"Error getting ranking stats: {e}")
        return jsonify({'error': 'Internal Server Error'}), 500