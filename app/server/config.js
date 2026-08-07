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
  host: normalizeHost(process.env.DATABRICKS_HOST || 'https://fevm-lactalis.cloud.databricks.com'),

  // Warehouse the app queries. Design contract warehouse is the default so the app
  // boots even before a `sql-warehouse` App resource is attached.
  warehouseId: process.env.DATABRICKS_WAREHOUSE_ID || '84b2d6209ac2f455',

  // Unity Catalog location of the gold/reco layer.
  catalog: process.env.RECO_CATALOG || 'lactalis_catalog',
  schema: process.env.RECO_SCHEMA || 'reco',

  // Optional resources. When unset the relevant UI features degrade gracefully.
  genieSpaceId: process.env.GENIE_SPACE_ID || '',
  dashboardId: process.env.DASHBOARD_ID || '',

  // Local dev convenience: profile used to mint a token when no token/secret is set.
  profile: process.env.DATABRICKS_CONFIG_PROFILE || process.env.DATABRICKS_PROFILE || 'fe-vm-lactalis',

  port: parseInt(process.env.PORT || '8000', 10),
  isProd: process.env.NODE_ENV === 'production',
};

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
