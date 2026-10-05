"""Stage 5: one card per word form and sense, in story order.

  1. Every analysed word becomes an occurrence keyed by (jmdict_id, sense, form),
     or (base, kanji, form) when it has no JMdict entry. A name names.py knows
     (a Pokémon, item, move, ability, type, place, main character, badge, game
     term, real-world name) has one card, where it is first seen. Any other
     name makes no card: a one-off character, a preset from the naming screen,
     a piece of a longer name. Words in known.txt are never carded.
  2. Sense merge: an entry used in several senses goes to Claude once, with
     the sentences, to group senses that are one meaning for a learner
     (人 "person" / "human being") while keeping real ones apart (見る "see" /
     〜てみる "try"). Cached per entry.
  3. Spread: non-dialogue lines are interleaved with the story by chapter (see
     spread.py), now on real card keys instead of tokenizer lemmas. The line
     that names a Pokémon or an item is not spread: it stays where the thing
     is first met.
  4. Cards follow their sentences, and within a sentence the words left to
     right. Each card takes its first sentence in that order, except that a dialogue
     sentence in the same chapter beats a non-dialogue line (not for a Pokémon
     or item name, which stays at the first place it is seen). Up to two later
     sentences become extra examples.
  5. Where the grammar pass has covered a sentence (see grammar.py), its word
     cards get the patterns the word takes part in and a breakdown of the
     sentence. There are no cards for the patterns themselves.

Run: uv run python -m firered_anki.cards [run-name]   (default: main)
"""

import hashlib
import json
import random
import re
import sys
from collections import Counter, defaultdict

import pandas as pd

from . import claude_cli, corrections, grammar, kanji_line, models, names, notes, placeholders, speakers
from .analyse import MAIN, load, results
from .grounding import sense_glosses
from .map_order import MapOrder
from .paths import DATA, ROOT
from .romaji import romaji, sentence_romaji
from .spans import word_span
from .spread import spread
from .tokenize import hira

CARDS_OUT = DATA / "05_cards" / "cards.parquet"
SPOTCHECK_OUT = DATA / "05_cards" / "spotcheck.txt"
KNOWN = ROOT / "known.txt"
MERGE_CACHE = DATA / "cache" / "sense_merge"
MERGE_MODEL, MERGE_EFFORT, MERGE_BATCH = "sonnet", "low", 50
MAX_EXTRA = 2


def known_words() -> set[str]:
    lines = KNOWN.read_text(encoding="utf-8").splitlines() if KNOWN.exists() else []
    return {ln.strip() for ln in lines if ln.strip() and not ln.startswith("#")}


PARTICLES = {"が", "を", "は", "に", "で", "と", "も", "の", "へ", "から", "まで", "より", "や"}


def form_of(w: dict) -> str:
    """The word's form without a particle the LLM glued on (ことが → こと)."""
    surface, base = w["surface"], w["base"]
    if hira(surface).startswith(hira(base)) and hira(surface)[len(base):] in PARTICLES:
        return surface[:len(base)]
    return surface


def is_particle(w: dict) -> bool:
    """A kana-only grammar word in its own form: は, を, へ, には…"""
    return not w.get("kanji") and w["surface"] == w["base"] and (
        w["surface"] in {"は", "へ", "を"} or w["surface"].endswith("は"))


def base_kana(w: dict, entry: dict) -> str:
    """The base form in kana. A few come back in kanji (言う); take the reading
    from the JMdict entry then."""
    base = w["base"]
    if not re.search(r"[\u4e00-\u9fff]", base) or not entry.get("r"):
        return base
    suru = "する" if base.endswith("する") and not entry["r"][0].endswith("する") else ""
    return entry["r"][0] + suru


KANJI = re.compile(r"[\u4e00-\u9fff々]")
# Readings as cited in a dictionary can differ from the reading inside a word
# by gemination (食 しょく in 食器 しょっき) or voicing (紙 かみ in 手紙 てがみ).
_PLAIN = str.maketrans("がぎぐげござじずぜぞだぢづでどばびぶべぼぱぴぷぺぽっ",
                       "かきくけこさしすせそたちつてとはひふへほはひふへほつ")


