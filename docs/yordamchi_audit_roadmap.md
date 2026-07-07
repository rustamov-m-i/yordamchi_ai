# Yordamchi (SI) — Task & Project Management: Professional Audit va Refactor Rejasi

> Grounded audit. Prinsip: **qayta qurish emas, ulash va sayqallash** (refactor, not rewrite). Har tavsiya proporsional. Notion/Jira klonini qo'lda qurish rad etilgan.
>
> Kod metrikalari `grep`/`wc` bilan tekshirilgan (2026-07-07). handlers.py = 14,957 satr, 556 funksiya definitsiyasi (523 top-level), 264 tasi `_` prefiksli.

## 1. Qisqa executive summary

Yordamchi allaqachon jiddiy, ishlaydigan tizim — 26,383 satr kod, 20+ scheduler ish, atomik reminder mexanizmi, config-driven Marketing Hub. Ko'p rahbarlar so'raydigan narsalar (kanban, calendar, table, dashboard, project types/workflows, risk skoring, jamoa yuklamasi) ALLAQACHON mavjud. Shuning uchun bu rejaning maqsadi — "noldan qurish" emas, balki **mavjudni ulash, sayqallash va discoverability'ni oshirish**.

Uchta strukturaviy uzilish eng katta qiymatni beradi:
1. **Task ↔ Project bog'lanishi yo'q** (`tasks` jadvalida `project_id` yo'q, `database.py:35-52`) — data modeli ikkiga bo'lingan.
2. **Discoverability muammosi** — Team, Risklar, Statistika bosh menyuda yo'q, faqat Cockpit orqali (`handlers.py:112-140`).
3. **Task modelida bir nechta rahbar-darajali maydon yo'q**: nazoratchi, izohlar, fayl/link, risk sababi, per-task AI tavsiya.

Qolgan hamma narsa — proporsional sayqallash. Notion/Jira klonini qo'lda qurish TAVSIYA ETILMAYDI (foydalanuvchi buni sinab, rad etgan). Rahbar uchun tamoyil: 1-2 bosishda natija.

## 2. Joriy funksional auditi

Har funksiya bo'yicha: hozir qanday / muammo / qulaylik / optimizatsiya / saqlash-yoki-refactor / qaysi PM platformadan o'rganish.

**2.1. Bosh menyu (main_reply_keyboard)**
- Hozir: 2×4 reply keyboard — Cockpit, Bugun, Vazifalar, Eslatmalar, Uchrashuvlar, Yangi, Qidiruv, Sozlamalar (`handlers.py:112-140`).
- Muammo: Team (👥 Ijrochilar), Risklar, Statistika bosh menyuda yo'q — faqat Cockpit drill-down orqali. BTN_STATS aniqlangan (line 122) lekin bosh menyuda ishlatilmaydi.
- Qulaylik: doim ko'rinadigan reply keyboard yaxshi UX.
- Optimizatsiya: 2×4 → 2×5 (Team/Risklar/Stats qo'shish) yoki rotatsiya.
- Saqlash yoki refactor: SAQLASH + kichik refactor (3 tugma qo'shish).
- O'rganish: Linear — flat, discoverable navigatsiya.

**2.2. Cockpit (Boshqaruv paneli)**
- Hozir: matn + inline drill-down (Team, Risklar, Stats, Qaydlar), `cmd_cockpit()` `handlers.py:11573-11606`.
- Muammo: "Back" tugmasi yo'q — "modal tuzoq" hissi; drill-down'lardan Cockpit'ga qaytish yo'q.
- Qulaylik: bir joyda kunlik operatsion snapshot.
- Optimizatsiya: har drill-down panelga "⬅️ Cockpit'ga" inline tugma.
- Saqlash yoki refactor: SAQLASH + back tugma.
- O'rganish: Sunsama/Height — dashboard'dan har elementga drill-down.

**2.3. Tez task yaratish (NL → Claude)**
- Hozir: matn → `claude_service.process_message()` → `create_task` action → `database.create_task()` (`handlers.py:1267-1296`, `database.py:552-601`). Confidence gate: ≥90% avto, 60-90% savol, <60% rad.
- Muammo: nazoratchi maydoni yo'q; kategoriya faqat mavjuddan (yaxshi, lekin on-the-fly yaratib bo'lmaydi).
- Qulaylik: eng tez yo'l — bitta jumla.
- Optimizatsiya: nazoratchi extraction qo'shish (assignee bilan bir xil UI).
- Saqlash: SAQLASH (bu tizimning kuchli tomoni).
- O'rganish: Motion/Todoist NL parsing.

**2.4. Bosqichma-bosqich task (NewTaskFSM)**
- Hozir: title → priority → deadline → assignee → confirm (`handlers.py:672-681`).
- Muammo: nazoratchi va project tanlash yo'q; status doim "todo".
- Optimizatsiya: forma oxiriga project + nazoratchi qadamlari (ixtiyoriy, skip qilinadigan).
- Saqlash: SAQLASH.
- O'rganish: Asana guided task creation.

**2.5. Task tahrirlash (update_task)**
- Hozir: 8 maydon inline tahrir; deadline o'zgarsa `reminded_at` tozalanadi; no-op guard (`database.py:604-663`).
- Muammo: nazoratchi, parent_id, project UI'dan tahrir qilinmaydi.
- Optimizatsiya: yangi maydonlar UI'ga.
- Saqlash: SAQLASH (arxitektura solid).

**2.6. Subtasks (parent_id)**
- Hozir: rekursiv CTE, cascade delete + restore (`database.py:854-983`).
- Muammo: parent status ≠ hamma bola done; UI'da re-parenting faqat Excel orqali.
- Saqlash: SAQLASH.
- O'rganish: ClickUp subtask progress rollup.

**2.7. Reminderlar + scheduler**
- Hozir: 20+ ish, atomik claim-then-notify, smart gate'lar (`scheduler.py`). Task/meeting/custom sweeps.
- Muammo: -24h pre-deadline nudge yo'q (faqat -2h); assignee no-response eskalatsiyasi yo'q; static reminder oyna.
- Qulaylik: proaktiv, cost-optimized.
- Optimizatsiya: quyida 12-bo'limda.
- Saqlash: SAQLASH (bu eng pishiq qism).
- O'rganish: Motion — priority bo'yicha adaptiv reminder.

**2.8. Risk paneli**
- Hozir: 0-100 kompozit skor (`compute_risk_score` `handlers.py:4877-4926`), 3 bucket klassifikatsiya, inline actions.
- Muammo: per-task risk sababi saqlanmaydi; blocked/dependency detection o'chirilgan (`_proactive_dependency_check`, `scheduler.py:566`, disabled — comment `scheduler.py:183`).
- Saqlash: SAQLASH + risk_reason maydoni.
- O'rganish: monday.com risk board.

**2.9. Jamoa/Ijrochilar paneli**
- Hozir: `assignee_load_map` + `assignee_profile` (`database.py:1886-1992`), drill-down, qayta taqsimlash.
- Muammo: bosh menyuda yo'q; velocity/burndown yo'q.
- Saqlash: SAQLASH (juda kuchli).

**2.10. Marketing Hub (Projects)**
- Hozir: universal `project_items`, config-driven workflows/types (`config_marketing.py`), kanban/table/calendar/overview (`webapp_static/index.html`).
- Muammo: owner yo'q; per-task↔project link yo'q; risk_flag/executive_summary/AI tavsiya yo'q; item audit log yo'q.
- Qulaylik: yaqinda toza qayta qurilgan — over-engineering yo'q.
- Saqlash: SAQLASH (arxitektura namuna).
- O'rganish: allaqachon Trello/Asana darajasida.

**2.11. Qidiruv/Filter**
- Hozir: LIKE substring, 30 natija limit, hardcoded filter tugmalar (`database.py:2939-2992`).
- Muammo: NL parsing yo'q; scope picker query handler bilan bog'lanmagan.
- Optimizatsiya: NL query → filter mapping (Claude).
- Saqlash: SAQLASH + NL qatlam.

**2.12. Hisobotlar/Statistika**
- Hozir: `_format_stats_dashboard`, `_format_executive_report` (`handlers.py:8923-9055`), 7/30 kun.
- Muammo: eksport yo'q (CSV/PDF); per-project hisobot yo'q; trend yo'q.
- Saqlash: SAQLASH + eksport qatlam.

## 3. Asosiy muammolar

1. **Task ↔ Project uzilishi (YUQORI / BLOKER).** `tasks` jadvalida `project_id` yo'q; task loyihaga tegishli bo'la olmaydi. Data modeli ikkiga bo'lingan (`tasks` vs `project_items`). MUHIM: bu ikki jadval **turli sxemaga ega** — `project_items`da stage/fields(JSON)/order_index bor, `tasks`da recurrence_rule bor. Shuning uchun bu "additive" bo'lsa-da, "1 haftalik" emas — 12-bo'lim va roadmap'ga qarang.
2. **Discoverability (YUQORI / BLOKER).** Team, Risklar, Statistika bosh menyuda yo'q; Cockpit "back" tugmasisiz tuzoq (`handlers.py:11573-11606`).
3. **Task modelida yetishmayotgan rahbar-maydonlari (O'RTA).** Nazoratchi, izohlar, fayl/link, risk sababi, per-task AI tavsiya — hech biri yo'q.
4. **handlers.py god-file (O'RTA).** 14,957 satr, 556 funksiya (523 top-level, 264 `_`-prefiksli helper). `_format_*_card` (5 ta variant) va `_render_*_for_filter` (4 ta versiya) dublikat — test qilib bo'lmaydi. MUHIM: yangi feature'lar qo'shilgani sari fayl kattalashadi, keyin refactor qiyinlashadi — bo'lishni ertaroq boshlash strategik jihatdan foydali (18-bo'limga qarang).
5. **NL qidiruv yo'q (O'RTA).** Foydalanuvchi aniq kategoriyalarni eslashi kerak.
6. **Reminder bo'shliqlari (O'RTA).** -24h nudge yo'q; assignee no-response eskalatsiyasi yo'q; static oyna.
7. **Menyu nomuvofiqliklari (PAST).** Back tugma yorliqlari har xil; reminder filter "anystate" workaround (`handlers.py:12328`); "Yangi" seksiya over-branching.
8. **Migration qo'lda triggerlanadi (PAST, lekin ehtiyot shart).** Startup'da avto emas — unutilsa xato; backup avtomatik (`migrations.py`). Idempotence `database.init()`da PRAGMA table_info tekshiruvi bilan (masalan `database.py:425-435`) — bu yaxshi pattern, lekin reusable helper emas.
9. **Task history UI'da ko'rinmaydi (PAST).** `task_history` to'liq loglanadi, lekin botda ko'rsatilmaydi. Bu **yangi implementatsiya** (API/callback/render), shunchaki tugma qo'shish emas.
10. **Reminder FK yo'q (PAST, hozirda mitigatsiya qilingan).** `reminders.task_id` FK yo'q (SQLite cheklovi), lekin `delete_task()` app-layer'da tozalaydi (`database.py:883` — `DELETE FROM reminders WHERE task_id IN (...)`). Orfan xavfi hozircha yo'q; faqat yangi delete yo'llarida shu patternni saqlash kerak.

## 4. Saqlab qolinadigan funksiyalar

Bularni O'ZGARTIRMANG — tizimning kuchli negizi:

- **NL task parsing + confidence gate** (`claude_service`, `handlers.py:1267-1296`) — eng tez yo'l.
- **task_history audit trail** (`database.py:142-150`) — to'liq, tez, alohida jadval.
- **Subtask rekursiya + cascade + undo** (`database.py:854-983`).
- **Atomik reminder claim-then-notify** (`mark_task_reminded`, `scheduler.py`) — restart/multi-instance xavfsiz.
- **20+ smart scheduler ish** (morning/evening/afternoon/retrospective, smart gate'lar) — cost-optimized.
- **Risk skoring bitta aggregat query bilan** (`risk_score_counts` `database.py:1805-1836`).
- **Jamoa yuklamasi + profil** (`assignee_load_map`, `assignee_profile`).
- **Marketing Hub arxitekturasi** — universal `project_items`, config-driven (`config_marketing.py`), kanban/table/calendar/overview.
- **Recurrence** (daily/weekdays/weekly/monthly/quarterly/yearly) — idempotent dedup.
- **Indekslar** (12+) + WAL + busy_timeout (`database.py:393-400`).
- **Backup + idempotent migration** (`migrations.py`).
- **delete_task'dagi orphan reminder cleanup** (`database.py:883`) — subtree bo'yicha ham reminderlarni tozalaydi. Bu allaqachon to'g'ri; SAQLASH.

## 5. Optimallashtiriladigan funksiyalar

- **Bosh menyu:** 2×4 → 2×5, Team/Risklar/Stats qo'shish; BTN_STATS'ni bosh menyuda faollashtirish.
- **Cockpit:** har drill-down panelga "⬅️ Cockpit'ga" tugma.
- **Task modeli:** `project_id`, `observer`, `risk_reason`, `ai_note` maydonlari; `task_comments`, `task_attachments` jadvallari.
- **NL qidiruv:** Claude → filter mapping qatlami (mavjud LIKE ustiga).
- **Reminderlar:** -24h nudge sweep; priority bo'yicha adaptiv oyna (P0→4h, P1→2.5h); assignee_assigned_at + eskalatsiya.
- **Blocked/dependency:** `_proactive_dependency_check`'ni 08:00 ertalabki ishga qayta ulash (02:30 emas).
- **Hisobot eksport:** CSV/Excel (mavjud stats ustiga).
- **Task history UI:** "📜 Tarix" tugma + render (`get_task_history` mavjud, lekin UI qatlam yangi).
- **Search scope FSM:** scope picker'ni query handler bilan bog'lash.
- **handlers.py:** submodullarga bo'lish (formatterlar/filterlar) — dublikat `_format_*_card` (5) va `_render_*_for_filter` (4) umumiy helperga.

## 6. Yangi project/task management modeli

Mavjudga bog'lab — qaysi maydon bor/yo'q. Prinsip: mavjud jadvallarga qo'shish (guarded ALTER — PRAGMA table_info tekshiruvi bilan), buzmaslik.

**TASK obyekti** (`tasks` jadvali, `database.py:35-52`):

| Maydon | Holat | Amal |
|--------|-------|------|
| title | ✅ bor | — |
| description | ✅ bor | — |
| deadline | ✅ bor (ISO, TZ-aware) | — |
| priority (P0-P3) | ✅ bor | — |
| status (5 qiymat) | ✅ bor | — |
| assignee (ijrochi) | ✅ bor (controlled list) | — |
| tags, category | ✅ bor | — |
| recurrence_* | ✅ bor | — |
| created_at/updated_at | ✅ bor | — |
| tarix | ✅ bor (`task_history`) | UI'da ko'rsatish (yangi qatlam) |
| reminder | ✅ bor (`reminders.task_id`, 1:N) | — |
| subtasks | ✅ bor (`parent_id`) | — |
| **project_id** | ❌ yo'q | **QO'SHISH** (nullable TEXT + index). Diqqat: sxema farqlari sabab endpoint/UI ishi katta (12-bo'lim) |
| **observer (nazoratchi)** | ❌ yo'q | **QO'SHISH** (TEXT, assignee picker UI) |
| **risk_reason / risk_level** | ❌ yo'q | **QO'SHISH** (ixtiyoriy) |
| **ai_note (per-task AI tavsiya)** | ❌ yo'q | **QO'SHISH** (proaktiv checkdan to'ldiriladi) |
| **izohlar (comments)** | ❌ yo'q | **`task_comments`** (id, task_id, author, text, created_at) |
| **fayl/link** | ❌ yo'q (link description'da) | **`task_attachments`** (id, task_id, url, title, type) |

**PROJECT obyekti** (`projects` jadvali, `database.py:311-379`):

| Maydon | Holat | Amal |
|--------|-------|------|
| name, description, color, icon | ✅ bor | — |
| status (active/archived) | ✅ bor | — |
| type, workflow (JSON), default_view | ✅ bor | — |
| start_date, end_date | ✅ bor | — |
| team (comma-delimited) | ✅ bor (partial) | — |
| updated_at | ✅ bor | — |
| progress%, task_count | ✅ read-time hisob | — |
| **owner** | ❌ yo'q | QO'SHISH (ixtiyoriy, single-user'da past prioritet) |
| **risk_flag** | ❌ yo'q | QO'SHISH (yoki item'lardan hisoblash) |
| **executive_summary / ai_note** | ❌ yo'q | QO'SHISH (Faza B/C) |
| **next_deadline** | 🟡 item'lardan hisoblanadi | read-time hisob qo'shish |

> Diqqat (dependency ordering): per-project stats endpoint'ni qurishdan OLDIN yuqoridagi project ustunlarini (owner/risk_flag/executive_summary) qo'shib bo'lish kerak — aks holda endpoint keyin qayta yozilishi kerak. Roadmap Faza B'da bu tartib hisobga olingan.

**PROJECT_ITEM** (`project_items`, universal): to'liq bor — type, status, priority, assignee, category, stage, sanalar, parent_id, fields (JSON). Item audit log yo'q (task_history'ga o'xshash `project_item_history` — ixtiyoriy).

> Sxema nomuvofiqligi eslatmasi: `project_items` statuslari config-driven (`config_marketing.WORKFLOWS`), `tasks` statuslari hardcoded CHECK. Task↔project ulanishida bu farq murakkablik keltiradi. Yechim Faza C dizayn hujjatiga qoldiriladi (task rigid qolsinmi yoki config-driven bo'lsinmi) — hozir hal qilinmaydi.

## 7. Yangi menyu strukturasi

Mavjud BTN_* larni saqlab, refactor. Prinsip: SAQLASH + qo'shish.

**Bosh menyu — 2×4 dan 2×5 ga:**
```
Row 1: 🎛 Boshqaruv paneli   | 📅 Bugun
Row 2: 📌 Vazifalar          | ⏰ Eslatmalar
Row 3: 🤝 Uchrashuvlar       | 👥 Ijrochilar   ← YANGI (BTN_TEAM)
Row 4: 🚨 Risklar ← YANGI    | 📊 Statistika ← YANGI (BTN_STATS)
Row 5: ➕ Yangi              | 🔍 Qidiruv
```
Sozlamalar → Cockpit yoki /settings orqali (10 tugma manageable, lekin 12 ko'p; Sozlamalarni siljitish mumkin).

**Cockpit refactor:** har drill-down (Team/Risklar/Stats) panelga "⬅️ Cockpit'ga" inline tugma (`cb_cockpit_*`).

**Back tugma standarti:**
- Reply-seksiya chiqishi: "⬅️ Asosiy menyu"
- Inline sub-panel: "⬅️ Orqaga"
- Ro'yxatga qaytish: "⬅️ Ro'yxatga"

**"Yangi" seksiya soddalashtirish:** 6 tugmadan (task/meeting/reminder/note/voice/polish) → 4 (task/meeting/reminder/note); voice/polish → `/voice`, `/polish` komandalar. Task har doim Tezkor/Forma so'rasin (barcha kirish nuqtalarida bir xil).

**Reminder "anystate" workaround** (`handlers.py:12328`) — seksiya chegaralarini kuchaytirib olib tashlash (foydalanuvchi avval Eslatmalar seksiyasiga kirsin).

## 8. Kanban modeli

**Hozir ALLAQACHON BOR** (`webapp_static/index.html:1100-1168`, `renderProjKanban`): config-driven ustunlar (workflow'dan), drag-drop, ustun editori (qo'shish/tartib/o'chirish/tahrir), item kartalari (type icon, title, assignee, sana). `POST /api/items/{id}/move` status+order_index. Bu Marketing Hub'ning kuchli tomoni.

**Yetishmayotgan (ixtiyoriy qo'shimchalar):**
- Subtask/dependency vizualizatsiyasi kartada (parent_id bor, UI yo'q).
- SLA/deadline ogohlantirish kartada (rangli badge — deadline o'tgan/yaqin).
- **Task kanban:** hozir kanban faqat `project_items` uchun. `tasks`ga `project_id` qo'shilgach, project ichidagi tasklar ham kanbanda ko'rinishi mumkin — LEKIN bu duplikatsiya yoki merged view talab qiladi (index.html:1100 hozir faqat project_items render qiladi). Alohida global task kanban (todo/in_progress/blocked/done) ham variant.

**Tavsiya:** Marketing Hub kanbanni SAQLASH. Task kanban — faqat `project_id` ulangandan keyin, additive, mixed-items endpoint bilan. Yangi kanban engine QURMANG.

## 9. Calendar modeli

**Hozir BOR** (`webapp_static/index.html:1284-1313`, `renderProjCal`): oylik grid, kunlar bo'yicha item, kategoriya filter chip'lari, kun-detali modal, done/in-progress vizual farq. iCloud sync ham bor (`scheduler.py` icloud_sync/retry/push_backfill).

**Yetishmayotgan (ixtiyoriy):**
- Hafta/kun ko'rinishi (faqat oylik bor).
- Meeting integratsiyasi calendar view'da (meetings alohida).
- Cross-project birlashtirilgan calendar (global).

**Tavsiya:** oylik calendar'ni SAQLASH. Meeting + task deadline'larni bitta calendar'da birlashtirish (`meetings.datetime_start` + `tasks.deadline` + `project_items.primary_date`) — Faza C. Yangi calendar engine QURMANG.

## 10. Dashboard modeli

**Global dashboard BOR** (`webapp.py:601-632`): progress %, radar (total/overdue/blocked/unassigned), bugun (meetings/tasks/next), priority top-5, jamoa (overloaded/stale). `insights` (7-kun bar, kategoriya donut). Cockpit ham (`handlers.py:4500+`).

**Per-project dashboard 🟡 qisman** (`renderProjOverview` `index.html:1070-1097`): total/completed/completion %, status donut, type h-bar, assignee load. Yetishmaydi: next deadline, risk indikator, executive summary, AI tavsiya.

**Executive summary namunasi (Telegram, matn):**
```
📄 EXECUTIVE XULOSA · 07.07.2026
━━━━━━━━━━━━━━━━━━━━
Risk score: 🟠 68/100 — Yuqori risk
✅ Yopildi (7 kun): 23 · Bajarilish: 74%
📌 Aktiv: 31 · ⌛ Muddati o'tgan: 4

🚨 DIQQAT (top-3)
  1. Telefilm script — 2 kun kechikdi (Murodjon)
  2. Byudjet hisoboti — bugun, ijrochisiz
  3. Tender hujjatlari — ertaga, P0

📋 IJROCHILAR
  • Murodjon: 8 aktiv (2 o'tgan) — ⚠️ yuqori
  • Aziza: 4 aktiv — muvozanatli

🤖 TAVSIYA
  Avval 4 ta muddati o'tganni yoping; Murodjonni
  yengillashtiring; byudjet hisobotiga ijrochi
  tayinlang.
```

**Tavsiya:** global dashboard SAQLASH. `GET /api/projects/{id}/stats` endpoint qo'shish (per-project KPI) — LEKIN project ustunlari (owner/risk_flag/executive_summary) qo'shilgandan KEYIN, ularni compute qilmasdan o'qish uchun. Overview'ga next_deadline + risk badge + AI xulosa qatori.

## 11. Risk management modeli

**Hozir BOR va kuchli** (`compute_risk_score` `handlers.py:4877-4926`, `_classify_risks` `handlers.py:9824-9899`): 0-100 kompozit skor, High/Medium/Low bucket, inline actions (assign/remind/deadline). 6/10 shart aniq mapped.

**Yaxshilash (proporsional):**
1. **Per-task `risk_reason` + `risk_level`** maydonlari — hozir sabab hisoblanadi lekin saqlanmaydi. Qo'shilsa hisobotlarda tarixiylashadi.
2. **Blocked/dependency detection qayta ulash** — `_proactive_dependency_check` kodi tayyor (`scheduler.py:566`), o'chirilgan (`scheduler.py:183` comment, 02:30 uyg'otish sababli). 08:00 ertalabki ishga ulang (sozlanadigan vaqt bilan).
3. **No-deadline aktivni risk-flag** qilish (hozir faqat filter).

**Yangi tavsiya emas:** risk board qurmang — mavjud panel yetarli. Faqat sabab saqlash + dependency re-enable.

## 12. Reminder modeli

**Hozir BOR:** task sweep (-2h config), meeting sweep (create-time DateTrigger + rehydrate), custom sweep (1-min), recurrence, snooze (15m/1h/1d). Atomik claim.

**Smart triggers — qo'shilishi kerak:**
- **-24h pre-deadline nudge** — `_early_deadline_sweep()` (08:00, deadline [now+24h, now+25h]). REALISTIK effort: index + scheduler registration + dedup + test = 2-4 soat (30 min emas). `mark_task_reminded` pattern qayta ishlatiladi.
- **Assignee no-response eskalatsiyasi** — `assignee_assigned_at` ustuni + sweep: 2+ kun "todo" bo'lsa eskalatsiya. Effort o'rta (migration kerak). Faza C.
- **P1 not-started** — morning brief'ga "🟡 Boshlanmagan (P1): N" qatori.
- **Unassigned + 48h** — morning brief highlight.

**Fleksibl sozlamalar:**
- **Priority bo'yicha adaptiv oyna:** P0→4h, P1→2.5h, P2→2h, P3→1h (hozir static `task_reminder_hours`, `scheduler.py:966`).
- **stale_delegation age** ni sozlanadigan qilish (hozir hardcoded 3 kun, `scheduler.py:704-727`). REALISTIK effort: settings row + read + validation = 1-2 soat (5 min emas).
- Quiet hours, notification on/off — ALLAQACHON bor (`scheduler.py` `_send`).

**SAQLASH:** butun scheduler arxitekturasi. Faqat yangi sweep'lar qo'shing.

## 13. Ijrochilar paneli

**Hozir BOR va to'liq** (`_render_team_panel` `handlers.py:9603-9625`, `assignee_load_map` + `assignee_profile`): per-assignee active/urgent/important/overdue/next_deadline; yuklama band (🔴/🟠/🟢/⚪); drill-down profil (completion %, avg_closing_hours, top-10 task); qayta taqsimlash subflow; heuristik tavsiya.

**Yaxshilash:**
- Bosh menyuga qo'yish (7-bo'lim).
- **Nazoratchi (observer) integratsiyasi** — task'ga observer qo'shilgach, panelda "nazorat qilayotgan" vazifalar ham ko'rinishi mumkin.
- Haftalik velocity/burndown (ixtiyoriy, Faza C) — `avg_closing_hours` bor, trend yo'q.

**SAQLASH:** panel arxitekturasi kuchli. Yangi analytics engine QURMANG.

## 14. Qidiruv va filter modeli

**Hozir BOR:** cross-entity LIKE (`search_all` `database.py:2939-2992`), 30 natija limit, hardcoded filter tugmalar (tasks/meetings/reminders/notes FSM state'lari).

**Muammolar:** NL parsing yo'q; search scope picker query handler bilan bog'lanmagan (`handle_global_search` `handlers.py:10129-10240`; `search_section_reply_keyboard` `handlers.py:491-503`).

**Yaxshilash (proporsional):**
1. **NL qatlam** — Claude query interpretatsiya: "o'tgan hafta shoshilinch" → filter(priority=P0, deadline range). Mavjud LIKE'ni almashtirmaydi, ustiga qo'yiladi. MUHIM: grammar spec (QUERY_GRAMMAR.md, 20+ misol), confidence gate (<70% → LIKE fallback), 10+ unit test, va per-query LLM cost o'lchovi kerak. Agar cost yuqori bo'lsa qayta ko'rib chiqing.
2. **SearchScopeFSM** — scope tanlash FSM state o'rnatsin, query handler hurmat qilsin (feedback bilan).
3. **`tasks(category)` index** qo'shish (hozir full scan).

**SAQLASH:** LIKE backend va filter tugmalar.

## 15. Hisobotlar modeli

**Hozir BOR:** `_format_stats_dashboard` (KPI, deadline bosim, priority load, delegation, meeting, bot audit/cost, risk) `handlers.py:8923-8991`; `_format_executive_report` (risk score, KPI, top-3 overdue, delegation, heuristik tavsiya) `handlers.py:8994-9055`; 7/30 kun (`cmd_report`).

**Yetishmayotgan:**
- **Eksport (CSV/Excel)** — hozir faqat Telegram matn. Faza C: `GET /api/reports/export?period=30d&format=csv`. CSV oson, Excel kutubxona talab qiladi (3-4 soat).
- **Per-project hisobot** — global bor, project-scoped yo'q. `/api/projects/{id}/stats` bilan bog'lash.
- **Trend tahlili** — haftalar bo'yicha completion rate (weekly_retrospective bor, trend saqlanmaydi).
- **Team productivity** — % on-time, per-assignee velocity.

**SAQLASH:** stats/executive report formatlar. Ustiga eksport + per-project qatlam.

## 16. Texnik refactor tavsiyalari

Prinsip: DELETE EMAS — bo'lish/ulash.

1. **Task ↔ Project ulash (YUQORI).** `tasks.project_id` (nullable TEXT) + index. Guarded ALTER (PRAGMA table_info tekshiruvi bilan, `database.py:425-435` patternida). Task list/search project bo'yicha filter. Buzmaydi (nullable). LEKIN sxema farqlari + yangi endpoint (`/api/projects/{id}/mixed-items`) + kanban kengaytmasi sabab REALISTIK effort ~3-4 hafta, "1 hafta" emas. Kodlashdan oldin DESIGN.md'da qaror (separate vs merge) yozing.
2. **handlers.py bo'lish (O'RTA, tadrijiy — ERTAROQ boshlang).** 14,957 satr → `handlers/tasks.py`, `handlers/meetings.py`, `handlers/filters.py`, `handlers/formatters.py`. `_format_*_card` (5 variant) va `_render_*_for_filter` (4 versiya) umumiy helperga (format_card_generic). BIRDANIGA emas — modul-modul, har biri testdan keyin. Strategik sabab: feature qo'shilgani sari fayl kattalashadi va refactor qiyinlashadi, shuning uchun kamida formatterlar/dedup ishini Faza A/B'da parallel boshlash foydali.
3. **Yangi maydonlar/jadvallar** (6-bo'lim): observer, risk_reason, ai_note ustunlar; task_comments, task_attachments jadvallar — barchasi guarded ALTER / CREATE IF NOT EXISTS.
4. **NL qidiruv qatlami** — claude_service'da yangi funksiya, LIKE fallback bilan.
5. **Orphan reminder cleanup — allaqachon bor** (`database.py:883`). Yangi delete/complete yo'llarida shu patternni saqlash kifoya; qayta implementatsiya SHART EMAS.
6. **Search scope FSM** — SearchScopeFSM state.
7. **Logging** — correlation ID, handler'larda silent failure'larni logga yozish (hozir user'ga message, logga yo'q).
8. **Sweep transaction chegarasi** — claim + send'ni bitta tx'ga o'rash (hozir claim bo'lib, send'gacha exception bo'lsa reminder yo'qoladi).
9. **Test infratuzilmasi (O'RTA, YANGI qo'shildi).** Kodbazada test yo'q. Faza B/C feature'lari test qarzini oshiradi. Minimal: `tests/` + pytest + asyncio fixtures, kritik yo'llar uchun 10 integration test (create_task, update_task field kombinatsiyalari, complete_task+recurrence, delete_task+reminders). Observer/comments qo'shilganda regression tutish uchun.

## 17. Database/migration bo'yicha ehtiyot choralari

**BACKUP MAJBURIY.** `migrations.py` da `backup_db()` bor (shutil.copy2, timestamped `.bak-YYYYMMDD-HHMMSS`). Har o'zgarishdan OLDIN:

1. **Har migration `backup_db()` chaqirsin** — hozirgi pattern (`migrations.py`), davom ettiring.
2. **Idempotent — allaqachon qisman bor.** `database.init()` ALTER'lari PRAGMA table_info tekshiruvi bilan qo'riqlangan (`database.py:416-490`) — "duplicate column" xatosi hozircha yo'q. YAXSHILASH: bu ad-hoc tekshiruvlarni bitta reusable helperga (`_safe_alter_add_column(db, table, col, defn)`) yig'ish — kelajakdagi ustunlar uchun bir xil, xatosiz. CREATE TABLE/INDEX — IF NOT EXISTS.
3. **Manual trigger saqlansin** — startup avto emas (`python migrations.py` deploy'dan oldin). Bu XATO xavfini kamaytiradi. Deploy hujjatida aniq yozing. Regressiyani oldini olish uchun: `database.init()`ga sxema o'zgarishlarini qo'shmaslik haqida aniq comment + (ixtiyoriy) init()'ni toza DB'da idempotent tekshiruvchi CI testi.
4. **FK yo'qligi ataylab** (SQLite ALTER cheklovi). Orphan cleanup app-layer'da ALLAQACHON bor (`database.py:883`) — yangi delete yo'llarida saqlang.
5. **Yangi ustun DEFAULT/NULL bilan** — mavjud satrlar buzilmasin (`project_id` NULL default, `observer` NULL, `risk_level` NULL).
6. **Test DB'da avval sinang** — production `.bak` bilan restore imkoniyati. Post-init `PRAGMA quick_check` — 'ok' bo'lmasa startup'ni to'xtatib backup'ga qaytaring.
7. **content_posts → project_items migratsiyasi idempotent** (`migrations.py:48-107`) — namuna sifatida yangi migratsiyalarga.

Xulosa: yangi maydonlar buzmaydi (nullable + guarded ALTER). Backup avtomatik (migrations.py). Faqat manual trigger'ni unutmaslik va `init()` ALTER'larini backup bilan o'rash.

## 18. Implementation roadmap

> Effort baholari konservativ. "Quick win"lar ko'pincha kutilganidan uzoqroq (index + scheduler reg + dedup + test). Har ishni haqiqiy dev bilan time-box qiling; keyingi fazalar uchun historik velocity ishlating.

**A — 1-2 hafta (tez g'alaba, past risk):**
- Bosh menyuga Team/Risklar/Stats (2×5) + BTN_STATS bosh menyuda faollashtirish.
- Cockpit drill-down'larga "⬅️ Cockpit'ga" tugma.
- Back tugma yorliqlari standarti.
- `_early_deadline_sweep` (-24h nudge) — 2-4 soat (test bilan).
- `_proactive_dependency_check` 08:00 ga qayta ulash (sozlanadigan vaqt) — 1 soat.
- Morning brief: "🟡 Boshlanmagan (P1)" + "Ijrochisiz+48h" qatorlari.
- stale_delegation age sozlanadigan — 1-2 soat.
- `tasks(category)` index.
- **Test infratuzilmasi bootstrap** (`tests/` + pytest + 10 integration test kritik yo'llar uchun).
- **handlers formatterlar dedup boshlash** (`_format_*_card` → shared helper) — god-file bloatini oldini olish uchun ertaroq.

**B — 3-6 hafta (strukturaviy, o'rta risk):**
- **DESIGN.md: task↔project qarori** (separate FK vs merge) — kodlashdan oldin.
- **`tasks.project_id`** qo'shish + `/api/projects/{id}/mixed-items` endpoint + task↔project filter/list (realistik ~3-4 hafta ishning o'zi).
- **Project sxema finalize** — owner/risk_flag/executive_summary/ai_note ustunlar (stats endpoint'dan OLDIN).
- **observer (nazoratchi)** ustuni + assignee picker UI + panel integratsiya.
- **risk_reason / risk_level / ai_note** ustunlar (task).
- **task_comments** jadvali + UI (frontend scope alohida baholansin — FRONTEND.md).
- **NL qidiruv qatlami** (Claude + LIKE fallback, QUERY_GRAMMAR.md + testlar + cost o'lchovi) + SearchScopeFSM.
- **Per-project stats** endpoint (yangi sxemani o'qib, compute qilmasdan) + overview'ga next_deadline/risk badge.
- **Priority-adaptiv reminder oyna.**

**C — 2-3 oy (kengaytma, katta effort):**
- **task_attachments** (fayl/link) + frontend.
- **Assignee no-response eskalatsiyasi** (`assignee_assigned_at` + sweep).
- **Task history UI** (📜 Tarix — YANGI API endpoint + callback + render, shunchaki tugma emas).
- **Hisobot eksport** (CSV/Excel).
- **handlers.py to'liq submodullarga bo'lish** (tadrijiy, test bilan).
- **Project velocity/burndown** metrikalar.
- **Cross-project birlashgan calendar** (meetings+tasks+items).
- **Project executive_summary + AI tavsiya** endpoint.
- **Task status flexibility qarori** (rigid vs config-driven — dizayn hujjati).
- Trend tahlili (haftalik completion rate saqlash).
- **Sweep scalability audit** (10k+ task'da sweep latency o'lchovi; kerak bo'lsa index/cache).

## 19. Eng muhim 10 ta prioritet ish

1. **`tasks.project_id` qo'shish** — data modeli uzilishini yopadi (eng katta strukturaviy g'alaba). Nullable + index; LEKIN sxema farqi sabab endpoint/UI ishi katta (Faza B, ~3-4 hafta).
2. **Bosh menyuga Team/Risklar/Stats** — 3 katta funksiya yashiringan (`handlers.py:112-140`).
3. **observer (nazoratchi) maydoni** — rahbar uchun asosiy talab, hozir umuman yo'q.
4. **-24h pre-deadline reminder** — reminder bo'shlig'i, past-o'rta effort, katta qiymat.
5. **Cockpit "back" tugmasi** — "modal tuzoq"ni tuzatadi.
6. **task_comments** — izoh/muhokama, hozir umuman yo'q.
7. **NL qidiruv qatlami** — foydalanuvchi aniq kategoriyani eslamasligi kerak (grammar + fallback + cost gate bilan).
8. **risk_reason saqlash + dependency re-enable** — risk tizimini to'ldiradi (kod tayyor `scheduler.py:566`).
9. **Test infratuzilmasi + formatter dedup** — regressiya tutish va god-file bloatini oldini olish (ertaroq).
10. **Per-project stats endpoint** — dashboard bo'shlig'ini yopadi (project sxema finalize'dan keyin).

## 20. Yakuniy professional tavsiya

Yordamchi — havaskor prototip emas, balki **pishiq, ishlaydigan tizim**: atomik reminderlar, smart scheduler, config-driven Marketing Hub, risk skoring, jamoa analitikasi. Rahbarlar so'raydigan aksariyat narsa (kanban, calendar, table, dashboard, project types/workflows) ALLAQACHON mavjud va toza arxitekturada.

Shuning uchun to'g'ri strategiya — **qayta qurish emas, ulash va sayqallash**. Uchta strukturaviy uzilish (task↔project bog'lanishi, discoverability, task modelining rahbar-maydonlari) qiymatning 80%ini beradi. Ularning ko'pi mavjud jadvallarga nullable ustun qo'shish yo'li bilan hal bo'ladi — buzmasdan — LEKIN task↔project ulash sxema farqlari sabab yengil emas: kodlashdan oldin DESIGN.md'da qaror (separate FK vs merge) va realistik ~3-4 haftalik reja kerak.

Qat'iy ogohlantirish: generic Notion/Jira klonini qo'lda qurishga QAYTMANG (allaqachon sinab, rad etilgan). Har qo'shimcha proporsional bo'lsin — rahbar uchun 1-2 bosishda natija. handlers.py'ni tadrijiy bo'ling va formatter dedup'ni ertaroq boshlang (feature qo'shilgani sari refactor qiyinlashadi). Test infratuzilmasini Faza A'da o'rnating (hozir test yo'q). Har migratsiyadan oldin `backup_db()` (majburiy), guarded ALTER (PRAGMA tekshiruvi — allaqachon qisman bor, reusable helperga yig'ing) bilan.

Faza A'ni darhol boshlang (past risk, tez ko'rinadigan natija), so'ng Faza B strukturaviy ulanishlarni amalga oshiring. Tizim allaqachon kuchli — uni yaxshilash, buzmaslik kerak.

---

## Ilova A — Adversarial findings bo'yicha qarorlar (audit izi)

**Qabul qilingan (report'ga kiritilgan):**
- Task↔Project uzilishi (BLOKER) — §3, §6, §16, §18, §19. Effort real ~3-4 haftaga tuzatildi.
- Menyu discoverability (BLOKER) — §7, §19.
- Yetishmayotgan task maydonlari (observer/comments/attachments/risk_reason/ai_note) — §6, §18.
- Scheduler bo'shliqlari (-24h, dependency re-enable, escalation) — §12; effort baholari realistlashtirildi.
- NL qidiruv (grammar/fallback/cost gate qo'shildi) — §14.
- handlers.py god-file + formatter dedup ertaroq — §16, §18.
- Static reminder oyna → priority-adaptiv — §12.
- Per-project stats (sxema finalize'dan keyin) — §10, §18.
- Report eksport — §15, §18.
- Stale delegation sozlanadigan, search scope FSM, Cockpit back — §12, §14, §7.
- Migration safety: reusable `_safe_alter` helper, init() backup wrap, quick_check, regressiya CI — §16, §17.
- Test infratuzilmasi (kodbazada test yo'q) — §16, §18.
- Sxema flexibility nomuvofiqligi (config-driven vs hardcoded status) — §6, §18 (Faza C dizayn).
- Frontend scope alohida baholanadi (FRONTEND.md) — §18.
- Sweep scalability audit — §18.

**Aniqlik tuzatishlari (yolg'on/noaniq raqamlar):**
- handlers.py "590 funksiya" → **556** (523 top-level), "264 helper" saqlandi (to'g'ri). — §3, §4.
- "`_format_*_card` 8+" → **5**; "`_render_*_for_filter` 4+" → **4** (aniq). — §4, §5, §16.
- Task history UI "quick win / tugma qo'shish" → **yangi implementatsiya** (API+callback+render), Faza A'dan **Faza C**'ga ko'chirildi. — §3, §5, §18.

**RAD ETILGAN (kod bilan tekshirilgan, noto'g'ri):**
- "delete_task orphan reminderlarni tozalamaydi" (findings #14, #20) — **NOTO'G'RI**. `database.py:883` allaqachon `DELETE FROM reminders WHERE task_id IN (...)` bajaradi, subtree bo'yicha ham. Qayta implementatsiya shart emas; §4, §16.5'da mavjud xatti-harakat sifatida qayd etildi.
- "database.py ALTER'lari bare/guardsiz, duplicate column crash" (finding #18) — **QISMAN NOTO'G'RI**. ALTER'lar PRAGMA table_info tekshiruvi bilan qo'riqlangan (`database.py:416-490`), crash hozircha yo'q. Faqat reusable helperga yig'ish tavsiyasi qoldirildi (over-engineering emas, sanitatsiya). §17.2.
