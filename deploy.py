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

Everything runs over the Databricks REST API. Nothing is compiled and no npm install
runs locally, so it works the same on Windows, macOS and Linux.

Authentication is delegated to the Databricks SDK, so this accepts whatever the
Databricks CLI accepts: browser OAuth, CLI profiles, DATABRICKS_* environment
variables, service principals, or a personal access token.

Requirements (optional, but the most reliable way to sign in):

    python -m pip install -r requirements.txt

    ...plus the Databricks CLI, which is a standalone program, not a pip package:
      Windows : winget install Databricks.DatabricksCLI
      macOS   : brew install databricks/tap/databricks
      Linux   : curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sh

Quick start (Windows, macOS, Linux):

    databricks auth login --host https://xxx.cloud.databricks.com
    python deploy.py

Other ways to authenticate:

    python deploy.py --profile my-cli-profile
    python deploy.py --host https://xxx.cloud.databricks.com --token dapi...
    set DATABRICKS_HOST=... && set DATABRICKS_TOKEN=... && python deploy.py

Without databricks-sdk installed the script still runs and falls back to prompting
for a workspace URL and personal access token. That path works, but a token is only
valid in the workspace that issued it, which is the usual reason a deploy fails at
the first API call.

Re-running is safe. Tables are CREATE OR REPLACE, and the app, Genie space, dashboard
and grants are all upserted.
"""

import argparse
import base64
import csv
import getpass
import json
import os
import posixpath
import re
import ssl
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

# Medallion prefixes. Gold keeps the bare seed-table names so the app, Genie space and
# dashboard keep reading the same objects they always did.
BRONZE = "bz_"
SILVER = "sv_"

# Natural keys, used by the silver layer to de-duplicate whatever bronze landed.
PRIMARY_KEYS = {
    "dim_dc": ["dc_id"],
    "dim_customer": ["customer_id"],
    "dim_product": ["product_id"],
    "fact_orders": ["order_id"],
    "customer_favorites": ["customer_id", "product_id"],
    "stock_by_dc": ["dc_id", "product_id"],
    "signal_weather": ["dc_id", "signal_date"],
    "signal_fuel_index": ["region", "signal_date"],
    "signal_calendar": ["signal_date"],
}

DEFAULT_APP_NAME = "lactalis-reco-engine"

# ---- scheduled refresh -------------------------------------------------------------
# A Databricks Job walks bronze -> silver -> gold -> reco -> ai_query twice a day, so the
# demo keeps moving instead of being frozen at whatever deploy.py built.
JOB_NAME_BASE = "[Lactalis] Medallion Refresh"
JOB_CRON = "0 0 8,16 * * ?"          # 08:00 and 16:00, quartz
JOB_TIMEZONE = "Australia/Brisbane"  # the demo is AU-based; Brisbane has no DST


def job_name(app_name):
    """Per-deployment job name so two demos in one workspace never collide.

    The job is looked up by name (Jobs API has no natural unique key we set), so a fixed
    name would make a second deployment jobs/reset the first one's job onto its schema.
    Scoping by app_name keeps each deployment's refresh job independent, the same way the
    pipeline SQL path and app source folder are already scoped by app_name.
    """
    return f"{JOB_NAME_BASE} ({app_name})"

# Session time zone on a warehouse is UTC, and 08:00 Brisbane is still the previous UTC
# day. Deriving the parity from a Brisbane-local date is what keeps both of a day's runs
# on the same side of the flip.
#
# Counting days since the epoch rather than using dayofyear() matters: day-of-year 365
# and day-of-year 1 are both odd, so a dayofyear parity would sit still over New Year in
# a non-leap year. Epoch days flip every single night.
PARITY_SQL = ("datediff(date(from_utc_timestamp(current_timestamp(), "
              f"'{JOB_TIMEZONE}')), DATE'1970-01-01') % 2")

# Stock rows the refresh flips in and out of stock. The two groups alternate rather than
# move together, so the fulfilment guardrail always has something to hold back and it is
# a different SKU each day. Group B is the better story: both SKUs are flavoured milk at
# DC-001, which fulfils every Petrol & Convenience customer, and they go out of stock on
# the same day the heatwave turns on.
MUTATE_STOCK_KEYS_A = [   # out of stock on a quiet day
    ("DC-001", "SKU-0001"),
    ("DC-003", "SKU-0016"),
    ("DC-004", "SKU-0011"),
]
MUTATE_STOCK_KEYS_B = [   # out of stock on a heatwave day
    ("DC-001", "SKU-0004"),
    ("DC-001", "SKU-0034"),
]
# DC-001 fulfils all 16 P&C customers, so its weather drives the flavoured-milk boost.
MUTATE_WEATHER_DCS = ["DC-001", "DC-002"]
# Every P&C customer in the seed data is in QLD, so that is the fuel signal worth moving.
MUTATE_FUEL_REGION = "QLD"

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
    def __init__(self, host, credentials, auth_label=""):
        self.host = normalize_host(host)
        self.warehouse_id = None
        self.auth_label = auth_label
        # A callable rather than a fixed string: OAuth access tokens are short-lived and
        # a full deploy outlives them, so every request asks for current headers.
        self._credentials = credentials

    @property
    def token(self):
        """The current bearer token, for the few callers that need the raw value."""
        header = self._credentials().get("Authorization", "")
        return header[7:] if header.startswith("Bearer ") else header

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

        headers = dict(self._credentials())
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

def sanitize_token(raw):
    """Strip the decorations people paste along with a token.

    A PAT copied out of a browser, terminal or chat message often arrives wrapped in
    quotes, prefixed with 'Bearer', or split across lines. Databricks answers a
    malformed Authorization header with a blanket HTTP 400 on every endpoint, which
    reads like a broken workspace rather than a bad paste, so clean it here.
    """
    t = (raw or "").strip()
    t = "".join(t.split())  # kill embedded newlines/spaces from wrapped pastes
    if t[:1] in "\"'" and t[-1:] == t[:1] and len(t) > 1:
        t = t[1:-1].strip()
    if t.lower().startswith("bearer"):
        t = t[len("bearer"):].strip()
    for prefix in ("token=", "DATABRICKS_TOKEN=", "--token="):
        if t.lower().startswith(prefix.lower()):
            t = t[len(prefix):].strip()
    return t.strip("\"'")


CLI_LOGIN_HINT = (
    "  The most reliable way to authenticate is the Databricks CLI, which opens a\n"
    "  browser and signs you in with your normal workspace login:\n"
    "    databricks auth login --host https://your-workspace.cloud.databricks.com\n"
    "  Then re-run this script with no --host/--token at all.\n"
    "  The CLI is a standalone program, not a pip package. To install it:\n"
    "    Windows : winget install Databricks.DatabricksCLI\n"
    "    macOS   : brew install databricks/tap/databricks\n"
    "    Linux   : curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli"
    "/main/install.sh | sh"
)


def resolve_auth(args):
    """Return (host, credentials, label) where credentials() yields request headers.

    Authentication is delegated to the Databricks SDK so this script accepts exactly
    what the Databricks CLI accepts: OAuth from `databricks auth login`, CLI profiles,
    DATABRICKS_* environment variables, service principals, and personal access
    tokens. Hand-rolling this is what made personal access tokens the only practical
    option, and a PAT pasted from the wrong workspace fails on every endpoint.
    """
    cfg, sdk_error = _sdk_config(args)
    if cfg is not None:
        return cfg.host, cfg.authenticate, (cfg.auth_type or "databricks-sdk")

    # The SDK is optional, so credentials that need no resolving still have to work
    # without it. Otherwise installing nothing would break --host/--token and the
    # DATABRICKS_* variables, which is the one combination CI relies on.
    static = _static_token_auth(args)
    if static:
        host, token, source = static
        return host, (lambda: {"Authorization": f"Bearer {token}"}), f"pat ({source})"

    if sys.stdin.isatty():
        if sdk_error:
            LOG.warn("auth", f"Could not authenticate through the Databricks SDK: {sdk_error}")
        host, token = _prompt_auth(args.host)
        return host, (lambda: {"Authorization": f"Bearer {token}"}), "pat (prompted)"

    raise DeployError(
        "Could not authenticate to Databricks and this is not an interactive terminal.\n"
        f"  {sdk_error or 'No credentials were supplied.'}\n"
        f"{CLI_LOGIN_HINT}\n"
        "  Non-interactive alternatives: --profile NAME, or set DATABRICKS_HOST and\n"
        "  DATABRICKS_TOKEN, or --host URL --token TOKEN."
    )


def _static_token_auth(args):
    """Host plus token taken straight from flags or the environment, no SDK needed.

    Returns (host, token, source) or None. Only reached when the SDK could not resolve
    anything, since with the SDK installed it handles both of these itself.
    """
    host = normalize_host(args.host or "")
    token = sanitize_token(args.token or "")
    if host and token:
        return host, token, "--host/--token"

    env_host = normalize_host(os.environ.get("DATABRICKS_HOST", ""))
    env_token = sanitize_token(os.environ.get("DATABRICKS_TOKEN", ""))
    if env_host and env_token:
        return env_host, env_token, "DATABRICKS_HOST/DATABRICKS_TOKEN"

    # One side supplied without the other is a mistake worth naming, because the next
    # thing the caller sees would otherwise be an unexplained prompt or a 401.
    if (host or env_host) and not (token or env_token):
        raise DeployError(
            "A workspace URL was supplied but no token, and no other credentials could "
            "be found.\n"
            "  Pass --token as well, set DATABRICKS_TOKEN, or install the tooling and "
            "sign in:\n"
            f"{CLI_LOGIN_HINT}"
        )
    return None


def _sdk_config(args):
    """Resolve credentials through the Databricks SDK's unified auth chain.

    Returns (config, None) on success and (None, reason) when nothing could be
    resolved, so the caller decides whether to prompt or fail.
    """
    try:
        from databricks.sdk.core import Config  # type: ignore[import-not-found]
    except ImportError:
        return None, ("the databricks-sdk package is not installed "
                      "(python -m pip install -r requirements.txt)")

    kwargs = {}
    if args.profile:
        kwargs["profile"] = args.profile
    if args.host:
        kwargs["host"] = normalize_host(args.host)
    if args.token:
        # An explicit token means the caller wants PAT auth; pin it so the SDK does not
        # silently prefer an unrelated cached CLI login for the same host.
        kwargs["token"] = sanitize_token(args.token)
        kwargs["auth_type"] = "pat"

    try:
        cfg = Config(**kwargs)
        cfg.authenticate()  # resolve now, so failures surface here and not mid-deploy
    except Exception as e:
        reason = str(e).strip().splitlines()
        detail = " ".join(line.strip() for line in reason if line.strip())
        if args.profile:
            raise DeployError(
                f"Could not authenticate with CLI profile '{args.profile}'.\n"
                f"  {detail}\n"
                f"  Re-authenticate that profile:\n"
                f"    databricks auth login --host <workspace-url> --profile {args.profile}"
            )
        return None, detail

    if not cfg.host:
        return None, "no workspace URL was configured"
    return cfg, None


def _prompt_auth(default_host):
    print()
    print("  Databricks connection details")
    print("  -----------------------------")
    print("  Workspace URL looks like: https://your-workspace.cloud.databricks.com")
    print("  Token: in Databricks go to your avatar (top right) > Settings > Developer")
    print("         > Access tokens > Manage > Generate new token")
    print("  The token must be created in the same workspace as the URL above.")
    print()
    host = ""
    while not host:
        entered = input(f"  Workspace URL{f' [{default_host}]' if default_host else ''}: ").strip()
        host = normalize_host(entered or default_host or "")
        if not host:
            print("  Please enter the workspace URL.")
    token = ""
    while not token:
        token = sanitize_token(getpass.getpass("  Personal access token (input hidden): "))
        if not token:
            print("  Please paste your token.")
    print()
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
    user = resolve_username(api, args.user)
    LOG.detail(f"Authenticated as {user}")

    # /Workspace/Users/<user>/ is normally created on first browser login. On a brand-new
    # workspace where the user only ever created a PAT, it may not exist yet, which would
    # break the Genie/dashboard parent_path and the source upload. mkdirs is recursive and
    # idempotent, so ensure the base folder up front.
    try:
        api.post("/api/2.0/workspace/mkdirs", {"path": f"/Workspace/Users/{user}"})
    except ApiError as e:
        LOG.warn("preflight", f"Could not pre-create /Workspace/Users/{user} ({e.status}). "
                              f"If later steps fail on a missing path, log into the workspace "
                              f"UI once (which creates your user folder) and re-run.")

    warehouse = ensure_warehouse(api, args.warehouse_id)
    check_sql_quoting(api)
    model = pick_model(api, args.model)
    return user, warehouse, model


def _probe_credentials(api):
    """Check whether an ordinary workspace API accepts this token.

    Returns (None, "") when the call succeeds, otherwise (status, message). Listing
    warehouses is read-only and available to any user, so a rejection here means the
    credentials are bad rather than the specific endpoint being fussy.
    """
    try:
        api.get("/api/2.0/sql/warehouses")
        return None, ""
    except ApiError as e:
        return e.status, e.message
    except DeployError as e:
        return -1, str(e)


def resolve_username(api, override=""):
    """Return the caller's workspace username (login email).

    Prefer SCIM Me. On some brand-new workspaces that call returns 400 even with a valid
    token; in that case --user must be supplied so we can still build /Workspace/Users/
    paths. An explicit --user always wins.
    """
    user = ""
    try:
        me = api.get("/api/2.0/preview/scim/v2/Me")
        user = me.get("userName", "") or ""
    except ApiError as e:
        if e.status in (401, 403):
            raise DeployError(
                f"Databricks rejected the credentials ({e.status}).\n"
                f"  Host: {api.host}\n"
                f"  Check the workspace URL is right and the token has not expired or been revoked.\n"
                f"  Generate a fresh token: avatar > Settings > Developer > Access tokens."
            )
        if e.status == 400:
            # A 400 here has two very different causes, so ask a second endpoint which
            # one it is. If an ordinary workspace API also refuses us, the credentials
            # are the problem and --user will not save the run; it only pushes the
            # failure downstream where it surfaces as a confusing "not found".
            probe_status, probe_message = _probe_credentials(api)
            if probe_status is not None:
                raise DeployError(
                    f"Databricks is refusing this token on {api.host}.\n"
                    f"  GET /api/2.0/preview/scim/v2/Me      -> HTTP 400\n"
                    f"  GET /api/2.0/sql/warehouses          -> HTTP {probe_status}: {probe_message}\n"
                    f"\n"
                    f"  Every call is being rejected, so this is the credentials, not a missing\n"
                    f"  warehouse or a missing --user flag. 'all-apis' on the token is the right\n"
                    f"  choice; the usual causes are:\n"
                    f"    1. The token was created in a different workspace. A PAT only works on\n"
                    f"       the workspace that issued it. Open {api.host} itself,\n"
                    f"       then avatar > Settings > Developer > Access tokens > Generate new token.\n"
                    f"    2. The token was truncated or altered on paste. Generate a fresh one and\n"
                    f"       copy it in a single action, with no quotes and no 'Bearer' prefix.\n"
                    f"\n"
                    f"  To confirm which, run this and check it returns your email:\n"
                    f"    curl -s -H \"Authorization: Bearer <token>\" \\\n"
                    f"      {api.host}/api/2.0/preview/scim/v2/Me"
                )
            if not override:
                raise DeployError(
                    f"Could not look up your username from {api.host} "
                    f"(GET /api/2.0/preview/scim/v2/Me returned 400).\n"
                    f"  Other workspace APIs accept this token, so the credentials are fine:\n"
                    f"  on a brand-new workspace this endpoint can reject the lookup before\n"
                    f"  user identity is fully provisioned.\n"
                    f"  Re-run and pass your workspace login email so the deployer can build your\n"
                    f"  workspace paths without the lookup, for example:\n"
                    f"    python deploy.py --user you@company.com   (plus your usual flags)"
                )
            LOG.detail(f"Username lookup returned 400 but the workspace API works; "
                       f"using --user {override}")
        else:
            # 5xx / unexpected: do not pretend --user is the cure.
            raise DeployError(
                f"Could not look up your username from {api.host} "
                f"(GET /api/2.0/preview/scim/v2/Me returned {e.status}: {e.message}).\n"
                f"  This looks like a transient Databricks error, not a missing username.\n"
                f"  Wait a minute and re-run. If it keeps failing, pass "
                f"--user you@company.com and try again."
            )

    if override:
        user = override
    if not user:
        raise DeployError(
            "Could not determine your username. Re-run with --user you@company.com."
        )
    return user


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
        except ApiError as e:
            # Only a 404 really means "no such warehouse". Reporting a 400/401/403 as
            # "not found" sends people hunting for the wrong problem: it is the
            # credentials being rejected, not the id being wrong.
            if e.status in (400, 401, 403):
                raise DeployError(
                    f"Databricks rejected the request for SQL warehouse '{requested}' "
                    f"(HTTP {e.status}).\n"
                    f"  {e.message}\n"
                    f"  The warehouse id is probably fine. This is the workspace refusing the\n"
                    f"  credentials or the permission. Re-run without --warehouse-id to confirm\n"
                    f"  the same error appears on the warehouse list, then generate a fresh\n"
                    f"  token inside this same workspace and paste it with no quotes."
                )
            raise DeployError(
                f"SQL warehouse '{requested}' was not found in this workspace (HTTP {e.status}).\n"
                f"  Omit --warehouse-id to let the deployer pick one automatically."
            )
    else:
        try:
            whs = api.get("/api/2.0/sql/warehouses").get("warehouses", [])
        except ApiError as e:
            if e.status in (400, 401, 403):
                raise DeployError(
                    f"Databricks rejected the SQL warehouse list call (HTTP {e.status}).\n"
                    f"  {e.message}\n"
                    f"  Host: {api.host}\n"
                    f"  This is the credentials being refused, not a missing warehouse.\n"
                    f"  Create a fresh token inside this same workspace (avatar > Settings >\n"
                    f"  Developer > Access tokens) and paste it without quotes or a Bearer prefix."
                )
            raise
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
    catalogs = {c["name"]: c for c in _list_catalogs(api)}

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
        visible = ", ".join(sorted(catalogs)) or "none"
        raise DeployError(
            f"Catalog '{requested}' does not exist, you cannot create it, and no existing\n"
            f"  catalog in this workspace grants you CREATE SCHEMA.\n"
            f"  Catalogs visible to you: {visible}\n"
            f"  If you can write to one of those, re-run with --catalog <name> and the\n"
            f"  deployer will use it. Otherwise ask a Unity Catalog admin for either:\n"
            f"    - CREATE CATALOG on the metastore, or\n"
            f"    - CREATE SCHEMA + USE CATALOG on an existing catalog."
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


def _list_catalogs(api):
    """Every catalog the caller can see, following pagination.

    The endpoint may return a partial page whatever max_results says: an unset
    next_page_token is the only reliable end-of-list signal. Reading one page can
    therefore hide the very catalog the caller asked for and make it look missing.

    Note this lists only catalogs the caller owns or holds USE_CATALOG on, unless they
    are a metastore admin, so an empty result means "none visible to you", not "none
    exist".
    """
    out, page_token, pages = [], None, 0
    while pages < 50:
        path = "/api/2.1/unity-catalog/catalogs"
        if page_token:
            path += f"?page_token={urllib.parse.quote(page_token)}"
        r = api.get(path)
        out.extend(r.get("catalogs", []) or [])
        page_token = r.get("next_page_token")
        pages += 1
        if not page_token:
            break
    return out


def _effective_privileges(api, securable_type, full_name, principal):
    """Privileges the principal effectively holds, or None if we could not find out.

    None and an empty set mean different things: the first is "the API would not tell
    us", the second is "it told us there are none". Collapsing them makes a workspace
    where this endpoint is unavailable look identical to one where the caller has no
    access at all, which turns into a dead end for someone who could have deployed.
    """
    path = (f"/api/2.1/unity-catalog/effective-permissions/{securable_type}/"
            f"{urllib.parse.quote(full_name)}?principal={urllib.parse.quote(principal)}")
    try:
        r = api.get(path)
    except ApiError:
        return None
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
        # Unavailable, or reported nothing. Either way the CREATE SCHEMA attempt is a
        # better test than refusing on the strength of an endpoint that can under-report.
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
        if privs is None:
            # Could not check. Worth offering last rather than ruling out, since the
            # CREATE SCHEMA attempt will give a definitive answer either way.
            rank = 2
        elif privs & {"ALL_PRIVILEGES", "MANAGE"}:
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
    LOG.step("data", f"Landing {len(SEED_TABLES)} seed files in bronze")

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

    LOG.step("data", "Promoting bronze -> silver -> gold")
    for stmt in promote_statements(fq):
        api.sql(stmt)

    api.sql(f"""CREATE OR REPLACE VIEW {fq}.vw_orders_enriched
      COMMENT 'Order lines enriched with customer segment and product attributes.' AS
      SELECT o.order_id,o.order_date,o.quantity,o.line_revenue,o.channel,o.source_system,
             c.customer_id,c.customer_name,c.segment,c.segment_label,c.region,c.dc_id,
             p.product_id,p.product_name,p.brand,p.category
      FROM {fq}.fact_orders o JOIN {fq}.dim_customer c ON o.customer_id=c.customer_id
      JOIN {fq}.dim_product p ON o.product_id=p.product_id""")
    LOG.detail("Data layer ready.")


def _load_table(api, fq, table):
    """Land one seed file in its bronze table, with the usual ingestion metadata."""
    header, rows = load_seed_csv(table)
    cols = SCHEMA[table]
    colnames = [c for c, _ in cols]
    if header != colnames:
        raise DeployError(
            f"Seed file {table}.csv does not match the expected schema.\n"
            f"  File has:     {header}\n"
            f"  Expected:     {colnames}"
        )

    bronze = ident(BRONZE + table)
    coldefs = ", ".join(f"{ident(n)} {ty}" for n, ty in cols)
    api.sql(f"CREATE OR REPLACE TABLE {fq}.{bronze} "
            f"({coldefs}, `_source_file` STRING, `_ingested_at` TIMESTAMP) "
            f"COMMENT {q('Bronze: raw ' + table + '.csv as landed. ' + TABLE_COMMENTS.get(table, ''))}")

    select_cols = ", ".join(
        (f"CAST(c{i} AS {typ}) AS {ident(name)}" if typ != "STRING" else f"c{i} AS {ident(name)}")
        for i, (name, typ) in enumerate(cols))
    raw_cols = ", ".join(f"c{i}" for i in range(len(cols)))

    total = 0
    for values in _value_chunks(rows, cols):
        api.sql(f"INSERT INTO {fq}.{bronze} "
                f"SELECT {select_cols}, {q(table + '.csv')}, current_timestamp() "
                f"FROM (VALUES {', '.join(values)}) AS v({raw_cols})")
        total += len(values)
    LOG.detail(f"{BRONZE}{table}: {total} rows")


def promote_statements(fq):
    """Bronze -> silver -> gold, as a list of statements.

    The same list is executed at deploy time and written into the scheduled job's SQL
    file, so there is exactly one definition of what a promotion means.
    """
    stmts = []
    for table in SEED_TABLES:
        cols = [name for name, _ in SCHEMA[table]]
        keys = PRIMARY_KEYS[table]
        key_cols = ", ".join(ident(k) for k in keys)
        not_null = " AND ".join(f"{ident(k)} IS NOT NULL" for k in keys)

        # Silver: typed, keyed, de-duplicated. Latest row per natural key wins, which is
        # what makes re-landing bronze safe.
        select_cols = []
        for name in cols:
            if table == "stock_by_dc" and name == "in_stock":
                # Recompute rather than trust the raw flag: the guardrail is the whole
                # point of this demo, so it is derived from the numbers every time.
                select_cols.append("(`on_hand_units` >= `threshold_units`) AS `in_stock`")
            else:
                select_cols.append(ident(name))
        stmts.append(
            f"CREATE OR REPLACE TABLE {fq}.{ident(SILVER + table)}\n"
            f"COMMENT {q('Silver: cleaned, typed and de-duplicated ' + table + '.')} AS\n"
            f"SELECT {', '.join(select_cols)}, current_timestamp() AS `_processed_at`\n"
            f"FROM (SELECT *, ROW_NUMBER() OVER (PARTITION BY {key_cols} "
            f"ORDER BY `_ingested_at` DESC) AS `_rn`\n"
            f"      FROM {fq}.{ident(BRONZE + table)} WHERE {not_null})\n"
            f"WHERE `_rn` = 1"
        )

        # Gold: exactly the columns the app, Genie space and dashboard already query.
        gold_cols = ", ".join(ident(name) for name in cols)
        stmts.append(
            f"CREATE OR REPLACE TABLE {fq}.{ident(table)}\n"
            f"COMMENT {q(TABLE_COMMENTS.get(table, ''))} AS\n"
            f"SELECT {gold_cols} FROM {fq}.{ident(SILVER + table)}"
        )
    return stmts


def mutate_statements(fq, as_of):
    """Move the demo on between runs by editing bronze, never gold.

    Parity is derived from the Brisbane calendar day, so the 08:00 and 16:00 runs agree
    with each other and the story flips overnight.
    """
    def keys(pairs):
        return " OR ".join(f"(`dc_id` = {q(dc)} AND `product_id` = {q(sku)})"
                           for dc, sku in pairs)

    dcs = ", ".join(q(dc) for dc in MUTATE_WEATHER_DCS)
    return [
        # Held back one day, back on the shelf the next.
        f"UPDATE {fq}.{ident(BRONZE + 'stock_by_dc')}\n"
        f"SET `on_hand_units` = CASE WHEN {PARITY_SQL} = 0 THEN 0 ELSE `threshold_units` + 50 END,\n"
        f"    `in_stock` = ({PARITY_SQL} <> 0)\n"
        f"WHERE {keys(MUTATE_STOCK_KEYS_A)}",

        # The mirror image, so the guardrail is never left with nothing to block.
        f"UPDATE {fq}.{ident(BRONZE + 'stock_by_dc')}\n"
        f"SET `on_hand_units` = CASE WHEN {PARITY_SQL} = 1 THEN 0 ELSE `threshold_units` + 50 END,\n"
        f"    `in_stock` = ({PARITY_SQL} <> 1)\n"
        f"WHERE {keys(MUTATE_STOCK_KEYS_B)}",

        f"UPDATE {fq}.{ident(BRONZE + 'signal_weather')}\n"
        f"SET `condition` = CASE WHEN {PARITY_SQL} = 1 THEN 'Hot' ELSE 'Mild' END,\n"
        f"    `is_heatwave` = ({PARITY_SQL} = 1),\n"
        f"    `temp_c` = CASE WHEN {PARITY_SQL} = 1 THEN 40.7 ELSE 22.0 END\n"
        f"WHERE `dc_id` IN ({dcs}) AND `signal_date` = DATE'{as_of}'",

        f"UPDATE {fq}.{ident(BRONZE + 'signal_fuel_index')}\n"
        f"SET `index_vs_avg` = CASE WHEN {PARITY_SQL} = 1 THEN 1.12 ELSE 0.98 END,\n"
        f"    `fuel_price_aud` = CASE WHEN {PARITY_SQL} = 1 THEN 1.72 ELSE 1.48 END\n"
        f"WHERE `region` = {q(MUTATE_FUEL_REGION)} AND `signal_date` = DATE'{as_of}'",
    ]


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
    scored, candidates, oos_view, kpi_view = score_statements(fq, as_of)
    api.sql(scored)

    LOG.step("engine", "Applying the hard out-of-stock guardrail and top-3 cap")
    api.sql(candidates)
    api.sql(oos_view)

    n_recs = int(api.scalar(f"SELECT COUNT(*) FROM {fq}.reco_candidates") or 0)
    build_rationale(api, fq, model, as_of, n_recs, rationale)

    api.sql(reco_full_view_statement(fq))
    api.sql(kpi_view)
    LOG.detail(f"Engine ready: {n_recs} recommendations.")


def score_statements(fq, as_of):
    """The scoring half of the engine: scored -> candidates -> guardrail/KPI views."""
    scored = f"""
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
FROM final WHERE final_score>0"""

    candidates = f"""CREATE OR REPLACE TABLE {fq}.reco_candidates
      COMMENT 'Final personalized recs: top-3 per customer AFTER hard out-of-stock guardrail.' AS
      SELECT customer_id,product_id,rn AS rank,affinity_score,context_boost,final_score,reco_type,trigger_signal,TRUE AS in_stock
      FROM (SELECT *,ROW_NUMBER() OVER (PARTITION BY customer_id ORDER BY final_score DESC,peer_adoption DESC,product_id) rn
            FROM {fq}.reco_scored WHERE in_stock=TRUE) WHERE rn<=3"""

    oos_view = f"""CREATE OR REPLACE VIEW {fq}.vw_oos_blocked
      COMMENT 'Recs removed by the fulfillment guardrail (in_stock=false).' AS
      SELECT customer_id,customer_name,segment_label,dc_id,product_id,product_name,brand,category,
             final_score,trigger_signal,on_hand_units,threshold_units,rank_all
      FROM {fq}.reco_scored WHERE in_stock=FALSE AND rank_all<=3"""

    kpi_view = f"""CREATE OR REPLACE VIEW {fq}.vw_reco_kpi_base AS
      SELECT rc.customer_id,rc.product_id,rc.reco_type,rc.trigger_signal,c.segment_label,p.brand,p.category,
        CASE WHEN EXISTS (SELECT 1 FROM {fq}.fact_orders o WHERE o.customer_id=rc.customer_id AND o.product_id=rc.product_id) THEN 1 ELSE 0 END converted
      FROM {fq}.reco_candidates rc JOIN {fq}.dim_customer c ON rc.customer_id=c.customer_id
      JOIN {fq}.dim_product p ON rc.product_id=p.product_id"""

    return [scored, candidates, oos_view, kpi_view]


def reco_full_view_statement(fq):
    """The single object the storefront reads. Depends on reco_rationale, so it is
    always rebuilt after the rationale, never before."""
    return f"""CREATE OR REPLACE VIEW {fq}.vw_reco_full
      COMMENT 'One row per surfaced recommendation: candidate + rationale + product + customer.' AS
      SELECT rc.customer_id,c.customer_name,c.segment,c.segment_label,c.dc_id,c.region,c.city,
             rc.product_id,p.product_name,p.brand,p.category,p.pack_size,p.unit_price,
             rc.rank,rc.affinity_score,rc.context_boost,rc.final_score,rc.reco_type,rc.trigger_signal,
             r.why_text,r.why_now_tag,r.generated_by
      FROM {fq}.reco_candidates rc
      JOIN {fq}.dim_customer c ON rc.customer_id=c.customer_id
      JOIN {fq}.dim_product p ON rc.product_id=p.product_id
      LEFT JOIN {fq}.reco_rationale r ON r.customer_id=rc.customer_id AND r.product_id=rc.product_id"""


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


def _ai_rationale_select(fq, model, as_of):
    """The FMAPI query itself. Wrapped by either a CREATE or an INSERT OVERWRITE."""
    return f"""{_rationale_ctx_sql(fq, as_of)}
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


