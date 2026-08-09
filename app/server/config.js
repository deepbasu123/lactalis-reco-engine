// Central configuration for the Lactalis Reco Engine backend.
// All values are env-overridable so the same build runs locally and in Databricks Apps.

function normalizeHost(raw) {
  if (!raw) return '';
  let h = raw.trim();
  // In Databricks Apps, DATABRICKS_HOST is injected as a bare hostname (no scheme).
  if (!/^https?:\/\//i.test(h)) h = `https://${h}`;
  // strip trailing slash
  return h.replace(/\/+$/, '');
}

export const config = {
  // Injected by the Databricks Apps runtime in production.
  host: normalizeHost(process.env.DATABRICKS_HOST || ''),

  // Bound to the app's `sql-warehouse` resource in production. No default: a wrong
  // warehouse id fails in a confusing way, an empty one fails with a clear message.
  warehouseId: process.env.DATABRICKS_WAREHOUSE_ID || '',

  // Unity Catalog location of the gold/reco layer.
  catalog: process.env.RECO_CATALOG || 'lactalis_catalog',
  schema: process.env.RECO_SCHEMA || 'reco',

  // Optional resources. When unset the relevant UI features degrade gracefully.
  genieSpaceId: process.env.GENIE_SPACE_ID || '',
  dashboardId: process.env.DASHBOARD_ID || '',

  // Local dev convenience: profile used to mint a token when no token/secret is set.
  profile: process.env.DATABRICKS_CONFIG_PROFILE || process.env.DATABRICKS_PROFILE || '',

  port: parseInt(process.env.PORT || '8000', 10),
  isProd: process.env.NODE_ENV === 'production',
};

/**
 * Misconfiguration the operator can fix, reported as a list of human-readable strings.
 * Checked at startup and surfaced through /api/health rather than crashing the process,
 * so the Apps runtime still routes to us and the message is visible in the app logs.
 */
export function configProblems() {
  const problems = [];
  if (!config.host) {
    problems.push('DATABRICKS_HOST is not set.');
  }
  if (!config.warehouseId) {
    problems.push(
      'DATABRICKS_WAREHOUSE_ID is not set. In Databricks Apps this comes from the ' +
        "app's `sql-warehouse` resource — re-run deploy.py to attach it."
    );
  }
  return problems;
}

// Fully-qualified table/view names, resolved from catalog+schema.
const q = (name) => `\`${config.catalog}\`.\`${config.schema}\`.\`${name}\``;

export const T = {
  dimCustomer: q('dim_customer'),
  dimProduct: q('dim_product'),
  factOrders: q('fact_orders'),
  customerFavorites: q('customer_favorites'),
  stockByDc: q('stock_by_dc'),
  signalWeather: q('signal_weather'),
  signalFuel: q('signal_fuel_index'),
  signalCalendar: q('signal_calendar'),
  recoCandidates: q('reco_candidates'),
  recoRationale: q('reco_rationale'),
  vwRecoFull: q('vw_reco_full'),
};
