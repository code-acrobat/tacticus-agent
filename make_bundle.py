#!/usr/bin/env python3
"""Build the browser bundle for the Pyodide-hosted reports.

Everything the witness CLIs open at runtime, mirrored at repo-root layout so
BASE = Path(__file__).parent keeps working after extraction:

    *.py, tacticus-drop-rates.json, tacticus-shop-prices.json,
    research/tacticus-planner-api/.../Data/**, research/datamine/gameconfig142.json

Deliberately EXCLUDED: tacticus-player.json (roster - the visitor fetches
their own through the Worker) and .tacticus_api_key (never leaves this box).
"""
import pathlib
import tarfile

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "dist" / "bundle.tar.gz"

# roster/credentials must never enter the bundle
FORBIDDEN = {"tacticus-player.json", ".tacticus_api_key", "swagger-ui.html"}


def members():
    for p in sorted(BASE.glob("*.py")):
        if p.name not in FORBIDDEN:
            yield p
    for name in ("tacticus-drop-rates.json", "tacticus-shop-prices.json"):
        yield BASE / name
    data = (BASE / "research" / "tacticus-planner-api" / "src" /
            "TacticusPlanner.GameCatalog" / "Data")
    yield from sorted(data.rglob("*"))
    yield BASE / "research" / "datamine" / "gameconfig142.json"


def main():
    OUT.parent.mkdir(exist_ok=True)
    files = [p for p in members() if p.is_file()]
    with tarfile.open(OUT, "w:gz") as tf:
        for p in files:
            tf.add(p, arcname=str(p.relative_to(BASE)))
    # self-check: no forbidden name inside, extraction set is complete
    with tarfile.open(OUT, "r:gz") as tf:
        names = tf.getnames()
    bad = [n for n in names if pathlib.Path(n).name in FORBIDDEN]
    assert not bad, f"forbidden files leaked: {bad}"
    assert "research/datamine/gameconfig142.json" in names
    assert any(n.startswith("research/tacticus-planner-api/") for n in names), (
        "planner Data missing - research/ not fetched?")
    assert any(n.endswith("rank_up_report.py") for n in names)
    print(f"{OUT}: {OUT.stat().st_size / 1e6:.1f} MB gz, {len(names)} files")


if __name__ == "__main__":
    main()
