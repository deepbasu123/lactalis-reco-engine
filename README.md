# Lactalis B2B Personalized Recommendation Engine

A Databricks demo that replaces the manual monthly "Suggested for You" spreadsheet on the
MyLactalis B2B portal with an automated, personalized, fulfilment-safe recommendation engine,
and gives the internal sales and marketing team natural-language analytics over the results.

Built entirely on native Databricks: Unity Catalog, Delta, Databricks SQL, Foundation Model
API (`ai_query`), Genie, Metric Views, AI/BI (Lakeview) dashboards, and Databricks Apps.

![architecture](docs/architecture.md)

## What it does

**Two views in one app:**

1. **MyLactalis Storefront** (the B2B customer experience) - a branded ordering portal showing
   a customer's favourites plus a "Suggested for You" strip of up to 3 personalized
   recommendations. Each card carries an AI-written reason and a "why now" context chip.
2. **Engine Console** (the internal team's view) - segment explorer, the contextual signal
   panel (weather / fuel / calendar), the out-of-stock guardrail in action, a Unity Catalog
   governance and lineage callout, an embedded Genie chat, and KPI tiles linked to the
   Lakeview dashboard.

**How recommendations are generated:**

- **Candidate scoring (SQL):** segment-category affinity + peer purchase adoption, blended
  with contextual boosts (heatwave lifts flavoured milk for Petrol & Convenience, cold snaps
  lift cream / hot beverages, restaurants get cooking cream, institutions get
  nutrition-compliant lines, high fuel index lifts single-serve impulse buys, school holidays
  lift convenience add-ons).
- **Hard out-of-stock guardrail:** any SKU below its fulfilment threshold at the customer's
  distribution centre is *removed* (not deprioritized), directly protecting the order
  fulfilment rate.
- **Favourites protection:** recommendations never repeat items the customer already reorders.
- **AI rationale (Foundation Model API):** `ai_query` writes the one-line "why we picked this"
  copy per recommendation.

## Segments and data

Five B2B segments, ~80 accounts, a realistic Lactalis Australia SKU catalogue (Pauls,
Président, Galbani, Lactel, Parmalat, Siggi's), 12 months of orders, favourites, stock by DC,
and weather / fuel / calendar signals. Source-tagged to SAP (ERP) and Salesforce (CRM and
e-commerce) to tell the Unity Catalog lineage story.

## One-shot deployment into your own workspace

`deploy.py` reproduces the entire demo (Unity Catalog objects, data, engine, Genie, metric
views, and app) in a single command. It is idempotent: re-running rebuilds cleanly.

### Prerequisites

- A Databricks workspace on Unity Catalog with a **serverless SQL warehouse** and
  **Foundation Model API** access (the recommendation rationale uses `ai_query`).
- The **Databricks CLI** authenticated to that workspace
  (`databricks auth login --host https://<your-workspace> --profile <name>`), OR a
  personal access token.
- Permission to `CREATE CATALOG` (or pass an existing catalog you can write to).
- Python 3.9+ locally. No third-party Python packages are required.

### Run it

```bash
# Using a CLI profile (recommended)
python deploy.py --profile <your-profile>

# Or with an explicit host + token
python deploy.py --host https://<your-workspace>.cloud.databricks.com --token <PAT>

# Common options
python deploy.py --profile <p> \
    --catalog lactalis_catalog \
    --schema reco \
    --warehouse-id <sql-warehouse-id> \   # else the deployer auto-picks a serverless one
    --model databricks-claude-sonnet-4-5 \ # FMAPI endpoint for the rationale
    --app-name lactalis-reco-engine
```

Useful flags:

- `--skip-app` - build the data, engine, Genie, and metric views but not the app.
- `--skip-genie` - skip Genie space creation (the app degrades gracefully without it).
- `--as-of YYYY-MM-DD` - the demo "as of" date that drives which contextual signals fire
  (defaults to the seeded heatwave date so the weather beat works out of the box).

The script prints a summary with the catalog/schema, warehouse, Genie space id, app URL, and
an integrity report (customer/product/recommendation counts, and that the out-of-stock
guardrail and favourites-protection rules hold with zero violations).

### After deployment

1. **App service principal grants.** The app runs as a service principal. Grant it read access
   to the data and use of the warehouse:
   ```sql
   GRANT USE CATALOG ON CATALOG lactalis_catalog TO `<app-service-principal>`;
   GRANT USE SCHEMA  ON SCHEMA  lactalis_catalog.reco TO `<app-service-principal>`;
   GRANT SELECT      ON SCHEMA  lactalis_catalog.reco TO `<app-service-principal>`;
   ```
   Also give it `CAN_USE` on the SQL warehouse and `CAN_RUN` on the Genie space.
2. **Dashboard.** Import `dashboard/lactalis_dashboard.lvdash.json` through the workspace
   (Dashboards > import), point it at your warehouse, and publish. Set `DASHBOARD_ID` in the
   app config to embed it in the console.

## Local development

```bash
cd app
npm install
npm run build:frontend   # rebuilds frontend/dist (only needed if you change the UI)
DATABRICKS_CONFIG_PROFILE=<profile> \
  RECO_CATALOG=lactalis_catalog RECO_SCHEMA=reco \
  DATABRICKS_WAREHOUSE_ID=<id> \
  GENIE_SPACE_ID=<id> DASHBOARD_ID=<id> \
  node server/index.js
# open http://localhost:8000
```

### App environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABRICKS_HOST` | prod (injected) | workspace URL |
| `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET` | prod (injected) | service-principal OAuth (M2M) |
| `DATABRICKS_TOKEN` or `DATABRICKS_CONFIG_PROFILE` | local | local auth |
| `DATABRICKS_WAREHOUSE_ID` | yes | SQL warehouse (bound via the `sql-warehouse` app resource in prod) |
| `RECO_CATALOG` / `RECO_SCHEMA` | yes | Unity Catalog location (default `lactalis_catalog` / `reco`) |
| `GENIE_SPACE_ID` | optional | enables the embedded Genie panel |
| `DASHBOARD_ID` | optional | enables the embedded Lakeview dashboard |

## Project layout

```
lactalis-reco-engine/
  deploy.py              one-shot end-to-end deployer
  deploy_seed/           verified seed data (CSV) the deployer loads
  app/                   Node.js/Express + React (Vite) dual-view app
    server/              Express backend (SQL, auth, Genie, routes)
    frontend/            React frontend (pre-built into frontend/dist)
    app.yaml             Databricks Apps runtime config
  dashboard/             Lakeview dashboard definition (import + publish)
  docs/                  design doc, schema contract, architecture
```

## Notes

- All data is synthetic and source-tagged for the demo narrative; there is no live SAP
  or Salesforce integration.
- The recommendation rationale is precomputed at deploy time for a fast, deterministic live
  demo. Re-run `deploy.py` (or just the engine step) to regenerate.
