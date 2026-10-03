"""Give each use of a short grammar word its dictionary sense.

Particles, sentence endings and the copula do several jobs each: か asks a
question or means "or", が marks the subject or means "but". The analysis
mostly leaves them without a dictionary sense, and grounding does not look
them up, so every use of a particle landed on one card that showed whatever
its first sentence said. か "or" was taught twice and か the question marker
not at all.

This stage asks Claude about every such use: it sees the sentence with the
word marked, the translation, the gloss the analysis wrote, and the senses of
the kana-only JMdict entries with that reading, and answers with an entry and
a sense. The cards stage then makes one card per sense, at the first sentence
that has it.

Answers are cached per (sentence, position, word) and applied when analyses
are read.
Run: uv run python -m firered_anki.particles [--chapter N] [--dry-run]
"""

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import lru_cache

from jamdict import Jamdict

from . import claude_cli
from .grounding import GRAMMAR, full_entry
from .paths import DATA
from .spans import word_span
from .tokenize import MAX_SENSES, hira

CACHE = DATA / "cache" / "particles"
MODEL, EFFORT, BATCH, WORKERS = "opus", "low", 50, 8
VERSION = "1"  # part of the cache key

SYSTEM = """You match short Japanese grammar words from Pokémon FireRed to dictionary senses: particles, sentence endings, the copula, short interjections. One such word does several jobs (か asks a question, or means "or"; が marks the subject, or means "but"), and a learner's deck needs a card for each job.

First comes a table of JMdict entries with numbered senses. Then the items. Each item gives the sentence with the word in 【brackets】, the sentence's English, the gloss already written for the word, and the IDs of the entries that could be this word.

For each item answer with the entry ID and the sense number that fit this use in this sentence. Go by what the word does in the sentence, not by the wording of the gloss. Where the word is part of a longer pattern (ずに, ても, のだ), choose the sense that covers its job there. Answer entry 0 and sense 0 if none of the candidates is this word, or if no listed sense fits.

Reply with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"n": {"type": "integer"}, "entry": {"type": "integer"}, "sense": {"type": "integer"}},
        "required": ["n", "entry", "sense"],
    }}},
    "required": ["items"],
}

_jam = None


def wanted(w: dict) -> bool:
    """A short kana grammar word with no dictionary sense yet."""
    return (not w.get("proper") and not w.get("kanji") and "＊" not in w["surface"]
            and bool(GRAMMAR.match(hira(w["base"]))) and not (w.get("jmdict_id") and w.get("sense")))


@lru_cache(maxsize=None)
def candidates(base: str) -> tuple[int, ...]:
    """Entries read this way that are written in kana: the particle か, not 蚊."""
    global _jam
    _jam = _jam or Jamdict()
    out = []
    for e in _jam.lookup(base, strict_lookup=True, lookup_chars=False).entries:
        in_kana = any("usually written using kana" in m for s in e.senses for m in s.misc)
        if not e.kanji_forms or in_kana:
            out.append(int(e.idseq))
    return tuple(out)


def cache_file(text: str, pos: int, word: dict):
    raw = json.dumps([VERSION, text, pos, word["surface"]], ensure_ascii=False)
    return CACHE / f"{hashlib.sha1(raw.encode()).hexdigest()}.json"


def apply(text: str, pos: int, word: dict, entries: dict) -> None:
    """Give the word the entry and sense chosen for this use."""
    if not wanted(word):
        return
    f = cache_file(text, pos, word)
    if not f.exists():
        return
    got = json.loads(f.read_text())
    full = full_entry(got["entry"]) if got["entry"] else None
    if not full or not 1 <= got["sense"] <= len(full["s"]):
        # Asked, and no listed sense fits (と naming what something becomes).
        # The cards stage keeps such a use off the cards of the listed senses.
        word["_no_sense"] = True
        return
    entries.setdefault(str(got["entry"]), {**full, "s": full["s"][:MAX_SENSES]})
    word.update(jmdict_id=got["entry"], sense=got["sense"], _id_source="particle",
                _sense_guessed=False, _form_mismatch=False)


def entry_line(eid: int) -> str:
    e = full_entry(eid)
    pos = sorted({p for s in e["s"] for p in s["pos"]})
    senses = " ".join(f"{i}) {'; '.join(s['g'])}" for i, s in enumerate(e["s"], 1))
    return f"{eid}: {'・'.join(e['k'] + e['r'])} [{', '.join(pos)}] {senses}"


def prompt(batch: list[tuple], analyses: dict) -> str:
    used: dict[int, None] = {}
    lines = []
    for n, (text, pos, w) in enumerate(batch):
        cands = list(dict.fromkeys(([w["jmdict_id"]] if w.get("jmdict_id") else []) + list(candidates(hira(w["base"])))))
        used.update(dict.fromkeys(cands))
        a, b = word_span(text, analyses[text]["words"], pos) or (0, 0)
        marked = f"{text[:a]}【{text[a:b]}】{text[b:]}" if b else text
        lines.append(f"{n}. {marked}\n   english: {analyses[text]['english']}\n"
                     f"   word: {w['surface']} — {w.get('in_context', '')}   candidates: {', '.join(map(str, cands))}")
    table = "\n".join(entry_line(eid) for eid in used)
    return (f"JMdict entries:\n{table}\n\nItems:\n" + "\n".join(lines)
            + "\n\nReturn the entry and sense for each item, with its number.")


def ask(batch: list[tuple], text: str) -> int:
    out, _ = claude_cli.call(text, SYSTEM, SCHEMA, MODEL, EFFORT, timeout=900)
    done = 0
    for it in out.get("items", []):
        if 0 <= it["n"] < len(batch):
            text, pos, w = batch[it["n"]]
            cache_file(text, pos, w).write_text(json.dumps(
                {"word": w["surface"], "entry": it["entry"], "sense": it["sense"]}, ensure_ascii=False))
            done += 1
    return done


def pending(chapter: int | None) -> tuple[list[tuple], dict]:
    from .cards import known_words, prepare  # late: cards imports analyse, which imports this module

    deck, _, analyses, _ = prepare(offline_merge=True)
    shown = deck[deck["card_order"].notna()].sort_values("card_order").drop_duplicates("text")
    if chapter is not None:
        shown = shown[shown["chapter"] == chapter]
    known = known_words()
    items = [(t, pos, w) for t in shown["text"] if t in analyses for pos, w in enumerate(analyses[t]["words"])
             if wanted(w) and w["base"] not in known and w["surface"] not in known
             and candidates(hira(w["base"])) and not cache_file(t, pos, w).exists()]
    # One word at a time: the same table and the same judgement through a batch.
    items.sort(key=lambda x: hira(x[2]["base"]))
    return items, analyses


def main() -> None:
    args = sys.argv[1:]
    chapter = int(args[args.index("--chapter") + 1]) if "--chapter" in args else None
    CACHE.mkdir(parents=True, exist_ok=True)
    items, analyses = pending(chapter)
    batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    print(f"{len(items)} uses of short grammar words to ask about, {len(batches)} calls")
    if "--dry-run" in args or not batches:
        return
    # The prompts are built here, not in the workers: the dictionary is an
    # SQLite connection and may only be used from the thread that opened it.
    prompts = [prompt(b, analyses) for b in batches]
    done = 0
    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            futs = [pool.submit(ask, b, p) for b, p in zip(batches, prompts)]
            for n, fut in enumerate(as_completed(futs), 1):
                try:
                    done += fut.result()
                except claude_cli.UsageLimit:
                    raise
                except Exception as e:  # schema failure, timeout, CLI error
                    print(f"  call failed: {str(e)[:200]}")
                    continue
                print(f"  call {n}/{len(batches)} done", flush=True)
    except claude_cli.UsageLimit as e:
        print(f"usage limit reached; re-run later to continue from the cache\n  {e}")
    print(f"answered {done}/{len(items)}; now re-run: uv run python -m firered_anki.cards")


if __name__ == "__main__":
    main()
