# Lactalis Medallion Refresh Pipeline — Design

**Date:** 2026-08-14  
**Author:** Deep Basu (Databricks SA)  
**Repo:** `lactalis-reco-engine`  
**Related:** `docs/2026-08-07-lactalis-reco-engine-design.md` (schema contract)

## Purpose

Add a scheduled Databricks Job that keeps the demo medallion and recommendation engine fresh twice a day, in the same one-command `deploy.py` style as the rest of this app (no Asset Bundle).

Inspired by Quest’s scheduled scoring job (`0 0 */4 * * ?` in `databricks.yml`), adapted to Lactalis:

| | Quest | Lactalis (this design) |
|---|---|---|
| Cadence | Every 4 hours UTC | Twice daily: 08:00 and 16:00 `Australia/Brisbane` |
| Packaging | DAB (`databricks.yml`) | Jobs API upsert from `deploy.py` |
| Work | Score system tables → Lakebase | Mutate bronze → promote silver/gold → reco + `ai_query` |
| App read path | Lakebase / warehouse | Unchanged: gold + `vw_reco_full` via SQL warehouse |

## Goals

1. Materialize real bronze (`bz_*`) and silver (`sv_*`) layers; gold keeps today’s app-facing names.
2. Run a multi-task Job twice daily that mutates bronze enough for OOS / weather / fuel hero moments to visibly drift, then rebuilds gold + reco + FMAPI rationale.
3. Keep one-command deploy: `python deploy.py` still builds everything and registers the Job.
4. No DAB, no app frontend changes, no live SAP/Salesforce ingest.

## Non-goals

- Lakeflow Spark Declarative Pipelines / DLT for this iteration
- `databricks.yml` / Asset Bundles
- Changing the Node/React app contract
- Streaming / Auto Loader ingestion
- Mutating customer, product, orders, favorites, or calendar identities

---

## Architecture

### Deploy-time path

```
deploy_seed/*.csv
      │
      ▼
  bz_*  (bronze, source-tagged)
      │
      ▼
  sv_*  (silver, cleaned / typed / in_stock recomputed)
      │
      ▼
  gold  dim_*, fact_orders, customer_favorites, stock_by_dc, signal_*
      │
      ▼
  reco_scored → reco_candidates → reco_rationale (ai_query)
  views: vw_reco_full, vw_oos_blocked, vw_reco_kpi_base, …
      │
      ▼
  Upsert Job "[Lactalis] Medallion Refresh" (UNPAUSED)
```

### Scheduled path (twice daily)

```
mutate_bronze → promote_medallion → score_reco → write_rationale
```

All four are SQL warehouse tasks (`sql_task`) on the same warehouse the app uses, chained with `depends_on`, `max_concurrent_runs: 1`.

**How SQL gets into the Job:** Databricks `sql_task` cannot embed raw SQL in the job JSON. It accepts one of `query` / `file` / `dashboard` / `alert`. This design uses **`sql_task.file` with `source: WORKSPACE`**: `deploy.py` renders the four SQL files, uploads them to a workspace path (same Workspace Import API already used for the app), then points each task at that file. A SQL file task may contain multiple statements separated by semicolons, which is what makes a four-file pipeline possible. SQL Query objects are out of scope.

---

## Tables

### Bronze (`bz_*`)

Seeded once at deploy from the existing CSVs. Same columns as today’s gold seed schemas in `deploy.py` `SCHEMA`, plus retained `source_system` where present.

| Bronze table | Seed file |
|---|---|
| `bz_dim_dc` | `dim_dc.csv` |
| `bz_dim_customer` | `dim_customer.csv` |
| `bz_dim_product` | `dim_product.csv` |
| `bz_fact_orders` | `fact_orders.csv` |
| `bz_customer_favorites` | `customer_favorites.csv` |
| `bz_stock_by_dc` | `stock_by_dc.csv` |
| `bz_signal_weather` | `signal_weather.csv` |
| `bz_signal_fuel_index` | `signal_fuel_index.csv` |
| `bz_signal_calendar` | `signal_calendar.csv` |

### Silver (`sv_*`)

`CREATE OR REPLACE` from bronze each promote:

- CAST types explicitly
- Drop rows with null primary keys
- Deduplicate on natural keys (`dc_id`+`product_id` for stock, etc.)
- Recompute `in_stock = on_hand_units >= threshold_units` for stock

### Gold (app-facing, names unchanged)

Promote from silver into the same table names the app / Genie / dashboard already use:

`dim_dc`, `dim_customer`, `dim_product`, `fact_orders`, `customer_favorites`, `stock_by_dc`, `signal_weather`, `signal_fuel_index`, `signal_calendar`

Engine outputs stay as today: `reco_scored`, `reco_candidates`, `reco_rationale`, plus views.

---

## Mutation rules

Deterministic per **Brisbane calendar day** so both 08:00 and 16:00 runs on the same day produce the same bronze state (idempotent within the day), and the next day flips.

**Parity expression (required — do not use bare `current_date()`):** warehouse sessions default to UTC. The 08:00 Brisbane run is still the previous UTC calendar day, so a naive `current_date()` would flip parity between the two daily runs. Use:

```sql
datediff(date(from_utc_timestamp(current_timestamp(), 'Australia/Brisbane')), DATE'1970-01-01') % 2 AS parity
```

Epoch days, not `dayofyear()`: day-of-year 365 and day-of-year 1 are both odd, so a day-of-year parity would fail to flip over New Year in a non-leap year.

### 1. Stock (`bz_stock_by_dc`)

Two groups that **alternate** rather than move together. If all demo SKUs flipped in stock on the same day, the fulfilment guardrail would have nothing to hold back and the Engine Console's out-of-stock hero moment would be empty every other day. (Confirmed by testing: the seed only ships four out-of-stock rows, and all four are demo keys.)

| Group | dc_id / product_id | Out of stock when |
|---|---|---|
| A | DC-001/SKU-0001, DC-003/SKU-0016, DC-004/SKU-0011 | `parity = 0` (quiet day) |
| B | DC-001/SKU-0004, DC-001/SKU-0034 | `parity = 1` (heatwave day) |

Group B is the stronger story: both are flavoured milk at DC-001, the DC that fulfils every P&C customer, and they go out of stock on the same day the heatwave turns on.

Out of stock sets `on_hand_units = 0`; in stock sets `on_hand_units = threshold_units + 50`. Silver recomputes `in_stock` from those numbers. All other stock rows are left alone.

### 2. Weather (`bz_signal_weather`)

On the demo `as_of` date (same resolver as today: `MAX(signal_date)` from weather, or deploy `--as-of`):

- Targets: **DC-001** (primary; all P&C customers ship from here; seed heatwave row) and **DC-002** (secondary Mild→Hot flip)
- `parity = 0`: `condition = 'Mild'`, `is_heatwave = false`, `temp_c = 22.0`
- `parity = 1`: `condition = 'Hot'`, `is_heatwave = true`, `temp_c = 40.7`

### 3. Fuel (`bz_signal_fuel_index`)

On the same `as_of` date, region **QLD** (every P&C customer in the seed is QLD):

- `parity = 0`: `index_vs_avg = 0.98` (below 1.05 boost threshold)
- `parity = 1`: `index_vs_avg = 1.12` (above threshold)

### 4. Never mutate

`bz_dim_*`, `bz_fact_orders`, `bz_customer_favorites`, `bz_signal_calendar`.

---

## Job definition

| Field | Value |
|---|---|
| Name | `[Lactalis] Medallion Refresh` |
| Quartz cron | `0 0 8,16 * * ?` |
| Timezone | `Australia/Brisbane` |
| Pause | `UNPAUSED` |
| Concurrency | `max_concurrent_runs: 1` |
| Compute | SQL warehouse tasks → existing demo warehouse |

### Tasks

| `task_key` | Depends on | Responsibility |
|---|---|---|
| `mutate_bronze` | — | Apply stock / weather / fuel mutation SQL |
| `promote_medallion` | `mutate_bronze` | Bronze → silver → gold `CREATE OR REPLACE` |
| `score_reco` | `promote_medallion` | Rebuild `reco_scored`, `reco_candidates`, `vw_oos_blocked`, `vw_reco_kpi_base` (same SQL as `build_reco_engine` without rationale) |
| `write_rationale` | `score_reco` | Rule-based rationale first, then `vw_reco_full`, then `ai_query` over the top, then a blank-fill. Writing the deterministic copy first means a failed FMAPI call leaves the storefront with text rather than none |

Job / task `parameters` on each `sql_task` (and/or placeholders already substituted at upload time): `catalog`, `schema`, `model`, `as_of`. Prefer substituting into the uploaded SQL at deploy time so the warehouse statements are self-contained.

---

## `deploy.py` changes

