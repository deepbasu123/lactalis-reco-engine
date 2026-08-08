import { useEffect, useRef } from 'react';
import type { ReactNode } from 'react';
import {
  IconBox,
  IconLayers,
  IconSpark,
  IconShield,
  IconSearch,
  IconChart,
  IconStore,
  IconArrow,
} from './Icons';

/**
 * "How this works — powered by Databricks" overlay.
 *
 * A non-navigating dialog: it opens over the current view, closes on X / ESC /
 * backdrop click, locks body scroll while open, and never changes routes or
 * pushes page content. The diagram is styled HTML/CSS + inline SVG icons — no
 * external image service — so it renders offline in the deployed app.
 */
export function ArchitectureModal({ open, onClose }: { open: boolean; onClose: () => void }) {
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onClose();
        return;
      }
      // Lightweight focus trap: keep Tab within the dialog.
      if (e.key === 'Tab' && dialogRef.current) {
        const focusables = dialogRef.current.querySelectorAll<HTMLElement>(
          'button, a[href], [tabindex]:not([tabindex="-1"])'
        );
        if (focusables.length === 0) return;
        const first = focusables[0];
        const last = focusables[focusables.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };

    document.addEventListener('keydown', onKey);
    const prevOverflow = document.body.style.overflow;
    document.body.style.overflow = 'hidden';
    // Move focus into the dialog for keyboard + screen-reader users.
    const t = window.setTimeout(() => dialogRef.current?.focus(), 0);

    return () => {
      document.removeEventListener('keydown', onKey);
      document.body.style.overflow = prevOverflow;
      window.clearTimeout(t);
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className="archmodal" role="presentation" onClick={onClose}>
      <div
        className="archmodal__dialog"
        role="dialog"
        aria-modal="true"
        aria-labelledby="archmodal-title"
        ref={dialogRef}
        tabIndex={-1}
        onClick={(e) => e.stopPropagation()}
      >
        <header className="archmodal__head">
          <div>
            <div className="archmodal__eyebrow">
              <span className="dot" /> Powered by Databricks
            </div>
            <h2 id="archmodal-title" className="archmodal__title">
              How this works
            </h2>
            <p className="archmodal__lead">
              Every pick on the storefront is produced on one governed platform. Here is the path a
              recommendation takes, from raw source systems to the card you just saw.
            </p>
          </div>
          <button className="archmodal__close" onClick={onClose} aria-label="Close">
            <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
              <path d="M6 6l12 12M18 6L6 18" />
            </svg>
          </button>
        </header>

        <div className="archmodal__body">
          {/* Stage 1 — Sources */}
          <Stage
            n="01"
            accent="sky"
            icon={<IconBox size={17} />}
            kicker="Sources"
            title="Where the data comes from"
            lead="Operational systems and outside signals, landed continuously into the lakehouse."
          >
            <Chip main="SAP ERP" sub="rep orders, pricing, stock" />
            <Chip main="Salesforce CRM" sub="accounts, segments, e-commerce, favorites" />
            <Chip main="External signals" sub="weather · fuel price · school calendar" />
          </Stage>

          <Connector label="Landed as-is" />

          {/* Stage 2 — Unity Catalog medallion */}
          <Stage
            n="02"
            accent="blue"
            icon={<IconLayers size={17} />}
            kicker="Unity Catalog · governed lakehouse"
            title="One clean, governed copy of the data"
            lead="Raw data is refined through the medallion layers and stored as Delta, with every table governed and lineage-tracked in Unity Catalog."
          >
            <div className="medallion-flow">
              <span className="med med--bronze">
                <b>Bronze</b>
                <small>raw, source-tagged</small>
              </span>
              <IconArrow size={16} className="med__arrow" />
              <span className="med med--silver">
                <b>Silver</b>
                <small>cleaned, conformed</small>
              </span>
              <IconArrow size={16} className="med__arrow" />
              <span className="med med--gold">
                <b>Gold</b>
                <small>business-ready tables</small>
              </span>
            </div>
          </Stage>

          <Connector label="Gold tables" />

          {/* Stage 3 — Recommendation engine */}
          <Stage
            n="03"
            accent="gold"
            icon={<IconSpark size={17} />}
            kicker="Recommendation engine"
            title="How each pick is chosen — and explained"
            lead="Ranking runs in Databricks SQL, a hard guardrail keeps it safe to ship, and a foundation model writes the reason in plain English."
          >
            <div className="engine-steps">
              <span className="estep">
                <span className="estep__k">Databricks SQL</span>
                affinity from order history
              </span>
              <span className="estep__plus">+</span>
              <span className="estep">
                <span className="estep__k">Context boosts</span>
                live weather, fuel and calendar signals
              </span>
              <span className="estep__plus">+</span>
              <span className="estep estep--guard">
                <IconShield size={14} />
                <span className="estep__k">Out-of-stock guardrail</span>
                nothing un-shippable is ever shown
              </span>
            </div>
            <div className="engine-ai">
              <IconSpark size={14} />
              <span>
                <b>Foundation Model API</b> — <code>ai_query</code> writes the "why we picked this"
                rationale on every recommendation.
              </span>
            </div>
          </Stage>

          <Connector label="Personalized picks + rationale" />

          {/* Stage 4 — Serving */}
          <Stage
            n="04"
            accent="navy"
            icon={<IconStore size={17} />}
            kicker="Serving layer"
            title="How people use it"
            lead="The same gold layer feeds three governed surfaces — no data copies, no separate stack."
          >
            <Chip icon={<IconSearch size={14} />} main="Genie" sub="ask the data in plain language" />
            <Chip icon={<IconChart size={14} />} main="AI/BI Dashboards" sub="campaign performance" />
            <Chip icon={<IconStore size={14} />} main="Databricks Apps" sub="this storefront + console" />
          </Stage>
        </div>

        <footer className="archmodal__foot">
          <span className="faint">
            Unity Catalog · Delta · Databricks SQL · Foundation Model API · Genie · AI/BI Dashboards · Databricks Apps
          </span>
          <button className="btn btn--primary btn--sm" onClick={onClose}>
            Back to the store
          </button>
        </footer>
      </div>
    </div>
  );
}

function Stage({
  n,
  accent,
  icon,
  kicker,
  title,
  lead,
  children,
}: {
  n: string;
  accent: 'sky' | 'blue' | 'gold' | 'navy';
  icon: JSX.Element;
  kicker: string;
  title: string;
  lead: string;
  children: ReactNode;
}) {
  return (
    <section className={`astage astage--${accent}`}>
      <span className="astage__num">{n}</span>
      <div className="astage__main">
        <div className="astage__head">
          <span className="astage__icon">{icon}</span>
          <div>
            <div className="astage__kicker">{kicker}</div>
            <h3 className="astage__title">{title}</h3>
          </div>
        </div>
        <p className="astage__lead">{lead}</p>
        <div className="astage__items">{children}</div>
      </div>
    </section>
  );
}

function Chip({ icon, main, sub }: { icon?: JSX.Element; main: string; sub: string }) {
  return (
    <span className="achip">
      {icon && <span className="achip__icon">{icon}</span>}
      <span className="achip__text">
        <b>{main}</b>
        <small>{sub}</small>
      </span>
    </span>
  );
}

function Connector({ label }: { label: string }) {
  return (
    <div className="aconn" aria-hidden>
      <span className="aconn__line" />
      <span className="aconn__label">{label}</span>
      <span className="aconn__arrow">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 5v14M6 13l6 6 6-6" />
        </svg>
      </span>
    </div>
  );
}
