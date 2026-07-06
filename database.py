"""Async SQLite operations for Yordamchi.

Tables: tasks, reminders, meetings, contacts, corrections, principal_profile, conversation_history, pending_actions.
All datetime fields are stored as ISO 8601 strings in Asia/Tashkent timezone.
"""

import json
import logging
import os
import uuid
from calendar import monthrange
from datetime import datetime, timedelta
from typing import Any, Optional

import aiosqlite
import pytz

import config
import config_marketing

logger = logging.getLogger(__name__)
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
    category TEXT,
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
-- Hot paths flagged in audit: assignee filtering (team panel),
-- source-tagged queries (recurring/manual filters), reminded_at IS NULL scans.
CREATE INDEX IF NOT EXISTS idx_tasks_assignee_status ON tasks(assignee, status);
CREATE INDEX IF NOT EXISTS idx_tasks_source_created ON tasks(source, created_at);
CREATE INDEX IF NOT EXISTS idx_tasks_reminded ON tasks(reminded_at);

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
    completed_at TEXT,
    recurrence_rule TEXT,
    recurrence_parent_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_meetings_start ON meetings(datetime_start);
-- Hot paths: reminded_at IS NULL scans for sweep claim;
-- followup_sent_at IS NULL for post-meeting follow-up sweep.
CREATE INDEX IF NOT EXISTS idx_meetings_reminded ON meetings(reminded_at);
CREATE INDEX IF NOT EXISTS idx_meetings_followup ON meetings(followup_sent_at);
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
-- Time-window scans (list_due_in_window, reminders_overview) benefit from a
-- standalone remind_at index. Note: SQLite is smart enough to use the leading
-- column of idx_reminders_status_time(status, remind_at) for status-filtered
-- queries, so this only helps date-range queries that don't filter on status.
CREATE INDEX IF NOT EXISTS idx_reminders_time ON reminders(remind_at);
-- NOTE: FOREIGN KEY constraints on reminders.task_id → tasks.id and
-- reminders.meeting_id → meetings.id were intentionally NOT added.
-- SQLite doesn't allow ALTER TABLE ADD CONSTRAINT, so retrofitting would
-- require full table recreation. Tracked as future work (Sprint C1.future):
-- needs orphan cleanup + migration script + downtime window.

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

