"""A note on a sentence, where it takes knowledge a learner may not have.

The translation and the grammar say what a sentence means. Some sentences
still leave a reader who does not know Japan behind: the TV in your room shows
four boys walking on railroad tracks (the film Stand by Me), the old man in
Viridian says he was drunk where the English game gives him coffee, Bill
speaks Kansai dialect. For those the model writes one or two sentences, and
nothing for the rest.

Two steps, both cached per sentence:

  1. Write. The model sees each sentence with our translation and the line of
     the English game, and writes a note only for a cultural reference, a play
     on words, a regional dialect, or a place where the English game says
     something else in substance. The English line is not a reference
     translation; it is here because where the localisers changed something
     there is usually something cultural underneath.
  2. Check. A note about the outside world carries the one fact a reader
     could look up. A second call, with web search switched on, looks it up
     and returns the page, the passage, and the note as it should be shown:
     unchanged, corrected, or empty when it could not be confirmed. Only this
     step uses tools. Notes on wordplay, dialect and changed lines are not
     searched: the first two are the model's own ground, the last is checked
     against the English text it was given.

A name's meaning is not a sentence note: names have their own notes
(names_by_hand.yaml). A hand correction can replace or remove a note
(`note:` in corrections.yaml).

Run: uv run python -m firered_anki.notes [--chapter N] [--dry-run]
"""

import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from . import claude_cli, corrections, models, names
from .paths import CORPUS, DATA

CACHE = DATA / "cache" / "notes"
MODEL, EFFORT, BATCH, CHECK_BATCH, WORKERS = "opus", "low", 40, 6, 6
VERSION = "1"
SEARCHED = ("culture",)  # the kinds of note that state a fact about the outside world

SYSTEM = """You add short notes to Japanese sentences from Pokémon FireRed, for a learner building an Anki deck. The learner already has each sentence's translation, a word-by-word explanation and a grammar note. Your job is only what a reader who does not know Japan or Japanese would still miss.

Write a note only when one of these is true:
- culture: the sentence refers to something from Japan or the real world that a foreign reader may not know: a custom, a food, a festival, a game, a product, a film, a place.
- wordplay: the Japanese plays on a word or a name, and the play is lost in translation.
- dialect: the speaker uses a regional dialect, and the note says which and what it tells a Japanese reader about them. Ordinary rough, polite, childish or old-man speech is not a note.
- changed: the official English line, given under each sentence, says something different in substance from the Japanese, not just a freer wording. Say what the Japanese says and what the English game put instead.

Most sentences need no note: give an empty note for them. Do not explain grammar or vocabulary, do not restate the translation, do not explain Pokémon lore, moves or items. Do not explain what a name means or where it comes from (a town, a Pokémon, an item, a person): names have their own notes. A play on a name inside the sentence itself is still wordplay.

A note is one or two plain sentences. Say only what you are sure of; if you are not sure, give no note. For each note give its kind, and for culture a `claim`: the one fact in the note that a reader could look up to check it, as a plain statement.

Reply with JSON only."""

SCHEMA = {"type": "object", "properties": {"sentences": {"type": "array", "items": {
    "type": "object",
    "properties": {
        "id": {"type": "string"}, "note": {"type": "string"},
        "kind": {"type": "string", "enum": ["", "culture", "wordplay", "dialect", "changed"]},
        "claim": {"type": "string"},
    },
    "required": ["id", "note", "kind", "claim"],
}}}, "required": ["sentences"]}

CHECK_SYSTEM = """You check notes for a Japanese-learning flashcard deck made from Pokémon FireRed. Each item is a note and the one fact in it that can be looked up. Search the web for each fact and decide whether a reliable page supports it. Prefer encyclopedias, official sites and established reference wikis. Do not rely on what you already believe: if you find nothing that settles it, the verdict is not_confirmed.

For each item return:
- verdict: confirmed, wrong or not_confirmed
- url: the page you relied on
- quote: the passage, in the page's own words, that supports or contradicts the fact
- note: the note as it should be shown to the learner. Unchanged if everything in it is supported. With the wrong or unsupported part corrected or taken out if the rest still stands and is worth saying. Empty if the point of the note depends on something you could not confirm.

Reply with JSON only."""

