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

from .decomp import STARTER_LEVEL, STARTER_MAP, STARTERS, Decomp, norm
from .lemmas import lemmas
from .map_order import MapOrder
from .spread import CHAPTER_ENDS, chapter_of, spread
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

# "start" text (start menu, options, bag, save) is first seen when the menu
# opens: after the opening sequence, as you stand in your bedroom.
MENU_OPENS = "PalletTown_PlayersHouse_2F"
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
        self.end_rank = float(len(mo.order) + 1)
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
            return "start", self.mo.rank[MENU_OPENS] - 0.5, "start"
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


NAME_LINES = {"species_names": "species", "battle_main": "type", "move_names": "move", "abilities": "ability"}
KIND_ORDER = {"species": 0, "type": 1, "move": 2, "ability": 3}


def fine_places(msgs: pd.DataFrame, placed: dict, placer: Placer, dc: Decomp) -> dict[str, tuple]:
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
    line = dict(zip(msgs["label"], msgs["line_no"]))
    said: dict[float, list[int]] = {}  # rank → line numbers of its dialogue messages
    for r in msgs.itertuples(index=False):
        if placed[r.msg_id][0] == "dialogue":
            said.setdefault(placed[r.msg_id][1], []).append(r.line_no)
    for lines in said.values():
        lines.sort()
    rank_of_label = {r.label: placed[r.msg_id][1] for r in msgs.itertuples(index=False)
                     if placed[r.msg_id][0] == "dialogue"}

    def hang(trainer: str, rank: float) -> float | None:
        """The line number to sort by: just after the challenge, or just
        before the defeat line, of a trainer whose text is at this rank."""
        texts = sorted(t for _, t in dc.script_refs["trainer_text"].get(trainer, ()) if rank_of_label.get(t) == rank)
        for suffix, shift in (("Intro", 0.5), ("Defeat", -0.5)):
            for t in texts:
                if t.endswith(suffix):
                    return line[t] + shift
        return line[texts[0]] + 0.5 if texts else None

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
            out[r.msg_id] = (rank, 0, at, n, KIND_ORDER[kind])
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
    placed = {}
    never = []
    for r in msgs.itertuples(index=False):
        if r.label in unused:
            placed[r.msg_id] = ("exclude", math.nan, "unused in the game", 0, False)
            never.append((r.msg_id, "the decomp marks it unused"))
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
        if placed[r.msg_id][0] not in ("exclude", "end", "unplaced") and KANA.search(english[r.line_no]):
            placed[r.msg_id] = ("exclude", math.nan, UNTRANSLATED, 0, False)
            never.append((r.msg_id, UNTRANSLATED))

    # A line a script shows only once a flag is set cannot come before the map
    # that sets it: the Silph employees' thanks wait for Giovanni's defeat,
    # the old man's "how is the Teachy TV?" for Brock's badge. It goes just
    # after that map and keeps its own place as its location.
    waits, set_in, later = dc.waits_for, dc.set_in, []
    for r in msgs.itertuples(index=False):
        b, rank, where, after, fallback = placed[r.msg_id]
        if b != "dialogue" or r.label not in waits:
            continue
        ranks = [min((placer.fs.map_rank.get(m, math.inf) for m in set_in.get(c, ())), default=math.inf)
                 for c in waits[r.label]]
        possible = max((x for x in ranks if not math.isinf(x)), default=-math.inf)
        if possible > rank:
            placed[r.msg_id] = (b, possible + 0.5, where, after, fallback)
            later.append((r.msg_id, r.group, r.label, mo.order[int(rank)], mo.order[int(possible)],
                          ", ".join(sorted(waits[r.label]))))
    pd.DataFrame(later, columns=["msg_id", "map", "label", "was_at", "now_after", "waits_for"]).to_csv(LATER_OUT, index=False)

    fine = fine_places(msgs, placed, placer, dc)
    for msg_id, (rank, after, tiebreak, sighting, kind_order) in fine.items():
        b, _, where, _, fallback = placed[msg_id]
        placed[msg_id] = (b, rank, where, after, fallback)

    cols = ["bucket", "rank", "first_seen", "after_dialogue", "fallback"]
    df = df.join(pd.DataFrame.from_dict(placed, orient="index", columns=cols), on="msg_id")
    ORDER_OUT.parent.mkdir(parents=True, exist_ok=True)
    df[df["fallback"]].to_parquet(FALLBACK_OUT, index=False)
    freq = df["text"].map(df["text"].value_counts())
    # The end bucket and unplaced rows: most frequent text first.
    tail = df["bucket"].isin(["end", "unplaced"])
    df["tiebreak"] = df["line_no"].where(~tail, -freq * 100000 + df["line_no"]).astype(float)
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
    kept["tail"] = kept["bucket"].isin(["end", "unplaced"])
    kept["chapter"] = chapter_of(kept["rank"], mo.rank)
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
    print(f"{len(later)} messages wait for a flag set later than their map and were moved after it → {LATER_OUT.relative_to(ROOT)}")
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
