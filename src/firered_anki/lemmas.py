"""Rough content-word lemmas from fugashi + UniDic, for previews before the LLM
pass. Kana-only text makes these guesses; the analyse stage replaces them."""

from functools import lru_cache

import fugashi

CONTENT = {"名詞", "動詞", "形容詞", "副詞", "形状詞", "連体詞", "感動詞", "代名詞", "接続詞"}
SKIP = {"固有名詞", "数詞"}

_tagger = None


@lru_cache(maxsize=None)
def lemmas(text: str) -> frozenset:
    global _tagger
    _tagger = _tagger or fugashi.Tagger()
    return frozenset(
        w.feature.lemma or w.surface
        for w in _tagger(text)
        if w.feature.pos1 in CONTENT and w.feature.pos2 not in SKIP
    )
