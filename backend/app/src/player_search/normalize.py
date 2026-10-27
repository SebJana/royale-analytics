"""Normalization shared by indexed players and search queries.

Stored names and typed queries go through the same functions, so a player is
found whenever both reduce to the same characters, whatever font, accent,
case or decoration either side uses. Matching never depends on how a name
was entered, only on what it reduces to.

NOTE Normalization is best effort. It covers fonts, accents, scripts and
decorations as far as Unicode allows, but no rule set can guarantee that
every name is found the way someone types it, and names are not unique.
The player tag is the only reliable identifier; the search UI says so.
"""

import unicodedata
from functools import lru_cache
from typing import NamedTuple

from clash_royale_api import ALPHABET

# NOTE Match check_tag_syntax's 4-12 limit. A longer query cannot be part of a tag.
TAG_MAX_LENGTH = 12

# Enclosing marks and invisible format characters (zero-width spaces,
# joiners, direction marks) vanish without separating words, so
# "Ro" + U+200B + "y" stays one word.
_INVISIBLE_CATEGORIES = {"Me", "Cf"}

# Combining marks readers skip and typists leave out: Latin, Greek and
# Cyrillic accents, Arabic vowel marks, Hebrew points. They vanish like
# invisible characters, so "José" matches "jose". Every other combining mark
# changes the letter (Japanese voicing marks, Indic vowel signs) and is kept:
# ぺ, べ and へ are different sounds, not accented variants.
_STRIPPED_MARKS = (
    (0x0300, 0x036F),  # Combining Diacritical Marks
    (0x0483, 0x0489),  # Cyrillic combining marks
    (0x0591, 0x05C7),  # Hebrew points and cantillation
    (0x064B, 0x065F),  # Arabic harakat
    (0x0670, 0x0670),  # Arabic superscript alef
    (0x1AB0, 0x1AFF),  # Combining Diacritical Marks Extended
    (0x1DC0, 0x1DFF),  # Combining Diacritical Marks Supplement
    (0x20D0, 0x20FF),  # Combining marks for symbols
    (0xFE20, 0xFE2F),  # Combining half marks
    # Not accents, but invisible: they only pick emoji or glyph styles.
    (0xFE00, 0xFE0F),  # Variation selectors
    (0xE0100, 0xE01EF),  # Variation selectors supplement
)

# Abbreviation symbols NFKC would spell out as letters ("ABD™" as "abdtm").
# Players use them as decoration, so they become separators before NFKC.
_DECORATIONS = frozenset("™℠℡№℻")

# Hangul and braille blanks are letters or symbols to Unicode but render as
# nothing; players use them for "invisible" names. NFKC maps U+3164 and
# U+FFA0 to U+1160, so they are dropped both before and after it.
_FILLERS = {chr(c) for c in (0x115F, 0x1160, 0x3164, 0xFFA0, 0x2800)}

# Letters with a stroke, a dotless i and ligatures read as accented or plain
# letters, but Unicode gives them no decomposition, so accent stripping
# misses them: "Çılgın" would never match "cilgin". Lowercase forms only;
# casefold runs first.
_STROKED_LETTERS = {
    "ı": "i",
    "đ": "d",
    "ð": "d",
    "ħ": "h",
    "ł": "l",
    "ø": "o",
    "ŧ": "t",
    "ƀ": "b",
    "ȼ": "c",
    "ɇ": "e",
    "ǥ": "g",
    "ɨ": "i",
    "ɉ": "j",
    "ɍ": "r",
    "ʉ": "u",
    "ɏ": "y",
    "ƶ": "z",
    "ⱦ": "t",
    "ᵽ": "p",
    "æ": "ae",
    "œ": "oe",
}

