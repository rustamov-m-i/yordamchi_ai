# TASK ↔ PROJECT LINK — ARXITEKTURA QARORI (DECISION DESIGN)

**Loyiha:** Yordamchi Telegram bot
**Tur:** QAROR HUJJATI (implementatsiya emas — reja + asos)
**Holat:** Yakunlangan (review fold-in bilan)
**Sana:** 2026-07-07

> Bu hujjat *nima qilinishini* va *nega* aynan shu yo'l tanlanganini belgilaydi. Kod yozilmagan.
> Tamoyillar: **non-breaking**, **refactor-not-rewrite**, **over-engineering yo'q**, har bosqich alohida deploy-ready.

---

## 1. Muammo bayoni

Kod bazasida ish-elementlarining **ikkita tubdan boshqacha modeli** mavjud va ular bir-biriga bog'lanmagan.

### 1.1 Nega ikki jadval?

- **`tasks`** — soddaroq, CRUD-markazli. Sxema: `database.py:35-52`. Bu bot yadrosining "vazifa" tushunchasi: eslatma, risk, takroriylik (recurrence), subtask.
- **`project_items`** — universal konteyner. Sxema: `database.py:352-373`. Marketing Hub uchun qurilgan; har xil tur (post/task/milestone/note/media_placement) bitta jadvalda, workflow bo'yicha status oqadi.

Ikkalasi alohida tarixiy manbadan o'sgan: `tasks` — botning umumiy vazifalari; `project_items` — `content_posts`dan migratsiya bo'lgan universal model (`migrations.py:48-101`, "migratsiyadan keyingi manba" — `database.py:1077-1078`).

### 1.2 Sxema farqi

| Xususiyat | `tasks` | `project_items` |
|---|---|---|
| `project_id` | **YO'Q** | bor (nullable, FK yo'q — `database.py:354`) |
| `type` | **YO'Q** (bir jinsli) | bor (`NOT NULL DEFAULT 'post'`) — polimorf |
| Status | ENUM CHECK (5 qiymat) `database.py:41` | TEXT, CHECK yo'q — workflow'dan `database.py:358` |
| Priority | `P0-P3` CHECK `database.py:40` | `P0-P3` optional (CHECK yo'q) |
| Recurrence | `recurrence_rule/next_at/parent_id` | **YO'Q** |
| `reminded_at` | bor `database.py:51` | **YO'Q** |
| `source` | bor `database.py:48` | **YO'Q** (`created_by` bor) |
| `fields` JSON | **YO'Q** (tekis) | bor (turga xos) |
| `order_index` | **YO'Q** | bor (kanban tartibi) |
| Scheduler | iste'mol qiladi | **iste'mol qilmaydi** |

### 1.3 Status modeli farqi (eng muhim ziddiyat)

- **`tasks.status`** — qat'iy ENUM: `todo/in_progress/blocked/done/cancelled` (`database.py:41`). Bu qiymatlar **scheduler va risk mantiqiga qattiq bog'langan**: "overdue" hisobi, risk agregatlari (`risk_score_counts`), reminder sweep (`priority IN ('P0','P1') AND reminded_at IS NULL`). Terminal holat = `done`/`cancelled`.
- **`project_items.status`** — **runtime'da** `projects.workflow` JSON'idan (`config_marketing.WORKFLOWS`) o'qiladi. Masalan SMM: `reja/jarayonda/tekshiruvda/joylandi/rad_etildi/bekor`. Terminal holat markazlashgan `config_marketing.TERMINAL_STATUSES` frozenset orqali. Loyiha progressi shu asosda hisoblanadi (`database.py:1076-1099`).

**Xulosa:** ikki status modeli **ortogonal**. `tasks` statusi kodda qattiq kodlangan (20+ so'rov), `project_items` statusi config'dan keladi. Ularni bitta o'qqa siqish "lossy" (yo'qotishli) map talab qiladi — bu qaror hujjatining markaziy chegarasi.

---

## 2. Variantlar

### Variant A — Additive: `tasks`ga nullable `project_id`

**Nima o'zgaradi:**
- `tasks` jadvaliga `project_id TEXT` (nullable) + `idx_tasks_project` qo'shiladi. Sxema qo'shimchasi — `database.py:34` SCHEMA'ga va guarded ALTER `init()` ichida (`database.py:425-435` shabloni bo'yicha).
- `tasks` **o'z jadvalida qoladi**: ENUM status, recurrence, reminded_at, source — hammasi tegilmaydi.
- Loyiha ichida tasklar **alohida read-yo'l** orqali ko'rsatiladi: `project_items` (o'z workflow'i bilan) + shu `project_id`li `tasks` (o'z ENUM'i bilan) alohida ro'yxatlar sifatida. Yozuv yo'llari alohida qoladi.

