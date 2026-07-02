"""Ranking and online-user endpoints against real MariaDB and Valkey."""

import json
import unittest

from .harness import (
    IntegrationTestCase,
    seed_time,
    seed_user,
    skip_unless_integration,
)


@skip_unless_integration
class RankingApiTests(IntegrationTestCase):
    def seed_ranked_players(self):
        seed_user(self.db, discord_id="100000001", name="Alpha")
        seed_time(self.db, platform="discord", platform_uid="100000001",
                  total_time=900, weekly_time=10)
        seed_user(self.db, teamspeak_id="t-bravo", name="Bravo")
        seed_time(self.db, platform="teamspeak", platform_uid="t-bravo",
                  total_time=500, weekly_time=400)
        seed_user(self.db, discord_id="100000003", name="Charlie")
        seed_time(self.db, platform="discord", platform_uid="100000003",
                  total_time=100, weekly_time=90)
        seed_user(self.db, discord_id="100000009", name="Ghost",
                  ranking_disabled=1)
        seed_time(self.db, platform="discord", platform_uid="100000009",
                  total_time=99999, weekly_time=99999)

    def test_top_ranking_orders_by_total_time_and_hides_disabled(self):
        self.seed_ranked_players()
        response = self.client.get("/api/ranking/top")
        self.assertEqual(response.status_code, 200)
        players = response.get_json()
        self.assertEqual([p["name"] for p in players],
                         ["Alpha", "Bravo", "Charlie"])
        self.assertEqual(players[0]["minutes"], 900)

    def test_top_ranking_weekly_period_changes_order(self):
        self.seed_ranked_players()
        players = self.client.get("/api/ranking/top?period=weekly").get_json()
        self.assertEqual([p["name"] for p in players],
                         ["Bravo", "Charlie", "Alpha"])

    def test_ranking_pagination_and_search(self):
        self.seed_ranked_players()
        page1 = self.client.get("/api/ranking?page=1&limit=2").get_json()
        self.assertEqual(page1["pages"], 2)
        self.assertEqual(page1["total"], 3)
        self.assertEqual(len(page1["players"]), 2)
        self.assertEqual(page1["players"][0]["rank"], 1)

        page2 = self.client.get("/api/ranking?page=2&limit=2").get_json()
        self.assertEqual(len(page2["players"]), 1)
        self.assertEqual(page2["players"][0]["name"], "Charlie")

        searched = self.client.get("/api/ranking?search=rav").get_json()
        self.assertEqual([p["name"] for p in searched["players"]], ["Bravo"])

    def test_ranking_rejects_invalid_pagination_args(self):
        response = self.client.get("/api/ranking?page=0")
        self.assertEqual(response.status_code, 400)

    def test_stats_aggregates_seeded_data(self):
        self.seed_ranked_players()
        stats = self.client.get("/api/ranking/stats").get_json()
        self.assertEqual(stats["total_users"], 3)
        # SUM() comes back as Decimal and Flask serializes it as a string;
        # compare numerically so the contract (the value) is what is pinned.
        self.assertEqual(int(stats["total_time"]), 1500)

    def test_online_users_resolves_names_from_database(self):
        from app.utils.valkey_manager import ValkeyManager

        seed_user(self.db, discord_id="111", name="OnlineGuy")
        seed_user(self.db, discord_id="222", name="OfflineGuy")
        manager = ValkeyManager()
        manager.valkey.set("discord:online_users", json.dumps(["111"]), ex=30)
        try:
            response = self.client.get("/api/user/online?platform=discord")
            self.assertEqual(response.status_code, 200)
            users = response.get_json()["users"]
            self.assertEqual([u["name"] for u in users], ["OnlineGuy"])
        finally:
            manager.valkey.delete("discord:online_users")

    def test_online_users_rejects_unknown_platform(self):
        response = self.client.get("/api/user/online?platform=icq")
        self.assertEqual(response.status_code, 400)

