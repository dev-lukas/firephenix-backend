"""Regression tests for the admin ranking-transfer data movers.

`_move_platform_data` (used by the admin transfer_ranking endpoint) does a
self-referential INSERT..SELECT with ON DUPLICATE KEY UPDATE. Against real
MariaDB an unqualified/ambiguous form throws "Column 'time.total_time' in
UPDATE is ambiguous"; these tests exercise the movers end-to-end so that stays
fixed. There was previously no real-DB coverage of this path.
"""

import unittest

from tests.integration.harness import (
    skip_unless_integration,
    open_database,
    reset_database,
    seed_time,
)


@skip_unless_integration
class AdminMoverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db = open_database()

    @classmethod
    def tearDownClass(cls):
        cls.db.close()

    def setUp(self):
        reset_database(self.db)

    def _time(self, uid):
        self.db.cursor.execute(
            "SELECT total_time, season_time FROM time WHERE platform='teamspeak' AND platform_uid = ?",
            (uid,))
        return self.db.cursor.fetchone()

    def test_move_time_sums_into_existing_target(self):
        from app.api.admin.routes import _move_platform_data
        seed_time(self.db, platform="teamspeak", platform_uid="SRC", total_time=100, season_time=10)
        seed_time(self.db, platform="teamspeak", platform_uid="DST", total_time=5, season_time=1)

        _move_platform_data(self.db, "teamspeak", "SRC", "DST")
        self.db.conn.commit()

        self.assertEqual(self._time("DST"), (105, 11))  # summed
        self.assertIsNone(self._time("SRC"))            # source removed

    def test_move_time_repoints_when_no_target(self):
        from app.api.admin.routes import _move_platform_data
        seed_time(self.db, platform="teamspeak", platform_uid="SRC", total_time=42, season_time=7)

        _move_platform_data(self.db, "teamspeak", "SRC", "DST")
        self.db.conn.commit()

        self.assertEqual(self._time("DST"), (42, 7))
        self.assertIsNone(self._time("SRC"))


if __name__ == "__main__":
    unittest.main()
