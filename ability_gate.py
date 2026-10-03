#!/usr/bin/env python3
"""Ability gate: which heroes are blocked on badges, and what the step costs.

Abilities level up with gold plus one alliance badge per step. The cost table
`clientGameConfig.units.abilityUpgradeCosts` is a flat 59-entry ladder indexed
by (current level - 1): an ability at level L costs entry[L-1] to reach L+1,
so level 60 is the end of the table (entry 0 = 1->2 ... entry 58 = 59->60).
There is no alliance inside a cost - the badge key is
`abilityToken<Rarity>`, and the ALLIANCE comes from the unit
(`grandAlliance`), matched against `player.inventory.abilityBadges`.

Statuses (per hero, both abilities considered):
  badge-blocked  at least one badge it needs sits in a pool where the ROSTER's
                 total next-level demand exceeds what you hold. The block is
                 aggregate: no single hero necessarily exceeds the stock alone
                 (e.g. Lysander wants 7 Uncommon Imperial of the 25 held, but
                 the roster demands 107 of them).
  capped         no costable ability left (both at level 60).
  ok             every pool it draws on covers the whole roster's demand.

The badge stock is ONE shared pool per alliance - "Need" is what one hero
wants, not what is left once others are served; the `short pools` line is the
real gap. Gold is reported per step but never gates: the snapshot carries no
gold balance anywhere (verified, depth<=4 key scan).

The 11 machines of war are excluded - `abilityUpgradeCostsMoW` is a different
ladder (machinesOfWarToken + dust + ascension resources; see AGENTS.md).

Usage:
    python3 ability_gate.py                    # full roster, grouped by status
    python3 ability_gate.py --only blocked     # just the badge-blocked heroes
    python3 ability_gate.py lysander           # one hero: steps, pools, verdict
    python3 ability_gate.py --json
"""
import argparse
import json
import pathlib
import sys
from collections import Counter

from rank_up_report import MOW_IDS, load_player, rank_name, run

BASE = pathlib.Path(__file__).resolve().parent
GAMECONFIG = BASE / "research" / "datamine" / "gameconfig142.json"
CC = "clientGameConfig"

RARITY = ("Common", "Uncommon", "Rare", "Epic", "Legendary", "Mythic")
ABBR = {r: r[0] for r in RARITY}          # Common C, Uncommon U, ... Mythic M
TOKEN = "abilityToken"

BLOCKED, CAPPED, OK = "badge-blocked", "capped", "ok"
ORDER = (BLOCKED, CAPPED, OK)
HEADINGS = {
    BLOCKED: "BADGE BLOCKED - needs a rarity whose roster demand exceeds stock",
    CAPPED: "CAPPED - every ability is at the end of the cost table",
    OK: "OK - every badge pool this hero draws on covers roster demand",
}
ONLY = {"blocked": BLOCKED, "capped": CAPPED, "ok": OK}
RANK_CAP = {v: k for k, v in ONLY.items()}


# --- data --------------------------------------------------------------------

def load_costs():
    """The 59-entry badge ladder (all heroes; MoWs use a different table)."""
    if not GAMECONFIG.exists():
        sys.exit(f"error: {GAMECONFIG} missing - the ability cost table only "
                 "exists in the raw datamine, see research/README.md")
    try:
        raw = json.loads(GAMECONFIG.read_text())
        costs = raw[CC]["units"]["abilityUpgradeCosts"]
    except (json.JSONDecodeError, KeyError) as error:
        sys.exit(f"error: cannot read abilityUpgradeCosts from "
                 f"{GAMECONFIG}: {error}")
    if not isinstance(costs, list) or not costs:
        sys.exit(f"error: abilityUpgradeCosts is not a list in {GAMECONFIG}")
    return costs


def load_stock(player):
    """{alliance: {rarity: amount}} from inventory.abilityBadges."""
    stock = {}
    badges = (player.get("inventory") or {}).get("abilityBadges") or {}
    for alliance, entries in badges.items():
        stock[alliance] = {
            str(e.get("rarity")): int(e.get("amount") or 0)
            for e in entries or []}
    return stock


def cost_entry(ability, costs):
    """entry for the next level, or None when the ladder has no rung left."""
    level = int(ability.get("level") or 0)
    index = level - 1                       # verified: level L -> entry L-1
    return costs[index] if 0 <= index < len(costs) else None


def token_of(entry):
    """('Uncommon', 2) from {'gold': 800, 'abilityTokenUncommon': 2}."""
    for key in entry:
        if key.startswith(TOKEN) and key != TOKEN:
            rarity = key[len(TOKEN):]
            if rarity in ABBR:
                return rarity, int(entry[key] or 0)
    return None, 0


