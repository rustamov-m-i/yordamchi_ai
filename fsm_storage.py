"""SQLite-backed FSM storage for aiogram.

Replaces MemoryStorage so FSM state survives bot restarts. Without this,
a deployment / crash / OOM mid-flow (e.g. mid task-creation) loses the
user's progress with no way to resume.

Storage shape:
    fsm_storage(key TEXT PRIMARY KEY, state TEXT, data TEXT, updated_at TEXT)

`key` is the JSON-serialised StorageKey tuple (bot_id, chat_id, user_id, thread_id).
"""

import json
import logging
from datetime import datetime, timedelta
from typing import Any, Mapping, Optional

import aiosqlite
import pytz

from aiogram.fsm.state import State
from aiogram.fsm.storage.base import BaseStorage, StorageKey

try:
    from aiogram.fsm.storage.base import StateType
except ImportError:  # pragma: no cover
    StateType = Any  # type: ignore

import config

logger = logging.getLogger(__name__)
_TZ = pytz.timezone(config.TIMEZONE)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS fsm_storage (
    key TEXT PRIMARY KEY,
    state TEXT,
    data TEXT,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_fsm_updated ON fsm_storage(updated_at);
"""


def _key_str(key: StorageKey) -> str:
    """StorageKey → stable string id."""
    return json.dumps([key.bot_id, key.chat_id, key.user_id, key.thread_id, key.business_connection_id],
                       sort_keys=True)


def _now_iso() -> str:
    return datetime.now(_TZ).isoformat()


class SQLiteStorage(BaseStorage):
    """aiogram FSM storage backed by the project's main SQLite database.

    All ops are awaited and use the same file as the rest of the bot. This
    means FSM state participates in WAL and survives restarts."""

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = db_path or config.DATABASE_PATH

    async def init(self) -> None:
        """Create the fsm_storage table. Idempotent — safe to call repeatedly."""
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(_SCHEMA)
            await db.commit()

    async def set_state(self, key: StorageKey, state: Optional[StateType]) -> None:  # type: ignore[override]
        state_str = state.state if isinstance(state, State) else state
        k = _key_str(key)
        async with aiosqlite.connect(self.db_path) as db:
            if state_str is None:
                # Clearing state — drop the entire row to keep the table tidy.
                await db.execute("DELETE FROM fsm_storage WHERE key=?", (k,))
            else:
                await db.execute(
                    """INSERT INTO fsm_storage (key, state, data, updated_at)
                       VALUES (?, ?, COALESCE((SELECT data FROM fsm_storage WHERE key=?), '{}'), ?)
                       ON CONFLICT(key) DO UPDATE SET
                           state=excluded.state,
                           updated_at=excluded.updated_at""",
                    (k, state_str, k, _now_iso()),
                )
            await db.commit()

    async def get_state(self, key: StorageKey) -> Optional[str]:
        k = _key_str(key)
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT state FROM fsm_storage WHERE key=?", (k,))
            row = await cur.fetchone()
            return row[0] if row else None

    async def set_data(self, key: StorageKey, data: Mapping[str, Any]) -> None:  # type: ignore[override]
        k = _key_str(key)
        payload = json.dumps(dict(data), ensure_ascii=False, default=str)
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO fsm_storage (key, state, data, updated_at)
                   VALUES (?, (SELECT state FROM fsm_storage WHERE key=?), ?, ?)
                   ON CONFLICT(key) DO UPDATE SET
                       data=excluded.data,
                       updated_at=excluded.updated_at""",
                (k, k, payload, _now_iso()),
            )
            await db.commit()

    async def get_data(self, key: StorageKey) -> dict[str, Any]:
        k = _key_str(key)
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("SELECT data FROM fsm_storage WHERE key=?", (k,))
            row = await cur.fetchone()
        if not row or not row[0]:
            return {}
        try:
            return json.loads(row[0])
        except json.JSONDecodeError:
            logger.warning("Corrupted FSM data for key=%s — returning empty dict", k)
            return {}

    async def close(self) -> None:
        # Per-call connections — nothing persistent to close.
        return

    async def purge_old_rows(self, max_age_hours: int = 24) -> int:
        """Maintenance: drop FSM rows older than `max_age_hours`. The
        in-handler FSM TTL middleware already auto-clears stale state on
        the user's next interaction; this is a backstop for sessions the
        user never returns to."""
        cutoff = (datetime.now(_TZ) - timedelta(hours=max_age_hours)).isoformat()
        async with aiosqlite.connect(self.db_path) as db:
            cur = await db.execute("DELETE FROM fsm_storage WHERE updated_at < ?", (cutoff,))
            await db.commit()
            return cur.rowcount


