import asyncio
import inspect
from datetime import datetime

import discord
from discord import app_commands
from discord.ext import commands

from app.config import Config
from app.utils.database import (
    DatabaseManager,
    build_ttt_achievement_payload,
    get_best_division_from_season_achievements,
)
from app.utils.logger import RankingLogger
from app.utils.source_server import (
    SourceServerQueryError,
    SourceServerTimeout,
    query_source_server,
)


logging = RankingLogger(__name__).get_logger()

DIVISION_NAMES = {
    1: "Bronze",
    2: "Silber",
    3: "Gold",
    4: "Platin",
    5: "Diamant",
    6: "Phönix",
}

DISCORD_INVITE_URL = "https://discord.gg/sT4NPRQSAT"
TTT_PASSWORD = "ember"
TEAMSPEAK_PUBLIC_HOST = "firephenix.de"


def format_minutes_short(minutes):
    try:
        minutes = max(0, int(minutes or 0))
    except (TypeError, ValueError):
        minutes = 0

    hours = minutes // 60
    mins = minutes % 60
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def level_label(level):
    try:
        level = int(level or 1)
    except (TypeError, ValueError):
        level = 1
    if level <= 20:
        return f"Level {level}"
    return f"Prestige {level - 20}"


def division_label(division):
    try:
        division = int(division or 1)
    except (TypeError, ValueError):
        division = 1
    return DIVISION_NAMES.get(division, "Unbekannt")


def progress_bar(current, target, width=12):
    try:
        current = max(0, int(current or 0))
        target = max(0, int(target or 0))
    except (TypeError, ValueError):
        current = 0
        target = 0

    if target <= 0:
        ratio = 1
    else:
        ratio = min(1, current / target)
    filled = round(ratio * width)
    return "█" * filled + "░" * (width - filled), round(ratio * 100)


def format_datetime(value):
    if not value:
        return "Noch nie"
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y")
    return str(value)


def avatar_url(member):
    return getattr(getattr(member, "display_avatar", None), "url", None)


def ttt_server_address():
    return f"{Config.TTT_STATUS_HOST}:{Config.TTT_STATUS_PORT}"


def _safe_ratio(numerator, denominator):
    try:
        numerator = int(numerator or 0)
        denominator = int(denominator or 0)
    except (TypeError, ValueError):
        return 0
    if denominator <= 0:
        return 0
    return numerator / denominator