def roster_demand(units, costs):
    """{(alliance, rarity): badges demanded by every next-level upgrade}."""
    demand = Counter()
    for unit in units:
        if unit.get("id") in MOW_IDS:
            continue
        alliance = unit.get("grandAlliance") or "?"
        for ability in unit.get("abilities") or []:
            entry = cost_entry(ability, costs)
            if not entry:
                continue
            rarity, count = token_of(entry)
            if rarity:
                demand[(alliance, rarity)] += count
    return demand


def short_pools(stock, demand):
    """Every (alliance, rarity) where roster demand beats the held stock."""
    pools = []
    for alliance in sorted(stock):
        for rarity in RARITY:
            held = stock[alliance].get(rarity, 0)
            need = demand[(alliance, rarity)]
            if need > held:
                pools.append({"alliance": alliance, "rarity": rarity,
                              "demand": need, "stock": held,
                              "short": need - held})
    return pools


# --- one unit ----------------------------------------------------------------

def analyse(unit, costs, stock, demand):
    alliance = unit.get("grandAlliance") or "?"
    abilities, needs = [], Counter()
    gold = 0
    for ability in unit.get("abilities") or []:
        aid = ability.get("id", "?")
        level = int(ability.get("level") or 0)
        entry = cost_entry(ability, costs)
        if entry is None:                   # level 60 (or below the floor)
            abilities.append({"id": aid, "level": level, "capped": True,
                              "next_level": None, "rarity": None,
                              "need": 0, "gold": 0})
            continue
        rarity, count = token_of(entry)
        step_gold = int(entry.get("gold") or 0)
        gold += step_gold
        if rarity:
            needs[rarity] += count
        abilities.append({"id": aid, "level": level, "next_level": level + 1,
                          "capped": False, "rarity": rarity, "need": count,
                          "gold": step_gold})

    held_for = stock.get(alliance) or {}
    short = {r: n for r, n in needs.items()
             if demand[(alliance, r)] > held_for.get(r, 0)}
    if not any(not a["capped"] for a in abilities):
        status = CAPPED
    elif short:
        status = BLOCKED
    else:
        status = OK

    return {
        "name": unit.get("name", "?"),
        "id": unit.get("id", "?"),
        "faction": unit.get("faction", "?"),
        "alliance": alliance,
        "rank": int(unit.get("rank") or 0),
        "rank_name": rank_name(int(unit.get("rank") or 0)),
        "abilities": abilities,
        "needs": dict(needs),
        "short": short,
        "short_total": sum(short.values()),
        "gold": gold,
        "status": status,
    }


# --- rendering ---------------------------------------------------------------

def need_str(needs):
    parts = [f"{ABBR[r]}:{n}" for r, n in sorted(
        needs.items(), key=lambda kv: RARITY.index(kv[0])
        if kv[0] in RARITY else 99) if r in ABBR and n]
    return " ".join(parts) or "-"


def stock_line(stock):
    bits = []
    for alliance in sorted(stock):
        bits.append(f"{alliance} " + " ".join(
            f"{ABBR[r]}{stock[alliance].get(r, 0)}" for r in RARITY))
    return " | ".join(bits) or "empty"


def full_table(rows, meta, stock, pools, counts, gold_total, show):
    pl = meta.get("powerLevel", "?")
    print(f"=== Ability gate === snapshot {meta.get('lastUpdatedOn', '?')} | "
          f"{meta.get('name', '?')} power level {pl}")
    print(f"badge stock (ONE shared pool per alliance): {stock_line(stock)}")
    if pools:
        print("short pools: " + " | ".join(
            f"{p['alliance']} {p['rarity']} {p['demand']} needed / "
            f"{p['stock']} held ({p['short']} short)" for p in pools))
    else:
        print("short pools: none - every pool covers roster demand")

    w_name = max([len(r["name"]) for r in rows] + [9])
    w_rank = max([len(r["rank_name"]) for r in rows] + [4])
    for status in ORDER:
        if not counts.get(status) or status not in show:
            continue
        group = [r for r in rows if r["status"] == status]
        heading = HEADINGS[status]
        print(f"\n--- {heading} [{len(group)}] "
              + "-" * max(1, 74 - len(heading)))
        print(f"  {'Character':<{w_name}}  {'Rank':<{w_rank}}  "
              f"{'Alliance':<8}  {'Lvls':<12}  {'Need':<8}  {'Gold':>9}")
        print("  " + " " * w_name + "  " + " " * w_rank +
              "  " + "-" * 8 + "  " + "-" * 12 + "  " + "-" * 8 + "  " + "-" * 9)
        for r in group:
            lvls = "/".join(str(a["level"]) for a in r["abilities"]) or "-"
            gold = format(r["gold"], ",") if r["gold"] else "-"
            print(f"  {r['name']:<{w_name}}  {r['rank_name']:<{w_rank}}  "
                  f"{r['alliance']:<8}  {lvls:<12}  {need_str(r['needs']):<8}  "
                  f"{gold:>9}")

    print(f"\nroster: {sum(counts.values())} characters | " + " | ".join(
        f"{RANK_CAP[s]} {counts.get(s, 0)}" for s in ORDER))
    print(f"gold: {gold_total:,} would cover every next upgrade "
          "(no gold balance exists in the snapshot - gold never gates here)")
    if pools:
        print("caveat: 'Need' is what ONE hero wants; the short-pools line "
              "above is the real gap")
        print("        (stock is ONE shared pool per alliance). 11 machines "
              "of war excluded - separate cost table.")


