"""Telegram Mini App backend — a small aiohttp API that exposes the bot's own
database (tasks / meetings / notes / reminders) to a web UI opened inside Telegram.

Design:
  • Reuses database.py verbatim — no duplicated business logic.
  • Auth = Telegram Mini App `initData`: every /api request must carry a valid,
    HMAC-signed initData (Authorization: "tma <initData>"); the signing key is the
    bot token, so only a real Telegram client can produce it, and we additionally
    restrict access to config.PRINCIPAL_USER_ID (single-user bot).
  • Binds to 127.0.0.1 only — a public nginx reverse proxy terminates TLS and
    forwards; the app process is never directly exposed.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web

import config
import database

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "webapp_static"
_MAX_INITDATA_AGE = 24 * 60 * 60  # reject initData older than 24h (replay guard)


# ───────────────────────── auth ─────────────────────────

def validate_init_data(init_data: str, bot_token: str,
                       max_age: int = _MAX_INITDATA_AGE) -> dict | None:
    """Verify a Telegram Mini App initData string. Returns the parsed `user` dict
    on success, else None. Implements the canonical algorithm:
        secret_key   = HMAC_SHA256(key="WebAppData", msg=bot_token)
        expected     = HMAC_SHA256(key=secret_key,  msg=data_check_string)
    where data_check_string is every field except `hash`, sorted by key, joined
    "k=v" with newlines. Also enforces auth_date freshness."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    # Newer clients add a `signature` (Ed25519, for third-party validation) that is
    # NOT part of the bot-token HMAC data_check_string — exclude it.
    pairs.pop("signature", None)
    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, received_hash):
        return None
    # Freshness: reject stale/replayed initData.
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if max_age and (time.time() - auth_date) > max_age:
        return None
    try:
        return json.loads(pairs.get("user", "null"))
    except (ValueError, TypeError):
        return None


@web.middleware
async def error_middleware(request: web.Request, handler):
    """Outermost safety net: an unexpected exception in any handler becomes a clean
    JSON 500 (logged server-side) instead of a raw aiohttp crash page. HTTP responses
    raised deliberately (400/401/403/404) pass through unchanged."""
    try:
        return await handler(request)
    except web.HTTPException:
        raise
    except Exception:
        logger.exception("Unhandled error in %s %s", request.method, request.path)
        return web.json_response({"error": "server error"}, status=500)


@web.middleware
async def auth_middleware(request: web.Request, handler):
    """Gate every /api/* route behind a valid initData for the principal. Static
    files and the health check are public (they carry no data)."""
    path = request.path
    if not path.startswith("/api/") or path == "/api/health":
        return await handler(request)
    header = request.headers.get("Authorization", "")
    init_data = header[4:].strip() if header.lower().startswith("tma ") else ""
    user = validate_init_data(init_data, config.TELEGRAM_BOT_TOKEN)
    if not user:
        logger.info("webapp AUTH-FAIL %s (Authorization len=%d, initData len=%d)",
                    path, len(header), len(init_data))
        return web.json_response({"error": "unauthorized"}, status=401)
    if int(user.get("id", 0)) != int(config.PRINCIPAL_USER_ID):
        # A validly-signed initData from a DIFFERENT Telegram user — not the owner.
        logger.info("webapp FORBIDDEN %s uid=%s (principal=%s)",
                    path, user.get("id"), config.PRINCIPAL_USER_ID)
        return web.json_response({"error": "forbidden"}, status=403)
    logger.info("webapp OK %s uid=%s", path, user.get("id"))
    request["user"] = user
    return await handler(request)


# ───────────────────────── helpers ─────────────────────────

async def _json_body(request: web.Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, ValueError):
        return {}


def _bad(msg: str) -> web.HTTPBadRequest:
    return web.HTTPBadRequest(text=json.dumps({"error": msg}), content_type="application/json")


# List-shaped columns the DB layer coerces itself (via _as_list / json.dumps).
_LIST_FIELDS = frozenset({"tags", "participants"})
# Per-field length caps — reject over-long strings so one request can't write a
# multi-MB row (no rate limit; a buggy/compromised client could otherwise bloat the DB).
_MAX_LEN = {"title": 512, "description": 8000, "content": 20000, "note": 4000,
            "agenda": 16000, "prep_notes": 8000, "location_or_link": 1024,
            "assignee": 256, "category": 128, "priority": 8, "status": 20,
            "recurrence_rule": 32, "deadline": 64, "datetime_start": 64,
            "datetime_end": 64, "remind_at": 64, "task_id": 64, "parent_id": 64}


def _pick(data: dict, fields: tuple[str, ...]) -> dict:
    """Whitelist incoming fields (never pass arbitrary client keys to the DB) AND
    validate value shape: a non-scalar value for a scalar column, or an over-long
    string, is rejected with 400 instead of blowing up as a raw sqlite 500."""
    out = {}
    for k in fields:
        if k not in data:
            continue
        v = data[k]
        if k in _LIST_FIELDS:
            out[k] = v            # DB coerces list/str → JSON list
            continue
        if isinstance(v, (dict, list)):
            raise _bad(f"'{k}' noto'g'ri turdagi qiymat")
        if isinstance(v, str) and len(v) > _MAX_LEN.get(k, 100000):
            raise _bad(f"'{k}' juda uzun (max {_MAX_LEN.get(k)})")
        out[k] = v
    return out


