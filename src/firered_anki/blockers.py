"""What can be read before the game stops you.

A blocker is a tile that runs a script when you step on it while a scene
variable has a certain value: Professor Oak in the grass north of Pallet
Town, the rival on the bridge out of Cerulean. Until its scene has run you
cannot go past, and everything on your side of it can be read first: the
people and signs you can still walk to, and whatever is behind the doors you
can still reach. Oak's lab is open before Oak stops you, so its aides, its
signs and the e-mail on its computer come before he does.

  - The blockers are the maps' own list: every `trigger` in a map.json.
    Only one on the way forward counts: one that guards a side room (the
    ticket counter of the Pewter Museum) leaves the walkthrough's order be.
  - Shut, a blocker's tiles part the ground around them. Each part is found
    by walking: over tiles you can walk on, through doors and stairs. A
    tile's height counts (water is lower than land, so nobody wades), a
    person who stands still is in the way (so are a tree to cut and a
    sleeping Snorlax), and any other blocker that may still be shut is a
    wall. The walk does not leave by a map's edge: whether a neighbouring
    route could be walked first is the route's business (map_order.yaml).
  - Your side is the part where the deck has already shown a line, when the
    other parts have shown none.
  - A line there is readable if some way a script shows it needs nothing
    that is not yet true when you reach the blocker, and nothing that is
    over by then.

Everything errs towards a smaller side: a line this cannot prove readable
stays where it was. And it only ever moves a line earlier: a tile the walk
did not reach has not been shown to be out of reach.
"""

import re
import struct
from collections import deque
from dataclasses import dataclass
from functools import cached_property

from . import walking
from .decomp import DECOMP, Decomp

STEPS = ((1, 0), (-1, 0), (0, 1), (0, -1))
MOVES_ABOUT = re.compile(r"MOVEMENT_TYPE_(WANDER|WALK)_")
ANY_HEIGHT = (0, 15)   # a tile at height 0 or 15 can be stepped on from any height
# A script command that changes the game, and not only what is said.
CHANGES = re.compile(r"setflag (?!FLAG_TEMP)|setvar VAR_MAP_SCENE|setworldmapflag|trainerbattle|setwildbattle|givemon|"
                     r"giveitem|msgreceiveditem|finditem|additem")


@dataclass(frozen=True)
class Blocker:
    map: str
    var: str
    value: int
    tiles: frozenset     # the tiles that run it
    scripts: tuple

    @property
    def name(self) -> str:
        return f"{self.map}, {self.var.replace('VAR_MAP_SCENE_', '')} = {self.value}"


