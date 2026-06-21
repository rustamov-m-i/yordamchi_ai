"""Uzbek Latin ↔ Cyrillic transliteration (rule-based, deterministic).

Used by the bayonnoma (meeting protocol) export so the principal can switch the
document's script with one tap. Covers standard Uzbek orthography:

  oʻ/gʻ (apostrophe forms)  ye/ya/yo/yu  sh/ch  tutuq belgisi (ʼ → ъ)

KNOWN LIMITS (the protocol UI keeps an edit path on purpose):
  - Proper nouns / brand / loanwords ("Fanzona", "bean bag") may not match the
    native rules.
  - The e/э distinction is approximated (word-initial → э, else → е).
  - Cyrillic→Latin is inherently lossy for е (e vs ye) and ц.

Pure functions, no deps — safe to import anywhere.
"""

import re

# Apostrophe-like marks: oʻ/gʻ modifier AND the tutuq belgisi. We accept every
# common variant a keyboard or copy-paste might produce.
_APOS = "'’ʼ‘ʻ`´ʹ"

# Latin → Cyrillic digraphs (checked BEFORE single letters; longest match first).
# NOTE: deliberately NO "ts"→ц (would break native "ketsa", "otsa") and NO
# "ng" rule (n→н + g→г already yields нг).
_L2C_DIGRAPHS = [
    ("ya", "я"), ("yo", "ё"), ("yu", "ю"), ("ye", "е"),
    ("ch", "ч"), ("sh", "ш"),
]

_L2C_SINGLE = {
    "a": "а", "b": "б", "d": "д", "f": "ф", "g": "г", "h": "ҳ", "i": "и",
    "j": "ж", "k": "к", "l": "л", "m": "м", "n": "н", "o": "о", "p": "п",
    "q": "қ", "r": "р", "s": "с", "t": "т", "u": "у", "v": "в", "x": "х",
    "y": "й", "z": "з",
    # loanword fallbacks (not in the modern Uzbek Latin alphabet)
    "c": "с", "w": "в",
}


def _cased(src_token: str, cyr: str) -> str:
    """Apply the source token's casing to the Cyrillic result."""
    if len(src_token) > 1 and src_token.isupper():
        return cyr.upper()
    if src_token[:1].isupper():
        return cyr[:1].upper() + cyr[1:]
    return cyr


def to_cyrillic(text: str) -> str:
    """Uzbek Latin → Cyrillic."""
    if not text:
        return text
    out: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        low = ch.lower()

        # oʻ / gʻ → ў / ғ
        if low in ("o", "g") and i + 1 < n and text[i + 1] in _APOS:
            cyr = "ў" if low == "o" else "ғ"
            out.append(cyr.upper() if ch.isupper() else cyr)
            i += 2
            continue

        # digraphs (ya/yo/yu/ye/ch/sh)
        matched = False
        for lat, cyr in _L2C_DIGRAPHS:
            seg = text[i:i + len(lat)]
            if seg.lower() == lat:
                out.append(_cased(seg, cyr))
                i += len(lat)
                matched = True
                break
        if matched:
            continue

        # tutuq belgisi (standalone apostrophe, not part of oʻ/gʻ) → ъ
        if ch in _APOS:
            out.append("ъ")
            i += 1
            continue

        # e → э word-initially, else е
        if low == "e":
            prev = text[i - 1] if i > 0 else ""
            word_start = (i == 0) or ((not prev.isalpha()) and (prev not in _APOS))
            cyr = "э" if word_start else "е"
            out.append(cyr.upper() if ch.isupper() else cyr)
            i += 1
            continue

        if low in _L2C_SINGLE:
            cyr = _L2C_SINGLE[low]
            out.append(cyr.upper() if ch.isupper() else cyr)
            i += 1
            continue

        # digits, punctuation, spaces, unknown — pass through
        out.append(ch)
        i += 1
    return "".join(out)


# ── Professional pass: keep brand names / acronyms / codes in Latin ──────────
# Tokens whose Cyrillic transliteration would look wrong in a formal document.
# Matched case-insensitively (so "Agrobank"/"AGROBANK"/"agrobank" all qualify).
# Deliberately EXCLUDES anything that collides with a real Uzbek word
# (e.g. "it" = dog, "ish", "or") so genuine Uzbek text still converts.
#
# Two tiers:
#   _QUOTE_NAMES   — proper nouns (banks, payment systems, products, apps).
#                    Kept Latin AND wrapped in quotes in formal export
#                    (Agrobank → "Agrobank"), per Uzbek orthographic norm.
#   _KEEP_LATIN_ONLY — acronyms, units, formats, URL schemes. Kept Latin but
#                    NEVER quoted (you don't write "KPI" or "USD").
_QUOTE_NAMES = {
    # local banks & payment systems
    "agrobank", "humo", "uzcard", "visa", "mastercard", "maestro", "unionpay",
    "swift", "click", "payme", "uzum", "anorbank", "kapitalbank", "ipoteka",
    "octobank", "tbc", "paynet", "apelsin", "nbu", "sqb", "infinbank", "davr",
    # software / apps / platforms (proper-noun products)
    "telegram", "excel", "word", "powerpoint", "outlook", "icloud", "claude",
    "openai", "chatgpt", "whisper", "gmail", "zoom", "google", "microsoft",
    "windows", "github", "iphone", "macbook", "youtube", "instagram",
    "facebook", "linkedin", "whatsapp", "viber",
}
_KEEP_LATIN_ONLY = {
    "pdf", "http", "https", "www", "android", "ios",
    # business acronyms & units (kept Latin in formal Uzbek writing)
    "kpi", "crm", "erp", "api", "sla", "swot", "qr", "sms", "vip", "ceo",
    "cfo", "coo", "cto", "usd", "uzs", "eur", "rub", "gb", "mb", "tb",
}
_KEEP_LATIN = _QUOTE_NAMES | _KEEP_LATIN_ONLY

