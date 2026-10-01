"""Stage 4: LLM analysis of every unique sentence, in context.

Batches are consecutive sentences from one map (or one non-dialogue group),
cut only at message boundaries, so the model sees whole conversations. Each
call returns every word of every sentence, disambiguated by context and tied
to a JMdict entry and sense from the tokenize stage's candidates.

Results are cached per sentence (by text hash) under a run name; a cached
sentence is never sent again.

  uv run python -m firered_anki.analyse dry-run   # compare batch sizes and models
"""

import hashlib
import json
import re
import sys
import time
from concurrent.futures import CancelledError, ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import pandas as pd

from . import claude_cli, sense_pick
from .order import ORDER_OUT
from .paths import DATA, ROOT
from .grounding import ground
from .tokenize import ENTRIES_OUT, TOKENIZE_OUT

CACHE = DATA / "cache" / "analyse"
DRYRUN_OUT = DATA / "dryrun"
PROMPT_VERSION = "2"

SYSTEM = """You analyse Japanese sentences from Pokémon FireRed (GBA, Japanese version) for a learner's Anki deck.

The game text is almost all kana, with spaces between phrases (not between words), so the same kana can be different words: かみ may be 紙, 神 or 髪. Decide every word's meaning from the whole sentence, the surrounding sentences, the speaker and the game context (a Pokémon RPG).

For each sentence, return:
- kanji: the sentence as a native would write it with normal kanji, keeping the game's wording
- english: a natural English translation
- words: every word in order, including particles. Attach conjugation endings and auxiliaries to their word (行っちゃった is one word), but particles (は, が, を, に, で, と, も, の, から, より…) are always separate words, also after nouns like こと or もの: ことが is こと + が. Skip ＊ (a runtime name) and punctuation.

For each word:
- surface: exactly as it appears in the sentence (a substring of it, without spaces)
- base: dictionary form, in kana
- kanji: kanji spelling of the base form, or "" if none; usually_kana: true if it is normally written in kana
- char_readings: reading of each kanji in `kanji`, like "研 けん · 究 きゅう · 所 じょ"; "" if no kanji
- literal: literal meaning, a few words
- in_context: what it means in this sentence, a few words
- modifiers: if not in base form, each ending explained, like "行っ (te-form) + ちゃ (= てしまう, contraction) + った (past)"; "" if base form
- jmdict_id and sense: the JMdict entry and 1-based sense number that fit this sentence. Prefer the candidates given; use null for both if none fits
- proper: true for names of Pokémon, people, places, moves, items and teams
- low_confidence: true if you are unsure of the reading, meaning or entry

Keep literal and in_context short. Reply with JSON only."""


WORD = {
    "type": "object",
    "properties": {
        "surface": {"type": "string"}, "base": {"type": "string"},
        "kanji": {"type": "string"}, "usually_kana": {"type": "boolean"},
        "char_readings": {"type": "string"}, "literal": {"type": "string"},
        "in_context": {"type": "string"}, "modifiers": {"type": "string"},
        "jmdict_id": {"type": ["integer", "null"]}, "sense": {"type": ["integer", "null"]},
        "proper": {"type": "boolean"}, "low_confidence": {"type": "boolean"},
    },
    "required": ["surface", "base", "kanji", "usually_kana", "char_readings", "literal",
                 "in_context", "modifiers", "jmdict_id", "sense", "proper", "low_confidence"],
}
SCHEMA = {
    "type": "object",
    "properties": {"sentences": {"type": "array", "items": {
        "type": "object",
        "properties": {
            "id": {"type": "string"}, "kanji": {"type": "string"},
            "english": {"type": "string"}, "words": {"type": "array", "items": WORD},
        },
        "required": ["id", "kanji", "english", "words"],
    }}},
    "required": ["sentences"],
}


@dataclass(frozen=True)
class Config:
    name: str
    model: str
    effort: str
    batch: int


