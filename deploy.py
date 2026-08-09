#!/usr/bin/env python3
"""
Lactalis B2B Personalized Recommendation Engine - one-shot deployer.

Reproduces the entire demo inside ANY Databricks workspace, in a single command:

  1. Unity Catalog  : catalog + schema + gold tables loaded from the bundled seed CSVs
  2. Reco engine    : SQL scoring + hard out-of-stock guardrail + FMAPI (ai_query) rationale
  3. Metric views   : governed semantic layer over sales and recommendation performance
  4. Genie space    : natural-language analytics for the internal team
  5. AI/BI dashboard: Lakeview dashboard, created and published
  6. Databricks App : Node.js/React storefront + engine console, with its SQL warehouse
                      resource bound and every permission its service principal needs
  7. Verification   : data integrity checks PLUS live HTTP calls against the running app

Everything runs over the Databricks REST API using only the Python standard library.
The Databricks CLI is NOT required, nothing is compiled, and no npm install runs locally.
That makes it work the same on Windows, macOS and Linux.

Quick start (Windows, macOS, Linux):

    python deploy.py

    ...and answer the two prompts (workspace URL + personal access token).

Non-interactive:

    python deploy.py --host https://xxx.cloud.databricks.com --token dapi...
    python deploy.py --profile my-cli-profile

Re-running is safe. Tables are CREATE OR REPLACE, and the app, Genie space, dashboard
and grants are all upserted.
"""

import argparse
import base64
import configparser
import csv
import getpass
import json
import os
import posixpath
import re
import ssl
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
SEED_DIR = os.path.join(HERE, "deploy_seed")
APP_DIR = os.path.join(HERE, "app")
DASHBOARD_FILE = os.path.join(HERE, "dashboard", "lactalis_dashboard.lvdash.json")

# The dashboard JSON ships with these baked into its queries; they get rewritten
# to whatever catalog/schema the deployment actually targets.
DASHBOARD_TEMPLATE_CATALOG = "lactalis_catalog"
DASHBOARD_TEMPLATE_SCHEMA = "reco"

# Rationale model. The deployer verifies the first entry exists and is READY in the
# target workspace and walks down this list if it does not, so a workspace in a region
# without Claude still gets AI-written rationale instead of failing.
# Tried in order, first one that is READY in the workspace wins. Sonnet 4.5 leads because
# the demo copy was tuned and timed against it; the rest are here so a workspace that has
# retired it still lands on a capable model instead of the smallest Llama.
MODEL_PREFERENCE = [
    "databricks-claude-sonnet-4-5",
    "databricks-claude-sonnet-5",
    "databricks-claude-sonnet-4-6",
    "databricks-claude-sonnet-4",
    "databricks-claude-haiku-4-5",
    "databricks-gpt-5-mini",
    "databricks-gpt-oss-120b",
    "databricks-llama-4-maverick",
    "databricks-meta-llama-3-3-70b-instruct",
    "databricks-meta-llama-3-1-8b-instruct",
]

# Only these paths are shipped to the Apps runtime. frontend/src and frontend/public are
# build inputs, not runtime assets, so leaving them out keeps the upload small and fast.
APP_UPLOAD_FILES = ["app.yaml", "package.json", "package-lock.json"]
APP_UPLOAD_DIRS = ["server", "frontend/dist"]

SEED_TABLES = [
    "dim_dc", "dim_customer", "dim_product", "fact_orders",
    "customer_favorites", "stock_by_dc",
    "signal_weather", "signal_fuel_index", "signal_calendar",
]

# Typed columns per seed table (name, spark_type). Drives a typed CREATE from raw CSV strings.
SCHEMA = {
    "dim_dc": [("dc_id", "STRING"), ("dc_name", "STRING"), ("region", "STRING"), ("city", "STRING")],
    "dim_customer": [("customer_id", "STRING"), ("customer_name", "STRING"), ("segment", "STRING"),
        ("segment_label", "STRING"), ("dc_id", "STRING"), ("dc_name", "STRING"), ("region", "STRING"),
        ("city", "STRING"), ("profile_note", "STRING"), ("account_tier", "STRING"), ("source_system", "STRING")],
    "dim_product": [("product_id", "STRING"), ("product_name", "STRING"), ("brand", "STRING"),
        ("category", "STRING"), ("pack_size", "STRING"), ("unit_price", "DOUBLE"),
        ("is_flavored_milk", "BOOLEAN"), ("is_single_serve", "BOOLEAN"), ("is_cooking_cream", "BOOLEAN"),
        ("is_nutrition_compliant", "BOOLEAN"), ("is_hot_beverage", "BOOLEAN"), ("source_system", "STRING")],
    "fact_orders": [("order_id", "STRING"), ("customer_id", "STRING"), ("product_id", "STRING"),
        ("order_date", "DATE"), ("quantity", "INT"), ("line_revenue", "DOUBLE"), ("channel", "STRING"),
        ("source_system", "STRING")],
    "customer_favorites": [("customer_id", "STRING"), ("product_id", "STRING"),
        ("reorder_frequency_days", "INT"), ("last_ordered_date", "DATE"), ("source_system", "STRING")],
    "stock_by_dc": [("dc_id", "STRING"), ("product_id", "STRING"), ("on_hand_units", "INT"),
        ("threshold_units", "INT"), ("in_stock", "BOOLEAN"), ("source_system", "STRING")],
    "signal_weather": [("dc_id", "STRING"), ("signal_date", "DATE"), ("temp_c", "DOUBLE"),
        ("condition", "STRING"), ("is_heatwave", "BOOLEAN")],
    "signal_fuel_index": [("region", "STRING"), ("signal_date", "DATE"), ("fuel_price_aud", "DOUBLE"),
        ("index_vs_avg", "DOUBLE")],
    "signal_calendar": [("signal_date", "DATE"), ("is_school_holiday", "BOOLEAN"),
        ("is_public_holiday", "BOOLEAN"), ("holiday_name", "STRING")],
}

TABLE_COMMENTS = {
    "dim_customer": "B2B customer master. Source: Salesforce CRM (segmentation) + SAP (master).",
    "dim_product": "Lactalis SKU catalog. Source: SAP product master.",
    "dim_dc": "Distribution centers. Source: SAP.",
    "fact_orders": "Order line history ~12mo. Source: Salesforce (ecommerce) + SAP (rep).",
    "customer_favorites": "Saved reorder items. Source: Salesforce.",
    "stock_by_dc": "On-hand vs fulfillment threshold by DC. in_stock=hard guardrail. Source: SAP.",
    "signal_weather": "Contextual weather signal by DC.",
    "signal_fuel_index": "Contextual fuel price index by region.",
    "signal_calendar": "School/public holiday calendar.",
}

# Tables exposed to the Genie space.
GENIE_TABLES = [
    "dim_customer", "dim_product", "dim_dc", "fact_orders", "customer_favorites",
    "stock_by_dc", "signal_weather", "signal_fuel_index", "signal_calendar",
    "reco_candidates", "vw_reco_full",
]


# ---------------------------------------------------------------- console

class Console:
    """Step-prefixed logging that survives a Windows cp1252 terminal.

    Guarded by a lock because the seed loader and the source uploader both log
    from worker threads, and interleaved half-lines look like corruption.
    """

    def __init__(self):
        for stream in (sys.stdout, sys.stderr):
            try:
                stream.reconfigure(encoding="utf-8")
            except Exception:
                pass
        self.warnings = []
        self._t0 = time.time()
        self._lock = threading.Lock()

    def _emit(self, line):
        with self._lock:
            print(line, flush=True)

    def step(self, name, msg):
        self._emit(f"[{name:>9}] {msg}")

    def detail(self, msg):
        self._emit(f"{'':>11} {msg}")

    def warn(self, name, msg):
        with self._lock:
            self.warnings.append(f"[{name}] {msg}")
            print(f"[{name:>9}] WARNING: {msg}", flush=True)

    def elapsed(self):
        secs = int(time.time() - self._t0)
        return f"{secs // 60}m {secs % 60}s"


LOG = Console()


class DeployError(Exception):
    """A failure the operator can act on. Printed without a Python traceback."""


# ---------------------------------------------------------------- REST client

RETRY_STATUS = {429, 500, 502, 503, 504}


