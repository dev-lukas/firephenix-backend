"""Integration tests for the TS3->TS6 identity bridge (Layer 0: myTeamSpeak id).

Run against a real MariaDB via scripts/run-integration-tests.sh. They exercise
the actual SQL in AsyncDatabaseManager.capture_myteamspeak_id / find_user_by_
myteamspeak_id / merge_teamspeak_identity / recognize_teamspeak_client (the
bridge lives on the bot's async manager; the sync manager only seeds/inspects).
"""

import asyncio
import unittest

from app.utils.async_database import AsyncDatabaseManager

from tests.integration.harness import (
    skip_unless_integration,
    open_database,
    reset_database,
    seed_user,
    seed_time,
    seed_special_achievement,
)


@skip_unless_integration
class IdentityBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = open_database()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        reset_database(self.db)

    def _bridge(self, method, *args, **kwargs):
        """Run one AsyncDatabaseManager method to completion (fresh pool per
        call: asyncio.run creates a new loop each time)."""
        async def go():
            adb = AsyncDatabaseManager()
            try:
                return await getattr(adb, method)(*args, **kwargs)
            finally:
                await adb.close()
        return asyncio.run(go())

    def _user_row(self, user_id):
        self.db.cursor.execute(
            "SELECT teamspeak_id, teamspeak6_id, myteamspeak_id, level FROM user WHERE id = %s",
            (user_id,))
        return self.db.cursor.fetchone()

    def _time_total(self, uid):
        self.db.cursor.execute(
            "SELECT total_time FROM time WHERE platform='teamspeak' AND platform_uid = %s",
            (uid,))
        row = self.db.cursor.fetchone()
        return row[0] if row else None

    # --- capture -------------------------------------------------------- #
    def test_capture_backfills_myteamspeak_id(self):
        uid = seed_user(self.db, teamspeak_id="TS3-UID", name="Alice")
        updated = self._bridge("capture_myteamspeak_id", "TS3-UID", "MYTSID-ALICE")
        self.assertTrue(updated)
        self.assertEqual(self._user_row(uid)[2], "MYTSID-ALICE")

    def test_capture_noop_when_empty(self):
        seed_user(self.db, teamspeak_id="TS3-UID", name="Alice")
        self.assertFalse(self._bridge("capture_myteamspeak_id", "TS3-UID", None))
        self.assertFalse(self._bridge("capture_myteamspeak_id", "TS3-UID", ""))

    def test_capture_idempotent(self):
        seed_user(self.db, teamspeak_id="TS3-UID", name="Alice")
        self.assertTrue(self._bridge("capture_myteamspeak_id", "TS3-UID", "MYTSID"))
        # already stored -> no row changed
        self.assertFalse(self._bridge("capture_myteamspeak_id", "TS3-UID", "MYTSID"))

    # --- lookup --------------------------------------------------------- #
    def test_find_excludes_connecting_uid(self):
        uid = seed_user(self.db, teamspeak_id="OLD", name="Bob")
        self._bridge("capture_myteamspeak_id", "OLD", "MYTSID-BOB")
        # Connecting with the same UID -> no *other* identity to bridge.
        self.assertIsNone(self._bridge("find_user_by_myteamspeak_id", "MYTSID-BOB", exclude_uid="OLD"))
        # Connecting with a new UID -> finds the prior row.
        found = self._bridge("find_user_by_myteamspeak_id", "MYTSID-BOB", exclude_uid="NEW")
        self.assertEqual(found[0], uid)

    # --- recognize + merge (the seamless payoff) ------------------------ #
    def test_recognize_merges_new_identity_into_history(self):
        # Historical identity with accrued perks.
        old_id = seed_user(self.db, teamspeak_id="OLD-UID", name="Carol", level=9)
        self._bridge("capture_myteamspeak_id", "OLD-UID", "MYTSID-CAROL")
        seed_time(self.db, platform="teamspeak", platform_uid="OLD-UID",
                  total_time=1000, season_time=200)
        seed_special_achievement(self.db, platform="teamspeak",
                                 platform_id="OLD-UID", achievement_type=1)
        # Bot pre-created a fresh placeholder row + a little time for the new UID.
        new_id = seed_user(self.db, teamspeak_id="NEW-UID", name="Carol")
        seed_time(self.db, platform="teamspeak", platform_uid="NEW-UID",
                  total_time=5, season_time=5)

        result = self._bridge("recognize_teamspeak_client", "NEW-UID", "MYTSID-CAROL", is_ts6=False)

        self.assertTrue(result["recognized"])
        self.assertEqual(result["canonical_uid"], "OLD-UID")
        # Placeholder row is gone; history survives on the canonical row.
        self.db.cursor.execute("SELECT COUNT(*) FROM user WHERE id = %s", (new_id,))
        self.assertEqual(self.db.cursor.fetchone()[0], 0)
        self.db.cursor.execute("SELECT COUNT(*) FROM user WHERE id = %s", (old_id,))
        self.assertEqual(self.db.cursor.fetchone()[0], 1)
        # Time summed onto canonical, source removed.
        self.assertEqual(self._time_total("OLD-UID"), 1005)
        self.assertIsNone(self._time_total("NEW-UID"))

    def test_recognize_ts6_records_alias_and_keeps_ts3_id(self):
        old_id = seed_user(self.db, teamspeak_id="TS3-SHA1", name="Dave", level=5)
        self._bridge("capture_myteamspeak_id", "TS3-SHA1", "MYTSID-DAVE")
        seed_time(self.db, platform="teamspeak", platform_uid="TS3-SHA1", total_time=500)

        result = self._bridge("recognize_teamspeak_client", "TS6-SHA256", "MYTSID-DAVE", is_ts6=True)

        self.assertEqual(result["canonical_uid"], "TS3-SHA1")
        ts3, ts6, mytsid, _ = self._user_row(old_id)
        self.assertEqual(ts3, "TS3-SHA1")       # canonical TS3 id preserved
        self.assertEqual(ts6, "TS6-SHA256")     # new SHA-256 recorded as alias

    def test_recognize_noop_for_unknown_account(self):
        seed_user(self.db, teamspeak_id="SOLO", name="Eve")
        result = self._bridge("recognize_teamspeak_client", "SOLO", "MYTSID-EVE", is_ts6=False)
        self.assertFalse(result["recognized"])

    # --- safety guard --------------------------------------------------- #
    def test_merge_refuses_cross_account(self):
        # Two different real people who happen to trip a match: distinct Steam ids.
        canon = seed_user(self.db, steam_id=111, teamspeak_id="CANON", name="Frank")
        other = seed_user(self.db, steam_id=222, teamspeak_id="OTHER", name="Grace")
        seed_time(self.db, platform="teamspeak", platform_uid="OTHER", total_time=42)

        result = self._bridge("merge_teamspeak_identity", "CANON", "OTHER")

        self.assertFalse(result["merged"])
        self.assertEqual(result["reason"], "cross_account_conflict")
        # Nothing moved or deleted.
        self.db.cursor.execute("SELECT COUNT(*) FROM user WHERE id = %s", (other,))
        self.assertEqual(self.db.cursor.fetchone()[0], 1)
        self.assertEqual(self._time_total("OTHER"), 42)


if __name__ == "__main__":
    unittest.main()
