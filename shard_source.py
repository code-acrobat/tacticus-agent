#!/usr/bin/env python3
"""Shard sources: where to get shards (or a full unlock) for a character.

Answers "where do I get shards for X?" from the raw game config
(research/datamine/gameconfig142.json): campaign nodes that drop them, shop
offers (incl. rotating event shops), requisition / summoning-portal odds,
post-unlock hero quests, the webstore bundle and past release banners.

There is no unlock-shard threshold anywhere in config, so this lists SOURCES
only - it never claims how many shards an unlock costs.

One concern per tool: acquisition lookup (like item_value) - deliberately NOT
a next_step witness (invariant 7): it is a per-character lookup, not a roster
gate.
"""

import argparse
import json
import sys
from pathlib import Path

from rank_up_report import load_player, rank_name, run

BASE = Path(__file__).resolve().parent
GAMECONFIG = BASE / "research" / "datamine" / "gameconfig142.json"
CC = "clientGameConfig"
# tacticus-shop-prices.json -> requisition.pull_1 (line 100); pulled from the
# portal chain itself, only the price is quoted from that file.
PULL_PRICE = "300 BS or 1 order"


def load_config():
    if not GAMECONFIG.exists():
        sys.exit(f"error: {GAMECONFIG} missing - this tool needs research/ "
                 f"(see AGENTS.md \"External data sync\")")
    try:
        return json.loads(GAMECONFIG.read_text())[CC]
    except json.JSONDecodeError as error:
        sys.exit(f"error: {GAMECONFIG} is not valid JSON: {error}")
    except KeyError:
        sys.exit(f"error: config layout changed - no '{CC}' in {GAMECONFIG}; "
                 f"update shard_source.py")


def resolve_unit(lineup, query):
    """(id, unit) for a partial name/id; exits on none or ambiguity."""
    q = query.lower()
    for uid, unit in lineup.items():                       # exact id / name
        if uid.lower() == q or (unit.get("name") or "").lower() == q:
            return uid, unit
    hits = [(uid, unit) for uid, unit in lineup.items()
            if q in uid.lower() or q in (unit.get("name") or "").lower()]
    if not hits:
        sys.exit(f"error: no character matches {query!r}")
    if len(hits) > 1:
        names = ", ".join((u.get("name") or i) for i, u in hits[:8])
        sys.exit(f"error: ambiguous query {query!r} - matches: {names}")
    return hits[0]


def _heads(reward):
    """Reward strings -> pool heads: 'shards_x:15' / 'shards_x%2/6' -> 'shards_x'."""
    items = reward if isinstance(reward, list) else [reward]
    return [str(v).split(":")[0].split("%")[0] for v in items if v]


def _cron(cron):
    """Quartz cron -> human rotation ('daily', 'weekly SUN', 'MON/THU')."""
    fields = (cron or "").split()
    if len(fields) < 6:
        return ""
    if fields[5] == "*":
        return "daily"
    return "/".join(fields[5].split(","))


def progress_index(player):
    """(type, campaign id) -> (display name, {battleIndex: attemptsLeft})."""
    index = {}
    for campaign in (player.get("progress") or {}).get("campaigns") or []:
        battles = {b.get("battleIndex"): b.get("attemptsLeft")
                   for b in campaign.get("battles") or []}
        index[(campaign.get("type"), campaign.get("id"))] = \
            (campaign.get("name"), battles)
    return index


def campaign_rows(cc, uid, progress):
    want = {f"shards_{uid}", f"hero_{uid}"}
    rows = []
    campaigns = (cc.get("battles") or {}).get("campaigns") or {}
    for difficulty, groups in campaigns.items():
        for group in groups or []:
            name, attempts = progress.get(
                (difficulty, group.get("id")), (group.get("id"), {}))
            for i, battle in enumerate(group.get("battles") or []):
                loot = battle.get("loot") or {}
                fields = [key for key in
                          ("base", "star1", "star2", "star3",
                           "chanceOf", "rewardEvenIfLost")
                          if any(h in want for h in _heads(loot.get(key)))]
                if not fields:
                    continue
                rows.append({
                    "difficulty": difficulty, "campaign": name,
                    "battle": battle.get("battleId"),
                    "board": battle.get("BoardId"),
                    "loot": fields,
                    "energy": battle.get("staminaCost"),
                    "max_attempts": battle.get("maxAttempts"),
                    "attempts_left": attempts.get(i),
                })
    return rows


def _flat(products):
    for item in products or []:
        if isinstance(item, list):
            yield from _flat(item)
        elif isinstance(item, dict):
            yield item


