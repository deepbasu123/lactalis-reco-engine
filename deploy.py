#!/usr/bin/env python3
"""
Lactalis B2B Personalized Recommendation Engine - one-shot deployer.

Reproduces the entire demo inside ANY Databricks workspace:
  1. Unity Catalog: catalog + schema + medallion (bronze -> silver -> gold) loaded from bundled seed CSVs
  2. Recommendation engine: SQL scoring + hard out-of-stock guardrail + FMAPI (ai_query) rationale
  3. Genie space for internal natural-language analytics
  4. AI/BI (Lakeview) dashboard for campaign performance
  5. Databricks App (Node.js/React dual-view storefront + engine console)

Design goals (mirrors the Databricks Quest deployer): single command, idempotent,
prints a clear summary, and fails loudly with the actual error rather than half-deploying.

Usage:
    python deploy.py --profile <cli-profile>
    python deploy.py --host https://xxx.cloud.databricks.com --token <PAT>
    python deploy.py --profile myws --catalog lactalis_catalog --schema reco \
                     --warehouse-id <id> --app-name lactalis-reco-engine

If --warehouse-id is omitted the deployer picks/starts a serverless SQL warehouse.
Re-running is safe: tables are CREATE OR REPLACE, grants and resources are upserted.
"""
import argparse, csv, json, os, subprocess, sys, time, urllib.request, urllib.error

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_DIR = os.path.join(HERE, "deploy_seed")
APP_DIR = os.path.join(HERE, "app")
RATIONALE_MODEL_DEFAULT = "databricks-claude-sonnet-4-5"

# Seed file -> gold table name. Order matters for FK sanity but CREATE OR REPLACE is order-free.
SEED_TABLES = [
    "dim_dc", "dim_customer", "dim_product", "fact_orders",
    "customer_favorites", "stock_by_dc",
    "signal_weather", "signal_fuel_index", "signal_calendar",
]

# Typed columns for each seed table (name, spark_type). Drives typed CREATE from raw CSV strings.
SCHEMA = {
    "dim_dc": [("dc_id","STRING"),("dc_name","STRING"),("region","STRING"),("city","STRING")],
    "dim_customer": [("customer_id","STRING"),("customer_name","STRING"),("segment","STRING"),
        ("segment_label","STRING"),("dc_id","STRING"),("dc_name","STRING"),("region","STRING"),
        ("city","STRING"),("profile_note","STRING"),("account_tier","STRING"),("source_system","STRING")],
    "dim_product": [("product_id","STRING"),("product_name","STRING"),("brand","STRING"),
        ("category","STRING"),("pack_size","STRING"),("unit_price","DOUBLE"),
        ("is_flavored_milk","BOOLEAN"),("is_single_serve","BOOLEAN"),("is_cooking_cream","BOOLEAN"),
        ("is_nutrition_compliant","BOOLEAN"),("is_hot_beverage","BOOLEAN"),("source_system","STRING")],
    "fact_orders": [("order_id","STRING"),("customer_id","STRING"),("product_id","STRING"),
        ("order_date","DATE"),("quantity","INT"),("line_revenue","DOUBLE"),("channel","STRING"),("source_system","STRING")],
    "customer_favorites": [("customer_id","STRING"),("product_id","STRING"),
        ("reorder_frequency_days","INT"),("last_ordered_date","DATE"),("source_system","STRING")],
    "stock_by_dc": [("dc_id","STRING"),("product_id","STRING"),("on_hand_units","INT"),
        ("threshold_units","INT"),("in_stock","BOOLEAN"),("source_system","STRING")],
    "signal_weather": [("dc_id","STRING"),("signal_date","DATE"),("temp_c","DOUBLE"),("condition","STRING"),("is_heatwave","BOOLEAN")],
    "signal_fuel_index": [("region","STRING"),("signal_date","DATE"),("fuel_price_aud","DOUBLE"),("index_vs_avg","DOUBLE")],
    "signal_calendar": [("signal_date","DATE"),("is_school_holiday","BOOLEAN"),("is_public_holiday","BOOLEAN"),("holiday_name","STRING")],
}