def sent_key(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


def cache_path(run: str, text: str):
    return CACHE / run / f"{sent_key(text)}.json"


# --- inputs ------------------------------------------------------------------

def load() -> tuple[pd.DataFrame, dict]:
    """Deck rows in order with tokenizer output, and the JMdict entry table."""
    df = pd.read_parquet(ORDER_OUT)
    tok = pd.read_parquet(TOKENIZE_OUT).set_index("text")
    df = df.join(tok, on="text")
    # Batch key: the map for dialogue, the group for everything else.
    df["batch_key"] = df["first_seen"].where(df["dialogue"], df["group"])
    entries = json.loads(ENTRIES_OUT.read_text(encoding="utf-8"))
    return df, entries


def make_batches(rows: pd.DataFrame, target: int) -> list[pd.DataFrame]:
    """Unique sentences, grouped by batch key in first-seen order, cut only
    between messages once a batch holds `target` sentences."""
    rows = rows.sort_values("first_seen_order").drop_duplicates("text")
    # Small maps run into the next one: every message carries its own map
    # header in the prompt, so a batch may span maps but never splits a message.
    batches = []
    cur: list[pd.DataFrame] = []
    size = 0
    for _, grp in rows.groupby("batch_key", sort=False):
        for _, msg in grp.groupby("msg_id", sort=False):
            if cur and size + len(msg) > target:
                batches.append(pd.concat(cur))
                cur, size = [], 0
            cur.append(msg)
            size += len(msg)
    if cur:
        batches.append(pd.concat(cur))
    return batches


def entry_line(eid: str, e: dict) -> str:
    head = "・".join(e["k"]) + "【" + "・".join(e["r"]) + "】" if e["k"] else "・".join(e["r"])
    senses = " ".join(
        f"{i}) {'; '.join(s['g'])}" + (f" [{', '.join(s['pos'])}]" if s["pos"] else "")
        for i, s in enumerate(e["s"], 1)
    )
    return f"{eid}: {head} {senses}"


def prompt(batch: pd.DataFrame, entries: dict) -> str:
    out, used = [], {}
    last_msg = None
    for i, r in enumerate(batch.itertuples()):
        if r.msg_id != last_msg:
            where = r.first_seen if r.dialogue else f"{r.group} (non-dialogue: menus, battle or item text)"
            spk = f", speaker: {r.speaker}" if r.speaker else ""
            out.append(f"\n## message {r.msg_id.split('.', 2)[-1]} — {where}{spk}")
            last_msg = r.msg_id
        toks = json.loads(r.tokens)
        split = " ".join(t["s"] if t["l"] == t["s"] else f"{t['s']}({t['l']})"
                         for t in toks if t["p"] != "空白")
        cands = json.loads(r.candidates)
        for ids in cands.values():
            for eid in ids:
                used[str(eid)] = True
        cand_txt = "; ".join(f"{k}→{','.join(map(str, v))}" for k, v in cands.items())
        out.append(f"s{i}: {r.text}\n    tokenizer: {split}\n    candidates: {cand_txt or '-'}")
    head = ["JMdict entries (id: kanji【reading】 senses):"]
    head += [entry_line(eid, entries[eid]) for eid in used]
    return "\n".join(head) + "\n\nSentences, in game order:" + "\n".join(out) + (
        "\n\nReturn one item per sentence, with its id (s0, s1, …).")


# --- running -----------------------------------------------------------------

def check(batch: pd.DataFrame, result: dict, entries: dict) -> list[dict]:
    """Per-sentence records with validation flags."""
    got = {s["id"]: s for s in result.get("sentences", [])}
    recs = []
    for i, r in enumerate(batch.itertuples()):
        s = got.get(f"s{i}")
        if s is None:
            recs.append({"text": r.text, "missing": True})
            continue
        cand_ids = {i for ids in json.loads(r.candidates).values() for i in ids}
        text_nospace = r.text.replace("　", "")
        for w in s["words"]:
            jid = w.get("jmdict_id")
            w["_not_in_text"] = w["surface"] not in text_nospace
            w["_off_list"] = jid is not None and jid not in cand_ids
            w["_unknown_id"] = jid is not None and str(jid) not in entries
            w["_bad_sense"] = (
                jid is not None and str(jid) in entries
                and not (1 <= (w.get("sense") or 0) <= len(entries[str(jid)]["s"]))
            )
        recs.append({"text": r.text, "missing": False, **s})
    return recs


def cached_model(run: str, text: str) -> str | None:
    p = cache_path(run, text)
    return json.loads(p.read_text())["_run"]["model"] if p.exists() else None


def run(cfg: Config, rows: pd.DataFrame, entries: dict, workers: int = 3,
        redo: bool = False) -> dict:
    """Analyse rows under cfg, using and filling the cache. → stats.
    redo: also re-analyse cached sentences unless this model already did them
    (the escalation pass overwrites Sonnet's answers with Opus's)."""
    run_dir = CACHE / cfg.name
    run_dir.mkdir(parents=True, exist_ok=True)
    done = (lambda t: cached_model(cfg.name, t) == cfg.model) if redo else (
        lambda t: cache_path(cfg.name, t).exists())
    todo = rows[~rows["text"].map(done)]
    batches = make_batches(todo, cfg.batch)
    stats = {"calls": 0, "failed_calls": 0, "ms": 0,
             "limited": False, "todo": todo["text"].nunique()}
    print(f"[{cfg.name}] {rows['text'].nunique()} sentences, {todo['text'].nunique()} to do, {len(batches)} calls")

    def one(b):
        res, info = claude_cli.call(prompt(b, entries), SYSTEM, SCHEMA, cfg.model, cfg.effort)
        return b, res, info

    with ThreadPoolExecutor(workers) as pool:
        futs = [pool.submit(one, b) for b in batches]
        for fut in as_completed(futs):
            stats["calls"] += 1
            try:
                b, res, info = fut.result()
            except CancelledError:
                continue
            except claude_cli.UsageLimit:
                # Stop sending, but keep what the calls still running bring back.
                if not stats["limited"]:
                    print(f"[{cfg.name}] usage limit: stopping; re-run to resume from the cache")
                    stats["limited"] = True
                    for f in futs:
                        f.cancel()
                continue
            except Exception as e:  # schema failure, timeout, CLI error
                stats["failed_calls"] += 1
                print(f"[{cfg.name}] call failed: {str(e)[:200]}")
                continue
            stats["ms"] += info["ms"]
            for rec in check(b, res, entries):
                if not rec["missing"]:
                    rec["_run"] = {"model": cfg.model, "effort": cfg.effort, "batch": len(b),
                                   "prompt": PROMPT_VERSION}
                    cache_path(cfg.name, rec["text"]).write_text(json.dumps(rec, ensure_ascii=False))
            print(f"[{cfg.name}] {stats['calls']}/{len(batches)} calls, {len(b)} sentences, {info['ms'] / 1000:.0f}s")
    return stats


def results(run_name: str, texts, entries: dict) -> dict[str, dict]:
    """Cached analyses, with JMdict grounding applied (the cache stays raw)."""
    out = {}
    for t in texts:
        p = cache_path(run_name, t)
        if p.exists():
            r = json.loads(p.read_text())
            for w in r["words"]:
                ground(w, entries)
                sense_pick.apply(t, w, entries)
            out[t] = r
    return out


def metrics(res: dict[str, dict], n_texts: int) -> dict:
    words = [w for r in res.values() for w in r["words"]]
    nw = max(len(words), 1)
    pct = lambda k: round(100 * sum(bool(w.get(k)) for w in words) / nw, 1)
    return {
        "answered": f"{len(res)}/{n_texts}",
        "words": len(words),
        "null id %": round(100 * sum(w["jmdict_id"] is None for w in words) / nw, 1),
        "filled by lookup %": round(100 * sum(w["_id_source"] == "lookup" for w in words) / nw, 1),
        "form mismatch %": pct("_form_mismatch"),
        "off-list id %": pct("_off_list"),
        "unknown id %": pct("_unknown_id"),
        "bad sense %": pct("_bad_sense"),
        "surface not in text %": pct("_not_in_text"),
        "low-confidence %": pct("low_confidence"),
    }


# --- full run ----------------------------------------------------------------

MAIN = Config("main", "sonnet", "low", 40)
LIMIT_WAIT_S = 30 * 60


def full() -> None:
    """Every unique sentence, in deck order. Waits out usage limits and retries
    failed batches until everything is cached."""
    df, entries = load()
    stalled = 0
    while True:
        st = run(MAIN, df, entries)
        if st["todo"] == 0:
            print(f"[{MAIN.name}] done: every sentence is cached")
            return
        if st["limited"]:
            print(f"[{MAIN.name}] waiting {LIMIT_WAIT_S // 60} min for the usage limit", flush=True)
            time.sleep(LIMIT_WAIT_S)
            continue
        # Only failed batches are left; retry, but not forever.
        stalled = stalled + 1 if st["calls"] == st["failed_calls"] else 0
        if stalled >= 3:
            sys.exit(f"[{MAIN.name}] giving up: 3 rounds with no successful call")


ESCALATE = Config("main", "opus", "low", 30)  # writes over the main cache
# An off-list ID is not a problem by itself (Sonnet often knows the right entry
# when the tokenizer's candidates lack it); the form check catches wrong ones.
FLAGS = ("low_confidence", "_form_mismatch", "_unknown_id", "_bad_sense")


def needs_escalation(rec: dict) -> bool:
    """Names go to their own deck and need no second opinion."""
    return any(w.get(f) for w in rec["words"] if not w.get("proper") for f in FLAGS)


def escalate() -> None:
    """Re-run every flagged sentence on Opus at low effort."""
    df, entries = load()
    res = results(MAIN.name, df["text"].unique(), entries)
    flagged = {t for t, r in res.items() if needs_escalation(r) and r["_run"]["model"] != ESCALATE.model}
    # Only the flagged sentences; the prompt still gives each its map and speaker.
    rows = df[df["text"].isin(flagged)]
    print(f"[escalate] {len(flagged)} flagged sentences in {rows['msg_id'].nunique()} messages")
    while True:
        st = run(ESCALATE, rows, entries, redo=True)
        if st["todo"] == 0:
            print("[escalate] done")
            return
        if st["limited"]:
            print(f"[escalate] waiting {LIMIT_WAIT_S // 60} min for the usage limit", flush=True)
            time.sleep(LIMIT_WAIT_S)
        elif st["calls"] == st["failed_calls"]:
            sys.exit("[escalate] giving up: a round with no successful call")


RERUN = Config("main", "opus", "low", 40)  # writes over the main cache


def rerun(chapter: int | None = None, workers: int = 10) -> None:
    """Redo on Opus the sentences only Sonnet has answered: every one, or the
    ones the deck shows in one chapter. Sonnet is wrong without flagging it in
    about 3 sentences in 100 (a wrong item name, あったら filed under ある for
    合う), which escalation cannot catch."""
    df, entries = load()
    if chapter is not None:
        from .cards import prepare  # late: cards imports this module

        deck = prepare(offline_merge=True)[0]
        shown = deck[(deck["chapter"] == chapter) & deck["card_order"].notna()]
        df = df[df["text"].isin(set(shown["text"]))]
    rows = df[df["text"].map(lambda t: cached_model(MAIN.name, t) not in (None, RERUN.model))]
    print(f"[rerun] {rows['text'].nunique()} sentences to redo on {RERUN.model}, {workers} calls at a time")
    while True:
        st = run(RERUN, rows, entries, workers=workers, redo=True)
        print(f"[rerun] round: {st['calls']} calls ({st['failed_calls']} failed)")
        if st["todo"] == 0:
            print("[rerun] done")
            return
        if st["limited"]:
            print(f"[rerun] waiting {LIMIT_WAIT_S // 60} min for the usage limit", flush=True)
            time.sleep(LIMIT_WAIT_S)
        elif st["calls"] == st["failed_calls"]:
            sys.exit("[rerun] giving up: a round with no successful call")


# --- dry run -----------------------------------------------------------------

def dry_plan(df: pd.DataFrame) -> list[tuple[Config, pd.DataFrame]]:
    first = df[df["teaches"]].drop_duplicates("text").head(200)
    first100 = first.head(100)
    return [
        (Config("dry-sonnet-low-b20", "sonnet", "low", 20), first),
        (Config("dry-sonnet-low-b30", "sonnet", "low", 30), first),
        (Config("dry-sonnet-low-b40", "sonnet", "low", 40), first),
        (Config("dry-sonnet-medium-b30", "sonnet", "medium", 30), first100),
        (Config("dry-opus-low-b30", "opus", "low", 30), first100),
    ]


def dry_run(only: str | None = None) -> None:
    """All dry-run configs and the report; with `only`, just fill that config's cache."""
    df, entries = load()
    plan = dry_plan(df)
    if only:
        cfg, rows = next((c, r) for c, r in plan if c.name == only)
        run(cfg, rows, entries)
        return
    first100 = plan[-1][1]
    DRYRUN_OUT.mkdir(parents=True, exist_ok=True)
    table = []
    for cfg, rows in plan:
        t0 = time.time()
        st = run(cfg, rows, entries)
        res = results(cfg.name, rows["text"].unique(), entries)
        m = metrics(res, rows["text"].nunique())
        table.append({
            "run": cfg.name, **m,
            "failed calls": f"{st['failed_calls']}/{st['calls']}",
            "wall min": round((time.time() - t0) / 60, 1),
        })
    rep = pd.DataFrame(table)
    (DRYRUN_OUT / "metrics.csv").write_text(rep.to_csv(index=False))
    print(rep.to_string(index=False))
    write_comparison([c for c, _ in plan], first100["text"].unique(), entries)


def write_comparison(cfgs: list[Config], texts, entries: dict) -> None:
    """Side-by-side word analyses of the same sentences, for spot-checking."""
    runs = {c.name: results(c.name, texts, entries) for c in cfgs}
    lines = ["# Dry run: same sentences across runs\n"]
    for t in list(texts)[:40]:
        lines.append(f"\n## {t}\n")
        for name, res in runs.items():
            r = res.get(t)
            if not r:
                lines.append(f"- **{name}**: (missing)")
                continue
            ws = " / ".join(
                f"{w['surface']}={w['kanji'] or w['base']}"
                + (f"#{w['jmdict_id']}.{w['sense']}" if w["jmdict_id"] else "")
                + ("?" if w["low_confidence"] else "")
                for w in r["words"]
            )
            lines.append(f"- **{name}**: {r['kanji']} — {r['english']}\n  {ws}")
    (DRYRUN_OUT / "comparison.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"→ {(DRYRUN_OUT / 'comparison.md').relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)  # progress shows up in redirected logs
    args = sys.argv[1:]
    if args[:1] == ["dry-run"] and len(args) <= 2:
        dry_run(args[1] if len(args) == 2 else None)
    elif args == ["full"]:
        full()
    elif args == ["escalate"]:
        escalate()
    elif args[:1] == ["rerun"]:
        rerun(int(args[1]) if len(args) > 1 else None)
    else:
        sys.exit("usage: python -m firered_anki.analyse full | escalate | rerun [chapter] | dry-run [config-name]")
