"""Kana → Hepburn romaji for the cards.

The game text is kana, so this is a plain table conversion. Particles read
differently from their spelling (は wa, へ e, を o); the caller says which
words are particles. A katakana long mark becomes a macron (ボール bōru);
hiragana long vowels stay as written (おう ou), so the romaji maps back to
the kana letter by letter.
"""

import re

BASE = {
    "あ": "a", "い": "i", "う": "u", "え": "e", "お": "o",
    "か": "ka", "き": "ki", "く": "ku", "け": "ke", "こ": "ko",
    "さ": "sa", "し": "shi", "す": "su", "せ": "se", "そ": "so",
    "た": "ta", "ち": "chi", "つ": "tsu", "て": "te", "と": "to",
    "な": "na", "に": "ni", "ぬ": "nu", "ね": "ne", "の": "no",
    "は": "ha", "ひ": "hi", "ふ": "fu", "へ": "he", "ほ": "ho",
    "ま": "ma", "み": "mi", "む": "mu", "め": "me", "も": "mo",
    "や": "ya", "ゆ": "yu", "よ": "yo",
    "ら": "ra", "り": "ri", "る": "ru", "れ": "re", "ろ": "ro",
    "わ": "wa", "ゐ": "i", "ゑ": "e", "を": "o", "ん": "n",
    "が": "ga", "ぎ": "gi", "ぐ": "gu", "げ": "ge", "ご": "go",
    "ざ": "za", "じ": "ji", "ず": "zu", "ぜ": "ze", "ぞ": "zo",
    "だ": "da", "ぢ": "ji", "づ": "zu", "で": "de", "ど": "do",
    "ば": "ba", "び": "bi", "ぶ": "bu", "べ": "be", "ぼ": "bo",
    "ぱ": "pa", "ぴ": "pi", "ぷ": "pu", "ぺ": "pe", "ぽ": "po",
    "ゔ": "vu",
    "ぁ": "a", "ぃ": "i", "ぅ": "u", "ぇ": "e", "ぉ": "o",
    "ゃ": "ya", "ゅ": "yu", "ょ": "yo", "ゎ": "wa",
}
# Two-kana combinations (after katakana → hiragana).
COMBO = {
    "きゃ": "kya", "きゅ": "kyu", "きょ": "kyo", "ぎゃ": "gya", "ぎゅ": "gyu", "ぎょ": "gyo",
    "しゃ": "sha", "しゅ": "shu", "しょ": "sho", "じゃ": "ja", "じゅ": "ju", "じょ": "jo",
    "ちゃ": "cha", "ちゅ": "chu", "ちょ": "cho", "ぢゃ": "ja", "ぢゅ": "ju", "ぢょ": "jo",
    "にゃ": "nya", "にゅ": "nyu", "にょ": "nyo", "ひゃ": "hya", "ひゅ": "hyu", "ひょ": "hyo",
    "びゃ": "bya", "びゅ": "byu", "びょ": "byo", "ぴゃ": "pya", "ぴゅ": "pyu", "ぴょ": "pyo",
    "みゃ": "mya", "みゅ": "myu", "みょ": "myo", "りゃ": "rya", "りゅ": "ryu", "りょ": "ryo",
    "しぇ": "she", "じぇ": "je", "ちぇ": "che",
    "ふぁ": "fa", "ふぃ": "fi", "ふぇ": "fe", "ふぉ": "fo", "ふゅ": "fyu",
    "てぃ": "ti", "でぃ": "di", "とぅ": "tu", "どぅ": "du", "てゅ": "tyu", "でゅ": "dyu",
    "うぃ": "wi", "うぇ": "we", "うぉ": "wo",
    "ゔぁ": "va", "ゔぃ": "vi", "ゔぇ": "ve", "ゔぉ": "vo",
    "つぁ": "tsa", "つぃ": "tsi", "つぇ": "tse", "つぉ": "tso",
}
PARTICLE = {"は": "wa", "へ": "e", "を": "o"}
# Particles before a topic は (には ni wa), and greetings spelled with は.
BEFORE_WA = {"に", "で", "と", "の", "へ", "から", "まで", "より", "て", "って", "こそ", "だけ"}
WA_WORDS = {"こんにちは": "konnichiwa", "こんばんは": "konbanwa"}
MACRON = str.maketrans("aiueo", "āīūēō")
PUNCT = {
    "。": ".", "！": "!", "？": "?", "、": ",", "…": "…", "・": " ", "「": "“", "」": "”",
    "『": "“", "』": "”", "（": "(", "）": ")", "：": ":", "～": "~", "＊": "…", "　": " ",
    "円": "en", "♂": "♂", "♀": "♀", "ー": "-",
}


def _hira(s: str) -> str:
    return "".join(chr(ord(c) - 0x60) if "ァ" <= c <= "ヶ" else c for c in s)


def _half(c: str) -> str:
    """Full-width letters and digits → ASCII."""
    return chr(ord(c) - 0xFEE0) if "！" <= c <= "～" else c


def romaji(text: str, particle: bool = False) -> str:
    if text in WA_WORDS:
        return WA_WORDS[text]
    if particle:
        if text in PARTICLE:
            return PARTICLE[text]
        if text.endswith("は") and text[:-1] in BEFORE_WA:
            return romaji(text[:-1], particle=True) + " wa"
    s = _hira(text)
    out: list[str] = []
    double = False
    i = 0
    while i < len(s):
        c = s[i]
        if c == "っ":
            double = True
            i += 1
            continue
        if c == "ー" and out and out[-1][-1:] in "aiueo":
            out[-1] = out[-1][:-1] + out[-1][-1].translate(MACRON)
            i += 1
            continue
        piece = COMBO.get(s[i:i + 2])
        if piece:
            i += 2
        else:
            piece = BASE.get(c)
            i += 1
            if piece is None:
                piece = PUNCT.get(c, _half(c))
        if c == "ん" and i < len(s) and (BASE.get(s[i], "x")[0] in "aiueoy"):
            piece = "n'"
        if double and piece[:1].isalpha():
            piece = ("t" if piece.startswith("ch") else piece[0]) + piece
        double = False
        out.append(piece)
    return "".join(out)


def sentence_romaji(text: str, words: list[dict], particles: set[int]) -> str:
    """The sentence with spaces between words. `words` are the analysed words
    in order (dicts with `surface`); `particles` holds the indexes that are
    particles."""
    tokens: list[str] = []
    pos = 0

    def gap(g: str) -> None:
        g = romaji(g).strip()
        if not g:
            return
        if tokens and not re.search(r"[a-zāīūēō0-9]", g, re.I):
            tokens[-1] += g  # punctuation hugs the word before it
        else:
            tokens.append(g)

    for n, w in enumerate(words):
        i = text.find(w["surface"], pos)
        if i < 0:
            continue
        gap(text[pos:i])
        tokens.append(romaji(w["surface"], particle=n in particles))
        pos = i + len(w["surface"])
    gap(text[pos:])
    return re.sub(r"\s+", " ", " ".join(tokens)).strip()