class Api:
    def __init__(self, host, token):
        self.host = normalize_host(host)
        self.token = token
        self.warehouse_id = None

    # -- transport ------------------------------------------------

    def request(self, method, path, body=None, raw=None, content_type="application/json",
                timeout=300, retries=4, expect_json=True):
        url = self.host + path
        if raw is not None:
            data = raw
        elif body is not None:
            data = json.dumps(body).encode("utf-8")
        else:
            data = None

        headers = {"Authorization": f"Bearer {self.token}"}
        if data is not None:
            headers["Content-Type"] = content_type

        last = None
        for attempt in range(retries + 1):
            req = urllib.request.Request(url, data=data, method=method, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    payload = resp.read()
                    if not expect_json:
                        return payload
                    return json.loads(payload) if payload else {}
            except urllib.error.HTTPError as e:
                detail = e.read().decode("utf-8", "replace")[:800]
                last = ApiError(e.code, method, path, detail)
                if e.code in RETRY_STATUS and attempt < retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise last
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                reason = getattr(e, "reason", e)
                if isinstance(reason, ssl.SSLError) or "CERTIFICATE_VERIFY_FAILED" in str(reason):
                    # Corporate TLS inspection is the usual cause and needs a different fix
                    # from a plain connectivity problem, so say so explicitly.
                    raise DeployError(
                        f"TLS certificate verification failed connecting to {self.host}.\n"
                        f"  Detail: {reason}\n"
                        f"  This normally means your network inspects HTTPS traffic. Point Python at\n"
                        f"  your organisation's CA bundle and re-run, for example:\n"
                        f"    Windows :  set SSL_CERT_FILE=C:\\path\\to\\corporate-ca.pem\n"
                        f"    mac/Linux: export SSL_CERT_FILE=/path/to/corporate-ca.pem"
                    )
                last = DeployError(
                    f"Could not reach {self.host}.\n"
                    f"  Network error: {reason}\n"
                    f"  Check the workspace URL and your internet connection. If you are behind a\n"
                    f"  corporate proxy, set it first:\n"
                    f"    Windows :  set HTTPS_PROXY=http://proxy.example.com:8080\n"
                    f"    mac/Linux: export HTTPS_PROXY=http://proxy.example.com:8080"
                )
                if attempt < retries:
                    time.sleep(min(2 ** attempt, 8))
                    continue
                raise last
        raise last

    def get(self, path, **kw):
        return self.request("GET", path, **kw)

    def post(self, path, body=None, **kw):
        return self.request("POST", path, body=body, **kw)

    def patch(self, path, body=None, **kw):
        return self.request("PATCH", path, body=body, **kw)

    def delete(self, path, **kw):
        return self.request("DELETE", path, **kw)

    # -- SQL ------------------------------------------------------

    def sql(self, statement, catalog=None, schema=None, timeout_s=1800):
        body = {
            "warehouse_id": self.warehouse_id,
            "statement": statement,
            "wait_timeout": "50s",
            "on_wait_timeout": "CONTINUE",
            "format": "JSON_ARRAY",
            "disposition": "INLINE",
        }
        if catalog:
            body["catalog"] = catalog
        if schema:
            body["schema"] = schema

        r = self.post("/api/2.0/sql/statements/", body)
        sid = r.get("statement_id")
        state = r.get("status", {}).get("state")
        deadline = time.time() + timeout_s
        while state in ("PENDING", "RUNNING"):
            if time.time() > deadline:
                try:
                    self.post(f"/api/2.0/sql/statements/{sid}/cancel")
                except Exception:
                    pass
                raise DeployError(f"SQL statement timed out after {timeout_s}s:\n  {statement[:300]}")
            time.sleep(2)
            r = self.get(f"/api/2.0/sql/statements/{sid}")
            state = r.get("status", {}).get("state")

        if state != "SUCCEEDED":
            msg = r.get("status", {}).get("error", {}).get("message", state)
            raise SqlError(msg, statement)
        return r.get("result", {}).get("data_array", []) or []

    def scalar(self, statement, **kw):
        rows = self.sql(statement, **kw)
        return rows[0][0] if rows and rows[0] else None


class ApiError(DeployError):
    def __init__(self, status, method, path, detail):
        self.status = status
        self.detail = detail
        try:
            parsed = json.loads(detail)
            self.message = parsed.get("message") or parsed.get("error") or detail
            self.error_code = parsed.get("error_code", "")
        except Exception:
            self.message = detail
            self.error_code = ""
        super().__init__(f"{method} {path} -> HTTP {status}: {self.message}")


class SqlError(DeployError):
    def __init__(self, message, statement):
        self.message = message
        self.statement = statement
        super().__init__(f"SQL failed: {message}\n  Statement: {statement[:300]}")


def normalize_host(raw):
    h = (raw or "").strip().rstrip("/")
    if not h:
        return ""
    if not re.match(r"^https?://", h, re.I):
        h = "https://" + h
    # Tolerate someone pasting a deep link such as .../explore/data or ?o=123
    m = re.match(r"^(https://[^/?#]+)", h, re.I)
    return m.group(1) if m else h


# ---------------------------------------------------------------- auth

def resolve_auth(args):
    """Return (host, token). Tries flags, env, CLI profile, then interactive prompts."""
    if args.host and args.token:
        return normalize_host(args.host), args.token

    if args.profile:
        host, token = _from_profile(args.profile, args.host)
        if token:
            return host, token
        raise DeployError(
            f"Could not get a token for CLI profile '{args.profile}'.\n"
            f"  Try:  databricks auth login --host <workspace-url> --profile {args.profile}\n"
            f"  Or run without --profile and paste a personal access token when prompted."
        )

    env_host = os.environ.get("DATABRICKS_HOST")
    env_token = os.environ.get("DATABRICKS_TOKEN")
    if env_host and env_token:
        return normalize_host(env_host), env_token

    env_profile = os.environ.get("DATABRICKS_CONFIG_PROFILE")
    if env_profile:
        host, token = _from_profile(env_profile, args.host)
        if token:
            return host, token

    if not sys.stdin.isatty():
        raise DeployError(
            "No credentials supplied and this is not an interactive terminal.\n"
            "  Pass --host and --token, or --profile, or set DATABRICKS_HOST and DATABRICKS_TOKEN."
        )
    return _prompt_auth(args.host)


def _prompt_auth(default_host):
    print()
    print("  Databricks connection details")
    print("  -----------------------------")
    print("  Workspace URL looks like: https://your-workspace.cloud.databricks.com")
    print("  Token: in Databricks go to your avatar (top right) > Settings > Developer")
    print("         > Access tokens > Manage > Generate new token")
    print()
    host = ""
    while not host:
        entered = input(f"  Workspace URL{f' [{default_host}]' if default_host else ''}: ").strip()
        host = normalize_host(entered or default_host or "")
        if not host:
            print("  Please enter the workspace URL.")
    token = ""
    while not token:
        token = getpass.getpass("  Personal access token (input hidden): ").strip()
        if not token:
            print("  Please paste your token.")
    print()
    return host, token


def _from_profile(profile, host_override):
    """Read host/token for a CLI profile. Uses ~/.databrickscfg first, CLI second."""
    host = normalize_host(host_override) if host_override else ""
    token = None

    cfg_path = os.environ.get("DATABRICKS_CONFIG_FILE") or os.path.join(
        os.path.expanduser("~"), ".databrickscfg")
    if os.path.exists(cfg_path):
        cp = configparser.ConfigParser()
        try:
            cp.read(cfg_path, encoding="utf-8")
            if cp.has_section(profile):
                host = host or normalize_host(cp.get(profile, "host", fallback=""))
                token = cp.get(profile, "token", fallback=None)
        except Exception:
            pass

    if not token:
        # OAuth (U2M) profiles keep no token in the file; the CLI mints one on demand.
        try:
            out = subprocess.run(
                ["databricks", "auth", "token", "--profile", profile],
                capture_output=True, text=True, timeout=120,
                shell=(os.name == "nt"),
            )
            if out.returncode == 0:
                token = json.loads(out.stdout).get("access_token")
        except Exception:
            token = None
    return host, token


# ---------------------------------------------------------------- helpers

def q(s):
    """Single-quote and escape a SQL string literal.

    Databricks SQL uses backslash escapes inside string literals, and it concatenates
    adjacent literals. The SQL-standard doubled quote is therefore not an escape here:
    'Siggi''s' parses as 'Siggi' followed by 's' and silently loads as "Siggis". The
    backslash must be escaped first so it cannot consume the quote that follows it.
    """
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'") + "'"


def ident(s):
    """Backtick-quote a SQL identifier."""
    return "`" + str(s).replace("`", "``") + "`"


def load_seed_csv(name):
    path = os.path.join(SEED_DIR, name + ".csv")
    if not os.path.exists(path):
        raise DeployError(
            f"Seed file missing: {path}\n"
            f"  Run deploy.py from inside the cloned repository so deploy_seed/ is present."
        )
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.reader(f))
    if not rows:
        raise DeployError(f"Seed file is empty: {path}")
    return rows[0], rows[1:]


# ---------------------------------------------------------------- preflight

def preflight(api, args):
    """Validate the connection, pick a warehouse, pick a rationale model."""
    LOG.step("preflight", f"Connecting to {api.host}")
    try:
        me = api.get("/api/2.0/preview/scim/v2/Me")
    except ApiError as e:
        if e.status in (401, 403):
            raise DeployError(
                f"Databricks rejected the credentials ({e.status}).\n"
                f"  Host: {api.host}\n"
                f"  Check the workspace URL is right and the token has not expired or been revoked.\n"
                f"  Generate a fresh token: avatar > Settings > Developer > Access tokens."
            )
        raise
    user = me.get("userName", "unknown")
    LOG.detail(f"Authenticated as {user}")

    warehouse = ensure_warehouse(api, args.warehouse_id)
    check_sql_quoting(api)
    model = pick_model(api, args.model)
    return user, warehouse, model


# Product names contain apostrophes and the seed loader builds SQL by interpolation, so a
# quoting mismatch corrupts data silently rather than raising. Prove the round-trip first.
QUOTING_PROBE = "Siggi's \"quoted\" back\\slash 100% semi;colon"


def check_sql_quoting(api):
    """Fail loudly if string literals do not survive a round-trip through this warehouse."""
    try:
        got = api.sql(f"SELECT {q(QUOTING_PROBE)} AS v")[0][0]
    except ApiError as e:
        raise DeployError(
            f"Could not run a query on the SQL warehouse.\n"
            f"  {e.message}\n"
            f"  Check you have CAN_USE on the warehouse and it is not stopped."
        )
    if got != QUOTING_PROBE:
        raise DeployError(
            "This SQL warehouse does not handle string escaping the way the loader expects,\n"
            "so the demo data would load corrupted.\n"
            f"  Sent back: {got!r}\n"
            f"  Expected : {QUOTING_PROBE!r}\n"
            "  This happens if the warehouse sets spark.sql.parser.escapedStringLiterals=true.\n"
            "  Remove that setting from the warehouse's SQL configuration, or pass\n"
            "  --warehouse-id for a warehouse that uses the default configuration."
        )


