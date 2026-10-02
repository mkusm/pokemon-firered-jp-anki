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
uv run python -m firered_anki.build     # → firered_jp.apkg, firered_jp_names.apkg
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
| `analyse` | every word of every sentence (`full`, `escalate`, `rerun N`) | yes |
| `grounding` | checks and fills dictionary links | no |
| `sense_pick` | checks links found by lookup | yes |
| `grammar` | patterns and sentence breakdown (`run --chapter N`) | yes |
| `corrections` | lays `corrections.yaml` over the cached answers | no |
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

**Check the game before asserting what the game does.**
- The decomp is in `vendor/pokefirered`, a partial sparse clone. Fetch a file
  that is not checked out with `git -C vendor/pokefirered show HEAD:src/<file>`.
- Never run `git ls-tree -l` or `git log -p` in the vendor clones: it downloads
  everything.
- The official English text (`en_msg.txt`) is a localisation and sometimes says
  something different from the Japanese. Do not treat it as the reference
  translation.

**Cards.**
- One card per word form and sense: つかまえた and つかまえて are two cards.
  Particles are never part of a form.
- Do not drop cards for stammers, garbled speech, word fragments or sound
  words. They are tagged (`fragment`, `onomatopoeia`), not filtered: the card is
  what explains the odd text.
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
- The grammar pass must run after the analysis of the same sentences: its
  answers are matched to the analysis's word list.

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
- Cut a release only when asked. Attach both `.apkg` files, built from the
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

- Chapter 0: analysed by Opus, grammar by Opus.
- Chapters 1 to 9: analysed by Sonnet, with Opus on the sentences Sonnet
  flagged; no grammar yet.
- `corrections.yaml` has six entries: the SELECT button line, and five word
  forms the model wrote that were not in their sentence.
