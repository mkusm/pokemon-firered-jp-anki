"""The order you walk past a map's people and signs.

The text dump lists a map's lines in the order the scripts were written: on
Nugget Bridge the prize comes first and the five trainers in reverse. Here a
map's lines are ordered by how far each person, sign or trigger is from where
the story first brings you in, measured along tiles you can walk on.

  - Where you come in: the door or the edge that leads to the map visited just
    before this one in map_order.yaml (else the earliest neighbour).
  - A person's or a sign's lines stay together, in the order the dump has them.
  - A line no event on the map shows (a cutscene started by a map script, a
    line reached through a macro this does not read) stays next to the line
    before it in the dump, or first.
  - What a trainer says when you battle them again (…RematchIntro) needs the
    VS Seeker and comes after everything else on the map.

It is an approximation: people wander, a script can move someone before you
arrive, water counts as walkable, and a town has no single route. Scenes that
matter are listed line by line in map_order.yaml and are not touched here.
"""

import math
import re
import struct
from collections import deque

from .decomp import DECOMP, Decomp

TEXT = re.compile(r"\b(\w+_Text_\w+|g?Text_\w+)\b")
JUMP = re.compile(r"(?:goto|call)(?:_if_\w+)? .*?(\w+)$")
EDGE = {"up": lambda w, h: [(x, 0) for x in range(w)], "down": lambda w, h: [(x, h - 1) for x in range(w)],
        "left": lambda w, h: [(0, y) for y in range(h)], "right": lambda w, h: [(w - 1, y) for y in range(h)]}


def grid(dc: Decomp, name: str) -> tuple[int, int, list[bool]] | None:
    """(width, height, whether each tile can be walked on). The block data is
    16 bits a tile; bits 10 and 11 are its collision."""
    lay = dc.layouts.get(dc.map_json[name].get("layout"))
    if not lay:
        return None
    raw = (DECOMP / lay["blockdata_filepath"]).read_bytes()
    w, h = lay["width"], lay["height"]
    tiles = struct.unpack(f"<{w * h}H", raw[:w * h * 2])
    return w, h, [(t >> 10) & 3 == 0 for t in tiles]


def texts_of(dc: Decomp, script: str, seen: set | None = None, depth: int = 0) -> list[str]:
    """The text labels a script can show, through its gotos and calls."""
    blocks = dc._script_blocks
    seen = set() if seen is None else seen
    if script in seen or script not in blocks or depth > 6:
        return []
    seen.add(script)
    out = []
    for c in blocks[script][1]:
        out += TEXT.findall(c)
        if m := JUMP.match(c):
            out += texts_of(dc, m.group(1), seen, depth + 1)
    return out


def way_in(dc: Decomp, name: str, map_rank: dict, w: int, h: int) -> list[tuple[int, int]]:
    """The tiles you first arrive on."""
    m = dc.map_json[name]
    here = map_rank.get(name, math.inf)
    ways: dict[str, list] = {}
    for warp in m.get("warp_events") or []:
        if other := dc.map_by_id.get(warp.get("dest_map")):
            ways.setdefault(other, []).append((warp["x"], warp["y"]))
    for c in m.get("connections") or []:
        if (other := dc.map_by_id.get(c["map"])) and c["direction"] in EDGE:
            ways.setdefault(other, []).extend(EDGE[c["direction"]](w, h))
    ways.pop(name, None)
    ranked = {o: map_rank.get(o, math.inf) for o in ways}
    before = [o for o in ways if ranked[o] < here]
    if before:
        return ways[max(before, key=ranked.get)]        # the map visited just before
    known = [o for o in ways if not math.isinf(ranked[o])]
    return ways[min(known, key=ranked.get)] if known else []


def distances(w: int, h: int, open_: list[bool], start: list[tuple[int, int]]) -> list[float]:
    dist = [math.inf] * (w * h)
    queue = deque()
    for x, y in start:  # a door's own tile is often solid: start beside it too
        for a, b in ((x, y), (x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= a < w and 0 <= b < h and open_[b * w + a] and math.isinf(dist[b * w + a]):
                dist[b * w + a] = 0
                queue.append((a, b))
    while queue:
        x, y = queue.popleft()
        d = dist[y * w + x] + 1
        for a, b in ((x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1)):
            if 0 <= a < w and 0 <= b < h and open_[b * w + a] and d < dist[b * w + a]:
                dist[b * w + a] = d
                queue.append((a, b))
    return dist


def order(dc: Decomp, name: str, labels: list[str], map_rank: dict) -> list[str] | None:
    """These labels (a map's lines, in the dump's order) in walking order.
    None when the map has no layout or no way in that can be told."""
    if name not in dc.map_json or not (g := grid(dc, name)):
        return None
    w, h, open_ = g
    start = way_in(dc, name, map_rank, w, h)
    if not start:
        return None
    dist = distances(w, h, open_, start)
    reached = max((d for d in dist if not math.isinf(d)), default=0)

    def far(x: int, y: int) -> float:
        """Steps to the tile or to one beside it (a sign is solid, you read it
        from next to it); a tile that cannot be reached, by the straight line."""
        near = [dist[b * w + a] for a, b in ((x, y), (x + 1, y), (x - 1, y), (x, y + 1), (x, y - 1))
                if 0 <= a < w and 0 <= b < h]
        best = min(near, default=math.inf)
        return best if not math.isinf(best) else reached + 1 + min(math.dist((x, y), s) for s in start)

    m = dc.map_json[name]
    mine = set(labels)
    place: dict[str, tuple[float, int]] = {}   # label → (steps, which event)
    events = [e for kind in ("object_events", "bg_events", "coord_events") for e in m.get(kind) or []]
    for n, e in enumerate(events):
        script = e.get("script")
        if not script or script == "0x0":
            continue
        d = far(e["x"], e["y"])
        for t in texts_of(dc, script):
            if t in mine and (t not in place or d < place[t][0]):
                place[t] = (d, n)
    if not place:
        return None
    last = (-1.0, -1)                           # before the first event: as you arrive
    keyed = []
    for i, label in enumerate(labels):
        last = place.get(label, last)
        keyed.append((label.endswith("RematchIntro"), *last, i, label))
    return [label for *_, label in sorted(keyed)]