def ai_rationale_statement(fq, model, as_of):
    """CREATE the rationale table from the Foundation Model API."""
    return (f"CREATE OR REPLACE TABLE {fq}.reco_rationale\n"
            f"COMMENT 'FMAPI-generated recommendation rationale. Model: {model}.' AS\n"
            f"{_ai_rationale_select(fq, model, as_of)}")


def ai_rationale_overwrite_statement(fq, model, as_of):
    """Same FMAPI copy, written over a table that already holds rule-based text.

    The scheduled job writes the deterministic text first so the storefront is never
    left without copy, then overwrites it with the model's.
    """
    return (f"INSERT OVERWRITE {fq}.reco_rationale\n"
            f"{_ai_rationale_select(fq, model, as_of)}")


def rule_rationale_statement(fq, as_of):
    return f"""CREATE OR REPLACE TABLE {fq}.reco_rationale
COMMENT 'Rule-based recommendation rationale (Foundation Model API not used).' AS
{_rationale_ctx_sql(fq, as_of)}
SELECT customer_id,product_id,
  {_fallback_why_sql()} AS why_text,
  {_WHY_NOW_SQL} AS why_now_tag,
  'rule-based' AS generated_by
FROM ctx"""


def rationale_blank_fill_statement(fq, as_of):
    """Backfill any row the model returned empty, keeping the rows it did write."""
    return f"""INSERT OVERWRITE {fq}.reco_rationale
{_rationale_ctx_sql(fq, as_of)}
SELECT c.customer_id,c.product_id,
  COALESCE(NULLIF(TRIM(r.why_text),''), {_fallback_why_sql()}) AS why_text,
  {_WHY_NOW_SQL} AS why_now_tag,
  COALESCE(r.generated_by, 'rule-based') AS generated_by
FROM ctx c LEFT JOIN {fq}.reco_rationale r
  ON r.customer_id=c.customer_id AND r.product_id=c.product_id"""


