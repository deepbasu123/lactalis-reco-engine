// Customer-facing + shared data endpoints. Every query is built to the schema
// contract in docs/2026-08-07-lactalis-reco-engine-design.md and tolerates a
// not-yet-created gold layer (returns empty-but-valid shapes).

import { Router } from 'express';
import { safeQuery } from '../db.js';
import { T } from '../config.js';

const router = Router();

// Static, doc-sourced descriptions for the 5 segments (explanatory copy only;
// counts and any data come from Unity Catalog). Codes/labels match the contract.
const SEGMENT_INFO = {
  'P&C': {
    label: 'Petrol & Convenience',
    blurb: 'Forecourt and convenience retail. Impulse single-serve and flavored milk skew hard with weather and fuel traffic.',
    accent: '#5BC5F2',
  },
  RESTAURANT: {
    label: 'Restaurants & Cafes',
    blurb: 'Foodservice kitchens. Cooking cream, butter and barista milk are the workhorse lines; menu-led cross-sell.',
    accent: '#004B85',
  },
  INSTITUTION: {
    label: 'Schools, Hospitals & Aged Care',
    blurb: 'Nutrition-compliant lines and predictable term-time reordering. Compliance is a hard filter, not a preference.',
    accent: '#3F4097',
  },
  BAKERY: {
    label: 'Bakeries',
    blurb: 'Butter, cream and specialty cheese in volume. Reorder cadence tracks production schedules closely.',
    accent: '#E8C759',
  },
  GROCER: {
    label: 'Independent Grocers',
    blurb: 'Broad basket across milk, cheese and yogurt. Shelf-share growth via adjacent-category cross-sell.',
    accent: '#0A7C6A',
  },
};

// GET /api/customers — account switcher list.
router.get('/customers', async (req, res, next) => {
  try {
    const rows = await safeQuery(
      `SELECT customer_id, customer_name, segment, segment_label, dc_id, dc_name,
              region, city, profile_note, account_tier, source_system
       FROM ${T.dimCustomer}
       ORDER BY segment, account_tier, customer_name`
    );
    res.json({ customers: rows, count: rows.length });
  } catch (err) {
    next(err);
  }
});

// GET /api/customer/:id — single customer profile.
router.get('/customer/:id', async (req, res, next) => {
  try {
    const rows = await safeQuery(
      `SELECT customer_id, customer_name, segment, segment_label, dc_id, dc_name,
              region, city, profile_note, account_tier, source_system
       FROM ${T.dimCustomer}
       WHERE customer_id = :customer_id`,
      [{ name: 'customer_id', value: req.params.id }]
    );
    res.json({ customer: rows[0] || null });
  } catch (err) {
    next(err);
  }
});

// GET /api/customer/:id/favorites — habitual reorder items with product detail.
router.get('/customer/:id/favorites', async (req, res, next) => {
  try {
    const rows = await safeQuery(
      `SELECT f.customer_id, f.product_id, f.reorder_frequency_days, f.last_ordered_date,
              p.product_name, p.brand, p.category, p.pack_size, p.unit_price,
              p.is_flavored_milk, p.is_single_serve, p.is_cooking_cream,
              p.is_nutrition_compliant, p.is_hot_beverage
       FROM ${T.customerFavorites} f
       JOIN ${T.dimProduct} p ON f.product_id = p.product_id
       WHERE f.customer_id = :customer_id
       ORDER BY f.last_ordered_date DESC`,
      [{ name: 'customer_id', value: req.params.id }]
    );
    res.json({ favorites: rows, count: rows.length });
  } catch (err) {
    next(err);
  }
});

// GET /api/customer/:id/recommendations — reads vw_reco_full (single-query contract).
router.get('/customer/:id/recommendations', async (req, res, next) => {
  try {
    const rows = await safeQuery(
      `SELECT * FROM ${T.vwRecoFull}
       WHERE customer_id = :customer_id
       ORDER BY rank`,
      [{ name: 'customer_id', value: req.params.id }]
    );
    res.json({ recommendations: rows, count: rows.length });
  } catch (err) {
    next(err);
  }
});

