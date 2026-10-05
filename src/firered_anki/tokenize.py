"""Stage 3: candidate words and dictionary entries for each unique sentence.

fugashi + UniDic splits the sentence (a hint: kana-only text makes it guess).
JMdict (jamdict) then gives every entry each word could be. Kana-only text is
often split wrong (てんそう → て+ん+そう), so besides single tokens we look up
runs of up to 4 adjacent tokens. The game's spaces mark phrase boundaries, but
it also puts one inside some words (とおり　すがり, しのび　こむ), so a run may
cross one space, never two. Such a run has to be four characters or longer:
the shorter ones that match an entry do so by chance (に　お is not 鳰).

Particles, sentence endings and the copula get candidates too: the entries
written in kana for that reading, with all their senses. A particle's jobs
are its senses (か asks a question, or means "or"), and the analysis can only
say which one it is doing if it is shown them. Runs of such words are looked
up as well, so that かな and のに are offered as the single words they are.

Writes one row per unique sentence, plus a table of the JMdict entries used.
A row also lists the strings that have an entry only when read across a space
(`across`): the splits stage asks about the ones the analysis cuts there.
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
MIN_ACROSS = 4       # characters in a run that crosses a space
MAX_PER_KEY = 6      # entries kept per lookup key, common words first
MAX_SENSES = 5
MAX_GLOSSES = 3
NO_LOOKUP = {"助詞", "助動詞", "補助記号", "空白", "記号"}
INFLECTS = {"動詞", "形容詞", "助動詞"}
KANA = re.compile(r"^[ぁ-ゟ゠-ヿー]+$")
SPACE = {"空白"}
GRAMMAR = re.compile(r"^[\u3041-\u309f]{1,2}$")  # の, が, から, です…
GRAMMAR_POS = {"助詞", "助動詞"}
MAX_GRAMMAR_RUN = 3  # かな, のに, ではない…

_tagger = fugashi.Tagger()
_jam = Jamdict()
_entries: dict[int, dict] = {}


def hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s or "")


def lemma_of(w) -> str:
    # UniDic lemmas carry a gloss for loanwords: ボックス-box
    return (w.feature.lemma or w.surface).split("-")[0]


def compact(e, max_senses: int | None = MAX_SENSES) -> dict:
    senses = []
    for s in e.senses[:max_senses]:
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


@lru_cache(maxsize=None)
def kana_entries(reading: str) -> tuple[int, ...]:
    """Entries read this way that are written in kana: the particle か, not 蚊.
    They are kept with every sense, not the first five (に has nine)."""
    ids = []
    for e in _jam.lookup(reading, strict_lookup=True, lookup_chars=False).entries:
        in_kana = any("usually written using kana" in m for s in e.senses for m in s.misc)
        if not e.kanji_forms or in_kana:
            eid = int(e.idseq)
            _entries[eid] = compact(e, max_senses=None)
            ids.append(eid)
    return tuple(ids)


def tokenize(text: str) -> tuple[list[dict], dict[str, list[int]], list[str]]:
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

    def add_grammar(key: str) -> None:
        if "＊" not in key and (ids := kana_entries(hira(key))):
            cands[key] = list(dict.fromkeys(cands.get(key, []) + list(ids)))

    def add_run(run: list[dict]) -> bool:
        """Look the run up, as written and with its last token in dictionary
        form. → whether either is an entry."""
        if all(x["p"] in NO_LOOKUP for x in run) or any("＊" in x["s"] for x in run):
            return False
        keys = ["".join(x["s"] for x in run)]
        if run[-1]["p"] in INFLECTS and run[-1]["lr"]:
            keys.append("".join(x["s"] for x in run[:-1]) + run[-1]["lr"])
        for key in keys:
            add(key)
        return any(key in cands for key in keys)

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
            if GRAMMAR.match(hira(t["s"])):
                add_grammar(t["s"])
            for j in range(i + 2, min(i + MAX_GRAMMAR_RUN, len(chunk)) + 1):
                if all(x["p"] in GRAMMAR_POS for x in chunk[i:j]):
                    add_grammar("".join(x["s"] for x in chunk[i:j]))
            for j in range(i + 2, min(i + MAX_RUN, len(chunk)) + 1):
                add_run(chunk[i:j])

    # A word written with a space inside: the end of one chunk and the start
    # of the next, looked up as one string.
    across = []
    for a, b in zip(chunks, chunks[1:]):
        for m in range(1, min(MAX_RUN - 1, len(a)) + 1):
            for n in range(1, min(MAX_RUN - m, len(b)) + 1):
                run = a[-m:] + b[:n]
                if sum(len(x["s"]) for x in run) >= MIN_ACROSS and add_run(run):
                    across.append("".join(x["s"] for x in run))
    return tokens, cands, across


def main() -> None:
    df = pd.read_parquet(ORDER_OUT)
    texts = df.drop_duplicates("text")["text"].tolist()
    rows = []
    for n, text in enumerate(texts, 1):
        tokens, cands, across = tokenize(text)
        rows.append({"text": text, "tokens": json.dumps(tokens, ensure_ascii=False),
                     "candidates": json.dumps(cands, ensure_ascii=False),
                     "across": json.dumps(across, ensure_ascii=False)})
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
