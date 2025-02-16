from flask import Blueprint, jsonify, request, session
import asyncio
from app.utils.database import DatabaseManager
from app.utils.security import limiter, login_required
from app.bots.discordbot import DiscordBot
from app.bots.teamspeakbot import TeamspeakBot

profile_channel_bp = Blueprint('/api/profile/channel', __name__)

@profile_channel_bp.route('/api/profile/channel', methods=['POST'])
@login_required
@limiter.limit("3 per 10 minutes")
def create_channel():
    platform = request.json.get('platform')
    steam_id = session.get('steam_id')
    
    if not all([platform, steam_id]):
        return jsonify({'error': 'Missing required fields'}), 400
        
    if platform not in ['discord', 'teamspeak']:
        return jsonify({'error': 'Invalid platform'}), 400
    
    db = DatabaseManager()
    query = f"""SELECT level, name, discord_id, teamspeak_id, {platform}_channel
    FROM user
    WHERE steam_id = ? AND steam_id IS NOT NULL
    """

    db.cursor.execute(query, (steam_id,))
    user_data = db.cursor.fetchone()
    if not user_data:
        return jsonify({'error': 'This account has not yet linked his steamid with an account'}), 404

    # Extract user data
    level, name, discord_id, teamspeak_id, platform_channel = user_data
    
    if platform_channel:
        return jsonify({'error': 'This account already has a channel on this plattform'}), 400
    if level < 20:
        return jsonify({
            'error': 'This account has not reached level 20'
        }), 400
    if platform == 'discord' and discord_id is None or platform == 'teamspeak' and teamspeak_id is None:
        return jsonify({
            'error': 'This account has not yet linked the needed account'
        }), 400
    
    if platform == 'discord':
        bot = DiscordBot()
        future = asyncio.run_coroutine_threadsafe(
            bot.create_owned_channel(discord_id, f"{name}'s Channel"), bot.bot.loop
        )
        try:
            channel_id = future.result(timeout=300)
        except TimeoutError:
            return jsonify({'error': 'Error creating channel'}), 500
    else:
        bot = TeamspeakBot()
        channel_id = bot.create_owned_channel(teamspeak_id, f"{name}'s Channel")

    if not channel_id:
        return jsonify({'error': 'Error creating channel'}), 500

    db.execute_query(f"""
        UPDATE user
        SET {platform}_channel = ?
        WHERE steam_id = ?
    """, (channel_id, steam_id,))

    db.close()

    return jsonify({'message': 'Channel successfully created'}), 200