def show_detail(row, stock, demand):
    held_for = stock.get(row["alliance"]) or {}
    print(f"=== {row['name']} ({row['id']}) === rank {row['rank_name']} | "
          f"{row['alliance']} | {row['faction']}")
    for a in row["abilities"]:
        if a["capped"]:
            print(f"  {a['id']:<24} L{a['level']}  - end of the cost table "
                  "(nothing left to price)")
            continue
        held = held_for.get(a["rarity"], 0)
        need = demand[(row["alliance"], a["rarity"])]
        marker = "SHORT" if need > held else "ok"
        print(f"  {a['id']:<24} L{a['level']} -> L{a['next_level']}  "
              f"{a['gold']:>7,} gold + {a['need']}x {a['rarity']} badge   "
              f"[pool {need}/{held} {marker}]")
    pools = [f"{p['rarity']} {p['demand']}/{p['stock']} (-{p['short']})"
             for p in pools_for(row, stock, demand)]
    print(f"  need: {need_str(row['needs'])} ({row['alliance']}) | "
          f"gold: {row['gold']:,}")
    if row["status"] == BLOCKED:
        print(f"  verdict: badge-blocked - short pools: {', '.join(pools)}")
    elif row["status"] == CAPPED:
        print("  verdict: capped - both abilities sit at the end of the "
              "59-entry cost table")
    else:
        print("  verdict: ok - every pool it draws on covers roster demand")
    print("caveat: badge stock is ONE shared pool per alliance - 'short' is "
          "measured roster-wide,\n        not after other heroes are served.")


def pools_for(row, stock, demand):
    held_for = stock.get(row["alliance"]) or {}
    return [{"rarity": r, "demand": demand[(row["alliance"], r)],
             "stock": held_for.get(r, 0),
             "short": demand[(row["alliance"], r)] - held_for.get(r, 0)}
            for r in sorted(row["needs"],
                            key=lambda x: RARITY.index(x) if x in RARITY else 99)
            if demand[(row["alliance"], r)] > held_for.get(r, 0)]


# --- entry points ------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="*",
                    help="character NAME or id (partial ok; names are what the "
                         "output prints). Omit for the full roster table")
    ap.add_argument("--only", choices=sorted(ONLY),
                    help="show just one status group")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    costs = load_costs()
    player, meta = load_player()
    stock = load_stock(player)
    units = [u for u in player.get("units") or []
             if u.get("id") not in MOW_IDS]
    demand = roster_demand(units, costs)
    pools = short_pools(stock, demand)

    rows = [analyse(u, costs, stock, demand) for u in units]
    rank_of = {s: i for i, s in enumerate(ORDER)}
    rows.sort(key=lambda r: (rank_of[r["status"]],
                             r["short_total"] if r["status"] == BLOCKED else 0,
                             r["name"]))

    details = player.get("details") or {}
    meta_out = {
        "snapshot": meta.get("lastUpdatedOn"),
        "player": details.get("name"),
        "power_level": details.get("powerLevel"),
        "stock": stock,
        "short_pools": pools,
        "gold_total": sum(r["gold"] for r in rows),
    }

    # ---- one hero -----------------------------------------------------------
    if args.query:
        needle = " ".join(args.query).replace("_", " ").lower()
        match = next((r for r in rows if r["id"].lower() == needle), None)
        if match is None:
            match = next((r for r in rows if needle in r["id"].lower()), None)
        if match is None:
            match = next((r for r in rows if needle in r["name"].lower()), None)
        if match is None:
            sys.exit(f"error: no character matches '{' '.join(args.query)}'")
        if args.json:
            json.dump({"schema_version": 1, "meta": meta_out, "unit": match,
                       "pools": pools_for(match, stock, demand)},
                      sys.stdout, indent=1)
            print()
            return
        show_detail(match, stock, demand)
        return

    # ---- full table ---------------------------------------------------------
    if args.only:
        want = ONLY[args.only]
        shown = [r for r in rows if r["status"] == want]
    else:
        shown = rows

    if args.json:
        json.dump({"schema_version": 1, "meta": meta_out, "rows": shown},
                  sys.stdout, indent=1)
        print()
        return

    counts = Counter(r["status"] for r in rows)
    show = {ONLY[args.only]} if args.only else set(ORDER)
    full_table(shown, {**details, "lastUpdatedOn": meta.get("lastUpdatedOn")},
               stock, pools, counts, meta_out["gold_total"], show)


if __name__ == "__main__":
    run(main)