def ensure_warehouse(api, requested):
    if requested:
        try:
            wh = api.get(f"/api/2.0/sql/warehouses/{requested}")
        except ApiError:
            raise DeployError(
                f"SQL warehouse '{requested}' was not found in this workspace.\n"
                f"  Omit --warehouse-id to let the deployer pick one automatically."
            )
    else:
        whs = api.get("/api/2.0/sql/warehouses").get("warehouses", [])
        if not whs:
            raise DeployError(
                "This workspace has no SQL warehouse.\n"
                "  Create one first: SQL > SQL Warehouses > Create SQL warehouse\n"
                "  (a Serverless warehouse is recommended), then re-run this script."
            )
        whs.sort(key=lambda w: (
            0 if w.get("enable_serverless_compute") else 1,
            0 if w.get("state") == "RUNNING" else 1,
            w.get("name", ""),
        ))
        wh = whs[0]

    api.warehouse_id = wh["id"]
    LOG.step("preflight", f"SQL warehouse: {wh.get('name')} ({wh['id']}, state={wh.get('state')})")

    if wh.get("state") != "RUNNING":
        LOG.detail("Starting the warehouse (this can take a minute on a cold start)...")
        try:
            api.post(f"/api/2.0/sql/warehouses/{api.warehouse_id}/start")
        except ApiError:
            pass  # already starting, or we lack CAN_MANAGE; the first query will wait anyway
        _wait_warehouse(api, api.warehouse_id)
    return wh


def _wait_warehouse(api, warehouse_id, timeout_s=600):
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        state = api.get(f"/api/2.0/sql/warehouses/{warehouse_id}").get("state")
        if state == "RUNNING":
            LOG.detail("Warehouse is running.")
            return
        if state in ("STOPPED", "DELETED"):
            raise DeployError(f"Warehouse {warehouse_id} entered state {state} and will not start.")
        time.sleep(5)
    LOG.warn("preflight", "Warehouse did not report RUNNING in time; continuing anyway.")


def pick_model(api, requested):
    """Choose an FMAPI chat endpoint that actually exists and is READY here."""
    try:
        endpoints = api.get("/api/2.0/serving-endpoints").get("endpoints", [])
    except ApiError as e:
        LOG.warn("preflight", f"Could not list serving endpoints ({e.status}); trusting '{requested}'.")
        return requested

    ready = {}
    for e in endpoints:
        name = e.get("name")
        state = (e.get("state") or {}).get("ready")
        if name and state == "READY":
            ready[name] = e.get("task", "")

    # 'auto' means the caller expressed no preference, so falling through the list is the
    # expected path and must not look like something went wrong.
    if requested in ("", "auto", None):
        requested = ""
    if requested and requested in ready:
        LOG.step("preflight", f"Rationale model: {requested}")
        return requested
    if requested:
        LOG.warn("preflight", f"Model '{requested}' is not available here; choosing an alternative.")

    for candidate in MODEL_PREFERENCE:
        if candidate in ready:
            LOG.step("preflight", f"Rationale model: {candidate}")
            return candidate

    chat = sorted(n for n, task in ready.items() if task == "llm/v1/chat")
    if chat:
        LOG.step("preflight", f"Rationale model: {chat[0]}")
        return chat[0]

    raise DeployError(
        "No Foundation Model chat endpoint is available in this workspace, so the\n"
        "  AI-written recommendation rationale cannot be generated.\n"
        "  Enable Foundation Model APIs (pay-per-token) for this workspace, or re-run with\n"
        "  --no-rationale to deploy with rule-based rationale text instead."
    )


# ---------------------------------------------------------------- step: data layer

def resolve_catalog(api, requested, user, assume_yes=False):
    """Return a catalog we can actually create the demo schema in.

    Creating a catalog needs CREATE CATALOG on the metastore, which plenty of
    workspaces do not hand out. Rather than dead-ending, fall back to a catalog the
    caller already has write access to.
    """
    catalogs = {c["name"]: c for c in api.get("/api/2.1/unity-catalog/catalogs").get("catalogs", [])}

    if requested in catalogs:
        if _can_create_schema(api, requested, user):
            return requested
        raise DeployError(
            f"Catalog '{requested}' exists but you do not have CREATE SCHEMA on it.\n"
            f"  Ask its owner for CREATE SCHEMA and USE CATALOG, or re-run with\n"
            f"  --catalog <another_catalog>."
        )

    try:
        api.sql(f"CREATE CATALOG IF NOT EXISTS {ident(requested)}")
        LOG.detail(f"Created catalog {requested}")
        return requested
    except SqlError as e:
        if "PERMISSION_DENIED" not in e.message and "CREATE CATALOG" not in e.message:
            raise DeployError(f"Could not create catalog '{requested}'.\n  Databricks said: {e.message}")
        LOG.warn("data", f"Cannot create catalog '{requested}' "
                         f"(no CREATE CATALOG on the metastore). Looking for one you can write to.")

    candidates = _writable_catalogs(api, catalogs, user)
    if not candidates:
        raise DeployError(
            f"Catalog '{requested}' does not exist, you cannot create it, and no existing\n"
            f"  catalog in this workspace grants you CREATE SCHEMA.\n"
            f"  Ask a Unity Catalog admin for either:\n"
            f"    - CREATE CATALOG on the metastore, or\n"
            f"    - CREATE SCHEMA + USE CATALOG on an existing catalog,\n"
            f"  then re-run (add --catalog <name> if you were given an existing catalog)."
        )

    if len(candidates) == 1 or assume_yes or not sys.stdin.isatty():
        chosen = candidates[0]
        LOG.step("data", f"Using existing catalog '{chosen}' instead of '{requested}'.")
        return chosen

    print()
    print("  You do not have permission to create a new catalog. Pick one to deploy into:")
    for i, name in enumerate(candidates, 1):
        print(f"    {i}. {name}")
    print()
    while True:
        answer = input(f"  Catalog number [1]: ").strip() or "1"
        if answer.isdigit() and 1 <= int(answer) <= len(candidates):
            chosen = candidates[int(answer) - 1]
            print()
            return chosen
        print("  Enter one of the numbers listed above.")


def _effective_privileges(api, securable_type, full_name, principal):
    path = (f"/api/2.1/unity-catalog/effective-permissions/{securable_type}/"
            f"{urllib.parse.quote(full_name)}?principal={urllib.parse.quote(principal)}")
    try:
        r = api.get(path)
    except ApiError:
        return set()
    privs = set()
    for assignment in r.get("privilege_assignments", []):
        for p in assignment.get("privileges", []):
            name = p.get("privilege")
            if name:
                privs.add(name)
    return privs


def _can_create_schema(api, catalog, user):
    privs = _effective_privileges(api, "catalog", catalog, user)
    if not privs:
        # The effective-permissions API is unavailable or told us nothing useful.
        # Assume yes and let the CREATE SCHEMA attempt be the real test.
        return True
    return bool(privs & {"ALL_PRIVILEGES", "CREATE_SCHEMA"})


def _writable_catalogs(api, catalogs, user):
    """Catalogs the caller can create a schema in, best candidates first."""
    skip_types = {"SYSTEM_CATALOG", "DELTASHARING_CATALOG", "FOREIGN_CATALOG", "INTERNAL_CATALOG"}
    names = [n for n, c in catalogs.items()
             if c.get("catalog_type") not in skip_types
             and not n.startswith("__")
             and n not in ("system", "samples", "hive_metastore")]

    scored = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(
            lambda n: (n, _effective_privileges(api, "catalog", n, user)), names))
    for name, privs in results:
        if privs & {"ALL_PRIVILEGES", "MANAGE"}:
            rank = 0
        elif "CREATE_SCHEMA" in privs:
            rank = 1
        else:
            continue
        scored.append((rank, name))
    scored.sort()
    return [name for _rank, name in scored]


def build_data_layer(api, catalog, schema):
    LOG.step("data", f"Preparing {catalog}.{schema}")
    try:
        api.sql(f"CREATE SCHEMA IF NOT EXISTS {ident(catalog)}.{ident(schema)}")
    except SqlError as e:
        raise DeployError(
            f"Could not create schema '{schema}' in catalog '{catalog}'.\n"
            f"  Databricks said: {e.message}\n"
            f"  You need USE CATALOG + CREATE SCHEMA on '{catalog}'."
        )

    fq = f"{ident(catalog)}.{ident(schema)}"
    LOG.step("data", f"Loading {len(SEED_TABLES)} seed tables")

    def load(table):
        _load_table(api, fq, table)
        return table

    errors = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(load, t): t for t in SEED_TABLES}
        for fut, table in futures.items():
            try:
                fut.result()
            except Exception as e:
                errors.append((table, e))
    if errors:
        table, err = errors[0]
        raise DeployError(f"Failed loading seed table '{table}': {err}")

    api.sql(f"""CREATE OR REPLACE VIEW {fq}.vw_orders_enriched
      COMMENT 'Order lines enriched with customer segment and product attributes.' AS
      SELECT o.order_id,o.order_date,o.quantity,o.line_revenue,o.channel,o.source_system,
             c.customer_id,c.customer_name,c.segment,c.segment_label,c.region,c.dc_id,
             p.product_id,p.product_name,p.brand,p.category
      FROM {fq}.fact_orders o JOIN {fq}.dim_customer c ON o.customer_id=c.customer_id
      JOIN {fq}.dim_product p ON o.product_id=p.product_id""")
    LOG.detail("Data layer ready.")


