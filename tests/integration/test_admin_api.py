"""Admin endpoints against the real database, verifying actual row changes."""

import json
import unittest

from . import harness
from .harness import (
    IntegrationTestCase,
    seed_time,
    seed_user,
    skip_unless_integration,
)


@skip_unless_integration
class AdminGuardTests(IntegrationTestCase):
    def test_anonymous_request_is_unauthorized(self):
        response = self.client.post("/api/admin/players/1/time", json={})
        self.assertEqual(response.status_code, 401)

    def test_non_admin_session_is_forbidden(self):
        headers = harness.login(self.client, "76561198099999999")
        response = self.client.post(
            "/api/admin/players/1/time", json={}, headers=headers
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_without_csrf_is_rejected(self):
        harness.admin_session(self.client)
        response = self.client.post("/api/admin/players/1/time", json={})
        self.assertEqual(response.status_code, 403)


@skip_unless_integration
class AdminTimeUpdateTests(IntegrationTestCase):
    def test_time_update_writes_rows_and_audit_log(self):
        user_id = seed_user(self.db, discord_id="d-target", name="Target")
        seed_time(self.db, platform="discord", platform_uid="d-target",
                  total_time=1000, daily_time=200, weekly_time=800,
                  monthly_time=900, season_time=500)
        headers = harness.admin_session(self.client)

        response = self.client.post(
            f"/api/admin/players/{user_id}/time",
            json={
                "platform": "discord",
                "total_time": 600,
                "season_time": 300,
                "reason": "integration test correction",
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertTrue(body["ok"])

        row = self.fetch_all(
            """
            SELECT total_time, daily_time, weekly_time, monthly_time, season_time
            FROM time WHERE platform = 'discord' AND platform_uid = 'd-target'
            """
        )[0]
        # daily/weekly/monthly must be capped at the new, lower total.
        self.assertEqual(tuple(row), (600, 200, 600, 600, 300))

        audit_rows = self.fetch_all(
            "SELECT admin_steam_id, action, result_status, summary FROM admin_audit_log"
        )
        self.assertEqual(len(audit_rows), 1)
        admin_steam_id, action, result_status, summary = audit_rows[0]
        self.assertEqual(admin_steam_id, harness.ADMIN_STEAM_ID)
        self.assertEqual(action, "ranking_time_update")
        self.assertEqual(result_status, "success")
        self.assertEqual(json.loads(summary)["reason"],
                         "integration test correction")

    def test_time_update_recalculates_level(self):
        user_id = seed_user(self.db, discord_id="d-level", name="Leveler",
                            level=1)
        seed_time(self.db, platform="discord", platform_uid="d-level",
                  total_time=10)
        headers = harness.admin_session(self.client)

        response = self.client.post(
            f"/api/admin/players/{user_id}/time",
            json={
                "platform": "discord",
                "total_time": 700,
                "season_time": 0,
                "reason": "level recalculation test",
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 200)

        # 700 minutes -> level 3 (600 <= 700 < 1200) per Config.LEVEL_REQUIREMENTS.
        rows = self.fetch_all("SELECT level FROM user WHERE id = ?", (user_id,))
        self.assertEqual(rows[0][0], 3)

    def test_time_update_rejects_season_time_above_total_and_audits_failure(self):
        user_id = seed_user(self.db, discord_id="d-invalid", name="Invalid")
        headers = harness.admin_session(self.client)

        response = self.client.post(
            f"/api/admin/players/{user_id}/time",
            json={
                "platform": "discord",
                "total_time": 100,
                "season_time": 200,
                "reason": "should fail",
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 400)

        statuses = [row[0] for row in self.fetch_all(
            "SELECT result_status FROM admin_audit_log")]
        self.assertEqual(statuses, ["failed"])

    def test_time_update_unknown_user_returns_404(self):
        headers = harness.admin_session(self.client)
        response = self.client.post(
            "/api/admin/players/424242/time",
            json={
                "platform": "discord",
                "total_time": 100,
                "season_time": 0,
                "reason": "missing user",
            },
            headers=headers,
        )
        self.assertEqual(response.status_code, 404)


@skip_unless_integration
class AdminSpecialAchievementTests(IntegrationTestCase):
    def test_grant_and_revoke_roundtrip(self):
        user_id = seed_user(self.db, discord_id="d-ach", name="Achiever")
        headers = harness.admin_session(self.client)

        grant = self.client.post(
            "/api/admin/special-achievements/grant",
            json={
                "user_id": user_id,
                "platform": "discord",
                "achievement_type": 1,
                "reason": "integration grant",
            },
            headers=headers,
        )
        self.assertEqual(grant.status_code, 200, grant.get_json())

        self.assertEqual(
            self.fetch_all(
                "SELECT platform, platform_id, achievement_type FROM special_achievements"
            ),
            [("discord", "d-ach", 1)],
        )

        revoke = self.client.post(
            "/api/admin/special-achievements/revoke",
            json={
                "user_id": user_id,
                "platform": "discord",
                "achievement_type": 1,
                "reason": "integration revoke",
            },
            headers=headers,
        )
        self.assertEqual(revoke.status_code, 200, revoke.get_json())

        self.assertEqual(
            self.fetch_all("SELECT COUNT(*) FROM special_achievements")[0][0], 0
        )

        audit = self.client.get("/api/admin/audit-log", headers=headers)
        self.assertEqual(audit.status_code, 200)
        actions = [entry["action"] for entry in audit.get_json()["entries"]]
        self.assertIn("special_achievement_grant", actions)
        self.assertIn("special_achievement_revoke", actions)

