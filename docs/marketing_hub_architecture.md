# Agrobank Marketing Hub — Konsepsiya va Arxitektura

**Status:** Taklif (konsepsiya)
**Ko'lam:** Mavjud `content` (SMM) modulini shu repo ichida to'liq **Marketing Project Management Hub** darajasiga kengaytirish
**Falsafa:** Notion (moslashuvchan database, view, filter, custom field) + Jira (workflow, status, kanban, assignee, reporting)

Bu hujjat generic konsepsiya emas — u ayni shu kod bazasidagi mavjud jadvallar, endpointlar va mini app funksiyalariga bog'langan. Har bir bo'lim "hozir nima bor → nima qo'shiladi" ko'rinishida yozilgan.

---

## 1. Boshqaruv xulosasi (Executive summary)

Tizim hozirda SMM postlarni rejalashtirishga qaratilgan: loyihalar bor, lekin loyiha ichidagi yagona obyekt — SMM **post**. Marketing boshqarmasining ishi esa postdan ancha keng: kampaniya, PR, brending, event, media plan, roadmap va h.k.

**Maqsad:** "post" modelini universal **ProjectItem** modeliga aylantirish; har bir loyihaga o'z **turi (type)**, **workflow'i** va **view'lari** (Calendar, Kanban, Table, Roadmap, Timeline, Dashboard) berish; global va loyiha darajasidagi analitikani ajratish.

**Muhim yangilik:** ko'p narsa noldan qurilmaydi — poydevor allaqachon bor:

| Talab | Hozirgi holat |
|---|---|
| Projects modeli | ✅ `projects` jadvali bor (minimal) |
| Loyiha statusi (active/archived) | ✅ bor |
| Global vs Project dashboard ajratilishi | ✅ `renderDash` vs `renderProjDash` — allaqachon ajratilgan |
| Kalendar view (loyiha ichida) | ✅ `renderProjCal` bor |
| Status workflow | ⚠️ bor, lekin faqat SMM uchun qattiq kodlangan (6 ta status) |
| Universal ProjectItem | ❌ yo'q — faqat `content_posts` |
| Project type | ❌ yo'q |
| Kanban / Table / Roadmap / Timeline | ❌ yo'q |
| Custom fields | ❌ yo'q |
| Template'lar | ❌ yo'q |

---

## 2. Hozirgi holat (As-Is) — kod bilan tasdiqlangan

### 2.1 Ma'lumotlar bazasi

**`projects`** ([database.py:310](../database.py)) — minimal:
```
id, name, description, color, status(active|archived), created_at, updated_at
```
Yo'q: `type`, `icon`, `default_view`, `workflow`, `custom_fields`, `team`, `kpi`, `deadline`.

**`content_posts`** ([database.py:320](../database.py)) — loyiha ichidagi yagona obyekt turi:
```
id, date, category, topic, format, platform, message, hashtags,
project_id, status, assignee, published_url, published_at, reject_reason,
created_at, updated_at
```
Status workflow (SMM'ga qattiq bog'langan): `reja | jarayonda | tekshiruvda | joylandi | rad_etildi | bekor`.

