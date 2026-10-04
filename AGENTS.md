# AGENTS.md

Notes for coding agents working in this repository. The README explains the
project to people; this file is what you need to know before changing it.

## What this is

A pipeline that turns the Japanese text of Pokémon FireRed into an Anki deck:
one card per word form and sense, in story order. A language model analyses
every sentence; its answers are cached in `data/cache/` and tracked in git, so
the deck can be rebuilt without calling the model.

## Build

```sh
scripts/fetch_vendor.sh     # once: game text and decomp into vendor/
uv sync
uv run python -m firered_anki.extract
uv run python -m firered_anki.order
uv run python -m firered_anki.tokenize
uv run python -m firered_anki.cards
uv run python -m firered_anki.build     # → firered_jp.apkg
```

About two minutes, no model calls. After a change that only affects cards
(`cards.py`, `build.py`, `grounding.py`, `corrections.yaml`, `known.txt`),
`cards` and `build` are enough.

There is no test suite. The checks are in "Before you say it is done" below.

## Stages

| Module | Does | Calls the model |
|---|---|---|
| `extract` | corpus → cleaned sentences | no |
| `order`, `map_order`, `decomp`, `spread` | story order of every line | no |
| `classify` | places strings no rule fits | yes |
| `tokenize` | tokens and JMdict candidates | no |
| `analyse` | every word of every sentence, particles included (`full`, `escalate`, `rerun N`) | yes |
| `grounding` | checks and fills dictionary links | no |
| `sense_pick` | checks links found by lookup | yes |
| `splits` | one fixed word boundary per string cut two ways; redoes the sentences that differ (`--chapter N`) | yes |
| `particles` | the particle uses the analysis left without a sense (`--chapter N`) | yes |
| `grammar` | patterns and sentence breakdown (`run --chapter N`) | yes |
| `chapter` | runs the model stages for one chapter in order until nothing is left (`chapter N`, `--dry-run`) | yes, through the stages it runs |
| `corrections` | lays `corrections.yaml` over the cached answers | no |
| `names` | finds the names (Pokémon, items, moves, abilities, places, people, badges, game terms) for their cards; gives the model the English names; redoes sentences that misname one (`--chapter N`, `--dry-run`) | yes, only for the sentences that fail |
| `cards` | occurrences → cards | only for a sense-merge question not yet cached |
| `build` | cards → `.apkg` | no |

Chapters are numbered from 0 in the code: chapter 0 is everything up to the
first gym badge. The docs call it "the first chapter".

## Rules

**Model calls spend the owner's Claude plan allowance.**
- Do not run a stage that calls the model unless asked, and run it on the
  scope that was named (a sentence, a chapter), not on the whole deck.
- While something is still being tried out, use the smallest run that answers
  the question, and talk through a prompt change before making it.
- Batches are 40 sentences. Larger ones drop answers near the end of the batch
  and come close to the output cap.

**The caches are the model's raw answers. Do not edit them by hand.**
- To fix one wrong translation, gloss, breakdown line or grammar explanation,
  add an entry to `corrections.yaml` with a `why`. It is laid over the cache on
  every read and survives a rerun.
- If `cards` stops with "these corrections no longer fit", a rerun changed the
  answer the correction pointed at. Fix or remove the entry; do not weaken the
  check.

**Word boundaries are decided per string, not per prompt.**
- When the same string is cut two ways, do not reword the analysis prompt
  again. Run `splits`: it fixes one decision per string in `splits.yaml` and
  re-analyses only the sentences that differ.
- A hand correction covers only the fields it names. A re-analysed sentence
  gets a fresh translation, so after any redo, re-read the sentences that have
  an entry in `corrections.yaml`.

**Check the game before asserting what the game does.**
- The decomp is in `vendor/pokefirered`, a partial sparse clone. Fetch a file
  that is not checked out with `git -C vendor/pokefirered show HEAD:src/<file>`.
- Never run `git ls-tree -l`, `git log -p` or `git grep <rev>` in the vendor
  clones: it downloads everything. To search, grep the checked-out files.
- The official English text (`en_msg.txt`) is a localisation and sometimes says
  something different from the Japanese. Do not treat it as the reference
  translation. The one use of it is names: a Pokémon, item, move, ability,
  place, person or badge is called what the English game calls it, on cards
  and in every translation and grammar gloss (フシギダネ is Bulbasaur,
  チャンピオンロード Victory Road, マサキ Bill). `name_spellings.yaml` replaces
  FireRed's twelve-letter squeezes with today's spelling. People and badges
  have no list in the game: they are in `names_by_hand.yaml`, and a name goes
  in there only after checking it against the English line that pairs with a
  Japanese line saying it. The player and the rival stay Red and Green.
- When a kind of name is added, raise `names.SHOWN`, so answers given before
  it are checked again.
- The analysis and the grammar pass are shown the names their sentences
  mention. To fix a misnamed thing, run `names`; do not hand-correct it and do
  not rerun the chapter. An answer given with the names shown is the model's
  decision and is not asked again.

**Text FireRed never shows is not in the deck.**
- The order stage leaves out Ruby/Sapphire data no FireRed player meets,
  text the decomp marks `@ Unused`, and text whose line in the English game is
  still Japanese (the translators skipped what no script shows), and lists it
  in `data/never_shown.csv`. Nothing downstream sees it, so no model call is
  ever spent on it.
- The untranslated-in-English test is not applied to the end of the deck
  (link play, union room): the decomp is of the English game and cannot say
  what the Japanese release's own features showed.
- Do not bring such text back to give a word a card. Link play, error messages
  and the Help menu are different: they can be seen, and stay at the end.

**Cards.**
- One card per word form and sense: つかまえた and つかまえて are two cards.
  Particles are never part of a form.
