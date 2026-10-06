#!/usr/bin/env python3
"""Convene every witness into one brief: where am I blocked, what next.

Every tool in this repo is a *witness*: it answers exactly one question
(items, XP, badges, gear, power, team fieldability) and stops - one
concern per tool, so each stays small and honest. The missing piece was
assembly: Tacticus Planner answers "what next?" with a pile of pages;
this script answers it with one brief.

It runs the witness CLIs with --json, merges their rows per hero and
prints:

    1. EVENTS             live/upcoming event windows + the strategy
                           pointer (or an ad-hoc hook) for each
    2. WHERE I'M BLOCKED  per-gate counts + examples (XP / XP-maxed /
                           badges / items / gear) plus comps you cannot
                           field
    3. WHAT TO DO NEXT    in leverage order: shared-pool purchases ->
                           apply books (stock first; the plan names any
                           guild-shop buys it needs) -> farm the best
                           power-per-energy rungs -> gear buys -> unlocks

Purity (the same rule as tacticus_mcp.py): zero pricing logic here.
Energy comes from rank_up_report, power from power_delta, statuses from
the gates. The only arithmetic added is d_power / energy - the
power-per-resource ranking the roster has always deserved. It reads
nothing but the witnesses' stdout - no research/, no config JSON.

Gate semantics worth knowing:
  * badges block ABILITIES, not rank-ups - a badge-blocked hero stays in
    the farm list;
  * XP and unpriceable items DO block rank-ups - those leave the farm
    list until fixed;
  * XP-MAXED = terminal (rarity cap below the requirement): stop
    investing;
  * gear green and a missing team member are buys/unlocks, not farm
    blocks;
  * a FREE rung can still drain contested bank stock - when several next
    rungs claim the same item, the first promoted takes the banked
    units. rank_up_report reports that contention; the farm rows here
    only carry its ! mark.

Usage:
    python3 next_step.py                 # the brief
    python3 next_step.py --top 20        # longer farm list
    python3 next_step.py --json          # machine-readable assembly
"""
import argparse
import json
import pathlib
import subprocess
import sys
from datetime import datetime, timezone

from rank_up_report import rank_name, run

BASE = pathlib.Path(__file__).resolve().parent

# key -> (script, args): every witness, always run fresh (same contract
# tacticus_mcp.py uses - their --json stdout is the only data we trust)
WITNESSES = {
    "rank": ("rank_up_report.py", ["--energy", "--json"]),
    "xp": ("xp_gate.py", ["--json"]),
    "ab": ("ability_gate.py", ["--json"]),
    "pw": ("power_delta.py", ["--json"]),
    "gear": ("gear_report.py", ["--json"]),
    "team": ("team_roster.py", ["--json"]),
    "event": ("home_screen_event.py", ["--json"]),   # advisory: schedule awareness
}

# per-event advice when a dedicated report exists; anything else gets the
# ad-hoc hook (build one on demand - pre-authorized 2026-10-03)
EVENT_STRATEGY = {
    "hse-machine-hunt":
        "python3 home_screen_event.py --items - nodes ranked by event Pt/E, attempts honored",
    "hse-training-rush":
        "python3 home_screen_event.py --items - Pt/E plus the 2.5-5x hero XP column;"
        " --xp-needed to rank by XP/E when an XP-blocked hero is the target",
    "hse-training-rush":
        "python3 home_screen_event.py --event training-rush --items --xp-needed - "
        "every kill counts; XP-multiplied runs, rank by XP/E when the XP gate bites",
    "legendary-event":
        "python3 lres_report.py - next-battle teams, power walls, mission farms",
}

# hero gates, in report order. RANKUP gates are the ones that keep a hero
# out of the farm list; the others are parallel concerns (abilities, gear).
GATE_NOTES = {
    "XP": ("rank-up grid needs a higher hero level", True),
    "XP-MAXED": ("rarity cap below the requirement - terminal", True),
    "ITEMS": ("next rung unpriceable (coming-soon items)", True),
    "BADGES": ("short alliance pool - blocks abilities only", False),
    "GEAR": ("slot(s) below target rarity", False),
}
GATE_ORDER = ("XP", "XP-MAXED", "ITEMS", "BADGES", "GEAR")


class WitnessError(Exception):
    """One witness CLI could not deliver its --json."""


