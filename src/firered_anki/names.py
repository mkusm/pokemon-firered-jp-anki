"""Names: Pokémon, items, moves, abilities, places, people, badges, game terms.

The game's lists give every species, item, move, ability and place a line of
its own (フシギダネ, げんきのかけら). The analysis reads a name as its parts (げんき +
の + かけら), which is right for the words and leaves the thing itself without a
card. This module finds the names: the list line itself, and in any other
sentence a name-word or a run of words that spells a name. Each use is one
occurrence of the name's card, which goes in the story deck where the name is
first seen. People, badges and a few game terms have no list in the game and
come from names_by_hand.yaml.

The card calls the thing what the English game calls it. That is the one use
of the official English text: フシギダネ is Bulbasaur, whatever its parts mean.
The parts go on the card as the literal meaning.

The same names are given to the model. The analysis and the grammar pass are
shown the English names of the Pokémon, items, moves, abilities, places, people and badges
their sentences mention, and a check finds the sentences whose translation
still calls one something else and has them redone.

Run: uv run python -m firered_anki.names [--chapter N] [--dry-run]
"""

import re
import sys
from functools import cache

import pandas as pd
import yaml

from .paths import CORPUS, EXTRACT_OUT, ROOT
from .romaji import romaji
from .spans import word_span
from .tokenize import hira

MAX_RUN = 5
KIND = {"pokemon": "Pokémon", "item": "item", "move": "move", "ability": "ability", "place": "place",
        "person": "person", "team": "team", "badge": "badge", "term": "game term",
        "type": "Pokémon type", "world": "real-world name"}
SPELLINGS = ROOT / "name_spellings.yaml"
BY_HAND = ROOT / "names_by_hand.yaml"
# マサラタウン is Pallet Town, so マサラ alone (マサラの　オーキド) is Pallet.
TOWN = (("シティ", " City"), ("タウン", " Town"), ("じま", " Island"), ("こうげん", " Plateau"))
# Which set of names an answer was given with, recorded with the answer. An
# answer from before a kind was added has not seen those names and is checked again.
SHOWN = 4  # 2: people, badges, towns by their short names; 3: gyms, regions, game terms; 4: types, real-world names
KEEP = {"HP", "PP", "TM", "HM", "TV", "VS", "S.S."}  # not title-cased
NUMBER = re.compile(r"[０-９]+$")
# A word the analysis glossed as the species: "Pidgey", "Pokémon name". Not
# クラブ "club" (Krabby), ゴースト "Ghost type" (Haunter), ラッキー "lucky" (Chansey).
SPECIES_GLOSS = re.compile(r"pok[ée]mon name|\(pok[ée]mon\)|^pok[ée]mon$", re.I)


def kind_of(group: str, label: str) -> str | None:
    """The kind of name a line of one of the game's name lists holds."""
    if group == "species_names":
        return "pokemon"
    if group == "items" and label.startswith("gItems."):
        return "item"
    if group == "move_names":
        return "move"
    if group == "abilities" and label.startswith("gAbilityNames."):
        return "ability"
    if group == "battle_main" and label.startswith("gTypeNames."):
        return "type"
    return "place" if group == "region_map_entry_strings" else None


listed = kind_of  # a line of a name list: the line a name's card can sit on


def bare(text: str) -> str:
    """わざマシン３９ → わざマシン: the fifty machines are one name."""
    return NUMBER.sub("", text) if "マシン" in text else text


def english(raw: str) -> str:
    """BULBASAUR → Bulbasaur, POKé BALL → Poké Ball, TM39 → TM."""
    raw = re.sub(r"^([TH]M)\d+$", r"\1", raw.strip())
    return " ".join(t if t in KEEP else re.sub(r"[A-Zé']+", lambda m: m.group().capitalize(), t)
                    for t in raw.split())


