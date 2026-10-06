#!/usr/bin/env python3
"""Build the browser bundle for the Pyodide-hosted reports.

Everything the witness CLIs open at runtime, mirrored at repo-root layout so
BASE = Path(__file__).parent keeps working after extraction:

    *.py, tacticus-drop-rates.json, tacticus-shop-prices.json,
    research/tacticus-planner-api/.../Data/**, research/datamine/gameconfig142.json,
    research/misc/bundle.js  (the liveEventConfig blocks home_screen_event slices)

Deliberately EXCLUDED: tacticus-player.json (roster - the visitor fetches
their own through the Worker) and .tacticus_api_key (never leaves this box).
"""
import io
import pathlib
import tarfile

BASE = pathlib.Path(__file__).resolve().parent
OUT = BASE / "dist" / "bundle.tar.gz"

# roster/credentials must never enter the bundle
FORBIDDEN = {"tacticus-player.json", ".tacticus_api_key", "swagger-ui.html"}

# home_screen_event slices its event config out of the shipped client bundle.
# The source is 7 MB of minified JS; the blocks it reads are ~6 KB, so the
# bundle carries only the blocks — same filename and path, so the tool has no
# second code path and local runs still read the real file.
BUNDLE_JS = BASE / "research" / "misc" / "bundle.js"


def event_block_slice(src, tier):
    """The `liveEventConfig:{...}` block for one client tier config, verbatim.

    Same bounds home_screen_event.event_block() uses, so both agree on what
    "the block" is: end at the offers object that follows the reward track.
    """
    i = src.find(f'eventName:"{tier}"')
    if i < 0:
        return ""
    start = src.rindex("liveEventConfig:{", 0, i)   # eventName is a field of it
    end = src.find(",offers:{", i)
    return src[start:end if end > 0 else len(src)]


def slim_bundle_js():
    """(bytes, [tier,...]) of the event blocks, read from EVENTS so a third
    home-screen event ships without touching this file."""
    import home_screen_event
    src = BUNDLE_JS.read_text(encoding="utf8", errors="replace")
    tiers = [v["tier"] for v in home_screen_event.EVENTS.values()]
    blocks = [event_block_slice(src, t) for t in tiers]
    missing = [t for t, b in zip(tiers, blocks) if not b]
    assert not missing, f"tier config not found in bundle.js: {missing}"
    return ("\n".join(blocks)).encode("utf8"), tiers


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
    slim, tiers = slim_bundle_js()
    arc = str(BUNDLE_JS.relative_to(BASE))
    with tarfile.open(OUT, "w:gz") as tf:
        for p in files:
            tf.add(p, arcname=str(p.relative_to(BASE)))
        info = tarfile.TarInfo(arc)
        info.size = len(slim)
        tf.addfile(info, io.BytesIO(slim))
    # self-check: no forbidden name inside, extraction set is complete
    with tarfile.open(OUT, "r:gz") as tf:
        names = tf.getnames()
        assert tf.extractfile(arc).read() == slim
    bad = [n for n in names if pathlib.Path(n).name in FORBIDDEN]
    assert not bad, f"forbidden files leaked: {bad}"
    assert "research/datamine/gameconfig142.json" in names
    assert arc in names, "slim bundle.js missing - home_screen_event dies in-browser"
    assert any(n.startswith("research/tacticus-planner-api/") for n in names), (
        "planner Data missing - research/ not fetched?")
    assert any(n.endswith("rank_up_report.py") for n in names)
    print(f"{OUT}: {OUT.stat().st_size / 1e6:.1f} MB gz, {len(names)} files, "
          f"bundle.js {len(slim) / 1e3:.0f} KB ({len(tiers)} events: {', '.join(tiers)})")


if __name__ == "__main__":
    main()
