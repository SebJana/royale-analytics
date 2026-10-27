"""In-memory search over tracked player names and tags.

Guarantees:
- Every true match is ranked before the result is cut to the limit. No
  cheaper first pass can drop a better match.
- The ranking is a total order, so the same players and query always give
  the same results, whatever order the players were added in.
- upsert and remove apply before they return, so a new player is searchable
  as soon as the add route has stored it.

Every trigram, and every one or two character prefix, maps to the players
containing it, pre-sorted by how a query of exactly that text would rank
them. Chinese, Japanese and Korean pieces of one or two characters are
listed from every position too, since there a single character is already
a word. A query up to three characters is the start of one list.

A longer query walks the list of its first trigram in order and stops once
no better match can follow: a name never ranks better for the query than
for its first three characters, so after the `limit`-th best match, an entry
whose trigram rank is already worse ends the walk. Exact names, the one
exception, come from a binary search first. "king" thus reads a few dozen
entries instead of every name holding "kin" (e.g. 65k of 1M). A query with few
matches but a common first trigram ("kingx") gives up on the walk and
checks the shortest of its trigram lists in full, which every match has to
be in. See _IndexSide._walk_first_gram.

Not thread-safe. All mutations and searches run on the event loop; only a
fresh index is built in a worker thread.

Measured on synthetic names built from a skewed vocabulary, so common
words repeat as they do in real names ("roy" sits in 12% of them), on a
desktop CPU. Search times are every keystroke of typing 400 names:

    players                     100k        300k        1M
    memory                      35 MB       97 MB       322 MB
    memory peak while building  94 MB       274 MB      912 MB
    full build                  3.7 s       14 s        49 s
    search, p50                 0.09 ms     0.13 ms     0.17 ms
    search, p99                 1.5 ms      3.8 ms      13 ms
    search, slowest             3.4 ms      14 ms       25 ms
    rename one player, p50      0.6 ms      1.0 ms      1.0 ms

Memory grows linearly, ~320 bytes and 18 list entries per player. The
build sorts every entry, so it grows as n log n. Queries up to three
characters cost the same at any size. Longer ones stop once the order
rules out a better match, which is immediate for common words but has to
pass every weaker match first otherwise, so their worst case grows with n.

Why in memory instead of SQLite FTS5 or Postgres pg_trgm? Both databases
were given the same normalized keys and word starts, found candidates
through their trigram and prefix indexes, ranked them with this exact sort
key in SQL, and returned the same results for every query. Per search over
the same typing workload (Postgres including a ~0.9 ms round trip):

    players         engine            mean      p50       p99       slowest
    100k            this index        0.34 ms   0.11 ms   3.3 ms    13 ms
                    SQLite FTS5       6.7 ms    0.83 ms   74 ms     119 ms
                    Postgres pg_trgm  9.7 ms    3.9 ms    71 ms     150 ms
    1M              this index        1.2 ms    0.14 ms   12 ms     19 ms
                    SQLite FTS5       85 ms     5.4 ms    941 ms    1.8 s
                    Postgres pg_trgm  82 ms     9.6 ms    770 ms    1.8 s

The databases are slowest on exactly the common case of autocomplete: a
short query matching thousands of names, which they rank row by row on every
request. Here that ranking is done once, ahead of time, so the query reads
the first `limit` entries of a list, and longer queries stop as soon as the
order rules out anything better. There is no network hop, query planner or
second store to keep in sync.

The design targets tens to a few hundred thousand tracked players. At that
scale a dedicated database or search engine buys nothing but cost: another
container to run and monitor, a network hop on every keystroke, another
dependency and driver, and a second copy of the players to keep in sync
with Mongo. This index lives in the API process, needs only the standard
library, and answers 99% of keystrokes within 15 ms up to about a million
players. Far beyond that, memory, build time and the slowest long
queries outgrow a single process, and a search service with change-based
sync becomes the better fit.

Ranking: Every match gets a sort key, smaller is better, and a search returns
the first `limit` keys over all matches:

    (tier, position, key length, key, tag)

    tier         kind of match, best first for a plain name query: exact tag,
                 exact name, name prefix, word start inside the name, anywhere
                 else in the name, tag prefix, anywhere else in the tag. A
                 query with a digit moves tag prefixes up to third; a query
                 starting with "#" puts every tag match first. An existing
                 full tag is pinned first in every mode.
    position     where the match starts; earlier is better.
    key length   shorter keys are closer to what was typed.
    key, tag     alphabetical, then the unique tag, so equal names still get
                 a fixed order.

For "roy" that gives: Roy (exact name), Royal King (prefix), TheRoyal (word
start at 3), SuperRoy (word start at 5), xxroyxx (inside, at 2), Ployroyd
(inside, at 3). Word starts come from normalize.normalize_name.

Size: A key of length n is listed under at most 2 prefixes and n - 2
trigrams, so at most n lists; a Chinese, Japanese or Korean key adds up to
2n - 1 one and two character pieces. Each list entry is a 4 byte slot number:

    entries per player   <= len(name key) + len(tag key)  (+ dense pieces)
    list memory          ~= 4 bytes x entries per player x players

Name keys have a median length of 7 and tags 8-9 characters, so about 16
entries per player. Measured on 4,248 tracked players: 72,501 entries (17
per player), built in 0.4 s. At 50k players that is ~850k entries and
~3.4 MB of lists; the per-player strings take most of the rest.

The build is the slow part: every one of those entries is sorted in Python.
It runs once at startup in a thread, and every later change is an
incremental upsert or remove.
"""