def _same_sounds(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    for x, y in zip(a.translate(_PLAIN), b.translate(_PLAIN)):
        if x != y and not ({x, y} <= set("つくちき")):
            return False
    return True


def clean_char_readings(kanji: str, base: str, raw: str) -> str:
    """Per-kanji readings in one format, or "" if they can't be trusted.

    The model sometimes lists the kana endings as extra items (決 き · める),
    includes them in a reading (売 うり in 安売り), or names a character that
    is not in the word. Kana items are dropped, overlapping endings trimmed,
    and the result is kept only if the readings add up to the word's reading.
    A reading for a group of characters (上手 うま) is kept as a group.
    """
    if not raw or not KANJI.search(kanji):
        return ""
    pairs = []
    for item in re.split(r"[·・,、]", raw):
        m = re.match(r"^\s*(\S+)\s+(\S+)\s*$", item)
        if m and all(KANJI.match(ch) for ch in m.group(1)):
            pairs.append([m.group(1), hira(m.group(2))])
    word, reading = kanji, hira(base)
    if word.endswith("する") and reading.endswith("する"):
        word, reading = word[:-2], reading[:-2]
    if "".join(h for h, _ in pairs) != "".join(ch for ch in word if KANJI.match(ch)):
        return ""  # a missing, extra or invented character

    def rebuild(trim: bool) -> tuple[str, list[tuple[str, str]]]:
        out, kept, i = "", [], 0
        for head, r in pairs:
            j = word.index(head, i)
            out += hira(word[i:j])
            i = j + len(head)
            if trim:
                # An ending the model counted twice: 売 うり before り.
                tail = hira(re.match(r"[^\u4e00-\u9fff々]*", word[i:]).group())
                for n in range(min(len(tail), len(r) - 1), 0, -1):
                    if r.endswith(tail[:n]):
                        r = r[:-n]
                        break
            kept.append((head, r))
            out += r
        return out + hira(word[i:]), kept

    # As given first: 幼 おさな in 幼なじみ ends in な legitimately.
    for trim in (False, True):
        rebuilt, kept = rebuild(trim)
        if _same_sounds(rebuilt, reading):
            return " · ".join(f"{h} {r}" for h, r in kept)
    return ""


# Pieces of words, not words: stammers (こっ　こんなに), speech broken up by
# sleep or possession (たべら……　れな……　い), the toothless Warden's garble.
# The analysis says so in its gloss. These cards stay: they explain text a
# learner would otherwise puzzle over. They get a tag so they can be found.
# A half of a compound the game splits with a space ("first half of 忍び込む")
# is ordinary vocabulary and is not matched; nor is げんきのかけら (Revive),
# literally a "fragment of vitality".
FRAGMENT = re.compile(
    r"stammer|stutter|garbled|broken (?:speech|up)|fragment '|\(cut off\)"
    r"|fragment of [\u3040-\u30ff\u4e00-\u9fff]"
    r"|placeholder text|end of '|\((?:first|middle|last|second) (?:part|half)[,)]",
    re.I,
)
# A whole line of short katakana scraps and ellipses: オ…　マ…　エモ…　…　ナカ…マニッ！
# (…　ココカラ　タチサレ… is a real sentence written in katakana: its runs are long.)
BROKEN_LINE = re.compile(r"[…　ァ-ヶーぞッっ！？]+")
SCRAP = re.compile(r"[ァ-ヶーぞッっ]+")
SOUND = re.compile(r"onomatop|mimetic|sound effect|sound word|\bsfx\b|\(sound|sound of|laugh|screech|noise|babbl", re.I)


# Not words: a number, a circled step number, a button's letter. They make no
# card anywhere. Abbreviations the game writes in Latin letters do (ＨＰ, Ｌｖ,
# ＳＴＡＲＴ), and so does a number with its counter (３０ぴき).
NOT_A_WORD = re.compile(r"[0-9０-９①-⑳]+|[A-ZＡ-Ｚ]")

# A name that is not one names.py knows still gets a card when the card is
# what explains the text: a Pokémon's cry (ぴかちゅ), a name drawn out in a
# shout (やどらーん).
ODD_NAME = re.compile(r"\bcr(?:y|ies)\b|drawn out", re.I)


def _gloss(w: dict) -> str:
    return " | ".join(w.get(k) or "" for k in ("in_context", "literal", "modifiers"))


def is_broken_line(text: str) -> bool:
    if not BROKEN_LINE.fullmatch(text) or text.count("…") < 2:
        return False
    runs = SCRAP.findall(text)
    return len(runs) >= 2 and sum(map(len, runs)) / len(runs) <= 3


def is_fragment(text: str, w: dict) -> bool:
    """A piece of a word: glossed as one, or any non-sound word in a line of scraps."""
    gloss = _gloss(w)
    return bool(FRAGMENT.search(gloss)) or (is_broken_line(text) and not SOUND.search(gloss))


def is_onomatopoeia(w: dict, entry: dict) -> bool:
    """JMdict marks the chosen sense as onomatopoeic or mimetic, or the gloss
    says it is a sound (for sound words with no entry, like ピカ)."""
    senses = entry.get("s", [])
    n = w.get("sense") or 0
    marked = senses[n - 1].get("on") if 1 <= n <= len(senses) else any(s.get("on") for s in senses)
    return bool(marked) or bool(re.search(r"onomatop|mimetic|sound effect|sound word|\bsfx\b|\(sound", _gloss(w), re.I))


_SMALL = {"to", "a", "an", "the", "of", "be", "one", "s", "is", "it", "in", "on", "for", "and", "or", "that", "as",
          "at", "by", "with", "etc", "esp", "e", "g"}


def _said(s: str) -> set:
    """The words of a gloss, endings off, small words out: "to stab" and "stabbing" are one."""
    return {re.sub(r"(ing|ed|es|s)$", "", x) for x in re.findall(r"[a-z]+", s.lower())} - _SMALL


def literal(w: dict, sense: str) -> str:
    """The literal meaning, where it says something. A name's is its parts
    ("vigor + shard"). A word's is shown only if it has a word that neither
    the meaning nor the dictionary sense has: ふとっぱら "big belly" beside
    "generous", not ずつう "headache" beside "headache"."""
    lit = (w.get("literal") or "").strip()
    if w.get("name") or not lit:
        return lit
    return lit if _said(lit) - _said(w.get("in_context") or "") - _said(sense) else ""


def dict_sense(w: dict, entry: dict) -> str:
    """The JMdict definition of the sense chosen for this sentence. It tells
    apart cards whose short gloss is the same: 危ない "dangerous" (sense 1) and
    "close (call); narrow (escape)" (sense 4). Nothing for a guessed sense:
    the sense check found none of the senses it was shown to fit (〜てあげる is
    あげる 24, and it saw five), so the one guessed from them is a wrong line
    ("to do up (one's hair)")."""
    if w.get("_sense_guessed"):
        return ""
    return "; ".join(sense_glosses(w.get("jmdict_id"), w.get("sense"), {str(w.get("jmdict_id")): entry}))


def dictionary(w: dict, entry: dict) -> str:
    """The entry's first senses, and the chosen one if it comes after them
    (する 6, "to decide on")."""
    senses = [(i, s["g"]) for i, s in enumerate(entry.get("s", []), 1)]
    n = w.get("sense") or 0
    if n > len(senses) and (g := sense_glosses(w.get("jmdict_id"), n, {})):
        senses.append((n, g))
    return "; ".join(f"{i}) {'; '.join(g)}" for i, g in senses)


def unsure(w: dict, entry: dict) -> bool:
    """Whether the card gets the low-confidence tag. Always when the entry is
    not this word. The model also flags words whose use no listed sense fits,
    a helper verb or an idiom (きたえて　いく, においが　する): those glosses
    checked out by hand, so the flag is dropped when the entry is verified
    and the word is a plain form of its base. It stays for a guess at what a
    scrap of text is (リッ for かなしばり) and for words with no entry."""
    if w.get("_form_mismatch") or not w.get("low_confidence"):
        return bool(w.get("_form_mismatch"))
    a, b = hira(form_of(w)).translate(_PLAIN), hira(base_kana(w, entry)).translate(_PLAIN)
    plain_form = a[:1] == b[:1] or b.endswith(("する", "くる"))  # します, きた: the stem changes
    return not (entry and plain_form)


def sentence_in_romaji(text: str, analysis: dict) -> str:
    words = analysis["words"]
    return sentence_romaji(text, words, {i for i, w in enumerate(words) if is_particle(w)})


def word_key(w: dict) -> tuple:
    form = form_of(w)
    if w.get("jmdict_id"):
        return ("jm", w["jmdict_id"], w.get("sense") or 1, form)
    return ("base", hira(w["base"]), w.get("kanji") or "", form)


def name_key(run: dict) -> tuple:
    """A name's card key. A string that names two things (ゴースト: Haunter and
    the Ghost type) has a card for each; the second carries its kind."""
    first = names.official()[run["name"]][0]
    return ("name", run["name"]) if run["kind"] == first else ("name", run["name"], run["kind"])


# --- 1. occurrences ------------------------------------------------------------

def occurrences(deck: pd.DataFrame, analyses: dict, known: set) -> pd.DataFrame:
    """One row per word use. `span` is set for a Pokémon or item name, which
    can be several of the analysis's words; `listed` marks the word a name-list
    line is there to teach."""
    rows = []
    # A list line that is one ordinary word is a name only if some sentence uses it as one.
    as_names = frozenset(
        run["name"] for r in deck.itertuples()
        if r.text in analyses and not names.listed(r.group, r.label)
        for run in names.find(r.text, analyses[r.text]["words"]))
    for r in deck.itertuples():
        a = analyses.get(r.text)
        if not a:
            continue
        kind = names.listed(r.group, r.label)
        runs = names.find(r.text, a["words"], kind, as_names=as_names)
        inside = {pos: run for run in runs for pos in range(run["first"], run["last"] + 1)}
        for run in runs:
            rows.append({
                "row": r.Index, "text": r.text, "pos": run["first"], "key": name_key(run),
                "proper": True, "known": run["name"] in known, "word": names.word(run, a["words"]),
                "span": run["span"], "listed": bool(kind), "name_line": bool(kind),
            })
        for pos, w in enumerate(a["words"]):
            if "＊" in w["surface"] or not w["surface"]:
                continue
            if NOT_A_WORD.fullmatch(w["surface"].replace("　", "")):
                continue
            if kind == "pokemon" and runs:
                continue  # マダ and ツボミ in マダツボミ are wordplay, on the name's card as its literal meaning
            # The name's card covers the name-word, and a part that is only a
            # name (ズリ in ズリのみ). Its ordinary words keep their cards (み "berry").
            if pos in inside and (w.get("proper") or inside[pos]["first"] == inside[pos]["last"]):
                continue
            skip = w["base"] in known or w["surface"] in known or (w.get("kanji") or "") in known
            rows.append({
                "row": r.Index, "text": r.text, "pos": pos, "key": word_key(w),
                "proper": bool(w.get("proper")) and not (is_fragment(r.text, w) or ODD_NAME.search(_gloss(w))),
                "known": skip, "word": w,
                "span": None, "listed": bool(kind) and not runs and len(a["words"]) == 1,
                "name_line": bool(kind),
            })
    return pd.DataFrame(rows)


def in_story(occ: pd.DataFrame) -> pd.Series:
    """The occurrences that make cards: ordinary words, and the names
    names.py knows. `proper` is left for names it does not know."""
    return (~occ["proper"] | occ["key"].map(lambda k: k[0] == "name")) & ~occ["known"]


def unify_unlinked(occ: pd.DataFrame) -> pd.Series:
    """Give a word with no JMdict entry the key of the same word where it has
    one. The analysis links を to its entry in some sentences and not in
    others; without this the same word makes two cards.

    A grammar word the particle pass looked at and found no listed sense for
    keeps its own key: と naming what something becomes is not と "if", and
    must not be the face of that card."""
    # Names and ordinary words are kept apart: そう in an item's name is not
    # the adverb そう.
    linked: dict[tuple, Counter] = defaultdict(Counter)
    for k, w in zip(occ["key"], occ["word"]):
        if k[0] == "jm":
            linked[(bool(w.get("proper")), hira(w["base"]), k[3])][k] += 1

    def fix(k: tuple, w: dict) -> tuple:
        if k[0] != "base" or w.get("_no_sense"):
            return k
        group = (bool(w.get("proper")), k[1], k[3])
        return linked[group].most_common(1)[0][0] if group in linked else k

    return pd.Series([fix(k, w) for k, w in zip(occ["key"], occ["word"])], index=occ.index)


# --- 2. sense merge ------------------------------------------------------------

MERGE_SYSTEM = """You help build a Japanese vocabulary deck from Pokémon FireRed. Each word below was tagged with different JMdict senses in different sentences. For each word, group the senses a learner would treat as ONE meaning (same idea, neighbouring dictionary senses) and keep apart senses that are really different to learn (見る "to see" vs 〜てみる "to try doing"; 居る "to exist" vs 〜ている "be doing"). Reply with JSON only."""

MERGE_SCHEMA = {
    "type": "object",
    "properties": {"words": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "integer"},
            "groups": {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}},
        },
        "required": ["id", "groups"],
    }}},
    "required": ["words"],
}