class Ground:
    """The maps as places to walk."""

    def __init__(self, dc: Decomp):
        self.dc = dc
        self._tiles: dict[str, tuple | None] = {}

    def tiles(self, name: str) -> tuple[int, int, list[bool], list[int]] | None:
        """(width, height, whether each tile is open, its height). The block
        data is 16 bits a tile: bits 10 and 11 its collision, 12 to 15 its
        height."""
        if name not in self._tiles:
            lay = self.dc.layouts.get(self.dc.map_json[name].get("layout"))
            if not lay:
                self._tiles[name] = None
            else:
                w, h = lay["width"], lay["height"]
                raw = struct.unpack(f"<{w * h}H", (DECOMP / lay["blockdata_filepath"]).read_bytes()[:w * h * 2])
                self._tiles[name] = (w, h, [(t >> 10) & 3 == 0 for t in raw], [t >> 12 for t in raw])
        return self._tiles[name]

    @cached_property
    def triggers(self) -> dict[str, list[dict]]:
        return {name: [e for e in m.get("coord_events") or [] if e.get("type") == "trigger" and e.get("script")]
                for name, m in self.dc.map_json.items()}

    @cached_property
    def moved_to(self) -> dict[str, dict[str, set]]:
        """Map → a person's id → the other tiles a script of the map can
        stand them on (the policeman in front of the robbed house in
        Cerulean). When it does so is not read: each is taken as in the way."""
        out: dict[str, dict] = {m: {} for m in self.dc.map_json}
        for where, cmds in self.dc._script_blocks.values():
            for c in cmds:
                if where in out and (m := re.match(r"setobjectxyperm (\w+), (\d+), (\d+)", c)):
                    out[where].setdefault(m.group(1), set()).add((int(m.group(2)), int(m.group(3))))
        return out

    @cached_property
    def blockers(self) -> list[Blocker]:
        """Every blocker on a scene variable. A tile on a temporary variable
        is not one: it runs on every visit (the Safari Zone's counter, the
        locked door of Cinnabar's gym) and is only ever a wall here."""
        found: dict[tuple, list] = {}
        for name, events in self.triggers.items():
            for e in events:
                if e["var"].startswith("VAR_MAP_SCENE_") and str(e["var_value"]).isdigit():
                    found.setdefault((name, e["var"], int(e["var_value"])), []).append(e)
        return [Blocker(name, var, n, frozenset((e["x"], e["y"]) for e in events),
                        tuple(sorted({e["script"] for e in events})))
                for (name, var, n), events in found.items()]

    def walk(self, name: str, start: list[tuple[int, int]], wall, any_door: bool = False) -> dict[str, set[tuple[int, int]]]:
        """Map → the tiles that can be stood on, walking from `start` on map
        `name` and going through doors. `wall(map)` is the set of tiles that
        are shut on that map. `any_door`: a door counts from whichever side
        it is stood beside, for a walk that must not fall short."""
        stood: dict[str, set] = {}
        seen: set = set()
        queue: deque = deque()

        def arrive(m: str, x: int, y: int, height: int | None = None) -> None:
            """Stand on this tile, or just below it when it is a door."""
            if not (g := self.tiles(m)):
                return
            w, h, open_, high = g
            for a, b in ((x, y), (x, y + 1)):
                if 0 <= a < w and 0 <= b < h and open_[b * w + a] and (a, b) not in wall(m):
                    state = (m, a, b, high[b * w + a] if height is None else height)
                    if state not in seen:
                        seen.add(state)
                        queue.append(state)
                    return

        for x, y in start:
            arrive(name, x, y)
        warps = {m: {(wp["x"], wp["y"]): wp for wp in self.dc.map_json[m].get("warp_events") or []}
                 for m in self.dc.map_json}
        gone_through: set = set()
        while queue:
            m, x, y, at = queue.popleft()
            stood.setdefault(m, set()).add((x, y))
            w, h, open_, high = self.tiles(m)
            shut = wall(m)
            # A door is a solid tile entered from below; stairs, a ladder or a
            # doormat is the tile stood on.
            for spot in ((x, y), (x, y - 1), *(((x, y + 1), (x - 1, y), (x + 1, y)) if any_door else ())):
                wp = warps[m].get(spot)
                if wp and (spot == (x, y) or not open_[spot[1] * w + spot[0]]) and (m, spot) not in gone_through:
                    gone_through.add((m, spot))
                    dest = self.dc.map_by_id.get(wp.get("dest_map"))
                    there = self.dc.map_json[dest].get("warp_events") or [] if dest else []
                    if str(wp.get("dest_warp_id", "")).isdigit() and int(wp["dest_warp_id"]) < len(there):
                        to = there[int(wp["dest_warp_id"])]
                        arrive(dest, to["x"], to["y"])
            here = high[y * w + x]
            for dx, dy in STEPS:
                a, b = x + dx, y + dy
                if not (0 <= a < w and 0 <= b < h) or not open_[b * w + a] or (a, b) in shut:
                    continue
                there = high[b * w + a]
                if at and there not in ANY_HEIGHT and there != at:
                    continue                     # a step up or down: water, a bridge overhead
                state = (m, a, b, at if 15 in (here, there) else there)
                if state not in seen:
                    seen.add(state)
                    queue.append(state)
        return stood

    def within_reach(self, m: str, e: dict, stood: set) -> bool:
        """Whether a person or sign can be spoken to from where you can stand:
        from the next tile, or indoors across one solid tile (a counter)."""
        x, y = e["x"], e["y"]
        if any((x + dx, y + dy) in stood for dx, dy in STEPS):
            return True
        if self.dc.map_json[m].get("connections") or not (g := self.tiles(m)):
            return False
        w, h, open_, _ = g
        return any((x + 2 * dx, y + 2 * dy) in stood and 0 <= x + dx < w and 0 <= y + dy < h
                   and not open_[(y + dy) * w + x + dx] for dx, dy in STEPS)


