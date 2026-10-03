#!/usr/bin/env python3
"""Machine-hunt event: Mechanical-trait kills per energy.

The event pays points per mechanical enemy killed — 3 pts in Standard/Mirror,
5 pts in Elite/MirrorElite (gameconfig trackers) — so Pt/E is the reward rate.
Enemy Mechanical trait comes from planner npcs/*.json, per-node enemy
lists + loot from campaign-battles/*.json. `Att` = attempts left today for
that exact node, straight from the player snapshot (`progress.campaigns`,
cap 10 standard / 6 elite); nodes with 0 left are hidden unless `--all`,
`?` = no attempt data (event campaigns aren't in the snapshot).

--items adds the balance view: rank nodes that drop leaves your roster's next
rank-ups still need (via `rank_up_report --energy --json` — the wanted set is
tier_detail[0] of every row, kept per hero; labels come from the upgrades
catalog; this tool prices nothing itself).
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from rank_up_report import run  # noqa: E402
import energy_cost  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "research/tacticus-planner-api/src/TacticusPlanner.GameCatalog/Data"


def pts_rate(trait_type):
    """Event points per mechanical kill (gameconfig killUnits trackers)."""
    return 5 if trait_type in ("Elite", "EliteMirror") else 3


def mech_ids():
    ids = set()
    for f in sorted((DATA / "npcs").glob("*.json")):
        for n in json.load(open(f))["npcs"]:
            if "Mechanical" in (n.get("traits") or []):
                ids.add(n["id"])
    return ids


def attempts():
    """(groupId, battleIndex) -> attemptsLeft, from the player snapshot."""
    doc = json.load(open(HERE / "tacticus-player.json"))
    return {(c["id"], b["battleIndex"]): b["attemptsLeft"]
            for c in doc["player"]["progress"]["campaigns"]
            for b in c["battles"]}


def _occurrences():
    """(definitionId, start, end) for every planned occurrence (planner events data)."""
    f = DATA / "events" / "event-occurrences.json"
    if not f.exists():
        return []
    out = []
    for o in json.load(open(f)):
        out.append((o.get("definitionId"),
                    datetime.fromisoformat(o["startUtc"].replace("Z", "+00:00")),
                    datetime.fromisoformat(o["endUtc"].replace("Z", "+00:00"))))
    return out


def event_window(now):
    """End of the live machine-hunt occurrence, or None."""
    for did, start, end in _occurrences():
        if did == "hse-machine-hunt" and start <= now <= end:
            return end
    return None


def event_list(now, cap=4):
    """Live events first, then upcoming: [{id, start, end, live}]."""
    evs = sorted((e for e in _occurrences() if e[2] >= now),
                 key=lambda e: (e[1] > now, e[1]))
    return [{"id": d, "start": s.isoformat(), "end": e.isoformat(),
             "live": s <= now <= e} for d, s, e in evs[:cap]]


def nodes(mech, att):
    rows = []
    for f in sorted((DATA / "campaign-battles").glob("*.json")):
        camp = f.stem[len("campaign-battles-"):]
        doc = json.load(open(f))
        for b in doc["battles"]:
            raw = b.get("rawEnemyTypes") or [
                {"id": e["id"], "count": e["count"]}
                for e in b.get("detailedEnemyTypes", [])
            ]
            m = sum(e["count"] for e in raw if e["id"].split(":")[0] in mech)
            if not m:
                continue
            rew = b.get("rewards") or {}
            drops = list(dict.fromkeys(  # same leaf can sit in both reward lists
                p["id"]
                for side in ("potential", "guaranteed")
                for p in (rew.get(side) or [])
                if str(p.get("id", "")).startswith("upg")
            ))
            rows.append({
                "campaign": camp, "id": b["id"], "type": b.get("type", ""),
                "energy": b["energyCost"], "mech": m,
                "foes": sum(e["count"] for e in raw), "drops": drops,
                "att": att.get((doc.get("groupId"), b["nodeNumber"] - 1)),
            })
    return rows


def wanted():
    """leaf id -> {hero name: still-to-farm count} across every next rung."""
    r = subprocess.run(
        [sys.executable, str(HERE / "rank_up_report.py"), "--energy", "--json"],
        capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"rank_up_report --energy failed:\n{r.stderr.strip()}")
    want = {}
    for row in json.loads(r.stdout)["rows"]:
        td = row.get("tier_detail") or []
        for leaf, info in ((td[0].get("leaves") if td else None) or {}).items():
            if info.get("need", 0) > 0:
                want.setdefault(leaf, {})[row["name"]] = info["need"]
    return want


def selftest():
    mech = mech_ids()
    att = attempts()
    rows = nodes(mech, att)
    assert len(mech) >= 90, len(mech)
    assert len(rows) >= 250, len(rows)
    assert all(0 < r["mech"] <= r["foes"] and r["energy"] >= 0 for r in rows)
    assert len(att) >= 500 and all(v >= 0 for v in att.values())
    # permanent campaigns resolve an attempts value; event ones don't
    perm = [r for r in rows if r["campaign"] in ("indomitus", "octarius")]
    assert perm and all(r["att"] is not None for r in perm)
    indo = [r for r in rows if r["campaign"] == "indomitus"]
    assert any(r["mech"] == r["foes"] for r in indo)  # pure-Necron nodes
    w = wanted()
    assert w and all(sum(v.values()) > 0 for v in w.values())
    now = datetime.now(timezone.utc)
    event_window(now)  # events data parses
    for e in event_list(now):
        assert set(e) == {"id", "start", "end", "live"} and e["id"]
    assert pts_rate("Elite") == 5 and pts_rate("EliteMirror") == 5
    assert pts_rate("Standard") == 3 and pts_rate("Mirror") == 3 and pts_rate("Extremis") == 3
    cat = energy_cost.load_upgrades()
    assert all(i in cat and cat[i].get("label") for i in w)  # every wanted leaf is named
    print(f"selftest OK: {len(mech)} mechanical npc ids, {len(rows)} nodes, "
          f"{len(att)} attempt slots, {len(w)} wanted leaves")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("campaign", nargs="?", help="filter: substring of campaign id (e.g. indomitus)")
    ap.add_argument("--items", action="store_true",
                    help="balance: factor in items missing for next rank-ups")
    ap.add_argument("--all", action="store_true",
                    help="also show nodes with 0 attempts left today")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0

    mech = mech_ids()
    rows = nodes(mech, attempts())
    now = datetime.now(timezone.utc)
    ends = event_window(now)
    evs = event_list(now)
    if a.campaign:
        rows = [r for r in rows if a.campaign.lower() in r["campaign"]]
    if not a.all:
        rows = [r for r in rows if r["att"] != 0]  # exhausted today
    want = wanted() if a.items else {}
    label = ({i: v.get("label", i) for i, v in energy_cost.load_upgrades().items()}
             if a.items else {})

    for r in rows:
        r["mech_per_e"] = float("inf") if r["energy"] == 0 else r["mech"] / r["energy"]
        r["pts"] = pts_rate(r["type"]) * r["mech"]
        r["pt_per_e"] = float("inf") if r["energy"] == 0 else r["pts"] / r["energy"]
        hits = [d for d in r["drops"] if d in want]
        r["want"] = [
            {"id": d, "name": label.get(d, d),
             "heroes": dict(sorted(want[d].items(), key=lambda kv: -kv[1]))}
            for d in hits]
        r["need"] = sum(sum(want[d].values()) for d in hits)
    # balance mode: dual-purpose first, then event reward rate, then need; ties -> more attempts left
    rows.sort(key=lambda r: (bool(r["want"]), r["pt_per_e"], r["need"],
                             r["att"] if r["att"] is not None else 99, r["mech"]),
              reverse=True)
    rows = rows[:a.top]

    if a.json:
        out = [{**r,
                "mech_per_e": None if r["mech_per_e"] == float("inf") else r["mech_per_e"],
                "pt_per_e": None if r["pt_per_e"] == float("inf") else r["pt_per_e"]}
               for r in rows]  # 0-energy = null (JSON has no Infinity)
        print(json.dumps({"schema_version": 1, "event_ends": ends.isoformat() if ends else None,
                          "events": evs, "rows": out}, indent=1))
        return 0
    if ends:
        left = ends - datetime.now(timezone.utc)
        print(f"machine hunt LIVE — ends {ends:%Y-%m-%d %H:%M} UTC "
              f"({left.days}d {left.seconds // 3600}h left)")
    print("Mechanical kills per energy (3 pts/kill std+mirror, 5 elite; Att = attempts left today)"
          + (" — Want = drops for next rank-ups" if a.items else ""))
    hdr = f"{'Pt/E':>6} {'Node':<8} {'Mech/E':>7} {'Pts':>4} {'Mech':>4} {'Foes':>4} {'E':>4} {'Att':>4}"
    if a.items:
        hdr += f" {'Want':>4} {'Need':>4}"
    print(hdr + f" {'Type':<12} Campaign")
    for r in rows:
        rate = "inf" if r["mech_per_e"] == float("inf") else f"{r['mech_per_e']:.1f}"
        prate = "inf" if r["pt_per_e"] == float("inf") else f"{r['pt_per_e']:.1f}"
        line = f"{prate:>6} {r['id']:<8} {rate:>7} {r['pts']:>4} {r['mech']:>4} {r['foes']:>4} {r['energy']:>4} {str(r['att'] if r['att'] is not None else '?'):>4}"
        if a.items:
            line += f" {len(r['want']):>4} {r['need']:>4}"
        print(f"{line} {r['type']:<12} {r['campaign']}")
        if a.items and r["want"]:
            detail = "; ".join(
                f"{w['name']} [{w['id']}] — "
                + ", ".join(f"{h} {n}" for h, n in w["heroes"].items())
                for w in r["want"])
            print(f"{'':>14} ^ {detail}")
    if a.items and any(r["want"] for r in rows):
        # totals over DISTINCT items shown (a leaf dropped by several nodes counts once)
        ids = list({w["id"] for r in rows for w in r["want"]})
        sums = {i: sum(want[i].values()) for i in ids}
        by_item = sorted(((label.get(i, i), s) for i, s in sums.items()),
                         key=lambda kv: (-kv[1], kv[0]))
        by_hero = {}
        for i in ids:
            for h, n in want[i].items():
                by_hero[h] = by_hero.get(h, 0) + n
        heroes = sorted(by_hero.items(), key=lambda kv: (-kv[1], kv[0]))
        print(f"Need total {sum(sums.values())}: "
              + ", ".join(f"{n} {c}" for n, c in by_item))
        more = f" (+{len(heroes) - 8} more)" if len(heroes) > 8 else ""
        print("By hero: " + ", ".join(f"{h} {n}" for h, n in heroes[:8]) + more)
    return 0


if __name__ == "__main__":
    run(main)