import heapq
from array import array
from bisect import bisect_left
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from typing import NamedTuple

from .normalize import is_dense, normalize_name, normalize_tag, parse_query

# Queries shorter than a trigram only match at the start of a name or tag.
# Common letter pairs like "ka" sit inside a large share of all names, so
# matching them anywhere would mostly return noise. Chinese, Japanese and
# Korean queries are the exception (see normalize.is_dense).
GRAM = 3

# Match tiers, the first part of the sort key (see "Ranking" in the module
# docstring). Best first for a plain name query; the orders below move the
# tag tiers up for tag-like queries.
EXACT_TAG = 0
EXACT_NAME = 1
NAME_PREFIX = 2
NAME_WORD = 3
NAME_INFIX = 4
TAG_PREFIX = 5
TAG_INFIX = 6

MATCH_TYPES = (
    "exactTag",
    "exactName",
    "namePrefix",
    "nameWord",
    "nameInfix",
    "tagPrefix",
    "tagInfix",
)

# Result position of each tier, indexed by tier. Every order keeps the name
# tiers and the tag tiers in their own relative order, so lists pre-sorted
# by tier stay sorted in any order and merge without re-sorting.
_NAME_FIRST = (0, 1, 2, 3, 4, 5, 6)
# A query with a digit is likely a tag: tag prefixes move above name prefixes.
_TAG_PREFIX_FIRST = (0, 1, 3, 4, 5, 2, 6)
# A query starting with "#" means a tag: every tag match comes first.
_TAGS_FIRST = (0, 3, 4, 5, 6, 1, 2)

# A long query whose shortest trigram list holds at most this many players
# checks that list outright, about a millisecond. Only longer lists are worth
# the early stopping walk, which can fall back to the same check and then
# costs up to twice as much.
_CHECK_ALL_MAX = 2048

# A long query's ranked walk checks its stopping bound on every this many
# entries (see _IndexSide._walk_first_gram). Small enough that a walk overshoots
# by a handful of entries, large enough to skip most bound computations.
_BOUND_STRIDE = 16

# Bits for a player's tie-break rank in a packed build sort key. Enough for
# 4 billion players; the slot arrays are 32 bit anyway.
_RANK_BITS = 32


# Word start bits kept per name: the starts are stored in 8 byte slots, not
# as one int object per player. Only keys that NFKC stretches past 64
# characters (ligatures like "ﷺ") lose their later word starts, so a word
# match there ranks as a match inside a word.
_STARTS_MASK = (1 << 64) - 1


class SearchResult(NamedTuple):
    """One ranked search result. match is one of MATCH_TYPES."""

    tag: str
    name: str
    match: str


