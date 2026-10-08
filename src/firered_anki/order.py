"""Stage 2: put every sentence in story order.

Dialogue goes by map_order.yaml. Everything else (menus, battle text, moves,
items, Pokédex…) goes where it is first seen, by first_seen.yaml plus the pret
decomp. Unplaceable rows go to the end, most frequent first.
Run: uv run python -m firered_anki.order
"""

import fnmatch
import math
import re
from collections import Counter

import pandas as pd
import yaml

from . import blockers, walking
from .decomp import STARTER_LEVEL, STARTER_MAP, STARTERS, Decomp, norm
from .lemmas import lemmas
from .map_order import MapOrder
from .spread import CHAPTER_ENDS, DECK_OF, HELP, LINK_PLAY, chapter_of, spread
from .paths import CORPUS, DATA, EXTRACT_OUT, ROOT

FIRST_SEEN = ROOT / "first_seen.yaml"
LLM_OVERRIDES = ROOT / "first_seen_llm.yaml"  # written by the classify stage
ORDER_OUT = DATA / "02_order" / "sentences.parquet"
# Every row placed only by a catch-all rule, before LLM overrides and
# exclusions: the classify stage's input.
FALLBACK_OUT = DATA / "02_order" / "fallback.parquet"
UNPLACED_OUT = DATA / "unplaced.csv"
NEVER_OUT = DATA / "never_shown.csv"
LATER_OUT = DATA / "moved_later.csv"  # lines moved to after the map that sets the flag they wait for
BEFORE_OUT = DATA / "before_blockers.csv"  # lines moved to before the scene that stops you
# Text FireRed never shows is left out of the deck, and so of every model
# stage: Ruby/Sapphire data no FireRed player meets (Hoenn Pokédex text,
# unobtainable items, moves nothing knows), text the decomp marks unused, and
# text the English game left in Japanese.
NEVER_MET = ("never met", "no move with this effect is met")
# The translators skipped text no script shows, so a line whose English copy
# is still Japanese is a leftover: Hoenn's Safari Zone script, berry tags, the
# rival's lines for battles you cannot lose and carry on. ("Any unused text
# was left untranslated", says the decomp's safari_zone.inc.) Link play and
# other end-of-deck text is kept: an English decomp cannot say what the
# Japanese release's own features (the e-Reader) showed.
KANA = re.compile(r"[ぁ-ヿ]")
UNTRANSLATED = "left in Japanese in the English game: no script shows it"

# The game's start is listed line by line in map_order.yaml: the opening, your
# house, Pallet Town, the scene in Oak's lab. Text with no exact place floats
# only once you have left the lab with your first Pokémon: "start" text (the
# title menu, the naming screen, the rest of the menus), battle text, and
# whatever a rule files under a place before that. This is the last entry
# before you step out: the rest of the lab, after the rival's battle. A
# name's own line (a starter's moves, its type) is not floating text and
# stays where it is met.
FLOATS_AFTER = "PalletTown_ProfessorOaksLab"
# gTypeNames.N is in this order (include/constants/pokemon.h).
TYPES = ["TYPE_NORMAL", "TYPE_FIGHTING", "TYPE_FLYING", "TYPE_POISON", "TYPE_GROUND", "TYPE_ROCK", "TYPE_BUG",
         "TYPE_GHOST", "TYPE_STEEL", "TYPE_MYSTERY", "TYPE_FIRE", "TYPE_WATER", "TYPE_GRASS", "TYPE_ELECTRIC",
         "TYPE_PSYCHIC", "TYPE_ICE", "TYPE_DRAGON", "TYPE_DARK"]
COMPUTED = {
    "type",  # a type's name → the first Pokémon of that type
    "species", "move", "item", "ability", "trainer", "trainer_class", "mapsec", "pokedex",
    "berry",  # a berry name or description → that berry item
    "named",  # a move or ability named in the label → that move or ability
    "battle_effect",  # a battle message → the first move whose effect prints it
    "area_desc",  # a Town Map area description → that area's map section
}


def load_overrides() -> dict[str, str]:
    """msg_id → anchor chosen by the classify stage for catch-all rows."""
    if not LLM_OVERRIDES.exists():
        return {}
    return yaml.safe_load(LLM_OVERRIDES.read_text(encoding="utf-8")) or {}


