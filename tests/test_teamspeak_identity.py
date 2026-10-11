import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

import atsq

from app.config import Config
from app.rankingsystem.bots.teamspeak.client_manager import ClientManager
from app.rankingsystem.bots.teamspeak.identity import (
    IdentityResolver,
    ts3_uid_from_public_key,
    ts6_uid_from_public_key,
)
from app.rankingsystem.bots.teamspeak.rank_manager import RankManager

# Synthetic key; the real derivation was verified live against a TS6 server
# (2026-10-11): SHA-1 / SHA-256 of client_public_key_raw == TS3 / TS6 UID.
PUBLIC_KEY = ("ME0DAgcAAgEgAiEAtestkeyForUnitTestsOnlyAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
              "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA==")
TS3_UID = "UQHA9NHzAyLXMNUKvBhJTTPUQkw="
TS6_UID = "q/Vu1dSlHpfSY28nKfcrUfo3PywHfuB5e7RPQQaY768="


def ts_client(dialect):
    client = MagicMock()
    client.dialect = dialect
    return client


def ts6_info(**overrides):
    info = {
        "client_database_id": "7",
        "client_unique_identifier": TS6_UID,
        "client_public_key_raw": PUBLIC_KEY,
        "client_nickname": "Tester",
        "client_myteamspeak_id": "",
    }
    info.update(overrides)
    return info


def db_without_rows():
    db = MagicMock()
    db.execute_query = AsyncMock(return_value=[])
    return db


class PublicKeyHashTests(unittest.TestCase):
    def test_hashes_reproduce_both_uid_generations(self):
        self.assertEqual(ts3_uid_from_public_key(PUBLIC_KEY), TS3_UID)
        self.assertEqual(ts6_uid_from_public_key(PUBLIC_KEY), TS6_UID)


class IdentityResolverTests(unittest.TestCase):
    def test_ts3_is_passthrough_without_db(self):
        db = db_without_rows()
        resolver = IdentityResolver(ts_client(atsq.Dialect.TS3), db)

        self.assertEqual(asyncio.run(resolver.resolve({"client_unique_identifier": "uid1"})), "uid1")
        self.assertEqual(asyncio.run(resolver.native_uid("uid1")), "uid1")
        db.execute_query.assert_not_awaited()

    def test_ts6_client_resolves_to_ts3_uid(self):
        resolver = IdentityResolver(ts_client(atsq.Dialect.TS6), db_without_rows())

        self.assertEqual(asyncio.run(resolver.resolve(ts6_info())), TS3_UID)
        self.assertEqual(asyncio.run(resolver.native_uid(TS3_UID)), TS6_UID)

    def test_ts6_resolution_is_cached(self):
        db = db_without_rows()
        resolver = IdentityResolver(ts_client(atsq.Dialect.TS6), db)

        asyncio.run(resolver.resolve(ts6_info()))
        asyncio.run(resolver.resolve(ts6_info()))

        self.assertEqual(db.execute_query.await_count, 1)

    def test_ts6_alias_wins_over_public_key(self):
        db = MagicMock()
        db.execute_query = AsyncMock(return_value=[("OLD-TS3-UID",)])
        resolver = IdentityResolver(ts_client(atsq.Dialect.TS6), db)

        self.assertEqual(asyncio.run(resolver.resolve(ts6_info())), "OLD-TS3-UID")

    def test_ts6_key_not_matching_uid_falls_back_to_native(self):
        resolver = IdentityResolver(ts_client(atsq.Dialect.TS6), db_without_rows())

        uid = asyncio.run(resolver.resolve(ts6_info(client_public_key_raw="something-else")))

        self.assertEqual(uid, TS6_UID)

    def test_ts6_missing_key_falls_back_to_native(self):
        resolver = IdentityResolver(ts_client(atsq.Dialect.TS6), db_without_rows())

        uid = asyncio.run(resolver.resolve(ts6_info(client_public_key_raw="")))

        self.assertEqual(uid, TS6_UID)

    def test_ts6_offline_user_native_uid_from_db_alias(self):
        db = MagicMock()
        db.execute_query = AsyncMock(return_value=[(TS6_UID,)])
        resolver = IdentityResolver(ts_client(atsq.Dialect.TS6), db)

        self.assertEqual(asyncio.run(resolver.native_uid(TS3_UID)), TS6_UID)


