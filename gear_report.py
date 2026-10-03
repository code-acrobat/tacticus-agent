#!/usr/bin/env python3
"""Gear parity report: legendary equipment for the roster, slot by slot.

Each hero wears gear in 3 slots (Slot1 = Crit, Slot2 = Defensive|Block,
Slot3 = Booster; the accepted types per unit live in
clientGameConfig.units.lineup.<id>.itemSlots - the 11 machines of war have no
slots and are excluded, like in rank_up_report).

Model
  * target     the rarity the report aims for (default Legendary, the in-game
               "green circle" ideal: a Legendary+ hero wearing sub-Legendary
               gear). --target lets you check any rung.
  * green      attribute in the full list: unit rarity >= target AND at least
               one slot below target. That is the client's green-circle hint,
               recomputed here (the hint itself is not in any config file).
  * relic      R_* pieces are relics: normal (Mythic) rarity plus a separate
               `isRelic` flag, so they get their own Relic column in the full
               table (Haarken's Helspear, Gulgortz's Headwoppa's Killchoppa)
               and a RELIC marker in the detail view. A relic only fits the
               slot indices named in the unit's `itemSlotsRelic`; relic SPARES
               in inventory.items carry no `rarity` field, so theirs comes
               from the catalog (never from parsing the id).
  * fill       how many of the below-target slots a spare from
               inventory.items can cover. Worn gear is a per-unit COPY (no
               transfer field exists anywhere in the snapshot and the API is
               read-only), so the plan only ever allocates SPARES - it never
               tells you to move a piece off another hero.
  * shared     inventory.items is ONE pool: the fill plan is greedy (green
               units first, most-constrained slot first, exact-target rarity
               before Mythic, higher level first) and never overspends an
               amount.
  * buy        slots no spare can cover: Daily Deals sell a RANDOM
               itemsLegendary roll for 499 BS, 2/day (ladder from
               tacticus-shop-prices.json other_prices.equipment_bs), with the
               crusade-shop price derived from the datamine when present.
   * variant    gear of one type comes in lines that differ in SHAPE, not in
                rarity (level-1 stats from the catalog): crit = gun
                (critChance 35, 49 items) vs knife (20, 11), with 25/30/40
                outliers; defensive = hp+armour (34) vs pure armour (6, the
                "Plated Greaves" line); block = chance lines 20 Refractor /
                25 Power / 30 Force / 35 Iron Halo. Chance NEVER scales with
                level - only the damage side does. Tagged in the detail view
                and in --json (`variant`).
   * pref       community rule of thumb per hero, derived from
                units.lineup.<id>.weapons[].hits and .traits: 1 hit -> knife,
                3+ hits -> gun (crit chance compounds on multi-hits), 2 hits
                ~ marginal; ActOfFaith (Sisters) -> always knife; MkXGravis
                (Bellator, Calgar, Burchard, Nubari) -> pure armour, everyone
                else hp+armour. A signal, not an order.

Faction/unit locks come from the planner catalog (research/tacticus-planner-api
Data/equipment/*.json -> allowedFactions / allowedUnits); an empty list means
unrestricted. Gear is never crafted and campaigns drop essentially none, so
there is no "farm" column here.

Usage:
    python3 gear_report.py                   # full roster table (pipe to fzf)
    python3 gear_report.py neurothrope       # one character, slot detail
    python3 gear_report.py --target Mythic   # aim at a different rung
    python3 gear_report.py --json            # machine-readable
"""
import argparse
import json
import pathlib
import re
import sys

from rank_up_report import MOW_IDS, load_player, rank_name, run

BASE = pathlib.Path(__file__).resolve().parent
GAMECONFIG = BASE / "research" / "datamine" / "gameconfig142.json"
SHOP_PRICES = BASE / "tacticus-shop-prices.json"
EQUIP_DIR = (BASE / "research" / "tacticus-planner-api" / "src"
             / "TacticusPlanner.GameCatalog" / "Data" / "equipment")
CC = "clientGameConfig"

