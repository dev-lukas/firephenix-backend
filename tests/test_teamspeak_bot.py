import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock

from app.config import Config
from app.rankingsystem.bots.teamspeak.bot import TeamspeakBot
from app.rankingsystem.bots.teamspeak.channel_manager import ChannelManager
from app.rankingsystem.bots.teamspeak.client_manager import ClientManager
from app.rankingsystem.bots.teamspeak.rank_manager import RankManager


def make_client_manager(ts_client):
    return ClientManager(Config, rank_manager=MagicMock(), client=ts_client)


class TeamspeakClientTrackingTests(unittest.TestCase):
    def test_connect_event_tracks_voice_client(self):
        ts = MagicMock()
        ts.client_info = AsyncMock(return_value={
            "client_database_id": "7",
            "client_unique_identifier": "uid1",
            "client_nickname": "Tester",
        })
        ts.server_groups_by_client = AsyncMock(return_value=[{"sgid": "6"}])
        manager = make_client_manager(ts)

        uid = asyncio.run(manager.handle_client_connect({"client_type": "0", "clid": "3"}))

        self.assertEqual(uid, "uid1")
        self.assertIn("uid1", manager.connected_users)
        self.assertEqual(manager.client_uid_map["3"], "uid1")
        self.assertEqual(manager.client_name_map["uid1"], "Tester")

    def test_connect_event_ignores_excluded_role(self):
        ts = MagicMock()
        ts.client_info = AsyncMock(return_value={
            "client_database_id": "7",
            "client_unique_identifier": "uid1",
            "client_nickname": "Tester",
        })
        ts.server_groups_by_client = AsyncMock(
            return_value=[{"sgid": Config.TS3_EXCLUDED_ROLE_ID}]
        )
        manager = make_client_manager(ts)

        uid = asyncio.run(manager.handle_client_connect({"client_type": "0", "clid": "3"}))

        self.assertIsNone(uid)
        self.assertEqual(manager.connected_users, set())

    def test_connect_event_ignores_query_clients(self):
        ts = MagicMock()
        ts.client_info = AsyncMock()
        manager = make_client_manager(ts)

        uid = asyncio.run(manager.handle_client_connect({"client_type": "1", "clid": "3"}))

        self.assertIsNone(uid)
        ts.client_info.assert_not_awaited()

    def test_disconnect_event_removes_tracking(self):
        manager = make_client_manager(MagicMock())
        manager.connected_users = {"uid1"}
        manager.client_uid_map = {"3": "uid1"}
        manager.client_name_map = {"uid1": "Tester"}

        uid = manager.handle_client_disconnect({"clid": "3", "reasonid": "8"})

        self.assertEqual(uid, "uid1")
        self.assertEqual(manager.connected_users, set())
        self.assertEqual(manager.client_uid_map, {})
        self.assertEqual(manager.client_name_map, {})

    def test_get_online_users_empty_while_disconnected(self):
        bot = object.__new__(TeamspeakBot)
        bot.client = MagicMock(connected=False)
        bot.client_manager = MagicMock()

        self.assertEqual(bot.get_online_users(), ([], {}))
        bot.client_manager.get_online_users.assert_not_called()

        bot.client.connected = True
        bot.client_manager.get_online_users.return_value = (["uid1"], {"uid1": "Tester"})
        self.assertEqual(bot.get_online_users(), (["uid1"], {"uid1": "Tester"}))


class TeamspeakRankManagerTests(unittest.TestCase):
    def test_set_server_group_already_present(self):
        ts = MagicMock()
        ts.client_dbid_from_uid = AsyncMock(return_value="7")
        ts.server_groups_by_client = AsyncMock(return_value=[{"sgid": "41"}])
        ts.server_group_add_client = AsyncMock()
        manager = RankManager(Config, MagicMock(), ts)

        result = asyncio.run(manager.set_server_group("uid1", 41))

        self.assertTrue(result["ok"])
        self.assertTrue(result["already_present"])
        ts.server_group_add_client.assert_not_awaited()

    def test_set_server_group_adds_missing_group(self):
        ts = MagicMock()
        ts.client_dbid_from_uid = AsyncMock(return_value="7")
        ts.server_groups_by_client = AsyncMock(return_value=[{"sgid": "6"}])
        ts.server_group_add_client = AsyncMock()
        manager = RankManager(Config, MagicMock(), ts)

        result = asyncio.run(manager.set_server_group("uid1", 41))

        self.assertTrue(result["ok"])
        self.assertFalse(result["already_present"])
        ts.server_group_add_client.assert_awaited_once_with(sgid=41, cldbid="7")

    def test_remove_server_group_skips_absent_group(self):
        ts = MagicMock()
        ts.client_dbid_from_uid = AsyncMock(return_value="7")
        ts.server_groups_by_client = AsyncMock(return_value=[{"sgid": "6"}])
        ts.server_group_del_client = AsyncMock()
        manager = RankManager(Config, MagicMock(), ts)

        self.assertTrue(asyncio.run(manager.remove_server_group("uid1", 41)))
        ts.server_group_del_client.assert_not_awaited()


class TeamspeakChannelManagerTests(unittest.TestCase):
    def test_send_verification_messages_matching_client(self):
        ts = MagicMock()
        ts.client_dbid_from_uid = AsyncMock(return_value="7")
        ts.client_list = AsyncMock(return_value=[
            {"clid": "2", "client_database_id": "9"},
            {"clid": "3", "client_database_id": "7"},
        ])
        ts.send_text_message = AsyncMock()
        manager = ChannelManager(Config, ts)

        self.assertTrue(asyncio.run(manager.send_verification("uid1", "1234")))
        ts.send_text_message.assert_awaited_once()
        self.assertEqual(ts.send_text_message.await_args.kwargs["target"], "3")

    def test_send_verification_returns_false_when_offline(self):
        ts = MagicMock()
        ts.client_dbid_from_uid = AsyncMock(return_value="7")
        ts.client_list = AsyncMock(return_value=[])
        ts.send_text_message = AsyncMock()
        manager = ChannelManager(Config, ts)

        self.assertFalse(asyncio.run(manager.send_verification("uid1", "1234")))
        ts.send_text_message.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
