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

# "start" text (start menu, options, bag, save) is first seen when the menu
# opens: after the opening sequence, as you stand in your bedroom.
MENU_OPENS = "PalletTown_PlayersHouse_2F"
COMPUTED = {
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
        self._build()

    @staticmethod
    def _see(table: dict, key: str, rank: float, where: str) -> None:
        if rank < table.get(key, (math.inf, ""))[0]:
            table[key] = (rank, where)

    def _rank(self, m: str) -> float:
        return self.map_rank.get(m, math.inf)

    def _build(self) -> None:
        dc, refs = self.dc, self.dc.script_refs
        for kind, table in (("item", self.item), ("species", self.species)):
            for const, maps in refs[kind].items():
                for m in maps:
                    self._see(table, const, self._rank(m), m)
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

        def see_mon(sp: str, lvl: int, moves, rank: float, where: str) -> None:
            self._see(self.species, sp, rank, where)
            for mv in moves or dc.moves_at(sp, lvl):
                if mv != "MOVE_NONE":
                    self._see(self.move, mv, rank, where)

        for sp in STARTERS:
            see_mon(sp, STARTER_LEVEL, None, self._rank(STARTER_MAP), STARTER_MAP)
        for m, sp, lvl, need in dc.wild:
            # A surfing or fishing encounter is first seen once you have both
            # the map and the HM or rod.
            rank = max(self._rank(m), self.item.get(need, (math.inf, ""))[0]) if need else self._rank(m)
            see_mon(sp, lvl, None, rank, m)
        for tr, (rank, where) in self.trainer.items():
            for sp, lvl, moves in dc.trainer_info.get(tr, {}).get("party", []):
                see_mon(sp, lvl, moves, rank, where)
        for it, mv in dc.tmhm_moves.items():
            if it in self.item:
                self._see(self.move, mv, *self.item[it])

        # A species only reachable by evolving (Vaporeon, Kabutops) is first seen
        # with the species it evolves from. Repeat for two-stage chains.
        for _ in range(2):
            for pre, post in dc.evolutions:
                if post not in self.species and pre in self.species:
                    rank, where = self.species[pre]
                    self._see(self.species, post, rank, f"{where} (evolves from {pre})")

        self.ability: dict[str, tuple[float, str]] = {}
        for sp, (rank, where) in self.species.items():
            for ab in dc.species_abilities.get(sp, []):
                self._see(self.ability, ab, rank, where)
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


def main() -> None:
    mo, dc = MapOrder(), Decomp()
    cfg = yaml.safe_load(FIRST_SEEN.read_text(encoding="utf-8"))
    placer = Placer(mo, FirstSeen(mo, dc), Keys(dc), cfg)

    df = pd.read_parquet(EXTRACT_OUT)
    msgs = df.drop_duplicates("msg_id")[["msg_id", "ns", "group", "label"]]
    overrides = load_overrides()
    placed = {}
    for r in msgs.itertuples(index=False):
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
        placed[r.msg_id] = (b, rank, where, 1, fallback)

    cols = ["bucket", "rank", "first_seen", "after_dialogue", "fallback"]
    df = df.join(pd.DataFrame.from_dict(placed, orient="index", columns=cols), on="msg_id")
    ORDER_OUT.parent.mkdir(parents=True, exist_ok=True)
    df[df["fallback"]].to_parquet(FALLBACK_OUT, index=False)
    freq = df["text"].map(df["text"].value_counts())
    # The end bucket and unplaced rows: most frequent text first.
    tail = df["bucket"].isin(["end", "unplaced"])
    df["tiebreak"] = df["line_no"].where(~tail, -freq * 100000 + df["line_no"])
    kept = df[df["bucket"] != "exclude"].sort_values(
        ["rank", "after_dialogue", "tiebreak", "page", "sent"]
    )
    kept = kept.drop(columns=["tiebreak"]).reset_index(drop=True)
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

    n_ex = (df["bucket"] == "exclude").sum()
    print(f"sentences kept {len(kept)}, excluded {n_ex}")
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