class DiscordProfileService:
    def __init__(self, db_factory=DatabaseManager):
        self.db_factory = db_factory

    def get_profile(self, discord_id):
        db = self.db_factory()
        try:
            query = """
            WITH ranked_users AS (
                SELECT
                    u.id,
                    COALESCE(u.name, 'Unknown') AS name,
                    u.discord_id,
                    u.teamspeak_id,
                    u.steam_id,
                    COALESCE(u.level, 1) AS level,
                    COALESCE(u.division, 1) AS division,
                    COALESCE(u.ranking_disabled, 0) AS ranking_disabled,
                    COALESCE(d.total_time, 0) + COALESCE(t.total_time, 0) AS total_time,
                    COALESCE(d.daily_time, 0) + COALESCE(t.daily_time, 0) AS daily_time,
                    COALESCE(d.weekly_time, 0) + COALESCE(t.weekly_time, 0) AS weekly_time,
                    COALESCE(d.monthly_time, 0) + COALESCE(t.monthly_time, 0) AS monthly_time,
                    COALESCE(d.season_time, 0) + COALESCE(t.season_time, 0) AS season_time,
                    COALESCE(d.total_time, 0) AS discord_time,
                    COALESCE(t.total_time, 0) AS teamspeak_time,
                    RANK() OVER (
                        ORDER BY COALESCE(d.total_time, 0) + COALESCE(t.total_time, 0) DESC
                    ) AS ranking_position,
                    COUNT(*) OVER () AS total_users
                FROM user u
                LEFT JOIN time d
                    ON d.platform = 'discord'
                    AND d.platform_uid = u.discord_id
                LEFT JOIN time t
                    ON t.platform = 'teamspeak'
                    AND t.platform_uid = u.teamspeak_id
                WHERE COALESCE(u.ranking_disabled, 0) = 0
            )
            SELECT *
            FROM ranked_users
            WHERE discord_id = %s
            """
            rows = db.execute_query(query, (str(discord_id),)) or []
            if not rows:
                disabled = db.execute_query(
                    """
                    SELECT id
                    FROM user
                    WHERE discord_id = %s
                        AND COALESCE(ranking_disabled, 0) = 1
                    """,
                    (str(discord_id),),
                ) or []
                if disabled:
                    return {"state": "disabled"}
                return {"state": "missing"}

            row = rows[0]
            profile = {
                "state": "ok",
                "id": row[0],
                "name": row[1],
                "discord_id": str(row[2]) if row[2] else None,
                "teamspeak_id": str(row[3]) if row[3] else None,
                "steam_id": str(row[4]) if row[4] else None,
                "level": int(row[5] or 1),
                "division": int(row[6] or 1),
                "total_time": int(row[8] or 0),
                "daily_time": int(row[9] or 0),
                "weekly_time": int(row[10] or 0),
                "monthly_time": int(row[11] or 0),
                "season_time": int(row[12] or 0),
                "discord_time": int(row[13] or 0),
                "teamspeak_time": int(row[14] or 0),
                "rank": int(row[15] or 0),
                "total_users": int(row[16] or 0),
            }

            profile["time_to_next_level"] = self._time_to_next_level(profile)
            profile["time_to_next_division"] = self._time_to_next_division(db, profile)
            profile["special_achievements"] = self._special_achievements(db, profile)
            profile["achievement_summary"] = self._achievement_summary(db, profile)
            profile["ttt_stats"] = (
                db.get_ttt_player_stats(profile["steam_id"])
                if profile["steam_id"]
                else None
            )
            return profile
        finally:
            close = getattr(db, "close", None)
            if close:
                close()

    def _time_to_next_level(self, profile):
        if profile["level"] >= 25:
            return None
        return max(0, Config.get_level_requirement(profile["level"] + 1) - profile["total_time"])

    def _time_to_next_division(self, db, profile):
        if profile["division"] < 5:
            return max(
                0,
                Config.get_division_requirement(profile["division"] + 1) - profile["season_time"],
            )
        if profile["division"] == 5:
            rows = db.execute_query("""
                SELECT COUNT(u.id), MIN(COALESCE(d.season_time, 0) + COALESCE(t.season_time, 0))
                FROM user u
                LEFT JOIN time d ON d.platform = 'discord' AND d.platform_uid = u.discord_id
                LEFT JOIN time t ON t.platform = 'teamspeak' AND t.platform_uid = u.teamspeak_id
                WHERE u.division = 6
                    AND COALESCE(u.ranking_disabled, 0) = 0
            """) or [(0, None)]
            div6_count, lowest_div6_time = rows[0]
            if div6_count and div6_count >= Config.TOP_DIVISION_PLAYER_AMOUNT and lowest_div6_time is not None:
                return max(0, int(lowest_div6_time) - profile["season_time"] + 1)
            return max(0, Config.get_division_requirement(5) - profile["season_time"])
        return None

    def _special_achievements(self, db, profile):
        if not profile["discord_id"] and not profile["teamspeak_id"]:
            return []
        rows = db.execute_query(
            """
            SELECT achievement_type
            FROM special_achievements
            WHERE (platform = 'discord' AND platform_id = %s)
               OR (platform = 'teamspeak' AND platform_id = %s)
            """,
            (profile["discord_id"], profile["teamspeak_id"]),
        ) or []
        return [row[0] for row in rows]

    def _achievement_summary(self, db, profile):
        streak_rows = db.execute_query(
            """
            SELECT
                SUM(logins) AS total_logins,
                MAX(longest_streak) AS longest_streak
            FROM login_streak
            WHERE (platform = 'discord' AND platform_uid = %s)
               OR (platform = 'teamspeak' AND platform_uid = %s)
            """,
            (profile["discord_id"], profile["teamspeak_id"]),
        ) or [(0, 0)]
        total_logins, longest_streak = streak_rows[0]
        total_logins = int(total_logins or 0)
        longest_streak = int(longest_streak or 0)

        heatmap_rows = db.execute_query(
            """
            SELECT
                COUNT(DISTINCT day_of_week),
                COUNT(DISTINCT CONCAT(day_of_week, '_', time_category))
            FROM activity_heatmap
            WHERE activity_minutes > 0
                AND (
                    (platform = 'discord' AND platform_uid = %s)
                    OR (platform = 'teamspeak' AND platform_uid = %s)
                )
            """,
            (profile["discord_id"], profile["teamspeak_id"]),
        ) or [(0, 0)]
        active_days, active_slots = heatmap_rows[0]
        active_days = int(active_days or 0)
        active_slots = int(active_slots or 0)

        def threshold_level(value, thresholds):
            return sum(1 for threshold in thresholds if value >= threshold)

        return {
            "streak": threshold_level(longest_streak, [2, 7, 14, 30]),
            "logins": threshold_level(total_logins, [2, 30, 365, 3650]),
            "time": threshold_level(profile["total_time"] / 60, [1, 10, 100, 1000]),
            "heatmap": 4 if active_slots >= 28 else threshold_level(active_days, [3, 5, 7]),
            "division": get_best_division_from_season_achievements(profile["special_achievements"]),
            "old_member": 1 if 1 in profile["special_achievements"] else 0,
            "legacy_supporter": 1 if 2 in profile["special_achievements"] else 0,
            "apex": 1 if 200 in profile["special_achievements"] else 0,
            "total_logins": total_logins,
            "longest_streak": longest_streak,
            "active_days": active_days,
        }


