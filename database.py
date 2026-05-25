"""Async SQLite operations for Yordamchi.

Tables: tasks, reminders, meetings, contacts, corrections, principal_profile, conversation_history, pending_actions.
All datetime fields are stored as ISO 8601 strings in Asia/Tashkent timezone.
"""

import json
import uuid
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any, Optional

import aiosqlite
import pytz

import config

TZ = pytz.timezone(config.TIMEZONE)


def now_iso() -> str:
    return datetime.now(TZ).isoformat()


def new_id(prefix: str = "") -> str:
    short = uuid.uuid4().hex[:10]
    return f"{prefix}{short}" if prefix else short


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    deadline TEXT,
    priority TEXT CHECK(priority IN ('P0','P1','P2','P3')) DEFAULT 'P2',
    status TEXT CHECK(status IN ('todo','in_progress','blocked','done','cancelled')) DEFAULT 'todo',
    tags TEXT,
    assignee TEXT,
    recurrence_rule TEXT,
    recurrence_next_at TEXT,
    recurrence_parent_id TEXT,
    source TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    reminded_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_tasks_status_deadline ON tasks(status, deadline);
CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);

CREATE TABLE IF NOT EXISTS meetings (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    datetime_start TEXT NOT NULL,
    datetime_end TEXT,
    participants TEXT,
    location_or_link TEXT,
    agenda TEXT,
    prep_notes TEXT,
    follow_up_actions TEXT,
    reminded_at TEXT,
    prep_sent_at TEXT,
    followup_sent_at TEXT,
    icloud_uid TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meetings_start ON meetings(datetime_start);
-- idx_meetings_icloud is created in init() migration block (needs column to exist first
-- on databases that were created before this column was added).

CREATE TABLE IF NOT EXISTS contacts (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    role TEXT,
    formality_level INTEGER DEFAULT 3,
    preferred_channel TEXT,
    last_interaction TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS corrections (
    id TEXT PRIMARY KEY,
    context TEXT,
    correction TEXT,
    reason TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS principal_profile (
    key TEXT PRIMARY KEY,
    data TEXT,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_conv_created ON conversation_history(created_at DESC);

CREATE TABLE IF NOT EXISTS llm_audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    provider TEXT NOT NULL,
    model TEXT,
    purpose TEXT,
    input_hash TEXT,
    input_chars INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    cache_read_tokens INTEGER,
    cache_creation_tokens INTEGER,
    redacted_terms_count INTEGER DEFAULT 0,
    estimated_cost_usd REAL,
    error TEXT
);

CREATE INDEX IF NOT EXISTS idx_audit_ts ON llm_audit_log(ts DESC);

CREATE TABLE IF NOT EXISTS task_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL,
    ts TEXT NOT NULL,
    action TEXT NOT NULL,
    field TEXT,
    old_value TEXT,
    new_value TEXT,
    source TEXT
);

CREATE INDEX IF NOT EXISTS idx_history_task ON task_history(task_id, ts DESC);

CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    note TEXT,
    remind_at TEXT NOT NULL,
    status TEXT CHECK(status IN ('scheduled','sent','done','cancelled')) DEFAULT 'scheduled',
    recurrence_rule TEXT,
    task_id TEXT,
    meeting_id TEXT,
    source TEXT,
    snooze_count INTEGER DEFAULT 0,
    sent_at TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reminders_status_time ON reminders(status, remind_at);
CREATE INDEX IF NOT EXISTS idx_reminders_task ON reminders(task_id);
CREATE INDEX IF NOT EXISTS idx_reminders_meeting ON reminders(meeting_id);

CREATE TABLE IF NOT EXISTS icloud_retry_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    operation TEXT NOT NULL,
    meeting_id TEXT,
    payload TEXT,
    attempts INTEGER DEFAULT 0,
    next_attempt_at TEXT NOT NULL,
    last_error TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_retry_next ON icloud_retry_queue(next_attempt_at);

-- Executive planning sessions (one /plan invocation = one row)
CREATE TABLE IF NOT EXISTS plans (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    input_text TEXT NOT NULL,
    output_text TEXT NOT NULL,
    accepted INTEGER DEFAULT 0,           -- did the principal explicitly accept
    follow_up_asked_at TEXT,              -- when did we ask "how did this plan go"
    follow_up_response TEXT,              -- principal's review of the plan
    task_ids TEXT                          -- JSON array of task IDs created from this plan
);

CREATE INDEX IF NOT EXISTS idx_plans_created ON plans(created_at DESC);

-- Proactive insights/suggestions that the bot surfaced
CREATE TABLE IF NOT EXISTS insights_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    insight_type TEXT NOT NULL,           -- stale_tasks, overload, missing_deadline, follow_up, etc.
    payload TEXT,                          -- JSON: details about what triggered
    user_action TEXT,                      -- accepted | dismissed | ignored | NULL=pending
    acted_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_insights_ts ON insights_log(ts DESC);
"""


async def init() -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        # WAL mode: allow concurrent reads while a write is in progress, drastically
        # reducing "database is locked" errors when scheduler + handler + webapp coincide.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        await db.executescript(SCHEMA)

        # Idempotent migrations for existing DBs (CREATE TABLE IF NOT EXISTS doesn't add columns)
        cur = await db.execute("PRAGMA table_info(meetings)")
        meeting_cols = {row[1] for row in await cur.fetchall()}
        if "icloud_uid" not in meeting_cols:
            await db.execute("ALTER TABLE meetings ADD COLUMN icloud_uid TEXT")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_meetings_icloud ON meetings(icloud_uid)")

        cur = await db.execute("PRAGMA table_info(tasks)")
        task_cols = {row[1] for row in await cur.fetchall()}
        if "assignee" not in task_cols:
            await db.execute("ALTER TABLE tasks ADD COLUMN assignee TEXT")
        for col in ("recurrence_rule", "recurrence_next_at", "recurrence_parent_id"):
            if col not in task_cols:
                await db.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_recurrence ON tasks(recurrence_rule, recurrence_next_at)")

        for col in ("prep_sent_at", "followup_sent_at"):
            if col not in meeting_cols:
                await db.execute(f"ALTER TABLE meetings ADD COLUMN {col} TEXT")

        await db.commit()


# ─────────────────────────────────────────── TASKS ───────────────────────────────────────────

async def _log_history(db, task_id: str, action: str, field: Optional[str] = None,
                        old_value: Optional[str] = None, new_value: Optional[str] = None,
                        source: Optional[str] = None) -> None:
    await db.execute(
        """INSERT INTO task_history (task_id, ts, action, field, old_value, new_value, source)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (task_id, now_iso(), action, field,
         str(old_value) if old_value is not None else None,
         str(new_value) if new_value is not None else None,
         source),
    )


async def create_task(data: dict) -> str:
    task_id = new_id("t-")
    now = now_iso()
    source = data.get("source", "telegram_text")
    recurrence_rule = normalize_recurrence_rule(data.get("recurrence_rule") or data.get("recurrence"))
    recurrence_next_at = data.get("recurrence_next_at")
    if recurrence_rule and not recurrence_next_at:
        recurrence_next_at = compute_next_recurrence(data.get("deadline"), recurrence_rule)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO tasks (id, title, description, deadline, priority, status, tags, assignee,
                                  recurrence_rule, recurrence_next_at, recurrence_parent_id,
                                  source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                data.get("title", "Vazifa"),
                data.get("description"),
                data.get("deadline"),
                data.get("priority", "P2"),
                data.get("status", "todo"),
                json.dumps(data.get("tags", []), ensure_ascii=False),
                data.get("assignee"),
                recurrence_rule,
                recurrence_next_at,
                data.get("recurrence_parent_id"),
                source,
                now,
                now,
            ),
        )
        await _log_history(db, task_id, "create", field=None,
                            new_value=data.get("title", "Vazifa"), source=source)
        await db.commit()
    return task_id