def merge_senses(occ: pd.DataFrame, entries: dict, offline: bool = False) -> dict[tuple, int]:
    """(jmdict_id, sense) → canonical sense (the group's first-used sense)."""
    jm = occ[occ["key"].map(lambda k: k[0] == "jm") & ~occ["proper"]]
    used: dict[int, dict[int, list[str]]] = defaultdict(lambda: defaultdict(list))
    for k, w, text in zip(jm["key"], jm["word"], jm["text"]):
        ex = used[k[1]][k[2]]
        if len(ex) < 2 and text not in ex:
            ex.append(text)
    multi = {eid: s for eid, s in used.items() if len(s) > 1}
    MERGE_CACHE.mkdir(parents=True, exist_ok=True)

    def cache_file(eid, senses):
        h = hashlib.sha1(json.dumps([eid, sorted(senses)]).encode()).hexdigest()
        return MERGE_CACHE / f"{h}.json"

    todo = [eid for eid, s in multi.items() if not cache_file(eid, s).exists()]
    if todo and not offline:
        print(f"sense merge: {len(multi)} entries used in several senses, {len(todo)} to ask")
    for i in range(0, 0 if offline else len(todo), MERGE_BATCH):
        chunk = todo[i:i + MERGE_BATCH]
        lines = []
        for eid in chunk:
            e = entries.get(str(eid), {})
            head = "・".join(e.get("k") or e.get("r") or ["?"])
            lines.append(f"\nword {eid}: {head}")
            for sn, exs in sorted(multi[eid].items()):
                gl = "; ".join(sense_glosses(eid, sn, entries)) or "?"
                lines.append(f"  sense {sn}: {gl}  e.g. {' / '.join(exs)}")
        try:
            out, _ = claude_cli.call("\n".join(lines) + "\n\nReturn groups of sense numbers per word id.",
                                     MERGE_SYSTEM, MERGE_SCHEMA, MERGE_MODEL, MERGE_EFFORT)
        except claude_cli.UsageLimit:
            print("sense merge: usage limit; unmerged entries keep their senses")
            break
        for item in out.get("words", []):
            if item["id"] in multi:
                e = entries.get(str(item["id"]), {})
                cache_file(item["id"], multi[item["id"]]).write_text(json.dumps(
                    {"id": item["id"], "word": (e.get("k") or e.get("r") or ["?"])[0],
                     "groups": item["groups"]}, ensure_ascii=False))

    canon = {}
    for eid, senses in multi.items():
        f = cache_file(eid, senses)
        if not f.exists():
            continue
        data = json.loads(f.read_text())
        for grp in data["groups"] if isinstance(data, dict) else data:
            grp = [s for s in grp if s in senses]
            if grp:
                # The lowest sense number names the group. (The first one used in
                # the deck was tried: every reordering then changed some keys.)
                first = min(grp)
                for s in grp:
                    canon[(eid, s)] = first
    return canon