**Nima BUZILADI:** hech narsa (2.4 Consumer Impact Matrix isbotlaydi).

**Data-safety:** eng xavfsiz — additive nullable ustun, orqaga qaytariladi (`DROP COLUMN`), backfill yo'q.

**Effort (halol, qayta ko'rilgan):** ~3-4 kun (3-bo'lim, "Effort" izohi).

**UX natijasi:** Loyiha sahifasida ikkita mantiqiy blok: (1) project_items kanban/kalendar/jadval (o'z workflow'i), (2) shu loyihaga bog'langan tasklar ro'yxati (o'z ENUM statusi bilan). Har element o'z tabiiy modelida qoladi.

### Variant B — Merge: tasklarni `project_items` ichiga ko'chirish (`type='task'`)

**Nima o'zgaradi:** barcha tasklar `project_items`ga `type='task'` bilan ko'chiriladi; recurrence/source/reminded_at → `fields` JSON'ga siqiladi.

**Nima BUZILADI (MAKSIMAL, kritik):**
- Status ENUM → workflow: 20+ so'rov `status IN ('todo','in_progress','blocked')` ga tayanadi (scheduler sweeps, risk hisobi, overdue) — **KRITIK**.
- Recurrence: `recurrence_*`/`parent_id` dedup + atomik `complete_task()` spawn (`database.py:552-660` atrofi) o'lik kod bo'ladi — **KRITIK**.
- `reminded_at`: maydon yo'q; reminder sweep claim va `idx_tasks_reminded` buziladi — **KRITIK**.
- Risk agregatlari (`risk_score_counts`, assignee load) tasks ustunlariga bog'langan — **KRITIK**.

**Data-safety:** xavfli — SQLite ALTER cheklovlari, JSON siqish lossy, rollback murakkab. `migrations.py` (backup + `--dry-run`) talab qiladi.

**Effort (halol):** 5+ faylda 300-500 qator biznes-mantiq qayta yoziladi + haftalab test. Buzuvchi.

### Variant C — Hybrid (adapter qatlami)

**Nima o'zgaradi:** A'dagidek `project_id` (fizik jadval alohida), LEKIN read paytida tasklar **soxta project_item**'ga normalizatsiya qilinadi (`type="task"`, status map `todo→jarayonda`...). Yagona virtual ro'yxat.

**Nima BUZILADI:** yozuv/scheduler tomoni buzilmaydi, LEKIN read adapteri status map'ni majburlaydi — lossy va sun'iy; kanban 2 status to'plamini aralashtiradi.

**Data-safety:** A darajasida (fizik o'zgarish faqat additive).

**Effort (halol):** A + adapter/mapping + kanban render o'zgarishi. Over-engineering xavfi (sun'iy status mapping).

### 2.4 Consumer Impact Matrix (Variant A — non-breaking isboti)

Har bir `tasks` iste'molchisi tekshirildi. Barchasi `project_id` ustunini WHERE/SELECT'da ishlatmaydi → e'tiborsiz qoldiradi.

| Iste'molchi | Joy | Ta'sir (Variant A) |
|---|---|---|
| Scheduler sweeps (reminder/overdue) | `scheduler.py` | Yo'q — status/deadline bo'yicha, `project_id`ga befarq |
| Risk agregatlari (`risk_score_counts`, assignee load) | `database.py` | Yo'q — status/priority bo'yicha |
| Recurrence spawn / dedup / `complete_task` | `database.py:552-660` | Yo'q — recurrence ustunlari tegilmaydi |
| Reminder claim | `database.py` + `idx_tasks_reminded` | Yo'q — `reminded_at IS NULL` bo'yicha |
| CRUD `create_task` / `update_task` | `database.py:552-663` | **Faqat qo'shimcha** — SQL ustun ro'yxatiga `project_id` qo'shiladi (4.2), eski chaqiruvlar `NULL` beradi |
| `list_tasks` va boshqa list_* | `database.py:1410+` | Yo'q — `SELECT *`, yangi ustun avtomatik oqadi; filtr optional |
| Undo (`snapshot_task_tree`/`restore_task_tree`) | `database.py:934-983` | **Avtomatik ijobiy** — dinamik `SELECT *` + `list(row.keys())`, yangi ustun o'z-o'zidan saqlanadi (2.5) |
| Webapp tasks API | `webapp.py:362-415` | **Faqat qo'shimcha** — `_TASK_FIELDS`ga `project_id` (4.2) |
| Claude state block / handlers | `claude_service.py`, `handlers.py` | Yo'q — task shakli o'zgarmaydi |
| Progress formulasi | `database.py:1076-1099` | Yo'q — faqat `project_items` sanaydi (qaror: shunday qoladi, 4.5) |

### 2.5 Undo yo'li — muhim aniqlik (rad etilgan noto'g'ri finding asosi)

Jonli undo yo'li **`restore_task_tree()`** (`database.py:951-983`), va u har qatorni `SELECT *`'dan olib `INSERT ... (list(row.keys()))` bilan dinamik qayta kiritadi. Demak `parent_id` **allaqachon** saqlanadi, va kelajakdagi `project_id` ustuni ham **hech qanday kod o'zgarishisiz** avtomatik saqlanadi. Eski statik `restore_task()` (`database.py:892-931`) statik ustun ro'yxatiga ega, LEKIN u **o'lik kod** — jonli undo (`handlers.py:12879`) faqat `restore_task_tree`ni chaqiradi. (Batafsil rad etish: 8-bo'lim, RF-3.)

