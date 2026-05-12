import json
import os
import socket
import time

import valkey

from app.utils.database import normalize_ttt_achievement_payload
from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()

TTT_ACHIEVEMENT_STREAM_KEY = "gameserver:ttt:achievement_events"
TTT_ACHIEVEMENT_CONSUMER_GROUP = "firephenix-backend"


class TttAchievementStreamConsumer:
    def __init__(
        self,
        valkey_client,
        database,
        stream_key: str = TTT_ACHIEVEMENT_STREAM_KEY,
        group: str = TTT_ACHIEVEMENT_CONSUMER_GROUP,
        consumer_name: str | None = None,
    ):
        self.valkey = valkey_client
        self.database = database
        self.stream_key = stream_key
        self.group = group
        self.consumer_name = consumer_name or f"bot:{socket.gethostname()}:{os.getpid()}"
        self.group_ready = False

    def ensure_group(self) -> None:
        if self.group_ready:
            return

        try:
            self.valkey.xgroup_create(self.stream_key, self.group, id="0", mkstream=True)
        except valkey.ResponseError as exc:
            if "BUSYGROUP" not in str(exc):
                raise

        self.group_ready = True

    def consume_once(self, block_ms: int = 5000, count: int = 10) -> int:
        self.ensure_group()
        streams = self.valkey.xreadgroup(
            self.group,
            self.consumer_name,
            {self.stream_key: ">"},
            count=count,
            block=block_ms,
        )

        handled = 0
        for stream_name, messages in streams or []:
            for message_id, fields in messages:
                if self.handle_message(stream_name, message_id, fields):
                    handled += 1

        return handled

    def handle_message(self, stream_name: str, message_id: str, fields: dict) -> bool:
        raw_payload = fields.get("payload") if isinstance(fields, dict) else None
        try:
            payload = json.loads(raw_payload) if isinstance(raw_payload, str) else None
            normalize_ttt_achievement_payload(payload)
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            logging.error(f"Acknowledging malformed TTT achievement event {message_id}: {exc}")
            self.valkey.xack(stream_name, self.group, message_id)
            return False

        try:
            self.database.ingest_ttt_achievement_event(payload)
        except Exception as exc:
            logging.error(f"Failed to ingest TTT achievement event {message_id}: {exc}")
            return False

        self.valkey.xack(stream_name, self.group, message_id)
        return True

    def run_forever(self, running):
        while running():
            try:
                self.consume_once()
            except valkey.ConnectionError as exc:
                self.group_ready = False
                logging.error(f"Valkey connection error in TTT achievement consumer: {exc}")
                time.sleep(3)
            except Exception as exc:
                logging.error(f"Unexpected TTT achievement consumer error: {exc}")
                time.sleep(3)
