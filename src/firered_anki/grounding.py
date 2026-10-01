"""Tie each analysed word to a JMdict entry, after the LLM pass.

The LLM picks entries from the tokenize stage's candidates. When the tokenizer
split a word wrongly its entry may not be among them (とおり read as the noun
通り, while the LLM rightly says 通る), so the LLM returns no ID. Here we look up
the LLM's own kanji and base form in JMdict and fill the ID when exactly one
entry has both. Cached LLM output stays untouched; this runs when results are
read.

Each word gains:
  _id_source      "llm" | "lookup" | None
  _sense_guessed  sense picked here by gloss overlap, not by the LLM
  _form_mismatch  the chosen entry lists neither the word's base nor its kanji
"""

import re
from functools import lru_cache

from jamdict import Jamdict

from .tokenize import compact, hira

_jam = None


@lru_cache(maxsize=None)
def _lookup(key: str) -> tuple:
    global _jam
    _jam = _jam or Jamdict()
    res = _jam.lookup(key, strict_lookup=True, lookup_chars=False)
    return tuple((int(e.idseq), compact(e)) for e in res.entries)


def _forms(e: dict) -> tuple[set, set]:
    return set(e["k"]), {hira(r) for r in e["r"]}


GRAMMAR = re.compile(r"^[\u3041-\u309f]{1,2}$")  # の, が, から, です…


def _stem(s: str) -> str:
    """解決する → 解決: JMdict lists する verbs under their noun."""
    return re.sub(r"(する|します|できる)$", "", s) or s


def _matches(e: dict, base: str, kanji: str) -> bool:
    """The entry is this word, allowing endings the LLM left on the base
    (いれておく → 入れる) and する verbs."""
    ks, rs = _forms(e)
    b, k = _stem(base), _stem(kanji)
    if b in rs or (k and k in ks) or base in ks:
        return True
    return any(b.startswith(r) for r in rs if len(r) >= 2) or any(
        k.startswith(x) for x in ks if k and len(x) >= 1 and not GRAMMAR.match(x))


def _words(s: str) -> set:
    return set(re.findall(r"[a-z]+", s.lower())) - {"to", "a", "the", "of", "be", "one", "s"}


def _best_sense(e: dict, gloss: str) -> int:
    want = _words(gloss)
    scores = [len(want & _words(" ".join(s["g"]))) for s in e["s"]]
    return scores.index(max(scores)) + 1 if scores else 1


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
        if e is not None:
            word["_form_mismatch"] = not _matches(e, base, kanji)
        else:
            word["_form_mismatch"] = True
        return word

    word["_form_mismatch"] = False
    word["_id_source"] = None
    # Short grammar words (の, が, から) stay unlinked: several entries each,
    # and cards dedup them by base form.
    if not base or word.get("proper") or (GRAMMAR.match(base) and not kanji):
        return word
    hits = {}
    for key in filter(None, (kanji, base)):
        for eid, e in _lookup(key):
            ks, rs = _forms(e)
            b, k = _stem(base), _stem(kanji)
            if b in rs and (not k or k in ks or word.get("usually_kana")):
                hits[eid] = e
    if len(hits) == 1:
        (eid, e), = hits.items()
        entries.setdefault(str(eid), e)
        word["jmdict_id"] = eid
        if len(e["s"]) == 1:
            word["sense"] = 1
        else:
            word["sense"] = _best_sense(e, f"{word.get('in_context', '')} {word.get('literal', '')}")
            word["_sense_guessed"] = True
        word["_id_source"] = "lookup"
    return word