def unavailable_embed(member, state):
    title = f"{member.display_name} ist noch nicht im Ranking"
    description = "Sobald Spielzeit auf Discord oder TeamSpeak erfasst wurde, erscheint hier ein Profil."
    if state == "disabled":
        title = f"{member.display_name} ist vom Ranking ausgeschlossen"
        description = "Für diesen Nutzer werden keine öffentlichen Rankingdaten angezeigt."
    return discord.Embed(title=title, description=description, color=discord.Color.dark_gray())


def build_status_embed(profile, member):
    if profile["state"] != "ok":
        return unavailable_embed(member, profile["state"])

    level_req = Config.get_level_requirement(profile["level"])
    next_level_req = Config.get_level_requirement(profile["level"] + 1) if profile["level"] < 25 else level_req
    level_span = max(0, next_level_req - level_req)
    level_current = max(0, profile["total_time"] - level_req)
    level_bar, level_percent = progress_bar(level_current, level_span)

    division_req = Config.get_division_requirement(min(profile["division"], 5))
    next_division_req = Config.get_division_requirement(profile["division"] + 1) if profile["division"] < 5 else None
    if next_division_req:
        division_bar, division_percent = progress_bar(
            profile["season_time"] - division_req,
            next_division_req - division_req,
        )
    else:
        division_bar, division_percent = progress_bar(1, 1)

    embed = discord.Embed(
        title=f"Status von {profile['name']}",
        color=discord.Color.orange(),
    )
    embed.set_author(name=member.display_name, icon_url=avatar_url(member))
    embed.add_field(name="Rang", value=f"{level_label(profile['level'])} · #{profile['rank']} von {profile['total_users']}", inline=True)
    embed.add_field(name="Gesamtzeit", value=format_minutes_short(profile["total_time"]), inline=True)
    embed.add_field(name="Season", value=f"{division_label(profile['division'])} · {format_minutes_short(profile['season_time'])}", inline=True)

    if profile["time_to_next_level"] is None:
        level_value = "Maximales Level erreicht."
    else:
        level_value = f"`{level_bar}` {level_percent}%\nNoch {format_minutes_short(profile['time_to_next_level'])}"
    embed.add_field(name="Nächstes Level", value=level_value, inline=False)

    if profile["time_to_next_division"] is None:
        division_value = "Maximale Division erreicht."
    elif profile["division"] == 5:
        division_value = f"`{division_bar}` {division_percent}%\nNoch {format_minutes_short(profile['time_to_next_division'])} bis Phönix / Top 10"
    else:
        division_value = f"`{division_bar}` {division_percent}%\nNoch {format_minutes_short(profile['time_to_next_division'])}"
    embed.add_field(name="Nächste Division", value=division_value, inline=False)

    ttt = profile.get("ttt_stats") or {}
    if ttt:
        ttt_value = (
            f"{int(ttt.get('rounds_played', 0))} Runden · "
            f"{int(ttt.get('rounds_won', 0))} Siege · "
            f"{int(ttt.get('kills', 0))} Kills"
        )
    elif profile.get("steam_id"):
        ttt_value = "Noch keine TTT-Statistiken."
    else:
        ttt_value = "Steam noch nicht über die Website verknüpft."
    embed.add_field(name="TTT", value=ttt_value, inline=False)
    embed.set_footer(text=f"Profil: {Config.SITE_URL}/profile")
    return embed