-- Quick-capture inbox ("Qaydlar") — GTD-style notes waiting to be triaged.
-- Sources: forward, /qayd command, voice via LLM, manual via section UI.
-- Once converted to a task or reminder, status flips to 'processed' and
-- converted_to_{id,type} link to the produced item. Archived notes stay
-- in the table for full-text search but drop out of the inbox view.
CREATE TABLE IF NOT EXISTS notes (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    -- HTML-formatted copy of the content (Telegram entities preserved as HTML
    -- tags). Used when rendering a forwarded note inside a <blockquote> so the
    -- original bold/italic/links/code formatting survives the round-trip.
    -- NULL for non-forward sources or older rows; render path falls back to
    -- escaped plain content.
    content_html TEXT,
    title TEXT,
    source TEXT NOT NULL,
    source_chat TEXT,
    source_author TEXT,
    source_message_id INTEGER,
    tags TEXT,
    status TEXT CHECK(status IN ('inbox','processed','archived'))
                 DEFAULT 'inbox' NOT NULL,
    converted_to_id TEXT,
    converted_to_type TEXT CHECK(converted_to_type IN ('task','reminder')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_notes_status_created
    ON notes(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_notes_source
    ON notes(source, created_at DESC);

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

-- Idempotency / crash-recovery queue for in-flight user message handling.
-- Pattern: enqueue BEFORE Claude call (state=pending) → mark in_progress →
-- complete on success → fail on exception. On bot restart, stuck rows
-- (state in {pending,in_progress} and older than ~5 min) are surfaced
-- so the principal knows their request was dropped instead of silently lost.
-- update_id is the Telegram update id and is UNIQUE so a redelivered update
-- (unusual but possible) cannot be double-processed.
CREATE TABLE IF NOT EXISTS pending_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    update_id INTEGER UNIQUE,
    chat_id INTEGER,
    message_id INTEGER,
    user_text TEXT,                        -- original input, redacted before LLM
    state TEXT NOT NULL DEFAULT 'pending', -- pending | in_progress | completed | failed
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

CREATE INDEX IF NOT EXISTS idx_pending_state ON pending_actions(state, updated_at);

-- Inline ulashish matn keshi (token=id → matn). Sayqallangan matn/protokol inline
-- orqali ulashilganda matn shu yerda saqlanadi — bot qayta ishga tushganda
-- yo'qolmaydi (avval xotirada edi → 'ssilka yo'qolib ketardi').
CREATE TABLE IF NOT EXISTS share_cache (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- First-class task categories (icon, sort order, archive). tasks.category links
-- by NAME (loose) — derived/auto category strings still work even without a row.
CREATE TABLE IF NOT EXISTS categories (
    name TEXT PRIMARY KEY,
    icon TEXT,
    archived INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS improvement_proposals (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'auto',
    title TEXT NOT NULL,
    problem TEXT,
    evidence TEXT,
    root_cause TEXT,
    fix_kind TEXT,
    proposed_change TEXT,
    impact_estimate TEXT,
    status TEXT NOT NULL DEFAULT 'new'
);
CREATE INDEX IF NOT EXISTS idx_proposals_status ON improvement_proposals(status, created_at DESC);

CREATE TABLE IF NOT EXISTS self_improvement_audit (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    proposal_id TEXT,
    action TEXT NOT NULL,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS idx_si_audit_ts ON self_improvement_audit(ts DESC);

CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT,
    color TEXT,                   -- HEX (kalendar/badge rangi)
    status TEXT NOT NULL DEFAULT 'active',   -- active | archived
    type TEXT,                    -- Marketing Hub: smm|campaign|pr|branding|... (NULL = legacy SMM)
    icon TEXT,                    -- Tabler icon nomi
    default_view TEXT NOT NULL DEFAULT 'calendar',  -- calendar|kanban|table|dashboard
    workflow TEXT,                -- JSON: {"statuses":[{key,label,color},...]}
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS content_posts (
    id TEXT PRIMARY KEY,
    date TEXT NOT NULL,            -- YYYY-MM-DD (SMM kontent-reja sanasi)
    category TEXT NOT NULL,        -- biznes | jamoa | bank | chakana (yoki boshqa)
    topic TEXT NOT NULL,
    format TEXT,
    platform TEXT,
    message TEXT,
    hashtags TEXT,
    project_id TEXT,              -- qaysi loyiha (NULL = umumiy)
    status TEXT NOT NULL DEFAULT 'reja',  -- reja|jarayonda|tekshiruvda|joylandi|rad_etildi|bekor
    assignee TEXT,               -- mas'ul (contacts katalogidan)
    published_url TEXT,          -- joylangan post havolasi
    published_at TEXT,           -- joylangan sana/vaqt (ISO)
    reject_reason TEXT,          -- rad etilsa — sabab
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_content_date ON content_posts(date);

-- ── Marketing Hub: universal ProjectItem modeli ──
-- Har loyiha item'i (post/task/milestone/media_placement/...) shu jadvalda saqlanadi.
-- 1st-class ustunlar filtrlanadi/indekslanadi; turga-xos qo'shimchalar `fields` JSON'da.
-- (Bosqich 1: faqat sxema — hali hech qayerda o'qilmaydi/yozilmaydi, xatti-harakat o'zgarmaydi.)
CREATE TABLE IF NOT EXISTS project_items (
    id TEXT PRIMARY KEY,
    project_id TEXT,                   -- qaysi loyiha (NULL = bog'lanmagan; FK yo'q)
    type TEXT NOT NULL DEFAULT 'post', -- post|task|milestone|note|media_placement|...
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'reja',
    priority TEXT,                     -- P0|P1|P2|P3 (ixtiyoriy)
    assignee TEXT,
    category TEXT,                     -- 1st-class scalar (kalendar/filtr uchun)
    stage TEXT,                        -- roadmap bosqichi (ixtiyoriy)
    primary_date TEXT,                 -- YYYY-MM-DD (kalendar/timeline asosiy sanasi)
    start_date TEXT,
    end_date TEXT,
    deadline TEXT,
    order_index INTEGER NOT NULL DEFAULT 0,   -- kanban ustun ichidagi tartib
    parent_id TEXT,                    -- subtask/related
    fields TEXT,                       -- JSON: turga xos qo'shimcha maydonlar
    created_by TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pitems_project_status   ON project_items(project_id, status);
CREATE INDEX IF NOT EXISTS idx_pitems_project_date     ON project_items(project_id, primary_date);
CREATE INDEX IF NOT EXISTS idx_pitems_project_type     ON project_items(project_id, type);
CREATE INDEX IF NOT EXISTS idx_pitems_project_category ON project_items(project_id, category);
CREATE INDEX IF NOT EXISTS idx_pitems_assignee         ON project_items(assignee);

-- Migratsiyalar jurnali — qaysi migratsiya qo'llanganini kuzatadi (idempotentlik uchun).
-- Migratsiyalar `migrations.py`da QO'LDA ishga tushiriladi (init() ichida EMAS).
CREATE TABLE IF NOT EXISTS schema_version (
    name TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL
);
"""


async def init() -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        # WAL mode: allow concurrent reads while a write is in progress, drastically
        # reducing "database is locked" errors when scheduler + handler + webapp coincide.
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA synchronous=NORMAL")
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("PRAGMA busy_timeout=5000")
        # Auto-checkpoint every 1000 pages keeps the WAL file from growing
        # unbounded between manual checkpoints; without this it can balloon
        # to >1GB after weeks of writes.
        await db.execute("PRAGMA wal_autocheckpoint=1000")
        # Startup self-heal: systemd restarts the bot after a crash/OOM-kill, which
        # can leave a bloated or half-written WAL. Fully checkpoint + TRUNCATE it so a
        # stuck WAL (a known "disk I/O error" trigger) is cleared on every boot, and
        # log a fast integrity signal so corruption is visible in the logs early.
        try:
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            cur = await db.execute("PRAGMA quick_check")
            qc = (await cur.fetchone() or ["?"])[0]
            if qc != "ok":
                logger.error("DB quick_check on startup: %s", qc)
        except Exception:
            logger.exception("DB startup self-heal (wal_checkpoint/quick_check) failed")
        await db.executescript(SCHEMA)

        # Idempotent migrations for existing DBs (CREATE TABLE IF NOT EXISTS doesn't add columns)
        cur = await db.execute("PRAGMA table_info(meetings)")
        meeting_cols = {row[1] for row in await cur.fetchall()}
        if "icloud_uid" not in meeting_cols:
            await db.execute("ALTER TABLE meetings ADD COLUMN icloud_uid TEXT")
        for col in ("recurrence_rule", "recurrence_parent_id"):
            if col not in meeting_cols:
                await db.execute(f"ALTER TABLE meetings ADD COLUMN {col} TEXT")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_meetings_icloud ON meetings(icloud_uid)")

        cur = await db.execute("PRAGMA table_info(tasks)")
        task_cols = {row[1] for row in await cur.fetchall()}
        if "assignee" not in task_cols:
            await db.execute("ALTER TABLE tasks ADD COLUMN assignee TEXT")
        for col in ("recurrence_rule", "recurrence_next_at", "recurrence_parent_id",
                    "category", "parent_id"):
            if col not in task_cols:
                await db.execute(f"ALTER TABLE tasks ADD COLUMN {col} TEXT")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_parent ON tasks(parent_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_recurrence ON tasks(recurrence_rule, recurrence_next_at)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_category ON tasks(category)")

        # Idempotency for redelivered Telegram updates. aiogram 3.x doesn't expose
        # the raw update_id on Message, so update_id was always NULL (NULLs never
        # collide in a UNIQUE column) → the dedup was dead and a slow turn that
        # Telegram redelivered produced a SECOND confirm card. Dedup on the stable
        # (chat_id, message_id) pair instead. Pre-clean any existing dup rows so the
        # UNIQUE index can be created.
        await db.execute(
            "DELETE FROM pending_actions WHERE chat_id IS NOT NULL AND message_id IS NOT NULL "
            "AND id NOT IN (SELECT MIN(id) FROM pending_actions "
            "WHERE chat_id IS NOT NULL AND message_id IS NOT NULL GROUP BY chat_id, message_id)")
        await db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_pending_chatmsg "
                         "ON pending_actions(chat_id, message_id)")

        for col in ("prep_sent_at", "followup_sent_at", "completed_at"):
            if col not in meeting_cols:
                await db.execute(f"ALTER TABLE meetings ADD COLUMN {col} TEXT")

        # Notes: content_html column added later — backfill on existing rows
        # is unnecessary (renderer falls back to escaped content when NULL).
        try:
            cur = await db.execute("PRAGMA table_info(notes)")
            note_cols = {row[1] for row in await cur.fetchall()}
            if note_cols and "content_html" not in note_cols:
                await db.execute("ALTER TABLE notes ADD COLUMN content_html TEXT")
        except Exception:
            # notes table didn't exist before this run — the CREATE TABLE
            # above already includes content_html, nothing to migrate.
            pass

        # content_posts: Loyihalar + status-workflow ustunlari (mavjud DB'lar uchun).
        # Bu ALTER'lar SCHEMA'dan KEYIN ishlaydi — shu bois project_id indeksi ham
        # shu yerda (SCHEMA'da bo'lsa, eski jadvalda ustun yo'qligidan executescript yiqiladi).
        cur = await db.execute("PRAGMA table_info(content_posts)")
        content_cols = {row[1] for row in await cur.fetchall()}
        if content_cols:   # jadval mavjud bo'lsagina migratsiya kerak
            for col in ("project_id", "assignee", "published_url", "published_at", "reject_reason"):
                if col not in content_cols:
                    await db.execute(f"ALTER TABLE content_posts ADD COLUMN {col} TEXT")
            if "status" not in content_cols:
                await db.execute("ALTER TABLE content_posts ADD COLUMN status TEXT NOT NULL DEFAULT 'reja'")
            await db.execute(
                "CREATE INDEX IF NOT EXISTS idx_content_project ON content_posts(project_id, date)")

        # projects: Marketing Hub type-aware ustunlari (mavjud DB'lar uchun).
        # (Bosqich 1: ustunlar qo'shiladi, lekin hali o'qilmaydi — xatti-harakat o'zgarmaydi.)
        cur = await db.execute("PRAGMA table_info(projects)")
        proj_cols = {row[1] for row in await cur.fetchall()}
        if proj_cols:
            for col in ("type", "icon", "workflow"):
                if col not in proj_cols:
                    await db.execute(f"ALTER TABLE projects ADD COLUMN {col} TEXT")
            if "default_view" not in proj_cols:
                await db.execute(
                    "ALTER TABLE projects ADD COLUMN default_view TEXT NOT NULL DEFAULT 'calendar'")

        await db.commit()

    # Bir martalik: SMM kontent-rejasi bo'sh bo'lsa, Agrobank iyul-2026 urug'ini import
    # qilamiz (content_seed.json). Jadval to'lgan bo'lsa — hech narsa qilmaydi.
    try:
        seed_path = os.path.join(os.path.dirname(__file__), "content_seed.json")
        if os.path.exists(seed_path):
            with open(seed_path, encoding="utf-8") as f:
                n = await seed_content_posts(json.load(f))
            if n:
                logger.info("Seeded %d content posts (SMM kontent-reja)", n)
    except Exception:
        logger.exception("Content seed skipped")

    # Bir martalik backfill: loyiha yo'q va bog'lanmagan postlar bo'lsa — "Agrobank SMM"
    # loyihasini yaratib, barcha egasiz postlarni unga bog'laymiz. Status: o'tgan sana →
    # joylandi, bugun/kelajak → reja (published_at joylandi uchun sanaga o'rnatiladi).
    try:
        async with aiosqlite.connect(config.DATABASE_PATH) as db:
            cur = await db.execute("SELECT COUNT(*) FROM projects")
            has_project = (await cur.fetchone())[0] > 0
            cur = await db.execute("SELECT COUNT(*) FROM content_posts WHERE project_id IS NULL")
            orphans = (await cur.fetchone())[0]
            if not has_project and orphans:
                pid = new_id("pr-")
                now = now_iso()
                await db.execute(
                    """INSERT INTO projects (id, name, description, color, status, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'active', ?, ?)""",
                    (pid, "Agrobank SMM", "Agrobank ijtimoiy tarmoqlar kontent-rejasi",
                     "#16A34A", now, now))
                today = datetime.now(TZ).date().isoformat()
                await db.execute(
                    "UPDATE content_posts SET project_id = ?, "
                    "status = CASE WHEN date < ? THEN 'joylandi' ELSE 'reja' END, "
                    "published_at = CASE WHEN date < ? THEN date ELSE published_at END "
                    "WHERE project_id IS NULL",
                    (pid, today, today))
                await db.commit()
                logger.info("Backfilled %d posts into 'Agrobank SMM' project", orphans)
    except Exception:
        logger.exception("Project backfill skipped")


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
    # Blank/whitespace title → safe default (the "Vazifa" fallback only fired on a
    # MISSING key, so "" / "   " slipped through and rendered as an empty card).
    title = (data.get("title") or "").strip() or "Vazifa"
    # Deadline must be a parseable ISO instant. A raw non-ISO string ("ertaga")
    # would be compared LEXICOGRAPHICALLY everywhere ('e' > '2') → treated as a
    # far-future date and silently dropped from every reminder/overdue/load surface.
    deadline = data.get("deadline")
    if deadline is not None and parse_iso_dt(deadline) is None:
        deadline = None
    recurrence_rule = normalize_recurrence_rule(data.get("recurrence_rule") or data.get("recurrence"))
    recurrence_next_at = data.get("recurrence_next_at")
    if recurrence_rule and not recurrence_next_at:
        # Allaqachon 'done' holatda yaratilgan yozuv (masalan arxiv-import) uchun
        # next_at yozilmaydi: complete_task bunday vazifada hech qachon nusxa
        # tug'dirmaydi, next_at esa hech kim iste'mol qilmaydigan yolg'on bo'lardi.
        if (data.get("status") or "todo") != "done":
            recurrence_next_at = compute_next_recurrence(deadline, recurrence_rule)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO tasks (id, title, description, deadline, priority, status, tags, category, assignee,
                                  recurrence_rule, recurrence_next_at, recurrence_parent_id, parent_id,
                                  source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                title,
                data.get("description"),
                deadline,
                data.get("priority", "P2"),
                data.get("status", "todo"),
                json.dumps(data.get("tags", []), ensure_ascii=False),
                (data.get("category") or None),
                data.get("assignee"),
                recurrence_rule,
                recurrence_next_at,
                data.get("recurrence_parent_id"),
                (data.get("parent_id") or None),
                source,
                now,
                now,
            ),
        )
        await _log_history(db, task_id, "create", field=None,
                            new_value=title, source=source)
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

        # A non-ISO deadline edit ("ertaga") must not be stored raw — it would sort
        # lexicographically as a far-future date and vanish from reminders/overdue.
        # Drop the key (leave the stored deadline untouched) rather than clear it.
        if data.get("deadline") is not None and parse_iso_dt(data["deadline"]) is None:
            data = {k: v for k, v in data.items() if k != "deadline"}

        fields = []
        values = []
        changes: list[tuple[str, str, str]] = []  # (field, old, new)
        for key in (
            "title", "description", "deadline", "priority", "status", "source", "assignee",
            "category", "recurrence_rule", "recurrence_next_at", "recurrence_parent_id",
            "parent_id",  # allow Excel № re-parenting (move a task under another / promote)
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
        # No-op guard: agar hech bir qiymat AMALDA o'zgarmagan bo'lsa (masalan bir xil
        # fayl qayta import qilinsa), UPDATE bajarilmaydi — updated_at behuda
        # "yangilangan" bo'lib qolmaydi (statistika/briefing proxy sifatida ishlatadi).
        if not any(str(old) != str(new) for _, old, new in changes):
            return True
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
    """Mark a task as done atomically. Only the first concurrent caller wins —
    subsequent callers see status='done' and return True without re-creating
    the recurring follow-up. This eliminates the duplicate-recurrence race
    between two handlers completing the same task."""
    now = now_iso()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
        before = await cur.fetchone()
        if before is None:
            return False
        if before["status"] == "done":
            return True
        # Conditional UPDATE: another caller may have flipped status between our
        # SELECT and UPDATE. Only the caller whose rowcount > 0 owns the side-effects.
        cur = await db.execute(
            "UPDATE tasks SET status = ?, updated_at = ? WHERE id = ? AND status != ?",
            ("done", now, task_id, "done"),
        )
        won = cur.rowcount > 0
        if won:
            await _log_history(db, task_id, "update", field="status",
                                old_value=before["status"], new_value="done",
                                source="complete")
        await db.commit()
    if not won:
        return True
    completed = _row_to_task(before)
    if completed.get("recurrence_rule"):
        # Re-verify the task still exists before spawning: a delete can interleave
        # between our commit above and this spawn (concurrent complete+delete), which
        # would otherwise leave a zombie next-occurrence pointing at a deleted parent.
        if await get_task(task_id) is not None:
            await create_next_recurring_task(completed)
    return True


def normalize_recurrence_rule(raw: Any) -> Optional[str]:
    """Normalize LLM/user recurrence wording to a small supported rule set."""
    if not raw:
        return None
    value = str(raw).strip().lower().replace("_", " ").replace("-", " ")
    aliases = {
        "daily": "daily", "every day": "daily", "har kuni": "daily", "kunlik": "daily",
        "weekdays": "weekdays", "weekday": "weekdays", "every weekday": "weekdays",
        "ish kunlari": "weekdays", "ish kuni": "weekdays", "har ish kuni": "weekdays",
        "dushanba juma": "weekdays", "dush juma": "weekdays",
        "weekly": "weekly", "every week": "weekly", "har hafta": "weekly", "haftalik": "weekly",
        "monthly": "monthly", "every month": "monthly", "har oy": "monthly", "oylik": "monthly",
        "quarterly": "quarterly", "every quarter": "quarterly", "har chorak": "quarterly", "choraklik": "quarterly",
        "yearly": "yearly", "annual": "yearly", "every year": "yearly", "har yil": "yearly", "yillik": "yearly",
    }
    return aliases.get(value)


def _coerce_dt(iso: Optional[str]) -> datetime:
    """Parse an ISO timestamp into an Asia/Tashkent-aware datetime. Aware
    inputs in another zone are converted (not localized — calling
    pytz.localize on an already-aware dt raises and silently drifts the
    wall-clock time). Returns now() on parse failure."""
    if iso:
        try:
            dt = datetime.fromisoformat(iso)
            if dt.tzinfo is not None:
                return dt.astimezone(TZ)
            return TZ.localize(dt)
        except (TypeError, ValueError):
            pass
    return datetime.now(TZ)


def parse_iso_dt(iso: Optional[str]) -> Optional[datetime]:
    """Parse an ISO timestamp into an Asia/Tashkent-aware datetime, or None
    if the input is missing/invalid. See _coerce_dt for tz-handling notes."""
    if not iso:
        return None
    try:
        dt = datetime.fromisoformat(iso)
        if dt.tzinfo is not None:
            return dt.astimezone(TZ)
        return TZ.localize(dt)
    except (TypeError, ValueError):
        return None


def is_past_deadline(iso: Optional[str], *, grace_minutes: int = 1) -> bool:
    """True if `iso` parses to a time already in the past (small grace window so a
    'now + a few seconds' round-trip isn't flagged). Missing/unparseable inputs
    return False — absence of a deadline is never 'past'."""
    dt = parse_iso_dt(iso)
    if dt is None:
        return False
    return dt < datetime.now(TZ) - timedelta(minutes=grace_minutes)


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

    # Step forward until strictly after `now`. Cap is a safety backstop against a
    # pathological/long-dormant base (1000 daily steps ≈ 2.7 yil) — high enough that
    # normal use always lands a future date inside the loop.
    for _ in range(1000):
        if rule == "daily":
            current += timedelta(days=1)
        elif rule == "weekdays":
            # Keyingi ish kuni — shanba/yakshanbani o'tkazib yuboradi.
            current += timedelta(days=1)
            while current.weekday() >= 5:  # 5=shanba, 6=yakshanba
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
    # Loop exhausted without reaching a future date (degenerate/very dormant base):
    # return None rather than a stale PAST date — the caller then skips the next
    # instance instead of creating an already-overdue one.
    return None


async def create_next_recurring_task(completed_task: dict) -> Optional[str]:
    rule = normalize_recurrence_rule(completed_task.get("recurrence_rule"))
    if not rule:
        return None
    # Base the next occurrence on the original deadline when set; otherwise (undated
    # recurring task, or empty/unparseable deadline) fall back to the completion time
    # (now) so an undated recurring chain never silently stops.
    base = completed_task.get("deadline")
    if not parse_iso_dt(base):
        base = now_iso()
    next_deadline = compute_next_recurrence(base, rule)
    if not next_deadline:
        return None
    # Dedup: reopen→re-done (yoki bir faylni qayta import qilish) ikkinchi bir xil
    # nusxani yaratmasin — shu zanjirda (parent yoki uning ajdodi) xuddi shu
    # muddatli, bekor qilinmagan nusxa allaqachon bo'lsa, qaytadan tug'dirmaymiz.
    chain_id = completed_task.get("recurrence_parent_id") or completed_task.get("id")
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        # Match ANY still-open occurrence in this chain (not the exact next_deadline):
        # editing a spawned child's deadline used to break an exact match and let a
        # reopen→re-complete cycle create a duplicate. A chain should have at most one
        # live "next" occurrence, so any open one means "already spawned".
        cur = await db.execute(
            """SELECT id FROM tasks
               WHERE recurrence_parent_id IN (?, ?)
                 AND status IN ('todo','in_progress','blocked') LIMIT 1""",
            (chain_id, completed_task.get("id")),
        )
        existing = await cur.fetchone()
    if existing:
        return existing[0]
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


async def _subtree_ids(db, root_id: str) -> list[str]:
    """All task ids in the subtree rooted at root_id (root + children + grandchildren
    …) via the parent_id closure — so a delete/snapshot reaches the WHOLE tree, not
    just direct children."""
    cur = await db.execute(
        """WITH RECURSIVE tree(id) AS (
               SELECT id FROM tasks WHERE id = ?
               UNION
               SELECT t.id FROM tasks t JOIN tree ON t.parent_id = tree.id
           )
           SELECT id FROM tree""",
        (root_id,),
    )
    return [r[0] for r in await cur.fetchall()]


async def delete_task(task_id: str, source: str = "manual") -> bool:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT title FROM tasks WHERE id = ?", (task_id,))
        row = await cur.fetchone()
        if row is None:
            return False
        title = row["title"]
        # Cascade over the FULL subtree (children, grandchildren, …) — there is no FK
        # ON DELETE, so drop every descendant's linked reminders + rows here, else a
        # grandchild (and its reminder that still fires) would be orphaned.
        ids = await _subtree_ids(db, task_id)
        qmarks = ",".join("?" * len(ids))
        await db.execute(f"DELETE FROM reminders WHERE task_id IN ({qmarks})", ids)
        cur = await db.execute(f"DELETE FROM tasks WHERE id IN ({qmarks})", ids)
        if cur.rowcount > 0:
            await _log_history(db, task_id, "delete", field=None,
                                old_value=title, source=source)
        await db.commit()
        return cur.rowcount > 0


async def restore_task(task: dict) -> bool:
    """Re-insert a previously deleted task with its ORIGINAL id (so chat buttons
    and task_history rows stay valid). Returns False if a row with that id already
    exists — guards a double-tap of the undo button. `task` is a dict as returned
    by get_task (tags is a list)."""
    tid = task.get("id")
    if not tid:
        return False
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("SELECT 1 FROM tasks WHERE id = ?", (tid,))
        if await cur.fetchone():
            return False  # already restored
        tags = task.get("tags", [])
        await db.execute(
            """INSERT INTO tasks (id, title, description, deadline, priority, status, tags, category, assignee,
                                  recurrence_rule, recurrence_next_at, recurrence_parent_id,
                                  source, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                tid,
                task.get("title", "Vazifa"),
                task.get("description"),
                task.get("deadline"),
                task.get("priority", "P2"),
                task.get("status", "todo"),
                json.dumps(tags if isinstance(tags, list) else [], ensure_ascii=False),
                (task.get("category") or None),
                task.get("assignee"),
                task.get("recurrence_rule"),
                task.get("recurrence_next_at"),
                task.get("recurrence_parent_id"),
                task.get("source", "manual"),
                task.get("created_at") or now_iso(),
                now_iso(),
            ),
        )
        await _log_history(db, tid, "restore", field=None,
                           new_value=task.get("title"), source="undo_delete")
        await db.commit()
        return True


async def snapshot_task_tree(task_id: str) -> dict:
    """Capture the FULL subtree (root + all descendants) and every linked reminder
    before a delete, so the undo button can restore ALL of it (restore_task_tree) —
    not just the parent row. Returns {} if the task is already gone."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        ids = await _subtree_ids(db, task_id)
        if not ids:
            return {}
        qm = ",".join("?" * len(ids))
        t_cur = await db.execute(f"SELECT * FROM tasks WHERE id IN ({qm})", ids)
        tasks = [dict(r) for r in await t_cur.fetchall()]
        r_cur = await db.execute(f"SELECT * FROM reminders WHERE task_id IN ({qm})", ids)
        reminders = [dict(r) for r in await r_cur.fetchall()]
    return {"tasks": tasks, "reminders": reminders}


async def restore_task_tree(snapshot: dict) -> int:
    """Re-insert a snapshot_task_tree() capture — all tasks (parent_id preserved) and
    their linked reminders — with ORIGINAL ids. Skips rows whose id already exists
    (double-tap undo guard). Returns the number of tasks restored."""
    tasks = (snapshot or {}).get("tasks") or []
    reminders = (snapshot or {}).get("reminders") or []
    if not tasks:
        return 0
    restored = 0
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row

        async def _reinsert(table: str, row: dict) -> bool:
            cur = await db.execute(f"SELECT 1 FROM {table} WHERE id = ?", (row.get("id"),))
            if await cur.fetchone():
                return False
            cols = list(row.keys())
            await db.execute(
                f"INSERT INTO {table} ({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                [row[c] for c in cols],
            )
            return True

        for t in tasks:
            if await _reinsert("tasks", t):
                restored += 1
        for rem in reminders:
            await _reinsert("reminders", rem)
        if restored:
            await _log_history(db, tasks[0].get("id"), "restore", field=None,
                               new_value=tasks[0].get("title"), source="undo_delete")
        await db.commit()
    return restored


# ─────────────── BULK DELETE (voice/text "barchasini o'chir" — always confirmed in handler) ───────────────

async def delete_all_tasks(status_in: Optional[list[str]] = None) -> int:
    """Delete tasks in bulk. Optional status filter (e.g. ['done']); None = ALL.
    Returns rows deleted. Caller MUST gate this behind a confirmation."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        if status_in:
            ph = ",".join("?" * len(status_in))
            cur = await db.execute(f"DELETE FROM tasks WHERE status IN ({ph})", tuple(status_in))
        else:
            cur = await db.execute("DELETE FROM tasks")
        await db.commit()
        return cur.rowcount


async def delete_all_meetings() -> int:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("DELETE FROM meetings")
        await db.commit()
        return cur.rowcount


async def delete_all_notes() -> int:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("DELETE FROM notes")
        await db.commit()
        return cur.rowcount


async def delete_all_reminders() -> int:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("DELETE FROM reminders")
        await db.commit()
        return cur.rowcount


async def delete_all_contacts() -> int:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("DELETE FROM contacts")
        await db.commit()
        return cur.rowcount


async def delete_contact(name: str) -> bool:
    """Remove one contact by name (case-insensitive). Only touches the contacts
    directory — existing tasks keep their assignee text untouched."""
    name = (name or "").strip()
    if not name:
        return False
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "DELETE FROM contacts WHERE lower(name) = lower(?)", (name,))
        await db.commit()
        return cur.rowcount > 0


# ─────────────────────────────── SMM KONTENT-REJA ───────────────────────────────

_CONTENT_FIELDS = ("date", "category", "topic", "format", "platform", "message", "hashtags",
                   "project_id", "status", "assignee", "published_url", "published_at", "reject_reason")
CONTENT_STATUSES = ("reja", "jarayonda", "tekshiruvda", "joylandi", "rad_etildi", "bekor")


async def list_content_posts(year: int | None = None, month: int | None = None,
                             project_id: str | None = None, status: str | None = None) -> list[dict]:
    """Kontent postlari. year+month, project_id, status bo'yicha filtrlanadi."""
    where, params = [], []
    if year and month:
        where.append("date LIKE ?"); params.append(f"{int(year):04d}-{int(month):02d}-%")
    if project_id:
        where.append("project_id = ?"); params.append(project_id)
    if status:
        where.append("status = ?"); params.append(status)
    sql = "SELECT * FROM content_posts"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY date, category"
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        return [dict(r) for r in await cur.fetchall()]


async def create_content_post(data: dict) -> str:
    cid = new_id("cp-")
    now = now_iso()
    v = {k: (data.get(k).strip() if isinstance(data.get(k), str) else data.get(k))
         for k in _CONTENT_FIELDS}
    st = v.get("status") or "reja"
    if st not in CONTENT_STATUSES:
        st = "reja"
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO content_posts (id, date, category, topic, format, platform,
                   message, hashtags, project_id, status, assignee, published_url,
                   published_at, reject_reason, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (cid, v["date"], v["category"] or "biznes", v["topic"] or "Post", v["format"],
             v["platform"], v["message"], v["hashtags"], v["project_id"], st, v["assignee"],
             v["published_url"], v["published_at"], v["reject_reason"], now, now))
        await db.commit()
    return cid


async def update_content_post(post_id: str, data: dict) -> bool:
    fields, values = [], []
    for k in _CONTENT_FIELDS:
        if k in data:
            fields.append(f"{k} = ?")
            v = data[k]
            values.append(v.strip() if isinstance(v, str) else v)
    if not fields:
        return False
    fields.append("updated_at = ?")
    values.append(now_iso())
    values.append(post_id)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            f"UPDATE content_posts SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()
        return cur.rowcount > 0


async def delete_content_post(post_id: str) -> bool:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("DELETE FROM content_posts WHERE id = ?", (post_id,))
        await db.commit()
        return cur.rowcount > 0


async def count_content_posts() -> int:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM content_posts")
        return (await cur.fetchone())[0]


async def seed_content_posts(posts: list[dict]) -> int:
    """Bir martalik urug'lantirish: jadval bo'sh bo'lsagina to'ldiradi (idempotent).
    Agrobank iyul-2026 kontent-rejasini ilk ishga tushirishda import qiladi."""
    if await count_content_posts() > 0:
        return 0
    now = now_iso()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        for p in posts:
            await db.execute(
                """INSERT INTO content_posts (id, date, category, topic, format, platform,
                                              message, hashtags, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_id("cp-"), p.get("date"), p.get("category"), p.get("topic"),
                 p.get("format"), p.get("platform"), p.get("message"), p.get("hashtags"), now, now))
        await db.commit()
    return len(posts)


# ─────────────────────────────── LOYIHALAR (Projects) ───────────────────────────────

async def list_projects(include_archived: bool = True) -> list[dict]:
    """Loyihalar + har birida item soni va bajarilish % (yopiq statuslar / jami).
    Sanoq project_items ustidan (universal model) — migratsiyadan keyingi manba."""
    terminal = sorted(config_marketing.TERMINAL_STATUSES)
    ph = ",".join("?" * len(terminal))
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM projects ORDER BY status, name")
        projects = [dict(r) for r in await cur.fetchall()]
        cur = await db.execute(
            f"SELECT project_id, COUNT(*) n, "
            f"SUM(CASE WHEN status IN ({ph}) THEN 1 ELSE 0 END) done "
            f"FROM project_items GROUP BY project_id", terminal)
        stats = {r["project_id"]: (r["n"], r["done"]) for r in await cur.fetchall()}
    out = []
    for p in projects:
        if not include_archived and p["status"] == "archived":
            continue
        n, done = stats.get(p["id"], (0, 0))
        p["item_count"] = n
        p["post_count"] = n   # frontend moslashuvi (eski kalit)
        p["progress"] = round((done or 0) / n * 100) if n else 0
        out.append(_normalize_project(p))
    return out


# ═══════════════════ Marketing Hub: universal ProjectItem CRUD ═══════════════════
# project_items ustidan CRUD. ADDITIVE — content_posts / content_* yo'liga tegmaydi.
_ITEM_UPDATE_SCALAR = ("title", "description", "status", "priority", "assignee",
                       "category", "stage", "primary_date", "start_date", "end_date",
                       "deadline", "parent_id", "order_index", "project_id")


def _row_to_item(r) -> dict:
    d = dict(r)
    raw = d.get("fields")
    try:
        d["fields"] = json.loads(raw) if raw else {}
    except (ValueError, TypeError):
        d["fields"] = {}
    return d


async def list_project_items(project_id: str | None, *, type_: str | None = None,
                             status: str | None = None, assignee: str | None = None,
                             category: str | None = None, year: int | None = None,
                             month: int | None = None, deadline: str | None = None,
                             overdue: bool = False) -> list[dict]:
    """Loyiha itemlari — filtrlar bilan. project_id=None → barcha loyihalardan."""
    where, params = ["1=1"], []
    if project_id is not None:
        where.append("project_id = ?"); params.append(project_id)
    if type_:
        where.append("type = ?"); params.append(type_)
    if status:
        where.append("status = ?"); params.append(status)
    if assignee:
        where.append("assignee = ?"); params.append(assignee)
    if category:
        where.append("category = ?"); params.append(category)
    if year and month:
        where.append("primary_date LIKE ?"); params.append(f"{int(year):04d}-{int(month):02d}-%")
    if deadline:
        where.append("primary_date LIKE ?"); params.append(f"{deadline}%")
    if overdue:
        terminal = sorted(config_marketing.TERMINAL_STATUSES)
        where.append("primary_date < ?"); params.append(datetime.now(TZ).date().isoformat())
        where.append(f"status NOT IN ({','.join('?' * len(terminal))})"); params.extend(terminal)
    sql = ("SELECT * FROM project_items WHERE " + " AND ".join(where)
           + " ORDER BY status, order_index, primary_date, title")
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        return [_row_to_item(r) for r in await cur.fetchall()]


async def get_project_item(item_id: str) -> dict | None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM project_items WHERE id = ?", (item_id,))
        row = await cur.fetchone()
        return _row_to_item(row) if row else None


async def create_project_item(project_id: str | None, data: dict) -> str:
    iid = new_id("pi-")
    now = now_iso()
    typ = data.get("type")
    if typ not in config_marketing.ITEM_TYPES:
        typ = "task"
    title = (data.get("title") or "").strip() or "Item"
    status = data.get("status") or "reja"
    fields_json = json.dumps(data.get("fields") or {}, ensure_ascii=False)

    def _s(k):
        v = data.get(k)
        return v.strip() if isinstance(v, str) else v

    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        # order_index — maqsad (project_id, status) ustunida oxiriga qo'shiladi.
        cur = await db.execute(
            "SELECT COALESCE(MAX(order_index), -1) + 1 FROM project_items "
            "WHERE project_id IS ? AND status = ?", (project_id, status))
        order_index = (await cur.fetchone())[0]
        await db.execute(
            """INSERT INTO project_items
               (id, project_id, type, title, description, status, priority, assignee,
                category, stage, primary_date, start_date, end_date, deadline,
                order_index, parent_id, fields, created_by, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (iid, project_id, typ, title, _s("description"), status, _s("priority"),
             _s("assignee"), _s("category"), _s("stage"), _s("primary_date"),
             _s("start_date"), _s("end_date"), _s("deadline"), order_index,
             _s("parent_id"), fields_json, _s("created_by"), now, now))
        await db.commit()
    return iid


async def update_project_item(item_id: str, data: dict) -> bool:
    """Atomik shallow-merge: scalar ustunlar + `fields` blob (null qiymat → kalitni o'chirish).
    BEGIN IMMEDIATE yozuv-lock ostida read-modify-write — bir vaqtli PATCH'lar seriyalanadi."""
    scalars = {}
    for k in _ITEM_UPDATE_SCALAR:
        if k in data:
            v = data[k]
            scalars[k] = v.strip() if isinstance(v, str) else v
    has_fields = isinstance(data.get("fields"), dict)
    if not scalars and not has_fields:
        return False
    async with aiosqlite.connect(config.DATABASE_PATH, isolation_level=None) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("BEGIN IMMEDIATE")
        try:
            cur = await db.execute("SELECT fields FROM project_items WHERE id = ?", (item_id,))
            row = await cur.fetchone()
            if row is None:
                await db.execute("ROLLBACK")
                return False
            sets, vals = [], []
            for k, v in scalars.items():
                sets.append(f"{k} = ?"); vals.append(v)
            if has_fields:
                cur_fields = {}
                if row[0]:
                    try:
                        cur_fields = json.loads(row[0])
                    except (ValueError, TypeError):
                        cur_fields = {}
                for fk, fv in data["fields"].items():
                    if fv is None:
                        cur_fields.pop(fk, None)
                    else:
                        cur_fields[fk] = fv
                sets.append("fields = ?"); vals.append(json.dumps(cur_fields, ensure_ascii=False))
            sets.append("updated_at = ?"); vals.append(now_iso())
            vals.append(item_id)
            cur = await db.execute(f"UPDATE project_items SET {', '.join(sets)} WHERE id = ?", vals)
            await db.execute("COMMIT")
            return cur.rowcount > 0
        except Exception:
            await db.execute("ROLLBACK")
            raise


async def delete_project_item(item_id: str) -> bool:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("DELETE FROM project_items WHERE id = ?", (item_id,))
        await db.commit()
        return cur.rowcount > 0


async def move_project_item(item_id: str, status: str, order_index: int | None = None) -> bool:
    """Kanban ko'chirish: status o'zgartirish + maqsad ustunni 0..n atomik qayta raqamlash."""
    async with aiosqlite.connect(config.DATABASE_PATH, isolation_level=None) as db:
        await db.execute("PRAGMA busy_timeout=5000")
        await db.execute("BEGIN IMMEDIATE")
        try:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT project_id FROM project_items WHERE id = ?", (item_id,))
            row = await cur.fetchone()
            if row is None:
                await db.execute("ROLLBACK")
                return False
            project_id = row["project_id"]
            await db.execute("UPDATE project_items SET status = ?, updated_at = ? WHERE id = ?",
                             (status, now_iso(), item_id))
            cur = await db.execute(
                "SELECT id FROM project_items WHERE project_id IS ? AND status = ? "
                "ORDER BY order_index, updated_at", (project_id, status))
            ids = [r["id"] for r in await cur.fetchall()]
            if item_id in ids:
                ids.remove(item_id)
            pos = order_index if order_index is not None else len(ids)
            pos = max(0, min(pos, len(ids)))
            ids.insert(pos, item_id)
            for idx, iid in enumerate(ids):
                await db.execute("UPDATE project_items SET order_index = ? WHERE id = ?", (idx, iid))
            await db.execute("COMMIT")
            return True
        except Exception:
            await db.execute("ROLLBACK")
            raise


def _normalize_project(p: dict) -> dict:
    """Eski (type NULL) loyihalar uchun read-side zaxira — type/default_view/workflow to'ldiradi."""
    if not p.get("type"):
        p["type"] = "smm"
    if not p.get("default_view"):
        p["default_view"] = "calendar"
    if not p.get("workflow"):
        p["workflow"] = json.dumps(config_marketing.default_workflow(p["type"]), ensure_ascii=False)
    return p


async def get_project(project_id: str) -> dict | None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM projects WHERE id = ?", (project_id,))
        row = await cur.fetchone()
        return _normalize_project(dict(row)) if row else None


async def create_project(data: dict) -> str:
    pid = new_id("pr-")
    now = now_iso()
    # Shablon (agar berilgan) type/icon/color/default_view/workflow'ni to'ldiradi;
    # data'dagi aniq qiymatlar ustun turadi. Tur berilmasa — 'smm' (legacy standart).
    tpl = config_marketing.apply_template(data.get("template_id") or "") or {}
    ptype = data.get("type") or tpl.get("type") or "smm"
    if ptype not in config_marketing.PROJECT_TYPES:
        ptype = "custom"
    cfg = config_marketing.PROJECT_TYPES[ptype]
    icon = data.get("icon") or tpl.get("icon") or cfg["icon"]
    default_view = data.get("default_view") or tpl.get("default_view") or cfg["default_view"]
    color = data.get("color") or tpl.get("color") or "#6C5CE7"
    workflow = data.get("workflow")
    if isinstance(workflow, (dict, list)):
        workflow = json.dumps(workflow, ensure_ascii=False)
    if not workflow:
        workflow = json.dumps(config_marketing.default_workflow(ptype), ensure_ascii=False)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO projects
               (id, name, description, color, status, type, icon, default_view, workflow,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (pid, (data.get("name") or "Loyiha").strip(), (data.get("description") or "").strip(),
             color, data.get("status") or "active", ptype, icon, default_view, workflow, now, now))
        await db.commit()
    return pid


async def update_project(project_id: str, data: dict) -> bool:
    allowed = ("name", "description", "color", "status", "type", "icon", "default_view", "workflow")
    fields, values = [], []
    for k in allowed:
        if k in data:
            v = data[k]
            if k == "workflow" and isinstance(v, (dict, list)):
                v = json.dumps(v, ensure_ascii=False)
            fields.append(f"{k} = ?")
            values.append(v.strip() if isinstance(v, str) else v)
    if not fields:
        return False
    fields.append("updated_at = ?"); values.append(now_iso()); values.append(project_id)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(f"UPDATE projects SET {', '.join(fields)} WHERE id = ?", values)
        await db.commit()
        return cur.rowcount > 0


async def delete_project(project_id: str, delete_posts: bool = False) -> bool:
    """Loyihani o'chirish. delete_posts=False → postlar saqlanadi (project_id NULL)."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        if delete_posts:
            await db.execute("DELETE FROM content_posts WHERE project_id = ?", (project_id,))
        else:
            await db.execute(
                "UPDATE content_posts SET project_id = NULL WHERE project_id = ?", (project_id,))
        cur = await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
        await db.commit()
        return cur.rowcount > 0


async def content_dashboard(project_id: str | None = None) -> dict:
    """SMM analitikasi: status/kategoriya/format/platforma/mas'ul taqsimoti,
    bajarilish %, so'nggi 8 hafta joylangan postlar, rad etilganlar sabablari."""
    where = "WHERE project_id = ?" if project_id else ""
    params = (project_id,) if project_id else ()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        rows = [dict(r) for r in await (
            await db.execute(f"SELECT * FROM content_posts {where}", params)).fetchall()]

    def _dist(key):
        d = {}
        for r in rows:
            k = (r.get(key) or "").strip() or "—"
            d[k] = d.get(k, 0) + 1
        return sorted(([k, v] for k, v in d.items()), key=lambda x: -x[1])

    status_count = {s: 0 for s in CONTENT_STATUSES}
    for r in rows:
        st = r.get("status") or "reja"
        status_count[st] = status_count.get(st, 0) + 1
    total = len(rows)
    done = status_count.get("joylandi", 0)
    planned = total - status_count.get("rad_etildi", 0) - status_count.get("bekor", 0)
    progress = round(done / planned * 100) if planned else 0

    # so'nggi 8 hafta — joylangan postlar (published_at yoki date bo'yicha)
    from collections import OrderedDict
    weeks = OrderedDict()
    now = datetime.now(TZ)
    monday = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    for i in range(7, -1, -1):
        wk = monday - timedelta(weeks=i)
        weeks[wk.strftime("%Y-%m-%d")] = 0
    for r in rows:
        if r.get("status") != "joylandi":
            continue
        ds = (r.get("published_at") or r.get("date") or "")[:10]
        try:
            d = TZ.localize(datetime.strptime(ds, "%Y-%m-%d"))
        except (ValueError, TypeError):
            continue
        wk = (d - timedelta(days=d.weekday())).strftime("%Y-%m-%d")
        if wk in weeks:
            weeks[wk] += 1

    reject_reasons = [{"topic": r.get("topic"), "reason": r.get("reject_reason")}
                      for r in rows if r.get("status") == "rad_etildi"]

    return {
        "total": total,
        "status": status_count,
        "progress": progress,
        "by_category": _dist("category"),
        "by_format": _dist("format"),
        "by_platform": _dist("platform"),
        "by_assignee": _dist("assignee"),
        "weekly_published": [{"week": k, "count": v} for k, v in weeks.items()],
        "rejects": reject_reasons,
    }


async def count_table(table: str) -> int:
    """Row count for a known table — used to preview bulk-delete impact."""
    if table not in {"tasks", "meetings", "notes", "reminders", "contacts"}:
        return 0
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(f"SELECT COUNT(*) FROM {table}")
        row = await cur.fetchone()
        return row[0] if row else 0


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


async def filter_existing_task_ids(ids: list[str]) -> set[str]:
    """Subset of `ids` that still exist (one IN query) — used to drop a deleted task
    from the LLM's 'last shown' context so it isn't told to act on a dead id."""
    ids = [i for i in (ids or []) if i]
    if not ids:
        return set()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            f"SELECT id FROM tasks WHERE id IN ({','.join('?' * len(ids))})", ids)
        return {r[0] for r in await cur.fetchall()}


async def list_tasks(status_in: Optional[list[str]] = None, limit: int = 50,
                     include_subtasks: bool = False) -> list[dict]:
    # By default the flat list shows only top-level tasks (subtasks live under their
    # parent). include_subtasks=True drops that filter — used by per-assignee export.
    sub_clause = "" if include_subtasks else "parent_id IS NULL"
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status_in:
            placeholders = ",".join("?" * len(status_in))
            where = f"status IN ({placeholders})" + (f" AND {sub_clause}" if sub_clause else "")
            cur = await db.execute(
                f"""SELECT * FROM tasks
                    WHERE {where}
                    ORDER BY
                      CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                      CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
                      deadline ASC
                    LIMIT ?""",
                (*status_in, limit),
            )
        else:
            where = f"WHERE {sub_clause}" if sub_clause else ""
            cur = await db.execute(
                f"SELECT * FROM tasks {where} ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]


async def list_subtasks(parent_id: str) -> list[dict]:
    """Child tasks of a parent — ordered open-first, then priority. Real tasks
    (own assignee/deadline/status/reminders); excluded from the flat list_tasks."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks WHERE parent_id = ?
               ORDER BY
                 CASE status WHEN 'done' THEN 1 WHEN 'cancelled' THEN 2 ELSE 0 END,
                 CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                 created_at ASC""",
            (parent_id,),
        )
        return [_row_to_task(r) for r in await cur.fetchall()]


async def list_task_categories() -> list[dict]:
    """Active-task counts per category (for the /tasks 'Kategoriyalar' view).
    Uncategorized tasks are grouped under '(boshqa)'. Sorted by count desc."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT COALESCE(NULLIF(TRIM(category), ''), '(boshqa)') AS cat, COUNT(*) AS n
               FROM tasks
               WHERE status IN ('todo','in_progress','blocked')
               GROUP BY cat ORDER BY n DESC, cat ASC""",
        )
        return [{"category": r["cat"], "count": r["n"]} for r in await cur.fetchall()]


async def list_tasks_by_category(category: str, limit: int = 100) -> list[dict]:
    """Active tasks in one category ('(boshqa)' = uncategorized)."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        if category == "(boshqa)":
            where, params = "(category IS NULL OR TRIM(category) = '')", ()
        else:
            where, params = "category = ?", (category,)
        cur = await db.execute(
            f"""SELECT * FROM tasks
                WHERE status IN ('todo','in_progress','blocked') AND parent_id IS NULL AND {where}
                ORDER BY
                  CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                  CASE WHEN deadline IS NULL THEN 1 ELSE 0 END, deadline ASC
                LIMIT ?""",
            (*params, limit),
        )
        return [_row_to_task(r) for r in await cur.fetchall()]


async def count_tasks_in_category(category: str) -> int:
    """Active-task count in a category ('(boshqa)' = uncategorized)."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        if category == "(boshqa)":
            cur = await db.execute(
                "SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress','blocked') "
                "AND (category IS NULL OR TRIM(category) = '')")
        else:
            cur = await db.execute(
                "SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress','blocked') AND category = ?",
                (category,))
        (n,) = await cur.fetchone()
        return n


async def rename_category(old: str, new: str) -> int:
    """Rename a category across all its tasks. Returns rows changed."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "UPDATE tasks SET category = ?, updated_at = ? WHERE category = ?",
            ((new or None), now_iso(), old))
        await db.commit()
        return cur.rowcount


async def clear_category(category: str) -> int:
    """Remove a category label from its tasks (tasks survive → uncategorized).
    Returns rows changed. ('(boshqa)' is already uncategorized → no-op.)"""
    if category == "(boshqa)":
        return 0
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "UPDATE tasks SET category = NULL, updated_at = ? WHERE category = ?",
            (now_iso(), category))
        await db.commit()
        return cur.rowcount


async def delete_tasks_by_category(category: str) -> int:
    """Hard-delete ACTIVE tasks in a category ('(boshqa)' = uncategorized), cascading
    to their subtrees + linked reminders (else subtasks orphan and their reminders
    keep firing). Returns rows deleted. Caller MUST gate this behind a confirmation."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        if category == "(boshqa)":
            sel = await db.execute(
                "SELECT id FROM tasks WHERE status IN ('todo','in_progress','blocked') "
                "AND (category IS NULL OR TRIM(category) = '')")
        else:
            sel = await db.execute(
                "SELECT id FROM tasks WHERE status IN ('todo','in_progress','blocked') AND category = ?",
                (category,))
        roots = [r[0] for r in await sel.fetchall()]
        if not roots:
            return 0
        # Expand to the full subtree of every matched task (subtasks may sit in a
        # different/blank category, so a category-scoped DELETE would miss them).
        all_ids: set[str] = set()
        for rid in roots:
            all_ids.update(await _subtree_ids(db, rid))
        ids = list(all_ids)
        qm = ",".join("?" * len(ids))
        await db.execute(f"DELETE FROM reminders WHERE task_id IN ({qm})", ids)
        cur = await db.execute(f"DELETE FROM tasks WHERE id IN ({qm})", ids)
        await db.commit()
        return cur.rowcount


# ─────────────────── CATEGORIES (first-class: icon, order, archive) ───────────────────

async def create_category(name: str, icon: Optional[str] = None) -> bool:
    """Create a managed category row (may be empty — no tasks yet). Idempotent."""
    name = (name or "").strip()
    if not name or name == "(boshqa)":
        return False
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("SELECT COALESCE(MAX(sort_order), 0) + 1 FROM categories")
        (order,) = await cur.fetchone()
        await db.execute(
            "INSERT OR IGNORE INTO categories (name, icon, archived, sort_order, created_at, updated_at) "
            "VALUES (?, ?, 0, ?, ?, ?)", (name, icon, order, now_iso(), now_iso()))
        if icon:
            await db.execute("UPDATE categories SET icon = ?, updated_at = ? WHERE name = ?",
                             (icon, now_iso(), name))
        await db.commit()
        return True


async def get_category(name: str) -> Optional[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM categories WHERE name = ?", (name,))
        r = await cur.fetchone()
        return dict(r) if r else None


async def update_category(old: str, new_name: Optional[str] = None, icon: Optional[str] = None) -> bool:
    """Rename a category (cascades to tasks.category) and/or set its icon.
    Auto-creates a row for derived/orphan categories so they become managed."""
    old = (old or "").strip()
    if not old:
        return False
    await create_category(old)  # ensure a row exists for orphans
    target = old
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        if new_name and new_name.strip() and new_name.strip() != old:
            new = new_name.strip()[:60]
            cur = await db.execute("SELECT 1 FROM categories WHERE name = ?", (new,))
            if await cur.fetchone():          # target name exists → merge
                await db.execute("DELETE FROM categories WHERE name = ?", (old,))
            else:
                await db.execute("UPDATE categories SET name = ?, updated_at = ? WHERE name = ?",
                                 (new, now_iso(), old))
            await db.execute("UPDATE tasks SET category = ?, updated_at = ? WHERE category = ?",
                             (new, now_iso(), old))
            target = new
        if icon is not None:
            await db.execute("UPDATE categories SET icon = ?, updated_at = ? WHERE name = ?",
                             (icon, now_iso(), target))
        await db.commit()
    return True


async def archive_category(name: str, archived: bool = True) -> bool:
    """Archive/unarchive a category — hidden from the active list, tasks preserved."""
    name = (name or "").strip()
    if not name or name == "(boshqa)":
        return False
    await create_category(name)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("UPDATE categories SET archived = ?, updated_at = ? WHERE name = ?",
                         (1 if archived else 0, now_iso(), name))
        await db.commit()
    return True


async def delete_category_record(name: str) -> int:
    """Remove a category's METADATA row (icon/order/archive). Tasks are untouched
    here — the caller decides whether to clear labels or delete tasks."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("DELETE FROM categories WHERE name = ?", (name,))
        await db.commit()
        return cur.rowcount


async def list_categories(include_archived: bool = False) -> list[dict]:
    """Merged view: managed rows (icon/order/archived) + derived task-category
    strings that have no row yet. Returns active-task counts. include_archived=True
    returns ONLY archived categories (for the archive view)."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT name, icon, archived, sort_order FROM categories")
        rows = {r["name"]: dict(r) for r in await cur.fetchall()}
        cur = await db.execute(
            "SELECT COALESCE(NULLIF(TRIM(category), ''), '(boshqa)') AS cat, COUNT(*) AS n "
            "FROM tasks WHERE status IN ('todo','in_progress','blocked') GROUP BY cat")
        counts = {r["cat"]: r["n"] for r in await cur.fetchall()}
    out, seen = [], set()
    for name, r in rows.items():
        is_arch = bool(r["archived"])
        if is_arch != include_archived:
            continue
        out.append({"name": name, "icon": r["icon"] or "📁", "count": counts.get(name, 0),
                    "archived": is_arch, "sort_order": r["sort_order"]})
        seen.add(name)
    if not include_archived:
        for cat, n in counts.items():
            if cat in seen or cat == "(boshqa)":
                continue
            out.append({"name": cat, "icon": "📁", "count": n, "archived": False, "sort_order": 9999})
        if counts.get("(boshqa)"):
            out.append({"name": "(boshqa)", "icon": "📂", "count": counts["(boshqa)"],
                        "archived": False, "sort_order": 1_000_000})
    out.sort(key=lambda c: (c["sort_order"], -c["count"], c["name"]))
    return out


async def existing_category_names() -> set:
    """Set of REAL category names (managed + in-use), excluding the '(boshqa)'
    placeholder. The supervised allowlist for auto-categorization: on create_task
    the LLM may only assign one of these — it must NEVER invent a new category
    (that caused category sprawl). New categories come only from create_category."""
    return {c["name"] for c in await list_categories()
            if c.get("name") and c["name"] != "(boshqa)"}


async def move_category(name: str, direction: str) -> bool:
    """Reorder a category up/down by swapping sort_order with its active neighbour."""
    if name == "(boshqa)":
        return False
    active = [c for c in await list_categories(include_archived=False) if c["name"] != "(boshqa)"]
    # ensure every active category has a real row (distinct sort_order)
    for c in active:
        await create_category(c["name"])
    fresh = [c for c in await list_categories(include_archived=False) if c["name"] != "(boshqa)"]
    idx = next((i for i, c in enumerate(fresh) if c["name"] == name), None)
    if idx is None:
        return False
    j = idx - 1 if direction == "up" else idx + 1
    if j < 0 or j >= len(fresh):
        return False
    a, b = fresh[idx], fresh[j]
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("UPDATE categories SET sort_order = ?, updated_at = ? WHERE name = ?",
                         (b["sort_order"], now_iso(), a["name"]))
        await db.execute("UPDATE categories SET sort_order = ?, updated_at = ? WHERE name = ?",
                         (a["sort_order"], now_iso(), b["name"]))
        await db.commit()
    return True


async def list_today_tasks() -> list[dict]:
    """STRICT today filter: only tasks whose deadline falls within today's date
    range. Excludes overdue (yesterday and earlier), future, and undated tasks.

    Design choice: undated tasks (created today without an explicit deadline)
    appear in /tasks (Aktiv) but NOT in /today — /today is reserved for items
    that the principal explicitly committed to TODAY."""
    today = datetime.now(TZ).date()
    start_of_day = TZ.localize(datetime.combine(today, datetime.min.time())).isoformat()
    end_of_day = TZ.localize(datetime.combine(today, datetime.max.time())).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress','blocked')
                 AND parent_id IS NULL
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
               WHERE status IN ('todo','in_progress','blocked')
                 AND parent_id IS NULL
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


async def completed_counts_by_day(days: int = 7) -> list[dict]:
    """[{day: 'YYYY-MM-DD', count: n}] of tasks completed per day over the last
    `days` (oldest→newest, every day present). Uses updated_at as the completion
    proxy (a done task's last write ≈ its completion) — see the completed_at
    follow-up. Powers the Mini App insights bar chart."""
    since = (datetime.now(TZ) - timedelta(days=days - 1)).replace(
        hour=0, minute=0, second=0, microsecond=0).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT substr(updated_at,1,10) AS d, COUNT(*) AS n FROM tasks "
            "WHERE status='done' AND updated_at >= ? GROUP BY d", (since,))
        seen = {r[0]: r[1] for r in await cur.fetchall()}
    out = []
    for i in range(days):
        d = (datetime.now(TZ) - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        out.append({"day": d, "count": seen.get(d, 0)})
    return out


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
               WHERE status IN ('todo','in_progress','blocked')
                 AND deadline IS NOT NULL
                 AND deadline >= ? AND deadline <= ?
                 AND reminded_at IS NULL
                 AND priority IN ('P0','P1')
               ORDER BY deadline ASC""",
            (start_iso, end_iso),
        )
        rows = await cur.fetchall()
        return [_row_to_task(r) for r in rows]


async def risk_score_counts() -> dict:
    """One-shot SELECT returning every count needed by handlers.compute_risk_score.
    Replaces the previous N+1 pattern (6 separate queries opening 6 connections)
    with a single aggregate query — same connection, same plan, ~6× faster on
    a cold cache and avoids racy cross-query inconsistencies."""
    now = datetime.now(TZ)
    h24 = (now + timedelta(hours=24)).isoformat()
    h48 = (now + timedelta(hours=48)).isoformat()
    now_iso_val = now.isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT
                  SUM(CASE WHEN deadline IS NOT NULL AND deadline < ? THEN 1 ELSE 0 END) AS overdue,
                  SUM(CASE WHEN deadline IS NOT NULL AND deadline >= ? AND deadline <= ? THEN 1 ELSE 0 END) AS due_24h,
                  SUM(CASE WHEN deadline IS NOT NULL AND deadline >= ? AND deadline <= ? THEN 1 ELSE 0 END) AS due_48h,
                  SUM(CASE WHEN deadline IS NULL THEN 1 ELSE 0 END) AS no_deadline,
                  SUM(CASE WHEN (assignee IS NULL OR TRIM(assignee) = '' OR LOWER(assignee) = 'belgilanmagan') THEN 1 ELSE 0 END) AS unassigned,
                  SUM(CASE WHEN priority = 'P0' THEN 1 ELSE 0 END) AS urgent_open,
                  SUM(CASE WHEN (assignee IS NULL OR TRIM(assignee) = '' OR LOWER(assignee) = 'belgilanmagan')
                           AND deadline IS NOT NULL AND deadline <= ? THEN 1 ELSE 0 END) AS unassigned_due_48h
               FROM tasks
               WHERE status IN ('todo','in_progress','blocked')""",
            (now_iso_val, now_iso_val, h24, now_iso_val, h48, h48),
        )
        row = await cur.fetchone()
    if not row:
        return {k: 0 for k in (
            "overdue", "due_24h", "due_48h", "no_deadline", "unassigned",
            "urgent_open", "unassigned_due_48h",
        )}
    return {k: int(row[k] or 0) for k in row.keys()}


async def list_unassigned_tasks(limit: int = 50) -> list[dict]:
    """Tasks with no assignee (or assigned to 'belgilanmagan')."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress','blocked')
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
               WHERE status IN ('todo','in_progress','blocked') AND deadline IS NULL
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
               WHERE status IN ('todo','in_progress','blocked')
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
               FROM tasks WHERE status IN ('todo','in_progress','blocked')""",
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


async def list_stale_delegations(min_age_days: int = 3, limit: int = 20) -> list[dict]:
    """Tasks delegated to OTHERS, still open, created >= min_age_days ago —
    oldest first (with age_days). Used by the daily delegation auto-chase digest."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT *, julianday('now') - julianday(created_at) AS age_days
               FROM tasks
               WHERE status IN ('todo','in_progress','blocked')
                 AND assignee IS NOT NULL
                 AND LOWER(TRIM(assignee)) NOT IN (
                     '', 'men', 'siz', 'belgilanmagan', '—',
                     'oʻzim', 'o''zim', 'o''z', 'ozim'
                 )
                 AND julianday('now') - julianday(created_at) >= ?
               ORDER BY age_days DESC
               LIMIT ?""",
            (min_age_days, limit),
        )
        # _row_to_task (not raw dict) so JSON columns like `tags` come back as a
        # list — consumers (mini-app task form) call tags.join() and a raw JSON
        # string crashes them. Keeps the extra age_days column.
        return [_row_to_task(r) for r in await cur.fetchall()]


async def list_recurring_tasks(limit: int = 30) -> list[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE recurrence_rule IS NOT NULL
                 AND status IN ('todo','in_progress','blocked')
               ORDER BY
                 CASE WHEN deadline IS NULL THEN 1 ELSE 0 END,
                 deadline ASC
               LIMIT ?""",
            (limit,),
        )
        return [_row_to_task(r) for r in await cur.fetchall()]


async def mark_task_reminded(task_id: str) -> bool:
    """Conditional claim: returns True only if THIS caller flipped reminded_at
    from NULL. Lets the scheduler 'claim then send' so two overlapping sweep
    instances cannot both deliver the same reminder."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "UPDATE tasks SET reminded_at = ? WHERE id = ? AND reminded_at IS NULL",
            (now_iso(), task_id),
        )
        await db.commit()
        return cur.rowcount > 0


def _row_to_task(row) -> dict:
    d = dict(row)
    if d.get("tags"):
        try:
            d["tags"] = json.loads(d["tags"])
        except json.JSONDecodeError:
            logger.warning("Corrupted JSON in tasks.tags for id=%s — defaulting to []", d.get("id"))
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


async def delete_reminder(reminder_id: str) -> bool:
    """Hard-delete a reminder (Mini App 🗑). Returns False if it didn't exist."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,))
        await db.commit()
        return cur.rowcount > 0


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
    """Atomically mark a reminder as sent, or roll it forward if it has a
    recurrence_rule. Single transaction: the conditional UPDATE on
    status='scheduled' ensures only one concurrent caller succeeds, so the
    scheduler cannot fire the same reminder twice during overlapping sweeps."""
    sent_at = now_iso()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM reminders WHERE id = ?", (reminder_id,))
        reminder = await cur.fetchone()
        if reminder is None:
            return False
        if reminder["status"] != "scheduled":
            return False  # already sent / cancelled by another caller
        rule = normalize_recurrence_rule(reminder["recurrence_rule"])
        next_at = compute_next_recurrence(reminder["remind_at"], rule) if rule else None
        if next_at:
            cur = await db.execute(
                "UPDATE reminders SET remind_at = ?, status = 'scheduled', "
                "sent_at = ?, updated_at = ? WHERE id = ? AND status = 'scheduled'",
                (next_at, sent_at, sent_at, reminder_id),
            )
        else:
            cur = await db.execute(
                "UPDATE reminders SET status = 'sent', sent_at = ?, updated_at = ? "
                "WHERE id = ? AND status = 'scheduled'",
                (sent_at, sent_at, reminder_id),
            )
        won = cur.rowcount > 0
        await db.commit()
        return won


def _row_to_reminder(row) -> dict:
    return dict(row)


# ─────────────────────────────────────────── NOTES (Qaydlar) ───────────────────────────────────────────

_NOTES_ALLOWED_STATUSES = ("inbox", "processed", "archived")
_NOTES_ALLOWED_SOURCES = ("forward", "command", "voice", "manual", "llm")


def _derive_title(content: str, max_len: int = 60) -> str:
    """First non-empty line of content, trimmed to max_len. Used when the
    caller doesn't supply an explicit title."""
    first_line = next((ln.strip() for ln in (content or "").splitlines() if ln.strip()), "")
    if not first_line:
        return "Qayd"
    if len(first_line) <= max_len:
        return first_line
    return first_line[: max_len - 1] + "…"


def _row_to_note(row) -> dict:
    d = dict(row)
    if d.get("tags"):
        try:
            d["tags"] = json.loads(d["tags"])
        except json.JSONDecodeError:
            logger.warning("Corrupted JSON in notes.tags for id=%s — defaulting to []", d.get("id"))
            d["tags"] = []
    else:
        d["tags"] = []
    return d


async def create_note(data: dict) -> str:
    """Insert a new note row. Required: content. Everything else is optional
    with sensible defaults. Returns the new note id."""
    content = (data.get("content") or "").strip()
    if not content:
        return ""
    note_id = new_id("n-")
    now = now_iso()
    source = data.get("source", "manual")
    if source not in _NOTES_ALLOWED_SOURCES:
        source = "manual"
    title = (data.get("title") or "").strip() or _derive_title(content)
    tags = data.get("tags") or []
    if not isinstance(tags, list):
        tags = [str(tags)]
    content_html = data.get("content_html")  # optional rich HTML for forwards
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO notes (id, content, content_html, title, source,
                                  source_chat, source_author, source_message_id,
                                  tags, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'inbox', ?, ?)""",
            (
                note_id, content, content_html, title, source,
                data.get("source_chat"), data.get("source_author"),
                data.get("source_message_id"),
                json.dumps(tags, ensure_ascii=False),
                now, now,
            ),
        )
        await db.commit()
    return note_id


async def get_note(note_id: str) -> Optional[dict]:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM notes WHERE id = ?", (note_id,))
        row = await cur.fetchone()
        return _row_to_note(row) if row else None


async def list_notes(status: Optional[str] = "inbox", limit: int = 200) -> list[dict]:
    """Return notes filtered by status (default 'inbox'), newest first.
    Pass status=None to fetch all (used by search/global views)."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        if status:
            cur = await db.execute(
                """SELECT * FROM notes WHERE status = ?
                   ORDER BY created_at DESC LIMIT ?""",
                (status, limit),
            )
        else:
            cur = await db.execute(
                "SELECT * FROM notes ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
        return [_row_to_note(r) for r in await cur.fetchall()]


async def count_notes_in_status(status: str) -> int:
    """Cheap count for the daily briefing's inbox-pending line."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "SELECT COUNT(*) FROM notes WHERE status = ?", (status,),
        )
        row = await cur.fetchone()
        return int(row[0] or 0)


async def search_notes(query: str, limit: int = 30) -> list[dict]:
    """Case-insensitive LIKE search across title + content. Excludes
    archived notes from the default surface."""
    q = f"%{query.strip().lower()}%"
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM notes
               WHERE status != 'archived'
                 AND (LOWER(title) LIKE ? OR LOWER(content) LIKE ?)
               ORDER BY created_at DESC LIMIT ?""",
            (q, q, limit),
        )
        return [_row_to_note(r) for r in await cur.fetchall()]


async def update_note(note_id: str, data: dict) -> bool:
    """Patch a subset of note fields. Mainly used by conversion flows."""
    allowed = {"content", "title", "tags", "status", "converted_to_id",
               "converted_to_type"}
    fields, values = [], []
    for key, value in data.items():
        if key not in allowed:
            continue
        if key == "tags":
            if not isinstance(value, list):
                value = [str(value)]
            value = json.dumps(value, ensure_ascii=False)
        if key == "status" and value not in _NOTES_ALLOWED_STATUSES:
            continue
        fields.append(f"{key} = ?")
        values.append(value)
    if not fields:
        return False
    fields.append("updated_at = ?")
    values.append(now_iso())
    values.append(note_id)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            f"UPDATE notes SET {', '.join(fields)} WHERE id = ?",
            values,
        )
        await db.commit()
        return cur.rowcount > 0


async def archive_note(note_id: str) -> bool:
    return await update_note(note_id, {"status": "archived"})


async def delete_note(note_id: str) -> bool:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("DELETE FROM notes WHERE id = ?", (note_id,))
        await db.commit()
        return cur.rowcount > 0


async def mark_note_processed(note_id: str, converted_to_type: str,
                                converted_to_id: str) -> bool:
    """Flip status='processed' and record the link to the produced task/reminder."""
    if converted_to_type not in ("task", "reminder"):
        return False
    return await update_note(note_id, {
        "status": "processed",
        "converted_to_id": converted_to_id,
        "converted_to_type": converted_to_type,
    })


async def mark_note_done(note_id: str) -> bool:
    """Mark a note processed WITHOUT converting it — 'reviewed, no action needed'.
    Completes the GTD triage: a note can leave the inbox without becoming a
    task/reminder. (mark_note_processed requires a conversion link; this doesn't.)"""
    return await update_note(note_id, {"status": "processed"})


# ─────────────────────────────────────────── MEETINGS ───────────────────────────────────────────

def _agenda_to_text(value) -> Optional[str]:
    """`agenda` is a plain-TEXT column (read back as a string everywhere). Claude
    sends it as a list of bullet points, which SQLite can't bind directly
    ("type 'list' is not supported"). Normalize a list (or any value) to a
    string so the INSERT/UPDATE never fails on it."""
    if value is None:
        return None
    if isinstance(value, (list, tuple)):
        items = [str(x).strip() for x in value if str(x).strip()]
        return "\n".join(f"• {x}" for x in items) if items else None
    return str(value)


# Uchrashuv davomiyligi noma'lum (datetime_end yo'q) bo'lsa — taxminiy uzunlik.
# Bu qiymat handlers._DEFAULT_MEETING_MIN bilan mos.
DEFAULT_MEETING_MINUTES = 60


def _meeting_interval(start_iso: Optional[str],
                      end_iso: Optional[str]) -> Optional[tuple[datetime, datetime]]:
    """Uchrashuv (boshlanish, tugash) oraliqini qaytaradi. datetime_end yo'q yoki
    boshlanishdan keyin bo'lmasa — taxminiy davomiylik qo'shiladi. Boshlanishni
    o'qib bo'lmasa None."""
    start = parse_iso_dt(start_iso)
    if start is None:
        return None
    end = parse_iso_dt(end_iso)
    if end is None or end <= start:
        end = start + timedelta(minutes=DEFAULT_MEETING_MINUTES)
    return start, end


def _intervals_overlap(a_start: datetime, a_end: datetime,
                       b_start: datetime, b_end: datetime) -> bool:
    """Yarim-ochiq [start, end) oraliqlar kesishsa True. Ketma-ket uchrashuvlar
    (biri tugagan payt ikkinchisi boshlansa) to'qnashuv HISOBLANMAYDI."""
    return a_start < b_end and b_start < a_end


async def find_meeting_conflicts(start_iso: Optional[str],
                                 end_iso: Optional[str] = None,
                                 exclude_id: Optional[str] = None) -> list[dict]:
    """Vaqti [start_iso, end_iso) bilan ustma-ust tushadigan faol uchrashuvlarni
    qaytaradi — ikki marta band qilishni (double-booking) oldini olish uchun.
    Yakunlangan uchrashuvlar va boshlanishi o'qilmaydiganlar e'tiborga olinmaydi.
    exclude_id — qayta rejalashtirishda joriy uchrashuvni o'tkazib yuborish uchun."""
    new_iv = _meeting_interval(start_iso, end_iso)
    if new_iv is None:
        return []
    new_start, new_end = new_iv

    # Yangi uchrashuvdan oldin boshlangan uzun uchrashuvni o'tkazib yubormaslik
    # uchun oraliqni bir kun kengaytiramiz; aniq tekshiruv Python tomonida.
    lo = (new_start - timedelta(days=1)).isoformat()
    hi = (new_end + timedelta(days=1)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE datetime_start BETWEEN ? AND ?
                 AND completed_at IS NULL
               ORDER BY datetime_start ASC""",
            (lo, hi),
        )
        rows = await cur.fetchall()

    conflicts: list[dict] = []
    for r in rows:
        m = _row_to_meeting(r)
        if exclude_id and m.get("id") == exclude_id:
            continue
        iv = _meeting_interval(m.get("datetime_start"), m.get("datetime_end"))
        if iv is None:
            continue
        if _intervals_overlap(new_start, new_end, iv[0], iv[1]):
            conflicts.append(m)
    return conflicts


def _as_list(value) -> list:
    """Normalize participants / follow_up_actions to a list. The LLM sometimes
    sends a comma/semicolon/newline STRING instead of a JSON array — stored raw it
    fails json.loads on read and silently resets to [] (data loss). Coerce here."""
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str):
        s = value.replace(";", ",").replace("\n", ",")
        return [p.strip() for p in s.split(",") if p.strip()]
    return []


async def create_meeting(data: dict) -> str:
    meeting_id = new_id("m-")
    # Materialize a default end (start + 60 min) when absent — so free-slot and
    # conflict checks read a real interval instead of an in-code 60-min fallback
    # that they could diverge from (a longer meeting silently shown as 60 min).
    _start = data.get("datetime_start")
    _end = data.get("datetime_end")
    if _start and not _end:
        _sd = parse_iso_dt(_start)
        if _sd:
            _end = (_sd + timedelta(minutes=60)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO meetings (id, title, datetime_start, datetime_end, participants,
                                     location_or_link, agenda, prep_notes, follow_up_actions,
                                     recurrence_rule, recurrence_parent_id, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                meeting_id,
                data.get("title", "Uchrashuv"),
                _start,
                _end,
                json.dumps(_as_list(data.get("participants")), ensure_ascii=False),
                data.get("location_or_link"),
                _agenda_to_text(data.get("agenda")),
                data.get("prep_notes"),
                json.dumps(_as_list(data.get("follow_up_actions")), ensure_ascii=False),
                normalize_recurrence_rule(data.get("recurrence_rule")),
                data.get("recurrence_parent_id"),
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
        "followup_sent_at", "icloud_uid", "recurrence_rule", "recurrence_parent_id",
    }
    # RENAME → propagate into the saved bayonnoma body. The protocol prose is frozen
    # in follow_up_actions at generation time; without this, correcting a mis-heard
    # meeting name leaves the OLD name in the exported bayonnoma (reported bug).
    if "title" in data and "follow_up_actions" not in data:
        async with aiosqlite.connect(config.DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute(
                "SELECT title, follow_up_actions FROM meetings WHERE id = ?", (meeting_id,))
            row = await cur.fetchone()
        if row:
            old_title = (row["title"] or "").strip()
            new_title = (data.get("title") or "").strip()
            try:
                body = json.loads(row["follow_up_actions"]) if row["follow_up_actions"] else []
            except (json.JSONDecodeError, TypeError):
                body = []
            if old_title and new_title and old_title != new_title and body:
                fixed = [b.replace(old_title, new_title) if isinstance(b, str) else b for b in body]
                if fixed != body:
                    data = {**data, "follow_up_actions": fixed}
    fields = []
    values: list[Any] = []
    for key, value in data.items():
        if key not in allowed:
            continue
        fields.append(f"{key} = ?")
        if key in ("participants", "follow_up_actions"):
            value = json.dumps(_as_list(value), ensure_ascii=False)  # coerce str→list (no [] data loss)
        elif key == "agenda":
            value = _agenda_to_text(value)
        elif key == "recurrence_rule":
            value = normalize_recurrence_rule(value)
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
        if cur.rowcount > 0:
            # Cascade — no FK ON DELETE; drop linked reminders so none fire for a
            # meeting that no longer exists.
            await db.execute("DELETE FROM reminders WHERE meeting_id = ?", (meeting_id,))
        await db.commit()
        return cur.rowcount > 0


async def complete_meeting(meeting_id: str) -> bool:
    """Mark a meeting as attended/done. It then drops out of the active
    (Bugun/Haftalik/…) views and shows with a ✅ in O'tgan.

    If the meeting is recurring, completing it spawns the next occurrence
    (mirrors task recurrence). Only the first concurrent caller — the one whose
    conditional UPDATE flips completed_at from NULL — owns the spawn, so two
    overlapping completes can't create two copies of next week's meeting."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM meetings WHERE id = ?", (meeting_id,))
        before = await cur.fetchone()
        if before is None:
            return False
        cur = await db.execute(
            "UPDATE meetings SET completed_at = ? WHERE id = ? AND completed_at IS NULL",
            (now_iso(), meeting_id),
        )
        won = cur.rowcount > 0
        await db.commit()
    if won and before["recurrence_rule"]:
        await create_next_recurring_meeting(_row_to_meeting(before))
    return won or before["completed_at"] is not None


async def create_next_recurring_meeting(completed_meeting: dict) -> Optional[str]:
    """Spawn the next occurrence of a recurring meeting, preserving the duration
    (end − start) and shifting the whole interval forward by the rule. Dedup: if
    an un-completed occurrence in this chain already exists, don't create another."""
    rule = normalize_recurrence_rule(completed_meeting.get("recurrence_rule"))
    if not rule:
        return None
    start = completed_meeting.get("datetime_start")
    next_start = compute_next_recurrence(start, rule)
    if not next_start:
        return None
    # Preserve the meeting length by shifting end by the same start→next_start delta.
    next_end = None
    sd, ed = parse_iso_dt(start), parse_iso_dt(completed_meeting.get("datetime_end"))
    nsd = parse_iso_dt(next_start)
    if sd and ed and nsd:
        next_end = (nsd + (ed - sd)).isoformat()
    chain_id = completed_meeting.get("recurrence_parent_id") or completed_meeting.get("id")
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            """SELECT id FROM meetings
               WHERE recurrence_parent_id IN (?, ?) AND completed_at IS NULL LIMIT 1""",
            (chain_id, completed_meeting.get("id")),
        )
        existing = await cur.fetchone()
    if existing:
        return existing[0]
    return await create_meeting({
        "title": completed_meeting.get("title") or "Uchrashuv",
        "datetime_start": next_start,
        "datetime_end": next_end,
        "participants": completed_meeting.get("participants", []),
        "location_or_link": completed_meeting.get("location_or_link"),
        "agenda": completed_meeting.get("agenda"),
        "recurrence_rule": rule,
        "recurrence_parent_id": chain_id,
    })


async def uncomplete_meeting(meeting_id: str) -> bool:
    """Undo a 'done' mark — returns the meeting to the active views."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "UPDATE meetings SET completed_at = NULL WHERE id = ?",
            (meeting_id,),
        )
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


async def mark_meeting_reminded(meeting_id: str) -> bool:
    """Conditional claim — see mark_task_reminded."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "UPDATE meetings SET reminded_at = ? WHERE id = ? AND reminded_at IS NULL",
            (now_iso(), meeting_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def mark_meeting_prep_sent(meeting_id: str) -> bool:
    """Conditional claim — see mark_task_reminded."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "UPDATE meetings SET prep_sent_at = ? WHERE id = ? AND prep_sent_at IS NULL",
            (now_iso(), meeting_id),
        )
        await db.commit()
        return cur.rowcount > 0


async def mark_meeting_followup_sent(meeting_id: str) -> bool:
    """Conditional claim — see mark_task_reminded."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "UPDATE meetings SET followup_sent_at = ? WHERE id = ? AND followup_sent_at IS NULL",
            (now_iso(), meeting_id),
        )
        await db.commit()
        return cur.rowcount > 0


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
                logger.warning("Corrupted JSON in meetings.%s for id=%s — defaulting to []",
                                key, d.get("id"))
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


async def list_meetings_to_push(days: int = 60, limit: int = 200) -> list[dict]:
    """Upcoming, active meetings NOT yet on iCloud — i.e. bot meetings that should be
    backfilled to the principal's calendar. Excludes completed and already-synced
    (icloud_uid set → either pushed or imported FROM iCloud) so we never re-push or
    push imported events back."""
    now = now_iso()
    hi = (datetime.now(TZ) + timedelta(days=days)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE datetime_start >= ? AND datetime_start <= ?
                 AND completed_at IS NULL
                 AND (icloud_uid IS NULL OR icloud_uid = '')
               ORDER BY datetime_start ASC LIMIT ?""",
            (now, hi, limit),
        )
        return [_row_to_meeting(r) for r in await cur.fetchall()]


async def set_meeting_icloud_uid(meeting_id: str, uid: str) -> None:
    """Tag a meeting with its iCloud event UID (so it isn't re-pushed / re-imported)."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute("UPDATE meetings SET icloud_uid = ? WHERE id = ?", (uid, meeting_id))
        await db.commit()


async def list_meetings_with_protocol(limit: int = 100) -> list[dict]:
    """Meetings whose follow_up_actions hold a saved protocol (bayonnoma) — newest
    meeting first. The caller filters protocol-text from task-id lists (the same
    column is reused by the post-meeting task flow). Powers the central
    'Bayonnomalar' list."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM meetings
               WHERE follow_up_actions IS NOT NULL
                 AND TRIM(follow_up_actions) NOT IN ('', '[]', 'null')
               ORDER BY datetime_start DESC
               LIMIT ?""",
            (limit,),
        )
        return [_row_to_meeting(r) for r in await cur.fetchall()]


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

        cur = await db.execute(
            """SELECT * FROM notes
               WHERE status != 'archived'
                 AND (LOWER(title) LIKE ? OR LOWER(content) LIKE ?)
               ORDER BY created_at DESC LIMIT ?""",
            (q, q, limit),
        )
        notes = [_row_to_note(r) for r in await cur.fetchall()]

    return {"tasks": tasks, "meetings": meetings, "contacts": contacts,
            "reminders": reminders, "notes": notes,
            "total": len(tasks) + len(meetings) + len(contacts) + len(reminders) + len(notes)}


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
            "SELECT priority, COUNT(*) AS n FROM tasks WHERE status IN ('todo','in_progress','blocked') GROUP BY priority"
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
        active_count = await count("SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress','blocked')")
        overdue_count = await count(
            """SELECT COUNT(*) FROM tasks
               WHERE status IN ('todo','in_progress','blocked') AND deadline IS NOT NULL AND deadline < ?""",
            (now.isoformat(),),
        )
        no_deadline_count = await count(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress','blocked') AND deadline IS NULL"
        )
        due_24_count = await count(
            """SELECT COUNT(*) FROM tasks
               WHERE status IN ('todo','in_progress','blocked') AND deadline BETWEEN ? AND ?""",
            (now.isoformat(), next_24.isoformat()),
        )
        due_48_count = await count(
            """SELECT COUNT(*) FROM tasks
               WHERE status IN ('todo','in_progress','blocked') AND deadline BETWEEN ? AND ?""",
            (now.isoformat(), next_48.isoformat()),
        )
        due_7_count = await count(
            """SELECT COUNT(*) FROM tasks
               WHERE status IN ('todo','in_progress','blocked') AND deadline BETWEEN ? AND ?""",
            (now.isoformat(), next_7.isoformat()),
        )
        recurring_count = await count(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress','blocked') AND recurrence_rule IS NOT NULL"
        )

        cur = await db.execute(
            """SELECT priority, COUNT(*) AS n FROM tasks
               WHERE status IN ('todo','in_progress','blocked') GROUP BY priority"""
        )
        by_priority = {r["priority"]: r["n"] for r in await cur.fetchall()}

        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress','blocked') AND deadline IS NOT NULL AND deadline < ?
               ORDER BY deadline ASC LIMIT 5""",
            (now.isoformat(),),
        )
        overdue_tasks = [_row_to_task(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress','blocked') AND deadline IS NULL
               ORDER BY
                 CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 WHEN 'P2' THEN 2 ELSE 3 END,
                 created_at ASC LIMIT 5"""
        )
        no_deadline_tasks = [_row_to_task(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT * FROM tasks
               WHERE status IN ('todo','in_progress','blocked') AND deadline BETWEEN ? AND ?
               ORDER BY deadline ASC LIMIT 8""",
            (now.isoformat(), next_48.isoformat()),
        )
        risk_tasks = [_row_to_task(r) for r in await cur.fetchall()]

        cur = await db.execute(
            """SELECT assignee, COUNT(*) AS total,
                      SUM(CASE WHEN deadline IS NOT NULL AND deadline < ? THEN 1 ELSE 0 END) AS overdue
               FROM tasks
               WHERE status IN ('todo','in_progress','blocked')
                 AND assignee IS NOT NULL
                 AND LOWER(TRIM(assignee)) NOT IN (
                     '', 'men', 'siz', 'belgilanmagan', '—',
                     'oʻzim', 'o''zim', 'o''z', 'ozim'
                 )
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
        "risk_score": risk_score,
    }


# ─────────────────────────────────────────── CONTACTS ───────────────────────────────────────────

async def save_contact(data: dict) -> str:
    """Idempotent upsert by contact name. Uses a single INSERT … ON CONFLICT
    statement so two concurrent callers with the same name cannot both pass
    the existence check and race to INSERT — the old SELECT-then-INSERT
    pattern crashed on the second caller with a UNIQUE constraint violation."""
    # Whitespace-decorated names must never enter the DB — a trailing space in a
    # contact defeats the assignee canonical-casing lookup on Excel import.
    name = (data.get("name") or "").strip()
    if not name:
        return ""
    candidate_id = new_id("c-")
    role = data.get("role")
    formality = data.get("formality_level")
    preferred = data.get("preferred_channel")
    now = now_iso()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        # excluded.* are the "would-have-inserted" values from the VALUES clause.
        # COALESCE(excluded.col, contacts.col) preserves existing data when the
        # caller didn't provide a new value (matches original update-only-if-set logic).
        cur = await db.execute(
            """INSERT INTO contacts (id, name, role, formality_level, preferred_channel,
                                     last_interaction, created_at)
               VALUES (?, ?, ?, COALESCE(?, 3), ?, ?, ?)
               ON CONFLICT(name) DO UPDATE SET
                   role = COALESCE(excluded.role, contacts.role),
                   formality_level = COALESCE(?, contacts.formality_level),
                   preferred_channel = COALESCE(excluded.preferred_channel, contacts.preferred_channel),
                   last_interaction = excluded.last_interaction
               RETURNING id""",
            (candidate_id, name, role, formality, preferred, now, now, formality),
        )
        row = await cur.fetchone()
        await db.commit()
        return row["id"] if row else ""


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
    "morning_briefing_time": "09:00",
    "evening_summary_time": "18:00",
    "meeting_reminder_min": 15,
    "task_reminder_hours": 2,
    # Quiet hours — default OFF so existing users see no behavior change
    # until they explicitly enable via /settings.
    "quiet_hours_enabled": False,
    "quiet_hours_start": "22:00",
    "quiet_hours_end": "07:00",
    # Voice: auto-process the transcript without an extra confirm tap.
    # Default ON — power users can re-enable confirmation via /settings.
    "voice_auto_confirm": config.VOICE_AUTO_CONFIRM,
    # Confirm before creating tasks/meetings. Default ON for safety so a
    # mis-transcribed voice message can't quietly create wrong items.
    "confirm_create_actions": config.CONFIRM_CREATE_ACTIONS,
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
            logger.error(
                "principal_profile.settings JSON is corrupted — falling back to "
                "defaults. User-customised reminder/briefing times have been lost "
                "until /settings is used again."
            )
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
                """DELETE FROM conversation_history
                   WHERE id IN (
                       SELECT id FROM conversation_history
                       ORDER BY id ASC LIMIT ?
                   )""",
                (total - keep,),
            )
            await db.commit()


async def purge_old_conversation_history(retention_days: int) -> int:
    """Time-based retention purge — drops every conversation_history row
    older than retention_days. Complements trim_history's count cap to
    satisfy NBU / banking data retention rules (1–3 year limits depending
    on data class). Returns rows deleted."""
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now(TZ) - timedelta(days=retention_days)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "DELETE FROM conversation_history WHERE created_at < ?",
            (cutoff,),
        )
        await db.commit()
        return cur.rowcount


