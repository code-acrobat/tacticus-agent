#!/usr/bin/env python3
"""Regenerate a lres-*.json snapshot from the raw datamine.

The planner deleted the served Data/lres/*.json files in the 2026-11 datamine
restructure, so the snapshot lres_report.py reads is now produced here instead:
a Python transcription of the planner's Denormalization/LreDatamineReader.cs.

    python3 gen_lres.py lostaBile --out lres-bile.json
    python3 gen_lres.py lostaBile --check lres-bile.json   # re-derive + diff after a datamine sync

Needs research/ (the raw gameconfig datamine + curated lre-curation.json).
"""
import argparse
import json
import re
import sys
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "research/tacticus-planner-api/src/TacticusPlanner.GameCatalog/Data"
RAW = DATA / "datamine" / "raw" / "gameconfig"

ACE = "Acing"
CUR = "legendaryEventCurrency:"
SHARDS = "legendaryEventShards:"
EVENT_NAME = re.compile(r"^legendary_hero_event_(\d+)$")

# objectiveType -> (served filter kind, exclude), verbatim LreObjectiveFilters.
FILTERS = {
    "Faction": ("Faction", False),
    "DamageType": ("DamageType", False),
    "NotDamageType": ("DamageType", True),
    "Trait": ("Trait", False),
    "NotTrait": ("Trait", True),
    "MaxHits": ("MaxHits", False),
    "MinHits": ("MinHits", False),
    "HasRangedAttack": ("AttackType", False),
    "HasNoRangedAttack": ("AttackType", True),
}
FACTION_NAME = {"AdeptusMechanicus": "Adeptus Mechanicus", "Tau": "T'au Empire"}
TRAIT_NAME = {"TerminatorArmour": "Terminator"}

# V1 mission-text labels -> catalog ids (LreDenormalizer.LreMission*Aliases).
FACTIONS = {
    "Adepta Sororitas": "Sisterhood", "Adeptus Astartes": "AdeptusAstartes",
    "Adeptus Custodes": "Custodes", "AdMechs": "AdeptusMechanicus",
    "Aeldari": "Aeldari", "Astra Militarum": "AstraMilitarum",
    "Black Legion": "BlackLegion", "Black Templars": "BlackTemplars",
    "Blood Angels": "BloodAngels", "Dark Angels": "DarkAngels",
    "Death Guard": "DeathGuard", "Emperor's Children": "EmperorsChildren",
    "Genestealer Cults": "Genestealers", "Leagues of Votann": "LeaguesOfVotann",
    "Necrons": "Necrons", "Orks": "Orks", "Space Wolves": "SpaceWolves",
    "T'au": "Tau", "The Lost and the Damned": "TheLostAndTheDamned",
    "Thousand Sons": "ThousandSons", "Tyranids": "Tyranids",
    "Ultramarines": "Ultramarines", "World Eaters": "WorldEaters",
}
TRAITS = {
    "Act of Faith": "ActOfFaith", "Ambush": "Ambush", "Big Target": "BigTarget",
    "Blessings of Khorne": "BlessingsOfKhorne", "Camouflage": "Camouflage",
    "Contagions of Nurgle": "ContagionsOfNurgle", "Crushing Strike": "CrushingStrike",
    "Daemon": "Daemon", "Deep Strike": "TeleportStrike", "Explodes": "Explodes",
    "Final Vengeance": "FinalJustice", "Flying": "Flying",
    "Get Stuck In": "GetStuckIn", "Healer": "Healer",
    "Heavy Weapon": "HeavyWeapon", "Indirect Fire": "IndirectFire",
    "Infiltrate": "Infiltrate", "Let the Galaxy Burn": "LetTheGalaxyBurn",
    "Living Metal": "LivingMetal", "Martial Ka'tah": "MartialKatah",
    "Mechanic": "Mechanic", "Mechanical": "Mechanical", "MK X Gravis": "MkXGravis",
    "Overwatch": "Overwatch", "Parry": "Parry", "Psyker": "Psyker",
    "Putrid Explosion": "PutridExplosion", "Rapid Assault": "RapidAssault",
    "Ranged Specialist": "RangedSpecialist", "Resilient": "Resilient",
    "Shadow in the Warp": "ShadowInTheWarp", "Suppressive Fire": "SuppressiveFire",
    "Synapse": "Synapse", "Terminator Armour": "TerminatorArmour",
    "Terrifying": "Terrifying", "Thrill Seekers": "ThrillSeekers",
    "Unstoppable": "Unstoppable", "Vehicle": "Vehicle",
    "Weaver of Fates": "WeaverOfFate",
}
# id -> label (the reverse of the tables above; the reader reverses them too)
F_LABEL = {v: k for k, v in FACTIONS.items()}
T_LABEL = {v: k for k, v in TRAITS.items()}


