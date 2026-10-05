#!/usr/bin/env bash
# Fetch the two upstream sources into vendor/. Both are partial, sparse clones:
# only the files the pipeline reads are downloaded (about 80 MB, not 3.4 GB).
#
#   poke-corpus   the game's text in every language (we use FireRed/LeafGreen)
#   pokefirered   the pret decompilation: maps, scripts, trainers, moves, items
#
# Do not run `git ls-tree -l` or `git log -p` inside these clones: asking a
# partial clone for file sizes or contents makes git download everything.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p vendor

if [ ! -d vendor/poke-corpus/.git ]; then
  git clone --depth 1 --filter=blob:none --no-checkout \
    https://github.com/abcboy101/poke-corpus vendor/poke-corpus
  git -C vendor/poke-corpus sparse-checkout set --no-cone 'corpus/FireRedLeafGreen/*'
  git -C vendor/poke-corpus checkout
fi

if [ ! -d vendor/pokefirered/.git ]; then
  git clone --depth 1 --filter=blob:none --no-checkout \
    https://github.com/pret/pokefirered vendor/pokefirered
  git -C vendor/pokefirered sparse-checkout set --no-cone \
    '/data/maps/' '/data/layouts/' '/data/scripts/' '/data/text/' '/data/event_scripts.s' \
    '/data/battle_scripts_1.s' '/data/battle_scripts_2.s' \
    '/src/data/' '/include/constants/' \
    '/src/battle_message.c' '/src/battle_script_commands.c'
  git -C vendor/pokefirered checkout
fi

ls vendor/poke-corpus/corpus/FireRedLeafGreen/ja-Hrkt_msg.txt \
   vendor/pokefirered/src/data/wild_encounters.json
echo "vendor/ is ready"