def _name_match(key: str, starts: int, query: str) -> tuple[int, int] | None:
    """Return the tier and position of query in a name key, None without a match.

    A match at a word start beats an earlier match inside a word, so "roy"
    in "xroySuperRoy" counts as a word match at position 9.
    """

    pos = key.find(query)
    if pos < 0:
        return None
    if pos == 0:
        return (EXACT_NAME if len(key) == len(query) else NAME_PREFIX), 0
    if len(query) < GRAM and not is_dense(query):
        return None
    # The first occurrence may sit inside a word while a later one starts a
    # word; walk the occurrences until one does.
    word = pos
    while word >= 0:
        if starts >> word & 1:
            return NAME_WORD, word
        word = key.find(query, word + 1)
    return NAME_INFIX, pos


def _tag_match(key: str, query: str) -> tuple[int, int] | None:
    """Return the tier and position of query in a tag key, None without a match."""

    pos = key.find(query)
    if pos < 0:
        return None
    if pos == 0:
        return TAG_PREFIX, 0
    if len(query) < GRAM:
        return None
    return TAG_INFIX, pos


# Lists of one field by kind: prefixes, trigrams and dense pieces, each piece
# mapped to the (tier, position) a query of exactly that piece matches at.
# Dicts, not lists of pairs: "royroy" holds "roy" twice but HAS to sit in its
# list once, since unlink finds a slot by its sort key and removes one copy.
_Pieces = tuple[dict[str, tuple[int, int]], ...]


def _name_pieces(key: str, starts: int) -> _Pieces:
    """Return the lists a name key is listed under, with its tier in each.

    Dense pieces are the Chinese, Japanese and Korean runs of one or two
    characters at any position. ASCII keys, most of them, have none.

    NOTE Gives each piece the tier and position _name_match would, in one
    walk over the key instead of one find per piece, which takes about a
    third off the build. Change both together: a list is ordered by the sort
    key of a query equal to its piece, so a tier that differs from what
    _name_match says puts the slot where bisect never looks for it.
    """

    prefixes: dict[str, tuple[int, int]] = {}
    grams: dict[str, tuple[int, int]] = {}
    dense: dict[str, tuple[int, int]] = {}
    if not key:
        return prefixes, grams, dense
    # One and two letter queries only match at the start (see GRAM), so these
    # two are all the short lists a key needs. Longer prefixes need no list
    # of their own: the trigram list holds every name starting with it, and
    # ranks them first. A one or two letter key is its own prefix, an exact
    # name.
    for piece in (key[:1], key[:2]):
        prefixes[piece] = (EXACT_NAME if piece == key else NAME_PREFIX), 0
    # Trigrams from every position: a three letter query matches anywhere,
    # and longer queries take their candidates from these lists.
    _walk_name(grams, key, starts, GRAM)
    # An ASCII key has no Chinese, Japanese or Korean character, so the walk
    # for dense pieces could only skip every piece of most names.
    if not key.isascii():
        _walk_name(dense, key, starts, 1)
        _walk_name(dense, key, starts, 2)
    return prefixes, grams, dense


def _walk_name(
    lists: dict[str, tuple[int, int]], key: str, starts: int, size: int
) -> None:
    """Add every piece of one size to lists, with its first or first word match.

    Pieces shorter than a trigram are only kept when dense: "ka" in the middle
    of a name is noise, while "王" is a word.
    """

    # Walked left to right, so the first sighting of a piece is its earliest
    # occurrence, which is what str.find in _name_match returns.
    for i in range(len(key) - size + 1):
        piece = key[i : i + size]
        if size < GRAM and not is_dense(piece):
            continue
        found = lists.get(piece)
        # The first occurrence counts unless a later one starts a word, the
        # better tier: "roy" in "xroySuperRoy" is a word match at 9. A prefix
        # or word match is already the best a piece can get, so only an
        # inside-a-word match is ever replaced.
        if found is None or found[0] == NAME_INFIX and starts >> i & 1:
            lists[piece] = _name_tier(key, starts, piece, i)


def _name_tier(key: str, starts: int, piece: str, pos: int) -> tuple[int, int]:
    """The tier and position of a piece of key occurring at pos."""

    # Position 0 never has a word start bit (normalize_name leaves it out),
    # so it is checked first: a match there is a prefix or the whole name.
    if pos == 0:
        return (EXACT_NAME if piece == key else NAME_PREFIX), 0
    return (NAME_WORD if starts >> pos & 1 else NAME_INFIX), pos