TABLE_COMMENTS = {
    "dim_customer": "B2B customer master. Source: Salesforce CRM (segmentation) + SAP (master).",
    "dim_product": "Lactalis SKU catalog. Source: SAP product master.",
    "dim_dc": "Distribution centers. Source: SAP.",
    "fact_orders": "Order line history ~12mo. Source: Alright Commerce (ecommerce) + SAP (rep).",
    "customer_favorites": "Saved reorder items. Source: Alright Commerce.",
    "stock_by_dc": "On-hand vs fulfillment threshold by DC. in_stock=hard guardrail. Source: SAP.",
    "signal_weather": "Contextual weather signal by DC.",
    "signal_fuel_index": "Contextual fuel price index by region.",
    "signal_calendar": "School/public holiday calendar.",
}

# ---------------------------------------------------------------- workspace client

class WS:
    def __init__(self, host, token, warehouse_id=None):
        self.host = host.rstrip("/")
        self.token = token
        self.warehouse_id = warehouse_id

    def _req(self, method, path, body=None):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(self.host + path, data=data, method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} {path} -> {e.code}: {e.read().decode()[:600]}")

    def sql(self, statement, catalog=None, schema=None):
        body = {"warehouse_id": self.warehouse_id, "statement": statement, "wait_timeout": "50s"}
        if catalog: body["catalog"] = catalog
        if schema: body["schema"] = schema
        r = self._req("POST", "/api/2.0/sql/statements/", body)
        sid = r["statement_id"]; st = r["status"]["state"]
        while st in ("PENDING", "RUNNING"):
            time.sleep(2)
            r = self._req("GET", f"/api/2.0/sql/statements/{sid}"); st = r["status"]["state"]
        if st != "SUCCEEDED":
            raise RuntimeError(f"SQL failed: {r['status'].get('error',{}).get('message','?')}\n  >> {statement[:200]}")
        return r.get("result", {}).get("data_array", []) or []

    def ensure_warehouse(self):
        """Pick a running/available serverless warehouse, or the first one, and start it."""
        if self.warehouse_id:
            return self.warehouse_id
        r = self._req("GET", "/api/2.0/sql/warehouses")
        whs = r.get("warehouses", [])
        if not whs:
            raise RuntimeError("No SQL warehouse found in this workspace. Create one, or pass --warehouse-id.")
        # prefer serverless, then running
        whs.sort(key=lambda w: (0 if w.get("enable_serverless_compute") else 1,
                                0 if w.get("state") == "RUNNING" else 1))
        self.warehouse_id = whs[0]["id"]
        if whs[0].get("state") != "RUNNING":
            try: self._req("POST", f"/api/2.0/sql/warehouses/{self.warehouse_id}/start")
            except Exception: pass
        return self.warehouse_id


# ---------------------------------------------------------------- helpers

def log(step, msg): print(f"[{step}] {msg}", flush=True)

def resolve_auth(args):
    """Return (host, token). Prefer explicit host/token, else derive from CLI profile."""
    if args.host and args.token:
        return args.host, args.token
    profile = args.profile or os.environ.get("DATABRICKS_CONFIG_PROFILE")
    if not profile and not (args.host and args.token):
        # allow env-only (e.g. running inside Databricks)
        if os.environ.get("DATABRICKS_HOST") and os.environ.get("DATABRICKS_TOKEN"):
            return os.environ["DATABRICKS_HOST"], os.environ["DATABRICKS_TOKEN"]
        raise SystemExit("Provide --profile, or --host and --token, or set DATABRICKS_HOST/DATABRICKS_TOKEN.")
    # host from profile
    host = args.host
    if not host:
        cfg = subprocess.run(["databricks","auth","env","--profile",profile],
                             capture_output=True, text=True)
        if cfg.returncode == 0:
            try: host = json.loads(cfg.stdout).get("env",{}).get("DATABRICKS_HOST")
            except Exception: pass
    if not host:
        # last resort: read ~/.databrickscfg
        import configparser, pathlib
        cp = configparser.ConfigParser(); cp.read(os.path.expanduser("~/.databrickscfg"))
        if cp.has_section(profile): host = cp.get(profile, "host", fallback=None)
    token = subprocess.check_output(["databricks","auth","token","--profile",profile], text=True)
    token = json.loads(token)["access_token"]
    if not host:
        raise SystemExit(f"Could not resolve host for profile {profile}. Pass --host explicitly.")
    return host, token