def build_ttt_embed(profile, member):
    if profile["state"] != "ok":
        return unavailable_embed(member, profile["state"])

    embed = discord.Embed(title=f"TTT-Statistiken von {profile['name']}", color=discord.Color.red())
    embed.set_author(name=member.display_name, icon_url=avatar_url(member))

    if not profile.get("steam_id"):
        embed.description = "Steam ist noch nicht über die Website verknüpft. TTT-Statistiken können deshalb nicht zugeordnet werden."
        return embed

    ttt = profile.get("ttt_stats") or {}
    if not ttt or int(ttt.get("rounds_played", 0) or 0) <= 0:
        embed.description = "Für diesen Steam-Account wurden noch keine TTT-Runden erfasst."
        return embed

    rounds_played = int(ttt.get("rounds_played", 0) or 0)
    rounds_won = int(ttt.get("rounds_won", 0) or 0)
    kills = int(ttt.get("kills", 0) or 0)
    deaths = int(ttt.get("deaths", 0) or 0)
    kd = _safe_ratio(kills, deaths)
    win_rate = _safe_ratio(rounds_won, rounds_played) * 100
    achievements = build_ttt_achievement_payload(ttt)

    embed.add_field(name="Runden", value=f"{rounds_played} gespielt\n{rounds_won} gewonnen ({win_rate:.1f}%)", inline=True)
    embed.add_field(name="Kills", value=f"{kills} Kills\n{deaths} Tode\nK/D {kd:.2f}", inline=True)
    embed.add_field(
        name="Rollensiege",
        value=(
            f"Innocent: {int(ttt.get('innocent_wins', 0) or 0)}\n"
            f"Detective: {int(ttt.get('detective_wins', 0) or 0)}\n"
            f"Traitor: {int(ttt.get('traitor_wins', 0) or 0)}"
        ),
        inline=True,
    )
    embed.add_field(name="TTT-Erfolge", value=f"{achievements['achievement_level']} / 12 Stufen", inline=True)
    embed.add_field(name="Letzte Runde", value=format_datetime(ttt.get("last_played_at")), inline=True)
    if ttt.get("last_ttt_name"):
        embed.add_field(name="Letzter TTT-Name", value=str(ttt["last_ttt_name"])[:1024], inline=True)
    return embed


def build_achievements_embed(profile, member):
    if profile["state"] != "ok":
        return unavailable_embed(member, profile["state"])

    summary = profile["achievement_summary"]
    ttt_payload = build_ttt_achievement_payload(profile.get("ttt_stats") or {})
    total = (
        summary["streak"]
        + summary["logins"]
        + summary["time"]
        + summary["heatmap"]
        + summary["division"]
        + summary["old_member"]
        + summary["legacy_supporter"]
        + summary["apex"]
        + ttt_payload["achievement_level"]
    )

    embed = discord.Embed(title=f"Erfolge von {profile['name']}", color=discord.Color.gold())
    embed.set_author(name=member.display_name, icon_url=avatar_url(member))
    embed.add_field(name="Gesamt", value=f"{total} freigeschaltete Stufen", inline=False)
    embed.add_field(name="Streak", value=f"{summary['streak']} / 4\nLängster: {summary['longest_streak']} Tage", inline=True)
    embed.add_field(name="Logins", value=f"{summary['logins']} / 4\nGesamt: {summary['total_logins']}", inline=True)
    embed.add_field(name="Spielzeit", value=f"{summary['time']} / 4\n{format_minutes_short(profile['total_time'])}", inline=True)
    embed.add_field(name="Aktivität", value=f"{summary['heatmap']} / 4\n{summary['active_days']} aktive Tage", inline=True)
    embed.add_field(name="Season", value=f"{division_label(summary['division']) if summary['division'] else 'Keine'}", inline=True)
    embed.add_field(name="TTT", value=f"{ttt_payload['achievement_level']} / 12", inline=True)
    return embed


