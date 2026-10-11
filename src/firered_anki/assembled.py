"""Sentences put together from text the model has already read.

The dump has some words on rows of their own that the game never shows
alone: a Pokédex category (たね, shown as たねポケモン), a nature (まじめ, shown
inside the trainer memo's sentence), a stat's name (とくぼう, shown inside
＊の　＊が　さがった！). The extract stage gives such a row the text the screen
shows and says what it was built from (`parts`: a frame, the slot of the
frame that is filled, and the pieces that fill it).

The analysis of such a sentence is not asked for. It is put together here,
the same way, from the cached answers for its parts: the frame's words with
the pieces' words in the slot, the frame's translation and kanji line with
the pieces' in the place of the ＊. A word keeps the record the model gave it
when it read the piece alone, so the card made from it keeps its key: asking
again about the longer sentence would be a new answer, with a boundary or a
sense that may differ.

A piece's answer is frozen by this: its bare text is no longer a sentence
of the deck, so no stage sends it again (`analyse redo` does, when the bare
word is listed). A frame is still a sentence of its own and can be redone;
what is built on it follows.
"""

import copy
import json
import re
from functools import lru_cache

import pandas as pd

from .paths import EXTRACT_OUT

VAR = "＊"
# A piece's translation often ends with a remark for the reader of the bare
# word: "Hardy (hard worker)", "Attack (stat)". Inside a sentence it goes;
# the word's own gloss on the card still has it. A recipe can ask to keep it
# (`remark`: a trainer class's "literally: shorts kid" is on no single word's
# card); then only a remark that just says what kind of word this is goes.
REMARK = re.compile(r"\s*\([^()]*\)\s*$")
KIND = re.compile(r"\s*\((?:trainer class|stat|Pokédex category|Pokémon category)\)\s*$")


@lru_cache(maxsize=None)
def recipes() -> dict[str, dict]:
    """sentence → what it is built from, for the sentences that are."""
    df = pd.read_parquet(EXTRACT_OUT, columns=["text", "parts"])
    return {t: json.loads(p) for t, p in zip(df["text"], df["parts"]) if p}


def recipe(text: str) -> dict | None:
    return recipes().get(text)


def is_label(text: str) -> bool:
    """Built without a frame: words side by side (たねポケモン), with nothing
    between them for the grammar pass or a note to explain."""
    rc = recipe(text)
    return bool(rc) and not rc["frame"]


def explained_as(text: str) -> str:
    """The sentence whose grammar answer and note a sentence carries: its
    own, or for a label the bare text it is built on. The model was asked
    about that text, and ポケモン after it changes nothing in what it said."""
    return recipe(text)["pieces"][0] if is_label(text) else text


def parts(rc: dict) -> list[str]:
    """The sentences whose cached answers the recipe needs: the frame first, if any."""
    return ([rc["frame"]] if rc["frame"] else []) + rc["pieces"]


def made(runs: list[dict], accepted: set, rank: dict) -> dict:
    """The `_run` of a built sentence: as good as the weakest of its parts'."""
    if not all(runs):
        return {}
    weakest = min(runs, key=lambda r: rank.get(r.get("model"), -1))
    stale = next((r for r in runs if r.get("prompt") not in accepted), None)
    return {"model": weakest.get("model"), "effort": weakest.get("effort"),
            "prompt": (stale or runs[0]).get("prompt"),
            "names": min((r.get("names") or 0) for r in runs), "assembled": True}


def _nth(s: str, n: int) -> int:
    at = -1
    for _ in range(n + 1):
        at = s.index(VAR, at + 1)
    return at


def _put(whole: str, n: int, piece: str, text: str, what: str) -> str:
    if whole.count(VAR) != text.count(VAR):
        raise ValueError(f"the frame's {what} has not one ＊ for each ＊ of {text}: {whole}")
    at = _nth(whole, n)
    return whole[:at] + piece + whole[at + 1:]


def join(text: str, rc: dict, records: list[dict]) -> dict:
    """The analysis of a built sentence, from its parts' (in the order of `parts`).
    Each word says where it comes from (`_from`: the part and its place there),
    which is what the caches kept per word are filed under."""
    from .splits import layout  # late: splits reads the analyses

    def words_of(part: str, rec: dict) -> list[dict]:
        return [{**copy.deepcopy(w), "_from": [part, pos]} for pos, w in enumerate(rec["words"])]

    frame = records[0] if rc["frame"] else None
    pieces = list(zip(rc["pieces"], records[1:] if frame else records))
    fill = [w for part, rec in pieces for w in words_of(part, rec)]
    kanji = "".join(rec["kanji"] for _, rec in pieces)
    drop = KIND if rc.get("remark") else REMARK
    english = " ".join(drop.sub("", rec["english"]) or rec["english"] for _, rec in pieces)
    if frame:
        own = words_of(rc["frame"], frame)
        # The slot's place among the frame's words: before the first word that starts after it.
        bare = rc["frame"].replace("　", "")
        slot = _nth(bare, rc["slot"])
        at = next((i for i, span in enumerate(layout(rc["frame"], frame["words"])) if span and span[0] > slot), len(own))
        fill = own[:at] + fill + own[at:]
        kanji = _put(frame["kanji"], rc["slot"], kanji, rc["frame"], "kanji line")
        english = _put(frame["english"], rc["slot"], english, rc["frame"], "translation")
    return {"text": text, "missing": False, "kanji": kanji, "english": english, "words": fill}
