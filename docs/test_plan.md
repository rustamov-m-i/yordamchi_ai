# Yordamchi — To'liq test-prompt (QA tekshiruv ro'yxati)

**Maqsad:** deploy'dan keyin barcha funksiyalar ishlashini tekshirish. Har bandda: **amal → kutilgan natija**. Belgilab boring: ✅ / ❌ (izoh bilan).

**Foydalanish:** (a) qo'lda o'zingiz bosib chiqing, yoki (b) tester/AI'ga bering. Har band mustaqil.

**Oldindan:** serverda `git pull` + (bir martalik) `python migrations.py` + bot restart bajarilgan bo'lsin. Ehtiyot uchun DB backup borligini tekshiring (`data/yordamchi.db.bak.*`).

---

## A. TELEGRAM BOT (chat)

**A1. Bosh menyu.** `/start` yoki botni oching → 2 ustunli menyu chiqadi. **Kutilgan:** Boshqaruv paneli · Bugun · Vazifalar · Uchrashuvlar · **Ijrochilar** · **Risklar** · **Statistika** · Eslatmalar · Yangi · Qidiruv · Sozlamalar (Ijrochilar/Risklar/Statistika endi bosh menyuda).

**A2. Tez vazifa (tabiiy til).** Yozing: *"Javoxir Tasnif shartnomasini 15-iyulgacha yakunlasin, muhim"*. **Kutilgan:** bot tasdiq kartasi — sarlavha ajratilgan, ijrochi=Javoxir, muddat=15-iyul, ustuvorlik=Muhim. Tasdiqlang → vazifa yaratiladi.

**A3. To'liq vazifa.** `/new` yoki "➕ Yangi". **Kutilgan:** bosqichma-bosqich so'raydi (sarlavha → ustuvorlik → muddat → ijrochi → tasdiq). Yaratiladi.

**A4. Tahrir / holat / sub-vazifa.** Vazifani oching → holatini o'zgartiring (Jarayonda), ustuvorlikni o'zgartiring, sub-vazifa qo'shing. **Kutilgan:** o'zgarishlar saqlanadi; ota vazifada "1/2" hisob ko'rinadi.

**A5. Bugun.** "📅 Bugun" yoki `/today`. **Kutilgan:** bugungi vazifa+uchrashuvlar ro'yxati.

**A6. Vazifalar filtrlari.** "📌 Vazifalar" → Aktiv / Bugun / O'tgan / Muhim / Bajarilgan tugmalari. **Kutilgan:** har filtr to'g'ri ro'yxatni ko'rsatadi.

**A7. Eslatmalar.** "⏰ Eslatmalar". **Kutilgan:** faol eslatmalar; qo'shish/o'chirish ishlaydi.

**A8. Boshqaruv paneli.** "🎛 Boshqaruv paneli" yoki `/cockpit`. **Kutilgan:** kunlik snapshot (top vazifalar, risklar, tavsiya). Drill-down (Ijrochilar/Risklar/Stats) → "⬅️ Asosiy menyu" bilan qaytish.

**A9. Ijrochilar.** "👥 Ijrochilar". **Kutilgan:** har ijrochi bo'yicha aktiv/kechikkan/yuklama; ijrochisiz vazifalar; qayta taqsimlash.

**A10. Risklar.** "🚨 Risklar". **Kutilgan:** risk skori bo'yicha vazifalar (kechikkan/ijrochisiz/turg'un), tezkor amal tugmalari.

**A11. Statistika + hisobot.** "📊 Statistika" → Bugun/7 kun/30 kun + "📄 Hisobot". **Kutilgan:** ko'rsatkichlar + executive hisobot matni.

**A12. Qidiruv.** "🔍 Qidiruv" → *"Javoxirning vazifalari"*. **Kutilgan:** mos vazifalar chiqadi.

