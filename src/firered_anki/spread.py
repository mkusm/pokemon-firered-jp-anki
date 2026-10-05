"""Interleave non-dialogue lines (menus, battle text, Pokédex…) with the story.

Story dialogue is fixed in play order. The game is cut into chapters, one per
gym badge. A non-dialogue line becomes available where it is first seen and
must be released before its chapter ends. Within a chapter:

  1. Keep only lines that teach a word neither earlier chapters nor this
     chapter's dialogue teach. Pick them greedily, most valuable first (value
     = new words weighted by corpus frequency), so each word needs one line.
  2. Spread the kept lines evenly over the chapter's dialogue; at each slot
     release the available line with the most value per word, so short common
     UI words come before long help-style prose.
  3. Lines that teach nothing stay out of the sequence (order = NaN). They are
     still candidates for extra example sentences.

A pinned line is not spread. The line that names a Pokémon or an item goes
exactly where the thing is first met, unless dialogue before it has already
shown the name; then it is an ordinary line.

`words` is any set of hashable word keys per sentence: tokenizer lemmas for a
preview in the order stage, (jmdict_id, sense) pairs in the cards stage.
"""

import math
from collections import Counter

import pandas as pd

# Each chapter ends with this map_order entry.
CHAPTER_ENDS = [
    "PewterCity_Gym",
    "CeruleanCity_Gym",
    "VermilionCity_Gym",
    "CeladonCity_Gym",
    "FuchsiaCity_Gym",
    "SaffronCity_Gym",
    "CinnabarIsland_Gym",
    "ViridianCity_Gym",
    "PokemonLeague_HallOfFame",
]  # then the postgame
# What comes after the story is not spread, and is two decks of its own: the
# Help menu, and link play (the Union Room, trading, Mystery Gift, mail and
# the easy-chat word lists). Each has a chapter number, after the postgame's,
# so that the model stages can be run on one of them alone.
HELP = len(CHAPTER_ENDS) + 1
LINK_PLAY = len(CHAPTER_ENDS) + 2
# Which deck a row is in, by its bucket in the order stage.
DECK_OF = {"help": "help", "end": "link", "unplaced": "link"}  # everything else: "story"


def chapter_of(ranks: pd.Series, rank_of: dict[str, int]) -> pd.Series:
    bounds = [rank_of[e] for e in CHAPTER_ENDS] + [math.inf]
    return ranks.map(lambda r: next(i for i, b in enumerate(bounds) if r <= b))


def spread(df: pd.DataFrame, words: pd.Series, pin: dict | None = None) -> pd.Series:
    """df: rows already sorted by first-seen position, with columns `dialogue`
    (bool), `chapter` (int) and `tail` (bool: end-of-deck rows, kept as they are).
    `pin`: row → the word that row is there to teach at its own place.
    Returns the new order (float, NaN for lines that teach nothing)."""
    pin = pin or {}
    freq = Counter(w for ws in words for w in ws)
    # fsum: the same total whatever order the set is walked in, so that two
    # lines of equal value do not swap places from one run to the next.
    value = lambda ws, known: math.fsum(math.sqrt(freq[w]) for w in ws - known)

    out: dict = {}
    known: set = set()
    shown: set = set()  # by dialogue and pinned lines so far, whose place in the story is exact
    pos = 0
    for ch in sorted(df.loc[~df["tail"], "chapter"].unique()):
        rows = df[(df["chapter"] == ch) & ~df["tail"]]
        dia = rows[rows["dialogue"]]
        ui = rows[~rows["dialogue"]]
        dia_words = set().union(*words[dia.index]) if len(dia) else set()

        # Pinned lines whose word nothing has shown by the time they come up.
        pinned = []
        for i, is_dia in zip(rows.index, rows["dialogue"]):
            if not is_dia and (i not in pin or pin[i] in shown):
                continue
            if not is_dia:
                pinned.append(i)
            shown |= words[i]
        pinned_words = set().union(*words[pinned]) if pinned else set()
        pinned = set(pinned)

        # 1. Which UI lines to keep.
        covered = known | dia_words | pinned_words
        pool = {i: words[i] for i in ui.index if i not in pinned and words[i] - covered}
        kept = set()
        while pool:
            best = max(pool, key=lambda i: value(pool[i], covered))
            if not (pool[best] - covered):
                break
            kept.add(best)
            covered |= pool.pop(best)

        # A kept line is available after the dialogue sentences before it.
        avail = {}
        n_dia = 0
        for i, is_dia in zip(rows.index, rows["dialogue"]):
            if is_dia:
                n_dia += 1
            elif i in kept or i in pinned:
                avail[i] = n_dia

        # 2. Spread evenly: after each dialogue sentence, release lines while
        # the running quota allows, most valuable available first.
        rate = len(kept) / max(len(dia), 1)
        credit = 0.0
        waiting: list = []
        pending = sorted((i for i in avail if i in kept), key=avail.get)
        due = [i for i in avail if i in pinned]  # in first-seen order
        seen_now = set(known)

        def release_upto(n_done: int, force: bool = False) -> None:
            nonlocal credit, pos
            while due and avail[due[0]] <= n_done:
                out[due[0]] = pos
                pos += 1
                seen_now.update(words[due.pop(0)])
            while pending and avail[pending[0]] <= n_done:
                waiting.append(pending.pop(0))
            while waiting and (credit >= 1 or force):
                # Per word, so short common lines (たたかう, どうぐ) come early and
                # long manual-style prose fills the later slots.
                best = max(
                    waiting,
                    key=lambda i: (value(words[i], seen_now) / max(len(words[i]), 1), -avail[i]),
                )
                waiting.remove(best)
                out[best] = pos
                pos += 1
                seen_now.update(words[best])
                credit -= 1

        release_upto(0)
        for k, i in enumerate(dia.index, start=1):
            out[i] = pos
            pos += 1
            seen_now.update(words[i])
            credit += rate
            release_upto(k)
            if not waiting:
                # Nothing was available: don't save up a burst for later (the
                # opening sequence runs before any menu text can be seen).
                credit = min(credit, 1.0)
        waiting.extend(pending)  # the chapter's deadline: release the rest
        pending.clear()
        release_upto(len(dia), force=True)

        known |= set().union(*words[rows.index]) if len(rows) else set()

    for i in df.index[df["tail"]]:
        out[i] = pos
        pos += 1
    return pd.Series(out, dtype=float).reindex(df.index)