async def update_task(task_id: str, data: dict, source: str = "manual") -> bool:
    if not data:
        return False
    # Read old values first so we can log diffs.
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        before = await cur.fetchone()
        if before is None:
            return False

        fields = []
        values = []
        changes: list[tuple[str, str, str]] = []  # (field, old, new)
        for key in (
            "title", "description", "deadline", "priority", "status", "source", "assignee",
            "recurrence_rule", "recurrence_next_at", "recurrence_parent_id",
        ):
            if key in data:
                if key == "recurrence_rule":
                    data[key] = normalize_recurrence_rule(data[key])
                fields.append(f"{key} = ?")
                values.append(data[key])
                changes.append((key, before[key] if key in before.keys() else None, data[key]))
        if "tags" in data:
            fields.append("tags = ?")
            values.append(json.dumps(data["tags"], ensure_ascii=False))
            changes.append(("tags", before["tags"], json.dumps(data["tags"], ensure_ascii=False)))
        if not fields:
            return False
        # Deadline o'zgarsa, eski reminded_at ni tozalash — yangi muddatga qarab
        # qayta eslatma yuborilishi uchun (_task_reminder_sweep'ga yo'l ochish).
        if "deadline" in data and str(before["deadline"]) != str(data.get("deadline")):
            fields.append("reminded_at = ?")
            values.append(None)
        fields.append("updated_at = ?")
        values.append(now_iso())
        values.append(task_id)
        cur = await db.execute(
            f"UPDATE tasks SET {', '.join(fields)} WHERE id = ?", values
        )
        for field, old, new in changes:
            if str(old) != str(new):
                await _log_history(db, task_id, "update", field=field,
                                    old_value=old, new_value=new, source=source)
        await db.commit()
        return cur.rowcount > 0


async def complete_task(task_id: str) -> bool:
    task = await get_task(task_id)
    if not task:
        return False
    if task.get("status") == "done":
        return True
    ok = await update_task(task_id, {"status": "done"}, source="complete")
    if ok:
        await create_next_recurring_task(task)
    return ok


def normalize_recurrence_rule(raw: Any) -> Optional[str]:
    """Normalize LLM/user recurrence wording to a small supported rule set."""
    if not raw:
        return None
    value = str(raw).strip().lower().replace("_", " ").replace("-", " ")
    aliases = {
        "daily": "daily", "every day": "daily", "har kuni": "daily", "kunlik": "daily",
        "weekly": "weekly", "every week": "weekly", "har hafta": "weekly", "haftalik": "weekly",
        "monthly": "monthly", "every month": "monthly", "har oy": "monthly", "oylik": "monthly",
        "quarterly": "quarterly", "every quarter": "quarterly", "har chorak": "quarterly", "choraklik": "quarterly",
        "yearly": "yearly", "annual": "yearly", "every year": "yearly", "har yil": "yearly", "yillik": "yearly",
    }
    return aliases.get(value)


def _coerce_dt(iso: Optional[str]) -> datetime:
    if iso:
        try:
            dt = datetime.fromisoformat(iso)
            return dt if dt.tzinfo else TZ.localize(dt)
        except (TypeError, ValueError):
            pass
    return datetime.now(TZ)


def parse_iso_dt(iso: Optional[str]) -> Optional[datetime]:
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        return dt if dt.tzinfo else TZ.localize(dt)
    except (TypeError, ValueError):
        return None


def _add_months(dt: datetime, months: int) -> datetime:
    month = dt.month - 1 + months
    year = dt.year + month // 12
    month = month % 12 + 1
    day = min(dt.day, monthrange(year, month)[1])
    return dt.replace(year=year, month=month, day=day)


def compute_next_recurrence(base_iso: Optional[str], recurrence_rule: Optional[str]) -> Optional[str]:
    rule = normalize_recurrence_rule(recurrence_rule)
    if not rule:
        return None
    current = _coerce_dt(base_iso)
    now = datetime.now(TZ)

    for _ in range(36):
        if rule == "daily":
            current += timedelta(days=1)
        elif rule == "weekly":
            current += timedelta(weeks=1)
        elif rule == "monthly":
            current = _add_months(current, 1)
        elif rule == "quarterly":
            current = _add_months(current, 3)
        elif rule == "yearly":
            current = _add_months(current, 12)
        else:
            return None
        if current > now:
            return current.isoformat()
    return current.isoformat()


async def create_next_recurring_task(completed_task: dict) -> Optional[str]:
    rule = normalize_recurrence_rule(completed_task.get("recurrence_rule"))
    if not rule:
        return None
    next_deadline = compute_next_recurrence(completed_task.get("deadline"), rule)
    if not next_deadline:
        return None
    next_data = {
        "title": completed_task.get("title") or "Vazifa",
        "description": completed_task.get("description"),
        "deadline": next_deadline,
        "priority": completed_task.get("priority", "P2"),
        "status": "todo",
        "tags": completed_task.get("tags", []),
        "assignee": completed_task.get("assignee"),
        "recurrence_rule": rule,
        "recurrence_next_at": compute_next_recurrence(next_deadline, rule),
        "recurrence_parent_id": completed_task.get("recurrence_parent_id") or completed_task.get("id"),
        "source": "recurring",
    }
    new_task_id = await create_task(next_data)
    await update_task(completed_task["id"], {"recurrence_next_at": next_deadline}, source="recurring")
    return new_task_id


async def delete_task(task_id: str, source: str = "manual") -> bool:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT title FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        title = row["title"] if row else None
        cur = await db.execute("DELETE FROM tasks WHERE id = ?", (task_id,))
        if cur.rowcount > 0:
            await _log_history(db, task_id, "delete", field=None,
                                old_value=title, source=source)
        await db.commit()
        return cur.rowcount > 0


async def get_task_history(task_id: str, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM task_history WHERE task_id = ? ORDER BY id DESC LIMIT ?",
            (task_id, limit),
        )
        return [dict(r) for r in await cur.fetchall()]




