"""Where a word is in its sentence."""


def word_span(text: str, words: list[dict], pos: int, form: str | None = None) -> tuple[int, int] | None:
    """Start and end of the word at `pos`, or of `form` when it is shorter
    than the word as analysed (a particle taken off its end).

    The word is found by walking the sentence word by word, not by searching
    for its kana: か "or" in Ｌか　Ｒ must not be found inside あそびかた earlier
    in the sentence. A word can run across the game's spaces (すすんで　ください
    is one word), so the walk ignores them and the span includes them.
    """
    index = [n for n, ch in enumerate(text) if ch != "　"]
    bare = text.replace("　", "")
    form = (form if form is not None else words[pos]["surface"]).replace("　", "")
    start, i = 0, -1
    for w in words[:pos + 1]:
        surface = w["surface"].replace("　", "")
        i = bare.find(surface, start) if surface else -1
        if i >= 0:
            start = i + len(surface)
    if i < 0:
        i = bare.find(form) if form else -1
    if i < 0 or not form:
        return None
    return index[i], index[i + len(form) - 1] + 1
