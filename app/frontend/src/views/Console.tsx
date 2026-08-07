import type { AppConfig } from '../types';
import { KpiTiles } from '../components/KpiTiles';
import { SegmentExplorer } from '../components/SegmentExplorer';
import { SignalPanel } from '../components/SignalPanel';
import { LineagePanel } from '../components/LineagePanel';
import { GeniePanel } from '../components/GeniePanel';
import { IconChart, IconLayers, IconSpark, IconShield, IconSearch, IconExternal } from '../components/Icons';

export function Console({ config }: { config: AppConfig | null }) {
  return (
    <div className="wrap console">
      <div className="console__intro reveal">
        <div>
          <div className="kicker">Engine Console · internal</div>
          <h1 style={{ marginTop: 8 }}>How the recommendation engine works</h1>
          <p>
            The same gold layer that powers the MyLactalis storefront, opened up for sales and marketing. See the
            demand signals, the fulfillment guardrail, per-segment personalization, governance lineage, and ask the
            data directly through Genie.
          </p>
        </div>
        <span className="poweredby">
          <span className="dot" /> Powered by Databricks
        </span>
      </div>

      {/* KPIs */}
      <section className="panel reveal" style={{ animationDelay: '60ms' }}>
        <SectionLabel icon={<IconChart size={16} />} title="Performance" />
        <KpiTiles />
      </section>

      {/* Segment explorer */}
      <section className="panel reveal" style={{ animationDelay: '120ms' }}>
        <div className="card">
          <PanelHead
            icon={<IconLayers size={18} />}
            title="Segment explorer"
            sub="The five B2B segments the engine personalizes for, with live account counts from Unity Catalog."
          />
          <SegmentExplorer />
        </div>
      </section>

      {/* Signal panel — the 3 hero moments */}
      <section className="panel reveal" style={{ animationDelay: '160ms' }}>
        <div className="card">
          <PanelHead
            icon={<IconSpark size={18} />}
            title="Signals and guardrails"
            sub="Step through the three moments that make the engine feel personal and safe to ship. Click each to narrate it."
          />
          <SignalPanel />
        </div>
      </section>

      {/* Governance / lineage */}
      <section className="panel reveal" style={{ animationDelay: '200ms' }}>
        <div className="card">
          <PanelHead
            icon={<IconShield size={18} />}
            title="Governance and lineage"
            sub="Source-tagged ingestion through the medallion architecture, governed in Unity Catalog."
          />
          <LineagePanel />
        </div>
      </section>

      {/* Genie */}
      <section className="panel reveal" style={{ animationDelay: '220ms' }}>
        <SectionLabel icon={<IconSearch size={16} />} title="Ask the data" />
        <GeniePanel />
      </section>

      {/* Dashboard */}
      <section className="panel reveal" style={{ animationDelay: '240ms' }}>
        <SectionLabel icon={<IconChart size={16} />} title="Campaign performance dashboard" />
        <DashboardEmbed config={config} />
      </section>
    </div>
  );
}

function SectionLabel({ icon, title }: { icon: JSX.Element; title: string }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 9, marginBottom: 16 }}>
      <span style={{ color: 'var(--blue)' }}>{icon}</span>
      <h2 style={{ fontSize: 20 }}>{title}</h2>
    </div>
  );
}

function PanelHead({ icon, title, sub }: { icon: JSX.Element; title: string; sub: string }) {
  return (
    <div className="section-head">
      <div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 9 }}>
          <span style={{ color: 'var(--blue)' }}>{icon}</span>
          <h2>{title}</h2>
        </div>
        <p className="sub">{sub}</p>
      </div>
    </div>
  );
}

function DashboardEmbed({ config }: { config: AppConfig | null }) {
  if (config?.dashboard_url && config.dashboard_id) {
    return (
      <div className="dash">
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 20px', borderBottom: '1px solid var(--line)' }}>
          <span className="muted" style={{ fontSize: 13 }}>
            AI/BI dashboard <code>{config.dashboard_id}</code>
          </span>
          <a
            className="btn btn--ghost btn--sm"
            href={`${config.workspace_host}/dashboardsv3/${config.dashboard_id}`}
            target="_blank"
            rel="noreferrer"
          >
            Open in Databricks <IconExternal size={15} />
          </a>
        </div>
        <iframe title="Lactalis campaign performance dashboard" src={config.dashboard_url} />
      </div>
    );
  }

  return (
    <div className="empty">
      <h4>Dashboard not linked yet</h4>
      <p>
        Set the <code>DASHBOARD_ID</code> environment variable to embed the AI/BI (Lakeview) campaign-performance
        dashboard here. It will render inline once the dashboard is published and the id is provided.
      </p>
    </div>
  );
}