# Small capitals are plain lowercase letters to a reader, but NFKC keeps them.
# There is no small capital x. Katakana fold to hiragana: both spell the same
# sounds, and a name typed in the other script would otherwise never match.
_LETTER_FOLDS = str.maketrans(
    {
        **{ord(letter): plain for letter, plain in _STROKED_LETTERS.items()},
        **{
            ord(cap): plain
            for cap, plain in zip(
                "ᴀʙᴄᴅᴇꜰɢʜɪᴊᴋʟᴍɴᴏᴘꞯʀꜱᴛᴜᴠᴡʏᴢ", "abcdefghijklmnopqrstuvwyz"
            )
        },
        **{kata: kata - 0x60 for kata in range(0x30A1, 0x30F7)},
        0x30FD: 0x309D,  # ヽ to ゝ
        0x30FE: 0x309E,  # ヾ to ゞ
    }
)


# Chinese, Japanese and Korean, where one character is a syllable or a whole
# word and names are only a few characters long. One or two of them are a
# meaningful query anywhere in a name, unlike one or two Latin letters.
_DENSE_SCRIPTS = (
    (0x1100, 0x11FF),  # Hangul Jamo
    (0x3040, 0x30FF),  # Hiragana, Katakana
    (0x3100, 0x312F),  # Bopomofo
    (0x3130, 0x318F),  # Hangul Compatibility Jamo
    (0x31F0, 0x31FF),  # Katakana Phonetic Extensions
    (0x3400, 0x4DBF),  # CJK Unified Ideographs Extension A
    (0x4E00, 0x9FFF),  # CJK Unified Ideographs
    (0xAC00, 0xD7AF),  # Hangul Syllables
    (0xF900, 0xFAFF),  # CJK Compatibility Ideographs
    (0x20000, 0x3134F),  # CJK Unified Ideographs Extensions B-G
)


def is_dense(text: str) -> bool:
    """Whether every character is Chinese, Japanese or Korean.

    Short queries in these scripts match anywhere in a name; short queries in
    other scripts only match at the start.
    """

    return bool(text) and all(
        any(low <= ord(c) <= high for low, high in _DENSE_SCRIPTS) for c in text
    )


def _is_stripped_mark(ch: str) -> bool:
    code = ord(ch)
    return any(low <= code <= high for low, high in _STRIPPED_MARKS)


def _is_invisible(ch: str) -> bool:
    """Whether a character disappears without separating words."""

    return unicodedata.category(ch) in _INVISIBLE_CATEGORIES or _is_stripped_mark(ch)


def _is_kept(ch: str) -> bool:
    category = unicodedata.category(ch)
    return (
        (category[0] in "LN" or category in ("Mc", "Mn"))
        and ch not in _FILLERS
        and not _is_stripped_mark(ch)
    )


@lru_cache(maxsize=8192)
def _fold_char(ch: str) -> str:
    """Reduce one character to the letters and digits it reads as.

    Folding per character, not per string, keeps every output character
    traceable to its source character, which word start detection needs.
    normalize_name composes the string first, so no character here still
    needs a neighbour to fold correctly.
    """

    if ch.isascii():
        return ch.lower() if ch.isalnum() else ""
    base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not _is_invisible(c))
    folded = unicodedata.normalize("NFKC", base).casefold().translate(_LETTER_FOLDS)
    return "".join(c for c in folded if _is_kept(c))


