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

### TAQIQ (NEVER)

- Sarlavhasiz katta paragraflar
- Qatorlar yopishgan, bo'sh qatorsiz dizayn
- `1. Bo'lim` `2. Bo'lim` raqamli sarlavhalar (emoji ishlat)
- 100+ belgilik bitta uzun satr
- Emoji yo'qligi (har bo'limda kerak)
- Tugma matnida 18+ belgi
