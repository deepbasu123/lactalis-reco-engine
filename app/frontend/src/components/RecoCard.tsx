import { useState } from 'react';
import type { Recommendation } from '../types';
import { aud } from '../api';
import { categoryTheme, brandMonogram } from './category';
import { productImage } from './productImage';
import { IconPlus, IconCheck, IconSun, IconClock } from './Icons';

// "why now" chip icon by trigger signal.
function triggerIcon(signal?: string) {
  if (signal === 'weather') return <IconSun size={14} />;
  if (signal === 'calendar') return <IconClock size={14} />;
  return null;
}

export function RecoCard({ rec, delay = 0 }: { rec: Recommendation; delay?: number }) {
  const [added, setAdded] = useState(false);
  const [imgOk, setImgOk] = useState(true);
  const theme = categoryTheme(rec.category);
  const label = rec.product_name || rec.product_id;

  return (
    <article className="reco reveal" style={{ animationDelay: `${delay}ms` }}>
      <span className="reco__rank" title={`Rank ${rec.rank}`}>
        {rec.rank}
      </span>
      {/* Product image on the category tint. Falls back to the packaging monogram
          if the bundled photo ever fails to load. */}
      <div className="reco__pack" style={{ background: theme.tint }}>
        <span className="reco__cap" style={{ background: theme.cap }} />
        {imgOk ? (
          <img
            className="reco__img"
            src={productImage(rec.product_name, rec.category)}
            alt={label}
            loading="lazy"
            onError={() => setImgOk(false)}
          />
        ) : (
          <span className="reco__mono" aria-hidden>
            {brandMonogram(rec.brand)}
          </span>
        )}
      </div>

      <div className="reco__body">
        <div>
          <div className="reco__brand">{rec.brand || '—'}</div>
          <h3 className="reco__name">{rec.product_name || rec.product_id}</h3>
          <div style={{ display: 'flex', gap: 8, marginTop: 8, flexWrap: 'wrap' }}>
            <span className="pill">{rec.category || 'Product'}</span>
            {rec.pack_size && <span className="pill">{rec.pack_size}</span>}
          </div>
        </div>

        {rec.why_text && (
          <p className="reco__why">
            <span className="lead">Why we picked this. </span>
            {rec.why_text}
          </p>
        )}

        <div className="reco__typebar">
          {rec.why_now_tag && (
            <span className="pill pill--gold">
              {triggerIcon(rec.trigger_signal)}
              {rec.why_now_tag}
            </span>
          )}
          {rec.reco_type && (
            <span className="pill pill--navy">{rec.reco_type === 'upsell' ? 'Upsell' : 'Cross-sell'}</span>
          )}
        </div>

        <div className="reco__foot">
          <div className="reco__price">
            <span className="amt mono">{aud(rec.unit_price)}</span>
            <span className="unit">per {rec.pack_size || 'unit'}</span>
          </div>
          <button
            className={`btn btn--primary addbtn ${added ? 'added' : ''}`}
            onClick={() => setAdded((v) => !v)}
            aria-pressed={added}
          >
            {added ? (
              <>
                <IconCheck size={16} /> Added
              </>
            ) : (
              <>
                <IconPlus size={16} /> Add to order
              </>
            )}
          </button>
        </div>
      </div>
    </article>
  );
}
