"""Check the JMdict entry, and pick its sense, for words linked by lookup.

When the analysis returns a word without a JMdict ID, grounding finds the
entry from the word's kanji and base form. Two things can be wrong with that:

- The entry is a different word that only sounds the same: ＰＰ (Power Points,
  read ピーピー) matched ピーピー "peep; chirp", バイバイ matched 売買 "trade".
- The entry is right but has several senses, and grounding can only guess one
  by gloss overlap (教えてください got 下さる "to give", not "to kindly do").

This stage asks Claude about every such word: it sees the sentence, the word,
the gloss the analysis wrote for it, and the entry's senses, and answers with
the sense number, or 0 if the entry is not this word. A 0 removes the link,
unless the analysis gave the word the entry's own kanji: then the entry is
right and the model only found no listed sense to fit (compound parts such as
ざる 猿 in ぶたざる), so the link stays with grounding's guessed sense.

Answers are cached per (sentence, word, entry) and applied when analyses are
read, so the sense also decides which occurrences share a card.
Run: uv run python -m firered_anki.sense_pick [--dry-run]
"""

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import claude_cli
from .grounding import _stem
from .paths import DATA

CACHE = DATA / "cache" / "sense_pick"
MODEL, EFFORT, BATCH, WORKERS = "sonnet", "low", 60, 3

VERSION = "2"  # part of the cache key: 2 added the "0 = not this word" answer

SYSTEM = """You match Japanese words from Pokémon FireRed to dictionary senses. For each item you get the sentence, one word in it, a short gloss of what the word means there, and the numbered senses of a JMdict entry that was found by the word's reading.

Answer with the sense number that fits the word in that sentence. For an auxiliary use (〜てください, 〜ている, 〜てみる) choose the auxiliary sense, not the main verb's.

Answer 0 if the entry is not this word at all: a different word that only sounds the same, or an abbreviation, letter or name read the same way. ＰＰ "Power Points" is not ピーピー "peep; chirp"; バイバイ "bye-bye" is not 売買 "trade". Do not answer 0 just because the gloss is worded differently from the dictionary.

Reply with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": {
        "type": "object",
        "properties": {"n": {"type": "integer"}, "sense": {"type": "integer"}},
        "required": ["n", "sense"],
    }}},
    "required": ["items"],
}


def cache_file(text: str, word: dict):
    raw = json.dumps([VERSION, text, word["surface"], word["jmdict_id"]], ensure_ascii=False)
    return CACHE / f"{hashlib.sha1(raw.encode()).hexdigest()}.json"


def apply(text: str, word: dict, entries: dict) -> None:
    """Apply the answer for a lookup-linked word: set its sense, or unlink it."""
    if word.get("_id_source") != "lookup":
        return
    f = cache_file(text, word)
    if not f.exists():
        return
    sense = json.loads(f.read_text())["sense"]
    entry = entries.get(str(word["jmdict_id"]), {})
    if sense == 0:
        # A homophone has a different spelling. When the kanji the analysis
        # gave is the entry's own (ひ 火 in ひのうま, ざる 猿 in ぶたざる), the
        # entry is right and only its listed senses did not fit: keep the link.
        kanji = _stem(word.get("kanji") or "")
        if kanji and kanji in {_stem(k) for k in entry.get("k", [])}:
            return
        word.update(jmdict_id=None, sense=None, _id_source=None, _sense_guessed=False, _unlinked=True)
    elif 1 <= sense <= len(entry.get("s", [])):
        word["sense"] = sense
        word["_sense_guessed"] = False
        word["_sense_picked"] = True


def pending() -> tuple[list[tuple[str, dict]], dict]:
    from .analyse import MAIN, load, results  # late: analyse imports this module

    df, entries = load()
    res = results(MAIN.name, df["text"].unique(), entries)
    items = [(t, w) for t, r in res.items() for w in r["words"]
             if w.get("_id_source") == "lookup" and not cache_file(t, w).exists()]
    return items, entries


def ask(batch: list[tuple[str, dict]], entries: dict) -> int:
    lines = []
    for n, (text, w) in enumerate(batch):
        e = entries[str(w["jmdict_id"])]
        head = "・".join(e["k"] or e["r"])
        senses = "  ".join(f"{i}) {'; '.join(s['g'])}" for i, s in enumerate(e["s"], 1))
        lines.append(f"{n}. sentence: {text}\n   word: {w['surface']} ({head}) — {w.get('in_context', '')}\n   senses: {senses}")
    out, _ = claude_cli.call("\n".join(lines) + "\n\nReturn the sense number for each item, or 0 if the entry is a different word.",
                             SYSTEM, SCHEMA, MODEL, EFFORT, timeout=600)
    done = 0
    for it in out.get("items", []):
        if 0 <= it["n"] < len(batch):
            text, w = batch[it["n"]]
            if 0 <= it["sense"] <= len(entries[str(w["jmdict_id"])]["s"]):
                cache_file(text, w).write_text(json.dumps(
                    {"word": w["surface"], "entry": w["jmdict_id"], "sense": it["sense"]}, ensure_ascii=False))
                done += 1
    return done


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    items, entries = pending()
    batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    print(f"{len(items)} lookup-linked words to check, {len(batches)} calls")
    if "--dry-run" in sys.argv or not batches:
        return
    done = 0
    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            futs = [pool.submit(ask, b, entries) for b in batches]
            for n, fut in enumerate(as_completed(futs), 1):
                done += fut.result()
                print(f"  call {n}/{len(batches)} done", flush=True)
    except claude_cli.UsageLimit as e:
        print(f"usage limit reached; re-run later to continue from the cache\n  {e}")
    print(f"picked {done}/{len(items)}; now re-run: uv run python -m firered_anki.cards")


if __name__ == "__main__":
    main()