@cache
def _tables() -> tuple[dict, dict, dict]:
    """(name → (kind, English name), name → its other readings, name → note).
    A string can be two things: ゴースト is Haunter and the Ghost type."""
    rows = pd.read_parquet(EXTRACT_OUT, columns=["group", "label", "text", "line_no"])
    en = (CORPUS / "en_msg.txt").read_text(encoding="utf-8").split("\n")
    today = yaml.safe_load(SPELLINGS.read_text(encoding="utf-8")) if SPELLINGS.exists() else {}
    out, also, notes = {}, {}, {}

    def add(jp: str, kind: str, name: str) -> None:
        if jp not in out:
            out[jp] = (kind, name)
        elif (kind, name) != out[jp] and (kind, name) not in also.get(jp, []):
            also.setdefault(jp, []).append((kind, name))

    for r in rows.itertuples():
        kind = kind_of(r.group, r.label)
        name = english(en[r.line_no])
        # A list's empty slots are ？？？ or a row of dashes.
        if kind and re.search(r"[ぁ-ヿ]", r.text.replace("ー", "")) and re.search("[A-Za-z]", name):
            add(bare(r.text.replace("　", "")), kind, today.get(name, name))
    for jp, (kind, name) in list(out.items()):
        for ja, eng in TOWN:
            if kind == "place" and jp.endswith(ja) and name.endswith(eng):
                add(jp[:-len(ja)], "place", name[:-len(eng)])
                # ニビジム: the English game says PEWTER GYM.
                add(jp[:-len(ja)] + "ジム", "place", name[:-len(eng)] + " Gym")
    by_hand = yaml.safe_load(BY_HAND.read_text(encoding="utf-8")) if BY_HAND.exists() else {}
    for kind, listing in by_hand.items():
        for jp, entry in listing.items():
            # "English name", or {en: English name, note: what a learner may not know}
            name, note = (entry["en"], entry.get("note")) if isinstance(entry, dict) else (entry, None)
            add(jp, kind, name)
            if note:
                notes[jp] = " ".join(note.split())
    return out, also, notes


def official() -> dict[str, tuple[str, str]]:
    """Name → (kind, English name): every line of the game's name lists, and
    names_by_hand.yaml. The name is written without the game's spaces
    (１ばん　どうろ → １ばんどうろ)."""
    return _tables()[0]


def readings(name: str) -> list[tuple[str, str]]:
    """Everything the string names, a species first: its gloss test is the strictest."""
    out, also, _ = _tables()
    return sorted([out[name], *also.get(name, [])], key=lambda v: v[0] != "pokemon")


load = official  # every name has a card in the story deck


def _is_name(w: dict, kind: str, eng: str, after: dict | None = None) -> bool:
    """Whether one word that spells a name is that name here. `after`: the next word."""
    gloss = f"{w.get('in_context') or ''} | {w.get('literal') or ''}"
    if kind == "type" and (re.search(r"\btype\b", gloss, re.I) or (after or {}).get("surface", "").startswith("タイプ")):
        return True  # ほのお in ほのおタイプ, whether or not the analysis marked it as a name
    if not w.get("proper"):
        return False  # じてんしゃ "bicycle", おちゃ "tea": the word's own card covers it
    if kind != "pokemon" or eng.lower() in gloss.lower() or any(SPECIES_GLOSS.search(g.strip()) for g in gloss.split("|")):
        return True
    # Glossed as another species (ゴローン as "Geodude"): a Pokémon, misnamed.
    return bool(set(re.findall(r"[a-zé'.♀♂-]+", gloss.lower())) & _species())


@cache
def _species() -> frozenset:
    return frozenset(eng.lower() for kind, eng in official().values() if kind == "pokemon" and " " not in eng)


