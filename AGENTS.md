# AGENTS.md

Operational context for AI agents working in `/home/user/workspace`.

## Goal

Give the user an interactive Swagger UI for the Tacticus game API
(`https://api.tacticusgame.com/api-docs`) that can execute real requests with
their API key. `README.md` is the human-facing version — keep the two in sync
when behaviour changes.

## Current state

- **UI: `http://127.0.0.1:8124/`** — served by `tacticus_proxy.py`, managed with
  `./proxy_ctl.sh {up|down|restart|status}`. It is a background process
  (`setsid nohup`) that survives the shell that spawned it, so it is **not
  guaranteed to be running**: check `./proxy_ctl.sh status` (exit 0 = up and
  healthy). It was last deliberately **stopped** in this session.
- Port **8124** is the proxy (UI + API calls). Port 8123 is dead — an earlier
  plain `python3 -m http.server` that was replaced.
- Verified end-to-end via curl (see "Verification"): preflight `204`, proxied
  `GET /api/v1/player` `200` with CORS headers and a valid body.

## Architecture — do not break these invariants

1. **The browser must never call `api.tacticusgame.com` directly.** The API
   sends no `Access-Control-Allow-Origin` and answers `OPTIONS` with `403`, so
   any direct call fails as *"Failed to fetch"*. All UI traffic goes through
   the proxy.
2. **`spec.servers` in `swagger-ui.html` must be `http://127.0.0.1:8124`**, not
   the real host. A relative `"/"` server resolves against the page origin
   (this was the original localhost-404 bug); the real host triggers CORS (the
   second bug).
3. **The `requestInterceptor` in `gen_swagger.py` is a safety net**, not the
   primary fix — it re-forces any foreign origin onto the proxy and injects
   `X-API-KEY` when absent. Don't point it at `api.tacticusgame.com`.
4. **`swagger-ui.html` is generated.** Edit `gen_swagger.py` and rerun
   `python3 gen_swagger.py`. Hand-edits are lost.
5. **`tacticus_mcp.py` is a marshalling layer, never a second
   implementation.** It subprocesses the CLIs and returns their `--json`
   stdout verbatim as MCP tools (`rank_up_report`, `item_value`, `xp_gate`,
   `gear_report`, `ability_gate`, `power_delta`, `team_roster`, `next_step`,
   `player_refresh`) plus config resources (`tacticus://config/drop-rates`,
   `tacticus://config/shop-prices`). Never copy pricing logic into it — the
   CLI `--json` is the contract (`{"schema_version": 1, "rows": [...]}` for
   list shapes, a flat object with `schema_version` for item detail).
6. **One concern per tool — the caller does the dance.** Standalone tools stay
   small and each expose `--json`; correlation across concepts (items × XP ×
   ascension × value) happens outside them — agent, MCP, or
   **`next_step.py`** (the aggregator that convenes the witnesses itself) —
   not by growing a single tool. This was the original design ask
   behind the thin MCP layer: powerful standalone tools, loosely joined.
 7. **A new expert tool must be considered for the aggregator in the same
    change.** When a new single-concern `--json` witness lands, that change
    must also decide whether `next_step.py` should convene it: add it to
    `WITNESSES` + the merge + both docs, or record here why not (e.g.
    `item_value.py` is a per-item lookup, not a roster gate — deliberately
    not a witness; `machine_hunt.py` is convened only as the advisory
    `event` witness for schedule/strategy awareness — never its node rows,
    still not a roster gate). The aggregator must never silently fall behind the
    witnesses it summarizes.

## Files

