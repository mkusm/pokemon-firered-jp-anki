"""Checks on the built deck. There is no test suite; these are the things that
have gone wrong before, looked for in the data the stages wrote.

Run after `cards` (and `build`): uv run python -m firered_anki.check
It calls no model. Exit code 1 if anything fails.
"""

import fnmatch
import json
import sys

import pandas as pd
import yaml

from . import analyse, chapter, grammar, models, notes, speakers
from .cards import CARDS_OUT, STAND_ALONE, in_story, prepare
from .map_order import MapOrder
from .order import FIRST_SEEN
from .paths import EXTRACT_OUT
from .spread import LINK_PLAY


def main() -> None:
    failed = []

    def check(what: str, bad: list, show=str) -> None:
        print(("ok    " if not bad else "FAIL  ") + what + (f": {len(bad)}" if bad else ""))
        for b in bad[:5]:
            print("        " + show(b)[:150])
        if bad:
            failed.append(what)

    deck, _, analyses, occ = prepare(offline_merge=True)
    cards = pd.read_parquet(CARDS_OUT)
    labels = set(pd.read_parquet(EXTRACT_OUT, columns=["label"])["label"])
    mo = MapOrder()

    # --- the order
    check("every line listed in a walk exists", [lb for lb in mo.place if lb not in labels])
    hand = (yaml.safe_load(FIRST_SEEN.read_text(encoding="utf-8")).get("never_shown") or {})
    groups = pd.read_parquet(EXTRACT_OUT, columns=["group", "label"]).drop_duplicates()
    full = set(groups["group"] + "." + groups["label"])
    check("every never-shown entry matches a line", [
        k for k in hand if k not in labels and not any(fnmatch.fnmatchcase(x, k) for x in labels | full)])
    story = deck[deck["card_order"].notna() & (deck["deck"] == "story")].sort_values("card_order").reset_index(drop=True)
    where: dict[str, list[int]] = {}
    for i, u in enumerate(story["unit"]):
        where.setdefault(u, []).append(i)
    check("no message or walk has other text inside it",
          [u for u, ix in where.items() if story["dialogue"][ix[0]] and ix[-1] - ix[0] + 1 > len(ix)])
    shown = deck[deck["card_order"].notna()].sort_values("card_order")
    last = {d: shown[shown["deck"] == d]["card_order"].agg(["min", "max"]) for d in ("story", "help", "link")}
    check("the three decks follow one another: story, Help, Link play",
          [d for a, d in (("story", "help"), ("help", "link")) if not last[a]["max"] < last[d]["min"]])

    # --- the cards
    check("card keys are unique", cards["key"][cards["key"].duplicated()].tolist())
    check("every card is in one of the three decks", cards[~cards["deck"].isin(["story", "help", "link"])]["key"].tolist())
    check("Order runs 000000, 000001, …", [o for i, o in enumerate(sorted(cards["Order"])) if o != f"{i:06d}"][:5])
    check("a stand-alone deck's cards carry its name in their key, and no others do", [
        k for k, d in zip(cards["key"], cards["deck"])
        if (json.loads(k)[0] in STAND_ALONE) != (d in STAND_ALONE)])
    check("every card has its sentence, a meaning and a translation", cards[
        (cards["Sentence"] == "") | (cards["InContext"] == "") | (cards["SentenceEnglish"] == "")]["key"].tolist())

    # A player who drops the other gender's tag must not lose a word they meet.
    met = occ[in_story(occ)].join(deck[["gender", "deck", "card_order"]], on="row")
    met = met[met["card_order"].notna() & ~met["deck"].isin(STAND_ALONE)]
    sides = met.groupby(met["key"].map(lambda k: json.dumps(list(k), ensure_ascii=False)))["gender"].agg(set)
    check("a card tagged player-boy or player-girl is on a word only that side meets", [
        f"{k} {tags}" for k, tags in zip(cards["key"], cards["tags"])
        for side in ("boy", "girl") if f"player-{side}" in tags and sides.get(k) != {side}])

    # --- what the cards say about a line
    marked = deck[deck["text"].str.contains("＊")]
    check("every ＊ of a sentence has the code it stands for", [
        t for t, v in zip(marked["text"], marked["vars"]) if t.count("＊") != len(json.loads(v))])
    # The lines on which the game itself names a main character are the test
    # of the speakers read from the maps: where both know, they must agree.
    main_cast = set(speakers.NAMED.values())
    check("a speaker read from the map is the one the game names on the line", [
        f"{sp} / {who}: {t}" for sp, lb, t in zip(deck["speaker"], deck["label"], deck["text"])
        if sp in main_cast and (who := speakers.who(lb, t)) and who != sp])

    # --- the models
    wrong = []
    for t in shown[shown["chapter"] < LINK_PLAY]["text"].unique():
        made = [json.loads(analyse.cache_path(analyse.MAIN.name, t).read_text())["_run"]["model"]] if t in analyses else []
        if t in analyses and (f := grammar.cache_file(t, analyses[t]["words"])).exists():
            made.append(json.loads(f.read_text())["_run"]["model"])
        if rec := notes.read(t):
            made.append(rec["_run"]["model"])
        if any(not models.enough(m, t) for m in made) or (models.of(t) != "opus"):
            wrong.append(t)
    check("no Sonnet answer under a sentence the story or the Help deck shows", wrong)

    # --- nothing left to ask
    for n, todo in chapter.left(sorted(deck["chapter"].unique())).items():
        open_ = {k: v for k, v in todo.items() if v and not k.startswith("_")}
        check(f"chapter {n} has nothing left to ask", [f"{v} {k}" for k, v in open_.items()])

    print()
    print("all checks passed" if not failed else f"{len(failed)} checks failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
