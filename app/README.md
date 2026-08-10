# Lactalis B2B Personalized Recommendation Engine

A dual-view Databricks App that replaces MyLactalis's manual monthly "Suggested for You"
spreadsheet with an automated, personalized, fulfillment-safe recommendation engine, and
gives the internal sales/marketing team a natural-language analytics surface via Genie.

- **Storefront ("MyLactalis")** — the B2B customer experience: a branded ordering portal
  with an account switcher, a favorites (reorder) list, and a "Suggested for you" strip of
  up to three recommendation cards. Each card carries an AI "why we picked this" line and a
  "why now" context chip.
- **Engine Console** — the internal view: segment explorer, a stepped signal panel that
  narrates the three hero demo moments (weather, out-of-stock guardrail, segment
  personalization), a Unity Catalog governance/lineage callout, an embedded Genie query box,
  KPI tiles, and a link/embed to the AI/BI dashboard.

Stack: **Node.js + Express** backend serving a **React (Vite) + TypeScript** frontend. All
Databricks access (SQL and Genie) is proxied through the backend so credentials never reach
the browser.

## Architecture

```
app/
├── app.yaml                 Databricks Apps runtime config (command + env + resource binding)
├── package.json             Server package. Runtime dependency: express only.
├── server/
│   ├── index.js             Express app: health, config, static SPA, route mounts
│   ├── config.js            Env-driven config + fully-qualified table names
│   ├── auth.js              Dual-mode auth (OAuth M2M / static token / CLI profile)
│   ├── db.js                SQL Statements REST client; tolerates a missing gold layer
│   └── routes/
│       ├── data.js          /api/customers, /favorites, /recommendations, /segments, /kpis
│       ├── console.js       /api/console/oos-demo, /weather-signal, /segment-personalization, /lineage
│       └── genie.js         /api/genie/status, /api/genie/ask (Conversation API proxy)
└── frontend/
    ├── index.html           Loads Rubik from Google Fonts
    ├── vite.config.ts        Builds to dist/; dev-proxies /api to :8000
    ├── public/lactalis-logo.svg   Brand logo (bundled, not hotlinked)
    └── src/
        ├── App.tsx           App shell + view switcher + shared customer state
        ├── api.ts / types.ts  Typed client + contract types
        ├── styles.css / storefront.css / console.css   Design system
        ├── views/            Storefront.tsx, Console.tsx
        └── components/       RecoCard, FavoriteCard, SignalPanel, SegmentExplorer,
                              KpiTiles, LineagePanel, GeniePanel, Icons, ...
```

### Why the SQL Statements REST API (not `@databricks/sql`)

The backend reads the gold layer via the Databricks **SQL Statements REST API** using Node's
built-in `fetch`. This keeps the server's only runtime npm dependency as `express`, works
identically behind the Databricks Apps proxy, and needs no native/thrift build. The React app
is pre-built into `frontend/dist` and shipped with the source, so **nothing is built at deploy
time** in the Apps environment.

### Graceful degradation (important for the demo timeline)

The `reco_candidates`, `reco_rationale`, and `vw_reco_full` objects (and the rest of the gold
layer) are built by `deploy.py` before the app is created, so a normal deployment never sees
them missing. If they are dropped later, every data endpoint returns an **empty-but-valid**
shape (e.g. `{"customers": [], "count": 0}`) instead of an error, and the UI renders empty
states. No fake recommendations are hardcoded in the frontend; everything reads from Unity
Catalog, and the app picks the tables back up with no code change.

## Dual-mode authentication

`server/auth.js` resolves credentials in this order:

1. **Service Principal OAuth M2M** — `DATABRICKS_CLIENT_ID` + `DATABRICKS_CLIENT_SECRET`.
   These are injected automatically by the Databricks Apps runtime when deployed.
2. **Static bearer token** — `DATABRICKS_TOKEN` (a PAT or short-lived OAuth token).
3. **CLI profile fallback** (local dev only) — shells out to
   `databricks auth token --profile <profile>`.

Tokens are cached in memory and refreshed shortly before expiry.

