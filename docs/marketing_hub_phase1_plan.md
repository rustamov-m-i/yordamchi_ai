# Agrobank Marketing Hub — Phase 1 (MVP) yagona implementatsiya rejasi (FINAL)

**Repo:** `/Users/maqsudrustamov/Documents/Yordamchi (SI)`
**Konsepsiya:** `docs/marketing_hub_architecture.md` (§3 data model, §11 1-bosqich, §13 risklar)
**Buildable increments:** har qadam mustaqil deploy-ready — DB avval va backward-compatible, `/content` alias saqlanadi, ilova hech qachon buzilmaydi.

> **Verification review holati:** Bu reja adversarial-review (17 topilma) dan keyingi YAKUNIY versiya. Har blocker/major topilma tegishli qadamga singdirilgan; minor'lar joyida qayd etilgan; noto'g'ri/amal qilmaydigan topilmalar oxirgi "Verification review — resolved concerns" bo'limida sabab bilan ochiqlab bekor qilingan.

Fayllar (Phase 1 butun bo'yicha):
- **YANGI:** `config_marketing.py` — workflow/template/item-type/field konstantalarining Python nusxasi (yagona haqiqat manbai).
- `database.py` — SCHEMA (projects ustunlari + `project_items` DDL, **scalar `category`/`priority`/`stage` ustunlari bilan**), `init()` migratsiya + copy (**bir xil `init()` konteksti ichida, marker-gated**), 6 ta CRUD funksiya, `content_*` adapterlar, `list_projects`/`content_dashboard` repoint.
- `webapp.py` — `_ITEM_FIELDS`/`_JSON_FIELDS`/`_MAX_LEN`, `_pick` 2-qatorli qo'shimcha, 5 handler + 5 route, `/api/templates`, `get_project`/`list_projects` `workflow`/`type` normalizatsiyasi, `_PROJECT_FIELDS` kengaytmasi.
- `webapp_static/index.html` — workflow resolverlar, Kanban + Table view, `_projTabs` dispatch, `itemAdd` type-picker + generic form, `projEdit` type/template flow, config nusxasi.
- **YANGI:** `tests/marketing_hub_check.py` — smoke test (**Qadam 6 va Qadam 7 doirasida yaratiladi, quyida to'liq tarkib berilgan**).

---

## Reconciliation — to'rt dizayn orasidagi kelishmovchiliklar hal qilindi

Amalga oshirishdan oldin to'rt dizayn bir-biriga zid bo'lgan 5 nuqta bir qarorga keltirildi. Bu qarorlar quyidagi qadamlarda majburiy:

1. **SMM workflow status kalitlari** — **6 ta legacy kalit** (`reja|jarayonda|tekshiruvda|joylandi|rad_etildi|bekor`) saqlanadi (Dizayn #4). Dizayn #3 dagi 5-kalit SMM shabloni **rad etiladi**: `content_dashboard()` (`SUM(... status='joylandi')`, `CONTENT_STATUSES` @ `database.py:986`), seed/backfill, va frontend `cSetStatus` shu 6 kalitga qattiq bog'langan. `config_marketing.WORKFLOWS["smm"]` = 6 legacy kalit, identity map.

2. **Multi-type workflow siyosati (yangi, review #9 dan)** — **Model B, aniq belgilangan:** `project_items.status` loyiha-turiga xos (campaign `brif|ishlab_chiqilmoqda|...`, pr `...`, h.k.). `content_dashboard()` **faqat `type='post'` itemlarni** tahlil qiladi (Qadam 7 adapter), shuning uchun `CONTENT_STATUSES` bo'yicha hard-code hisobot **buzilmaydi** — u hech qachon non-SMM statuslarni ko'rmaydi. Kanban/Table esa har loyiha `workflow` JSON'idan status ustunlarini oladi (`_wf()`), `CONTENT_STATUSES`'ga bog'liq emas. Model A (barcha turlar 6-status majbur) **rad etiladi** — arxitektura maqsadiga zid.

3. **`fields` PATCH semantikasi** — **shallow-merge** (Dizayn #2): `{"platform":"Instagram"}` yuborilsa `hashtags` yo'qolmaydi; `null` qiymat kalitni o'chiradi. **Concurrency (review #3):** merge bitta yozuv-tranzaksiyasi ichida read-modify-write (`BEGIN IMMEDIATE`) bilan bajariladi — SQLite yozuv-mutexi ostida atomik; `JSON_SET` ishlatilmaydi (u null→o'chirish semantikasini ifodalay olmaydi). Batafsil Qadam 6.

4. **`content_posts` taqdiri** — **`project_items`ga repoint** (Dizayn #2): to'rtta `content_*` DB funksiyasi `type='post'` ustidagi ingichka adapterga aylanadi. `content_posts` jadvali **saqlanadi** (yozilmaydi) — faqat rollback zaxira. Ikki jadvalni parallel yozish (divergence/duplikat xavfi) **rad etiladi**.

5. **DB funksiya imzolari** — Dizayn #2 nusxasi kanonik (`type_`, `_row_to_item`, `get_project_item`, order_index auto-append, `move_project_item` re-pack). Dizayn #1 dagi `year/month` filtr qo'shiladi (calendar uchun kerak).

6. **Migratsiya tartibi (qayta ishlangan, review #1/#10 dan)** — copy blok **bir xil `init()` `async with` konteksti ichida** (alohida connection EMAS), va **orphan-backfill dan OLDIN** ishlaydi: `content_posts` → `project_items` (NULL `project_id` bilan birga) ko'chiriladi, so'ng orphan-backfill `project_items.project_id IS NULL` ni "Agrobank SMM" ga bog'laydi. Idempotentlik **marker qatori** (`id='_migration_marker'`) bilan, `pi_empty` heuristikasi emas. Batafsil Qadam 4–5.

7. **1st-class ustunlar vs `fields` blob (yangi, review #11 dan)** — Aniq shartnoma:
   - **1st-class (queryable) ustunlar:** `id, project_id, type, title, description, status, priority, assignee, category, stage, primary_date, start_date, end_date, deadline, order_index, parent_id, created_by, created_at, updated_at`.
   - **`fields` JSON blob:** faqat type-ga xos qo'shimcha maydonlar — post uchun `format, platform, hashtags, published_url, published_at, reject_reason`; boshqa turlar uchun ITEM_FIELDS bo'yicha.
   - `content_posts.category` → `project_items.category` **scalar ustuni** (filtr uchun), `fields`ga solinmaydi (review #2). `format/platform/...` esa `fields`ga.

8. **`config_marketing` ↔ `index.html` lockstep (yangi, review #14 dan)** — Ikkala manba **inson tomonidan qo'lda sinxron** saqlanadi, lekin drift'ni ushlash uchun **runtime guard + test** qo'shiladi: `/api/templates` va (yangi) `/api/marketing-config` backend'dan JS config'ni yetkazadi; `tests/marketing_hub_check.py` `config_marketing` va `webapp_static/index.html` ичидаги JS bloklaridagi `WORKFLOWS`/`TEMPLATES` kalitlar to'plamini regex bilan taqqoslaydi. Batafsil Qadam 10 + Testing.

---

## PHASE 1 SCOPE (shu rejada quriladi)

Universal `project_items` model · `content_posts→project_items` migratsiya · items API (list/create/update/delete/move) · workflow-driven dinamik statuslar · **Kanban** view · **Table** view · "Item qo'shish" type-picker + generic form · loyiha yaratish flow (type/template/default_view/icon) · workflow+template konstantalar (Python↔JS mirror + drift guard) · **`tests/marketing_hub_check.py` smoke test**.

## PHASE 2/3 GA KECHIKTIRILADI (shu rejada YO'Q)

**Phase 2:** Roadmap view · Timeline/Gantt · Custom-field builder · Approval flow · Fayl va commentlar · Hisobot eksporti · Overview tab boyitilishi · Global Dashboard loyihalar kesimi · `fields` bo'yicha `json_extract` filtr (platform/channel/vendor) · per-project workflow tahriri · `content_posts→project_items` uzluksiz sinxronizatsiya (Phase 1 point-in-time snapshot) · non-post itemlar uchun cross-project analytics dashboard. **Phase 3:** iCloud/Google Calendar sync · Telegram eslatmalar · Jira/Drive integratsiya · KPI tracking · AI (brief/caption/risk/deadline). **Tegilmaydi:** `tasks`/`meetings`/`notes` modullari — `project_items`dan mustaqil.

---

# QADAMLAR

## Qadam 1 — `config_marketing.py` yaratish (yagona konstanta manbai)

**(a) Fayl:** `/Users/maqsudrustamov/Documents/Yordamchi (SI)/config_marketing.py` (YANGI).

**(b) O'zgarish:** Dizayn #4 dagi Python bloklarini to'liq shu modulga joylashtirish. Boshqa hech narsaga bog'liq emas, shuning uchun birinchi — keyingi qadamlar buni import qiladi.

> **GATE (review #7 — blocker):** Qadam 2 ga o'tishdan **oldin** bu qadam to'liq bajarilib, acceptance testidan o'tishi SHART. Barcha downstream qadamlar `import config_marketing` ga tayanadi; agar bu modul import bo'lmasa, hamma narsa import xatosi bilan yiqiladi. Majburiy tekshiruv: `venv/bin/python -c "import config_marketing as m; print(list(m.WORKFLOWS.keys()))"` xatosiz ishlashi kerak.

**(c) Aniq mazmun** (Dizayn #4 §1–§5 Python nusxasi):

```python
# config_marketing.py — webapp_static/index.html dagi JS config blok bilan LOCKSTEP.
# Ikkalasidan biri o'zgarsa, ikkinchisi ham. tests/marketing_hub_check.py drift'ni ushlaydi.
_C = {"grey":"#8a8a9e","amber":"#E8A317","amber2":"#B45309","violet":"#7C3AED",
      "blue":"#2f7ae5","navy":"#0C4A6E","green":"#16A34A","red":"#d64545",
      "pink":"#E8557F","teal":"#0EA5A5"}

WORKFLOWS = {
    # smm — LEGACY 6 kalit, identity map. RENAME QILINMAYDI (content_dashboard bog'liq).
    "smm": [
        {"key":"reja","label":"Reja","color":_C["amber2"]},
        {"key":"jarayonda","label":"Jarayonda","color":_C["blue"]},
        {"key":"tekshiruvda","label":"Tekshiruvda","color":_C["violet"]},
        {"key":"joylandi","label":"Joylandi","color":_C["green"]},
        {"key":"rad_etildi","label":"Rad etildi","color":_C["red"]},
        {"key":"bekor","label":"Bekor","color":_C["grey"]},
    ],
    "campaign":[...], "pr":[...], "branding":[...], "simple":[...], "roadmap":[...],  # Dizayn #4 §1
}
PROJECT_TYPES = { "smm":{...}, "campaign":{...}, ... }                                 # Dizayn #4 §1
ITEM_TYPES = { "post":{...}, "task":{...}, ... }                                       # Dizayn #4 §2
PROJECT_ITEM_TYPES = { "smm":["post","task","milestone","note"], ... }                 # Dizayn #4 §2
ITEM_FIELDS = { "post":[...], "campaign_item":[...], "task":[], ... }                  # Dizayn #4 §3
TEMPLATES = [ {"id":"smm_calendar",...}, ..., {"id":"blank",...} ]                     # Dizayn #4 §4
TEMPLATES_BY_ID = {t["id"]: t for t in TEMPLATES}

def default_workflow(project_type: str) -> dict:
    key = PROJECT_TYPES.get(project_type, PROJECT_TYPES["custom"])["workflow"]
    return {"statuses": WORKFLOWS[key]}

def item_types_for(project_type: str) -> list[str]:
    return PROJECT_ITEM_TYPES.get(project_type, PROJECT_ITEM_TYPES["custom"])

def fields_for(item_type: str) -> list[dict]:
    return ITEM_FIELDS.get(item_type, [])

def apply_template(template_id: str) -> dict:
    t = TEMPLATES_BY_ID.get(template_id)
    if not t: return {}
    return {"type":t["type"],"icon":t["icon"],"color":t["color"],
            "default_view":t["default_view"],"workflow":default_workflow(t["type"])}

def map_legacy_post_status(status: str) -> str:
    valid = {s["key"] for s in WORKFLOWS["smm"]}
    return status if status in valid else "reja"
```

> **Reconciliation qaydi:** `WORKFLOWS["smm"]` = 6 legacy kalit (Reconciliation #1). Dizayn #3 dagi 5-kalit SMM shabloni ishlatilmaydi.

**(d) Acceptance:** `venv/bin/python -c "import config_marketing as m; assert [s['key'] for s in m.WORKFLOWS['smm']]==['reja','jarayonda','tekshiruvda','joylandi','rad_etildi','bekor']; assert m.default_workflow('smm')['statuses'][3]['key']=='joylandi'; assert m.apply_template('campaign_360')['type']=='campaign'; print('ok')"` → `ok`. Import xatosiz. Bu qadam yashil bo'lmasa Qadam 2 boshlanmaydi.

---

## Qadam 2 — DB SCHEMA: `projects` ustunlari + `project_items` jadvali

**(a) Fayl:** `database.py`.

**(b) O'zgarish:**
1. `projects` `CREATE TABLE` ichiga (`database.py:310–318`) 4 ustun qo'shish (fresh DB uchun).
2. `project_items` DDL + 5 indeksni SCHEMA `"""` string **ichiga**, `idx_content_date` (@338) qatoridan **keyin**, yopuvchi `"""` (@339) dan **oldin** (review #8 — blocker: aynan shu joy).

> **Joylashuv (review #8):** SCHEMA `"""` string 33-qatordan boshlanadi va 339-qatorda yopiladi. Yangi DDL **string ichida** turishi shart (aks holda `executescript(SCHEMA)` @366 buziladi). Aynan `idx_content_date` (338) dan keyin, yopuvchi triple-quote (339) dan oldin joylashtiriladi.

**(c) Aniq kod** (Dizayn #1 §1 + §2, review #2/#11 bilan tuzatilgan):

`projects` CREATE (@310–318), `status` qatoridan keyin:
```sql
    type TEXT,
    icon TEXT,
    default_view TEXT NOT NULL DEFAULT 'calendar',
    workflow TEXT,
```

`idx_content_date` (@338) dan keyin, yopuvchi `"""` (@339) dan oldin — to'liq DDL:
```sql
CREATE TABLE IF NOT EXISTS project_items (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,          -- FK YO'Q (reminders/content_posts konvensiyasi)
    type TEXT NOT NULL DEFAULT 'post',
    title TEXT NOT NULL,
    description TEXT,
    status TEXT NOT NULL DEFAULT 'reja',
    priority TEXT,
    assignee TEXT,
    category TEXT,                     -- 1st-class scalar (filtr uchun; review #2)
    stage TEXT,
    primary_date TEXT,                 -- YYYY-MM-DD (datetime EMAS)
    start_date TEXT, end_date TEXT, deadline TEXT,
    order_index INTEGER NOT NULL DEFAULT 0,
    parent_id TEXT, fields TEXT, created_by TEXT,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pitems_project_status   ON project_items(project_id, status);
CREATE INDEX IF NOT EXISTS idx_pitems_project_date     ON project_items(project_id, primary_date);
CREATE INDEX IF NOT EXISTS idx_pitems_project_type     ON project_items(project_id, type);
CREATE INDEX IF NOT EXISTS idx_pitems_project_category ON project_items(project_id, category);
CREATE INDEX IF NOT EXISTS idx_pitems_assignee         ON project_items(assignee);
```

> **`category` scalar ustuni (review #2 — blocker, qisman):** Reviewer "project_items da scalar category yo'q" degan — bu DDL ga qo'shilgani bilan **hal qilindi**. `category` filtri (kalendar `_catList()`, dashboard) scalar ustunga tayanadi; copy loop (Qadam 5) va `_post_to_item_data` (Qadam 7) scalar `category` ni to'ldiradi. `format/platform/...` esa `fields` blobida qoladi (Reconciliation #7). **`priority`/`stage`** ham 1st-class scalar sifatida DDL da — kelajakdagi type'lar (task priority, campaign stage) uchun; hozir NULL.
>
> **FK YO'Q** (Dizayn #1 §5): migratsiya oldidagi `project_id=NULL` postlar va SQLite `ALTER` da FK qo'sha olmasligi sabab. Ownership handler qatlamida (Qadam 8) va `delete_project` fan-out (Qadam 7) da ta'minlanadi.

**(d) Acceptance:** Fresh DB da `venv/bin/python -c "import asyncio,database; asyncio.run(database.init())"` → `sqlite3 data/yordamchi.db ".schema project_items"` jadval + 5 indeks (`category` ustuni bilan) ko'rsatadi; `.schema projects` da `type/icon/default_view/workflow` bor. Mavjud DB hali buzilmaydi (migratsiya keyingi qadamda).

---

## Qadam 3 — `init()` migratsiyalari: `projects` ALTER (mavjud DB uchun)

**(a) Fayl:** `database.py`, `init()` ichida, `content_posts` ALTER blokidan keyin (@419–431), asosiy `await db.commit()` (@433) dan **oldin**.

**(b) O'zgarish:** mavjud DB'larga `projects` ustunlarini idempotent qo'shish (meetings/tasks/content_posts idiomi @368+, @419+).

**(c) Aniq kod** (Dizayn #1 §1; review #16 — idempotentlik guard'i allaqachon mavjud, quyida saqlangan):
```python
        # projects: Marketing Hub ustunlari (mavjud DB'lar uchun).
        cur = await db.execute("PRAGMA table_info(projects)")
        project_cols = {row[1] for row in await cur.fetchall()}
        if project_cols:
            for col in ("type", "icon", "workflow"):
                if col not in project_cols:
                    await db.execute(f"ALTER TABLE projects ADD COLUMN {col} TEXT")
            if "default_view" not in project_cols:                     # review #16: existence-guard
                await db.execute(
                    "ALTER TABLE projects ADD COLUMN default_view TEXT NOT NULL DEFAULT 'calendar'")
```

> **Idempotentlik (review #16 — minor):** `default_view` uchun `if "default_view" not in project_cols` guard'i **allaqachon** yuqoridagi kodda bor (draft'da ham bor edi) — takroriy `init()` da `ALTER` qayta ishlamaydi. `NOT NULL` + `DEFAULT 'calendar'` SQLite `ALTER` qoidasiga mos (content_posts.status @429 bilan bir xil idiom). Review #16 ning fix'i allaqachon qanoatlantirilgan; qo'shimcha o'zgarish shart emas — faqat guard borligini implementatsiyada tasdiqlang.

**(d) Acceptance:** Migratsiya qilingan eski DB da `PRAGMA table_info(projects)` 11 ustun; `default_view` default `'calendar'`. `init()` ikki marta chaqirilsa xatosiz (idempotent, hech qanday "column already exists"). Mavjud `/api/projects` javob bermay qolmaydi.

---

## Qadam 4 — `content_posts → project_items` copy + orphan-backfill (birlashtirilgan, tartiblangan)

> **Katta qayta ishlash (review #1, #6, #10 — blocker/major/minor):** Draft'da bu ikki qadam (copy va backfill) ajratilgan va noto'g'ri tartibda edi. Yakuniy rejada ular **bitta izchil bloik** sifatida, **asosiy `init()` `async with` konteksti ichida** (alohida connection EMAS), **marker-gated** idempotentlik bilan, va **copy → backfill** tartibida ishlaydi.

**(a) Fayl:** `database.py`, `init()` ichida — mavjud orphan-backfill bloki (@452–473) **o'rniga**. Fayl boshiga `import config_marketing` (@18 `import config` yonida).

**(b) O'zgarish — izchil migratsiya bloki, tartib va atomiklik bilan:**

Sabablar:
- **review #1 (blocker):** alohida connection + tashqi try/except partial commit'ni yashiradi. → Bir xil `init()` konteksti, marker qatori bilan.
- **review #10 (major):** eski tartib (backfill→copy) copy'ni ikki marta ma'lumot ustida ishlatardi. → **copy AVVAL** (NULL bilan birga), **backfill KEYIN** (`project_items` ustida).
- **review #6 (minor):** `pi_empty` gate to'liqlikni emas, urinishni tekshiradi. → **marker qatori** (`_migration_marker`) gate.
- **review #5 (major):** backfill loyihasi `type/icon/workflow` ni to'liq INSERT qilsin (soft-default emas). → quyida hard-coded.

**(c) Aniq kod** (asosiy `init()` konteksti ichida, `await db.commit()` @433 dan keyin, seed @441 dan keyin joylashtiriladi — hammasi bitta `db` connection'da):

```python
        # ── Marketing Hub migratsiyasi (copy AVVAL, backfill KEYIN) ────────────────
        # Idempotentlik: marker qatori. pi_empty heuristikasi EMAS (review #6).
        cur = await db.execute(
            "SELECT 1 FROM project_items WHERE id = '_migration_marker'")
        already = await cur.fetchone()
        cur = await db.execute("SELECT COUNT(*) FROM content_posts")
        cp_count = (await cur.fetchone())[0]

        if not already and cp_count:
            now = now_iso()
            # 1) content_posts -> project_items (project_id NULL bilan birga ko'chiriladi).
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT * FROM content_posts")
            posts = await cur.fetchall()
            for p in posts:
                fields = {"format": p["format"], "platform": p["platform"],
                          "hashtags": p["hashtags"], "published_url": p["published_url"],
                          "published_at": p["published_at"], "reject_reason": p["reject_reason"]}
                await db.execute(
                    """INSERT INTO project_items
                       (id, project_id, type, title, description, status, priority, assignee,
                        category, stage, primary_date, start_date, end_date, deadline,
                        order_index, parent_id, fields, created_by, created_at, updated_at)
                       VALUES (?,?,'post',?,?,?,NULL,?,?,NULL,?,NULL,NULL,NULL,0,NULL,?,NULL,?,?)""",
                    (new_id("pi-"), p["project_id"], p["topic"] or "Post", p["message"],
                     config_marketing.map_legacy_post_status(p["status"]), p["assignee"],
                     p["category"],                       # scalar category (review #2)
                     p["date"],                           # primary_date
                     json.dumps(fields, ensure_ascii=False),
                     p["created_at"], p["updated_at"]))
            logger.info("Migrated %d content_posts -> project_items (type='post')", len(posts))
            db.row_factory = None

            # 2) Orphan-backfill: project_id IS NULL bo'lgan project_items'ni "Agrobank SMM" ga.
            #    (Eski kod content_posts ustida edi; endi project_items ustida — copy'dan keyin.)
            cur = await db.execute("SELECT COUNT(*) FROM projects")
            has_project = (await cur.fetchone())[0] > 0
            cur = await db.execute(
                "SELECT COUNT(*) FROM project_items WHERE project_id IS NULL")
            orphans = (await cur.fetchone())[0]
            if not has_project and orphans:
                pid = new_id("pr-")
                today = datetime.now(TZ).date().isoformat()
                await db.execute(
                    """INSERT INTO projects (id, name, description, color, status,
                           type, icon, default_view, workflow, created_at, updated_at)
                       VALUES (?, ?, ?, ?, 'active', 'smm', 'brand-instagram', 'calendar', ?, ?, ?)""",
                    (pid, "Agrobank SMM", "Agrobank ijtimoiy tarmoqlar kontent-rejasi", "#16A34A",
                     json.dumps(config_marketing.default_workflow("smm"), ensure_ascii=False),
                     now, now))
                # status normalizatsiya + published_at (eski backfill semantikasi saqlanadi).
                await db.execute(
                    "UPDATE project_items SET project_id = ?, "
                    "status = CASE WHEN primary_date < ? THEN 'joylandi' ELSE status END "
                    "WHERE project_id IS NULL AND type = 'post'",
                    (pid, today))
                await db.execute(
                    "UPDATE project_items "
                    "SET fields = json_set(fields, '$.published_at', primary_date) "
                    "WHERE project_id = ? AND type = 'post' AND primary_date < ? "
                    "AND json_extract(fields, '$.published_at') IS NULL",
                    (pid, today))
                logger.info("Backfilled %d orphan project_items into 'Agrobank SMM'", orphans)

            # 3) Marker — copy+backfill tamomlandi (review #1/#6).
            await db.execute(
                "INSERT INTO project_items "
                "(id, project_id, type, title, status, order_index, created_at, updated_at) "
                "VALUES ('_migration_marker', '_system', 'marker', 'migration', 'reja', -1, ?, ?)",
                (now, now))
        elif already:
            logger.info("Marketing Hub migration already complete (marker present); skipping.")
        # ── migratsiya oxiri; keyingi eski satrlar (@483 task_history seed) davom etadi ──
```

> **Marker qatori izohi:** `id='_migration_marker'`, `type='marker'`, `project_id='_system'`. Barcha CRUD/list so'rovlari `project_id`/`type` bo'yicha filtrlaydi, marker esa haqiqiy loyiha yoki `type='post'` ostida hech qachon paydo bo'lmaydi. Qo'shimcha xavfsizlik: `list_project_items` va adapterlar `type != 'marker'` ni **avtomatik** chetlab o'tadi, chunki ular aniq `type_`/`type='post'` filtr qo'yadi (Qadam 6/7). Global `list` (filtrsiz) Phase 1 da yo'q.
>
> **Nega asosiy kontekst ichida (review #1):** alohida `aiosqlite.connect()` + tashqi `try/except` partial-commit'ni yashirar edi. Endi copy `init()` ning yagona `db` connection'ida — agar `init()` xato bersa, `async with` chiqishida commit qilinmagan ish rollback bo'ladi (marker ham yozilmaydi), keyingi boot toza holatdan qayta boshlaydi. `pi_empty` gate o'rniga marker → operator qismidan itemlarni qo'lda o'chirsa ham, marker turgani uchun copy qayta ishlamaydi va log aniq "already complete" deydi.
>
> **Nega copy avval (review #10):** avval barcha `content_posts` (NULL `project_id` bilan) `project_items`ga ko'chadi; keyin backfill faqat `project_items.project_id IS NULL` ni bog'laydi. Ikki bosqich ustma-ust ma'lumot ustida ishlamaydi.
>
> **Nega backfill loyihasi to'liq INSERT (review #5):** `type='smm'`, `icon='brand-instagram'`, `default_view='calendar'`, `workflow` JSON hard-coded — Qadam 9 read-side normalizatsiyasi endi faqat **eski, migratsiyadan oldingi** loyihalar uchun zaxira (fresh migratsiya to'g'ri qiymat yozadi). Qadam 9 (soft-default) saqlanadi, chunki mavjud (bu migratsiyadan oldin yaratilgan) `type IS NULL` loyihalar bo'lishi mumkin; lekin migratsiya yo'li endi ularga tayanmaydi.

**(d) Acceptance:**
- Seed DB da `init()` dan keyin `SELECT COUNT(*) FROM project_items WHERE type='post'` == `SELECT COUNT(*) FROM content_posts`.
- Hech bir `type='post'` item `project_id IS NULL` emas (backfill'dan keyin).
- `SELECT type,default_view,workflow FROM projects WHERE name='Agrobank SMM'` → `smm|calendar|{...6 status...}`.
- `SELECT COUNT(*) FROM project_items WHERE id='_migration_marker'` == 1.
- `init()` ikkinchi marta → yangi qator qo'shilmaydi (marker gate), log "already complete".
- `fields` JSON `platform/format/...` ni saqlaydi (Uzbek matn buzilmaydi — `ensure_ascii=False`).

---

## Qadam 5 — (birlashtirildi Qadam 4 ga)

> Draft'dagi alohida "copy (eng oxirida)" qadami **Qadam 4 ga birlashtirildi** (review #1/#10). Bu bo'lim ataylab bo'sh — raqamlash keyingi qadamlarda saqlanadi.

---

## Qadam 6 — `project_items` CRUD funksiyalari (DB qatlami) + smoke test skeleti

**(a) Fayl:** `database.py`, content funksiyalari yonida (`CONTENT_STATUSES` @986 va `content_dashboard` @1155 orasi/atrofi). Va **YANGI** `tests/marketing_hub_check.py` (skelet shu qadamda, adapter testlari Qadam 7 da to'ldiriladi — review #13).

**(b) O'zgarish:** 7 funksiya: `_row_to_item`, `list_project_items` (+`year/month`), `get_project_item`, `create_project_item` (order auto-append), `update_project_item` (fields shallow-merge, **atomik**), `delete_project_item`, `move_project_item` (**atomik** re-pack).

**(c) Aniq kod** (Dizayn #2 §2 kanonik + Dizayn #1 `year/month`; review #3/#4 concurrency tuzatildi):
```python
ITEM_TYPES = ("post","task","note","event","milestone","campaign_item",
              "media_placement","pr_material","branding_item","event_item","report","approval")

def _row_to_item(r) -> dict:
    r = dict(r)
    try: r["fields"] = json.loads(r["fields"]) if r.get("fields") else {}
    except (ValueError, TypeError): r["fields"] = {}
    return r

async def list_project_items(project_id, *, type_=None, status=None, assignee=None,
                             category=None, year=None, month=None, deadline=None, overdue=False):
    where, params = ["project_id = ?", "type != 'marker'"], [project_id]   # marker hech qachon
    if type_:     where.append("type = ?");     params.append(type_)
    if status:    where.append("status = ?");   params.append(status)
    if assignee:  where.append("assignee = ?"); params.append(assignee)
    if category:  where.append("category = ?"); params.append(category)
    if year and month:
        where.append("primary_date LIKE ?"); params.append(f"{int(year):04d}-{int(month):02d}-%")
    if deadline:
        where.append("primary_date LIKE ?"); params.append(f"{deadline}%")
    if overdue:
        where.append("primary_date < ?"); params.append(datetime.now(TZ).date().isoformat())
        where.append("status NOT IN ('joylandi','bekor','done')")
    sql = ("SELECT * FROM project_items WHERE " + " AND ".join(where)
           + " ORDER BY status, order_index, primary_date, title")
    async with aiosqlite.connect(config.DATABASE_PATH) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute(sql, params)
        return [_row_to_item(r) for r in await cur.fetchall()]

async def get_project_item(item_id):   # SELECT ... WHERE id=? AND type!='marker' -> _row_to_item|None
    ...

async def create_project_item(project_id, data):
    # type ITEM_TYPES da bo'lmasa 'task'; order_index = COALESCE(MAX(order_index),-1)+1 shu status ustunida
    # (SELECT MAX va INSERT bitta connection ichida). new_id("pi-"), json.dumps(fields, ensure_ascii=False)
    ...

async def update_project_item(item_id, data):
    # ATOMIK shallow-merge (review #3): bitta connection, "BEGIN IMMEDIATE" bilan yozuv-lock;
    #   SELECT fields -> Python merge (null => pop) -> UPDATE ... SET fields=? -> COMMIT.
    #   BEGIN IMMEDIATE SQLite yozuv-mutexini darrov oladi, shuning uchun ikkita bir vaqtli PATCH
    #   ketma-ket seriyalanadi (last-write EMAS — ikkinchi PATCH birinchining natijasini o'qiydi).
    #   JSON_SET ISHLATILMAYDI: u '$.k'=NULL ni kalit-o'chirish emas, NULL-qiymat sifatida yozadi
    #   (bizga null => pop kerak). Scalar whitelist ustunlar alohida SET.
    ...

async def delete_project_item(item_id):                # DELETE WHERE id=? AND type!='marker'; rowcount>0
    ...

async def move_project_item(item_id, status, order_index=None):
    # ATOMIK re-pack (review #4): bitta connection + "BEGIN IMMEDIATE".
    #   maqsad ustundagi sibling'larni o'qib, item'ni pos ga qo'yib, butun ustunni 0..n qayta raqamlash,
    #   COMMIT. IMMEDIATE lock ostida ikkita move ketma-ket bajariladi -> gap/duplikat bo'lmaydi.
    ...
```

> **Concurrency qarori (review #3 & #4 — major):**
> - Reviewer JSON_SET (#3) va SERIALIZABLE/float-index (#4) taklif qildi. Biz **bitta `BEGIN IMMEDIATE` yozuv-tranzaksiyasi** yondashuvini tanladik, chunki:
>   1. `JSON_SET` **null→pop** semantikasini ifodalay olmaydi (Reconciliation #3 talab qiladi) — shuning uchun #3 ning aynan fix'i **amal qilmaydi**; lekin uning yadro tashvishi (atomiklik) `BEGIN IMMEDIATE` bilan hal qilinadi.
>   2. `float order_index` (#4 muqobili) DDL o'zgartirishni va cheksiz-precision drift'ni keltiradi; `BEGIN IMMEDIATE` re-pack ni oddiy `INTEGER` da atomik qiladi. Bu loyiha bitta-yozuvchi (Telegram bot + webapp, past konkurentlik) — IMMEDIATE lock yetarli.
> - Amaliyot: `aiosqlite` da `await db.execute("BEGIN IMMEDIATE")` → ish → `await db.commit()`; xatoda `rollback`. Bu ilovadagi mavjud yozuv-og'ir funksiyalar bilan bir uslub.

**Smoke test skeleti (`tests/marketing_hub_check.py`, review #13 — bu qadamda yaratiladi):**
```python
# tests/marketing_hub_check.py — Marketing Hub Phase 1 smoke test.
# tests/tasks_section_smoke.py uslubida: tashqi API yo'q, faqat database + config_marketing
# pure logikasi, vaqtinchalik DB (tempfile). CI-safe: iCloud/Telegram/tarmoqqa tegmaydi.
import asyncio, json, os, re, tempfile
# config.DATABASE_PATH ni temp faylga yo'naltirish, keyin database import + init.
# ... (framework bootstrap tasks_section_smoke.py dan)
```
Shu qadamda test quyidagilarni qamraydi (adapter qismi Qadam 7 da qo'shiladi):
- `config_marketing`: `WORKFLOWS["smm"]` 6 legacy kalit; `apply_template` har id uchun to'g'ri `type`/`workflow`; `map_legacy_post_status` identity + noma'lum→`reja`.
- Migratsiya: seed DB da `init()` → `project_items(type='post')` soni == `content_posts` soni; ikkinchi `init()` idempotent (marker gate, soni o'zgarmaydi); hech bir `type='post'` item `project_id IS NULL` emas; marker qatori mavjud.
- CRUD: `create_project_item` → `get_project_item`; filtrlar (type/status/assignee/category/year+month); `update_project_item` shallow-merge (`hashtags` saqlanadi, `null`→o'chirish); `move_project_item` ustunni 0..n qayta raqamlaydi.

**(d) Acceptance:** `venv/bin/python tests/marketing_hub_check.py` → yashil (config + migration + CRUD bloklari). Ad-hoc: `update_project_item(id, {"fields":{"platform":"Instagram"}})` `hashtags`ni saqlaydi; `move_project_item(id, "jarayonda", 0)` ustun tartibini 0..n qiladi; ikkita ketma-ket `update` ikkinchisi birinchi natijani ko'radi (atomiklik).

---

## Qadam 7 — `content_*` funksiyalarini `project_items` (`type='post'`) ga repoint + adapter testlar

**(a) Fayl:** `database.py` — `list/create/update/delete_content_post` (@989–1053), `list_projects` stats (@1088–1091), `content_dashboard` (@1155–1206). Va `tests/marketing_hub_check.py` adapter bloki.

**(b) O'zgarish:** Reconciliation #4 — to'rtta content funksiyani `project_items` ustidagi adapterga aylantirish; stats/dashboardni `type='post'` ga repoint. Handler/route/frontend **o'zgarmaydi** — javob shakli byte-for-byte bir xil.

**(c) Aniq kod** (Dizayn #2 §5 + §6):
```python
_POST_CORE = {"topic":"title","message":"description","date":"primary_date"}
_POST_FIELDS = ("format","platform","hashtags","published_url","published_at","reject_reason")

def _post_to_item_data(data):   # flat SMM payload -> item columns + fields blob (Dizayn #2 §5)
    # topic->title, message->description, date->primary_date, category->category (scalar!),
    # status/assignee verbatim, _POST_FIELDS -> fields dict.  category fields'ga SOLINMAYDI.
    ...
def _item_to_post(item):        # item -> legacy flat post dict (Dizayn #2 §5)
    # title->topic, description->message, primary_date->date, category<-scalar column,
    # fields blob'dan _POST_FIELDS ni yoyadi.
    ...

async def list_content_posts(year=None, month=None, project_id=None, status=None):
    items = await list_project_items(project_id, type_="post", status=status, year=year, month=month)
    return [_item_to_post(i) for i in items]

async def create_content_post(data):      return await create_project_item(data.get("project_id"), _post_to_item_data(data))
async def update_content_post(pid, data): return await update_project_item(pid, _post_to_item_data(data))
async def delete_content_post(pid):       return await delete_project_item(pid)
```

`list_projects` stats (@1088–1091) — `content_posts` → `project_items WHERE type='post'`:
```python
        cur = await db.execute(
            "SELECT project_id, COUNT(*) n, "
            "SUM(CASE WHEN status='joylandi' THEN 1 ELSE 0 END) done "
            "FROM project_items WHERE type = 'post' GROUP BY project_id")
```

`content_dashboard` (@1155–1206) — `type='post'` filtr + har qatorni `_item_to_post(_row_to_item(r))` orqali flatten qilib mavjud `_dist`/`status_count`/`weekly` sikliga uzatish. Aggregatsiya kodi (`CONTENT_STATUSES` @986 bo'yicha `status_count`) **o'zgarmaydi**.

> **review #9 (major) — amal qilmaydi, sabab bilan:** Reviewer `content_dashboard` `CONTENT_STATUSES` bo'yicha hard-code qilgani non-SMM (masalan campaign `Brif`) statuslarni tashlab yuboradi deb xavotir bildirdi. Bu yerda **`content_dashboard` faqat `type='post'` itemlarni tanlaydi** (yuqoridagi filtr), va `type='post'` **faqat SMM workflow** statuslarini ishlatadi (post itemlar faqat SMM loyihalarda yaratiladi, `map_legacy_post_status` doim 6-kalitga tushiradi). Shuning uchun campaign statuslari bu funksiyaga hech qachon yetib bormaydi — hisobot **buzilmaydi**. Bu Reconciliation #2 (Model B) ning bevosita natijasi: campaign/pr Kanban/Table `_wf()` orqali ishlaydi, `content_dashboard` orqali emas. Cross-type analytics — Phase 2 ishi.

> **`delete_project` fan-out (@1142–1152):** `content_posts` fan-out saqlanadi (jadval hali bor), **va** `project_items` uchun qo'shiladi: `UPDATE project_items SET project_id = NULL WHERE project_id = ?` (yoki `delete_posts` bo'lsa `DELETE FROM project_items WHERE project_id = ?`). Aks holda loyiha o'chirilganda itemlar orphan qoladi va FK yo'qligi sabab (Qadam 2) DB darajasida ushlanmaydi.

**Adapter testlar (`tests/marketing_hub_check.py` ga qo'shiladi):**
- `create_content_post` → `list_content_posts` da ko'rinadi.
- `_item_to_post(_post_to_item_data(x))` round-trip barcha SMM maydonlarni saqlaydi (`topic/message/date/category/status/assignee/format/platform/hashtags/...`).
- `content_dashboard` `type='post'` ustida ishlaydi, `status_count` to'g'ri (marker va non-post itemlar hisobga kirmaydi).
- `delete_project` fan-out: loyiha o'chirilsa `project_items.project_id` NULL (yoki `delete_posts` da o'chadi).

**(d) Acceptance:** `dev_web.py` ishga tushib `GET /api/content?year=2026&month=7` migratsiyadan oldingi bilan **bir xil** post ro'yxatini qaytaradi (id prefix `pi-` bo'ladi, opaque). `POST /api/content` → `GET` da ko'rinadi. `GET /api/projects` `post_count`/`progress` SMM loyihada avvalgidek. `GET /api/projects/{id}/dashboard` byte-for-byte bir xil struktura. `tests/marketing_hub_check.py` adapter bloki yashil.

---

## Qadam 8 — Items API: whitelist + handlerlar + routelar (backend)

**(a) Fayl:** `webapp.py`.

> **Line-number qaydi (review #15 — minor):** Quyidagi joylashuvlar **symbol nomi** bilan berilgan (raqam emas), chunki Qadam 6/7 `database.py` ga funksiyalar qo'shib downstream raqamlarni siljitadi. `webapp.py` `database.py` dan mustaqil bo'lsa-da, izchillik uchun barcha anchor'lar symbol nomi bilan.

**(b) O'zgarish:**
1. `_ITEM_FIELDS`, `_JSON_FIELDS`, `_MAX_LEN` update, `_pick` ga `fields` bypass (`_pick` ta'rifi ichida, `_LIST_FIELDS` branch'idan **keyin**).
2. 5 handler (`items_list`, `item_create`, `item_update`, `item_delete`, `item_move`) content handlerlaridan keyin.
3. 5 route `add_routes` da `projects/{id}/dashboard` route'idan keyin.
4. `/api/templates` + `/api/marketing-config` handler + route (config drift guard — Reconciliation #8).

**(c) Aniq kod** (Dizayn #2 §1, §3, §4 + template/config endpoint):

Whitelist (`_LIST_FIELDS`/`_MAX_LEN` ta'riflari yonida):
```python
_ITEM_FIELDS = ("type","title","description","primary_date","status",
                "assignee","category","fields","order_index")   # category 1st-class
_JSON_FIELDS = frozenset({"fields"})
_MAX_LEN.update({"type":24,"title":512,"description":8000,"primary_date":64,
                 "status":24,"assignee":256,"category":128})
```
`_pick` ga (`_LIST_FIELDS` branch'idan keyin):
```python
        if k in _JSON_FIELDS:
            if v is not None and not isinstance(v, dict):
                raise _bad(f"'{k}' obyekt bo'lishi kerak")
            out[k] = v; continue
```
Handlerlar (Dizayn #2 §3 verbatim):
- `items_list` — query: `type/status/assignee/category/deadline/overdue` + `year`/`month`.
- `item_create` — **path segmentidan `project_id` (ownership trust boundary)**, `get_project(pid)` mavjudlik tekshiruvi → yo'q bo'lsa 404, `title` majburiy → yo'q bo'lsa 400.
- `item_update` — `_pick` whitelist bilan PATCH.
- `item_delete`.
- `item_move` — `status` majburiy, `order_index` int validatsiya (`_reject_bad_parent` uslubidagi tekshiruv).

Templates + config mirror:
```python
async def templates_list(request):
    return web.json_response({"templates": config_marketing.TEMPLATES})

async def marketing_config(request):    # frontend drift guard (Reconciliation #8)
    return web.json_response({"workflows": config_marketing.WORKFLOWS,
                              "project_types": config_marketing.PROJECT_TYPES,
                              "item_types": config_marketing.ITEM_TYPES,
                              "project_item_types": config_marketing.PROJECT_ITEM_TYPES,
                              "item_fields": config_marketing.ITEM_FIELDS,
                              "templates": config_marketing.TEMPLATES})
```
Routelar (`projects/{id}/dashboard` route'idan keyin):
```python
        web.get("/api/projects/{id}/items", items_list),
        web.post("/api/projects/{id}/items", item_create),
        web.patch("/api/items/{id}", item_update),
        web.delete("/api/items/{id}", item_delete),
        web.post("/api/items/{id}/move", item_move),
        web.get("/api/templates", templates_list),
        web.get("/api/marketing-config", marketing_config),
```
`import config_marketing` webapp.py boshiga (mavjud `import` bloki yonida).

> Hammasi `/api/*` — `auth_middleware` avtomatik gate qiladi. `item_create` da path segmenti ownership trust boundary (Dizayn #2 §3).

**(d) Acceptance:** `dev_web.py` + `curl`/preview: `POST /api/projects/{pid}/items {"type":"task","title":"Test"}` → 201 + item; `GET .../items?type=task` uni qaytaradi; `PATCH /api/items/{id} {"fields":{"platform":"Instagram"}}` merge; `POST /api/items/{id}/move {"status":"jarayonda"}` 200; nomavjud loyihaga POST → 404; `title` yo'q → 400; `fields` list yuborilsa → 400; `GET /api/templates` 8 ta shablon; `GET /api/marketing-config` WORKFLOWS/TEMPLATES kalitlarni qaytaradi.

---

## Qadam 9 — `get_project`/`list_projects` da `type`/`workflow` normalizatsiya (backend read-side, eski loyihalar zaxirasi)

**(a) Fayl:** `database.py` — `get_project`, `list_projects` loop.

**(b) O'zgarish:** `type IS NULL` (bu migratsiyadan **oldin** yaratilgan eski loyiha) → `'smm'`, `workflow IS NULL` → `default_workflow('smm')` JSON, o'qish paytida. Frontend `_wf()` shu qiymatga tayanadi.

> **review #5 (major) qayd:** Reviewer soft-default ("if not type: type=smm") ikki manba-haqiqat yaratadi dedi va uni olib tashlashni so'radi. Biz **backfill INSERT'ni to'liq qildik** (Qadam 4 — yangi migratsiya to'g'ri qiymat yozadi), shuning uchun soft-default endi **normal yo'lda kerak emas**. Ammo Qadam 9 ni **saqlaymiz** faqat **legacy-fallback** sifatida: allaqachon mavjud (bu deploy'dan oldin qo'lda/eski kod bilan yaratilgan) `type IS NULL` loyihalar bo'lishi mumkin. Farq: normalizatsiya yonida **`logger.warning`** qo'shiladi, shunda operator NULL type'ni ko'radi va tuzatadi (review #5 ning "fail loudly" ruhiga mos, lekin ishlab turgan loyihani buzmasdan):
```python
        d = dict(row)
        if not d.get("type"):
            logger.warning("Project %s has NULL type; defaulting to 'smm' (legacy row)", d["id"])
            d["type"] = "smm"
        if not d.get("workflow"):
            d["workflow"] = json.dumps(config_marketing.default_workflow("smm"), ensure_ascii=False)
        return d
```
`list_projects` loopida ham har `p` uchun bir xil (warning har element uchun emas, faqat NULL bo'lganda).

**(d) Acceptance:** Eski (NULL type) loyihada `GET /api/projects/{id}` `type:"smm"` + `workflow` JSON qaytaradi va log'da bir marta warning. `GET /api/projects` har elementda `type`/`default_view`/`workflow` bor. Yangi (migratsiya/template bilan yaratilgan) loyihalarda saqlangan qiymat buzilmaydi va **warning chiqmaydi** (type NOT NULL).

---

> **Bu yergacha backend to'liq va backward-compatible.** Frontend eski `/content` bilan avvalgidek ishlaydi. Qadam 10+ frontendni qo'shadi; har biri o'zi deploy qilinsa ham eski SMM loyihalar buzilmaydi (fallback yo'llari sabab).

---

## Qadam 10 — Frontend: dinamik workflow resolverlar + config nusxasi + drift guard

**(a) Fayl:** `webapp_static/index.html`.

**(b) O'zgarish:** const blokni (`CATCOL/CATLBL/STCOL/STLBL/STICON/STOPA` ta'riflari, ~951–961) legacy default + resolverlar bilan almashtirish; Dizayn #4 JS config bloklarini (`_MC`, `WORKFLOWS`, `PROJECT_TYPES`, `ITEM_TYPES`, `PROJECT_ITEM_TYPES`, `ITEM_FIELDS`, `TEMPLATES`) qo'shish; `openProj` to'liq loyihani fetch qilsin + **`default_view`→tab-key mapi**.

> **Config drift guard (review #14 — major):** Draft "LOCKSTEP" deb yozgan lekin mexanizm yo'q edi. Yakuniy yechim ikki qatlamli:
> 1. **JS config bloki `index.html` da inline** (Dizayn #4 mirror) — bu Phase 1 uchun kanonik frontend manba (offline/tez).
> 2. **Test-darajali guard:** `tests/marketing_hub_check.py` `config_marketing.WORKFLOWS`/`TEMPLATES` kalit-to'plamini `webapp_static/index.html` ичидаги JS `WORKFLOWS`/`TEMPLATES` bloklaridan regex bilan ajratib **taqqoslaydi** — kalitlar farq qilsa test yiqiladi. Bu drift'ni CI'da ushlaydi.
> 3. **Eski hard-coded konstantalar** (`CATCOL/STCOL/...`) **saqlanadi lekin `// LEGACY FALLBACK — _wf() ishlamasa` deb belgilangan** — ular endi faqat `_wf()` parse xatosida ishlatiladi (o'chirilmaydi, chunki arity-mos call-site'lar bor).

**(c) Aniq kod** (Dizayn #3 §1 + Dizayn #4 JS mirror; review #17 map qo'shildi):
- Legacy `CATCOL/CATLBL/STCOL/STLBL/STICON/STOPA` **saqlanadi** (fallback) + resolverlar: `_wf()` (JSON-string parse + `WORKFLOWS.smm` fallback), `_stList()/_catList()/_st()/_cat()`, va **eski nomlar** `_catCol/_catLbl/_stCol/_stLbl` (arity saqlanadi → tegilmagan call-site'lar ishlaydi).
- Dizayn #4 JS config bloklari resolverlardan keyin.
- **`default_view`→tab-key mapi (review #12/#17):**
```js
const _VIEW2TAB = {calendar:"cal", dashboard:"dash", kanban:"kanban", table:"table"};
async function openProj(id){
  _proj = (_PROJS.find(p=>p.id===id)) || {id, name:"Loyiha"};
  try{ const full = await api(`/projects/${id}`); _proj = {..._proj, ...(full.project||full)}; }catch{}
  _projTab = _VIEW2TAB[_proj.default_view] || "cal";        // review #17: kwot map
  if(!_projTabs().some(x=>x.k===_projTab)) _projTab = _projTabs()[0].k;   // stale tab guard
  render("content");
}
```
- Dinamik call-site tuzatishlari (Dizayn #3 §1): `renderProjCal` (`_catList()` universe; `_st(p.status).opacity`), `smmCard` (`_st(st)`, `_stList()` select), `cEdit` (`_catList()/_stList()`), `renderProjDash` (`_stList()` donut).

**(d) Acceptance:** `dev_web.py` + preview: mavjud "Agrobank SMM" loyihasi ochilganda Kalendar avvalgidek render (kategoriya chiplar, status badge ranglari, opacity). Console'da xato yo'q. `_wf()` NULL workflow'da `WORKFLOWS.smm` qaytaradi. `default_view:"calendar"` bo'lgan loyiha `cal` tab'da ochiladi (map ishlaydi). `tests/marketing_hub_check.py` config-drift bloki yashil.

---

## Qadam 11 — Frontend: Kanban view

**(a) Fayl:** `webapp_static/index.html` — `renderProjDetail` dan keyin JS; CSS mavjud project CSS blokidan keyin.

**(b) O'zgarish:** `renderProjKanban`, `kCard`, drag-drop (`kDrag/kOver/kDrop` → `POST /items/{id}/move`), Dizayn #3 §2 Kanban CSS.

**(c) Aniq kod:** Dizayn #3 §2 verbatim. Ustunlar `_stList()`; kartalar `GET /projects/{id}/items` dan; drop optimistik + `POST /items/{id}/move` (server 200 bermasa revert). `.kcol` ga `ondragleave` clear qo'shiladi. CSS `.kbwrap` `overflow-x:auto` — sahifa gorizontal skroll qilmaydi.

**(d) Acceptance:** Kanban tab (Qadam 13 dan keyin ko'rinadi; hozircha `renderProjKanban(document.getElementById('ptb'))` bilan qo'lda test) ustunlarni workflow bo'yicha ko'rsatadi; kartani boshqa ustunga tortish `POST /items/{id}/move` yuboradi va status DB da yangilanadi (`GET .../items` tasdiqlaydi). Mobil (375px) da board gorizontal skroll, sahifa body skroll qilmaydi.

---

## Qadam 12 — Frontend: Table view

**(a) Fayl:** `webapp_static/index.html` — Kanban funksiyalaridan keyin; CSS Kanban blokidan keyin.

**(b) O'zgarish:** `renderProjTable`, `_TCOLS`, `tblSort`, `tblGroup` + Table CSS (Dizayn #3 §3).

**(c) Aniq kod:** Dizayn #3 §3 verbatim. Sortable header, group-by (status/category/assignee), kategoriya filtr chiplar (`.cflt` reuse), `.tblwrap overflow-x:auto min-width:520px`. Qator bosilsa `itemOpen(id)` (Qadam 14).

**(d) Acceptance:** Table tab itemlarni jadvalda; header bosilsa sort yo'nalishi almashadi; group-by select ishlaydi; jadval `.tblwrap` ichida skroll qiladi, sahifa body emas.

---

## Qadam 13 — Frontend: tab bar + `_projTabs` dispatch

**(a) Fayl:** `webapp_static/index.html` — `renderProjDetail` `.ptabs` bloki; `.ptabs` CSS.

**(b) O'zgarish:** tab barni dinamik `_projTabs()` ga o'tkazish; dispatch `R={dash,cal,kanban,table}`; narrow-screen media query.

> **Tab-tanlash siyosati (review #12 — major, aniq belgilandi):** Reviewer "tab tanlash `type` ga qarab bo'ladi, lekin `default_view` saqlanadi — ziddiyat" dedi. **Qaror:**
> - **Qaysi tablar KO'RINADI** — loyiha `type` ga qarab (SMM/content → Calendar tab qo'shiladi; boshqalar → yo'q, chunki Calendar faqat SMM sana-asosli kontent uchun mantiqiy).
> - **Qaysi tab DASTLAB ochiladi** — `default_view` ga qarab (`_VIEW2TAB` map, Qadam 10). Agar `default_view` mos tab ko'rinmaydigan bo'lsa (masalan campaign'da `default_view` xato tarzda `calendar` bo'lsa), `openProj` dagi stale-tab guard birinchi mavjud tabga tushiradi.
> - Demak `default_view` **advisory (dastlabki tab)**, tab-ro'yxati esa **type-driven**. Bu ziddiyat emas: ikki xil savolga (qaysilar bor / qaysi biri birinchi) ikki xil manba. Kod izohi shuni yozadi.

**(c) Aniq kod** (Dizayn #3 §4):
```js
    <div class="ptabs">${_projTabs().map(t=>`<button class="${_projTab===t.k?'on':''}" onclick="projTab('${t.k}')"><i class="ti ti-${t.icon}"></i> ${t.lbl}</button>`).join("")}</div>
    <div id="ptb"></div>`;
  const tb = $("ptb");
  const R = {dash:renderProjDash, cal:renderProjCal, kanban:renderProjKanban, table:renderProjTable};
  await (R[_projTab] || renderProjCal)(tb);
}
// Tab-RO'YXATI type-driven (qaysilar ko'rinadi). Dastlabki tab default_view-driven (openProj, _VIEW2TAB).
function _projTabs(){
  const cal  = {k:"cal", lbl:"Kalendar", icon:"calendar-month"};
  const base = [{k:"dash",lbl:"Overview",icon:"chart-pie"},
                {k:"kanban",lbl:"Kanban",icon:"layout-kanban"},
                {k:"table",lbl:"Jadval",icon:"table"}];
  const t = _proj.type || "smm";
  return (t==="smm" || t==="content") ? [base[0], cal, base[1], base[2]] : base;
}
```
`.ptabs button{...white-space:nowrap;overflow:hidden}` + `@media(max-width:400px){.ptabs button{font-size:12px;padding:9px 4px}}`.

**(d) Acceptance:** SMM loyihada 4 tab (Overview/Kalendar/Kanban/Jadval); campaign-type loyihada 3 tab (Calendar yo'q). Tab almashtirsa to'g'ri view render. campaign'da `default_view:"kanban"` → Kanban dastlab ochiladi (`_VIEW2TAB`). Stale tab (`cal` campaign'da) `openProj` guard bilan `dash` ga tushadi. 375px da tab labellar sig'adi.

---

## Qadam 14 — Frontend: "Item qo'shish" type-picker + generic form

**(a) Fayl:** `webapp_static/index.html` — `cEdit` dan oldin JS; "Post qo'shish" tugmalari; CSS Table blokidan keyin.

**(b) O'zgarish:** `itemAdd` (type-picker, `itemTypesFor(_proj.type)`), `_pickItem`, `itemEdit` (generic form + type-specific `fieldsFor()` maydonlari), `itemSave`, `itemDel`, `itemOpen` (post→`cEdit`, boshqa→`itemEdit`). "Post qo'shish" tugmalari → `itemAdd(date)`.

**(c) Aniq kod:** Dizayn #3 §5 + Dizayn #4 `itemTypesFor`/`fieldsFor` integratsiyasi. `itemEdit` da umumiy maydonlar (title/status/date/assignee/category/desc) doim, so'ng `fieldsFor(type)` dinamik render (kind: text/textarea/date/number/select/url). `type='post'` → mavjud `cEdit`/`cSave` (o'zgarmaydi). `itemSave` → `POST /projects/{id}/items` yoki `PATCH /items/{id}`; scalar `category` alohida field, type-specific maydonlar `fields` blob'ga yig'iladi.

Kalendar "Item qo'shish" tugmasi:
```js
<button class="btn pri" ... onclick="itemAdd('${_cYM.y}-${String(_cYM.m+1).padStart(2,'0')}-15')"><i class="ti ti-plus"></i> Item qo'shish</button>
```
`cOpenDay` kun tugmasi → `onclick="itemAdd('${ds}')"`.

**(d) Acceptance:** SMM loyihada "Item qo'shish" → picker (Post/Task/Milestone/Note); Post → mavjud SMM forma; Task → generic forma (title/status/date/assignee/category/desc). Saqlangach Kanban/Table/Kalendar'da ko'rinadi. Kanban karta / Table qator bosilsa `itemOpen` to'g'ri formani ochadi (post→cEdit, task→itemEdit).

---

## Qadam 15 — Frontend: `projEdit` type/template/default-view/icon flow (+ backend create/update)

**(a) Fayl:** `webapp_static/index.html` — `projEdit`, `projSave`; `webapp.py` `_PROJECT_FIELDS` + `project_create`; `database.py` `create_project`/`update_project`.

**(b) O'zgarish:** `projEdit` ga template `<select>` (`TEMPLATES` dan), type/icon/default_view selectlar; `projTplChange` (template tanlansa type/icon/view/color to'ldiradi, foydalanuvchi kiritganini saqlab); `projSave` yangi loyihaga `template_id` yuboradi (backend `apply_template` → `workflow`).

**(c) Aniq kod:** Dizayn #3 §6 (`projEdit`/`projTplChange`/`projSave`) — **template manbai `config_marketing.TEMPLATES`** (Qadam 1 mirror / `/api/templates`), Dizayn #3 dagi ichki `PTEMPLATES` **emas** (yagona manba). `projSave` yangi loyihada `b.template_id` yuboradi.

**Backend (bu qadamda):**
- `webapp.py` `_PROJECT_FIELDS` ga `type,icon,default_view` qo'shiladi; `_MAX_LEN` mos.
- `project_create` da `template_id` bo'lsa `config_marketing.apply_template(template_id)` bilan `type/icon/color/default_view/workflow` ni resolve qilib payload'ga merge (foydalanuvchi aniq bergan qiymatlar ustun turadi).
- `database.create_project`/`update_project` `type,icon,default_view,workflow` ustunlarini INSERT/UPDATE qiladi (`workflow` JSON `json.dumps(..., ensure_ascii=False)`).

**(d) Acceptance:** "Yangi loyiha" → template select ("SMM Content Calendar", "360 Marketing Campaign", ...); "360 Marketing Campaign" tanlansa type=`campaign`, default_view=`kanban`. Saqlangach `GET /api/projects/{id}` `type:"campaign"`, `workflow` campaign statuslarini; loyiha ochilganda Kanban dastlab (`_VIEW2TAB`), ustunlar campaign workflow bo'yicha. Mavjud loyiha tahririda template picker **yo'q** (faqat scalar PATCH). Yangi campaign loyihada Qadam 9 warning **chiqmaydi** (type NOT NULL).

---

## Testing

**dev_web.py + preview orqali qo'lda tekshirish:**
```
WEBAPP_OPEN_ACCESS=1 venv/bin/python dev_web.py   # http://localhost:8080
```
1. **Migratsiya (Qadam 2–7):** ishga tushirishdan **oldin** DB backup (`cp data/yordamchi.db data/yordamchi.db.bak`, arch §13.5). Boot logida "Migrated N content_posts -> project_items" va "Backfilled ... 'Agrobank SMM'". `sqlite3` bilan `project_items(type='post')` soni == `content_posts` soni; `project_id IS NULL` yo'q; marker qatori bor. Ikkinchi boot: "migration already complete" logi, soni o'zgarmaydi.
2. **Backward-compat:** eski "Agrobank SMM" Kalendar avvalgidek; `/api/content?year=2026&month=7` migratsiyadan oldingi bilan bir xil (id prefix bundan mustasno); dashboard byte-for-byte.
3. **Kanban (Qadam 11,13):** kartani ustundan ustunga tortish → status DB da o'zgaradi (`preview_network` `/move` 200); refresh da saqlanadi. Mobil (375px) gorizontal skroll board ichida. Bir vaqtda ikki tez move → gap/duplikat order_index yo'q (atomik re-pack).
4. **Table (Qadam 12):** sort/group-by; jadval `.tblwrap` ichida skroll.
5. **Item picker (Qadam 14):** SMM da Post→cEdit, Task→generic; Kanban/Table da ko'rinishi.
6. **Loyiha yaratish (Qadam 15):** har template to'g'ri type/view/workflow; campaign'da Calendar tab yo'q, Kanban dastlab.
7. **Console/network:** `preview_console_logs level:error` bo'sh; `preview_network filter:failed` yo'q.

**Avtomatik test — YANGI `tests/marketing_hub_check.py`** (`tests/tasks_section_smoke.py` framework uslubida — tashqi API yo'q, faqat `database` + `config_marketing` pure logikasi, vaqtinchalik DB; skelet Qadam 6, adapter Qadam 7):
- **config_marketing:** `WORKFLOWS["smm"]` 6 legacy kalit; `apply_template` har id uchun to'g'ri type/workflow; `map_legacy_post_status` identity + noma'lum→`reja`.
- **Migratsiya:** seed DB da `init()` → `project_items(type='post')` soni == `content_posts` soni; ikkinchi `init()` idempotent (marker gate); hech bir `type='post'` item `project_id IS NULL` emas; marker qatori mavjud; marker CRUD/list'da hech qachon ko'rinmaydi.
- **CRUD:** `create_project_item`→`get_project_item`; filtrlar (type/status/assignee/category/year+month); `update_project_item` shallow-merge (`hashtags` saqlanadi, `null`→o'chirish); ketma-ket ikki `update` atomik (ikkinchi birinchini ko'radi); `move_project_item` ustunni 0..n qayta raqamlaydi.
- **Adapter:** `create_content_post`→`list_content_posts`; `_item_to_post(_post_to_item_data(x))` round-trip barcha SMM maydonlarni saqlaydi; `content_dashboard` `type='post'` ustida, `status_count` to'g'ri (marker/non-post kirmaydi).
- **`delete_project` fan-out:** loyiha o'chirilsa `project_items.project_id` NULL (yoki `delete_posts` da o'chadi).
- **Config drift guard (review #14):** `config_marketing.WORKFLOWS`/`TEMPLATES` kalit-to'plami `webapp_static/index.html` ичидаги JS `WORKFLOWS`/`TEMPLATES` bloklari bilan mos (regex extract + set-compare); farq bo'lsa test yiqiladi.

**Mavjud testlarni yangilash:** `tests/qa_regression.py` va `tests/full_test.py` `content_posts` ga to'g'ridan-to'g'ri tayansa (`list_content_posts`/dashboard chaqirsa), repoint (Qadam 7) dan keyin funksiya imzolari o'zgarmagani uchun o'tishi kerak; ishga tushirib tasdiqlanadi:
```
venv/bin/python tests/qa_regression.py && venv/bin/python tests/full_test.py && venv/bin/python tests/marketing_hub_check.py
```

**Ishga tushirish tartibi (buzilmaslik kafolati):** Qadam 1 (GATE — acceptance yashil bo'lmasa to'xtash) → 2→4→6→7→8→9 (backend, har biri o'zi deploy-ready; 4–7 dan keyin `/content` alias ishlaydi) → 10→11→12→13→14→15 (frontend, fallback yo'llari eski loyihalarni saqlaydi). Har backend qadamdan keyin `dev_web.py` boot + `tests/marketing_hub_check.py` yashil bo'lishi kerak.

---

## Verification review — resolved concerns

17 adversarial topilma ko'rib chiqildi. Har biri: **qanday hal qilindi**, yoki **nega amal qilmaydi**.

**Blocker (5):**
- **#1 (copy transaction boundary):** Copy loop endi alohida connection/try-except emas, balki asosiy `init()` ning yagona `async with db` konteksti ichida (Qadam 4). Partial-fail'da `async with` chiqishi commit qilinmagan ishni rollback qiladi, marker yozilmaydi, keyingi boot toza qayta boshlaydi. `pi_empty` heuristikasi → **marker qatori** (`_migration_marker`) gate.
- **#2 (scalar category):** Reviewer "project_items da scalar category yo'q" degan — **DDL da category ustuni allaqachon bor edi**, lekin copy loop uni `fields` blob'ga solar edi. Tuzatildi: `category` 1st-class scalar sifatida to'ldiriladi (Qadam 2 DDL + index, Qadam 4 copy, Qadam 7 `_post_to_item_data`). Reviewer'ning "column yo'q" da'vosi qisman noto'g'ri, ammo asosiy tashvish (folding buzadi) o'rinli va bartaraf etildi.
- **#7 (config_marketing yo'qligi):** Tasdiqlandi — fayl yo'q (repo tekshirildi). Qadam 1 ga qattiq **GATE** qo'yildi: acceptance yashil bo'lmaguncha Qadam 2 boshlanmaydi.
- **#8 (DDL joylashuvi):** Tasdiqlandi — `database.py:338` = `idx_content_date`, `339` = yopuvchi `"""`. DDL string **ichiga**, 338 dan keyin / 339 dan oldin (Qadam 2 aniq belgilandi).
- **#13 (test fayli yo'q):** Tasdiqlandi — fayl yo'q. Uni yaratish endi Qadam 6 (skelet) + Qadam 7 (adapter) doirasida, to'liq tarkib bilan.

**Major (9):**
- **#3 (shallow-merge race):** JSON_SET **amal qilmaydi** — u `null→pop` semantikasini ifodalay olmaydi (Reconciliation #3 talab). O'rniga `BEGIN IMMEDIATE` yozuv-tranzaksiyasi (Qadam 6) — atomiklik ta'minlanadi, null→pop saqlanadi.
- **#4 (order_index race):** `BEGIN IMMEDIATE` re-pack (Qadam 6) — SERIALIZABLE muqobili; float-index rad etildi (DDL murakkabligi, past-konkurentlik ilovada shart emas).
- **#5 (backfill type NULL):** Backfill INSERT endi `type/icon/default_view/workflow` ni to'liq hard-code qiladi (Qadam 4). Qadam 9 soft-default faqat **legacy** loyihalar zaxirasi bo'lib qoldi va `logger.warning` bilan "fail loudly" ruhida.
- **#9 (dashboard hard-coded statuslar):** **Amal qilmaydi.** `content_dashboard` faqat `type='post'` itemlarni tanlaydi (Qadam 7), ular esa faqat SMM 6-statusda. Campaign statuslari bu funksiyaga hech qachon yetmaydi (Model B, Reconciliation #2). Cross-type analytics — Phase 2.
- **#10 (backfill/copy collision):** Tartib teskari qilindi — **copy AVVAL** (NULL bilan), **backfill KEYIN** (`project_items` ustida), bitta izchil blokda (Qadam 4).
- **#11 (fields blob shartnomasi noaniq):** Aniq shartnoma yozildi (Reconciliation #7): 1st-class ustunlar ro'yxati vs `fields` blob; minimal filtr to'plami.
- **#12 (tab logic ziddiyati):** Ziddiyat emas deb aniqlandi va hujjatlashtirildi (Qadam 13): tab-**ro'yxati** type-driven, dastlabki tab `default_view`-driven (`_VIEW2TAB`). Ikki xil savolga ikki manba.
- **#14 (lockstep enforcement yo'q):** Uch qatlamli guard: inline JS mirror + `/api/marketing-config` endpoint + `tests/marketing_hub_check.py` regex kalit-taqqoslash (Qadam 8/10, Testing).

**Minor (3):**
- **#6 (idempotency gate noaniq):** Marker qatori + aniq log ("already complete" / "running") — Qadam 4.
- **#15 (line-number brittleness):** `webapp.py` anchor'lari symbol-nomiga o'tkazildi (Qadam 8). `database.py` anchor'lari joriy `@NNN` bilan qoldi (bu fayl birinchi o'zgaradi, raqamlar amal qiladi), lekin symbol nomi ham berildi.
- **#16 (default_view ALTER idempotency):** Existence-guard (`if "default_view" not in project_cols`) draft'da allaqachon bor edi; Qadam 3 da tasdiqlab qoldirildi — qo'shimcha o'zgarish shart emas.
