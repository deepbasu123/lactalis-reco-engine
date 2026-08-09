# Lactalis B2B Personalized Recommendation Engine

A Databricks demo that replaces the manual monthly "Suggested for You" spreadsheet on the
MyLactalis B2B portal with an automated, personalized, fulfilment-safe recommendation
engine, and gives the internal sales and marketing team natural-language analytics over the
results.

Built entirely on native Databricks: Unity Catalog, Delta, Databricks SQL, Foundation Model
API (`ai_query`), Genie, Metric Views, AI/BI (Lakeview) dashboards, and Databricks Apps.

---

## Deploy it into your own workspace

One command builds everything: the data, the recommendation engine, the Genie space, the
dashboard, the app, and every permission the app needs. There are no manual follow-up steps.

### What you need

1. **Python 3.9 or newer** on your machine. Nothing else is installed: no Databricks CLI,
   no Node.js, no `pip install`. The script uses only the Python standard library.
   (Tested on Python 3.9, 3.10, 3.13 and 3.14.)
2. **A Databricks workspace** with a SQL warehouse, and Unity Catalog enabled.
3. **A personal access token** for that workspace (instructions below).

### Windows: step by step

**Step 1 - Install Python (skip if you already have it)**

Download it from [python.org/downloads](https://www.python.org/downloads/) and run the
installer. On the first screen of the installer, tick **"Add python.exe to PATH"** before
clicking Install. That checkbox is easy to miss and is the single most common cause of
"python is not recognized" later.

To confirm it worked, open **Command Prompt** (press the Windows key, type `cmd`, press
Enter) and run:

```
python --version
```

You should see something like `Python 3.13.1`.

**Step 2 - Get the code**

On the GitHub page for this repository, click the green **Code** button, then
**Download ZIP**. Save it, then right-click the downloaded file and choose
**Extract All...**. Note where you extracted it, for example
`C:\Users\yourname\Downloads\lactalis-reco-engine`.

**Step 3 - Get your workspace URL and a token**

Your **workspace URL** is what you see in the browser address bar when you are signed in to
Databricks, up to and including `.com`. For example:

```
https://your-company.cloud.databricks.com
```

For the **token**, in Databricks click your avatar in the top-right corner, then:

**Settings** > **Developer** > **Access tokens** > **Manage** > **Generate new token**

Give it any comment, leave the lifetime as-is, and click Generate. **Copy the token
immediately**, because Databricks only shows it once. It looks like `dapi1a2b3c...`.

**Step 4 - Run the deployer**

In Command Prompt, change into the extracted folder and run the script:

```
cd C:\Users\yourname\Downloads\lactalis-reco-engine
python deploy.py
```

It will ask for your workspace URL and then your token. The token is hidden as you type or
paste it. That is deliberate, it is not frozen. Press Enter and it runs.

> **Paste tip:** in Command Prompt, right-click pastes. Ctrl+V may not work.

That's it. Leave the window open until it prints the deployment summary.

### macOS and Linux

Same thing, using `python3`:

```bash
git clone https://github.com/<your-org>/lactalis-reco-engine.git
cd lactalis-reco-engine
python3 deploy.py
```

### Running it without prompts

If you would rather not be prompted, pass the credentials directly:

```bash
python deploy.py --host https://your-company.cloud.databricks.com --token dapi1a2b3c...
```

Or, if you already use the Databricks CLI:

```bash
python deploy.py --profile <your-cli-profile>
```

---

## What happens when you run it

The script prints each step as it goes and finishes with a summary. A typical run takes
**5 to 10 minutes**, most of which is the Foundation Model writing recommendation copy and
the app container starting for the first time.

| Step | What it builds |
|---|---|
| `preflight` | Checks your credentials, picks a SQL warehouse, picks an available AI model |
| `data` | Creates the catalog/schema and loads 9 tables from the bundled CSV seed data |
| `engine` | Scores recommendations, applies the out-of-stock guardrail, writes AI rationale |
| `metrics` | Creates the governed metric views |
| `genie` | Creates the Genie space over the demo tables |
| `dashboard` | Creates and publishes the AI/BI dashboard |
| `app` | Creates the Databricks App, binds its SQL warehouse, uploads the code, deploys it |
| `grants` | Gives the app's service principal access to the catalog, schema, warehouse, Genie space and dashboard |
| `verify` | Checks the data, then calls the running app over HTTP to confirm it really works |

The `verify` step is the important one. It does not just check that objects exist, it proves
the app will actually work: that the service principal's grants resolved, that the app's own
queries return rows, that every recommendation has its written rationale, and that Genie
answers a real question.

```
[   verify] Backend checks (grants, app queries, Genie)
            PASS  service principal on catalog: USE_CATALOG
            PASS  service principal on schema: SELECT, USE_SCHEMA
            PASS  customer list query: 80 accounts
            PASS  recommendation query: 240 rows in vw_reco_full
            PASS  rationale populated: 0 missing
            PASS  out-of-stock console query: 4 SKUs held back by the guardrail
            PASS  service principal on Genie space: CAN_RUN
            PASS  Genie question: answered
```

If you deployed with a Databricks CLI profile instead of a token, the deployer can also call
the running app directly over HTTP and you get this stronger version instead:

```
[   verify] Live checks against the running app
            PASS  app health: warehouse reachable, auth oauth-m2m
            PASS  customer list: 80 accounts
            PASS  recommendations + rationale: 3 for CUST-0064, all with written rationale
            PASS  KPI tiles: 240 recs surfaced, conversion 65%
            PASS  out-of-stock console: 4 SKUs held back by the guardrail
            PASS  Genie question: answered with 5 rows
```

Both are a pass. Databricks Apps only accept OAuth tokens at their front door, so a personal
access token cannot call the app's API directly. That is a property of the platform, not a
problem with your deployment, and it has no effect on opening the app in a browser. The
deployer detects it, says so, and runs the backend checks instead.

If anything genuinely fails, the script says exactly what failed and exits with a non-zero
status. It never reports success on a half-finished deployment.

At the end you get a clickable app URL. Open it in the same browser where you are signed in
to Databricks.

**Re-running is safe.** Everything is idempotent: tables are recreated, and the app, Genie
space, dashboard and grants are updated in place rather than duplicated.

---

## What gets created in your workspace

| Object | Default name | Notes |
|---|---|---|
| Catalog | `lactalis_catalog` | Reused if it exists; see the permissions note below |
| Schema | `reco` | Holds 9 seed tables, the engine tables, and 6 views |
| Genie space | `Lactalis Recommendation Analytics` | Over 11 tables |
| Dashboard | `Lactalis Recommendation Engine Performance` | Created and published |
| Databricks App | `lactalis-reco-engine` | Node.js/React, runs as its own service principal |

Change any of them with `--catalog`, `--schema` and `--app-name`.

### Workspace permissions you need

The deployer needs your account to be able to:

- **Create a schema** in some catalog. It tries to create `lactalis_catalog` first. If you do
  not have `CREATE CATALOG` on the metastore (common in managed environments), it
  automatically finds a catalog you *can* write to and uses that instead, telling you which
  one it picked. Use `--catalog <name>` to choose explicitly.
- **Create a Databricks App.**
- **Create a Genie space.**
- **Use a SQL warehouse.**

The app's own service principal is granted everything it needs automatically:
`USE CATALOG`, `USE SCHEMA` + `SELECT` on the schema, `CAN_USE` on the warehouse, `CAN_RUN`
on the Genie space, and `CAN_READ` on the dashboard.

### Feature requirements

- **Unity Catalog** must be enabled.
- **Databricks Apps** must be enabled.
- **Genie** must be enabled.
- **Foundation Model APIs (pay-per-token)** are used for the recommendation rationale. The
  deployer checks which models your workspace actually has and picks the best available one,
  so it works in regions without Claude. If no chat model is available at all, re-run with
  `--no-rationale` to use rule-based rationale text instead.

---

## If something goes wrong

| What you see | What it means and what to do |
|---|---|
| `'python' is not recognized...` | Python is not on your PATH. Reinstall it and tick "Add python.exe to PATH", or use `py deploy.py` instead. |
| `Databricks rejected the credentials (401)` | The token is wrong, expired, or belongs to a different workspace. Generate a fresh one. |
| `Could not reach https://...` | Check the workspace URL. If you are behind a corporate proxy, set it first: `set HTTPS_PROXY=http://proxy:port` (Windows) or `export HTTPS_PROXY=...` (macOS/Linux). |
| `This workspace has no SQL warehouse` | Create one in **SQL** > **SQL Warehouses** > **Create SQL warehouse** (Serverless is recommended), then re-run. |
| `Cannot create catalog ... no CREATE CATALOG` | Not a failure. The deployer falls back to a catalog you can write to and carries on. Pass `--catalog <name>` to choose. |
| `Could not create the Genie space` | Genie is not enabled, or you cannot create spaces. Ask an admin, or use `--skip-genie` to deploy the rest. |
| `No Foundation Model chat endpoint is available` | Pay-per-token model serving is not enabled in this workspace/region. Re-run with `--no-rationale`. |
| `App deployment finished in state FAILED` | Open **Compute** > **Apps** > your app > **Logs** in the workspace. The runtime error is there. |
| Deployment succeeded but a `verify` check failed | The summary lists which one. Re-running usually resolves transient warehouse or Genie timeouts. |

The script prints the real Databricks error rather than hiding it, so the message in the
terminal is the message to act on.

---

## Options

```
python deploy.py [options]

  --host URL              Workspace URL
  --token TOKEN           Personal access token
  --profile NAME          Use a Databricks CLI profile instead of host/token

  --catalog NAME          Catalog to deploy into      (default: lactalis_catalog)
  --schema NAME           Schema to deploy into       (default: reco)
  --app-name NAME         Databricks App name         (default: lactalis-reco-engine)
  --warehouse-id ID       Specific SQL warehouse      (default: auto-pick serverless)
  --model NAME            Foundation Model endpoint   (default: best available)
  --as-of YYYY-MM-DD      Demo "today" driving the contextual signals (default: auto)

  --skip-app              Build data, engine, Genie and dashboard but not the app
  --skip-genie            Do not create the Genie space
  --skip-dashboard        Do not create the dashboard
  --no-rationale          Use rule-based rationale instead of calling a model
  --no-verify-app         Skip the live HTTP checks against the app
  --yes, -y               Never prompt; accept the deployer's choices
```

### Removing the demo

```bash
python deploy.py --destroy
```

This lists what it is about to delete (the app and its source folder, the Genie space, the
dashboard, and the schema with its tables), then asks you to type `delete` to confirm. It
never deletes a catalog, and it will not touch a Genie space or dashboard that points at a
different schema from the one you are removing. Add `--catalog` / `--schema` / `--app-name`
if you deployed with non-default names.

---

## What the demo shows

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
  copy per recommendation, precomputed at deploy time so the live demo is fast and
  deterministic.

**Segments and data.** Five B2B segments, 80 accounts, a realistic Lactalis Australia SKU
catalogue (Pauls, Président, Galbani, Lactel, Parmalat, Siggi's), 12 months of orders,
favourites, stock by DC, and weather / fuel / calendar signals. Source-tagged to SAP (ERP) and
Salesforce (CRM and e-commerce) to tell the Unity Catalog lineage story.

All data is synthetic. There is no live SAP or Salesforce integration.

---

## Local development

Only needed if you want to change the UI. The deployed app does not build anything. The
React bundle in `app/frontend/dist` is committed and shipped as-is.

```bash
cd app
npm install
npm run build:frontend        # rebuild frontend/dist after changing the UI

DATABRICKS_HOST=https://your-company.cloud.databricks.com \
DATABRICKS_TOKEN=dapi... \
DATABRICKS_WAREHOUSE_ID=<id> \
RECO_CATALOG=lactalis_catalog RECO_SCHEMA=reco \
GENIE_SPACE_ID=<id> DASHBOARD_ID=<id> \
  node server/index.js
# open http://localhost:8000
```

Re-run `python deploy.py` afterwards to push the rebuilt frontend.

### App environment variables

| Variable | Required | Purpose |
|---|---|---|
| `DATABRICKS_HOST` | prod (injected) | workspace URL |
| `DATABRICKS_CLIENT_ID` / `DATABRICKS_CLIENT_SECRET` | prod (injected) | service-principal OAuth (M2M) |
| `DATABRICKS_TOKEN` or `DATABRICKS_CONFIG_PROFILE` | local | local auth |
| `DATABRICKS_WAREHOUSE_ID` | yes | SQL warehouse (bound via the `sql-warehouse` app resource in prod) |
| `RECO_CATALOG` / `RECO_SCHEMA` | yes | Unity Catalog location |
| `GENIE_SPACE_ID` | optional | enables the embedded Genie panel |
| `DASHBOARD_ID` | optional | enables the embedded Lakeview dashboard |

`/api/health` reports any missing configuration under `config_problems`.

---

## Project layout

```
lactalis-reco-engine/
  deploy.py              one-shot end-to-end deployer (standard library only)
  deploy_seed/           seed data (CSV) the deployer loads
  app/                   Node.js/Express + React (Vite) dual-view app
    server/              Express backend (SQL, auth, Genie, routes)
    frontend/dist/       pre-built React bundle, shipped as-is
    app.yaml             template; deploy.py generates the real one
  dashboard/             Lakeview dashboard definition
  docs/                  design doc, schema contract, architecture
```