def q(s):
    return "'" + str(s).replace("'", "''") + "'"


# ---------------------------------------------------------------- step 1: UC + data

def load_seed_csv(name):
    path = os.path.join(SEED_DIR, name + ".csv")
    with open(path, newline="") as f:
        rows = list(csv.reader(f))
    return rows[0], rows[1:]

def build_data_layer(ws, catalog, schema):
    log("uc", f"Ensuring catalog {catalog} and schema {schema}")
    # The catalog may already exist (e.g. pre-provisioned by an admin/FEVM). Only try to
    # create it when it's genuinely absent, and give a clear message if creation is denied.
    existing = {r[0] for r in ws.sql("SHOW CATALOGS")}
    if catalog not in existing:
        try:
            ws.sql(f"CREATE CATALOG IF NOT EXISTS {catalog}")
        except RuntimeError as e:
            if "PERMISSION_DENIED" in str(e) or "CREATE CATALOG" in str(e):
                raise SystemExit(
                    f"Catalog '{catalog}' does not exist and you lack CREATE CATALOG on the metastore.\n"
                    f"Ask an admin to create it (or grant CREATE CATALOG), or pass --catalog <existing-catalog>\n"
                    f"you can already write to.")
            raise
    ws.sql(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
    fq = f"{catalog}.{schema}"

    for t in SEED_TABLES:
        header, rows = load_seed_csv(t)
        cols = SCHEMA[t]
        colnames = [c for c, _ in cols]
        assert header == colnames, f"{t}: seed header {header} != schema {colnames}"
        # Build a typed VALUES insert in batches to stay under statement size limits.
        select_cols = ", ".join(
            (f"CAST(c{i} AS {typ}) AS {name}" if typ != "STRING" else f"c{i} AS {name}")
            for i, (name, typ) in enumerate(cols))
        comment = TABLE_COMMENTS.get(t, "")
        # create empty typed table first
        coldefs = ", ".join(f"{n} {ty}" for n, ty in cols)
        ws.sql(f"CREATE OR REPLACE TABLE {fq}.{t} ({coldefs}) COMMENT {q(comment)}")
        # insert in chunks
        CH = 200
        for start in range(0, len(rows), CH):
            chunk = rows[start:start+CH]
            vals = []
            for r in chunk:
                cells = []
                for i, (_, typ) in enumerate(cols):
                    v = r[i] if i < len(r) else ""
                    cells.append("NULL" if v == "" else q(v))
                vals.append("(" + ", ".join(cells) + ")")
            raw_cols = ", ".join(f"c{i}" for i in range(len(cols)))
            ws.sql(f"INSERT INTO {fq}.{t} SELECT {select_cols} FROM (VALUES {', '.join(vals)}) AS v({raw_cols})")
        n = ws.sql(f"SELECT COUNT(*) FROM {fq}.{t}")[0][0]
        log("uc", f"  {t}: {n} rows")

    # enriched analytics view
    ws.sql(f"""CREATE OR REPLACE VIEW {fq}.vw_orders_enriched
      COMMENT 'Order lines enriched with customer segment and product attributes.' AS
      SELECT o.order_id,o.order_date,o.quantity,o.line_revenue,o.channel,o.source_system,
             c.customer_id,c.customer_name,c.segment,c.segment_label,c.region,c.dc_id,
             p.product_id,p.product_name,p.brand,p.category
      FROM {fq}.fact_orders o JOIN {fq}.dim_customer c ON o.customer_id=c.customer_id
      JOIN {fq}.dim_product p ON o.product_id=p.product_id""")
    log("uc", "Data layer ready.")


# ---------------------------------------------------------------- step 2: reco engine

def build_reco_engine(ws, catalog, schema, model, as_of="2026-08-07"):
    fq = f"{catalog}.{schema}"
    log("reco", "Scoring candidates (affinity + context boosts)")
    ws.sql(f"""
CREATE OR REPLACE TABLE {fq}.reco_scored AS
WITH params AS (SELECT DATE'{as_of}' AS as_of),
seg_totals AS (SELECT segment, COUNT(*) n_cust FROM {fq}.dim_customer GROUP BY segment),
seg_prod AS (SELECT c.segment,o.product_id,COUNT(DISTINCT o.customer_id) buyers
  FROM {fq}.fact_orders o JOIN {fq}.dim_customer c ON o.customer_id=c.customer_id GROUP BY c.segment,o.product_id),
peer AS (SELECT sp.segment,sp.product_id,sp.buyers*1.0/st.n_cust peer_adoption
  FROM seg_prod sp JOIN seg_totals st ON sp.segment=st.segment),
seg_cat AS (SELECT c.segment,p.category,SUM(o.quantity) qty
  FROM {fq}.fact_orders o JOIN {fq}.dim_customer c ON o.customer_id=c.customer_id
  JOIN {fq}.dim_product p ON o.product_id=p.product_id GROUP BY c.segment,p.category),
seg_cat_norm AS (SELECT segment,category,qty*1.0/MAX(qty) OVER (PARTITION BY segment) cat_affinity FROM seg_cat),
fav_cat AS (SELECT DISTINCT f.customer_id,p.category FROM {fq}.customer_favorites f JOIN {fq}.dim_product p ON f.product_id=p.product_id),
cand AS (
  SELECT c.customer_id,c.customer_name,c.segment,c.segment_label,c.dc_id,c.region,
         p.product_id,p.product_name,p.brand,p.category,p.unit_price,
         p.is_flavored_milk,p.is_single_serve,p.is_cooking_cream,p.is_nutrition_compliant,p.is_hot_beverage,
         COALESCE(pe.peer_adoption,0) peer_adoption,COALESCE(scn.cat_affinity,0) cat_affinity,
         CASE WHEN fc.category IS NOT NULL THEN 'upsell' ELSE 'cross-sell' END reco_type
  FROM {fq}.dim_customer c CROSS JOIN {fq}.dim_product p
  LEFT JOIN peer pe ON pe.segment=c.segment AND pe.product_id=p.product_id
  LEFT JOIN seg_cat_norm scn ON scn.segment=c.segment AND scn.category=p.category
  LEFT JOIN {fq}.customer_favorites cf ON cf.customer_id=c.customer_id AND cf.product_id=p.product_id
  LEFT JOIN fav_cat fc ON fc.customer_id=c.customer_id AND fc.category=p.category
  WHERE cf.product_id IS NULL
    AND NOT (c.segment='INSTITUTION' AND (p.product_name ILIKE '%coffee%' OR p.product_name ILIKE '%espresso%'
             OR p.product_name ILIKE '%mocha%' OR p.product_name ILIKE '%latte%' OR p.product_name ILIKE '%ice break%'))),
sig AS (
  SELECT cand.*,w.condition,w.is_heatwave,w.temp_c,fi.index_vs_avg,cal.is_school_holiday,st.in_stock,st.on_hand_units,st.threshold_units
  FROM cand
  LEFT JOIN {fq}.signal_weather w ON w.dc_id=cand.dc_id AND w.signal_date=(SELECT as_of FROM params)
  LEFT JOIN {fq}.signal_fuel_index fi ON fi.region=cand.region AND fi.signal_date=(SELECT as_of FROM params)
  LEFT JOIN {fq}.signal_calendar cal ON cal.signal_date=(SELECT as_of FROM params)
  LEFT JOIN {fq}.stock_by_dc st ON st.dc_id=cand.dc_id AND st.product_id=cand.product_id),
scored AS (SELECT *,
    ROUND(0.6*peer_adoption+0.4*cat_affinity,4) affinity_score,
    (CASE WHEN (condition='Hot' OR is_heatwave) AND is_flavored_milk THEN 0.30 ELSE 0 END) b_weather_hot,
    (CASE WHEN condition='Cold' AND (is_hot_beverage OR is_cooking_cream) THEN 0.25 ELSE 0 END) b_weather_cold,
    (CASE WHEN segment='RESTAURANT' AND is_cooking_cream THEN 0.22 ELSE 0 END) b_seg_rest,
    (CASE WHEN segment='INSTITUTION' AND is_nutrition_compliant THEN 0.22 ELSE 0 END) b_seg_inst,
    (CASE WHEN segment='P&C' AND COALESCE(index_vs_avg,0)>1.05 AND is_single_serve THEN 0.15 ELSE 0 END) b_fuel,
    (CASE WHEN COALESCE(is_school_holiday,false) AND segment IN ('P&C','GROCER') AND is_single_serve THEN 0.10 ELSE 0 END) b_cal
  FROM sig),
final AS (SELECT *,
    ROUND(b_weather_hot+b_weather_cold+b_seg_rest+b_seg_inst+b_fuel+b_cal,4) context_boost,
    ROUND(affinity_score+b_weather_hot+b_weather_cold+b_seg_rest+b_seg_inst+b_fuel+b_cal,4) final_score,
    CASE WHEN b_weather_hot>0 OR b_weather_cold>0 THEN 'weather' WHEN b_fuel>0 THEN 'fuel'
         WHEN b_cal>0 THEN 'calendar' WHEN b_seg_rest>0 OR b_seg_inst>0 THEN 'segment' ELSE 'segment' END trigger_signal
  FROM scored)
SELECT *,ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY final_score DESC,peer_adoption DESC,product_id) rank_all
FROM final WHERE final_score>0""")

    log("reco", "Applying hard out-of-stock guardrail + top-3 cap")
    ws.sql(f"""CREATE OR REPLACE TABLE {fq}.reco_candidates
      COMMENT 'Final personalized recs: top-3 per customer AFTER hard out-of-stock guardrail.' AS
      SELECT customer_id,product_id,rn AS rank,affinity_score,context_boost,final_score,reco_type,trigger_signal,TRUE AS in_stock
      FROM (SELECT *,ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY final_score DESC,peer_adoption DESC,product_id) rn
            FROM {fq}.reco_scored WHERE in_stock=TRUE) WHERE rn<=3""")
    ws.sql(f"""CREATE OR REPLACE VIEW {fq}.vw_oos_blocked
      COMMENT 'Recs removed by the fulfillment guardrail (in_stock=false).' AS
      SELECT customer_id,customer_name,segment_label,dc_id,product_id,product_name,brand,category,
             final_score,trigger_signal,on_hand_units,threshold_units,rank_all
      FROM {fq}.reco_scored WHERE in_stock=FALSE AND rank_all<=3""")

    log("reco", f"Generating rationale via ai_query ({model}) - this calls the FMAPI per rec")
    ws.sql(f"""
CREATE OR REPLACE TABLE {fq}.reco_rationale
COMMENT 'FMAPI-generated recommendation rationale. Model: {model}.' AS
WITH ctx AS (
  SELECT rc.customer_id,rc.product_id,c.customer_name,c.segment_label,c.city,c.region,
         p.product_name,p.brand,p.category,rc.reco_type,rc.trigger_signal,
         w.condition,w.temp_c,w.is_heatwave,fi.index_vs_avg,cal.is_school_holiday
  FROM {fq}.reco_candidates rc
  JOIN {fq}.dim_customer c ON rc.customer_id=c.customer_id
  JOIN {fq}.dim_product p ON rc.product_id=p.product_id
  LEFT JOIN {fq}.signal_weather w ON w.dc_id=c.dc_id AND w.signal_date=DATE'{as_of}'
  LEFT JOIN {fq}.signal_fuel_index fi ON fi.region=c.region AND fi.signal_date=DATE'{as_of}'
  LEFT JOIN {fq}.signal_calendar cal ON cal.signal_date=DATE'{as_of}')
SELECT customer_id,product_id,
  ai_query('{model}', CONCAT(
    'You are a Lactalis B2B account assistant writing the one-line justification shown under a "Suggested for You" product card in the MyLactalis ordering portal. ',
    'Write ONE sentence (max 22 words), concrete and specific, no emojis, no exclamation marks, plain professional Australian business English. ',
    'Do not mention that you are an AI. Do not use the word "leverage". ',
    'Customer: ',customer_name,' (',segment_label,', ',city,', ',region,'). ',
    'Recommended product: ',product_name,' by ',brand,' (',category,'). ',
    'This is a ',reco_type,' recommendation triggered by: ',trigger_signal,'. ',
    'Context today: weather=',COALESCE(condition,'n/a'),' ',COALESCE(CAST(temp_c AS STRING),''),'C',
      CASE WHEN is_heatwave THEN ' (heatwave)' ELSE '' END,
      ', fuel index vs avg=',COALESCE(CAST(ROUND(index_vs_avg,2) AS STRING),'n/a'),
      CASE WHEN is_school_holiday THEN ', school holidays' ELSE '' END,'. ',
    'Tie the reason to this segment and context. Return only the sentence.')) AS why_text,
  CASE trigger_signal
    WHEN 'weather' THEN CASE WHEN is_heatwave THEN CONCAT('Heatwave in ',region)
      WHEN condition='Cold' THEN CONCAT('Cold snap in ',region) ELSE CONCAT('Warm weather in ',region) END
    WHEN 'fuel' THEN 'High fuel prices driving impulse buys'
    WHEN 'calendar' THEN 'School holiday demand'
    ELSE CONCAT('Popular with ',segment_label) END AS why_now_tag,
  '{model}' AS generated_by
FROM ctx""")

    ws.sql(f"""CREATE OR REPLACE VIEW {fq}.vw_reco_full
      COMMENT 'One row per surfaced recommendation: candidate + rationale + product + customer.' AS
      SELECT rc.customer_id,c.customer_name,c.segment,c.segment_label,c.dc_id,c.region,c.city,
             rc.product_id,p.product_name,p.brand,p.category,p.pack_size,p.unit_price,
             rc.rank,rc.affinity_score,rc.context_boost,rc.final_score,rc.reco_type,rc.trigger_signal,
             r.why_text,r.why_now_tag,r.generated_by
      FROM {fq}.reco_candidates rc
      JOIN {fq}.dim_customer c ON rc.customer_id=c.customer_id
      JOIN {fq}.dim_product p ON rc.product_id=p.product_id
      LEFT JOIN {fq}.reco_rationale r ON r.customer_id=rc.customer_id AND r.product_id=rc.product_id""")

    # KPI base + metric views
    ws.sql(f"""CREATE OR REPLACE VIEW {fq}.vw_reco_kpi_base AS
      SELECT rc.customer_id,rc.product_id,rc.reco_type,rc.trigger_signal,c.segment_label,p.brand,p.category,
        CASE WHEN EXISTS (SELECT 1 FROM {fq}.fact_orders o WHERE o.customer_id=rc.customer_id AND o.product_id=rc.product_id) THEN 1 ELSE 0 END converted
      FROM {fq}.reco_candidates rc JOIN {fq}.dim_customer c ON rc.customer_id=c.customer_id
      JOIN {fq}.dim_product p ON rc.product_id=p.product_id""")
    n = ws.sql(f"SELECT COUNT(*) FROM {fq}.reco_candidates")[0][0]
    log("reco", f"Engine ready: {n} recommendations across all customers.")


def build_metric_views(ws, catalog, schema):
    fq = f"{catalog}.{schema}"
    log("metrics", "Creating governed metric views")
    sales_yaml = f"""version: 0.1
source: {fq}.vw_orders_enriched
dimensions:
  - name: Segment
    expr: segment_label
  - name: Brand
    expr: brand
  - name: Category
    expr: category
  - name: Region
    expr: region
  - name: Channel
    expr: channel
  - name: Order Date
    expr: order_date
measures:
  - name: Total Revenue
    expr: SUM(line_revenue)
  - name: Units Sold
    expr: SUM(quantity)
  - name: Order Lines
    expr: COUNT(order_id)
  - name: Active Customers
    expr: COUNT(DISTINCT customer_id)
  - name: Avg Line Value
    expr: AVG(line_revenue)
"""
    reco_yaml = f"""version: 0.1
source: {fq}.vw_reco_kpi_base
dimensions:
  - name: Segment
    expr: segment_label
  - name: Reco Type
    expr: reco_type
  - name: Trigger Signal
    expr: trigger_signal
  - name: Brand
    expr: brand
measures:
  - name: Recommendations Surfaced
    expr: COUNT(*)
  - name: Conversion Rate
    expr: AVG(converted)
"""
    ws.sql(f"CREATE OR REPLACE VIEW {fq}.mv_sales_performance WITH METRICS LANGUAGE YAML AS $$\n{sales_yaml}$$")
    ws.sql(f"CREATE OR REPLACE VIEW {fq}.mv_reco_performance WITH METRICS LANGUAGE YAML AS $$\n{reco_yaml}$$")
    log("metrics", "Metric views ready.")


# ---------------------------------------------------------------- step 3: Genie

def build_genie(ws, catalog, schema):
    fq = f"{catalog}.{schema}"
    tables = sorted([f"{fq}.{t}" for t in
        ["dim_customer","dim_product","dim_dc","fact_orders","customer_favorites",
         "stock_by_dc","signal_weather","signal_fuel_index","signal_calendar"]])
    serialized = json.dumps({"version": 2, "data_sources": {"tables": [{"identifier": t} for t in tables]}})
    body = {
        "warehouse_id": ws.warehouse_id,
        "title": "Lactalis Recommendation Analytics",
        "description": "B2B dairy upsell/cross-sell, segment behaviour and fulfilment analytics for sales & marketing.",
        "serialized_space": serialized,
    }
    try:
        r = ws._req("POST", "/api/2.0/genie/spaces", body)
        sid = r.get("space_id")
        log("genie", f"Created Genie space {sid}")
        return sid
    except Exception as e:
        log("genie", f"WARNING: could not create Genie space automatically ({str(e)[:120]}). "
                     "Create it manually over the {fq} tables; the app degrades gracefully without it.")
        return ""


# ---------------------------------------------------------------- step 5: App

def deploy_app(ws, host, catalog, schema, warehouse_id, genie_space_id, dashboard_id, app_name, profile):
    """Deploy the Node.js app via the databricks CLI (apps). Falls back to instructions if CLI too old."""
    log("app", f"Preparing app '{app_name}'")
    # write app.yaml env with resolved ids
    app_yaml = f"""command: ['node', 'server/index.js']
env:
  - name: 'NODE_ENV'
    value: 'production'
  - name: 'DATABRICKS_WAREHOUSE_ID'
    valueFrom: 'sql-warehouse'
  - name: 'RECO_CATALOG'
    value: '{catalog}'
  - name: 'RECO_SCHEMA'
    value: '{schema}'
  - name: 'GENIE_SPACE_ID'
    value: '{genie_space_id}'
  - name: 'DASHBOARD_ID'
    value: '{dashboard_id}'
"""
    with open(os.path.join(APP_DIR, "app.yaml"), "w") as f:
        f.write(app_yaml)

    prof = ["--profile", profile] if profile else []
    # 1. create app (idempotent)
    subprocess.run(["databricks","apps","create",app_name,*prof],
                   capture_output=True, text=True)
    # 2. sync source to workspace
    src_path = f"/Workspace/Users/{_me(ws)}/{app_name}"
    subprocess.run(["databricks","sync",APP_DIR,src_path,*prof], capture_output=True, text=True)
    # 3. deploy
    dep = subprocess.run(["databricks","apps","deploy",app_name,
                          "--source-code-path",src_path,*prof], capture_output=True, text=True)
    if dep.returncode != 0:
        log("app", "CLI deploy did not complete (older CLI often can't). "
                   "Use the manual `databricks apps` flow or the workspace UI - source is synced at "
                   f"{src_path}. Detail: {dep.stderr[:200]}")
        return ""
    # fetch URL
    try:
        info = ws._req("GET", f"/api/2.0/apps/{app_name}")
        url = info.get("url","")
        log("app", f"App deployed: {url}")
        return url
    except Exception:
        return ""

def _me(ws):
    return ws._req("GET", "/api/2.0/preview/scim/v2/Me").get("userName","")


# ---------------------------------------------------------------- verification

def verify(ws, catalog, schema):
    fq = f"{catalog}.{schema}"
    log("verify", "Running integrity checks")
    checks = {
        "customers": (f"SELECT COUNT(*) FROM {fq}.dim_customer", lambda v: int(v) > 0),
        "products": (f"SELECT COUNT(*) FROM {fq}.dim_product", lambda v: int(v) > 0),
        "recommendations": (f"SELECT COUNT(*) FROM {fq}.reco_candidates", lambda v: int(v) > 0),
        "rationale non-null": (f"SELECT COUNT(*) FROM {fq}.reco_rationale WHERE why_text IS NULL OR length(trim(why_text))=0", lambda v: int(v) == 0),
        "OOS guardrail violations": (f"SELECT COUNT(*) FROM {fq}.reco_candidates rc JOIN {fq}.dim_customer c ON rc.customer_id=c.customer_id JOIN {fq}.stock_by_dc st ON st.dc_id=c.dc_id AND st.product_id=rc.product_id WHERE st.in_stock=FALSE", lambda v: int(v) == 0),
        "favorites leak": (f"SELECT COUNT(*) FROM {fq}.reco_candidates rc JOIN {fq}.customer_favorites f ON rc.customer_id=f.customer_id AND rc.product_id=f.product_id", lambda v: int(v) == 0),
        "region coherence": (f"SELECT COUNT(*) FROM {fq}.dim_customer c JOIN {fq}.dim_dc d ON c.dc_id=d.dc_id WHERE c.region<>d.region", lambda v: int(v) == 0),
    }
    ok = True
    for name, (sql, test) in checks.items():
        v = ws.sql(sql)[0][0]
        passed = test(v)
        ok = ok and passed
        print(f"    {'PASS' if passed else 'FAIL'}  {name}: {v}")
    return ok


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description="Deploy the Lactalis B2B Recommendation Engine demo end to end.")
    ap.add_argument("--profile", help="Databricks CLI profile")
    ap.add_argument("--host", help="Workspace URL (with --token)")
    ap.add_argument("--token", help="PAT (with --host)")
    ap.add_argument("--catalog", default="lactalis_catalog")
    ap.add_argument("--schema", default="reco")
    ap.add_argument("--warehouse-id", help="SQL warehouse id (else auto-pick serverless)")
    ap.add_argument("--model", default=RATIONALE_MODEL_DEFAULT, help="FMAPI chat endpoint for rationale")
    ap.add_argument("--app-name", default="lactalis-reco-engine")
    ap.add_argument("--dashboard-id", default="", help="Lakeview dashboard id to embed (import the dashboard first)")
    ap.add_argument("--as-of", default="2026-08-07", help="Demo 'as of' date driving contextual signals")
    ap.add_argument("--skip-app", action="store_true", help="Build data/engine/genie/metrics but not the app")
    ap.add_argument("--skip-genie", action="store_true")
    args = ap.parse_args()

    host, token = resolve_auth(args)
    ws = WS(host, token, args.warehouse_id)
    print(f"== Lactalis Reco Engine deployer ==\n   host: {host}\n   target: {args.catalog}.{args.schema}\n")

    ws.ensure_warehouse()
    log("warehouse", f"Using warehouse {ws.warehouse_id}")

    build_data_layer(ws, args.catalog, args.schema)
    build_reco_engine(ws, args.catalog, args.schema, args.model, args.as_of)
    build_metric_views(ws, args.catalog, args.schema)

    genie_id = "" if args.skip_genie else build_genie(ws, args.catalog, args.schema)

    ok = verify(ws, args.catalog, args.schema)

    app_url = ""
    if not args.skip_app:
        app_url = deploy_app(ws, host, args.catalog, args.schema, ws.warehouse_id,
                             genie_id, args.dashboard_id, args.app_name, args.profile)

    print("\n== Deployment summary ==")
    print(f"   Catalog/schema : {args.catalog}.{args.schema}")
    print(f"   Warehouse      : {ws.warehouse_id}")
    print(f"   Genie space    : {genie_id or '(create manually / skipped)'}")
    print(f"   App URL        : {app_url or '(deploy via CLI/UI - source synced)'}")
    print(f"   Integrity      : {'ALL CHECKS PASSED' if ok else 'SOME CHECKS FAILED - review above'}")
    print("\n   Dashboard: import via the workspace (see README) - or build over the metric views.")
    if not ok:
        sys.exit(1)

if __name__ == "__main__":
    main()
