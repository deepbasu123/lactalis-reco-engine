import type { Favorite } from '../types';
import { aud } from '../api';
import { categoryTheme, brandMonogram } from './category';
import { IconClock } from './Icons';

export function FavoriteCard({ fav }: { fav: Favorite }) {
  const theme = categoryTheme(fav.category);
  return (
    <div className="fav">
      <div className="fav__thumb" style={{ background: theme.tint }}>
        <span className="cap" style={{ background: theme.cap }} />
        <span aria-hidden>{brandMonogram(fav.brand)}</span>
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
