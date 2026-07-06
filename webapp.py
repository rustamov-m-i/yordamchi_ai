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

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode

import aiohttp
from aiohttp import web

import config
import database
import claude_service

try:
    import jwt  # PyJWT — verifies the RS256-signed OIDC id_token from Telegram
except ImportError:  # pragma: no cover
    jwt = None

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "webapp_static"
_MAX_INITDATA_AGE = 24 * 60 * 60  # reject initData older than 24h (replay guard)


# ───────────────────────── auth ─────────────────────────

def validate_init_data(init_data: str, bot_token: str,
                       max_age: int = _MAX_INITDATA_AGE) -> dict | None:
    """Verify a Telegram Mini App initData string. Returns the parsed `user` dict
    on success, else None. Algorithm:
        secret_key   = HMAC_SHA256(key="WebAppData", msg=bot_token)
        expected     = HMAC_SHA256(key=secret_key,  msg=data_check_string)
    where data_check_string is every field EXCEPT `hash`, sorted by key, joined
    "k=v" with newlines. The newer `signature` field IS part of that string (only
    `hash` is excluded) — some implementations wrongly drop it; we try WITH it
    first, then WITHOUT as a fallback for older clients. Also enforces freshness."""
    if not init_data or not bot_token:
        return None
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True, strict_parsing=True))
    except ValueError:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()

    def _match(fields: dict) -> bool:
        dcs = "\n".join(f"{k}={fields[k]}" for k in sorted(fields))
        return hmac.compare_digest(
            hmac.new(secret_key, dcs.encode(), hashlib.sha256).hexdigest(), received_hash)

    ok = _match(pairs)  # canonical: all fields except hash (signature INCLUDED)
    if not ok and "signature" in pairs:
        ok = _match({k: v for k, v in pairs.items() if k != "signature"})
    if not ok:
        logger.info("initData HMAC mismatch — fields=%s", sorted(pairs.keys()))
        return None
    # Freshness: reject stale/replayed initData.
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if max_age and (time.time() - auth_date) > max_age:
        logger.info("initData too old (auth_date=%s)", auth_date)
        return None
    try:
        return json.loads(pairs.get("user", "null"))
    except (ValueError, TypeError):
        return None


def validate_login_widget(data: dict, bot_token: str, max_age: int = _MAX_INITDATA_AGE) -> dict | None:
    """Verify Telegram Login Widget auth data (browser 'Log in with Telegram'). NOTE:
    the secret differs from Mini App initData — here it's SHA256(bot_token) (a plain
    digest), and the data-check-string is all fields except `hash`. Returns the data
    dict on success, else None."""
    if not isinstance(data, dict):
        return None
    received = data.get("hash")
    if not received:
        return None
    pairs = {k: v for k, v in data.items() if k != "hash"}
    dcs = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hashlib.sha256(bot_token.encode()).digest()
    expected = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(received)):
        return None
    try:
        if max_age and time.time() - int(data.get("auth_date", 0)) > max_age:
            return None
    except (ValueError, TypeError):
        return None
    return data


# ── Browser session (issued after a valid Login-Widget auth) ──
SESSION_COOKIE = "ya_session"
_SESSION_TTL = 30 * 24 * 60 * 60  # 30 days


def _session_secret() -> bytes:
    return hashlib.sha256(("ya-session:" + config.TELEGRAM_BOT_TOKEN).encode()).digest()