| File | Role |
|---|---|
| `gen_swagger.py` | Source of truth for the page. Reads `tacticus-openapi.json` + `.tacticus_api_key`, writes `swagger-ui.html`. |
| `swagger-ui.html` | Generated output (Swagger UI 5.11.0 via unpkg, spec embedded inline, key embedded). |
| `tacticus_proxy.py` | Server: serves the page at `/`, forwards everything else upstream, adds CORS, answers `OPTIONS` with `204`. Python stdlib only — no dependencies. |
| `proxy_ctl.sh` | Lifecycle wrapper: `up`, `down`, `restart`, `status` (default `status`). Health-checks `http://127.0.0.1:8124/`, replaces stale processes, tails the log on failure. Exit 0 = up. |
| `tacticus-openapi.json` | OpenAPI 3.0.1 spec, 4 GET paths. Refresh: `curl -sS -o tacticus-openapi.json https://api.tacticusgame.com/api-docs` |
| `tacticus-player.json` | Cached sample `GET /api/v1/player` response (246 KB). Local-only — gitignored (roster data), purged from history 2026-10-03. |
| `update_player.py` | Refreshes that cache: reads the key, fetches, atomic write, prints a summary. `--pretty`, `-o`, `--url`, `-q`. |
| `rank_up_report.py` | Characters sorted by items still missing for the next rank. Model: ladder Stone→…→Adamantine, 6-cell 2×3 grid per rank-up, `missing = 6 - len(set(upgrades))`. Flags `--top`, `--sort missing\|rank\|energy\|tier`, `--json` (rows carry both `blocked` = some rung in the tier is unreleased and `next_blocked` = this rung cannot be valued at all — only the latter counts as unpriceable; with `--energy` the payload also carries `contention[]` per over-claimed bank item: `id/name/bank/claims/heroes/free_rungs/tier_claims`, free-rung drains first), `--energy` (exact via `energy_cost.py`; `Energy` col = next rung, `TierE` col = whole tier left, `Days` col = TierE ÷ daily budget from `energy.extra`), `--estimate` (rarity shortcut: `--rates`, `--tier`, `--include-events`, `--item-rarity`). **Excludes the 11 machines of war** (`MOW_IDS`) — no rank grid, they level abilities. Full derivation in README § "Rank-up proximity report". |
| `energy_cost.py` | The exact pricing model behind `--energy`. Reads `research/tacticus-planner-api/` (units → `rankUpUpgrades`, upgrades → `recipe`, campaign-battles → `energyCost`+`rewards`, drop-chances → `effectiveRate`), deducts `player.inventory.upgrades` at every recipe level, then farms each leaf at `energyCost/effectiveRate`. Raises `MissingSource` → the report tells you to pass `--estimate`. |
| `item_value.py` | Per-item shop-deal check. No args = static dictionary of all 557 items (rarity, stat, craft/farm, own, farm energy, fair BS floor, `SHOP`, `VERDICT`, days, note) — pipe to `fzf`; `SHOP` = `typical_single_bs` from the shop-prices file, `VERDICT` = `SHOP` ÷ floor graded on the same scale. With a query (id or label, partial ok, `_` = space): flat component list (item × count, **no recursion** — each component is its own dictionary row), leaf farm table, `--price BS` verdict on a **profile-relative 6-step scale** (STRONG BUY … STRONG AVOID: lower steps AND ceiling move with the profile) vs the **full-craft** floor (inventory ignored — judged against the total price — delta shown separately); profile via `energy.spender` or `--spender`/`--threshold`. Flags `--qty`, `--rarity`, `--stat`, `--json`. |
| `xp_gate.py` | The **XP half** of a rank-up — items are the other half, this never prices them. Per unit: open cells = indices 0..5 not in `upgrades` vs `units.upgradeSlots.xpLevelRequirements[rank]` (row = `rank` field as-is), compared to `xpLevel`; gap = `xpLevels[need−2] − xp`, **clamped by `heroProgressionSteps[].maxXpLevel` → `maxed` prints no gap and no book list** (Calgar needs 65, Mythic caps at 60). Statuses `xp-blocked` / `maxed` / `xp-ok` / `grid-full`; `Books` = owned `inventory.xpBooks` first (one shared pool) then guild-shop offers unlocked at this power level (PL60 = Mythic Grimoire only), apply-gold + guild credits in `--json`. Flags `--only blocked\|maxed\|ok\|full`, `--json`. Needs `research/datamine/gameconfig142.json`; excludes `MOW_IDS`; ~0.2 s. |
| `gear_report.py` | **Gear parity** (equipment, not rank-ups): per hero 3 slots (`units.lineup.<id>.itemSlots` = crit / defensive-or-block / booster), `--target` rarity (default Legendary) with `Green` = the shop's green circle (hero ≥ target but a slot below — no server flag, we compute it). Greedy fill plan from `inventory.items` spares only (**worn pieces are per-unit copies — not movable**), respecting slot type, planner `allowedFactions`/`allowedUnits` and the relic-slot index `itemSlotsRelic`; buy line for the rest (499 BS Daily Deals / 715 crusade). **Relic** column + footer (2 worn: Gulgortz `orksWarboss`, Haarken; 6 spares). Query = one hero detail (partial name/id, `_`=space). Flags `--target`, `--json`; excludes `MOW_IDS`. Needs planner `Data/equipment/*.json` + gameconfig + shop-prices. Detail view tags each piece's **variant** (`variant`: crit gun/knife, hp+armour vs pure armour, block chance line) and prints a per-hero `pref (community)` line from `lineup.<id>.weapons[].hits` + `.traits` (1 hit → knife, 3+ → gun, ActOfFaith → knife, MkXGravis → pure armour). `--json` carries both. |
| `ability_gate.py` | The **badge half** of an ability upgrade (never mentions items/XP). Per unit over both abilities: cost = `units.abilityUpgradeCosts[level−1]` (59 entries, L60 = no entry → `capped`), needs = Counter of rarity→count, gold summed but **never gates** (no gold balance in the snapshot). Statuses `badge-blocked` / `capped` / `ok` — blocked iff a needed rarity sits in a **short pool** (roster-wide demand > `inventory.abilityBadges` stock; the block is AGGREGATE, one shared pool per alliance). Header prints stock + short pools; footer prints full-roster counts (even under `--only`) + gold total + MoW caveat. Query = one hero (partial name/id, `_`=space, exit 1 no match). Flags `--only`, `--json`. Excludes `MOW_IDS` (they use `abilityUpgradeCostsMoW`). Needs `research/datamine/gameconfig142.json`. |
| `power_delta.py` | **Power bought by the next rank-up** — the "power gained" half of power-per-energy advice (correlate with `rank_up_report --energy` outside the tool). Formula = Tacticus Planner `combat-power.ts` verbatim (no in-game formula exists): attr = W_ATTR×stars×(1.25^R + step/9×appliedCells), ability term constant within a rung so `d_power` = round(attr(rank+1,0,stars)) − round(attr(rank,filled,stars)); `d_hp/d_dmg/d_arm` = sum of `units.lineup.<id>.upgradesStatIncrease[rank]` over the remaining empty cells (cols 0,1 / 2,3 / 4,5). Terminal (rank+1 ≥ 20) → `+0 END`, stat grants null. Query = one hero with the full formula breakdown. Flags `--json`. Imports `GRID_SLOTS, MOW_IDS, rank_name` plus the shared `load_player`/`run` entry helpers from rank_up_report; excludes the 11 MoWs. Research: each star = +10% base stats (the "+20% ascension" number is the *ability* rarity jump), rank growth = flat ×1.25 at every tier. |
| `team_roster.py` | **Team comp fieldability** (curated guild-raid comps, not a synergy score): reads planner `Data/guild-raid-meta/guild-raid-comps.json` (7 comps: AdMech, Battlesuits, Custodes, Laviscus, Multi-Hit, Neuro, Z'Kar; `id` + `signatureUnitId` + `coreCharacterIds` + `flexCharacterIds` + `mowIds`, source terminus-maximus+cognitae, updated 2026-09-14) + `Data/units/units-*.json` (names for heroes you do not own; MoW names come from the player file). Per comp: signature owned, core/flex/MoW owned/total, `Heroes` = owned in pool vs 5 slots, verdict `fieldable` / `no signature` / `thin N/5`, `Missing` = deduped unowned signature+core. Signature may be a **MoW** (Z'Kar = `thousDaemonPrince`) — ownership always tested against `units_by_id`, never assumed. MoW count never gates the verdict. **No synergy number exists in config** (no combo/bonus key) — do not add one. Query = partial comp id (non-alnum stripped: `multi`, `zkar`), exit 1 no match; `--json` = `{schema_version, meta{snapshot, player, power_level, source, updated}, rows[17 keys]}`. Imports `MOW_IDS, rank_name` plus the shared `load_player`/`run` entry helpers from rank_up_report; keeps MoWs (they are part of comps). Boss recs (`guild-raid-meta-boss-*.json`) deliberately not read — invariant 6. |
| `next_step.py` | **The aggregator** — answers "where am I blocked, what should I do next?" in one brief. Runs all seven witnesses fresh (`rank_up_report --energy --json`, `xp_gate`, `ability_gate`, `power_delta`, `gear_report`, `team_roster`, `machine_hunt` = the advisory `event` witness), joins by hero (rank rows carry name only → bridge via xp), then prints `EVENTS` (live/upcoming windows from `events[]` — a strategy pointer per live event, or the ad-hoc hook for events without a dedicated report) and `WHERE I'M BLOCKED` (per-gate counts + up to 5 hero examples each, gates flagged `blocks_rankups`) and `WHAT TO DO NEXT` in leverage order: 1 shared-pool badge purchases (demand/stock) → 2 apply XP books (stock first; `books.buyers`/`shop_gc`/`shop_books` name the heroes whose plans need guild-shop purchases — plan-covered ≠ stock-covered) → 3 farm ranked by `d_power/energy` (**energy-0 rungs = `free`, sort first**; rows marked `!` drain contested bank stock — the rank report's `contention`, each farm row carries `contested[]`) → 4 gear buys → 5 unlocks, plus a `NEXT:` chain line. **Gate semantics**: a gate stops one activity — badges block abilities only (badge-blocked heroes stay in the farm list), XP/MAXED/ITEMS block rank-ups, gear/team are buys/unlocks. Purity: zero pricing logic (only arithmetic = `d_power/energy`), reads nothing but witness stdout (no research/, no config). A failing hub witness aborts with every failure listed; a failing `team` or `event` degrades (WARNINGS, exit 0) — `meta.failed_sources` in `--json`. Flags `--top N` (default 10), `--json` = `{schema_version, meta{snapshot, player, power_level, witnesses, top, gated_heroes, roster, badge_gold_total, gear_target, gear_buy_bs, events, failed_sources}, gates[{gate,count,note,blocks_rankups}], blocked[{id,name,rank,gates}], purchases, books, farm, gear, team}`. **Invariant 7**: a new expert tool must be considered for this tool in the same change. |
| `machine_hunt.py` | **Mechanical kills per energy** (machine-hunt event view): planner `npcs/*.json` traits → the Mechanical enemy ids, `campaign-battles/*.json` per-node enemy lists + loot + `energyCost`, player `progress.campaigns` attempts as `Att` (cap 10 std / 6 elite; `0` → hidden unless `--all`; event campaigns aren't in the snapshot → `?`). Ranks by event **points**/E (3 pts per Standard/Mirror kill, 5 per Elite — gameconfig `killUnits` trackers; `Pt/E`/`Pts` columns, `pt_per_e`/`pts` in `--json`) with Node in column 2. `--items` = balance view: nodes whose drops are in the next-rung wanted set (subprocess `rank_up_report --energy --json`; zero pricing logic of its own) + a footer totalling need per distinct leaf and per top hero. Header prints the live event window (planner `events/event-occurrences.json`; `event_ends` + the live/upcoming `events[]` in `--json`). Flags: `campaign` substring, `--top`, `--all`, `--json`, `--selftest`. **Convened by `next_step` as the advisory `event` witness** (schedule/strategy awareness only — `events[]`, never its node rows); **not an MCP tool** (event kill-farming view, not a roster gate — invariant 7 verdict recorded here). |
| `tacticus-drop-rates.json` | The economy config the energy model reads: base drop rates per tier × rarity, Elite guaranteed+overflow rules, energy costs (6/6/6/10/10), regen, **`extra` daily energy sources (ad + free daily-deal crate + the three blackstone bundles = +350/day, so 638/day total behind the `Days` column)**, the `spender` verdict-ceiling profiles (frugal/moderate/generous), campaign list, mercy notes, `rank_item_rarity` inference table, and `unresolved` gaps. **User-supplied and hand-tunable** — this is where new numbers go. |
| `tacticus-shop-prices.json` | Daily Deals / shop price reference (energy refill ladder, per-rarity item prices, chests, requisition, coins, equipment, `fair_price_floor_bs` markup analysis, `typical_single_bs` ladder feeding item_value's `SHOP` column, `blackstone_income` = subscription + promo-code monthly estimates). Two sources reconciled + conflicts flagged. For the buy-vs-farm angle. |
| `tacticus_mcp.py` | Thin MCP adapter (stdio, stdlib only): marshals the CLIs' `--json` as tools (`rank_up_report`, `item_value`, `xp_gate`, `gear_report`, `ability_gate`, `power_delta`, `team_roster`, `next_step`, `player_refresh`) + 2 config resources (`tacticus://config/drop-rates`, `…/shop-prices`). **No pricing logic** — subprocesses the CLI, returns stdout verbatim. Registered in project `opencode.json` → `mcp.servers.tacticus` (`opencode mcp add`); in Code Mode the tools are `tools.tacticus.*` and the result arrives already parsed (no `.content` wrapper). |
| `make_bundle.py` | Browser build: packs `dist/bundle.tar.gz` (gitignored) = all `*.py` + economy configs + planner `Data/**` + gameconfig at repo-root layout (so `BASE = __file__` works after extraction). Asserts **no roster/key file** enters — the visitor's snapshot is supplied at runtime. |
| `browser_harness.py` | Pyodide runner: `bootstrap()` extracts the bundle + writes the visitor snapshot, `install()` shims the 2 `subprocess` call sites (next_step, machine_hunt) to in-process `run_cli()` (argv + captured stdout, mimics `CompletedProcess`). Self-check: `python3 browser_harness.py` must be byte-equal to a real subprocess run of 3 witnesses. **Not a witness** (runner, not a report — invariant 7). |
| `web/index.html` | Pages front end: loads Pyodide (CDN, pinned v0.26.2), bootstraps the harness, fetches the player via the Worker (`POST /player`), renders `next_step --top 10` text as the brief, chat → `POST /chat`. Key lives in sessionStorage only; reports compute in the browser. `WORKER` const is committed = the deployed worker URL (`https://<worker>.<account-subdomain>.workers.dev`) — update it in the same change that renames the worker, then push so Pages rebuilds. |
| `worker/worker.js` | Cloudflare Worker proxy (+ `worker/wrangler.toml`): `/player` = CORS pass-through to the game API (key never stored), `/chat` = LLM call over the precomputed brief (game key never reaches the LLM; secrets `LLM_KEY`, `LLM_MODEL`, optional `LLM_BASE_URL` = OpenAI-compatible default). In-memory 30/min/IP. |
| `.github/workflows/pages.yml` | Pages deploy: re-fetches gitignored `research/` (planner pinned by `PLANNER_SHA` — bump on sync, gameconfig pinned by file), runs `make_bundle.py`, publishes `web/index.html` + bundle + harness. One-time repo setting: Pages Source = "GitHub Actions". |
| `.tacticus_api_key` | Key, `chmod 600`, no trailing newline. Source for key injection. |
| `README.md` | Human docs; update alongside this file. |
| `research/` | **Git-ignored (`research/`)**. Offline copies of every third-party source used for game-mechanics research, moved here from `/tmp` so investigations can resume. See `research/README.md` for provenance and the "what each dir is good for" map. ~74 MB. |

## `research/` — what is in there

Git-ignored, ~74 MB, relocated from `/tmp`. `research/README.md` has the full
provenance table; this is the short map. **Start there before cloning anything
again.**

| Path | What it answers |
|---|---|
| `research/tacticus-planner-api/` | **Clean, strict JSON.** The primary source. `src/TacticusPlanner.GameCatalog/Data/units/units-*.json` → `rankUpUpgrades` = the 6 required item ids for every character × rank (117 × 20); `Data/upgrades/*.json` → 557-item catalog with `rarity`, `stat`, `recipe[]` (the crafting graph); `Data/campaign-battles/*.json` → per-node `energyCost` (6 standard/mirror, 10 elite/eliteMirror) and `rewards.guaranteed` / `rewards.potential[].chanceId`; `Data/drop-chances.json` → 33 rows of rewardKind × difficulty with numerator/denominator/**effectiveRate** (mercy already applied). AGPL-3.0. |
| `research/tacticus-planner-apps/` | Sparse clone. `packages/game-catalog/src/schemas/` holds the zod schemas (incl. `upgrade.ts` with nested recipes) and `dataset-keys.ts` names the SPA's `/api/v1/game-catalog/*` endpoints — which are **not live**, so use the API repo instead. |
| `research/datamine/` | Raw Snowprint config from `unrstuart/datamine_tacticus`. `gameconfig142.json` (20 MB) → `misc.campaignMercyWeightAdjustment` (**the mercy numbers**), `clientGameConfig.units.upgradeSlots.xpLevelRequirements` + `units.xpLevels` + `consumables.xpBooks` (the XP gate, thresholds and the 6 books — see "XP gate & XP economics"), `clientGameConfig.upgrades[id].gold` (apply cost: Common 10 / Uncommon 25 / Rare 75 / Epic 215 / Legendary 650 / Mythic 2000), `maxStamina:60` + `staminaRegenerationTime:300` + `staminaRegenerationAmount:1` (cap **60**, 1 energy/5 min = 288/day). `newCampaignData.json` → 1170 per-node drop rows — **not strict JSON, needs a lenient parser.** |
| `research/misc/` | Wiki/tacticusdb scrapes, the planner SPA `bundle.js`, `liveconfig.json`, `globalgameconfig.json`, and the scratch scripts that produced the above. |

**Known gaps** (documented in `research/README.md`): no rank-dependent gold
multiplier anywhere in config; upgrades have **no faction lock** (only equipment
has `allowedFactions`); the rank ladder stops at Adamantine2.

### Machines of war (MOWs)

`units-*.json` splits into `characters` (117, all with `rankUpUpgrades`) and
`mows` (11, none with it — the shapes are disjoint). Authoritative:
`TacticusPlanner.Domain/PlayerData/Chunks/Units.cs` — *"MoWs have no rank and no
equipment slots"*, `PlayerMowRecord` has no `Rank` property. They still eat
upgrade items, but through **ability levels**: `mows[].primaryAbility|
secondaryAbility` = `{name, recipes:[[3 ids] × 60]}`, mirrored in
`gameconfig142.json` → `clientGameConfig.units.<id>.abilities.<id>.upgrades`.
Cost per rung: `Data/mow-upgrade-costs.json` (gold/salvage/badges/components).
Shards per star: `heroProgressionStepsMoW` + `heroConversionMoW`.
`rank_up_report.py` therefore drops them via `MOW_IDS` — do not "fix" that by
giving them a rank grid.

### Mercy, concretely

`campaignMercyWeightAdjustment = {"success":0,"fail":-1,"threshold":1}` →
chance = `numerator / (denominator − fails)`, 100% once `denominator − fails ≤
numerator`, counter resets on a drop, scoped per drop table. So **effective
rates are always ≥ the base rates** in `tacticus-drop-rates.json`.

### XP gate & XP economics (researched 2026-09-28 — surfaced by `xp_gate.py`)

Rank-ups are gated by hero **xpLevel** as well as items — why Haarken and
Azrael can't finish Diamond3 despite having the items.

**The gate.** `clientGameConfig.units.upgradeSlots.xpLevelRequirements` (the
`units.` prefix is required) = 20×6 int matrix; row = unit's current rank index
(0 Stone1 … 16 Diamond2, 17 Diamond3, 18 Adamantine1, 19 Adamantine2), column =
grid cell in `[top1,bottom1,top2,bottom2,top3,bottom3]` order (matches planner
`rankUpUpgrades[].upgradeIds` = [Hp,Hp,Dmg,Dmg,Arm,Arm]). Compared against the
unit's **`xpLevel`** (discrete), not raw `xp`. Verified: every filled cell
across all 117 units satisfies req ≤ xpLevel, and Haarken/Azrael have filled
exactly {cells with req ≤ xpLevel}. Rank-17 row = top **50/51/52**, bottom
**53/54/55** → at research time both were xpLevel 54, blocked on cell 5 (bottom
Armour, req 55); **Haarken has since promoted to rank 18** (row 18 =
55/58/56/59/57/60), so every hard-coded gap below goes stale each snapshot —
read current ones from **`xp_gate.py`**.

**Thresholds.** `clientGameConfig.units.xpLevels` (64 entries, index = level−2):
lvl 54 = 3,675,200 · **lvl 55 = 3,985,200** (step = 310,000 XP) · last entry
7,360,200 = lvl 65. (Snapshot-dependent examples: Azrael 285,872 to lvl 55 ·
Haarken 1,620,355 to lvl 60.)

**Books** — `clientGameConfig.consumables.xpBooks` (id: xp / apply-gold):
xpCommon 20/5 · xpUncommon 100/15 · xpRare 500/50 · xpEpic 2,500/150 ·
xpLegendary 12,500/500 · xpMythic 62,500/2000.

**Sources.**
- Guild shop `clientGameConfig.shop.merchants.guild.products` (guildCredits,
  power-gated; planner `Data/shops/shops-guild.json`): xpUncommon 15 (PL≤10) ·
  xpRare 50 (PL11-15) · xpEpic 180 (PL16-20) · xpLegendary 600/2 (PL21-40) ·
  **xpMythic 1500/1 (PL≥41)** — the user's "62,500 book" = Mythic Grimoire @ 1500 gc.
- Battle pass `clientGameConfig.loot.tieredProgressRewards.battle_pass_*`: free
  xpRare×2 + xpEpic×3 + xpLegendary; premium up to ×5 Legendary; **premium
  endless tier (t51/t56, 360 progress, cap 300) = repeatable xpEpic (2,500)**.
  BP *missions* grant BP progress, NOT hero XP. No Mythic in BP.
- Campaign star drops `clientGameConfig.battles.campaigns.<diff>[i].battles[].loot.star1..3`
  (**not** `newCampaignData.json` — zero XP there): **Elite star1 = xpLegendary
  on 60/160 nodes** (10 E, 6 attempts/day) · EliteMirror star1+star2 = 2
  Codices/run · Standard/Mirror = Common/Uncommon/Rare + 1 Epic node only —
  **no Codices in Standard**. Event star2/3 up to xpMythic (6 nodes). Also
  achievements, featureIntros, `loot.dropTables.xpBooksAll` (weights 50/27/13/7/3/1).
- Battle XP `battles[].xp` (Standard 6-188, Elite 86-581; ×2 lightning victory
  +100, difficulty 50/85/120) — grinding Haarken's gap ≈ 370 Elite wins vs
  5 Codices = 50 energy. Books win by orders of magnitude.

**Economics of the current gate** (research-time figures — Haarken has since
promoted, so re-derive with `xp_gate.py`): Haarken = 1× Mythic Grimoire (1500 gc
+ 2000 gold) or 5 Elite star-1 wins (50 energy); Azrael = 5× Grimoire (7500 gc +
10,000 gold) or 23 Codices (230 energy Elite, ~12 EliteMirror runs).

**User's three claims:** 62,500 ✔ · "epic tome 2500k" ✘ — it is **2,500** and
on the BP track (not missions) · 12,500 ✔ but from **Elite/EliteMirror**, never
Standard.

**Other gates / quirks:** per-cell apply gold `upgrades[id].gold` (10/25/75/
215/650/2000 — rank-dependent multiplier still absent); rarity cap
`heroProgressionSteps.maxRank` (Legendary 17, Mythic 20 → Adamantine is
Mythic-only); **config inconsistency**: `xpLevels` reaches 65 but `maxXpLevel`
caps Mythic 60 / Legendary 50 / Epic 35 / Rare 26 / Uncommon 17 / Common 8,
while the Adamantine2 row demands 65.

**Terminal / MAXED units — clamp before quoting a gap.** Any XP advice must
apply the rarity `maxXpLevel` cap (Mythic 60 / Legendary 50 / Epic 35 / Rare 26
/ Uncommon 17 / Common 8): if the grid's required levels exceed the cap, the
unit cannot finish that grid → print **maxed**, not a gap or a book list.
Concrete: **Calgar is maxed in every regard except the items he carries** —
xpLevel 60 (Mythic cap), rank 19 = Adamantine II is terminal; his rank-20 grid
needs 60–65 (unreachable past 60) *and* six "Coming soon" items. The earlier
"28× xpMythic" figure for him was illusory — books would change nothing.

### Ascension & progression value (user-supplied 2026-09-28 — data research running)

Rank-ups can also be gated by **ascension** (stars), independent of items and
XP — Titus (Diamond III, empty grid, 1.49M XP gap) can't improve without
ascending anyway, so cost-only ranking mis-labels him. A **character shards
helper** is needed to surface this (research under way: ascension config,
shard rates, onslaught tables).

- **Ascension grants +20% health and damage** (user-confirmed).
- **Power gains are tier-weighted**: Gold→Diamond and Diamond→Adamantine give
  the biggest power-level jumps; low-rank promotions give tiny gains. Advice
  that sorts by cheapest-first systematically under-invests in high-rank
  characters — that is why Gulgortz, Azrael and Haarken got the recent love.
  Any recommendation output should weigh **power gained per resource**, not
  resource alone — `power_delta.py` now supplies the power-gained half (see
  its Files row); correlating it with energy stays a caller job.
- **Shards**: standard-campaign characters (e.g. Titus) drop in **Mirror**
  campaigns, higher rates in **Elite** — but energy economics usually forbid
  farming them; the **Onslaught** event is the practical ascension push
  (Tan Gi'da "on the way", Titus "a bit further"). User plans **Mythic
  ascension for Imospekh**.
- Player fields to read: `units[].shards`, `units[].mythicShards`,
  `units[].progressionIndex` (stars), `inventory.orbs|shards|mythicShards`.

### Adamantine item sourcing (user-supplied 2026-09-28, verified against both catalogs)

**"Adamantine items can't be found in campaigns — they have to be purchased in
the crusade shop, rogue trader shop, or the normal shop for BS."** Verified
against `gameconfig142.json` + the planner catalog (1316 = 1316 nodes,
recipes identical):

- The **6 top-level Adamantine composites per grid** (`upgHpM123C`,
  `upgHpL213C`, `upgDmgM123C`/`upgDmgM212C`, `upgDmgL213C`, `upgArmM212C`,
  `upgArmL213C` — exact set varies by hero) have **zero campaign drops**. Their
  drop tables (`Upgrades<Faction>Rank17/18C`) are referenced only by
  `shop.realMoneyProducts` (daily-faction Adamantine crates) and event
  products, never by a battle. The **normal shop sells all six for
  2790–3675 BS** each (`shop.merchants.default`); `upgHpM123C` appears in *no*
  drop table at all.
- **Mythic leaves** (`upgHpM001..004` etc.) never drop in any permanent
  campaign. They are bought: **crusade shop 430 crusade currency**, guild shop
  900, **rogue trader 35 elder currency**, or event-currency shops (15).
- **The Common→Legendary leaves underneath still farm normally**: for
  Haarken's rank-18 grid, 21 of 22 leaves are permanent campaign drops = **75.6%
  of the 2,898 E**, and only `upgHpM002` (×18, 706 E, 24.4%) is event/shop-gated.
  So "unfarmable" applies to the composites and the Mythic part, **not** to the
  whole grid — do not "correct" `energy_cost.py` to zero these leaves out.
- Consequence: `rank_up_report` prices an Adamantine rung **as if you craft it
  from farmed leaves**. The buy-instead path (6 × 2790–3675 BS ≈ 17k–22k BS) is
  **not modelled anywhere** — a possible `--alt-shop` view, deliberately deferred.

### Gear & equipment (researched 2026-09-28 — surfaced by `gear_report.py`)

User's side goal: every hero at Legendary rarity **and** Legendary gear (the
shop's green circle = a Legendary+ hero wearing sub-Legendary gear). Verified:

- **3 slots per hero**, from `units.lineup.<id>.itemSlots` (crit / defensive
  or block / booster; 117 entries, MoWs have none) — **not** from the worn item
  ids. `itemSlotsRelic` is an **index** into that list (0/1/2), not a slot id.
- Catalog `clientGameConfig.items` = 214 entries (`I_<Type>_…`, 32 `R_*`
  relics). Rarity is in the id chain (`nextInSeries`); a worn/spare instance
  carries only `{slotId, level, id, name, rarity}` — **relic spares carry no
  `rarity` at all**, so take it from the catalog (id-guessing mis-reads
  `R_Booster_Crit_…` as Common). Max level per rarity: C3 U5 R7 E9 L11 M10;
  level-ups cost dust (=salvage) + gold; Epic→Legendary ascension = 10,000 gold
  + 400 dust. Stats are flat adds; **power deltas from gear are not modelled**.
- **Green circle has no server flag** (only `webstoreFreeExclusiveOffersGreenDot`
  for the webstore) — `gear_report.py` computes it: hero rarity ≥ target AND
  ≥1 slot below. Current snapshot: 5 (Neurothrope, Ammuk, Sy-gex, Morvenn Vahl,
  Typhus) — Lysander dropped off when he upgraded slot 3 in-game (was 6).
- **Worn gear is per-unit copies** (44 ids worn by 2+ heroes, no owner field) →
  a wear-to-wear shuffle is *not data-verifiable*; only `inventory.items`
  spares are a movable pool. Say this plainly in any plan: parity check YES,
  shuffle plan = spares only.
- **Faction/unit locks live only in the planner** catalog
  (`allowedFactions` 139 partial / 51 empty (= unrestricted) / 24 full-list;
  the 24 split 6×17 + 6×21 + 12×22, see "Variants & preferences";
  `allowedUnits` set exactly for the 32 relics). Datamine `allowedFactions` is
  `null` everywhere — use `research/tacticus-planner-api/.../Data/equipment/*.json`.
- **Acquisition** (never crafted, essentially no campaign drops): Daily Deals
  19/49/149/**499 BS** (Legendary, max 2/day, hidden if the hero already wears
  Legendary, random roll over `loot.dropTables.itemsLegendary`) and gold
  1300/3900/12000; **crusade shop 15/30/70/215/715/1785** (Common→Mythic);
  elder shop Mythic 145; event shops U1/R3/E8/L25/M65. `tacticus-shop-prices.json`
  → `other_prices.equipment_bs` has 19/49/149/499 only (no Mythic key — guard it).
- Salvage = `dust` (+ `mythicDust`), earned in Salvage Run / battle pass /
  events, spent on level-ups, ascension and token crafting; `dustRefundPct=100`.
  No dust balance exists in the player snapshot, so nothing here is priced in dust.

**Variants & preferences (added 2026-09-28).** Level-1 stats from the planner
catalog define shape lines: crit `critChance` 20 knife / 25 / 30 / 35 gun / 40
(never scales with level — only `critDamage` does); defensive = hp+armour 34
vs pure armour 6 ("Plated Greaves", one per rarity); block = chance lines
20/25/30/35 (Refractor/Power/Force/Iron Halo) — block items always carry both
`blockChance`+`blockDamage`; boosters' bonuses are rarity-locked (no choice).
Hero preference is data-driven from `units.lineup.<id>`: `weapons[]` (entry[0]
melee without `Range`, entry[1] ranged) → max hits, and `traits` →
`ActOfFaith` (5 Sisters: Vindicta, Isabella, Morvenn, Roswitha, Celestine) and
`MkXGravis` (4: Bellator `ultraInceptorSgt`, Calgar, Burchard `templAggressor`,
Nubari `astarEradicator`). Community rule encoded: 1 hit → knife, 3+ → gun
(crit compounds), 2 ≈ marginal, ActOfFaith always knife, MkXGravis pure armour.
Crit-camp buffs (Howl/Aethana/Eldryon) are NOT detectable from data — noted in
the tool's docstring instead. Full-list `allowedFactions` come in three
lengths (17 / 21 / 22 of the 22-faction universe, 6+6+12 items), so "universal"
= 51 empty + 24 full-list = 75; the other 139 are faction-bound (verified by
recounting the histogram: 51 + 139 + 24 = 214).

### Ability / badge economy (researched 2026-09-28 — surfaced by `ability_gate.py`)

Abilities level up with **gold + alliance badges** (not items, not XP). The
user's pain: permanently short on **Uncommon Imperial** badges, buying them in
the guild-war shop.

- **Stock** `player.inventory.abilityBadges` = `{alliance: [{name, rarity,
  amount}]}` — Imperial C28 U25 R5 E156 L219 M47 · Xenos 62/28/94/123/220/13 ·
  Chaos 434/174/300/372/434/84. One shared pool **per alliance** — the block is
  aggregate, never per-hero. Siblings: `inventory.components` =
  `[{name, grandAlliance, amount}]` (Imperial 676 / Xenos 650 / Chaos 492 —
  **purpose unresolved**, consumed by nothing found) and `inventory.forgeBadges`
  = `itemAscensionResource_<rarity>` C0/U635/R413/E109/L87/M1 (the craft-up
  ingredient).
- **Ids** `abilityToken<Rarity>_<Alliance>` (18 = 6 rarities × 3 alliances);
  no catalog entry. Cost side uses generic `abilityToken<Rarity>` keys.
- **Cost ladder** `clientGameConfig.units.abilityUpgradeCosts` = **59 entries**,
  keys only `gold` + exactly one `abilityToken<Rarity>`; **no alliance scoping**
  (alliance comes from the unit). **Indexing = level − 1** (verified: 59 entries
  cover L1→L60, max observed = Calgar 60 → no entry → `capped`). Ranges by
  index: Common 0–6 / Uncommon 7–15 / Rare 16–24 / Epic 25–33 / Legendary
  34–48 / Mythic 49–58 — so the Uncommon barrier is **L8→9**, not 7→8.
  `abilityUpgradeCostsMoW` = same 59 + `machinesOfWarToken`/`dust`/
  `itemAscensionResource_*` (why the 11 MoWs are excluded from the report).
- **Craft-up** `resourceCrafting.recipes` (15) = 3× lower badge, same alliance
  + 1 forge badge + gold (1000 U / 1500 R / 3000 E / 6000 L / 9000 M).
- **Acquisition**: guild-war shop `guildWars` slot10 = `draft_abilityTokens
  Uncommon:3` @ **385 guild-war currency, daily, NO maxPurchases and NO season
  lock in the config** — the user's "only in GW season" claim is partly wrong;
  the GW ladder also sells C3/195, R3/770, E3/1545, L3/3085, M3/6175. Default
  shop slot4 = 99 gems / 4 draft Uncommon (max 5/day, min PL20) plus singles
  C5/65 R4/199 E3/299 L2/399 M2/519. Event shops 10×Uncommon for 15 event
  currency; elder shop Mythic 30. **The guild shop (guildCredits) and the
  crusade shop sell NO ability tokens.** Elsewhere: crafting, battle-pass
  chests, IAP.
- **Abilities per hero**: 106 heroes × 2 (active + passive); 11 MoWs × 3 (third
  always level 0). Abilities have **no rarity** — ascension boosts them through
  `heroProgressionSteps[].abilityStatMultiplierPct` (100 → 200 per rarity jump —
  this is the "+20%" number) + `abilityPowerMultiplier`.
- Current snapshot short pools (roster demand > stock): **Imperial Uncommon
  107/25 (82 short) · Imperial Rare 31/5 (26) · Xenos Uncommon 69/28 (41)**;
  gold for every next upgrade = **2,234,600** (no gold balance exists in the
  snapshot, so gold never gates the tool's status).

## Commands

```bash
# regenerate the page (after spec refresh or key rotation)
python3 gen_swagger.py

# refresh the cached player data (atomic write; --pretty to indent, -q quiet)
python3 update_player.py

# characters closest to their next rank (6-slot grid model)
python3 rank_up_report.py --top 15

# ...priced in energy (exact; needs research/ present)
python3 rank_up_report.py --energy --sort energy --top 15
python3 rank_up_report.py --energy --sort tier --top 15     # whole tier left, not 1 rung
python3 rank_up_report.py --energy --json                  # per-leaf breakdown
# ...rarity shortcut (no research/ needed; reads tacticus-drop-rates.json)
python3 rank_up_report.py --energy --estimate --tier Elite
python3 rank_up_report.py --energy --estimate --item-rarity Legendary
python3 rank_up_report.py --energy --estimate --include-events   # allow Extremis

# per-item shop deal check (names in output, ids in --json; needs research/)
python3 item_value.py                                   # static dictionary, all 557 items
python3 item_value.py | fzf                             # drill down / look up a component
python3 item_value.py grand_strategy --price 540        # 6-step verdict vs the fair floor
python3 item_value.py "Grand Strategy" --price 540 --qty 2 --json
python3 item_value.py upgHpL017C --price 540 --spender frugal   # stricter ceiling (or --threshold 3)

# xp gate: which characters are blocked by hero XP instead of items
python3 xp_gate.py                          # grouped by status, gap-ascending
python3 xp_gate.py --only blocked           # just the book-blocked ones
python3 xp_gate.py --only maxed --json      # rarity-capped units, machine-readable

# gear parity: who is below the target rarity, and what the spare pool covers
python3 gear_report.py | fzf                # full roster table (green = missing)
python3 gear_report.py haarken              # one hero: slots, candidates, fill plan
python3 gear_report.py --target Mythic --json

# ability gate: which heroes are blocked on badges (one shared pool per alliance)
python3 ability_gate.py                       # grouped by status, blocked first
python3 ability_gate.py --only blocked        # just the badge-blocked heroes
python3 ability_gate.py lysander --json       # one hero: per-ability cost + pool

# power delta: power bought by the next rank-up (planner-proxy formula)
python3 power_delta.py                        # roster sorted by +Rung
python3 power_delta.py haarken --json         # one hero: formula with its numbers

# team comps: which curated guild-raid comps can you field?
python3 team_roster.py                        # 7 comps, fieldable first
python3 team_roster.py multi --json           # one comp in detail (partial id)

# the assembler: where am I blocked, what should I do next? (seven witnesses)
python3 next_step.py                          # the full brief (~1.1 s)
python3 next_step.py --top 20                 # longer farm list
python3 next_step.py --json                   # machine-readable assembly

# MCP adapter (agent-facing) - marshalling only, no pricing logic of its own
printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"smoke","version":"0"}}}' | python3 tacticus_mcp.py   # exit 0, one JSON reply
opencode mcp list                    # tacticus should show as connected

# proxy lifecycle (preferred) - `status` is the default and exits 0 only when up
./proxy_ctl.sh up          # start (or replace a stale/hung process)
./proxy_ctl.sh down        # stop
./proxy_ctl.sh restart     # stop then start
./proxy_ctl.sh status      # exit 0 = running and answering HTTP 200
# equivalent raw start, if the wrapper is unavailable:
#   cd /home/user/workspace && setsid nohup python3 tacticus_proxy.py > /tmp/opencode/proxy.log 2>&1 < /dev/null &

# direct API access (outside the browser)
curl -sS -H "X-API-KEY: $(cat .tacticus_api_key)" https://api.tacticusgame.com/api/v1/player
```

## External data sync (`research/`)

`research/` was fetched **once** (2026-09-27) and nothing auto-refreshes —
provenance and the "what each dir answers" map: `research/README.md`.
Consumers: planner Data via `energy_cost.py` (→ `rank_up_report --energy`,
`item_value.py`, `machine_hunt.py`), `gear_report.py`; datamine
`gameconfig142.json` is **hardcoded in four tools** (`xp_gate.py`,
`ability_gate.py`, `gear_report.py`, `power_delta.py`). Spec and player have
their own refresh commands (above) and are independent of `research/`.

**When to sync:** a game patch (config version bump, new campaign/nodes), any
`KeyError` / `MissingSource` from a tool after upstream moves, or before
trusting `--energy` numbers for a big decision. Cheap staleness check:
`git -C research/tacticus-planner-api log -1 --format=%cs` — **if that date is
more than 7 days old, remind the user to sync** (ask first; never pull
unprompted).

**How** (git-ignored, so a bad sync is `git checkout` inside the clone —
never `git add -f`):

```bash
git -C research/tacticus-planner-api pull --ff-only   # then: git ... diff --stat
git -C research/tacticus-planner-apps pull --ff-only
# datamine: raw downloads, NOT a clone. Check unrstuart/datamine_tacticus for
# a gameconfig.1.4X.json newer than 1.42; download it beside the old file,
# repoint GAMECONFIG in the four tools, update research/README.md, run the
# smoke below, then delete gameconfig142.json.
```

**Safety test — after any sync, with the same player snapshot:**

```bash
python3 -m py_compile *.py &&
for c in "rank_up_report.py --energy --json" "xp_gate.py --json" "ability_gate.py --json" "power_delta.py --json" "gear_report.py --json" "team_roster.py --json" "item_value.py --json" "machine_hunt.py --json" "machine_hunt.py --selftest" "next_step.py --json"; do
  python3 $c >/dev/null || echo "FAIL: $c"
done
```

Zero `FAIL` lines = every consumer still parses its input. For silent schema
drift (exit 0 but changed meaning): capture key `--json` outputs before the
sync and `diff` after — outputs are deterministic for a fixed snapshot.
`machine_hunt.py --selftest` additionally asserts known ids still resolve.

## Verification

```bash
curl -i -X OPTIONS http://127.0.0.1:8124/api/v1/player \
  -H 'Origin: http://127.0.0.1:8124' -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: x-api-key'      # → 204 + ACAO: *
curl -i -H "X-API-KEY: $(cat .tacticus_api_key)" \
  http://127.0.0.1:8124/api/v1/player                 # → 200 + ACAO: * + JSON
curl -i -H 'Origin: http://127.0.0.1:8124' \
  http://127.0.0.1:8124/api/v1/player                 # no key → 403, still ACAO: *
```

A `403` with CORS headers is a *success* for the proxy — the browser can read it.
Only "Failed to fetch" indicates a proxy/CORS problem.

## Gotchas learned the hard way

- **`pkill -f <pattern>` can match your own shell.** `pkill -f http.server 8123`
  killed the shell running it (the pattern appears in that shell's own command
  line), silently aborting the rest of the script. Use a bracket pattern such as
  `'tacticus_proxy[.]py'`, and never chain critical work after a bare `pkill`.
- **No desktop browser is connected to this session** — `browser.tabs.*` and
  `browser.preview` fail with `[browser.disconnected]`. Verify with curl instead
  of attempting page automation; tell the user to hard-reload (`Ctrl+Shift+R`).
- **The spec is inlined** into `swagger-ui.html`, so there is no fetch of the
  definition. That also means caching hides edits until a hard reload.
- **Context7 MCP is enabled** in `~/.config/opencode/opencode.json`. Use
  `tools.context7["resolve-library-id"]` + `tools.context7["query-docs"]` for
  library docs (used here for the Swagger UI standalone setup).
- The bundled key has scope **`Player` only** — `/api/v1/guild` and
  `/api/v1/guildRaid` will return `403` for this user.
- **`.mise.toml` pins tool versions** (node 24 for openspec — Ubuntu apt ships
  18.19.1 and `/usr/local` is root-owned so `npm -g` fails; openspec 1.13.2).
  `mise install` on first run, `mise upgrade` to update; the shims dir must be
  on PATH or node falls back to system 18.x outside the repo tree.

## Security notes

- The API key is a bearer credential and exists in plaintext in
  `.tacticus_api_key` **and** `swagger-ui.html`.
- **The repo root is `/home/user/workspace/tacticus`** (branch `main`). The
  `.gitignore` excludes `.tacticus_api_key`, `swagger-ui.html`,
  `tacticus-player.json` and `research/`;
  never `git add -f` any of them — `research/` is 74 MB of third-party clones.
  Before every commit, check `git status` and `git diff --cached --name-only`
  — the key must appear nowhere in the index (search it: `git grep -l
  <key-prefix>` on the staged list).
- To rotate: update `.tacticus_api_key`, rerun `gen_swagger.py`, and note the
  key also appears in this conversation's history.

## Open / possible follow-ups

- Point the UI at guild endpoints — needs a key with `Guild` / `Guild Raid` scope.
- Fetch the live spec on demand instead of embedding (would reintroduce a fetch
  through the proxy; current inline approach is deliberately simpler).
- **Energy model: exact now, but 4 things still unmodelled.** `--energy` joins
  `research/` via `energy_cost.py` (required item ids → recipes → inventory →
  cheapest node). Remaining gaps: (a) **gold** apply/craft cost (per-rarity
  10/25/75/215/650/2000 is in `research/datamine/gameconfig142.json` but the
  rank-dependent multiplier is absent from every config found); (b) the **XP
  gate** — now its own tool, `xp_gate.py` (Files row above), so the rank-up
  report still never mentions XP: correlating "item price + XP gap +
  ascension" for one character stays a caller job;
  (c) **attempt limits / event rotation**, so energy ≠ calendar time; (d)
  inventory is priced **per character independently**, so a multi-promotion plan
  overstates how far the bank stretch — *surfaced* since 2026-09-29:
  `rank_up_report --energy` reports `contention` (bank vs next-rung claims,
  free-rung drains first) and `next_step` marks its farm rows `!`; a
  sequencer that assigns the shared bank to chosen heroes is still unmodelled.
- **10 items are unpriceable**: `upg{Hp,Dmg,Arm}{,E,R}CS` ("Coming soon") plus
  `upgDmgE005` — no drop, no recipe. They block **Calgar only** (the 11 MOWs
  that used to pad this list are excluded from the report now).
- **MOW ability pricing** (new section): price `mows[].…Ability.recipes[0..59]`
  × `Data/mow-upgrade-costs.json` — a separate report, not a rank grid.
- Optional: copy `research/` into a small derived JSON so the report does not
  read the 15 MB clone (nice-to-have; the clone loads in well under a second).

## Next research angle: shop vs farm (open)

Goal: decide **when to buy an item (daily deals / blackstone) vs when to farm
it**, and test the hypothesis that **shop singles at high rarity are
overpriced** (composites even more so).

**Valuation rule of thumb (persisted so we do not re-derive it):**

- Cheapest blackstone → energy is the **25 BS bundle = 60 energy ⇒ 0.417 BS/E**
  (next: 50 BS → 100 E = 0.50 BS/E; 100 BS → 100 E = 1.00 BS/E; the daily ad is
  60 free energy ⇒ 0 BS/E). Sources live in `tacticus-drop-rates.json` →
  `energy.extra`.
- Therefore a **fair shop price** for anything = `energy_to_farm × 0.417 BS`
  (floor). Anything materially above that is a bad deal *unless* attempts or
  time are the binding constraint — buying wins when the daily attempt cap
  (10 standard / 6 elite per node) or event rotation blocks farming.
- **energy_to_farm per single leaf item** is already computed by
  `energy_cost.py` (`leaves[id].per_item`, cheapest node, mercy baked in):
  Common ≈ 5.7–8 E, Uncommon ≈ 7.5–8 E, Rare ≈ 9–10 E (Elite guaranteed),
  Epic ≈ 14–18 E, Legendary ≈ 23.4–30 E (Elite), Mythic ≈ 39 E (events only).
  → implied fair prices ≈ Common 3 BS, Rare 4 BS, Epic 6–7.5 BS,
  Legendary 10–12.5 BS, Mythic 16 BS.
- **Composites**: price = expand the `recipe` tree and sum leaves (what
  `energy_cost.price()` does with an empty inventory) — never compare a shop
  price against the top-level item only.

**Data status: FOUND (2026-09-27).** The shop table lives in the local datamine
— `research/datamine/gameconfig142.json` → `$.clientGameConfig.shop`
(`merchants.*.products` = the weighted daily-deal pool, `products` = 958 priced
SKUs, `realMoneyProducts` = IAPs in cents, `staminaShopTiers` = the refill
ladder) — plus the community layout at `tacticus.wiki.gg/wiki/Shops`. Both are
distilled into **`tacticus-shop-prices.json`** (prices, sources, resolved
conflicts in `conflicts_resolved` — the slot-3 chest-vs-single ladder and the
110 BS refill — plus `requisition.ev_from_weights`, and the
`fair_price_floor_bs` markup analysis). Per-item verdicts are automated in
**`item_value.py`** (fair floor = full-craft farm energy × 0.417 BS, inventory
ignored; the stock delta is displayed, not judged; verdict = profile-relative
6-step scale from `energy.spender` / `--spender` / `--threshold`, and the
static view grades the `typical_single_bs` ladder into `SHOP`/`VERDICT`
columns). Blackstone income — $4.99 Daily Shipment subscription + Discord
promo codes — is quantified in the same file under `blackstone_income`
(~2,000 BS/30d sub + ~180–350 typical monthly codes ≈ 2,100–3,400/month
before salvage, vs the 185 BS/day refill habit ≈ 5,550/month, so refills
outspend the subscription). Interpretation note: shop singles are priced
**flat per rarity** in the datamine (every Uncommon 30 BS, every Rare 80 BS,
regardless of craft depth — only Epic/Legendary vary by series tier, E0xx 225
vs E1xx 675, L0xx 690 vs L1xx 1790 vs L2xx 2880), so at equal rarity price a
composite carries far more farm energy than a single: craft rows grade much
better than farm rows, and there is no "bundle discount" — it is flat pricing
meeting depth-varying floors. The public API is
read-only (4 GETs) so it exposes **no** shop data; today's picks are
server-rolled, only the weighted pool is in config (no fixed calendar).
Also note `player.inventory.requisitionOrders` (`regular`/`blessed`) and
`resetStones` — requisition pulls (300 BS each) are a related "is it worth it"
angle; EV computed from the raw drop-table weights in
`tacticus-shop-prices.json` → `requisition.ev_from_weights` (18.3% full unit,
8.15 shards, 0.9 orbs per pull).