class FirstSeen:
    """First-seen rank of game things, from the decomp and the map order."""

    def __init__(self, mo: MapOrder, dc: Decomp):
        self.mo, self.dc = mo, dc
        # A decomp map with no text of its own ranks with the earliest listed
        # map in its region map section (MtMoon_B1F → MtMoon_1F), else with the
        # listed map its name extends (…TanobyRuins_MoneanChamber → …TanobyRuins).
        by_sec: dict[str, float] = {}
        for name, sec in dc.mapsec.items():
            if name in mo.rank:
                by_sec[sec] = min(by_sec.get(sec, math.inf), mo.rank[name])

        def rank_of(name: str, sec: str) -> float:
            if name in mo.rank:
                return mo.rank[name]
            if sec in by_sec:
                return by_sec[sec]
            parts = name.split("_")
            for i in range(len(parts) - 1, 0, -1):
                if (prefix := "_".join(parts[:i])) in mo.rank:
                    return mo.rank[prefix]
            return math.inf

        self.map_rank = {name: rank_of(name, sec) for name, sec in dc.mapsec.items()}
        self.map_rank[STARTER_MAP] = mo.rank[STARTER_MAP]

        self.species: dict[str, tuple[float, str]] = {}
        self.move: dict[str, tuple[float, str]] = {}
        self.item: dict[str, tuple[float, str]] = {}
        self.trainer: dict[str, tuple[float, str]] = {}
        # How a species, move or ability is first seen, finer than its map:
        # (kind, constant) → (the trainer who sends it out, or None for a wild
        # or gift Pokémon; the number of the sighting). One sighting is one
        # Pokémon, so a species, the moves it shows and its ability share a
        # number and stay together in the deck.
        self.fine: dict[tuple[str, str], tuple[str | None, float]] = {}
        self._sightings = 0
        self._build()

    @staticmethod
    def _see(table: dict, key: str, rank: float, where: str) -> None:
        if rank < table.get(key, (math.inf, ""))[0]:
            table[key] = (rank, where)

    def _see_at(self, kind: str, key: str, rank: float, where: str, trainer: str | None, n: float) -> None:
        """_see that also records the sighting. On the same map a trainer's
        Pokémon wins over a wild one: its place in the story is exact."""
        table = getattr(self, kind)
        old = table.get(key, (math.inf, ""))[0]
        if rank < old or (rank == old and trainer and not self.fine.get((kind, key), (None, 0))[0]
                          and not math.isinf(rank)):
            table[key] = (rank, where)
            self.fine[(kind, key)] = (trainer, n)

    def _rank(self, m: str) -> float:
        return self.map_rank.get(m, math.inf)

    def _build(self) -> None:
        dc, refs = self.dc, self.dc.script_refs
        for const, maps in refs["item"].items():
            for m in maps:
                self._see(self.item, const, self._rank(m), m)
        for const, maps in refs["species"].items():  # gifts, trades, Pokémon standing on the map
            self._sightings += 1
            for m in maps:
                self._see_at("species", const, self._rank(m), m, None, self._sightings)
        # A trainer is met where their battle text is placed, which follows the
        # map_order label overrides: the rival's second Route 22 battle counts
        # from Route22_Text_LateRival…, not from the first visit to Route 22.
        for const, maps in refs["trainer"].items():
            placed = False
            for m, text in refs["trainer_text"].get(const, ()):
                bucket, entry = self.mo.resolve(m, text)
                # League rematch teams (…_2, …_REMATCH_…) reuse the first
                # visit's defeat text; they belong with the rematch intro.
                rematch = f"{m}_Text_RematchIntro"
                if re.search(r"_2$|REMATCH", const) and rematch in self.mo.rank:
                    entry = rematch
                if bucket == "order":
                    self._see(self.trainer, const, self.mo.rank[entry], entry)
                    placed = True
            if not placed:
                for m in maps:
                    self._see(self.trainer, const, self._rank(m), m)

        def see_mon(sp: str, lvl: int, moves, rank: float, where: str, trainer: str | None = None) -> None:
            self._sightings += 1
            self._see_at("species", sp, rank, where, trainer, self._sightings)
            for mv in moves or dc.moves_at(sp, lvl):
                if mv != "MOVE_NONE":
                    self._see_at("move", mv, rank, where, trainer, self._sightings)

        for sp in STARTERS:
            see_mon(sp, STARTER_LEVEL, None, self._rank(STARTER_MAP), STARTER_MAP)
        seen_wild: dict[tuple, None] = {}  # a map lists a species once per slot
        for m, sp, lvl, need in dc.wild:
            seen_wild.setdefault((m, sp, lvl, need))
        for m, sp, lvl, need in seen_wild:
            # A surfing or fishing encounter is first seen once you have both
            # the map and the HM or rod.
            rank = max(self._rank(m), self.item.get(need, (math.inf, ""))[0]) if need else self._rank(m)
            see_mon(sp, lvl, None, rank, m)
        for tr, (rank, where) in self.trainer.items():
            for sp, lvl, moves in dc.trainer_info.get(tr, {}).get("party", []):
                see_mon(sp, lvl, moves, rank, where, tr)
        for it, mv in dc.tmhm_moves.items():
            if it in self.item:
                self._sightings += 1
                self._see_at("move", mv, *self.item[it], None, self._sightings)

        # A species only reachable by evolving (Vaporeon, Kabutops) is first seen
        # with the species it evolves from. Repeat for two-stage chains.
        for _ in range(2):
            for pre, post in dc.evolutions:
                if post not in self.species and pre in self.species:
                    rank, where = self.species[pre]
                    trainer, n = self.fine.get(("species", pre), (None, 0))
                    self._see_at("species", post, rank, f"{where} (evolves from {pre})", trainer, n + 0.1)

        self.ability: dict[str, tuple[float, str]] = {}
        for sp, (rank, where) in self.species.items():
            for ab in dc.species_abilities.get(sp, []):
                self._see_at("ability", ab, rank, where, *self.fine.get(("species", sp), (None, 0)))
        self.type: dict[str, tuple[float, str]] = {}
        for sp, (rank, where) in self.species.items():
            for ty in dc.species_types.get(sp, []):
                self._see_at("type", ty, rank, where, *self.fine.get(("species", sp), (None, 0)))
        self.trainer_class: dict[str, tuple[float, str]] = {}
        for tr, (rank, where) in self.trainer.items():
            if cls := dc.trainer_info.get(tr, {}).get("class"):
                self._see(self.trainer_class, cls, rank, where)
        # A battle message is first seen with the earliest move whose effect
        # prints it; inf when none of those moves appears in FireRed.
        self.battle_effect: dict[str, tuple[float, str]] = {}
        for label, effects in dc.battle_string_effects.items():
            self.battle_effect[label] = (math.inf, "no move with this effect is met")
            for eff in effects:
                for mv in dc.effect_moves.get(eff, []):
                    if mv in self.move:
                        rank, where = self.move[mv]
                        self._see(self.battle_effect, label, rank, f"{where} ({mv})")
        self.mapsec: dict[str, tuple[float, str]] = {}
        for m, sec in dc.mapsec.items():
            self._see(self.mapsec, sec, self._rank(m), m)
        dex_rank = self.mo.rank["pokedex_rating"]
        self.pokedex = {
            sp: (max(r, dex_rank), where) for sp, (r, where) in self.species.items()
        }

    def effect_matching(self, pattern: str) -> tuple[float, str]:
        """First time any move whose battle effect matches is seen."""
        rx = re.compile(pattern)
        hits = [
            (self.move[mv][0], f"{self.move[mv][1]} ({mv})")
            for eff, moves in self.dc.effect_moves.items() if rx.fullmatch(eff)
            for mv in moves if mv in self.move
        ]
        return min(hits) if hits else (math.inf, "no move with this effect is met")

    def items_matching(self, pattern: str) -> tuple[float, str]:
        rx = re.compile(pattern)
        hits = [v for k, v in self.item.items() if rx.fullmatch(k)]
        return min(hits) if hits else (math.inf, "")


