import { useEffect, useState } from 'react';
import { api } from '../api';
import type { Segment } from '../types';

export function SegmentExplorer() {
  const [segments, setSegments] = useState<Segment[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let alive = true;
    api
      .segments()
      .then((s) => alive && setSegments(s.segments || []))
      .catch(() => alive && setSegments([]))
      .finally(() => alive && setLoading(false));
    return () => {
      alive = false;
    };
  }, []);

  if (loading) {
    return (
      <div className="segs">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="skeleton" style={{ height: 210 }} />
        ))}
      </div>
    );
  }

  return (
    <div className="segs">
      {segments.map((s) => (
        <div className="seg" key={s.segment}>
          <div className="seg__top">
            <span className="seg__code" style={{ background: s.accent }} title={s.segment_label}>
              {s.segment}
            </span>
            <div className="seg__count">
              <b className="mono">{s.customer_count.toLocaleString()}</b>
              <span>accounts</span>
            </div>
          </div>
          <div className="seg__label">{s.segment_label}</div>
          <div className="seg__blurb">{s.blurb}</div>
          {s.top_categories.length > 0 ? (
            <div className="seg__cats">
              {s.top_categories.map((c) => (
                <span key={c} className="pill">
                  {c}
                </span>
              ))}
            </div>
          ) : (
            s.dc_count > 0 && (
              <div className="seg__cats">
                <span className="pill">{s.dc_count} DCs</span>
                <span className="pill">{s.region_count} regions</span>
              </div>
            )
          )}
        </div>
      ))}
    </div>
  );
}