// GET /api/segments — segment explorer: counts from UC + doc-sourced characteristics.
router.get('/segments', async (req, res, next) => {
  try {
    const counts = await safeQuery(
      `SELECT segment, ANY_VALUE(segment_label) AS segment_label,
              COUNT(*) AS customer_count,
              COUNT(DISTINCT region) AS region_count,
              COUNT(DISTINCT dc_id) AS dc_count
       FROM ${T.dimCustomer}
       GROUP BY segment`
    );

    // Top product categories per segment, from real order history (enrichment; may be empty).
    const topCats = await safeQuery(
      `SELECT segment, category, orders_in_cat FROM (
         SELECT c.segment, p.category, COUNT(*) AS orders_in_cat,
                ROW_NUMBER() OVER (PARTITION BY c.segment ORDER BY COUNT(*) DESC) AS rn
         FROM ${T.factOrders} o
         JOIN ${T.dimCustomer} c ON o.customer_id = c.customer_id
         JOIN ${T.dimProduct} p ON o.product_id = p.product_id
         GROUP BY c.segment, p.category
       ) WHERE rn <= 3
       ORDER BY segment, orders_in_cat DESC`
    );

    const catBySegment = {};
    for (const r of topCats) {
      (catBySegment[r.segment] ||= []).push(r.category);
    }

    // Merge live counts with doc-sourced descriptions; keep the 5 canonical segments
    // present even before data lands so the explorer always renders.
    const byCode = Object.fromEntries(counts.map((c) => [c.segment, c]));
    const segments = Object.entries(SEGMENT_INFO).map(([code, info]) => ({
      segment: code,
      segment_label: byCode[code]?.segment_label || info.label,
      blurb: info.blurb,
      accent: info.accent,
      customer_count: byCode[code]?.customer_count ?? 0,
      region_count: byCode[code]?.region_count ?? 0,
      dc_count: byCode[code]?.dc_count ?? 0,
      top_categories: catBySegment[code] || [],
    }));

    res.json({ segments, total_customers: counts.reduce((a, c) => a + (c.customer_count || 0), 0) });
  } catch (err) {
    next(err);
  }
});

// GET /api/kpis — headline metrics computed transparently from the gold layer.
// Definitions follow the contract; raw counts are returned so nothing is opaque.
router.get('/kpis', async (req, res, next) => {
  try {
    const conv = await safeQuery(
      `WITH surfaced AS (SELECT DISTINCT customer_id, product_id FROM ${T.recoCandidates}),
            converted AS (
              SELECT DISTINCT s.customer_id, s.product_id
              FROM surfaced s
              JOIN ${T.factOrders} o
                ON o.customer_id = s.customer_id AND o.product_id = s.product_id
            )
       SELECT (SELECT COUNT(*) FROM surfaced)  AS recs_surfaced,
              (SELECT COUNT(*) FROM converted) AS recs_converted`
    );

    const fulfill = await safeQuery(
      `SELECT COUNT(*) AS total_lines,
              SUM(CASE WHEN in_stock THEN 1 ELSE 0 END) AS in_stock_lines
       FROM ${T.stockByDc}`
    );

    const reach = await safeQuery(
      `SELECT COUNT(DISTINCT customer_id) AS customers_served,
              COUNT(*) AS total_recs
       FROM ${T.recoCandidates}`
    );

    const surfaced = conv[0]?.recs_surfaced ?? 0;
    const converted = conv[0]?.recs_converted ?? 0;
    const totalLines = fulfill[0]?.total_lines ?? 0;
    const inStock = fulfill[0]?.in_stock_lines ?? 0;

    res.json({
      conversion: {
        label: 'Upsell / cross-sell conversion',
        rate: surfaced > 0 ? converted / surfaced : null,
        numerator: converted,
        denominator: surfaced,
        definition: 'Recommended SKUs later ordered ÷ recommendations surfaced',
      },
      fulfillment: {
        label: 'Order fulfillment readiness',
        rate: totalLines > 0 ? inStock / totalLines : null,
        numerator: inStock,
        denominator: totalLines,
        definition: 'SKU-DC lines in stock ÷ all SKU-DC lines (the guardrail made visible)',
      },
      reach: {
        customers_served: reach[0]?.customers_served ?? 0,
        total_recommendations: reach[0]?.total_recs ?? 0,
      },
      source: 'gold-layer',
    });
  } catch (err) {
    next(err);
  }
});

export default router;