async def get_task(task_id: str) -> Optional[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        return _row_to_task(row) if row else None


async def list_tasks(status_in: Optional[list[str]] = None, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status_in:
            placeholders = ",".join("?" * len(status_in))
            cur = await db.execute(
                f"""SELECT * FROM tasks
                    WHERE status IN ({placeholders})
                    ORDER BY
                      CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                      CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
                      deadline ASC
                    LIMIT ?""",
                (*status_in, limit),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]


async def list_today_tasks() -> list[dict]:
    """Tasks whose deadline falls within today's date range only.
    Excludes overdue (yesterday and earlier), future, and undated tasks.
    """
    today = datetime.now(TZ).date()
    start_of_day = TZ.localize(datetime.combine(today, datetime.min.time())).isoformat()
    end_of_day = TZ.localize(datetime.combine(today, datetime.max.time())).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress')
                 AND deadline IS NOT NULL
                 AND deadline >= ?
                 AND deadline <= ?
               ORDER BY
                 CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                 deadline ASC""",
            (start_of_day, end_of_day),
        )
        rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]


async def list_overdue_tasks() -> list[dict]:
    now = now_iso()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress')
                 AND deadline IS NOT NULL
                 AND deadline < ?
               ORDER BY deadline ASC""",
            (now,),
        )
        rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]


async def list_tasks_done_today() -> list[dict]:
    today_start = TZ.localize(datetime.combine(datetime.now(TZ).date(), datetime.min.time())).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status = 'done' AND updated_at >= ?
               ORDER BY updated_at DESC""",
            (today_start,),
        )
        rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]


async def list_due_in_window(start_iso: str, end_iso: str) -> list[dict]:
    """Returns P0/P1 tasks due within [start_iso, end_iso] that have NOT been
    reminded yet. Once `mark_task_reminded` writes reminded_at, the task is
    permanently excluded — until `update_task` resets reminded_at (which it
    does automatically when the deadline changes).

    Avvalgi mantiqda `reminded_at < start_iso` shart ishlatilgan, lekin
    sweep har 5 daqiqada window'ni oldinga suradi → reminded_at darrov
    start_iso'dan oldin qoladi va bir xil eslatma takror yuborilaverardi.
    """
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress')
                 AND deadline IS NOT NULL
                 AND deadline >= ? AND deadline <= ?
                 AND reminded_at IS NULL
                 AND priority IN ('P0','P1')
               ORDER BY deadline ASC""",
            (start_iso, end_iso),
        )
        rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]


async def list_unassigned_tasks(limit: int = 50) -> list[dict]:
    """Tasks with no assignee (or assigned to 'belgilanmagan')."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress')
                 AND (assignee IS NULL OR TRIM(assignee) = '' OR LOWER(assignee) = 'belgilanmagan')
               ORDER BY
                 CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
                 deadline ASC
               LIMIT ?""",
            (limit,),
        )
        return [_row_to_task(r) for r in await cur.fetchall()]


async def list_tasks_without_deadline(limit: int = 50) -> list[dict]:
    """Active tasks with no deadline set."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress') AND deadline IS NULL
               ORDER BY created_at DESC LIMIT ?""",
            (limit,),
        )
        return [_row_to_task(r) for r in await cur.fetchall()]


async def list_tasks_due_within(hours: int) -> list[dict]:
    """Active tasks whose deadline is within the next N hours from now (not overdue)."""
    now = datetime.now(TZ)
    later = (now + timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress')
                 AND deadline IS NOT NULL
                 AND deadline >= ? AND deadline <= ?
               ORDER BY deadline ASC""",
            (now.isoformat(), later),
        )
        return [_row_to_task(r) for r in await cur.fetchall()]


async def assignee_load_map() -> dict[str, dict]:
    """Per-assignee statistics: active count, urgent/important counts, overdue.

    Key: assignee name (string). Includes 'belgilanmagan' bucket for unassigned tasks.
    """
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT id, title, priority, status, assignee, deadline
               FROM tasks WHERE status IN ('todo','in_progress')""",
        )
        rows = await cur.fetchall()

    now_iso_str = now_iso()
    out: dict[str, dict] = {}
    for r in rows:
        key = (r["assignee"] or "").strip() or "belgilanmagan"
        if key.lower() in ("men", "oʻzim", "ozim", "o'zim"):
            key = "Men"
        d = out.setdefault(key, {
            "name": key, "active": 0, "urgent": 0, "important": 0,
            "overdue": 0, "next_deadline": None,
        })
        d["active"] += 1
        p = r["priority"]
        if p == "P0":
            d["urgent"] += 1
        elif p == "P1":
            d["important"] += 1
        if r["deadline"] and r["deadline"] < now_iso_str:
            d["overdue"] += 1
        # Track earliest upcoming deadline
        if r["deadline"] and r["deadline"] >= now_iso_str:
            if d["next_deadline"] is None or r["deadline"] < d["next_deadline"]:
                d["next_deadline"] = r["deadline"]
    return out


async def assignee_profile(name: str) -> dict:
    """Detailed profile for a single assignee.

    Returns: active/done/overdue/urgent/important counts, completion_rate,
    avg_closing_time (hours), tasks (top 10 by priority+deadline).
    """
    name_lc = name.strip().lower()
    if name_lc in ("men", "oʻzim", "ozim", "o'zim"):
        name_lc = "men"
        match_clause = "LOWER(TRIM(assignee)) IN ('men', 'oʻzim', 'ozim', \"o'zim\")"
    elif name_lc == "belgilanmagan":
        match_clause = "(assignee IS NULL OR TRIM(assignee) = '' OR LOWER(assignee) = 'belgilanmagan')"
    else:
        match_clause = "LOWER(TRIM(assignee)) = ?"

    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        params = () if "?" not in match_clause else (name_lc,)
        cur = await db.execute(
            f"""SELECT * FROM tasks WHERE {match_clause}""",
            params,
        )
        rows = await cur.fetchall()

    active = []
    completed = []
    overdue = 0
    urgent = 0
    important = 0
    durations_hours = []
    now_iso_str = now_iso()
    for r in rows:
        rd = _row_to_task(r)
        if rd["status"] == "done":
            completed.append(rd)
            try:
                c = datetime.fromisoformat(rd["created_at"])
                u = datetime.fromisoformat(rd["updated_at"])
                durations_hours.append((u - c).total_seconds() / 3600.0)
            except (ValueError, TypeError):
                pass
        elif rd["status"] in ("todo", "in_progress"):
            active.append(rd)
            if rd["priority"] == "P0":
                urgent += 1
            elif rd["priority"] == "P1":
                important += 1
            if rd.get("deadline") and rd["deadline"] < now_iso_str:
                overdue += 1

    total = len(active) + len(completed)
    completion_rate = round((len(completed) / total) * 100, 1) if total else 0.0
    avg_hours = round(sum(durations_hours) / len(durations_hours), 1) if durations_hours else 0.0
    # Sort active tasks by priority then deadline
    active.sort(key=lambda t: (
        {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(t.get("priority", "P2"), 2),
        t.get("deadline") or "9999",
    ))
    return {
        "name": name,
        "active": len(active),
        "completed": len(completed),
        "overdue": overdue,
        "urgent": urgent,
        "important": important,
        "completion_rate": completion_rate,
        "avg_closing_hours": avg_hours,
        "tasks": active[:10],
    }


async def list_delegated_open_tasks(limit: int = 20) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress')
                 AND assignee IS NOT NULL
                 AND TRIM(LOWER(assignee)) NOT IN ('', 'men', 'oʻzim', 'ozim')
               ORDER BY
                 CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
                 deadline ASC
               LIMIT ?""",
            (limit,),
        )
        return [_row_to_task(r) for r in await cur.fetchall()]


async def list_recurring_tasks(limit: int = 30) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE recurrence_rule IS NOT NULL
                 AND status IN ('todo','in_progress')
               ORDER BY
                 CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
                 deadline ASC
               LIMIT ?""",
            (limit,),
        )
        return [_row_to_task(r) for r in await cur.fetchall()]