def witness(key):
    """Run one witness CLI and parse its --json stdout."""
    script, args = WITNESSES[key]
    path = BASE / script
    if not path.exists():
        raise WitnessError(f"{script}: not found")
    proc = subprocess.run([sys.executable, str(path)] + args,
                          capture_output=True, text=True)
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip().splitlines()
        raise WitnessError(f"{script}: exited {proc.returncode}"
                           + (f" ({detail[-1]})" if detail else ""))
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as error:
        raise WitnessError(f"{script}: non-JSON ({error})") from error


def fetch():
    """Run every witness, keep going, report every failure at once.

    The five hub witnesses (rank/xp/ab/pw/gear) are required - a brief
    that silently drops one would lie. team and event are optional:
    their absence prints a WARNINGS line and skips their sections.
    """
    docs, failed = {}, []
    for key in WITNESSES:
        try:
            docs[key] = witness(key)
        except WitnessError as error:
            failed.append(str(error))
    hubs = set(WITNESSES) - {"team", "event"}
    fatal = [f for f in failed if any(f.startswith(s) for s in hubs)]
    if fatal:
        sys.exit("error: cannot convene, witness(s) failed:\n  "
                 + "\n  ".join(fatal))
    return docs, [f for f in failed if f not in fatal]


# --- assembly ---------------------------------------------------------------
#
# No witness's logic is repeated here: we only read the fields each one
# already published and join them per hero (rank rows carry a name, the
# rest an id - xp provides the name -> id bridge).

