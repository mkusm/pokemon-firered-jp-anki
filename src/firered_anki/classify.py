"""Place catch-all rows with an LLM.

Rows that only a catch-all '.' rule in first_seen.yaml placed (generic UI
strings, Help pages, battle messages naming no move) go to Claude with their
label, Japanese and English text. It picks the story point where a normal
FireRed playthrough first shows each one, from first_seen.yaml `llm_points`.

Results are cached per message; re-running only asks about new or changed
rows. Writes first_seen_llm.yaml, which the order stage applies.
Run: uv run python -m firered_anki.classify [--dry-run]
"""

import hashlib
import json
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd
import yaml

from . import claude_cli
from .order import FALLBACK_OUT, FIRST_SEEN, LLM_OVERRIDES
from .paths import CORPUS, DATA, ROOT

MODEL = "sonnet"
EFFORT = "low"
BATCH = 40
WORKERS = 3
CACHE = DATA / "cache" / "classify"
VERSION = "3"  # bump to invalidate the cache after changing the prompt or points

SYSTEM = """You place strings from the Japanese Pokémon FireRed (GBA) into a normal single-player playthrough.

For each string, choose the story point where a typical player FIRST sees it on screen. Use the label (the programmer's name for the string), the English text and the Japanese text. Pick the earliest point where the feature, screen, item or battle effect it belongs to realistically appears; if it only appears in link play, trading, wireless or Mystery Gift, or never in a normal game, choose "end"; if it is debug or unused text, choose "exclude".

Battle messages: generic ones (attacks, damage, fainting, EXP, level up, sending out, running away, prize money) belong to the first battles. Status effects belong where status moves are first common. Weather, rarely used moves and double-battle messages belong later. Trainer-battle text (prize money, trainer sends out) belongs to Route22 or later, not the lab, unless it is the rival battle itself.

Help pages (group help_system) can be opened at any time, so never place them by when the menu is available: place each where the player first needs the topic it explains (cave help at the first cave, registering key items at the first key item, trainer card at the start).

Flavor text (group flavor_text) is shown when the player inspects an object such as a shelf, a TV, a machine or a sign. Place it where that kind of object is first found on the route: a kitchen in the player's house, a store shelf in the first Poké Mart, lab machines in Oak's lab, a named sign at the place it names.

Reply with JSON only."""


def points() -> dict[str, str]:
    return yaml.safe_load(FIRST_SEEN.read_text(encoding="utf-8"))["llm_points"]


def english() -> dict[str, str]:
    ids = (CORPUS / "qid_msg.txt").read_text(encoding="utf-8").split("\n")
    en = (CORPUS / "en_msg.txt").read_text(encoding="utf-8").split("\n")
    return dict(zip(ids, en))


def candidates() -> pd.DataFrame:
    df = pd.read_parquet(FALLBACK_OUT).sort_values(["line_no", "page", "sent"])
    msgs = df.groupby("msg_id", sort=False).agg(
        group=("group", "first"), label=("label", "first"), ja=("text", "　".join)
    )
    en = english()
    msgs["en"] = [en.get(m, "") for m in msgs.index]
    return msgs.reset_index()


def cache_key(row, pts: dict) -> str:
    h = hashlib.sha1()
    for part in (VERSION, MODEL, json.dumps(pts, sort_keys=True), row.msg_id, row.ja, row.en):
        h.update(part.encode())
        h.update(b"\0")
    return h.hexdigest()


def prompt(batch: pd.DataFrame, pts: dict) -> str:
    lines = ["Story points (id: what is first met there), in play order:"]
    lines += [f"- {k}: {v}" for k, v in pts.items()]
    lines.append("\nStrings:")
    for i, r in enumerate(batch.itertuples()):
        en = re.sub(r"\\[a-z]", " ", r.en)
        lines.append(f"{i}. group={r.group} label={r.label}\n   ja: {r.ja}\n   en: {en}")
    lines.append("\nReturn one item per string: its number and the chosen point id.")
    return "\n".join(lines)


def schema(pts: dict) -> dict:
    return {
        "type": "object",
        "properties": {"items": {"type": "array", "items": {
            "type": "object",
            "properties": {"n": {"type": "integer"}, "point": {"enum": list(pts)}},
            "required": ["n", "point"],
        }}},
        "required": ["items"],
    }


def ask(batch: pd.DataFrame, pts: dict) -> tuple[dict[int, str], float]:
    out, info = claude_cli.call(prompt(batch, pts), SYSTEM, schema(pts), MODEL, EFFORT, timeout=600)
    items = out.get("items", [])
    return {it["n"]: it["point"] for it in items if 0 <= it["n"] < len(batch)}, info["cost"]


def main() -> None:
    dry = "--dry-run" in sys.argv
    pts = points()
    msgs = candidates()
    msgs["key"] = [cache_key(r, pts) for r in msgs.itertuples()]
    CACHE.mkdir(parents=True, exist_ok=True)
    cached = {k: json.loads((CACHE / f"{k}.json").read_text()) for k in msgs["key"] if (CACHE / f"{k}.json").exists()}
    todo = msgs[~msgs["key"].isin(cached)]
    batches = [todo.iloc[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    print(f"{len(msgs)} rows, {len(cached)} cached, {len(todo)} to ask in {len(batches)} calls")
    if dry:
        if batches:
            print(prompt(batches[0].head(3), pts)[-1200:])
        return

    cost, missing = 0.0, 0
    try:
        with ThreadPoolExecutor(WORKERS) as pool:
            futs = {pool.submit(ask, b, pts): b for b in batches}
            for n, fut in enumerate(as_completed(futs), 1):
                b = futs[fut]
                got, c = fut.result()
                cost += c
                for i, r in enumerate(b.itertuples()):
                    if i in got:
                        (CACHE / f"{r.key}.json").write_text(json.dumps({"msg_id": r.msg_id, "point": got[i]}))
                        cached[r.key] = {"point": got[i]}
                    else:
                        missing += 1
                print(f"  call {n}/{len(batches)} done ({len(got)}/{len(b)} answered)")
    except claude_cli.UsageLimit as e:
        print(f"usage limit reached, stopping; re-run later to continue from the cache\n  {e}")

    out = {r.msg_id: cached[r.key]["point"] for r in msgs.itertuples() if r.key in cached}
    LLM_OVERRIDES.write_text(
        "# Written by: uv run python -m firered_anki.classify. msg_id → story point.\n"
        + yaml.safe_dump(dict(sorted(out.items())), allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )
    print(f"answered {len(out)}/{len(msgs)}, unanswered this run {missing}, cost ${cost:.2f} (API-equivalent)")
    print(f"→ {LLM_OVERRIDES.relative_to(ROOT)}; now re-run: uv run python -m firered_anki.order")


if __name__ == "__main__":
    main()