# --- 3–4. order and examples ------------------------------------------------------

# What the corpus calls its groups of text, in words a player would use.
PLACES = {
    "new_game_intro": "Opening", "strings": "Menus and messages", "battle_message": "Battle messages",
    "battle_main": "Battle messages", "pokedex_text_fr": "Pokédex entries", "pokedex_entries": "Pokédex: species",
    "pokedex_rating": "Oak's Pokédex rating", "move_descriptions": "Move descriptions", "move_names": "Moves",
    "items": "Item descriptions", "abilities": "Abilities", "help_system": "Help menu", "fame_checker": "Fame Checker",
    "teachy_tv": "Teachy TV", "pokedude": "Teachy TV", "union_room_message": "Union Room", "union_room": "Union Room",
    "union_room_chat": "Union Room", "quest_log": "Quest log", "cable_club": "Cable Club",
    "flavor_text": "Things you look at", "event_scripts": "Common events", "poke_mart": "Poké Mart",
    "trainer_class_names": "Trainer classes", "trainers": "Trainers", "day_care": "Day Care", "daycare": "Day Care",
    "nature_names": "Natures", "field_moves": "Field moves", "ingame_trade": "In-game trades",
    "ingame_trades": "In-game trades", "white_out": "Blacking out", "save": "Saving", "pc": "PC",
    "pc_transfer": "PC", "route23": "Route 23", "safari_zone": "Safari Zone", "diploma": "Diploma",
    "obtain_item": "Getting an item", "aide": "Oak's aides", "surf": "Surf", "itemfinder": "Itemfinder",
    "seagallop": "Seagallop ferry", "berries": "Berries", "berry": "Berries", "trainer_card": "Trainer Card",
    "learn_move": "Learning a move", "mon_markings": "Pokémon markings", "keyboard_text": "Naming screen",
    "mystery_gift_menu": "Mystery Gift", "mystery_event_club": "Mystery Gift", "mystery_event_msg": "Mystery Gift",
    "trade": "Trading", "sign_lady": "Pallet Town", "repel": "Repel", "sprite": "Menus and messages",
    "species_names": "Pokémon", "region_map_entry_strings": "Town Map", "battle_interface": "Battle messages",
    "battle_script_commands": "Battle messages", "battle_anim_status_effects": "Battle messages",
    "field_player_avatar": "Menus and messages", "string_util": "Menus and messages", "item_menu": "Bag",
    "link_rfu_2": "Wireless link", "dodrio_berry_picking": "Dodrio Berry-Picking", "competitive_brothers": "Seven Island",
    "eon_ticket": "Mystery Gift", "description": "Menus and messages", "header": "Menus and messages",
    "pokemon": "Menus and messages", "test": "Menus and messages",
    # Places a walk in map_order.yaml names that are screens, not maps or text groups.
    "title_screen": "Title screen", "start_menu": "Start menu", "options_menu": "Options", "bag": "Bag",
    "party_menu": "Party screen", "pokedex": "Pokédex",
}
# Map names as the decomp spells them, to words: Route1, SSAnne, ProfessorOaksLab.
SPELLED = [(r"(?<=[a-z])(?=[A-Z0-9])|(?<=[A-Z])(?=[A-Z][a-z])|_", " "), (r"\bSS Anne\b", "S.S. Anne"),
           (r"\bMt\b", "Mt."), (r"\bCo\b", "Co."), (r"\bPokemon\b", "Pokémon"),
           (r"\b(Oak|Rival|Player|Copycat|Warden|Captain|Lorelei|Bruno|Agatha|Lance|Champion|Diglett|Mr Psychic)s\b", r"\1's"),
           (r"\bMr\b", "Mr."), (r" +", " ")]


