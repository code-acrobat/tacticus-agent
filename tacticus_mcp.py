#!/usr/bin/env python3
"""Thin MCP adapter over the Tacticus CLI tools.

stdio transport, JSON-RPC 2.0, Python stdlib only - no dependencies.

Design rule (AGENTS.md "Architecture"): this file is a MARSHALLING LAYER
ONLY. Every tool shells out to the existing CLI with --json and returns its
stdout verbatim as text; zero pricing logic is duplicated here, so the CLI
contract (schema_version 1, {schema_version, rows} for list-shaped output)
stays the single source of truth. Resources serve the two hand-tunable
config JSON files as text.

Register as a local server (project opencode.json):

    "mcp": { "servers": { "tacticus": {
        "type": "local",
        "command": ["python3", "/home/user/workspace/tacticus/tacticus_mcp.py"]
    } } }

Protocol surface: initialize, ping, tools/list, tools/call, resources/list,
resources/read (plus empty prompts/resources-templates answers for fussy
clients). Unknown requests -> -32601; notifications are not answered.
Logs go to stderr - stdout belongs to the protocol.

Run standalone for a smoke test:
    python3 tacticus_mcp.py            # speaks JSON-RPC on stdin/stdout
"""
import json
import pathlib
import subprocess
import sys

BASE = pathlib.Path(__file__).resolve().parent
SERVER_NAME = "tacticus"
VERSION = "1.5.0"
PROTOCOL_FALLBACK = "2024-11-05"   # classic initialize handshake
RUN_TIMEOUT = 120                  # seconds; the report itself runs in ~0.05 s

