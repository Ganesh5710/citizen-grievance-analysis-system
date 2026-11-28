"""
nlp/duration.py
Deterministic multilingual duration & recurrence extractor for English, Telugu,
Hindi, and code-mixed civic complaints.

Public API:
  extract_duration(text) -> tuple[float, str, float] | None
      Returns (hours, matched_text, confidence) or None.
  analyze_duration_and_recurrence(text) -> dict
      Returns full analysis including recurring flag and diagnostics.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Any

# Digit normalization maps (Devanagari & Telugu -> ASCII)
_DIGIT_TRANSLATION = str.maketrans(
    "०१२३४५६७८९౦౧౨౩౪౫౬౭౮౯",
    "01234567890123456789",
)

# Number words across English, Hindi (Roman & Devanagari), Telugu (Roman & Telugu script)
NUMBER_WORDS: dict[str, float] = {
    # English
    "zero": 0.0, "half": 0.5, "a": 1.0, "an": 1.0, "single": 1.0,
    "one": 1.0, "two": 2.0, "couple": 2.0, "three": 3.0, "few": 3.0,
    "four": 4.0, "five": 5.0, "six": 6.0, "seven": 7.0, "eight": 8.0,
    "nine": 9.0, "ten": 10.0, "eleven": 11.0, "twelve": 12.0, "thirteen": 13.0,
    "fourteen": 14.0, "fifteen": 15.0, "sixteen": 16.0, "seventeen": 17.0,
    "eighteen": 18.0, "nineteen": 19.0, "twenty": 20.0,
    "twenty-one": 21.0, "twenty one": 21.0, "twenty-two": 22.0, "twenty two": 22.0,
    "twenty-three": 23.0, "twenty three": 23.0, "twenty-four": 24.0, "twenty four": 24.0,
    "twenty-five": 25.0, "twenty five": 25.0, "twenty-six": 26.0, "twenty six": 26.0,
    "twenty-seven": 27.0, "twenty seven": 27.0, "twenty-eight": 28.0, "twenty eight": 28.0,
    "twenty-nine": 29.0, "twenty nine": 29.0, "thirty": 30.0,
    # Hindi (Roman)
    "aadha": 0.5, "adha": 0.5, "dedh": 1.5, "dhai": 2.5,
    "ek": 1.0, "do": 2.0, "teen": 3.0, "tin": 3.0, "chaar": 4.0, "char": 4.0,
    "paanch": 5.0, "panch": 5.0, "chhah": 6.0, "chhe": 6.0, "cheh": 6.0, "che": 6.0,
    "saat": 7.0, "sat": 7.0, "aath": 8.0, "ath": 8.0, "nau": 9.0, "das": 10.0,
    "gyarah": 11.0, "barah": 12.0, "terah": 13.0, "chaudah": 14.0, "pandrah": 15.0,
    "solah": 16.0, "satrah": 17.0, "atharah": 18.0, "unnis": 19.0, "bees": 20.0,
    "ikkees": 21.0, "baees": 22.0, "teees": 23.0, "chaubees": 24.0, "pachchees": 25.0,
    "chhabbees": 26.0, "sattaees": 27.0, "atthaees": 28.0, "untees": 29.0, "tees": 30.0,
    # Hindi (Devanagari)
    "आधा": 0.5, "आधे": 0.5, "डेढ़": 1.5, "ढाई": 2.5,
    "एक": 1.0, "दो": 2.0, "तीन": 3.0, "चार": 4.0, "पांच": 5.0, "पाँच": 5.0,
    "छह": 6.0, "छे": 6.0, "सात": 7.0, "आठ": 8.0, "नौ": 9.0, "दस": 10.0,
    "ग्यारह": 11.0, "बारह": 12.0, "तेरह": 13.0, "चौदह": 14.0, "पंद्रह": 15.0,
    "सोलह": 16.0, "सत्रह": 17.0, "अठारह": 18.0, "उन्नीस": 19.0, "बीस": 20.0,
    "इक्कीस": 21.0, "बाईस": 22.0, "तेईस": 23.0, "चौबीस": 24.0, "पच्चीस": 25.0,
    "छब्बीस": 26.0, "सत्ताईस": 27.0, "अट्ठाईस": 28.0, "उनतीस": 29.0, "तीस": 30.0,
    # Telugu (Roman)
    "ara": 0.5,
    "okati": 1.0, "oka": 1.0, "okka": 1.0, "rendu": 2.0, "moodu": 3.0, "mudu": 3.0,
    "nalugu": 4.0, "naalugu": 4.0, "aidu": 5.0, "ayidu": 5.0, "aaru": 6.0, "aru": 6.0,
    "edu": 7.0, "yedu": 7.0, "enimidi": 8.0, "tommidi": 9.0, "padi": 10.0,
    "padakondu": 11.0, "pannendu": 12.0, "padamoodu": 13.0, "padanalugu": 14.0,
    "padihenu": 15.0, "padahaaru": 16.0, "padihedu": 17.0, "paddhenimidi": 18.0,
    "pandommidi": 19.0, "iravai": 20.0, "iravai okati": 21.0, "iravai rendu": 22.0,
    "iravai moodu": 23.0, "iravai nalugu": 24.0, "iravai aidu": 25.0,
    "iravai aaru": 26.0, "iravai edu": 27.0, "iravai enimidi": 28.0,
    "iravai tommidi": 29.0, "muppai": 30.0,
    # Telugu script
    "అర": 0.5,
    "ఒకటి": 1.0, "ఒక": 1.0, "రెండు": 2.0, "మూడు": 3.0, "నాలుగు": 4.0,
    "ఐదు": 5.0, "ఆరు": 6.0, "ఏడు": 7.0, "ఎనిమిది": 8.0, "తొమ్మిది": 9.0, "పది": 10.0,
    "పదకొండు": 11.0, "పన్నెండు": 12.0, "పదమూడు": 13.0, "పద్నాలుగు": 14.0,
    "పదిహేను": 15.0, "పదహారు": 16.0, "పదిహేడు": 17.0, "పద్దెనిమిది": 18.0,
    "పందొమ్మిది": 19.0, "ఇరవై": 20.0, "ముప్పై": 30.0,
}

# Unit aliases mapped to hours
UNIT_TO_HOURS: dict[str, float] = {
    # Minutes
    "minute": 1.0 / 60.0, "minutes": 1.0 / 60.0, "min": 1.0 / 60.0, "mins": 1.0 / 60.0,
    "mint": 1.0 / 60.0, "mints": 1.0 / 60.0, "mnt": 1.0 / 60.0, "mnts": 1.0 / 60.0,
    "मिनट": 1.0 / 60.0, "मिनटों": 1.0 / 60.0,
    "నిమిషం": 1.0 / 60.0, "నిమిషాలు": 1.0 / 60.0, "నిమిషాలుగా": 1.0 / 60.0, "నిమిషంగా": 1.0 / 60.0,
    "nimisham": 1.0 / 60.0, "nimishalu": 1.0 / 60.0, "nimishaluga": 1.0 / 60.0,
    # Hours
    "hour": 1.0, "hours": 1.0, "hr": 1.0, "hrs": 1.0,
    "ghanta": 1.0, "ghante": 1.0, "ghanton": 1.0, "ganta": 1.0, "gantalu": 1.0, "gantaluga": 1.0,
    "घंटा": 1.0, "घंटे": 1.0, "घंटों": 1.0, "घन्टा": 1.0, "घन्टे": 1.0,
    "గంట": 1.0, "గంటగా": 1.0, "గంటలు": 1.0, "గంటలుగా": 1.0, "గంటల": 1.0,
    # Days
    "day": 24.0, "days": 24.0, "dy": 24.0, "dys": 24.0, "daay": 24.0, "daays": 24.0,
    "din": 24.0, "dino": 24.0, "dinon": 24.0, "dinam": 24.0,
    "दिन": 24.0, "दिनों": 24.0,
    "roju": 24.0, "rojulu": 24.0, "rojula": 24.0, "rojuluga": 24.0,
    "రోజు": 24.0, "రోజులు": 24.0, "రోజుల": 24.0, "రోజులుగా": 24.0,
    # Weeks
    "week": 168.0, "weeks": 168.0, "wk": 168.0, "wks": 168.0, "wek": 168.0, "weks": 168.0,
    "hafta": 168.0, "hafte": 168.0, "hafton": 168.0, "saptah": 168.0,
    "हफ्ता": 168.0, "हफ्ते": 168.0, "हफ्तों": 168.0, "सप्ताह": 168.0,
    "vaaram": 168.0, "varam": 168.0, "vaaralu": 168.0, "varalu": 168.0, "vaaraluga": 168.0,
    "వారం": 168.0, "వారంగా": 168.0, "వారము": 168.0, "వారాలు": 168.0, "వారాలుగా": 168.0, "వారాల": 168.0,
    # Months
    "month": 720.0, "months": 720.0, "mth": 720.0, "mths": 720.0, "mo": 720.0, "mos": 720.0,
    "mnth": 720.0, "mnths": 720.0,
    "mahina": 720.0, "mahine": 720.0, "mahinon": 720.0,
    "महीना": 720.0, "महीने": 720.0, "महीनों": 720.0,
    "nela": 720.0, "nelalu": 720.0, "nelaluga": 720.0, "nelala": 720.0,
    "నెల": 720.0, "నెలగా": 720.0, "నెలలు": 720.0, "నెలలుగా": 720.0, "నెలల": 720.0,
    # Years
    "year": 8760.0, "years": 8760.0, "yr": 8760.0, "yrs": 8760.0,
    "saal": 8760.0, "sal": 8760.0,
    "साल": 8760.0, "वर्ष": 8760.0,
    "samvatsaram": 8760.0, "samvatsaralu": 8760.0, "samvatsaramga": 8760.0,
    "సంవత్సరం": 8760.0, "సంవత్సరంగా": 8760.0, "సంవత్సరాలు": 8760.0, "సంవత్సరాలుగా": 8760.0,
}

# Build sorted alternations (longest first) for clean boundary-safe matching
_NUM_WORD_ALTS = "|".join(re.escape(k) for k in sorted(NUMBER_WORDS.keys(), key=len, reverse=True))
_UNIT_ALTS = "|".join(re.escape(k) for k in sorted(UNIT_TO_HOURS.keys(), key=len, reverse=True))
_LEFT_BOUND = r"(?:^|(?<=[\s,.;:!?()\[\]/\-]))"
_RIGHT_BOUND = r"(?:$|(?=[\s,.;:!?()\[\]/\-]))"

# Preposition prefixes often attached to durations
_PREP_PREFIX = (
    r"(?:(?:for\s+the\s+last|for\s+the\s+past|over\s+the\s+last|over\s+the\s+past|"
    r"for\s+last|for\s+past|since\s+the\s+last|since\s+last|since|from|for|past|last|"
    r"pichle|gatha|पिछले|గత)\s+)?"
)

_NUM_EXPR = rf"(?:\d+(?:\.\d+)?|(?:half\s+(?:a|an)\s+|aadha\s+|ara\s+|आधा\s+|అర\s+)?(?:{_NUM_WORD_ALTS})|half\s+(?:a|an))"

# Range expression: "2 to 3 days", "3-4 days", "2 se 3 din", "2 nundi 3 rojulu", "two to three weeks"
_RANGE_CONNECTOR = r"(?:\s*(?:-|to|se|nundi|से|నుండి)\s*)"
_RANGE_RE = re.compile(
    rf"{_LEFT_BOUND}({_PREP_PREFIX}({_NUM_EXPR}){_RANGE_CONNECTOR}({_NUM_EXPR})\s+({_UNIT_ALTS})(?:\s+(?:ago|back|nundi|se|నుండి|से|पहले|క్రితం))?){_RIGHT_BOUND}",
    flags=re.IGNORECASE | re.UNICODE,
)

# Single expression: "3 days", "half a day", "for last 3 days", "since 2 months", "3 రోజులుగా", "पिछले 3 दिनों से"
_SINGLE_RE = re.compile(
    rf"{_LEFT_BOUND}({_PREP_PREFIX}({_NUM_EXPR})\s+({_UNIT_ALTS})(?:\s+(?:ago|back|nundi|se|నుండి|से|पहले|క్రితం))?){_RIGHT_BOUND}",
    flags=re.IGNORECASE | re.UNICODE,
)

# Phrases with fixed estimated hours: (pattern, hours, confidence, label_hint)
_PHRASE_SPECS: list[tuple[str, float, float]] = [
    # Yesterday (~24h)
    (r"(?:since|from)\s+yesterday", 24.0, 0.90),
    (r"kal\s+se|कल\s+से|ninna\s+nundi|నిన్న(?:టి)?\s+నుండి|నిన్నటినుండి", 24.0, 0.88),
    # Last night (~12h)
    (r"(?:since|from)\s+last\s+night", 12.0, 0.85),
    (r"(?:pichli\s+)?raat\s+se|रात\s+से|(?:gatha\s+)?rathri\s+nundi|రాత్రి\s+నుండి", 12.0, 0.85),
    # Morning (~8h)
    (r"(?:since|from)\s+(?:this\s+)?morning", 8.0, 0.85),
    (r"subah\s+se|सुबह\s+से|udayam\s+nundi|ఉదయం\s+నుండి", 8.0, 0.85),
    # Last week (7 days = 168h)
    (r"(?:since|from)\s+last\s+week", 168.0, 0.88),
    (r"pichle\s+hafte\s+se|पिछले\s+(?:हफ्ते|सप्ताह)\s+से|gatha\s+vaaram\s+nundi|గత\s+వారం\s+నుండి", 168.0, 0.86),
    # Last month (30 days = 720h)
    (r"(?:since|from)\s+last\s+month", 720.0, 0.85),
    (r"pichle\s+mahine\s+se|पिछले\s+महीने\s+से|gatha\s+nela\s+nundi|గత\s+నెల\s+నుండి", 720.0, 0.84),
    # Vague / long time -> low confidence (~0.35), triggers review queue!
    (r"(?:for\s+ages|for\s+a\s+long\s+time|since\s+a\s+long\s+time|since\s+long|for\s+so\s+long|for\s+months\s+and\s+months)", 168.0, 0.35),
    (r"bahut\s+(?:dino|dinon|samay|waqt)\s+se|बहुत\s+(?:दिनों|समय|वक्त)\s+से|chala\s+(?:rojulu\s*ga|rojula\s+nundi)|చాలా\s+రోజులుగా", 168.0, 0.35),
]
_PHRASE_RES = [(re.compile(rf"{_LEFT_BOUND}({pat}){_RIGHT_BOUND}", re.IGNORECASE | re.UNICODE), hrs, conf) for pat, hrs, conf in _PHRASE_SPECS]

# Recurring frequency patterns -> flag as recurring without converting to duration
_RECURRING_PATTERNS: list[str] = [
    r"\bevery\s+(?:day|morning|night|evening|week|weekend|month|two\s+days)\b",
    r"\b(?:daily|weekly|monthly|every\s+now\s+and\s+then|repeatedly|recurring)\b",
    r"\b(?:once|twice|thrice|two\s+times|three\s+times)\s+(?:a|every|per)\s+(?:day|week|month)\b",
    r"\b(?:har\s+(?:din|roz|hafte|mahine)|rozana)\b",
    r"(?:हर\s+(?:दिन|रोज|हफ्ते|महीने)|रोजाना)",
    r"\b(?:prathi\s+(?:roju|vaaram|nela)|prathiroju)\b",
    r"(?:ప్రతి\s+(?:రోజు|వారం|నెల)|రోజూ)",
]
_RECURRING_RE = re.compile("|".join(_RECURRING_PATTERNS), re.IGNORECASE | re.UNICODE)

# Context filters (word/token-bounded to prevent substring collisions like wage/sewage, rent/current, fee/feeder, pass/bypass)
_IRRELEVANT_CTX_WORDS = (
    "salary", "wage", "wages", "paid", "bonus", "fee", "fees", "rent",
    "valid for", "validity", "expires", "expiry", "warranty", "guarantee",
    "ticket", "pass", "voucher", "subscription",
    "call me after", "come after", "visit after", "wait for",
    "festival", "holiday", "vacation", "leave", "wedding", "event", "exhibition",
    "वेतन", "सैलरी", "छुट्टी", "त्योहार", "वैधता", "वैधता वाला पास", "बस पास", "बाद कॉल", "दिन बाद", "दिनों बाद",
    "జీతం", "సెలవు", "పండుగ", "టికెట్", "గడువు", "తర్వాత వచ్చి", "రోజుల తర్వాత",
)
_IRRELEVANT_CTX_RE = re.compile(
    rf"{_LEFT_BOUND}(?:{'|'.join(re.escape(w) for w in _IRRELEVANT_CTX_WORDS)}){_RIGHT_BOUND}",
    flags=re.IGNORECASE | re.UNICODE,
)
_REPAIRED_VERBS = (
    "fixed", "repaired", "resolved", "cleared", "restored", "cleaned", "patched",
    "ठीक", "బాగు", "మరమ్మత్తు",
)
_REPAIRED_RE = re.compile(
    r"(?:^|\s)(?:" + "|".join(re.escape(w) for w in _REPAIRED_VERBS) + r")(?:\S*)(?=$|\s|[,.;:!?])",
    flags=re.IGNORECASE | re.UNICODE,
)
_REPORTED_UNRESOLVED = (
    "no response", "nobody came", "no action", "still", "not fixed", "unresolved",
    "no reply", "pending", "कोई जवाब नहीं", "स्पंदन नहीं", "స్పందన లేదు",
    "called", "complained", "reported", "informed", "wrote", "messaged", "फोन", "ఫోన్", "शिकायत",
)


def normalize_text_digits(text: str) -> str:
    """Normalize Unicode NFC and translate Devanagari/Telugu digits to ASCII digits."""
    return unicodedata.normalize("NFC", text).translate(_DIGIT_TRANSLATION)


def _parse_num_token(token: str) -> float | None:
    t = " ".join(token.strip().lower().split())
    if re.fullmatch(r"\d+(?:\.\d+)?", t):
        return float(t)
    if t.startswith("half a ") or t.startswith("half an "):
        rest = t.split(None, 2)[-1]
        if rest in NUMBER_WORDS:
            return 0.5 * NUMBER_WORDS[rest]
        return 0.5
    if t in ("half a", "half an", "half"):
        return 0.5
    return NUMBER_WORDS.get(t)


def _window_around(text: str, start: int, end: int, radius: int = 45) -> tuple[str, str]:
    before = text[max(0, start - radius) : start].lower()
    after = text[end : min(len(text), end + radius)].lower()
    return before, after


def _is_irrelevant_match(text_lower: str, match_str: str, start: int, end: int) -> tuple[bool, float]:
    """
    Evaluate context around a matched duration.
    Returns (is_rejected, adjusted_confidence).
    """
    before, after = _window_around(text_lower, start, end, radius=55)
    ctx = before + " " + match_str.lower() + " " + after
    clause_before = re.split(r"[,;.!?]|\bbut\b", before)[-1]
    clause_after = re.split(r"[,;.!?]|\bbut\b|\band\b", after)[0]
    local_ctx = clause_before + " " + match_str.lower() + " " + clause_after

    # Special rule: "fixed/repaired X ago but broke again" or "5 hours ago fixed X but stopped"
    has_ago = any(tok in match_str.lower() or tok in after[:15] for tok in ("ago", "back", "पहले", "క్రితం"))
    if has_ago:
        if _REPAIRED_RE.search(clause_before + " " + clause_after[:25]):
            return True, 0.0
        # "Called the office 3 days ago, no response" -> medium confidence (at least X old)
        if any(r in ctx for r in _REPORTED_UNRESOLVED):
            return False, 0.68

    # Check word-bounded irrelevant context words
    tight_before, tight_after = _window_around(text_lower, start, end, radius=28)
    tight_ctx = tight_before + " " + match_str.lower() + " " + tight_after
    if _IRRELEVANT_CTX_RE.search(tight_ctx):
        return True, 0.0
    # Future directives or post-repair failures: "call me after 3 days", "stopped after 1 hour"
    if re.search(r"\b(?:stopped|broke|failed|restarted|tripped)\s+after\s*$", tight_before):
        return True, 0.0
    if re.search(r"\b(?:call|come|visit|meet|कृपया)\b", local_ctx) and re.search(
        r"(?:\bafter\b|\bwithin\b|बाद|తర్వాత)", tight_before + " " + tight_after[:18]
    ):
        return True, 0.0

    # Recurring guard: if the phrase itself contains "every"/"daily"/"per"/"har"/"prathi"
    if _RECURRING_RE.search(before[-18:] + match_str):
        return True, 0.0

    # Base confidence boosted when explicitly tied to "since"/"for"/"past"/Telugu-ga/Hindi-se
    if any(p in match_str.lower() for p in ("since", "past", "last", "from", "for", "గా", "nundi", "se", "से", "నుండి")):
        return False, 0.92
    return False, 0.85


_PROBLEM_CUES = (
    "no water", "water", "no power", "power", "electricity", "leak", "leaking",
    "broken", "damaged", "blocked", "overflow", "garbage", "outage", "cut",
    "dark", "pothole", "signal", "jam", "sewage", "drain", "problem", "issue",
    "since", "past", "for the last", "नहीं", "बंद", "సమస్య", "లేదు", "రావడం",
)


def _problem_attachment_score(text_lower: str, start: int, end: int) -> int:
    before, after = _window_around(text_lower, start, end, radius=50)
    clause = before + " " + after
    return sum(1 for cue in _PROBLEM_CUES if cue in clause)


def analyze_duration_and_recurrence(text: str) -> dict[str, Any]:
    """
    Analyze complaint text for duration (in hours), matched span, confidence, and
    recurrence ('every week', 'twice a month', etc.).
    """
    if not isinstance(text, str) or not text.strip():
        return {
            "hours": None,
            "matched_text": "",
            "confidence": None,
            "recurring": False,
            "recurring_phrase": "",
            "ambiguous": False,
            "rejected_count": 0,
        }

    norm = normalize_text_digits(text)
    norm_lower = norm.lower()

    rec_match = _RECURRING_RE.search(norm)
    recurring = rec_match is not None
    recurring_phrase = rec_match.group(0).strip() if rec_match else ""

    candidates: list[dict[str, Any]] = []
    occupied_spans: list[tuple[int, int]] = []
    rejected_count = 0

    def _overlaps(s: int, e: int) -> bool:
        return any(not (e <= os_s or s >= os_e) for os_s, os_e in occupied_spans)

    # 1. Ranges first ("2 to 3 days", "3-4 weeks") so inner single unit isn't double-counted
    for m in _RANGE_RE.finditer(norm):
        compound_check = f"{m.group(2).strip().lower()}-{m.group(3).strip().lower()}"
        if compound_check in NUMBER_WORDS:
            continue
        full_txt = m.group(1).strip()
        val_upper = _parse_num_token(m.group(3))
        unit_hrs = UNIT_TO_HOURS.get(m.group(4).strip().lower())
        if val_upper is None or unit_hrs is None or val_upper <= 0:
            continue
        rejected, conf = _is_irrelevant_match(norm_lower, full_txt, m.start(1), m.end(1))
        if rejected:
            rejected_count += 1
            continue
        occupied_spans.append((m.start(1), m.end(1)))
        candidates.append({
            "hours": round(val_upper * unit_hrs, 2),
            "matched_text": full_txt,
            "confidence": conf,
            "attach_score": _problem_attachment_score(norm_lower, m.start(1), m.end(1)),
            "start": m.start(1),
        })

    # 2. Single numeric / number-word durations
    for m in _SINGLE_RE.finditer(norm):
        s, e = m.start(1), m.end(1)
        if _overlaps(s, e):
            continue
        full_txt = m.group(1).strip()
        num_raw = m.group(2).strip()
        unit_raw = m.group(3).strip().lower()
        val = _parse_num_token(num_raw)
        unit_hrs = UNIT_TO_HOURS.get(unit_raw)
        if val is None or unit_hrs is None or val <= 0:
            continue
        # "half a day" matched inside SINGLE_RE: if num_raw already had half + a(=1), adjust cleanly
        if "half" in num_raw.lower() and unit_raw in ("day", "days", "hour", "hours", "week", "weeks", "month", "months"):
            val = 0.5
        elif any(h in num_raw.lower() for h in ("aadha ", "ara ", "आधा ", "అర ")):
            val = 0.5

        rejected, conf = _is_irrelevant_match(norm_lower, full_txt, s, e)
        if rejected:
            rejected_count += 1
            continue
        occupied_spans.append((s, e))
        candidates.append({
            "hours": round(val * unit_hrs, 2),
            "matched_text": full_txt,
            "confidence": conf,
            "attach_score": _problem_attachment_score(norm_lower, s, e),
            "start": s,
        })

    # 3. Relative / vague idioms
    for pat_re, hrs, base_conf in _PHRASE_RES:
        for m in pat_re.finditer(norm):
            s, e = m.start(1), m.end(1)
            if _overlaps(s, e):
                continue
            full_txt = m.group(1).strip()
            rejected, ctx_conf = _is_irrelevant_match(norm_lower, full_txt, s, e)
            if rejected:
                rejected_count += 1
                continue
            occupied_spans.append((s, e))
            candidates.append({
                "hours": float(hrs),
                "matched_text": full_txt,
                "confidence": min(base_conf, ctx_conf),
                "attach_score": _problem_attachment_score(norm_lower, s, e),
                "start": s,
            })

    if not candidates:
        return {
            "hours": None,
            "matched_text": "",
            "confidence": None,
            "recurring": recurring,
            "recurring_phrase": recurring_phrase,
            "ambiguous": False,
            "rejected_count": rejected_count,
        }

    if len(candidates) == 1:
        c = candidates[0]
        return {
            "hours": c["hours"],
            "matched_text": c["matched_text"],
            "confidence": round(float(c["confidence"]), 2),
            "recurring": recurring,
            "recurring_phrase": recurring_phrase,
            "ambiguous": c["confidence"] < 0.55,
            "rejected_count": rejected_count,
        }

    # Multiple candidates: prefer clearly higher problem attachment score;
    # if attachment is unclear/tied across different durations, take the longest & lower confidence.
    distinct_hours = {c["hours"] for c in candidates}
    max_attach = max(c["attach_score"] for c in candidates)
    top_attached = [c for c in candidates if c["attach_score"] == max_attach]
    second_attach = max((c["attach_score"] for c in candidates if c["attach_score"] < max_attach), default=-1)

    if len(top_attached) == 1 and max_attach > second_attach:
        chosen = top_attached[0]
        ambiguous = chosen["confidence"] < 0.55
        conf_out = chosen["confidence"]
    elif len(distinct_hours) > 1:
        chosen = max(candidates, key=lambda c: c["hours"])
        conf_out = min(float(chosen["confidence"]), 0.45)
        ambiguous = True
    else:
        chosen = max(candidates, key=lambda c: c["confidence"])
        conf_out = chosen["confidence"]
        ambiguous = conf_out < 0.55

    return {
        "hours": chosen["hours"],
        "matched_text": chosen["matched_text"],
        "confidence": round(float(conf_out), 2),
        "recurring": recurring,
        "recurring_phrase": recurring_phrase,
        "ambiguous": ambiguous,
        "rejected_count": rejected_count,
    }


def extract_duration(text: str) -> tuple[float, str, float] | None:
    """
    Extract duration from complaint text.
    Returns (hours, matched_text, confidence) or None if no valid problem duration found.
    """
    info = analyze_duration_and_recurrence(text)
    if info["hours"] is None:
        return None
    return (float(info["hours"]), str(info["matched_text"]), float(info["confidence"]))


_TIME_HINT_RE = re.compile(
    r"\b(?:day|days|week|weeks|month|months|year|years|hour|hours|hrs|wks|dys|"
    r"yesterday|morning|night|ago|ages|forever|din|hafta|mahina|saal|roju|rojulu|vaaram|nela)\b|"
    r"(?:दिन|हफ्त|महीन|घंट|రోజు|వార|నెల|గంట)",
    re.IGNORECASE | re.UNICODE,
)


def looks_like_unparsed_duration(text: str, analysis: dict[str, Any] | None = None) -> bool:
    """Detect texts containing time-like cues where no valid duration parsed (for continuous improvement logs)."""
    if not isinstance(text, str) or not text.strip():
        return False
    info = analysis if analysis is not None else analyze_duration_and_recurrence(text)
    if info.get("hours") is not None or info.get("recurring") or info.get("rejected_count", 0) > 0:
        return False
    return _TIME_HINT_RE.search(text) is not None

