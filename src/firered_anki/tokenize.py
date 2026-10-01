"""Stage 3: candidate words and dictionary entries for each unique sentence.

fugashi + UniDic splits the sentence (a hint: kana-only text makes it guess).
JMdict (jamdict) then gives every entry each word could be. Kana-only text is
often split wrong (てんそう → て+ん+そう), so besides single tokens we look up
runs of up to 4 adjacent tokens. Runs never cross the game's spaces, which mark
phrase boundaries.

Writes one row per unique sentence, plus a table of the JMdict entries used.
Run: uv run python -m firered_anki.tokenize
"""

import json
import re
from functools import lru_cache

import fugashi
import pandas as pd
from jamdict import Jamdict

from .order import ORDER_OUT
from .paths import DATA, ROOT

TOKENIZE_OUT = DATA / "03_tokenize" / "sentences.parquet"
ENTRIES_OUT = DATA / "03_tokenize" / "entries.json"

MAX_RUN = 4          # tokens per looked-up run
MAX_PER_KEY = 6      # entries kept per lookup key, common words first
MAX_SENSES = 5
MAX_GLOSSES = 3
NO_LOOKUP = {"助詞", "助動詞", "補助記号", "空白", "記号"}
INFLECTS = {"動詞", "形容詞", "助動詞"}
KANA = re.compile(r"^[ぁ-ゟ゠-ヿー]+$")
SPACE = {"空白"}

_tagger = fugashi.Tagger()
_jam = Jamdict()
_entries: dict[int, dict] = {}


def hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s or "")


def lemma_of(w) -> str:
    # UniDic lemmas carry a gloss for loanwords: ボックス-box
    return (w.feature.lemma or w.surface).split("-")[0]


def compact(e) -> dict:
    senses = []
    for s in e.senses[:MAX_SENSES]:
        sense = {
            "pos": [p.split(" (")[0] for p in s.pos][:2],
            "g": [g.text for g in s.gloss][:MAX_GLOSSES],
        }
        if "onomatopoeic or mimetic word" in s.misc:
            sense["on"] = True  # ドキドキ, キラキラ…
        senses.append(sense)
    return {
        "k": [k.text for k in e.kanji_forms][:3],
        "r": [k.text for k in e.kana_forms][:3],
        "common": any(k.pri for k in list(e.kanji_forms) + list(e.kana_forms)),
        "s": senses,
    }


@lru_cache(maxsize=None)
def lookup(key: str) -> tuple[int, ...]:
    if not key or len(key) < 2 and KANA.match(key):
        return ()
    res = _jam.lookup(key, strict_lookup=True, lookup_chars=False)
    ids = []
    for e in res.entries:
        eid = int(e.idseq)
        if eid not in _entries:
            _entries[eid] = compact(e)
        ids.append(eid)
    ids.sort(key=lambda i: not _entries[i]["common"])
    return tuple(ids[:MAX_PER_KEY])


def tokenize(text: str) -> tuple[list[dict], dict[str, list[int]]]:
    words = list(_tagger(text))
    tokens = [
        {
            "s": w.surface,
            "l": lemma_of(w),
            "p": w.feature.pos1,
            "r": hira(w.feature.kana or ""),
            "lr": hira(w.feature.lForm or ""),
        }
        for w in words
    ]
    cands: dict[str, list[int]] = {}

    def add(key: str) -> None:
        if key and key not in cands and (ids := lookup(key)):
            cands[key] = list(ids)

    # Chunks between the game's spaces.
    chunks, cur = [], []
    for t in tokens:
        if t["p"] in SPACE:
            chunks.append(cur)
            cur = []
        else:
            cur.append(t)
    chunks.append(cur)

    for chunk in chunks:
        for i, t in enumerate(chunk):
            if t["p"] not in NO_LOOKUP and "＊" not in t["s"]:
                add(t["l"])
                add(t["lr"])
                add(t["s"])
            for j in range(i + 2, min(i + MAX_RUN, len(chunk)) + 1):
                run = chunk[i:j]
                if all(x["p"] in NO_LOOKUP for x in run) or any("＊" in x["s"] for x in run):
                    continue
                add("".join(x["s"] for x in run))
                if run[-1]["p"] in INFLECTS and run[-1]["lr"]:
                    add("".join(x["s"] for x in run[:-1]) + run[-1]["lr"])
    return tokens, cands


def main() -> None:
    df = pd.read_parquet(ORDER_OUT)
    texts = df.drop_duplicates("text")["text"].tolist()
    rows = []
    for n, text in enumerate(texts, 1):
        tokens, cands = tokenize(text)
        rows.append({"text": text, "tokens": json.dumps(tokens, ensure_ascii=False),
                     "candidates": json.dumps(cands, ensure_ascii=False)})
        if n % 2000 == 0:
            print(f"  {n}/{len(texts)}")
    out = pd.DataFrame(rows)
    TOKENIZE_OUT.parent.mkdir(parents=True, exist_ok=True)
    out.to_parquet(TOKENIZE_OUT, index=False)
    ENTRIES_OUT.write_text(json.dumps({str(k): v for k, v in _entries.items()}, ensure_ascii=False))

    n_c = out["candidates"].map(lambda c: len({i for ids in json.loads(c).values() for i in ids}))
    print(f"sentences {len(out)}, JMdict entries used {len(_entries)}")
    print(f"candidate entries per sentence: median {n_c.median():.0f}, p90 {n_c.quantile(.9):.0f}, max {n_c.max()}")
    print(f"→ {TOKENIZE_OUT.relative_to(ROOT)}, {ENTRIES_OUT.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
