#!/usr/bin/env python3
"""Rank-up proximity report: which characters are closest to their next rank.

Model (inferred from tacticus-player.json, see README):
  * A character's rank runs Stone -> Iron -> Bronze -> Silver -> Gold ->
    Diamond -> Adamantine, three steps each (rank index 0..20+).
  * Each rank-up requires filling all 6 cells of the 2x3 upgrade grid.
  * unit['upgrades'] lists the *filled* cell indices (0..5) toward the NEXT
    rank: no character in the data ever has 6 (promotion resets it), while 21
    characters sit at 5/6 unpromoted, so 6 - filled is the number of missing
    items.

With --energy the missing items are priced. Two modes:

  --energy            EXACT (default). Joins research/ (see energy_cost.py):
                      the 6 required item ids per character per rank, the
                      recipe graph expanded to leaves, the player's own
                      inventory deducted at every level, then the cheapest
                      campaign node per leaf (energyCost / effectiveRate).
                      Mercy is already inside effectiveRate.
  --energy --estimate the old shortcut: one rarity guessed from the target
                      rank, one flat rate, no recipes, no inventory. Needs
                      only tacticus-drop-rates.json.

Because recipes nest and components are shared across the 6 grid cells, an
exact total is far larger than "6 x one legendary drop" - composites are the
cost, and what you already own is subtracted first.

Two numbers are reported for --energy:
  Energy  the single next rank-up (1 -> 2 -> 3 inside a tier)
  TierE   every rung left before the tier itself switches (Stone -> Iron ->
          Bronze ...), priced against ONE inventory pool so a later rung sees
          what the earlier ones consumed. This is the "can this character
          change material category soonest" view; --sort tier ranks by it.

Plus one derived from them:
  Days    TierE / daily energy budget. The budget is regen (1 per 5 min =
          288/day) plus whatever extra energy is claimed daily - the ad, the
          free daily-deal crate, and the blackstone bundles, listed under
          energy.extra in tacticus-drop-rates.json. Default 638/day; tune it
          there.

The 11 machines of war are excluded: they have no rank grid at all (they
progress by ability levels instead - see research/README.md).

Usage:
    python3 rank_up_report.py
    python3 rank_up_report.py --top 15
    python3 rank_up_report.py --sort rank      # group by how advanced they are
    python3 rank_up_report.py --energy         # exact energy cost
    python3 rank_up_report.py --energy --sort energy   # cheapest next rank-up
    python3 rank_up_report.py --energy --sort tier     # cheapest tier crossing
    python3 rank_up_report.py --energy --estimate          # rarity shortcut
    python3 rank_up_report.py --energy --json              # detail per leaf
    python3 rank_up_report.py --json           # machine-readable
"""
import argparse
import json
import os
import pathlib
import signal
import sys
from collections import Counter

import energy_cost

BASE = pathlib.Path(__file__).resolve().parent
DEFAULT_DATA = BASE / "tacticus-player.json"
DEFAULT_RATES = BASE / "tacticus-drop-rates.json"

GRID_SLOTS = 6
TIERS = ["Stone", "Iron", "Bronze", "Silver", "Gold", "Diamond", "Adamantine"]
ROMAN = ["I", "II", "III"]
MAX_RANK = len(TIERS) * len(ROMAN) - 1  # 20 = Adamantine III

# Energy costs exist for five difficulties, but Extremis only appears on timed
# event campaigns, so it is excluded from --tier auto unless --include-events.
PERMANENT_TIERS = ("Normal", "Mirror", "Elite", "EliteMirror")
EVENT_TIERS = ("Extremis",)
ALL_TIERS = PERMANENT_TIERS + EVENT_TIERS

RARITIES = ("Common", "Uncommon", "Rare", "Epic", "Legendary")