def build_rationale(api, fq, model, as_of, n_recs, use_ai):
    """Generate the one-line 'why we picked this' copy for every recommendation."""
    if use_ai:
        LOG.step("engine", f"Writing rationale for {n_recs} recs via ai_query ({model})")
        LOG.detail("This calls the Foundation Model API once per recommendation; allow a few minutes.")
        try:
            api.sql(ai_rationale_statement(fq, model, as_of), timeout_s=3600)
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
            api.sql(rationale_blank_fill_statement(fq, as_of))
            return

    LOG.step("engine", "Writing rule-based rationale (no Foundation Model call)")
    api.sql(rule_rationale_statement(fq, as_of))


def describe_rationale_source(api, fq):
    """Read back what actually wrote the rationale.

    The FMAPI path can fail or come back partly empty and fall through to rule-based text,
    so the summary reports the `generated_by` column rather than what was requested.
    """
    try:
        rows = api.sql(f"SELECT generated_by, COUNT(*) AS n FROM {fq}.reco_rationale "
                       f"GROUP BY generated_by ORDER BY n DESC")
    except SqlError:
        return "unknown"
    if not rows:
        return "unknown"
    if len(rows) == 1:
        return str(rows[0][0])
    return ", ".join(f"{r[0]} ({r[1]} rows)" for r in rows)


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