# Quote-like marks already in the source — used to avoid double-quoting a name.
# A SET (not a str): membership of an empty string in a str is always True, which
# would wrongly suppress quoting for a name at the very start/end of a cell.
_QUOTE_CHARS = frozenset("\"«»“”„‘’" + _APOS)

# A maximal run that STARTS with a Latin letter and may carry domain/code chars
# (dot, @, +, _, -) and Uzbek apostrophes (oʻ/gʻ/tutuq) — but NOT ":" or "/", so
# "Hisobot:" splits cleanly while "example.com" / "oʻquv" stay whole.
_LATIN_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9.+_@" + _APOS + r"-]*")


def _keep_latin_token(tok: str) -> bool:
    """True if the token should stay in Latin (brand, acronym, code, URL, id)."""
    if any(c.isdigit() for c in tok):      # *9088, model ids, "4.6", dates
        return True
    if any(c in ".@" for c in tok):        # domains, emails, code fragments
        return True
    core = tok.strip(_APOS + "-._").casefold()
    return core in _KEEP_LATIN


def to_cyrillic_pro(text: str) -> str:
    """Professional Latin → Cyrillic for formal documents.

    Transliterates Uzbek words but KEEPS brand names, acronyms, alphanumeric
    codes, URLs and emails in Latin — so "Agrobank", "VISA *9088", "KPI" and
    "humo.uz" survive intact while "Aktiv vazifalar" → "Актив вазифалар".
    """
    if not text:
        return text
    return _LATIN_TOKEN.sub(
        lambda m: m.group(0) if _keep_latin_token(m.group(0)) else to_cyrillic(m.group(0)),
        text,
    )


def quote_names(text: str, quote: str = '"') -> str:
    """Wrap recognized brand / organization / product names in `quote` marks,
    per the formal-Uzbek norm: Agrobank → "Agrobank".

    Only proper nouns in _QUOTE_NAMES are touched — acronyms (KPI), units (USD),
    codes (*9088), URLs/emails and already-quoted names are left untouched
    (no double-quoting). Script-independent: run it before transliteration so
    both the Latin and the Cyrillic export get identical quoting.
    """
    if not text:
        return text

    def _repl(m: "re.Match") -> str:
        tok = m.group(0)
        if any(c.isdigit() for c in tok) or any(c in ".@" for c in tok):
            return tok  # URL / email / code — never a quotable plain name
        if tok.strip(_APOS + "-._").casefold() not in _QUOTE_NAMES:
            return tok
        s, i, j = m.string, m.start(), m.end()
        before = s[i - 1] if i > 0 else ""
        after = s[j] if j < len(s) else ""
        if before in _QUOTE_CHARS or after in _QUOTE_CHARS:
            return tok  # already quoted — don't double it
        return f"{quote}{tok}{quote}"

    return _LATIN_TOKEN.sub(_repl, text)


# Cyrillic → Latin (digraphs/special first). Soft sign is dropped; е→e (lossy).
_C2L = [
    ("ў", "oʻ"), ("ғ", "gʻ"), ("ш", "sh"), ("ч", "ch"),
    ("я", "ya"), ("ё", "yo"), ("ю", "yu"), ("ц", "ts"),
    ("ъ", "ʼ"), ("ь", ""),
    ("а", "a"), ("б", "b"), ("в", "v"), ("г", "g"), ("д", "d"), ("е", "e"),
    ("ж", "j"), ("з", "z"), ("и", "i"), ("й", "y"), ("к", "k"), ("л", "l"),
    ("м", "m"), ("н", "n"), ("о", "o"), ("п", "p"), ("р", "r"), ("с", "s"),
    ("т", "t"), ("у", "u"), ("ф", "f"), ("х", "x"), ("ҳ", "h"), ("қ", "q"),
    ("э", "e"), ("ы", "i"),
]
_C2L_MAP = {c: lat for c, lat in _C2L}


def to_latin(text: str) -> str:
    """Uzbek Cyrillic → Latin."""
    if not text:
        return text
    out: list[str] = []
    for ch in text:
        low = ch.lower()
        if low in _C2L_MAP:
            lat = _C2L_MAP[low]
            if not lat:  # ь → dropped
                continue
            if ch.isupper():
                # title-case the multi-char result (oʻ→Oʻ, ya→Ya), upper the single
                lat = lat[:1].upper() + lat[1:]
            out.append(lat)
        else:
            out.append(ch)
    return "".join(out)


def transliterate(text: str, script: str) -> str:
    """Convert `text` to the requested script ('cyrillic'/'kiril' or
    'latin'/'lotin'). Unknown script → text unchanged."""
    s = (script or "").strip().lower()
    if s in ("cyrillic", "cyril", "kiril", "krill", "cyr", "kr"):
        return to_cyrillic_pro(text)
    if s in ("latin", "lotin", "lat", "lt"):
        return to_latin(text)
    return text