**A13. Uchrashuvlar.** "🤝 Uchrashuvlar" yoki `/meetings`. **Kutilgan:** ro'yxat; yaratish/tahrir; bayonnoma.

**A14. Fayl → AI vazifa (bot).** Botga fayl/rasm yuboring (masalan vazifalar ro'yxati bo'lgan .xlsx/.pdf/rasm). **Kutilgan:** "🔍 Hujjatni o'qiyapman…" → AI tahlil + "✅ Vazifa yarat" tugmasi bilan taklif; tasdiqlansa yaratiladi.

**A15. Ovoz.** Ovozli xabar yuboring: *"Ertaga Dilnozaga bannerlarni tayyorlashni topshir"*. **Kutilgan:** matnga o'giriladi → vazifa taklifi.

**A16. Takroriylik.** Takroriy vazifa yarating (masalan "har kuni") → bajaring. **Kutilgan:** yangi nusxa avtomatik tug'iladi (dublikatsiz).

---

## B. MINI-APP (WebApp)

**B1. Ochish.** Botdan mini-app'ni oching. **Kutilgan:** Bosh sahifa (dashboard) — "Diqqat markazida" radar, "Bugungi kun", "Natijalar". Pastda nav: Bosh sahifa · Vazifalar · Uchrashuvlar · Profil.

**B2. Vazifalar.** "Vazifalar" tab → filtrlar (Faol/Kechikkan/Bajarilgan/Hammasi). **Kutilgan:** vazifa kartalari; kartani bosish → tafsilot; "Bajarildi" belgilanadi.

