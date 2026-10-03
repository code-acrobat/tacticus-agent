# Tacticus API — Swagger UI

Interactive Swagger UI for the [Tacticus](https://tacticusgame.com) game API
(`https://api.tacticusgame.com/api-docs`), plus a small local proxy so the
browser can actually talk to it.

**Open: <http://127.0.0.1:8124/>**

---

## Why a proxy exists

The Tacticus API is read-only but has **no CORS support**:

| Request | Result |
|---|---|
| `GET /api/v1/player` with `Origin: …` | `200` but **no** `Access-Control-Allow-Origin` header |
| `OPTIONS` preflight (browser sends this because of the custom `X-API-KEY` header) | **403** |

A page calling the API directly therefore fails with *"Failed to fetch"*, and no
amount of client-side code can fix that. `tacticus_proxy.py` sits on
`127.0.0.1:8124`, serves the UI, forwards every other path to the real origin
(body untouched), adds the `Access-Control-*` headers, and answers preflights
itself with `204`.

Because the UI is served *from the proxy*, its API calls are same-origin and
CORS-approved regardless of which origin header the browser attaches.

---

## Quick start

```bash
cd /home/user/workspace
./proxy_ctl.sh up          # serves http://127.0.0.1:8124/
```

Then open <http://127.0.0.1:8124/> and press **Try it out** → **Execute** on any
operation. The `X-API-KEY` header is pre-filled (see below).

```bash
./proxy_ctl.sh status      # exit 0 = up and answering HTTP 200
./proxy_ctl.sh restart     # stop, then start
./proxy_ctl.sh down        # stop
```

Log: `/tmp/opencode/proxy.log`. Without the wrapper you can run the server
directly (`python3 tacticus_proxy.py`) and stop it with
`pkill -f 'tacticus_proxy[.]py'` — note the bracket, which stops `pkill` from
matching its own command line.

## Files

| File | Purpose |
|---|---|
| `tacticus_proxy.py` | HTTP server: UI at `/`, CORS proxy for everything else |
| `proxy_ctl.sh` | Start/stop/status wrapper: `./proxy_ctl.sh {up|down|restart|status}` |
| `swagger-ui.html` | **Generated** — Swagger UI v5.11.0 with the spec embedded inline |
| `gen_swagger.py` | **Generates** `swagger-ui.html` from the spec + API key |
| `tacticus-openapi.json` | The OpenAPI 3.0.1 spec, downloaded from `/api-docs` |
| `tacticus-player.json` | Sample response from `GET /api/v1/player` (246 KB) — gitignored, roster data stays local |
| `update_player.py` | Refreshes `tacticus-player.json` from the API (atomic write, prints a summary) |
| `rank_up_report.py` | Ranks characters by how many items remain until their next rank; `--energy` prices those items exactly |
| `energy_cost.py` | The exact price model behind `--energy`: loads the catalog under `research/`, expands recipes, deducts the inventory, picks the cheapest node per leaf |
| `item_value.py` | Per-item shop-deal check: a static dictionary of all 557 catalog items (pipe to `fzf`; `SHOP`+`VERDICT` columns grade the typical rarity price), or one item's components + leaf-by-leaf farm costs + a `--price` verdict on a profile-relative 6-step scale vs the full-craft fair floor (delta shown separately; `--spender`/`--threshold`) |
| `xp_gate.py` | The XP half of a rank-up: which characters are blocked by hero **XP** rather than items. Open cells vs `xpLevelRequirements[rank]`, gap clamped at the rarity `maxXpLevel` (→ `maxed`, no book list), and a book plan (stock first, then the guild-shop offers unlocked at your power level) with apply-gold + guild credits. `--only blocked\|maxed\|ok\|full`, `--json` |
| `gear_report.py` | **Gear parity** across the 3 equipment slots per hero: who is below the target rarity (default Legendary — the green-circle list), a greedy fill plan from the shared spare-gear pool (type + faction + relic-slot aware), a buy line for what's left (Daily Deals / crusade shop), plus a **Relic** column and relic spares. The detail view tags each piece's **variant** (crit gun/knife, hp+armour vs pure armour, block chance line) and prints a per-hero `pref (community)` line from its hits/traits. Query = one hero's detail view. `--target`, `--json` |
| `ability_gate.py` | The **badge half** of an ability upgrade: which heroes are blocked because a roster-wide badge pool (`inventory.abilityBadges`, one shared pool per alliance) can't cover demand. Cost ladder `abilityUpgradeCosts[level−1]`; statuses `badge-blocked`/`capped`/`ok`; `Need` = one hero's ask, the `short pools:` line = the real gap; gold is reported but never gates (no gold balance in the snapshot). Query = one hero: each ability's next-level cost with `[pool … SHORT]` markers, `--only`, `--json` |
| `power_delta.py` | **Power bought by the next rank-up** (planner's V1 formula as a proxy — the in-game one is unpublished). `Power` now, `+Rung` = power from filling the next 6-cell grid, `+Hp/+Dmg/+Arm` = the flat stat grants, `+END` = ladder finished (Calgar). Detail view shows the formula with this hero's numbers. Query = one hero detail, `--json`. Correlate with `rank_up_report --energy` outside the tool (power per energy) |
| `team_roster.py` | **Which curated team comps can you field?** The 7 community guild-raid comps (terminus-maximus + cognitae, via the planner) checked against your roster: signature / core / flex / MoW ownership, a 5-hero pool count and a `fieldable` / `no signature` / `thin N/5` verdict. Deliberately computes **no synergy score** (no combo key exists in config). Query = one comp in detail, `--json` |
| `next_step.py` | **The assembler** — "where am I blocked, what should I do next?" in one screen. Convenes the seven witnesses (rank, xp, badges, power, gear, team, events) and prints an `EVENTS` block (live/upcoming windows + strategy pointer per event), then the gates with hero examples, then a leverage-ordered action list: pool purchases → apply stock books → farm by power per energy → gear → unlocks. Zero pricing logic of its own. `--top N`, `--json` |
| `machine_hunt.py` | **Where do Mechanical enemies die cheapest?** Event view ranking campaign nodes by event **points** per energy (3 pts per Standard/Mirror kill, 5 per Elite — gameconfig trackers; `Pt/E`/`Pts` columns, `pt_per_e`/`pts` in `--json`), with your live attempt count per node (`Att` — exhausted nodes hidden, `--all` shows them), plus a header line with the live event window (`--json` → `event_ends`). `--items` = balance option: flag nodes that also drop items your next rank-ups need, ending with a totals footer (need per distinct item + top heroes). `campaign` filter, `--top`, `--json`, `--selftest`. `--json` also carries `events` (live/upcoming schedule) — that is what `next_step.py` reads as its advisory `event` witness |
| `tacticus-drop-rates.json` | Economy config: drop rates by tier × rarity, energy costs, regen + the `extra` daily sources (ad/crate/blackstone → 638/day behind `Days`) + the `spender` verdict-ceiling profiles, mercy notes, tunable `rank_item_rarity` (used by `--estimate`) |
| `research/` | **Git-ignored, ~74 MB** — offline copies of the third-party sources used for game-mechanics research (planner repos, raw game config, wiki scrapes). See `research/README.md` |
| `tacticus-shop-prices.json` | Daily Deals / shop prices (energy refill ladder, per-rarity singles, chests, requisition + EV, coins, `typical_single_bs` ladder behind the `SHOP` column, `blackstone_income` = subscription + promo-code estimates) with `fair_price_floor_bs` markup vs farming. For the buy-vs-farm angle. |
| `tacticus_mcp.py` | Thin MCP adapter (stdio, stdlib only) exposing `rank_up_report`, `item_value`, `xp_gate`, `gear_report`, `ability_gate`, `power_delta`, `team_roster`, `next_step`, `player_refresh` as agent tools + the two config files as resources. Marshalling only — runs the CLIs and returns their `--json` verbatim. Registered in `opencode.json`. |
| `make_bundle.py` | Packs the browser bundle (`dist/bundle.tar.gz`, gitignored): code + configs + planner data, **no roster/key**. |
| `browser_harness.py` | Pyodide runner: extracts the bundle, shims the two `subprocess` call sites in-process. Self-check: `python3 browser_harness.py` (byte-equal vs real subprocess). |
| `web/index.html` | Browser front end: Pyodide in-tab, brief via `next_step`, chat via the Worker proxy. Key stays in the tab (sessionStorage) and only transits the proxy to the game API. Chat replies render as HTML — markdown tables become sortable tables (DataTables + jQuery, jsDelivr). |
| `worker/worker.js` | Cloudflare Worker: `/player` CORS pass-through (key never stored), `/chat` LLM relay over the precomputed brief. |
| `.github/workflows/pages.yml` | Deploys the site to GitHub Pages (re-fetches gitignored `research/`, pinned). |
| `.tacticus_api_key` | API key (`chmod 600`), read by `gen_swagger.py` |

## Regenerating the UI

```bash
python3 gen_swagger.py     # rewrites swagger-ui.html
```

Do this after refreshing the spec or rotating the key. The generator:

1. Embeds the spec inline (so no fetch/CORS issue for the definition itself)
2. Sets `servers` to `http://127.0.0.1:8124` — **the proxy**, never the real host
3. Puts the API key in `example` on every `X-API-KEY` param (prefills the field)
4. Adds a `requestInterceptor` that forces the origin onto the proxy and injects
   the key when the header is missing/empty

Refreshing the spec:

```bash
curl -sS -o tacticus-openapi.json https://api.tacticusgame.com/api-docs
python3 gen_swagger.py
```

## Refreshing the player data

```bash
python3 update_player.py            # refetch tacticus-player.json + print a summary
python3 update_player.py --pretty   # indented JSON (21k lines) instead of as-served bytes
python3 update_player.py -q         # quiet, just update the file
```

Writes are atomic (`.tmp` then rename), so an interrupted run never truncates the
cache. A failed request exits non-zero without touching the file. On success:

```
wrote /home/user/workspace/tacticus-player.json (246,558 bytes)
  player      codeacrobat - power level 60
  units       117
  top ranked  Calgar (Ultramarines) rank 19
  server sync 2026-09-26 13:50:16 UTC
  scopes      Player
```

`server sync` is `metaData.lastUpdatedOn` — when the game server last produced
the data, which may lag your fetch by a few minutes.

## Rank-up proximity report

Which characters are closest to their next rank ("item level"):

```bash
python3 rank_up_report.py              # full list, closest first
python3 rank_up_report.py --top 15     # just the nearest 15
python3 rank_up_report.py --sort rank  # most advanced first instead
python3 rank_up_report.py --json       # machine-readable

python3 rank_up_report.py --energy --sort energy --top 15   # cheapest to promote
python3 rank_up_report.py --energy --sort tier --top 15     # cheapest tier crossing
python3 rank_up_report.py --energy --json                   # per-item breakdown
python3 rank_up_report.py --energy --estimate               # fast rarity shortcut
python3 rank_up_report.py --energy --estimate --tier Elite  # ...force a farm tier
```

Both `--json` modes are versioned: list-shaped payloads (rows of characters or
items) are `{"schema_version": 1, "rows": [...]}`, and a single item's detail
is a flat object with `schema_version` alongside its fields. Consumers — and
the MCP adapter below — should check `schema_version` before reading `rows`.
With `--energy --json` the payload also carries `contention` (see
"Shared-bank contention" below).

The report covers the **106 characters**. The 11 machines of war are excluded
by `MOW_IDS`: they have no rank grid at all (`PlayerMowRecord` has no `Rank`
property — they progress through ability levels instead, which use the same
upgrade items but a different ladder). They are listed in `research/README.md`.

```
Rank-up proximity - 106 characters
Grid: 6 slots per rank-up; 'Missing' = slots still empty.
Excluded 11 machines of war - they have no rank grid (they level abilities instead; see research/README.md).

#  Character  Faction      Current      Next          Grid  Missing
-  ---------  -----------  -----------  ------------  ----  -------
1  Azrael     DarkAngels   Diamond III  Adamantine I  5/6         1
2  Imospekh   Necrons      Diamond II   Diamond III   5/6         1
3  Archimatos BlackLegion  Gold II      Gold III      5/6         1
...
```

### How "missing" is derived

The API does not publish promotion costs (the "which upgrade costs how much
energy" part), so the count is inferred from the roster data:

- Rank ladder is `Stone → Iron → Bronze → Silver → Gold → Diamond → Adamantine`,
  three steps each (rank index `0..20`, so "Gold 3" = rank 14 and its next step
  is Diamond I). This matches `rank` in the OpenAPI spec, extended past the
  spec's documented maximum of 17 — the game has since added Adamantine.
- Each unit carries `upgrades`, the **filled cells of the 2×3 rank-up grid**
  (`0 = top left, 1 = bottom left, 2 = top centre, …`).
- **A rank-up needs all 6 cells.** Evidence from the data: no character ever has
  6 filled (hitting 6 promotes and resets the grid), while 21 characters sit at
  5/6 unpromoted across ranks 5–17 — if 5 were enough they would already have
  promoted.
- Therefore `missing = 6 − len(set(upgrades))`.

The grid alone tells you *how many* pieces are left, not *which* ones — the API
exposes no requirement table. `--energy` therefore reads
`research/tacticus-planner-api/` directly, which does publish the 6 required
item ids for every character × rank, so the count above and the items actually
priced below always agree.

Sorting is `missing` ascending, ties broken by rank descending (the more
invested character first), then name.

### Pricing it in energy

`--energy` prices the exact items still needed, by joining four offline sources
under `research/tacticus-planner-api/` (implemented in `energy_cost.py`):

```
#  Character  Faction      Current      Next          Grid  Energy  TierE  Days  Note
-  ---------  -----------  -----------  ------------  ----  ------  -----  ----  ----
1  Azrael     DarkAngels   Diamond III  Adamantine I  5/6      486    486   0.8  -
2  Imospekh   Necrons      Diamond II   Diamond III   5/6      187   2628   4.1  events
3  Wrask      WorldEaters  Gold I       Gold II       5/6      294   2919   4.6  -
...
Energy (exact, catalog-priced, 106 unpromoted characters):
  total to promote everyone: 45,575  [+1 unpriceable]
  total to leave their current tier: 158,835  (every rung up to the tier switch, not just the next one)
  (of 106, 1 have a later rung not yet released, so their tier total is excluded here)
  of it, only obtainable in timed events: 1,608
  cheapest single rank-up: Maugan Ra (Silver I -> Silver II), 20 energy across 1 leaf item(s)
  cheapest full tier climb: Typhus (Iron III -> Bronze I), 50 energy over 1 rung(s)
  at 638/day (288 regen + 350 claimed from ads/blackstone): 71.4 days for one rung each; 249.0 days to clear a tier each
  blocked - no drop and no recipe yet: Calgar
  next rung priceable, later rung in this tier unreleased: Haarken
```

**Two different kinds of "blocked".** Haarken's row prices to `2898` energy
yet shows `?` for `TierE`/`Days` with the note `tier pending`: he is on a fresh
Adamantine I grid that costs nothing to value, but the **Adamantine II** rung
above him is still six "Coming soon" items, so his *tier* total does not exist.
Such rows stay in `total to promote everyone` (the next rung is real) but are
excluded from `total to leave their current tier` and named on their own footer
line. Calgar's grid *itself* is unreleased, so he is the one genuinely
`blocked` row — `next_blocked` in `--json`. Haarken and Azrael are separately
gated by **XP** — that half has its own tool, `xp_gate.py` (§ "XP gate report"
below), so this report stays items-only. Totals move on every
`tacticus-player.json` refresh.

What it does, in order:

1. **Which items?** `rankUpUpgrades[current rank].upgradeIds`, minus the cells
   already filled in `unit.upgrades` — cell *i* of the grid is item *i*.
2. **Recipes.** Every item is either farmable *or* craftable, never both (the two
   id sets are disjoint, verified). Craftables expand through their `recipe`
   recursively, up to 3 levels deep. All 6 cells are aggregated **first**, so a
   component shared by several of them is counted once, not once per cell.
3. **Inventory.** `player.inventory.upgrades` (164 ids, joins the catalog 1:1) is
   consumed at *every* level before recursing — an owned composite saves
   crafting it, an owned leaf is free. This is why "which parts are already
   available" moves the number so much.
4. **Leaves.** net need × `energyCost / effectiveRate` of the cheapest node that
   drops that item. `effectiveRate` already has mercy applied, so the raw
   `numerator/denominator` is never used.

Sanity check: Titus (Diamond III → Adamantine I, 0/6 filled) needs 190 raw
component units across 18 ids; the inventory covers 41, leaving 137 permanent +
12 Mythic → **≈2,417 energy**, versus 180 for the naive "6 × one legendary
drop". The rarity estimate was off by an order of magnitude — that is the whole
point of reading the recipes.

### Energy, TierE and Days

The report carries three numbers, because they answer different questions:

| Column | Scope | Question it answers |
|---|---|---|
| `Energy` | the single next rank-up (`1 → 2 → 3` inside a tier) | "who can promote *right now* cheapest?" |
| `TierE` | every rung left before the tier itself switches (`Stone → Iron → Bronze → …`) | "who can change material category soonest?" |
| `Days` | `TierE ÷ daily energy budget` | "how long will that crossing take?" |

Tier pricing walks `rank … tier_end(rank)` and prices all of those grids
**against one inventory pool**, so a component already banked for rung 1 is not
counted again for rung 2 — the same rule as the multi-cell aggregation inside a
single grid. `tier_end` stops at the first rung of the next tier (`Bronze I →
Silver I`), and `--json` exposes `tier_energy`, `tier_steps`, `tier_target` and
`tier_detail` (per-rung breakdown) for each character.

`--sort tier` ranks by `TierE`, blocked rows last. It also works with
`--estimate` (there each remaining rung is priced as a fresh 6 cells at that
rung's inferred rarity, still top-level items only).

The two rankings genuinely disagree: Tjark is cheapest for a single promotion
(15 energy) but needs 375 to clear Bronze, while Lucius needs 15 for both —
he sits on the last rung of Iron, so his next promotion *is* the tier switch.

#### Shared-bank contention (who actually gets the bank?)

**Every hero is priced against the full inventory independently.** So when two
next rungs both withdraw the same banked item, both rows may call it free — but
only the hero you promote **first** gets it from the bank; the rest must craft
the difference. `--energy` aggregates those withdrawals: the footer lists the
items whose combined next-rung claims exceed what you hold, free-rung drains
sorting first, and `--json` carries the full list as `contention`:

```json
{"id": "upgDmgR13C", "name": "Prey-Sight", "bank": 1, "claims": 8,
 "heroes": ["Lysander", "Sibyll", "Asmodai", "..."],
 "free_rungs": 1, "tier_claims": 33}
```

`free_rungs` counts 0-energy next rungs that drain the item — the trap in
practice: Lysander's "free" Bronze II spends the one Prey-Sight that Sibyll's
Digital Weapons composite needs (she must craft a second one for ~76 energy if
he goes first). `tier_claims` widens the count to every rung left inside each
hero's current tier. The footer reads:

```text
  bank contention - 89 item(s) whose next rungs claim more than the bank holds:
    Grand Skull Trophy         bank   1 / claims  14  (Tjark, Corrodius, Gulgortz, Angrax, +10 more) | 1 FREE rung(s) drain it
    Prey-Sight                 bank   1 / claims   8  (Lysander, Sibyll, Asmodai, Atlacoya, +4 more) | 1 FREE rung(s) drain it
    Oath Seal                  bank  10 / claims 106  (Aesoth, Godswyl, Atlacoya, Vitruvius, +13 more)
    ...
```

`next_step` reads the same field and marks its farm rows with `!` (§ "Next
step brief"). This *flags* contention — it does not sequence promotions; a
full multi-hero planner remains unmodelled.

#### The daily energy budget behind `Days`

`Days` is not just regen. The budget is regen **plus the extra energy claimed
each day**, all from `tacticus-drop-rates.json` → `energy`:

| Source | Energy | Blackstone | assumed |
|---|---|---|---|
| regen (1 per 5 min) | 288/day | — | always |
| watch ad | 60 | free | 1×/day |
| free daily-deal crate | 30 | free | 1×/day |
| 25 BS bundle | 60 | 25 | 1×/day |
| 50 BS bundle | 100 | 50 | 1×/day |
| 110 BS bundle | 100 | 110 | 1×/day |
| **total** | **638** | 185 BS | |

That lives under `energy.extra.sources`, each entry carrying `energy`,
`bs_cost` and `per_day` — change `per_day` to 0 to drop a source, or raise it
if one is repeatable, and every `Days` figure and footer line follows. The
per-blackstone value order is `25 BS` (0.42 BS/energy) → `50 BS` (0.50) →
`110 BS` (1.10), so if you ever ration it, drop the 110 BS bundle first. Past
that tier buying the item outright beats buying energy (see
`tacticus-shop-prices.json`).

With 638/day: one rung each for the whole roster is **71.4 days**, clearing
everyone's current tier is **249.0 days** — and the roster-wide total is the
sum of independent per-character inventory draws, so a real multi-character
plan takes longer. Energy is also not the same as calendar time: node attempt
caps (10/day standard, 6/day elite) and the event rotation still apply.

Honest limits, printed in the footer:

1. **Calgar is unpriceable** — his entire grid plus `upgDmgE005` are marked
   "Coming soon" with no drop and no recipe. He shows `?` and sorts last. The
   11 machines of war are excluded outright (see above), so they no longer pad
   this list.
2. **A priceable next rung can still sit under an unreleased one.** `blocked`
   in `--json` means *some* rung in the tier is unreleased (so `TierE`/`Days`
   read `?` and the row drops out of the tier total); `next_blocked` means the
   rung you are on cannot be valued at all. Only the second earns a place in
   `[+N unpriceable]` and the `blocked - no drop and no recipe yet:` line.
3. **Mythic parts drop only in timed event campaigns**, so that share of the
   total (1,608 energy) is only available some of the time — the `Note` column
   says `events`.
4. **Each row prices the inventory independently.** Promoting several characters
   at once costs *more* than the printed total: they compete for the same
   components.
5. **Gold is not modelled, and XP is out of scope here.** Applying upgrades
   costs gold (Common 10 … Mythic 2000), and grid row 2 needs the character's
   level to hit `xpLevelRequirements` — both can block a promotion the items
   allow. The XP half has its own tool, `xp_gate.py` (§ "XP gate report" below).

`--estimate` keeps the old shortcut: one rarity guessed from the target rank,
one flat rate, no recipes, no inventory (`tacticus-drop-rates.json` only, so it
also works without `research/`). It is ~10× optimistic — 4,362 vs 45,575 for the
whole roster — and its footer says so.

Both totals are snapshots: they move every time `tacticus-player.json` is
refreshed (spending inventory raises them, earning items lowers them).

## Per-item shop deal check

`item_value.py` answers "is this shop price any good?" for one item, using
the same exact price model as `--energy`. Human-readable output uses item
**names**; raw ids appear only in `--json`, the `(id …)` header suffix, and
the last column of the dictionary below.

No arguments = a static dictionary of all 557 catalog items, one row each,
built for fzf:

```bash
python3 item_value.py                 # the whole dictionary
python3 item_value.py | fzf           # drill down
python3 item_value.py | grep -i blood # plain grep works too
```

Columns: rarity, stat, `craft`/`farm`, how many you own, farm energy, fair
BS floor, `SHOP` (the typical Daily Deals single for that rarity — Common
uses the 5 BS chest), `VERDICT` (`SHOP` ÷ fair floor, graded on the same
scale), days, note. Given a query (item name **or** id, partial match,
`_`/`-` count as spaces) it prints that item's components (flat — name ×
count, no recursion; every component has its own dictionary row), the
leaf-by-leaf farm table, and with `--price` a verdict:

```bash
python3 item_value.py grand_strategy --price 540
python3 item_value.py "Grand Strategy" --price 540 --qty 2 --json
python3 item_value.py upgHpL017C --price 540 --spender frugal  # stricter scale
```

```
Grand Strategy  [Legendary / Health / crafted (composite)]  (id upgHpL017C)
  components (crafted = has its own recipe, [owned N] = in inventory; ...):
       5 x Secret Agenda          Epic             [owned 4]
       1 x Battle Plan            Rare, crafted
       2 x Box of Ammo            Rare, crafted
  ...
  fair floor: 299.0 E x 0.417 BS/E = 125 BS   (full craft, inventory ignored)
  delta:      your stock covers 96.9 E (40 BS) -> net farm 202.1 E = 84 BS
  time: 0.5 days full-craft / 0.3 net of stock at the 638/day budget
  shop offer: 540 BS for 1
    fair floor  = 299.0 E x 0.417 BS/E = 125 BS  (full craft, inventory ignored)
    delta       = stock covers 96.9 E (40 BS) -> net 202.1 E = 84 BS (6.43x)
    scale       STRONG BUY<=0.5x | BUY<=1.1x | FAIR<=2x | OK<=3.5x | OVERPRICED<=5x | STRONG AVOID>5x
                (moderate spender profile - energy.spender in tacticus-drop-rates.json; override with --spender/--threshold)
    you pay     = 540 BS = 4.32x floor  ->  OVERPRICED - only if you need it now
```

The floor is judged against the **full craft** — farm energy from an empty
inventory × 0.417 BS/E (the cheapest refill) — so the verdict compares the
shop price to the total cost of making the item from scratch, not to what
your stock happens to cover. The `delta` lines show that stock contribution
separately (`--json` carries `total_energy`, `delta_energy`, `fair_basis`).
The sample numbers move with the inventory snapshot. The verdict is a 6-step
scale computed on the full-craft floor — `STRONG BUY, BUY, FAIR, OK, OVER-
PRICED, STRONG AVOID` — and the whole scale is personal: `energy.spender` in
`tacticus-drop-rates.json` picks a profile (**frugal / moderate** (default)
**/ generous**), or override per run with `--spender`/`--threshold`. The upper
half follows that profile's ceiling (**2.5× / 5× / 8×**) and the lower steps
move with it too (frugal `≤0.5/1/1.5×`, moderate `≤0.5/1.1/2×`, generous
`≤2/3/4×`), so `STRONG BUY` only ever appears on a generous profile, while
frugal never calls anything at or above the floor a buy. The floor itself
always stays the cheapest refill (0.417 BS/E). The lenient lower half is
deliberate: BS spent on cheap items is roster growth — arguably better value
than 300 BS shard pulls — so a 5 BS common at 2.5× reads `OK`, while a
220 BS rare at 6.5× reads `STRONG AVOID`. These are **signals, not mandatory
buys**: the grade doesn't know whether the item unlocks a rank-up today that
energy couldn't reach in time. An ambiguous query lists the candidates and
exits 1.

Income side (kept in `tacticus-shop-prices.json` → `blackstone_income`): the
$4.99 Daily Shipment subscription is 2,000 BS per 30 days, promo codes another
~180–350 BS in a typical month (~2,100–3,400 BS/month total before salvage) —
while the daily refill habit in `energy.extra` costs 185 BS (~5,550/month), so
refills outspend the subscription and item buys come out of the promo /
salvage surplus. Buy signals land best in a good promo month.

Gold isn't modelled and days ignore attempt caps — the same caveats as the
report above.

## XP gate report

Items are only half of a rank-up. Every grid cell also needs the unit's hero
**XP level** at or above `xpLevelRequirements[rank][cell]`, capped by the rarity
`maxXpLevel` — that is the gate `xp_gate.py` reports. It never prices items, so
the two tools stay separate concerns (this one answers "books or nothing?",
`rank_up_report.py --energy` answers "which items and how much energy?").

```bash
python3 xp_gate.py                   # everything, grouped by status
python3 xp_gate.py --only blocked    # just the book-blocked characters
python3 xp_gate.py --only maxed      # at the rarity XP cap (books useless)
python3 xp_gate.py --json            # machine-readable
```

```
=== XP gate === snapshot 1790622091 | codeacrobat power level 60
book stock: 1x xpLegendary (12500 XP), 4x xpEpic (2500 XP), 18x xpRare (500 XP), 39x xpUncommon (100 XP), 155x xpCommon (20 XP) = 38,500 XP  (ONE shared pool)
guild shop at PL60: xpMythic 1500 gc, xpMythic 1500 gc  (other book offers are power-locked)

--- XP BLOCKED - the grid needs a higher level, books are the gate [43] --------
  Character            Current       Next           Grid  Lvl  Need     Gap XP  Books
  Imospekh             Diamond II    Diamond III     5/6   49    50     82,431  stock + 1x xpMythic (1500 gc)
  Azrael               Diamond III   Adamantine I    5/6   54    55    285,656  stock + 4x xpMythic (6000 gc)
  Haarken              Adamantine I  Adamantine II   0/6   55    60  1,620,230  stock + 26x xpMythic (39000 gc)

--- MAXED - at the rarity XP cap, so books cannot help [5] --------------------
  Calgar     Adamantine II  Adamantine III   0/6   60    65          -  needs lvl 65 > cap 60
  Titus      Diamond III    Adamantine I     0/6   50    55          -  needs lvl 55 > cap 50

--- XP OK - every open cell's level requirement is already met [58] ------------
  ...  XP is not their problem - items are: rank_up_report.py --energy

roster: 106 characters | blocked 43 | maxed 5 | ok 58 | full 0
```

### The four statuses

| status | meaning |
|---|---|
| `xp-blocked` | some open cell needs a higher `xpLevel` — a book fixes this. `Gap XP` = `xpLevels[need − 2] − xp` for the hardest open cell, so `Need` is the level that matters, not each cell separately |
| `maxed` | the hardest open cell needs more than the rarity `maxXpLevel`, so books **cannot** help (Calgar needs 65, Mythic caps at 60). Printed with no gap and no book list — just the cap and the level it would need |
| `xp-ok` | every open cell's requirement is already met: XP is not their problem, items are |
| `grid-full` | all 6 cells filled, nothing left to gate (no such unit in the current snapshot) |

Rows are sorted by gap inside each group — cheapest fix first. The 11 machines
of war are excluded (no rank grid, they level abilities).

### How `Books` is calculated

Owned `inventory.xpBooks` first (free apart from the apply-gold), largest book
first, then the guild-shop XP offers actually **unlocked at your power level** —
at PL60 only the Mythic Grimoire (62,500 XP / 1,500 guild credits) is
reachable; every smaller offer is power-locked below. `apply_gold` and
`guild_credits` for the whole plan land in `--json`.

**The stock is one shared pool.** Each plan assumes it gets *all* of it, so two
plans printed together cannot both be paid from stock — the footer says so too.

It needs `research/datamine/gameconfig142.json` (the XP tables exist only in the
raw datamine — the planner has no XP data) and runs in ~0.2 s. XP is one of
three rank-up gates: items, XP, ascension.

## Gear parity report

Equipment is a third axis entirely: every hero wears **3 slots** of gear
(slot 1 = crit, slot 2 = defensive or block, slot 3 = booster), rarities
Common → Mythic, levelled with salvage (dust) + gold. The practical goal most
players actually run is **everything at Legendary or better** — that is also
what the shop's green circle means: a Legendary-or-higher hero still wearing a
sub-Legendary piece.

`gear_report.py` answers *"who is still short, and can my spare gear cover it?"*
It reports parity and a fill plan; it never prices levelling, power deltas or
salvage farming (separate concerns).

```bash
python3 gear_report.py                 # full roster table, pipe to fzf
python3 gear_report.py haarken         # one hero: slots, candidates, fill plan
python3 gear_report.py --target Mythic # same view against a higher rung
python3 gear_report.py --json          # machine-readable
```

```
=== Gear parity vs Legendary === snapshot 1790622091 | codeacrobat | green = Legendary+ hero with a sub-Legendary slot
  Character            Faction            Rank  Rarity      S1   S2   S3  Relic                   Below  Green   Fill
  Gulgortz             Orks                 16  Mythic     M7R   L6   M1  Headwoppa's Killchoppa      0      -      -
  Haarken              BlackLegion          18  Mythic     M7R   L6   M1  Helspear                    0      -      -
  ...  (green heroes first, then most-missing)

relics: 2 worn (Gulgortz Headwoppa's Killchoppa, Haarken Helspear) | 6 spare in inventory (R_Block_DaemonfleshPlate, ...)

roster: 106 | green 5 | below Legendary 43 | slots below 120 | filled from spares 24 | still to buy 96
caveat: buy = 499 BS each in Daily Deals (random roll, 2/day), 715 in the crusade shop - verdicts are signals, not orders; ...
```

### Columns

| column | meaning |
|---|---|
| `S1`–`S3` | what is worn: rarity letter + level (`M7` = Mythic 7), suffixed `R` when the piece is a **relic** |
| `Relic` | the worn relic's name (only 2 heroes have one) or `-` |
| `Below` | how many of the 3 slots are under `--target` |
| `Green` | `yes` when the hero's own rarity ≥ target but at least one slot is below it — the shop green circle, computed here (the server sends no such flag) |
| `Fill` | how many of those gaps the **spare pool** can cover after the greedy allocation |

Rows sort green first, then most-missing, then hero rarity. `--target` retunes
the whole view (`Common` … `Mythic`); the green rule follows it too, so
`--target Mythic` lists every hero not already in full Mythic gear.

### Variants & preferences

Gear of one type comes in **lines that differ in shape, not in rarity** —
each hero's detail view tags them (`variant`, also in `--json`):

| slot | variants (level-1 stats from the catalog) |
|---|---|
| crit | `crit35 gun` (49 items) vs `crit20 knife` (11), plus `crit25` (15), `crit30`, `crit40` outliers |
| defensive | `hp+armour` (34 pieces) vs `armour` (6 — the "Plated Greaves" line) |
| block | chance lines: `block20` Refractor / `block25` Power / `block30` Force / `block35` Iron Halo |
| booster | `crit+N` / `block+N` — bonus size is bound to rarity, so there is no choice to make |

Chance **never scales with level or rarity** — only the damage side does, so
the tag at level 1 stays true at level 11.

Each detail view then prints one `pref (community):` line, derived from
`units.lineup.<id>.weapons[].hits` and `.traits`:

- **crit → knife** for 1-hit heroes (all damage on one crit), **gun** for 3+
  hits (crit chance compounds: a hit only crits if the previous one did),
  *marginal* at 2 hits. **ActOfFaith** heroes (the 5 Sisters) are always
  knife. Crit-chance buffers (Howl, Aethana, Eldryon) push back toward knife —
  that buff is not data-detectable, so the line only reads the hero.
- **armour → hp+armour** for everyone except **MkXGravis** heroes (Bellator,
  Calgar, Burchard, Nubari), whose trait already grants HP: they prefer pure
  armour.
- **block → highest chance line** (35 Iron Halo); the block damage side is
  rarity-scaled anyway.

Both are **signals, not orders** — they say which shape to *want*, not what
to buy or whom to strip.

### Spares, buys and the shuffle caveat

`inventory.items` is the only gear you can freely move — the pieces heroes
**wear are per-unit copies** (no owner field links two heroes' identical
items), so the tool plans from spares only and says so. Allocation respects
slot type, **faction locks** (from the planner catalog; an empty list =
unrestricted) and **relic slots** (`itemSlotsRelic` is an *index* into
`itemSlots`, so a relic may only fill that one slot, and relic items are
unit-locked via `allowedUnits`).

Whatever the pool can't cover shows up as `still to buy`, priced from the
config: **499 BS** per Legendary in Daily Deals (random roll over the rarity's
loot table, max 2/day, and the deal hides while the hero already wears
Legendary) or **715 crusade currency** in the crusade shop — same ladder
15/30/70/215/715/1785 for Common → Mythic; below-Legendary gear is also sold
for gold. `--json` carries `buy_bs` and `crusade_price` per slot. Verdicts are
signals, not orders.

### Relics

Relics are **unit-unique Mythic items** (`isRelic` + `allowedUnits`, ids
`R_*`, 10 levels, mythic-dust costs) — not a rarity of their own: in the player
data they read as plain `Mythic`. Exactly two are worn (Gulgortz's Headwoppa's
Killchoppa, Haarken's Helspear, both slot 1) and six sit as spares. Because a
relic spare lacks a `rarity` field, its rarity comes from the catalog —
guessing from the id would mis-read `R_Booster_Crit_…` as Common. The full
table prints them in the `Relic` column and a footer line; a hero's detail view
flags `[RELIC, relic-slot]` on the slot that can accept one.

The tool needs `research/` (planner equipment catalog for faction/relic locks)
and `research/datamine/gameconfig142.json` (slot layout, rarity, crusade
prices). It excludes the 11 machines of war — they have no equipment slots.

## Ability gate report

`ability_gate.py` — abilities level up with **gold + alliance badges**, and
this report answers which heroes are blocked on badges, by how much, and where
the real gap is (roster-wide demand vs the one shared stock per alliance).

```bash
python3 ability_gate.py                      # full roster, grouped by status
python3 ability_gate.py --only blocked       # just the badge-blocked heroes
python3 ability_gate.py lysander             # one hero: per-ability cost + pool
python3 ability_gate.py lysander --json      # unit + pools envelope
```

```text
=== Ability gate === snapshot 1790630247 | codeacrobat power level 60
badge stock (ONE shared pool per alliance): Chaos C434 U174 R300 E372 L434 M84 | Imperial C28 U25 R5 E156 L221 M47 | Xenos C62 U28 R97 E123 L220 M13
short pools: Imperial Uncommon 107 needed / 25 held (82 short) | Imperial Rare 31 needed / 5 held (26 short) | Xenos Uncommon 69 needed / 28 held (41 short)

--- BADGE BLOCKED - needs a rarity whose roster demand exceeds stock [55] ----------
  Character            Rank           Alliance  Lvls          Need           Gold
                                      --------  ------------  --------  ---------
  Bellator             Silver III     Imperial  33/21         R:2 E:4      14,000
  Dante                Gold I         Imperial  11/35         U:2 L:1      12,800
  Eldryon              Silver I       Xenos     11/28         U:2 E:1       8,300
  ...
roster: 106 characters | blocked 55 | capped 0 | ok 51
gold: 2,234,600 would cover every next upgrade (no gold balance exists in the snapshot - gold never gates here)
caveat: 'Need' is what ONE hero wants; the short-pools line above is the real gap
        (stock is ONE shared pool per alliance). 11 machines of war excluded - separate cost table.
```

| Column | Meaning |
|---|---|
| `Lvls` | this hero's ability levels (`33/21` = active 33, passive 21) |
| `Need` | badges **this one hero** still wants, in the short pools (`U:7` = 7 Uncommon) |
| `Gold` | gold for its next upgrades — informational only (no gold balance exists) |
| `short pools:` header line | **roster-wide** demand vs stock — the real gap; `Need` is one hero's share of it |

Cost ladder: `clientGameConfig.units.abilityUpgradeCosts` (59 entries,
**index = current level − 1**; an ability at 60 has no entry → `capped`).
Barriers are the rarity bands inside it (Uncommon runs 8→16, Rare 17→24, …).
Statuses: `badge-blocked` (needs a pool that is short), `capped` (nothing left
to price), `ok`. Stock is `inventory.abilityBadges` — **one shared pool per
alliance**, so the plans across heroes compete for the same badges.

One hero's detail view prices each ability's next level and marks the pool:

```text
=== Lysander (astarLysander) === rank Bronze I | Imperial | AdeptusAstartes
  TitanhammerSquad         L11 -> L12      800 gold + 2x Uncommon badge   [pool 107/25 SHORT]
  IconOfObstinacy          L16 -> L17    1,750 gold + 5x Uncommon badge   [pool 107/25 SHORT]
  need: U:7 (Imperial) | gold: 2,550
  verdict: badge-blocked - short pools: Uncommon 107/25 (-82)
```

Badge routes (from config research, see AGENTS.md): guild-war shop 385 GW
currency for 3 Uncommon drafts **daily — no season lock in the data**, the
default shop sells 99 gems/4 Uncommon drafts (max 5/day) plus per-rarity
singles, event shops run 10×Uncommon for 15 event currency, and you can
**craft up**: 3× lower-rarity badge of the same alliance + 1 forge badge +
gold. The guild shop (guild credits) and crusade shop sell no ability tokens.

## Power delta

`power_delta.py` — "cheapest first" systematically under-invests in high-rank
characters, because **power gained per promotion is what matters**, not
resource alone. This tool supplies the "power gained" half; correlating it
with `rank_up_report.py --energy` (power per energy) stays the caller's job.

```bash
python3 power_delta.py                # roster sorted by +Rung (biggest first)
python3 power_delta.py haarken        # one hero: the formula with its numbers
python3 power_delta.py --json
```

```text
=== Power delta vs next rank-up === snapshot 1790630247 | planner proxy (V1 port), not the in-game score
  Character            Rank           Stars  Fill     Power    +Rung     +Hp   +Dmg   +Arm
                                      -----  ----  --------  ------  ------  -----  -----
  Haarken              Adamantine I      12   0/6    48,300   +9,821    +475    +55   +100
  Tan Gi'da            Diamond III       10   0/6    37,817   +7,143    +426   +174    +79
  Titus                Diamond III        9   0/6    34,329   +6,786    +476   +129   +114
  ...
  Calgar               Adamantine II     11   0/6    73,249       +0 END       -      -      -

roster: 106 characters | ladder-end: 1 | biggest rung: Haarken +9,821 power
caveat: planner proxy - the wiki confirms the in-game formula is unknown. The delta is
        hero-agnostic (heroes differ only by stars/filled cells - compare +Hp/+Dmg/+Arm
        per hero). Gear, future ability levels and XP are not in the delta.
        Correlate with `rank_up_report.py --energy` outside this tool (power per energy).
```

| Column | Meaning |
|---|---|
| `Stars` | ascension steps (each star = +10% base stats) |
| `Fill` | cells already applied toward the next rank-up |
| `Power` | the hero's current proxy power (attribute + ability) |
| `+Rung` | power bought by **filling the rest of this grid** — `+0 END` = ladder finished |
| `+Hp/+Dmg/+Arm` | the grid's flat stat grants over the remaining empty cells (hero-specific — this is how heroes differ) |

**No in-game formula is published** (the wiki's Power Score page says so
outright; neither Snowprint config nor the planner's C# has one), so the tool
ports Tacticus Planner's `combat-power.ts` (V1) verbatim: attribute power =
base × star coefficient × 1.25^rank with a 1/9 step per filled grid cell,
plus an ability term from rarity × summed ability-level coefficients. The
detail view prints the same arithmetic with the hero's own numbers.

The delta is **hero-agnostic** — every non-terminal hero at the same
stars/fill gains the same power from a rank-up, so the honest per-hero
comparison is the `+Hp/+Dmg/+Arm` grants (which come from the unit's own
`upgradesStatIncrease` matrix). Research facts worth knowing: each **star =
+10%** base stats (not "+20% per ascension" — that figure is the *ability*
stat multiplier across a rarity jump), rank stat growth is a flat **×1.25 at
every tier**, and gear/ability/XP gains are outside this delta.

The query form shows the whole derivation for one hero:

```text
=== Haarken (blackHaarken) === BlackLegion | snapshot 1790630247
rank Adamantine I -> Adamantine II | stars 12 (Mythic) | grid 0/6
power now: 48,300 = attribute 39,285 + ability 9,015
  attribute = 321.6813 x stars 2.2 x (1.25^18 + step/9 x 0)   [1.25^18 = 55.5112]
  ability   = 12.1142 x rarity 2.0 x sum(abilityCoeff)
    activeAbilities HeraldOfTheApocalypse L47 -> coeff 177.4
    passiveAbilities HeadClaimer L48 -> coeff 194.7
next rank-up: +9,821 power (attribute 39,285 -> 49,106), grid resets
  grid 0/6, empty cells [1, 2, 3, 4, 5, 6] -> +475 HP, +55 Dmg, +100 Arm (config grants, summed over those cells)
base lineup stats: Health 95, Damage 11, FixedArmor 20 | powerMultiplier 84
caveat: proxy, not the in-game score; base x rank x star stat composition is not derivable from config.
```

## Team comp roster

`team_roster.py` — some players are only strong in combinations, so a per-hero
score does not answer *"what can I actually bring?"*. The tool takes the
**curated comps** the community ships (terminus-maximus + cognitae, mirrored
by the planner at `research/.../Data/guild-raid-meta/guild-raid-comps.json`,
updated 2026-09-14) and checks each against your roster.

```bash
python3 team_roster.py               # all comps, fieldable first
python3 team_roster.py multi         # one comp in detail (partial id)
python3 team_roster.py --json        # machine-readable
```

```text
=== Team comps vs roster === snapshot 1790630247 | codeacrobat | source terminus-maximus-and-cognitae-guild-raid-meta (updated 2026-09-14)
  Comp         Signature          Core   Flex    MoW  Heroes  Verdict        Missing
                                 -----  -----  -----  ------  -------------  -------
  AdMech       Exitor-Rho        3/ 3  10/11  3/ 3   13/5  fieldable      
  Laviscus     Laviscus          1/ 1  8/10  2/ 2    9/5  fieldable      
  Neuro        Neurothrope       1/ 1  7/ 9  1/ 1    8/5  fieldable      
  Battlesuits  Re'vas            3/ 3  3/ 4  4/ 4    6/5  fieldable      
  Custodes     Kariyan           2/ 3  4/ 7  2/ 2    6/5  fieldable      Kharn
  Z'Kar        Z'Kar [MoW]       1/ 1  4/ 7  1/ 1    5/5  fieldable      
  Multi-Hit    Ragnar (MISSING)  2/ 3  5/ 7  2/ 2    7/5  NO SIGNATURE   Ragnar

comps: 7 | fieldable 6 | needs signature 1 | thin 0
caveat: curated community comps (terminus-maximus + cognitae), NOT a computed
        synergy score - no combo/bonus key exists in the game config. A team is
        5 heroes from signature+core+flex plus 1 machine of war; boss-by-boss
        recommendations live in research/.../guild-raid-meta/.
```

| Column | Meaning |
|---|---|
| `Signature` | the comp's identity hero — without it the comp is not that comp (`[MoW]` = it is a machine of war, `Z'Kar`) |
| `Core`/`Flex`/`MoW` | owned / total in each tier of the comp (core = fixed backbone, flex = interchangeable bench) |
| `Heroes` | distinct owned heroes in the pool — a team needs 5 |
| `Verdict` | `fieldable` (signature owned + ≥5 heroes) · `NO SIGNATURE` · `thin N/5` |
| `Missing` | signature + core heroes you do not own (deduped) |

The detail view lists the missing heroes by role, then every available owned
hero with its rank:

```text
=== Multi-Hit === source terminus-maximus-and-cognitae-guild-raid-meta (updated 2026-09-14)   [no signature]
  signature  Ragnar (spaceBlackmane) - MISSING
  core       2/3
  flex       5/7
  MoW        2/2
    missing  Ragnar (spaceBlackmane) [signature]
    missing  Helbrecht (templHelbrecht) [flex]
    missing  Kharn (worldKharn) [flex]
  available 7/5 heroes needed:
    Aun'Shi              Bronze II      core
    Eldryon              Silver I       core
    ...
  verdict: no signature - missing identity heroes: Ragnar
```

Notes: a comp's MoW count never gates the verdict (raids are playable
without one — the column still shows what you have). There is deliberately
**no numeric synergy score**: no `combo`/`synergy`/`teamBonus` key exists
anywhere in the game config, so one would be invented. Boss-by-boss
recommendations with efficiency ratings sit next door in
`guild-raid-meta-boss-*.json` and are left to the caller.

## Next step brief (the assembler)

`next_step.py` — the individual reports each answer exactly one question; this
one convenes all seven witnesses fresh every run and answers *"where am I
blocked, what should I do next?"*. It opens with an **EVENTS** block: live and
upcoming event windows (from `machine_hunt.py`'s `events[]`), a strategy
pointer for events that have a dedicated report, and an explicit note for
events that don't. It adds **no pricing logic of its own**
(its only arithmetic is `d_power / energy`): energy comes from
`rank_up_report`, power from `power_delta`, statuses from the gates, exactly
as the witnesses printed them.

```bash
python3 next_step.py            # the brief (~1.1 s)
python3 next_step.py --top 20   # longer farm list
python3 next_step.py --json     # machine-readable assembly
```

```text
=== Next step brief === snapshot 1790663693 | codeacrobat | PL60
witnesses: rank_up_report, xp_gate, ability_gate, power_delta, gear_report, team_roster

WHERE I'M BLOCKED - 87/106 heroes carry >=1 gate
  XP        45  rank-up grid needs a higher hero level
             Arjac           Bronze I      27 xp short (lvl 19->20) - covered by books in stock
             Re'vas          Bronze II     136 xp short (lvl 22->23) - covered by books in stock
             Ulf             Bronze I      156 xp short (lvl 19->20) - covered by books in stock
             Actus           Bronze II     195 xp short (lvl 22->23) - covered by books in stock
             Lucius          Bronze I      615 xp short (lvl 19->20) - covered by books in stock
             ... +40 more
  XP-MAXED  5   rarity cap below the requirement - terminal
             Calgar          Adamantine II rarity cap 60 < need 65 - terminal
             Ramus           Silver I      rarity cap 26 < need 29 - terminal
             Sarquael        Gold I        rarity cap 35 < need 38 - terminal
             Tan Gi'da       Diamond III   rarity cap 50 < need 55 - terminal
             Titus           Diamond III   rarity cap 50 < need 55 - terminal
  ITEMS     1   next rung unpriceable (coming-soon items)
             Calgar          Adamantine II next rung unpriceable (coming-soon items)
  BADGES    55  short alliance pool - blocks abilities only  (not a rank-up block)
             Arjac           Bronze I      short Uncommonx4 (shared pool)
             Re'vas          Bronze II     short Uncommonx2 (shared pool)
             Ulf             Bronze I      short Uncommonx4 (shared pool)
             Actus           Bronze II     short Uncommonx5 (shared pool)
             Parasite of MortrexBronze II     short Uncommonx4 (shared pool)
             ... +50 more
  GEAR      4   slot(s) below target rarity  (not a rank-up block)
             Morvenn Vahl    Silver I      1 slot(s) below target
             Neurothrope     Bronze II     2 slot(s) below target
             Sy-gex          Bronze I      1 slot(s) below target
             Typhus          Iron III      1 slot(s) below target
  TEAM      comp not fieldable: Multi-Hit (missing Ragnar) - unlock, not farmable

WHAT TO DO NEXT
  1) POOL PURCHASES - one shared pool per alliance; buying here unblocks every hero at once
     Imperial Uncommon  buy 69  (demand 107 / stock 38)
     Imperial Rare      buy 24  (demand 31 / stock 7)
     Xenos Uncommon  buy 41  (demand 69 / stock 28)
  2) APPLY XP BOOKS - 45 heroes are xp-blocked; all 45 covered - 26 of them need guild-shop books (132,000 gc), 303,805 gold to apply
  3) FARM - 56 actionable rungs ranked by power per energy (top 10)
     hero            next rung        energy   +power  pow/E  days
     Lysander        Bronze II             0      245   free     1  ! Prey-Sight
     Tjark           Bronze II             0      218   free     1  ! Grand Skull Trophy
     Asmodai         Bronze II            21      290   13.8     1  ! Prey-Sight, Fine Purity Seal...
     Corrodius       Bronze III           30      383   12.8     0  ! Grand Skull Trophy, Adamantium Ore...
     Forcas          Bronze II            21      218   10.4     1  ! Fine Purity Seal, Ceramite Lump
     Hascule         Bronze II            21      218   10.4     1  ! Fine Purity Seal, Ceramite Lump
     Baldr           Silver III           69      632    9.2     1  ! Tank of Promethium, Standard-Issue Tourniquet...
     Thothmek        Silver I             96      767    8.0     0  ! Advanced Filaments, Ancient Runes...
     Atlacoya        Bronze II            57      368    6.5     1  ! Prey-Sight, Oath Seal...
     Ammuk           Bronze II            40      245    6.2     0  ! Advanced Filaments, Sophisticated Material
     ! = spends bank stock other next rungs also claim - the first promoted takes it, the rest must craft
  4) GEAR - 4 heroes below Legendary target: Neurothrope (2), Morvenn Vahl (1), Sy-gex (1), Typhus (1); buy line 499 BS
  5) TEAM - Multi-Hit needs Ragnar (unlock, not farmable)

NEXT: buy 134 badges in the guild-war shop (unblocks 55 heroes' abilities) -> apply XP books to 45 heroes (26 need 132,000 gc of books, 303,805 gold) -> farm Lysander -> Bronze II (free, +245 power; drains Prey-Sight from the shared bank) -> fix gear on 4 heroes -> unlock Ragnar for Multi-Hit
note: assembled from the seven witnesses - energy/power/prices come from them
      unchanged (next_step.py computes only power-per-energy). Signals, not orders.
```

Notes:

- **A gate stops one activity, not everything.** Badges block *abilities*
  only — those heroes still appear in the farm list. XP / XP-MAXED / ITEMS
  block *rank-ups* — those leave it. Gear and team rows are buys/unlocks,
  never farm blocks.
- **Free rungs lead**: a rung whose energy is 0 (all leaves already in your
  inventory) prints as `free` and sorts first — Lysander above. The `!` marks
  rungs that withdraw **contested** bank stock (rank_up_report's `contention`,
  § "Shared-bank contention"); the free rungs are exactly the trap — they
  empty the shared pool other heroes' rungs were priced against.
- **Degradation**: every witness runs first; a failing hub tool aborts with
  every failure listed, a failing `team_roster` only drops the TEAM lines and
  prints a `WARNINGS` section (exit 0). `failed_sources` is in the JSON.
- `--json` = `{schema_version, meta{snapshot, player, power_level,
  witnesses, top, gated_heroes, roster, badge_gold_total, gear_target,
  gear_buy_bs, failed_sources}, gates[{gate, count, note, blocks_rankups}],
  blocked, purchases, books, farm, gear, team}` — each farm row carries
  `contested`, the bank items its rung would drain.
- Order = leverage: shared-pool purchases first (one buy unblocks many
  heroes), XP books next (stock first — the plan names any guild-shop books
  and its gold to apply), then the best
  power-per-energy rungs, then gear, then unlocks.

## Machine hunt event (mechanical kills)

`machine_hunt.py` — for events that pay per Mechanical-trait enemy killed.
Enemy trait from the planner's NPC catalog, per-node enemy lists and loot
from the campaign data, and `Att` = attempts left today for that exact node
straight from the player snapshot (daily cap 10 standard / 6 elite). Nodes
with 0 attempts left are hidden — `--all` shows them; `?` = event campaign,
which isn't in the snapshot. Ranked by event points per energy: the tracker
pays 3 pts per Standard/Mirror mechanical kill and 5 pts per Elite kill.

```bash
python3 machine_hunt.py                  # best points/E, exhausted nodes hidden
python3 machine_hunt.py --items          # balance: nodes that also drop your next-rank items
python3 machine_hunt.py --all            # include nodes with 0 attempts left
python3 machine_hunt.py indomitus-elite  # one campaign (substring)
python3 machine_hunt.py --json --top 5   # machine-readable
```

```text
machine hunt LIVE — ends 2026-10-06 08:00 UTC (2d 20h left)
Mechanical kills per energy (3 pts/kill std+mirror, 5 elite; Att = attempts left today)
  Pt/E Node      Mech/E  Pts Mech Foes    E  Att Type         Campaign
  12.0 I09          4.0   36   12   12    3    3 Standard     indomitus
   9.0 I12          3.0   27    9    9    3    3 Standard     indomitus
   8.0 I10          2.7   24    8    8    3    3 Standard     indomitus
```

The Node sits in column 2 (the action item — Pt/E is only the sort key).
Under `--items` the report ends with a totals footer: need summed over the
distinct leaves shown, per item and per top hero.

- Indomitus standard is all-Necron: 100% mechanical, and its early nodes
  are the cheapest energy anywhere (battles 1–5 free, 6–14 = 3E, 15–29 = 5E,
  30+ = 6E; Elite = 10E — a 3-attempt cap sits on the 3E band). Battles 1–14
  drop **no** upgrade loot (gold only), so they're pure event kills; the
  `--items` view ranks the nodes that double as rank-up farming instead.
- Zero pricing logic of its own: the wanted set comes from
  `rank_up_report --energy --json` (next rung only). Deliberately not an
  MCP tool and not a `next_step` witness — an event view, not a roster gate.

## MCP adapter (AI agents)

`tacticus_mcp.py` is a thin Model Context Protocol server (stdio, Python stdlib
only) that puts the CLI tools in front of an AI agent — OpenCode, Claude Code,
any MCP client:

| MCP surface | Backed by |
|---|---|
| tool `rank_up_report` | `rank_up_report.py --energy --json` (+ the `--estimate`/`--sort`/`--top` flags) |
| tool `item_value` | `item_value.py --json` (omit `query` for the whole 557-row dictionary) |
| tool `xp_gate` | `xp_gate.py --json` (pass `only` = `blocked`/`maxed`/`ok`/`full` for one status group) |
| tool `gear_report` | `gear_report.py --json` (pass `query` for one hero, `target` = `Common`..`Mythic`) |
| tool `ability_gate` | `ability_gate.py --json` (pass `query` for one hero, `only` = `blocked`/`capped`/`ok`) |
| tool `power_delta` | `power_delta.py --json` (pass `query` for one hero's formula breakdown) |
| tool `team_roster` | `team_roster.py --json` (pass `query` for one comp id fragment: `multi`, `zkar`) |
| tool `next_step` | `next_step.py --json` (pass `top` for a longer farm list; ~1.1 s run) |
| tool `player_refresh` | `update_player.py` — refreshes the local cache only; the API is read-only |
| resource `tacticus://config/drop-rates` | `tacticus-drop-rates.json` |
| resource `tacticus://config/shop-prices` | `tacticus-shop-prices.json` |

It is a **marshalling layer, not a second implementation**: each call
subprocesses the CLI and returns its `--json` stdout verbatim (the
`schema_version` envelope above). No pricing logic lives here, so the CLI
stays the single contract and the agent can never disagree with the terminal.
Failures come back as an MCP `isError` carrying the CLI's stderr.

Registered in the project's `opencode.json` (created with `opencode mcp add
tacticus -- python3 /home/user/workspace/tacticus/tacticus_mcp.py`; check with
`opencode mcp list`). In OpenCode's Code Mode the tools surface as
`tools.tacticus.rank_up_report(...)` / `tools.tacticus.item_value(...)` /
`tools.tacticus.xp_gate(...)` / `tools.tacticus.gear_report(...)` /
`tools.tacticus.ability_gate(...)` / `tools.tacticus.power_delta(...)` /
`tools.tacticus.team_roster(...)` / `tools.tacticus.next_step(...)` /
`tools.tacticus.player_refresh(...)`, and the
result already arrives **parsed** — read `.rows` / `.ratio` directly instead of
hunting for an MCP content wrapper.

## Web deployment (GitHub Pages + Cloudflare Worker)

The same reports run as a static site: **https://code-acrobat.github.io/tacticus-agent/**

```
browser ──POST /player──▶ Worker ──X-API-KEY──▶ api.tacticusgame.com
   │  next_step runs in-tab (Pyodide + bundle.tar.gz)
   └──POST /chat────────▶ Worker ──Bearer──────▶ Groq (openai/gpt-oss-20b)
```

| Piece | Role |
|---|---|
| **GitHub Pages** | Serves `web/index.html` + `browser_harness.py` + `bundle.tar.gz`. Static build only — there is no app server. |
| **Pyodide** (CDN, pinned v0.26.2) | Runs the real Python CLIs **in the visitor's tab**; the `next_step` brief is computed locally, never on a server. |
| **Cloudflare Worker** (`worker/worker.js`) | Two routes: `/player` = key pass-through to the game API, `/chat` = LLM relay over the precomputed brief. In-memory 30 req/min/IP, CORS for any origin. |
| **Groq** | The chat model — OpenAI-compatible, so the Worker needs no code to swap providers. |

**Key custody.** The API key lives in `sessionStorage`, travels through the
Worker **once** to the game API, and is never stored nor sent to the LLM. Chat
sees only the brief (hero names, gates, energy numbers) — and the page says so
in plain text above the form, with a link back to this repository.

**Worker secrets.** Three `wrangler secret put`s, no code change:

```bash
cd worker
printf '%s' 'https://api.groq.com/openai/v1' | npx wrangler secret put LLM_BASE_URL
printf '%s' 'gsk_...'                        | npx wrangler secret put LLM_KEY
printf '%s' 'openai/gpt-oss-20b'             | npx wrangler secret put LLM_MODEL
```

Secrets apply immediately — no redeploy. The worker URL is
`https://<worker-name>.<account-subdomain>.workers.dev` (here both are
`tacticus-agent`, which is why it looks doubled); it is **committed** as the
`WORKER` const in `web/index.html`, so update it in the same change that renames
the worker.

**Deploy.** Push to `main` → `pages.yml` re-fetches the gitignored `research/`
(planner pinned by `PLANNER_SHA`, gameconfig pinned by commit), runs
`make_bundle.py`, publishes the three files. One-time repo setting:
**Settings → Pages → Source: GitHub Actions**.

**Two traps worth knowing:**

- Re-running an *old* workflow run redeploys *that run's* artifact, so the site
  goes backwards in time. If it looks stale, push a new commit (an empty one
  works) — never rerun history.
- Changing Pages settings (or adding a custom domain) resets Source to "deploy
  from a branch" → Jekyll renders `README.md` instead and `bundle.tar.gz` 404s.
  Set it back to **GitHub Actions**.

**Run it yourself:** clone, `python3 make_bundle.py`, serve `web/index.html`
next to `browser_harness.py` + `dist/bundle.tar.gz`, `cd worker && npx wrangler
deploy`, then put your own three secrets.

## API key

Stored in `.tacticus_api_key` (no trailing newline). Use it directly:

```bash
curl -sS -H "X-API-KEY: $(cat .tacticus_api_key)" \
  https://api.tacticusgame.com/api/v1/player
```

⚠️ **The key is a bearer credential and is embedded in `swagger-ui.html`.**
This is now a git repo, and `.gitignore` already excludes both files — keep it
that way:

```
.tacticus_api_key
swagger-ui.html
```

Verify before committing: `git status` must never list either file.

After rotating the key in-game, update `.tacticus_api_key` and rerun
`python3 gen_swagger.py`.

## API surface

Four GET endpoints, all requiring `X-API-KEY`:

| Path | Scope |
|---|---|
| `/api/v1/player` | `Player` — details, 117 units, inventory, campaign/event progress |
| `/api/v1/guild` | `Guild` |
| `/api/v1/guildRaid` | `Guild Raid` (current season) |
| `/api/v1/guildRaid/{season}` | `Guild Raid` (specific season) |

Errors: `403 FORBIDDEN` (bad/missing key), `404 NOT_FOUND` (e.g. no guild),
`500 UNKNOWN_ERROR`. Responses include `metaData.lastUpdatedOn` — a Unix
timestamp of when the data was *actually* fetched from the game server.

The bundled key has scope `Player` only; the guild endpoints will return `403`.

## Troubleshooting

| Symptom | Cause / fix |
|---|---|
| "Failed to fetch" in the UI | Proxy not running — `./proxy_ctl.sh up`, then `./proxy_ctl.sh status` should print `up … HTTP 200`. Check `curl -i http://127.0.0.1:8124/` returns CORS headers. |
| `403` on Execute | Key missing/invalid. The UI injects it automatically; confirm with `curl -i -H "X-API-KEY: $(cat .tacticus_api_key)" http://127.0.0.1:8124/api/v1/player`. |
| Stale UI after editing | Hard reload: `Ctrl+Shift+R` (the spec is inline, so caching bites). |
| `Address already in use` | `./proxy_ctl.sh restart` — it stops the old process (even a stale one holding the port) and starts a fresh one. If you `pkill` yourself, use the bracket pattern `'tacticus_proxy[.]py'`: a plain `pkill -f tacticus_proxy.py` matches your own shell's command line and kills it. |
| `proxy.log` | Proxy logs to `/tmp/opencode/proxy.log`. |

## Verification

```bash
# preflight (what the browser sends before the real call)
curl -i -X OPTIONS http://127.0.0.1:8124/api/v1/player \
  -H 'Origin: http://127.0.0.1:8124' \
  -H 'Access-Control-Request-Method: GET' \
  -H 'Access-Control-Request-Headers: x-api-key'
# expect 204 + Access-Control-Allow-Origin: *

# the real call through the proxy
curl -i -H "X-API-KEY: $(cat .tacticus_api_key)" \
  http://127.0.0.1:8124/api/v1/player
# expect 200 + Access-Control-Allow-Origin: * + JSON body
```