# ---------------------------------------------------------------- step: scheduled pipeline

def pipeline_files(fq, model, as_of, use_ai):
    """The four SQL files the scheduled job runs, in order.

    Every statement here is the same one deploy.py just executed, so the job cannot
    drift away from what the one-shot deployment built.
    """
    header = ("-- Generated by deploy.py for the Lactalis medallion refresh job.\n"
              "-- Edit deploy.py, not this file: it is overwritten on every deployment.\n")

    def render(title, statements):
        body = ";\n\n".join(s.strip() for s in statements)
        return f"{header}-- {title}\n\n{body};\n"

    if use_ai:
        # Deterministic copy lands first so the storefront always has a sentence to show,
        # then the model's text overwrites it, then anything it left blank is refilled.
        rationale = [
            rule_rationale_statement(fq, as_of),
            reco_full_view_statement(fq),
            ai_rationale_overwrite_statement(fq, model, as_of),
            rationale_blank_fill_statement(fq, as_of),
        ]
    else:
        rationale = [rule_rationale_statement(fq, as_of), reco_full_view_statement(fq)]

    scored, candidates, oos_view, kpi_view = score_statements(fq, as_of)
    return [
        ("01_mutate_bronze.sql", render(
            "Move the demo on: stock, weather and fuel drift with the Brisbane day.",
            mutate_statements(fq, as_of))),
        ("02_promote_medallion.sql", render(
            "Bronze -> silver -> gold.", promote_statements(fq))),
        ("03_score_reco.sql", render(
            "Re-score recommendations and re-apply the out-of-stock guardrail.",
            [scored, candidates, oos_view, kpi_view])),
        ("04_write_rationale.sql", render(
            "Write the 'why we picked this' copy and refresh the storefront view.",
            rationale)),
    ]


