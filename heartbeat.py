"""Liveness heartbeat for the supervised deployer (Phase 5).

The bot touches a small file on startup and every ~30s; the deployer's health-check
reads it to confirm a freshly-restarted bot is ACTUALLY alive (not merely systemd
'active' while crash-looping). Pure file I/O.

The deployer reads this file STANDALONE (it does not import this module — it must
work even if the bot's code is broken). This module is the bot-side WRITER plus
read helpers for in-process use.
"""
import os
import time

import config

_HEARTBEAT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(config.DATABASE_PATH)), ".heartbeat")


def heartbeat_path() -> str:
    return _HEARTBEAT_PATH


def write_heartbeat() -> None:
    """Write current epoch seconds to the heartbeat file (atomic replace).
    Best-effort — never raises, so it can't crash the bot."""
    try:
        os.makedirs(os.path.dirname(_HEARTBEAT_PATH), exist_ok=True)
        tmp = _HEARTBEAT_PATH + ".tmp"
        with open(tmp, "w") as f:
            f.write(str(int(time.time())))
        os.replace(tmp, _HEARTBEAT_PATH)
    except Exception:
        pass


def heartbeat_age_seconds(path: "str | None" = None) -> "float | None":
    """Seconds since the last heartbeat, or None if missing/unreadable."""
    try:
        with open(path or _HEARTBEAT_PATH) as f:
            return max(0.0, time.time() - int(f.read().strip()))
    except Exception:
        return None


def is_alive(max_age: float = 60.0, path: "str | None" = None) -> bool:
    age = heartbeat_age_seconds(path)
    return age is not None and age <= max_age