class Keys:
    """Corpus label → decomp constant, per computed kind."""

    def __init__(self, dc: Decomp):
        inv = lambda d: {v: k for k, v in d.items()}
        self.species_by_id = inv(dc.species)
        self.move_by_id = inv(dc.moves)
        self.item_by_id = inv(dc.items)
        self.ability_by_id = inv(dc.abilities)
        self.trainer_by_id = inv(dc.trainers)
        self.class_by_id = inv(dc.trainer_classes)
        self.species_by_norm = {norm(k[8:]): k for k in dc.species}
        self.move_by_norm = {norm(k[5:]): k for k in dc.moves}
        self.ability_by_norm = {norm(k[8:]): k for k in dc.abilities}
        # Map-name labels drop the é (sMapsecName_POKMONTOWER) and Kanto's
        # sections are KANTO_-prefixed, so join on the English name instead.
        en = dict(zip(
            (CORPUS / "qid_msg.txt").read_text(encoding="utf-8").split("\n"),
            (CORPUS / "en_msg.txt").read_text(encoding="utf-8").split("\n"),
        ))
        sec_by_name: dict[str, str] = {}
        for s in dc.mapsec_sections:
            if "name" in s and not s.get("name_clone"):
                sec_by_name.setdefault(s["name"], s["id"])
        self.mapsec_by_label = {
            qid.rsplit(".", 1)[-1]: sec_by_name.get(text)
            for qid, text in en.items()
            if ".region_map_entry_strings." in qid
        }
        self.battle_labels = set(dc.battle_string_effects)
        # Town Map area descriptions name their section: AreaDesc_SafariZone →
        # MAPSEC_KANTO_SAFARI_ZONE.
        self.mapsec_by_norm = {}
        for sec in sorted(set(dc.mapsec.values())):
            self.mapsec_by_norm.setdefault(norm(sec[len("MAPSEC_"):].replace("KANTO_", "")), sec)
        self.item_by_norm = {norm(k[5:]): k for k in dc.items}
        self.cheri = dc.items["ITEM_CHERI_BERRY"]
        # Move and ability names as word tuples, for finding them inside
        # battle-message labels (sText_PkmnSappedByLeechSeed → Leech Seed).
        self.named = sorted(
            [(tuple(k[5:].lower().split("_")), "move", k) for k in dc.moves if k != "MOVE_NONE"]
            + [(tuple(k[8:].lower().split("_")), "ability", k) for k in dc.abilities if k != "ABILITY_NONE"],
            key=lambda t: -len(t[0]),
        )
        nat_by_id = inv(dc.national_dex)
        self.species_by_dex = {
            n: "SPECIES_" + k[len("NATIONAL_DEX_"):] for n, k in nat_by_id.items()
        }

    def key(self, kind: str, label: str) -> str | None:
        num = re.search(r"\.(\d+)$", label)
        n = int(num.group(1)) if num else None
        if kind == "species":
            return self.species_by_id.get(n)
        if kind == "type":
            return TYPES[n] if n is not None and n < len(TYPES) else None
        if kind == "pokedex":
            if m := re.fullmatch(r"g(\w+?)PokedexText", label):
                return self.species_by_norm.get(norm(m.group(1)))
            return self.species_by_dex.get(n) if n else None
        if kind == "move":
            if m := re.fullmatch(r"gMoveDescription_(\w+)", label):
                return self.move_by_norm.get(norm(m.group(1)))
            return self.move_by_id.get(n)
        if kind == "item":
            if m := re.fullmatch(r"gItemDescription_(ITEM_\w+)", label):
                return m.group(1)
            return self.item_by_id.get(n)
        if kind == "ability":
            if m := re.fullmatch(r"s(\w+)Description", label):
                return self.ability_by_norm.get(norm(m.group(1)))
            return self.ability_by_id.get(n)
        if kind == "trainer":
            return self.trainer_by_id.get(n)
        if kind == "trainer_class":
            return self.class_by_id.get(n)
        if kind == "mapsec":
            return self.mapsec_by_label.get(label)
        if kind == "berry":
            if m := re.fullmatch(r"sBerryDescriptionPart\d_(\w+)", label):
                return self.item_by_norm.get(norm(m.group(1) + "Berry"))
            return self.item_by_id.get(self.cheri + n) if n is not None else None
        if kind == "named":
            return self.named_in(label)
        if kind == "area_desc":
            if m := re.search(r"AreaDesc_(\w+)$", label):
                return self.mapsec_by_norm.get(norm(m.group(1)))
            return None
        if kind == "battle_effect":
            return label if label in self.battle_labels else None
        return None

    def named_in(self, label: str) -> str | None:
        """The longest move or ability name spelled as whole words in a label."""
        body = re.sub(r"^[gs](?:Battle)?Text_", "", label)
        words = [w.lower() for w in re.findall(r"[A-Z][a-z]+|[A-Z]+(?![a-z])|\d+", body)]
        for name, _, const in self.named:
            n = len(name)
            if any(tuple(words[i:i + n]) == name for i in range(len(words) - n + 1)):
                return const
        return None


