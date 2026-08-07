# Architecture

## Data flow (Unity Catalog medallion)

```
Source systems (simulated, source-tagged)
  SAP ERP ............ product catalogue, orders, stock by DC, customer master
  Salesforce CRM ..... customer segmentation, account context
  Alright Commerce ... favourites, e-commerce order lines
  External signals ... weather, fuel price index, holiday calendar
        |
        v
  BRONZE  (bz_*)      raw, source-tagged           <- loaded from deploy_seed/*.csv
        |
        v
  SILVER  (sv_*)      cleaned, de-duplicated, typed
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
