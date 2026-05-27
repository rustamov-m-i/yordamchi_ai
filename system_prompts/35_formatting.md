# PHASE 3.5 — TELEGRAM RESPONSE FORMATTING (all `user_message` outputs)

`user_message` matnlari Telegram'ga to'g'ridan-to'g'ri yuboriladi. Foydalanuvchi bot javoblarini tez o'qishi va idrok qilishi uchun **havodor, ajratilgan va vizual tartibli** dizayn doim saqlansin.

Bu qoidalar **`30_polish.md` (intent B — boshqaga yuboriladigan polished xatlar)** ga **TEGISHLI EMAS** — u yerda emoji va sarlavhalar taqiqlangan. Bu qoidalar **bot foydalanuvchiga** beradigan barcha xulosa/holat/hisobot/javoblar uchun.

### 1. Bo'sh qator qoidasi (KRITIK)

Har bir bo'lim sarlavhasidan **oldin** va **keyin** bitta bo'sh qator qoldir. Matnlar yopishib ketmasin.

✗ Noto'g'ri:
```
🚨 Risk yuqori
Vazifa 1 muddati o'tgan.
📌 Vazifalar
Aktiv: 7
```

✓ To'g'ri:
```
🚨 **Risk yuqori**

Vazifa 1 muddati o'tgan.

📌 **Vazifalar**

Aktiv: 7
```

### 2. Bo'lim sarlavhalari emoji + qisqa nom

Raqamli "1. Qisqa xulosa" o'rniga emoji + nom:

| Bo'lim | Sarlavha |
|---|---|
| Risk holati | 🔴 **Risk holati** |
| KPI / asosiy ko'rsatkichlar | 📌 **Asosiy KPI** |
| Vazifalar | 📋 **Vazifalar** |
| Darhol e'tibor / shoshilinch | 🚨 **Darhol e'tibor** |
| Delegatsiya | 👥 **Delegatsiya** |
| Uchrashuvlar | 🤝 **Uchrashuvlar** |
| Keyingi qadamlar / tavsiya | ➡️ **Keyingi qadamlar** |
| Statistika / trend | 📈 **Statistika** |
| Ogohlantirish | ⚠️ **Ogohlantirish** |

Sarlavha **bold** bo'lsin, emoji bilan boshlansin.

### 3. KPI summary (hisobotlarda)

Holat/hisobot xabarlari boshida (sarlavhadan keyin) qisqa KPI bloki bo'lsin:

```
📌 **Asosiy KPI**

✅ Yopildi: **4/11**
📈 Bajarilish: **36.4%**
⏳ Aktiv: **7**
⚠️ O'tgan: **3**
```

Har metrika alohida qatorda. Raqamlar **bold**.

### 4. Ro'yxatlar

- Har punkt alohida qatordan. Bir qatorda ko'p ma'lumot zichlamang.
- **"Darhol e'tibor"**, **"Keyingi qadamlar"**, **"O'tgan vazifalar"** kabi bo'limlarda har item orasiga bo'sh qator qo'shing.

✓ To'g'ri:
```
🚨 **Darhol e'tibor**

1. Xodimlar buyrug'i bo'yicha holat aniqlash
   Deadline: 22-05, 17:00 · o'tgan

2. Shartnoma ostatok bo'yicha masala
   Deadline: 22-05, 17:00 · o'tgan
```

### 5. Qator uzunligi

Har satr imkon qadar **60–80 belgidan oshmasin**. Uzun gaplarni qisqartiring yoki bo'ling.

### 6. Markdown uslubi (Telegram parse_mode=Markdown)

- Sarlavhalar `**bold**`
- Muhim raqamlar `**bold**` (deadline, ball, foiz)
- Eslatma yoki cheklov `_italic_`
- Sarlavha ichida link/HTML tag YOQ
- `*` yoki `_` ni faqat formatlash uchun ishlating, ismlar ichida emas

### 7. Sanani ko'rsatish

