#!/usr/bin/env python3
"""Power delta: the power bought by the next rank-up, per character.

Why: the rank-up report can price a promotion in energy, but "cheapest first"
systematically under-invests in high-rank characters - power gained per
resource is the number that matters. This tool supplies the "power gained"
half; correlating it with energy stays the caller's job (invariant: one concern
per tool).

Model (no in-game formula is published - the wiki says so outright, see
"Power Score" - so we use Tacticus Planner's ported V1 formula, verbatim from
research/tacticus-planner-apps/packages/game-domain/src/combat-power.ts):

    attribute = (3,000,000/9326) x starsCoeff x (1.25^rank
                 + (1.25^(rank+1) - 1.25^rank)/9 x appliedCells)
    ability   = (500,000/41,274) x rarityCoeff x sum(abilityCoeff(level))
    power     = round(attribute) + round(ability)

  * starsCoeff = 1 + 0.1 x stars; rarityCoeff = 1..2 (Common..Mythic);
    abilityCoeff = V1's piecewise level curve (<=22: level, then steeper).
  * rank ladder = 20 rungs, Stone1 .. Adamantine2 - the planner deliberately
    leaves Adamantine3 out ("nothing in-game can reach it yet"), so a unit at
    rank 19 is TERMINAL here and its delta is 0.
  * "next rank-up" delta = attr(rank+1, empty grid) - attr(rank, filled grid):
    finishing the 6 cells promotes you and the grid resets.
  * Grid grants (exact, per hero) come from the raw config:
    units.lineup.<id>.upgradesStatIncrease[rank] = the six cells'
    [Hp,Hp,Dmg,Dmg,Arm,Arm] values. +Hp/+Dmg/+Arm = the cells still empty.

Caveats printed in the footer: the proxy delta is hero-agnostic (heroes differ
only by stars and filled cells - compare the stat columns for per-hero
differences); gear, later ability levels and XP are not in the delta; the
base-stat composition (base x rank x star multipliers) is not derivable from
config, so absolute stats are shown raw. MoWs are excluded (no rank grid).

Usage:
    python3 power_delta.py               # roster, biggest delta first
    python3 power_delta.py haarken       # one hero, formula breakdown
    python3 power_delta.py --json        # machine-readable
"""
import argparse
import json
import pathlib
import sys

from rank_up_report import GRID_SLOTS, MOW_IDS, load_player, rank_name, run

BASE = pathlib.Path(__file__).resolve().parent
GAMECONFIG = BASE / "research" / "datamine" / "gameconfig142.json"
CC = "clientGameConfig"

# Ported from the planner's combat-power.ts (AGPL-3.0, credited above).
W_ATTR = 3_000_000 / 9326
W_ABIL = 500_000 / 41_274
LADDER = 20                     # Stone1 .. Adamantine2 (indices 0..19)
RARITY_COEFF = {"Common": 1.0, "Uncommon": 1.2, "Rare": 1.4,
                "Epic": 1.6, "Legendary": 1.8, "Mythic": 2.0}


def ability_coeff(level):
    """V1's piecewise ability level curve, verbatim."""
    if level <= 22:
        return level
    if level <= 39:
        return 3.8 * (level - 22) + 22
    if level <= 40:
        return 5.3 * (level - 39) + 86.6
    if level <= 44:
        return 8.4 * (level - 40) + 91.9
    if level <= 50:
        return 17.3 * (level - 44) + 125.5
    return 35 * (level - 50) + 229.3


def attr_power(rank, applied, stars):
    """Planner attribute power at (rank, appliedCells, stars)."""
    current = 1.25 ** rank
    nxt = 1.25 ** (rank + 1) if rank + 1 < LADDER else current
    return W_ATTR * (1 + 0.1 * stars) * (current + (nxt - current) / 9 * applied)


def ability_power(levels, rarity):
    """Planner ability power summed over a unit's ability levels."""
    return W_ABIL * RARITY_COEFF.get(rarity, 1.0) * sum(
        ability_coeff(lvl) for lvl in levels)


# --- data --------------------------------------------------------------------

def load_config():
    if not GAMECONFIG.exists():
        sys.exit(f"error: {GAMECONFIG} missing - the rank stat grants only exist "
                 "in the raw datamine, see research/README.md")
    try:
        raw = json.loads(GAMECONFIG.read_text())
    except json.JSONDecodeError as error:
        sys.exit(f"error: {GAMECONFIG} is not valid JSON: {error}")
    try:
        units_cfg = raw[CC]["units"]
        return {
            "lineup": units_cfg["lineup"],
            "steps": units_cfg["heroProgressionSteps"],
        }
    except KeyError as error:
        sys.exit(f"error: {GAMECONFIG} is missing {error} - the config layout "
                 "changed; update power_delta.py")


