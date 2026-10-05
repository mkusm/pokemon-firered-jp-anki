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
uv run python -m firered_anki.check     # checks on what was built, no model calls
```

About two minutes, no model calls. After a change that only affects cards
(`cards.py`, `build.py`, `grounding.py`, `corrections.yaml`, `known.txt`),
`cards` and `build` are enough.

There is no test suite. `check` looks in the built data for the things that
have gone wrong before; the rest is in "Before you say it is done" below.

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
| `notes` | a note where a sentence needs knowledge a learner may not have; checks the factual ones by web search (`--chapter N`, `--dry-run`) | yes; the check is the only call with tools |
| `chapter` | runs the model stages for one chapter in order until nothing is left (`chapter N`, `--dry-run`) | yes, through the stages it runs |
| `corrections` | lays `corrections.yaml` over the cached answers | no |
| `names` | finds the names (Pokémon, items, moves, abilities, places, people, badges, game terms) for their cards; gives the model the English names; redoes sentences that misname one (`--chapter N`, `--dry-run`) | yes, only for the sentences that fail |
| `cards` | occurrences → cards | only for a sense-merge question not yet cached |
| `build` | cards → `.apkg` | no |

Chapters are numbered from 0 in the code: chapter 0 is everything up to the
first gym badge. The docs call it "the first chapter". Chapter 9 is the
postgame story. The two decks that are not the story have chapter numbers in
the code too, so that the model stages can be run on one alone: 10 is the Help
deck, 11 the Link play deck. The docs do not call them chapters.

## Rules

**Which model answers is decided per sentence (`models.py`).**
- Opus, except the Link play deck, which the owner put on Sonnet: analysis,
  grammar, notes and particle senses. A sentence that any Opus chapter shows
  is Opus's wherever a stage meets it, and an Opus answer is never handed
  back to Sonnet. Word-boundary decisions hold for the whole deck and stay on
  Opus. `sense_pick` and the sense merge in `cards` were always Sonnet's.
- Do not put a story or Help sentence on Sonnet. To check, compare which
  model made each cached answer under chapters 0 to 10 before and after a run.
- Sonnet sometimes answers only the first few sentences of a call. Its calls
  are 20 sentences (`analyse.BATCH_BY_MODEL`); the rerun asks again for what
  is missing and gives up after three rounds without progress.

**Model calls spend the owner's Claude plan allowance.**
- Do not run a stage that calls the model unless asked, and run it on the
  scope that was named (a sentence, a chapter), not on the whole deck.
- While something is still being tried out, use the smallest run that answers
  the question, and talk through a prompt change before making it.
- Batches are 40 sentences. Larger ones drop answers near the end of the batch
  and come close to the output cap.
- Model calls run with tools switched off. The one exception is the notes
  stage's fact check, which is given web search.

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
- A game term that is an ordinary word is marked `plain: true` in
  `names_by_hand.yaml`: レポート is Save wherever it stands, so that every
  translation says "save" and the card carries the literal meaning. `accept`
  lists other forms a translation may use ("saving").
- The analysis and the grammar pass are shown the names their sentences
  mention. To fix a misnamed thing, run `names`; do not hand-correct it and do
  not rerun the chapter. An answer given with the names shown is the model's
  decision and is not asked again.

**A line cannot come before the flag it waits for.**
- The order stage reads the scripts' branches (`decomp.waits_for`) and moves a
  dialogue line that is shown only once a flag is set to just after the map
  whose script sets it (`decomp.set_in`); the list is `data/moved_later.csv`.
  The line keeps its own map as its location.
- Inside a map, lines go in walking order (`walking.py`): by how far each
  person, sign or trigger is, over walkable tiles, from the door or edge that
  leads to the map visited just before. It needs `data/layouts/` from the
  decomp (in `scripts/fetch_vendor.sh`). A person's lines stay together;
  rematch lines go last. It is an approximation: where the order of a scene
  matters, list it in `map_order.yaml`, do not tune the measuring.
- Inside its own map, a line that waits for something the map itself sets
  (a flag or a scene variable, when no earlier map sets it) comes after the
  line shown as it is set, or else after the map's ordinary lines
  (`order.within_map`). What this cannot see is a condition set without a
  line, on re-entering the map: the woman by Pallet Town's sign says
  わたしも　ポケモンを　そだててるの！ only after her sign routine, and was placed
  by hand. When a line reads oddly early, look at its script.
- Where the position inside a scene matters, list the scene's labels in
  `map_order.yaml` instead, as for Professor Oak's lab and Pallet Town. Take
  the scenes from the map's `scripts.inc`, not from memory.

**Where the game gives one thing to do at a time, the order is a fixed walk.**
- The opening and the player's house are listed line by line in
  `map_order.yaml`, as walks: `{at: place, lines: [labels]}`. A walk's lines
  are shown in exactly that order and may be any text: a start-menu entry and
  its description, an item's name, what the bedroom PC says. `at` is the
  card's `Location`.
- The options screen, the save questions and the bag are walks too, each at
  its first natural use: before the first battle, after the aide in the lab
  explains saving, on Route 1 once the first Potion is in the bag.
- Take a walk's lines from the code that draws the screen (`start_menu.c`,
  `player_pc.c`, `item_pc.c`, `option_menu.c`, `item_menu.c`), fetched with
  `git show`, not from memory.
- Floating text lands only between units: a message is one, a walk is one
  (`unit` in the order stage's output). No more than two floating lines come
  in a row (`spread.MOST_IN_A_ROW`), and the pace is what is left over the
  sentences left. `TODO.md` item 11: what is first seen at a chapter's last
  map still piles up at the chapter's end.
- The first battle is a walk around the rival's challenge in Oak's lab, from
  `battle_controller_oak_old_man.c`. The party screen, the Mart's counter and
  the Pokédex are walks too, each where it is first used.
- A floating line still waiting when its chapter ends runs on into the next
  chapter. It keeps its own chapter number, which is what the model stages go
  by, so a chapter's sentences are not all between that chapter's maps.
- The Help deck is in the Help menu's order: each question or term, then its
  answer (`order.help_place`, by the labels' names).
- The game shows some lines only to a boy and others only to a girl
  (`decomp.gender_only`, 14 pairs). A card is tagged `player-boy` or
  `player-girl` only when every sentence that could carry it is that side's:
  the tag is for dropping, and dropping it must not lose a word the player
  meets elsewhere. Tagging every card that sits on such a line was tried
  first and marked 97 cards as the boy's, 89 of them words everyone needs.
  A bare easy-chat word is tagged `word-list`. Tag, do not remove: the
  owner's choice.
- Text outside a walk floats (`spread.py`). Nothing floats before the player
  first leaves Oak's lab (`order.FLOATS_AFTER`, the owner's choice): do not
  let menu or battle text drift into the house, the town or the lab scene
  again. A name's own line (a starter's move, its type) is not floating text
  and stays where the thing is met.
- Pallet Town itself is a walk, in walking order from the player's door to
  the lab. The owner confirmed that order; ask before changing it.
- Text the game draws as a picture is not in the text dump. The title screen's
  ポケットモンスター is written by hand in `hand_lines.yaml`; add to it only
  what the owner asks for.

**Text FireRed never shows is not in the deck.**
- The order stage leaves out Ruby/Sapphire data no FireRed player meets,
  text the decomp marks `@ Unused`, and text whose line in the English game is
  still Japanese (the translators skipped what no script shows), and lists it
  in `data/never_shown.csv`. Nothing downstream sees it, so no model call is
  ever spent on it.
- What those three tests cannot tell is listed by hand under `never_shown` in
  `first_seen.yaml`, each with how we know: the TV seen from the side, the
  rain message, Wally's line, the wild double battle. Check the decomp before
  adding one; The Cutting Room Floor's page on the game is a good place to
  find candidates, and must be read from a saved copy (the site refuses
  Claude).
- The untranslated-in-English test is not applied to the Help and Link play
  decks: the decomp is of the English game and cannot say what the Japanese
  release's own features (the e-Reader, the Joyspot) showed. What is known to
  be Ruby and Sapphire's there is listed by hand under `never_shown`, by
  feature: the decomp has no source file for the old men of Mauville, trendy
  sayings, the Lilycove lady, TV shows, contests or secret bases.
- Do not bring such text back to give a word a card.
- Save failures and other error messages can be shown, but the owner chose to
  leave them out (`exclude` in `first_seen.yaml`). The one kept is the warning
  before saving over another game's file, which is in the save walk.

**Three decks, one package.**
- The story is one deck; the Help menu and link play are decks of their own
  (`spread.DECK_OF`, `build.DECKS`). Link play is everything that needs a
  second player or device: the Union Room, the Cable Club, trading, Mystery
  Gift, the e-Reader, mail, the easy-chat screens and word lists. In
  `first_seen.yaml` the anchor `help` sends text to the first and `end` to
  the second.
- The Help deck stands alone (`cards.STAND_ALONE`): it has a card for every
  word the Help menu uses, on its first sentence there, even when the story
  has a card for the word. The menu can be opened from the first screen, so
  it must not lean on later chapters. Such a card's key begins with `help`,
  which makes it a separate note. The Link play deck holds only what the
  story has not taught, and does not count on the Help deck. The owner chose
  this; do not make Link play stand alone without asking.
- Solo text does not go there. A line a player can see alone belongs in the
  story where it is seen: blacking out from Route 1, the diploma at the end of
  the postgame, releasing a Pokémon at the PC.
- The deck names are the owner's: `Pokemon FireRed Japanese`, with `1 Story`,
  `2 Help` and `3 Link play / multiplayer` under it. The numbers keep Anki's
  alphabetical list in order.
- The deck is in development and the owner is its only user: a new version
  replaces the old one (delete the deck, then import). Do not build or
  document a migration path. For the record, Anki matches decks by name on
  import, not by ID, and leaves a card it already has in the deck it is in;
  this was tested with Anki's own import code (`uv run --no-project --with
  anki`), an old package first, then a new one.

**Cards.**
- One card per word form and sense: つかまえた and つかまえて are two cards.
  Particles are never part of a form.
- The cards of one sentence come in the order its words stand in, left to
  right. Only the `Order` field depends on this, not a card's key.
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
- The words inside a name get a card from a real sentence when one has them,
  and from the bare name line only when nothing else does. The pieces of a
  Pokémon's name and the number in a route's name make no card.
- A number, a circled step number and a button's letter are not words and
  make no card (`cards.NOT_A_WORD`). Abbreviations in Latin letters do.
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
- A card's `Location` is a readable name from `cards.PLACES` and
  `cards.place_name`. Add a new text group there; do not show raw group names.
- A card's identity is its key (dictionary entry, sense, form). Changing how
  keys are made changes identities. While the deck is in development that
  costs nobody their review history, but still measure it (see below) and say
  so: an unexpected number is how a mistake shows.
- The note type ID is fixed. Add a new field at the end of the field list, so
  Anki can merge the note type on import. A card's deck is not a field: it is
  `deck` in `cards.parquet`.

**Grammar.**
- Explanations are in plain words for a learner who knows no grammar terms, and
  end with what that stretch of the sentence means.
- Everything on a card is written for that card's sentence. There is no shared
  index of patterns; one was tried and produced headings that contradicted the
  explanation under them.
- A bare name line and a line of one word get no grammar: nothing is asked
  and nothing is shown (`grammar.has_grammar`).
- The grammar pass must run after the analysis of the same sentences: its
  answers are matched to the analysis's word list. The order for a chapter is
  `analyse rerun N`, `splits --chapter N`, `names --chapter N`, `sense_pick`,
  `cards`, `particles --chapter N`, `grammar run --chapter N`,
  `notes --chapter N`, `cards`, `build`.
- `chapter N` runs that series, and repeats it until nothing is left to ask:
  a change to card keys can change which lines the chapter shows. Use it
  instead of running the stages by hand. `chapter N --dry-run` only says what
  is left, and calls nothing; run it after any change that could touch a
  finished chapter.

**Notes.**
- A sentence note is for what a reader who does not know Japan would miss: a
  cultural reference, a play on words, a regional dialect, a place where the
  English game says something else in substance. Not grammar, vocabulary,
  Pokémon lore, or what a name means (names have their own notes in
  `names_by_hand.yaml`).
- A note that states a fact about the outside world is shown only after the
  check step has found a page that supports it; the page and the passage are
  kept in the cache. Do not show an unchecked one, and do not write such a
  note by hand without looking it up.
- To change or remove a note, use `note:` in `corrections.yaml`.

**No cost figures.** Do not put dollar amounts or token counts in the code's
output, the docs or release notes. Sentence counts, call counts and minutes are
fine.

**Docs move with the code.** When numbers or behaviour change, update
`README.md` and `docs/how-it-works.html` in the same piece of work.

## Git and releases

- Commit only when asked. New commits only: no amending, no force-push, no
  moving tags.
- Use the repository's own git identity; do not override it.
- Commit messages and release notes carry no links to Claude sessions, and no
  `Claude-Session:` line. The history was rewritten once, on the owner's
  request, to remove them; that was the exception to the rule above.
- `docs/ankiweb-description.md` is left untracked on purpose. `vendor/`, the
  `.apkg` files and everything in `data/` except the model caches are ignored.
  `data/grounding_lookups.json` is a copy of dictionary lookups, rebuilt when
  missing; delete it freely.
- Cut a release only when asked. Attach `firered_jp.apkg`, built from the
  commit being tagged. Write the notes so they stand alone against v1.0.

## Before you say it is done

- `cards` and `build` run without errors, and the card counts printed are the
  ones you expect.
- `check` passes. When something new goes wrong, add a check for it there.
- If the change could touch card identities, compare the `key` column of
  `data/05_cards/cards.parquet` before and after, and report how many cards
  went and how many appeared.
- Read `data/05_cards/spotcheck.txt` (30 random cards) or the cards your change
  was about. A stage that ran is not the same as a card that reads right.
- If a command was interrupted or refused, check what it had already changed
  before reporting the state of the files.

## Current state

Update this when it changes.

- Chapters 0 to 10, the whole story and the Help deck: analysed by Opus with prompt version 4 or 5 (particle
  senses picked by the analysis), grammar and notes by Opus. All settled:
  `chapter N --dry-run` reports nothing left for each.
- Chapter 11 (the Link play deck): analysed by Sonnet with prompt version 5,
  grammar, notes and particle senses by Sonnet too, at the owner's choice.
  1,537 of its sentences are Sonnet's; the 129 that an earlier chapter also
  shows are Opus's. Settled. `analyse rerun N` redoes every sentence of a
  chapter that its model has not answered on version 4 or 5;
  `analyse redo <file>` re-analyses only the sentences listed in a file.
- `corrections.yaml` has eleven entries: the SELECT button line, Mom's two
  TV lines, seven レポート translations the name check had left stiff ("the
  Pokémon Save"), and one word form Sonnet wrote that is not in its sentence
  (ナウい for ナウイ). Four corrections of word forms Sonnet wrote wrongly were removed
  as their chapters were redone on Opus, which wrote the forms correctly; the
  last two (ｃｍ, twice) went with the postgame.
- `splits.yaml` has 476 decisions, from chapters 0 to 11. They are checked
  across chapters: a later chapter's run can send an earlier sentence back.
- Names: 953 cards (221 Pokémon, 153 items, 284 moves, 76 abilities, 17
  types, 132 places, 23 people, 2 for Team Rocket, 8 badges, 22 game terms, 15
  real-world names), found by `names.py` in every chapter with no model call.
  The 56 name lines this put in chapter 0 were re-analysed on Opus. The names
  deck is gone. Three decks, 11,078 cards: story 9,533, Help 940 (stand-alone), Link play 605. The note type has `Note` and
  `SentenceNote` fields.
- English names: the name check passes on every sentence the deck shows, in
  all chapters. 91 sentences were redone on Opus for it, most of them in
  chapters 1 to 9, so those chapters now have a few more Opus sentences.
  `name_spellings.yaml` has 40 entries, `names_by_hand.yaml` 93.
- `names.SHOWN` is 5: レポート (Save, `plain`) and ポケットモンスター (Pokémon)
  were added, and 23 more sentences redone for them. Every translation of a
  レポート sentence now says "save" in some form; the wording is the model's
  and varies ("the Save", "save file", "this report (save)"). The Rocket
  Warehouse's レポート is a real report, and the model kept it so.
- Walks: 19 in `map_order.yaml`, 187 lines: the title and the bars of the
  opening, the house, Pallet Town, the party screen, options, the first
  battle, saving, the bag, the Mart's counter, the Pokédex.
  A walk's line is not moved by the flag rule: its place is the listed one.
  `hand_lines.yaml` has one line, the title. `never_shown` in
  `first_seen.yaml` has 38 entries, two of them globs.
- `chapter N` can stop with "not settled: three rounds in a row" on a chain
  of near-identical floating lines, of which only one is shown at a time (the
  Fame Checker's four してんのう … とくしゅう！ titles in chapter 8): each
  round redoes one and brings up the next. Check that the sentence left is a
  different one each round, and run the command again.
- A merged sense takes the lowest sense number of its group as the key, so
  the order of the deck no longer decides a key.
- Re-analysing a chapter changes how about one sentence in five is split, even
  between two Opus runs, and so changes card identities (about 220 of the first
  chapter's 2,500 cards each time). Do not rerun a finished chapter for a small
  prompt change.
