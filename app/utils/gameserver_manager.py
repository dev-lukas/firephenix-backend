import json
import time
import uuid

from app.utils.valkey_manager import ValkeyManager


class GameServerCommandClient:
    ALLOWED_COMMANDS = {"status", "healthcheck", "restart", "start", "stop"}

    def __init__(self, valkey_client=None, timeout_seconds=3, poll_interval_seconds=0.1):
        self.valkey = valkey_client or ValkeyManager().valkey
        self.timeout_seconds = timeout_seconds
        self.poll_interval_seconds = poll_interval_seconds

    def command(self, server_id: str, command: str):
        if command not in self.ALLOWED_COMMANDS:
            return {"ok": False, "error": "command_not_allowed", "command": command}, 400

        message_id = uuid.uuid4().hex
        channel = f"gameserver:{server_id}:commands"
        response_key = f"gameserver:{server_id}:responses:{message_id}"
        status_key = f"gameserver:{server_id}:status"
        payload = {
            "version": 1,
            "message_id": message_id,
            "command": command,
            "server": server_id,
            "created_at": int(time.time()),
        }

        self.valkey.publish(channel, json.dumps(payload, separators=(",", ":")))

        deadline = time.monotonic() + self.timeout_seconds
        while time.monotonic() < deadline:
            raw = self.valkey.get(response_key)
            if raw:
                self.valkey.delete(response_key)
                try:
                    response = json.loads(raw)
                except json.JSONDecodeError:
                    return {"ok": False, "error": "invalid_manager_response"}, 502
                if response.get("error"):
                    return response, 502
                return response, 200
            time.sleep(self.poll_interval_seconds)

        if not self.valkey.get(status_key):
            return {"ok": False, "error": "manager_unavailable", "server": server_id}, 503
        return {"ok": False, "error": "manager_timeout", "server": server_id}, 504