class Placer:
    def __init__(self, mo: MapOrder, fs: FirstSeen, keys: Keys, cfg: dict):
        self.mo, self.fs, self.keys = mo, fs, keys
        self.groups = cfg["groups"]
        # After the story: the Help deck, then the Link play deck.
        self.help_rank = float(len(mo.order) + 1)
        self.end_rank = float(len(mo.order) + 2)
        self.stats: Counter = Counter()

    def rules_for(self, name: str):
        if name in self.groups:
            return self.groups[name]
        for pat, rule in self.groups.items():
            if fnmatch.fnmatchcase(name, pat):
                return rule
        return None

    def anchor_for(self, name: str, label: str) -> tuple[str | None, bool]:
        """→ (anchor, whether it came from a catch-all '.' rule)."""
        rule = self.rules_for(name)
        if isinstance(rule, list):
            for rx, anchor in rule:
                if re.search(rx, label):
                    return anchor, rx == "."
            return None, False
        return rule, False

    def place(self, name: str, label: str) -> tuple[str, float, str, bool]:
        """→ (bucket, rank, where, fallback). bucket: anchored | computed | start |
        end | exclude | unplaced. fallback: placed only by a catch-all rule, so a
        candidate for the LLM classification pass.

        An anchor "a|b" tries a, and falls back to b when a can't place the row.
        """
        anchor, catch_all = self.anchor_for(name, label)
        if anchor is None:
            return "unplaced", self.end_rank, f"no rule for {name}", False
        # item:<regex> and effect:<regex> may contain |, so they are never split.
        *tries, last = [anchor] if anchor.startswith(("item:", "effect:")) else anchor.split("|")
        for a in tries:
            placed = self.place_one(a, label)
            if placed[0] != "unplaced":
                return (*placed, False)
        return (*self.place_one(last, label), catch_all)

    def place_one(self, anchor: str, label: str) -> tuple[str, float, str]:
        if anchor == "exclude":
            return "exclude", math.nan, ""
        if anchor == "start":
            return "start", self.mo.rank[FLOATS_AFTER] + 0.5, "start"
        if anchor == "help":
            return "help", self.help_rank, "help"
        if anchor == "end":
            return "end", self.end_rank, "end"
        if anchor.startswith("item:"):
            rank, where = self.fs.items_matching(anchor[5:])
            return self._computed(anchor, rank, where)
        if anchor.startswith("effect:"):
            rank, where = self.fs.effect_matching(anchor[7:])
            if math.isinf(rank):
                return "end", self.end_rank, f"{anchor}: {where}"
            self.stats[("effect:", "placed")] += 1
            return "computed", rank, where
        if anchor in COMPUTED:
            const = self.keys.key(anchor, label)
            if const is None:
                self.stats[(anchor, "no key")] += 1
                return "unplaced", self.end_rank, f"{anchor}: no key"
            table = {"berry": "item", "area_desc": "mapsec",
                     "named": const.split("_", 1)[0].lower()}.get(anchor, anchor)
            rank, where = getattr(self.fs, table).get(const, (math.inf, ""))
            if anchor == "battle_effect" and math.isinf(rank):
                # Traced to moves FireRed never shows: not seen in a normal game.
                self.stats[(anchor, "never met → end")] += 1
                return "end", self.end_rank, f"battle_effect: {where}"
            return self._computed(anchor, rank, where or const)
        if anchor in self.mo.rank:
            return "anchored", float(self.mo.rank[anchor]), anchor
        raise ValueError(f"first_seen.yaml: unknown anchor {anchor!r} for {label}")

    def _computed(self, kind: str, rank: float, where: str):
        if math.isinf(rank):
            self.stats[(kind, "never met")] += 1
            return "unplaced", self.end_rank, f"{kind}: never met ({where})"
        self.stats[(kind, "placed")] += 1
        return "computed", rank, where


def help_place(label: str, line_no: int, asked: dict[str, int]) -> float:
    """Where a Help line sorts: an answer goes right after the line it
    answers (Help_Text_AnswerX, HowToX and DefineX after Help_Text_X;
    HowToUseX after UsingX)."""
    if m := re.fullmatch(r"Help_Text_(Answer|HowTo|Define)(\w+)", label):
        kind, what = m.groups()
        titles = [f"Help_Text_{what}"] + ([f"Help_Text_Using{what[3:]}"] if kind == "HowTo" and what.startswith("Use") else [])
        for title in titles:
            if title in asked:
                return asked[title] + 0.5
    return float(line_no)


NAME_LINES = {"species_names": "species", "battle_main": "type", "move_names": "move", "abilities": "ability"}
KIND_ORDER = {"species": 0, "type": 1, "move": 2, "ability": 3}