def assemble(top):
    docs, failed = fetch()
    xp_rows = docs["xp"]["rows"]
    by_name = {row["name"]: row["id"] for row in xp_rows}
    xp_by_id = {row["id"]: row for row in xp_rows}
    rank_rows = {row["name"]: row for row in docs["rank"]["rows"]}
    ab_by_id = {row["id"]: row for row in docs["ab"]["rows"]}
    pw_by_id = {row["id"]: row for row in docs["pw"]["rows"]}
    gear_by_id = {row["id"]: row for row in docs["gear"]["rows"]}

    orphan = [n for n in rank_rows if n not in by_name]
    if orphan:
        sys.exit(f"error: cannot join rank rows to xp rows: {orphan[:3]}")

    # ---- WHERE: gate tags per hero ------------------------------------
    blocked = []            # [{id, name, rank, gates:[{gate, detail}]}]
    gates = {g: [] for g in GATE_ORDER}
    for row in xp_rows:
        row["_tags"] = []
        if row["status"] == "xp-blocked":
            plan = row.get("plan") or {}
            if plan.get("buy"):
                buys = ", ".join(f"{n}x {b}" for b, n in plan["buy"].items())
                state = (f"covered - buy {buys} "
                         f"({plan.get('guild_credits') or 0:,} gc)")
            elif plan.get("covered"):
                state = "covered by books in stock"
            else:
                state = (f"still short {plan.get('short_xp', 0):,} xp "
                         "even after stock and shop buys")
            row["_tags"].append(
                ("XP", f"{row['gap_xp']:,} xp short "
                       f"(lvl {row['xp_level']}->{row['max_req']}) - {state}"))
        elif row["status"] == "maxed":
            row["_tags"].append(("XP-MAXED", f"rarity cap {row['cap']} < need "
                                             f"{row['max_req']} - terminal"))
        for gate, _ in row["_tags"]:
            gates[gate].append(row["id"])
    for row in xp_rows:
        ab_row = ab_by_id.get(row["id"], {})
        if ab_row.get("status") == "badge-blocked":
            short = " ".join(f"{rarity}x{n}"
                             for rarity, n in (ab_row.get("short") or {}).items())
            row["_tags"].append(("BADGES", f"short {short} (shared pool)"))
            gates["BADGES"].append(row["id"])
        gear_row = gear_by_id.get(row["id"], {})
        if gear_row.get("green"):
            row["_tags"].append(("GEAR", f"{gear_row['below']} slot(s) below target"))
            gates["GEAR"].append(row["id"])
        rank_row = rank_rows.get(row["name"]) or {}
        if rank_row.get("next_blocked"):
            row["_tags"].append(
                ("ITEMS", "next rung unpriceable (coming-soon items)"))
            gates["ITEMS"].append(row["id"])
    for row in xp_rows:
        if row["_tags"]:
            blocked.append({
                "id": row["id"],
                "name": row["name"],
                "rank": rank_name(row["rank"]),
                "gates": [{"gate": g, "detail": d} for g, d in row["_tags"]],
            })

    # ---- NEXT: five actions, highest leverage first --------------------
    ab_meta = docs["ab"]["meta"]
    purchases = [p for p in (ab_meta.get("short_pools") or []) if p.get("short")]

    xp_blocked = [r for r in xp_rows if r["status"] == "xp-blocked"]
    covered = [r for r in xp_blocked if (r.get("plan") or {}).get("covered")]
    need_books = [r for r in xp_blocked if r not in covered]
    buyers = [r for r in covered if (r.get("plan") or {}).get("buy")]
    shop_books = {}
    for r in buyers:
        for bid, n in ((r.get("plan") or {}).get("buy") or {}).items():
            shop_books[bid] = shop_books.get(bid, 0) + n
    books = {
        "blocked": len(xp_blocked),
        "covered": len(covered),
        "need_purchase": len(need_books),
        "buyers": len(buyers),
        "shop_gc": sum((r.get("plan") or {}).get("guild_credits") or 0
                       for r in buyers),
        "shop_books": shop_books,
        "apply_gold": sum((r.get("plan") or {}).get("apply_gold") or 0
                          for r in covered),
        "heroes": [{"id": r["id"], "name": r["name"], "gap_xp": r["gap_xp"],
                    "covered": bool((r.get("plan") or {}).get("covered")),
                    "apply_gold": (r.get("plan") or {}).get("apply_gold")}
                   for r in sorted(xp_blocked, key=lambda r: -r["gap_xp"])],
    }

    # farm list: heroes whose NEXT RUNG is actionable right now. Badges and
    # gear are parallel concerns - they never gate an item rank-up. Energy 0
    # = every leaf already in inventory (a FREE rung) - kept, ranked first.
    # `contested` = the rank report's bank-contention list: an item whose
    # next-rung claims exceed the bank. Farm rows spending such an item are
    # marked - the "free" rungs withdraw from the same pool everyone wants.
    contested = {c["id"]: c for c in (docs["rank"].get("contention") or [])}
    farm = []
    for name, rank_row in rank_rows.items():
        nid = by_name[name]
        hero_gates = {g for g, _ in (xp_by_id.get(nid, {}).get("_tags") or [])}
        if hero_gates & {"XP", "XP-MAXED", "ITEMS"}:
            continue
        if rank_row.get("blocked") or rank_row.get("next_blocked"):
            continue
        pw_row = pw_by_id.get(nid, {})
        energy = rank_row.get("energy") or 0
        d_power = pw_row.get("d_power") or 0
        if d_power <= 0 or pw_row.get("terminal"):
            continue
        spent = (rank_row.get("detail") or {}).get("spent_from_inventory") or {}
        hits = [i for i in spent if i in contested]
        # free-rung drains first, then the widest over-claim: the item the
        # whole roster fights over is the one worth naming in 2 slots
        hits.sort(key=lambda i: (-contested[i]["free_rungs"],
                                 -(contested[i]["claims"] - contested[i]["bank"]),
                                 contested[i]["name"].lower()))
        drains = [contested[i]["name"] for i in hits]
        farm.append({
            "id": nid, "name": name, "next": rank_row.get("next"),
            "energy": round(energy, 1), "d_power": d_power,
            "pow_per_energy": (round(d_power / energy, 1) if energy > 0
                               else None),
            "days": round(rank_row.get("days") or 0),
            "contested": drains,
        })
    farm.sort(key=lambda r: (r["pow_per_energy"] is not None,
                             -(r["pow_per_energy"] or 0)))

    gear_buys = [{"id": r["id"], "name": r["name"], "below": r["below"]}
                 for r in docs["gear"]["rows"] if r.get("green")]
    gear_buys.sort(key=lambda r: (-r["below"], r["name"]))
    team = [{"id": r["id"], "verdict": r["verdict"], "missing": r["missing"]}
            for r in (docs.get("team") or {}).get("rows", [])
            if r["verdict"] != "fieldable"]
    events = (docs.get("event") or {}).get("events") or []

    gates_out = []
    for gate in GATE_ORDER:
        note, is_rankup = GATE_NOTES[gate]
        gates_out.append({"gate": gate, "count": len(gates[gate]),
                          "note": note, "blocks_rankups": is_rankup})

    return {
        "schema_version": 1,
        "meta": {
            "snapshot": docs["xp"]["meta"].get("snapshot"),
            "player": docs["xp"]["meta"].get("player"),
            "power_level": docs["xp"]["meta"].get("power_level"),
            "witnesses": [script for script, _ in WITNESSES.values()],
            "top": top,
            "gated_heroes": len(blocked),
            "roster": len(xp_rows),
            "badge_gold_total": ab_meta.get("gold_total"),
            "gear_target": docs["gear"]["meta"].get("target"),
            "gear_buy_bs": docs["gear"]["meta"].get("buy_bs"),
            "events": events,
            "failed_sources": failed,
        },
        "gates": gates_out,
        "blocked": blocked,
        "purchases": purchases,
        "books": books,
        "farm": farm,
        "gear": gear_buys,
        "team": team,
    }


