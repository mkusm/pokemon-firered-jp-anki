"""Hand corrections to the model's answers.

`corrections.yaml` lists what a person has corrected: a translation, a word's
gloss, a breakdown line, a grammar explanation. The caches keep the model's
raw answers; the corrections are laid over them every time they are read, so
running the model again never loses one.

A correction names the thing it corrects (a word by its surface, a breakdown
line by its Japanese, a pattern by its span). If that thing is no longer in
the model's answer, because the sentence was re-analysed and split or named
differently, the cards stage stops and says which correction no longer fits,
instead of dropping it silently.
"""

from functools import lru_cache

import yaml

from .paths import ROOT

FILE = ROOT / "corrections.yaml"
_applied: set[tuple] = set()

# What names an item in each list. To correct a word's form itself, give
# `new_surface`: the model sometimes writes a form that is not in the sentence
# (きずぐすri, or ASCII cm for the game's ｃｍ).
# `drop: true` takes the item out: a grammar pattern the sentence does not have.
KEYS = {"words": ("surface", "nth", "new_surface"), "breakdown": ("jp", "drop"), "patterns": ("span", "drop")}


@lru_cache(maxsize=None)
def load() -> dict[str, dict]:
    """sentence → its correction."""
    items = yaml.safe_load(FILE.read_text(encoding="utf-8")) if FILE.exists() else []
    out: dict[str, dict] = {}
    for c in items or []:
        if c["sentence"] in out:
            raise SystemExit(f"corrections.yaml: two entries for {c['sentence']}")
        out[c["sentence"]] = c
    return out


def _fix(text: str, kind: str, fixes: list[dict], items: list[dict], field: str) -> None:
    """Lay each fix over the item whose `field` equals the fix's key."""
    key = KEYS[kind][0]
    for n, fix in enumerate(fixes):
        hits = [it for it in items if it.get(field) == fix[key]]
        if "nth" in fix:
            hits = hits[fix["nth"] - 1:fix["nth"]]
        for it in hits[:1]:
            if fix.get("drop"):
                items.remove(it)
            it.update({k: v for k, v in fix.items() if k not in KEYS[kind]})
            if "new_surface" in fix:
                it["surface"] = fix["new_surface"]
            _applied.add((text, kind, n))


def apply_analysis(text: str, rec: dict) -> None:
    """Correct a sentence's translation, kanji spelling, or its words' fields."""
    c = load().get(text)
    if not c:
        return
    for k in ("english", "kanji"):
        if k in c:
            rec[k] = c[k]
            _applied.add((text, k, 0))
    _fix(text, "words", c.get("words", []), rec["words"], "surface")


def apply_grammar(text: str, g: dict) -> None:
    """Correct a sentence's breakdown lines or its patterns' fields."""
    c = load().get(text)
    if not c:
        return
    _fix(text, "breakdown", c.get("breakdown", []), g["structure"], "jp")
    _fix(text, "patterns", c.get("patterns", []), g["patterns"], "span")


def note(text: str) -> str | None:
    """The hand-written note for a sentence, or None if there is none. An
    empty note removes the model's. It stands on its own: there need not be
    a model's note under it."""
    c = load().get(text)
    return " ".join(str(c["note"]).split()) if c and "note" in c else None


def check() -> None:
    """Stop if a correction found nothing to correct. Call after the analyses
    and the grammar of the whole deck have been read."""
    missed = []
    for text, c in load().items():
        for k in ("english", "kanji"):
            if k in c and (text, k, 0) not in _applied:
                missed.append(f"{text}\n    {k}: the sentence has no analysis")
        for kind in KEYS:
            for n, fix in enumerate(c.get(kind, [])):
                if (text, kind, n) not in _applied:
                    missed.append(f"{text}\n    {kind}: nothing matches {fix[KEYS[kind][0]]}")
    if missed:
        raise SystemExit("corrections.yaml: these corrections no longer fit the model's answers. "
                         "Fix or remove them.\n  " + "\n  ".join(missed))