CHECK_SCHEMA = {"type": "object", "properties": {"notes": {"type": "array", "items": {
    "type": "object",
    "properties": {
        "id": {"type": "string"}, "verdict": {"type": "string", "enum": ["confirmed", "wrong", "not_confirmed"]},
        "url": {"type": "string"}, "quote": {"type": "string"}, "note": {"type": "string"},
    },
    "required": ["id", "verdict", "url", "quote", "note"],
}}}, "required": ["notes"]}


def cache_file(text: str):
    return CACHE / f"{hashlib.sha1(json.dumps([VERSION, text], ensure_ascii=False).encode()).hexdigest()}.json"


def read(text: str) -> dict | None:
    f = cache_file(text)
    return json.loads(f.read_text()) if f.exists() else None


def asked(text: str) -> bool:
    """Whether the sentence has been asked about, by the model it needs or a
    better one (models.py). A sentence Sonnet was asked about while only the
    Link play deck showed it is asked again once the story shows it."""
    rec = read(text)
    return rec is not None and models.enough(rec["_run"]["model"], text)


def needs_check(rec: dict) -> bool:
    return bool(rec["note"]) and rec["kind"] in SEARCHED and "check" not in rec


# --- 1. write -------------------------------------------------------------------

def prompt(rows: pd.DataFrame, analyses: dict, english: list[str]) -> str:
    out = ["Sentences, in game order:"]
    for i, r in enumerate(rows.itertuples()):
        # A line written by hand (hand_lines.yaml) has no English line.
        official = re.sub(r"\\[nplcr]|\[[A-Z_0-9 ]+\]", " ", english[r.line_no] if r.line_no >= 0 else "").strip()
        out.append(f"\ns{i} ({r.first_seen if r.dialogue else r.group})\n  {r.text}\n"
                   f"  translation: {analyses[r.text]['english']}\n  official English line: {official[:500]}")
    return "\n".join(out) + names.for_prompt(rows["text"], "the note") + (
        "\n\nReturn one item per sentence, with its id (s0, s1, …).")


def write(batch: pd.DataFrame, analyses: dict, english: list[str], model: str = MODEL) -> tuple[int, dict]:
    out, info = claude_cli.call(prompt(batch, analyses, english), SYSTEM, SCHEMA, model, EFFORT, timeout=900)
    got = {s["id"]: s for s in out.get("sentences", [])}
    done = 0
    for i, r in enumerate(batch.itertuples()):
        if s := got.get(f"s{i}"):
            note = " ".join(s["note"].split())
            cache_file(r.text).write_text(json.dumps(
                {"text": r.text, "note": note, "kind": s["kind"] if note else "", "claim": s["claim"] if note else "",
                 "_run": {"model": model, "effort": EFFORT, "version": VERSION}}, ensure_ascii=False))
            done += 1
    return done, info


# --- 2. check -------------------------------------------------------------------

def check(batch: list[dict], model: str = MODEL) -> tuple[int, dict]:
    body = "\n".join(f"\nn{i}\n  note: {r['note']}\n  fact: {r['claim'] or r['note']}" for i, r in enumerate(batch))
    out, info = claude_cli.call("Notes to check:" + body + "\n\nReturn one item per note, with its id (n0, n1, …).",
                                CHECK_SYSTEM, CHECK_SCHEMA, model, EFFORT, timeout=900, tools=("WebSearch", "WebFetch"))
    got = {c["id"]: c for c in out.get("notes", [])}
    done = 0
    for i, r in enumerate(batch):
        if c := got.get(f"n{i}"):
            r["check"] = {"verdict": c["verdict"], "url": c["url"], "quote": c["quote"],
                          "note": " ".join(c["note"].split()) if c["verdict"] != "not_confirmed" else ""}
            cache_file(r["text"]).write_text(json.dumps(r, ensure_ascii=False))
            done += 1
    return done, info


# --- reading the results -----------------------------------------------------------