def upload_pipeline_sql(api, path, files):
    api.post("/api/2.0/workspace/mkdirs", {"path": path})
    for name, body in files:
        api.request("POST", "/api/2.0/workspace/import", body={
            "path": f"{path}/{name}",
            "format": "RAW",
            "overwrite": True,
            "content": base64.b64encode(body.encode("utf-8")).decode("ascii"),
        })
    LOG.detail(f"Uploaded {len(files)} SQL files to {path}")


def set_job_schedule_paused(api, job_id, paused):
    """Flip only the schedule's pause flag. The cron and timezone are ours anyway."""
    api.post("/api/2.1/jobs/update", {"job_id": job_id, "new_settings": {"schedule": {
        "quartz_cron_expression": JOB_CRON,
        "timezone_id": JOB_TIMEZONE,
        "pause_status": "PAUSED" if paused else "UNPAUSED",
    }}})


def quiesce_pipeline_job(api, app_name):
    """Get the refresh job out of the way before rebuilding the tables underneath it.

    deploy.py rewrites bronze with CREATE OR REPLACE while the job's first task is
    UPDATE-ing the same tables. Delta rejects that with a concurrency conflict and the
    deployment dies half-built. Cancelling an in-flight run is not enough on its own: a
    deployment easily straddles 08:00 or 16:00, and the next trigger would land in the
    middle of it. So the schedule is paused for the duration and re-armed at the end.

    Returns the job id if it was paused here, so the caller knows to re-arm it. Never
    fatal: the worst case is a deployment that has to be re-run.
    """
    name = job_name(app_name)
    job_id = _find_job(api, name)
    if not job_id:
        return ""
    path = f"/api/2.1/jobs/runs/list?job_id={job_id}&active_only=true"
    try:
        set_job_schedule_paused(api, job_id, True)
        LOG.step("pipeline", "Paused the refresh schedule until this deployment finishes")
        LOG.detail("If the deployment stops early the schedule stays paused; re-running "
                   "deploy.py re-arms it.")
        if not api.get(path).get("runs"):
            return job_id
        LOG.detail("Cancelling the refresh run that is already in flight...")
        api.post("/api/2.1/jobs/runs/cancel-all", {"job_id": job_id})
        deadline = time.time() + 300
        while time.time() < deadline:
            if not api.get(path).get("runs"):
                LOG.detail("Refresh job is idle.")
                return job_id
            time.sleep(5)
        LOG.warn("pipeline", "A refresh job run is still active. If this deployment hits a "
                             "Delta concurrency error, wait for the run to finish and re-run.")
        return job_id
    except ApiError as e:
        LOG.warn("pipeline", f"Could not quiesce the refresh job ({e.message[:120]}). "
                             f"If this deployment fails on a Delta concurrency error, pause "
                             f"'{name}' in Workflows and re-run.")
        return ""