def _tag_pieces(key: str) -> _Pieces:
    """Return the lists a tag key is listed under, with its tier in each.

    Tags have no dense pieces: a tag query only holds tag alphabet characters.

    NOTE Gives each piece the tier and position _tag_match would. Change both
    together, for the same reason as _name_pieces.
    """

    if not key:
        return {}, {}, {}
    # No exact tier here, unlike names: search pins a full tag through the
    # tag lookup before reading any list, so the lists only rank prefixes.
    prefixes = {key[:1]: (TAG_PREFIX, 0), key[:2]: (TAG_PREFIX, 0)}
    grams: dict[str, tuple[int, int]] = {}
    for i in range(len(key) - 2):
        # Tags have no words, so the earliest occurrence always counts and
        # setdefault keeps it.
        grams.setdefault(key[i : i + GRAM], (TAG_PREFIX if i == 0 else TAG_INFIX, i))
    return prefixes, grams, {}


def _copy_lists(lists: dict[str, array]) -> dict[str, array]:
    return {group: slots[:] for group, slots in lists.items()}


class _IndexSide:
    """The prefix, trigram and dense lists of one field (names or tags)."""

    def __init__(
        self,
        sort_key: Callable[[int, str], tuple],
        pieces: Callable[[int], _Pieces],
        exact_tier: int | None = None,
        prefix_tier: int | None = None,
    ):
        # Each list holds slot numbers as array("I"): raw 4 byte unsigned
        # ints in one block. A Python list would hold an 8 byte pointer to a
        # ~28 byte int object per entry, about 36 bytes instead of 4. "I" is
        # 4 bytes on every common platform ("L" is 8 on Linux), which caps
        # slots at 2**32 - 1. The price: inserting or deleting in the middle
        # shifts the rest of the array, which is most of what upsert and
        # remove spend their time on in large lists.
        self.prefixes: dict[str, array] = {}
        self.grams: dict[str, array] = {}
        self.dense: dict[str, array] = {}
        # (slot, query) -> (tier, position, key length, key, tag key), the
        # order every list is kept in.
        self.sort_key = sort_key
        # slot -> the lists its current key belongs to, with its tier in each.
        self.pieces = pieces
        # The tier of a key equal to the query and of a key starting with it.
        # Only names have an exact tier in their lists; a full tag is pinned
        # by search through the tag lookup instead.
        self.exact_tier = exact_tier
        self.prefix_tier = prefix_tier

    @property
    def all_lists(self) -> tuple[dict[str, array], ...]:
        """The list dicts in the order pieces returns their members."""

        return self.prefixes, self.grams, self.dense

    def link(self, slot: int) -> None:
        for lists, members in zip(self.all_lists, self.pieces(slot)):
            for group in members:
                slots = lists.get(group)
                if slots is None:
                    lists[group] = array("I", [slot])
                    continue
                # Binary search by the sort key the list is ordered by. The
                # list stores slots, so the key is computed per probed slot;
                # g=group binds this loop's group into the lambda.
                at = bisect_left(
                    slots,
                    self.sort_key(slot, group),
                    key=lambda s, g=group: self.sort_key(s, g),
                )
                slots.insert(at, slot)

    def unlink(self, slot: int) -> None:
        """Remove a slot. Its keys HAVE to still be the ones it was linked with."""

        for lists, members in zip(self.all_lists, self.pieces(slot)):
            for group in members:
                slots = lists[group]
                # Sort keys end with the unique tag, so no two slots share
                # one and the search lands exactly on this slot.
                at = bisect_left(
                    slots,
                    self.sort_key(slot, group),
                    key=lambda s, g=group: self.sort_key(s, g),
                )
                # Only possible if a list was corrupted or the keys changed
                # before unlinking; failing loudly beats a silent stale hit.
                if at >= len(slots) or slots[at] != slot:
                    raise RuntimeError(f"Search list {group!r} lost slot {slot}")
                del slots[at]
                if not slots:
                    del lists[group]

    def _short_query_lists(self, query: str) -> dict[str, array]:
        """The lists whose entry for query already holds every match, in order."""

        if len(query) == GRAM:
            return self.grams
        # Short Chinese, Japanese and Korean queries match anywhere.
        return self.dense if is_dense(query) else self.prefixes

    def matches(self, query: str, order: tuple, limit: int) -> Iterator[tuple]:
        """Yield the matches for query, best first, as merge-ready tuples.

        Only the start of a pre-sorted list is read for queries up to GRAM
        long. Longer queries walk their first trigram's list until no better
        match can follow, or check the shortest trigram list in full, then
        keep the best `limit`: no player beyond a side's own top `limit` can
        reach the overall top `limit`.
        """

        # Yielded as (order position, position, key length, key, tag key,
        # tier, slot). heapq.merge compares them up to the tag key, which is
        # unique, so tier and slot only ride along: the tier names the match
        # type in the result, the slot finds the player.
        if len(query) <= GRAM:
            for slot in self._short_query_lists(query).get(query, ()):
                tier, pos, *rest = self.sort_key(slot, query)
                yield (order[tier], pos, *rest, tier, slot)
            return

        # Every match contains every trigram of the query, so any one list is
        # a complete candidate set; the shortest is the cheapest to check. A
        # trigram no key has means no match at all.
        shortest = None
        for i in range(len(query) - 2):
            slots = self.grams.get(query[i : i + GRAM])
            if slots is None:
                return
            if shortest is None or len(slots) < len(shortest):
                shortest = slots
        first = self.grams[query[:GRAM]]
        best = None
        if len(shortest) > _CHECK_ALL_MAX:
            # The early stopping walk can lose to checking the shortest list
            # outright, when the first trigram is common and the query is not
            # ("kingx"). Giving up after as many entries as the shortest list
            # holds caps the worst case at twice the plain check.
            budget = None if first is shortest else len(shortest)
            best = self._walk_first_gram(first, query, limit, budget)
        if best is None:
            best = self._check_all(shortest, query, limit)
        # Ranked by the plain sort key; every order keeps the tiers of one
        # side in their relative order, so this is also the order's ranking.
        for tier, pos, *rest, slot in best:
            yield (order[tier], pos, *rest, tier, slot)

    def _check_all(self, slots: array, query: str, limit: int) -> list[tuple]:
        """Rank every slot, keep the best `limit` as (*sort key, slot)."""

        found = []
        for slot in slots:
            # None when the trigrams occur, but not as one run ("aab...aaa"
            # holds both trigrams of "aaab" without containing it).
            rank = self.sort_key(slot, query)
            if rank is not None:
                found.append((*rank, slot))
        return heapq.nsmallest(limit, found)

    def _walk_first_gram(
        self, first: array, query: str, limit: int, budget: int | None
    ) -> list[tuple] | None:
        """Rank the first trigram's list in order until nothing better can follow.

        That list is sorted by how its trigram ranks in each key, and the
        trigram never ranks worse than the whole query: wherever the query
        matches, its first three characters match too, at the same position
        or an earlier one with an equal or better tier. So once an entry's
        trigram rank is worse than the `limit`-th best match found so far,
        every later entry's query rank is worse as well, and the walk stops.
        For a common word like "king" that is after a few dozen entries
        instead of every name holding "kin".

        The one exception is an exact name: "king" is an exact match for the
        name "king", but "kin" is only a prefix of it. Exact names are taken
        from their own run up front, see _exact_run.

        Returns:
            list[tuple] | None: The best `limit` as (*sort key, slot), or
                None when the walk passed `budget` entries without finishing.
        """

        if limit <= 0:
            return []
        gram = query[:GRAM]
        best = self._exact_run(first, query, limit)
        # The `limit`-th best sort key so far; None until `limit` are known.
        threshold = None
        if len(best) >= limit:
            best = heapq.nsmallest(limit, best)
            threshold = best[-1][:-1]
        for n, slot in enumerate(first):
            if budget is not None and n >= budget:
                return None
            # Checked every few entries, not each: the bound costs as much as
            # ranking the entry, and the few extra entries ranked meanwhile
            # are only extra candidates.
            if (
                threshold is not None
                and n % _BOUND_STRIDE == 0
                and self.sort_key(slot, gram) > threshold
            ):
                break
            rank = self.sort_key(slot, query)
            if rank is None or rank[0] == self.exact_tier:
                continue
            best.append((*rank, slot))
            # Trimmed in batches: one nsmallest per `limit` additions is far
            # cheaper than keeping a heap of tuples ordered on every add.
            if len(best) >= 2 * limit:
                best = heapq.nsmallest(limit, best)
                threshold = best[-1][:-1]
        return heapq.nsmallest(limit, best)

    def _exact_run(self, first: array, query: str, limit: int) -> list[tuple]:
        """Return the best `limit` keys equal to query, as (*sort key, slot).

        In the first trigram's list they are prefix matches of that trigram
        with the query's length, sorted by key, so they form one run that a
        binary search finds. Within the run only the tag differs, so the run
        is already best first and its first `limit` entries are the best.
        Common names are exact names of thousands of players ("king" of
        ~9,000 in a million), and ranking them all cost ~18 ms.
        """

        if self.exact_tier is None:
            return []
        gram = query[:GRAM]
        # "" sorts before every tag, so the search lands on the run's start.
        probe = (self.prefix_tier, 0, len(query), query, "")
        at = bisect_left(first, probe, key=lambda s: self.sort_key(s, gram))
        run = []
        while at < len(first) and len(run) < limit:
            rank = self.sort_key(first[at], query)
            if rank is None or rank[0] != self.exact_tier:
                break
            run.append((*rank, first[at]))
            at += 1
        return run


