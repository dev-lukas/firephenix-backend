from flask import Blueprint, jsonify, session
from app.config import Config
from app.utils.database import DatabaseManager
from app.utils.security import login_required, handle_errors
from app.utils.security import limiter

user_bp = Blueprint('/api/user', __name__)

@user_bp.route('/api/user', methods=['GET'])
@login_required
@handle_errors
@limiter.limit("10 per minute")
def get_connected_users():
    steam_id = session.get('steam_id')

    if not steam_id:
        return jsonify({'error': 'No steam ID in session'}), 401
    
    db = DatabaseManager()

    query = """
        SELECT 
            u.id,
            u.name, 
            u.discord_id, 
            u.teamspeak_id, 
            u.level,
            u.division, 
            u.discord_channel, 
            u.teamspeak_channel,
            u.discord_moveable,
            u.teamspeak_moveable,
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.total_time ELSE 0 END) + 
                     SUM(CASE WHEN t.platform = 'teamspeak' THEN t.total_time ELSE 0 END), 0) as total_time,
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.daily_time ELSE 0 END) + 
                     SUM(CASE WHEN t.platform = 'teamspeak' THEN t.daily_time ELSE 0 END), 0) as daily_time,
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.weekly_time ELSE 0 END) + 
                     SUM(CASE WHEN t.platform = 'teamspeak' THEN t.weekly_time ELSE 0 END), 0) as weekly_time,
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.monthly_time ELSE 0 END) + 
                     SUM(CASE WHEN t.platform = 'teamspeak' THEN t.monthly_time ELSE 0 END), 0) as monthly_time,
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.season_time ELSE 0 END) + 
                     SUM(CASE WHEN t.platform = 'teamspeak' THEN t.season_time ELSE 0 END), 0) as season_time
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
            'discord_moveable': 0,
            'teamspeak_moveable': 0,
            'total_time': 0,
            'daily_time': 0,
            'weekly_time': 0,
            'monthly_time': 0,
            'season_time': 0,
            'apex_division': 0,
            'apex_rank': 0,
            'discord_upgraded': 0,
            'teamspeak_upgraded': 0,
            'time_to_next_level': 0,
            'time_to_next_division': 0,
            'best_division_achieved': 0,
            'season_one_skins_unlocked': {2: False, 3: False, 4: False, 5: False, 6: False},
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
        discord_id = str(user_data[2]) if user_data[2] else None
        teamspeak_id = str(user_data[3]) if user_data[3] else None

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

        time_to_next_level = 0
        time_to_next_division = 0
        if user_data[4] < 25:
            next_level_req = Config.get_level_requirement(user_data[4] + 1)
            time_to_next_level = max(0, next_level_req - user_data[10])
        
        if user_data[5] < 5:
            next_division_req = Config.get_division_requirement(user_data[5] + 1)
            time_to_next_division = max(0, next_division_req - int(user_data[14]))
        elif user_data[5] == 5:
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
                time_to_next_division = max(0, lowest_div6_time - int(user_data[14]) + 1) 
            else:
                next_division_req = Config.get_division_requirement(5)
                time_to_next_division = max(0, next_division_req - int(user_data[14]))

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
        apex_division = False
        apex_rank = False
        discord_upgraded = False
        teamspeak_upgraded = False

        season_one_skins = {2: False, 3: False, 4: False, 5: False, 6: False}

        if special_achievements_data:
            for achievement in special_achievements_data:
                achievement_type = achievement[0]
                if 101 <= achievement_type <= 106:
                    best_division_achieved = max(best_division_achieved, achievement_type - 100)
                elif achievement_type == 200:
                    apex_division = True
                elif achievement_type == 300:
                    apex_rank = True

        unlockable_query = """SELECT platform, unlockable_type
        FROM unlockables
        WHERE steam_id = ?
        """
        unlockable_data = db.execute_query(unlockable_query, (steam_id,))
        if unlockable_data:
            for unlockable in unlockable_data:
                platform = unlockable[0]
                unlockable_type = unlockable[1]
                if platform == 'discord' and unlockable_type == 1:
                    discord_upgraded = True
                elif platform == 'teamspeak' and unlockable_type == 1:
                    teamspeak_upgraded = True
                if platform == 'gameserver':
                    if 12 <= unlockable_type <= 16:
                        season_one_skins[unlockable_type - 10] = True

        response = jsonify({
            'id': user_data[0],
            'name': user_data[1],
            'discord_id': discord_id,
            'teamspeak_id': teamspeak_id,
            'level': user_data[4],
            'division': user_data[5],
            'discord_channel': user_data[6],
            'teamspeak_channel': user_data[7],
            'discord_moveable': bool(user_data[8]),
            'teamspeak_moveable': bool(user_data[9]),
            'total_time': int(user_data[10]),
            'daily_time': int(user_data[11]),
            'weekly_time': int(user_data[12]),
            'monthly_time': int(user_data[13]),
            'season_time': int(user_data[14]),
            'apex_division': apex_division,
            'apex_rank': apex_rank,
            'discord_upgraded': discord_upgraded,
            'teamspeak_upgraded': teamspeak_upgraded,
            'time_to_next_level': int(time_to_next_level),
            'time_to_next_division': int(time_to_next_division),
            'best_division_achieved': best_division_achieved,
            'season_one_skins_unlocked': season_one_skins,
            'activity_heatmap': {
                'data': heatmap
            },
            'login_streaks': streaks
        })

    db.close()
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"

    return response
