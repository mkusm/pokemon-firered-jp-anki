from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CORPUS = ROOT / "vendor" / "poke-corpus" / "corpus" / "FireRedLeafGreen"
DATA = ROOT / "data"

EXTRACT_OUT = DATA / "01_extract" / "sentences.parquet"