## Environment variables

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `DATABRICKS_HOST` | prod: injected | _(none)_ | Workspace URL. In Apps it arrives as a bare hostname; `config.js` adds `https://`. |
| `DATABRICKS_WAREHOUSE_ID` | yes | _(none)_ | SQL warehouse that serves the gold layer. In Apps, bound via the `sql-warehouse` resource (`valueFrom`). |
| `RECO_CATALOG` | no | `lactalis_catalog` | Unity Catalog catalog. |
| `RECO_SCHEMA` | no | `reco` | Schema holding the gold/reco tables. |
| `GENIE_SPACE_ID` | no | _(empty)_ | Genie space for the in-app NL analytics box. When empty, the Genie panel shows a "not configured" state. |
| `DASHBOARD_ID` | no | _(empty)_ | AI/BI (Lakeview) dashboard id to embed. When empty, the dashboard panel shows a "not linked" state. |
| `DATABRICKS_CLIENT_ID` | prod: injected | — | SP OAuth client id (Apps runtime). |
| `DATABRICKS_CLIENT_SECRET` | prod: injected | — | SP OAuth client secret (Apps runtime). |
| `DATABRICKS_TOKEN` | local alt | — | Static bearer token for local dev. |
| `DATABRICKS_CONFIG_PROFILE` | local alt | _(none)_ | CLI profile used for the local token fallback. |
| `PORT` | no | `8000` | Listen port. Apps sets this dynamically. |
| `NODE_ENV` | no | — | `production` in `app.yaml`. |

`DATABRICKS_HOST` and `DATABRICKS_WAREHOUSE_ID` deliberately have no defaults. A wrong
warehouse id fails in a confusing way, so an unset one fails with a clear message instead:
the server still starts, and `/api/health` lists what is missing under `config_problems`.

The service principal needs: **USE CATALOG / USE SCHEMA + SELECT** on `lactalis_catalog.reco`,
**CAN USE** on the warehouse, and (for Genie) **CAN RUN** on the Genie space. `deploy.py`
grants all of these automatically.

## Local development

```bash
# From app/
npm install                 # installs express (server)
npm run build               # installs + builds the frontend into frontend/dist

# Run the production-style server (serves the built frontend + proxies Databricks).
# Local auth uses the CLI profile fallback:
DATABRICKS_CONFIG_PROFILE=<your-cli-profile> \
DATABRICKS_HOST=https://<your-workspace>.cloud.databricks.com \
DATABRICKS_WAREHOUSE_ID=<your-warehouse-id> \
PORT=8000 npm start
# open http://localhost:8000
```

`deploy.py` prints the warehouse id it used, and `GET /api/health` on the deployed app
reports the catalog and schema it is reading.

Front-end hot-reload during development (two terminals):

```bash
PORT=8000 npm run dev:server     # API on :8000
npm run dev:frontend             # Vite dev server on :5173, proxies /api -> :8000
```

Optionally set `GENIE_SPACE_ID` and `DASHBOARD_ID` to exercise those panels locally.

## Deploying to Databricks Apps

Use `deploy.py` in the repository root. It builds the data layer, creates the Genie space and
dashboard, creates the app, binds the warehouse resource, grants the service principal
everything it needs, uploads the source, deploys, and verifies the result:

```bash
python deploy.py
```

See the root `README.md` for the full walkthrough and the available flags.

`app.yaml` in this folder is a template. `deploy.py` generates the real one at upload time
with the Genie space id and dashboard id filled in. It runs `node server/index.js`, sets
`NODE_ENV=production`, binds the warehouse via the `sql-warehouse` resource, and sets the
catalog and schema. The frontend is pre-built and committed under `frontend/dist`, so the
Apps runtime installs npm dependencies but does not build the frontend.

`deploy.py` already sets `GENIE_SPACE_ID` and `DASHBOARD_ID`. Set them by hand only if you
created those objects yourself, or pass `--skip-genie` / `--skip-dashboard` to leave them out.

## API reference

| Method | Path | Returns |
|---|---|---|
| GET | `/api/health` | Liveness + auth mode + warehouse reachability |
| GET | `/api/config` | Non-secret client config (feature flags, dashboard URL) |
| GET | `/api/customers` | Account switcher list (`dim_customer`) |
| GET | `/api/customer/:id` | One customer profile |
| GET | `/api/customer/:id/favorites` | Reorder items joined to product detail |
| GET | `/api/customer/:id/recommendations` | Rows of `vw_reco_full` (max 3) |
| GET | `/api/segments` | 5 segments with live counts + characteristics |
| GET | `/api/kpis` | Conversion + fulfillment + reach, with numerators/denominators |
| GET | `/api/console/oos-demo` | Held-back (OOS) vs passing stock lines |
| GET | `/api/console/weather-signal` | Hot/heatwave rows + flavored-milk SKUs |
| GET | `/api/console/segment-personalization` | Recs grouped by recipient segment |
| GET | `/api/console/lineage` | Medallion + source systems + real source tags |
| GET | `/api/genie/status` | Whether Genie is configured |
| POST | `/api/genie/ask` | `{question}` -> answer text + SQL + result table |

Schema contract: `docs/2026-08-07-lactalis-reco-engine-design.md`.

## Branding

- Logo: bundled `frontend/public/lactalis-logo.svg` (served at `/lactalis-logo.svg`).
- Palette: primary `#004B85`, sky `#5BC5F2`, navy `#3F4097`, gold `#E8C759`, bg `#F5F5F5`.
- Font: Rubik (Google Fonts).
