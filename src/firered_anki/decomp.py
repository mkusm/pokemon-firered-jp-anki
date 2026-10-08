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
# FLAG_SYS_GAME_CLEAR and the scenes hall_of_fame.inc sets up count from here.
HALL_OF_FAME = "PokemonLeague_HallOfFame"

TRAINER_RE = re.compile(r"^\s*trainerbattle_\w+\s+(TRAINER_\w+)")
ITEM_RES = [
    re.compile(r"^\s*(?:finditem|giveitem|additem)\s+(ITEM_\w+)"),
    re.compile(r"^\s*(?:giveitem_msg|msgreceiveditem)\s+\w+,\s*(ITEM_\w+)"),
    re.compile(r"^\s*\.2byte\s+(ITEM_\w+)"),  # Poké Mart stock lists
]
MON_RE = re.compile(r"^\s*(?:givemon|setwildbattle)\s+(SPECIES_\w+)")
LABEL_RE = re.compile(r"^(\w+)::?")
# A variable a map's scripts keep to themselves: a temporary one, or the name the file gives it.
OWN_VAR = r"VAR_TEMP_\w+|(?!VAR_|FLAG_|LOCALID_)[A-Z][A-Z0-9_]*"
UNSET = ("0", "FALSE")  # what such a variable is when the map loads


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
    def layouts(self) -> dict[str, dict]:
        """Layout id → its size and where its block data is."""
        return {lay["id"]: lay for lay in json.loads(read("data/layouts/layouts.json"))["layouts"] if "id" in lay}

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

    # --- what a line waits for ----------------------------------------------
    @cached_property
    def _script_blocks(self) -> dict[str, tuple[str | None, list[str]]]:
        """Script label → (the map its file belongs to, its commands)."""
        blocks: dict[str, tuple[str | None, list[str]]] = {}
        files = sorted((DECOMP / "data" / "maps").glob("*/scripts.inc"))
        files += sorted((DECOMP / "data" / "scripts").glob("*.inc"))
        for f in files:
            where = f.parent.name if f.parent.parent.name == "maps" else HALL_OF_FAME if f.name == "hall_of_fame.inc" else None
            cur = None
            for line in f.read_text(encoding="utf-8", errors="ignore").splitlines():
                if m := re.match(r"^(\w+)::?\s*(?:@.*)?$", line):
                    cur = m.group(1)
                    blocks[cur] = (where, [])
                elif cur and line.strip() and not line.strip().startswith("@"):
                    # Without a comment after the command: a jump is read by its last word.
                    blocks[cur][1].append(line.strip() if ".string" in line else line.split("@")[0].strip())
        return blocks

    @cached_property
    def hidden_at_start(self) -> set[str]:
        """The flags a new game starts with set: the people they hide (Oak in
        his lab, the rival on Route 22) are not there until a script clears
        the flag."""
        body = read("data/event_scripts.s").split("EventScript_ResetAllMapFlags::", 1)[1].split("\tend\n", 1)[0]
        return set(re.findall(r"setflag (FLAG_\w+)", body))

    @cached_property
    def _scenes(self) -> tuple[dict[str, set[str]], dict[str, list[str]]]:
        """(block → the blocks that jump to it, block → the text labels it shows)."""
        blocks = self._script_blocks
        into: dict[str, set[str]] = defaultdict(set)
        for label, (_, cmds) in blocks.items():
            for c in cmds:
                if m := re.match(r"(?:goto|call)(?:_if_\w+)? .*?(\w+)$", c):
                    into[m.group(1)].add(label)
        shows = {label: [t for c in cmds for t in re.findall(r"\b(\w+_Text_\w+|g?Text_\w+)\b", c)]
                 for label, (_, cmds) in blocks.items()}
        return into, shows

    def scene_lines(self, label: str) -> list[str]:
        """The text labels a script block shows, or, when it shows none (the
        last step of a scene often only sets things), those of the blocks
        that lead into it."""
        into, shows = self._scenes
        seen, level = {label}, [label]
        for _ in range(4):
            if found := [t for b in level for t in shows[b]]:
                return found
            level = [src for b in level for src in sorted(into[b]) if src not in seen and src in shows]
            seen.update(level)
        return []

    @cached_property
    def handed_over(self) -> dict[str, dict[str, list[list[str]]]]:
        """An item → map → for each script block of the map that gives it,
        the text labels of the scene (none for a Mart's stock list or an item
        lying on the ground)."""
        out: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
        for label, (where, cmds) in self._script_blocks.items():
            for item in {m.group(1) for c in cmds for rx in ITEM_RES if (m := rx.match(c))}:
                if where and item != "ITEM_NONE":
                    out[item][where].append(self.scene_lines(label))
        return out

    @cached_property
    def setters(self) -> dict[str, list[tuple[str | None, str, list[str]]]]:
        """A condition → the script blocks that make it true: (the map, the
        block, the text labels the scene shows up to there). A condition is
        a flag, "VAR_MAP_SCENE_X>=n", "FLAG_HIDE_X cleared" for a person who
        is hidden when the game starts, or "Map: VAR==value" for a variable
        a map keeps to itself (Daisy's "have I given you the map yet")."""
        blocks, lines = self._script_blocks, self.scene_lines
        # A scene variable only goes up. It first reaches n where a script
        # sets it to the lowest value any script gives it that is n or more:
        # setting it to 9 says nothing about when it passed 6.
        given: dict[str, set[int]] = defaultdict(set)
        for _, cmds in blocks.values():
            for c in cmds:
                if m := re.match(r"setvar (VAR_MAP_SCENE_\w+), (\d+)", c):
                    given[m.group(1)].add(int(m.group(2)))
        out: dict[str, list] = defaultdict(list)
        for label, (where, cmds) in blocks.items():
            made = set()
            for c in cmds:
                if m := re.match(r"setflag (FLAG_\w+)", c):
                    made.add(m.group(1))
                if m := re.match(r"setvar (VAR_MAP_SCENE_\w+), (\d+)", c):
                    var, to = m.group(1), int(m.group(2))
                    below = max((v for v in given[var] if v < to), default=0)
                    made.update(f"{var}>={n}" for n in range(below + 1, to + 1))
                if (m := re.match(r"clearflag (FLAG_\w+)", c)) and m.group(1) in self.hidden_at_start:
                    made.add(f"{m.group(1)} cleared")
                if where and (m := re.match(rf"setvar ({OWN_VAR}), (\w+)$", c)) and m.group(2) not in UNSET:
                    made.add(f"{where}: {m.group(1)}=={m.group(2)}")
            for cond in made:
                out[cond].append((where, label, lines(label)))
        return out

    @cached_property
    def set_in(self) -> dict[str, set[str]]:
        """A condition → the maps whose scripts make it true."""
        out: dict[str, set[str]] = defaultdict(set)
        for cond, made in self.setters.items():
            out[cond].update(where for where, _, _ in made if where)
        out["FLAG_SYS_GAME_CLEAR"].add(HALL_OF_FAME)  # set by the game's code, after the credits
        return out

    @cached_property
    def gender_only(self) -> dict[str, str]:
        """Text label → "boy" or "girl": a line the scripts show only to a
        player of that gender (Mom's "all boys leave home some day"). Read
        from the branches after `checkplayergender`: the block a MALE or
        FEMALE branch goes to, and what follows a lone MALE branch in the
        same block, which is the girl's."""
        blocks = self._script_blocks
        direct = lambda cmds: [t for c in cmds if re.match(r"msgbox|message", c)
                               for t in re.findall(r"\b(\w+_Text_\w+|g?Text_\w+)\b", c)]
        seen: dict[str, set[str]] = defaultdict(set)
        for _, cmds in blocks.values():
            for i, c in enumerate(cmds):
                if c != "checkplayergender":
                    continue
                sides = {}
                for j in range(i + 1, min(i + 3, len(cmds))):
                    if m := re.match(r"(goto|call)_if_eq VAR_RESULT, (MALE|FEMALE), (\w+)", cmds[j]):
                        sides[m.group(2)] = (m.group(1), m.group(3), j)
                for side, (_, target, _) in sides.items():
                    for t in direct(blocks.get(target, (None, []))[1]):
                        seen[t].add("boy" if side == "MALE" else "girl")
                if set(sides) == {"MALE"} and sides["MALE"][0] == "goto":
                    for t in direct(cmds[sides["MALE"][2] + 1:]):
                        seen[t].add("girl")
        return {t: next(iter(s)) for t, s in seen.items() if len(s) == 1}

    @cached_property
    def set_with(self) -> dict[str, set[str]]:
        """A condition → the text labels shown by the scripts that make it
        true: the line you read as the thing happens (the clerk handing over
        the parcel, as FLAG_GOT_... is set)."""
        out: dict[str, set[str]] = defaultdict(set)
        for cond, made in self.setters.items():
            for _, _, shown in made:
                out[cond].update(shown)
        return out

    @cached_property
    def starts(self) -> dict[str, list[tuple[frozenset, frozenset]]]:
        """A script → the ways a map starts it, each as (what must hold, what
        must not hold yet). A tile that runs it while a scene variable is n
        needs the variable to have reached n and not passed it; a person
        hidden when the game starts has to have been brought in; a person a
        flag takes away for good is gone once it is set."""
        cleared = {m.group(1) for _, cmds in self._script_blocks.values() for c in cmds
                   if (m := re.match(r"clearflag (FLAG_\w+)", c))}
        out: dict[str, list] = defaultdict(list)
        for m in self.map_json.values():
            for kind in ("object_events", "bg_events", "coord_events"):
                for e in m.get(kind) or []:
                    script = e.get("script")
                    if not script or script == "0x0":
                        continue
                    must, not_yet = set(), set()
                    var, n = e.get("var", ""), str(e.get("var_value", ""))
                    if kind == "coord_events" and var.startswith("VAR_MAP_SCENE_") and n.isdigit():
                        if int(n):
                            must.add(f"{var}>={n}")
                        not_yet.add(f"{var}>={int(n) + 1}")
                    flag = e.get("flag", "0") if kind == "object_events" else "0"
                    if flag in self.hidden_at_start:
                        must.add(f"{flag} cleared")
                    elif flag.startswith("FLAG_HIDE_") and flag not in cleared:
                        not_yet.add(flag)
                    out[script].append((frozenset(must), frozenset(not_yet)))
        return out

    @cached_property
    def shown_when(self) -> dict[str, list[tuple[frozenset, frozenset]]]:
        """Text label → the ways a script comes to show it, each as (what
        must hold, what must not hold yet). Read from the scripts' branches
        (goto_if_set, call_if_unset, goto_if_eq on a scene variable or on one
        of the map's own, a map's scene table) and from how the maps start
        each script (`starts`), followed through gotos and calls."""
        blocks = self._script_blocks
        free = (frozenset(), frozenset())
        comes_back = {m.group(1) for _, cmds in blocks.values() for c in cmds
                      if (m := re.match(r"clearflag (FLAG_\w+)", c))}
        edges: dict[str, list] = defaultdict(list)   # target → [(source, must, not yet)]
        texts: dict[str, list] = defaultdict(list)   # block → [(text label, must, not yet)]
        for script, ways in self.starts.items():
            edges[script] += [(None, must, not_yet) for must, not_yet in ways]
        for label, (where, cmds) in blocks.items():
            if label in self.unused_labels:
                continue                             # never run: it leads nowhere
            here: set[str] = set()                   # true from this line of the block on
            gone: set[str] = set()                   # not true yet, from this line on
            for c in cmds:
                if m := re.match(r"(goto|call)_if_(set|unset) (FLAG_\w+), (\w+)", c):
                    kind, state, flag, target = m.groups()
                    lasting = {flag} - comes_back if not flag.startswith("FLAG_TEMP_") else set()
                    if state == "set":
                        edges[target].append((label, frozenset(here | {flag}), frozenset(gone)))
                        if kind == "goto":
                            gone |= lasting          # carried on: the flag is not set
                    else:
                        edges[target].append((label, frozenset(here), frozenset(gone | lasting)))
                        if kind == "goto":
                            here.add(flag)           # carried on: the flag is set
                elif m := re.match(r"(goto|call)_if_(eq|ge|lt) (VAR_MAP_SCENE_\w+), (\d+), (\w+)", c):
                    kind, test, var, n, target = m.groups()
                    n = int(n)
                    reached, passed = ({f"{var}>={n}"} if n else set()), {f"{var}>={n + 1}"}
                    if test == "eq":
                        edges[target].append((label, frozenset(here | reached), frozenset(gone | passed)))
                        if kind == "goto" and n == 0:
                            here |= passed           # carried on: no longer 0
                    elif test == "ge":
                        edges[target].append((label, frozenset(here | reached), frozenset(gone)))
                        if kind == "goto":
                            gone |= reached          # carried on: still below n
                    else:
                        edges[target].append((label, frozenset(here), frozenset(gone | reached)))
                        if kind == "goto":
                            here |= reached          # carried on: n or more
                elif where and (m := re.match(rf"(?:goto|call)_if_eq ({OWN_VAR}), (\w+), (\w+)", c)) and m.group(2) not in UNSET:
                    edges[m.group(3)].append((label, frozenset(here | {f"{where}: {m.group(1)}=={m.group(2)}"}), frozenset(gone)))
                elif m := re.match(r"map_script_2 (VAR_MAP_SCENE_\w+), (\d+), (\w+)", c):
                    var, n, target = m.groups()
                    edges[target].append((None, frozenset({f"{var}>={n}"} if int(n) else set()),
                                          frozenset({f"{var}>={int(n) + 1}"})))
                elif m := re.match(r"(?:goto|call)(?:_if_\w+)? .*?(\w+)$", c):
                    edges[m.group(1)].append((label, frozenset(here), frozenset(gone)))
                # Any command that names a text shows it: msgbox, trainerbattle, giveitem_msg…
                for t in re.findall(r"\b(\w+_Text_\w+|g?Text_\w+)\b", c):
                    texts[label].append((t, frozenset(here), frozenset(gone)))

        def fewest(ways: set) -> frozenset:
            """Without the ways that ask for all another asks and more; and
            as one way, what they share, when they are many."""
            ways = {w for w in ways if not any(o != w and o[0] <= w[0] and o[1] <= w[1] for o in ways)}
            if len(ways) > 12:
                ways = {(frozenset.intersection(*(w[0] for w in ways)), frozenset.intersection(*(w[1] for w in ways)))}
            return frozenset(ways)

        # The ways into each block. Empty: not worked out yet.
        into: dict[str, frozenset] = {b: (frozenset() if b in edges else frozenset({free})) for b in blocks}
        for _ in range(40):
            changed = False
            for b in blocks:
                new = fewest({(base[0] | must, base[1] | not_yet) for src, must, not_yet in edges.get(b, ())
                              for base in (into.get(src, {free}) if src else {free})})
                if new and new != into[b]:
                    into[b], changed = new, True
            if not changed:
                break
        ways: dict[str, set] = defaultdict(set)
        for b, shown in texts.items():
            for t, must, not_yet in shown:
                ways[t] |= {(base[0] | must, base[1] | not_yet) for base in into[b] or {free}}
        return {t: sorted(fewest(w), key=lambda x: (sorted(x[0]), sorted(x[1]))) for t, w in ways.items()}

    @cached_property
    def _conditions(self) -> tuple[dict[str, frozenset], dict[str, frozenset]]:
        """(waits_for, gone_after): what every way of showing a line shares."""
        waits, gone_after = {}, {}
        for t, ways in self.shown_when.items():
            if w := frozenset.intersection(*(x[0] for x in ways)):
                waits[t] = w
            if g := frozenset.intersection(*(x[1] for x in ways)):
                gone_after[t] = g
        return waits, gone_after

    @property
    def waits_for(self) -> dict[str, frozenset]:
        """Text label → the conditions that hold every time a script shows it:
        Mom's "you and your Pokémon are looking great" waits for
        FLAG_BEAT_RIVAL_IN_OAKS_LAB, the old man's catching lesson for the
        parcel to have been delivered."""
        return self._conditions[0]

    @property
    def gone_after(self) -> dict[str, frozenset]:
        """Text label → the conditions that do not hold yet any time a script
        shows it: once one is true the line can no longer be read (the gym
        guide's advice on Brock, gone when Brock is beaten)."""
        return self._conditions[1]

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
