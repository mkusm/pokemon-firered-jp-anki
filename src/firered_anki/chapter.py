"""Bring one chapter up to date: every stage that calls the model, in order.

After anything changes in a chapter (a prompt, a rule, the order of lines), a
fixed series of stages has to run before the chapter is consistent again:

  1. analyse rerun N       sentences the chapter shows that are not on the current analysis
  2. splits --chapter N    strings now cut two ways
  3. names --chapter N     sentences that call a name something else
  4. sense_pick            dictionary links found by lookup
  5. cards                 the steps above change words, and so which sentences are shown
  6. particles --chapter N particle uses left without a sense
  7. grammar run --chapter N  shown sentences with no grammar yet
  8. cards

A step can bring a new sentence into the chapter, so the series repeats until
nothing is left to ask. Each stage runs as its own process and works out the
deck for itself, exactly as when run by hand: nothing is shared between them.

This calls the model. Run it for the chapter you were asked to do.

Run: uv run python -m firered_anki.chapter N [--dry-run]
       --dry-run  only say what is left to do
"""

import math
import subprocess
import sys

from . import analyse, grammar, names, particles, sense_pick, splits
from .paths import ROOT

MAX_ROUNDS = 4


def steps(n: int) -> list[list[str]]:
    return [
        ["analyse", "rerun", str(n)],
        ["splits", "--chapter", str(n)],
        ["names", "--chapter", str(n)],
        ["sense_pick"],
        ["cards"],
        ["particles", "--chapter", str(n)],
        ["grammar", "run", "--chapter", str(n)],
        ["cards"],
    ]


def left(n: int) -> dict[str, int]:
    """What the chapter still needs from the model, counted the way each stage counts it."""
    from .cards import known_words, prepare  # late: cards imports the stages

    deck, _, analyses, _ = prepare(offline_merge=True)
    shown = deck[deck["card_order"].notna() & (deck["chapter"] == n)].sort_values("card_order").drop_duplicates("text")
    shown = shown[shown["text"].map(lambda t: t in analyses)]
    current = [t for t in shown["text"] if analyse.current(analyse.MAIN.name, t, analyse.RERUN.model)]
    sentences = shown[[not names.listed(g, lb) for g, lb in zip(shown["group"], shown["label"])]]  # not a bare name
    gram = grammar.results(sentences["text"], analyses)
    decided = splits.load()
    return {
        "sentences to re-analyse": len(shown) - len(current),
        "strings cut two ways, not decided": sum(s not in decided for s in splits.conflicts(current, analyses)),
        "sentences that cut a fixed string differently": len(splits.violations(current, analyses)),
        "translations that misname something": sum(bool(names.misses(t, analyses[t])) for t in sentences["text"]),
        "breakdowns that misname something": sum(bool(names.grammar_misses(t, analyses[t], g)) for t, g in gram.items()),
        "dictionary links to check": sum(
            w.get("_id_source") == "lookup" and not sense_pick.cache_file(t, w).exists()
            for t, a in analyses.items() for w in a["words"]),
        "particle uses to ask about": len(particles.unasked(shown["text"], analyses, known_words())),
        "sentences with no grammar": sum(not grammar.answered(t, analyses) for t in sentences["text"]),
        "_shown": len(shown),
    }


def report(n: int, todo: dict[str, int]) -> int:
    open_ = {k: v for k, v in todo.items() if v and not k.startswith("_")}
    print(f"[chapter {n}] {todo['_shown']} sentences shown; " + (
        "nothing left to ask" if not open_ else "left: " + "; ".join(f"{v} {k}" for k, v in open_.items())))
    return sum(open_.values())


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1 or not args[0].isdigit():
        sys.exit(__doc__.strip().splitlines()[-2].strip())
    n = int(args[0])
    todo = left(n)
    total = report(n, todo)
    if "--dry-run" in sys.argv or not total:
        if todo["sentences to re-analyse"] or todo["sentences with no grammar"]:
            print(f"[chapter {n}] about {math.ceil(todo['sentences to re-analyse'] / analyse.RERUN.batch)} analysis calls "
                  f"and {math.ceil(todo['sentences with no grammar'] / grammar.BATCH)} grammar calls to start with")
        if todo["sentences to re-analyse"] and todo["particle uses to ask about"]:
            print(f"[chapter {n}] the particle uses are counted on the old analysis; the new one picks most of them itself")
        return
    for round_no in range(1, MAX_ROUNDS + 1):
        for step in steps(n):
            print(f"\n== round {round_no}: {' '.join(step)}", flush=True)
            done = subprocess.run([sys.executable, "-m", f"firered_anki.{step[0]}", *step[1:]], cwd=ROOT)
            if done.returncode:
                sys.exit(f"[chapter {n}] stopped: {' '.join(step)} failed. Fix that and run this again; "
                         "it carries on from the caches.")
        print()
        before, total = total, report(n, left(n))
        if not total:
            subprocess.run([sys.executable, "-m", "firered_anki.build"], cwd=ROOT)
            print(f"[chapter {n}] settled")
            return
        if total >= before:
            break
    sys.exit(f"[chapter {n}] not settled: a round did not bring the count down "
             "(a usage limit, or an answer the model keeps giving). See the lines above.")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
