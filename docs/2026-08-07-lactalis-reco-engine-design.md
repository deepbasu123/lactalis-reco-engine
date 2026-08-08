# Lactalis B2B Personalized Recommendation Engine — Design & Schema Contract

**Date:** 2026-08-07
**Author:** Deep Basu (Databricks SA)
**Workspace:** https://fevm-lactalis.cloud.databricks.com (profile `fe-vm-lactalis`)
**Catalog:** `lactalis_catalog`  •  **Schema:** `reco`
**Warehouse:** `84b2d6209ac2f455` (Serverless Starter, path `/sql/1.0/warehouses/84b2d6209ac2f455`)

## Purpose

Replace MyLactalis's manual monthly "Suggested for You" spreadsheet (wired to SAP +
Salesforce) with an automated, personalized,
fulfillment-safe recommendation engine — and let the internal sales/marketing team
interrogate performance in natural language via Genie.

**Explicitly NOT** a customer-facing chatbot. The customer sees a recommendation *feed*.
Genie is the *internal* analytics surface.

## App shape — dual-view Databricks App (Node.js/React + Express)

- **View 1 — MyLactalis Storefront (customer):** branded favorites page + "Suggested for
  You" strip (max 3 cards), each card with an AI "why we picked this" line and a "why now"
  context tag. Segment/account switcher to demo different personas.
- **View 2 — Engine Console (internal):** segment explorer, signal panel stepping through
  the 3 hero moments (weather, OOS guardrail, segment personalization), UC
  governance/lineage callout, embedded Genie chat, KPI tiles + link to Lakeview dashboard.

## Databricks products (all native)

| Product | Role |
|---|---|
| Unity Catalog | `lactalis_catalog.reco` medallion, source-tagged, lineage + governance |
| Delta + Databricks SQL (serverless) | storage + app query path (gold layer) |
| Foundation Model API (`ai_query`, `databricks-claude-sonnet-4-5`) | per-rec NL rationale, precomputed to gold |
| Genie | NL analytics space over gold (in-app) |
| Metric View | governed KPIs (upsell conversion, fulfillment rate) |
| AI/BI (Lakeview) Dashboard | campaign performance by segment/product |
| Databricks Apps | hosts Node.js app, SP OAuth auto-injected |

## Segments (all 5 from use-case doc)

`P&C` (Petrol & Convenience), `RESTAURANT` (Restaurants & Cafes),
`INSTITUTION` (Schools/Hospitals/Aged Care), `BAKERY`, `GROCER` (Independent Grocers).

---

## SCHEMA CONTRACT (authoritative — all workstreams build to this)

All tables in `lactalis_catalog.reco`. Medallion: bronze (`bz_*`, raw + source tag),
silver (`sv_*`, cleaned), gold (`dim_*`/`fact_*`/`signal_*`/`reco_*`, app-facing).

### Gold — app-facing tables

**`dim_customer`** *(source: Salesforce CRM + SAP master)*
| column | type | notes |
|---|---|---|
| customer_id | STRING | PK, e.g. `CUST-0001` |
| customer_name | STRING | business name |
| segment | STRING | one of the 5 codes above |
| segment_label | STRING | human label |
| dc_id | STRING | fulfilling distribution center, FK stock_by_dc |
| dc_name | STRING | |
| region | STRING | AU state (demo is AU-based, Brisbane HQ) |
| city | STRING | |
| profile_note | STRING | chef/nutritionist/venue context |
| account_tier | STRING | Gold/Silver/Bronze |
| source_system | STRING | `Salesforce` / `SAP` |

**`dim_product`** *(source: SAP catalog)*
| column | type | notes |
|---|---|---|
| product_id | STRING | PK, e.g. `SKU-0001` |
| product_name | STRING | |
| brand | STRING | Président, Galbani, Parmalat, Lactel, Siggi's, Ismail (regional), etc. |
| category | STRING | Milk / Cheese / Butter / Cream / Yogurt / Flavored Milk / Hot Beverage |
| pack_size | STRING | e.g. `250ml`, `2L`, `1kg` |
| unit_price | DOUBLE | AUD |
| is_flavored_milk | BOOLEAN | flags for signal logic |
| is_single_serve | BOOLEAN | |
| is_cooking_cream | BOOLEAN | |
| is_nutrition_compliant | BOOLEAN | school/aged-care compliant |
| is_hot_beverage | BOOLEAN | hot chocolate etc. |
| source_system | STRING | `SAP` |

**`fact_orders`** *(source: SAP (rep) + Salesforce (ecommerce))*
| column | type | notes |
|---|---|---|
| order_id | STRING | |
| customer_id | STRING | FK |
| product_id | STRING | FK |
| order_date | DATE | last ~12 months |
| quantity | INT | |
| line_revenue | DOUBLE | AUD |
| channel | STRING | `ecommerce` / `rep` |
| source_system | STRING | `Salesforce` / `SAP` |

