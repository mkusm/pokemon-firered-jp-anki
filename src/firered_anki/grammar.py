"""Grammar pass: the patterns that sit between words.

The word analysis explains each word alone. It cannot say that the と in
しゅじんこうと　なって belongs to なる, or that ことに　なります is one pattern.
This pass looks at each sentence again and returns its grammar patterns and a
clause-by-clause breakdown. The cards stage puts them on the word cards:
each card shows the patterns its word takes part in.

Answers are cached per sentence and its word list, so a sentence the analysis
later splits differently is asked again.

Everything a card shows about a pattern was written for that card's sentence.
There is no shared index of patterns: one was tried, with a fixed heading and
plain-words line per pattern name, and a name that covers several uses (から
for "from" and for "because") then put a line on the card that contradicted
the explanation under it. The price is that one pattern can be spelled two
ways on different cards (〜せる and 〜させる).

  uv run python -m firered_anki.grammar run [--chapter N | --cards N | --sentences N]   # default: the whole deck
  uv run python -m firered_anki.grammar dry-run [first words of a sentence] [count]
"""

import hashlib
import json
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import escape

import pandas as pd

from . import claude_cli, corrections
from .analyse import MAIN, load, results as analysis_results
from .paths import DATA

CACHE = DATA / "cache" / "grammar"
DRY_OUT = DATA / "dryrun" / "grammar.json"
# Opus, like the analysis it builds on. Sonnet was given the translation and
# still glossed a phrase its own way where it read the sentence differently:
# とうろくして　つかいます came out as "registering … / it is used" under the
# translation "using a registered Key Item". An answer from another model
# counts as missing and is asked again.
# 80 sentences tried at 10, 40 and 80 a call: 10 and 40 found the same patterns,
# 80 dropped some near the end of the batch, with either model.
MODEL, EFFORT, BATCH, WORKERS = "opus", "low", 40, 8
VERSION = "2"  # part of the cache key: 2 asks for explanations without grammar terms