class PlayerSearchIndex:
    """Ranked substring search over player names and tags. See the module docstring."""

    def __init__(self):
        # slot -> player, one column per field. A slot is just a player's
        # position here, 0 to N-1 after a build, and the number every list
        # stores. It carries no meaning; the order inside a list comes from
        # the sort key. A removed player's slot has tag key None, goes to
        # _free, and the next new player reuses it. Columns instead of one
        # tuple per player save the tuple, ~90 bytes a player.
        self._tag_keys: list[str | None] = []
        self._display_names: list[str] = []
        self._name_keys: list[str] = []
        self._name_starts = array("Q")
        # slot -> tag as given, only where it is not "#" + tag key. Tags the
        # API validated never are, so nearly no tag is stored twice, while
        # any other spelling still comes back unchanged.
        self._odd_tags: dict[int, str] = {}
        self._free: list[int] = []
        # tag key -> slot, for exact tags, upsert and remove.
        self._by_tag: dict[str, int] = {}
        self._names = _IndexSide(
            self._name_sort_key, self._name_pieces, EXACT_NAME, NAME_PREFIX
        )
        self._tags = _IndexSide(self._tag_sort_key, self._tag_pieces)

    def _name_pieces(self, slot: int) -> _Pieces:
        return _name_pieces(self._name_keys[slot], self._name_starts[slot])

    def _tag_pieces(self, slot: int) -> _Pieces:
        return _tag_pieces(self._tag_keys[slot])

    def _name_sort_key(self, slot: int, query: str) -> tuple | None:
        key = self._name_keys[slot]
        match = _name_match(key, self._name_starts[slot], query)
        if match is None:
            return None
        return (*match, len(key), key, self._tag_keys[slot])

    def _tag_sort_key(self, slot: int, query: str) -> tuple | None:
        key = self._tag_keys[slot]
        match = _tag_match(key, query)
        if match is None:
            return None
        # The tag key fills both the key and the tie-break place, so name and
        # tag sort keys have the same shape and merge into one ranking.
        return (*match, len(key), key, key)

    def _new_slot(self) -> int:
        self._tag_keys.append(None)
        self._display_names.append("")
        self._name_keys.append("")
        self._name_starts.append(0)
        return len(self._tag_keys) - 1

    def _store(self, slot: int, tag: str, name: str | None, tag_key: str) -> None:
        name = name or ""
        name_key, starts = normalize_name(name)
        self._tag_keys[slot] = tag_key
        self._display_names[slot] = name
        self._name_keys[slot] = name_key
        self._name_starts[slot] = starts & _STARTS_MASK
        if tag == "#" + tag_key:
            self._odd_tags.pop(slot, None)
        else:
            self._odd_tags[slot] = tag

    def _tag(self, slot: int) -> str:
        return self._odd_tags.get(slot) or "#" + self._tag_keys[slot]

    @classmethod
    def build(cls, players: Iterable[tuple[str, str | None]]) -> "PlayerSearchIndex":
        """Build an index from (tag, name) pairs in one pass.

        Sorting every list once is much faster than inserting players one by
        one. Each list member is packed into one int (tier, position, rank by
        key length, key and tag), so the sorts compare ints instead of tuples
        and give exactly the order upsert keeps.

        Args:
            players (Iterable[tuple[str, str | None]]): Tags and names. A
                repeated tag keeps its last name.

        Returns:
            PlayerSearchIndex: The populated index.
        """

        index = cls()
        for tag, name in players:
            tag_key = normalize_tag(tag)
            if not tag_key:
                continue
            slot = index._by_tag.get(tag_key)
            if slot is None:
                slot = index._new_slot()
                index._by_tag[tag_key] = slot
            index._store(slot, tag, name, tag_key)

        # A player's position in these orders is their tie-break rank: one
        # small int that sorts like (key length, key, tag), the last three
        # parts of every sort key, so _fill can pack whole keys into ints.
        name_keys, tag_keys = index._name_keys, index._tag_keys
        by_name = sorted(
            range(len(tag_keys)),
            key=lambda s: (len(name_keys[s]), name_keys[s], tag_keys[s]),
        )
        by_tag = sorted(
            range(len(tag_keys)), key=lambda s: (len(tag_keys[s]), tag_keys[s])
        )
        index._fill(index._names, by_name)
        index._fill(index._tags, by_tag)
        return index

    @staticmethod
    def _fill(side: _IndexSide, slot_by_rank: list[int]) -> None:
        mask = (1 << _RANK_BITS) - 1
        packed = tuple(defaultdict(list) for _ in side.all_lists)
        for rank, slot in enumerate(slot_by_rank):
            for lists, pieces in zip(packed, side.pieces(slot)):
                for group, (tier, pos) in pieces.items():
                    # One int per entry, bits from high to low: tier, match
                    # position (32 bits), tie-break rank (32 bits). Comparing
                    # these ints compares the full sort keys.
                    lists[group].append((tier << 32 | pos) << _RANK_BITS | rank)
        # Sorted ints back to slots: the low bits are the rank, and
        # slot_by_rank turns a rank into its slot.
        for target, lists in zip(side.all_lists, packed):
            for group, values in lists.items():
                values.sort()
                target[group] = array("I", [slot_by_rank[v & mask] for v in values])

    def upsert(self, tag: str, name: str | None) -> bool:
        """Add a player or update their name.

        Args:
            tag (str): The player tag, e.g. "#YYRJQY28".
            name (str | None): The player name; None indexes the tag only.

        Returns:
            bool: False when the player was already indexed with this exact
                tag and name, True otherwise.

        Raises:
            ValueError: If the tag is empty after normalization.
        """

        tag_key = normalize_tag(tag)
        if not tag_key:
            raise ValueError(f"Cannot index an empty tag: {tag!r}")
        slot = self._by_tag.get(tag_key)
        if slot is not None:
            if self._tag(slot) == tag and self._display_names[slot] == (name or ""):
                return False
            # Unlinked while the old keys are still in place: the lists find
            # a slot by its sort key, which comes from the keys.
            self._names.unlink(slot)
            self._tags.unlink(slot)
        elif self._free:
            slot = self._free.pop()
        else:
            slot = self._new_slot()
        self._store(slot, tag, name, tag_key)
        self._by_tag[tag_key] = slot
        self._names.link(slot)
        self._tags.link(slot)
        return True

    def remove(self, tag: str) -> bool:
        """Remove a player. Returns False if the tag was not indexed."""

        slot = self._by_tag.pop(normalize_tag(tag), None)
        if slot is None:
            return False
        self._names.unlink(slot)
        self._tags.unlink(slot)
        # Emptied, not just marked free, so the old strings can be collected.
        self._tag_keys[slot] = None
        self._display_names[slot] = self._name_keys[slot] = ""
        self._name_starts[slot] = 0
        self._odd_tags.pop(slot, None)
        self._free.append(slot)
        return True

    def search(self, query: str, limit: int) -> list[SearchResult]:
        """Return the best `limit` players for a query, best first.

        Names and tags are both searched and an existing full tag is pinned
        first. A leading "#" ranks every tag match above every name match.

        Args:
            query (str): The query as typed.
            limit (int): Maximum number of results.

        Returns:
            list[SearchResult]: Ranked results, each player at most once.
        """

        parsed = parse_query(query)
        picked: list[tuple[int, int]] = []
        seen: set[int] = set()

        exact = self._by_tag.get(parsed.tag) if parsed.tag else None
        if exact is not None:
            picked.append((exact, EXACT_TAG))
            seen.add(exact)

        if parsed.tags_first:
            order = _TAGS_FIRST
        elif parsed.tag_prefix_first:
            order = _TAG_PREFIX_FIRST
        else:
            order = _NAME_FIRST
        # Both sides yield best first in the same order, so merging them gives
        # one ranking without sorting. A player matching by name and by tag
        # comes up twice; the first, better match wins and the second is
        # skipped. The sides are lazy, so only about `limit` entries are read.
        sides = []
        if parsed.name:
            sides.append(self._names.matches(parsed.name, order, limit))
        if parsed.tag:
            sides.append(self._tags.matches(parsed.tag, order, limit))
        for *_, tier, slot in heapq.merge(*sides):
            if len(picked) >= limit:
                break
            if slot not in seen:
                seen.add(slot)
                picked.append((slot, tier))
        return self._results(picked[:limit])

    def _results(self, picked: list[tuple[int, int]]) -> list[SearchResult]:
        return [
            SearchResult(self._tag(slot), self._display_names[slot], MATCH_TYPES[tier])
            for slot, tier in picked
        ]

    def get(self, tag: str) -> str | None:
        """Return the indexed name of a tag, None if the tag is not indexed."""

        slot = self._by_tag.get(normalize_tag(tag))
        return None if slot is None else self._display_names[slot]

    def slot_count(self) -> int:
        """Return the number of slots, used and free. Slots run 0 to count - 1."""

        return len(self._tag_keys)

    def slot_of(self, tag: str) -> int | None:
        """Return the slot of a tag, None if the tag is not indexed.

        A slot only identifies the player until they are removed; then the
        next new player may reuse it.
        """

        return self._by_tag.get(normalize_tag(tag))

    def tag_at(self, slot: int) -> str | None:
        """Return the tag in a slot, None for a free slot."""

        return None if self._tag_keys[slot] is None else self._tag(slot)

    def items(self) -> Iterator[tuple[str, str]]:
        """Yield every indexed (tag, name) pair."""

        for slot, tag_key in enumerate(self._tag_keys):
            if tag_key is not None:
                yield self._tag(slot), self._display_names[slot]

    def __len__(self) -> int:
        return len(self._by_tag)

    def to_state(self) -> dict:
        """Return a copy of the index data, for saving it elsewhere.

        A copy, not the live containers: the caller pickles it in a worker
        thread while the event loop keeps changing the index, and a pickle
        of a dict that changes size midway fails or mixes two states. The
        copy itself is plain memory copying, a few milliseconds even at
        hundreds of thousands of players.

        Returns:
            dict: Everything from_state needs. Only valid for this exact
                version of the index and normalize code.
        """

        return {
            "tagKeys": self._tag_keys.copy(),
            "displayNames": self._display_names.copy(),
            "nameKeys": self._name_keys.copy(),
            "nameStarts": array("Q", self._name_starts),
            "oddTags": self._odd_tags.copy(),
            "free": self._free.copy(),
            "names": [_copy_lists(lists) for lists in self._names.all_lists],
            "tags": [_copy_lists(lists) for lists in self._tags.all_lists],
        }

    @classmethod
    def from_state(cls, state: dict) -> "PlayerSearchIndex":
        """Restore an index from to_state, without rebuilding any list.

        Args:
            state (dict): A to_state result of the same code version.

        Returns:
            PlayerSearchIndex: The restored index.
        """

        index = cls()
        index._tag_keys = state["tagKeys"]
        index._display_names = state["displayNames"]
        index._name_keys = state["nameKeys"]
        index._name_starts = state["nameStarts"]
        index._odd_tags = state["oddTags"]
        index._free = state["free"]
        # Derived, so not stored: one dict pass is cheaper than its pickle.
        index._by_tag = {
            key: slot for slot, key in enumerate(index._tag_keys) if key is not None
        }
        for side, saved in (
            (index._names, state["names"]),
            (index._tags, state["tags"]),
        ):
            side.prefixes, side.grams, side.dense = saved
        return index

    def stats(self) -> dict[str, int]:
        """Return the index size for logging."""

        sides = (*self._names.all_lists, *self._tags.all_lists)
        return {
            "players": len(self),
            "nameTrigrams": len(self._names.grams),
            "tagTrigrams": len(self._tags.grams),
            "listEntries": sum(len(v) for lists in sides for v in lists.values()),
        }
