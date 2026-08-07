import { useEffect, useState } from 'react';
import { api, aud } from '../api';
import type { StockLine, WeatherRow, SegmentPersonalizationExample } from '../types';
import { Spinner, EmptyState } from './Common';
import { IconSun, IconBlock, IconLayers, IconArrow, IconCheck } from './Icons';

type StepKey = 'weather' | 'oos' | 'segment';

const STEPS: { key: StepKey; title: string; desc: string; icon: JSX.Element }[] = [
  { key: 'weather', title: 'Weather to flavored milk', desc: 'A demand signal lifts the right SKUs', icon: <IconSun size={16} /> },
  { key: 'oos', title: 'Out-of-stock guardrail', desc: 'Fulfillment removes, never deprioritizes', icon: <IconBlock size={16} /> },
  { key: 'segment', title: 'Segment personalization', desc: 'Same moment, different recommendation', icon: <IconLayers size={16} /> },
];

export function SignalPanel() {
  const [active, setActive] = useState<StepKey>('weather');

  return (
    <div className="signal">
      <div className="steps" role="tablist" aria-label="Engine demo moments">
        {STEPS.map((s, i) => (
          <button
            key={s.key}
            role="tab"
            aria-selected={active === s.key}
            className={`step ${active === s.key ? 'active' : ''}`}
            onClick={() => setActive(s.key)}
          >
            <span className="step__num">{i + 1}</span>
            <span>
              <span className="step__t">{s.title}</span>
              <span className="step__d">{s.desc}</span>
            </span>
          </button>
        ))}
      </div>

      <div className="stage">
        {active === 'weather' && <WeatherStage />}
        {active === 'oos' && <OosStage />}
        {active === 'segment' && <SegmentStage />}
      </div>
    </div>
  );
}