RARITY = ("Common", "Uncommon", "Rare", "Epic", "Legendary", "Mythic")
RARITY_RANK = {r: i for i, r in enumerate(RARITY)}
# first letter used in the table cells: C U R E L M
LETTER = {r: r[0] for r in RARITY}


def norm_type(value):
    """'I_Booster_Block' / 'R_Crit' / 'boosterBlock' -> 'booster_block' style."""
    text = str(value or "").lower()
    text = text.replace("boosterblock", "booster_block").replace("boostercrit", "booster_crit")
    bits = [b for b in text.split("_") if b and b not in ("i", "r")]
    if len(bits) == 3 and bits[0] == "booster":        # booster + crit + stray
        bits = ["booster_" + bits[1]]
    return "_".join(bits)


def type_of(item_id, catalog=None):
    """Best-effort slot type of an item id ('I_Crit_L007' -> 'crit')."""
    entry = (catalog or {}).get(item_id)
    if entry and entry.get("type"):
        return norm_type(entry["type"])
    base = re.sub(r"_[curelm]\d+$", "", str(item_id or ""))   # strip _L007
    bits = base.split("_")
    if len(bits) >= 3 and bits[1] == "booster":
        return "booster_" + bits[2]
    if len(bits) >= 2:
        return bits[1]
    return ""


# --- data --------------------------------------------------------------------


def load_config():
    """Unit rarities + accepted slot types, from the raw datamine."""
    if not GAMECONFIG.exists():
        sys.exit(f"error: {GAMECONFIG} missing - see research/README.md")
    try:
        raw = json.loads(GAMECONFIG.read_text())
        units_cfg = raw[CC]["units"]
        steps = units_cfg["heroProgressionSteps"]
        lineup = units_cfg.get("lineup") or {}
    except (KeyError, json.JSONDecodeError) as error:
        sys.exit(f"error: unexpected datamine layout ({error}); update gear_report.py")
    return {"steps": steps, "lineup": lineup, "raw": raw}


def load_catalog():
    """Planner equipment catalog: type, faction/unit locks, isRelic."""
    if not EQUIP_DIR.exists():
        sys.exit(f"error: {EQUIP_DIR} missing - see research/README.md")
    catalog = {}
    for path in sorted(EQUIP_DIR.glob("*.json")):
        try:
            entries = json.loads(path.read_text())
        except json.JSONDecodeError as error:
            sys.exit(f"error: {path} is not valid JSON: {error}")
        for entry in entries if isinstance(entries, list) else []:
            catalog[entry.get("id")] = entry
    if not catalog:
        sys.exit(f"error: no equipment entries under {EQUIP_DIR}")
    return catalog


def load_prices():
    """Recorded shop ladder (other_prices.equipment_bs) with fallbacks."""
    prices = {}
    if SHOP_PRICES.exists():
        try:
            doc = json.loads(SHOP_PRICES.read_text())
            prices = {k: int(v) for k, v in
                      ((doc.get("other_prices") or {}).get("equipment_bs") or {}).items()}
        except (ValueError, TypeError):
            prices = {}
    return prices


def merchant_price(cfg, merchant, rarity, cost_type):
    """First matching gear offer amount in a merchant's products (deep walk)."""
    try:
        products = cfg["raw"][CC]["shop"]["merchants"][merchant]["products"]
    except (KeyError, TypeError):
        return None
    found = [None]

    def walk(node):
        if isinstance(node, list):
            for child in node:
                walk(child)
        elif isinstance(node, dict):
            reward = str(node.get("reward") or "")
            cost = node.get("cost") or {}
            if (reward == f"items{rarity}" or reward.startswith(f"items{rarity}_")) \
                    and cost.get("type") == cost_type:
                found[0] = int(cost.get("amount") or 0)
            for value in node.values():
                if isinstance(value, (list, dict)):
                    walk(value)

    walk(products)
    return found[0]


