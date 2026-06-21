"""Uzbek Latin↔Cyrillic transliterator tests (translit.py). No external deps."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import translit  # noqa: E402

_PASS = 0
_FAIL = 0
_FAILED: list[str] = []


def check(name: str, got, want) -> None:
    global _PASS, _FAIL
    ok = got == want
    print(f"  [{'✓' if ok else '✗'}] {name}" + ("" if ok else f"  got={got!r} want={want!r}"))
    if ok:
        _PASS += 1
    else:
        _FAIL += 1
        _FAILED.append(name)


def main() -> None:
    print("── Latin → Cyrillic ──")
    c = translit.to_cyrillic
    check("oʻ → ў", c("oʻzbek"), "ўзбек")
    check("o' (ASCII apos) → ў", c("o'zbek"), "ўзбек")
    check("gʻ → ғ", c("gʻalaba"), "ғалаба")
    check("sh → ш", c("shahar"), "шаҳар")
    check("ch → ч", c("choy"), "чой")
    check("ya/yo/yu", c("yaxshi yoz yurak"), "яхши ёз юрак")
    check("tutuq ʼ → ъ", c("maʼno"), "маъно")
    check("tutuq ' → ъ", c("ma'no"), "маъно")
    check("e word-initial → э", c("eshik"), "эшик")
    check("e mid-word → е", c("kel"), "кел")
    check("q→қ x→х h→ҳ", c("xalq huquq"), "халқ ҳуқуқ")
    check("j→ж", c("jurnal"), "журнал")
    # CRITICAL: native 't+s' must NOT become ц (no ts→ц rule)
    check("ketsa → кетса (NOT кеца)", c("ketsa"), "кетса")
    check("capitalization: Toshkent", c("Toshkent"), "Тошкент")
    # "bayon" (statement) is баён in standard Uzbek Cyrillic → yo=ё, so баённома.
    check("ALL CAPS: BAYONNOMA → БАЁННОМА", c("BAYONNOMA"), "БАЁННОМА")
    check("Title digraph: Sharq", c("Sharq"), "Шарқ")
    check("digits/punct pass-through", c("2026-yil, 14:00"), "2026-йил, 14:00")
    check("Oʻ title", c("Oʻzbekiston"), "Ўзбекистон")

    print("\n── Cyrillic → Latin ──")
    la = translit.to_latin
    check("ў → oʻ", la("ўзбек"), "oʻzbek")
    check("ғ → gʻ", la("ғалаба"), "gʻalaba")
    check("ш → sh", la("шаҳар"), "shahar")
    check("ч → ch", la("чой"), "choy")
    check("я/ё/ю", la("яхши ёз юрак"), "yaxshi yoz yurak")
    check("ъ → ʼ", la("маъно"), "maʼno")
    check("қ х ҳ", la("халқ ҳуқуқ"), "xalq huquq")
    check("Title: Тошкент", la("Тошкент"), "Toshkent")

    print("\n── transliterate() dispatcher ──")
    check("kiril alias", translit.transliterate("salom", "kiril"), "салом")
    check("lotin alias", translit.transliterate("салом", "lotin"), "salom")
    check("unknown script → unchanged", translit.transliterate("salom", "xyz"), "salom")
    check("empty string", translit.transliterate("", "kiril"), "")

    print("\n── to_cyrillic_pro() — brands/acronyms/codes kept Latin ──")
    p = translit.to_cyrillic_pro
    # Uzbek words still convert
    check("plain Uzbek converts", p("Aktiv vazifalar"), "Актив вазифалар")
    check("ALL CAPS Uzbek converts", p("AKTIV VAZIFALAR"), "АКТИВ ВАЗИФАЛАР")
    check("oʻ/gʻ inside still convert", p("oʻquv gʻalaba"), "ўқув ғалаба")
    # Brand names stay Latin (any casing)
    check("brand: Agrobank", p("Agrobank rejasi"), "Agrobank режаси")
    check("brand: VISA caps", p("VISA karta"), "VISA карта")
    check("brand: HUMO + UzCard", p("HUMO va UzCard"), "HUMO ва UzCard")
    check("acronym: KPI", p("KPI hisoboti"), "KPI ҳисоботи")
    check("app: Telegram", p("Telegram bot"), "Telegram бот")
    # Codes / numbers / URLs / emails stay verbatim
    check("card code *9088", p("VISA *9088 karta"), "VISA *9088 карта")
    check("version 4.6", p("Claude 4.6 versiya"), "Claude 4.6 версия")
    check("domain humo.uz", p("humo.uz sayti"), "humo.uz сайти")
    check("email kept", p("ali@bank.uz ga"), "ali@bank.uz га")
    # Colon must split (not be swallowed as a code)
    check("colon splits word", p("Hisobot: natija"), "Ҳисобот: натижа")
    # 'it' (dog) is NOT in the keep-list → must convert (collision guard)
    check("uzbek 'it' converts", p("it va mushuk"), "ит ва мушук")
    check("empty string", p(""), "")
    check("transliterate(cyr) uses pro", translit.transliterate("Agrobank reja", "kiril"), "Agrobank режа")

    print("\n── quote_names() — brand/org names wrapped in \"...\" ──")
    q = translit.quote_names
    check("brand at start", q("Agrobank uchun reja"), '"Agrobank" uchun reja')
    check("brand at end", q("reja: Agrobank"), 'reja: "Agrobank"')
    check("bare brand", q("Agrobank"), '"Agrobank"')
    check("two brands both quoted", q("HUMO va UzCard"), '"HUMO" va "UzCard"')
    check("app names quoted", q("Telegram va Excel"), '"Telegram" va "Excel"')
    check("acronym NOT quoted", q("KPI hisoboti"), "KPI hisoboti")
    check("unit NOT quoted", q("USD kursi"), "USD kursi")
    check("code NOT quoted", q("VISA *9088"), '"VISA" *9088')
    check("URL NOT quoted", q("humo.uz sayti"), "humo.uz sayti")
    check("no double «»", q("«Agrobank» reja"), "«Agrobank» reja")
    check('no double ""', q('"Agrobank" reja'), '"Agrobank" reja')
    check("plain Uzbek untouched", q("vazifa bajarilsin"), "vazifa bajarilsin")
    check("empty string", q(""), "")
    # Export pipeline: quote first, then transliterate — brand stays Latin, quoted
    check("quote+cyr pipeline",
          translit.to_cyrillic_pro(q("Agrobank rejasi")), '"Agrobank" режаси')

    print("\n── Round-trip (Latin→Cyrillic→Latin) ──")
    for w in ("ozbekiston", "shahar", "choy", "yaxshi", "xalq", "huquq", "marketing"):
        rt = la(c(w))
        check(f"round-trip stable: {w}", rt, w)

    print("\n" + "=" * 48)
    print(f"NATIJA:  ✅ {_PASS} o'tdi   ❌ {_FAIL} yiqildi")
    if _FAILED:
        print("Yiqilganlar: " + ", ".join(_FAILED))
    print("=" * 48)
    sys.exit(1 if _FAIL else 0)


if __name__ == "__main__":
    main()
