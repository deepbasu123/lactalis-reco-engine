// Thin fetch client for the backend. Every call returns typed data and never
// throws on empty gold-layer results (the backend returns valid empty shapes).

import type {
  AppConfig,
  Customer,
  Favorite,
  GenieAnswer,
  Kpis,
  Lineage,
  Recommendation,
  Segment,
  SegmentPersonalizationExample,
  StockLine,
  WeatherRow,
} from './types';

async function getJSON<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: 'application/json' } });
  if (!res.ok) throw new Error(`${url} -> ${res.status}`);
  return res.json() as Promise<T>;
}

export const api = {
  config: () => getJSON<AppConfig>('/api/config'),
  health: () => getJSON<Record<string, unknown>>('/api/health'),

  customers: () => getJSON<{ customers: Customer[]; count: number }>('/api/customers'),
  customer: (id: string) => getJSON<{ customer: Customer | null }>(`/api/customer/${encodeURIComponent(id)}`),
  favorites: (id: string) =>
    getJSON<{ favorites: Favorite[]; count: number }>(`/api/customer/${encodeURIComponent(id)}/favorites`),
  recommendations: (id: string) =>
    getJSON<{ recommendations: Recommendation[]; count: number }>(
      `/api/customer/${encodeURIComponent(id)}/recommendations`
    ),

  segments: () => getJSON<{ segments: Segment[]; total_customers: number }>('/api/segments'),
  kpis: () => getJSON<Kpis>('/api/kpis'),

  oosDemo: () =>
    getJSON<{ held_back: StockLine[]; passing: StockLine[]; rule: string }>('/api/console/oos-demo'),
  weatherSignal: () =>
    getJSON<{ weather: WeatherRow[]; flavored_products: unknown[]; weather_triggered_recs: unknown[] }>(
      '/api/console/weather-signal'
    ),
  segmentPersonalization: () =>
    getJSON<{ segments: { segment: string; segment_label: string; examples: SegmentPersonalizationExample[] }[] }>(
      '/api/console/segment-personalization'
    ),
  lineage: () => getJSON<Lineage>('/api/console/lineage'),

  genieStatus: () => getJSON<{ enabled: boolean; space_id: string | null }>('/api/genie/status'),
  genieAsk: async (question: string): Promise<GenieAnswer> => {
    const res = await fetch('/api/genie/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ question }),
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || `Genie request failed (${res.status})`);
    return data as GenieAnswer;
  },
};

// AUD currency formatting used across product cards.
export const aud = (n?: number | null) =>
  typeof n === 'number'
    ? new Intl.NumberFormat('en-AU', { style: 'currency', currency: 'AUD' }).format(n)
    : '—';

export const pct = (n?: number | null) =>
  typeof n === 'number' ? `${(n * 100).toFixed(1)}%` : '—';