async def llm_cost_breakdown(days: int = 7) -> dict:
    """Cost analytics for the past N days, broken down by model and cache
    hit/miss. Used by /diagnostics and the executive dashboard to track
    where Claude spend is going and whether prompt caching is actually
    working as intended."""
    cutoff = (datetime.now(TZ) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT
                  model,
                  COUNT(*) AS calls,
                  SUM(COALESCE(input_tokens, 0)) AS input_tokens,
                  SUM(COALESCE(output_tokens, 0)) AS output_tokens,
                  SUM(COALESCE(cache_read_tokens, 0)) AS cache_read,
                  SUM(COALESCE(cache_creation_tokens, 0)) AS cache_write,
                  SUM(COALESCE(estimated_cost_usd, 0)) AS cost_usd
               FROM llm_audit_log
               WHERE ts >= ? AND error IS NULL
               GROUP BY model
               ORDER BY cost_usd DESC""",
            (cutoff,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    totals = {
        "calls": sum(r["calls"] or 0 for r in rows),
        "input_tokens": sum(r["input_tokens"] or 0 for r in rows),
        "output_tokens": sum(r["output_tokens"] or 0 for r in rows),
        "cache_read": sum(r["cache_read"] or 0 for r in rows),
        "cache_write": sum(r["cache_write"] or 0 for r in rows),
        "cost_usd": round(sum(float(r["cost_usd"] or 0) for r in rows), 4),
    }
    # Cache hit rate = cache_read / (input_tokens + cache_read). If it's
    # >50%, prompt caching is paying off significantly.
    denom = totals["input_tokens"] + totals["cache_read"]
    totals["cache_hit_rate"] = round(totals["cache_read"] / denom, 3) if denom else 0.0
    return {"by_model": rows, "totals": totals, "days": days}


# ───────────────── SELF-IMPROVEMENT — PERCEPTION (Phase 1, read-only) ─────────────────
# Queryable telemetry signals for the supervised self-improvement subsystem. Pure
# reads over existing tables (llm_audit_log / corrections / conversation_history) —
# no new tables, no writes, no behaviour change. Consumed by metrics.py.

async def llm_error_breakdown(days: int = 7) -> dict:
    """Error / fallback rate over the past N days. A row with a non-NULL `error` is a
    turn that fell back to a degraded response, so error_calls == fallbacks. Returns
    totals + breakdown by error label and by purpose-family (user vs internal)."""
    cutoff = (datetime.now(TZ) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT error, purpose, COUNT(*) AS n
                 FROM llm_audit_log
                WHERE ts >= ?
                GROUP BY error, purpose""",
            (cutoff,),
        )
        rows = [dict(r) for r in await cur.fetchall()]

    def _family(purpose):
        p = (purpose or "").lower()
        if p.startswith("internal"):
            return "internal"
        if p.startswith("user") or p == "document":
            return "user"
        return "other"

    total = sum(r["n"] for r in rows)
    error_calls = sum(r["n"] for r in rows if r["error"])
    by_label: dict = {}
    by_family: dict = {}
    for r in rows:
        fam = by_family.setdefault(_family(r["purpose"]), {"calls": 0, "errors": 0})
        fam["calls"] += r["n"]
        if r["error"]:
            by_label[r["error"]] = by_label.get(r["error"], 0) + r["n"]
            fam["errors"] += r["n"]
    return {
        "window_days": days,
        "total_calls": total,
        "error_calls": error_calls,
        "error_rate": round(error_calls / total, 4) if total else 0.0,
        "by_label": sorted(
            ({"label": k, "calls": v} for k, v in by_label.items()),
            key=lambda x: -x["calls"]),
        "by_family": by_family,
    }