def compatible(entry, unit_id, faction):
    """Faction/unit locks; empty list = unrestricted (verified vs 318 worn)."""
    if not entry:
        return True
    factions = entry.get("allowedFactions") or []
    if factions and faction not in factions:
        return False
    units = entry.get("allowedUnits") or []
    if units and unit_id not in units:
        return False
    return True



def variant(item_id, catalog):
    """Shape tag for one piece, from the catalog's level-1 stats.

    Chance values are constant across levels, so stats[0] is enough. Returns
    e.g. "crit35 gun", "crit20 knife", "hp+armour", "armour", "block35",
    "crit+5", "block+3" - or "-" when nothing distinguishes the line.
    """
    entry = (catalog or {}).get(item_id) or {}
    levels = entry.get("levels") or []
    stats = (levels[0].get("stats") or {}) if levels else {}
    if "critChance" in stats:
        chance = stats["critChance"]
        kind = "gun" if chance >= 33 else ("mid" if chance > 27 else "knife")
        return f"crit{chance} {kind}"
    if "blockChance" in stats:
        return f"block{stats['blockChance']}"
    if "armor" in stats:
        return "hp+armour" if "hp" in stats else "armour"
    if "critChanceBonus" in stats:
        return f"crit+{stats['critChanceBonus']}"
    if "blockChanceBonus" in stats:
        return f"block+{stats['blockChanceBonus']}"
    if entry.get("isRelic") or str(item_id or "").startswith("R_"):
        return "relic"
    return "-"


def hero_preference(lineup):
    """Community rule of thumb for this hero's slot shapes.

    crit:  1 hit -> knife (all damage on one crit), 3+ hits -> gun (crit
           chance compounds: a hit can only crit if the previous one did),
           2 hits ~ marginal; ActOfFaith (Sisters) -> always knife.
    armour: MkXGravis trait -> pure armour (the trait already grants HP),
            everyone else -> hp+armour (pierce ignores armour; HP is healable).
    Only the crit-camp buff case (Howl/Aethana/Eldryon pushing back to knife)
    is NOT data-detectable here.
    """
    weapons = lineup.get("weapons") or []
    hits = max((w.get("hits") or 0) for w in weapons) if weapons else None
    traits = set(lineup.get("traits") or [])
    if "ActOfFaith" in traits:
        crit = "knife (ActOfFaith)"
    elif hits is None:
        crit = "? (no weapon data)"
    elif hits == 1:
        crit = "knife (1 hit)"
    elif hits >= 3:
        crit = f"gun ({hits} hits compound)"
    else:
        crit = "marginal (2 hits: knife ~ gun)"
    armour = ("pure armour (MkXGravis)" if "MkXGravis" in traits
              else "hp+armour")
    return {
        "hits": hits,
        "crit": crit,
        "armour": armour,
        "traits": sorted(traits),
    }


# --- model -------------------------------------------------------------------

