"""Fixed word boundaries: one decision per string, not one per sentence.

The analysis decides where words begin and end sentence by sentence, and
cannot see what it chose elsewhere. So the same string comes out as one word
in some sentences and as several in others (ポケモンセンター, ように,
たいせつな), and each rerun of a chapter splits a fifth of it differently.
A prose rule cannot fix that: the fix is a list of decisions.

This stage
  1. finds the strings that are split two ways,
  2. asks Claude about each, twice and independently: always one word, always
     split (and how), or "depends on the meaning" (とは, でも). A string is
     only fixed when both answers agree; otherwise it is left to the rule,
  3. writes the decisions to `splits.yaml`, each with its reason,
  4. re-analyses the sentences that break a decision.

The analysis is shown the decisions that apply to its batch, so later runs
and later chapters follow them.

Run: uv run python -m firered_anki.splits [--chapter N] [--dry-run]
"""

import json
import sys
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache

import yaml

from . import claude_cli, names
from .paths import ROOT
from .spread import chapters

FILE = ROOT / "splits.yaml"
MODEL, EFFORT, BATCH = "opus", "low", 25
DEPENDS = "depends"
HEADER = """# Fixed word boundaries, written by: uv run python -m firered_anki.splits
# One decision per string, so that it is split the same way in every sentence.
#   as: the string itself (one word), its pieces joined with " + ", or
#       "depends" (the meaning decides; left to the analysis's own rule)
# A string is fixed only when two independent decisions agreed. You can edit
# a line by hand; the stage never changes a string that is already here.

"""

SYSTEM = """You settle how recurring strings are cut into words in a Japanese learner's Anki deck made from Pokémon FireRed. The deck's analysis decides word boundaries one sentence at a time, so the same string has come out as one word in some sentences and as several words in others. For each string, decide once.

Answer for each string:
- "one": always one word.
- "split": always the same several words. Give the pieces; together they must spell the string exactly.
- "depends": the right cut depends on what the string means in the sentence, or the two readings are different words that only look alike. Then the analysis decides each time.

Principles, in this order:
1. A name of a place, item, move, facility or game feature is one word (ポケモンセンター, わざマシン).
2. Never cut inside a conjugated form: when what would be left before an ending cannot stand on its own, it is one word (はけば).
3. A run of particles, sentence endings and the copula is one word only when the dictionary has an entry for it and one of its pieces has no sense of its own that fits: かな, のだ, んだ, んです, のか, なの and their old-man forms (のじゃ, んじゃ) are one word. Where each piece does a job it has a sense for, they are separate: には is に + は, にも is に + も.
4. A set expression that means something its words do not add up to is one word (というわけだ, かもしれない). A phrase whose meaning is just its words together is separate words (においが　する).
5. A noun followed by a particle is two words (ことが is こと + が).
6. When none of these decides, choose the way most sentences in the deck already cut it, so that fewer cards change.

Use "depends" sparingly: only when you can name two different meanings that need different cuts (でも "but" against で + も "even at"). Give a reason in one short sentence.

Reply with JSON only."""

SCHEMA = {
    "type": "object",
    "properties": {"strings": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "n": {"type": "integer"}, "decision": {"enum": ["one", "split", "depends"]},
            "pieces": {"type": "array", "items": {"type": "string"}}, "reason": {"type": "string"},
        },
        "required": ["n", "decision", "pieces", "reason"],
    }}},
    "required": ["strings"],
}


@lru_cache(maxsize=None)
def load() -> dict[str, dict]:
    return (yaml.safe_load(FILE.read_text(encoding="utf-8")) or {}) if FILE.exists() else {}


def pieces_of(decision: dict, string: str) -> list[str] | None:
    """The fixed pieces, or None when the meaning decides."""
    return None if decision["as"] == DEPENDS else decision["as"].split(" + ")


def layout(text: str, words: list[dict]) -> list[tuple[int, int] | None]:
    """Where each word is in the sentence with its spaces taken out."""
    bare = text.replace("　", "")
    out, start = [], 0
    for w in words:
        s = w["surface"].replace("　", "")
        i = bare.find(s, start) if s else -1
        out.append((i, i + len(s)) if i >= 0 else None)
        if i >= 0:
            start = i + len(s)
    return out


# --- 1. strings split two ways ---------------------------------------------------

