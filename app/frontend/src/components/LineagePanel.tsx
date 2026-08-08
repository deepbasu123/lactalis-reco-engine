import { useEffect, useState } from 'react';
import { api } from '../api';
import type { Lineage } from '../types';
import { Spinner } from './Common';
import { IconArrow } from './Icons';

const LAYER_COLOR: Record<string, string> = {
  Bronze: '#b8763a',
  Silver: '#9aa7b2',
  Gold: '#e8c759',
};
const SOURCE_COLOR: Record<string, string> = {
  SAP: '#0a7c6a',
  Salesforce: '#00a1e0',
};

export function LineagePanel() {
  const [data, setData] = useState<Lineage | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api
      .lineage()
      .then((d) => alive && setData(d))
      .catch(() => alive && setData(null))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  if (loading) return <Spinner />;

  const tableSourceMap = new Map<string, string[]>(
    (data?.table_sources || []).map((t) => [t.table, t.source_systems])
  );

  return (
    <div className="lineage">
      <div>
        <div className="kicker" style={{ marginBottom: 12 }}>Source systems · Unity Catalog governed</div>
        <div className="srcgrid">
          {(data?.sources || []).map((s) => {
            // Which gold tables actually carry this source tag (proves provenance).
            const tables = [...tableSourceMap.entries()]
              .filter(([, systems]) => systems.includes(s.system))
              .map(([t]) => t);
            return (
              <div className="src" key={s.system}>
                <span className="src__badge" style={{ background: SOURCE_COLOR[s.system] || '#004b85' }}>
                  {s.system}
                </span>
                <span className="src__role">{s.role}</span>
                {tables.length > 0 && <span className="src__tables">{tables.length} tables tagged</span>}
              </div>
            );
          })}
        </div>
        <p className="muted" style={{ fontSize: 12.5, lineHeight: 1.55, marginTop: 14 }}>
          Every gold row carries its <code>source_system</code>, so provenance from SAP and Salesforce
          is queryable and lineage is tracked end to end in{' '}
          <code>
            {data?.catalog || 'lactalis_catalog'}.{data?.schema || 'reco'}
          </code>
          .
        </p>
      </div>

      <div>
        <div className="kicker" style={{ marginBottom: 12 }}>Medallion flow</div>
        <div className="medallion">
          {(data?.medallion || []).map((m, i) => (
            <div key={m.layer}>
              <div className="mlayer">
                <span className="mlayer__dot" style={{ background: LAYER_COLOR[m.layer] || '#5bc5f2' }} />
                <div>
                  <div className="mlayer__name">
                    {m.layer}
                    <span className="mlayer__prefix">{m.prefix}</span>
                  </div>
                  <div className="mlayer__note">{m.note}</div>
                </div>
              </div>
              {i < (data?.medallion?.length || 0) - 1 && (
                <div style={{ textAlign: 'center', color: 'var(--sky)', margin: '2px 0', transform: 'rotate(90deg)' }}>
                  <IconArrow size={16} />
                </div>
              )}
            </div>
          ))}
          <div className="mlayer" style={{ borderColor: 'var(--blue)', background: 'var(--sky-soft)' }}>
            <span className="mlayer__dot" style={{ background: 'var(--blue)' }} />
            <div>
              <div className="mlayer__name">
                Recommendation engine
                <span className="mlayer__prefix">reco_*</span>
              </div>
              <div className="mlayer__note">
                Ranked, OOS-filtered candidates + Foundation Model rationale, served to MyLactalis.
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
