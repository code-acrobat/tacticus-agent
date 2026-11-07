#!/usr/bin/env python3
"""Exact energy cost of the items a character still needs for its next rank.

rank_up_report.py's --estimate mode guesses a rarity; this module does the
real arithmetic by joining the offline game catalog in research/ :

    datamine raw (post-2026-11 planner restructure, see SKILL.md):
    units/lineup.json         upgrades[] rows = the 6 cell ids per rank
    upgrades.json             crafting[] = {id, amount} recipe tree
    battles/campaigns.json    per node: staminaCost + loot{base,chanceOf}
    curated/campaign-curation.json  group -> node-id prefix
    drop-chances.json           chanceId -> effectiveRate (mercy ALREADY baked in)

Model
  * missing cells = upgradeIds[current rank], minus the cell indices already
    present in unit['upgrades'] (grid order matches upgradeIds, cell 0 = top left).
  * **farmable and craftable are disjoint sets** - an id is either a farmable
    leaf or a craftable composite, never both. So there is no "cheaper of the
    two" choice; the only choice is "consume the owned composite" vs "craft it",
    and consuming is always free, hence always optimal.
  * recipes nest up to 3 deep. All 6 cells are aggregated FIRST, then owned
    counts are consumed at every level, so a shared component (upgHpE003 shows
    up in several of Titus's trees) is deducted once instead of six times.
  * leaves cost net_need * (node energyCost / effectiveRate).
    Never use numerator/denominator: effectiveRate is the mercy-inflated rate.

Within one character the pool is shared across steps, so a tier climb
prices every rung against what the rungs before it left in the bank.

Per-character pricing starts from a fresh copy of the inventory, so two
characters do not double-spend the same owned item - but neither do they
share one, so pricing N characters at once is optimistic. See the report
footer.
"""
import json
import pathlib
import re
from collections import Counter

BASE = pathlib.Path(__file__).resolve().parent
DATA = (BASE / "research" / "tacticus-planner-api" / "src"
        / "TacticusPlanner.GameCatalog" / "Data")
RAW = DATA / "datamine" / "raw" / "gameconfig"
UNITS_FILE = RAW / "units" / "lineup.json"
UPGRADES_FILE = RAW / "upgrades.json"
CAMPAIGNS_FILE = RAW / "battles" / "campaigns.json"
CURATION_FILE = DATA / "curated" / "campaign-curation.json"
DROP_CHANCES = DATA / "drop-chances.json"


class MissingSource(RuntimeError):
    """research/ is not populated - the caller should fall back to --estimate."""


def _read(path):
    try:
        return json.loads(path.read_text())
    except OSError as error:
        raise MissingSource(f"{path}: {error}") from error
    except json.JSONDecodeError as error:
        raise MissingSource(f"{path} is not valid JSON: {error}") from error


def _require(path):
    if not path.exists():
        raise MissingSource(
            f"{path} not found - clone TacticusPlanner/tacticus-planner-api "
            f"into research/ (see research/README.md), or use --estimate")
    return path


def load_units():
    """id -> character record (also keyed by name), rank grid normalized.

    The raw lineup keeps the 6-cell grid as `upgrades` rows (20 ranks); the
    old served shape called that `rankUpUpgrades[].upgradeIds`, which is what
    cells() reads, so it is aliased here and nothing downstream changes.
    """
    by_id, by_name = {}, {}
    for cid, unit in (_read(_require(UNITS_FILE)) or {}).items():
        if not isinstance(unit, dict):
            continue
        if unit.get("upgrades"):
            unit = dict(unit, rankUpUpgrades=[{"upgradeIds": row}
                                              for row in unit["upgrades"]])
        by_id[cid] = unit
        if unit.get("name"):
            by_name[unit["name"]] = unit
    gridded = sum(1 for u in by_id.values() if u.get("rankUpUpgrades"))
    if gridded < 100:  # canary: `upgrades` key renamed upstream?
        raise MissingSource(f"only {gridded} units with a rank grid in {UNITS_FILE}")
    return by_id, by_name