def conflicts(texts, analyses: dict) -> dict[str, dict]:
    """string → {whole: [(sentence, word)], split: [(sentence, words)]}."""
    whole = defaultdict(list)
    for t in texts:
        for w in analyses[t]["words"]:
            whole[w["surface"].replace("　", "")].append((t, w))
    found: dict[str, dict] = {}
    named = names.load()
    for t in texts:
        words, at = analyses[t]["words"], layout(t, analyses[t]["words"])
        for n in (2, 3, 4):
            for i in range(len(words) - n + 1):
                run = at[i:i + n]
                if None in run or any(run[k][1] != run[k + 1][0] for k in range(n - 1)):
                    continue
                s = "".join(w["surface"].replace("　", "") for w in words[i:i + n])
                # A Pokémon or item name has its own card whichever way it is
                # cut (names.py), so its cut needs no decision.
                if len(s) >= 2 and s in whole and names.bare(s) not in named:
                    found.setdefault(s, {"whole": whole[s], "split": []})["split"].append((t, words[i:i + n]))
    return found


# --- 2. decisions ----------------------------------------------------------------

def _entries(string: str) -> str:
    from .tokenize import _jam  # late: loading the dictionary is slow

    out = []
    for e in _jam.lookup(string, strict_lookup=True, lookup_chars=False).entries[:2]:
        head = "・".join([k.text for k in e.kanji_forms][:2] + [k.text for k in e.kana_forms][:2])
        out.append(f"{head}: {'; '.join(g.text for g in e.senses[0].gloss[:3])}")
    return " | ".join(out) or "no entry for the whole string"


def question(n: int, string: str, c: dict) -> str:
    cuts = Counter(" + ".join(w["surface"] for w in ws) for _, ws in c["split"])
    lines = [f"{n}. {string}   (one word in {len(c['whole'])} sentences; cut in {len(c['split'])}: "
             + ", ".join(f"{k} ×{v}" for k, v in cuts.most_common(3)) + ")",
             f"   dictionary: {_entries(string)}"]
    for t, w in c["whole"][:3]:
        lines.append(f"   one word: {t[:60]} — “{w.get('in_context', '')}”")
    for t, ws in c["split"][:3]:
        lines.append(f"   cut:      {t[:60]} — " + " + ".join(f"{w['surface']} “{w.get('in_context', '')}”" for w in ws))
    return "\n".join(lines)


def decide(found: dict[str, dict]) -> dict[str, dict]:
    """Ask twice; keep a decision only where the two answers agree."""
    strings = sorted(found)
    batches = [strings[i:i + BATCH] for i in range(0, len(strings), BATCH)]
    prompts = ["\n\n".join(question(n, s, found[s]) for n, s in enumerate(b))
               + "\n\nReturn one item per string, with its number." for b in batches]
    with ThreadPoolExecutor(max(1, 2 * len(batches))) as pool:
        futs = [[pool.submit(claude_cli.call, p, SYSTEM, SCHEMA, MODEL, EFFORT, 900) for _ in (1, 2)] for p in prompts]
        answers = [[f.result()[0] for f in pair] for pair in futs]
    out = {}
    for b, (first, second) in zip(batches, answers):
        got = [{a["n"]: a for a in ans.get("strings", [])} for ans in (first, second)]
        for n, s in enumerate(b):
            a, c = got[0].get(n), got[1].get(n)
            ex = (found[s]["whole"] or [(None,)])[0][0]
            ok = lambda x: x and (x["decision"] != "split" or "".join(x["pieces"]) == s)
            if not ok(a) or not ok(c):
                continue  # no usable answer: ask again next time
            same = a["decision"] == c["decision"] and (a["decision"] != "split" or a["pieces"] == c["pieces"])
            if not same:
                out[s] = {"as": DEPENDS, "why": f"two independent decisions disagreed: {_say(a, s)} / {_say(c, s)}", "example": ex}
            else:
                out[s] = {"as": _say(a, s), "why": a["reason"], "example": ex}
    return out


def _say(a: dict, s: str) -> str:
    return s if a["decision"] == "one" else " + ".join(a["pieces"]) if a["decision"] == "split" else DEPENDS


def save(decisions: dict[str, dict]) -> None:
    FILE.write_text(HEADER + yaml.safe_dump(dict(sorted(decisions.items())), allow_unicode=True, sort_keys=False, width=1000),
                    encoding="utf-8")
    load.cache_clear()