def _find_job(api, name):
    try:
        r = api.get(f"/api/2.1/jobs/list?name={urllib.parse.quote(name)}&limit=25")
    except ApiError:
        return None
    for job in r.get("jobs", []):
        if (job.get("settings") or {}).get("name") == name:
            return job.get("job_id")
    return None


def ensure_pipeline_job(api, catalog, schema, user, model, as_of, use_ai, app_name):
    """Create or update the twice-daily bronze-to-gold refresh job.

    Returns (job_id, sql_path). Never fatal: a workspace that will not let this user
    create jobs still gets a complete, working demo, just a static one.
    """
    fq = f"{ident(catalog)}.{ident(schema)}"
    sql_path = f"/Workspace/Users/{user}/{app_name}-pipeline"
    LOG.step("pipeline", f"Scheduling the medallion refresh ({JOB_CRON}, {JOB_TIMEZONE})")

    try:
        upload_pipeline_sql(api, sql_path, pipeline_files(fq, model, as_of, use_ai))
    except ApiError as e:
        LOG.warn("pipeline", f"Could not upload the pipeline SQL ({e.message[:160]}). "
                             f"The demo is deployed but will not refresh on a schedule.")
        return "", ""

    def task(key, filename, depends=None):
        spec = {
            "task_key": key,
            "sql_task": {
                "warehouse_id": api.warehouse_id,
                "file": {"path": f"{sql_path}/{filename}", "source": "WORKSPACE"},
            },
            "timeout_seconds": 3600,
        }
        if depends:
            spec["depends_on"] = [{"task_key": depends}]
        return spec

    name = job_name(app_name)
    settings = {
        "name": name,
        "description": (f"Refreshes {catalog}.{schema} bronze -> silver -> gold, re-scores "
                        f"recommendations and rewrites the AI rationale."),
        # The tasks are whole-table CREATE OR REPLACE rebuilds, so two runs at once (a
        # manual trigger overlapping the schedule) would race. Queue them instead.
        "max_concurrent_runs": 1,
        "schedule": {
            "quartz_cron_expression": JOB_CRON,
            "timezone_id": JOB_TIMEZONE,
            "pause_status": "UNPAUSED",
        },
        "tags": {"demo": "lactalis-reco-engine"},
        "tasks": [
            task("mutate_bronze", "01_mutate_bronze.sql"),
            task("promote_medallion", "02_promote_medallion.sql", "mutate_bronze"),
            task("score_reco", "03_score_reco.sql", "promote_medallion"),
            task("write_rationale", "04_write_rationale.sql", "score_reco"),
        ],
    }

    try:
        job_id = _find_job(api, name)
        if job_id:
            api.post("/api/2.1/jobs/reset", {"job_id": job_id, "new_settings": settings})
            LOG.detail(f"Updated existing job {job_id}")
        else:
            job_id = api.post("/api/2.1/jobs/create", settings).get("job_id")
            LOG.detail(f"Created job {job_id}")
    except ApiError as e:
        LOG.warn("pipeline", f"Could not create the refresh job ({e.message[:160]}). "
                             f"The demo is deployed but will not refresh on a schedule.")
        return "", sql_path
    return str(job_id), sql_path


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
    views = (("mv_sales_performance", sales_yaml), ("mv_reco_performance", reco_yaml))
    made = 0
    for name, body in views:
        try:
            api.sql(f"CREATE OR REPLACE VIEW {fq}.{ident(name)} "
                    f"WITH METRICS LANGUAGE YAML AS $$\n{body}$$")
            made += 1
        except SqlError as e:
            LOG.warn("metrics", f"Skipped metric view {name}: {e.message[:160]}")
    if made:
        LOG.detail(f"{made} metric view(s) ready.")
    return made, len(views)


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