def _load_table(api, fq, table):
    header, rows = load_seed_csv(table)
    cols = SCHEMA[table]
    colnames = [c for c, _ in cols]
    if header != colnames:
        raise DeployError(
            f"Seed file {table}.csv does not match the expected schema.\n"
            f"  File has:     {header}\n"
            f"  Expected:     {colnames}"
        )

    coldefs = ", ".join(f"{ident(n)} {ty}" for n, ty in cols)
    api.sql(f"CREATE OR REPLACE TABLE {fq}.{ident(table)} ({coldefs}) "
            f"COMMENT {q(TABLE_COMMENTS.get(table, ''))}")

    select_cols = ", ".join(
        (f"CAST(c{i} AS {typ}) AS {ident(name)}" if typ != "STRING" else f"c{i} AS {ident(name)}")
        for i, (name, typ) in enumerate(cols))
    raw_cols = ", ".join(f"c{i}" for i in range(len(cols)))

    total = 0
    for values in _value_chunks(rows, cols):
        api.sql(f"INSERT INTO {fq}.{ident(table)} "
                f"SELECT {select_cols} FROM (VALUES {', '.join(values)}) AS v({raw_cols})")
        total += len(values)
    LOG.detail(f"{table}: {total} rows")


def _value_chunks(rows, cols, max_chars=180_000):
    """Group rows into VALUES tuples, capped by generated SQL size, not row count."""
    buf, size = [], 0
    for r in rows:
        cells = []
        for i, (_, _typ) in enumerate(cols):
            v = r[i] if i < len(r) else ""
            cells.append("NULL" if v == "" else q(v))
        tup = "(" + ", ".join(cells) + ")"
        if buf and size + len(tup) > max_chars:
            yield buf
            buf, size = [], 0
        buf.append(tup)
        size += len(tup) + 2
    if buf:
        yield buf


# ---------------------------------------------------------------- step: reco engine

def resolve_as_of(api, catalog, schema, requested):
    """'auto' pins the demo to the date the bundled signals actually cover."""
    fq = f"{ident(catalog)}.{ident(schema)}"
    if requested and requested != "auto":
        return requested
    value = api.scalar(f"SELECT CAST(MAX(signal_date) AS STRING) FROM {fq}.signal_weather")
    if not value:
        raise DeployError("signal_weather has no rows, so the contextual signals cannot be dated.")
    return value


def build_reco_engine(api, catalog, schema, model, as_of, rationale=True):
    fq = f"{ident(catalog)}.{ident(schema)}"

    LOG.step("engine", f"Scoring candidates (affinity + context boosts, as of {as_of})")
    api.sql(f"""
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

    LOG.step("engine", "Applying the hard out-of-stock guardrail and top-3 cap")
    api.sql(f"""CREATE OR REPLACE TABLE {fq}.reco_candidates
      COMMENT 'Final personalized recs: top-3 per customer AFTER hard out-of-stock guardrail.' AS
      SELECT customer_id,product_id,rn AS rank,affinity_score,context_boost,final_score,reco_type,trigger_signal,TRUE AS in_stock
      FROM (SELECT *,ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY final_score DESC,peer_adoption DESC,product_id) rn
            FROM {fq}.reco_scored WHERE in_stock=TRUE) WHERE rn<=3""")
    api.sql(f"""CREATE OR REPLACE VIEW {fq}.vw_oos_blocked
      COMMENT 'Recs removed by the fulfillment guardrail (in_stock=false).' AS
      SELECT customer_id,customer_name,segment_label,dc_id,product_id,product_name,brand,category,
             final_score,trigger_signal,on_hand_units,threshold_units,rank_all
      FROM {fq}.reco_scored WHERE in_stock=FALSE AND rank_all<=3""")

    n_recs = int(api.scalar(f"SELECT COUNT(*) FROM {fq}.reco_candidates") or 0)
    build_rationale(api, fq, model, as_of, n_recs, rationale)

    api.sql(f"""CREATE OR REPLACE VIEW {fq}.vw_reco_full
      COMMENT 'One row per surfaced recommendation: candidate + rationale + product + customer.' AS
      SELECT rc.customer_id,c.customer_name,c.segment,c.segment_label,c.dc_id,c.region,c.city,
             rc.product_id,p.product_name,p.brand,p.category,p.pack_size,p.unit_price,
             rc.rank,rc.affinity_score,rc.context_boost,rc.final_score,rc.reco_type,rc.trigger_signal,
             r.why_text,r.why_now_tag,r.generated_by
      FROM {fq}.reco_candidates rc
      JOIN {fq}.dim_customer c ON rc.customer_id=c.customer_id
      JOIN {fq}.dim_product p ON rc.product_id=p.product_id
      LEFT JOIN {fq}.reco_rationale r ON r.customer_id=rc.customer_id AND r.product_id=rc.product_id""")

    api.sql(f"""CREATE OR REPLACE VIEW {fq}.vw_reco_kpi_base AS
      SELECT rc.customer_id,rc.product_id,rc.reco_type,rc.trigger_signal,c.segment_label,p.brand,p.category,
        CASE WHEN EXISTS (SELECT 1 FROM {fq}.fact_orders o WHERE o.customer_id=rc.customer_id AND o.product_id=rc.product_id) THEN 1 ELSE 0 END converted
      FROM {fq}.reco_candidates rc JOIN {fq}.dim_customer c ON rc.customer_id=c.customer_id
      JOIN {fq}.dim_product p ON rc.product_id=p.product_id""")
    LOG.detail(f"Engine ready: {n_recs} recommendations.")


# The rationale prompt and the deterministic "why now" chip, shared by both paths.
_WHY_NOW_SQL = """CASE trigger_signal
    WHEN 'weather' THEN CASE WHEN is_heatwave THEN CONCAT('Heatwave in ',region)
      WHEN condition='Cold' THEN CONCAT('Cold snap in ',region) ELSE CONCAT('Warm weather in ',region) END
    WHEN 'fuel' THEN 'High fuel prices driving impulse buys'
    WHEN 'calendar' THEN 'School holiday demand'
    ELSE CONCAT('Popular with ',segment_label) END"""


def _rationale_ctx_sql(fq, as_of):
    return f"""WITH ctx AS (
  SELECT rc.customer_id,rc.product_id,c.customer_name,c.segment_label,c.city,c.region,
         p.product_name,p.brand,p.category,rc.reco_type,rc.trigger_signal,
         w.condition,w.temp_c,w.is_heatwave,fi.index_vs_avg,cal.is_school_holiday
  FROM {fq}.reco_candidates rc
  JOIN {fq}.dim_customer c ON rc.customer_id=c.customer_id
  JOIN {fq}.dim_product p ON rc.product_id=p.product_id
  LEFT JOIN {fq}.signal_weather w ON w.dc_id=c.dc_id AND w.signal_date=DATE'{as_of}'
  LEFT JOIN {fq}.signal_fuel_index fi ON fi.region=c.region AND fi.signal_date=DATE'{as_of}'
  LEFT JOIN {fq}.signal_calendar cal ON cal.signal_date=DATE'{as_of}')"""


def build_rationale(api, fq, model, as_of, n_recs, use_ai):
    """Generate the one-line 'why we picked this' copy for every recommendation."""
    if use_ai:
        LOG.step("engine", f"Writing rationale for {n_recs} recs via ai_query ({model})")
        LOG.detail("This calls the Foundation Model API once per recommendation; allow a few minutes.")
        stmt = f"""