# The 11 machines of war in the player file. They have no rank grid at all -
# PlayerMowRecord has no Rank property (research/tacticus-planner-api/.../
# PlayerData/Chunks/Units.cs) and the catalog carries them under `mows`, not
# `characters`, so they have no rankUpUpgrades. They still consume upgrade
# items, but through their 60 ability levels (mows[].primaryAbility/recipes),
# which this report does not price. Listed by id so grid mode can drop them
# without reading research/. Add new MOWs here when the catalog gains them.
MOW_IDS = frozenset({
    "astraOrdnanceBattery", "blackForgefiend", "darkaStormSpeeder",
    "deathCrawler", "necroReanimator", "ultraDreadnought", "orksRukkatrukk",
    "adeptExorcist", "tauBroadside", "thousDaemonPrince", "tyranBiovore",
})

# Fallback only - tacticus-drop-rates.json > rank_item_rarity is authoritative.
RANK_ITEM_RARITY = {
    "Stone": "Common",
    "Iron": "Common",
    "Bronze": "Uncommon",
    "Silver": "Rare",
    "Gold": "Epic",
    "Diamond": "Legendary",
    "Adamantine": "Legendary",
}


def rank_name(rank):
    if 0 <= rank // 3 < len(TIERS):
        return f"{TIERS[rank // 3]} {ROMAN[rank % 3]}"
    return f"rank {rank}"  # beyond the tiers we know about


def analyse(unit):
    filled = len(set(unit.get("upgrades") or []))
    filled = min(filled, GRID_SLOTS)
    rank = unit["rank"]
    return {
        "name": unit["name"],
        "faction": unit.get("faction", "?"),
        "rank": rank,
        "current": rank_name(rank),
        "next": rank_name(rank + 1) if rank < MAX_RANK else "MAX",
        "filled": filled,
        "missing": GRID_SLOTS - filled,
        "stars": unit.get("progressionIndex"),
        "level": unit.get("xpLevel"),
    }


# --- energy model ------------------------------------------------------------

def load_rates(path):
    if not path.exists():
        sys.exit(f"error: {path} not found - it holds the drop rates and costs")
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as error:
        sys.exit(f"error: {path} is not valid JSON: {error}")


def rate_tier(rates, tier):
    """EliteMirror reuses Elite's rates; everything else maps straight through."""
    return (rates.get("rate_aliases") or {}).get(tier, tier)


def expected_items(rates, tier, rarity):
    """Expected items from one attempt, before mercy is taken into account."""
    base = rates["tiers"][rate_tier(rates, tier)][rarity]
    if tier in ("Elite", "EliteMirror"):
        # Elite guarantees the first drop for C/U/R, then rolls an independent
        # overflow roll. Epic/Legendary have neither, so bonus stays 0.
        return base + ((rates.get("elite_overflow") or {}).get(rarity) or 0.0)
    return base


def energy_per_item(rates, tier, rarity):
    """Energy spent per item obtained at this tier, base rates only."""
    cost = rates["energy"]["attempt_cost"][tier]
    per_attempt = expected_items(rates, tier, rarity)
    return cost / per_attempt if per_attempt > 0 else float("inf")


def pick_tier(rates, rarity, wanted, include_events):
    """Cheapest tier for this rarity, or the one forced with --tier."""
    if wanted != "auto":
        return wanted, energy_per_item(rates, wanted, rarity)
    pool = ALL_TIERS if include_events else PERMANENT_TIERS
    best = min(pool, key=lambda t: (energy_per_item(rates, t, rarity), t))
    return best, energy_per_item(rates, best, rarity)


def daily_energy(rates):
    """(regen, extra claimed, total) energy available per day.

    `extra.sources` in tacticus-drop-rates.json lists the ad / blackstone
    bundles the player claims each day - energy the regen rate alone misses.
    """
    energy = rates.get("energy") or {}
    regen = (energy.get("regen") or {}).get("per_day") or 0
    extra = sum((s.get("energy") or 0) * (s.get("per_day") or 0)
                for s in ((energy.get("extra") or {}).get("sources") or []))
    return regen, extra, regen + extra