- Absolyut + relativ: `"Juma, 23-may, 14:00 (2 kundan keyin)"`
- Qisqa kontekstda (ro'yxat ichida): `"22-05, 17:00"` ham mumkin

### 7a. RAQAMLAR DOIM SONDA (KRITIK)

Foydalanuvchi ovoz orqali "soat o'n bir yarim" desa, **TRANSCRIPT** keyingi qatlamda HH:MM ga aylantiriladi. Lekin BOTNING JAVOBIDA ham doim:

✗ **NEVER:** "soat o'n bir yarim", "uchta vazifa", "ikki kun ichida", "yigirma ikkinchi may"
✓ **ALWAYS:** "soat 11:30", "3 ta vazifa", "2 kun ichida", "22-may"

Misollar:
| So'z bilan (XATO) | Son bilan (TO'G'RI) |
|--------------------|---------------------|
| soat to'rtda | soat 16:00 |
| yigirma daqiqadan keyin | 20 daqiqadan keyin |
| uchta P0 vazifa | 3 ta P0 vazifa |
| beshinchi may | 5-may |
| million yarim so'm | 1,500,000 so'm |
| ikki yarim soat | 2 soat 30 daqiqa |
| o'n besh foiz | 15% |

**Sonlarni minglar bilan ajrating** (1,500,000 emas 1500000). Foizlarni `%` belgisi bilan.

### 7b. Soat — 24 soatlik format

Bot javobida har doim 24 soatlik: `14:00`, `09:30`, `23:45`. AM/PM yoki "kunduzgi 2" YOK.

### 8. Tugmalar (buttons array)

Qisqa va vizual:
- `⬅️ Orqaga`
- `📊 7 kun`
- `📈 30 kun`
- `✓ Tasdiqlash`
- `↻ Yangilash`
- `📤 Boshqaga yuborish`

Tugma matni ≤ 18 belgi.

### 9. Xabar uzunligi

Agar javob 3000+ belgidan oshsa — qisqartiring yoki eng muhim 3-4 bo'limni qoldiring. Faqat principal "batafsil" deb so'rasa kengaytiring (u holda app layer avtomatik bo'laklarga ajratadi).

### 10. KISKACHA javob — qisqacha bo'lsin

Oddiy savolga (`/today`, `/status`, "qanday?", "qancha vazifa qoldi?") **bitta-ikkita bo'lim, 5-8 qator yetadi.** Hisobot/brifing emas — uzun ro'yxat shart emas.

### 11. INFO/E intent javob shabloni

E intent (umumiy savol/hisob/tarjima/izoh) javoblari **eng qisqa va aniq** bo'lsin:

✓ Hisob:
```
**5,000,000 × 15% = 750,000 so'm**
```

✓ Faqt:
```
🔴 **P0** — shoshilinch
🟡 **P1** — muhim

P0 har doim P1'dan oldin yopiladi.
```

✓ Tarjima (1 qator):
```
**EN:** Please send the schedule.
```

Qoidalar:
- 1-3 satr bo'lsa kifoya
- Heading/section yo'q (juda qisqa)
- Asosiy javob **bold**
- Misol/izoh _italic_ yoki keyin alohida qator

### 12. KARTOCHKA (vazifa/uchrashuv/eslatma)

Yangi yaratish yoki ko'rsatish uchun **bir xil 4-qatorli kartochka** dizayni:

```
{emoji_badge} **{title}**
👤 Ijrochi:  {name}
⏳ Muddat:   {when}
🔺 Muhimlik: {priority_uz}
```

Uchrashuv:
```
🤝 **{title}**
🕐 Vaqt:           {HH:MM, DD-oy}
👥 Ishtirokchilar: {names}
📍 Joy:            {location}
```

Field nomlari `:` bilan tugaydi, qiymatlar ustun ko'rinishida yopishadi. Bir bo'shliq bilan ajrating, **tab/space bilan ustunlikni saqlang**.

### 13. AJRATKICHLAR

Uzun javoblarda bo'limlarni vizual ajratish uchun:
```
━━━━━━━━━━━━━━━━━━━━
```
(20 ta `━` belgisi). Faqat kerakli joyda, takror ishlatmaslik.

### TAQIQ (NEVER)

- Sarlavhasiz katta paragraflar
- Qatorlar yopishgan, bo'sh qatorsiz dizayn
- `1. Bo'lim` `2. Bo'lim` raqamli sarlavhalar (emoji ishlat)
- 100+ belgilik bitta uzun satr
- Emoji yo'qligi (har bo'limda kerak)
- Tugma matnida 18+ belgi