def within_map(msgs: pd.DataFrame, placed: dict, dc: Decomp, mo: MapOrder, map_rank: dict) -> dict[str, float]:
    """Dialogue label → where it sorts inside its map_order entry.

    A map's lines go in the order you walk past the people and signs that say
    them (walking.py); a map this cannot be worked out for keeps the text
    dump's order. Then a line that waits for something the same map sets is
    put just after the lines it has to follow: by the way of showing it that
    opens first, when a script has several. A condition an earlier map can
    set already holds on arrival and holds nothing back (Daisy's "an errand
    for Grandpa?" waits for the lab)."""
    ways, set_in, set_with = dc.shown_when, dc.set_in, dc.set_with
    rows = [r for r in msgs.itertuples(index=False) if placed[r.msg_id][0] == "dialogue" and r.ns == "script"]
    at = {r.label: float(r.line_no) for r in rows}
    rank = {r.label: placed[r.msg_id][1] for r in rows}
    in_entry: dict[float, list] = {}
    for r in sorted(rows, key=lambda r: r.line_no):
        in_entry.setdefault(rank[r.label], []).append(r.label)
    for where, labels in in_entry.items():
        name = mo.order[int(where)] if where == int(where) and where < len(mo.order) else None
        if name in dc.map_json and (walked := walking.order(dc, name, labels, map_rank)):
            at.update((label, float(i)) for i, label in enumerate(walked))
    held: dict[str, list[list[str]]] = {}    # label → for each way a script shows it, the conditions its own map sets
    for r in rows:
        if r.label in mo.place or r.label in mo.rank:
            continue
        own = dc.map_of_label(r.label) or r.group
        here = map_rank.get(own, math.inf)
        local = [[c for c in must if own in set_in.get(c, ()) and all(map_rank.get(m, math.inf) >= here for m in set_in[c])]
                 for must, _ in ways.get(r.label, ())]
        if local and all(local):             # every way waits for something here
            held[r.label] = local
    last_free: dict[float, float] = {}  # entry → its last line that waits for nothing here
    for r in rows:
        if r.label not in held:
            last_free[rank[r.label]] = max(last_free.get(rank[r.label], -math.inf), at[r.label])
    for _ in range(5):                  # a line can follow a line that was itself moved
        changed = False
        for label, alts in held.items():
            after = math.inf
            for conds in alts:          # the way that opens first
                before = [at[t] for c in conds for t in set_with.get(c, ())
                          if t != label and t in at and rank[t] == rank[label]]
                after = min(after, (max(before) if before else last_free.get(rank[label], -math.inf)) + 0.25)
            if after > at[label]:
                at[label], changed = after, True
        if not changed:
            break
    return at


STEP = 1e-6  # how far apart the lines moved before a blocker sit: all of them inside the gap before its scene


def positions(msgs: pd.DataFrame, placed: dict, within: dict) -> dict[str, tuple[float, float]]:
    """Dialogue label → where it sorts: (its entry, its place inside it)."""
    return {r.label: (placed[r.msg_id][1], within.get(r.label, float(r.line_no)) if r.ns == "script" else float(r.line_no))
            for r in msgs.itertuples(index=False) if placed[r.msg_id][0] == "dialogue"}


def before_blockers(msgs: pd.DataFrame, placed: dict, within: dict, dc: Decomp, mo: MapOrder) -> list[tuple]:
    """Move every line that can be read before a blocker, and is placed after
    it, to just before the blocker's scene. Changes `placed` and `within`.
    → (blocker, the scene's first line, label, the label's map, where it was).

    Only a line of a map's own entry moves: a line listed by hand, alone or
    in a walk, stays. The lines moved before one blocker keep the order they
    had. Moving a line can make another readable (a gym leader's badge
    speech, once the challenge is in front of the blocker), so this repeats
    until nothing is left."""
    msg_of = {r.label: r.msg_id for r in msgs.itertuples(index=False)}
    ground = blockers.Ground(dc)
    out, n = [], 0
    for _ in range(8):
        pos = positions(msgs, placed, within)
        scene_at = {at: t for t, at in pos.items()}
        found: dict[str, tuple] = {}
        for b, moment, t, m in blockers.late(blockers.Sides(dc, ground, pos, pos)):
            if t not in mo.rank and t not in mo.place and placed[msg_of[t]][2] in dc.map_json:
                if t not in found or moment < found[t][0]:
                    found[t] = (moment, b, m)
        if not found:
            break
        for t in sorted(found, key=pos.get):
            moment, b, m = found[t]
            bucket, _, where, after, fallback = placed[msg_of[t]]
            n += 1
            placed[msg_of[t]] = (bucket, moment[0], where, after, fallback)
            within[t] = moment[1] - 10000 * STEP + n * STEP  # after the ones moved earlier, before the scene
            out.append((b.name, scene_at[moment], t, m, where))
    return out


