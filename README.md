# pokemon-firered-jp-anki

Builds an Anki deck from the Japanese text of Pokémon FireRed (GBA): one card
for every form of every word, in the order you meet them when you play, each
shown in the sentence where it first appears.

The game is written almost entirely in kana, so the same spelling can be several
words (かみ is 紙, 神 or 髪). A tokenizer and a dictionary alone pick wrong, so
every sentence is analysed by Claude with its neighbours, speaker and location in
view, and JMdict is used to ground and check the answer.

This is an unofficial fan project for studying Japanese. Pokémon and the game's
text belong to Nintendo, Creatures and GAME FREAK; the text comes from the public
[poke-corpus](https://github.com/abcboy101/poke-corpus) and is also present in
`data/cache/`, inside the analysed sentences.

How it works, in detail: [`docs/how-it-works.html`](docs/how-it-works.html)
(open it in a browser).

## What you get

| | |
|---|---|
| `firered_jp.apkg` | 11,236 cards, in story order |
| `firered_jp_names.apkg` | 925 cards for names (Pokémon, places, moves, items), optional |

Both are attached to the [latest release](https://github.com/mkusm/pokemon-firered-jp-anki/releases/latest).

Each card:

- **Front:** the sentence as the game shows it, with one word highlighted, and
  the speaker when known.
- **Back:** the word's form and dictionary form, kanji with per-character
  readings, romaji, what it means here, the dictionary sense that was chosen,
  how the form is built
  (`つかまえ (stem) + て (te-form)`), the sentence in kanji and in English, up to
  two more example sentences, the whole message, and the JMdict senses.

つかまえた, つかまえて and つかまえられる are three cards: every form is drilled
where the story first uses it.

Tags: every card has its location. `low-confidence` marks cards where the
analysis was unsure, `onomatopoeia` sound and mimetic words (ドキドキ, キラキラ),
and `fragment` pieces of broken speech: stammers (こっ　こんなに), garbled or
interrupted words. Fragment cards are kept on purpose, because they explain
text that would otherwise be puzzling.

## How to use

1. **Get the deck.** Download `firered_jp.apkg` from the
   [latest release](https://github.com/mkusm/pokemon-firered-jp-anki/releases/latest)
   and open it in Anki (desktop: File → Import; AnkiDroid and AnkiMobile: open
   the file). Add `firered_jp_names.apkg` too if you want the names.
2. **Keep the story order.** New cards must come in the order they were added.
   In the deck's options, under New Cards, set **Insertion order** to
   *Sequential (oldest cards first)*, and leave the display order on its
   defaults. With a random order the deck loses its point.
3. **Pick a pace.** The cards follow the game, so the natural way to use the
   deck is to study a stretch and then play it. For scale: the opening sequence
   is the first 166 cards, everything up to the first gym about 2,500, the whole
   deck 11,236. At 20 new cards a day that is about four months to Brock.
4. **Read the card.** The front is the line as the game shows it, in kana, with
   one word highlighted: read the sentence and recall that word. The back gives
   the reading in romaji, the dictionary form, the meaning in this sentence,
   how the form is built, and the sentence in kanji and in English.
5. **Trim what you don't want.** In the browser, search by tag and suspend:
   `tag:fragment` (stammers and broken speech), `tag:onomatopoeia`,
   `tag:low-confidence`, or a place such as `tag:Pewter_City_Gym`. To drop
   words for good, add them to `known.txt` and rebuild.
6. **Update later.** Import a newer release over the old one. Card IDs are
   stable, so your review history stays. In the import dialog set **Update
   notes** and **Update note types** to *Always*, or Anki keeps the old card
   layout.

## Requirements

- [uv](https://docs.astral.sh/uv/) and Python 3.10+
- git
- For the LLM stages only: the `claude` CLI (Claude Code), logged in. They run
  on a Claude subscription through `claude -p`. With the caches in this repo you
  do not need it to rebuild the deck.

## Build the deck

```sh
scripts/fetch_vendor.sh          # the game text and the decomp, into vendor/
uv sync

uv run python -m firered_anki.extract    # corpus → cleaned sentences
uv run python -m firered_anki.order      # story order
uv run python -m firered_anki.tokenize   # tokens and dictionary candidates
uv run python -m firered_anki.cards      # one card per word form and sense
uv run python -m firered_anki.build      # → firered_jp.apkg, firered_jp_names.apkg
```

That takes about two minutes and makes no LLM calls: every answer is read from
`data/cache/`.

The LLM stages only do work when something is missing from the cache, for
example after you change a rule that brings in new text:

```sh
uv run python -m firered_anki.classify          # place strings no rule fits
uv run python -m firered_anki.order             # apply those placements
uv run python -m firered_anki.analyse full      # analyse uncached sentences
uv run python -m firered_anki.analyse escalate  # redo flagged ones on Opus
uv run python -m firered_anki.sense_pick        # check entries found by lookup, choose their sense
```

Both resume from the cache, and `analyse` waits out a usage limit and carries on.
For scale: the first full run was 12,747 sentences in 322 calls (Sonnet, low
effort, 40 sentences a call), about five hours with one usage-limit pause, and
roughly $110 at API prices. Escalation redid 2,132 sentences on Opus.

## Things you edit

| File | What it controls |
|---|---|
| `known.txt` | Words you already know. They are analysed but never carded. |
| `map_order.yaml` | Play order of the 344 maps and scenes, following Bulbapedia's walkthrough. Lists single labels where a map is revisited later in the story. |
| `first_seen.yaml` | Where menus, battle text, items and other non-dialogue text is first seen: rules per text group, and the list of story points the LLM may choose from. |
| `first_seen_llm.yaml` | Written by `classify`: the story point chosen for each string no rule fits. Readable, so you can review it. |

After editing any of them, re-run from `order` onward. Card IDs come from the
word, sense and form, so a rebuilt deck updates your existing cards and keeps
their review history.

## How the order is decided

1. **Dialogue** follows `map_order.yaml`.
2. **Everything else** goes where you first see it. Species, moves, items,
   abilities, map names and battle messages are worked out from the decomp: wild
   encounters, trainer parties, Mart stock, item pickups, TMs, and which move
   effect's battle script prints which message. Menus and other UI go by the
   rules in `first_seen.yaml`.
3. **Spreading.** The game is cut into chapters, one per gym badge. Non-dialogue
   lines are interleaved with the story inside their chapter, and only kept if
   they teach something the chapter's dialogue does not.
4. The deck opens with the game's own opening (controls guide, intro, Oak's
   speech); menu text only starts once the menu can be opened.

Text a normal game never shows goes to the end: link play, error messages, the
Help menu, and Ruby/Sapphire leftovers that have no place in FireRed.

## Layout

```
src/firered_anki/
  extract.py      corpus → sentences
  map_order.py    resolve a message to a map_order entry; coverage check
  decomp.py       read the pret decomp
  order.py        first-seen placement and the global order
  classify.py     LLM placement of catch-all strings
  spread.py       interleave non-dialogue lines by chapter
  tokenize.py     fugashi tokens, JMdict candidates
  analyse.py      per-sentence LLM analysis, dry run, escalation
  claude_cli.py   headless `claude -p` with a JSON schema
  grounding.py    fill and check JMdict IDs after the LLM
  sense_pick.py   LLM check of entries found by lookup, and their sense
  cards.py        occurrences → cards, sense merge, examples
  romaji.py       kana → Hepburn
  build.py        cards → .apkg
data/cache/       the LLM's answers (tracked)
data/…            stage outputs (ignored; rebuilt)
vendor/           upstream clones (ignored)
```

## Known limits

- About 250 battle messages and 580 other UI strings are placed by the LLM's
  judgement, not computed. See `first_seen_llm.yaml`.
- Chapter 1, up to Brock, is the largest, about 2,500 cards: that is where the
  menus and most basic vocabulary first appear.
- JMdict comes from `jamdict-data` (a 2020 snapshot).
- Rematch lines of ordinary trainers sit at their route's first visit.

## Sources

- Text: [abcboy101/poke-corpus](https://github.com/abcboy101/poke-corpus)
- Game data: [pret/pokefirered](https://github.com/pret/pokefirered)
- Dictionary: JMdict, via [jamdict](https://github.com/neocl/jamdict)
- Route: [Bulbapedia's FireRed and LeafGreen walkthrough](https://bulbapedia.bulbagarden.net/wiki/Walkthrough:Pok%C3%A9mon_FireRed_and_LeafGreen)