SYSTEM = """You explain the grammar of Japanese sentences from Pokémon FireRed to a learner who is building an Anki deck. The words have already been explained one by one. Your job is what sits between words.

For each sentence return:

1. structure: the sentence cut into its clauses or phrases, in order, each with the Japanese exactly as in the sentence (kana, with its spaces) and a short English gloss. Together the chunks must cover the whole sentence.

2. patterns: the grammar patterns a learner most needs in order to read this sentence. At most three, most useful first; fewer is fine, and none is fine for a sentence with nothing to explain. For each:
   - name: the pattern as a Japanese grammar-book heading, in dictionary form, with 〜 for the slot: 〜ことになる, 〜となる, 〜てしまう, 〜ながら, 〜たり. Rules for names:
     • Always Japanese with 〜. Never an English description as the name, with one exception: a verb, or a longer phrase ending in a verb, that describes the noun right after it has no Japanese form to name, so call it exactly "verb + noun".
     • Name the standard pattern, not the variant in the sentence. おる for いる, じゃ for だ, ちゃう for てしまう, てる for ている are all named by the standard form (〜ている, 〜だ, 〜てしまう); say in the explanation that this is a dialect, old-man or contracted form.
     • Add a short English tag in brackets only when one Japanese form has several jobs: 〜て (linking), 〜で (how), 〜と (quoting), 〜に (purpose), 〜られる (passive), 〜られる (potential), 〜の (nominalizer), 〜こと (nominalizer). の and こと are different patterns: name the one that is in the sentence.
     • The same pattern must get exactly the same name every time.
   - span: the text that shows the pattern, copied exactly from the sentence, including its spaces. One continuous stretch only, and the whole pattern: for 〜ていく give the て-form verb together with いく (ひらかれて　いきます), not いきます alone. If a pattern appears twice, give the first.
   - words: the words from the given word list that take part in it
   - explanation: one or two short sentences in plain everyday words, for a beginner who knows no grammar terms. Say what the pattern does here, and always end with what that piece of the sentence means, written as Japanese = 'English': "てしまう after a verb says the thing happened completely, often to your regret. わすれてしまった = 'I went and forgot'."
     Do not use grammar terms in the explanation: no "marks", "modifies", "manner", "basis", "nominalizer", "clause", "volitional", "auxiliary". If one cannot be avoided, say what it means in a few words. No slashes between alternatives: pick the one that fits.

Include: verb and adjective endings that carry meaning (〜ている, 〜てしまう, 〜たい, 〜られる, 〜ましょう, 〜てください), set patterns across words (〜ことになる, 〜かもしれない, 〜なければならない, 〜こともある), particles used in a way a learner would misread (と marking the result of なる, で for how something is done, に for purpose), how clauses are linked, relative clauses, and marked speech style (old-man じゃ, rough ぜ and ぞ) the first time it matters.
Leave out: the plain uses of は, が, を, の, も, へ, です, ます and よ; greetings and other fixed phrases that are just vocabulary (はじめまして, ようこそ); lists with や; and anything that is only the dictionary meaning of a word.

Reply with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {"sentences": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"},
            "structure": {"type": "array", "items": {
                "type": "object",
                "properties": {"jp": {"type": "string"}, "en": {"type": "string"}},
                "required": ["jp", "en"],
            }},
            "patterns": {"type": "array", "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"}, "span": {"type": "string"},
                    "words": {"type": "array", "items": {"type": "string"}},
                    "explanation": {"type": "string"},
                },
                "required": ["name", "span", "words", "explanation"],
            }},
        },
        "required": ["id", "structure", "patterns"],
    }}},
    "required": ["sentences"],
}


def cache_file(text: str, words: list[dict]):
    raw = json.dumps([VERSION, text, [w["surface"] for w in words]], ensure_ascii=False)
    return CACHE / f"{hashlib.sha1(raw.encode()).hexdigest()}.json"


def prompt(rows, analyses) -> str:
    out = ["Sentences, in game order:"]
    for i, r in enumerate(rows.itertuples()):
        a = analyses[r.text]
        where = r.first_seen if r.dialogue else r.group
        spk = f", speaker: {r.speaker}" if r.speaker else ""
        words = " / ".join(w["surface"] for w in a["words"])
        out.append(f"\ns{i} ({where}{spk})\n  {r.text}\n  kanji: {a['kanji']}\n  english: {a['english']}\n  words: {words}")
    return "\n".join(out) + "\n\nReturn one item per sentence, with its id (s0, s1, …)."


# --- running -------------------------------------------------------------------

def ask(batch: pd.DataFrame, analyses: dict) -> tuple[int, dict]:
    out, info = claude_cli.call(prompt(batch, analyses), SYSTEM, SCHEMA, MODEL, EFFORT, timeout=900)
    got = {s["id"]: s for s in out.get("sentences", [])}
    done = 0
    for i, r in enumerate(batch.itertuples()):
        s = got.get(f"s{i}")
        if s:
            cache_file(r.text, analyses[r.text]["words"]).write_text(json.dumps(
                {"text": r.text, "structure": s["structure"], "patterns": s["patterns"],
                 "_run": {"model": MODEL, "effort": EFFORT, "version": VERSION}}, ensure_ascii=False))
            done += 1
    return done, info


def run(rows: pd.DataFrame, analyses: dict) -> None:
    """Fill the cache for rows: deck rows in card order, one per sentence."""
    CACHE.mkdir(parents=True, exist_ok=True)
    rows = rows[rows["text"].map(lambda t: t in analyses)]

    def done(t: str) -> bool:
        f = cache_file(t, analyses[t]["words"])
        return f.exists() and json.loads(f.read_text())["_run"]["model"] == MODEL

    for _ in range(3):  # a call can fail or skip a sentence: go round again
        todo = rows[~rows["text"].map(done)]
        if todo.empty:
            break
        batches = [todo.iloc[i:i + BATCH] for i in range(0, len(todo), BATCH)]
        print(f"[grammar] {len(rows)} sentences, {len(todo)} to do, {len(batches)} calls")
        with ThreadPoolExecutor(WORKERS) as pool:
            futs = [pool.submit(ask, b, analyses) for b in batches]
            for n, fut in enumerate(as_completed(futs), 1):
                try:
                    got, info = fut.result()
                except claude_cli.UsageLimit as e:
                    for f in futs:
                        f.cancel()
                    print(f"[grammar] usage limit reached; re-run later to continue from the cache\n  {e}")
                    return
                except Exception as e:  # schema failure, timeout, CLI error
                    print(f"[grammar] call failed: {str(e)[:200]}")
                    continue
                print(f"[grammar] {n}/{len(batches)} calls, {got} sentences, {info['ms'] / 1000:.0f}s")
    left = int((~rows["text"].map(done)).sum())
    print(f"[grammar] done: {len(rows) - left}/{len(rows)} sentences cached")


def first_sentences(n_cards: int | None = None, n_sentences: int | None = None,
                    chapter: int | None = None) -> tuple[pd.DataFrame, dict]:
    """The deck's sentences in card order: all of them, one chapter's, the
    first n, or up to the one the nth card is on."""
    from .cards import CARDS_OUT, prepare  # late: cards imports this module

    deck, _, analyses, _ = prepare(offline_merge=True)
    placed = deck[deck["card_order"].notna()].sort_values("card_order").drop_duplicates("text")
    if chapter is not None:
        placed = placed[placed["chapter"] == chapter]
    if n_cards:
        cut = pd.read_parquet(CARDS_OUT, columns=["order"])["order"].iloc[n_cards - 1]
        placed = placed[placed["card_order"] <= cut]
    return placed.head(n_sentences) if n_sentences else placed, analyses


# --- reading the results -----------------------------------------------------------

def tidy(name: str) -> str:
    name = name.replace("～", "〜").replace("（", " (").replace("）", ")")
    return " ".join(name.split())


def cached(texts, analyses: dict):
    """(sentence, cached answer) for the sentences that have one."""
    for t in texts:
        f = cache_file(t, analyses[t]["words"]) if t in analyses else None
        if f and f.exists():
            yield t, json.loads(f.read_text())


def locate(text: str, span: str) -> tuple[int, int] | None:
    """Where the span is in the sentence. The model sometimes drops or adds a
    space, so fall back to matching with the spaces taken out."""
    i = text.find(span) if span else -1
    if i >= 0:
        return i, i + len(span)
    index = [n for n, ch in enumerate(text) if ch != "　"]
    bare = span.replace("　", "")
    j = text.replace("　", "").find(bare) if bare else -1
    return (index[j], index[j + len(bare) - 1] + 1) if j >= 0 else None


def results(texts, analyses: dict) -> dict[str, dict]:
    """Cached grammar per sentence, with the hand corrections applied. Each
    pattern also gets where its span is (`at`: start and end in the sentence,
    or None if it is not in it)."""
    out = {}
    for t, g in cached(texts, analyses):
        corrections.apply_grammar(t, g)
        for p in g["patterns"]:
            p.update(name=tidy(p["name"]), at=locate(t, p["span"]))
        out[t] = g
    return out


def word_patterns(g: dict, text: str, words: list[dict], pos: int) -> list[dict]:
    """The sentence's patterns that the word at `pos` takes part in. A surface
    that is in the sentence twice (two の) counts only where it is inside the span."""
    surface = words[pos]["surface"]
    start = 0
    for w in words[:pos + 1]:  # the same walk as the romaji: each word after the last
        i = text.find(w["surface"], start)
        if i >= 0:
            at, start = (i, i + len(w["surface"])), i + len(w["surface"])
        else:
            at = None
    twice = sum(w["surface"] == surface for w in words) > 1
    out = []
    for p in g["patterns"]:
        if surface not in p["words"]:
            continue
        inside = at and p["at"] and at[0] < p["at"][1] and p["at"][0] < at[1]
        if inside or not twice:
            out.append(p)
    return out


def esc(text: str) -> str:
    return escape(text, quote=False)


def pattern_html(p: dict) -> str:
    """The pattern's name as the model gave it for this sentence, the part of
    the sentence, and the explanation."""
    return (f'<div class="pat"><span class="pname">{esc(p["name"])}</span> '
            f'<span class="pspan">{esc(p["span"])}</span>'
            f'<div class="pexp">{esc(p["explanation"])}</div></div>')


def breakdown_html(g: dict) -> str:
    """The sentence chunk by chunk, then all of its patterns."""
    chunks = "".join(f'<tr><td class="cj">{esc(c["jp"])}</td><td class="ce">{esc(c["en"])}</td></tr>'
                     for c in g["structure"])
    return f'<table class="chunks">{chunks}</table>' + "".join(pattern_html(p) for p in g["patterns"])


# --- dry run ---------------------------------------------------------------------

def dry_run(start: str, count: int) -> None:
    df, entries = load()
    deck = df[df["teaches"]].sort_values("order").drop_duplicates("text").reset_index(drop=True)
    first = deck.index[deck["text"].str.startswith(start)][0]
    rows = deck.iloc[first:first + count]
    analyses = analysis_results(MAIN.name, rows["text"], entries)
    out, info = claude_cli.call(prompt(rows, analyses), SYSTEM, SCHEMA, MODEL, EFFORT, timeout=600)
    got = {s["id"]: s for s in out["sentences"]}
    saved = []
    for i, r in enumerate(rows.itertuples()):
        g = got.get(f"s{i}")
        saved.append({"text": r.text, "kanji": analyses[r.text]["kanji"],
                      "english": analyses[r.text]["english"], **(g or {})})
        print(f"\n━━ {r.text}\n   {analyses[r.text]['english']}")
        if not g:
            print("   (no answer)")
            continue
        for c in g["structure"]:
            print(f"   [{c['jp']}] {c['en']}")
        for p in g["patterns"]:
            ok = "" if p["span"] in r.text else "  ⚠ span not in sentence"
            print(f"   • {p['name']}  ⟨{p['span']}⟩  words: {' + '.join(p['words'])}{ok}\n       {p['explanation']}")
    DRY_OUT.parent.mkdir(parents=True, exist_ok=True)
    DRY_OUT.write_text(json.dumps(saved, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n{len(rows)} sentences, {info['ms'] / 1000:.0f}s")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up in redirected logs
    args = sys.argv[1:]
    if args[:1] == ["run"]:
        n = int(args[2]) if len(args) > 2 else None
        run(*first_sentences(n_cards=n if args[1:2] == ["--cards"] else None,
                             n_sentences=n if args[1:2] == ["--sentences"] else None,
                             chapter=n if args[1:2] == ["--chapter"] else None))
    elif args[:1] == ["dry-run"]:
        dry_run(args[1] if len(args) > 1 else "これから　はじまる", int(args[2]) if len(args) > 2 else 10)
    else:
        sys.exit("usage: python -m firered_anki.grammar run [--chapter N | --cards N | --sentences N] | dry-run [sentence start] [count]")