def price(rows, rates, args):
    """Attach rarity / tier / energy to every row in place."""
    ladder = {**(RANK_ITEM_RARITY), **(rates.get("rank_item_rarity") or {})}
    ladder = {k: v for k, v in ladder.items() if k != "note"}
    for row in rows:
        if row["next"] == "MAX":
            row.update(item_rarity=None, tier=None, energy=0.0,
                       energy_per_item=0.0, tier_energy=0.0)
            continue
        rarity = args.item_rarity or ladder.get(row["next"].split()[0], "Rare")
        tier, per_item = pick_tier(rates, rarity, args.tier, args.include_events)
        # Accumulated cost of the rest of the tier: the grid resets on every
        # promotion, so each remaining rung is a fresh 6 cells priced with that
        # rung's own inferred rarity (still the top-level item only).
        tier_total = row["missing"] * per_item
        for step in range(row["rank"] + 1,
                          energy_cost.tier_end(row["rank"], MAX_RANK)):
            step_rarity = (args.item_rarity
                           or ladder.get(rank_name(step).split()[0], "Rare"))
            _, step_per = pick_tier(rates, step_rarity, args.tier,
                                    args.include_events)
            tier_total += GRID_SLOTS * step_per
        row.update(
            item_rarity=rarity,
            tier=tier,
            energy_per_item=per_item,
            energy=row["missing"] * per_item,
            tier_energy=tier_total,
        )


# --- output ------------------------------------------------------------------

def print_table(rows, mode="grid"):
    """mode: 'grid' (count only), 'estimate' (rarity guess), 'exact' (catalog)."""
    if mode == "exact":
        headers = ("#", "Character", "Faction", "Current", "Next", "Grid",
                   "Energy", "TierE", "Days", "Note")
        aligns = ["<", "<", "<", "<", "<", "<", ">", ">", ">", "<"]
        table = [
            (str(i), r["name"], r["faction"], r["current"], r["next"],
             f"{r['filled']}/{GRID_SLOTS}",
             ("?" if (r.get("detail") or {}).get("blocked") else f"{r['energy']:.0f}"),
             ("?" if r.get("blocked") else f"{r['tier_energy']:.0f}"),
             ("?" if r.get("blocked") or r.get("days") is None
              else f"{r['days']:.1f}"),
             r.get("note") or "-")
            for i, r in enumerate(rows, 1)
        ]
    elif mode == "estimate":
        headers = ("#", "Character", "Faction", "Current", "Next", "Grid",
                   "Missing", "Rarity", "Tier", "E/item", "Energy")
        aligns = ["<", "<", "<", "<", "<", "<", ">", "<", "<", ">", ">"]
        table = [
            (str(i), r["name"], r["faction"], r["current"], r["next"],
             f"{r['filled']}/{GRID_SLOTS}", str(r["missing"]),
             r["item_rarity"] or "-", r["tier"] or "-",
             f"{r['energy_per_item']:.1f}", f"{r['energy']:.0f}")
            for i, r in enumerate(rows, 1)
        ]
    else:
        headers = ("#", "Character", "Faction", "Current", "Next", "Grid", "Missing")
        aligns = ["<", "<", "<", "<", "<", "<", ">"]  # numeric columns right-aligned
        table = [
            (str(i), r["name"], r["faction"], r["current"], r["next"],
             f"{r['filled']}/{GRID_SLOTS}", str(r["missing"]))
            for i, r in enumerate(rows, 1)
        ]
    widths = [max(len(h), *(len(row[c]) for row in table))
              for c, h in enumerate(headers)]
    fmt = "  ".join(f"{{:{a}{w}}}" for a, w in zip(aligns, widths))
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in table:
        print(fmt.format(*row))


