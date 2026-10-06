#!/usr/bin/env python3
"""Home-screen kill events: which campaign node is worth the energy.

Two events, one report (`--event`, default: whichever is live right now):
`machine-hunt` scores Mechanical-trait kills only, `training-rush` scores
every non-Summon/non-Steppable kill AND multiplies hero XP by the character's
rarity (2.5x Common .. 5x Mythic). Both pay 3 pts/kill in Standard/Mirror and
5 in Elite/MirrorElite, so Pt/E is the reward rate either way — the per-event
difference is only which enemies count, which is why this is one tool.

Who counts comes from planner npcs/*.json traits vs the event's
traitRestrictions (parsed out of the shipped client bundle, which is the only
place the trackers live); per-node enemy lists + loot from
campaign-battles/*.json. `Att` = attempts left today for that exact node,
straight from the player snapshot (`progress.campaigns`, cap 10 standard /
6 elite); nodes with 0 left are hidden unless `--all`, `?` = no attempt data
(event campaigns aren't in the snapshot). `XP/E` is the node's battle XP before
the event multiplier (gameconfig has it, the planner doesn't), so it ranks nodes
for the XP rush independently of points.

--items adds the balance view: rank nodes that drop leaves your roster's next
rank-ups still need (via `rank_up_report --energy --json` — the wanted set is
tier_detail[0] of every row, kept per hero; labels come from the upgrades
catalog; this tool prices nothing itself).

--xp-needed re-ranks on XP/E instead of Pt/E and lists the heroes the XP gate
blocks (via `xp_gate.py --only blocked --json`) — the mode to use when XP, not
points, is what a rank-up is waiting on. Nodes whose XP is unknown (the event
campaigns) sink to the bottom rather than passing for the best farm.

--track prints the reward ladder (points -> chest/XP/gold) and the gold
trade-in for leftover points. It needs the 20 MB gameconfig, so it is opt-in;
the player API carries no event progress, hence no "points to next milestone".
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from rank_up_report import run  # noqa: E402
import energy_cost  # noqa: E402

HERE = Path(__file__).resolve().parent
DATA = HERE / "research/tacticus-planner-api/src/TacticusPlanner.GameCatalog/Data"
CLIENT_CFG = HERE / "research/misc/bundle.js"  # ships the liveEventConfig blocks
GAMECONFIG = HERE / "research/datamine/gameconfig142.json"

# Per-event: which client tier config holds its trackers, which trait
# restrictions decide whose kills count, and the planner occurrence to window
# it by. Tier (low/mid/high) is chosen server-side and is NOT derivable — the
# trackers/modifiers are identical across tiers, only the ladder differs.
EVENTS = {
    "machine-hunt": {
        "label": "machine hunt", "definition": "hse-machine-hunt",
        "tier": "machine_hunt_tier_high",
        "progress": "hse_machine_hunt_tier_high",
        "needs": {"Mechanical"}, "skip": set(),
    },
    "training-rush": {
        "label": "training rush", "definition": "hse-training-rush",
        "tier": "rarity_training_rush_tier_high",
        "progress": "hse_rarity_training_rush_tier_high",
        "needs": set(), "skip": {"Summon", "Steppable"},
    },
}


@lru_cache(maxsize=None)
def event_block(event_name):
    """Raw minified text of the shipped client's tier config for `event_name`.

    The killUnits trackers, the unitXp modifiers and the reward ladder live
    NOWHERE else — gameconfig's `loot.tieredProgressRewards` holds the same
    ladder but not the trackers. bundle.js is minified JS with unquoted keys,
    so targeted regexes beat a JSON parse. Each tier is delimited by the
    `offers:` sibling that closes it (liveEventConfig -> ... -> rewards).
    """
    if not CLIENT_CFG.exists():
        sys.exit(f"missing {CLIENT_CFG} — see research/README.md")
    s = CLIENT_CFG.read_text(encoding="utf8", errors="replace")
    i = s.find(f'eventName:"{event_name}"')
    if i < 0:
        return None
    start = s.rindex("liveEventConfig:{", 0, i)   # eventName is a field of it
    end = s.find(",offers:{", i)
    return s[start:end if end > 0 else len(s)]


def kill_rates(event_name):
    """{points_per_kill} from the killUnits trackers (standard vs elite).

    The trackers split on game mode, not on the trait: elite nodes carry the
    higher rate — that is where the 5 comes from.
    """
    blk = event_block(event_name) or ""
    out = set()
    for tr in re.finditer(r'type:"killUnits".{0,240}?points:(\d+)', blk):
        out.add(int(tr.group(1)))
    return out or {3}


def xs_mult(event_name):
    """{rarity: XP multiplier} from a unitXp modifier, else {}.

    Training Rush scales hero XP by the CHARACTER's rarity and ignores the
    battle's rarity cap — so Mythic carries 5x, Common 2.5x.
    """
    blk = event_block(event_name) or ""
    m = re.search(r'type:"unitXp".{0,400}?modifiers:\{([^}]+)\}', blk)
    if not m:
        return {}
    return {k: int(v) / 100 for k, v in
            re.findall(r"(\w+):(\d+)", m.group(1))}


def reward_ladder(progress_id):
    """[(points, rewardId, endless)] from gameconfig loot.tieredProgressRewards.

    Needs the 20 MB gameconfig, so only loaded under --track.
    """
    if not GAMECONFIG.exists():
        return []
    doc = json.load(open(GAMECONFIG))["clientGameConfig"]["loot"]["tieredProgressRewards"]
    return [(r["requiredProgress"], r["chestRewardId"], bool(r.get("endless")))
            for r in doc.get(progress_id, [])]


def gold_per_point(event_name):
    """Gold the leftover points trade in for, or None."""
    m = re.search(r"tradeInResourceAmountPerPoint:(\d+)", event_block(event_name) or "")
    return int(m.group(1)) if m else None


def npc_traits():
    """npc id -> traits, one pass over the catalog."""
    out = {}
    for f in sorted((DATA / "npcs").glob("*.json")):
        for n in json.load(open(f))["npcs"]:
            out[n["id"]] = n.get("traits") or []
    return out


def countable(traits, event):
    """Npc ids whose kills score points, per the event's traitRestrictions.

    machine hunt allows Mechanical only; training rush disallows Summon and
    Steppable (loot props, mines) and counts everything else.
    """
    ev = EVENTS[event]
    return {i for i, t in traits.items()
            if (not ev["needs"] or ev["needs"] & set(t)) and not ev["skip"] & set(t)}


def mech_ids():
    return countable(npc_traits(), "machine-hunt")


def attempts():
    """(groupId, battleIndex) -> attemptsLeft, from the player snapshot."""
    doc = json.load(open(HERE / "tacticus-player.json"))
    return {(c["id"], b["battleIndex"]): b["attemptsLeft"]
            for c in doc["player"]["progress"]["campaigns"]
            for b in c["battles"]}


def _occurrences():
    """(definitionId, start, end) for every planned occurrence (planner events data)."""
    f = DATA / "events" / "event-occurrences.json"
    if not f.exists():
        return []
    out = []
    for o in json.load(open(f)):
        out.append((o.get("definitionId"),
                    datetime.fromisoformat(o["startUtc"].replace("Z", "+00:00")),
                    datetime.fromisoformat(o["endUtc"].replace("Z", "+00:00"))))
    return out


def event_window(now, event="machine-hunt"):
    """End of the live occurrence of `event`, or None."""
    did = EVENTS[event]["definition"]
    for d, start, end in _occurrences():
        if d == did and start <= now <= end:
            return end
    return None


def live_event(now):
    """The event to report on: --event, else whichever one is live."""
    for name in EVENTS:
        if event_window(now, name):
            return name
    return "machine-hunt"


def legendary_cycle(now):
    """Window of the Fixed legendary-event cycle covering now, else the next one.

    Planner lists no *occurrence* for the LRES (it only has the definition), so
    derive it: anchorUtc + k*intervalDays, durationDays long. Calibrated against
    the in-game countdown 2026-10-04 (Oct 4 00:00Z -> Oct 11 00:00Z = 6d 16h).
    """
    f = DATA / "events" / "event-definitions.json"
    if not f.exists():
        return None
    d = next((x for x in json.load(open(f))
              if x.get("id") == "legendary-event"), None)
    rec = (d or {}).get("recurrence") or {}
    if rec.get("kind") != "Fixed" or not rec.get("intervalDays"):
        return None
    anchor = datetime.fromisoformat(rec["anchorUtc"].replace("Z", "+00:00"))
    step = timedelta(days=int(rec["intervalDays"]))
    dur = timedelta(days=int(rec.get("durationDays") or 7))
    k = 0 if now < anchor else int((now - anchor) // step)
    start, end = anchor + step * k, anchor + step * k + dur
    if now > end:                       # cycle finished - jump to the next one
        start, end = start + step, end + step
    return start, end


def event_list(now, cap=4):
    """Live events first, then upcoming: [{id, start, end, live}].

    Includes the derived legendary-event (LRES) cycle alongside the planned
    occurrences - the LRES window rides this list into next_step's EVENTS.
    """
    evs = [(d, s, e) for d, s, e in _occurrences() if e >= now]
    cyc = legendary_cycle(now)
    if cyc:
        evs.append(("legendary-event", cyc[0], cyc[1]))
    evs.sort(key=lambda e: (e[1] > now, e[1]))
    return [{"id": d, "start": s.isoformat(), "end": e.isoformat(),
             "live": s <= now <= e} for d, s, e in evs[:cap]]


def battle_xp():
    """(campaignId, battleId) -> xp awarded, from gameconfig campaigns.

    ponytail: the 20 MB gameconfig is parsed once per run, and only when the
    event pays XP at all. battleId is a string and not always numeric ("03B"),
    so it is keyed as-is; a node with no gameconfig twin (the event-vs
    campaigns) simply stays None and prints as `?`.
    """
    if not GAMECONFIG.exists():
        return {}
    camps = json.load(open(GAMECONFIG))["clientGameConfig"]["battles"]["campaigns"]
    return {(c["id"], b["battleId"]): b.get("xp")
            for group in camps.values() for c in group for b in c["battles"]}


def nodes(event, att, xp_of):
    """One row per node whose kills score: {campaign, id, kills, foes, xp, ...}."""
    score = countable(npc_traits(), event)
    rates = sorted(kill_rates(EVENTS[event]["tier"]))
    elite = rates[-1]                       # the higher rate IS the elite one
    rows = []
    for f in sorted((DATA / "campaign-battles").glob("*.json")):
        camp = f.stem[len("campaign-battles-"):]
        doc = json.load(open(f))
        for b in doc["battles"]:
            raw = b.get("rawEnemyTypes") or [
                {"id": e["id"], "count": e["count"]}
                for e in b.get("detailedEnemyTypes", [])
            ]
            k = sum(e["count"] for e in raw if e["id"].split(":")[0] in score)
            if not k:
                continue
            rew = b.get("rewards") or {}
            drops = list(dict.fromkeys(  # same leaf can sit in both reward lists
                p["id"]
                for side in ("potential", "guaranteed")
                for p in (rew.get(side) or [])
                if str(p.get("id", "")).startswith("upg")
            ))
            rows.append({
                "campaign": camp, "id": b["id"], "type": b.get("type", ""),
                "energy": b["energyCost"], "kills": k,
                "foes": sum(e["count"] for e in raw), "drops": drops,
                "xp": xp_of.get((doc.get("groupId"), f"{b['nodeNumber']:02d}")),
                "pts": (elite if b.get("type") in ("Elite", "EliteMirror")
                        else rates[0]) * k,
                "att": att.get((doc.get("groupId"), b["nodeNumber"] - 1)),
            })
    return rows


def xp_gaps():
    """hero name -> xp still needed, for the heroes the XP gate blocks."""
    r = subprocess.run(
        [sys.executable, str(HERE / "xp_gate.py"), "--only", "blocked", "--json"],
        capture_output=True, text=True)
    if r.returncode:
        return {}
    return {row["name"]: row["gap_xp"] for row in json.loads(r.stdout)["rows"]}


def wanted():
    """leaf id -> {hero name: still-to-farm count} across every next rung."""
    r = subprocess.run(
        [sys.executable, str(HERE / "rank_up_report.py"), "--energy", "--json"],
        capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"rank_up_report --energy failed:\n{r.stderr.strip()}")
    want = {}
    for row in json.loads(r.stdout)["rows"]:
        td = row.get("tier_detail") or []
        for leaf, info in ((td[0].get("leaves") if td else None) or {}).items():
            if info.get("need", 0) > 0:
                want.setdefault(leaf, {})[row["name"]] = info["need"]
    return want


def selftest():
    att = attempts()
    xp_of = battle_xp()
    assert len(xp_of) > 1300, len(xp_of)
    now = datetime.now(timezone.utc)
    mech = nodes("machine-hunt", att, xp_of)
    rush = nodes("training-rush", att, xp_of)
    assert len(mech) >= 250, len(mech)
    # training rush scores every kill, so it can only ever have more nodes
    assert len(rush) > len(mech)
    assert len(countable(npc_traits(), "machine-hunt")) >= 90
    for rows in (mech, rush):
        assert all(0 < r["kills"] <= r["foes"] and r["energy"] >= 0 for r in rows)
        assert all(r["pts"] > 0 for r in rows)
    # the 5-pt rate is elite-only, the 3-pt rate the rest
    for r in rush:
        per = r["pts"] / r["kills"]
        assert per == (5 if r["type"] in ("Elite", "EliteMirror") else 3), r
    assert len(att) >= 500 and all(v >= 0 for v in att.values())
    # permanent campaigns resolve attempts AND xp; event campaigns resolve neither
    perm = [r for r in rush if r["campaign"] in ("indomitus", "octarius")]
    assert perm and all(r["att"] is not None and r["xp"] for r in perm)
    assert all(r["xp"] is None for r in rush if "vs-" in r["campaign"])
    indo = [r for r in mech if r["campaign"] == "indomitus"]
    assert any(r["kills"] == r["foes"] for r in indo)  # pure-Necron nodes
    for name in EVENTS:
        rates = kill_rates(EVENTS[name]["tier"])
        assert rates == {3, 5}, (name, rates)
        assert gold_per_point(EVENTS[name]["tier"])
        assert reward_ladder(EVENTS[name]["progress"]), name
    assert xs_mult(EVENTS["training-rush"]["tier"])["Mythic"] == 5.0
    assert xs_mult(EVENTS["machine-hunt"]["tier"]) == {}
    assert event_window(now, "training-rush") == event_window(now, "training-rush")
    for e in event_list(now):
        assert set(e) == {"id", "start", "end", "live"} and e["id"]
    w = wanted()
    assert w and all(sum(v.values()) > 0 for v in w.values())
    cat = energy_cost.load_upgrades()
    assert all(i in cat and cat[i].get("label") for i in w)  # every wanted leaf is named
    print(f"selftest OK: {len(mech)} machine-hunt / {len(rush)} training-rush nodes, "
          f"{len(xp_of)} xp rows, {len(att)} attempt slots, {len(w)} wanted leaves")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("campaign", nargs="?", help="filter: substring of campaign id (e.g. indomitus)")
    ap.add_argument("--items", action="store_true",
                    help="balance: factor in items missing for next rank-ups")
    ap.add_argument("--all", action="store_true",
                    help="also show nodes with 0 attempts left today")
    ap.add_argument("--top", type=int, default=15)
    ap.add_argument("--event", choices=sorted(EVENTS) + ["auto"], default="auto",
                    help="which event to score (default: whichever is live)")
    ap.add_argument("--track", action="store_true",
                    help="print the reward track and the gold trade-in (needs gameconfig)")
    ap.add_argument("--xp-needed", action="store_true",
                    help="XP mode: rank by xp/E instead of Pt/E, with your blocked heroes' gaps")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        return 0

    now = datetime.now(timezone.utc)
    event = a.event if a.event != "auto" else live_event(now)
    ev = EVENTS[event]
    xs = xs_mult(ev["tier"])
    rows = nodes(event, attempts(), battle_xp() if xs else {})
    ends = event_window(now, event)
    if a.campaign:
        rows = [r for r in rows if a.campaign.lower() in r["campaign"]]
    if not a.all:
        rows = [r for r in rows if r["att"] != 0]  # exhausted today
    want = wanted() if a.items else {}
    label = ({i: v.get("label", i) for i, v in energy_cost.load_upgrades().items()}
             if a.items else {})

    # training rush pays XP on top of points, so sort on whichever the user asked
    # for; every other key is identical, so both modes stay a stable re-ranking.
    for r in rows:
        r["xp_per_e"] = (None if r["xp"] is None or r["energy"] == 0
                         else r["xp"] / r["energy"])
        r["pt_per_e"] = float("inf") if r["energy"] == 0 else r["pts"] / r["energy"]
        hits = [d for d in r["drops"] if d in want]
        r["want"] = [
            {"id": d, "name": label.get(d, d),
             "heroes": dict(sorted(want[d].items(), key=lambda kv: -kv[1]))}
            for d in hits]
        r["need"] = sum(sum(want[d].values()) for d in hits)
    # balance mode: dual-purpose first, then the reward rate, then need; ties -> more attempts left.
    # reverse=True, so an unknown rate must sink: negate the value (bigger = better) and
    # give the None rows a rate of -inf so nothing outranks them.
    rate = ((lambda r: (float("-inf") if r["xp_per_e"] is None
                        else r["xp_per_e"], False))
            if a.xp_needed else (lambda r: (r["pt_per_e"], False)))
    rows.sort(key=lambda r: (bool(r["want"]), *rate(r), r["need"],
                             r["att"] if r["att"] is not None else 99, r["kills"]),
              reverse=True)
    rows = rows[:a.top]

    if a.json:
        out = [{**r,
                "pt_per_e": None if r["pt_per_e"] == float("inf") else r["pt_per_e"]}
               for r in rows]  # 0-energy = null (JSON has no Infinity)
        doc = {"schema_version": 1, "event": event, "event_ends": ends.isoformat() if ends else None,
               "events": event_list(now), "rows": out}
        if xs:
            doc["xp_multipliers"] = xs
        if a.track:
            doc["reward_track"] = [{"points": p, "reward": r, "endless": e}
                                   for p, r, e in reward_ladder(ev["progress"])]
            doc["gold_per_point"] = gold_per_point(ev["tier"])
        if a.xp_needed:
            doc["xp_gaps"] = xp_gaps()
        print(json.dumps(doc, indent=1))
        return 0
    if ends:
        left = ends - now
        print(f"{ev['label']} LIVE — ends {ends:%Y-%m-%d %H:%M} UTC "
              f"({left.days}d {left.seconds // 3600}h left)")
    who = ("Mechanical kills" if ev["needs"]
           else f"Kills (skip {'/'.join(sorted(ev['skip']))})")
    sortnote = ("xp/E" if a.xp_needed else "Pt/E")
    print(f"{who} per energy — {sortnote} first; "
          f"3 pts/kill std+mirror, 5 elite; Att = attempts left today"
          + (f"; hero XP x{min(xs.values()):.1f}-{max(xs.values()):.1f} by rarity "
             "(XP/E is pre-multiplier)" if xs else "")
          + (" — Want = drops for next rank-ups" if a.items else ""))
    hdr = f"{'Pt/E':>6} {'Node':<8}"
    if xs:
        hdr += f" {'XP/E':>6}"
    hdr += f" {'Pts':>4} {'Kills':>5} {'Foes':>4} {'E':>4} {'Att':>4}"
    if a.items:
        hdr += f" {'Want':>4} {'Need':>4}"
    print(hdr + f" {'Type':<12} Campaign")
    for r in rows:
        prate = "inf" if r["pt_per_e"] == float("inf") else f"{r['pt_per_e']:.1f}"
        line = f"{prate:>6} {r['id']:<8}"
        if xs:
            line += f" {r['xp_per_e']:>6.1f}" if r["xp_per_e"] is not None else f" {'?':>6}"
        line += (f" {r['pts']:>4} {r['kills']:>5} {r['foes']:>4} {r['energy']:>4} "
                 f"{str(r['att'] if r['att'] is not None else '?'):>4}")
        if a.items:
            line += f" {len(r['want']):>4} {r['need']:>4}"
        print(f"{line} {r['type']:<12} {r['campaign']}")
        if a.items and r["want"]:
            detail = "; ".join(
                f"{w['name']} [{w['id']}] — "
                + ", ".join(f"{h} {n}" for h, n in w["heroes"].items())
                for w in r["want"])
            print(f"{'':>16} ^ {detail}")
    if a.xp_needed:
        gaps = xp_gaps()
        if gaps:
            print("XP-blocked heroes: " + ", ".join(
                f"{h} {n:,}" for h, n in sorted(gaps.items(), key=lambda kv: -kv[1])))
        else:
            print("No XP-blocked heroes — XP farming buys nothing this event.")
    gpp = gold_per_point(ev["tier"])
    if a.track:
        ladder = reward_ladder(ev["progress"])
        if ladder:
            print("Track: " + ", ".join(
                f"{p:,}{'+' if e else ''} {r}" for p, r, e in ladder))
        else:
            print("Track: unavailable (needs research/datamine/gameconfig142.json)")
    if gpp:
        print(f"leftover points trade in for {gpp} gold each")
    if a.items and any(r["want"] for r in rows):
        # totals over DISTINCT items shown (a leaf dropped by several nodes counts once)
        ids = list({w["id"] for r in rows for w in r["want"]})
        sums = {i: sum(want[i].values()) for i in ids}
        by_item = sorted(((label.get(i, i), s) for i, s in sums.items()),
                         key=lambda kv: (-kv[1], kv[0]))
        by_hero = {}
        for i in ids:
            for h, n in want[i].items():
                by_hero[h] = by_hero.get(h, 0) + n
        heroes = sorted(by_hero.items(), key=lambda kv: (-kv[1], kv[0]))
        print(f"Need total {sum(sums.values())}: "
              + ", ".join(f"{n} {c}" for n, c in by_item))
        more = f" (+{len(heroes) - 8} more)" if len(heroes) > 8 else ""
        print("By hero: " + ", ".join(f"{h} {n}" for h, n in heroes[:8]) + more)
    return 0


if __name__ == "__main__":
    run(main)
