import type { ReactNode } from 'react';

export function EmptyState({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div className="empty">
      <h4>{title}</h4>
      <p>{children}</p>
    </div>
  );
}

export function SkeletonCard({ height = 220 }: { height?: number }) {
  return <div className="skeleton" style={{ height }} />;
}

export function Loading({ label = 'Loading' }: { label?: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text-soft)', fontSize: 14 }}>
      <Spinner />
      {label}
    </div>
  );
}

export function Spinner({ size = 16 }: { size?: number }) {
  return (
    <svg className="spin" width={size} height={size} viewBox="0 0 24 24" fill="none" aria-hidden>
      <circle cx="12" cy="12" r="9" stroke="currentColor" strokeOpacity="0.2" strokeWidth="2.5" />
      <path d="M21 12a9 9 0 00-9-9" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" />
    </svg>
  );
}
