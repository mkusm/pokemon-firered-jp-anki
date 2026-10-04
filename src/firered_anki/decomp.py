"""Read the pret pokefirered decomp: where each species, move, item, trainer and
map section is first met, as a decomp map name. Ranking those maps in story order
is the order stage's job; this module only knows the game data.
"""

import json
import re
from collections import defaultdict
from functools import cached_property

from .paths import ROOT

DECOMP = ROOT / "vendor" / "pokefirered"

# The lab's starter choice is done in C, not in the map script.
STARTERS = ["SPECIES_BULBASAUR", "SPECIES_CHARMANDER", "SPECIES_SQUIRTLE"]
STARTER_MAP = "PalletTown_ProfessorOaksLab"
STARTER_LEVEL = 5

TRAINER_RE = re.compile(r"^\s*trainerbattle_\w+\s+(TRAINER_\w+)")
ITEM_RES = [
    re.compile(r"^\s*(?:finditem|giveitem|additem)\s+(ITEM_\w+)"),
    re.compile(r"^\s*(?:giveitem_msg|msgreceiveditem)\s+\w+,\s*(ITEM_\w+)"),
    re.compile(r"^\s*\.2byte\s+(ITEM_\w+)"),  # Poké Mart stock lists
]
MON_RE = re.compile(r"^\s*(?:givemon|setwildbattle)\s+(SPECIES_\w+)")
LABEL_RE = re.compile(r"^(\w+)::?")


