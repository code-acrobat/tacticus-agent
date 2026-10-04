#!/usr/bin/env python3
"""The ancestors are watching (Uthar LRES): teams, power walls, mission farms.

Three tracks (alpha/beta/gamma), 18 battles each: lane objectives pay per-hero
bonus points, milestones pay engram -> chests -> shards toward Uthar. Per lane
this prints the next uncleared battle, the best owned specialist team, and the
power ladder - your strongest allowed 5 vs each tier's difficulty, so you walk
up to the wall instead of trying it early or farming a low-point tier. Also
prints the event window (derived from the planner's Fixed legendary-event
recurrence via machine_hunt.event_list) and the machine-hunt overlap (beta
fights machines while that event is live).

Usage:  python3 lres_report.py [alpha|beta|gamma] [--json] [--selftest]
Reads:  research/.../lres/lres-votanuthar.json + units + campaign-battles,
        tacticus-player.json (lanes progress, campaign attempts), power_delta.py.
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "research/tacticus-planner-api/src/TacticusPlanner.GameCatalog/Data"
LRES_FILE = DATA / "lres/lres-votanuthar.json"
EVENT_ID = "votanUthar"
ALL_LANES = ("alpha", "beta", "gamma")
# lane alliance exclusion: track -> grandAlliance you may NOT field
EXCL_ALLIANCE = {"alpha": "Xenos", "beta": "Imperial", "gamma": "Chaos"}
# "Defeat N <label>" mission -> how to farm it (alphanumeric-normalised match
# over campaign node enemy factions / alliances)
FARM_KEY = {
    "necrons": ("faction", "necrons"),
    "admechs": ("faction", "adeptusmechanicus"),
    "death guard": ("faction", "deathguard"),
    "chaos": ("alliance", "chaos"),
    "imperial": ("alliance", "imperial"),
    "xenos": ("alliance", "xenos"),
}


def norm(s):
    return "".join(ch for ch in s.lower() if ch.isalnum())


def load_player():
    return json.load(open(HERE / "tacticus-player.json"))["player"]


def catalog():
    """planner characters: id -> traits / hits / damage types."""
    cat = {}
    for f in sorted((DATA / "units").glob("units-*.json")):
        for c in json.load(open(f)).get("characters", []):
            dmg = set()
            for k in ("meleeDamage", "rangedDamage"):
                if c.get(k):
                    dmg.add(c[k])
            for k in ("activeAbilityDamage", "passiveAbilityDamage"):
                for d in (c.get(k) or []):
                    dmg.add(d)
            cat[c["id"]] = {"traits": set(c.get("traits") or []),
                            "melee": c.get("meleeHits") or 0,
                            "ranged": c.get("rangedHits") or 0,
                            "dmg": dmg}
    return cat


def hero_power():
    """absolute hero power (planner combat-power) for every unit."""
    out = subprocess.run([sys.executable, str(HERE / "power_delta.py"), "--json"],
                         capture_output=True, text=True, check=True)
    return {r["id"]: r["power_now"] for r in json.loads(out.stdout)["rows"]}


def locked(u):
    # no rank-up progress at all and no shards banked -> treat as not built
    return u.get("rank", 0) == 0 and not u.get("upgrades")


def strength(u):
    return (u.get("rank", 0), u.get("progressionIndex", 0))


def matches(obj, uid, u, cat):
    """Does unit uid satisfy one lane objective? (Acing = play skill, auto-true)."""
    kind, tgt = obj["objectiveType"], obj.get("objectiveTarget", "")
    if kind == "Acing":
        return True
    if kind == "HasNoRangedAttack":
        return not cat[uid]["ranged"]
    if kind == "HasRangedAttack":
        return bool(cat[uid]["ranged"])
    if kind == "NotDamageType":
        return tgt not in cat[uid]["dmg"]
    if kind == "Faction":
        return u["faction"] == tgt
    if kind == "Trait":          # exclude flag applied by the caller
        return tgt in cat[uid]["traits"]
    if kind == "NotTrait":
        return tgt not in cat[uid]["traits"]
    if kind == "MaxHits":
        return max(cat[uid]["melee"], cat[uid]["ranged"]) <= int(tgt)
    if kind == "MinHits":
        return max(cat[uid]["melee"], cat[uid]["ranged"]) >= int(tgt)
    if kind == "AttackType":
        return bool(cat[uid]["ranged"]) if tgt == "Ranged" else bool(cat[uid]["melee"])
    if kind == "DamageType":
        return tgt in cat[uid]["dmg"]
    return False


def obj_label(o):
    lbl = str(o.get("objectiveTarget") or o["objectiveType"])
    if o["objectiveType"].startswith("Not"):
        return "No" + lbl
    if o["objectiveType"] == "HasNoRangedAttack":
        return "NoRanged"
    return lbl


def allowed(uid, u, disallowed, track, owned):
    if uid not in owned or locked(u):
        return False
    if u["faction"] in disallowed:
        return False
    return u["grandAlliance"] != EXCL_ALLIANCE[track]


def event_window():
    """The live/upcoming legendary-event window [{id,start,end,live}], or None."""
    try:
        from machine_hunt import event_list
        return next((e for e in event_list(datetime.now(timezone.utc))
                     if e["id"] == "legendary-event"), None)
    except Exception:
        return None


def mission_targets(lres):
    """Defeat-N missions -> farm keys (skips waves/damage/ability missions)."""
    pats = [  # 'Defeat 30 Necrons'  |  'Defeat 30 enemies with Chaos units'
        re.compile(r"Defeat\s+(\d+)\s+([A-Za-z ]+)$"),
        re.compile(r"Defeat\s+(\d+)\s+enemies with\s+([A-Za-z]+) units$"),
    ]
    out, seen = [], set()
    for s in lres.get("regularMissions", []) + lres.get("premiumMissions", []):
        for pat in pats:
            m = pat.match(s.strip())
            if not m:
                continue
            label = m.group(2)
            if label.lower() not in FARM_KEY:
                continue        # matched the shape, not a kill mission - try next
            if label not in seen:
                seen.add(label)
                mode, key = FARM_KEY[label.lower()]
                out.append({"need": int(m.group(1)), "label": label,
                            "mode": mode, "key": key})
            break
    return out


def farm_rows(player, missions, cap=3):
    """Best campaign nodes per mission: (kills/run x attempts) / energy."""
    att = {(c["id"], b["battleIndex"]): b["attemptsLeft"]
           for c in player["progress"]["campaigns"] for b in c["battles"]}
    for mis in missions:
        rows = []
        for f in sorted((DATA / "campaign-battles").glob("*.json")):
            doc = json.load(open(f))
            camp = doc.get("groupId", f.stem)
            for b in doc["battles"]:
                fr = [norm(x) for x in (b.get("enemiesFactions") or [])]
                fa = [norm(x) for x in (b.get("enemiesAlliances") or [])]
                hit = (any(mis["key"] in x for x in fr) if mis["mode"] == "faction"
                       else mis["key"] in fa)
                if not hit:
                    continue
                left = att.get((camp, b.get("nodeNumber")))
                if not left:
                    continue
                kills = sum(e["count"] for e in (b.get("rawEnemyTypes") or []))
                e = b.get("energyCost") or 6
                if kills:
                    rows.append({"camp": camp, "node": b.get("nodeNumber"),
                                 "per_run": kills, "att": left, "energy": e,
                                 "score": kills * left / e})
        rows.sort(key=lambda r: -r["score"])
        mis["rows"] = rows[:cap]
    return missions


def analyse(lane_filter=None):
    player = load_player()
    lres = json.load(open(LRES_FILE))
    cat = catalog()
    PW = hero_power()
    units = {u["id"]: u for u in player["units"]}
    owned = {i for i in units if i in cat}
    ev = next(e for e in player["progress"]["legendaryEvents"]
              if e["id"] == EVENT_ID)

    # ---- event state -------------------------------------------------------
    left_ms = [m for m in lres["pointsMilestones"]
               if m["cumulativePoints"] > ev["currentPoints"]]
    chest0 = lres["chestsMilestones"][0]
    shards_needed = max(0, 400 - ev["currentShards"])
    need_chests = -(-shards_needed // lres["shardsPerChest"])  # ceil
    next_ms = left_ms[0] if left_ms else None
    meta = {
        "event": "The ancestors are watching",
        "unit": lres.get("unitSnowprintId", EVENT_ID),
        "window": event_window(),
        "points": ev["currentPoints"],
        "engram": ev["currentCurrency"],
        "shards": ev["currentShards"],
        "shards_to_unlock": 400,
        "next_milestone": ({"milestone": next_ms["milestone"],
                            "points": next_ms["cumulativePoints"],
                            "engram": next_ms["engramPayout"],
                            "to_go": next_ms["cumulativePoints"] - ev["currentPoints"]}
                           if next_ms else None),
        "chest": {"engram_cost": chest0["engramCost"],
                  "shards": lres["shardsPerChest"]},
        "outlook": {
            "milestones_left": len(left_ms),
            "engram_left": sum(m["engramPayout"] for m in left_ms),
            "banked": ev["currentCurrency"],
            "engram_total": (sum(m["engramPayout"] for m in left_ms)
                             + ev["currentCurrency"]),
            "shards_needed": shards_needed,
            "chests_needed": need_chests,
            "engram_for_chests": need_chests * chest0["engramCost"],
        },
        "machine_hunt_overlap": "beta fights machines - kills count for both",
    }

    # ---- lanes -------------------------------------------------------------
    lanes = []
    for track in ALL_LANES:
        if lane_filter and not track.startswith(lane_filter):
            continue
        lane = next(l for l in ev["lanes"] if l["name"].lower().startswith(track[0]))
        cfgs, prog = lane["battleConfigs"], lane["progress"]
        nxt = next((i for i, c in enumerate(cfgs)
                    if i >= len(prog)
                    or len(prog[i].get("objectivesCleared", [])) < len(c["objectives"])),
                   None)
        if nxt is None:
            lanes.append({"lane": track, "name": lane["name"], "cleared": 18})
            continue
        c = cfgs[nxt]
        disallowed = c.get("disallowedFactions") or []
        base = lres[track]["battlesPoints"][nxt]
        objs = [o for o in c["objectives"] if o["objectiveType"] != "Acing"]

        # score every allowed unit across all objectives, greedy top-5
        scored = []
        for uid in owned:
            u = units[uid]
            if not allowed(uid, u, disallowed, track, owned):
                continue
            pts, detail = 0, []
            for o in objs:
                hit = matches(o, uid, u, cat)
                for r in lres[track]["unitsRestrictions"]:
                    if (r["filter"]["kind"] == o["objectiveType"]
                            and str(r["filter"].get("target"))
                            == str(o.get("objectiveTarget", ""))):
                        if r["filter"].get("exclude") and o["objectiveType"] != "NotTrait":
                            hit = not hit
                if hit:
                    pts += o["score"]
                    detail.append(obj_label(o))
            if pts:
                scored.append({"id": uid, "name": u["name"], "pts": pts,
                               "rank": u.get("rank", 0),
                               "stars": u.get("progressionIndex", 0),
                               "covers": detail})
        scored.sort(key=lambda x: (-x["pts"], -x["rank"], -x["stars"]))
        team = scored[:5]
        covered = {d for t in team for d in t["covers"]}
        warnings = [f"no owned unit covers +{o['score']} {obj_label(o)}"
                    for o in objs if obj_label(o) not in covered]

        # power ladder: objective team + strongest allowed 5 vs each tier
        battles = lres[track]["battles"]
        team_pw = sum(PW.get(t["id"], 0) for t in team)
        pool = [uid for uid in owned if allowed(uid, units[uid], disallowed, track, owned)]
        best5 = sorted(pool, key=lambda i: -PW.get(i, 0))[:5]
        best_pw = sum(PW.get(i, 0) for i in best5)
        wall = next((b["number"] for b in battles if b["power"] > best_pw), None)
        obj_wall = next((b["number"] for b in battles if b["power"] > team_pw), None)
        ladder = []
        for i, b in enumerate(battles[nxt:nxt + 6], start=nxt):
            ratio = best_pw / b["power"]
            oratio = team_pw / b["power"] if b["power"] else 0
            full = (base if i == nxt else lres[track]["battlesPoints"][i]) + \
                sum(t["pts"] for t in team) + \
                cfgs[i]["numEnemies"] * lres[track]["killPoints"]
            ladder.append({"number": b["number"], "need": b["power"],
                           "ratio": round(ratio, 2),
                           "mark": "SAFE" if ratio >= 2 else "MARGINAL" if ratio >= 1 else "TOO WEAK",
                           "obj_ratio": round(oratio, 2),
                           "obj_mark": "ok" if oratio >= 1.5 else "thin" if oratio >= 1 else "FAIL?",
                           "full_clear": full})
        lanes.append({
            "lane": track, "name": lane["name"], "next_battle": nxt + 1,
            "base_pts": base, "num_enemies": c["numEnemies"],
            "kill_pts": lres[track]["killPoints"], "disallowed": disallowed,
            "objectives": [{"score": o["score"], "type": o["objectiveType"],
                            "target": o.get("objectiveTarget", ""),
                            "label": obj_label(o)} for o in objs],
            "team": team, "team_pts": sum(t["pts"] for t in team),
            "warnings": warnings,
            "power": {"obj_team": team_pw,
                      "best5": [{"id": i, "name": units[i]["name"],
                                 "power": PW.get(i, 0)} for i in best5],
                      "best5_total": best_pw, "wall": wall,
                      "obj_wall": obj_wall},
            "ladder": ladder,
        })

    return {"schema_version": 1, "meta": meta, "lanes": lanes,
            "missions": farm_rows(player, mission_targets(lres))}


# ---- human report ----------------------------------------------------------
def report(d):
    m = d["meta"]
    w = m.get("window")
    print("=" * 78)
    head = "ANCESTORS ARE WATCHING (Uthar LRES)"
    if w:
        fmt = "%Y-%m-%d %H:%M"
        s, e = datetime.fromisoformat(w["start"]), datetime.fromisoformat(w["end"])
        if w.get("live"):
            left = e - datetime.now(timezone.utc)
            head += (f"  window {s:{fmt}} -> {e:{fmt}} UTC "
                     f"(LIVE, {left.days}d {left.seconds // 3600}h left)")
        else:
            head += f"  window {s:{fmt}} -> {e:{fmt}} UTC (upcoming)"
    else:
        head += "  window: not derivable from planner data - check in-game"
    print(head)
    print(f"EVENT STATE  points {m['points']}  engram {m['engram']}  "
          f"shards {m['shards']}/{m['shards_to_unlock']} to unlock")
    nm = m.get("next_milestone")
    if nm:
        print(f"  next milestone #{nm['milestone']}: {nm['points']} pts "
              f"(+{nm['engram']} engram, {nm['to_go']} to go)")
    o = m["outlook"]
    print(f"  chests: {m['chest']['engram_cost']}+ engram each, "
          f"{m['chest']['shards']} shards/chest; you hold {o['banked']} engram")
    print(f"  outlook: {o['milestones_left']} milestones left paying {o['engram_left']} "
          f"engram (+{o['banked']} banked = {o['engram_total']}) vs "
          f"{o['chests_needed']} chests ({o['engram_for_chests']} engram at tier-1 "
          f"price) for the last {o['shards_needed']} shards")

    for L in d["lanes"]:
        print("\n" + "=" * 78)
        if "cleared" in L:
            print(f"{L['name'].upper()}: all {L['cleared']} battles cleared")
            continue
        print(f"{L['name'].upper()} -> next battle #{L['next_battle']}/18  "
              f"base {L['base_pts']} pts  {L['num_enemies']} enemies  "
              f"(disallowed: {', '.join(L['disallowed']) or 'none'})")
        for o in L["objectives"]:
            print(f"   +{o['score']:>3}  {o['type']} {o['target']}")
        print(f"   LINEUP for every battle in this lane (objectives identical "
              f"#1-17; #18 only raises Acing, not a filter):")
        for t in L["team"]:
            print(f"      {t['pts']:>3}  {t['name']:<22} rank {t['rank']:>2} "
                  f"star {t['stars']}  [{', '.join(t['covers'])}]")
        if L["team"]:
            print(f"      team pts {L['team_pts']} + base {L['base_pts']} = "
                  f"{L['team_pts'] + L['base_pts']} before kills ({L['kill_pts']}/kill)")
        for wmsg in L["warnings"]:
            print(f"      ! {wmsg}")
        pw = L["power"]
        print(f"   POWER: lineup {pw['obj_team']:,} | strongest allowed 5 (ceiling, "
              f"ignores specialisation) {pw['best5_total']:,} "
              f"({', '.join(t['name'] for t in pw['best5'])})")
        print(f"   lineup wall: battle {pw['obj_wall'] or '>18'} first exceeds this "
              f"lineup - from there field the ceiling five (per-hero objective "
              f"points still bank)")
        print(f"   ceiling wall: battle {pw['wall'] or '>18'} is the first tier "
              f"above your strongest allowed 5")
        for r in L["ladder"]:
            print(f"      #{r['number']:>2} need {r['need']:>10,}  "
                  f"lineup {r['obj_ratio']:4.1f}x {r['obj_mark']:<6} | "
                  f"ceiling {r['ratio']:5.1f}x {r['mark']:<8} "
                  f"full-clear ~{r['full_clear']:,} pts")

    print("\n" + "=" * 78)
    print("MISSION FARMING (Defeat-N missions -> best campaign nodes)")
    for mis in d["missions"]:
        top = "; ".join(f"{r['camp']}#{r['node']} {r['per_run']}/run "
                        f"x{r['att']} att ({r['energy']}E)" for r in mis["rows"])
        print(f"  {mis['need']:>4} {mis['label']:<12} -> "
              f"{top or 'no attempts left (kills also count in event battles)'}")
    print("\nMACHINE-HUNT OVERLAP: " + d["meta"]["machine_hunt_overlap"])
    print("for the live machine-hunt window, see next_step EVENTS / machine_hunt.py")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("lane", nargs="?", help="alpha|beta|gamma (substring ok)")
    ap.add_argument("--json", action="store_true", help="machine-readable")
    ap.add_argument("--selftest", action="store_true",
                    help="analyse once and assert the payload shape")
    a = ap.parse_args(argv)
    d = analyse(a.lane)
    if a.selftest:
        assert d["schema_version"] == 1
        assert d["meta"]["shards_to_unlock"] == 400
        assert d["lanes"], "no lanes analysed"
        for L in d["lanes"]:
            if "cleared" not in L:
                assert L["team"] and L["ladder"] and "wall" in L["power"]
        assert d["missions"] and all("rows" in m for m in d["missions"])
        print(f"selftest PASS ({len(d['lanes'])} lane(s), "
              f"{len(d['missions'])} farm missions)")
        return 0
    if a.json:
        json.dump(d, sys.stdout, indent=1)
        print()
    else:
        report(d)
    return 0


if __name__ == "__main__":
    sys.exit(main())
