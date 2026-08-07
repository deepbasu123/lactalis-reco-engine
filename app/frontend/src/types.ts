// Types mirror the schema contract in
// docs/2026-08-07-lactalis-reco-engine-design.md. Fields are optional where the
// gold layer may not yet exist, so the UI can render empty states safely.

export interface Customer {
  customer_id: string;
  customer_name: string;
  segment: string;
  segment_label: string;
  dc_id?: string;
  dc_name?: string;
  region?: string;
  city?: string;
  profile_note?: string;
  account_tier?: string;
  source_system?: string;
}

export interface Product {
  product_id: string;
  product_name: string;
  brand: string;
  category: string;
  pack_size: string;
  unit_price: number;
  is_flavored_milk?: boolean;
  is_single_serve?: boolean;
  is_cooking_cream?: boolean;
  is_nutrition_compliant?: boolean;
  is_hot_beverage?: boolean;
}

export interface Favorite extends Product {
  customer_id: string;
  reorder_frequency_days?: number;
  last_ordered_date?: string;
}

// One row of vw_reco_full (candidates + rationale + product + customer joined).
export interface Recommendation {
  customer_id: string;
  product_id: string;
  rank: number;
  affinity_score?: number;
  context_boost?: number;
  final_score?: number;
  reco_type?: string; // cross-sell | upsell
  trigger_signal?: string; // weather | fuel | calendar | segment
  in_stock?: boolean;
  // product fields
  product_name?: string;
  brand?: string;
  category?: string;
  pack_size?: string;
  unit_price?: number;
  // rationale fields
  why_text?: string;
  why_now_tag?: string;
  generated_by?: string;
}

export interface Segment {
  segment: string;
  segment_label: string;
  blurb: string;
  accent: string;
  customer_count: number;
  region_count: number;
  dc_count: number;
  top_categories: string[];
}

export interface StockLine {
  dc_id: string;
  dc_name?: string;
  product_id: string;
  product_name: string;
  brand: string;
  category: string;
  pack_size: string;
  unit_price: number;
  on_hand_units: number;
  threshold_units: number;
  in_stock: boolean;
}

export interface WeatherRow {
  dc_id: string;
  dc_name?: string;
  region?: string;
  signal_date: string;
  temp_c: number;
  condition: string;
  is_heatwave: boolean;
}

export interface SegmentPersonalizationExample {
  segment: string;
  segment_label: string;
  customer_id: string;
  customer_name: string;
  product_id: string;
  product_name: string;
  brand: string;
  category: string;
  pack_size: string;
  rank: number;
  reco_type: string;
  trigger_signal: string;
  final_score: number;
}

export interface Kpis {
  conversion: KpiMetric;
  fulfillment: KpiMetric;
  reach: { customers_served: number; total_recommendations: number };
  source: string;
}

export interface KpiMetric {
  label: string;
  rate: number | null;
  numerator: number;
  denominator: number;
  definition: string;
}

export interface Lineage {
  catalog: string;
  schema: string;
  medallion: { layer: string; prefix: string; note: string }[];
  sources: { system: string; role: string }[];
  table_sources: { table: string; source_systems: string[] }[];
}

export interface AppConfig {
  catalog: string;
  schema: string;
  warehouse_id: string;
  genie_enabled: boolean;
  dashboard_id: string | null;
  dashboard_url: string | null;
  workspace_host: string;
}

export interface GenieAnswer {
  question: string;
  answer: string | null;
  sql: string | null;
  query_description: string | null;
  table: { columns: { name: string; type: string }[]; rows: string[][]; row_count: number } | null;
}