# --- rendering --------------------------------------------------------------

def _bottom_line(asm, top):
    parts = []
    short = sum(p.get("short", 0) for p in asm["purchases"])
    if asm["purchases"]:
        parts.append(f"buy {short} badges in the guild-war shop "
                     f"(unblocks {asm['gates'][3]['count']} heroes' abilities)")
    books = asm["books"]
    if books["need_purchase"]:
        parts.append(f"buy XP books for {books['need_purchase']} heroes")
    elif books["covered"]:
        buys = (f"{books['buyers']} need {books['shop_gc']:,} gc of books, "
                if books.get("buyers") else "all in stock, ")
        parts.append(f"apply XP books to {books['covered']} heroes "
                     f"({buys}{books['apply_gold']:,} gold)")
    if asm["farm"]:
        best = asm["farm"][0]
        cost = ("free" if best["energy"] <= 0
                else f"{best['energy']:,.0f} E")
        drain = (f"; drains {best['contested'][0]} from the shared bank"
                 if best.get("contested") else "")
        parts.append(f"farm {best['name']} -> {best['next']} "
                     f"({cost}, +{best['d_power']:,} power{drain})")
    if asm["gear"]:
        parts.append(f"fix gear on {len(asm['gear'])} heroes")
    for comp in asm["team"]:
        parts.append(f"unlock {', '.join(comp['missing'])} for {comp['id']}")
    return "NEXT: " + " -> ".join(parts) if parts else "NEXT: nothing blocked - farm anywhere"