def build_profile_embed(member, private=False):
    embed = discord.Embed(
        title="FirePhenix Profil",
        description=(
            f"Profil, Account-Verknüpfung und Belohnungen verwaltest du auf {Config.SITE_URL}/profile."
        ),
        color=discord.Color.orange(),
    )
    embed.set_author(name=member.display_name, icon_url=avatar_url(member))
    if not private:
        embed.set_footer(text="Tipp: Nutze /profil privat:True, wenn nur du den Link sehen sollst.")
    return embed


def build_help_embed():
    embed = discord.Embed(
        title="Ember Hilfe",
        description="Nützliche FirePhenix-Kommandos direkt in Discord.",
        color=discord.Color.orange(),
    )
    embed.add_field(
        name="/status [spieler] [privat]",
        value="Rang, Spielzeit, Level-Fortschritt, Season-Fortschritt und TTT-Kurzstatus.",
        inline=False,
    )
    embed.add_field(
        name="/ttt [spieler] [privat]",
        value="TTT-Runden, Siege, Kills, K/D und TTT-Erfolge.",
        inline=False,
    )
    embed.add_field(
        name="/achievements [spieler] [privat]",
        value="Übersicht über FirePhenix-Erfolge.",
        inline=False,
    )
    embed.add_field(
        name="/server",
        value="TTT-Serverstatus, Map, Spielerzahl und Verbindungsdaten.",
        inline=False,
    )
    embed.add_field(
        name="/links",
        value="Website, Profil, Discord, TeamSpeak, TTT und Support.",
        inline=False,
    )
    embed.add_field(
        name="Rechtsklick auf Nutzer",
        value="Apps -> View FirePhenix Status zeigt den Status eines Spielers ohne Tippen.",
        inline=False,
    )
    embed.set_footer(text="Mit privat:True siehst nur du die Antwort.")
    return embed


def build_links_embed():
    embed = discord.Embed(
        title="FirePhenix Links",
        color=discord.Color.orange(),
    )
    embed.add_field(name="Website", value=Config.SITE_URL, inline=False)
    embed.add_field(name="Profil", value=f"{Config.SITE_URL}/profile", inline=False)
    embed.add_field(name="Discord", value=DISCORD_INVITE_URL, inline=False)
    embed.add_field(name="TeamSpeak 3", value=TEAMSPEAK_PUBLIC_HOST, inline=True)
    embed.add_field(name="TTT", value=f"{ttt_server_address()}\nPasswort: {TTT_PASSWORD}", inline=True)
    embed.add_field(name="Support", value="Discord-Team, TeamSpeak-Support oder admin@firephenix.de", inline=False)
    return embed


def build_server_embed(payload=None, error=None):
    embed = discord.Embed(
        title="FirePhenix Server",
        color=discord.Color.green() if payload and payload.get("status") == "online" else discord.Color.dark_gray(),
    )
    embed.add_field(name="TeamSpeak 3", value=TEAMSPEAK_PUBLIC_HOST, inline=True)
    embed.add_field(name="TTT Adresse", value=ttt_server_address(), inline=True)
    embed.add_field(name="TTT Passwort", value=TTT_PASSWORD, inline=True)

    if payload and payload.get("status") == "online":
        players = payload.get("players") or {}
        current_players = players.get("current", payload.get("current_players", 0))
        max_players = players.get("max", payload.get("max_players", 0))
        embed.add_field(name="TTT Status", value="Online", inline=True)
        embed.add_field(name="Map", value=payload.get("current_map") or payload.get("map") or "Unbekannt", inline=True)
        embed.add_field(name="Spieler", value=f"{current_players}/{max_players}", inline=True)
        if payload.get("latency_ms") is not None:
            embed.add_field(name="Latenz", value=f"{payload['latency_ms']} ms", inline=True)
        if payload.get("name"):
            embed.set_footer(text=str(payload["name"])[:2048])
        return embed

    if isinstance(error, SourceServerTimeout):
        status_text = "Offline oder keine Antwort vom Server."
    elif error:
        status_text = "Status konnte gerade nicht abgefragt werden."
    else:
        status_text = "Status nicht abgefragt."
    embed.add_field(name="TTT Status", value=status_text, inline=False)
    return embed


