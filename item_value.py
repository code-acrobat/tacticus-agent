#!/usr/bin/env python3
"""Per-item value lookup: farm energy, fair BS floor, shop-deal verdict.

Static view, one row per catalog item (557) - pipe it to fzf:

    python3 item_value.py                 # the whole catalog
    python3 item_value.py | fzf           # drill down
    python3 item_value.py | grep -i blood # plain grep works too

Each row carries SHOP (the typical Daily Deals single price for that rarity,
Common = the 5 BS chest) and VERDICT - that price against the floor, graded on
the same 6-step scale the detail view uses.

Detail for ONE item (matches id, label, or part of either; "_"/"-" count as
spaces, so `grand_strategy` == `Grand Strategy`):

    python3 item_value.py grand_strategy --price 540
    python3 item_value.py upgHpL017C --price 540 --qty 2

Numbers
  ENERGY   NET: recipes expanded to leaves with your inventory deducted at
           every recipe level (mercy baked into rates) - what you must farm.
  FAIR_BS  floor price = FULL-CRAFT energy (inventory ignored) x the cheapest
           blackstone->energy rate in tacticus-drop-rates.json (25 BS -> 60 E
           = 0.417 BS/E) - the item's value, judged against the total cost,
           never against what your stock covers. The difference (your delta)
           is printed next to it in the detail view, and as total_energy /
           delta_energy in --json.
  DAYS     NET energy / the daily energy budget in the same file (638/day).
  Verdict  --price / FAIR_BS = a 6-step scale - STRONG BUY .. STRONG AVOID.
           The lower half is PROFILE-RELATIVE (top_steps): frugal buys almost
           nothing (0.5/1/1.5 - sub-floor bargains only), moderate matches the
           calibration data (0.94x buy, 1.88x fair, 2.5-3.33x ok), generous
           gets strong-buy signals up to 2x floor (then buy<=3, fair<=4).
           The upper half comes from the ceiling: OK<=mid | OVERPRICED<=ceiling
           | STRONG AVOID>ceiling, with mid = 2+(ceiling-2)/2. Computed on the
           full craft. The FLOOR stays 0.417 BS/E - only the ceiling is
           personal: energy.spender.profile in tacticus-drop-rates.json
           (frugal 2.5x / moderate 5x / generous 8x) or --spender / --threshold.
           These are SIGNALS, not mandatory buys: situational value (e.g. the
           item unlocks a rank-up today that energy cannot reach in time) is
           NOT factored in. The lenient lower half is deliberate: BS spent on
           items is roster growth (compare 300 BS pulls for shards), so a
           5 BS common at 2.5x stays OK on moderate.

Gold is not modelled. Attempt caps and the event rotation are ignored, so
DAYS is an optimistic floor - same caveat as rank_up_report's Days column.
Rows are priced independently against a fresh inventory (like the report).
"""
import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))
import energy_cost  # noqa: E402
from rank_up_report import run  # noqa: E402

RANK = {"Common": 0, "Uncommon": 1, "Rare": 2, "Epic": 3, "Legendary": 4, "Mythic": 5}
RATES_PATH = BASE / "tacticus-drop-rates.json"
PLAYER_PATH = BASE / "tacticus-player.json"
SHOP_PATH = BASE / "tacticus-shop-prices.json"


def norm(text):
    return re.sub(r"[\s_\-]+", " ", (text or "")).strip().lower()


def load_rates():
    if not RATES_PATH.is_file():
        sys.exit(f"error: {RATES_PATH} not found - holds the drop rates and the energy budget")
    with open(RATES_PATH) as fh:
        return json.load(fh)


def load_shop():
    """{rarity: typical chosen-single BS} for the static SHOP/VERDICT columns.

    Empty dict if tacticus-shop-prices.json is missing - the columns then
    show '?', nothing crashes.
    """
    if not SHOP_PATH.is_file():
        return {}
    with open(SHOP_PATH) as fh:
        ladder = json.load(fh).get("typical_single_bs") or {}
    return {k: v for k, v in ladder.items() if isinstance(v, (int, float))}


def economics(rates):
    """(daily energy budget, cheapest BS->energy rate, that source dict)."""
    energy = rates.get("energy") or {}
    regen = (energy.get("regen") or {}).get("per_day") or 0
    sources = (energy.get("extra") or {}).get("sources") or []
    extra = sum((s.get("energy") or 0) * (s.get("per_day") or 0) for s in sources)
    paid = [s for s in sources if (s.get("bs_cost") or 0) > 0 and (s.get("energy") or 0) > 0]
    best = min(paid, key=lambda s: s["bs_cost"] / s["energy"]) if paid else None
    return regen + extra, (best["bs_cost"] / best["energy"] if best else None), best


