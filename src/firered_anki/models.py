"""Which model answers for which sentence.

Opus, except where the owner chose otherwise: the Link play deck is analysed,
explained and annotated by Sonnet. The choice is made per sentence, not per
run. A sentence that any Opus chapter shows is Opus's wherever a stage meets
it, so a run on the Link play deck never puts a Sonnet answer under a card of
the story or of the Help deck; and a sentence Opus has already answered is
never handed back to Sonnet.

Word-boundary decisions (splits) hold for the whole deck and stay with Opus.
The two small deck-wide checks that were always Sonnet's (sense_pick, the
sense merge in cards) are not chosen here.
"""

from .spread import LINK_PLAY

DEFAULT = "opus"
BY_CHAPTER = {LINK_PLAY: "sonnet"}
RANK = {"sonnet": 0, "opus": 1}  # the higher may stand in for the lower, never the other way

_wanted: dict[str, str] | None = None


def use(deck) -> None:
    """Work the choice out from the deck: rows with `text`, `chapter` and
    `card_order`. A sentence shown in several chapters takes the best of their models."""
    global _wanted
    shown = deck[deck["card_order"].notna()]
    wanted: dict[str, str] = {}
    for text, chapter in zip(shown["text"], shown["chapter"]):
        model = BY_CHAPTER.get(int(chapter), DEFAULT)
        if text not in wanted or RANK[model] > RANK[wanted[text]]:
            wanted[text] = model
    _wanted = wanted


def of(text: str) -> str:
    """The model whose answer this sentence needs. A sentence the deck does not show: the default."""
    if _wanted is None:
        from .cards import prepare  # late: cards imports the stages, which import this module

        prepare(offline_merge=True)  # sets the choice as it builds the deck
    return _wanted.get(text, DEFAULT)


def enough(made_by: str | None, text: str) -> bool:
    """Whether an answer by `made_by` will do for this sentence."""
    return RANK.get(made_by, -1) >= RANK[of(text)]


def by_model(texts) -> dict[str, list[str]]:
    """These sentences, grouped by the model that answers for them, Opus first."""
    out: dict[str, list[str]] = {}
    for t in texts:
        out.setdefault(of(t), []).append(t)
    return dict(sorted(out.items(), key=lambda kv: -RANK[kv[0]]))
