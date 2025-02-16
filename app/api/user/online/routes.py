from flask import Blueprint, jsonify, request
from app.utils.database import DatabaseManager
from app.utils.security import limiter
from app.bots.discordbot import DiscordBot
from app.bots.teamspeakbot import TeamspeakBot

user_online_bp = Blueprint('/api/user/online', __name__)

@user_online_bp.route('/api/user/online', methods=['GET'])
@limiter.limit("10 per minute")
def get_connected_users():
    platform = request.args.get('platform')
    if platform not in ['discord', 'teamspeak']:
        return jsonify({'error': 'Invalid platform'}), 400
        
    bot = DiscordBot() if platform == 'discord' else TeamspeakBot()
    online_users = bot.get_online_users()
    
    id_column = 'discord_id' if platform == 'discord' else 'teamspeak_id'
    placeholders = ','.join(['?'] * len(online_users))
    
    if not online_users:
        return jsonify({'users': []})
    
    query = f"""
        SELECT {id_column}, name 
        FROM user
        WHERE {id_column} IN ({placeholders})
    """
    
    db = DatabaseManager()
    results = db.execute_query(query, tuple(online_users))
    db.close()

    user_data = []
    for user_id, name in results:
        user_data.append({
            'id': str(user_id),
            'name': name
        })
    
    response = jsonify({
        'users': user_data
    })

    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Pragma"] = "no-cache"

    return response