def load_owned():
    """Inventory counts. The snapshot is {player: {...}, metaData: {...}} and
    energy_cost.load_inventory wants the player chunk - see rank_up_report."""
    if not PLAYER_PATH.is_file():
        return energy_cost.load_inventory({}), "no tacticus-player.json - inventory ignored"
    with open(PLAYER_PATH) as fh:
        doc = json.load(fh)
    return energy_cost.load_inventory(doc.get("player") or doc), None


def row_for(iid, item, catalog, farms, owned0, budget, bs_e, qty=1):
    n = max(1, qty)
    res = energy_cost.price([iid] * n, catalog, owned0.copy(), farms)
    # Full craft priced against an EMPTY inventory: the item's total value.
    # The fair floor is judged on this (shop price vs total cost), never on
    # the net figure - but the delta (what your stock covers) rides along.
    full = energy_cost.price([iid] * n, catalog, Counter(), farms)
    energy, blocked, event = res["energy"], bool(res["blocked"]), res["event_energy"]
    full_ok = not (full["blocked"] and not full["energy"])
    total = full["energy"] if full_ok else energy
    delta = (total - energy) if full_ok else 0.0
    if blocked and not energy:
        note = "no-drop"
    elif blocked:
        note = "part-no-drop"
    elif event and event < energy:
        note = "part-event"
    elif event:
        note = "event"
    elif energy == 0:
        note = "have"
    else:
        note = ""
    return {
        "rarity": item.get("rarity") or "?",
        "stat": item.get("stat") or "?",
        "kind": "craft" if item.get("craftable") else "farm",
        "own": owned0.get(iid, 0),
        "energy": None if (blocked and not energy) else round(energy, 1),
        "total_energy": round(total, 1),
        "delta_energy": round(delta, 1),
        "event_energy": round(event, 1),
        "fair_bs": (None if (blocked and not energy) or not bs_e
                    else round(total * bs_e)),
        "fair_basis": "full" if full_ok else "net",
        "days": (None if (blocked and not energy) or not budget
                 else round(energy / budget, 1)),
        "note": note,
        "id": iid,
        "label": item.get("label") or item.get("material") or iid,
        "blocked": {k: v for k, v in (res["blocked"] or {}).items()},
        "leaves": res["leaves"],
        "crafted": res["crafted"],
        "spent_from_inventory": res["spent_from_inventory"],
    }


def static(catalog, farms, owned0, budget, bs_e, shop, ceiling, profile, want_json):
    rows = [row_for(iid, it, catalog, farms, owned0, budget, bs_e)
            for iid, it in catalog.items()]
    for r in rows:
        s = shop.get(r["rarity"])
        r["shop_bs"] = s
        r["shop_ratio"] = (round(s / r["fair_bs"], 2)
                           if s and r["fair_bs"] else None)
        r["shop_grade"] = (grade_tag(s / r["fair_bs"], ceiling, profile)
                           if s and r["fair_bs"] else None)
    rows.sort(key=lambda r: (RANK.get(r["rarity"], 9), r["stat"], r["id"]))
    if want_json:
        json.dump({"schema_version": 1, "rows": rows}, sys.stdout, indent=1)
        print()
        return
    print(f"# ENERGY/DAYS = net farm, your stock deducted | FAIR_BS = full-craft"
          f" energy x {bs_e:.3f} BS/E, inventory ignored (the delta is the OWN"
          f" gap - see note 'have') | SHOP = typical Daily Deals single BS for"
          f" the rarity (Common = 5 BS chest; exact offers via --price),"
          f" VERDICT = SHOP / FAIR_BS on the {profile} scale (ceiling"
          f" {ceiling:g}x) | drill down:"
          f" python3 item_value.py <name-or-id> [--price BS]")
    print(f"{'RARITY':<10} {'STAT':<9} {'KIND':<5} {'OWN':>4} {'ENERGY':>9} "
          f"{'FAIR_BS':>8} {'SHOP':>6} {'VERDICT':<13} {'DAYS':>6} {'NOTE':<12} "
          f"{'NAME':<30} ID")
    for r in rows:
        e = f"{r['energy']:>9.1f}" if r["energy"] is not None else f"{'?':>9}"
        f = f"{r['fair_bs']:>8}" if r["fair_bs"] is not None else f"{'?':>8}"
        s = f"{r['shop_bs']:>6}" if r["shop_bs"] is not None else f"{'?':>6}"
        v = f"{(r['shop_grade'] or '?'):<13}"
        d = f"{r['days']:>6.1f}" if r["days"] is not None else f"{'?':>6}"
        print(f"{r['rarity']:<10} {r['stat']:<9} {r['kind']:<5} {r['own']:>4} {e} {f} "
              f"{s} {v} {d} {r['note']:<12} {r['label'][:30]:<30} {r['id']}")


