"""Stage 1: PokéCorpus FRLG ja-Hrkt → cleaned sentences.

One row per sentence, keyed by message ID. The raw message is kept on every row.
Run: uv run python -m firered_anki.extract
"""

import json
import re
from collections import Counter

import pandas as pd

import yaml

from .paths import CORPUS, EXTRACT_OUT, ROOT

# Text the game draws as a picture, so the corpus has no line for it.
HAND_LINES = ROOT / "hand_lines.yaml"

PLAYER = "レッド"
RIVAL = "グリーン"
# Stands in for any runtime-filled name or number. UniDic tags it as a symbol,
# so it never becomes a card.
VAR = "＊"

# Placeholders replaced by fixed text. Anything not matched here or by the
# patterns below is an error, so new codes can't slip through unnoticed.
FIXED = {
    "PLAYER": PLAYER,
    "B_PLAYER_NAME": PLAYER,
    "RIVAL": RIVAL,
    "KUN": "くん",
    "A_BUTTON": "Ａ",
    "B_BUTTON": "Ｂ",
    "START_BUTTON": "ＳＴＡＲＴ",
    "PLUS": "＋",
    "CIRCLE_DOT": "◎",
    "TRIANGLE": "△",
    "PP": "ＰＰ",
    "ID": "ＩＤ",
    "NO": "Ｎｏ．",
    "ROUND_LEFT_PAREN": "（",
    "ROUND_RIGHT_PAREN": "）",
    "RIGHT_ARROW": "→",
    "UNDERSCORE": "＿",
}
CIRCLED = "①②③④⑤⑥⑦⑧⑨"

# Runtime variables: names, numbers, buffers.
VAR_RE = re.compile(
    r"STR_VAR_\d|DYNAMIC \d+|B_[A-Z0-9_]+"
)
# Formatting, sound and pacing codes: removed.
DROP_RE = re.compile(
    r"(COLOR|SHADOW|HIGHLIGHT|PALETTE|COLOR_HIGHLIGHT_SHADOW) .*"
    r"|FONT_[A-Z]+|PAUSE \d+|PAUSE_UNTIL_PRESS|PAUSE_MUSIC|RESUME_MUSIC"
    r"|PLAY_BGM .*|PLAY_SE .*|WAIT_SE|DPAD_[A-Z]+|EMOJI_[A-Z_]+"
)

PLACEHOLDER = re.compile(r"\[([^\]]*)\]")
# While a message is cut into sentences, each runtime variable is one
# private-use character that says which it is: the n-th is MARK + n. A
# sentence then knows which codes its ＊ stand for (`vars`), for the hint on
# the card (placeholders.py).
MARK = 0xE000
MARKS = re.compile(r"[\ue000-\uf8ff]")
# "オーキド『…" names the speaker. Help steps like "①『たたかう』" do not.
SPEAKER = re.compile(r"^([぀-ヿ＊\ue000-\uf8ff][^　 『』「」。！？…]{0,11})『")
# A sentence ends at a run of 。！？ plus any closing brackets after it.
SENTENCE = re.compile(r"[^。！？!?]+(?:[。！？!?]+[」』）)]*)?")
ENDS = re.compile(r"[。！？!?][」』）)]*$")
HAS_WORD = re.compile(r"[぀-ヿ一-鿿Ａ-Ｚａ-ｚA-Za-z]")


def replace_placeholder(m: re.Match, unknown: Counter) -> str:
    code = m.group(1)
    if code in FIXED:
        return FIXED[code]
    if m2 := re.fullmatch(r"CIRCLE_(\d)", code):
        return CIRCLED[int(m2.group(1)) - 1]
    if m2 := re.fullmatch(r"LV_\d|RIGHT_ARROW_\d", code):
        return "Ｌｖ" if code.startswith("LV") else "→"
    if VAR_RE.fullmatch(code):
        return VAR
    if DROP_RE.fullmatch(code):
        return ""
    unknown[code] += 1
    return m.group(0)