class Sides:
    """For each blocker, what is on your side of it when you reach it, given
    where every line is placed.

    first, last: label → where its first and its last sentence sort (any
    values that compare)."""

    def __init__(self, dc: Decomp, ground: Ground, first: dict, last: dict):
        self.dc, self.ground, self.first, self.last = dc, ground, first, last
        self._when: dict[str, object] = {}

    def when(self, cond: str):
        """Where in the deck a condition becomes true: after the last line of
        the first scene that sets it. None when no scene that sets it has a
        place."""
        if cond not in self._when:
            times = []
            for _, _, lines in self.dc.setters.get(cond, ()):
                placed = [self.last[t] for t in lines if t in self.last]
                if placed:
                    times.append(max(placed))
            self._when[cond] = min(times) if times else None
        return self._when[cond]

    def true_by(self, cond: str, moment) -> bool | None:
        """Whether a condition is true before this moment. None: not known."""
        t = self.when(cond)
        return None if t is None else t < moment

    def scene_of(self, b: Blocker) -> list[str]:
        """The lines of the scene that takes you past this blocker: those of
        the part of its script that moves its variable on. A blocker that
        only turns you back has none, and a guard who lets you by once you
        have something (the tea) has only the lines for that."""
        blocks = self.dc._script_blocks
        mine: set[str] = set()
        todo = list(b.scripts)
        while todo:
            script = todo.pop()
            if script not in mine and script in blocks:
                mine.add(script)
                todo += [m.group(1) for c in blocks[script][1] if (m := walking.JUMP.match(c))]
        return [t for _, block, lines in self.dc.setters.get(f"{b.var}>={b.value + 1}", ()) if block in mine
                for t in lines if t in self.first]

    def shut(self, m: str, moment, but: Blocker | None = None) -> set[tuple[int, int]]:
        """The tiles of map m that cannot be walked through at this moment."""
        tiles: set = set()
        for e in self.ground.triggers[m]:
            var, n = e["var"], str(e["var_value"])
            if but and m == but.map and var == but.var and n == str(but.value):
                continue
            if var.startswith("VAR_MAP_SCENE_") and n.isdigit():
                not_yet = int(n) > 0 and self.true_by(f"{var}>={n}", moment) is False
                done = self.true_by(f"{var}>={int(n) + 1}", moment) is True
                if not_yet or done:
                    continue
            tiles.add((e["x"], e["y"]))
        for e in self.dc.map_json[m].get("object_events") or []:
            if "movement_type" not in e or MOVES_ABOUT.match(e["movement_type"]):
                continue
            flag = e.get("flag", "0")
            if flag in self.dc.hidden_at_start:
                there = self.true_by(f"{flag} cleared", moment) is not False
            else:
                there = not (flag.startswith("FLAG_") and self.true_by(flag, moment) is True)
            if there:
                tiles.add((e["x"], e["y"]))
                tiles.update(self.ground.moved_to[m].get(e.get("local_id"), ()))
        return tiles

    def readable(self, label: str, moment) -> bool:
        """Whether a line can be read at this moment, as far as its conditions
        go: some way a script shows it asks for nothing that is not true yet
        and nothing that is over. A condition this cannot time counts
        against it."""
        return any(all(self.true_by(c, moment) is True for c in must)
                   and not any(self.true_by(c, moment) is True for c in not_yet)
                   for must, not_yet in self.dc.shown_when.get(label, ()))

    def before(self, b: Blocker) -> tuple[object, dict[str, str]] | None:
        """(the moment the blocker is passed, the lines readable before it
        with the map each is on), or None when this cannot be told.

        Shut, the blocker's tiles part the ground around them. Your side is
        the part where the deck has already shown a line; the far side is
        where it has shown none. Nothing is said when that does not single
        out one part (Mt. Moon's fossils are listed before the man who
        guards them, so both parts have been "visited"), or when the blocker
        was switched on by a scene in its own map (the rival's battle in
        Oak's lab, once you hold a Pokémon): where you stand then is that
        scene's doing."""
        scene = self.scene_of(b)
        g = self.ground.tiles(b.map)
        if not scene or not g:
            return None
        moment = min(self.first[t] for t in scene)
        if self.true_by(f"{b.var}>={b.value + 1}", moment):
            return None      # passed elsewhere first: one cup of tea opens all four of Saffron's gates
        if b.value and b.map in self.dc.set_in.get(f"{b.var}>={b.value}", ()):
            return None
        scene = {t for s in b.scripts for t in walking.texts_of(self.dc, s)}  # all it can say, either way it goes
        # The scene starts with its first line that belongs to this visit: the
        # museum's "it's ¥50 for a child's ticket", not the thanks for paying.
        moment = min((self.first[t] for t in scene if t in self.first and self.first[t] <= moment and self.readable(t, moment)),
                     default=moment)
        w, h, open_, _ = g
        walls: dict[str, set] = {}

        def wall(m: str) -> set:
            if m not in walls:
                walls[m] = self.shut(m, moment) | (set(b.tiles) if m == b.map else set())
            return walls[m]

        parts: list[dict[str, set]] = []
        begun: list[tuple[int, int]] = []        # the tile each part was walked from
        for x, y in sorted(b.tiles):
            for dx, dy in STEPS:
                a, c = x + dx, y + dy
                if (0 <= a < w and 0 <= c < h and open_[c * w + a] and (a, c) not in wall(b.map)
                        and not any((a, c) in part.get(b.map, ()) for part in parts)):
                    parts.append(self.ground.walk(b.map, [(a, c)], wall))
                    begun.append((a, c))
        mine, been = [], []
        for part in parts:
            lines: dict[str, str] = {}
            visited = False                      # the deck has shown a line only this part has
            for m, stood in part.items():
                for kind in ("object_events", "bg_events"):
                    for e in self.dc.map_json[m].get(kind) or []:
                        script = e.get("script")
                        if not script or script == "0x0" or not self.ground.within_reach(m, e, stood):
                            continue
                        # Someone who can be spoken to from two sides says nothing about which side you were on.
                        only_here = not any(self.ground.within_reach(m, e, other.get(m, ())) for other in parts if other is not part)
                        for t in walking.texts_of(self.dc, script):
                            # A line the whole game shares (a shop's greeting, a tree that
                            # can be cut) has its place from elsewhere.
                            if t not in self.first or self.dc.map_of_label(t) != m:
                                continue
                            visited |= only_here and self.last[t] < moment
                            if t not in scene and not t.endswith("RematchIntro") and self.readable(t, moment):
                                lines.setdefault(t, m)
            mine.append(lines)
            been.append(visited)
        if len(parts) < 2 or sum(been) != 1:
            return None
        # What is behind it, walked without the caution used for your side: nobody
        # in the way, no other blocker shut. Falling short here would drop a real blocker.
        only_this = lambda m: b.tiles if m == b.map else ()
        behind = [self.ground.walk(b.map, [at], only_this, any_door=True) for at, here in zip(begun, been) if not here]
        if not any(self.leads_on(far, parts[been.index(True)], b) for far in behind):
            return None      # it only guards a side room: the walkthrough's order stands
        return moment, mine[been.index(True)]

    def leads_on(self, far: dict[str, set], near: dict[str, set], b: Blocker) -> str | None:
        """What makes the ground behind a blocker part of the way forward:
        a map's edge to walk out by, or a person, a thing or a tile whose
        script changes the game (sets a flag or a scene variable, hands over
        an item or a Pokémon, starts a battle). None when there is only talk
        behind it: the Pewter Museum's counter guards an exhibition, and
        being stopped there is no reason to see the rest of the town first."""
        blocks = self.dc._script_blocks

        def changes(script: str, seen: set) -> str | None:
            if script in seen or script not in blocks:
                return None
            seen.add(script)
            for c in blocks[script][1]:
                if CHANGES.match(c):
                    return c
                if (m := walking.JUMP.match(c)) and (found := changes(m.group(1), seen)):
                    return found
            return None

        for m, stood in far.items():
            data, (w, h, _, _) = self.dc.map_json[m], self.ground.tiles(m)
            for c in data.get("connections") or []:
                if c["direction"] in walking.EDGE and stood & set(walking.EDGE[c["direction"]](w, h)):
                    return f"the edge of {m}"
            for kind in ("object_events", "bg_events", "coord_events"):
                for e in data.get(kind) or []:
                    at = (e["x"], e["y"])
                    if m == b.map and at in b.tiles:
                        continue
                    here = at in stood if kind == "coord_events" else self.ground.within_reach(m, e, stood)
                    if not here or self.ground.within_reach(m, e, near.get(m, ())):
                        continue
                    if e.get("type") == "hidden_item":
                        return f"{e['item']} on {m}"
                    if (script := e.get("script")) and (found := changes(script, set())):
                        return f"{script}: {found}"
        return None


def late(sides: Sides) -> list[tuple[Blocker, object, str, str]]:
    """(blocker, the moment it is passed, label, the label's map) for every
    line that can be read before a blocker and is placed after it."""
    out = []
    for b in sides.ground.blockers:
        if found := sides.before(b):
            moment, lines = found
            out += [(b, moment, t, m) for t, m in lines.items() if sides.first[t] > moment]
    return out
