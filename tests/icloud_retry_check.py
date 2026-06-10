"""iCloud push failure → retry-queue enqueue (no silent calendar-sync gaps).
The retry sweep already existed; this verifies the push path now FEEDS it."""

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
config.DATABASE_PATH = "/tmp/yd_icloud_retry_test.db"
try:
    os.remove(config.DATABASE_PATH)
except OSError:
    pass

import aiosqlite  # noqa: E402
import database  # noqa: E402
import handlers  # noqa: E402
import calendar_service  # noqa: E402

_P = _F = 0
_FAILED: list = []


def check(n, c, d=""):
    global _P, _F
    if c:
        _P += 1
        print(f"  ✅ {n}")
    else:
        _F += 1
        _FAILED.append(n)
        print(f"  ❌ {n}   {d}")


async def _queue_count() -> int:
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        cur = await db.execute("SELECT COUNT(*) FROM icloud_retry_queue")
        return (await cur.fetchone())[0]


async def main():
    await database.init()
    _orig = calendar_service.push_meeting
    base = {"datetime_start": "2026-06-12T10:00:00+05:00",
            "datetime_end": "2026-06-12T11:00:00+05:00", "title": "Retry"}
    try:
        # 1) push returns no UID → retry enqueued
        calendar_service.push_meeting = lambda *a, **k: None
        b = await _queue_count()
        await handlers._push_meeting_to_icloud("m-r1", base)
        check("push None → retry navbatiga qo'shildi", await _queue_count() == b + 1)

        # 2) push raises → retry enqueued (even when start computed already)
        def _boom(*a, **k):
            raise RuntimeError("network down")
        calendar_service.push_meeting = _boom
        b2 = await _queue_count()
        await handlers._push_meeting_to_icloud("m-r2", {"datetime_start": "2026-06-12T10:00:00+05:00", "title": "X"})
        check("push exception → retry navbatiga qo'shildi", await _queue_count() == b2 + 1)

        # 3) success → NO retry + icloud_uid persisted
        calendar_service.push_meeting = lambda *a, **k: "UID-OK-123"
        mid = await database.create_meeting({"title": "OK", "datetime_start": "2026-06-12T12:00:00+05:00"})
        b3 = await _queue_count()
        await handlers._push_meeting_to_icloud(mid, {"datetime_start": "2026-06-12T12:00:00+05:00", "title": "OK"})
        check("push muvaffaqiyatli → retry QO'SHILMAYDI", await _queue_count() == b3)
        m = await database.get_meeting(mid)
        check("push muvaffaqiyatli → icloud_uid saqlanadi", bool(m) and m.get("icloud_uid") == "UID-OK-123")

        # 4) enqueued payload is sweep-readable (dt_start/dt_end keys)
        async with aiosqlite.connect(config.DATABASE_PATH) as db:
            db.row_factory = aiosqlite.Row
            row = await (await db.execute(
                "SELECT payload FROM icloud_retry_queue WHERE meeting_id='m-r1'")).fetchone()
        import json as _json
        pl = _json.loads(row["payload"]) if row else {}
        check("retry payload sweep o'qiy oladi (dt_start/dt_end)",
              "dt_start" in pl and "dt_end" in pl, str(pl)[:80])
    finally:
        calendar_service.push_meeting = _orig

    # 5) dedup (3b): adopt uid onto a matching NULL-uid meeting (no duplicate import)
    _t = "2026-06-13T09:00:00+05:00"
    _exist = await database.create_meeting({"title": "Sync M", "datetime_start": _t})
    check("dedup: NULL-uid + bir xil kontent → uid asrab olinadi",
          calendar_service._adopt_uid_for_content("UID-ADOPT-1", "Sync M", _t) is True)
    _em = await database.get_meeting(_exist)
    check("dedup: mavjud meeting uid oldi (dublikat yo'q)",
          bool(_em) and _em.get("icloud_uid") == "UID-ADOPT-1")
    check("dedup: kontent mos kelmasa → False (import bo'ladi)",
          calendar_service._adopt_uid_for_content("UID-X", "Boshqa nom", _t) is False)

    # 6) import conflict (3b): _save_imported_meeting returns a description on overlap
    await database.create_meeting({"title": "Band", "datetime_start": "2026-06-14T10:00:00+05:00",
                                   "datetime_end": "2026-06-14T11:00:00+05:00"})
    _conf = await calendar_service._save_imported_meeting(
        "UID-IMP-1", "Tashqi", "2026-06-14T10:30:00+05:00", "2026-06-14T11:30:00+05:00", None, None)
    check("import konflikt → tavsif qaytadi", isinstance(_conf, str) and "to'qnashadi" in _conf, str(_conf))
    _noconf = await calendar_service._save_imported_meeting(
        "UID-IMP-2", "Bo'sh", "2026-06-20T15:00:00+05:00", "2026-06-20T16:00:00+05:00", None, None)
    check("import bo'sh slot → None", _noconf is None, str(_noconf))

    import inspect as _insp_i
    import scheduler as _sched
    _sync_src = _insp_i.getsource(_sched.YordamchiScheduler._icloud_sync)
    check("_icloud_sync tuple ochadi + konfliktда xabar beradi",
          "imported, conflicts" in _sync_src and "to'qnashuv" in _sync_src)

    # 7) bot.py drains in-flight background tasks on shutdown (source-level)
    bsrc = (ROOT / "bot.py").read_text(encoding="utf-8")
    check("bot.py shutdown background ishlarni drain qiladi (grace)",
          "_background_tasks" in bsrc and "wait_for" in bsrc)

    print("\n" + "=" * 50)
    print(f"RESULT: {_P} passed, {_F} failed")
    if _FAILED:
        print("FAILED: " + ", ".join(_FAILED))
    print("=" * 50)
    sys.exit(1 if _F else 0)


if __name__ == "__main__":
    asyncio.run(main())