def shop_rows(cc, uid):
    want = {f"shards_{uid}", f"hero_{uid}"}
    rows = []
    merchants = (cc.get("shop") or {}).get("merchants") or {}
    for name, merchant in merchants.items():
        for product in _flat((merchant or {}).get("products")):
            heads = _heads(product.get("reward"))
            if not any(h in want for h in heads):
                continue
            head = next(h for h in heads if h in want)
            qty = str(product.get("reward") if isinstance(product.get("reward"),
                      str) else "").partition(":")[2]
            cost = product.get("cost") or {}
            conditions = product.get("conditions") or {}
            rows.append({
                "shop": name, "reward": head,
                "qty": int(qty) if qty.isdigit() else 1,
                "cost": cost.get("amount"), "currency": cost.get("type"),
                "rotation": _cron(product.get("cronSchedule")),
                "max_purchases": product.get("maxPurchases"),
                "lock": conditions.get("lockId"),
                "event": "EventShop" in name,
            })
    return rows


def requisition(cc, uid):
    """Two masses from summoningPortal1: p (probability) for full-unit rows,
    e (expected rolls, x amountMin) for shard rows."""
    tables = (cc.get("loot") or {}).get("dropTables") or {}
    want_s, want_h = f"shards_{uid}", f"hero_{uid}"
    mass_p, pool_rows = {}, {}
    totals = {"e_shards_per_pull": 0.0, "p_full_unit": 0.0}

    def walk(table, p, e, seen):
        if table in seen or table not in tables:
            return
        mass_p[table] = mass_p.get(table, 0) + p
        rows = tables[table].get("rewards") or []
        total = sum(r.get("weight", 1) for r in rows) or 1
        for row in rows:
            share = row.get("weight", 1) / total
            amount = row.get("amountMin") or 1
            if row.get("type") == "dropTable":
                walk(row.get("reward"), p * share, e * share * amount,
                     seen | {table})
                continue
            head = str(row.get("reward") or "").split(":")[0]
            if head == want_s:
                totals["e_shards_per_pull"] += e * share * amount
                pool_rows[(table, "shards")] = {"table": table, "kind": "shards",
                                                "weight": row.get("weight", 1),
                                                "total": total}
            elif head == want_h:
                totals["p_full_unit"] += p * share * amount
                pool_rows[(table, "hero")] = {"table": table, "kind": "hero",
                                              "weight": row.get("weight", 1),
                                              "total": total}

    walk("summoningPortal1", 1.0, 1.0, frozenset())
    pools = [dict(entry, mass=mass_p.get(entry["table"], 0.0))
             for entry in pool_rows.values()]
    return {"price": PULL_PRICE, "pools": pools, **totals}


def quest_rows(cc, uid):
    """Hero chain - POST-unlock: every task needs the hero it belongs to.
    The mythic_* chain pays mythicShards (ascension), so it is filtered out."""
    groups = (cc.get("quests") or {}).get("groups") or {}
    rows = []
    for quest in (groups.get("hero") or {}).get("quests") or []:
        name = str(quest.get("name") or "")
        rewards = quest.get("rewards") or []
        if not name.startswith(f"hero_{uid}_"):
            continue
        if f"shards_{uid}" not in _heads(rewards):
            continue
        rows.append({"quest": name, "rewards": rewards,
                     "tasks": [t.get("name") for t in quest.get("tasks") or []]})
    return rows


def iap_rows(cc, uid):
    want = {f"shards_{uid}", f"hero_{uid}"}
    rows = []
    products = (cc.get("shop") or {}).get("realMoneyProducts") or {}
    for pid, product in products.items():
        if any(h in want for h in _heads((product or {}).get("rewards"))):
            rows.append({"product": pid, "price_cents": product.get("price"),
                         "rewards": product.get("rewards")})
    return rows


def banner_rows(cc, uid):
    want = {f"shards_{uid}", f"hero_{uid}"}
    rows = []
    tables = (cc.get("loot") or {}).get("dropTables") or {}
    for name, table in tables.items():
        if not name.startswith("sp_spec_banner"):
            continue
        if any(h in want for h in
               _heads([r.get("reward") for r in table.get("rewards") or []])):
            rows.append(name)
    return rows


def verdict_lines(campaign, shops):
    live = [s for s in shops if not s["event"]]
    event = [s for s in shops if s["event"]]
    lines = []
    if campaign:
        diffs = ", ".join(sorted({c["difficulty"] for c in campaign}))
        lines.append(f"farm: {len(campaign)} campaign node(s) ({diffs})")
    else:
        lines.append("no campaign node drops his shards")
    if live:
        lines.append("buy: " + "; ".join(
            f"{s['shop']} {s['qty']} for {s['cost']} {s['currency']}"
            for s in live))
    if event:
        lines.append(f"rotating event-shop offers: {len(event)} "
                     f"(dated - only while the event runs)")
    if not campaign and not shops:
        lines.append(f"worst case: requisition ({PULL_PRICE}/pull)")
    return lines