def load_upgrades():
    """id -> catalog item. Raw `crafting[{id,amount}]` -> recipe[{material,count}]."""
    catalog = {}
    for iid, raw in (_read(_require(UPGRADES_FILE)) or {}).items():
        item = dict(raw, id=iid, stat=raw.get("statType"),
                    label=raw.get("name") or iid)
        craft = item.pop("crafting", None)
        item["craftable"] = bool(craft)
        item["recipe"] = [{"material": c["id"], "count": c.get("amount", 0)}
                          for c in craft or []]
        catalog[iid] = item
    if sum(1 for i in catalog.values() if i["craftable"]) < 300:  # canary
        raise MissingSource(f"crafting recipes missing in {UPGRADES_FILE}")
    return catalog


def load_inventory(player):
    """Owned upgrade counts from tacticus-player.json (164 ids, joins 1:1)."""
    owned = Counter()
    for entry in ((player.get("inventory") or {}).get("upgrades") or []):
        amount = entry.get("amount", entry.get("qty", 0)) or 0
        if entry.get("id") and amount > 0:
            owned[entry["id"]] += amount
    return owned


def _farms(nodes, chances, event):
    """item id -> (energy per item, node id, kind) for event/non-event nodes."""
    best = {}
    for node in nodes:
        rewards = node.get("rewards") or {}
        potential = rewards.get("potential") or []
        guaranteed = rewards.get("guaranteed") or []
        node_is_event = node.get("_event", False)   # bucket Event, set by load_farms
        if node_is_event != event:
            continue
        cost = node.get("energyCost")
        if not cost:
            continue
        for roll in potential:
            item, row = roll.get("id"), chances.get(roll.get("chanceId"))
            rate = (row or {}).get("effectiveRate") or 0
            if not item or rate <= 0:
                continue
            per = cost / rate
            if per < best.get(item, (float("inf"),))[0]:
                best[item] = (per, node["id"], "roll")
        for drop in guaranteed:
            item, low = drop.get("id"), drop.get("min") or 0
            if not item or not str(item).startswith("upg") or low <= 0:
                continue
            per = cost / low
            if per < best.get(item, (float("inf"),))[0]:
                best[item] = (per, node["id"], "guaranteed")
    return best


_RARITY = {"C": "Common", "U": "Uncommon", "R": "Rare", "E": "Epic",
           "L": "Legendary", "M": "Mythic"}


def _chance_id(item, bucket, group, challenge):
    """chanceOf item -> drop-chances key `kind_<difficulty>[Challenge]`.

    Mirrors the served campaign-battles projection (SKILL.md): the difficulty
    comes from the bucket, except Event groups which split eventStandard /
    eventExtremis by group id; challenge ("B" battle ids) appends Challenge.
    """
    m = re.match(r"upg(?:Hp|Dmg|Arm)([CURELM])", item)
    if item.startswith("mythicShards"):
        kind = "mythicShard"
    elif item.startswith("shards"):
        kind = "shard"
    elif m:
        kind = "upgrade" + _RARITY[m.group(1)]
    else:
        return None
    if bucket == "Event":
        diff = ("eventStandard" if group.startswith("eventStandard")
                else "eventExtremis")
    else:
        diff = {"Standard": "standard", "Mirror": "mirror",
                "Elite": "elite", "EliteMirror": "elite"}.get(bucket, bucket)
    return f"{kind}_{diff}{'Challenge' if challenge else ''}"