def resolve(query, catalog, rarity, stat):
    q = norm(" ".join(query))
    pool = [i for i, it in catalog.items()
            if (not rarity or (it.get("rarity") or "") == rarity)
            and (not stat or norm(it.get("stat")) == norm(stat))]
    for key in (lambda i: norm(i), lambda i: norm(catalog[i].get("label"))):
        hit = [i for i in pool if key(i) == q]
        if hit:
            return hit
    return [i for i in pool
            if q in norm(i) or q in norm(catalog[i].get("label"))
            or q in norm(catalog[i].get("material"))]


def component_lines(catalog, iid, count, owned):
    """Flat list of the DIRECT recipe entries - name and how many, no recursion.
    Each component has its own row in the static view (`item_value.py | fzf`)
    if you want to value it separately."""
    lines = []
    for comp in (catalog.get(iid) or {}).get("recipe") or []:
        cid = comp.get("material")
        item = catalog.get(cid) or {}
        label = item.get("label") or cid
        rar = (item.get("rarity") or "?") + (", crafted" if item.get("craftable") else "")
        have = owned.get(cid, 0)
        lines.append(f"    {comp.get('count', 0) * count:>4} x {label:<26} {rar:<16}"
                     + (f"  [owned {have}]" if have else ""))
    return lines


def spender_profile(rates, args):
    """(profile name, ceiling ratio) - the top of the verdict scale: the max
    shop-price/floor ratio that still counts as a buy. The floor itself stays
    the cheapest paid refill; only this ceiling is personal."""
    cfg = ((rates.get("energy") or {}).get("spender")) or {}
    profiles = {"frugal": 2.5, "moderate": 5.0, "generous": 8.0,
                **(cfg.get("profiles") or {})}
    profile = (args.spender or cfg.get("profile") or "moderate").lower()
    if args.threshold:
        return profile, max(1.0, float(args.threshold))
    if profile not in profiles:
        sys.exit(f"error: unknown spender profile '{profile}' - use one of"
                 f" {', '.join(sorted(profiles))} or --threshold RATIO")
    return profile, float(profiles[profile])


def top_steps(profile):
    """(STRONG BUY, BUY, FAIR) upper bounds - the lower half of the scale is
    profile-relative so each spender sees their own signals: frugal buys
    almost nothing (sub-floor bargains only), moderate matches the
    calibration data, generous gets strong-buy signals up to 2x floor.
    Signals, not mandatory buys."""
    return {"frugal": (0.5, 1.0, 1.5),
            "moderate": (0.5, 1.1, 2.0),
            "generous": (2.0, 3.0, 4.0)}.get(profile, (0.5, 1.1, 2.0))


def grade(ratio, ceiling, profile="moderate"):
    """6-step recommendation: strong buy .. strong not buy."""
    mid = 2 + (ceiling - 2) / 2 if ceiling > 2 else 2.0
    sb, buy, fair = top_steps(profile)
    fair, buy, sb = min(fair, mid), min(buy, fair), min(sb, buy)
    if ratio <= sb:
        return "STRONG BUY"
    if ratio <= buy:
        return "BUY"
    if ratio <= fair:
        return "FAIR"
    if ratio <= mid:
        return "OK - buy if you need it"
    if ratio <= ceiling:
        return "OVERPRICED - only if you need it now"
    return "STRONG AVOID"


def scale_line(ceiling, profile="moderate"):
    mid = 2 + (ceiling - 2) / 2 if ceiling > 2 else 2.0
    sb, buy, fair = top_steps(profile)
    fair, buy, sb = min(fair, mid), min(buy, fair), min(sb, buy)
    return (f"STRONG BUY<={sb:g}x | BUY<={buy:g}x | FAIR<={fair:g}x"
            f" | OK<={mid:g}x | OVERPRICED<={ceiling:g}x | STRONG AVOID>{ceiling:g}x")


def grade_tag(ratio, ceiling, profile):
    """Short verdict for the static table's VERDICT column."""
    return {"STRONG BUY": "strong buy", "BUY": "buy", "FAIR": "fair",
            "OK - buy if you need it": "ok",
            "OVERPRICED - only if you need it now": "overpriced",
            "STRONG AVOID": "strong avoid"}.get(grade(ratio, ceiling, profile), "?")


