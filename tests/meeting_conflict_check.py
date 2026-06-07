"""Uchrashuv to'qnashuvini oldini olish — tekshiruvlar.

Bir xil vaqtga ustma-ust uchrashuv qo'yilmasligini tasdiqlaydi:
  - sof oraliq-kesishish helperlari (_intervals_overlap, _meeting_interval)
  - database.find_meeting_conflicts (ustma-ust / ketma-ket / yakunlangan)
  - handlers._execute_actions schedule_meeting'ni to'qnashuvda o'tkazib yuboradi
  - _conflict_note foydalanuvchiga ogohlantirish beradi

Run:  venv/bin/python tests/meeting_conflict_check.py
"""
import asyncio
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.DATABASE_PATH = "/tmp/yordamchi_meeting_conflict_test.db"
if os.path.exists(config.DATABASE_PATH):
    os.remove(config.DATABASE_PATH)

import database     # noqa: E402
import handlers     # noqa: E402

PASS = 0
FAIL = 0
FAILED = []


def check(name, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  ✅ {name}")
    else:
        FAIL += 1
        FAILED.append(name)
        print(f"  ❌ {name}   {detail}")


def _iso(base, hour, minute=0):
    return database.TZ.localize(
        datetime.combine(base, datetime.min.time()).replace(hour=hour, minute=minute)
    ).isoformat()


async def main():
    await database.init()
    # Kelajakdagi sobit kun (o'tib ketmasligi uchun)
    day = (datetime.now(database.TZ) + timedelta(days=3)).date()

    print("[ A. sof helperlar ]")
    s = database.parse_iso_dt(_iso(day, 10))
    e = database.parse_iso_dt(_iso(day, 11))
    check("ustma-ust oraliq → True",
          database._intervals_overlap(s, e, s + timedelta(minutes=30), e + timedelta(minutes=30)))
    check("ketma-ket (chetma-chet) → False",
          not database._intervals_overlap(s, e, e, e + timedelta(hours=1)))
    check("ajralgan oraliq → False",
          not database._intervals_overlap(s, e, e + timedelta(hours=2), e + timedelta(hours=3)))
    iv = database._meeting_interval(_iso(day, 9), None)
    check("datetime_end yo'q → taxminiy davomiylik qo'shiladi",
          iv is not None and (iv[1] - iv[0]) == timedelta(minutes=database.DEFAULT_MEETING_MINUTES))
    check("noto'g'ri start → None", database._meeting_interval("buzuq", None) is None)

    print("\n[ B. find_meeting_conflicts ]")
    mid = await database.create_meeting({
        "title": "Birinchi", "datetime_start": _iso(day, 10), "datetime_end": _iso(day, 11),
    })
    overlap = await database.find_meeting_conflicts(_iso(day, 10, 30), _iso(day, 11, 30))
    check("kesishadigan vaqt → to'qnashuv topiladi", len(overlap) == 1 and overlap[0]["id"] == mid)
    none_clash = await database.find_meeting_conflicts(_iso(day, 11), _iso(day, 12))
    check("ketma-ket vaqt → to'qnashuv yo'q", none_clash == [])
    far = await database.find_meeting_conflicts(_iso(day, 15), _iso(day, 16))
    check("uzoq vaqt → to'qnashuv yo'q", far == [])
    excl = await database.find_meeting_conflicts(_iso(day, 10), _iso(day, 11), exclude_id=mid)
    check("exclude_id → o'zini hisobga olmaydi", excl == [])
    # Yakunlangan uchrashuv to'qnashuv hisoblanmaydi
    await database.complete_meeting(mid)
    done = await database.find_meeting_conflicts(_iso(day, 10, 30), _iso(day, 11, 30))
    check("yakunlangan uchrashuv → to'qnashuv yo'q", done == [])
    await database.uncomplete_meeting(mid)

    print("\n[ C. _execute_actions schedule_meeting'ni bloklaydi ]")
    actions = [{
        "type": "schedule_meeting",
        "data": {"title": "Ikkinchi", "datetime_start": _iso(day, 10, 30),
                 "datetime_end": _iso(day, 11, 30)},
    }]
    before = await database.find_meeting_conflicts(_iso(day, 10, 30), _iso(day, 11, 30))
    ids = await handlers._execute_actions(actions)
    check("to'qnashuvda meeting yaratilmadi", ids["meeting"] == [])
    check("_conflict to'ldirildi", len(ids["_conflict"]) == 1)
    check("DB'da yangi uchrashuv qo'shilmadi",
          len(await database.find_meeting_conflicts(_iso(day, 10, 30), _iso(day, 11, 30))) == len(before))
    note = handlers._conflict_note(ids)
    check("_conflict_note ogohlantirish beradi", "to'qnashadi" in note and "qo'yilmadi" in note)

    print("\n[ D. bo'sh vaqtga uchrashuv qo'yiladi ]")
    actions_ok = [{
        "type": "schedule_meeting",
        "data": {"title": "Uchinchi", "datetime_start": _iso(day, 14),
                 "datetime_end": _iso(day, 15)},
    }]
    ids_ok = await handlers._execute_actions(actions_ok)
    check("bo'sh vaqt → meeting yaratildi", len(ids_ok["meeting"]) == 1)
    check("bo'sh vaqt → _conflict bo'sh", ids_ok["_conflict"] == [])

    print("\n" + "=" * 56)
    print(f"RESULT: {PASS} passed, {FAIL} failed")
    if FAILED:
        print("FAILED: " + ", ".join(FAILED))
    print("=" * 56)
    sys.exit(1 if FAIL else 0)


if __name__ == "__main__":
    asyncio.run(main())
