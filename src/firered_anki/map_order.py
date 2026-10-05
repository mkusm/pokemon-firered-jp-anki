"""Resolve each script message to an entry in map_order.yaml.

Run as a check: uv run python -m firered_anki.map_order
Prints coverage and writes unmatched labels to data/unmapped.csv.
"""

import pandas as pd
import yaml

from .paths import DATA, EXTRACT_OUT, ROOT

MAP_ORDER = ROOT / "map_order.yaml"
UNMAPPED_OUT = DATA / "unmapped.csv"


class MapOrder:
    def __init__(self, path=MAP_ORDER):
        cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
        # An entry is a name, or a walk: {at: place, lines: [labels]}. A walk's
        # lines are seen in exactly that order at that place, whatever kind of
        # text they are (a start-menu entry, an item's name, a sign).
        self.order, self.place, self.walk = [], {}, {}
        for n, entry in enumerate(cfg["order"]):
            if isinstance(entry, dict):
                self.order += entry["lines"]
                self.place.update(dict.fromkeys(entry["lines"], entry["at"]))
                self.walk.update(dict.fromkeys(entry["lines"], n))  # which walk a line is in
            else:
                self.order.append(entry)
        self.rank = {name: i for i, name in enumerate(self.order)}
        dupes = {n for n in self.order if self.order.count(n) > 1}
        if dupes:
            raise ValueError(f"duplicate entries in {path.name}: {sorted(dupes)}")
        self.global_ = set(cfg.get("global", []))
        self.exclude = set(cfg.get("exclude", []))
        self.names = list(self.rank) + list(self.global_) + list(self.exclude)

    def resolve(self, group: str, label: str) -> tuple[str, str | None]:
        """→ (bucket, entry). bucket is order | global | exclude | unmapped.

        The longest match wins. The group (the map file the text lives in)
        counts as a match and wins ties, because labels are sometimes misnamed
        (Mansion 2F text labelled _1F_). A longer label entry such as
        Route22_Text_LateRival overrides its map, for maps revisited later in
        the story. Shared groups such as trainers go by label prefix. A label
        listed under its own name is that entry, whatever its group.
        """
        if label in self.rank:
            return "order", label
        best = group if group in self.names else None
        for name in self.names:
            if label == name or label.startswith(name + "_"):
                if best is None or len(name) > len(best):
                    best = name
        if best is None:
            return "unmapped", None
        if best in self.rank:
            return "order", best
        return ("global" if best in self.global_ else "exclude"), best


def main() -> None:
    mo = MapOrder()
    df = pd.read_parquet(EXTRACT_OUT)
    msgs = df[df["ns"] == "script"].drop_duplicates("msg_id")
    res = [mo.resolve(g, l) for g, l in zip(msgs["group"], msgs["label"])]
    msgs = msgs.assign(bucket=[r[0] for r in res], entry=[r[1] for r in res])

    print("script messages by bucket:")
    print(msgs["bucket"].value_counts().to_string())
    used = set(msgs["entry"].dropna()) | (set(df["label"]) & set(mo.place))  # a walk also lists menu text
    # Maps with no dialogue (PowerPlant) are listed for the decomp's sake.
    maps_dir = ROOT / "vendor" / "pokefirered" / "data" / "maps"
    unused = [n for n in mo.names if n not in used and not (maps_dir / n).is_dir()]
    if unused:
        print(f"entries matching nothing: {unused}")

    un = msgs[msgs["bucket"] == "unmapped"][["msg_id", "group", "label", "raw"]]
    UNMAPPED_OUT.parent.mkdir(parents=True, exist_ok=True)
    un.to_csv(UNMAPPED_OUT, index=False)
    if len(un):
        print(f"unmapped by group: {un['group'].value_counts().to_dict()}")
    print(f"→ {UNMAPPED_OUT.relative_to(ROOT)} ({len(un)} rows)")


if __name__ == "__main__":
    main()
