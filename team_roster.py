#!/usr/bin/env python3
"""Team comp roster: which curated guild-raid comps can you field?

Some heroes are only strong in combinations, so a per-hero score does not
answer "what can I actually bring?". This tool takes the curated comps the
community ships (terminus-maximus + cognitae, mirrored by the planner at
research/.../Data/guild-raid-meta/guild-raid-comps.json) and checks each one
against YOUR roster.

One concern only: fieldability. It reports, per comp:

  * signature   the comp's identity hero - a comp without it is not that comp
  * core        the fixed backbone (owned / total)
  * flex        the interchangeable bench (owned / total)
  * MoW         the recommended machines of war (owned / total)
  * Heroes      how many distinct owned heroes are in the pool (a team is 5)
  * Verdict     fieldable | no signature | thin N/5
  * Missing     signature + core heroes you do not own (deduped)

Verdicts deliberately ignore the MoW count (a raid is playable without one;
the column still shows what you have). NO synergy score is computed: there is
no combo/synergy/bonus key anywhere in the game config, so inventing a number
would be dishonest. Boss-by-boss recommendations with efficiency ratings live
next door in guild-raid-meta-boss-*.json and are left to the caller.

Sources: tacticus-player.json (roster + names) and the planner catalog
(names for heroes you do not own). Excludes nothing - machines of war are
part of comps (as signature and as mowIds), so unlike the rank tools this
one deliberately keeps them.

Usage:
    python3 team_roster.py                # all comps, fieldable first
    python3 team_roster.py multi          # one comp in detail (partial id)
    python3 team_roster.py --json         # machine-readable
"""
import argparse
import json
import pathlib
import sys

from rank_up_report import MOW_IDS, load_player, rank_name, run

BASE = pathlib.Path(__file__).resolve().parent
DATA = (BASE / "research" / "tacticus-planner-api" / "src" /
        "TacticusPlanner.GameCatalog" / "Data")
COMPS_FILE = DATA / "guild-raid-meta" / "guild-raid-comps.json"
UNITS_DIR = DATA / "units"

TEAM_SIZE = 5                  # heroes per raid team (plus 1 machine of war)
VERDICTS = ("fieldable", "no signature", "thin")


# --- data --------------------------------------------------------------------


def load_comps():
    """The curated comps + their display names for heroes you do not own."""
    if not COMPS_FILE.exists():
        sys.exit(f"error: {COMPS_FILE} missing - the curated comps live in "
                 "research/, see research/README.md")
    try:
        raw = json.loads(COMPS_FILE.read_text())
        comps = raw.get("comps") or []
        if not isinstance(comps, list) or not comps:
            raise ValueError("no comps[] in the file")
    except (json.JSONDecodeError, ValueError, OSError) as error:
        sys.exit(f"error: cannot read {COMPS_FILE}: {error}")

    names = {}
    if UNITS_DIR.is_dir():
        for path in sorted(UNITS_DIR.glob("units-*.json")):
            try:
                doc = json.loads(path.read_text())
            except (json.JSONDecodeError, OSError):
                continue
            for unit in doc.get("characters") or []:
                if unit.get("id") and unit.get("name"):
                    names[unit["id"]] = unit["name"]
    return {
        "source": raw.get("sourceId") or "unknown source",
        "updated": raw.get("updatedOn") or "?",
        "comps": comps,
        "names": names,
    }


def display_name(unit_id, names, units_by_id):
    """Player names win (MoWs like Z'Kar are only named there)."""
    unit = units_by_id.get(unit_id)
    if unit and unit.get("name"):
        return unit["name"]
    return names.get(unit_id, unit_id)


# --- one comp ---------------------------------------------------------------

