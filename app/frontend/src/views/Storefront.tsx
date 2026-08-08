import { useEffect, useMemo, useState } from 'react';
import { api } from '../api';
import type { Customer, Favorite, Recommendation } from '../types';
import { RecoCard } from '../components/RecoCard';
import { FavoriteCard } from '../components/FavoriteCard';
import { GeniePanel } from '../components/GeniePanel';
import { EmptyState, SkeletonCard, Spinner } from '../components/Common';
import { IconChevron, IconSpark, IconStore, IconBox, IconSearch } from '../components/Icons';

// Buyer-framed prompts for the storefront Genie panel. All three were verified
// to return data against the Lactalis Recommendation Analytics Genie space.
const STOREFRONT_GENIE_PROMPTS = [
  'What are the top selling products by revenue?',
  'Which products are most popular with Restaurants and Cafes?',
  'Show total spend by product category',
];

interface Props {
  customers: Customer[];
  customersLoading: boolean;
  selectedId: string | null;
  onSelect: (id: string) => void;
}

export function Storefront({ customers, customersLoading, selectedId, onSelect }: Props) {
  const [favorites, setFavorites] = useState<Favorite[]>([]);
  const [recs, setRecs] = useState<Recommendation[]>([]);
  const [loading, setLoading] = useState(false);

  const selected = useMemo(
    () => customers.find((c) => c.customer_id === selectedId) || null,
    [customers, selectedId]
  );

  useEffect(() => {
    if (!selectedId) return;
    let alive = true;
    setLoading(true);
    Promise.all([api.favorites(selectedId), api.recommendations(selectedId)])
      .then(([f, r]) => {
        if (!alive) return;
        setFavorites(f.favorites || []);
        setRecs((r.recommendations || []).slice(0, 3));
      })
      .catch(() => {
        if (!alive) return;
        setFavorites([]);
        setRecs([]);
      })
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, [selectedId]);

  return (
    <div className="wrap" style={{ paddingTop: 28, paddingBottom: 72 }}>
      {/* Account switcher */}
      <div className="acct" style={{ marginBottom: 24 }}>
        <div>
          <div className="kicker">Viewing account as</div>
          <div className="acct__select" style={{ marginTop: 8 }}>
            <select
              id="account-switcher"
              name="account-switcher"
              value={selectedId || ''}
              onChange={(e) => onSelect(e.target.value)}
              disabled={customersLoading || customers.length === 0}
              aria-label="Select customer account"
            >
              {customersLoading && <option>Loading accounts…</option>}
              {!customersLoading && customers.length === 0 && <option value="">No accounts available yet</option>}
              {customers.map((c) => (
                <option key={c.customer_id} value={c.customer_id}>
                  {c.customer_name} · {c.segment_label}
                </option>
              ))}
            </select>
            <IconChevron className="chev" size={18} />
          </div>
        </div>
        {selected?.account_tier && (
          <div style={{ marginTop: 22 }}>
            <span className="pill pill--sky">{selected.account_tier} account</span>
          </div>
        )}
      </div>

      {/* Hero */}
      <Hero selected={selected} loading={customersLoading} empty={customers.length === 0} />

      {/* Suggested for You */}
      <section className="suggest">
        <div className="section-head">
          <div>
            <div className="kicker" style={{ display: 'flex', alignItems: 'center', gap: 7 }}>
              <IconSpark size={15} /> Suggested for you
            </div>
            <h2 style={{ marginTop: 8 }}>This month's picks</h2>
            <p className="sub">
              Personalized from your ordering history, segment and live demand signals. Fulfillment-checked, so
              everything here can ship from your distribution center.
            </p>
          </div>
        </div>

        {loading ? (
          <div className="suggest__grid">
            <SkeletonCard height={340} />
            <SkeletonCard height={340} />
            <SkeletonCard height={340} />
          </div>
        ) : recs.length > 0 ? (
          <div className="suggest__grid">
            {recs.map((r, i) => (
              <RecoCard key={r.product_id} rec={r} delay={i * 90} />
            ))}
          </div>
        ) : (
          <EmptyState title="No suggestions to show yet">
            Recommendations appear here once the engine's <code>vw_reco_full</code> view is populated in{' '}
            <code>lactalis_catalog.reco</code>. The storefront reads live from Unity Catalog, so picks will surface
            automatically for the selected account.
          </EmptyState>
        )}
      </section>

      {/* Favorites */}
      <section className="favs">
        <div className="rulehead">
          <IconStore size={18} className="muted" />
          <h3>Your favorites</h3>
          <span className="count">{favorites.length > 0 ? `${favorites.length} regular items` : 'reorder list'}</span>
        </div>

        {loading ? (
          <div className="favs__grid">
            {Array.from({ length: 6 }).map((_, i) => (
              <SkeletonCard key={i} height={82} />
            ))}
          </div>
        ) : favorites.length > 0 ? (
          <div className="favs__grid">
            {favorites.map((f) => (
              <FavoriteCard key={f.product_id} fav={f} />
            ))}
          </div>
        ) : (
          <EmptyState title="No favorites recorded">
            This account's habitual reorder items (from Salesforce) will list here once{' '}
            <code>customer_favorites</code> lands.
          </EmptyState>
        )}
      </section>

      {/* Talk to your data — B2B self-service analytics on the storefront.
          Reuses the same Genie panel + /api/genie/ask backend as the Engine
          Console; only the framing/chips change. Sits at the end so it never
          disrupts the browse-and-reorder flow above. */}
      <section className="askdata">
        <div className="rulehead">
          <IconSearch size={18} className="muted" />
          <h3>Talk to your data</h3>
          <span className="count">powered by Databricks Genie</span>
        </div>
        <p className="askdata__lead">
          Ask about your orders, products and trends in plain language — no dashboards to build. Genie writes the
          query, runs it on your governed data, and shows the answer.
        </p>
        <GeniePanel
          kicker="Ask MyLactalis"
          title="Self-service analytics for your account"
          placeholder="e.g. What are my top products by spend this quarter?"
          suggestions={STOREFRONT_GENIE_PROMPTS}
          notConfiguredCopy={
            <>
              Natural-language analytics turns on once this workspace's Genie space is connected (
              <code>GENIE_SPACE_ID</code>). Your favorites and suggestions above work without it.
            </>
          }
        />
      </section>
    </div>
  );
}

