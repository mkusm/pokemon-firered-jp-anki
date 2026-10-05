"""Stage 1: PokéCorpus FRLG ja-Hrkt → cleaned sentences.

One row per sentence, keyed by message ID. The raw message is kept on every row.
Run: uv run python -m firered_anki.extract
"""

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
# "オーキド『…" names the speaker. Help steps like "①『たたかう』" do not.
SPEAKER = re.compile(r"^([぀-ヿ＊][^　 『』「」。！？…]{0,11})『")
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
    text = PLACEHOLDER.sub(lambda m: replace_placeholder(m, unknown), raw)
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
                rows.append({**c, "sent": sent_no, "text": sent})
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


def hand_lines() -> pd.DataFrame:
    """hand_lines.yaml as corpus rows: namespace `hand`, no line number."""
    listed = yaml.safe_load(HAND_LINES.read_text(encoding="utf-8")) if HAND_LINES.exists() else {}
    rows = [{"line_no": -1, "msg_id": f"frlg.hand.{key}", "raw": text, "ns": "hand",
             "group": key.split(".", 1)[0], "label": key.split(".", 1)[1]} for key, text in (listed or {}).items()]
    return pd.DataFrame(rows, columns=["line_no", "msg_id", "raw", "ns", "group", "label"])


def main() -> None:
    df = join_rows(load_corpus())
    n_all = len(df)
    df = df[~df["raw"].isin(["", "[NULL]"])]
    n_text = len(df)
    # FireRed only: the LeafGreen Pokédex, and LeafGreen's in-game trades
    # (labels ending ^LG; FireRed's end ^FR).
    df = df[(df["group"] != "pokedex_text_lg") & ~df["label"].str.endswith("^LG")]
    by_hand = hand_lines()
    df = pd.concat([df, by_hand], ignore_index=True)

    unknown: Counter = Counter()
    rows = []
    for r in df.itertuples(index=False):
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
                    "raw": r.raw,
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
    print(f"→ {EXTRACT_OUT.relative_to(EXTRACT_OUT.parents[2])}")


if __name__ == "__main__":
    main()
