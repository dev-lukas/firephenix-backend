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


@ranking_top_bp.route('/api/ranking/hall-of-fame', methods=['GET'])
@handle_errors
@limiter.limit("10 per minute")
def get_hall_of_fame():
    db = DatabaseManager()

    def query_top3(sql):
        result = db.execute_query(sql)
        return [{'id': r[0], 'name': r[1], 'level': r[2], 'value': r[3]} for r in result] if result else []

    longest_streak = query_top3("""
        SELECT u.id, COALESCE(u.name,'Unknown'), u.level, MAX(ls.longest_streak) as val
        FROM user u
        INNER JOIN login_streak ls ON
            (ls.platform='discord' AND ls.platform_uid=u.discord_id) OR
            (ls.platform='teamspeak' AND ls.platform_uid=u.teamspeak_id)
        GROUP BY u.id, u.name, u.level
        ORDER BY val DESC LIMIT 3
    """)

    most_logins = query_top3("""
        SELECT u.id, COALESCE(u.name,'Unknown'), u.level, SUM(ls.logins) as val
        FROM user u
        INNER JOIN login_streak ls ON
            (ls.platform='discord' AND ls.platform_uid=u.discord_id) OR
            (ls.platform='teamspeak' AND ls.platform_uid=u.teamspeak_id)
        GROUP BY u.id, u.name, u.level
        ORDER BY val DESC LIMIT 3
    """)

    most_achievements = query_top3("""
        SELECT u.id, COALESCE(u.name,'Unknown'), u.level, COUNT(DISTINCT sa.achievement_type) as val
        FROM user u
        INNER JOIN special_achievements sa ON
            (sa.platform='discord' AND sa.platform_id=u.discord_id) OR
            (sa.platform='teamspeak' AND sa.platform_id=u.teamspeak_id)
        GROUP BY u.id, u.name, u.level
        ORDER BY val DESC LIMIT 3
    """)

    most_active_times = query_top3("""
        SELECT u.id, COALESCE(u.name,'Unknown'), u.level,
            COUNT(DISTINCT CONCAT(ah.day_of_week,'_',ah.time_category)) as val
        FROM user u
        INNER JOIN activity_heatmap ah ON
            (ah.platform='discord' AND ah.platform_uid=u.discord_id) OR
            (ah.platform='teamspeak' AND ah.platform_uid=u.teamspeak_id)
        WHERE ah.activity_minutes > 0
        GROUP BY u.id, u.name, u.level
        ORDER BY val DESC LIMIT 3
    """)

    oldest_member = query_top3("""
        SELECT u.id, COALESCE(u.name,'Unknown'), u.level,
            DATEDIFF(CURDATE(), DATE(u.created_at)) as val
        FROM user u
        WHERE u.created_at IS NOT NULL
        ORDER BY u.created_at ASC LIMIT 3
    """)

    current_streak = query_top3("""
        SELECT u.id, COALESCE(u.name,'Unknown'), u.level, MAX(ls.current_streak) as val
        FROM user u
        INNER JOIN login_streak ls ON
            (ls.platform='discord' AND ls.platform_uid=u.discord_id) OR
            (ls.platform='teamspeak' AND ls.platform_uid=u.teamspeak_id)
        WHERE ls.last_login >= CURDATE() - INTERVAL 1 DAY
        GROUP BY u.id, u.name, u.level
        ORDER BY val DESC LIMIT 3
    """)

    db.close()

    return jsonify({
        'longest_streak': longest_streak,
        'most_logins': most_logins,
        'most_achievements': most_achievements,
        'most_active_times': most_active_times,
        'oldest_member': oldest_member,
        'current_streak': current_streak
    })