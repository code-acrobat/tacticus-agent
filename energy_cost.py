#!/usr/bin/env python3
"""Exact energy cost of the items a character still needs for its next rank.

rank_up_report.py's --estimate mode guesses a rarity; this module does the
real arithmetic by joining the offline game catalog in research/ :

    units/units-*.json          rankUpUpgrades[rank].upgradeIds = the 6 cells
    upgrades/upgrades-*.json    id -> rarity, craftable, recipe[{material,count}]
    campaign-battles/*.json     per node: energyCost + rewards
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
from collections import Counter

BASE = pathlib.Path(__file__).resolve().parent
DATA = (BASE / "research" / "tacticus-planner-api" / "src"
        / "TacticusPlanner.GameCatalog" / "Data")
UNITS_DIR = DATA / "units"
UPGRADES_DIR = DATA / "upgrades"
BATTLES_DIR = DATA / "campaign-battles"
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
    """id -> character record (also keyed by name)."""
    by_id, by_name = {}, {}
    for path in sorted(_require(UNITS_DIR).glob("units-*.json")):
        for character in (_read(path) or {}).get("characters") or []:
            by_id[character["id"]] = character
            by_name[character["name"]] = character
    if not by_id:
        raise MissingSource(f"no characters under {UNITS_DIR}")
    return by_id, by_name


def load_upgrades():
    """id -> {rarity, craftable, recipe}. 557 items across 11 files."""
    catalog = {}
    for path in sorted(_require(UPGRADES_DIR).glob("upgrades-*.json")):
        for item in _read(path) or []:
            catalog[item["id"]] = item
    if not catalog:
        raise MissingSource(f"no upgrades under {UPGRADES_DIR}")
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
        node_is_event = any("event" in (p.get("chanceId") or "") for p in potential)
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


def load_farms():
    if not _require(BATTLES_DIR).exists() or not _require(DROP_CHANCES).exists():
        raise MissingSource("campaign data missing")
    nodes = []
    for path in sorted(BATTLES_DIR.glob("campaign-battles-*.json")):
        nodes.extend((_read(path) or {}).get("battles") or [])
    chances = {row["id"]: row for row in _read(DROP_CHANCES) or []}
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
    return (UNITS_DIR.is_dir() and UPGRADES_DIR.is_dir()
            and BATTLES_DIR.is_dir() and DROP_CHANCES.is_file())
