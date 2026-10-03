#!/usr/bin/env python3
"""XP gate: which characters are blocked by hero XP, and what unblocks them.

Items are only half of a rank-up. Every grid cell also requires the unit's
xpLevel to be at least xpLevelRequirements[rank][cell] (see AGENTS.md,
"XP gate & XP economics"). This tool reports that half only - it never prices
items, because that is rank_up_report.py's concern.

Model
  * open cells   cell indices 0..5 NOT in unit['upgrades'] - the cells you
                 still need for the next rank-up.
  * requirement  clientGameConfig.units.xpLevelRequirements[rank][cell]
                 (row index = the unit's `rank` field, 0-based), compared
                 against unit['xpLevel'] - a discrete level, not raw xp.
  * xpLevels     cumulative XP per level, index = level - 2 (there is no
                 entry for level 1, so guard lvl >= 2).
  * cap          heroProgressionSteps[progressionIndex].maxXpLevel, the rarity
                 cap (Common 8 / Uncommon 17 / Rare 26 / Epic 35 /
                 Legendary 50 / Mythic 60). If the hardest open cell needs MORE
                 than the cap the unit is MAXED: books would change nothing, so
                 no gap and no book list is printed - just the cap and the
                 level it would need.
  * gap          xpLevels[maxReq - 2] - unit['xp'] for the hardest open cell.
  * plan         owned xpBooks first (free, apply-gold only), then the
                 guild-shop book offers actually unlocked at this power level.
                 The stock is ONE shared pool: every plan assumes it gets all
                 of it, so running two plans in parallel does not work.

The 11 machines of war are excluded (no rank grid - they level abilities).

Usage:
    python3 xp_gate.py                   # everything, grouped by status
    python3 xp_gate.py --only blocked    # just the book-blocked characters
    python3 xp_gate.py --json            # machine-readable
"""
import argparse
import json
import pathlib
import sys
from collections import Counter

from rank_up_report import GRID_SLOTS, MOW_IDS, MAX_RANK, load_player, rank_name, run

BASE = pathlib.Path(__file__).resolve().parent
GAMECONFIG = BASE / "research" / "datamine" / "gameconfig142.json"
CC = "clientGameConfig"          # the datamine nests everything under this

# Statuses, in the order they are printed.
BLOCKED, MAXED, OK, FULL = "xp-blocked", "maxed", "xp-ok", "grid-full"
ORDER = (BLOCKED, MAXED, OK, FULL)
HEADINGS = {
    BLOCKED: "XP BLOCKED - the grid needs a higher level, books are the gate",
    MAXED: "MAXED - at the rarity XP cap, so books cannot help",
    OK: "XP OK - every open cell's level requirement is already met",
    FULL: "GRID FULL - nothing left to unlock on this rung",
}
ONLY_CHOICES = {BLOCKED: "blocked", MAXED: "maxed", OK: "ok", FULL: "full"}
RANK_CAP = {v: k for k, v in ONLY_CHOICES.items()}


# --- data --------------------------------------------------------------------

def load_config():
    """The four XP tables, straight out of the raw datamine."""
    if not GAMECONFIG.exists():
        sys.exit(f"error: {GAMECONFIG} missing - the XP tables only exist in "
                 "the raw datamine, see research/README.md")
    try:
        raw = json.loads(GAMECONFIG.read_text())
    except json.JSONDecodeError as error:
        sys.exit(f"error: {GAMECONFIG} is not valid JSON: {error}")
    try:
        units_cfg = raw[CC]["units"]
        cfg = {
            "req": units_cfg["upgradeSlots"]["xpLevelRequirements"],
            "levels": units_cfg["xpLevels"],
            "steps": units_cfg["heroProgressionSteps"],
            "books": {b["id"]: b for b in raw[CC]["consumables"]["xpBooks"]},
            "guild": raw[CC]["shop"]["merchants"]["guild"]["products"],
        }
    except KeyError as error:
        sys.exit(f"error: {GAMECONFIG} is missing {error} - the XP config "
                 "layout changed; update xp_gate.py")
    return cfg


def book_xp(cfg):
    return {bid: int(b.get("xpIncrease") or 0) for bid, b in cfg["books"].items()}


def book_gold(cfg):
    return {bid: int(b.get("gold") or 0) for bid, b in cfg["books"].items()}


def guild_offers(cfg, power_level):
    """Guild-shop XP-book offers actually unlocked at this power level.

    reward is a flat "id" or "id:count" string; conditions is a flat dict
    (minPowerLevel / maxPowerLevel / lockId) or {}. lockId and cronSchedule are
    ignored - they only rotate WHICH day an offer appears.
    """
    xp = book_xp(cfg)
    out = []
    for group in cfg["guild"]:
        for offer in group if isinstance(group, list) else [group]:
            reward = str((offer.get("reward") or "")).strip()
            rid, _, count = reward.partition(":")
            if rid not in xp:
                continue
            cost = offer.get("cost") or {}
            if cost.get("type") != "guildCredits":
                continue
            cond = offer.get("conditions") or {}
            if power_level < int(cond.get("minPowerLevel") or 0):
                continue
            if cond.get("maxPowerLevel") and power_level > int(cond["maxPowerLevel"]):
                continue
            raw_max = str(offer.get("maxPurchases") or "")
            out.append({
                "id": rid,
                "pieces": int(count) if count.isdigit() else 1,
                "gc": int(cost.get("amount") or 0),
                "max_purchases": int(raw_max) if raw_max.isdigit() else None,
            })
    return out