TOOLS = [
    {
        "name": "rank_up_report",
        "title": "Rank-up proximity report",
        "description": (
            "Characters ranked by how fast they can reach their next rank-up, "
            "priced in energy (mercy-baked drop rates, recipes, inventory "
            "deducted). Always runs with --energy; pass estimate=true for the "
            "rarity shortcut when research/ is unavailable. Returns the CLI's "
            "--json payload verbatim: {schema_version, rows:[...]}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "top": {"type": "integer", "minimum": 1,
                        "description": "only the N closest (default: all)"},
                "sort": {"type": "string",
                         "enum": ["missing", "rank", "energy", "tier"],
                         "description": "missing = fewest items (default); "
                                        "energy = cheapest rank-up; tier = "
                                        "cheapest whole-tier climb"},
                "estimate": {"type": "boolean",
                             "description": "rarity-inference shortcut instead "
                                            "of exact catalog pricing"},
                "tier": {"type": "string",
                         "description": "farm tier for --estimate (auto|Standard|"
                                        "Mirror|Elite|...); auto = cheapest"},
                "include_events": {"type": "boolean",
                                   "description": "allow Extremis (timed event "
                                                  "campaigns) in tier auto"},
                "item_rarity": {"type": "string",
                                "description": "force one item rarity for every "
                                               "row (estimate mode)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "item_value",
        "title": "Per-item shop deal check",
        "description": (
            "One item: name or id (partial ok, '_' matches a space), its flat "
            "component list, the leaf farm table, and - with price_bs - the "
            "6-step buy verdict against the full-craft fair floor. Omit query "
            "for the whole 557-row dictionary (559 lines of text - prefer a "
            "query, or parse --json output). Detail returns {schema_version, "
            "...row, price_bs, ratio, grade}; the dictionary returns "
            "{schema_version, rows:[...]}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "item name or id; omit for the full "
                                         "dictionary"},
                "price_bs": {"type": "number", "minimum": 0,
                             "description": "blackstone offer to judge"},
                "qty": {"type": "integer", "minimum": 1,
                        "description": "how many the offer buys (default 1)"},
                "spender": {"type": "string",
                            "enum": ["frugal", "moderate", "generous"],
                            "description": "verdict profile (default from "
                                           "energy.spender in the drop-rates "
                                           "config)"},
                "threshold": {"type": "number", "minimum": 1,
                              "description": "custom verdict ceiling in x-floor; "
                                             "overrides spender"},
                "rarity": {"type": "string",
                           "description": "disambiguate matches "
                                          "(Common..Mythic)"},
                "stat": {"type": "string",
                         "description": "disambiguate matches "
                                        "(Health|Damage|Armour)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "xp_gate",
        "title": "Hero-XP gate for rank-ups",
        "description": (
            "The XP half of a rank-up (items are rank_up_report's half, and "
            "this never prices them): per character, the open grid cells vs "
            "the per-rank XP requirement matrix, the XP gap to the hardest "
            "one, and a "
            "book plan from the shared inventory stock plus the guild-shop "
            "offers unlocked at this power level. Statuses: xp-blocked (a "
            "book fixes it), maxed (the rarity maxXpLevel cap is below what "
            "the grid needs - no gap, no book list), xp-ok (XP is fine, "
            "items are the blocker), grid-full. Returns {schema_version, "
            "meta, rows:[...]}. Needs research/ for the XP tables."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "only": {"type": "string",
                         "enum": ["blocked", "maxed", "ok", "full"],
                         "description": "just one status group (default: all "
                                        "four, gap-ascending inside each)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "gear_report",
        "title": "Gear parity report",
        "description": (
            "Equipment parity, not rank-ups: per hero the 3 gear slots vs a "
            "target rarity (default Legendary), green = a hero at/above the "
            "target still wearing something below it (computed, no server "
            "flag), a greedy fill plan from the shared spare pool (worn "
            "pieces are per-unit copies - not movable), buy lines for the "
            "rest, and a Relic column (worn relics + relic spares). Detail "
            "mode also tags each piece's variant (crit gun/knife, hp+armour "
            "vs pure armour, block chance) and prints a per-hero community "
            "preference. Pass query for one hero (partial name/id, '_' = "
            "space); omit for the roster table. Returns {schema_version, "
            "meta, plan, rows:[...]}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "character name or id (partial ok); "
                                         "omit for the full roster table"},
                "target": {"type": "string",
                           "enum": ["Common", "Uncommon", "Rare", "Epic",
                                    "Legendary", "Mythic"],
                           "description": "parity target rarity "
                                          "(default Legendary)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ability_gate",
        "title": "Ability / badge gate",
        "description": (
            "The badge half of an ability upgrade (never prices items or XP): "
            "per hero, the next level of each ability costs gold + alliance "
            "badges, and a hero is badge-blocked only when a rarity it needs "
            "sits in a short pool - badge stock is ONE shared pool per "
            "alliance, so the block is aggregate. Header carries the stock and "
            "the roster-wide short pools; footer carries full-roster counts "
            "and the gold total (gold never gates - no balance in the "
            "snapshot). Pass query for one hero (partial name/id, '_' = "
            "space) to get its per-level costs with [pool ... SHORT] markers; "
            "omit for the grouped roster. Returns {schema_version, meta, "
            "rows:[...]}, detail returns {schema_version, meta, unit, pools}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "character name or id (partial ok); "
                                         "omit for the full roster view"},
                "only": {"type": "string",
                         "enum": ["blocked", "capped", "ok"],
                         "description": "just one status group (default: all, "
                                        "blocked first)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "power_delta",
        "title": "Power gained by the next rank-up",
        "description": (
            "Power bought by the next promotion, so advice can weigh power "
            "per resource instead of cheapest-first. Uses the Tacticus "
            "Planner formula as a proxy (the in-game formula is unpublished): "
            "per hero Power now, +Rung, the grid grants (+Hp/+Dmg/+Arm) and "
            "+END for a terminal rank. Pass query for one hero (partial "
            "name/id, '_' = space): --json then returns that hero's single "
            "row carrying every number the breakdown prints, while the text "
            "view prints the step-by-step formula. Omit for the roster "
            "sorted by +Rung. Returns {schema_version, meta, rows:[...]}. "
            "Correlate with rank_up_report's energy column outside this tool."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "character name or id (partial ok); "
                                         "omit for the full roster"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "team_roster",
        "title": "Curated team comp fieldability",
        "description": (
            "Which of the 7 curated community guild-raid comps your roster "
            "can field (terminus-maximus + cognitae, via the planner): per "
            "comp the signature/core/flex/MoW ownership, a 5-hero pool "
            "count, and a fieldable / no signature / thin verdict with the "
            "missing heroes. Deliberately computes NO synergy score - none "
            "exists in the game config. A signature may be a machine of "
            "war; MoW count never gates the verdict. Pass query for one "
            "comp (partial id, non-alnum stripped: 'multi', 'zkar'); omit "
            "for all 7. Returns {schema_version, meta, rows:[...]}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string",
                          "description": "comp id fragment; omit for all "
                                         "seven comps"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "machine_hunt",
        "title": "Machine-hunt event farming",
        "description": (
            "Where do Mechanical enemies die cheapest? Campaign nodes "
            "ranked by event points per energy (3 pts Standard/Mirror, 5 "
            "Elite), your attempt count per node (exhausted hidden unless "
            "all=true), and the live/upcoming event window. items=true also "
            "flags nodes that drop items your next rank-ups need, with a "
            "footer totalling the need per item and per top hero. campaign "
            "filters by substring of the campaign id ('indomitus'). Returns "
            "{schema_version, event_ends, events, rows:[...]} - rows carry "
            "pt_per_e, pts, att, want, need. next_step convenes this as its "
            "advisory 'event' witness (schedule awareness only)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "campaign": {"type": "string",
                             "description": "substring of the campaign id "
                                            "(e.g. 'indomitus')"},
                "top": {"type": "integer", "minimum": 1,
                        "description": "rows to show (default 15)"},
                "all": {"type": "boolean",
                        "description": "also show nodes whose attempts are "
                                       "exhausted"},
                "items": {"type": "boolean",
                          "description": "flag nodes dropping items the next "
                                         "rank-ups need (+ footer totals)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "next_step",
        "title": "Assembler: where am I blocked, what next",
        "description": (
            "The assembler: convenes seven witnesses fresh (rank_up_report "
            "--energy, xp_gate, ability_gate, power_delta, gear_report, "
            "team_roster, machine_hunt as the advisory event witness) into "
            "one brief - WHERE I'M BLOCKED (per-gate "
            "counts with hero examples) and WHAT TO DO NEXT in leverage "
            "order (shared-pool badge purchases -> apply books in stock -> "
            "farm by power-per-energy -> gear buys -> unlocks) plus a NEXT "
            "chain line. A gate stops ONE activity: badges block "
            "abilities only (badge-blocked heroes stay in the farm list), "
            "XP/MAXED/ITEMS block rank-ups, gear/team are buys/unlocks. "
            "Free rungs (energy 0) sort first. Zero pricing logic of its "
            "own; ~1.1 s runtime; a failing team witness degrades with a "
            "WARNINGS section instead of dying. Returns {schema_version, "
            "meta, gates, blocked, purchases, books, farm, gear, team}."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "top": {"type": "integer", "minimum": 1,
                        "description": "farm rows to show (default 10)"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "player_refresh",
        "title": "Refresh the roster snapshot",
        "description": (
            "Re-fetch the player snapshot from the (read-only) Tacticus API "
            "into tacticus-player.json and return the CLI summary text. This "
            "writes only the local cache - the API exposes 4 GET endpoints and "
            "accepts no mutations. Subsequent report/item_value calls price "
            "against the fresh inventory."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "quiet": {"type": "boolean",
                          "description": "suppress the CLI summary"},
            },
            "additionalProperties": False,
        },
    },
]

RESOURCES = [
    {
        "uri": "tacticus://config/drop-rates",
        "name": "tacticus-drop-rates.json",
        "title": "Economy config",
        "description": "Hand-tunable drop rates, energy budget (638/day), "
                       "spender profiles, rank_item_rarity inference table.",
        "mimeType": "application/json",
        "path": "tacticus-drop-rates.json",
    },
    {
        "uri": "tacticus://config/shop-prices",
        "name": "tacticus-shop-prices.json",
        "title": "Shop price reference",
        "description": "Daily Deals ladders (chest vs chosen-single), "
                       "typical_single_bs, requisition EV, blackstone income.",
        "mimeType": "application/json",
        "path": "tacticus-shop-prices.json",
    },
]
BY_URI = {r["uri"]: r for r in RESOURCES}

INSTRUCTIONS = (
    "Thin adapter over the local Tacticus CLI tools. Every tool returns the "
    "CLI's --json output verbatim as TEXT - parse the JSON out of it "
    "(schema_version 1; list shapes are {schema_version, rows}). Exact "
    "pricing needs research/ (present in this repo). For a single item pass "
    "query; omitting it returns the whole 557-row dictionary. Verdicts are "
    "signals, not mandatory buys. xp_gate covers the hero-XP half of a "
    "rank-up (its stock of books is ONE shared pool across plans). "
    "gear_report covers equipment parity: spares are ONE shared pool and "
    "worn gear cannot be moved between heroes. ability_gate covers the "
    "badge half of ability upgrades (badge stock is ONE shared pool per "
    "alliance, so a block is roster-wide). power_delta gives the power "
    "gained by the next rank-up as a planner-proxy, not the in-game score. "
    "next_step is the ASSEMBLER: one brief answering where you are blocked "
    "and what to do next (seven witnesses, one run, ~1.1 s) - start there "
    "when the question spans gates. team_roster checks the curated "
    "guild-raid comps (fieldability only, no synergy score exists in "
    "config). machine_hunt ranks campaign nodes by event points per energy "
    "with your attempt counts - the 'where do I hunt next?' answer."
)


# --------------------------------------------------------------------------
# tool execution - subprocess the CLI, return stdout verbatim
# --------------------------------------------------------------------------
def build_argv(name, args):
    if name == "rank_up_report":
        argv = [sys.executable, str(BASE / "rank_up_report.py"),
                "--energy", "--json"]
        if args.get("estimate"):
            argv.append("--estimate")
        if args.get("tier"):
            argv += ["--tier", str(args["tier"])]
        if args.get("include_events"):
            argv.append("--include-events")
        if args.get("item_rarity"):
            argv += ["--item-rarity", str(args["item_rarity"])]
        if args.get("top"):
            argv += ["--top", str(int(args["top"]))]
        if args.get("sort"):
            argv += ["--sort", str(args["sort"])]
        return argv
    if name == "item_value":
        argv = [sys.executable, str(BASE / "item_value.py"), "--json"]
        query = args.get("query")
        if query:
            argv.append(str(query))
        for flag, key in (("--price", "price_bs"), ("--qty", "qty"),
                          ("--spender", "spender"), ("--threshold", "threshold"),
                          ("--rarity", "rarity"), ("--stat", "stat")):
            if args.get(key) is not None:
                argv += [flag, str(args[key])]
        return argv
    if name == "xp_gate":
        argv = [sys.executable, str(BASE / "xp_gate.py"), "--json"]
        if args.get("only"):
            argv += ["--only", str(args["only"])]
        return argv
    if name == "gear_report":
        argv = [sys.executable, str(BASE / "gear_report.py"), "--json"]
        if args.get("target"):
            argv += ["--target", str(args["target"])]
        if args.get("query"):
            argv.append(str(args["query"]))
        return argv
    if name == "ability_gate":
        argv = [sys.executable, str(BASE / "ability_gate.py"), "--json"]
        if args.get("only"):
            argv += ["--only", str(args["only"])]
        if args.get("query"):
            argv.append(str(args["query"]))
        return argv
    if name == "power_delta":
        argv = [sys.executable, str(BASE / "power_delta.py"), "--json"]
        if args.get("query"):
            argv.append(str(args["query"]))
        return argv
    if name == "team_roster":
        argv = [sys.executable, str(BASE / "team_roster.py")]
        if args.get("query"):
            argv.append(str(args["query"]))
        argv.append("--json")
        return argv
    if name == "machine_hunt":
        argv = [sys.executable, str(BASE / "machine_hunt.py"), "--json"]
        if args.get("campaign"):
            argv.append(str(args["campaign"]))
        if args.get("items"):
            argv.append("--items")
        if args.get("all"):
            argv.append("--all")
        if args.get("top"):
            argv += ["--top", str(int(args["top"]))]
        return argv
    if name == "next_step":
        argv = [sys.executable, str(BASE / "next_step.py")]
        if args.get("top"):
            argv += ["--top", str(int(args["top"]))]
        argv.append("--json")
        return argv
    if name == "player_refresh":
        argv = [sys.executable, str(BASE / "update_player.py")]
        if args.get("quiet"):
            argv.append("-q")
        return argv
    raise ValueError(f"unknown tool {name!r}")


def call_tool(name, args):
    try:
        proc = subprocess.run(build_argv(name, args), cwd=str(BASE),
                              capture_output=True, text=True,
                              timeout=RUN_TIMEOUT)
    except subprocess.TimeoutExpired:
        return {"content": [{"type": "text",
                             "text": f"{name} timed out after {RUN_TIMEOUT}s"}],
                "isError": True}
    except ValueError as exc:
        return {"content": [{"type": "text", "text": str(exc)}],
                "isError": True}
    out = (proc.stdout or "").rstrip("\n")
    if proc.returncode != 0:
        err = (proc.stderr or "").strip() or out or f"exit {proc.returncode}"
        return {"content": [{"type": "text", "text": err}], "isError": True}
    return {"content": [{"type": "text", "text": out}]}


# --------------------------------------------------------------------------
# JSON-RPC dispatch
# --------------------------------------------------------------------------
def rpc_result(mid, result):
    return {"jsonrpc": "2.0", "id": mid, "result": result}


def rpc_error(mid, code, message):
    return {"jsonrpc": "2.0", "id": mid,
            "error": {"code": code, "message": message}}


def handle(req):
    method, mid = req.get("method"), req.get("id")
    params = req.get("params") or {}

    if method == "initialize":
        return rpc_result(mid, {
            "protocolVersion": params.get("protocolVersion") or PROTOCOL_FALLBACK,
            "capabilities": {"tools": {"listChanged": False},
                             "resources": {}},
            "serverInfo": {"name": SERVER_NAME,
                           "title": "Tacticus rank-up & shop tools",
                           "version": VERSION},
            "instructions": INSTRUCTIONS,
        })
    if method == "ping":
        return rpc_result(mid, {})
    if method == "tools/list":
        tools = [{k: v for k, v in t.items() if k != "path"} for t in TOOLS]
        return rpc_result(mid, {"tools": tools})
    if method == "tools/call":
        name = params.get("name")
        if name not in {t["name"] for t in TOOLS}:
            return rpc_error(mid, -32602, f"unknown tool: {name!r}")
        try:
            result = call_tool(name, params.get("arguments") or {})
        except Exception as exc:                       # never kill the loop
            print(f"tool {name} failed: {exc!r}", file=sys.stderr)
            result = {"content": [{"type": "text", "text": repr(exc)}],
                      "isError": True}
        return rpc_result(mid, result)
    if method == "resources/list":
        return rpc_result(mid, {"resources": [
            {k: v for k, v in r.items() if k != "path"} for r in RESOURCES]})
    if method == "resources/read":
        res = BY_URI.get(params.get("uri"))
        if res is None:
            return rpc_error(mid, -32602, f"unknown resource: {params.get('uri')!r}")
        try:
            text = (BASE / res["path"]).read_text(encoding="utf-8")
        except OSError as exc:
            return rpc_error(mid, -32603, f"cannot read {res['path']}: {exc}")
        return rpc_result(mid, {"contents": [{"uri": res["uri"],
                                              "mimeType": res["mimeType"],
                                              "text": text}]})
    if method in ("prompts/list", "resources/templates/list"):
        return rpc_result(mid, {"prompts": []} if method == "prompts/list"
                           else {"resourceTemplates": []})
    if mid is None:                      # notifications/cancelled etc.
        return None
    return rpc_error(mid, -32601, f"method not found: {method!r}")


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except json.JSONDecodeError as exc:
            send(rpc_error(None, -32700, f"parse error: {exc}"))
            continue
        try:
            resp = handle(req)
        except Exception as exc:                       # never kill the loop
            print(f"dispatch failed: {exc!r}", file=sys.stderr)
            resp = rpc_error(req.get("id"), -32603, repr(exc))
        if resp is not None:
            try:
                send(resp)
            except BrokenPipeError:
                return


if __name__ == "__main__":
    try:
        main()
    except (BrokenPipeError, KeyboardInterrupt):
        pass
