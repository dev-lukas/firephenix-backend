import asyncio
import json
import threading
import unittest
from unittest.mock import AsyncMock, MagicMock

from asyncmy import errors as asyncmy_errors

from app.rankingsystem import rankingsystem as rs_module
from app.rankingsystem.rankingsystem import RankingSystem
from app.utils.async_database import AsyncDatabaseManager


class FakeAsyncValkey:
    def __init__(self):
        self.sets = {}

    async def set(self, key, value, ex=None):
        self.sets[key] = (value, ex)


def make_system():
    rs = object.__new__(RankingSystem)
    rs.valkey = FakeAsyncValkey()
    rs.dc = None
    rs.ts = None
    rs.running = True
    rs._loop = None
    rs._stop_event = None
    return rs


class TeamspeakCommandDispatchTests(unittest.TestCase):
    def test_create_owned_channel_roundtrip(self):
        rs = make_system()
        rs.ts = MagicMock()
        rs.ts.create_owned_channel = AsyncMock(return_value="42")

        data = json.dumps({
            "command": "create_owned_channel",
            "platform_id": "uid1",
            "channel_name": "Chan",
            "message_id": "msg:1",
        })
        asyncio.run(rs._handle_command("teamspeak:commands", data))

        rs.ts.create_owned_channel.assert_awaited_once_with("uid1", "Chan")
        value, ex = rs.valkey.sets["msg:1"]
        self.assertEqual(json.loads(value), {"channel_id": "42"})
        self.assertEqual(ex, 30)

    def test_add_move_shield_returns_structured_result(self):
        rs = make_system()
        rs.ts = MagicMock()
        rs.ts.set_server_group = AsyncMock(
            return_value={"ok": True, "already_present": False, "cldbid": "7", "group_id": 41})

        data = json.dumps({"command": "add_move_shield", "platform_id": "uid1", "message_id": "msg:2"})
        asyncio.run(rs._handle_command("teamspeak:commands", data))

        payload = json.loads(rs.valkey.sets["msg:2"][0])
        self.assertTrue(payload["result"])
        self.assertEqual(payload["cldbid"], "7")

    def test_slow_command_times_out_without_raising(self):
        rs = make_system()
        rs.ts = MagicMock()

        async def slow(*args, **kwargs):
            await asyncio.sleep(1)

        rs.ts.check_ranks = slow
        original = rs_module.COMMAND_TIMEOUT
        rs_module.COMMAND_TIMEOUT = 0.05
        try:
            asyncio.run(rs._handle_command(
                "teamspeak:commands", json.dumps({"command": "check_ranks", "platform_id": "u"})))
        finally:
            rs_module.COMMAND_TIMEOUT = original

    def test_invalid_json_is_swallowed(self):
        rs = make_system()
        rs.ts = MagicMock()
        asyncio.run(rs._handle_command("teamspeak:commands", "{not json"))


class DiscordCommandDispatchTests(unittest.TestCase):
    def test_add_ignore_role_untracks_user(self):
        rs = make_system()
        rs.dc = MagicMock()
        rs.dc.set_user_group = AsyncMock(return_value=True)

        data = json.dumps({"command": "add_ignore_role", "platform_id": "123", "message_id": "msg:3"})
        asyncio.run(rs._handle_command("discord:commands", data))

        rs.dc.set_user_group.assert_awaited_once()
        rs.dc.time_tracker.remove_tracked_user.assert_called_once_with(123)
        self.assertEqual(json.loads(rs.valkey.sets["msg:3"][0]), {"result": True})

    def test_send_verification_dispatches(self):
        rs = make_system()
        rs.dc = MagicMock()
        rs.dc.send_verification = AsyncMock(return_value=True)

        data = json.dumps({"command": "send_verification", "platform_id": "123", "code": "9999"})
        asyncio.run(rs._handle_command("discord:commands", data))

        rs.dc.send_verification.assert_awaited_once_with(123, "9999")


class ShutdownTests(unittest.TestCase):
    def test_shutdown_wakes_loop_from_other_thread(self):
        rs = make_system()

        async def go():
            rs._loop = asyncio.get_running_loop()
            rs._stop_event = asyncio.Event()
            thread = threading.Thread(target=rs.shutdown)
            thread.start()
            await asyncio.wait_for(rs._stop_event.wait(), timeout=5)
            thread.join(timeout=5)

        asyncio.run(go())
        self.assertFalse(rs.running)


class AsyncDatabaseRetryTests(unittest.TestCase):
    def test_run_retries_once_after_driver_error(self):
        db = AsyncDatabaseManager()
        attempts = []

        class FakeAcquire:
            async def __aenter__(self):
                return "conn"

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakePool:
            def acquire(self):
                return FakeAcquire()

        async def fake_ensure():
            return FakePool()

        db._ensure_pool = fake_ensure
        db._dispose_pool = AsyncMock()

        async def op(conn):
            attempts.append(1)
            if len(attempts) == 1:
                raise asyncmy_errors.OperationalError(2013, "gone")
            return "ok"

        result = asyncio.run(db._run(op))

        self.assertEqual(result, "ok")
        self.assertEqual(len(attempts), 2)
        db._dispose_pool.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