def split_message(raw: str, unknown: Counter) -> list[dict]:
    codes: list[str] = []

    def replace(m: re.Match) -> str:
        out = replace_placeholder(m, unknown)
        if out != VAR:
            return out
        codes.append(m.group(1))
        return chr(MARK + len(codes) - 1)

    text = PLACEHOLDER.sub(replace, raw)
    # \c clears the box: a new page. \n and \r are line breaks inside a page;
    # they fall on phrase boundaries, so they become a space like the game's own.
    # A page that doesn't end a sentence runs on into the next one, unless the
    # next page starts with a new speaker.
    chunks: list[dict] = []
    speaker = None
    for page_no, page in enumerate(text.split("\\c")):
        page = re.sub(r"\\[nre]", "　", page)
        page = re.sub(r"[　 ]+", "　", page).strip("　 ")
        new_speaker = SPEAKER.match(page)
        if new_speaker:
            speaker = new_speaker.group(1)
            page = page[new_speaker.end():]
        if not page:
            continue
        prev = chunks[-1] if chunks else None
        if prev and not new_speaker and not ENDS.search(prev["text"]):
            prev["text"] += "　" + page
        else:
            chunks.append({"page": page_no, "speaker": speaker, "text": page})

    rows = []
    for c in chunks:
        for sent_no, sent in enumerate(SENTENCE.findall(c["text"])):
            sent = sent.strip("　 ")
            if HAS_WORD.search(sent):
                rows.append({**c, "sent": sent_no, "text": MARKS.sub(VAR, sent),
                             "speaker": c["speaker"] and MARKS.sub(VAR, c["speaker"]),
                             "vars": [codes[ord(x) - MARK] for x in MARKS.findall(sent)]})
    return rows


def load_corpus() -> pd.DataFrame:
    ids = (CORPUS / "qid_msg.txt").read_text(encoding="utf-8").split("\n")
    ja = (CORPUS / "ja-Hrkt_msg.txt").read_text(encoding="utf-8").split("\n")
    if len(ids) != len(ja):
        raise ValueError(f"line count mismatch: {len(ids)} ids vs {len(ja)} ja")
    df = pd.DataFrame({"line_no": range(len(ids)), "msg_id": ids, "raw": ja})
    parts = df["msg_id"].str.split(".", n=3, expand=True)
    df["ns"], df["group"], df["label"] = parts[1], parts[2], parts[3]
    return df


# One label the dump has in two rows. The party menu's confirm button is
# けっ in the slot where the English game has "OK", and てい in the next,
# which is an empty string there. party_menu.c draws the one label, so the
# two rows are joined: けってい.
JOINED = {"frlg.common.strings.gText_PartyMenu_OK": ["frlg.common.strings.gText_Empty"]}


def join_rows(df: pd.DataFrame) -> pd.DataFrame:
    raw = df.set_index("msg_id")["raw"]
    for head, rest in JOINED.items():
        df.loc[df["msg_id"] == head, "raw"] = raw[head] + "".join(raw[m] for m in rest)
    return df[~df["msg_id"].isin({m for rest in JOINED.values() for m in rest})]


# A message the battle code puts together from rows of the dump. A stat
# change is shown through a frame, ＊の　＊が　＊: the Pokémon, the stat, and a
# last buffer that ChangeStatBuffs (battle_script_commands.c) fills with
# さがった！ or あがった！, with がくっと or ぐーんと before it when the stat moves two
# stages. The dump has the frame and the four pieces as rows of their own,
# and none of them is what a player reads. So the row of each piece becomes
# the whole message that piece is shown in: the frame, with the pieces listed
# here in its last buffer. One frame serves the Pokémon making the move and
# another the one it is aimed at, with the same text; the usual case is kept
# (a stat falls on the target and rises on the user), and the bare frames
# are dropped. A berry that raises a stat has a frame of its own
# (battle_util.c fills the same buffer), which is filled in place.
B = "frlg.common.battle_message."
FILLED = {
    B + "sText_StatFell": (B + "sText_DefendersStatFell", ["sText_StatFell"]),
    B + "sText_StatHarshly": (B + "sText_DefendersStatFell", ["sText_StatHarshly", "sText_StatFell"]),
    B + "gBattleText_Rose": (B + "sText_AttackersStatRose", ["gBattleText_Rose"]),
    B + "sText_StatSharply": (B + "sText_AttackersStatRose", ["sText_StatSharply", "gBattleText_Rose"]),
    B + "sText_UsingItemTheStatOfPkmnRose": (B + "sText_UsingItemTheStatOfPkmnRose", ["gBattleText_Rose"]),
}
FRAMES = [B + "sText_AttackersStatRose", B + "gText_DefendersStatRose",
          B + "sText_AttackersStatFell", B + "sText_DefendersStatFell"]