function Hero({ selected, loading, empty }: { selected: Customer | null; loading: boolean; empty: boolean }) {
  if (loading) {
    return <div className="skeleton" style={{ height: 190, borderRadius: 22 }} />;
  }
  return (
    <div className="hero">
      <div className="hero__row">
        <div style={{ maxWidth: 640 }}>
          <div className="kicker" style={{ color: 'rgba(255,255,255,0.6)' }}>
            MyLactalis · Business ordering
          </div>
          <h1 className="hero__title" style={{ marginTop: 10 }}>
            {selected ? `Welcome back, ${selected.customer_name}` : 'Welcome to MyLactalis'}
          </h1>
          <p className="hero__sub">
            {selected
              ? 'Your personalized ordering hub. Reorder your regulars, or take a look at what we are suggesting for you this month.'
              : empty
              ? 'Connect the reco gold layer to see personalized favorites and suggestions here.'
              : 'Pick an account above to see its favorites and personalized suggestions.'}
          </p>
          {selected && (
            <div className="hero__meta">
              <span className="hero__chip">
                <IconStore size={14} /> {selected.segment_label}
              </span>
              {selected.city && (
                <span className="hero__chip">
                  {selected.city}
                  {selected.region ? `, ${selected.region}` : ''}
                </span>
              )}
              {selected.dc_name && (
                <span className="hero__chip">
                  <IconBox size={14} /> Fulfilled from {selected.dc_name}
                </span>
              )}
            </div>
          )}
        </div>

        {selected?.profile_note && (
          <div className="hero__profile">
            <div className="kicker">Account profile</div>
            <p>{selected.profile_note}</p>
          </div>
        )}
      </div>
    </div>
  );
}
