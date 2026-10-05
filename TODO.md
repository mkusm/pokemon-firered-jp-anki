# What to improve next

Ideas from 2026-10-05, after every part of the deck was put on the present
analysis. Tick an item when it is done and say in one line what was done.
Numbers are from the deck at commit `29c7689d` (11,081 cards).

## Ordering

- [x] **1. Keep floating text out of the middle of a message.** 388 of the
  story's 1,469 messages of several sentences had a menu or battle line
  between their sentences (メロメロ between the two halves of the sign lady's
  line). Done 2026-10-05: floating text lands only between messages and never
  inside a walk; at most two floating lines come in a row; and the pace is
  set by what is left over the sentences left, so the stretch where nothing
  floats (the start of the game) no longer ends in a pile at the chapter's
  last line (148 in a row before, now 48).
- [x] **2. Order a map's lines by what they wait for.** The first diagnosis
  was wrong: scene variables were already followed. The gap was a line that
  waits on something its own map sets (149 messages, 104 of them listed
  before the map's ordinary lines in the text dump). Done 2026-10-05: such a
  line comes after the line shown as the thing is set, or else after the
  map's ordinary lines. 113 lines moved, in 48 maps: the Mart clerk's "come
  and see us" now follows the Potion on Route 1.
- [x] **3. Walking order inside a map.** Done 2026-10-05 (`walking.py`): a
  map's lines go in the order you walk past the people and signs that say
  them, measured over walkable tiles from the door or edge the story enters
  by. 190 of 260 maps changed. Nugget Bridge now runs from the first trainer
  to the prize; Pewter Gym from the guide to Brock to "you're champ
  material". Rematch lines stay at the end of their map. Known limits:
  people who wander or are moved by a script, water counted as walkable, and
  towns, which have no single route.
- [x] **4. More walks at first use.** Options, saving, the bag, and now:
  - [x] the first battle: done 2026-10-05, 24 lines around the rival's
    challenge in Oak's lab, with Oak's commentary in the order the battle code
    gives it
  - [x] the party screen and the summary's three pages, once you have a
    Pokémon: done 2026-10-05, 19 lines
  - [x] the Pokédex, when Oak hands it over: done 2026-10-05, 34 lines
  - [x] the Mart's buy and sell text: done 2026-10-05, 15 lines
- [x] **11. What is first seen at a chapter's last map piles up there.** Text
  that only becomes available at the gym (the leader's Pokémon, the TM) had
  no room left before the chapter ended: 48 floating lines in a row after
  Brock, 28 after Misty. Done 2026-10-05: what is still waiting when a
  chapter ends runs on into the next one (121 lines). The longest run of
  floating text is now 16, at the very end of the postgame; the others of
  that length are a trainer's Pokémon with its moves and ability.
- [x] **5. The Help deck in the Help menu's own order.** Done 2026-10-05: its
  topics first, then each question or term followed by its answer, as the
  dump's labels pair them (131 of 136 answers found their question).

## Quality

- [x] **6. Tag the boy's and the girl's lines.** Done 2026-10-05: 14 pairs of
  lines, read from the scripts' gender branches. A word that only one side
  ever meets is tagged `player-boy` (8 cards) or `player-girl` (10); nothing
  is removed. A word that also comes up in a line everyone sees is not
  tagged, so dropping a tag cannot lose a word you need.
- [x] **7. The "changed in English" notes.** Decided on 2026-10-05: leave
  them as they are. 268 of the 556 notes; 198 are on dialogue and a sample of
  those read as worth having, 70 are on descriptions and menu text and mostly
  say what the English text adds. Not worth sorting.
- [ ] **8. Loose ends.**
  - [ ] 9 words whose form is not in their sentence: the card cannot mark them
  - [ ] read the 157 `low-confidence` cards (43 of them in Link play)
  - [ ] the stiff レポート translations ("the Pokémon Save")
  - [ ] a merged sense takes the key of whichever sense comes first in the
    deck, so reordering changes keys; use the lowest sense number
- [x] **9. Tag the word lists.** Done 2026-10-05: cards on a bare word from
  the easy-chat lists are tagged `word-list`.

## Safety

- [x] **10. A small test suite.** Done 2026-10-05: `uv run python -m
  firered_anki.check`, no model calls. It checks that every line in a walk
  and every never-shown entry exists, that no message or walk is split, that
  the three decks follow one another, that card keys are unique and cards
  complete, that no Sonnet answer sits under a story or Help sentence, and
  that every chapter has nothing left to ask.
  - [x] its first run found 3 Link play cards with an empty meaning (どりの,
    しているひと, している): Sonnet had cut the sentences badly; asked again,
    it cut them properly
  - [x] and one sentence whose note Sonnet had been asked for while only the
    Link play deck showed it: the notes stage now asks again when a sentence
    comes to need Opus
