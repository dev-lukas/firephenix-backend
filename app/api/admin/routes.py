import json
from datetime import datetime

from flask import Blueprint, jsonify, request, session

from app.config import Config
from app.utils.database import (
    DatabaseManager,
    get_ttt_season_reward_item_uuid,
    get_ttt_season_reward_key,
)
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
SPECIAL_ACHIEVEMENTS = {
    1: {"name": "Altes Eisen", "description": "Sei ein Urgestein von FirePhenix"},
    2: {"name": "Ehrenmitglied", "description": "Unterstütze den Server in der Vergangenheit"},
}


def _admin_steam_id():
    return str(session.get("steam_id"))


def _fetch_user_for_update(db, user_id):
    db.cursor.execute("""
        SELECT
            id, steam_id, discord_id, teamspeak_id, name, level, division,
            COALESCE(ranking_disabled, 0)
        FROM user
        WHERE id = %s
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


def _linked_platforms(user):
    return [
        platform
        for platform in ("teamspeak", "discord")
        if user.get(f"{platform}_id")
    ]


def _parse_non_negative_minutes(value, field_name):
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be numeric")
    if minutes < 0:
        raise ValueError(f"{field_name} must be non-negative")
    return minutes


def _parse_join_date(value):
    if not isinstance(value, str) or not value:
        raise ValueError("created_at is required")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise ValueError("created_at must use YYYY-MM-DD")
    if parsed.date() > datetime.now().date():
        raise ValueError("created_at must not be in the future")
    return parsed


def _fetch_active_admin_target(db, user_id, platform=None):
    user = _user_dict(_fetch_user_for_update(db, user_id))
    if not user:
        return None, None, "user not found", 404
    if user["ranking_disabled"]:
        return user, None, "ranking-disabled users cannot be edited", 400
    linked_platforms = _linked_platforms(user)
    if not linked_platforms:
        return user, None, "user has no linked platform", 400
    if platform is not None:
        if platform not in VALID_PLATFORMS:
            return user, None, "platform must be discord or teamspeak", 400
        platform_uid = user[f"{platform}_id"]
        if not platform_uid:
            return user, None, f"user has no {platform} id", 400
        return user, platform_uid, None, None
    return user, None, None, None


def _write_audit(db, action, target_identifiers, summary, result_status):
    db.cursor.execute("""
        INSERT INTO admin_audit_log
            (admin_steam_id, action, target_identifiers, summary, result_status)
        VALUES (%s, %s, %s, %s, %s)
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
        WHERE u.id = %s
    """, (user_id,))
    total_time, season_time = db.cursor.fetchone() or (0, 0)
    level = Config.get_level_for_minutes(total_time or 0)
    division = Config.get_division_for_minutes(season_time or 0)
    db.cursor.execute("""
        UPDATE user
        SET level = %s, division = %s
        WHERE id = %s
    """, (level, division, user_id))
    return {"level": level, "division": division}


def _move_time(db, platform, source_uid, target_uid):
    db.cursor.execute("""
        INSERT INTO time (
            platform_uid, platform, total_time, daily_time, weekly_time,
            monthly_time, season_time, last_update
        )
        SELECT
            %s, platform, total_time, daily_time, weekly_time,
            monthly_time, season_time, last_update
        FROM time src
        WHERE src.platform = %s AND src.platform_uid = %s
        ON DUPLICATE KEY UPDATE
            time.total_time = time.total_time + VALUES(total_time),
            time.daily_time = time.daily_time + VALUES(daily_time),
            time.weekly_time = time.weekly_time + VALUES(weekly_time),
            time.monthly_time = time.monthly_time + VALUES(monthly_time),
            time.season_time = time.season_time + VALUES(season_time),
            time.last_update = GREATEST(time.last_update, VALUES(last_update))
    """, (target_uid, platform, source_uid))
    db.cursor.execute(
        "DELETE FROM time WHERE platform = %s AND platform_uid = %s",
        (platform, source_uid),
    )


def _move_heatmap(db, platform, source_uid, target_uid):
    db.cursor.execute("""
        INSERT INTO activity_heatmap (
            platform_uid, platform, day_of_week, time_category,
            activity_minutes, last_update
        )
        SELECT
            %s, platform, day_of_week, time_category,
            activity_minutes, last_update
        FROM activity_heatmap src
        WHERE src.platform = %s AND src.platform_uid = %s
        ON DUPLICATE KEY UPDATE
            activity_heatmap.activity_minutes = activity_heatmap.activity_minutes + VALUES(activity_minutes),
            activity_heatmap.last_update = GREATEST(activity_heatmap.last_update, VALUES(last_update))
    """, (target_uid, platform, source_uid))
    db.cursor.execute(
        "DELETE FROM activity_heatmap WHERE platform = %s AND platform_uid = %s",
        (platform, source_uid),
    )


def _move_login_streak(db, platform, source_uid, target_uid):
    db.cursor.execute("""
        INSERT INTO login_streak (
            platform_uid, platform, logins, current_streak,
            longest_streak, last_login
        )
        SELECT
            %s, platform, logins, current_streak, longest_streak, last_login
        FROM login_streak src
        WHERE src.platform = %s AND src.platform_uid = %s
        ON DUPLICATE KEY UPDATE
            login_streak.logins = login_streak.logins + VALUES(logins),
            login_streak.current_streak = GREATEST(login_streak.current_streak, VALUES(current_streak)),
            login_streak.longest_streak = GREATEST(login_streak.longest_streak, VALUES(longest_streak)),
            login_streak.last_login = GREATEST(login_streak.last_login, VALUES(last_login))
    """, (target_uid, platform, source_uid))
    db.cursor.execute(
        "DELETE FROM login_streak WHERE platform = %s AND platform_uid = %s",
        (platform, source_uid),
    )


def _move_special_achievements(db, platform, source_uid, target_uid):
    db.cursor.execute("""
        INSERT IGNORE INTO special_achievements
            (platform, platform_id, achievement_type, awarded_at)
        SELECT platform, %s, achievement_type, awarded_at
        FROM special_achievements
        WHERE platform = %s AND platform_id = %s
    """, (target_uid, platform, source_uid))
    db.cursor.execute(
        "DELETE FROM special_achievements WHERE platform = %s AND platform_id = %s",
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
        WHERE name LIKE %s
           OR CAST(steam_id AS CHAR) LIKE %s
           OR discord_id LIKE %s
           OR teamspeak_id LIKE %s
        ORDER BY
            CASE WHEN name LIKE %s THEN 0 ELSE 1 END,
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


@admin_bp.route("/api/admin/special-achievements")
@admin_required
@handle_errors
def special_achievements_catalog():
    return jsonify({
        "achievements": [
            {
                "achievement_type": achievement_type,
                "name": data["name"],
                "description": data["description"],
            }
            for achievement_type, data in SPECIAL_ACHIEVEMENTS.items()
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
        WHERE id = %s
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
            WHERE platform = %s AND platform_uid = %s
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
        WHERE (platform = 'discord' AND platform_uid = %s)
           OR (platform = 'teamspeak' AND platform_uid = %s)
        GROUP BY platform
    """, (discord_id, teamspeak_id))
    heatmap = {
        row[0]: {"slots": row[1], "minutes": int(row[2] or 0)}
        for row in db.cursor.fetchall()
    }

    db.cursor.execute("""
        SELECT platform, logins, current_streak, longest_streak, last_login
        FROM login_streak
        WHERE (platform = 'discord' AND platform_uid = %s)
           OR (platform = 'teamspeak' AND platform_uid = %s)
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

    special_achievements = []
    if discord_id or teamspeak_id:
        db.cursor.execute(f"""
            SELECT platform, platform_id, achievement_type, awarded_at
            FROM special_achievements
            WHERE achievement_type IN ({','.join(['%s'] * len(SPECIAL_ACHIEVEMENTS))})
              AND (
                (platform = 'discord' AND platform_id = %s)
                OR (platform = 'teamspeak' AND platform_id = %s)
              )
            ORDER BY achievement_type, platform
        """, (
            *SPECIAL_ACHIEVEMENTS.keys(),
            discord_id,
            teamspeak_id,
        ))
        special_achievements = [
            {
                "platform": row[0],
                "platform_id": str(row[1]),
                "achievement_type": row[2],
                "name": SPECIAL_ACHIEVEMENTS[row[2]]["name"],
                "awarded_at": row[3].isoformat() if row[3] else None,
            }
            for row in db.cursor.fetchall()
        ]

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
        "special_achievements": special_achievements,
    })


@admin_bp.route("/api/admin/players/<int:user_id>/time", methods=["POST"])
@admin_required
@csrf_required
@handle_errors
def update_player_time(user_id):
    body = request.get_json(silent=True) or {}
    platform = body.get("platform")
    reason = (body.get("reason") or "").strip()
    action = "ranking_time_update"
    target_identifiers = {"user_id": user_id, "platform": platform}

    db = DatabaseManager()
    try:
        if not reason:
            return _admin_error(db, action, target_identifiers, {}, "reason is required")
        try:
            total_time = _parse_non_negative_minutes(body.get("total_time"), "total_time")
            season_time = _parse_non_negative_minutes(body.get("season_time"), "season_time")
        except ValueError as error:
            return _admin_error(db, action, target_identifiers, {}, str(error))
        if season_time > total_time:
            return _admin_error(db, action, target_identifiers, {}, "season_time must not exceed total_time")

        user, platform_uid, error, status = _fetch_active_admin_target(db, user_id, platform)
        if error:
            return _admin_error(db, action, target_identifiers, {}, error, status)

        db.cursor.execute("""
            SELECT
                COALESCE(total_time, 0),
                COALESCE(daily_time, 0),
                COALESCE(weekly_time, 0),
                COALESCE(monthly_time, 0),
                COALESCE(season_time, 0)
            FROM time
            WHERE platform = %s AND platform_uid = %s
            FOR UPDATE
        """, (platform, platform_uid))
        old_row = db.cursor.fetchone()
        old_time = {
            "total_time": old_row[0] if old_row else 0,
            "daily_time": old_row[1] if old_row else 0,
            "weekly_time": old_row[2] if old_row else 0,
            "monthly_time": old_row[3] if old_row else 0,
            "season_time": old_row[4] if old_row else 0,
        }
        next_daily = min(old_time["daily_time"], total_time)
        next_weekly = min(old_time["weekly_time"], total_time)
        next_monthly = min(old_time["monthly_time"], total_time)

        db.cursor.execute("""
            INSERT INTO time (
                platform_uid, platform, total_time, daily_time, weekly_time,
                monthly_time, season_time, last_update
            )
            VALUES (%s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP)
            ON DUPLICATE KEY UPDATE
                total_time = VALUES(total_time),
                daily_time = VALUES(daily_time),
                weekly_time = VALUES(weekly_time),
                monthly_time = VALUES(monthly_time),
                season_time = VALUES(season_time),
                last_update = CURRENT_TIMESTAMP
        """, (
            platform_uid,
            platform,
            total_time,
            next_daily,
            next_weekly,
            next_monthly,
            season_time,
        ))
        rank = _recalculate_user_rank(db, user["id"])
        summary = {
            "platform_uid": platform_uid,
            "old_time": old_time,
            "new_time": {
                "total_time": total_time,
                "daily_time": next_daily,
                "weekly_time": next_weekly,
                "monthly_time": next_monthly,
                "season_time": season_time,
            },
            "rank": rank,
            "reason": reason,
        }
        _write_audit(db, action, target_identifiers, summary, "success")
        return jsonify({"ok": True, **summary})
    except Exception:
        db.conn.rollback()
        raise
    finally:
        db.close()


@admin_bp.route("/api/admin/players/<int:user_id>/join-date", methods=["POST"])
@admin_required
@csrf_required
@handle_errors
def update_player_join_date(user_id):
    body = request.get_json(silent=True) or {}
    reason = (body.get("reason") or "").strip()
    action = "user_join_date_update"
    target_identifiers = {"user_id": user_id}

    db = DatabaseManager()
    try:
        if not reason:
            return _admin_error(db, action, target_identifiers, {}, "reason is required")
        try:
            created_at = _parse_join_date(body.get("created_at"))
        except ValueError as error:
            return _admin_error(db, action, target_identifiers, {}, str(error))

        user, _, error, status = _fetch_active_admin_target(db, user_id)
        if error:
            return _admin_error(db, action, target_identifiers, {}, error, status)

        db.cursor.execute("SELECT created_at FROM user WHERE id = %s", (user["id"],))
        old_created_at = db.cursor.fetchone()
        db.cursor.execute("""
            UPDATE user
            SET created_at = %s
            WHERE id = %s
        """, (created_at, user["id"]))
        summary = {
            "old_created_at": old_created_at[0].isoformat() if old_created_at and old_created_at[0] else None,
            "new_created_at": created_at.isoformat(),
            "reason": reason,
        }
        _write_audit(db, action, target_identifiers, summary, "success")
        return jsonify({"ok": True, **summary})
    except Exception:
        db.conn.rollback()
        raise
    finally:
        db.close()


def _special_achievement_request(action):
    body = request.get_json(silent=True) or {}
    user_id = body.get("user_id")
    platform = body.get("platform")
    achievement_type = body.get("achievement_type")
    reason = (body.get("reason") or "").strip()
    target_identifiers = {
        "user_id": user_id,
        "platform": platform,
        "achievement_type": achievement_type,
    }

    db = DatabaseManager()
    try:
        if not reason:
            return _admin_error(db, action, target_identifiers, {}, "reason is required")
        try:
            user_id = int(user_id)
        except (TypeError, ValueError):
            return _admin_error(db, action, target_identifiers, {}, "user_id must be numeric")
        try:
            achievement_type = int(achievement_type)
        except (TypeError, ValueError):
            return _admin_error(db, action, target_identifiers, {}, "achievement_type must be numeric")
        if achievement_type not in SPECIAL_ACHIEVEMENTS:
            return _admin_error(db, action, target_identifiers, {}, "achievement_type is not managed")

        user, platform_uid, error, status = _fetch_active_admin_target(db, user_id, platform)
        if error:
            return _admin_error(db, action, target_identifiers, {}, error, status)

        db.cursor.execute("""
            SELECT id
            FROM special_achievements
            WHERE platform = %s AND platform_id = %s AND achievement_type = %s
            FOR UPDATE
        """, (platform, platform_uid, achievement_type))
        existing = db.cursor.fetchone()
        if action == "special_achievement_grant":
            if not existing:
                db.cursor.execute("""
                    INSERT INTO special_achievements
                        (platform, platform_id, achievement_type)
                    VALUES (%s, %s, %s)
                """, (platform, platform_uid, achievement_type))
            changed_key = "created"
            changed = existing is None
        else:
            if existing:
                db.cursor.execute("""
                    DELETE FROM special_achievements
                    WHERE platform = %s AND platform_id = %s AND achievement_type = %s
                """, (platform, platform_uid, achievement_type))
            changed_key = "deleted"
            changed = existing is not None

        summary = {
            "platform_uid": platform_uid,
            "achievement_type": achievement_type,
            "achievement_name": SPECIAL_ACHIEVEMENTS[achievement_type]["name"],
            changed_key: changed,
            "reason": reason,
        }
        _write_audit(db, action, target_identifiers, summary, "success")
        return jsonify({"ok": True, **summary})
    except Exception:
        db.conn.rollback()
        raise
    finally:
        db.close()


@admin_bp.route("/api/admin/special-achievements/grant", methods=["POST"])
@admin_required
@csrf_required
@handle_errors
def grant_special_achievement():
    return _special_achievement_request("special_achievement_grant")


@admin_bp.route("/api/admin/special-achievements/revoke", methods=["POST"])
@admin_required
@csrf_required
@handle_errors
def revoke_special_achievement():
    return _special_achievement_request("special_achievement_revoke")


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
                ranking_disabled_reason = %s
            WHERE id = %s
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
            f"SELECT id FROM user WHERE {id_column} = %s AND id <> %s FOR UPDATE",
            (platform_uid, user["id"]),
        )
        if db.cursor.fetchone():
            return _admin_error(db, action, target_identifiers, {}, f"{platform} id already exists on another user")

        db.cursor.execute("""
            SELECT COALESCE(total_time, 0), COALESCE(season_time, 0)
            FROM time
            WHERE platform = %s AND platform_uid = %s
        """, (platform, platform_uid))
        time_row = db.cursor.fetchone() or (0, 0)
        new_level = Config.get_level_for_minutes(time_row[0] or 0)
        new_division = Config.get_division_for_minutes(time_row[1] or 0)

        db.cursor.execute(f"""
            INSERT INTO user ({id_column}, name, level, division, created_at)
            VALUES (%s, %s, %s, %s, CURRENT_TIMESTAMP)
        """, (platform_uid, user["name"], new_level, new_division))
        new_user_id = db.cursor.lastrowid

        remaining_platforms = [
            existing_platform
            for existing_platform in VALID_PLATFORMS
            if existing_platform != platform and user[f"{existing_platform}_id"]
        ]
        original_user_disabled = not remaining_platforms
        db.cursor.execute(f"""
            UPDATE user
            SET {id_column} = NULL,
                ranking_disabled = CASE WHEN %s = 1 THEN 1 ELSE ranking_disabled END,
                ranking_disabled_at = CASE
                    WHEN %s = 1 THEN CURRENT_TIMESTAMP
                    ELSE ranking_disabled_at
                END,
                ranking_disabled_reason = CASE
                    WHEN %s = 1 THEN %s
                    ELSE ranking_disabled_reason
                END
            WHERE id = %s
        """, (
            int(original_user_disabled),
            int(original_user_disabled),
            int(original_user_disabled),
            reason[:255],
            user["id"],
        ))
        original_rank = _recalculate_user_rank(db, user["id"])

        summary = {
            "new_user_id": new_user_id,
            "platform_uid": platform_uid,
            "new_user_rank": {"level": new_level, "division": new_division},
            "original_user_rank": original_rank,
            "original_user_disabled": original_user_disabled,
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
            WHERE id = %s
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

        if not result:
            _write_audit(db, action, target_identifiers, summary, result_status)
            return jsonify({
                "error": command_response.get("error") or "Failed to assign ignore role",
                "details": command_response,
            }), 502

        db.cursor.execute("""
            UPDATE user
            SET ranking_disabled = 1,
                ranking_disabled_at = CURRENT_TIMESTAMP,
                ranking_disabled_reason = %s
            WHERE id = %s
        """, (reason[:255], user_id))
        summary["ranking_disabled"] = True
        _write_audit(db, action, target_identifiers, summary, result_status)
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
    season_number = body.get("season", body.get("season_number", 1))
    reason = (body.get("reason") or "").strip()
    action = "ttt_season_skin_grant"
    target_identifiers = {"steam_id64": steam_id64, "season": season_number, "tier": tier}

    db = DatabaseManager()
    try:
        if not steam_id64.isdigit():
            return _admin_error(db, action, target_identifiers, {}, "steam_id64 must be numeric")
        try:
            tier = int(tier)
        except (TypeError, ValueError):
            return _admin_error(db, action, target_identifiers, {}, "tier must be numeric")
        try:
            season_number = int(season_number)
        except (TypeError, ValueError):
            return _admin_error(db, action, target_identifiers, {}, "season must be numeric")
        item_uuid = get_ttt_season_reward_item_uuid(season_number, tier)
        if not item_uuid:
            return _admin_error(db, action, target_identifiers, {}, "tier is not configured")
        if not reason:
            return _admin_error(db, action, target_identifiers, {}, "reason is required")

        steam_id2 = steamid64_to_steam2(steam_id64)
        command_payload = {
            "steam_id64": steam_id64,
            "steam_id2": steam_id2,
            "season": season_number,
            "tier": tier,
            "item_uuid": item_uuid,
            "reward_key": get_ttt_season_reward_key(season_number, tier),
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
    try:
        limit = int(request.args.get("limit", 5))
    except (TypeError, ValueError):
        limit = 5
    limit = max(1, min(limit, 100))
    db = DatabaseManager()
    db.cursor.execute("""
        SELECT
            id, admin_steam_id, action, target_identifiers,
            summary, result_status, created_at
        FROM admin_audit_log
        ORDER BY created_at DESC, id DESC
        LIMIT %s
    """, (limit + 1,))
    rows = db.cursor.fetchall()
    db.close()

    has_more = len(rows) > limit
    rows = rows[:limit]
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
    return jsonify({"entries": entries, "has_more": has_more})