def fine_places(msgs: pd.DataFrame, placed: dict, placer: Placer, dc: Decomp,
                within: dict[str, float] | None = None, close: frozenset = frozenset(),
                moved: frozenset = frozenset()) -> dict[str, tuple]:
    """Where inside its map the line that names a Pokémon, a move, an ability
    or a place goes: msg_id → (rank, after_dialogue, tiebreak or None,
    sighting, kind order).

      - A Pokémon first met in a trainer's team, with its type, the moves it
        shows and its ability, goes right after that trainer's challenge (or
        right before their defeat line, when there is no challenge line).
      - Wild and gift Pokémon have no line to hang on. A map's sightings are
        spread evenly between its messages, each Pokémon still followed by
        its moves and ability.
      - A place's name comes up as you walk in: before the map's dialogue.
    """
    fs, keys = placer.fs, placer.keys
    line = {**dict(zip(msgs["label"], msgs["line_no"])), **(within or {})}  # where each line sorts in its entry
    said: dict[float, list[int]] = {}  # rank → those positions, for its dialogue messages
    for r in msgs.itertuples(index=False):
        if placed[r.msg_id][0] == "dialogue":
            said.setdefault(placed[r.msg_id][1], []).append(line[r.label] if r.ns == "script" else r.line_no)
    for lines in said.values():
        lines.sort()
    rank_of_label = {r.label: placed[r.msg_id][1] for r in msgs.itertuples(index=False)
                     if placed[r.msg_id][0] == "dialogue"}

    def hang(trainer: str, rank: float) -> tuple[float, float] | None:
        """Where to sort, as (rank, line number): just after the challenge,
        or just before the defeat line, of a trainer whose text is at this
        rank, or was moved from it (`moved`): to before a blocker (`close`:
        those lines sit a small step apart) or to after what it waits for."""
        mine = sorted(t for _, t in dc.script_refs["trainer_text"].get(trainer, ()) if t in rank_of_label)
        texts = [t for t in mine if rank_of_label[t] == rank] or [t for t in mine if t in moved]
        for suffix, shift in (("Intro", 0.5), ("Defeat", -0.5)):
            for t in texts:
                if t.endswith(suffix):
                    return rank_of_label[t], line[t] + shift * (STEP if t in close else 1)
        return (rank_of_label[texts[0]], line[texts[0]] + 0.5 * (STEP if texts[0] in close else 1)) if texts else None

    out, loose = {}, {}
    for r in msgs.itertuples(index=False):
        bucket, rank, _, _, _ = placed[r.msg_id]
        if bucket != "computed":
            continue
        if r.group == "region_map_entry_strings":
            out[r.msg_id] = (rank, -1, None, 0.0, 0)
            continue
        kind = NAME_LINES.get(r.group)
        if not kind or (kind == "ability" and not r.label.startswith("gAbilityNames.")):
            continue
        trainer, n = fs.fine.get((kind, keys.key(kind, r.label)), (None, 0.0))
        at = hang(trainer, rank) if trainer else None
        if at is not None:
            out[r.msg_id] = (at[0], 0, at[1], n, KIND_ORDER[kind])
        else:
            loose.setdefault(rank, []).append((n, KIND_ORDER[kind], r.msg_id))
    for rank, rows in loose.items():
        sightings = sorted({n for n, _, _ in rows})
        lines = said.get(rank, [])
        for n, kind_order, msg_id in rows:
            i = (sightings.index(n) + 1) * len(lines) // (len(sightings) + 1) - 1
            out[msg_id] = (rank, 0, lines[i] + 0.5, n, kind_order) if i >= 0 else (rank, -1, None, n, kind_order)
    return out