async def mark_task_reminded(task_id: str) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("UPDATE tasks SET reminded_at = ? WHERE id = ?", (now_iso(), task_id))
        await db.commit()


def _row_to_task(row) -> dict:
    d = dict(row)
    if d.get("tags"):
        try:
            d["tags"] = json.loads(d["tags"])
        except json.JSONDecodeError:
            d["tags"] = []
    else:
        d["tags"] = []
    return d


# ─────────────────────────────────────────── REMINDERS ───────────────────────────────────────────

async def create_reminder(data: dict) -> str:
    reminder_id = new_id("r-")
    now = now_iso()
    recurrence_rule = normalize_recurrence_rule(data.get("recurrence_rule") or data.get("recurrence"))
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO reminders (id, title, note, remind_at, status, recurrence_rule,
                                      task_id, meeting_id, source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                reminder_id,
                data.get("title", "Eslatma"),
                data.get("note"),
                data.get("remind_at"),
                data.get("status", "scheduled"),
                recurrence_rule,
                data.get("task_id"),
                data.get("meeting_id"),
                data.get("source", "manual"),
                now,
                now,
            ),
        )
        await db.commit()
    return reminder_id


async def get_reminder(reminder_id: str) -> Optional[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        row = await cur.fetchone()
        return _row_to_reminder(row) if row else None


async def update_reminder(reminder_id: str, data: dict) -> bool:
    if not data:
        return False
    allowed = {
        "title", "note", "remind_at", "status", "recurrence_rule", "task_id",
        "meeting_id", "source", "snooze_count", "sent_at",
    }
    fields = []
    values: list[Any] = []
    for key, value in data.items():
        if key not in allowed:
            continue
        if key == "recurrence_rule":
            value = normalize_recurrence_rule(value)
        fields.append(f"{key} = ?")
        values.append(value)
    if not fields:
        return False
    fields.append("updated_at = ?")
    values.append(now_iso())
    values.append(reminder_id)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(f"UPDATE reminders SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()
        return cur.rowcount > 0


async def cancel_reminder(reminder_id: str) -> bool:
    return await update_reminder(reminder_id, {"status": "cancelled"})


async def complete_reminder(reminder_id: str) -> bool:
    return await update_reminder(reminder_id, {"status": "done"})


async def snooze_reminder(reminder_id: str, remind_at_iso: str) -> bool:
    reminder = await get_reminder(reminder_id)
    if not reminder:
        return False
    return await update_reminder(reminder_id, {
        "remind_at": remind_at_iso,
        "status": "scheduled",
        "sent_at": None,
        "snooze_count": int(reminder.get("snooze_count") or 0) + 1,
    })


async def list_reminders(status_in: Optional[list[str]] = None, limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status_in:
            placeholders = ",".join("?" * len(status_in))
            cur = await db.execute(
                f"""SELECT * FROM reminders
                    WHERE status IN ({placeholders})
                    ORDER BY
                      CASE WHEN remind_at < ? AND status = 'scheduled' THEN 0 ELSE 1 END,
                      remind_at ASC
                    LIMIT ?""",
                (*status_in, now_iso(), limit),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM reminders WHERE status != 'cancelled' ORDER BY remind_at DESC LIMIT ?",
                (limit,),
            )
        return [_row_to_reminder(r) for r in await cur.fetchall()]


async def list_today_reminders(limit: int = 50) -> list[dict]:
    today = datetime.now(TZ).date()
    start = TZ.localize(datetime.combine(today, datetime.min.time())).isoformat()
    end = TZ.localize(datetime.combine(today, datetime.max.time())).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM reminders
               WHERE status = 'scheduled'
                 AND remind_at BETWEEN ? AND ?
               ORDER BY remind_at ASC
               LIMIT ?""",
            (start, end, limit),
        )
        return [_row_to_reminder(r) for r in await cur.fetchall()]


async def list_due_reminders(now_iso_value: Optional[str] = None, limit: int = 20) -> list[dict]:
    current = now_iso_value or now_iso()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM reminders
               WHERE status = 'scheduled'
                 AND remind_at <= ?
               ORDER BY remind_at ASC
               LIMIT ?""",
            (current, limit),
        )
        return [_row_to_reminder(r) for r in await cur.fetchall()]


async def search_reminders(query: str, limit: int = 30) -> list[dict]:
    q = f"%{query.strip().lower()}%"
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM reminders
               WHERE status != 'cancelled'
                 AND (LOWER(title) LIKE ? OR LOWER(COALESCE(note,'')) LIKE ?)
               ORDER BY remind_at DESC LIMIT ?""",
            (q, q, limit),
        )
        return [_row_to_reminder(r) for r in await cur.fetchall()]


async def reminders_overview() -> dict:
    now = datetime.now(TZ)
    today_start = TZ.localize(datetime.combine(now.date(), datetime.min.time())).isoformat()
    today_end = TZ.localize(datetime.combine(now.date(), datetime.max.time())).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        async def count(sql: str, params: tuple = ()) -> int:
            cur = await db.execute(sql, params)
            row = await cur.fetchone()
            return int(row[0] or 0)

        scheduled = await count("SELECT COUNT(*) FROM reminders WHERE status = 'scheduled'")
        overdue = await count(
            "SELECT COUNT(*) FROM reminders WHERE status = 'scheduled' AND remind_at < ?",
            (now.isoformat(),),
        )
        today = await count(
            """SELECT COUNT(*) FROM reminders
               WHERE status = 'scheduled' AND remind_at BETWEEN ? AND ?""",
            (today_start, today_end),
        )
        sent = await count("SELECT COUNT(*) FROM reminders WHERE status = 'sent'")
        recurring = await count(
            "SELECT COUNT(*) FROM reminders WHERE status = 'scheduled' AND recurrence_rule IS NOT NULL"
        )
    return {
        "scheduled": scheduled,
        "overdue": overdue,
        "today": today,
        "sent": sent,
        "recurring": recurring,
    }


async def mark_reminder_sent(reminder_id: str) -> bool:
    reminder = await get_reminder(reminder_id)
    if not reminder:
        return False
    sent_at = now_iso()
    rule = normalize_recurrence_rule(reminder.get("recurrence_rule"))
    next_at = compute_next_recurrence(reminder.get("remind_at"), rule) if rule else None
    if next_at:
        return await update_reminder(reminder_id, {
            "remind_at": next_at,
            "status": "scheduled",
            "sent_at": sent_at,
        })
    return await update_reminder(reminder_id, {
        "status": "sent",
        "sent_at": sent_at,
    })


def _row_to_reminder(row) -> dict:
    return dict(row)


# ─────────────────────────────────────────── MEETINGS ───────────────────────────────────────────

async def create_meeting(data: dict) -> str:
    meeting_id = new_id("m-")
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO meetings (id, title, datetime_start, datetime_end, participants,
                                     location_or_link, agenda, prep_notes, follow_up_actions, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                meeting_id,
                data.get("title", "Uchrashuv"),
                data.get("datetime_start"),
                data.get("datetime_end"),
                json.dumps(data.get("participants", []), ensure_ascii=False),
                data.get("location_or_link"),
                data.get("agenda"),
                data.get("prep_notes"),
                json.dumps(data.get("follow_up_actions", []), ensure_ascii=False),
                now_iso(),
            ),
        )
        await db.commit()
    return meeting_id


async def update_meeting(meeting_id: str, data: dict) -> bool:
    if not data:
        return False
    allowed = {
        "title", "datetime_start", "datetime_end", "participants", "location_or_link",
        "agenda", "prep_notes", "follow_up_actions", "reminded_at", "prep_sent_at",
        "followup_sent_at", "icloud_uid",
    }
    fields = []
    values: list[Any] = []
    for key, value in data.items():
        if key not in allowed:
            continue
        fields.append(f"{key} = ?")
        if key in ("participants", "follow_up_actions") and isinstance(value, list):
            value = json.dumps(value, ensure_ascii=False)
        values.append(value)
    if not fields:
        return False
    values.append(meeting_id)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(f"UPDATE meetings SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()
        return cur.rowcount > 0


async def cancel_meeting(meeting_id: str) -> bool:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("DELETE FROM meetings WHERE id = ?", (meeting_id,))
        await db.commit()
        return cur.rowcount > 0


async def list_today_meetings() -> list[dict]:
    today = datetime.now(TZ).date()
    start = TZ.localize(datetime.combine(today, datetime.min.time())).isoformat()
    end = TZ.localize(datetime.combine(today, datetime.max.time())).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE datetime_start BETWEEN ? AND ?
               ORDER BY datetime_start ASC""",
            (start, end),
        )
        rows = await cur.fetchall()
        return [_row_to_meeting(r) for r in rows]


async def list_upcoming_meetings(within_minutes: int = 15) -> list[dict]:
    now = datetime.now(TZ)
    soon = now + timedelta(minutes=within_minutes)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE datetime_start BETWEEN ? AND ?
                 AND (reminded_at IS NULL OR reminded_at < ?)
               ORDER BY datetime_start ASC""",
            (now.isoformat(), soon.isoformat(), now.isoformat()),
        )
        rows = await cur.fetchall()
        return [_row_to_meeting(r) for r in rows]


async def mark_meeting_reminded(meeting_id: str) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("UPDATE meetings SET reminded_at = ? WHERE id = ?", (now_iso(), meeting_id))
        await db.commit()


async def mark_meeting_prep_sent(meeting_id: str) -> None:
    await update_meeting(meeting_id, {"prep_sent_at": now_iso()})


async def mark_meeting_followup_sent(meeting_id: str) -> None:
    await update_meeting(meeting_id, {"followup_sent_at": now_iso()})


async def list_meetings_needing_prep(window_minutes: int = 60) -> list[dict]:
    now = datetime.now(TZ)
    soon = now + timedelta(minutes=window_minutes)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE datetime_start BETWEEN ? AND ?
                 AND prep_sent_at IS NULL
               ORDER BY datetime_start ASC""",
            (now.isoformat(), soon.isoformat()),
        )
        return [_row_to_meeting(r) for r in await cur.fetchall()]