async def correction_frequency(days: int = 30, limit: int = 500) -> dict:
    """Style/behaviour corrections the principal made in the window. Theming is
    derived downstream (metrics.py) from `reason`/`context` — there is no theme
    column. Returns the count + the rows (newest first)."""
    cutoff = (datetime.now(TZ) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT context, correction, reason, created_at FROM corrections "
            "WHERE created_at >= ? ORDER BY created_at DESC LIMIT ?",
            (cutoff, limit),
        )
        items = [dict(r) for r in await cur.fetchall()]
    return {"window_days": days, "total": len(items), "items": items}


async def cost_trend_by_day(days: int = 14) -> dict:
    """Per-day cost / token / call / error trend. Latency is NOT recorded in
    llm_audit_log, so this is cost+token based. Day = local-TZ date prefix of `ts`."""
    cutoff = (datetime.now(TZ) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT substr(ts, 1, 10) AS day,
                      COUNT(*) AS calls,
                      SUM(CASE WHEN error IS NOT NULL THEN 1 ELSE 0 END) AS errors,
                      SUM(COALESCE(input_tokens, 0) + COALESCE(output_tokens, 0)) AS tokens,
                      SUM(COALESCE(estimated_cost_usd, 0)) AS cost_usd
                 FROM llm_audit_log
                WHERE ts >= ?
                GROUP BY day
                ORDER BY day ASC""",
            (cutoff,),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    for r in rows:
        r["cost_usd"] = round(float(r["cost_usd"] or 0), 4)
        r["tokens"] = int(r["tokens"] or 0)
    return {"window_days": days, "by_day": rows}


async def recent_conversation(days: int = 7, limit: int = 500) -> list[dict]:
    """Chronological (oldest→newest) conversation turns in the window — role,
    content, created_at. Feeds metrics.py's 'unmet request' rephrase heuristic.
    Takes the most-recent `limit` rows, then returns them oldest-first."""
    cutoff = (datetime.now(TZ) - timedelta(days=days)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT role, content, created_at FROM conversation_history "
            "WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
            (cutoff, limit),
        )
        rows = [dict(r) for r in await cur.fetchall()]
    rows.reverse()
    return rows


# ───────────────── SELF-IMPROVEMENT — PROPOSALS (Phase 2) ─────────────────
# improvement_proposals: the queue of supervised improvement proposals (Channel A
# nightly auto-diagnosis, or Channel B /improve in Phase 3). Surfacing & approval
# live in Phase 3 — these are plain CRUD helpers. Unknown fix_kind/status/source
# from a malformed LLM proposal are coerced to safe defaults.

_PROPOSAL_FIX_KINDS = {"prompt", "code", "config", "data", "feature"}
_PROPOSAL_STATUSES = {"new", "approved", "rejected", "in_progress", "pr_open",
                      "merged", "deployed", "reverted", "done", "requires_manual"}


async def create_improvement_proposal(data: dict) -> str:
    """Insert one proposal; returns its id ('imp-…')."""
    pid = new_id("imp-")
    fix_kind = (data.get("fix_kind") or "").strip().lower()
    if fix_kind not in _PROPOSAL_FIX_KINDS:
        fix_kind = "code"
    status = (data.get("status") or "new").strip().lower()
    if status not in _PROPOSAL_STATUSES:
        status = "new"
    source = (data.get("source") or "auto").strip().lower()
    if source not in ("auto", "manual"):
        source = "auto"
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            """INSERT INTO improvement_proposals
                 (id, created_at, source, title, problem, evidence, root_cause,
                  fix_kind, proposed_change, impact_estimate, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (pid, datetime.now(TZ).isoformat(), source, (data.get("title") or "—")[:200],
             data.get("problem"), data.get("evidence"), data.get("root_cause"),
             fix_kind, data.get("proposed_change"), data.get("impact_estimate"), status),
        )
        await db.commit()
    return pid


async def list_improvement_proposals(status_in: "list | None" = None, limit: int = 50) -> list[dict]:
    q = "SELECT * FROM improvement_proposals"
    params: list = []
    if status_in:
        q += " WHERE status IN (%s)" % ",".join("?" * len(status_in))
        params += list(status_in)
    q += " ORDER BY created_at DESC LIMIT ?"
    params.append(limit)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]