- Do not drop cards for stammers, garbled speech, word fragments or sound
  words. They are tagged (`fragment`, `onomatopoeia`), not filtered: the card is
  what explains the odd text.
- A particle gets one card per job (か the question marker, か "or"), from the
  sense the analysis gives each use, or the `particles` stage where it gave none. A use with no sense of its own is
  filed under the particle's most common card, as an example only.
- Everything `names.py` knows as a name has one card in the story deck:
  Pokémon, items, moves, abilities, types, places, and the people, badges,
  game terms and real-world names of `names_by_hand.yaml`, whatever words the
  analysis cut the name into
  (げんき + の + かけら is still the Revive). The card sits at the first dialogue
  line that says the name, or else at the name's line in the game's list,
  which stays where the thing is first met and is not spread like other menu
  text. A menu or battle line that mentions a name does not move its card.
- Any other name makes no card. There is no names deck: do not bring it back
  for naming-screen presets, one-off characters or pieces of names. A name
  that should have a card goes into `names_by_hand.yaml`. A cry or a shouted
  name keeps its card like a fragment does.
- A name's `note` in `names_by_hand.yaml` is for what a learner may not know:
  Japanese culture, the real world, another Pokémon game. Write it only from
  what you can check, and say where the English game replaced the reference.
- Where a name line goes inside its map is decided in the order stage
  (`fine_places`): a trainer's Pokémon after that trainer's challenge, with
  its moves and ability; wild ones spread between the map's messages; a place
  before the map's dialogue; a type with the first Pokémon of that type. Change
  placement there, not in `cards` or `spread`.
- A card's identity is its key (dictionary entry, sense, form). Changing how
  keys are made changes identities, and people lose review history on those
  cards. Measure it (see below) and say so.
- The note type and deck IDs are fixed. Add a new field at the end of the field
  list, so Anki can merge the note type on import.

**Grammar.**
- Explanations are in plain words for a learner who knows no grammar terms, and
  end with what that stretch of the sentence means.
- Everything on a card is written for that card's sentence. There is no shared
  index of patterns; one was tried and produced headings that contradicted the
  explanation under them.
- A line of the species or item list is one name. The grammar pass skips it.
- The grammar pass must run after the analysis of the same sentences: its
  answers are matched to the analysis's word list. The order for a chapter is
  `analyse rerun N`, `splits --chapter N`, `names --chapter N`, `sense_pick`,
  `cards`, `particles --chapter N`, `grammar run --chapter N`, `cards`, `build`.
- `chapter N` runs that series, and repeats it until nothing is left to ask:
  a change to card keys can change which lines the chapter shows. Use it
  instead of running the stages by hand. `chapter N --dry-run` only says what
  is left, and calls nothing; run it after any change that could touch a
  finished chapter.

**No cost figures.** Do not put dollar amounts or token counts in the code's
output, the docs or release notes. Sentence counts, call counts and minutes are
fine.

**Docs move with the code.** When numbers or behaviour change, update
`README.md` and `docs/how-it-works.html` in the same piece of work.

## Git and releases

- Commit only when asked. New commits only: no amending, no force-push, no
  moving tags.
- Use the repository's own git identity; do not override it.
- `docs/ankiweb-description.md` is left untracked on purpose. `vendor/`, the
  `.apkg` files and everything in `data/` except the model caches are ignored.
  `data/grounding_lookups.json` is a copy of dictionary lookups, rebuilt when
  missing; delete it freely.
- Cut a release only when asked. Attach `firered_jp.apkg`, built from the
  commit being tagged. Write the notes so they stand alone against v1.0.

## Before you say it is done

- `cards` and `build` run without errors, and the card counts printed are the
  ones you expect.
- If the change could touch card identities, compare the `key` column of
  `data/05_cards/cards.parquet` before and after, and report how many cards
  went and how many appeared.
- Read `data/05_cards/spotcheck.txt` (30 random cards) or the cards your change
  was about. A stage that ran is not the same as a card that reads right.
- If a command was interrupted or refused, check what it had already changed
  before reporting the state of the files.

## Current state

Update this when it changes.

- Chapter 0: analysed by Opus with prompt version 4, and 5 for 49 sentences (particle
  senses picked by the analysis), grammar by Opus.
- Chapters 1 to 9: analysed by Sonnet with prompt version 2, with Opus on the
  sentences Sonnet flagged; no grammar, and one card per particle. `analyse
  rerun N` redoes every sentence of a chapter that is not Opus on version 4 or 5;
  `analyse redo <file>` re-analyses only the sentences listed in a file.
- `corrections.yaml` has seven entries: the SELECT button line, Mom's two TV
  lines, and four word
  forms the model wrote that were not in their sentence.
- `splits.yaml` has 72 decisions, all from the first chapter.
- Names: 901 cards (221 Pokémon, 148 items, 253 moves, 61 abilities, 17
  types, 132 places, 24 people, 2 for Team Rocket, 8 badges, 20 game terms, 15
  real-world names), found by `names.py` in every chapter with no model call.
  The 56 name lines this put in chapter 0 were re-analysed on Opus. The names
  deck is gone: one deck, 11,251 cards. The note type has a `Note` field.
- English names: the name check passes on every sentence the deck shows, in
  all chapters. 91 sentences were redone on Opus for it, most of them in
  chapters 1 to 9, so those chapters now have a few more Opus sentences.
  `name_spellings.yaml` has 40 entries, `names_by_hand.yaml` 91.
- Re-analysing a chapter changes how about one sentence in five is split, even
  between two Opus runs, and so changes card identities (about 220 of the first
  chapter's 2,500 cards each time). Do not rerun a finished chapter for a small
  prompt change.