async def list_meetings_needing_followup(min_age_minutes: int = 30, max_age_hours: int = 24) -> list[dict]:
    now = datetime.now(TZ)
    earliest = now - timedelta(hours=max_age_hours)
    latest = now - timedelta(minutes=min_age_minutes)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE COALESCE(datetime_end, datetime_start) BETWEEN ? AND ?
                 AND followup_sent_at IS NULL
               ORDER BY datetime_start ASC""",
            (earliest.isoformat(), latest.isoformat()),
        )
        return [_row_to_meeting(r) for r in await cur.fetchall()]


async def get_meeting(meeting_id: str) -> Optional[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
        row = await cur.fetchone()
        return _row_to_meeting(row) if row else None


async def list_unreminded_future_meetings() -> list[dict]:
    now = now_iso()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE datetime_start > ?
                 AND reminded_at IS NULL
               ORDER BY datetime_start ASC""",
            (now,),
        )
        rows = await cur.fetchall()
        return [_row_to_meeting(r) for r in rows]


async def next_first_meeting() -> Optional[dict]:
    now = now_iso()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM meetings WHERE datetime_start >= ?
               ORDER BY datetime_start ASC LIMIT 1""",
            (now,),
        )
        row = await cur.fetchone()
        return _row_to_meeting(row) if row else None


def _row_to_meeting(row) -> dict:
    d = dict(row)
    for key in ("participants", "follow_up_actions"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except json.JSONDecodeError:
                d[key] = []
        else:
            d[key] = []
    return d


async def list_meetings_in_window(start_iso: str, end_iso: str) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE datetime_start BETWEEN ? AND ?
               ORDER BY datetime_start ASC""",
            (start_iso, end_iso),
        )
        rows = await cur.fetchall()
        return [_row_to_meeting(r) for r in rows]


