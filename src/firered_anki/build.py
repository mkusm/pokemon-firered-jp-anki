"""Stage 6: cards → Anki packages.

Fixed model and deck IDs, and note GUIDs from each card's key, so re-running
the build updates existing notes instead of duplicating them and keeps review
history. Notes are added in story order; set the deck's new-card order to
"order added" in Anki.

Fields added after the first release are at the end of the note type, so that
Anki can merge it into the earlier one on import ("Merge note types"): Grammar
and Breakdown came with the grammar pass and are empty on cards whose sentence
it has not reached; Note is the explanation on a name a learner may not know,
SentenceNote the one on a sentence.

Run: uv run python -m firered_anki.build
"""

import json

import genanki
import pandas as pd

from .cards import CARDS_OUT
from .paths import ROOT

MODEL_ID = 1607392319
DECK_ID = 2059400110
# 2059400111 was the names deck (FireRed JP::Names), retired: every name worth a card is in the main deck.
DECK_OUT = ROOT / "firered_jp.apkg"

FIELDS = [
    "Order", "Sentence", "SentenceRomaji", "Word", "WordRomaji", "Base", "BaseRomaji",
    "Kanji", "UsuallyKana", "CharReadings",
    "Literal", "InContext", "DictSense", "Onomatopoeia", "Modifiers", "SentenceKanji", "SentenceEnglish",
    "ExtraExamples", "Context", "Location", "MessageId", "Speaker", "Dictionary",
    "Grammar", "Breakdown", "Note", "SentenceNote",
]

FRONT = """
<div class="meta">{{#Speaker}}{{Speaker}}{{/Speaker}}</div>
<div class="sentence">{{Sentence}}</div>
"""

BACK = """
{{FrontSide}}
<div class="romaji">{{SentenceRomaji}}</div>
<hr id="answer">
<div class="word">
  <span class="form">{{Word}}</span>
  <span class="arrow">→</span>
  <span class="base">{{Base}}</span>
  {{#Kanji}}<span class="kanji {{#UsuallyKana}}rare{{/UsuallyKana}}">{{Kanji}}</span>{{/Kanji}}
</div>
<div class="romaji">{{WordRomaji}} → {{BaseRomaji}}</div>
{{#UsuallyKana}}{{#Kanji}}<div class="note">usually written in kana</div>{{/Kanji}}{{/UsuallyKana}}
{{#CharReadings}}<div class="readings">{{CharReadings}}</div>{{/CharReadings}}
<div class="meaning">{{InContext}}</div>
{{#Note}}<div class="about">{{Note}}</div>{{/Note}}
{{#Literal}}<div class="literal">literally: {{Literal}}</div>{{/Literal}}
{{#DictSense}}<div class="literal">dictionary sense: {{DictSense}}</div>{{/DictSense}}
{{#Onomatopoeia}}<div class="literal">{{Onomatopoeia}}</div>{{/Onomatopoeia}}
{{#Modifiers}}<div class="modifiers">{{Modifiers}}</div>{{/Modifiers}}
{{#Grammar}}<div class="grammar">{{Grammar}}</div>{{/Grammar}}
<div class="block">
  <div class="kanji-sentence">{{SentenceKanji}}</div>
  <div class="english">{{SentenceEnglish}}</div>
  {{#SentenceNote}}<div class="about">{{SentenceNote}}</div>{{/SentenceNote}}
</div>
{{#Breakdown}}<details><summary>Sentence breakdown</summary><div class="breakdown">{{Breakdown}}</div></details>{{/Breakdown}}
{{#ExtraExamples}}<details><summary>More examples</summary><div class="extra">{{ExtraExamples}}</div></details>{{/ExtraExamples}}
<details><summary>Whole message</summary><div class="context">{{Context}}</div></details>
{{#Dictionary}}<details><summary>Dictionary</summary><div class="dict">{{Dictionary}}</div></details>{{/Dictionary}}
<div class="meta">{{Location}} · {{MessageId}}</div>
"""

CSS = """
.card { font-family: "Hiragino Sans", "Noto Sans JP", sans-serif; font-size: 18px;
        line-height: 1.5; text-align: left; color: #1f2328; background: #fbfaf7;
        padding: 12px 16px; max-width: 640px; margin: 0 auto; }
.nightMode.card, .night_mode .card { color: #e6e6e6; background: #1e1f22; }
.sentence { font-size: 26px; line-height: 1.7; margin: 8px 0; }
.sentence b { color: #c2410c; font-weight: 700; }
.nightMode .sentence b { color: #fb923c; }
.word { font-size: 24px; margin-top: 8px; }
.form { font-weight: 700; }
.arrow, .meta, .note, .literal { color: #6b7280; }
.kanji { margin-left: 8px; font-weight: 600; }
.kanji.rare { color: #9ca3af; font-weight: 400; }
.readings { font-size: 16px; color: #6b7280; }
.romaji { font-size: 16px; color: #6b7280; font-style: italic; letter-spacing: 0.01em; }
.meaning { font-size: 21px; font-weight: 600; margin-top: 6px; }
.literal, .note { font-size: 15px; }
.about { font-size: 16px; margin: 4px 0; }
.modifiers { font-size: 16px; margin-top: 6px; padding: 6px 8px; border-radius: 6px;
             background: rgba(127, 127, 127, 0.1); }
.block { margin-top: 12px; }
.kanji-sentence { font-size: 19px; }
.english { font-size: 16px; color: #4b5563; }
.nightMode .english { color: #a1a1aa; }
details { margin-top: 10px; font-size: 15px; }
summary { color: #6b7280; cursor: pointer; }
.context u { text-decoration-color: #c2410c; }
.meta { font-size: 12px; margin-top: 14px; }
.grammar { margin-top: 8px; }
.pat { margin-top: 6px; padding-left: 8px; border-left: 3px solid #2563eb; }
.pname { font-weight: 700; color: #1d4ed8; }
.pspan { margin-left: 6px; color: #6b7280; font-size: 16px; }
.pexp { font-size: 16px; }
.nightMode .pname { color: #93c5fd; }
.nightMode .pat { border-left-color: #60a5fa; }
.chunks { border-collapse: collapse; margin: 4px 0 8px; }
.chunks td { padding: 2px 12px 2px 0; vertical-align: top; }
.ce { color: #6b7280; }
"""

MODEL = genanki.Model(
    MODEL_ID, "FireRed JP word",
    fields=[{"name": f} for f in FIELDS],
    templates=[{"name": "Read", "qfmt": FRONT, "afmt": BACK}],
    css=CSS, sort_field_index=0,
)


def package(cards: pd.DataFrame, deck_id: int, name: str, path) -> None:
    deck = genanki.Deck(deck_id, name)
    for c in cards.sort_values("Order").itertuples():
        key = json.loads(c.key)
        deck.add_note(genanki.Note(
            model=MODEL,
            fields=[str(getattr(c, f) or "") for f in FIELDS],
            guid=genanki.guid_for(*map(str, key)),
            tags=list(c.tags),
        ))
    genanki.Package(deck).write_to_file(path)
    print(f"{name}: {len(cards)} notes → {path.relative_to(ROOT)}")


def main() -> None:
    package(pd.read_parquet(CARDS_OUT), DECK_ID, "FireRed JP", DECK_OUT)


if __name__ == "__main__":
    main()
