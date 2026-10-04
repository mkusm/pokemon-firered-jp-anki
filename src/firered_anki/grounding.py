"""Tie each analysed word to a JMdict entry, after the LLM pass.

The LLM picks entries from the tokenize stage's candidates. When the tokenizer
split a word wrongly its entry may not be among them (とおり read as the noun
通り, while the LLM rightly says 通る), so the LLM returns no ID. Here we look up
the LLM's own kanji and base form in JMdict and fill the ID when exactly one
entry has both. Cached LLM output stays untouched; this runs when results are
read.

The LLM's own IDs are checked against the whole dictionary, not only the
candidates: it links particles (の, が, を), which are never candidates, and
those IDs are real. An ID whose entry is another word (んだ tied to なのだ) is
replaced when exactly one entry has the word's base form. A sense number past
the five the entry table keeps (する 6, "to decide on") is checked against the
full entry.

Each word gains:
  _id_source      "llm" | "lookup" | None
  _sense_guessed  sense picked here by gloss overlap, not by the LLM
  _form_mismatch  the chosen entry lists neither the word's base nor its kanji
  _unknown_id     the LLM's ID is not in JMdict at all
  _bad_sense      the LLM's sense number is not in the entry
"""

import re
from functools import lru_cache

from jamdict import Jamdict

from .tokenize import GRAMMAR, MAX_SENSES, compact, hira

_jam = None


@lru_cache(maxsize=None)
def _lookup(key: str) -> tuple:
    global _jam
    _jam = _jam or Jamdict()
    res = _jam.lookup(key, strict_lookup=True, lookup_chars=False)
    return tuple((int(e.idseq), compact(e)) for e in res.entries)


@lru_cache(maxsize=None)
def full_entry(jid: int) -> dict | None:
    """The entry with this ID and all of its senses, or None if JMdict has no such ID."""
    global _jam
    _jam = _jam or Jamdict()
    res = _jam.lookup(f"id#{jid}")
    return compact(res.entries[0], max_senses=None) if res.entries else None


def sense_glosses(jid, n: int | None, entries: dict) -> list[str]:
    """The glosses of sense n, also when it is past the five the entry table keeps."""
    senses = (entries.get(str(jid)) or {}).get("s", [])
    if not n or n > len(senses):
        senses = (full_entry(jid) or {}).get("s", []) if jid and n else []
    return senses[n - 1]["g"] if n and 1 <= n <= len(senses) else []


def _forms(e: dict) -> tuple[set, set]:
    return set(e["k"]), {hira(r) for r in e["r"]}




def _stem(s: str) -> str:
    """解決する → 解決: JMdict lists する verbs under their noun."""
    return re.sub(r"(する|します|できる)$", "", s) or s


def _matches(e: dict, base: str, kanji: str, surface: str = "") -> bool:
    """The entry is this word, allowing endings the LLM left on the base
    (いれておく → 入れる) and する verbs. An entry for the form in the sentence
    counts too: ください has its own entry beside 下さる, ねえ beside ない."""
    ks, rs = _forms(e)
    b, k = _stem(base), _stem(kanji)
    if b in rs or (k and k in ks) or base in ks:
        return True
    # The whole word is the entry: 対する, 気がする, お願いします.
    if base in rs or (kanji and kanji in ks) or (surface and (hira(surface) in rs or surface in ks)):
        return True
    # A polite お or ご on a noun: おとどけもの is 届け物.
    if (b[:1] in "おご" and b[1:] in rs) or (k[:1] in "お御ご" and k[1:] in ks):
        return True
    # An interjection spelled shorter or longer: お！ is おっ, え…！ is ええ.
    if not kanji and any(_loose(r) == _loose(b) for r in rs):
        return True
    return any(b.startswith(r) for r in rs if len(r) >= 2) or any(
        k.startswith(x) for x in ks if k and len(x) >= 1 and not GRAMMAR.match(x))


def _loose(s: str) -> str:
    return re.sub(r"(.)\1+", r"\1", s.replace("っ", "").replace("ー", ""))


