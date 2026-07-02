"""Game-server commands and skin redemption over the real Valkey channel.

A FakeGameServerResponder thread plays the role of the game-server manager,
so the full publish/response handshake and the database side effects are
exercised for real.
"""

import unittest
from unittest.mock import patch

from app.config import Config

from . import harness
from .harness import (
    FakeGameServerResponder,
    IntegrationTestCase,
    seed_special_achievement,
    seed_user,
    skip_unless_integration,
)

STEAM_ID = 76561198012345678


@skip_unless_integration
class GameServerStatusTests(IntegrationTestCase):
    def test_status_reports_offline_when_server_unreachable(self):
        # Point the public A2S status query at a closed local port so the
        # test never touches the production game server.
        with patch.object(Config, "TTT_STATUS_HOST", "127.0.0.1"), \
                patch.object(Config, "TTT_STATUS_PORT", 9), \
                patch.object(Config, "TTT_STATUS_TIMEOUT_SECONDS", 0.2):
            response = self.client.get("/api/gameservers/ttt/status")
        self.assertEqual(response.status_code, 200)
        body = response.get_json()
        self.assertFalse(body["ok"])
        self.assertIn(body["status"], ("offline", "unknown"))
        self.assertEqual(body["server"], "ttt")

    def test_restart_requires_admin(self):
        harness.login(self.client, "76561198099999999", with_csrf=True)
        response = self.client.post(
            "/api/gameservers/ttt/restart",
            headers={"X-CSRF-Token": "integration-test-csrf-token"},
        )
        self.assertEqual(response.status_code, 403)

    def test_admin_can_restart_via_manager(self):
        headers = harness.admin_session(self.client)
        responder = FakeGameServerResponder(response={"ok": True}).start()
        try:
            response = self.client.post(
                "/api/gameservers/ttt/restart", headers=headers
            )
        finally:
            responder.stop()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(responder.received[0]["command"], "restart")


@skip_unless_integration
class SkinRedemptionTests(IntegrationTestCase):
    def redeem(self, tier=3, season=1):
        headers = harness.login(self.client, STEAM_ID)
        return self.client.post(
            "/api/user/profile/skins",
            json={"platform": "garrysmod", "tier": tier, "season": season},
            headers=headers,
        )

    def seed_eligible_user(self, division=3):
        seed_user(self.db, steam_id=STEAM_ID, discord_id="d-skin",
                  name="SkinFan")
        # Season 1 division achievements start at type 1001 (division 1).
        seed_special_achievement(
            self.db, platform="discord", platform_id="d-skin",
            achievement_type=1000 + division,
        )

    def unlockable_rows(self):
        return self.fetch_all(
            "SELECT steam_id, platform, unlockable_type FROM unlockables"
        )

    def test_successful_redemption_persists_unlockable(self):
        self.seed_eligible_user(division=3)
        responder = FakeGameServerResponder(response={"ok": True}).start()
        try:
            response = self.redeem(tier=3)
        finally:
            responder.stop()

        self.assertEqual(response.status_code, 200, response.get_json())
        self.assertEqual(self.unlockable_rows(),
                         [(STEAM_ID, "gameserver", 13)])
        grant = responder.received[0]
        self.assertEqual(grant["command"], "grant_season_skin")
        self.assertEqual(grant["steam_id64"], str(STEAM_ID))
        self.assertEqual(grant["tier"], 3)

    def test_second_redemption_is_rejected(self):
        self.seed_eligible_user(division=3)
        responder = FakeGameServerResponder(response={"ok": True}).start()
        try:
            first = self.redeem(tier=3)
            second = self.redeem(tier=3)
        finally:
            responder.stop()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 400)
        self.assertEqual(len(self.unlockable_rows()), 1)

    def test_ttt_error_leaves_no_unlockable_row(self):
        self.seed_eligible_user(division=3)
        responder = FakeGameServerResponder(
            response={"ok": False, "error": "player_offline"}
        ).start()
        try:
            response = self.redeem(tier=3)
        finally:
            responder.stop()

        self.assertEqual(response.status_code, 409)
        self.assertEqual(self.unlockable_rows(), [])

    def test_insufficient_division_is_rejected_without_ttt_call(self):
        self.seed_eligible_user(division=2)
        responder = FakeGameServerResponder(response={"ok": True}).start()
        try:
            response = self.redeem(tier=5)
        finally:
            responder.stop()

        self.assertEqual(response.status_code, 400)
        self.assertEqual(responder.received, [])
        self.assertEqual(self.unlockable_rows(), [])

    def test_unlinked_account_is_rejected(self):
        responder = FakeGameServerResponder(response={"ok": True}).start()
        try:
            response = self.redeem(tier=3)
        finally:
            responder.stop()
        self.assertEqual(response.status_code, 404)

