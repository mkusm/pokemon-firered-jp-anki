"""Find lines the deck shows while the person or sign that says them cannot be reached.

The order of the deck is the walkthrough's, with rules laid over it, and none
of them asks whether you can get to the speaker at that point. This does: for
each line of a person or a sign, at the moment the deck shows it, it walks the
game from the bedroom with only the walls that are certain at that moment,
and reports the lines whose speaker it does not reach although the ground
allows it. That is how the Rocket behind Cerulean's robbed house was found
before the house, the Old Amber's room before Cut, and a camper who needs
Surf on the first walk down Route 4.

The walk goes through doors and over the edges of maps, jumps ledges the way
they are jumped, crosses water unless Surf has certainly not been handed over
yet, and takes any boat and any warp a script makes (when is not read: the
walk must not fall short). The walls:
  - a blocker's tiles while its scene variable certainly has that value;
  - a person who certainly stands there: one who stands still, is never
    moved by a scene and has not been taken away yet, or one the map's own
    arrival script stands on a tile while its condition certainly holds;
  - a tree to cut until HM01 has been handed over.

It is a report, not a check, because of what it cannot know. Listed under
KNOWN below, with the reason: a scene that walks you in past a blocker, a
person the map data lists somewhere they are not, someone spoken to across a
table. A line it does report and that is not one of those belongs later in
map_order.yaml, where its place opens.

Run: uv run python -m firered_anki.reach      (about a minute; calls no model)
"""

import bisect
import re
import struct
from collections import Counter, defaultdict, deque

import pandas as pd

from . import blockers, walking
from .blockers import ANY_HEIGHT, MOVES_ABOUT, STEPS
from .decomp import DECOMP, Decomp, read
from .order import ORDER_OUT

START = ("PalletTown_PlayersHouse_2F", 6, 6)
WATER = 1                                    # the height of a tile of water
LEDGE = {0x38: (1, 0), 0x39: (-1, 0), 0x3A: (0, -1), 0x3B: (0, 1)}  # MB_JUMP_EAST…: the way each is jumped
PRIMARY = 640                                # metatiles in a primary tileset
SIDE = {(0, -1): "up", (0, 1): "down", (-1, 0): "left", (1, 0): "right"}
# Where a boat puts you down in the two harbours that are part of a town.
QUAYS = {"VermilionCity": (23, 33), "CinnabarIsland": (14, 12)}
# Reported, and right where they are. Map → why.
KNOWN = {
    "PalletTown_ProfessorOaksLab": "the starter scene: Oak walks you in, past the tiles that then keep you in",
    "ViridianCity": "the old man in the road: the map data lists him further north than he lies",
    "CeladonCity_Restaurant": "a diner spoken to across her table",
    "FourIsland_IcefallCave_Back": "Lorelei, in the scene that has just let you through",
    "FiveIsland_ResortGorgeous_House": "Selphy, who has gone indoors: the map data still has her at the door",
}