**`customer_favorites`** *(source: Salesforce)*
| column | type | notes |
|---|---|---|
| customer_id | STRING | FK |
| product_id | STRING | FK |
| reorder_frequency_days | INT | typical reorder cadence |
| last_ordered_date | DATE | |
| source_system | STRING | `Salesforce` |

**`stock_by_dc`** *(source: SAP stock)*
| column | type | notes |
|---|---|---|
| dc_id | STRING | |
| product_id | STRING | FK |
| on_hand_units | INT | |
| threshold_units | INT | fulfillment threshold |
| in_stock | BOOLEAN | on_hand_units >= threshold_units (the hard guardrail) |
| source_system | STRING | `SAP` |

**`signal_weather`** *(contextual)* — `dc_id`, `signal_date DATE`, `temp_c DOUBLE`, `condition STRING` (Hot/Mild/Cold), `is_heatwave BOOLEAN`
**`signal_fuel_index`** *(contextual)* — `region STRING`, `signal_date DATE`, `fuel_price_aud DOUBLE`, `index_vs_avg DOUBLE` (>1 = above average)
**`signal_calendar`** *(contextual)* — `signal_date DATE`, `is_school_holiday BOOLEAN`, `is_public_holiday BOOLEAN`, `holiday_name STRING`

### Gold — engine output tables

**`reco_candidates`** — ranked, OOS-filtered recommendations (max 3 per customer)
| column | type | notes |
|---|---|---|
| customer_id | STRING | FK |
| product_id | STRING | FK |
| rank | INT | 1..3 |
| affinity_score | DOUBLE | 0..1 segment+history score |
| context_boost | DOUBLE | added by active signals |
| final_score | DOUBLE | affinity_score + context_boost |
| reco_type | STRING | `cross-sell` / `upsell` |
| trigger_signal | STRING | `weather` / `fuel` / `calendar` / `segment` (drives "why now") |
| in_stock | BOOLEAN | always TRUE here (OOS filtered out upstream) |

**`reco_rationale`** — FMAPI-generated copy, joined to `reco_candidates`
| column | type | notes |
|---|---|---|
| customer_id | STRING | FK |
| product_id | STRING | FK |
| why_text | STRING | "why we picked this" (<= ~140 chars) |
| why_now_tag | STRING | short context chip, e.g. "Heatwave in QLD" |
| generated_by | STRING | `databricks-claude-sonnet-4-5` |

**`vw_reco_full`** (VIEW) — one row per surfaced rec joining candidates + rationale +
product + customer, for the app to read in a single query.

### Engine logic (reco_candidates)

1. **Candidate pool:** products in customer's segment's top categories NOT already in their
   favorites (cross-sell) + higher-tier variants of favorites (upsell).
2. **affinity_score:** segment-category affinity × normalized peer purchase rate.
3. **context_boost:** applied from active signals on the demo "as-of" date, e.g.:
   - Hot/heatwave + `is_flavored_milk` + P&C → boost
   - Cold + `is_hot_beverage`/`is_cooking_cream` → boost
   - Restaurant + `is_cooking_cream` → boost; Institution + `is_nutrition_compliant` → boost
   - high fuel index → boost single-serve add-ons at P&C
4. **Hard OOS guardrail:** `JOIN stock_by_dc ... WHERE in_stock = TRUE` — removes, not
   deprioritizes. This is the fulfillment-rate KPI made visible.
5. **Rank + cap:** `ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY final_score DESC)` ≤ 3.

### KPIs (Metric View `mv_reco_performance`)

- **Upsell/cross-sell conversion rate** = orders of recommended SKUs ÷ recs surfaced
- **Order fulfillment rate** = fulfilled lines ÷ ordered lines (guardrail proof)

## Branding

- Logo (verified live SVG): `https://images.ctfassets.net/c4xiawjy3foe/1g0m0q8M8maOs0Pw1BpoxW/5bc69deb4244085e094c61a30c5f7436/Lactalis-logo.svg`
- Palette: primary `#004B85`, light-blue accent `#5BC5F2`, navy `#3F4097`, gold `#E8C759`,
  bg `#F5F5F5`, white `#FFFFFF`
- Font: **Rubik** (Google Fonts)
- Style: clean corporate, generous whitespace, rounded cards, subtle shadows, pill buttons.

## Auth (app)

Dual-mode: local dev uses `DATABRICKS_TOKEN`/profile; deployed uses injected SP OAuth
(`DATABRICKS_CLIENT_ID`/`DATABRICKS_CLIENT_SECRET`). SP needs USE CATALOG/SCHEMA + SELECT on
`lactalis_catalog.reco`, CAN_USE on the warehouse, CAN_RUN on the Genie space.

## Non-goals (YAGNI)

No real SAP/Salesforce integration (synthetic, source-tagged). No live
model training. No auth/login UI (segment switcher instead). No write-back to orders.