def norm(name: str) -> str:
    """CamelCase or CONST_CASE → comparable key: 'KarateChop' == 'KARATE_CHOP'."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def read(rel: str) -> str:
    return (DECOMP / rel).read_text(encoding="utf-8")


def defines(rel: str, prefix: str) -> dict[str, int]:
    out = {}
    for name, val in re.findall(rf"^#define ({prefix}\w+)\s+(\d+)\b", read(rel), re.M):
        out[name] = int(val)
    return out


def enum_values(rel: str, prefix: str) -> dict[str, int]:
    names = re.findall(rf"^\s*({prefix}\w+)\s*,", read(rel), re.M)
    return {n: i for i, n in enumerate(names)}


class Decomp:
    # --- constants ---------------------------------------------------------
    @cached_property
    def species(self) -> dict[str, int]:
        return defines("include/constants/species.h", "SPECIES_")

    @cached_property
    def moves(self) -> dict[str, int]:
        return defines("include/constants/moves.h", "MOVE_")

    @cached_property
    def items(self) -> dict[str, int]:
        return defines("include/constants/items.h", "ITEM_")

    @cached_property
    def abilities(self) -> dict[str, int]:
        return defines("include/constants/abilities.h", "ABILITY_")

    @cached_property
    def trainers(self) -> dict[str, int]:
        return defines("include/constants/opponents.h", "TRAINER_")

    @cached_property
    def trainer_classes(self) -> dict[str, int]:
        return defines("include/constants/trainers.h", "TRAINER_CLASS_")

    @cached_property
    def national_dex(self) -> dict[str, int]:
        return enum_values("include/constants/pokedex.h", "NATIONAL_DEX_")

    # --- maps --------------------------------------------------------------
    @cached_property
    def map_json(self) -> dict[str, dict]:
        out = {}
        for f in sorted((DECOMP / "data" / "maps").glob("*/map.json")):
            m = json.loads(f.read_text(encoding="utf-8"))
            out[m["name"]] = m
        return out

    @cached_property
    def mapsec(self) -> dict[str, str]:
        return {name: m["region_map_section"] for name, m in self.map_json.items()}

    @cached_property
    def mapsec_sections(self) -> list[dict]:
        """id, and for FRLG's 109 named sections their English name."""
        return json.loads(read("src/data/region_map/region_map_sections.json"))["map_sections"]

    @cached_property
    def map_by_id(self) -> dict[str, str]:
        return {m["id"]: name for name, m in self.map_json.items()}

    def map_of_label(self, label: str) -> str | None:
        """Longest decomp map name the label starts with."""
        best = None
        for name in self.map_json:
            if label == name or label.startswith(name + "_"):
                if best is None or len(name) > len(best):
                    best = name
        return best

    # --- text the game has but never shows -----------------------------------
    @cached_property
    def unused_labels(self) -> set[str]:
        """Text labels the decomp marks `@ Unused` on the line above them:
        leftovers from Ruby and Sapphire and lines no script calls, such as
        the Trainer School e-mail from the R/S rival's computer."""
        files = [DECOMP / "data" / "event_scripts.s", *(DECOMP / "data").glob("text/*.inc"),
                 *(DECOMP / "data").glob("scripts/*.inc"), *(DECOMP / "data").glob("maps/*/*.inc")]
        out = set()
        for f in files:
            lines = f.read_text(encoding="utf-8", errors="replace").split("\n")
            for mark, nxt in zip(lines, lines[1:]):
                m = LABEL_RE.match(nxt)
                # The mark itself, capitalised: a note that merely contains the
                # word (Brock's defeat text, "the otherwise unused array") is not one.
                if m and re.match(r"\s*@ Unused\b", mark):
                    out.add(m.group(1))
        return out

    # --- scripts: trainers, items, gift/static Pokémon per map --------------
    @cached_property
    def script_refs(self) -> dict[str, dict[str, set[str]]]:
        """kind → constant → set of maps. kind is trainer | item | species;
        trainer_text → constant → set of (map, text label)."""
        refs: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
        files = list((DECOMP / "data" / "maps").glob("*/scripts.inc"))
        files += list((DECOMP / "data" / "scripts").glob("*.inc"))
        for f in files:
            file_map = f.parent.name if f.parent.parent.name == "maps" else None
            label = None
            for line in f.read_text(encoding="utf-8").splitlines():
                if m := LABEL_RE.match(line):
                    label = m.group(1)
                    continue
                where = file_map or (label and self.map_of_label(label))
                if not where:
                    continue
                if m := TRAINER_RE.match(line):
                    refs["trainer"][m.group(1)].add(where)
                    # The battle's text labels say which visit it belongs to
                    # (Route22_Text_LateRivalDefeat).
                    for text in re.findall(r"\b\w+_Text_\w+", line):
                        refs["trainer_text"][m.group(1)].add((where, text))
                if m := MON_RE.match(line):
                    refs["species"][m.group(1)].add(where)
                for rx in ITEM_RES:
                    if (m := rx.match(line)) and m.group(1) != "ITEM_NONE":
                        refs["item"][m.group(1)].add(where)
        for name, m in self.map_json.items():
            for bg in m.get("bg_events", []):
                if bg.get("type") == "hidden_item":
                    refs["item"][bg["item"]].add(name)
        return refs

    # --- Pokémon data --------------------------------------------------------
    @cached_property
    def learnsets(self) -> dict[str, list[tuple[int, str]]]:
        text = read("src/data/pokemon/level_up_learnsets.h")
        tables = {
            name: [(int(l), mv) for l, mv in re.findall(r"LEVEL_UP_MOVE\(\s*(\d+),\s*(MOVE_\w+)\)", body)]
            for name, body in re.findall(r"(s\w+LevelUpLearnset)\[\] = \{(.*?)\};", text, re.S)
        }
        ptrs = read("src/data/pokemon/level_up_learnset_pointers.h")
        return {sp: tables[t] for sp, t in re.findall(r"\[(SPECIES_\w+)\]\s*=\s*(s\w+)", ptrs)}

    def moves_at(self, species: str, level: int) -> list[str]:
        """The four moves a wild or default-moveset mon knows at this level."""
        learned = [mv for lvl, mv in self.learnsets.get(species, []) if lvl <= level]
        return list(dict.fromkeys(reversed(learned)))[:4]

    @cached_property
    def evolutions(self) -> list[tuple[str, str]]:
        """(from, to) for every evolution."""
        text = read("src/data/pokemon/evolution.h")
        out = []
        for sp, body in re.findall(r"\[(SPECIES_\w+)\]\s*=\s*\{(.*?)\}\},?\n", text, re.S):
            out += [(sp, to) for to in re.findall(r"(SPECIES_\w+)\s*\}", body + "}")]
        return out

    @cached_property
    def species_abilities(self) -> dict[str, list[str]]:
        text = read("src/data/pokemon/species_info.h")
        out = {}
        parts = re.split(r"\n    \[(SPECIES_\w+)\]\s*=", text)
        for sp, body in zip(parts[1::2], parts[2::2]):
            if m := re.search(r"\.abilities = \{(\w+),\s*(\w+)\}", body):
                out[sp] = [a for a in m.groups() if a != "ABILITY_NONE"]
        return out

    @cached_property
    def species_types(self) -> dict[str, list[str]]:
        text = read("src/data/pokemon/species_info.h")
        out = {}
        parts = re.split(r"\n    \[(SPECIES_\w+)\]\s*=", text)
        for sp, body in zip(parts[1::2], parts[2::2]):
            if m := re.search(r"\.types = \{(\w+),\s*(\w+)\}", body):
                out[sp] = list(dict.fromkeys(m.groups()))
        return out

    @cached_property
    def trainer_info(self) -> dict[str, dict]:
        """TRAINER_X → class, party [(species, level, moves or None)]."""
        parties = {}
        text = read("src/data/trainer_parties.h")
        parts = re.split(r"\bstatic const struct \w+ (sParty_\w+)\[\] =", text)
        for name, body in zip(parts[1::2], parts[2::2]):
            mons = []
            body = body.split("};", 1)[0]
            # Each mon is an innermost {...}; .moves = {...} nests one deeper,
            # so take the mon blocks by their .iv field.
            for mon in re.split(r"\{\s*\.iv\b", body)[1:]:
                sp = re.search(r"\.species = (SPECIES_\w+)", mon)
                lvl = re.search(r"\.lvl = (\d+)", mon)
                mv = re.search(r"\.moves = \{([^}]*)", mon)
                if sp and lvl:
                    moves = re.findall(r"MOVE_\w+", mv.group(1)) if mv else None
                    mons.append((sp.group(1), int(lvl.group(1)), moves))
            parties[name] = mons
        out = {}
        text = read("src/data/trainers.h")
        parts = re.split(r"\n    \[(TRAINER_\w+)\]\s*=", text)
        for tr, body in zip(parts[1::2], parts[2::2]):
            cls = re.search(r"\.trainerClass = (TRAINER_CLASS_\w+)", body)
            party = re.search(r"\.party = \w+\((sParty_\w+)\)", body)
            out[tr] = {
                "class": cls.group(1) if cls else None,
                "party": parties.get(party.group(1), []) if party else [],
            }
        return out

    @cached_property
    def wild(self) -> list[tuple[str, str, int, str | None]]:
        """(map, species, max level, item needed) for every FireRed wild slot.
        Surfing needs HM03, Rock Smash HM06, and fishing a rod: slots 0–1 are
        the Old Rod's, 2–4 the Good Rod's, 5–9 the Super Rod's."""
        data = json.loads(read("src/data/wild_encounters.json"))
        out = []
        for group in data["wild_encounter_groups"]:
            for enc in group["encounters"]:
                if enc.get("base_label", "").endswith("_LeafGreen"):
                    continue
                name = self.map_by_id.get(enc["map"])
                if not name:
                    continue
                for field, val in enc.items():
                    if field.endswith("_mons"):
                        for slot, mon in enumerate(val["mons"]):
                            need = {
                                "water_mons": "ITEM_HM03", "rock_smash_mons": "ITEM_HM06",
                                "fishing_mons": "ITEM_OLD_ROD" if slot < 2 else
                                "ITEM_GOOD_ROD" if slot < 5 else "ITEM_SUPER_ROD",
                            }.get(field)
                            out.append((name, mon["species"], mon["max_level"], need))
        return out

    # --- battle messages --------------------------------------------------------
    @cached_property
    def effect_moves(self) -> dict[str, list[str]]:
        """EFFECT_X → the moves with that battle effect."""
        out: dict[str, list[str]] = defaultdict(list)
        for mv, eff in re.findall(r"\[(MOVE_\w+)\]\s*=\s*\{\s*\.effect = (EFFECT_\w+)",
                                  read("src/data/battle_moves.h")):
            out[eff].append(mv)
        return out

    @cached_property
    def battle_string_effects(self) -> dict[str, set[str]]:
        """Battle message label (sText_PkmnFellAsleep) → the move effects whose
        battle script can print it.

        Each effect's script is followed through gotos, calls, fall-through into
        the next label, message tables (printfromtable) and secondary effects
        (setmoveeffect, which the engine resolves through sMoveEffectBS_Ptrs).
        Messages only C code triggers (end-of-turn damage, abilities, trainer
        text) are not reached and keep their other placement. So do the few the
        engine also prints for every move ("attack missed"), which one script
        happens to print too.
        """
        msg = read("src/battle_message.c")
        label_of = dict(re.findall(r"\[(STRINGID_\w+) - BATTLESTRINGS_TABLE_START\]\s*=\s*(\w+)", msg))
        tables = {
            name: re.findall(r"STRINGID_\w+", body)
            for name, body in re.findall(r"const u16 (g\w+)\[\]\s*=\s*\{(.*?)\};", msg, re.S)
        }
        engine = read("src/battle_script_commands.c")
        effect_bs = dict(re.findall(r"\[(MOVE_EFFECT_\w+)\]\s*=\s*(BattleScript_\w+)", engine))
        # The table pads effects that have no script with the sleep script.
        effect_bs = {k: v for k, v in effect_bs.items()
                     if v != "BattleScript_MoveEffectSleep" or k == "MOVE_EFFECT_SLEEP"}
        generic = set(re.findall(r"STRINGID_\w+", engine))
        for t in re.findall(r"\bg\w+StringIds\b", engine):
            generic.update(tables.get(t, []))

        text = read("data/battle_scripts_1.s")
        strings: dict[str, set[str]] = defaultdict(set)
        refs: dict[str, set[str]] = defaultdict(set)
        order: list[str] = []
        ends = {"goto", "end", "end2", "end3", "return", "finishaction", "finishturn"}
        label, last = None, None
        for line in text.splitlines():
            line = line.split("@")[0].strip()
            if m := re.match(r"(\w+)::?$", line):
                if label and last not in ends:
                    refs[label].add(m.group(1))  # falls through into the next block
                label, last = m.group(1), None
                order.append(label)
                continue
            if not label or not line or line.startswith("."):
                continue
            last = line.split()[0]
            strings[label].update(re.findall(r"STRINGID_\w+", line))
            for t in re.findall(r"\bg\w+", line):
                strings[label].update(tables.get(t, []))
            refs[label].update(re.findall(r"BattleScript_\w+", line))
            for me in re.findall(r"MOVE_EFFECT_\w+", line):
                if me in effect_bs:
                    refs[label].add(effect_bs[me])

        table = re.search(r"gBattleScriptsForMoveEffects::(.*?)\n\n", text, re.S).group(1)
        out: dict[str, set[str]] = defaultdict(set)
        for script, eff in re.findall(r"\.4byte (BattleScript_\w+)\s*@ (EFFECT_\w+)", table):
            seen, todo = set(), [script]
            while todo:
                cur = todo.pop()
                if cur in seen:
                    continue
                seen.add(cur)
                todo.extend(refs.get(cur, ()))
                for sid in strings.get(cur, ()):
                    if sid in label_of and sid not in generic:
                        out[label_of[sid]].add(eff)
        return out

    @cached_property
    def tmhm_moves(self) -> dict[str, str]:
        """ITEM_TM01_FOCUS_PUNCH-style constant → the move it teaches."""
        text = read("src/data/party_menu.h")
        body = re.search(r"sTMHMMoves\[\] =\s*\{(.*?)\};", text, re.S).group(1)
        moves = re.findall(r"MOVE_\w+", body)
        out = {}
        for item in self.items:
            if m := re.match(r"ITEM_(TM|HM)(\d+)", item):
                idx = int(m.group(2)) - 1 + (50 if m.group(1) == "HM" else 0)
                if idx < len(moves):
                    out[item] = moves[idx]
        return out
