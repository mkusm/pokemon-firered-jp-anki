"""What each ＊ in a sentence stands for, as a line for the card.

The game fills names and numbers into its text as it shows it. The extract
stage writes every such variable as ＊ and keeps its code. A battle message's
code says what goes there (battle_message.c, BattleStringExpandPlaceholders):
the attacker's name, the target's, a move, an item, an ability. (The game
puts やせいの or あいての before the name of a wild or a foe's Pokémon.) The other
codes are plain buffers (STR_VAR_1, DYNAMIC 0, B_BUFF1) that any script or
screen may fill with anything. Those are named in two cases: where a map
script fills the buffer just above the message that shows it
(`bufferspeciesname STR_VAR_1, …` and then `message …`), and where the
sentence itself tells: a ＊ with a counter after it, or Ｌｖ before it, is a
number. What a screen written in C puts into a buffer is not read.

A sentence in which nothing can be named gets no line.
"""

import json
import re
from functools import lru_cache

from .decomp import DECOMP

VAR = "＊"

# From the cases of BattleStringExpandPlaceholders in battle_message.c.
BATTLE = {
    "B_ATK_NAME_WITH_PREFIX": "the Pokémon making the move",
    "B_DEF_NAME_WITH_PREFIX": "the Pokémon it is aimed at",
    "B_EFF_NAME_WITH_PREFIX": "the Pokémon the effect falls on",
    "B_SCR_ACTIVE_NAME_WITH_PREFIX": "the Pokémon this is about",
    "B_ACTIVE_NAME_WITH_PREFIX": "the Pokémon this is about",
    "B_CURRENT_MOVE": "the move being used",
    "B_LAST_ITEM": "the item just used",
    "B_LAST_ABILITY": "the ability that just worked",
    "B_ATK_ABILITY": "the ability of the Pokémon making the move",
    "B_DEF_ABILITY": "the ability of the Pokémon it is aimed at",
    "B_SCR_ACTIVE_ABILITY": "that Pokémon's ability",
    "B_EFF_ABILITY": "the ability of the Pokémon the effect falls on",
    "B_TRAINER1_CLASS": "the kind of trainer (Bug Catcher, Lass…)",
    "B_TRAINER1_NAME": "the trainer's name",
    "B_PLAYER_MON1_NAME": "your Pokémon",
    "B_PLAYER_MON2_NAME": "your second Pokémon",
    "B_OPPONENT_MON1_NAME": "the opponent's Pokémon",
    "B_OPPONENT_MON2_NAME": "the opponent's second Pokémon",
    "B_LINK_PLAYER_MON1_NAME": "your Pokémon",
    "B_LINK_PLAYER_MON2_NAME": "your second Pokémon",
    "B_LINK_OPPONENT_MON1_NAME": "the other player's Pokémon",
    "B_LINK_OPPONENT_MON2_NAME": "the other player's second Pokémon",
    "B_LINK_OPPONENT1_NAME": "the other player's name",
    "B_LINK_OPPONENT2_NAME": "the second other player's name",
    "B_LINK_PARTNER_NAME": "your partner's name",
    "B_LINK_SCR_TRAINER_NAME": "a player's name",
    "B_PC_CREATOR_NAME": "だれかの or マサキの (whose PC it is)",
    # The side of the Pokémon making the move, or of the one it is aimed at.
    "B_ATK_PREFIX1": "みかたの or あいての (your side's or the foe's)",
    "B_ATK_PREFIX2": "みかたは or あいては (your side or the foe's)",
    "B_ATK_PREFIX3": "みかたを or あいてを (your side or the foe's)",
    "B_DEF_PREFIX1": "みかたの or あいての (your side's or the foe's)",
}
# Script commands that fill a text buffer, and what they put there.
BUFFERS = {
    "bufferspeciesname": "a Pokémon",
    "bufferleadmonspeciesname": "your first Pokémon",
    "bufferpartymonnick": "one of your Pokémon",
    "bufferitemname": "an item",
    "bufferitemnameplural": "an item",
    "buffermovename": "a move",
    "buffernumberstring": "a number",
    "bufferboxname": "a PC box",
}
BUFFER = re.compile(r"^\s*(buffer\w+)\s+(STR_VAR_\d)")
LABEL = re.compile(r"^(\w+)::?")
SHOWN = re.compile(r"\b\w*Text_\w+")


@lru_cache(maxsize=None)
def from_scripts() -> dict[str, dict[str, str]]:
    """text label → {buffer: what a script put in it before showing the text}.
    Only what stands above the message inside the same script; a buffer that
    two scripts fill differently for one text is left out."""
    found: dict[str, dict[str, set]] = {}
    for path in sorted((DECOMP / "data").rglob("*.inc")) + sorted((DECOMP / "data").glob("*.s")):
        filled: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            if LABEL.match(line):
                filled = {}
            elif m := BUFFER.match(line):
                filled[m.group(2)] = BUFFERS.get(m.group(1), "")
            elif filled:
                for label in SHOWN.findall(line):
                    for var, what in filled.items():
                        found.setdefault(label, {}).setdefault(var, set()).add(what)
    return {label: {var: next(iter(ws)) for var, ws in by.items() if len(ws) == 1 and "" not in ws}
            for label, by in found.items()}


# A counter after the ＊, or a word for a level or a number before it.
# The short ones in hiragana only where the word ends there (＊こそ is not a count).
NUMBER_AFTER = re.compile(r"コ|まい|ひき|びき|ぴき|にん|円|ターン|ポイント|％|しゅるい|ｃｍ|ふん|びょう"
                          r"|(?:こ|かい|ばん|たい|ほ|つ)(?![ぁ-ゖ])")
NUMBER_BEFORE = re.compile(r"(?:Ｌｖ|レベル|Ｎｏ．)$")
UNKNOWN = "a name or number the game fills in"


def kinds(text: str, codes: list[str], label: str = "") -> list[str | None]:
    """What each ＊ of the sentence is, in order; None where it cannot be told."""
    out, at, scripted = [], -1, from_scripts().get(label, {})
    for code in codes:
        at = text.index(VAR, at + 1)
        if code in BATTLE:
            out.append(BATTLE[code])
        elif code in scripted:
            out.append(scripted[code])
        elif NUMBER_AFTER.match(text, at + 1) or NUMBER_BEFORE.search(text[:at]):
            out.append("a number")
        else:
            out.append(None)
    return out


def hint(text: str, codes: str | list[str], label: str = "") -> str:
    """The card's line: "＊ = the Pokémon making the move", or the ＊ in order."""
    listed: list[str] = json.loads(codes) if isinstance(codes, str) else list(codes)
    if not listed or text.count(VAR) != len(listed):
        return ""
    what = kinds(text, listed, label)
    if not any(what):
        return ""
    if len(what) == 1:
        return f"{VAR} = {what[0]}"
    return f"{VAR} in order: " + " · ".join(w or UNKNOWN for w in what)