APP_WAREHOUSE_RESOURCE = "sql-warehouse"


def merge_app_resources(existing, warehouse_id):
    """Add our SQL warehouse resource without discarding resources someone else bound.

    The apps API replaces the whole resources list on update, so sending only ours would
    silently unbind a secret or model endpoint the app already depended on. That matters
    most when deploying on top of an app the customer built themselves.
    """
    ours = {
        "name": APP_WAREHOUSE_RESOURCE,
        "description": "SQL warehouse serving the Lactalis gold layer.",
        "sql_warehouse": {"id": warehouse_id, "permission": "CAN_USE"},
    }
    kept = [r for r in (existing or []) if r.get("name") != APP_WAREHOUSE_RESOURCE]
    return kept + [ours]


def ensure_app(api, app_name, warehouse_id, must_exist=False):
    """Create the app (with its SQL warehouse resource bound) or update it in place.

    With must_exist, a missing app is an error rather than a prompt to create one: the
    caller asked to deploy onto something that already exists, and quietly creating a
    second app under a mistyped name is worse than stopping.
    """
    description = "Lactalis B2B Personalized Recommendation Engine (MyLactalis storefront + Engine Console)."

    try:
        app = api.get(f"/api/2.0/apps/{app_name}")
        exists = True
    except ApiError as e:
        if e.status != 404:
            raise
        app, exists = None, False

    if not exists and must_exist:
        raise DeployError(
            f"No Databricks App named '{app_name}' exists in this workspace.\n"
            f"  --existing-app only deploys onto an app that is already there, so nothing\n"
            f"  was created. Check the spelling in Compute > Apps, or drop --existing-app\n"
            f"  to let the deployer create the app for you."
        )

    if exists:
        if must_exist:
            LOG.step("app", f"Deploying onto existing app '{app_name}'")
            LOG.detail("Its current source is replaced by this demo; bound resources are kept.")
        else:
            LOG.step("app", f"Reusing existing app '{app_name}'")
        resources = merge_app_resources(app.get("resources"), warehouse_id)
        carried = len(resources) - 1
        if carried:
            LOG.detail(f"Keeping {carried} resource(s) already bound to this app.")
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
                "resources": merge_app_resources(None, warehouse_id),
            })
        except ApiError as e:
            raise DeployError(
                f"Could not create the Databricks App '{app_name}' ({e.status}: {e.message[:250]}).\n"
                f"  Databricks Apps must be enabled for this workspace and you need permission\n"
                f"  to create apps. Check Compute > Apps in the workspace UI.\n"
                f"  If the app already exists and you meant to deploy onto it, re-run with\n"
                f"  --existing-app {app_name}."
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
        ("bronze landed", f"SELECT COUNT(*) FROM {fq}.{ident(BRONZE + 'fact_orders')}", lambda v: v > 0),
        # Every layer must carry the same rows through, or a promotion silently dropped data.
        ("medallion row counts agree",
         "SELECT SUM(ABS(d)) FROM (" + " UNION ALL ".join(
             f"SELECT (SELECT COUNT(*) FROM {fq}.{ident(SILVER + t)}) "
             f"- (SELECT COUNT(*) FROM {fq}.{ident(t)}) AS d"
             for t in SEED_TABLES) + ")",
         lambda v: v == 0),
        ("no orphan recommendations",
         f"SELECT COUNT(*) FROM {fq}.reco_candidates r WHERE NOT EXISTS "
         f"(SELECT 1 FROM {fq}.dim_product p WHERE p.product_id=r.product_id)", lambda v: v == 0),
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


def verify_backend(api, catalog, schema, sp, genie_space_id, dashboard_id=""):
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
        acl_ok, acl_detail = _object_acl(api, f"/api/2.0/permissions/genie/{genie_space_id}",
                                         sp, ("CAN_RUN", "CAN_EDIT", "CAN_MANAGE"))
        ok = _report("service principal on Genie space", acl_detail, acl_ok) and ok
        answer_ok, answer_detail = _genie_ask(api, genie_space_id)
        ok = _report("Genie question", answer_detail, answer_ok) and ok

    if dashboard_id:
        acl_ok, acl_detail = _object_acl(api, f"/api/2.0/permissions/dashboards/{dashboard_id}",
                                         sp, ("CAN_READ", "CAN_EDIT", "CAN_RUN", "CAN_MANAGE"))
        ok = _report("service principal on dashboard", acl_detail, acl_ok) and ok
    return ok


def _object_acl(api, path, sp, accepted):
    """Check the service principal's permission entry on a workspace object."""
    try:
        acl = api.get(path).get("access_control_list", [])
    except ApiError as e:
        return False, f"error: {e.message[:120]}"
    for entry in acl:
        if entry.get("service_principal_name") == sp:
            levels = [p.get("permission_level") for p in entry.get("all_permissions", [])]
            return (any(l in accepted for l in levels),
                    ", ".join(x for x in levels if x) or "no permission level")
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
    """True when an object's serialized definition mentions catalog.schema.

    An absent field is reported rather than read as "no match", because that would
    quietly spare an object that should have been removed.
    """
    try:
        body = api.get(path)
    except ApiError as e:
        LOG.warn("destroy", f"Could not inspect {path.split('?')[0]} ({e.message[:120]}); "
                            f"leaving it in place.")
        return False
    if field not in body:
        LOG.warn("destroy", f"{path.split('?')[0]} returned no '{field}', so its contents "
                            f"could not be checked; leaving it in place.")
        return False
    return marker in (body.get(field) or "")


def destroy(api, args, user):
    """Remove everything the deployer creates. Never touches the catalog itself."""
    targets = []

    try:
        api.get(f"/api/2.0/apps/{args.app_name}")
        if args.existing_app:
            LOG.step("destroy", f"Leaving app '{args.app_name}' in place (it was not created "
                                f"by this deployer).")
        else:
            targets.append(("app", args.app_name))
    except ApiError:
        pass

    src_path = f"/Workspace/Users/{user}/{args.app_name}"
    try:
        api.get(f"/api/2.0/workspace/get-status?path={urllib.parse.quote(src_path)}")
        targets.append(("app source folder", src_path))
    except ApiError:
        pass

    jname = job_name(args.app_name)
    job_id = _find_job(api, jname)
    if job_id:
        targets.append(("refresh job", f"{jname} ({job_id})"))

    sql_path = f"/Workspace/Users/{user}/{args.app_name}-pipeline"
    try:
        api.get(f"/api/2.0/workspace/get-status?path={urllib.parse.quote(sql_path)}")
        targets.append(("pipeline SQL folder", sql_path))
    except ApiError:
        pass

    # Only remove a Genie space or dashboard that actually points at the schema being
    # destroyed. Both are found by a fixed name, so a workspace with more than one
    # deployment must not lose the other one's objects.
    marker = f"{args.catalog}.{args.schema}."

    genie_id = _find_genie_space(api, GENIE_TITLE)
    # The Genie GET omits serialized_space unless it is asked for, and a missing field would
    # read as "does not reference this schema" and silently spare a space that should go.
    if genie_id and not _references_schema(
            api, f"/api/2.0/genie/spaces/{genie_id}?include_serialized_space=true",
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

    # Only now, past the point of no return: a refresh running through the teardown would
    # recreate tables in the schema being dropped. Pausing before the prompt would leave
    # the schedule off for anyone who answered no.
    quiesce_pipeline_job(api, args.app_name)

    for kind, name in targets:
        try:
            if kind == "app":
                api.delete(f"/api/2.0/apps/{name}")
            elif kind in ("app source folder", "pipeline SQL folder"):
                api.post("/api/2.0/workspace/delete", {"path": name, "recursive": True})
            elif kind == "refresh job":
                # Jobs delete is a POST with the id in the body, not a REST DELETE.
                api.post("/api/2.1/jobs/delete", {"job_id": job_id})
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
    p.add_argument("--user", help="Your workspace login email. Only needed if the automatic "
                                  "username lookup fails (e.g. a brand-new workspace where SCIM "
                                  "Me returns 400); used to build /Workspace/Users/<you>/ paths.")
    p.add_argument("--catalog", default="lactalis_catalog", help="Unity Catalog catalog (default: lactalis_catalog)")
    p.add_argument("--schema", default="reco", help="Schema inside the catalog (default: reco)")
    p.add_argument("--warehouse-id", help="SQL warehouse id (default: auto-pick a serverless one)")
    p.add_argument("--model", default="auto",
                   help="Foundation Model endpoint for the rationale (default: auto-pick an available one)")
    # Defaulted in validate_names, not here: leaving it None is the only way to tell
    # "not passed" from "passed the default value", which --existing-app has to know.
    p.add_argument("--app-name", default=None,
                   help=f"Databricks App name (default: {DEFAULT_APP_NAME})")
    p.add_argument("--existing-app", metavar="NAME",
                   help="Deploy on top of an app that already exists, instead of creating one. "
                        "The app must already be there: if it is not, the deployer stops rather "
                        "than quietly creating a second app under a mistyped name. --destroy "
                        "leaves it in place, because it is not ours to delete.")
    p.add_argument("--as-of", default="auto",
                   help="Demo 'as of' date driving the contextual signals (default: auto-detect from the seed data)")
    p.add_argument("--skip-app", action="store_true", help="Build the data, engine, Genie and dashboard but not the app")
    p.add_argument("--skip-job", action="store_true",
                   help="Do not create the twice-daily bronze-to-gold refresh job")
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

    if args.existing_app:
        if args.app_name is not None and args.app_name != args.existing_app:
            raise DeployError(
                f"--app-name '{args.app_name}' and --existing-app '{args.existing_app}' name two "
                f"different apps.\n"
                f"  Pass only --existing-app {args.existing_app}."
            )
        args.app_name = args.existing_app
    elif args.app_name is None:
        args.app_name = DEFAULT_APP_NAME

    if not re.match(r"^[a-z0-9-]{2,30}$", args.app_name):
        flag = "--existing-app" if args.existing_app else "--app-name"
        raise DeployError(
            f"{flag} '{args.app_name}' must be 2-30 characters of lowercase letters, digits and hyphens.")


def run(args):
    validate_names(args)
    host, credentials, auth_label = resolve_auth(args)
    api = Api(host, credentials, auth_label)

    print()
    print("=" * 72)
    title = "remove the demo" if args.destroy else "deployer"
    print(f"  Lactalis B2B Personalized Recommendation Engine - {title}")
    print("=" * 72)
    print(f"  Workspace : {api.host}")
    print(f"  Auth      : {api.auth_label}")
    print(f"  Target    : {args.catalog}.{args.schema}")
    print(f"  App       : {args.app_name}"
          f"{' (existing, will not be deleted by --destroy)' if args.existing_app else ''}")
    print("=" * 72)
    print()

    if args.destroy:
        user = resolve_username(api, args.user)
        LOG.detail(f"Authenticated as {user}")
        ensure_warehouse(api, args.warehouse_id)
        return destroy(api, args, user)

    user, warehouse, model = preflight(api, args)
    catalog = resolve_catalog(api, args.catalog, user, args.yes)

    paused_job = quiesce_pipeline_job(api, args.app_name)
    build_data_layer(api, catalog, args.schema)
    as_of = resolve_as_of(api, catalog, args.schema, args.as_of)
    build_reco_engine(api, catalog, args.schema, model, as_of, rationale=not args.no_rationale)
    rationale_source = describe_rationale_source(api, f"{ident(catalog)}.{ident(args.schema)}")
    views_made, views_total = build_metric_views(api, catalog, args.schema)

    job_id = ""
    if not args.skip_job:
        # Writes the full settings, including pause_status UNPAUSED, so this re-arms the
        # schedule that quiesce_pipeline_job paused.
        job_id, _ = ensure_pipeline_job(api, catalog, args.schema, user, model, as_of,
                                        not args.no_rationale, args.app_name)
    elif paused_job:
        # --skip-job means "leave the job alone", not "silently turn its schedule off".
        try:
            set_job_schedule_paused(api, paused_job, False)
            LOG.step("pipeline", "Left the existing refresh job as it was and re-armed it")
        except ApiError as e:
            LOG.warn("pipeline", f"Could not re-arm the refresh schedule ({e.message[:120]}). "
                                 f"Unpause '{job_name(args.app_name)}' in Workflows.")

    # An object that was asked for but could not be built is a failure, not a skip, and the
    # summary and exit code have to tell those two apart.
    genie_id = "" if args.skip_genie else build_genie(api, catalog, args.schema, user)
    dashboard_id, dashboard_failed = "", False
    if not args.skip_dashboard:
        dashboard_id = build_dashboard(api, catalog, args.schema, user)
        dashboard_failed = not dashboard_id

    data_ok = verify_data(api, catalog, args.schema)

    app_url, app_ok = "", None
    if not args.skip_app:
        app, sp = ensure_app(api, args.app_name, api.warehouse_id,
                             must_exist=bool(args.existing_app))
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
                app_ok = verify_backend(api, catalog, args.schema, sp, genie_id, dashboard_id)
            else:
                app_ok = result == "ok"

    print()
    print("=" * 72)
    print("  Deployment summary")
    print("=" * 72)
    print(f"  Catalog / schema : {catalog}.{args.schema}")
    print(f"  SQL warehouse    : {warehouse.get('name')} ({api.warehouse_id})")
    print(f"  Rationale source : {rationale_source}")
    print(f"  Metric views     : {views_made} of {views_total}"
          f"{'' if views_made == views_total else '  FAILED (see above)'}")
    print(f"  Genie space      : {genie_id or '(skipped)'}")
    if genie_id:
        print(f"                     {api.host}/genie/rooms/{genie_id}")
    print(f"  Dashboard        : {dashboard_id or ('FAILED (see above)' if dashboard_failed else '(skipped)')}")
    if dashboard_id:
        print(f"                     {api.host}/dashboardsv3/{dashboard_id}/published")
    print(f"  Refresh job      : {job_id or '(skipped)'}")
    if job_id:
        print(f"                     {JOB_CRON} {JOB_TIMEZONE} (08:00 and 16:00 daily)")
        print(f"                     {api.host}/jobs/{job_id}")
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

    failed = ((not data_ok) or (app_ok is False) or dashboard_failed
              or views_made != views_total)
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