/* ---------- (a) Weather -> flavored milk ---------- */
function WeatherStage() {
  const [data, setData] = useState<{ weather: WeatherRow[]; flavored_products: any[] } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api
      .weatherSignal()
      .then((d) => alive && setData(d as any))
      .catch(() => alive && setData(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const hot = data?.weather?.[0];
  return (
    <div>
      <div className="stage__head">
        <span className="pill pill--gold">
          <IconSun size={14} /> Weather signal
        </span>
        <h4>Heat lifts flavored milk at the forecourt</h4>
      </div>
      <p className="stage__lead">
        The engine reads <code>signal_weather</code> by distribution center. On a hot or heatwave day, single-serve
        flavored milk gets a context boost for Petrol &amp; Convenience accounts fulfilled by that DC. The lift is
        additive on top of the base affinity score, so it nudges ranking without overriding history.
      </p>

      {loading ? (
        <Spinner />
      ) : (
        <div className="flow">
          <div className="flow__node">
            <div className="kicker">Signal</div>
            {hot ? (
              <>
                <div style={{ fontSize: 15, fontWeight: 600 }}>
                  {hot.condition}
                  {hot.is_heatwave ? ' · heatwave' : ''}
                </div>
                <div className="mono" style={{ fontSize: 26, fontWeight: 700, marginTop: 4 }}>
                  {typeof hot.temp_c === 'number' ? `${hot.temp_c.toFixed(0)}°C` : '—'}
                </div>
                <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>
                  {hot.dc_name || hot.dc_id}
                  {hot.region ? ` · ${hot.region}` : ''}
                </div>
              </>
            ) : (
              <div className="faint" style={{ fontSize: 13 }}>Awaiting signal_weather rows.</div>
            )}
          </div>
          <IconArrow className="flow__arrow" size={22} />
          <div className="flow__node">
            <div className="kicker">Rule</div>
            <div style={{ fontSize: 13.5, lineHeight: 1.5 }}>
              <code>is_flavored_milk = TRUE</code> and segment <code>P&amp;C</code> and condition Hot/heatwave then
              apply <b>context_boost</b>.
            </div>
          </div>
          <IconArrow className="flow__arrow" size={22} />
          <div className="flow__node">
            <div className="kicker">Boosted SKUs</div>
            {data?.flavored_products?.length ? (
              <div style={{ display: 'flex', flexDirection: 'column', gap: 7, marginTop: 2 }}>
                {data.flavored_products.slice(0, 3).map((p: any) => (
                  <div key={p.product_id} style={{ fontSize: 13 }}>
                    <b>{p.brand}</b> {p.product_name}
                    <span className="faint"> · {p.pack_size}</span>
                  </div>
                ))}
              </div>
            ) : (
              <div className="faint" style={{ fontSize: 13 }}>No flavored-milk SKUs found yet.</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/* ---------- (b) Out-of-stock guardrail ---------- */
function OosStage() {
  const [data, setData] = useState<{ held_back: StockLine[]; passing: StockLine[] } | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api
      .oosDemo()
      .then((d) => alive && setData(d as any))
      .catch(() => alive && setData(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  return (
    <div>
      <div className="stage__head">
        <span className="pill pill--warn">
          <IconBlock size={14} /> Fulfillment guardrail
        </span>
        <h4>High-affinity, but held back</h4>
      </div>
      <p className="stage__lead">
        A SKU can score well and still be wrong to recommend if the fulfilling DC cannot ship it. The engine joins{' '}
        <code>stock_by_dc</code> and keeps only <code>in_stock = TRUE</code>. These candidates are removed from the
        feed, not pushed down. That is the order-fulfillment KPI made concrete.
      </p>

      {loading ? (
        <Spinner />
      ) : data?.held_back?.length ? (
        <div className="oosgrid">
          {data.held_back.slice(0, 4).map((s) => (
            <HeldCard key={`${s.dc_id}-${s.product_id}`} s={s} />
          ))}
        </div>
      ) : (
        <EmptyState title="No out-of-stock lines to show">
          Once <code>stock_by_dc</code> lands with lines where <code>on_hand_units &lt; threshold_units</code>, the
          held-back candidates surface here with a live stock bar.
        </EmptyState>
      )}

      {data?.passing?.length ? (
        <div style={{ marginTop: 18 }}>
          <div className="kicker" style={{ marginBottom: 8 }}>For contrast · clears the guardrail</div>
          <div className="oosgrid">
            {data.passing.slice(0, 2).map((s) => (
              <HeldCard key={`ok-${s.dc_id}-${s.product_id}`} s={s} />
            ))}
          </div>
        </div>
      ) : null}
    </div>
  );
}

function HeldCard({ s }: { s: StockLine }) {
  const held = !s.in_stock;
  const denom = Math.max(s.threshold_units || 1, s.on_hand_units || 0, 1);
  const fillPct = Math.min(100, Math.round(((s.on_hand_units || 0) / denom) * 100));
  const threshPct = Math.min(100, Math.round(((s.threshold_units || 0) / denom) * 100));
  return (
    <div className={`oos ${held ? 'oos--held' : ''}`}>
      {held ? (
        <span className="oos__stamp">HELD BACK</span>
      ) : (
        <span className="pill pill--ok" style={{ position: 'absolute', top: 14, right: 14 }}>
          <IconCheck size={13} /> In stock
        </span>
      )}
      <div className="oos__brand">{s.brand}</div>
      <div className="oos__name">{s.product_name}</div>
      <div className="faint" style={{ fontSize: 12, marginTop: 4 }}>
        {s.dc_name || s.dc_id} · {aud(s.unit_price)} / {s.pack_size}
      </div>
      <div className="oos__stock">
        <div className="stockbar">
          <div
            className="stockbar__fill"
            style={{ width: `${fillPct}%`, background: held ? 'var(--warn)' : 'var(--ok)' }}
          />
          <div className="stockbar__thresh" style={{ left: `${threshPct}%` }} title="Threshold" />
        </div>
        <div className="oos__nums mono">
          <span>{s.on_hand_units?.toLocaleString?.() ?? s.on_hand_units} on hand</span>
          <span>threshold {s.threshold_units?.toLocaleString?.() ?? s.threshold_units}</span>
        </div>
      </div>
    </div>
  );
}

/* ---------- (c) Segment personalization ---------- */
function SegmentStage() {
  const [segments, setSegments] = useState<
    { segment: string; segment_label: string; examples: SegmentPersonalizationExample[] }[]
  >([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api
      .segmentPersonalization()
      .then((d) => alive && setSegments(d.segments || []))
      .catch(() => alive && setSegments([]))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  const focus = segments.filter((s) => ['INSTITUTION', 'RESTAURANT', 'P&C'].includes(s.segment));
  const shown = focus.length ? focus : segments.slice(0, 3);

  return (
    <div>
      <div className="stage__head">
        <span className="pill pill--navy">
          <IconLayers size={14} /> Personalization
        </span>
        <h4>Same moment, three different answers</h4>
      </div>
      <p className="stage__lead">
        Segment-category affinity means the identical context produces different picks. A school kitchen gets
        nutrition-compliant lines, a restaurant gets cooking cream, a convenience store gets single-serve. Below are
        live recommendations grouped by the recipient's segment.
      </p>

      {loading ? (
        <Spinner />
      ) : shown.length ? (
        <div className="flow" style={{ alignItems: 'stretch' }}>
          {shown.map((seg) => (
            <div className="flow__node" key={seg.segment} style={{ minWidth: 200 }}>
              <div className="kicker">{seg.segment_label}</div>
              {seg.examples.slice(0, 3).map((ex, i) => (
                <div
                  key={`${ex.customer_id}-${ex.product_id}-${i}`}
                  style={{ fontSize: 13, paddingTop: i === 0 ? 2 : 8, borderTop: i ? '1px solid var(--line)' : undefined, marginTop: i ? 8 : 0 }}
                >
                  <div style={{ fontWeight: 600 }}>{ex.product_name}</div>
                  <div className="faint" style={{ fontSize: 11.5 }}>
                    {ex.category} · {ex.reco_type}
                  </div>
                </div>
              ))}
            </div>
          ))}
        </div>
      ) : (
        <EmptyState title="No segment recommendations yet">
          When <code>reco_candidates</code> is populated, this shows how the same demand moment resolves to different
          SKUs per segment.
        </EmptyState>
      )}
    </div>
  );
}
