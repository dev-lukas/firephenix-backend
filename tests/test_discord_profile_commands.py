import types
import unittest

from app.rankingsystem.bots.discord import profile_commands
from app.rankingsystem.bots.discord.profile_commands import (
    DiscordProfileService,
    UtilityCommands,
    build_help_embed,
    build_links_embed,
    build_server_embed,
    build_status_embed,
    build_ttt_embed,
    division_label,
    format_minutes_short,
    level_label,
    progress_bar,
)


class FakeDatabase:
    def __init__(self):
        self.closed = False

    def execute_query(self, query, params=None):
        if "WITH ranked_users" in query:
            return [(
                7,
                "Lukas",
                "10",
                "ts-id",
                "76561198000000000",
                4,
                2,
                0,
                370,
                10,
                120,
                240,
                2500,
                300,
                70,
                3,
                42,
            )]
        if "WHERE u.division = 6" in query:
            return [(10, 4500)]
        if "FROM special_achievements" in query:
            return [(1001,), (1002,), (200,)]
        if "FROM login_streak" in query:
            return [(30, 7)]
        if "FROM activity_heatmap" in query:
            return [(5, 9)]
        raise AssertionError(f"Unexpected query: {query}")

    def get_ttt_player_stats(self, steam_id):
        return {
            "steam_id": steam_id,
            "last_ttt_name": "TTT Lukas",
            "rounds_played": 50,
            "rounds_won": 10,
            "innocent_wins": 6,
            "detective_wins": 1,
            "traitor_wins": 3,
            "kills": 25,
            "deaths": 5,
            "last_played_at": None,
        }

    def close(self):
        self.closed = True


class MissingDatabase:
    def execute_query(self, query, params=None):
        return []

    def close(self):
        pass


class FakeMember:
    def __init__(self, user_id=10, display_name="Lukas"):
        self.id = user_id
        self.display_name = display_name
        self.display_avatar = types.SimpleNamespace(url="https://example.test/avatar.png")


class FakeResponse:
    def __init__(self):
        self.deferred = False
        self.ephemeral = None

    def is_done(self):
        return self.deferred

    async def defer(self, ephemeral=False):
        self.deferred = True
        self.ephemeral = ephemeral


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, **kwargs):
        self.sent.append(kwargs)


class FakeInteraction:
    def __init__(self, user=None):
        self.user = user or FakeMember()
        self.response = FakeResponse()
        self.followup = FakeFollowup()


class StaticService:
    def __init__(self, profile):
        self.profile = profile
        self.calls = []

    def get_profile(self, discord_id):
        self.calls.append(discord_id)
        return self.profile


class ProfileCommandFormattingTests(unittest.TestCase):
    def test_short_time_and_rank_labels(self):
        self.assertEqual(format_minutes_short(65), "1h 5m")
        self.assertEqual(format_minutes_short(60), "1h")
        self.assertEqual(format_minutes_short(5), "5m")
        self.assertEqual(level_label(4), "Level 4")
        self.assertEqual(level_label(22), "Prestige 2")
        self.assertEqual(division_label(6), "Phönix")

    def test_progress_bar_caps_at_full(self):
        bar, percent = progress_bar(15, 10, width=5)

        self.assertEqual(bar, "█████")
        self.assertEqual(percent, 100)

    def test_service_builds_profile_from_database(self):
        service = DiscordProfileService(db_factory=FakeDatabase)

        profile = service.get_profile(10)

        self.assertEqual(profile["state"], "ok")
        self.assertEqual(profile["name"], "Lukas")
        self.assertEqual(profile["rank"], 3)
        self.assertEqual(profile["time_to_next_level"], 2030)
        self.assertEqual(profile["time_to_next_division"], 500)
        self.assertEqual(profile["achievement_summary"]["division"], 2)
        self.assertEqual(profile["ttt_stats"]["kills"], 25)

    def test_service_reports_missing_user(self):
        service = DiscordProfileService(db_factory=MissingDatabase)

        self.assertEqual(service.get_profile(10), {"state": "missing"})

    def test_status_embed_contains_focused_progress(self):
        profile = DiscordProfileService(db_factory=FakeDatabase).get_profile(10)

        embed = build_status_embed(profile, FakeMember())

        field_names = [field.name for field in embed.fields]
        self.assertIn("Nächstes Level", field_names)
        self.assertIn("Nächste Division", field_names)
        self.assertIn("TTT", field_names)
        self.assertIn("#3 von 42", embed.fields[0].value)

    def test_ttt_embed_handles_missing_steam_link(self):
        profile = DiscordProfileService(db_factory=FakeDatabase).get_profile(10)
        profile["steam_id"] = None
        profile["ttt_stats"] = None

        embed = build_ttt_embed(profile, FakeMember())

        self.assertIn("Steam ist noch nicht", embed.description)

    def test_help_embed_lists_new_utility_commands(self):
        embed = build_help_embed()
        field_names = [field.name for field in embed.fields]

        self.assertIn("/server", field_names)
        self.assertIn("/links", field_names)
        self.assertTrue(any("View FirePhenix Status" in field.value for field in embed.fields))

    def test_links_embed_contains_public_addresses(self):
        embed = build_links_embed()
        values = "\n".join(field.value for field in embed.fields)

        self.assertIn(profile_commands.Config.SITE_URL, values)
        self.assertIn("firephenix.de", values)
        self.assertIn("Passwort: ember", values)

    def test_server_embed_formats_online_status(self):
        embed = build_server_embed({
            "status": "online",
            "name": "FirePhenix TTT",
            "current_map": "ttt_rooftops",
            "players": {"current": 4, "max": 16},
            "latency_ms": 23,
        })
        values = "\n".join(field.value for field in embed.fields)

        self.assertIn("Online", values)
        self.assertIn("ttt_rooftops", values)
        self.assertIn("4/16", values)