def plan_books(gap, stock, offers, xp_table, gold_table):
    """Cheapest route to `gap` XP: owned books first, then shop offers.

    Returns the plan plus whether it actually covers the gap (a stock that runs
    out mid-way is reported honestly rather than silently short).
    """
    need = gap
    used, buy = Counter(), Counter()
    gold = gc = 0
    for bid in sorted((b for b, n in stock.items() if n),
                      key=lambda b: -xp_table.get(b, 0)):
        if need <= 0 or not xp_table.get(bid):
            break
        take = min(stock[bid], -(-need // xp_table[bid]))   # ceil
        used[bid] += take
        gold += take * gold_table.get(bid, 0)
        need -= take * xp_table[bid]
    for offer in sorted(offers, key=lambda o: -xp_table.get(o["id"], 0)):
        if need <= 0 or not xp_table.get(offer["id"]):
            continue
        bought = 0
        while need > 0 and (offer["max_purchases"] is None
                            or bought < offer["max_purchases"]):
            bought += 1
            need -= offer["pieces"] * xp_table[offer["id"]]
        if bought:
            buy[offer["id"]] += bought * offer["pieces"]
            gc += bought * offer["gc"]
            gold += bought * offer["pieces"] * gold_table.get(offer["id"], 0)
    return {
        "stock": dict(used),
        "buy": dict(buy),
        "guild_credits": gc,
        "apply_gold": gold,
        "covered": need <= 0,
        "short_xp": max(0, need),
    }


# --- one unit ----------------------------------------------------------------

def analyse(unit, cfg, offers, stock, xp_table, gold_table):
    rank = int(unit.get("rank") or 0)
    filled = set(unit.get("upgrades") or [])
    open_cells = [c for c in range(GRID_SLOTS) if c not in filled]
    pi = unit.get("progressionIndex")
    step = cfg["steps"][pi] if isinstance(pi, int) and 0 <= pi < len(cfg["steps"]) else {}
    req_row = cfg["req"][rank] if 0 <= rank < len(cfg["req"]) else None

    row = {
        "name": unit.get("name", "?"),
        "id": unit.get("id", "?"),
        "faction": unit.get("faction", "?"),
        "rank": rank,
        "current": rank_name(rank),
        "next": rank_name(rank + 1) if rank < MAX_RANK else "MAX",
        "filled": len(filled),
        "missing": GRID_SLOTS - len(filled),
        "open_cells": open_cells,
        "xp": int(unit.get("xp") or 0),
        "xp_level": int(unit.get("xpLevel") or 1),
        "progression_index": pi,
        "rarity": step.get("rarity", "?"),
        "cap": step.get("maxXpLevel"),
        "max_req": None,
        "gap_xp": 0,
        "status": OK,
        "plan": None,
    }

    if not open_cells:
        row["status"] = FULL
        return row
    if req_row is None:                 # rank 20+ - no further grid we know
        row["status"] = MAXED
        return row

    reqs = [req_row[c] for c in open_cells]
    max_req = max(reqs)
    row["max_req"] = max_req
    cap = row["cap"]
    if cap is not None and max_req > int(cap):
        row["status"] = MAXED          # the AGENTS.md "clamp before quoting"
        return row
    if row["xp_level"] >= max_req:
        row["status"] = OK
        return row

    row["status"] = BLOCKED
    if max_req >= 2 and max_req - 2 < len(cfg["levels"]):
        row["gap_xp"] = max(0, cfg["levels"][max_req - 2] - row["xp"])
    if row["gap_xp"]:
        row["plan"] = plan_books(row["gap_xp"], stock, offers, xp_table, gold_table)
    return row


# --- rendering ---------------------------------------------------------------

def human(rows, meta, stock, offers, xp_table, show):
    pl = meta.get("powerLevel", "?")
    name = meta.get("name", "?")
    snapshot = meta.get("lastUpdatedOn", "?")
    print(f"=== XP gate === snapshot {snapshot} | {name} power level {pl}")

    stock_bits = ", ".join(
        f"{n}x {bid} ({xp_table.get(bid, 0)} XP)"
        for bid, n in sorted(stock.items(), key=lambda kv: -xp_table.get(kv[0], 0)) if n)
    total = sum(n * xp_table.get(bid, 0) for bid, n in stock.items())
    print(f"book stock: {stock_bits or 'empty'} = {total:,} XP  (ONE shared pool)")
    if offers:
        bits = ", ".join(
            f"{o['id']} {o['gc']} gc" + (f" x{o['pieces']}" if o["pieces"] > 1 else "")
            for o in sorted(offers, key=lambda o: -xp_table.get(o["id"], 0)))
        print(f"guild shop at PL{pl}: {bits}  (other book offers are power-locked)")
    else:
        print(f"guild shop at PL{pl}: no XP books unlocked")

    counts = Counter(r["status"] for r in rows)
    for status in ORDER:
        if not counts.get(status) or status not in show:
            continue
        group = [r for r in rows if r["status"] == status]
        print(f"\n--- {HEADINGS[status]} [{len(group)}] "
              + "-" * max(1, 70 - len(HEADINGS[status])))
        _table(group, status)

    print(f"\nroster: {len(rows)} characters | " + " | ".join(
        f"{ONLY_CHOICES[s]} {counts.get(s, 0)}" for s in ORDER))
    if counts.get(BLOCKED):
        print("caveat: the book stock is ONE shared pool - each plan above assumes")
        print("        it gets all of it. XP is one of three rank-up gates (items,")
        print("        XP, ascension) - see AGENTS.md.")


def _table(group, status):
    w_name = max([len(r["name"]) for r in group] + [9])
    w_cur = max([len(r["current"]) for r in group] + [7])
    w_next = max([len(r["next"]) for r in group] + [4])
    head = (f"  {'Character':<{w_name}}  {'Current':<{w_cur}}  {'Next':<{w_next}}"
            f"  {'Grid':>4}  {'Lvl':>3}  {'Need':>4}  {'Gap XP':>9}  Books")
    print(head)
    print("  " + " " * w_name + "  " + " " * w_cur + "  " + " " * w_next
          + "  " + "-" * 4 + "  " + "-" * 3 + "  " + "-" * 4 + "  " + "-" * 6
          + "  " + "-" * 5)
    for r in group:
        if status == BLOCKED:
            need, gap = str(r["max_req"]), f"{r['gap_xp']:,}"
            books = _plan_text(r["plan"])
        elif status == MAXED:
            need = str(r["max_req"])
            gap = "-"
            cap = r["cap"]
            books = (f"needs lvl {r['max_req']} > cap {cap}"
                     if cap is not None and r["max_req"] and r["max_req"] > cap
                     else "no grid row")
        else:
            need = str(r["max_req"] or "-")
            gap, books = "-", "-"
        grid = f"{r['filled']}/{GRID_SLOTS}"
        print(f"  {r['name']:<{w_name}}  {r['current']:<{w_cur}}  {r['next']:<{w_next}}"
              f"  {grid:>4}  {r['xp_level']:>3}  {need:>4}  {gap:>9}  {books}")


def _plan_text(plan):
    if not plan:
        return "-"
    parts = []
    if plan["stock"]:
        parts.append("stock")
    parts += [f"{n}x {bid}" for bid, n in plan["buy"].items()]
    text = " + ".join(parts) or "-"
    if plan["guild_credits"]:
        text += f" ({plan['guild_credits']} gc)"
    if not plan["covered"]:
        text += " SHORT"
    return text


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", choices=sorted(ONLY_CHOICES.values()),
                    help="show just one status group")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    cfg = load_config()
    player, meta = load_player()
    xp_table, gold_table = book_xp(cfg), book_gold(cfg)

    details = player.get("details") or {}
    offers = guild_offers(cfg, int(details.get("powerLevel") or 0))
    stock = {b["id"]: int(b.get("amount") or 0)
             for b in (player.get("inventory") or {}).get("xpBooks") or []}
    stock_total = sum(n * xp_table.get(bid, 0) for bid, n in stock.items())

    rows = []
    for unit in player.get("units") or []:
        if unit.get("id") in MOW_IDS:
            continue                      # no rank grid, they level abilities
        rows.append(analyse(unit, cfg, offers, stock, xp_table, gold_table))

    rank_of = {s: i for i, s in enumerate(ORDER)}
    rows.sort(key=lambda r: (rank_of[r["status"]], r["gap_xp"], r["name"]))
    if args.only:
        want = RANK_CAP[args.only]
        rows = [r for r in rows if r["status"] == want]

    if args.json:
        meta_out = {
            "snapshot": meta.get("lastUpdatedOn"),
            "player": details.get("name"),
            "power_level": details.get("powerLevel"),
            "book_stock": stock,
            "book_stock_xp": stock_total,
            "guild_offers": offers,
        }
        json.dump({"schema_version": 1, "meta": meta_out, "rows": rows},
                  sys.stdout, indent=1)
        print()
        return

    show = {RANK_CAP[args.only]} if args.only else set(ORDER)
    human(rows, {**details, "lastUpdatedOn": meta.get("lastUpdatedOn")},
          stock, offers, xp_table, show)


if __name__ == "__main__":
    run(main)