async def _reject_bad_parent(pid: str, self_id: str | None) -> None:
    """Guard parent_id on tasks: must exist, not be itself, and not create a cycle
    (a cycle makes delete_task cascade over the loop and silently drop an unrelated
    task). Raises 400 on any violation. No-op for a blank parent_id."""
    pid = (pid or "").strip()
    if not pid:
        return
    if pid == self_id:
        raise _bad("vazifa o'ziga ota bo'la olmaydi")
    hops = 0
    cur = pid
    while cur and hops < 64:
        node = await database.get_task(cur)
        if not node:
            raise _bad("ota vazifa topilmadi")
        if self_id and node.get("parent_id") == self_id:
            raise _bad("halqa (cycle) hosil bo'ladi")
        cur = node.get("parent_id")
        hops += 1


_TASK_FIELDS = ("title", "description", "deadline", "priority", "status",
                "assignee", "category", "recurrence_rule", "parent_id", "tags")
_MEETING_FIELDS = ("title", "datetime_start", "datetime_end", "location_or_link",
                   "participants", "agenda", "prep_notes")
_NOTE_FIELDS = ("title", "content", "status", "tags")
_REMINDER_FIELDS = ("title", "note", "remind_at", "recurrence_rule", "task_id")


# ───────────────────────── tasks ─────────────────────────

async def tasks_list(request: web.Request) -> web.Response:
    status = request.query.get("status", "active")
    if status == "all":
        rows = await database.list_tasks(limit=1000, include_subtasks=True)
    elif status == "done":
        rows = await database.list_tasks(status_in=["done"], limit=1000, include_subtasks=True)
    else:  # active
        rows = await database.list_tasks(status_in=["todo", "in_progress", "blocked"],
                                         limit=1000, include_subtasks=True)
    return web.json_response({"tasks": rows})


async def task_create(request: web.Request) -> web.Response:
    data = _pick(await _json_body(request), _TASK_FIELDS)
    if not (data.get("title") or "").strip():
        return web.json_response({"error": "title required"}, status=400)
    await _reject_bad_parent(data.get("parent_id"), None)
    data["source"] = "webapp"  # trusted origin → may introduce assignee/category
    tid = await database.create_task(data)
    return web.json_response({"id": tid, "task": await database.get_task(tid)}, status=201)


async def task_update(request: web.Request) -> web.Response:
    tid = request.match_info["id"]
    data = _pick(await _json_body(request), _TASK_FIELDS)
    await _reject_bad_parent(data.get("parent_id"), tid)
    data["source"] = "webapp"
    ok = await database.update_task(tid, data)
    if not ok:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"task": await database.get_task(tid)})


async def task_complete(request: web.Request) -> web.Response:
    ok = await database.complete_task(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


async def task_delete(request: web.Request) -> web.Response:
    ok = await database.delete_task(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


# ───────────────────────── meetings ─────────────────────────

async def meetings_list(request: web.Request) -> web.Response:
    # Recent + upcoming window so the app shows a useful slice, not the whole history.
    now = database.now_iso()
    start = database.parse_iso_dt(now)
    from datetime import timedelta
    lo = (start - timedelta(days=7)).isoformat()
    hi = (start + timedelta(days=60)).isoformat()
    rows = await database.list_meetings_in_window(lo, hi)
    return web.json_response({"meetings": rows})


async def meeting_create(request: web.Request) -> web.Response:
    data = _pick(await _json_body(request), _MEETING_FIELDS)
    if not (data.get("title") or "").strip():
        return web.json_response({"error": "title required"}, status=400)
    mid = await database.create_meeting(data)
    return web.json_response({"id": mid, "meeting": await database.get_meeting(mid)}, status=201)


async def meeting_update(request: web.Request) -> web.Response:
    mid = request.match_info["id"]
    ok = await database.update_meeting(mid, _pick(await _json_body(request), _MEETING_FIELDS))
    if not ok:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"meeting": await database.get_meeting(mid)})


async def meeting_cancel(request: web.Request) -> web.Response:
    ok = await database.cancel_meeting(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


async def meeting_complete(request: web.Request) -> web.Response:
    ok = await database.complete_meeting(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


# ───────────────────────── notes ─────────────────────────

async def notes_list(request: web.Request) -> web.Response:
    status = request.query.get("status", "inbox")
    rows = await database.list_notes(status=(None if status == "all" else status), limit=500)
    return web.json_response({"notes": rows})


async def note_create(request: web.Request) -> web.Response:
    data = _pick(await _json_body(request), _NOTE_FIELDS)
    if not (data.get("content") or "").strip():
        return web.json_response({"error": "content required"}, status=400)
    data["source"] = "manual"
    nid = await database.create_note(data)
    if not nid:
        return web.json_response({"error": "content required"}, status=400)
    return web.json_response({"id": nid, "note": await database.get_note(nid)}, status=201)


async def note_update(request: web.Request) -> web.Response:
    nid = request.match_info["id"]
    ok = await database.update_note(nid, _pick(await _json_body(request), _NOTE_FIELDS))
    if not ok:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"note": await database.get_note(nid)})


