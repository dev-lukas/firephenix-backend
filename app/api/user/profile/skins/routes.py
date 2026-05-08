from flask import Blueprint, jsonify, request, session
from app.utils.database import (
    DatabaseManager,
    can_claim_season_skin,
    get_best_division_from_season_achievements,
)
from app.config import Config
from app.utils.security import csrf_required, limiter, login_required, handle_errors
from app.utils.steam import steamid64_to_steam2
from app.utils.valkey_manager import ValkeyManager

user_profile_skins_bp = Blueprint('/api/user/profile/skins', __name__)

valkey_manager = ValkeyManager()


def ttt_error_response(payload: dict, status_code: int):
    error = payload.get('error')
    messages = {
        'manager_unavailable': 'TTT server unavailable, try again later',
        'manager_timeout': 'TTT server timed out while granting the reward, try again later',
        'invalid_manager_response': 'TTT server returned an invalid response',
        'invalid_reward_item': 'Reward item is not configured on the TTT server',
        'invalid_reward_request': 'TTT server rejected the reward request',
        'player_offline': 'You must be online on the TTT server to claim this reward',
        'pointshop_inventory_unavailable': 'TTT Pointshop inventory is still loading, try again in a moment',
        'pointshop_unavailable': 'TTT Pointshop is not ready, try again later',
        'grant_failed': 'TTT server could not grant the reward',
        'rcon_unavailable': 'TTT server unavailable, try again later',
        'rcon_auth_failed': 'TTT server unavailable, try again later',
        'lua_timeout': 'TTT server timed out while granting the reward, try again later',
    }
    return jsonify({'error': messages.get(error, payload.get('message') or 'Error gifting skin')}), status_code

@user_profile_skins_bp.route('/api/user/profile/skins', methods=['POST'])
@login_required
@csrf_required
@handle_errors
@limiter.limit("1 per minute")
def set_skin():
    payload = request.get_json(silent=True) or {}
    platform = payload.get('platform')
    tier = payload.get('tier')
    steam_id = session.get('steam_id')
    
    if not all([platform, steam_id, tier]):
        return jsonify({'error': 'Missing required fields'}), 400
        
    if platform not in ['garrysmod']:
        return jsonify({'error': 'Invalid platform'}), 400
    
    if tier not in [2, 3, 4, 5, 6]:
        return jsonify({'error': 'Invalid tier'}), 400
    
    item_uuid = Config.TTT_SEASON_REWARD_ITEM_UUIDS.get(tier)
    if not item_uuid:
        return jsonify({'error': 'Reward item is not configured'}), 500

    try:
        steam_id2 = steamid64_to_steam2(steam_id)
    except (TypeError, ValueError):
        return jsonify({'error': 'Invalid Steam account'}), 400

    db = DatabaseManager()

    try:
        query = """SELECT teamspeak_id, discord_id
        FROM user
        WHERE steam_id = ? AND steam_id IS NOT NULL
        """

        db.cursor.execute(query, (steam_id,))
        user_data = db.cursor.fetchone()
        if not user_data:
            return jsonify({'error': 'This account has not yet linked his steamid with an account'}), 404

        teamspeak_id, discord_id = user_data

        if not teamspeak_id and not discord_id:
            return jsonify({'error': 'This account has not yet linked his steamid with an account'}), 404

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

        best_division_achieved = get_best_division_from_season_achievements(
            [achievement[0] for achievement in special_achievements_data],
        )

        if not can_claim_season_skin(best_division_achieved, tier):
            return jsonify({
                'error': 'This account has not reached the needed tier'
            }), 400

        unlock_query = """
            SELECT unlocked_at
            FROM unlockables
            WHERE steam_id = ?
            AND platform = 'gameserver'
            AND unlockable_type = ?
        """

        db.cursor.execute(unlock_query, (steam_id, tier + 10))
        unlocked = db.cursor.fetchone()

        if unlocked:
            return jsonify({'error': 'This skin has already been unlocked'}), 400

        grant_payload, grant_status = valkey_manager.gameserver_command('ttt', 'grant_season_skin', {
            'steam_id64': str(steam_id),
            'steam_id2': steam_id2,
            'tier': tier,
            'item_uuid': item_uuid,
            'reward_key': f'season_1_tier_{tier}',
        }, timeout_seconds=60)
        if not grant_payload.get('ok'):
            return ttt_error_response(grant_payload, grant_status)

        db.execute_query("""
            INSERT INTO unlockables (steam_id, platform, unlockable_type)
            VALUES (?, 'gameserver', ?)
        """, (steam_id, tier + 10))
    finally:
        db.close()

    return jsonify({'message': 'Skin unlocked'})
