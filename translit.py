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
        return to_cyrillic(text)
    if s in ("latin", "lotin", "lat", "lt"):
        return to_latin(text)
    return text
