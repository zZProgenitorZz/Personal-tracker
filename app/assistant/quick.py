"""Logging a chapter without the API: "Solo Leveling 120", "ik ben bij hoofdstuk 120 van Solo Leveling",
"I'm at chapter one hundred twenty of Solo Leveling". A pure function: no Claude, no network.

Only when it is sure: one title and one chapter number, a title that matches one series clearly
(sounding alike counts: speech recognition hears "solo leaving"), and a small step forward.
Anything else returns None, and Sunny asks Claude as usual (with Confirm).

sound_key and title_score are shared with find_item, so typed and spoken titles match the same way.
"""
import re
import unicodedata
from dataclasses import dataclass

from rapidfuzz import fuzz

from ..reading.events import Status

MIN_SCORE = 85         # the best title must score at least this ...
MIN_LEAD = 10          # ... and this much more than the next one
MAX_STEP = 50          # a bigger jump forward is checked by Claude, with Confirm
MAX_NUMBER = 9999
MAX_WORDS = 14         # a short sentence; longer ones are for Claude
# Words around the title and the number that aren't part of the title (only stripped at its edges,
# so "Lord of the Mysteries" keeps its "of").
FILLERS = set("""ik ben bij zit op heb hebt gelezen lees nu tot en met hoofdstuk hoofdstukken hfst h van
i im am at read reading up to on chapter chapters ch of the in""".split())

NL_UNITS = {"een": 1, "één": 1, "twee": 2, "drie": 3, "vier": 4, "vijf": 5, "zes": 6, "zeven": 7, "acht": 8,
            "negen": 9}
NL_TEENS = {"tien": 10, "elf": 11, "twaalf": 12, "dertien": 13, "veertien": 14, "vijftien": 15, "zestien": 16,
            "zeventien": 17, "achttien": 18, "negentien": 19}
NL_TENS = {"twintig": 20, "dertig": 30, "veertig": 40, "vijftig": 50, "zestig": 60, "zeventig": 70,
           "tachtig": 80, "negentig": 90}
EN_SMALL = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve "
                                       "thirteen fourteen fifteen sixteen seventeen eighteen nineteen".split())}
EN_TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60, "seventy": 70, "eighty": 80,
           "ninety": 90}
NUMBER = re.compile(r"^\d+(?:[.,]\d+)?$")


@dataclass(frozen=True)
class QuickLog:
    series_id: str
    title: str
    old: float
    new: float
    heard: str  # the title as it was heard: a nickname if this match turns out wrong


# ---- Titles that sound alike ----

def _plain(text: str) -> str:
    """Lower case, no accents or punctuation, single spaces."""
    text = unicodedata.normalize("NFKD", text.lower())
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^\w\s]", " ", text.replace("'", ""))).strip()


def sound_key(text: str) -> str:
    """'Omniscient Reader's Viewpoint' and 'omniscient readers few point' come close: lower case, no
    punctuation or spaces, ph→f, ck→k, ie/y→i, double letters single."""
    key = _plain(text).replace(" ", "").replace("ph", "f").replace("ck", "k").replace("ie", "i").replace("y", "i")
    return re.sub(r"(.)\1+", r"\1", key)


def title_score(heard: str, title: str) -> float:
    """0-100: the better of the ordinary fuzzy score and the sound-alike score."""
    return max(fuzz.WRatio(heard, title, processor=str.lower), fuzz.ratio(sound_key(heard), sound_key(title)))


# ---- Numbers ----

def _dutch(word: str) -> int | None:
    """'honderdeenentwintig' -> 121, 'tweehonderd' -> 200, 'drieëntwintig' -> 23."""
    if not word:
        return 0
    for name, value in (("duizend", 1000), ("honderd", 100)):
        if name in word:
            left, right = word.split(name, 1)
            times = _dutch(left) if left else 1
            rest = _dutch(right)
            return None if times is None or rest is None else times * value + rest
    if word in NL_UNITS:
        return NL_UNITS[word]
    if word in NL_TEENS:
        return NL_TEENS[word]
    if word in NL_TENS:
        return NL_TENS[word]
    for unit, value in NL_UNITS.items():  # "eenentwintig": a unit, "en" (or "ën"), a ten
        for joiner in ("en", "ën"):
            if word.startswith(unit + joiner) and word[len(unit + joiner):] in NL_TENS:
                return value + NL_TENS[word[len(unit + joiner):]]
    return None