def place_name(name: str) -> str:
    name = re.sub(r"^llm: ", "", str(name))
    if name in PLACES:
        return PLACES[name]
    if m := re.fullmatch(r"easy_chat_group_(\w+)", name):
        return "Easy chat words: " + m.group(1).replace("_", " ")
    lowercase = name == name.lower()  # a group the table lacks: its own name, tidied
    for pattern, to in SPELLED:
        name = re.sub(pattern, to, name)
    name = name.strip()
    return name.capitalize() if lowercase else name


def location(r) -> str:
    # A map_order entry that is a single label (gControlsGuide_Text_Intro,
    # Route22_Text_LateRivalIntro) only moves the line in time: the place is
    # still the message's own group.
    place = r.group if "_Text_" in str(r.first_seen) else r.first_seen
    # A line of a name list: the place where the thing is first met.
    if names.listed(r.group, r.label):
        place = re.sub(r"_Text_.*| \(.*", "", str(r.first_seen))
    return place_name(place if r.dialogue or names.listed(r.group, r.label) else r.group)


def english(r, analysis: dict) -> str:
    """The sentence's translation. A line of a name list is the name itself:
    the game's English name, not the model's guess at it."""
    return names.line_english(r.group, r.label, r.text) or analysis["english"]


def highlight(text: str, words: list[dict], pos: int, span=None) -> str:
    """The sentence with the word at `pos` in bold, or the stretch `span` for
    a name that is several words."""
    at = span if isinstance(span, tuple) else word_span(text, words, pos, form_of(words[pos]))
    return text if at is None else f"{text[:at[0]]}<b>{text[at[0]:at[1]]}</b>{text[at[1]:]}"


