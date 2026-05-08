import json

from flask import Blueprint, jsonify, request, session

from app.config import Config
from app.utils.database import DatabaseManager
from app.utils.security import admin_required, csrf_required, handle_errors
from app.utils.steam import steamid64_to_steam2
from app.utils.valkey_manager import ValkeyManager


admin_bp = Blueprint("admin", __name__)
valkey_manager = ValkeyManager()

VALID_PLATFORMS = {"discord", "teamspeak"}
PLATFORM_ID_COLUMNS = {
    "discord": "discord_id",
    "teamspeak": "teamspeak_id",
}


def _admin_steam_id():
    return str(session.get("steam_id"))


def _fetch_user_for_update(db, user_id):
    db.cursor.execute("""
        SELECT
            id, steam_id, discord_id, teamspeak_id, name, level, division,
            COALESCE(ranking_disabled, 0)
        FROM user
        WHERE id = ?
        FOR UPDATE
    """, (user_id,))
    return db.cursor.fetchone()


def _user_dict(row):
    if not row:
        return None
    return {
        "id": row[0],
        "steam_id": str(row[1]) if row[1] is not None else None,
        "discord_id": str(row[2]) if row[2] is not None else None,
        "teamspeak_id": str(row[3]) if row[3] is not None else None,
        "name": row[4],
        "level": row[5],
        "division": row[6],
        "ranking_disabled": bool(row[7]),
    }


def _write_audit(db, action, target_identifiers, summary, result_status):
    db.cursor.execute("""
        INSERT INTO admin_audit_log
            (admin_steam_id, action, target_identifiers, summary, result_status)
        VALUES (?, ?, ?, ?, ?)
    """, (
        _admin_steam_id(),
        action,
        json.dumps(target_identifiers, sort_keys=True),
        json.dumps(summary, sort_keys=True),
        result_status,
    ))
    db.conn.commit()


def _admin_error(db, action, target_identifiers, summary, message, status_code=400):
    db.conn.rollback()
    try:
        _write_audit(db, action, target_identifiers, {**summary, "error": message}, "failed")
    except Exception:
        db.conn.rollback()
    return jsonify({"error": message}), status_code


def _recalculate_user_rank(db, user_id):
    db.cursor.execute("""
        SELECT
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.total_time ELSE 0 END), 0) +
            COALESCE(SUM(CASE WHEN t.platform = 'teamspeak' THEN t.total_time ELSE 0 END), 0),
            COALESCE(SUM(CASE WHEN t.platform = 'discord' THEN t.season_time ELSE 0 END), 0) +
            COALESCE(SUM(CASE WHEN t.platform = 'teamspeak' THEN t.season_time ELSE 0 END), 0)
        FROM user u
        LEFT JOIN time t ON
            (t.platform = 'discord' AND t.platform_uid = u.discord_id) OR
            (t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id)
        WHERE u.id = ?
    """, (user_id,))
    total_time, season_time = db.cursor.fetchone() or (0, 0)
    level = Config.get_level_for_minutes(total_time or 0)
    division = Config.get_division_for_minutes(season_time or 0)
    db.cursor.execute("""
        UPDATE user
        SET level = ?, division = ?
        WHERE id = ?
    """, (level, division, user_id))
    return {"level": level, "division": division}


def _move_time(db, platform, source_uid, target_uid):
    db.cursor.execute("""
        INSERT INTO time (
            platform_uid, platform, total_time, daily_time, weekly_time,
            monthly_time, season_time, last_update
        )
        SELECT
            ?, platform, total_time, daily_time, weekly_time,
            monthly_time, season_time, last_update
        FROM time
        WHERE platform = ? AND platform_uid = ?
        ON DUPLICATE KEY UPDATE
            total_time = time.total_time + VALUES(total_time),
            daily_time = time.daily_time + VALUES(daily_time),
            weekly_time = time.weekly_time + VALUES(weekly_time),
            monthly_time = time.monthly_time + VALUES(monthly_time),
            season_time = time.season_time + VALUES(season_time),
            last_update = GREATEST(time.last_update, VALUES(last_update))
    """, (target_uid, platform, source_uid))
    db.cursor.execute(
        "DELETE FROM time WHERE platform = ? AND platform_uid = ?",
        (platform, source_uid),
    )