def build_units(player, cfg, catalog, target_rank):
    rows = []
    for unit in player.get("units") or []:
        uid = unit.get("id")
        if uid in MOW_IDS:
            continue                      # 0 slots - MoWs level abilities
        pi = unit.get("progressionIndex")
        step = cfg["steps"][pi] if isinstance(pi, int) and 0 <= pi < len(cfg["steps"]) else {}
        unit_rarity = step.get("rarity") or "?"
        lineup = cfg["lineup"].get(uid) or {}
        slot_types = lineup.get("itemSlots") or []
        # itemSlotsRelic = indices (into itemSlots) whose slot accepts relics
        relic_slots = {int(i) for i in (lineup.get("itemSlotsRelic") or [])}
        items = {it.get("slotId"): it for it in (unit.get("items") or [])}
        slots = []
        for index, slot_id in enumerate(("Slot1", "Slot2", "Slot3")):
            accepted = slot_types[index] if index < len(slot_types) else None
            if isinstance(accepted, list):
                accepted = [norm_type(a) for a in accepted]
            elif accepted:
                accepted = [norm_type(accepted)]
            else:                                  # fall back to what is worn
                worn0 = items.get(slot_id)
                accepted = [type_of(worn0.get("id"), catalog)] if worn0 else []
            worn = items.get(slot_id)
            rarity = (worn or {}).get("rarity")
            below = RARITY_RANK.get(rarity, -1) < target_rank
            relic = bool(worn) and (str(worn.get("id") or "").startswith("R_")
                                    or (catalog.get(worn.get("id")) or {}).get("isRelic"))
            slots.append({
                "slotId": slot_id,
                "accepted": accepted,
                "relic": relic,
                "relic_ok": index in relic_slots,
                "item": None if not worn else {
                    "id": worn.get("id"), "name": worn.get("name"),
                    "rarity": rarity, "level": worn.get("level"),
                    "variant": variant(worn.get("id"), catalog)},
                "below": bool(worn is None or below),
            })
        below_count = sum(1 for s in slots if s["below"])
        rows.append({
            "name": unit.get("name", "?"), "id": uid,
            "faction": unit.get("faction", "?"),
            "rank": int(unit.get("rank") or 0),
            "rank_name": rank_name(int(unit.get("rank") or 0)),
            "rarity": unit_rarity,
            "rarity_rank": RARITY_RANK.get(unit_rarity, -1),
            "slots": slots, "below": below_count, "fill": 0,
            "relic_slots": relic_slots,
            "relics": [s["item"]["name"] for s in slots if s["relic"] and s["item"]],
            "pref": hero_preference(lineup),
            "green": RARITY_RANK.get(unit_rarity, -1) >= target_rank and below_count > 0,
        })
    return rows


def spares_by_type(player, catalog, target_rank):
    """id -> {spare info, amount} for every piece at/above the target.

    Relic spares (R_*) carry NO `rarity` field in the snapshot, so fall back to
    the catalog entry - never guess from the id: `R_Booster_Crit_OrbsOfDecay`
    contains `_C` and would parse as Common.
    """
    pool = {}
    for entry in (player.get("inventory") or {}).get("items") or []:
        item_id = entry.get("id")
        meta = catalog.get(item_id) or {}
        rarity = entry.get("rarity") or meta.get("rarity")
        rank = RARITY_RANK.get(rarity, -1)
        if rank < target_rank:
            continue
        pool[item_id] = {
            "id": item_id,
            "name": entry.get("name") or meta.get("name"),
            "rarity": rarity or "?", "rarity_rank": rank,
            "level": int(entry.get("level") or 1),
            "amount": int(entry.get("amount") or 0),
            "type": type_of(item_id, catalog),
            "variant": variant(item_id, catalog),
            "relic": bool(meta.get("isRelic")) or str(item_id).startswith("R_"),
        }
    return pool


def candidates_for(slot, unit, pool, catalog, target_rank, amounts=None):
    """Spare pieces that fit one slot: type + locks + rarity >= target.

    A relic spare can only go into a slot listed in the unit's
    `itemSlotsRelic` indices (index 0/1/2 = Slot1/Slot2/Slot3); ordinary
    spares fit any slot of the right type, relic slot included.
    """
    index = int(str(slot.get("slotId", "Slot1"))[-1] or 1) - 1
    out = []
    for entry in pool.values():
        left = (amounts or {}).get(entry["id"], entry["amount"])
        if left <= 0 or entry["type"] not in slot["accepted"]:
            continue
        if entry.get("relic") and index not in unit.get("relic_slots", ()):
            continue
        if not compatible(catalog.get(entry["id"]), unit["id"], unit["faction"]):
            continue
        out.append(entry)
    # exact target before Mythic (don't burn Mythic spares first), level high first
    out.sort(key=lambda e: (0 if e["rarity_rank"] == target_rank else 1,
                            e["rarity_rank"], -e["level"], e["id"]))
    return out