async def search_all(query: str, limit: int = 30) -> dict:
    """Full-text-ish search across tasks, reminders, meetings, contacts. Uses LIKE for simplicity."""
    q = f"%{query.strip().lower()}%"
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE LOWER(title) LIKE ?
                  OR LOWER(COALESCE(description,'')) LIKE ?
                  OR LOWER(COALESCE(tags,'')) LIKE ?
                  OR LOWER(COALESCE(assignee,'')) LIKE ?
               ORDER BY created_at DESC LIMIT ?""",
            (q, q, q, q, limit),
        )
        tasks = [_row_to_task(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE LOWER(title) LIKE ? OR LOWER(COALESCE(agenda,'')) LIKE ?
                  OR LOWER(COALESCE(participants,'')) LIKE ? OR LOWER(COALESCE(location_or_link,'')) LIKE ?
               ORDER BY datetime_start DESC LIMIT ?""",
            (q, q, q, q, limit),
        )
        meetings = [_row_to_meeting(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT * FROM contacts
               WHERE LOWER(name) LIKE ? OR LOWER(COALESCE(role,'')) LIKE ?
               ORDER BY last_interaction DESC LIMIT ?""",
            (q, q, limit),
        )
        contacts = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT * FROM reminders
               WHERE status != 'cancelled'
                 AND (LOWER(title) LIKE ? OR LOWER(COALESCE(note,'')) LIKE ?)
               ORDER BY remind_at DESC LIMIT ?""",
            (q, q, limit),
        )
        reminders = [_row_to_reminder(r) for r in await cur.fetchall()]

    return {"tasks": tasks, "meetings": meetings, "contacts": contacts, "reminders": reminders,
            "total": len(tasks) + len(meetings) + len(contacts) + len(reminders)}


async def list_meetings_in_month(year: int, month: int) -> list[dict]:
    """All meetings whose start falls inside the given calendar month."""
    from calendar import monthrange
    start_iso = TZ.localize(datetime(year, month, 1)).isoformat()
    last_day = monthrange(year, month)[1]
    end_iso = TZ.localize(datetime(year, month, last_day, 23, 59, 59)).isoformat()
    return await list_meetings_in_window(start_iso, end_iso)


async def list_tasks_in_month(year: int, month: int) -> list[dict]:
    """Tasks with deadlines in the given calendar month."""
    from calendar import monthrange
    start_iso = TZ.localize(datetime(year, month, 1)).isoformat()
    last_day = monthrange(year, month)[1]
    end_iso = TZ.localize(datetime(year, month, last_day, 23, 59, 59)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE deadline IS NOT NULL AND deadline >= ? AND deadline <= ?
               ORDER BY deadline ASC""",
            (start_iso, end_iso),
        )
        return [_row_to_task(r) for r in await cur.fetchall()]


async def weekly_stats(week_start_iso: str) -> dict:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row

        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE created_at >= ?",
            (week_start_iso,),
        )
        created = (await cur.fetchone())["n"]

        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM tasks WHERE status = 'done' AND updated_at >= ?",
            (week_start_iso,),
        )
        done = (await cur.fetchone())["n"]

        cur = await db.execute(
            "SELECT COUNT(*) AS n FROM meetings WHERE datetime_start >= ?",
            (week_start_iso,),
        )
        meetings = (await cur.fetchone())["n"]

        cur = await db.execute(
            "SELECT priority, COUNT(*) AS n FROM tasks WHERE status IN ('todo','in_progress') GROUP BY priority"
        )
        priority_rows = await cur.fetchall()
        by_priority = {r["priority"]: r["n"] for r in priority_rows}

    completion_rate = round(done / created * 100, 1) if created > 0 else 0.0

    return {
        "tasks_created": created,
        "tasks_done": done,
        "meetings": meetings,
        "completion_rate_pct": completion_rate,
        "by_priority": by_priority,
    }


async def executive_stats(days: int = 7) -> dict:
    """Return decision-grade operational metrics for /stats and /report."""
    now = datetime.now(TZ)
    start = now - timedelta(days=days)
    today_start = TZ.localize(datetime.combine(now.date(), datetime.min.time()))
    tomorrow_start = today_start + timedelta(days=1)
    next_24 = now + timedelta(hours=24)
    next_48 = now + timedelta(hours=48)
    next_7 = now + timedelta(days=7)

    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row

        async def count(sql: str, params: tuple = ()) -> int:
            cur = await db.execute(sql, params)
            row = await cur.fetchone()
            return int(row[0] or 0)

        tasks_created = await count("SELECT COUNT(*) FROM tasks WHERE created_at >= ?", (start.isoformat(),))
        tasks_done = await count("SELECT COUNT(*) FROM tasks WHERE status = 'done' AND updated_at >= ?", (start.isoformat(),))
        active_count = await count("SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress')")
        overdue_count = await count(
            """SELECT COUNT(*) FROM tasks
               WHERE status IN ('todo','in_progress') AND deadline IS NOT NULL AND deadline < ?""",
            (now.isoformat(),),
        )
        no_deadline_count = await count(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress') AND deadline IS NULL"
        )
        due_24_count = await count(
            """SELECT COUNT(*) FROM tasks
               WHERE status IN ('todo','in_progress') AND deadline BETWEEN ? AND ?""",
            (now.isoformat(), next_24.isoformat()),
        )
        due_48_count = await count(
            """SELECT COUNT(*) FROM tasks
               WHERE status IN ('todo','in_progress') AND deadline BETWEEN ? AND ?""",
            (now.isoformat(), next_48.isoformat()),
        )
        due_7_count = await count(
            """SELECT COUNT(*) FROM tasks
               WHERE status IN ('todo','in_progress') AND deadline BETWEEN ? AND ?""",
            (now.isoformat(), next_7.isoformat()),
        )
        recurring_count = await count(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress') AND recurrence_rule IS NOT NULL"
        )

        cur = await db.execute(
            """SELECT priority, COUNT(*) AS n FROM tasks
               WHERE status IN ('todo','in_progress') GROUP BY priority"""
        )
        by_priority = {r["priority"]: r["n"] for r in await cur.fetchall()}

        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress') AND deadline IS NOT NULL AND deadline < ?
               ORDER BY deadline ASC LIMIT 5""",
            (now.isoformat(),),
        )
        overdue_tasks = [_row_to_task(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress') AND deadline IS NULL
               ORDER BY
                 CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                 created_at ASC LIMIT 5"""
        )
        no_deadline_tasks = [_row_to_task(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress') AND deadline BETWEEN ? AND ?
               ORDER BY deadline ASC LIMIT 8""",
            (now.isoformat(), next_48.isoformat()),
        )
        risk_tasks = [_row_to_task(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT assignee, COUNT(*) AS total,
                      SUM(CASE WHEN deadline IS NOT NULL AND deadline < ? THEN 1 ELSE 0 END) AS overdue
               FROM tasks
               WHERE status IN ('todo','in_progress')
                 AND assignee IS NOT NULL
                 AND TRIM(LOWER(assignee)) NOT IN ('', 'men', 'oʻzim', 'ozim')
               GROUP BY assignee ORDER BY total DESC LIMIT 8""",
            (now.isoformat(),),
        )
        delegation = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status = 'done' AND updated_at >= ?
               ORDER BY updated_at DESC LIMIT 200""",
            (start.isoformat(),),
        )
        done_rows = [_row_to_task(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE datetime_start BETWEEN ? AND ?
               ORDER BY datetime_start ASC""",
            (start.isoformat(), now.isoformat()),
        )
        period_meetings = [_row_to_meeting(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE datetime_start BETWEEN ? AND ?
               ORDER BY datetime_start ASC""",
            (now.isoformat(), next_7.isoformat()),
        )
        upcoming_meetings = [_row_to_meeting(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT DATE(created_at) AS day, COUNT(*) AS created
               FROM tasks WHERE created_at >= ?
               GROUP BY DATE(created_at)""",
            ((now - timedelta(days=6)).isoformat(),),
        )
        created_by_day = {r["day"]: r["created"] for r in await cur.fetchall()}

        cur = await db.execute(
            """SELECT DATE(updated_at) AS day, COUNT(*) AS done
               FROM tasks WHERE status = 'done' AND updated_at >= ?
               GROUP BY DATE(updated_at)""",
            ((now - timedelta(days=6)).isoformat(),),
        )
        done_by_day = {r["day"]: r["done"] for r in await cur.fetchall()}

        cur = await db.execute(
            """SELECT provider, COUNT(*) AS calls, COALESCE(SUM(estimated_cost_usd), 0) AS cost
               FROM llm_audit_log WHERE ts >= ?
               GROUP BY provider ORDER BY calls DESC""",
            (start.isoformat(),),
        )
        llm_by_provider = [dict(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT COUNT(*) AS total,
                      SUM(CASE WHEN user_action = 'accepted' THEN 1 ELSE 0 END) AS accepted,
                      SUM(CASE WHEN user_action = 'dismissed' THEN 1 ELSE 0 END) AS dismissed
               FROM insights_log WHERE ts >= ?""",
            (start.isoformat(),),
        )
        insight_row = dict(await cur.fetchone())

    completion_rate = round((tasks_done / tasks_created) * 100, 1) if tasks_created else 0.0

    durations = []
    for task in done_rows:
        created_at = parse_iso_dt(task.get("created_at"))
        updated_at = parse_iso_dt(task.get("updated_at"))
        if created_at and updated_at and updated_at >= created_at:
            durations.append((updated_at - created_at).total_seconds() / 3600)
    avg_completion_hours = round(sum(durations) / len(durations), 1) if durations else 0.0

    def meeting_hours(meetings: list[dict]) -> float:
        total = 0.0
        for meeting in meetings:
            start_dt = parse_iso_dt(meeting.get("datetime_start"))
            end_dt = parse_iso_dt(meeting.get("datetime_end"))
            if start_dt and end_dt and end_dt > start_dt:
                total += (end_dt - start_dt).total_seconds() / 3600
            elif start_dt:
                total += 1.0
        return round(total, 1)

    meeting_action_items = sum(len(m.get("follow_up_actions") or []) for m in period_meetings)
    meetings_with_prep = sum(1 for m in period_meetings if m.get("prep_notes") or m.get("prep_sent_at"))
    meetings_with_followup = sum(1 for m in period_meetings if m.get("follow_up_actions") or m.get("followup_sent_at"))

    trend = []
    for i in range(6, -1, -1):
        d = (now - timedelta(days=i)).date().isoformat()
        trend.append({"day": d, "created": created_by_day.get(d, 0), "done": done_by_day.get(d, 0)})

    risk_score = min(
        100,
        overdue_count * 18
        + by_priority.get("P0", 0) * 12
        + by_priority.get("P1", 0) * 6
        + due_24_count * 8
        + no_deadline_count * 3,
    )

    return {
        "period_days": days,
        "period_start": start.isoformat(),
        "period_end": now.isoformat(),
        "tasks": {
            "created": tasks_created,
            "done": tasks_done,
            "active": active_count,
            "overdue": overdue_count,
            "no_deadline": no_deadline_count,
            "due_24h": due_24_count,
            "due_48h": due_48_count,
            "due_7d": due_7_count,
            "recurring": recurring_count,
            "completion_rate_pct": completion_rate,
            "avg_completion_hours": avg_completion_hours,
            "by_priority": by_priority,
            "overdue_tasks": overdue_tasks,
            "no_deadline_tasks": no_deadline_tasks,
            "risk_tasks": risk_tasks,
        },
        "delegation": delegation,
        "meetings": {
            "count": len(period_meetings),
            "hours": meeting_hours(period_meetings),
            "prep_count": meetings_with_prep,
            "followup_count": meetings_with_followup,
            "action_items": meeting_action_items,
            "upcoming_7d_count": len(upcoming_meetings),
            "upcoming_7d_hours": meeting_hours(upcoming_meetings),
        },
        "trend": trend,
        "llm": {
            "providers": llm_by_provider,
            "calls": sum(r["calls"] for r in llm_by_provider),
            "cost": round(sum(float(r["cost"] or 0) for r in llm_by_provider), 4),
        },
        "insights": {
            "total": int(insight_row.get("total") or 0),
            "accepted": int(insight_row.get("accepted") or 0),
            "dismissed": int(insight_row.get("dismissed") or 0),
        },
        "risk_score": risk_score,
    }


# ─────────────────────────────────────────── CONTACTS ───────────────────────────────────────────

async def save_contact(data: dict) -> str:
    name = data.get("name")
    if not name:
        return ""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM contacts WHERE name = ?", (name,))
        existing = await cur.fetchone()
        if existing:
            contact_id = existing["id"]
            fields = []
            values: list[Any] = []
            for key in ("role", "formality_level", "preferred_channel"):
                if key in data and data[key] is not None:
                    fields.append(f"{key} = ?")
                    values.append(data[key])
            fields.append("last_interaction = ?")
            values.append(now_iso())
            values.append(contact_id)
            await db.execute(f"UPDATE contacts SET {', '.join(fields)} WHERE id = ?", values)
        else:
            contact_id = new_id("c-")
            await db.execute(
                """INSERT INTO contacts (id, name, role, formality_level, preferred_channel,
                                         last_interaction, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    contact_id,
                    name,
                    data.get("role"),
                    data.get("formality_level", 3),
                    data.get("preferred_channel"),
                    now_iso(),
                    now_iso(),
                ),
            )
        await db.commit()
    return contact_id


async def list_contacts() -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM contacts
               ORDER BY CASE WHEN last_interaction IS NULL THEN 1 ELSE 0 END,
                        last_interaction DESC
               LIMIT 30"""
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────── CORRECTIONS ───────────────────────────────────────────

async def save_correction(data: dict) -> str:
    cid = new_id("corr-")
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO corrections (id, context, correction, reason, created_at)
               VALUES (?, ?, ?, ?, ?)""",
            (cid, data.get("context"), data.get("correction"), data.get("reason"), now_iso()),
        )
        await db.commit()
    return cid


async def list_recent_corrections(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM corrections ORDER BY created_at DESC LIMIT ?", (limit,)
        )
        rows = await cur.fetchall()
        return [dict(r) for r in rows]


# ─────────────────────────────────────────── CONVERSATION HISTORY ───────────────────────────────────────────

DEFAULT_SETTINGS = {
    "notifications_enabled": True,
    "language": "uz",
    "morning_briefing_time": "08:00",
    "evening_summary_time": "18:00",
    "meeting_reminder_min": 15,
    "task_reminder_hours": 2,
}


async def get_settings() -> dict:
    """Return user settings merged with defaults."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT data FROM principal_profile WHERE key = 'settings'")
        row = await cur.fetchone()
    saved: dict = {}
    if row and row["data"]:
        try:
            saved = json.loads(row["data"])
        except json.JSONDecodeError:
            saved = {}
    merged = {**DEFAULT_SETTINGS, **saved}
    return merged


async def set_setting(key: str, value) -> None:
    """Update a single setting key."""
    current = await get_settings()
    current[key] = value
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO principal_profile (key, data, updated_at) VALUES ('settings', ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET data=excluded.data, updated_at=excluded.updated_at",
            (json.dumps(current, ensure_ascii=False), now_iso()),
        )
        await db.commit()


async def append_message(role: str, content: str) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO conversation_history (role, content, created_at) VALUES (?, ?, ?)",
            (role, content, now_iso()),
        )
        await db.commit()


async def recent_messages(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT role, content FROM conversation_history ORDER BY id DESC LIMIT ?",
            (limit,),
        )
        rows = await cur.fetchall()
        return list(reversed([dict(r) for r in rows]))


async def trim_history(keep: int = 200) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM conversation_history")
        (total,) = await cur.fetchone()
        if total > keep:
            await db.execute(
                f"""DELETE FROM conversation_history
                    WHERE id IN (
                        SELECT id FROM conversation_history
                        ORDER BY id ASC LIMIT ?
                    )""",
                (total - keep,),
            )
            await db.commit()


# ─────────────────────────────────────────── PLANS ───────────────────────────────────────────

async def save_plan(input_text: str, output_text: str, task_ids: Optional[list] = None) -> str:
    """Save an executive planning session for later review and learning."""
    plan_id = new_id("p-")
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO plans (id, created_at, input_text, output_text, task_ids)
               VALUES (?, ?, ?, ?, ?)""",
            (plan_id, now_iso(), input_text, output_text,
             json.dumps(task_ids or [], ensure_ascii=False)),
        )
        await db.commit()
    return plan_id


async def mark_plan_accepted(plan_id: str) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("UPDATE plans SET accepted = 1 WHERE id = ?", (plan_id,))
        await db.commit()


async def list_recent_plans(limit: int = 10) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT * FROM plans ORDER BY created_at DESC LIMIT ?",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def list_plans_due_followup() -> list[dict]:
    """Plans created 48h ago that haven't had follow-up yet."""
    cutoff = (datetime.now(TZ) - timedelta(hours=48)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM plans
               WHERE created_at <= ? AND follow_up_asked_at IS NULL AND accepted = 1
               ORDER BY created_at ASC LIMIT 5""",
            (cutoff,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def mark_plan_followup_asked(plan_id: str) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            "UPDATE plans SET follow_up_asked_at = ? WHERE id = ?",
            (now_iso(), plan_id),
        )
        await db.commit()


# ─────────────────────────────────────────── INSIGHTS ───────────────────────────────────────────

async def log_insight(insight_type: str, payload: dict) -> int:
    """Record a proactive insight the bot surfaced. Returns the row id."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            """INSERT INTO insights_log (ts, insight_type, payload)
               VALUES (?, ?, ?)""",
            (now_iso(), insight_type, json.dumps(payload, ensure_ascii=False)),
        )
        await db.commit()
        return cur.lastrowid


async def mark_insight_action(insight_id: int, action: str) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            "UPDATE insights_log SET user_action = ?, acted_at = ? WHERE id = ?",
            (action, now_iso(), insight_id),
        )
        await db.commit()


async def recent_insight_pattern(insight_type: str, hours: int = 24) -> int:
    """How many times we've surfaced this insight type recently — used to avoid repeats."""
    cutoff = (datetime.now(TZ) - timedelta(hours=hours)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM insights_log WHERE insight_type = ? AND ts >= ?",
            (insight_type, cutoff),
        )
        return (await cur.fetchone())[0]


async def insight_acceptance_rate(insight_type: str) -> float:
    """How often the principal acts on this insight type. Used for learning."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            """SELECT
                  COUNT(*) FILTER (WHERE user_action = 'accepted') AS accepted,
                  COUNT(*) FILTER (WHERE user_action IS NOT NULL) AS responded
               FROM insights_log WHERE insight_type = ?""",
            (insight_type,),
        )
        row = await cur.fetchone()
        accepted, responded = row[0] or 0, row[1] or 0
        return (accepted / responded) if responded else 0.5  # neutral when no data


# ─────────────────────────────────────────── iCLOUD RETRY QUEUE ───────────────────────────────────────────

# Exponential backoff: 1min → 5min → 15min → 1h → 4h → give up
_RETRY_BACKOFFS_SECONDS = [60, 300, 900, 3600, 14400]


async def enqueue_icloud_retry(operation: str, meeting_id: Optional[str],
                                payload: dict, error: str) -> None:
    """Add a failed iCloud operation to the retry queue."""
    next_at = (datetime.now(TZ) + timedelta(seconds=_RETRY_BACKOFFS_SECONDS[0])).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO icloud_retry_queue
               (operation, meeting_id, payload, attempts, next_attempt_at, last_error, created_at)
               VALUES (?, ?, ?, 0, ?, ?, ?)""",
            (operation, meeting_id, json.dumps(payload, ensure_ascii=False),
             next_at, error[:500], now_iso()),
        )
        await db.commit()


async def list_due_icloud_retries(limit: int = 20) -> list[dict]:
    now = now_iso()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM icloud_retry_queue
               WHERE next_attempt_at <= ? AND attempts < ?
               ORDER BY next_attempt_at ASC LIMIT ?""",
            (now, len(_RETRY_BACKOFFS_SECONDS), limit),
        )
        rows = await cur.fetchall()
        results = []
        for r in rows:
            d = dict(r)
            try:
                d["payload"] = json.loads(d["payload"])
            except (json.JSONDecodeError, TypeError):
                d["payload"] = {}
            results.append(d)
        return results


async def mark_icloud_retry_success(retry_id: int) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("DELETE FROM icloud_retry_queue WHERE id = ?", (retry_id,))
        await db.commit()


async def mark_icloud_retry_failure(retry_id: int, error: str) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT attempts FROM icloud_retry_queue WHERE id = ?", (retry_id,))
        row = await cur.fetchone()
        if not row:
            return
        new_attempts = row["attempts"] + 1
        if new_attempts >= len(_RETRY_BACKOFFS_SECONDS):
            # Permanent failure — keep row for inspection but stop trying
            await db.execute(
                "UPDATE icloud_retry_queue SET attempts = ?, last_error = ? WHERE id = ?",
                (new_attempts, error[:500], retry_id),
            )
        else:
            next_at = (datetime.now(TZ) + timedelta(seconds=_RETRY_BACKOFFS_SECONDS[new_attempts])).isoformat()
            await db.execute(
                "UPDATE icloud_retry_queue SET attempts = ?, next_attempt_at = ?, last_error = ? WHERE id = ?",
                (new_attempts, next_at, error[:500], retry_id),
            )
        await db.commit()


# ─────────────────────────────────────────── LLM AUDIT LOG ───────────────────────────────────────────

async def log_llm_call(
    provider: str,
    model: Optional[str],
    purpose: str,
    input_hash: Optional[str],
    input_chars: int,
    input_tokens: Optional[int],
    output_tokens: Optional[int],
    cache_read_tokens: Optional[int] = None,
    cache_creation_tokens: Optional[int] = None,
    redacted_terms_count: int = 0,
    estimated_cost_usd: Optional[float] = None,
    error: Optional[str] = None,
) -> None:
    """Append an audit row. NEVER stores the input itself — only its hash and metrics."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO llm_audit_log (
                ts, provider, model, purpose, input_hash, input_chars,
                input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                redacted_terms_count, estimated_cost_usd, error
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                now_iso(), provider, model, purpose, input_hash, input_chars,
                input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
                redacted_terms_count, estimated_cost_usd, error,
            ),
        )
        await db.commit()