def find(text: str, words: list[dict], kind: str | None = None, names: dict | None = None,
         as_names: frozenset = frozenset()) -> list[dict]:
    """The names in a sentence: {name, kind, english, first, last, span}, where
    first and last are word positions. `kind` is set for a list line, which is
    one name whatever the analysis made of it. `as_names`: the names some other
    sentence uses as a name."""
    names = load() if names is None else names
    notes = _tables()[2]
    if kind:
        name = bare(text.replace("　", ""))
        eng = next((e for k, e in readings(name) if k == kind), None) if name in names else None
        if not eng or not words:
            return []
        # Called by an ordinary word (じしゃく "magnet", たいあたり "tackle") and
        # never used as a name elsewhere: that word's own card is the card.
        if kind not in ("pokemon", "type") and len(words) == 1 and not words[0].get("proper") and name not in as_names:
            return []
        return [{"name": name, "kind": kind, "english": eng, "note": notes.get(name),
                 "first": 0, "last": len(words) - 1, "span": (0, len(text))}]
    out, i = [], 0
    while i < len(words):
        for j in range(min(len(words), i + MAX_RUN) - 1, i - 1, -1):
            spelled = "".join(w["surface"].replace("　", "") for w in words[i:j + 1])
            name = spelled if spelled in names else bare(spelled)
            if name not in names:
                continue
            is_a = names[name]
            if j == i:  # one word: which of the things it can name, if any
                nxt = words[i + 1] if i + 1 < len(words) else None
                is_a = next((v for v in readings(name) if _is_name(words[i], *v, nxt)), None)
                if not is_a:
                    continue
            a, b = word_span(text, words, i), word_span(text, words, j)
            # The words must stand together with no space: いい　キズぐすり is
            # "a good Potion", いいキズぐすり is the Super Potion. Routes, badges
            # and game terms are written with one (１ばん　どうろ, グレー　バッジ,
            # フレンドリィ　ショップ), and so is a name the analysis took as one word.
            if not a or not b or text[a[0]:b[1]].replace("　", "") != spelled:
                continue
            if "　" in text[a[0]:b[1]] and j > i and is_a[0] not in ("place", "badge", "term", "world"):
                continue
            # A move or an ability is often spelled by ordinary words (かたく + なる
            # "become hard" is not Harden): one of them must be marked as a name.
            if is_a[0] in ("move", "ability") and not any(w.get("proper") for w in words[i:j + 1]):
                continue
            out.append({"name": name, "kind": is_a[0], "english": is_a[1], "note": notes.get(name),
                        "first": i, "last": j, "span": (a[0], b[1])})
            i = j
            break
        i += 1
    return out


def word(run: dict, words: list[dict]) -> dict:
    """The name as a word for its card, built from the words that spell it."""
    parts = [w for w in words[run["first"]:run["last"] + 1] if not NUMBER.fullmatch(w["surface"])]
    if len(parts) == 1 and parts[0]["surface"] != run["name"]:
        parts = [{"surface": run["name"]}]  # わざマシン３９ as one word: its gloss is about the number too
    if len(parts) == 1:
        literal = parts[0].get("literal") or ""
        # Not a meaning: the English name again, or the name in Latin letters.
        if literal.lower() in (run["english"].lower(), "pokémon name", "item name", romaji(run["name"]).lower()):
            literal = ""
    else:
        literal = " + ".join(
            f"{w['surface']} ({w['literal']})" if w.get("literal") and len(w["surface"]) > 1 else w["surface"]
            for w in parts)
    # The parts' kanji are those of their dictionary forms: only good when no
    # part is inflected (なおし in まひなおし would come out as 治す).
    has_kanji = any(w.get("kanji") and w["kanji"] != w["surface"] for w in parts) and all(
        not w.get("kanji") or hira(w["surface"]) == hira(w.get("base") or w["surface"]) for w in parts)
    return {
        "surface": run["name"], "base": run["name"],
        "kanji": "".join(w.get("kanji") or w["surface"] for w in parts) if has_kanji else "",
        "usually_kana": has_kanji,
        "char_readings": " · ".join(w["char_readings"] for w in parts if w.get("char_readings")),
        "literal": literal, "in_context": f"{run['english']} ({KIND[run['kind']]})",
        "modifiers": "", "jmdict_id": None, "sense": None, "proper": True, "name": run["kind"],
        "note": run.get("note") or "",
    }


# --- the same names for the model --------------------------------------------------

@cache
def _any_name() -> re.Pattern:
    # あわ (Bubble) is in half the deck's sentences; no other name is that short.
    names = [n for n in official() if len(n) > 2 or not re.fullmatch("[ぁ-ゖ]+", n)]
    return re.compile("|".join(map(re.escape, sorted(names, key=len, reverse=True))))


def mentioned(texts) -> dict[str, tuple[str, str]]:
    """The names whose letters appear in these sentences, longest first where
    two overlap (コラッタ, not ラッタ). Some will be ordinary words."""
    names = official()
    out = {}
    for t in texts:
        t = t.replace("　", "")
        for m in _any_name().finditer(t):
            name = m.group() if m.group() in names else bare(m.group())
            out[name] = names[name]
    return out


def for_prompt(texts, where: str) -> str:
    """The English names that apply to these sentences, as lines for the model."""
    found = mentioned(texts)
    if not found:
        return ""
    lines = "\n".join(f"- {name}: " + " or ".join(f"{eng} ({KIND[kind]})" for kind, eng in readings(name))
                      for name in found)
    return ("\n\nEnglish names. Where a word in these sentences is this Pokémon, item, move, ability, place, "
            f"person or badge, call it exactly this in {where}. The same letters can also be an ordinary word "
            "(じしん \"confidence\", クラブ \"club\"): then this does not apply.\n" + lines)