def allocate(units, pool, catalog, target_rank):
    """Greedy one-spare-per-gap fill. Green units first, tightest slot first."""
    gaps = []
    for unit in units:
        for slot in unit["slots"]:
            if not slot["below"]:
                continue
            opts = candidates_for(slot, unit, pool, catalog, target_rank)
            gaps.append((unit, slot, opts))
    gaps.sort(key=lambda g: (not g[0]["green"], len(g[2]),
                             -g[0]["rarity_rank"], g[0]["name"], g[1]["slotId"]))
    amounts = {eid: e["amount"] for eid, e in pool.items()}
    plan = []
    for unit, slot, opts in gaps:
        pick = next((o for o in opts if amounts.get(o["id"], 0) > 0), None)
        if pick:
            amounts[pick["id"]] -= 1
            unit["fill"] += 1
            plan.append({"unit": unit["id"], "name": unit["name"],
                         "slotId": slot["slotId"], "id": pick["id"],
                         "item_name": pick["name"], "rarity": pick["rarity"],
                         "level": pick["level"]})
    return plan, amounts


# --- rendering ---------------------------------------------------------------

def full_table(units, prices, meta, target, plan, pool):
    total_below = sum(u["below"] for u in units)
    total_fill = sum(u["fill"] for u in units)
    greens = [u for u in units if u["green"]]
    bs = prices.get(target)
    print(f"=== Gear parity vs {target} === snapshot {meta.get('lastUpdatedOn', '?')} | "
          f"{(player_details or {}).get('name', '?')} | "
          f"green = {target}+ hero with a sub-{target} slot")
    print(f"spares: {_pool_line} | buy: {bs} BS (daily deal, random roll, 2/day)"
          if bs else f"spares: {_pool_line}")
    crusade = _crusade_price
    if crusade:
        print(f"alternatives: crusade shop {crusade} crusade currency | "
              "worn gear is per-unit (not movable in the data) - fill uses spares only")
    else:
        print("worn gear is per-unit (not movable in the data) - fill uses spares only")

    w_name = max([len(u["name"]) for u in units] + [9])
    w_fac = max([len(u["faction"]) for u in units] + [7])
    w_rel = max([len(u["relics"][0]) for u in units if u["relics"]] + [5])
    head = (f"  {'Character':<{w_name}}  {'Faction':<{w_fac}}  {'Rank':>4}  {'Rarity':<9}"
            f"  {'S1':>3}  {'S2':>3}  {'S3':>3}  {'Relic':<{w_rel}}"
            f"  {'Below':>5}  {'Green':>5}  {'Fill':>5}")
    print("\n" + head)
    print("  " + " " * w_name + "  " + " " * w_fac + "  " + "-" * 4 + "  " + "-" * 9
          + "  " + "  ".join("-" * 3 for _ in range(3)) + "  " + "-" * w_rel
          + "  " + "-" * 5 + "  " + "-" * 5 + "  " + "-" * 5)
    for u in units:
        cells = []
        for slot in u["slots"]:
            it = slot["item"]
            marker = "R" if slot["relic"] else ""
            cells.append(f"{LETTER.get(it['rarity'], '?')}{it['level']}{marker}"
                         if it else "--")
        relic = u["relics"][0] if u["relics"] else "-"
        green = "yes" if u["green"] else "-"
        fill = f"{u['fill']}/{u['below']}" if u["below"] else "-"
        print(f"  {u['name']:<{w_name}}  {u['faction']:<{w_fac}}  {u['rank']:>4}"
              f"  {u['rarity']:<9}  {cells[0]:>3}  {cells[1]:>3}  {cells[2]:>3}"
              f"  {relic:<{w_rel}}  {u['below']:>5}  {green:>5}  {fill:>5}")

    relic_worn = [f"{u['name']} {u['relics'][0]}" for u in units if u["relics"]]
    relic_spares = [e for e in pool.values() if e.get("relic")]
    if relic_worn or relic_spares:
        print(f"\nrelics: {len(relic_worn)} worn ({', '.join(relic_worn) or '-'})"
              f" | {len(relic_spares)} spare in inventory"
              + (f" ({', '.join(sorted(e['id'] for e in relic_spares))})"
                 if relic_spares else ""))

    below_units = sum(1 for u in units if u["below"])
    print(f"\nroster: {len(units)} | green {len(greens)} | below {target} {below_units}"
          f" | slots below {total_below} | filled from spares {total_fill}"
          f" | still to buy {total_below - total_fill}")
    if total_below - total_fill:
        price_bits = (f"{bs} BS each in Daily Deals (random roll, 2/day)"
                      if bs else f"no {target} slot in the Daily Deals ladder")
        if _crusade_price:
            price_bits += f", {_crusade_price} in the crusade shop"
        print(f"caveat: buy = {price_bits} - verdicts are signals, not orders; "
              "faction locks are checked, levels are not (levelling costs "
              "salvage + gold, unmodelled).")


