"""TS3 -> TS6 identity bridge via the client public key.

A TS UID is a hash of the identity's public key: TS3 uses SHA-1, TS6 SHA-256.
TS6 query exposes ``client_public_key_raw`` and hashing that exact string
reproduces both UIDs (verified live 2026-10-11 on 6.0.0-beta13.1). So on TS6 we
derive every client's TS3 UID and keep it as the canonical ``teamspeak_id``:
all stored history stays keyed as before. The native TS6 UID is only needed
for server-side lookups (``client_dbid_from_uid``), see :meth:`native_uid`.
"""

import base64
import hashlib

import atsq

from app.utils.logger import RankingLogger

logging = RankingLogger(__name__).get_logger()


def ts3_uid_from_public_key(public_key_raw: str) -> str:
    return base64.b64encode(hashlib.sha1(public_key_raw.encode("ascii")).digest()).decode()


def ts6_uid_from_public_key(public_key_raw: str) -> str:
    return base64.b64encode(hashlib.sha256(public_key_raw.encode("ascii")).digest()).decode()


class IdentityResolver:
    """Maps native TS UIDs to canonical ``teamspeak_id`` values and back.

    On TS3 both are the same UID, so every method is a passthrough."""

    def __init__(self, client: atsq.Client, db=None):
        self.client = client
        self.db = db
        self._canonical_by_native = {}
        self._native_by_canonical = {}

    def is_ts6(self) -> bool:
        try:
            return self.client.dialect == atsq.Dialect.TS6
        except Exception:
            return False

    def remember(self, canonical_uid: str, native_uid: str) -> None:
        self._canonical_by_native[native_uid] = canonical_uid
        self._native_by_canonical[canonical_uid] = native_uid

    async def resolve(self, client_info: dict):
        """Canonical UID for a connected client, or None if it has no UID."""
        native = client_info.get("client_unique_identifier")
        if not native or not self.is_ts6():
            return native
        if native in self._canonical_by_native:
            return self._canonical_by_native[native]

        # A user already bridged to an older identity (myTeamSpeak merge) keeps
        # their historical row, recorded as the teamspeak6_id alias.
        canonical = await self._canonical_from_alias(native)
        if canonical is None:
            public_key = client_info.get("client_public_key_raw") or ""
            if public_key and ts6_uid_from_public_key(public_key) == native:
                canonical = ts3_uid_from_public_key(public_key)
            else:
                logging.warning(
                    f"No usable public key for TS6 client {native}; tracking it by its TS6 UID")
                canonical = native
        self.remember(canonical, native)
        return canonical

    async def native_uid(self, canonical_uid: str) -> str:
        """The UID the TS server knows this user by (for client_dbid_from_uid)."""
        canonical_uid = str(canonical_uid)
        if not self.is_ts6():
            return canonical_uid
        if canonical_uid in self._native_by_canonical:
            return self._native_by_canonical[canonical_uid]
        if self.db is not None:
            rows = await self.db.execute_query(
                "SELECT teamspeak6_id FROM user WHERE teamspeak_id = %s AND teamspeak6_id IS NOT NULL",
                (canonical_uid,))
            if rows:
                return rows[0][0]
        return canonical_uid

    async def _canonical_from_alias(self, native_uid: str):
        if self.db is None:
            return None
        rows = await self.db.execute_query(
            "SELECT teamspeak_id FROM user WHERE teamspeak6_id = %s AND teamspeak_id IS NOT NULL",
            (native_uid,))
        return rows[0][0] if rows else None