class Ts6BotWiringTests(unittest.TestCase):
    def _manager(self, ts):
        manager = ClientManager(Config, rank_manager=MagicMock(), client=ts)
        manager.db = MagicMock()
        manager.db.execute_query = AsyncMock(return_value=[])
        manager.db.recognize_teamspeak_client = AsyncMock(return_value={"recognized": False})
        manager.identity.db = manager.db
        return manager

    def test_connect_tracks_ts6_client_under_ts3_uid(self):
        ts = ts_client(atsq.Dialect.TS6)
        ts.client_info = AsyncMock(return_value=ts6_info())
        ts.server_groups_by_client = AsyncMock(return_value=[{"sgid": "6"}])
        manager = self._manager(ts)

        uid = asyncio.run(manager.handle_client_connect({"client_type": "0", "clid": "3"}))

        self.assertEqual(uid, TS3_UID)
        self.assertIn(TS3_UID, manager.connected_users)
        manager.db.recognize_teamspeak_client.assert_awaited_once_with(
            TS3_UID, "", native_uid=TS6_UID)

    def test_connect_rekeys_to_merged_history_on_ts6(self):
        ts = ts_client(atsq.Dialect.TS6)
        ts.client_info = AsyncMock(return_value=ts6_info(client_myteamspeak_id="MYTS"))
        ts.server_groups_by_client = AsyncMock(return_value=[{"sgid": "6"}])
        manager = self._manager(ts)
        manager.db.recognize_teamspeak_client = AsyncMock(return_value={
            "recognized": True, "merged": True,
            "canonical_uid": "OLD-TS3-UID", "absorbed_uid": TS3_UID})

        uid = asyncio.run(manager.handle_client_connect({"client_type": "0", "clid": "3"}))

        self.assertEqual(uid, "OLD-TS3-UID")
        self.assertEqual(asyncio.run(manager.identity.native_uid("OLD-TS3-UID")), TS6_UID)

    def test_ts3_merge_keeps_native_uid(self):
        ts = ts_client(atsq.Dialect.TS3)
        ts.client_info = AsyncMock(return_value={
            "client_database_id": "7", "client_unique_identifier": "NEW-UID",
            "client_nickname": "Tester", "client_myteamspeak_id": "MYTS"})
        ts.server_groups_by_client = AsyncMock(return_value=[{"sgid": "6"}])
        manager = self._manager(ts)
        manager.db.recognize_teamspeak_client = AsyncMock(return_value={
            "recognized": True, "merged": True,
            "canonical_uid": "OLD-UID", "absorbed_uid": "NEW-UID"})

        uid = asyncio.run(manager.handle_client_connect({"client_type": "0", "clid": "3"}))

        self.assertEqual(uid, "NEW-UID")
        manager.db.recognize_teamspeak_client.assert_awaited_once_with(
            "NEW-UID", "MYTS", native_uid=None)

    def test_rank_manager_looks_up_native_uid_on_ts6(self):
        ts = ts_client(atsq.Dialect.TS6)
        ts.client_dbid_from_uid = AsyncMock(return_value="7")
        resolver = IdentityResolver(ts, db_without_rows())
        resolver.remember(TS3_UID, TS6_UID)
        manager = RankManager(Config, MagicMock(), ts, resolver)

        self.assertEqual(asyncio.run(manager._cldbid(TS3_UID)), "7")
        ts.client_dbid_from_uid.assert_awaited_once_with(TS6_UID)


if __name__ == "__main__":
    unittest.main()