def load(path):
    return json.loads((RAW / path).read_text())


def faction_name(fid):
    return FACTION_NAME.get(fid) or F_LABEL.get(fid) or fail(f"faction '{fid}' has no label")


def trait_name(tid):
    return TRAIT_NAME.get(tid) or T_LABEL.get(tid) or fail(f"trait '{tid}' has no label")


def fail(msg):
    raise SystemExit(f"gen_lres: {msg}")


def task_text(quest, task):
    """One task's English mission text (LreDatamineReader.LreTaskText)."""
    target, params = task["target"], task.get("taskParameters") or {}
    plural = "" if target == 1 else "s"
    thousands = f"{Decimal(target) / Decimal(1000):f}" + "k"

    def param(name):
        v = params.get(name)
        if not v:
            fail(f"quest '{quest}' task '{task['name']}' has no '{name}' parameter")
        return v

    def label(table, name):
        v = param(name)
        if v not in table:
            fail(f"quest '{quest}' names '{v}' with no mission label")
        return table[v]

    name = task["name"]
    if name == "DefeatWaves":
        return f"Defeat {target} waves of enemies in any wave-based game mode"
    if name == "DealDamage":
        return f"Deal {target} damage"
    if name == "DealAbilityDamage":
        return f"Deal {thousands} damage with Abilities"
    if name == "DealDamageOfProfileType":
        return f"Deal {thousands} {param('damageProfileType')} damage"
    if name == "SlayEnemiesOfFaction":
        return f"Defeat {target} {label(F_LABEL, 'factionId')}"
    if name == "SlayEnemiesOfTrait":
        return f"Defeat {target} {label(T_LABEL, 'trait')} enemies"
    if name == "UseAbilityWithFactionTrait":
        return f"Use abilities {target} times with {param('factionTrait')} units"
    if name == "WinBattlesWithoutUsingSummons":
        return f"Win {target} battle{plural} without Summoning any units"
    if name == "SlayEnemiesWithFactionTrait":
        return f"Defeat {target} enemies with {param('factionTrait')} units"
    if name == "WinBattlesWithoutTrait":
        return f"Win {target} battle{plural} without deploying any {label(T_LABEL, 'trait')} characters"
    if name == "SlayEnemiesWithDamageProfile":
        return f"Defeat {target} enemies with {param('damageProfileType')} Damage"
    fail(f"quest '{quest}' has unknown task '{name}'")


def restriction_name(objective):
    kind, target = objective["objectiveType"], objective.get("objectiveTarget", "")
    if kind == "Trait":
        return trait_name(target)
    if kind == "NotTrait":
        return f"No {trait_name(target)}"
    if kind == "DamageType":
        return target
    if kind == "NotDamageType":
        return f"No {target}"
    if kind == "Faction":
        return faction_name(target)
    if kind == "MaxHits":
        return f"Max {target} hits"
    if kind == "MinHits":
        return f"Min {target} hits"
    if kind == "HasRangedAttack":
        return "Ranged"
    if kind == "HasNoRangedAttack":
        return "Melee"
    fail(f"unknown restriction type '{kind}'")


def army_enemies(army):
    """One entry per spawned enemy -> [{id, ladderIndex, count}] in first-appearance order."""
    out = []
    for entry in army:
        if ":" not in entry:
            fail(f"enemy '{entry}' without a progression index")
        eid, _, idx = entry.rpartition(":")
        if not eid or not idx.isdigit():
            fail(f"enemy '{entry}' without a progression index")
        idx = int(idx)
        hit = next((e for e in out if e["id"] == eid and e["ladderIndex"] == idx), None)
        if hit:
            hit["count"] += 1
        else:
            out.append({"id": eid, "ladderIndex": idx, "count": 1})
    return out


