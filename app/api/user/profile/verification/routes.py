from flask import Blueprint, jsonify, request, session
from app.utils.database import DatabaseManager
from app.utils.security import limiter, login_required, generate_verification_code, handle_errors
from app.utils.valkey_manager import ValkeyManager

user_profile_verification_bp = Blueprint('/api/user/profile/verification/', __name__)

valkey_manager = ValkeyManager()

@user_profile_verification_bp.route('/api/user/profile/verification/initiate', methods=['POST'])
@login_required
@handle_errors
@limiter.limit("3 per 10 minutes")
def initiate_verification():
    platform = request.json.get('platform')
    platform_id = request.json.get('platform_id')
    steam_id = session.get('steam_id')
    
    if not all([platform, platform_id, steam_id]):
        return jsonify({'error': 'Missing required fields'}), 400
        
    if platform not in ['discord', 'teamspeak']:
        return jsonify({'error': 'Invalid platform'}), 400
    
    online_users = valkey_manager.get_online_users(platform)
    if platform == 'discord':
        if int(platform_id) not in online_users:
            return jsonify({'error': 'User not connected'}), 400
    else:
        if platform_id not in online_users:
            return jsonify({'error': 'User not connected'}), 400
    
    db = DatabaseManager()
    existing = db.execute_query(
        f"SELECT steam_id FROM user WHERE {platform}_id = ? AND steam_id IS NOT NULL",
        (platform_id,)
    )

    if existing:
        return jsonify({
            'error': 'This account is already linked to another Steam profile'
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

    valkey_manager.publish_command(platform, 'send_verification', platform_id=platform_id, code=verification_code)

    return jsonify({'message': 'Verification code sent'})

@user_profile_verification_bp.route('/api/user/profile/verification/verify', methods=['POST'])
@login_required
@handle_errors
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
    
    try:
        db.execute_query("START TRANSACTION")
        
        existing_users = db.execute_query("""
            SELECT id, steam_id, discord_id, teamspeak_id, name, level, division,
                   discord_channel, teamspeak_channel, discord_moveable, teamspeak_moveable
            FROM user
            WHERE steam_id = ?
        """, (steam_id,))
        
        if not existing_users:
            db.execute_query(f"""
                UPDATE user 
                SET steam_id = ?
                WHERE {platform}_id = ?
            """, (steam_id, platform_id))
        
        else:        
            primary_user = min(existing_users, key=lambda x: x[0])
            primary_id = primary_user[0]
            
            user_to_merge = db.execute_query(f"""
                SELECT level, division, discord_channel, teamspeak_channel, discord_moveable, teamspeak_moveable
                FROM user
                WHERE {platform}_id = ?
            """, (platform_id,))
            
            if user_to_merge:
                merge_level = user_to_merge[0][0] or 1
                merge_division = user_to_merge[0][1] or 1
                merge_discord_channel = user_to_merge[0][2]
                merge_teamspeak_channel = user_to_merge[0][3]
                merge_discord_moveable = user_to_merge[0][4]
                merge_teamspeak_moveable = user_to_merge[0][5]
                
                primary_level = primary_user[5] or 1  
                primary_division = primary_user[6] or 1
                primary_discord_channel = primary_user[7]
                primary_teamspeak_channel = primary_user[8]
                primary_discord_moveable = primary_user[9]
                primary_teamspeak_moveable = primary_user[10]
                
                max_level = max(primary_level, merge_level)
                max_division = max(primary_division, merge_division)
                
                final_discord_channel = merge_discord_channel if merge_discord_channel else primary_discord_channel
                final_teamspeak_channel = merge_teamspeak_channel if merge_teamspeak_channel else primary_teamspeak_channel
                final_discord_moveable = merge_discord_moveable if merge_discord_moveable is True else primary_discord_moveable
                final_teamspeak_moveable = merge_teamspeak_moveable if merge_teamspeak_moveable is True else primary_teamspeak_moveable
            else:
                max_level = primary_user[5] or 1
                max_division = primary_user[6] or 1
                final_discord_channel = primary_user[7]
                final_teamspeak_channel = primary_user[8]
                final_discord_moveable = primary_user[9]
                final_teamspeak_moveable = primary_user[10]
            
            db.execute_query(f"""
                DELETE FROM user
                WHERE id != ? AND {platform}_id = ?
            """, (primary_id, platform_id))
            
            db.execute_query(f"""
                UPDATE user
                SET steam_id = ?,
                    {platform}_id = ?,
                    level = ?,
                    division = ?,
                    discord_channel = ?,
                    teamspeak_channel = ?,
                    discord_moveable = ?,
                    teamspeak_moveable = ?
                WHERE id = ?
            """, (steam_id, platform_id, max_level, max_division, 
                  final_discord_channel, final_teamspeak_channel, 
                  final_discord_moveable, final_teamspeak_moveable, primary_id))

        db.execute_query("COMMIT")
        db.close()

        valkey_manager.publish_command(platform, 'check_ranks', platform_id=platform_id)
        
    except Exception as e:
        db.execute_query("ROLLBACK")
        db.close()
        raise RuntimeError(f"Merge failed: {str(e)}")
    
    return jsonify({'message': 'Verification successful'})