# Decks that can be studied without the story: every word in one of them has a
# card there, on its first sentence in that deck, even when the story has a
# card for the word too. The Help menu can be opened from the first screen, so
# it must not lean on what later chapters teach. The Link play deck is the
# other kind: it holds only what the story has not taught, and does not count
# on the Help deck either.
STAND_ALONE = ("help",)


def prepare(run_name: str = MAIN.name, offline_merge: bool = False) -> tuple[pd.DataFrame, dict, dict, pd.DataFrame]:
    """Deck rows with their card order, the JMdict entries, the analyses, and
    every word occurrence under its card key."""
    deck, entries = load()
    deck = deck.sort_values("first_seen_order").reset_index(drop=True)
    analyses = results(run_name, deck["text"].unique(), entries)
    occ = occurrences(deck, analyses, known_words())

    unified = unify_unlinked(occ)
    occ["own"] = [a == b for a, b in zip(occ["key"], unified)]  # False: filed under a card by its form alone
    occ["key"] = unified
    canon = merge_senses(occ, entries, offline=offline_merge)
    occ["key"] = occ["key"].map(
        lambda k: (k[0], k[1], canon.get((k[1], k[2]), k[2]), k[3]) if k[0] == "jm" else k)

    # Spread on card keys (other names and known words make no cards here).
    # A Pokémon or item name is first seen in dialogue or where its list line
    # puts it. A menu or battle line that mentions it has no exact place in
    # the story, so it does not count as showing the name.
    occ["exact"] = occ["row"].map(deck["dialogue"]) | occ["listed"]
    is_name = occ["key"].map(lambda k: k[0] == "name")
    anchored = set(occ["key"][is_name & occ["exact"]])
    cardable = occ[in_story(occ) & (occ["exact"] | ~occ["key"].isin(anchored))]
    # A bare name teaches its name. The words inside it (しっぽ in しっぽをふる) are
    # better learned from a real sentence, so they count here only when no
    # real sentence has them.
    elsewhere = set(cardable["key"][~cardable["name_line"]])
    cardable = cardable[~cardable["name_line"] | is_name[cardable.index] | ~cardable["key"].isin(elsewhere)]
    words = cardable.groupby("row")["key"].agg(lambda ks: frozenset(ks))
    words = words.reindex(deck.index).map(lambda x: x if isinstance(x, frozenset) else frozenset())
    pin = dict(zip(cardable["row"][cardable["listed"]], cardable["key"][cardable["listed"]]))
    deck["card_order"] = spread(deck, words, pin)
    models.use(deck)  # which model answers for which sentence follows from what each chapter shows
    return deck, entries, analyses, occ