class UtilityCommandTests(unittest.IsolatedAsyncioTestCase):
    async def test_status_command_defaults_to_public_response(self):
        profile = DiscordProfileService(db_factory=FakeDatabase).get_profile(10)
        cog = UtilityCommands(bot=None, service=StaticService(profile))
        interaction = FakeInteraction()

        await cog.status.callback(cog, interaction)

        self.assertFalse(interaction.response.ephemeral)
        self.assertFalse(interaction.followup.sent[0]["ephemeral"])
        self.assertEqual(cog.service.calls, [10])

    async def test_status_command_can_send_private_lookup(self):
        profile = DiscordProfileService(db_factory=FakeDatabase).get_profile(10)
        cog = UtilityCommands(bot=None, service=StaticService(profile))
        interaction = FakeInteraction(user=FakeMember(10, "Requester"))
        other = FakeMember(11, "Other")

        await cog.status.callback(cog, interaction, spieler=other, privat=True)

        self.assertTrue(interaction.response.ephemeral)
        self.assertTrue(interaction.followup.sent[0]["ephemeral"])
        self.assertEqual(cog.service.calls, [11])

    async def test_profile_command_only_points_to_website(self):
        cog = UtilityCommands(bot=None, service=StaticService({"state": "missing"}))
        interaction = FakeInteraction()

        await cog.profil.callback(cog, interaction)

        embed = interaction.followup.sent[0]["embed"]
        self.assertIn(profile_commands.Config.SITE_URL, embed.description)
        self.assertEqual(cog.service.calls, [])

    async def test_help_command_defaults_to_public_response(self):
        cog = UtilityCommands(bot=None, service=StaticService({"state": "missing"}))
        interaction = FakeInteraction()

        await cog.help.callback(cog, interaction)

        self.assertFalse(interaction.response.ephemeral)
        embed = interaction.followup.sent[0]["embed"]
        self.assertEqual(embed.title, "Ember Hilfe")
        self.assertEqual(cog.service.calls, [])

    async def test_links_command_can_send_private_response(self):
        cog = UtilityCommands(bot=None, service=StaticService({"state": "missing"}))
        interaction = FakeInteraction()

        await cog.links.callback(cog, interaction, privat=True)

        self.assertTrue(interaction.response.ephemeral)
        self.assertTrue(interaction.followup.sent[0]["ephemeral"])
        self.assertEqual(cog.service.calls, [])

    async def test_server_command_uses_injected_query(self):
        async def fake_query(host, port, timeout_seconds=2):
            return {
                "status": "online",
                "name": "FirePhenix TTT",
                "current_map": "ttt_rooftops",
                "players": {"current": 4, "max": 16},
                "latency_ms": 23,
            }

        cog = UtilityCommands(bot=None, service=StaticService({"state": "missing"}), server_query=fake_query)
        interaction = FakeInteraction()

        await cog.server.callback(cog, interaction)

        embed = interaction.followup.sent[0]["embed"]
        values = "\n".join(field.value for field in embed.fields)
        self.assertIn("ttt_rooftops", values)
        self.assertEqual(cog.service.calls, [])

    async def test_user_context_command_shows_selected_member_status(self):
        profile = DiscordProfileService(db_factory=FakeDatabase).get_profile(10)
        cog = UtilityCommands(bot=None, service=StaticService(profile))
        interaction = FakeInteraction(user=FakeMember(10, "Requester"))
        target = FakeMember(11, "Target")

        await cog.view_firephenix_status(interaction, target)

        self.assertFalse(interaction.response.ephemeral)
        self.assertEqual(cog.service.calls, [11])


if __name__ == "__main__":
    unittest.main()