CREATE OR REPLACE TABLE {fq}.reco_rationale
COMMENT 'FMAPI-generated recommendation rationale. Model: {model}.' AS
{_rationale_ctx_sql(fq, as_of)}
SELECT customer_id,product_id,
  ai_query({q(model)}, CONCAT(
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
  {_WHY_NOW_SQL} AS why_now_tag,
  {q(model)} AS generated_by
FROM ctx"""
        try:
            api.sql(stmt, timeout_s=3600)
        except SqlError as e:
            LOG.warn("engine", f"ai_query failed ({e.message[:200]}). Falling back to rule-based rationale.")
            use_ai = False
        else:
            blank = int(api.scalar(
                f"SELECT COUNT(*) FROM {fq}.reco_rationale "
                f"WHERE why_text IS NULL OR length(trim(why_text))=0") or 0)
            if blank == 0:
                return
            LOG.warn("engine", f"{blank} rationale rows came back empty; filling them with rule-based text.")
            api.sql(f"""INSERT OVERWRITE {fq}.reco_rationale
{_rationale_ctx_sql(fq, as_of)}
SELECT c.customer_id,c.product_id,
  COALESCE(NULLIF(TRIM(r.why_text),''), {_fallback_why_sql()}) AS why_text,
  {_WHY_NOW_SQL} AS why_now_tag,
  COALESCE(r.generated_by, 'rule-based') AS generated_by
FROM ctx c LEFT JOIN {fq}.reco_rationale r
  ON r.customer_id=c.customer_id AND r.product_id=c.product_id""")
            return

    LOG.step("engine", "Writing rule-based rationale (no Foundation Model call)")
    api.sql(f"""
CREATE OR REPLACE TABLE {fq}.reco_rationale
COMMENT 'Rule-based recommendation rationale (Foundation Model API not used).' AS
{_rationale_ctx_sql(fq, as_of)}
SELECT customer_id,product_id,
  {_fallback_why_sql()} AS why_text,
  {_WHY_NOW_SQL} AS why_now_tag,
  'rule-based' AS generated_by
FROM ctx""")


def _fallback_why_sql():
    """Deterministic sentence used when the FMAPI is unavailable or returns nothing."""
    return """CONCAT(
    CASE
      WHEN trigger_signal='weather' AND is_heatwave THEN CONCAT('Heatwave conditions across ',region,' are lifting demand for ')
      WHEN trigger_signal='weather' AND condition='Cold' THEN CONCAT('Cold weather across ',region,' is lifting demand for ')
      WHEN trigger_signal='weather' THEN CONCAT('Warm weather across ',region,' is lifting demand for ')
      WHEN trigger_signal='fuel' THEN 'Elevated fuel prices are driving forecourt impulse purchases of '
      WHEN trigger_signal='calendar' THEN 'School holiday traffic is lifting convenience demand for '
      ELSE CONCAT('Accounts like ',segment_label,' are consistently ordering ')
    END,
    product_name,' from ',brand,
    CASE WHEN reco_type='upsell' THEN ', an easy add to lines you already stock.'
         ELSE ', a strong fit alongside your current range.' END)"""


# ---------------------------------------------------------------- step: metric views

def build_metric_views(api, catalog, schema):
    fq = f"{ident(catalog)}.{ident(schema)}"
    plain = f"{catalog}.{schema}"
    LOG.step("metrics", "Creating governed metric views")

    sales_yaml = f"""version: 0.1
source: {plain}.vw_orders_enriched
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
source: {plain}.vw_reco_kpi_base
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
    made = 0
    for name, body in (("mv_sales_performance", sales_yaml), ("mv_reco_performance", reco_yaml)):
        try:
            api.sql(f"CREATE OR REPLACE VIEW {fq}.{ident(name)} "
                    f"WITH METRICS LANGUAGE YAML AS $$\n{body}$$")
            made += 1
        except SqlError as e:
            LOG.warn("metrics", f"Skipped metric view {name}: {e.message[:160]}")
    if made:
        LOG.detail(f"{made} metric view(s) ready.")


# ---------------------------------------------------------------- step: Genie

def build_genie(api, catalog, schema, user):
    """Create (or re-point) the Genie space over the demo tables."""
    title = "Lactalis Recommendation Analytics"
    description = ("B2B dairy upsell/cross-sell, segment behaviour and fulfilment analytics "
                   "for sales & marketing.")

    tables = sorted(f"{catalog}.{schema}.{t}" for t in GENIE_TABLES)
    serialized = json.dumps({
        "version": 2,
        "data_sources": {"tables": [{"identifier": t} for t in tables]},
    })

    existing = _find_genie_space(api, title)
    if existing:
        # Re-point it: a space left over from an earlier run may reference a different
        # catalog/schema, which would silently answer questions from the wrong data.
        try:
            api.patch(f"/api/2.0/genie/spaces/{existing}", {
                "title": title,
                "description": description,
                "warehouse_id": api.warehouse_id,
                "serialized_space": serialized,
            })
            LOG.step("genie", f"Updated existing Genie space {existing} to {catalog}.{schema}")
            return existing
        except ApiError as e:
            LOG.warn("genie", f"Could not update Genie space {existing} ({e.message[:160]}); "
                              f"creating a new one.")

    body = {
        "warehouse_id": api.warehouse_id,
        "title": title,
        "description": description,
        "serialized_space": serialized,
    }
    if user:
        body["parent_path"] = f"/Users/{user}"

    try:
        space_id = api.post("/api/2.0/genie/spaces", body).get("space_id")
    except ApiError as e:
        raise DeployError(
            f"Could not create the Genie space ({e.status}: {e.message[:250]}).\n"
            f"  Genie must be enabled for this workspace and you need permission to create\n"
            f"  Genie spaces. An admin can enable it under Settings > Previews / Workspace admin.\n"
            f"  To finish the deployment without Genie, re-run with --skip-genie."
        )
    if not space_id:
        raise DeployError("Genie space creation returned no space_id.")
    LOG.step("genie", f"Created Genie space {space_id} over {len(tables)} tables")
    return space_id


def _find_genie_space(api, title):
    try:
        spaces = api.get("/api/2.0/genie/spaces").get("spaces", [])
    except ApiError:
        return None
    for s in spaces:
        if s.get("title") == title:
            return s.get("space_id")
    return None


# ---------------------------------------------------------------- step: dashboard

def build_dashboard(api, catalog, schema, user):
    """Create + publish the Lakeview dashboard, repointed at this catalog/schema."""
    name = "Lactalis Recommendation Engine Performance"
    if not os.path.exists(DASHBOARD_FILE):
        LOG.warn("dashboard", f"{DASHBOARD_FILE} not found; skipping the dashboard.")
        return ""

    with open(DASHBOARD_FILE, encoding="utf-8") as f:
        serialized = f.read()
    serialized = serialized.replace(
        f"{DASHBOARD_TEMPLATE_CATALOG}.{DASHBOARD_TEMPLATE_SCHEMA}.", f"{catalog}.{schema}.")

    dashboard_id = _find_dashboard(api, name)
    try:
        if dashboard_id:
            LOG.step("dashboard", f"Updating existing dashboard {dashboard_id}")
            api.patch(f"/api/2.0/lakeview/dashboards/{dashboard_id}", {
                "display_name": name,
                "warehouse_id": api.warehouse_id,
                "serialized_dashboard": serialized,
            })
        else:
            body = {
                "display_name": name,
                "warehouse_id": api.warehouse_id,
                "serialized_dashboard": serialized,
            }
            if user:
                body["parent_path"] = f"/Users/{user}"
            created = api.post("/api/2.0/lakeview/dashboards", body)
            dashboard_id = created.get("dashboard_id")
            LOG.step("dashboard", f"Created dashboard {dashboard_id}")
    except ApiError as e:
        LOG.warn("dashboard", f"Could not create the dashboard ({e.status}: {e.message[:200]}).")
        return ""

    try:
        api.post(f"/api/2.0/lakeview/dashboards/{dashboard_id}/published", {
            "embed_credentials": True,
            "warehouse_id": api.warehouse_id,
        })
        LOG.detail("Dashboard published (embedded with the publisher's credentials).")
    except ApiError as e:
        LOG.warn("dashboard", f"Dashboard created but publish failed ({e.message[:160]}). "
                              f"Open it in the workspace and click Publish.")
    return dashboard_id or ""


def _find_dashboard(api, name):
    try:
        page_token, seen = None, 0
        while seen < 500:
            path = "/api/2.0/lakeview/dashboards?page_size=100"
            if page_token:
                path += f"&page_token={urllib.parse.quote(page_token)}"
            r = api.get(path)
            for d in r.get("dashboards", []):
                seen += 1
                if d.get("display_name") == name and d.get("lifecycle_state") != "TRASHED":
                    return d.get("dashboard_id")
            page_token = r.get("next_page_token")
            if not page_token:
                break
    except ApiError:
        pass
    return None


# ---------------------------------------------------------------- step: app

def ensure_app(api, app_name, warehouse_id):
    """Create the app (with its SQL warehouse resource bound) or update it in place."""
    resources = [{
        "name": "sql-warehouse",
        "description": "SQL warehouse serving the Lactalis gold layer.",
        "sql_warehouse": {"id": warehouse_id, "permission": "CAN_USE"},
    }]
    description = "Lactalis B2B Personalized Recommendation Engine (MyLactalis storefront + Engine Console)."

    try:
        app = api.get(f"/api/2.0/apps/{app_name}")
        exists = True
    except ApiError as e:
        if e.status != 404:
            raise
        app, exists = None, False

    if exists:
        LOG.step("app", f"Reusing existing app '{app_name}'")
        try:
            app = api.patch(f"/api/2.0/apps/{app_name}",
                            {"description": description, "resources": resources})
        except ApiError as e:
            LOG.warn("app", f"Could not update the app's resources ({e.message[:160]}).")
        if (app.get("compute_status") or {}).get("state") == "STOPPED":
            LOG.detail("Starting app compute...")
            try:
                api.post(f"/api/2.0/apps/{app_name}/start")
            except ApiError:
                pass
    else:
        LOG.step("app", f"Creating app '{app_name}' with its SQL warehouse resource")
        try:
            app = api.post("/api/2.0/apps", {
                "name": app_name,
                "description": description,
                "resources": resources,
            })
        except ApiError as e:
            raise DeployError(
                f"Could not create the Databricks App '{app_name}' ({e.status}: {e.message[:250]}).\n"
                f"  Databricks Apps must be enabled for this workspace and you need permission\n"
                f"  to create apps. Check Compute > Apps in the workspace UI.\n"
                f"  If the name is taken by someone else, re-run with --app-name <other-name>."
            )

    sp_client_id = app.get("service_principal_client_id") or app.get("id")
    if not sp_client_id:
        app = api.get(f"/api/2.0/apps/{app_name}")
        sp_client_id = app.get("service_principal_client_id") or app.get("id")
    if not sp_client_id:
        raise DeployError("The app was created but Databricks did not report its service principal.")
    LOG.detail(f"App service principal: {sp_client_id}")
    return app, sp_client_id


def grant_app_permissions(api, sp, catalog, schema, warehouse_id, genie_space_id, dashboard_id):
    """Everything the app's service principal needs to read data and drive Genie."""
    LOG.step("grants", "Granting the app service principal access to data and resources")

    _uc_grant(api, "catalog", catalog, sp, ["USE_CATALOG"])
    _uc_grant(api, "schema", f"{catalog}.{schema}", sp, ["USE_SCHEMA", "SELECT"])
    _acl_grant(api, f"/api/2.0/permissions/warehouses/{warehouse_id}", sp, "CAN_USE", "SQL warehouse")
    if genie_space_id:
        _acl_grant(api, f"/api/2.0/permissions/genie/{genie_space_id}", sp, "CAN_RUN", "Genie space")
    if dashboard_id:
        _acl_grant(api, f"/api/2.0/permissions/dashboards/{dashboard_id}", sp, "CAN_READ", "dashboard")


def _uc_grant(api, securable_type, full_name, principal, privileges):
    path = f"/api/2.1/unity-catalog/permissions/{securable_type}/{urllib.parse.quote(full_name)}"
    try:
        api.patch(path, {"changes": [{"principal": principal, "add": privileges}]})
        LOG.detail(f"{securable_type} {full_name}: {', '.join(privileges)}")
    except ApiError as e:
        LOG.warn("grants", f"Could not grant {privileges} on {securable_type} {full_name}: {e.message[:200]}")


def _acl_grant(api, path, principal, level, label):
    try:
        api.patch(path, {"access_control_list": [
            {"service_principal_name": principal, "permission_level": level}]})
        LOG.detail(f"{label}: {level}")
    except ApiError as e:
        LOG.warn("grants", f"Could not grant {level} on the {label}: {e.message[:200]}")


def render_app_yaml(catalog, schema, genie_space_id, dashboard_id):
    return f"""# Generated by deploy.py. Edit deploy.py, not this file.
command: ['node', 'server/index.js']

env:
  - name: 'NODE_ENV'
    value: 'production'

  # Bound to the app's `sql-warehouse` resource, which deploy.py attaches.
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


def upload_app_source(api, src_path, app_yaml_text, workers=8):
    """Upload the app to the workspace over REST. No Databricks CLI, no local npm."""
    files = _collect_app_files()
    if not files:
        raise DeployError(f"No app files found under {APP_DIR}.")

    LOG.step("app", f"Uploading source to {src_path}")
    try:
        api.post("/api/2.0/workspace/delete", {"path": src_path, "recursive": True})
    except ApiError as e:
        if e.status not in (400, 404):
            raise

    dirs = sorted({posixpath.dirname(f"{src_path}/{rel}") for rel in files} | {src_path})
    for d in dirs:
        api.post("/api/2.0/workspace/mkdirs", {"path": d})

    def put(rel, content):
        api.request("POST", "/api/2.0/workspace/import", body={
            "path": f"{src_path}/{rel}",
            "format": "AUTO",
            "overwrite": True,
            "content": base64.b64encode(content).decode("ascii"),
        }, timeout=300)
        return rel

    payloads = [("app.yaml", app_yaml_text.encode("utf-8"))]
    for rel in files:
        if rel == "app.yaml":
            continue  # the generated one wins
        with open(os.path.join(APP_DIR, rel.replace("/", os.sep)), "rb") as f:
            payloads.append((rel, f.read()))

    errors = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(put, rel, data): rel for rel, data in payloads}
        for fut, rel in futures.items():
            try:
                fut.result()
            except Exception as e:
                errors.append((rel, e))
    if errors:
        rel, err = errors[0]
        raise DeployError(f"Failed uploading app file '{rel}': {err}")
    LOG.detail(f"Uploaded {len(payloads)} files.")


def _collect_app_files():
    """Relative POSIX paths (relative to app/) that the Apps runtime needs."""
    skip_names = {".DS_Store", "Thumbs.db"}
    out = []
    for rel in APP_UPLOAD_FILES:
        if os.path.exists(os.path.join(APP_DIR, rel)):
            out.append(rel)
    for d in APP_UPLOAD_DIRS:
        root_dir = os.path.join(APP_DIR, d.replace("/", os.sep))
        if not os.path.isdir(root_dir):
            raise DeployError(
                f"Expected app directory is missing: {root_dir}\n"
                f"  The React build (app/frontend/dist) ships with the repository. If you deleted it,\n"
                f"  rebuild with:  cd app && npm run build:frontend"
            )
        for dirpath, dirnames, filenames in os.walk(root_dir):
            dirnames[:] = [x for x in dirnames if x not in ("node_modules", "__pycache__", ".git")]
            for fn in filenames:
                if fn in skip_names or fn.endswith((".log", ".map")):
                    continue
                full = os.path.join(dirpath, fn)
                rel_path = os.path.relpath(full, APP_DIR).replace(os.sep, "/")
                out.append(rel_path)
    return sorted(set(out))


def wait_for_app_compute(api, app_name, timeout_s=900):
    """A deployment can only be created once the app's compute is ACTIVE."""
    deadline = time.time() + timeout_s
    announced = False
    while time.time() < deadline:
        app = api.get(f"/api/2.0/apps/{app_name}")
        state = (app.get("compute_status") or {}).get("state", "")
        if state == "ACTIVE":
            return app
        if state in ("ERROR", "DELETING"):
            raise DeployError(
                f"App compute entered state {state}.\n"
                f"  {(app.get('compute_status') or {}).get('message', '')}"
            )
        if state == "STOPPED":
            try:
                api.post(f"/api/2.0/apps/{app_name}/start")
            except ApiError:
                pass
        if not announced:
            LOG.step("app", "Waiting for app compute to start (usually 2-4 minutes)")
            announced = True
        time.sleep(10)
    raise DeployError(
        f"App compute for '{app_name}' did not become ACTIVE within {timeout_s // 60} minutes.\n"
        f"  Check Compute > Apps > {app_name} in the workspace."
    )


def deploy_app(api, app_name, src_path):
    LOG.step("app", "Starting deployment (the Apps runtime installs npm dependencies here)")
    try:
        dep = api.post(f"/api/2.0/apps/{app_name}/deployments",
                       {"source_code_path": src_path, "mode": "SNAPSHOT"})
    except ApiError as e:
        raise DeployError(f"Could not start the app deployment ({e.status}: {e.message[:250]}).")

    dep_id = dep.get("deployment_id")
    state = (dep.get("status") or {}).get("state", "IN_PROGRESS")
    deadline = time.time() + 1200
    last_msg = ""
    while state in ("IN_PROGRESS", "PENDING", "") and time.time() < deadline:
        time.sleep(10)
        dep = api.get(f"/api/2.0/apps/{app_name}/deployments/{dep_id}")
        status = dep.get("status") or {}
        state = status.get("state", "")
        msg = status.get("message", "")
        if msg and msg != last_msg:
            LOG.detail(msg)
            last_msg = msg

    if state != "SUCCEEDED":
        raise DeployError(
            f"App deployment finished in state {state}.\n"
            f"  {(dep.get('status') or {}).get('message', '')}\n"
            f"  Open Compute > Apps > {app_name} > Logs in the workspace to see the runtime output."
        )
    LOG.detail("Deployment succeeded.")
    return dep_id


def wait_for_app_running(api, app_name, timeout_s=900):
    LOG.step("app", "Waiting for the app to report RUNNING")
    deadline = time.time() + timeout_s
    last = ""
    app = {}
    while time.time() < deadline:
        app = api.get(f"/api/2.0/apps/{app_name}")
        compute = (app.get("compute_status") or {}).get("state", "")
        status = (app.get("app_status") or {}).get("state", "")
        current = f"{compute}/{status}"
        if current != last:
            LOG.detail(f"compute={compute or '?'} app={status or '?'}")
            last = current
        if compute == "ACTIVE" and status == "RUNNING":
            return app
        if compute in ("ERROR", "STOPPED") or status == "CRASHED":
            raise DeployError(
                f"The app entered state compute={compute}, app={status}.\n"
                f"  {(app.get('app_status') or {}).get('message', '')}\n"
                f"  Check Compute > Apps > {app_name} > Logs in the workspace."
            )
        time.sleep(10)
    LOG.warn("app", "The app did not report RUNNING within the timeout; it may still be starting.")
    return app


# ---------------------------------------------------------------- verification

def verify_data(api, catalog, schema):
    fq = f"{ident(catalog)}.{ident(schema)}"
    LOG.step("verify", "Data integrity checks")
    checks = [
        ("customers loaded", f"SELECT COUNT(*) FROM {fq}.dim_customer", lambda v: v > 0),
        ("products loaded", f"SELECT COUNT(*) FROM {fq}.dim_product", lambda v: v > 0),
        ("orders loaded", f"SELECT COUNT(*) FROM {fq}.fact_orders", lambda v: v > 0),
        ("recommendations built", f"SELECT COUNT(*) FROM {fq}.reco_candidates", lambda v: v > 0),
        ("every customer has recs",
         f"SELECT COUNT(*) FROM {fq}.dim_customer c WHERE NOT EXISTS "
         f"(SELECT 1 FROM {fq}.reco_candidates r WHERE r.customer_id=c.customer_id)", lambda v: v == 0),
        ("rationale is non-empty",
         f"SELECT COUNT(*) FROM {fq}.vw_reco_full WHERE why_text IS NULL OR length(trim(why_text))=0",
         lambda v: v == 0),
        ("out-of-stock guardrail holds",
         f"SELECT COUNT(*) FROM {fq}.reco_candidates rc JOIN {fq}.dim_customer c ON rc.customer_id=c.customer_id "
         f"JOIN {fq}.stock_by_dc st ON st.dc_id=c.dc_id AND st.product_id=rc.product_id WHERE st.in_stock=FALSE",
         lambda v: v == 0),
        ("favourites never repeated",
         f"SELECT COUNT(*) FROM {fq}.reco_candidates rc JOIN {fq}.customer_favorites f "
         f"ON rc.customer_id=f.customer_id AND rc.product_id=f.product_id", lambda v: v == 0),
        ("customer regions match their DC",
         f"SELECT COUNT(*) FROM {fq}.dim_customer c JOIN {fq}.dim_dc d ON c.dc_id=d.dc_id "
         f"WHERE c.region<>d.region", lambda v: v == 0),
    ]
    ok = True
    for name, stmt, test in checks:
        try:
            value = int(api.scalar(stmt) or 0)
            passed = test(value)
        except Exception as e:
            value, passed = f"error: {e}", False
        ok = ok and passed
        print(f"{'':>11} {'PASS' if passed else 'FAIL'}  {name}: {value}", flush=True)
    return ok


def _report(label, detail, passed):
    print(f"{'':>11} {'PASS' if passed else 'FAIL'}  {label}: {detail}", flush=True)
    return passed


def verify_app_live(api, app_url, genie_enabled):
    """Call the deployed app exactly as a browser would.

    Returns 'ok', 'failed', or 'unauthorized'. Databricks Apps only accept OAuth
    tokens at their front door, so a personal access token gets a 401 here even
    though the app is perfectly healthy. That is reported separately, not as a
    failure, and the backend checks cover the same ground.
    """
    if not app_url or not app_url.startswith("http"):
        LOG.warn("verify", "No app URL to test.")
        return "failed"

    def call(path, method="GET", body=None, timeout=180):
        url = app_url.rstrip("/") + path
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {"Authorization": f"Bearer {api.token}"}
        if data:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read() or b"{}")

    time.sleep(5)  # let the container settle after its first RUNNING report

    try:
        health = call("/api/health", timeout=60)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            return "unauthorized"
        LOG.step("verify", "Live checks against the running app")
        _report("app health", f"error: HTTP {e.code}", False)
        return "failed"
    except Exception as e:
        LOG.step("verify", "Live checks against the running app")
        _report("app health", f"error: {str(e)[:160]}", False)
        return "failed"

    LOG.step("verify", "Live checks against the running app")
    ok = True
    state = {}

    def check(label, fn):
        nonlocal ok
        try:
            detail = fn()
            passed = detail is not None
        except Exception as e:
            detail, passed = f"error: {str(e)[:160]}", False
        ok = _report(label, detail, passed) and ok

    def health_check():
        problems = health.get("config_problems") or []
        if health.get("status") != "healthy" or health.get("warehouse") != "reachable" or problems:
            return None
        return f"warehouse {health.get('warehouse')}, auth {health.get('auth_mode')}"

    def customers():
        r = call("/api/customers")
        n = r.get("count", 0)
        state["customer_id"] = (r.get("customers") or [{}])[0].get("customer_id")
        return f"{n} accounts" if n > 0 else None

    def recommendations():
        cid = state.get("customer_id")
        if not cid:
            return None
        recs = call(f"/api/customer/{cid}/recommendations").get("recommendations") or []
        if not recs or any(not (x.get("why_text") or "").strip() for x in recs):
            return None
        return f"{len(recs)} for {cid}, all with written rationale"

    def kpis():
        r = call("/api/kpis")
        rate = (r.get("conversion") or {}).get("rate")
        reach = (r.get("reach") or {}).get("total_recommendations", 0)
        return f"{reach} recs surfaced, conversion {rate:.0%}" if reach else None

    def console_oos():
        held = call("/api/console/oos-demo").get("held_back") or []
        return f"{len(held)} SKUs held back by the guardrail" if held else None

    def genie():
        r = call("/api/genie/ask", method="POST",
                 body={"question": "How many customers are in each segment?"}, timeout=180)
        if r.get("error"):
            return None
        rows = ((r.get("table") or {}).get("rows")) or []
        if not rows and not r.get("answer"):
            return None
        return f"answered with {len(rows)} rows"

    check("app health", health_check)
    check("customer list", customers)
    check("recommendations + rationale", recommendations)
    check("KPI tiles", kpis)
    check("out-of-stock console", console_oos)
    if genie_enabled:
        check("Genie question", genie)
    return "ok" if ok else "failed"


def verify_backend(api, catalog, schema, sp, genie_space_id):
    """Prove the app will work, without calling the app itself.

    Checks the grants that the app's service principal actually resolved to, runs the
    same queries the app's endpoints run, and asks the Genie space a real question.
    """
    LOG.step("verify", "Backend checks (grants, app queries, Genie)")
    fq = f"{ident(catalog)}.{ident(schema)}"
    ok = True

    cat_privs = _effective_privileges(api, "catalog", catalog, sp)
    ok = _report("service principal on catalog",
                 ", ".join(sorted(cat_privs)) or "none",
                 bool(cat_privs & {"USE_CATALOG", "ALL_PRIVILEGES"})) and ok

    sch_privs = _effective_privileges(api, "schema", f"{catalog}.{schema}", sp)
    ok = _report("service principal on schema",
                 ", ".join(sorted(sch_privs)) or "none",
                 bool(sch_privs & {"ALL_PRIVILEGES"}) or
                 {"SELECT", "USE_SCHEMA"} <= sch_privs) and ok

    queries = [
        ("customer list query", f"SELECT COUNT(*) FROM {fq}.dim_customer", lambda v: v > 0,
         lambda v: f"{v} accounts"),
        ("recommendation query", f"SELECT COUNT(*) FROM {fq}.vw_reco_full", lambda v: v > 0,
         lambda v: f"{v} rows in vw_reco_full"),
        ("rationale populated",
         f"SELECT COUNT(*) FROM {fq}.vw_reco_full WHERE why_text IS NULL OR length(trim(why_text))=0",
         lambda v: v == 0, lambda v: f"{v} missing"),
        ("out-of-stock console query",
         f"SELECT COUNT(*) FROM {fq}.stock_by_dc WHERE in_stock = FALSE", lambda v: v > 0,
         lambda v: f"{v} SKUs held back by the guardrail"),
    ]
    for label, stmt, test, fmt in queries:
        try:
            value = int(api.scalar(stmt) or 0)
            ok = _report(label, fmt(value), test(value)) and ok
        except Exception as e:
            ok = _report(label, f"error: {str(e)[:160]}", False) and ok

    if genie_space_id:
        acl_ok, acl_detail = _genie_acl(api, genie_space_id, sp)
        ok = _report("service principal on Genie space", acl_detail, acl_ok) and ok
        answer_ok, answer_detail = _genie_ask(api, genie_space_id)
        ok = _report("Genie question", answer_detail, answer_ok) and ok
    return ok


def _genie_acl(api, space_id, sp):
    try:
        acl = api.get(f"/api/2.0/permissions/genie/{space_id}").get("access_control_list", [])
    except ApiError as e:
        return False, f"error: {e.message[:120]}"
    for entry in acl:
        if entry.get("service_principal_name") == sp:
            levels = [p.get("permission_level") for p in entry.get("all_permissions", [])]
            return (any(l in ("CAN_RUN", "CAN_EDIT", "CAN_MANAGE") for l in levels),
                    ", ".join(x for x in levels if x))
    return False, "no permission entry found"


def _genie_ask(api, space_id, question="How many customers are in each segment?"):
    try:
        started = api.post(f"/api/2.0/genie/spaces/{space_id}/start-conversation",
                           {"content": question})
        conv = started.get("conversation_id") or (started.get("conversation") or {}).get("id")
        msg = started.get("message_id") or (started.get("message") or {}).get("id")
        if not conv or not msg:
            return False, "Genie did not start a conversation"
        url = f"/api/2.0/genie/spaces/{space_id}/conversations/{conv}/messages/{msg}"
        deadline = time.time() + 180
        message = {}
        while time.time() < deadline:
            message = api.get(url)
            if message.get("status") in ("COMPLETED", "FAILED", "CANCELLED", "QUERY_RESULT_EXPIRED"):
                break
            time.sleep(3)
        status = message.get("status")
        if status != "COMPLETED":
            return False, f"status {status or 'timeout'}"
        for att in message.get("attachments", []):
            if att.get("query") or (att.get("text") or {}).get("content"):
                return True, "answered"
        return False, "completed with no answer"
    except ApiError as e:
        return False, f"error: {e.message[:120]}"


# ---------------------------------------------------------------- teardown

GENIE_TITLE = "Lactalis Recommendation Analytics"
DASHBOARD_NAME = "Lactalis Recommendation Engine Performance"


def _references_schema(api, path, field, marker):
    """True when an object's serialized definition mentions catalog.schema."""
    try:
        return marker in (api.get(path).get(field) or "")
    except ApiError:
        return False


def destroy(api, args, user):
    """Remove everything the deployer creates. Never touches the catalog itself."""
    targets = []

    try:
        api.get(f"/api/2.0/apps/{args.app_name}")
        targets.append(("app", args.app_name))
    except ApiError:
        pass

    src_path = f"/Workspace/Users/{user}/{args.app_name}"
    try:
        api.get(f"/api/2.0/workspace/get-status?path={urllib.parse.quote(src_path)}")
        targets.append(("app source folder", src_path))
    except ApiError:
        pass

    # Only remove a Genie space or dashboard that actually points at the schema being
    # destroyed. Both are found by a fixed name, so a workspace with more than one
    # deployment must not lose the other one's objects.
    marker = f"{args.catalog}.{args.schema}."

    genie_id = _find_genie_space(api, GENIE_TITLE)
    if genie_id and not _references_schema(api, f"/api/2.0/genie/spaces/{genie_id}",
                                           "serialized_space", marker):
        LOG.detail(f"Leaving Genie space {genie_id} alone: it does not reference "
                   f"{args.catalog}.{args.schema}.")
        genie_id = None
    if genie_id:
        targets.append(("Genie space", f"{GENIE_TITLE} ({genie_id})"))

    dashboard_id = _find_dashboard(api, DASHBOARD_NAME)
    if dashboard_id and not _references_schema(api, f"/api/2.0/lakeview/dashboards/{dashboard_id}",
                                               "serialized_dashboard", marker):
        LOG.detail(f"Leaving dashboard {dashboard_id} alone: it does not reference "
                   f"{args.catalog}.{args.schema}.")
        dashboard_id = None
    if dashboard_id:
        targets.append(("dashboard", f"{DASHBOARD_NAME} ({dashboard_id})"))

    schema_fq = f"{args.catalog}.{args.schema}"
    try:
        api.get(f"/api/2.1/unity-catalog/schemas/{urllib.parse.quote(schema_fq)}")
        targets.append(("schema (and every table in it)", schema_fq))
    except ApiError:
        pass

    if not targets:
        print("  Nothing to remove: none of the demo objects exist in this workspace.")
        return 0

    print()
    print("  The following will be permanently deleted:")
    for kind, name in targets:
        print(f"    - {kind}: {name}")
    print()
    print(f"  The catalog '{args.catalog}' itself will NOT be deleted.")
    print()

    if not args.yes:
        if not sys.stdin.isatty():
            raise DeployError("Refusing to delete without confirmation. Re-run with --yes.")
        if input("  Type 'delete' to confirm: ").strip().lower() != "delete":
            print("  Cancelled. Nothing was deleted.")
            return 1
        print()

    for kind, name in targets:
        try:
            if kind == "app":
                api.delete(f"/api/2.0/apps/{name}")
            elif kind == "app source folder":
                api.post("/api/2.0/workspace/delete", {"path": name, "recursive": True})
            elif kind == "Genie space":
                api.delete(f"/api/2.0/genie/spaces/{genie_id}")
            elif kind == "dashboard":
                api.delete(f"/api/2.0/lakeview/dashboards/{dashboard_id}")
            elif kind.startswith("schema"):
                api.sql(f"DROP SCHEMA IF EXISTS {ident(args.catalog)}.{ident(args.schema)} CASCADE")
            LOG.step("destroy", f"Deleted {kind}: {name}")
        except Exception as e:
            LOG.warn("destroy", f"Could not delete {kind} '{name}': {str(e)[:200]}")

    print()
    print("  Done." + ("" if not LOG.warnings else " Some objects could not be removed; see above."))
    return 1 if LOG.warnings else 0


# ---------------------------------------------------------------- main

def build_parser():
    p = argparse.ArgumentParser(
        description="Deploy the Lactalis B2B Recommendation Engine demo end to end.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="Run with no arguments to be prompted for the workspace URL and token.")
    p.add_argument("--host", help="Workspace URL, e.g. https://xxx.cloud.databricks.com")
    p.add_argument("--token", help="Personal access token (use with --host)")
    p.add_argument("--profile", help="Databricks CLI profile to read credentials from")
    p.add_argument("--catalog", default="lactalis_catalog", help="Unity Catalog catalog (default: lactalis_catalog)")
    p.add_argument("--schema", default="reco", help="Schema inside the catalog (default: reco)")
    p.add_argument("--warehouse-id", help="SQL warehouse id (default: auto-pick a serverless one)")
    p.add_argument("--model", default="auto",
                   help="Foundation Model endpoint for the rationale (default: auto-pick an available one)")
    p.add_argument("--app-name", default="lactalis-reco-engine", help="Databricks App name")
    p.add_argument("--as-of", default="auto",
                   help="Demo 'as of' date driving the contextual signals (default: auto-detect from the seed data)")
    p.add_argument("--skip-app", action="store_true", help="Build the data, engine, Genie and dashboard but not the app")
    p.add_argument("--skip-genie", action="store_true", help="Do not create the Genie space")
    p.add_argument("--skip-dashboard", action="store_true", help="Do not create the AI/BI dashboard")
    p.add_argument("--no-rationale", action="store_true",
                   help="Skip the Foundation Model calls and use rule-based rationale text")
    p.add_argument("--no-verify-app", action="store_true", help="Skip the live HTTP checks against the app")
    p.add_argument("--yes", "-y", action="store_true",
                   help="Never prompt; accept the deployer's choice when a decision is needed")
    p.add_argument("--destroy", action="store_true",
                   help="Delete the app, Genie space, dashboard and schema this script created")
    return p


def validate_names(args):
    if not re.match(r"^[a-z0-9_]+$", args.catalog):
        raise DeployError(f"--catalog '{args.catalog}' must be lowercase letters, digits and underscores only.")
    if not re.match(r"^[a-z0-9_]+$", args.schema):
        raise DeployError(f"--schema '{args.schema}' must be lowercase letters, digits and underscores only.")
    if not re.match(r"^[a-z0-9-]{2,30}$", args.app_name):
        raise DeployError(
            f"--app-name '{args.app_name}' must be 2-30 characters of lowercase letters, digits and hyphens.")


def run(args):
    validate_names(args)
    host, token = resolve_auth(args)
    api = Api(host, token)

    print()
    print("=" * 72)
    title = "remove the demo" if args.destroy else "deployer"
    print(f"  Lactalis B2B Personalized Recommendation Engine - {title}")
    print("=" * 72)
    print(f"  Workspace : {api.host}")
    print(f"  Target    : {args.catalog}.{args.schema}")
    print(f"  App       : {args.app_name}")
    print("=" * 72)
    print()

    if args.destroy:
        me = api.get("/api/2.0/preview/scim/v2/Me").get("userName", "")
        ensure_warehouse(api, args.warehouse_id)
        return destroy(api, args, me)

    user, warehouse, model = preflight(api, args)
    catalog = resolve_catalog(api, args.catalog, user, args.yes)

    build_data_layer(api, catalog, args.schema)
    as_of = resolve_as_of(api, catalog, args.schema, args.as_of)
    build_reco_engine(api, catalog, args.schema, model, as_of, rationale=not args.no_rationale)
    build_metric_views(api, catalog, args.schema)

    genie_id = "" if args.skip_genie else build_genie(api, catalog, args.schema, user)
    dashboard_id = "" if args.skip_dashboard else build_dashboard(api, catalog, args.schema, user)

    data_ok = verify_data(api, catalog, args.schema)

    app_url, app_ok = "", None
    if not args.skip_app:
        app, sp = ensure_app(api, args.app_name, api.warehouse_id)
        grant_app_permissions(api, sp, catalog, args.schema, api.warehouse_id, genie_id, dashboard_id)

        src_path = f"/Workspace/Users/{user}/{args.app_name}"
        upload_app_source(api, src_path,
                          render_app_yaml(catalog, args.schema, genie_id, dashboard_id))
        wait_for_app_compute(api, args.app_name)
        deploy_app(api, args.app_name, src_path)
        app = wait_for_app_running(api, args.app_name)
        app_url = app.get("url", "")
        if not args.no_verify_app:
            result = verify_app_live(api, app_url, bool(genie_id))
            if result == "unauthorized":
                LOG.step("verify", "Databricks Apps only accept OAuth tokens for direct API "
                                   "calls, so the app cannot be called with a")
                LOG.detail("personal access token. This does not affect the app itself, which "
                           "you open in a browser.")
                LOG.detail("Verifying everything the app depends on instead:")
                app_ok = verify_backend(api, catalog, args.schema, sp, genie_id)
            else:
                app_ok = result == "ok"

    print()
    print("=" * 72)
    print("  Deployment summary")
    print("=" * 72)
    print(f"  Catalog / schema : {catalog}.{args.schema}")
    print(f"  SQL warehouse    : {warehouse.get('name')} ({api.warehouse_id})")
    print(f"  Rationale model  : {'rule-based (no FMAPI)' if args.no_rationale else model}")
    print(f"  Genie space      : {genie_id or '(skipped)'}")
    if genie_id:
        print(f"                     {api.host}/genie/rooms/{genie_id}")
    print(f"  Dashboard        : {dashboard_id or '(skipped)'}")
    if dashboard_id:
        print(f"                     {api.host}/dashboardsv3/{dashboard_id}/published")
    print(f"  App              : {app_url or '(skipped)'}")
    print(f"  Data checks      : {'ALL PASSED' if data_ok else 'SOME FAILED (see above)'}")
    if app_ok is not None:
        print(f"  Live app checks  : {'ALL PASSED' if app_ok else 'SOME FAILED (see above)'}")
    print(f"  Elapsed          : {LOG.elapsed()}")

    if LOG.warnings:
        print()
        print(f"  {len(LOG.warnings)} warning(s):")
        for w in LOG.warnings:
            print(f"    - {w}")

    print("=" * 72)
    if app_url:
        print()
        print(f"  Open the demo:  {app_url}")
        print()

    failed = (not data_ok) or (app_ok is False)
    return 1 if failed else 0


def main():
    args = build_parser().parse_args()
    try:
        sys.exit(run(args))
    except DeployError as e:
        print()
        print("-" * 72)
        print("  DEPLOYMENT STOPPED")
        print("-" * 72)
        print(f"  {e}")
        print("-" * 72)
        sys.exit(1)
    except KeyboardInterrupt:
        print("\nCancelled.")
        sys.exit(130)


if __name__ == "__main__":
    main()