**B3. 🆕 AI import (Vazifalar).** Vazifalar header'da **✨** → fayl tanlang (xlsx/csv/pdf/txt/rasm — masalan tartibsiz vazifalar ro'yxati). **Kutilgan:** "🔍 AI faylni o'qiyapti…" → **preview** oynasi: AI topgan vazifalar (sarlavha/mas'ul/muddat/ustuvorlik, har biri belgilangan). Keraksizni belgidan chiqaring → **"Joylashtirish"** → vazifalar yaratiladi, ro'yxatda ko'rinadi.

**B4. Excel import + export.** Header'da **⬆** (aniq ustunli .xlsx/.csv import) va **⬇** (Excel eksport). **Kutilgan:** eksport faylini yuklab olib, uni qayta ⬆ orqali import qilsa — vazifalar yangilanadi (round-trip).

**B5. Loyihalar.** Profil → **Loyihalar**. **Kutilgan:** loyihalar ro'yxati (nom + element soni + progress).

**B6. Loyiha yaratish (shablon/tur).** "Loyiha qo'shish" → **Shablon** ochiladi. **Kutilgan:** ro'yxatda — SMM Content Calendar, 360 Marketing Campaign, **Yangi mahsulot kampaniyasi**, **Mavsumiy aksiya**, **Video-kontent ishlab chiqarish**, **Tender/pudratchi jarayoni**, PR/Branding/Event/Media/Roadmap/Influencer/Product Launch, **"Boshqa — o'zim sozlayman"**. Shablon tanlansa tur/ko'rinish/rang avto to'ladi. Yaratiladi.

**B7. Overview (loyiha).** Loyihani oching → Overview. **Kutilgan:** KPI (Jami/Yakunlangan/Bajarilish%), Holatlar doirasi (loyiha workflow'i bo'yicha), Turlar, Mas'ullar yuklamasi, va **"Bog'langan vazifalar: N (M bajarilgan)"** bloki.

**B8. Kalendar (SMM/sana-markazli loyihada).** Kalendar tab. **Kutilgan:** oy ko'rinishi, elementlar rangli chiziqlar; kunni bosish → o'sha kun elementlari.

**B9. Kanban + workflow editor.** Kanban tab. **Kutilgan:** workflow ustunlari (masalan Brif→…→Hisobot). Kartani boshqa ustunga **sudrash** → holat o'zgaradi. Ustun ⋯ yoki "**+ Ustun qo'shish**" → **workflow editor** (ustun qo'shish/nom/rang/tartib/o'chirish). Ustun o'chirilsa — undagi elementlarni boshqa ustunga ko'chirish so'raladi.

**B10. Jadval (Table).** Jadval tab. **Kutilgan:** ustun sarlavhalarini **sudrab** tartiblash, ustun tanlash (show/hide), guruhlash (Holat/Mas'ul/Tur), qatorni sudrab qayta tartiblash.

**B11. Item qo'shish.** Kanban/Jadval'da "**+ Item qo'shish**". **Kutilgan:** loyiha turiga mos item-tur pikeri (masalan Media joylashtirish/Vazifa/Hisobot). Tanlab → forma → saqlash → ko'rinadi.

**B12. 🆕 Loyiha ichi AI import.** Loyiha header'da **✨** → fayl. **Kutilgan:** preview → "Joylashtirish" → elementlar **o'sha loyiha** ichida (kanban/jadvalda) paydo bo'ladi (status = workflow'ning 1-ustuni).

**B13. 🆕 Task↔Loyiha bog'lash.** Vazifa formasida (mini-app) "**Loyiha**" selektori → loyihani tanlang → saqlang. **Kutilgan:** o'sha loyiha Overview'idagi "Bog'langan vazifalar" blokida ko'rinadi (kanbanga aralashmaydi, progress %'ga qo'shilmaydi).

**B14. Uchrashuvlar (mini-app).** Uchrashuvlar tab. **Kutilgan:** uchrashuvlar sanalari bo'yicha; **iOS/Android'da ham sana siljimaydi** (timezone fix — har qurilmada bir xil kun).

---

## C. BU DEPLOYDA QO'SHILGAN — ALOHIDA DIQQAT

1. **Menyu discoverability** — Ijrochilar/Risklar/Statistika bosh menyuda (A1).
2. **Marketing shablonlar** + video/tender workflow + "Boshqa" (B6).
3. **Task↔Project bog'lanishi** (B7, B13).
4. **AI import** — Vazifalar (B3) va har loyiha ichida (B12).

---

## D. MA'LUMOT XAVFSIZLIGI / MIGRATSIYA

**D1.** Deploy'dan oldin `python migrations.py --dry-run` → nima o'zgarishini ko'rsatadi, hech narsa yozmaydi. **Kutilgan:** content_posts→project_items ko'chirish soni ko'rinadi (agar hali qilinmagan bo'lsa).

**D2.** `python migrations.py` → avtomatik backup + ko'chirish. Qayta ishga tushiring → **hech narsa o'zgarmaydi** (idempotent).

**D3.** Restart'dan keyin: eski vazifalar/uchrashuvlar/loyihalar **butun**; SMM kontent-reja avvalgidek (71 post) ko'rinadi.

---

## E. REGRESSIYA (eski funksiyalar buzilmaganini tekshirish)

**E1.** Tez task, tahrir, sub-vazifa, eslatma — avvalgidek ishlaydi.
**E2.** `/export` va mini-app eksport — avvalgi Excel formati.
**E3.** SMM loyiha Kalendari — 71 post, kategoriya chiplari, rang shaffofligi.
**E4.** Avtomatik testlar (dev muhitda): `venv/bin/python tests/tasks_section_smoke.py` (173/0), `tests/qa_regression.py`, `tests/marketing_hub_config_check.py` (162/0), `tests/marketing_hub_items_check.py`, `tests/marketing_hub_migration_check.py` — hammasi yashil.

---

## Belgilash jadvali

| Bo'lim | ✅/❌ | Izoh |
|---|---|---|
| A. Telegram bot (A1–A16) | | |
| B. Mini-app (B1–B14) | | |
| C. Yangi funksiyalar | | |
| D. Migratsiya/xavfsizlik | | |
| E. Regressiya | | |