_pool_line = ""
_crusade_price = None
player_details = {}


def show_detail(matches, pool, catalog, target, target_rank, prices, cfg, plan):
    for unit in matches:
        print(f"=== {unit['name']} ({unit['id']}) === {unit['faction']} | "
              f"rank {unit['rank']} ({unit['rank_name']}) | rarity {unit['rarity']}"
              + (" | GREEN" if unit["green"] else ""))
        types = [t for s in unit["slots"] for t in s["accepted"]]
        pref_bits = []
        if any(t == "crit" for t in types):
            pref_bits.append(f"crit -> {unit['pref']['crit']}")
        if any(t == "defensive" for t in types):
            pref_bits.append(f"armour -> {unit['pref']['armour']}")
        if any(t == "block" for t in types):
            pref_bits.append("block -> highest chance line (35 Iron Halo)")
        if pref_bits:
            print("  pref (community): " + " | ".join(pref_bits))
        for slot in unit["slots"]:
            it = slot["item"]
            state = "BELOW" if slot["below"] else "ok"
            flags = []
            if slot.get("relic"):
                flags.append("RELIC")
            if slot.get("relic_ok"):
                flags.append("relic-slot")
            suffix = ("" if not flags else "  [" + ", ".join(flags) + "]")
            if it:
                print(f"  {slot['slotId']}  {it['id']:<22} {str(it['name'])[:28]:<28}"
                      f" {LETTER.get(it['rarity'], '?')}{it['level']:<3}"
                      f" {str(it.get('variant') or '-'):<13} {state}"
                      f"  accepts {','.join(slot['accepted']) or '?'}{suffix}")
            else:
                print(f"  {slot['slotId']}  {'(empty)':<22} {'':<28} {'':>3} {state}"
                      f"  accepts {','.join(slot['accepted']) or '?'}{suffix}")
            if not slot["below"]:
                continue
            opts = candidates_for(slot, unit, pool, catalog, target_rank)
            if opts:
                for entry in opts[:4]:
                    print(f"      spare: {entry['id']:<22} {str(entry['name'])[:28]:<28}"
                          f" {LETTER.get(entry['rarity'], '?')}{entry['level']:<3}"
                          f" {entry.get('variant') or '-':<13} x{entry['amount']}")
                if len(opts) > 4:
                    print(f"      ... and {len(opts) - 4} more compatible spares")
            else:
                bs = prices.get(unit_rarity_price_key(target))
                bits = [f"{bs} BS daily deal (random roll, 2/day)"] if bs else []
                if _crusade_price:
                    bits.append(f"{_crusade_price} crusade currency")
                print(f"      no spare fits - buy: {' or '.join(bits) or 'see shop'}")
        mine = [p for p in plan if p["unit"] == unit["id"]]
        if mine:
            print("  fill plan (ONE shared pool, greedy):")
            for pick in mine:
                print(f"    {pick['slotId']} <- {pick['id']} "
                      f"({pick['item_name']}, {pick['rarity']} L{pick['level']})")
        elif unit["below"]:
            print("  fill plan: nothing left in the spare pool for this hero")
        else:
            print(f"  all 3 slots are {target}+ - nothing to fill")
        print()


def unit_rarity_price_key(target):
    return target


def sort_units(units):
    units.sort(key=lambda u: (not u["green"], -u["below"], -u["rarity_rank"], u["name"]))