def price_exact(row, unit, by_id, by_name, catalog, farms, owned):
    """Attach catalog-priced energy; never mutates the shared inventory.

    Sets both the next-step cost (`energy`) and the accumulated cost of every
    rung left inside the current tier (`tier_energy`), priced against ONE
    inventory pool so a later rung sees what the earlier ones consumed.
    """
    base = dict(energy=0.0, tier_energy=0.0, tier_steps=0, tier_target="MAX",
                tier_detail=[], blocked=False, next_blocked=False,
                note="", detail=None)

    if row["next"] == "MAX":
        row.update(base, note="max")
        return
    character = by_id.get(unit["id"]) or by_name.get(unit["name"])
    if character is None:
        row.update(base, blocked=True, next_blocked=True,
                   note="no catalog entry")
        return

    rank = unit["rank"]
    end = energy_cost.tier_end(rank, MAX_RANK)
    pool = Counter(owned)
    detail = []
    total, blocked, has_event = 0.0, False, False
    first, next_blocked = None, False
    for step in range(rank, end):
        items = energy_cost.cells(character, step,
                                  unit.get("upgrades") if step == rank else ())
        if not items:
            continue
        result = energy_cost.price(items, catalog, pool, farms)
        detail.append({"rank": step, **result})
        total += result["energy"]
        blocked = blocked or bool(result["blocked"])
        has_event = has_event or result["event_energy"] > 0
        if step == rank:
            first = result
            next_blocked = bool(result["blocked"])

    # `blocked` = some rung in this tier still has "Coming soon" cells, so the
    # TierE total is incomplete; `next_blocked` = the rung you are on now cannot
    # be priced at all. Haarken sits in rank 18, priceable, with the rank-19
    # grid unreleased - only the second flag should read as "unpriceable".
    note = ("blocked" if next_blocked else
            "tier pending" if blocked else
            "events" if has_event else
            "ready" if first is None and total == 0 else "")
    row.update(energy=(first or {}).get("energy", 0.0),
               tier_energy=total, tier_steps=len(detail),
               tier_target=rank_name(end), tier_detail=detail,
               blocked=blocked, next_blocked=next_blocked,
               note=note, detail=first)


def build_contention(all_rows, owned, catalog):
    """Next-rung bank withdrawals aggregated across every hero.

    Each row is priced against the FULL inventory independently (see
    price_exact), so when two heroes' next rungs both withdraw the same
    item only the first one promoted actually gets it from the bank -
    the rest must craft. Items whose combined claims exceed the stocked
    quantity are contested. Contested items drained by a FREE (0-energy)
    rung sort first: those are the promotions that quietly empty the bank.
    """
    claims, holders, free, tier = Counter(), {}, Counter(), Counter()
    for row in all_rows:
        first = row.get("detail")
        spent = (first or {}).get("spent_from_inventory") or {}
        for item, count in spent.items():
            claims[item] += count
            holders.setdefault(item, []).append(row["name"])
        if spent and (row.get("energy") or 0) <= 0 and not row.get("next_blocked"):
            for item in spent:
                free[item] += 1
        for step in row.get("tier_detail") or []:
            for item, count in (step.get("spent_from_inventory") or {}).items():
                tier[item] += count
    out = []
    for item, total in claims.items():
        bank = int(owned.get(item, 0) or 0)
        if total > bank:
            out.append({
                "id": item,
                "name": (catalog.get(item) or {}).get("material") or item,
                "bank": bank,
                "claims": total,
                "heroes": holders[item],
                "free_rungs": free[item],
                "tier_claims": tier[item],
            })
    out.sort(key=lambda c: (-c["free_rungs"], -(c["claims"] - c["bank"]),
                            -c["claims"], c["name"]))
    return out


def load_player(path=DEFAULT_DATA):
    """Read a player snapshot; returns (player, metaData) or exits."""
    if not path.exists():
        sys.exit(f"error: {path} not found - run update_player.py first")
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as error:
        sys.exit(f"error: {path} is not valid JSON: {error}")
    return doc.get("player") or doc, doc.get("metaData") or {}