def _words(s: str) -> set:
    return set(re.findall(r"[a-z]+", s.lower())) - {"to", "a", "the", "of", "be", "one", "s"}


def _sense_scores(e: dict, gloss: str) -> list[int]:
    want = _words(gloss)
    return [len(want & _words(" ".join(s["g"]))) for s in e["s"]]


def _best_sense(e: dict, gloss: str) -> int:
    scores = _sense_scores(e, gloss)
    return scores.index(max(scores)) + 1 if scores else 1


def _by_forms(word: dict, base: str, kanji: str) -> dict:
    """Entries that have the word's base form, and its kanji if it has one."""
    hits = {}
    for key in filter(None, (kanji, base)):
        for eid, e in _lookup(key):
            ks, rs = _forms(e)
            b, k = _stem(base), _stem(kanji)
            if b in rs and (not k or k in ks or word.get("usually_kana")):
                hits[eid] = e
    return hits


def _link(word: dict, entries: dict, eid: int, e: dict) -> None:
    gloss = f"{word.get('in_context', '')} {word.get('literal', '')}"
    entries.setdefault(str(eid), e)
    word["jmdict_id"] = eid
    word["sense"] = 1 if len(e["s"]) == 1 else _best_sense(e, gloss)
    word["_sense_guessed"] = len(e["s"]) > 1
    word["_id_source"] = "lookup"
    word["_form_mismatch"] = False


def _check_sense(word: dict, e: dict) -> None:
    """A sense number past the ones the model was shown comes from its memory
    of the dictionary. Keep it if the full entry has it and no other sense is
    clearly closer to the model's own gloss: します "decide on" cited as する 7,
    "to be sensed", is する 6, and 〜てあげる cited as あげる 23, "to vomit", is
    24. One shared word is not enough to move it (いく 8, "to continue", glossed
    "go on doing", is not いく 1, "to go"). No number at all is fine: no listed
    sense fitted."""
    n = word.get("sense")
    word["_bad_sense"] = False
    if n is None or 1 <= n <= len(e["s"]):
        return
    full = full_entry(word["jmdict_id"])
    if not full or not 1 <= n <= len(full["s"]):
        word["sense"], word["_bad_sense"] = None, True
        return
    scores = _sense_scores(full, word.get("in_context") or "")
    if scores[n - 1] == 0 and max(scores) >= 2:
        word["sense"] = scores.index(max(scores)) + 1


def ground(word: dict, entries: dict) -> dict:
    """Fill or check one word's JMdict entry. Adds any looked-up entry to
    `entries` so later stages can show it."""
    kanji, base = word.get("kanji") or "", hira(word.get("base") or "")
    jid = word.get("jmdict_id")
    word["_sense_guessed"] = False

    if jid is not None:
        word["_id_source"] = "llm"
        e = entries.get(str(jid))
        if e is None:
            # An ID outside the candidates: fetch it via the word's forms.
            for key in filter(None, (kanji, base)):
                for eid, ce in _lookup(key):
                    entries.setdefault(str(eid), ce)
            e = entries.get(str(jid))
        if e is None and (full := full_entry(jid)):
            # A real entry under other forms.
            e = entries.setdefault(str(jid), {**full, "s": full["s"][:MAX_SENSES]})
        word["_unknown_id"] = e is None
        word["_form_mismatch"] = e is None or not _matches(e, base, kanji, word.get("surface") or "")
        if word["_form_mismatch"]:
            # Not this word. Take the one entry that is, if there is just one.
            hits = _by_forms(word, base, kanji)
            if len(hits) == 1:
                _link(word, entries, *next(iter(hits.items())))
        elif e is not None:
            _check_sense(word, e)
        return word

    word["_form_mismatch"] = False
    word["_id_source"] = None
    # Short grammar words (の, が, から) stay unlinked: several entries each,
    # and cards dedup them by base form.
    if not base or word.get("proper") or (GRAMMAR.match(base) and not kanji):
        return word
    hits = _by_forms(word, base, kanji)
    if len(hits) == 1:
        _link(word, entries, *next(iter(hits.items())))
    return word