def analyse(comp, units_by_id, names):
    sig = comp.get("signatureUnitId") or ""
    core = list(dict.fromkeys(comp.get("coreCharacterIds") or []))
    flex = list(dict.fromkeys(comp.get("flexCharacterIds") or []))
    mow = list(dict.fromkeys(comp.get("mowIds") or []))
    sig_is_mow = sig in MOW_IDS
    if sig_is_mow and sig not in mow:
        mow = [sig] + mow

    # heroes = the regular-hero pool (MoWs live in their own slot)
    seen, heroes = set(), []
    for role, ids in (("signature", [] if sig_is_mow else [sig]),
                      ("core", core), ("flex", flex)):
        for unit_id in ids:
            if not unit_id or unit_id in seen:
                continue
            seen.add(unit_id)
            unit = units_by_id.get(unit_id) or {}
            heroes.append({
                "id": unit_id,
                "name": display_name(unit_id, names, units_by_id),
                "role": role,
                "owned": bool(unit),
                "mow": unit_id in MOW_IDS,
                "rank": unit.get("rank"),
                "rank_name": ("MoW" if unit_id in MOW_IDS
                              else (rank_name(unit["rank"])
                                    if unit.get("rank") is not None else "-")),
            })

    core_ids = list(dict.fromkeys(comp.get("coreCharacterIds") or []))
    flex_ids = list(dict.fromkeys(comp.get("flexCharacterIds") or []))
    owned_ids = {h["id"] for h in heroes if h["owned"]}
    # a MoW signature is not in heroes/owned_ids - it is owned iff the player
    # has that unit at all (units_by_id, which includes machines of war)
    sig_ok = sig in units_by_id

    if not sig_ok:
        verdict = "no signature"
    elif len(owned_ids) < TEAM_SIZE:
        verdict = "thin"
    else:
        verdict = "fieldable"

    # missing = the identity heroes (signature + core) you do not own.
    # units_by_id is the ownership test for MoWs too (they are not in
    # owned_ids/heroes); dedupe because the signature often is core as well
    missing, seen_missing = [], set()
    for unit_id in [sig] + core_ids:
        if unit_id in units_by_id or unit_id in seen_missing:
            continue
        seen_missing.add(unit_id)
        missing.append(display_name(unit_id, names, units_by_id))

    return {
        "id": comp.get("id") or "?",
        "signature_unit_id": sig,
        "signature_is_mow": sig_is_mow,
        "signature_owned": sig in units_by_id,
        "signature": display_name(sig, names, units_by_id),
        "signature_rank": ((units_by_id.get(sig) or {}).get("rank")
                           if not sig_is_mow else None),
        "core_total": len(core_ids),
        "core_owned": sum(1 for i in core_ids if i in owned_ids),
        "flex_total": len(flex_ids),
        "flex_owned": sum(1 for i in flex_ids if i in owned_ids),
        "mow_total": len(mow),
        "mow_owned": sum(1 for i in mow if i in units_by_id),
        "mow_ids": mow,
        "pool_owned": len(owned_ids),
        "verdict": verdict,
        "missing": missing,
        "heroes": heroes,
    }


# --- rendering ---------------------------------------------------------------

def full_table(rows, meta):
    print(f"=== Team comps vs roster === snapshot {meta['snapshot']} | "
          f"{meta['player']} | source {meta['source']} "
          f"(updated {meta['updated']})")
    w_id = max([len(r["id"]) for r in rows] + [6])
    sigs = []
    for r in rows:
        s = r["signature"] + ("" if r["signature_owned"] else " (MISSING)")
        if r["signature_is_mow"]:
            s += " [MoW]"
        sigs.append(s)
    w_sig = max([len(s) for s in sigs] + [9])
    head = (f"  {'Comp':<{w_id}}  {'Signature':<{w_sig}}  {'Core':>5}  "
            f"{'Flex':>5}  {'MoW':>5}  {'Heroes':>6}  {'Verdict':<13}  Missing")
    print(head)
    print("  " + " " * w_id + "  " + " " * w_sig + "  " + "-" * 5 + "  "
          + "-" * 5 + "  " + "-" * 5 + "  " + "-" * 6 + "  " + "-" * 13
          + "  " + "-" * 7)
    for r, sig in zip(rows, sigs):
        missing = ", ".join(r["missing"][:3])
        if len(r["missing"]) > 3:
            missing += f" +{len(r['missing']) - 3}"
        verdict = {"fieldable": "fieldable",
                   "no signature": "NO SIGNATURE",
                   "thin": f"thin {r['pool_owned']}/{TEAM_SIZE}"}[r["verdict"]]
        print(f"  {r['id']:<{w_id}}  {sig:<{w_sig}}  "
              f"{r['core_owned']}/{r['core_total']:>2}  "
              f"{r['flex_owned']}/{r['flex_total']:>2}  "
              f"{r['mow_owned']}/{r['mow_total']:>2}  "
              f"{r['pool_owned']:>3}/{TEAM_SIZE}  {verdict:<13}  {missing}")

    counts = {v: sum(1 for r in rows if r["verdict"] == v) for v in VERDICTS}
    print(f"\ncomps: {len(rows)} | fieldable {counts['fieldable']} | "
          f"needs signature {counts['no signature']} | thin {counts['thin']}")
    print("caveat: curated community comps (terminus-maximus + cognitae), "
          "NOT a computed\n        synergy score - no combo/bonus key exists "
          "in the game config. A team is\n        "
          f"{TEAM_SIZE} heroes from signature+core+flex plus 1 machine of "
          "war; boss-by-boss\n        recommendations live in "
          "research/.../guild-raid-meta/.")