---

## 3. TAVSIYA + sabab

## → **VARIANT A (Additive `project_id`)**

**Sabab (tamoyillarga muvofiqlik):**

1. **Non-breaking (qat'iy talab).** 2.4 matritsa isbotlaydi: 0 ta buzilish. Barcha 20+ so'rov o'zgarishsiz. `project_id` — WHERE'da yo'q, e'tiborsiz.
2. **REFACTOR, DELETE EMAS.** Jadval buzilmaydi/ko'chirilmaydi. Tasks o'z modelida qoladi; loyiha bog'lanishi qo'shimcha atribut sifatida ustiga qo'yiladi.
3. **Orqaga qaytariladi.** `DROP COLUMN` mumkin; B'da JSON yechish kerak.
4. **Over-engineering yo'q.** C'ning sun'iy status-mapping adapteri kiritilmaydi. Bu kod bazasining mavjud naqshiga mos: dashboard/today/search hammasi **alohida so'rov → alohida JSON kalit → client kompozitsiya** naqshini ishlatadi. A aynan shu naqshni davom ettiradi.
5. **Kelajakka yo'l ochiq.** Kerak bo'lsa A bosqichma-bosqich B'ga o'tishga imkon beradi (avval bog'lash, keyin sekin migratsiya) — lekin bu hozir SCOPE'da EMAS.

B rad etiladi: buzuvchi, katta effort, tamoyilga zid. C rad etiladi: over-engineering (lossy status mapping), yagona foyda (yagona kanban) UX chalkashligini keltiradi.

**Effort (halol, qayta ko'rilgan — RF-6):** Dastlabki "1-2 kun" so'rov qatlamining o'zgarmasligini nazarda tutgan; lekin feature integratsiyasi (loyiha kontekstida tasklarni ko'rsatish) ko'proqni talab qiladi. Realroq baho **~3-4 kun**: (1) DB additive + `list_tasks` optional filtr; (2) `create_task`/`update_task` SQL + `_TASK_FIELDS` write yo'li; (3) `/api/projects/{id}/tasks` read-endpoint; (4) mini-app dual-source render; (5) regressiya testlari (4.6). Ilova biznes-mantig'ida hamon **0 o'zgarish**.

---

## 4. Variant A uchun ANIQ reja

### 4.1 DB o'zgarishi

**Pattern: guarded ALTER `database.init()` ichida — `migrations.py` EMAS.**

Sabab: additive nullable ustun uchun kod bazasi doimo `init()` ichida idempotent guarded ALTER ishlatadi (`database.py:425-435` — tasks assignee/recurrence/category/parent_id shu naqsh bilan qo'shilgan). `migrations.py` faqat **ma'lumot transformatsiyasi** uchun (bulk COPY, status rewrite — `migrations.py:48-101`), qo'lda `--dry-run`/backup bilan. Bu yerda backfill YO'Q, shuning uchun `migrations.py` kerak emas.

```python
# 1. SCHEMA konstantasiga (database.py:35-52 ichida, tasks CREATE ga):
project_id TEXT,   -- nullable; projects.id ga ishora (FK constraint YO'Q — mavjud naqsh)

# 2. init() ichida, tasks migration blokidan keyin (database.py:435 dan keyin):
if "project_id" not in task_cols:
    await db.execute("ALTER TABLE tasks ADD COLUMN project_id TEXT")
await db.execute("CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id)")
```

**Guarded-ALTER naqshi izohi (RF-8 — kelajak muallif uchun aniqlik):**
- ALTER ADD COLUMN **guard ichida** turishi SHART: ustun faqat bir marta qo'shilishi mumkin, ikkinchi marta xato beradi. `PRAGMA table_info` guard shuni ta'minlaydi.
- CREATE INDEX **guard tashqarisida** turadi: `IF NOT EXISTS` orqali o'zi idempotent.
- **Indexni guard blokiga KO'CHIRMANG.** Aks holda ustun mavjud bo'lsa-yu index bo'lmasa (masalan avval qo'lda qo'shilgan bazada), index hech qachon yaratilmaydi. Joriy joylashuv `init()`ni xavfsiz qayta-ishga-tushuriladigan qiladi.

**NULL semantikasi (RF-9 — aniq yozib qo'yiladi):**
- `tasks.project_id = NULL` → task hech qaysi loyihaga bog'lanmagan (orqaga mos, eski tasklar hammasi shunday).
- So'rovlar aniq filtrlashi kerak: `project_id = ?` (aniq loyiha), `project_id IS NULL` (bog'lanmagan), yoki filtrni umuman qo'ymaslik (ikkalasini ham qamraydi). Filtrsiz so'rov `NULL`larni ham qaytaradi — bu to'g'ri, lekin intuitiv emas, shuning uchun yozib qo'yiladi.

> **FK constraint qo'shilmaydi** — SQLite `ALTER TABLE ADD CONSTRAINT`ni qo'llab-quvvatlamaydi; bu qaror kod bazasida allaqachon qabul qilingan (reminders/project_items nullable FK'siz `project_id`). Nullable `project_id` FK'siz ishlaydi.

### 4.2 Yozuv yo'li (write path) — barcha teginish nuqtalari

Draft dastlab faqat `_TASK_FIELDS`ni eslatgan edi; aslida statik SQL ustun ro'yxatlari ham bor. To'liq ro'yxat:

1. `webapp.py:362-363` — `_TASK_FIELDS` tuple'iga `"project_id"` qo'shiladi (RF-4). Bu bo'lmasa web API orqali bog'lab bo'lmaydi.
2. `database.py:576-597` — `create_task()` INSERT statik ustun ro'yxati. `project_id` ustun + VALUES placeholder qo'shiladi; qiymat `data.get("project_id")`.
3. `database.py:621-627` — `update_task()` ruxsat berilgan maydonlar sikliga `"project_id"` qo'shiladi (Excel re-parenting naqshi kabi).

Barchasi **faqat qo'shimcha**: yangi kalitni qabul qiladi, uzatilmaganda `NULL` (mavjud xatti-harakat).

### 4.3 Yangi read-endpoint

**Yangi (read-only):**
```
GET /api/projects/{id}/tasks   →  database.list_tasks(project_id=id, ...)  (yangi optional filtr)
```
`list_tasks()` ga **optional** `project_id` param (default `None` → mavjud xatti-harakat, filtr qo'shilmaydi). Mavjud chaqiruvlar tegilmaydi.

Muqobil: loyiha detali javobiga alohida `tasks` kaliti (dashboard naqshi bo'yicha — alohida so'rov, alohida JSON kalit, client kompozitsiya qiladi). Ikki variant ham mos; endpoint yondashuvi soddaroq va ustuvor.

**O'zgarmaydi:** scheduler so'rovlari, risk so'rovlari, recurrence, reminders, claude state block.

### 4.4 Status-model kelishuvi (F1 — status collision fold-in)

**Kelishuv: task loyiha ichida ham O'Z ENUM statusini ko'rsatadi. Status map QILINMAYDI.**

Sabab: task statusi (`todo/in_progress/blocked/done/cancelled`) va loyiha workflow statuslari (masalan `reja/jarayonda/joylandi/...`) ortogonal. Bularni bir kanban ustunlariga tiqish — kanban render faqat workflow statuslarini kutadi (`config_marketing.WORKFLOWS`dan) — noto'g'ri render yoki lossy map keltiradi.

Shuning uchun:
- Loyiha sahifasidagi **"Vazifalar" bloki** task statusini o'z etiketlari bilan ko'rsatadi (`handlers`dagi task status tanlovlari: Tayyorlashda/Bajarilishda/To'xtab qoldi/Tayyorlandi/Bekor).
- **`project_items` bloki** workflow statusini ko'rsatadi (config'dan).
- Ikkalasi vizual **alohida bo'limlar** — status ziddiyati yo'q, mapping yo'q, lossy konversiya yo'q. Bu A variantining asosiy soddaligini saqlaydi.
- Tasklar `project_items` kanbaniga **aralashtirilmaydi** (5-bo'lim, punkt 2).

### 4.5 Progress hisobi — aniq qaror (F2 + F7 fold-in)

**QAROR (defer emas, aniq): bog'langan tasklar loyiha progress %'iga QO'SHILMAYDI.** Progress formulasi (`database.py:1076-1099`) o'zgarmaydi — faqat `project_items` va `TERMINAL_STATUSES` sanaydi.

Sabab:
1. Ikki status modeli ortogonal — semantik agregatsiya noaniq ("task done" = "joylandi"mi? task weight qancha?).
2. Formulaga qo'shish orqaga mos EMAS — mavjud loyihalar progressi jim o'zgaradi.

**UX asimmetriyasini hal qilish (F7):** foydalanuvchi bog'langan tasklarni ko'radi-yu ular progress %'iga ta'sir qilmasligi chalkash bo'lmasligi uchun:
- Progress % yonida **alohida, aniq ajratilgan** informatsion ko'rsatkich: **"Bog'langan vazifalar: N ta (M bajarilgan)"** — formuladan tashqari, alohida yorliq bilan.
- Progress bar faqat `project_items` bo'yicha ekani UI'da tushunarli bo'ladi (masalan tooltip: "Loyiha elementlari asosida").

**Hujjatlashtirilgan cheklov (kelajak muallif uchun):** "Loyiha progressi faqat `project_items`ni sanaydi, bog'langan tasklarni EMAS." Agar kelajakda tasklarni progressga qo'shish talabi kelsa — bu **Phase 2 qaror hujjati** mavzusi (weight/konfiguratsiya qarorlari bilan), hozirgi scope'da EMAS.

### 4.6 Test strategiyasi (F10 fold-in)

Non-breaking da'vosi test bilan mustahkamlanadi. Mavjud test naqshlaridan foydalaniladi (`tests/tasks_section_smoke.py`, `tests/qa_regression.py`).

Qo'shiladigan/tekshiriladigan testlar:
1. **Recurrence spawn `project_id`ni saqlaydi** — `complete_task` bog'langan takroriy taskda yangi nusxaga `project_id`ni o'tkazadimi (yoki ataylab NULLmi — qaror qilinadi va test bilan qulflanadi).
2. **Orphan (project_id=NULL) recurrence o'zgarishsiz** ishlaydi.
3. **Reminder sweep `project_id`ga befarq** — claim mantig'i o'zgarmaydi.
4. **Risk agregatlari project filtr QO'SHMAYDI** — hisob global qoladi.
5. **today/overdue filtrlari project-agnostik qoladi.**
6. **Undo (`restore_task_tree`) `project_id`ni saqlaydi** — dinamik SELECT * naqshi tufayli avtomatik, lekin regressiya testi bilan qulflanadi.
7. **Progress formulasi o'zgarmaganini** tasdiqlovchi test (`project_items`-only).

### 4.7 Mini-app o'zgarishi

- `webapp_static/index.html` loyiha detalida yangi **"Vazifalar" seksiyasi** (project_items kanban/kalendar/jadval yonida). Alohida ro'yxat, task ENUM statusi bilan.
- Yangi `/api/projects/{id}/tasks` chaqiriladi.
- "Vazifani loyihaga bog'lash" tugmasi (write yo'li, 4.2).
- "Bog'langan vazifalar: N/M" informatsion ko'rsatkich (4.5).
- Kanban/kalendar `project_items` uchun o'zgarishsiz qoladi (workflow'dan render).

---

## 5. Nima QILINMAYDI (scope guard)

1. **Status mapping/adapter YO'Q** (C'dagi task_status→workflow konversiyasi). Lossy va sun'iy.
2. **`project_items` kanbaniga tasklarni tiqish YO'Q** — ikki ortogonal status modeli aralashtirilmaydi.
3. **Progress formulasiga tasklar QO'SHILMAYDI** (`database.py:1076-1099` tegilmaydi) — mavjud progress qiymatlarini buzmaslik uchun (4.5).
4. **`tasks`ga `type`/`fields`/`order_index`/`stage` ustunlari QO'SHILMAYDI** — bu B'ga sudrab boruvchi bloat. Tasks bir jinsli qoladi.
5. **FK CONSTRAINT qo'shilmaydi** (SQLite cheklovi + mavjud qaror).
6. **`migrations.py` yozilmaydi** — additive nullable ustun `init()` guarded ALTER'ga tegishli.
7. **Auto-migration/backfill YO'Q** — eski tasklar `NULL` bo'lib qoladi.
8. **scheduler/recurrence/reminder biznes-mantiqiga o'zgarish YO'Q.**
9. **Cross-project subtask qoidalari** hozir hal qilinmaydi (`parent_id` sxema agnostik qoladi) — over-engineering.

---

## 6. Risklar + ehtiyot choralari

| Risk | Ehtiyot chorasi |
|---|---|
| Guarded ALTER ikki marta ishlashi | `PRAGMA table_info` guard idempotent (`database.py:425-435` naqsh) — xavfsiz |
| DB buzilishi (kutilmagan) | Deploy oldidan **majburiy `backup_db()`** (`migrations.py:41`) bir marta |
| Ustun to'g'ri qo'shilganini tekshirish | Deploy oldidan `PRAGMA quick_check` (butunlik) + `PRAGMA table_info(tasks)` |
| Orphan `project_id` (loyiha o'chirilsa) | FK yo'q → app-layer: loyiha o'chirilganda bog'langan tasklarni `project_id = NULL`ga qo'yish, YOKI read `LEFT JOIN` — orphan task shunchaki "bog'lanmagan" ko'rinadi. Bosqich 5 (ixtiyoriy) |
| Index'siz sekin filtr | `idx_tasks_project` majburiy |
| Index kardinalligi past bo'lishi | Ko'p task `project_id=NULL` bo'ladi → oddiy `(project_id)` index yetarli. Agar keyin loyiha-ichi status filtri hot yo'l bo'lsa, kompozit `idx_tasks_project_status ON tasks(project_id, status)` ko'rib chiqiladi (RF-5) — hozir kerak emas |
| Progress buzilishi | Formula tegilmaydi (4.5) — risk yo'q |
| Non-breaking regressiya | 4.6 test to'plami |

---

## 7. Bosqichlar (buzmasdan, har biri deploy-ready)

**Bosqich 0 — Backup + tekshirish (deploy oldidan)**
`backup_db()` bajarish; `PRAGMA quick_check`. Deploy-ready: mustaqil.

**Bosqich 1 — DB additive (backend)**
SCHEMA'ga `project_id` + `init()` guarded ALTER + `idx_tasks_project`. `list_tasks()`ga optional `project_id` (default `None`). Barcha mavjud consumerlar o'zgarishsiz.
*Deploy-ready:* ha — hech kim `project_id`ni ishlatmaydi.

**Bosqich 2 — Write yo'li (webapp + SQL)**
`_TASK_FIELDS`ga `project_id` (`webapp.py:362`); `create_task`/`update_task` SQL'ga ustun (`database.py:576-597`, `621-627`). Loyiha detalida "vazifani loyihaga bog'lash".
*Deploy-ready:* ha — additive, eski tasklar `NULL`.

**Bosqich 3 — Read-endpoint**
`GET /api/projects/{id}/tasks` (yoki kengaytirilgan javob, alohida `tasks` kaliti).
*Deploy-ready:* ha — yangi endpoint, mavjudlarga tegmaydi.

**Bosqich 4 — Mini-app UI + testlar**
`webapp_static/index.html`da "Vazifalar" bloki (task ENUM statusi bilan) + "Bog'langan vazifalar: N/M" ko'rsatkich. 4.6 regressiya testlari qo'shiladi.
*Deploy-ready:* ha — frontend qo'shimchasi + testlar.

**Bosqich 5 (ixtiyoriy, kelajak) — orphan tozalash**
Loyiha o'chirilganda bog'langan tasklarni `project_id = NULL`ga qo'yish (app-layer). Hozir SCOPE'da emas.

---

## 8. Review findinglari — qaror (fold / minor / rad)

**Folded (blocker/major — dizaynga kiritildi):**
- **F1 — Status collision** → 4.4 (mapping yo'q; alohida bloklar; kanbanga tiqilmaydi) + 5-bo'lim punkt 1-2.
- **F2 — Progress semantic gap** → 4.5 (aniq qaror: qo'shilmaydi; hujjatlashtirilgan cheklov).
- **F6 — Effort underestimate** → 3-bo'lim Effort izohi (~3-4 kun, aniq scope bilan).
- **F7 — Progress UX asimmetriyasi** → 4.5 (alohida "Bog'langan vazifalar: N/M" ko'rsatkich + tooltip).

**Folded (minor — qo'shildi):**
- **F4 — `_TASK_FIELDS` to'liq emas** → 4.2 (write yo'lining barcha uch teginish nuqtasi: `_TASK_FIELDS` + `create_task` SQL + `update_task` sikli).
- **F5 — Index strategiyasi** → 6-bo'lim (oddiy `(project_id)` yetarli; kompozit — kelajak ehtiyoji bo'lsa).
- **F8 — ALTER pattern hujjati** → 4.1 (guard ichi/tashi izohi + "indexni ko'chirmang").
- **F9 — Nullable semantikasi** → 4.1 (NULL = bog'lanmagan; so'rovlar aniq filtrlashi).
- **F10 — Test coverage** → 4.6 (yetti test; to'g'ri fayllar `tests/tasks_section_smoke.py`, `tests/qa_regression.py`).

**Rad etilgan (asos bilan):**
- **RF-3 (major da'vo qilingan) — "restore_task_tree() parent_id'ni tiklamaydi, PRE-EXISTING bug".** **NOTO'G'RI.** Kod tekshirildi: jonli undo yo'li `restore_task_tree()` (`database.py:951-983`) har qatorni `SELECT *`'dan olib `INSERT ... (list(row.keys()))` bilan **dinamik** qayta kiritadi (`database.py:967-971`) — `parent_id` allaqachon saqlanadi, va kelajakdagi `project_id` ham **hech qanday kod o'zgarishisiz** avtomatik saqlanadi. Snapshot ham `SELECT *` (`database.py:944`), demak butun subtree `parent_id` bilan ushlanadi. Finding statik `restore_task()` (`database.py:892-931`, statik ustun ro'yxati) ni jonli yo'l bilan chalkashtirgan — biroq `restore_task()` **o'lik kod** (`handlers.py:12879` faqat `restore_task_tree`ni chaqiradi). Shuning uchun na parent_id yo'qoladi, na project_id yo'qoladi. Bug yo'q, tuzatish shart emas. (Ixtiyoriy tozalash: o'lik `restore_task()`ni olib tashlash — bu alohida, scope'dan tashqari mavzu.)

---

## Yakuniy xulosa

**Variant A** — additive nullable `project_id` + index + optional filtr + read-endpoint + write-yo'l uchtaligi (`_TASK_FIELDS` / `create_task` / `update_task`) + frontend "Vazifalar" bloki. **Ilova biznes-mantig'ida 0 buzilish** (2.4 matritsa). Status modellari alohida qoladi (mapping yo'q, 4.4); progress formulasi o'zgarmaydi (4.5). B — 300+ qator qayta yozish + kritik buzilishlar. A qat'iy **non-breaking**, **refactor-not-rewrite** va **over-engineering yo'q** tamoyillariga to'liq mos. Halol effort ~3-4 kun, besh bosqich, har biri alohida deploy-ready.

**Tegishli fayllar:**
- `/Users/maqsudrustamov/Documents/Yordamchi (SI)/database.py` — SCHEMA `35-52`, guarded ALTER naqshi `425-435`, `create_task` `552-601`, `update_task` `606-663`, undo `snapshot_task_tree`/`restore_task_tree` `934-983`, `list_tasks` `1410+`, progress `1076-1099`, `project_items` SCHEMA `352-373`
- `/Users/maqsudrustamov/Documents/Yordamchi (SI)/webapp.py` — `_TASK_FIELDS` `362-363`, tasks API `379-415`
- `/Users/maqsudrustamov/Documents/Yordamchi (SI)/scheduler.py` — reminder/overdue sweeps
- `/Users/maqsudrustamov/Documents/Yordamchi (SI)/migrations.py` — `backup_db` `41`, dry-run `103`
- `/Users/maqsudrustamov/Documents/Yordamchi (SI)/config_marketing.py` — `WORKFLOWS` / `TERMINAL_STATUSES`
- `/Users/maqsudrustamov/Documents/Yordamchi (SI)/webapp_static/index.html` — kanban/kalendar render
- `/Users/maqsudrustamov/Documents/Yordamchi (SI)/tests/tasks_section_smoke.py`, `/Users/maqsudrustamov/Documents/Yordamchi (SI)/tests/qa_regression.py` — regressiya testlari
