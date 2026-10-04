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
| `firered_jp.apkg` | 11,212 cards, in story order |

It is attached to the [latest release](https://github.com/mkusm/pokemon-firered-jp-anki/releases/latest).

Each card:

- **Front:** the sentence as the game shows it, with one word highlighted, and
  the speaker when known.
- **Back:** the word's form and dictionary form, kanji with per-character
  readings, romaji, what it means here, the dictionary sense that was chosen,
  how the form is built
  (`つかまえ (stem) + て (te-form)`), the sentence in kanji and in English, up to
  two more example sentences, the whole message, and the JMdict senses.
- **Grammar**, on the back, for the first chapter so far (everything up to the
  first gym, 2,402 cards): the patterns the word takes part in, each explained
  in plain words for that sentence, and a breakdown of the sentence phrase by
  phrase. The card for と in しゅじんこうと　なって says: "と before なる shows
  what someone turns into; it is a more formal に. しゅじんこうと　なって =
  'becoming the hero'."

つかまえた, つかまえて and つかまえられる are three cards: every form is drilled
where the story first uses it.

Particles get a card for each job they do, in the first chapter so far: か has
one card as the question marker and another for "or", が one as the subject
marker and another for "but". In the later chapters each particle still has a
single card, showing its first use.

Names are in English as the English game has them, on the cards and in every
translation: ニビシティ is Pewter City, ふしぎなアメ the Rare Candy, チャンピオンロード
Victory Road, マサキ Bill, グレーバッジ the Boulder Badge. Names FireRed had to
squeeze into twelve letters are spelled the way the series spells them now
(Paralyze Heal, not PARLYZ HEAL). The player and the rival keep the names the
deck fills in for them, Red and Green.

Everything with a name worth knowing has one card: 221 Pokémon, 148 items,
253 moves, 61 abilities, the 17 types, 132 places, the main characters, the
badges, 20 game terms (ポケモンずかん, してんのう) and 15 names from the real world
(ファミコン, きょうと). The card sits at the first line of dialogue that says the
name, or else where the thing is first met:

- a Pokémon in a trainer's team comes right after that trainer's challenge,
  followed by its type, the moves it shows there and its ability (Brock's
  イシツブテ, じめん, まるくなる, がんじょう, then イワーク, しめつける, がんせきふうじ,
  before his defeat line);
- wild Pokémon are spread along their route, each with its type, moves and
  ability;
- a place comes as you walk in; an item where you can first get it.

The card gives the name the English game uses and, where the Japanese name is
made of words, what they mean: げんきのかけら is the Revive, "vigor + shard". A
type says it is one: ほのお is "Fire (Pokémon type)". Where a name needs
knowledge a learner may not have, the card explains it: ファミコン is "short for
ファミリーコンピュータ (Family Computer), the console sold outside Japan as the
NES".

Names not worth a card get none: the presets of the naming screen, characters
who appear once, pieces of longer names. There is no separate names deck.

A line of one word (はい, a menu word) and a bare name get no grammar: there
is nothing between words to explain.

Tags: every card has its location (Route 1, Pallet Town Professor Oak's Lab,
Battle messages, Teachy TV). `pokemon`, `item`, `move`, `ability`,
`type`, `place`, `person`, `badge`, `term` and `world` mark the name cards (with `proper`, so
you can suspend them all at once). `low-confidence` marks cards whose
dictionary entry could not be confirmed, or whose word is a scrap of text the
analysis had to guess at, `onomatopoeia` sound and mimetic words (ドキドキ, キラキラ),
and `fragment` pieces of broken speech: stammers (こっ　こんなに), garbled or
interrupted words. Fragment cards are kept on purpose, because they explain
text that would otherwise be puzzling.

## How to use

All you need is [Anki](https://apps.ankiweb.net/), on desktop or on your phone.

1. **Get the deck.** Download `firered_jp.apkg` from the
   [latest release](https://github.com/mkusm/pokemon-firered-jp-anki/releases/latest)
   and open it in Anki (desktop: File → Import; AnkiDroid and AnkiMobile: open
   the file).
2. **Keep the story order.** New cards must come in the order they were added.
   In the deck's options, under New Cards, set **Insertion order** to
   *Sequential (oldest cards first)*, and leave the display order on its
   defaults. With a random order the deck loses its point.
3. **Pick a pace.** The cards follow the game, so the natural way to use the
   deck is to study a stretch and then play it. For scale: the opening sequence
   is the first 168 cards, everything up to the first gym about 2,500, the whole
   deck 11,212. At 20 new cards a day that is about four months to Brock.
4. **Read the card.** The front is the line as the game shows it, in kana, with
   one word highlighted: read the sentence and recall that word. The back gives
   the reading in romaji, the dictionary form, the meaning in this sentence,
   how the form is built, the grammar it is part of, and the sentence in kanji
   and in English.
5. **Trim what you don't want.** In the browser, search by tag and suspend:
   `tag:fragment` (stammers and broken speech), `tag:onomatopoeia`,
   `tag:low-confidence`, or a place such as `tag:Pewter_City_Gym`. To drop
   words for good, add them to `known.txt` and rebuild.
6. **Update later.** Import a newer release over the old one. Card IDs are
   stable, so your review history stays. In the import dialog set **Update
   notes** and **Update note types** to *Always*, or Anki keeps the old card
   layout. Also tick **Merge note types** when the note type has gained a
   field since your copy: v1.2 added two for the grammar, and the build after
   v1.3.1 one for notes on names. If you have the old names deck
   (FireRed JP::Names), delete it: its cards are in the main deck now. About 220 first-chapter cards were re-analysed
   into different words or forms in v1.2; those arrive as new cards and their
   old versions stay in your collection until you delete them.

## How the order is decided

1. **Dialogue** follows `map_order.yaml`. A line a script shows only once a
   story flag is set goes after the map that sets the flag: the Silph Co.
   employees thank you only after Giovanni is beaten, Mom says "you and your
   Pokémon are looking great" only once you have one.
2. **Everything else** goes where you first see it. Species, moves, items,
   abilities, map names and battle messages are worked out from the decomp: wild
   encounters, trainer parties, Mart stock, item pickups, TMs, and which move
   effect's battle script prints which message. Menus and other UI go by the
   rules in `first_seen.yaml`.
3. **Spreading.** The game is cut into chapters, one per gym badge. Non-dialogue
   lines are interleaved with the story inside their chapter, and only kept if
   they teach something the chapter's dialogue does not. The line that names a
   Pokémon, an item, a move, an ability or a place is not spread: it stays
   where the thing is first met, unless dialogue has already said the name.
   Inside a map, a trainer's Pokémon, its moves and its ability hang on that
   trainer's challenge; wild ones are spread between the map's messages.
4. The deck opens with the game's own opening (controls guide, intro, Oak's
   speech); menu text only starts once the menu can be opened.

Link play, error messages and the Help menu go to the end of the deck.

Text FireRed never shows is left out altogether, and no model stage ever sees
it: Ruby and Sapphire data that no FireRed player meets (Hoenn Pokédex text,
items that cannot be obtained, moves nothing knows), text the decomp marks
as unused, such as an e-mail from the Ruby/Sapphire rival's computer, and text
the English game left in Japanese. The translators skipped what no script
shows, so a line whose English copy is still Japanese is a leftover: a copy of
Hoenn's Safari Zone script, berry tags, the rival's lines for battles you
cannot lose and carry on. The order stage lists what it left out, and why, in
`data/never_shown.csv`.

## Building it yourself

You don't need any of this to use the deck: the release files and Anki are
enough. This part is for rebuilding the deck or changing how it is made.

### Requirements

- [uv](https://docs.astral.sh/uv/) and Python 3.10+
- git
- For the LLM stages only: the `claude` CLI (Claude Code), logged in. They run
  on a Claude subscription through `claude -p`. With the caches in this repo you
  do not need it to rebuild the deck.

### Build the deck

```sh
scripts/fetch_vendor.sh          # the game text and the decomp, into vendor/
uv sync

uv run python -m firered_anki.extract    # corpus → cleaned sentences
uv run python -m firered_anki.order      # story order
uv run python -m firered_anki.tokenize   # tokens and dictionary candidates
uv run python -m firered_anki.cards      # one card per word form and sense
uv run python -m firered_anki.build      # → firered_jp.apkg
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
uv run python -m firered_anki.analyse rerun 0    # redo a chapter on Opus with the current prompt (no number: all)
uv run python -m firered_anki.sense_pick        # check entries found by lookup, choose their sense
uv run python -m firered_anki.splits --chapter 0        # settle strings cut two ways, redo the sentences that differ
uv run python -m firered_anki.names --chapter 0         # redo sentences whose translation misnames a Pokémon, item, move, ability, place, person or badge
uv run python -m firered_anki.particles --chapter 0     # particle uses the analysis left without a sense
uv run python -m firered_anki.grammar run --chapter 0   # grammar patterns and sentence breakdowns
```

For a whole chapter there is one command that runs the last six in the right
order and repeats until nothing is left to ask:

```sh
uv run python -m firered_anki.chapter 1 --dry-run   # what the chapter still needs
uv run python -m firered_anki.chapter 1             # do it, then build
```

All of them resume from the cache, and `analyse` waits out a usage limit and
carries on. For scale: the first full run was 12,747 sentences in 322 calls
(Sonnet, low effort, 40 sentences a call), about five hours with one usage-limit
pause. Escalation redid 2,132 sentences on Opus. Redoing the first chapter on
Opus is about 1,200 sentences in 31 calls, twenty at a time, 10 minutes; that analysis
also picks the sense of each particle. The grammar pass, also on Opus, is 40
sentences a call, eight at a time: 5 minutes for the whole chapter. The particle
pass only mops up what the analysis left without a sense: a single call.

### Things you edit

| File | What it controls |
|---|---|
| `known.txt` | Words you already know. They are analysed but never carded. |
| `name_spellings.yaml` | English names FireRed squeezed into twelve letters, with today's spelling (PARLYZ HEAL → Paralyze Heal). |
| `names_by_hand.yaml` | English names of the things the game has no list of, 91 in all: people, Team Rocket, badges, places, game terms and real-world names, some with a note for the card. |
| `map_order.yaml` | Play order of the 344 maps and scenes, following Bulbapedia's walkthrough. Lists single labels where a map is revisited later in the story. |
| `first_seen.yaml` | Where menus, battle text, items and other non-dialogue text is first seen: rules per text group, and the list of story points the LLM may choose from. |
| `first_seen_llm.yaml` | Written by `classify`: the story point chosen for each string no rule fits. Readable, so you can review it. |
| `splits.yaml` | Fixed word boundaries, one decision per string (ポケモンセンター is one word, たいせつな is たいせつ + な), each with its reason. Written by the `splits` stage from two independent model decisions that agreed; you can edit a line by hand, and the stage never changes a string that is already there. |
| `corrections.yaml` | Hand corrections to the model's answers: a translation, a word's gloss, a breakdown line, a grammar explanation. They are laid over the cache when it is read, so running the model again never overwrites them. If the model's answer changes so that a correction no longer fits, `cards` stops and says so. |

After editing any of them, re-run from `order` onward (`corrections.yaml` and
`known.txt` only need `cards` and `build`). Card IDs come from the
word, sense and form, so a rebuilt deck updates your existing cards and keeps
their review history.

### Layout

```
src/firered_anki/
  extract.py      corpus → sentences
  map_order.py    resolve a message to a map_order entry; coverage check
  decomp.py       read the pret decomp
  order.py        first-seen placement and the global order
  classify.py     LLM placement of catch-all strings
  spread.py       interleave non-dialogue lines by chapter
  tokenize.py     fugashi tokens, JMdict candidates
  analyse.py      per-sentence LLM analysis, dry run, escalation, Opus rerun
  claude_cli.py   headless `claude -p` with a JSON schema
  grounding.py    fill and check JMdict IDs after the LLM
  corrections.py  lay the hand corrections over the LLM's answers
  sense_pick.py   LLM check of entries found by lookup, and their sense
  particles.py    LLM pass: the dictionary sense of each use of a particle
  splits.py       LLM pass: one fixed word boundary per string cut two ways
  spans.py        where a word is in its sentence
  grammar.py      LLM pass for grammar patterns and sentence breakdowns
  cards.py        occurrences → cards, sense merge, examples
  romaji.py       kana → Hepburn
  build.py        cards → .apkg
data/cache/       the LLM's answers (tracked)
data/…            stage outputs (ignored; rebuilt)
vendor/           upstream clones (ignored)
```

## Known limits

- The deck is in two states. The first chapter was analysed by Opus and has
  grammar on its cards. The other nine chapters are Sonnet's analysis, with
  Opus only on the sentences Sonnet flagged, and have no grammar yet. On a
  sample of 100 sentences Opus corrected a clear Sonnet error in 3: a wrong
  item name, a line read as "I was asked a favor" that means "do me a favor",
  and あったら filed under ある where it is 合う.
- Particles are split into one card per job only in the first chapter. In the
  later chapters all uses of a particle are still filed under one card.
- A grammar pattern's name is written per sentence, so the same pattern can be
  spelled two ways on different cards (〜せる and 〜させる).
- Nothing has been proofread by a native speaker.
- A wild Pokémon is spread along its route by rule, not by where its grass
  is, and a move is placed with the first Pokémon that could show it, whether
  or not it uses it.
- A map's dialogue is all placed at the map's first visit, so a name first
  said on a later visit (１のしま in Oak's lab) gets its card early.
- A Pokémon or an item is placed with the map where it is first met or found,
  at that map's first visit, even where it is in a part of the map reached
  only later.
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
