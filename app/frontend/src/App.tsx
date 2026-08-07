import { useEffect, useState } from 'react';
import { api } from './api';
import type { AppConfig, Customer } from './types';
import { Storefront } from './views/Storefront';
import { Console } from './views/Console';
import { ArchitectureModal } from './components/ArchitectureModal';
import { IconStore, IconConsole, IconLayers } from './components/Icons';

type Mode = 'store' | 'console';

export default function App() {
  const [mode, setMode] = useState<Mode>('store');
  const [config, setConfig] = useState<AppConfig | null>(null);
  const [archOpen, setArchOpen] = useState(false);

  // Customer list is loaded once and shared with the storefront (account switcher).
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [customersLoading, setCustomersLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string | null>(null);

  useEffect(() => {
    api.config().then(setConfig).catch(() => setConfig(null));
    api
      .customers()
      .then((res) => {
        setCustomers(res.customers || []);
        if (res.customers?.length) setSelectedId(res.customers[0].customer_id);
      })
      .catch(() => setCustomers([]))
      .finally(() => setCustomersLoading(false));
  }, []);

  return (
    <>
      <header className="appbar">
        <div className="wrap appbar__inner">
          <div className="brand">
            <img src="/lactalis-logo.svg" alt="Lactalis" />
            <span className="brand__divider" />
            <span className="brand__tag">{mode === 'store' ? 'MyLactalis' : 'Recommendation Engine'}</span>
          </div>

          <nav className="modeswitch" aria-label="View switcher">
            <button className={mode === 'store' ? 'active' : ''} onClick={() => setMode('store')}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                <IconStore size={16} /> Storefront
              </span>
            </button>
            <button className={mode === 'console' ? 'active' : ''} onClick={() => setMode('console')}>
              <span style={{ display: 'inline-flex', alignItems: 'center', gap: 7 }}>
                <IconConsole size={16} /> Engine Console
              </span>
            </button>
          </nav>
        </div>
      </header>

      <main>
        {mode === 'store' ? (
          <Storefront
            customers={customers}
            customersLoading={customersLoading}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
        ) : (
          <Console config={config} />
        )}
      </main>

      <footer className="sitefoot">
        <div className="wrap sitefoot__inner">
          <div className="sitefoot__left">
            <span className="faint" style={{ fontSize: 12.5 }}>
              Lactalis B2B Personalized Recommendation Engine · demo
            </span>
            <span className="faint sitefoot__credit">Product imagery: Wikimedia Commons</span>
          </div>

          {/* App-wide trigger — opens the architecture overlay without navigating. */}
          <button
            className="archtrigger"
            onClick={() => setArchOpen(true)}
            aria-haspopup="dialog"
          >
            <span className="dot" />
            <span className="archtrigger__label">
              Powered by Databricks
              <span className="archtrigger__cta">
                <IconLayers size={13} /> see the architecture
              </span>
            </span>
            {config?.catalog && (
              <span className="faint archtrigger__loc">
                {config.catalog}.{config.schema}
              </span>
            )}
          </button>
        </div>
      </footer>

      <ArchitectureModal open={archOpen} onClose={() => setArchOpen(false)} />
    </>
  );
}