def build_cards(run_name: str = MAIN.name, offline_merge: bool = False) -> pd.DataFrame:
    deck, entries, analyses, occ = prepare(run_name, offline_merge)
    gram = grammar.results(deck["text"].unique(), analyses)
    noted = notes.results(deck["text"].unique())
    readings = kanji_line.readings_from(analyses)
    corrections.check()  # every hand correction found what it corrects

    occ = occ.join(deck[["card_order", "dialogue", "chapter", "msg_id", "text", "speaker", "deck", "gender"]]
                   .rename(columns={"text": "_t"}), on="row")

    msg_text = deck.sort_values(["line_no", "page", "sent"]).groupby("msg_id")["text"].agg(list)

    rows = list(deck.itertuples())  # deck's index is 0…n-1: a row by its number

    def one_deck(sub: pd.DataFrame, alone: str | None = None) -> list[dict]:
        """One card per key. `alone`: only the cards of this deck, each on its
        first sentence there, whatever the other decks have already taught;
        without it, the cards of every other deck."""
        # Plain lists and dicts from here on: filtering a DataFrame once per
        # card took three quarters of the stage's time.
        uses: dict[tuple, list[dict]] = {}
        for u in sub.to_dict("records"):
            # A use can be a card's sentence only in its own run; any use can be an extra example.
            mine = (u["deck"] == alone) if alone else (u["deck"] not in STAND_ALONE)
            u["placed"] = mine and u["card_order"] == u["card_order"]  # not NaN
            uses.setdefault(u["key"], []).append(u)
        out = []
        for key, g in uses.items():
            placed = sorted((u for u in g if u["placed"]), key=lambda u: u["card_order"])
            if not placed:
                continue
            if key[0] != "name":
                # A real sentence before a bare name the word happens to be part of.
                placed = [u for u in placed if not u["name_line"]] or placed
            # The word is one only a boy, or only a girl, ever meets when every
            # sentence that could carry its card is that side's. A word that
            # also turns up in a line everyone sees is not tagged, wherever
            # its card sits: dropping the tag must not drop a word you need.
            sides = {u["gender"] for u in placed}
            only = next(iter(sides)) if len(sides) == 1 else ""
            first = placed[0]
            if key[0] == "name":
                first = next((u for u in placed if u["exact"]), first)
            elif not first["dialogue"]:
                first = next((u for u in placed if u["dialogue"] and u["chapter"] == first["chapter"]), first)
            later, seen = [], {first["text"]}
            for u in g:
                if u["text"] not in seen:
                    seen.add(u["text"])
                    later.append(u)
            # A use filed here by its form alone may be another sense: show it last.
            later.sort(key=lambda u: (not u["own"], not u["dialogue"], not u["placed"], u["card_order"] if u["placed"] else 0))
            w, r = first["word"], rows[first["row"]]
            a = analyses[first["text"]]
            # A name-list line whose card is the ordinary word (たいあたり): the
            # card still says what the English game calls it.
            if not w.get("name") and len(a["words"]) == 1 and (eng := names.line_english(r.group, r.label, r.text)):
                w = {**w, "in_context": f"{eng} ({names.KIND[names.kind_of(r.group, r.label)]})"}
            ctx = " ".join(f"<u>{t}</u>" if t == first["text"] else t for t in msg_text[first["msg_id"]])
            extras = [
                f"{highlight(u['text'], analyses[u['text']]['words'], u['pos'], u['span'])}"
                f" — {english(rows[u['row']], analyses[u['text']])}"
                + (f" ({u['word']['surface']}: {u['word']['modifiers']})" if u["word"].get("modifiers") else "")
                for u in later[:MAX_EXTRA]
            ]
            e = entries.get(str(w.get("jmdict_id")), {}) if w.get("jmdict_id") else {}
            g = gram.get(first["text"])
            out.append({
                # A stand-alone deck's card is a note of its own beside the story's card for the word.
                "key": json.dumps([alone, *key] if alone else key, ensure_ascii=False), "order": first["card_order"],
                "deck": r.deck,  # story, help or link: the deck of the card's sentence
                # Within a sentence: the words left to right, a name before a word inside it.
                "_at": (first["pos"], key[0] != "name"),
                "Sentence": highlight(first["text"], a["words"], first["pos"], first["span"]),
                "Word": form_of(w), "Base": w["base"], "Kanji": w.get("kanji") or "",
                "UsuallyKana": "yes" if w.get("usually_kana") else "",
                "WordRomaji": romaji(form_of(w), particle=is_particle(w)),
                "BaseRomaji": romaji(base_kana(w, e), particle=is_particle(w)),
                "SentenceRomaji": sentence_in_romaji(first["text"], a),
                "CharReadings": clean_char_readings(
                    w.get("kanji") or "", base_kana(w, e), w.get("char_readings") or ""),
                "Literal": literal(w, dict_sense(w, e)),
                "InContext": w.get("in_context") or "", "Modifiers": w.get("modifiers") or "",
                "Note": w.get("note") or "",
                # Not beside a name's own note, which says the same thing better.
                "SentenceNote": "" if w.get("note") else noted.get(first["text"], ""),
                "DictSense": dict_sense(w, e),
                "Onomatopoeia": "onomatopoeic or mimetic word" if is_onomatopoeia(w, e) else "",
                "Grammar": "".join(map(grammar.pattern_html, grammar.word_patterns(
                    g, first["text"], a["words"], first["pos"]))) if g else "",
                "Breakdown": grammar.breakdown_html(g) if g else "",
                "SentenceKanji": kanji_line.spaced(first["text"], a["kanji"], readings),
                "SentenceEnglish": english(r, a),
                "ExtraExamples": "<br>".join(extras), "Context": ctx,
                "Location": location(r), "MessageId": r.msg_id,
                # Named by the line itself (オーキド『…), or else read from the map.
                "Speaker": first["speaker"] or speakers.who(r.label, first["text"]),
                "Dictionary": dictionary(w, e),
                "Placeholders": placeholders.hint(r.text, r.vars, r.label),
                "tags": [re.sub(r"\W", "_", location(r))]
                + (["proper"] if w.get("proper") else [])
                + ([w["name"]] if w.get("name") else [])
                + (["low-confidence"] if unsure(w, e) else [])
                + (["sense-guessed"] if w.get("_sense_guessed") else [])
                + (["onomatopoeia"] if is_onomatopoeia(w, e) else [])
                + (["fragment"] if is_fragment(first["text"], w) and not w.get("name") else [])
                # A word only one gender of player meets; a bare word from the easy-chat lists.
                + ([f"player-{only}"] if only else [])
                + (["word-list"] if r.group.startswith("easy_chat_group_") else []),
            })
        return out

    made = occ[in_story(occ)]
    out = one_deck(made) + [c for name in STAND_ALONE for c in one_deck(made, name)]
    out.sort(key=lambda c: (c["order"], c.pop("_at")))
    df = pd.DataFrame(out)
    df["Order"] = [f"{i:06d}" for i in range(len(df))]
    return df