def verdict(price_bs, floor_bs, energy, total_energy, budget, bs_e, qty, ceiling, profile):
    print(f"\n  shop offer: {price_bs:g} BS for {qty}")
    if floor_bs is None:
        print("    cannot value - this item has no drop and no recipe (blocked)")
        return
    bought_e = price_bs / bs_e if bs_e else float("nan")
    print(f"    fair floor  = {total_energy:.1f} E x {bs_e:.3f} BS/E"
          f" = {floor_bs:.0f} BS  (full craft, inventory ignored)")
    if total_energy - energy >= 0.05:
        net_floor = round(energy * bs_e) if bs_e else 0
        print(f"    delta       = stock covers {total_energy - energy:.1f} E"
              f" ({(total_energy - energy) * bs_e:.0f} BS) -> net"
              f" {energy:.1f} E = {net_floor} BS"
              + (f" ({price_bs / net_floor:.2f}x)" if net_floor else ""))
    print(f"    scale       {scale_line(ceiling, profile)}")
    print(f"                ({profile} spender profile - energy.spender in"
          f" tacticus-drop-rates.json; override with --spender/--threshold)")
    if energy == 0:
        print("    you already own it (inventory covers it) - farming costs 0,")
        print("    so ANY price for it is a bad deal")
        return
    ratio = price_bs / floor_bs
    mark = grade(ratio, ceiling, profile)
    under = (1 - ratio) * 100
    where = (f"{abs(under):.0f}% under the floor" if under >= 0
             else f"{-under:.0f}% over the floor")
    print(f"    you pay     = {price_bs:g} BS = {ratio:.2f}x floor  ->  {mark}")
    print(f"    {where}")
    print(f"    energy view = {price_bs:g} BS buys {bought_e:.0f} E"
          f" ({bought_e / budget:.1f} days) vs {total_energy:.1f} E"
          f" ({total_energy / budget:.1f} days) to farm")
    print(f"    time saved  = {total_energy / budget:.1f} days full /"
          f" {energy / budget:.1f} net at {budget:.0f}/day"
          f" (attempt caps/event rotation may stretch both)")