async def note_delete(request: web.Request) -> web.Response:
    ok = await database.delete_note(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


# ───────────────────────── reminders ─────────────────────────

async def reminders_list(request: web.Request) -> web.Response:
    rows = await database.list_reminders(status_in=["scheduled"], limit=500)
    return web.json_response({"reminders": rows})


async def reminder_create(request: web.Request) -> web.Response:
    data = _pick(await _json_body(request), _REMINDER_FIELDS)
    if not (data.get("remind_at") or "").strip():
        return web.json_response({"error": "remind_at required"}, status=400)
    rid = await database.create_reminder(data)
    return web.json_response({"id": rid, "reminder": await database.get_reminder(rid)}, status=201)


async def reminder_update(request: web.Request) -> web.Response:
    rid = request.match_info["id"]
    ok = await database.update_reminder(rid, _pick(await _json_body(request), _REMINDER_FIELDS))
    if not ok:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"reminder": await database.get_reminder(rid)})


async def reminder_complete(request: web.Request) -> web.Response:
    ok = await database.complete_reminder(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


# ───────────────────────── meta + health ─────────────────────────

async def dashboard(request: web.Request) -> web.Response:
    """Home-screen summary: this-week progress, headline counts, today's tasks."""
    active = await database.list_tasks(status_in=["todo", "in_progress", "blocked"], limit=1000)
    today = await database.list_today_tasks()
    week = await database.completed_counts_by_day(7)
    done_week = sum(d["count"] for d in week)
    active_n = len(active)
    total = active_n + done_week
    progress = round(done_week / total * 100) if total else 0
    return web.json_response({
        "progress": progress,
        "counts": {"total": total, "done": done_week, "pending": active_n},
        "today": today,
    })


async def insights(request: web.Request) -> web.Response:
    """Charts: completed-per-day (bar) + active tasks per category (donut)."""
    by_day = await database.completed_counts_by_day(7)
    cats = await database.list_task_categories()
    return web.json_response({
        "by_day": by_day,
        "categories": [{"name": c["category"], "count": c["count"]} for c in cats],
    })


async def meta(request: web.Request) -> web.Response:
    """Dropdown data for forms: categories + contacts."""
    cats = await database.list_task_categories()
    contacts = await database.list_contacts()
    return web.json_response({
        "categories": [c.get("category") for c in cats if c.get("category")],
        "contacts": [c.get("name") for c in contacts if c.get("name")],
        "priorities": ["P0", "P1", "P2", "P3"],
        "statuses": ["todo", "in_progress", "blocked", "done", "cancelled"],
    })


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


# ───────────────────────── app factory / runner ─────────────────────────

def create_app() -> web.Application:
    app = web.Application(middlewares=[error_middleware, auth_middleware])
    app.add_routes([
        web.get("/api/health", health),
        web.get("/api/meta", meta),
        web.get("/api/dashboard", dashboard),
        web.get("/api/insights", insights),
        web.get("/api/tasks", tasks_list),
        web.post("/api/tasks", task_create),
        web.patch("/api/tasks/{id}", task_update),
        web.post("/api/tasks/{id}/complete", task_complete),
        web.delete("/api/tasks/{id}", task_delete),
        web.get("/api/meetings", meetings_list),
        web.post("/api/meetings", meeting_create),
        web.patch("/api/meetings/{id}", meeting_update),
        web.post("/api/meetings/{id}/cancel", meeting_cancel),
        web.post("/api/meetings/{id}/complete", meeting_complete),
        web.get("/api/notes", notes_list),
        web.post("/api/notes", note_create),
        web.patch("/api/notes/{id}", note_update),
        web.delete("/api/notes/{id}", note_delete),
        web.get("/api/reminders", reminders_list),
        web.post("/api/reminders", reminder_create),
        web.patch("/api/reminders/{id}", reminder_update),
        web.post("/api/reminders/{id}/complete", reminder_complete),
    ])
    # Static frontend (served last so /api wins). index.html at "/", with no-store so
    # Telegram never serves a stale cached build after a redeploy.
    if _STATIC_DIR.is_dir():
        async def _index(request):
            return web.FileResponse(_STATIC_DIR / "index.html",
                                    headers={"Cache-Control": "no-store, must-revalidate"})
        app.router.add_get("/", _index)
        app.router.add_static("/", _STATIC_DIR, show_index=False)
    return app


async def start_webapp() -> web.AppRunner:
    """Start the Mini App server on 127.0.0.1:WEBAPP_PORT. Returns the runner so the
    caller can clean it up on shutdown."""
    runner = web.AppRunner(create_app(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, config.WEBAPP_HOST, config.WEBAPP_PORT)
    await site.start()
    logger.info("Mini App server on http://%s:%s (public: %s)",
                config.WEBAPP_HOST, config.WEBAPP_PORT, config.WEBAPP_URL or "unset")
    return runner