def _english(words: list[str]) -> int | None:
    """['one', 'hundred', 'and', 'twenty'] -> 120."""
    total = current = 0
    for word in words:
        if word in EN_SMALL:
            current += EN_SMALL[word]
        elif word in EN_TENS:
            current += EN_TENS[word]
        elif word == "hundred":
            current = (current or 1) * 100
        elif word == "thousand":
            total, current = total + (current or 1) * 1000, 0
        elif word != "and":
            return None
    return total + current


def _is_number_word(word: str) -> bool:
    return word in EN_SMALL or word in EN_TENS or word in ("hundred", "thousand") or _dutch(word) not in (None, 0)


def _numbers(words: list[str]) -> list[tuple[int, int, float]]:
    """(first index, end index, value) for every number: digits, or a run of number words."""
    found, i = [], 0
    while i < len(words):
        if NUMBER.match(words[i]):
            found.append((i, i + 1, float(words[i].replace(",", "."))))
            i += 1
            continue
        j = i
        while j < len(words) and (_is_number_word(words[j])
                                  or (words[j] in ("and", "en") and j > i and j + 1 < len(words)
                                      and _is_number_word(words[j + 1]))):
            j += 1
        if j == i:
            i += 1
            continue
        run = words[i:j]
        value = _english(run)
        if value is None:
            value = _dutch("".join(w for w in run if w != "en"))
        if value is not None:
            found.append((i, j, float(value)))
        i = j
    return found


# ---- The sentence ----

def _strip(words: list[str]) -> list[str]:
    while words and words[0] in FILLERS:
        words = words[1:]
    while words and words[-1] in FILLERS:
        words = words[:-1]
    return words


def _parse(text: str) -> tuple[str, float] | None:
    """(title as heard, chapter) from a short sentence, or None."""
    words = re.sub(r"[^\w\s.,]", " ", text.lower().replace("'", "")).replace(",", " ,").split()
    words = [w.strip(".,") or w for w in words if w not in (",", ".")]
    if not words or len(words) > MAX_WORDS:
        return None
    numbers = _numbers(words)
    if len(numbers) != 1:
        return None
    start, end, chapter = numbers[0]
    before, after = _strip(words[:start]), _strip(words[end:])
    if bool(before) == bool(after):  # the title on exactly one side of the number
        return None
    if not 0 < chapter <= MAX_NUMBER:
        return None
    return " ".join(before or after), chapter


def _best(heard: str, entries: list) -> object | None:
    scored = sorted(((title_score(heard, e.title), e) for e in entries), key=lambda se: -se[0])
    if not scored or scored[0][0] < MIN_SCORE:
        return None
    if len(scored) > 1 and scored[0][0] - scored[1][0] < MIN_LEAD:
        return None
    return scored[0][1]


def quick_log(texts: list[str], library, aliases=None) -> QuickLog | None:
    """The chapter to log from what was heard (the text and its alternatives), or None when not sure.
    Nicknames first, then series on Reading, then the rest of the library."""
    for text in texts:
        parsed = _parse(text or "")
        if parsed is None:
            continue
        heard, chapter = parsed
        entry = None
        alias = aliases.series_for(heard) if aliases is not None else None
        if alias:
            entry = library.get(alias)
        if entry is None:
            reading = library.by_status(Status.READING)
            entry = _best(heard, reading) or _best(heard, library.all())
        if entry is None:
            continue
        if entry.current_chapter < chapter <= entry.current_chapter + MAX_STEP:
            return QuickLog(entry.series_id, entry.title, entry.current_chapter, chapter, heard)
        return None  # the right series, but back, the same or a big jump: that's for Claude, with Confirm
    return None
