from flask import Blueprint, jsonify, request
from app.utils.database import DatabaseManager
from app.utils.security import handle_errors
from app.utils.security import limiter

user_ranking_profile_achievements_bp = Blueprint('/api/ranking/profile/achievements', __name__)

@user_ranking_profile_achievements_bp.route('/api/ranking/profile/achievements', methods=['GET'])
@handle_errors
@limiter.limit("10 per minute")
def get_achievements():
    user_id = int(request.args.get('id', 1))

    if not user_id:
        return jsonify({'error': 'Invalid user ID'}), 400
    
    db = DatabaseManager()

    query = """
        SELECT 
            u.discord_id, 
            u.teamspeak_id, 
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.total_time ELSE 0 END) + 
                     SUM(CASE WHEN t.platform = 'teamspeak' THEN t.total_time ELSE 0 END), 0) as total_time
        FROM user u
        LEFT JOIN time t ON 
            (t.platform = 'discord' AND t.platform_uid = u.discord_id) OR
            (t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id)
        WHERE u.id = ?
        GROUP BY u.discord_id, u.teamspeak_id
    """

    results = db.execute_query(query, (user_id,))

    if not results:
        return jsonify({'error': 'User has no linked account yet'}), 404

    user_data = results[0]
    discord_id = str(user_data[0]) if user_data[0] else None
    teamspeak_id = str(user_data[1]) if user_data[1] else None
    total_time = user_data[2]

    streak_query = """
    SELECT 
        SUM(logins) as total_logins,
        MAX(longest_streak) as max_longest_streak
    FROM login_streak
    WHERE (platform = 'discord' AND platform_uid = ?)
        OR (platform = 'teamspeak' AND platform_uid = ?)
    """
    
    streak_data = db.execute_query(streak_query, (discord_id, teamspeak_id))

    if not streak_data or streak_data[0][0] is None:
        total_logins = 0
        longest_streak = 0
    else:
        total_logins = int(streak_data[0][0]) or 0
        longest_streak = int(streak_data[0][1]) or 0

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

    heatmap_data = db.execute_query(heatmap_query, (user_id,))

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

    old_member_achievement = 0
    legacy_supporter_achievement = 0
    division_achievement = 0
    apex_achievement = 0

    if special_achievements_data:
        for achievement in special_achievements_data:
            achievement_type = achievement[0]
            if achievement_type == 1:
                old_member_achievement = 1
            elif achievement_type == 2:
                legacy_supporter_achievement = 1
            elif 101 <= achievement_type <= 104:
                division_achievement = max(division_achievement, achievement_type - 100)
            elif achievement_type == 200:
                apex_achievement = 1

    streak_achievement = 0
    if longest_streak >= 30:
        streak_achievement = 3
    elif longest_streak >= 14:
        streak_achievement = 2
    elif longest_streak >= 7:
        streak_achievement = 2
    elif longest_streak >= 2:
        streak_achievement = 1

    login_achievement = 0
    if total_logins >= 3650:
        login_achievement = 4
    elif total_logins >= 365:
        login_achievement = 3
    elif total_logins >= 30:
        login_achievement = 2
    elif total_logins >= 2:
        login_achievement = 1
    
    time_achievement = 0
    total_time_hours = total_time / 60
    if total_time_hours >= 1000:
        time_achievement = 4
    elif total_time_hours >= 100:
        time_achievement = 3
    elif total_time_hours >= 10:
        time_achievement = 2
    elif total_time_hours >= 1:
        time_achievement = 1
    
    if not heatmap_data or heatmap_data[0][0] is None:
        heatmap_achievement = 0
    else:
        unique_days = set()
        time_slots_by_day = {0: set(), 1: set(), 2: set(), 3: set(), 4: set(), 5: set(), 6: set()}
        
        for entry in heatmap_data:
            day = entry[0]
            time_slot = entry[1]
            minutes = entry[2]
            
            if minutes > 0:
                unique_days.add(day)
                time_slots_by_day[day].add(time_slot)
        
        all_days_all_slots = True
        for day in range(7):
            if len(time_slots_by_day[day]) < 4:
                all_days_all_slots = False
                break
                
        days_count = len(unique_days)
        
        if all_days_all_slots:
            heatmap_achievement = 4
        elif days_count == 7:
            heatmap_achievement = 3
        elif days_count >= 5:
            heatmap_achievement = 2
        elif days_count >= 3:
            heatmap_achievement = 1
        else:
            heatmap_achievement = 0
    

    
    response = jsonify({
        'streak': {
            'longest_streak': longest_streak,
            'total_logins': total_logins,
            'achievement_level': streak_achievement
        },
        'logins': {
            'total_logins': total_logins,
            'achievement_level': login_achievement
        },
        'time': {
            'total_hours': int(total_time_hours),
            'achievement_level': time_achievement
        },
        'heatmap': {
            'active_days': len(unique_days) if 'unique_days' in locals() else 0,
            'achievement_level': heatmap_achievement
        },
        'old_member': {
            'achievement_level': old_member_achievement
        },
        'legacy_supporter': {
            'achievement_level': legacy_supporter_achievement
        },
        'division': {
            'achievement_level': division_achievement
        },
        'apex': {
            'achievement_level': apex_achievement
        }
    })

    db.close()
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"

    return response