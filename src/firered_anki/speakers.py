"""Who says a line, read from the map it is said on.

The game names a speaker only where the line itself does (オーキド『…). For
the rest, a map's data says which person stands where and which script
talking to them runs (map.json, object_events), and the script says which
texts it shows. So a text that only one kind of person on its map can show is
that person's. The person is named by their sprite: a named character by the
name the game uses, anyone else by what they are (old man, clerk, hiker).

Inside a script the game says when the voice changes: it sets the text colour
to neutral before the narrator's lines (a letter, "レッド got the item") and to
a man's or a woman's before somebody else's, and puts it back afterwards. A
text shown under a colour the script set itself is not counted as the
person's.

Two people of the same sex need no change of colour, so a scene is told
another way: a script that moves or turns another person of its map (the
rival in Professor Oak's script) has more than one voice in it, and none of
its texts is given a speaker here. The game names the speaker itself in most
such lines.

What this cannot tell is left without a speaker: a text two different people
show, a scene, a line started by stepping on a tile, a sign or a shelf.
"""

import re
from functools import lru_cache

from .decomp import Decomp
from .walking import JUMP, TEXT

# Named characters, as the game writes them (names_by_hand.yaml has the English).
NAMED = {
    "PROF_OAK": "オーキド", "BLUE": "グリーン", "MOM": "おかあさん", "DAISY": "ナナミ", "BILL": "マサキ",
    "MR_FUJI": "フジ", "CELIO": "ニシキ", "GIOVANNI": "サカキ",
    "BROCK": "タケシ", "MISTY": "カスミ", "LT_SURGE": "マチス", "ERIKA": "エリカ", "KOGA": "キョウ",
    "SABRINA": "ナツメ", "BLAINE": "カツラ",
    "LORELEI": "カンナ", "BRUNO": "シバ", "AGATHA": "キクコ", "LANCE": "ワタル",
}
# Everyone else, by sprite. A sprite not listed (an item ball, a boulder, a
# Pokémon, a sprite chosen at run time) gives no speaker.
PEOPLE = {
    "ROCKET_M": "Team Rocket grunt", "ROCKET_F": "Team Rocket grunt",
    "CABLE_CLUB_RECEPTIONIST": "receptionist", "UNION_ROOM_RECEPTIONIST": "receptionist",
    "SCIENTIST": "scientist", "YOUNGSTER": "youngster", "LASS": "lass", "ROCKER": "rocker", "BIKER": "biker",
    "HIKER": "hiker", "GENTLEMAN": "gentleman", "PICNICKER": "picnicker", "CAMPER": "camper",
    "MAN": "man", "BALDING_MAN": "man", "FAT_MAN": "man", "TRAINER_TOWER_DUDE": "man",
    "WOMAN_1": "woman", "WOMAN_2": "woman", "WOMAN_3": "woman",
    "OLD_MAN_1": "old man", "OLD_MAN_2": "old man", "OLD_WOMAN": "old woman",
    "BOY": "boy", "LITTLE_BOY": "little boy", "LITTLE_GIRL": "little girl", "GBA_KID": "boy",
    "TUBER_M_WATER": "boy", "TUBER_F": "girl",
    "SAILOR": "sailor", "CAPTAIN": "captain", "FISHER": "fisherman", "WORKER_M": "worker", "WORKER_F": "worker",
    "BUG_CATCHER": "bug catcher", "CLERK": "clerk", "NURSE": "nurse", "CHEF": "chef", "POLICEMAN": "policeman",
    "BLACK_BELT": "black belt", "CRUSH_GIRL": "crush girl", "CHANNELER": "channeler", "BEAUTY": "beauty",
    "POKE_MANIAC": "Pokémaniac", "COOLTRAINER_M": "trainer", "COOLTRAINER_F": "trainer",
    "SWIMMER_M_WATER": "swimmer", "SWIMMER_F_WATER": "swimmer", "SWIMMER_M_LAND": "swimmer",
    "SWIMMER_F_LAND": "swimmer", "GYM_GUY": "gym guide", "MG_DELIVERYMAN": "deliveryman",
}
# The narrator inside someone's script: what the player did or got.
NARRATOR = re.compile(r"^(?:レッドは|レッドの|＊は|＊を)")
ITEM_MESSAGE = re.compile(r"\s*(?:giveitem_msg|msgreceiveditem)\b")


PERSON = re.compile(r"\bLOCALID_\w+")


def texts_of(dc: Decomp, script: str, others: set, own: bool = True, seen: set | None = None,
             depth: int = 0) -> list[tuple[str, bool]] | None:
    """The texts a script can show, through its gotos and calls, each with
    whether it is in the voice of the person the script belongs to. None for
    a scene: a script that names one of `others`, the map's other people."""
    blocks = dc._script_blocks
    seen = set() if seen is None else seen
    if script in seen or script not in blocks or depth > 6:
        return []
    seen.add(script)
    out = []
    for c in blocks[script][1]:
        if others & set(PERSON.findall(c)):
            return None
        if c.strip().startswith("textcolor"):
            own = False
        elif "EventScript_RestorePrevTextColor" in c:
            own = True
        else:
            out += [(t, own and not ITEM_MESSAGE.match(c)) for t in TEXT.findall(c)]
            if m := JUMP.match(c):
                if (more := texts_of(dc, m.group(1), others, own, seen, depth + 1)) is None:
                    return None
                out += more
    return out


def all_texts(dc: Decomp, script: str) -> list[str]:
    """Every text a script can reach, scene or not."""
    return [t for t, _ in texts_of(dc, script, set()) or []]


@lru_cache(maxsize=None)
def from_maps() -> dict[str, str]:
    """text label → who says it, where only one kind of person can."""
    dc = Decomp()
    said: dict[str, set[str]] = {}
    for m in dc.map_json.values():
        events = m.get("object_events") or []
        sprite = lambda e: e["graphics_id"].removeprefix("OBJ_EVENT_GFX_")
        people = {e["local_id"] for e in events if e.get("local_id") and (sprite(e) in NAMED or sprite(e) in PEOPLE)}
        for e in events:
            script = e.get("script")
            if not script or script == "0x0":
                continue
            who = NAMED.get(sprite(e)) or PEOPLE.get(sprite(e)) or ""
            texts = texts_of(dc, script, people - {e.get("local_id")})
            if texts is None:  # a scene: nobody's for certain
                texts = [(t, False) for t in all_texts(dc, script)]
            for label, own in texts:
                said.setdefault(label, set()).add(who if own else "")
    return {label: next(iter(ws)) for label, ws in said.items() if len(ws) == 1 and "" not in ws}


def who(label: str, text: str) -> str:
    """The speaker of this sentence of the text, or "" when it cannot be told."""
    return "" if NARRATOR.match(text) else from_maps().get(label, "")