class World:
    def __init__(self, dc: Decomp, sides: blockers.Sides):
        self.dc, self.sides, self.ground = dc, sides, sides.ground
        self.blocks = dc._script_blocks
        maps = dc.map_json
        self.warps = {m: {(w["x"], w["y"]): w for w in maps[m].get("warp_events") or []} for m in maps}
        self.next_to = {m: [(c["direction"], dc.map_by_id.get(c["map"]), c["offset"]) for c in maps[m].get("connections") or []]
                        for m in maps}
        self.harbours = [m for m in maps if m.endswith("_Harbor")] + list(QUAYS)
        self.taken_to: dict[str, set] = defaultdict(set)      # map → where its scripts warp you
        self.stood_by: dict[str, dict] = defaultdict(lambda: defaultdict(list))  # map → person → [(x, y, condition)]
        self.moved: dict[str, set] = defaultdict(set)         # map → the people a scene moves
        for m in maps:
            arrival = f"{m}_OnTransition"
            called = {mm.group(4): mm.group(1, 2, 3) for c in self.blocks.get(arrival, (None, []))[1]
                      if (mm := re.match(r"call_if_(eq|ge|set|unset) (\w+)(?:, (\w+))?, (\w+)$", c))}
            for label, (where, cmds) in self.blocks.items():
                if where != m:
                    continue
                for c in cmds:
                    if (mm := re.match(r"warp\w* (MAP_\w+), (\d+), (\d+)", c)) and (dest := dc.map_by_id.get(mm.group(1))):
                        self.taken_to[m].add((dest, int(mm.group(2)), int(mm.group(3))))
                    if mm := re.match(r"setobjectxyperm (\w+), (\d+), (\d+)", c):
                        at = (int(mm.group(2)), int(mm.group(3)))
                        if label == arrival:
                            self.stood_by[m][mm.group(1)].append((*at, None))
                        elif label in called:
                            self.stood_by[m][mm.group(1)].append((*at, called[label]))
                        else:
                            self.moved[m].add(mm.group(1))
        paths = dict(re.findall(r'(gMetatileAttributes_\w+)\[\] = INCBIN_U32\("([^"]+)"\)', read("src/data/tilesets/metatiles.h")))
        self.behaviour = {}
        for name, attr in re.findall(r"const struct Tileset (gTileset_\w+) =\s*\{.*?\.metatileAttributes = (\w+),",
                                     read("src/data/tilesets/headers.h"), re.S):
            if (f := DECOMP / paths.get(attr, "-")).exists():
                raw = f.read_bytes()
                self.behaviour[name] = [a & 0x1FF for a in struct.unpack(f"<{len(raw) // 4}I", raw)]
        if not self.behaviour:
            raise SystemExit("no tile behaviours in vendor/: run scripts/fetch_vendor.sh (data/tilesets/*/*/metatile_attributes.bin)")
        self._ledges: dict[str, dict] = {}

    def ledges(self, m: str) -> dict[tuple[int, int], tuple[int, int]]:
        """Tile → the direction it is jumped in."""
        if m not in self._ledges:
            lay, out = self.dc.layouts.get(self.dc.map_json[m].get("layout")), {}
            if lay and lay["primary_tileset"] in self.behaviour and lay["secondary_tileset"] in self.behaviour:
                w, h = lay["width"], lay["height"]
                first, second = self.behaviour[lay["primary_tileset"]], self.behaviour[lay["secondary_tileset"]]
                for i, t in enumerate(struct.unpack(f"<{w * h}H", (DECOMP / lay["blockdata_filepath"]).read_bytes()[:w * h * 2])):
                    n = t & 0x3FF
                    b = first[n] if n < PRIMARY and n < len(first) else second[n - PRIMARY] if 0 <= n - PRIMARY < len(second) else 0
                    if b in LEDGE:
                        out[(i % w, i // w)] = LEDGE[b]
            self._ledges[m] = out
        return self._ledges[m]

    def holds(self, cond: tuple | None, moment) -> bool:
        """Whether a condition of an arrival script certainly holds."""
        if cond is None:
            return True
        kind, name, value = cond
        if kind in ("set", "unset"):
            return self.sides.true_by(name, moment) is (kind == "set")
        if not name.startswith("VAR_MAP_SCENE_") or not str(value).isdigit():
            return False
        n = int(value)
        reached = n == 0 or self.sides.true_by(f"{name}>={n}", moment) is True
        return reached if kind == "ge" else reached and self.sides.true_by(f"{name}>={n + 1}", moment) is False

    def walls(self, m: str, moment) -> set[tuple[int, int]]:
        """The tiles of a map that are certainly shut at this moment."""
        true_by, tiles = self.sides.true_by, set()
        for e in self.ground.triggers[m]:
            if self.holds(("eq", e["var"], str(e["var_value"])), moment):
                tiles.add((e["x"], e["y"]))
        for e in self.dc.map_json[m].get("object_events") or []:
            if "movement_type" not in e or MOVES_ABOUT.match(e["movement_type"]):
                continue
            looks, flag, who = e.get("graphics_id", ""), e.get("flag", "0"), e.get("local_id")
            if looks == "OBJ_EVENT_GFX_CUT_TREE":
                if true_by("FLAG_GOT_HM01", moment) is False:
                    tiles.add((e["x"], e["y"]))
                continue
            if "BOULDER" in looks or "ROCK_SMASH" in looks or "ITEM_BALL" in looks:
                continue
            if flag in self.dc.hidden_at_start:
                there = true_by(f"{flag} cleared", moment) is True
            else:
                there = not flag.startswith("FLAG_") or true_by(flag, moment) is False
            if not there or who in self.moved[m]:
                continue
            if who in self.stood_by[m]:
                tiles.update((x, y) for x, y, cond in self.stood_by[m][who] if self.holds(cond, moment))
            else:
                tiles.add((e["x"], e["y"]))
        return tiles

    def walk(self, wall, surf: bool = True) -> dict[str, set[tuple[int, int]]]:
        """Map → the tiles that can be stood on, from the bedroom. `wall(map)`
        gives the tiles that are shut."""
        tiles, stood, seen, queue, gone = self.ground.tiles, defaultdict(set), set(), deque(), set()

        def arrive(m: str, x: int, y: int) -> None:
            if not (g := tiles(m)):
                return
            w, h, open_, high = g
            for a, b in ((x, y), (x, y + 1), (x, y - 1), (x - 1, y), (x + 1, y)):
                if 0 <= a < w and 0 <= b < h and open_[b * w + a] and (a, b) not in wall(m):
                    if (state := (m, a, b, high[b * w + a])) not in seen:
                        seen.add(state)
                        queue.append(state)
                    return

        def step(m: str, a: int, b: int, here: int, at: int) -> None:
            w, h, open_, high = tiles(m)
            there = high[b * w + a]
            if at and there not in ANY_HEIGHT and there != at and not (surf and WATER in (at, there)):
                return
            if (state := (m, a, b, at if 15 in (here, there) else there)) not in seen:
                seen.add(state)
                queue.append(state)

        arrive(*START)
        while queue:
            m, x, y, at = queue.popleft()
            if m not in stood:
                for dest, a, b in self.taken_to[m]:
                    arrive(dest, a, b)
                if m in self.harbours:
                    for other in self.harbours:
                        w0 = (self.dc.map_json[other].get("warp_events") or [{}])[0]
                        arrive(other, *QUAYS.get(other, (w0.get("x", 0), w0.get("y", 0))))
            stood[m].add((x, y))
            w, h, open_, high = tiles(m)
            shut, here = wall(m), high[y * w + x]
            for spot in ((x, y), (x, y - 1), (x, y + 1), (x - 1, y), (x + 1, y)):
                wp = self.warps[m].get(spot)
                inside = 0 <= spot[0] < w and 0 <= spot[1] < h
                if wp and (spot == (x, y) or not inside or not open_[spot[1] * w + spot[0]]) and (m, spot) not in gone:
                    gone.add((m, spot))
                    dest = self.dc.map_by_id.get(wp.get("dest_map"))
                    there = self.dc.map_json[dest].get("warp_events") or [] if dest else []
                    if str(wp.get("dest_warp_id", "")).isdigit() and int(wp["dest_warp_id"]) < len(there):
                        arrive(dest, there[int(wp["dest_warp_id"])]["x"], there[int(wp["dest_warp_id"])]["y"])
            for dx, dy in STEPS:
                a, b = x + dx, y + dy
                if not (0 <= a < w and 0 <= b < h):          # over the edge, into the map beside this one
                    for direction, other, off in self.next_to[m]:
                        if direction != SIDE[(dx, dy)] or not other or not (g := tiles(other)):
                            continue
                        w2, h2, open2, _ = g
                        a2, b2 = {"up": (x - off, h2 - 1), "down": (x - off, 0), "left": (w2 - 1, y - off), "right": (0, y - off)}[direction]
                        if 0 <= a2 < w2 and 0 <= b2 < h2 and open2[b2 * w2 + a2] and (a2, b2) not in wall(other):
                            step(other, a2, b2, here, at)
                elif (a, b) in self.ledges(m):               # jumped in its own direction only, landing beyond it
                    a2, b2 = a + dx, b + dy
                    if (self.ledges(m)[(a, b)] == (dx, dy) and 0 <= a2 < w and 0 <= b2 < h and open_[b2 * w + a2]
                            and (a2, b2) not in shut and (state := (m, a2, b2, high[b2 * w + a2])) not in seen):
                        seen.add(state)
                        queue.append(state)
                elif open_[b * w + a] and (a, b) not in shut:
                    step(m, a, b, here, at)
        return stood


def walled_off(dc: Decomp, first: dict, last: dict) -> tuple[list[tuple], int, int]:
    """→ ((where it sorts, map, who, label) for each line shown while its
    speaker is walled off; the number of lines looked at; the number that
    cannot be judged, because no walk reaches the speaker at all)."""
    sides = blockers.Sides(dc, blockers.Ground(dc), first, last)
    world = World(dc, sides)
    said_by: dict[str, list] = defaultdict(list)
    for m, data in dc.map_json.items():
        for kind in ("object_events", "bg_events"):
            for e in data.get(kind) or []:
                if e.get("script") and e["script"] != "0x0":
                    for t in set(walking.texts_of(dc, e["script"])):
                        if t in first and dc.map_of_label(t) == m:
                            said_by[t].append((m, e))
    reach = lambda t, stood: any(world.ground.within_reach(m, e, stood.get(m, ())) for m, e in said_by[t])
    free = world.walk(lambda m: ())
    # The walls only change when something the scripts wait for becomes true.
    changes = sorted({w for c in dc.setters if (w := sides.when(c)) is not None})
    walks: dict[int, dict] = {}
    found, unjudged = [], 0
    for t in sorted(said_by, key=first.get):
        if not reach(t, free):
            unjudged += 1
            continue
        k = bisect.bisect_left(changes, first[t])
        if k not in walks:
            cache: dict[str, set] = {}
            wall = lambda m, moment=first[t], cache=cache: cache[m] if m in cache else cache.setdefault(m, world.walls(m, moment))
            walks[k] = world.walk(wall, surf=sides.true_by("FLAG_GOT_HM03", first[t]) is not False)
        if not reach(t, walks[k]):
            m, e = said_by[t][0]
            found.append((first[t], m, e["script"].split("EventScript_")[-1], t))
    return found, len(said_by), unjudged


def main() -> None:
    dc = Decomp()
    deck = pd.read_parquet(ORDER_OUT)
    said = deck[deck["dialogue"] & (deck["deck"] == "story")].groupby("label")["first_seen_order"].agg(["min", "max"])
    found, looked_at, unjudged = walled_off(dc, said["min"].to_dict(), said["max"].to_dict())
    by_map: dict[str, list] = defaultdict(list)
    for at, m, who, label in found:
        by_map[m].append((who, label))
    new = {m: rows for m, rows in by_map.items() if m not in KNOWN}
    print(f"{looked_at} lines of people and signs; {unjudged} cannot be judged (no walk reaches the speaker: a puzzle door, a quiz gate)")
    print(f"{len(found)} are shown while their speaker is walled off: {sum(map(len, new.values()))} to look at, "
          f"{len(found) - sum(map(len, new.values()))} known and right where they are")
    for m, rows in new.items():
        print(f"\n{m}: " + ", ".join(f"{who} {n}" for who, n in Counter(who for who, _ in rows).items()))
        for _, label in rows:
            print(f"    {label}")
    print("\nknown:")
    for m, why in KNOWN.items():
        print(f"  {len(by_map.get(m, [])):3d}  {m}: {why}")


if __name__ == "__main__":
    main()