def load_farms():
    """Build the farm node list from raw campaigns + curation, then price it."""
    if not (CAMPAIGNS_FILE.exists() and CURATION_FILE.exists()
            and DROP_CHANCES.exists()):
        raise MissingSource("campaign data missing")
    camps = _read(CAMPAIGNS_FILE)
    by_id = {g["id"]: (bucket, g)
             for bucket, gs in camps.items() for g in gs}
    cur = _read(CURATION_FILE) or {}
    nodes = []
    for key in sorted(cur):
        if not key.startswith("campaign-battles-"):
            continue
        for spec in (cur[key].get("campaigns") or []):
            bucket, g = by_id[spec["campaign"]]
            prefix = spec["battleIdPrefix"]
            for b in g["battles"]:
                bid = b["battleId"]
                challenge = "B" in bid
                node_id = (prefix + "C" + bid.lstrip("0") if challenge
                           else prefix + bid)
                loot = b.get("loot") or {}
                guaranteed = []
                for s in loot.get("base") or []:
                    iid, _, rng = s.partition(":")
                    if rng:
                        lo, _, hi = rng.partition("-")
                        lo = int(lo)
                        guaranteed.append({"id": iid, "min": lo,
                                           "max": int(hi) if hi else lo})
                    else:
                        guaranteed.append({"id": iid, "min": 1, "max": 1})
                potential = []
                cf = loot.get("chanceOf")
                if cf:
                    iid = cf.split("%")[0]
                    cid = _chance_id(iid, bucket, g["id"], challenge)
                    if cid:
                        potential.append({"id": iid, "chanceId": cid})
                nodes.append({
                    "id": node_id, "energyCost": b.get("staminaCost") or 0,
                    "rewards": {"guaranteed": guaranteed,
                                "potential": potential},
                    "_event": bucket == "Event",
                })
    chances = {row["id"]: row for row in _read(DROP_CHANCES) or []}
    if len(nodes) < 1200 or len(chances) < 30:  # canary: curation/rate drift
        raise MissingSource(
            f"farms collapsed: {len(nodes)} nodes, {len(chances)} chances")
    return {"permanent": _farms(nodes, chances, False),
            "event": _farms(nodes, chances, True),
            "nodes": len(nodes), "chances": len(chances)}


def cells(character, rank, filled):
    """Ids of the grid cells at `rank` not already in `filled`."""
    ladder = character.get("rankUpUpgrades") or []
    if not 0 <= rank < len(ladder):
        return []
    ids = ladder[rank].get("upgradeIds") or []
    filled = set(filled)
    return [ids[i] for i in range(min(len(ids), 6)) if i not in filled]


def tier_end(rank, max_rank=20):
    """Rank index one past the last step needed to reach the next tier."""
    return min((rank // 3 + 1) * 3, max_rank)


def price(items, catalog, owned, farms):
    """Energy to obtain `items`, consuming `owned` at every recipe level.

    `owned` is a pool the caller owns: it is mutated in place, so successive
    rank-ups can be priced against what the earlier ones left behind.
    Returns energy, per-leaf detail, and the flags the report prints.
    """
    req = Counter(items)
    crafted, spent = Counter(), Counter()
    event_energy = 0.0
    blocked, event_leaves = Counter(), {}

    # 1. walk composites down to leaves, preferring owned composites
    while True:
        target = next((i for i, n in req.items()
                       if n > 0 and (catalog.get(i) or {}).get("craftable")), None)
        if target is None:
            break
        qty = req.pop(target)
        take = min(owned.get(target, 0), qty)
        if take:
            owned[target] -= take
            spent[target] += take
        left = qty - take
        if left:
            crafted[target] += left
            for comp in catalog[target].get("recipe") or []:
                req[comp["material"]] += comp.get("count", 0) * left

    # 2. leaves: deduct owned, then farm the remainder
    energy = 0.0
    leaves = {}
    for item in sorted(req):
        qty = req[item]
        take = min(owned.get(item, 0), qty)
        if take:
            owned[item] -= take
            spent[item] += take
        left = qty - take
        if left <= 0:
            continue
        entry, from_event = farms["permanent"].get(item), False
        if entry is None:
            entry, from_event = farms["event"].get(item), item in farms["event"]
        if entry is None:
            blocked[item] = left
            continue
        per, node, kind = entry
        cost = per * left
        energy += cost
        if from_event:
            event_energy += cost
            event_leaves[item] = left
        leaves[item] = {"need": left, "per_item": round(per, 2),
                        "node": node, "how": kind, "event": from_event}

    return {
        "energy": energy,
        "event_energy": event_energy,
        "cells": len(items),
        "leaves": leaves,
        "crafted": dict(crafted),
        "spent_from_inventory": dict(spent),
        "blocked": dict(blocked),
    }


def available():
    """True when the catalog the exact model needs is on disk."""
    return (UNITS_FILE.is_file() and UPGRADES_FILE.is_file()
            and CAMPAIGNS_FILE.is_file() and CURATION_FILE.is_file()
            and DROP_CHANCES.is_file())