def render(asm, top):
    m = asm["meta"]
    print(f"=== Next step brief === snapshot {m['snapshot']} | {m['player']} | "
          f"PL{m['power_level']}")
    print("witnesses: " + ", ".join(w.replace(".py", "") for w in m["witnesses"]))

    if m.get("events"):
        print("\nEVENTS")
        now = datetime.now(timezone.utc)
        for e in m["events"]:
            end = datetime.fromisoformat(e["end"])
            if e.get("live"):
                left = end - now
                print(f"  LIVE     {e['id']}  until {end:%Y-%m-%d %H:%M} UTC "
                      f"({left.days}d {left.seconds // 3600}h left)")
                print("           " + EVENT_STRATEGY.get(
                    e["id"], "no dedicated report yet - say the word to build one ad hoc"))
            else:
                start = datetime.fromisoformat(e["start"])
                print(f"  upcoming {e['id']}  {start:%Y-%m-%d %H:%M} -> "
                      f"{end:%Y-%m-%d %H:%M} UTC")

    print(f"\nWHERE I'M BLOCKED - {m['gated_heroes']}/{m['roster']} heroes "
          "carry >=1 gate")
    for g in asm["gates"]:
        if not g["count"]:
            print(f"  {g['gate']:<9} 0")
            continue
        flag = "" if g["blocks_rankups"] else "  (not a rank-up block)"
        print(f"  {g['gate']:<9} {g['count']:<3} {g['note']}{flag}")
        shown = [b for b in asm["blocked"]
                 if any(t["gate"] == g["gate"] for t in b["gates"])][:5]
        # width from the longest name in THIS block, so nothing runs together
        nw = max((len(b["name"]) for b in shown), default=0)
        rw = max((len(b["rank"]) for b in shown), default=0)
        for b in shown:
            detail = next(t["detail"] for t in b["gates"] if t["gate"] == g["gate"])
            print(f"             {b['name']:<{nw}}  {b['rank']:<{rw}}  {detail}")
        rest = g["count"] - len(shown)
        if rest > 0:
            print(f"             ... +{rest} more")
    for comp in asm["team"]:
        print(f"  TEAM      comp not fieldable: {comp['id']} "
              f"(missing {', '.join(comp['missing'])}) - unlock, not farmable")

    print("\nWHAT TO DO NEXT")
    idx = 1
    if asm["purchases"]:
        print(f"  {idx}) POOL PURCHASES - one shared pool per alliance; "
              "buying here unblocks every hero at once")
        for p in asm["purchases"]:
            print(f"     {p['alliance']} {p['rarity']:<9} buy {p['short']:<3} "
                  f"(demand {p['demand']} / stock {p['stock']})")
        idx += 1
    books = asm["books"]
    if books["blocked"]:
        if books["need_purchase"]:
            line = (f"{books['need_purchase']} still short even after "
                    "stock and shop buys")
        elif books["buyers"]:
            line = (f"all {books['covered']} covered - {books['buyers']} of "
                    f"them need guild-shop books ({books['shop_gc']:,} gc), "
                    f"{books['apply_gold']:,} gold to apply")
        else:
            line = (f"all {books['covered']} covered by books in stock "
                    f"({books['apply_gold']:,} gold to apply), 0 to buy")
        print(f"  {idx}) APPLY XP BOOKS - {books['blocked']} heroes are "
              f"xp-blocked; {line}")
        idx += 1
    if asm["farm"]:
        print(f"  {idx}) FARM - {len(asm['farm'])} actionable rungs ranked by "
              f"power per energy (top {min(top, len(asm['farm']))})")
        rows = asm["farm"][:top]
        nw = max(len(r["name"]) for r in rows)
        print(f"     {'hero':<{nw}}  {'next rung':<16}{'energy':>7}{'+power':>9}"
              f"{'pow/E':>7}{'days':>6}")
        for r in rows:
            pe = "free" if r["pow_per_energy"] is None else f"{r['pow_per_energy']:.1f}"
            mark = ""
            if r.get("contested"):
                mark = "  ! " + ", ".join(r["contested"][:2]) \
                    + ("..." if len(r["contested"]) > 2 else "")
            print(f"     {r['name']:<{nw}}  {r['next']:<16}{r['energy']:>7,.0f}"
                  f"{r['d_power']:>9,}{pe:>7}{r['days']:>6}{mark}")
        if any(r.get("contested") for r in rows):
            print("     ! = spends bank stock other next rungs also claim - "
                  "the first promoted takes it, the rest must craft")
        idx += 1
    if asm["gear"]:
        names = ", ".join(f"{g['name']} ({g['below']})" for g in asm["gear"][:5])
        buy = asm["meta"].get("gear_buy_bs")
        print(f"  {idx}) GEAR - {len(asm['gear'])} heroes below "
              f"{asm['meta'].get('gear_target')} target: {names}"
              + (f"; buy line {buy} BS" if buy else ""))
        idx += 1
    for comp in asm["team"]:
        print(f"  {idx}) TEAM - {comp['id']} needs "
              f"{', '.join(comp['missing'])} (unlock, not farmable)")

    print("\n" + _bottom_line(asm, top))
    if m.get("failed_sources"):
        print("\nWARNINGS - some witnesses failed, their tracks are missing:")
        for w in m["failed_sources"]:
            print(f"  {w}")
    print(f"note: assembled from {len(m['witnesses'])} witnesses - energy/power/prices come "
          "from them\n      unchanged (next_step.py computes only "
          "power-per-energy). Signals, not orders.")


# --- main -------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--top", type=int, default=10,
                    help="rows in the farm list (default 10)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()
    if args.top < 1:
        ap.error("--top must be >= 1")

    asm = assemble(args.top)
    if args.json:
        json.dump(asm, sys.stdout, indent=1)
        print()
        return
    render(asm, args.top)


if __name__ == "__main__":
    run(main)
