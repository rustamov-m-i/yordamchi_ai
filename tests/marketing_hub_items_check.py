"""Marketing Hub — Bosqich 3a: project_items CRUD + loyiha turi/shablon/normalize.

Vaqtinchalik DB'da (ADDITIVE — content_* yo'liga tegmaydi). Ishga tushirish:
    PYTHONPATH=. venv/bin/python tests/marketing_hub_items_check.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import sys
import tempfile

import config

_SCRATCH = "/private/tmp/claude-501/-Users-maqsudrustamov-Documents-Yordamchi-oxirgi/75cf7db8-bb71-4bce-83d1-e0d24afca88b/scratchpad"
_tmp = tempfile.mktemp(suffix=".db", dir=_SCRATCH if os.path.isdir(_SCRATCH) else None)
config.DATABASE_PATH = _tmp

import database  # noqa: E402

_pass = _fail = 0


def check(name, cond):
    global _pass, _fail
    if cond:
        _pass += 1
        print(f"  ✅ {name}")
    else:
        _fail += 1
        print(f"  ❌ {name}")


async def main():
    await database.init()

    # ── Loyiha turi / shablon / normalize ──
    p_camp = await database.create_project({"name": "Kampaniya", "type": "campaign"})
    pc = await database.get_project(p_camp)
    wf = json.loads(pc["workflow"])
    check("create_project(type=campaign): type/icon/default_view saqlandi",
          pc["type"] == "campaign" and pc["default_view"] == "kanban" and pc["icon"] == "speakerphone")
    check("create_project(campaign): workflow = campaign statuslari (brif...)",
          wf["statuses"][0]["key"] == "brif" and len(wf["statuses"]) == 7)

    p_tpl = await database.create_project({"name": "SMM", "template_id": "smm_calendar"})
    pt = await database.get_project(p_tpl)
    check("create_project(template=smm_calendar): type=smm, default_view=calendar",
          pt["type"] == "smm" and pt["default_view"] == "calendar")

    p_def = await database.create_project({"name": "Turi berilmagan"})
    pd = await database.get_project(p_def)
    check("create_project(tursiz): 'smm' legacy standart + workflow to'ldirildi",
          pd["type"] == "smm" and bool(pd["workflow"]))

    # legacy row (type NULL) → get_project normalize
    c = sqlite3.connect(_tmp)
    c.execute("INSERT INTO projects (id,name,status,default_view,created_at,updated_at) "
              "VALUES ('pr-legacy','Eski',?,?,?,?)", ("active", "calendar", "2026-01-01", "2026-01-01"))
    c.commit()
    c.close()
    pl = await database.get_project("pr-legacy")
    check("legacy (type NULL) → normalize: type='smm' + workflow",
          pl["type"] == "smm" and bool(pl["workflow"]))
    check("list_projects: har loyiha type/workflow bilan (normalize)",
          all(pr.get("type") and pr.get("workflow") for pr in await database.list_projects()))

    # ── Item CRUD ──
    i1 = await database.create_project_item(p_camp, {
        "type": "media_placement", "title": "TV rolik", "status": "brif",
        "category": "bank", "primary_date": "2026-07-05", "assignee": "D. Karimova",
        "fields": {"channel": "TV", "budget": 5000}})
    it = await database.get_project_item(i1)
    check("create/get_project_item: fields JSON parse qilinadi (dict)",
          isinstance(it["fields"], dict) and it["fields"]["channel"] == "TV" and it["fields"]["budget"] == 5000)
    check("create_project_item: type/title/status/category/primary_date saqlandi",
          it["type"] == "media_placement" and it["title"] == "TV rolik"
          and it["status"] == "brif" and it["primary_date"] == "2026-07-05")

    inv = await database.get_project_item(await database.create_project_item(p_camp, {"type": "yoq_bunday", "title": "X"}))
    check("create_project_item: noto'g'ri type → 'task'", inv["type"] == "task")

    # order_index avto-oshadi (bir xil project+status)
    a = await database.create_project_item(p_camp, {"title": "A", "status": "rejalashtirish"})
    b = await database.create_project_item(p_camp, {"title": "B", "status": "rejalashtirish"})
    cc = await database.create_project_item(p_camp, {"title": "C", "status": "rejalashtirish"})
    oa = (await database.get_project_item(a))["order_index"]
    ob = (await database.get_project_item(b))["order_index"]
    oc = (await database.get_project_item(cc))["order_index"]
    check("order_index avto: A/B/C = 0/1/2", (oa, ob, oc) == (0, 1, 2))

    # ── filtrlar ──
    check("list_project_items(type=media_placement): 1 ta", len(await database.list_project_items(p_camp, type_="media_placement")) == 1)
    check("list_project_items(status=rejalashtirish): 3 ta", len(await database.list_project_items(p_camp, status="rejalashtirish")) == 3)
    check("list_project_items(category=bank): 1 ta", len(await database.list_project_items(p_camp, category="bank")) == 1)
    check("list_project_items(year/month 2026-07): 1 ta (primary_date bor)", len(await database.list_project_items(p_camp, year=2026, month=7)) == 1)
    check("list_project_items(project_id=None): barcha loyihalardan", len(await database.list_project_items(None)) >= 5)

    # ── update: scalar + fields shallow-merge (null → o'chirish) ──
    await database.update_project_item(i1, {"status": "tasdiqlash", "fields": {"budget": 8000, "vendor": "Acme", "channel": None}})
    it2 = await database.get_project_item(i1)
    check("update_project_item: scalar (status) yangilandi", it2["status"] == "tasdiqlash")
    check("update_project_item: fields merge — budget yangilandi, vendor qo'shildi, channel o'chdi (null)",
          it2["fields"].get("budget") == 8000 and it2["fields"].get("vendor") == "Acme" and "channel" not in it2["fields"])

    # ── move: status + order_index 0..n re-pack ──
    await database.move_project_item(a, "rejalashtirish", order_index=None)  # A ni oxiriga
    ords = {(await database.get_project_item(x))["title"]: (await database.get_project_item(x))["order_index"]
            for x in (a, b, cc)}
    check("move (o'sha status, oxiriga): re-pack 0..n, A oxirida",
          sorted(ords.values()) == [0, 1, 2] and ords["A"] == 2)
    await database.move_project_item(b, "ishlab_chiqarish")
    check("move (boshqa status): B yangi ustunda order_index=0",
          (await database.get_project_item(b))["status"] == "ishlab_chiqarish"
          and (await database.get_project_item(b))["order_index"] == 0)

    # ── delete ──
    check("delete_project_item: True", await database.delete_project_item(cc) is True)
    check("delete_project_item: o'chgan item None", await database.get_project_item(cc) is None)
    check("delete_project_item(yoq): False", await database.delete_project_item("pi-yoq") is False)


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
