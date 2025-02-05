from flask import Blueprint, jsonify, request, session
import asyncio
from app.utils.database import DatabaseManager
from app.utils.security import limiter, login_required, generate_verification_code
from app.bots.discordbot import DiscordBot
from app.bots.teamspeakbot import TeamspeakBot

profile_verification_bp = Blueprint('/api/profile/verification/', __name__)

@profile_verification_bp.route('/api/profile/verification/initiate', methods=['POST'])
@login_required
@limiter.limit("3 per 10 minutes")
def initiate_verification():
    platform = request.json.get('platform')
    platform_id = request.json.get('platform_id')
    steam_id = session.get('steam_id')
    
    if not all([platform, platform_id, steam_id]):
        return jsonify({'error': 'Missing required fields'}), 400
        
    if platform not in ['discord', 'teamspeak']:
        return jsonify({'error': 'Invalid platform'}), 400
    
    if platform == 'discord':
        bot = DiscordBot()
        if int(platform_id) not in bot.get_online_users():
            return jsonify({'error': 'User not connected'}), 400
    else:
        bot = TeamspeakBot()
        if platform_id not in bot.get_online_users():
            return jsonify({'error': 'User not connected'}), 400
    
    db = DatabaseManager()
    existing = db.execute_query(
        f"SELECT steam_id FROM user_time WHERE {platform}_uid = ? AND steam_id IS NOT NULL",
        (platform_id,)
    )

    if existing:
        return jsonify({
            'error': 'This  account is already linked to another Steam profile'
        }), 400

    verification_code = generate_verification_code()

    db.execute_query("""
        DELETE FROM verification
        WHERE steam_id = ? OR expires_at < NOW()
    """, (steam_id,))
    
    db.execute_query("""
        INSERT INTO verification 
        (steam_id, platform_id, platform, verification_code, expires_at)
        VALUES (?, ?, ?, ?, DATE_ADD(NOW(), INTERVAL 10 MINUTE))
    """, (steam_id, platform_id, platform, verification_code))
    
    db.close()

    if platform == 'discord':
        asyncio.run_coroutine_threadsafe(
            bot.send_verification(platform_id, verification_code), bot.bot.loop
        )
    else:
        bot.send_verification(platform_id, verification_code)

    return jsonify({'message': 'Verification code sent'})

@profile_verification_bp.route('/api/profile/verification/verify', methods=['POST'])
@login_required
@limiter.limit("3 per minute")
def verify_code():
    code = request.json.get('code')
    platform = request.json.get('platform')
    steam_id = session.get('steam_id')

    if not all([code, platform, steam_id]):
        return jsonify({'error': 'Missing parameter'}), 400
        
    if platform not in ['discord', 'teamspeak']:
        return jsonify({'error': 'Invalid platform'}), 400
    
    db = DatabaseManager()
    result = db.execute_query("""
        SELECT platform_id FROM verification
        WHERE steam_id = ? AND platform = ? AND verification_code = ?
        AND expires_at > NOW()
        LIMIT 1
    """, (steam_id, platform, code))
    
    if not result:
        return jsonify({'error': 'Invalid or expired code'}), 400
        
    platform_id = result[0][0]

    db.execute_query("""
        DELETE FROM verification
        WHERE steam_id = ?
    """, (steam_id,))
    
    # Link accounts
    try:
        db.execute_query("START TRANSACTION")
        duplicate_data = db.execute_query(f"""
            SELECT 
                discord_uid,
                teamspeak_uid,
                total_time,
                daily_time,
                weekly_time,
                monthly_time
            FROM user_time
            WHERE (steam_id = ? OR {platform}_uid = ?)
            AND id != (
                SELECT MIN(id) 
                FROM user_time 
                WHERE steam_id = ? OR {platform}_uid = ?
            )
        """, (steam_id, platform_id, steam_id, platform_id))
        db.execute_query(f"""
            DELETE FROM user_time
            WHERE (steam_id = ? OR {platform}_uid = ?)
            AND id != (
                SELECT MIN(id) 
                FROM user_time 
                WHERE steam_id = ? OR {platform}_uid = ?
            )
        """, (steam_id, platform_id, steam_id, platform_id))

        if duplicate_data:
            (discord_uid, teamspeak_uid, 
             total_t, daily_t, weekly_t, monthly_t) = duplicate_data[0]

            db.execute_query(f"""
                UPDATE user_time
                SET
                    {platform}_uid = ?,
                    steam_id = ?,
                    discord_uid = COALESCE(discord_uid, ?),
                    teamspeak_uid = COALESCE(teamspeak_uid, ?),
                    total_time = total_time + ?,
                    daily_time = daily_time + ?,
                    weekly_time = weekly_time + ?,
                    monthly_time = monthly_time + ?
                WHERE id = (
                    SELECT MIN(id) 
                    FROM user_time 
                    WHERE steam_id = ? OR {platform}_uid = ?
                )
            """, (platform_id, steam_id,
                  discord_uid, teamspeak_uid,
                  total_t, daily_t, weekly_t, monthly_t,
                  steam_id, platform_id))
        else:
            db.execute_query(f"""
                UPDATE user_time
                SET steam_id = ? 
                WHERE {platform}_uid = ?
            """, (steam_id, platform_id))

        db.execute_query("COMMIT")
        db.close()
    except Exception as e:
        db.execute_query("ROLLBACK")
        db.close()
        raise RuntimeError(f"Merge failed: {str(e)}")
    
    return jsonify({'message': 'Verification successful'})