LAST_BUFFER = "[B_BUFF2]"


def fill_rows(df: pd.DataFrame) -> pd.DataFrame:
    raw = df.set_index("msg_id")["raw"]
    for row, (frame, pieces) in FILLED.items():
        if raw[frame].count(LAST_BUFFER) != 1:
            raise ValueError(f"{frame} has no single {LAST_BUFFER}: {raw[frame]}")
        df.loc[df["msg_id"] == row, "raw"] = raw[frame].replace(LAST_BUFFER, "".join(raw[B + p] for p in pieces))
    return df[~df["msg_id"].isin(FRAMES)]


# A word the game never shows alone, on a row of its own in the dump: a
# Pokédex category, a nature, a stat's name. The model has read each as the
# bare word, and the cards made from those answers must stay the cards they
# are. So the row's text becomes what the screen shows, and the row says
# what it was put together from (`parts`): the analysis is then put together
# the same way from the answers already cached (assembled.py), and the word
# is not asked about again.
#   たねポケモン: pokedex_screen.c (DexScreen_PrintMonCategory) prints the
#     category and gText_PokedexPokemon right after it.
#   まじめなせいかく　Ｌｖ＊のとき　＊で　であった: the summary screen's trainer
#     memo (pokemon_summary_screen.c) puts the nature into gText_PokeSum_Met;
#     the two natures that are い-adjectives take the frame without な.
#   ＊の　とくぼうが　さがった！: the stat messages above, whose other buffer is
#     a name from gStatNamesTable (battle_message.c). Each stat is shown the
#     way a move of the game changes it. たいりょく is in the table and no
#     move changes it: its row stays as it is.
#   ミニスカートの　＊が　しょうぶを　しかけてきた！: a trainer's class stands
#     before the name in the challenge (sText_Trainer1WantsToBattle, the
#     B_TRAINER1_CLASS buffer). The class's translation keeps its remark
#     there ("literally: shorts kid"): nothing else on the card says it.
S = "frlg.common.strings."
CATEGORIES, CATEGORY_TAIL = "pokedex_entries", S + "gText_PokedexPokemon"
NATURES, NATURE_SLOT = "nature_names", "DYNAMIC 0"
NATURE_FRAME, NATURE_FRAME_I = S + "gText_PokeSum_Met", S + "gText_PokeSum_Met_BoldGentleGrammar"
I_ADJECTIVES = {"sBoldNatureName", "sGentleNatureName"}  # PokeSum_IsMonBoldOrGentle
STAT_SLOT = "B_BUFF1"
CLASSES, CLASS_FRAME, CLASS_SLOT = "trainer_class_names", "sText_Trainer1WantsToBattle", "B_TRAINER1_CLASS"
STATS = {
    "sText_Attack2": "sText_StatFell", "sText_Defense2": "sText_StatFell", "sText_Speed": "sText_StatFell",
    "sText_SpAtk2": "gBattleText_Rose", "sText_SpDef2": "sText_StatFell", "sText_Accuracy": "sText_StatFell",
    "sText_Evasiveness": "gBattleText_Rose",
}


def build_rows(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, dict]]:
    """→ the rows with their text as shown, and msg_id → what it was built
    from: the frame's raw text (or None), the code of the slot, the pieces' raw texts."""
    raw = df.set_index("msg_id")["raw"]
    plan: dict[str, tuple] = {}
    for r in df.itertuples(index=False):
        if r.group == CATEGORIES:
            plan[r.msg_id] = (None, None, [r.msg_id, CATEGORY_TAIL])
        elif r.group == NATURES:
            plan[r.msg_id] = (NATURE_FRAME_I if r.label in I_ADJECTIVES else NATURE_FRAME, NATURE_SLOT, [r.msg_id])
        elif r.msg_id.startswith(B) and r.label in STATS:
            plan[r.msg_id] = (B + STATS[r.label], STAT_SLOT, [r.msg_id])
        elif r.group == CLASSES:
            plan[r.msg_id] = (B + CLASS_FRAME, CLASS_SLOT, [r.msg_id])
    built, new = {}, {}
    for row, (frame, code, pieces) in plan.items():
        fill = "".join(raw[p] for p in pieces)
        if frame and raw[frame].count(f"[{code}]") != 1:
            raise ValueError(f"{frame} has no single [{code}]: {raw[frame]}")
        new[row] = raw[frame].replace(f"[{code}]", fill) if frame else fill
        built[row] = {"frame": raw[frame] if frame else None, "code": code, "pieces": [raw[p] for p in pieces],
                      "remark": frame == B + CLASS_FRAME}
    df = df[df["msg_id"] != CATEGORY_TAIL].copy()
    df["raw"] = df["msg_id"].map(new).fillna(df["raw"])
    return df, built


