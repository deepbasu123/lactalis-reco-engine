import { useState } from 'react';
import type { Favorite } from '../types';
import { aud } from '../api';
import { categoryTheme, brandMonogram } from './category';
import { productImage } from './productImage';
import { IconClock } from './Icons';

export function FavoriteCard({ fav }: { fav: Favorite }) {
  const [imgOk, setImgOk] = useState(true);
  const theme = categoryTheme(fav.category);
  return (
    <div className="fav">
      {/* Product photo on the category tint; monogram fallback on load error. */}
      <div className="fav__thumb" style={{ background: theme.tint }}>
        <span className="cap" style={{ background: theme.cap }} />
        {imgOk ? (
          <img
            className="fav__img"
            src={productImage(fav.product_name, fav.category)}
            alt={fav.product_name}
            loading="lazy"
            onError={() => setImgOk(false)}
          />
        ) : (
          <span aria-hidden>{brandMonogram(fav.brand)}</span>
        )}
      </div>
      <div className="fav__info">
        <div className="fav__brand">{fav.brand}</div>
        <div className="fav__name" title={fav.product_name}>
          {fav.product_name}
        </div>
        <div className="fav__meta">
          <span className="fav__price mono">{aud(fav.unit_price)}</span>
          <span>·</span>
          <span>{fav.pack_size}</span>
          {typeof fav.reorder_frequency_days === 'number' && (
            <span className="fav__cadence">
              <IconClock size={13} /> every {fav.reorder_frequency_days}d
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