def ability_names(lineup_entry):
    """{ability id: (kind, display name)} from the lineup entry (shape-agnostic)."""
    names = {}
    for kind in ("activeAbilities", "passiveAbilities"):
        for entry in lineup_entry.get(kind) or []:
            if isinstance(entry, dict) and entry.get("id"):
                names[entry["id"]] = (kind, entry.get("name") or entry["id"])
            elif isinstance(entry, str):
                names[entry] = (kind, entry)
    return names


# --- one unit ----------------------------------------------------------------

def analyse(unit, cfg):
    uid = unit.get("id", "?")
    lineup = cfg["lineup"].get(uid) or {}
    rank = int(unit.get("rank") or 0)
    pi = unit.get("progressionIndex")
    step = (cfg["steps"][pi]
            if isinstance(pi, int) and 0 <= pi < len(cfg["steps"]) else {})
    stars = int(step.get("stars") or 0)
    rarity = step.get("rarity") or "?"

    filled_cells = {int(c) for c in (unit.get("upgrades") or [])
                    if str(c).isdigit() and 0 <= int(c) < GRID_SLOTS}
    filled = len(filled_cells)
    remaining = [c for c in range(GRID_SLOTS) if c not in filled_cells]

    levels = [int(a.get("level") or 0) for a in unit.get("abilities") or []]
    attr_now = attr_power(rank, filled, stars)
    abil = ability_power(levels, rarity)
    terminal = rank + 1 >= LADDER
    attr_after = attr_now if terminal else attr_power(rank + 1, 0, stars)

    # Exact grid grants for the cells still empty (config, per hero).
    matrix = lineup.get("upgradesStatIncrease") or []
    grant = matrix[rank] if 0 <= rank < len(matrix) and matrix[rank] else None
    d_hp = d_dmg = d_arm = None
    if grant and len(grant) >= GRID_SLOTS and not terminal:
        d_hp = sum(int(grant[c]) for c in remaining if c in (0, 1))
        d_dmg = sum(int(grant[c]) for c in remaining if c in (2, 3))
        d_arm = sum(int(grant[c]) for c in remaining if c in (4, 5))

    return {
        "name": unit.get("name", "?"),
        "id": uid,
        "faction": unit.get("faction", "?"),
        "rank": rank,
        "current": rank_name(rank),
        "next": "ladder end" if terminal else rank_name(rank + 1),
        "stars": stars,
        "rarity": rarity,
        "filled": filled,
        "remaining_cells": remaining,
        "abilities": unit.get("abilities") or [],
        "ability_names": ability_names(lineup),
        "levels_sum": levels,
        "attr_now": attr_now,
        "abil_power": abil,
        "power_now": round(attr_now) + round(abil),
        "attr_after": attr_after,
        "d_power": round(attr_after) - round(attr_now),
        "d_hp": d_hp,
        "d_dmg": d_dmg,
        "d_arm": d_arm,
        "terminal": terminal,
        "base_stats": {k: lineup.get("stats", {}).get(k)
                       for k in ("Health", "Damage", "FixedArmor")}
        if lineup.get("stats") else None,
        "power_multiplier": lineup.get("powerMultiplier"),
    }


# --- rendering ---------------------------------------------------------------

def _fmt_delta(value, kind):
    if value is None:
        return "-"
    return f"+{value:,}" if kind else f"{value:,}"


def full_table(rows, meta):
    snapshot = meta.get("lastUpdatedOn", "?")
    print(f"=== Power delta vs next rank-up === snapshot {snapshot} | "
          f"planner proxy (V1 port), not the in-game score")
    w_name = max([len(r["name"]) for r in rows] + [9])
    w_rank = max([len(r["current"]) for r in rows] + [4])
    print(f"  {'Character':<{w_name}}  {'Rank':<{w_rank}}  Stars  Fill  "
          f"{'Power':>8}  {'+Rung':>7}  {'+Hp':>6}  {'+Dmg':>5}  {'+Arm':>5}")
    print("  " + " " * w_name + "  " + " " * w_rank + "  " + "-" * 5 + "  "
          + "-" * 4 + "  " + "-" * 8 + "  " + "-" * 6 + "  " + "-" * 6 + "  "
          + "-" * 5 + "  " + "-" * 5)
    for r in rows:
        fill = f"{r['filled']}/{GRID_SLOTS}"
        terminal = " END" if r["terminal"] else ""
        print(f"  {r['name']:<{w_name}}  {r['current']:<{w_rank}}  "
              f"{r['stars']:>5}  {fill:>4}  {r['power_now']:>8,}  "
              f"{_fmt_delta(r['d_power'], True):>7}{terminal}  "
              f"{_fmt_delta(r['d_hp'], True):>6}  "
              f"{_fmt_delta(r['d_dmg'], True):>5}  "
              f"{_fmt_delta(r['d_arm'], True):>5}")

    terminal_n = sum(1 for r in rows if r["terminal"])
    best = rows[0] if rows else None
    print(f"\nroster: {len(rows)} characters | ladder-end: {terminal_n}"
          + (f" | biggest rung: {best['name']} +{best['d_power']:,} power"
             if best and best["d_power"] else ""))
    print("caveat: planner proxy - the wiki confirms the in-game formula is "
          "unknown. The delta is")
    print("        hero-agnostic (heroes differ only by stars/filled cells - "
          "compare +Hp/+Dmg/+Arm")
    print("        per hero). Gear, future ability levels and XP are not in "
          "the delta.")
    print("        Correlate with `rank_up_report.py --energy` outside this "
          "tool (power per energy).")


