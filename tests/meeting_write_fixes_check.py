"""Bot uchrashuv-yaratish tuzatishlari (write-side) regression testi.

Ikki tasdiqlangan xatoni qamrab oladi:
  #1  Tasdiq/to'g'ridan-to'g'ri yo'l to'qnashuv (_conflict) va yaroqsiz-vaqt
      (_badtime) ogohlantirishlarini ko'rsatishi — "qo'shildi" deb yolg'on
      aytmasligi.
  #2/#3  create_meeting datetime_start'ni kanonik Asia/Tashkent ISO'ga
      normallashtiradi (faqat-sana / naive qiymat kalendardan yo'qolmaydi), va
      _execute_actions yaroqsiz vaqtli uchrashuvni YARATMAYDI.

Vaqtinchalik DB'da, haqiqiy DB'ga tegmaydi. Ishga tushirish:
    PYTHONPATH=. venv/bin/python tests/meeting_write_fixes_check.py
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
import tempfile

import config

_SCRATCH = "/private/tmp/claude-501/-Users-maqsudrustamov-Documents-Yordamchi-oxirgi/75cf7db8-bb71-4bce-83d1-e0d24afca88b/scratchpad"
_tmp = tempfile.mktemp(suffix=".db", dir=_SCRATCH if os.path.isdir(_SCRATCH) else None)
config.DATABASE_PATH = _tmp
# iCloud push hech qanday holatda haqiqiy kalendarga yozmasligi uchun o'chiramiz.
config.ICLOUD_ENABLED = False

import database  # noqa: E402
import handlers  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  ✅ {name}")
    else:
        _fail += 1
        print(f"  ❌ {name}")


def _sched_meeting(title, start, end=None, participants=None):
    d = {"title": title, "datetime_start": start}
    if end:
        d["datetime_end"] = end
    if participants:
        d["participants"] = participants
    return {"type": "schedule_meeting", "data": d}


async def main():
    await database.init()

    # ── #2/#3: create_meeting normallashtirish + topilishi ──
    print("=== #2/#3: datetime_start normallashtirish ===")
    # faqat-sana → +05:00 li kanonik ISO, va oy ko'rinishida topiladi
    mid = await database.create_meeting({"title": "Faqat sana", "datetime_start": "2026-08-15"})
    row = sqlite3.connect(_tmp).execute(
        "SELECT datetime_start, datetime_end FROM meetings WHERE id=?", (mid,)).fetchone()
    check("faqat-sana '2026-08-15' → '2026-08-15T00:00:00+05:00' saqlandi",
          row[0] == "2026-08-15T00:00:00+05:00")
    check("faqat-sana: end = start+60min materializatsiya qilindi",
          row[1] == "2026-08-15T01:00:00+05:00")
    aug = await database.list_meetings_in_month(2026, 8)
    check("faqat-sana uchrashuv oy ko'rinishida TOPILADI (ilgari yo'qolardi)",
          any(m["id"] == mid for m in aug))

    # naive (ofsetsiz) qiymat ham normallashadi
    mid2 = await database.create_meeting({"title": "Naive", "datetime_start": "2026-08-20T14:00:00"})
    row2 = sqlite3.connect(_tmp).execute(
        "SELECT datetime_start FROM meetings WHERE id=?", (mid2,)).fetchone()
    check("naive '2026-08-20T14:00:00' → '+05:00' li ISO",
          row2[0] == "2026-08-20T14:00:00+05:00")
    check("naive uchrashuv oy ko'rinishida topiladi",
          any(m["id"] == mid2 for m in await database.list_meetings_in_month(2026, 8)))

    # Z-suffiksli UTC ISO (Python 3.9 fromisoformat 'Z'ni tushunmaydi) — parse bo'lishi
    # va yaroqli uchrashuv RAD ETILMASLIGI kerak (10:00Z UTC → 15:00 +05:00).
    check("Z-suffiks '2026-08-15T10:00:00Z' parse bo'ladi (None emas)",
          database.parse_iso_dt("2026-08-15T10:00:00Z") is not None)
    midz = await database.create_meeting({"title": "UTC-Z", "datetime_start": "2026-08-15T10:00:00Z"})
    rowz = sqlite3.connect(_tmp).execute("SELECT datetime_start FROM meetings WHERE id=?", (midz,)).fetchone()
    check("Z-suffiks uchrashuv +05:00 ga o'giriladi (10:00Z → 15:00)", rowz[0] == "2026-08-15T15:00:00+05:00")
    check("Z-suffiksli uchrashuv oy ko'rinishida topiladi",
          any(m["id"] == midz for m in await database.list_meetings_in_month(2026, 8)))

    # ── #2: _execute_actions yaroqsiz vaqtni yaratmaydi + _badtime ──
    print("=== #2: yaroqsiz datetime_start → yaratilmaydi, ogohlantiriladi ===")
    before = len(await database.list_meetings_in_month(2026, 9))
    ids = await handlers._execute_actions([_sched_meeting("Yaroqsiz", "ertaga 10:00")])
    after = len(await database.list_meetings_in_month(2026, 9))
    check("yaroqsiz 'ertaga 10:00' → uchrashuv YARATILMADI (meeting bucket bo'sh)",
          not ids.get("meeting"))
    check("yaroqsiz vaqt → _badtime bucket to'ldi", ids.get("_badtime") == ["Yaroqsiz"])
    check("_badtime_note ochiq ogohlantirish qaytaradi",
          "vaqt aniqlanmadi" in handlers._badtime_note(ids).lower())
    check("bo'sh datetime_start ('') ham rad etiladi",
          handlers._execute_actions and (await handlers._execute_actions(
              [_sched_meeting("Bo'sh", "")])).get("_badtime") == ["Bo'sh"])

    # ── #1: to'qnashuv → yaratilmaydi + _conflict_note ko'rsatiladi ──
    print("=== #1: to'qnashuv → jim tashlanmaydi ===")
    ok = await handlers._execute_actions([_sched_meeting("Asl", "2026-09-10T15:00:00+05:00")])
    check("yaroqli uchrashuv yaratildi (nazorat)", len(ok.get("meeting") or []) == 1)
    clash = await handlers._execute_actions([_sched_meeting("Ustma-ust", "2026-09-10T15:30:00+05:00")])
    check("kesishuvchi uchrashuv YARATILMADI (meeting bucket bo'sh)",
          not clash.get("meeting"))
    check("to'qnashuv → _conflict bucket to'ldi", bool(clash.get("_conflict")))
    check("_conflict_note ochiq ogohlantirish qaytaradi",
          "vaqt band" in handlers._conflict_note(clash).lower())
    # DB'da faqat 1 ta 2026-09-10 uchrashuvi bo'lishi kerak (dublikat yo'q)
    sep = [m for m in await database.list_meetings_in_month(2026, 9)
           if str(m["datetime_start"]).startswith("2026-09-10")]
    check("DB'da 2026-09-10 uchun faqat 1 ta uchrashuv (to'qnashuv qo'shilmadi)", len(sep) == 1)

    # ── note funksiyalari bo'sh bucket'da hech narsa qaytarmaydi (regressiya) ──
    empty = {"_conflict": [], "_badtime": [], "_failed": []}
    check("_conflict_note bo'sh → ''", handlers._conflict_note(empty) == "")
    check("_badtime_note bo'sh → ''", handlers._badtime_note(empty) == "")


try:
    asyncio.run(main())
finally:
    for f in list(__import__("glob").glob(_tmp + "*")):
        try:
            os.remove(f)
        except OSError:
            pass

print("\n" + "=" * 48)
print(f"NATIJA:  ✅ {_pass} o'tdi   ❌ {_fail} yiqildi")
print("=" * 48)
sys.exit(1 if _fail else 0)