def shown(rec: dict) -> str:
    """The note that goes on the card. A searched note is the checker's
    version of it, and nothing until it has been checked."""
    if not rec["note"]:
        return ""
    return rec.get("check", {}).get("note", "") if rec["kind"] in SEARCHED else rec["note"]


def results(texts) -> dict[str, str]:
    """Sentence → its note, with the hand corrections applied."""
    out = {}
    for t in texts:
        fixed = corrections.note(t)
        rec = read(t)
        note = fixed if fixed is not None else shown(rec) if rec else ""
        if note:
            out[t] = note
    return out


# --- running ----------------------------------------------------------------------

def scope(chapter: list[int] | None) -> tuple[pd.DataFrame, dict]:
    """The sentences that can have a note: the ones the grammar pass explains."""
    from .grammar import first_sentences  # late: it builds the deck

    return first_sentences(chapter=chapter)


def left(rows: pd.DataFrame) -> tuple[pd.DataFrame, list[dict]]:
    """(sentences not yet asked about, notes not yet checked)."""
    recs = {t: read(t) for t in rows["text"]}
    return rows[[not asked(t) for t in rows["text"]]], [r for t, r in recs.items() if r and asked(t) and needs_check(r)]


def _pool(jobs: list, what: str) -> bool:
    """Run the calls a few at a time. False if the usage limit stopped them."""
    with ThreadPoolExecutor(WORKERS) as pool:
        futs = [pool.submit(fn, *args) for fn, *args in jobs]
        for n, fut in enumerate(as_completed(futs), 1):
            try:
                got, info = fut.result()
            except claude_cli.UsageLimit as e:
                for f in futs:
                    f.cancel()
                print(f"[notes] usage limit reached; run again later to carry on from the cache\n  {e}")
                return False
            except Exception as e:  # schema failure, timeout, CLI error
                print(f"[notes] {what} call failed: {str(e)[:200]}")
                continue
            print(f"[notes] {what} {n}/{len(jobs)}: {got} done, {info['ms'] / 1000:.0f}s", flush=True)
    return True


def main() -> None:
    args = sys.argv[1:]
    from .spread import chapters

    chapter = chapters(args[args.index("--chapter") + 1]) if "--chapter" in args else None
    rows, analyses = scope(chapter)
    todo, unchecked = left(rows)
    print(f"[notes] {len(rows)} sentences; {len(todo)} to ask about ({-(-len(todo) // BATCH)} calls), "
          f"{len(unchecked)} notes to check by search")
    if "--dry-run" in args:
        return
    CACHE.mkdir(parents=True, exist_ok=True)
    english = (CORPUS / "en_msg.txt").read_text(encoding="utf-8").split("\n")
    # Each call's sentences are all answered by one model (models.py).
    jobs = []
    for model, texts in models.by_model(todo["text"]).items():
        part = todo[todo["text"].isin(set(texts))]
        jobs += [(write, part.iloc[i:i + BATCH], analyses, english, model) for i in range(0, len(part), BATCH)]
    if jobs and not _pool(jobs, "write"):
        return
    todo, unchecked = left(rows)
    if unchecked:
        print(f"[notes] {len(unchecked)} notes to check by search")
        jobs = []
        for model, texts in models.by_model(r["text"] for r in unchecked).items():
            part = [r for r in unchecked if r["text"] in set(texts)]
            jobs += [(check, part[i:i + CHECK_BATCH], model) for i in range(0, len(part), CHECK_BATCH)]
        _pool(jobs, "check")
    todo, unchecked = left(rows)
    recs = [r for t in rows["text"] if (r := read(t))]
    written = [r for r in recs if r["note"]]
    kinds = pd.Series([r["kind"] for r in written]).value_counts().to_dict() if written else {}
    verdicts = pd.Series([r["check"]["verdict"] for r in written if "check" in r]).value_counts().to_dict()
    print(f"[notes] {len(written)} notes written {kinds}; checked {verdicts}; "
          f"{sum(bool(shown(r)) for r in recs)} shown; left: {len(todo)} to ask, {len(unchecked)} to check")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