def main() -> None:
    run_name = sys.argv[1] if len(sys.argv) > 1 else MAIN.name
    cards = build_cards(run_name)
    CARDS_OUT.parent.mkdir(parents=True, exist_ok=True)
    cards.to_parquet(CARDS_OUT, index=False)

    # The spec's check before trusting the deck: 30 random cards as text.
    rnd = random.Random(7)
    lines = []
    for _, c in cards.iloc[sorted(rnd.sample(range(len(cards)), min(30, len(cards))))].iterrows():
        lines.append(
            f"#{c.Order} [{c.Location}] {' '.join(c.tags)}\n"
            f"  {re.sub('<.*?>', '', c.Sentence)}\n"
            f"  {c.Word} → {c.Kanji or c.Base}【{c.Base}】 {c.CharReadings}\n"
            f"  literal: {c.Literal} | here: {c.InContext}\n"
            f"  {c.Modifiers}\n  {c.SentenceKanji}\n  {c.SentenceEnglish}\n")
    SPOTCHECK_OUT.write_text("\n".join(lines), encoding="utf-8")

    named = sum(k.startswith('["name"') for k in cards.key)
    print(f"cards {len(cards)}, {named} of them names "
          f"(grammar on {sum(cards.Breakdown != '')} cards, a pattern line on {sum(cards.Grammar != '')})")
    print(f"low-confidence {sum('low-confidence' in t for t in cards.tags)}, "
          f"sense guessed {sum('sense-guessed' in t for t in cards.tags)}")
    print(f"→ {CARDS_OUT.relative_to(ROOT)}, {SPOTCHECK_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