async def get_improvement_proposal(pid: str) -> "dict | None":
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT * FROM improvement_proposals WHERE id = ?", (pid,))
        row = await cur.fetchone()
        return dict(row) if row else None


async def update_proposal_status(pid: str, status: str) -> bool:
    status = (status or "").strip().lower()
    if status not in _PROPOSAL_STATUSES:
        return False
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "UPDATE improvement_proposals SET status = ? WHERE id = ?", (status, pid))
        await db.commit()
        return cur.rowcount > 0


async def count_proposals_by_status() -> dict:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            "SELECT status, COUNT(*) AS n FROM improvement_proposals GROUP BY status")
        return {r["status"]: r["n"] for r in await cur.fetchall()}


# Full audit trail for the self-improvement loop (spec §9 #6): every prepare /
# push / merge / reject / deploy is logged here, reviewable end to end.
async def log_si_audit(action: str, proposal_id: "str | None" = None,
                       detail: "str | None" = None) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            "INSERT INTO self_improvement_audit (ts, proposal_id, action, detail) "
            "VALUES (?,?,?,?)",
            (datetime.now(TZ).isoformat(), proposal_id, action, (detail or "")[:2000]))
        await db.commit()


async def si_daily_op_count(since_iso: str,
                            actions: tuple = ("implement_started", "diagnose_started")) -> int:
    """Count self_improvement_audit rows for LLM-spending SI ops since `since_iso`
    — the daily circuit-breaker counter (caps runaway diagnosis/implementation)."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        placeholders = ",".join("?" * len(actions))
        cur = await db.execute(
            f"SELECT COUNT(*) FROM self_improvement_audit "
            f"WHERE action IN ({placeholders}) AND ts >= ?",
            (*actions, since_iso))
        row = await cur.fetchone()
        return int(row[0]) if row else 0


async def list_si_audit(limit: int = 50, proposal_id: "str | None" = None) -> list[dict]:
    q = "SELECT * FROM self_improvement_audit"
    params: list = []
    if proposal_id:
        q += " WHERE proposal_id = ?"
        params.append(proposal_id)
    q += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(q, params)
        return [dict(r) for r in await cur.fetchall()]


async def purge_old_audit_logs(retention_days: int) -> int:
    """Same idea for the LLM audit log table — used by scheduler nightly."""
    if retention_days <= 0:
        return 0
    cutoff = (datetime.now(TZ) - timedelta(days=retention_days)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "DELETE FROM llm_audit_log WHERE ts < ?",
            (cutoff,),
        )
        await db.commit()
        return cur.rowcount


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


# ─────────────────────────────────────────── PENDING ACTIONS (idempotency) ───────────────────────────────────────────


async def enqueue_pending_action(update_id: Optional[int], chat_id: Optional[int],
                                  message_id: Optional[int], user_text: str) -> Optional[int]:
    """Record that we're about to process a user message. Returns the row id,
    or None if this update_id was already processed (idempotency win — a
    redelivered Telegram update won't trigger a second Claude call)."""
    now = now_iso()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        try:
            cur = await db.execute(
                """INSERT INTO pending_actions
                   (update_id, chat_id, message_id, user_text, state, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'pending', ?, ?)""",
                (update_id, chat_id, message_id, user_text, now, now),
            )
            await db.commit()
            return cur.lastrowid
        except aiosqlite.IntegrityError:
            # (chat_id, message_id) UNIQUE hit — this message was already enqueued,
            # i.e. Telegram redelivered the same update. Swallow the duplicate.
            logger.info("Skipping duplicate message chat=%s msg=%s", chat_id, message_id)
            return None


async def mark_pending_in_progress(pending_id: int) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            "UPDATE pending_actions SET state='in_progress', updated_at=? WHERE id=?",
            (now_iso(), pending_id),
        )
        await db.commit()