def render(unit, uid, roster, campaign, shops, req, quests, iap, banners, meta):
    print(f"=== Shard sources: {unit.get('name')} ({uid}) ===")
    if roster:
        print(f"{unit.get('FactionId')} | {unit.get('BaseRarity')} | in your "
              f"roster: rank {rank_name(roster.get('rank', 0))}, "
              f"{roster.get('shards', 0)} shards held "
              f"(snapshot {meta.get('lastUpdatedOn') or '?'})")
    else:
        print(f"{unit.get('FactionId')} | {unit.get('BaseRarity')} | not in "
              f"your roster (snapshot {meta.get('lastUpdatedOn') or '?'})")

    print("\nCAMPAIGN")
    if not campaign:
        print("  none - no campaign node drops his shards")
    for row in campaign:
        left = "?" if row["attempts_left"] is None else row["attempts_left"]
        energy = "?" if row["energy"] is None else f'{row["energy"]}E'
        att = (f'{row["max_attempts"]}/day' if row["max_attempts"] else "?")
        print(f'  {row["difficulty"]:<11} {row["campaign"]}  battle '
              f'{row["battle"]} ({row["board"]})  '
              f'{"+".join(row["loot"])}  {energy}  {att}  left {left}')

    print("\nSHOPS")
    if not shops:
        print("  none")
    for row in shops:
        qty = ("FULL UNLOCK" if row["reward"].startswith("hero_")
               else f'{row["qty"]} shards')
        flags = []
        if row["rotation"]:
            flags.append(row["rotation"])
        if row["max_purchases"]:
            flags.append(f'max {row["max_purchases"]}')
        if row["event"]:
            flags.append("event, rotating")
        print(f'  {row["shop"]:<10} {qty:<12} {row["cost"]} '
              f'{row["currency"]:<30} {" ".join(flags)}')
        if row["lock"]:
            print(f'      lock {row["lock"]}')

    print(f'\nREQUISITION (summoning portal, {req["price"]}/pull)')
    print(f'  expected {req["e_shards_per_pull"]:.3f} of his shards per pull; '
          f'full-unit odds {req["p_full_unit"] * 100:.3f}%'
          + (f' (~1 in {round(1 / req["p_full_unit"]):,})'
             if req["p_full_unit"] else ""))
    for pool in req["pools"]:
        print(f'  {pool["table"]}: mass {pool["mass"]:.3f}, '
              f'his weight {pool["weight"]}/{pool["total"]}')
    if not req["pools"]:
        print("  not in the portal pools")
    print("  caveat: base weights; mercy and duplicate conversion ignored")

    if quests:
        print("\nHERO QUESTS (post-unlock - every task needs the hero)")
        for quest in quests:
            print(f'  {quest["quest"]}: {", ".join(quest["rewards"] or [])}'
                  f'  [{", ".join(quest["tasks"] or [])}]')
    if iap:
        print("\nWEBSTORE")
        for offer in iap:
            print(f'  {offer["product"]}  {offer["price_cents"]} cents  '
                  f'{", ".join(offer["rewards"] or [])}')
    if banners:
        print("\nBANNERS (past/rotating release banners)")
        for name in banners:
            print(f"  {name}")

    print("\nVERDICT")
    for line in verdict_lines(campaign, shops):
        print(f"  {line}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="*",
                    help="character NAME or id (partial ok, _ = space)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    query = " ".join(args.query).replace("_", " ").strip()
    if not query:
        ap.error("a character query is required")

    cfg = load_config()
    player, meta = load_player()
    lineup = cfg.get("units", {}).get("lineup") or {}
    uid, unit = resolve_unit(lineup, query)

    roster = next((u for u in player.get("units") or []
                   if u.get("id") == uid), None)
    campaign = campaign_rows(cfg, uid, progress_index(player))
    shops = shop_rows(cfg, uid)
    req = requisition(cfg, uid)
    quests = quest_rows(cfg, uid)
    iap = iap_rows(cfg, uid)
    banners = banner_rows(cfg, uid)

    if args.json:
        json.dump({
            "schema_version": 1,
            "meta": {"snapshot": meta.get("lastUpdatedOn"),
                     "unit": {"id": uid, "name": unit.get("name"),
                              "faction": unit.get("FactionId"),
                              "rarity": unit.get("BaseRarity"),
                              "owned": roster is not None,
                              "rank": (roster or {}).get("rank"),
                              "shards": (roster or {}).get("shards")}},
            "campaign": campaign, "shops": shops, "requisition": req,
            "quests": quests, "iap": iap, "banners": banners,
            "verdict": verdict_lines(campaign, shops),
        }, sys.stdout, indent=1)
        print()
        return

    render(unit, uid, roster, campaign, shops, req, quests, iap, banners, meta)


if __name__ == "__main__":
    run(main)
