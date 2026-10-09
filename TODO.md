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

- [x] **12. What can be read before a blocker comes before it.** Done
  2026-10-08 (`blockers.py`): where a tile's script stops you until its scene
  has run, the lines still within reach on your side come first. 115 messages
  moved before four scenes (Cerulean's rival, Silph Co.'s, the S.S. Anne's,
  Three Island's bikers); a blocker that only guards a side room (the Pewter
  Museum's counter) is not used. The rival's house
  and Oak's lab come before Oak stops you in the grass, with the save walk
  after the lab's sign. Tile triggers and hidden people now count as
  conditions too: 143 messages come after what they wait for (the catching
  lesson after the parcel, Mr. Fuji after the Tower). `check` tests both.
- [x] **13. What lies behind a blocker comes after it.** The other half of
  12, done by hand on 2026-10-08: three scenes are listed in
  `map_order.yaml` (Mt. Moon's fossils after the man who guards them, the
  Silph president after Giovanni, the camper west of Nugget Bridge after the
  Rocket). No rule: the walk in `blockers.py` proves what can be reached,
  not what cannot. With the three listed, the blocker rule judges 13 of the
  25 scenes.
- [x] **14. Side content before main content.** Two rules measured on
  2026-10-08. "In a room, whoever moves the story on comes last" would have
  moved 49 lines in 12 maps, about half of them wrongly, so the seven rooms
  where it is right are listed by hand in `map_order.yaml` instead (15
  lines). "A town's gym after the rest of the town" already holds: the only
  maps listed after their town's gym are Cerulean's robbed house, which is
  locked until then, and the Safari Zone's gate and office in Fuchsia, which
  the walkthrough visits after Koga.
  - Dead ends before the way on: inside one map nothing to do (all of a
    map's lines come before the next map's), and across maps it is the
    question below.
  - [x] Places that can be walked to before the walkthrough goes there,
    measured by walking from the bedroom at 40 moments of the story. Moved
    on 2026-10-08, at the owner's "use your judgment": Saffron's Pokémon
    Center, Mart and Mr. Psychic's house to the first walk through the city,
    and the house that gives Fly to Celadon. Left where the walkthrough has
    them: the Fighting Dojo and the Fan Club, the rest of Route 16, the
    beach of Route 19.

- [x] **15. Lines shown while their speaker cannot be reached.** Done
  2026-10-08 (`reach.py`, a report). The walking order knows nothing of what
  stands in the way: a blocker, a person, a tree, water. Walking the game
  from the bedroom at the moment each line is shown found 35 lines in nine
  places that were too early, now listed by hand where they open: Viridian's
  gym corner, the Rocket behind Cerulean's robbed house, the Pewter Museum's
  back room, a house in Saffron, the gate guards' greeting, the Diglett's
  Cave sign, and three places that need Surf. 26 lines it lists wrongly are
  named in the module with the reason; 77 it cannot judge.
  - [ ] It is a report, not part of `check`. To make it one, the 26 known
    lines would have to be told apart by rule (a scene that walks you in, a
    person a script has moved).

- [x] **16. Lines read by looking at a kind of thing.** Done 2026-10-09: the
  28 lines of the `flavor_text` group were placed by a guess from their
  names. The game picks them by the kind of tile you face, so 23 now go to
  the first map of the route that has such a tile (`decomp.looked_at`):
  "what kind of machine is this" moved from the Power Plant to Oak's lab,
  the advertising poster from Celadon to Viridian's Mart, the telephone from
  Pallet Town to Celadon's Department Store.
  - [x] Five of them no tile can show (`Text_ImpressiveMachine`,
    `Text_VideoGame`, `Text_Snacks`, `Text_PolishedWindow`,
    `Text_BeautifulSkyWindow`). Left out on 2026-10-09 at the owner's word,
    under `never_shown`; four cards went with them (してある, すごそうな, まど,
    みがかれた). The deck has 11,080 cards.
  - [ ] The Poké Mart and Pokémon Center signs, the wall map and the Indigo
    Plateau signs are picked by tests this does not read yet; they keep
    their guessed places, which look right.

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
- [x] **8. Loose ends.** All done 2026-10-05.
  - [x] 9 words whose form is not in their sentence. Eight were a false
    alarm: names written with a space (ユニオン　ルーム), which the flag did
    not allow for; it does now. The ninth, ナウい for ナウイ, is a hand
    correction.
  - [x] read the 155 `low-confidence` cards. They are what the tag is for:
    cries and sound words, garbled and stammered speech, sentence-end
    particles with no dictionary entry, compound verbs JMdict lacks. No
    wrong gloss found. In Link play Sonnet also leaves some verb chains
    whole (みせてあげる) where Opus would cut them.
  - [x] the stiff レポート translations: seven hand corrections, each keeping
    both "save" and "report"
  - [x] a merged sense now takes the lowest sense number as its key, so
    reordering cannot change keys. 278 cards changed key once for this.
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