def line_english(group: str, label: str, text: str) -> str | None:
    """The English name of a line of one of the name lists: the translation of
    such a line is the name itself, and needs no model."""
    name, kind = bare(text.replace("　", "")), kind_of(group, label)
    if not kind or name not in official():
        return None
    eng = next((e for k, e in readings(name) if k == kind), None)
    # A bare ほのお is "Fire", which says too little.
    return f"{eng} ({KIND[kind]})" if eng and kind == "type" else eng


def _plain(s: str) -> str:
    return re.sub(r"[^a-z0-9♀♂]", "", (s or "").lower().replace("é", "e"))


def misses(text: str, analysis: dict, seen: bool = False) -> list[dict]:
    """The names in a sentence that its translation calls something else.
    An answer given with the English names in the prompt is the model's own
    decision (だいもんじ, the Kyoto festival, is not Fire Blast) and is left
    alone; `seen` asks for exactly those."""
    if (analysis.get("_run", {}).get("names") == SHOWN) != seen:
        return []
    said = _plain(analysis["english"])
    return [run for run in find(text, analysis["words"], names=official()) if _plain(run["english"]) not in said]


def grammar_misses(text: str, analysis: dict, answer: dict, seen: bool = False) -> list[dict]:
    """The names a line of the sentence's breakdown calls something else."""
    if (answer.get("_run", {}).get("names") == SHOWN) != seen:
        return []
    out = []
    for run in find(text, analysis["words"], names=official()):
        spelled = text[run["span"][0]:run["span"][1]].replace("　", "")
        for line in answer.get("structure", []):
            if spelled in line["jp"].replace("　", "") and _plain(run["english"]) not in _plain(line["en"]):
                out.append(run)
    return out


def scope(chapter: int | None):
    """The sentences the deck shows, with their analyses."""
    from .cards import prepare  # late: cards imports this module

    deck, entries, analyses, _ = prepare(offline_merge=True)
    shown = deck[deck["card_order"].notna()].sort_values("card_order").drop_duplicates("text")
    if chapter is not None:
        shown = shown[shown["chapter"] == chapter]
    shown = shown[[not kind_of(g, lb) and t in analyses for g, lb, t in zip(shown["group"], shown["label"], shown["text"])]]
    return shown, analyses, entries


def main() -> None:
    from . import analyse, grammar  # late: both import this module

    args = sys.argv[1:]
    chapter = int(args[args.index("--chapter") + 1]) if "--chapter" in args else None
    shown, analyses, entries = scope(chapter)

    def report(label: str, bad: dict) -> None:
        print(f"[names] {label}: {len(bad)} of {len(shown)} sentences")
        for t, runs in list(bad.items())[:8]:
            print(f"    {runs[0]['name']} = {runs[0]['english']} | {analyses[t]['english'][:90]}")

    def with_grammar() -> dict:
        return grammar.results(shown["text"], analyses)

    bad = {t: m for t in shown["text"] if (m := misses(t, analyses[t]))}
    report("translation calls a name something else", bad)
    had = with_grammar()
    bad_grammar = {t: m for t, g in had.items() if (m := grammar_misses(t, analyses[t], g))}
    report("breakdown calls a name something else", bad_grammar)
    if "--dry-run" in args or not (bad or bad_grammar):
        return

    if bad:
        analyse.redo_texts(set(bad))
        analyses.update(analyse.results(analyse.MAIN.name, list(bad), entries))
        report("not redone (run again)", {t: m for t in bad if (m := misses(t, analyses[t]))})
        report("kept by the model with the names in front of it",
               {t: m for t in bad if (m := misses(t, analyses[t], seen=True))})
    # Grammar again where its breakdown had the other name, and where the redo
    # changed the translation under a breakdown already written.
    again = set(bad_grammar) | (set(bad) & set(had))
    if again:
        grammar.redo(shown[shown["text"].isin(again)], analyses)
        now = with_grammar()
        report("breakdown kept by the model with the names in front of it",
               {t: m for t in again if t in now and (m := grammar_misses(t, analyses[t], now[t], seen=True))})


if __name__ == "__main__":
    main()