1. **Seed into bronze** instead of writing gold tables directly from CSV.
2. **Shared promote + engine SQL** used by both the deploy-time path and the Job files (`pipeline/*.sql` templates with `{{catalog}}` / `{{schema}}` / `{{model}}` / `{{as_of}}` substitution, or equivalent Python builders that write the final SQL before upload).
3. Upload rendered SQL files to a workspace path under the app/source folder (e.g. `/Workspace/Users/.../lactalis-reco-engine/pipeline/`), then point each `sql_task.file.path` at them with `source: WORKSPACE`.
4. After first successful full build, **upsert Job** via Jobs REST API `2.1`: `GET .../jobs/list?name=...` → `POST .../jobs/create` or `POST .../jobs/reset` (body: `job_id` + `new_settings`). Note: Jobs delete/reset are **POST**, not HTTP DELETE — do not reuse `api.delete()` path-style helpers naively.
5. Deployment summary prints job id and schedule.
6. **`destroy`**: `POST /api/2.1/jobs/delete` with `{ "job_id": ... }` after looking up by name, alongside other resource teardown.
7. Keep the deploy body stdlib-only. (Superseded for auth: credentials are now resolved by
   `databricks-sdk`, matching databricks-quest, so `databricks auth login` works. The SDK is
   an optional import and the deployer still falls back to a token prompt without it.)

### SQL artifact layout

The four files are generated by `deploy.py` (not committed), so the scheduled job and the
one-shot deployment always run byte-identical SQL:

```
/Workspace/Users/<user>/<app-name>-pipeline/
  01_mutate_bronze.sql
  02_promote_medallion.sql
  03_score_reco.sql
  04_write_rationale.sql
```

---

## Error handling

- `max_concurrent_runs: 1` so overlapping scheduled + manual/deploy runs queue instead of racing on `CREATE OR REPLACE` (Quest uses the same guard for its DELETE+re-INSERT notebook).
- **Job vs. deployment race (found in live testing).** `max_concurrent_runs` only serialises the job against itself. A `deploy.py` run that straddles 08:00 or 16:00 rebuilds bronze with `CREATE OR REPLACE` while the job's `mutate_bronze` holds an `UPDATE` on the same tables, and Delta kills the deployment with `DELTA_CONCURRENT_APPEND.WHOLE_TABLE_READ`. `quiesce_pipeline_job()` therefore pauses the schedule and cancels in-flight runs before the rebuild, and the schedule is re-armed at the end (by the full-settings write in `ensure_pipeline_job`, or explicitly on the `--skip-job` path). A deployment that stops early leaves the schedule paused; the next run re-arms it.
- `write_rationale` keeps today’s behavior: on `ai_query` failure or blank rows, fall back to rule-based rationale so the app still has copy. Note: a warehouse SQL task cannot catch Python exceptions like `deploy.py`; the rationale SQL file should prefer a single `ai_query` statement, and deploy-time can still use the richer Python fallback. If FMAPI is unavailable in the Job run, document that the previous `reco_rationale` may remain until a successful run (or ship a second rule-based SQL file as an optional follow-up task later — YAGNI for v1 unless needed).
- If `mutate_bronze` fails, downstream tasks do not run (`UPSTREAM_FAILED`).
- Redeploy is idempotent: bronze reseed + promote + job upsert.

---

## Testing / verification

1. Deploy once; confirm `bz_*`, `sv_*`, gold, and `vw_reco_full` exist.
2. Confirm Job exists, schedule shows Unpaused, cron and timezone match.
3. Trigger a manual Job run; confirm tasks succeed in order.
4. Force parity flip (or temporarily override parity in SQL) and confirm:
   - `stock_by_dc.in_stock` changes for demo keys
   - `vw_oos_blocked` / console OOS endpoint reflects it
   - weather / fuel-triggered recs and `why_now_tag` can change
   - `reco_rationale.generated_by` is the FMAPI model when available
5. `destroy` removes the Job.

---

## Decisions log

| Decision | Choice |
|---|---|
| Scope | Full medallion + reco + `ai_query` |
| Cadence | Twice daily, not every 4 hours |
| Schedule | 08:00 and 16:00 `Australia/Brisbane` |
| Packaging | Lactalis-style Jobs API from `deploy.py` (Approach 1) |
| Bronze behavior | Mutate stock/signals each run (deterministic day parity) |
| Pipeline shape | Multi-task Job, `sql_task.file` + workspace-uploaded `pipeline/*.sql` |
| Gold names | Unchanged (app compatibility) |
| Parity clock | `datediff(date(from_utc_timestamp(current_timestamp(), 'Australia/Brisbane')), DATE'1970-01-01') % 2` |
