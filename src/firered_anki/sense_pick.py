"""Pick the JMdict sense for words whose entry was filled in by lookup.

When the analysis returns a word without a JMdict ID, grounding finds the
entry from the word's kanji and base form. If that entry has several senses,
grounding can only guess one by gloss overlap, and usually lands on sense 1
(教えてください got 下さる "to give", not "to kindly do for one"). This stage
asks Claude to choose: it sees the sentence, the word, the gloss the analysis
wrote for it, and the entry's senses.

Answers are cached per (sentence, word, entry) and applied when analyses are
read, so the sense also decides which occurrences share a card.
Run: uv run python -m firered_anki.sense_pick [--dry-run]
"""

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import claude_cli
from .paths import DATA

CACHE = DATA / "cache" / "sense_pick"
MODEL, EFFORT, BATCH, WORKERS = "sonnet", "low", 60, 3

SYSTEM = """You match Japanese words from Pokémon FireRed to dictionary senses. For each item you get the sentence, one word in it, a short gloss of what the word means there, and the numbered senses of its JMdict entry. Choose the sense number that fits the word in that sentence. For an auxiliary use (〜てください, 〜ている, 〜てみる) choose the auxiliary sense, not the main verb's. Reply with JSON only."""

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
    raw = json.dumps([text, word["surface"], word["jmdict_id"]], ensure_ascii=False)
    return CACHE / f"{hashlib.sha1(raw.encode()).hexdigest()}.json"


def apply(text: str, word: dict, entries: dict) -> None:
    """Replace a guessed sense with the picked one, if there is one."""
    if not word.get("_sense_guessed"):
        return
    f = cache_file(text, word)
    if not f.exists():
        return
    sense = json.loads(f.read_text())["sense"]
    if 1 <= sense <= len(entries.get(str(word["jmdict_id"]), {}).get("s", [])):
        word["sense"] = sense
        word["_sense_guessed"] = False
        word["_sense_picked"] = True


def pending() -> tuple[list[tuple[str, dict]], dict]:
    from .analyse import MAIN, load, results  # late: analyse imports this module

    df, entries = load()
    res = results(MAIN.name, df["text"].unique(), entries)
    items = [(t, w) for t, r in res.items() for w in r["words"] if w.get("_sense_guessed")]
    return items, entries


def ask(batch: list[tuple[str, dict]], entries: dict) -> int:
    lines = []
    for n, (text, w) in enumerate(batch):
        e = entries[str(w["jmdict_id"])]
        head = "・".join(e["k"] or e["r"])
        senses = "  ".join(f"{i}) {'; '.join(s['g'])}" for i, s in enumerate(e["s"], 1))
        lines.append(f"{n}. sentence: {text}\n   word: {w['surface']} ({head}) — {w.get('in_context', '')}\n   senses: {senses}")
    out, _ = claude_cli.call("\n".join(lines) + "\n\nReturn the sense number for each item.",
                             SYSTEM, SCHEMA, MODEL, EFFORT, timeout=600)
    done = 0
    for it in out.get("items", []):
        if 0 <= it["n"] < len(batch):
            text, w = batch[it["n"]]
            if 1 <= it["sense"] <= len(entries[str(w["jmdict_id"])]["s"]):
                cache_file(text, w).write_text(json.dumps(
                    {"word": w["surface"], "entry": w["jmdict_id"], "sense": it["sense"]}, ensure_ascii=False))
                done += 1
    return done


def main() -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    items, entries = pending()
    batches = [items[i:i + BATCH] for i in range(0, len(items), BATCH)]
    print(f"{len(items)} words with a guessed sense, {len(batches)} calls")
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