async def complete_pending_action(pending_id: int) -> None:
    """Mark an action as completed. The row is kept (not deleted) so it
    serves as an idempotency record for the update_id."""
    now = now_iso()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            "UPDATE pending_actions SET state='completed', updated_at=?, completed_at=? WHERE id=?",
            (now, now, pending_id),
        )
        await db.commit()


async def fail_pending_action(pending_id: int, error: str) -> None:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            "UPDATE pending_actions SET state='failed', error=?, updated_at=? WHERE id=?",
            (error[:500], now_iso(), pending_id),
        )
        await db.commit()


async def list_recent_actions(limit: int = 10) -> list[dict]:
    """Audit trail — recently processed user actions (completed/failed),
    newest first. Used by the diagnostics 'So'nggi amallar' section."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT user_text, state, error, created_at, completed_at, updated_at
               FROM pending_actions
               WHERE state IN ('completed', 'failed')
               ORDER BY COALESCE(completed_at, updated_at) DESC
               LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def get_share_text(token: str) -> Optional[str]:
    """Inline ulashish keshidan matnni token (id) bo'yicha o'qiydi. DB-backed —
    bot restart'da ham yo'qolmaydi."""
    if not str(token or "").isdigit():
        return None
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("SELECT content FROM share_cache WHERE id = ?", (int(token),))
        row = await cur.fetchone()
        return row[0] if row else None


async def list_stuck_pending_actions(stuck_after_minutes: int = 5) -> list[dict]:
    """Return rows still in pending/in_progress state past the timeout —
    these likely belong to a crashed handler. Caller decides whether to
    notify the principal, retry, or just log."""
    cutoff = (datetime.now(TZ) - timedelta(minutes=stuck_after_minutes)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(
            """SELECT * FROM pending_actions
               WHERE state IN ('pending','in_progress') AND updated_at < ?
               ORDER BY updated_at ASC""",
            (cutoff,),
        )
        return [dict(r) for r in await cur.fetchall()]


async def purge_old_pending_actions(retention_days: int = 7) -> int:
    """Drop completed/failed rows older than retention_days. Returns rows
    deleted. Keeps the table bounded over time."""
    cutoff = (datetime.now(TZ) - timedelta(days=retention_days)).isoformat()
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute(
            "DELETE FROM pending_actions WHERE state IN ('completed','failed') AND updated_at < ?",
            (cutoff,),
        )
        await db.commit()
        return cur.rowcount


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


async def mark_icloud_retry_dead(retry_id: int, error: str) -> None:
    """Permanently retire a retry row — sets attempts past the max so the
    list_due_icloud_retries() WHERE clause filters it out. Use for
    unrecoverable failures (auth, missing calendar) where further retries
    would just spam logs without ever succeeding."""
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        await db.execute(
            "UPDATE icloud_retry_queue SET attempts = ?, last_error = ? WHERE id = ?",
            (len(_RETRY_BACKOFFS_SECONDS), error[:500], retry_id),
        )
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