def read_track(unit, key, battles, rosters):
    """ReadLreTrack: name / killPoints / battlesPoints / restrictions / battles."""
    if not battles:
        fail(f"{key} has no battles")
    restrictions = battles[0]["objectives"][1:]
    for b in battles:
        if not b["objectives"] or b["objectives"][0]["objectiveType"] != ACE:
            fail(f"{key} battle {b['battleNr']} does not start with an Acing objective")
        if b["objectives"][1:] != restrictions:
            fail(f"{key} battle {b['battleNr']} has restrictions that differ from battle 1")
    disallowed = battles[0].get("disallowedFactions") or []
    if any((b.get("disallowedFactions") or []) != disallowed for b in battles):
        fail(f"{key} has battles that disallow different factions")

    barred = [a for a, facs in rosters.items() if facs <= set(disallowed)]
    if len(barred) != 1:
        fail(f"{key} disallows {len(barred)} whole alliances, not one")
    extras = [f for f in disallowed if f not in rosters[barred[0]]]
    name = f"{key.capitalize()} (No {barred[0]}" + "".join(
        f" or {faction_name(f)}" for f in extras) + ")"

    units_restrictions = []
    for i, o in enumerate(restrictions):
        kind_excl = FILTERS.get(o["objectiveType"])
        if not kind_excl:
            fail(f"{key} has unknown restriction type '{o['objectiveType']}'")
        kind, excl = kind_excl
        target = "Ranged" if kind == "AttackType" else o.get("objectiveTarget", "")
        if not target:
            fail(f"{key} restriction '{o['objectiveType']}' has no target")
        units_restrictions.append({
            "name": restriction_name(o), "points": o["score"], "index": i,
            "filter": {"kind": kind, "target": target, "exclude": excl},
        })

    return {
        "name": name,
        "enemies": {"label": "", "link": ""},
        "killPoints": battles[0]["objectives"][0]["score"],
        "battlesPoints": [sum(len(w["army"]) for w in b["waves"]) for b in battles],
        "defeatAll": [b["objectives"][0]["score"] for b in battles],
        "allowedUnitsFilter": (
            [{"kind": "Alliance", "target": barred[0], "exclude": True}]
            + [{"kind": "Faction", "target": f, "exclude": True} for f in extras]),
        "unitsRestrictions": units_restrictions,
        "battles": [{
            "mapId": b["boardId"], "number": b["battleNr"], "power": b["power"],
            "tier": b["tier"], "disallowedFactions": disallowed,
            "waves": [{"round": w["round"], "power": w["power"],
                       "enemies": army_enemies(w["army"])} for w in b["waves"]],
        } for b in battles],
    }


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("unit", help="unit snowprint id, e.g. lostaBile")
    ap.add_argument("--out", type=Path, help="write the snapshot here instead of stdout")
    ap.add_argument("--check", type=Path,
                    help="diff the generated payload against an existing snapshot "
                         "(sourceFile/wikiLink/runStartDatesUtc are not derived here)")
    a = ap.parse_args(argv)

    events = load("liveEvents/legendaryHeroEvent.json")
    mine = [e for e in events if (e.get("unlockableHero") or {}).get("heroId") == a.unit]
    if len(mine) != 1:
        fail(f"{a.unit} is unlocked by {len(mine)} events, not one")
    ev = mine[0]
    m = EVENT_NAME.match(ev["eventName"])
    if not m:
        fail(f"event '{ev['eventName']}' has no event number")

    lineup = load("units/lineup.json")
    if a.unit not in lineup:
        fail(f"{a.unit} is not a character the game has")
    rosters = {}
    for u in lineup.values():
        rosters.setdefault(u["GrandAllianceId"], set()).add(u["FactionId"])

    battle_sets = load("battles/battleSets.json")
    tracks = {}
    for key, lane in zip(("alpha", "beta", "gamma"), sorted(ev["lanes"], key=lambda l: l["laneIndex"])):
        if lane["battleSetId"] not in battle_sets:
            fail(f"{key} names battle set '{lane['battleSetId']}' the game does not have")
        tracks[key] = read_track(a.unit, key, battle_sets[lane["battleSetId"]], rosters)
    if len({len(t["battles"]) for t in tracks.values()}) != 1:
        fail("tracks with different battle counts")
    if len({len(t["unitsRestrictions"]) for t in tracks.values()}) != 1:
        fail("tracks with different restriction counts")

    # enemy label + wiki link, per lane faction
    for key, lane in zip(("alpha", "beta", "gamma"), sorted(ev["lanes"], key=lambda l: l["laneIndex"])):
        label = faction_name(lane["factionId"])
        tracks[key]["enemies"] = {
            "label": label,
            "link": f"https://tacticus.wiki.gg/wiki/{label.replace(' ', '_')}#NPC_Units",
        }

    # missions
    groups = load("quests/groups.json")
    picked = []
    for gname in ev["questGroups"]:
        if gname not in groups:
            fail(f"names quest group '{gname}' the game does not have")
        picked.append(groups[gname])
    miss = {}
    for g in picked:
        miss[g["questType"]] = [", ".join(task_text(q["name"], t)
                                           for t in q["tasks"]) for q in g["quests"]]
    if "standard" not in miss or "premium" not in miss:
        fail(f"has no single standard/premium quest group (got {sorted(miss)})")

    # points milestones
    ladder = load("loot/tieredProgressRewards.json").get(ev["rewardProgressId"])
    if not ladder:
        fail(f"names points ladder '{ev['rewardProgressId']}' the game does not have")
    points = []
    for i, step in enumerate(ladder, 1):
        rid = step["chestRewardId"]
        if not rid.startswith(CUR) or not rid[len(CUR):].isdigit():
            fail(f"points milestone {i} pays '{rid}', not event currency")
        points.append({"milestone": i, "cumulativePoints": step["requiredProgress"],
                       "engramPayout": int(rid[len(CUR):])})

    # chests
    segments = load("shop/segments.json")
    seg = next((s for s in segments if s["segmentId"] == ev["shopSegmentId"]), None)
    if not seg or len(seg["productSets"]) != 1:
        fail(f"shop segment '{ev['shopSegmentId']}' missing or has != 1 product set")
    products = load("shop/products.json")
    chests_src = load("loot/chests.json")
    chests, shard_amounts = [], set()
    for i, pid in enumerate(seg["productSets"][0]["products"], 1):
        p = products.get(pid)
        if not p:
            fail(f"names product '{pid}' the game does not have")
        if p["cost"]["type"] != "legendaryEventCurrency":
            fail(f"product '{pid}' costs '{p['cost']['type']}', not event currency")
        chests.append({"chestLevel": i, "engramCost": p["cost"]["amount"]})
        for chest in chests_src.get(p["chestId"], []):
            for r in chest.get("rewards", []):
                if r.startswith(SHARDS):
                    shard_amounts.add(int(r[len(SHARDS):]))
    if len(shard_amounts) != 1:
        fail(f"chests grant {len(shard_amounts)} different shard amounts, not one")

    # ascension progression (Legendary 8/9/10/11, Mythic 11/12)
    steps = load("units/heroProgressionStepsPerUnit.json").get(a.unit)
    if not steps:
        fail("has no progression steps in the game")
    prog = {}
    for rarity, stars, key2 in (("Legendary", 8, "unlock"), ("Legendary", 9, "fourStars"),
                                ("Legendary", 10, "fiveStars"), ("Legendary", 11, "blueStar"),
                                ("Mythic", 11, "mythic"), ("Mythic", 12, "twoBlueStars")):
        step = next((s for s in steps if s["rarity"] == rarity and s["stars"] == stars), None)
        cost = (step or {}).get("ascensionCosts", {}).get("legendaryEventShards")
        if not cost:
            fail(f"has no event shard cost for {rarity} {stars} stars")
        prog[key2] = cost

    lre = {
        "sourceFile": f"gameconfig/liveEvents/legendaryHeroEvent.json#{ev['eventName']}",
        "id": int(m.group(1)),
        "unitSnowprintId": a.unit,
        "name": lineup[a.unit]["name"],
        "wikiLink": "https://tacticus.wiki.gg/wiki/Legendary_Character_Events",
        "runStartDatesUtc": json.loads((DATA / "curated" / "lre-curation.json")
                                       .read_text()).get(a.unit, {}).get("runStartDatesUtc", []),
        "regularMissions": miss["standard"],
        "premiumMissions": miss["premium"],
        **tracks,
        "pointsMilestones": points,
        "chestsMilestones": chests,
        "shardsPerChest": shard_amounts.pop(),
        "battlesCount": len(tracks["alpha"]["battles"]),
        "constraintsCount": len(tracks["alpha"]["unitsRestrictions"]),
        "progression": prog,
    }

    if a.check:
        have = json.loads(a.check.read_text())
        ignore = {"sourceFile", "wikiLink", "runStartDatesUtc"}
        diffs = [k for k in set(have) | set(lre)
                 if k not in ignore and have.get(k) != lre.get(k)]
        if diffs:
            for k in diffs:
                print(f"DIFF {k}:", file=sys.stderr)
                print(f"  have {json.dumps(have.get(k))[:400]}", file=sys.stderr)
                print(f"  gen  {json.dumps(lre.get(k))[:400]}", file=sys.stderr)
            return 1
        print(f"check OK: {a.check} matches the datamine "
              f"({len(lre['regularMissions'])} regular / {len(lre['premiumMissions'])} premium missions)")
        return 0

    text = json.dumps(lre, indent=1) + "\n"
    if a.out:
        a.out.write_text(text)
        print(f"wrote {a.out} ({a.out.stat().st_size} bytes): {lre['name']} / "
              f"{len(lre['regularMissions'])} missions / "
              f"{lre['battlesCount']} battles per lane")
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