def parts_of(built: dict, unknown: Counter) -> str:
    """A built row's parts as the sentences the model was given: the frame,
    which slot of it is filled (the n-th ＊, from 0), and the pieces."""
    def one(raw: str) -> dict:
        got = split_message(raw, unknown)
        if len(got) != 1:
            raise ValueError(f"a part must be one sentence: {raw}")
        return got[0]

    frame = one(built["frame"]) if built["frame"] else None
    return json.dumps({"frame": frame and frame["text"], "slot": frame and frame["vars"].index(built["code"]),
                       "pieces": [one(p)["text"] for p in built["pieces"]], "remark": built["remark"]}, ensure_ascii=False)


def hand_lines() -> pd.DataFrame:
    """hand_lines.yaml as corpus rows: namespace `hand`, no line number."""
    listed = yaml.safe_load(HAND_LINES.read_text(encoding="utf-8")) if HAND_LINES.exists() else {}
    rows = [{"line_no": -1, "msg_id": f"frlg.hand.{key}", "raw": text, "ns": "hand",
             "group": key.split(".", 1)[0], "label": key.split(".", 1)[1]} for key, text in (listed or {}).items()]
    return pd.DataFrame(rows, columns=["line_no", "msg_id", "raw", "ns", "group", "label"])


def main() -> None:
    corpus = load_corpus()
    n_all, n_text = len(corpus), int((~corpus["raw"].isin(["", "[NULL]"])).sum())
    df, built = build_rows(fill_rows(join_rows(corpus)))
    df = df[~df["raw"].isin(["", "[NULL]"])]
    # FireRed only: the LeafGreen Pokédex, and LeafGreen's in-game trades
    # (labels ending ^LG; FireRed's end ^FR).
    df = df[(df["group"] != "pokedex_text_lg") & ~df["label"].str.endswith("^LG")]
    by_hand = hand_lines()
    df = pd.concat([df, by_hand], ignore_index=True)

    unknown: Counter = Counter()
    rows = []
    for r in df.itertuples(index=False):
        parts = parts_of(built[r.msg_id], unknown) if r.msg_id in built else ""
        for s in split_message(r.raw, unknown):
            rows.append(
                {
                    "sent_id": f"{r.msg_id}#{s['page']}.{s['sent']}",
                    "msg_id": r.msg_id,
                    "line_no": r.line_no,
                    "ns": r.ns,
                    "group": r.group,
                    "label": r.label,
                    "page": s["page"],
                    "sent": s["sent"],
                    "speaker": s["speaker"],
                    "text": s["text"],
                    "vars": json.dumps(s["vars"]),
                    "raw": r.raw,
                    "parts": parts,
                }
            )
    if unknown:
        raise ValueError(f"unhandled placeholders: {dict(unknown)}")

    out = pd.DataFrame(rows)
    EXTRACT_OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(EXTRACT_OUT, index=False)

    print(f"corpus lines        {n_all}")
    print(f"with text           {n_text}")
    print(f"FireRed messages    {len(df)} ({len(by_hand)} written by hand)")
    print(f"sentences           {len(out)}")
    print(f"unique sentences    {out['text'].nunique()}")
    print(f"  from dialogue     {out.loc[out['ns'] == 'script', 'text'].nunique()}")
    print(f"with speaker        {out['speaker'].notna().sum()}")
    print(f"put together        {(out['parts'] != '').sum()} from words the dump has alone")
    print(f"→ {EXTRACT_OUT.relative_to(EXTRACT_OUT.parents[2])}")


if __name__ == "__main__":
    main()