def run(main):
    """Unix-filter entry point shared with the sibling tools.

    Die like a normal Unix filter when the reader quits (`... | head`):
    CPython otherwise swallows SIGPIPE and prints "Exception ignored ...
    BrokenPipeError" during its shutdown flush, exiting 120.
    """
    try:
        signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    except (AttributeError, ValueError, OSError):
        pass  # no SIGPIPE on this platform; the handler below still covers it
    try:
        main()
    except BrokenPipeError:
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(141)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("data", nargs="?", type=pathlib.Path, default=DEFAULT_DATA,
                        help=f"player JSON (default: {DEFAULT_DATA.name})")
    parser.add_argument("--top", type=int, metavar="N", help="only show the N closest")
    parser.add_argument("--sort", choices=("missing", "rank", "energy", "tier"),
                        default="missing",
                        help="missing: fewest items first (default). "
                             "rank: most advanced first. "
                             "energy: cheapest single rank-up first. "
                             "tier: cheapest to cross out of the current tier.")
    # Energy model -------------------------------------------------------
    parser.add_argument("--energy", action="store_true",
                        help="price the missing items in energy; uses the full "
                             "catalog in research/ (exact), or --estimate")
    parser.add_argument("--estimate", action="store_true",
                        help="use the rarity shortcut instead of the catalog: "
                             "one rate per rarity, no recipes, no inventory")
    parser.add_argument("--rates", type=pathlib.Path, default=DEFAULT_RATES,
                        help=f"drop rates / energy costs (default: {DEFAULT_RATES.name})")
    parser.add_argument("--tier", default="auto",
                        choices=("auto",) + ALL_TIERS,
                        help="farm tier: auto = cheapest for the rarity (default). "
                             "Extremis needs --include-events.")
    parser.add_argument("--include-events", action="store_true",
                        help="let --tier auto consider Extremis (timed event campaigns)")
    parser.add_argument("--item-rarity", choices=RARITIES,
                        help="force one item rarity for every row instead of "
                             "inferring it from the target rank")
    parser.add_argument("--json", action="store_true", help="emit JSON instead of a table")
    args = parser.parse_args()

    player, _ = load_player(args.data)

    all_units = player["units"]
    mows = [u for u in all_units if u["id"] in MOW_IDS]
    raw_units = [u for u in all_units if u["id"] not in MOW_IDS]
    rows = [analyse(u) for u in raw_units]

    rates = None
    mode = "grid"
    if args.energy or args.estimate or args.sort in ("energy", "tier"):
        rates = load_rates(args.rates)
        if args.estimate:
            mode = "estimate"
            if args.tier != "auto" and args.tier in EVENT_TIERS and not args.include_events:
                sys.exit(f"error: --tier {args.tier} is an event-campaign tier; "
                         f"pass --include-events as well")
            price(rows, rates, args)
        else:
            mode = "exact"
            try:
                by_id, by_name = energy_cost.load_units()
                catalog = energy_cost.load_upgrades()
                farms = energy_cost.load_farms()
                owned = energy_cost.load_inventory(player)
            except energy_cost.MissingSource as error:
                sys.exit(f"error: {error}\n"
                         f"       or pass --estimate to fall back to the "
                         f"rarity shortcut (needs only tacticus-drop-rates.json)")
            for row, unit in zip(rows, raw_units):
                price_exact(row, unit, by_id, by_name, catalog, farms, owned)

    # How long: TierE against the daily budget (regen + claimed extras).
    per_day, regen_day, extra_day = 0, 0, 0
    if rates:
        regen_day, extra_day, per_day = daily_energy(rates)
        if per_day:
            for row in rows:
                row["days"] = (row.get("tier_energy") or 0.0) / per_day

    # closest to the next rank first; ties -> the more advanced character wins
    if args.sort in ("energy", "tier"):
        # Only the rung being priced can sink a row; a priceable next rung with
        # a later unreleased rung still sorts on its own numbers.
        sink = "next_blocked" if args.sort == "energy" else "blocked"
        rows.sort(key=lambda r: (bool(r.get(sink)),
                                 r["energy"] if args.sort == "energy"
                                 else r["tier_energy"],
                                 r["missing"], -r["rank"], r["name"]))
    elif args.sort == "rank":
        rows.sort(key=lambda r: (-r["rank"], r["missing"], r["name"]))
    else:
        rows.sort(key=lambda r: (r["missing"], -r["rank"], r["name"]))
    all_rows, rows = rows, rows[: args.top] if args.top else rows

    contention = (build_contention(all_rows, owned, catalog)
                  if mode == "exact" else [])

    if args.json:
        print(json.dumps({"schema_version": 1, "rows": rows,
                          "contention": contention}, indent=2))
        return

    maxed = [r for r in all_rows if r["next"] == "MAX"]
    active = [r for r in all_rows if r["next"] != "MAX"]

    sort_label = {"missing": "fewest missing items",
                  "rank": "most advanced rank",
                  "energy": "cheapest next rank-up (energy)",
                  "tier": "cheapest to leave the tier (accumulated energy)"}[args.sort]
    print(f"Rank-up proximity - {len(raw_units)} characters")
    print(f"Grid: {GRID_SLOTS} slots per rank-up; 'Missing' = slots still empty.")
    if mows:
        print(f"Excluded {len(mows)} machines of war - they have no rank grid "
              f"(they level abilities instead; see research/README.md).")
    print(f"Sort: {sort_label} first.")
    if args.top and len(rows) < len(all_rows):
        print(f"Showing the {len(rows)} closest of {len(all_rows)} (summary covers all).")
    if mode == "exact":
        print(f"Energy: exact - {len(catalog)} catalog items, {farms['nodes']} campaign "
              f"nodes, mercy baked into effectiveRate, inventory deducted per "
              f"character; recipes expanded through to their components.")
        print("  Energy = one rank-up ahead.  TierE = every rung left before the "
              "tier itself changes.")
        if per_day:
            budget = (f"{per_day}/day = {regen_day} regen"
                      + (f" + {extra_day} from ads/blackstone" if extra_day else ""))
            print(f"  Days  = TierE divided by that budget ({budget}).")
    elif mode == "estimate":
        rarity_note = "" if args.item_rarity else " (inferred from target rank)"
        if args.tier == "auto":
            tier_note = ("tier=auto cheapest, incl. Extremis (events)"
                         if args.include_events else "tier=auto cheapest")
        else:
            tier_note = f"tier={args.tier} forced"
        print(f"Energy: ESTIMATE, base rates, {tier_note}, "
              f"rarity={args.item_rarity or 'from config'}{rarity_note}.")
    print()
    print_table(rows, mode)

    print(f"\nSummary (all {len(all_rows)} characters)")
    ready = [r for r in active if r["missing"] == 0]
    print(f"  at 6/6 - promotes immediately, so normally 0: {len(ready)}"
          + ("  -> " + ", ".join(r["name"] for r in ready) if ready else ""))
    buckets = {}
    for r in active:
        buckets.setdefault(r["missing"], []).append(r)
    for missing in sorted(buckets):
        names = buckets[missing]
        print(f"  {missing} missing: {len(names):3}  "
              + ", ".join(r["name"] for r in names[:6])
              + ("..." if len(names) > 6 else ""))
    if maxed:
        print(f"  at known max rank: {len(maxed)} -> "
              + ", ".join(r["name"] for r in maxed))

    budget_note = ""
    if rates and per_day:
        budget_note = (f"{per_day}/day ({regen_day} regen"
                       + (f" + {extra_day} claimed from ads/blackstone)"
                          if extra_day else " only)"))

    if mode == "exact":
        priced = [r for r in active if r.get("detail")]
        unpriceable = [r for r in active if r.get("next_blocked")]
        # A later unreleased rung makes TierE incomplete but leaves the next
        # rung priceable - such rows stay in the promote total, never in the
        # tier total, and are named separately from the truly unpriceable.
        pending = [r for r in active
                   if r.get("blocked") and not r.get("next_blocked")]
        total = sum(r["energy"] for r in priced)
        event_total = sum(r["detail"]["event_energy"] for r in priced)
        print(f"\nEnergy (exact, catalog-priced, {len(active)} unpromoted characters):")
        print(f"  total to promote everyone: {total:,.0f}"
              + ("" if not unpriceable
                 else f"  [+{len(unpriceable)} unpriceable]"))
        tier_total = sum(r["tier_energy"] for r in active
                         if not r.get("blocked"))
        if tier_total:
            print(f"  total to leave their current tier: {tier_total:,.0f}"
                  f"  (every rung up to the tier switch, not just the next one)")
        if pending:
            print(f"  (of {len(active)}, {len(pending)} have a later rung not yet"
                  f" released, so their tier total is excluded here)")
        if event_total:
            print(f"  of it, only obtainable in timed events: {event_total:,.0f}")
        cheapest = min((r for r in priced
                        if r["missing"] and not r.get("next_blocked")),
                       key=lambda r: r["energy"], default=None)
        if cheapest:
            print(f"  cheapest single rank-up: {cheapest['name']} "
                  f"({cheapest['current']} -> {cheapest['next']}), "
                  f"{cheapest['energy']:.0f} energy across "
                  f"{len(cheapest['detail']['leaves'])} leaf item(s)")
        climb = min((r for r in active
                     if r.get("tier_steps") and not r.get("blocked")),
                    key=lambda r: r["tier_energy"], default=None)
        if climb:
            print(f"  cheapest full tier climb: {climb['name']} "
                  f"({climb['current']} -> {climb['tier_target']}), "
                  f"{climb['tier_energy']:.0f} energy over "
                  f"{climb['tier_steps']} rung(s)")
        if per_day:
            print(f"  at {budget_note}: {total / per_day:.1f} days for one rung each"
                  + (f"; {tier_total / per_day:.1f} days to clear a tier each"
                     if tier_total else ""))
        if unpriceable:
            print("  blocked - no drop and no recipe yet: "
                  + ", ".join(r["name"] for r in unpriceable[:6])
                  + ("..." if len(unpriceable) > 6 else ""))
        if pending:
            print("  next rung priceable, later rung in this tier unreleased: "
                  + ", ".join(r["name"] for r in pending[:6])
                  + ("..." if len(pending) > 6 else ""))
        print("  every character is priced against the full inventory separately, so")
        print("  promoting several at once costs more than this total.")
        if contention:
            print(f"  bank contention - {len(contention)} item(s) whose next rungs "
                  "claim more than the bank holds:")
            for c in contention[:8]:
                names = ", ".join(c["heroes"][:4])
                extra = len(c["heroes"]) - 4
                if extra > 0:
                    names += f", +{extra} more"
                trap = (f" | {c['free_rungs']} FREE rung(s) drain it"
                        if c.get("free_rungs") else "")
                print(f"    {c['name']:<26} bank {c['bank']:>3} / claims "
                      f"{c['claims']:>3}  ({names}){trap}")
            if len(contention) > 8:
                print(f"    ... {len(contention) - 8} more - the full list is in "
                      "--json 'contention'")
            print("    the first hero promoted takes the banked units; "
                  "the rest must craft them.")

    elif mode == "estimate":
        total = sum(r["energy"] for r in active)
        tier_total = sum(r.get("tier_energy") or 0.0 for r in active)
        print(f"\nEnergy (ESTIMATE - base rates, {len(active)} unpromoted characters):")
        print(f"  total to promote everyone: {total:,.0f}")
        if tier_total:
            print(f"  total to leave their current tier: {tier_total:,.0f}")
        cheapest = min((r for r in active if r["missing"]),
                       key=lambda r: r["energy"], default=None)
        if cheapest:
            print(f"  cheapest single rank-up: {cheapest['name']} "
                  f"({cheapest['current']} -> {cheapest['next']}), "
                  f"{cheapest['energy']:.0f} energy "
                  f"({cheapest['missing']} x {cheapest['item_rarity']} "
                  f"on {cheapest['tier']})")
        if per_day:
            print(f"  at {budget_note}: {total / per_day:.1f} days for one rung each"
                  + (f"; {tier_total / per_day:.1f} days to clear a tier each"
                     if tier_total else ""))
        print("  this prices the TOP-LEVEL item only and ignores components you "
              "already own -")
        print("  composites cost far more than this. Drop --estimate for the real "
              "number.")


if __name__ == "__main__":
    run(main)
