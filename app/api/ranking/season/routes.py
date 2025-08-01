from flask import Blueprint, jsonify, request
from app.utils.database import DatabaseManager
from app.utils.valkey_manager import ValkeyManager
from app.utils.security import limiter, handle_errors
from datetime import datetime

valkey_manager = ValkeyManager()
ranking_season_bp = Blueprint('ranking_season', __name__)

@ranking_season_bp.route('/api/ranking/season', methods=['GET'])
@limiter.limit("10 per minute")
@handle_errors
def get_ranking():
    page = int(request.args.get('page', 1))
    search = request.args.get('search', '')
    limit = min(int(request.args.get('limit', 10)), 50)
    
    offset = (page - 1) * limit
    
    db = DatabaseManager()
    

    discord_users = valkey_manager.get_online_users('discord')
    teamspeak_users = valkey_manager.get_online_users('teamspeak')
    online_users = set(discord_users) | set(teamspeak_users)
    
    count_query = """
        SELECT COUNT(*) 
        FROM user
        LEFT JOIN time AS discord_time 
            ON user.discord_id = discord_time.platform_uid 
            AND discord_time.platform = 'discord'
        LEFT JOIN time AS teamspeak_time 
            ON user.teamspeak_id = teamspeak_time.platform_uid 
            AND teamspeak_time.platform = 'teamspeak'
        WHERE (COALESCE(discord_time.season_time, 0) + COALESCE(teamspeak_time.season_time, 0)) > 0
    """

    query = """
        SELECT 
            user.id,
            COALESCE(user.name, 'Unknown') AS name,
            COALESCE(user.level, 1) AS level,
            COALESCE(user.division, 1) AS division,
            (COALESCE(discord_time.season_time, 0) + COALESCE(teamspeak_time.season_time, 0)) AS minutes,
            GREATEST(
                COALESCE(discord_time.last_update, '1970-01-01'), 
                COALESCE(teamspeak_time.last_update, '1970-01-01')
            ) AS last_update,
            RANK() OVER (ORDER BY (COALESCE(discord_time.season_time, 0) + COALESCE(teamspeak_time.season_time, 0)) DESC) AS rank,
            user.discord_id AS discord_uid,
            user.teamspeak_id AS teamspeak_uid
        FROM user
        LEFT JOIN time AS discord_time 
            ON user.discord_id = discord_time.platform_uid 
            AND discord_time.platform = 'discord'
        LEFT JOIN time AS teamspeak_time 
            ON user.teamspeak_id = teamspeak_time.platform_uid 
            AND teamspeak_time.platform = 'teamspeak'
        WHERE (COALESCE(discord_time.season_time, 0) + COALESCE(teamspeak_time.season_time, 0)) > 0
        """
    
    params = []
    if search:
        count_query += " AND name LIKE ?"
        query += " AND name LIKE ?"
        params.append(f"%{search}%")
    query += " ORDER BY minutes DESC LIMIT ? OFFSET ?"
    params.extend([limit, offset])

    total_count = db.execute_query(count_query, params[:-2] if search else None)[0][0]
    result = db.execute_query(query, params)
    current_time = datetime.now()
    db.close()

    players = []
    for row in result:
        if row[7] and int(row[7]) in online_users:
            last_online = "Online"
        elif row[8] in online_users:
            last_online = "Online"
        else:
            date_string = row[5]
            date_object = datetime.strptime(date_string, '%Y-%m-%d %H:%M:%S')
            time_diff = current_time - date_object

            if time_diff.days > 0:
                last_online = f"vor {time_diff.days} Tagen"
                if time_diff.days == 1:
                    last_online = "Vor einem Tag"
            else:
                hours = time_diff.seconds // 3600
                if hours > 0:
                    last_online = f"vor {hours} Studen"
                    if hours == 1:
                        last_online = "Vor einer Stunde"
                else:
                    minutes = (time_diff.seconds % 3600) // 60
                    last_online = f"vor {minutes} Minuten"
                    if minutes == 1:
                        last_online = "Vor einer Minute"
        
        players.append({
            'id': row[0],
            'name': row[1],
            'level': row[2],
            'division': row[3],
            'minutes': row[4],
            'last_online': last_online,
            'rank': row[6]
        })
    
    return jsonify({
        'players': players,
        'total': total_count,
        'page': page,
        'pages': (total_count + limit - 1) // limit,
        'limit': limit
    })