def detail(query, catalog, farms, owned0, budget, bs_e, profile, ceiling, args):
    hits = resolve(query, catalog, args.rarity, args.stat)
    if not hits:
        sys.exit(f"no item matches '{' '.join(query)}' - list everything with:"
                 " python3 item_value.py")
    if len(hits) > 1:
        print(f"{len(hits)} matches for '{' '.join(query)}' - drill down first"
              " (python3 item_value.py | fzf), then rerun with the id:\n")
        for iid in hits[:25]:
            it = catalog[iid]
            print(f"  {(it.get('label') or iid):<26} {it.get('rarity') or '?':<10} "
                  f"{it.get('stat') or '?':<9} {iid}")
        if len(hits) > 25:
            print(f"  ... {len(hits) - 25} more")
        sys.exit(1)
    iid = hits[0]
    item = catalog[iid]
    qty = max(1, args.qty)
    res = row_for(iid, item, catalog, farms, owned0, budget, bs_e, qty)
    energy = res["energy"] if res["energy"] is not None else 0.0
    floor = res["fair_bs"]

    if args.json:
        res["price_bs"] = args.price
        res["qty"] = qty
        res["fair_bs"] = floor
        res["daily_budget"] = budget
        res["bs_per_energy"] = bs_e
        res["spender_profile"] = profile
        res["spender_ceiling"] = ceiling
        if args.price is not None and floor:
            res["ratio"] = round(args.price / floor, 2)
            res["grade"] = grade(args.price / floor, ceiling, profile)
        json.dump({"schema_version": 1, **res}, sys.stdout, indent=1)
        print()
        return

    kind = "crafted (composite)" if item.get("craftable") else "farmable leaf"

    def name(cid):
        return (catalog.get(cid) or {}).get("label") or cid

    print(f"{res['label']}  [{res['rarity']} / {res['stat']} / {kind}]  (id {iid})")
    if item.get("craftable"):
        print("  components (crafted = has its own recipe, [owned N] = in inventory;"
              " each is its own row in `python3 item_value.py | fzf`):")
        for line in component_lines(catalog, iid, qty, owned0):
            print(line)
    print(f"  you own {owned0.get(iid, 0)}   "
          f"(every lookup deducts your stock before farming)")

    print(f"\n  farm {qty}x (inventory deducted):")
    if not res["leaves"]:
        if res["blocked"]:
            print("    BLOCKED - no drop and no recipe "
                  f"(their label is a placeholder): {', '.join(sorted(res['blocked']))}")
        else:
            print("    nothing to farm - inventory covers it")
    else:
        print(f"    {'NET':>5} {'HAVE':>5} {'PER-E':>8} {'NODE':<14} {'KIND':<11} "
              f"{'ENERGY':>9}  NAME")
        for lid, leaf in sorted(res["leaves"].items(),
                                key=lambda kv: -kv[1]["need"] * kv[1]["per_item"]):
            e = leaf["need"] * leaf["per_item"]
            print(f"    {leaf['need']:>5} {owned0.get(lid, 0):>5} "
                  f"{leaf['per_item']:>8.2f} {leaf['node']:<14} "
                  f"{('event ' if leaf['event'] else '') + leaf['how']:<11} "
                  f"{e:>9.1f}  {name(lid)[:30]}")
        print(f"    {len(res['leaves'])} leaves | "
              f"total {energy:.1f} energy"
              + (f" (of which {res['event_energy']:.1f} event-only)"
                 if res["event_energy"] else "")
              + (f" | would craft: "
                 + ", ".join(f"{n}x {name(cid)}" for cid, n in res["crafted"].items())
                 if res["crafted"] else "")
              + (" | blocked: " + ", ".join(res["blocked"])
                 if res["blocked"] else ""))
    if res["spent_from_inventory"]:
        took = ", ".join(f"{n}x {name(cid)}"
                         for cid, n in sorted(res["spent_from_inventory"].items()))
        print(f"    taken from inventory: {took}")

    total = res["total_energy"]
    delta = res["delta_energy"]
    basis = ("full craft, inventory ignored" if res.get("fair_basis") == "full"
             else "full craft blocked - net basis")
    print(f"\n  fair floor: {total:.1f} E x {bs_e:.3f} BS/E = "
          f"{floor if floor is not None else '?'} BS   ({basis})"
          "   - cheapest refill in tacticus-drop-rates.json")
    if floor is not None and delta >= 0.05:
        print(f"  delta:      your stock covers {delta:.1f} E ({delta * bs_e:.0f} BS)"
              f" -> net farm {energy:.1f} E = {round(energy * bs_e)} BS")
    if delta >= 0.05:
        print(f"  time: {total / budget:.1f} days full-craft /"
              f" {energy / budget:.1f} net of stock at the {budget:.0f}/day budget")
    else:
        print(f"  time: {energy:.1f} / {budget:.0f} = {energy / budget:.1f} days"
              " at the full daily budget")
    if args.price is not None:
        verdict(args.price, floor, energy, total, budget, bs_e, qty, ceiling, profile)
    else:
        print("  add --price <BS> to compare against a shop offer")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="*",
                    help="item NAME or id (partial ok; names are what the output "
                         "prints). Omit for the full static list")
    ap.add_argument("--price", type=float, metavar="BS",
                    help="what the shop charges in total for --qty items")
    ap.add_argument("--qty", type=int, default=1, metavar="N", help="quantity (default 1)")
    ap.add_argument("--spender", metavar="PROFILE",
                    help="spender profile from tacticus-drop-rates.json "
                         "energy.spender.profiles (frugal/moderate/generous)")
    ap.add_argument("--threshold", type=float, metavar="RATIO",
                    help="ceiling ratio for the buy scale; overrides --spender")
    ap.add_argument("--rarity", choices=sorted(RANK), help="disambiguate")
    ap.add_argument("--stat", help="disambiguate (Health, Damage, Armor, ...)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    if args.price is not None and not args.query:
        ap.error("--price needs an item query (or none at all for the full list)")

    if not energy_cost.available():
        sys.exit("error: research/ catalog missing - see research/README.md")
    rates = load_rates()
    budget, bs_e, best = economics(rates)
    profile, ceiling = spender_profile(rates, args)
    if not bs_e:
        sys.exit("error: no paid energy bundle in tacticus-drop-rates.json energy.extra")
    catalog = energy_cost.load_upgrades()
    farms = energy_cost.load_farms()
    owned0, warn = load_owned()

    if not args.query:
        static(catalog, farms, owned0, budget, bs_e, load_shop(), ceiling, profile,
               args.json)
    else:
        if warn:
            print(f"warning: {warn}", file=sys.stderr)
        detail(args.query, catalog, farms, owned0, budget, bs_e, profile, ceiling, args)


if __name__ == "__main__":
    run(main)