def show_detail(row, meta):
    snapshot = meta.get("lastUpdatedOn", "?")
    print(f"=== {row['name']} ({row['id']}) === {row['faction']} | "
          f"snapshot {snapshot}")
    print(f"rank {row['current']} -> {row['next']} | stars {row['stars']} "
          f"({row['rarity']}) | grid {row['filled']}/{GRID_SLOTS}"
          + (" | LADDER END - no further rank-up" if row["terminal"] else ""))
    star_c = 1 + 0.1 * row["stars"]
    cur = 1.25 ** row["rank"]
    nxt = 1.25 ** (row["rank"] + 1) if not row["terminal"] else cur
    print(f"power now: {row['power_now']:,} = attribute "
          f"{round(row['attr_now']):,} + ability {round(row['abil_power']):,}")
    print(f"  attribute = {W_ATTR:.4f} x stars {star_c:.1f} x "
          f"(1.25^{row['rank']} + step/9 x {row['filled']})"
          f"   [1.25^{row['rank']} = {cur:.4f}]")
    print(f"  ability   = {W_ABIL:.4f} x rarity "
          f"{RARITY_COEFF.get(row['rarity'], 1.0)} x sum(abilityCoeff)")
    for ab in row["abilities"]:
        aid = ab.get("id", "?")
        kind, name = row["ability_names"].get(aid, ("ability", aid))
        lvl = int(ab.get("level") or 0)
        print(f"    {kind:<9} {name} L{lvl} -> coeff {ability_coeff(lvl):.1f}")
    if row["terminal"]:
        print("next rank-up: none - Adamantine2 is the last ladder rung "
              "(planner omits Adamantine3)")
    else:
        print(f"next rank-up: +{row['d_power']:,} power "
              f"(attribute {round(row['attr_now']):,} -> "
              f"{round(row['attr_after']):,}), grid resets")
        print(f"  grid {row['filled']}/{GRID_SLOTS}, empty cells "
              f"{[c + 1 for c in row['remaining_cells']] or 'none'} -> "
              f"+{row['d_hp'] or 0:,} HP, +{row['d_dmg'] or 0:,} Dmg, "
              f"+{row['d_arm'] or 0:,} Arm (config grants, summed over those "
              "cells)")
    if row["base_stats"]:
        stats = row["base_stats"]
        pm = row["power_multiplier"]
        print(f"base lineup stats: Health {stats['Health']}, "
              f"Damage {stats['Damage']}, FixedArmor {stats['FixedArmor']}"
              + (f" | powerMultiplier {pm}" if pm is not None else
                 " | powerMultiplier - (not set for this hero)"))
    print("caveat: proxy, not the in-game score; base x rank x star stat "
          "composition is not derivable from config.")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="*",
                    help="character NAME or id (partial ok; names are what the "
                         "output prints). Omit for the full roster table")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    cfg = load_config()
    player, meta = load_player()

    rows = []
    for unit in player.get("units") or []:
        if unit.get("id") in MOW_IDS:
            continue                    # no rank grid, they level abilities
        rows.append(analyse(unit, cfg))
    rows.sort(key=lambda r: (-r["d_power"], r["name"]))

    query = " ".join(args.query).replace("_", " ").lower()
    if query:
        matched = [r for r in rows
                   if query in r["name"].lower() or query in r["id"].lower()]
        if not matched:
            sys.exit(f"error: no character matches {query!r}")
        rows = matched

    if args.json:
        for row in rows:                # drop non-serialisable helper dicts
            row.pop("ability_names", None)
        meta_out = {
            "snapshot": meta.get("lastUpdatedOn"),
            "player": (player.get("details") or {}).get("name"),
            "power_level": (player.get("details") or {}).get("powerLevel"),
            "formula": "tacticus-planner combat-power.ts (V1 port)",
            "constants": {"w_attr": W_ATTR, "w_abil": W_ABIL,
                          "ladder": LADDER,
                          "rarity_coeff": RARITY_COEFF},
        }
        json.dump({"schema_version": 1, "meta": meta_out, "rows": rows},
                  sys.stdout, indent=1)
        print()
        return

    if query and len(rows) == 1:
        show_detail(rows[0], meta)
    else:
        full_table(rows, meta)


if __name__ == "__main__":
    run(main)
