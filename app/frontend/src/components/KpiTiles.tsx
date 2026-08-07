import { useEffect, useState } from 'react';
import { api, pct } from '../api';
import type { Kpis } from '../types';
import { Spinner } from './Common';

export function KpiTiles() {
  const [kpis, setKpis] = useState<Kpis | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api
      .kpis()
      .then((k) => alive && setKpis(k))
      .catch(() => alive && setKpis(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  if (loading) {
    return (
      <div className="kpis">
        {[0, 1, 2].map((i) => (
          <div key={i} className="skeleton" style={{ height: 168 }} />
        ))}
      </div>
    );
  }

  const conv = kpis?.conversion;
  const ful = kpis?.fulfillment;
  const reach = kpis?.reach;

  return (
    <div className="kpis">
      <div className="kpi kpi--navy">
        <div className="kpi__label">{conv?.label || 'Upsell / cross-sell conversion'}</div>
        <div className="kpi__value">{conv?.rate != null ? pct(conv.rate) : <small>Awaiting data</small>}</div>
        {conv && conv.denominator > 0 && (
          <div className="kpi__frac mono">
            {conv.numerator.toLocaleString()} / {conv.denominator.toLocaleString()} recs converted
          </div>
        )}
        <div className="kpi__def">{conv?.definition || 'Recommended SKUs later ordered ÷ recommendations surfaced'}</div>
      </div>

      <div className="kpi kpi--gold">
        <div className="kpi__label">{ful?.label || 'Order fulfillment readiness'}</div>
        <div className="kpi__value">{ful?.rate != null ? pct(ful.rate) : <small>Awaiting data</small>}</div>
        {ful && ful.denominator > 0 && (
          <div className="kpi__frac mono">
            {ful.numerator.toLocaleString()} / {ful.denominator.toLocaleString()} SKU-DC lines in stock
          </div>
        )}
        <div className="kpi__def">{ful?.definition || 'In-stock lines ÷ all lines (the guardrail made visible)'}</div>
      </div>

      <div className="kpi">
        <div className="kpi__label">Recommendations in market</div>
        <div className="kpi__value">
          {reach ? reach.total_recommendations.toLocaleString() : <small>Awaiting data</small>}
        </div>
        {reach && reach.customers_served > 0 && (
          <div className="kpi__frac mono">across {reach.customers_served.toLocaleString()} accounts</div>
        )}
        <div className="kpi__def">Distinct surfaced picks currently live across all B2B accounts.</div>
      </div>
    </div>
  );
}
