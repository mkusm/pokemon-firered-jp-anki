"""Bring one chapter up to date: every stage that calls the model, in order.

After anything changes in a chapter (a prompt, a rule, the order of lines), a
fixed series of stages has to run before the chapter is consistent again:

  1. analyse rerun N       sentences the chapter shows that are not on the current analysis
  2. splits --chapter N    strings now cut two ways, or at a space inside a word
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
therefore checked again afterwards, and the ones it reopened are settled
together: one run of the series for all of them, so that a few sentences from
several chapters share a call instead of costing one call per chapter.

N counts from 0. 9 is the postgame. 10 is the Help deck and 11 the Link play
deck, which are not part of the story.

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

MAX_ROUNDS = 12
LIMITED = 75  # exit code when the plan's usage limit stopped the run: try again later


def name(ns: list[int]) -> str:
    """Chapters as the stages take them: 7, or 0,1,4."""
    return ",".join(map(str, ns))


def steps(ns: list[int]) -> list[list[str]]:
    n = name(ns)
    return [
        ["analyse", "rerun", n],
        ["splits", "--chapter", n],
        ["names", "--chapter", n],
        ["sense_pick"],
        ["cards"],
        ["particles", "--chapter", n],
        ["grammar", "run", "--chapter", n],
        ["notes", "--chapter", n],
        ["cards"],
    ]


def left(ns: list[int]) -> dict[int, dict[str, int]]:
    """What each chapter still needs from the model, counted the way each
    stage counts it. The deck is worked out once for all of them."""
    from .cards import known_words, prepare  # late: cards imports the stages

    deck, _, analyses, _ = prepare(offline_merge=True)
    return {n: _left(n, deck, analyses, known_words()) for n in ns}


def _left(n: int, deck, analyses: dict, known: set) -> dict[str, int]:
    shown = deck[deck["card_order"].notna() & (deck["chapter"] == n)].sort_values("card_order").drop_duplicates("text")
    shown = shown[shown["text"].map(lambda t: t in analyses)]
    current = [t for t in shown["text"] if analyse.ok(t)]
    sentences = shown[[not names.listed(g, lb) for g, lb in zip(shown["group"], shown["label"])]]  # not a bare name
    # The grammar and the notes are asked for a sentence in the chapter that
    # shows it first (grammar.first_sentences). A sentence an earlier chapter
    # also shows is that chapter's to explain: counting it here too left this
    # chapter waiting for an answer only the earlier one's run asks for.
    first_in = deck[deck["card_order"].notna()].sort_values("card_order").drop_duplicates("text")
    mine = set(first_in[first_in["chapter"] == n]["text"])
    explained = sentences[sentences["text"].map(lambda t: t in mine and len(analyses[t]["words"]) > 1)]["text"]
    gram = grammar.results(sentences["text"], analyses)
    bounded = splits.in_scope(deck, analyses, n)
    return {
        "sentences to re-analyse": len(shown) - len(current),
        "strings cut two ways or at a space inside, not decided": len(splits.undecided(bounded, analyses)),
        "sentences that cut a fixed string differently": len(splits.violations(bounded, analyses)),
        "translations that misname something": sum(bool(names.misses(t, analyses[t])) for t in sentences["text"]),
        "breakdowns that misname something": sum(bool(names.grammar_misses(t, analyses[t], g)) for t, g in gram.items()),
        "dictionary links to check": sum(
            w.get("_id_source") == "lookup" and not sense_pick.cache_file(t, w).exists()
            for t, a in analyses.items() for w in a["words"]),
        "particle uses to ask about": len(particles.unasked(shown["text"], analyses, known)),
        "sentences with no grammar": sum(not grammar.answered(t, analyses) for t in explained),
        "sentences not asked for a note": sum(not notes.asked(t) for t in explained),
        "notes not checked": sum(bool(r := notes.read(t)) and notes.needs_check(r) for t in explained),
        "_shown": len(shown),
    }


def report(n: int, todo: dict[str, int]) -> int:
    open_ = {k: v for k, v in todo.items() if v and not k.startswith("_")}
    print(f"[chapter {n}] {todo['_shown']} sentences shown; " + (
        "nothing left to ask" if not open_ else "left: " + "; ".join(f"{v} {k}" for k, v in open_.items())))
    return sum(open_.values())


def status(ns: list[int]) -> dict[int, dict[str, int]]:
    """left(ns), worked out by a fresh process."""
    done = subprocess.run([sys.executable, "-m", "firered_anki.chapter", name(ns), "--status"],
                          cwd=ROOT, capture_output=True, text=True)
    if done.returncode:
        sys.exit(f"[chapter {name(ns)}] could not read the state:\n{done.stderr[-600:]}")
    return {int(n): todo for n, todo in json.loads(done.stdout.strip().splitlines()[-1]).items()}


def report_all(ns: list[int]) -> tuple[int, list[int]]:
    """Report each chapter. → (what is left in all of them, the chapters with something left)."""
    if not ns:      # chapter 0 has no chapters before it
        return 0, []
    now = status(ns)
    counts = {n: report(n, now[n]) for n in ns}
    return sum(counts.values()), [n for n in ns if counts[n]]


def run(step: list[str]) -> tuple[int, bool]:
    """Run one stage, passing its output through. → (exit code, whether it
    said the usage limit stopped it)."""
    proc = subprocess.Popen([sys.executable, "-m", f"firered_anki.{step[0]}", *step[1:]], cwd=ROOT,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    limited = False
    for line in proc.stdout:
        print(line, end="", flush=True)
        # A stage that gives up on the limit says so. (The analysis waits it out and carries on.)
        limited |= any(x in line for x in ("usage limit reached", "usage limit;", "usage limit: stopping",
                                           "stopped by the usage limit"))
    return proc.wait(), limited


def settle(ns: list[int], total: int) -> None:
    """Run the series for these chapters, together, until nothing is left in any of them."""
    n = name(ns)
    stalled = 0
    for round_no in range(1, MAX_ROUNDS + 1):
        limited = False
        for step in steps(ns):
            print(f"\n== chapter {n}, round {round_no}: {' '.join(step)}", flush=True)
            code, hit = run(step)
            limited |= hit
            if code:
                sys.exit(f"[chapter {n}] stopped: {' '.join(step)} failed. Fix that and run this again; "
                         "it carries on from the caches.")
        print()
        before, (total, ns) = total, report_all(ns)  # the next round: only the chapters with something left
        if not total:
            return
        n = name(ns)
        if limited:
            print(f"[chapter {n}] stopped by the plan's limit. Run this again later; it carries on from the caches.")
            sys.exit(LIMITED)
        # A round can end with as much left as it began with and still have
        # helped: redoing a sentence changes which sentences the chapter
        # shows, and the next one comes up. Every sentence redone stays done,
        # so this runs out. Three such rounds in a row is something else.
        stalled = stalled + 1 if total >= before else 0
        if stalled == 3:
            sys.exit(f"[chapter {n}] not settled: three rounds in a row left as much to do as before. "
                     "An answer the model keeps giving, most likely. See the lines above.")
    sys.exit(f"[chapter {n}] not settled after {MAX_ROUNDS} rounds, though each one helped. Run this again.")


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if "--status" in sys.argv and len(args) == 1:  # several chapters at once: 0,1,4
        print(json.dumps(left([int(x) for x in args[0].split(",")]), ensure_ascii=False))
        return
    if len(args) != 1 or not args[0].isdigit():
        sys.exit(__doc__.strip().splitlines()[-2].strip())
    n = int(args[0])
    todo = left([n])[n]
    total = report(n, todo)
    if "--dry-run" in sys.argv:
        if todo["sentences to re-analyse"] or todo["sentences with no grammar"]:
            print(f"[chapter {n}] about {math.ceil(todo['sentences to re-analyse'] / analyse.RERUN.batch)} analysis calls "
                  f"and {math.ceil(todo['sentences with no grammar'] / grammar.BATCH)} grammar calls to start with")
        if todo["sentences to re-analyse"] and todo["particle uses to ask about"]:
            print(f"[chapter {n}] the particle uses are counted on the old analysis; the new one picks most of them itself")
        return
    if total:
        settle([n], total)
    # The chapters before it: settled already, unless this run reopened a
    # sentence of theirs. The reopened ones are settled together.
    for _ in range(3):
        t, reopened = report_all(list(range(n)))
        if not reopened:
            break
        settle(reopened, t)
        if t := report(n, status([n])[n]):
            settle([n], t)
    else:
        sys.exit(f"[chapter {n}] the earlier chapters keep being reopened. See the lines above.")
    subprocess.run([sys.executable, "-m", "firered_anki.build"], cwd=ROOT)
    print(f"[chapter {n}] settled, and the chapters before it")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    main()