def _move_heatmap(db, platform, source_uid, target_uid):
    db.cursor.execute("""
        INSERT INTO activity_heatmap (
            platform_uid, platform, day_of_week, time_category,
            activity_minutes, last_update
        )
        SELECT
            ?, platform, day_of_week, time_category,
            activity_minutes, last_update
        FROM activity_heatmap
        WHERE platform = ? AND platform_uid = ?
        ON DUPLICATE KEY UPDATE
            activity_minutes = activity_heatmap.activity_minutes + VALUES(activity_minutes),
            last_update = GREATEST(activity_heatmap.last_update, VALUES(last_update))
    """, (target_uid, platform, source_uid))
    db.cursor.execute(
        "DELETE FROM activity_heatmap WHERE platform = ? AND platform_uid = ?",
        (platform, source_uid),
    )


def _move_login_streak(db, platform, source_uid, target_uid):
    db.cursor.execute("""
        INSERT INTO login_streak (
            platform_uid, platform, logins, current_streak,
            longest_streak, last_login
        )
        SELECT
            ?, platform, logins, current_streak, longest_streak, last_login
        FROM login_streak
        WHERE platform = ? AND platform_uid = ?
        ON DUPLICATE KEY UPDATE
            logins = login_streak.logins + VALUES(logins),
            current_streak = GREATEST(login_streak.current_streak, VALUES(current_streak)),
            longest_streak = GREATEST(login_streak.longest_streak, VALUES(longest_streak)),
            last_login = GREATEST(login_streak.last_login, VALUES(last_login))
    """, (target_uid, platform, source_uid))
    db.cursor.execute(
        "DELETE FROM login_streak WHERE platform = ? AND platform_uid = ?",
        (platform, source_uid),
    )


def _move_special_achievements(db, platform, source_uid, target_uid):
    db.cursor.execute("""
        INSERT IGNORE INTO special_achievements
            (platform, platform_id, achievement_type, awarded_at)
        SELECT platform, ?, achievement_type, awarded_at
        FROM special_achievements
        WHERE platform = ? AND platform_id = ?
    """, (target_uid, platform, source_uid))
    db.cursor.execute(
        "DELETE FROM special_achievements WHERE platform = ? AND platform_id = ?",
        (platform, source_uid),
    )


def _move_platform_data(db, platform, source_uid, target_uid):
    _move_time(db, platform, source_uid, target_uid)
    _move_heatmap(db, platform, source_uid, target_uid)
    _move_login_streak(db, platform, source_uid, target_uid)
    _move_special_achievements(db, platform, source_uid, target_uid)


@admin_bp.route("/api/admin/players/search")
@admin_required
@handle_errors
def search_players():
    q = request.args.get("q", "").strip()
    if len(q) < 2:
        return jsonify({"players": []})

    db = DatabaseManager()
    like = f"%{q}%"
    db.cursor.execute("""
        SELECT
            id, steam_id, discord_id, teamspeak_id, COALESCE(name, 'Unknown'),
            COALESCE(level, 1), COALESCE(division, 1),
            COALESCE(ranking_disabled, 0)
        FROM user
        WHERE name LIKE ?
           OR CAST(steam_id AS CHAR) LIKE ?
           OR discord_id LIKE ?
           OR teamspeak_id LIKE ?
        ORDER BY
            CASE WHEN name LIKE ? THEN 0 ELSE 1 END,
            id DESC
        LIMIT 20
    """, (like, like, like, like, like))
    rows = db.cursor.fetchall()
    db.close()

    return jsonify({
        "players": [
            {
                "id": row[0],
                "steam_id": str(row[1]) if row[1] is not None else None,
                "discord_id": str(row[2]) if row[2] is not None else None,
                "teamspeak_id": str(row[3]) if row[3] is not None else None,
                "name": row[4],
                "level": row[5],
                "division": row[6],
                "ranking_disabled": bool(row[7]),
            }
            for row in rows
        ]
    })