def make_session(uid: int, ttl: int = _SESSION_TTL) -> str:
    body = f"{uid}.{int(time.time()) + ttl}"
    sig = hmac.new(_session_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"{body}.{sig}"


def check_session(token: str | None) -> int | None:
    """Return the uid from a valid, unexpired session token, else None."""
    if not token:
        return None
    try:
        uid_s, exp_s, sig = token.rsplit(".", 2)
        body = f"{uid_s}.{exp_s}"
    except ValueError:
        return None
    if not hmac.compare_digest(
            hmac.new(_session_secret(), body.encode(), hashlib.sha256).hexdigest(), sig):
        return None
    try:
        if time.time() > int(exp_s):
            return None
        return int(uid_s)
    except ValueError:
        return None


# ── Browser login: Telegram OAuth 2.0 / OpenID Connect (classic widget deprecated) ──
_OAUTH_AUTH_URL = "https://oauth.telegram.org/auth"
_OAUTH_TOKEN_URL = "https://oauth.telegram.org/token"
_OAUTH_JWKS_URL = "https://oauth.telegram.org/.well-known/jwks.json"
_OAUTH_ISSUER = "https://oauth.telegram.org"
_OAUTH_STATE_COOKIE = "ya_oauth"
_OAUTH_STATE_TTL = 600  # 10 min to finish the round-trip


def oauth_redirect_uri() -> str:
    """The callback URL registered in BotFather (Bot Settings > Web Login →
    Redirect URIs). Must match exactly what we send to the auth endpoint."""
    return config.WEBAPP_URL.rstrip("/") + "/api/auth/tg/callback"


def _oauth_pack(data: dict) -> str:
    """Sign a short-lived {state, verifier} blob into a cookie value (no server-side
    store needed) — same HMAC key family as the session cookie."""
    body = base64.urlsafe_b64encode(json.dumps(data).encode()).decode()
    exp = int(time.time()) + _OAUTH_STATE_TTL
    payload = f"{body}.{exp}"
    sig = hmac.new(_session_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _oauth_unpack(token: str | None) -> dict | None:
    if not token:
        return None
    try:
        body, exp_s, sig = token.rsplit(".", 2)
        payload = f"{body}.{exp_s}"
    except ValueError:
        return None
    if not hmac.compare_digest(
            hmac.new(_session_secret(), payload.encode(), hashlib.sha256).hexdigest(), sig):
        return None
    try:
        if time.time() > int(exp_s):
            return None
        return json.loads(base64.urlsafe_b64decode(body.encode()))
    except (ValueError, json.JSONDecodeError):
        return None


def _verify_id_token(id_token: str) -> int | None:
    """Verify the OIDC id_token's RS256 signature against Telegram's JWKS and its
    iss/aud, then return the Telegram user id (blocking — run in an executor)."""
    if not jwt:
        logger.error("PyJWT not installed — cannot verify OIDC id_token")
        return None
    try:
        signing_key = jwt.PyJWKClient(_OAUTH_JWKS_URL).get_signing_key_from_jwt(id_token)
        claims = jwt.decode(
            id_token, signing_key.key,
            algorithms=["RS256", "ES256", "EdDSA", "ES256K"],
            issuer=_OAUTH_ISSUER,
            options={"verify_aud": False},  # aud may be numeric bot id; check below
        )
    except Exception as e:
        logger.warning("OIDC id_token verification failed: %s", e)
        return None
    aud = claims.get("aud")
    cid = str(config.WEBAPP_OAUTH_CLIENT_ID)
    if str(aud) != cid and cid not in (aud if isinstance(aud, list) else [aud]):
        logger.warning("OIDC aud mismatch: token aud=%r, our client_id=%s", aud, cid)
        return None
    uid = claims.get("id", claims.get("sub"))
    try:
        return int(uid)
    except (TypeError, ValueError):
        return None


@web.middleware
async def noindex_middleware(request: web.Request, handler):
    """Tag every response noindex/nofollow so search engines never index the app
    (it holds private data; matters especially while open browser access is on)."""
    try:
        resp = await handler(request)
    except web.HTTPException as e:
        e.headers["X-Robots-Tag"] = "noindex, nofollow"
        raise
    resp.headers["X-Robots-Tag"] = "noindex, nofollow"
    return resp


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
    # Public: health, config, and the login endpoints (widget + OAuth start/callback).
    if not path.startswith("/api/") or path in (
            "/api/health", "/api/auth/telegram", "/api/config",
            "/api/auth/tg/start", "/api/auth/tg/callback"):
        return await handler(request)
    # 1) Telegram Mini App initData (opened INSIDE Telegram).
    header = request.headers.get("Authorization", "")
    init_data = header[4:].strip() if header.lower().startswith("tma ") else ""
    user = validate_init_data(init_data, config.TELEGRAM_BOT_TOKEN)
    uid = int(user["id"]) if (user and user.get("id") is not None) else None
    # 2) Browser session cookie (issued after a Login-Widget sign-in).
    if uid is None:
        uid = check_session(request.cookies.get(SESSION_COOKIE))
    # 3) ⚠️ TEMPORARY: fully-open browser access (env WEBAPP_OPEN_ACCESS=1). Every
    # request is treated as the principal — anyone with the URL sees all data. Only
    # for building the web version; disable before real use. (noindex header is set
    # on every response by noindex_middleware so search engines don't index it.)
    if uid is None and config.WEBAPP_OPEN_ACCESS:
        uid = int(config.PRINCIPAL_USER_ID)
    if uid is None:
        logger.info("webapp AUTH-FAIL %s", path)
        return web.json_response({"error": "unauthorized"}, status=401)
    if uid != int(config.PRINCIPAL_USER_ID):
        logger.info("webapp FORBIDDEN %s uid=%s (principal=%s)", path, uid, config.PRINCIPAL_USER_ID)
        return web.json_response({"error": "forbidden"}, status=403)
    request["uid"] = uid
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
                   "participants", "agenda", "prep_notes", "recurrence_rule")
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
    # Wide enough that the "O'tgan" (past) filter in the mini app has real content;
    # the client splits yaqin/o'tgan/hammasi.
    now = database.now_iso()
    start = database.parse_iso_dt(now)
    from datetime import timedelta
    lo = (start - timedelta(days=90)).isoformat()
    hi = (start + timedelta(days=90)).isoformat()
    rows = await database.list_meetings_in_window(lo, hi)
    return web.json_response({"meetings": rows})


async def meeting_get(request: web.Request) -> web.Response:
    """Single meeting (incl. participants + follow_up_actions/protocol) — used by
    the detail view for meetings that fall outside the list window or need a
    fresh copy after a protocol is generated."""
    m = await database.get_meeting(request.match_info["id"])
    if not m:
        return web.json_response({"error": "not found"}, status=404)
    return web.json_response({"meeting": m})


async def _enroll_participants(participants) -> None:
    """Ishtirokchi = ijrochi: har bir ishtirokchi ismini yagona Odamlar katalogiga
    (contacts) qo'shamiz, shunda ular vazifa ijrochisi dropdown'ida ham chiqadi va
    ikkita alohida ro'yxat hosil bo'lmaydi. save_contact idempotent (nom bo'yicha)."""
    for name in (participants or []):
        nm = str(name).strip()
        if nm:
            await database.save_contact({"name": nm})


async def meeting_create(request: web.Request) -> web.Response:
    data = _pick(await _json_body(request), _MEETING_FIELDS)
    if not (data.get("title") or "").strip():
        return web.json_response({"error": "title required"}, status=400)
    mid = await database.create_meeting(data)
    await _enroll_participants(data.get("participants"))
    return web.json_response({"id": mid, "meeting": await database.get_meeting(mid)}, status=201)


async def meeting_update(request: web.Request) -> web.Response:
    mid = request.match_info["id"]
    data = _pick(await _json_body(request), _MEETING_FIELDS)
    ok = await database.update_meeting(mid, data)
    if not ok:
        return web.json_response({"error": "not found"}, status=404)
    await _enroll_participants(data.get("participants"))
    return web.json_response({"meeting": await database.get_meeting(mid)})


async def meeting_cancel(request: web.Request) -> web.Response:
    ok = await database.cancel_meeting(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


async def meeting_complete(request: web.Request) -> web.Response:
    ok = await database.complete_meeting(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


async def meeting_uncomplete(request: web.Request) -> web.Response:
    ok = await database.uncomplete_meeting(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


_last_proto_ts = [0.0]


async def meeting_protocol(request: web.Request) -> web.Response:
    """Generate + save a meeting protocol (bayonnoma) from raw notes — mirrors the
    bot exactly: same [INTERNAL] directive, same brain, saved to follow_up_actions.
    Optionally creates the follow-up tasks Claude proposes."""
    import time as _t
    import handlers
    mid = request.match_info["id"]
    m = await database.get_meeting(mid)
    if not m:
        return web.json_response({"error": "not found"}, status=404)
    data = await _json_body(request)
    notes = (data.get("notes") or "").strip()
    if not notes:
        return web.json_response({"error": "notes required"}, status=400)
    if len(notes) > 6000:
        return web.json_response({"error": "notes too long"}, status=400)
    now = _t.monotonic()
    if now - _last_proto_ts[0] < 2.0:
        return web.json_response({"error": "Biroz kuting — bir zumdan keyin qayting."}, status=429)
    _last_proto_ts[0] = now
    try:
        directive = handlers._build_protocol_directive(m, notes)
        resp = await claude_service.process_message("", internal_directive=directive)
    except Exception:
        logger.exception("webapp: protocol generation failed")
        return web.json_response({"error": "Bayonnomani tuzib bo'lmadi. Qayta urining."}, status=502)
    protocol_text = (resp.get("user_message") or "").strip()
    if not protocol_text:
        return web.json_response({"error": "Bayonnoma bo'sh chiqdi. Qayta urining."}, status=502)
    await database.update_meeting(mid, {"follow_up_actions": [protocol_text]})
    tasks_created = 0
    tasks_failed = False
    if data.get("create_tasks"):
        creates = [a for a in (resp.get("actions") or []) if a.get("type") == "create_task"]
        if creates:
            try:
                ids = await handlers._execute_actions(creates)
                tasks_created = len(ids.get("task") or [])
            except Exception:
                # Protocol is already saved; only the optional follow-up tasks failed.
                # Tell the client instead of silently reporting 0 created.
                logger.exception("webapp: protocol follow-up tasks failed")
                tasks_failed = True
    return web.json_response({"protocol_text": protocol_text, "tasks_created": tasks_created,
                              "tasks_failed": tasks_failed, "meeting": await database.get_meeting(mid)})


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


async def reminder_delete(request: web.Request) -> web.Response:
    ok = await database.delete_reminder(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


# ───────────────────────── meta + health ─────────────────────────

async def dashboard(request: web.Request) -> web.Response:
    """Executive home: attention radar, today's agenda, priority tasks, delegation
    oversight, weekly pulse. Assembled from existing DB functions in one call."""
    active = await database.list_tasks(status_in=["todo", "in_progress", "blocked"], limit=1000)
    overdue = await database.list_overdue_tasks()
    unassigned = await database.list_unassigned_tasks(limit=200)
    today_tasks = await database.list_today_tasks()
    today_meetings = await database.list_today_meetings()
    week = await database.completed_counts_by_day(7)
    done_week = sum(d["count"] for d in week)
    today_done = week[-1]["count"] if week else 0   # last bucket = today
    total = len(active)
    blocked = sum(1 for t in active if t.get("status") == "blocked")
    progress = round(done_week / (total + done_week) * 100) if (total + done_week) else 0
    priority = [t for t in active if t.get("priority") in ("P0", "P1") and not t.get("parent_id")][:5]
    load = await database.assignee_load_map()
    ranked = sorted(load.values(), key=lambda x: -x.get("active", 0))
    # Team oversight = delegatees only. Skip the unassigned bucket (covered by the
    # "Ijrochisiz" radar tile) and the principal himself.
    overloaded = [p for p in ranked
                  if p.get("active", 0) >= 5
                  and p.get("name") not in ("belgilanmagan", "Men")][:2]
    stale = await database.list_stale_delegations(min_age_days=3, limit=5)
    nxt = await database.next_first_meeting()
    return web.json_response({
        "progress": progress,                                   # compat (Profil + pulse)
        "counts": {"total": total, "done": done_week, "pending": total, "today_done": today_done},
        "radar": {"total": total, "overdue": len(overdue), "blocked": blocked, "unassigned": len(unassigned)},
        "today": {"meetings": today_meetings, "tasks": today_tasks, "next": nxt},
        "priority": priority,
        "team": {"overloaded": overloaded, "stale": stale},
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


async def search(request: web.Request) -> web.Response:
    """Cross-entity search (tasks/meetings/notes/reminders) via database.search_all."""
    q = (request.query.get("q") or "").strip()
    if len(q) < 2:
        return web.json_response({"tasks": [], "meetings": [], "notes": [], "reminders": []})
    res = await database.search_all(q, limit=30)
    return web.json_response({
        "tasks": res.get("tasks", []),
        "meetings": res.get("meetings", []),
        "notes": res.get("notes", []),
        "reminders": res.get("reminders", []),
    })


# Destructive action types that must NOT auto-apply from chat — they need an explicit
# confirm (delete/bulk/cancel). Pending confirmations are cached briefly by token.
_DESTRUCTIVE = {"delete_task", "delete_all_tasks", "delete_tasks_by_category",
                "delete_category", "cancel_meeting", "delete_note", "delete_reminder"}
_PENDING_CHAT: dict = {}          # token -> (actions, monotonic_ts)
_PENDING_TTL = 300
_last_chat_ts = [0.0]


async def _apply_chat_actions(actions):
    """Execute non-show actions. Returns (created, notes, refresh):
      created  — {type: count} of items actually created/updated,
      notes    — user-facing warnings (meeting conflicts, failed actions) the bot
                 also surfaces; without these a conflict/failure looks like success,
      refresh  — whether the client should re-render the current section."""
    import handlers
    try:
        ids = await handlers._execute_actions(actions)
    except Exception:
        logger.exception("webapp chat: _execute_actions failed")
        return {}, ["Amalni bajarishda xatolik yuz berdi."], False
    created = {k: len(v) for k, v in ids.items() if v and not k.startswith("_")}
    notes = []
    for c in ids.get("_conflict") or []:
        notes.append("⚠️ Vaqt to'qnashuvi: " + c)
    if ids.get("_failed"):
        notes.append("⚠️ Bajarilmadi: " + ", ".join(ids["_failed"]))
    refresh = bool(created) or bool(ids.get("_refresh")) or bool(ids.get("_conflict"))
    return created, notes, refresh


async def _chat_view(action: dict) -> dict | None:
    """Resolve a show_* action into structured data for the mini-app chat.

    The bot renders these as Telegram lists (handlers._render_show_action); the web
    chat can't receive Telegram messages, so we return the SAME DB-backed slice as
    JSON for the client to render inline. Without this, "vazifalarni ko'rsat" /
    "bugungi uchrashuvlar" / "statistika" produced a reply with no data attached."""
    from datetime import datetime, timedelta
    atype = action.get("type")
    filt = ((action.get("data") or {}).get("filter") or "").strip().lower()

    if atype == "show_tasks":
        # Top-level only (parent_id IS NULL) — a compact chat preview, same overview
        # scope the bot shows; the user taps "open section" for grouped subtasks.
        if filt == "all":
            items = await database.list_tasks(limit=1000)
        elif filt == "done":
            items = await database.list_tasks(status_in=["done"], limit=200)
        elif filt == "today":
            items = await database.list_today_tasks()
        elif filt == "overdue":
            items = await database.list_overdue_tasks()
        elif filt == "important":
            _act = await database.list_tasks(status_in=["todo", "in_progress", "blocked"], limit=1000)
            items = [t for t in _act if t.get("priority") in ("P0", "P1")]
        else:
            filt = "active"
            items = await database.list_tasks(status_in=["todo", "in_progress", "blocked"], limit=1000)
        return {"kind": "tasks", "filter": filt, "items": items}

    if atype == "show_meetings":
        now = datetime.now(database.TZ)
        day0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if filt == "today":
            items = await database.list_today_meetings()
        elif filt == "tomorrow":
            items = await database.list_meetings_in_window(
                (day0 + timedelta(days=1)).isoformat(), (day0 + timedelta(days=2)).isoformat())
        elif filt == "past":
            items = await database.list_meetings_in_window(
                (day0 - timedelta(days=30)).isoformat(), now.isoformat())
        elif filt == "all":
            items = await database.list_meetings_in_window(
                (day0 - timedelta(days=7)).isoformat(), (day0 + timedelta(days=60)).isoformat())
        else:
            # "week" starts at today_start (not now) so a meeting already under way
            # today still shows — same rule as the bot (_render_meetings_for_filter).
            filt = "week"
            items = await database.list_meetings_in_window(
                day0.isoformat(), (day0 + timedelta(days=7)).isoformat())
        # Exclude completed meetings for every view except the explicit "past" one,
        # matching the bot so a done meeting doesn't linger in "upcoming".
        if filt != "past":
            items = [m for m in items if not m.get("completed_at")]
        return {"kind": "meetings", "filter": filt, "items": items}

    if atype == "show_free_slots":
        import handlers
        now = datetime.now(database.TZ)
        base = handlers._resolve_target_date(action.get("data", {}).get("date"), now) or now.date()
        rng = (action.get("data", {}).get("range") or "day").strip().lower()

        def _slots(free):
            return [{"start": s.strftime("%H:%M"), "end": e.strftime("%H:%M"),
                     "mins": int((e - s).total_seconds() // 60)} for s, e in free]

        days = []
        if rng in ("week", "hafta"):
            monday = base - timedelta(days=base.weekday())
            for i in range(5):  # Mon–Fri
                dd = monday + timedelta(days=i)
                free, _ = await handlers._free_slots_for_day(dd)
                days.append({"date": dd.isoformat(), "slots": _slots(free)})
            rng = "week"
        else:
            free, _ = await handlers._free_slots_for_day(base)
            days.append({"date": base.isoformat(), "slots": _slots(free)})
            rng = "day"
        return {"kind": "slots", "range": rng, "days": days}

    if atype == "show_notes":
        return {"kind": "notes", "items": await database.list_notes(status="inbox", limit=200)}
    if atype == "show_reminders":
        return {"kind": "reminders", "items": await database.list_reminders(status_in=["scheduled"], limit=200)}
    if atype == "show_contacts":
        return {"kind": "contacts", "items": await database.list_contacts()}
    if atype == "show_stats":
        d = action.get("data") or {}
        try:
            days = int(d.get("days", 7))
        except (TypeError, ValueError):
            days = 7
        if days not in (1, 7, 30):
            days = 7
        return {"kind": "stats", "days": days, "stats": await database.executive_stats(days=days)}
    # show_free_slots / run_plan have no simple data slice — the reply text stands alone.
    return None


async def chat(request: web.Request) -> web.Response:
    """AI (natural language) — same brain as the bot. Non-destructive actions
    auto-apply (single-user, like voice auto-confirm); destructive ones (delete/
    bulk/cancel) return confirm_token and are held until POST /api/chat/confirm."""
    import time as _t
    import handlers
    data = await _json_body(request)
    msg = (data.get("message") or "").strip()
    if not msg:
        return web.json_response({"error": "message required"}, status=400)
    if len(msg) > 4000:
        return web.json_response({"error": "message too long"}, status=400)
    # Light rate-limit: 1 request / 1.5s (each call costs a Claude request).
    now = _t.monotonic()
    if now - _last_chat_ts[0] < 1.5:
        return web.json_response({"reply": "Biroz sekinroq — bir zumdan keyin qayting."})
    _last_chat_ts[0] = now
    try:
        # Same brain as the bot. process_message pulls the shared conversation
        # history from the DB itself, so we pass the raw message — a manual prepend
        # would double the history and pollute the saved transcript.
        resp = await claude_service.process_message(msg)
    except Exception:
        logger.exception("webapp chat: process_message failed")
        return web.json_response({"reply": "AI vaqtinchalik javob bera olmadi. Qayta urining."})
    actions = resp.get("actions", []) or []
    # Fall back to the clarification question when the model asks one but leaves
    # user_message empty — otherwise the chat would show a misleading "Bajarildi."
    reply = ((resp.get("user_message") or "").strip()
             or (resp.get("clarification_question") or "").strip()
             or "Bajarildi.")

    # The model is asking a question, not acting — mirror the bot: surface the
    # question and execute NOTHING (a half-understood action must not auto-apply).
    if resp.get("needs_clarification"):
        return web.json_response({"reply": reply, "needs_clarification": True})

    # show_* actions render Telegram lists in the bot; resolve them into structured
    # `views` the web chat can render inline (this is what makes "ko'rsat/ro'yxat/
    # statistika/bo'sh vaqt" actually return DB data in the mini app).
    show_types = handlers._SHOW_ACTION_TYPES
    notes = []
    views = []
    for a in [a for a in actions if a.get("type") in show_types]:
        try:
            v = await _chat_view(a)
            if v:
                views.append(v)
            elif a.get("type") == "run_plan":
                notes.append("📋 To'liq rejalashtirishni bot orqali /plan bilan bajaring.")
        except Exception:
            logger.exception("webapp chat: show-view resolve failed (%s)", a.get("type"))
            notes.append("⚠️ Ba'zi ma'lumotlarni yuklab bo'lmadi.")
    rest = [a for a in actions if a.get("type") not in show_types]

    # export_tasks → a download hint the client fetches (the endpoint streams xlsx).
    download = None
    _exp = next((a for a in rest if a.get("type") == "export_tasks"), None)
    if _exp is not None:
        rest = [a for a in rest if a is not _exp]
        _d = _exp.get("data") or {}
        _filt = handlers._EXPORT_STATUS_WORDS.get((_d.get("status") or "").strip().lower()) or "active"
        _script = "cyr" if (_d.get("script") or "").strip().lower() in ("cyr", "kiril", "krill") else "lat"
        download = {"path": f"/export/tasks?filter={_filt}&script={_script}", "name": "vazifalar.xlsx"}

    destructive = [a for a in rest if a.get("type") in _DESTRUCTIVE]
    if destructive:
        # Purge stale, cache the non-show actions, ask the client to confirm.
        for k in [k for k, (_, ts) in _PENDING_CHAT.items() if now - ts > _PENDING_TTL]:
            _PENDING_CHAT.pop(k, None)
        token = database.new_id("chat-")
        _PENDING_CHAT[token] = (rest, now)
        n = len(destructive)
        if notes:
            reply = reply + "\n\n" + "\n".join(notes)
        return web.json_response({"reply": reply, "views": views, "download": download,
                                  "confirm_token": token,
                                  "confirm_prompt": f"{n} ta o'chirish/bekor amali bajarilsinmi?"})
    created, apply_notes, refresh = (await _apply_chat_actions(rest)) if rest else ({}, [], False)
    notes.extend(apply_notes)
    if notes:
        reply = reply + "\n\n" + "\n".join(notes)
    return web.json_response({"reply": reply, "created": created, "views": views,
                              "download": download, "refresh": refresh})


async def chat_confirm(request: web.Request) -> web.Response:
    """Execute the destructive actions held under a confirm_token from /api/chat."""
    import time as _t
    data = await _json_body(request)
    token = (data.get("token") or "").strip()
    entry = _PENDING_CHAT.pop(token, None)
    if not entry or _t.monotonic() - entry[1] > _PENDING_TTL:
        return web.json_response({"error": "muddati o'tdi"}, status=410)
    created, notes, refresh = await _apply_chat_actions(entry[0])
    reply = "Bajarildi." if not notes else "Bajarildi.\n\n" + "\n".join(notes)
    return web.json_response({"reply": reply, "created": created, "refresh": refresh})


async def protocols_list(request: web.Request) -> web.Response:
    """Meetings that have a saved protocol (bayonnoma)."""
    import handlers
    out = []
    for m in await database.list_meetings_with_protocol(limit=100):
        if handlers._looks_like_protocol(m.get("follow_up_actions")):
            out.append({"id": m["id"], "title": m.get("title"),
                        "datetime_start": m.get("datetime_start")})
    return web.json_response({"protocols": out})


async def protocol_download(request: web.Request) -> web.Response:
    """Render a saved protocol to Word/PDF and stream it back."""
    import handlers
    import protocol_doc
    mid = request.match_info["id"]
    fmt = request.query.get("fmt", "word")
    script = "cyrillic" if request.query.get("script") == "cyr" else "latin"
    m = await database.get_meeting(mid)
    if not m:
        return web.json_response({"error": "not found"}, status=404)
    fu = m.get("follow_up_actions") or []
    text = (fu[0] if (isinstance(fu, list) and fu) else str(fu or "")).strip()
    if not text:
        return web.json_response({"error": "bayonnoma yo'q"}, status=404)
    proto_tasks = handlers._proto_tasks_from_text(text)
    fields = protocol_doc.build_fields(m, text, proto_tasks, await database.get_settings())
    try:
        if fmt == "pdf":
            blob = protocol_doc.build_pdf(fields, script)
            ct, ext = "application/pdf", "pdf"
        else:
            blob = protocol_doc.build_docx(fields, script)
            ct, ext = ("application/vnd.openxmlformats-officedocument.wordprocessingml.document", "docx")
    except RuntimeError as e:  # e.g. reportlab not installed for PDF
        return web.json_response({"error": str(e)}, status=400)
    return web.Response(body=blob, headers={
        "Content-Type": ct, "Content-Disposition": f'attachment; filename="bayonnoma-{mid}.{ext}"'})


async def export_tasks(request: web.Request) -> web.Response:
    """Excel export of tasks — reuses the bot's _send_tasks_export via a capturing
    fake message (no refactor of the 400-line builder)."""
    import handlers
    status = request.query.get("filter", "active")
    script = "cyr" if request.query.get("script") == "cyr" else "lat"
    cap: dict = {}

    class _Capture:
        async def answer_document(self, file, caption=None, parse_mode=None, reply_markup=None):
            cap["data"] = file.data
            cap["name"] = getattr(file, "filename", "vazifalar.xlsx")

        async def answer(self, *a, **k):
            cap["err"] = a[0] if a else ""

    await handlers._send_tasks_export(_Capture(), status=status, script=script)
    if "data" not in cap:
        return web.json_response({"error": cap.get("err", "export failed")}, status=500)
    return web.Response(body=cap["data"], headers={
        "Content-Type": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "Content-Disposition": f'attachment; filename="{cap["name"]}"'})


async def team(request: web.Request) -> web.Response:
    """Per-assignee workload (active/urgent/overdue) merged with the managed
    contacts directory, so people with zero active tasks still show up (and can
    be added/removed). Sorted by active desc, then name."""
    m = await database.assignee_load_map()
    # Fold in directory contacts that have no active task yet (0 load).
    for c in await database.list_contacts():
        nm = (c.get("name") or "").strip()
        if nm and nm not in m and nm not in ("belgilanmagan", "Men"):
            m[nm] = {"name": nm, "active": 0, "urgent": 0, "important": 0,
                     "overdue": 0, "next_deadline": None}
    rows = sorted(m.values(), key=lambda x: (-x.get("active", 0), x.get("name", "")))
    return web.json_response({"team": rows})


async def risks(request: web.Request) -> web.Response:
    counts = await database.risk_score_counts()
    overdue = await database.list_overdue_tasks()
    unassigned = await database.list_unassigned_tasks(limit=50)
    return web.json_response({"counts": counts, "overdue": overdue, "unassigned": unassigned})


async def contacts_create(request: web.Request) -> web.Response:
    name = ((await _json_body(request)).get("name") or "").strip()
    if not name:
        return web.json_response({"error": "name required"}, status=400)
    await database.save_contact({"name": name})
    return web.json_response({"ok": True}, status=201)


async def contacts_delete(request: web.Request) -> web.Response:
    name = (request.query.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "name required"}, status=400)
    ok = await database.delete_contact(name)
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


_CONTENT_FIELDS = ("date", "category", "topic", "format", "platform", "message", "hashtags",
                   "project_id", "status", "assignee", "published_url", "published_at", "reject_reason")
_PROJECT_FIELDS = ("name", "description", "color", "status")


async def content_list(request: web.Request) -> web.Response:
    """SMM postlari — year+month / project / status bo'yicha filtr."""
    q = request.query
    try:
        year = int(q["year"]) if q.get("year") else None
        month = int(q["month"]) if q.get("month") else None
    except ValueError:
        year = month = None
    posts = await database.list_content_posts(
        year, month, project_id=q.get("project") or None, status=q.get("status") or None)
    return web.json_response({"posts": posts})


async def content_create(request: web.Request) -> web.Response:
    data = _pick(await _json_body(request), _CONTENT_FIELDS)
    if not (data.get("date") or "").strip():
        return web.json_response({"error": "date required"}, status=400)
    cid = await database.create_content_post(data)
    return web.json_response({"id": cid}, status=201)


async def content_update(request: web.Request) -> web.Response:
    ok = await database.update_content_post(
        request.match_info["id"], _pick(await _json_body(request), _CONTENT_FIELDS))
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


async def content_delete(request: web.Request) -> web.Response:
    ok = await database.delete_content_post(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


# ───────────────────────── loyihalar (projects) ─────────────────────────

async def projects_list(request: web.Request) -> web.Response:
    return web.json_response({"projects": await database.list_projects()})


async def project_create(request: web.Request) -> web.Response:
    data = _pick(await _json_body(request), _PROJECT_FIELDS)
    if not (data.get("name") or "").strip():
        return web.json_response({"error": "name required"}, status=400)
    return web.json_response({"id": await database.create_project(data)}, status=201)


async def project_update(request: web.Request) -> web.Response:
    ok = await database.update_project(
        request.match_info["id"], _pick(await _json_body(request), _PROJECT_FIELDS))
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


async def project_delete(request: web.Request) -> web.Response:
    ok = await database.delete_project(request.match_info["id"])
    return web.json_response({"ok": ok}, status=(200 if ok else 404))


async def project_dashboard(request: web.Request) -> web.Response:
    return web.json_response(await database.content_dashboard(request.match_info["id"]))


async def categories_list(request: web.Request) -> web.Response:
    return web.json_response({"categories": await database.list_categories()})


async def category_create(request: web.Request) -> web.Response:
    name = (( await _json_body(request)).get("name") or "").strip()
    if not name:
        return web.json_response({"error": "name required"}, status=400)
    await database.create_category(name)
    return web.json_response({"ok": True}, status=201)


async def category_delete(request: web.Request) -> web.Response:
    """Bot bilan bir xil: avval vazifalardagi yorliqni tozalaymiz, keyin metadata
    qatorini o'chiramiz. Aks holda list_categories (birlashgan ko'rinish) kategoriyani
    faol vazifalardan qayta hosil qilib, o'chirish 'ishlamagandek' ko'rinadi."""
    name = (request.query.get("name") or "").strip()
    if not name:
        return web.json_response({"error": "name required"}, status=400)
    cleared = await database.clear_category(name)   # tasks lose the label, not deleted
    await database.delete_category_record(name)
    return web.json_response({"ok": True, "cleared": cleared})


async def calendar(request: web.Request) -> web.Response:
    now = database.parse_iso_dt(database.now_iso())
    try:
        year = int(request.query.get("year") or now.year)
        month = int(request.query.get("month") or now.month)
    except ValueError:
        year, month = now.year, now.month
    rows = await database.list_meetings_in_month(year, month)
    return web.json_response({"year": year, "month": month, "meetings": rows})


async def import_tasks_file(request: web.Request) -> web.Response:
    """Import tasks from an uploaded .xlsx/.csv (raw body). Deterministic parse only
    (no LLM smart path) → executes via the same pipeline as the bot's file import."""
    import io
    import handlers
    name = (request.query.get("name") or "").lower()
    data = await request.read()
    if not data:
        return web.json_response({"error": "fayl bo'sh"}, status=400)
    if len(data) > 6 * 1024 * 1024:
        return web.json_response({"error": "fayl juda katta (max 6MB)"}, status=413)
    try:
        if name.endswith(".csv"):
            import csv
            table = [tuple(r) for r in csv.reader(io.StringIO(data.decode("utf-8-sig", errors="replace")))
                     if any((c or "").strip() for c in r)]
        else:
            from openpyxl import load_workbook
            wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            table = handlers._read_task_sheet(wb)
    except Exception:
        logger.exception("webapp import: parse failed")
        return web.json_response({"error": "faylni o'qib bo'lmadi (.xlsx yoki .csv)"}, status=400)
    if not table:
        return web.json_response({"error": "fayl bo'sh"}, status=400)
    actions = handlers._structured_tasks_from_table(table)
    for a in actions:
        rid = a.pop("_id", "")
        if rid and await database.get_task(rid):
            a["type"] = "update_task"
            a["id"] = rid
    if not actions:
        return web.json_response({"error": "vazifaga o'xshash ustunlar topilmadi"}, status=400)
    for a in actions:
        a.setdefault("data", {})["source"] = "excel"
    n_upd = sum(1 for a in actions if a.get("type") == "update_task")
    await handlers._execute_actions(actions)
    return web.json_response({"created": len(actions) - n_upd, "updated": n_upd})


async def voice(request: web.Request) -> web.Response:
    """Transcribe an uploaded audio clip (raw body) → text (Uzbek). The frontend
    puts the text into the chat input for the user to review/send."""
    import voice_service
    data = await request.read()
    if not data:
        return web.json_response({"error": "audio yo'q"}, status=400)
    if len(data) > getattr(voice_service, "MAX_AUDIO_BYTES", 20 * 1024 * 1024):
        return web.json_response({"error": "audio juda katta"}, status=413)
    try:
        text = await voice_service.transcribe(data, filename=request.query.get("name", "voice.webm"), language="uz")
    except Exception:
        logger.exception("webapp voice: transcribe failed")
        return web.json_response({"error": "ovozni o'girib bo'lmadi"}, status=500)
    if not text:
        return web.json_response({"error": "ovoz tushunilmadi"}, status=422)
    return web.json_response({"text": text})


async def health(request: web.Request) -> web.Response:
    return web.json_response({"ok": True})


async def app_config(request: web.Request) -> web.Response:
    """Public: bot username (fallback link) + whether browser OAuth login is
    configured, so the login screen can show the right button. Nothing sensitive."""
    return web.json_response({
        "bot": getattr(config, "BOT_USERNAME", ""),
        "oauth": bool(config.WEBAPP_OAUTH_CLIENT_SECRET and config.WEBAPP_URL),
    })


async def auth_telegram(request: web.Request) -> web.Response:
    """Browser 'Log in with Telegram': validate the widget payload, ensure it's the
    principal, then set a signed HttpOnly session cookie."""
    data = await _json_body(request)
    u = validate_login_widget(data, config.TELEGRAM_BOT_TOKEN)
    if not u:
        return web.json_response({"error": "unauthorized"}, status=401)
    if int(u.get("id", 0)) != int(config.PRINCIPAL_USER_ID):
        return web.json_response({"error": "forbidden"}, status=403)
    resp = web.json_response({"ok": True, "name": u.get("first_name")})
    resp.set_cookie(SESSION_COOKIE, make_session(int(u["id"])), max_age=_SESSION_TTL,
                    httponly=True, secure=True, samesite="Strict", path="/")
    return resp


async def auth_tg_start(request: web.Request) -> web.Response:
    """Begin browser login via Telegram OAuth 2.0 / OpenID Connect. Generates PKCE +
    state, stashes them in a short-lived signed cookie, and redirects the user to
    Telegram's consent page. (Classic Login Widget is deprecated.)"""
    if not config.WEBAPP_OAUTH_CLIENT_SECRET or not config.WEBAPP_URL:
        return web.Response(
            text="Login sozlanmagan: WEBAPP_OAUTH_CLIENT_SECRET va WEBAPP_URL kerak.",
            status=503)
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    state = secrets.token_urlsafe(24)
    params = {
        "client_id": config.WEBAPP_OAUTH_CLIENT_ID,
        "redirect_uri": oauth_redirect_uri(),
        "response_type": "code",
        "scope": "openid",
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    resp = web.HTTPFound(_OAUTH_AUTH_URL + "?" + urlencode(params))
    # SameSite=Lax so the cookie survives the top-level redirect back from Telegram.
    resp.set_cookie(_OAUTH_STATE_COOKIE, _oauth_pack({"s": state, "v": verifier}),
                    max_age=_OAUTH_STATE_TTL, httponly=True, secure=True,
                    samesite="Lax", path="/")
    return resp


async def auth_tg_callback(request: web.Request) -> web.Response:
    """OAuth redirect target: verify state, exchange the code for an id_token, confirm
    the Telegram user is the principal, then set the session cookie."""
    def _fail(reason: str):
        r = web.HTTPFound("/?login_error=" + reason)
        r.del_cookie(_OAUTH_STATE_COOKIE, path="/")
        return r

    if request.query.get("error"):
        return _fail(request.query.get("error", "denied"))
    code = request.query.get("code")
    state = request.query.get("state")
    stash = _oauth_unpack(request.cookies.get(_OAUTH_STATE_COOKIE))
    if not code or not stash or not hmac.compare_digest(stash.get("s", ""), state or ""):
        return _fail("state")   # expired / CSRF
    _sec = config.WEBAPP_OAUTH_CLIENT_SECRET
    logger.warning("OAuth token exchange: client_id=%r secret_len=%d secret_sha8=%s redirect_uri=%s",
                   config.WEBAPP_OAUTH_CLIENT_ID, len(_sec),
                   hashlib.sha256(_sec.encode()).hexdigest()[:8] if _sec else "-",
                   oauth_redirect_uri())
    # client_secret_post (discovery'da e'lon qilingan) — Basic-header kodlash
    # nozikliklarini chetlab o'tadi; secret to'g'ridan-to'g'ri form body'da ketadi.
    try:
        async with aiohttp.ClientSession() as sess:
            async with sess.post(
                _OAUTH_TOKEN_URL,
                data={
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": oauth_redirect_uri(),
                    "client_id": config.WEBAPP_OAUTH_CLIENT_ID,
                    "client_secret": _sec,
                    "code_verifier": stash["v"],
                },
                timeout=aiohttp.ClientTimeout(total=15),
            ) as r:
                body = await r.text()
                if r.status != 200:
                    logger.warning("OAuth token exchange failed: HTTP %s body=%s", r.status, body[:500])
                    return _fail("token")
                tok = json.loads(body)
    except Exception:
        logger.exception("OAuth token exchange error")
        return _fail("token")
    id_token = tok.get("id_token")
    if not id_token:
        logger.warning("OAuth: token response has no id_token: %s", tok)
        return _fail("token")
    # JWKS fetch + JWT verify are blocking (urllib) — keep the event loop free.
    uid = await asyncio.get_event_loop().run_in_executor(None, _verify_id_token, id_token)
    if uid is None:
        return _fail("token")
    if uid != int(config.PRINCIPAL_USER_ID):
        logger.warning("OAuth: non-principal login blocked (uid=%s, principal=%s)",
                       uid, config.PRINCIPAL_USER_ID)
        return _fail("forbidden")
    resp = web.HTTPFound("/")
    resp.set_cookie(SESSION_COOKIE, make_session(uid), max_age=_SESSION_TTL,
                    httponly=True, secure=True, samesite="Strict", path="/")
    resp.del_cookie(_OAUTH_STATE_COOKIE, path="/")
    return resp


async def auth_logout(request: web.Request) -> web.Response:
    resp = web.json_response({"ok": True})
    resp.del_cookie(SESSION_COOKIE, path="/")
    return resp


async def me(request: web.Request) -> web.Response:
    """Reached only when authed (via initData or session) — the browser uses a 401
    here as the 'show login page' signal."""
    return web.json_response({"ok": True, "uid": request.get("uid")})


# ───────────────────────── app factory / runner ─────────────────────────

async def robots(request: web.Request) -> web.Response:
    """Disallow all crawlers — the app is private (belt-and-braces with the
    X-Robots-Tag header)."""
    return web.Response(text="User-agent: *\nDisallow: /\n", content_type="text/plain")


def create_app() -> web.Application:
    if config.WEBAPP_OPEN_ACCESS:
        logger.warning("⚠️ WEBAPP_OPEN_ACCESS is ON — browser access is UNAUTHENTICATED "
                       "(anyone with the URL sees all data). Disable before real use.")
    app = web.Application(middlewares=[noindex_middleware, error_middleware, auth_middleware])
    app.add_routes([
        web.get("/api/health", health),
        web.get("/api/config", app_config),
        web.post("/api/auth/telegram", auth_telegram),
        web.get("/api/auth/tg/start", auth_tg_start),
        web.get("/api/auth/tg/callback", auth_tg_callback),
        web.post("/api/auth/logout", auth_logout),
        web.get("/api/me", me),
        web.get("/api/meta", meta),
        web.get("/api/dashboard", dashboard),
        web.get("/api/insights", insights),
        web.get("/api/search", search),
        web.post("/api/chat", chat),
        web.post("/api/chat/confirm", chat_confirm),
        web.get("/api/protocols", protocols_list),
        web.get("/api/protocols/{id}/download", protocol_download),
        web.get("/api/export/tasks", export_tasks),
        web.get("/api/team", team),
        web.post("/api/contacts", contacts_create),
        web.delete("/api/contacts", contacts_delete),
        web.get("/api/content", content_list),
        web.post("/api/content", content_create),
        web.patch("/api/content/{id}", content_update),
        web.delete("/api/content/{id}", content_delete),
        web.get("/api/projects", projects_list),
        web.post("/api/projects", project_create),
        web.patch("/api/projects/{id}", project_update),
        web.delete("/api/projects/{id}", project_delete),
        web.get("/api/projects/{id}/dashboard", project_dashboard),
        web.get("/api/risks", risks),
        web.get("/api/categories", categories_list),
        web.post("/api/categories", category_create),
        web.delete("/api/categories", category_delete),
        web.get("/api/calendar", calendar),
        web.post("/api/import/tasks", import_tasks_file),
        web.post("/api/voice", voice),
        web.get("/api/tasks", tasks_list),
        web.post("/api/tasks", task_create),
        web.patch("/api/tasks/{id}", task_update),
        web.post("/api/tasks/{id}/complete", task_complete),
        web.delete("/api/tasks/{id}", task_delete),
        web.get("/api/meetings", meetings_list),
        web.post("/api/meetings", meeting_create),
        web.get("/api/meetings/{id}", meeting_get),
        web.patch("/api/meetings/{id}", meeting_update),
        web.post("/api/meetings/{id}/cancel", meeting_cancel),
        web.post("/api/meetings/{id}/complete", meeting_complete),
        web.post("/api/meetings/{id}/uncomplete", meeting_uncomplete),
        web.post("/api/meetings/{id}/protocol", meeting_protocol),
        web.get("/api/notes", notes_list),
        web.post("/api/notes", note_create),
        web.patch("/api/notes/{id}", note_update),
        web.delete("/api/notes/{id}", note_delete),
        web.get("/api/reminders", reminders_list),
        web.post("/api/reminders", reminder_create),
        web.patch("/api/reminders/{id}", reminder_update),
        web.post("/api/reminders/{id}/complete", reminder_complete),
        web.delete("/api/reminders/{id}", reminder_delete),
    ])
    app.router.add_get("/robots.txt", robots)
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