def main() -> None:
    mo, dc = MapOrder(), Decomp()
    cfg = yaml.safe_load(FIRST_SEEN.read_text(encoding="utf-8"))
    placer = Placer(mo, FirstSeen(mo, dc), Keys(dc), cfg)

    df = pd.read_parquet(EXTRACT_OUT)
    msgs = df.drop_duplicates("msg_id")[["msg_id", "ns", "group", "label", "line_no"]]
    english = (CORPUS / "en_msg.txt").read_text(encoding="utf-8").split("\n")
    overrides = load_overrides()
    unused = dc.unused_labels
    by_hand = cfg.get("never_shown") or {}  # label → why: what no rule here can tell
    placed = {}
    never = []
    for r in msgs.itertuples(index=False):
        if r.label in unused:
            placed[r.msg_id] = ("exclude", math.nan, "unused in the game", 0, False)
            never.append((r.msg_id, "the decomp marks it unused"))
            continue
        why = by_hand.get(r.label) or next(
            (w for pat, w in by_hand.items() if "*" in pat and (
                fnmatch.fnmatchcase(r.label, pat) or fnmatch.fnmatchcase(f"{r.group}.{r.label}", pat))), None)
        if why:
            placed[r.msg_id] = ("exclude", math.nan, "never shown", 0, False)
            never.append((r.msg_id, why))
            continue
        if r.label in mo.place:
            # A line of a walk has an exact place, like dialogue, whatever kind of text it is.
            placed[r.msg_id] = ("dialogue", float(mo.rank[r.label]), mo.place[r.label], 0, False)
            continue
        if r.ns == "script":
            bucket, entry = mo.resolve(r.group, r.label)
            if bucket == "order":
                placed[r.msg_id] = ("dialogue", float(mo.rank[entry]), entry, 0, False)
                continue
            if bucket == "exclude":
                placed[r.msg_id] = ("exclude", math.nan, entry, 0, False)
                continue
            name = entry or r.group
        else:
            name = r.group
        b, rank, where, fallback = placer.place(name, r.label)
        if fallback and r.msg_id in overrides:
            b, rank, where = placer.place_one(overrides[r.msg_id], r.label)
            where = f"llm: {where}"
        if any(x in where for x in NEVER_MET):
            never.append((r.msg_id, where))
            b, rank = "exclude", math.nan
        placed[r.msg_id] = (b, rank, where, 1, fallback)
    for r in msgs.itertuples(index=False):
        if r.ns == "hand":
            continue  # no English line to compare
        if placed[r.msg_id][0] not in ("exclude", "help", "end", "unplaced") and KANA.search(english[r.line_no]):
            placed[r.msg_id] = ("exclude", math.nan, UNTRANSLATED, 0, False)
            never.append((r.msg_id, UNTRANSLATED))

    # A line a script shows only once something has happened cannot come
    # before it: the Silph employees' thanks wait for Giovanni's defeat, the
    # old man's catching lesson for the parcel to reach Professor Oak. It
    # goes after the scene that makes it possible, where that scene has a
    # place of its own, or else after the map whose script does, and keeps
    # its own map as its location.
    ways, later = dc.shown_when, {}
    said = {r.label: r.msg_id for r in msgs.itertuples(index=False) if placed[r.msg_id][0] == "dialogue"}

    def home(entry: str) -> str | None:
        """The map an entry of the route is on. None for a screen (a menu's walk)."""
        if entry in mo.place:
            return mo.place[entry] if mo.place[entry] in dc.map_json else None
        return entry if entry in dc.map_json else dc.map_of_label(entry)

    def left(rank: float, own: str | None) -> float:
        """For a line of another map, the rank by which you have left the
        scene's map: past the entries that follow it on the same map, and
        the menus opened there. The catching lesson in Viridian comes after
        all Oak says in his lab and after the Pokédex he hands over."""
        i = int(rank)
        if rank != i or i >= len(mo.order) or not (here := home(mo.order[i])) or own == here:
            return rank
        while i + 1 < len(mo.order) and home(mo.order[i + 1]) in (here, None):
            i += 1
        return float(i)

    def true_from(cond: str, own: str | None) -> float:
        # A scene with no line of its own counts from its map; one that only
        # sets a variable the map keeps to itself (which side you came in
        # from) is part of the same visit and holds nothing back.
        ranks = [left(max(at), own) if (at := [placed[said[t]][1] for t in lines if t in said])
                 else -math.inf if "==" in cond else placer.fs.map_rank.get(where, math.inf)
                 for where, _, lines in dc.setters.get(cond, ())]
        if cond not in dc.setters:      # set by the game's own code
            ranks = [placer.fs.map_rank.get(m, math.inf) for m in dc.set_in.get(cond, ())]
        return min(ranks, default=math.inf)

    for _ in range(5):                  # a line can wait for a line that was itself moved
        moved = False
        for r in msgs.itertuples(index=False):
            b, rank, where, after, fallback = placed[r.msg_id]
            # A walk's lines are in the order they were listed in by hand.
            if b != "dialogue" or r.label not in ways or r.label in mo.place:
                continue
            own = dc.map_of_label(r.label) or r.group
            # The earliest of the ways a script shows it; a way is open once all it asks for is true.
            possible = min(max((x for c in must if (x := true_from(c, own)) != math.inf), default=-math.inf)
                           for must, _ in ways[r.label])
            if possible > rank:
                # Halfway to the next entry, so that a line waiting for a moved line still follows it.
                placed[r.msg_id] = (b, possible + (math.floor(possible) + 1 - possible) / 2, where, after, fallback)
                later[r.msg_id] = (r.msg_id, r.group, r.label, later.get(r.msg_id, (0, 0, 0, mo.order[int(rank)]))[3],
                                   mo.order[int(possible)], " or ".join(", ".join(sorted(must)) for must, _ in ways[r.label]))
                moved = True
        if not moved:
            break
    later = list(later.values())
    pd.DataFrame(later, columns=["msg_id", "map", "label", "was_at", "now_after", "waits_for"]).to_csv(LATER_OUT, index=False)

    # The name of a thing a scene hands over is met in that scene. Mr. Fuji
    # gives the Poké Flute once he is back from the Tower: now that his lines
    # come after it, the flute's own line goes with them, so that its card
    # sits on his sentence and not on the bare name.
    for r in msgs.itertuples(index=False):
        b, rank, where, after, fallback = placed[r.msg_id]
        anchor = placer.anchor_for(r.group, r.label)[0] if b == "computed" else None
        if anchor not in ("item", "berry") or not (const := placer.keys.key(anchor, r.label)):
            continue
        met = []                         # for each map that has it, when it is first to be had there
        for m in dc.script_refs["item"].get(const, ()):
            scenes = dc.handed_over.get(const, {}).get(m, [[]])
            placed_at = [[placed[said[t]][1] for t in lines if t in said] for lines in scenes]
            met.append(min(max(at) for at in placed_at) if all(placed_at) else placer.fs.map_rank.get(m, math.inf))
        if met and not math.isinf(min(met)) and min(met) > rank:
            placed[r.msg_id] = (b, min(met), where, after, fallback)

    # Inside a map, a line that waits for something the same map sets comes
    # after the line shown as it is set (the gym guide's "you're champ
    # material" after Brock's badge speech), or else after the map's ordinary
    # lines. The text dump does not always list them that way. A walk's lines
    # keep their listed place; so does a line that is an entry of its own.
    within = within_map(msgs, placed, dc, mo, placer.fs.map_rank)

    # What can be read before the game stops you comes before it does
    # (blockers.py): Oak's lab is open before Oak calls you back from the
    # grass. A line listed by hand keeps its place; `check` tests those.
    before = before_blockers(msgs, placed, within, dc, mo)
    pd.DataFrame(before, columns=["blocker", "before", "label", "map", "was_at"]).to_csv(BEFORE_OUT, index=False)

    close = frozenset(t for _, _, t, _, _ in before)
    fine = fine_places(msgs, placed, placer, dc, within, close, close | {label for _, _, label, *_ in later})
    for msg_id, (rank, after, tiebreak, sighting, kind_order) in fine.items():
        b, _, where, _, fallback = placed[msg_id]
        placed[msg_id] = (b, rank, where, after, fallback)

    # Nothing floats before the first walk out of the lab. Name lines, placed
    # just above, are not floating text.
    outside = float(mo.rank[FLOATS_AFTER]) + 0.5
    for msg_id, (b, rank, where, after, fallback) in placed.items():
        if b in ("start", "anchored", "computed") and rank < outside and msg_id not in fine:
            placed[msg_id] = (b, outside, where, after, fallback)

    cols = ["bucket", "rank", "first_seen", "after_dialogue", "fallback"]
    df = df.join(pd.DataFrame.from_dict(placed, orient="index", columns=cols), on="msg_id")
    ORDER_OUT.parent.mkdir(parents=True, exist_ok=True)
    df[df["fallback"]].to_parquet(FALLBACK_OUT, index=False)
    freq = df["text"].map(df["text"].value_counts())
    # The end bucket and unplaced rows: most frequent text first.
    tail = df["bucket"].isin(list(DECK_OF))
    df["tiebreak"] = df["line_no"].where(~tail, -freq * 100000 + df["line_no"]).astype(float)
    # The Help deck reads like the Help menu: its topics, then each question
    # or term followed by its answer. The dump lists all questions first.
    in_help = df["bucket"] == "help"
    asked = dict(zip(df.loc[in_help, "label"], df.loc[in_help, "line_no"]))
    df.loc[in_help, "tiebreak"] = [help_place(lb, n, asked) for lb, n in zip(df.loc[in_help, "label"], df.loc[in_help, "line_no"])]
    moved_in_map = {r.msg_id: within[r.label] for r in msgs.itertuples(index=False)
                    if r.ns == "script" and placed[r.msg_id][0] == "dialogue" and r.label in within}
    df["tiebreak"] = df["msg_id"].map(moved_in_map).fillna(df["tiebreak"])
    df["sighting"], df["kind_order"] = 0.0, 0
    for msg_id, (_, _, tiebreak, sighting, kind_order) in fine.items():
        at = df["msg_id"] == msg_id
        if tiebreak is not None:
            df.loc[at, "tiebreak"] = tiebreak
        df.loc[at, ["sighting", "kind_order"]] = sighting, kind_order
    kept = df[df["bucket"] != "exclude"].sort_values(
        ["rank", "after_dialogue", "tiebreak", "sighting", "kind_order", "line_no", "page", "sent"]
    )
    kept = kept.drop(columns=["tiebreak", "sighting", "kind_order"]).reset_index(drop=True)
    kept["first_seen_order"] = kept.index

    # Interleave non-dialogue lines with the story, chapter by chapter. This
    # preview uses tokenizer lemmas; the cards stage re-runs it on LLM senses.
    kept["dialogue"] = kept["bucket"] == "dialogue"
    # What must not be broken up by floating text: a message, and a walk.
    kept["unit"] = [f"walk {mo.walk[lb]}" if lb in mo.walk else m for lb, m in zip(kept["label"], kept["msg_id"])]
    kept["tail"] = kept["bucket"].isin(list(DECK_OF))
    kept["deck"] = kept["bucket"].map(DECK_OF).fillna("story")
    kept["gender"] = kept["label"].map(dc.gender_only).fillna("")  # shown only to a boy, or only to a girl
    kept["chapter"] = chapter_of(kept["rank"], mo.rank)
    kept.loc[kept["deck"] == "help", "chapter"] = HELP
    kept.loc[kept["deck"] == "link", "chapter"] = LINK_PLAY
    kept["order"] = spread(kept, kept["text"].map(lemmas))
    kept["teaches"] = kept["order"].notna()
    kept = kept.sort_values("order", na_position="last")

    ORDER_OUT.parent.mkdir(parents=True, exist_ok=True)
    kept.to_parquet(ORDER_OUT, index=False)
    un = kept[kept["bucket"] == "unplaced"].drop_duplicates("msg_id")
    un[["msg_id", "group", "label", "first_seen", "text"]].to_csv(UNPLACED_OUT, index=False)

    why = dict(never)
    gone = df[df["msg_id"].isin(why)].drop_duplicates("msg_id")
    gone.assign(why=gone["msg_id"].map(why))[["msg_id", "group", "label", "why", "text"]].to_csv(NEVER_OUT, index=False)

    n_ex = (df["bucket"] == "exclude").sum()
    print(f"{len(later)} messages wait for something that happens later than their map and were moved after it → {LATER_OUT.relative_to(ROOT)}")
    print(f"{len(before)} messages can be read before a blocker and were moved in front of it "
          f"({len({b for b, *_ in before})} blockers) → {BEFORE_OUT.relative_to(ROOT)}")
    print(f"sentences kept {len(kept)}, excluded {n_ex} (of them never shown in the game: "
          f"{int(df['msg_id'].isin(why).sum())}, in {len(why)} messages → {NEVER_OUT.relative_to(ROOT)})")
    print(kept["bucket"].value_counts().to_string())
    body = kept[~kept["tail"]]
    per = body.groupby("chapter").agg(
        dialogue=("dialogue", "sum"),
        ui_teaching=("teaches", lambda s: int((s & ~body.loc[s.index, "dialogue"]).sum())),
        ui_idle=("teaches", lambda s: int((~s).sum())),
    )
    per.index = [*CHAPTER_ENDS, "postgame"][: len(per)]
    print("\nper chapter (ends at):")
    print(per.to_string())
    print("\ncomputed joins (messages):")
    for (kind, what), n in sorted(placer.stats.items()):
        print(f"  {kind:22} {what:10} {n}")
    print(f"\n→ {ORDER_OUT.relative_to(ROOT)}")
    print(f"→ {UNPLACED_OUT.relative_to(ROOT)} ({len(un)} messages)")


if __name__ == "__main__":
    main()