@admin_bp.route("/api/admin/players/<int:user_id>")
@admin_required
@handle_errors
def get_player_detail(user_id):
    db = DatabaseManager()
    db.cursor.execute("""
        SELECT
            id, steam_id, discord_id, teamspeak_id, COALESCE(name, 'Unknown'),
            COALESCE(level, 1), COALESCE(division, 1),
            COALESCE(discord_channel, 0), COALESCE(teamspeak_channel, 0),
            COALESCE(discord_moveable, 1), COALESCE(teamspeak_moveable, 1),
            created_at, COALESCE(ranking_disabled, 0),
            ranking_disabled_at, ranking_disabled_reason
        FROM user
        WHERE id = ?
    """, (user_id,))
    user = db.cursor.fetchone()
    if not user:
        db.close()
        return jsonify({"error": "User not found"}), 404

    discord_id = str(user[2]) if user[2] is not None else None
    teamspeak_id = str(user[3]) if user[3] is not None else None

    platform_times = {}
    for platform, platform_uid in (("discord", discord_id), ("teamspeak", teamspeak_id)):
        if not platform_uid:
            platform_times[platform] = None
            continue
        db.cursor.execute("""
            SELECT total_time, daily_time, weekly_time, monthly_time, season_time, last_update
            FROM time
            WHERE platform = ? AND platform_uid = ?
        """, (platform, platform_uid))
        row = db.cursor.fetchone()
        platform_times[platform] = {
            "total_time": row[0],
            "daily_time": row[1],
            "weekly_time": row[2],
            "monthly_time": row[3],
            "season_time": row[4],
            "last_update": row[5].isoformat() if row and row[5] else None,
        } if row else None

    db.cursor.execute("""
        SELECT platform, COUNT(*), SUM(activity_minutes)
        FROM activity_heatmap
        WHERE (platform = 'discord' AND platform_uid = ?)
           OR (platform = 'teamspeak' AND platform_uid = ?)
        GROUP BY platform
    """, (discord_id, teamspeak_id))
    heatmap = {
        row[0]: {"slots": row[1], "minutes": int(row[2] or 0)}
        for row in db.cursor.fetchall()
    }

    db.cursor.execute("""
        SELECT platform, logins, current_streak, longest_streak, last_login
        FROM login_streak
        WHERE (platform = 'discord' AND platform_uid = ?)
           OR (platform = 'teamspeak' AND platform_uid = ?)
    """, (discord_id, teamspeak_id))
    streaks = {
        row[0]: {
            "logins": row[1],
            "current_streak": row[2],
            "longest_streak": row[3],
            "last_login": row[4].isoformat() if row[4] else None,
        }
        for row in db.cursor.fetchall()
    }

    db.close()
    return jsonify({
        "id": user[0],
        "steam_id": str(user[1]) if user[1] is not None else None,
        "discord_id": discord_id,
        "teamspeak_id": teamspeak_id,
        "name": user[4],
        "level": user[5],
        "division": user[6],
        "channels": {
            "discord": str(user[7]) if user[7] else None,
            "teamspeak": str(user[8]) if user[8] else None,
        },
        "moveable": {
            "discord": bool(user[9]),
            "teamspeak": bool(user[10]),
        },
        "created_at": user[11].isoformat() if user[11] else None,
        "ranking": {
            "disabled": bool(user[12]),
            "disabled_at": user[13].isoformat() if user[13] else None,
            "disabled_reason": user[14],
        },
        "platform_time": platform_times,
        "activity_heatmap": heatmap,
        "login_streaks": streaks,
    })


@admin_bp.route("/api/admin/ranking/transfer", methods=["POST"])
@admin_required
@csrf_required
@handle_errors
def transfer_ranking():
    body = request.get_json(silent=True) or {}
    source_user_id = body.get("source_user_id")
    target_user_id = body.get("target_user_id")
    platforms = body.get("platforms") or []
    reason = (body.get("reason") or "").strip()
    action = "ranking_transfer"
    target_identifiers = {
        "source_user_id": source_user_id,
        "target_user_id": target_user_id,
        "platforms": platforms,
    }

    db = DatabaseManager()
    try:
        if not source_user_id or not target_user_id:
            return _admin_error(db, action, target_identifiers, {}, "source_user_id and target_user_id are required")
        try:
            source_user_id = int(source_user_id)
            target_user_id = int(target_user_id)
        except (TypeError, ValueError):
            return _admin_error(db, action, target_identifiers, {}, "source_user_id and target_user_id must be numeric")
        if source_user_id == target_user_id:
            return _admin_error(db, action, target_identifiers, {}, "source and target must be different users")
        if not isinstance(platforms, list) or not platforms:
            return _admin_error(db, action, target_identifiers, {}, "platforms must not be empty")
        if any(platform not in VALID_PLATFORMS for platform in platforms):
            return _admin_error(db, action, target_identifiers, {}, "platforms may only contain discord and teamspeak")
        if not reason:
            return _admin_error(db, action, target_identifiers, {}, "reason is required")

        platforms = list(dict.fromkeys(platforms))
        source = _user_dict(_fetch_user_for_update(db, source_user_id))
        target = _user_dict(_fetch_user_for_update(db, target_user_id))
        if not source or not target:
            return _admin_error(db, action, target_identifiers, {}, "source or target user not found", 404)

        moved = []
        for platform in platforms:
            source_uid = source[f"{platform}_id"]
            target_uid = target[f"{platform}_id"]
            if not source_uid:
                return _admin_error(db, action, target_identifiers, {}, f"source user has no {platform} id")
            if not target_uid:
                return _admin_error(db, action, target_identifiers, {}, f"target user has no {platform} id")
            _move_platform_data(db, platform, source_uid, target_uid)
            moved.append({"platform": platform, "source_uid": source_uid, "target_uid": target_uid})

        target_rank = _recalculate_user_rank(db, target["id"])
        db.cursor.execute("""
            UPDATE user
            SET ranking_disabled = 1,
                ranking_disabled_at = CURRENT_TIMESTAMP,
                ranking_disabled_reason = ?
            WHERE id = ?
        """, (reason[:255], source["id"]))

        summary = {"moved": moved, "target_rank": target_rank, "reason": reason}
        _write_audit(db, action, target_identifiers, summary, "success")
        return jsonify({"ok": True, **summary})
    except Exception:
        db.conn.rollback()
        raise
    finally:
        db.close()


