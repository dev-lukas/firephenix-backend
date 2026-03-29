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

    # Achievements are computed from multiple sources, not a single table count.
    # We need to replicate the achievement calculation logic per user.
    ach_query = """
        SELECT
            u.id, COALESCE(u.name,'Unknown') as name, u.level,
            u.discord_id, u.teamspeak_id,
            COALESCE(SUM(CASE WHEN t.platform='discord' THEN t.total_time ELSE 0 END) +
                     SUM(CASE WHEN t.platform='teamspeak' THEN t.total_time ELSE 0 END), 0) as total_time,
            (SELECT MAX(ls2.longest_streak) FROM login_streak ls2
             WHERE (ls2.platform='discord' AND ls2.platform_uid=u.discord_id)
                OR (ls2.platform='teamspeak' AND ls2.platform_uid=u.teamspeak_id)) as longest_streak,
            (SELECT SUM(ls3.logins) FROM login_streak ls3
             WHERE (ls3.platform='discord' AND ls3.platform_uid=u.discord_id)
                OR (ls3.platform='teamspeak' AND ls3.platform_uid=u.teamspeak_id)) as total_logins,
            (SELECT COUNT(DISTINCT CONCAT(ah.day_of_week,'_',ah.time_category))
             FROM activity_heatmap ah
             WHERE ((ah.platform='discord' AND ah.platform_uid=u.discord_id)
                 OR (ah.platform='teamspeak' AND ah.platform_uid=u.teamspeak_id))
               AND ah.activity_minutes > 0) as active_slots,
            (SELECT COUNT(DISTINCT ah2.day_of_week)
             FROM activity_heatmap ah2
             WHERE ((ah2.platform='discord' AND ah2.platform_uid=u.discord_id)
                 OR (ah2.platform='teamspeak' AND ah2.platform_uid=u.teamspeak_id))
               AND ah2.activity_minutes > 0) as active_days
        FROM user u
        LEFT JOIN time t ON
            (t.platform='discord' AND t.platform_uid=u.discord_id) OR
            (t.platform='teamspeak' AND t.platform_uid=u.teamspeak_id)
        WHERE u.discord_id IS NOT NULL OR u.teamspeak_id IS NOT NULL
        GROUP BY u.id, u.name, u.level, u.discord_id, u.teamspeak_id
    """
    ach_rows = db.execute_query(ach_query) or []

    # Get all special achievements in one query
    sa_query = "SELECT platform, platform_id, achievement_type FROM special_achievements"
    sa_rows = db.execute_query(sa_query) or []
    # Build lookup: platform_id -> set of achievement_types
    sa_map = {}
    for platform, pid, atype in sa_rows:
        sa_map.setdefault(pid, set()).add(atype)

    def calc_achievement_count(row):
        uid, name, level, discord_id, ts_id, total_time, longest_streak, total_logins, active_slots, active_days = row
        longest_streak = longest_streak or 0
        total_logins = total_logins or 0
        active_slots = active_slots or 0
        active_days = active_days or 0
        total_hours = total_time / 60

        count = 0
        # Streak (4 levels)
        for threshold in [2, 7, 14, 30]:
            if longest_streak >= threshold: count += 1
        # Logins (4 levels)
        for threshold in [2, 30, 365, 3650]:
            if total_logins >= threshold: count += 1
        # Time (4 levels)
        for threshold in [1, 10, 100, 1000]:
            if total_hours >= threshold: count += 1
        # Heatmap (4 levels): 3 days, 5 days, 7 days, all 28 slots
        if active_days >= 3: count += 1
        if active_days >= 5: count += 1
        if active_days >= 7: count += 1
        if active_slots >= 28: count += 1
        # Special achievements from sa_map
        user_sa = set()
        if discord_id: user_sa |= sa_map.get(str(discord_id), set())
        if ts_id: user_sa |= sa_map.get(str(ts_id), set())
        # Division (count levels reached)
        for d in range(101, 107):
            if d in user_sa: count += 1
        if 1 in user_sa: count += 1   # old member
        if 2 in user_sa: count += 1   # legacy supporter
        if 200 in user_sa: count += 1 # apex
        return {'id': uid, 'name': name, 'level': level, 'value': count}

    scored = [calc_achievement_count(r) for r in ach_rows]
    scored.sort(key=lambda x: x['value'], reverse=True)
    most_achievements = scored[:3]

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