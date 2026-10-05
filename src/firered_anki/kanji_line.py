"""The kanji line of a card, with the game's spaces put back.

The analysis writes each sentence "as a native would write it", and is not
steady about the game's spaces: it kept them all in a quarter of the
sentences, some in a few, none in the rest. A list or two clauses with no
punctuation between them need theirs (プラスパワー　ディフェンダー　スピーダー),
so the spaces cannot simply go. They are all put back instead: the kanji line
is laid against the kana sentence, character by character, and gets a space
wherever the game has one. Then the two lines read chunk for chunk.

Kana must match kana. A kanji takes one of the readings the analysis gave it
somewhere in the deck, or failing that any one to seven kana. A comma or full
stop the analysis added matches nothing, and stands in for the space it was
put at. Of the ways the two lines fit, the one is taken in which most of the
game's spaces fall between two characters of the kanji line (３びき　そろって
is ３匹　揃って, not 匹 read び). A line that cannot be laid against its
sentence (the analysis changed more than the script) is left as it is.
"""

import re
import sys
import unicodedata
from collections import defaultdict
from functools import lru_cache

from .tokenize import hira

SPACE = "　"
KANJI = re.compile(r"[一-鿿々〆ヶ]")
KANA = re.compile(r"[ぁ-ゖー]")
KANA_RUN = re.compile(r"[ぁ-ゖー]+")
ADDED = "、。，,"
LONGEST = 7  # kana one kanji may stand for (承 うけたまわ is 5)


def readings_from(analyses: dict) -> dict[str, set[str]]:
    """Kanji, or a run of them read as one (今日 きょう), → the readings the analysis gave."""
    out = defaultdict(set)
    for a in analyses.values():
        for w in a.get("words", []):
            for pair in (w.get("char_readings") or "").split("·"):
                if len(p := pair.split()) == 2 and KANJI.search(p[0]):
                    out[p[0]].add(hira(p[1]))
    return out


def _fold(text: str) -> str:
    """For comparing only: one script of kana, one width of letters and marks."""
    return "".join(n if len(n := unicodedata.normalize("NFKC", c)) == 1 else c for c in hira(text))


def _align(k: str, s: str, cuts: set, readings: dict) -> list[tuple[int, bool]] | None:
    """For each character of k: where in s it begins, and whether it is a mark
    the analysis added. None if the two do not match. `cuts`: where in s the
    game has a space."""
    sys.setrecursionlimit(max(sys.getrecursionlimit(), 4 * (len(k) + len(s)) + 1000))
    longest_key = max(map(len, readings), default=1)

    @lru_cache(maxsize=None)
    def best(i: int, j: int) -> tuple[int, tuple] | None:
        """The best fit of k[i:] to s[j:]: its score, and the step taken here
        (characters of k, characters of s)."""
        if i == len(k):
            return (0, ()) if j == len(s) else None
        c, steps = k[i], []
        if j < len(s) and (c == s[j] or "ー" in (c, s[j]) and KANA.match(c) and KANA.match(s[j])):
            steps.append((1, 1, 0))  # the same character; しょーがない written しょうがない
        if c in ADDED:
            steps.append((1, 0, 0))
        if KANJI.match(c):
            for n in range(min(longest_key, len(k) - i), 0, -1):  # a run read as one first
                steps += [(n, len(r), 1) for r in readings.get(k[i:i + n], ()) if s.startswith(r, j)]
            steps += [(1, n, 0) for n in range(1, min(LONGEST, len(s) - j) + 1) if KANA_RUN.fullmatch(s, j, j + n)]
        found = None
        for nk, ns, known in steps:
            if (rest := best(i + nk, j + ns)) is not None:
                score = rest[0] + known + (10 if i and ns and j in cuts else 0)
                if found is None or score > found[0]:
                    found = (score, (nk, ns))
        return found

    out, i, j = [], 0, 0
    while i < len(k):
        if (b := best(i, j)) is None:
            return None
        nk, ns = b[1]
        out += [(j, ns == 0)] * nk
        i, j = i + nk, j + ns
    return out if j == len(s) else None


def spaced(text: str, kanji: str, readings: dict) -> str:
    """The kanji line with a space wherever the sentence has one."""
    if SPACE not in text or not kanji:
        return kanji
    k = re.sub(r"[ 　]", "", kanji)
    chunks = text.split(SPACE)
    # Offsets in the sentence, spaces out, where a chunk begins.
    cuts, at = set(), 0
    for c in chunks[:-1]:
        at += len(c)
        cuts.add(at)
    fit = _align(_fold(k), _fold("".join(chunks)), frozenset(cuts), readings)
    if fit is None:
        return kanji
    out, done = [], {0}
    for c, (start, added) in zip(k, fit):
        # Once per space, and not where the analysis put a comma or a full stop.
        if start in cuts and start not in done:
            done.add(start)
            if not added:
                out.append(SPACE)
        out.append(c)
    return "".join(out)