> Diqqat: `tasks` va `meetings` — bu alohida, yordamchi botning asosiy jadvallari ([database.py:34](../database.py), [database.py:61](../database.py)). Ular Marketing Hub'dan mustaqil. Kelajakda "Task" ProjectItem turi bilan bog'lash mumkin (10-bo'limga qarang), lekin MVP'da tegilmaydi.

### 2.2 Backend endpointlar ([webapp.py:1325](../webapp.py))
```
GET    /api/content?project=&year=&month=     content_list
POST   /api/content                           content_create
PATCH  /api/content/{id}                       content_update
DELETE /api/content/{id}                       content_delete
GET    /api/projects                           projects_list
POST   /api/projects                           project_create
PATCH  /api/projects/{id}                      project_update
DELETE /api/projects/{id}                      project_delete
GET    /api/projects/{id}/dashboard            project_dashboard
```

### 2.3 Mini app (frontend) — `webapp_static/index.html`
- Nav: "content" tab = **"Loyihalar"** ([index.html:538](../webapp_static/index.html), [index.html:839](../webapp_static/index.html)).
- `renderContent` → `renderProjList` (loyihalar ro'yxati) yoki `renderProjDetail` ([index.html:964](../webapp_static/index.html)).
- **Loyiha ichida faqat 2 tab:** `Dashboard` va `Kalendar` ([index.html:987](../webapp_static/index.html)).
- SMM post kartochkasi `smmCard` ([index.html:1055](../webapp_static/index.html)), post formasi `cEdit` ([index.html:1080](../webapp_static/index.html)).
- JS konstantalar: `CATCOL` (kategoriya ranglari), `STLBL/STICON/STOPA/_stCol` (status yorliq/ikon/rang) ([index.html:951](../webapp_static/index.html)).

### 2.4 Xulosa (as-is)
Arxitektura "bitta loyiha = SMM kontent kalendari" degan taxminga qurilgan. Loyiha turi degan tushuncha yo'q; barcha view va workflow SMM postga moslangan.

---

## 3. Maqsadli data model (To-Be)

Ikki asosiy o'zgarish: **(A)** `projects` jadvalini boyitish, **(B)** `content_posts` o'rniga universal **`project_items`** jadvali.

### 3.1 `projects` (kengaytirilgan)
Mavjud ustunlar saqlanadi, quyidagilar qo'shiladi (idempotent `ALTER TABLE` migratsiya bilan — [database.py:419](../database.py)dagi content_posts migratsiyasi namunasida):

```
type          TEXT   -- smm | campaign | pr | branding | event | roadmap | media_plan | custom
icon          TEXT   -- tabler icon nomi (masalan "brand-instagram")
default_view  TEXT   -- overview | calendar | kanban | table | roadmap | timeline | dashboard
workflow      TEXT   -- JSON: status ustunlar ro'yxati (quyida)
custom_fields TEXT   -- JSON: qo'shimcha maydonlar ta'rifi (2-bosqich)
team          TEXT   -- JSON: mas'ullar ro'yxati (contacts id'lari)
kpi           TEXT   -- JSON: KPI ta'riflari
deadline      TEXT   -- loyiha umumiy deadline (ISO)
```

`workflow` JSON namunasi (project type'ga qarab default to'ldiriladi):
```json
{ "statuses": [
  {"key":"idea","label":"G'oya","color":"#8a8a9e"},
  {"key":"draft","label":"Qoralama","color":"#E8A317"},
  {"key":"design","label":"Dizayn","color":"#7C3AED"},
  {"key":"approval","label":"Tasdiqda","color":"#2f7ae5"},
  {"key":"scheduled","label":"Rejalashtirilgan","color":"#0C4A6E"},
  {"key":"published","label":"Joylandi","color":"#16A34A"}
]}
```

### 3.2 `project_items` (universal ProjectItem)
`content_posts` o'rnini bosadigan yagona jadval. Umumiy maydonlar — ustun; turga xos maydonlar — `fields` JSON blobida (ustunlar portlashining oldini oladi).

```
id           TEXT PRIMARY KEY
project_id   TEXT NOT NULL         -- qaysi loyiha
type         TEXT NOT NULL         -- post|task|subtask|milestone|design|video|
                                   --   event|brief|approval|report|media_placement|
                                   --   pr_material|creative|document|risk|kpi
title        TEXT NOT NULL
description  TEXT
status       TEXT NOT NULL         -- project.workflow ichidagi status key
priority     TEXT                  -- P0|P1|P2|P3 (tasks bilan bir xil konvensiya)
assignee     TEXT                  -- contacts katalogidan
category     TEXT                  -- kalendar ranglash uchun (SMM'da mavjud)
stage        TEXT                  -- roadmap bosqichi (ixtiyoriy)
primary_date TEXT                  -- kalendar/timeline uchun asosiy sana (ISO)
start_date   TEXT
end_date     TEXT
deadline     TEXT
order_index  INTEGER               -- kanban ustun ichidagi tartib
parent_id    TEXT                  -- subtask/related uchun
fields       TEXT                  -- JSON: turga xos maydonlar (quyida)
created_by   TEXT
created_at   TEXT NOT NULL
updated_at   TEXT NOT NULL
```

Indekslar: `(project_id, status)`, `(project_id, primary_date)`, `(assignee)`, `(project_id, type)`.

**`fields` JSON — turga xos maydonlar:**

| Type | `fields` ichidagi maydonlar |
|---|---|
| `post` (SMM) | platform, format, caption, hashtags, publish_date, content_category, designer, copywriter, approval_status, published_url, kpi{reach,views,er,clicks,saves} |
| `campaign` | channel, campaign_stage, budget, kpi, placement, vendor, material_type, launch_date, report_date |
| `branding` | object_type, location, size, contractor, design_status, production_status, installation_date, photo_report, approval_person |
| `pr_material` | media_name, material_type, speaker, journalist, publication_date, coverage_link, pr_value, responsible_person |
| `event` | venue, date, participants, agenda, budget |
| `milestone` | target_date, dependencies |

> **Nega JSON?** Har bir type uchun 8-12 ta ustun qo'shsak, jadval 60+ ustunga o'sadi va aksariyati NULL bo'ladi. Umumiy, tez-tez filtrlanadigan maydonlar (status, assignee, deadline, primary_date, category) — ustun; qolgani — JSON. Bu Notion'ning "property" yondashuviga mos va SQLite `json_extract()` bilan filtrlashga imkon beradi.

### 3.3 Migratsiya: `content_posts` → `project_items`
Ma'lumot yo'qotmasdan (backward-compatible):
- Har bir `content_posts` qatori → `project_items` qatoriga (`type='post'`).
- Ko'chirish: `topic→title`, `message→description`, `date→primary_date`, `category/status/assignee/project_id` — to'g'ridan-to'g'ri.
- `format, platform, hashtags, published_url, published_at, reject_reason` → `fields` JSON ichiga.
- Eski status kalitlari (`reja|jarayonda|...`) SMM workflow'i sifatida saqlanadi — mavjud SMM loyihalar ishlashda davom etadi.
- Migratsiya idempotent skript sifatida `database.init()` ichida ([database.py:342](../database.py)), aynan hozirgi migratsiya uslubida.

---

## 4. Workflow engine (Jira uslubi)

Har bir loyiha o'z status ustunlariga ega. Default'lar type bo'yicha (talabnomadagidek):

- **SMM:** Idea → Draft → Design → Approval → Scheduled → Published
- **Campaign:** Brief → Planning → Production → Approval → Launch → Monitoring → Report
- **Branding:** Brief → Design → Approval → Production → Installation → Photo report
- **PR:** Draft → Review → Distribution → Published → Coverage
- **Roadmap/Event:** oddiy (To do → In progress → Done) yoki bosqichli

Statuslar `projects.workflow` JSON'da saqlanadi va Kanban ustunlarini hamda Calendar rang shaffofligini (hozirgi `STOPA` — [index.html:958](../webapp_static/index.html)) generatsiya qiladi. Frontend'dagi qattiq kodlangan `STLBL/STCOL` konstantalari **workflow'dan dinamik o'qishga** o'tkaziladi.

---

## 5. View tizimi

Har bir loyiha uchun 7 view. Hozir 2 tasi bor; qolgani qo'shiladi. Barchasi bitta `/api/projects/{id}/items` endpointidan (filtr param'lari bilan) oziqlanadi.

| View | Holat | Izoh |
|---|---|---|
| **Overview** | 🆕 | Loyiha nomi, turi, progress, mas'ul, deadline, KPI, risklar, bugungi muhim itemlar |
| **Calendar** | ♻️ mavjud `renderProjCal` | `content_posts` o'rniga barcha item turlarini `primary_date` bo'yicha ko'rsatadi |
| **Kanban** | 🆕 | `workflow.statuses` bo'yicha ustunlar; drag-drop → `status` + `order_index` PATCH |
| **Table** | 🆕 | Notion-style: ustun tanlash, filter, sort, group-by (status/assignee/date) |
| **Roadmap** | 🆕 | `stage` bo'yicha bosqichli ko'rinish |
| **Timeline/Gantt** | 🆕 | `start_date`–`end_date` bo'yicha vaqt chizig'i, bog'liqliklar |
| **Dashboard** | ♻️ mavjud `renderProjDash` | Faqat shu loyiha analitikasi (allaqachon bor) |

Tab bar ([index.html:987](../webapp_static/index.html)dagi `.ptabs`) shu 7 view'ga kengaytiriladi. Loyiha `default_view` bilan ochiladi.

---

## 6. Global vs Project Dashboard (allaqachon ajratilgan)

Bu talab qisman bajarilgan — buzmaslik kerak:

- **Global Dashboard** = `renderDash` ([index.html:540](../webapp_static/index.html)), `/api/dashboard`. Butun platforma: jami/aktiv/kechikayotgan loyihalar, bugungi deadline'lar, jamoa yuklamasi, umumiy progress. → **Qo'shiladi:** loyihalar kesimi (hozir asosan tasks/meetings ko'rsatadi).
- **Project Dashboard** = `renderProjDash` ([index.html:1109](../webapp_static/index.html)), `/api/projects/{id}/dashboard`. Faqat bitta loyiha: progress, itemlar, KPI, mas'ullar, deadline, risklar.

Ikkisi allaqachon alohida endpoint va alohida render funksiya — aralashmaydi. ✅

---

## 7. Universal "Item qo'shish"

Hozir: "Post qo'shish" ([index.html:1039](../webapp_static/index.html)) → to'g'ridan-to'g'ri SMM post formasi (`cEdit`).

Yangi: **"+ Item qo'shish"** → avval **type picker** (loyiha turiga mos ro'yxat), keyin o'sha turga mos forma. Masalan SMM loyihada: Post / Task / Milestone; Campaign loyihada: Media placement / Task / Report / Approval.

Forma **type + `custom_fields`** asosida dinamik quriladi: umumiy maydonlar doim, `fields` maydonlari type'ga qarab. Hozirgi `cEdit` SMM (`type=post`) uchun maxsus holat bo'lib qoladi.

---

## 8. Filtrlar

Har bir loyiha ichida kuchli filtr (Notion-style), `/api/projects/{id}/items` query param'lari orqali:

`status, type, assignee, priority, deadline, start_date, category, stage, overdue, created_by` — ustunlardan; `platform, channel, vendor, approval_status` — `fields` JSON'dan (`json_extract`).

Kombinatsiyalar (talabnomadagidek): `Instagram + Reels + Tasdiqda`, `Outdoor + Dizayn bosqichida`, `Deadline shu hafta`, `Kechikkan`, `PR + e'lon qilingan`, `Filial materiallari + ishlab chiqarishda`.

Frontend: saqlanadigan filtr-holati (view bo'yicha), chip ko'rinishida (hozirgi kalendar kategoriya-chip'lari `.cflt` — [index.html:1036](../webapp_static/index.html) namunasida).

---

## 9. Template'lar

Loyiha yaratishda tayyor shablon (type + default workflow + default view + custom fields to'plami):

`SMM Content Calendar · 360 Marketing Campaign · PR Campaign · Branding Project · Event Management · Media Plan · Roadmap Project · Influencer Campaign · Product Launch · Internal Communication Plan`

Texnik: template'lar dastlab **koddagi konstanta** sifatida (JS + Python), 2-bosqichda foydalanuvchi yaratadigan **Template Builder**ga o'sadi.

---

## 10. API o'zgarishlari

Yangi/o'zgargan endpointlar (mavjud `add_routes` — [webapp.py:1325](../webapp.py)ga qo'shiladi):

```
GET    /api/projects/{id}/items?view=&status=&type=&assignee=&...   -- universal ro'yxat
POST   /api/projects/{id}/items                                     -- item yaratish (type bilan)
PATCH  /api/items/{id}                                              -- yangilash (status/order/fields)
DELETE /api/items/{id}
POST   /api/items/{id}/move                                         -- kanban drag-drop (status+order)
GET    /api/templates                                               -- template ro'yxati
```

Backward-compat: mavjud `/api/content*` endpointlari `type=post` uchun **alias** sifatida saqlanadi (eski frontend/bot buzilmasin), keyin bosqichma-bosqich `/items`ga ko'chiriladi.

---

## 11. MVP yo'l xaritasi (repo'ga moslangan)

Talabnomadagi 3 bosqichni mavjud kodga bog'lab, real ish tartibida:

### 1-bosqich — Poydevor (universal model + asosiy view'lar)
1. `projects`ga `type, icon, default_view, workflow` ustunlarini qo'shish (migratsiya).
2. `project_items` jadvali + `content_posts`→`project_items` migratsiyasi.
3. Backend: `/api/projects/{id}/items` (GET/POST) + `/api/items/{id}` (PATCH/DELETE), eski `/content` alias.
4. Loyiha yaratish flow'iga **type + template + default view** qadamlarini qo'shish (`projEdit` — [index.html:996](../webapp_static/index.html)).
5. Frontend: qattiq kodlangan `STLBL` o'rniga `workflow`dan dinamik status.
6. **Kanban view** (drag-drop status) + **Table view**.
7. `renderProjCal`ni universal itemlarga moslash.
8. "Post qo'shish" → "Item qo'shish" + type picker.
9. Loyiha ichida filtr paneli (status/type/assignee/deadline).
10. Global Dashboard'ga loyihalar kesimini qo'shish.

### 2-bosqich — Chuqurlashtirish
Roadmap view · Timeline/Gantt · Custom fields · Template builder · Jamoa yuklamasi · Approval flow · Fayl va commentlar · Hisobot eksporti.

### 3-bosqich — Integratsiya va AI
iCloud/Google Calendar sync (mavjud `calendar_service.py`dan foydalanib) · Telegram eslatmalar (mavjud `scheduler.py`) · Jira/Drive integratsiya · KPI tracking · AI yordamchi (brief/caption yozish, risk tahlili, deadline monitoring — mavjud `claude_service.py`ustida).

---

## 12. Nomlash va UI/UX

- Nom: **Agrobank Marketing Hub**. Header: "Agrobank Marketing Hub" + pastida joriy kontekst ("Loyiha: Agrobank SMM / Iyul kontent rejasi").
- Dark mode va hozirgi vizual uslub **saqlanadi** — faqat arxitektura kengayadi.
- Loyiha ichidagi tab'lar: Overview · Calendar · Kanban · Roadmap · Timeline · Table · KPI · Files · Settings.

---

## 13. Risklar va e'tibor

1. **Backward-compatibility:** mavjud SMM loyihalar va bot integratsiyasi buzilmasligi shart — `content_posts` migratsiyasi va `/content` alias majburiy.
2. **Ustunlar portlashi:** turga xos maydonlar uchun JSON `fields` (10-15 ta type × 8-12 maydon = 100+ ustunning oldini oladi).
3. **Ko'lam nazorati:** 1-bosqich MVP (universal model + Kanban/Table/Calendar) alohida yetkazib berilishi kerak — hammasini birdan qurish tavsiya etilmaydi.
4. **`tasks`/`meetings` bilan chalkashlik:** ular alohida asosiy modul — MVP'da `project_items` ulardan mustaqil; integratsiya keyingi bosqichda.
5. **Migratsiya xavfsizligi:** ishlab chiqarish DB'sida sinovdan oldin zaxira; `dev_web.py` lokal DB'da sinov.

---

## 14. Yakuniy natija

Natijada Agrobank uchun quyidagi imkoniyatli platforma: SMM rejalashtirish · marketing kampaniyalari · PR/media plan · brending kuzatuvi · roadmap/milestone · Kanban ish jarayoni · har loyiha uchun alohida dashboard va workflow · Notion uslubidagi view/filter · Jira uslubidagi status/assignee/deadline/reporting.

Bu — mavjud SMM kontent kalendarini **marketing boshqarmasi uchun to'liq Project Management Hub**ga aylantirish.
