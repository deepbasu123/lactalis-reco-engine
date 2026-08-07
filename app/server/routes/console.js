// Engine Console endpoints — the internal "how the engine works" surface.
// These power the three hero demo moments and the governance/lineage callout.

import { Router } from 'express';
import { safeQuery } from '../db.js';
import { T, config } from '../config.js';

const router = Router();

// GET /api/console/oos-demo
// Hero moment (b): the out-of-stock guardrail. Surfaces SKU-DC lines that are
// held back because on_hand_units < threshold_units (in_stock = FALSE). These are
// exactly the items the ranker would otherwise be free to recommend.
router.get('/oos-demo', async (req, res, next) => {
  try {
    const held = await safeQuery(
      `SELECT s.dc_id, s.product_id, s.on_hand_units, s.threshold_units, s.in_stock,
              p.product_name, p.brand, p.category, p.pack_size, p.unit_price,
              d.dc_name
       FROM ${T.stockByDc} s
       JOIN ${T.dimProduct} p ON s.product_id = p.product_id
       LEFT JOIN (SELECT DISTINCT dc_id, dc_name FROM ${T.dimCustomer}) d ON s.dc_id = d.dc_id
       WHERE s.in_stock = FALSE
       ORDER BY (s.threshold_units - s.on_hand_units) DESC
       LIMIT 6`
    );

    // A companion in-stock example for contrast in the UI.
    const passing = await safeQuery(
      `SELECT s.dc_id, s.product_id, s.on_hand_units, s.threshold_units, s.in_stock,
              p.product_name, p.brand, p.category, p.pack_size, p.unit_price,
              d.dc_name
       FROM ${T.stockByDc} s
       JOIN ${T.dimProduct} p ON s.product_id = p.product_id
       LEFT JOIN (SELECT DISTINCT dc_id, dc_name FROM ${T.dimCustomer}) d ON s.dc_id = d.dc_id
       WHERE s.in_stock = TRUE
       ORDER BY (s.on_hand_units - s.threshold_units) DESC
       LIMIT 3`
    );

    res.json({
      held_back: held,
      passing,
      rule: 'JOIN stock_by_dc ... WHERE in_stock = TRUE  — removes, never deprioritizes',
    });
  } catch (err) {
    next(err);
  }
});

// GET /api/console/weather-signal
// Hero moment (a): weather -> flavored milk. Returns active hot/heatwave weather rows
// and the flavored-milk SKUs whose context_boost they drive.
router.get('/weather-signal', async (req, res, next) => {
  try {
    const weather = await safeQuery(
      `SELECT w.dc_id, w.signal_date, w.temp_c, w.condition, w.is_heatwave,
              d.dc_name, d.region
       FROM ${T.signalWeather} w
       LEFT JOIN (SELECT DISTINCT dc_id, dc_name, region FROM ${T.dimCustomer}) d
              ON w.dc_id = d.dc_id
       WHERE w.condition = 'Hot' OR w.is_heatwave = TRUE
       ORDER BY w.is_heatwave DESC, w.temp_c DESC
       LIMIT 8`
    );

    const flavored = await safeQuery(
      `SELECT product_id, product_name, brand, category, pack_size, unit_price
       FROM ${T.dimProduct}
       WHERE is_flavored_milk = TRUE
       ORDER BY unit_price DESC
       LIMIT 6`
    );

    // Recs actually attributed to the weather trigger by the engine (proof it fired).
    // trigger_signal is selected explicitly so the field is present in the response
    // (contract: reco_candidates.trigger_signal), consistent with the segment query.
    const triggered = await safeQuery(
      `SELECT customer_id, product_id, rank, affinity_score, context_boost, final_score,
              reco_type, trigger_signal
       FROM ${T.recoCandidates}
       WHERE trigger_signal = 'weather'
       ORDER BY final_score DESC
       LIMIT 8`
    );

    res.json({ weather, flavored_products: flavored, weather_triggered_recs: triggered });
  } catch (err) {
    next(err);
  }
});

// GET /api/console/segment-personalization
// Hero moment (c): same context, different recs per segment. Groups surfaced recs by
// the recipient's segment so the UI can show school vs restaurant vs P&C side by side.
router.get('/segment-personalization', async (req, res, next) => {
  try {
    const rows = await safeQuery(
      `SELECT c.segment, c.segment_label, r.customer_id, cu.customer_name,
              r.product_id, p.product_name, p.brand, p.category, p.pack_size,
              r.rank, r.reco_type, r.trigger_signal, r.final_score
       FROM ${T.recoCandidates} r
       JOIN ${T.dimCustomer} c  ON r.customer_id = c.customer_id
       JOIN ${T.dimCustomer} cu ON r.customer_id = cu.customer_id
       JOIN ${T.dimProduct} p   ON r.product_id = p.product_id
       ORDER BY c.segment, r.customer_id, r.rank`
    );

    const bySegment = {};
    for (const r of rows) {
      (bySegment[r.segment] ||= { segment: r.segment, segment_label: r.segment_label, examples: [] })
        .examples.push(r);
    }
    res.json({ segments: Object.values(bySegment) });
  } catch (err) {
    next(err);
  }
});

// GET /api/console/lineage
// Governance / lineage callout. Reads the real source_system tags that exist on the
// gold tables (proving provenance) and renders the medallion story around them.
router.get('/lineage', async (req, res, next) => {
  try {
    // Distinct source systems actually present, per gold table.
    const probes = [
      { table: 'dim_customer', expr: T.dimCustomer },
      { table: 'dim_product', expr: T.dimProduct },
      { table: 'fact_orders', expr: T.factOrders },
      { table: 'customer_favorites', expr: T.customerFavorites },
      { table: 'stock_by_dc', expr: T.stockByDc },
    ];
    const results = await Promise.all(
      probes.map(async (p) => {
        const rows = await safeQuery(
          `SELECT DISTINCT source_system FROM ${p.expr} WHERE source_system IS NOT NULL ORDER BY source_system`
        );
        return { table: p.table, source_systems: rows.map((r) => r.source_system) };
      })
    );

    res.json({
      catalog: config.catalog,
      schema: config.schema,
      medallion: [
        { layer: 'Bronze', prefix: 'bz_*', note: 'Raw ingest, source-tagged as received' },
        { layer: 'Silver', prefix: 'sv_*', note: 'Cleaned, conformed, deduplicated' },
        { layer: 'Gold', prefix: 'dim_/fact_/signal_/reco_', note: 'App-facing, governed, lineage-tracked' },
      ],
      sources: [
        { system: 'SAP', role: 'Product master, orders, stock' },
        { system: 'Salesforce', role: 'Customer / CRM master' },
        { system: 'Alright Commerce', role: 'Orders, favorites (MyLactalis platform)' },
      ],
      table_sources: results,
    });
  } catch (err) {
    next(err);
  }
});

export default router;
