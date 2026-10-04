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
  8. notes --chapter N     sentences not yet asked for a note, notes not yet checked
  9. cards

A step can bring a new sentence into the chapter, so the series repeats until
nothing is left to ask. Each stage runs as its own process and works out the
deck for itself, exactly as when run by hand: nothing is shared between them.
What is left is counted in a fresh process too, after every round: the stages
write files this process has already read (splits.yaml).

Word boundaries are fixed across chapters, so a chapter's run can send a
sentence of an earlier chapter back to the model. The earlier chapters are
therefore checked again afterwards, and settled if anything was reopened.

This calls the model. Run it for the chapter you were asked to do.

Run: uv run python -m firered_anki.chapter N [--dry-run]
       --dry-run  only say what is left to do
"""

import json
import math
import subprocess
import sys

from . import analyse, grammar, names, notes, particles, sense_pick, splits
from .paths import ROOT

MAX_ROUNDS = 8


def steps(n: int) -> list[list[str]]:
    return [
        ["analyse", "rerun", str(n)],
        ["splits", "--chapter", str(n)],
        ["names", "--chapter", str(n)],
        ["sense_pick"],
        ["cards"],
        ["particles", "--chapter", str(n)],
        ["grammar", "run", "--chapter", str(n)],
        ["notes", "--chapter", str(n)],
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
    explained = sentences[sentences["text"].map(lambda t: len(analyses[t]["words"]) > 1)]["text"]
    gram = grammar.results(sentences["text"], analyses)
    decided, bounded = splits.load(), splits.in_scope(deck, analyses, n)
    return {
        "sentences to re-analyse": len(shown) - len(current),
        "strings cut two ways, not decided": sum(s not in decided for s in splits.conflicts(bounded, analyses)),
        "sentences that cut a fixed string differently": len(splits.violations(bounded, analyses)),
        "translations that misname something": sum(bool(names.misses(t, analyses[t])) for t in sentences["text"]),
        "breakdowns that misname something": sum(bool(names.grammar_misses(t, analyses[t], g)) for t, g in gram.items()),
        "dictionary links to check": sum(
            w.get("_id_source") == "lookup" and not sense_pick.cache_file(t, w).exists()
            for t, a in analyses.items() for w in a["words"]),
        "particle uses to ask about": len(particles.unasked(shown["text"], analyses, known_words())),
        "sentences with no grammar": sum(not grammar.answered(t, analyses) for t in explained),
        "sentences not asked for a note": sum(notes.read(t) is None for t in explained),
        "notes not checked": sum(bool(r := notes.read(t)) and notes.needs_check(r) for t in explained),
        "_shown": len(shown),
    }


def report(n: int, todo: dict[str, int]) -> int:
    open_ = {k: v for k, v in todo.items() if v and not k.startswith("_")}
    print(f"[chapter {n}] {todo['_shown']} sentences shown; " + (
        "nothing left to ask" if not open_ else "left: " + "; ".join(f"{v} {k}" for k, v in open_.items())))
    return sum(open_.values())


def status(n: int) -> dict[str, int]:
    """left(n), worked out by a fresh process."""
    done = subprocess.run([sys.executable, "-m", "firered_anki.chapter", str(n), "--status"],
                          cwd=ROOT, capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"[chapter {n}] could not read the chapter's state:\n{done.stderr[-600:]}")
    return json.loads(done.stdout.strip().splitlines()[-1])


def settle(n: int, total: int) -> None:
    """Run the series for one chapter until nothing is left."""
    for round_no in range(1, MAX_ROUNDS + 1):
        for step in steps(n):
            print(f"\n== chapter {n}, round {round_no}: {' '.join(step)}", flush=True)
            done = subprocess.run([sys.executable, "-m", f"firered_anki.{step[0]}", *step[1:]], cwd=ROOT)
            if done.returncode:
                sys.exit(f"[chapter {n}] stopped: {' '.join(step)} failed. Fix that and run this again; "
                         "it carries on from the caches.")
        print()
        before, total = total, report(n, status(n))
        if not total:
            return
        if total >= before:
            sys.exit(f"[chapter {n}] not settled: a round did not bring the count down "
                     "(a usage limit, or an answer the model keeps giving). See the lines above.")
    sys.exit(f"[chapter {n}] not settled after {MAX_ROUNDS} rounds, though each one helped. Run this again.")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) != 1 or not args[0].isdigit():
        sys.exit(__doc__.strip().splitlines()[-2].strip())
    n = int(args[0])
    if "--status" in sys.argv:
        print(json.dumps(left(n), ensure_ascii=False))
        return
    todo = left(n)
    total = report(n, todo)
    if "--dry-run" in sys.argv:
        if todo["sentences to re-analyse"] or todo["sentences with no grammar"]:
            print(f"[chapter {n}] about {math.ceil(todo['sentences to re-analyse'] / analyse.RERUN.batch)} analysis calls "
                  f"and {math.ceil(todo['sentences with no grammar'] / grammar.BATCH)} grammar calls to start with")
        if todo["sentences to re-analyse"] and todo["particle uses to ask about"]:
            print(f"[chapter {n}] the particle uses are counted on the old analysis; the new one picks most of them itself")
        return
    if total:
        settle(n, total)
    # The chapters before it: settled already, unless this run reopened a sentence of theirs.
    for _ in range(3):
        reopened = [(m, t) for m in range(n) if (t := report(m, status(m)))]
        if not reopened:
            break
        for m, t in reopened:
            settle(m, t)
        if t := report(n, status(n)):
            settle(n, t)
    else:
        sys.exit(f"[chapter {n}] the earlier chapters keep being reopened. See the lines above.")
    subprocess.run([sys.executable, "-m", "firered_anki.build"], cwd=ROOT)
    print(f"[chapter {n}] settled, and the chapters before it")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
