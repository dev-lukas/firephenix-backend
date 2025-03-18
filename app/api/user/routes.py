from flask import Blueprint, jsonify, session
from app.config import Config
from app.utils.database import DatabaseManager
from app.utils.security import login_required
from app.utils.security import limiter

user_bp = Blueprint('/api/user', __name__)

@user_bp.route('/api/user', methods=['GET'])
@login_required
@limiter.limit("10 per minute")
def get_connected_users():
    steam_id = session.get('steam_id')

    if not steam_id:
        return jsonify({'error': 'No steam ID in session'}), 401
    
    db = DatabaseManager()

    query = """
        SELECT 
            u.name, 
            u.discord_id, 
            u.teamspeak_id, 
            u.level,
            u.division, 
            u.discord_channel, 
            u.teamspeak_channel,
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.total_time ELSE 0 END) + 
                     SUM(CASE WHEN t.platform = 'teamspeak' THEN t.total_time ELSE 0 END), 0) as total_time,
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.daily_time ELSE 0 END) + 
                     SUM(CASE WHEN t.platform = 'teamspeak' THEN t.daily_time ELSE 0 END), 0) as daily_time,
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.weekly_time ELSE 0 END) + 
                     SUM(CASE WHEN t.platform = 'teamspeak' THEN t.weekly_time ELSE 0 END), 0) as weekly_time,
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.monthly_time ELSE 0 END) + 
                     SUM(CASE WHEN t.platform = 'teamspeak' THEN t.monthly_time ELSE 0 END), 0) as monthly_time
        FROM user u
        LEFT JOIN time t ON 
            (t.platform = 'discord' AND t.platform_uid = u.discord_id) OR
            (t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id)
        WHERE u.steam_id = ?
        GROUP BY u.name, u.discord_id, u.teamspeak_id, u.level,
                 u.division, u.discord_channel, u.teamspeak_channel
    """

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
        WHERE u.steam_id = ?
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

    results = db.execute_query(query, (steam_id,))

    if not results:
        response = jsonify({
            'name': None,
            'discord_id': None,
            'teamspeak_id': None,
            'level': 0,
            'division': None,
            'discord_channel': None,
            'teamspeak_channel': None,
            'total_time': 0,
            'daily_time': 0,
            'weekly_time': 0,
            'monthly_time': 0,
            'activity_heatmap': {
                'data': {
                    day: {
                        time_cat: 0 
                        for time_cat in ['morning', 'noon', 'evening', 'night']
                    } 
                    for day in range(7)
                }
            },
            'login_streaks': {
                'discord': {'current': 0, 'longest': 0},
                'teamspeak': {'current': 0, 'longest': 0}
            }
        })
    else:
        user_data = results[0]
        discord_id = str(user_data[1]) if user_data[1] else None
        teamspeak_id = str(user_data[2]) if user_data[2] else None

        # Get heatmap data
        heatmap_data = db.execute_query(heatmap_query, (steam_id,))
        heatmap = {
            day: {
                time_cat: 0 
                for time_cat in ['morning', 'noon', 'evening', 'night']
            } 
            for day in range(7)
        }
        
        for heatmap_row in heatmap_data:
            if heatmap_row[0] is not None and heatmap_row[1] is not None:
                heatmap[heatmap_row[0]][heatmap_row[1]] = int(heatmap_row[2])

        streak_query = """
        SELECT 
            platform,
            current_streak,
            longest_streak
        FROM login_streak
        WHERE (platform = 'discord' AND platform_uid = ?)
           OR (platform = 'teamspeak' AND platform_uid = ?)
        """
        
        streak_data = db.execute_query(streak_query, (discord_id, teamspeak_id))
        streaks = {
            'discord': {'current': 0, 'longest': 0},
            'teamspeak': {'current': 0, 'longest': 0}
        }
        
        if streak_data:
            for platform, current, longest in streak_data:
                streaks[platform] = {
                    'current': current,
                    'longest': longest
                }

        time_to_next = 0
        if user_data[3] < 25:
            next_level_req = Config.get_level_requirement(user_data[3] + 1)
            time_to_next = max(0, next_level_req - user_data[7])

        response = jsonify({
            'name': user_data[0],
            'discord_id': discord_id,
            'teamspeak_id': teamspeak_id,
            'level': user_data[3],
            'division': user_data[4],
            'discord_channel': user_data[5],
            'teamspeak_channel': user_data[6],
            'total_time': int(user_data[7]),
            'daily_time': int(user_data[8]),
            'weekly_time': int(user_data[9]),
            'monthly_time': int(user_data[10]),
            'time_to_next_level': int(time_to_next),
            'activity_heatmap': {
                'data': heatmap
            },
            'login_streaks': streaks
        })

    db.close()
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"

    return response