# --- 3. the analysis is told, and checked ------------------------------------------

def for_prompt(texts) -> str:
    """The decisions that apply to these sentences, as lines for the analysis."""
    bare = [t.replace("　", "") for t in texts]
    lines = []
    for s, d in load().items():
        ps = pieces_of(d, s)
        if ps and any(s in b for b in bare):
            lines.append(f"- {s}: " + ("one word" if len(ps) == 1 else " + ".join(ps)))
    if not lines:
        return ""
    return ("\n\nFixed word boundaries in this deck. Wherever one of these strings is a word or a run of words, "
            "cut it exactly this way:\n" + "\n".join(lines))


def violations(texts, analyses: dict) -> dict[str, list[str]]:
    """sentence → the fixed strings it cuts differently."""
    pins = {s: ps for s, d in load().items() if (ps := pieces_of(d, s))}
    out = defaultdict(list)
    for t in texts:
        bare = t.replace("　", "")
        hit = [s for s in pins if s in bare]
        if not hit:
            continue
        words = analyses[t]["words"]
        at = layout(t, words)
        starts = {a[0]: i for i, a in enumerate(at) if a}
        ends = {a[1]: i for i, a in enumerate(at) if a}
        for s in hit:
            i = bare.find(s)
            while i >= 0:
                # Only where the string is made of whole words: たいせつな inside
                # the name たいせつなもの is not a use of たいせつな.
                if i in starts and i + len(s) in ends and ends[i + len(s)] >= starts[i]:
                    got = [w["surface"].replace("　", "") for w in words[starts[i]:ends[i + len(s)] + 1]]
                    if got != pins[s]:
                        out[t].append(s)
                i = bare.find(s, i + 1)
    return dict(out)


# --- the stage ---------------------------------------------------------------------

def scope(chapter: int | None):
    from . import analyse
    from .cards import prepare

    deck, _, analyses, _ = prepare(offline_merge=True)
    return in_scope(deck, analyses, chapter), analyses


def in_scope(deck, analyses: dict, chapter: int | None) -> list[str]:
    """The sentences whose word boundaries must agree: every shown sentence up
    to and including the chapter. A string cut one way in the first chapter
    and another way in the second is the same disagreement as inside one
    chapter, so a later chapter's run can send an earlier sentence back."""
    from . import analyse

    shown = deck[deck["card_order"].notna()].sort_values("card_order").drop_duplicates("text")
    if chapter is not None:
        shown = shown[shown["chapter"] <= chapter]
    # Only sentences on the current analysis: the older ones follow other rules.
    return [t for t in shown["text"] if t in analyses and analyse.current(analyse.MAIN.name, t, analyse.RERUN.model)]


def main() -> None:
    args = sys.argv[1:]
    # Strings are checked up to and including a chapter: of several, the last.
    chapter = max(chapters(args[args.index("--chapter") + 1])) if "--chapter" in args else None
    texts, analyses = scope(chapter)
    found = conflicts(texts, analyses)
    known = dict(load())
    new = {s: c for s, c in found.items() if s not in known}
    print(f"[splits] {len(texts)} sentences; {len(found)} strings cut two ways, {len(new)} not decided yet")
    if "--dry-run" in args:
        return
    if new:
        made = decide(new)
        known.update(made)
        save(known)
        kinds = Counter("one word" if d["as"] == s else "left to the rule" if d["as"] == DEPENDS else "split" for s, d in made.items())
        print(f"[splits] decided {len(made)} of {len(new)}: {dict(kinds)} → {FILE.name}")
    bad = violations(texts, analyses)
    print(f"[splits] {len(bad)} sentences cut a fixed string differently")
    if bad:
        from . import analyse

        analyse.redo_texts(set(bad))
        texts, analyses = scope(chapter)
        left = violations(texts, analyses)
        print(f"[splits] after re-analysis: {len(left)} sentences still differ")
        for t, ss in list(left.items())[:10]:
            print(f"    {', '.join(ss)}: {t[:50]}")
    print("[splits] now run: sense_pick, particles, grammar run, cards")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    try:
        main()
    except claude_cli.UsageLimit as e:
        # Nothing is half-written: decisions are saved only once all are made.
        print(f"[splits] usage limit reached; run again later to carry on\n  {e}")