@admin_bp.route("/api/admin/steam/unlink", methods=["POST"])
@admin_required
@csrf_required
@handle_errors
def unlink_steam_platform():
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    platform = body.get("platform")
    reason = (body.get("reason") or "").strip()
    action = "steam_unlink"
    target_identifiers = {"user_id": user_id, "platform": platform}

    db = DatabaseManager()
    try:
        if platform not in VALID_PLATFORMS:
            return _admin_error(db, action, target_identifiers, {}, "platform must be discord or teamspeak")
        if not user_id:
            return _admin_error(db, action, target_identifiers, {}, "user_id is required")
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return _admin_error(db, action, target_identifiers, {}, "user_id must be numeric")
        if not reason:
            return _admin_error(db, action, target_identifiers, {}, "reason is required")

        user = _user_dict(_fetch_user_for_update(db, user_id))
        if not user:
            return _admin_error(db, action, target_identifiers, {}, "user not found", 404)
        if not user["steam_id"]:
            return _admin_error(db, action, target_identifiers, {}, "user has no SteamID")

        platform_uid = user[f"{platform}_id"]
        if not platform_uid:
            return _admin_error(db, action, target_identifiers, {}, f"user has no {platform} id")

        id_column = PLATFORM_ID_COLUMNS[platform]
        db.cursor.execute(
            f"SELECT id FROM user WHERE {id_column} = ? AND id <> ? FOR UPDATE",
            (platform_uid, user["id"]),
        )
        if db.cursor.fetchone():
            return _admin_error(db, action, target_identifiers, {}, f"{platform} id already exists on another user")

        db.cursor.execute("""
            SELECT COALESCE(total_time, 0), COALESCE(season_time, 0)
            FROM time
            WHERE platform = ? AND platform_uid = ?
        """, (platform, platform_uid))
        time_row = db.cursor.fetchone() or (0, 0)
        new_level = Config.get_level_for_minutes(time_row[0] or 0)
        new_division = Config.get_division_for_minutes(time_row[1] or 0)

        db.cursor.execute(f"""
            INSERT INTO user ({id_column}, name, level, division, created_at)
            VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, (platform_uid, user["name"], new_level, new_division))
        new_user_id = db.cursor.lastrowid

        db.cursor.execute(f"""
            UPDATE user
            SET {id_column} = NULL
            WHERE id = ?
        """, (user["id"],))
        original_rank = _recalculate_user_rank(db, user["id"])

        summary = {
            "new_user_id": new_user_id,
            "platform_uid": platform_uid,
            "new_user_rank": {"level": new_level, "division": new_division},
            "original_user_rank": original_rank,
            "reason": reason,
        }
        _write_audit(db, action, target_identifiers, summary, "success")
        return jsonify({"ok": True, **summary})
    except Exception:
        db.conn.rollback()
        raise
    finally:
        db.close()


@admin_bp.route("/api/admin/ranking/ignore-role", methods=["POST"])
@admin_required
@csrf_required
@handle_errors
def assign_ignore_role():
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    platform = body.get("platform")
    reason = (body.get("reason") or "").strip()
    action = "ranking_ignore_role"
    target_identifiers = {"user_id": user_id, "platform": platform}

    db = DatabaseManager()
    try:
        if platform not in VALID_PLATFORMS:
            return _admin_error(db, action, target_identifiers, {}, "platform must be discord or teamspeak")
        if not user_id:
            return _admin_error(db, action, target_identifiers, {}, "user_id is required")
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return _admin_error(db, action, target_identifiers, {}, "user_id must be numeric")
        if not reason:
            return _admin_error(db, action, target_identifiers, {}, "reason is required")

        id_column = PLATFORM_ID_COLUMNS[platform]
        db.cursor.execute(f"""
            SELECT {id_column}
            FROM user
            WHERE id = ?
        """, (user_id,))
        row = db.cursor.fetchone()
        if not row:
            return _admin_error(db, action, target_identifiers, {}, "user not found", 404)
        platform_uid = str(row[0]) if row[0] is not None else None
        if not platform_uid:
            return _admin_error(db, action, target_identifiers, {}, f"user has no {platform} id")

        command_response = valkey_manager.set_ignore_role(platform, platform_uid)
        if isinstance(command_response, dict):
            result = bool(command_response.get("ok", command_response.get("result")))
        else:
            result = bool(command_response)
            command_response = {"ok": result, "result": command_response}
        result_status = "success" if result else "failed"
        summary = {
            "platform_uid": platform_uid,
            "role_id": Config.DISCORD_EXCLUDED_ROLE_ID if platform == "discord" else Config.TS3_EXCLUDED_ROLE_ID,
            "reason": reason,
            "command_response": command_response,
        }
        _write_audit(db, action, target_identifiers, summary, result_status)

        if not result:
            return jsonify({
                "error": command_response.get("error") or "Failed to assign ignore role",
                "details": command_response,
            }), 502
        return jsonify({"ok": True, **summary})
    finally:
        db.close()


@admin_bp.route("/api/admin/ttt/season-skin", methods=["POST"])
@admin_required
@csrf_required
@handle_errors
def grant_ttt_season_skin():
    body = request.get_json(silent=True) or {}
    steam_id64 = str(body.get("steam_id64") or "").strip()
    tier = body.get("tier")
    reason = (body.get("reason") or "").strip()
    action = "ttt_season_skin_grant"
    target_identifiers = {"steam_id64": steam_id64, "tier": tier}

    db = DatabaseManager()
    try:
        if not steam_id64.isdigit():
            return _admin_error(db, action, target_identifiers, {}, "steam_id64 must be numeric")
        try:
            tier = int(tier)
        except (TypeError, ValueError):
            return _admin_error(db, action, target_identifiers, {}, "tier must be numeric")
        item_uuid = Config.TTT_SEASON_REWARD_ITEM_UUIDS.get(tier)
        if not item_uuid:
            return _admin_error(db, action, target_identifiers, {}, "tier is not configured")
        if not reason:
            return _admin_error(db, action, target_identifiers, {}, "reason is required")

        steam_id2 = steamid64_to_steam2(steam_id64)
        command_payload = {
            "steam_id64": steam_id64,
            "steam_id2": steam_id2,
            "tier": tier,
            "item_uuid": item_uuid,
            "reward_key": f"season_1_tier_{tier}",
        }
        grant_payload, status_code = valkey_manager.gameserver_command(
            "ttt",
            "grant_season_skin",
            command_payload,
            timeout_seconds=60,
        )
        result_status = "success" if status_code == 200 else "failed"
        _write_audit(db, action, target_identifiers, {
            "command_payload": command_payload,
            "response": grant_payload,
            "reason": reason,
        }, result_status)
        return jsonify(grant_payload), status_code
    finally:
        db.close()


@admin_bp.route("/api/admin/audit-log")
@admin_required
@handle_errors
def audit_log():
    limit = min(int(request.args.get("limit", 50)), 100)
    db = DatabaseManager()
    db.cursor.execute("""
        SELECT
            id, admin_steam_id, action, target_identifiers,
            summary, result_status, created_at
        FROM admin_audit_log
        ORDER BY created_at DESC, id DESC
        LIMIT ?
    """, (limit,))
    rows = db.cursor.fetchall()
    db.close()

    entries = []
    for row in rows:
        entries.append({
            "id": row[0],
            "admin_steam_id": row[1],
            "action": row[2],
            "target_identifiers": json.loads(row[3]) if row[3] else {},
            "summary": json.loads(row[4]) if row[4] else {},
            "result_status": row[5],
            "created_at": row[6].isoformat() if row[6] else None,
        })
    return jsonify({"entries": entries})
