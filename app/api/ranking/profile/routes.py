from flask import Blueprint, jsonify, request
from app.config import Config
from app.utils.database import DatabaseManager
from app.utils.security import limiter, handle_errors

ranking_profile_bp = Blueprint('ranking_profile', __name__)

@ranking_profile_bp.route('/api/ranking/profile', methods=['GET'])
@handle_errors
@limiter.limit("10 per minute")
def get_ranking():
    user_id = int(request.args.get('id', 1))

    if not user_id:
        return jsonify({'error': 'Invalid user ID'}), 400
    
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
    ),
    ranked_users AS (
        SELECT 
            u.id,
            RANK() OVER (ORDER BY (COALESCE(d.total_time, 0) + COALESCE(t.total_time, 0)) DESC) as rank,
            COALESCE(u.name, 'Unknown') as name,
            COALESCE(u.level, 1) as level,
            COALESCE(u.division, 1) as division,
            COALESCE(d.total_time, 0) + COALESCE(t.total_time, 0) as total_time,
            COALESCE(d.monthly_time, 0) + COALESCE(t.monthly_time, 0) as monthly_time,
            COALESCE(d.weekly_time, 0) + COALESCE(t.weekly_time, 0) as weekly_time,
            COALESCE(d.season_time, 0) + COALESCE(t.season_time, 0) as season_time,
            u.discord_id,
            u.teamspeak_id
        FROM user u
        LEFT JOIN time d ON d.platform = 'discord' AND d.platform_uid = u.discord_id
        LEFT JOIN time t ON t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id
    )
    SELECT 
        r.*,
        (SELECT total_users FROM user_stats) as total_users,
        (SELECT mean_time FROM user_stats) as mean_time,
        (SELECT best_time FROM user_stats) as best_time
    FROM ranked_users r
    WHERE r.id = ?
    """

    streak_query = """
    SELECT 
        platform,
        current_streak,
        longest_streak
    FROM login_streak
    WHERE (platform = 'discord' AND platform_uid = ?)
        OR (platform = 'teamspeak' AND platform_uid = ?)
    """

    db.cursor.execute(query, (user_id,))
    user_data = db.cursor.fetchone()

    if not user_data:
        db.close()
        return jsonify({'error': 'User not found'}), 404

    (id, rank, name, level, division, total_time, monthly_time, weekly_time, season_time,
        discord_id, teamspeak_id, total_users, mean_time, best_time) = user_data

    db.cursor.execute(streak_query, (discord_id, teamspeak_id))
    streak_data = db.cursor.fetchall()
    
    streaks = {
        'discord': {'current': 0, 'longest': 0},
        'teamspeak': {'current': 0, 'longest': 0}
    }
    
    for platform, current, longest in streak_data:
        streaks[platform] = {
            'current': current,
            'longest': longest
        }

    heatmap_query = """
    SELECT 
        h.day_of_week,
        h.time_category,
        SUM(h.activity_minutes) as total_minutes
    FROM user u
    LEFT JOIN (
        SELECT 
            day_of_week,
            time_category,
            platform_uid,
            platform,
            activity_minutes
        FROM activity_heatmap
        WHERE platform IN ('discord', 'teamspeak')
    ) h ON (h.platform = 'discord' AND h.platform_uid = u.discord_id)
            OR (h.platform = 'teamspeak' AND h.platform_uid = u.teamspeak_id)
    WHERE u.id = ?
        AND (u.discord_id IS NOT NULL OR u.teamspeak_id IS NOT NULL)
    GROUP BY h.day_of_week, h.time_category
    ORDER BY h.day_of_week, 
        CASE h.time_category 
            WHEN 'morning' THEN 1 
            WHEN 'noon' THEN 2 
            WHEN 'evening' THEN 3 
            WHEN 'night' THEN 4 
        END
    """
    
    db.cursor.execute(heatmap_query, (user_id,))
    heatmap_data = db.cursor.fetchall()
    
    heatmap = {
        day: {
            time_cat: 0 
            for time_cat in ['morning', 'noon', 'evening', 'night']
        } 
        for day in range(7)
    }
    
    for row in heatmap_data:
        if row[0] is not None and row[1] is not None:
            heatmap[row[0]][row[1]] = row[2]

    time_to_next_level = 0
    time_to_next_division = 0
    if level < 25:
        next_level_req = Config.get_level_requirement(level + 1)
        time_to_next_level = max(0, next_level_req - total_time)
    if division < 5:
        next_division_req = Config.get_division_requirement(division + 1)
        time_to_next_division = max(0, next_division_req - season_time)
    elif division == 5:
        div6_query = """
        SELECT COUNT(u.id), MIN(COALESCE(d.total_time, 0) + COALESCE(t.total_time, 0))
        FROM user u
        LEFT JOIN time d ON d.platform = 'discord' AND d.platform_uid = u.discord_id
        LEFT JOIN time t ON t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id
        WHERE u.division = 6
        """
        db.cursor.execute(div6_query)
        div6_count, lowest_div6_time = db.cursor.fetchone()

        if div6_count is None:
            div6_count = 0
            
        if div6_count >= Config.TOP_DIVISION_PLAYER_AMOUNT and lowest_div6_time is not None:
            time_to_next_division = max(0, lowest_div6_time - season_time + 1) 
        else:
            next_division_req = Config.get_division_requirement(5)
            time_to_next_division = max(0, next_division_req - season_time)
        
    rank_percentage = (rank / total_users) * 100 if total_users > 0 else 0

    special_achievements_query = """
        SELECT achievement_type
        FROM special_achievements
        WHERE (platform = 'discord' AND platform_id = ?)
           OR (platform = 'teamspeak' AND platform_id = ?)
    """
    
    special_achievements_params = []
    if discord_id:
        special_achievements_params.append(discord_id)
    else:
        special_achievements_params.append(None)

    if teamspeak_id:
        special_achievements_params.append(teamspeak_id)
    else:
        special_achievements_params.append(None) 

    special_achievements_data = []
    if discord_id or teamspeak_id:
         special_achievements_data = db.execute_query(special_achievements_query, tuple(special_achievements_params))

    best_division_achieved = 0

    if special_achievements_data:
        for achievement in special_achievements_data:
            achievement_type = achievement[0]
            if 101 <= achievement_type <= 106:
                best_division_achieved = max(best_division_achieved, achievement_type - 100)
    
    db.close()

    return jsonify({
        'id': id,
        'rank': rank,
        'name': name,
        'level': level,
        'division': division,
        'total_time': total_time,
        'monthly_time': monthly_time,
        'weekly_time': weekly_time,
        'season_time': season_time,
        'time_to_next_level': time_to_next_level,
        'time_to_next_division': time_to_next_division,
        'rank_percentage': rank_percentage,
        'mean_total_time': mean_time,
        'best_player_time': best_time,
        'best_division_achieved': best_division_achieved,
        'activity_heatmap': {
            'data': heatmap
        },
        'login_streaks': streaks
    })