def normalize_name(name: str) -> tuple[str, int]:
    """Reduce a player name to its search key and its word starts.

    The key keeps only letters, digits and the marks that change a letter,
    casefolded, without the accents people skip when typing, and with
    katakana written as hiragana. Spaces,
    punctuation, symbols, emoji and blank fillers are removed, so
    "Royal King", "Royal_King" and "★RoyalKing★" all become "royalking" and
    a query finds them however the separator is typed. The separators live on
    as word starts: "kin" matches "Royal King" at a word start, which ranks
    above the same match inside "Earthkinkling".

    A word starts after a removed separator, at a lower to upper case step
    ("SuperRoy"), at a letter/digit switch ("king99roy") and where ASCII and
    other scripts meet ("西蒙丨Snow", "Beta能"). Position 0 is never marked: a
    match there is a prefix match.

    Args:
        name (str): The player name as stored or typed.

    Returns:
        tuple[str, int]: The key and a bitmask with bit i set when a word
            starts at key position i.
    """

    # Composing first makes pairs that only combine across characters fold
    # like the single character: a letter and its accent, Hangul typed as
    # separate jamo, half-width kana with a separate voicing mark ("ﾍﾟ").
    # Decorations become spaces first, so NFKC cannot spell them out.
    if not name.isascii():
        name = unicodedata.normalize(
            "NFKC", "".join(" " if c in _DECORATIONS else c for c in name)
        )
    parts = []
    # Key length so far, i.e. the key position the next folded text starts at.
    length = 0
    starts = 0
    # A removed separator came since the last kept character.
    separated = False
    # Last kept source character (for case steps) and last key character
    # (for digit and script switches).
    prev_raw = ""
    prev_kept = ""
    for ch in name:
        folded = _fold_char(ch)
        if not folded:
            # Symbols and spaces separate words; invisible characters don't.
            if not _is_invisible(ch):
                separated = True
            continue
        # length > 0 skips position 0, which is a prefix match anyway.
        if length and (
            separated
            or (ch.isupper() and prev_raw.islower())
            or folded[0].isdigit() != prev_kept.isdigit()
            or folded[0].isascii() != prev_kept.isascii()
        ):
            starts |= 1 << length
        parts.append(folded)
        length += len(folded)
        prev_raw = ch
        prev_kept = folded[-1]
        separated = False
    return "".join(parts), starts


def normalize_tag(tag: str) -> str:
    """Reduce a player tag or tag query to its search key.

    "#yyrjqy28", "＃YYRJQY28" and " yyrjqy28 " all become "YYRJQY28". The tag
    alphabet has no letter O, so an O always means the digit 0: "2pyo" becomes
    "2PY0".

    Args:
        tag (str): The tag as stored or typed, with or without "#".

    Returns:
        str: The tag without "#" and whitespace, uppercased, O mapped to 0.
    """

    compact = "".join(unicodedata.normalize("NFKC", tag).split())
    return compact.replace("#", "").upper().replace("O", "0")


def is_tag_like(tag_key: str) -> bool:
    """Whether a normalized tag query can be part of a player tag."""

    return 0 < len(tag_key) <= TAG_MAX_LENGTH and all(c in ALPHABET for c in tag_key)


class ParsedQuery(NamedTuple):
    """A search query reduced to what each side of the index is searched with.

    name is the name key, empty when the query has no letters or digits. tag
    is the tag key, empty when the query cannot be part of a tag.
    tags_first puts every tag match above every name match; it is set when
    the query starts with "#". tag_prefix_first lets tag prefixes outrank
    name prefixes; it is set when the query contains a digit, which almost
    every tag does and few names do. tags_first wins when both are set.
    """

    name: str
    tag: str
    tags_first: bool
    tag_prefix_first: bool


def parse_query(query: str) -> ParsedQuery:
    """Decide how a raw search query is matched against names and tags.

    Names and tags are both searched, with or without a leading "#". The "#"
    only ranks tags first: names may contain "#" too, and the name key drops
    it like any other symbol.

    Args:
        query (str): The query as typed.

    Returns:
        ParsedQuery: The keys and flags the index searches with.
    """

    stripped = unicodedata.normalize("NFKC", query).strip()
    tag = normalize_tag(stripped)
    if not is_tag_like(tag):
        tag = ""
    # The raw query, not the NFKC form: normalize_name has to see decorations
    # like "™" before NFKC spells them out, exactly as for stored names.
    name = normalize_name(query)[0]
    return ParsedQuery(
        name=name,
        tag=tag,
        tags_first=bool(tag) and stripped.startswith("#"),
        # Checked on the typed text: the O to 0 mapping would turn "roy" into
        # a query with a digit.
        tag_prefix_first=bool(tag) and any(c.isdigit() for c in stripped),
    )