def main():
    global _pool_line, _crusade_price, player_details
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="*",
                    help="character NAME or id (partial ok) for slot detail. "
                         "Omit for the full roster table")
    ap.add_argument("--target", choices=list(RARITY), default="Legendary",
                    help="rarity to aim for (default Legendary)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    player, meta = load_player()
    cfg = load_config()
    catalog = load_catalog()
    prices = load_prices()
    player_details = player.get("details") or {}
    target_rank = RARITY_RANK[args.target]

    pool = spares_by_type(player, catalog, target_rank)
    units = build_units(player, cfg, catalog, target_rank)
    sort_units(units)
    plan, amounts = allocate(units, pool, catalog, target_rank)

    _pool_line = (f"{len(pool)} spare ids, {sum(e['amount'] for e in pool.values())} pieces "
                  f"(ONE shared pool)")
    _crusade_price = merchant_price(cfg, "crusadeShop", args.target, "crusadeCurrency")

    # ---- detail (query) --------------------------------------------------
    if args.query:
        needle = " ".join(args.query).lower().replace("_", " ")
        matches = [u for u in units
                   if needle in u["name"].lower() or needle in u["id"].lower()
                   or needle.replace(" ", "") in u["name"].lower().replace(" ", "")]
        if not matches:
            sys.exit(f"error: no character matches {' '.join(args.query)!r}")
        if args.json:
            out = []
            for unit in matches:
                slots = []
                for slot in unit["slots"]:
                    opts = candidates_for(slot, unit, pool, catalog, target_rank)
                    slots.append({
                        "slotId": slot["slotId"], "accepted": slot["accepted"],
                        "below": slot["below"], "item": slot["item"],
                        "relic": slot["relic"], "relic_ok": slot["relic_ok"],
                        "candidates": [{k: e.get(k) for k in
                                        ("id", "name", "rarity", "level", "amount",
                                         "relic", "variant")}
                                       for e in opts],
                        "buy_bs": prices.get(args.target) if slot["below"] and not opts else None,
                    })
                out.append({k: unit[k] for k in
                            ("name", "id", "faction", "rank", "rank_name",
                             "rarity", "green", "below", "fill", "relics")}
                           | {"target": args.target, "slots": slots,
                              "preference": unit["pref"],
                              "assigned": [p for p in plan
                                           if p["unit"] == unit["id"]]})
            payload = (out[0] if len(out) == 1 else out)
            json.dump({"schema_version": 1, **payload}, sys.stdout, indent=1)
            print()
            return
        show_detail(matches, pool, catalog, args.target, target_rank, prices, cfg, plan)
        return

    # ---- full table ------------------------------------------------------
    if args.json:
        rows = []
        for u in units:
            rows.append({
                "name": u["name"], "id": u["id"], "faction": u["faction"],
                "rank": u["rank"], "rank_name": u["rank_name"], "rarity": u["rarity"],
                "green": u["green"], "below": u["below"], "fill": u["fill"],
                "relics": u["relics"], "preference": u["pref"],
                "slots": [{"slotId": s["slotId"], "below": s["below"],
                           "relic": s["relic"], "relic_ok": s["relic_ok"],
                           **(s["item"] or {"id": None, "name": None,
                                            "rarity": None, "level": None,
                                            "variant": None})}
                          for s in u["slots"]],
            })
        relic_spares = sorted(e["id"] for e in pool.values() if e.get("relic"))
        json.dump({"schema_version": 1,
                   "meta": {"snapshot": meta.get("lastUpdatedOn"),
                            "player": player_details.get("name"),
                            "target": args.target,
                            "buy_bs": prices.get(args.target),
                            "crusade_price": _crusade_price,
                            "spare_ids": len(pool),
                            "spare_pieces": sum(e["amount"] for e in pool.values()),
                            "relic_spares": relic_spares,
                            "relics_worn": [f"{u['name']}: {u['relics'][0]}"
                                            for u in units if u["relics"]]},
                   "plan": plan,
                   "rows": rows}, sys.stdout, indent=1)
        print()
        return
    full_table(units, prices, meta, args.target, plan, pool)


if __name__ == "__main__":
    run(main)