def show_detail(row, meta):
    print(f"=== {row['id']} === source {meta['source']} "
          f"(updated {meta['updated']})   [{row['verdict']}]")
    sig = row["signature"]
    if row["signature_is_mow"]:
        sig_line = (f"signature  {sig} [MoW]"
                    + ("" if row["signature_owned"] else " - MISSING"))
    else:
        state = "owned" if row["signature_owned"] else "MISSING"
        rank = row["signature_rank"]
        sig_line = (f"signature  {sig} ({row['signature_unit_id']}) "
                    f"- {state}"
                    + (f", {rank_name(rank)}" if rank is not None else ""))
    print(f"  {sig_line}")
    for role, owned, total in (("core", row["core_owned"], row["core_total"]),
                               ("flex", row["flex_owned"], row["flex_total"]),
                               ("MoW", row["mow_owned"], row["mow_total"])):
        print(f"  {role:<10} {owned}/{total}")
    for h in row["heroes"]:
        if h["owned"]:
            continue
        print(f"    missing  {h['name']} ({h['id']}) [{h['role']}]")
    owned = [h for h in row["heroes"] if h["owned"]]
    print(f"  available {len(owned)}/{TEAM_SIZE} heroes needed:")
    for h in sorted(owned, key=lambda h: (h["role"], h["name"])):
        print(f"    {h['name']:<20} {h['rank_name']:<14} {h['role']}")
    if row["missing"]:
        print(f"  verdict: {row['verdict']} - missing identity heroes: "
              + ", ".join(row["missing"]))
    elif row["verdict"] == "fieldable":
        print(f"  verdict: fieldable - {row['pool_owned']} heroes available "
              f"for {TEAM_SIZE} slots"
              + ("" if row["mow_owned"] else
                 f"; no recommended MoW owned ({row['mow_total']} listed)"))
    else:
        print(f"  verdict: thin - only {row['pool_owned']}/{TEAM_SIZE} "
              "heroes available")
    print("caveat: curated community comp, not a synergy score; hero-by-hero "
          "boss picks\n        with efficiency ratings are in "
          "research/.../guild-raid-meta/.")


# --- main --------------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="*",
                    help="comp id or part of it (partial ok, case-insensitive; "
                         "e.g. 'multi', 'zkar'). Omit for the full table")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    player, meta_raw = load_player()
    data = load_comps()
    units_by_id = {u.get("id"): u for u in (player.get("units") or []) if u.get("id")}
    meta = {
        "snapshot": meta_raw.get("lastUpdatedOn"),
        "player": (player.get("details") or {}).get("name"),
        "power_level": (player.get("details") or {}).get("powerLevel"),
        "source": data["source"],
        "updated": data["updated"],
    }

    rows = [analyse(c, units_by_id, data["names"]) for c in data["comps"]]
    order = {v: i for i, v in enumerate(VERDICTS)}
    rows.sort(key=lambda r: (order[r["verdict"]], -r["pool_owned"], r["id"]))

    needle = "".join(args.query).lower()
    if needle:
        key = "".join(ch for ch in needle if ch.isalnum())
        matched = [r for r in rows
                   if key in "".join(ch for ch in r["id"].lower() if ch.isalnum())]
        if not matched:
            sys.exit(f"error: no comp matches {' '.join(args.query)!r}")
        rows = matched

    if args.json:
        json.dump({"schema_version": 1, "meta": meta, "rows": rows},
                  sys.stdout, indent=1)
        print()
        return

    if args.query and len(rows) == 1:
        show_detail(rows[0], meta)
    else:
        full_table(rows, meta)


if __name__ == "__main__":
    run(main)