class UtilityCommands(commands.Cog):
    def __init__(self, bot, service=None, server_query=query_source_server):
        self.bot = bot
        self.service = service or DiscordProfileService()
        self.server_query = server_query
        self.view_firephenix_status_menu = app_commands.ContextMenu(
            name="View FirePhenix Status",
            callback=self.view_firephenix_status,
            guild_ids=[Config.DISCORD_GUILD_ID],
        )
        if self.bot and getattr(self.bot, "tree", None):
            self.bot.tree.add_command(
                self.view_firephenix_status_menu,
                guild=discord.Object(id=Config.DISCORD_GUILD_ID),
                override=True,
            )

    async def _send_embed(self, interaction, embed, private):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=private)
        await interaction.followup.send(
            embed=embed,
            ephemeral=private,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def _profile_for(self, interaction, spieler):
        member = spieler or interaction.user
        return member, self.service.get_profile(member.id)

    async def _query_ttt_server(self):
        if inspect.iscoroutinefunction(self.server_query):
            return await self.server_query(
                Config.TTT_STATUS_HOST,
                Config.TTT_STATUS_PORT,
                timeout_seconds=Config.TTT_STATUS_TIMEOUT_SECONDS,
            )
        return await asyncio.to_thread(
            self.server_query,
            Config.TTT_STATUS_HOST,
            Config.TTT_STATUS_PORT,
            timeout_seconds=Config.TTT_STATUS_TIMEOUT_SECONDS,
        )

    @app_commands.command(name="status", description="Zeigt deinen FirePhenix-Rang und Fortschritt.")
    @app_commands.guilds(discord.Object(id=Config.DISCORD_GUILD_ID))
    async def status(self, interaction: discord.Interaction, spieler: discord.Member | None = None, privat: bool = False):
        member, profile = await self._profile_for(interaction, spieler)
        await self._send_embed(interaction, build_status_embed(profile, member), privat)

    @app_commands.command(name="ttt", description="Zeigt deine TTT-Statistiken.")
    @app_commands.guilds(discord.Object(id=Config.DISCORD_GUILD_ID))
    async def ttt(self, interaction: discord.Interaction, spieler: discord.Member | None = None, privat: bool = False):
        member, profile = await self._profile_for(interaction, spieler)
        await self._send_embed(interaction, build_ttt_embed(profile, member), privat)

    @app_commands.command(name="achievements", description="Zeigt deine FirePhenix-Erfolge.")
    @app_commands.guilds(discord.Object(id=Config.DISCORD_GUILD_ID))
    async def erfolge(self, interaction: discord.Interaction, spieler: discord.Member | None = None, privat: bool = False):
        member, profile = await self._profile_for(interaction, spieler)
        await self._send_embed(interaction, build_achievements_embed(profile, member), privat)

    @app_commands.command(name="profil", description="Zeigt den Link zu deinem FirePhenix-Profil.")
    @app_commands.guilds(discord.Object(id=Config.DISCORD_GUILD_ID))
    async def profil(self, interaction: discord.Interaction, privat: bool = False):
        await self._send_embed(interaction, build_profile_embed(interaction.user, privat), privat)

    @app_commands.command(name="help", description="Zeigt die wichtigsten Ember-Kommandos.")
    @app_commands.guilds(discord.Object(id=Config.DISCORD_GUILD_ID))
    async def help(self, interaction: discord.Interaction, privat: bool = False):
        await self._send_embed(interaction, build_help_embed(), privat)

    @app_commands.command(name="links", description="Zeigt wichtige FirePhenix-Links und Adressen.")
    @app_commands.guilds(discord.Object(id=Config.DISCORD_GUILD_ID))
    async def links(self, interaction: discord.Interaction, privat: bool = False):
        await self._send_embed(interaction, build_links_embed(), privat)

    @app_commands.command(name="server", description="Zeigt FirePhenix Serverstatus und Verbindungsdaten.")
    @app_commands.guilds(discord.Object(id=Config.DISCORD_GUILD_ID))
    async def server(self, interaction: discord.Interaction, privat: bool = False):
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=privat)

        try:
            payload = await self._query_ttt_server()
            embed = build_server_embed(payload=payload)
        except (SourceServerTimeout, SourceServerQueryError) as error:
            embed = build_server_embed(error=error)

        await interaction.followup.send(
            embed=embed,
            ephemeral=privat,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def view_firephenix_status(self, interaction: discord.Interaction, member: discord.Member):
        profile = self.service.get_profile(member.id)
        await self._send_embed(interaction, build_status_embed(profile, member), False)
