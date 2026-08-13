# Architecture

## Data flow (Unity Catalog medallion)

```
Source systems (simulated, source-tagged)
  SAP ERP ............ product catalogue, rep orders, stock by DC, customer master
  Salesforce CRM ..... customer segmentation, e-commerce order lines, favourites
  External signals ... weather, fuel price index, holiday calendar
        |
        v
  BRONZE  (bz_*)      raw, source-tagged, _source_file + _ingested_at
                      <- landed from deploy_seed/*.csv
        |
        v
  SILVER  (sv_*)      cleaned, typed, de-duplicated on natural keys,
                      in_stock recomputed from on_hand vs threshold
        |
        v
  GOLD    dim_customer, dim_product, dim_dc,
          fact_orders, customer_favorites, stock_by_dc,
          signal_weather, signal_fuel_index, signal_calendar
        |
        v
  ENGINE  reco_scored ....... every candidate scored (affinity + context boosts)
          reco_candidates ... top-3 per customer AFTER the hard out-of-stock guardrail
          reco_rationale .... FMAPI (ai_query) "why we picked this" + "why now"
          vw_reco_full ...... one row per surfaced rec (what the app reads)
          vw_oos_blocked .... recs removed by the guardrail (the fulfilment story)
        |
        +--> mv_sales_performance, mv_reco_performance  (governed Metric Views)
        +--> Genie space        (internal natural-language analytics)
        +--> Lakeview dashboard (campaign performance)
        +--> Databricks App     (storefront + engine console)
```

## Scheduled refresh

A Databricks Job, `[Lactalis] Medallion Refresh`, walks the whole medallion twice a day at
**08:00 and 16:00 Australia/Brisbane** (`0 0 8,16 * * ?`). It is four chained SQL warehouse
tasks, capped at one concurrent run so two rebuilds never race:

```
mutate_bronze -> promote_medallion -> score_reco -> write_rationale
```

| Task | What it does |
|---|---|
| `mutate_bronze` | Edits `bz_stock_by_dc` and the signal tables so the demo moves between runs |
| `promote_medallion` | `bz_* -> sv_* -> gold`, every seed table |
| `score_reco` | Rebuilds `reco_scored`, `reco_candidates`, `vw_oos_blocked`, `vw_reco_kpi_base` |
| `write_rationale` | Rule-based copy first, then `ai_query` over the top, then `vw_reco_full` |

The mutation is deterministic from the **Brisbane calendar day** (days since the epoch, mod 2),
so the 08:00 and 16:00 runs agree with each other and the story flips every night:

| | quiet day | heatwave day |
|---|---|---|
| DC-001 / DC-002 weather | Mild, 22.0C | Hot, heatwave, 40.7C |
| QLD fuel `index_vs_avg` | 0.98 (below the 1.05 boost) | 1.12 (above it) |
| Out of stock | SKU-0001, SKU-0011, SKU-0016 | SKU-0004, SKU-0034 |

Epoch days rather than day-of-year, because day-of-year 365 and day-of-year 1 are both odd,
so a day-of-year parity would sit still over New Year in a non-leap year.

The two stock groups alternate rather than move together, so the fulfilment guardrail always
has something to hold back, and it is a different SKU each day. Customers, products, orders,
favourites and the holiday calendar are never mutated.

`deploy.py` writes the four SQL files to
`/Workspace/Users/<you>/<app-name>-pipeline/` and upserts the job over the Jobs API. The SQL
is generated from the same Python that the one-shot deployment runs, so the scheduled job and
the deployment can never drift apart. `--skip-job` leaves the schedule out.

## Recommendation scoring

```
final_score = affinity_score + context_boost

affinity_score = 0.6 * peer_adoption          (share of segment peers who buy the SKU)
               + 0.4 * category_affinity       (segment's normalized appetite for the category)

context_boost  = sum of the applicable signal boosts on the "as of" date:
   +0.30  hot/heatwave weather  AND flavoured milk           (Petrol & Convenience impulse)
   +0.25  cold weather          AND hot beverage / cream
   +0.22  RESTAURANT            AND cooking cream
   +0.22  INSTITUTION           AND nutrition-compliant
   +0.15  high fuel index       AND single-serve  (P&C)
   +0.10  school holiday        AND single-serve  (P&C / GROCER)

Guardrails:
  - Favourites protection: never recommend an item already in the customer's favourites.
  - Institution safety: never recommend caffeinated products to schools/hospitals/aged care.
  - Hard out-of-stock: JOIN stock_by_dc, keep only in_stock = TRUE (remove, not deprioritize).
  - Cap: ROW_NUMBER() per customer ORDER BY final_score DESC, keep rank <= 3.
```

## App

- **Backend:** Node.js + Express. Queries the SQL warehouse (SQL Statements API). Dual-mode
  auth: service-principal OAuth (M2M) when deployed, PAT / CLI profile locally. Routes for
  customers, favourites, recommendations (`vw_reco_full`), segments, the out-of-stock console
  demo, Genie ask, and KPIs.
- **Frontend:** React (Vite), pre-built into `frontend/dist` so the Apps runtime serves it
  without a build step. Two views: MyLactalis storefront and the Engine Console.
- **Branding:** Lactalis blue `#004B85`, light blue `#5BC5F2`, navy `#3F4097`, gold `#E8C759`,
  Rubik typeface, official Lactalis logo.

## Databricks products used

Unity Catalog (governance, lineage, medallion) - Delta - Databricks SQL (serverless) -
Foundation Model API (`ai_query`) - Genie - Metric Views - AI/BI (Lakeview) dashboards -
Databricks Apps.
